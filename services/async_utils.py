"""
Async utilities.

Why not asyncio.to_thread?
In some constrained environments, asyncio.to_thread (which uses contextvars.copy_context().run)
can hang. This helper uses loop.run_in_executor with a plain functools.partial instead.
"""

import asyncio
import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Literal, TypeVar

from .env_aliases import resolve_env
from .metrics import metrics

T = TypeVar("T")

logger = logging.getLogger("ComfyUI-OpenClaw.services.async_utils")

ExecutorLane = Literal["llm", "io"]

_DEFAULT_LLM_WORKERS = 6
_DEFAULT_IO_WORKERS = 4
_MIN_WORKERS = 1
_MAX_LLM_WORKERS = 12
_MAX_IO_WORKERS = 8
_WAIT_BUCKET_MS = 250


def _parse_worker_count(
    env_key_families: Iterable[tuple[str, tuple[str, ...]]],
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    resolution = None
    for canonical, aliases in env_key_families:
        # IMPORTANT: resolve each canonical/legacy pair separately. Flattening a fallback
        # canonical into aliases mislabels it as legacy and emits a false deprecation warning.
        resolution = resolve_env(canonical, aliases=aliases)
        if resolution.selected_key is not None:
            break
    if resolution is None:
        return default
    raw = resolution.value
    if raw is None or str(raw).strip() == "":
        return default
    selected_key = resolution.selected_key
    assert selected_key is not None
    try:
        parsed = int(str(raw).strip())
    except Exception:
        logger.warning(
            "R129: invalid worker count for %s=%r; using safe default %d",
            selected_key,
            raw,
            default,
        )
        return default
    if parsed < minimum or parsed > maximum:
        logger.warning(
            "R129: out-of-range worker count for %s=%r (allowed %d..%d); using safe default %d",
            selected_key,
            parsed,
            minimum,
            maximum,
            default,
        )
        return default
    return parsed


_LLM_WORKERS = _parse_worker_count(
    (
        ("OPENCLAW_LLM_EXECUTOR_WORKERS", ("MOLTBOT_LLM_EXECUTOR_WORKERS",)),
        # Backward-compatible global fallback for older local env setups.
        ("OPENCLAW_THREAD_POOL_WORKERS", ("MOLTBOT_THREAD_POOL_WORKERS",)),
    ),
    _DEFAULT_LLM_WORKERS,
    minimum=_MIN_WORKERS,
    maximum=_MAX_LLM_WORKERS,
)
_IO_WORKERS = _parse_worker_count(
    (("OPENCLAW_IO_EXECUTOR_WORKERS", ("MOLTBOT_IO_EXECUTOR_WORKERS",)),),
    _DEFAULT_IO_WORKERS,
    minimum=_MIN_WORKERS,
    maximum=_MAX_IO_WORKERS,
)

_LLM_EXECUTOR = ThreadPoolExecutor(
    max_workers=_LLM_WORKERS, thread_name_prefix="openclaw-llm"
)
_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=_IO_WORKERS, thread_name_prefix="openclaw-io"
)


def _executor_for_lane(lane: ExecutorLane) -> ThreadPoolExecutor:
    if lane == "io":
        return _IO_EXECUTOR
    return _LLM_EXECUTOR


def _record_lane_metrics(lane: ExecutorLane, *, submitted_at: float) -> None:
    metrics.increment(f"executor_{lane}_started")
    wait_ms = int(max(0.0, (time.perf_counter() - submitted_at) * 1000))
    metrics.increment(f"executor_{lane}_wait_ms_total", wait_ms)
    if wait_ms >= _WAIT_BUCKET_MS:
        metrics.increment(f"executor_{lane}_wait_over_{_WAIT_BUCKET_MS}ms")


def get_executor_diagnostics() -> Dict[str, Any]:
    """
    Return lightweight runtime diagnostics for executor-lane saturation.
    """
    all_counters = metrics.get_all()
    return {
        "llm": {
            "workers": _LLM_WORKERS,
            "submitted": all_counters.get("executor_llm_submitted", 0),
            "started": all_counters.get("executor_llm_started", 0),
            "completed": all_counters.get("executor_llm_completed", 0),
            "wait_ms_total": all_counters.get("executor_llm_wait_ms_total", 0),
            "wait_over_250ms": all_counters.get("executor_llm_wait_over_250ms", 0),
        },
        "io": {
            "workers": _IO_WORKERS,
            "submitted": all_counters.get("executor_io_submitted", 0),
            "started": all_counters.get("executor_io_started", 0),
            "completed": all_counters.get("executor_io_completed", 0),
            "wait_ms_total": all_counters.get("executor_io_wait_ms_total", 0),
            "wait_over_250ms": all_counters.get("executor_io_wait_over_250ms", 0),
        },
    }


async def run_in_thread(
    func: Callable[..., T], /, *args: Any, lane: ExecutorLane = "llm", **kwargs: Any
) -> T:
    """Run a sync callable in the lane-specific thread pool executor."""
    loop = asyncio.get_running_loop()
    # NOTE: We intentionally avoid asyncio's *default* executor here.
    # In some environments, loop.run_in_executor(None, ...) can hang when args/kwargs are used.
    pool = _executor_for_lane(lane)
    submitted_at = time.perf_counter()
    metrics.increment(f"executor_{lane}_submitted")
    call = functools.partial(func, *args, **kwargs)

    def _wrapped_call() -> T:
        _record_lane_metrics(lane, submitted_at=submitted_at)
        try:
            return call()
        finally:
            metrics.increment(f"executor_{lane}_completed")

    return await loop.run_in_executor(pool, _wrapped_call)


async def run_io_in_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run sync I/O tasks in the dedicated IO lane.
    """
    # CRITICAL: Keep callback/history/network I/O isolated from long-running LLM calls.
    # Sharing one small pool causes callback starvation during concurrent assist/chat load.
    return await run_in_thread(func, *args, lane="io", **kwargs)
