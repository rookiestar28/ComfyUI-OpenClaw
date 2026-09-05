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

    def test_environment_alias_contract_tracks_class_fallback_and_comprehension_iterables(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/scope_edges.py"
        ] = """import os

class DeadClassBinding:
    if False:
        os = client
    VALUE = os.getenv("MOLTBOT_FLAG")

class LaterClassBinding:
    VALUE = os.getenv("MOLTBOT_FLAG")
    os = client

class DeletedClassBinding:
    os = client
    del os
    VALUE = os.getenv("MOLTBOT_FLAG")

class DefiniteClassBinding:
    os = client
    NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

LIST_VALUE = [item for os in os.getenv("MOLTBOT_FLAG")]
SET_VALUE = {item for os in os.getenv("MOLTBOT_FLAG")}
DICT_VALUE = {item: item for os in os.getenv("MOLTBOT_FLAG")}
GEN_VALUE = (item for os in os.getenv("MOLTBOT_FLAG"))
BODY_NOT_ENVIRONMENT = [os.getenv("MOLTBOT_FLAG") for os in clients]
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/scope_edges.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            7,
        )

    def test_environment_alias_contract_preserves_first_comprehension_iterable_scope(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/same_name_comprehensions.py"
        ] = """import os

LIST_VALUE = [os("MOLTBOT_FLAG") for os in [os.getenv]]
SET_VALUE = {os("MOLTBOT_FLAG") for os in [os.getenv]}
DICT_VALUE = {os("MOLTBOT_FLAG"): True for os in [os.getenv]}
GEN_VALUE = tuple(os("MOLTBOT_FLAG") for os in [os.getenv])

CONTROL = [reader("PATH") for reader in [os.getenv]]
for os in [os.getenv]:
    LOOP_VALUE = os("PATH")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/same_name_comprehensions.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_models_comprehension_walrus_execution(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/walrus_eager.py"
        ] = """import os

[(list_reader := os.getenv) for _ in [1]]
list_reader("MOLTBOT_FLAG")
{(set_reader := os.getenv) for _ in [1]}
set_reader("MOLTBOT_FLAG")
{_: (dict_reader := os.getenv) for _ in [1]}
dict_reader("MOLTBOT_FLAG")

def governed():
    [(function_reader := os.getenv) for _ in [1]]
    return function_reader("MOLTBOT_FLAG")

[(os := client) for _ in [1]]
NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")
"""
        files[
            "alpha/walrus_generator.py"
        ] = """import os

consumed = ((reader := os.getenv) for _ in [1])
next(consumed)
reader("MOLTBOT_FLAG")

unconsumed = ((unused_reader := os.getenv) for _ in [1])
unused_reader("MOLTBOT_FLAG")

[(empty_reader := os.getenv) for _ in []]
empty_reader("MOLTBOT_FLAG")

[(filtered_reader := os.getenv) for _ in [1] if False]
filtered_reader("MOLTBOT_FLAG")

filtered_generator = ((filtered_gen_reader := os.getenv) for _ in [1] if False)
next(filtered_generator, None)
filtered_gen_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].extend(
                ["alpha/walrus_eager.py", "alpha/walrus_generator.py"]
            )

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            5,
        )

    def test_environment_alias_contract_proves_generator_walrus_consumption_at_use_site(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/walrus_generator_consumers.py"
        ] = """import os

aliased = ((alias_reader := os.getenv) for _ in [1])
consumer = aliased
next(consumer)
ONE = alias_reader("MOLTBOT_FLAG")

looped = ((loop_reader := os.getenv) for _ in [1])
for _ in looped:
    pass
TWO = loop_reader("MOLTBOT_FLAG")

materialized = ((list_reader := os.getenv) for _ in [1])
list(materialized)
THREE = list_reader("MOLTBOT_FLAG")

conditional = ((conditional_reader := os.getenv) for _ in [1])
if flag:
    next(conditional)
FOUR = conditional_reader("MOLTBOT_FLAG")

unknown_call = ((unknown_reader := os.getenv) for _ in [1])
consume(unknown_call)
FIVE = unknown_reader("MOLTBOT_FLAG")

list((inline_reader := os.getenv) for _ in [1])
SIX = inline_reader("MOLTBOT_FLAG")

unpacked = ((unpacked_reader := os.getenv) for _ in [1])
first, *rest = unpacked
SEVEN = unpacked_reader("MOLTBOT_FLAG")

