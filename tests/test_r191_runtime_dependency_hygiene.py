import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.tool_runner import ToolRunner

ROOT = Path(__file__).resolve().parents[1]


class TestR191RuntimeDependencyHygiene(unittest.TestCase):
    def test_package_allowlist_resource_resolves_under_package_root(self):
        try:
            from services.runtime_dependency_hygiene import (
                resolve_package_resource_path,
            )
        except ImportError as exc:
            self.fail(f"missing runtime dependency hygiene helper: {exc}")

        allowlist_path = Path(
            resolve_package_resource_path("tools_allowlist", package_root=ROOT)
        )

        self.assertEqual(allowlist_path, ROOT / "data" / "tools_allowlist.json")
        self.assertTrue(allowlist_path.exists())

    def test_package_resource_resolution_supports_packaged_layouts(self):
        try:
            from services.runtime_dependency_hygiene import (
                resolve_package_resource_path,
            )
        except ImportError as exc:
            self.fail(f"missing runtime dependency hygiene helper: {exc}")

        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir) / "openclaw_package"
            package_data = package_root / "data"
            package_data.mkdir(parents=True)
            expected = package_data / "tools_allowlist.json"
            expected.write_text('{"tools": []}', encoding="utf-8")

            resolved = Path(
                resolve_package_resource_path(
                    "tools_allowlist", package_root=package_root
                )
            )

        self.assertEqual(resolved, expected)

    def test_tool_runner_default_uses_package_allowlist_not_state_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            bind_mount_cwd = Path(temp_dir) / "bind_mount"
            state_dir.mkdir()
            bind_mount_cwd.mkdir()
            original_cwd = os.getcwd()

            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_STATE_DIR": str(state_dir),
                    "MOLTBOT_STATE_DIR": "",
                    "OPENCLAW_TOOLS_CONFIG_PATH": "",
                },
            ):
                try:
                    os.chdir(bind_mount_cwd)
                    runner = ToolRunner()
                finally:
                    os.chdir(original_cwd)

        self.assertEqual(
            Path(runner._config_path),
            ROOT / "data" / "tools_allowlist.json",
        )
        self.assertEqual(runner.list_tools(), [])

    def test_explicit_tools_config_path_still_overrides_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_allowlist = Path(temp_dir) / "custom_tools.json"
            custom_allowlist.write_text(
                """
                {
                    "tools": [
                        {
                            "name": "custom_echo",
                            "command": ["echo", "{message}"],
                            "args": {"message": "^[a-z]+$"}
                        }
                    ]
                }
                """,
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"OPENCLAW_TOOLS_CONFIG_PATH": str(custom_allowlist)},
            ):
                runner = ToolRunner()

        self.assertEqual(Path(runner._config_path), custom_allowlist)
        self.assertEqual(
            {"custom_echo"}, {tool["name"] for tool in runner.list_tools()}
        )

    def test_runtime_cache_contract_keeps_generated_paths_separate(self):
        try:
            from services.runtime_dependency_hygiene import (
                get_runtime_dependency_hygiene_contract,
                resolve_state_owned_runtime_path,
            )
        except ImportError as exc:
            self.fail(f"missing runtime dependency hygiene helper: {exc}")

        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir) / "state"
            runtime_cache = Path(
                resolve_state_owned_runtime_path("runtime_cache", state_dir=state_dir)
            )
            tool_sandbox = Path(
                resolve_state_owned_runtime_path("tool_sandbox", state_dir=state_dir)
            )

        contract = get_runtime_dependency_hygiene_contract()
        generated_repo_cache_paths = {
            item["path"] for item in contract["repo_local_generated_caches"]
        }

        self.assertEqual(runtime_cache, state_dir / "cache")
        self.assertEqual(tool_sandbox, state_dir / "tool_sandbox")
        self.assertIn(".tmp/", generated_repo_cache_paths)
        self.assertEqual(
            contract["managed_runtime_dependency_cache"]["status"],
            "not_implemented",
        )
        self.assertFalse(
            contract["managed_runtime_dependency_cache"]["automatic_repair"]
        )


if __name__ == "__main__":
    unittest.main()
