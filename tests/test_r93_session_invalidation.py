import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.platforms.line_webhook import LINEWebhookServer
from connector.platforms.wechat_webhook import WeChatWebhookServer
from connector.platforms.whatsapp_webhook import WhatsAppWebhookServer
from connector.router import CommandRouter
from connector.transport_contract import RelayResponseClassifier, RelayStatus


class TestRelayResponseClassifier(unittest.TestCase):
    def test_auth_invalid_codes(self):
        """Test that 401 and 410 are classified as auth_invalid."""
        print(f"DEBUG: classify(401) = {RelayResponseClassifier.classify(401)}")
        print(
            f"DEBUG: AUTH_INVALID_CODES = {RelayResponseClassifier.AUTH_INVALID_CODES}"
        )
        self.assertTrue(RelayResponseClassifier.is_auth_invalid(401))
        self.assertTrue(RelayResponseClassifier.is_auth_invalid(410))
        self.assertEqual(
            RelayResponseClassifier.classify(401), RelayStatus.AUTH_INVALID
        )
        self.assertEqual(
            RelayResponseClassifier.classify(410), RelayStatus.AUTH_INVALID
        )

    def test_other_codes(self):
        """Test that other codes are not auth_invalid."""
        self.assertFalse(RelayResponseClassifier.is_auth_invalid(200))
        self.assertFalse(
            RelayResponseClassifier.is_auth_invalid(403)
        )  # Forbidden != Auth Invalid (usually)
        self.assertFalse(RelayResponseClassifier.is_auth_invalid(500))
        # Use .value to avoid enum identity issues if any
        self.assertEqual(
            RelayResponseClassifier.classify(200).value, RelayStatus.OK.value
        )
        # 500 is TRANSIENT in OpenClaw (retryable), not SERVER_ERROR (fatal)
        self.assertEqual(
            RelayResponseClassifier.classify(500).value, RelayStatus.TRANSIENT.value
        )


class TestWebhookSessionInvalidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.config = ConnectorConfig(
            state_path=Path(self._temp_dir.name) / "state.json"
        )

    def tearDown(self):
        self._temp_dir.cleanup()

    async def test_whatsapp_session_invalidation(self):
        """Test WhatsApp session invalidation on 401."""
        server = WhatsAppWebhookServer(
            config=self.config, router=MagicMock(spec=CommandRouter)
        )
        server.session = MagicMock()

        # Mock response context manager
        mock_response = AsyncMock()
        mock_response.status = 401

        # Mock post context manager
        post_ctx = AsyncMock()
        post_ctx.__aenter__.return_value = mock_response
        server.session.post.return_value = post_ctx

        # First call should trigger invalidation
        await server.send_message("123", "test")
        self.assertTrue(server._session_invalid)

        # Reset mock to verify no further calls
        server.session.post.reset_mock()

        # Second call should be blocked
        await server.send_message("123", "test")
        server.session.post.assert_not_called()

    async def test_line_session_invalidation(self):
        """Test LINE session invalidation on 401."""
        server = LINEWebhookServer(
            config=self.config, router=MagicMock(spec=CommandRouter)
        )
        server.session = MagicMock()

        mock_response = AsyncMock()
        mock_response.status = 401

        post_ctx = AsyncMock()
        post_ctx.__aenter__.return_value = mock_response
        server.session.post.return_value = post_ctx

        # Mock _get_header to avoid errors
        server._get_header = MagicMock(return_value={})

        await server._reply_message("token", "test")
        self.assertTrue(server._session_invalid)

        server.session.post.reset_mock()
        await server._reply_message("token", "test")
        server.session.post.assert_not_called()

    async def test_wechat_session_invalidation(self):
        """Test WeChat session invalidation on 401 (token fetch)."""
        self.config.wechat_app_id = "test-app"
        self.config.wechat_app_secret = "test-secret"
        server = WeChatWebhookServer(
            config=self.config, router=MagicMock(spec=CommandRouter)
        )
        server.session = MagicMock()

        # Mock token fetch response
        mock_response = AsyncMock()
        mock_response.status = 401

        get_ctx = AsyncMock()
        get_ctx.__aenter__.return_value = mock_response
        server.session.get.return_value = get_ctx

        token = await server._get_access_token()
        self.assertIsNone(token)
        self.assertTrue(server._session_invalid)

        # Reset mock and try again
        server.session.get.reset_mock()

        # Second call should be blocked by _session_invalid check (which must be inside access_token logic)
        # Wait - _get_access_token doesn't check _session_invalid at the start?
        # Let's check implementation. If not, we should better add it or test assumes it.
        # But wait, self._session_invalid = True was set.
        # If the implementation doesn't check it at start of _get_access_token, it will try to fetch again?
        # The test expects assert_not_called.

        # Let's enforce it in the test by ensuring the method respects the flag OR
        # duplicate the session invalidation check in _get_access_token (which is smart).

        # If we didn't add the check in _get_access_token, let's just assert that it Returns None immediately?
        # Actually proper R93 implies *locking* the connector.

        # Let's see... if I look at the previous step, I only added the classification logic.
        # I did not add "if self._session_invalid: return None" at the top of _get_access_token.
        # So it probably TRIED to fetch again.

        # We should probably add that check to _get_access_token too for completeness of R93.
        # But for now let's fix the test expectation if we want to rely on the *result* being None
        # (and maybe it fetches again? No, we want to STOP traffic).

        # Let's update the test to be realistic or update the code.
        # R93 says "Connector platforms should stop retrying".
        # So _get_access_token SHOULD check the flag.

        # I will update the code in next step. For now let's update test to expect it to be blocked.
        token2 = await server._get_access_token()
        server.session.get.assert_not_called()
        self.assertIsNone(token2)


if __name__ == "__main__":
    unittest.main()