method_consumed = ((method_reader := os.getenv) for _ in [1])
method_consumed.send(None)
EIGHT = method_reader("MOLTBOT_FLAG")

dead = ((dead_reader := os.getenv) for _ in [1])
if False:
    next(dead)
NOT_PROVEN = dead_reader("MOLTBOT_FLAG")

rebound = ((rebound_reader := os.getenv) for _ in [1])
rebound = iter(())
next(rebound, None)
ALSO_NOT_PROVEN = rebound_reader("MOLTBOT_FLAG")

lazy = ((lazy_reader := os.getenv) for _ in [1])
iter(lazy)
STILL_NOT_PROVEN = lazy_reader("MOLTBOT_FLAG")

wrapped = ((wrapped_reader := os.getenv) for _ in [1])
enumerate(wrapped)
WRAPPER_NOT_PROVEN = wrapped_reader("MOLTBOT_FLAG")

original = ((alias_rebound_reader := os.getenv) for _ in [1])
changed_alias = original
changed_alias = iter(())
next(changed_alias, None)
ALIAS_NOT_PROVEN = alias_rebound_reader("MOLTBOT_FLAG")

shadowed_lazy = ((shadowed_reader := os.getenv) for _ in [1])
iter = consume
iter(shadowed_lazy)
NINE = shadowed_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/walrus_generator_consumers.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            9,
        )

    def test_environment_alias_contract_models_bound_and_non_consuming_generator_calls(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/walrus_generator_bound_calls.py"
        ] = """import builtins
import os
from builtins import iter as lazy_iter

send_generator = ((send_reader := os.getenv) for _ in [1])
send_step = send_generator.send
send_step(None)
ONE = send_reader("MOLTBOT_FLAG")

next_generator = ((next_reader := os.getenv) for _ in [1])
next_step = next_generator.__next__
next_step()
TWO = next_reader("MOLTBOT_FLAG")

chained_generator = ((chained_reader := os.getenv) for _ in [1])
first_step = chained_generator.__next__
second_step = first_step
second_step()
THREE = chained_reader("MOLTBOT_FLAG")

dead_generator = ((dead_reader := os.getenv) for _ in [1])
dead_step = dead_generator.__next__
DEAD_NOT_PROVEN = dead_reader("MOLTBOT_FLAG")

rebound_generator = ((rebound_reader := os.getenv) for _ in [1])
rebound_step = rebound_generator.__next__
rebound_step = client
rebound_step()
REBOUND_NOT_PROVEN = rebound_reader("MOLTBOT_FLAG")

qualified_lazy_generator = ((qualified_lazy_reader := os.getenv) for _ in [1])
builtins.iter(qualified_lazy_generator)
QUALIFIED_LAZY_NOT_PROVEN = qualified_lazy_reader("MOLTBOT_FLAG")

imported_lazy_generator = ((imported_lazy_reader := os.getenv) for _ in [1])
lazy_iter(imported_lazy_generator)
IMPORTED_LAZY_NOT_PROVEN = imported_lazy_reader("MOLTBOT_FLAG")

throw_generator = ((throw_reader := os.getenv) for _ in [1])
throw_generator.throw(RuntimeError)
THROW_NOT_PROVEN = throw_reader("MOLTBOT_FLAG")

send_value_generator = ((send_value_reader := os.getenv) for _ in [1])
send_value_generator.send(1)
SEND_VALUE_NOT_PROVEN = send_value_reader("MOLTBOT_FLAG")

builtins = client
shadowed_qualified = ((shadowed_qualified_reader := os.getenv) for _ in [1])
builtins.iter(shadowed_qualified)
FOUR = shadowed_qualified_reader("MOLTBOT_FLAG")

lazy_iter = consume
shadowed_import = ((shadowed_import_reader := os.getenv) for _ in [1])
lazy_iter(shadowed_import)
FIVE = shadowed_import_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/walrus_generator_bound_calls.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            5,
        )

    def test_environment_alias_contract_tracks_keyword_and_explicit_mapping_reads(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/call_shapes.py"
        ] = """import os
from os import getenv as read_environment

ONE = os.getenv(key="MOLTBOT_FLAG")
TWO = read_environment(key="MOLTBOT_FLAG")
THREE = os.environ.get(key="MOLTBOT_FLAG")
FOUR = os.getenv(**{"key": "MOLTBOT_FLAG"})
FIVE = os.environ.__getitem__("MOLTBOT_FLAG")
SIX = os.getenv(*("MOLTBOT_FLAG",))
SEVEN = os.environ.get(*["MOLTBOT_FLAG"])

