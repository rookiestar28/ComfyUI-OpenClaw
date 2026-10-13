"""Process spawn boundaries for the external tool runner, transform worker and doctor.

Every case that could launch a process mocks the launch; nothing is executed.
"""

import importlib.metadata
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from services.operator_doctor import DoctorReport, check_runtime_provenance
from services.tool_runner import SandboxProfile, ToolDefinition, ToolResult, ToolRunner
from services.transform_common import (
    TransformLimits,
    TransformRegistry,
    TrustedTransform,
)
from services.transform_runner import TransformProcessRunner

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"
PROBE_PROGRAM = "openclaw-probe-tool"
PROBE_FILE_NAME = PROBE_PROGRAM + (".exe" if IS_WINDOWS else "")


def _completed(stdout: str = "ok") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = ""
    return proc


class _ToolRunnerCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env_patch = patch.dict(
            os.environ, {"OPENCLAW_TOOL_SANDBOX_DIR": str(self.root / "sandbox")}
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.runner = ToolRunner(config_path=str(self.root / "missing.json"))

    def add_tool(
        self,
        command: List[str],
        args: Dict[str, str],
        sandbox: Optional[SandboxProfile] = None,
    ) -> None:
        self.runner._tools["probe"] = ToolDefinition(
            name="probe",
            command_template=command,
            allowed_args=args,
            timeout_sec=1,
            sandbox=sandbox or SandboxProfile.strict(),
            sandbox_declared=True,
        )

    def run_probe(self, arguments: Dict[str, str]) -> Tuple[ToolResult, MagicMock]:
        with patch("subprocess.run", return_value=_completed()) as mock_run:
            result = self.runner.execute_tool("probe", arguments)
        return result, mock_run

    def make_program(self, directory: Path, file_name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        program = directory / file_name
        program.write_bytes(b"")
        program.chmod(0o755)
        return program


class ToolRunnerPathBoundaryTests(_ToolRunnerCase):
    def setUp(self) -> None:
        super().setUp()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.add_tool(
            [sys.executable, "{path}"],
            {"path": r".+"},
            SandboxProfile(allow_fs_read=[str(self.allowed)]),
        )

    def test_value_inside_allowed_directory_reaches_spawn(self) -> None:
        result, mock_run = self.run_probe({"path": str(self.allowed / "file.txt")})

        self.assertTrue(result.success, result.error)
        mock_run.assert_called_once()

    def test_sibling_directory_sharing_the_prefix_is_rejected(self) -> None:
        sibling = Path(str(self.allowed) + "-evil") / "file.txt"

        result, mock_run = self.run_probe({"path": str(sibling)})

        self.assertEqual(result.error_code, "workspace_violation")
        mock_run.assert_not_called()

    def test_symlink_inside_allowed_directory_pointing_outside_is_rejected(
        self,
    ) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.allowed / "link.txt"
        try:
            os.symlink(outside, link)
        except (NotImplementedError, OSError):
            self.skipTest("Symlink creation is not available in this environment")

        result, mock_run = self.run_probe({"path": str(link)})

        self.assertEqual(result.error_code, "workspace_violation")
        mock_run.assert_not_called()

    def test_relative_value_is_checked_where_the_tool_runs(self) -> None:
        # The tool runs in the sandbox workspace, so "file.txt" names a file there even
        # when this process's working directory is inside the allowed directory.
        original = os.getcwd()
        os.chdir(self.allowed)
        self.addCleanup(os.chdir, original)

        result, mock_run = self.run_probe({"path": "file.txt"})

        self.assertEqual(result.error_code, "workspace_violation")
        mock_run.assert_not_called()

    def test_relative_value_inside_allowed_sandbox_workspace_passes(self) -> None:
        sandbox = self.root / "sandbox"
        self.add_tool(
            [sys.executable, "{path}"],
            {"path": r".+"},
            SandboxProfile(allow_fs_read=[str(sandbox)]),
        )

        result, mock_run = self.run_probe({"path": "file.txt"})

        self.assertTrue(result.success, result.error)
        mock_run.assert_called_once()

    @unittest.skipUnless(IS_WINDOWS, "directory junctions exist only on Windows")
    def test_junction_inside_allowed_directory_pointing_outside_is_rejected(
        self,
    ) -> None:
        outside = self.root / "outside-dir"
        outside.mkdir()
        junction = self.allowed / "junction"
        try:
            import _winapi

            _winapi.CreateJunction(str(outside), str(junction))
        except (ImportError, AttributeError, OSError):
            self.skipTest("Directory junctions are not available in this environment")

        result, mock_run = self.run_probe({"path": str(junction / "file.txt")})

        self.assertEqual(result.error_code, "workspace_violation")
        mock_run.assert_not_called()

    @unittest.skipUnless(IS_WINDOWS, "Windows paths are case-insensitive")
    def test_case_variant_of_allowed_directory_is_inside_on_windows(self) -> None:
        value = str(self.allowed / "file.txt").swapcase()

        result, mock_run = self.run_probe({"path": value})

        self.assertTrue(result.success, result.error)
        mock_run.assert_called_once()

    @unittest.skipUnless(IS_WINDOWS, "drive letters exist only on Windows")
    def test_other_drive_is_outside_on_windows(self) -> None:
        drive = "Q:" if not str(self.allowed).upper().startswith("Q:") else "R:"

        result, mock_run = self.run_probe({"path": drive + "\\data\\file.txt"})

        self.assertEqual(result.error_code, "workspace_violation")
        mock_run.assert_not_called()

    def test_violation_message_names_the_argument_not_the_value(self) -> None:
        outside = self.root / "other" / "private-file-name.txt"

        result, mock_run = self.run_probe({"path": str(outside)})

        self.assertEqual(result.error_code, "workspace_violation")
        self.assertTrue((result.error or "").startswith("Sandbox FS violation"))
        self.assertNotIn("private-file-name", result.error or "")
        self.assertIn("'path'", result.error or "")
        mock_run.assert_not_called()


class ToolRunnerTemplateTests(_ToolRunnerCase):
    def load(self, tools: List[Dict[str, object]]) -> ToolRunner:
        config = self.root / "tools.json"
        config.write_text(json.dumps({"tools": tools}), encoding="utf-8")
        return ToolRunner(config_path=str(config))

    def test_templates_that_let_a_request_choose_the_program_are_skipped(
        self,
    ) -> None:
        runner = self.load(
            [
                {"name": "placeholder", "command": ["{program}"], "args": {}},
                {"name": "embedded", "command": ["py{version}"], "args": {}},
                {"name": "escaped", "command": ["tool{{x}}"], "args": {}},
                {"name": "relative", "command": [os.path.join("bin", "tool")]},
                {"name": "empty", "command": []},
                {"name": "blank", "command": [" "]},
                {"name": "not_a_list", "command": "tool --flag"},
                {"name": "not_strings", "command": ["tool", 1]},
                {
                    "name": "kept",
                    "command": [sys.executable, "{msg}"],
                    "args": {"msg": "^[a-z]+$"},
                },
            ]
        )

        self.assertEqual({tool["name"] for tool in runner.list_tools()}, {"kept"})

    def test_injected_templated_executable_is_refused_before_spawn(self) -> None:
        self.add_tool(["{program}", "x"], {"program": r".+"})

        result, mock_run = self.run_probe({"program": sys.executable})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "execution_error")
        mock_run.assert_not_called()


class ToolRunnerExecutableResolutionTests(_ToolRunnerCase):
    def test_bare_name_is_spawned_by_absolute_path_found_on_path(self) -> None:
        program = self.make_program(self.root / "bin", PROBE_FILE_NAME)
        self.add_tool([PROBE_PROGRAM, "{msg}"], {"msg": "^[a-z]+$"})

        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}):
            result, mock_run = self.run_probe({"msg": "hello"})

        self.assertTrue(result.success, result.error)
        self.assertEqual(mock_run.call_args.args[0], [str(program), "hello"])

    def test_absolute_executable_is_used_in_place(self) -> None:
        self.add_tool([sys.executable, "{msg}"], {"msg": "^[a-z]+$"})

        result, mock_run = self.run_probe({"msg": "hello"})

        self.assertTrue(result.success, result.error)
        self.assertEqual(mock_run.call_args.args[0], [sys.executable, "hello"])

    def test_program_in_current_directory_is_not_used(self) -> None:
        cwd = self.root / "cwd"
        self.make_program(cwd, PROBE_FILE_NAME)
        empty = self.root / "empty"
        empty.mkdir()
        self.add_tool([PROBE_PROGRAM, "{msg}"], {"msg": "^[a-z]+$"})
        original = os.getcwd()
        os.chdir(cwd)
        self.addCleanup(os.chdir, original)

        with patch.dict(os.environ, {"PATH": str(empty)}):
            result, mock_run = self.run_probe({"msg": "hello"})

        self.assertEqual(result.error_code, "interpreter_missing")
        mock_run.assert_not_called()

    def test_relative_path_entries_are_not_searched(self) -> None:
        self.make_program(self.root / "bin", PROBE_FILE_NAME)
        self.add_tool([PROBE_PROGRAM, "{msg}"], {"msg": "^[a-z]+$"})
        original = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)

        with patch.dict(os.environ, {"PATH": "bin"}):
            result, mock_run = self.run_probe({"msg": "hello"})

        self.assertEqual(result.error_code, "interpreter_missing")
        mock_run.assert_not_called()

    @unittest.skipUnless(IS_WINDOWS, "batch files run through cmd.exe on Windows")
    def test_windows_batch_target_is_refused(self) -> None:
        self.make_program(self.root / "bin", PROBE_PROGRAM + ".cmd")
        self.add_tool([PROBE_PROGRAM + ".cmd", "{msg}"], {"msg": "^[a-z]+$"})

        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}):
            result, mock_run = self.run_probe({"msg": "hello"})

        self.assertEqual(result.error_code, "execution_error")
        self.assertIn("batch", (result.error or "").lower())
        mock_run.assert_not_called()


