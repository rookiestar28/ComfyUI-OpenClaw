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

expanded_next_generator = ((expanded_next_reader := os.getenv) for _ in [1])
expanded_next_generator.__next__(*[])
SIX = expanded_next_reader("MOLTBOT_FLAG")

expanded_send_generator = ((expanded_send_reader := os.getenv) for _ in [1])
expanded_send_step = expanded_send_generator.send
expanded_send_step(*[None])
SEVEN = expanded_send_reader("MOLTBOT_FLAG")

selected_next_generator = ((selected_next_reader := os.getenv) for _ in [1])
selected_steps = [selected_next_generator.__next__]
selected_steps[0]()
EIGHT = selected_next_reader("MOLTBOT_FLAG")

selected_alias_generator = ((selected_alias_reader := os.getenv) for _ in [1])
selected_alias_step = selected_alias_generator.__next__
selected_alias_steps = [selected_alias_step]
selected_alias_steps[0]()
NINE = selected_alias_reader("MOLTBOT_FLAG")

bare_lazy_generator = ((bare_lazy_reader := os.getenv) for _ in [1])
bare_lazy = iter
bare_lazy(bare_lazy_generator)
BARE_LAZY_NOT_PROVEN = bare_lazy_reader("MOLTBOT_FLAG")

qualified_alias_generator = ((qualified_alias_reader := os.getenv) for _ in [1])
qualified_lazy = builtins.iter
qualified_lazy(qualified_alias_generator)
QUALIFIED_ALIAS_LAZY_NOT_PROVEN = qualified_alias_reader("MOLTBOT_FLAG")

imported_alias_generator = ((imported_alias_reader := os.getenv) for _ in [1])
imported_lazy_alias = lazy_iter
imported_lazy_alias(imported_alias_generator)
IMPORTED_ALIAS_LAZY_NOT_PROVEN = imported_alias_reader("MOLTBOT_FLAG")

lazy_alias_chain_generator = ((lazy_alias_chain_reader := os.getenv) for _ in [1])
lazy_alias_one = iter
lazy_alias_two = lazy_alias_one
lazy_alias_two(lazy_alias_chain_generator)
LAZY_ALIAS_CHAIN_NOT_PROVEN = lazy_alias_chain_reader("MOLTBOT_FLAG")

rebound_lazy_generator = ((rebound_lazy_reader := os.getenv) for _ in [1])
rebound_lazy = iter
rebound_lazy = consume
rebound_lazy(rebound_lazy_generator)
TEN = rebound_lazy_reader("MOLTBOT_FLAG")

starred_selected_generator = ((starred_selected_reader := os.getenv) for _ in [1])
starred_selected_steps = [*[starred_selected_generator.__next__]]
starred_selected_steps[0]()
ELEVEN = starred_selected_reader("MOLTBOT_FLAG")

opaque_expansion_generator = ((opaque_expansion_reader := os.getenv) for _ in [1])
opaque_expansion_step = opaque_expansion_generator.__next__
opaque_expansion_step(*unknown_args)
TWELVE = opaque_expansion_reader("MOLTBOT_FLAG")

opaque_keyword_generator = ((opaque_keyword_reader := os.getenv) for _ in [1])
opaque_keyword_generator.__next__(**unknown_kwargs)
THIRTEEN = opaque_keyword_reader("MOLTBOT_FLAG")

starred_argument_generator = ((starred_argument_reader := os.getenv) for _ in [1])
consume(*[starred_argument_generator])
FOURTEEN = starred_argument_reader("MOLTBOT_FLAG")

selected_argument_generator = ((selected_argument_reader := os.getenv) for _ in [1])
selected_argument_generators = [selected_argument_generator]
consume(selected_argument_generators[0])
FIFTEEN = selected_argument_reader("MOLTBOT_FLAG")

keyword_argument_generator = ((keyword_argument_reader := os.getenv) for _ in [1])
consume(**{"value": keyword_argument_generator})
SIXTEEN = keyword_argument_reader("MOLTBOT_FLAG")

lazy_starred_generator = ((lazy_starred_reader := os.getenv) for _ in [1])
iter(*[lazy_starred_generator])
LAZY_STARRED_NOT_PROVEN = lazy_starred_reader("MOLTBOT_FLAG")

