import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services import route_bootstrap


class TestExceptionBoundaryGovernance(unittest.TestCase):
    def setUp(self):
        route_bootstrap.reset_route_bootstrap_for_tests()

    def tearDown(self):
        # IMPORTANT: registration installs process-static posture; keep test order inert.
        route_bootstrap.reset_route_bootstrap_for_tests()

    def test_register_routes_once_reraises_initial_registration_failure(self):
        route_bootstrap._routes_registered = False
        server = SimpleNamespace(app=object())
        prompt_server = SimpleNamespace(instance=server)

        with (
            patch("services.route_bootstrap._register_plugins_and_shutdown_hooks"),
            patch("services.route_bootstrap._initialize_registries_and_security_gate"),
            patch(
                "services.route_bootstrap._do_full_registration",
                side_effect=RuntimeError("route-registration-broken"),
            ),
            patch(
                "services.route_bootstrap.logging.getLogger", return_value=MagicMock()
            ),
            patch.dict(
                sys.modules,
                {"server": SimpleNamespace(PromptServer=prompt_server)},
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                route_bootstrap.register_routes_once()

        self.assertIn("route-registration-broken", str(ctx.exception))
        self.assertFalse(route_bootstrap._routes_registered)

    def test_selected_module_broad_catches_match_exception_policy(self):
        from scripts.verify_exception_boundary_policy import (
            load_policy,
            validate_exception_boundary_policy,
        )

        repo_root = Path(__file__).resolve().parents[1]
        failures = validate_exception_boundary_policy(
            repo_root,
            load_policy(repo_root / "tests" / "exception_boundary_policy.json"),
        )

        self.assertEqual(failures, [])

    def test_connector_trust_parsing_no_longer_uses_broad_exception(self):
        from scripts.verify_exception_boundary_policy import iter_broad_catches

        repo_root = Path(__file__).resolve().parents[1]
        catches = list(iter_broad_catches(repo_root / "connector" / "router.py"))
        broad_scopes = {
            catch.scope
            for catch in catches
            if catch.scope == "CommandRouter._is_trusted"
        }

        self.assertEqual(broad_scopes, set())


if __name__ == "__main__":
    unittest.main()
