from dataclasses import replace
from typing import cast

import niquests
import pytest
from clyde import Webhook
from clyde.components import ActionRow, Container, MediaGallery, Section, TextDisplay

from core.archive import ZiggyClient
from core.state import StateStore, XCursor
from core.x import WebhookDelivery, XFeed, XInstance, XMedia, XPost, XPostReference

RECEIPT_ID = "00000000-0000-4000-8000-000000000000"
TARGET_URL = "https://nitter.example/user/status/2"


class State:
    def __init__(self):
        self.cursor = XCursor(1, "1")

    def get(self, _state_key, _username):
        return self.cursor

    def set(self, _state_key, _username, cursor, force=False):
        self.cursor = cursor


class Limiter:
    def wait(self):
        pass

    def observe(self, headers):
        pass

    def defer(self, response):
        return 0.0


class Response:
    status_code = 202
    headers = {}

    def raise_for_status(self):
        return self

    def json(self):
        return {"submissions": [{"receipt_id": RECEIPT_ID, "url": TARGET_URL}]}


class Session:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def post(self, url, **kwargs):
        self.events.append("archive")
        self.calls.append((url, kwargs))
        return Response()

    def close(self):
        pass


class Capture:
    def archive_url(self):
        return "https://archive.example/capture/1"


class DirectArchive:
    authenticated = False

    def __init__(self, events):
        self.events = events
        self.targets = []

    def save(self, target, _options):
        self.events.append("archive")
        self.targets.append(target)
        return Capture()


def post():
    return XPost(
        post_id="2",
        url="https://x.com/user/status/2",
        username="user",
        display_name="User",
        created_at=2,
        source="Test",
    )


def configure(instance, item):
    instance.state_key = "test"
    instance.archive_base_url = "https://nitter.example/"
    instance.fetch_user = lambda _username, _cursor: XFeed("user", (item,))


def test_ziggy_queues_once_after_notification_without_archive_button(
    monkeypatch: pytest.MonkeyPatch,
):
    events = []
    session = Session(events)
    client = ZiggyClient("ziggy:9449", "bluebird", session, Limiter())
    item = post()
    instance = XInstance([], cast(StateStore, State()), client)
    configure(instance, item)
    deliveries: tuple[WebhookDelivery, ...] = (
        (cast(Webhook, object()), "first"),
        (cast(Webhook, object()), "second"),
    )
    monkeypatch.setattr(
        instance, "notify", lambda _post: events.append("notify") or deliveries
    )
    archive_buttons = []
    monkeypatch.setattr(
        instance, "add_archive_button", lambda *_args: archive_buttons.append(True)
    )

    instance.watch_user("user")

    assert events == ["notify", "archive"]
    assert len(session.calls) == 1
    assert session.calls[0][1]["json"]["urls"] == [TARGET_URL]
    assert archive_buttons == []


def test_direct_capture_still_adds_archive_button(monkeypatch: pytest.MonkeyPatch):
    events = []
    client = DirectArchive(events)
    item = post()
    instance = XInstance([], cast(StateStore, State()), cast(ZiggyClient, client))
    configure(instance, item)
    deliveries: tuple[WebhookDelivery, ...] = ((cast(Webhook, object()), "message"),)
    monkeypatch.setattr(
        instance, "notify", lambda _post: events.append("notify") or deliveries
    )
    archive_buttons = []
    monkeypatch.setattr(
        instance,
        "add_archive_button",
        lambda received, url: archive_buttons.append((received, url)),
    )

    instance.watch_user("user")

    archive_url = "https://archive.example/capture/1"
    assert events == ["notify", "archive"]
    assert client.targets == [TARGET_URL]
    assert archive_buttons == [(deliveries, archive_url)]


def test_rejected_ziggy_submission_continues_and_advances_cursor(
    monkeypatch: pytest.MonkeyPatch,
):
    events = []
    session = Session(events)
    response = niquests.Response()
    response.status_code = 403
    response._content = b'{"error":"identifier is not allowed"}'

    def reject(_url, **_kwargs):
        events.append("archive")
        return response

    monkeypatch.setattr(session, "post", reject)
    client = ZiggyClient("ziggy:9449", "bluebird", session, Limiter())
    state = State()
    instance = XInstance([], cast(StateStore, state), client)
    configure(instance, post())
    monkeypatch.setattr(instance, "notify", lambda _post: events.append("notify") or ())
    monkeypatch.setattr(
        instance,
        "add_archive_button",
        lambda *_args: pytest.fail("Archive button added after rejected submission"),
    )

    instance.watch_user("user")
    instance.watch_user("user")

    assert events == ["notify", "archive"]
    assert state.cursor == XCursor(2, "2")


