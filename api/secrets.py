"""
S25: Secrets Management API

Admin-gated endpoints for managing server-side secrets (API keys, tokens).

Security model:
- Requires valid Admin Token
- Loopback-only unless OPENCLAW_ALLOW_REMOTE_ADMIN=1
- Never returns actual secret values
- Redacts request bodies containing secrets

Endpoints:
- GET /openclaw/secrets/status: Secret configuration status (no values)
- PUT /openclaw/secrets: Save API key to server store
- DELETE /openclaw/secrets/{provider}: Clear provider secret
"""

from __future__ import annotations

import logging
from typing import Optional

# Import discipline:
# - ComfyUI runtime: package-relative imports only.
# - Unit tests: allow top-level fallbacks.
if __package__ and "." in __package__:
    from ..models.schemas import MAX_BODY_SIZE
    from ..services.access_control import require_admin_token, resolve_token_info
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.audit import emit_audit_event
    from ..services.csrf_protection import require_same_origin_if_no_token
    from ..services.env_aliases import get_env_value
    from ..services.metrics import metrics
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.request_ip import get_client_ip
    from ..services.runtime_config import get_admin_token, is_loopback_client
    from ..services.secret_store import get_secret_store
else:  # pragma: no cover (test-only import mode)
    from models.schemas import MAX_BODY_SIZE  # type: ignore
    from services.access_control import require_admin_token  # type: ignore
    from services.access_control import resolve_token_info  # type: ignore
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.audit import emit_audit_event  # type: ignore
    from services.csrf_protection import require_same_origin_if_no_token  # type: ignore
    from services.env_aliases import get_env_value
    from services.metrics import metrics  # type: ignore
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.request_ip import get_client_ip  # type: ignore
    from services.runtime_config import get_admin_token  # type: ignore
    from services.runtime_config import is_loopback_client
    from services.secret_store import get_secret_store  # type: ignore

web = import_aiohttp_web()

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.secrets")

_TRUTHY = ("1", "true", "yes", "on")


def _deny_if_remote_admin_not_allowed(request: web.Request) -> Optional[web.Response]:
    allow_remote = (
        get_env_value("OPENCLAW_ALLOW_REMOTE_ADMIN", default="") or ""
    ).lower()
    if allow_remote in _TRUTHY:
        return None
    remote = request.remote or ""
    if not is_loopback_client(remote):
        # R99: audit deny path
        emit_audit_event(
            action="secrets.access",
            target="secrets",
            outcome="deny",
            status_code=403,
            details={"reason": "remote_admin_denied", "remote": remote},
            request=request,
        )
        return web.json_response(
            {
                "ok": False,
                "error": "remote_admin_denied",
                "message": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
            },
            status=403,
        )
    return None


def _require_admin(request: web.Request) -> Optional[web.Response]:
    allowed, error = require_admin_token(request)
    if not allowed:
        # R99: audit deny path
        emit_audit_event(
            action="secrets.access",
            target="secrets",
            outcome="deny",
            status_code=403,
            details={"reason": error or "admin_token_required"},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": error or "Unauthorized"}, status=403
        )
    return None


