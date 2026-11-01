import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import preflight_check

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "check-node-runtime.mjs"
POLICY = "^20.19.0 || ^22.12.0 || >=24.0.0"
BOUNDARIES = {
    "18.20.8": False,
    "20.18.0": False,
    "20.19.0": True,
    "20.20.1": True,
    "21.7.0": False,
    "22.11.0": False,
    "22.12.0": True,
    "23.9.0": False,
    "24.13.1": True,
    "25.0.0": True,
    "24.0.0-rc.1": False,
    "unexpected v24.13.1 output": False,
    "": False,
}


class NodeRuntimePolicyTests(unittest.TestCase):
    def _guard_probe(self, version, policy=POLICY):
        code = (
            f"import {{ ensureSupportedNode }} from {json.dumps(GUARD.as_uri())};"
            "try { ensureSupportedNode(process.argv[1], process.argv[2]); }"
            "catch (error) { console.error(error.message); process.exitCode = 1; }"
        )
        return subprocess.run(
            ["node", "--input-type=module", "--eval", code, version, policy],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_declared_and_lock_root_policy_match_locked_tools(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(package["engines"]["node"], POLICY)
        self.assertEqual(lock["packages"][""]["engines"]["node"], POLICY)

    def test_shared_guard_enforces_boundaries_and_rejects_unknown_policy(self):
        for version, supported in BOUNDARIES.items():
            with self.subTest(version=version):
                result = self._guard_probe(version)
                self.assertEqual(result.returncode == 0, supported, result.stderr)
                if not supported:
                    self.assertIn("Required:", result.stderr)
                    self.assertIn("Node 24", result.stderr)
        for policy in (">=20", "*", "^0.2.0", "^20.19.0 || nonsense"):
            with self.subTest(policy=policy):
                result = self._guard_probe("24.13.1", policy)
                self.assertNotEqual(result.returncode, 0)

    def test_preflight_rejects_the_same_false_green_versions(self):
        for version, supported in BOUNDARIES.items():
            with self.subTest(version=version):

                def check_node(command, **_kwargs):
                    # Keep the old raw-version seam reproducible; the delegated
                    # path executes the real guard against the same observed value.
                    if command == ["node", "--version"]:
                        return f"v{version}".encode()
                    self.assertEqual(command, ["node", str(GUARD)])
                    result = self._guard_probe(version)
                    if result.returncode:
                        raise subprocess.CalledProcessError(
                            result.returncode, command, output=result.stderr.encode()
                        )
                    return result.stdout.encode()

                with (
                    patch.object(
                        preflight_check.subprocess,
                        "check_output",
                        side_effect=check_node,
                    ),
                    patch("builtins.print"),
                ):
                    self.assertEqual(preflight_check.check_node_version(), supported)

    def test_missing_node_is_an_actionable_preflight_failure(self):
        with (
            patch.object(
                preflight_check.subprocess,
                "check_output",
                side_effect=FileNotFoundError,
            ),
            patch("builtins.print") as output,
        ):
            self.assertFalse(preflight_check.check_node_version())
        self.assertIn("Node 24", str(output.call_args_list))

    def _fixture(self, directory):
        root = Path(directory)
        (root / "scripts").mkdir()
        for name in (
            "check-node-runtime.mjs",
            "run-playwright.mjs",
            "run_full_tests_windows.ps1",
            "run_full_tests_linux.sh",
        ):
            source = ROOT / "scripts" / name
            if source.exists():
                shutil.copyfile(source, root / "scripts" / name)
        shutil.copyfile(ROOT / "package.json", root / "package.json")
        shim = root / "old-node.cjs"
        shim.write_text(
            "Object.defineProperty(process.versions, 'node', {value: '18.20.8'});\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            NODE_OPTIONS=f'--require "{shim.as_posix()}"',
            PRE_COMMIT_HOME=str(root / ".tmp" / "pre-commit"),
            BLACK_CACHE_DIR=str(root / ".tmp" / "black"),
            OPENCLAW_PLAYWRIGHT_INSTALL="1",
        )
        return root, env

    def test_guard_needs_no_dependencies_and_reads_its_own_manifest(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            root, env = self._fixture(directory)
            env.pop("NODE_OPTIONS", None)
            command = ["node", str(root / "scripts" / "check-node-runtime.mjs")]
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "node_modules").exists())
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            package["engines"]["node"] = ">=99.0.0"
            (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=10
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Required: >=99.0.0", result.stderr)

    def test_direct_npm_entries_reject_before_loading_uninstalled_tools(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            root, env = self._fixture(directory)
            for entry in ("test", "test:unit"):
                with self.subTest(entry=entry):
                    result = subprocess.run(
                        [
                            shutil.which("npm.cmd" if os.name == "nt" else "npm"),
                            "run",
                            entry,
                        ],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Required: {POLICY}", result.stdout + result.stderr)
                    self.assertFalse((root / "node_modules").exists())

    def test_platform_full_script_rejects_before_bootstrap_or_npm(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            root, env = self._fixture(directory)
            if sys.platform == "win32":
                probe = root / "probe.ps1"
                probe.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    "function git { $global:LASTEXITCODE = 0 }\n"
                    "function npm { throw 'NPM_MUST_NOT_RUN' }\n"
                    "function py { throw 'BOOTSTRAP_MUST_NOT_RUN' }\n"
                    f"& '{str(root / 'scripts' / 'run_full_tests_windows.ps1').replace(chr(39), chr(39) * 2)}'\n",
                    encoding="utf-8-sig",
                )
                command = ["powershell.exe", "-NoProfile", "-File", str(probe)]
            else:
                probe = root / "probe.sh"
                probe.write_text(
                    "git() { return 0; }; npm() { echo NPM_MUST_NOT_RUN; return 99; }; "
                    "python3() { echo BOOTSTRAP_MUST_NOT_RUN; return 99; }; "
                    "python() { echo BOOTSTRAP_MUST_NOT_RUN; return 99; }; "
                    "export -f git npm python3 python; bash scripts/run_full_tests_linux.sh\n",
                    encoding="utf-8",
                )
                command = ["bash", str(probe)]
            result = subprocess.run(
                command, cwd=root, env=env, capture_output=True, text=True, timeout=20
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Required: {POLICY}", combined)
            self.assertNotIn("BOOTSTRAP_MUST_NOT_RUN", combined)
            self.assertNotIn("NPM_MUST_NOT_RUN", combined)
            self.assertFalse((root / ".venv").exists())

    def test_both_shells_use_shared_guard_before_runtime_bootstrap(self):
        for name, bootstrap in (
            ("run_full_tests_windows.ps1", "# Prefer project-local"),
            ("run_full_tests_linux.sh", "# Always use project-local"),
            ("pre_push_checks.sh", 'VENV_DIR="$(select_venv_dir)"'),
        ):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertLess(
                    text.index("check-node-runtime.mjs"),
                    text.index(bootstrap),
                )
                self.assertNotIn("nvm use 18", text)
