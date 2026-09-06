"""The directory this pack serves to browsers must contain runtime code only.

ComfyUI builds its ``/extensions`` listing by globbing ``**/*.js`` under every registered
web directory and the frontend imports every entry it finds, so anything shipped there is
executed in the user's browser on every page load. Test files placed under that directory
are fetched, fail to resolve their test-runner imports, and leave a permanent cluster of
console errors that masks real ones.
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_INIT = REPO_ROOT / "__init__.py"

TEST_FILE_SUFFIXES = (".test.js", ".spec.js")
TEST_DIRECTORY_NAMES = frozenset({"tests", "test", "__tests__"})


def _declared_web_directory() -> str:
    """Read WEB_DIRECTORY out of the package source without importing the package.

    Importing the package would start the node registration and security machinery, which
    this test has no business exercising. A static read also means the test keeps working
    if the declaration ever moves behind an import guard.
    """

    tree = ast.parse(PACKAGE_INIT.read_text(encoding="utf-8"), filename=str(PACKAGE_INIT))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "WEB_DIRECTORY":
                if not isinstance(node.value, ast.Constant) or not isinstance(
                    node.value.value, str
                ):
                    raise AssertionError(
                        "WEB_DIRECTORY is no longer a literal string, so this test can no "
                        "longer tell which directory is served. Teach it the new form; do "
                        "not weaken it."
                    )
                return node.value.value
    raise AssertionError(
        "The pack no longer declares WEB_DIRECTORY. If serving a web directory was "
        "deliberately dropped, delete this test; do not weaken it."
    )


def _served_root() -> Path:
    """Resolve the directory the pack actually registers, not a hardcoded guess."""

    return (REPO_ROOT / _declared_web_directory()).resolve()


class TheServedWebDirectoryShipsRuntimeCodeOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.served_root = _served_root()
        self.assertTrue(
            self.served_root.is_dir(),
            f"WEB_DIRECTORY resolves to {self.served_root}, which is not a directory.",
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(REPO_ROOT).as_posix()

    def test_no_test_file_is_shipped_into_the_served_directory(self) -> None:
        offenders = sorted(
            self._relative(path)
            for path in self.served_root.rglob("*")
            if path.is_file() and path.name.endswith(TEST_FILE_SUFFIXES)
        )
        self.assertEqual(
            offenders,
            [],
            "These test files sit under the served web directory, so every ComfyUI host "
            "hands them to the browser and the browser fails to run them. Move them out "
            "of the served tree instead of renaming or deleting them:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_test_directory_is_shipped_into_the_served_directory(self) -> None:
        offenders = sorted(
            self._relative(path)
            for path in self.served_root.rglob("*")
            if path.is_dir() and path.name in TEST_DIRECTORY_NAMES
        )
        self.assertEqual(
            offenders,
            [],
            "These test directories sit under the served web directory. Everything "
            "beneath them is published to browsers and to anyone who reads /extensions:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
