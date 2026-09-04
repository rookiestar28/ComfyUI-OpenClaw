"""Owned observability and jobs handler implementations for the API facade."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ComfyUI-OpenClaw")


@dataclass(frozen=True)
class RouteHandlerDependencies:
    web: Any
    pack_name: Any
    pack_version: Any
    pack_start_time: Any
    log_file: Any
    metrics: Any
    tail_log: Any
    require_observability_access: Any
    require_admin_token: Any
    check_rate_limit: Any
    build_rate_limit_response: Any
    trace_store: Any
    get_executor_diagnostics: Any
    redact_text: Any
    check_dependency: Callable[[str], bool]
    resolve_token_info: Any
    emit_audit_event: Any
    jobs_request_tenant_scope: Any
    normalize_jobs_query: Any
    build_jobs_audit_details: Any
    safe_job_audit_outcomes: Any
    jobs_security_error: Any
    tenant_boundary_error: Any
    jobs_host_contract_unsupported: Any
    jobs_backend_unavailable: Any
    read_jobs: Any
    ensure_observability_deps_ready: Any


def ensure_observability_deps_ready(
    deps: RouteHandlerDependencies,
) -> tuple[bool, str | None]:
    """Reject partially initialized observability handlers deterministically."""

    missing: list[str] = []
    if not callable(deps.require_observability_access):
        missing.append("require_observability_access")
    if not callable(deps.check_rate_limit):
        missing.append("check_rate_limit")
    if not callable(deps.tail_log):
        missing.append("tail_log")
    if missing:
        return (
            False,
            "Backend not fully initialized (missing route dependencies: "
            + ", ".join(missing)
            + ").",
        )
    return True, None


async def health_response(request: Any, deps: RouteHandlerDependencies) -> Any:
    """Build the existing partial-failure-tolerant health response."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.llm_client import LLMClient
        from ..services.providers.keys import requires_api_key
    except ImportError:
        from services.llm_client import LLMClient
        from services.providers.keys import requires_api_key

    uptime = time.time() - deps.pack_start_time
    provider_info = {
        "provider": "unknown",
        "key_configured": False,
        "model": "unknown",
        "base_url": None,
        "api_type": None,
    }
    key_required = True
    try:
        client = LLMClient()
        provider_info = client.get_provider_summary()
        key_required = requires_api_key(provider_info.get("provider", "unknown"))
    except Exception:
        provider_info = {
            "provider": "unknown",
            "key_configured": False,
            "model": "unknown",
            "base_url": None,
            "api_type": None,
        }
        key_required = True

    try:
        from ..services.access_control import is_loopback

        _ = is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)
    except ImportError:
        from services.access_control import is_loopback

        _ = is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)
    policy_mode = "token" if token_configured else "loopback_only"

    try:
        metrics_snapshot = deps.metrics.get_snapshot()
    except Exception:
        metrics_snapshot = {"errors_captured": 0, "logs_processed": 0}
    try:
        executor_snapshot = deps.get_executor_diagnostics() or {}
    except Exception:
        executor_snapshot = {}
    try:
        if __package__ and "." in __package__:
            from ..services.startup_lifecycle import get_startup_diagnostics
        else:
            from services.startup_lifecycle import get_startup_diagnostics
        startup_diagnostics = get_startup_diagnostics()
    except Exception:
        # SECURITY: keep the public fallback deterministic and content-free even when
        # startup diagnostics cannot be imported.
        startup_diagnostics = {
            "schema_version": 1,
            "phase": "package_import",
            "state": "fatal",
            "reason_code": "bootstrap_import_failed",
            "ready": False,
            "degraded": False,
            "fatal": True,
            "attempt": 0,
            "max_attempts": 0,
            "elapsed_ms": 0,
            "phase_elapsed_ms": 0,
            "ready_elapsed_ms": None,
            "warmups": [],
        }

    job_stats = {}
    try:
        from ..services.job_events import get_job_event_store

        job_stats = get_job_event_store().stats()
    except Exception as exc:
        logger.warning("health.job_stats_degraded (error_type=%s)", type(exc).__name__)

    control_plane_info = {}
    runtime_profile = "minimal"
    try:
        try:
            from ..services.capabilities import _get_control_plane_info
            from ..services.runtime_profile import get_runtime_profile
        except ImportError:
            from services.capabilities import _get_control_plane_info
            from services.runtime_profile import get_runtime_profile
        control_plane_info = _get_control_plane_info()
        runtime_profile = get_runtime_profile().value
    except Exception as exc:
        # IMPORTANT: health remains partially available, but exception content must
        # never cross this public boundary through logs or the response.
        logger.warning(
            "health.control_plane_degraded (error_type=%s)", type(exc).__name__
        )

    return deps.web.json_response(
        {
            "ok": True,
            "pack": {
                "name": deps.pack_name,
                "version": deps.pack_version,
                "dependencies": {
                    "aiohttp": deps.check_dependency("aiohttp"),
                    "watchdog": deps.check_dependency("watchdog"),
                },
            },
            "uptime_sec": uptime,
            "config": {
                "provider": provider_info.get("provider"),
                "model": provider_info.get("model"),
                "base_url": provider_info.get("base_url"),
                "api_type": provider_info.get("api_type"),
                "llm_key_configured": provider_info.get("key_configured", False),
                "llm_key_required": key_required,
            },
            "stats": {
                "errors_captured": metrics_snapshot["errors_captured"],
                "logs_processed": metrics_snapshot["logs_processed"],
                "executors": executor_snapshot,
                "observability": job_stats,
            },
            "startup": startup_diagnostics,
            "access_policy": {
                "observability": policy_mode,
                "token_configured": token_configured,
            },
            "control_plane": control_plane_info,
            "runtime_profile": runtime_profile,
        }
    )


