"""R248 regressions for deterministic repository import resolution."""

from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.errors import APIError, ErrorCode
from services import queue_submit
from services.import_fallback import import_module_dual

ROOT = Path(__file__).resolve().parents[1]


class ImportResolutionContractTests(unittest.TestCase):
    def test_selected_package_dependency_failure_preserves_original_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            files = {
                "r248_package/__init__.py": "",
                "r248_package/sub/__init__.py": "",
                "r248_package/preferred.py": "import r248_missing_internal_dependency\n",
                "r248_absolute.py": "VALUE = 'wrong-namespace'\n",
            }
            for relative, content in files.items():
                path = temp_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            sys.path.insert(0, str(temp_root))
            self.addCleanup(
                lambda: (
                    sys.path.remove(str(temp_root))
                    if str(temp_root) in sys.path
                    else None
                )
            )
            touched = (
                "r248_package.preferred",
                "r248_package.sub",
                "r248_package",
                "r248_absolute",
            )
            self.addCleanup(lambda: [sys.modules.pop(name, None) for name in touched])

            with self.assertRaises(ModuleNotFoundError) as captured:
                import_module_dual(
                    "r248_package.sub",
                    "..preferred",
                    "r248_absolute",
                )

            self.assertEqual(
                captured.exception.name, "r248_missing_internal_dependency"
            )
            self.assertNotIn("r248_absolute", sys.modules)

    def test_top_level_queue_uses_canonical_api_error_symbols(self):
        self.assertIs(queue_submit.APIError, APIError)
        self.assertIs(queue_submit.ErrorCode, ErrorCode)
        source = inspect.getsource(queue_submit)
        self.assertNotIn("class APIError", source)
        self.assertNotIn("class ErrorCode", source)

    def test_packaged_queue_uses_its_own_canonical_api_error_symbols(self):
        child = textwrap.dedent(
            f"""
            import importlib
            import json
            import os
            import sys
            import types

            root = os.path.normcase(os.path.abspath({str(ROOT)!r}))
            sys.path = [
                entry for entry in sys.path
                if os.path.normcase(os.path.abspath(entry or os.curdir)) != root
            ]
            package_name = "r248_synthetic_package"
            package = types.ModuleType(package_name)
            package.__package__ = package_name
            package.__path__ = [{str(ROOT)!r}]
            sys.modules[package_name] = package

            queue = importlib.import_module(package_name + ".services.queue_submit")
            errors = importlib.import_module(package_name + ".api.errors")
            print(json.dumps({{
                "api_error_identity": queue.APIError is errors.APIError,
                "error_code_identity": queue.ErrorCode is errors.ErrorCode,
                "api_error_module": queue.APIError.__module__,
                "top_level_api_loaded": any(
                    name == "api" or name.startswith("api.") for name in sys.modules
                ),
            }}, sort_keys=True))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["api_error_identity"])
        self.assertTrue(payload["error_code_identity"])
        self.assertEqual(
            payload["api_error_module"], "r248_synthetic_package.api.errors"
        )
        self.assertFalse(payload["top_level_api_loaded"])

    def test_bridge_selected_import_failure_is_not_silently_omitted(self):
        from api import route_orchestration

        marker = ModuleNotFoundError(
            "internal bridge dependency is missing",
            name="r248_bridge_internal_dependency",
        )
        with patch.object(
            route_orchestration,
            "import_attrs_dual",
            side_effect=marker,
            create=True,
        ):
            with self.assertRaises(ModuleNotFoundError) as captured:
                route_orchestration._register_bridge(SimpleNamespace())

        self.assertIs(captured.exception, marker)

    def test_packs_selected_import_failure_is_not_silently_omitted(self):
        from api import packs, route_orchestration

        marker = ModuleNotFoundError(
            "internal packs dependency is missing",
            name="r248_packs_internal_dependency",
        )
        deps = SimpleNamespace(
            register_route_family=lambda *_args: None,
            register_dual_route=lambda *_args: None,
            build_pack_route_specs=lambda *_args: (),
        )
        with (
            patch.object(
                route_orchestration,
                "import_attrs_dual",
                side_effect=marker,
                create=True,
            ),
            patch.object(packs, "PacksHandlers", return_value=object()),
        ):
            with self.assertRaises(ModuleNotFoundError) as captured:
                route_orchestration._register_packs(
                    SimpleNamespace(), ("/openclaw", "/moltbot"), deps
                )

        self.assertIs(captured.exception, marker)


if __name__ == "__main__":
    unittest.main()
