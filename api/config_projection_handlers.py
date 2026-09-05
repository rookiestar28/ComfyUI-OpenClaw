"""Owned config projection and mutation handler implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

if __package__ and "." in __package__:
    from ..services.env_aliases import get_env_value
else:  # pragma: no cover - test-only import mode
    from services.env_aliases import get_env_value


@dataclass(frozen=True)
class ConfigHandlerDependencies:
    web: Any
    logger: Any
    provider_catalog: Any
    pack_version: Any
    require_observability_access: Any
    require_admin_token: Any
    require_same_origin_if_no_token: Any
    resolve_token_info: Any
    emit_audit_event: Any
    check_rate_limit: Any
    build_rate_limit_response: Any
    get_client_ip: Any
    is_loopback: Any
    get_admin_token: Any
    get_apply_semantics: Any
    get_effective_config: Any
    get_llm_egress_controls: Any
    get_runtime_guardrails: Any
    get_settings_schema: Any
    is_loopback_client: Any
    update_config: Any
    tenant_boundary_error: Any
    request_tenant_scope: Any
    runtime_only_code: Any
    payload_contains_runtime_guardrails: Any
    model_cache_get: Any
    format_llm_ssrf_error: Any
    llm_insecure_override_enabled: Any
    fetch_remote_model_list: Any
    get_stale_cached_models: Any
    resolve_model_list_target: Any
    validate_model_list_target: Any
    llm_client: Any


async def config_get_response(request: Any, deps: ConfigHandlerDependencies) -> Any:
    """Return the tenant-scoped effective configuration projection."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    allowed, error = deps.require_observability_access(request)
    if not allowed:
        return deps.web.json_response({"ok": False, "error": error}, status=403)
    if not deps.check_rate_limit(request, "admin"):
        return deps.build_rate_limit_response(
            request,
            "admin",
            web_module=deps.web,
            error="Rate limit exceeded",
            include_ok=True,
        )
    token_info = deps.resolve_token_info(request)
    try:
        with deps.request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            effective, sources = deps.get_effective_config(tenant_id=tenant.tenant_id)
            guardrails = deps.get_runtime_guardrails()
            if guardrails.get("status") != "ok":
                deps.emit_audit_event(
                    action="runtime.guardrails",
                    target="runtime_guardrails",
                    outcome="warn",
                    token_info=token_info,
                    status_code=200,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "code": guardrails.get("code"),
                        "violations": guardrails.get("violations", []),
                    },
                    request=request,
                )
            return deps.web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "config": effective,
                    "sources": sources,
                    "runtime_guardrails": guardrails,
                    "providers": deps.provider_catalog,
                    "schema": deps.get_settings_schema(),
                    "write_enabled": True,
                }
            )
    except deps.tenant_boundary_error as exc:
        return deps.web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)}, status=403
        )
    except Exception as exc:
        deps.logger.error("Error getting config (error_type=%s)", type(exc).__name__)
        return deps.web.json_response(
            {"ok": False, "error": "config_read_failed"}, status=500
        )


async def config_put_response(request: Any, deps: ConfigHandlerDependencies) -> Any:
    """Validate and atomically apply tenant-scoped non-secret config updates."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
    admin_token_configured = bool(deps.get_admin_token())
    response = deps.require_same_origin_if_no_token(request, admin_token_configured)
    if response:
        return response
    if not deps.check_rate_limit(request, "admin"):
        return deps.build_rate_limit_response(
            request,
            "admin",
            web_module=deps.web,
            error="Rate limit exceeded",
            include_ok=True,
        )
    token_info = deps.resolve_token_info(request)
    allowed, error = deps.require_admin_token(request)
    if not allowed:
        deps.emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": error or "admin_token_required"},
            request=request,
        )
        return deps.web.json_response(
            {"ok": False, "error": error or "Unauthorized"}, status=403
        )

    allow_remote = (
        get_env_value("OPENCLAW_ALLOW_REMOTE_ADMIN", default="") or ""
    ).lower()
    if allow_remote not in ("1", "true", "yes", "on"):
        remote = deps.get_client_ip(request)
        if not deps.is_loopback(remote):
            deps.emit_audit_event(
                action="config.update",
                target="config.json",
                outcome="deny",
                token_info=token_info,
                status_code=403,
                details={"reason": "remote_admin_denied", "remote": remote},
                request=request,
            )
            return deps.web.json_response(
                {
                    "ok": False,
                    "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                },
                status=403,
            )
    try:
        with deps.request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return deps.web.json_response(
                    {"ok": False, "error": "Invalid JSON body"}, status=400
                )
            if deps.payload_contains_runtime_guardrails(body):
                deps.emit_audit_event(
                    action="config.update",
                    target="config.json",
                    outcome="deny",
                    token_info=token_info,
                    status_code=400,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "reason": "runtime_guardrails_runtime_only",
                        "code": deps.runtime_only_code,
                    },
                    request=request,
                )
                return deps.web.json_response(
                    {
                        "ok": False,
                        "error": "runtime_guardrails are runtime-only (ENV-driven) and cannot be persisted via /config",
                        "code": deps.runtime_only_code,
                    },
                    status=400,
                )
            updates = body.get("llm", body)
            if not isinstance(updates, dict):
                return deps.web.json_response(
                    {"ok": False, "error": "Expected object with config fields"},
                    status=400,
                )
            success, errors = deps.update_config(updates, tenant_id=tenant.tenant_id)
            deps.emit_audit_event(
                action="config.update",
                target="config.json",
                outcome="allow" if success else "error",
                token_info=token_info,
                status_code=200 if success else 400,
                details=(
                    {"tenant_id": tenant.tenant_id, "errors": errors}
                    if errors
                    else {"tenant_id": tenant.tenant_id}
                ),
                request=request,
            )
            if not success:
                return deps.web.json_response(
                    {"ok": False, "errors": errors}, status=400
                )
            effective, sources = deps.get_effective_config(tenant_id=tenant.tenant_id)
            apply_info = deps.get_apply_semantics(list(updates.keys()))
            return deps.web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "config": effective,
                    "sources": sources,
                    "apply": apply_info,
                }
            )
    except deps.tenant_boundary_error as exc:
        deps.emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": exc.code},
            request=request,
        )
        return deps.web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)}, status=403
        )
