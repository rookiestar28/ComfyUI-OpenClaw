"""
R65 structured logging (opt-in).

Provides a JSON formatter and a small helper to emit bounded metadata-only
structured events. Default behavior remains unchanged unless explicitly enabled.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .env_aliases import get_env_value

_CONFIGURED_LOGGERS: set[str] = set()
_LOCK = threading.RLock()


def is_structured_logging_enabled() -> bool:
    value = (get_env_value("OPENCLAW_LOG_FORMAT", default="") or "").strip().lower()
    if value == "json":
        return True
    flag = (get_env_value("OPENCLAW_STRUCTURED_LOGS", default="") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


class OpenClawJsonFormatter(logging.Formatter):
    """JSON formatter for OpenClaw logs (opt-in)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "openclaw_event", None)
        if event:
            payload["event"] = str(event)
        fields = getattr(record, "openclaw_fields", None)
        if isinstance(fields, dict) and fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logger_for_structured_output(logger: logging.Logger) -> bool:
    """
    Replace existing handler formatters with JSON formatter when opt-in is enabled.
    Returns True when formatter was applied this call.
    """
    if not is_structured_logging_enabled():
        return False
    with _LOCK:
        if logger.name in _CONFIGURED_LOGGERS:
            return False
        formatter = OpenClawJsonFormatter()
        for handler in logger.handlers:
            try:
                handler.setFormatter(formatter)
            except Exception:
                continue
        _CONFIGURED_LOGGERS.add(logger.name)
        return True


def _sanitize_value(value: Any, *, max_len: int = 256) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
        if len(text) > max_len:
            return text[:max_len] + "...[truncated]"
        return text
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v, max_len=max_len) for v in list(value)[:20]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 20:
                out["__truncated__"] = True
                break
            out[str(k)] = _sanitize_value(v, max_len=max_len)
        return out
    return str(value)[:max_len]


def emit_structured_log(
    logger: logging.Logger,
    *,
    level: int,
    event: str,
    message: Optional[str] = None,
    fields: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Emit a structured metadata-only log record.

    The record is emitted regardless of formatter mode; JSON structure is visible
    when `configure_logger_for_structured_output(...)` has been applied.
    """
    if not is_structured_logging_enabled():
        return
    safe_fields = _sanitize_value(fields or {})
    if not isinstance(safe_fields, dict):
        safe_fields = {"value": safe_fields}
    logger.log(
        level,
        message or event,
        extra={"openclaw_event": event, "openclaw_fields": safe_fields},
    )


def reset_structured_logging_state_for_tests() -> None:
    with _LOCK:
        _CONFIGURED_LOGGERS.clear()
