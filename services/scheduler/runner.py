"""
Scheduler Runner (R4).
Background tick loop for executing due schedules.
"""

import asyncio
import hashlib
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from ..runtime_config import get_scheduler_config
from ..tenant_context import get_current_tenant_id
from .delivery_outbox import get_delivery_outbox
from .history import RunRecord, get_run_history
from .models import Schedule, TriggerType
from .storage import get_schedule_store

logger = logging.getLogger("ComfyUI-OpenClaw.services.scheduler")

SCHEDULER_EXECUTION_EMBEDDED = "embedded"
SCHEDULER_EXECUTION_DELEGATED = "delegated"


def resolve_scheduler_execution_mode(config: Optional[dict] = None) -> str:
    """
    Resolve scheduler execution mode for current runtime.

    Modes:
    - embedded: in-process scheduler executes due jobs.
    - delegated: in-process scheduler is read-only; execution is delegated.
    """
    if config is None:
        config = get_scheduler_config()

    explicit = str(config.get("execution_mode", "auto")).strip().lower()
    if explicit in {SCHEDULER_EXECUTION_EMBEDDED, SCHEDULER_EXECUTION_DELEGATED}:
        return explicit

    try:
        from ..control_plane import ControlPlaneMode, resolve_control_plane_mode
        from ..effective_security_posture import get_effective_security_posture

        posture = get_effective_security_posture(required=False)
        profile = (
            posture.deployment_profile
            if posture is not None
            else os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").strip().lower()
        )

        if (
            profile == "public"
            and resolve_control_plane_mode(profile, posture=posture)
            == ControlPlaneMode.SPLIT
        ):
            return SCHEDULER_EXECUTION_DELEGATED
    except Exception:
        # Fail-open to embedded for non-public/local workflows if control-plane
        # module cannot be resolved in this runtime.
        pass

    return SCHEDULER_EXECUTION_EMBEDDED


