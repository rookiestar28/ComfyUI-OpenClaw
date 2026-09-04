"""
R68: Integration-level E2E Minimal Suite (WP3).

Verifies the critical path `webhook -> preflight -> queue -> callback`
under real service wiring without heavy ComfyUI runtime dependencies.

Scope:
- Real `aiohttp` app hosting `webhook_submit_handler`
- Real auth middleware (S2)
- Real payload mapping engine (F40)
- Real idempotency store (R3)
- Real template rendering service (F5) (with in-memory manifest)
- Real queue submission logic (F5/R33)
- Mocked UPSTREAM ComfyUI `/prompt` endpoint (network boundary)

Confirms that all services are correctly wired and data flows from
webhook input to queue submission and callback scheduling.
"""

import asyncio
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# Import dependencies (using top-level names for test environment)
from api.webhook_submit import webhook_submit_handler
from services.templates import TemplateService, get_template_service

# Setup logging
logging.basicConfig(level=logging.ERROR)


class TestR68IntegrationFlow(AioHTTPTestCase):
    """R68 Integration Flow Test Suite."""

    async def get_application(self):
        """Build the real application with the webhook route."""
        app = web.Application()
        # Register the handler directly
        app.router.add_post("/openclaw/webhook/submit", webhook_submit_handler)
        return app

    def setUp(self):
        super().setUp()
        self.fixtures_dir = os.path.abspath("tests/fixtures")
        os.makedirs(self.fixtures_dir, exist_ok=True)

        # Manifest file (implicit name in service is "manifest.json")
        self.manifest_path = os.path.join(self.fixtures_dir, "manifest.json")

        self.manifest_data = {
            "templates": {
                "r68-test": {
                    "path": "r68-test.json",
                    "version": 1,
                    "defaults": {"seed": 42, "positive_prompt": "test"},
                }
            },
            "version": 1,
        }
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest_data, f)

        # Template file
        self.template_file_path = os.path.join(self.fixtures_dir, "r68-test.json")
        with open(self.template_file_path, "w") as f:
            workflow = {
                "3": {
                    "inputs": {"seed": "{{seed}}", "text": "{{positive_prompt}}"},
                    "class_type": "KSampler",
                }
            }
            json.dump(workflow, f)

        self.patchers = []

        # Mock auth environment
        self.env = {
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "r68-secret",
            "OPENCLAW_COMFYUI_URL": "http://mock-upstream:8188",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0",
        }
        self.env_patcher = patch.dict(os.environ, self.env)
        self.env_patcher.start()
        self.patchers.append(self.env_patcher)

        # Reset TemplateService singleton
        import services.templates

        services.templates._SERVICE = None
        services.templates.TemplateService._instance = None

        # Patch TEMPLATES_ROOT to verify disk lookup
        self.root_patcher = patch(
            "services.templates.TEMPLATES_ROOT", self.fixtures_dir
        )
        self.root_patcher.start()
        self.patchers.append(self.root_patcher)

        # Force re-init with explicit root (bypass stale default arg)
        services.templates.TemplateService._instance = (
            services.templates.TemplateService(templates_root=self.fixtures_dir)
        )

        # Patch COMFYUI_URL to point to mock upstream (module-level constant)
        self.url_patcher = patch(
            "services.queue_submit.COMFYUI_URL", "http://mock-upstream:8188"
        )
        self.url_patcher.start()
        self.patchers.append(self.url_patcher)

        # Mock Callback Delivery `start_callback_watch`
        self.callback_patcher = patch(
            "api.webhook_submit.start_callback_watch", new_callable=AsyncMock
        )
        self.mock_callback = self.callback_patcher.start()
        self.patchers.append(self.callback_patcher)

    def tearDown(self):
        super().tearDown()
        for p in reversed(self.patchers):
            p.stop()

        # Cleanup setup files
        if os.path.exists(self.manifest_path):
            os.remove(self.manifest_path)
        if os.path.exists(self.template_file_path):
            os.remove(self.template_file_path)

    @unittest_run_loop
    async def test_webhook_full_flow_success(self):
        """
        Verify success flow:
        Auth -> Normalize -> Template -> Queue (Mocked Upstream) -> Callback
        """
        import hashlib
        import hmac

        # 1. Prepare Payload
        payload = {
            "template_id": "r68-test",
            "profile_id": "default",
            "version": 1,
            "inputs": {"positive_prompt": "integration flow", "seed": 12345},
            "callback": {"url": "http://callback-sink.com/result"},
        }
        body = json.dumps(payload).encode("utf-8")

        # 2. Sign Payload (Auth S2)
        signature = hmac.new(b"r68-secret", body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-OpenClaw-Signature": f"sha256={signature}",
        }

        # 3. Mock Upstream ComfyUI Response (F5/R33)
        # We need to patch aiohttp.ClientSession.post inside services.queue_submit
        # Since queue_submit imports aiohttp inside the function, we patch where it's used.
        # But `aiohttp` is imported inside `submit_prompt`.
        # We can patch `services.queue_submit.aiohttp.ClientSession`.

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {"prompt_id": "pid-r68-001", "number": 1}

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            # 4. Execute Request
            resp = await self.client.post(
                "/openclaw/webhook/submit", data=body, headers=headers
            )

            # 5. Assertions
            self.assertEqual(resp.status, 200, await resp.text())
            data = await resp.json()

            self.assertTrue(data["ok"])
            self.assertEqual(data["prompt_id"], "pid-r68-001")
            self.assertTrue(data["callback_scheduled"])

            # Verify Template Rendering happened
            # (Implicit: request would fail if template not found)

            # Verify Queue Submission happened
            mock_session.post.assert_called_once()
            call_args = mock_session.post.call_args
            self.assertEqual(call_args[0][0], "http://mock-upstream:8188/prompt")
            sent_payload = call_args[1]["json"]
            # Correct inputs injected?
            workflow = sent_payload["prompt"]
            self.assertEqual(workflow["3"]["inputs"]["text"], "integration flow")
            self.assertEqual(
                sent_payload["extra_data"]["comfy_usage_source"],
                "comfyui-openclaw",
            )
            self.assertEqual(
                sent_payload["extra_data"]["openclaw"]["tenant_id"], "default"
            )
            self.assertIn("trace_id", sent_payload["extra_data"]["moltbot"])
            self.assertNotIn(
                "integration flow",
                sent_payload["extra_data"]["comfy_usage_source"],
            )

            # Verify Trace ID propagation
            self.assertIn("trace_id", data)

            # Verify Callback Scheduled
            self.mock_callback.assert_called_once()
            self.assertEqual(self.mock_callback.call_args[0][0], "pid-r68-001")

    @unittest_run_loop
    async def test_webhook_auth_rejection(self):
        """Verify Auth S2 rejection stops the flow."""
        resp = await self.client.post(
            "/openclaw/webhook/submit",
            data=json.dumps({"template_id": "r68-test"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 401)
        # Upstream never called
        # Callback never scheduled
        self.mock_callback.assert_not_called()

    @unittest_run_loop
    async def test_idempotency_deduplication(self):
        """R3: Replay with same job_id (if provided) returns cached result."""
        import hashlib
        import hmac

        # 1. Payload with explicit job_id
        payload = {
            "template_id": "r68-test",
            "profile_id": "default",
            "version": 1,
            "job_id": "unique-job-123",
            "inputs": {"seed": 1},
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(b"r68-secret", body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-OpenClaw-Signature": f"sha256={signature}",
        }

        # Mock upstream
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {"prompt_id": "pid-original", "number": 1}
        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            # First Call
            resp1 = await self.client.post(
                "/openclaw/webhook/submit", data=body, headers=headers
            )
            self.assertEqual(resp1.status, 200)
            data1 = await resp1.json()
            self.assertFalse(data1["deduped"])
            self.assertEqual(data1["prompt_id"], "pid-original")

            # Second Call (Same body -> same signature)
            resp2 = await self.client.post(
                "/openclaw/webhook/submit", data=body, headers=headers
            )
            self.assertEqual(resp2.status, 200)
            data2 = await resp2.json()

            # Assert Deduplication
            self.assertTrue(data2["deduped"])
            self.assertEqual(data2["prompt_id"], "pid-original")

            # Assert Upstream called only ONCE
            self.assertEqual(mock_session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
