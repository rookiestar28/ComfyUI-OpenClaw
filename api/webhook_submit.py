"""
Webhook Submit Handler (F5).
Connects S2 (Auth) -> R8 (Normalization) -> R3 (Idempotency) -> F5 (Execution).
"""

import json
import logging

# Import discipline:
# - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
# - Unit tests: allow top-level fallbacks.
#
# IMPORTANT (recurring production bug):
# Do NOT wrap these imports in a broad `try/except ImportError`. In ComfyUI, that can silently
# import another pack's top-level `services` module and break allowlists/auth in surprising ways.
if __package__ and "." in __package__:
    from ..models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from ..services.aiohttp_compat import import_aiohttp_web, read_bounded_body
    from ..services.callback_delivery import start_callback_watch
    from ..services.execution_budgets import BudgetExceededError
    from ..services.idempotency_store import IdempotencyStore
    from ..services.job_events import JobEventType, get_job_event_store
    from ..services.metrics import metrics
    from ..services.queue_submit import submit_prompt
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.templates import get_template_service
    from ..services.trace import get_effective_trace_id
    from ..services.trace_store import trace_store
    from ..services.webhook_auth import require_auth
    from ..services.webhook_mapping import (  # F40/S59
        apply_mapping,
        resolve_profile,
        validate_canonical_schema,
    )
