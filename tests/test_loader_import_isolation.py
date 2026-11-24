"""Isolation contracts for the loader-import smoke test and the route integration test.

Importing the real package without a host PromptServer starts a bounded host-wait owner
thread. These tests keep that thread, and every other side effect of the smoke test, out
of the test process, and keep the route integration test's thread check scoped to threads
that test could have created. Everything is synchronised with events, never with sleeps.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SMOKE_MODULE_PREFIX = "comfyui_openclaw_loader_smoke"
JOIN_TIMEOUT_SEC = 10.0


def _load_test_module(alias: str, filename: str) -> ModuleType:
    """Load a sibling test module by path, without registering it in sys.modules."""
    spec = importlib.util.spec_from_file_location(alias, str(TESTS_DIR / filename))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_case(case_class: type[unittest.TestCase], method: str) -> unittest.TestResult:
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(unittest.TestSuite([case_class(method)]))


def _describe(result: unittest.TestResult) -> str:
    return "\n".join(text for _, text in [*result.failures, *result.errors])


def _live_threads() -> set[tuple[str, int | None]]:
    return {(t.name, t.ident) for t in threading.enumerate() if t.is_alive()}


def _smoke_modules() -> list[str]:
    return sorted(n for n in sys.modules if n.startswith(SMOKE_MODULE_PREFIX))


class LoaderImportSmokeIsolationTests(unittest.TestCase):
    def test_smoke_test_leaves_no_thread_environment_or_module_behind(self) -> None:
        module = _load_test_module(
            "loader_import_smoke_under_test", "test_comfyui_loader_import.py"
        )
        env_before = dict(os.environ)
        threads_before = _live_threads()
        try:
            result = _run_case(
                module.TestComfyUICustomNodeLoaderImport,
                "test_init_import_via_file_loader",
            )
            env_after = dict(os.environ)
            new_threads = sorted(name for name, _ in _live_threads() - threads_before)
            modules_after = _smoke_modules()
        finally:
            # Contain only the environment damage of a regression. Do not touch
            # sys.modules: removing the package's submodules would change what the
            # leaked thread does next and hide the behaviour this test must observe.
            os.environ.clear()
            os.environ.update(env_before)

        if result.skipped:
            self.skipTest(result.skipped[0][1])
        self.assertTrue(result.wasSuccessful(), _describe(result))
        self.assertEqual(new_threads, [], "the smoke test left live threads behind")
        self.assertEqual(env_after, env_before, "the smoke test changed os.environ")
        self.assertEqual(modules_after, [], "the smoke test left its module alias")


class RouteIntegrationThreadInvariantTests(unittest.TestCase):
    CASE = "LoaderReadyRouteIntegrationTests"
    METHOD = "test_loader_ready_registration_contract"

    def _load(self) -> ModuleType:
        return _load_test_module(
            "route_integration_under_test",
            "test_r240_loader_ready_route_integration.py",
        )

    def test_a_thread_that_existed_before_may_exit_during_the_run(self) -> None:
        module = self._load()
        real_run_child = module._run_child
        release = threading.Event()
        foreign = threading.Thread(
            target=release.wait, name="foreign-thread-exits-mid-run", daemon=True
        )
        foreign.start()
        self.addCleanup(foreign.join, JOIN_TIMEOUT_SEC)
        self.addCleanup(release.set)

        def run_child_after_foreign_exit(state_dir: Path):
            # Deterministic: the foreign thread is gone before the test's final check.
            release.set()
            foreign.join(JOIN_TIMEOUT_SEC)
            self.assertFalse(foreign.is_alive())
            return real_run_child(state_dir)

        with mock.patch.object(module, "_run_child", run_child_after_foreign_exit):
            result = _run_case(getattr(module, self.CASE), self.METHOD)

        self.assertTrue(result.wasSuccessful(), _describe(result))

    def test_a_thread_left_alive_by_the_run_still_fails(self) -> None:
        module = self._load()
        real_run_child = module._run_child
        release = threading.Event()
        started: list[threading.Thread] = []
        self.addCleanup(lambda: [t.join(JOIN_TIMEOUT_SEC) for t in started])
        self.addCleanup(release.set)

        def run_child_and_leave_a_thread(state_dir: Path):
            thread = threading.Thread(
                target=release.wait, name="thread-left-alive-by-the-run", daemon=True
            )
            thread.start()
            started.append(thread)
            return real_run_child(state_dir)

        with mock.patch.object(module, "_run_child", run_child_and_leave_a_thread):
            result = _run_case(getattr(module, self.CASE), self.METHOD)

        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1, _describe(result))
        self.assertIn("thread-left-alive-by-the-run", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
