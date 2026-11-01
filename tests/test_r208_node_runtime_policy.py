"""
R208 node runtime policy guard.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestR208NodeRuntimePolicy(unittest.TestCase):
    def test_package_engine_stays_aligned_with_openclaw_sop(self):
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        test_sop = (REPO_ROOT / "tests" / "TEST_SOP.md").read_text(encoding="utf-8")
        e2e_sop = (REPO_ROOT / "tests" / "E2E_TESTING_SOP.md").read_text(
            encoding="utf-8"
        )

        policy = "^20.19.0 || ^22.12.0 || >=24.0.0"
        self.assertEqual(package["engines"]["node"], policy)
        self.assertIn(policy, test_sop)
        self.assertIn("Node 20 line", test_sop)
        self.assertIn(policy, e2e_sop)
        self.assertNotEqual(package["engines"]["node"], ">=26.8.2 <27")

    def test_compatibility_matrix_documents_host_frontend_engine_boundary(self):
        matrix = (REPO_ROOT / "docs" / "release" / "compatibility_matrix.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## Residual Host-Contract Decisions", matrix)
        self.assertIn("`node >=26.8.2 <27`", matrix)
        self.assertIn("`^20.19.0 || ^22.12.0 || >=24.0.0`", matrix)
        self.assertIn("tests/TEST_SOP.md", matrix)
        self.assertIn("tests/E2E_TESTING_SOP.md", matrix)
        self.assertIn("does not build the host frontend workspace", matrix)
        self.assertNotIn("reference/", matrix)
        self.assertNotIn(".planning/", matrix)


if __name__ == "__main__":
    unittest.main()
