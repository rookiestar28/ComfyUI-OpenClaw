import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.quality_governance_test_utils import (
    sample_policy_payload,
    write_governance_baseline_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_quality_governance.py"


class TestR156QualityGovernance(unittest.TestCase):
    def _run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    @staticmethod
    def _ratchet45_release_review(
        *, cycle_id, start_tag, start_commit, end_tag, end_commit
    ):
        families = [
            "safe_io",
            "security_boundary",
            "connector_config",
            "config_bootstrap",
        ]
        return {
            "cycle_id": cycle_id,
            "stage_id": "ratchet-45",
            "reviewed_at": "2026-07-11",
            "overall_percent_covered": 69.78,
            "reviewed_hotspot_families": families,
            "hotspot_percent_covered": {
                "safe_io": 82.95,
                "security_boundary": 63.33,
                "connector_config": 65.03,
                "config_bootstrap": 68.77,
            },
            "owned_regression_suites": {
                family: [f"tests/test_{family}.py"] for family in families
            },
            "release_cycle": {
                "start_tag": start_tag,
                "start_commit": start_commit,
                "end_tag": end_tag,
                "end_commit": end_commit,
            },
            "reviewed_commit": end_commit,
            "coverage_command": "python scripts/run_backend_coverage.py --start-dir tests",
            "artifact_reference": f"{end_tag} full-suite coverage JSON",
            "artifact_sha256": "a" * 64,
        }

    @staticmethod
    def _ratchet55_policy():
        policy = sample_policy_payload(
            current_stage="ratchet-55",
            stages=[
                {"id": "baseline-35", "min_fail_under": 35.0},
                {"id": "ratchet-45", "min_fail_under": 45.0},
                {"id": "ratchet-55", "min_fail_under": 55.0},
            ],
        )
        for family in policy["hotspot_families"]:
            family["ratchet55_readiness"] = {
                "targeted_regression_suite": f"tests/test_{family['id']}.py",
                "ownership_status": "targeted-regression-owned",
                "readiness_notes": "Focused regression owner remains live.",
            }
        return policy

    def test_repo_governance_baseline_passes(self):
        result = self._run_script()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("GOVERNANCE-PASS", result.stdout)

    def test_repo_policy_declares_transactional_boundary_families(self):
        policy = json.loads(
            (ROOT / "tests" / "coverage_governance_policy.json").read_text(
                encoding="utf-8"
            )
        )
        required = set(policy["required_hotspot_families"])
        families = {family["id"]: family for family in policy["hotspot_families"]}

        self.assertEqual(
            required,
            {"safe_io", "security_boundary", "connector_config", "config_bootstrap"},
        )

        for family_id, expected_floor in (
            ("connector_ingress", 34.0),
            ("admin_api", 42.0),
        ):
            self.assertIn(family_id, families)
            self.assertEqual(
                families[family_id]["minimum_percent_covered"], expected_floor
            )
            readiness = families[family_id]["ratchet55_readiness"]
            self.assertEqual(
                readiness["ownership_status"],
                "transactional-trust-boundary-owned",
            )

    def test_invalid_family_coverage_floor_is_rejected(self):
        invalid_values = (True, -0.01, 100.01, "42.0")
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                policy = sample_policy_payload()
                policy["hotspot_families"][0]["minimum_percent_covered"] = invalid
                fixture = write_governance_baseline_fixture(
                    tmp, coverage_policy_payload=policy
                )

                result = self._run_script(
                    "--pyproject",
                    str(fixture["pyproject"]),
                    "--adversarial-gate",
                    str(fixture["adversarial_gate"]),
                    "--test-sop",
                    str(fixture["test_sop"]),
                    "--mutation-survivor-allowlist",
                    str(fixture["survivor_allowlist"]),
                    "--release-policy-doc",
                    str(fixture["release_policy_doc"]),
                    "--coverage-policy",
                    str(fixture["coverage_policy"]),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("minimum_percent_covered", result.stdout)

    def test_missing_coverage_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(tmp)

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(tmp / "missing_policy.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage governance policy", result.stdout)

    def test_non_monotonic_policy_thresholds_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                coverage_policy_payload=sample_policy_payload(
                    stages=[
                        {"id": "baseline-35", "min_fail_under": 35.0},
                        {"id": "ratchet-30", "min_fail_under": 30.0},
                    ],
                    required_hotspot_families=["safe_io"],
                    hotspot_families=[
                        {"id": "safe_io", "paths": ["services/safe_io.py"]}
                    ],
                ),
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("coverage stages must increase strictly", result.stdout)

    def test_missing_required_hotspot_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                coverage_policy_payload=sample_policy_payload(
                    required_hotspot_families=["safe_io", "connector_config"],
                    hotspot_families=[
                        {"id": "safe_io", "paths": ["services/safe_io.py"]}
                    ],
                ),
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required hotspot families", result.stdout)

    def test_missing_fail_under_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(tmp, fail_under=None)

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage fail_under", result.stdout)

    def test_stale_exception_review_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                coverage_policy_payload=sample_policy_payload(
                    exceptions=[
                        {
                            "id": "stale-hotspot-gap",
                            "family": "connector_config",
                            "paths": ["connector/config.py"],
                            "reason": "temporary uplift gap",
                            "review_by": "2000-01-01",
                        }
                    ],
                ),
            )
            review_evidence = tmp / "coverage_promotion_reviews.json"
            review_evidence.write_text(
                json.dumps({"schema_version": 1, "reviews": []}, indent=2) + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(review_evidence),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale exception review date", result.stdout)

    def test_promoted_stage_requires_two_previous_stage_reviews(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=45.0,
                coverage_policy_payload=sample_policy_payload(
                    current_stage="ratchet-45",
                    stages=[
                        {
                            "id": "baseline-35",
                            "min_fail_under": 35.0,
                            "promotion_requires": ["reviewed hotspots"],
                            "rollback_triggers": ["coverage regression"],
                        },
                        {
                            "id": "ratchet-45",
                            "min_fail_under": 45.0,
                            "promotion_requires": ["two consecutive reviews"],
                            "rollback_triggers": ["coverage regression"],
                        },
                    ],
                ),
            )
            review_evidence = tmp / "coverage_promotion_reviews.json"
            review_evidence.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reviews": [
                            {
                                "cycle_id": "baseline-cycle-1",
                                "stage_id": "baseline-35",
                                "reviewed_at": "2026-04-20",
                                "overall_percent_covered": 68.14,
                                "reviewed_hotspot_families": [
                                    "connector_config",
                                    "config_bootstrap",
                                ],
                                "hotspot_percent_covered": {
                                    "connector_config": 58.0,
                                    "config_bootstrap": 65.0,
                                },
                                "artifact_reference": ".tmp/coverage/cycle1.json",
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(review_evidence),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires at least 2 promotion review cycles", result.stdout)

    def test_promoted_stage_with_two_previous_stage_reviews_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=45.0,
                coverage_policy_payload=sample_policy_payload(
                    current_stage="ratchet-45",
                    stages=[
                        {
                            "id": "baseline-35",
                            "min_fail_under": 35.0,
                            "promotion_requires": ["reviewed hotspots"],
                            "rollback_triggers": ["coverage regression"],
                        },
                        {
                            "id": "ratchet-45",
                            "min_fail_under": 45.0,
                            "promotion_requires": ["two consecutive reviews"],
                            "rollback_triggers": ["coverage regression"],
                        },
                    ],
                ),
            )
            review_evidence = tmp / "coverage_promotion_reviews.json"
            review_evidence.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reviews": [
                            {
                                "cycle_id": "baseline-cycle-1",
                                "stage_id": "baseline-35",
                                "reviewed_at": "2026-04-19",
                                "overall_percent_covered": 67.25,
                                "reviewed_hotspot_families": [
                                    "connector_config",
                                    "config_bootstrap",
                                ],
                                "hotspot_percent_covered": {
                                    "connector_config": 56.5,
                                    "config_bootstrap": 64.0,
                                },
                                "artifact_reference": ".tmp/coverage/cycle1.json",
                            },
                            {
                                "cycle_id": "baseline-cycle-2",
                                "stage_id": "baseline-35",
                                "reviewed_at": "2026-04-20",
                                "overall_percent_covered": 68.14,
                                "reviewed_hotspot_families": [
                                    "connector_config",
                                    "config_bootstrap",
                                ],
                                "hotspot_percent_covered": {
                                    "connector_config": 58.0,
                                    "config_bootstrap": 65.0,
                                },
                                "artifact_reference": ".tmp/coverage/cycle2.json",
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(review_evidence),
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("GOVERNANCE-PASS", result.stdout)

    def test_ratchet55_rejects_legacy_reviews_without_release_artifact_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            reviews = [
                {
                    "cycle_id": f"legacy-{index}",
                    "stage_id": "ratchet-45",
                    "reviewed_at": "2026-07-11",
                    "overall_percent_covered": 70.0,
                    "reviewed_hotspot_families": ["safe_io"],
                    "hotspot_percent_covered": {"safe_io": 82.95},
                    "artifact_reference": "transient coverage output",
                }
                for index in (1, 2)
            ]
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=55.0,
                coverage_policy_payload=self._ratchet55_policy(),
                coverage_review_evidence_payload={
                    "schema_version": 1,
                    "reviews": reviews,
                },
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(fixture["coverage_review_evidence"]),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("complete release-cycle evidence", result.stdout)

    def test_ratchet55_rejects_malformed_review_types_without_crashing(self):
        malformed = self._ratchet45_release_review(
            cycle_id="malformed-review",
            start_tag="v0.9.0",
            start_commit="1" * 40,
            end_tag="v0.9.5",
            end_commit="2" * 40,
        )
        malformed["reviewed_hotspot_families"] = 7
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=55.0,
                coverage_policy_payload=self._ratchet55_policy(),
                coverage_review_evidence_payload={
                    "schema_version": 1,
                    "reviews": [
                        malformed,
                        malformed | {"cycle_id": "malformed-review-2"},
                    ],
                },
            )
            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(fixture["coverage_review_evidence"]),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("complete release-cycle evidence", result.stdout)

    def test_ratchet55_rejects_nonconsecutive_release_cycles(self):
        first = self._ratchet45_release_review(
            cycle_id="v0.9.0-to-v0.9.5",
            start_tag="v0.9.0",
            start_commit="1" * 40,
            end_tag="v0.9.5",
            end_commit="2" * 40,
        )
        second = self._ratchet45_release_review(
            cycle_id="v0.9.7-to-v1.0.0",
            start_tag="v0.9.7",
            start_commit="3" * 40,
            end_tag="v1.0.0",
            end_commit="4" * 40,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=55.0,
                coverage_policy_payload=self._ratchet55_policy(),
                coverage_review_evidence_payload={
                    "schema_version": 1,
                    "reviews": [first, second],
                },
            )
            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(fixture["coverage_review_evidence"]),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("consecutive release cycles", result.stdout)

    def test_ratchet55_accepts_complete_consecutive_release_evidence(self):
        first = self._ratchet45_release_review(
            cycle_id="v0.9.0-to-v0.9.5",
            start_tag="v0.9.0",
            start_commit="1" * 40,
            end_tag="v0.9.5",
            end_commit="2" * 40,
        )
        second = self._ratchet45_release_review(
            cycle_id="v0.9.5-to-v1.0.0",
            start_tag="v0.9.5",
            start_commit="2" * 40,
            end_tag="v1.0.0",
            end_commit="3" * 40,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=55.0,
                coverage_policy_payload=self._ratchet55_policy(),
                coverage_review_evidence_payload={
                    "schema_version": 1,
                    "reviews": [first, second],
                },
            )
            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(fixture["coverage_review_evidence"]),
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_ratchet55_policy_and_fail_under_must_move_atomically(self):
        first = self._ratchet45_release_review(
            cycle_id="v0.9.0-to-v0.9.5",
            start_tag="v0.9.0",
            start_commit="1" * 40,
            end_tag="v0.9.5",
            end_commit="2" * 40,
        )
        second = self._ratchet45_release_review(
            cycle_id="v0.9.5-to-v1.0.0",
            start_tag="v0.9.5",
            start_commit="2" * 40,
            end_tag="v1.0.0",
            end_commit="3" * 40,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                fail_under=45.0,
                coverage_policy_payload=self._ratchet55_policy(),
                coverage_review_evidence_payload={
                    "schema_version": 1,
                    "reviews": [first, second],
                },
            )
            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
                "--coverage-review-evidence",
                str(fixture["coverage_review_evidence"]),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "does not match policy current-stage floor 55.0", result.stdout
            )

    def test_repo_ratchet55_owned_regression_suites_exist(self):
        payload = json.loads(
            (ROOT / "tests" / "coverage_promotion_reviews.json").read_text(
                encoding="utf-8"
            )
        )
        ratchet45_reviews = [
            review
            for review in payload["reviews"]
            if review["stage_id"] == "ratchet-45"
        ]
        self.assertGreaterEqual(len(ratchet45_reviews), 2)
        for review in ratchet45_reviews:
            for suites in review["owned_regression_suites"].values():
                for relative_path in suites:
                    self.assertTrue((ROOT / relative_path).is_file(), relative_path)