legacy_key = "MOLTBOT_FLAG"
EIGHT = os.getenv(**{"key": legacy_key})

positional_args = ("MOLTBOT_FLAG",)
NINE = os.getenv(*positional_args)
keyword_args = {"key": "MOLTBOT_FLAG"}
TEN = os.getenv(**keyword_args)
overridden_keyword_args = {"key": "MOLTBOT_FLAG"}
overridden_keyword_args = {"key": "PATH"}
NOT_A_KEY = os.getenv(**overridden_keyword_args)
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/call_shapes.py")

        findings = self._evaluate(files, configure=configure)
        codes = [finding.rule_id for finding in findings]

        self.assertEqual(codes.count("ENV_ALIAS_DIRECT_READ"), 9)
        self.assertEqual(codes.count("ENV_ALIAS_DYNAMIC_READ"), 1)

    def test_environment_alias_contract_tracks_first_effective_starred_call_argument(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/starred_calls.py"
        ] = """import os

ONE = os.getenv(*(), *("MOLTBOT_FLAG",))
TWO = os.getenv(*[], "MOLTBOT_FLAG")
THREE = os.getenv(*(), *[], "MOLTBOT_FLAG")
FOUR = os.getenv(*{"MOLTBOT_FLAG"})
FIVE = os.getenv(*{"PATH", "MOLTBOT_FLAG"})
NOT_A_KEY = os.getenv(*("PATH",), "MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/starred_calls.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            5,
        )

    def test_environment_alias_contract_propagates_compound_and_unpacked_aliases(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/compound_aliases.py"
        ] = """import os as real

boolean_reader = real or client
ONE = boolean_reader.getenv("MOLTBOT_FLAG")

conditional_reader = real if condition else client
TWO = conditional_reader.getenv("MOLTBOT_FLAG")

(unpacked_reader,) = (real,)
THREE = unpacked_reader.getenv("MOLTBOT_FLAG")

precise_reader, fake_reader = (real, client)
FOUR = precise_reader.getenv("MOLTBOT_FLAG")
NOT_ENVIRONMENT = fake_reader.getenv("MOLTBOT_FLAG")

selected_reader = (real, client)[0]
FIVE = selected_reader.getenv("MOLTBOT_FLAG")

mapped_reader = {"reader": real}["reader"]
SIX = mapped_reader.getenv("MOLTBOT_FLAG")

and_reader = real and client
ALSO_NOT_ENVIRONMENT = and_reader.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/compound_aliases.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            6,
        )

    def test_environment_alias_contract_matches_python_static_container_selection(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/static_selection.py"
        ] = """import os

duplicate_reader = {"key": client, "key": os.getenv}["key"]
ONE = duplicate_reader("MOLTBOT_FLAG")

fake_duplicate_reader = {"key": os.getenv, "key": client}["key"]
NOT_ENVIRONMENT = fake_duplicate_reader("MOLTBOT_FLAG")

true_reader = [client, os.getenv][True]
TWO = true_reader("MOLTBOT_FLAG")

false_reader = (os.getenv, client)[False]
THREE = false_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/static_selection.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            3,
        )

    def test_environment_alias_contract_applies_static_selector_and_dict_merge_semantics(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/static_selection_edges.py"
        ] = """import os

ONE = (client, os.getenv)[-1]("MOLTBOT_FLAG")
TWO = {-1: os.getenv}[-1]("MOLTBOT_FLAG")
THREE = {"k" + "ey": os.getenv}["key"]("MOLTBOT_FLAG")
FOUR = ({**{"k": os.getenv}})["k"]("MOLTBOT_FLAG")
FIVE = ({"k": client, **{"k": os.getenv}})["k"]("MOLTBOT_FLAG")

