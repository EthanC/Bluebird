"""Entrypoint for Bluebird."""

import logging
from collections.abc import Callable
from functools import partial
from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from sys import stdout
from threading import Event, Thread

from environs import env
from loguru import logger
from loguru_discord import DiscordSink, Intercept

from core.bettertwitfix import BetterTwitFix
from core.config import XConfig, load_x_configs
from core.fxembed import FxEmbed
from core.service import ServiceCircuitBreaker
from core.state import StateStore
from core.twitterwebviewer import TwitterWebViewer
from core.x import XDataSource, XInstance

PROJECT_ROOT = Path(__file__).resolve().parent


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


def start() -> None:
    """Initialize Bluebird and begin primary functionality."""
    logger.info("Bluebird")
    logger.info("https://github.com/EthanC/Bluebird")

    # Reroute standard logging to Loguru
    logging.basicConfig(handlers=[Intercept(None)], level=0, force=True)

    if env.read_env(PROJECT_ROOT / ".env", recurse=False):
        logger.info("Loaded environment variables")

    if level := env.str("LOG_LEVEL", None):
        logger.remove()
        logger.add(stdout, level=level)

        logger.info(f"Set console logging level to {level}")

    if url := env.url("LOG_DISCORD_WEBHOOK_URL", None):
        logger.add(
            DiscordSink(url.geturl()),
            level=env.str("LOG_DISCORD_WEBHOOK_LEVEL", "WARNING"),
            backtrace=False,
            enqueue=True,
            filter=lambda record: (
                not (record["name"] or "").startswith(("clyde", "loguru_discord"))
            ),
        )

        logger.info("Enabled logging to Discord webhook")

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

    source_factories: list[Callable[[], XDataSource]] = []

    for variable, service_name, factory in (
        ("DISABLE_BETTERTWITFIX", "BetterTwitFix", BetterTwitFix),
        ("DISABLE_FXEMBED", "FxEmbed", FxEmbed),
        ("DISABLE_TWITTERWEBVIEWER", "TwitterWebViewer", TwitterWebViewer),
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

    try:
        config_path: Path = PROJECT_ROOT / "config.toml"
        configs: tuple[XConfig, ...] = load_x_configs(config_path)
        state: StateStore = StateStore(PROJECT_ROOT / "data" / "state.toml")
    except Exception as e:
        logger.opt(exception=e).critical("Failed to initialize configuration and state")

        raise SystemExit(1) from e

    logger.info(f"Loaded {len(configs):,} X instances from config.toml")
    logger.info(f"Using persistent state at {state.path}")

    stop: Event = Event()
    failures: Queue[tuple[int, Exception]] = Queue()
    threads: list[Thread] = []

    for index, config in enumerate(configs):
        instance = XInstance([factory() for factory in source_factories], state)
        thread = Thread(
            target=run_instance,
            args=(instance, config, index, stop, failures),
            name=f"x-{index}",
        )
        thread.start()
        threads.append(thread)

    try:
        while not stop.wait(1):
            try:
                index, error = failures.get_nowait()
            except Empty:
                continue

            raise RuntimeError(f"X[{index}] worker stopped unexpectedly") from error
    except KeyboardInterrupt:
        logger.info("Shutting down Bluebird")
    finally:
        stop.set()

        for thread in threads:
            thread.join()

        logger.complete()


if __name__ == "__main__":
    start()