invalid_expansion_generator = ((invalid_expansion_reader := os.getenv) for _ in [1])
invalid_expansion_generator.__next__(*[1])
INVALID_EXPANSION_NOT_PROVEN = invalid_expansion_reader("MOLTBOT_FLAG")

guaranteed_invalid_next = ((invalid_next_reader := os.getenv) for _ in [1])
guaranteed_invalid_next.__next__(*unknown_args, 1)
INVALID_NEXT_NOT_PROVEN = invalid_next_reader("MOLTBOT_FLAG")

guaranteed_invalid_send = ((invalid_send_reader := os.getenv) for _ in [1])
guaranteed_invalid_send.send(*unknown_args, 1)
INVALID_SEND_NOT_PROVEN = invalid_send_reader("MOLTBOT_FLAG")

guaranteed_invalid_keyword = ((invalid_keyword_reader := os.getenv) for _ in [1])
guaranteed_invalid_keyword.__next__(**unknown_kwargs, named=1)
INVALID_KEYWORD_NOT_PROVEN = invalid_keyword_reader("MOLTBOT_FLAG")

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
            16,
        )

    def test_environment_alias_contract_exposes_members_of_unresolved_starred_iterables(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/unresolved_starred_members.py"
        ] = """import os

flag = choose()
unknown = []

ONE = [*(unknown or [os.getenv])][0]("MOLTBOT_FLAG")

values = [os.getenv] if flag else []
TWO = [*values][0]("MOLTBOT_FLAG")

values2 = flag and [os.getenv]
THREE = [*values2][0]("MOLTBOT_FLAG")

FOUR = ([client] if flag else [os.getenv])[0]("MOLTBOT_FLAG")
FIVE = (flag and [os.getenv])[0]("MOLTBOT_FLAG")

client_values = [client] if flag else []
NOT_ENVIRONMENT = [*client_values][0]("MOLTBOT_FLAG")

dead_values = [os.getenv] if False else [client]
DEAD_NOT_ENVIRONMENT = [*dead_values][0]("MOLTBOT_FLAG")
DEAD_SELECTION = ([os.getenv] if False else [client])[0]("MOLTBOT_FLAG")
NESTED_SHORT_CIRCUIT_NOT_ENVIRONMENT = (
    (flag and [os.getenv]) and [client]
)[0]("MOLTBOT_FLAG")
nested_name_values = flag and [os.getenv]
NESTED_NAME_SHORT_CIRCUIT_NOT_ENVIRONMENT = (
    nested_name_values and [client]
)[0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/unresolved_starred_members.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            5,
        )

    def test_environment_alias_contract_tracks_conditional_bound_generator_consumers(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/conditional_bound_consumers.py"
        ] = """import os

flag = choose()

g1 = ((r1 := os.getenv) for _ in [1])
(flag and g1.__next__)()
ONE = r1("MOLTBOT_FLAG")

g2 = ((r2 := os.getenv) for _ in [1])
(g2.__next__ if flag else client)()
TWO = r2("MOLTBOT_FLAG")

g3 = ((r3 := os.getenv) for _ in [1])
step = g3.__next__ if flag else client
step()
THREE = r3("MOLTBOT_FLAG")

g4 = ((r4 := os.getenv) for _ in [1])
steps = [*(flag and [g4.__next__])]
steps[0]()
FOUR = r4("MOLTBOT_FLAG")

g5 = ((r5 := os.getenv) for _ in [1])
([g5.__next__] if flag else [client])[0]()
FIVE = r5("MOLTBOT_FLAG")

g6 = ((r6 := os.getenv) for _ in [1])
(flag and [g6.__next__])[0]()
SIX = r6("MOLTBOT_FLAG")

dead = ((dead_reader := os.getenv) for _ in [1])
dead_step = dead.__next__ if False else client
dead_step()
DEAD_NOT_PROVEN = dead_reader("MOLTBOT_FLAG")

dead_container = ((dead_container_reader := os.getenv) for _ in [1])
([dead_container.__next__] if False else [client])[0]()
DEAD_CONTAINER_NOT_PROVEN = dead_container_reader("MOLTBOT_FLAG")

invalid = ((invalid_reader := os.getenv) for _ in [1])
invalid_step = invalid.send if flag else client
invalid_step(1)
INVALID_NOT_PROVEN = invalid_reader("MOLTBOT_FLAG")