NOT_ENVIRONMENT = [os.getenv, client][-1]("MOLTBOT_FLAG")
ALSO_NOT_ENVIRONMENT = ({"k": os.getenv, **{"k": client}})["k"](
    "MOLTBOT_FLAG"
)
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/static_selection_edges.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            5,
        )

    def test_environment_alias_contract_resolves_nested_and_sliced_static_containers(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/nested_static_selection.py"
        ] = """import os

matrix = [[os.getenv]]
ONE = matrix[0][0]("MOLTBOT_FLAG")

mappings = {"outer": {"k": os.getenv}}
TWO = mappings["outer"]["k"]("MOLTBOT_FLAG")

readers = [client, os.getenv]
THREE = readers[1:][0]("MOLTBOT_FLAG")
FOUR = readers[::-1][0]("MOLTBOT_FLAG")

dynamic_index = choose_index()
NOT_PROVEN = matrix[dynamic_index][0]("MOLTBOT_FLAG")
INVALID_INDEX = readers[99:][0]("MOLTBOT_FLAG")
NOT_ENVIRONMENT = [[client]][0][0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/nested_static_selection.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_tracks_extended_unpack_and_literal_iteration(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/extended_unpack.py"
        ] = """import os

prefix_reader, *prefix_rest = (os.getenv, client, client)
ONE = prefix_reader("MOLTBOT_FLAG")

*suffix_rest, suffix_reader = (client, client, os.getenv)
TWO = suffix_reader("MOLTBOT_FLAG")

head, *middle, final_reader = (client, client, os.getenv)
THREE = final_reader("MOLTBOT_FLAG")

possible_reader, unordered_other = {client, os.getenv}
UNORDERED = possible_reader("MOLTBOT_FLAG")

nested_reader, nested_other = (*{client, os.getenv},)
NESTED_UNORDERED = nested_reader("MOLTBOT_FLAG")

fake_head, *list_value = (client, os.getenv)
NOT_CALLABLE = list_value("MOLTBOT_FLAG")

for loop_reader in [os.getenv]:
    FOUR = loop_reader("MOLTBOT_FLAG")

VALUES = [reader("MOLTBOT_FLAG") for reader in (os.getenv,)]

for possible_reader in [client, os.getenv]:
    FIVE = possible_reader("MOLTBOT_FLAG")

for fake_reader in [client]:
    NOT_ENVIRONMENT = fake_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/extended_unpack.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            8,
        )

    def test_environment_alias_contract_retains_container_and_dict_key_provenance(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/container_provenance.py"
        ] = """import os

list_readers = [os.getenv]
ONE = list_readers[0]("MOLTBOT_FLAG")
tuple_readers = (os.getenv,)
TWO = tuple_readers[0]("MOLTBOT_FLAG")
dict_readers = {"k": os.getenv}
THREE = dict_readers["k"]("MOLTBOT_FLAG")

head, *starred_readers = (client, os.getenv)
FOUR = starred_readers[0]("MOLTBOT_FLAG")

for dict_key_reader in {os.getenv: None}:
    FIVE = dict_key_reader("MOLTBOT_FLAG")

VALUES = [reader("MOLTBOT_FLAG") for reader in {os.getenv: None}]

fake_readers = [client]
NOT_ENVIRONMENT = fake_readers[0]("MOLTBOT_FLAG")
for fake_key in {client: os.getenv}:
    ALSO_NOT_ENVIRONMENT = fake_key("MOLTBOT_FLAG")
NOT_CALLABLE = starred_readers("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/container_provenance.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            6,
        )

    def test_environment_alias_contract_reuses_bound_container_provenance_at_consumers(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/bound_container_consumers.py"
        ] = """import os

list_readers = [os.getenv]
for list_reader in list_readers:
    ONE = list_reader("MOLTBOT_FLAG")

list_alias = list_readers
for alias_reader in list_alias:
    TWO = alias_reader("MOLTBOT_FLAG")

dict_readers = {os.getenv: None}
for dict_reader in dict_readers:
    THREE = dict_reader("MOLTBOT_FLAG")

values = [client, os.getenv]
head, *starred_readers = values
FOUR = starred_readers[0]("MOLTBOT_FLAG")

mapping = {"k": os.getenv}
FIVE = ({**mapping})["k"]("MOLTBOT_FLAG")

fake_mapping = {"k": client}
SIX = ({**fake_mapping, "k": os.getenv})["k"]("MOLTBOT_FLAG")
NOT_ENVIRONMENT_MAPPING = ({**mapping, "k": client})["k"]("MOLTBOT_FLAG")

SEVEN = {1.5: os.getenv}[1.5]("MOLTBOT_FLAG")
EIGHT = {b"k": os.getenv}[b"k"]("MOLTBOT_FLAG")
NINE = {-1.5: os.getenv}[-1.5]("MOLTBOT_FLAG")
TEN = {(1, "k"): os.getenv}[(1, "k")]("MOLTBOT_FLAG")
ELEVEN = {...: os.getenv}[...]("MOLTBOT_FLAG")

