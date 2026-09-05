"""
Callback Delivery Service (F16).
Watches job completion and delivers results to callback URLs.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from .async_utils import run_io_in_thread
from .comfyui_history import extract_images, fetch_history, get_job_status
from .env_aliases import get_env_value
from .job_events import JobEventType, get_job_event_store  # R71
from .metrics import metrics
from .reasoning_redaction import sanitize_operator_payload
from .safe_io import SSRFError, safe_request_json
from .trace_store import trace_store

logger = logging.getLogger("ComfyUI-OpenClaw.services.callback_delivery")

# Config
CALLBACK_ALLOW_HOSTS_ENV = "OPENCLAW_CALLBACK_ALLOW_HOSTS"
LEGACY_CALLBACK_ALLOW_HOSTS_ENV = "MOLTBOT_CALLBACK_ALLOW_HOSTS"
CALLBACK_TIMEOUT_SEC = int(
    get_env_value("OPENCLAW_CALLBACK_TIMEOUT_SEC", default="10") or "10"
)
CALLBACK_MAX_RETRIES = int(
    get_env_value("OPENCLAW_CALLBACK_MAX_RETRIES", default="3") or "3"
)
POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 150  # 5 minutes at 2s interval


def get_callback_allow_hosts() -> Set[str]:
    """Get allowed callback hosts from environment."""
    hosts_str = (
        get_env_value(
            CALLBACK_ALLOW_HOSTS_ENV,
            aliases=(LEGACY_CALLBACK_ALLOW_HOSTS_ENV,),
            default="",
        )
        or ""
    )
    if not hosts_str.strip():
        return set()
    return {h.strip() for h in hosts_str.split(",") if h.strip()}


async def start_callback_watch(
    prompt_id: str, callback_config: Dict[str, Any], trace_id: Optional[str] = None
) -> None:
    """
    Start a background watcher for a job and deliver results on completion.

    Args:
        prompt_id: The ComfyUI prompt ID to watch.
        callback_config: Config dict with 'url', optional 'method', 'headers', 'mode'.
    """
    asyncio.create_task(
        _watch_and_deliver(prompt_id, callback_config, trace_id=trace_id)
    )


async def _watch_and_deliver(
    prompt_id: str, callback_config: Dict[str, Any], trace_id: Optional[str] = None
) -> None:
    """Internal watcher loop."""
    url = callback_config.get("url")
    method = callback_config.get("method", "POST").upper()
    headers = callback_config.get("headers", {})

    if not url:
        logger.error(f"[Callback] No URL provided for {prompt_id}")
        return

    allow_hosts = get_callback_allow_hosts()
    if not allow_hosts:
        logger.error(
            f"[Callback] No allowed hosts configured. Set {CALLBACK_ALLOW_HOSTS_ENV} (or legacy {LEGACY_CALLBACK_ALLOW_HOSTS_ENV})."
        )
        metrics.inc("callback_blocked")
        return

    # Poll for completion
    attempts = 0
    history_item = None

    while attempts < POLL_MAX_ATTEMPTS:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        attempts += 1

        # Use thread to avoid blocking event loop
        # IMPORTANT (R129): history polling is network/disk-bound I/O and must not
        # compete with long LLM calls in the default lane.
        history_item = await run_io_in_thread(fetch_history, prompt_id)
        status = get_job_status(history_item)

        if status in ("completed", "error"):
            break

    if history_item is None:
        logger.warning(f"[Callback] Job {prompt_id} never completed (timed out)")
        metrics.inc("callback_timeout")
        try:
            get_job_event_store().emit(
                JobEventType.FAILED,
                prompt_id=prompt_id,
                trace_id=trace_id or "",
                data={"reason": "timeout"},
            )
        except Exception:
            pass
        return

    # R25: Record completion
    try:
        trace_store.add_event(prompt_id, trace_id or "", get_job_status(history_item))
    except Exception:
        pass

    # R71: Emit lifecycle event (COMPLETED vs ERROR)
    status = get_job_status(history_item)
    try:
        event_type = (
            JobEventType.COMPLETED if status == "completed" else JobEventType.FAILED
        )
        get_job_event_store().emit(
            event_type,
            prompt_id=prompt_id,
            trace_id=trace_id or "",
            data={"status": status},
        )
    except Exception:
        pass

    # Extract outputs
    images = extract_images(history_item) if history_item else []

    # Build payload
    payload = {
        "prompt_id": prompt_id,
        "trace_id": trace_id,
        "status": get_job_status(history_item),
        "outputs": images,
    }
    payload = sanitize_operator_payload(payload)

    # R121: Dual-lane delivery retries
    from .retry_partition import RetryDecision, RetryPartition

    partition = RetryPartition(
        rate_limit_retries=2,
        transport_retries=CALLBACK_MAX_RETRIES,
        backoff_base=1.0,
    )

    max_total_attempts = CALLBACK_MAX_RETRIES + 2  # transport + rate-limit budgets

    for attempt in range(max_total_attempts):
        try:
            await run_io_in_thread(
                safe_request_json,
                method,
                url,
                payload,
                allow_hosts=allow_hosts,
                headers=headers,
                timeout_sec=CALLBACK_TIMEOUT_SEC,
            )
            logger.info(f"[Callback] Delivered to {url} for {prompt_id}")
            try:
                trace_store.add_event(
                    prompt_id,
                    trace_id or "",
                    "delivered",
                    {"host": (url.split("/")[2] if "/" in url else url)},
                )
                # R71: Emit delivery success
                get_job_event_store().emit(
                    JobEventType.CALLBACK_SENT,
                    prompt_id=prompt_id,
                    trace_id=trace_id or "",
                    data={"target": url},
                )
            except Exception:
                pass
            metrics.inc("callback_success")
            return
        except SSRFError as e:
            logger.warning(f"[Callback] SSRF blocked for {url}: {e}")
            metrics.inc("callback_blocked")
            return  # Don't retry SSRF policy blocks
        except Exception as e:
            evidence = partition.record_failure(e)
            logger.warning(
                f"[Callback] R121 attempt={attempt + 1} "
                f"decision={evidence.decision.value} "
                f"lane={evidence.lane} error={e}"
            )

            if not partition.should_retry(evidence):
                # Emit audit event for lane exhaustion
                try:
                    from .audit_events import build_audit_event, emit_audit_event

                    event = build_audit_event(
                        f"r121.callback.{evidence.decision.value.lower()}",
                        payload={
                            "prompt_id": prompt_id,
                            "url": url,
                            "lane": evidence.lane,
                            "attempt": evidence.attempt,
                            "error": str(e)[:500],
                        },
                    )
                    emit_audit_event(event)
                except Exception:
                    pass
                break

            await asyncio.sleep(partition.backoff_for(evidence))

    logger.error(f"[Callback] All retries failed for {prompt_id}")
    metrics.inc("callback_failed")
    try:
        get_job_event_store().emit(
            JobEventType.CALLBACK_FAILED,
            prompt_id=prompt_id,
            trace_id=trace_id or "",
            data={
                "target": url,
                "reason": "r121_budget_exhausted",
                "lanes": partition.diagnostics(),
            },
        )
    except Exception:
        pass