lazy = ((lazy_reader := os.getenv) for _ in [1])
lazy_step = iter if flag else iter
lazy_step(lazy)
LAZY_NOT_PROVEN = lazy_reader("MOLTBOT_FLAG")

not_callable = ((not_callable_reader := os.getenv) for _ in [1])
method_container = [not_callable.__next__]
method_container()
CONTAINER_NOT_PROVEN = not_callable_reader("MOLTBOT_FLAG")

conditional_not_callable = ((conditional_container_reader := os.getenv) for _ in [1])
conditional_method_container = (
    [conditional_not_callable.__next__] if flag else [client]
)
conditional_method_container()
CONDITIONAL_CONTAINER_NOT_PROVEN = conditional_container_reader("MOLTBOT_FLAG")

nested_short_circuit = ((nested_reader := os.getenv) for _ in [1])
((flag and [nested_short_circuit.__next__]) and [client])[0]()
NESTED_SHORT_CIRCUIT_NOT_PROVEN = nested_reader("MOLTBOT_FLAG")

nested_name = ((nested_name_reader := os.getenv) for _ in [1])
nested_method_values = flag and [nested_name.__next__]
(nested_method_values and [client])[0]()
NESTED_NAME_SHORT_CIRCUIT_NOT_PROVEN = nested_name_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/conditional_bound_consumers.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            6,
        )

    def test_environment_alias_contract_exposes_executed_starred_iterable_producers(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/starred_iterable_producers.py"
        ] = """import os

ONE = [*(os.getenv for _ in [1])][0]("MOLTBOT_FLAG")
TWO = [*[os.getenv for _ in [1]]][0]("MOLTBOT_FLAG")
THREE = [*{os.getenv for _ in [1]}][0]("MOLTBOT_FLAG")
FOUR = [*iter([os.getenv])][0]("MOLTBOT_FLAG")
FIVE = [*{os.getenv: value for value in [1]}][0]("MOLTBOT_FLAG")

g1 = ((r1 := os.getenv) for _ in [1])
steps1 = [*(g1.__next__ for _ in [1])]
steps1[0]()
SIX = r1("MOLTBOT_FLAG")

g2 = ((r2 := os.getenv) for _ in [1])
steps2 = [*iter([g2.__next__])]
steps2[0]()
SEVEN = r2("MOLTBOT_FLAG")

g3 = ((r3 := os.getenv) for _ in [1])
steps3 = [*{g3.__next__: value for value in [1]}]
steps3[0]()
EIGHT = r3("MOLTBOT_FLAG")

EMPTY = [*(os.getenv for _ in [])]
FILTERED = [*(os.getenv for _ in [1] if False)]
empty_values = []
EMPTY_ALIAS = [*(os.getenv for _ in empty_values)]
false_filter = False
FILTERED_ALIAS = [*(os.getenv for _ in [1] if false_filter)]

TWO_ARGUMENT_ITER = [*iter(os.getenv, sentinel)][0]("MOLTBOT_FLAG")
iter = consume
SHADOWED_ITER = [*iter([os.getenv])][0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/starred_iterable_producers.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            8,
        )

    def test_environment_alias_contract_preserves_starred_display_truth_constraints(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/starred_truth_constraints.py"
        ] = """import os

members = [os.getenv]

NEGATIVE = ([*members] and [client])[0]("MOLTBOT_FLAG")
ONE = ([*members] or [client])[0]("MOLTBOT_FLAG")

g1 = ((r1 := os.getenv) for _ in [1])
members1 = [g1.__next__]
([*members1] and [client])[0]()
NEGATIVE_BOUND = r1("MOLTBOT_FLAG")

g2 = ((r2 := os.getenv) for _ in [1])
members2 = [g2.__next__]
([*members2] or [client])[0]()
TWO = r2("MOLTBOT_FLAG")

