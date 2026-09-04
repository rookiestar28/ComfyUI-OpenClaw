"""
API routes for observability endpoints.
Registers /openclaw/* endpoints (and legacy /moltbot/*) against ComfyUI PromptServer.
"""

# IMPORTANT: __future__ imports MUST be the first non-docstring line in the file.
# Do not move this import or insert code above it, or ComfyUI route registration will fail.
from __future__ import annotations

import logging
import os
from typing import cast

if __package__ and "." in __package__:
    from ..services.import_fallback import import_attrs_dual
else:
    from services.import_fallback import import_attrs_dual  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw")

# IMPORTANT: route-family helpers must stay one-way imported from here.
# Do not make api.route_registrars import api.routes back, or bootstrap can
# regress into circular import failures before PromptServer registration.
(
    build_assist_route_specs,
    build_connector_installation_route_specs,
    build_core_route_specs,
    build_pack_route_specs,
    register_route_family,
) = import_attrs_dual(
    __package__,
    "..api.route_registrars",
    "api.route_registrars",
    (
        "build_assist_route_specs",
        "build_connector_installation_route_specs",
        "build_core_route_specs",
        "build_pack_route_specs",
        "register_route_family",
    ),
)

(
    RouteHandlerDependencies,
    emit_jobs_list_audit,
    health_response,
    jobs_response,
    logs_tail_response,
    owned_ensure_observability_deps_ready,
    trace_response,
) = import_attrs_dual(
    __package__,
    "..api.route_handlers",
    "api.route_handlers",
    (
        "RouteHandlerDependencies",
        "emit_jobs_list_audit",
        "health_response",
        "jobs_response",
        "logs_tail_response",
        "ensure_observability_deps_ready",
        "trace_response",
    ),
)

(
    RouteRegistrationDependencies,
    orchestrate_dual_route,
    register_route_families,
    run_mae_startup_gate,
) = import_attrs_dual(
    __package__,
    "..api.route_orchestration",
    "api.route_orchestration",
    (
        "RouteRegistrationDependencies",
        "register_dual_route",
        "register_route_families",
        "run_mae_startup_gate",
    ),
)

# R98 / R64: Endpoint Metadata import via shared helper
(
    AuthTier,
    RiskTier,
    RoutePlane,
    endpoint_metadata,
) = import_attrs_dual(
    __package__,
    "..services.endpoint_manifest",
    "services.endpoint_manifest",
    ("AuthTier", "RiskTier", "RoutePlane", "endpoint_metadata"),
)

(build_legacy_route_deprecation_headers,) = import_attrs_dual(
    __package__,
    "..services.legacy_compat",
    "services.legacy_compat",
    ("build_legacy_route_deprecation_headers",),
)

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

PACK_NAME = PACK_VERSION = PACK_START_TIME = LOG_FILE = get_api_key = None  # type: ignore
metrics = tail_log = require_observability_access = check_rate_limit = trace_store = None  # type: ignore
require_admin_token = resolve_token_info = emit_audit_event = None  # type: ignore
jobs_request_tenant_scope = normalize_jobs_query = build_jobs_audit_details = None  # type: ignore
SAFE_JOB_AUDIT_OUTCOMES = None  # type: ignore
JobsSecurityError = TenantBoundaryError = None  # type: ignore
JobsHostContractUnsupported = JobsBackendUnavailable = read_jobs = None  # type: ignore
get_executor_diagnostics = None  # type: ignore
webhook_handler = webhook_submit_handler = webhook_validate_handler = capabilities_handler = preflight_handler = None  # type: ignore
pnginfo_handler = None  # type: ignore  # R168
config_get_handler = config_put_handler = llm_test_handler = llm_models_handler = llm_chat_handler = None  # type: ignore
remote_admin_page_handler = None  # type: ignore  # F61
security_doctor_handler = None  # type: ignore  # S30
connector_installations_list_handler = connector_installation_get_handler = None  # type: ignore
connector_installation_resolve_handler = connector_installation_audit_handler = None  # type: ignore
connector_extraction_contract_handler = None  # type: ignore
templates_list_handler = None  # type: ignore
rewrite_recipes_list_handler = rewrite_recipe_get_handler = None  # type: ignore
rewrite_recipe_create_handler = rewrite_recipe_update_handler = None  # type: ignore
rewrite_recipe_delete_handler = rewrite_recipe_dry_run_handler = None  # type: ignore
rewrite_recipe_apply_handler = None  # type: ignore
model_search_handler = model_download_create_handler = None  # type: ignore
model_download_list_handler = model_download_get_handler = None  # type: ignore
model_download_cancel_handler = model_import_handler = None  # type: ignore
model_installations_list_handler = None  # type: ignore
secrets_status_handler = secrets_put_handler = secrets_delete_handler = None  # type: ignore
list_checkpoints_handler = create_checkpoint_handler = get_checkpoint_handler = delete_checkpoint_handler = None  # type: ignore
events_stream_handler = events_poll_handler = None  # type: ignore  # R71
redact_text = None  # type: ignore

