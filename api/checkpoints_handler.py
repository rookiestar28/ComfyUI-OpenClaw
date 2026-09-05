"""
Checkpoints API Handlers (R47).
Exposes endpoints for listing, creating, retrieving, and deleting workflow checkpoints.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore

if __package__ and "." in __package__:
    from ..models.schemas import MAX_BODY_SIZE
    from ..services.access_control import is_loopback, require_admin_token
    from ..services.checkpoints import (
        create_checkpoint,
        delete_checkpoint,
        get_checkpoint,
        list_checkpoints,
    )
    from ..services.env_aliases import get_env_value
    from ..services.rate_limit import build_rate_limit_payload, check_rate_limit
    from ..services.request_ip import get_client_ip
else:  # pragma: no cover (test-only import mode)
    from models.schemas import MAX_BODY_SIZE  # type: ignore
    from services.access_control import is_loopback, require_admin_token  # type: ignore
    from services.checkpoints import (  # type: ignore
        create_checkpoint,
        delete_checkpoint,
        get_checkpoint,
        list_checkpoints,
    )
    from services.env_aliases import get_env_value
    from services.rate_limit import (  # type: ignore
        build_rate_limit_payload,
        check_rate_limit,
    )
    from services.request_ip import get_client_ip  # type: ignore


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


logger = logging.getLogger("ComfyUI-OpenClaw.api.checkpoints")


def _json_resp(data: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _remote_admin_allowed() -> bool:
    val = (
        (get_env_value("OPENCLAW_ALLOW_REMOTE_ADMIN", default="") or "").strip().lower()
    )
    return val in ("1", "true", "yes", "on")


def _deny_remote_admin_if_needed(request: web.Request) -> web.Response | None:
    if _remote_admin_allowed():
        return None
    client_ip = get_client_ip(request)
    if is_loopback(client_ip):
        return None
    return _json_resp(
        {
            "ok": False,
            "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
        },
        403,
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List checkpoints",
    description="List available workflow checkpoints.",
    audit="checkpoints.list",
    plane=RoutePlane.ADMIN,
)
async def list_checkpoints_handler(request: web.Request) -> web.Response:
    """GET /openclaw/checkpoints"""
    if web is None:
        raise RuntimeError("aiohttp not available")

    if not check_rate_limit(request, "admin"):
        return _json_resp(
            build_rate_limit_payload(
                request,
                "admin",
                error="rate_limit_exceeded",
                include_ok=True,
            ),
            429,
        )

    allowed, error = require_admin_token(request)
    if not allowed:
        return _json_resp({"ok": False, "error": error or "unauthorized"}, 403)
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    try:
        checkpoints = list_checkpoints()
        return _json_resp({"ok": True, "checkpoints": checkpoints})
    except Exception as e:
        logger.exception("List checkpoints failed")
        return _json_resp({"ok": False, "error": str(e)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Create checkpoint",
    description="Create a new workflow checkpoint.",
    audit="checkpoints.create",
    plane=RoutePlane.ADMIN,
)
async def create_checkpoint_handler(request: web.Request) -> web.Response:
    """POST /openclaw/checkpoints"""
    if web is None:
        raise RuntimeError("aiohttp not available")

    if not check_rate_limit(request, "admin"):
        return _json_resp(
            build_rate_limit_payload(
                request,
                "admin",
                error="rate_limit_exceeded",
                include_ok=True,
            ),
            429,
        )

    # Body Size Check
    if request.content_length and request.content_length > MAX_BODY_SIZE:
        return _json_resp({"ok": False, "error": "payload_too_large"}, 413)

    try:
        data = await request.json()
    except Exception:
        return _json_resp({"ok": False, "error": "invalid_json"}, 400)

    # Admin boundary (localhost convenience mode if no token configured)
    allowed, error = require_admin_token(request)
    if not allowed:
        return _json_resp({"ok": False, "error": error or "unauthorized"}, 403)
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    # Extract info
    workflow = data.get("workflow") or data.get("prompt")
    name = data.get("name", "Untitled Snapshot")
    description = data.get("description", "")

    if not workflow or not isinstance(workflow, dict):
        return _json_resp({"ok": False, "error": "missing_workflow"}, 400)

    try:
        meta = create_checkpoint(name, workflow, description)
        return _json_resp({"ok": True, "checkpoint": meta}, 201)
    except ValueError as e:
        return _json_resp(
            {"ok": False, "error": "validation_error", "detail": str(e)}, 400
        )
    except Exception as e:
        logger.exception("Create checkpoint failed")
        return _json_resp({"ok": False, "error": str(e)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Get checkpoint",
    description="Retrieve specific checkpoint details.",
    audit="checkpoints.get",
    plane=RoutePlane.ADMIN,
)
async def get_checkpoint_handler(request: web.Request) -> web.Response:
    """GET /openclaw/checkpoints/{id}"""
    if web is None:
        raise RuntimeError("aiohttp not available")

    checkpoint_id = request.match_info.get("id")
    if not checkpoint_id:
        return _json_resp({"ok": False, "error": "missing_id"}, 400)

    allowed, error = require_admin_token(request)
    if not allowed:
        return _json_resp({"ok": False, "error": error or "unauthorized"}, 403)
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    try:
        data = get_checkpoint(checkpoint_id)
        if not data:
            return _json_resp({"ok": False, "error": "not_found"}, 404)
        return _json_resp({"ok": True, "checkpoint": data})
    except Exception as e:
        logger.exception("Get checkpoint failed")
        return _json_resp({"ok": False, "error": str(e)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Delete checkpoint",
    description="Delete a workflow checkpoint.",
    audit="checkpoints.delete",
    plane=RoutePlane.ADMIN,
)
async def delete_checkpoint_handler(request: web.Request) -> web.Response:
    """DELETE /openclaw/checkpoints/{id}"""
    if web is None:
        raise RuntimeError("aiohttp not available")

    checkpoint_id = request.match_info.get("id")
    if not checkpoint_id:
        return _json_resp({"ok": False, "error": "missing_id"}, 400)

    allowed, error = require_admin_token(request)
    if not allowed:
        return _json_resp({"ok": False, "error": error or "unauthorized"}, 403)
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    try:
        deleted = delete_checkpoint(checkpoint_id)
        if not deleted:
            return _json_resp({"ok": False, "error": "not_found"}, 404)
        return _json_resp({"ok": True})
    except Exception as e:
        logger.exception("Delete checkpoint failed")
        return _json_resp({"ok": False, "error": str(e)}, 500)
