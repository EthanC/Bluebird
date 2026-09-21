import pytest

import core.service as service


def test_success_and_not_found_keep_service_available() -> None:
    breaker = service.ServiceCircuitBreaker("Test", 2, 10.0, 2)

    assert breaker.call(lambda: "result") == "result"

    def missing() -> None:
        raise service.ServiceNotFound

    assert breaker.call(missing) is None
    assert breaker._failures == 0
    assert breaker._disabled_until is None


def test_failures_disable_probe_and_restore_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(service, "monotonic", lambda: now)
    breaker = service.ServiceCircuitBreaker("Test", 2, 10.0, 2)

    def fail() -> None:
        raise service.ServiceFailure

    assert breaker.call(fail) is None
    assert breaker.call(fail) is None
    assert breaker.call(lambda: "blocked") is None
    assert breaker._disabled_until == 110.0

    now = 111.0
    assert breaker.call(fail) is None
    assert breaker._disable_count == 2
    assert breaker._disabled_until == 121.0

    now = 122.0
    assert breaker.call(lambda: "restored") == "restored"
    assert breaker._disabled_until is None
    assert breaker._disable_count == 0
    assert breaker._failures == 0


def test_unexpected_errors_count_as_failures() -> None:
    breaker = service.ServiceCircuitBreaker("Test", 1, 10.0, 2)

    with pytest.raises(RuntimeError, match="unexpected"):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("unexpected")))

    assert breaker._disabled_until is not None
