import logging
import tomllib
from os import environ
from sys import stdout
from time import sleep
from typing import Any

from environs import env
from loguru import logger
from loguru_discord import DiscordSink

from core.intercept import Intercept
from core.x import XInstance


def start() -> None:
    """Initialize Bluebird and begin primary functionality."""

    logger.success("Bluebird")
    logger.success("https://github.com/EthanC/Bluebird")

    # Reroute standard logging to Loguru
    logging.basicConfig(handlers=[Intercept()], level=0, force=True)

    if env.read_env(recurse=False):
        logger.info("Loaded environment variables")

    if environ.get("LOG_LEVEL"):
        level: str = env.str("LOG_LEVEL")

        logger.remove()
        logger.add(stdout, level=level)

        logger.info(f"Set console logging level to {level}")

    if environ.get("LOG_DISCORD_WEBHOOK_URL"):
        url: str = env.url("LOG_DISCORD_WEBHOOK_URL").geturl()

        logger.add(
            DiscordSink(url),
            level=env.str("LOG_DISCORD_WEBHOOK_LEVEL"),
            backtrace=False,
        )

        logger.info("Enabled logging to Discord webhook")
        logger.trace(f"{url=}")

    config: dict[str, Any] | None = None

    try:
        with open("config.toml", "r") as file:
            config = tomllib.loads(file.read())
    except Exception as e:
        logger.opt(exception=e).critical("Failed to load config.toml")

        return

    instances: dict[str, list[dict[str, Any]]] = config.get("instances", [])

    logger.info(f"Loaded {len(instances):,} instances from config.toml")
    logger.trace(f"{config=}")

    for instance in instances.get("x", []):
        XInstance.start(XInstance(), instance, instances["x"].index(instance))

    # Keep parent thread alive so child threads continue to run
    while True:
        sleep(1)


if __name__ == "__main__":
    try:
        start()
    except KeyboardInterrupt:
        pass
