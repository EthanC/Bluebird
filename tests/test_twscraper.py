import asyncio
import json
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from loguru import logger
from twscrape import NoAccountError, Response
from twscrape.account import Account
from twscrape.http import HttpClient
from twscrape.models import MediaPhoto, TextLink, Tweet
from twscrape.queue_client import Ctx

from core.format import Format
from core.service import ServiceCircuitBreaker, ServiceDeferred, ServiceFailure
from core.twscraper import Twscraper, TwscraperRuntime, _TwscraperUnavailable


@pytest.fixture
def runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TwscraperRuntime]:
    monkeypatch.setenv("TWS_TELEMETRY", "0")
    monkeypatch.setattr(
        Account,
        "make_client",
        lambda self, proxy=None: cast(HttpClient, SimpleNamespace(aclose=AsyncMock())),
    )
    runtime = TwscraperRuntime(tmp_path / "twscraper.db")
    runtime.start("auth_token=test; ct0=test")
    try:
        yield runtime
    finally:
        runtime.close()


@dataclass
class RawResponse:
    status_code: int
    headers: dict[str, str]
    payload: dict[str, Any]
    text: str = ""
    content: bytes = b""
    url: str = "https://x.com/i/api/graphql/test"
    request: Any = None

    def json(self) -> dict[str, Any]:
        return self.payload


def response(
    status: int = 200,
    headers: dict[str, str] | None = None,
    *,
    cursor: str | None = None,
    post_id: str = "123",
) -> Response:
    entries: list[dict[str, Any]] = [{"entryId": f"tweet-{post_id}", "content": {}}]
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom-0",
                "content": {"cursorType": "Bottom", "value": cursor},
            }
        )
    return Response(RawResponse(status, headers or {}, {"data": {"entries": entries}}))


def test_startup_workers_share_account_without_acquisition_failures(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def request(self, method, url, params=None):
        await asyncio.sleep(0.2)
        return response()

    monkeypatch.setattr(Ctx, "req", request)
    barrier = Barrier(6)

    def fetch(index: int) -> Response | None:
        barrier.wait(timeout=5)
        return runtime.run(
            lambda: runtime.api.user_by_login_raw(f"user{index}"), timeout=1.0
        )

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(fetch, range(6)))

    assert len(results) == 6
    assert all(item is not None and item.status_code == 200 for item in results)
    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and account.locks == {}


@pytest.mark.parametrize("timeline", [False, True])
@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize(
    "headers", [{}, {"x-rate-limit-remaining": "495", "x-rate-limit-limit": "500"}]
)
def test_server_error_retries_without_persisting_account_cooldown(
    runtime: TwscraperRuntime,
    monkeypatch: pytest.MonkeyPatch,
    timeline: bool,
    status: int,
    headers: dict[str, str],
) -> None:
    replies = [response(status, headers), response()]
    request = AsyncMock(side_effect=replies)
    monkeypatch.setattr(Ctx, "req", request)
    source = Twscraper(ServiceCircuitBreaker("Twscraper", 10, 3600, 24), runtime)
    source.retries = 1
    source.retry_delay = 0

    async def fetch():
        if not timeline:
            return await runtime.api.user_by_login_raw("example")
        async with aclosing(runtime.api.user_tweets_and_replies_raw(123)) as pages:
            return [page async for page in pages]

    result = source._request(fetch, "example")

    assert result is not None
    assert request.await_count == 2
    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and account.locks == {}


@pytest.mark.parametrize("status", [429, 503])
def test_rate_limit_survives_restart(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    reset = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())
    request = AsyncMock(
        return_value=response(
            status, {"x-rate-limit-remaining": "0", "x-rate-limit-reset": str(reset)}
        )
    )
    monkeypatch.setattr(Ctx, "req", request)

    with pytest.raises(ServiceDeferred):
        runtime.run(lambda: runtime.api.user_by_login_raw("example"))

    runtime.close()
    restarted = TwscraperRuntime(runtime.database_path)
    restarted.start("auth_token=test; ct0=test")
    try:
        with pytest.raises(ServiceDeferred):
            restarted.run(lambda: restarted.api.user_by_login_raw("example"))
        account = restarted.run(lambda: restarted.api.pool.get_account("bluebird"))
        assert account is not None
        assert int(account.locks["UserByScreenName"].timestamp()) == reset
        assert request.await_count == 1
    finally:
        restarted.close()


