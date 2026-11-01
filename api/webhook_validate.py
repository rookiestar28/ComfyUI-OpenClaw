"""
Webhook Validation Handler (R32).

Dry-run validation for webhook requests without queue submission.

Validates:
- S2: Auth (bearer/hmac, optional replay protection)
- R8: Normalization (common wrappers + camelCase aliases)
- Schema: WebhookJobRequest (strict)
- F5/R8: Template allowlist + rendering
- R33: Render size budget (via services.execution_budgets.check_render_size)
- Warnings: unresolved placeholders in rendered workflow

Routes are registered in `api/routes.py` for both:
- /openclaw/webhook/validate and /api/openclaw/webhook/validate
- legacy /moltbot/webhook/validate and /api/moltbot/webhook/validate
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    web = None  # type: ignore

# Import discipline:
# - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
# - Unit tests: allow top-level fallbacks.
#
# IMPORTANT (recurring production bug):
# Do NOT wrap these imports in a broad `try/except ImportError`. In ComfyUI, that can silently
# import another pack's top-level `services` module and break allowlists/auth in surprising ways.
if __package__ and "." in __package__:
    from ..models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from ..services.aiohttp_compat import read_bounded_body
    from ..services.execution_budgets import BudgetExceededError, check_render_size
    from ..services.metrics import metrics
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.templates import get_template_service
    from ..services.trace import get_effective_trace_id
    from ..services.webhook_auth import require_auth
    from ..services.webhook_mapping import apply_mapping, resolve_profile  # F40
else:  # pragma: no cover (test-only import mode)
    from models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from services.aiohttp_compat import read_bounded_body
    from services.execution_budgets import (  # type: ignore
        BudgetExceededError,
        check_render_size,
    )
    from services.metrics import metrics  # type: ignore
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.templates import get_template_service  # type: ignore
    from services.trace import get_effective_trace_id  # type: ignore
    from services.webhook_auth import require_auth  # type: ignore
    from services.webhook_mapping import (  # F40  # type: ignore
        apply_mapping,
        resolve_profile,
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

logger = logging.getLogger("ComfyUI-OpenClaw.api.webhook_validate")

PLACEHOLDER_PATTERN = re.compile(r"\{\{[^{}]+\}\}")


def _safe_error_response(status: int, error: str, detail: str = "") -> web.Response:
    body: Dict[str, Any] = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return web.json_response(body, status=status)


@endpoint_metadata(
    auth=AuthTier.WEBHOOK,
    risk=RiskTier.LOW,
    summary="Webhook validate",
    description="Dry-run validation for webhook requests.",
    audit="webhook.validate",
    plane=RoutePlane.EXTERNAL,
)
async def webhook_validate_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/webhook/validate (legacy: /moltbot/webhook/validate)
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # S17: Rate limit (same bucket as submit)
    if not check_rate_limit(request, "webhook"):
        metrics.inc("webhook_denied")
        return build_rate_limit_response(
            request,
            "webhook",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    # S2: Content-Type + body size
    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith("application/json"):
        metrics.inc("webhook_denied")
        return _safe_error_response(415, "unsupported_media_type")

    try:
        raw_body = await read_bounded_body(request.content, MAX_BODY_SIZE)
        if len(raw_body) > MAX_BODY_SIZE:
            metrics.inc("webhook_denied")
            return _safe_error_response(
                413, "payload_too_large", f"Max body size: {MAX_BODY_SIZE} bytes"
            )
    except Exception:
        metrics.inc("errors")
        return _safe_error_response(400, "read_error")

    valid, error = require_auth(request, raw_body)
    if not valid:
        metrics.inc("webhook_denied")
        if error in (
            "auth_not_configured",
            "bearer_not_configured",
            "hmac_not_configured",
        ):
            return _safe_error_response(403, error)
        return _safe_error_response(401, error)

    # R8: Parse JSON + unwrap common envelopes
    try:
        data: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        metrics.inc("webhook_denied")
        return _safe_error_response(400, "invalid_json", str(e))

    if "payload" in data and isinstance(data["payload"], dict):
        data = data["payload"]
    elif "data" in data and isinstance(data["data"], dict):
        data = data["data"]

    # R8: Common alias normalization (camelCase -> snake_case)
    if "templateId" in data:
        data["template_id"] = data.pop("templateId")
    if "profileId" in data:
        data["profile_id"] = data.pop("profileId")
    if "jobId" in data:
        data["job_id"] = data.pop("jobId")
    if "traceId" in data:
        data["trace_id"] = data.pop("traceId")

    # R25: Trace context (derive after normalization)
    trace_id = get_effective_trace_id(request.headers, data)
    data["trace_id"] = trace_id

    # F40: Payload Mapping Engine
    # 1. Resolve profile
    mapping_profile = resolve_profile(request.headers)
    mapping_warnings = []

    # 2. Apply mapping if profile found
    if mapping_profile:
        try:
            data, mapping_warnings = apply_mapping(mapping_profile, data)
        except ValueError as e:
            metrics.inc("webhook_denied")
            return _safe_error_response(400, "mapping_error", str(e))

    # Schema validation
    try:
        job_request = WebhookJobRequest.from_dict(data)
        normalized = job_request.to_normalized()
    except ValueError as e:
        metrics.inc("webhook_denied")
        # Log validation error for debugging test failures
        logger.warning(f"Webhook validation failed: {e}")
        return _safe_error_response(400, "validation_error", str(e))

    template_id = normalized["template_id"]
    inputs = normalized.get("inputs", {}) or {}

    # Render template (dry-run)
    template_service = get_template_service()
    try:
        workflow = template_service.render_template(template_id, inputs)
    except ValueError as e:
        metrics.inc("webhook_denied")
        return _safe_error_response(400, "template_error", str(e))
    except Exception as e:
        metrics.inc("errors")
        logger.exception("Unexpected template rendering failure")
        return _safe_error_response(500, "template_error", str(e))

    if not isinstance(workflow, dict):
        metrics.inc("webhook_denied")
        return _safe_error_response(
            400,
            "template_error",
            f"Rendered workflow must be an object, got {type(workflow).__name__}",
        )

    # R33: Render size budget
    try:
        check_render_size(workflow, trace_id=trace_id)
    except BudgetExceededError as e:
        metrics.inc("webhook_denied")
        return web.json_response(
            {"ok": False, "error": "payload_too_large", "detail": str(e)},
            status=413,
            headers={"Retry-After": str(e.retry_after)},
        )

    # Warnings: unresolved placeholders + mapping warnings
    warnings: List[str] = mapping_warnings
    unresolved: List[str] = []
    try:
        workflow_json = json.dumps(workflow, ensure_ascii=False, separators=(",", ":"))
        workflow_bytes = len(workflow_json.encode("utf-8"))
        unresolved = sorted(set(PLACEHOLDER_PATTERN.findall(workflow_json)))[:10]
        if unresolved:
            warnings.append(
                f"Unresolved placeholders detected ({len(unresolved)} shown): {', '.join(unresolved)}"
            )
    except Exception:
        workflow_bytes = 0

    # Redact normalized response (security: may contain secrets)
    try:
        # Import discipline: attempt package-relative first
        if __package__ and "." in __package__:
            from ..services.redaction import redact_json
        else:
            from services.redaction import redact_json

        safe_normalized = redact_json(normalized)
    except ImportError:
        # Fallback: use original if redaction not available
        safe_normalized = normalized

    metrics.inc("webhook_requests_validated")

    return web.json_response(
        {
            "ok": True,
            "mapped": bool(mapping_profile),  # F40: Indicate if mapping occurred
            "trace_id": trace_id,
            "template_id": template_id,
            "normalized": safe_normalized,  # Redacted for security
            "render": {
                "workflow_bytes": workflow_bytes,
                "node_count_estimate": len(workflow),
                "unresolved_placeholders": unresolved,
            },
            "warnings": warnings,
        }
    )
