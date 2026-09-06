from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Surfaces that must describe the *current* host baseline. A prior anchor here is
# a stale claim, not history.
ACTIVE_PUBLIC_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "release" / "compatibility_matrix.md",
    REPO_ROOT / "docs" / "release" / "support_policy.md",
    REPO_ROOT / "docs" / "frontend_ux_walkthrough.md",
    REPO_ROOT / "docs" / "asset_api_adoption_decision.md",
    REPO_ROOT / "web" / "openclaw_host_surface.js",
)

# R254: dated records of what a past refresh did. They keep their original
# anchors, and rewriting them is itself a failure.
HISTORICAL_RECORD_PATHS = (
    REPO_ROOT / "docs" / "release" / "v1.1.0.md",
    REPO_ROOT / "docs" / "release" / "recent_updates.md",
)
HISTORICAL_REQUIRED_MARKERS = ("3aba3dae", "1.52.1")

STALE_MARKERS = (
    "1377a2f7",
    "ceb5ae1eba",
    "standalone frontend `1.48.1`",
    "9cf91339",
    "v0.29.0-12-g9cf91339",
    "4b3866b838",
    "1.49.1",
    # R254: superseded by the 2026-09-05 source-review baseline.
    "3aba3dae",
    "v0.33.0-27-g3aba3dae",
    "569e65b30f",
    "v1.52.1-3-g569e65b30f",
    "standalone frontend `1.52.1`",
    "`1.49.6`",
)

CURRENT_MARKERS = (
    # ComfyUI core source-review head, tag baseline and bundled frontend pin.
    "31dfbd4c",
    "v0.34.0-46-g31dfbd4c",
    "12d52794",
    "1.51.9",
    # Standalone frontend source-review head versus the reproducible release.
    "9ff3fd7f0e",
    "v1.54.3-21-g9ff3fd7f0e",
    "b2f55875",
    "1.54.3",
    # Desktop generations are unchanged and must stay published.
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

    def test_historical_records_retain_their_original_anchors(self):
        # R254: the allowlist exists so history survives the refresh. Losing a
        # historical anchor means a dated record was rewritten.
        missing: dict[str, list[str]] = {}
        for path in HISTORICAL_RECORD_PATHS:
            text = path.read_text(encoding="utf-8")
            absent = [
                marker for marker in HISTORICAL_REQUIRED_MARKERS if marker not in text
            ]
            if absent:
                missing[str(path.relative_to(REPO_ROOT))] = absent
        self.assertEqual(missing, {})

    def test_historical_allowlist_and_active_surfaces_are_disjoint(self):
        overlap = set(ACTIVE_PUBLIC_PATHS) & set(HISTORICAL_RECORD_PATHS)
        self.assertEqual(overlap, set())

    def test_active_public_surfaces_do_not_expose_internal_planning_material(self):
        leaks: dict[str, list[str]] = {}
        for path in ACTIVE_PUBLIC_PATHS + HISTORICAL_RECORD_PATHS:
            text = path.read_text(encoding="utf-8")
            hits = [marker for marker in FORBIDDEN_INTERNAL_MARKERS if marker in text]
            if hits:
                leaks[str(path.relative_to(REPO_ROOT))] = hits
        self.assertEqual(leaks, {})


if __name__ == "__main__":
    unittest.main()
