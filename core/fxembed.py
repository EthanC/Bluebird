"""FxEmbed data source for X posts (https://github.com/FxEmbed/FxEmbed)."""

import re
from re import Pattern
from typing import Any, Self

import niquests
from loguru import logger
from niquests import Response

from .x import XFeed, XMedia, XPost, XPostReference

POST_URL_PATTERN: Pattern[str] = re.compile(
    r"https?://(?:www\.)?(?:twitter|x)\.com/([^/]+)/status/(\d+)"
)


class FxEmbed:
    """Fetch and normalize X posts with FxEmbed."""

    api_url: str = "https://api.fxtwitter.com/2"
    user_agent: str = "https://github.com/EthanC/Bluebird"
    retries: int = 3

    def log(self: Self, username: str, post_id: str | None = None) -> str:
        """Craft the head of a source log message."""
        head: str = f"FxEmbed[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def fetch_user(self: Self, username: str) -> XFeed | None:
        """Fetch and normalize the latest available posts for an X user."""
        try:
            res: Response = niquests.get(
                f"{self.api_url}/profile/{username}/statuses",
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=self.retries,
            ).raise_for_status()

            logger.debug(f"{self.log(username)} Requested data for user")
            logger.trace(f"{self.log(username)} {res=}")

            data: Any = res.json()

            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError(f"Expected results, received invalid data {data=}")

            feed_username: str = self._feed_username(data["results"], username)
            posts: list[XPost] = []

            for post_data in data["results"]:
                try:
                    if (
                        not isinstance(post_data, dict)
                        or post_data.get("type") != "status"
                    ):
                        raise ValueError(
                            f"Expected status object, received {post_data=}"
                        )

                    # FxEmbed omits the repost event ID and timestamp, so using its
                    # original-post representation would change the notification.
                    if post_data.get("reposted_by"):
                        logger.debug(
                            f"{self.log(feed_username)} Skipped lossy repost data"
                        )

                        continue

                    posts.append(self._normalize_post(post_data))
                except (TypeError, ValueError, OverflowError) as e:
                    logger.opt(exception=e).error(
                        f"{self.log(feed_username)} Skipped invalid post data"
                    )
                    logger.trace(f"{self.log(feed_username)} {post_data=}")

            feed: XFeed = XFeed(
                username=feed_username, posts=tuple(posts), complete=False
            )
        except (niquests.RequestException, TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username)} Failed to fetch data for user"
            )

            return None

        logger.debug(f"{self.log(username)} Fetched data for user")
        logger.trace(f"{self.log(username)} {feed=}")

        return feed

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Fetch and normalize one X post."""
        try:
            res: Response = niquests.get(
                f"{self.api_url}/status/{post_id}",
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=self.retries,
            ).raise_for_status()

            logger.debug(f"{self.log(username, post_id)} Requested post data")
            logger.trace(f"{self.log(username, post_id)} {res=}")

            data: Any = res.json()
            post_data: Any = data.get("status") if isinstance(data, dict) else None

            if not isinstance(post_data, dict) or post_data.get("type") != "status":
                raise ValueError(
                    f"Expected status object, received invalid data {data=}"
                )

            post: XPost = self._normalize_post(post_data)

            if post.post_id != post_id:
                raise ValueError(
                    f"Expected post {post_id}, received post {post.post_id}"
                )
        except (niquests.RequestException, TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username, post_id)} Failed to fetch post data"
            )

            return None

        logger.debug(f"{self.log(username, post_id)} Fetched post data")
        logger.trace(f"{self.log(username, post_id)} {post=}")

        return post

    def _normalize_post(self: Self, data: dict[str, Any]) -> XPost:
        """Translate an FxEmbed status object into the shared post model."""
        post_id: str | None = self._string(data.get("id"))
        created_at_raw: Any = data.get("created_timestamp")
        author: Any = data.get("author")

        if not post_id or created_at_raw is None or not isinstance(author, dict):
            raise ValueError(
                f"Expected id, created_timestamp, and author, received {data=}"
            )

        username: str | None = self._string(author.get("screen_name"))

        if not username:
            raise ValueError(f"Expected author screen_name, received {author=}")

        media: list[XMedia] = []
        media_data: Any = data.get("media")

        if isinstance(media_data, dict) and isinstance(media_data.get("all"), list):
            for item in media_data["all"]:
                if not isinstance(item, dict):
                    continue

                media_url: str | None = self._string(item.get("url"))

                if media_url:
                    media.append(
                        XMedia(
                            url=self._media_url(media_url),
                            alt_text=self._string(item.get("altText")),
                        )
                    )

        replying_to: Any = data.get("replying_to")
        reply_to: XPostReference | None = None

        if isinstance(replying_to, dict):
            reply_username: str | None = self._string(replying_to.get("screen_name"))
            reply_post_id: str | None = self._string(replying_to.get("status"))

            if reply_username and reply_post_id:
                reply_to = XPostReference(
                    username=reply_username, post_id=reply_post_id
                )

        quote: Any = data.get("quote")

        return XPost(
            post_id=post_id,
            url=self._string(data.get("url"))
            or f"https://x.com/{username}/status/{post_id}",
            username=username,
            display_name=self._string(author.get("name")) or username,
            created_at=int(created_at_raw),
            text=self._string(data.get("text")),
            bio=self._string(author.get("description")),
            profile_image_url=self._profile_image_url(
                self._string(author.get("avatar_url"))
            ),
            media=tuple(media),
            possibly_sensitive=bool(data.get("possibly_sensitive", False)),
            is_reply=replying_to is not None,
            is_quote=quote is not None,
            reply_to=reply_to,
            quote_of=self._quote_reference(quote),
        )

    @classmethod
    def _feed_username(cls, results: list[Any], fallback: str) -> str:
        """Find the canonical casing of the requested profile's username."""
        for result in results:
            if not isinstance(result, dict):
                continue

            for profile_key in ("reposted_by", "author"):
                profile: Any = result.get(profile_key)

                if not isinstance(profile, dict):
                    continue

                username: str | None = cls._string(profile.get("screen_name"))

                if username and username.casefold() == fallback.casefold():
                    return username

        return fallback

    @classmethod
    def _quote_reference(cls, quote: Any) -> XPostReference | None:
        """Extract a referenced quote from an FxEmbed status or tombstone."""
        if not isinstance(quote, dict):
            return None

        post_id: str | None = cls._string(quote.get("id"))
        author: Any = quote.get("author")

        if post_id and isinstance(author, dict):
            username: str | None = cls._string(author.get("screen_name"))

            if username:
                return XPostReference(username=username, post_id=post_id)

        url: str | None = cls._string(quote.get("url"))

        if not url or not (match := POST_URL_PATTERN.fullmatch(url)):
            return None

        return XPostReference(username=match.group(1), post_id=match.group(2))

    @staticmethod
    def _media_url(url: str) -> str:
        """Match BetterTwitFix's attached-media URL representation."""
        return url.partition("?")[0]

    @staticmethod
    def _profile_image_url(url: str | None) -> str | None:
        """Match BetterTwitFix's profile-image URL representation."""
        return url.replace("_200x200", "_normal") if url else None

    @staticmethod
    def _string(value: Any) -> str | None:
        """Return a non-empty string value."""
        return value if isinstance(value, str) and value else None