def compute_idempotency_key(schedule_id: str, tick_ts: float) -> str:
    """
    Generate a deterministic idempotency key for a schedule tick.

    This ensures that the same tick (schedule + time window) always produces
    the same key, preventing duplicate runs.

    Args:
        schedule_id: The schedule ID.
        tick_ts: The tick timestamp (floored to minute for cron, or interval boundary).

    Returns:
        A deterministic hash-based idempotency key.
    """
    # Floor to minute for cron schedules, or to interval boundary for interval schedules
    tick_minute = int(tick_ts // 60) * 60
    raw = f"{schedule_id}:{tick_minute}"
    return f"sched_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def is_cron_due(cron_expr: str, last_tick_ts: Optional[float], now: datetime) -> bool:
    """
    Check if a cron schedule is due.

    Simple implementation covering basic 5-field cron (min hour day month weekday).
    Uses croniter if available, otherwise falls back to basic matching.
    """
    try:
        from croniter import croniter

        if last_tick_ts:
            base = datetime.fromtimestamp(last_tick_ts, tz=timezone.utc)
        else:
            # First run: check if current minute matches
            base = now.replace(second=0, microsecond=0)
            base = base.replace(
                minute=base.minute - 1
            )  # Go back 1 minute to include current

        cron = croniter(cron_expr, base)
        next_time = cron.get_next(datetime)

        # Due if next scheduled time is <= now
        return next_time <= now

    except ImportError:
        # Fallback: basic matching (less accurate)
        logger.warning("croniter not installed, using basic cron matching")
        return _basic_cron_match(cron_expr, now)


def _basic_cron_match(cron_expr: str, now: datetime) -> bool:
    """Basic cron matching without croniter (limited functionality)."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    minute, hour, day, month, weekday = parts

    def matches(field: str, value: int, max_val: int) -> bool:
        if field == "*":
            return True
        if field.isdigit():
            return int(field) == value
        if "," in field:
            return value in [int(x) for x in field.split(",") if x.isdigit()]
        if "/" in field:
            step = int(field.split("/")[1])
            return value % step == 0
        return True

    return (
        matches(minute, now.minute, 59)
        and matches(hour, now.hour, 23)
        and matches(day, now.day, 31)
        and matches(month, now.month, 12)
        and matches(
            weekday, now.weekday(), 6
        )  # 0=Mon in Python vs 0=Sun in cron (approximate)
    )


def is_interval_due(
    interval_sec: int, last_tick_ts: Optional[float], now_ts: float
) -> bool:
    """Check if an interval schedule is due."""
    if last_tick_ts is None:
        return True  # Never run, due immediately

    elapsed = now_ts - last_tick_ts
    return elapsed >= interval_sec


class SchedulerRunner:
    """
    Background scheduler runner.

    Runs in a daemon thread, periodically checking for due schedules
    and submitting them to the queue.
    """

    def __init__(
        self,
        submit_fn: Optional[Callable[..., Awaitable]] = None,
        tick_interval: float = 30.0,
    ):
        """
        Args:
            submit_fn: Async function to submit a schedule's workflow.
                       Signature: (template_id, inputs, trace_id, idempotency_key) -> result
            tick_interval: How often to check for due schedules (seconds).
        """
        self._submit_fn = submit_fn
        self._tick_interval = max(10.0, min(tick_interval, 300.0))  # Clamp 10s-5min

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._store = get_schedule_store()

    def is_execution_delegated(self, config: Optional[dict] = None) -> bool:
        """Return True when in-process scheduler execution is delegated/blocked."""
        return resolve_scheduler_execution_mode(config) == SCHEDULER_EXECUTION_DELEGATED

    @staticmethod
    def _compute_cursor_tick_ts(schedule: Schedule, tick_ts: float) -> float:
        """
        Compute the persisted cursor timestamp for this execution.

        R92 invariant:
        - Interval schedules advance by one interval step from previous cursor
          (when available), rather than jumping directly to `now`.
        - This prevents cadence drift/long-jump behavior for daily intervals.
        """
        if (
            schedule.trigger_type == TriggerType.INTERVAL
            and schedule.interval_sec
            and schedule.last_tick_ts is not None
        ):
            next_tick = schedule.last_tick_ts + float(schedule.interval_sec)
            if next_tick <= tick_ts:
                return next_tick
        return tick_ts

    @staticmethod
    def _compute_startup_skip_cursor_ts(schedule: Schedule, now_ts: float) -> float:
        """
        Compute cursor advancement for startup skip-missed maintenance.

        For interval schedules, advance to the latest interval boundary <= now.
        For all other cases, fall back to now.
        """
        if (
            schedule.trigger_type == TriggerType.INTERVAL
            and schedule.interval_sec
            and schedule.last_tick_ts is not None
            and schedule.interval_sec > 0
        ):
            elapsed = now_ts - schedule.last_tick_ts
            if elapsed <= 0:
                return now_ts
            intervals = int(elapsed // float(schedule.interval_sec))
            if intervals > 0:
                return schedule.last_tick_ts + (
                    intervals * float(schedule.interval_sec)
                )
        return now_ts

    def _record_compute_error(
        self, schedule: Schedule, exc: Exception, disable_threshold: int
    ) -> None:
        """Persist per-schedule due/recompute error and deterministic disable state."""
        disabled = schedule.record_compute_error(str(exc), disable_threshold)
        self._store.update(schedule)

        if disabled:
            logger.error(
                "R92_SCHED_COMPUTE_DISABLED: schedule=%s disabled after %s due/recompute errors",
                schedule.schedule_id,
                schedule.compute_error_count,
            )
        else:
            logger.warning(
                "R92_SCHED_COMPUTE_ERROR: schedule=%s error_count=%s error=%s",
                schedule.schedule_id,
                schedule.compute_error_count,
                str(exc),
            )

    def _evaluate_schedule_due(
        self,
        schedule: Schedule,
        now: datetime,
        now_ts: float,
        disable_threshold: int,
    ) -> bool:
        """Evaluate due status with bounded error tracking."""
        try:
            is_due = False
            if schedule.trigger_type == TriggerType.CRON:
                is_due = is_cron_due(schedule.cron_expr, schedule.last_tick_ts, now)
            elif schedule.trigger_type == TriggerType.INTERVAL:
                is_due = is_interval_due(
                    schedule.interval_sec, schedule.last_tick_ts, now_ts
                )
            had_compute_error = bool(
                schedule.compute_error_count or schedule.last_compute_error
            )
            schedule.clear_compute_error()
            if had_compute_error:
                self._store.update(schedule)
            return is_due
        except Exception as e:
            self._record_compute_error(schedule, e, disable_threshold)
            return False

    def _collect_due_schedules(
        self,
        schedules: list[Schedule],
        now: datetime,
        now_ts: float,
        disable_threshold: int,
    ) -> list[Schedule]:
        """Execution-path recompute: collect due schedules without cursor mutation."""
        due_schedules: list[Schedule] = []
        for schedule in schedules:
            if not schedule.enabled:
                continue
            if self._evaluate_schedule_due(schedule, now, now_ts, disable_threshold):
                due_schedules.append(schedule)
        return due_schedules

    def start(self) -> None:
        """Start the scheduler background loop."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        startup_config = get_scheduler_config()
        if self.is_execution_delegated(startup_config):
            logger.info(
                "R92: Scheduler execution delegated (public+split). Embedded runner is disabled."
            )
            self._running = False
            self._thread = None
            return

        self._running = True
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run_loop, name="moltbot-scheduler", daemon=True
        )
        self._thread.start()
        logger.info(f"Scheduler started (tick interval: {self._tick_interval}s)")

    def stop(self) -> None:
        """Stop the scheduler background loop."""
        if not self._running:
            return

        self._stop_event.set()
        self._running = False

        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

        logger.info("Scheduler stopped")

    def _run_loop(self) -> None:
        """Main scheduler loop (runs in thread)."""
        logger.debug("Scheduler loop started")

        # R34: Read config once at startup for jitter/skip behavior
        config = get_scheduler_config()

        if self.is_execution_delegated(config):
            logger.info(
                "R92: Scheduler loop startup blocked by delegated execution mode."
            )
            self._running = False
            return

        # 1. Startup Jitter
        jitter_sec = config.get("startup_jitter_sec", 0)
        if jitter_sec > 0:
            # Clamp to safe range just in case
            jitter_sec = min(300, max(0, jitter_sec))
            delay = random.uniform(0, jitter_sec)
            logger.info(f"Startup jitter enabled: sleeping {delay:.2f}s")
            # Wait with stop_event check to be interruptible
            if self._stop_event.wait(timeout=delay):
                logger.debug("Scheduler stopped during jitter wait")
                return

        # 2. Skip Missed Intervals
        if config.get("skip_missed_intervals"):
            logger.info("Skip Missed Intervals enabled: advancing cursors...")
            try:
                self._skip_missed_ticks(config=config)
            except Exception as e:
                logger.error(f"Failed to skip missed ticks: {e}")

        while not self._stop_event.is_set():
            if self.is_execution_delegated():
                logger.warning(
                    "R92: execution mode switched to delegated; stopping embedded scheduler loop."
                )
                self._running = False
                break
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Scheduler tick error: {e}", exc_info=True)

            # Wait for next tick
            self._stop_event.wait(timeout=self._tick_interval)

        logger.debug("Scheduler loop exited")

    def _skip_missed_ticks(self, config: Optional[dict] = None) -> None:
        """
        Advance all due schedules to now without executing them.
        Prevents backlog burst after downtime.
        """
        if config is None:
            config = get_scheduler_config()

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        disable_threshold = int(config.get("compute_error_disable_threshold", 3))
        schedules = self._store.list_all()

        skipped_count = 0
        due_schedules = self._collect_due_schedules(
            schedules=schedules,
            now=now,
            now_ts=now_ts,
            disable_threshold=disable_threshold,
        )

        for schedule in due_schedules:
            # Update cursor without running (startup skip policy)
            # Use a special run_id to indicate skip.
            skip_ts = self._compute_startup_skip_cursor_ts(schedule, now_ts)
            schedule.update_cursor(skip_ts, "skipped_startup")
            self._store.update(schedule)
            skipped_count += 1

        if skipped_count > 0:
            logger.info(
                f"Skipped {skipped_count} missed schedules due to startup policy."
            )

    def _tick(self) -> None:
        """Process one scheduler tick."""
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        # R34: Dynamic config read for runtime tuning
        config = get_scheduler_config()
        max_runs = config.get("max_runs_per_tick", 5)
        disable_threshold = int(config.get("compute_error_disable_threshold", 3))

        if self.is_execution_delegated(config):
            logger.debug(
                "R92: delegated mode active, skipping in-process scheduler tick execution."
            )
            return

        schedules = self._store.list_all()
        due_schedules = self._collect_due_schedules(
            schedules=schedules,
            now=now,
            now_ts=now_ts,
            disable_threshold=disable_threshold,
        )

        if due_schedules:
            logger.debug(f"Found {len(due_schedules)} due schedules")

            # R34: Cap max runs per tick
            if len(due_schedules) > max_runs:
                logger.warning(
                    f"Throttling scheduler: {len(due_schedules)} due, "
                    f"capping to {max_runs} (max_runs_per_tick)."
                )
                # Sort by last_tick_ts to prioritize oldest starved schedules
                # If last_tick_ts is None, treat as 0 (very old)
                due_schedules.sort(key=lambda s: s.last_tick_ts or 0)
                due_schedules = due_schedules[:max_runs]

        for schedule in due_schedules:
            self._execute_schedule(schedule, now_ts)

    @staticmethod
    def _record_delivery_intent(
        schedule: Schedule, run_id: str, idempotency_key: str, prompt_id: str
    ) -> None:
        delivery = schedule.delivery
        if not isinstance(delivery, dict) or not delivery.get("enabled"):
            return
        # CRITICAL: delivery must be durable data, not a watcher task. The submit
        # coroutine runs on a short-lived loop that closes when it returns, so a task
        # started there is destroyed and the result is never delivered.
        try:
            get_delivery_outbox().record(
                idempotency_key=idempotency_key,
                run_id=run_id,
                schedule_id=schedule.schedule_id,
                prompt_id=prompt_id,
                delivery=delivery,
                tenant_id=get_current_tenant_id(),
            )
        except Exception as exc:  # the run itself succeeded; keep it recorded
            logger.error(
                "Schedule delivery intent not recorded for run %s (%s)",
                run_id,
                type(exc).__name__,
            )

    def _execute_schedule(self, schedule: Schedule, tick_ts: float) -> None:
        """Execute a single due schedule."""
        if self.is_execution_delegated():
            raise RuntimeError("scheduler_delegated")

        idempotency_key = compute_idempotency_key(schedule.schedule_id, tick_ts)

        # R9: Check if already processed via history
        history = get_run_history()
        if history.is_processed(idempotency_key):
            logger.debug(f"Skipping already-processed tick: {idempotency_key}")
            return

        # Generate trace_id for this run
        from ..trace import generate_trace_id

        trace_id = generate_trace_id()
        run_id = f"run_{trace_id[:12]}"

        # R9: Create run record
        run_record = RunRecord(
            run_id=run_id,
            schedule_id=schedule.schedule_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )

        logger.info(
            f"Executing schedule {schedule.schedule_id} "
            f"(template={schedule.template_id}, trace={trace_id})"
        )

        try:
            if self._submit_fn:
                # Use async bridge to call async submit function
                from ..plugins.async_bridge import run_async_in_sync_context

                result = run_async_in_sync_context(
                    self._submit_fn(
                        template_id=schedule.template_id,
                        inputs=schedule.inputs,
                        trace_id=trace_id,
                        idempotency_key=idempotency_key,
                        delivery=schedule.delivery,
                        source="scheduler",
                    )
                )

                prompt_id = (
                    result.get("prompt_id") if isinstance(result, dict) else None
                )
                deduped = (
                    result.get("deduped", False) if isinstance(result, dict) else False
                )

                if deduped:
                    run_record.skip("Already queued")
                    logger.info(f"Schedule {schedule.schedule_id} skipped (deduped)")
                else:
                    run_record.complete(prompt_id)
                    logger.info(
                        f"Schedule {schedule.schedule_id} queued: prompt_id={prompt_id}"
                    )
                # Deduped retries also record: a crash between submit and the run
                # record must not lose the delivery of the prompt that was queued.
                if prompt_id:
                    self._record_delivery_intent(
                        schedule, run_id, idempotency_key, str(prompt_id)
                    )
            else:
                logger.warning(
                    "No submit function configured, skipping actual submission"
                )
                run_record.skip("No submit function")

            # Update cursor
            effective_tick_ts = self._compute_cursor_tick_ts(schedule, tick_ts)
            schedule.update_cursor(effective_tick_ts, run_id)
            self._store.update(schedule)

            # R9: Record run
            history.add_run(run_record)

        except Exception as e:
            logger.error(f"Schedule {schedule.schedule_id} execution failed: {e}")

            # R9: Record failure
            run_record.fail(str(e))
            history.add_run(run_record)

            # Still update cursor to avoid retry storm
            effective_tick_ts = self._compute_cursor_tick_ts(schedule, tick_ts)
            schedule.update_cursor(effective_tick_ts, run_id)
            self._store.update(schedule)


# Singleton runner instance
_scheduler_runner: Optional[SchedulerRunner] = None


def get_scheduler_runner() -> SchedulerRunner:
    """Get the singleton scheduler runner."""
    global _scheduler_runner
    if _scheduler_runner is None:
        _scheduler_runner = SchedulerRunner()
    return _scheduler_runner


def start_scheduler() -> None:
    """Start the background scheduler."""
    get_scheduler_runner().start()


def stop_scheduler() -> None:
    """Stop the background scheduler."""
    if _scheduler_runner:
        _scheduler_runner.stop()


def reset_scheduler_runner(*, stop: bool = True) -> None:
    """Reset singleton scheduler runner (tests / controlled reset helper)."""
    global _scheduler_runner
    if _scheduler_runner is not None and stop:
        try:
            _scheduler_runner.stop()
        except Exception:
            logger.exception("R67: scheduler stop during reset failed")
    _scheduler_runner = None
