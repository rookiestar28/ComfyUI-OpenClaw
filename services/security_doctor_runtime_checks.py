"""Runtime and filesystem Security Doctor checks."""

from __future__ import annotations

import os
import platform
import stat
import sys
from pathlib import Path

from .env_aliases import get_env_value
from .security_doctor_report import (
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)


def check_state_dir_permissions(report: SecurityReport) -> None:
    state_dir = None
    try:
        from .state_dir import get_state_dir

        state_dir = get_state_dir()
    except Exception:
        try:
            from services.state_dir import get_state_dir

            state_dir = get_state_dir()
        except Exception:
            state_dir = get_env_value("OPENCLAW_STATE_DIR")

    if not state_dir:
        report.add(
            SecurityCheckResult(
                name="state_dir_perms",
                severity=SecuritySeverity.SKIP.value,
                message="State directory not configured — using defaults",
                category="state_dir",
            )
        )
        return

    path = Path(state_dir)
    if not path.exists():
        report.add(
            SecurityCheckResult(
                name="state_dir_exists",
                severity=SecuritySeverity.INFO.value,
                message=f"State dir does not exist yet: {state_dir}",
                category="state_dir",
            )
        )
        return

    if not os.access(str(path), os.W_OK):
        report.add(
            SecurityCheckResult(
                name="state_dir_writable",
                severity=SecuritySeverity.FAIL.value,
                message=f"State dir not writable: {state_dir}",
                category="state_dir",
                remediation="Check file permissions on the state directory.",
            )
        )
        return

    if platform.system() != "Windows":
        try:
            mode = os.stat(str(path)).st_mode
            if mode & stat.S_IROTH:
                report.add(
                    SecurityCheckResult(
                        name="state_dir_world_readable",
                        severity=SecuritySeverity.WARN.value,
                        message="State directory is world-readable",
                        category="state_dir",
                        detail=f"Permissions: {oct(mode)}",
                        remediation=f"Run: chmod 700 {state_dir}",
                    )
                )
            if mode & stat.S_IWOTH:
                report.add(
                    SecurityCheckResult(
                        name="state_dir_world_writable",
                        severity=SecuritySeverity.FAIL.value,
                        message="State directory is world-writable — critical security risk",
                        category="state_dir",
                        detail=f"Permissions: {oct(mode)}",
                        remediation=f"Run: chmod 700 {state_dir}",
                    )
                )
        except Exception:
            pass

    secrets_file = path / "secrets.json"
    if secrets_file.exists() and platform.system() != "Windows":
        try:
            mode = os.stat(str(secrets_file)).st_mode
            if mode & (stat.S_IROTH | stat.S_IWOTH):
                report.add(
                    SecurityCheckResult(
                        name="secrets_file_perms",
                        severity=SecuritySeverity.FAIL.value,
                        message="Secrets file has world-accessible permissions",
                        category="state_dir",
                        remediation=f"Run: chmod 600 {secrets_file}",
                    )
                )
        except Exception:
            pass

    report.add(
        SecurityCheckResult(
            name="state_dir_check",
            severity=SecuritySeverity.PASS.value,
            message=f"State directory permissions OK: {state_dir}",
            category="state_dir",
        )
    )


def check_redaction_drift(report: SecurityReport) -> None:
    try:
        from .redaction import SENSITIVE_KEYS
    except ImportError:
        try:
            from services.redaction import SENSITIVE_KEYS
        except ImportError:
            report.add(
                SecurityCheckResult(
                    name="redaction_module",
                    severity=SecuritySeverity.SKIP.value,
                    message="Redaction module not available",
                    category="redaction",
                )
            )
            return

    expected_keys = {
        "api_key",
        "password",
        "secret",
        "token",
        "authorization",
        "private_key",
    }
    missing = expected_keys - SENSITIVE_KEYS
    if missing:
        report.add(
            SecurityCheckResult(
                name="redaction_coverage",
                severity=SecuritySeverity.WARN.value,
                message=f"Redaction missing expected sensitive keys: {missing}",
                category="redaction",
                remediation="Update services/redaction.py SENSITIVE_KEYS to include missing keys.",
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="redaction_coverage",
            severity=SecuritySeverity.PASS.value,
            message=f"Redaction covers all {len(expected_keys)} expected sensitive keys",
            category="redaction",
        )
    )


