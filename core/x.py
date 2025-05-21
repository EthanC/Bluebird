import random
import re
from datetime import datetime
from operator import itemgetter
from os import environ
from re import Pattern
from threading import Thread
from time import sleep
from typing import Any, Self

import httpx
from clyde import Webhook
from clyde.components import (
    ActionRow,
    Container,
    LinkButton,
    MediaGallery,
    MediaGalleryItem,
    Section,
    Seperator,
    SeperatorSpacing,
    TextDisplay,
    Thumbnail,
    UnfurledMediaItem,
)
from clyde.markdown import Markdown
from clyde.timestamp import Timestamp
from environs import env
from httpx import Response
from loguru import logger

from .format import Format

pattern_post_url: Pattern[str] = re.compile(
    r"https://twitter\.com/([^/]+)/status/(\d+)"
)


class XInstance:
    """Class representing an X instance configuration."""

    base_url: str = "https://x.com/"
    index: int
    state: dict[str, int] = {}
    usernames: list[str]
    webhook_url: str | None
    require_media: bool | None
    require_keyword: list[str] | None
    exclude_reply: bool | None
    exclude_repost: bool | None
    exclude_keyword: list[str] | None

    def log(self: Self, username: str | None = None, post_id: str | None = None) -> str:
        """Craft the head of a log message given an instance and username."""
        head: str = "X"

        head += f"[{self.index:,}]"

        if username:
            head += f"[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def start(self: Self, config: dict[str, Any], index: int) -> None:
        """Create threads for the usernames within the X instance."""
        self.index = index
        self.usernames = config.get("usernames", [])
        self.webhook_url = config.get("discord_webhook_url")
        self.require_media = config.get("require_media")
        self.require_keyword = config.get("require_keyword")
        self.exclude_reply = config.get("exclude_reply")
        self.exclude_repost = config.get("exclude_repost")
        self.exclude_keyword = config.get("exclude_keyword")

        logger.info(f"{self.log()} Loaded instance configuration")
        logger.trace(f"{self.log()} {self=}")

        for username in self.usernames:
            if environ.get("DEBUG_STATE"):
                self.state[username] = env.int("DEBUG_STATE")

            Thread(target=self.watch_user, args=[username], daemon=True).start()

            sleep(random.uniform(1.0, 3.0))

    def watch_user(self: Self, username: str) -> None:
        """
        Run a continuous loop that processes user data and triggers notifications upon
        the discovery of new posts for the provided X username.
        """
        logger.info(f"{self.log(username)} Started watching for new posts")

        max_age: float = 60.0

        while True:
            logger.debug(f"{self.log(username)} Checking for new posts...")

            data: dict[str, Any] | None = self.fetch_user(username)

            if not data or not data.get("latest_tweets"):
                logger.debug(
                    f"{self.log(username)} Invalid data, retry in {max_age:,}s..."
                )
                logger.trace(f"{self.log(username)} {data=}")

                sleep(max_age)

                continue

            # Use proper username if available
            username = data.get("screen_name", username)

            if data.get("max_age"):
                max_age = data["max_age"]

            posts: list[dict[str, Any]] = data["latest_tweets"]

            if not self.state.get(username):
                self.state[username] = posts[-1]["date_epoch"]

                logger.info(
                    f"{self.log(username)} Set initial state ({self.state[username]}), sleeping for {max_age:,}s..."
                )
                logger.trace(f"{self.log(username)} {self.state=}")

                sleep(max_age)

                continue

            for post in posts:
                post_id: str | None = post.get("tweetID")
                post_epoch: int | None = post.get("date_epoch")

                if not post_epoch:
                    logger.debug(
                        f"{self.log(username, post_id)} Skipped post, invalid data"
                    )
                    logger.trace(f"{self.log(username, post_id)} {post=}")

                    continue

                if post_epoch <= self.state[username]:
                    logger.debug(
                        f"{self.log(username, post_id)} Skipped post, older than desired"
                    )
                    logger.trace(f"{self.log(username, post_id)} {post=}")

                    continue

                if self.require_keyword:
                    keyword_found: str | None = None
                    post_text: str | None = post.get("text")

                    if not post_text:
                        logger.debug(
                            f"{self.log(username, post_id)} Skipped post, keyword requirement not met"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                    for keyword in self.require_keyword:
                        if keyword.lower() in post_text.lower():
                            keyword_found = keyword

                            break

                    if not keyword_found:
                        logger.debug(
                            f"{self.log(username, post_id)} Skipped post, keyword requirement not met"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                if self.require_media:
                    if len(post.get("media_extended", [])) == 0:
                        logger.debug(
                            f"{self.log(username, post_id)} Skipped post, media requirement not met"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                if self.exclude_reply:
                    if post.get("is_reply"):
                        logger.debug(
                            f"{self.log(username), post_id} Skipped post, replies excluded"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                if self.exclude_repost:
                    if post.get("is_repost"):
                        logger.debug(
                            f"{self.log(username, post_id)} Skipped post, reposts excluded"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                if self.exclude_keyword:
                    keyword_found: str | None = None
                    post_text: str | None = post.get("text")

                    if post_text:
                        for keyword in self.exclude_keyword:
                            if keyword.lower() in post_text.lower():
                                keyword_found = keyword

                                break

                    if keyword_found:
                        logger.debug(
                            f"{self.log(username, post_id)} Skipped post, keyword {keyword_found} excluded"
                        )
                        logger.trace(f"{self.log(username, post_id)} {post=}")

                        continue

                # Avoid unnecessary redirects
                if post_url := post.get("tweetURL"):
                    post_url = post_url.replace("twitter.com", "x.com")
                    post["tweetURL"] = post_url

                logger.success(
                    f"{self.log(username, post_id)} Discovered new post {post_url}"
                )

                if not self.webhook_url:
                    logger.debug(
                        f"{self.log(username, post_id)} Skipped notification, Webhook not configured"
                    )
                    logger.trace(f"{self.log(username, post_id)} {self=}")

                    continue

                self.notify(username, post_id, post)

            if self.state[username] != posts[-1]["date_epoch"]:
                self.state[username] = posts[-1]["date_epoch"]

                logger.info(
                    f"{self.log(username)} Set latest state ({self.state[username]})"
                )
                logger.trace(f"{self.log(username)} {self.state=}")

            logger.info(
                f"{self.log(username)} {len(posts):,} posts processed, sleeping for {max_age:,}s..."
            )

            sleep(max_age)

    def fetch_user(self: Self, username: str) -> dict[str, Any] | None:
        """Fetch the latest available data for the provided X username."""
        data: dict[str, Any] | None = None
        res: None | Response = None

        try:
            res = httpx.get(
                f"https://api.vxtwitter.com/{username}",
                params={"with_tweets": True},
                headers={"User-Agent": "https://github.com/EthanC/Bluebird"},
            ).raise_for_status()

            logger.debug(f"{self.log(username)} Requested data for user")
            logger.trace(f"{self.log(username)} {res=}")

            data = res.json()

            if not data or not data.get("latest_tweets"):
                raise ValueError("Invalid data received")

            # Sort posts chronologically
            data["latest_tweets"] = sorted(
                data["latest_tweets"], key=itemgetter("date_epoch")
            )

            # Add miscellaneous data to each post object
            for post in data["latest_tweets"]:
                post["user_bio"] = data.get("description")
                post["is_repost"] = bool(post.get("retweetURL") or post.get("retweet"))
                post["is_quote"] = bool(post.get("qrtURL"))
                post["is_reply"] = bool(
                    post.get("replyingToID") or data.get("replyingTo")
                )

            # Set max_age based on response headers
            if cache_control := res.headers.get("cache-control"):
                data["max_age"] = float(cache_control.split("max-age=")[1])
        except Exception as e:
            # HTTP 500 happens often, don't log as error
            if "500 Internal Server Error" in str(e):
                logger.opt(exception=e).debug(
                    f"{self.log(username)} Failed to fetch data for user"
                )

                return data

            logger.opt(exception=e).error(
                f"{self.log(username)} Failed to fetch data for user"
            )

            return data

        logger.debug(f"{self.log(username)} Fetched data for user")
        logger.trace(f"{self.log(username)} {data=}")

        return data

    def fetch_post(self: Self, username: str, post_id: str) -> dict[str, Any]:
        """Fetch the post data for the provided username and post ID combination."""
        data: dict[str, Any] = {}

        try:
            res: Response = httpx.get(
                f"https://api.vxtwitter.com/{username}/status/{post_id}",
                headers={"User-Agent": "https://github.com/EthanC/Bluebird"},
            ).raise_for_status()

            logger.debug(f"{self.log(username, post_id)} Requested post data")
            logger.trace(f"{self.log(username, post_id)} {res=}")

            data = res.json()

            data["is_reply"] = bool(data.get("replyingToID") or data.get("replyingTo"))
            data["is_repost"] = bool(data.get("retweetURL") or data.get("retweet"))
            data["is_quote"] = bool(data.get("qrtURL"))
        except Exception as e:
            logger.opt(exception=e).error(
                f"{self.log(username, post_id)} Failed to fetch post data"
            )

            return data

        logger.debug(f"{self.log(username, post_id)} Fetched post data")
        logger.trace(f"{self.log(username, post_id)} {data=}")

        return data

    def notify(
        self: Self, username: str, post_id: str | None, post: dict[str, Any]
    ) -> None:
        """Send a Discord Webhook notification for the provided X post."""
        webhook: Webhook = Webhook(url=self.webhook_url)

        if post.get("is_reply") and post.get("replyingTo") and post.get("replyingToID"):
            reply_parent: dict[str, Any] = self.fetch_post(
                post["replyingTo"], post["replyingToID"]
            )

            webhook.add_component(
                self.build_post(username, post_id, reply_parent, True)
            )

        webhook.add_component(self.build_post(username, post_id, post))

        if post.get("is_quote") and post.get("qrtURL"):
            if re_match := re.match(pattern_post_url, post["qrtURL"]):
                quote_username: str = re_match.group(1)
                quote_post: dict[str, Any] = self.fetch_post(
                    quote_username, re_match.group(2)
                )

                webhook.add_component(
                    self.build_post(quote_username, post_id, quote_post, True)
                )
            else:
                logger.warning(
                    f"{self.log(username, post_id)} Failed to process Quote Post {post['qrtURL']}"
                )

        if post.get("is_repost") and post.get("retweetURL"):
            if re_match := re.match(pattern_post_url, post["retweetURL"]):
                repost_username: str = re_match.group(1)
                repost: dict[str, Any] = self.fetch_post(
                    repost_username, re_match.group(2)
                )

                webhook.add_component(
                    self.build_post(repost_username, post_id, repost, True)
                )
            else:
                logger.warning(
                    f"{self.log(username, post_id)} Failed to process Repost {post['retweetURL']}"
                )

        webhook.add_component(
            self.build_post_outbound(username, post_id, post["tweetURL"])
        )

        logger.debug(f"{self.log(username, post_id)} Built Webhook for post")
        logger.trace(f"{self.log(username, post_id)} {webhook=}")

        webhook.execute()

    def build_post(
        self: Self,
        username: str,
        post_id: str | None,
        post: dict[str, Any],
        mini: bool = False,
    ) -> Container:
        """Build a Discord Container Component for the provided X post."""
        container: Container = Container(accent_color="#000000")

        head: TextDisplay | Section = self.build_post_head(
            username, post_id, post, mini
        )
        body: TextDisplay | None = self.build_post_body(username, post_id, post)
        media: MediaGallery | None = self.build_post_media(username, post_id, post)
        footer: TextDisplay = self.build_post_footer(username, post_id, post)

        container.add_component(head)

        if body and not post.get("is_repost"):
            container.add_component(body)

        if media and not post.get("is_repost"):
            container.add_component(media)

        container.add_component(Seperator(divider=True, spacing=SeperatorSpacing.SMALL))
        container.add_component(footer)

        logger.debug(f"{self.log(username, post_id)} Built Container for post")
        logger.trace(f"{self.log(username, post_id)} {container=}")

        return container

    def build_post_head(
        self: Self,
        username: str,
        post_id: str | None,
        post: dict[str, Any],
        mini: bool = False,
    ) -> TextDisplay | Section:
        """Build a Discord Text Display or Section Component for the provided X post."""
        name_username: str = Markdown.masked_link(
            f"@{username}", f"{self.base_url}{username}"
        )
        name_display: str = post.get("user_name", username)

        if mini:
            return TextDisplay(
                content=Markdown.bold(f"{name_display} ({name_username})")
            )

        bio: str | None = post.get("user_bio")
        avatar: str | None = post.get("user_profile_image_url")

        head: Section = Section()

        head.add_component(
            component=TextDisplay(
                content=Markdown.header_1(f"{name_display} ({name_username})")
            )
        )

        if bio:
            bio = Format.replace_mentions(bio, self.base_url)
            bio = Format.replace_hashtags(bio, f"{self.base_url}hashtag/")
            bio = Format.replace_cashtags(bio, f"{self.base_url}search?q=%24/")

            # Bio may have become None after formatting
            if bio:
                head.add_component(TextDisplay(content=Markdown.subtext(bio)))

        if avatar:
            # Use full-size avatar
            avatar = avatar.replace("_normal", "")
            head.set_accessory(accessory=Thumbnail(media=UnfurledMediaItem(url=avatar)))

        logger.debug(f"{self.log(username, post_id)} Built head for post")
        logger.trace(f"{self.log(username, post_id)} {head=}")

        return head

    def build_post_body(
        self: Self, username: str, post_id: str | None, post: dict[str, Any]
    ) -> TextDisplay | None:
        """Build a Discord Text Display Component for the provided X post."""
        text: str | None = post.get("text")

        if not text:
            return

        text = Format.replace_mentions(text, self.base_url)
        text = Format.replace_hashtags(text, f"{self.base_url}hashtag/")
        text = Format.replace_cashtags(text, f"{self.base_url}search?q=%24/")

        # Text may have become None after formatting
        if not text:
            return

        body: TextDisplay = TextDisplay(content=Markdown.block_quote(text))

        logger.debug(f"{self.log(username, post_id)} Built body for post")
        logger.trace(f"{self.log(username, post_id)} {body=}")

        return body

    def build_post_media(
        self: Self, username: str, post_id: str | None, post: dict[str, Any]
    ) -> MediaGallery | None:
        """Build a Discord Media Gallery Component for the provided X post."""
        media_raw: list[dict[str, Any]] | None = post.get("media_extended")

        if not media_raw or len(media_raw) == 0:
            return

        media: MediaGallery = MediaGallery()

        for item_raw in media_raw:
            item: MediaGalleryItem = MediaGalleryItem(
                media=UnfurledMediaItem(url=item_raw.get("url"))
            )

            if alt_text := item_raw.get("altText"):
                item.set_description(alt_text)

            if post.get("possibly_sensitive", False):
                item.set_spoiler(True)

            media.add_item(item)

        logger.debug(f"{self.log(username, post_id)} Built media for post")
        logger.trace(f"{self.log(username, post_id)} {media=}")

        return media

    def build_post_footer(
        self: Self, username: str, post_id: str | None, post: dict[str, Any]
    ) -> TextDisplay:
        """Build a Discord Seperator and Text Display Component for the provided X post."""

        posted: int | datetime = post.get("date_epoch", datetime.now())
        ts_long: str = Timestamp.long_date_time(posted)
        ts_relative: str = Timestamp.relative_time(posted)

        action: str = "Posted"

        if post.get("is_repost", False):
            action = "Reposted"
        elif post.get("is_quote", False):
            action = "Quoted"
        elif post.get("is_reply", False):
            action = "Replied"

        footer: TextDisplay = TextDisplay(
            content=Markdown.subtext(f"{action} {ts_long} ({ts_relative})")
        )

        logger.debug(f"{self.log(username, post_id)} Built footer for post")
        logger.trace(f"{self.log(username, post_id)} {footer=}")

        return footer

    def build_post_outbound(
        self: Self, username: str, post_id: str | None, post_url: str
    ) -> ActionRow:
        """Build a Discord Action Row Component for the provided X post."""

        outbound: ActionRow = ActionRow()

        outbound.add_component(LinkButton(label="View on X", url=post_url))
        outbound.add_component(
            LinkButton(
                label="Powered by Bluebird", url="https://github.com/EthanC/Bluebird"
            )
        )

        logger.debug(f"{self.log(username, post_id)} Built outbound links for post")
        logger.trace(f"{self.log(username, post_id)} {outbound=}")

        return outbound
