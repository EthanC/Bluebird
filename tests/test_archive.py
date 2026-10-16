from typing import cast

import niquests
import pytest
from niquests import Response

import core.archive as archive

RECEIPT_ID = "00000000-0000-4000-8000-000000000000"
TARGET_URL = "https://nitter.example/user/status/1"


class FakeResponse:
    def __init__(self, status_code=202, data=None, headers=None, text=""):
        self.status_code = status_code
        self.text = text
        self.data = (
            data
            if data is not None
            else {"submissions": [{"receipt_id": RECEIPT_ID, "url": TARGET_URL}]}
        )
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = niquests.HTTPError("request failed")
            error.response = cast(Response, self)
            raise error
        return self

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


class FakeLimiter:
    def __init__(self):
        self.waits = 0
        self.observed = []
        self.deferred = []

    def wait(self):
        self.waits += 1

    def observe(self, headers):
        self.observed.append(headers)

    def defer(self, response):
        self.deferred.append(response)
        return float(response.headers.get("Retry-After", 60))


@pytest.fixture
def error_logs():
    records = []
    sink = archive.logger.add(
        lambda message: records.append(message.record), level="ERROR"
    )
    try:
        yield records
    finally:
        archive.logger.remove(sink)


@pytest.mark.parametrize(
    ("host", "identifier", "message"),
    [
        ("http://ziggy", "bluebird", "hostname"),
        ("user:pass@ziggy", "bluebird", "hostname"),
        ("ziggy/path", "bluebird", "hostname"),
        ("ziggy host", "bluebird", "hostname"),
        ("ziggy:invalid", "bluebird", "hostname"),
        ("ziggy:", "bluebird", "hostname"),
        ("ziggy", " ", "nonblank"),
        ("ziggy", "x" * 129, "at most 128"),
    ],
)
def test_rejects_invalid_ziggy_configuration(host, identifier, message):
    with pytest.raises(ValueError, match=message):
        archive.validate_ziggy_configuration(host, identifier)


@pytest.mark.parametrize("host", ["ziggy.example", "192.0.2.10:9449"])
def test_normalizes_ziggy_configuration(host):
    assert archive.validate_ziggy_configuration(f" {host} ", " bluebird ") == (
        host,
        "bluebird",
    )


def test_submits_payload_and_returns_receipt(error_logs):
    response = FakeResponse(headers={"x-rate-limit-limit": "60"})
    session = FakeSession(response)
    limiter = FakeLimiter()
    client = archive.ZiggyClient("ziggy:9449", "bluebird", session, limiter)

    assert client.save(TARGET_URL) == RECEIPT_ID
    assert session.calls == [
        (
            "http://ziggy:9449/v1/queue",
            {
                "json": {"identifier": "bluebird", "urls": [TARGET_URL]},
                "timeout": 30,
                "allow_redirects": False,
            },
        )
    ]
    assert limiter.waits == 1
    assert limiter.observed == [{"x-rate-limit-limit": "60"}]
    assert error_logs == []


def test_retries_server_errors_and_honors_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("core.retry.sleep", sleeps.append)
    limited = FakeResponse(429, headers={"Retry-After": "7"})
    server_error = FakeResponse(503, headers={"Retry-After": "2"})
    session = FakeSession(limited, server_error, FakeResponse())
    limiter = FakeLimiter()
    client = archive.ZiggyClient("ziggy", "bluebird", session, limiter)
    client.retry_delay = 1

    assert client.save(TARGET_URL) == RECEIPT_ID
    assert len(session.calls) == 3
    assert limiter.deferred == [limited]
    assert sleeps == [7.0, 2.0]


def test_retries_network_errors_until_exhausted(monkeypatch, error_logs):
    monkeypatch.setattr("core.retry.sleep", lambda _delay: None)
    failure = niquests.ConnectionError("offline")
    session = FakeSession(failure, failure)
    client = archive.ZiggyClient("ziggy", "bluebird", session, FakeLimiter())
    client.retries = 1
    client.retry_delay = 0

    assert client.save(TARGET_URL) is None
    assert len(session.calls) == 2
    assert len(error_logs) == 1
    message = error_logs[0]["message"]
    assert TARGET_URL in message
    assert client.api_url in message
    assert "identifier='bluebird'" in message
    assert "offline" in message
    assert error_logs[0]["exception"].value is failure


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"submissions": []},
        {"submissions": [{"receipt_id": "invalid", "url": TARGET_URL}]},
        {"submissions": [{"receipt_id": RECEIPT_ID, "url": "relative"}]},
        ValueError("invalid JSON"),
    ],
)
def test_rejects_malformed_receipts_without_retry(data, error_logs):
    session = FakeSession(FakeResponse(data=data))
    client = archive.ZiggyClient("ziggy", "bluebird", session, FakeLimiter())

    assert client.save(TARGET_URL) is None
    assert len(session.calls) == 1
    assert len(error_logs) == 1
    assert "HTTP 202" in error_logs[0]["message"]
    assert "ValueError" in error_logs[0]["message"]
    assert client.api_url in error_logs[0]["message"]


def test_rejects_nonaccepted_success_without_retry():
    session = FakeSession(FakeResponse(status_code=200))
    client = archive.ZiggyClient("ziggy", "bluebird", session, FakeLimiter())

    assert client.save(TARGET_URL) is None
    assert len(session.calls) == 1


@pytest.mark.parametrize("status_code", [200, 302, 400, 401, 403, 404, 422, 429, 503])
def test_logs_unaccepted_response_details(status_code, error_logs, monkeypatch):
    monkeypatch.setattr("core.retry.sleep", lambda _delay: None)
    response = Response()
    response.status_code = status_code
    response.url = "http://ziggy:9449/v1/queue"
    response._content = b'{"error":"identifier is not allowed"}'
    session = FakeSession(response, response)
    client = archive.ZiggyClient("ziggy:9449", "bluebird", session, FakeLimiter())
    client.retries = 1

    assert client.save(TARGET_URL) is None
    assert len(session.calls) == (2 if status_code in {429, 503} else 1)
    assert len(error_logs) == 1
    record = error_logs[0]
    assert record["level"].name == "ERROR"
    assert TARGET_URL in record["message"]
    assert client.api_url in record["message"]
    assert "identifier='bluebird'" in record["message"]
    assert f"HTTP {status_code}" in record["message"]
    assert "identifier is not allowed" in record["message"]


def test_bounds_response_body_in_failure_log(error_logs):
    response = FakeResponse(403, text="rejected\n" + "x" * 2_000 + "hidden tail")
    client = archive.ZiggyClient(
        "ziggy", "bluebird", FakeSession(response), FakeLimiter()
    )

    assert client.save(TARGET_URL) is None
    message = error_logs[0]["message"]
    assert "rejected\\n" in message
    assert "\n" not in message
    assert "[truncated]" in message
    assert "hidden tail" not in message
    assert len(message) < 1_500


def test_logs_response_for_invalid_json_receipt(error_logs):
    response = Response()
    response.status_code = 202
    response._content = b"not JSON"
    session = FakeSession(response)
    client = archive.ZiggyClient("ziggy", "bluebird", session, FakeLimiter())

    assert client.save(TARGET_URL) is None
    assert len(session.calls) == 1
    assert "HTTP 202" in error_logs[0]["message"]
    assert "response body='not JSON'" in error_logs[0]["message"]


def test_closes_ziggy_session():
    session = FakeSession()
    client = archive.ZiggyClient("ziggy", "bluebird", session, FakeLimiter())

    client.close()

    assert session.closed is True
