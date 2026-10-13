"""
R98 Drift Detection Test.

Ensures that:
1. Every registered route in the `aiohttp` application has explicit security annotations.
2. No "shadow endpoints" exist without a known auth/risk classification.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from aiohttp import web

from api.routes import register_routes
from services.endpoint_manifest import (
    AuthTier,
    RiskTier,
    generate_manifest,
    get_metadata,
)


class TestEndpointDrift(unittest.TestCase):

    def setUp(self):
        self.app = web.Application()
        # Mock server object to match what register_routes expects
        self.server = MagicMock()
        self.server.app = self.app
        # Mock routes attribute for standard ComfyUI registration
        self.server.routes = MagicMock()
        self.server.routes.get = MagicMock(return_value=lambda x: x)
        self.server.routes.post = MagicMock(return_value=lambda x: x)
        self.server.routes.put = MagicMock(return_value=lambda x: x)
        self.server.routes.delete = MagicMock(return_value=lambda x: x)

    def tearDown(self):
        # Clean up sys.modules hacks if any remain (none in this version)
        pass

    def test_all_routes_have_metadata(self):
        """
        CRITICAL SECURITY CATCH:
        Iterate over ALL registered routes and fail if any lack @endpoint_metadata.
        Verification includes ensuring that optional modules (Bridge, Packs) are
        actually enabled and registered during the test to prove they are guarded.
        """
        # 1. Setup Environment
        # We need config and services.modules to allow loading optional components
        mock_config = MagicMock()
        mock_config.DATA_DIR = "/tmp"

        # Patch essential services to force-enable modules
        with (
            patch.dict(
                sys.modules,
                {
                    "config": mock_config,
                    # We must NOT mock api.bridge or api.packs here,
                    # we want the REAL modules to load and register REAL routes.
                },
            ),
            patch("services.modules.is_module_enabled", return_value=True),
        ):

            # Re-import api.routes to pick up the patched environment if needed,
            # though register_routes is what matters.
            pass

            # 2. Trigger route registration
            register_routes(self.server)

        # 3. Generate manifest from the actual aiohttp app
        manifest = generate_manifest(self.app)

        # 4. Verify Coverage (Prevent "Vacuous Truth" pass)
        # Ensure that we actually registered the routes we expect to guard
        methods_paths = [f"{m['method']} {m['path']}" for m in manifest]

        # Check Bridge
        has_bridge = any("bridge" in p for p in methods_paths)
        if not has_bridge:
            self.fail(
                "Drift Test Configuration Error: Bridge routes were not registered! Test is not covering all endpoints."
            )

        # Check Packs
        has_packs = any("packs" in p for p in methods_paths)
        if not has_packs:
            self.fail(
                "Drift Test Configuration Error: Packs routes were not registered! Test is not covering all endpoints."
            )

        # 5. Scan for unclassified routes
        unclassified = []
        for m in manifest:
            # Skip verify_handshake or other non-handler routes if any (aiohttp adds HEAD/OPTIONS sometimes)
            if m["method"] not in ["GET", "POST", "PUT", "DELETE"]:
                continue

            if not m["classified"]:
                unclassified.append(m)

        if unclassified:
            # Pretty print failure
            fail_msg = f"Found {len(unclassified)} unclassified routes (R98 Drift):\n"
            for u in unclassified:
                fail_msg += f"- {u['method']} {u['path']} -> {u['handler']}\n"

            self.fail(fail_msg)

    def test_bridge_routes_have_metadata(self):
        """Verify bridge routes specifically have metadata."""
        # This implicitly tests the AuthTier.BRIDGE existence and importability
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()

        meta = get_metadata(handlers.submit_handler)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.auth_tier.value, "bridge")

        meta = get_metadata(handlers.health_handler)
        self.assertIsNotNone(meta)
        # Health is PUBLIC (internally guarded)
        self.assertEqual(meta.auth_tier.value, "public")


if __name__ == "__main__":
    unittest.main()
