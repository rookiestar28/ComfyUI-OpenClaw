"""Endpoint, token, SSRF, feature-flag, and advisory Security Doctor checks."""

from __future__ import annotations

import ipaddress
import json
import os
from typing import Dict

from .env_aliases import get_env_value
from .security_doctor_report import (
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)

try:
    from ..config import PACK_VERSION
except Exception:
    try:
        from config import PACK_VERSION  # type: ignore
    except Exception:
        PACK_VERSION = "0.0.0"

try:
    from .security_advisories import build_advisory_status
except Exception:
    from services.security_advisories import build_advisory_status  # type: ignore

HIGH_RISK_FLAGS: Dict[str, str] = {
    "OPENCLAW_ENABLE_REMOTE_ADMIN": "Remote admin access",
    "OPENCLAW_ENABLE_BRIDGE": "Sidecar bridge",
    "OPENCLAW_ENABLE_TRANSFORMS": "Constrained transforms (F42)",
    "OPENCLAW_ENABLE_REGISTRY_SYNC": "Remote registry sync (F41)",
    "OPENCLAW_DEV_MODE": "Development mode (auth bypass)",
}


def check_endpoint_exposure(report: SecurityReport) -> None:
    admin_token = get_env_value("OPENCLAW_ADMIN_TOKEN")
    obs_token = get_env_value("OPENCLAW_OBSERVABILITY_TOKEN")

    if not admin_token and not obs_token:
        report.add(
            SecurityCheckResult(
                name="endpoint_exposure",
                severity=SecuritySeverity.WARN.value,
                message="No admin or observability tokens configured — loopback-only mode",
                category="endpoint",
                detail="All admin/observability endpoints require loopback access.",
                remediation="Set OPENCLAW_ADMIN_TOKEN and OPENCLAW_OBSERVABILITY_TOKEN for remote deployments.",
            )
        )
    elif not admin_token:
        report.add(
            SecurityCheckResult(
                name="admin_token_missing",
                severity=SecuritySeverity.WARN.value,
                message="No admin token — config/secrets endpoints in convenience mode",
                category="endpoint",
                remediation="Set OPENCLAW_ADMIN_TOKEN for production deployments.",
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity=SecuritySeverity.PASS.value,
                message="Admin token configured",
                category="endpoint",
            )
        )

    if obs_token:
        report.add(
            SecurityCheckResult(
                name="observability_token_set",
                severity=SecuritySeverity.PASS.value,
                message="Observability token configured",
                category="endpoint",
            )
        )


def check_public_shared_surface_boundary(report: SecurityReport) -> None:
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").strip().lower()
    if not profile:
        profile = "local"

    ack_raw = (
        get_env_value("OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK", default="") or ""
    ).strip()
    ack = ack_raw.lower() in {"1", "true", "yes", "on"}

    report.environment["deployment_profile"] = profile
    report.environment["public_shared_surface_boundary_ack"] = (
        "enabled" if ack else "off"
    )

    if profile != "public":
        report.add(
            SecurityCheckResult(
                name="public_shared_surface_boundary",
                severity=SecuritySeverity.PASS.value,
                message="Shared-surface boundary acknowledgement not required outside public profile",
                category="endpoint",
                detail=f"profile={profile}",
            )
        )
        return

    if ack:
        report.add(
            SecurityCheckResult(
                name="public_shared_surface_boundary",
                severity=SecuritySeverity.PASS.value,
                message="Public shared-surface boundary acknowledgement is enabled",
                category="endpoint",
                remediation=(
                    "Keep reverse-proxy path allowlist + network ACL controls aligned with this acknowledgement."
                ),
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="public_shared_surface_boundary",
            severity=SecuritySeverity.WARN.value,
            message="Public profile boundary acknowledgement is missing for shared ComfyUI/OpenClaw surface",
            category="endpoint",
            remediation=(
                "Set OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1 only after reverse-proxy "
                "path allowlist and network ACL deny ComfyUI-native high-risk routes."
            ),
        )
    )


