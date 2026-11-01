"""
Tests for R61 Webhook Adoption.
Verifies that webhook_handler returns R61-compliant error responses.
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.errors import ErrorCode
from api.webhook import webhook_handler


class TestR61WebhookAdoption(unittest.IsolatedAsyncioTestCase):

    async def test_rate_limit_error(self):
        """Test rate limit error uses R61 contract."""
        with patch("api.webhook.check_rate_limit", return_value=False):
            request = MagicMock()

            with patch("aiohttp.web.json_response") as mock_json_response:
                await webhook_handler(request)

                mock_json_response.assert_called_once()
                args, kwargs = mock_json_response.call_args
                body = args[0]
                status = kwargs["status"]

                self.assertEqual(status, 429)
                self.assertFalse(body["ok"])
                self.assertEqual(body["code"], "rate_limit_exceeded")

    async def test_content_type_error(self):
        """Test content type error."""
        with patch("api.webhook.check_rate_limit", return_value=True):
            request = MagicMock()
            request.headers.get.return_value = "text/plain"

            with patch("aiohttp.web.json_response") as mock_json_response:
                await webhook_handler(request)

                args, kwargs = mock_json_response.call_args
                body = args[0]
                status = kwargs["status"]

                self.assertEqual(status, 415)
                self.assertEqual(body["code"], "unsupported_media_type")

    async def test_auth_failed(self):
        """Test auth failure."""
        with patch("api.webhook.check_rate_limit", return_value=True):
            with patch(
                "api.webhook.require_auth", return_value=(False, "auth_failed_test")
            ):
                request = MagicMock()
                request.headers.get.return_value = "application/json"
                # A finite request body ends at EOF; repeating bytes forever is not
                # an HTTP stream and would correctly exceed the ingestion limit.
                request.content.read = AsyncMock(side_effect=[b"{}", b""])

                # Mock MAX_BODY_SIZE if needed during read?
                # Real module is imported, so it uses real Int. That's fine.

                with patch("aiohttp.web.json_response") as mock_json_response:
                    await webhook_handler(request)

                    args, kwargs = mock_json_response.call_args
                    body = args[0]
                    status = kwargs["status"]

                    self.assertEqual(status, 401)
                    self.assertEqual(body["code"], ErrorCode.AUTH_FAILED)
                    self.assertEqual(body["message"], "auth_failed_test")


if __name__ == "__main__":
    unittest.main()
