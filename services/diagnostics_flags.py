"""
Diagnostics Flags Service (R46).

Provides a centralized way to enable scoped diagnostic logging without leaking secrets.
Controlled via OPENCLAW_DIAGNOSTICS environment variable (e.g., "webhook.*,templates.render").
"""

import fnmatch
import logging
from typing import Set

from .env_aliases import get_env_value
from .redaction import redact_dict_safe, redact_text

# Default logger for this module
logger = logging.getLogger("ComfyUI-OpenClaw.services.diagnostics_flags")


class DiagnosticsManager:
    """Manages active diagnostic flags and provides safe logging helpers."""

    def __init__(self):
        self._patterns: Set[str] = set()
        self._enabled_cache: dict[str, bool] = {}
        self.reload()

    def reload(self):
        """Reload flags from environment variable."""
        raw = get_env_value("OPENCLAW_DIAGNOSTICS", default="") or ""

        self._patterns = {p.strip() for p in raw.split(",") if p.strip()}
        self._enabled_cache = {}
        if self._patterns:
            logger.info(f"Diagnostics enabled for: {self._patterns}")

    def is_enabled(self, subsystem: str) -> bool:
        """
        Check if diagnostics are enabled for a subsystem.
        Uses glob matching (fnmatch).
        """
        if subsystem in self._enabled_cache:
            return self._enabled_cache[subsystem]

        enabled = False
        for pattern in self._patterns:
            if fnmatch.fnmatch(subsystem, pattern):
                enabled = True
                break

        self._enabled_cache[subsystem] = enabled
        return enabled

    def get_logger(self, name: str, subsystem: str = "") -> "ScopedLogger":
        """
        Get a logger wrapper that conditionally logs and enforces redaction.
        If subsystem is empty, uses 'name' as default subsystem.
        """
        target_subsystem = subsystem or name
        # Strip common prefixes for shorter subsystem names if desired
        return ScopedLogger(logging.getLogger(name), target_subsystem, self)


class ScopedLogger:
    """
    Wrapper around python logger that:
    1. Checks if diagnostics are enabled for this subsystem.
    2. Redacts sensitive data before logging.
    """

    def __init__(
        self, logger: logging.Logger, subsystem: str, manager: DiagnosticsManager
    ):
        self._logger = logger
        self._subsystem = subsystem
        self._manager = manager

    def is_debug_enabled(self) -> bool:
        return self._manager.is_enabled(self._subsystem)

    def debug(self, msg: str, data: dict | None = None, **kwargs):
        """
        Log at INFO level (forced) if diagnostics enabled, ensuring visibility.
        Redacts 'data' dict safe-by-default.
        """
        if not self.is_debug_enabled():
            return

        # Prepare message
        prefix = f"[DIAG:{self._subsystem}]"

        # Reduct data if present
        if data:
            safe_data = redact_dict_safe(data)
            # Serialize for clarity
            import json

            try:
                # Use default str for non-serializable objects
                json_part = json.dumps(safe_data, default=str)
            except Exception:
                json_part = str(safe_data)

            self._logger.info(f"{prefix} {msg} | Data: {json_part}", **kwargs)
        else:
            self._logger.info(f"{prefix} {msg}", **kwargs)

    def error(self, msg: str, exc_info=True, **kwargs):
        """Always log errors, but respect standard logger."""
        self._logger.error(msg, exc_info=exc_info, **kwargs)

    def warning(self, msg: str, **kwargs):
        """Pass through standard warning."""
        self._logger.warning(msg, **kwargs)

    def exception(self, msg: str, **kwargs):
        """Pass through exception (error with stack trace)."""
        self._logger.exception(msg, **kwargs)

    def info(self, msg: str, **kwargs):
        """Pass through standard info."""
        self._logger.info(msg, **kwargs)


# Global singleton
diagnostics = DiagnosticsManager()