def check_token_boundaries(report: SecurityReport) -> None:
    admin_token = (get_env_value("OPENCLAW_ADMIN_TOKEN", default="") or "").strip()
    obs_token = (
        get_env_value("OPENCLAW_OBSERVABILITY_TOKEN", default="") or ""
    ).strip()

    if admin_token and obs_token and admin_token == obs_token:
        report.add(
            SecurityCheckResult(
                name="token_reuse",
                severity=SecuritySeverity.FAIL.value,
                message="Admin and observability tokens are identical — privilege confusion risk",
                category="token",
                remediation="Use distinct tokens for admin and observability access.",
            )
        )
    elif admin_token and obs_token:
        report.add(
            SecurityCheckResult(
                name="token_separation",
                severity=SecuritySeverity.PASS.value,
                message="Admin and observability tokens are distinct",
                category="token",
            )
        )

    for label, token in [("admin", admin_token), ("observability", obs_token)]:
        if token and len(token) < 16:
            report.add(
                SecurityCheckResult(
                    name=f"{label}_token_weak",
                    severity=SecuritySeverity.WARN.value,
                    message=f"{label.title()} token is short ({len(token)} chars) — consider longer tokens",
                    category="token",
                    remediation=f"Use a {label} token of at least 16 characters.",
                )
            )


def check_ssrf_posture(report: SecurityReport) -> None:
    # IMPORTANT: preserve the historical two-family order. Resolving ALLOWLIST before
    # exhausting ALLOW_HOSTS aliases changes which restriction governs SSRF checks.
    callback_allowlist = (
        get_env_value("OPENCLAW_CALLBACK_ALLOW_HOSTS", default="") or ""
    ).strip() or (
        get_env_value("OPENCLAW_CALLBACK_ALLOWLIST", default="") or ""
    ).strip()
    if callback_allowlist:
        hosts = [host.strip() for host in callback_allowlist.split(",") if host.strip()]
        if any("*" in host for host in hosts):
            report.add(
                SecurityCheckResult(
                    name="callback_wildcard",
                    severity=SecuritySeverity.FAIL.value,
                    message="Callback allowlist contains overly broad wildcards",
                    category="ssrf",
                    detail=f"Allowlist: {callback_allowlist}",
                    remediation="Use specific hostnames instead of wildcards in callback allowlists.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="callback_allowlist",
                    severity=SecuritySeverity.PASS.value,
                    message=f"Callback allowlist configured with {len(hosts)} host(s)",
                    category="ssrf",
                )
            )

    config_path = None
    try:
        from .state_dir import get_state_dir

        config_path = os.path.join(get_state_dir(), "config.json")
    except Exception:
        try:
            from services.state_dir import get_state_dir

            config_path = os.path.join(get_state_dir(), "config.json")
        except Exception:
            config_path = None

    if config_path and os.path.exists(config_path):
        try:
            from urllib.parse import urlparse

            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            base_url = cfg.get("base_url", "")
            if base_url:
                host = urlparse(base_url).hostname or ""
                try:
                    ip = ipaddress.ip_address(host)
                    if ip.is_private and not ip.is_loopback:
                        report.add(
                            SecurityCheckResult(
                                name="base_url_private_ip",
                                severity=SecuritySeverity.WARN.value,
                                message=f"LLM base_url points to private IP ({host})",
                                category="ssrf",
                                remediation="Ensure this is an intentional local LLM setup.",
                            )
                        )
                except ValueError:
                    pass
        except Exception:
            pass

    report.add(
        SecurityCheckResult(
            name="ssrf_posture",
            severity=SecuritySeverity.PASS.value,
            message="SSRF posture check completed",
            category="ssrf",
        )
    )


