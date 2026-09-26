"""Entrypoint for Bluebird."""

import logging
import os
from collections.abc import Callable
from functools import partial
from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from sys import stdout
from threading import Event, Thread
from typing import Any

from archivist import InternetArchiveAccount
from environs import env
from loguru import logger
from loguru_discord import DiscordSink, Intercept

from core.archive import (
    InternetArchiveSession,
    ZiggyClient,
    validate_ziggy_configuration,
)
from core.bettertwitfix import BetterTwitFix
from core.carryfeed import CarryFeed
from core.config import XConfig, load_x_configs
from core.fxembed import FxEmbed
from core.service import ServiceCircuitBreaker
from core.state import StateStore
from core.x import XDataSource, XInstance

PROJECT_ROOT = Path(__file__).resolve().parent


def configure_logging() -> None:
    """Install Bluebird's final console, standard-library, and Discord sinks."""
    level: str = env.str("LOG_LEVEL", "DEBUG")
    logger.remove()
    logger.add(stdout, level=level, backtrace=False, diagnose=False)
    logging.basicConfig(handlers=[Intercept(None)], level=0, force=True)

    logger.info(f"Set console logging level to {level}")

    if url := env.url("LOG_DISCORD_WEBHOOK_URL", None):
        logger.add(
            DiscordSink(url.geturl()),
            level=env.str("LOG_DISCORD_WEBHOOK_LEVEL", "WARNING"),
            backtrace=False,
            diagnose=False,
            enqueue=True,
            filter=lambda record: (
                not (record["name"] or "").startswith(("clyde", "loguru_discord"))
            ),
        )

        logger.info("Enabled logging to Discord webhook")


def run_instance(
    instance: XInstance,
    config: XConfig,
    index: int,
    stop: Event,
    failures: Queue[tuple[int, Exception]],
) -> None:
    """Run an X instance and report unexpected termination to the parent."""
    try:
        instance.start(config, index, stop)
    except Exception as error:
        failures.put((index, error))


def initialize_archive_client(
    enabled: bool,
) -> InternetArchiveSession | ZiggyClient | None:
    """Validate archive settings and create the selected shared client."""
    ziggy_host: str | None = env.str("ZIGGY_HOST", None)
    ziggy_identifier: str | None = env.str("ZIGGY_IDENTIFIER", None)

    if (ziggy_host is None) != (ziggy_identifier is None):
        raise ValueError("ZIGGY_HOST and ZIGGY_IDENTIFIER must be set together")

    if ziggy_host is not None and ziggy_identifier is not None:
        ziggy_host, ziggy_identifier = validate_ziggy_configuration(
            ziggy_host, ziggy_identifier
        )

        return ZiggyClient(ziggy_host, ziggy_identifier) if enabled else None

    archive_email: str | None = env.str("INTERNET_ARCHIVE_EMAIL", None)
    archive_password: str | None = env.str("INTERNET_ARCHIVE_PASSWORD", None)

    if (archive_email is None) != (archive_password is None):
        raise ValueError(
            "INTERNET_ARCHIVE_EMAIL and INTERNET_ARCHIVE_PASSWORD must be set together"
        )

    archive_account: InternetArchiveAccount | None = (
        InternetArchiveAccount(archive_email, archive_password)
        if archive_email is not None and archive_password is not None
        else None
    )

    return InternetArchiveSession(archive_account) if enabled else None


