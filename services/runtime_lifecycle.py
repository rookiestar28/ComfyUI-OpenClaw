"""
R67 runtime lifecycle hooks for shutdown/reset consistency.

Centralizes flush-first shutdown/reset behavior for scheduler/failover runtime
state so controlled resets and process shutdown use the same ordering.
"""

from __future__ import annotations

import atexit
import logging
import threading
from typing import Any, Dict, List

logger = logging.getLogger("ComfyUI-OpenClaw.services.runtime_lifecycle")

_shutdown_hook_registered = False
_shutdown_lock = threading.RLock()


def flush_runtime_state(*, stop_scheduler_runner: bool = True) -> Dict[str, Any]:
    """
    Flush runtime state for scheduler/failover paths.

    Returns a small diagnostics report for testing/operational visibility.
    """
    report: Dict[str, Any] = {"ok": True, "steps": []}

    def _step(name: str, fn) -> None:
        try:
            result = fn()
            report["steps"].append({"name": name, "ok": True, "result": result})
        except Exception as e:
            report["ok"] = False
            report["steps"].append({"name": name, "ok": False, "error": str(e)})
            logger.exception("R67: runtime lifecycle step failed (%s)", name)

    if stop_scheduler_runner:
        _step(
            "scheduler.stop",
            lambda: _import_runner_module().stop_scheduler(),
        )

    _step(
        "scheduler.store.flush",
        lambda: _import_schedule_storage().get_schedule_store().flush(),
    )
    _step(
        "scheduler.history.flush",
        lambda: _import_scheduler_history().get_run_history().flush(),
    )
    _step("failover.flush", lambda: _import_failover().get_failover_state().flush())

    return report


def reset_runtime_state(*, flush_first: bool = True) -> Dict[str, Any]:
    """
    Controlled reset hook for tests/admin reset flows.

    Flushes runtime state first, then resets scheduler/failover singletons in a
    deterministic order. Returns diagnostics including flush report.
    """
    report: Dict[str, Any] = {"ok": True, "flush": None, "reset_steps": []}
    if flush_first:
        report["flush"] = flush_runtime_state(stop_scheduler_runner=True)
        report["ok"] = bool(report["flush"].get("ok", False))

    resets: List[tuple[str, Any]] = [
        (
            "scheduler.runner.reset",
            lambda: _import_runner_module().reset_scheduler_runner(stop=False),
        ),
        (
            "scheduler.store.reset",
            lambda: _import_schedule_storage().reset_schedule_store(flush=False),
        ),
        (
            "scheduler.history.reset",
            lambda: _import_scheduler_history().reset_run_history(flush=False),
        ),
        (
            "scheduler.delivery_outbox.reset",
            lambda: _import_delivery_outbox().reset_delivery_outbox(),
        ),
        (
            "failover.reset",
            lambda: _import_failover().reset_failover_state(flush=False),
        ),
    ]

    for name, fn in resets:
        try:
            fn()
            report["reset_steps"].append({"name": name, "ok": True})
        except Exception as e:
            report["ok"] = False
            report["reset_steps"].append({"name": name, "ok": False, "error": str(e)})
            logger.exception("R67: runtime reset step failed (%s)", name)
    return report


def register_shutdown_hooks() -> bool:
    """Register process shutdown hook once (best effort, idempotent)."""
    global _shutdown_hook_registered
    with _shutdown_lock:
        if _shutdown_hook_registered:
            return False
        atexit.register(_atexit_shutdown_hook)
        _shutdown_hook_registered = True
        logger.debug("R67: Registered runtime shutdown hook")
        return True


def _atexit_shutdown_hook() -> None:
    """Best-effort shutdown flush for process exit."""
    try:
        flush_runtime_state(stop_scheduler_runner=True)
    except Exception:
        logger.exception("R67: atexit runtime shutdown hook failed")


def reset_shutdown_hook_registration_for_tests() -> None:
    """Test helper to reset in-memory registration state."""
    global _shutdown_hook_registered
    with _shutdown_lock:
        _shutdown_hook_registered = False


def _import_runner_module():
    try:
        from .scheduler import runner as _runner
    except ImportError:
        from services.scheduler import runner as _runner  # type: ignore
    return _runner


def _import_schedule_storage():
    try:
        from .scheduler import storage as _storage
    except ImportError:
        from services.scheduler import storage as _storage  # type: ignore
    return _storage


def _import_scheduler_history():
    try:
        from .scheduler import history as _history
    except ImportError:
        from services.scheduler import history as _history  # type: ignore
    return _history


def _import_delivery_outbox():
    # The outbox writes every mutation through, so reset needs no flush step.
    from .scheduler import delivery_outbox as _outbox

    return _outbox


def _import_failover():
    try:
        from . import failover as _failover_mod
    except ImportError:
        import services.failover as _failover_mod  # type: ignore
    return _failover_mod