dynamic_members = choose()
THREE = ([*dynamic_members] and [os.getenv])[0]("MOLTBOT_FLAG")
FOUR = ([*dynamic_members] or [os.getenv])[0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/starred_truth_constraints.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_resolves_exact_boolean_name_aliases(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/exact_boolean_aliases.py"
        ] = """import os

false_flag = False
values = [os.getenv] if false_flag else [client]
NEGATIVE_ONE = values[0]("MOLTBOT_FLAG")

true_flag = True
values2 = [client] if true_flag else [os.getenv]
NEGATIVE_TWO = values2[0]("MOLTBOT_FLAG")

unknown_flag = choose()
values3 = [os.getenv] if unknown_flag else [client]
ONE = values3[0]("MOLTBOT_FLAG")

rebound_flag = False
rebound_flag = choose()
values4 = [os.getenv] if rebound_flag else [client]
TWO = values4[0]("MOLTBOT_FLAG")

if choose():
    branch_flag = False
else:
    branch_flag = True
values5 = [os.getenv] if branch_flag else [client]
THREE = values5[0]("MOLTBOT_FLAG")

dead_g1 = ((dead_r1 := os.getenv) for _ in [1])
(dead_g1.__next__ if false_flag else client)()
NEGATIVE_BOUND_ONE = dead_r1("MOLTBOT_FLAG")

dead_g2 = ((dead_r2 := os.getenv) for _ in [1])
(client if true_flag else dead_g2.__next__)()
NEGATIVE_BOUND_TWO = dead_r2("MOLTBOT_FLAG")

live_g = ((live_reader := os.getenv) for _ in [1])
(live_g.__next__ if unknown_flag else client)()
FOUR = live_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/exact_boolean_aliases.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_preserves_class_comprehension_iterable_scope(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/class_comprehension_iterables.py"
        ] = """import os

module_readers = [os.getenv]
MODULE_VALUE = [reader("MOLTBOT_FLAG") for reader in module_readers]

def function_scope():
    readers = [os.getenv]
    return [reader("MOLTBOT_FLAG") for reader in readers]

class Governed:
    readers = [os.getenv]
    nested_readers = [[os.getenv]]
    LIST_VALUE = [reader("MOLTBOT_FLAG") for reader in readers]
    SET_VALUE = {reader("MOLTBOT_FLAG") for reader in readers}
    DICT_VALUE = {reader("MOLTBOT_FLAG"): True for reader in readers}
    GEN_VALUE = tuple(reader("MOLTBOT_FLAG") for reader in readers)
    STAR_VALUE = [*(reader for reader in readers)][0]("MOLTBOT_FLAG")
    ITER_VALUE = [*iter(readers)][0]("MOLTBOT_FLAG")
    NESTED_VALUE = [
        reader("MOLTBOT_FLAG")
        for group in nested_readers
        for reader in group
    ]

class ReverseControls:
    readers = [client]
    NOT_ENVIRONMENT = [reader("MOLTBOT_FLAG") for reader in readers]
    class_reader = os.getenv
    CLASS_NAMESPACE_IS_SKIPPED = [class_reader("MOLTBOT_FLAG") for _ in [1]]
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/class_comprehension_iterables.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            9,
        )

    def test_environment_alias_contract_activates_generators_in_eager_class_regions(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/eager_class_generator_consumers.py"
        ] = """import os

direct = ((direct_reader := os.getenv) for _ in [1])

class DirectConsumer:
    direct.__next__()

ONE = direct_reader("MOLTBOT_FLAG")

defaulted = ((default_reader := os.getenv) for _ in [1])

class DefaultConsumer:
    def method(value=defaulted.__next__()):
        return value

TWO = default_reader("MOLTBOT_FLAG")

nested = ((nested_reader := os.getenv) for _ in [1])

class OuterConsumer:
    class InnerConsumer:
        nested.__next__()

THREE = nested_reader("MOLTBOT_FLAG")

def local_scope():
    local = ((local_reader := os.getenv) for _ in [1])

    class LocalConsumer:
        local.__next__()

    return local_reader("MOLTBOT_FLAG")

deferred = ((deferred_reader := os.getenv) for _ in [1])

class DeferredControl:
    def method(self):
        deferred.__next__()

DEFERRED_NOT_ACTIVATED = deferred_reader("MOLTBOT_FLAG")

not_environment = ((fake_reader := client) for _ in [1])

class NonReaderControl:
    not_environment.__next__()

NOT_ENVIRONMENT = fake_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append(
                "alpha/eager_class_generator_consumers.py"
            )

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            4,
        )

    def test_environment_alias_contract_preserves_canonical_iter_loop_members(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/canonical_iter_loop_members.py"
        ] = """import os
from builtins import iter as builtin_iter

readers = [os.getenv]

for reader in iter(readers):
    reader("MOLTBOT_FLAG")

