"""Owned remote model-discovery handler implementation."""

from __future__ import annotations

from typing import Any

from .config_projection_handlers import ConfigHandlerDependencies

if __package__ and "." in __package__:
    from ..services.env_aliases import get_env_value
else:  # pragma: no cover - test-only import mode
    from services.env_aliases import get_env_value


async def llm_models_response(request: Any, deps: ConfigHandlerDependencies) -> Any:
    """Serve tenant-isolated bounded provider model discovery."""

    if deps.web is None:
        raise RuntimeError("aiohttp not available")
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
            allowed, error = deps.require_admin_token(request)
            if not allowed:
                deps.emit_audit_event(
                    action="config.update",
                    target="config.json",
                    outcome="deny",
                    token_info=token_info,
                    status_code=403,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "reason": error or "unauthorized",
                    },
                    request=request,
                )
                return deps.web.json_response(
                    {"ok": False, "error": error or "Unauthorized"}, status=403
                )
            allow_remote = (
                get_env_value("OPENCLAW_ALLOW_REMOTE_ADMIN", default="") or ""
            ).lower()
            if allow_remote not in ("1", "true", "yes", "on"):
                remote = request.remote or ""
                if not deps.is_loopback_client(remote):
                    return deps.web.json_response(
                        {
                            "ok": False,
                            "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                        },
                        status=403,
                    )
            provider_override = (request.query.get("provider") or "").strip().lower()
            effective, _sources = deps.get_effective_config(tenant_id=tenant.tenant_id)
            try:
                target = deps.resolve_model_list_target(
                    provider_override, effective, tenant.tenant_id
                )
            except (TypeError, ValueError) as exc:
                return deps.web.json_response(
                    {"ok": False, "error": str(exc)}, status=400
                )
            cached_entry = deps.model_cache_get(target.cache_key)
            if cached_entry:
                _timestamp, models = cached_entry
                if isinstance(models, list):
                    return deps.web.json_response(
                        {
                            "ok": True,
                            "tenant_id": tenant.tenant_id,
                            "provider": target.provider,
                            "models": models,
                            "cached": True,
                        }
                    )
            # CRITICAL: local providers intentionally work without API keys.
            if target.requires_api_key and not target.api_key:
                return deps.web.json_response(
                    {
                        "ok": False,
                        "error": f"No API key configured for provider '{target.provider}'.",
                    },
                    status=400,
                )
            try:
                controls = deps.get_llm_egress_controls(
                    target.provider,
                    target.base_url,
                    allow_private_network=target.allow_private_network,
                )
                deps.validate_model_list_target(
                    target,
                    controls,
                    allow_insecure_base_url=deps.llm_insecure_override_enabled(),
                )
            except Exception as exc:
                return deps.web.json_response(
                    {"ok": False, "error": deps.format_llm_ssrf_error(exc)},
                    status=403,
                )
            try:
                try:
                    from ..services.safe_io import SSRFError
                except ImportError:
                    from services.safe_io import SSRFError

                models = deps.fetch_remote_model_list(
                    target,
                    controls,
                    pack_version=deps.pack_version,
                    allow_insecure_base_url=deps.llm_insecure_override_enabled(),
                )
                return deps.web.json_response(
                    {
                        "ok": True,
                        "tenant_id": tenant.tenant_id,
                        "provider": target.provider,
                        "models": models,
                        "cached": False,
                    }
                )
            except SSRFError as exc:
                return deps.web.json_response(
                    {"ok": False, "error": deps.format_llm_ssrf_error(exc)},
                    status=403,
                )
            except RuntimeError as exc:
                error_text = str(exc)
                if "HTTP" in error_text:
                    stale = deps.get_stale_cached_models(target.cache_key)
                    if stale:
                        _timestamp, models = stale
                        warning = f"Using cached list (refresh failed: {error_text})"
                        return deps.web.json_response(
                            {
                                "ok": True,
                                "tenant_id": tenant.tenant_id,
                                "provider": target.provider,
                                "models": models,
                                "cached": True,
                                "warning": warning,
                            }
                        )
                    return deps.web.json_response(
                        {"ok": False, "error": f"Upstream error: {error_text}"},
                        status=502,
                    )
                raise
            except Exception as exc:
                stale = deps.get_stale_cached_models(target.cache_key)
                if stale:
                    deps.logger.warning(
                        "Model list refresh failed, serving cached list: %s", exc
                    )
                    _timestamp, models = stale
                    warning = f"Using cached list (refresh failed: {exc!s})"
                    return deps.web.json_response(
                        {
                            "ok": True,
                            "tenant_id": tenant.tenant_id,
                            "provider": target.provider,
                            "models": models,
                            "cached": True,
                            "warning": warning,
                        }
                    )
                deps.logger.exception("Failed to fetch model list")
                return deps.web.json_response(
                    {"ok": False, "error": str(exc)}, status=500
                )
    except deps.tenant_boundary_error as exc:
        return deps.web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)}, status=403
        )
