"""Retry synchronous requests with a fixed delay."""

from time import sleep
from typing import Callable

import niquests


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
    """Run a request, waiting a fixed delay after each failed attempt."""
    attempt: int = 0

    while True:
        try:
            return request()
        except exceptions as error:
            if (retry_if is not None and not retry_if(error)) or attempt >= retries:
                raise

            attempt += 1
            sleep(retry_delay)
