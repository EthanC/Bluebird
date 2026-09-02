"""Manage shared Internet Archive authentication and captures."""

from collections import deque
from collections.abc import Callable
from threading import Condition, Lock
from typing import Self, TypeVar

from archivist import (
    AuthenticationError,
    InternetArchiveAccount,
    InternetArchiveClient,
    InternetArchiveSaveOptions,
    InternetArchiveSuccessStatus,
)

T = TypeVar("T")


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
