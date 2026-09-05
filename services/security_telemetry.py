"""
R102: Security telemetry + alert contract.
Defines bounded anomaly event schema and deterministic anomaly producers.
"""

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .audit_events import build_audit_event, emit_audit_event
from .env_aliases import EnvLookupMode, resolve_env

logger = logging.getLogger("ComfyUI-OpenClaw.services.security_telemetry")

# Anomaly Codes
ANOMALY_AUTH_FAILURE_SPIKE = "SEC-001"
ANOMALY_REPLAY_BURST = "SEC-002"
ANOMALY_DANGEROUS_OVERRIDE = "SEC-003"
ANOMALY_QUEUE_SATURATION = "SEC-004"

# Thresholds (default) -> moved to configuration in future
THRESHOLDS = {
    ANOMALY_AUTH_FAILURE_SPIKE: {"count": 10, "window": 60},  # 10 failures in 60s
    ANOMALY_REPLAY_BURST: {"count": 20, "window": 10},  # 20 replays in 10s
    ANOMALY_QUEUE_SATURATION: {
        "count": 100,
        "window": 300,
    },  # 100 queued items sustained? No, simple count check
}

TELEMETRY_OPT_OUT_ENV_KEYS = (
    "OPENCLAW_TELEMETRY_OPT_OUT",
    "MOLTBOT_TELEMETRY_OPT_OUT",
)


def _is_truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def is_security_telemetry_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """S9: Explicit opt-out gate for security anomaly telemetry emission."""
    env_map = env or os.environ
    # IMPORTANT: preserve presence-based canonical precedence. A present false canonical flag must
    # suppress a true legacy opt-out instead of falling through to it.
    resolution = resolve_env(
        TELEMETRY_OPT_OUT_ENV_KEYS[0],
        aliases=TELEMETRY_OPT_OUT_ENV_KEYS[1:],
        mode=EnvLookupMode.PRESENCE,
        env=env_map,
        warn_legacy=env is None,
    )
    if resolution.selected_key is None:
        return True
    return not _is_truthy(resolution.value or "")


@dataclass
class AnomalyEvent:
    code: str
    severity: str  # "low", "medium", "high", "critical"
    source: str
    count: int
    window: float
    action: str  # "monitor", "block", "alert"

    def to_dict(self) -> Dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "count": self.count,
            "window": self.window,
            "action": self.action,
        }


class TimeWindowCounter:
    """Tracks event counts within a sliding time window."""

    def __init__(self, window_seconds: float):
        self.window_seconds = window_seconds
        self.timestamps: Deque[float] = deque()

    def add(self):
        now = time.time()
        self.timestamps.append(now)
        self._prune(now)

    def count(self) -> int:
        self._prune(time.time())
        return len(self.timestamps)

    def _prune(self, now: float):
        while self.timestamps and (now - self.timestamps[0] > self.window_seconds):
            self.timestamps.popleft()


class SecurityTelemetry:
    def __init__(self):
        self._auth_failure_counter = TimeWindowCounter(
            THRESHOLDS[ANOMALY_AUTH_FAILURE_SPIKE]["window"]
        )
        self._replay_counter = TimeWindowCounter(
            THRESHOLDS[ANOMALY_REPLAY_BURST]["window"]
        )
        # Suppress duplicate alerts for a short period
        self._last_alert_time: Dict[str, float] = {}

    def record_auth_failure(self, source_ip: str):
        """Record an authentication failure."""
        self._auth_failure_counter.add()
        count = self._auth_failure_counter.count()
        threshold = THRESHOLDS[ANOMALY_AUTH_FAILURE_SPIKE]["count"]

        if count >= threshold:
            self._trigger_anomaly(
                ANOMALY_AUTH_FAILURE_SPIKE,
                "medium",
                source=f"auth_module:{source_ip}",
                count=count,
                window=THRESHOLDS[ANOMALY_AUTH_FAILURE_SPIKE]["window"],
                action="alert",
            )

    def record_replay_rejection(self, source: str):
        """Record a replay attack rejection."""
        self._replay_counter.add()
        count = self._replay_counter.count()
        threshold = THRESHOLDS[ANOMALY_REPLAY_BURST]["count"]

        if count >= threshold:
            self._trigger_anomaly(
                ANOMALY_REPLAY_BURST,
                "high",
                source=source,
                count=count,
                window=THRESHOLDS[ANOMALY_REPLAY_BURST]["window"],
                action="block",
            )

    def record_dangerous_override(self, override_key: str, user: str):
        """Record usage of a dangerous override (always an anomaly)."""
        self._trigger_anomaly(
            ANOMALY_DANGEROUS_OVERRIDE,
            "medium",
            source=f"{user}:{override_key}",
            count=1,
            window=0,
            action="monitor",
        )

    def record_queue_saturation(self, queue_size: int):
        """Record queue saturation event."""
        # This might be called periodically by a queue monitor
        if queue_size > 1000:  # specific hardcoded limit for now
            self._trigger_anomaly(
                ANOMALY_QUEUE_SATURATION,
                "low",
                source="job_queue",
                count=queue_size,
                window=0,
                action="monitor",
            )

    def _trigger_anomaly(
        self,
        code: str,
        severity: str,
        source: str,
        count: int,
        window: float,
        action: str,
    ):
        if not is_security_telemetry_enabled():
            # IMPORTANT: S9 opt-out disables anomaly telemetry emission by contract.
            return

        # Debounce alerts: don't fire same alert code for same source too often (e.g., every 10s)
        alert_key = f"{code}:{source}"
        now = time.time()
        if now - self._last_alert_time.get(alert_key, 0) < 10:
            return

        self._last_alert_time[alert_key] = now

        anomaly = AnomalyEvent(
            code=code,
            severity=severity,
            source=source,
            count=count,
            window=window,
            action=action,
        )

        # Log via Audit Service
        event = build_audit_event(
            event_type="security.anomaly",
            payload=anomaly.to_dict(),
            meta={"component": "SecurityTelemetry"},
        )
        emit_audit_event(event)

        # Also log to structured logger
        logger.warning(f"Security Anomaly Detected: {anomaly.to_dict()}")


# Global singleton
_telemetry_instance = None


def get_security_telemetry() -> SecurityTelemetry:
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = SecurityTelemetry()
    return _telemetry_instance
