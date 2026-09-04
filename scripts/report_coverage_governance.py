#!/usr/bin/env python3
"""
R174: summarize staged coverage governance and hotspot-family coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from quality_governance_common import (
    load_and_validate_policy,
    read_json,
    summarize_coverage,
)


def _render_text(summary: dict[str, object]) -> str:
    policy = summary["policy"]
    overall = summary["overall"]
    hotspot_families = summary["hotspot_families"]
    lines = [
        "COVERAGE-GOVERNANCE-REPORT",
        f"current_stage={policy['current_stage']} fail_under={policy['current_stage_fail_under']}",
        f"next_stage={policy['next_stage']} next_fail_under={policy['next_stage_fail_under']}",
        f"overall={overall['percent_covered']} ({overall['covered_lines']}/{overall['num_statements']})",
        "hotspot_families:",
    ]
    for family_id, payload in hotspot_families.items():
        floor = payload["minimum_percent_covered"]
        floor_suffix = ""
        if floor is not None:
            floor_suffix = f" floor={floor} floor_met={payload['floor_met']}"
        lines.append(
            f"  - {family_id}: {payload['percent_covered']} "
            f"({payload['covered_lines']}/{payload['num_statements']}){floor_suffix}"
        )
        if payload["missing_paths"]:
            lines.append(f"    missing={', '.join(payload['missing_paths'])}")
    return "\n".join(lines) + "\n"


def _coverage_failures(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    hotspot_families = summary["hotspot_families"]
    for family_id, payload in hotspot_families.items():
        for path in payload["missing_paths"]:
            failures.append(f"hotspot family {family_id} missing coverage path: {path}")
        floor = payload["minimum_percent_covered"]
        if floor is not None and not payload["floor_met"]:
            failures.append(
                f"hotspot family {family_id} below coverage floor: "
                f"{payload['percent_covered']} < {floor}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report overall and hotspot-family coverage from a coverage JSON report."
    )
    parser.add_argument(
        "--coverage-policy",
        default="tests/coverage_governance_policy.json",
        help="Path to the staged coverage governance policy JSON.",
    )
    parser.add_argument(
        "--coverage-json",
        required=True,
        help="Path to a coverage.py JSON report (`coverage json -o <path>`).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    args = parser.parse_args()

    policy, failures = load_and_validate_policy(Path(args.coverage_policy))
    if failures:
        for failure in failures:
            print(f"REPORT-FAIL: {failure}")
        return 1

    coverage_payload = read_json(Path(args.coverage_json))
    summary = summarize_coverage(policy=policy, coverage_payload=coverage_payload)
    coverage_failures = _coverage_failures(summary)
    if args.format == "json":
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_render_text(summary))
    for failure in coverage_failures:
        print(f"REPORT-FAIL: {failure}", file=sys.stderr)
    return 1 if coverage_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