def start() -> None:
    """Initialize Bluebird and begin primary functionality."""
    loaded_environment: bool = bool(env.read_env(PROJECT_ROOT / ".env", recurse=False))
    os.environ.setdefault("TWS_TELEMETRY", "0")
    twscraper_cookies: str | None = env.str("TWSCRAPER_COOKIES", None)
    twscraper_disabled: bool = env.bool("DISABLE_TWSCRAPER", False)
    twscraper_components: tuple[Any, Any, Any] | None = None

    if twscraper_cookies and not twscraper_disabled:
        # Twscrape replaces all Loguru sinks when imported. Import it only after
        # environment setup, then restore Bluebird's sinks before any work starts.
        from core.twscraper import (
            Twscraper,
            TwscraperConfigurationError,
            TwscraperRuntime,
            validate_cookies,
        )

        twscraper_components = (
            Twscraper,
            TwscraperRuntime,
            TwscraperConfigurationError,
        )

    configure_logging()
    logger.info("Bluebird")
    logger.info("https://github.com/EthanC/Bluebird")

    if loaded_environment:
        logger.info("Loaded environment variables")

    if twscraper_cookies and not twscraper_disabled:
        try:
            validate_cookies(twscraper_cookies)
        except TwscraperConfigurationError as error:
            logger.critical(str(error))
            raise SystemExit(1) from None
    elif twscraper_disabled:
        logger.warning("Disabled Twscraper data source service via DISABLE_TWSCRAPER")

    try:
        failure_threshold: int = env.int("SERVICE_FAILURE_THRESHOLD", 10)
        disable_seconds: float = env.float("SERVICE_DISABLE_SECONDS", 3_600.0)
        disable_error_threshold: int = env.int("SERVICE_DISABLE_ERROR_THRESHOLD", 24)

        if failure_threshold <= 0:
            raise ValueError("SERVICE_FAILURE_THRESHOLD must be greater than zero")
        if not isfinite(disable_seconds) or disable_seconds <= 0:
            raise ValueError(
                "SERVICE_DISABLE_SECONDS must be finite and greater than zero"
            )
        if disable_error_threshold <= 0:
            raise ValueError(
                "SERVICE_DISABLE_ERROR_THRESHOLD must be greater than zero"
            )
    except Exception as e:
        logger.opt(exception=e).critical("Failed to initialize service disable rules")

        raise SystemExit(1) from e

    try:
        config_path: Path = PROJECT_ROOT / "config.toml"
        configs: tuple[XConfig, ...] = load_x_configs(config_path)
        state: StateStore = StateStore(PROJECT_ROOT / "data" / "state.toml")
    except Exception as e:
        logger.opt(exception=e).critical("Failed to initialize configuration and state")

        raise SystemExit(1) from e

    logger.info(f"Loaded {len(configs):,} X instances from config.toml")
    logger.info(f"Using persistent state at {state.path}")

    source_factories: list[Callable[[], XDataSource]] = []
    twscraper_runtime: Any | None = None

    if twscraper_components is not None and twscraper_cookies is not None:
        Twscraper, TwscraperRuntime, _ = twscraper_components
        twscraper_runtime = TwscraperRuntime(PROJECT_ROOT / "data" / "twscraper.db")
        circuit_breaker = ServiceCircuitBreaker(
            "Twscraper", failure_threshold, disable_seconds, disable_error_threshold
        )
        source_factories.append(partial(Twscraper, circuit_breaker, twscraper_runtime))

    for variable, service_name, factory in (
        ("DISABLE_BETTERTWITFIX", "BetterTwitFix", BetterTwitFix),
        ("DISABLE_CARRYFEED", "CarryFeed", CarryFeed),
        ("DISABLE_FXEMBED", "FxEmbed", FxEmbed),
    ):
        if env.bool(variable, False):
            logger.warning(
                f"Disabled {service_name} data source service via {variable}"
            )
        else:
            circuit_breaker = ServiceCircuitBreaker(
                service_name,
                failure_threshold,
                disable_seconds,
                disable_error_threshold,
            )
            source_factories.append(partial(factory, circuit_breaker))

    if not source_factories:
        logger.critical("All X data source services are disabled")

        raise SystemExit(1)

    stop: Event = Event()
    failures: Queue[tuple[int, Exception]] = Queue()
    threads: list[Thread] = []
    health_path_value: str | None = env.str("BLUEBIRD_HEALTHCHECK_FILE", None)
    health_path: Path | None = (
        Path(health_path_value) if health_path_value is not None else None
    )
    archive_client: InternetArchiveSession | ZiggyClient | None = None

    try:
        try:
            archive_client = initialize_archive_client(
                any(config.archive for config in configs)
            )
        except Exception as error:
            logger.opt(exception=error).critical(
                "Failed to initialize archive configuration"
            )
            raise SystemExit(1) from error

        if twscraper_runtime is not None and twscraper_cookies is not None:
            try:
                twscraper_runtime.start(twscraper_cookies)
            except Exception as error:
                logger.opt(exception=error).critical(
                    "Failed to initialize Twscraper data source"
                )
                raise SystemExit(1) from error

            logger.info(
                "Enabled Twscraper data source with persistent state at "
                f"{twscraper_runtime.database_path}"
            )

        for index, config in enumerate(configs):
            instance = XInstance(
                [factory() for factory in source_factories],
                state,
                archive_client if config.archive else None,
            )
            thread = Thread(
                target=run_instance,
                args=(instance, config, index, stop, failures),
                name=f"x-{index}",
            )
            thread.start()
            threads.append(thread)

        while not stop.wait(1):
            if health_path:
                if all(thread.is_alive() for thread in threads):
                    health_path.touch()
                else:
                    health_path.unlink(missing_ok=True)

            try:
                index, error = failures.get_nowait()
            except Empty:
                continue

            raise RuntimeError(f"X[{index}] worker stopped unexpectedly") from error
    except KeyboardInterrupt:
        logger.info("Shutting down Bluebird")
    finally:
        stop.set()

        if health_path:
            health_path.unlink(missing_ok=True)

        if twscraper_runtime is not None:
            twscraper_runtime.close()

        for thread in threads:
            thread.join()

        if archive_client:
            archive_client.close()

        logger.complete()


if __name__ == "__main__":
    start()
