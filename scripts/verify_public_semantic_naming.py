#!/usr/bin/env python3
"""Ratchet public source and test names away from internal roadmap shorthand.

Public tracked files should describe behavior. An internal item id such as the
one used in this repository's private planning records tells a reader nothing
about what the code does, and it leaks the shape of records that are not
published.

This verifier pins two quantities per repository:

* the exact set of tracked test files whose filename carries an item code, and
* the number of comment lines carrying an item code, per file.

Adding a coded comment to any file raises that file's count and fails. Editing
an unrelated line in the same file does not, so measured historical debt never
blocks work that has nothing to do with it. Removing debt also fails until the
baseline is updated, which keeps every reduction deliberate and reviewable.

The report deliberately contains only tracked repo-relative paths, line numbers
and matched tokens. It never reads, quotes, links or serializes internal
planning records, reference checkouts or command logs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = "tests/public_semantic_naming_policy.json"
COMMENT_PREFIXES = ("#", "//", "*", "/*")
ITEM_CODE_RE = re.compile(r"\b([RSF])(\d{1,4})\b")
CODE_NAMED_TEST_RE = re.compile(r"(?:^|/)test_[rsf]\d{1,4}_", re.IGNORECASE)

# Fragments that must never appear in the verifier's own output.
FORBIDDEN_REPORT_FRAGMENTS = (
    ".planning",
    "reference/",
    "REFERENCE/",
    ".reference",
    ".tmp/",
    "\\",
)

REFERENCE_SEARCH_SUFFIXES = (
    ".py",
    ".js",
    ".mjs",
    ".json",
    ".yml",
    ".yaml",
    ".ps1",
    ".sh",
    ".md",
    ".cfg",
    ".toml",
)


class PolicyError(RuntimeError):
    """The policy document is malformed."""


def tracked_files(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    )


def _is_scanned(path: str, policy: Mapping[str, Any]) -> bool:
    excluded = tuple(policy.get("excluded_prefixes", ()))
    if path.startswith(excluded):
        return False
    return path.endswith(tuple(policy.get("scanned_suffixes", ())))


def _allowed_tokens(policy: Mapping[str, Any]) -> dict[str, set[str]]:
    """Map a path to the item-code tokens explicitly allowed inside it."""
    allowed: dict[str, set[str]] = {}
    for entry in policy.get("allowed_public_identifiers", ()):
        if not isinstance(entry, Mapping):
            raise PolicyError("allowed_public_identifiers entries must be objects")
        path = entry.get("path")
        token = entry.get("token")
        reason = entry.get("reason")
        if not isinstance(path, str) or not isinstance(token, str):
            raise PolicyError("an allowed identifier needs a string path and token")
        if not isinstance(reason, str) or not reason.strip():
            raise PolicyError(f"allowed identifier {path}:{token} needs a reason")
        if any(fragment in reason for fragment in (".planning", "reference/")):
            raise PolicyError(
                f"allowed identifier {path}:{token} reason must stay public-safe"
            )
        allowed.setdefault(path, set()).add(token)
    return allowed


def scan_comment_codes(
    repo_root: Path, policy: Mapping[str, Any], paths: Iterable[str]
) -> tuple[Counter[str], list[dict[str, Any]]]:
    """Count item-code-bearing comment lines per file."""
    allowed = _allowed_tokens(policy)
    counts: Counter[str] = Counter()
    occurrences: list[dict[str, Any]] = []

    for path in paths:
        if not _is_scanned(path, policy):
            continue
        try:
            text = (repo_root / path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        permitted = allowed.get(path, set())
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(COMMENT_PREFIXES):
                continue
            tokens = [
                f"{cls}{num}"
                for cls, num in ITEM_CODE_RE.findall(stripped)
                if f"{cls}{num}" not in permitted
            ]
            if not tokens:
                continue
            counts[path] += 1
            occurrences.append({"path": path, "line": lineno, "tokens": tokens})

    return counts, occurrences


def find_code_named_tests(paths: Iterable[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path.endswith(".py") and CODE_NAMED_TEST_RE.search(path)
    )


def build_reference_graph(
    repo_root: Path, paths: Sequence[str], target: str
) -> list[str]:
    """Every tracked file that names `target` by stem, module path or full path."""
    stem = target.rsplit("/", 1)[-1]
    if stem.endswith(".py"):
        stem = stem[: -len(".py")]
    module = target[: -len(".py")].replace("/", ".") if target.endswith(".py") else ""
    needles = tuple(needle for needle in (target, module, stem) if needle)

    referrers = []
    for path in paths:
        if path == target or not path.endswith(REFERENCE_SEARCH_SUFFIXES):
            continue
        if path.startswith(("reference/", "REFERENCE/")):
            continue
        try:
            text = (repo_root / path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if any(needle in text for needle in needles):
            referrers.append(path)
    return sorted(referrers)


def build_report(repo_root: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    paths = tracked_files(repo_root)
    counts, occurrences = scan_comment_codes(repo_root, policy, paths)
    return {
        "code_named_tests": find_code_named_tests(paths),
        "comment_counts": dict(sorted(counts.items())),
        "occurrence_total": len(occurrences),
        "scanned_files": sum(1 for path in paths if _is_scanned(path, policy)),
    }


def compare_report(policy: Mapping[str, Any], report: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []

    pinned_tests = list(policy.get("code_named_tests", ()))
    found_tests = list(report["code_named_tests"])
    for path in sorted(set(found_tests) - set(pinned_tests)):
        failures.append(
            f"new item-code test filename: {path} - name the file for the behavior "
            "it protects"
        )
    for path in sorted(set(pinned_tests) - set(found_tests)):
        failures.append(
            f"stale baseline test filename: {path} is pinned but no longer tracked"
        )

    pinned_counts = policy.get("comment_counts", {})
    if not isinstance(pinned_counts, Mapping):
        raise PolicyError("comment_counts must be an object")
    found_counts = report["comment_counts"]
    for path in sorted(set(pinned_counts) | set(found_counts)):
        expected = int(pinned_counts.get(path, 0))
        found = int(found_counts.get(path, 0))
        if found > expected:
            failures.append(
                f"new item-code comment: {path} expected {expected}, found {found} - "
                "describe the invariant instead of citing an internal item"
            )
        elif found < expected:
            failures.append(
                f"stale baseline comment count: {path} expected {expected}, "
                f"found {found}"
            )
    return failures


def validate_report_privacy(report: Mapping[str, Any]) -> list[str]:
    """The report must expose only tracked repo-relative public paths."""
    serialized = json.dumps(report, sort_keys=True)
    return [
        f"report leaked a non-public path fragment: {fragment!r}"
        for fragment in FORBIDDEN_REPORT_FRAGMENTS
        if fragment in serialized
    ]


def read_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise PolicyError("policy document must be an object")
    for field in ("scanned_suffixes", "excluded_prefixes", "code_named_tests"):
        if not isinstance(policy.get(field), list):
            raise PolicyError(f"policy field {field} must be a list")
    if not isinstance(policy.get("comment_counts"), dict):
        raise PolicyError("policy field comment_counts must be an object")
    return policy


def verify(repo_root: Path, policy_path: Path) -> tuple[list[str], dict[str, Any]]:
    policy = read_policy(policy_path)
    report = build_report(repo_root, policy)
    failures = validate_report_privacy(report)
    failures.extend(compare_report(policy, report))
    return failures, report


def _write_baseline(policy_path: Path, report: Mapping[str, Any]) -> None:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["code_named_tests"] = list(report["code_named_tests"])
    policy["comment_counts"] = dict(report["comment_counts"])
    policy_path.write_bytes(
        (json.dumps(policy, indent=2, sort_keys=False) + "\n").encode("utf-8")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--policy", default=DEFAULT_POLICY_PATH)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Replace the accepted baseline after reviewing the change.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    policy_path = repo_root / args.policy

    try:
        policy = read_policy(policy_path)
        report = build_report(repo_root, policy)
    except (
        PolicyError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"SEMANTIC-NAMING-FAIL: cannot evaluate policy ({type(exc).__name__})")
        return 1

    if args.write_baseline:
        _write_baseline(policy_path, report)
        print(
            "SEMANTIC-NAMING-BASELINE-WRITTEN: "
            f"{len(report['code_named_tests'])} code-named tests, "
            f"{sum(report['comment_counts'].values())} comment lines"
        )
        return 0

    failures = validate_report_privacy(report)
    failures.extend(compare_report(policy, report))
    if failures:
        for failure in failures:
            print(f"SEMANTIC-NAMING-FAIL: {failure}")
        return 1

    print(
        "SEMANTIC-NAMING-PASS: "
        f"{len(report['code_named_tests'])} code-named tests, "
        f"{sum(report['comment_counts'].values())} comment lines, "
        f"{report['scanned_files']} files scanned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