if web is not None:
    # Import discipline:
    # - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
    # - Unit tests: allow top-level imports.
    (capabilities_handler,) = import_attrs_dual(
        __package__,
        "..api.capabilities",
        "api.capabilities",
        ("capabilities_handler",),
    )
    (
        connector_installations_list_handler,
        connector_installation_get_handler,
        connector_installation_resolve_handler,
        connector_installation_audit_handler,
        connector_extraction_contract_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.connector_contracts",
        "api.connector_contracts",
        (
            "connector_installations_list_handler",
            "connector_installation_get_handler",
            "connector_installation_resolve_handler",
            "connector_installation_audit_handler",
            "connector_extraction_contract_handler",
        ),
    )
    (
        create_checkpoint_handler,
        delete_checkpoint_handler,
        get_checkpoint_handler,
        list_checkpoints_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.checkpoints_handler",
        "api.checkpoints_handler",
        (
            "create_checkpoint_handler",
            "delete_checkpoint_handler",
            "get_checkpoint_handler",
            "list_checkpoints_handler",
        ),
    )
    (
        config_get_handler,
        config_put_handler,
        llm_chat_handler,
        llm_models_handler,
        llm_test_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.config",
        "api.config",
        (
            "config_get_handler",
            "config_put_handler",
            "llm_chat_handler",
            "llm_models_handler",
            "llm_test_handler",
        ),
    )
    (events_poll_handler, events_stream_handler) = import_attrs_dual(  # R71
        __package__,
        "..api.events",
        "api.events",
        ("events_poll_handler", "events_stream_handler"),
    )
    (remote_admin_page_handler,) = import_attrs_dual(  # F61
        __package__,
        "..api.remote_admin",
        "api.remote_admin",
        ("remote_admin_page_handler",),
    )
    (inventory_handler, preflight_handler) = import_attrs_dual(
        __package__,
        "..api.preflight_handler",
        "api.preflight_handler",
        ("inventory_handler", "preflight_handler"),
    )
    (pnginfo_handler,) = import_attrs_dual(
        __package__,
        "..api.pnginfo",
        "api.pnginfo",
        ("pnginfo_handler",),
    )
    (secrets_delete_handler, secrets_put_handler, secrets_status_handler) = (
        import_attrs_dual(
            __package__,
            "..api.secrets",
            "api.secrets",
            ("secrets_delete_handler", "secrets_put_handler", "secrets_status_handler"),
        )
    )
    (security_doctor_handler,) = import_attrs_dual(  # S30
        __package__,
        "..api.security_doctor",
        "api.security_doctor",
        ("security_doctor_handler",),
    )
    (templates_list_handler,) = import_attrs_dual(
        __package__,
        "..api.templates",
        "api.templates",
        ("templates_list_handler",),
    )
    (
        rewrite_recipe_apply_handler,
        rewrite_recipe_create_handler,
        rewrite_recipe_delete_handler,
        rewrite_recipe_dry_run_handler,
        rewrite_recipe_get_handler,
        rewrite_recipe_update_handler,
        rewrite_recipes_list_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.rewrite_recipes",
        "api.rewrite_recipes",
        (
            "rewrite_recipe_apply_handler",
            "rewrite_recipe_create_handler",
            "rewrite_recipe_delete_handler",
            "rewrite_recipe_dry_run_handler",
            "rewrite_recipe_get_handler",
            "rewrite_recipe_update_handler",
            "rewrite_recipes_list_handler",
        ),
    )
    (
        model_download_cancel_handler,
        model_download_create_handler,
        model_download_get_handler,
        model_download_list_handler,
        model_import_handler,
        model_installations_list_handler,
        model_search_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.model_manager",
        "api.model_manager",
        (
            "model_download_cancel_handler",
            "model_download_create_handler",
            "model_download_get_handler",
            "model_download_list_handler",
            "model_import_handler",
            "model_installations_list_handler",
            "model_search_handler",
        ),
    )
    (tools_list_handler, tools_run_handler) = import_attrs_dual(  # S12
        __package__,
        "..api.tools",
        "api.tools",
        ("tools_list_handler", "tools_run_handler"),
    )
    (webhook_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook",
        "api.webhook",
        ("webhook_handler",),
    )
    (webhook_submit_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook_submit",
        "api.webhook_submit",
        ("webhook_submit_handler",),
    )
    (webhook_validate_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook_validate",
        "api.webhook_validate",
        ("webhook_validate_handler",),
    )

    # IMPORTANT: use PACK_VERSION / PACK_START_TIME from config.
    # Do NOT import VERSION or config_path (they do not exist) or route registration will fail.
    (LOG_FILE, PACK_NAME, PACK_START_TIME, PACK_VERSION) = import_attrs_dual(
        __package__,
        "..config",
        "config",
        ("LOG_FILE", "PACK_NAME", "PACK_START_TIME", "PACK_VERSION"),
    )

    # CRITICAL: These imports MUST remain present.
    # If edited out, module-level placeholders stay as None and handlers raise at runtime
    # (e.g., TypeError: 'NoneType' object is not callable), producing noisy aiohttp tracebacks.
    (require_admin_token, require_observability_access, resolve_token_info) = (
        import_attrs_dual(
            __package__,
            "..services.access_control",
            "services.access_control",
            (
                "require_admin_token",
                "require_observability_access",
                "resolve_token_info",
            ),
        )
    )
    (emit_audit_event,) = import_attrs_dual(
        __package__,
        "..services.audit",
        "services.audit",
        ("emit_audit_event",),
    )
    (
        JobsSecurityError,
        SAFE_JOB_AUDIT_OUTCOMES,
        TenantBoundaryError,
        build_jobs_audit_details,
        jobs_request_tenant_scope,
        normalize_jobs_query,
    ) = import_attrs_dual(
        __package__,
        "..services.jobs_security",
        "services.jobs_security",
        (
            "JobsSecurityError",
            "SAFE_JOB_AUDIT_OUTCOMES",
            "TenantBoundaryError",
            "build_jobs_audit_details",
            "jobs_request_tenant_scope",
            "normalize_jobs_query",
        ),
    )
    (
        JobsBackendUnavailable,
        JobsHostContractUnsupported,
        read_jobs,
    ) = import_attrs_dual(
        __package__,
        "..services.jobs_read_model",
        "services.jobs_read_model",
        (
            "JobsBackendUnavailable",
            "JobsHostContractUnsupported",
            "read_jobs",
        ),
    )
    (tail_log,) = import_attrs_dual(
        __package__,
        "..services.log_tail",
        "services.log_tail",
        ("tail_log",),
    )
    (metrics,) = import_attrs_dual(
        __package__,
        "..services.metrics",
        "services.metrics",
        ("metrics",),
    )
    (get_executor_diagnostics,) = import_attrs_dual(
        __package__,
        "..services.async_utils",
        "services.async_utils",
        ("get_executor_diagnostics",),
    )
    (
        create_compare_handler,
        create_sweep_handler,
        get_experiment_handler,
        list_experiments_handler,
        select_apply_winner_handler,
        update_experiment_handler,
    ) = import_attrs_dual(
        __package__,
        "..services.parameter_lab",
        "services.parameter_lab",
        (
            "create_compare_handler",
            "create_sweep_handler",
            "get_experiment_handler",
            "list_experiments_handler",
            "select_apply_winner_handler",
            "update_experiment_handler",
        ),
    )
    (check_rate_limit, build_rate_limit_response) = import_attrs_dual(
        __package__,
        "..services.rate_limit",
        "services.rate_limit",
        ("check_rate_limit", "build_rate_limit_response"),
    )
    (redact_text,) = import_attrs_dual(
        __package__,
        "..services.redaction",
        "services.redaction",
        ("redact_text",),
    )

    # IMPORTANT: services.trace does NOT expose a `trace` symbol.
    # Do not import `trace` here or route registration will fail.
    (trace_store,) = import_attrs_dual(
        __package__,
        "..services.trace_store",
        "services.trace_store",
        ("trace_store",),
    )