else:  # pragma: no cover (test-only import mode)
    from models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from services.aiohttp_compat import (  # type: ignore
        import_aiohttp_web,
        read_bounded_body,
    )
    from services.callback_delivery import start_callback_watch  # type: ignore
    from services.execution_budgets import BudgetExceededError  # type: ignore
    from services.idempotency_store import IdempotencyStore  # type: ignore
    from services.job_events import JobEventType, get_job_event_store  # type: ignore
    from services.metrics import metrics  # type: ignore
    from services.queue_submit import submit_prompt  # type: ignore
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.templates import get_template_service  # type: ignore
    from services.trace import get_effective_trace_id  # type: ignore
    from services.trace_store import trace_store  # type: ignore
    from services.webhook_auth import require_auth  # type: ignore
    from services.webhook_mapping import (  # F40  # type: ignore
        apply_mapping,
        resolve_profile,
        validate_canonical_schema,
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

logger = logging.getLogger("ComfyUI-OpenClaw.api.webhook_submit")
web = import_aiohttp_web()


def safe_error_response(status: int, error: str, detail: str = "") -> web.Response:
    body = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return web.json_response(body, status=status)


@endpoint_metadata(
    auth=AuthTier.WEBHOOK,
    risk=RiskTier.HIGH,
    summary="Webhook submit",
    description="Authenticated endpoint for external job requests (modern pipeline).",
    audit="webhook.submit",
    plane=RoutePlane.EXTERNAL,
)
async def webhook_submit_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/webhook/submit

    1. Rate Limit (S17)
    2. Auth (S2)
    3. Normalize (R8)
    4. Idempotency Check (R3)
    5. Render Template (F5)
    6. Submit to Queue (F5)
    """
    # S62: Block webhook execution in public+split mode
    try:
        # CRITICAL: package-relative import must stay first in ComfyUI runtime.
        from ..services.surface_guard import check_surface
    except ImportError:
        from services.surface_guard import check_surface  # type: ignore
    blocked = check_surface("webhook_execute", request)
    if blocked:
        return blocked

    # S17: Rate Limit
    if not check_rate_limit(request, "webhook"):
        metrics.inc("webhook_denied")
        return build_rate_limit_response(
            request,
            "webhook",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    try:
        # --- 1. S2: Auth & Basic Validation ---
        content_type = request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            metrics.inc("webhook_denied")
            return safe_error_response(415, "unsupported_media_type")

        try:
            raw_body = await read_bounded_body(request.content, MAX_BODY_SIZE)
            if len(raw_body) > MAX_BODY_SIZE:
                metrics.inc("webhook_denied")
                return safe_error_response(413, "payload_too_large")
        except Exception:
            metrics.inc("errors")
            return safe_error_response(400, "read_error")

        valid, error = require_auth(request, raw_body)
        if not valid:
            metrics.inc("webhook_denied")
            # Map specific auth errors
            if error in (
                "auth_not_configured",
                "bearer_not_configured",
                "hmac_not_configured",
            ):
                return safe_error_response(403, error)
            return safe_error_response(401, error)

        # --- 2. R8: Normalization ---
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception:
            metrics.inc("webhook_denied")
            return safe_error_response(400, "invalid_json")

        # Unwrap common envelopes (R8)
        if "payload" in data and isinstance(data["payload"], dict):
            data = data["payload"]
        elif "data" in data and isinstance(data["data"], dict):
            data = data["data"]

        # Common alias normalization (camelCase -> snake_case)
        if "templateId" in data:
            data["template_id"] = data.pop("templateId")
        if "profileId" in data:
            data["profile_id"] = data.pop("profileId")
        if "jobId" in data:
            data["job_id"] = data.pop("jobId")
        if "traceId" in data:
            data["trace_id"] = data.pop("traceId")

        # R25: Trace context
        trace_id = get_effective_trace_id(request.headers, data)
        data["trace_id"] = trace_id

        # F40: Payload Mapping Engine
        # 1. Resolve profile
        mapping_profile = resolve_profile(request.headers)

        # 2. Apply mapping if profile found
        if mapping_profile:
            try:
                # Log usage of mapping profile for observability
                logger.info(
                    f"Applying mapping profile '{mapping_profile.id}' to request (trace: {trace_id})"
                )
                mapped_data, mapping_warnings = apply_mapping(mapping_profile, data)

                # If trace_id was not in source but resolved from headers, re-inject it
                if "trace_id" not in mapped_data:
                    mapped_data["trace_id"] = trace_id

                data = mapped_data
                for w in mapping_warnings:
                    logger.warning(f"Mapping warning (trace: {trace_id}): {w}")
            except ValueError as e:
                metrics.inc("webhook_denied")
                return safe_error_response(400, "mapping_error", str(e))

        # Validate against schema
        # S59: enforce canonical post-map schema gate before typed parsing.
        canonical_ok, canonical_errors = validate_canonical_schema(data)
        if not canonical_ok:
            metrics.inc("webhook_denied")
            return safe_error_response(
                400,
                "validation_error",
                "; ".join(canonical_errors),
            )

        try:
            job_request = WebhookJobRequest.from_dict(data)
            normalized = job_request.to_normalized()
        except ValueError as e:
            metrics.inc("webhook_denied")
            return safe_error_response(400, "validation_error", str(e))

        # --- 3. R3: Idempotency ---
        job_id = normalized.get("job_id")
        store = IdempotencyStore()
        normalized_for_key = dict(normalized)
        normalized_for_key.pop("trace_id", None)
        key = store.generate_key(job_id, normalized_for_key)

        is_duplicate, existing_prompt_id = store.check_and_record(key)

        if is_duplicate:
            logger.info(f"Duplicate request suppressed. Key: {key}")
            metrics.inc("webhook_requests_deduped")
            return web.json_response(
                {
                    "ok": True,
                    "deduped": True,
                    "prompt_id": existing_prompt_id,
                    "trace_id": trace_id,
                    "message": "Request already processed",
                }
            )

        # --- 4. F5: Execution (Render & Submit) ---
        template_id = normalized["template_id"]
        template_service = get_template_service()

        try:
            # Render workflow
            workflow = template_service.render_template(
                template_id, normalized["inputs"]
            )
        except ValueError as e:
            # Template not found or invalid inputs
            metrics.inc("webhook_denied")
            return safe_error_response(400, "template_error", str(e))

        # Submit to queue
        try:
            result = await submit_prompt(
                workflow,
                client_id="moltbot-webhook",
                extra_data={"moltbot": {"trace_id": trace_id, "job_id": job_id}},
                source="webhook",
                trace_id=trace_id,
            )
            prompt_id = result.get("prompt_id")

            # Update store with prompt_id for future dedupes
            if prompt_id:
                store.update_prompt_id(key, prompt_id)

            # R25: Record trace mapping + queued event
            if prompt_id:
                trace_store.add_event(
                    prompt_id, trace_id, "queued", {"source": "webhook"}
                )

            # F16: Schedule callback delivery if configured
            callback_config = job_request.callback
            if callback_config and prompt_id:
                await start_callback_watch(
                    prompt_id, callback_config, trace_id=trace_id
                )

            # R71: Emit QUEUED event
            if prompt_id:
                try:
                    get_job_event_store().emit(
                        JobEventType.QUEUED,
                        prompt_id=prompt_id,
                        trace_id=trace_id,
                        data={
                            "source": "webhook",
                            "template_id": template_id,
                            "job_id": job_id,
                        },
                    )
                except Exception:
                    pass

            metrics.inc("webhook_requests_executed")
            return web.json_response(
                {
                    "ok": True,
                    "deduped": False,
                    "prompt_id": prompt_id,
                    "trace_id": trace_id,
                    "mapped": bool(mapping_profile),  # F40
                    "number": result.get("number"),
                    "callback_scheduled": bool(callback_config),
                }
            )

        except BudgetExceededError as e:
            # R33: Execution budgets (concurrency caps / render size budgets)
            logger.warning(f"Execution budget exceeded: {e}")
            metrics.inc("webhook_denied")

            status = 429
            if getattr(e, "budget_type", "") in (
                "rendered_workflow_size",
                "workflow_serialization",
            ):
                status = 413

            return web.json_response(
                {"ok": False, "error": "budget_exceeded", "detail": str(e)},
                status=status,
                headers={"Retry-After": str(getattr(e, "retry_after", 1))},
            )
        except Exception as e:
            logger.error(f"Queue submission failed: {e}")
            metrics.inc("errors")
            return safe_error_response(500, "execution_failed")

    except Exception as e:
        logger.exception(f"Unexpected error in webhook submission: {e}")
        metrics.inc("errors")
        return safe_error_response(500, "internal_error")