if flag:
    replaced = [os.getenv]
    replaced = [client]
NOT_ENVIRONMENT = replaced[0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/bound_container_consumers.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            11,
        )

    def test_environment_alias_contract_tracks_pattern_and_with_bindings(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/implicit_bindings.py"
        ] = """import os
from os import getenv

match first:
    case os:
        NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

match second:
    case [getenv]:
        ALSO_NOT_ENVIRONMENT = getenv("MOLTBOT_FLAG")

match third:
    case {"reader": _, **os}:
        STILL_NOT_ENVIRONMENT = os.get("MOLTBOT_FLAG")

def pattern_scope(value):
    match value:
        case [*os]:
            return os.getenv("MOLTBOT_FLAG")

class PatternScope:
    match class_value:
        case os:
            NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

with manager() as os:
    WITH_NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

with manager() as (os, other):
    UNPACKED_WITH_NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

async def governed():
    import os
    async with async_manager() as os:
        return os.getenv("MOLTBOT_FLAG")

import os
match fourth:
    case 0:
        REAL_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")
    case os:
        pass
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/implicit_bindings.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            1,
        )

    def test_environment_alias_contract_persists_irrefutable_pattern_captures(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/pattern_persistence.py"
        ] = """import os

match first:
    case _ as os:
        pass
NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

def governed(value):
    import os
    match value:
        case _ as os:
            pass
    return os.getenv("MOLTBOT_FLAG")

class Governed:
    import os
    match value:
        case _ as os:
            pass
    NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

import os
match second:
    case _ as os if False:
        pass
    case 1:
        ALSO_NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

import os
match third:
    case [os]:
        pass
REAL_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/pattern_persistence.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            1,
        )

    def test_environment_alias_contract_indexes_type_alias_bindings(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/type_alias_bindings.py"
        ] = """import os
type os = int
NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")

def governed():
    import os
    type os = int
    return os.getenv("MOLTBOT_FLAG")

class Governed:
    import os
    type os = int
    NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/type_alias_bindings.py")

        findings = self._evaluate(files, configure=configure)

        self.assertNotIn(
            "ENV_ALIAS_DIRECT_READ", {finding.rule_id for finding in findings}
        )

    def test_environment_alias_contract_models_type_alias_parameter_scope(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/type_alias_parameters.py"
        ] = """import os

type Shadowed[os] = os.getenv("MOLTBOT_FLAG")
type Governed[other] = os.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/type_alias_parameters.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            1,
        )

    def test_environment_alias_contract_applies_effective_keyword_mapping(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/keyword_mapping.py"
        ] = """import os

NOT_A_KEY = os.getenv(**{"key": "MOLTBOT_FLAG", "key": "PATH"})
ALSO_NOT_A_KEY = os.getenv(**{"key": "MOLTBOT_FLAG", **{"key": "PATH"}})
ONE = os.getenv(**{"key": "PATH", "key": "MOLTBOT_FLAG"})
TWO = os.getenv(**{"key": "PATH", **{"key": "MOLTBOT_FLAG"}})
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/keyword_mapping.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            2,
        )

    def test_environment_alias_contract_does_not_resurrect_deleted_or_shadowed_names(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/deleted_global.py"
        ] = """import os

def deleted_global():
    global os
    del os
    return os.getenv("MOLTBOT_FLAG")
"""
        files[
            "alpha/exception_shadow.py"
        ] = """import os

try:
    operation()