MODULE_LIST = [reader("MOLTBOT_FLAG") for reader in iter(readers)]
MODULE_SET = {reader("MOLTBOT_FLAG") for reader in builtin_iter(readers)}
assigned_iter = builtin_iter
MODULE_DICT = {
    reader("MOLTBOT_FLAG"): True for reader in assigned_iter(readers)
}

def function_scope():
    local_readers = [os.getenv]
    return tuple(
        reader("MOLTBOT_FLAG") for reader in iter(local_readers)
    )

class Governed:
    readers = [os.getenv]
    LIST_VALUE = [reader("MOLTBOT_FLAG") for reader in iter(readers)]
    SET_VALUE = {reader("MOLTBOT_FLAG") for reader in iter(readers)}
    DICT_VALUE = {
        reader("MOLTBOT_FLAG"): True for reader in iter(readers)
    }
    GEN_VALUE = tuple(
        reader("MOLTBOT_FLAG") for reader in iter(readers)
    )

bound_generator = ((bound_reader := os.getenv) for _ in [1])
for step in iter([bound_generator.__next__]):
    step()
BOUND_VALUE = bound_reader("MOLTBOT_FLAG")

selected_generator = ((selected_reader := os.getenv) for _ in [1])
SELECTED_STEPS = [
    step() for step in iter([selected_generator.__next__])
]
SELECTED_VALUE = selected_reader("MOLTBOT_FLAG")

for not_reader in iter([client]):
    not_reader("MOLTBOT_FLAG")

def shadowed_iter():
    iter = client
    return [reader("MOLTBOT_FLAG") for reader in iter([os.getenv])]

sentinel = object()
for two_arg_reader in iter(lambda: os.getenv, sentinel):
    two_arg_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/canonical_iter_loop_members.py")

        findings = self._evaluate(files, configure=configure)

        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]
        self.assertEqual(
            len(direct_reads),
            11,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
        )

    def test_environment_alias_contract_correlates_invoked_closure_consumers(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/invoked_closure_consumers.py"
        ] = """import os

def direct_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    return inner()

def aliased_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        step = generator.__next__
        step()
        return reader("MOLTBOT_FLAG")

    return inner()

def selected_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        [generator.__next__][0]()
        return reader("MOLTBOT_FLAG")

    return inner()

def class_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        class Consumer:
            generator.__next__()
        return reader("MOLTBOT_FLAG")

    return inner()

def aliased_call_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    call_inner = inner
    return call_inner()

def selected_call_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    return [inner][0]()

def conditional_rebind_outer(flag):
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    if flag:
        inner = client
    return inner()

def invoked_lambda_outer():
    generator = ((reader := os.getenv) for _ in [1])
    action = lambda: (generator.__next__(), reader("MOLTBOT_FLAG"))
    return action()

def never_called_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def deferred():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    return None

def no_consumer_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        return reader("MOLTBOT_FLAG")

    return inner()

def rebound_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator = iter(())
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    return inner()

def non_reader_outer():
    generator = ((reader := client) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    return inner()

def before_consumer_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        value = reader("MOLTBOT_FLAG")
        generator.__next__()
        return value

    return inner()

def definitely_rebound_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    inner = client
    return inner()

def deferred_lambda_outer():
    generator = ((reader := os.getenv) for _ in [1])
    action = lambda: (generator.__next__(), reader("MOLTBOT_FLAG"))
    return None
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/invoked_closure_consumers.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            8,
        )

    def test_environment_alias_contract_preserves_materialized_comprehension_members(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/materialized_comprehension_members.py"
        ] = """import os

plain_readers = [reader for reader in [os.getenv]]
ONE = plain_readers[0]("MOLTBOT_FLAG")

iter_readers = [reader for reader in iter([os.getenv])]
TWO = iter_readers[0]("MOLTBOT_FLAG")

THREE = [reader for reader in [os.getenv]][0]("MOLTBOT_FLAG")

starred_readers = [*(reader for reader in iter([os.getenv]))]
FOUR = starred_readers[0]("MOLTBOT_FLAG")

literal_readers = [os.getenv]
FIVE = literal_readers[0]("MOLTBOT_FLAG")

mapping = {"reader": reader for reader in [os.getenv]}
SIX = mapping["reader"]("MOLTBOT_FLAG")

(list_reader,) = [reader for reader in [os.getenv]]
SEVEN = list_reader("MOLTBOT_FLAG")

(set_reader,) = {reader for reader in [os.getenv]}
EIGHT = set_reader("MOLTBOT_FLAG")