def _rate_limit_admin(request: web.Request) -> Optional[web.Response]:
    if check_rate_limit(request, "admin"):
        return None
    metrics.increment("rate_limit_exceeded")
    return build_rate_limit_response(
        request,
        "admin",
        web_module=web,
        error="rate_limit_exceeded",
        include_ok=True,
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Get secret status",
    description="Returns secret configuration status (NO ACTUAL VALUES).",
    audit="secrets.status",
    plane=RoutePlane.ADMIN,
)
async def secrets_status_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/secrets/status

    Returns secret configuration status (NO ACTUAL VALUES).

    Security:
    - Admin-gated
    - Rate-limited
    """
    resp = _deny_if_remote_admin_not_allowed(request)
    if resp:
        return resp

    resp = _require_admin(request)
    if resp:
        return resp

    resp = _rate_limit_admin(request)
    if resp:
        return resp

    # Get status (no secret values)
    store = get_secret_store()
    status = store.get_status()

    return web.json_response(
        {
            "ok": True,
            "secrets": status,
            "warning": "Secrets stored server-side. Recommended for localhost-only usage.",
        }
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Write secret",
    description="Save API key to server store.",
    audit="secrets.write",
    plane=RoutePlane.ADMIN,
)
async def secrets_put_handler(request: web.Request) -> web.Response:
    """
    PUT /openclaw/secrets

    Save API key to server store.

    Body: {"provider": "openai"|"anthropic"|"generic", "api_key": "<YOUR_API_KEY>"}

    Security:
    - Admin-gated
    - Rate-limited
    - CSRF-protected (same-origin if no token)
    - Request body NEVER logged
    """
    # S62: Block secrets write in public+split mode
    try:
        # CRITICAL: package-relative import must stay first in ComfyUI runtime.
        from ..services.surface_guard import check_surface
    except ImportError:
        from services.surface_guard import check_surface  # type: ignore
    blocked = check_surface("secrets_write", request)
    if blocked:
        return blocked

    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    resp = _deny_if_remote_admin_not_allowed(request)
    if resp:
        return resp

    resp = _require_admin(request)
    if resp:
        return resp

    token_info = resolve_token_info(request)

    resp = _rate_limit_admin(request)
    if resp:
        return resp

    if request.content_length and request.content_length > MAX_BODY_SIZE:
        return web.json_response(
            {"ok": False, "error": "payload_too_large"}, status=413
        )

    # Parse body (do NOT log)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    # Validate required fields
    provider = body.get("provider")
    api_key = body.get("api_key")

    if not provider or not isinstance(provider, str):
        return web.json_response(
            {
                "ok": False,
                "error": "missing_provider",
                "message": "Field 'provider' is required (string)",
            },
            status=400,
        )

    provider = provider.strip().lower()
    if not provider or len(provider) > 64:
        return web.json_response({"ok": False, "error": "invalid_provider"}, status=400)

    if not api_key or not isinstance(api_key, str):
        return web.json_response(
            {
                "ok": False,
                "error": "missing_api_key",
                "message": "Field 'api_key' is required (non-empty string)",
            },
            status=400,
        )

    # Size check
    if len(api_key) > 4096:  # Reasonable key size limit
        return web.json_response(
            {
                "ok": False,
                "error": "api_key_too_long",
                "message": "API key exceeds maximum length (4KB)",
            },
            status=400,
        )

    # Save to store
    actor_ip = get_client_ip(request)
    try:
        store = get_secret_store()
        store.set_secret(provider, api_key)

        # Never log the secret value
        logger.info(f"S25: Saved secret for provider '{provider}' via UI")

        emit_audit_event(
            action="secrets.write",
            target=provider,
            outcome="allow",
            token_info=token_info,
            status_code=200,
            details={"actor_ip": actor_ip},
            request=request,
        )

        return web.json_response(
            {"ok": True, "message": f"Secret saved for provider '{provider}'"}
        )
    except Exception as e:
        logger.error(f"S25: Failed to save secret: {e}")
        emit_audit_event(
            action="secrets.write",
            target=provider,
            outcome="error",
            token_info=token_info,
            status_code=500,
            details={"actor_ip": actor_ip, "error": str(e)},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "save_failed", "message": str(e)}, status=500
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Delete secret",
    description="Clear provider secret.",
    audit="secrets.delete",
    plane=RoutePlane.ADMIN,
)
async def secrets_delete_handler(request: web.Request) -> web.Response:
    """
    DELETE /openclaw/secrets/{provider}

    Clear provider secret.

    Security:
    - Admin-gated
    - Rate-limited
    - CSRF-protected (same-origin if no token)
    """
    # S62: Block secrets write in public+split mode
    try:
        # CRITICAL: package-relative import must stay first in ComfyUI runtime.
        from ..services.surface_guard import check_surface
    except ImportError:
        from services.surface_guard import check_surface  # type: ignore
    blocked = check_surface("secrets_write", request)
    if blocked:
        return blocked

    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    resp = _deny_if_remote_admin_not_allowed(request)
    if resp:
        return resp

    resp = _require_admin(request)
    if resp:
        return resp

    token_info = resolve_token_info(request)

    resp = _rate_limit_admin(request)
    if resp:
        return resp

    # Get provider from path
    provider = request.match_info.get("provider")
    if not provider:
        return web.json_response({"ok": False, "error": "missing_provider"}, status=400)

    provider = provider.strip().lower()
    if not provider or len(provider) > 64:
        return web.json_response({"ok": False, "error": "invalid_provider"}, status=400)

    # Clear from store
    actor_ip = get_client_ip(request)
    try:
        store = get_secret_store()
        removed = store.clear_secret(provider)

        if removed:
            logger.info(f"S25: Cleared secret for provider '{provider}' via UI")
            emit_audit_event(
                action="secrets.delete",
                target=provider,
                outcome="allow",
                token_info=token_info,
                status_code=200,
                details={"actor_ip": actor_ip},
                request=request,
            )
            return web.json_response(
                {"ok": True, "message": f"Secret cleared for provider '{provider}'"}
            )
        else:
            emit_audit_event(
                action="secrets.delete",
                target=provider,
                outcome="deny",
                token_info=token_info,
                status_code=404,
                details={"actor_ip": actor_ip, "reason": "not_found"},
                request=request,
            )
            return web.json_response(
                {
                    "ok": False,
                    "error": "not_found",
                    "message": f"No secret found for provider '{provider}'",
                },
                status=404,
            )
    except Exception as e:
        logger.error(f"S25: Failed to clear secret: {e}")
        emit_audit_event(
            action="secrets.delete",
            target=provider,
            outcome="error",
            token_info=token_info,
            status_code=500,
            details={"actor_ip": actor_ip, "error": str(e)},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "clear_failed", "message": str(e)}, status=500
        )
