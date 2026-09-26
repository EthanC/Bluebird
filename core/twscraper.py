"""Authenticated twscrape data source for X posts."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import aclosing
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from typing import Any, Self, TypeVar

from loguru import logger
from twscrape import (
    API,
    AccountsPool,
    ConnectError,
    HttpError,
    NetworkError,
    NoAccountError,
    Response,
)
from twscrape.account import has_required_cookies
from twscrape.models import Tweet, User, parse_tweet, parse_user
from twscrape.utils import parse_cookies, to_old_rep

from .service import ServiceCircuitBreaker, ServiceFailure, ServiceNotFound
from .state import XCursor
from .x import XFeed, XMedia, XPost, XPostReference

_ACCOUNT_LABEL = "bluebird"
_ACCOUNT_WAIT_SECONDS = 10.0
_FETCH_TIMEOUT_SECONDS = 60.0
_START_TIMEOUT_SECONDS = 15.0
_CANCEL_TIMEOUT_SECONDS = 10.0
_ResultT = TypeVar("_ResultT")


class TwscraperConfigurationError(Exception):
    """Indicate invalid local twscrape configuration."""


class _TwscraperUnavailable(Exception):
    """Indicate that twscrape ended without a usable response."""


class _TwscraperDataError(Exception):
    """Indicate malformed or inconsistent upstream data."""


class _ConfirmedNotFound(Exception):
    """Indicate an explicit missing-resource response from X."""


def validate_cookies(value: str) -> None:
    """Validate required cookie names without exposing their values."""
    try:
        cookies: dict[str, str] = parse_cookies(value)
    except Exception:
        raise TwscraperConfigurationError(
            "TWSCRAPER_COOKIES must be a valid Cookie header"
        ) from None

    if not has_required_cookies(cookies):
        raise TwscraperConfigurationError(
            "TWSCRAPER_COOKIES must contain nonempty auth_token and ct0 cookies"
        )


class TwscraperRuntime:
    """Own twscrape's event loop, API client, and persistent account pool."""

    def __init__(self: Self, database_path: Path) -> None:
        """Prepare a stopped process-wide runtime."""
        self.database_path: Path = database_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._ready: Event = Event()
        self._lock: Lock = Lock()
        self._closed: bool = False
        self._pool: AccountsPool | None = None
        self._api: API | None = None

    def start(self: Self, cookies: str) -> None:
        """Start the event loop and initialize the cookie account."""
        validate_cookies(cookies)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._protect_database()
        self._thread = Thread(target=self._run_loop, name="twscraper", daemon=False)
        self._thread.start()

        if not self._ready.wait(_START_TIMEOUT_SECONDS) or self._loop is None:
            self.close()
            raise TwscraperConfigurationError("Twscraper event loop failed to start")

        try:
            self.run(lambda: self._initialize(cookies), _START_TIMEOUT_SECONDS)
        except Exception:
            self.close()
            raise

    def run(
        self: Self,
        operation: Callable[[], Awaitable[_ResultT]],
        timeout: float = _FETCH_TIMEOUT_SECONDS,
    ) -> _ResultT:
        """Run one operation on the owned loop and cancel it at the deadline."""
        with self._lock:
            loop: asyncio.AbstractEventLoop | None = self._loop
            if self._closed or loop is None or not loop.is_running():
                raise _TwscraperUnavailable("Twscraper runtime is not running")

            cleanup_finished = Event()
            future: Future[_ResultT] = asyncio.run_coroutine_threadsafe(
                self._track_cleanup(operation(), cleanup_finished), loop
            )

        try:
            return future.result(timeout)
        except FutureTimeoutError:
            future.cancel()
            # QueueClient and raw generators release account and HTTP resources in
            # cancellation handlers. Do not return while that cleanup is still live.
            if not cleanup_finished.wait(_CANCEL_TIMEOUT_SECONDS):
                self.close()
            raise _TwscraperUnavailable(
                f"Twscraper request exceeded its {timeout:g}s deadline"
            ) from None

    def close(self: Self) -> None:
        """Cancel pending work, finish cleanup, and join the event-loop thread."""
        with self._lock:
            if self._closed:
                return

            self._closed = True
            loop: asyncio.AbstractEventLoop | None = self._loop
            thread: Thread | None = self._thread

        if loop is not None:
            if loop.is_running():
                shutdown = asyncio.run_coroutine_threadsafe(
                    self._cancel_pending(), loop
                )
                shutdown.result()
            loop.call_soon_threadsafe(loop.stop)

        if thread is not None:
            thread.join()

    async def _initialize(self: Self, cookies: str) -> None:
        """Create the pool and import new cookie credentials when needed."""
        try:
            parsed_cookies: dict[str, str] = parse_cookies(cookies)
        except Exception:
            raise TwscraperConfigurationError(
                "TWSCRAPER_COOKIES could not be parsed"
            ) from None

        if not has_required_cookies(parsed_cookies):
            raise TwscraperConfigurationError(
                "TWSCRAPER_COOKIES must contain nonempty auth_token and ct0 cookies"
            )

        pool = AccountsPool(
            db_file=str(self.database_path),
            raise_when_no_account=True,
            wait_timeout=_ACCOUNT_WAIT_SECONDS,
            wait_interval=1.0,
        )
        account = await pool.get_account(_ACCOUNT_LABEL)

        if account is None or account.cookies != parsed_cookies:
            await pool.add_account_cookies(_ACCOUNT_LABEL, cookies)

        self._pool = pool
        self._api = API(pool)

    def _protect_database(self: Self) -> None:
        """Create or restrict the credential database before SQLite opens it."""
        try:
            descriptor = os.open(
                self.database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)

        self.database_path.chmod(0o600)

    async def _track_cleanup(
        self: Self, operation: Awaitable[_ResultT], finished: Event
    ) -> _ResultT:
        """Signal only after cancellation handlers and context managers finish."""
        try:
            return await operation
        finally:
            finished.set()

    async def _cancel_pending(self: Self) -> None:
        """Cancel all loop tasks except this shutdown task."""
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]

        for task in pending:
            task.cancel()

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _run_loop(self: Self) -> None:
        """Run and finally drain the process-owned event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()

        try:
            with self._lock:
                closed = self._closed
            if not closed:
                loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    @property
    def api(self: Self) -> API:
        """Return the initialized API while executing on the owned loop."""
        if self._api is None:
            raise _TwscraperUnavailable("Twscraper API is not initialized")
        return self._api


class Twscraper:
    """Fetch and normalize authenticated X data with twscrape."""

    service_name: str = "Twscraper"
    retries: int = 3
    retry_delay: float = 5.0

    def __init__(
        self: Self, circuit_breaker: ServiceCircuitBreaker, runtime: TwscraperRuntime
    ) -> None:
        """Initialize one adapter with shared runtime and service health."""
        self.circuit_breaker: ServiceCircuitBreaker = circuit_breaker
        self.runtime: TwscraperRuntime = runtime

    def log(self: Self, username: str, post_id: str | None = None) -> str:
        """Craft the head of a source log message."""
        head: str = f"{self.service_name}[@{username}]"
        if post_id:
            head += f"[{post_id}]"
        return head

    def fetch_user(
        self: Self, username: str, cursor: XCursor | None = None
    ) -> XFeed | None:
        """Fetch and normalize the authenticated profile timeline."""
        return self.circuit_breaker.call(
            lambda: self._request(lambda: self._fetch_user(username, cursor), username)
        )

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Fetch and normalize one authenticated X post."""
        return self.circuit_breaker.call(
            lambda: self._request(
                lambda: self._fetch_post(username, post_id), username, post_id
            )
        )

    def _request(
        self: Self,
        operation: Callable[[], Awaitable[_ResultT]],
        username: str,
        post_id: str | None = None,
    ) -> _ResultT:
        """Apply bounded adapter retries and map failures to service health."""
        attempt = 0

        while True:
            try:
                return self.runtime.run(operation)
            except _ConfirmedNotFound as error:
                raise ServiceNotFound from error
            except Exception as error:
                transient = isinstance(
                    error,
                    (
                        _TwscraperUnavailable,
                        NoAccountError,
                        NetworkError,
                        ConnectError,
                        HttpError,
                        OSError,
                        TimeoutError,
                    ),
                )

                if transient and attempt < self.retries:
                    attempt += 1
                    sleep(self.retry_delay)
                    continue

                logger.opt(exception=error).error(
                    f"{self.log(username, post_id)} Failed to fetch data"
                )
                raise ServiceFailure from error

    async def _fetch_user(self: Self, username: str, cursor: XCursor | None) -> XFeed:
        """Resolve a profile and consume raw timeline pages safely."""
        profile_response: Response | None = await self.runtime.api.user_by_login_raw(
            username
        )
        if profile_response is None:
            raise _TwscraperUnavailable("Profile lookup returned no response")

        profile_data = self._response_object(profile_response)
        profile: User | None = parse_user(profile_response)
        if profile is None:
            if self._confirmed_missing(profile_data):
                raise _ConfirmedNotFound
            raise _TwscraperDataError("Profile response could not be parsed")

        posts_by_id: dict[str, XPost] = {}
        malformed = False
        received_page = False
        reached_cursor = False
        terminal = False
        generator = self.runtime.api.user_tweets_and_replies_raw(profile.id, limit=-1)

        async with aclosing(generator) as pages:
            async for response in pages:
                received_page = True
                page_data = self._response_object(response)
                groups, page_malformed = self._timeline_groups(page_data)
                malformed = malformed or page_malformed
                normalized = to_old_rep(page_data)
                normalized_tweets = normalized.get("tweets")
                if not isinstance(normalized_tweets, dict):
                    normalized_tweets = {}
                    malformed = True

                for group, pinned in groups:
                    group_cursors: list[XCursor] = []

                    for result in group:
                        post_id = self._tweet_result_id(result)
                        if post_id is None:
                            malformed = True
                            continue

                        raw_tweet = normalized_tweets.get(post_id)
                        if not isinstance(raw_tweet, dict):
                            malformed = True
                            continue

                        author_id = raw_tweet.get("user_id_str")
                        if author_id is not None and str(author_id) != profile.id_str:
                            continue

                        try:
                            tweet = Tweet.parse(raw_tweet, normalized)
                            if tweet.id_str != post_id:
                                raise ValueError("Parsed timeline event ID changed")
                            if (
                                tweet.user.username.casefold()
                                != profile.username.casefold()
                            ):
                                continue
                            post = self._normalize_post(
                                tweet, self._media_alt_text(result)
                            )
                        except TypeError, ValueError, OverflowError:
                            malformed = True
                            continue

                        posts_by_id.setdefault(post.post_id, post)
                        if cursor is not None and not pinned:
                            group_cursors.append(XCursor(post.created_at, post.post_id))

                    # A conversation module can contain an old thread parent and a
                    # new reply. It covers the cursor only when the whole monitored
                    # user's group is at or before that cursor.
                    if (
                        cursor is not None
                        and group_cursors
                        and all(not item.is_after(cursor) for item in group_cursors)
                    ):
                        reached_cursor = True

                bottom_cursor, cursor_valid = self._bottom_cursor(page_data)
                malformed = malformed or not cursor_valid
                terminal = cursor_valid and bottom_cursor is None

                if cursor is None or reached_cursor or terminal:
                    break

        complete = not malformed and (
            (cursor is None and received_page)
            or (cursor is not None and (reached_cursor or terminal))
        )
        feed = XFeed(
            username=profile.username,
            posts=tuple(posts_by_id.values()),
            complete=complete,
        )
        logger.debug(f"{self.log(profile.username)} Fetched data for user")
        logger.trace(f"{self.log(profile.username)} {feed=}")
        return feed

    async def _fetch_post(self: Self, username: str, post_id: str) -> XPost:
        """Fetch one raw post response and require an exact parsed ID."""
        response: Response | None = await self.runtime.api.tweet_details_raw(
            int(post_id)
        )
        if response is None:
            raise _TwscraperUnavailable("Post lookup returned no response")

        data = self._response_object(response)
        tweet: Tweet | None = parse_tweet(response, int(post_id))
        if tweet is None:
            if self._confirmed_missing(data, post_id):
                raise _ConfirmedNotFound
            raise _TwscraperDataError("Post response could not be parsed")
        if tweet.id_str != post_id:
            raise _TwscraperDataError("Post response returned a different ID")

        result = self._find_tweet_result(data, post_id)
        post = self._normalize_post(
            tweet, self._media_alt_text(result) if result is not None else {}
        )
        logger.debug(f"{self.log(username, post_id)} Fetched post data")
        logger.trace(f"{self.log(username, post_id)} {post=}")
        return post

    def _normalize_post(self: Self, tweet: Tweet, alt_text: Mapping[str, str]) -> XPost:
        """Translate a twscrape tweet while preserving the outer repost event."""
        media: list[XMedia] = []
        text = tweet.rawContent

        for link in tweet.links:
            if link.tcourl:
                text = text.replace(link.tcourl, link.url)

        for photo in tweet.media.photos:
            media.append(XMedia(photo.url, alt_text.get(photo.url)))

        for video in tweet.media.videos:
            if not video.variants:
                continue
            variant = max(video.variants, key=lambda item: item.bitrate)
            media.append(XMedia(variant.url, alt_text.get(video.thumbnailUrl)))

        for animated in tweet.media.animated:
            media.append(XMedia(animated.videoUrl, alt_text.get(animated.thumbnailUrl)))

        return XPost(
            post_id=tweet.id_str,
            url=tweet.url.replace("twitter.com", "x.com"),
            username=tweet.user.username,
            display_name=tweet.user.displayname or tweet.user.username,
            created_at=int(tweet.date.timestamp()),
            source=self.service_name,
            text=text or None,
            bio=tweet.user.rawDescription or None,
            profile_image_url=tweet.user.profileImageUrl or None,
            media=tuple(media),
            possibly_sensitive=bool(tweet.possibly_sensitive),
            is_reply=tweet.inReplyToTweetIdStr is not None,
            is_quote=tweet.isQuoteStatus or tweet.quotedTweet is not None,
            is_repost=tweet.retweetedTweet is not None,
            reply_to=self._reply_reference(tweet),
            quote_of=self._tweet_reference(tweet.quotedTweet),
            repost_of=self._tweet_reference(tweet.retweetedTweet),
        )

    @staticmethod
    def _reply_reference(tweet: Tweet) -> XPostReference | None:
        """Build a reply reference when both ID and author are available."""
        post_id = tweet.inReplyToTweetIdStr
        username = (
            tweet.inReplyToUser.username
            if tweet.inReplyToUser is not None
            else tweet.inReplyToScreenName
        )
        return (
            XPostReference(username=username, post_id=post_id)
            if username and post_id
            else None
        )

    @staticmethod
    def _tweet_reference(tweet: Tweet | None) -> XPostReference | None:
        """Build a reference to an embedded quote or repost original."""
        return (
            XPostReference(username=tweet.user.username, post_id=tweet.id_str)
            if tweet is not None
            else None
        )

    @classmethod
    def _timeline_groups(
        cls, data: dict[str, Any]
    ) -> tuple[list[tuple[list[dict[str, Any]], bool]], bool]:
        """Select top-level timeline events without nested related tweets."""
        entries, malformed = cls._timeline_entries(data)
        if entries is None:
            return [], True

        groups: list[tuple[list[dict[str, Any]], bool]] = []

        for entry, instruction_pinned in entries:
            entry_id = entry.get("entryId")
            content = entry.get("content")
            if not isinstance(entry_id, str):
                malformed = True
                continue
            if entry_id.startswith("cursor-"):
                if entry_id.startswith("cursor-bottom-") and (
                    not isinstance(content, dict)
                    or content.get("cursorType") != "Bottom"
                    or not isinstance(content.get("value"), str)
                    or not content["value"]
                ):
                    malformed = True
                continue
            if not isinstance(content, dict):
                if entry_id.startswith(("tweet-", "profile-conversation-")):
                    malformed = True
                continue

            pinned = (
                instruction_pinned
                or entry_id.startswith("profile-pinned-tweet-")
                or cls._path(content, "itemContent", "socialContext", "contextType")
                == "Pin"
            )
            direct = cls._path(content, "itemContent", "tweet_results", "result")
            if isinstance(direct, dict):
                groups.append(([direct], pinned))
                continue

            items = content.get("items")
            if isinstance(items, list):
                if not entry_id.startswith("profile-conversation-"):
                    continue

                module: list[dict[str, Any]] = []
                for item in items:
                    item_entry_id = (
                        item.get("entryId") if isinstance(item, dict) else None
                    )
                    result = cls._path(
                        item, "item", "itemContent", "tweet_results", "result"
                    )
                    if isinstance(item_entry_id, str) and isinstance(result, dict):
                        module.append(result)
                    else:
                        malformed = True
                if module:
                    groups.append((module, pinned))
                elif entry_id.startswith("profile-conversation-"):
                    malformed = True
                continue

            if entry_id.startswith(("tweet-", "profile-conversation-")):
                malformed = True

        return groups, malformed

    @staticmethod
    def _timeline_entries(
        data: dict[str, Any],
    ) -> tuple[list[tuple[dict[str, Any], bool]] | None, bool]:
        """Find the response's timeline instruction entries."""
        found_instructions = False
        malformed = False
        entries: list[tuple[dict[str, Any], bool]] = []
        stack: list[Any] = [data]

        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                instructions = value.get("instructions")
                if isinstance(instructions, list):
                    found_instructions = True
                    for instruction in instructions:
                        if not isinstance(instruction, dict):
                            malformed = True
                            continue
                        instruction_type = instruction.get("type")
                        pinned = instruction_type == "TimelinePinEntry"
                        instruction_entries = instruction.get("entries")
                        if isinstance(instruction_entries, list):
                            entries.extend(
                                (item, pinned)
                                for item in instruction_entries
                                if isinstance(item, dict)
                            )
                            if any(
                                not isinstance(item, dict)
                                for item in instruction_entries
                            ):
                                malformed = True
                        elif "entries" in instruction:
                            malformed = True
                        entry = instruction.get("entry")
                        if isinstance(entry, dict):
                            entries.append((entry, pinned))
                        elif "entry" in instruction:
                            malformed = True
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

        if not found_instructions:
            return None, True

        unique: dict[str, tuple[dict[str, Any], bool]] = {}
        unnamed: list[tuple[dict[str, Any], bool]] = []
        for entry, pinned in entries:
            entry_id = entry.get("entryId")
            if isinstance(entry_id, str):
                existing = unique.get(entry_id)
                unique[entry_id] = (
                    entry,
                    pinned or (existing[1] if existing is not None else False),
                )
            else:
                unnamed.append((entry, pinned))
        return [*unique.values(), *unnamed], malformed

    @classmethod
    def _find_tweet_result(
        cls, data: dict[str, Any], post_id: str
    ) -> dict[str, Any] | None:
        """Find a raw tweet result with an exact post ID."""
        stack: list[Any] = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if cls._tweet_result_id(value) == post_id and (
                    value.get("__typename") in {"Tweet", "TweetWithVisibilityResults"}
                    or "legacy" in value
                ):
                    return value
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        return None

    @classmethod
    def _tweet_result_id(cls, result: dict[str, Any]) -> str | None:
        """Return the event ID from a raw tweet result wrapper."""
        tweet = result.get("tweet")
        if isinstance(tweet, dict):
            result = tweet
        post_id = result.get("rest_id") or result.get("id_str")
        return str(post_id) if post_id is not None and str(post_id).isdigit() else None

    @classmethod
    def _media_alt_text(cls, result: dict[str, Any]) -> dict[str, str]:
        """Recover media alt text omitted by twscrape's media models."""
        tweet = result.get("tweet")
        if isinstance(tweet, dict):
            result = tweet
        legacy = result.get("legacy")
        source = legacy if isinstance(legacy, dict) else result
        media = cls._path(source, "extended_entities", "media")
        recovered: dict[str, str] = {}

        if not isinstance(media, list):
            return recovered

        for item in media:
            if not isinstance(item, dict):
                continue
            url = item.get("media_url_https")
            alt = item.get("ext_alt_text")
            if isinstance(url, str) and isinstance(alt, str) and alt:
                recovered[url] = alt
        return recovered

    @staticmethod
    def _bottom_cursor(data: dict[str, Any]) -> tuple[str | None, bool]:
        """Return the bottom cursor and whether its structure is valid."""
        entries, malformed = Twscraper._timeline_entries(data)
        if entries is None or malformed:
            return None, False

        cursors: set[str] = set()
        found = False
        for entry, _ in entries:
            entry_id = entry.get("entryId")
            content = entry.get("content")
            if not isinstance(content, dict):
                if isinstance(entry_id, str) and entry_id.startswith("cursor-bottom-"):
                    return None, False
                continue

            is_bottom = content.get("cursorType") == "Bottom"
            named_bottom = isinstance(entry_id, str) and entry_id.startswith(
                "cursor-bottom-"
            )
            if not is_bottom and not named_bottom:
                continue
            if not is_bottom:
                return None, False

            found = True
            cursor = content.get("value")
            if not isinstance(cursor, str) or not cursor:
                return None, False
            cursors.add(cursor)

        if len(cursors) > 1:
            return None, False
        return (next(iter(cursors)), True) if found else (None, True)

    @staticmethod
    def _confirmed_missing(
        data: dict[str, Any], target_post_id: str | None = None
    ) -> bool:
        """Return whether X explicitly reports a resource as missing."""
        errors = data.get("errors")
        if isinstance(errors, list):
            for error in errors:
                message = error.get("message") if isinstance(error, dict) else None
                if Twscraper._missing_message(message):
                    return True

        stack: list[Any] = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if target_post_id is None:
                    if value.get("__typename") == "UserUnavailable" and (
                        Twscraper._missing_reason(value.get("reason"))
                        or Twscraper._missing_message(value.get("message"))
                    ):
                        return True
                else:
                    value_id = value.get("rest_id") or value.get("id_str")
                    entry_id = value.get("entryId")
                    identifies_target = (
                        value_id is not None and str(value_id) == target_post_id
                    ) or (isinstance(entry_id, str) and target_post_id in entry_id)
                    if identifies_target and Twscraper._contains_missing(value):
                        return True
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        return False

    @staticmethod
    def _contains_missing(value: dict[str, Any]) -> bool:
        """Find explicit missing evidence inside one target-bound container."""
        stack: list[Any] = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if (
                    Twscraper._missing_reason(item.get("reason"))
                    or Twscraper._missing_message(item.get("message"))
                    or Twscraper._missing_message(
                        Twscraper._path(item, "tombstone", "text", "text")
                    )
                ):
                    return True
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        return False

    @staticmethod
    def _missing_reason(value: Any) -> bool:
        """Return whether an unavailable-result reason explicitly means missing."""
        return isinstance(value, str) and value.casefold().replace("_", "") in {
            "notfound",
            "deleted",
        }

    @staticmethod
    def _missing_message(value: Any) -> bool:
        """Return whether a server message explicitly means missing or deleted."""
        if not isinstance(value, str):
            return False
        folded = value.casefold()
        return any(
            marker in folded
            for marker in (
                "no status found",
                "not found",
                "does not exist",
                "was deleted",
            )
        )

    @staticmethod
    def _response_object(response: Response) -> dict[str, Any]:
        """Read and validate one JSON response object."""
        data: Any = response.json()
        if not isinstance(data, dict):
            raise _TwscraperDataError("Expected a JSON response object")
        return data

    @staticmethod
    def _path(value: Any, *parts: str) -> Any:
        """Read a fixed dictionary path without flattening nested content."""
        for part in parts:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value