class ToolRunnerArgumentGuardTests(_ToolRunnerCase):
    def test_control_characters_are_rejected_without_echoing_the_value(
        self,
    ) -> None:
        self.add_tool([sys.executable, "--text={msg}"], {"msg": r"[\s\S]*"})

        for value in (
            "line\nbreak",
            "nul\x00byte",
            "carriage\rreturn",
            "tab\there",
            "delete\x7fchar",
        ):
            with self.subTest(value=repr(value)):
                result, mock_run = self.run_probe({"msg": value})

                self.assertFalse(result.success)
                self.assertIn("control", (result.error or "").lower())
                self.assertNotIn(value, result.error or "")
                mock_run.assert_not_called()

    def test_value_that_would_start_an_argument_with_a_dash_is_rejected(
        self,
    ) -> None:
        cases = (
            ([sys.executable, "{target}"], "--output=elsewhere"),
            ([sys.executable, "{target}"], "-c"),
            ([sys.executable, "{target}.txt"], "-rf"),
        )
        for command, value in cases:
            with self.subTest(command=command[1], value=value):
                self.add_tool(command, {"target": r".+"})

                result, mock_run = self.run_probe({"target": value})

                self.assertFalse(result.success)
                self.assertIn("'target'", result.error or "")
                self.assertNotIn(value, result.error or "")
                mock_run.assert_not_called()

    def test_dash_value_after_an_operator_prefix_is_allowed(self) -> None:
        self.add_tool([sys.executable, "--offset={n}"], {"n": r"-?\d+"})

        result, mock_run = self.run_probe({"n": "-5"})

        self.assertTrue(result.success, result.error)
        self.assertEqual(mock_run.call_args.args[0], [sys.executable, "--offset=-5"])

    def test_pattern_mismatch_message_omits_the_value(self) -> None:
        self.add_tool([sys.executable, "{msg}"], {"msg": "^[a-z]+$"})

        result, mock_run = self.run_probe({"msg": "Sensitive-Value-42"})

        self.assertFalse(result.success)
        self.assertIn("'msg'", result.error or "")
        self.assertNotIn("Sensitive-Value-42", result.error or "")
        mock_run.assert_not_called()


