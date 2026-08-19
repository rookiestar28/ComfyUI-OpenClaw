import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "scripts" / "run_adversarial_gate.py"

LEGACY_HIGH_RISK_PATTERNS = {
    "services/access_control.py",
    "services/tenant_context.py",
    "api/routes.py",
    "services/security_*.py",
    "services/startup_profile_gate.py",
    "services/control_plane.py",
    "services/endpoint_manifest.py",
    "services/webhook_auth.py",
    "services/safe_io.py",
}
STARTUP_OWNERS = [
    "__init__.py",
    "services/route_bootstrap.py",
    "services/bootstrap/registration.py",
    "services/route_bootstrap_contract.py",
    "services/import_fallback.py",
]
EXPECTED_SORTED_STARTUP_OWNERS = sorted(STARTUP_OWNERS)


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "adversarial_bootstrap_classification_gate", GATE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load adversarial gate module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdversarialBootstrapClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = _load_gate_module()

    def test_each_startup_owner_diff_selects_extended(self):
        for owner in STARTUP_OWNERS:
            with (
                self.subTest(owner=owner),
                patch.object(
                    self.gate,
                    "_collect_changed_files",
                    return_value=([owner], "fixture: startup owner"),
                ),
            ):
                result = self.gate._resolve_effective_profile(
                    "auto", None, None, self.gate.DEFAULT_HIGH_RISK_PATTERNS
                )

            self.assertEqual(
                result,
                ("extended", [owner], [owner], "fixture: startup owner"),
            )

    def test_all_startup_owners_diff_selects_extended_with_exact_matches(self):
        changed = sorted([*STARTUP_OWNERS, "tests/test_route_bootstrap_contract.py"])
        with patch.object(
            self.gate,
            "_collect_changed_files",
            return_value=(changed, "fixture: startup owners"),
        ):
            result = self.gate._resolve_effective_profile(
                "auto", None, None, self.gate.DEFAULT_HIGH_RISK_PATTERNS
            )

        self.assertEqual(
            result,
            (
                "extended",
                changed,
                EXPECTED_SORTED_STARTUP_OWNERS,
                "fixture: startup owners",
            ),
        )

    def test_default_inventory_preserves_legacy_and_adds_only_exact_boundaries(self):
        patterns = set(self.gate.DEFAULT_HIGH_RISK_PATTERNS)
        self.assertTrue(LEGACY_HIGH_RISK_PATTERNS.issubset(patterns))
        self.assertEqual(
            patterns - LEGACY_HIGH_RISK_PATTERNS,
            set(STARTUP_OWNERS),
        )
        self.assertEqual(len(patterns), len(self.gate.DEFAULT_HIGH_RISK_PATTERNS))
        self.assertNotIn("services/**", patterns)
        self.assertNotIn("services/bootstrap/**", patterns)
        self.assertNotIn("services/bootstrap/*.py", patterns)
        self.assertNotIn("**/__init__.py", patterns)

    def test_legacy_high_risk_paths_still_match(self):
        candidates = [
            "services/access_control.py",
            "services/tenant_context.py",
            "api/routes.py",
            "services/security_boundary.py",
            "services/startup_profile_gate.py",
            "services/control_plane.py",
            "services/endpoint_manifest.py",
            "services/webhook_auth.py",
            "services/safe_io.py",
        ]
        self.assertEqual(
            self.gate._filter_high_risk_files(
                candidates, self.gate.DEFAULT_HIGH_RISK_PATTERNS
            ),
            sorted(candidates),
        )

    def test_unrelated_and_neighboring_paths_remain_non_hotspots(self):
        candidates = [
            "services/bootstrap/posture.py",
            "services/bootstrap/registration_helper.py",
            "services/route_bootstrap_contract.py.bak",
            "services/route_bootstrap_helper.py",
            "services/import_fallback.py.bak",
            "nested/services/import_fallback.py",
            "services/__init__.py",
            "services/bootstrap/__init__.py",
            "services/other.py",
            "tests/test_route_bootstrap_contract.py",
            "docs/route_bootstrap.md",
        ]
        self.assertEqual(
            self.gate._filter_high_risk_files(
                candidates, self.gate.DEFAULT_HIGH_RISK_PATTERNS
            ),
            [],
        )
        with patch.object(
            self.gate,
            "_collect_changed_files",
            return_value=(candidates, "fixture: non-hotspot"),
        ):
            result = self.gate._resolve_effective_profile(
                "auto", None, None, self.gate.DEFAULT_HIGH_RISK_PATTERNS
            )
        self.assertEqual(result, ("smoke", candidates, [], "fixture: non-hotspot"))

    def test_explicit_profiles_take_precedence_without_diff_discovery(self):
        for requested in ("smoke", "extended"):
            with (
                self.subTest(requested=requested),
                patch.object(
                    self.gate,
                    "_collect_changed_files",
                    side_effect=AssertionError("explicit profile inspected diff"),
                ),
            ):
                self.assertEqual(
                    self.gate._resolve_effective_profile(
                        requested,
                        "malformed;base",
                        "malformed|head",
                        self.gate.DEFAULT_HIGH_RISK_PATTERNS,
                    ),
                    (requested, [], [], "explicit profile"),
                )

    def test_candidate_normalization_is_cross_platform_and_deterministic(self):
        candidates = [
            r".\__init__.py",
            r".\services\route_bootstrap.py",
            "services/bootstrap/./registration.py",
            r"services\route_bootstrap_contract.py",
            "services/./import_fallback.py",
        ]
        self.assertEqual(
            self.gate._filter_high_risk_files(candidates, STARTUP_OWNERS),
            EXPECTED_SORTED_STARTUP_OWNERS,
        )

    def test_malformed_paths_cannot_alias_bootstrap_boundaries(self):
        candidates = []
        for owner in STARTUP_OWNERS:
            candidates.extend(
                [
                    f"../{owner}",
                    f"/{owner}",
                    rf"C:\repo\{owner}",
                    f".../{owner}",
                    f"{owner}.bak",
                    f"nested/{owner}",
                ]
            )
        self.assertEqual(
            self.gate._filter_high_risk_files(candidates, STARTUP_OWNERS),
            [],
        )
        self.assertEqual(
            self.gate._normalize_rel_path("../services/bootstrap/registration.py"),
            "../services/bootstrap/registration.py",
        )
        self.assertEqual(
            self.gate._normalize_rel_path("/services/bootstrap/registration.py"),
            "/services/bootstrap/registration.py",
        )

    def test_build_manifest_preserves_selection_and_artifact_schema(self):
        changed_files = sorted(
            [
                "scripts/run_adversarial_gate.py",
                "tests/test_adversarial_bootstrap_classification.py",
            ]
        )
        fuzz_result = {
            "suite": "r111_fuzz",
            "passed": True,
            "seed": 42,
            "max_runs_per_target": 200,
            "total_crashes": 0,
            "crash_artifacts": [],
        }
        mutation_result = {
            "suite": "r113_mutation",
            "passed": True,
            "score": 90.0,
            "threshold": 20.0,
            "total_mutants": 10,
            "killed": 9,
            "survived": 1,
        }
        manifest = self.gate.build_manifest(
            "auto",
            "smoke",
            42,
            fuzz_result,
            mutation_result,
            ".tmp/adversarial-extended",
            1.25,
            changed_files=changed_files,
            high_risk_changed_files=[],
            diff_source="git diff base...candidate",
        )

        self.assertEqual(
            set(manifest),
            {
                "r118_version",
                "profile_requested",
                "profile",
                "seed",
                "timestamp",
                "elapsed_sec",
                "decision",
                "selection",
                "suites",
                "artifact_dir",
                "replay_command",
            },
        )
        self.assertEqual(manifest["r118_version"], "1.0")
        self.assertEqual(manifest["profile_requested"], "auto")
        self.assertEqual(manifest["profile"], "smoke")
        self.assertEqual(manifest["seed"], 42)
        self.assertEqual(manifest["decision"], "PASS")
        self.assertEqual(
            manifest["selection"],
            {
                "diff_source": "git diff base...candidate",
                "changed_files": changed_files,
                "high_risk_changed_files": [],
            },
        )
        self.assertEqual(
            manifest["suites"],
            {"r111_fuzz": fuzz_result, "r113_mutation": mutation_result},
        )
        self.assertEqual(manifest["elapsed_sec"], 1.25)
        self.assertEqual(
            manifest["artifact_dir"],
            os.path.abspath(".tmp/adversarial-extended"),
        )
        self.assertIn(
            "--profile smoke --seed 42 --artifact-dir .tmp/adversarial-extended",
            manifest["replay_command"],
        )

    def test_custom_appended_pattern_remains_supported(self):
        patterns = [*self.gate.DEFAULT_HIGH_RISK_PATTERNS, "custom/policy.py"]
        self.assertEqual(
            self.gate._filter_high_risk_files(
                ["custom/policy.py", "custom/nearby.py"], patterns
            ),
            ["custom/policy.py"],
        )

    def test_hostile_diff_refs_remain_non_shell_arguments(self):
        hostile_base = "main;echo injected"
        hostile_head = "HEAD|type secrets"
        calls = []

        def fail_git(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad ref")

        with (
            patch.object(self.gate.shutil, "which", return_value="git"),
            patch.object(self.gate.subprocess, "run", side_effect=fail_git),
        ):
            self.assertEqual(self.gate._run_git_diff(hostile_base, hostile_head), [])

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][0],
            ["git", "diff", "--name-only", f"{hostile_base}...{hostile_head}"],
        )
        self.assertEqual(
            calls[1][0],
            ["git", "diff", "--name-only", hostile_base, hostile_head],
        )
        for _command, kwargs in calls:
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs, {"capture_output": True, "text": True})

    def test_missing_diff_context_deterministically_selects_smoke(self):
        with patch.object(
            self.gate,
            "_collect_changed_files",
            return_value=([], "no git diff context"),
        ):
            self.assertEqual(
                self.gate._resolve_effective_profile(
                    "auto", None, None, self.gate.DEFAULT_HIGH_RISK_PATTERNS
                ),
                ("smoke", [], [], "no git diff context"),
            )

    def test_mutation_thresholds_remain_governed(self):
        self.assertEqual(self.gate.SMOKE_MUTATION_THRESHOLD, 20.0)
        self.assertEqual(self.gate.EXTENDED_MUTATION_THRESHOLD, 80.0)


if __name__ == "__main__":
    unittest.main()
