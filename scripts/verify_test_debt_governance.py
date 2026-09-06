#!/usr/bin/env python3
"""
R171: validate skip-policy and mutation-survivor debt metadata.

This script is intentionally stdlib-only so it can run early in local/full-test
gates before optional dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _normalize_repo_rel_path(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("./")


def _parse_review_after(value: Any, *, label: str, failures: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        failures.append(f"{label}: missing review_after")
        return
    try:
        review_after = date.fromisoformat(value)
    except ValueError:
        failures.append(f"{label}: invalid review_after '{value}'")
        return
    if review_after < date.today():
        failures.append(f"{label}: review_after {value} is in the past")


def _validate_reason(value: Any, *, label: str, failures: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        failures.append(f"{label}: missing non-empty reason")


def _resolve_test_module_path(repo_root: Path, module_name: str) -> Path:
    return repo_root / Path(module_name.replace(".", "/")).with_suffix(".py")


def _validate_skip_policy(repo_root: Path, path: Path) -> list[str]:
    failures: list[str] = []
    payload = _read_json_object(path)

    max_skipped = payload.get("max_skipped")
    if not isinstance(max_skipped, int) or max_skipped < 0:
        failures.append("skip policy: max_skipped must be a non-negative integer")

    modules = payload.get("no_skip_modules", [])
    if not isinstance(modules, list) or any(
        not isinstance(item, str) or not item.strip() for item in modules
    ):
        failures.append(
            "skip policy: no_skip_modules must be a list of non-empty strings"
        )
        return failures

    seen = set()
    duplicates = set()
    normalized_modules: list[str] = []
    for module in modules:
        normalized = module.strip()
        normalized_modules.append(normalized)
        if normalized in seen:
            duplicates.add(normalized)
        seen.add(normalized)
        module_path = _resolve_test_module_path(repo_root, normalized)
        if not module_path.is_file():
            failures.append(
                f"skip policy: module path does not exist for {normalized} -> {module_path.relative_to(repo_root)}"
            )
    if duplicates:
        failures.append(
            "skip policy: duplicate no-skip modules: " + ", ".join(sorted(duplicates))
        )

    metadata = payload.get("no_skip_module_metadata")
    if not isinstance(metadata, dict):
        failures.append(
            "skip policy: no_skip_module_metadata must be an object keyed by module name"
        )
        return failures

    metadata_keys = {str(key).strip() for key in metadata.keys()}
    missing_metadata = [
        module for module in normalized_modules if module not in metadata_keys
    ]
    extra_metadata = sorted(
        key for key in metadata_keys if key and key not in set(normalized_modules)
    )
    if missing_metadata:
        failures.append(
            "skip policy: missing metadata for no-skip modules: "
            + ", ".join(sorted(missing_metadata))
        )
    if extra_metadata:
        failures.append(
            "skip policy: stale metadata without matching no-skip module: "
            + ", ".join(extra_metadata)
        )

    for module_name in normalized_modules:
        raw_meta = metadata.get(module_name)
        label = f"skip policy metadata[{module_name}]"
        if not isinstance(raw_meta, dict):
            failures.append(f"{label}: metadata entry must be an object")
            continue
        _validate_reason(raw_meta.get("reason"), label=label, failures=failures)
        _parse_review_after(
            raw_meta.get("review_after"), label=label, failures=failures
        )
    return failures


def _validate_mutation_allowlist(repo_root: Path, path: Path) -> list[str]:
    failures: list[str] = []
    payload = _read_json_object(path)
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        return ["mutation allowlist: entries must be a list"]

    seen: set[tuple[str, int]] = set()
    duplicates: set[tuple[str, int]] = set()
    for index, raw_entry in enumerate(entries):
        label = f"mutation allowlist entry[{index}]"
        if not isinstance(raw_entry, dict):
            failures.append(f"{label}: entry must be an object")
            continue
        file_path = _normalize_repo_rel_path(str(raw_entry.get("file", "")))
        mutation_index = raw_entry.get("mutation_index")
        if not file_path:
            failures.append(f"{label}: missing file")
        elif not (repo_root / Path(file_path)).is_file():
            failures.append(f"{label}: file does not exist in repo: {file_path}")
        if not isinstance(mutation_index, int) or mutation_index < 0:
            failures.append(f"{label}: mutation_index must be a non-negative integer")
        else:
            key = (file_path, mutation_index)
            if key in seen:
                duplicates.add(key)
            seen.add(key)
        _validate_reason(raw_entry.get("reason"), label=label, failures=failures)
        _parse_review_after(
            raw_entry.get("review_after"), label=label, failures=failures
        )

    if duplicates:
        failures.append(
            "mutation allowlist: duplicate (file, mutation_index) entries: "
            + ", ".join(
                f"{file}@{mutation_index}"
                for file, mutation_index in sorted(duplicates)
            )
        )
    return failures


def verify_test_debt_governance(
    *,
    repo_root: Path,
    skip_policy_path: Path,
    mutation_allowlist_path: Path,
) -> list[str]:
    failures: list[str] = []
    if not skip_policy_path.is_file():
        failures.append(f"missing skip policy: {skip_policy_path}")
    else:
        try:
            failures.extend(_validate_skip_policy(repo_root, skip_policy_path))
        except Exception as exc:
            failures.append(
                f"skip policy: failed to validate {skip_policy_path}: {exc}"
            )

    if not mutation_allowlist_path.is_file():
        failures.append(
            f"missing mutation survivor allowlist: {mutation_allowlist_path}"
        )
    else:
        try:
            failures.extend(
                _validate_mutation_allowlist(repo_root, mutation_allowlist_path)
            )
        except Exception as exc:
            failures.append(
                f"mutation allowlist: failed to validate {mutation_allowlist_path}: {exc}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate skip-policy and mutation-survivor debt metadata."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used to resolve test modules and file paths.",
    )
    parser.add_argument(
        "--skip-policy",
        default="tests/skip_policy.json",
        help="Path to tests/skip_policy.json",
    )
    parser.add_argument(
        "--mutation-survivor-allowlist",
        default="tests/mutation_survivor_allowlist.json",
        help="Path to tests/mutation_survivor_allowlist.json",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    failures = verify_test_debt_governance(
        repo_root=repo_root,
        skip_policy_path=Path(args.skip_policy),
        mutation_allowlist_path=Path(args.mutation_survivor_allowlist),
    )
    if failures:
        for failure in failures:
            print(f"TEST-DEBT-GOVERNANCE-FAIL: {failure}")
        return 1

    print(
        "TEST-DEBT-GOVERNANCE-PASS: skip-policy and mutation-survivor debt metadata are current."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
