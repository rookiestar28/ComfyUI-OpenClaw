"""
R118 -- Adversarial Gate Runner.

Unified entry point for adversarial verification suites:
- R111: Fuzz / property-based testing (bounded, deterministic when seeded)
- R113: Mutation testing with kill-rate threshold

Supports two profiles:
- ``smoke``: Fast, bounded, deterministic -- required on PR/push CI.
- ``extended``: Deeper coverage -- nightly/manual dispatch.
- ``auto``: Diff-aware selector; escalates to ``extended`` on high-risk path changes.

Usage:
    python scripts/run_adversarial_gate.py --profile smoke
    python scripts/run_adversarial_gate.py --profile extended --seed 42
    python scripts/run_adversarial_gate.py --profile auto --artifact-dir .tmp/adversarial

CRITICAL: keep fuzz seed/runner deterministic and bounded.
IMPORTANT: do not downgrade mutation threshold to report-only unless explicitly
           approved in roadmap.
"""

import argparse
import fnmatch
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_HIGH_RISK_PATTERNS = [
    "services/access_control.py",
    "services/tenant_context.py",
    "api/routes.py",
    # CRITICAL: keep route-bootstrap owners exact; broad service globs over-escalate CI.
    "__init__.py",
    "services/route_bootstrap.py",
    "services/bootstrap/registration.py",
    "services/route_bootstrap_contract.py",
    "services/import_fallback.py",
    "services/security_*.py",
    "services/startup_profile_gate.py",
    "services/control_plane.py",
    "services/endpoint_manifest.py",
    "services/webhook_auth.py",
    "services/safe_io.py",
]
DEFAULT_MUTATION_ALLOWLIST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "mutation_survivor_allowlist.json"
)
SMOKE_MUTATION_THRESHOLD = 20.0
EXTENDED_MUTATION_THRESHOLD = 80.0


def _normalize_rel_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return pathlib.PurePosixPath(normalized).as_posix()