def check_dependency(module_name: str) -> bool:
    """Check if a module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _handler_dependencies():
    """Capture facade patch seams for the owned route implementations."""

    return RouteHandlerDependencies(
        web=web,
        pack_name=PACK_NAME,
        pack_version=PACK_VERSION,
        pack_start_time=PACK_START_TIME,
        log_file=LOG_FILE,
        metrics=metrics,
        tail_log=tail_log,
        require_observability_access=require_observability_access,
        require_admin_token=require_admin_token,
        check_rate_limit=check_rate_limit,
        build_rate_limit_response=build_rate_limit_response,
        trace_store=trace_store,
        get_executor_diagnostics=get_executor_diagnostics,
        redact_text=redact_text,
        check_dependency=check_dependency,
        resolve_token_info=resolve_token_info,
        emit_audit_event=emit_audit_event,
        jobs_request_tenant_scope=jobs_request_tenant_scope,
        normalize_jobs_query=normalize_jobs_query,
        build_jobs_audit_details=build_jobs_audit_details,
        safe_job_audit_outcomes=SAFE_JOB_AUDIT_OUTCOMES,
        jobs_security_error=JobsSecurityError,
        tenant_boundary_error=TenantBoundaryError,
        jobs_host_contract_unsupported=JobsHostContractUnsupported,
        jobs_backend_unavailable=JobsBackendUnavailable,
        read_jobs=read_jobs,
        ensure_observability_deps_ready=_ensure_observability_deps_ready,
    )


def _ensure_observability_deps_ready() -> tuple[bool, str | None]:
    """Preserve the established initialization-check patch seam."""

    return cast(
        tuple[bool, str | None],
        owned_ensure_observability_deps_ready(_handler_dependencies()),
    )


@endpoint_metadata(
    auth=AuthTier.PUBLIC,
    risk=RiskTier.LOW,
    summary="Health check",
    description="Returns pack status, uptime, dependencies, and stats.",
    audit="health.check",
    plane=RoutePlane.USER,
)
async def health_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/health (legacy: /moltbot/health)
    Returns pack status, uptime, dependencies, config presence, and stats.
    """
    return await health_response(request, _handler_dependencies())


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Tail logs",
    description="Returns the last N lines of the log file.",
    audit="logs.tail",
    plane=RoutePlane.ADMIN,
)
async def logs_tail_handler(request: web.Request) -> web.Response:
    """GET /moltbot/logs/tail - Returns the last N lines of the log file."""
    # CRITICAL: logs_tail_response performs require_admin_token( before log access.
    return await logs_tail_response(request, _handler_dependencies())


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List jobs",
    description="Admin-authorized versioned bounded in-process jobs read model.",
    audit="jobs.list",
    plane=RoutePlane.ADMIN,
)
async def jobs_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/jobs (legacy: /moltbot/jobs).
    This handler preserves the authorization and tenant boundary around the read model.
    """
    # CRITICAL: jobs_response performs require_admin_token( before queue/history access.
    return await jobs_response(request, _handler_dependencies())


def _emit_jobs_list_audit(
    *,
    request,
    token_info,
    outcome: str,
    status_code: int,
    reason: str,
    **counts,
) -> None:
    """Preserve the established facade seam with content-free dependency capture."""

    emit_jobs_list_audit(
        _handler_dependencies(),
        request=request,
        token_info=token_info,
        outcome=outcome,
        status_code=status_code,
        reason=reason,
        **counts,
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Get trace",
    description="Returns redacted timeline for a prompt.",
    audit="trace.get",
    plane=RoutePlane.ADMIN,
)
async def trace_handler(request: web.Request) -> web.Response:
    """GET /moltbot/trace/{prompt_id} - Returns trace_id and redacted timeline."""
    # CRITICAL: trace_response performs require_admin_token( before trace access.
    return await trace_response(request, _handler_dependencies())


assist = None
if web is not None:
    # Initialize Assist Handlers
    try:
        from ..api.assist import AssistHandlers
    except ImportError:
        from api.assist import AssistHandlers
    assist = AssistHandlers()


def register_dual_route(server, method: str, path: str, handler) -> None:
    """
    Registers a route to both the standard PromptServer table
    and directly to the aiohttp router with and without /api prefix
    to ensure robustness against loading order (R26/F24).
    """
    orchestrate_dual_route(
        server,
        method,
        path,
        handler,
        metrics=metrics,
        legacy_headers_builder=build_legacy_route_deprecation_headers,
    )


def _resolve_mae_profile() -> str:
    try:
        if __package__ and "." in __package__:
            from ..services.effective_security_posture import (
                get_effective_security_posture,
            )
        else:
            from services.effective_security_posture import (
                get_effective_security_posture,
            )
        posture = get_effective_security_posture(required=False)
        if posture is not None:
            return str(posture.mae_profile)
    except ImportError:
        # IMPORTANT: dependency-light import mode retains the accepted resolver below.
        pass

    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").strip().lower()
    if profile in {"public", "hardened"}:
        return profile

    try:
        if __package__ and "." in __package__:
            from ..services.runtime_profile import get_runtime_profile
        else:
            from services.runtime_profile import get_runtime_profile
        runtime_profile = get_runtime_profile().value
        if runtime_profile == "hardened":
            return "hardened"
    except ImportError:
        # IMPORTANT: optional standalone import absence may fall back to the deployment
        # profile, but unexpected resolver failures must propagate instead of downgrading
        # hardened posture silently.
        pass
    return profile or "local"


def _run_mae_startup_gate(server) -> None:
    run_mae_startup_gate(server, _resolve_mae_profile)


def register_routes(server) -> None:
    """
    Register API routes with the ComfyUI server.
    Called from __init__.py during pack initialization.
    """
    # S56: Startup deployment profile gate (fail-closed pre-route validation).
    # Must run BEFORE any route or worker registration.
    try:
        try:
            from ..services.effective_security_posture import (
                get_effective_security_posture,
                resolve_effective_security_posture,
            )
            from ..services.startup_profile_gate import enforce_startup_gate
        except (ImportError, ValueError):
            from services.effective_security_posture import (
                get_effective_security_posture,
                resolve_effective_security_posture,
            )
            from services.startup_profile_gate import enforce_startup_gate

        posture = get_effective_security_posture(required=False)
        if posture is None:
            # Compatibility/direct-test invocation is not the process owner.
            posture = resolve_effective_security_posture()
        enforce_startup_gate(posture=posture)
    except RuntimeError:
        # CRITICAL: fail-closed. Never continue route registration after S56
        # startup gate failure.
        raise

    logger.info("startup.route_registration_begin")
    core_handlers = {
        "remote_admin_page_handler": remote_admin_page_handler,
        "health_handler": health_handler,
        "logs_tail_handler": logs_tail_handler,
        "jobs_handler": jobs_handler,
        "trace_handler": trace_handler,
        "webhook_handler": webhook_handler,
        "webhook_submit_handler": webhook_submit_handler,
        "webhook_validate_handler": webhook_validate_handler,
        "capabilities_handler": capabilities_handler,
        "config_get_handler": config_get_handler,
        "config_put_handler": config_put_handler,
        "llm_test_handler": llm_test_handler,
        "llm_chat_handler": llm_chat_handler,
        "llm_models_handler": llm_models_handler,
        "templates_list_handler": templates_list_handler,
        "preflight_handler": preflight_handler,
        "inventory_handler": inventory_handler,
        "pnginfo_handler": pnginfo_handler,
        "list_checkpoints_handler": list_checkpoints_handler,
        "create_checkpoint_handler": create_checkpoint_handler,
        "get_checkpoint_handler": get_checkpoint_handler,
        "delete_checkpoint_handler": delete_checkpoint_handler,
        "rewrite_recipes_list_handler": rewrite_recipes_list_handler,
        "rewrite_recipe_create_handler": rewrite_recipe_create_handler,
        "rewrite_recipe_get_handler": rewrite_recipe_get_handler,
        "rewrite_recipe_update_handler": rewrite_recipe_update_handler,
        "rewrite_recipe_delete_handler": rewrite_recipe_delete_handler,
        "rewrite_recipe_dry_run_handler": rewrite_recipe_dry_run_handler,
        "rewrite_recipe_apply_handler": rewrite_recipe_apply_handler,
        "model_search_handler": model_search_handler,
        "model_download_create_handler": model_download_create_handler,
        "model_download_list_handler": model_download_list_handler,
        "model_download_get_handler": model_download_get_handler,
        "model_download_cancel_handler": model_download_cancel_handler,
        "model_import_handler": model_import_handler,
        "model_installations_list_handler": model_installations_list_handler,
        "secrets_status_handler": secrets_status_handler,
        "secrets_put_handler": secrets_put_handler,
        "events_stream_handler": events_stream_handler,
        "events_poll_handler": events_poll_handler,
        "secrets_delete_handler": secrets_delete_handler,
        "security_doctor_handler": security_doctor_handler,
        "tools_list_handler": tools_list_handler,
        "tools_run_handler": tools_run_handler,
        "create_sweep_handler": create_sweep_handler,
        "create_compare_handler": create_compare_handler,
        "list_experiments_handler": list_experiments_handler,
        "get_experiment_handler": get_experiment_handler,
        "update_experiment_handler": update_experiment_handler,
        "select_apply_winner_handler": select_apply_winner_handler,
    }

    connector_handlers = None
    if connector_installations_list_handler:
        connector_handlers = {
            "connector_installations_list_handler": connector_installations_list_handler,
            "connector_extraction_contract_handler": connector_extraction_contract_handler,
            "connector_installation_resolve_handler": connector_installation_resolve_handler,
            "connector_installation_audit_handler": connector_installation_audit_handler,
            "connector_installation_get_handler": connector_installation_get_handler,
        }
    register_route_families(
        server,
        RouteRegistrationDependencies(
            build_core_route_specs=build_core_route_specs,
            build_assist_route_specs=build_assist_route_specs,
            build_connector_installation_route_specs=build_connector_installation_route_specs,
            build_pack_route_specs=build_pack_route_specs,
            register_route_family=register_route_family,
            register_dual_route=register_dual_route,
            core_handlers=core_handlers,
            assist=assist,
            connector_installation_handlers=connector_handlers,
            run_mae_startup_gate=_run_mae_startup_gate,
        ),
    )
