from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PUBLIC_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "release" / "compatibility_matrix.md",
    REPO_ROOT / "docs" / "release" / "support_policy.md",
    REPO_ROOT / "docs" / "frontend_ux_walkthrough.md",
    REPO_ROOT / "docs" / "asset_api_adoption_decision.md",
    REPO_ROOT / "web" / "openclaw_host_surface.js",
)
STALE_MARKERS = (
    "1377a2f7",
    "ceb5ae1eba",
    "standalone frontend `1.48.1`",
    "9cf91339",
    "v0.29.0-12-g9cf91339",
    "4b3866b838",
    "1.49.1",
)
CURRENT_MARKERS = (
    "3aba3dae",
    "569e65b30f",
    "85e28b7a",
    "installation_specific",
)
FORBIDDEN_INTERNAL_MARKERS = (
    "." + "planning/",
    "reference/" + "docs/",
    "R" + "234",
)


class TestHostAnchorAlignment(unittest.TestCase):
    def test_active_public_surfaces_remove_stale_host_anchors(self):
        stale_hits: dict[str, list[str]] = {}
        for path in ACTIVE_PUBLIC_PATHS:
            text = path.read_text(encoding="utf-8")
            hits = [marker for marker in STALE_MARKERS if marker in text]
            if hits:
                stale_hits[str(path.relative_to(REPO_ROOT))] = hits
        self.assertEqual(stale_hits, {})

    def test_active_public_surfaces_publish_all_current_anchor_markers(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in ACTIVE_PUBLIC_PATHS
        )
        missing = [marker for marker in CURRENT_MARKERS if marker not in combined]
        self.assertEqual(missing, [])

    def test_active_public_surfaces_do_not_expose_internal_planning_material(self):
        leaks: dict[str, list[str]] = {}
        for path in ACTIVE_PUBLIC_PATHS:
            text = path.read_text(encoding="utf-8")
            hits = [marker for marker in FORBIDDEN_INTERNAL_MARKERS if marker in text]
            if hits:
                leaks[str(path.relative_to(REPO_ROOT))] = hits
        self.assertEqual(leaks, {})


if __name__ == "__main__":
    unittest.main()
