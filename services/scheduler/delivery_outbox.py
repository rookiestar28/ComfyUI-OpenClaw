"""
Durable outbox for schedule delivery targets.

A schedule run that has an enabled delivery target records one intent here. The connector
process, which holds the chat platform credentials, claims intents with a time-boxed lease,
delivers the job result and acknowledges the outcome. Intents are data on disk, so they
outlive the short-lived event loop that submits scheduled prompts and process restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..state_dir import get_state_dir
from ..tenant_context import DEFAULT_TENANT_ID

logger = logging.getLogger("ComfyUI-OpenClaw.services.scheduler.delivery_outbox")

STATE_PENDING = "pending"
STATE_CLAIMED = "claimed"
OUTCOME_SENT = "sent"
OUTCOME_JOB_FAILED = "job_failed"
OUTCOME_TIMED_OUT = "timed_out"
OUTCOME_UNDELIVERABLE = "undeliverable"
STATE_EXPIRED = "expired"

ACK_OUTCOMES = frozenset(
    {OUTCOME_SENT, OUTCOME_JOB_FAILED, OUTCOME_TIMED_OUT, OUTCOME_UNDELIVERABLE}
)
TERMINAL_STATES = ACK_OUTCOMES | {STATE_EXPIRED}
ALL_STATES = TERMINAL_STATES | {STATE_PENDING, STATE_CLAIMED}

# Reason codes are a closed set so that no target, message or error text is stored.
ACK_REASONS: Mapping[str, frozenset[str]] = {
    OUTCOME_SENT: frozenset(),
    OUTCOME_JOB_FAILED: frozenset({"job_error"}),
    OUTCOME_TIMED_OUT: frozenset({"result_timeout"}),
    OUTCOME_UNDELIVERABLE: frozenset(
        {"target_not_allowed", "platform_unavailable", "connector_error"}
    ),
}
REASON_NO_CONNECTOR_ADAPTER = "no_connector_adapter"
REASON_NOT_CLAIMED = "not_claimed"
REASON_CAPACITY = "capacity"

# Platforms the connector can deliver to; other schedule targets have no adapter.
CONNECTOR_PLATFORMS = frozenset({"discord", "feishu", "slack", "telegram"})

PENDING_TTL_SEC = 24 * 3600
TERMINAL_RETENTION_SEC = 7 * 24 * 3600
MAX_RECORDS = 2000
MIN_LEASE_SEC = 60
MAX_LEASE_SEC = 7200
MAX_CLAIM_BATCH = 10
_DELIVERY_FIELDS = (
    "platform",
    "target_id",
    "thread_id",
    "workspace_id",
    "account_id",
    "mode",
    "failure_alert",
)


class DeliveryOutboxError(ValueError):
    """Raised for an invalid outbox operation; ``code`` is a bounded identifier."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def delivery_id_for(idempotency_key: str) -> str:
    """Stable intent id for one scheduled tick (one idempotency key)."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"dlv_{digest[:24]}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeliveryIntent:
    delivery_id: str
    run_id: str
    schedule_id: str
    prompt_id: str
    tenant_id: str
    delivery: Mapping[str, Any]
    state: str
    created_at: float
    updated_at: float
    reason: str | None = None
    attempts: int = 0
    lease_expires_at: float | None = None
    lease_token_hash: str | None = field(default=None, repr=False)

    def to_storage(self) -> dict[str, Any]:
        data = asdict(self)
        data["delivery"] = dict(self.delivery)
        return data

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> DeliveryIntent:
        return cls(
            delivery_id=str(data["delivery_id"]),
            run_id=str(data["run_id"]),
            schedule_id=str(data["schedule_id"]),
            prompt_id=str(data["prompt_id"]),
            tenant_id=str(data.get("tenant_id") or DEFAULT_TENANT_ID),
            delivery=dict(data.get("delivery") or {}),
            state=str(data["state"]),
            created_at=float(data["created_at"]),
            updated_at=float(data["updated_at"]),
            reason=data.get("reason"),
            attempts=int(data.get("attempts", 0)),
            lease_expires_at=data.get("lease_expires_at"),
            lease_token_hash=data.get("lease_token_hash"),
        )

    def to_public(self) -> dict[str, Any]:
        """Response shape: normalized target identifiers only, never the lease hash."""
        return {
            "delivery_id": self.delivery_id,
            "run_id": self.run_id,
            "schedule_id": self.schedule_id,
            "prompt_id": self.prompt_id,
            "state": self.state,
            "reason": self.reason,
            "delivery": dict(self.delivery),
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lease_expires_at": self.lease_expires_at,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "state": self.state,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ClaimedIntent:
    intent: DeliveryIntent
    lease_token: str

    def to_public(self) -> dict[str, Any]:
        data = self.intent.to_public()
        data["lease_token"] = self.lease_token
        return data


@dataclass(frozen=True)
class AckResult:
    intent: DeliveryIntent
    repeated: bool


def _delivery_target(delivery: Mapping[str, Any]) -> dict[str, Any]:
    return {key: delivery[key] for key in _DELIVERY_FIELDS if key in delivery}


class DeliveryOutbox:
    """JSON-backed intent store; every mutation is written atomically under a lock."""

    def __init__(self, path: str | None = None, clock=time.time):
        self._path = path or os.path.join(
            get_state_dir(), "scheduler", "delivery_outbox.json"
        )
        self._clock = clock
        self._lock = threading.RLock()
        self._intents: dict[str, DeliveryIntent] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as handle:
                rows = json.load(handle)
            for row in rows if isinstance(rows, list) else []:
                intent = DeliveryIntent.from_storage(row)
                if intent.state in ALL_STATES:
                    self._intents[intent.delivery_id] = intent
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Keep serving; a corrupt file must not stop schedule execution.
            logger.error(
                "Failed to load schedule delivery outbox: %s", type(exc).__name__
            )

    def _save(self) -> None:
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        rows = [intent.to_storage() for intent in self._intents.values()]
        fd, temp_path = tempfile.mkstemp(
            suffix=".json", dir=directory, prefix="outbox_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(rows, handle, indent=2, ensure_ascii=False)
            os.replace(temp_path, self._path)
        except BaseException:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _maintain(self, now: float) -> bool:
        """Expire unclaimed intents and prune old terminal records; True when changed."""
        changed = False
        for intent in list(self._intents.values()):
            if (
                intent.state == STATE_PENDING
                and now - intent.created_at > PENDING_TTL_SEC
            ):
                self._intents[intent.delivery_id] = replace(
                    intent,
                    state=STATE_EXPIRED,
                    reason=REASON_NOT_CLAIMED,
                    updated_at=now,
                )
                changed = True
            elif (
                intent.state in TERMINAL_STATES
                and now - intent.updated_at > TERMINAL_RETENTION_SEC
            ):
                del self._intents[intent.delivery_id]
                changed = True
        overflow = len(self._intents) - MAX_RECORDS
        if overflow > 0:
            by_age = sorted(self._intents.values(), key=lambda item: item.updated_at)
            terminal_first = [i for i in by_age if i.state in TERMINAL_STATES] + [
                i for i in by_age if i.state not in TERMINAL_STATES
            ]
            for intent in terminal_first[:overflow]:
                if intent.state in TERMINAL_STATES:
                    del self._intents[intent.delivery_id]
                else:
                    # Evicting live work must stay visible, never silent.
                    logger.warning(
                        "Schedule delivery outbox full; expiring %s", intent.delivery_id
                    )
                    self._intents[intent.delivery_id] = replace(
                        intent,
                        state=STATE_EXPIRED,
                        reason=REASON_CAPACITY,
                        updated_at=now,
                    )
            changed = True
        return changed

    def record(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        schedule_id: str,
        prompt_id: str,
        delivery: Mapping[str, Any],
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> DeliveryIntent:
        """Record one intent per scheduled tick; repeated calls return the existing one."""
        delivery_id = delivery_id_for(idempotency_key)
        with self._lock:
            self._ensure_loaded()
            existing = self._intents.get(delivery_id)
            if existing is not None:
                return existing
            now = self._clock()
            target = _delivery_target(delivery)
            adapter_missing = target.get("platform") not in CONNECTOR_PLATFORMS
            intent = DeliveryIntent(
                delivery_id=delivery_id,
                run_id=run_id,
                schedule_id=schedule_id,
                prompt_id=prompt_id,
                tenant_id=tenant_id,
                delivery=target,
                state=OUTCOME_UNDELIVERABLE if adapter_missing else STATE_PENDING,
                reason=REASON_NO_CONNECTOR_ADAPTER if adapter_missing else None,
                created_at=now,
                updated_at=now,
            )
            self._intents[delivery_id] = intent
            self._maintain(now)
            self._save()
            return intent

    def list_intents(
        self,
        *,
        tenant_id: str,
        state: str | None = None,
        platform: str | None = None,
        limit: int = 50,
    ) -> list[DeliveryIntent]:
        with self._lock:
            self._ensure_loaded()
            if self._maintain(self._clock()):
                self._save()
            matches = [
                intent
                for intent in self._intents.values()
                if intent.tenant_id == tenant_id
                and (state is None or intent.state == state)
                and (platform is None or intent.delivery.get("platform") == platform)
            ]
        matches.sort(key=lambda intent: intent.created_at, reverse=True)
        return matches[: max(0, limit)]

    def summaries_for_keys(
        self, idempotency_keys: Iterable[str], *, tenant_id: str
    ) -> dict[str, dict[str, Any]]:
        """Delivery summaries keyed by schedule idempotency key (run history join)."""
        with self._lock:
            self._ensure_loaded()
            result: dict[str, dict[str, Any]] = {}
            for key in idempotency_keys:
                intent = self._intents.get(delivery_id_for(key))
                if intent is not None and intent.tenant_id == tenant_id:
                    result[key] = intent.summary()
            return result

    def claim(
        self,
        *,
        tenant_id: str,
        platforms: Iterable[str],
        limit: int,
        lease_sec: int,
    ) -> list[ClaimedIntent]:
        """Lease up to ``limit`` pending (or lease-expired) intents for these platforms."""
        wanted = {p for p in platforms if p in CONNECTOR_PLATFORMS}
        batch = max(1, min(int(limit), MAX_CLAIM_BATCH))
        lease = max(MIN_LEASE_SEC, min(int(lease_sec), MAX_LEASE_SEC))
        claimed: list[ClaimedIntent] = []
        with self._lock:
            self._ensure_loaded()
            now = self._clock()
            changed = self._maintain(now)
            candidates = sorted(
                (
                    intent
                    for intent in self._intents.values()
                    if intent.tenant_id == tenant_id
                    and intent.delivery.get("platform") in wanted
                    and (
                        intent.state == STATE_PENDING
                        or (
                            intent.state == STATE_CLAIMED
                            and (intent.lease_expires_at or 0) <= now
                        )
                    )
                ),
                key=lambda intent: intent.created_at,
            )
            for intent in candidates[:batch]:
                token = secrets.token_urlsafe(24)
                leased = replace(
                    intent,
                    state=STATE_CLAIMED,
                    attempts=intent.attempts + 1,
                    lease_expires_at=now + lease,
                    lease_token_hash=_hash_token(token),
                    updated_at=now,
                )
                self._intents[intent.delivery_id] = leased
                claimed.append(ClaimedIntent(intent=leased, lease_token=token))
            if claimed or changed:
                self._save()
        return claimed

    def ack(
        self,
        *,
        tenant_id: str,
        delivery_id: str,
        lease_token: str,
        outcome: str,
        reason: str | None,
    ) -> AckResult:
        """Accept one terminal outcome from the current lease holder."""
        if outcome not in ACK_OUTCOMES:
            raise DeliveryOutboxError("invalid_outcome", "unsupported outcome")
        allowed_reasons = ACK_REASONS[outcome]
        if reason is None and allowed_reasons:
            reason = sorted(allowed_reasons)[0] if len(allowed_reasons) == 1 else None
        if (reason is None and allowed_reasons) or (
            reason is not None and reason not in allowed_reasons
        ):
            raise DeliveryOutboxError(
                "invalid_reason", "unsupported reason for outcome"
            )
        if not lease_token:
            raise DeliveryOutboxError("lease_token_required", "lease_token is required")
        token_hash = _hash_token(lease_token)
        with self._lock:
            self._ensure_loaded()
            intent = self._intents.get(delivery_id)
            if intent is None or intent.tenant_id != tenant_id:
                raise DeliveryOutboxError("not_found", "delivery not found")
            if intent.lease_token_hash != token_hash:
                raise DeliveryOutboxError("lease_mismatch", "lease is not held")
            if intent.state in TERMINAL_STATES:
                if intent.state == outcome and intent.reason == reason:
                    return AckResult(intent=intent, repeated=True)
                raise DeliveryOutboxError("already_finalized", "delivery is finalized")
            if intent.state != STATE_CLAIMED:
                raise DeliveryOutboxError("lease_mismatch", "lease is not held")
            final = replace(
                intent, state=outcome, reason=reason, updated_at=self._clock()
            )
            self._intents[delivery_id] = final
            self._save()
            return AckResult(intent=final, repeated=False)

    def _expire_lease_for_tests(self, delivery_id: str) -> None:
        with self._lock:
            intent = self._intents[delivery_id]
            self._intents[delivery_id] = replace(
                intent, lease_expires_at=self._clock() - 1
            )
            self._save()


_outbox: DeliveryOutbox | None = None
_outbox_lock = threading.Lock()


def get_delivery_outbox() -> DeliveryOutbox:
    global _outbox
    with _outbox_lock:
        if _outbox is None:
            _outbox = DeliveryOutbox()
        return _outbox


def reset_delivery_outbox() -> None:
    """Drop the singleton so the next access reloads from disk (tests, admin reset)."""
    global _outbox
    with _outbox_lock:
        _outbox = None
