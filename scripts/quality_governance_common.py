from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch
from itertools import pairwise
from pathlib import Path
from typing import Any, Iterable

REQUIRED_HOTSPOT_FAMILIES = (
    "safe_io",
    "security_boundary",
    "connector_config",
    "config_bootstrap",
)
MIN_PROMOTION_REVIEW_CYCLES = 2
RATCHET55_CRITICAL_FAMILIES = REQUIRED_HOTSPOT_FAMILIES
_FULL_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CoverageStage:
    stage_id: str
    min_fail_under: float


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_repo_path(raw: str) -> str:
    return raw.replace("\\", "/").lstrip("./")


def _validate_hotspot_family(
    family: dict[str, Any], seen_ids: set[str], failures: list[str]
) -> None:
    family_id = family.get("id")
    if not isinstance(family_id, str) or not family_id.strip():
        failures.append("coverage policy: hotspot family missing string id")
        return
    if family_id in seen_ids:
        failures.append(f"coverage policy: duplicate hotspot family id: {family_id}")
    seen_ids.add(family_id)

    paths = family.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and path.strip() for path in paths)
    ):
        failures.append(
            f"coverage policy: hotspot family {family_id} must define a non-empty paths list"
        )

    minimum_percent = family.get("minimum_percent_covered")
    if minimum_percent is not None and (
        isinstance(minimum_percent, bool)
        or not isinstance(minimum_percent, (int, float))
        or not 0.0 <= float(minimum_percent) <= 100.0
    ):
        failures.append(
            "coverage policy: hotspot family "
            f"{family_id} minimum_percent_covered must be a number in [0, 100]"
        )


def _validate_ratchet55_readiness(family: dict[str, Any], failures: list[str]) -> None:
    family_id = family["id"]
    readiness = family.get("ratchet55_readiness")
    if not isinstance(readiness, dict):
        failures.append(
            f"coverage policy: hotspot family {family_id} must include ratchet55_readiness metadata"
        )
        return

    for field_name in (
        "targeted_regression_suite",
        "ownership_status",
        "readiness_notes",
    ):
        value = readiness.get(field_name)
        if not isinstance(value, str) or not value.strip():
            failures.append(
                "coverage policy: hotspot family "
                f"{family_id} ratchet55_readiness missing {field_name}"
            )


