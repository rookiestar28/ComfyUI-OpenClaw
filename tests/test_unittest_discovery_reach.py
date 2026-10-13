"""The gate test runner must reach every test module under ``tests/``.

Python 3.11+ unittest discovery does not enter directories without ``__init__.py``; the
runner loads those modules by their dotted name from the repository root. These checks keep
that reach complete, keep runtime packages unshadowed, and keep test modules from leaking
process-wide state at import time (the gate runs every module in one process).
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
PATTERN = "test_*.py"
_PRUNED_DIRS = {"__pycache__", "node_modules"}

# Reviewed exclusions: files that match the test pattern but define no TestCase on purpose.
NOT_TEST_MODULES = {
    "test_r111_fuzz_harness": (
        "adversarial gate harness library imported by scripts/run_adversarial_gate.py"
    ),
}


def _load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openclaw_run_unittests_under_test", REPO_ROOT / "scripts" / "run_unittests.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_files(root: Path) -> dict[str, Path]:
    """Map ``tests``-relative dotted module names to files matching the test pattern."""
    files: dict[str, Path] = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in _PRUNED_DIRS
        )
        for filename in sorted(filenames):
            if filename.startswith("test_") and filename.endswith(".py"):
                path = Path(current, filename)
                rel = path.relative_to(root).with_suffix("")
                files[".".join(rel.parts)] = path
    return files


def _suite_modules(suite: unittest.TestSuite) -> set[str]:
    modules: set[str] = set()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            modules |= _suite_modules(item)
        else:
            modules.add(type(item).__module__)
    return modules


def _defines_test_case(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = (
                    base.attr
                    if isinstance(base, ast.Attribute)
                    else getattr(base, "id", "")
                )
                if name.endswith("TestCase"):
                    return True
    return False


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _import_time_sys_modules_mutations(path: Path) -> list[int]:
    """Line numbers of module-level statements that write to ``sys.modules``."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    lines: list[int] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Assign, ast.Delete)):
                targets = node.targets
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            else:
                targets = []
            if any(
                isinstance(target, ast.Subscript) and _is_sys_modules(target.value)
                for target in targets
            ):
                lines.append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _is_sys_modules(node.func.value)
                and node.func.attr in {"update", "setdefault", "pop", "clear"}
            ):
                lines.append(node.lineno)
    return lines


class GateSuiteReachTests(unittest.TestCase):
    def test_gate_suite_reaches_every_test_module(self) -> None:
        runner = _load_runner()
        suite = runner._build_suite(
            argparse.Namespace(
                module=None,
                start_dir=str(TESTS_DIR),
                pattern=PATTERN,
                top_level_dir=None,
            )
        )
        reached = {name.removeprefix("tests.") for name in _suite_modules(suite)}
        expected = set(_test_files(TESTS_DIR)) - set(NOT_TEST_MODULES)

        self.assertEqual(sorted(expected - reached), [])

    def test_exclusions_exist_and_define_no_test_case(self) -> None:
        files = _test_files(TESTS_DIR)
        for name, reason in NOT_TEST_MODULES.items():
            with self.subTest(module=name):
                self.assertTrue(reason.strip())
                self.assertIn(name, files)
                self.assertFalse(_defines_test_case(files[name]))

    def test_runtime_packages_resolve_to_the_repository(self) -> None:
        import api
        import connector
        import services

        for package in (api, connector, services):
            with self.subTest(package=package.__name__):
                self.assertEqual(
                    Path(package.__file__ or "").resolve().parent,
                    REPO_ROOT / package.__name__,
                )

    def test_no_test_module_writes_sys_modules_at_import_time(self) -> None:
        offenders = {
            path.relative_to(REPO_ROOT).as_posix(): lines
            for path in _test_files(TESTS_DIR).values()
            if (lines := _import_time_sys_modules_mutations(path))
        }
        self.assertEqual(offenders, {})


class UnreachableModuleSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_discovery_reach_")
        self.root = Path(self.tmp.name)
        self.start = self.root / "tests"
        layout = {
            "test_top.py": "",
            "pkg/__init__.py": "",
            "pkg/test_in_package.py": "",
            "pkg/plain/test_under_package.py": "",
            "plain/test_plain.py": "",
            "plain/helper.py": "",
            "plain/inner/__init__.py": "",
            "plain/inner/test_inner.py": "",
            ".hidden/test_hidden.py": "",
            "plain/__pycache__/test_cached.py": "",
        }
        for rel, body in layout.items():
            path = self.start / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_selects_only_modules_that_discovery_cannot_enter(self) -> None:
        selected = self.runner._unreachable_test_modules(self.start, PATTERN, self.root)

        self.assertEqual(
            [
                (name, path.relative_to(self.start).as_posix())
                for name, path in selected
            ],
            [
                (
                    "tests.pkg.plain.test_under_package",
                    "pkg/plain/test_under_package.py",
                ),
                ("tests.plain.inner.test_inner", "plain/inner/test_inner.py"),
                ("tests.plain.test_plain", "plain/test_plain.py"),
            ],
        )

    def test_start_dir_outside_or_at_the_root_selects_nothing(self) -> None:
        self.assertEqual(
            self.runner._unreachable_test_modules(
                self.start, PATTERN, self.start / "pkg"
            ),
            [],
        )
        self.assertEqual(
            self.runner._unreachable_test_modules(self.root, PATTERN, self.root), []
        )

    def test_a_module_that_resolves_elsewhere_is_rejected(self) -> None:
        impostor = types.ModuleType("tests.plain.test_plain")
        impostor.__file__ = str(self.root / "elsewhere" / "test_plain.py")

        with patch.object(
            self.runner.importlib, "import_module", return_value=impostor
        ):
            with self.assertRaisesRegex(RuntimeError, "tests.plain.test_plain"):
                self.runner._unreachable_module_suite(
                    unittest.TestLoader(), self.start, PATTERN, self.root
                )

    def test_an_import_failure_becomes_a_failing_test(self) -> None:
        with patch.object(
            self.runner.importlib, "import_module", side_effect=RuntimeError("boom")
        ):
            suite = self.runner._unreachable_module_suite(
                unittest.TestLoader(), self.start, PATTERN, self.root
            )
        result = unittest.TestResult()
        suite.run(result)

        self.assertEqual(result.testsRun, 3)
        self.assertEqual(len(result.errors), 3)
        self.assertIn("tests.plain.test_plain", result.errors[-1][0].id())


if __name__ == "__main__":
    unittest.main()
