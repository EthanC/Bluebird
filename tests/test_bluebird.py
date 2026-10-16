import signal
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import bluebird

ARCHIVE_VARIABLES = (
    "ZIGGY_HOST",
    "ZIGGY_IDENTIFIER",
    "INTERNET_ARCHIVE_EMAIL",
    "INTERNET_ARCHIVE_PASSWORD",
)


@pytest.fixture(autouse=True)
def clear_archive_environment(monkeypatch):
    for variable in ARCHIVE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.parametrize(
    ("variable", "value"),
    [("ZIGGY_HOST", "ziggy:9449"), ("ZIGGY_IDENTIFIER", "bluebird")],
)
def test_ziggy_settings_must_be_paired(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match="must be set together"):
        bluebird.initialize_archive_client(True)


def test_ziggy_takes_precedence_over_internet_archive(monkeypatch):
    selected = object()
    monkeypatch.setenv("ZIGGY_HOST", "ziggy:9449")
    monkeypatch.setenv("ZIGGY_IDENTIFIER", "bluebird")
    monkeypatch.setenv("INTERNET_ARCHIVE_EMAIL", "email-only-is-invalid")
    monkeypatch.setattr(bluebird, "ZiggyClient", lambda *_args: selected)
    monkeypatch.setattr(
        bluebird,
        "InternetArchiveAccount",
        lambda *_args: pytest.fail("Internet Archive account was constructed"),
    )
    monkeypatch.setattr(
        bluebird,
        "InternetArchiveSession",
        lambda *_args: pytest.fail("Internet Archive client was constructed"),
    )

    assert bluebird.initialize_archive_client(True) is selected


def test_invalid_ziggy_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("ZIGGY_HOST", "ziggy/path")
    monkeypatch.setenv("ZIGGY_IDENTIFIER", "bluebird")
    monkeypatch.setenv("INTERNET_ARCHIVE_EMAIL", "user@example.com")
    monkeypatch.setenv("INTERNET_ARCHIVE_PASSWORD", "password")
    monkeypatch.setattr(
        bluebird,
        "InternetArchiveAccount",
        lambda *_args: pytest.fail("Internet Archive account was constructed"),
    )

    with pytest.raises(ValueError, match="hostname"):
        bluebird.initialize_archive_client(True)


def test_direct_archive_client_remains_default(monkeypatch):
    account = object()
    selected = object()
    monkeypatch.setenv("INTERNET_ARCHIVE_EMAIL", "user@example.com")
    monkeypatch.setenv("INTERNET_ARCHIVE_PASSWORD", "password")
    monkeypatch.setattr(bluebird, "InternetArchiveAccount", lambda *_args: account)
    monkeypatch.setattr(
        bluebird,
        "InternetArchiveSession",
        lambda received: selected if received is account else None,
    )

    assert bluebird.initialize_archive_client(True) is selected


def test_disabled_archiving_creates_no_ziggy_client(monkeypatch):
    monkeypatch.setenv("ZIGGY_HOST", "ziggy:9449")
    monkeypatch.setenv("ZIGGY_IDENTIFIER", "bluebird")
    monkeypatch.setattr(
        bluebird,
        "ZiggyClient",
        lambda *_args: pytest.fail("Ziggy client was constructed"),
    )
    monkeypatch.setattr(
        bluebird,
        "InternetArchiveAccount",
        lambda *_args: pytest.fail("Internet Archive account was constructed"),
    )

    assert bluebird.initialize_archive_client(False) is None


def test_sigterm_runs_runtime_and_worker_cleanup(monkeypatch, tmp_path: Path):
    import core.twscraper as twscraper

    previous_handler = signal.getsignal(signal.SIGTERM)
    runtime = Mock(database_path=tmp_path / "twscraper.db")
    thread = Mock()

    class StopOnWait(Event):
        def wait(self, timeout=None):
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)
            return self.is_set()

    monkeypatch.setenv("TWSCRAPER_COOKIES", "auth_token=test; ct0=test")
    monkeypatch.setenv("DISABLE_TWSCRAPER", "false")
    for variable in ("DISABLE_BETTERTWITFIX", "DISABLE_CARRYFEED", "DISABLE_FXEMBED"):
        monkeypatch.setenv(variable, "true")
    monkeypatch.delenv("BLUEBIRD_HEALTHCHECK_FILE", raising=False)
    monkeypatch.setattr(bluebird.env, "read_env", lambda *args, **kwargs: False)
    monkeypatch.setattr(bluebird, "configure_logging", lambda: None)
    monkeypatch.setattr(bluebird, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        bluebird, "load_x_configs", lambda _: (SimpleNamespace(archive=False),)
    )
    monkeypatch.setattr(bluebird, "StateStore", lambda path: SimpleNamespace(path=path))
    monkeypatch.setattr(bluebird, "Thread", lambda **kwargs: thread)
    monkeypatch.setattr(bluebird, "Event", StopOnWait)
    monkeypatch.setattr(twscraper, "TwscraperRuntime", lambda _: runtime)

    bluebird.start()

    runtime.start.assert_called_once_with("auth_token=test; ct0=test")
    runtime.close.assert_called_once_with()
    thread.start.assert_called_once_with()
    thread.join.assert_called_once_with()
    assert signal.getsignal(signal.SIGTERM) == previous_handler
