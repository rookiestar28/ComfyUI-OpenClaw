import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
HELPER_PATH = ROOT / "scripts" / "bootstrap_project_venv.py"
TEST_SOP = ROOT / "tests" / "TEST_SOP.md"
R240_TEST = ROOT / "tests" / "test_r240_loader_ready_route_integration.py"

SCOPED_WORKFLOWS = {
    "ci.yml": 8,
    "pre-commit.yml": 1,
    "secret-scan.yml": 1,
}
BOOTSTRAP_COMMAND = "python scripts/bootstrap_project_venv.py"


def _workflow_steps(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^\s{6}- (?:name:|uses:)", line)
    ]
    starts.append(len(lines))
    return ["\n".join(lines[start:end]) for start, end in zip(starts, starts[1:])]


def _load_helper():
    if not HELPER_PATH.is_file():
        raise AssertionError(f"project-venv bootstrap helper missing: {HELPER_PATH}")
    spec = importlib.util.spec_from_file_location("bootstrap_project_venv", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load bootstrap helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostedWorkflowVenvContractTests(unittest.TestCase):
    def test_every_setup_python_step_immediately_bootstraps_project_venv(self):
        for workflow_name, expected_count in SCOPED_WORKFLOWS.items():
            with self.subTest(workflow=workflow_name):
                text = (WORKFLOW_ROOT / workflow_name).read_text(encoding="utf-8")
                steps = _workflow_steps(text)
                setup_indexes = [
                    index
                    for index, step in enumerate(steps)
                    if "uses: actions/setup-python@v6" in step
                ]
                self.assertEqual(len(setup_indexes), expected_count)
                for setup_index in setup_indexes:
                    self.assertLess(setup_index + 1, len(steps))
                    bootstrap_step = steps[setup_index + 1]
                    self.assertIn(
                        "name: Bootstrap project-local Python", bootstrap_step
                    )
                    self.assertIn(BOOTSTRAP_COMMAND, bootstrap_step)

    def test_bootstrap_helper_is_bounded_and_dependency_free(self):
        self.assertTrue(
            HELPER_PATH.is_file(), "project-venv bootstrap helper is required"
        )
        source = HELPER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "pip install",
            "requests",
            "urllib",
            "rmtree",
            "shell=True",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_invalid_existing_venv_fails_closed_without_deletion(self):
        helper = _load_helper()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            marker = repo_root / ".venv" / "keep.txt"
            marker.parent.mkdir()
            marker.write_text("preserve", encoding="utf-8")
            github_path = repo_root / "github_path.txt"

            with self.assertRaises(helper.ProjectVenvBootstrapError):
                helper.bootstrap_project_venv(repo_root, github_path)

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertFalse(github_path.exists())

    def test_validated_venv_executable_directory_is_exported(self):
        helper = _load_helper()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            venv_dir = repo_root / ".venv"
            venv_python = helper.project_venv_python(venv_dir)
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("placeholder", encoding="utf-8")
            github_path = repo_root / "github_path.txt"
            probe = subprocess.CompletedProcess(
                args=[str(venv_python)],
                returncode=0,
                stdout=f"{venv_python.resolve()}\n",
                stderr="",
            )

            with patch.object(helper.subprocess, "run", return_value=probe) as run:
                exported = helper.bootstrap_project_venv(repo_root, github_path)

            self.assertEqual(exported, venv_python.parent.resolve())
            self.assertEqual(
                github_path.read_text(encoding="utf-8").splitlines(),
                [str(venv_python.parent.resolve())],
            )
            run.assert_called_once()

    def test_external_interpreter_identity_is_rejected(self):
        helper = _load_helper()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            venv_python = helper.project_venv_python(repo_root / ".venv")
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("placeholder", encoding="utf-8")
            external_python = repo_root / "toolcache" / "python"
            external_python.parent.mkdir()
            external_python.write_text("placeholder", encoding="utf-8")
            github_path = repo_root / "github_path.txt"
            probe = subprocess.CompletedProcess(
                args=[str(venv_python)],
                returncode=0,
                stdout=f"{external_python.resolve()}\n",
                stderr="",
            )

            with patch.object(helper.subprocess, "run", return_value=probe):
                with self.assertRaises(helper.ProjectVenvBootstrapError):
                    helper.bootstrap_project_venv(repo_root, github_path)

            self.assertFalse(github_path.exists())

    def test_missing_venv_is_created_only_with_stdlib_venv(self):
        helper = _load_helper()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            bootstrap_python = repo_root / "base-python"
            bootstrap_python.write_text("placeholder", encoding="utf-8")
            github_path = repo_root / "github_path.txt"
            venv_python = helper.project_venv_python(repo_root / ".venv")

            def fake_run(args, **kwargs):
                if args[1:4] == ["-m", "venv", "--copies"]:
                    venv_python.parent.mkdir(parents=True)
                    venv_python.write_text("placeholder", encoding="utf-8")
                    return subprocess.CompletedProcess(args=args, returncode=0)
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=f"{venv_python.resolve()}\n",
                    stderr="",
                )

            with patch.object(helper.subprocess, "run", side_effect=fake_run) as run:
                helper.bootstrap_project_venv(
                    repo_root,
                    github_path,
                    bootstrap_python=bootstrap_python,
                )

            create_args = run.call_args_list[0].args[0]
            self.assertEqual(
                create_args,
                [
                    str(bootstrap_python.resolve()),
                    "-m",
                    "venv",
                    "--copies",
                    str(repo_root / ".venv"),
                ],
            )
            self.assertEqual(run.call_count, 2)

    def test_sop_and_loader_contract_keep_project_venv_mandatory(self):
        sop = TEST_SOP.read_text(encoding="utf-8")
        loader_test = R240_TEST.read_text(encoding="utf-8")

        self.assertIn("Project-local venv required", sop)
        self.assertNotIn("**Project venv recommended**", sop)
        self.assertIn(
            'raise AssertionError("child interpreter is not project-local .venv")',
            loader_test,
        )
        self.assertNotIn("skipTest", loader_test)


if __name__ == "__main__":
    unittest.main()