def test_cancelled_request_releases_account_before_next_worker(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaned_up = False

    async def stalled_request(self, method, url, params=None):
        nonlocal cleaned_up
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            cleaned_up = True

    monkeypatch.setattr(Ctx, "req", stalled_request)
    with pytest.raises(_TwscraperUnavailable, match="deadline"):
        runtime.run(lambda: runtime.api.user_by_login_raw("example"), timeout=0.05)

    assert cleaned_up
    request = AsyncMock(return_value=response())
    monkeypatch.setattr(Ctx, "req", request)
    assert runtime.run(lambda: runtime.api.user_by_login_raw("example")) is not None
    assert request.await_count == 1


def test_abrupt_exit_does_not_leave_an_in_use_lock(runtime: TwscraperRuntime) -> None:
    runtime.close()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "from core.twscraper import TwscraperRuntime; "
            "runtime = TwscraperRuntime(Path(sys.argv[1])); "
            "runtime.start('auth_token=test; ct0=test'); "
            "runtime.run(lambda: runtime.api.pool.get_for_queue_or_wait('UserByScreenName')); "
            "os._exit(0)",
            str(runtime.database_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        timeout=15,
    )
    restarted = TwscraperRuntime(runtime.database_path)
    restarted.start("auth_token=test; ct0=test")
    try:
        account = restarted.run(lambda: restarted.api.pool.get_account("bluebird"))
        assert account is not None and account.locks == {}
    finally:
        restarted.close()


def test_persisted_startup_lock_defers_without_disabling_service(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())
    runtime.run(
        lambda: runtime.api.pool.lock_until("bluebird", "UserByScreenName", reset)
    )
    runtime.close()
    restarted = TwscraperRuntime(runtime.database_path)
    messages = []
    sink = logger.add(messages.append, level="WARNING")
    try:
        restarted.start("auth_token=test; ct0=test")
        request = AsyncMock(return_value=response())
        monkeypatch.setattr(Ctx, "req", request)
        breaker = ServiceCircuitBreaker("Twscraper", 1, 3600, 24)
        for index in range(6):
            source = Twscraper(breaker, restarted)
            source.retries = 0
            assert source.fetch_user(f"user{index}") is None

        assert breaker._failures == 0
        assert breaker._disabled_until is None
        assert request.await_count == 0
        assert not [message for message in messages if message.record["level"].no >= 40]
        assert (
            len([message for message in messages if "UserByScreenName" in message]) == 1
        )
        account = restarted.run(lambda: restarted.api.pool.get_account("bluebird"))
        assert account is not None
        assert int(account.locks["UserByScreenName"].timestamp()) == reset

        # Simulate the stored cooldown expiring without waiting fifteen minutes.
        restarted.run(
            lambda: restarted.api.pool.lock_until("bluebird", "UserByScreenName", 1)
        )
        assert (
            restarted.run(lambda: restarted.api.user_by_login_raw("example"))
            is not None
        )
        assert request.await_count == 1
    finally:
        restarted.close()
        logger.remove(sink)


def test_runtime_shutdown_cancels_active_and_queued_fetches_without_errors(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Event()

    async def request(self, method, url, params=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Ctx, "req", request)
    breaker = ServiceCircuitBreaker("Twscraper", 1, 3600, 24)
    source = Twscraper(breaker, runtime)
    messages = []
    sink = logger.add(messages.append, level="ERROR")
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            active = workers.submit(source.fetch_user, "example")
            assert started.wait(5)
            queued = workers.submit(source.fetch_user, "another")
            runtime.close()
            assert active.result(timeout=2) is None
            assert queued.result(timeout=2) is None
        assert breaker._failures == 0
        assert not messages
    finally:
        logger.remove(sink)


def test_runtime_shutdown_interrupts_retry_delay(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = Event()
    original_wait = runtime.wait_for_retry

    def wait(delay: float) -> None:
        waiting.set()
        original_wait(delay)

    monkeypatch.setattr(runtime, "wait_for_retry", wait)
    monkeypatch.setattr(Ctx, "req", AsyncMock(return_value=response(503)))
    breaker = ServiceCircuitBreaker("Twscraper", 1, 3600, 24)
    source = Twscraper(breaker, runtime)
    source.retry_delay = 30
    with ThreadPoolExecutor(max_workers=1) as workers:
        active = workers.submit(source.fetch_user, "example")
        assert waiting.wait(5)
        runtime.close()
        assert active.result(timeout=2) is None
    assert breaker._failures == 0


def test_repeated_server_errors_exhaust_adapter_retries(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AsyncMock(return_value=response(503))
    monkeypatch.setattr(Ctx, "req", request)
    source = Twscraper(ServiceCircuitBreaker("Twscraper", 10, 3600, 24), runtime)
    source.retries = 2
    source.retry_delay = 0

    with pytest.raises(ServiceFailure) as failure:
        source._request(lambda: runtime.api.user_by_login_raw("example"), "example")

    assert isinstance(failure.value.__cause__, _TwscraperUnavailable)
    assert "HTTP 503" in str(failure.value.__cause__)
    assert request.await_count == 3
    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and account.locks == {}


def test_authentication_failure_still_deactivates_account(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AsyncMock(
        return_value=Response(
            RawResponse(
                401,
                {},
                {"errors": [{"code": 32, "message": "Could not authenticate you"}]},
            )
        )
    )
    monkeypatch.setattr(Ctx, "req", request)

    with pytest.raises(NoAccountError):
        runtime.run(lambda: runtime.api.user_by_login_raw("example"))

    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and not account.active
    assert request.await_count == 1


def test_pagination_retries_server_error_on_later_page(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AsyncMock(
        side_effect=[
            response(cursor="next"),
            response(503),
            response(cursor="next"),
            response(post_id="124"),
        ]
    )
    monkeypatch.setattr(Ctx, "req", request)
    source = Twscraper(ServiceCircuitBreaker("Twscraper", 10, 3600, 24), runtime)
    source.retries = 1
    source.retry_delay = 0

    async def fetch():
        async with aclosing(runtime.api.user_tweets_and_replies_raw(123)) as pages:
            return [page async for page in pages]

    pages = source._request(fetch, "example")

    assert len(pages) == 2
    variables = [
        json.loads(call.kwargs["params"]["variables"])
        for call in request.await_args_list
    ]
    assert [item.get("cursor") for item in variables] == [None, "next", None, "next"]
    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and account.locks == {}


def test_early_pagination_close_releases_account(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AsyncMock(return_value=response(cursor="next"))
    monkeypatch.setattr(Ctx, "req", request)

    async def fetch():
        async with aclosing(runtime.api.user_tweets_and_replies_raw(123)) as pages:
            return await anext(pages)

    assert runtime.run(fetch).status_code == 200
    account = runtime.run(lambda: runtime.api.pool.get_account("bluebird"))
    assert account is not None and account.locks == {}
    assert request.await_count == 1


def test_pagination_stops_on_repeated_cursor(
    runtime: TwscraperRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = AsyncMock(return_value=response(cursor="repeated"))
    monkeypatch.setattr(Ctx, "req", request)

    async def fetch():
        async with aclosing(runtime.api.user_tweets_and_replies_raw(123)) as pages:
            return [page async for page in pages]

    assert len(runtime.run(fetch)) == 1
    assert request.await_count == 2


def tweet_with_links(
    text: str,
    links: list[TextLink],
    *,
    bio: str = "",
    description_links: list[TextLink] | None = None,
) -> Tweet:
    return cast(
        Tweet,
        SimpleNamespace(
            id_str="123",
            url="https://x.com/example/status/123",
            user=SimpleNamespace(
                username="example",
                displayname="Example User",
                rawDescription=bio,
                descriptionLinks=description_links or [],
                profileImageUrl="",
            ),
            date=datetime(2000, 1, 1, tzinfo=UTC),
            rawContent=text,
            links=links,
            media=SimpleNamespace(photos=[], videos=[], animated=[]),
            possibly_sensitive=False,
            inReplyToTweetIdStr=None,
            inReplyToUser=None,
            inReplyToScreenName=None,
            isQuoteStatus=False,
            quotedTweet=None,
            retweetedTweet=None,
        ),
    )


def test_normalize_post_decodes_hearts_before_discord_formatting() -> None:
    tweet = tweet_with_links("Example &lt;3 &lt;3", [])

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == "Example <3 <3"
    assert Format.x_text(post.text, "https://x.com/") == r"Example \<3 \<3"


@pytest.mark.parametrize(
    ("encoded", "decoded"),
    [
        ("&lt;tag&gt; &amp; friends", "<tag> & friends"),
        ("&quot;hello&quot; &#39;world&#39;", "\"hello\" 'world'"),
        ("&#60;3 &#x1F499;", "<3 💙"),
        ("literal &amp;lt;3", "literal &lt;3"),
        ("plain <3 & 💙", "plain <3 & 💙"),
        ("", ""),
    ],
)
def test_normalize_post_decodes_text_fields_once(encoded: str, decoded: str) -> None:
    tweet = tweet_with_links(encoded, [], bio=encoded)
    tweet.user.displayname = encoded

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == (decoded or None)
    assert post.bio == (decoded or None)
    assert post.display_name == (decoded or tweet.user.username)


def test_normalize_post_preserves_entities_in_expanded_urls() -> None:
    url = "https://example.com/?a=1&copy=2&literal=&lt;3"
    link = TextLink(url, None, "https://t.co/link")
    tweet = tweet_with_links(
        "&lt;3 https://t.co/link",
        [link],
        bio="&lt;3 https://t.co/link",
        description_links=[link],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == f"<3 {url}"
    assert post.bio == f"<3 {url}"


def test_normalize_post_expands_tco_links() -> None:
    tweet = tweet_with_links(
        "Lorem ipsum https://t.co/article",
        [
            TextLink(
                url="https://example.com/article",
                text="example.com/article",
                tcourl="https://t.co/article",
            )
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == "Lorem ipsum https://example.com/article"


def test_normalize_post_only_expands_mapped_tco_links() -> None:
    tweet = tweet_with_links(
        "https://t.co/first https://t.co/unmapped https://t.co/second",
        [
            TextLink("https://example.com/first", None, "https://t.co/first"),
            TextLink("https://example.com/missing", None, None),
            TextLink("https://example.com/second", None, "https://t.co/second"),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == (
        "https://example.com/first https://t.co/unmapped https://example.com/second"
    )


def test_normalize_post_expands_tco_links_in_bio() -> None:
    tweet = tweet_with_links(
        "Post text",
        [],
        bio=(
            "Lorem ipsum dolor sit amet https://t.co/profile // Email: user@example.com"
        ),
        description_links=[
            TextLink(
                url="https://example.com/profile",
                text="example.com/profile",
                tcourl="https://t.co/profile",
            ),
            TextLink(
                url="https://example.com/about", text="example.com/about", tcourl=None
            ),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.bio == (
        "Lorem ipsum dolor sit amet https://example.com/profile // Email: "
        "user@example.com"
    )


def test_normalize_post_removes_media_shortlinks() -> None:
    tweet = tweet_with_links("Lorem ipsum dolor sit amet https://t.co/media", [])
    tweet.media.photos.append(MediaPhoto(url="https://example.com/media/photo.jpg"))

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, {"https://t.co/media"})

    assert post.text == "Lorem ipsum dolor sit amet"
    assert post.media[0].url == "https://example.com/media/photo.jpg"


def test_media_metadata_recovers_alt_text_and_shortlinks() -> None:
    result = {
        "legacy": {
            "extended_entities": {
                "media": [
                    {
                        "url": "https://t.co/photo",
                        "media_url_https": "https://example.com/media/photo.jpg",
                        "ext_alt_text": "Lorem ipsum",
                    },
                    {
                        "url": "https://t.co/video",
                        "media_url_https": "https://example.com/media/video.jpg",
                        "ext_alt_text": None,
                    },
                ]
            }
        }
    }

    alt_text, shortlinks = Twscraper._media_metadata(result)

    assert alt_text == {"https://example.com/media/photo.jpg": "Lorem ipsum"}
    assert shortlinks == {"https://t.co/photo", "https://t.co/video"}