def _run_git_diff(base: Optional[str], head: Optional[str]) -> List[str]:
    if not shutil.which("git"):
        return []
    cmd: List[str]
    if base and head:
        # Prefer merge-base-aware comparison for branch-based refs.
        cmd = ["git", "diff", "--name-only", f"{base}...{head}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            cmd = ["git", "diff", "--name-only", base, head]
            result = subprocess.run(cmd, capture_output=True, text=True)
    elif base:
        cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            cmd = ["git", "diff", "--name-only", base, "HEAD"]
            result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        # Include uncommitted changes first so local pre-push/full-test runs
        # can escalate to extended before commit.
        cmd = ["git", "diff", "--name-only", "HEAD"]
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    files = [_normalize_rel_path(line) for line in result.stdout.splitlines() if line]
    return sorted(set(files))


def _collect_changed_files(
    diff_base: Optional[str], diff_head: Optional[str]
) -> Tuple[List[str], str]:
    files = _run_git_diff(diff_base, diff_head)
    if files:
        if diff_base and diff_head:
            return files, f"git diff {diff_base}...{diff_head}"
        if diff_base:
            return files, f"git diff {diff_base}...HEAD"
        return files, "git diff HEAD (working tree)"

    # Fallback for shallow/no-diff contexts.
    if shutil.which("git"):
        mb = subprocess.run(
            ["git", "merge-base", "origin/main", "HEAD"],
            capture_output=True,
            text=True,
        )
        if mb.returncode == 0 and mb.stdout.strip():
            files = _run_git_diff(mb.stdout.strip(), "HEAD")
            if files:
                return files, "git diff $(merge-base origin/main HEAD)...HEAD"

        files = _run_git_diff("HEAD~1", "HEAD")
        if files:
            return files, "git diff HEAD~1...HEAD"

    return [], "no git diff context"


def _filter_high_risk_files(changed_files: List[str], patterns: List[str]) -> List[str]:
    matched: Set[str] = set()
    normalized_patterns = [_normalize_rel_path(p) for p in patterns if p.strip()]
    for file_path in changed_files:
        normalized_file = _normalize_rel_path(file_path)
        for pattern in normalized_patterns:
            if fnmatch.fnmatch(normalized_file, pattern):
                matched.add(normalized_file)
                break
    return sorted(matched)


def _resolve_effective_profile(
    requested_profile: str,
    diff_base: Optional[str],
    diff_head: Optional[str],
    high_risk_patterns: List[str],
) -> Tuple[str, List[str], List[str], str]:
    if requested_profile != "auto":
        return requested_profile, [], [], "explicit profile"

    changed_files, diff_source = _collect_changed_files(diff_base, diff_head)
    high_risk_changed = _filter_high_risk_files(changed_files, high_risk_patterns)
    if high_risk_changed:
        return "extended", changed_files, high_risk_changed, diff_source
    return "smoke", changed_files, high_risk_changed, diff_source


def _load_survivor_allowlist(path: str) -> Set[Tuple[str, int]]:
    if not path or not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries", []) if isinstance(data, dict) else []
        allowlist: Set[Tuple[str, int]] = set()
        for entry in entries:
            file_path = _normalize_rel_path(str(entry.get("file", "")))
            mutation_index = entry.get("mutation_index")
            if file_path and isinstance(mutation_index, int):
                allowlist.add((file_path, mutation_index))
        return allowlist
    except Exception:
        return set()


def run_fuzz_suite(seed: int, max_runs: int, artifact_dir: str) -> Dict[str, Any]:
    """
    Run R111 fuzz harness with deterministic seed and bounded iteration.

    Returns:
        Result dict with pass/fail, crash count, seed, and artifact paths.
    """
    # Set global seed for reproducibility
    random.seed(seed)

    # Import fuzz suite from tests
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

    from test_r111_fuzz_harness import (  # type: ignore
        Fuzzer,
        FuzzStrategies,
        fuzz_is_loopback,
        fuzz_policy_bundle,
        fuzz_url_validation,
    )

    os.makedirs(artifact_dir, exist_ok=True)

    # Override the default artifact dir
    import test_r111_fuzz_harness

    test_r111_fuzz_harness.ARTIFACT_DIR = artifact_dir

    fuzzer = Fuzzer()

    import string
    from unittest.mock import patch

    from test_r111_fuzz_harness import _deterministic_getaddrinfo

    # 1. URL validation fuzz
    def url_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        return "http://" + FuzzStrategies.random_string(
            1, 20, string.ascii_letters + ".:/"
        )

    fuzzer.fuzz_target(
        "validate_outbound_url", fuzz_url_validation, url_gen, max_runs=max_runs
    )

    # 2. Policy bundle fuzz
    def bundle_gen():
        return FuzzStrategies.random_json(depth=3)

    fuzzer.fuzz_target(
        "PolicyBundle.from_dict", fuzz_policy_bundle, bundle_gen, max_runs=max_runs
    )

    # 3. Loopback fuzz
    def ip_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        return ".".join(str(random.randint(0, 300)) for _ in range(4))

    fuzzer.fuzz_target("is_loopback", fuzz_is_loopback, ip_gen, max_runs=max_runs)

    # 4. Path normalization
    from services.safe_io import PathTraversalError, resolve_under_root

    def path_gen():
        parts = ["foo", "..", "bar", "//", "\\", "C:", "/etc/passwd", "~", "."]
        return os.path.join(
            *[random.choice(parts) for _ in range(random.randint(1, 5))]
        )

    def fuzz_resolve(inp):
        try:
            # Use a temp dir as root
            resolve_under_root(os.path.join(artifact_dir, "_safe_root"), inp)
        except (PathTraversalError, ValueError):
            pass

    fuzzer.fuzz_target("resolve_under_root", fuzz_resolve, path_gen, max_runs=max_runs)

    return {
        "suite": "r111_fuzz",
        "seed": seed,
        "max_runs_per_target": max_runs,
        "total_crashes": len(fuzzer.crashes),
        "crash_artifacts": fuzzer.crashes,
        "passed": len(fuzzer.crashes) == 0,
    }


def run_mutation_suite(
    threshold: float,
    artifact_dir: str,
    *,
    strict_zero_survivor_files: Optional[List[str]] = None,
    survivor_allowlist: Optional[Set[Tuple[str, int]]] = None,
) -> Dict[str, Any]:
    """
    Run R113 mutation test with kill-rate threshold enforcement.

    Returns:
        Result dict with score, pass/fail, and report path.
    """
    script = os.path.join(os.path.dirname(__file__), "run_mutation_test.py")

    if not os.path.isfile(script):
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": f"Mutation test script not found: {script}",
            "score": 0.0,
            "threshold": threshold,
        }

    try:
        report_path = os.path.join(
            os.path.dirname(__file__), "..", ".planning", "mutation_report.json"
        )
        # IMPORTANT: remove stale report before each run to avoid parsing
        # previous results when the current mutation subprocess fails early.
        if os.path.isfile(report_path):
            os.remove(report_path)

        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        # Parse report
        score = 0.0
        report_data: Dict[str, Any] = {}

        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_data = json.load(f)
            total = report_data.get("total_mutants", 0)
            killed = report_data.get("killed", 0)
            if total > 0:
                score = (killed / total) * 100.0
        else:
            # Fallback: parse score from runner log when report is missing.
            combined = f"{result.stdout or ''}\n{result.stderr or ''}"
            m = re.search(r"Mutation Score:\s*([0-9]+(?:\.[0-9]+)?)%", combined)
            if m:
                score = float(m.group(1))

        passed = score >= threshold

        error = None
        # CRITICAL: treat mutation subprocess return code as authoritative.
        # If mutation runner exits non-zero, this suite must fail even if a stale
        # score could be parsed from logs.
        if result.returncode != 0:
            error = (
                "mutation subprocess failed "
                f"(rc={result.returncode}); "
                "see stdout_tail/stderr_tail for details"
            )
            passed = False
        elif not report_data:
            # IMPORTANT: this indicates diagnostic degradation (e.g., runner did
            # not emit report). Keep it visible in manifest for CI triage.
            error = "mutation report missing; used score fallback from process output"

        strict_targets = sorted(
            {
                _normalize_rel_path(p)
                for p in (strict_zero_survivor_files or [])
                if str(p).strip()
            }
        )
        allowlist = survivor_allowlist or set()
        raw_details = (
            report_data.get("details", []) if isinstance(report_data, dict) else []
        )
        surviving_details: List[Dict[str, Any]] = []
        for detail in raw_details:
            if isinstance(detail, dict) and detail.get("status") == "SURVIVED":
                surviving_details.append(detail)

        strict_violations: List[Dict[str, Any]] = []
        allowlisted_survivors: List[Dict[str, Any]] = []
        if strict_targets:
            strict_set = set(strict_targets)
            for detail in surviving_details:
                file_path = _normalize_rel_path(str(detail.get("file", "")))
                mutation_index = detail.get("mutation_index")
                key = (
                    file_path,
                    mutation_index if isinstance(mutation_index, int) else -1,
                )
                if file_path not in strict_set:
                    continue
                if key in allowlist:
                    allowlisted_survivors.append(detail)
                else:
                    strict_violations.append(detail)
            if strict_violations:
                passed = False
                strict_err = (
                    "strict zero-survivor violation on high-risk changed files: "
                    f"{len(strict_violations)} non-allowlisted survivor(s)"
                )
                error = f"{error}; {strict_err}" if error else strict_err

        return {
            "suite": "r113_mutation",
            "score": round(score, 2),
            "threshold": threshold,
            "total_mutants": report_data.get("total_mutants", 0),
            "killed": report_data.get("killed", 0),
            "survived": report_data.get("survived", 0),
            "passed": passed,
            "report_path": report_path if os.path.isfile(report_path) else None,
            "stdout_tail": result.stdout[-500:] if result.stdout else "",
            "stderr_tail": result.stderr[-500:] if result.stderr else "",
            "returncode": result.returncode,
            "error": error,
            "strict_zero_survivor_files": strict_targets,
            "strict_survivor_violations": strict_violations,
            "allowlisted_survivors": allowlisted_survivors,
        }

    except subprocess.TimeoutExpired:
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": "Mutation test timed out (300s)",
            "score": 0.0,
            "threshold": threshold,
        }
    except Exception as e:
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": str(e),
            "score": 0.0,
            "threshold": threshold,
        }


