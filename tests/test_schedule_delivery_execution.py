"""Schedule delivery targets must survive the production submit path as durable intents.

The real schedule routes, scheduler binding and submit function are installed through
``_do_full_registration`` on a real aiohttp application. Only side-effect sinks are replaced:
the ComfyUI queue POST and the template service. Background ticks run on a worker thread so
the submit coroutine executes on the same short-lived loop the scheduler thread uses.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# IMPORTANT: import before any test points OPENCLAW_STATE_DIR at a temp directory.
# This module opens a rotating log file in the state dir on first import; on
# Windows that open handle would make the per-test temp directory undeletable.
import services.llm_client  # noqa: F401
from services.bootstrap import registration
from services.posture.effective import reset_effective_security_posture_for_tests
from services.rate_limit import RateLimiter
from services.route_bootstrap_contract import load_route_bootstrap_contract
from services.scheduler.history import get_run_history, reset_run_history
from services.scheduler.models import Schedule, TriggerType
from services.scheduler.runner import get_scheduler_runner, reset_scheduler_runner

ADMIN_TOKEN = "schedule-delivery-synthetic-admin-token"
ADMIN_HEADERS = {"X-OpenClaw-Admin-Token": ADMIN_TOKEN}
DELIVERIES = "/openclaw/schedule-deliveries"
TELEGRAM_TOPIC = {
    "platform": "telegram",
    "target_id": "-1001234567890",
    "thread_id": "42",
}
SLACK_THREAD = {
    "platform": "slack",
    "target_id": "C0123456789",
    "thread_ts": "1712345678.000100",
    "workspace_id": "T0123456789",
}


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
        return {"1": {"class_type": "DeliveryProbe", "inputs": dict(inputs or {})}}


def _reset_outbox() -> None:
    # The outbox module does not exist before the fix; a missing module is the RED state.
    try:
        from services.scheduler.delivery_outbox import reset_delivery_outbox
    except ImportError:
        return
    reset_delivery_outbox()


class _ProductionScheduleWiring(unittest.IsolatedAsyncioTestCase):
    """Real bootstrap wiring with real schedule routes and isolated singletons."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_schedule_delivery_")
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
        for key in (
            "OPENCLAW_CONTROL_PLANE_MODE",
            "OPENCLAW_SPLIT_COMPAT_OVERRIDE",
            "OPENCLAW_SCHEDULER_EXECUTION_MODE",
        ):
            os.environ.pop(key, None)
        reset_effective_security_posture_for_tests()
        reset_scheduler_runner(stop=False)
        reset_run_history()
        _reset_outbox()

        self.prompt_counter = 0

        async def _submit_prompt(*_args: Any, **_kwargs: Any) -> dict[str, str]:
            self.prompt_counter += 1
            return {"prompt_id": f"prompt-{self.prompt_counter}"}

        self.submit_prompt = AsyncMock(side_effect=_submit_prompt)
        contract = dict(load_route_bootstrap_contract())
        contract.update(
            {
                # Unrelated families stay out; schedule routes and the scheduler
                # submit binding remain production code.
                "register_routes": lambda server: None,
                "register_preset_routes": lambda app: None,
            }
        )
        self.patches = [
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
            # Per-test limiter: the process-wide token buckets would otherwise carry
            # one test's request budget into the next.
            patch("services.rate_limit.rate_limiter", RateLimiter()),
        ]
        for active in self.patches:
            active.start()

        self.app = web.Application()
        prompt_handlers: list[Any] = []
        server = SimpleNamespace(
            routes=_DummyRoutes(),
            app=self.app,
            on_prompt_handlers=prompt_handlers,
            add_on_prompt_handler=prompt_handlers.append,
        )
        registration._do_full_registration(server)
        self.install_host_routes(self.app)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    def install_host_routes(self, app: web.Application) -> None:
        """Hook for ComfyUI host route stubs; the router freezes when the server starts."""

    async def asyncTearDown(self) -> None:
        await self.client.close()
        for active in reversed(self.patches):
            active.stop()
        reset_scheduler_runner(stop=False)
        reset_run_history()
        _reset_outbox()
        reset_effective_security_posture_for_tests()
        self.env.stop()
        self.tmp.cleanup()

    async def create_schedule(self, delivery: Any, name: str = "Delivery probe") -> str:
        body: dict[str, Any] = {
            "name": name,
            "template_id": "delivery_probe",
            "trigger_type": "interval",
            "interval_sec": 3600,
            "enabled": True,
        }
        if delivery is not ...:
            body["delivery"] = delivery
        response = await self.client.post(
            "/openclaw/schedules", json=body, headers=ADMIN_HEADERS
        )
        payload = await response.json()
        self.assertIn(response.status, (200, 201), payload)
        return payload["schedule"]["schedule_id"]

    async def run_manually(self, schedule_id: str) -> None:
        response = await self.client.post(
            f"/openclaw/schedules/{schedule_id}/run", headers=ADMIN_HEADERS
        )
        self.assertEqual(response.status, 200, await response.text())

    async def run_background_tick(self, schedule: Schedule, tick: float) -> None:
        runner = get_scheduler_runner()
        await asyncio.to_thread(runner._execute_schedule, schedule, tick)

    async def list_deliveries(self, **query: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            DELIVERIES, params=query, headers=ADMIN_HEADERS
        )
        payload = await response.json(content_type=None)
        self.assertEqual(response.status, 200, payload)
        return payload["deliveries"]

    async def claim(self, platforms: list[str], **extra: Any) -> tuple[int, dict]:
        body: dict[str, Any] = {"platforms": platforms, "limit": 5, "lease_sec": 600}
        body.update(extra)
        response = await self.client.post(
            f"{DELIVERIES}/claim", json=body, headers=ADMIN_HEADERS
        )
        return response.status, await response.json(content_type=None)

    async def ack(self, delivery_id: str, **body: Any) -> tuple[int, dict]:
        response = await self.client.post(
            f"{DELIVERIES}/{delivery_id}/ack", json=body, headers=ADMIN_HEADERS
        )
        return response.status, await response.json(content_type=None)


