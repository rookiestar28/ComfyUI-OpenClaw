#!/usr/bin/env python3
"""Emit and verify bounded, exact-version Python compatibility evidence."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_DAYS = 14
SUPPORTED_EVIDENCE_VERSIONS = frozenset({"3.10", "3.11", "3.12", "3.13"})
PYTHON_310_REASSESSMENT_DATE = date(2026, 10, 31)
_FULL_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


class PythonCompatibilityEvidenceError(ValueError):
    """Raised when compatibility evidence cannot be emitted safely."""


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PythonCompatibilityEvidenceError(
            f"invalid ISO-8601 timestamp: {value}"
        ) from exc
    if parsed.tzinfo is None:
        raise PythonCompatibilityEvidenceError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _major_minor(version: str) -> str:
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise PythonCompatibilityEvidenceError(f"invalid Python version: {version}")
    return f"{match.group(1)}.{match.group(2)}"


def build_evidence(
    *,
    expected_version: str,
    commit_sha: str,
    observed_at: str | None = None,
    actual_version: str | None = None,
) -> dict[str, Any]:
    """Build passed evidence only when the active interpreter matches the lane."""

    if expected_version not in SUPPORTED_EVIDENCE_VERSIONS:
        raise PythonCompatibilityEvidenceError(
            f"unsupported evidence version: {expected_version}"
        )
    if not _FULL_SHA_RE.fullmatch(commit_sha):
        raise PythonCompatibilityEvidenceError(
            "commit must be an exact lowercase 40-character SHA"
        )

    actual = actual_version or platform.python_version()
    if _major_minor(actual) != expected_version:
        raise PythonCompatibilityEvidenceError(
            f"active Python {actual} does not match expected {expected_version}"
        )

    observed = (
        _parse_utc(observed_at)
        if observed_at is not None
        else datetime.now(timezone.utc)
    )
    horizon = (
        PYTHON_310_REASSESSMENT_DATE.isoformat() if expected_version == "3.10" else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "expected_python": expected_version,
        "actual_python": actual,
        "commit_sha": commit_sha,
        "observed_at": _format_utc(observed),
        "support_reassessment_date": horizon,
    }


def _decision(current: bool, code: str, detail: str) -> dict[str, object]:
    return {"current": current, "code": code, "detail": detail}


def evaluate_evidence(
    evidence: Mapping[str, object] | None,
    *,
    expected_version: str,
    as_of: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict[str, object]:
    """Return a fail-closed currency decision for one exact Python version."""

    if evidence is None:
        return _decision(False, "PYTHON_EVIDENCE_MISSING", "evidence is missing")
    if not isinstance(evidence, Mapping):
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "evidence must be an object")
    if expected_version not in SUPPORTED_EVIDENCE_VERSIONS or max_age_days < 1:
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "invalid policy input")

    current_time = as_of or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        return _decision(
            False, "PYTHON_EVIDENCE_INVALID", "as_of must be timezone-aware"
        )
    current_time = current_time.astimezone(timezone.utc)

    if evidence.get("schema_version") != SCHEMA_VERSION:
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "schema mismatch")
    if evidence.get("status") != "passed":
        return _decision(False, "PYTHON_EVIDENCE_FAILED", "lane did not pass")
    if evidence.get("expected_python") != expected_version:
        return _decision(
            False,
            "PYTHON_EVIDENCE_VERSION_MISMATCH",
            "evidence belongs to a different Python lane",
        )

    actual_version = evidence.get("actual_python")
    if not isinstance(actual_version, str):
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "actual version missing")
    try:
        if _major_minor(actual_version) != expected_version:
            return _decision(
                False,
                "PYTHON_EVIDENCE_VERSION_MISMATCH",
                "active interpreter did not match the expected lane",
            )
    except PythonCompatibilityEvidenceError:
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "actual version malformed")

    commit_sha = evidence.get("commit_sha")
    if not isinstance(commit_sha, str) or not _FULL_SHA_RE.fullmatch(commit_sha):
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "commit SHA malformed")

    observed_at = evidence.get("observed_at")
    if not isinstance(observed_at, str):
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "timestamp missing")
    try:
        observed = _parse_utc(observed_at)
    except PythonCompatibilityEvidenceError:
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "timestamp malformed")

    expected_horizon = (
        PYTHON_310_REASSESSMENT_DATE.isoformat() if expected_version == "3.10" else None
    )
    if evidence.get("support_reassessment_date") != expected_horizon:
        return _decision(False, "PYTHON_EVIDENCE_INVALID", "policy horizon mismatch")

    # IMPORTANT: passing runtime evidence and upstream maintenance policy are separate.
    # Reusing any 3.10 artifact on/after this date would silently bypass reassessment.
    if (
        expected_version == "3.10"
        and current_time.date() >= PYTHON_310_REASSESSMENT_DATE
    ):
        return _decision(
            False,
            "PYTHON_310_REASSESSMENT_REQUIRED",
            "Python 3.10 reached its explicit support reassessment date",
        )
    if observed > current_time:
        return _decision(False, "PYTHON_EVIDENCE_FUTURE", "evidence is future-dated")
    if current_time - observed > timedelta(days=max_age_days):
        return _decision(False, "PYTHON_EVIDENCE_STALE", "evidence is too old")
    return _decision(
        True, "PYTHON_EVIDENCE_CURRENT", "exact-version evidence is current"
    )


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    emit = commands.add_parser("emit", help="emit evidence for the active interpreter")
    emit.add_argument("--expected-version", required=True)
    emit.add_argument("--commit", required=True)
    emit.add_argument("--observed-at")
    emit.add_argument("--output", type=Path, required=True)

    verify = commands.add_parser("verify", help="verify one evidence document")
    verify.add_argument("--expected-version", required=True)
    verify.add_argument("--evidence", type=Path, required=True)
    verify.add_argument("--as-of")
    verify.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "emit":
            payload = build_evidence(
                expected_version=args.expected_version,
                commit_sha=args.commit,
                observed_at=args.observed_at,
            )
            _write_json_atomic(args.output, payload)
            print(json.dumps({"written": str(args.output), "status": "passed"}))
            return 0

        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        as_of = _parse_utc(args.as_of) if args.as_of else None
        decision = evaluate_evidence(
            evidence,
            expected_version=args.expected_version,
            as_of=as_of,
            max_age_days=args.max_age_days,
        )
        print(json.dumps(decision, sort_keys=True))
        return 0 if decision["current"] else 1
    except (OSError, json.JSONDecodeError, PythonCompatibilityEvidenceError) as exc:
        print(f"python compatibility evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
