"""Registry archive scope.

`comfy node publish` zips the files listed by `git ls-files`, minus the paths that
`.comfyignore` matches with gitwildmatch rules (the last matching pattern wins). These tests
derive that shipped set and pin what it must and must not contain.
"""

import ast
import importlib.util
import re
import subprocess
import unittest
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE_FILE = ROOT / ".comfyignore"

RUNTIME_FILES = frozenset(
    {
        "__init__.py",
        "config.py",
        "pyproject.toml",
        "requirements.txt",
        "LICENSE",
        "README.md",
    }
)
RUNTIME_DIRECTORIES = (
    "api/",
    "assets/",
    "connector/",
    "data/",
    "docs/",
    "models/",
    "nodes/",
    "services/",
    "web/",
)
# Operator commands that user-facing docs tell people to run on an installed pack.
OPERATOR_SCRIPTS = frozenset(
    {
        "scripts/check_deployment_profile.py",
        "scripts/openclaw_smoke_import.py",
        "scripts/operator_doctor.py",
        "scripts/run_crypto_lifecycle_drills.py",
        "scripts/verify_audit_chain.py",
    }
)
# Documented maintainer commands that need a source checkout (tests or release tooling).
SOURCE_CHECKOUT_COMMANDS = frozenset(
    {
        "scripts/generate_provenance.py",
        "scripts/run_full_tests_linux.sh",
        "scripts/run_full_tests_windows.ps1",
        "scripts/run_unittests.py",
        "scripts/verify_provenance.py",
    }
)
DEVELOPMENT_DIRECTORIES = ("tests/", "scripts/", ".github/", ".githooks/")
DEVELOPMENT_FILES = frozenset(
    {
        ".comfyignore",
        ".detect-secrets.cfg",
        ".gitattributes",
        ".gitignore",
        ".nvmrc",
        ".pre-commit-config.yaml",
        ".secrets.baseline",
        "package-lock.json",
        "package.json",
        "playwright.config.js",
        "playwright.real-host.config.js",
        "requirements-quality.txt",
        "vitest.config.mjs",
    }
)
INTERNAL_PREFIXES = (
    ".planning/",
    ".reference/",
    ".sessions/",
    ".tmp/",
    "REFERENCE/",
    "reference/",
)
# Reviewed shipped modules that start processes. A new entry fails until it is reviewed.
PROCESS_API_INVENTORY = frozenset(
    {
        "services/operator_doctor.py",
        "services/secret_providers.py",
        "services/tool_runner.py",
        "services/transform_runner.py",
    }
)
PROCESS_MODULES = frozenset({"pty", "subprocess"})
OS_PROCESS_CALL = re.compile(
    r"^(?:system|popen|startfile|exec\w*|spawn\w*|posix_spawn\w*)$"
)
ASYNC_PROCESS_CALLS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
JS_PROCESS_OR_EVAL = re.compile(
    r"\bchild_process\b|\bexecSync\b|(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\("
)
SCRIPT_REFERENCE = re.compile(r"scripts/[A-Za-z0-9_./-]+\.(?:py|sh|ps1|js|mjs)")
SUBPROCESS_RUN_CALL = re.compile(r"\bsubprocess\.run\s*\(")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


@lru_cache(maxsize=1)
def tracked_files() -> frozenset[str]:
    return frozenset(path for path in _git("ls-files", "-z").split("\0") if path)


@lru_cache(maxsize=1)
def excluded_files() -> frozenset[str]:
    output = _git(
        "ls-files", "-z", "--cached", "--ignored", f"--exclude-from={IGNORE_FILE}"
    )
    return frozenset(path for path in output.split("\0") if path)


def shipped_files() -> frozenset[str]:
    return tracked_files() - excluded_files()


def ignore_patterns() -> list[str]:
    # Same line rules as comfy-cli: strip, drop blank lines and comments.
    lines = IGNORE_FILE.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def python_uses_process_api(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] in PROCESS_MODULES for alias in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] in PROCESS_MODULES
        ):
            return True
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and OS_PROCESS_CALL.match(node.attr)
            ):
                return True
            if node.attr in ASYNC_PROCESS_CALLS:
                return True
    return False


def python_dynamic_or_installer_lines(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec"}
        ):
            lines.append(node.lineno)
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            pairs = set(zip(values, values[1:]))
            if {("-m", "pip"), ("pip", "install"), ("pip3", "install")} & pairs or (
                "ensurepip" in values
            ):
                lines.append(node.lineno)
    return lines


