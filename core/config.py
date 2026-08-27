"""Load and validate Bluebird configuration."""

import math
import re
import tomllib
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_]{1,15}")
WEBHOOK_PATH_PATTERN = re.compile(r"/api/webhooks/\d+/[^/]+")


@dataclass(frozen=True, slots=True)
class XConfig:
    """Contain one validated X monitoring configuration."""

    usernames: tuple[str, ...]
    discord_webhook_url: str
    state_key: str
    cooldown: float = 60.0
    retries: int = 3
    retry_delay: float = 5.0
    require_media: bool = False
    require_keyword: tuple[str, ...] = ()
    exclude_reply: bool = False
    exclude_repost: bool = False
    exclude_keyword: tuple[str, ...] = ()
    proxy: bool = False
    proxy_host: str = "twstalker.com"
    proxy_name: str = "TwStalker"


def load_x_configs(path: Path) -> tuple[XConfig, ...]:
    """Load all X instances from a TOML configuration file."""
    with path.open("rb") as file:
        config: Any = tomllib.load(file)

    if not isinstance(config, dict):
        raise ValueError("config.toml must contain a TOML table")

    unknown_root: set[str] = set(config) - {"instances"}
    if unknown_root:
        raise ValueError(f"Unknown top-level configuration keys: {_keys(unknown_root)}")

    instances: Any = config.get("instances")

    if not isinstance(instances, dict):
        raise ValueError("config.toml must define an [instances] table")

    unknown_instances: set[str] = set(instances) - {"x"}
    if unknown_instances:
        raise ValueError(
            f"Unknown instance configuration keys: {_keys(unknown_instances)}"
        )

    x_instances: Any = instances.get("x")

    if not isinstance(x_instances, list) or not x_instances:
        raise ValueError("config.toml must define at least one [[instances.x]] table")

    configs: tuple[XConfig, ...] = tuple(
        _parse_x_config(instance, index) for index, instance in enumerate(x_instances)
    )

    if len({config.state_key for config in configs}) != len(configs):
        raise ValueError("config.toml contains duplicate X instance configurations")

    return configs


def _parse_x_config(value: Any, index: int) -> XConfig:
    """Validate and normalize one X instance table."""
    label: str = f"instances.x[{index}]"

    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a table")

    allowed: set[str] = {
        "usernames",
        "discord_webhook_url",
        "cooldown",
        "retries",
        "retry_delay",
        "require_media",
        "require_keyword",
        "exclude_reply",
        "exclude_repost",
        "exclude_keyword",
        "proxy",
        "proxy_host",
        "proxy_name",
    }
    unknown: set[str] = set(value) - allowed

    if unknown:
        raise ValueError(f"Unknown keys in {label}: {_keys(unknown)}")

    usernames: tuple[str, ...] = _string_list(
        value.get("usernames"), f"{label}.usernames", required=True
    )

    for username in usernames:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(
                f"{label}.usernames contains invalid username {username!r}"
            )

    webhook_url: Any = value.get("discord_webhook_url")

    if not isinstance(webhook_url, str) or not _valid_webhook_url(webhook_url):
        raise ValueError(f"{label}.discord_webhook_url must be a Discord webhook URL")

    cooldown: Any = value.get("cooldown", 60.0)

    if (
        isinstance(cooldown, bool)
        or not isinstance(cooldown, int | float)
        or not math.isfinite(cooldown)
        or cooldown <= 0
    ):
        raise ValueError(f"{label}.cooldown must be a positive finite number")

    retries: Any = value.get("retries", 3)

    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError(f"{label}.retries must be a non-negative integer")

    retry_delay: Any = value.get("retry_delay", 5.0)

    if (
        isinstance(retry_delay, bool)
        or not isinstance(retry_delay, int | float)
        or not math.isfinite(retry_delay)
        or retry_delay < 0
    ):
        raise ValueError(f"{label}.retry_delay must be a non-negative finite number")

    config = XConfig(
        usernames=usernames,
        discord_webhook_url=webhook_url,
        state_key="",
        cooldown=float(cooldown),
        retries=retries,
        retry_delay=float(retry_delay),
        require_media=_boolean(value, "require_media", label),
        require_keyword=_string_list(
            value.get("require_keyword"), f"{label}.require_keyword"
        ),
        exclude_reply=_boolean(value, "exclude_reply", label),
        exclude_repost=_boolean(value, "exclude_repost", label),
        exclude_keyword=_string_list(
            value.get("exclude_keyword"), f"{label}.exclude_keyword"
        ),
        proxy=_boolean(value, "proxy", label),
        proxy_host=_proxy_host(value.get("proxy_host", "twstalker.com"), label),
        proxy_name=_nonempty_string(
            value.get("proxy_name", "TwStalker"), "proxy_name", label
        ),
    )

    return replace(config, state_key=_state_key(config))


