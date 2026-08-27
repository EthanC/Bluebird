"""Temporarily disable unstable external services."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Self

from loguru import logger


class ServiceFailure(Exception):
    """Indicate that a service request failed."""


class ServiceNotFound(Exception):
    """Indicate that a service successfully reported a missing resource."""


@dataclass(frozen=True, slots=True)
class _Permit:
    """Identify one operation admitted by a circuit breaker generation."""

    generation: int
    probe: bool = False


class ServiceCircuitBreaker:
    """Disable one service after consecutive failures across all callers."""

    def __init__(
        self: Self,
        service_name: str,
        failure_threshold: int,
        disable_seconds: float,
        disable_error_threshold: int,
    ) -> None:
        """Initialize a closed service circuit breaker."""
        self.service_name: str = service_name
        self.failure_threshold: int = failure_threshold
        self.disable_seconds: float = disable_seconds
        self.disable_error_threshold: int = disable_error_threshold
        self._lock: Lock = Lock()
        self._failures: int = 0
        self._disable_count: int = 0
        self._disabled_until: float | None = None
        self._probe_in_flight: bool = False
        self._generation: int = 0

    def call[ResultT](self: Self, operation: Callable[[], ResultT]) -> ResultT | None:
        """Run an operation when enabled and update service health from its result."""
        permit: _Permit | None = self._acquire()

        if permit is None:
            return None

        try:
            result: ResultT = operation()
        except ServiceNotFound:
            self._succeed(permit)

            return None
        except ServiceFailure:
            self._fail(permit)

            return None
        except BaseException:
            self._fail(permit)

            raise

        self._succeed(permit)

        return result

    def _acquire(self: Self) -> _Permit | None:
        """Admit an operation or reject it while this service is disabled."""
        with self._lock:
            if self._disabled_until is None:
                return _Permit(self._generation)

            if monotonic() < self._disabled_until or self._probe_in_flight:
                return None

            self._probe_in_flight = True
            permit: _Permit = _Permit(self._generation, probe=True)

        logger.info(f"Probing disabled {self.service_name} data source service")

        return permit

    def _succeed(self: Self, permit: _Permit) -> None:
        """Record a successful operation if its permit is still current."""
        restored: bool = False

        with self._lock:
            if permit.generation != self._generation:
                return

            if permit.probe:
                self._disabled_until = None
                self._probe_in_flight = False
                self._disable_count = 0
                self._generation += 1
                restored = True

            self._failures = 0

        if restored:
            logger.info(f"Restored {self.service_name} data source service")

    def _fail(self: Self, permit: _Permit) -> None:
        """Record a failed operation if its permit is still current."""
        failures: int | None = None
        disable_count: int | None = None

        with self._lock:
            if permit.generation != self._generation:
                return

            self._failures += 1

            if permit.probe or self._failures >= self.failure_threshold:
                self._disabled_until = monotonic() + self.disable_seconds
                self._probe_in_flight = False
                self._disable_count += 1
                self._generation += 1
                failures = self._failures
                disable_count = self._disable_count

        if failures is not None and disable_count is not None:
            message: str = (
                f"Disabled {self.service_name} data source service for "
                f"{self.disable_seconds:g}s after {failures:,} consecutive failures "
                f"(disable {disable_count:,})"
            )

            if disable_count == self.disable_error_threshold:
                logger.error(message)

                return

            logger.warning(message)
