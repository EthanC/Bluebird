"""Tests for serialized Internet Archive captures."""

from threading import Event, Thread
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from core.archive import InternetArchiveSession


class InternetArchiveSessionTests(TestCase):
    """Verify capture jobs run sequentially in submission order."""

    @patch("core.archive.InternetArchiveClient")
    def test_save_queues_complete_capture_lifecycles_in_order(
        self, client_class: MagicMock
    ) -> None:
        """Do not submit a capture until the preceding capture has finished."""
        client = client_class.return_value
        first_waiting = Event()
        release_first = Event()
        first_capture = MagicMock()
        second_capture = MagicMock()
        client.submit.side_effect = ["first-job", "second-job"]

        def wait(job: str) -> MagicMock:
            if job == "first-job":
                first_waiting.set()
                if not release_first.wait(1):
                    raise TimeoutError("test did not release the first capture")
                return first_capture

            return second_capture

        client.wait.side_effect = wait
        options = MagicMock(save_to_archive=False)
        session = InternetArchiveSession()
        results: dict[str, object] = {}
        first = Thread(
            target=lambda: results.setdefault("first", session.save("first", options))
        )
        second = Thread(
            target=lambda: results.setdefault("second", session.save("second", options))
        )

        first.start()
        self.assertTrue(first_waiting.wait(1))
        second.start()

        with session._save_condition:
            self.assertTrue(
                session._save_condition.wait_for(
                    lambda: len(session._save_queue) == 2, timeout=1
                )
            )

        self.assertEqual(client.submit.call_args_list, [call("first", options)])
        release_first.set()
        first.join(1)
        second.join(1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(
            client.submit.call_args_list,
            [call("first", options), call("second", options)],
        )
        self.assertEqual(results, {"first": first_capture, "second": second_capture})

    @patch("core.archive.InternetArchiveClient")
    def test_failed_save_releases_next_queued_capture(
        self, client_class: MagicMock
    ) -> None:
        """Continue processing queued captures after an earlier one fails."""
        client = client_class.return_value
        client.submit.side_effect = [RuntimeError("capture failed"), "second-job"]
        second_capture = MagicMock()
        client.wait.return_value = second_capture
        options = MagicMock(save_to_archive=False)
        session = InternetArchiveSession()

        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            session.save("first", options)

        self.assertIs(session.save("second", options), second_capture)
        self.assertEqual(
            client.submit.call_args_list,
            [call("first", options), call("second", options)],
        )