async def logs_tail_response(request: Any, deps: RouteHandlerDependencies) -> Any:
    """Authorize, bound, filter, and redact the log-tail response."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = deps.ensure_observability_deps_ready()
    if not ok:
        return deps.web.json_response({"ok": False, "error": init_error}, status=500)
    allowed, error = deps.require_admin_token(request)
    if not allowed:
        return deps.web.json_response({"ok": False, "error": error}, status=403)
    if not deps.check_rate_limit(request, "logs"):
        return deps.build_rate_limit_response(
            request,
            "logs",
            web_module=deps.web,
            error="Rate limit exceeded",
            include_ok=True,
        )

    try:
        line_count = 50
        val_n = request.query.get("n")
        val_lines = request.query.get("lines")
        target_val = val_n if val_n is not None else val_lines
        if target_val:
            with suppress(ValueError):
                line_count = int(target_val)
        line_count = min(max(line_count, 1), 500)
        trace_id_filter = request.query.get("trace_id")
        prompt_id_filter = request.query.get("prompt_id")
        content = deps.tail_log(deps.log_file, line_count)
        if trace_id_filter or prompt_id_filter:
            content = [
                line
                for line in content
                if (trace_id_filter and trace_id_filter in line)
                or (prompt_id_filter and prompt_id_filter in line)
            ]
        if deps.redact_text:
            content = [deps.redact_text(line) for line in content]
        max_bytes = 100_000
        if sum(len(line.encode("utf-8")) for line in content) > max_bytes:
            truncated: list[str] = []
            current_bytes = 0
            for line in reversed(content):
                line_bytes = len(line.encode("utf-8"))
                if current_bytes + line_bytes > max_bytes:
                    break
                truncated.insert(0, line)
                current_bytes += line_bytes
            content = truncated
        return deps.web.json_response(
            {
                "ok": True,
                "content": content,
                "filtered": bool(trace_id_filter or prompt_id_filter),
            }
        )
    except Exception as exc:
        return deps.web.json_response({"ok": False, "error": str(exc)}, status=500)


def emit_jobs_list_audit(
    deps: RouteHandlerDependencies,
    *,
    request: Any,
    token_info: Any,
    outcome: str,
    status_code: int,
    reason: str,
    **counts: Any,
) -> None:
    safe_outcome = outcome if outcome in deps.safe_job_audit_outcomes else "error"
    deps.emit_audit_event(
        action="jobs.list",
        target="jobs",
        outcome=safe_outcome,
        token_info=token_info,
        status_code=status_code,
        details=deps.build_jobs_audit_details(reason, **counts),
        request=request,
    )


async def jobs_response(request: Any, deps: RouteHandlerDependencies) -> Any:
    """Serve the R213 bounded jobs read model behind its security transaction."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    token_info = deps.resolve_token_info(request)
    if not deps.check_rate_limit(request, "admin"):
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="rate_limit",
            status_code=429,
            reason="jobs_rate_limited",
        )
        return deps.build_rate_limit_response(
            request,
            "admin",
            web_module=deps.web,
            error="jobs_rate_limited",
            include_ok=True,
        )

    # CRITICAL: metadata is descriptive; this guard must precede queue/history access.
    allowed, _error = deps.require_admin_token(request)
    if not allowed:
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="deny",
            status_code=403,
            reason="jobs_admin_required",
        )
        return deps.web.json_response(
            {"ok": False, "error": "jobs_admin_required"}, status=403
        )
    try:
        with deps.jobs_request_tenant_scope(request, token_info) as tenant_context:
            query = deps.normalize_jobs_query(request.query)
            body = deps.read_jobs(query, tenant_id=tenant_context.tenant_id)
            scan = body["scan"]
            emit_jobs_list_audit(
                deps,
                request=request,
                token_info=token_info,
                outcome="allow",
                status_code=200,
                reason="jobs_listed",
                returned_count=len(body["jobs"]),
                excluded_count=scan["excluded"],
                malformed_count=scan["malformed"],
            )
    except deps.tenant_boundary_error as exc:
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="deny",
            status_code=403,
            reason=exc.code,
        )
        return deps.web.json_response({"ok": False, "error": exc.code}, status=403)
    except deps.jobs_security_error:
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="error",
            status_code=400,
            reason="jobs_query_invalid",
        )
        return deps.web.json_response(
            {"ok": False, "error": "jobs_query_invalid"}, status=400
        )
    except deps.jobs_host_contract_unsupported:
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="unsupported",
            status_code=501,
            reason="jobs_host_contract_unsupported",
        )
        return deps.web.json_response(
            {"ok": False, "error": "jobs_host_contract_unsupported"}, status=501
        )
    except deps.jobs_backend_unavailable:
        emit_jobs_list_audit(
            deps,
            request=request,
            token_info=token_info,
            outcome="error",
            status_code=503,
            reason="jobs_backend_unavailable",
        )
        return deps.web.json_response(
            {"ok": False, "error": "jobs_backend_unavailable"}, status=503
        )
    return deps.web.json_response(body)


