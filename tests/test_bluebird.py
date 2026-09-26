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
