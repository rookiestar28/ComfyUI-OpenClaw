"""
Unit tests for F33 LINE Image Delivery.
Tests MediaStore logic and LINE Adapter image sending.
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.media_response import (
    build_connector_media_response,
    is_dangerous_content_type,
)
from connector.media_store import MediaStore
from connector.platforms.line_webhook import LINEWebhookServer
from connector.platforms.whatsapp_webhook import WhatsAppWebhookServer
from connector.router import CommandRouter


class _FakeRequest:
    def __init__(self, token: str):
        self.match_info = {"token": token}


class _FakeWeb:
    class Response:
        def __init__(self, status=200, text=""):
            self.status = status
            self.text = text
            self.headers = {}

    class FileResponse:
        def __init__(self, path, headers=None):
            self.status = 200
            self.path = Path(path)
            self.headers = headers or {}


class TestMediaStore(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config = ConnectorConfig()
        # media_path is for URL, not FS
        self.config.media_path = "/media"
        self.config.media_ttl_sec = 2
        self.config.media_max_mb = 1
        # Use storage_path to test FS ops in tmp_dir
        self.store = MediaStore(self.config, storage_path=Path(self.tmp_dir))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_store_and_get_image(self):
        """Should store image and return valid path via token."""
        data = b"fake_image_bytes"
        token = self.store.store_image(data, ".png", "channel1")

        path = self.store.get_image_path(token)
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        with open(path, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_token_expiry(self):
        """Should reject expired tokens."""
        data = b"image"
        # Manually create tokens with past expiry
        filename = "test.png"
        expiry = int(time.time()) - 10
        token = self.store._generate_token(filename, "ch1", expiry)

        # Ensure file exists so checks pass up to logical expiry
        (Path(self.tmp_dir) / filename).touch()

        path = self.store.get_image_path(token)
        self.assertIsNone(path)

    def test_cleanup(self):
        """Should remove expired files."""
        # Create an "old" file
        old_file = Path(self.tmp_dir) / "old.png"
        old_file.touch()
        # Set mtime to past (TTL + buffer + extra)
        past = time.time() - self.config.media_ttl_sec - 100
        os.utime(old_file, (past, past))

        # Create a "new" file
        new_file = Path(self.tmp_dir) / "new.png"
        new_file.touch()

        self.store.cleanup()

        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())

    def test_size_limit(self):
        """Should raise error if image is too large."""
        self.config.media_max_mb = 0  # Zero MB
        with self.assertRaises(ValueError):
            self.store.store_image(b"123", ".png", "ch1")


class TestConnectorMediaResponse(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def _write_file(self, name: str, data: bytes = b"payload") -> Path:
        path = Path(self.tmp_dir) / name
        path.write_bytes(data)
        return path

    def test_dangerous_content_type_normalization(self):
        for content_type in (
            "text/html; charset=utf-8",
            "TEXT/HTML",
            "image/svg+xml; charset=utf-8",
            "application/rss+xml",
            "application/xml",
            "message/rfc822",
        ):
            self.assertTrue(is_dangerous_content_type(content_type), content_type)

        for content_type in ("image/png", "image/jpeg", "image/webp", "text/plain"):
            self.assertFalse(is_dangerous_content_type(content_type), content_type)

    def test_dangerous_media_forces_attachment_octet_stream_and_nosniff(self):
        for filename in (
            "evil.html",
            "evil.svg",
            "evil.js",
            "evil.css",
            "evil.xml",
        ):
            with self.subTest(filename=filename):
                response = build_connector_media_response(
                    _FakeWeb, self._write_file(filename)
                )

                self.assertEqual(
                    response.headers["Content-Type"], "application/octet-stream"
                )
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn(
                    "attachment", response.headers["Content-Disposition"].lower()
                )

    def test_safe_images_remain_inline_compatible_with_nosniff(self):
        for filename, expected_type in (
            ("safe.png", "image/png"),
            ("safe.jpg", "image/jpeg"),
            ("safe.webp", "image/webp"),
            ("safe.gif", "image/gif"),
        ):
            with self.subTest(filename=filename):
                response = build_connector_media_response(
                    _FakeWeb, self._write_file(filename)
                )

                self.assertEqual(response.headers["Content-Type"], expected_type)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertNotIn(
                    "attachment", response.headers["Content-Disposition"].lower()
                )
                self.assertIn("filename=", response.headers["Content-Disposition"])

    def test_content_disposition_filename_is_escaped(self):
        response = build_connector_media_response(
            _FakeWeb, Path(self.tmp_dir) / 'bad"name.svg'
        )

        self.assertIn(
            r'filename="bad\"name.svg"', response.headers["Content-Disposition"]
        )


class TestLINESendImage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.config = ConnectorConfig()
        self.config.state_path = Path(self._temp_dir.name) / "state.json"
        self.config.line_channel_secret = "secret"
        self.config.line_channel_access_token = "token"

        self.router = MagicMock(spec=CommandRouter)
        self.server = LINEWebhookServer(self.config, self.router)

        # Mock MediaStore to be independent of FS
        self.server.media_store = MagicMock()
        self.server.media_store.store_image.return_value = "mock_token.sig"
        self.server.media_store.build_preview.return_value = None

        self.server.session = MagicMock()
        self.server.session.post.return_value.__aenter__.return_value.status = 200

    def tearDown(self):
        self._temp_dir.cleanup()

    async def test_send_image_fallback(self):
        """Should send text fallback if public_base_url is missing."""
        self.config.public_base_url = None
        self.server.send_message = AsyncMock()

        await self.server.send_image("ch1", b"data")

        self.server.send_message.assert_called_once()
        args = self.server.send_message.call_args[0]
        self.assertIn("cannot be delivered", args[1])

    async def test_send_image_success(self):
        """Should upload image and send payload if public_base_url is set."""
        self.config.public_base_url = "https://example.com"
        self.server._send_line_image_payload = AsyncMock()

        await self.server.send_image("ch1", b"data", "foo.png")

        self.server.media_store.store_image.assert_called_once()
        self.server._send_line_image_payload.assert_called_once()

        url = self.server._send_line_image_payload.call_args[0][1]

        # URL = public_base_url / media_path / token
        # defaults: media_path="/media"
        self.assertEqual(url, "https://example.com/media/mock_token.sig")


class TestConnectorMediaRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config = ConnectorConfig()
        self.config.state_path = Path(self.tmp_dir) / "state.json"
        self.router = MagicMock(spec=CommandRouter)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def _write_file(self, name: str, data: bytes = b"payload") -> Path:
        path = Path(self.tmp_dir) / name
        path.write_bytes(data)
        return path

    async def test_line_media_route_hardens_dangerous_media_after_token_validation(
        self,
    ):
        server = LINEWebhookServer(self.config, self.router)
        server.media_store = MagicMock()
        server.media_store.get_image_path.return_value = self._write_file("evil.svg")

        with patch(
            "connector.platforms.line_webhook._import_aiohttp_web",
            return_value=(None, _FakeWeb),
        ):
            response = await server._handle_media_request(_FakeRequest("signed.token"))

        server.media_store.get_image_path.assert_called_once_with("signed.token")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response.headers["Content-Disposition"].lower())
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    async def test_whatsapp_media_route_preserves_safe_image_delivery(self):
        server = WhatsAppWebhookServer(self.config, self.router)
        server.media_store = MagicMock()
        server.media_store.get_image_path.return_value = self._write_file("safe.png")

        with patch(
            "connector.platforms.whatsapp_webhook._import_aiohttp_web",
            return_value=(None, _FakeWeb),
        ):
            response = await server._handle_media_request(_FakeRequest("signed.token"))

        server.media_store.get_image_path.assert_called_once_with("signed.token")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertNotIn("attachment", response.headers["Content-Disposition"].lower())
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    async def test_media_routes_keep_invalid_tokens_fail_closed(self):
        for server_cls, patch_target in (
            (
                LINEWebhookServer,
                "connector.platforms.line_webhook._import_aiohttp_web",
            ),
            (
                WhatsAppWebhookServer,
                "connector.platforms.whatsapp_webhook._import_aiohttp_web",
            ),
        ):
            with self.subTest(server=server_cls.__name__):
                server = server_cls(self.config, self.router)
                server.media_store = MagicMock()
                server.media_store.get_image_path.return_value = None

                with patch(patch_target, return_value=(None, _FakeWeb)):
                    response = await server._handle_media_request(
                        _FakeRequest("expired.token")
                    )

                server.media_store.get_image_path.assert_called_once_with(
                    "expired.token"
                )
                self.assertEqual(response.status, 404)
                self.assertEqual(response.text, "Media Not Found or Expired")


if __name__ == "__main__":
    unittest.main()
