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


def configure_environment_alias_contract(
    policy: dict,
    *,
    supported: list[str] | None = None,
    supported_dynamic: list[str] | None = None,
    rejected: list[str] | None = None,
    exceptions: list[dict] | None = None,
) -> None:
    policy["environment_alias_contract"] = {
        "production_roots": ["alpha", "beta"],
        "central_owner": "alpha/env_aliases.py",
        "supported_legacy_keys": supported or ["MOLTBOT_FLAG"],
        "supported_dynamic_legacy_keys": supported_dynamic or [],
        "rejected_legacy_keys": rejected or ["CLAWDBOT_REJECTED"],
        "direct_read_exceptions": exceptions or [],
    }
    policy["domains"]["alpha"].append("alpha/env_aliases.py")


def environment_alias_owner_source() -> str:
    return (
        "LEGACY_MOLTBOT_ENV_KEYS = frozenset({'MOLTBOT_FLAG'})\n"
        "SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()\n"
        "SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()\n"
        "REJECTED_LEGACY_ENV_KEYS = frozenset({'CLAWDBOT_REJECTED'})\n"
    )


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

    def test_environment_alias_contract_rejects_literal_getenv_subscript_and_dynamic_reads(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os
from os import environ
from os import getenv
legacy = "OPENCLAW_FLAG".replace("OPENCLAW_", "MOLTBOT_", 1)
KEYS = ("OPENCLAW_FLAG", "MOLTBOT_FLAG")
ONE = os.environ.get("MOLTBOT_FLAG")
TWO = os.getenv("MOLTBOT_UNKNOWN")
THREE = os.environ["MOLTBOT_FLAG"]
FOUR = os.environ.get(legacy)
for key in KEYS:
    FIVE = os.environ.get(key)
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        codes = [finding.rule_id for finding in findings]

        self.assertEqual(codes.count("ENV_ALIAS_DIRECT_READ"), 3)
        self.assertEqual(codes.count("ENV_ALIAS_DYNAMIC_READ"), 2)
        self.assertEqual(codes.count("ENV_ALIAS_UNKNOWN_KEY"), 1)

    def test_environment_alias_contract_tracks_import_and_environment_object_aliases(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os as operating_system
from os import environ as process_environment
from os import getenv as read_environment

environment_alias = operating_system.environ
ONE = operating_system.getenv("MOLTBOT_FLAG")
TWO = read_environment("MOLTBOT_FLAG")
THREE = environment_alias.get("MOLTBOT_FLAG")
FOUR = process_environment["MOLTBOT_FLAG"]
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_respects_shadowing_and_lexical_signal_scope(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os
from os import environ
from os import getenv

KEYS = ("OPENCLAW_FLAG", "MOLTBOT_FLAG")

def governed():
    for key in KEYS:
        os.getenv(key)

def unrelated():
    key = "PATH"
    os.getenv(key)

def parameter_shadow(os):
    return os.getenv("MOLTBOT_FLAG")

def local_shadow():
    os = client
    return os.getenv("MOLTBOT_FLAG")

def imported_getter_shadow(getenv):
    return getenv("MOLTBOT_FLAG")

def imported_mapping_shadow(environ):
    return environ.get("MOLTBOT_FLAG")
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DYNAMIC_READ"),
            1,
        )
        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            0,
        )

    def test_environment_alias_contract_respects_same_scope_rebinding(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os
from os import environ
from os import getenv

os = fake_client
environ = fake_mapping
getenv = fake_reader

ONE = os.getenv("MOLTBOT_FLAG")
TWO = environ.get("MOLTBOT_FLAG")
THREE = getenv("MOLTBOT_FLAG")
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertNotIn(
            "ENV_ALIAS_DIRECT_READ", {finding.rule_id for finding in findings}
        )

    def test_environment_alias_contract_uses_execution_order_and_fails_closed_for_branches(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/dead_branch.py"
        ] = """import os
if False:
    os = client
VALUE = os.getenv("MOLTBOT_FLAG")
"""
        files[
            "alpha/later_rebind.py"
        ] = """import os
VALUE = os.getenv("MOLTBOT_FLAG")
os = client
"""
        files[
            "alpha/canonical_rebind.py"
        ] = """os = client
import os
VALUE = os.getenv("MOLTBOT_FLAG")
"""
        files[
            "alpha/conditional_origin.py"
        ] = """if condition:
    import os as reader
else:
    reader = client
VALUE = reader.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].extend(
                [
                    "alpha/dead_branch.py",
                    "alpha/later_rebind.py",
                    "alpha/canonical_rebind.py",
                    "alpha/conditional_origin.py",
                ]
            )

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_tracks_use_site_aliases_and_try_regions(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/use_site.py"
        ] = """from os import getenv as read_environment
ONE = read_environment("MOLTBOT_FLAG")
read_environment = client

import os as operating_system
environment = operating_system.environ
operating_system = client
TWO = environment.get("MOLTBOT_FLAG")

class DeadBranch:
    import os
    if False:
        os = client
    VALUE = os.getenv("MOLTBOT_FLAG")

class LaterRebind:
    import os
    VALUE = os.getenv("MOLTBOT_FLAG")
    os = client

class CanonicalRebind:
    os = client
    import os
    VALUE = os.getenv("MOLTBOT_FLAG")

try:
    import os as else_reader
except ImportError:
    pass
else:
    SIX = else_reader.getenv("MOLTBOT_FLAG")

try:
    import os as handler_reader
    might_fail()
except Exception:
    SEVEN = handler_reader.getenv("MOLTBOT_FLAG")

import os as deleted_reader
deleted_reader = client
del deleted_reader
UNBOUND = deleted_reader.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/use_site.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            7,
        )

    def test_environment_alias_contract_tracks_global_import_and_rebinding_order(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os

def read_before_rebind():
    global os
    value = os.getenv("MOLTBOT_FLAG")
    os = client
    return value

def read_after_rebind():
    global os
    os = client
    return os.getenv("MOLTBOT_FLAG")

def imported_global():
    global read_environment
    from os import getenv as read_environment
    return read_environment("MOLTBOT_FLAG")
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            2,
        )

    def test_environment_alias_contract_skips_class_namespace_for_method_resolution(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os

class Example:
    os = client

    def governed(self):
        return os.getenv("MOLTBOT_FLAG")

    def parameter_shadow(self, os):
        return os.getenv("MOLTBOT_FLAG")
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            1,
        )

    def test_environment_alias_contract_rejects_split_constant_dynamic_key(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/api.py"
        ] = """import os
key = "MOLT" + "BOT_FLAG"
VALUE = os.getenv(key)
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DYNAMIC_READ"),
            1,
        )

    def test_environment_alias_registry_requires_auditable_literal_frozensets(self):
        files = self._base_files()
        files[
            "alpha/env_aliases.py"
        ] = """def load_keys():
    return ()

LEGACY_MOLTBOT_ENV_KEYS = frozenset(
    load_keys() if True else {"MOLTBOT_FLAG"}
)
SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_REJECTED"})
"""

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

        files[
            "alpha/env_aliases.py"
        ] = """if condition:
    frozenset = fake

LEGACY_MOLTBOT_ENV_KEYS = frozenset({"MOLTBOT_FLAG"})
SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_REJECTED"})
"""
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

        files[
            "alpha/env_aliases.py"
        ] = """LEGACY_MOLTBOT_ENV_KEYS = frozenset({"MOLTBOT_FLAG"})
SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_REJECTED"})

frozenset = fake
"""
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertNotIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

        files[
            "alpha/env_aliases.py"
        ] = """LEGACY_MOLTBOT_ENV_KEYS = frozenset({"MOLTBOT_FLAG"})
SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_REJECTED"})

if condition:
    LEGACY_MOLTBOT_ENV_KEYS = frozenset({"MOLTBOT_OTHER"})
"""
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

        files[
            "alpha/env_aliases.py"
        ] = """def frozenset(values):
    return load_keys()

LEGACY_MOLTBOT_ENV_KEYS = frozenset({"MOLTBOT_FLAG"})
SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset()
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_REJECTED"})
"""
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

    def test_environment_alias_contract_matches_static_registry_without_importing_it(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = (
            environment_alias_owner_source()
            + 'raise RuntimeError("must not import production registry")\n'
        )

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
            bom_paths={"alpha/env_aliases.py"},
        )

        self.assertEqual(findings, [])

    def test_environment_alias_contract_fails_closed_on_registry_drift(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(
                policy,
                supported=["MOLTBOT_FLAG", "CLAWDBOT_MISSING"],
                rejected=["CLAWDBOT_DIFFERENT"],
            ),
        )
        codes = {finding.rule_id for finding in findings}

        self.assertIn("ENV_ALIAS_REGISTRY_MISSING", codes)
        self.assertIn("ENV_ALIAS_REJECTED_DRIFT", codes)

    def test_environment_alias_contract_fails_closed_on_dynamic_family_drift(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source().replace(
            "SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset()",
            "SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset({'MOLTBOT_DYNAMIC_FLAG'})",
        )

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(
                policy,
                supported_dynamic=["MOLTBOT_DIFFERENT_DYNAMIC_FLAG"],
            ),
        )

        self.assertIn(
            "ENV_ALIAS_DYNAMIC_KEYS_DRIFT",
            {finding.rule_id for finding in findings},
        )

    def test_environment_alias_direct_read_exception_must_be_exact_and_live(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files["alpha/api.py"] = 'import os\nVALUE = os.getenv("MOLTBOT_FLAG")\n'
        exception = {"path": "alpha/api.py", **metadata("reviewed raw-read fixture")}

        configured = lambda policy: configure_environment_alias_contract(
            policy, exceptions=[exception]
        )
        self.assertEqual(self._evaluate(files, configure=configured), [])

        files["alpha/api.py"] = "VALUE = 1\n"
        findings = self._evaluate(files, configure=configured)
        self.assertCodes(findings, "ENV_ALIAS_EXCEPTION_STALE")

    def test_environment_alias_contract_rejects_unknown_fields_and_unsafe_ownership(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()

        def invalid(policy):
            configure_environment_alias_contract(policy)
            contract = policy["environment_alias_contract"]
            contract["unexpected"] = True
            contract["production_roots"].append("missing")
            contract["central_owner"] = "../outside.py"
            contract["supported_legacy_keys"].append("MOLTBOT_FLAG")

        codes = {
            finding.rule_id for finding in self._evaluate(files, configure=invalid)
        }

        self.assertTrue(
            {
                "POLICY_UNKNOWN_KEY",
                "ENV_ALIAS_ROOTS_INVALID",
                "ENV_ALIAS_OWNER_INVALID",
                "ENV_ALIAS_KEY_DUPLICATE",
            }.issubset(codes)
        )


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