empty_readers = [reader for reader in []]
EMPTY = empty_readers[0]("MOLTBOT_FLAG")

filtered_readers = [reader for reader in [os.getenv] if False]
FILTERED = filtered_readers[0]("MOLTBOT_FLAG")

invalid_readers = [reader for reader in [os.getenv]]
INVALID = invalid_readers[1]("MOLTBOT_FLAG")

def shadowed_reader():
    os = client
    readers = [reader for reader in [os.getenv]]
    return readers[0]("MOLTBOT_FLAG")

bare_container = [reader for reader in [os.getenv]]
BARE = bare_container("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append(
                "alpha/materialized_comprehension_members.py"
            )

        findings = self._evaluate(files, configure=configure)
        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]

        self.assertEqual(
            len(direct_reads),
            8,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
        )

    def test_environment_alias_contract_correlates_materialized_comprehension_bindings(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/correlated_materialized_comprehensions.py"
        ] = """import os

def client(key):
    return None

readers = [reader for reader in [client, os.getenv]]
SAFE_INDEX = readers[0]("MOLTBOT_FLAG")
LIVE_INDEX = readers[1]("MOLTBOT_FLAG")

iter_readers = [reader for reader in iter([client, os.getenv])]
SAFE_ITER_INDEX = iter_readers[0]("MOLTBOT_FLAG")
LIVE_ITER_INDEX = iter_readers[1]("MOLTBOT_FLAG")

safe_unpack, live_unpack = [reader for reader in [client, os.getenv]]
SAFE_UNPACK = safe_unpack("MOLTBOT_FLAG")
LIVE_UNPACK = live_unpack("MOLTBOT_FLAG")

mapping = {
    key: value
    for key, value in [("safe", client), ("reader", os.getenv)]
}
SAFE_DICT = mapping["safe"]("MOLTBOT_FLAG")
LIVE_DICT = mapping["reader"]("MOLTBOT_FLAG")

safe_last = {
    key: value
    for key, value in [("reader", os.getenv), ("reader", client)]
}
SAFE_LAST = safe_last["reader"]("MOLTBOT_FLAG")

live_last = {
    key: value
    for key, value in [("reader", client), ("reader", os.getenv)]
}
LIVE_LAST = live_last["reader"]("MOLTBOT_FLAG")

dynamic_key = "reader"
dynamic_mapping = {
    key: value
    for key, value in [(dynamic_key, os.getenv)]
}
LIVE_DYNAMIC_KEY = dynamic_mapping["reader"]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append(
                "alpha/correlated_materialized_comprehensions.py"
            )

        findings = self._evaluate(files, configure=configure)
        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]

        self.assertEqual(
            len(direct_reads),
            6,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
        )

    def test_environment_alias_contract_consumes_bound_materialized_members(self):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/bound_materialized_members.py"
        ] = """import os

plain_generator = ((plain_reader := os.getenv) for _ in [1])
plain_steps = [step for step in [plain_generator.__next__]]
plain_steps[0]()
ONE = plain_reader("MOLTBOT_FLAG")

iter_generator = ((iter_reader := os.getenv) for _ in [1])
iter_steps = [step for step in iter([iter_generator.__next__])]
iter_steps[0]()
TWO = iter_reader("MOLTBOT_FLAG")

starred_generator = ((starred_reader := os.getenv) for _ in [1])
starred_steps = [*(step for step in iter([starred_generator.__next__]))]
starred_steps[0]()
THREE = starred_reader("MOLTBOT_FLAG")

literal_generator = ((literal_reader := os.getenv) for _ in [1])
literal_steps = [literal_generator.__next__]
literal_steps[0]()
FOUR = literal_reader("MOLTBOT_FLAG")

deferred_generator = ((deferred_reader := os.getenv) for _ in [1])
deferred_steps = [step for step in [deferred_generator.__next__]]
DEFERRED = deferred_reader("MOLTBOT_FLAG")

filtered_generator = ((filtered_reader := os.getenv) for _ in [1])
filtered_steps = [step for step in [filtered_generator.__next__] if False]
FILTERED = filtered_reader("MOLTBOT_FLAG")

