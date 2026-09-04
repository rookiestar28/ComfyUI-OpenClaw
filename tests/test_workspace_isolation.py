import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePath
from unittest.mock import MagicMock

from connector.config import ConnectorConfig
from connector.media_store import MediaStore
from connector.state import ConnectorState

ROOT = Path(__file__).resolve().parents[1]
HYGIENE_SCRIPT = ROOT / "scripts" / "check_workspace_hygiene.py"
WINDOWS_GATE = ROOT / "scripts" / "run_full_tests_windows.ps1"
LINUX_GATE = ROOT / "scripts" / "run_full_tests_linux.sh"
PRE_PUSH_GATE = ROOT / "scripts" / "pre_push_checks.sh"


def _load_hygiene_module():
    spec = importlib.util.spec_from_file_location("workspace_hygiene", HYGIENE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("workspace hygiene script is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConnectorPathIsolationTests(unittest.TestCase):
    def test_media_store_rejects_magicmock_state_path_before_directory_creation(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            previous = Path.cwd()
            try:
                os.chdir(workspace)
                config = ConnectorConfig()
                config.state_path = MagicMock(name="state_path")

                with self.assertRaisesRegex(TypeError, "state_path"):
                    MediaStore(config)

                self.assertEqual([], list(workspace.iterdir()))
            finally:
                os.chdir(previous)

    def test_media_store_rejects_magicmock_explicit_storage_before_mutation(self):
        config = ConnectorConfig()
        config.state_path = None

        with self.assertRaisesRegex(TypeError, "storage_path"):
            MediaStore(config, storage_path=MagicMock(name="storage_path"))

    def test_connector_state_rejects_magicmock_path_before_filesystem_access(self):
        with self.assertRaisesRegex(TypeError, "path"):
            ConnectorState(path=MagicMock(name="state_path"))

    def test_string_and_purepath_inputs_remain_supported(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = ConnectorState(path=PurePath(root / "connector-state.json"))
            state.set_offset("telegram", 7)
            self.assertEqual(7, state.get_offset("telegram"))

            config = ConnectorConfig()
            config.state_path = str(root / "connector-state.json")
            store = MediaStore(config)
            self.assertEqual(root / "media", store.media_dir)


class WorkspaceHygieneContractTests(unittest.TestCase):
    def test_hygiene_snapshot_preserves_existing_and_reports_only_new_root_labels(self):
        self.assertTrue(HYGIENE_SCRIPT.is_file(), "workspace hygiene helper is missing")
        hygiene = _load_hygiene_module()

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "MagicMock" / "existing").mkdir(parents=True)
            (root / "error.log").write_text("existing", encoding="utf-8")
            before = hygiene.capture_workspace(root)

            (root / "MagicMock" / "new-child").mkdir()
            (root / "audit.log").write_text("new", encoding="utf-8")

            self.assertEqual(
                ["MagicMock", "audit.log"],
                hygiene.find_new_forbidden_roots(root, before),
            )
            self.assertTrue((root / "MagicMock" / "existing").is_dir())
            self.assertEqual(
                "existing", (root / "error.log").read_text(encoding="utf-8")
            )

    def test_snapshot_round_trip_uses_bounded_schema(self):
        self.assertTrue(HYGIENE_SCRIPT.is_file(), "workspace hygiene helper is missing")
        hygiene = _load_hygiene_module()

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot_path = root / "snapshot.json"
            before = hygiene.capture_workspace(root)
            hygiene.write_snapshot(snapshot_path, before)
            restored = hygiene.read_snapshot(snapshot_path)

            self.assertEqual(before, restored)
            self.assertEqual(
                set(hygiene.FORBIDDEN_ROOTS),
                set(restored),
            )

    def test_cli_check_fails_with_root_label_only_for_new_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot_path = root / "snapshot.json"
            snapshot = subprocess.run(
                [
                    sys.executable,
                    str(HYGIENE_SCRIPT),
                    "snapshot",
                    "--root",
                    str(root),
                    "--snapshot",
                    str(snapshot_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, snapshot.returncode, snapshot.stderr)

            private_content = "must-not-appear-in-diagnostics"
            (root / "audit.log").write_text(private_content, encoding="utf-8")
            check = subprocess.run(
                [
                    sys.executable,
                    str(HYGIENE_SCRIPT),
                    "check",
                    "--root",
                    str(root),
                    "--snapshot",
                    str(snapshot_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(1, check.returncode)
            self.assertEqual("", check.stdout)
            self.assertEqual("WORKSPACE_HYGIENE_NEW_ROOTS: audit.log\n", check.stderr)
            self.assertNotIn(str(root), check.stderr)
            self.assertNotIn(private_content, check.stderr)

    def test_gate_scripts_snapshot_before_tests_and_check_after_tests(self):
        cases = (
            (
                WINDOWS_GATE,
                'Write-Host "[tests] 5/10 backend unit tests"',
                'Write-Host "[tests] 10/10 frontend E2E"',
            ),
            (
                LINUX_GATE,
                'echo "[tests] 5/10 backend unit tests"',
                'echo "[tests] 10/10 frontend E2E"',
            ),
            (
                PRE_PUSH_GATE,
                'echo "[pre-push] 5/9 backend unit tests"',
                'echo "[pre-push] 9/9 npm test (Playwright)"',
            ),
        )
        for path, backend_marker, frontend_marker in cases:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                snapshot_at = content.index("check_workspace_hygiene.py snapshot")
                backend_at = content.index(backend_marker)
                frontend_at = content.index(frontend_marker)
                check_at = content.index("check_workspace_hygiene.py check")
                self.assertLess(snapshot_at, backend_at)
                self.assertLess(backend_at, frontend_at)
                self.assertLess(frontend_at, check_at)

    def test_local_gate_state_paths_are_owned_by_tmp(self):
        for path in (WINDOWS_GATE, LINUX_GATE, PRE_PUSH_GATE):
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8").replace("\\", "/")
                self.assertIn(".tmp/test-state", content)
                self.assertNotIn("moltbot_state/_local_", content)
                self.assertNotIn("moltbot_state/_pre_push_", content)

    def test_regression_is_no_skip_owned(self):
        policy = (ROOT / "tests" / "skip_policy.json").read_text(encoding="utf-8")
        self.assertIn('"tests.test_workspace_isolation"', policy)


class AdHocArtifactWriterContractTests(unittest.TestCase):
    def test_integration_failure_uses_assertion_message_not_root_error_log(self):
        source = (ROOT / "tests" / "test_r68_integration_flow.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('open("error.log", "w")', source)
        self.assertIn("await resp.text()", source)

    def test_session_invalidation_uses_real_tmp_bound_connector_config(self):
        source = (ROOT / "tests" / "test_r93_session_invalidation.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("config=MagicMock()", source)
        self.assertIn("TemporaryDirectory", source)
        self.assertIn("state_path", source)


if __name__ == "__main__":
    unittest.main()
