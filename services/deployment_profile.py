"""
Deployment profile evaluator for security posture checks.

This module provides a deterministic, machine-verifiable baseline for
deployment scenarios:
- local: single-user localhost
- lan: private network / trusted subnet
- public: internet-facing behind reverse proxy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional

from .env_aliases import EnvLookupMode, resolve_env

try:
    from .connector_allowlist_posture import evaluate_connector_allowlist_posture
except Exception:
    from services.connector_allowlist_posture import (  # type: ignore
        evaluate_connector_allowlist_posture,
    )

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}
_PUBLIC_BOUNDARY_ACK_ENV = "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK"
_PUBLIC_BOUNDARY_ACK_LEGACY_ENV = "MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK"


@dataclass
class DeploymentCheck:
    severity: str  # pass | warn | fail
    code: str
    message: str
    remediation: str = ""


@dataclass
class DeploymentProfileReport:
    profile: str
    checks: list[DeploymentCheck] = field(default_factory=list)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "warn")

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "pass")

    @property
    def has_failures(self) -> bool:
        return self.fail_count > 0

    def add(
        self, severity: str, code: str, message: str, remediation: str = ""
    ) -> None:
        self.checks.append(
            DeploymentCheck(
                severity=severity,
                code=code,
                message=message,
                remediation=remediation,
            )
        )

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "summary": {
                "pass": self.pass_count,
                "warn": self.warn_count,
                "fail": self.fail_count,
            },
            "checks": [
                {
                    "severity": c.severity,
                    "code": c.code,
                    "message": c.message,
                    "remediation": c.remediation,
                }
                for c in self.checks
            ],
        }

    def to_text(self) -> str:
        lines = [
            f"Deployment Profile: {self.profile}",
            (
                f"Summary: pass={self.pass_count} "
                f"warn={self.warn_count} fail={self.fail_count}"
            ),
            "",
        ]
        for c in self.checks:
            marker = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}[c.severity]
            lines.append(f"{marker} {c.code}: {c.message}")
            if c.remediation:
                lines.append(f"       Remediation: {c.remediation}")
        return "\n".join(lines)


def _env_get(
    env: Mapping[str, str],
    primary: str,
    legacy: Optional[str] = None,
    default: str = "",
) -> str:
    resolution = resolve_env(
        primary,
        aliases=(legacy,) if legacy else (),
        mode=EnvLookupMode.PRESENCE,
        default=default,
        env=env,
        warn_legacy=False,
    )
    return str(resolution.value)


def _is_enabled(
    env: Mapping[str, str], primary: str, legacy: Optional[str] = None
) -> bool:
    value = _env_get(env, primary, legacy, "").strip().lower()
    return value in TRUTHY


def _is_explicitly_disabled(
    env: Mapping[str, str], primary: str, legacy: Optional[str] = None
) -> bool:
    value = _env_get(env, primary, legacy, "").strip().lower()
    return value in FALSY


def _has_value(
    env: Mapping[str, str], primary: str, legacy: Optional[str] = None
) -> bool:
    return bool(_env_get(env, primary, legacy, "").strip())


def _has_public_shared_surface_boundary_ack(env: Mapping[str, str]) -> bool:
    raw = _env_get(
        env,
        _PUBLIC_BOUNDARY_ACK_ENV,
        _PUBLIC_BOUNDARY_ACK_LEGACY_ENV,
        "",
    ).strip()
    return raw.lower() in TRUTHY


def _check_webhook_auth(
    report: DeploymentProfileReport,
    env: Mapping[str, str],
    *,
    require_replay_protection: bool,
) -> None:
    mode = (
        _env_get(
            env,
            "OPENCLAW_WEBHOOK_AUTH_MODE",
            "MOLTBOT_WEBHOOK_AUTH_MODE",
            "",
        )
        .strip()
        .lower()
    )
    if not mode:
        report.add(
            "fail",
            "DP-WEBHOOK-001",
            "Webhook auth mode is not configured.",
            "Set OPENCLAW_WEBHOOK_AUTH_MODE to bearer, hmac, or bearer_or_hmac.",
        )
        return

    has_bearer = _has_value(
        env,
        "OPENCLAW_WEBHOOK_BEARER_TOKEN",
        "MOLTBOT_WEBHOOK_BEARER_TOKEN",
    )
    has_hmac = _has_value(
        env,
        "OPENCLAW_WEBHOOK_HMAC_SECRET",
        "MOLTBOT_WEBHOOK_HMAC_SECRET",
    )

    if mode == "bearer" and not has_bearer:
        report.add(
            "fail",
            "DP-WEBHOOK-002",
            "Webhook auth mode is bearer, but bearer token is missing.",
            "Set OPENCLAW_WEBHOOK_BEARER_TOKEN.",
        )
    elif mode == "hmac" and not has_hmac:
        report.add(
            "fail",
            "DP-WEBHOOK-003",
            "Webhook auth mode is hmac, but HMAC secret is missing.",
            "Set OPENCLAW_WEBHOOK_HMAC_SECRET.",
        )
    elif mode == "bearer_or_hmac" and not (has_bearer or has_hmac):
        report.add(
            "fail",
            "DP-WEBHOOK-004",
            "Webhook auth mode is bearer_or_hmac, but no credentials are configured.",
            (
                "Set OPENCLAW_WEBHOOK_BEARER_TOKEN and/or "
                "OPENCLAW_WEBHOOK_HMAC_SECRET."
            ),
        )
    elif mode not in {"bearer", "hmac", "bearer_or_hmac"}:
        report.add(
            "fail",
            "DP-WEBHOOK-005",
            f"Unsupported webhook auth mode: {mode}",
            "Use bearer, hmac, or bearer_or_hmac.",
        )
    else:
        report.add("pass", "DP-WEBHOOK-000", f"Webhook auth mode configured: {mode}")

    if require_replay_protection and _is_explicitly_disabled(
        env,
        "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION",
        "MOLTBOT_WEBHOOK_REQUIRE_REPLAY_PROTECTION",
    ):
        report.add(
            "fail",
            "DP-WEBHOOK-006",
            "Webhook replay protection is explicitly disabled.",
            "Set OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1.",
        )
    elif require_replay_protection:
        report.add(
            "pass",
            "DP-WEBHOOK-007",
            "Webhook replay protection is enabled (or default fail-closed).",
        )


def _check_flags_disabled(
    report: DeploymentProfileReport,
    env: Mapping[str, str],
    flags: Iterable[tuple[str, str]],
) -> None:
    for code, flag in flags:
        if _is_enabled(env, flag):
            report.add(
                "fail",
                code,
                f"{flag}=1 increases attack surface for this deployment profile.",
                f"Set {flag}=0 unless you have a dedicated admin-only control plane.",
            )
        else:
            report.add("pass", code, f"{flag} is disabled.")


def evaluate_deployment_profile(
    profile: str,
    environ: Optional[Mapping[str, str]] = None,
) -> DeploymentProfileReport:
    env: Mapping[str, str] = environ or {}
    normalized_profile = profile.strip().lower()
    if normalized_profile not in {"local", "lan", "public"}:
        raise ValueError(f"Unsupported deployment profile: {profile}")

    report = DeploymentProfileReport(profile=normalized_profile)

    if normalized_profile == "local":
        if _is_enabled(
            env, "OPENCLAW_ALLOW_REMOTE_ADMIN", "MOLTBOT_ALLOW_REMOTE_ADMIN"
        ):
            report.add(
                "fail",
                "DP-LOCAL-001",
                "Remote admin is enabled in local profile.",
                "Set OPENCLAW_ALLOW_REMOTE_ADMIN=0 for localhost-only posture.",
            )
        else:
            report.add(
                "pass",
                "DP-LOCAL-001",
                "Remote admin override is disabled.",
            )

        if _is_enabled(
            env,
            "OPENCLAW_TRUST_X_FORWARDED_FOR",
            "MOLTBOT_TRUST_X_FORWARDED_FOR",
        ):
            report.add(
                "fail",
                "DP-LOCAL-002",
                "Trusted proxy mode is enabled in local profile.",
                "Disable OPENCLAW_TRUST_X_FORWARDED_FOR unless you run behind a reverse proxy.",
            )
        else:
            report.add("pass", "DP-LOCAL-002", "Trusted proxy mode is disabled.")

        for code, flag in (
            ("DP-LOCAL-003", "OPENCLAW_ENABLE_EXTERNAL_TOOLS"),
            ("DP-LOCAL-004", "OPENCLAW_ENABLE_REGISTRY_SYNC"),
            ("DP-LOCAL-005", "OPENCLAW_ENABLE_TRANSFORMS"),
        ):
            if _is_enabled(env, flag):
                report.add(
                    "fail",
                    code,
                    f"{flag}=1 is enabled in local profile.",
                    (
                        f"Set {flag}=0 for local profile, or switch to lan/public "
                        "profile with explicit compensating controls."
                    ),
                )

        if not _has_value(env, "OPENCLAW_ADMIN_TOKEN", "MOLTBOT_ADMIN_TOKEN"):
            report.add(
                "warn",
                "DP-LOCAL-006",
                "Admin token is not configured (localhost convenience mode).",
                "Set OPENCLAW_ADMIN_TOKEN if this host may become shared later.",
            )
        else:
            report.add("pass", "DP-LOCAL-006", "Admin token is configured.")

        return report

    # LAN/Public common baseline.
    if not _has_value(env, "OPENCLAW_ADMIN_TOKEN", "MOLTBOT_ADMIN_TOKEN"):
        report.add(
            "fail",
            "DP-COMMON-001",
            "Admin token is missing.",
            "Set OPENCLAW_ADMIN_TOKEN.",
        )
    else:
        report.add("pass", "DP-COMMON-001", "Admin token is configured.")

    if not _has_value(
        env,
        "OPENCLAW_OBSERVABILITY_TOKEN",
        "MOLTBOT_OBSERVABILITY_TOKEN",
    ):
        report.add(
            "fail",
            "DP-COMMON-002",
            "Observability token is missing.",
            "Set OPENCLAW_OBSERVABILITY_TOKEN.",
        )
    else:
        report.add("pass", "DP-COMMON-002", "Observability token is configured.")

    _check_webhook_auth(report, env, require_replay_protection=True)

    _check_flags_disabled(
        report,
        env,
        [
            ("DP-COMMON-003", "OPENCLAW_ENABLE_EXTERNAL_TOOLS"),
            ("DP-COMMON-004", "OPENCLAW_ENABLE_REGISTRY_SYNC"),
            ("DP-COMMON-005", "OPENCLAW_ENABLE_TRANSFORMS"),
            ("DP-COMMON-006", "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST"),
            ("DP-COMMON-007", "OPENCLAW_ALLOW_INSECURE_BASE_URL"),
            ("DP-COMMON-008", "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE"),
        ],
    )

    bridge_enabled = _is_enabled(
        env, "OPENCLAW_BRIDGE_ENABLED", "MOLTBOT_BRIDGE_ENABLED"
    )
    if bridge_enabled and not _has_value(
        env, "OPENCLAW_BRIDGE_DEVICE_TOKEN", "MOLTBOT_BRIDGE_DEVICE_TOKEN"
    ):
        report.add(
            "fail",
            "DP-COMMON-009",
            "Bridge is enabled but device token is missing.",
            "Set OPENCLAW_BRIDGE_DEVICE_TOKEN.",
        )
    elif bridge_enabled:
        report.add("pass", "DP-COMMON-009", "Bridge device token is configured.")

    if normalized_profile == "lan":
        if not _is_enabled(
            env, "OPENCLAW_ALLOW_REMOTE_ADMIN", "MOLTBOT_ALLOW_REMOTE_ADMIN"
        ):
            report.add(
                "fail",
                "DP-LAN-001",
                "LAN profile requires explicit remote admin opt-in.",
                "Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 and protect access at network boundary.",
            )
        else:
            report.add(
                "pass",
                "DP-LAN-001",
                "Remote admin opt-in is enabled for LAN profile.",
            )

        if _is_enabled(
            env,
            "OPENCLAW_TRUST_X_FORWARDED_FOR",
            "MOLTBOT_TRUST_X_FORWARDED_FOR",
        ) and not _has_value(
            env,
            "OPENCLAW_TRUSTED_PROXIES",
            "MOLTBOT_TRUSTED_PROXIES",
        ):
            report.add(
                "fail",
                "DP-LAN-002",
                "Trusted proxy mode is enabled but trusted proxy CIDRs are missing.",
                "Set OPENCLAW_TRUSTED_PROXIES to explicit proxy IP/CIDR values.",
            )

        if bridge_enabled and not _is_enabled(env, "OPENCLAW_BRIDGE_MTLS_ENABLED"):
            report.add(
                "warn",
                "DP-LAN-003",
                "Bridge is enabled without mTLS enforcement.",
                "Enable OPENCLAW_BRIDGE_MTLS_ENABLED=1 for stronger LAN hardening.",
            )
        return report

    # Public profile checks.
    if _is_enabled(env, "OPENCLAW_ALLOW_REMOTE_ADMIN", "MOLTBOT_ALLOW_REMOTE_ADMIN"):
        report.add(
            "fail",
            "DP-PUBLIC-001",
            "Public profile forbids OPENCLAW_ALLOW_REMOTE_ADMIN=1 in baseline posture.",
            "Set OPENCLAW_ALLOW_REMOTE_ADMIN=0 and isolate admin plane behind private access.",
        )
    else:
        report.add(
            "pass",
            "DP-PUBLIC-001",
            "Remote admin override is disabled.",
        )

    if not _is_enabled(
        env,
        "OPENCLAW_TRUST_X_FORWARDED_FOR",
        "MOLTBOT_TRUST_X_FORWARDED_FOR",
    ):
        report.add(
            "fail",
            "DP-PUBLIC-002",
            "Public profile requires trusted proxy mode for accurate source attribution.",
            "Set OPENCLAW_TRUST_X_FORWARDED_FOR=1.",
        )
    else:
        report.add("pass", "DP-PUBLIC-002", "Trusted proxy mode is enabled.")

    if not _has_value(env, "OPENCLAW_TRUSTED_PROXIES", "MOLTBOT_TRUSTED_PROXIES"):
        report.add(
            "fail",
            "DP-PUBLIC-003",
            "Trusted proxy CIDRs are missing.",
            "Set OPENCLAW_TRUSTED_PROXIES to exact reverse proxy IP/CIDR ranges.",
        )
    else:
        report.add("pass", "DP-PUBLIC-003", "Trusted proxy CIDRs are configured.")

    if not _has_value(
        env,
        "OPENCLAW_CALLBACK_ALLOW_HOSTS",
        "MOLTBOT_CALLBACK_ALLOW_HOSTS",
    ):
        report.add(
            "warn",
            "DP-PUBLIC-004",
            "Callback allow_hosts is not configured.",
            "If callback delivery is used, set OPENCLAW_CALLBACK_ALLOW_HOSTS to strict host allowlist.",
        )

    connector_posture = evaluate_connector_allowlist_posture(env)
    if connector_posture["has_unguarded_connectors"]:
        # CRITICAL: public profile must fail closed for connector ingress without
        # explicit allowlists; warn-only here would leave internet-facing gaps.
        report.add(
            "fail",
            "DP-PUBLIC-009",
            "Connector allowlist coverage is missing for active platform(s): "
            + ", ".join(connector_posture["unguarded_platforms"]),
            (
                "Set connector allowlist vars before enabling public deployment. "
                "Allowed vars: "
                + ", ".join(connector_posture["recommended_allowlist_vars"])
            ),
        )
    elif connector_posture["has_active_connectors"]:
        report.add(
            "pass",
            "DP-PUBLIC-009",
            "Active connector platforms have allowlist coverage.",
        )
    else:
        report.add(
            "pass",
            "DP-PUBLIC-009",
            "No connector ingress platforms are active.",
        )

    if bridge_enabled:
        if not _is_enabled(env, "OPENCLAW_BRIDGE_MTLS_ENABLED"):
            report.add(
                "fail",
                "DP-PUBLIC-005",
                "Bridge is enabled in public profile without mTLS.",
                "Set OPENCLAW_BRIDGE_MTLS_ENABLED=1.",
            )
        if not _has_value(env, "OPENCLAW_BRIDGE_DEVICE_CERT_MAP"):
            report.add(
                "fail",
                "DP-PUBLIC-006",
                "Bridge mTLS is enabled but device certificate map is missing.",
                "Set OPENCLAW_BRIDGE_DEVICE_CERT_MAP=device_id:fingerprint,...",
            )
        if not _has_value(
            env,
            "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS",
            "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS",
        ):
            report.add(
                "fail",
                "DP-PUBLIC-007",
                "Bridge is enabled but allowed device ID list is missing.",
                "Set OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS with explicit device IDs.",
            )

    # CRITICAL: OpenClaw shares ComfyUI listener/port. Public posture cannot
    # infer reverse-proxy path policy automatically; require explicit operator
    # ack that upstream boundary controls are actually enforced.
    if not _has_public_shared_surface_boundary_ack(env):
        report.add(
            "fail",
            "DP-PUBLIC-008",
            "Public profile requires explicit shared-surface boundary acknowledgement.",
            (
                "Set OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1 only after "
                "reverse proxy path allowlist and network ACL deny ComfyUI-native "
                "high-risk routes."
            ),
        )
    else:
        report.add(
            "pass",
            "DP-PUBLIC-008",
            "Shared-surface boundary acknowledgement is enabled for public profile.",
        )

    return report
