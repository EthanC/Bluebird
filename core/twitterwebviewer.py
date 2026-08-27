"""TwitterWebViewer data source for X posts (https://twitterwebviewer.com)."""

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from threading import Lock
from typing import Any, Self

from curl_cffi import requests
from curl_cffi.requests import RequestsError, Response
from loguru import logger

from .retry import retry_request
from .service import ServiceCircuitBreaker, ServiceFailure, ServiceNotFound
from .x import XFeed, XMedia, XPost, XPostReference


class TwitterWebViewer:
    """Fetch and normalize X posts with TwitterWebViewer."""

    api_url: str = "https://api.twitterwebviewer.com/api"
    impersonate: str = "chrome"
    retries: int = 3
    retry_delay: float = 5.0
    hydration_workers: int = 4
    twitter_epoch_ms: int = 1_288_834_974_657
    post_cache_size: int = 1_000

    def __init__(self: Self, circuit_breaker: ServiceCircuitBreaker) -> None:
        """Initialize shared service health and the hydrated-post cache."""
        self.circuit_breaker: ServiceCircuitBreaker = circuit_breaker
        self.post_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.post_cache_lock: Lock = Lock()

    def log(self: Self, username: str, post_id: str | None = None) -> str:
        """Craft the head of a source log message."""
        head: str = f"TwitterWebViewer[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def fetch_user(self: Self, username: str) -> XFeed | None:
        """Fetch and normalize the latest available posts for an X user."""
        profile: dict[str, Any] | None = self._fetch_data(f"user/{username}", username)
        timeline: dict[str, Any] | None = self._fetch_data(
            f"tweets/{username}", username
        )

        if profile is None or timeline is None:
            return None

        try:
            feed_username: str | None = self._string(profile.get("username"))
            tweets: Any = timeline.get("tweets")
            timeline_profile: Any = timeline.get("user")

            if not feed_username or feed_username.casefold() != username.casefold():
                raise ValueError(
                    f"Expected profile for {username}, received invalid data {profile=}"
                )
            if not isinstance(tweets, list):
                raise ValueError(f"Expected tweets, received invalid data {timeline=}")
            if (
                not isinstance(timeline_profile, dict)
                or (self._string(timeline_profile.get("username")) or "").casefold()
                != feed_username.casefold()
            ):
                raise ValueError(
                    f"Expected timeline for {feed_username}, received invalid data {timeline=}"
                )

            posts: list[XPost] = []
            hydration: list[tuple[dict[str, Any], Future[dict[str, Any] | None]]] = []

            with ThreadPoolExecutor(max_workers=self.hydration_workers) as executor:
                for post_data in tweets:
                    if not isinstance(post_data, dict) or not (
                        post_id := self._string(post_data.get("id"))
                    ):
                        logger.error(
                            f"{self.log(feed_username)} Skipped invalid post data"
                        )
                        logger.trace(f"{self.log(feed_username)} {post_data=}")

                        continue

                    hydration.append(
                        (
                            post_data,
                            executor.submit(
                                self._fetch_post_data, feed_username, post_id
                            ),
                        )
                    )

                for post_data, future in hydration:
                    post_id: str = self._string(post_data.get("id")) or ""
                    detail: dict[str, Any] | None = future.result()

                    if detail is None:
                        logger.error(
                            f"{self.log(feed_username, post_id)} Skipped unhydrated post data"
                        )

                        continue

                    try:
                        posts.append(self._normalize_post(post_data, detail, profile))
                        self._cache_post_data(post_id, detail)
                    except (TypeError, ValueError, OverflowError) as e:
                        logger.opt(exception=e).error(
                            f"{self.log(feed_username, post_id)} Skipped invalid post data"
                        )
                        logger.trace(
                            f"{self.log(feed_username, post_id)} {post_data=} {detail=}"
                        )

            feed: XFeed = XFeed(
                username=feed_username, posts=tuple(posts), complete=False
            )
        except (TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username)} Failed to process data for user"
            )

            return None

        logger.debug(f"{self.log(feed_username)} Fetched data for user")
        logger.trace(f"{self.log(feed_username)} {feed=}")

        return feed

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Fetch and normalize one X post."""
        detail: dict[str, Any] | None = self._fetch_data(
            f"tweet/{post_id}", username, post_id
        )

        if detail is None:
            return None

        try:
            post: XPost = self._normalize_post(detail, detail, detail_only=True)

            if post.post_id != post_id:
                raise ValueError(
                    f"Expected post {post_id}, received post {post.post_id}"
                )
        except (TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username, post_id)} Failed to process post data"
            )

            return None

        logger.debug(f"{self.log(username, post_id)} Fetched post data")
        logger.trace(f"{self.log(username, post_id)} {post=}")

        return post

    def _fetch_data(
        self: Self, path: str, username: str, post_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch and validate one TwitterWebViewer response envelope."""
        return self.circuit_breaker.call(
            lambda: self._request_data(path, username, post_id)
        )

    def _request_data(
        self: Self, path: str, username: str, post_id: str | None = None
    ) -> dict[str, Any]:
        """Request one response while admitted by the circuit breaker."""
        try:
            res: Response = retry_request(
                lambda: requests.get(
                    f"{self.api_url}/{path}",
                    timeout=5,
                    allow_redirects=False,
                    impersonate=self.impersonate,
                ),
                self.retries,
                self.retry_delay,
                RequestsError,
            )
            res.raise_for_status()

            logger.debug(f"{self.log(username, post_id)} Requested data")
            logger.trace(f"{self.log(username, post_id)} {res=}")

            response_data: Any = res.json()
            data: Any = (
                response_data.get("data") if isinstance(response_data, dict) else None
            )

            if (
                not isinstance(response_data, dict)
                or response_data.get("success") is not True
                or not isinstance(data, dict)
            ):
                raise ValueError(
                    f"Expected successful data response, received {response_data=}"
                )
        except (RequestsError, TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username, post_id)} Failed to fetch data"
            )

            if self._is_not_found(e):
                raise ServiceNotFound from e

            raise ServiceFailure from e

        return data

    def _fetch_post_data(
        self: Self, username: str, post_id: str
    ) -> dict[str, Any] | None:
        """Fetch post details, reusing immutable relationship data by post ID."""
        with self.post_cache_lock:
            if cached := self.post_cache.get(post_id):
                self.post_cache.move_to_end(post_id)

                return cached

        return self._fetch_data(f"tweet/{post_id}", username, post_id)

    def _cache_post_data(self: Self, post_id: str, detail: dict[str, Any]) -> None:
        """Cache immutable relationship fields after successful normalization."""
        relationship: dict[str, Any] = {"id": post_id}

        for key in ("inReplyToId", "inReplyToUsername"):
            if key in detail:
                relationship[key] = detail[key]

        with self.post_cache_lock:
            self.post_cache[post_id] = relationship
            self.post_cache.move_to_end(post_id)

            while len(self.post_cache) > self.post_cache_size:
                self.post_cache.popitem(last=False)

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        """Return whether TwitterWebViewer confirmed a resource is missing."""
        return (
            isinstance(error, RequestsError)
            and error.response is not None
            and error.response.status_code == 404
        )

    def _normalize_post(
        self: Self,
        timeline: dict[str, Any],
        detail: dict[str, Any],
        profile: dict[str, Any] | None = None,
        *,
        detail_only: bool = False,
    ) -> XPost:
        """Translate hydrated TwitterWebViewer data into the shared post model."""
        post_id: str | None = self._string(timeline.get("id"))
        detail_id: str | None = self._string(detail.get("id"))

        if not post_id or post_id != detail_id:
            raise ValueError(
                f"Expected matching timeline and detail IDs, received {post_id=} {detail_id=}"
            )

        is_repost: bool = bool(timeline.get("isRetweet") or detail.get("isRetweet"))
        author: Any = detail.get("author")

        if not isinstance(author, dict):
            author = timeline.get("author")
        if not isinstance(author, dict):
            raise ValueError(f"Expected post author, received {timeline=} {detail=}")

        if profile is not None:
            actor: Any = profile
        elif is_repost:
            actor = detail.get("retweetedBy") or timeline.get("retweetedBy")
        else:
            actor = author

        if not isinstance(actor, dict):
            raise ValueError(f"Expected post actor, received {timeline=} {detail=}")

        username: str | None = self._string(actor.get("username"))

        if not username:
            raise ValueError(f"Expected actor username, received {actor=}")

        created_at_raw: Any = timeline.get("timelineAt") or timeline.get("createdAt")

        if created_at_raw is None:
            raise ValueError(f"Expected post timestamp, received {timeline=}")

        media: list[XMedia] = []
        media_data: Any = detail.get("media")

        if not isinstance(media_data, list):
            media_data = timeline.get("media")
        if isinstance(media_data, list):
            for item in media_data:
                if not isinstance(item, dict):
                    continue

                media_url: str | None = self._string(
                    item.get("videoUrl")
                ) or self._string(item.get("url"))

                if media_url:
                    media.append(
                        XMedia(
                            url=media_url.partition("?")[0],
                            alt_text=self._text(item.get("altText")),
                        )
                    )

        reply_post_id: str | None = (
            None if is_repost else self._string(detail.get("inReplyToId"))
        )
        reply_username: str | None = (
            None if is_repost else self._string(detail.get("inReplyToUsername"))
        )
        quote_data: Any = detail.get("quotedTweet") or timeline.get("quotedTweet")
        quote_of: XPostReference | None = self._post_reference(quote_data)
        repost_of: XPostReference | None = None

        if is_repost:
            original_post_id: str | None = self._string(
                timeline.get("originalTweetId")
            ) or self._string(detail.get("originalTweetId"))
            original_username: str | None = self._string(author.get("username"))

            if not original_post_id or not original_username:
                raise ValueError(
                    f"Expected original repost identity, received {timeline=} {detail=}"
                )

            repost_of = XPostReference(
                username=original_username, post_id=original_post_id
            )

        created_at: int = (
            self._snowflake_timestamp(post_id)
            if detail_only and is_repost
            else self._timestamp(created_at_raw)
        )

        return XPost(
            post_id=post_id,
            url=f"https://x.com/{username}/status/{post_id}",
            username=username,
            display_name=self._text(actor.get("displayName")) or username,
            created_at=created_at,
            text=self._text(detail.get("content"))
            or self._text(timeline.get("content")),
            bio=self._text(profile.get("bio")) if profile else None,
            profile_image_url=self._profile_image_url(
                self._string(actor.get("avatar"))
            ),
            media=tuple(media),
            possibly_sensitive=bool(
                detail.get("possiblySensitive")
                or timeline.get("possiblySensitive")
                or (profile or {}).get("possiblySensitive")
            ),
            is_reply=bool(reply_post_id or reply_username),
            is_quote=quote_data is not None,
            is_repost=is_repost,
            reply_to=(
                XPostReference(username=reply_username, post_id=reply_post_id)
                if reply_username and reply_post_id
                else None
            ),
            quote_of=quote_of,
            repost_of=repost_of,
        )

    @classmethod
    def _post_reference(cls, post: Any) -> XPostReference | None:
        """Extract a post reference from embedded TwitterWebViewer data."""
        if not isinstance(post, dict):
            return None

        post_id: str | None = cls._string(post.get("id"))
        author: Any = post.get("author")
        username: str | None = (
            cls._string(author.get("username")) if isinstance(author, dict) else None
        )

        if not post_id or not username:
            return None

        return XPostReference(username=username, post_id=post_id)

    @staticmethod
    def _timestamp(value: Any) -> int:
        """Parse TwitterWebViewer's ISO or X-formatted timestamp."""
        if isinstance(value, bool):
            raise ValueError("Invalid post timestamp")
        if isinstance(value, (int, float)):
            return int(value)
        if not isinstance(value, str) or not value:
            raise ValueError("Invalid post timestamp")

        try:
            parsed: datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            raise ValueError("Post timestamp has no timezone")

        return int(parsed.timestamp())

    @classmethod
    def _snowflake_timestamp(cls, post_id: str) -> int:
        """Derive an event timestamp from an X snowflake ID."""
        return ((int(post_id) >> 22) + cls.twitter_epoch_ms) // 1_000

    @staticmethod
    def _profile_image_url(url: str | None) -> str | None:
        """Match the profile-image representation used by other sources."""
        return url.replace("_400x400", "_normal") if url else None

    @staticmethod
    def _string(value: Any) -> str | None:
        """Return a non-empty string value."""
        return value if isinstance(value, str) and value else None

    @classmethod
    def _text(cls, value: Any) -> str | None:
        """Return decoded, non-empty text."""
        text: str | None = cls._string(value)

        return unescape(text) if text else None
