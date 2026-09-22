import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "high_risk_acceptance.py"


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(
        repo,
        "-c",
        "user.name=Governance Test",
        "-c",
        "user.email=governance@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_repo(
    root: Path, changed_path: str, ignore_rule: str = ".planning/\n"
) -> tuple[Path, str, str]:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    (repo / ".gitignore").write_text(ignore_rule, encoding="utf-8")
    initial = repo / changed_path
    initial.parent.mkdir(parents=True, exist_ok=True)
    initial.write_text("before\n", encoding="utf-8")
    base = _commit(repo, "initial")
    initial.write_text("after\n", encoding="utf-8")
    candidate = _commit(repo, "candidate")
    return repo, base, candidate


def _invoke(
    repo: Path,
    base: str,
    candidate: str,
    *,
    include_closeout: bool = True,
    output: str = ".planning/acceptance/R999.json",
    item: str = "R999",
    implementer: str = "implementer-a",
    reviewer: str = "reviewer-b",
    verdict: str = "APPROVED",
    full_gate: str = "PASS",
    review_mode: str | None = "INDEPENDENT",
    item_marker: str | None = "IMPORTANT",
    marker_reason: str | None = "Declared high-impact contract review",
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--base",
        base,
        "--candidate",
        candidate,
    ]
    if include_closeout:
        command.extend(["--item", item, "--implementer", implementer])
        if review_mode is not None:
            command.extend(["--review-mode", review_mode])
        if item_marker is not None:
            command.extend(["--item-marker", item_marker])
        if marker_reason is not None:
            command.extend(["--marker-reason", marker_reason])
        if reviewer is not None:
            command.extend(["--reviewer", reviewer])
        command.extend(
            [
                "--review-verdict",
                verdict,
                "--full-gate-status",
                full_gate,
                "--output",
                output,
            ]
        )
    return subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


