#!/usr/bin/env python3
"""
R114 implementation-record lint for defect-first regression evidence.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_KEYWORD_PATTERN = r"(bug|regression|security[ _-]?fix|hotfix|漏洞|瑕疵|修補)"


def _discover_record_files(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.glob("*_IMPLEMENTATION_RECORD.md")))
        elif p.is_file():
            files.append(p)
    dedup: list[Path] = []
    seen = set()
    for f in files:
        resolved = f.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        dedup.append(f)
    return dedup


def lint_record_text(
    text: str,
    *,
    strict: bool,
    keyword_pattern: str = DEFAULT_KEYWORD_PATTERN,
) -> tuple[bool, list[str]]:
    enforce = strict
    if not enforce:
        enforce = re.search(keyword_pattern, text, re.IGNORECASE) is not None
    if not enforce:
        return True, []

    issues: list[str] = []
    if not re.search(r"(?im)^##\s+Regression Evidence\b", text):
        issues.append("missing section: '## Regression Evidence'")

    required_fields = (
        r"(?im)^\s*[-*]\s*Defect(?:\s+ID)?\s*:\s*\S+",
        r"(?im)^\s*[-*]\s*Regression Test(?:\s+ID)?\s*:\s*\S+",
        r"(?im)^\s*[-*]\s*Failing Evidence\s*:\s*\S+",
        r"(?im)^\s*[-*]\s*Passing Evidence\s*:\s*\S+",
    )
    for pattern in required_fields:
        if not re.search(pattern, text):
            issues.append(f"missing required field matching: {pattern}")

    return len(issues) == 0, issues


def lint_records(
    paths: Sequence[str],
    *,
    strict: bool,
    keyword_pattern: str = DEFAULT_KEYWORD_PATTERN,
) -> tuple[bool, list[str]]:
    files = _discover_record_files(paths)
    if not files:
        return True, []

    errors: list[str] = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        ok, issues = lint_record_text(
            text,
            strict=strict,
            keyword_pattern=keyword_pattern,
        )
        if ok:
            continue
        for issue in issues:
            errors.append(f"{file_path}: {issue}")
    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint implementation records for defect-first regression evidence."
    )
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="Record file or directory. Repeatable. Defaults to .planning.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Force regression-evidence lint on all implementation records.",
    )
    parser.add_argument(
        "--keyword-pattern",
        default=DEFAULT_KEYWORD_PATTERN,
        help="Regex used to trigger lint in non-strict mode.",
    )
    args = parser.parse_args()

    paths = args.path or [".planning"]
    ok, errors = lint_records(
        paths,
        strict=args.strict,
        keyword_pattern=args.keyword_pattern,
    )
    if ok:
        print("IMPLEMENTATION_RECORD_LINT: PASS")
        return 0

    for err in errors:
        print(f"IMPLEMENTATION_RECORD_LINT: FAIL: {err}", file=sys.stdout)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