def check_feature_flags(report: SecurityReport) -> None:
    enabled_flags = []
    for env_key, label in HIGH_RISK_FLAGS.items():
        value = (get_env_value(env_key, default="") or "").strip().lower()
        if value in ("1", "true", "yes", "on"):
            enabled_flags.append(f"{env_key} ({label})")

    if enabled_flags:
        report.add(
            SecurityCheckResult(
                name="high_risk_flags",
                severity=SecuritySeverity.WARN.value,
                message=f"{len(enabled_flags)} high-risk feature flag(s) enabled",
                category="feature_flags",
                detail="; ".join(enabled_flags),
                remediation="Disable high-risk flags unless explicitly required for your deployment.",
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="high_risk_flags",
                severity=SecuritySeverity.PASS.value,
                message="All high-risk features disabled (default-off)",
                category="feature_flags",
            )
        )


def check_api_key_posture(report: SecurityReport) -> None:
    api_key = get_env_value("OPENCLAW_LLM_API_KEY", default="") or ""

    if not api_key:
        report.add(
            SecurityCheckResult(
                name="api_key_present",
                severity=SecuritySeverity.INFO.value,
                message="No LLM API key in environment — may use stored key or local LLM",
                category="api_key",
            )
        )
        return

    if len(api_key) < 10:
        report.add(
            SecurityCheckResult(
                name="api_key_length",
                severity=SecuritySeverity.WARN.value,
                message="LLM API key appears unusually short",
                category="api_key",
                remediation="Verify the API key is complete and valid.",
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="api_key_present",
            severity=SecuritySeverity.PASS.value,
            message="LLM API key configured via environment",
            category="api_key",
        )
    )


def check_vulnerability_advisories(report: SecurityReport) -> None:
    status = build_advisory_status(current_version=PACK_VERSION)
    report.advisory_status = status
    report.environment["advisory_current_version"] = str(
        status.get("current_version", "")
    )
    report.environment["advisory_affected"] = (
        "true" if bool(status.get("affected")) else "false"
    )
    report.environment["advisory_high_severity_affected"] = str(
        int(status.get("high_severity_affected") or 0)
    )

    if not status.get("affected"):
        report.add(
            SecurityCheckResult(
                name="vulnerability_advisories",
                severity=SecuritySeverity.PASS.value,
                message="No applicable security advisories for current version",
                category="advisory",
            )
        )
        return

    high_count = int(status.get("high_severity_affected") or 0)
    total_affected = len(
        [entry for entry in status.get("advisories", []) if entry.get("affected")]
    )
    mitigation = str(status.get("mitigation") or "").strip()
    remediation = (
        mitigation or "Upgrade to a non-affected version listed in advisory guidance."
    )

    if high_count > 0:
        report.add(
            SecurityCheckResult(
                name="vulnerability_advisories",
                severity=SecuritySeverity.WARN.value,
                message=(
                    f"Current version is affected by {total_affected} advisory(s), "
                    f"including {high_count} high-severity advisory(s)"
                ),
                category="advisory",
                remediation=remediation,
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="vulnerability_advisories",
            severity=SecuritySeverity.WARN.value,
            message=(
                f"Current version is affected by {total_affected} non-high-severity advisory(s)"
            ),
            category="advisory",
            remediation=remediation,
        )
    )


def check_csrf_no_origin_override(report: SecurityReport) -> None:
    raw = os.environ.get("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", "")
    enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
    report.environment["csrf_no_origin_override"] = "enabled" if enabled else "off"

    if enabled:
        report.add(
            SecurityCheckResult(
                name="csrf_no_origin_override",
                severity=SecuritySeverity.WARN.value,
                message=(
                    "OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN is enabled; "
                    "requests without Origin/Sec-Fetch-Site are allowed in localhost convenience mode"
                ),
                category="endpoint",
                remediation=(
                    "Unset OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN unless CLI/no-origin clients are required."
                ),
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="csrf_no_origin_override",
            severity=SecuritySeverity.PASS.value,
            message="No-origin CSRF override is disabled (strict default active)",
            category="endpoint",
        )
    )


__all__ = [
    "HIGH_RISK_FLAGS",
    "check_endpoint_exposure",
    "check_public_shared_surface_boundary",
    "check_token_boundaries",
    "check_ssrf_posture",
    "check_feature_flags",
    "check_api_key_posture",
    "check_vulnerability_advisories",
    "check_csrf_no_origin_override",
]
