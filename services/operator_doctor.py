"""
R72 — Operator Doctor CLI.

One-command diagnostics for deployment readiness and runtime health.
Read-only checks only; no auto-remediation.

Checks:
- Release-gate: required contract files, feature-flag policy, route health
- Runtime: .venv usage, Python/Node versions, Windows pre-commit/cache pitfalls
- Config/Token: state-dir permissions, token posture, env key presence

Usage:
    python -m services.operator_doctor
    python scripts/operator_doctor.py
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .env_aliases import EnvLookupMode, get_env_value

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Severity(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    INFO = "info"


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    severity: str  # Severity.value
    message: str
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.remediation:
            d["remediation"] = self.remediation
        return d


@dataclass
class DoctorReport:
    """Aggregated diagnostic report."""

    checks: List[CheckResult] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    def build_summary(self) -> None:
        counts: Dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
        for c in self.checks:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        self.summary = counts

    @property
    def has_failures(self) -> bool:
        return any(c.severity == Severity.FAIL.value for c in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        self.build_summary()
        return {
            "environment": self.environment,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
        }

    def to_human(self) -> str:
        """Human-readable report output."""
        self.build_summary()
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  OpenClaw Operator Doctor Report")
        lines.append("=" * 60)
        lines.append("")

        # Environment
        lines.append("Environment:")
        for k, v in self.environment.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

        # Checks grouped by severity
        for sev in [Severity.FAIL, Severity.WARN, Severity.PASS, Severity.SKIP]:
            checks = [c for c in self.checks if c.severity == sev.value]
            if not checks:
                continue
            icon = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "○"}[sev.value]
            lines.append(f"  [{icon}] {sev.value.upper()} ({len(checks)})")
            for c in checks:
                lines.append(f"      {c.name}: {c.message}")
                if c.detail:
                    lines.append(f"        Detail: {c.detail}")
                if c.remediation:
                    lines.append(f"        Fix: {c.remediation}")
            lines.append("")

        # Summary
        total = sum(self.summary.values())
        lines.append("-" * 60)
        lines.append(
            f"  Total: {total}  |  "
            f"Pass: {self.summary.get('pass', 0)}  |  "
            f"Warn: {self.summary.get('warn', 0)}  |  "
            f"Fail: {self.summary.get('fail', 0)}  |  "
            f"Skip: {self.summary.get('skip', 0)}"
        )
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pack root detection
# ---------------------------------------------------------------------------


def _get_pack_root() -> Path:
    """Detect the ComfyUI-OpenClaw pack root directory."""
    # Try relative to this file
    this_dir = Path(__file__).resolve().parent
    candidate = this_dir.parent
    if (candidate / "ROADMAP.md").exists():
        return candidate
    # Fallback to cwd
    return Path.cwd()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version(report: DoctorReport) -> None:
    ver = sys.version_info
    report.environment["python"] = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver.major == 3 and ver.minor >= 10:
        report.add(
            CheckResult(
                name="python_version",
                severity=Severity.PASS.value,
                message=f"Python {ver.major}.{ver.minor}.{ver.micro}",
            )
        )
    else:
        report.add(
            CheckResult(
                name="python_version",
                severity=Severity.FAIL.value,
                message=f"Python {ver.major}.{ver.minor}.{ver.micro} (need >=3.10)",
                remediation="Install Python 3.10 or later.",
            )
        )


def check_node_version(report: DoctorReport) -> None:
    node = shutil.which("node")
    if not node:
        report.add(
            CheckResult(
                name="node_version",
                severity=Severity.WARN.value,
                message="Node.js not found on PATH",
                remediation="Install Node.js 18+ for frontend E2E tests.",
            )
        )
        return
    try:
        out = subprocess.check_output([node, "--version"], text=True, timeout=5).strip()
        report.environment["node"] = out
        major = int(out.lstrip("v").split(".")[0])
        if major >= 18:
            report.add(
                CheckResult(
                    name="node_version",
                    severity=Severity.PASS.value,
                    message=f"Node.js {out}",
                )
            )
        else:
            report.add(
                CheckResult(
                    name="node_version",
                    severity=Severity.FAIL.value,
                    message=f"Node.js {out} (need >=18)",
                    remediation="Upgrade to Node.js 18 or later.",
                )
            )
    except Exception as e:
        report.add(
            CheckResult(
                name="node_version",
                severity=Severity.WARN.value,
                message=f"Could not determine Node.js version: {e}",
            )
        )


def check_venv(report: DoctorReport) -> None:
    in_venv = sys.prefix != sys.base_prefix
    report.environment["in_venv"] = str(in_venv)
    if in_venv:
        report.add(
            CheckResult(
                name="venv_active",
                severity=Severity.PASS.value,
                message="Running inside a virtual environment",
            )
        )
    else:
        report.add(
            CheckResult(
                name="venv_active",
                severity=Severity.WARN.value,
                message="Not running inside a virtual environment",
                remediation="Use a project-local .venv for isolation.",
            )
        )


def check_contract_files(report: DoctorReport, pack_root: Path) -> None:
    """Check that required release-gate contract files exist."""
    required_files = [
        "docs/release/api_contract.md",
        "docs/release/config_secrets_contract.md",
        "docs/release/compatibility_matrix.md",
        "docs/release/support_policy.md",
        "docs/release/ci_regression_policy.md",
        "RELEASE_CHECKLIST.md",
        "SECURITY.md",
        "tests/TEST_SOP.md",
    ]
    for rel_path in required_files:
        full = pack_root / rel_path
        if full.exists():
            report.add(
                CheckResult(
                    name=f"contract_file:{rel_path}",
                    severity=Severity.PASS.value,
                    message=f"Found: {rel_path}",
                )
            )
        else:
            report.add(
                CheckResult(
                    name=f"contract_file:{rel_path}",
                    severity=Severity.FAIL.value,
                    message=f"Missing required contract file: {rel_path}",
                    remediation=f"Create or restore {rel_path}.",
                )
            )


def check_compatibility_matrix_governance(
    report: DoctorReport, pack_root: Path
) -> None:
    """
    R90: Compatibility matrix freshness + anchor drift visibility.

    Read-only local check. Uses optional env overrides for observed anchors:
    - OPENCLAW_COMPAT_ANCHOR_COMFYUI
    - OPENCLAW_COMPAT_ANCHOR_COMFYUI_FRONTEND
    - OPENCLAW_COMPAT_ANCHOR_DESKTOP
    - OPENCLAW_COMPAT_ANCHOR_COMFY_DESKTOP
    """
    matrix_path = pack_root / "docs" / "release" / "compatibility_matrix.md"
    if not matrix_path.exists():
        report.add(
            CheckResult(
                name="compatibility_matrix_governance",
                severity=Severity.SKIP.value,
                message="Compatibility matrix file missing",
            )
        )
        return

    try:
        from .compatibility_matrix_governance import (
            build_host_surface_contract,
            detect_anchor_drift,
            normalize_observed_anchors,
            read_matrix_document,
            validate_metadata,
        )
    except Exception as e:
        report.add(
            CheckResult(
                name="compatibility_matrix_governance",
                severity=Severity.WARN.value,
                message="Compatibility matrix governance helpers unavailable",
                detail=str(e),
            )
        )
        return

    doc = read_matrix_document(matrix_path)
    validation = validate_metadata(doc.get("metadata"))
    observed = normalize_observed_anchors(
        comfyui=os.environ.get("OPENCLAW_COMPAT_ANCHOR_COMFYUI"),
        comfyui_frontend=os.environ.get("OPENCLAW_COMPAT_ANCHOR_COMFYUI_FRONTEND"),
        desktop=os.environ.get("OPENCLAW_COMPAT_ANCHOR_DESKTOP"),
        comfy_desktop=os.environ.get("OPENCLAW_COMPAT_ANCHOR_COMFY_DESKTOP"),
    )
    drift = detect_anchor_drift((doc.get("metadata") or {}).get("anchors"), observed)
    host_contract = build_host_surface_contract(
        (doc.get("metadata") or {}).get("anchors"),
        published_surfaces=(doc.get("metadata") or {}).get("host_surfaces"),
    )

    report.environment["compat_matrix_validation_code"] = str(
        validation.get("code", "")
    )
    if validation.get("age_days") is not None:
        report.environment["compat_matrix_age_days"] = str(validation["age_days"])
    report.environment["compat_matrix_drift_code"] = str(drift.get("code", ""))
    report.environment["compat_host_surface_code"] = str(host_contract.get("code", ""))
    report.environment["compat_desktop_embedded_frontend_status"] = str(
        (
            host_contract.get("surfaces", {})
            .get("desktop", {})
            .get("frontend_parity", {})
            .get("status", "")
        )
    )
    report.environment["compat_comfy_desktop_generation"] = str(
        host_contract.get("surfaces", {}).get("comfy_desktop", {}).get("generation", "")
    )
    report.environment["compat_comfy_desktop_hosted_version_mode"] = str(
        host_contract.get("surfaces", {})
        .get("comfy_desktop", {})
        .get("hosted_version_mode", "")
    )

    if not validation.get("ok"):
        report.add(
            CheckResult(
                name="compatibility_matrix_governance",
                severity=Severity.WARN.value,
                message="Compatibility matrix metadata invalid",
                detail=json.dumps(
                    {
                        "doc_issues": doc.get("issues", []),
                        "violations": validation.get("violations", []),
                    },
                    ensure_ascii=False,
                ),
                remediation=(
                    "Repair the metadata block in docs/release/compatibility_matrix.md "
                    "or run scripts/compatibility_matrix_refresh.py --apply."
                ),
            )
        )
        return

    age_days = validation.get("age_days")
    status = validation.get("status")
    if status in ("warning", "stale"):
        report.add(
            CheckResult(
                name="compatibility_matrix_governance",
                severity=Severity.WARN.value,
                message=(
                    "Compatibility matrix age exceeds warning policy"
                    if status == "warning"
                    else "Compatibility matrix is stale"
                ),
                detail=json.dumps(
                    {
                        "age_days": age_days,
                        "warn_age_days": validation.get("warn_age_days"),
                        "max_age_days": validation.get("max_age_days"),
                    }
                ),
                remediation=(
                    "Refresh and validate the compatibility matrix before release tagging "
                    "(scripts/compatibility_matrix_refresh.py)."
                ),
            )
        )
    else:
        report.add(
            CheckResult(
                name="compatibility_matrix_governance",
                severity=Severity.PASS.value,
                message=f"Compatibility matrix metadata fresh (age={age_days}d)",
            )
        )

    if not host_contract.get("ok"):
        report.add(
            CheckResult(
                name="compatibility_matrix_host_surface_contract",
                severity=Severity.WARN.value,
                message="Compatibility matrix host-surface contract is incomplete",
                detail=json.dumps(
                    host_contract.get("violations", []), ensure_ascii=False
                ),
                remediation=(
                    "Record both Desktop generations: the legacy "
                    "`<desktop> (core <core> / frontend <frontend>)` bundle and the "
                    "current `<app> (<revision> / <describe>)` managed-install anchor."
                ),
            )
        )
    else:
        desktop_surface = host_contract["surfaces"]["desktop"]
        current_desktop_surface = host_contract["surfaces"]["comfy_desktop"]
        parity = desktop_surface["frontend_parity"]
        report.add(
            CheckResult(
                name="compatibility_matrix_host_surface_contract",
                severity=Severity.PASS.value,
                message=(
                    "Desktop host generations tracked separately "
                    f"({parity['status']} vs standalone frontend)"
                ),
                detail=json.dumps(
                    {
                        "desktop_anchor": desktop_surface["anchor"],
                        "desktop_version": desktop_surface["desktop_version"],
                        "embedded_frontend_version": desktop_surface[
                            "embedded_frontend_version"
                        ],
                        "reference_frontend_version": parity[
                            "reference_frontend_version"
                        ],
                        "comfy_desktop_anchor": current_desktop_surface["anchor"],
                        "comfy_desktop_version": current_desktop_surface[
                            "desktop_version"
                        ],
                        "comfy_desktop_generation": current_desktop_surface[
                            "generation"
                        ],
                        "comfy_desktop_hosted_version_mode": current_desktop_surface[
                            "hosted_version_mode"
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        )

    if not drift.get("ok"):
        report.add(
            CheckResult(
                name="compatibility_matrix_anchor_drift",
                severity=Severity.WARN.value,
                message="Compatibility matrix anchor drift detected",
                detail=json.dumps(drift.get("drift", []), ensure_ascii=False),
                remediation=(
                    "Refresh matrix anchors and publish updated evidence before release."
                ),
            )
        )


def check_state_dir(report: DoctorReport) -> None:
    """Check state directory accessibility."""
    # IMPORTANT: canonical must be evaluated before the legacy alias. Reversing this order makes
    # doctor inspect a different directory than runtime when both variables are configured.
    state_dir = get_env_value(
        "OPENCLAW_STATE_DIR",
        mode=EnvLookupMode.NONEMPTY,
    )
    if not state_dir:
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.PASS.value,
                message="Using default state directory (user data dir)",
            )
        )
        return

    p = Path(state_dir)
    if not p.exists():
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.WARN.value,
                message=f"State dir does not exist: {state_dir}",
                remediation="The directory will be created on first run.",
            )
        )
    elif not os.access(str(p), os.W_OK):
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.FAIL.value,
                message=f"State dir not writable: {state_dir}",
                remediation="Check file permissions.",
            )
        )
    else:
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.PASS.value,
                message=f"State dir OK: {state_dir}",
            )
        )


def check_token_posture(report: DoctorReport) -> None:
    """Check admin/observability token configuration."""
    admin_token = get_env_value("OPENCLAW_ADMIN_TOKEN")
    obs_token = get_env_value("OPENCLAW_OBSERVABILITY_TOKEN")

    if admin_token:
        report.add(
            CheckResult(
                name="admin_token",
                severity=Severity.PASS.value,
                message="Admin token configured",
            )
        )
    else:
        report.add(
            CheckResult(
                name="admin_token",
                severity=Severity.WARN.value,
                message="No admin token — loopback-only convenience mode",
                detail="Remote admin access is denied by default.",
            )
        )

    if obs_token:
        report.add(
            CheckResult(
                name="observability_token",
                severity=Severity.PASS.value,
                message="Observability token configured",
            )
        )
    else:
        report.add(
            CheckResult(
                name="observability_token",
                severity=Severity.WARN.value,
                message="No observability token — loopback-only",
            )
        )


def check_pre_commit(report: DoctorReport) -> None:
    """Check pre-commit availability."""
    pre_commit = shutil.which("pre-commit")
    if pre_commit:
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.PASS.value,
                message="pre-commit found on PATH",
            )
        )
        return

    # Try as Python module
    try:
        importlib.import_module("pre_commit")
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.PASS.value,
                message="pre-commit available as Python module",
            )
        )
    except ImportError:
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.WARN.value,
                message="pre-commit not found",
                remediation="pip install pre-commit (required for SOP validation).",
            )
        )


def check_core_imports(report: DoctorReport) -> None:
    """Verify core service modules can be imported."""
    modules = [
        "services.runtime_config",
        "services.capabilities",
        "services.webhook_auth",
        "services.templates",
        "services.llm_client",
        "services.metrics",
    ]
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            report.add(
                CheckResult(
                    name=f"import:{mod_name}",
                    severity=Severity.PASS.value,
                    message=f"OK: {mod_name}",
                )
            )
        except Exception as e:
            report.add(
                CheckResult(
                    name=f"import:{mod_name}",
                    severity=Severity.FAIL.value,
                    message=f"Import failed: {mod_name}",
                    detail=str(e),
                    remediation=f"Check for missing dependencies or circular imports.",
                )
            )


# ---------------------------------------------------------------------------
# R91: Runtime Provenance v2 — structured schema
# ---------------------------------------------------------------------------

_PROVENANCE_SOURCES = ("system", "venv", "conda", "manager", "shim")
_PROVENANCE_STATUSES = ("ok", "version_low", "not_found", "shim_drift")


@dataclass
class RuntimeProvenance:
    """Deterministic provenance record for a single runtime executable."""

    runtime: str  # "python" | "node" | "pip"
    executable: str  # actual path used
    path_executable: str  # PATH-resolved path
    version: str  # version string or ""
    source: str  # one of _PROVENANCE_SOURCES
    status: str  # one of _PROVENANCE_STATUSES
    managers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _detect_python_source() -> str:
    """Classify Python executable source."""
    sys_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        return "venv"
    if os.environ.get("CONDA_PREFIX"):
        return "conda"
    path_exe = shutil.which("python")
    if (
        platform.system() == "Windows"
        and path_exe
        and "WindowsApps" in str(path_exe)
        and "WindowsApps" not in sys_exe
    ):
        return "shim"
    for mgr in ("MISE_DATA_DIR", "MISE_CONFIG_DIR", "PYENV_ROOT"):
        if os.environ.get(mgr):
            return "manager"
    return "system"


def _detect_node_source() -> str:
    """Classify Node.js executable source."""
    for env, label in (
        ("NVM_DIR", "manager"),
        ("FNM_DIR", "manager"),
        ("MISE_DATA_DIR", "manager"),
    ):
        if os.environ.get(env):
            return label
    return "system"


def _detect_managers() -> List[str]:
    """Detect active environment managers."""
    managers: List[str] = []
    if os.environ.get("NVM_DIR"):
        managers.append("nvm")
    if os.environ.get("FNM_DIR"):
        managers.append("fnm")
    if os.environ.get("MISE_DATA_DIR") or os.environ.get("MISE_CONFIG_DIR"):
        managers.append("mise")
    if os.environ.get("CONDA_PREFIX"):
        managers.append("conda")
    if os.environ.get("PYENV_ROOT"):
        managers.append("pyenv")
    return managers


def check_runtime_provenance(report: DoctorReport) -> None:
    """
    R91: Runtime provenance v2 — deterministic provenance for Python/Node/Pip.
    Emits structured RuntimeProvenance records + backward-compatible check results.
    """
    managers = _detect_managers()
    provenance_records: List[Dict[str, Any]] = []

    # --- Python ---
    sys_exe = sys.executable
    path_exe = shutil.which("python") or ""
    py_ver = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    py_source = _detect_python_source()
    py_status = "shim_drift" if py_source == "shim" else "ok"

    py_prov = RuntimeProvenance(
        runtime="python",
        executable=sys_exe,
        path_executable=str(path_exe),
        version=py_ver,
        source=py_source,
        status=py_status,
        managers=[m for m in managers if m in ("conda", "pyenv", "mise")],
    )
    provenance_records.append(py_prov.to_dict())

    # Backward-compatible environment keys
    report.environment["sys_executable"] = sys_exe
    report.environment["path_python"] = str(path_exe)

    if py_status == "shim_drift":
        report.add(
            CheckResult(
                name="python_shim_drift",
                severity=Severity.WARN.value,
                message="Python token on PATH is a Windows Store shim",
                detail=f"PATH points to: {path_exe}\nRunning as: {sys_exe}",
                remediation="Adjust PATH to prioritize your installed Python 'Scripts' directory.",
            )
        )

    # --- Node ---
    node_exe = shutil.which("node") or ""
    node_ver = ""
    node_status = "not_found"
    if node_exe:
        try:
            node_ver = subprocess.check_output(
                [node_exe, "--version"], text=True, timeout=5
            ).strip()
            major = int(node_ver.lstrip("v").split(".")[0])
            node_status = "ok" if major >= 18 else "version_low"
        except Exception:
            node_status = "not_found"
    node_source = _detect_node_source() if node_exe else "system"

    node_prov = RuntimeProvenance(
        runtime="node",
        executable=node_exe,
        path_executable=node_exe,
        version=node_ver,
        source=node_source,
        status=node_status,
        managers=[m for m in managers if m in ("nvm", "fnm", "mise")],
    )
    provenance_records.append(node_prov.to_dict())

    # --- Pip ---
    pip_exe = shutil.which("pip") or ""
    pip_ver = ""
    pip_status = "not_found"
    if pip_exe:
        try:
            pip_out = subprocess.check_output(
                [pip_exe, "--version"], text=True, timeout=5
            ).strip()
            pip_ver = pip_out.split()[1] if " " in pip_out else pip_out
            pip_status = "ok"
            report.environment["pip_version"] = pip_out
        except Exception:
            pip_status = "not_found"

    pip_prov = RuntimeProvenance(
        runtime="pip",
        executable=pip_exe,
        path_executable=pip_exe,
        version=pip_ver,
        source=py_source,  # pip inherits Python source
        status=pip_status,
    )
    provenance_records.append(pip_prov.to_dict())

    # Backward-compat check results for pip
    if pip_status == "ok":
        report.add(
            CheckResult(
                name="pip_provenance",
                severity=Severity.PASS.value,
                message=f"Pip found: {pip_ver}",
            )
        )
    elif pip_exe:
        report.add(
            CheckResult(
                name="pip_provenance",
                severity=Severity.WARN.value,
                message="Pip found but failed to run",
            )
        )
    else:
        report.add(
            CheckResult(
                name="pip_provenance",
                severity=Severity.WARN.value,
                message="Pip not found on PATH",
            )
        )

    # --- Managers ---
    if managers:
        report.environment["env_managers"] = ", ".join(managers)
        report.add(
            CheckResult(
                name="env_manager_detected",
                severity=Severity.INFO.value,
                message=f"Environment manager(s) active: {', '.join(managers)}",
                detail="Ensure IDE/Terminal profiles share the same manager context.",
            )
        )

    # R91: Structured provenance output (machine-readable)
    report.environment["runtime_provenance"] = json.dumps(provenance_records)


def check_os_environment(report: DoctorReport) -> None:
    """Record OS environment info."""
    report.environment["os"] = platform.system()
    report.environment["os_version"] = platform.version()
    report.environment["arch"] = platform.machine()

    if platform.system() == "Windows":
        # Check for common Windows pitfalls
        long_path = os.environ.get("MSYS_NO_PATHCONV")
        report.add(
            CheckResult(
                name="windows_env",
                severity=Severity.PASS.value,
                message="Windows environment detected",
                detail=f"Architecture: {platform.machine()}",
            )
        )


def check_permissions(report: DoctorReport) -> None:
    """
    S42: Check permission posture (state dir, secrets).
    """
    try:
        from .permission_posture import (
            PermissionEvaluator,
            PermissionSeverity,
            evaluate_startup_permissions,
        )

        evaluator = PermissionEvaluator()
        results = evaluator.evaluate()

        for res in results:
            severity = Severity.PASS
            if res.severity == PermissionSeverity.WARN:
                severity = Severity.WARN
            elif res.severity == PermissionSeverity.FAIL:
                severity = Severity.FAIL
            elif res.severity == PermissionSeverity.SKIP:
                severity = Severity.SKIP

            report.add(
                CheckResult(
                    name=res.code,
                    severity=severity.value,
                    message=res.message,
                    remediation=res.remediation,
                )
            )
    except ImportError:
        report.add(
            CheckResult(
                name="permission_evaluator_import",
                severity=Severity.WARN.value,
                message="Could not import permission evaluator",
            )
        )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_doctor(pack_root: Optional[Path] = None) -> DoctorReport:
    """Run all diagnostic checks and return a report."""
    report = DoctorReport()

    if pack_root is None:
        pack_root = _get_pack_root()

    report.environment["pack_root"] = str(pack_root)

    # Run checks
    check_os_environment(report)
    check_runtime_provenance(report)  # R86
    check_permissions(report)  # S42
    check_python_version(report)
    check_node_version(report)
    check_venv(report)
    check_pre_commit(report)
    check_state_dir(report)
    check_token_posture(report)
    check_contract_files(report, pack_root)
    check_compatibility_matrix_governance(report, pack_root)  # R90
    check_core_imports(report)

    report.build_summary()
    return report


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for operator doctor."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenClaw Operator Doctor — deployment readiness diagnostics"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )
    parser.add_argument(
        "--pack-root",
        type=str,
        default=None,
        help="Override pack root directory detection",
    )
    args = parser.parse_args()

    pack_root = Path(args.pack_root) if args.pack_root else None
    report = run_doctor(pack_root)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_human())

    sys.exit(1 if report.has_failures else 0)


if __name__ == "__main__":
    main()
