#!/usr/bin/env python3
"""
R156: verify coverage + mutation governance baseline configuration.

This script is intentionally stdlib-only so it can run early in local/full-test
gates before any optional tooling is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from quality_governance_common import (
    MIN_PROMOTION_REVIEW_CYCLES,
    current_stage_threshold,
    load_and_validate_policy,
    load_and_validate_review_evidence,
    next_stage,
)

MIN_COVERAGE_FAIL_UNDER = 35.0
SMOKE_MUTATION_THRESHOLD = 20.0
EXTENDED_MUTATION_THRESHOLD = 80.0


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _extract_toml_section(text: str, header: str) -> str | None:
    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\]\s*$\n(?P<body>.*?)(?=^\[|\Z)")
    match = pattern.search(text)
    if not match:
        return None
    return match.group("body")


def _extract_float_assignment(section_text: str, key: str) -> float | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        section_text,
    )
    if not match:
        return None
    return float(match.group(1))


def _extract_bool_assignment(section_text: str, key: str) -> bool | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(true|false)\s*$", section_text)
    if not match:
        return None
    return match.group(1) == "true"


def _extract_python_constant(text: str, name: str) -> float | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$", text
    )
    if not match:
        return None
    return float(match.group(1))


def _require_phrase(text: str, phrase: str, failures: list[str], label: str) -> None:
    if phrase not in text:
        failures.append(f"{label}: missing required phrase: {phrase}")


def verify_governance(
    *,
    pyproject_path: Path,
    adversarial_gate_path: Path,
    test_sop_path: Path,
    survivor_allowlist_path: Path,
    coverage_policy_path: Path,
    coverage_review_evidence_path: Path,
    release_policy_doc_path: Path,
) -> list[str]:
    failures: list[str] = []
    policy_payload, policy_failures = load_and_validate_policy(coverage_policy_path)
    failures.extend(policy_failures)
    review_payload = None
    if policy_payload is not None:
        review_payload, review_failures = load_and_validate_review_evidence(
            coverage_review_evidence_path,
            policy=policy_payload,
        )
        failures.extend(review_failures)

        for entry in policy_payload.get("exceptions", []):
            review_by = entry.get("review_by")
            entry_id = entry.get("id", "<unknown>")
            if isinstance(review_by, str):
                if date.fromisoformat(review_by) < date.today():
                    failures.append(
                        "coverage policy: stale exception review date for "
                        f"{entry_id}: {review_by}"
                    )

        stages = policy_payload.get("stages", [])
        current_stage = policy_payload.get("current_stage")
        if review_payload is not None and isinstance(stages, list):
            stage_ids = [stage.get("id") for stage in stages if isinstance(stage, dict)]
            if current_stage in stage_ids:
                current_index = stage_ids.index(current_stage)
                if current_index > 0:
                    previous_stage = stage_ids[current_index - 1]
                    reviews = review_payload.get("reviews", [])
                    previous_stage_reviews = [
                        review
                        for review in reviews
                        if isinstance(review, dict)
                        and review.get("stage_id") == previous_stage
                    ]
                    if len(previous_stage_reviews) < MIN_PROMOTION_REVIEW_CYCLES:
                        failures.append(
                            "coverage review evidence: promoted stage "
                            f"{current_stage} requires at least {MIN_PROMOTION_REVIEW_CYCLES} "
                            f"promotion review cycles for previous stage {previous_stage}"
                        )

    pyproject_text = _read_text(pyproject_path)
    report_section = _extract_toml_section(pyproject_text, "tool.coverage.report")
    if report_section is None:
        failures.append("pyproject: missing [tool.coverage.report] section")
    else:
        fail_under = _extract_float_assignment(report_section, "fail_under")
        if fail_under is None:
            failures.append("pyproject: missing coverage fail_under")
        elif fail_under < MIN_COVERAGE_FAIL_UNDER:
            failures.append(
                "pyproject: coverage fail_under "
                f"{fail_under} below minimum baseline {MIN_COVERAGE_FAIL_UNDER}"
            )
        elif policy_payload is not None:
            expected_fail_under = current_stage_threshold(policy_payload)
            if fail_under != expected_fail_under:
                failures.append(
                    "pyproject: coverage fail_under "
                    f"{fail_under} does not match policy current-stage floor {expected_fail_under}"
                )

        show_missing = _extract_bool_assignment(report_section, "show_missing")
        if show_missing is not True:
            failures.append("pyproject: coverage show_missing must be true")

        skip_covered = _extract_bool_assignment(report_section, "skip_covered")
        if skip_covered is not True:
            failures.append("pyproject: coverage skip_covered must be true")

    gate_text = _read_text(adversarial_gate_path)
    smoke_threshold = _extract_python_constant(gate_text, "SMOKE_MUTATION_THRESHOLD")
    if smoke_threshold != SMOKE_MUTATION_THRESHOLD:
        failures.append(
            "adversarial gate: smoke mutation threshold drifted "
            f"(expected {SMOKE_MUTATION_THRESHOLD}, got {smoke_threshold})"
        )

    extended_threshold = _extract_python_constant(
        gate_text, "EXTENDED_MUTATION_THRESHOLD"
    )
    if extended_threshold != EXTENDED_MUTATION_THRESHOLD:
        failures.append(
            "adversarial gate: extended mutation threshold drifted "
            f"(expected {EXTENDED_MUTATION_THRESHOLD}, got {extended_threshold})"
        )

    test_sop_text = _read_text(test_sop_path)
    _require_phrase(
        test_sop_text,
        "R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)",
        failures,
        "tests/TEST_SOP.md",
    )
    _require_phrase(
        test_sop_text,
        "global score threshold (`>= 80%` unless explicitly overridden)",
        failures,
        "tests/TEST_SOP.md",
    )
    _require_phrase(
        test_sop_text,
        "coverage governance check (`scripts/verify_quality_governance.py`)",
        failures,
        "tests/TEST_SOP.md",
    )
    _require_phrase(
        test_sop_text,
        "staged coverage ratchet policy (`tests/coverage_governance_policy.json`)",
        failures,
        "tests/TEST_SOP.md",
    )

    if not survivor_allowlist_path.is_file():
        failures.append(
            "mutation governance: missing tests/mutation_survivor_allowlist.json"
        )
    else:
        try:
            payload = json.loads(_read_text(survivor_allowlist_path))
        except json.JSONDecodeError as exc:
            failures.append(
                f"mutation governance: invalid survivor allowlist JSON: {exc}"
            )
        else:
            if not isinstance(payload, dict) or not isinstance(
                payload.get("entries", []), list
            ):
                failures.append(
                    "mutation governance: survivor allowlist must be an object with an entries list"
                )

    release_policy_text = _read_text(release_policy_doc_path)
    _require_phrase(
        release_policy_text,
        "staged coverage ratchet policy (`tests/coverage_governance_policy.json`)",
        failures,
        "docs/release/ci_regression_policy.md",
    )
    _require_phrase(
        release_policy_text,
        "`fail_under` must match the current stage floor declared in `tests/coverage_governance_policy.json`",
        failures,
        "docs/release/ci_regression_policy.md",
    )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify repository coverage and mutation governance baselines."
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml",
    )
    parser.add_argument(
        "--adversarial-gate",
        default="scripts/run_adversarial_gate.py",
        help="Path to the adversarial gate runner",
    )
    parser.add_argument(
        "--test-sop",
        default="tests/TEST_SOP.md",
        help="Path to the main test SOP",
    )
    parser.add_argument(
        "--mutation-survivor-allowlist",
        default="tests/mutation_survivor_allowlist.json",
        help="Path to the mutation survivor allowlist JSON",
    )
    parser.add_argument(
        "--coverage-policy",
        default="tests/coverage_governance_policy.json",
        help="Path to the staged coverage governance policy JSON.",
    )
    parser.add_argument(
        "--coverage-review-evidence",
        default="tests/coverage_promotion_reviews.json",
        help="Path to the retained coverage promotion review evidence JSON.",
    )
    parser.add_argument(
        "--release-policy-doc",
        default="docs/release/ci_regression_policy.md",
        help="Path to the release/CI regression policy document.",
    )
    args = parser.parse_args()

    failures = verify_governance(
        pyproject_path=Path(args.pyproject),
        adversarial_gate_path=Path(args.adversarial_gate),
        test_sop_path=Path(args.test_sop),
        survivor_allowlist_path=Path(args.mutation_survivor_allowlist),
        coverage_policy_path=Path(args.coverage_policy),
        coverage_review_evidence_path=Path(args.coverage_review_evidence),
        release_policy_doc_path=Path(args.release_policy_doc),
    )
    if failures:
        for failure in failures:
            print(f"GOVERNANCE-FAIL: {failure}")
        return 1

    policy_payload, _ = load_and_validate_policy(Path(args.coverage_policy))
    current_stage = policy_payload["current_stage"]
    next_policy_stage = next_stage(policy_payload)
    print(
        "GOVERNANCE-PASS: coverage fail_under/show_missing/skip_covered, "
        f"staged policy ({current_stage} -> {next_policy_stage['id'] if next_policy_stage else 'none'}), "
        "and mutation thresholds are aligned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