class ScheduleDeliveryIntentTests(_ProductionScheduleWiring):
    async def test_background_tick_records_one_intent_for_a_telegram_topic(
        self,
    ) -> None:
        schedule = Schedule(
            schedule_id="sched_delivery_tick",
            name="Tick probe",
            template_id="delivery_probe",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
            delivery=dict(TELEGRAM_TOPIC),
        )

        await self.run_background_tick(schedule, 1_000_000.0)

        deliveries = await self.list_deliveries()
        self.assertEqual(len(deliveries), 1)
        intent = deliveries[0]
        self.assertEqual(intent["state"], "pending")
        self.assertEqual(intent["prompt_id"], "prompt-1")
        self.assertEqual(intent["schedule_id"], "sched_delivery_tick")
        self.assertEqual(intent["delivery"]["platform"], "telegram")
        self.assertEqual(intent["delivery"]["target_id"], "-1001234567890")
        self.assertEqual(intent["delivery"]["thread_id"], "42")
        self.assertTrue(intent["delivery"]["failure_alert"])

    async def test_manual_run_records_one_intent_for_a_slack_thread(self) -> None:
        schedule_id = await self.create_schedule(dict(SLACK_THREAD))

        await self.run_manually(schedule_id)

        deliveries = await self.list_deliveries(platform="slack")
        self.assertEqual(len(deliveries), 1)
        delivery = deliveries[0]["delivery"]
        self.assertEqual(delivery["target_id"], "C0123456789")
        self.assertEqual(delivery["thread_id"], "1712345678.000100")
        self.assertEqual(delivery["workspace_id"], "T0123456789")
        self.assertEqual(deliveries[0]["schedule_id"], schedule_id)

    async def test_no_delivery_variants_record_nothing(self) -> None:
        for delivery in (..., None, {"enabled": False}, {"mode": "none"}):
            with self.subTest(delivery=delivery):
                schedule_id = await self.create_schedule(
                    delivery, name=f"none {delivery}"
                )
                await self.run_manually(schedule_id)

        self.assertEqual(self.submit_prompt.await_count, 4)
        self.assertEqual(await self.list_deliveries(), [])

    async def test_retry_after_lost_run_record_keeps_one_intent(self) -> None:
        schedule = Schedule(
            schedule_id="sched_delivery_retry",
            name="Retry probe",
            template_id="delivery_probe",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
            delivery=dict(TELEGRAM_TOPIC),
        )
        await self.run_background_tick(schedule, 2_000_000.0)
        # Simulate a crash after submit and before the run record was saved.
        history = get_run_history()
        history._ensure_loaded()
        history._runs.clear()
        history._save()

        await self.run_background_tick(schedule, 2_000_000.0)

        deliveries = await self.list_deliveries()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["prompt_id"], "prompt-1")

    async def test_intent_survives_a_process_restart(self) -> None:
        schedule_id = await self.create_schedule(dict(TELEGRAM_TOPIC))
        await self.run_manually(schedule_id)

        _reset_outbox()

        deliveries = await self.list_deliveries()
        self.assertEqual([d["state"] for d in deliveries], ["pending"])

    async def test_target_without_connector_adapter_is_visible_as_undeliverable(
        self,
    ) -> None:
        schedule_id = await self.create_schedule(
            {"platform": "webhook", "target_id": "ops-hook"}
        )

        await self.run_manually(schedule_id)

        deliveries = await self.list_deliveries()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["state"], "undeliverable")
        self.assertEqual(deliveries[0]["reason"], "no_connector_adapter")
        status, body = await self.claim(["webhook", "telegram"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["deliveries"], [])

    async def test_run_history_shows_the_delivery_state(self) -> None:
        schedule_id = await self.create_schedule(dict(TELEGRAM_TOPIC))
        await self.run_manually(schedule_id)

        response = await self.client.get(
            f"/openclaw/schedules/{schedule_id}/runs", headers=ADMIN_HEADERS
        )
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        run = payload["runs"][0]
        self.assertEqual(run["delivery"]["state"], "pending")
        self.assertTrue(run["delivery"]["delivery_id"].startswith("dlv_"))


class ScheduleDeliveryClaimTests(_ProductionScheduleWiring):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        schedule_id = await self.create_schedule(dict(TELEGRAM_TOPIC))
        await self.run_manually(schedule_id)

    async def test_only_one_concurrent_claimer_receives_the_intent(self) -> None:
        results = await asyncio.gather(
            self.claim(["telegram"]), self.claim(["telegram"]), self.claim(["telegram"])
        )

        claimed = [d for status, body in results for d in body.get("deliveries", [])]
        self.assertEqual([status for status, _ in results], [200, 200, 200])
        self.assertEqual(len(claimed), 1)
        self.assertTrue(claimed[0]["lease_token"])
        self.assertEqual(claimed[0]["delivery"]["thread_id"], "42")

    async def test_other_platforms_do_not_claim(self) -> None:
        status, body = await self.claim(["slack", "discord"])

        self.assertEqual(status, 200, body)
        self.assertEqual(body["deliveries"], [])

    async def test_ack_is_accepted_once_and_repeat_is_idempotent(self) -> None:
        _, body = await self.claim(["telegram"])
        intent = body["deliveries"][0]

        first = await self.ack(
            intent["delivery_id"], lease_token=intent["lease_token"], outcome="sent"
        )
        repeat = await self.ack(
            intent["delivery_id"], lease_token=intent["lease_token"], outcome="sent"
        )

        self.assertEqual(first[0], 200, first[1])
        self.assertEqual(first[1]["status"], "acknowledged")
        self.assertEqual(repeat[0], 200, repeat[1])
        self.assertEqual(repeat[1]["status"], "already_acknowledged")
        self.assertEqual((await self.list_deliveries())[0]["state"], "sent")
        self.assertEqual((await self.claim(["telegram"]))[1]["deliveries"], [])

    async def test_stale_or_wrong_token_is_rejected(self) -> None:
        _, body = await self.claim(["telegram"])
        intent = body["deliveries"][0]

        status, payload = await self.ack(
            intent["delivery_id"], lease_token="not-the-lease", outcome="sent"
        )

        self.assertEqual(status, 409, payload)
        self.assertEqual((await self.list_deliveries())[0]["state"], "claimed")

    async def test_expired_lease_is_offered_again(self) -> None:
        _, body = await self.claim(["telegram"])
        first = body["deliveries"][0]
        from services.scheduler.delivery_outbox import get_delivery_outbox

        get_delivery_outbox()._expire_lease_for_tests(first["delivery_id"])

        _, again = await self.claim(["telegram"])
        self.assertEqual(len(again["deliveries"]), 1)
        second = again["deliveries"][0]
        self.assertNotEqual(second["lease_token"], first["lease_token"])
        status, _ = await self.ack(
            first["delivery_id"], lease_token=first["lease_token"], outcome="sent"
        )
        self.assertEqual(status, 409)

    async def test_bounded_outcomes_and_reasons(self) -> None:
        _, body = await self.claim(["telegram"])
        intent = body["deliveries"][0]

        bad_outcome = await self.ack(
            intent["delivery_id"], lease_token=intent["lease_token"], outcome="maybe"
        )
        bad_reason = await self.ack(
            intent["delivery_id"],
            lease_token=intent["lease_token"],
            outcome="undeliverable",
            reason="free text with the target -1001234567890",
        )
        good = await self.ack(
            intent["delivery_id"],
            lease_token=intent["lease_token"],
            outcome="undeliverable",
            reason="target_not_allowed",
        )

        self.assertEqual(bad_outcome[0], 400)
        self.assertEqual(bad_reason[0], 400)
        self.assertEqual(good[0], 200, good[1])
        listed = (await self.list_deliveries())[0]
        self.assertEqual(
            (listed["state"], listed["reason"]), ("undeliverable", "target_not_allowed")
        )

    async def test_routes_require_the_admin_token(self) -> None:
        for method, path in (
            ("GET", DELIVERIES),
            ("POST", f"{DELIVERIES}/claim"),
            ("POST", f"{DELIVERIES}/dlv_unknown/ack"),
        ):
            with self.subTest(path=path):
                response = await self.client.request(method, path, json={})
                self.assertEqual(response.status, 403)


class ScheduleDeliveryRouteGovernanceTests(_ProductionScheduleWiring):
    async def test_new_routes_are_admin_plane_and_pass_mae_validation(self) -> None:
        from services.endpoint_manifest import generate_manifest, validate_mae_posture

        manifest = [
            entry
            for entry in generate_manifest(self.app)
            if "/schedule-deliveries" in entry.get("path", "")
        ]
        paths = {(entry["method"], entry["path"]) for entry in manifest}
        self.assertIn(("GET", DELIVERIES), paths)
        self.assertIn(("POST", f"{DELIVERIES}/claim"), paths)
        self.assertIn(("POST", f"{DELIVERIES}/{{delivery_id}}/ack"), paths)
        self.assertFalse(any("/moltbot/" in path for _, path in paths))
        for profile in ("local", "public", "hardened"):
            with self.subTest(profile=profile):
                ok, errors = validate_mae_posture(manifest, profile=profile)
                self.assertTrue(ok, errors)

    async def test_claim_and_ack_use_the_connector_rate_limit(self) -> None:
        seen: list[str] = []

        def _limited(request: Any, category: str, *args: Any, **kwargs: Any) -> bool:
            seen.append(category)
            return False

        with patch("api.schedules.check_rate_limit", side_effect=_limited):
            claim = await self.client.post(
                f"{DELIVERIES}/claim",
                json={"platforms": ["telegram"]},
                headers=ADMIN_HEADERS,
            )
            listing = await self.client.get(DELIVERIES, headers=ADMIN_HEADERS)

        self.assertEqual(claim.status, 429)
        self.assertEqual(listing.status, 429)
        self.assertEqual(seen, ["connector", "admin"])


PNG_BYTES = b"\x89PNG\r\n\x1a\nschedule-delivery-probe"


class _RecordingPlatform:
    """Chat platform sink: records exactly what the connector hands to the adapter."""

    def __init__(self) -> None:
        self.images: list[tuple[str, bytes, str, dict[str, Any]]] = []
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: str | None = None,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        self.images.append(
            (channel_id, image_data, filename, dict(delivery_context or {}))
        )

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append((channel_id, text, dict(delivery_context or {})))


class ScheduleDeliveryConnectorLaneTests(_ProductionScheduleWiring):
    """Unmocked seam: real routes over HTTP with the real connector client, poller and worker.

    Only the chat platform sink and the ComfyUI host history/view routes are stand-ins.
    """

    def install_host_routes(self, app: web.Application) -> None:
        self.history: dict[str, dict[str, Any]] = {}

        async def _history(request: web.Request) -> web.Response:
            prompt_id = request.match_info["prompt_id"]
            entry = self.history.get(prompt_id)
            return web.json_response({prompt_id: entry} if entry else {})

        async def _view(_request: web.Request) -> web.Response:
            return web.Response(body=PNG_BYTES, content_type="image/png")

        app.router.add_get("/history/{prompt_id}", _history)
        app.router.add_get("/view", _view)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from connector.config import ConnectorConfig
        from connector.openclaw_client import OpenClawClient
        from connector.results_poller import ResultsPoller
        from connector.schedule_delivery import ScheduleDeliveryWorker

        config = ConnectorConfig()
        config.openclaw_url = str(self.client.make_url("")).rstrip("/")
        config.admin_token = ADMIN_TOKEN
        config.delivery_timeout_sec = 30
        self.sink = _RecordingPlatform()
        platforms: dict[str, Any] = {"telegram": self.sink}
        self.connector_client = OpenClawClient(config)
        await self.connector_client.start()
        poller = ResultsPoller(config, self.connector_client, platforms)
        self.worker = ScheduleDeliveryWorker(
            config, self.connector_client, poller, platforms
        )

    async def asyncTearDown(self) -> None:
        await self.worker.stop()
        await self.connector_client.close()
        await super().asyncTearDown()

    async def schedule_one_run(self) -> dict[str, Any]:
        schedule_id = await self.create_schedule(dict(TELEGRAM_TOPIC))
        await self.run_manually(schedule_id)
        [intent] = await self.list_deliveries()
        return intent

    async def test_scheduled_result_reaches_the_chat_target_exactly_once(self) -> None:
        intent = await self.schedule_one_run()
        self.history[intent["prompt_id"]] = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "probe.png", "subfolder": "", "type": "output"}
                    ]
                }
            },
            "status": {"status_str": "success", "completed": True},
        }

        self.assertEqual(await self.worker.poll_once(), 1)
        await self.worker.drain()

        self.assertEqual(
            self.sink.images,
            [
                (
                    TELEGRAM_TOPIC["target_id"],
                    PNG_BYTES,
                    "probe.png",
                    {"thread_id": "42", "mode": "reply"},
                )
            ],
        )
        self.assertEqual(self.sink.messages, [])
        [after] = await self.list_deliveries()
        self.assertEqual((after["state"], after["reason"]), ("sent", None))

        self.assertEqual(await self.worker.poll_once(), 0)
        await self.worker.drain()
        self.assertEqual(len(self.sink.images), 1)

    async def test_failed_job_sends_a_bounded_alert_and_is_recorded(self) -> None:
        intent = await self.schedule_one_run()
        self.history[intent["prompt_id"]] = {
            "outputs": {},
            "status": {"status_str": "error", "completed": False},
        }

        self.assertEqual(await self.worker.poll_once(), 1)
        await self.worker.drain()

        self.assertEqual(self.sink.images, [])
        self.assertEqual(len(self.sink.messages), 1)
        target, text, context = self.sink.messages[0]
        self.assertEqual(target, TELEGRAM_TOPIC["target_id"])
        self.assertEqual(text, f"\u26a0\ufe0f Scheduled run {intent['run_id']} failed.")
        self.assertEqual(context, {"thread_id": "42", "mode": "reply"})
        [after] = await self.list_deliveries()
        self.assertEqual((after["state"], after["reason"]), ("job_failed", "job_error"))


if __name__ == "__main__":
    unittest.main()
