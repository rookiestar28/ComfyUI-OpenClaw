"""
Tests for WhatsApp Webhook Server (F36).
"""

import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.contract import CommandRequest
from connector.platforms.whatsapp_webhook import WhatsAppWebhookServer


class TestWhatsAppWebhook(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.config = ConnectorConfig()
        self.config.state_path = Path(self._temp_dir.name) / "state.json"
        self.config.whatsapp_access_token = "dummy_access"
        self.config.whatsapp_verify_token = "dummy_verify"
        self.config.whatsapp_app_secret = "secret123"
        self.config.whatsapp_allowed_users = ["123456789"]

        self.router = MagicMock()
        self.router.handle = AsyncMock()

        self.server = WhatsAppWebhookServer(self.config, self.router)

    def tearDown(self):
        self._temp_dir.cleanup()

    def _sign(self, body):
        secret = b"secret123"
        sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return f"sha256={sig}"

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_verify_webhook_success(self, mock_import):
        """Test GET verification handshake."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        def side_effect(text=None, **kwargs):
            m = MagicMock()
            m.text = text
            m.status = kwargs.get("status", 200)
            return m

        mock_web.Response.side_effect = side_effect

        request = MagicMock()
        request.query = {
            "hub.mode": "subscribe",
            "hub.verify_token": "dummy_verify",
            "hub.challenge": "1234",
        }

        resp = await self.server.handle_verify(request)
        self.assertEqual(resp.text, "1234")

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_verify_webhook_fail(self, mock_import):
        """Test GET verification failure."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        def side_effect(text=None, **kwargs):
            m = MagicMock()
            m.text = text
            m.status = kwargs.get("status", 200)
            return m

        mock_web.Response.side_effect = side_effect

        request = MagicMock()
        request.query = {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "1234",
        }

        resp = await self.server.handle_verify(request)
        # Should return 403 Forbidden
        mock_web.Response.assert_called_with(status=403, text="Verification failed")

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_handle_message(self, mock_import):
        """Test POST message handling (Happy Path)."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        def side_effect(text=None, **kwargs):
            m = MagicMock()
            m.text = text
            m.status = kwargs.get("status", 200)
            return m

        mock_web.Response.side_effect = side_effect

        now_ts = int(time.time())
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": "123456789",
                                        "id": "msg_id_fresh",
                                        "timestamp": str(now_ts),
                                        "type": "text",
                                        "text": {"body": "/run check"},
                                    }
                                ],
                                "contacts": [
                                    {"wa_id": "123456789", "profile": {"name": "Alice"}}
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")

        request = MagicMock()
        request.read = AsyncMock(return_value=body)
        request.headers = {}
        request.headers["X-Hub-Signature-256"] = self._sign(body)

        await self.server.handle_webhook(request)

        # Verify router called
        self.router.handle.assert_called_once()
        args = self.router.handle.call_args[0][0]
        self.assertEqual(args.sender_id, "123456789")

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_handle_message_replay_old(self, mock_import):
        """Test Replay Protection: Stale Timestamp."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        old_ts = int(time.time()) - 400  # > 300s window
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": "123456789",
                                        "id": "msg_id_old",
                                        "timestamp": str(old_ts),
                                        "type": "text",
                                        "text": {"body": "/run check"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = MagicMock()
        request.read = AsyncMock(return_value=body)
        request.headers = {}
        request.headers["X-Hub-Signature-256"] = self._sign(body)

        with self.assertLogs(
            "connector.platforms.whatsapp_webhook", level="WARNING"
        ) as cm:
            await self.server.handle_webhook(request)
            self.assertTrue(any("Replay rejected" in o for o in cm.output))

        self.router.handle.assert_not_called()

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_handle_message_replay_duplicate(self, mock_import):
        """Test Replay Protection: Duplicate Message ID."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        now_ts = int(time.time())
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": "123456789",
                                        "id": "msg_id_dup",
                                        "timestamp": str(now_ts),
                                        "type": "text",
                                        "text": {"body": "/run check"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = MagicMock()
        request.read = AsyncMock(return_value=body)
        request.headers = {}
        request.headers["X-Hub-Signature-256"] = self._sign(body)

        # First call: Success
        await self.server.handle_webhook(request)
        self.assertEqual(self.router.handle.call_count, 1)

        # Second call: Fail (Duplicate)
        with self.assertLogs(
            "connector.platforms.whatsapp_webhook", level="WARNING"
        ) as cm:
            await self.server.handle_webhook(request)
            self.assertTrue(any("Replay rejected" in o for o in cm.output))

        self.assertEqual(self.router.handle.call_count, 1)  # Still 1

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_untrusted_user_logging(self, mock_import):
        """Test logging for user not in allowlist."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        # Configure allowlist
        self.config.whatsapp_allowed_users = ["999999"]  # Sender is not 999999
        # Re-init server to pick up new allowlist policy (which snapshots config)
        self.server = WhatsAppWebhookServer(self.config, self.router)

        now_ts = int(time.time())
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": "123456789",  # Untrusted
                                        "id": "msg_id_untrusted",
                                        "timestamp": str(now_ts),
                                        "type": "text",
                                        "text": {"body": "/run check"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = MagicMock()
        request.read = AsyncMock(return_value=body)
        request.headers = {}
        request.headers["X-Hub-Signature-256"] = self._sign(body)

        with self.assertLogs(
            "connector.platforms.whatsapp_webhook", level="WARNING"
        ) as cm:
            await self.server.handle_webhook(request)
            self.assertTrue(any("Untrusted WhatsApp message" in o for o in cm.output))

        # Router is STILL called (router enforces permissions)
        self.router.handle.assert_called_once()

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_handle_message_bad_signature(self, mock_import):
        """Test POST message with bad signature."""
        mock_web = MagicMock()
        mock_import.return_value = (MagicMock(), mock_web)

        request = MagicMock()
        request.read = AsyncMock(return_value=b"{}")
        request.headers = {"X-Hub-Signature-256": "sha256=bad_sig"}

        await self.server.handle_webhook(request)

        mock_web.Response.assert_called_with(status=401, text="Invalid Signature")
        self.router.handle.assert_not_called()

    @patch("connector.platforms.whatsapp_webhook._import_aiohttp_web")
    async def test_send_message(self, mock_import):
        """Test outbound message sending."""
        mock_aiohttp = MagicMock()
        mock_import.return_value = (mock_aiohttp, MagicMock())

        # Mock session post
        mock_session = MagicMock()
        post_ctx = AsyncMock()
        post_ctx.__aenter__.return_value.status = 200
        mock_session.post.return_value = post_ctx
        self.server.session = mock_session

        self.config.whatsapp_phone_number_id = "pid"

        await self.server.send_message("123456789", "Hello")

        mock_session.post.assert_called_once()
        url = mock_session.post.call_args[0][0]
        self.assertIn("/pid/messages", url)

        kwargs = mock_session.post.call_args[1]
        self.assertEqual(kwargs["json"]["to"], "123456789")
        self.assertEqual(kwargs["json"]["text"]["body"], "Hello")


if __name__ == "__main__":
    unittest.main()