class HighRiskAcceptancePilotTests(unittest.TestCase):
    def test_valid_high_risk_closeout_writes_lightweight_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
            result = _invoke(repo, base, candidate)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("HIGH_RISK_ACCEPTANCE: PASS", result.stdout)
            receipt_path = repo / ".planning/acceptance/R999.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(receipt),
                {
                    "schema",
                    "generated_at",
                    "item",
                    "branch",
                    "base_commit",
                    "candidate_commit",
                    "changed_files",
                    "high_risk_changed_files",
                    "review",
                    "gates",
                    "limitations",
                },
            )
            self.assertEqual(receipt["schema"], "openclaw-high-risk-receipt/2")
            self.assertEqual(receipt["item"], "R999")
            self.assertEqual(receipt["branch"], "dev")
            self.assertEqual(receipt["base_commit"], base)
            self.assertEqual(receipt["candidate_commit"], candidate)
            self.assertEqual(receipt["changed_files"], ["services/safe_io.py"])
            self.assertEqual(
                receipt["high_risk_changed_files"], ["services/safe_io.py"]
            )
            self.assertEqual(
                receipt["review"],
                {
                    "implementer": "implementer-a",
                    "reviewer": "reviewer-b",
                    "verdict": "APPROVED",
                    "mode": "INDEPENDENT",
                    "item_marker": "IMPORTANT",
                    "marker_reason": "Declared high-impact contract review",
                },
            )
            self.assertEqual(receipt["gates"], {"full_test_sop": "PASS"})
            self.assertIn("not identity authentication", receipt["limitations"])
            self.assertNotIn(str(repo), receipt_path.read_text(encoding="utf-8"))

    def test_unmarked_self_review_writes_no_invented_reviewer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
            result = _invoke(
                repo,
                base,
                candidate,
                review_mode="SELF",
                item_marker="NONE",
                marker_reason=None,
                reviewer=None,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads(
                (repo / ".planning/acceptance/R999.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                receipt["review"],
                {
                    "implementer": "implementer-a",
                    "verdict": "APPROVED",
                    "mode": "SELF",
                    "item_marker": "NONE",
                    "marker_reason": None,
                },
            )
            self.assertNotIn("reviewer", receipt["review"])

    def test_review_declaration_rejects_missing_or_contradictory_modes(self):
        cases = (
            {"review_mode": None},
            {"item_marker": None},
            {"review_mode": "SELF"},
            {"review_mode": "SELF", "item_marker": "NONE", "marker_reason": None},
            {"item_marker": "NONE"},
            {"item_marker": "NONE", "marker_reason": None},
            {"marker_reason": None},
            {"marker_reason": ""},
            {
                "review_mode": "SELF",
                "item_marker": "NONE",
                "marker_reason": None,
                "reviewer": None,
                "verdict": "FAIL",
            },
        )
        for overrides in cases:
            with (
                self.subTest(overrides=overrides),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
                result = _invoke(repo, base, candidate, **overrides)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((repo / ".planning/acceptance/R999.json").exists())

    def test_standard_risk_and_empty_diffs_are_not_applicable_without_receipts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, base, candidate = _make_repo(Path(tmpdir), "docs/readme.md")
            result = _invoke(repo, base, candidate, include_closeout=False, output="")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("HIGH_RISK_ACCEPTANCE: NOT_APPLICABLE", result.stdout)
            self.assertFalse((repo / ".planning").exists())

            empty = _invoke(
                repo, candidate, candidate, include_closeout=False, output=""
            )
            self.assertEqual(empty.returncode, 0, empty.stdout + empty.stderr)
            self.assertIn("HIGH_RISK_ACCEPTANCE: NOT_APPLICABLE", empty.stdout)
            self.assertFalse((repo / ".planning").exists())

    def test_high_risk_closeout_rejects_dirty_or_mismatched_repository_state(self):
        scenarios = (
            "dirty",
            "staged",
            "non_head",
            "symbolic_candidate",
            "wrong_branch",
            "non_ancestor",
        )
        for scenario in scenarios:
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
                if scenario == "dirty":
                    (repo / "services/safe_io.py").write_text(
                        "uncommitted\n", encoding="utf-8"
                    )
                elif scenario == "staged":
                    (repo / "services/safe_io.py").write_text(
                        "staged\n", encoding="utf-8"
                    )
                    _git(repo, "add", "services/safe_io.py")
                elif scenario == "non_head":
                    extra = repo / "docs/extra.md"
                    extra.parent.mkdir(parents=True, exist_ok=True)
                    extra.write_text("extra\n", encoding="utf-8")
                    _commit(repo, "later")
                elif scenario == "symbolic_candidate":
                    candidate = "HEAD"
                elif scenario == "wrong_branch":
                    _git(repo, "branch", "-m", "main")
                else:
                    _git(repo, "checkout", "--orphan", "other")
                    other = repo / "other.txt"
                    other.write_text("other\n", encoding="utf-8")
                    _git(repo, "rm", "-r", "--cached", ".")
                    candidate = _commit(repo, "unrelated")
                    _git(repo, "branch", "-D", "dev")
                    _git(repo, "branch", "-m", "dev")

                result = _invoke(repo, base, candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("HIGH_RISK_ACCEPTANCE: FAIL", result.stdout)
                self.assertFalse((repo / ".planning/acceptance/R999.json").exists())

    def test_high_risk_closeout_rejects_incomplete_or_non_independent_review(self):
        cases = (
            {"item": "r999"},
            {"reviewer": "IMPLEMENTER-A"},
            {"verdict": "CHANGES_REQUESTED"},
            {"full_gate": "FAIL"},
            {"reviewer": ""},
        )
        for overrides in cases:
            with (
                self.subTest(overrides=overrides),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
                result = _invoke(repo, base, candidate, **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("HIGH_RISK_ACCEPTANCE: FAIL", result.stdout)
                self.assertFalse((repo / ".planning/acceptance/R999.json").exists())

    def test_receipt_output_must_be_new_ignored_path_under_planning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, base, candidate = _make_repo(Path(tmpdir), "services/safe_io.py")
            outside = _invoke(repo, base, candidate, output="receipt.json")
            self.assertNotEqual(outside.returncode, 0)
            self.assertFalse((repo / "receipt.json").exists())

            first = _invoke(repo, base, candidate)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = _invoke(repo, base, candidate)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stdout)

        with tempfile.TemporaryDirectory() as tmpdir:
            repo, base, candidate = _make_repo(
                Path(tmpdir),
                "services/safe_io.py",
                ignore_rule=".planning/accepted/\n",
            )
            unignored = _invoke(repo, base, candidate)
            self.assertNotEqual(unignored.returncode, 0)
            self.assertIn("must be ignored", unignored.stdout)
            self.assertFalse((repo / ".planning/acceptance/R999.json").exists())

    def test_high_risk_path_policy_is_reused_not_duplicated(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_adversarial_gate", source)
        self.assertIn("DEFAULT_HIGH_RISK_PATTERNS", source)
        self.assertIn("_filter_high_risk_files", source)
        self.assertNotIn('"services/safe_io.py"', source)
        self.assertNotIn("DEFAULT_HIGH_RISK_PATTERNS =", source)


if __name__ == "__main__":
    unittest.main()
