"""Real-aiohttp transactions for selected administrative trust boundaries."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import services.approvals.service as approval_service_module
import services.approvals.storage as approval_storage_module
import services.scheduler.storage as schedule_storage_module
from api.approvals import ApprovalHandlers
from api.bridge import BridgeHandlers, register_bridge_routes
from api.rewrite_recipes import (
    rewrite_recipe_apply_handler,
    rewrite_recipe_create_handler,
    rewrite_recipe_dry_run_handler,
)
from api.schedules import ScheduleHandlers
from services.access_control import require_admin_token
from services.approvals.models import ApprovalStatus
from services.rewrite_recipes import rewrite_recipe_store

ADMIN_TOKEN = "r247-synthetic-admin-token"
BRIDGE_TOKEN = "r247-synthetic-bridge-token"
ADMIN_HEADERS = {"X-OpenClaw-Admin-Token": ADMIN_TOKEN}
BRIDGE_HEADERS = {
    "X-OpenClaw-Device-Id": "r247-worker",
    "X-OpenClaw-Device-Token": BRIDGE_TOKEN,
    "X-OpenClaw-Scopes": "job:status,job:submit",
}


class TestAdminApiTransactions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_r247_admin_")
        self.env = patch.dict(
            os.environ,
            {
                "OPENCLAW_STATE_DIR": self.tmp.name,
                "OPENCLAW_ADMIN_TOKEN": ADMIN_TOKEN,
                "OPENCLAW_BRIDGE_ENABLED": "1",
                "OPENCLAW_BRIDGE_DEVICE_TOKEN": BRIDGE_TOKEN,
            },
            clear=False,
        )
        self.env.start()

        self.old_approval_store = approval_storage_module._approval_store
        self.old_approval_service = approval_service_module._approval_service
        self.old_schedule_store = schedule_storage_module._schedule_store
        self.old_rewrite_dir = rewrite_recipe_store.storage_dir
        approval_storage_module._approval_store = None
        approval_service_module._approval_service = None
        schedule_storage_module._schedule_store = None
        rewrite_recipe_store.storage_dir = Path(self.tmp.name) / "rewrite_recipes"
        rewrite_recipe_store.storage_dir.mkdir(parents=True, exist_ok=True)

        self.approvals = ApprovalHandlers(
            require_admin_token_fn=require_admin_token,
            submit_fn=None,
        )
        self.schedules = ScheduleHandlers(require_admin_token_fn=require_admin_token)
        self.bridge = BridgeHandlers()

        app = web.Application()
        app.router.add_get("/openclaw/approvals", self.approvals.list_approvals)
        app.router.add_post(
            "/openclaw/approvals/{approval_id}/approve",
            self.approvals.approve_request,
        )
        app.router.add_post(
            "/openclaw/approvals/{approval_id}/reject",
            self.approvals.reject_request,
        )
        app.router.add_post("/openclaw/schedules", self.schedules.create_schedule)
        app.router.add_get(
            "/openclaw/schedules/{schedule_id}", self.schedules.get_schedule
        )
        app.router.add_post(
            "/openclaw/schedules/{schedule_id}/run", self.schedules.run_now
        )
        app.router.add_post("/openclaw/rewrite/recipes", rewrite_recipe_create_handler)
        app.router.add_post(
            "/openclaw/rewrite/recipes/{recipe_id}/dry-run",
            rewrite_recipe_dry_run_handler,
        )
        app.router.add_post(
            "/openclaw/rewrite/recipes/{recipe_id}/apply",
            rewrite_recipe_apply_handler,
        )
        register_bridge_routes(app, self.bridge)

        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        approval_storage_module._approval_store = self.old_approval_store
        approval_service_module._approval_service = self.old_approval_service
        schedule_storage_module._schedule_store = self.old_schedule_store
        rewrite_recipe_store.storage_dir = self.old_rewrite_dir
        self.env.stop()
        self.tmp.cleanup()

    async def test_admin_and_bridge_auth_fail_closed(self):
        admin_denied = await self.client.get(
            "/openclaw/approvals",
            headers={"X-OpenClaw-Admin-Token": "wrong-synthetic-token"},
        )
        self.assertEqual(admin_denied.status, 403)

        bridge_denied = await self.client.get(
            "/bridge/worker/poll",
            headers={
                **BRIDGE_HEADERS,
                "X-OpenClaw-Device-Token": "wrong-synthetic-token",
            },
        )
        self.assertEqual(bridge_denied.status, 401)

    async def test_approval_list_approve_and_reject_persist_terminal_states(self):
        approved = self.approvals._service.create_request(
            template_id="sdxl_basic",
            inputs={"positive_prompt": "synthetic approval one"},
        )
        rejected = self.approvals._service.create_request(
            template_id="sdxl_basic",
            inputs={"positive_prompt": "synthetic approval two"},
        )

        listed = await self.client.get("/openclaw/approvals", headers=ADMIN_HEADERS)
        self.assertEqual(listed.status, 200)
        listed_body = await listed.json()
        self.assertEqual(listed_body["count"], 2)
        self.assertEqual(listed_body["pending_count"], 2)

        approve_response = await self.client.post(
            f"/openclaw/approvals/{approved.approval_id}/approve",
            json={"actor": "r247-operator", "auto_execute": False},
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(approve_response.status, 200)
        self.assertFalse((await approve_response.json())["executed"])

        reject_response = await self.client.post(
            f"/openclaw/approvals/{rejected.approval_id}/reject",
            json={"actor": "r247-operator"},
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(reject_response.status, 200)

        self.assertEqual(
            self.approvals._service.get(approved.approval_id).status,
            ApprovalStatus.APPROVED,
        )
        self.assertEqual(
            self.approvals._service.get(rejected.approval_id).status,
            ApprovalStatus.REJECTED,
        )

    async def test_schedule_create_read_and_run_reaches_only_execution_seam(self):
        created = await self.client.post(
            "/openclaw/schedules",
            json={
                "name": "R247 synthetic schedule",
                "template_id": "sdxl_basic",
                "trigger_type": "interval",
                "interval_sec": 60,
                "inputs": {"positive_prompt": "synthetic schedule"},
            },
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(created.status, 201, msg=await created.text())
        schedule_id = (await created.json())["schedule"]["schedule_id"]

        readback = await self.client.get(
            f"/openclaw/schedules/{schedule_id}", headers=ADMIN_HEADERS
        )
        self.assertEqual(readback.status, 200)
        self.assertEqual(
            (await readback.json())["schedule"]["template_id"], "sdxl_basic"
        )

        runner = Mock()
        runner.is_execution_delegated.return_value = False
        with patch("api.schedules._get_scheduler_runner", return_value=runner):
            run_response = await self.client.post(
                f"/openclaw/schedules/{schedule_id}/run", headers=ADMIN_HEADERS
            )
        self.assertEqual(run_response.status, 200)
        self.assertTrue((await run_response.json())["triggered"])
        runner._execute_schedule.assert_called_once()

    async def test_bridge_poll_and_result_round_trip_uses_real_auth(self):
        self.bridge._worker_job_queue.append(
            {"job_id": "bridge-job-r247", "template_id": "sdxl_basic"}
        )
        polled = await self.client.get("/bridge/worker/poll", headers=BRIDGE_HEADERS)
        self.assertEqual(polled.status, 200)
        self.assertEqual((await polled.json())["jobs"][0]["job_id"], "bridge-job-r247")

        result = await self.client.post(
            "/bridge/worker/result/bridge-job-r247",
            json={"status": "completed", "outputs": {"images": []}},
            headers={**BRIDGE_HEADERS, "X-Idempotency-Key": "r247-result-one"},
        )
        self.assertEqual(result.status, 201, msg=await result.text())
        self.assertEqual(
            self.bridge._worker_results["bridge-job-r247"]["status"], "completed"
        )

    async def test_rewrite_create_dry_run_and_guarded_apply_transaction(self):
        created = await self.client.post(
            "/openclaw/rewrite/recipes",
            json={
                "name": "R247 synthetic rewrite",
                "operations": [{"path": "/1/inputs/text", "value": "{{topic}}"}],
                "constraints": {"required_inputs": ["topic"]},
            },
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(created.status, 201, msg=await created.text())
        recipe_id = (await created.json())["recipe"]["id"]
        workflow = {"1": {"inputs": {"text": "old"}}}

        dry_run = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/dry-run",
            json={"workflow": workflow, "inputs": {"topic": "new"}},
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(dry_run.status, 200)
        self.assertEqual(
            (await dry_run.json())["workflow"]["1"]["inputs"]["text"], "new"
        )

        guarded = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/apply",
            json={"workflow": workflow, "inputs": {"topic": "new"}},
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(guarded.status, 400)
        guarded_body = await guarded.json()
        self.assertEqual(guarded_body["error"], "apply_requires_confirm")
        self.assertEqual(guarded_body["rollback_snapshot"], workflow)

        applied = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/apply",
            json={
                "workflow": workflow,
                "inputs": {"topic": "new"},
                "confirm": True,
            },
            headers=ADMIN_HEADERS,
        )
        self.assertEqual(applied.status, 200)
        self.assertEqual(
            (await applied.json())["applied_workflow"]["1"]["inputs"]["text"],
            "new",
        )


if __name__ == "__main__":
    unittest.main()
