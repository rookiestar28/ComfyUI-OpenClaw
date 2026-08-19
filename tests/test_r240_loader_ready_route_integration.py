"""Loader-ready route integration regression through the real package entrypoint."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "synthetic_name"
EXPECTED_CONTRACT_MODULES = {
    "BridgeHandlers": f"{PACKAGE_NAME}.api.bridge",
    "register_approval_routes": f"{PACKAGE_NAME}.api.approvals",
    "register_preset_routes": f"{PACKAGE_NAME}.api.presets",
    "register_routes": f"{PACKAGE_NAME}.api.routes",
    "register_schedule_routes": f"{PACKAGE_NAME}.api.schedules",
    "register_trigger_routes": f"{PACKAGE_NAME}.api.triggers",
}
EXPECTED_NODE_MAPPING_KEYS = sorted(
    (
        "MoltbotBatchVariants",
        "MoltbotImageToPrompt",
        "MoltbotPromptPlanner",
        "MoltbotPromptRefiner",
    )
)
EXPECTED_ROUTE_PATHS = sorted(
    (
        "/api/openclaw/approvals",
        "/api/openclaw/bridge/submit",
        "/api/openclaw/config",
        "/api/openclaw/presets",
        "/api/openclaw/schedules",
        "/api/openclaw/triggers/fire",
        "/openclaw/approvals",
        "/openclaw/bridge/submit",
        "/openclaw/config",
        "/openclaw/presets",
        "/openclaw/schedules",
        "/openclaw/triggers/fire",
    )
)
EXPECTED_RESULT_KEYS = {
    "contract_modules",
    "initial_attempt",
    "node_mapping_keys",
    "package_name",
    "registration_state",
    "retry_owner_alive",
    "route_paths",
    "side_effects_started",
}
ALLOWED_NEUTRALIZATION_TARGETS = frozenset(
    {
        f"{PACKAGE_NAME}.services.runtime_lifecycle.register_shutdown_hooks",
        f"{PACKAGE_NAME}.services.plugins.builtin.register_all",
        f"{PACKAGE_NAME}.services.scheduler.runner.start_scheduler",
        f"{PACKAGE_NAME}.services.bootstrap.registration._build_optional_startup_warmups",
    }
)
ALLOWED_SIDE_EFFECT_VALUES = frozenset(
    {
        "builtin_plugin_registration",
        "optional_startup_warmup",
        "scheduler_thread",
        "shutdown_hook",
    }
)
_CHILD_STAGE = "setup"


class _RecordingResource:
    def __init__(self, path: str) -> None:
        self.canonical = path


class _RecordingRoute:
    def __init__(self, method: str, path: str, handler) -> None:
        self.method = method
        self.handler = handler
        self.resource = _RecordingResource(path)


class _RecordingRouter:
    def __init__(self) -> None:
        self._routes: list[_RecordingRoute] = []

    def add_route(self, method: str, path: str, handler):
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise RuntimeError("unsupported_route_method")
        if not isinstance(path, str) or not callable(handler):
            raise RuntimeError("unsupported_route_registration")
        route = _RecordingRoute(method, path, handler)
        self._routes.append(route)
        return route

    def add_get(self, path: str, handler):
        return self.add_route("GET", path, handler)

    def add_post(self, path: str, handler):
        return self.add_route("POST", path, handler)

    def add_put(self, path: str, handler):
        return self.add_route("PUT", path, handler)

    def add_delete(self, path: str, handler):
        return self.add_route("DELETE", path, handler)

    def routes(self):
        return tuple(self._routes)


class _RecordingRouteTable:
    def __init__(self) -> None:
        self._routes: list[_RecordingRoute] = []

    def _decorator(self, method: str, path: str):
        def register(handler):
            self._routes.append(_RecordingRoute(method, path, handler))
            return handler

        return register

    def get(self, path: str):
        return self._decorator("GET", path)

    def post(self, path: str):
        return self._decorator("POST", path)

    def put(self, path: str):
        return self._decorator("PUT", path)

    def delete(self, path: str):
        return self._decorator("DELETE", path)


class _RecordingApp:
    def __init__(self) -> None:
        self.router = _RecordingRouter()


class _RecordingPromptServer:
    def __init__(self) -> None:
        self.routes = _RecordingRouteTable()
        self.app = _RecordingApp()
        self.on_prompt_handlers: list[object] = []

    def add_on_prompt_handler(self, handler) -> None:
        self.on_prompt_handlers.append(handler)


def _thread_fingerprint() -> tuple[tuple[str, int | None, bool], ...]:
    return tuple(
        sorted(
            (thread.name, thread.ident, thread.daemon)
            for thread in threading.enumerate()
        )
    )


def _top_level_api_aliases() -> tuple[str, ...]:
    return tuple(
        sorted(name for name in sys.modules if name == "api" or name.startswith("api."))
    )


def _allowlisted_child_environment(state_dir: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH")
        if os.environ.get(key)
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENCLAW_DEPLOYMENT_PROFILE": "local",
            "OPENCLAW_STATE_DIR": str(state_dir),
            "MOLTBOT_STATE_DIR": str(state_dir),
        }
    )
    return environment


def _install_side_effect_neutralization(calls: set[str]) -> None:
    global _CHILD_STAGE
    _CHILD_STAGE = "neutralization_registration"
    registration = importlib.import_module(
        f"{PACKAGE_NAME}.services.bootstrap.registration"
    )
    _CHILD_STAGE = "neutralization_runtime"
    runtime_lifecycle = importlib.import_module(
        f"{PACKAGE_NAME}.services.runtime_lifecycle"
    )
    _CHILD_STAGE = "neutralization_plugins"
    builtin_plugins = importlib.import_module(
        f"{PACKAGE_NAME}.services.plugins.builtin"
    )
    _CHILD_STAGE = "neutralization_scheduler"
    scheduler_runner = importlib.import_module(
        f"{PACKAGE_NAME}.services.scheduler.runner"
    )
    _CHILD_STAGE = "neutralization_table"
    targets = {
        f"{PACKAGE_NAME}.services.runtime_lifecycle.register_shutdown_hooks": (
            runtime_lifecycle,
            "register_shutdown_hooks",
            None,
        ),
        f"{PACKAGE_NAME}.services.plugins.builtin.register_all": (
            builtin_plugins,
            "register_all",
            None,
        ),
        f"{PACKAGE_NAME}.services.scheduler.runner.start_scheduler": (
            scheduler_runner,
            "start_scheduler",
            None,
        ),
        f"{PACKAGE_NAME}.services.bootstrap.registration._build_optional_startup_warmups": (
            registration,
            "_build_optional_startup_warmups",
            (),
        ),
    }
    if frozenset(targets) != ALLOWED_NEUTRALIZATION_TARGETS:
        raise AssertionError("neutralization_allowlist_changed")

    for target, (module, name, result) in targets.items():

        def neutralized(*_args, _target=target, _result=result, **_kwargs):
            calls.add(_target)
            return _result

        setattr(module, name, neutralized)


def _install_network_guard():
    original_create_connection = socket.create_connection

    def denied_network(*_args, **_kwargs):
        raise RuntimeError("network_disabled")

    socket.create_connection = denied_network

    def restore() -> None:
        socket.create_connection = original_create_connection

    return restore


def _execute_real_package() -> dict[str, object]:
    global _CHILD_STAGE
    _CHILD_STAGE = "setup"
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"]).resolve()
    state_root = (ROOT / ".tmp" / "r240-state").resolve()
    if state_root not in state_dir.parents:
        raise AssertionError("state_path_outside_workspace_temp")
    if _top_level_api_aliases():
        raise AssertionError("top_level_api_preloaded")

    original_sys_path = list(sys.path)
    root_text = str(ROOT.resolve())
    sys.path = [
        entry for entry in sys.path if os.path.abspath(entry or os.curdir) != root_text
    ]
    before_threads = _thread_fingerprint()
    neutralized_calls: set[str] = set()
    restore_network = _install_network_guard()
    host = _RecordingPromptServer()
    server_module = types.ModuleType("server")
    server_module.PromptServer = types.SimpleNamespace(instance=host)
    sys.modules["server"] = server_module

    init_path = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        str(init_path),
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError("file_loader_spec_unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package

    try:
        # Import only the real registration owner before executing the root entrypoint so
        # the four test-only external side-effect seams can be neutralized in-package.
        _CHILD_STAGE = "registration_import"
        registration = importlib.import_module(
            f"{PACKAGE_NAME}.services.bootstrap.registration"
        )
        _CHILD_STAGE = "queue_submit_import"
        importlib.import_module(f"{PACKAGE_NAME}.services.queue_submit")
        _CHILD_STAGE = "neutralization"
        _install_side_effect_neutralization(neutralized_calls)
        _CHILD_STAGE = "root_execution"
        spec.loader.exec_module(package)

        _CHILD_STAGE = "alias_proof"
        facade = importlib.import_module(f"{PACKAGE_NAME}.services.route_bootstrap")
        implementation = importlib.import_module(
            f"{PACKAGE_NAME}.services.bootstrap.registration"
        )
        if facade is not implementation:
            raise AssertionError("route_bootstrap_facade_identity_changed")

        _CHILD_STAGE = "contract"
        contract_module = importlib.import_module(
            f"{PACKAGE_NAME}.services.route_bootstrap_contract"
        )
        contract = contract_module.load_route_bootstrap_contract()
        contract_modules = {
            key: getattr(value, "__module__", "") for key, value in contract.items()
        }
        if contract_modules != EXPECTED_CONTRACT_MODULES:
            raise AssertionError("packaged_contract_origins_changed")
        if not all(callable(value) for value in contract.values()):
            raise AssertionError("contract_symbol_not_callable")

        _CHILD_STAGE = "node_mapping"
        node_mapping_keys = sorted(package.NODE_CLASS_MAPPINGS)
        if node_mapping_keys != EXPECTED_NODE_MAPPING_KEYS:
            raise AssertionError("node_mapping_keys_changed")
        if not all(
            value.__module__.startswith(f"{PACKAGE_NAME}.nodes.")
            for value in package.NODE_CLASS_MAPPINGS.values()
        ):
            raise AssertionError("node_mapping_origin_changed")

        _CHILD_STAGE = "route_capture"
        route_paths = sorted(
            {
                route.resource.canonical
                for route in host.app.router.routes()
                if route.resource.canonical in EXPECTED_ROUTE_PATHS
            }
        )
        if route_paths != EXPECTED_ROUTE_PATHS:
            raise AssertionError("representative_route_paths_missing")
        if len(host.on_prompt_handlers) != 1:
            raise AssertionError("prompt_handler_registration_changed")

        _CHILD_STAGE = "lifecycle"
        lifecycle = importlib.import_module(
            f"{PACKAGE_NAME}.services.bootstrap.lifecycle"
        )
        outcome = lifecycle.get_startup_outcome()
        if outcome.state.value != "ready" or not outcome.ready:
            raise AssertionError("startup_not_ready")
        if outcome.attempt != 0 or outcome.max_attempts != 0:
            raise AssertionError("startup_was_not_initial_attempt")
        retry_owner = getattr(registration, "_registration_retry_thread", None)
        if retry_owner is not None and retry_owner.is_alive():
            raise AssertionError("retry_owner_started")
        _CHILD_STAGE = "isolation_aliases"
        aliases = _top_level_api_aliases()
        if aliases:
            raise AssertionError("top_level_api_alias_created")
        _CHILD_STAGE = "isolation_threads"
        if _thread_fingerprint() != before_threads:
            raise AssertionError("background_thread_started")
        _CHILD_STAGE = "isolation_neutralization"
        if neutralized_calls != set(ALLOWED_NEUTRALIZATION_TARGETS):
            raise AssertionError("neutralization_call_set_changed")

        return {
            "contract_modules": contract_modules,
            "initial_attempt": True,
            "node_mapping_keys": node_mapping_keys,
            "package_name": PACKAGE_NAME,
            "registration_state": outcome.state.value,
            "retry_owner_alive": False,
            "route_paths": route_paths,
            "side_effects_started": [],
        }
    finally:
        restore_network()
        sys.path = original_sys_path


def _run_child(state_dir: Path) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).resolve()
    project_venv = (ROOT / ".venv").resolve()
    if project_venv not in executable.parents:
        raise AssertionError("child interpreter is not project-local .venv")
    return subprocess.run(
        [str(executable), str(Path(__file__).resolve()), "--child"],
        cwd=str(ROOT),
        env=_allowlisted_child_environment(state_dir),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _child_main() -> None:
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with (
            contextlib.redirect_stdout(captured_stdout),
            contextlib.redirect_stderr(captured_stderr),
        ):
            payload = _execute_real_package()
    except ModuleNotFoundError as exc:
        if "services.api" in str(exc):
            payload = {
                "error_type": "ModuleNotFoundError",
                "reason_code": "packaged_services_api_resolution_failed",
                "target_namespace": "services.api",
            }
        else:
            payload = {
                "error_type": type(exc).__name__,
                "reason_code": f"child_execution_failed_{_CHILD_STAGE}",
            }
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "reason_code": f"child_execution_failed_{_CHILD_STAGE}",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


class LoaderReadyRouteIntegrationTests(unittest.TestCase):
    def test_loader_ready_registration_contract(self) -> None:
        env_before = dict(os.environ)
        threads_before = _thread_fingerprint()
        aliases_before = _top_level_api_aliases()
        state_root = ROOT / ".tmp" / "r240-state"
        state_root.mkdir(parents=True, exist_ok=True)
        state_dir = Path(tempfile.mkdtemp(prefix="run-", dir=str(state_root)))
        try:
            result = _run_child(state_dir)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout.count("\n"), 1)
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload), EXPECTED_RESULT_KEYS)
            self.assertEqual(payload["package_name"], PACKAGE_NAME)
            self.assertEqual(payload["registration_state"], "ready")
            self.assertEqual(payload["contract_modules"], EXPECTED_CONTRACT_MODULES)
            self.assertEqual(payload["node_mapping_keys"], EXPECTED_NODE_MAPPING_KEYS)
            self.assertEqual(payload["route_paths"], EXPECTED_ROUTE_PATHS)
            self.assertEqual(payload["side_effects_started"], [])
            self.assertTrue(payload["initial_attempt"])
            self.assertFalse(payload["retry_owner_alive"])
        finally:
            if state_dir.exists():
                shutil.rmtree(state_dir)
            if state_root.exists() and not any(state_root.iterdir()):
                state_root.rmdir()
        self.assertEqual(dict(os.environ), env_before)
        self.assertEqual(_thread_fingerprint(), threads_before)
        self.assertEqual(_top_level_api_aliases(), aliases_before)


if __name__ == "__main__" and "--child" in sys.argv:
    raise SystemExit(_child_main())
elif __name__ == "__main__":
    unittest.main()
