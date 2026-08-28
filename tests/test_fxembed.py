"""Tests for FxEmbed-compatible timeline processing."""

from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from core.fxembed import FxEmbed
from core.state import XCursor
from core.x import XInstance


class FxEmbedTimelineTests(TestCase):
    """Verify profile timeline normalization and pagination."""

    def test_excludes_conversation_context_without_stopping_pagination(self) -> None:
        """Keep only the requested profile's posts and paginate to its cursor."""
        first_page = MagicMock(status_code=200)
        first_page.json.return_value = {
            "results": [
                {
                    "type": "status",
                    "id": "2093410282999591197",
                    "created_timestamp": 90,
                    "author": {"screen_name": "Lionxo_", "name": "Lion"},
                    "replying_to": None,
                },
                {
                    "type": "status",
                    "id": "2093421939238867432",
                    "created_timestamp": 110,
                    "author": {"screen_name": "CallofDuty", "name": "Call of Duty"},
                    "replying_to": {
                        "screen_name": "Lionxo_",
                        "status": "2093410282999591197",
                    },
                },
            ],
            "cursor": {"bottom": "next-page"},
        }
        second_page = MagicMock(status_code=200)
        second_page.json.return_value = {
            "results": [
                {
                    "type": "status",
                    "id": "2093000000000000000",
                    "created_timestamp": 100,
                    "author": {"screen_name": "CallofDuty", "name": "Call of Duty"},
                    "replying_to": None,
                }
            ],
            "cursor": {"bottom": None},
        }
        source = FxEmbed(MagicMock())
        cursor = XCursor(100, "2093000000000000000")

        with patch.object(
            source, "_request_timeline", side_effect=[first_page, second_page]
        ) as request_timeline:
            feed = source._fetch_user("CallofDuty", cursor)

        self.assertEqual(feed.username, "CallofDuty")
        self.assertEqual(
            [post.post_id for post in feed.posts],
            ["2093421939238867432", "2093000000000000000"],
        )
        self.assertTrue(feed.posts[0].is_reply)
        reply_to = feed.posts[0].reply_to

        if reply_to is None:
            self.fail("Expected reply metadata")

        self.assertEqual(reply_to.post_id, "2093410282999591197")
        instance = XInstance((), MagicMock())
        instance.exclude_reply = True
        self.assertEqual(instance.filter_reason(feed.posts[0]), "replies excluded")
        request_timeline.assert_has_calls(
            [call("CallofDuty", cursor, None), call("CallofDuty", cursor, "next-page")]
        )
