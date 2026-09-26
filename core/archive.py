"""Manage shared Internet Archive captures and Ziggy queue submissions."""

from collections import deque
from collections.abc import Callable
from threading import Condition, Lock
from typing import Any, Protocol, Self, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import niquests
from archivist import (
    AuthenticationError,
    InternetArchiveAccount,
    InternetArchiveClient,
    InternetArchiveSaveOptions,
    InternetArchiveSuccessStatus,
)
from loguru import logger
from niquests import Response

from .retry import RequestRateLimiter, retry_request, retry_transient_request

T = TypeVar("T")
_ZIGGY_RATE_LIMITER = RequestRateLimiter(limit=60, period=60.0)


class _ZiggySession(Protocol):
    """Describe the HTTP session operations used by ZiggyClient."""

    def post(
        self: Self, url: str, *, json: Any, timeout: float, allow_redirects: bool
    ) -> Response:
        """Submit a JSON request."""
        ...

    def close(self: Self) -> None:
        """Close the session."""
        ...


class _RateLimiter(Protocol):
    """Describe the shared request pacing operations used by ZiggyClient."""

    def wait(self: Self) -> None:
        """Wait for request admission."""
        ...

    def observe(self: Self, headers: Any) -> None:
        """Observe response rate-limit headers."""
        ...

    def defer(self: Self, response: Response) -> float:
        """Apply a server-directed shared delay."""
        ...


