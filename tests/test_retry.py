from types import SimpleNamespace

import niquests
import pytest

import core.retry as retry


def response(headers: dict[str, str], status_code: int = 200):
    return SimpleNamespace(headers=headers, status_code=status_code)


def http_error(status_code: int) -> niquests.HTTPError:
    error = niquests.HTTPError("request failed")
    error.response = response({}, status_code)
    return error


@pytest.mark.parametrize(
    ("value", "expected"), [("5", 5.0), ("-2", 0.0), ("inf", None), ("invalid", None)]
)
def test_parses_retry_after_seconds(value: str, expected: float | None) -> None:
    assert retry.retry_after_seconds(response({"Retry-After": value})) == expected


def test_parses_past_retry_after_date() -> None:
    delay = retry.retry_after_seconds(
        response({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    )

    assert delay == 0.0
    assert retry.retry_after_seconds(None) is None
    assert retry.retry_after_seconds(response({})) is None


def test_rate_limiter_waits_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((0.0, 0.0, 2.0))
    sleeps: list[float] = []
    monkeypatch.setattr(retry, "monotonic", lambda: next(times))
    monkeypatch.setattr(retry, "sleep", sleeps.append)
    limiter = retry.RequestRateLimiter(limit=2, period=2.0)

    limiter.wait()
    limiter.wait()

    assert sleeps == [2.0]


@pytest.mark.parametrize(("limit", "period"), [(0, 1.0), (1, 0.0)])
def test_rate_limiter_rejects_invalid_settings(limit: int, period: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        retry.RequestRateLimiter(limit, period)


def test_rate_limiter_observes_limits_and_defers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retry, "monotonic", lambda: 10.0)
    limiter = retry.RequestRateLimiter(limit=10, period=9.0)

    limiter.observe({"x-rate-limit-limit": "5", "x-rate-limit-ip-limit": "3"})
    assert limiter._limit == 3
    assert limiter._interval == 4.5
    assert limiter._next_request_at == 14.5

    limiter.observe({"x-rate-limit-limit": "invalid"})
    limiter.observe({"x-rate-limit-limit": "0"})
    assert limiter._limit == 3

    assert limiter.defer(response({"Retry-After": "4"})) == 4.0
    assert limiter._not_before == 14.0


def test_classifies_transient_requests() -> None:
    assert retry.retry_transient_request(OSError()) is True
    assert retry.retry_transient_request(http_error(429)) is True
    assert retry.retry_transient_request(http_error(500)) is True
    assert retry.retry_transient_request(http_error(404)) is False


def test_retries_request_with_the_larger_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []
    monkeypatch.setattr(retry, "sleep", sleeps.append)

    def request() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = http_error(429)
            assert error.response is not None
            error.response.headers["Retry-After"] = "3"
            raise error
        return "ok"

    result = retry.retry_request(
        request,
        retries=1,
        retry_delay=1.0,
        exceptions=niquests.HTTPError,
        retry_if=retry.retry_transient_request,
    )

    assert result == "ok"
    assert attempts == 2
    assert sleeps == [3.0]


def test_does_not_retry_rejected_or_exhausted_errors() -> None:
    error = ValueError("failed")

    with pytest.raises(ValueError, match="failed"):
        retry.retry_request(
            lambda: (_ for _ in ()).throw(error),
            retries=2,
            retry_delay=0,
            exceptions=ValueError,
            retry_if=lambda _: False,
        )

    with pytest.raises(ValueError, match="failed"):
        retry.retry_request(
            lambda: (_ for _ in ()).throw(error),
            retries=0,
            retry_delay=0,
            exceptions=ValueError,
        )
