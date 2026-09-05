"""
Preset Management API (F22).
CRUD endpoints for local presets.
"""

import logging
import time
from typing import Optional

if __package__ and "." in __package__:
    from ..services.import_fallback import import_attrs_dual
else:
    from services.import_fallback import import_attrs_dual  # type: ignore

(require_admin_token, resolve_token_info) = import_attrs_dual(
    __package__,
    "..services.access_control",
    "services.access_control",
    ("require_admin_token", "resolve_token_info"),
)
(import_aiohttp_web,) = import_attrs_dual(
    __package__,
    "..services.aiohttp_compat",
    "services.aiohttp_compat",
    ("import_aiohttp_web",),
)
(AuthTier, RiskTier, RoutePlane, endpoint_metadata) = import_attrs_dual(
    __package__,
    "..services.endpoint_manifest",
    "services.endpoint_manifest",
    ("AuthTier", "RiskTier", "RoutePlane", "endpoint_metadata"),
)
(Preset, preset_store) = import_attrs_dual(
    __package__,
    "..services.presets",
    "services.presets",
    ("Preset", "preset_store"),
)
(TenantBoundaryError, request_tenant_scope) = import_attrs_dual(
    __package__,
    "..services.tenant_context",
    "services.tenant_context",
    ("TenantBoundaryError", "request_tenant_scope"),
)
(get_env_value,) = import_attrs_dual(
    __package__,
    "..services.env_aliases",
    "services.env_aliases",
    ("get_env_value",),
)

logger = logging.getLogger("ComfyUI-OpenClaw.api.presets")
web = import_aiohttp_web()


