"""
Scheduler CRUD API (R4).
REST endpoints for managing persistent schedules.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web as aiohttp_web

# Import discipline:
# - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
# - Unit tests: allow top-level fallbacks.
#
# IMPORTANT: Avoid a broad `try/except ImportError` here. Falling back to `services.*` in ComfyUI
# can silently import another pack's module and break auth/approval semantics.
if __package__ and "." in __package__:
    from ..services.access_control import resolve_token_info
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.scheduler.delivery_contract import (
        DeliveryContractError,
        normalize_schedule_delivery,
    )
    from ..services.scheduler.delivery_outbox import (
        ALL_STATES,
        DeliveryOutboxError,
        get_delivery_outbox,
    )
    from ..services.scheduler.models import Schedule, TriggerType
    from ..services.scheduler.storage import get_schedule_store
    from ..services.templates import is_template_allowed
    from ..services.tenant_context import (
        TenantBoundaryError,
        get_current_tenant_id,
        request_tenant_scope,
    )
    from ..services.webhook_auth import AuthError
else:  # pragma: no cover (test-only import mode)
    from services.access_control import resolve_token_info
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from services.rate_limit import build_rate_limit_response, check_rate_limit
    from services.scheduler.delivery_contract import (  # type: ignore
        DeliveryContractError,
        normalize_schedule_delivery,
    )
    from services.scheduler.delivery_outbox import (
        ALL_STATES,
        DeliveryOutboxError,
        get_delivery_outbox,
    )
    from services.scheduler.models import Schedule, TriggerType  # type: ignore
    from services.scheduler.storage import get_schedule_store  # type: ignore
    from services.templates import is_template_allowed  # type: ignore
    from services.tenant_context import (
        TenantBoundaryError,
        get_current_tenant_id,
        request_tenant_scope,
    )
    from services.webhook_auth import AuthError  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.api.schedules")
web = import_aiohttp_web()


def _delivery_error_response(exc: DeliveryContractError) -> web.Response:
    return web.json_response({"error": str(exc), "code": exc.code}, status=400)


_DELIVERY_ID = re.compile(r"^dlv_[0-9a-f]{24}$")
_OUTBOX_ERROR_STATUS = {
    "invalid_outcome": 400,
    "invalid_reason": 400,
    "lease_token_required": 400,
    "not_found": 404,
    "lease_mismatch": 409,
    "already_finalized": 409,
}
_MAX_CLAIM_PLATFORMS = 8
_DEFAULT_LEASE_SEC = 720


def _outbox_error(code: str, status: int) -> aiohttp_web.Response:
    return web.json_response({"ok": False, "error": code}, status=status)


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int | None:
    """Parse an optional integer and clamp it; None means the value was malformed."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(number, high))


async def _json_object(request: aiohttp_web.Request) -> dict | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _runs_with_delivery(runs: list) -> list[dict]:
    """Run dicts plus the delivery summary of each run's scheduled tick, when one exists."""
    rows = [run.to_dict() for run in runs]
    summaries = get_delivery_outbox().summaries_for_keys(
        (row.get("idempotency_key") or "" for row in rows),
        tenant_id=get_current_tenant_id(),
    )
    for row in rows:
        summary = summaries.get(row.get("idempotency_key") or "")
        if summary:
            row["delivery"] = summary
    return rows


def _get_scheduler_runner():
    if __package__ and "." in __package__:
        from ..services.scheduler.runner import get_scheduler_runner
    else:  # pragma: no cover (test-only import mode)
        from services.scheduler.runner import get_scheduler_runner  # type: ignore
    return get_scheduler_runner()


def _get_run_history():
    if __package__ and "." in __package__:
        from ..services.scheduler.history import get_run_history
    else:  # pragma: no cover (test-only import mode)
        from services.scheduler.history import get_run_history  # type: ignore
    return get_run_history()


