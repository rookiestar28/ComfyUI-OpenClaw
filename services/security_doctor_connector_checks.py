"""Connector-specific Security Doctor checks."""

from __future__ import annotations

import os

from .env_aliases import get_env_value
from .security_doctor_report import (
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)

try:
    from .connector_allowlist_posture import (
        evaluate_connector_allowlist_posture,
        is_strict_connector_allowlist_profile,
    )
except Exception:
    from services.connector_allowlist_posture import (  # type: ignore
        evaluate_connector_allowlist_posture,
        is_strict_connector_allowlist_profile,
    )


def check_connector_security_posture(report: SecurityReport) -> None:
    posture = evaluate_connector_allowlist_posture(os.environ)
    active_markers = posture["active_markers"]
    active_platforms = posture["active_platforms"]
    unguarded_platforms = posture["unguarded_platforms"]
    configured_allowlists = posture["configured_allowlists"]
    recommended_allowlist_vars = posture["recommended_allowlist_vars"]

    if active_markers:
        report.add(
            SecurityCheckResult(
                name="s32_connector_tokens",
                severity=SecuritySeverity.PASS.value,
                message=f"{len(active_platforms)} connector platform(s) active",
                category="connector",
                detail=(
                    "Platforms: "
                    + ", ".join(active_platforms)
                    + " | markers: "
                    + ", ".join(active_markers)
                ),
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="s32_connector_tokens",
                severity=SecuritySeverity.INFO.value,
                message="No connector tokens configured (connectors not enabled)",
                category="connector",
            )
        )

    if active_platforms and unguarded_platforms:
        strict_profile = is_strict_connector_allowlist_profile(os.environ)
        severity = (
            SecuritySeverity.FAIL.value
            if strict_profile
            else SecuritySeverity.WARN.value
        )
        posture_hint = "public/hardened" if strict_profile else "non-strict"
        report.add(
            SecurityCheckResult(
                name="s32_allowlist_coverage",
                severity=severity,
                message=(
                    "Connector ingress active but allowlist coverage missing for: "
                    + ", ".join(unguarded_platforms)
                ),
                category="connector",
                detail=(
                    f"Profile posture={posture_hint}. "
                    "Without allowlists, connectors may accept messages from any user/channel."
                ),
                remediation=(
                    "Set platform allowlists before enabling internet-facing connector ingress. "
                    "Allowed vars: " + ", ".join(recommended_allowlist_vars)
                ),
            )
        )
    elif active_platforms:
        report.add(
            SecurityCheckResult(
                name="s32_allowlist_coverage",
                severity=SecuritySeverity.PASS.value,
                message=(
                    f"Allowlist coverage present for {len(active_platforms)} active connector platform(s)"
                ),
                category="connector",
                detail=(
                    "Configured allowlists: " + ", ".join(configured_allowlists)
                    if configured_allowlists
                    else "Configured allowlists: (none required for inactive platforms)"
                ),
            )
        )

    wa_token = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN", "").strip()
    wa_secret = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET", "").strip()
    line_secret = os.environ.get("OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET", "").strip()
    line_token = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN", ""
    ).strip()

    if wa_token and not wa_secret:
        report.add(
            SecurityCheckResult(
                name="s32_whatsapp_sig_missing",
                severity=SecuritySeverity.WARN.value,
                message="WhatsApp access token set but app_secret missing — webhook signature verification disabled",
                category="connector",
                remediation="Set OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET for production webhook security.",
            )
        )
    if line_token and not line_secret:
        report.add(
            SecurityCheckResult(
                name="s32_line_sig_missing",
                severity=SecuritySeverity.WARN.value,
                message="LINE access token set but channel_secret missing — webhook signature verification disabled",
                category="connector",
                remediation="Set OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET for production webhook security.",
            )
        )

    dev_mode = (get_env_value("OPENCLAW_DEV_MODE", default="") or "").strip().lower()
    if dev_mode in ("1", "true", "yes", "on") and active_markers:
        report.add(
            SecurityCheckResult(
                name="s32_dev_mode_with_connectors",
                severity=SecuritySeverity.WARN.value,
                message="Dev mode enabled with active connectors — auth bypass risk",
                category="connector",
                remediation="Disable OPENCLAW_DEV_MODE when connectors are internet-exposed.",
            )
        )


__all__ = ["check_connector_security_posture"]