async def trace_response(request: Any, deps: RouteHandlerDependencies) -> Any:
    """Authorize and return the redacted operator trace projection."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = deps.ensure_observability_deps_ready()
    if not ok:
        return deps.web.json_response({"ok": False, "error": init_error}, status=500)
    allowed, error = deps.require_admin_token(request)
    if not allowed:
        return deps.web.json_response({"ok": False, "error": error}, status=403)
    prompt_id = request.match_info.get("prompt_id")
    if not prompt_id:
        return deps.web.json_response(
            {"ok": False, "error": "missing_prompt_id"}, status=400
        )
    record = deps.trace_store.get(prompt_id)
    if not record:
        return deps.web.json_response({"ok": False, "error": "not_found"}, status=404)
    trace_data = record.to_dict()
    try:
        from ..services.reasoning_redaction import (
            audit_reasoning_reveal,
            resolve_reasoning_reveal,
            sanitize_operator_payload,
        )
        from ..services.redaction import redact_json
    except ImportError:
        from services.reasoning_redaction import (
            audit_reasoning_reveal,
            resolve_reasoning_reveal,
            sanitize_operator_payload,
        )
        from services.redaction import redact_json
    if redact_json:
        trace_data = redact_json(trace_data)
    reveal = resolve_reasoning_reveal(request, admin_authorized=allowed)
    audit_reasoning_reveal(request, target="trace.get", decision=reveal)
    trace_data = sanitize_operator_payload(
        trace_data, include_reasoning=reveal["allowed"]
    )
    return deps.web.json_response({"ok": True, "trace": trace_data})
