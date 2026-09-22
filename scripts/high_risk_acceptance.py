"""Lightweight closeout evidence for changes classified as high risk.

This pilot deliberately reuses the adversarial gate's path classifier. Standard-
risk and empty diffs remain outside this workflow and produce no receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from run_adversarial_gate import (
    DEFAULT_HIGH_RISK_PATTERNS,
    _filter_high_risk_files,
    _run_git_diff,
)

SCHEMA = "openclaw-high-risk-receipt/2"
EXACT_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
ITEM_RE = re.compile(r"[A-Z][A-Z0-9-]{0,31}\Z")


class AcceptanceError(RuntimeError):
    """A safe, user-actionable closeout validation failure."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AcceptanceError("current directory is not inside a Git worktree")
    return Path(result.stdout.strip()).resolve()


def _resolve_commit(repo_root: Path, reference: str, label: str) -> str:
    if not reference or reference.startswith("-"):
        raise AcceptanceError(f"{label} must be a valid Git revision")
    result = _run_git(repo_root, "rev-parse", "--verify", f"{reference}^{{commit}}")
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not EXACT_COMMIT_RE.fullmatch(commit):
        raise AcceptanceError(f"{label} does not resolve to a commit")
    return commit


def _require_ancestor(repo_root: Path, base_commit: str, candidate_commit: str) -> None:
    result = _run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        base_commit,
        candidate_commit,
    )
    if result.returncode != 0:
        raise AcceptanceError("base commit is not an ancestor of candidate commit")


def _changed_files(
    repo_root: Path, base_commit: str, candidate_commit: str
) -> list[str]:
    previous_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        return [str(path) for path in _run_git_diff(base_commit, candidate_commit)]
    finally:
        os.chdir(previous_cwd)