def check_comfyui_runtime(report: SecurityReport) -> None:
    in_venv = sys.prefix != sys.base_prefix
    report.environment["in_venv"] = str(in_venv)
    report.environment["os"] = platform.system()

    if not in_venv:
        report.add(
            SecurityCheckResult(
                name="venv_isolation",
                severity=SecuritySeverity.WARN.value,
                message="Not running in a virtual environment — shared system packages risk",
                category="runtime",
                remediation="Use a project-local .venv for dependency isolation.",
            )
        )

    desktop_indicators = [
        os.environ.get("COMFYUI_DESKTOP"),
        os.environ.get("ELECTRON_RUN_AS_NODE"),
    ]
    if any(desktop_indicators):
        report.environment["runtime_mode"] = "desktop"
        report.add(
            SecurityCheckResult(
                name="desktop_mode",
                severity=SecuritySeverity.INFO.value,
                message="ComfyUI Desktop mode detected",
                category="runtime",
                detail="Desktop mode may restrict file access and network behavior.",
            )
        )
    else:
        report.environment["runtime_mode"] = "standard"

    ver = sys.version_info
    if ver.major == 3 and ver.minor < 10:
        report.add(
            SecurityCheckResult(
                name="python_security",
                severity=SecuritySeverity.WARN.value,
                message=f"Python {ver.major}.{ver.minor} may lack security patches",
                category="runtime",
                remediation="Upgrade to Python 3.10+ for active security support.",
            )
        )


def check_hardening_wave2(report: SecurityReport) -> None:
    try:
        from .constrained_transforms import (
            TransformExecutorUnavailable,
            get_transform_executor,
        )
        from .transform_common import is_transforms_enabled
        from .transform_runner import TransformProcessRunner

        if not is_transforms_enabled():
            report.add(
                SecurityCheckResult(
                    name="s35_isolation",
                    severity=SecuritySeverity.SKIP.value,
                    message="Transforms disabled (feature flag off)",
                    category="wave2",
                )
            )
        else:
            executor = get_transform_executor()
            if isinstance(executor, TransformProcessRunner):
                report.add(
                    SecurityCheckResult(
                        name="s35_isolation",
                        severity=SecuritySeverity.PASS.value,
                        message="S35: Process isolation active",
                        category="wave2",
                    )
                )
            else:
                report.add(
                    SecurityCheckResult(
                        name="s35_isolation",
                        severity=SecuritySeverity.FAIL.value,
                        message="S35: Process isolation NOT active (using thread/unsafe executor)",
                        category="wave2",
                        detail=f"Current executor: {type(executor)}",
                        remediation="Ensure TransformProcessRunner is used.",
                    )
                )
    except TransformExecutorUnavailable as exc:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Process isolation unavailable; transforms disabled for safety",
                category="wave2",
                detail=str(exc),
                remediation="Restore services.transform_runner and its dependencies.",
            )
        )
    except ImportError:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Modules not importable",
                category="wave2",
            )
        )
    except RuntimeError as exc:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Process isolation check failed at runtime",
                category="wave2",
                detail=str(exc),
                remediation="Inspect transform runner initialization and environment dependencies.",
            )
        )

    try:
        from .tool_runner import is_tools_enabled

        if is_tools_enabled():
            report.add(
                SecurityCheckResult(
                    name="s12_tooling",
                    severity=SecuritySeverity.WARN.value,
                    message="S12: External tooling ENABLED (admin-only)",
                    category="wave2",
                    detail="Ensure tools_allowlist.json is strict.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="s12_tooling",
                    severity=SecuritySeverity.PASS.value,
                    message="S12: External tooling disabled (safe default)",
                    category="wave2",
                )
            )
    except ImportError:
        pass

    try:
        from .integrity import load_verified

        report.add(
            SecurityCheckResult(
                name="r77_integrity",
                severity=SecuritySeverity.PASS.value,
                message="R77: Integrity module loaded",
                category="wave2",
            )
        )
    except ImportError:
        report.add(
            SecurityCheckResult(
                name="r77_integrity",
                severity=SecuritySeverity.FAIL.value,
                message="R77: Integrity module missing",
                category="wave2",
            )
        )


