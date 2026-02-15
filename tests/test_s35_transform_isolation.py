"""
S35 Transform Isolation Tests.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.transform_common import (
    TransformLimits,
    TransformRegistry,
    TransformStatus,
    TrustedTransform,
)
from services.transform_runner import TransformProcessRunner


class TestS35TransformIsolation(unittest.TestCase):

    def setUp(self):
        self.registry = MagicMock(spec=TransformRegistry)
        self.limits = TransformLimits(
            timeout_sec=2.0, max_output_bytes=1024, max_transforms_per_request=1
        )
        self.runner = TransformProcessRunner(self.registry, self.limits)

    def test_process_execution_success(self):
        """Test successful execution in a subprocess."""
        # Create a dummy transform module on disk
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def transform(data):\n    return {'echo': data['input']}")
            module_path = f.name

        try:
            # Mock registry to return this module
            self.registry.get_transform.return_value = TrustedTransform(
                id="test_echo",
                label="Echo",
                module_path=module_path,
                sha256="dummy_hash",
            )
            self.registry.verify_integrity.return_value = True

            result = self.runner.execute_transform(
                "test_echo", {"input": "hello"}, trace_id="test_s35"
            )

            self.assertEqual(result.status, TransformStatus.SUCCESS.value)
            self.assertEqual(result.output, {"echo": "hello"})
            # Ensure it ran in a process? Hard to prove from here without spying on subprocess.
            # But the runner uses subprocess.run.
        finally:
            if os.path.exists(module_path):
                os.remove(module_path)

    def test_timeout_enforcement(self):
        """Test that slow transforms are killed."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                "import time\ndef transform(data):\n    time.sleep(5)\n    return {}"
            )
            module_path = f.name

        try:
            self.registry.get_transform.return_value = TrustedTransform(
                id="test_slow",
                label="Slow",
                module_path=module_path,
                sha256="dummy_hash",
            )
            self.registry.verify_integrity.return_value = True

            result = self.runner.execute_transform(
                "test_slow", {}, trace_id="test_timeout"
            )

            self.assertEqual(result.status, TransformStatus.TIMEOUT.value)
            self.assertIn("timeout exceeded", result.error)
        finally:
            if os.path.exists(module_path):
                os.remove(module_path)

    def test_capability_denial_network(self):
        """Test that network access is denied (by monkeypatch in worker)."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
import socket
def transform(data):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('example.com', 80))
        return {'status': 'connected'}
    except Exception as e:
        return {'error': str(e)}
"""
            )
            module_path = f.name

        try:
            self.registry.get_transform.return_value = TrustedTransform(
                id="test_net", label="Net", module_path=module_path, sha256="dummy_hash"
            )
            self.registry.verify_integrity.return_value = True

            result = self.runner.execute_transform(
                "test_net", {}, trace_id="test_net_deny"
            )

            # The worker monkeypatches socket to raise RuntimeError
            # So the transform returns {'error': ...}
            # Or crashes if it didn't catch it.
            # The script above catches it and returns it.

            output = result.output or {}
            self.assertEqual(result.status, TransformStatus.SUCCESS.value)
            self.assertIn("Network access denied", output.get("error", ""))
        finally:
            if os.path.exists(module_path):
                os.remove(module_path)


class TestGetTransformExecutorFailClosed(unittest.TestCase):
    """Test that get_transform_executor refuses to fall back to in-process executor."""

    def setUp(self):
        # Reset the global singleton before each test
        import services.constrained_transforms as ct

        ct._executor = None

    def tearDown(self):
        import services.constrained_transforms as ct

        ct._executor = None

    def test_raises_when_process_runner_unavailable(self):
        """get_transform_executor must raise RuntimeError, not fall back."""
        import importlib

        import services.constrained_transforms as ct

        # Force the module to re-evaluate the local import by removing the
        # transform_runner module from sys.modules temporarily.
        saved = sys.modules.pop("services.transform_runner", None)
        # Also remove any cached reference
        sys.modules["services.transform_runner"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises((RuntimeError, ImportError)) as ctx:
                ct.get_transform_executor()
            error_msg = str(ctx.exception).lower()
            self.assertTrue(
                "unavailable" in error_msg or "import" in error_msg,
                f"Expected 'unavailable' or 'import' in error: {ctx.exception}",
            )
        finally:
            # Restore
            if saved is not None:
                sys.modules["services.transform_runner"] = saved
            else:
                sys.modules.pop("services.transform_runner", None)

    def test_returns_process_runner_when_available(self):
        """get_transform_executor returns TransformProcessRunner normally."""
        from services.constrained_transforms import get_transform_executor

        executor = get_transform_executor()
        from services.transform_runner import TransformProcessRunner

        self.assertIsInstance(executor, TransformProcessRunner)


if __name__ == "__main__":
    unittest.main()