invalid_generator = ((invalid_reader := os.getenv) for _ in [1])
invalid_steps = [step for step in [invalid_generator.__next__]]
invalid_steps[1]()
INVALID = invalid_reader("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/bound_materialized_members.py")

        findings = self._evaluate(files, configure=configure)
        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]

        self.assertEqual(
            len(direct_reads),
            4,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
        )

    def test_environment_alias_contract_tracks_transitive_deferred_execution(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/transitive_deferred_execution.py"
        ] = """import os

def direct_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return inner()

def multilevel_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    def middle():
        return inner()
    return middle()

def immediate_lambda_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return (lambda: inner())()

def eager_listcomp_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return [inner() for _ in [1]]

def consumed_genexpr_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return list(inner() for _ in [1])

def deferred_genexpr_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return (inner() for _ in [1])

def never_called_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return None

def definitely_replaced_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    inner = client
    return (lambda: inner())()

def read_before_consume_outer():
    generator = ((reader := os.getenv) for _ in [1])
    def inner():
        value = reader("MOLTBOT_FLAG")
        generator.__next__()
        return value
    def middle():
        return inner()
    return middle()

def nonreader_outer():
    generator = ((reader := client) for _ in [1])
    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")
    return (lambda: inner())()
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/transitive_deferred_execution.py")

        findings = self._evaluate(files, configure=configure)
        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]

        self.assertEqual(
            len(direct_reads),
            5,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
        )

    def test_environment_alias_contract_checks_every_reachable_execution_entry(
        self,
    ):
        files = self._base_files()
        files["alpha/env_aliases.py"] = environment_alias_owner_source()
        files[
            "alpha/multiple_execution_entries.py"
        ] = """import os

def client():
    return None

def later_live_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    saved = inner

    def middle():
        return inner()

    inner = client
    middle()
    inner = saved
    return middle()

def first_live_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    def middle():
        return inner()

    middle()
    inner = client
    return middle()

def never_live_outer():
    generator = ((reader := os.getenv) for _ in [1])

    def inner():
        generator.__next__()
        return reader("MOLTBOT_FLAG")

    def middle():
        return inner()

    inner = client
    return middle()
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/multiple_execution_entries.py")

        findings = self._evaluate(files, configure=configure)
        direct_reads = [
            finding
            for finding in findings
            if finding.rule_id == "ENV_ALIAS_DIRECT_READ"
        ]

        self.assertEqual(
            len(direct_reads),
            2,
            msg="\n".join(
                f"{finding.path}:{finding.line}: {finding.subject}"
                for finding in direct_reads
            ),
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
FIVE = [*[], os.getenv][0]("MOLTBOT_FLAG")
SIX = (*(), os.getenv)[0]("MOLTBOT_FLAG")
SEVEN = [*dynamic_readers, os.getenv][0]("MOLTBOT_FLAG")
STARRED_NOT_ENVIRONMENT = [*[], client][0]("MOLTBOT_FLAG")
EIGHT = (*[*(), os.getenv],)[0]("MOLTBOT_FLAG")
"""

        def configure(policy):
            configure_environment_alias_contract(policy)
            policy["domains"]["alpha"].append("alpha/nested_static_selection.py")

        findings = self._evaluate(files, configure=configure)

        self.assertEqual(
            [finding.rule_id for finding in findings].count("ENV_ALIAS_DIRECT_READ"),
            8,
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
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
empty = []
for frozenset in empty:
    pass
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
for frozenset in range(0):
    pass
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
g = ((frozenset := fake) for _ in [1])
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
g = ((frozenset := fake) for _ in [1])
iter(g)
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[(lambda: (frozenset := fake)) for _ in [1]]
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[(frozenset := fake) for _ in [1] if False]
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[(frozenset := fake) for _ in [1] if 0]
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
g = ((frozenset := fake) for _ in [1])
lazy_one = iter
lazy_two = lazy_one
lazy_two(g)
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
False and (frozenset := fake)
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
value = (frozenset := fake) if False else 1
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
[1 for _ in [1] if False and (frozenset := fake)]
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
class Holder:
    frozenset = fake
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
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
True and (frozenset := fake)
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
callback = lambda value=(frozenset := fake): value
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
def callback(value=(frozenset := fake)):
    return value
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
class Holder:
    global frozenset
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
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
g = ((frozenset := fake) for _ in [1])
next(g)
""",
            """frozenset = fake
try:
    del frozenset
except Exception:
    pass
g = ((frozenset := fake) for _ in [1])
steps = [*[g.__next__]]
steps[0](*[])
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
