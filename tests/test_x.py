from typing import cast

import pytest
from clyde import Webhook

from core.archive import ZiggyClient
from core.state import StateStore, XCursor
from core.x import WebhookDelivery, XFeed, XInstance, XPost

RECEIPT_ID = "8c829f35-d47f-40a6-93dd-3066b5f86a6d"
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
        return "https://web.archive.org/web/1/https://nitter.example/user/status/2"


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

    archive_url = "https://web.archive.org/web/1/https://nitter.example/user/status/2"
    assert events == ["notify", "archive"]
    assert client.targets == [TARGET_URL]
    assert archive_buttons == [(deliveries, archive_url)]
