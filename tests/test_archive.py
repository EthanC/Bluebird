"""Tests for shared Internet Archive sessions."""

from unittest import TestCase
from unittest.mock import MagicMock, call, patch, sentinel

from archivist import (
    AuthenticationError,
    InternetArchiveAccount,
    InternetArchiveSaveOptions,
)

from core.archive import InternetArchiveSession


class InternetArchiveSessionTests(TestCase):
    """Verify client reuse and authentication recovery."""

    @patch("core.archive.InternetArchiveClient")
    def test_reuses_authenticated_client(self, client_type: MagicMock) -> None:
        """Authenticate once and use the same client for later captures."""
        client = client_type.return_value
        client.submit.side_effect = [sentinel.first_job, sentinel.second_job]
        client.wait.side_effect = [sentinel.first_capture, sentinel.second_capture]
        account = InternetArchiveAccount("user@example.com", "password")
        session = InternetArchiveSession(account)

        self.assertIs(session.save("https://example.com/1"), sentinel.first_capture)
        self.assertIs(session.save("https://example.com/2"), sentinel.second_capture)

        client_type.assert_called_once_with(account=account)
        client.login.assert_called_once_with()
        client.submit.assert_has_calls(
            [
                call("https://example.com/1", InternetArchiveSaveOptions()),
                call("https://example.com/2", InternetArchiveSaveOptions()),
            ]
        )
        client.wait.assert_has_calls(
            [call(sentinel.first_job), call(sentinel.second_job)]
        )

    @patch("core.archive.InternetArchiveClient")
    def test_reauthenticates_after_authentication_error(
        self, client_type: MagicMock
    ) -> None:
        """Replace an expired authenticated client and retry the capture once."""
        failed_client = MagicMock()
        replacement = MagicMock()
        failed_client.submit.return_value = sentinel.job
        failed_client.wait.return_value = sentinel.capture
        failed_client.add_to_my_web_archive.side_effect = AuthenticationError("expired")
        client_type.side_effect = [failed_client, replacement]
        account = InternetArchiveAccount("user@example.com", "password")
        session = InternetArchiveSession(account)
        options = InternetArchiveSaveOptions(save_to_archive=True)

        self.assertIs(session.save("https://example.com/", options), sentinel.capture)

        self.assertEqual(client_type.call_count, 2)
        failed_client.login.assert_called_once_with()
        replacement.login.assert_called_once_with()
        failed_client.submit.assert_called_once_with("https://example.com/", options)
        failed_client.wait.assert_called_once_with(sentinel.job)
        failed_client.add_to_my_web_archive.assert_called_once_with(sentinel.capture)
        replacement.submit.assert_not_called()
        replacement.wait.assert_not_called()
        replacement.add_to_my_web_archive.assert_called_once_with(sentinel.capture)

        session.close()
        failed_client.close.assert_called_once_with()
        replacement.close.assert_called_once_with()

    @patch("core.archive.InternetArchiveClient")
    def test_does_not_reauthenticate_anonymous_client(
        self, client_type: MagicMock
    ) -> None:
        """Propagate authentication errors when no account can refresh them."""
        client = client_type.return_value
        client.submit.side_effect = AuthenticationError("credentials required")
        session = InternetArchiveSession()

        with self.assertRaises(AuthenticationError):
            session.save("https://example.com/")

        client_type.assert_called_once_with(account=None)
        client.login.assert_not_called()

    @patch("core.archive.InternetArchiveClient")
    def test_does_not_reauthenticate_after_other_errors(
        self, client_type: MagicMock
    ) -> None:
        """Keep authenticated state when a capture fails for another reason."""
        client = client_type.return_value
        client.submit.side_effect = RuntimeError("capture failed")
        account = InternetArchiveAccount("user@example.com", "password")
        session = InternetArchiveSession(account)

        with self.assertRaises(RuntimeError):
            session.save("https://example.com/")

        client_type.assert_called_once_with(account=account)
        client.login.assert_called_once_with()

    @patch("core.archive.InternetArchiveClient")
    def test_closes_reused_client_once(self, client_type: MagicMock) -> None:
        """Allow repeated shutdown without closing a client more than once."""
        client = client_type.return_value
        session = InternetArchiveSession()
        session.save("https://example.com/")

        session.close()
        session.close()

        client.close.assert_called_once_with()
