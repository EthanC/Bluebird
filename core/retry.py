"""Retry synchronous requests with a fixed delay."""

from time import sleep
from typing import Callable


def retry_request[ResponseT](
    request: Callable[[], ResponseT],
    retries: int,
    retry_delay: float,
    exceptions: type[BaseException] | tuple[type[BaseException], ...],
) -> ResponseT:
    """Run a request, waiting a fixed delay after each failed attempt."""
    attempt: int = 0

    while True:
        try:
            return request()
        except exceptions:
            if attempt >= retries:
                raise

            attempt += 1
            sleep(retry_delay)