class RegistryPackageScopeTests(unittest.TestCase):
    def test_ignore_file_exists_with_patterns(self) -> None:
        self.assertTrue(IGNORE_FILE.is_file())
        self.assertGreater(len(ignore_patterns()), 0)

    def test_tracked_paths_need_no_quoting(self) -> None:
        # comfy-cli reads plain `git ls-files` output; a quoted path would not exist on disk
        # and would be dropped from the archive silently.
        plain = frozenset(line for line in _git("ls-files").splitlines() if line)
        self.assertEqual(plain, tracked_files())

    def test_runtime_content_ships(self) -> None:
        shipped = shipped_files()
        self.assertLessEqual(RUNTIME_FILES, shipped)
        for directory in RUNTIME_DIRECTORIES:
            with self.subTest(directory=directory):
                tracked = {
                    path for path in tracked_files() if path.startswith(directory)
                }
                self.assertTrue(tracked)
                self.assertEqual(sorted(tracked - shipped), [])

    def test_documented_operator_scripts_ship(self) -> None:
        shipped = shipped_files()
        shipped_scripts = {path for path in shipped if path.startswith("scripts/")}
        self.assertEqual(shipped_scripts, OPERATOR_SCRIPTS)
        self.assertLessEqual(SOURCE_CHECKOUT_COMMANDS, tracked_files())

        documents = ["README.md"] + sorted(
            str(path.relative_to(ROOT).as_posix())
            for path in (ROOT / "docs").glob("*.md")
        )
        for document in documents:
            for reference in set(SCRIPT_REFERENCE.findall(read_text(document))):
                with self.subTest(document=document, reference=reference):
                    self.assertIn(
                        reference, OPERATOR_SCRIPTS | SOURCE_CHECKOUT_COMMANDS
                    )

    def test_development_and_test_tooling_does_not_ship(self) -> None:
        shipped = shipped_files()
        leaked = sorted(
            path
            for path in shipped
            if path in DEVELOPMENT_FILES
            or (
                path.startswith(DEVELOPMENT_DIRECTORIES)
                and path not in OPERATOR_SCRIPTS
            )
        )
        self.assertEqual(leaked, [])

    def test_internal_paths_are_neither_tracked_nor_shipped(self) -> None:
        self.assertEqual(
            sorted(
                path for path in tracked_files() if path.startswith(INTERNAL_PREFIXES)
            ),
            [],
        )

    def test_shipped_process_api_users_match_reviewed_inventory(self) -> None:
        users = set()
        for path in sorted(shipped_files()):
            if path.endswith(".py"):
                if python_uses_process_api(ast.parse(read_text(path), filename=path)):
                    users.add(path)
            elif path.endswith((".js", ".mjs", ".cjs")):
                if JS_PROCESS_OR_EVAL.search(read_text(path)):
                    users.add(path)

        self.assertEqual(users, PROCESS_API_INVENTORY)

    def test_shipped_python_has_no_dynamic_execution_or_installer_call(self) -> None:
        for path in sorted(shipped_files()):
            if not path.endswith(".py"):
                continue
            with self.subTest(path=path):
                tree = ast.parse(read_text(path), filename=path)
                self.assertEqual(python_dynamic_or_installer_lines(tree), [])

    def test_shipped_subprocess_run_sites_are_the_runtime_services(self) -> None:
        sites = {}
        for path in sorted(shipped_files()):
            if path.endswith(".py"):
                count = len(SUBPROCESS_RUN_CALL.findall(read_text(path)))
                if count:
                    sites[path] = count

        self.assertEqual(
            sites,
            {
                "services/secret_providers.py": 1,
                "services/tool_runner.py": 1,
                "services/transform_runner.py": 1,
            },
        )

    def test_pathspec_and_git_agree_on_the_excluded_set(self) -> None:
        if importlib.util.find_spec("pathspec") is None:
            self.skipTest("pathspec is not installed")
        from pathspec import PathSpec

        spec = PathSpec.from_lines("gitwildmatch", ignore_patterns())
        excluded = frozenset(path for path in tracked_files() if spec.match_file(path))

        self.assertEqual(excluded, excluded_files())


if __name__ == "__main__":
    unittest.main()
