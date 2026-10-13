"""
Approval API Endpoints (S7/F12).
REST endpoints for managing approval requests.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

try:
    from ..services.access_control import resolve_token_info
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.approvals.models import ApprovalStatus
    from ..services.approvals.service import get_approval_service
    from ..services.audit import emit_audit_event
    from ..services.management_query import bounded_scan_collect, normalize_limit_offset
    from ..services.tenant_context import TenantBoundaryError, request_tenant_scope
    from ..services.webhook_auth import AuthError
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.access_control import resolve_token_info  # type: ignore
    from services.aiohttp_compat import import_aiohttp_web
    from services.approvals.models import ApprovalStatus
    from services.approvals.service import get_approval_service
    from services.audit import emit_audit_event  # type: ignore
    from services.management_query import (  # type: ignore
        bounded_scan_collect,
        normalize_limit_offset,
    )
    from services.tenant_context import (  # type: ignore
        TenantBoundaryError,
        request_tenant_scope,
    )
    from services.webhook_auth import AuthError

logger = logging.getLogger("ComfyUI-OpenClaw.api.approvals")
web = import_aiohttp_web()


class ApprovalHandlers:
    """
    CRUD handlers for /moltbot/approvals endpoints.
    All endpoints require admin token authentication.
    """

    def __init__(self, require_admin_token_fn=None, submit_fn=None):
        """
        Args:
            require_admin_token_fn: Function to validate admin token.
            submit_fn: Async function to submit a workflow (for execution on approval).
        """
        self._require_admin_token = require_admin_token_fn
        self._submit_fn = submit_fn
        self._service = get_approval_service()

    async def _check_auth(self, request: web.Request) -> None:
        """Require admin token for all approval operations."""
        if self._require_admin_token:
            import inspect

            result = self._require_admin_token(request)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, tuple):
                allowed, error = result
                if not allowed:
                    raise AuthError(error or "Unauthorized")

    def _audit(
        self,
        *,
        request: web.Request,
        action: str,
        target: str,
        outcome: str,
        status_code: int,
        details: Optional[dict] = None,
    ) -> None:
        try:
            token_info = resolve_token_info(request)
        except Exception:
            token_info = None
        emit_audit_event(
            action=action,
            target=target,
            outcome=outcome,
            token_info=token_info,
            status_code=status_code,
            details=details or {},
            request=request,
        )

    async def list_approvals(self, request: web.Request) -> web.Response:
        """GET /moltbot/approvals - List approval requests."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            self._audit(
                request=request,
                action="approvals.list",
                target="approvals",
                outcome="deny",
                status_code=403,
                details={"reason": str(e)},
            )
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            self._audit(
                request=request,
                action="approvals.list",
                target="approvals",
                outcome="deny",
                status_code=403,
                details={"reason": "unauthorized"},
            )
            return web.json_response({"error": "Unauthorized"}, status=403)

        # Parse query params
        status_filter = request.query.get("status")
        page = normalize_limit_offset(
            request.query,
            default_limit=100,
            max_limit=500,
            default_offset=0,
            max_offset=5000,
        )

        # Validate and convert status
        status = None
        if status_filter:
            try:
                status = ApprovalStatus(status_filter)
            except ValueError:
                return web.json_response(
                    {"error": f"Invalid status: {status_filter}"}, status=400
                )

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                # Get approvals
                # R95: bounded scan window protects API serialization path and keeps
                # malformed-record behavior deterministic without swallowing service errors.
                scan_cap = max(page.offset + page.limit + 200, page.limit * 10)
                approvals = self._service.list_all(
                    status=status,
                    limit=min(scan_cap, 5000),
                    offset=0,
                    tenant_id=tenant.tenant_id,
                )
                page_result = bounded_scan_collect(
                    approvals,
                    skip=page.offset,
                    take=page.limit,
                    scan_cap=min(scan_cap, 5000),
                    serializer=lambda a: a.to_dict(),
                )

                return web.json_response(
                    {
                        "tenant_id": tenant.tenant_id,
                        "approvals": page_result.items,
                        "count": len(page_result.items),
                        "pending_count": self._service.count_pending(
                            tenant_id=tenant.tenant_id
                        ),
                        "pagination": {
                            "limit": page.limit,
                            "offset": page.offset,
                            "warnings": page.warnings,
                        },
                        "scan": page_result.to_dict(),
                    }
                )
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )

    async def get_approval(self, request: web.Request) -> web.Response:
        """GET /moltbot/approvals/{approval_id} - Get a single approval."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            self._audit(
                request=request,
                action="approvals.get",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": str(e)},
            )
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            self._audit(
                request=request,
                action="approvals.get",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": "unauthorized"},
            )
            return web.json_response({"error": "Unauthorized"}, status=403)

        approval_id = request.match_info.get("approval_id", "")
        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                approval = self._service.get(approval_id, tenant_id=tenant.tenant_id)

                if not approval:
                    return web.json_response(
                        {"error": "Approval not found"}, status=404
                    )

                return web.json_response(
                    {"tenant_id": tenant.tenant_id, "approval": approval.to_dict()}
                )
        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )

    async def approve_request(self, request: web.Request) -> web.Response:
        """POST /moltbot/approvals/{approval_id}/approve - Approve and execute request."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            self._audit(
                request=request,
                action="approvals.approve",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": str(e)},
            )
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            self._audit(
                request=request,
                action="approvals.approve",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": "unauthorized"},
            )
            return web.json_response({"error": "Unauthorized"}, status=403)

        approval_id = request.match_info.get("approval_id", "")

        # Parse optional body
        actor = None
        auto_execute = True  # Default: execute immediately after approval
        try:
            data = await request.json()
            actor = data.get("actor")
            auto_execute = data.get("auto_execute", True)
        except Exception:
            pass  # No body is fine

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                # First approve the request
                approval = self._service.approve(
                    approval_id, actor=actor, tenant_id=tenant.tenant_id
                )
                logger.info(f"Approved request: {approval_id}")

                result = {
                    "tenant_id": tenant.tenant_id,
                    "approved": True,
                    "approval": approval.to_dict(),
                }

                # Execute if requested and submit_fn is available
                if auto_execute and self._submit_fn:
                    try:
                        from .triggers import execute_approved_trigger

                        exec_result = await execute_approved_trigger(
                            approval_id=approval_id,
                            submit_fn=self._submit_fn,
                        )

                        result["executed"] = True
                        result["prompt_id"] = exec_result.get("prompt_id")
                        result["trace_id"] = exec_result.get("trace_id")
                        if "callback" in exec_result:
                            result["callback"] = exec_result["callback"]

                        if result.get("prompt_id"):
                            # NOTE: Persist executed_prompt_id so connector can deliver results
                            # after UI approvals. Do not remove without updating connector.
                            try:
                                self._service.record_execution(
                                    approval_id,
                                    prompt_id=result.get("prompt_id"),
                                    trace_id=result.get("trace_id"),
                                    actor=actor,
                                    tenant_id=tenant.tenant_id,
                                )
                            except Exception as record_error:
                                logger.error(
                                    "Failed to record approval execution metadata: "
                                    f"{record_error}"
                                )

                        logger.info(
                            f"Executed approved trigger: {approval_id} -> {result.get('prompt_id')}"
                        )

                    except Exception as exec_error:
                        logger.error(
                            f"Failed to execute approved trigger: {exec_error}"
                        )
                        result["executed"] = False
                        result["execution_error"] = str(exec_error)
                else:
                    result["executed"] = False

                self._audit(
                    request=request,
                    action="approvals.approve",
                    target=approval_id,
                    outcome="allow",
                    status_code=200,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "executed": result.get("executed", False),
                        "actor": actor,
                    },
                )
                return web.json_response(result)

        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )
        except ValueError as e:
            self._audit(
                request=request,
                action="approvals.approve",
                target=approval_id,
                outcome="error",
                status_code=400,
                details={"error": str(e), "actor": actor},
            )
            return web.json_response({"error": str(e)}, status=400)

    async def reject_request(self, request: web.Request) -> web.Response:
        """POST /moltbot/approvals/{approval_id}/reject - Reject a request."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            self._audit(
                request=request,
                action="approvals.reject",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": str(e)},
            )
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            self._audit(
                request=request,
                action="approvals.reject",
                target=request.match_info.get("approval_id", ""),
                outcome="deny",
                status_code=403,
                details={"reason": "unauthorized"},
            )
            return web.json_response({"error": "Unauthorized"}, status=403)

        approval_id = request.match_info.get("approval_id", "")

        # Parse optional body
        actor = None
        try:
            data = await request.json()
            actor = data.get("actor")
        except Exception:
            pass

        token_info = resolve_token_info(request)
        try:
            with request_tenant_scope(
                request=request,
                token_info=token_info,
                allow_default_when_missing=True,
            ) as tenant:
                approval = self._service.reject(
                    approval_id, actor=actor, tenant_id=tenant.tenant_id
                )

                logger.info(f"Rejected request: {approval_id}")
                self._audit(
                    request=request,
                    action="approvals.reject",
                    target=approval_id,
                    outcome="allow",
                    status_code=200,
                    details={"tenant_id": tenant.tenant_id, "actor": actor},
                )
                return web.json_response(
                    {
                        "tenant_id": tenant.tenant_id,
                        "rejected": True,
                        "approval": approval.to_dict(),
                    }
                )

        except TenantBoundaryError as exc:
            return web.json_response(
                {"error": exc.code, "message": str(exc)},
                status=403,
            )
        except ValueError as e:
            self._audit(
                request=request,
                action="approvals.reject",
                target=approval_id,
                outcome="error",
                status_code=400,
                details={"error": str(e), "actor": actor},
            )
            return web.json_response({"error": str(e)}, status=400)


def register_approval_routes(
    app: web.Application,
    require_admin_token_fn=None,
    submit_fn=None,
) -> None:
    """Register approval API routes on the aiohttp app."""
    handlers = ApprovalHandlers(
        require_admin_token_fn=require_admin_token_fn,
        submit_fn=submit_fn,
    )

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        routes = [
            ("GET", f"{prefix}/approvals", handlers.list_approvals),
            ("GET", f"{prefix}/approvals/{{approval_id}}", handlers.get_approval),
            (
                "POST",
                f"{prefix}/approvals/{{approval_id}}/approve",
                handlers.approve_request,
            ),
            (
                "POST",
                f"{prefix}/approvals/{{approval_id}}/reject",
                handlers.reject_request,
            ),
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

    logger.info("Registered approval API routes (dual)")
