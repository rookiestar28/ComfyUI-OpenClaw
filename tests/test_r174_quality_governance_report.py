import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.quality_governance_test_utils import sample_policy_payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "report_coverage_governance.py"


class TestR174QualityGovernanceReport(unittest.TestCase):
    def _run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    def test_reports_hotspot_family_totals_from_coverage_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"

            policy.write_text(
                json.dumps(
                    sample_policy_payload(
                        hotspot_families=[
                            {"id": "safe_io", "paths": ["services/safe_io.py"]},
                            {
                                "id": "connector_config",
                                "paths": ["connector/config.py"],
                            },
                            {
                                "id": "security_boundary",
                                "paths": ["services/security_gate.py"],
                            },
                            {"id": "config_bootstrap", "paths": ["config.py"]},
                        ]
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"version": "7.6.0"},
                        "files": {
                            "services/safe_io.py": {
                                "summary": {
                                    "covered_lines": 80,
                                    "num_statements": 100,
                                    "percent_covered": 80.0,
                                }
                            },
                            "connector/config.py": {
                                "summary": {
                                    "covered_lines": 18,
                                    "num_statements": 30,
                                    "percent_covered": 60.0,
                                }
                            },
                            "services/security_gate.py": {
                                "summary": {
                                    "covered_lines": 1,
                                    "num_statements": 1,
                                    "percent_covered": 100.0,
                                }
                            },
                            "config.py": {
                                "summary": {
                                    "covered_lines": 1,
                                    "num_statements": 1,
                                    "percent_covered": 100.0,
                                }
                            },
                            "services/llm_client.py": {
                                "summary": {
                                    "covered_lines": 50,
                                    "num_statements": 100,
                                    "percent_covered": 50.0,
                                }
                            },
                        },
                        "totals": {
                            "covered_lines": 148,
                            "num_statements": 230,
                            "percent_covered": 64.35,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["overall"]["percent_covered"], 64.35)
            self.assertEqual(
                payload["hotspot_families"]["safe_io"]["percent_covered"], 80.0
            )
            self.assertEqual(
                payload["hotspot_families"]["connector_config"]["percent_covered"], 60.0
            )

    def test_missing_hotspot_files_are_reported_deterministically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"

            policy.write_text(
                json.dumps(
                    sample_policy_payload(
                        hotspot_families=[
                            {"id": "safe_io", "paths": ["services/safe_io.py"]},
                            {
                                "id": "security_boundary",
                                "paths": ["services/security_gate.py"],
                            },
                            {
                                "id": "connector_config",
                                "paths": ["connector/config.py"],
                            },
                            {"id": "config_bootstrap", "paths": ["config.py"]},
                        ]
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"version": "7.6.0"},
                        "files": {},
                        "totals": {
                            "covered_lines": 0,
                            "num_statements": 0,
                            "percent_covered": 100.0,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["hotspot_families"]["security_boundary"]["missing_paths"],
                ["services/security_gate.py"],
            )
            self.assertIn("missing coverage path", result.stderr)

    def test_family_floor_equality_passes_and_below_floor_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"
            policy_payload = sample_policy_payload()
            policy_payload["hotspot_families"][0]["minimum_percent_covered"] = 80.0
            policy.write_text(
                json.dumps(policy_payload, indent=2) + "\n", encoding="utf-8"
            )

            def write_coverage(covered_lines):
                coverage_json.write_text(
                    json.dumps(
                        {
                            "files": {
                                "services/safe_io.py": {
                                    "summary": {
                                        "covered_lines": covered_lines,
                                        "num_statements": 100,
                                        "percent_covered": float(covered_lines),
                                    }
                                },
                                "services/security_gate.py": {
                                    "summary": {
                                        "covered_lines": 1,
                                        "num_statements": 1,
                                        "percent_covered": 100.0,
                                    }
                                },
                                "connector/config.py": {
                                    "summary": {
                                        "covered_lines": 1,
                                        "num_statements": 1,
                                        "percent_covered": 100.0,
                                    }
                                },
                                "config.py": {
                                    "summary": {
                                        "covered_lines": 1,
                                        "num_statements": 1,
                                        "percent_covered": 100.0,
                                    }
                                },
                                "services/runtime_config.py": {
                                    "summary": {
                                        "covered_lines": 1,
                                        "num_statements": 1,
                                        "percent_covered": 100.0,
                                    }
                                },
                            },
                            "totals": {
                                "covered_lines": covered_lines + 4,
                                "num_statements": 104,
                                "percent_covered": 80.0,
                            },
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

            write_coverage(80)
            equal = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertEqual(equal.returncode, 0, msg=equal.stdout + equal.stderr)
            equal_payload = json.loads(equal.stdout)
            self.assertTrue(equal_payload["hotspot_families"]["safe_io"]["floor_met"])

            write_coverage(79)
            below = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertNotEqual(below.returncode, 0)
            below_payload = json.loads(below.stdout)
            self.assertFalse(below_payload["hotspot_families"]["safe_io"]["floor_met"])
            self.assertIn("below coverage floor", below.stderr)

    def test_windows_style_coverage_paths_are_normalized_for_hotspot_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"

            policy.write_text(
                json.dumps(
                    sample_policy_payload(
                        hotspot_families=[
                            {"id": "safe_io", "paths": ["services/safe_io.py"]},
                            {
                                "id": "connector_config",
                                "paths": ["connector/config.py", "connector/router.py"],
                            },
                            {
                                "id": "security_boundary",
                                "paths": ["services/security_gate.py"],
                            },
                            {
                                "id": "config_bootstrap",
                                "paths": ["config.py", "services/runtime_config.py"],
                            },
                        ]
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"version": "7.13.5"},
                        "files": {
                            "services\\safe_io.py": {
                                "summary": {
                                    "covered_lines": 50,
                                    "num_statements": 100,
                                    "percent_covered": 50.0,
                                }
                            },
                            "connector\\config.py": {
                                "summary": {
                                    "covered_lines": 45,
                                    "num_statements": 50,
                                    "percent_covered": 90.0,
                                }
                            },
                            "connector\\router.py": {
                                "summary": {
                                    "covered_lines": 40,
                                    "num_statements": 80,
                                    "percent_covered": 50.0,
                                }
                            },
                            "services\\runtime_config.py": {
                                "summary": {
                                    "covered_lines": 28,
                                    "num_statements": 40,
                                    "percent_covered": 70.0,
                                }
                            },
                            "services\\security_gate.py": {
                                "summary": {
                                    "covered_lines": 1,
                                    "num_statements": 1,
                                    "percent_covered": 100.0,
                                }
                            },
                            "config.py": {
                                "summary": {
                                    "covered_lines": 1,
                                    "num_statements": 1,
                                    "percent_covered": 100.0,
                                }
                            },
                        },
                        "totals": {
                            "covered_lines": 163,
                            "num_statements": 270,
                            "percent_covered": 60.37,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["hotspot_families"]["connector_config"]["covered_lines"], 85
            )
            self.assertEqual(
                payload["hotspot_families"]["connector_config"]["num_statements"], 130
            )
            self.assertAlmostEqual(
                payload["hotspot_families"]["connector_config"]["percent_covered"],
                65.38,
                places=2,
            )
