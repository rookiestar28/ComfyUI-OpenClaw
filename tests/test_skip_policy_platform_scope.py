"""Skip budget scoped to declared platform-exclusive tests, with canonical skip ids.

A Windows-only case cannot run on Linux, so its skip there is a platform fact rather than
missing coverage. Such tests are declared per test id in ``tests/skip_policy.json`` and are
excused only off their declared platforms; every other skip still counts.
"""

import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests" / "skip_policy.json"
GOVERNANCE_SCRIPT = ROOT / "scripts" / "verify_test_debt_governance.py"

HARDENING = "tests.test_runtime_process_spawn_hardening"
WINDOWS_ONLY_HARDENING_CASES = frozenset(
    {
        f"{HARDENING}.ToolRunnerPathBoundaryTests.test_junction_inside_allowed_directory_pointing_outside_is_rejected",
        f"{HARDENING}.ToolRunnerPathBoundaryTests.test_case_variant_of_allowed_directory_is_inside_on_windows",
        f"{HARDENING}.ToolRunnerPathBoundaryTests.test_other_drive_is_outside_on_windows",
        f"{HARDENING}.ToolRunnerExecutableResolutionTests.test_windows_batch_target_is_refused",
    }
)
WECHAT_AES = "tests.connector.platforms.test_r82_wechat_parity.TestAESDecrypt"
# Canonical form of the six skips in the Linux unit-tests job of the v1.3.0 CI run.
LINUX_CI_SKIPS = sorted(WINDOWS_ONLY_HARDENING_CASES) + [
    f"{WECHAT_AES}.test_round_trip_decrypt",
    f"{WECHAT_AES}.test_wrong_app_id_raises",
]

DECLARED = "tests.test_example.ExampleTests.test_windows_only"
FAKE_MODULE = "test_fake_platform_scope_module"


def _load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_unittests_platform_scope", ROOT / "scripts" / "run_unittests.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load run_unittests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skipping_case(module_name: str) -> type:
    class Case(unittest.TestCase):
        def test_skipped(self) -> None:
            self.skipTest("not on this platform")

        def test_subtest_skipped(self) -> None:
            with self.subTest(variant="a"):
                self.skipTest("not on this platform either")

    Case.__module__ = module_name
    Case.__qualname__ = "Case"
    return Case