class PresetHandlers:
    """Handlers for preset API."""

    @endpoint_metadata(
        auth=AuthTier.PUBLIC,  # Conditionally public
        risk=RiskTier.LOW,
        summary="List presets",
        description="List available presets (dynamic auth).",
        audit="presets.list",
        plane=RoutePlane.USER,
    )
    async def list_presets(self, request: web.Request) -> web.Response:
        """
        GET /moltbot/presets
        Query: category, tag
        """
        # Milestone B: Auth Check for Read Endpoints
        # Policy: Public by default (local-first), but locked down if:
        # 1. OPENCLAW_PRESETS_PUBLIC_READ (or legacy MOLTBOT_PRESETS_PUBLIC_READ) = '0' (Explicitly disabled)
        # 2. OPENCLAW_STRICT_LOCALHOST_AUTH (or legacy MOLTBOT_STRICT_LOCALHOST_AUTH) = '1' (Implicit strict mode)

        public_read = get_env_value("OPENCLAW_PRESETS_PUBLIC_READ", default="1") == "1"
        strict_auth = (
            get_env_value("OPENCLAW_STRICT_LOCALHOST_AUTH", default="1") == "1"
        )

        # If public read is OFF, or Strict Mode is ON, we gate it.
        if not public_read or strict_auth:
            allowed, error = require_admin_token(request)
            if not allowed:
                return web.json_response({"error": error or "Unauthorized"}, status=403)

        category = request.query.get("category")
        tag = request.query.get("tag")
        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                presets = preset_store.list_presets(
                    category=category,
                    tag=tag,
                    tenant_id=tenant.tenant_id,
                )
                return web.json_response([p.to_dict() for p in presets])
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )

    @endpoint_metadata(
        auth=AuthTier.PUBLIC,  # Conditionally public
        risk=RiskTier.LOW,
        summary="Get preset",
        description="Get preset details (dynamic auth).",
        audit="presets.get",
        plane=RoutePlane.USER,
    )
    async def get_preset(self, request: web.Request) -> web.Response:
        """GET /moltbot/presets/{preset_id}"""
        # Milestone B: Auth Check
        public_read = get_env_value("OPENCLAW_PRESETS_PUBLIC_READ", default="1") == "1"
        strict_auth = (
            get_env_value("OPENCLAW_STRICT_LOCALHOST_AUTH", default="1") == "1"
        )

        if not public_read or strict_auth:
            allowed, error = require_admin_token(request)
            if not allowed:
                return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                preset = preset_store.get_preset(preset_id, tenant_id=tenant.tenant_id)
                if not preset:
                    return web.json_response({"error": "Not Found"}, status=404)

                return web.json_response(preset.to_dict())
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Create preset",
        description="Create a new preset.",
        audit="presets.create",
        plane=RoutePlane.ADMIN,
    )
    async def create_preset(self, request: web.Request) -> web.Response:
        """POST /moltbot/presets"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        name = data.get("name")
        content = data.get("content")

        if not name or not content:
            return web.json_response({"error": "Name and Content required"}, status=400)
        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                # Create object
                preset = Preset.new(
                    name=data["name"],
                    content=data["content"],
                    category=data.get("category", "general"),
                    tags=data.get("tags", []),
                )
                preset.tenant_id = tenant.tenant_id

                # Milestone E: Schema Validation
                try:
                    preset.validate_content()
                except ValueError as e:
                    return web.json_response(
                        {"error": f"Validation Error: {str(e)}"}, status=400
                    )

                # Save
                preset_store.save_preset(preset)
                logger.info(f"Created preset {preset.id} ({preset.name})")

                return web.json_response(preset.to_dict(), status=201)
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )
        except Exception as e:
            logger.error(f"Failed to create preset: {e}")
            return web.json_response({"error": str(e)}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Update preset",
        description="Update an existing preset.",
        audit="presets.update",
        plane=RoutePlane.ADMIN,
    )
    async def update_preset(self, request: web.Request) -> web.Response:
        """PUT /moltbot/presets/{preset_id}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                preset = preset_store.get_preset(preset_id, tenant_id=tenant.tenant_id)
                if not preset:
                    return web.json_response({"error": "Not Found"}, status=404)

                try:
                    data = await request.json()
                except Exception:
                    return web.json_response({"error": "Invalid JSON"}, status=400)

                # Update fields
                if "name" in data:
                    preset.name = data["name"]
                if "content" in data:
                    preset.content = data["content"]
                if "category" in data:
                    preset.category = data["category"]
                if "tags" in data:
                    preset.tags = data["tags"]

                # Milestone E: Schema Validation
                try:
                    preset.validate_content()
                except ValueError as e:
                    return web.json_response(
                        {"error": f"Validation Error: {str(e)}"}, status=400
                    )

                preset.updated_at = time.time()
                preset_store.save_preset(preset)

                return web.json_response(preset.to_dict())
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.HIGH,
        summary="Delete preset",
        description="Delete a preset.",
        audit="presets.delete",
        plane=RoutePlane.ADMIN,
    )
    async def delete_preset(self, request: web.Request) -> web.Response:
        """DELETE /moltbot/presets/{preset_id}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                if preset_store.delete_preset(preset_id, tenant_id=tenant.tenant_id):
                    return web.json_response({"ok": True})
                return web.json_response({"error": "Not Found or Failed"}, status=404)
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )


def register_preset_routes(app: web.Application):
    """Register routes."""
    handlers = PresetHandlers()

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        routes = [
            ("GET", f"{prefix}/presets", handlers.list_presets),
            ("POST", f"{prefix}/presets", handlers.create_preset),
            ("GET", f"{prefix}/presets/{{preset_id}}", handlers.get_preset),
            ("PUT", f"{prefix}/presets/{{preset_id}}", handlers.update_preset),
            ("DELETE", f"{prefix}/presets/{{preset_id}}", handlers.delete_preset),
        ]

        for method, path, handler in routes:
            # 1. Legacy
            try:
                app.router.add_route(method, path, handler)
            except RuntimeError:
                pass

            # 2. /api Shim aligned
            try:
                app.router.add_route(method, "/api" + path, handler)
            except RuntimeError:
                pass

    logger.info("Registered preset API routes (dual)")
