import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.tool_runner import ToolRunner


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.tf = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json")
        self.tf.write(
            json.dumps(
                {
                    "tools": [
                        {
                            "name": "test_net_off",
                            "command": [
                                sys.executable,
                                "-c",
                                "import os; print(os.environ.get('HTTP_PROXY', 'NONE'))",
                            ],
                            "args": {},
                            "sandbox": {"network": False, "allow_network_hosts": []},
                        },
                        {
                            "name": "test_net_on",
                            "command": [
                                sys.executable,
                                "-c",
                                "import os; print(os.environ.get('HTTP_PROXY', 'NONE'))",
                            ],
                            "args": {},
                            "sandbox": {
                                "network": True,
                                "allow_network_hosts": ["example.com"],
                            },
                        },
                    ]
                }
            )
        )
        self.tf.close()
        self.runner = ToolRunner(config_path=self.tf.name)

    def tearDown(self):
        if os.path.exists(self.tf.name):
            os.remove(self.tf.name)

    def test_profile_defaults(self):
        with open(self.tf.name, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"tools": [{"name": "implicit", "command": ["echo"], "args": {}}]}
                )
            )
        self.runner.reload_config()
        tool = self.runner._tools["implicit"]
        self.assertFalse(tool.sandbox.network)
        self.assertEqual(tool.sandbox.allow_fs_read, [])
        self.assertEqual(tool.sandbox.allow_network_hosts, [])

    def test_env_scrubbing(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://bad.com"}):
            result = self.runner.execute_tool("test_net_off", {})
            self.assertTrue(result.success)
            self.assertIn("NONE", result.output.strip())

            result2 = self.runner.execute_tool("test_net_on", {})
            self.assertTrue(result2.success)
            self.assertIn("http://bad.com", result2.output.strip())

    def test_hardened_requires_explicit_sandbox(self):
        with open(self.tf.name, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"tools": [{"name": "implicit", "command": ["echo"], "args": {}}]}
                )
            )
        self.runner.reload_config()
        with patch(
            "services.tool_runner.ToolRunner._is_hardened_mode", return_value=True
        ):
            ok, issues = self.runner.evaluate_sandbox_posture()
            self.assertFalse(ok)
            self.assertTrue(any("missing explicit sandbox policy" in i for i in issues))
            res = self.runner.execute_tool("implicit", {})
            self.assertFalse(res.success)
            self.assertIn("Missing explicit sandbox policy", res.error or "")

    def test_hardened_network_requires_allowlist(self):
        with open(self.tf.name, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "net_no_allowlist",
                                "command": ["echo", "x"],
                                "args": {},
                                "sandbox": {"network": True},
                            }
                        ]
                    }
                )
            )
        self.runner.reload_config()
        with patch(
            "services.tool_runner.ToolRunner._is_hardened_mode", return_value=True
        ):
            ok, issues = self.runner.evaluate_sandbox_posture()
            self.assertFalse(ok)
            self.assertTrue(any("requires allow_network_hosts" in i for i in issues))
            res = self.runner.execute_tool("net_no_allowlist", {})
            self.assertFalse(res.success)
            self.assertIn("allow_network_hosts", res.error or "")

    def test_hardened_runtime_unavailable_fail_closed(self):
        with patch(
            "services.tool_runner.ToolRunner._is_hardened_mode", return_value=True
        ):
            with patch.dict(
                os.environ, {"OPENCLAW_TOOL_SANDBOX_RUNTIME_AVAILABLE": "0"}
            ):
                ok, issues = self.runner.evaluate_sandbox_posture()
                self.assertFalse(ok)
                self.assertTrue(any("runtime unavailable" in i.lower() for i in issues))
                res = self.runner.execute_tool("test_net_off", {})
                self.assertFalse(res.success)
                self.assertIn("Sandbox runtime unavailable", res.error or "")

    def test_fs_path_enforcement(self):
        """S47: tools with allow_fs allowlists reject out-of-scope paths."""
        # Use a real temp directory as the allowed path
        allowed_dir = tempfile.mkdtemp(prefix="sandbox_test_")
        try:
            with open(self.tf.name, "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "tools": [
                                {
                                    "name": "restricted_tool",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import sys; print(sys.argv[1])",
                                        "{path}",
                                    ],
                                    "args": {"path": "^.*$"},
                                    "sandbox": {
                                        "network": False,
                                        "allow_fs_read": [allowed_dir],
                                        "allow_fs_write": [allowed_dir],
                                    },
                                }
                            ]
                        }
                    )
                )
            self.runner.reload_config()

            # Path outside all allowlists should be blocked
            res = self.runner.execute_tool(
                "restricted_tool",
                {
                    "path": os.path.join(
                        tempfile.gettempdir(), "not_allowed", "secret.txt"
                    )
                },
            )
            self.assertFalse(res.success)
            self.assertIn("Sandbox FS violation", res.error or "")

            # Path inside allowlist should pass
            allowed_file = os.path.join(allowed_dir, "file.txt")
            res2 = self.runner.execute_tool("restricted_tool", {"path": allowed_file})
            self.assertTrue(res2.success, f"Expected success but got: {res2.error}")
        finally:
            os.rmdir(allowed_dir)

    def test_fs_no_allowlist_allows_execution(self):
        """S47: tools with empty FS allowlists still execute (backward compat)."""
        res = self.runner.execute_tool("test_net_off", {})
        self.assertTrue(res.success)
