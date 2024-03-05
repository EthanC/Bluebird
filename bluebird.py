import logging
from os import environ
from random import randint
from sys import exit, stdout
from threading import Thread
from time import sleep
from typing import Self

import dotenv
from discord_webhook import DiscordEmbed, DiscordWebhook
from loguru import logger
from loguru_discord import DiscordSink

from handlers import Intercept
from services import X


class Bluebird:
    """
    Monitor users on X and report new posts via Discord.

    https://github.com/EthanC/Bluebird
    """

    def Initialize(self: Self) -> None:
        """Initialize Bluebird and begin functionality."""

        logger.info("Bluebird")
        logger.info("https://github.com/EthanC/Bluebird")

        if dotenv.load_dotenv():
            logger.success("Loaded environment variables")

        self.state: dict[str, int] = {}

        # Reroute standard logging to Loguru
        logging.basicConfig(handlers=[Intercept()], level=0, force=True)

        if level := environ.get("LOG_LEVEL"):
            logger.remove()
            logger.add(stdout, level=level)

            logger.success(f"Set console logging level to {level}")

        if url := environ.get("LOG_DISCORD_WEBHOOK_URL"):
            logger.add(
                DiscordSink(url),
                level=environ.get("LOG_DISCORD_WEBHOOK_LEVEL"),
                backtrace=False,
            )

            logger.success("Enabled logging to Discord webhook")
            logger.trace(url)

        usersAll: list[str] = []
        usersTop: list[str] = []
        usersMedia: list[str] = []

        if value := environ.get("USERS_ALL"):
            usersAll = value.split(",")

        if value := environ.get("USERS_TOP"):
            usersTop = value.split(",")

        if value := environ.get("USERS_MEDIA"):
            usersMedia = value.split(",")

        for username in usersAll:
            watcher: Thread = Thread(target=Bluebird.WatchPosts, args=(self, username))

            watcher.daemon = True
            watcher.start()

        for username in usersTop:
            watcher: Thread = Thread(
                target=Bluebird.WatchPosts,
                args=(self, username),
                kwargs={"replies": False},
            )

            watcher.daemon = True
            watcher.start()

        for username in usersMedia:
            watcher: Thread = Thread(
                target=Bluebird.WatchPosts,
                args=(self, username),
                kwargs={"media": True},
            )

            watcher.daemon = True
            watcher.start()

        # Keep the parent thread alive while the child threads run.
        while True:
            sleep(1)

    def WatchPosts(
        self: Self,
        username: str,
        replies: bool = True,
        reposts: bool = True,
        media: bool = False,
    ) -> None:
        """
        Begin a loop for a single user that fires a notification upon
        new post detection.
        """

        while True:
            # Randomize cooldown to mimic natural behavior.
            cooldownMin: int = int(environ.get("COOLDOWN_MIN_TIME", 60))
            cooldownMax: int = int(environ.get("COOLDOWN_MAX_TIME", 300))

            cooldown: int = randint(cooldownMin, cooldownMax)

            logger.info(
                f"[@{username}] Waiting {cooldown:,}s before checking for new posts"
            )

            sleep(cooldown)

            posts: list[dict[str, int | str]] = X.GetUserPosts(
                username,
                includeReplies=replies,
                includeReposts=reposts,
                onlyMedia=media,
            )

            # We didn't get any posts. Sleep then try again.
            if len(posts) <= 0:
                continue

            # We don't have the latest post timestamp saved. This is
            # likely a fresh run of the script. Set the latest post
            # timestamp, then restart the loop.
            if not self.state.get(username):
                self.state[username] = posts[-1]["timestamp"]

                logger.info(
                    f"[@{username}] Watching for new posts after {posts[-1]["postId"]} ({posts[-1]["timestamp"]})"
                )
                logger.debug(f"https://x.com/{username}/status/{posts[-1]["postId"]}")
                logger.debug(self.state)

                continue

            for post in posts:
                if self.state[username] >= post["timestamp"]:
                    logger.debug(
                        f"[@{username}] Skipped post {post["postId"]} due to timestamp ({self.state[username]} >= {post["timestamp"]})"
                    )
                    logger.debug(f"https://x.com/{username}/status/{post["postId"]}")

                    continue

                logger.success(
                    f"[@{username}] Detected new post {post["postId"]} ({post["timestamp"]})"
                )
                logger.debug(f"https://x.com/{username}/status/{post["postId"]}")

                details: dict = X.GetPost(username, post["postId"])

                if details:
                    Bluebird.NotifyPost(username, details)

            self.state[username] = posts[-1]["timestamp"]

            logger.info(
                f"[@{username}] Watching for new posts after {posts[-1]["postId"]} ({posts[-1]["timestamp"]})"
            )
            logger.debug(f"https://x.com/{username}/status/{posts[-1]["postId"]}")
            logger.debug(self.state)

    def NotifyPost(username: str, post: dict) -> None:
        """Send a Discord Embed object for the specified X post."""

        if not (webhook := environ.get("DISCORD_WEBHOOK_URL")):
            return

        embeds: list[DiscordEmbed] = []

        primary: DiscordEmbed = DiscordEmbed()
        extras: list[DiscordEmbed] = []

        postUrl: str = (
            f"https://x.com/{post["author"]["screen_name"]}/status/{post["id"]}"
        )

        primary.set_color("1D9BF0")
        primary.set_author(
            f"{post["author"]["name"]} (@{post["author"]["screen_name"]})",
            url=f"https://x.com/{post["author"]["screen_name"]}",
            icon_url=post["author"]["avatar_url"],
        )
        primary.set_title("Post on X")
        primary.set_url(postUrl)
        primary.set_footer(post["source"], icon_url="https://i.imgur.com/hZbC8my.png")
        primary.set_timestamp(post["created_timestamp"])

        if (post["text"]) and (len(post["text"]) > 0):
            primary.set_description(f">>> {post["text"]}")

        if media := post.get("media"):
            idx: int = 0
            assets: list[dict] = media.get("all", [])

            # External media is not included in the all array.
            if media.get("external"):
                assets.append(media["external"])

            for asset in assets:
                extra: DiscordEmbed = DiscordEmbed()

                extra.set_url(postUrl)

                match asset["type"]:
                    case "photo":
                        if idx == 0:
                            primary.set_image(asset["url"])
                        else:
                            extra.set_image(asset["url"])
                    case "gif":
                        if idx == 0:
                            primary.set_image(asset["thumbnail_url"])
                        else:
                            extra.set_image(asset["thumbnail_url"])
                    case "video":
                        if idx == 0:
                            primary.set_image(asset["thumbnail_url"])
                        else:
                            extra.set_image(asset["thumbnail_url"])
                    case _:
                        logger.warning(
                            f"[@{username}] Unknown media asset type {asset["type"]} for post {post["id"]}"
                        )
                        logger.debug(postUrl)

                if idx > 0:
                    extras.append(extra)

                idx += 1

        embeds.append(primary)
        embeds.extend(extras)

        DiscordWebhook(url=webhook, embeds=embeds, rate_limit_retry=True).execute()


if __name__ == "__main__":
    try:
        Bluebird.Initialize(Bluebird)
    except KeyboardInterrupt:
        exit()
