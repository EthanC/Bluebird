from pathlib import Path

import pytest

from core.config import load_x_configs

WEBHOOK = "https://discord.com/api/webhooks/123/token"


def write_config(path: Path, content: str) -> Path:
    config_path = path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_loads_and_normalizes_x_config(tmp_path: Path) -> None:
    config = load_x_configs(
        write_config(
            tmp_path,
            f"""
            [[instances.x]]
            usernames = [" ExampleUser "]
            discord_webhook_url = "{WEBHOOK}"
            cooldown = 30
            retries = 0
            retry_delay = 0
            require_media = true
            require_keyword = [" Release "]
            exclude_reply = true
            exclude_repost = true
            exclude_keyword = [" Leak "]
            archive = true
            proxy = true
            proxy_host = "nitter.example"
            proxy_name = " Nitter "
            """,
        )
    )

    assert len(config) == 1
    assert config[0].usernames == ("ExampleUser",)
    assert config[0].discord_webhook_url == WEBHOOK
    assert config[0].discord_webhook_urls == ()
    assert config[0].cooldown == 30.0
    assert config[0].retries == 0
    assert config[0].retry_delay == 0.0
    assert config[0].require_media is True
    assert config[0].require_keyword == ("Release",)
    assert config[0].exclude_reply is True
    assert config[0].exclude_repost is True
    assert config[0].exclude_keyword == ("Leak",)
    assert config[0].archive is True
    assert config[0].proxy is True
    assert config[0].proxy_host == "nitter.example"
    assert config[0].proxy_name == "Nitter"
    assert config[0].state_key.startswith("123-")


def test_state_key_ignores_order_case_and_webhook_tokens(tmp_path: Path) -> None:
    first = load_x_configs(
        write_config(
            tmp_path,
            """
            [[instances.x]]
            usernames = ["Alice", "Bob"]
            discord_webhook_urls = [
              "https://discord.com/api/webhooks/2/first-secret",
              "https://discord.com/api/webhooks/1/second-secret",
            ]
            require_keyword = ["News", "Update"]
            """,
        )
    )[0]
    second = load_x_configs(
        write_config(
            tmp_path,
            """
            [[instances.x]]
            usernames = ["bob", "alice"]
            discord_webhook_urls = [
              "https://discord.com/api/webhooks/1/replaced",
              "https://discord.com/api/webhooks/2/replaced",
            ]
            require_keyword = ["update", "news"]
            """,
        )
    )[0]

    assert first.state_key == second.state_key


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("title = 'Bluebird'", "Unknown top-level"),
        ("[instances]\nunknown = true", "Unknown instance"),
        ("[instances]", "at least one"),
        (
            f"[[instances.x]]\nusernames = ['bad-name']\ndiscord_webhook_url = '{WEBHOOK}'",
            "invalid username",
        ),
        ("[[instances.x]]\nusernames = ['user']", "exactly one"),
        (
            "[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = 'http://example.com'",
            "Discord webhook URL",
        ),
        (
            f"[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = '{WEBHOOK}'\ncooldown = 0",
            "positive finite",
        ),
        (
            f"[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = '{WEBHOOK}'\nretries = -1",
            "non-negative integer",
        ),
        (
            f"[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = '{WEBHOOK}'\nretry_delay = -1",
            "non-negative finite",
        ),
        (
            f"[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = '{WEBHOOK}'\narchive = 'yes'",
            "must be a Boolean",
        ),
        (
            f"[[instances.x]]\nusernames = ['user']\ndiscord_webhook_url = '{WEBHOOK}'\nproxy_host = 'example.com/path'",
            "must be a hostname",
        ),
        (
            f"[[instances.x]]\nusernames = ['user', 'USER']\ndiscord_webhook_url = '{WEBHOOK}'",
            "must not contain duplicates",
        ),
    ],
)
def test_rejects_invalid_config(tmp_path: Path, content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_x_configs(write_config(tmp_path, content))


def test_rejects_duplicate_instances(tmp_path: Path) -> None:
    instance = f"""
        [[instances.x]]
        usernames = ["user"]
        discord_webhook_url = "{WEBHOOK}"
    """

    with pytest.raises(ValueError, match="duplicate X instance"):
        load_x_configs(write_config(tmp_path, instance + instance))
