import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_test_debt_governance.py"
# IMPORTANT: valid fixtures need relative future dates; a fixed deadline makes
# unrelated governance tests fail as soon as that date expires.
VALID_REVIEW_DATE = (date.today() + timedelta(days=30)).isoformat()


def _write_repo_fixture(tmp: Path) -> dict[str, Path]:
    tests_dir = tmp / "tests"
    services_dir = tmp / "services"
    tests_dir.mkdir()
    services_dir.mkdir()

    guarded_test = tests_dir / "test_guarded_lane.py"
    guarded_test.write_text("import unittest\n", encoding="utf-8")
    access_control = services_dir / "access_control.py"
    access_control.write_text("TOKEN = 'ok'\n", encoding="utf-8")

    skip_policy = tests_dir / "skip_policy.json"
    skip_policy.write_text(
        json.dumps(
            {
                "max_skipped": 1,
                "no_skip_modules": ["tests.test_guarded_lane"],
                "no_skip_module_metadata": {
                    "tests.test_guarded_lane": {
                        "reason": "Guarded lane must stay no-skip in CI parity.",
                        "review_after": VALID_REVIEW_DATE,
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    mutation_allowlist = tests_dir / "mutation_survivor_allowlist.json"
    mutation_allowlist.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "file": "services/access_control.py",
                        "mutation_index": 9,
                        "reason": "Equivalent compare_digest branch with empty token.",
                        "review_after": VALID_REVIEW_DATE,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "repo_root": tmp,
        "skip_policy": skip_policy,
        "mutation_allowlist": mutation_allowlist,
    }


class TestR171TestDebtGovernance(unittest.TestCase):
    def _run_script(self, *args: str):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    def test_repo_governance_baseline_passes(self):
        result = self._run_script()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("TEST-DEBT-GOVERNANCE-PASS", result.stdout)

    def test_valid_fixture_passes_without_an_absolute_calendar_deadline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("TEST-DEBT-GOVERNANCE-PASS", result.stdout)

    def test_duplicate_no_skip_modules_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            fixture["skip_policy"].write_text(
                json.dumps(
                    {
                        "max_skipped": 1,
                        "no_skip_modules": [
                            "tests.test_guarded_lane",
                            "tests.test_guarded_lane",
                        ],
                        "no_skip_module_metadata": {
                            "tests.test_guarded_lane": {
                                "reason": "Guarded lane must stay no-skip in CI parity.",
                                "review_after": VALID_REVIEW_DATE,
                            }
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate no-skip modules", result.stdout)

    def test_missing_no_skip_module_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            fixture["skip_policy"].write_text(
                json.dumps(
                    {
                        "max_skipped": 1,
                        "no_skip_modules": ["tests.test_guarded_lane"],
                        "no_skip_module_metadata": {},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing metadata for no-skip modules", result.stdout)

    def test_duplicate_mutation_allowlist_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            fixture["mutation_allowlist"].write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "file": "services/access_control.py",
                                "mutation_index": 9,
                                "reason": "Equivalent compare_digest branch with empty token.",
                                "review_after": VALID_REVIEW_DATE,
                            },
                            {
                                "file": "services/access_control.py",
                                "mutation_index": 9,
                                "reason": "Duplicate entry for regression coverage.",
                                "review_after": VALID_REVIEW_DATE,
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate (file, mutation_index) entries", result.stdout)

    def test_stale_mutation_allowlist_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            fixture["mutation_allowlist"].write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "file": "services/missing.py",
                                "mutation_index": 9,
                                "reason": "Stale path should fail closed.",
                                "review_after": VALID_REVIEW_DATE,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("file does not exist in repo", result.stdout)

    def test_past_review_after_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_repo_fixture(Path(tmpdir))
            fixture["mutation_allowlist"].write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": [
                            {
                                "file": "services/access_control.py",
                                "mutation_index": 9,
                                "reason": "Expired reviews must not linger.",
                                "review_after": "2025-01-01",
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self._run_script(
                "--repo-root",
                str(fixture["repo_root"]),
                "--skip-policy",
                str(fixture["skip_policy"]),
                "--mutation-survivor-allowlist",
                str(fixture["mutation_allowlist"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("review_after 2025-01-01 is in the past", result.stdout)


if __name__ == "__main__":
    unittest.main()
