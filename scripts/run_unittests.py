#!/usr/bin/env python3
"""
Run unit tests with numeric-only summary output.
Suppresses dot-progress noise while preserving failure tracebacks.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _build_suite(args: argparse.Namespace) -> unittest.TestSuite:
    if args.module:
        return unittest.defaultTestLoader.loadTestsFromName(args.module)
    return unittest.defaultTestLoader.discover(
        args.start_dir,
        pattern=args.pattern,
        top_level_dir=args.top_level_dir,
    )


def _load_skip_policy(path: str) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("skip policy must be a JSON object")
    max_skipped = data.get("max_skipped")
    no_skip_modules = data.get("no_skip_modules", [])
    if max_skipped is not None and (
        not isinstance(max_skipped, int) or max_skipped < 0
    ):
        raise ValueError("max_skipped must be a non-negative integer")
    if not isinstance(no_skip_modules, list) or any(
        not isinstance(item, str) or not item.strip() for item in no_skip_modules
    ):
        raise ValueError("no_skip_modules must be a list of non-empty strings")
    return {
        "max_skipped": max_skipped,
        "no_skip_modules": [item.strip() for item in no_skip_modules],
    }


def _collect_skips(result: unittest.TestResult) -> list[tuple[str, str]]:
    skips: list[tuple[str, str]] = []
    for test_case, reason in result.skipped:
        if hasattr(test_case, "id"):
            test_id = test_case.id()
        else:
            test_id = str(test_case)
        skips.append((test_id, str(reason)))
    return skips


def _evaluate_skip_policy(
    *,
    skips: Iterable[tuple[str, str]],
    max_skipped: int | None,
    no_skip_modules: Iterable[str],
) -> list[str]:
    violations: list[str] = []
    skip_list = list(skips)
    if max_skipped is not None and len(skip_list) > max_skipped:
        violations.append(
            f"skip budget exceeded: skipped={len(skip_list)} > max_skipped={max_skipped}"
        )

    prefixes = [p.strip() for p in no_skip_modules if p and p.strip()]
    for test_id, reason in skip_list:
        for prefix in prefixes:
            if test_id.startswith(prefix):
                violations.append(
                    f"no-skip suite skipped: {test_id} (reason={reason}, prefix={prefix})"
                )
                break
    return violations


def _write_skip_report(
    path: str,
    *,
    tests_run: int,
    duration_sec: float,
    skips: list[tuple[str, str]],
    max_skipped: int | None,
    no_skip_modules: list[str],
    violations: list[str],
) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tests_run": tests_run,
        "duration_sec": round(duration_sec, 3),
        "skipped_count": len(skips),
        "skipped": [
            {"test_id": test_id, "reason": reason} for test_id, reason in skips
        ],
        "policy": {
            "max_skipped": max_skipped,
            "no_skip_modules": no_skip_modules,
        },
        "violations": violations,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run unit tests quietly.")
    parser.add_argument(
        "--module",
        help="Optional test module name (e.g., tests.test_comfyui_loader_import).",
    )
    parser.add_argument(
        "--start-dir",
        default="tests",
        help="Start directory for discovery (default: tests).",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="Discovery pattern (default: test_*.py).",
    )
    parser.add_argument(
        "--top-level-dir",
        default=None,
        help="Top level dir for discovery (optional).",
    )
    parser.add_argument(
        "--enforce-skip-policy",
        default=None,
        help="Path to skip policy JSON (max_skipped + no_skip_modules).",
    )
    parser.add_argument(
        "--max-skipped",
        default=None,
        type=int,
        help="Override skip budget (non-negative integer).",
    )
    parser.add_argument(
        "--no-skip-module",
        action="append",
        default=[],
        help="Additional module prefix that must not be skipped (repeatable).",
    )
    parser.add_argument(
        "--skip-report",
        default=None,
        help="Optional JSON output path for skipped-test report.",
    )
    args = parser.parse_args()

    # Ensure repo root is on sys.path so imports like "services" and "connector" work.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    suite = _build_suite(args)

    # Capture default runner output to avoid dot/noise.
    sink = io.StringIO()
    runner = unittest.TextTestRunner(stream=sink, verbosity=1)

    start = time.time()
    result = runner.run(suite)
    duration = time.time() - start

    # Print failures/errors with full tracebacks.
    if result.failures or result.errors:
        for test, tb in result.failures:
            print(tb, file=sys.stdout)
        for test, tb in result.errors:
            print(tb, file=sys.stdout)

    policy_max_skipped: int | None = None
    policy_no_skip_modules: list[str] = []
    skip_policy_active = bool(
        args.enforce_skip_policy or args.max_skipped is not None or args.no_skip_module
    )
    if args.enforce_skip_policy:
        try:
            policy = _load_skip_policy(args.enforce_skip_policy)
        except Exception as exc:
            print(f"ERROR: failed to load skip policy: {exc}", file=sys.stdout)
            return 2
        policy_max_skipped = policy["max_skipped"]
        policy_no_skip_modules = policy["no_skip_modules"]

    if args.max_skipped is not None:
        if args.max_skipped < 0:
            print("ERROR: --max-skipped must be >= 0", file=sys.stdout)
            return 2
        policy_max_skipped = args.max_skipped

    policy_no_skip_modules = list(
        dict.fromkeys(policy_no_skip_modules + args.no_skip_module)
    )
    skips = _collect_skips(result)
    policy_violations: list[str] = []
    if skip_policy_active:
        # CRITICAL: this gate is opt-in by flag to avoid breaking ad-hoc local runs.
        policy_violations = _evaluate_skip_policy(
            skips=skips,
            max_skipped=policy_max_skipped,
            no_skip_modules=policy_no_skip_modules,
        )

    skip_report_path = args.skip_report
    if not skip_report_path and skip_policy_active:
        skip_report_path = str(repo_root / ".tmp" / "unit_skip_report.json")
    if skip_report_path:
        _write_skip_report(
            skip_report_path,
            tests_run=result.testsRun,
            duration_sec=duration,
            skips=skips,
            max_skipped=policy_max_skipped,
            no_skip_modules=policy_no_skip_modules,
            violations=policy_violations,
        )
        print(f"Skip report: {skip_report_path}", file=sys.stdout)

    summary = f"Ran {result.testsRun} tests in {duration:.3f}s"
    if result.wasSuccessful() and not policy_violations:
        if result.skipped:
            print(f"{summary}\nOK (skipped={len(result.skipped)})")
        else:
            print(f"{summary}\nOK")
        return 0

    if policy_violations:
        for line in policy_violations:
            print(f"SKIP-POLICY-FAIL: {line}", file=sys.stdout)

    print(
        f"{summary}\nFAILED (failures={len(result.failures)}, "
        f"errors={len(result.errors)}, skipped={len(result.skipped)}, "
        f"skip_policy_violations={len(policy_violations)})"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
