import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
PRE_PUSH = ROOT / "scripts" / "pre_push_checks.sh"
FULL_TESTS_LINUX = ROOT / "scripts" / "run_full_tests_linux.sh"
FULL_TESTS_WINDOWS = ROOT / "scripts" / "run_full_tests_windows.ps1"
PREFLIGHT = ROOT / "scripts" / "preflight_check.py"
PYPROJECT = ROOT / "pyproject.toml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
# CI jobs that run full discovery under tests/skip_policy.json.
SKIP_POLICY_CI_JOBS = ("unit-tests", "python-compatibility")


def _optional_dependency_packages() -> list[str]:
    """Distribution names of every ``[project.optional-dependencies]`` requirement."""
    text = PYPROJECT.read_text(encoding="utf-8")
    section = re.search(
        r"^\[project\.optional-dependencies\]\n(?P<body>.*?)(?=^\[|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if section is None:
        return []
    packages = []
    for match in re.finditer(
        r"^[A-Za-z0-9_.-]+\s*=\s*(?P<value>\[.*?\])",
        section.group("body"),
        flags=re.MULTILINE | re.DOTALL,
    ):
        for requirement in ast.literal_eval(match.group("value")):
            packages.append(re.match(r"[A-Za-z0-9_.-]+", requirement).group(0))
    return packages


def _job_block(text: str, job_id: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing CI job: {job_id}")
    return match.group("body")


def _parse_required_python_packages():
    module = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "REQUIRED_PYTHON_PACKAGES":
                return ast.literal_eval(node.value)
    raise AssertionError(
        "REQUIRED_PYTHON_PACKAGES missing from scripts/preflight_check.py"
    )


class DependencyParityContractTests(unittest.TestCase):
    def test_requirements_stays_aligned_with_declared_runtime_dependencies(self):
        requirements = REQUIREMENTS.read_text(encoding="utf-8")
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("cryptography>=41.0", requirements)
        self.assertIn("defusedxml>=0.7.1", requirements)
        self.assertIn(
            'dependencies = ["cryptography>=41.0", "defusedxml>=0.7.1"]', pyproject
        )

    def test_preflight_declares_all_essential_runtime_packages(self):
        required = _parse_required_python_packages()
        self.assertIn(("cryptography", "41.0"), required)
        self.assertIn(("defusedxml", "0.7.1"), required)

    def test_local_acceptance_bootstraps_install_defusedxml(self):
        self.assertIn("import defusedxml", PRE_PUSH.read_text(encoding="utf-8"))
        self.assertIn(
            "pip install defusedxml", FULL_TESTS_WINDOWS.read_text(encoding="utf-8")
        )
        self.assertIn("import defusedxml", FULL_TESTS_LINUX.read_text(encoding="utf-8"))

    def test_frontend_e2e_preflight_uses_requirements_contract(self):
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python -m pip install -r requirements.txt", workflow)

    def test_skip_policy_lanes_install_every_optional_extra(self):
        # IMPORTANT: a lane without an optional extra skips its tests and spends skip
        # budget the Windows gate never sees (v1.3.0 Linux CI failed this way).
        packages = _optional_dependency_packages()
        self.assertIn("pycryptodomex", packages)
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        lanes = {
            "scripts/run_full_tests_windows.ps1": FULL_TESTS_WINDOWS.read_text(
                encoding="utf-8"
            ),
            "scripts/pre_push_checks.sh": PRE_PUSH.read_text(encoding="utf-8"),
            "scripts/run_full_tests_linux.sh": FULL_TESTS_LINUX.read_text(
                encoding="utf-8"
            ),
        }
        for job in SKIP_POLICY_CI_JOBS:
            lanes[f"ci.yml {job}"] = _job_block(workflow, job)
        for lane, text in lanes.items():
            with self.subTest(lane=lane):
                self.assertIn("--enforce-skip-policy", text)
            for package in packages:
                with self.subTest(lane=lane, package=package):
                    self.assertRegex(
                        text,
                        rf"pip(?:_install_or_fail| install)\b[^\n]*\b{re.escape(package)}\b",
                    )


if __name__ == "__main__":
    unittest.main()