def _boolean(config: dict[str, Any], key: str, label: str) -> bool:
    """Read an optional Boolean setting."""
    value: Any = config.get(key, False)

    if not isinstance(value, bool):
        raise ValueError(f"{label}.{key} must be a Boolean")

    return value


def _nonempty_string(value: Any, key: str, label: str) -> str:
    """Read and normalize a non-empty string setting."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")

    return value.strip()


def _proxy_host(value: Any, label: str) -> str:
    """Read a proxy hostname without a scheme, path, or port."""
    host: str = _nonempty_string(value, "proxy_host", label)

    try:
        parsed = urlsplit(f"https://{host}")
        port: int | None = parsed.port
    except ValueError:
        raise ValueError(f"{label}.proxy_host must be a hostname") from None

    if (
        parsed.hostname is None
        or any(character.isspace() for character in host)
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label}.proxy_host must be a hostname")

    return host


def _string_list(value: Any, label: str, *, required: bool = False) -> tuple[str, ...]:
    """Read an optional list of non-empty strings."""
    if value is None and not required:
        return ()

    if (
        not isinstance(value, list)
        or (required and not value)
        or any(not isinstance(entry, str) or not entry.strip() for entry in value)
    ):
        requirement: str = "a non-empty list" if required else "a list"
        raise ValueError(f"{label} must be {requirement} of non-empty strings")

    normalized: tuple[str, ...] = tuple(entry.strip() for entry in value)

    if len(set(entry.casefold() for entry in normalized)) != len(normalized):
        raise ValueError(f"{label} must not contain duplicates")

    return normalized


def _valid_webhook_url(url: str) -> bool:
    """Return whether a URL identifies a Discord webhook."""
    try:
        parsed = urlsplit(url)
        port: int | None = parsed.port
    except ValueError:
        return False

    hosts: set[str] = {
        "discord.com",
        "www.discord.com",
        "canary.discord.com",
        "ptb.discord.com",
        "discordapp.com",
        "www.discordapp.com",
    }
    return (
        parsed.scheme == "https"
        and parsed.hostname in hosts
        and parsed.username is None
        and parsed.password is None
        and port is None
        and WEBHOOK_PATH_PATTERN.fullmatch(parsed.path) is not None
    )


def _state_key(config: XConfig) -> str:
    """Create a stable non-secret identity for an X instance."""
    webhook_id: str = urlsplit(config.discord_webhook_url).path.split("/")[3]
    fields: tuple[object, ...] = (
        webhook_id,
        tuple(sorted(username.casefold() for username in config.usernames)),
        config.require_media,
        tuple(sorted(keyword.casefold() for keyword in config.require_keyword)),
        config.exclude_reply,
        config.exclude_repost,
        tuple(sorted(keyword.casefold() for keyword in config.exclude_keyword)),
        config.proxy,
        config.proxy_host.casefold() if config.proxy else None,
        config.proxy_name if config.proxy else None,
    )
    digest: str = sha256(repr(fields).encode()).hexdigest()[:16]

    return f"{webhook_id}-{digest}"


def _keys(keys: set[str]) -> str:
    """Format configuration keys for an error message."""
    return ", ".join(sorted(repr(key) for key in keys))
