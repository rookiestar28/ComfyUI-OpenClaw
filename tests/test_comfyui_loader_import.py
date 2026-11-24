"""\
Smoke test: simulate ComfyUI custom node loader behavior.

Goal: Ensure this repo can be imported when loaded by file path (as ComfyUI does),
without relying on the caller to pre-configure sys.path.

This catches issues like:
- `ModuleNotFoundError: No module named 'services.*'`
- broken relative imports when module is loaded as a package via file loader
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHILD_TIMEOUT_SEC = 90

# CRITICAL: keep this import in a child interpreter. Importing the package without a host
# PromptServer starts the real host-wait owner thread (`openclaw-route-retry`, a daemon
# with about two minutes of bounded retries). Inside the test process that thread outlives
# this test, and the import also changes environment variables and module aliases. Later
# tests then see foreign threads appear and disappear. The child's exit ends the thread
# with the process; do not move the import back in-process.
CHILD_SCRIPT = """
import importlib
import importlib.util
import json
import os
import sys

repo_root, init_path = sys.argv[1], sys.argv[2]
root_key = os.path.abspath(repo_root)
root_was_on_sys_path = any(os.path.abspath(p) == root_key for p in sys.path)
# Make the test meaningful: the repository root must not be importable by accident.
sys.path = [p for p in sys.path if os.path.abspath(p) != root_key]

name = "comfyui_openclaw_loader_smoke"
# Simulate ComfyUI: load the module by file path as a *package*.
spec = importlib.util.spec_from_file_location(
    name, init_path, submodule_search_locations=[repo_root]
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)

mappings = module.NODE_CLASS_MAPPINGS
planner_cls = mappings["MoltbotPromptPlanner"]
# After import, `services.*` must be importable because __init__.py self-heals sys.path.
llm_mod = importlib.import_module("services.llm_client")
planner_mod = importlib.import_module("nodes.prompt_planner")

print(
    json.dumps(
        {
            "llm_client_has_llmclient": hasattr(llm_mod, "LLMClient"),
            "planner_alias_matches": (
                planner_mod.MoltbotPromptPlanner is planner_mod.OpenClawPromptPlanner
            ),
            "planner_class_name": planner_cls.__name__,
            "portability_keys_match": sorted(module.NODE_PORTABILITY_MAPPINGS)
            == sorted(mappings),
            "repo_root_was_on_sys_path": root_was_on_sys_path,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
"""

EXPECTED_CHILD_RESULT = {
    "llm_client_has_llmclient": True,
    "planner_alias_matches": True,
    "planner_class_name": "OpenClawPromptPlanner",
    "portability_keys_match": True,
    "repo_root_was_on_sys_path": False,
}


def _try_writable_dir(p: Path) -> str | None:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / "._write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return str(p)
    except Exception:
        return None


def _select_state_dir(repo_root: Path) -> tuple[str | None, bool]:
    """Pick a writable state dir; the bool tells the caller to remove it afterwards.

    Prefer an explicit env var (CI sets this), otherwise a repo-local folder,
    otherwise a system temp dir.
    """
    env_dir = os.environ.get("OPENCLAW_STATE_DIR") or os.environ.get(
        "MOLTBOT_STATE_DIR"
    )
    if env_dir:
        chosen = _try_writable_dir(Path(env_dir))
        if chosen:
            return chosen, False

    chosen = _try_writable_dir(
        repo_root / "openclaw_state" / "_unittest" / "loader_smoke"
    )
    if chosen:
        return chosen, False

    try:
        return tempfile.mkdtemp(prefix="openclaw_state_loader_smoke_"), True
    except Exception:
        return None, False


class TestComfyUICustomNodeLoaderImport(unittest.TestCase):
    def test_init_import_via_file_loader(self):
        repo_root = Path(__file__).resolve().parent.parent
        init_path = repo_root / "__init__.py"
        self.assertTrue(init_path.exists())

        chosen, remove_after = _select_state_dir(repo_root)
        if not chosen:
            self.skipTest(
                "No writable OPENCLAW_STATE_DIR/MOLTBOT_STATE_DIR available for import smoke test"
            )

        try:
            # The chosen state dir goes to the child only; this process's environment
            # stays untouched.
            child_env = dict(os.environ)
            child_env["OPENCLAW_STATE_DIR"] = chosen
            child_env["MOLTBOT_STATE_DIR"] = chosen
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        CHILD_SCRIPT,
                        str(repo_root),
                        str(init_path),
                    ],
                    cwd=str(repo_root),
                    env=child_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=CHILD_TIMEOUT_SEC,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self.fail(f"loader import child exceeded {CHILD_TIMEOUT_SEC}s")
        finally:
            if remove_after:
                shutil.rmtree(chosen, ignore_errors=True)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        # Normal startup may legitimately log a security-gate warning on stderr, and the
        # text depends on the environment, so stderr is not required to be empty. Any
        # traceback is a failure, including a daemon thread that crashed.
        self.assertNotIn("Traceback (most recent call last)", completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(lines[0]), EXPECTED_CHILD_RESULT)


if __name__ == "__main__":
    unittest.main()
