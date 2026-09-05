"""
Preflight API Handler (R42).

Exposes POST /openclaw/preflight to run diagnostics on a workflow payload.
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
    from ..services.env_aliases import get_env_value
    from ..services.preflight import (
        _get_node_class_mappings,
        get_model_inventory_snapshot,
        run_preflight_check,
    )
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.request_ip import get_client_ip
    from ..services.workflow_portability import get_workflow_portability_contract
else:  # pragma: no cover (test-only import mode)
    from models.schemas import MAX_BODY_SIZE  # type: ignore
    from services.access_control import is_loopback, require_admin_token  # type: ignore
    from services.env_aliases import get_env_value
    from services.preflight import (  # type: ignore
        _get_node_class_mappings,
        get_model_inventory_snapshot,
        run_preflight_check,
    )
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.request_ip import get_client_ip  # type: ignore
    from services.workflow_portability import (  # type: ignore
        get_workflow_portability_contract,
    )

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

logger = logging.getLogger("ComfyUI-OpenClaw.api.preflight")


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
    return web.json_response(
        {
            "ok": False,
            "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
        },
        status=403,
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,  # Read-only analysis
    summary="Run preflight check",
    description="Analyze workflow JSON for missing nodes and models.",
    audit="preflight.analyze",
    plane=RoutePlane.ADMIN,
)
async def preflight_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/preflight
    Analyze workflow JSON for missing nodes and models.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate limit: admin-grade endpoint (inventory leak + CPU cost)
    if not check_rate_limit(request, "admin"):
        return build_rate_limit_response(
            request,
            "admin",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    # Body Size Check
    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith("application/json"):
        return web.json_response(
            {"ok": False, "error": "unsupported_media_type"}, status=415
        )

    try:
        raw_body = await request.content.read(MAX_BODY_SIZE + 1)
        if len(raw_body) > MAX_BODY_SIZE:
            return web.json_response(
                {"ok": False, "error": "payload_too_large"}, status=413
            )
        data = json.loads(raw_body)
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    # Admin boundary (localhost convenience mode if no token configured)
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": error or "unauthorized"}, status=403
        )
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    # Extract workflow
    # It might be in { "prompt": ... } or root
    workflow = data.get("prompt") or data

    if not isinstance(workflow, dict):
        return web.json_response(
            {
                "ok": False,
                "error": "invalid_payload",
                "detail": "Expected JSON object with workflow data",
            },
            status=400,
        )

    # Run Diagnostics
    try:
        report = run_preflight_check(workflow)
        return web.json_response(report)
    except Exception as e:
        logger.exception("Preflight check failed")
        return web.json_response(
            {"ok": False, "error": "internal_error", "detail": str(e)}, status=500
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Get inventory",
    description="Returns a snapshot of available nodes and models.",
    audit="preflight.inventory",
    plane=RoutePlane.ADMIN,
)
async def inventory_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/preflight/inventory
    Returns a snapshot of available nodes and models for the Explorer UI.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate Limit
    if not check_rate_limit(request, "admin"):
        return build_rate_limit_response(
            request,
            "admin",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    # Admin boundary (localhost convenience mode if no token configured)
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": error or "unauthorized"}, status=403
        )
    deny_resp = _deny_remote_admin_if_needed(request)
    if deny_resp:
        return deny_resp

    try:
        # Nodes (Classes only needed)
        nodes_map = _get_node_class_mappings()
        node_classes = sorted(list(nodes_map.keys()))

        inventory_snapshot = get_model_inventory_snapshot()

        return web.json_response(
            {
                "ok": True,
                "nodes": node_classes,
                "models": inventory_snapshot["models"],
                "portability_contract": get_workflow_portability_contract(),
                "snapshot_ts": inventory_snapshot["snapshot_ts"],
                "scan_state": inventory_snapshot["scan_state"],
                "stale": inventory_snapshot["stale"],
                "last_error": inventory_snapshot["last_error"],
            }
        )
    except Exception as e:
        logger.exception("Inventory fetch failed")
        return web.json_response(
            {"ok": False, "error": "internal_error", "detail": str(e)}, status=500
        )
