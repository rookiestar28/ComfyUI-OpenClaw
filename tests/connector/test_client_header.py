import unittest

from connector.config import ConnectorConfig
from connector.openclaw_client import OpenClawClient


class TestClientHeader(unittest.IsolatedAsyncioTestCase):
    async def test_admin_header_present(self):
        """Verify X-OpenClaw-Admin-Token is set when config has token."""
        config = ConnectorConfig()
        config.admin_token = "my-secret-token"

        client = OpenClawClient(config)
        self.assertIn("X-OpenClaw-Admin-Token", client.headers)
        self.assertEqual(client.headers["X-OpenClaw-Admin-Token"], "my-secret-token")

    async def test_admin_header_absent(self):
        """Verify X-OpenClaw-Admin-Token is NOT set when token is empty."""
        config = ConnectorConfig()
        config.admin_token = ""

        client = OpenClawClient(config)
        self.assertNotIn("X-OpenClaw-Admin-Token", client.headers)


if __name__ == "__main__":
    unittest.main()
