"""
Scheduled run delivery worker.

Claims pending schedule delivery intents from the core outbox, waits for the
job result, sends it through the platform adapter that already holds the chat
credentials, and acknowledges a bounded outcome back to the core.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Mapping
from typing import Any

from .config import ConnectorConfig
from .openclaw_client import OpenClawClient
from .results_poller import ResultsPoller

logger = logging.getLogger(__name__)

SCHEDULE_DELIVERY_PLATFORMS = ("discord", "feishu", "slack", "telegram")

OUTCOME_SENT = "sent"
OUTCOME_JOB_FAILED = "job_failed"
OUTCOME_TIMED_OUT = "timed_out"
OUTCOME_UNDELIVERABLE = "undeliverable"

REASON_JOB_ERROR = "job_error"
REASON_RESULT_TIMEOUT = "result_timeout"
REASON_TARGET_NOT_ALLOWED = "target_not_allowed"
REASON_PLATFORM_UNAVAILABLE = "platform_unavailable"
REASON_CONNECTOR_ERROR = "connector_error"

LEASE_GRACE_SEC = 120
CLAIM_BATCH_LIMIT = 10
MAX_ACTIVE_DELIVERIES = 20
ACK_ATTEMPTS = 5
ACK_RETRY_DELAY_SEC = 1.0
MAX_ACK_RETRY_DELAY_SEC = 30.0

_CONTEXT_FIELDS = ("thread_id", "workspace_id", "account_id", "mode")


class ScheduleDeliveryWorker:
    """Poll loop that turns claimed schedule intents into platform sends."""

    def __init__(
        self,
        config: ConnectorConfig,
        client: OpenClawClient,
        poller: ResultsPoller,
        platforms: Mapping[str, Any],
    ) -> None:
        self._config = config
        self._client = client
        self._poller = poller
        self._platforms = platforms
        self._active: dict[str, asyncio.Task[None]] = {}
        self._lease_tokens: dict[str, str] = {}

    @property
    def active_delivery_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    def enabled_platforms(self) -> list[str]:
        return [name for name in SCHEDULE_DELIVERY_PLATFORMS if name in self._platforms]

    def is_enabled(self) -> bool:
        return bool(self._config.admin_token) and bool(self.enabled_platforms())

    @property
    def lease_sec(self) -> int:
        return int(self._config.delivery_timeout_sec) + LEASE_GRACE_SEC

    async def start(self) -> None:
        logger.info(
            "Schedule delivery worker started for %s.",
            ", ".join(self.enabled_platforms()),
        )
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Schedule delivery poll failed: %s", type(exc).__name__)
            await asyncio.sleep(self._config.schedule_delivery_poll_sec)

    async def stop(self) -> None:
        # Cancelled work is not acknowledged; its lease expires and the core re-offers it.
        tasks = list(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait until every delivery started so far has been acknowledged or failed."""
        tasks = list(self._active.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def poll_once(self) -> int:
        """Claim one batch and start a task per new intent; returns the number started."""
        platforms = self.enabled_platforms()
        capacity = MAX_ACTIVE_DELIVERIES - len(self._active)
        if not platforms or capacity <= 0:
            return 0
        res = await self._client.claim_schedule_deliveries(
            platforms, min(CLAIM_BATCH_LIMIT, capacity), self.lease_sec
        )
        if not res.get("ok"):
            logger.warning(
                "Schedule delivery claim rejected (status=%s).", res.get("status")
            )
            return 0
        data = res.get("data")
        items = data.get("deliveries") if isinstance(data, dict) else None
        started = 0
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            delivery_id = item.get("delivery_id")
            lease_token = item.get("lease_token")
            if not isinstance(delivery_id, str) or not isinstance(lease_token, str):
                continue
            # IMPORTANT: a re-offered intent that is still running here keeps one task;
            # the newest lease token replaces the old one so the final ack is not stale.
            self._lease_tokens[delivery_id] = lease_token
            if delivery_id in self._active:
                continue
            task = asyncio.create_task(self._run(delivery_id, item))
            self._active[delivery_id] = task
            task.add_done_callback(functools.partial(self._forget, delivery_id))
            started += 1
        return started

    def _forget(self, delivery_id: str, _task: asyncio.Task[None]) -> None:
        self._active.pop(delivery_id, None)
        self._lease_tokens.pop(delivery_id, None)

    async def _run(self, delivery_id: str, item: dict[str, Any]) -> None:
        try:
            outcome, reason = await self._deliver(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Schedule delivery %s failed in the connector: %s",
                delivery_id,
                type(exc).__name__,
            )
            outcome, reason = OUTCOME_UNDELIVERABLE, REASON_CONNECTOR_ERROR
        await self._ack(delivery_id, outcome, reason)

    async def _deliver(self, item: dict[str, Any]) -> tuple[str, str | None]:
        raw_delivery = item.get("delivery")
        delivery: Mapping[str, Any] = (
            raw_delivery if isinstance(raw_delivery, dict) else {}
        )
        platform_name = str(delivery.get("platform") or "")
        target_id = str(delivery.get("target_id") or "")
        prompt_id = str(item.get("prompt_id") or "")
        run_id = str(item.get("run_id") or "")
        if platform_name not in self._platforms or not target_id or not prompt_id:
            return OUTCOME_UNDELIVERABLE, REASON_PLATFORM_UNAVAILABLE
        if not self._target_allowed(platform_name, target_id):
            return OUTCOME_UNDELIVERABLE, REASON_TARGET_NOT_ALLOWED

        context = {
            key: delivery[key]
            for key in _CONTEXT_FIELDS
            if delivery.get(key) not in (None, "")
        }
        job = await self._poller.wait_for_job(prompt_id)
        if job is None:
            await self._poller.send_text(
                platform_name,
                target_id,
                f"⚠️ Scheduled run {run_id} timed out waiting for results.",
                delivery_context=context,
            )
            return OUTCOME_TIMED_OUT, REASON_RESULT_TIMEOUT
        if _job_failed(job):
            if delivery.get("failure_alert") is True:
                await self._poller.send_text(
                    platform_name,
                    target_id,
                    f"⚠️ Scheduled run {run_id} failed.",
                    delivery_context=context,
                )
            return OUTCOME_JOB_FAILED, REASON_JOB_ERROR
        await self._poller.deliver_job_results(
            prompt_id, job, platform_name, target_id, delivery_context=context
        )
        return OUTCOME_SENT, None

    def _target_allowed(self, platform_name: str, target_id: str) -> bool:
        """An empty connector allowlist means the platform has no target restriction."""
        allowed: list[Any]
        if platform_name == "telegram":
            allowed = list(self._config.telegram_allowed_chats)
        elif platform_name == "discord":
            allowed = list(self._config.discord_allowed_channels)
        elif platform_name == "slack":
            allowed = list(self._config.slack_allowed_channels)
        elif platform_name == "feishu":
            allowed = list(self._config.feishu_allowed_chats)
        else:
            return False
        return not allowed or target_id in {str(value) for value in allowed}

    async def _ack(self, delivery_id: str, outcome: str, reason: str | None) -> None:
        for attempt in range(1, ACK_ATTEMPTS + 1):
            lease_token = self._lease_tokens.get(delivery_id)
            if lease_token is None:
                return
            res = await self._client.ack_schedule_delivery(
                delivery_id, lease_token, outcome, reason
            )
            if res.get("ok"):
                logger.info(
                    "Schedule delivery %s acknowledged: %s", delivery_id, outcome
                )
                return
            status = res.get("status")
            # IMPORTANT: a lost ack re-offers an already sent result after the lease
            # expires, so throttling (429), server errors and transport failures are
            # retried; any other 4xx answer (stale lease, bad request) is final.
            if isinstance(status, int) and status < 500 and status != 429:
                logger.warning(
                    "Schedule delivery %s ack rejected (status=%s).",
                    delivery_id,
                    status,
                )
                return
            if attempt < ACK_ATTEMPTS:
                await asyncio.sleep(_ack_retry_delay(res, attempt))
        logger.warning(
            "Schedule delivery %s ack failed; the lease will expire and be re-offered.",
            delivery_id,
        )


def _ack_retry_delay(res: Mapping[str, Any], attempt: int) -> float:
    """Honor the core's bounded retry hint on throttling; otherwise back off linearly."""
    data = res.get("data")
    hint = data.get("retry_after_sec") if isinstance(data, dict) else None
    if res.get("status") == 429 and isinstance(hint, (int, float)) and hint > 0:
        return float(min(hint, MAX_ACK_RETRY_DELAY_SEC))
    return ACK_RETRY_DELAY_SEC * attempt


def _job_failed(job: Mapping[str, Any]) -> bool:
    status = job.get("status")
    return isinstance(status, dict) and status.get("status_str") == "error"