def load_and_validate_policy(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    if not path.is_file():
        return None, [f"coverage policy: missing coverage governance policy: {path}"]

    try:
        payload = read_json(path)
    except json.JSONDecodeError as exc:
        return None, [f"coverage policy: invalid JSON: {exc}"]

    if not isinstance(payload, dict):
        return None, ["coverage policy: policy root must be an object"]

    schema_version = payload.get("schema_version")
    if schema_version != 1:
        failures.append(
            f"coverage policy: schema_version must be 1, got {schema_version!r}"
        )

    stages_raw = payload.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        failures.append("coverage policy: stages must be a non-empty list")
        return payload, failures

    stage_ids: set[str] = set()
    stages: list[CoverageStage] = []
    for raw_stage in stages_raw:
        if not isinstance(raw_stage, dict):
            failures.append("coverage policy: each stage must be an object")
            continue
        stage_id = raw_stage.get("id")
        min_fail_under = raw_stage.get("min_fail_under")
        if not isinstance(stage_id, str) or not stage_id.strip():
            failures.append("coverage policy: stage missing string id")
            continue
        if stage_id in stage_ids:
            failures.append(f"coverage policy: duplicate stage id: {stage_id}")
        stage_ids.add(stage_id)
        if not isinstance(min_fail_under, (int, float)):
            failures.append(
                f"coverage policy: stage {stage_id} missing numeric min_fail_under"
            )
            continue
        stages.append(
            CoverageStage(stage_id=stage_id, min_fail_under=float(min_fail_under))
        )

    for previous, current in pairwise(stages):
        if current.min_fail_under <= previous.min_fail_under:
            failures.append(
                "coverage policy: coverage stages must increase strictly by min_fail_under"
            )
            break

    current_stage = payload.get("current_stage")
    if not isinstance(current_stage, str) or current_stage not in stage_ids:
        failures.append(
            "coverage policy: current_stage must reference one declared stage id"
        )

    required_families = payload.get("required_hotspot_families")
    if not isinstance(required_families, list) or not all(
        isinstance(item, str) and item.strip() for item in required_families
    ):
        failures.append(
            "coverage policy: required_hotspot_families must be a list of strings"
        )
        required_families = []

    missing_required_defaults = sorted(
        set(REQUIRED_HOTSPOT_FAMILIES) - set(required_families)
    )
    if missing_required_defaults:
        failures.append(
            "coverage policy: missing required hotspot families: "
            + ", ".join(missing_required_defaults)
        )

    family_payload = payload.get("hotspot_families")
    if not isinstance(family_payload, list) or not family_payload:
        failures.append("coverage policy: hotspot_families must be a non-empty list")
        family_payload = []

    seen_family_ids: set[str] = set()
    for family in family_payload:
        if not isinstance(family, dict):
            failures.append("coverage policy: each hotspot family must be an object")
            continue
        _validate_hotspot_family(family, seen_family_ids, failures)

    policy_next_stage = None
    if current_stage in stage_ids:
        for index, raw_stage in enumerate(stages_raw):
            if raw_stage.get("id") == current_stage:
                if index + 1 < len(stages_raw):
                    policy_next_stage = stages_raw[index + 1].get("id")
                break
    if current_stage == "ratchet-55" or policy_next_stage == "ratchet-55":
        families_by_id = {
            family.get("id"): family
            for family in family_payload
            if isinstance(family, dict) and isinstance(family.get("id"), str)
        }
        for family_id in RATCHET55_CRITICAL_FAMILIES:
            family = families_by_id.get(family_id)
            if family is not None:
                _validate_ratchet55_readiness(family, failures)

    missing_declared_required = sorted(set(required_families) - seen_family_ids)
    if missing_declared_required:
        failures.append(
            "coverage policy: missing required hotspot families: "
            + ", ".join(missing_declared_required)
        )

    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        failures.append("coverage policy: exceptions must be a list")
        exceptions = []

    for entry in exceptions:
        if not isinstance(entry, dict):
            failures.append("coverage policy: each exception must be an object")
            continue
        entry_id = entry.get("id")
        family = entry.get("family")
        reason = entry.get("reason")
        review_by = entry.get("review_by")
        if not isinstance(entry_id, str) or not entry_id.strip():
            failures.append("coverage policy: exception missing string id")
        if not isinstance(family, str) or family not in seen_family_ids:
            failures.append(
                f"coverage policy: exception {entry_id!r} references unknown family"
            )
        if not isinstance(reason, str) or not reason.strip():
            failures.append(
                f"coverage policy: exception {entry_id!r} must include a non-empty reason"
            )
        if not isinstance(review_by, str):
            failures.append(
                f"coverage policy: exception {entry_id!r} must include review_by"
            )
            continue
        try:
            date.fromisoformat(review_by)
        except ValueError:
            failures.append(
                f"coverage policy: exception {entry_id!r} has invalid review_by date"
            )

    return payload, failures


def load_and_validate_review_evidence(
    path: Path, *, policy: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    if not path.is_file():
        return None, [f"coverage review evidence: missing review evidence file: {path}"]

    try:
        payload = read_json(path)
    except json.JSONDecodeError as exc:
        return None, [f"coverage review evidence: invalid JSON: {exc}"]

    if not isinstance(payload, dict):
        return None, ["coverage review evidence: root must be an object"]

    schema_version = payload.get("schema_version")
    if schema_version != 1:
        failures.append(
            f"coverage review evidence: schema_version must be 1, got {schema_version!r}"
        )

    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        failures.append("coverage review evidence: reviews must be a list")
        return payload, failures

    stage_ids = {stage["id"] for stage in policy.get("stages", [])}
    family_ids = {family["id"] for family in policy.get("hotspot_families", [])}
    seen_cycle_ids: set[str] = set()

    for entry in reviews:
        if not isinstance(entry, dict):
            failures.append("coverage review evidence: each review must be an object")
            continue
        cycle_id = entry.get("cycle_id")
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            failures.append("coverage review evidence: review missing string cycle_id")
        elif cycle_id in seen_cycle_ids:
            failures.append(
                f"coverage review evidence: duplicate review cycle_id: {cycle_id}"
            )
        else:
            seen_cycle_ids.add(cycle_id)

        stage_id = entry.get("stage_id")
        if not isinstance(stage_id, str) or stage_id not in stage_ids:
            failures.append(
                f"coverage review evidence: review {cycle_id!r} references unknown stage_id"
            )

        reviewed_at = entry.get("reviewed_at")
        if not isinstance(reviewed_at, str):
            failures.append(
                f"coverage review evidence: review {cycle_id!r} must include reviewed_at"
            )
        else:
            try:
                date.fromisoformat(reviewed_at)
            except ValueError:
                failures.append(
                    f"coverage review evidence: review {cycle_id!r} has invalid reviewed_at date"
                )

        overall_percent = entry.get("overall_percent_covered")
        if not isinstance(overall_percent, (int, float)):
            failures.append(
                f"coverage review evidence: review {cycle_id!r} missing numeric overall_percent_covered"
            )

        reviewed_families = entry.get("reviewed_hotspot_families")
        if not isinstance(reviewed_families, list) or not reviewed_families:
            failures.append(
                f"coverage review evidence: review {cycle_id!r} must include reviewed_hotspot_families"
            )
            reviewed_families = []
        elif any(
            not isinstance(family, str) or family not in family_ids
            for family in reviewed_families
        ):
            failures.append(
                f"coverage review evidence: review {cycle_id!r} includes unknown hotspot family"
            )

        hotspot_percent_covered = entry.get("hotspot_percent_covered")
        if not isinstance(hotspot_percent_covered, dict):
            failures.append(
                f"coverage review evidence: review {cycle_id!r} must include hotspot_percent_covered map"
            )
            hotspot_percent_covered = {}
        else:
            for family in reviewed_families:
                value = hotspot_percent_covered.get(family)
                if not isinstance(value, (int, float)):
                    failures.append(
                        "coverage review evidence: review "
                        f"{cycle_id!r} missing numeric hotspot percent for {family}"
                    )

        artifact_reference = entry.get("artifact_reference")
        if not isinstance(artifact_reference, str) or not artifact_reference.strip():
            failures.append(
                f"coverage review evidence: review {cycle_id!r} must include artifact_reference"
            )

    if policy.get("current_stage") == "ratchet-55":
        ratchet45_reviews = [
            entry
            for entry in reviews
            if isinstance(entry, dict) and entry.get("stage_id") == "ratchet-45"
        ]
        required_families = set(policy.get("required_hotspot_families", []))
        complete_reviews: list[dict[str, Any]] = []
        for entry in ratchet45_reviews:
            cycle_id = entry.get("cycle_id")
            release_cycle = entry.get("release_cycle")
            reviewed_commit = entry.get("reviewed_commit")
            coverage_command = entry.get("coverage_command")
            artifact_sha256 = entry.get("artifact_sha256")
            raw_reviewed_families = entry.get("reviewed_hotspot_families")
            reviewed_families = (
                set(raw_reviewed_families)
                if isinstance(raw_reviewed_families, list)
                else set()
            )
            hotspot_percent = entry.get("hotspot_percent_covered")
            owned_suites = entry.get("owned_regression_suites")

            start_tag = (
                release_cycle.get("start_tag")
                if isinstance(release_cycle, dict)
                else None
            )
            end_tag = (
                release_cycle.get("end_tag")
                if isinstance(release_cycle, dict)
                else None
            )
            start_commit = (
                release_cycle.get("start_commit")
                if isinstance(release_cycle, dict)
                else None
            )
            end_commit = (
                release_cycle.get("end_commit")
                if isinstance(release_cycle, dict)
                else None
            )
            release_fields_valid = (
                isinstance(start_tag, str)
                and bool(start_tag.strip())
                and isinstance(end_tag, str)
                and bool(end_tag.strip())
                and isinstance(start_commit, str)
                and _FULL_GIT_SHA_RE.fullmatch(start_commit.lower()) is not None
                and isinstance(end_commit, str)
                and _FULL_GIT_SHA_RE.fullmatch(end_commit.lower()) is not None
            )
            artifact_valid = (
                isinstance(artifact_sha256, str)
                and _SHA256_RE.fullmatch(artifact_sha256.lower()) is not None
                and isinstance(coverage_command, str)
                and "run_backend_coverage.py" in coverage_command
                and "--start-dir tests" in coverage_command
            )
            reviewed_commit_valid = (
                isinstance(reviewed_commit, str)
                and _FULL_GIT_SHA_RE.fullmatch(reviewed_commit.lower()) is not None
                and isinstance(end_commit, str)
                and reviewed_commit.lower() == end_commit.lower()
            )
            hotspot_valid = (
                reviewed_families == required_families
                and isinstance(hotspot_percent, dict)
                and all(
                    isinstance(hotspot_percent.get(family), (int, float))
                    and 0.0 <= float(hotspot_percent[family]) <= 100.0
                    for family in required_families
                )
            )
            ownership_valid = isinstance(owned_suites, dict) and all(
                isinstance(owned_suites.get(family), list)
                and bool(owned_suites[family])
                and all(
                    isinstance(path, str)
                    and path.startswith("tests/")
                    and path.endswith(".py")
                    for path in owned_suites[family]
                )
                for family in required_families
            )
            overall = entry.get("overall_percent_covered")
            overall_valid = isinstance(overall, (int, float)) and float(overall) >= 45.0

            if all(
                (
                    release_fields_valid,
                    artifact_valid,
                    reviewed_commit_valid,
                    hotspot_valid,
                    ownership_valid,
                    overall_valid,
                )
            ):
                complete_reviews.append(entry)
            else:
                failures.append(
                    "coverage review evidence: ratchet-55 promotion review "
                    f"{cycle_id!r} requires complete release-cycle evidence, full-suite "
                    "artifact identity, all required hotspots, and owned regression suites"
                )

        if len(complete_reviews) >= MIN_PROMOTION_REVIEW_CYCLES:
            for previous, current in pairwise(complete_reviews):
                previous_cycle = previous.get("release_cycle")
                current_cycle = current.get("release_cycle")
                if not isinstance(previous_cycle, dict) or not isinstance(
                    current_cycle, dict
                ):
                    continue
                if (
                    previous_cycle["end_tag"] != current_cycle["start_tag"]
                    or previous_cycle["end_commit"].lower()
                    != current_cycle["start_commit"].lower()
                ):
                    failures.append(
                        "coverage review evidence: ratchet-55 requires consecutive release cycles"
                    )
                    break

    return payload, failures


def current_stage_threshold(policy: dict[str, Any]) -> float:
    current_id = policy["current_stage"]
    for raw_stage in policy["stages"]:
        if raw_stage["id"] == current_id:
            return float(raw_stage["min_fail_under"])
    raise KeyError(f"Unknown current stage: {current_id}")


def next_stage(policy: dict[str, Any]) -> dict[str, Any] | None:
    current_id = policy["current_stage"]
    stages = policy["stages"]
    for index, raw_stage in enumerate(stages):
        if raw_stage["id"] == current_id:
            next_index = index + 1
            if next_index < len(stages):
                return stages[next_index]
            return None
    return None


def _matching_coverage_files(
    coverage_files: dict[str, Any], patterns: Iterable[str]
) -> tuple[list[str], list[str]]:
    normalized_files = {
        normalize_repo_path(path): payload for path, payload in coverage_files.items()
    }
    matched: set[str] = set()
    missing: list[str] = []
    for pattern in patterns:
        normalized_pattern = normalize_repo_path(pattern)
        hits = [
            path
            for path in normalized_files
            if fnmatch(path, normalized_pattern) or path == normalized_pattern
        ]
        if hits:
            matched.update(hits)
        else:
            missing.append(normalized_pattern)
    return sorted(matched), missing


def summarize_coverage(
    *, policy: dict[str, Any], coverage_payload: dict[str, Any]
) -> dict[str, Any]:
    files = coverage_payload.get("files", {})
    totals = coverage_payload.get("totals", {})
    if not isinstance(files, dict) or not isinstance(totals, dict):
        raise ValueError("coverage payload must contain files and totals objects")

    normalized_files = {
        normalize_repo_path(path): payload for path, payload in files.items()
    }

    hotspot_summary: dict[str, Any] = {}
    for family in policy["hotspot_families"]:
        family_id = family["id"]
        matched_files, missing_paths = _matching_coverage_files(
            normalized_files, family["paths"]
        )
        covered_lines = 0
        num_statements = 0
        for file_path in matched_files:
            summary = normalized_files[file_path].get("summary", {})
            covered_lines += int(summary.get("covered_lines", 0))
            num_statements += int(summary.get("num_statements", 0))
        percent = (
            round((covered_lines / num_statements) * 100, 2) if num_statements else 0.0
        )
        minimum_percent = family.get("minimum_percent_covered")
        hotspot_summary[family_id] = {
            "matched_files": matched_files,
            "missing_paths": missing_paths,
            "covered_lines": covered_lines,
            "num_statements": num_statements,
            "percent_covered": percent,
            "minimum_percent_covered": (
                float(minimum_percent) if minimum_percent is not None else None
            ),
            "floor_met": (
                percent >= float(minimum_percent)
                if minimum_percent is not None
                else None
            ),
        }

    next_policy_stage = next_stage(policy)
    overall_percent = totals.get("percent_covered")
    if isinstance(overall_percent, int):
        overall_percent = float(overall_percent)

    return {
        "policy": {
            "current_stage": policy["current_stage"],
            "current_stage_fail_under": current_stage_threshold(policy),
            "next_stage": next_policy_stage["id"] if next_policy_stage else None,
            "next_stage_fail_under": (
                float(next_policy_stage["min_fail_under"])
                if next_policy_stage
                else None
            ),
        },
        "overall": {
            "covered_lines": int(totals.get("covered_lines", 0)),
            "num_statements": int(totals.get("num_statements", 0)),
            "percent_covered": round(float(overall_percent or 0.0), 2),
        },
        "hotspot_families": hotspot_summary,
        "exceptions": policy.get("exceptions", []),
    }
