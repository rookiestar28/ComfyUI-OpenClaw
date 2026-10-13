"""Trigger callbacks must be delivered through the production bootstrap wiring.

These tests register the real trigger and approval routes on a real aiohttp
application through ``_do_full_registration`` so the submit function under test
is the one ComfyUI installs. Only side-effect sinks are replaced: the ComfyUI
queue POST, template files, the callback watcher (dispatch tests) or the
history/HTTP network boundary (end-to-end watcher tests).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import services.approvals.service as approval_service_module
import services.approvals.storage as approval_storage_module

# IMPORTANT: import before any test points OPENCLAW_STATE_DIR at a temp directory.
# This module opens a rotating log file in the state dir on first import; on
# Windows that open handle would make the per-test temp directory undeletable.
import services.llm_client  # noqa: F401
from services.bootstrap import registration
from services.posture.effective import reset_effective_security_posture_for_tests
from services.route_bootstrap_contract import load_route_bootstrap_contract
from services.scheduler.history import reset_run_history
from services.scheduler.runner import get_scheduler_runner, reset_scheduler_runner

ADMIN_TOKEN = "trigger-callback-synthetic-admin-token"
ADMIN_HEADERS = {"X-OpenClaw-Admin-Token": ADMIN_TOKEN}
CALLBACK_HOST = "callback.example.test"
CALLBACK = {"url": f"https://{CALLBACK_HOST}/hooks/openclaw"}
FIRE_PATH = "/openclaw/triggers/fire"


class _DummyRoutes:
    def _decorator(self, _method, _path):
        return lambda handler: handler

    def get(self, path):
        return self._decorator("GET", path)

    def post(self, path):
        return self._decorator("POST", path)

    def put(self, path):
        return self._decorator("PUT", path)

    def delete(self, path):
        return self._decorator("DELETE", path)


class _FakeTemplateService:
    def get_template_config(self, template_id):
        return {"template_id": template_id}

    def render_template(self, template_id, inputs):
        return {"1": {"class_type": "CallbackProbe", "inputs": dict(inputs or {})}}


class _ProductionTriggerWiring(unittest.IsolatedAsyncioTestCase):
    """Real bootstrap wiring on a real aiohttp app with isolated singletons."""

    watch_callbacks = True

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_trigger_callback_")
        self.env = patch.dict(
            os.environ,
            {
                "OPENCLAW_STATE_DIR": self.tmp.name,
                "OPENCLAW_ADMIN_TOKEN": ADMIN_TOKEN,
                "OPENCLAW_DEPLOYMENT_PROFILE": "local",
            },
            clear=False,
        )
        self.env.start()
        for key in ("OPENCLAW_CONTROL_PLANE_MODE", "OPENCLAW_SPLIT_COMPAT_OVERRIDE"):
            os.environ.pop(key, None)
        reset_effective_security_posture_for_tests()

        self.old_approval_store = approval_storage_module._approval_store
        self.old_approval_service = approval_service_module._approval_service
        approval_storage_module._approval_store = None
        approval_service_module._approval_service = None
        reset_scheduler_runner(stop=False)
        reset_run_history()

        self.prompt_counter = 0

        async def _submit_prompt(*_args, **_kwargs):
            self.prompt_counter += 1
            return {"prompt_id": f"prompt-{self.prompt_counter}"}

        self.submit_prompt = AsyncMock(side_effect=_submit_prompt)
        self.callback_watch = AsyncMock(return_value=None)

        contract = dict(load_route_bootstrap_contract())
        contract.update(
            {
                # Unrelated families stay out of this test; trigger, approval and
                # bridge wiring plus the scheduler binding remain production code.
                "register_routes": lambda server: None,
                "register_preset_routes": lambda app: None,
                "register_schedule_routes": lambda app, require_admin_token_fn=None: None,
            }
        )
        patches = [
            patch(
                "services.route_bootstrap_contract.load_route_bootstrap_contract",
                return_value=contract,
            ),
            patch("services.scheduler.runner.start_scheduler"),
            patch("services.queue_submit.submit_prompt", self.submit_prompt),
            patch(
                "services.templates.get_template_service",
                return_value=_FakeTemplateService(),
            ),
        ]
        if self.watch_callbacks:
            patches.append(
                patch(
                    "services.callback_delivery.start_callback_watch",
                    self.callback_watch,
                )
            )
        self.patches = patches
        for active in self.patches:
            active.start()

        self.app = web.Application()
        prompt_handlers = []
        server = SimpleNamespace(
            routes=_DummyRoutes(),
            app=self.app,
            on_prompt_handlers=prompt_handlers,
            add_on_prompt_handler=prompt_handlers.append,
        )
        registration._do_full_registration(server)

        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for active in reversed(self.patches):
            active.stop()
        reset_scheduler_runner(stop=False)
        reset_run_history()
        approval_storage_module._approval_store = self.old_approval_store
        approval_service_module._approval_service = self.old_approval_service
        reset_effective_security_posture_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def _trace_id(label: str) -> str:
        return f"trace-{label}-{uuid.uuid4().hex}"

    async def _fire(self, **body):
        payload = {"template_id": "callback_probe", "inputs": {"prompt": "probe"}}
        payload.update(body)
        response = await self.client.post(
            FIRE_PATH, json=payload, headers=ADMIN_HEADERS
        )
        return response, await response.json()

    async def _approve(self, approval_id: str):
        response = await self.client.post(
            f"/openclaw/approvals/{approval_id}/approve",
            json={"actor": "callback-test"},
            headers=ADMIN_HEADERS,
        )
        return response, await response.json()

    def _approval_count(self) -> int:
        service = approval_service_module.get_approval_service()
        return len(service.list_all(limit=10_000))


class TestTriggerCallbackDispatch(_ProductionTriggerWiring):
    async def test_direct_trigger_schedules_exactly_one_callback(self):
        trace_id = self._trace_id("direct")

        response, body = await self._fire(trace_id=trace_id, callback=CALLBACK)

        self.assertEqual(response.status, 200, body)
        self.assertTrue(body["triggered"])
        self.assertEqual(body.get("callback"), "scheduled")
        self.callback_watch.assert_awaited_once_with(
            body["prompt_id"], CALLBACK, trace_id=trace_id
        )

    async def test_trigger_without_callback_keeps_response_shape(self):
        response, body = await self._fire(trace_id=self._trace_id("plain"))

        self.assertEqual(response.status, 200, body)
        self.assertNotIn("callback", body)
        self.callback_watch.assert_not_awaited()

    async def test_deduplicated_trigger_does_not_start_second_callback(self):
        trace_id = self._trace_id("dedupe")

        _first, first_body = await self._fire(trace_id=trace_id, callback=CALLBACK)
        second, second_body = await self._fire(trace_id=trace_id, callback=CALLBACK)

        self.assertEqual(second.status, 200, second_body)
        self.assertTrue(second_body["deduped"])
        self.assertEqual(second_body.get("callback"), "deduped")
        self.assertEqual(self.submit_prompt.await_count, 1)
        self.callback_watch.assert_awaited_once_with(
            first_body["prompt_id"], CALLBACK, trace_id=trace_id
        )

    async def test_approved_trigger_schedules_exactly_one_callback(self):
        trace_id = self._trace_id("approval")

        response, body = await self._fire(
            trace_id=trace_id, callback=CALLBACK, require_approval=True
        )
        self.assertEqual(response.status, 202, body)
        self.submit_prompt.assert_not_awaited()
        self.callback_watch.assert_not_awaited()

        approved, approved_body = await self._approve(body["approval_id"])

        self.assertEqual(approved.status, 200, approved_body)
        self.assertTrue(approved_body["executed"])
        self.assertEqual(approved_body.get("callback"), "scheduled")
        self.callback_watch.assert_awaited_once_with(
            approved_body["prompt_id"], CALLBACK, trace_id=trace_id
        )

    async def test_callback_method_is_normalized_before_storage_and_dispatch(self):
        trace_id = self._trace_id("normalized")
        callback = {
            "url": CALLBACK["url"],
            "method": "put",
            "headers": {"X-Callback-Signature": "synthetic-signature"},
        }

        response, body = await self._fire(trace_id=trace_id, callback=callback)

        self.assertEqual(response.status, 200, body)
        self.callback_watch.assert_awaited_once_with(
            body["prompt_id"], {**callback, "method": "PUT"}, trace_id=trace_id
        )

    async def test_malformed_callbacks_fail_before_submission_or_approval(self):
        too_many_headers = {f"X-H{i}": "v" for i in range(21)}
        cases = {
            "string": "https://callback.example.test/x",
            "list": [CALLBACK["url"]],
            "missing_url": {"method": "POST"},
            "empty_url": {"url": "   "},
            "ftp_scheme": {"url": "ftp://callback.example.test/x"},
            "javascript_scheme": {"url": "javascript:alert(1)"},
            "no_host": {"url": "https:///path-only"},
            "userinfo": {"url": "https://user:hunter2-secret@callback.example.test/x"},
            "whitespace": {"url": "https://callback.example.test/a b"},
            "bad_port": {"url": "https://callback.example.test:99999/x"},
            "oversized_url": {"url": "https://callback.example.test/" + "a" * 2048},
            "unknown_field": {"url": CALLBACK["url"], "token": "leak-me-not"},
            "get_method": {"url": CALLBACK["url"], "method": "GET"},
            "headers_not_object": {"url": CALLBACK["url"], "headers": ["X-A: b"]},
            "header_crlf": {
                "url": CALLBACK["url"],
                "headers": {"X-A": "ok\r\nInjected: crlf-secret"},
            },
            "header_bad_name": {"url": CALLBACK["url"], "headers": {"Bad Name": "v"}},
            "host_header": {"url": CALLBACK["url"], "headers": {"Host": "internal"}},
            "too_many_headers": {"url": CALLBACK["url"], "headers": too_many_headers},
            "empty_mode": {"url": CALLBACK["url"], "mode": ""},
        }
        approvals_before = self._approval_count()

        for label, callback in cases.items():
            for require_approval in (False, True):
                with self.subTest(case=label, require_approval=require_approval):
                    response, body = await self._fire(
                        trace_id=self._trace_id(label),
                        callback=callback,
                        require_approval=require_approval,
                    )
                    self.assertEqual(response.status, 400, body)
                    self.assertEqual(body.get("code"), "callback_malformed")
                    rendered = str(body)
                    self.assertNotIn("hunter2-secret", rendered)
                    self.assertNotIn("leak-me-not", rendered)
                    self.assertNotIn("crlf-secret", rendered)

        self.submit_prompt.assert_not_awaited()
        self.callback_watch.assert_not_awaited()
        self.assertEqual(self._approval_count(), approvals_before)

    async def test_split_control_plane_blocks_trigger_callback_before_side_effects(
        self,
    ):
        os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "public"
        approvals_before = self._approval_count()

        for require_approval in (False, True):
            with self.subTest(require_approval=require_approval):
                response, body = await self._fire(
                    trace_id=self._trace_id("split"),
                    callback=CALLBACK,
                    require_approval=require_approval,
                )
                self.assertEqual(response.status, 403, body)
                self.assertEqual(body.get("code"), "SURFACE_BLOCKED")
                self.assertEqual(body.get("surface_id"), "callback_egress")

        self.submit_prompt.assert_not_awaited()
        self.callback_watch.assert_not_awaited()
        self.assertEqual(self._approval_count(), approvals_before)

        # Triggers without a callback are not affected by the egress guard.
        response, body = await self._fire(trace_id=self._trace_id("split-plain"))
        self.assertEqual(response.status, 200, body)

    async def test_approval_executes_without_callback_when_egress_became_blocked(self):
        response, body = await self._fire(
            trace_id=self._trace_id("late-split"),
            callback=CALLBACK,
            require_approval=True,
        )
        self.assertEqual(response.status, 202, body)
        os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "public"

        approved, approved_body = await self._approve(body["approval_id"])

        self.assertEqual(approved.status, 200, approved_body)
        self.assertTrue(approved_body["executed"])
        self.assertEqual(approved_body.get("callback"), "blocked")
        self.callback_watch.assert_not_awaited()

    async def test_stored_invalid_callback_is_reported_not_dispatched(self):
        service = approval_service_module.get_approval_service()
        approval = service.create_request(
            template_id="callback_probe",
            inputs={"prompt": "probe"},
            trace_id=self._trace_id("stored-invalid"),
            delivery={"url": "ftp://callback.example.test/legacy"},
        )

        approved, approved_body = await self._approve(approval.approval_id)

        self.assertEqual(approved.status, 200, approved_body)
        self.assertTrue(approved_body["executed"])
        self.assertEqual(approved_body.get("callback"), "invalid")
        self.callback_watch.assert_not_awaited()

    async def test_scheduler_runs_never_start_callback_watchers(self):
        from services.scheduler.models import Schedule, TriggerType

        runner = get_scheduler_runner()
        schedule = Schedule(
            schedule_id="sched_callback_guard",
            name="Callback guard",
            template_id="callback_probe",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
            delivery={"platform": "telegram", "target_id": "-1001234567890"},
        )

        runner._execute_schedule(schedule, 1_000_000.0)

        self.assertEqual(self.submit_prompt.await_count, 1)
        self.callback_watch.assert_not_awaited()


class TestTriggerCallbackEndToEnd(_ProductionTriggerWiring):
    """The real watcher runs on the live loop; only history and HTTP are replaced."""

    watch_callbacks = False

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.history_item = {
            "status": {"status_str": "success", "completed": True},
            "outputs": {},
        }
        self.http_post = Mock(return_value={})
        self.network = [
            patch("services.callback_delivery.POLL_INTERVAL_SEC", 0),
            patch(
                "services.callback_delivery.fetch_history",
                return_value=self.history_item,
            ),
            patch("services.callback_delivery.safe_request_json", self.http_post),
        ]
        for active in self.network:
            active.start()

    async def asyncTearDown(self):
        for active in reversed(self.network):
            active.stop()
        await super().asyncTearDown()

    async def _wait_for(self, predicate, timeout: float = 5.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return predicate()

    async def test_trigger_callback_posts_once_to_allowlisted_host(self):
        os.environ["OPENCLAW_CALLBACK_ALLOW_HOSTS"] = CALLBACK_HOST
        trace_id = self._trace_id("e2e")

        response, body = await self._fire(trace_id=trace_id, callback=CALLBACK)

        self.assertEqual(response.status, 200, body)
        self.assertTrue(await self._wait_for(lambda: self.http_post.call_count >= 1))
        await asyncio.sleep(0.05)
        self.assertEqual(self.http_post.call_count, 1)
        args, kwargs = self.http_post.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], CALLBACK["url"])
        self.assertEqual(args[2]["prompt_id"], body["prompt_id"])
        self.assertEqual(args[2]["trace_id"], trace_id)
        self.assertEqual(kwargs["allow_hosts"], {CALLBACK_HOST})

    async def test_trigger_callback_without_allowlist_never_posts(self):
        os.environ.pop("OPENCLAW_CALLBACK_ALLOW_HOSTS", None)
        os.environ.pop("MOLTBOT_CALLBACK_ALLOW_HOSTS", None)

        response, body = await self._fire(
            trace_id=self._trace_id("e2e-deny"), callback=CALLBACK
        )

        self.assertEqual(response.status, 200, body)
        self.assertEqual(body.get("callback"), "scheduled")
        await asyncio.sleep(0.1)
        self.http_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