class ShippedAllowlistTests(unittest.TestCase):
    def test_package_allowlist_ships_no_tool(self) -> None:
        data = json.loads(
            (ROOT / "data" / "tools_allowlist.json").read_text(encoding="utf-8")
        )

        self.assertEqual(data, {"tools": []})


class TransformWorkerEnvironmentTests(unittest.TestCase):
    STARTUP_VARIABLES = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
    }

    def test_worker_receives_only_interpreter_startup_variables(self) -> None:
        registry = MagicMock(spec=TransformRegistry)
        registry.get_transform.return_value = TrustedTransform(
            id="env_probe",
            label="Environment probe",
            module_path="unused_module.py",
            sha256="unused",
        )
        registry.verify_integrity.return_value = True
        runner = TransformProcessRunner(
            registry,
            TransformLimits(
                timeout_sec=2.0, max_output_bytes=1024, max_transforms_per_request=1
            ),
        )
        planted = {
            "DATABASE_URL": "postgres://user:pw@db.invalid/app",
            "SERVICE_PASSWORD": "planted",
            "OPENAI_API_KEY": "planted",
            "PYTHONIOENCODING": "utf-8",
        }

        with (
            patch.dict(os.environ, planted),
            patch(
                "subprocess.run",
                return_value=_completed('{"status": "success", "output": {}}'),
            ) as mock_run,
        ):
            expected_path = os.environ.get("PATH")
            runner.execute_transform("env_probe", {}, trace_id="env-probe")

        env = mock_run.call_args.kwargs["env"]
        # Compare names and counts only: a failure message must never print the host
        # environment (values or variable names) into test logs.
        leaked = [
            name
            for name in ("DATABASE_URL", "SERVICE_PASSWORD", "OPENAI_API_KEY")
            if name in env
        ]
        self.assertEqual(leaked, [])
        unexpected = {name.upper() for name in env} - self.STARTUP_VARIABLES
        self.assertEqual(len(unexpected), 0, "unexpected variables reached the worker")
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8")
        self.assertTrue(env.get("PATH") == expected_path, "PATH was not preserved")


class DoctorPipProbeTests(unittest.TestCase):
    def test_pip_version_is_read_without_spawning_a_process(self) -> None:
        def which(name: str) -> Optional[str]:
            return os.path.join("fake-bin", "pip") if name == "pip" else None

        report = DoctorReport()
        with (
            patch("shutil.which", side_effect=which),
            patch(
                "subprocess.check_output", return_value="pip 0.0 from x (python 3)"
            ) as mock_check_output,
        ):
            check_runtime_provenance(report)

        mock_check_output.assert_not_called()
        records = json.loads(report.environment["runtime_provenance"])
        pip_record = next(record for record in records if record["runtime"] == "pip")
        try:
            expected = importlib.metadata.version("pip")
        except importlib.metadata.PackageNotFoundError:
            expected = ""
        self.assertEqual(pip_record["version"], expected)


if __name__ == "__main__":
    unittest.main()