class ScheduleHandlers:
    """
    CRUD handlers for /moltbot/schedules endpoints.
    All endpoints require admin token authentication.
    """

    def __init__(self, require_admin_token_fn=None, template_checker=None):
        """
        Args:
            require_admin_token_fn: Function to validate admin token.
                Expected to return either:
                - (allowed: bool, error: Optional[str])  OR
                - an awaitable resolving to that tuple.
            template_checker: Function to check if template_id is allowed.
        """
        self._require_admin_token = require_admin_token_fn
        self._template_checker = template_checker or is_template_allowed
        self._store = get_schedule_store()

    async def _check_auth(self, request: web.Request) -> None:
        """Require admin token for all schedule operations."""
        if self._require_admin_token:
            import inspect

            result = self._require_admin_token(request)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, tuple):
                allowed, error = result
                if not allowed:
                    raise AuthError(error or "Unauthorized")

    async def list_schedules(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules - List all schedules."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedules = self._store.list_all()

        # Optional filter by enabled status
        enabled_filter = request.query.get("enabled")
        if enabled_filter is not None:
            enabled = enabled_filter.lower() == "true"
            schedules = [s for s in schedules if s.enabled == enabled]

        return web.json_response(
            {
                "schedules": [s.to_dict() for s in schedules],
                "count": len(schedules),
            }
        )

    async def get_schedule(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules/{schedule_id} - Get a single schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        schedule = self._store.get(schedule_id)

        if not schedule:
            return web.json_response({"error": "Schedule not found"}, status=404)

        return web.json_response({"schedule": schedule.to_dict()})

    async def create_schedule(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules - Create a new schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Validate required fields
        if not data.get("name"):
            return web.json_response({"error": "name is required"}, status=400)
        if not data.get("template_id"):
            return web.json_response({"error": "template_id is required"}, status=400)

        # Check template allowlist
        template_id = data["template_id"]
        if not self._template_checker(template_id):
            return web.json_response(
                {"error": f"template_id '{template_id}' not found"},
                status=404,
            )

        # Build schedule
        try:
            schedule = Schedule(
                schedule_id=Schedule.generate_id(),
                name=data["name"],
                template_id=template_id,
                trigger_type=TriggerType(data.get("trigger_type", "interval")),
                cron_expr=data.get("cron_expr"),
                interval_sec=data.get("interval_sec"),
                inputs=data.get("inputs", {}),
                delivery=data.get("delivery"),
                timezone=data.get("timezone", "local"),
                enabled=data.get("enabled", True),
            )
        except DeliveryContractError as e:
            return _delivery_error_response(e)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        # Store
        if not self._store.add(schedule):
            return web.json_response({"error": "Failed to create schedule"}, status=500)

        logger.info(f"Created schedule: {schedule.schedule_id}")
        return web.json_response(
            {"schedule": schedule.to_dict(), "created": True}, status=201
        )

    async def update_schedule(self, request: web.Request) -> web.Response:
        """PUT /moltbot/schedules/{schedule_id} - Update a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Validate template_id if changing
        if "template_id" in data:
            template_id = data["template_id"]
            if not self._template_checker(template_id):
                return web.json_response(
                    {"error": f"template_id '{template_id}' not found"},
                    status=404,
                )
            existing.template_id = template_id

        # Update allowed fields
        if "name" in data:
            existing.name = data["name"]
        if "trigger_type" in data:
            existing.trigger_type = TriggerType(data["trigger_type"])
        if "cron_expr" in data:
            existing.cron_expr = data["cron_expr"]
        if "interval_sec" in data:
            existing.interval_sec = data["interval_sec"]
        if "inputs" in data:
            existing.inputs = data["inputs"]
        if "delivery" in data:
            try:
                existing.delivery = normalize_schedule_delivery(data["delivery"])
            except DeliveryContractError as e:
                return _delivery_error_response(e)
        if "timezone" in data:
            existing.timezone = data["timezone"]
        if "enabled" in data:
            existing.enabled = bool(data["enabled"])

        # Re-validate
        try:
            existing.validate()
        except DeliveryContractError as e:
            return _delivery_error_response(e)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        # Save
        from datetime import datetime
        from datetime import timezone as tz

        existing.updated_at = datetime.now(tz.utc).isoformat()

        if not self._store.update(existing):
            return web.json_response({"error": "Failed to update schedule"}, status=500)

        logger.info(f"Updated schedule: {schedule_id}")
        return web.json_response({"schedule": existing.to_dict(), "updated": True})

    async def delete_schedule(self, request: web.Request) -> web.Response:
        """DELETE /moltbot/schedules/{schedule_id} - Delete a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        if not self._store.delete(schedule_id):
            return web.json_response({"error": "Failed to delete schedule"}, status=500)

        logger.info(f"Deleted schedule: {schedule_id}")
        return web.json_response({"deleted": True, "schedule_id": schedule_id})

    async def toggle_schedule(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules/{schedule_id}/toggle - Toggle enabled status."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        existing.enabled = not existing.enabled

        from datetime import datetime
        from datetime import timezone as tz

        existing.updated_at = datetime.now(tz.utc).isoformat()

        if not self._store.update(existing):
            return web.json_response({"error": "Failed to toggle schedule"}, status=500)

        logger.info(f"Toggled schedule {schedule_id}: enabled={existing.enabled}")
        return web.json_response(
            {
                "schedule_id": schedule_id,
                "enabled": existing.enabled,
            }
        )

    async def run_now(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules/{schedule_id}/run - Manually trigger a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        schedule = self._store.get(schedule_id)

        if not schedule:
            return web.json_response({"error": "Schedule not found"}, status=404)

        # Trigger immediate execution via scheduler runner
        import time

        runner = _get_scheduler_runner()
        if runner.is_execution_delegated():
            # IMPORTANT: in public+split mode, embedded scheduler execution must remain blocked.
            return web.json_response(
                {
                    "error": "Scheduler execution is delegated to external control plane",
                    "code": "scheduler_delegated",
                    "remediation": "Use external scheduler control plane in split mode.",
                },
                status=503,
            )

        try:
            runner._execute_schedule(schedule, time.time())
            return web.json_response(
                {
                    "triggered": True,
                    "schedule_id": schedule_id,
                    "template_id": schedule.template_id,
                }
            )
        except Exception as e:
            logger.error(f"Manual run failed: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def list_runs(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules/{schedule_id}/runs - List runs for a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            return web.json_response({"error": "Unauthorized"}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")

        # Verify schedule exists
        if not self._store.get(schedule_id):
            return web.json_response({"error": "Schedule not found"}, status=404)

        history = _get_run_history()
        limit = int(request.query.get("limit", "100"))
        offset = int(request.query.get("offset", "0"))
        status = request.query.get("status")

        runs = history.list_runs(
            schedule_id=schedule_id,
            status=status,
            limit=min(limit, 500),
            offset=offset,
        )

        return web.json_response(
            {
                "runs": _runs_with_delivery(runs),
                "count": len(runs),
                "total": history.count_runs(schedule_id),
            }
        )

    async def list_all_runs(self, request: web.Request) -> web.Response:
        """GET /moltbot/runs - List all runs across all schedules."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            return web.json_response({"error": "Unauthorized"}, status=403)

        history = _get_run_history()
        limit = int(request.query.get("limit", "100"))
        offset = int(request.query.get("offset", "0"))
        status = request.query.get("status")
        schedule_id = request.query.get("schedule_id")

        runs = history.list_runs(
            schedule_id=schedule_id,
            status=status,
            limit=min(limit, 500),
            offset=offset,
        )

        return web.json_response(
            {
                "runs": _runs_with_delivery(runs),
                "count": len(runs),
                "total": history.count_runs(),
            }
        )

    async def _delivery_guard(
        self, request: aiohttp_web.Request, rate_class: str
    ) -> aiohttp_web.Response | None:
        if not check_rate_limit(request, rate_class):
            return build_rate_limit_response(
                request,
                rate_class,
                web_module=web,
                error="Rate limit exceeded",
                include_ok=True,
            )
        # Fail closed: delivery routes are never open, even without an injected checker.
        if self._require_admin_token is None:
            return _outbox_error("admin_token_required", 403)
        try:
            await self._check_auth(request)
        except Exception:
            return _outbox_error("admin_token_required", 403)
        return None

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.LOW,
        summary="List schedule delivery intents",
        description="Returns bounded schedule delivery intents with normalized targets and states.",
        audit="schedules.deliveries.list",
        plane=RoutePlane.ADMIN,
    )
    async def list_deliveries(
        self, request: aiohttp_web.Request
    ) -> aiohttp_web.Response:
        """GET /openclaw/schedule-deliveries"""
        if (guard := await self._delivery_guard(request, "admin")) is not None:
            return guard
        state = request.query.get("state") or None
        if state is not None and state not in ALL_STATES:
            return _outbox_error("invalid_state", 400)
        platform = (request.query.get("platform") or "").strip().lower() or None
        limit = _bounded_int(request.query.get("limit"), default=50, low=1, high=200)
        if limit is None:
            return _outbox_error("invalid_limit", 400)
        try:
            with request_tenant_scope(
                request=request,
                token_info=resolve_token_info(request),
                allow_default_when_missing=True,
            ) as tenant:
                intents = get_delivery_outbox().list_intents(
                    tenant_id=tenant.tenant_id,
                    state=state,
                    platform=platform,
                    limit=limit,
                )
        except TenantBoundaryError as exc:
            return _outbox_error(exc.code, 403)
        return web.json_response(
            {"ok": True, "deliveries": [intent.to_public() for intent in intents]}
        )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Claim schedule delivery intents",
        description="Leases pending schedule delivery intents for the requested connector platforms.",
        audit="schedules.deliveries.claim",
        plane=RoutePlane.ADMIN,
    )
    async def claim_deliveries(
        self, request: aiohttp_web.Request
    ) -> aiohttp_web.Response:
        """POST /openclaw/schedule-deliveries/claim"""
        if (guard := await self._delivery_guard(request, "connector")) is not None:
            return guard
        body = await _json_object(request)
        if body is None:
            return _outbox_error("invalid_json", 400)
        platforms = body.get("platforms")
        if (
            not isinstance(platforms, list)
            or not 0 < len(platforms) <= _MAX_CLAIM_PLATFORMS
            or not all(isinstance(item, str) for item in platforms)
        ):
            return _outbox_error("invalid_platforms", 400)
        limit = _bounded_int(body.get("limit"), default=5, low=1, high=10)
        lease_sec = _bounded_int(
            body.get("lease_sec"), default=_DEFAULT_LEASE_SEC, low=60, high=7200
        )
        if limit is None or lease_sec is None:
            return _outbox_error("invalid_limit", 400)
        try:
            with request_tenant_scope(
                request=request,
                token_info=resolve_token_info(request),
                allow_default_when_missing=True,
            ) as tenant:
                claimed = get_delivery_outbox().claim(
                    tenant_id=tenant.tenant_id,
                    platforms=[item.strip().lower() for item in platforms],
                    limit=limit,
                    lease_sec=lease_sec,
                )
        except TenantBoundaryError as exc:
            return _outbox_error(exc.code, 403)
        return web.json_response(
            {"ok": True, "deliveries": [item.to_public() for item in claimed]}
        )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Acknowledge a schedule delivery",
        description="Records the bounded outcome of a leased schedule delivery intent.",
        audit="schedules.deliveries.ack",
        plane=RoutePlane.ADMIN,
    )
    async def ack_delivery(self, request: aiohttp_web.Request) -> aiohttp_web.Response:
        """POST /openclaw/schedule-deliveries/{delivery_id}/ack"""
        if (guard := await self._delivery_guard(request, "connector")) is not None:
            return guard
        delivery_id = request.match_info.get("delivery_id", "")
        if not _DELIVERY_ID.match(delivery_id):
            return _outbox_error("not_found", 404)
        body = await _json_object(request)
        if body is None:
            return _outbox_error("invalid_json", 400)
        lease_token = body.get("lease_token")
        outcome = body.get("outcome")
        reason = body.get("reason")
        if (
            not isinstance(lease_token, str)
            or len(lease_token) > 128
            or not isinstance(outcome, str)
            or (reason is not None and not isinstance(reason, str))
        ):
            return _outbox_error("invalid_ack", 400)
        try:
            with request_tenant_scope(
                request=request,
                token_info=resolve_token_info(request),
                allow_default_when_missing=True,
            ) as tenant:
                result = get_delivery_outbox().ack(
                    tenant_id=tenant.tenant_id,
                    delivery_id=delivery_id,
                    lease_token=lease_token,
                    outcome=outcome,
                    reason=reason,
                )
        except TenantBoundaryError as exc:
            return _outbox_error(exc.code, 403)
        except DeliveryOutboxError as exc:
            return _outbox_error(exc.code, _OUTBOX_ERROR_STATUS.get(exc.code, 400))
        return web.json_response(
            {
                "ok": True,
                "status": (
                    "already_acknowledged" if result.repeated else "acknowledged"
                ),
                "delivery": result.intent.to_public(),
            }
        )


def register_schedule_routes(app: web.Application, require_admin_token_fn=None) -> None:
    """Register schedule CRUD routes on the aiohttp app."""
    handlers = ScheduleHandlers(require_admin_token_fn)

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        routes = [
            ("GET", f"{prefix}/schedules", handlers.list_schedules),
            ("POST", f"{prefix}/schedules", handlers.create_schedule),
            ("GET", f"{prefix}/schedules/{{schedule_id}}", handlers.get_schedule),
            ("PUT", f"{prefix}/schedules/{{schedule_id}}", handlers.update_schedule),
            ("DELETE", f"{prefix}/schedules/{{schedule_id}}", handlers.delete_schedule),
            (
                "POST",
                f"{prefix}/schedules/{{schedule_id}}/toggle",
                handlers.toggle_schedule,
            ),
            ("POST", f"{prefix}/schedules/{{schedule_id}}/run", handlers.run_now),
            # R9: Run history endpoints
            ("GET", f"{prefix}/schedules/{{schedule_id}}/runs", handlers.list_runs),
            ("GET", f"{prefix}/runs", handlers.list_all_runs),
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

    # New delivery routes use the canonical prefix only; legacy aliases cover old routes.
    delivery_routes = [
        ("GET", "/openclaw/schedule-deliveries", handlers.list_deliveries),
        ("POST", "/openclaw/schedule-deliveries/claim", handlers.claim_deliveries),
        (
            "POST",
            "/openclaw/schedule-deliveries/{delivery_id}/ack",
            handlers.ack_delivery,
        ),
    ]
    for method, path, handler in delivery_routes:
        for route_path in (path, "/api" + path):
            with contextlib.suppress(RuntimeError):
                app.router.add_route(method, route_path, handler)

    logger.info("Registered schedule CRUD routes (dual)")