@pytest.fixture
def notification_instance(monkeypatch: pytest.MonkeyPatch) -> XInstance:
    class DiscordResponse:
        def json(self):
            return {"id": "message"}

    class DiscordWebhook:
        def __init__(self, **_kwargs):
            self.components = []

        def add_component(self, component):
            self.components.append(component)

        def set_wait(self, _wait):
            pass

        def execute(self):
            return DiscordResponse()

    instance = XInstance([], cast(StateStore, State()))
    instance.webhook_urls = ("https://discord.com/api/webhooks/1/token",)
    monkeypatch.setattr("core.x.Webhook", DiscordWebhook)
    return instance


@pytest.mark.parametrize("relationship", ["reply", "quote", "repost"])
def test_notification_includes_related_post_media(
    relationship: str, notification_instance: XInstance, monkeypatch: pytest.MonkeyPatch
):
    instance = notification_instance
    related = XPost(
        post_id="1",
        url="https://x.com/related/status/1",
        username="related",
        display_name="Related",
        created_at=1,
        source="Twscraper",
        media=(XMedia("https://example.com/media/photo.jpg"),),
    )
    reference = XPostReference(username=related.username, post_id=related.post_id)
    item = XPost(
        post_id="2",
        url="https://x.com/user/status/2",
        username="user",
        display_name="User",
        created_at=2,
        source="Twscraper",
        is_repost=relationship == "repost",
        reply_to=reference if relationship == "reply" else None,
        quote_of=reference if relationship == "quote" else None,
        repost_of=reference if relationship == "repost" else None,
    )
    monkeypatch.setattr(instance, "fetch_post", lambda *_args: related)

    deliveries = instance.notify(item)

    webhook = deliveries[0][0]
    assert isinstance(webhook.components, list)
    container = cast(Container, webhook.components[0])
    assert any(
        isinstance(component, MediaGallery) for component in container.components
    )


@pytest.mark.parametrize(
    ("quote_on_event", "quote_on_repost"), [(True, True), (True, False), (False, True)]
)
def test_reposted_quote_preserves_media_and_post_order(
    quote_on_event: bool,
    quote_on_repost: bool,
    notification_instance: XInstance,
    monkeypatch: pytest.MonkeyPatch,
):
    instance = notification_instance
    original = replace(
        post(),
        post_id="1000000000000000001",
        url="https://x.com/example_studio/status/1000000000000000001",
        username="example_studio",
        display_name="Example Studio",
        text="Our announcement goes here.",
        media=(XMedia("https://pbs.twimg.com/media/example1.jpg"),),
    )
    quote_reference = XPostReference(original.username, original.post_id)
    reposted = replace(
        post(),
        post_id="1000000000000000002",
        url="https://x.com/example_dev/status/1000000000000000002",
        username="example_dev",
        display_name="Example Dev",
        text="Update returns in #example 📸",
        media=(
            XMedia("https://pbs.twimg.com/media/example2.jpg"),
            XMedia("https://pbs.twimg.com/media/example3.png"),
        ),
        is_quote=True,
        quote_of=quote_reference if quote_on_repost else None,
    )
    item = replace(
        original,
        post_id="1000000000000000003",
        url="https://x.com/example_studio/status/1000000000000000003",
        created_at=1700000000,
        text="RT @example_dev: Update returns in #example 📸",
        media=reposted.media,
        is_repost=True,
        is_quote=quote_on_event,
        repost_of=XPostReference(reposted.username, reposted.post_id),
        quote_of=quote_reference if quote_on_event else None,
    )
    posts = {related.post_id: related for related in (original, reposted)}
    monkeypatch.setattr(
        instance, "fetch_post", lambda _username, post_id: posts[post_id]
    )

    deliveries = instance.notify(item)

    webhook = deliveries[0][0]
    assert isinstance(webhook.components, list)
    container = cast(Container, webhook.components[0])
    galleries = [
        component
        for component in container.components
        if isinstance(component, MediaGallery)
    ]
    assert [[media.media.url for media in gallery.items] for gallery in galleries] == [
        [media.url for media in reposted.media],
        [media.url for media in original.media],
    ]
    heads = [
        component
        for component in container.components
        if isinstance(component, Section)
    ]
    assert len(heads) == 3
    for head, username in zip(
        heads, (item.username, reposted.username, original.username)
    ):
        assert f"https://x.com/{username}" in head.components[0].content
    assert (
        container.components.index(heads[1])
        < container.components.index(galleries[0])
        < container.components.index(heads[2])
        < container.components.index(galleries[1])
    )
    text = [
        component.content
        for component in container.components
        if isinstance(component, TextDisplay)
    ]
    assert text[:2] == [
        ">>> Update returns in [#example](https://x.com/hashtag/example) 📸",
        r">>> Our announcement goes here\.",
    ]
    assert len(text) == 3
    assert text[-1].startswith("-# Reposted ")
    assert "1700000000" in text[-1]
    outbound = cast(ActionRow, webhook.components[1])
    assert [button.url for button in outbound.components[:2]] == [
        item.url,
        reposted.url,
    ]
