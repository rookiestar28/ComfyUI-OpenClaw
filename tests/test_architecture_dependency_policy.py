from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import verify_production_dependencies as dependency_policy


class ArchitecturePolicyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self._write("app/__init__.py", "")
        self._write("app/main.py", "from core.util import VALUE\n")
        self._write("core/__init__.py", "")
        self._write("core/util.py", "VALUE = 1\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, relative_path: str, content: str, *, bom: bool = False) -> None:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8-sig" if bom else "utf-8")

    def _tracked_files(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.relative_to(self.repo_root).as_posix()
                for path in self.repo_root.rglob("*.py")
            )
        )

    @staticmethod
    def _review_metadata() -> dict[str, str]:
        return {
            "owner": "architecture-maintainers",
            "rationale": "Temporary compatibility boundary.",
            "review_condition": "Remove when the importing module is moved.",
        }

    def _policy(self) -> dict:
        return {
            "schema_version": 1,
            "review": {
                "owner": "architecture-maintainers",
                "reviewed_at": "2026-07-31",
                "next_review_by": "2026-10-31",
            },
            "tracked_roots": ["app", "core"],
            "domains": {
                "app": ["app/__init__.py", "app/main.py"],
                "core": ["core/__init__.py", "core/util.py"],
            },
            "allowed_dependencies": {
                "app": ["app", "core"],
                "core": ["core"],
            },
            "compatibility_exceptions": [],
            "facade_contracts": [],
            "accepted_cycles": [],
            "dynamic_imports": [],
        }

    def _verify(self, policy: dict | None = None):
        return dependency_policy.verify_repository(
            self.repo_root,
            policy or self._policy(),
            tracked_files=self._tracked_files(),
        )

    def _codes(self, policy: dict | None = None) -> set[str]:
        return {finding.rule_id for finding in self._verify(policy)}

    def test_allowed_direction_passes(self):
        self.assertEqual(self._verify(), ())

    def test_forbidden_direction_fails_with_stable_rule(self):
        self._write("core/util.py", "from app.main import VALUE\n")

        self.assertIn("DEP_FORBIDDEN_DIRECTION", self._codes())

    def test_exact_compatibility_exception_passes_and_stale_entry_fails(self):
        self._write("app/main.py", "VALUE = 1\n")
        self._write("core/util.py", "from app.main import VALUE\n")
        policy = self._policy()
        policy["compatibility_exceptions"] = [
            {
                "importer": "core.util",
                "imported": "app.main",
                **self._review_metadata(),
            }
        ]
        self.assertEqual(self._verify(policy), ())

        self._write("core/util.py", "VALUE = 1\n")
        self.assertIn("DEP_STALE_EXCEPTION", self._codes(policy))

    def test_same_domain_facade_reverse_dependency_fails(self):
        self._write("app/facade.py", "from . import implementation\n")
        self._write("app/implementation.py", "VALUE = 1\n")
        policy = self._policy()
        policy["domains"]["app"].extend(
            [
                "app/facade.py",
                "app/implementation.py",
            ]
        )
        policy["facade_contracts"] = [
            {
                "facade": "app.facade",
                "implementation": "app.implementation",
                **self._review_metadata(),
            }
        ]
        self.assertEqual(self._verify(policy), ())

        self._write("app/implementation.py", "from . import facade\n")
        self.assertIn("FACADE_REVERSE_DEPENDENCY", self._codes(policy))

    def test_facade_contract_rejects_unknown_stale_and_duplicate_entries(self):
        self._write("app/facade.py", "from . import implementation\n")
        self._write("app/implementation.py", "VALUE = 1\n")
        policy = self._policy()
        policy["domains"]["app"].extend(
            [
                "app/facade.py",
                "app/implementation.py",
            ]
        )
        entry = {
            "facade": "app.facade",
            "implementation": "app.implementation",
            **self._review_metadata(),
        }
        policy["facade_contracts"] = [entry, copy.deepcopy(entry)]
        self.assertIn("FACADE_DUPLICATE", self._codes(policy))

        policy["facade_contracts"] = [
            {
                **entry,
                "implementation": "app.missing",
            }
        ]
        self.assertIn("FACADE_MODULE_UNKNOWN", self._codes(policy))

        policy["facade_contracts"] = [entry]
        self._write("app/facade.py", "VALUE = 1\n")
        self.assertIn("FACADE_STALE", self._codes(policy))

        policy["facade_contracts"] = "invalid"
        self.assertIn("FACADES_INVALID", self._codes(policy))

        policy["facade_contracts"] = [
            {
                "facade": "app.facade",
                "implementation": "app.implementation",
                "owner": "",
                "rationale": "",
                "review_condition": "",
                "unexpected": True,
            }
        ]
        codes = self._codes(policy)
        self.assertIn("POLICY_REVIEW_METADATA", codes)
        self.assertIn("POLICY_UNKNOWN_KEY", codes)

        policy["facade_contracts"] = "invalid"
        self.assertIn("FACADES_INVALID", self._codes(policy))

        policy["facade_contracts"] = [
            {
                "facade": "app.facade",
                "implementation": "app.facade",
                "owner": "",
                "rationale": "",
                "review_condition": "",
            }
        ]
        codes = self._codes(policy)
        self.assertIn("FACADES_INVALID", codes)
        self.assertIn("POLICY_REVIEW_METADATA", codes)

    def test_new_cycle_and_stale_accepted_cycle_fail(self):
        self._write("core/util.py", "from app.main import VALUE\n")
        policy = self._policy()
        policy["allowed_dependencies"]["core"].append("app")
        self.assertIn("CYCLE_NEW", self._codes(policy))

        policy["accepted_cycles"] = [
            {
                "modules": ["app.main", "core.util"],
                **self._review_metadata(),
            }
        ]
        self.assertEqual(self._verify(policy), ())

        self._write("core/util.py", "VALUE = 1\n")
        self.assertIn("CYCLE_STALE", self._codes(policy))

    def test_literal_and_expression_dynamic_imports_require_exact_registration(self):
        self._write(
            "app/main.py",
            "import importlib\n\n"
            "def load_literal():\n"
            '    return importlib.import_module("optional_plugin")\n\n'
            "def load_expression(module_name):\n"
            "    return importlib.import_module(module_name)\n",
        )
        self.assertEqual(
            self._codes(),
            {
                "DYNAMIC_UNREGISTERED_EXPRESSION",
                "DYNAMIC_UNREGISTERED_LITERAL",
            },
        )

        policy = self._policy()
        policy["dynamic_imports"] = [
            {
                "path": "app/main.py",
                "scope": "load_expression",
                "callee": "importlib.import_module",
                "target_kind": "expression",
                "target": "module_name",
                **self._review_metadata(),
            },
            {
                "path": "app/main.py",
                "scope": "load_literal",
                "callee": "importlib.import_module",
                "target_kind": "literal",
                "target": "optional_plugin",
                **self._review_metadata(),
            },
        ]
        self.assertEqual(self._verify(policy), ())

        self._write("app/main.py", "VALUE = 1\n")
        self.assertIn("DYNAMIC_STALE", self._codes(policy))

    def test_builtin_and_importlib_aliases_cannot_bypass_dynamic_registration(self):
        self._write(
            "app/main.py",
            "import builtins as runtime_builtins\n"
            "from importlib import import_module as load_module\n\n"
            "def load(module_name):\n"
            "    runtime_builtins.__import__(module_name)\n"
            '    return load_module("optional_plugin")\n',
        )

        self.assertEqual(
            self._codes(),
            {
                "DYNAMIC_UNREGISTERED_EXPRESSION",
                "DYNAMIC_UNREGISTERED_LITERAL",
            },
        )

    def test_dual_package_and_top_level_imports_resolve_to_one_owned_module(self):
        self._write(
            "app/main.py",
            "try:\n"
            "    from ..core.util import VALUE\n"
            "except ImportError:\n"
            "    from core.util import VALUE\n",
        )

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            self._policy(),
            tracked_files=self._tracked_files(),
        )

        self.assertEqual(analysis.static_edges, (("app.main", "core.util"),))
        self.assertEqual(analysis.findings, ())

    def test_canonical_dual_import_helper_literal_is_a_governed_static_edge(self):
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py", "def import_attrs_dual(*args): ...\n"
        )
        self._write("app/main.py", "VALUE = 1\n")
        self._write(
            "core/util.py",
            "from services.import_fallback import import_attrs_dual as load_attrs\n"
            "(VALUE,) = load_attrs(__package__, '..app.main', 'app.main', ('VALUE',))\n",
        )
        policy = self._policy()
        policy["tracked_roots"].append("services")
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
        ]
        policy["allowed_dependencies"]["services"] = ["services"]
        policy["allowed_dependencies"]["core"].append("services")
        policy["compatibility_exceptions"] = [
            {
                "importer": "core.util",
                "imported": "app.main",
                **self._review_metadata(),
            }
        ]

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            policy,
            tracked_files=self._tracked_files(),
        )

        self.assertIn(("core.util", "app.main"), analysis.static_edges)
        self.assertEqual(analysis.findings, ())

        self._write(
            "core/util.py",
            "from services.import_fallback import import_attrs_dual as load_attrs\n",
        )
        self.assertIn("DEP_STALE_EXCEPTION", self._codes(policy))

    def test_canonical_dual_import_helper_keyword_literal_is_a_governed_static_edge(
        self,
    ):
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py",
            "def import_attrs_dual(*args, **kwargs): ...\n",
        )
        self._write("app/main.py", "VALUE = 1\n")
        self._write(
            "core/util.py",
            "from services.import_fallback import import_attrs_dual as load_attrs\n"
            "(VALUE,) = load_attrs(\n"
            "    package_name=__package__,\n"
            "    relative_module='..app.main',\n"
            "    absolute_module='app.main',\n"
            "    attr_names=('VALUE',),\n"
            ")\n",
        )
        policy = self._policy()
        policy["tracked_roots"].append("services")
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
        ]
        policy["allowed_dependencies"]["services"] = ["services"]
        policy["allowed_dependencies"]["core"].append("services")

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            policy,
            tracked_files=self._tracked_files(),
        )

        self.assertIn(("core.util", "app.main"), analysis.static_edges)
        self.assertIn(
            "DEP_FORBIDDEN_DIRECTION",
            {finding.rule_id for finding in analysis.findings},
        )

        policy["compatibility_exceptions"] = [
            {
                "importer": "core.util",
                "imported": "app.main",
                **self._review_metadata(),
            }
        ]
        self.assertEqual(self._verify(policy), ())

    def test_canonical_dual_import_helper_governs_nonliteral_and_rejects_ambiguous(
        self,
    ):
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py",
            "def import_attrs_dual(*args, **kwargs): ...\n",
        )
        self._write("app/main.py", "VALUE = 1\n")
        self._write(
            "core/util.py",
            "from services.import_fallback import import_attrs_dual as load_attrs\n"
            "target = 'app.main'\n"
            "load_attrs(__package__, '..app.main', target, ('VALUE',))\n"
            "load_attrs(__package__, '..app.main', 'app.main', "
            "absolute_module='app.main', attr_names=('VALUE',))\n"
            "load_attrs(__package__, '..app.main', "
            "**{'absolute_module': 'app.main', 'attr_names': ('VALUE',)})\n"
            "star_args = ('..app.main', 'app.main', ('VALUE',))\n"
            "load_attrs(__package__, *star_args)\n",
        )
        policy = self._policy()
        policy["tracked_roots"].append("services")
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
        ]
        policy["allowed_dependencies"]["services"] = ["services"]
        policy["allowed_dependencies"]["core"].append("services")

        findings = self._verify(policy)

        invalid = [
            finding
            for finding in findings
            if finding.rule_id == "DUAL_IMPORT_HELPER_TARGET_INVALID"
        ]
        self.assertEqual(len(invalid), 3)
        self.assertIn(
            "DYNAMIC_UNREGISTERED_EXPRESSION",
            {finding.rule_id for finding in findings},
        )

        policy["dynamic_imports"] = [
            {
                "path": "core/util.py",
                "scope": "<module>",
                "callee": "services.import_fallback.import_attrs_dual",
                "target_kind": "expression",
                "target": "target",
                **self._review_metadata(),
            }
        ]
        self.assertEqual(
            {finding.rule_id for finding in self._verify(policy)},
            {"DUAL_IMPORT_HELPER_TARGET_INVALID"},
        )

    def test_canonical_dual_import_helper_rejects_mismatched_literal_targets(self):
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py",
            "def import_module_dual(*args, **kwargs): ...\n",
        )
        self._write("app/main.py", "VALUE = 1\n")
        self._write(
            "core/util.py",
            "from services.import_fallback import import_module_dual as load_module\n"
            "load_module(__package__, '..core.util', 'app.main')\n",
        )
        policy = self._policy()
        policy["tracked_roots"].append("services")
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
        ]
        policy["allowed_dependencies"]["services"] = ["services"]
        policy["allowed_dependencies"]["core"].append("services")

        self.assertIn("DUAL_IMPORT_HELPER_TARGET_MISMATCH", self._codes(policy))

    def test_canonical_dual_import_helper_normalizes_package_init_relative_target(self):
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py",
            "def import_module_dual(*args, **kwargs): ...\n",
        )
        self._write(
            "core/__init__.py",
            "from services.import_fallback import import_module_dual as load_module\n"
            "load_module(__package__, '.util', 'core.util')\n",
        )
        policy = self._policy()
        policy["tracked_roots"].append("services")
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
        ]
        policy["allowed_dependencies"]["services"] = ["services"]
        policy["allowed_dependencies"]["core"].append("services")

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            policy,
            tracked_files=self._tracked_files(),
        )

        self.assertIn(("core", "core.util"), analysis.static_edges)
        self.assertEqual(analysis.findings, ())

    def test_canonical_dual_import_helper_normalizes_repo_root_init_target(self):
        self._write(
            "__init__.py",
            "from services.import_fallback import import_module_dual as load_module\n"
            "load_module(__package__, '.services.queue_submit', "
            "'services.queue_submit')\n",
        )
        self._write("services/__init__.py", "")
        self._write(
            "services/import_fallback.py",
            "def import_module_dual(*args, **kwargs): ...\n",
        )
        self._write("services/queue_submit.py", "VALUE = 1\n")
        policy = self._policy()
        policy["tracked_roots"].extend(["__init__.py", "services"])
        policy["domains"]["root"] = ["__init__.py"]
        policy["domains"]["services"] = [
            "services/__init__.py",
            "services/import_fallback.py",
            "services/queue_submit.py",
        ]
        policy["allowed_dependencies"]["root"] = ["root", "services"]
        policy["allowed_dependencies"]["services"] = ["services"]

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            policy,
            tracked_files=self._tracked_files(),
        )

        self.assertIn(("__init__", "services.queue_submit"), analysis.static_edges)
        self.assertEqual(analysis.findings, ())

    def test_unrelated_same_name_helper_does_not_synthesize_static_edge(self):
        self._write(
            "core/util.py",
            "def import_attrs_dual(*args): ...\n"
            "(VALUE,) = import_attrs_dual("
            "__package__, '..app.main', 'app.main', ('VALUE',))\n",
        )

        analysis = dependency_policy.analyze_repository(
            self.repo_root,
            self._policy(),
            tracked_files=self._tracked_files(),
        )

        self.assertNotIn(("core.util", "app.main"), analysis.static_edges)
        self.assertEqual(analysis.findings, ())

    def test_unowned_new_tracked_module_fails(self):
        self._write("core/new_module.py", "VALUE = 2\n")

        self.assertIn("OWN_UNOWNED_MODULE", self._codes())

    def test_policy_rejects_missing_unsafe_duplicate_and_unknown_ownership(self):
        policy = self._policy()
        policy["tracked_roots"].extend(["missing", "../outside"])
        policy["domains"]["core"].append("app/main.py")
        policy["allowed_dependencies"]["app"].append("unknown-domain")

        codes = self._codes(policy)

        self.assertTrue(
            {
                "ROOT_MISSING",
                "PATH_UNSAFE",
                "OWN_DUPLICATE",
                "DOMAIN_UNKNOWN",
            }.issubset(codes)
        )

    def test_policy_rejects_unknown_keys_and_incomplete_review_metadata(self):
        policy = self._policy()
        policy["PRIVATE_POLICY_SENTINEL"] = True
        policy["compatibility_exceptions"] = [
            {
                "importer": "core.util",
                "imported": "app.main",
                "owner": "",
                "rationale": "fixture",
                "review_condition": "",
            }
        ]

        codes = self._codes(policy)

        self.assertIn("POLICY_UNKNOWN_KEY", codes)
        self.assertIn("POLICY_REVIEW_METADATA", codes)
        self.assertNotIn(
            "PRIVATE_POLICY_SENTINEL",
            dependency_policy.render_findings(self._verify(policy)),
        )

    def test_python_encoding_detection_accepts_utf8_bom_without_rewriting(self):
        self._write("core/util.py", "VALUE = 1\n", bom=True)
        before = (self.repo_root / "core" / "util.py").read_bytes()

        self.assertEqual(self._verify(), ())
        self.assertEqual((self.repo_root / "core" / "util.py").read_bytes(), before)

    def test_source_is_parsed_without_execution_or_source_content_disclosure(self):
        marker = self.repo_root / "executed.txt"
        secret = "PRIVATE_SOURCE_SENTINEL"
        self._write(
            "app/main.py",
            "from pathlib import Path\n"
            f'Path({str(marker)!r}).write_text("{secret}")\n'
            "import importlib\n"
            'importlib.import_module("unregistered")\n',
        )
        before = {
            path.relative_to(self.repo_root).as_posix(): path.read_bytes()
            for path in self.repo_root.rglob("*")
            if path.is_file()
        }

        findings = self._verify()
        rendered = dependency_policy.render_findings(findings)
        after = {
            path.relative_to(self.repo_root).as_posix(): path.read_bytes()
            for path in self.repo_root.rglob("*")
            if path.is_file()
        }

        self.assertFalse(marker.exists())
        self.assertEqual(after, before)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(str(self.repo_root), rendered)
        self.assertIn("DYNAMIC_UNREGISTERED_LITERAL", rendered)
        self.assertIn("app/main.py", rendered)


class RepositoryArchitecturePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.policy_path = (
            cls.repo_root / "tests" / "architecture_dependency_policy.json"
        )

    def test_repository_policy_is_canonical_and_current(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))

        analysis = dependency_policy.analyze_repository(self.repo_root, policy)

        self.assertEqual(analysis.findings, ())
        self.assertIn("scripts/bootstrap_project_venv.py", analysis.owned_paths)
        self.assertEqual(len(analysis.owned_paths), 313)
        self.assertEqual(len(policy["accepted_cycles"]), 2)
        self.assertEqual(len(policy["dynamic_imports"]), 9)
        self.assertEqual(len(policy["compatibility_exceptions"]), 9)
        self.assertIn(
            ("services.queue_submit", "api.errors"),
            {
                (entry["importer"], entry["imported"])
                for entry in policy["compatibility_exceptions"]
            },
        )
        self.assertEqual(len(policy["facade_contracts"]), 3)

    def test_policy_change_does_not_weaken_static_analysis_governance(self):
        static_policy_path = self.repo_root / "tests" / "static_analysis_policy.json"
        static_policy = json.loads(static_policy_path.read_text(encoding="utf-8"))
        architecture_policy = json.loads(self.policy_path.read_text(encoding="utf-8"))

        self.assertEqual(
            architecture_policy["tracked_roots"],
            static_policy["production_roots"],
        )
        self.assertEqual(
            architecture_policy["review"]["static_analysis_policy_schema"],
            static_policy["schema_version"],
        )

    def test_precommit_runs_the_dependency_verifier_without_runtime_dependencies(self):
        config = (self.repo_root / ".pre-commit-config.yaml").read_text(
            encoding="utf-8"
        )
        hook = config.split("- id: production-dependency-boundary", 1)[1].split(
            "# Secret detection", 1
        )[0]

        self.assertIn("scripts/verify_production_dependencies.py", hook)
        self.assertIn("language: python", hook)
        self.assertIn("pass_filenames: false", hook)
        self.assertIn("always_run: true", hook)
        self.assertNotIn("additional_dependencies", hook)

    def test_repository_verifier_is_deterministic(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))

        first = dependency_policy.verify_repository(self.repo_root, policy)
        second = dependency_policy.verify_repository(
            self.repo_root, copy.deepcopy(policy)
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
