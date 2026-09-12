"""Pace and retry synchronous requests."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from threading import Lock
from time import monotonic, sleep
from typing import Self

import niquests
from niquests import Response


def retry_after_seconds(response: Response | None) -> float | None:
    """Parse an HTTP Retry-After header as a non-negative delay."""
    if response is None or not (value := response.headers.get("Retry-After")):
        return None

    try:
        delay: float = float(value)

        return max(delay, 0.0) if isfinite(delay) else None
    except ValueError:
        try:
            retry_at: datetime = parsedate_to_datetime(value)
        except TypeError, ValueError, OverflowError:
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)


class RequestRateLimiter:
    """Space request starts across threads and share server-directed cooldowns."""

    def __init__(self: Self, limit: int, period: float) -> None:
        """Initialize a limiter with one request of boundary headroom."""
        if limit <= 0 or not isfinite(period) or period <= 0:
            raise ValueError("Rate limit and period must be greater than zero")

        self._lock = Lock()
        self._limit: int = limit
        self._period: float = period
        self._interval: float = self._request_interval(limit)
        self._next_request_at: float = 0.0
        self._not_before: float = 0.0

    def wait(self: Self) -> None:
        """Wait until this thread can start its request."""
        while True:
            with self._lock:
                now: float = monotonic()
                request_at: float = max(self._next_request_at, self._not_before)

                if now >= request_at:
                    self._next_request_at = now + self._interval

                    return

                delay: float = request_at - now

            sleep(delay)

    def observe(self: Self, headers: Mapping[str, str]) -> None:
        """Update the request pace from rate-limit response headers."""
        try:
            client_limit: int = int(headers["x-rate-limit-limit"])
        except KeyError, TypeError, ValueError:
            return

        limits: list[int] = [client_limit]

        try:
            limits.append(int(headers["x-rate-limit-ip-limit"]))
        except KeyError, TypeError, ValueError:
            pass

        limit: int = min(limits)

        if limit <= 0:
            return

        with self._lock:
            if limit == self._limit:
                return

            self._limit = limit
            self._interval = self._request_interval(limit)
            self._next_request_at = max(
                self._next_request_at, monotonic() + self._interval
            )

    def defer(self: Self, response: Response) -> float:
        """Pause all callers according to Retry-After or one full limit period."""
        server_delay: float | None = retry_after_seconds(response)
        delay: float = self._period if server_delay is None else server_delay

        with self._lock:
            self._not_before = max(self._not_before, monotonic() + delay)

        return delay

    def _request_interval(self: Self, limit: int) -> float:
        """Leave one request unused so a rolling window cannot cross the limit."""
        return self._period / max(limit - 1, 1)


def retry_transient_request(error: BaseException) -> bool:
    """Retry network failures, rate limits, and server errors."""
    if not isinstance(error, niquests.HTTPError) or error.response is None:
        return True

    status_code: int | None = error.response.status_code

    return status_code is None or status_code == 429 or status_code >= 500


def retry_request[ResponseT](
    request: Callable[[], ResponseT],
    retries: int,
    retry_delay: float,
    exceptions: type[BaseException] | tuple[type[BaseException], ...],
    retry_if: Callable[[BaseException], bool] | None = None,
) -> ResponseT:
    """Run a request, respecting Retry-After between failed attempts."""
    attempt: int = 0

    while True:
        try:
            return request()
        except exceptions as error:
            if (retry_if is not None and not retry_if(error)) or attempt >= retries:
                raise

            attempt += 1
            server_delay: float | None = (
                retry_after_seconds(error.response)
                if isinstance(error, niquests.HTTPError)
                else None
            )
            sleep(max(retry_delay, server_delay or 0.0))
