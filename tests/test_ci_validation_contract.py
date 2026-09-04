from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
EVIDENCE_HELPER_PATH = ROOT / "scripts" / "python_compatibility_evidence.py"


def _job_block(text: str, job_id: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing CI job: {job_id}")
    return match.group("body")


def _load_evidence_helper():
    if not EVIDENCE_HELPER_PATH.is_file():
        raise AssertionError(
            f"Python compatibility evidence helper missing: {EVIDENCE_HELPER_PATH}"
        )
    spec = importlib.util.spec_from_file_location(
        "python_compatibility_evidence", EVIDENCE_HELPER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load evidence helper: {EVIDENCE_HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HostedValidationWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = CI_PATH.read_text(encoding="utf-8")

    def test_workflow_cancels_superseded_runs(self):
        self.assertRegex(
            self.workflow,
            re.compile(
                r"^concurrency:\n"
                r"  group: ci-\$\{\{ github\.workflow \}\}-"
                r"\$\{\{ github\.event\.pull_request\.number \|\| github\.ref \}\}\n"
                r"  cancel-in-progress: true$",
                re.MULTILINE,
            ),
        )

    def test_every_job_has_a_measured_finite_timeout(self):
        expected = {
            "import-smoke": 15,
            "frontend-e2e": 25,
            "unit-tests": 30,
            "backend-e2e-real": 15,
            "contract-tests": 15,
            "security-audit": 20,
            "adversarial-smoke": 20,
            "adversarial-extended": 45,
            "python-compatibility": 45,
        }
        for job_id, timeout in expected.items():
            with self.subTest(job=job_id):
                block = _job_block(self.workflow, job_id)
                self.assertIn(f"timeout-minutes: {timeout}", block)
                self.assertEqual(block.count("timeout-minutes:"), 1)
        self.assertEqual(self.workflow.count("timeout-minutes:"), len(expected))

    def test_setup_action_caches_are_dependency_keyed_and_non_bypassing(self):
        python_blocks = re.findall(
            r"- uses: actions/setup-python@v6\n"
            r"        with:\n(?P<body>(?:          [^\n]*\n)+)",
            self.workflow,
        )
        self.assertEqual(len(python_blocks), 9)
        for block in python_blocks:
            self.assertIn("cache: 'pip'", block)
            self.assertIn("cache-dependency-path: |", block)
            self.assertIn("requirements.txt", block)
            self.assertIn("requirements-quality.txt", block)

        for job_id in ("frontend-e2e", "security-audit"):
            block = _job_block(self.workflow, job_id)
            self.assertRegex(
                block,
                re.compile(
                    r"- uses: actions/setup-node@v5\n"
                    r"        with:\n"
                    r"          node-version: '20'\n"
                    r"          cache: 'npm'\n"
                    r"          cache-dependency-path: package-lock\.json"
                ),
            )

        self.assertNotIn("uses: actions/cache@", self.workflow)
        self.assertGreaterEqual(self.workflow.count("python -m pip install"), 9)
        self.assertEqual(self.workflow.count("npm ci"), 2)
        self.assertIn("npm audit --audit-level=high", self.workflow)

    def test_npm_audit_is_fail_closed_but_transport_bounded(self):
        block = _job_block(self.workflow, "security-audit")
        self.assertIn("npm audit --audit-level=high", block)
        self.assertIn("--fetch-retries=2", block)
        self.assertIn("--fetch-timeout=120000", block)
        self.assertNotRegex(
            block, re.compile(r"^\s+continue-on-error\s*:", re.MULTILINE)
        )
        self.assertNotIn("|| true", block)

    def test_state_isolation_uses_only_the_canonical_name(self):
        self.assertNotIn("MOLTBOT_STATE_DIR", self.workflow)
        self.assertGreaterEqual(self.workflow.count("OPENCLAW_STATE_DIR"), 7)

    def test_python_matrix_is_bounded_to_schedule_and_manual_events(self):
        block = _job_block(self.workflow, "python-compatibility")
        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'",
            block,
        )
        self.assertIn("runs-on: ubuntu-latest", block)
        self.assertIn("fail-fast: false", block)
        self.assertIn("max-parallel: 2", block)
        self.assertIn("python-version: ['3.10', '3.11', '3.12', '3.13']", block)
        self.assertIn("python-version: ${{ matrix.python-version }}", block)
        self.assertIn(
            'python scripts/run_unittests.py --start-dir tests --pattern "test_*.py"',
            block,
        )
        self.assertIn("python scripts/python_compatibility_evidence.py emit", block)
        self.assertIn("uses: actions/upload-artifact@v6", block)
        self.assertIn("if: success()", block)

    def test_public_support_copy_distinguishes_targets_from_executed_evidence(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "release" / "compatibility_matrix.md").read_text(
            encoding="utf-8"
        )
        support = (ROOT / "docs" / "release" / "support_policy.md").read_text(
            encoding="utf-8"
        )
        sop = (ROOT / "tests" / "TEST_SOP.md").read_text(encoding="utf-8")

        for text in (readme, matrix, support):
            self.assertNotIn("Python 3.10-3.13 is validated and supported", text)
        normalized_readme = " ".join(readme.split())
        self.assertIn(
            "Python 3.13 is the current locally validated baseline", normalized_readme
        )
        self.assertIn(
            "Python 3.10-3.12 remain compatibility targets", normalized_readme
        )
        self.assertIn("Current executed baseline", matrix)
        self.assertIn("Compatibility targets", support)
        self.assertIn("scheduled/manual Python 3.10-3.13 matrix", sop)
        self.assertIn("2026-10-31", sop)


class PythonCompatibilityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.helper = _load_evidence_helper()
        self.commit = "a" * 40

    def _evidence(
        self,
        version: str = "3.10",
        observed_at: str = "2026-10-30T12:00:00Z",
        status: str = "passed",
    ) -> dict[str, object]:
        evidence = self.helper.build_evidence(
            expected_version=version,
            commit_sha=self.commit,
            observed_at=observed_at,
            actual_version=f"{version}.9",
        )
        evidence["status"] = status
        return evidence

    def test_pre_eol_python_310_evidence_can_be_current(self):
        result = self.helper.evaluate_evidence(
            self._evidence(),
            expected_version="3.10",
            as_of=datetime(2026, 10, 30, 13, tzinfo=timezone.utc),
        )
        self.assertTrue(result["current"])
        self.assertEqual(result["code"], "PYTHON_EVIDENCE_CURRENT")

    def test_python_310_evidence_requires_reassessment_on_eol_date_and_after(self):
        for as_of in (
            datetime(2026, 10, 31, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        ):
            with self.subTest(as_of=as_of):
                result = self.helper.evaluate_evidence(
                    self._evidence(), expected_version="3.10", as_of=as_of
                )
                self.assertFalse(result["current"])
                self.assertEqual(result["code"], "PYTHON_310_REASSESSMENT_REQUIRED")

    def test_missing_failed_and_wrong_version_evidence_fail_closed(self):
        as_of = datetime(2026, 9, 5, tzinfo=timezone.utc)
        cases = (
            (None, "3.13", "PYTHON_EVIDENCE_MISSING"),
            (
                self._evidence("3.13", "2026-09-04T00:00:00Z", "failed"),
                "3.13",
                "PYTHON_EVIDENCE_FAILED",
            ),
            (
                self._evidence("3.12", "2026-09-04T00:00:00Z"),
                "3.13",
                "PYTHON_EVIDENCE_VERSION_MISMATCH",
            ),
        )
        for evidence, version, code in cases:
            with self.subTest(code=code):
                result = self.helper.evaluate_evidence(
                    evidence, expected_version=version, as_of=as_of
                )
                self.assertFalse(result["current"])
                self.assertEqual(result["code"], code)

    def test_non_object_evidence_fails_closed(self):
        result = self.helper.evaluate_evidence(
            ["not-an-evidence-object"],
            expected_version="3.13",
            as_of=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
        self.assertFalse(result["current"])
        self.assertEqual(result["code"], "PYTHON_EVIDENCE_INVALID")

    def test_stale_and_future_evidence_fail_closed(self):
        as_of = datetime(2026, 9, 30, tzinfo=timezone.utc)
        cases = (
            ("2026-09-01T00:00:00Z", "PYTHON_EVIDENCE_STALE"),
            ("2026-10-01T00:00:00Z", "PYTHON_EVIDENCE_FUTURE"),
        )
        for observed_at, code in cases:
            with self.subTest(code=code):
                result = self.helper.evaluate_evidence(
                    self._evidence("3.13", observed_at),
                    expected_version="3.13",
                    as_of=as_of,
                )
                self.assertFalse(result["current"])
                self.assertEqual(result["code"], code)

    def test_newer_successful_exact_version_evidence_is_current(self):
        result = self.helper.evaluate_evidence(
            self._evidence("3.11", "2026-09-04T12:00:00Z"),
            expected_version="3.11",
            as_of=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
        self.assertTrue(result["current"])
        self.assertEqual(result["code"], "PYTHON_EVIDENCE_CURRENT")

    def test_emit_cli_writes_safe_exact_interpreter_evidence(self):
        expected = f"{sys.version_info.major}.{sys.version_info.minor}"
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "evidence.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(EVIDENCE_HELPER_PATH),
                    "emit",
                    "--expected-version",
                    expected,
                    "--commit",
                    self.commit,
                    "--observed-at",
                    "2026-09-05T00:00:00Z",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["expected_python"], expected)
            self.assertEqual(payload["commit_sha"], self.commit)
            self.assertNotIn("environment", payload)
            self.assertNotIn("token", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
