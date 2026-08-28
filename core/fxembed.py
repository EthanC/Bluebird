"""FxEmbed data source for X posts (https://github.com/FxEmbed/FxEmbed)."""

import re
from re import Pattern
from typing import Any, Self

import niquests
from loguru import logger
from niquests import Response

from .retry import retry_request, retry_transient_request
from .service import ServiceCircuitBreaker, ServiceFailure, ServiceNotFound
from .state import XCursor
from .x import XFeed, XMedia, XPost, XPostReference

POST_URL_PATTERN: Pattern[str] = re.compile(
    r"https?://(?:www\.)?(?:twitter|x)\.com/([^/]+)/status/(\d+)"
)


class FxEmbed:
    """Fetch and normalize X posts with FxEmbed."""

    service_name: str = "FxEmbed"
    api_url: str = "https://api.fxtwitter.com/2"
    user_agent: str = "https://github.com/EthanC/Bluebird"
    retries: int = 3
    retry_delay: float = 5.0
    supports_profile_lookup: bool = True
    timeline_page_size: int = 100
    include_timeline_replies: bool = True
    supports_timeline_pagination: bool = True
    supports_timeline_since: bool = True

    def __init__(self: Self, circuit_breaker: ServiceCircuitBreaker) -> None:
        """Initialize the data source with shared service health state."""
        self.circuit_breaker: ServiceCircuitBreaker = circuit_breaker

    def log(self: Self, username: str, post_id: str | None = None) -> str:
        """Craft the head of a source log message."""
        head: str = f"{self.service_name}[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def fetch_user(
        self: Self, username: str, cursor: XCursor | None = None
    ) -> XFeed | None:
        """Fetch and normalize the latest available posts for an X user."""
        return self.circuit_breaker.call(lambda: self._fetch_user(username, cursor))

    def _fetch_user(self: Self, username: str, cursor: XCursor | None) -> XFeed:
        """Fetch one user feed while admitted by the circuit breaker."""
        try:
            page_cursor: str | None = None
            seen_page_cursors: set[str] = set()
            feed_username: str | None = None
            posts_by_id: dict[str, XPost] = {}
            feed: XFeed | None = None

            while True:
                try:
                    res: Response = self._request_timeline(
                        username, cursor, page_cursor
                    )
                except niquests.HTTPError as error:
                    if (
                        page_cursor is None
                        and self.supports_profile_lookup
                        and self._is_not_found(error)
                    ):
                        feed = XFeed(
                            username=self._fetch_profile_username(username),
                            posts=(),
                            complete=False,
                        )
                        break

                    raise

                logger.debug(f"{self.log(username)} Requested data for user")
                logger.trace(f"{self.log(username)} {res=}")

                if res.status_code == 204:
                    feed = XFeed(username=username, posts=(), complete=False)
                    break

                data: Any = res.json()

                if not isinstance(data, dict) or not isinstance(
                    data.get("results"), list
                ):
                    raise ValueError(f"Expected results, received invalid data {data=}")

                results: list[Any] = data["results"]
                feed_username = self._feed_username(results, username) or feed_username
                reached_cursor: bool = False

                for post_data in results:
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
                                f"{self.log(feed_username or username)} Skipped lossy repost data"
                            )
                            continue

                        post: XPost = self._normalize_post(post_data)
                        posts_by_id.setdefault(post.post_id, post)

                        if cursor and not XCursor(
                            post.created_at, post.post_id
                        ).is_after(cursor):
                            reached_cursor = True
                    except (TypeError, ValueError, OverflowError) as e:
                        logger.opt(exception=e).error(
                            f"{self.log(feed_username or username)} Skipped invalid post data"
                        )
                        logger.trace(
                            f"{self.log(feed_username or username)} {post_data=}"
                        )

                cursor_data: Any = data.get("cursor")
                bottom_cursor: str | None = (
                    self._string(cursor_data.get("bottom"))
                    if isinstance(cursor_data, dict)
                    else None
                )

                if (
                    not self.supports_timeline_pagination
                    or cursor is None
                    or reached_cursor
                    or not bottom_cursor
                    or bottom_cursor in seen_page_cursors
                ):
                    break

                seen_page_cursors.add(bottom_cursor)
                page_cursor = bottom_cursor

            if feed is None:
                if feed_username is None:
                    feed_username = (
                        self._fetch_profile_username(username)
                        if self.supports_profile_lookup
                        else username
                    )

                feed = XFeed(
                    username=feed_username,
                    posts=tuple(posts_by_id.values()),
                    complete=False,
                )
        except (niquests.RequestException, TypeError, ValueError, OverflowError) as e:
            logger.opt(exception=e).error(
                f"{self.log(username)} Failed to fetch data for user"
            )

            if self._is_not_found(e):
                raise ServiceNotFound from e

            raise ServiceFailure from e

        logger.debug(f"{self.log(username)} Fetched data for user")
        logger.trace(f"{self.log(username)} {feed=}")

        return feed

    def _request_timeline(
        self: Self, username: str, cursor: XCursor | None, page_cursor: str | None
    ) -> Response:
        """Request one profile timeline page from FxEmbed."""
        params: dict[str, str] = {"count": str(self.timeline_page_size)}

        if self.include_timeline_replies:
            params["with_replies"] = "true"

        if page_cursor and self.supports_timeline_pagination:
            params["cursor"] = page_cursor
        elif cursor and self.supports_timeline_since:
            # Include the prior second so same-second posts can be compared by ID.
            params["since"] = str(max(cursor.created_at - 1, 0))

        return retry_request(
            lambda: niquests.get(
                f"{self.api_url}/profile/{username}/statuses",
                params=params,
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=0,
            ).raise_for_status(),
            self.retries,
            self.retry_delay,
            niquests.RequestException,
            retry_transient_request,
        )

    def _fetch_profile_username(self: Self, username: str) -> str:
        """Fetch the canonical username for an available profile."""
        res: Response = retry_request(
            lambda: niquests.get(
                f"{self.api_url}/profile/{username}",
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=0,
            ).raise_for_status(),
            self.retries,
            self.retry_delay,
            niquests.RequestException,
            retry_transient_request,
        )
        data: Any = res.json()
        profile: Any = data.get("user") if isinstance(data, dict) else None
        profile_username: str | None = (
            self._string(profile.get("screen_name"))
            if isinstance(profile, dict)
            else None
        )

        if not profile_username:
            raise ValueError(f"Expected user profile, received invalid data {data=}")

        return profile_username

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Fetch and normalize one X post."""
        return self.circuit_breaker.call(lambda: self._fetch_post(username, post_id))

    def _fetch_post(self: Self, username: str, post_id: str) -> XPost:
        """Fetch one post while admitted by the circuit breaker."""
        try:
            res: Response = self._request_post(username, post_id)

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

            if self._is_not_found(e):
                raise ServiceNotFound from e

            raise ServiceFailure from e

        logger.debug(f"{self.log(username, post_id)} Fetched post data")
        logger.trace(f"{self.log(username, post_id)} {post=}")

        return post

    def _request_post(self: Self, username: str, post_id: str) -> Response:
        """Request one post from FxEmbed."""
        return retry_request(
            lambda: niquests.get(
                f"{self.api_url}/status/{post_id}",
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=0,
            ).raise_for_status(),
            self.retries,
            self.retry_delay,
            niquests.RequestException,
            retry_transient_request,
        )

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        """Return whether FxEmbed confirmed a resource is missing."""
        return (
            isinstance(error, niquests.HTTPError)
            and error.response is not None
            and error.response.status_code == 404
        )

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
            source=self.service_name,
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
    def _feed_username(cls, results: list[Any], fallback: str) -> str | None:
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

        return None

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
