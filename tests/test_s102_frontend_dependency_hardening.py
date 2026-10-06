import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
PACKAGE_LOCK = ROOT / "package-lock.json"
WINDOWS_GATE = ROOT / "scripts" / "run_full_tests_windows.ps1"
LINUX_GATE = ROOT / "scripts" / "run_full_tests_linux.sh"
PRE_PUSH_GATE = ROOT / "scripts" / "pre_push_checks.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TEST_SOP = ROOT / "tests" / "TEST_SOP.md"
CI_POLICY = ROOT / "docs" / "release" / "ci_regression_policy.md"


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise AssertionError(f"expected a stable semantic version, got {value!r}")
    return tuple(int(part) for part in match.groups())


def _assert_ordered(test: unittest.TestCase, content: str, *needles: str) -> None:
    positions = [content.index(needle) for needle in needles]
    test.assertEqual(
        positions,
        sorted(positions),
        f"expected ordered commands: {' -> '.join(needles)}",
    )


class TestFrontendDependencyHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        cls.lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))

    def test_lock_resolves_patched_transitive_dependencies(self):
        packages = self.lock["packages"]
        self.assertGreaterEqual(
            _version_tuple(packages["node_modules/ws"]["version"]),
            (8, 21, 0),
        )
        self.assertGreaterEqual(
            _version_tuple(packages["node_modules/postcss"]["version"]),
            (8, 5, 18),
        )
        self.assertGreaterEqual(
            _version_tuple(packages["node_modules/nanoid"]["version"]),
            (3, 3, 17),
        )

    def test_root_manifest_does_not_claim_transitive_packages(self):
        root_dependencies = {
            name
            for section in ("dependencies", "devDependencies", "optionalDependencies")
            for name in self.package.get(section, {})
        }
        self.assertTrue(
            {"ws", "postcss", "nanoid", "source-map-js"}.isdisjoint(root_dependencies)
        )
        self.assertEqual(self.lock["lockfileVersion"], 3)

    def test_lock_resolves_patched_source_map_dependencies(self):
        versions = [
            package["version"]
            for path, package in self.lock["packages"].items()
            if path.endswith("node_modules/source-map-js")
        ]
        self.assertTrue(versions, "expected the PostCSS source-map dependency")
        # IMPORTANT: pre-1.2.2 indexed source maps can block the event loop;
        # check every locked copy so a nested vulnerable resolution cannot hide.
        for version in versions:
            with self.subTest(version=version):
                self.assertGreaterEqual(_version_tuple(version), (1, 2, 2))

    def test_windows_full_gate_reconciles_then_audits_frontend_tree(self):
        content = WINDOWS_GATE.read_text(encoding="utf-8")
        self.assertNotIn("$playwrightPkg", content)
        self.assertNotIn("Test-Path $playwrightPkg", content)
        self.assertEqual(content.count('Invoke-Checked "npm ci" { npm ci }'), 1)
        _assert_ordered(
            self,
            content,
            'Invoke-Checked "npm ci" { npm ci }',
            'Invoke-Checked "npm audit" { npm audit --audit-level=high }',
            'Invoke-Checked "frontend E2E" { npm test }',
        )

    def test_linux_full_gate_reconciles_then_audits_frontend_tree(self):
        content = LINUX_GATE.read_text(encoding="utf-8")
        self.assertNotIn("node_modules/@playwright/test/package.json", content)
        self.assertEqual(content.splitlines().count("  npm ci"), 1)
        _assert_ordered(
            self,
            content,
            "npm ci",
            "npm audit --audit-level=high",
            "npm test",
        )

    def test_pre_push_reconciles_then_audits_frontend_tree(self):
        content = PRE_PUSH_GATE.read_text(encoding="utf-8")
        self.assertEqual(content.count("npm ci"), 1)
        _assert_ordered(
            self,
            content,
            "npm ci",
            "npm audit --audit-level=high",
            "npm test",
        )

    def test_ci_security_job_blocks_full_frontend_tree_findings(self):
        content = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm ci", content)
        self.assertIn("npm audit --audit-level=high", content)
        self.assertNotIn("npm audit --production", content)
        _assert_ordered(
            self,
            content,
            "npm ci",
            "npm audit --audit-level=high",
        )

    def test_public_acceptance_docs_require_full_frontend_audit(self):
        required_command = "npm audit --audit-level=high"
        self.assertIn(required_command, TEST_SOP.read_text(encoding="utf-8"))
        policy = CI_POLICY.read_text(encoding="utf-8")
        self.assertIn(required_command, policy)
        self.assertIn("development dependencies", policy.lower())


if __name__ == "__main__":
    unittest.main()
