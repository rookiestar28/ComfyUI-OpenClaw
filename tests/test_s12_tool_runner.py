"""
S12 Tool Runner Tests.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from services.tool_runner import ToolDefinition, ToolResult, ToolRunner


class TestS12ToolRunner(unittest.TestCase):

    def setUp(self):
        self.runner = ToolRunner(config_path="dummy_path")  # Won't load anything real

        # Manually inject a test tool
        self.tool = ToolDefinition(
            name="test_echo",
            command_template=[sys.executable, "{msg}"],
            allowed_args={"msg": "^[a-z0-9]+$"},
            timeout_sec=1,
            max_output_bytes=100,
        )
        self.runner._tools["test_echo"] = self.tool

    def test_validate_args_success(self):
        self.tool.validate_args({"msg": "hello"})

    def test_validate_args_failure_regex(self):
        with self.assertRaises(ValueError):
            self.tool.validate_args({"msg": "BAD@KEY"})

    def test_validate_args_failure_unknown(self):
        with self.assertRaises(ValueError):
            self.tool.validate_args({"msg": "hello", "unknown": "val"})

    @patch("subprocess.run")
    def test_execute_success(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "hello\n"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        result = self.runner.execute_tool("test_echo", {"msg": "hello"})

        self.assertTrue(result.success)
        self.assertEqual(result.output, "hello\n")
        self.assertEqual(result.exit_code, 0)

        # Verify call args
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], [sys.executable, "hello"])

    @patch("subprocess.run")
    def test_env_sanitization(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        with patch.dict(
            os.environ, {"SECRET_KEY": "fail", "TOKEN_X": "fail", "SAFE_VAR": "ok"}
        ):
            self.runner.execute_tool("test_echo", {"msg": "hello"})

            args, kwargs = mock_run.call_args
            env_used = kwargs["env"]

            self.assertIn("SAFE_VAR", env_used)
            self.assertNotIn("SECRET_KEY", env_used)
            self.assertNotIn("TOKEN_X", env_used)

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["echo"], timeout=1)

        result = self.runner.execute_tool("test_echo", {"msg": "hello"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Execution timed out")


if __name__ == "__main__":
    unittest.main()
