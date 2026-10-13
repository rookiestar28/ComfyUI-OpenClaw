import json
import unittest
from unittest.mock import MagicMock, patch


class HealthEndpointContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_structure(self):
        """
        Contract: /openclaw/health must return:
        - ok: bool
        - pack: dict (name, version)
        - uptime_sec: float
        - config: dict (provider, model, etc)
        - stats: dict

        Only the provider lookup and the metrics source are replaced, through patches that are
        restored afterwards, so the real handler, dependency wiring and aiohttp response are
        exercised without leaking stubs into other tests in the same process.
        """
        import api.routes

        provider = MagicMock()
        provider.get_provider_summary.return_value = {
            "provider": "openai",
            "model": "gpt-4",
            "key_configured": True,
        }
        metrics = MagicMock()
        metrics.get_snapshot.return_value = {"errors_captured": 0, "logs_processed": 0}
        request = MagicMock()
        request.remote = "127.0.0.1"

        with (
            patch("services.llm_client.LLMClient", return_value=provider),
            patch("services.providers.keys.requires_api_key", return_value=True),
            patch.object(api.routes, "metrics", metrics),
            patch.object(api.routes, "PACK_START_TIME", 1000000),
        ):
            response = await api.routes.health_handler(request)

        payload = json.loads(response.body)
        self.assertIs(payload["ok"], True)
        self.assertIn("name", payload["pack"])
        self.assertIn("version", payload["pack"])
        self.assertIsInstance(payload["uptime_sec"], (int, float))
        self.assertEqual(payload["config"]["provider"], "openai")
        self.assertEqual(payload["config"]["model"], "gpt-4")
        self.assertIn("stats", payload)


if __name__ == "__main__":
    unittest.main()
