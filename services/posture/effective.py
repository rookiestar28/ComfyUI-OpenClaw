"""Immutable process-static security posture implementation."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..env_aliases import EnvLookupMode, resolve_env

SCHEMA_VERSION = 1
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})
_CONTROL_PLANE_TRUTHY = frozenset({"1", "true", "yes"})
_VALID_DEPLOYMENT_PROFILES = frozenset({"local", "lan", "public"})
_VALID_WEBHOOK_MODES = frozenset({"bearer", "hmac", "bearer_or_hmac"})
_installed_posture: EffectiveSecurityPosture | None = None
_posture_lock = threading.RLock()


@dataclass(frozen=True, slots=True, kw_only=True)
class PostureFinding:
    severity: str
    code: str
    message: str
    remediation: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectiveSecurityPosture:
    schema_version: int
    runtime_profile: str
    deployment_profile: str
    mae_profile: str
    network_exposed: bool
    admin_token_configured: bool
    observability_token_configured: bool
    dangerous_profile_override: bool
    dangerous_bind_override: bool
    localhost_no_origin_override: bool
    allow_any_public_llm_host: bool
    allow_insecure_base_url: bool
    webhook_auth_mode: str
    webhook_bearer_configured: bool
    webhook_hmac_configured: bool
    webhook_replay_protection_required: bool
    remote_admin_enabled: bool
    trust_x_forwarded_for: bool
    trusted_proxies_configured: bool
    callback_allow_hosts_configured: bool
    external_tools_enabled: bool
    registry_sync_enabled: bool
    transforms_enabled: bool
    bridge_enabled: bool
    bridge_device_token_configured: bool
    bridge_mtls_enabled: bool
    bridge_device_cert_map_configured: bool
    bridge_allowed_device_ids_configured: bool
    public_shared_surface_acknowledged: bool
    control_plane_mode: str
    control_plane_url_configured: bool
    control_plane_token_configured: bool
    control_plane_prerequisites_satisfied: bool
    control_plane_compat_override: bool
    connector_active_platforms: tuple[str, ...]
    connector_unguarded_platforms: tuple[str, ...]
    connector_recommended_allowlist_vars: tuple[str, ...]
    deployment_checks: tuple[PostureFinding, ...]
    deployment_pass_codes: tuple[str, ...]
    deployment_warn_codes: tuple[str, ...]
    deployment_fail_codes: tuple[str, ...]
    startup_profile_passed: bool
    startup_profile_overridden: bool
    startup_profile_violation_codes: tuple[str, ...]
    blocked_surface_ids: tuple[str, ...]
    decision_codes: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _read(
    environ: Mapping[str, str],
    primary: str,
    legacy: str | None = None,
    default: str = "",
) -> str:
    try:
        value = resolve_env(
            primary,
            aliases=(legacy,) if legacy else (),
            mode=EnvLookupMode.PRESENCE,
            default=default,
            env=environ,
            warn_legacy=False,
        ).value
    except Exception:
        # CRITICAL: malformed environment providers must fail closed without echoing
        # exception content or the attempted value into diagnostics.
        raise ValueError("security posture input unavailable") from None
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        raise ValueError("security posture input is not scalar") from None


def _normalized(
    environ: Mapping[str, str],
    primary: str,
    legacy: str | None = None,
    default: str = "",
) -> str:
    return _read(environ, primary, legacy, default).strip().lower()


def _enabled(
    environ: Mapping[str, str],
    primary: str,
    legacy: str | None = None,
) -> bool:
    return _normalized(environ, primary, legacy) in _TRUTHY


def _configured(
    environ: Mapping[str, str],
    primary: str,
    legacy: str | None = None,
) -> bool:
    return bool(_read(environ, primary, legacy).strip())


def _network_exposed_from_argv() -> bool:
    # Preserve the accepted S41 heuristic exactly: only the explicit --listen flag
    # changes this process-static decision.
    return "--listen" in sys.argv


def _deployment_report(profile: str, environ: Mapping[str, str]):
    try:
        from ..deployment_profile import evaluate_deployment_profile
    except ImportError:  # pragma: no cover - top-level compatibility mode
        from services.deployment_profile import evaluate_deployment_profile

    return evaluate_deployment_profile(profile, environ)


def _connector_posture(environ: Mapping[str, str]) -> Mapping[str, Any]:
    try:
        from ..connector_allowlist_posture import evaluate_connector_allowlist_posture
    except ImportError:  # pragma: no cover - top-level compatibility mode
        from services.connector_allowlist_posture import (
            evaluate_connector_allowlist_posture,
        )

    return evaluate_connector_allowlist_posture(environ)


def _blocked_surface_ids(profile: str, mode: str) -> tuple[str, ...]:
    if profile != "public" or mode != "split":
        return ()
    # IMPORTANT: these are the stable scalar IDs from the S62 registry. Importing
    # control_plane here would create a dependency cycle before R233 packages the domain.
    return (
        "callback_egress",
        "registry_sync",
        "secrets_write",
        "tool_execution",
        "transforms_exec",
        "webhook_execute",
    )


def _safe_finding(check: Any) -> PostureFinding:
    message = str(check.message)
    if str(check.code) == "DP-WEBHOOK-005":
        # IMPORTANT: the legacy evaluator includes the raw invalid environment value.
        # The immutable boundary retains the stable code but never the untrusted value.
        message = "Unsupported webhook auth mode."
    return PostureFinding(
        severity=str(check.severity),
        code=str(check.code),
        message=message,
        remediation=str(check.remediation),
    )


def resolve_effective_security_posture(
    environ: Mapping[str, str] | None = None,
    *,
    network_exposed: bool | None = None,
) -> EffectiveSecurityPosture:
    # IMPORTANT: an explicitly supplied empty mapping means empty input. Do not use
    # `environ or os.environ`; doing so makes tests and lifecycle injection ambient.
    env = os.environ if environ is None else environ

    resolved_network_exposed = (
        _network_exposed_from_argv()
        if network_exposed is None
        else bool(network_exposed)
    )
    deployment_profile = _normalized(
        env, "OPENCLAW_DEPLOYMENT_PROFILE", default="local"
    )
    if deployment_profile not in _VALID_DEPLOYMENT_PROFILES:
        raise ValueError("unsupported deployment profile")

    raw_runtime_profile = _normalized(
        env, "OPENCLAW_RUNTIME_PROFILE", default="minimal"
    )
    runtime_profile = "hardened" if raw_runtime_profile == "hardened" else "minimal"
    mae_profile = (
        "hardened"
        if runtime_profile == "hardened" and deployment_profile != "public"
        else deployment_profile
    )

    try:
        report = _deployment_report(deployment_profile, env)
    except Exception:
        # CRITICAL: delegated evaluators must not expose hostile mapping values or
        # exception text across the immutable posture boundary.
        raise ValueError("security posture evaluation failed") from None
    findings = tuple(_safe_finding(check) for check in report.checks)
    pass_codes = tuple(item.code for item in findings if item.severity == "pass")
    warn_codes = tuple(item.code for item in findings if item.severity == "warn")
    fail_codes = tuple(item.code for item in findings if item.severity == "fail")

    dangerous_profile_override = _enabled(
        env, "OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE"
    )
    startup_violations = () if deployment_profile == "local" else fail_codes
    startup_overridden = bool(startup_violations and dangerous_profile_override)
    startup_passed = (
        deployment_profile == "local" or not startup_violations or startup_overridden
    )

    explicit_control_mode = _normalized(env, "OPENCLAW_CONTROL_PLANE_MODE")
    if explicit_control_mode in {"embedded", "split"}:
        control_plane_mode = explicit_control_mode
    elif deployment_profile == "public":
        control_plane_mode = "split"
    else:
        control_plane_mode = "embedded"

    control_plane_url_configured = _configured(env, "OPENCLAW_CONTROL_PLANE_URL")
    control_plane_token_configured = _configured(env, "OPENCLAW_CONTROL_PLANE_TOKEN")
    control_plane_prerequisites_satisfied = (
        control_plane_url_configured and control_plane_token_configured
    )
    control_plane_compat_override = (
        _normalized(env, "OPENCLAW_SPLIT_COMPAT_OVERRIDE") in _CONTROL_PLANE_TRUTHY
    )

    try:
        connector = _connector_posture(env)
    except Exception:
        raise ValueError("security posture evaluation failed") from None
    active_platforms = tuple(
        sorted({str(item) for item in connector["active_platforms"]})
    )
    unguarded_platforms = tuple(
        sorted({str(item) for item in connector["unguarded_platforms"]})
    )
    recommended_allowlist_vars = tuple(
        sorted({str(item) for item in connector["recommended_allowlist_vars"]})
    )

    reason_codes = list(startup_violations)
    if deployment_profile == "public" and control_plane_mode == "split":
        if not control_plane_url_configured:
            reason_codes.append("CP-URL-MISSING")
        if not control_plane_token_configured:
            reason_codes.append("CP-TOKEN-MISSING")
    elif deployment_profile == "public" and control_plane_mode == "embedded":
        if not control_plane_compat_override:
            reason_codes.append("CP-PUBLIC-EMBEDDED")
    reason_codes.extend(
        f"CONNECTOR-ALLOWLIST-{platform.upper()}" for platform in unguarded_platforms
    )
    if raw_runtime_profile not in {"", "minimal", "hardened"}:
        reason_codes.append("RUNTIME-PROFILE-DEFAULTED")

    decision_codes = [
        (
            "STARTUP-OVERRIDDEN"
            if startup_overridden
            else "STARTUP-PASS" if startup_passed else "STARTUP-DENY"
        ),
        (
            "CONTROL-PLANE-PASS"
            if (
                deployment_profile != "public"
                or (
                    control_plane_mode == "split"
                    and control_plane_prerequisites_satisfied
                )
                or (control_plane_mode == "embedded" and control_plane_compat_override)
            )
            else "CONTROL-PLANE-DENY"
        ),
        (
            "CONNECTORS-NONE"
            if not active_platforms
            else "CONNECTORS-UNGUARDED" if unguarded_platforms else "CONNECTORS-GUARDED"
        ),
        "NETWORK-EXPOSED" if resolved_network_exposed else "NETWORK-LOOPBACK",
    ]

    raw_webhook_mode = _normalized(
        env,
        "OPENCLAW_WEBHOOK_AUTH_MODE",
        "MOLTBOT_WEBHOOK_AUTH_MODE",
    )
    webhook_auth_mode = (
        raw_webhook_mode
        if raw_webhook_mode in _VALID_WEBHOOK_MODES
        else "unset" if not raw_webhook_mode else "invalid"
    )
    replay_value = _normalized(
        env,
        "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION",
        "MOLTBOT_WEBHOOK_REQUIRE_REPLAY_PROTECTION",
    )

    return EffectiveSecurityPosture(
        schema_version=SCHEMA_VERSION,
        runtime_profile=runtime_profile,
        deployment_profile=deployment_profile,
        mae_profile=mae_profile,
        network_exposed=resolved_network_exposed,
        admin_token_configured=_configured(
            env, "OPENCLAW_ADMIN_TOKEN", "MOLTBOT_ADMIN_TOKEN"
        ),
        observability_token_configured=_configured(
            env, "OPENCLAW_OBSERVABILITY_TOKEN", "MOLTBOT_OBSERVABILITY_TOKEN"
        ),
        dangerous_profile_override=dangerous_profile_override,
        dangerous_bind_override=_enabled(
            env,
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE",
            "MOLTBOT_SECURITY_DANGEROUS_BIND_OVERRIDE",
        ),
        localhost_no_origin_override=(
            _normalized(env, "OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN") == "true"
        ),
        allow_any_public_llm_host=_enabled(
            env,
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
            "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
        ),
        allow_insecure_base_url=_enabled(
            env,
            "OPENCLAW_ALLOW_INSECURE_BASE_URL",
            "MOLTBOT_ALLOW_INSECURE_BASE_URL",
        ),
        webhook_auth_mode=webhook_auth_mode,
        webhook_bearer_configured=_configured(
            env,
            "OPENCLAW_WEBHOOK_BEARER_TOKEN",
            "MOLTBOT_WEBHOOK_BEARER_TOKEN",
        ),
        webhook_hmac_configured=_configured(
            env,
            "OPENCLAW_WEBHOOK_HMAC_SECRET",
            "MOLTBOT_WEBHOOK_HMAC_SECRET",
        ),
        webhook_replay_protection_required=replay_value not in _FALSY,
        remote_admin_enabled=_enabled(
            env, "OPENCLAW_ALLOW_REMOTE_ADMIN", "MOLTBOT_ALLOW_REMOTE_ADMIN"
        ),
        trust_x_forwarded_for=_enabled(
            env,
            "OPENCLAW_TRUST_X_FORWARDED_FOR",
            "MOLTBOT_TRUST_X_FORWARDED_FOR",
        ),
        trusted_proxies_configured=_configured(
            env, "OPENCLAW_TRUSTED_PROXIES", "MOLTBOT_TRUSTED_PROXIES"
        ),
        callback_allow_hosts_configured=_configured(
            env,
            "OPENCLAW_CALLBACK_ALLOW_HOSTS",
            "MOLTBOT_CALLBACK_ALLOW_HOSTS",
        ),
        external_tools_enabled=_enabled(env, "OPENCLAW_ENABLE_EXTERNAL_TOOLS"),
        registry_sync_enabled=_enabled(env, "OPENCLAW_ENABLE_REGISTRY_SYNC"),
        transforms_enabled=_enabled(env, "OPENCLAW_ENABLE_TRANSFORMS"),
        bridge_enabled=_enabled(
            env, "OPENCLAW_BRIDGE_ENABLED", "MOLTBOT_BRIDGE_ENABLED"
        ),
        bridge_device_token_configured=_configured(
            env,
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "MOLTBOT_BRIDGE_DEVICE_TOKEN",
        ),
        bridge_mtls_enabled=_enabled(env, "OPENCLAW_BRIDGE_MTLS_ENABLED"),
        bridge_device_cert_map_configured=_configured(
            env, "OPENCLAW_BRIDGE_DEVICE_CERT_MAP"
        ),
        bridge_allowed_device_ids_configured=_configured(
            env,
            "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS",
            "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS",
        ),
        public_shared_surface_acknowledged=_enabled(
            env,
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
            "MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
        ),
        control_plane_mode=control_plane_mode,
        control_plane_url_configured=control_plane_url_configured,
        control_plane_token_configured=control_plane_token_configured,
        control_plane_prerequisites_satisfied=control_plane_prerequisites_satisfied,
        control_plane_compat_override=control_plane_compat_override,
        connector_active_platforms=active_platforms,
        connector_unguarded_platforms=unguarded_platforms,
        connector_recommended_allowlist_vars=recommended_allowlist_vars,
        deployment_checks=findings,
        deployment_pass_codes=pass_codes,
        deployment_warn_codes=warn_codes,
        deployment_fail_codes=fail_codes,
        startup_profile_passed=startup_passed,
        startup_profile_overridden=startup_overridden,
        startup_profile_violation_codes=startup_violations,
        blocked_surface_ids=_blocked_surface_ids(
            deployment_profile, control_plane_mode
        ),
        decision_codes=tuple(decision_codes),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def install_effective_security_posture(
    posture: EffectiveSecurityPosture,
) -> EffectiveSecurityPosture:
    if not isinstance(posture, EffectiveSecurityPosture):
        raise TypeError("posture must be EffectiveSecurityPosture")
    global _installed_posture
    with _posture_lock:
        if _installed_posture is None:
            _installed_posture = posture
        elif _installed_posture is not posture:
            # CRITICAL: silently replacing process posture creates contradictory
            # authorization decisions. Reset is an explicit lifecycle/test operation.
            raise RuntimeError("effective security posture is already installed")
        return _installed_posture


def get_effective_security_posture(
    *, required: bool = True
) -> EffectiveSecurityPosture | None:
    with _posture_lock:
        posture = _installed_posture
    if posture is None and required:
        raise RuntimeError("effective security posture is not installed")
    return posture


def get_or_create_effective_security_posture(
    environ: Mapping[str, str] | None = None,
    *,
    network_exposed: bool | None = None,
) -> EffectiveSecurityPosture:
    with _posture_lock:
        if _installed_posture is not None:
            return _installed_posture
        posture = resolve_effective_security_posture(
            environ,
            network_exposed=network_exposed,
        )
        # The RLock makes this identity-stable even under concurrent startup.
        return install_effective_security_posture(posture)


def reset_effective_security_posture_for_tests() -> None:
    global _installed_posture
    with _posture_lock:
        _installed_posture = None


def effective_security_posture_diagnostics(
    posture: EffectiveSecurityPosture | None = None,
) -> dict[str, Any]:
    resolved = posture or get_effective_security_posture()
    assert resolved is not None
    return {
        "schema_version": resolved.schema_version,
        "runtime_profile": resolved.runtime_profile,
        "deployment_profile": resolved.deployment_profile,
        "mae_profile": resolved.mae_profile,
        "network_exposed": resolved.network_exposed,
        "authentication": {
            "admin_configured": resolved.admin_token_configured,
            "observability_configured": resolved.observability_token_configured,
        },
        "startup_gate": {
            "passed": resolved.startup_profile_passed,
            "overridden": resolved.startup_profile_overridden,
            "violation_codes": list(resolved.startup_profile_violation_codes),
        },
        "control_plane": {
            "mode": resolved.control_plane_mode,
            "prerequisites_satisfied": (resolved.control_plane_prerequisites_satisfied),
            "compat_override": resolved.control_plane_compat_override,
            "blocked_surface_count": len(resolved.blocked_surface_ids),
        },
        "connectors": {
            "active_count": len(resolved.connector_active_platforms),
            "unguarded_count": len(resolved.connector_unguarded_platforms),
        },
        "decision_codes": list(resolved.decision_codes),
        "reason_codes": list(resolved.reason_codes),
    }
