"""R249 exception, diagnostics, privacy, and stdout regression contracts."""

from __future__ import annotations

import asyncio
import builtins
import copy
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api import route_handlers, route_orchestration
from connector import router_dispatch, router_execution_handlers
from connector.config import ConnectorConfig
from connector.contract import CommandRequest
from connector.router import CommandRouter

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests" / "exception_boundary_policy.json"
PRIVATE_SENTINEL = "token=PRIVATE_R249 C:/private/prompt.txt raw-request-body"


class TestRuntimeLoggingContracts(unittest.TestCase):
    def test_missing_handler_uses_logger_without_stdio(self):
        logger = MagicMock()
        stream = io.StringIO()
        server = SimpleNamespace(routes=SimpleNamespace())

        with (
            patch.object(route_orchestration, "logger", logger, create=True),
            redirect_stdout(stream),
            redirect_stderr(stream),
        ):
            route_orchestration.register_dual_route(
                server, "GET", "/openclaw/missing", None
            )

        self.assertEqual(stream.getvalue(), "")
        rendered = repr(logger.method_calls)
        self.assertIn("route.handler_missing", rendered)
        self.assertIn("GET", rendered)

    def test_legacy_metric_failure_is_content_free_and_routing_continues(self):
        captured = {}

        class Routes:
            @staticmethod
            def get(path):
                def decorate(handler):
                    captured[path] = handler
                    return handler

                return decorate

        async def handler(_request):
            return SimpleNamespace(headers={})

        metrics = SimpleNamespace(
            inc=MagicMock(side_effect=RuntimeError(PRIVATE_SENTINEL))
        )
        logger = MagicMock()
        stream = io.StringIO()
        server = SimpleNamespace(routes=Routes())

        with patch.object(route_orchestration, "logger", logger, create=True):
            route_orchestration.register_dual_route(
                server,
                "GET",
                "/moltbot/test",
                handler,
                metrics=metrics,
            )
            with redirect_stdout(stream), redirect_stderr(stream):
                response = asyncio.run(
                    captured["/moltbot/test"](SimpleNamespace(path="/moltbot/test"))
                )

        self.assertEqual(response.headers, {})
        self.assertEqual(stream.getvalue(), "")
        rendered = repr(logger.method_calls)
        self.assertIn("route.legacy_metric_failed", rendered)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)

    def test_direct_alias_failure_logs_type_without_exception_or_stdio(self):
        class Routes:
            @staticmethod
            def get(_path):
                return lambda handler: handler

        async def handler(_request):
            return None

        logger = MagicMock()
        stream = io.StringIO()
        server = SimpleNamespace(
            routes=Routes(),
            app=SimpleNamespace(
                router=SimpleNamespace(
                    add_route=MagicMock(side_effect=ValueError(PRIVATE_SENTINEL))
                )
            ),
        )

        with (
            patch.object(route_orchestration, "logger", logger, create=True),
            redirect_stdout(stream),
            redirect_stderr(stream),
        ):
            route_orchestration.register_dual_route(
                server, "GET", "/openclaw/test", handler
            )

        self.assertEqual(stream.getvalue(), "")
        rendered = repr(logger.method_calls)
        self.assertIn("route.direct_alias_failed", rendered)
        self.assertIn("ValueError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)

    def test_local_mae_violation_logs_only_count_and_profile(self):
        logger = MagicMock()
        stream = io.StringIO()
        with (
            patch.object(route_orchestration, "logger", logger, create=True),
            patch(
                "services.endpoint_manifest.generate_manifest",
                return_value=[{"path": "/openclaw/config"}],
            ),
            patch(
                "services.endpoint_manifest.validate_mae_posture",
                return_value=(False, [PRIVATE_SENTINEL]),
            ),
            redirect_stdout(stream),
            redirect_stderr(stream),
        ):
            route_orchestration.run_mae_startup_gate(
                SimpleNamespace(app=object()), lambda: "local"
            )

        self.assertEqual(stream.getvalue(), "")
        rendered = repr(logger.method_calls)
        self.assertIn("startup.mae_posture_degraded", rendered)
        self.assertIn("local", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)

    def test_mae_import_failure_keeps_optional_continuation_content_free(self):
        logger = MagicMock()
        stream = io.StringIO()
        resolve_profile = MagicMock(return_value="public")
        original_import = builtins.__import__

        def fail_endpoint_manifest_import(
            name, globals=None, locals=None, fromlist=(), level=0
        ):
            if name == "services.endpoint_manifest":
                raise ImportError(PRIVATE_SENTINEL)
            return original_import(name, globals, locals, fromlist, level)

        with (
            patch.object(route_orchestration, "logger", logger, create=True),
            patch("builtins.__import__", side_effect=fail_endpoint_manifest_import),
            redirect_stdout(stream),
            redirect_stderr(stream),
        ):
            route_orchestration.run_mae_startup_gate(
                SimpleNamespace(app=object()), resolve_profile
            )

        self.assertEqual(stream.getvalue(), "")
        resolve_profile.assert_not_called()
        rendered = repr(logger.method_calls)
        self.assertIn("startup.mae_unavailable", rendered)
        self.assertIn("ImportError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)


class TestHealthDiagnostics(unittest.TestCase):
    def test_silent_health_degradations_emit_content_free_diagnostics(self):
        web = SimpleNamespace(json_response=lambda body, **_kwargs: body)
        deps = SimpleNamespace(
            web=web,
            pack_start_time=time.time(),
            pack_name="openclaw",
            pack_version="test",
            metrics=SimpleNamespace(
                get_snapshot=lambda: {
                    "errors_captured": 0,
                    "logs_processed": 0,
                }
            ),
            get_executor_diagnostics=lambda: {},
            check_dependency=lambda _name: True,
        )
        client = MagicMock()
        client.get_provider_summary.return_value = {
            "provider": "openai",
            "model": "test",
            "key_configured": False,
        }
        logger = MagicMock()

        with (
            patch("services.llm_client.LLMClient", return_value=client),
            patch("services.providers.keys.requires_api_key", return_value=True),
            patch(
                "services.startup_lifecycle.get_startup_diagnostics",
                return_value={"state": "ready"},
            ),
            patch(
                "services.job_events.get_job_event_store",
                side_effect=RuntimeError(PRIVATE_SENTINEL),
            ),
            patch(
                "services.capabilities._get_control_plane_info",
                side_effect=ValueError(PRIVATE_SENTINEL),
            ),
            patch.object(route_handlers, "logger", logger, create=True),
        ):
            payload = asyncio.run(
                route_handlers.health_response(SimpleNamespace(), deps)
            )

        self.assertEqual(payload["stats"]["observability"], {})
        self.assertEqual(payload["control_plane"], {})
        self.assertEqual(payload["runtime_profile"], "minimal")
        rendered = repr(logger.method_calls)
        self.assertIn("health.job_stats_degraded", rendered)
        self.assertIn("health.control_plane_degraded", rendered)
        # Top-level dependency-light mode cannot resolve the package-relative job
        # probe; its existing fallback remains intentionally in scope as degradation.
        self.assertIn("ImportError", rendered)
        self.assertIn("ValueError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)


class TestConnectorPrivacyContracts(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request(text: str = "/help") -> CommandRequest:
        return CommandRequest(
            platform="telegram",
            channel_id="channel",
            sender_id="user",
            username="name",
            message_id="message",
            text=text,
            timestamp=0,
        )

    async def test_dispatch_translates_to_fixed_error_without_raw_detail(self):
        router = CommandRouter(ConnectorConfig(), MagicMock())
        router._handle_help = AsyncMock(side_effect=RuntimeError(PRIVATE_SENTINEL))
        logger = MagicMock()

        with patch.object(router_dispatch, "logger", logger):
            response = await router.handle(self._request())

        self.assertEqual(response.text, "[Internal Error]")
        rendered = repr(logger.method_calls)
        self.assertIn("connector.command_failed", rendered)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)
        logger.exception.assert_not_called()

    async def test_template_metadata_debug_log_uses_error_type_only(self):
        config = ConnectorConfig()
        config.debug = True
        client = MagicMock()
        client.get_templates = AsyncMock(side_effect=ValueError(PRIVATE_SENTINEL))
        router = CommandRouter(config, client)
        logger = MagicMock()

        with patch.object(router_execution_handlers, "logger", logger):
            result = await router._get_template_meta("template")

        self.assertEqual(result, {})
        rendered = repr(logger.method_calls)
        self.assertIn("connector.template_metadata_failed", rendered)
        self.assertIn("ValueError", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)


class TestExceptionDiagnosticsPolicy(unittest.TestCase):
    def test_repository_policy_v3_passes_exact_ratchets(self):
        from scripts.verify_exception_boundary_policy import (
            load_policy,
            validate_exception_boundary_policy,
        )

        policy = load_policy(POLICY_PATH)
        self.assertEqual(policy["version"], 3)
        self.assertEqual(
            set(policy),
            {
                "version",
                "selected_modules",
                "pass_only_contract",
                "stdout_contract",
            },
        )
        self.assertEqual(
            sum(
                entry["expected_count"]
                for entry in policy["pass_only_contract"]["grandfathered"]
            ),
            37,
        )
        self.assertEqual(
            sum(
                entry["expected_count"]
                for entry in policy["stdout_contract"]["allowed"]
            ),
            7,
        )
        self.assertEqual(validate_exception_boundary_policy(ROOT, policy), [])

    def _minimal_policy(self) -> dict:
        return {
            "version": 3,
            "selected_modules": {
                "selected.py": {
                    "coverage": "all_broad_catches",
                    "broad_catches": [],
                }
            },
            "pass_only_contract": {
                "roots": ["api"],
                "grandfathered": [],
            },
            "stdout_contract": {
                "roots": ["api"],
                "allowed": [],
            },
        }

    @staticmethod
    def _entry(path: str, scope: str, count: int = 1) -> dict:
        return {
            "path": path,
            "scope": scope,
            "expected_count": count,
            "reason": "fixture contract",
            "review_after": "2027-01-11",
        }

    def test_policy_rejects_new_and_stale_pass_only_boundaries(self):
        from scripts.verify_exception_boundary_policy import (
            validate_exception_boundary_policy,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "api").mkdir()
            (repo / "tests").mkdir()
            (repo / "selected.py").write_text("value = 1\n", encoding="utf-8")
            source = repo / "api" / "runtime.py"
            source.write_text(
                "def degrade():\n    try:\n        return 1\n    except Exception:\n        pass\n",
                encoding="utf-8",
            )
            policy = self._minimal_policy()
            failures = validate_exception_boundary_policy(repo, policy)
            self.assertTrue(any("unowned pass-only" in item for item in failures))

            policy["pass_only_contract"]["grandfathered"].append(
                self._entry("api/runtime.py", "degrade")
            )
            self.assertEqual(validate_exception_boundary_policy(repo, policy), [])

            source.write_text("def degrade():\n    return 1\n", encoding="utf-8")
            failures = validate_exception_boundary_policy(repo, policy)
            self.assertTrue(any("stale pass-only" in item for item in failures))

    def test_policy_parses_bom_and_rejects_unowned_runtime_print(self):
        from scripts.verify_exception_boundary_policy import (
            validate_exception_boundary_policy,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "api").mkdir()
            (repo / "selected.py").write_text("value = 1\n", encoding="utf-8")
            source = repo / "api" / "runtime.py"
            source.write_text(
                "\ufeffdef degrade():\n    try:\n        return 1\n    except Exception:\n        pass\n",
                encoding="utf-8",
            )
            policy = self._minimal_policy()
            policy["pass_only_contract"]["grandfathered"].append(
                self._entry("api/runtime.py", "degrade")
            )
            self.assertEqual(validate_exception_boundary_policy(repo, policy), [])

            source.write_text(
                "\ufeffdef degrade():\n    try:\n        return 1\n    except Exception:\n        pass\n\n"
                "def runtime():\n    print('not-owned')\n",
                encoding="utf-8",
            )
            failures = validate_exception_boundary_policy(repo, policy)
            self.assertTrue(any("unowned runtime print" in item for item in failures))

    def test_policy_accepts_exact_owned_stdout_and_rejects_count_drift(self):
        from scripts.verify_exception_boundary_policy import (
            validate_exception_boundary_policy,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "api").mkdir()
            (repo / "selected.py").write_text("value = 1\n", encoding="utf-8")
            source = repo / "api" / "cli.py"
            source.write_text("def main():\n    print('owned')\n", encoding="utf-8")
            policy = self._minimal_policy()
            policy["stdout_contract"]["allowed"].append(
                self._entry("api/cli.py", "main")
            )
            self.assertEqual(validate_exception_boundary_policy(repo, policy), [])

            drift = copy.deepcopy(policy)
            drift["stdout_contract"]["allowed"][0]["expected_count"] = 2
            failures = validate_exception_boundary_policy(repo, drift)
            self.assertTrue(any("runtime print" in item for item in failures))

    def test_type_only_import_uses_type_checking_without_runtime_dependency(self):
        source = (ROOT / "connector" / "router.py").read_text(encoding="utf-8")
        self.assertIn("TYPE_CHECKING", source)
        self.assertNotIn("if False:  # Type hinting only", source)


if __name__ == "__main__":
    unittest.main()