def _fake_module(name: str, file_path: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = str(file_path)
    return module


def _run_case(case_class: type, method: str) -> unittest.TestResult:
    result = unittest.TestResult()
    case_class(method).run(result)
    return result


class PlatformExclusiveBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()
        self.exclusive = {DECLARED: frozenset({"win32"})}

    def evaluate(self, skips, *, platform, max_skipped=0, no_skip_modules=()):
        return self.runner._evaluate_skip_policy(
            skips=skips,
            max_skipped=max_skipped,
            no_skip_modules=list(no_skip_modules),
            platform_exclusive_tests=self.exclusive,
            platform=platform,
        )

    def test_off_platform_skip_of_a_declared_test_is_excused(self) -> None:
        self.assertEqual(self.evaluate([(DECLARED, "windows")], platform="linux"), [])

    def test_on_platform_skip_of_a_declared_test_counts(self) -> None:
        violations = self.evaluate(
            [(DECLARED, "junctions unavailable")], platform="win32"
        )

        self.assertEqual(len(violations), 1)
        self.assertIn("skip budget exceeded: skipped=1 > max_skipped=0", violations[0])

    def test_undeclared_skip_counts(self) -> None:
        violations = self.evaluate(
            [
                (DECLARED, "windows"),
                ("tests.test_example.ExampleTests.test_other", "dep"),
            ],
            platform="linux",
        )

        self.assertEqual(len(violations), 1)
        self.assertIn("skipped=1 > max_skipped=0", violations[0])

    def test_an_excused_skip_in_a_no_skip_module_still_fails(self) -> None:
        violations = self.evaluate(
            [(DECLARED, "windows")],
            platform="linux",
            max_skipped=5,
            no_skip_modules=["tests.test_example"],
        )

        self.assertEqual(len(violations), 1)
        self.assertIn("no-skip suite skipped", violations[0])

    def test_partition_separates_counted_and_excused_skips(self) -> None:
        other = ("tests.test_example.ExampleTests.test_other", "dep")

        counted, excused = self.runner._partition_skips(
            [(DECLARED, "windows"), other],
            platform_exclusive_tests=self.exclusive,
            platform="linux",
        )

        self.assertEqual(counted, [other])
        self.assertEqual(excused, [(DECLARED, "windows")])

    def test_report_lists_counted_and_excused_skips(self) -> None:
        other = ("tests.test_example.ExampleTests.test_other", "dep")
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            self.runner._write_skip_report(
                str(report_path),
                tests_run=3,
                duration_sec=0.5,
                skips=[(DECLARED, "windows"), other],
                max_skipped=4,
                no_skip_modules=[],
                violations=[],
                platform_exclusive_tests=self.exclusive,
                platform="linux",
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["platform"], "linux")
        self.assertEqual(report["skipped_count"], 2)
        self.assertEqual(report["counted_skipped_count"], 1)
        self.assertEqual(
            report["platform_excused"], [{"test_id": DECLARED, "reason": "windows"}]
        )
        self.assertEqual(report["policy"]["platform_exclusive_tests"], [DECLARED])


class CanonicalSkipIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def collect(self, module: types.ModuleType, method: str):
        result = _run_case(_skipping_case(module.__name__), method)
        with patch.dict(sys.modules, {module.__name__: module}):
            return self.runner._collect_skips(result)

    def test_top_level_discovery_ids_gain_the_tests_prefix(self) -> None:
        module = _fake_module(FAKE_MODULE, ROOT / "tests" / f"{FAKE_MODULE}.py")

        skips = self.collect(module, "test_skipped")

        self.assertEqual(
            skips, [(f"tests.{FAKE_MODULE}.Case.test_skipped", "not on this platform")]
        )

    def test_dotted_subdirectory_ids_are_unchanged(self) -> None:
        name = f"tests.connector.{FAKE_MODULE}"
        module = _fake_module(name, ROOT / "tests" / "connector" / f"{FAKE_MODULE}.py")

        skips = self.collect(module, "test_skipped")

        self.assertEqual(skips[0][0], f"{name}.Case.test_skipped")

    def test_a_subtest_skip_is_named_after_its_parent_test(self) -> None:
        module = _fake_module(FAKE_MODULE, ROOT / "tests" / f"{FAKE_MODULE}.py")

        skips = self.collect(module, "test_subtest_skipped")

        self.assertEqual(len(skips), 1)
        self.assertTrue(
            skips[0][0].startswith(f"tests.{FAKE_MODULE}.Case.test_subtest_skipped "),
            skips[0][0],
        )

    def test_a_module_outside_the_repository_keeps_its_raw_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = _fake_module(FAKE_MODULE, Path(tmp) / f"{FAKE_MODULE}.py")

            skips = self.collect(module, "test_skipped")

        self.assertEqual(skips[0][0], f"{FAKE_MODULE}.Case.test_skipped")

    def test_the_full_run_no_skip_check_matches_a_top_level_module(self) -> None:
        module = _fake_module(FAKE_MODULE, ROOT / "tests" / f"{FAKE_MODULE}.py")

        violations = self.runner._evaluate_skip_policy(
            skips=self.collect(module, "test_skipped"),
            max_skipped=10,
            no_skip_modules=[f"tests.{FAKE_MODULE}"],
        )

        self.assertEqual(len(violations), 1)
        self.assertIn("no-skip suite skipped", violations[0])


class DeclarationLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def load(self, declarations):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skip_policy.json"
            path.write_text(
                json.dumps(
                    {
                        "max_skipped": 4,
                        "no_skip_modules": [],
                        "platform_exclusive_tests": declarations,
                    }
                ),
                encoding="utf-8",
            )
            return self.runner._load_skip_policy(str(path))

    def test_valid_declarations_load_as_platform_sets(self) -> None:
        policy = self.load(
            {DECLARED: {"platforms": ["win32"], "reason": "NTFS junctions"}}
        )

        self.assertEqual(
            policy["platform_exclusive_tests"], {DECLARED: frozenset({"win32"})}
        )

    def test_a_policy_without_declarations_loads_an_empty_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skip_policy.json"
            path.write_text(json.dumps({"max_skipped": 4}), encoding="utf-8")

            policy = self.runner._load_skip_policy(str(path))

        self.assertEqual(policy["platform_exclusive_tests"], {})

    def test_malformed_declarations_are_rejected(self) -> None:
        cases = {
            "not an object": [DECLARED],
            "unknown platform": {DECLARED: {"platforms": ["windows"], "reason": "x"}},
            "empty platforms": {DECLARED: {"platforms": [], "reason": "x"}},
            "platforms not a list": {DECLARED: {"platforms": "win32", "reason": "x"}},
            "missing reason": {DECLARED: {"platforms": ["win32"]}},
            "blank reason": {DECLARED: {"platforms": ["win32"], "reason": " "}},
            "entry not an object": {DECLARED: "win32"},
            "id not tests-qualified": {
                "test_example.ExampleTests.test_windows_only": {
                    "platforms": ["win32"],
                    "reason": "x",
                }
            },
            "id without class and method": {
                "tests.test_example": {"platforms": ["win32"], "reason": "x"}
            },
        }
        for label, declarations in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.load(declarations)


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()
        self.policy = self.runner._load_skip_policy(str(POLICY_PATH))

    def test_the_windows_only_hardening_cases_are_declared_for_windows(self) -> None:
        declared = self.policy["platform_exclusive_tests"]

        for test_id in WINDOWS_ONLY_HARDENING_CASES:
            self.assertEqual(declared.get(test_id), frozenset({"win32"}), test_id)

    def test_the_linux_ci_skip_set_fits_the_budget(self) -> None:
        skips = [(test_id, "observed on Linux CI") for test_id in LINUX_CI_SKIPS]

        violations = self.runner._evaluate_skip_policy(
            skips=skips,
            max_skipped=self.policy["max_skipped"],
            no_skip_modules=self.policy["no_skip_modules"],
            platform_exclusive_tests=self.policy["platform_exclusive_tests"],
            platform="linux",
        )
        counted, _ = self.runner._partition_skips(
            skips,
            platform_exclusive_tests=self.policy["platform_exclusive_tests"],
            platform="linux",
        )

        self.assertEqual(violations, [])
        self.assertEqual(self.policy["max_skipped"], 4)
        self.assertEqual(
            sorted(test_id for test_id, _ in counted),
            [
                f"{WECHAT_AES}.test_round_trip_decrypt",
                f"{WECHAT_AES}.test_wrong_app_id_raises",
            ],
        )

    def test_each_declared_test_skips_exactly_off_its_platforms(self) -> None:
        declared = self.policy["platform_exclusive_tests"]
        self.assertTrue(declared)
        for test_id, platforms in sorted(declared.items()):
            module_name, class_name, method_name = test_id.rsplit(".", 2)
            with self.subTest(test_id):
                module = importlib.import_module(module_name)
                method = getattr(getattr(module, class_name), method_name)
                self.assertEqual(
                    bool(getattr(method, "__unittest_skip__", False)),
                    sys.platform not in platforms,
                )


WINDOWS_ONLY_SOURCE = (
    "import sys\nimport unittest\n\n\n"
    "class ExampleTests(unittest.TestCase):\n"
    '    @unittest.skipUnless(sys.platform == "win32", "Windows only")\n'
    "    def test_windows_only(self):\n"
    "        pass\n"
)


def _write_governance_fixture(root: Path, declarations, files=None) -> list[str]:
    tests_dir = root / "tests"
    tests_dir.mkdir()
    sources = files if files is not None else {"test_example.py": WINDOWS_ONLY_SOURCE}
    for relative, source in sources.items():
        path = tests_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    policy = tests_dir / "skip_policy.json"
    policy.write_text(
        json.dumps(
            {
                "max_skipped": 4,
                "no_skip_modules": [],
                "no_skip_module_metadata": {},
                "platform_exclusive_tests": declarations,
            }
        ),
        encoding="utf-8",
    )
    allowlist = tests_dir / "mutation_survivor_allowlist.json"
    allowlist.write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    return [
        "--repo-root",
        str(root),
        "--skip-policy",
        str(policy),
        "--mutation-survivor-allowlist",
        str(allowlist),
    ]


def _run_governance(declarations, files=None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        args = _write_governance_fixture(Path(tmp), declarations, files)
        return subprocess.run(
            [sys.executable, str(GOVERNANCE_SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )


def _entry(**overrides):
    future = (date.today() + timedelta(days=180)).isoformat()
    entry = {"platforms": ["win32"], "reason": "NTFS only", "review_after": future}
    entry.update(overrides)
    return {key: value for key, value in entry.items() if value is not None}


class DeclarationGovernanceTests(unittest.TestCase):
    def test_runner_and_governance_accept_the_same_platforms(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "verify_test_debt_governance_platform_scope", GOVERNANCE_SCRIPT
        )
        assert spec is not None and spec.loader is not None
        governance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(governance)

        self.assertEqual(
            governance.SKIP_POLICY_PLATFORMS, _load_runner().SKIP_POLICY_PLATFORMS
        )

    def test_a_current_declaration_passes(self) -> None:
        result = _run_governance({DECLARED: _entry()})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_defective_declarations_fail(self) -> None:
        past = (date.today() - timedelta(days=1)).isoformat()
        cases = {
            "missing method": (
                {"tests.test_example.ExampleTests.test_gone": _entry()},
                "test_gone",
            ),
            "missing class": (
                {"tests.test_example.GoneTests.test_windows_only": _entry()},
                "GoneTests",
            ),
            "missing module": (
                {"tests.test_gone.ExampleTests.test_windows_only": _entry()},
                "module path does not exist",
            ),
            "unknown platform": (
                {DECLARED: _entry(platforms=["nt"])},
                "platforms",
            ),
            "missing reason": ({DECLARED: _entry(reason=None)}, "reason"),
            "missing review date": (
                {DECLARED: _entry(review_after=None)},
                "missing review_after",
            ),
            "past review date": (
                {DECLARED: _entry(review_after=past)},
                "is in the past",
            ),
        }
        for label, (declarations, expected) in cases.items():
            with self.subTest(label):
                result = _run_governance(declarations)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("platform_exclusive_tests", result.stdout)
                self.assertIn(expected, result.stdout)


class PlatformSkipInventoryTests(unittest.TestCase):
    """The gate must fail on a platform-conditional skip nobody declared.

    Otherwise the Windows gate passes while every Linux run spends budget on it.
    """

    FORMS = {
        "decorator through a module-level alias": (
            {
                "test_example.py": "import os\nimport unittest\n\n"
                'IS_WINDOWS = os.name == "nt"\n\n\n'
                "class ExampleTests(unittest.TestCase):\n"
                '    @unittest.skipUnless(IS_WINDOWS, "Windows only")\n'
                "    def test_alias(self):\n"
                "        pass\n"
            },
            ["tests.test_example.ExampleTests.test_alias"],
        ),
        "class decorator": (
            {
                "test_example.py": "import sys\nimport unittest\n\n\n"
                '@unittest.skipIf(sys.platform.startswith("win"), "POSIX only")\n'
                "class ExampleTests(unittest.TestCase):\n"
                "    def test_one(self):\n"
                "        pass\n\n"
                "    def test_two(self):\n"
                "        pass\n"
            },
            [
                "tests.test_example.ExampleTests.test_one",
                "tests.test_example.ExampleTests.test_two",
            ],
        ),
        "skipTest under a platform check": (
            {
                "test_example.py": "import platform\nimport unittest\n\n\n"
                "class ExampleTests(unittest.TestCase):\n"
                "    def test_body(self):\n"
                '        if platform.system() != "Windows":\n'
                '            self.skipTest("Windows only")\n'
            },
            ["tests.test_example.ExampleTests.test_body"],
        ),
        "skipTest under a platform check in setUp": (
            {
                "test_example.py": "import os\nimport unittest\n\n\n"
                "class ExampleTests(unittest.TestCase):\n"
                "    def setUp(self):\n"
                '        if os.name != "nt":\n'
                '            self.skipTest("Windows only")\n\n'
                "    def test_one(self):\n"
                "        pass\n"
            },
            ["tests.test_example.ExampleTests.test_one"],
        ),
        "decorator imported by name": (
            {
                "test_example.py": "import os\nimport unittest\n"
                "from unittest import skipIf\n\n\n"
                "class ExampleTests(unittest.TestCase):\n"
                '    @skipIf(os.name == "nt", "POSIX only")\n'
                "    def test_bare(self):\n"
                "        pass\n"
            },
            ["tests.test_example.ExampleTests.test_bare"],
        ),
        "module in a test subdirectory": (
            {"connector/test_sub.py": WINDOWS_ONLY_SOURCE},
            ["tests.connector.test_sub.ExampleTests.test_windows_only"],
        ),
    }

    def test_each_undeclared_platform_skip_fails_the_gate(self) -> None:
        for label, (files, test_ids) in self.FORMS.items():
            with self.subTest(label):
                result = _run_governance({}, files)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                for test_id in test_ids:
                    self.assertIn(
                        f"not declared in platform_exclusive_tests: {test_id}",
                        result.stdout,
                    )

    def test_each_declared_platform_skip_passes(self) -> None:
        for label, (files, test_ids) in self.FORMS.items():
            with self.subTest(label):
                result = _run_governance(
                    {test_id: _entry() for test_id in test_ids}, files
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_dependency_skip_is_not_a_platform_skip(self) -> None:
        files = {
            "test_example.py": "import unittest\n\nweb = None\n\n\n"
            "class ExampleTests(unittest.TestCase):\n"
            '    @unittest.skipIf(web is None, "aiohttp not installed")\n'
            "    def test_route(self):\n"
            "        pass\n"
        }

        result = _run_governance({}, files)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_declaration_without_a_platform_condition_fails(self) -> None:
        files = {
            "test_example.py": "import unittest\n\n\n"
            "class ExampleTests(unittest.TestCase):\n"
            '    @unittest.skipUnless(False, "never")\n'
            "    def test_windows_only(self):\n"
            "        pass\n"
        }

        result = _run_governance({DECLARED: _entry()}, files)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("no platform-conditional skip", result.stdout)


if __name__ == "__main__":
    unittest.main()