def check_s45_exposure_posture(report: SecurityReport) -> None:
    is_exposed = "--listen" in sys.argv

    try:
        from .access_control import is_any_token_configured
    except ImportError:
        try:
            from services.access_control import is_any_token_configured  # type: ignore
        except ImportError:
            return

    auth_ready = is_any_token_configured()
    if is_exposed and not auth_ready:
        try:
            from .runtime_config import get_config
        except ImportError:
            from services.runtime_config import get_config  # type: ignore

        config = get_config()
        if config.security_dangerous_bind_override:
            report.add(
                SecurityCheckResult(
                    name="s45_dangerous_override",
                    severity=SecuritySeverity.WARN.value,
                    message="Server exposed without auth but dangerous override is active",
                    category="exposure",
                    detail="OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=1",
                    remediation="Remove override and configure authentication tokens.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="s45_exposed_no_auth",
                    severity=SecuritySeverity.FAIL.value,
                    message="Server exposed (--listen) without any authentication token",
                    category="exposure",
                    remediation="Set OPENCLAW_ADMIN_TOKEN or OPENCLAW_OBSERVABILITY_TOKEN.",
                )
            )
        return

    if not auth_ready:
        try:
            from .runtime_profile import is_hardened_mode
        except ImportError:
            try:
                from services.runtime_profile import is_hardened_mode  # type: ignore
            except ImportError:
                return

        try:
            from .access_control import is_auth_configured
        except ImportError:
            from services.access_control import is_auth_configured  # type: ignore

        if is_hardened_mode() and not is_auth_configured():
            report.add(
                SecurityCheckResult(
                    name="s45_hardened_loopback_no_admin",
                    severity=SecuritySeverity.WARN.value,
                    message="HARDENED profile requires admin auth even on loopback",
                    category="exposure",
                    remediation="Set OPENCLAW_ADMIN_TOKEN for hardened deployments.",
                )
            )
            return

    report.add(
        SecurityCheckResult(
            name="s45_exposure_posture",
            severity=SecuritySeverity.PASS.value,
            message="S45 exposure posture OK",
            category="exposure",
        )
    )


def check_runtime_guardrails(report: SecurityReport) -> None:
    try:
        from .runtime_guardrails import get_runtime_guardrails_snapshot
    except ImportError:
        try:
            from services.runtime_guardrails import (  # type: ignore
                get_runtime_guardrails_snapshot,
            )
        except ImportError:
            report.add(
                SecurityCheckResult(
                    name="s66_runtime_guardrails",
                    severity=SecuritySeverity.SKIP.value,
                    message="S66 runtime guardrails module unavailable",
                    category="runtime",
                )
            )
            return

    snapshot = get_runtime_guardrails_snapshot()
    report.environment["runtime_guardrails_status"] = str(snapshot.get("status", "ok"))
    report.environment["runtime_guardrails_code"] = str(snapshot.get("code", ""))
    report.environment["runtime_guardrails_violation_count"] = str(
        len(snapshot.get("violations", []))
    )

    if snapshot.get("status") == "ok":
        report.add(
            SecurityCheckResult(
                name="s66_runtime_guardrails",
                severity=SecuritySeverity.PASS.value,
                message="S66 runtime guardrails diagnostics OK",
                category="runtime",
            )
        )
        return

    violations = snapshot.get("violations", [])
    first = violations[0] if violations else {}
    report.add(
        SecurityCheckResult(
            name="s66_runtime_guardrails",
            severity=SecuritySeverity.WARN.value,
            message="S66 runtime guardrails degraded (invalid/clamped ENV values)",
            category="runtime",
            detail=(
                f"code={snapshot.get('code')} "
                f"path={first.get('path', '')} "
                f"violation={first.get('code', '')}"
            ).strip(),
            remediation="Fix invalid OPENCLAW guardrail environment values or remove overrides.",
        )
    )


__all__ = [
    "check_state_dir_permissions",
    "check_redaction_drift",
    "check_comfyui_runtime",
    "check_hardening_wave2",
    "check_s45_exposure_posture",
    "check_runtime_guardrails",
]