def build_manifest(
    requested_profile: str,
    effective_profile: str,
    seed: int,
    fuzz_result: Dict[str, Any],
    mutation_result: Dict[str, Any],
    artifact_dir: str,
    elapsed_sec: float,
    changed_files: Optional[List[str]] = None,
    high_risk_changed_files: Optional[List[str]] = None,
    diff_source: str = "",
) -> Dict[str, Any]:
    """Build machine-readable JSON manifest for CI artifact upload."""
    overall_passed = fuzz_result["passed"] and mutation_result["passed"]

    manifest = {
        "r118_version": "1.0",
        "profile_requested": requested_profile,
        "profile": effective_profile,
        "seed": seed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_sec": round(elapsed_sec, 2),
        "decision": "PASS" if overall_passed else "FAIL",
        "selection": {
            "diff_source": diff_source,
            "changed_files": changed_files or [],
            "high_risk_changed_files": high_risk_changed_files or [],
        },
        "suites": {
            "r111_fuzz": fuzz_result,
            "r113_mutation": mutation_result,
        },
        "artifact_dir": os.path.abspath(artifact_dir),
        "replay_command": (
            f"python scripts/run_adversarial_gate.py "
            f"--profile {effective_profile} --seed {seed} "
            f"--artifact-dir {artifact_dir}"
        ),
    }

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="R118 Adversarial Gate Runner")
    parser.add_argument(
        "--profile",
        choices=["smoke", "extended", "auto"],
        default="smoke",
        help=(
            "Execution profile: smoke, extended, or auto. "
            "auto escalates to extended when high-risk paths are changed."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic replay (default: random)",
    )
    parser.add_argument(
        "--artifact-dir",
        default=".tmp/adversarial",
        help="Directory for crash artifacts and manifests",
    )
    parser.add_argument(
        "--mutation-threshold",
        type=float,
        default=None,
        help="Mutation kill-rate threshold %% (overrides profile default)",
    )
    parser.add_argument(
        "--diff-base",
        default=os.environ.get("OPENCLAW_DIFF_BASE"),
        help="Optional git diff base ref/sha for auto profile selection.",
    )
    parser.add_argument(
        "--diff-head",
        default=os.environ.get("OPENCLAW_DIFF_HEAD"),
        help="Optional git diff head ref/sha for auto profile selection.",
    )
    parser.add_argument(
        "--high-risk-pattern",
        action="append",
        default=None,
        help=(
            "Additional high-risk path pattern (glob). "
            "Can be provided multiple times."
        ),
    )
    parser.add_argument(
        "--mutation-survivor-allowlist",
        default=DEFAULT_MUTATION_ALLOWLIST_PATH,
        help=(
            "JSON allowlist for known equivalent mutation survivors used by "
            "strict zero-survivor enforcement."
        ),
    )
    parser.add_argument(
        "--no-enforce-zero-survivor-hotspots",
        action="store_true",
        help=(
            "Disable strict zero-survivor enforcement for changed high-risk files. "
            "Use only for explicit emergency diagnostics."
        ),
    )
    args = parser.parse_args()

    high_risk_patterns = list(DEFAULT_HIGH_RISK_PATTERNS)
    if args.high_risk_pattern:
        high_risk_patterns.extend(args.high_risk_pattern)

    effective_profile, changed_files, high_risk_changed_files, diff_source = (
        _resolve_effective_profile(
            args.profile,
            args.diff_base,
            args.diff_head,
            high_risk_patterns,
        )
    )

    # Profile defaults
    if effective_profile == "smoke":
        fuzz_max_runs = 200
        mutation_threshold = args.mutation_threshold or SMOKE_MUTATION_THRESHOLD
    else:  # extended
        fuzz_max_runs = 2000
        mutation_threshold = args.mutation_threshold or EXTENDED_MUTATION_THRESHOLD

    seed = args.seed if args.seed is not None else random.randint(0, 2**31)
    artifact_dir = os.path.abspath(args.artifact_dir)
    os.makedirs(artifact_dir, exist_ok=True)

    print(f"R118 Adversarial Gate -- profile={effective_profile}, seed={seed}")
    print(f"  Fuzz: {fuzz_max_runs} runs/target")
    print(f"  Mutation threshold: {mutation_threshold}%")
    print(f"  Artifacts: {artifact_dir}")
    print(
        f"  Profile selection: requested={args.profile}, "
        f"diff_source={diff_source}, high_risk_changes={len(high_risk_changed_files)}"
    )
    if high_risk_changed_files:
        print("  High-risk changed files:")
        for p in high_risk_changed_files:
            print(f"    - {p}")
    print("-" * 60)

    start = time.time()

    # Run fuzz suite
    print("\n[R111] Running fuzz suite...")
    fuzz_result = run_fuzz_suite(seed, fuzz_max_runs, artifact_dir)
    fuzz_status = "PASS" if fuzz_result["passed"] else "FAIL"
    print(f"[R111] {fuzz_status} -- {fuzz_result['total_crashes']} crashes")

    # Run mutation suite
    print("\n[R113] Running mutation suite...")
    strict_zero_survivor_files: List[str] = []
    if (
        not args.no_enforce_zero_survivor_hotspots
        and high_risk_changed_files
        and effective_profile == "extended"
    ):
        strict_zero_survivor_files = list(high_risk_changed_files)
    survivor_allowlist = _load_survivor_allowlist(args.mutation_survivor_allowlist)
    mutation_result = run_mutation_suite(
        mutation_threshold,
        artifact_dir,
        strict_zero_survivor_files=strict_zero_survivor_files,
        survivor_allowlist=survivor_allowlist,
    )
    mutation_status = "PASS" if mutation_result["passed"] else "FAIL"
    print(
        f"[R113] {mutation_status} -- "
        f"score={mutation_result.get('score', 0)}% "
        f"(threshold={mutation_threshold}%)"
    )

    elapsed = time.time() - start

    # Build and write manifest
    manifest = build_manifest(
        args.profile,
        effective_profile,
        seed,
        fuzz_result,
        mutation_result,
        artifact_dir,
        elapsed,
        changed_files=changed_files,
        high_risk_changed_files=high_risk_changed_files,
        diff_source=diff_source,
    )

    manifest_path = os.path.join(artifact_dir, "adversarial_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"R118 DECISION: {manifest['decision']}")
    print(f"Manifest: {manifest_path}")
    print(f"Elapsed: {elapsed:.1f}s")

    return 0 if manifest["decision"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
