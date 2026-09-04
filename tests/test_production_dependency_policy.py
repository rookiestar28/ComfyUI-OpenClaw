import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts import verify_production_dependencies as verifier


class ProductionDependencyFixture:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def policy() -> dict:
        return {
            "schema_version": 1,
            "review": {
                "owner": "maintainers",
                "reviewed_at": "2026-07-31",
                "next_review_by": "2026-10-31",
            },
            "tracked_roots": ["root.py", "alpha", "beta"],
            "domains": {
                "root": ["root.py"],
                "alpha": [
                    "alpha/__init__.py",
                    "alpha/api.py",
                    "alpha/helper.py",
                ],
                "beta": ["beta/__init__.py", "beta/adapter.py"],
            },
            "allowed_dependencies": {
                "root": ["alpha", "root"],
                "alpha": ["alpha", "beta"],
                "beta": ["beta"],
            },
            "compatibility_exceptions": [],
            "accepted_cycles": [],
            "dynamic_imports": [],
        }

    def write(
        self,
        files: dict[str, str],
        *,
        policy: dict | None = None,
        bom_paths: set[str] | None = None,
    ) -> dict:
        payload = deepcopy(policy or self.policy())
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            encoding = "utf-8-sig" if relative in (bom_paths or set()) else "utf-8"
            path.write_text(content, encoding=encoding)
        (self.root / "policy.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        return payload


def metadata(reason: str = "temporary compatibility debt") -> dict[str, str]:
    return {
        "owner": "maintainers",
        "rationale": reason,
        "review_condition": "remove after the dependency direction is corrected",
    }


def import_fallback_entry(
    path: str,
    classification: str,
    *,
    site_count: int,
    repository_site_count: int,
    alternate_site_count: int,
    baseline_site_count: int | None = None,
) -> dict:
    return {
        "path": path,
        "classification": classification,
        "baseline_site_count": (
            site_count if baseline_site_count is None else baseline_site_count
        ),
        "site_count": site_count,
        "repository_site_count": repository_site_count,
        "alternate_site_count": alternate_site_count,
        **metadata("reviewed import fallback fixture"),
    }


def configure_import_fallback_contract(
    policy: dict,
    inventory: list[dict],
    *,
    expected_live_candidate_count: int,
) -> None:
    finalized_site_count = sum(entry["baseline_site_count"] for entry in inventory)
    finalized_alternate_site_count = sum(
        entry["alternate_site_count"] for entry in inventory
    )
    finalized_repository_site_count = sum(
        entry["repository_site_count"]
        + (
            entry["baseline_site_count"] - entry["site_count"]
            if entry["classification"] == "migrated"
            else 0
        )
        for entry in inventory
    )
    policy["import_fallback_contract"] = {
        "production_roots": ["alpha", "beta"],
        "repository_roots": ["alpha", "beta"],
        "finalized_candidate_count": len(inventory),
        "finalized_site_count": finalized_site_count,
        "finalized_repository_site_count": finalized_repository_site_count,
        "finalized_alternate_site_count": finalized_alternate_site_count,
        "expected_live_candidate_count": expected_live_candidate_count,
        "inventory": inventory,
    }


class TestProductionDependencyPolicy(unittest.TestCase):
    def _base_files(self) -> dict[str, str]:
        return {
            "root.py": "from alpha import api\n",
            "alpha/__init__.py": "",
            "alpha/api.py": "from beta import adapter\n",
            "alpha/helper.py": "VALUE = 1\n",
            "beta/__init__.py": "",
            "beta/adapter.py": "VALUE = 2\n",
        }

    def _evaluate(
        self,
        files: dict[str, str] | None = None,
        *,
        configure=None,
        bom_paths: set[str] | None = None,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = ProductionDependencyFixture(root)
            policy = fixture.policy()
            if configure is not None:
                configure(policy)
            fixture.write(
                files or self._base_files(), policy=policy, bom_paths=bom_paths
            )
            findings = verifier.evaluate_repository(root, policy)
        return findings

    def assertCodes(self, findings, *codes: str) -> None:
        self.assertEqual([finding.rule_id for finding in findings], list(codes))

    def test_allowed_direction_and_dual_import_mode_pass_without_importing_source(self):
        files = self._base_files()
        files[
            "alpha/dual.py"
        ] = """
try:
    from . import helper
except ImportError:
    from alpha import helper

raise RuntimeError("production modules must never be imported by the verifier")
"""

        def own_dual(policy):
            policy["domains"]["alpha"].append("alpha/dual.py")

        findings = self._evaluate(files, configure=own_dual)

        self.assertEqual(findings, [])

    def test_import_fallback_contract_accepts_exact_repository_site(self):
        files = self._base_files()
        files[
            "alpha/dual.py"
        ] = """
try:
    from . import helper
except ImportError:
    from alpha import helper
"""

        def exact(policy):
            policy["domains"]["alpha"].append("alpha/dual.py")
            configure_import_fallback_contract(
                policy,
                [
                    import_fallback_entry(
                        "alpha/dual.py",
                        "migration_required",
                        site_count=1,
                        repository_site_count=1,
                        alternate_site_count=0,
                    )
                ],
                expected_live_candidate_count=1,
            )

        self.assertEqual(self._evaluate(files, configure=exact), [])

    def test_import_fallback_contract_rejects_unclassified_site(self):
        files = self._base_files()
        files[
            "alpha/dual.py"
        ] = """
try:
    from . import helper
except ImportError:
    from alpha import helper
"""

        def unclassified(policy):
            policy["domains"]["alpha"].append("alpha/dual.py")
            configure_import_fallback_contract(
                policy, [], expected_live_candidate_count=1
            )

        findings = self._evaluate(files, configure=unclassified)
        self.assertIn(
            "IMPORT_FALLBACK_UNCLASSIFIED",
            {finding.rule_id for finding in findings},
        )

    def test_import_fallback_contract_rejects_count_and_category_drift(self):
        files = self._base_files()
        files[
            "alpha/dual.py"
        ] = """
try:
    import fast_optional
except ModuleNotFoundError:
    import slow_optional
"""

        def drifted(policy):
            policy["domains"]["alpha"].append("alpha/dual.py")
            configure_import_fallback_contract(
                policy,
                [
                    import_fallback_entry(
                        "alpha/dual.py",
                        "migration_required",
                        site_count=2,
                        repository_site_count=0,
                        alternate_site_count=2,
                    )
                ],
                expected_live_candidate_count=1,
            )

        findings = self._evaluate(files, configure=drifted)
        codes = {finding.rule_id for finding in findings}
        self.assertIn("IMPORT_FALLBACK_COUNT_DRIFT", codes)
        self.assertIn("IMPORT_FALLBACK_CLASSIFICATION", codes)

    def test_import_fallback_contract_rejects_migrated_path_regression(self):
        files = self._base_files()
        files[
            "alpha/dual.py"
        ] = """
try:
    from . import helper
except ImportError:
    from alpha import helper
"""

        def regressed(policy):
            policy["domains"]["alpha"].append("alpha/dual.py")
            configure_import_fallback_contract(
                policy,
                [
                    import_fallback_entry(
                        "alpha/dual.py",
                        "migrated",
                        site_count=0,
                        repository_site_count=0,
                        alternate_site_count=0,
                        baseline_site_count=1,
                    )
                ],
                expected_live_candidate_count=0,
            )

        findings = self._evaluate(files, configure=regressed)
        self.assertIn(
            "IMPORT_FALLBACK_REGRESSION",
            {finding.rule_id for finding in findings},
        )

    def test_import_fallback_contract_accepts_approved_alternate_dependency(self):
        files = self._base_files()
        files[
            "alpha/optional.py"
        ] = """
try:
    import fast_optional
except ImportError:
    import slow_optional
"""

        def approved(policy):
            policy["domains"]["alpha"].append("alpha/optional.py")
            configure_import_fallback_contract(
                policy,
                [
                    import_fallback_entry(
                        "alpha/optional.py",
                        "approved_alternate_dependency",
                        site_count=1,
                        repository_site_count=0,
                        alternate_site_count=1,
                    )
                ],
                expected_live_candidate_count=1,
            )

        self.assertEqual(self._evaluate(files, configure=approved), [])

    def test_missing_submodule_does_not_collapse_to_an_owned_parent_package(self):
        files = self._base_files()
        files["alpha/api.py"] = "VALUE = 1\n"
        files["alpha/probe.py"] = "import beta.missing.redaction\n"

        def exact_only(policy):
            policy["domains"]["alpha"].append("alpha/probe.py")
            policy["allowed_dependencies"]["alpha"] = ["alpha"]

        findings = self._evaluate(files, configure=exact_only)

        self.assertEqual(findings, [])

    def test_forbidden_direction_has_stable_path_and_rule_code(self):
        files = self._base_files()
        files["beta/reverse.py"] = "from alpha import api\n"

        def own_reverse(policy):
            policy["domains"]["beta"].append("beta/reverse.py")

        findings = self._evaluate(files, configure=own_reverse)

        self.assertCodes(findings, "DEP_FORBIDDEN_DIRECTION")
        self.assertEqual(findings[0].path, "beta/reverse.py")
        self.assertEqual(findings[0].identity, "beta.reverse->alpha.api")

    def test_exact_compatibility_exception_passes_and_stale_entry_fails(self):
        files = self._base_files()
        files["alpha/api.py"] = "VALUE = 1\n"
        files["beta/reverse.py"] = "from alpha import api\n"

        def accepted(policy):
            policy["domains"]["beta"].append("beta/reverse.py")
            policy["compatibility_exceptions"] = [
                {
                    "importer": "beta.reverse",
                    "imported": "alpha.api",
                    **metadata(),
                }
            ]

        self.assertEqual(self._evaluate(files, configure=accepted), [])

        files["beta/reverse.py"] = "VALUE = 3\n"
        findings = self._evaluate(files, configure=accepted)
        self.assertCodes(findings, "DEP_STALE_EXCEPTION")

    def test_new_cycle_and_stale_accepted_cycle_are_rejected(self):
        files = self._base_files()
        files["beta/adapter.py"] = "from alpha import api\n"

        def allow_reverse(policy):
            policy["allowed_dependencies"]["beta"].append("alpha")

        findings = self._evaluate(files, configure=allow_reverse)
        self.assertCodes(findings, "CYCLE_NEW")

        def accept_cycle(policy):
            policy["allowed_dependencies"]["beta"].append("alpha")
            policy["accepted_cycles"] = [
                {
                    "modules": ["alpha.api", "beta.adapter"],
                    **metadata("reviewed fixture cycle"),
                }
            ]

        self.assertEqual(self._evaluate(files, configure=accept_cycle), [])
        files["beta/adapter.py"] = "VALUE = 2\n"
        findings = self._evaluate(files, configure=accept_cycle)
        self.assertCodes(findings, "CYCLE_STALE")

    def test_dynamic_literal_and_expression_require_exact_registration(self):
        files = self._base_files()
        files[
            "alpha/dynamic.py"
        ] = """
import importlib

def load(name):
    importlib.import_module("external.literal")
    return __import__(name)
"""

        def own_dynamic(policy):
            policy["domains"]["alpha"].append("alpha/dynamic.py")

        findings = self._evaluate(files, configure=own_dynamic)
        self.assertCodes(
            findings,
            "DYNAMIC_UNREGISTERED_EXPRESSION",
            "DYNAMIC_UNREGISTERED_LITERAL",
        )

        def register(policy):
            policy["domains"]["alpha"].append("alpha/dynamic.py")
            policy["dynamic_imports"] = [
                {
                    "path": "alpha/dynamic.py",
                    "scope": "load",
                    "callee": "__import__",
                    "target_kind": "expression",
                    "target": "name",
                    **metadata("runtime-selected module fixture"),
                },
                {
                    "path": "alpha/dynamic.py",
                    "scope": "load",
                    "callee": "importlib.import_module",
                    "target_kind": "literal",
                    "target": "external.literal",
                    **metadata("optional external module fixture"),
                },
            ]

        self.assertEqual(self._evaluate(files, configure=register), [])
        files["alpha/dynamic.py"] = "VALUE = 1\n"
        findings = self._evaluate(files, configure=register)
        self.assertCodes(findings, "DYNAMIC_STALE", "DYNAMIC_STALE")

    def test_policy_validation_rejects_unsafe_missing_duplicate_and_unknown_ownership(
        self,
    ):
        def invalid(policy):
            policy["unexpected"] = True
            policy["tracked_roots"].append("missing")
            policy["domains"]["root"].append("../outside.py")
            policy["domains"]["beta"].append("alpha/api.py")
            policy["allowed_dependencies"]["alpha"].append("ghost")

        findings = self._evaluate(configure=invalid)
        codes = {finding.code for finding in findings}

        self.assertTrue(
            {
                "POLICY_UNKNOWN_KEY",
                "ROOT_MISSING",
                "PATH_UNSAFE",
                "OWN_DUPLICATE",
                "DOMAIN_UNKNOWN",
            }.issubset(codes)
        )

    def test_tracked_module_in_accepted_root_must_be_owned(self):
        files = self._base_files()
        files["orphan/tool.py"] = "VALUE = 1\n"

        def unowned(policy):
            policy["tracked_roots"].append("orphan")

        findings = self._evaluate(files, configure=unowned)

        self.assertCodes(findings, "OWN_UNOWNED_MODULE")
        self.assertEqual(findings[0].path, "orphan/tool.py")

    def test_bom_source_is_parsed_and_source_content_is_not_reported(self):
        files = self._base_files()
        files["beta/reverse.py"] = (
            "# PRIVATE_SOURCE_MARKER_MUST_NOT_APPEAR\nfrom alpha import api\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = ProductionDependencyFixture(root)
            policy = fixture.policy()
            policy["domains"]["beta"].append("beta/reverse.py")
            fixture.write(
                files,
                policy=policy,
                bom_paths={"beta/reverse.py"},
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        Path(__file__).resolve().parents[1]
                        / "scripts"
                        / "verify_production_dependencies.py"
                    ),
                    "--repo-root",
                    str(root),
                    "--policy",
                    "policy.json",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("DEP_FORBIDDEN_DIRECTION beta/reverse.py", result.stdout)
        self.assertNotIn("PRIVATE_SOURCE_MARKER", result.stdout)
        self.assertNotIn(str(root), result.stdout)
        self.assertNotIn(str(root).replace("\\", "/"), result.stdout)

    def test_malformed_source_reports_only_bounded_content_free_identity(self):
        files = self._base_files()
        files["alpha/broken.py"] = "PRIVATE_PAYLOAD = '''unterminated\n"

        def own_broken(policy):
            policy["domains"]["alpha"].append("alpha/broken.py")

        findings = self._evaluate(files, configure=own_broken)

        self.assertCodes(findings, "SOURCE_PARSE")
        self.assertEqual(findings[0].path, "alpha/broken.py")
        self.assertEqual(findings[0].identity, "SyntaxError")
        self.assertNotIn("PRIVATE_PAYLOAD", findings[0].render())

    def test_cli_bounds_many_findings_and_reports_truncation(self):
        files = self._base_files()
        for index in range(verifier.MAX_FINDINGS + 5):
            files[f"beta/reverse_{index:02d}.py"] = "from alpha import api\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = ProductionDependencyFixture(root)
            policy = fixture.policy()
            policy["domains"]["beta"].extend(
                f"beta/reverse_{index:02d}.py"
                for index in range(verifier.MAX_FINDINGS + 5)
            )
            fixture.write(files, policy=policy)
            exit_code, lines = verifier.run_cli(
                root, root / "policy.json", max_findings=verifier.MAX_FINDINGS
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(lines), verifier.MAX_FINDINGS + 1)
        self.assertTrue(lines[-1].startswith("FINDINGS_TRUNCATED - "))


class TestRepositoryProductionDependencyPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.policy_path = (
            cls.repo_root / "tests" / "architecture_dependency_policy.json"
        )

    def test_repository_policy_is_complete_and_current(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))

        findings = verifier.evaluate_repository(self.repo_root, policy)

        self.assertEqual(findings, [])
        self.assertEqual(
            policy["tracked_roots"],
            [
                "__init__.py",
                "config.py",
                "api",
                "connector",
                "models",
                "nodes",
                "services",
                "scripts",
            ],
        )
        self.assertEqual(len(policy["accepted_cycles"]), 2)
        self.assertEqual(len(policy["dynamic_imports"]), 9)
        import_contract = policy["import_fallback_contract"]
        self.assertEqual(import_contract["finalized_candidate_count"], 60)
        self.assertEqual(import_contract["finalized_site_count"], 117)
        self.assertEqual(import_contract["finalized_repository_site_count"], 111)
        self.assertEqual(import_contract["finalized_alternate_site_count"], 6)
        self.assertEqual(import_contract["expected_live_candidate_count"], 58)
        self.assertEqual(len(import_contract["inventory"]), 60)
        self.assertEqual(
            {
                entry["path"]
                for entry in import_contract["inventory"]
                if entry["classification"] == "migrated"
            },
            {"api/route_orchestration.py", "services/queue_submit.py"},
        )

    def test_precommit_invokes_the_dependency_verifier(self):
        content = (self.repo_root / ".pre-commit-config.yaml").read_text(
            encoding="utf-8"
        )
        hook = content.split("- id: production-dependency-boundary", 1)[1].split(
            "# Secret detection", 1
        )[0]

        self.assertIn("scripts/verify_production_dependencies.py", hook)
        self.assertIn("language: python", hook)
        self.assertIn("pass_filenames: false", hook)
        self.assertIn("always_run: true", hook)
        self.assertNotIn("additional_dependencies", hook)


if __name__ == "__main__":
    unittest.main()
