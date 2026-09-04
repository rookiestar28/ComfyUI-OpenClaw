"""R219 exception-boundary phase-2 regression contracts."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api import config as config_api
from api import routes
from connector.platforms import feishu_webhook, slack_webhook
from services import route_bootstrap
from services.runtime_profile import RuntimeProfile

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tests" / "exception_boundary_policy.json"


class TestPolicyV3(unittest.TestCase):
    def test_repository_policy_v3_is_complete_and_has_no_open_followups(self):
        from scripts.verify_exception_boundary_policy import (
            load_policy,
            validate_exception_boundary_policy,
        )

        policy = load_policy(POLICY_PATH)
        self.assertEqual(policy["version"], 3)
        self.assertEqual(
            set(policy["selected_modules"]),
            {
                "api/route_handlers.py",
                "api/route_orchestration.py",
                "connector/router_admin_handlers.py",
                "connector/router_dispatch.py",
                "connector/router_execution_handlers.py",
                "services/bootstrap/registration.py",
                "api/config_projection_handlers.py",
                "api/config_llm_handlers.py",
                "connector/platforms/slack_installation_handlers.py",
                "connector/platforms/slack_ingress_handlers.py",
                "connector/platforms/feishu_ingress_handlers.py",
            },
        )
        for module in policy["selected_modules"].values():
            self.assertIn(module["coverage"], {"all_broad_catches", "selected_scopes"})
            for entry in module["broad_catches"]:
                self.assertTrue(entry["regression_owner"])
                self.assertRegex(entry["review_after"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertNotEqual(
                    entry["classification"], "needs_follow_up_test_coverage"
                )
        self.assertEqual(validate_exception_boundary_policy(ROOT, policy), [])

    def test_policy_rejects_expiry_missing_owner_and_new_selected_scope_catch(self):
        from scripts.verify_exception_boundary_policy import (
            validate_exception_boundary_policy,
        )

        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        expired = copy.deepcopy(policy)
        first = expired["selected_modules"]["api/route_handlers.py"]["broad_catches"][0]
        first["review_after"] = "2020-01-01"
        self.assertTrue(
            any(
                "expired" in item
                for item in validate_exception_boundary_policy(ROOT, expired)
            )
        )

        missing_owner = copy.deepcopy(policy)
        del missing_owner["selected_modules"]["api/route_handlers.py"]["broad_catches"][
            0
        ]["regression_owner"]
        self.assertTrue(
            any(
                "regression_owner" in item
                for item in validate_exception_boundary_policy(ROOT, missing_owner)
            )
        )

        missing_owner_file = copy.deepcopy(policy)
        missing_owner_file["selected_modules"]["api/route_handlers.py"][
            "broad_catches"
        ][0]["regression_owner"] = "tests/does_not_exist.py"
        self.assertTrue(
            any(
                "does not exist" in item
                for item in validate_exception_boundary_policy(ROOT, missing_owner_file)
            )
        )

        stale_scope = copy.deepcopy(policy)
        stale_scope["selected_modules"]["api/config_projection_handlers.py"][
            "selected_scopes"
        ].append("removed_scope")
        self.assertTrue(
            any(
                "selected scope has no broad catch" in item
                for item in validate_exception_boundary_policy(ROOT, stale_scope)
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "api").mkdir()
            (repo / "selected.py").write_text(
                "def governed():\n    try:\n        return 1\n    except Exception:\n        return 0\n",
                encoding="utf-8",
            )
            selected_policy = {
                "version": 3,
                "selected_modules": {
                    "selected.py": {
                        "coverage": "selected_scopes",
                        "broad_catches": [],
                        "selected_scopes": ["governed"],
                    }
                },
                "pass_only_contract": {
                    "roots": ["api"],
                    "grandfathered": [],
                },
                "stdout_contract": {"roots": ["api"], "allowed": []},
            }
            failures = validate_exception_boundary_policy(repo, selected_policy)
        self.assertTrue(any("undocumented broad catch" in item for item in failures))

    def test_policy_rejects_unknown_schema_keys_and_unsafe_module_paths(self):
        from scripts.verify_exception_boundary_policy import (
            validate_exception_boundary_policy,
        )

        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        unknown_entry = copy.deepcopy(policy)
        unknown_entry["selected_modules"]["api/route_handlers.py"]["broad_catches"][0][
            "unexpected"
        ] = True
        self.assertTrue(
            any(
                "entry keys" in item
                for item in validate_exception_boundary_policy(ROOT, unknown_entry)
            )
        )

        unsafe_path = copy.deepcopy(policy)
        unsafe_path["selected_modules"]["../outside.py"] = unsafe_path[
            "selected_modules"
        ].pop("api/route_handlers.py")
        self.assertTrue(
            any(
                "unsafe selected module path" in item
                for item in validate_exception_boundary_policy(ROOT, unsafe_path)
            )
        )


class TestMaeProfileBoundary(unittest.TestCase):
    def setUp(self):
        from services.effective_security_posture import (
            reset_effective_security_posture_for_tests,
        )

        reset_effective_security_posture_for_tests()

    def tearDown(self):
        from services.effective_security_posture import (
            reset_effective_security_posture_for_tests,
        )

        reset_effective_security_posture_for_tests()

    def test_explicit_deployment_profile_precedes_runtime_probe(self):
        with (
            patch.dict(
                os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}, clear=True
            ),
            patch(
                "services.runtime_profile.get_runtime_profile",
                side_effect=RuntimeError("must-not-run"),
            ) as probe,
        ):
            self.assertEqual(routes._resolve_mae_profile(), "public")
        probe.assert_not_called()

    def test_missing_profile_import_falls_back_but_unexpected_failure_propagates(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "services.runtime_profile.get_runtime_profile",
                return_value=RuntimeProfile.HARDENED,
            ):
                self.assertEqual(routes._resolve_mae_profile(), "hardened")

            with patch(
                "services.runtime_profile.get_runtime_profile",
                side_effect=ImportError("optional-profile-import"),
            ):
                self.assertEqual(routes._resolve_mae_profile(), "local")

            with patch(
                "services.runtime_profile.get_runtime_profile",
                side_effect=RuntimeError("profile-resolution-failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "profile-resolution-failed"):
                    routes._resolve_mae_profile()


class TestOptionalStartupRegistrars(unittest.TestCase):
    def test_shutdown_and_plugins_are_independent_and_logs_are_content_free(self):
        shutdown = MagicMock(side_effect=RuntimeError("sensitive-shutdown-detail"))
        plugins = MagicMock(side_effect=ValueError("sensitive-plugin-detail"))
        logger = MagicMock()
        with (
            patch.object(
                route_bootstrap,
                "_load_plugin_shutdown_registrars",
                return_value=(shutdown, plugins),
            ),
            patch("services.route_bootstrap.logging.getLogger", return_value=logger),
        ):
            route_bootstrap._register_plugins_and_shutdown_hooks()

        shutdown.assert_called_once_with()
        plugins.assert_called_once_with()
        rendered_calls = repr(logger.method_calls)
        self.assertIn("RuntimeError", rendered_calls)
        self.assertIn("ValueError", rendered_calls)
        self.assertNotIn("sensitive-shutdown-detail", rendered_calls)
        self.assertNotIn("sensitive-plugin-detail", rendered_calls)

    def test_baseexception_from_optional_registrar_propagates(self):
        shutdown = MagicMock(side_effect=KeyboardInterrupt("cancel"))
        plugins = MagicMock()
        with patch.object(
            route_bootstrap,
            "_load_plugin_shutdown_registrars",
            return_value=(shutdown, plugins),
        ):
            with self.assertRaises(KeyboardInterrupt):
                route_bootstrap._register_plugins_and_shutdown_hooks()
        plugins.assert_not_called()

    def test_registrar_import_failure_is_content_free(self):
        logger = MagicMock()
        with (
            patch.object(
                route_bootstrap,
                "_load_plugin_shutdown_registrars",
                side_effect=ImportError("sensitive-import-detail"),
            ),
            patch("services.route_bootstrap.logging.getLogger", return_value=logger),
        ):
            route_bootstrap._register_plugins_and_shutdown_hooks()
        rendered_calls = repr(logger.method_calls)
        self.assertIn("ImportError", rendered_calls)
        self.assertNotIn("sensitive-import-detail", rendered_calls)


class TestExpectedParserBoundaries(unittest.TestCase):
    def test_slack_and_feishu_json_helpers_narrow_expected_parse_errors(self):
        self.assertEqual(slack_webhook._json_loads_safe("{"), {})
        self.assertEqual(feishu_webhook._json_loads_safe("{"), {})

        for module, helper in (
            (slack_webhook, slack_webhook._json_loads_safe),
            (feishu_webhook, feishu_webhook._json_loads_safe),
        ):
            with self.subTest(module=module.__name__):
                with patch.object(
                    module.json, "loads", side_effect=RuntimeError("parser-defect")
                ):
                    with self.assertRaisesRegex(RuntimeError, "parser-defect"):
                        helper("{}")
                with patch.object(
                    module.json, "loads", side_effect=KeyboardInterrupt("cancel")
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        helper("{}")

    def test_config_numeric_parsers_have_no_broad_catch(self):
        from scripts.verify_exception_boundary_policy import iter_broad_catches

        catches = list(iter_broad_catches(ROOT / "api" / "config_llm_handlers.py"))
        llm_test = [catch for catch in catches if catch.scope == "llm_test_response"]
        self.assertEqual(len(llm_test), 2)


class TestFixedConfigErrorTranslation(unittest.IsolatedAsyncioTestCase):
    async def test_config_read_failure_uses_fixed_code_without_echo(self):
        request = MagicMock()
        web = MagicMock()
        web.json_response.side_effect = lambda body, status=200: (body, status)
        logger = MagicMock()
        tenant = SimpleNamespace(tenant_id="default")
        with (
            patch.object(config_api, "web", web),
            patch.object(
                config_api, "require_observability_access", return_value=(True, None)
            ),
            patch.object(config_api, "check_rate_limit", return_value=True),
            patch.object(
                config_api, "resolve_token_info", return_value=SimpleNamespace()
            ),
            patch.object(
                config_api, "request_tenant_scope", return_value=nullcontext(tenant)
            ),
            patch.object(
                config_api,
                "get_effective_config",
                side_effect=RuntimeError("sensitive-config-detail"),
            ),
            patch.object(config_api, "logger", logger),
        ):
            body, status = await config_api.config_get_handler(request)

        self.assertEqual(status, 500)
        self.assertEqual(body, {"ok": False, "error": "config_read_failed"})
        self.assertNotIn("sensitive-config-detail", repr(logger.method_calls))

    async def test_llm_test_failure_uses_fixed_response_audit_and_log(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={})
        web = MagicMock()
        web.json_response.side_effect = lambda body, status=200: (body, status)
        logger = MagicMock()
        audit = MagicMock()
        tenant = SimpleNamespace(tenant_id="default")
        with (
            patch.object(config_api, "web", web),
            patch.object(config_api, "get_admin_token", return_value="configured"),
            patch.object(
                config_api, "require_same_origin_if_no_token", return_value=None
            ),
            patch.object(config_api, "check_rate_limit", return_value=True),
            patch.object(
                config_api, "resolve_token_info", return_value=SimpleNamespace()
            ),
            patch.object(config_api, "require_admin_token", return_value=(True, None)),
            patch.object(
                config_api, "request_tenant_scope", return_value=nullcontext(tenant)
            ),
            patch.object(
                config_api,
                "LLMClient",
                side_effect=RuntimeError("sensitive-llm-detail"),
            ),
            patch.object(config_api, "emit_audit_event", audit),
            patch.object(config_api, "logger", logger),
        ):
            body, status = await config_api.llm_test_handler(request)

        self.assertEqual(status, 500)
        self.assertEqual(body, {"ok": False, "error": "llm_test_failed"})
        combined = repr(logger.method_calls) + repr(audit.call_args_list)
        self.assertIn("llm_test_failed", combined)
        self.assertNotIn("sensitive-llm-detail", combined)


class TestSelectedConnectorLogging(unittest.IsolatedAsyncioTestCase):
    async def test_slack_event_failure_logs_type_without_exception_content(self):
        from tests.test_r124_slack_ingress_contract import (
            _make_event_payload,
            _make_server,
        )

        server = _make_server(require_mention=False)
        server.router.handle.side_effect = RuntimeError("sensitive-slack-detail")
        logger = MagicMock()
        with patch.object(slack_webhook, "logger", logger):
            await server.process_event_payload(_make_event_payload())

        rendered_calls = repr(logger.method_calls)
        self.assertIn("RuntimeError", rendered_calls)
        self.assertNotIn("sensitive-slack-detail", rendered_calls)


if __name__ == "__main__":
    unittest.main()
