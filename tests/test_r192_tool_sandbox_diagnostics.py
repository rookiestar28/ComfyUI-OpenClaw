import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.tool_runner import SandboxProfile, ToolDefinition, ToolRunner


def _runner_with_tool(tool: ToolDefinition) -> ToolRunner:
    runner = ToolRunner(config_path="dummy_path")
    runner._tools[tool.name] = tool
    return runner


def _echo_tool(name: str = "diagnostic_echo") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        command_template=[sys.executable, "{msg}"],
        allowed_args={"msg": "^[a-z]+$"},
        timeout_sec=1,
        max_output_bytes=100,
        sandbox=SandboxProfile.strict(),
        sandbox_declared=True,
    )


class TestR192ToolSandboxDiagnostics(unittest.TestCase):
    def test_hardened_missing_sandbox_runtime_is_coded_and_fail_closed(self):
        runner = _runner_with_tool(_echo_tool())

        with (
            patch.dict(
                "os.environ",
                {
                    "OPENCLAW_RUNTIME_PROFILE": "hardened",
                    "OPENCLAW_TOOL_SANDBOX_RUNTIME_AVAILABLE": "0",
                },
            ),
            patch("subprocess.run") as mock_run,
        ):
            result = runner.execute_tool("diagnostic_echo", {"msg": "hello"})

        self.assertFalse(result.success)
        self.assertEqual(
            getattr(result, "error_code", None),
            "sandbox_runtime_unavailable",
        )
        self.assertIn("OPENCLAW_TOOL_SANDBOX_RUNTIME_AVAILABLE", result.remediation)
        mock_run.assert_not_called()

    def test_missing_interpreter_is_coded_and_actionable(self):
        runner = _runner_with_tool(_echo_tool())

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("executable not found")
            result = runner.execute_tool("diagnostic_echo", {"msg": "hello"})

        self.assertFalse(result.success)
        self.assertEqual(getattr(result, "error_code", None), "interpreter_missing")
        self.assertIn("Install the executable", result.remediation)

    def test_timeout_preserves_error_text_and_adds_diagnostics(self):
        runner = _runner_with_tool(_echo_tool())

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["echo"], timeout=1)
            result = runner.execute_tool("diagnostic_echo", {"msg": "hello"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Execution timed out")
        self.assertEqual(getattr(result, "error_code", None), "timeout")
        self.assertIn("timeout", result.remediation.lower())

    def test_workspace_violation_is_coded_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed = Path(temp_dir) / "allowed"
            denied = Path(temp_dir) / "denied" / "input.txt"
            allowed.mkdir()
            denied.parent.mkdir()
            denied.write_text("blocked", encoding="utf-8")

            tool = ToolDefinition(
                name="path_reader",
                command_template=[sys.executable, "{path}"],
                allowed_args={"path": r"^.+$"},
                timeout_sec=1,
                sandbox=SandboxProfile(allow_fs_read=[str(allowed)]),
                sandbox_declared=True,
            )
            runner = _runner_with_tool(tool)

            with patch("subprocess.run") as mock_run:
                result = runner.execute_tool("path_reader", {"path": str(denied)})

        self.assertFalse(result.success)
        self.assertEqual(getattr(result, "error_code", None), "workspace_violation")
        self.assertIn("allowed filesystem", result.remediation)
        mock_run.assert_not_called()

    def test_allowed_simple_command_has_no_failure_diagnostics(self):
        runner = _runner_with_tool(_echo_tool())
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = runner.execute_tool("diagnostic_echo", {"msg": "hello"})

        self.assertTrue(result.success)
        self.assertTrue(hasattr(result, "error_code"))
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.remediation)


if __name__ == "__main__":
    unittest.main()