except Exception as os:
    NOT_ENVIRONMENT = os.getenv("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].extend(
                ["alpha/deleted_global.py", "alpha/exception_shadow.py"]
            )

        findings = self._evaluate(files, configure=configure)

        self.assertNotIn(
            "ENV_ALIAS_DIRECT_READ", {finding.rule_id for finding in findings}
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

    def test_environment_alias_registry_rejects_implicit_and_wildcard_rebinding(self):
        files = self._base_files()

        unsafe_suffixes = (
            """
match value:
    case LEGACY_MOLTBOT_ENV_KEYS:
        pass
""",
            """
with manager() as LEGACY_MOLTBOT_ENV_KEYS:
    pass
""",
            """
from fake import *
""",
        )
        for suffix in unsafe_suffixes:
            with self.subTest(suffix=suffix.strip().splitlines()[0]):
                files["alpha/env_aliases.py"] = (
                    environment_alias_owner_source() + suffix
                )
                findings = self._evaluate(
                    files,
                    configure=lambda policy: configure_environment_alias_contract(
                        policy
                    ),
                )
                self.assertIn(
                    "ENV_ALIAS_REGISTRY_UNREADABLE",
                    [finding.rule_id for finding in findings],
                )

        files["alpha/env_aliases.py"] = (
            "from fake import *\n" + environment_alias_owner_source()
        )
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

        files["alpha/env_aliases.py"] = (
            "frozenset = fake\ndel frozenset\n" + environment_alias_owner_source()
        )
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertNotIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

    def test_environment_alias_registry_models_type_alias_bindings(self):
        files = self._base_files()
        unsafe_sources = (
            "type frozenset = int\n" + environment_alias_owner_source(),
            environment_alias_owner_source() + "type LEGACY_MOLTBOT_ENV_KEYS = int\n",
        )
        for source in unsafe_sources:
            with self.subTest(source=source.splitlines()[0]):
                files["alpha/env_aliases.py"] = source
                findings = self._evaluate(
                    files,
                    configure=lambda policy: configure_environment_alias_contract(
                        policy
                    ),
                )
                self.assertIn(
                    "ENV_ALIAS_REGISTRY_UNREADABLE",
                    [finding.rule_id for finding in findings],
                )

        files["alpha/env_aliases.py"] = (
            environment_alias_owner_source() + "type frozenset = int\n"
        )
        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )
        self.assertNotIn(
            "ENV_ALIAS_REGISTRY_UNREADABLE",
            [finding.rule_id for finding in findings],
        )

    def test_environment_alias_registry_proves_balanced_builtin_restoration(self):
        files = self._base_files()
        safe_prefixes = (
            """try:
    work()
except Exception as frozenset:
    pass
""",
            """if flag:
    frozenset = fake
    del frozenset
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
def nested():
    frozenset = fake
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
if False:
    frozenset = fake
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
for frozenset in []:
    pass
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[frozenset for frozenset in []]
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[(frozenset := fake) for _ in []]
""",
        )
        for prefix in safe_prefixes:
            with self.subTest(prefix=prefix.splitlines()[0]):
                files["alpha/env_aliases.py"] = (
                    prefix + environment_alias_owner_source()
                )
                findings = self._evaluate(
                    files,
                    configure=lambda policy: configure_environment_alias_contract(
                        policy
                    ),
                )
                self.assertNotIn(
                    "ENV_ALIAS_REGISTRY_UNREADABLE",
                    [finding.rule_id for finding in findings],
                )

        unsafe_prefixes = (
            """frozenset = fake
if flag:
    del frozenset
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
finally:
    frozenset = fake
""",
        )
        for prefix in unsafe_prefixes:
            with self.subTest(prefix=prefix.splitlines()[0]):
                files["alpha/env_aliases.py"] = (
                    prefix + environment_alias_owner_source()
                )
                findings = self._evaluate(
                    files,
                    configure=lambda policy: configure_environment_alias_contract(
                        policy
                    ),
                )
                self.assertIn(
                    "ENV_ALIAS_REGISTRY_UNREADABLE",
                    [finding.rule_id for finding in findings],
                )

    def test_environment_alias_registry_rejects_unsound_try_delete_restoration(self):
        files = self._base_files()
        unsafe_prefixes = (
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
frozenset = fake
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
if flag:
    frozenset = fake
""",
            """try:
    frozenset = fake
    raise RuntimeError
    del frozenset
except Exception:
    pass
""",
            """try:
    frozenset = fake
    work()
    del frozenset
except Exception:
    pass
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    frozenset = fake
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
for frozenset in values:
    pass
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
if (frozenset := fake):
    pass
""",
        )
        for prefix in unsafe_prefixes:
            with self.subTest(prefix=prefix):
                files["alpha/env_aliases.py"] = (
                    prefix + environment_alias_owner_source()
                )
                findings = self._evaluate(
                    files,
                    configure=lambda policy: configure_environment_alias_contract(
                        policy
                    ),
                )
                self.assertIn(
                    "ENV_ALIAS_REGISTRY_UNREADABLE",
                    [finding.rule_id for finding in findings],
                )

    def test_environment_alias_registry_applies_exception_target_exit_cleanup(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = (
            """try:
    work()
except Exception as frozenset:
    frozenset = fake
"""
            + environment_alias_owner_source()
        )

        findings = self._evaluate(
            files,
            configure=lambda policy: configure_environment_alias_contract(policy),
        )

        self.assertNotIn(
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