def _require_closeout_state(
    repo_root: Path,
    candidate_argument: str,
    candidate_commit: str,
) -> str:
    if not EXACT_COMMIT_RE.fullmatch(candidate_argument):
        raise AcceptanceError(
            "high-risk candidate must be an exact 40-character commit SHA"
        )

    head_commit = _resolve_commit(repo_root, "HEAD", "HEAD")
    if candidate_commit != head_commit:
        raise AcceptanceError("candidate commit must equal current HEAD")

    branch_result = _run_git(repo_root, "branch", "--show-current")
    branch = branch_result.stdout.strip()
    if branch_result.returncode != 0 or branch != "dev":
        raise AcceptanceError("high-risk closeout must run on branch dev")

    status_result = _run_git(
        repo_root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    if status_result.returncode != 0 or status_result.stdout.strip():
        raise AcceptanceError("tracked worktree and index must be clean")
    return branch


def _require_identity(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise AcceptanceError(f"{label} is required for high-risk closeout")
    normalized = value.strip()
    if len(normalized) > 128 or any(ord(character) < 32 for character in normalized):
        raise AcceptanceError(f"{label} contains invalid characters")
    return normalized


def _validate_closeout_arguments(
    args: argparse.Namespace,
) -> tuple[str, dict[str, str | None]]:
    if args.item is None or not ITEM_RE.fullmatch(args.item):
        raise AcceptanceError("item must be an uppercase roadmap identifier")

    implementer = _require_identity(args.implementer, "implementer")
    if args.review_verdict != "APPROVED":
        raise AcceptanceError("review verdict must be APPROVED")
    if args.full_gate_status != "PASS":
        raise AcceptanceError("full TEST_SOP gate must be PASS")

    # IMPORTANT: mode and marker must be explicit; inferring either from reviewer
    # flags could silently turn a marked item into a self-reviewed receipt.
    if args.review_mode not in {"SELF", "INDEPENDENT"}:
        raise AcceptanceError("review mode must be SELF or INDEPENDENT")
    if args.item_marker not in {"NONE", "IMPORTANT", "DANGEROUS"}:
        raise AcceptanceError("item marker must be NONE, IMPORTANT or DANGEROUS")

    review: dict[str, str | None] = {
        "implementer": implementer,
        "mode": args.review_mode,
        "item_marker": args.item_marker,
        "marker_reason": None,
        "verdict": args.review_verdict,
    }
    if args.item_marker == "NONE":
        if args.review_mode != "SELF":
            raise AcceptanceError("unmarked item requires SELF review mode")
        if args.marker_reason is not None:
            raise AcceptanceError("unmarked item must not declare a marker reason")
        if args.reviewer is not None:
            raise AcceptanceError("self-review must not declare a separate reviewer")
    else:
        if args.review_mode != "INDEPENDENT":
            raise AcceptanceError("marked item requires INDEPENDENT review mode")
        review["marker_reason"] = _require_identity(args.marker_reason, "marker reason")
        reviewer = _require_identity(args.reviewer, "reviewer")
        if implementer.casefold() == reviewer.casefold():
            raise AcceptanceError("reviewer must be distinct from implementer")
        review["reviewer"] = reviewer
    return args.item, review


def _resolve_output(repo_root: Path, output: str | None) -> tuple[Path, str]:
    if output is None or not output.strip():
        raise AcceptanceError("output is required for high-risk closeout")

    candidate = Path(output)
    output_path = (
        (repo_root / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    planning_root = (repo_root / ".planning").resolve()
    try:
        relative_to_planning = output_path.relative_to(planning_root)
        relative_to_repo = output_path.relative_to(repo_root)
    except ValueError as exc:
        raise AcceptanceError(
            "output must be under the repository .planning directory"
        ) from exc
    if relative_to_planning == Path("."):
        raise AcceptanceError("output must name a file under .planning")
    if output_path.exists():
        raise AcceptanceError("output already exists")

    relative_posix = relative_to_repo.as_posix()
    ignored = _run_git(
        repo_root,
        "check-ignore",
        "-v",
        "--no-index",
        "--",
        relative_posix,
    )
    if ignored.returncode != 0:
        raise AcceptanceError("output must be ignored by repository Git rules")
    return output_path, relative_posix


def _write_receipt(output_path: Path, receipt: dict[str, object]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise AcceptanceError("output already exists") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and record the lightweight high-risk closeout pilot."
    )
    parser.add_argument("--base", required=True, help="Base Git revision.")
    parser.add_argument("--candidate", default="HEAD", help="Candidate Git revision.")
    parser.add_argument("--item")
    parser.add_argument("--implementer")
    parser.add_argument(
        "--review-mode", help="SELF or INDEPENDENT; required for high-risk closeout."
    )
    parser.add_argument(
        "--item-marker",
        help="NONE, IMPORTANT or DANGEROUS as filed in the active item.",
    )
    parser.add_argument(
        "--marker-reason",
        help="Concrete filed reason for an IMPORTANT or DANGEROUS marker.",
    )
    parser.add_argument(
        "--reviewer", help="Distinct reviewer for marked independent review only."
    )
    parser.add_argument(
        "--review-verdict",
        help="Declared APPROVED disposition for the selected review mode.",
    )
    parser.add_argument("--full-gate-status")
    parser.add_argument("--output")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo_root = _git_root()
        base_commit = _resolve_commit(repo_root, args.base, "base")
        candidate_commit = _resolve_commit(repo_root, args.candidate, "candidate")

        # IMPORTANT: ancestry is validated before classification so unrelated
        # histories cannot be mistaken for a standard-risk, non-applicable diff.
        _require_ancestor(repo_root, base_commit, candidate_commit)
        changed_files = _changed_files(repo_root, base_commit, candidate_commit)
        high_risk_changed = _filter_high_risk_files(
            changed_files, DEFAULT_HIGH_RISK_PATTERNS
        )
        if not high_risk_changed:
            print("HIGH_RISK_ACCEPTANCE: NOT_APPLICABLE")
            return 0

        branch = _require_closeout_state(repo_root, args.candidate, candidate_commit)
        item, review = _validate_closeout_arguments(args)
        output_path, output_label = _resolve_output(repo_root, args.output)
        receipt: dict[str, object] = {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "item": item,
            "branch": branch,
            "base_commit": base_commit,
            "candidate_commit": candidate_commit,
            "changed_files": changed_files,
            "high_risk_changed_files": high_risk_changed,
            "review": review,
            "gates": {"full_test_sop": args.full_gate_status},
            "limitations": (
                "Pilot receipt records declared review and gate results; it is not "
                "identity authentication or a cryptographic attestation."
            ),
        }
        _write_receipt(output_path, receipt)
        print(f"HIGH_RISK_ACCEPTANCE: PASS ({output_label})")
        return 0
    except AcceptanceError as exc:
        print(f"HIGH_RISK_ACCEPTANCE: FAIL: {exc}")
        return 1
    except (OSError, subprocess.SubprocessError):
        print("HIGH_RISK_ACCEPTANCE: FAIL: repository validation could not complete")
        return 1


if __name__ == "__main__":
    sys.exit(run())
