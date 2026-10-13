"""
Tests for Sidecar Bridge Client (F46).
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from services.sidecar.bridge_client import BridgeClient


class TestBridgeClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = BridgeClient("http://bridge.com", "token", "worker1")

    async def test_headers(self):
        """Test authentication headers."""
        with patch("aiohttp.ClientSession") as mock_session:
            await self.client.start()
            mock_session.assert_called_with(
                headers={
                    "Authorization": "Bearer token",
                    "X-Worker-ID": "worker1",
                    "User-Agent": "OpenClaw-Sidecar/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )

    async def test_fetch_jobs_empty(self):
        """Test polling when no jobs."""
        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 204

            mock_session = MagicMock()
            mock_session.get.return_value.__aenter__.return_value = mock_resp
            mock_session_cls.return_value = mock_session

            self.client.session = mock_session  # inject manually or use start

            jobs = await self.client.fetch_jobs()
            self.assertEqual(jobs, [])

    async def test_fetch_jobs_data(self):
        """Test polling with jobs."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"jobs": [{"id": "1"}]}

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp

        self.client.session = mock_session

        jobs = await self.client.fetch_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "1")

    async def test_submit_result(self):
        """Test result submission."""
        mock_resp = AsyncMock()
        mock_resp.status = 200

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        self.client.session = mock_session

        ok = await self.client.submit_result("job1", {"output": "data"})
        self.assertTrue(ok)
        mock_session.post.assert_called_once()
        args, kwargs = mock_session.post.call_args
        self.assertEqual(kwargs["json"], {"output": "data"})


if __name__ == "__main__":
    unittest.main()
