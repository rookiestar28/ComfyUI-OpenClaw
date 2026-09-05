"""
Route/bootstrap orchestration implementation owner.

Keeps __init__.py thin while preserving startup behavior and fallback handling.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable

from ..env_aliases import get_env_value

_routes_registered = False
_registration_condition = threading.Condition(threading.RLock())
_registration_inflight = False
_registration_started = False
_registration_error: Exception | None = None
_registration_retry_thread: threading.Thread | None = None
_registration_generation = 0
_REGISTRATION_MAX_ATTEMPTS = 10
_REGISTRATION_INITIAL_DELAY_SEC = 2.0


def _resolve_optional_warmup_timeout_sec() -> float:
    raw = get_env_value("OPENCLAW_STARTUP_WARMUP_TIMEOUT_SEC", default="5") or "5"
    try:
        return max(0.1, min(float(raw), 60.0))
    except (TypeError, ValueError):
        return 5.0


def _warm_model_inventory_snapshot() -> None:
    from ..preflight import get_model_inventory_snapshot

    get_model_inventory_snapshot(trigger_refresh=True)


def _build_optional_startup_warmups():
    timeout_sec = _resolve_optional_warmup_timeout_sec()
    return [
        ("model_inventory", _warm_model_inventory_snapshot, timeout_sec),
    ]


def _mark_startup_ready_and_start_warmups() -> None:
    from .lifecycle import mark_startup_ready, start_optional_warmups

    # Required readiness is part of successful route registration and must not be
    # hidden behind the optional warmup boundary.
    mark_startup_ready("routes")
    try:
        start_optional_warmups(_build_optional_startup_warmups())
    except Exception as exc:
        # IMPORTANT: optional warmup diagnostics must not undo successful route startup.
        logging.getLogger("ComfyUI-OpenClaw").error(
            "Optional startup warmups could not be started (error_type=%s)",
            type(exc).__name__,
        )


def _mark_startup_fatal(
    phase: str,
    exc: BaseException,
    *,
    reason_code=None,
) -> None:
    try:
        from .lifecycle import mark_startup_fatal

        mark_startup_fatal(phase, exc, reason_code=reason_code)
    except Exception as diagnostics_exc:
        # IMPORTANT: preserve the original bootstrap exception even if diagnostics fail.
        logging.getLogger("ComfyUI-OpenClaw").error(
            "Startup diagnostics update failed (error_type=%s)",
            type(diagnostics_exc).__name__,
        )


def _mark_required_initialization_started() -> None:
    from .lifecycle import mark_required_initialization_started

    mark_required_initialization_started()


def _mark_host_waiting(*, attempt: int, max_attempts: int) -> None:
    from .lifecycle import mark_host_waiting

    mark_host_waiting(attempt=attempt, max_attempts=max_attempts)


def _mark_route_registration_started(
    *, attempt: int = 0, max_attempts: int = 0
) -> None:
    from .lifecycle import mark_route_registration_started

    mark_route_registration_started(
        attempt=attempt,
        max_attempts=max_attempts,
    )


def _load_plugin_shutdown_registrars():
    """Load optional startup registrars behind one patchable compatibility seam."""

    from ..plugins.builtin import register_all
    from ..runtime_lifecycle import register_shutdown_hooks

    return register_shutdown_hooks, register_all


def _register_plugins_and_shutdown_hooks() -> None:
    # R67: Best-effort process shutdown hook and built-in plugin registration.
    try:
        register_shutdown_hooks, register_all = _load_plugin_shutdown_registrars()
    except ImportError as exc:
        logging.getLogger("ComfyUI-OpenClaw").error(
            "Optional startup registrars unavailable (error_type=%s)",
            type(exc).__name__,
        )
        return

    logger = logging.getLogger("ComfyUI-OpenClaw")
    for component, registrar in (
        ("shutdown_hooks", register_shutdown_hooks),
        ("builtin_plugins", register_all),
    ):
        try:
            registrar()
        except Exception as exc:
            # IMPORTANT: these optional steps are independent. Keep startup available,
            # do not echo exception content, and do not catch BaseException cancellation.
            logger.error(
                "Optional startup registrar failed (component=%s, error_type=%s)",
                component,
                type(exc).__name__,
            )


def _initialize_registries_and_security_gate() -> None:
    # R63/R84: Initialize Service & Module Registries.
    try:
        from ..modules import ModuleCapability, ModuleRegistry, enable_module
        from ..registry import SVC_RUNTIME_CONFIG, ServiceRegistry
        from ..runtime_config import get_config

        config = get_config()
        ServiceRegistry.register(SVC_RUNTIME_CONFIG, config)

        from ..posture.effective import (
            get_effective_security_posture,
            resolve_effective_security_posture,
        )

        posture = get_effective_security_posture(required=False)
        if posture is None:
            # Direct compatibility/test invocation does not own process installation.
            posture = resolve_effective_security_posture()

        # Always-on modules
        enable_module(ModuleCapability.CORE)
        enable_module(ModuleCapability.SECURITY)
        enable_module(ModuleCapability.OBSERVABILITY)

        # S50: initialize durable idempotency storage early.
        from ..idempotency_store import IdempotencyStore
        from ..state_dir import get_state_dir

        db_path = os.path.join(get_state_dir(), "idempotency.db")
        # CRITICAL: pass db_path as keyword (first positional arg is backend object).
        IdempotencyStore().configure_durable(db_path=db_path, strict_mode=True)
        logging.getLogger("ComfyUI-OpenClaw").info(
            "IdempotencyStore durable backend configured (strict_mode=True)"
        )

        if config.bridge_enabled:
            enable_module(ModuleCapability.BRIDGE)

        # Core runtime modules stay enabled; runners decide active behavior.
        enable_module(ModuleCapability.SCHEDULER)
        enable_module(ModuleCapability.WEBHOOK)
        enable_module(ModuleCapability.CONNECTOR)

        ModuleRegistry.lock()
        logging.getLogger("ComfyUI-OpenClaw").info(
            "Initialized modules: %s", ModuleRegistry.get_enabled_list()
        )

        from ..security_gate import enforce_startup_gate

        enforce_startup_gate(posture=posture)
    except Exception as exc:
        logging.getLogger("ComfyUI-OpenClaw").error(
            "Required registry initialization failed (error_type=%s)",
            type(exc).__name__,
        )
        # CRITICAL: keep bootstrap fail-closed; swallowing startup gate errors
        # silently degrades security posture and can expose partial registration.
        raise


def _do_full_registration(server) -> None:
    """Register all OpenClaw routes including bridge/scheduler bindings."""
    from ..access_control import require_admin_token
    from ..parameter_lab_queue_receipt import (
        register_parameter_lab_queue_receipt_handler,
    )
    from ..plugins.async_bridge import run_async_in_sync_context
    from ..queue_submit import submit_prompt
    from ..route_bootstrap_contract import load_route_bootstrap_contract
    from ..scheduler.runner import get_scheduler_runner, start_scheduler
    from ..templates import get_template_service

    # IMPORTANT: the contract owns its stable relative-import anchor; forwarding this
    # nested package resolves `..api` to the nonexistent `services.api` namespace.
    contract = load_route_bootstrap_contract()
    register_approval_routes = contract["register_approval_routes"]
    BridgeHandlers = contract["BridgeHandlers"]
    register_preset_routes = contract["register_preset_routes"]
    register_routes = contract["register_routes"]
    register_schedule_routes = contract["register_schedule_routes"]
    register_trigger_routes = contract["register_trigger_routes"]

    register_routes(server)
    # CRITICAL: receipt promotion is required for exact Parameter Lab run ownership.
    register_parameter_lab_queue_receipt_handler(server)
    register_preset_routes(server.app)
    register_schedule_routes(server.app, require_admin_token_fn=require_admin_token)

    class QueueSubmitService:
        def submit(self, job_req):
            tmpl_svc = get_template_service()
            workflow = tmpl_svc.render_template(job_req.template_id, job_req.inputs)

            async def _do_submit():
                return await submit_prompt(
                    workflow,
                    client_id=job_req.session_id or "bridge",
                    extra_data={
                        "openclaw": {"trace_id": job_req.trace_id},
                        # Legacy key kept for existing tooling that expects this blob.
                        "moltbot": {"trace_id": job_req.trace_id},
                    },
                    source="bridge",
                    trace_id=job_req.trace_id,
                )

            return run_async_in_sync_context(_do_submit())

    bridge_handlers = BridgeHandlers(submit_service=QueueSubmitService())
    _register_bridge_routes(server.app.router, bridge_handlers)

    async def unified_submit_fn(
        template_id,
        inputs,
        trace_id,
        idempotency_key,
        delivery=None,
        source="unknown",
    ):
        """Submit function for scheduler and trigger-triggered runs."""
        # NOTE: Use IdempotencyStore API (check_and_record/update_prompt_id).
        # Avoid legacy get_store/get/set usage; wrong API here breaks route registration at runtime.
        from ..idempotency_store import IdempotencyStore
        from ..queue_submit import submit_prompt as _submit_prompt
        from ..templates import get_template_service as _get_template_service

        store = IdempotencyStore()
        is_dup, existing_prompt_id = store.check_and_record(idempotency_key)
        if is_dup:
            return {"prompt_id": existing_prompt_id, "deduped": True}

        tmpl_svc = _get_template_service()
        workflow = tmpl_svc.render_template(template_id, inputs)

        result = await _submit_prompt(
            workflow,
            extra_data={
                "openclaw": {"trace_id": trace_id, "source": "automation"},
                "moltbot": {"trace_id": trace_id, "source": "automation"},
            },
            source=source,
            trace_id=trace_id,
        )

        if result.get("prompt_id"):
            store.update_prompt_id(idempotency_key, result["prompt_id"])
        return result

    runner = get_scheduler_runner()
    runner._submit_fn = unified_submit_fn
    start_scheduler()

    register_trigger_routes(
        server.app,
        require_admin_token_fn=require_admin_token,
        submit_fn=unified_submit_fn,
    )
    register_approval_routes(
        server.app,
        require_admin_token_fn=require_admin_token,
        submit_fn=unified_submit_fn,
    )


_BRIDGE_ROUTE_SPECS = (
    ("add_post", "/moltbot/bridge/submit", "submit_handler"),
    ("add_post", "/moltbot/bridge/deliver", "deliver_handler"),
    ("add_get", "/moltbot/bridge/health", "health_handler"),
    ("add_post", "/openclaw/bridge/submit", "submit_handler"),
    ("add_post", "/openclaw/bridge/deliver", "deliver_handler"),
    ("add_get", "/openclaw/bridge/health", "health_handler"),
    ("add_post", "/api/moltbot/bridge/submit", "submit_handler"),
    ("add_post", "/api/moltbot/bridge/deliver", "deliver_handler"),
    ("add_get", "/api/moltbot/bridge/health", "health_handler"),
    ("add_post", "/api/openclaw/bridge/submit", "submit_handler"),
    ("add_post", "/api/openclaw/bridge/deliver", "deliver_handler"),
    ("add_get", "/api/openclaw/bridge/health", "health_handler"),
)


def _register_bridge_routes(router, bridge_handlers) -> None:
    # IMPORTANT: keep bridge route registration table-driven.
    # Missing one alias path here silently breaks one control-plane surface while
    # leaving the rest apparently healthy, which is hard to diagnose during startup.
    for method_name, path, handler_name in _BRIDGE_ROUTE_SPECS:
        registrar = getattr(router, method_name, None)
        if registrar is None:
            continue
        try:
            registrar(path, getattr(bridge_handlers, handler_name))
        except RuntimeError:
            if path.startswith("/api/"):
                continue
            raise


def _resolve_prompt_server():
    ps_mod = sys.modules.get("server")
    prompt_server = getattr(ps_mod, "PromptServer", None) if ps_mod else None
    return getattr(prompt_server, "instance", None) if prompt_server else None


def reset_route_bootstrap_for_tests() -> None:
    """Invalidate background ownership and reset the route bootstrap seam."""

    global _routes_registered
    global _registration_error
    global _registration_generation
    global _registration_inflight
    global _registration_retry_thread
    global _registration_started
    with _registration_condition:
        _registration_generation += 1
        _routes_registered = False
        _registration_inflight = False
        _registration_started = False
        _registration_error = None
        _registration_retry_thread = None
        _registration_condition.notify_all()
    try:
        from ..posture.effective import reset_effective_security_posture_for_tests

        reset_effective_security_posture_for_tests()
    except ImportError:
        # Dependency-light test/import mode may omit the posture module.
        pass


def _store_registration_success(*, generation: int | None = None) -> bool:
    global _routes_registered
    global _registration_error
    global _registration_inflight
    global _registration_started
    with _registration_condition:
        if generation is not None and generation != _registration_generation:
            return False
        _routes_registered = True
        _registration_inflight = False
        _registration_started = True
        _registration_error = None
        _registration_condition.notify_all()
        return True


def _store_registration_failure(
    exc: Exception,
    *,
    generation: int | None = None,
) -> bool:
    global _registration_error
    global _registration_inflight
    global _registration_started
    with _registration_condition:
        if generation is not None and generation != _registration_generation:
            return False
        _registration_inflight = False
        _registration_started = True
        _registration_error = exc
        _registration_condition.notify_all()
        return True


def _run_registration_retry_loop(
    *,
    max_attempts: int = _REGISTRATION_MAX_ATTEMPTS,
    initial_delay: float = _REGISTRATION_INITIAL_DELAY_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
    generation: int | None = None,
) -> None:
    """Run the sole bounded host-wait owner with explicit terminal outcomes."""

    global _registration_retry_thread
    logger = logging.getLogger("ComfyUI-OpenClaw")
    with _registration_condition:
        owner_generation = (
            _registration_generation if generation is None else generation
        )

    delay = max(0.0, float(initial_delay))
    try:
        for attempt in range(1, max_attempts + 1):
            with _registration_condition:
                if owner_generation != _registration_generation:
                    return

            try:
                server = _resolve_prompt_server()
            except Exception as exc:
                _mark_startup_fatal("route_registration", exc)
                _store_registration_failure(exc, generation=owner_generation)
                logger.error(
                    "PromptServer resolution failed (attempt=%s, error_type=%s)",
                    attempt,
                    type(exc).__name__,
                )
                return

            if server is not None:
                try:
                    _mark_route_registration_started(
                        attempt=attempt,
                        max_attempts=max_attempts,
                    )
                    _do_full_registration(server)
                    _mark_startup_ready_and_start_warmups()
                except Exception as exc:
                    _mark_startup_fatal("route_registration", exc)
                    _store_registration_failure(exc, generation=owner_generation)
                    logger.error(
                        "Route registration failed (attempt=%s, error_type=%s)",
                        attempt,
                        type(exc).__name__,
                    )
                    return
                _store_registration_success(generation=owner_generation)
                logger.info(
                    "Routes registered successfully (attempt=%s)",
                    attempt,
                )
                return

            _mark_host_waiting(attempt=attempt, max_attempts=max_attempts)
            logger.debug("PromptServer not ready (attempt=%s)", attempt)
            if attempt < max_attempts:
                sleep_fn(delay)
                delay = min(delay * 1.5, 30.0)

        failure = RuntimeError("route registration retry exhausted")
        _mark_startup_fatal(
            "host_wait",
            failure,
            reason_code="retry_exhausted",
        )
        _store_registration_failure(failure, generation=owner_generation)
        logger.error(
            "Route registration retry exhausted (attempts=%s)",
            max_attempts,
        )
    finally:
        with _registration_condition:
            if owner_generation == _registration_generation:
                current = threading.current_thread()
                if _registration_retry_thread is current:
                    _registration_retry_thread = None
                _registration_condition.notify_all()


def _start_registration_retry_loop() -> None:
    """Start at most one background host-wait owner."""

    global _registration_retry_thread
    with _registration_condition:
        existing = _registration_retry_thread
        if existing is not None and existing.is_alive():
            return
        generation = _registration_generation
        thread = threading.Thread(
            target=_run_registration_retry_loop,
            kwargs={"generation": generation},
            name="openclaw-route-retry",
            daemon=True,
        )
        _registration_retry_thread = thread
        thread.start()


def register_routes_once() -> None:
    """Initialize and register routes through one process-wide bootstrap owner."""

    global _registration_inflight
    global _registration_started
    logger = logging.getLogger("ComfyUI-OpenClaw")

    # CRITICAL: one condition owns initialization, registration, retry creation, and
    # terminal error replay. Independent flags reintroduce duplicate side effects.
    with _registration_condition:
        if _routes_registered:
            return
        if _registration_error is not None:
            raise _registration_error
        if _registration_started:
            while _registration_inflight:
                _registration_condition.wait()
            if _registration_error is not None:
                raise _registration_error
            return
        _registration_started = True
        _registration_inflight = True
        generation = _registration_generation

    try:
        from ..posture.effective import get_or_create_effective_security_posture

        # CRITICAL: this required startup owner installs process-static posture once.
        # Direct helper/API invocations resolve ephemeral snapshots instead.
        get_or_create_effective_security_posture()
        _mark_required_initialization_started()
        try:
            _register_plugins_and_shutdown_hooks()
            _initialize_registries_and_security_gate()
        except Exception as exc:
            _mark_startup_fatal("required_initialization", exc)
            _store_registration_failure(exc, generation=generation)
            logger.error(
                "Required startup initialization failed (error_type=%s)",
                type(exc).__name__,
            )
            raise

        try:
            server = _resolve_prompt_server()
        except Exception as exc:
            _mark_startup_fatal("route_registration", exc)
            _store_registration_failure(exc, generation=generation)
            logger.error(
                "Initial PromptServer resolution failed (error_type=%s)",
                type(exc).__name__,
            )
            raise
        if server is None:
            _mark_host_waiting(
                attempt=0,
                max_attempts=_REGISTRATION_MAX_ATTEMPTS,
            )
            try:
                _start_registration_retry_loop()
            except Exception as exc:
                _mark_startup_fatal(
                    "host_wait",
                    exc,
                    reason_code="retry_exhausted",
                )
                _store_registration_failure(exc, generation=generation)
                logger.error(
                    "Route registration retry owner failed to start (error_type=%s)",
                    type(exc).__name__,
                )
                raise
            logger.info(
                "PromptServer not ready; route registration retry owner started"
            )
            return

        _mark_route_registration_started()
        try:
            _do_full_registration(server)
            _mark_startup_ready_and_start_warmups()
        except Exception as exc:
            _mark_startup_fatal("route_registration", exc)
            _store_registration_failure(exc, generation=generation)
            logger.error(
                "Initial route registration failed (error_type=%s)",
                type(exc).__name__,
            )
            # CRITICAL: only host availability is retryable. Broken route
            # registration remains fail-closed and replays the same error.
            raise

        _store_registration_success(generation=generation)
        logger.info("Routes registered successfully on initial attempt")
    except BaseException:
        with _registration_condition:
            if (
                generation == _registration_generation
                and _registration_error is None
                and not _routes_registered
            ):
                _registration_started = False
            _registration_inflight = False
            _registration_condition.notify_all()
        raise
    finally:
        with _registration_condition:
            if generation == _registration_generation:
                _registration_inflight = False
                _registration_condition.notify_all()