def validate_ziggy_configuration(host: str, identifier: str) -> tuple[str, str]:
    """Validate and normalize Ziggy queue configuration."""
    host = host.strip()
    identifier = identifier.strip()

    try:
        parsed = urlsplit(f"http://{host}")
        parsed.port
    except ValueError:
        raise ValueError(
            "ZIGGY_HOST must be a hostname with an optional port"
        ) from None

    if (
        parsed.hostname is None
        or any(character.isspace() for character in host)
        or host.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ZIGGY_HOST must be a hostname with an optional port")
    if not identifier:
        raise ValueError("ZIGGY_IDENTIFIER must be nonblank")
    if len(identifier) > 128:
        raise ValueError("ZIGGY_IDENTIFIER must be at most 128 characters")

    return host, identifier


class ZiggyClient:
    """Submit archive targets to a Ziggy HTTP queue."""

    def __init__(
        self: Self,
        host: str,
        identifier: str,
        session: _ZiggySession | None = None,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        """Initialize a reusable Ziggy queue client."""
        self.host, self.identifier = validate_ziggy_configuration(host, identifier)
        self.api_url: str = f"http://{self.host}/v1/queue"
        self.session: _ZiggySession = session or niquests.Session(retries=0)
        self.rate_limiter: _RateLimiter = rate_limiter or _ZIGGY_RATE_LIMITER
        self.retries: int = 3
        self.retry_delay: float = 5.0

    def _post(self: Self, target_url: str) -> Response:
        """Send one paced admission request and classify its response."""
        self.rate_limiter.wait()
        response: Response = self.session.post(
            self.api_url,
            json={"identifier": self.identifier, "urls": [target_url]},
            timeout=30,
            allow_redirects=False,
        )
        self.rate_limiter.observe(response.headers)

        if response.status_code == 429:
            self.rate_limiter.defer(response)

        response.raise_for_status()

        if response.status_code != 202:
            raise ValueError(
                f"Expected Ziggy to return 202 Accepted, got {response.status_code}"
            )

        return response

    @staticmethod
    def _receipt(response: Response) -> str:
        """Extract and validate the receipt for a one-URL submission."""
        data: Any = response.json()
        submissions: Any = data.get("submissions") if isinstance(data, dict) else None

        if not isinstance(submissions, list) or len(submissions) != 1:
            raise ValueError(f"Expected one Ziggy submission receipt, received {data=}")

        submission: Any = submissions[0]
        receipt_id: Any = (
            submission.get("receipt_id") if isinstance(submission, dict) else None
        )
        receipt_url: Any = (
            submission.get("url") if isinstance(submission, dict) else None
        )

        if not isinstance(receipt_id, str) or not isinstance(receipt_url, str):
            raise ValueError(f"Received invalid Ziggy submission receipt {submission=}")

        try:
            UUID(receipt_id)
            parsed_url = urlsplit(receipt_url)
        except ValueError:
            raise ValueError(
                f"Received invalid Ziggy submission receipt {submission=}"
            ) from None

        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname is None:
            raise ValueError(f"Received invalid Ziggy submission receipt {submission=}")

        return receipt_id

    def save(self: Self, target_url: str) -> str | None:
        """Queue one URL, returning its durable receipt when accepted."""
        try:
            response: Response = retry_request(
                lambda: self._post(target_url),
                self.retries,
                self.retry_delay,
                niquests.RequestException,
                retry_transient_request,
            )
            return self._receipt(response)
        except (niquests.RequestException, TypeError, ValueError) as error:
            logger.opt(exception=error).error(
                f"Failed to submit archive target to Ziggy: {target_url}"
            )

            return None

    def close(self: Self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()


class InternetArchiveSession:
    """Reuse one authenticated client and replace it when authentication expires."""

    def __init__(self: Self, account: InternetArchiveAccount | None = None) -> None:
        """Initialize a lazily created Internet Archive client."""
        self.account: InternetArchiveAccount | None = account
        self.authenticated: bool = account is not None
        self._client: InternetArchiveClient | None = None
        self._retired_clients: list[InternetArchiveClient] = []
        self._lock: Lock = Lock()
        self._save_condition: Condition = Condition()
        self._save_queue: deque[object] = deque()
        self._closed: bool = False

    def _new_client(self: Self) -> InternetArchiveClient:
        """Create a client and authenticate it when account credentials are available."""
        client = InternetArchiveClient(account=self.account)

        if self.account is not None:
            try:
                client.login()
            except Exception:
                client.close()
                raise

        return client

    def _get_client(self: Self) -> InternetArchiveClient:
        """Return the current client, creating it once when first needed."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Internet Archive session is closed")
            if self._client is None:
                self._client = self._new_client()

            return self._client

    def _reauthenticate(
        self: Self, failed_client: InternetArchiveClient
    ) -> InternetArchiveClient:
        """Replace a client unless another worker has already replaced it."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Internet Archive session is closed")
            if self._client is failed_client:
                replacement = self._new_client()
                self._retired_clients.append(failed_client)
                self._client = replacement

            if self._client is None:
                raise AssertionError("Internet Archive client was not initialized")

            return self._client

    def _run(self: Self, operation: Callable[[InternetArchiveClient], T]) -> T:
        """Run one operation, retrying it once after an authentication failure."""
        client = self._get_client()

        try:
            return operation(client)
        except AuthenticationError:
            if self.account is None:
                raise

        client = self._reauthenticate(client)

        return operation(client)

    def save(
        self: Self, target_url: str, options: InternetArchiveSaveOptions | None = None
    ) -> InternetArchiveSuccessStatus:
        """Capture a URL after all previously queued captures have completed."""
        queue_entry = object()

        with self._save_condition:
            self._save_queue.append(queue_entry)
            self._save_condition.wait_for(lambda: self._save_queue[0] is queue_entry)

        try:
            effective_options = options or InternetArchiveSaveOptions()
            job = self._run(lambda client: client.submit(target_url, effective_options))
            capture = self._run(lambda client: client.wait(job))

            if effective_options.save_to_archive:
                self._run(lambda client: client.add_to_my_web_archive(capture))

            return capture
        finally:
            with self._save_condition:
                self._save_queue.popleft()
                self._save_condition.notify_all()

    def close(self: Self) -> None:
        """Close every client created by this session."""
        with self._lock:
            if self._closed:
                return

            self._closed = True
            clients = self._retired_clients.copy()

            if self._client is not None:
                clients.append(self._client)

            self._retired_clients.clear()
            self._client = None

        for client in clients:
            client.close()
