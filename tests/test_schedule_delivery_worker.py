"""Connector worker that turns claimed schedule delivery intents into platform sends.

The core client and the results poller are replaced with recording doubles; the real
HTTP path is covered by the unmocked lane in ``test_schedule_delivery_execution``.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from connector import config as connector_config
from connector.config import ConnectorConfig
from connector.schedule_delivery import (
    ACK_ATTEMPTS,
    CLAIM_BATCH_LIMIT,
    LEASE_GRACE_SEC,
    MAX_ACK_RETRY_DELAY_SEC,
    ScheduleDeliveryWorker,
    _ack_retry_delay,
)

TELEGRAM_TARGET = {
    "platform": "telegram",
    "target_id": "-1001234567890",
    "thread_id": "42",
    "mode": "reply",
    "failure_alert": True,
}
FINISHED_JOB = {
    "outputs": {"9": {"images": [{"filename": "probe.png", "type": "output"}]}},
    "status": {"status_str": "success", "completed": True},
}
FAILED_JOB = {
    "outputs": {},
    "status": {"status_str": "error", "completed": False},
}


def _offer(
    delivery_id: str = "dlv_000000000000000000000001",
    lease_token: str = "lease-1",
    delivery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "delivery_id": delivery_id,
        "run_id": "run_probe",
        "schedule_id": "sched_probe",
        "prompt_id": "prompt-1",
        "state": "claimed",
        "delivery": dict(TELEGRAM_TARGET if delivery is None else delivery),
        "lease_token": lease_token,
    }


def _claimed(*offers: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "status": 200, "data": {"ok": True, "deliveries": list(offers)}}


class ScheduleDeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = ConnectorConfig()
        self.config.admin_token = "synthetic-connector-admin-token"
        self.config.delivery_timeout_sec = 300
        self.client = MagicMock()
        self.client.claim_schedule_deliveries = AsyncMock(return_value=_claimed())
        self.client.ack_schedule_delivery = AsyncMock(
            return_value={"ok": True, "status": 200}
        )
        self.poller = MagicMock()
        self.poller.wait_for_job = AsyncMock(return_value=FINISHED_JOB)
        self.poller.send_text = AsyncMock()
        self.poller.deliver_job_results = AsyncMock()
        self.platforms: dict[str, Any] = {"telegram": object(), "discord": object()}
        self.worker = ScheduleDeliveryWorker(
            self.config, self.client, self.poller, self.platforms
        )

    async def run_offers(self, *offers: dict[str, Any]) -> int:
        self.client.claim_schedule_deliveries.return_value = _claimed(*offers)
        started = await self.worker.poll_once()
        await self.worker.drain()
        return started

    def assert_acked(self, outcome: str, reason: str | None, token: str = "lease-1"):
        self.client.ack_schedule_delivery.assert_awaited_once_with(
            "dlv_000000000000000000000001", token, outcome, reason
        )

    def test_enablement_needs_admin_token_and_a_schedule_capable_platform(self) -> None:
        self.assertTrue(self.worker.is_enabled())
        self.assertEqual(self.worker.enabled_platforms(), ["discord", "telegram"])

        self.config.admin_token = None
        self.assertFalse(self.worker.is_enabled())

        self.config.admin_token = "synthetic-connector-admin-token"
        line_only = ScheduleDeliveryWorker(
            self.config, self.client, self.poller, {"line": object()}
        )
        self.assertFalse(line_only.is_enabled())

    async def test_claim_names_loaded_platforms_and_outlives_the_result_wait(
        self,
    ) -> None:
        await self.run_offers()

        self.client.claim_schedule_deliveries.assert_awaited_once_with(
            ["discord", "telegram"],
            CLAIM_BATCH_LIMIT,
            self.config.delivery_timeout_sec + LEASE_GRACE_SEC,
        )

    async def test_finished_job_is_sent_to_the_target_thread_and_acked_sent(
        self,
    ) -> None:
        started = await self.run_offers(_offer())

        self.assertEqual(started, 1)
        self.poller.wait_for_job.assert_awaited_once_with("prompt-1")
        self.poller.deliver_job_results.assert_awaited_once_with(
            "prompt-1",
            FINISHED_JOB,
            "telegram",
            "-1001234567890",
            delivery_context={"thread_id": "42", "mode": "reply"},
        )
        self.poller.send_text.assert_not_awaited()
        self.assert_acked("sent", None)

    async def test_failed_job_alerts_only_when_failure_alert_is_enabled(self) -> None:
        self.poller.wait_for_job.return_value = FAILED_JOB
        await self.run_offers(_offer())

        self.poller.deliver_job_results.assert_not_awaited()
        self.poller.send_text.assert_awaited_once()
        platform, target, text = self.poller.send_text.await_args.args
        self.assertEqual((platform, target), ("telegram", "-1001234567890"))
        self.assertIn("run_probe", text)
        self.assert_acked("job_failed", "job_error")

        self.poller.send_text.reset_mock()
        self.client.ack_schedule_delivery.reset_mock()
        quiet = dict(TELEGRAM_TARGET, failure_alert=False)
        await self.run_offers(_offer(delivery=quiet))

        self.poller.send_text.assert_not_awaited()
        self.assert_acked("job_failed", "job_error")

    async def test_result_timeout_notifies_and_acks_timed_out(self) -> None:
        self.poller.wait_for_job.return_value = None
        await self.run_offers(_offer())

        self.poller.send_text.assert_awaited_once()
        self.assertIn("timed out", self.poller.send_text.await_args.args[2])
        self.poller.deliver_job_results.assert_not_awaited()
        self.assert_acked("timed_out", "result_timeout")

    async def test_target_outside_the_connector_allowlist_is_never_sent(self) -> None:
        self.config.telegram_allowed_chats = [-1009999999999]
        await self.run_offers(_offer())

        self.poller.wait_for_job.assert_not_awaited()
        self.poller.send_text.assert_not_awaited()
        self.poller.deliver_job_results.assert_not_awaited()
        self.assert_acked("undeliverable", "target_not_allowed")

    async def test_target_inside_the_connector_allowlist_is_sent(self) -> None:
        self.config.telegram_allowed_chats = [-1001234567890]
        await self.run_offers(_offer())

        self.poller.deliver_job_results.assert_awaited_once()
        self.assert_acked("sent", None)

    async def test_platform_not_loaded_here_is_undeliverable(self) -> None:
        slack = {"platform": "slack", "target_id": "C0123456789", "mode": "reply"}
        await self.run_offers(_offer(delivery=slack))

        self.poller.wait_for_job.assert_not_awaited()
        self.assert_acked("undeliverable", "platform_unavailable")

    async def test_adapter_exception_is_acked_as_connector_error(self) -> None:
        self.poller.deliver_job_results.side_effect = RuntimeError("adapter down")
        await self.run_offers(_offer())

        self.assert_acked("undeliverable", "connector_error")

    async def test_reoffered_intent_keeps_one_send_and_acks_with_the_newest_lease(
        self,
    ) -> None:
        release = asyncio.Event()

        async def _slow_wait(_prompt_id: str) -> dict[str, Any]:
            await release.wait()
            return FINISHED_JOB

        self.poller.wait_for_job.side_effect = _slow_wait
        self.client.claim_schedule_deliveries.return_value = _claimed(_offer())
        self.assertEqual(await self.worker.poll_once(), 1)
        await asyncio.sleep(0)

        self.client.claim_schedule_deliveries.return_value = _claimed(
            _offer(lease_token="lease-2")
        )
        self.assertEqual(await self.worker.poll_once(), 0)

        release.set()
        await self.worker.drain()

        self.poller.wait_for_job.assert_awaited_once()
        self.poller.deliver_job_results.assert_awaited_once()
        self.assert_acked("sent", None, token="lease-2")
        self.assertEqual(self.worker.active_delivery_ids, frozenset())

    async def test_throttled_server_and_transport_ack_failures_are_retried(
        self,
    ) -> None:
        self.client.ack_schedule_delivery.side_effect = [
            {"ok": False, "status": 503},
            {"ok": False, "error": "connection reset"},
            {"ok": False, "status": 429, "data": {"retry_after_sec": 0.01}},
            {"ok": True, "status": 200},
        ]
        with patch("connector.schedule_delivery.ACK_RETRY_DELAY_SEC", 0):
            await self.run_offers(_offer())
        self.assertEqual(self.client.ack_schedule_delivery.await_count, 4)

        self.client.ack_schedule_delivery.reset_mock(side_effect=True)
        self.client.ack_schedule_delivery.return_value = {"ok": False, "status": 503}
        with patch("connector.schedule_delivery.ACK_RETRY_DELAY_SEC", 0):
            await self.run_offers(_offer())
        self.assertEqual(self.client.ack_schedule_delivery.await_count, ACK_ATTEMPTS)

    async def test_ack_rejections_other_than_throttling_are_final(self) -> None:
        self.client.ack_schedule_delivery.return_value = {"ok": False, "status": 409}
        await self.run_offers(_offer())
        self.assertEqual(self.client.ack_schedule_delivery.await_count, 1)

    def test_throttle_retry_hint_is_bounded(self) -> None:
        throttled = {"ok": False, "status": 429, "data": {"retry_after_sec": 900}}
        self.assertEqual(_ack_retry_delay(throttled, 1), MAX_ACK_RETRY_DELAY_SEC)
        self.assertEqual(_ack_retry_delay({"ok": False, "status": 503}, 2), 2.0)
        self.assertEqual(
            _ack_retry_delay({"ok": False, "status": 429, "data": {}}, 3), 3.0
        )

    async def test_rejected_claim_or_malformed_offers_start_nothing(self) -> None:
        self.client.claim_schedule_deliveries.return_value = {
            "ok": False,
            "status": 403,
        }
        self.assertEqual(await self.worker.poll_once(), 0)

        self.client.claim_schedule_deliveries.return_value = _claimed(
            {"delivery_id": "dlv_x"}, "not-a-dict", {"lease_token": "only-token"}
        )
        self.assertEqual(await self.worker.poll_once(), 0)
        self.client.ack_schedule_delivery.assert_not_awaited()

    async def test_capacity_bounds_how_much_is_claimed(self) -> None:
        release = asyncio.Event()

        async def _slow_wait(_prompt_id: str) -> dict[str, Any]:
            await release.wait()
            return FINISHED_JOB

        self.poller.wait_for_job.side_effect = _slow_wait
        self.client.claim_schedule_deliveries.return_value = _claimed(_offer())
        with patch("connector.schedule_delivery.MAX_ACTIVE_DELIVERIES", 1):
            self.assertEqual(await self.worker.poll_once(), 1)
            self.assertEqual(await self.worker.poll_once(), 0)
        self.assertEqual(self.client.claim_schedule_deliveries.await_count, 1)
        release.set()
        await self.worker.drain()

    async def test_stop_cancels_in_flight_work_without_acknowledging(self) -> None:
        never = asyncio.Event()

        async def _blocked_wait(_prompt_id: str) -> dict[str, Any]:
            await never.wait()
            return FINISHED_JOB

        self.poller.wait_for_job.side_effect = _blocked_wait
        self.client.claim_schedule_deliveries.return_value = _claimed(_offer())
        await self.worker.poll_once()
        await asyncio.sleep(0)

        await self.worker.stop()

        self.client.ack_schedule_delivery.assert_not_awaited()
        self.assertEqual(self.worker.active_delivery_ids, frozenset())


class ScheduleDeliveryPollIntervalConfigTests(unittest.TestCase):
    KEY = "OPENCLAW_CONNECTOR_SCHEDULE_DELIVERY_POLL_SEC"

    def load(self, value: str | None) -> ConnectorConfig:
        env = dict(os.environ)
        env.pop(self.KEY, None)
        if value is not None:
            env[self.KEY] = value
        with patch.dict(os.environ, env, clear=True):
            return connector_config.load_config()

    def test_default_and_valid_value(self) -> None:
        self.assertEqual(
            self.load(None).schedule_delivery_poll_sec,
            connector_config.DEFAULT_SCHEDULE_DELIVERY_POLL_SEC,
        )
        self.assertEqual(self.load("30").schedule_delivery_poll_sec, 30)

    def test_out_of_range_values_are_clamped_and_garbage_uses_the_default(self) -> None:
        with self.assertLogs("ComfyUI-OpenClaw.connector.config", level="WARNING"):
            low = self.load("1")
        with self.assertLogs("ComfyUI-OpenClaw.connector.config", level="WARNING"):
            high = self.load("100000")
        with self.assertLogs("ComfyUI-OpenClaw.connector.config", level="WARNING"):
            garbage = self.load("soon")
        self.assertEqual(
            low.schedule_delivery_poll_sec,
            connector_config.MIN_SCHEDULE_DELIVERY_POLL_SEC,
        )
        self.assertEqual(
            high.schedule_delivery_poll_sec,
            connector_config.MAX_SCHEDULE_DELIVERY_POLL_SEC,
        )
        self.assertEqual(
            garbage.schedule_delivery_poll_sec,
            connector_config.DEFAULT_SCHEDULE_DELIVERY_POLL_SEC,
        )


if __name__ == "__main__":
    unittest.main()
