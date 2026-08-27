"""BetterTwitFix data source for X posts (https://github.com/dylanpdx/BetterTwitFix)."""

import re
from dataclasses import replace
from datetime import datetime, timezone
from re import Pattern
from typing import Any, Self
from urllib.parse import urljoin

import niquests
from loguru import logger
from niquests import Response

from .retry import retry_request
from .x import XFeed, XMedia, XPost, XPostReference

POST_URL_PATTERN: Pattern[str] = re.compile(
    r"https?://(?:www\.)?(?:twitter|x)\.com/([^/]+)/status/(\d+)"
)
PLACEHOLDER_USERNAME = "i"
MAX_AGE_PATTERN: Pattern[str] = re.compile(
    r"(?:^|,)\s*max-age=(\d+(?:\.\d+)?)", re.IGNORECASE
)


class BetterTwitFix:
    """Fetch and normalize X posts with BetterTwitFix."""

    api_url: str = "https://api.vxtwitter.com"
    user_agent: str = "https://github.com/EthanC/Bluebird"
    retries: int = 3
    retry_delay: float = 5.0

    def log(self: Self, username: str, post_id: str | None = None) -> str:
        """Craft the head of a source log message."""
        head: str = f"BetterTwitFix[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def fetch_user(self: Self, username: str) -> XFeed | None:
        """Fetch and normalize the latest available posts for an X user."""
        try:
            res: Response = retry_request(
                lambda: niquests.get(
                    f"{self.api_url}/{username}",
                    params={
                        "with_tweets": "true",
                        "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
                    },
                    headers={"User-Agent": self.user_agent},
                    timeout=5,
                    allow_redirects=False,
                    retries=0,
                ),
                self.retries,
                self.retry_delay,
                niquests.RequestException,
            ).raise_for_status()

            logger.debug(f"{self.log(username)} Requested data for user")
            logger.trace(f"{self.log(username)} {res=}")

            data: Any = res.json()

            if not isinstance(data, dict) or not isinstance(
                data.get("latest_tweets"), list
            ):
                raise ValueError(
                    f"Expected latest_tweets, received invalid data {data=}"
                )

            feed_username: str = self._string(data.get("screen_name")) or username
            bio: str | None = self._string(data.get("description"))
            posts: list[XPost] = []
            complete: bool = True

            for post_data in data["latest_tweets"]:
                try:
                    if not isinstance(post_data, dict):
                        raise ValueError(f"Expected post object, received {post_data=}")

                    posts.append(self._normalize_post(post_data, feed_username, bio))
                except (TypeError, ValueError, OverflowError) as e:
                    complete = False
                    logger.opt(exception=e).error(
                        f"{self.log(feed_username)} Skipped invalid post data"
                    )
                    logger.trace(f"{self.log(feed_username)} {post_data=}")

            feed: XFeed = XFeed(
                username=feed_username,
                posts=tuple(posts),
                max_age=self._max_age(res),
                complete=complete,
            )
        except (niquests.RequestException, TypeError, ValueError, OverflowError) as e:
            # HTTP 500 happens often, don't log it as an error.
            if self._is_internal_server_error(e):
                logger.opt(exception=e).debug(
                    f"{self.log(username)} Failed to fetch data for user"
                )
            else:
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
            res: Response = retry_request(
                lambda: niquests.get(
                    f"{self.api_url}/{username}/status/{post_id}",
                    headers={"User-Agent": self.user_agent},
                    timeout=5,
                    allow_redirects=False,
                    retries=0,
                ),
                self.retries,
                self.retry_delay,
                niquests.RequestException,
            ).raise_for_status()

            logger.debug(f"{self.log(username, post_id)} Requested post data")
            logger.trace(f"{self.log(username, post_id)} {res=}")

            data: Any = res.json()

            if not isinstance(data, dict):
                raise ValueError(f"Expected post object, received invalid data {data=}")

            post: XPost = self._normalize_post(data, username)

            if self._is_placeholder_username(post.username):
                resolved_username: str | None = self._redirected_username(
                    post.url, post_id
                )

                if resolved_username:
                    post = replace(
                        post,
                        username=resolved_username,
                        url=f"https://x.com/{resolved_username}/status/{post_id}",
                    )

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

    def _normalize_post(
        self: Self,
        data: dict[str, Any],
        fallback_username: str,
        user_bio: str | None = None,
    ) -> XPost:
        """Translate a BetterTwitFix post object into the shared post model."""
        post_id: str | None = self._string(data.get("tweetID"))
        created_at_raw: Any = data.get("date_epoch")

        if not post_id or created_at_raw is None:
            raise ValueError(
                f"Expected tweetID and date_epoch, received invalid data {data=}"
            )

        created_at: int = int(created_at_raw)
        source_url: str | None = self._string(data.get("tweetURL"))
        source_reference: XPostReference | None = self._post_reference(source_url)
        source_username: str | None = (
            source_reference.username
            if source_reference
            and not self._is_placeholder_username(source_reference.username)
            else None
        )
        api_username: str | None = self._string(
            data.get("user_screen_name")
        ) or self._string(data.get("screen_name"))

        if api_username and self._is_placeholder_username(api_username):
            api_username = None

        username: str = api_username or source_username or fallback_username
        url: str = source_url or f"https://x.com/{username}/status/{post_id}"

        if (
            source_reference
            and self._is_placeholder_username(source_reference.username)
            and not self._is_placeholder_username(username)
        ):
            url = f"https://x.com/{username}/status/{post_id}"
        media: list[XMedia] = []
        media_data: Any = data.get("media_extended")

        if isinstance(media_data, list):
            for item in media_data:
                if not isinstance(item, dict):
                    continue

                media_url: str | None = self._string(item.get("url"))

                if media_url:
                    media.append(
                        XMedia(
                            url=media_url, alt_text=self._string(item.get("altText"))
                        )
                    )

        replying_to: str | None = self._string(data.get("replyingTo"))
        replying_to_id: str | None = self._string(data.get("replyingToID"))
        quote_url: str | None = self._string(data.get("qrtURL"))
        repost_url: str | None = self._string(data.get("retweetURL"))

        return XPost(
            post_id=post_id,
            url=url.replace("twitter.com", "x.com"),
            username=username,
            display_name=self._string(data.get("user_name")) or username,
            created_at=created_at,
            text=self._string(data.get("text")),
            bio=user_bio
            or self._string(data.get("user_bio"))
            or self._string(data.get("user_description")),
            profile_image_url=self._string(data.get("user_profile_image_url")),
            media=tuple(media),
            possibly_sensitive=bool(data.get("possibly_sensitive", False)),
            is_reply=bool(replying_to_id or replying_to),
            is_quote=bool(quote_url or data.get("qrt")),
            is_repost=bool(repost_url or data.get("retweet")),
            reply_to=(
                XPostReference(username=replying_to, post_id=replying_to_id)
                if replying_to and replying_to_id
                else None
            ),
            quote_of=self._embedded_post_reference(data.get("qrt"))
            or self._post_reference(quote_url),
            repost_of=self._embedded_post_reference(data.get("retweet"))
            or self._post_reference(repost_url),
        )

    @staticmethod
    def _string(value: Any) -> str | None:
        """Return a non-empty string value."""
        return value if isinstance(value, str) and value else None

    @classmethod
    def _embedded_post_reference(cls, post: Any) -> XPostReference | None:
        """Extract an X post reference from embedded BetterTwitFix data."""
        if not isinstance(post, dict):
            return None

        source_reference: XPostReference | None = cls._post_reference(
            cls._string(post.get("tweetURL"))
        )
        username: str | None = cls._string(post.get("user_screen_name")) or cls._string(
            post.get("screen_name")
        )

        if username and cls._is_placeholder_username(username):
            username = None

        if (
            not username
            and source_reference
            and not cls._is_placeholder_username(source_reference.username)
        ):
            username = source_reference.username
        post_id: str | None = cls._string(post.get("tweetID"))

        if not username or not post_id:
            return None

        return XPostReference(username=username, post_id=post_id)

    @staticmethod
    def _post_reference(url: str | None) -> XPostReference | None:
        """Extract an X post reference from a BetterTwitFix relationship URL."""
        if not url or not (match := POST_URL_PATTERN.fullmatch(url)):
            return None

        return XPostReference(username=match.group(1), post_id=match.group(2))

    @staticmethod
    def _is_placeholder_username(username: str) -> bool:
        """Return whether X used its internal post-route placeholder as the author."""
        return username.casefold() == PLACEHOLDER_USERNAME

    def _redirected_username(self: Self, post_url: str, post_id: str) -> str | None:
        """Resolve a placeholder post URL to the canonical X username."""
        try:
            res: Response = retry_request(
                lambda: niquests.head(
                    post_url,
                    headers={"User-Agent": self.user_agent},
                    timeout=5,
                    allow_redirects=False,
                    retries=0,
                ),
                self.retries,
                self.retry_delay,
                niquests.RequestException,
            )
        except niquests.RequestException as e:
            logger.opt(exception=e).debug(
                f"{self.log(PLACEHOLDER_USERNAME, post_id)} Failed to resolve placeholder username"
            )

            return None

        location: str | None = res.headers.get("location")
        reference: XPostReference | None = self._post_reference(
            urljoin(post_url, location) if location else None
        )

        if (
            not reference
            or reference.post_id != post_id
            or self._is_placeholder_username(reference.username)
        ):
            logger.debug(
                f"{self.log(PLACEHOLDER_USERNAME, post_id)} X did not return a canonical username"
            )

            return None

        return reference.username

    @staticmethod
    def _max_age(res: Response) -> float | None:
        """Extract the BetterTwitFix cache lifetime from a response."""
        cache_control: str | None = res.headers.get("cache-control")

        if not cache_control or not (match := MAX_AGE_PATTERN.search(cache_control)):
            return None

        return float(match.group(1))

    @staticmethod
    def _is_internal_server_error(error: Exception) -> bool:
        """Return whether an exception represents BetterTwitFix's common HTTP 500."""
        return (
            isinstance(error, niquests.HTTPError)
            and error.response is not None
            and error.response.status_code == 500
        )
