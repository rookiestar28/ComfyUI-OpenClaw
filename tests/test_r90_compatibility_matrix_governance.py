"""
R90 compatibility matrix governance tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from services.compatibility_matrix_governance import (
    build_host_surface_contract,
    build_reference_evidence_projection,
    detect_anchor_drift,
    read_matrix_document,
    run_refresh_workflow,
    validate_metadata,
)
from services.operator_doctor import DoctorReport, check_compatibility_matrix_governance

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CURRENT_ANCHORS = {
    "comfyui": "31dfbd4c (v0.34.0-46-g31dfbd4c / pyproject 0.34.0)",
    "comfyui_frontend": "1.54.3 (9ff3fd7f0e / v1.54.3-21-g9ff3fd7f0e)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)",
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
}
EXPECTED_HOST_SURFACES = {
    "desktop": {
        "generation": "legacy_fixed_bundle",
        "anchor_key": "desktop",
        "hosted_version_mode": "fixed",
        "core_version": "0.22.3",
        "frontend_version": "1.43.18",
    },
    "comfy_desktop": {
        "generation": "managed_install",
        "anchor_key": "comfy_desktop",
        "hosted_version_mode": "installation_specific",
        "core_version": None,
        "frontend_version": None,
    },
}
ACTIVE_CURRENT_REFERENCE_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "release" / "compatibility_matrix.md",
    REPO_ROOT / "docs" / "release" / "support_policy.md",
    REPO_ROOT / "docs" / "asset_api_adoption_decision.md",
    REPO_ROOT / "docs" / "frontend_ux_walkthrough.md",
)
STALE_ACTIVE_REFERENCE_TOKENS = (
    "1377a2f7",
    "v0.27.0-47-g1377a2f7",
    "ceb5ae1eba",
    "v1.48.1-1-gceb5ae1eba",
    # R254: superseded by the 2026-09-05 source-review baseline.
    "3aba3dae",
    "v0.33.0-27-g3aba3dae",
    "569e65b30f",
    "v1.52.1-3-g569e65b30f",
)
EXPECTED_REFERENCE_BASELINES = {
    "comfyui": {
        "bundled_frontend_version": "1.51.9",
        "project_version": "0.34.0",
        "source_describe": "v0.34.0-46-g31dfbd4c",
        "source_head": "31dfbd4ca0cb36ab6a573fc13daec8cc3a2e1e98",
        "tag": "v0.34.0",
        "tag_commit": "12d5279438bfefc058a269eae805ceab6047777f",
    },
    "comfyui_frontend": {
        "package_version": "1.54.3",
        "release_tag": "v1.54.3",
        "release_tag_commit": "b2f5587509d744d7779accce193db53f36a91d4a",
        "release_version": "1.54.3",
        "source_describe": "v1.54.3-21-g9ff3fd7f0e",
        "source_head": "9ff3fd7f0e36b810a621288ceaf6e74e3846bedd",
    },
}


class TestR90CompatMatrixGovernance(unittest.TestCase):
    def test_repo_matrix_has_valid_metadata(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        self.assertTrue(doc["has_meta"], msg=doc["issues"])
        validation = validate_metadata(doc["metadata"])
        self.assertTrue(validation["ok"], msg=validation)
        self.assertIn(validation["status"], ("fresh", "warning", "stale"))

    def test_repo_matrix_tracks_current_reference_anchors(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        self.assertEqual(doc["metadata"]["schema_version"], 3)
        self.assertEqual(doc["metadata"]["anchors"], EXPECTED_CURRENT_ANCHORS)
        self.assertEqual(doc["metadata"]["host_surfaces"], EXPECTED_HOST_SURFACES)
        self.assertEqual(
            doc["metadata"]["reference_baselines"], EXPECTED_REFERENCE_BASELINES
        )

    def test_active_current_reference_files_reject_stale_anchors(self):
        stale_hits = {}
        for path in ACTIVE_CURRENT_REFERENCE_FILES:
            text = path.read_text(encoding="utf-8")
            hits = [token for token in STALE_ACTIVE_REFERENCE_TOKENS if token in text]
            if hits:
                stale_hits[str(path.relative_to(REPO_ROOT))] = hits
        self.assertEqual(stale_hits, {})

    def test_python_support_tiers_match_package_floor_and_test_sop_prerequisite(self):
        matrix = (REPO_ROOT / "docs" / "release" / "compatibility_matrix.md").read_text(
            encoding="utf-8"
        )
        support = (REPO_ROOT / "docs" / "release" / "support_policy.md").read_text(
            encoding="utf-8"
        )
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        test_sop = (REPO_ROOT / "tests" / "TEST_SOP.md").read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.10"', pyproject)
        self.assertIn("| **Python** | 3.13 |", matrix)
        self.assertIn("Current executed baseline is", matrix)
        self.assertIn(
            "- **Python**: 3.13 (current local Windows Full Gate baseline).",
            support,
        )
        self.assertIn(
            "- **Compatibility targets**: Python 3.10, 3.11, and 3.12", support
        )
        self.assertIn("- **Python**: 3.14.", support)
        self.assertIn("- **Python**: < 3.10.", support)
        self.assertIn("Python 3.10+", test_sop)

    def test_detect_anchor_drift(self):
        published = {
            "comfyui": "a",
            "comfyui_frontend": "b",
            "desktop": "c",
            "comfy_desktop": "d",
        }
        observed = {
            "comfyui": "a",
            "comfyui_frontend": "b2",
            "desktop": "unknown",
            "comfy_desktop": "unknown",
        }
        drift = detect_anchor_drift(published, observed)
        self.assertFalse(drift["ok"])
        self.assertEqual(drift["code"], "R90_ANCHOR_DRIFT")
        self.assertEqual(drift["drift"][0]["anchor"], "comfyui_frontend")

    def test_build_host_surface_contract_tracks_desktop_embedded_frontend_lag(self):
        contract = build_host_surface_contract(
            {
                "comfyui": EXPECTED_CURRENT_ANCHORS["comfyui"],
                "comfyui_frontend": EXPECTED_CURRENT_ANCHORS["comfyui_frontend"],
                "desktop": EXPECTED_CURRENT_ANCHORS["desktop"],
                "comfy_desktop": EXPECTED_CURRENT_ANCHORS["comfy_desktop"],
            },
            published_surfaces=EXPECTED_HOST_SURFACES,
        )
        self.assertTrue(contract["ok"], msg=contract)
        self.assertEqual(contract["code"], "R164_HOST_SURFACES_READY")
        self.assertEqual(
            contract["surfaces"]["desktop"]["embedded_frontend_version"], "1.43.18"
        )
        self.assertEqual(
            contract["surfaces"]["desktop"]["frontend_parity"]["status"], "lagging"
        )
        current = contract["surfaces"]["comfy_desktop"]
        self.assertEqual(current["desktop_version"], "1.0.32-rc.1")
        self.assertEqual(current["generation"], "managed_install")
        self.assertEqual(current["hosted_version_mode"], "installation_specific")
        self.assertIsNone(current["core_version"])
        self.assertIsNone(current["frontend_version"])

    def test_build_host_surface_contract_marks_invalid_desktop_anchor(self):
        contract = build_host_surface_contract(
            {
                "comfyui_frontend": "1.44.4",
                "desktop": "desktop-head",
            },
        )
        self.assertFalse(contract["ok"])
        self.assertEqual(contract["code"], "R164_HOST_SURFACE_CONTRACT_INVALID")
        self.assertEqual(contract["violations"][0]["code"], "R164_DESKTOP_ANCHOR_PARSE")

    def test_build_host_surface_contract_rejects_cross_wired_current_desktop(self):
        malformed_surfaces = json.loads(json.dumps(EXPECTED_HOST_SURFACES))
        malformed_surfaces["comfy_desktop"]["anchor_key"] = "desktop"
        malformed_surfaces["comfy_desktop"]["core_version"] = "0.29.0"
        contract = build_host_surface_contract(
            EXPECTED_CURRENT_ANCHORS,
            published_surfaces=malformed_surfaces,
        )
        self.assertFalse(contract["ok"])
        codes = {entry["code"] for entry in contract["violations"]}
        self.assertIn("R164_COMFY_DESKTOP_ANCHOR_KEY", codes)
        self.assertIn("R164_COMFY_DESKTOP_HOSTED_VERSION_MODE", codes)

    def test_validate_metadata_rejects_schema_v2_without_current_desktop_surface(self):
        validation = validate_metadata(
            {
                "schema_version": 2,
                "last_validated_date": "2026-07-31",
                "policy": {"warn_age_days": 30, "max_age_days": 45},
                "anchors": dict(EXPECTED_CURRENT_ANCHORS),
                "host_surfaces": {"desktop": EXPECTED_HOST_SURFACES["desktop"]},
            },
            today=date(2026, 7, 31),
        )
        self.assertFalse(validation["ok"])
        codes = {entry["code"] for entry in validation["violations"]}
        self.assertIn("R90_META_HOST_SURFACE_MISSING", codes)

    def test_validate_metadata_rejects_malformed_or_unknown_anchor_contracts(self):
        malformed = {
            "schema_version": 2,
            "last_validated_date": "2026-07-30",
            "policy": {"warn_age_days": 30, "max_age_days": 45},
            "anchors": {
                **EXPECTED_CURRENT_ANCHORS,
                "comfy_desktop": "current-desktop-head",
                "unexpected_host": "must-not-be-accepted",
            },
            "host_surfaces": json.loads(json.dumps(EXPECTED_HOST_SURFACES)),
        }
        validation = validate_metadata(malformed, today=date(2026, 7, 30))
        self.assertFalse(validation["ok"])
        codes = {entry["code"] for entry in validation["violations"]}
        self.assertIn("R90_META_ANCHOR_FORMAT", codes)
        self.assertIn("R90_META_ANCHOR_UNKNOWN", codes)

    def test_validate_metadata_requires_explicit_schema_v1_upgrade(self):
        validation = validate_metadata(
            {
                "schema_version": 1,
                "last_validated_date": "2020-01-01",
                "policy": {"warn_age_days": 30, "max_age_days": 45},
                "anchors": {
                    key: value
                    for key, value in EXPECTED_CURRENT_ANCHORS.items()
                    if key != "comfy_desktop"
                },
            }
        )
        self.assertFalse(validation["ok"])
        codes = {entry["code"] for entry in validation["violations"]}
        self.assertIn("R90_META_SCHEMA_UPGRADE_REQUIRED", codes)

    def test_validate_stale_metadata(self):
        metadata = {
            "schema_version": 2,
            "last_validated_date": "2020-01-01",
            "policy": {"warn_age_days": 1, "max_age_days": 2},
            "anchors": dict(EXPECTED_CURRENT_ANCHORS),
            "host_surfaces": json.loads(json.dumps(EXPECTED_HOST_SURFACES)),
        }
        validation = validate_metadata(metadata)
        self.assertTrue(validation["ok"])
        self.assertEqual(validation["status"], "stale")
        self.assertEqual(validation["code"], "R90_MATRIX_STALE")

    def test_refresh_workflow_dry_run_and_apply(self):
        src = REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            matrix.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            dry = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors={
                    "comfyui": "core-1",
                    "comfyui_frontend": "fe-1",
                    "desktop": "desktop-1",
                    "comfy_desktop": "current-desktop-1",
                },
                apply=False,
                updated_by="test",
            )
            dry_payload = dry.to_dict()
            self.assertIn("collect", dry_payload["stages"])
            self.assertEqual(dry_payload["stages"]["publish"]["mode"], "dry-run")
            self.assertFalse(dry_payload["stages"]["publish"]["updated"])

            applied = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors=dict(EXPECTED_CURRENT_ANCHORS),
                apply=True,
                updated_by="test",
            )
            self.assertTrue(applied.ok)
            doc = read_matrix_document(matrix)
            self.assertEqual(doc["metadata"]["schema_version"], 3)
            self.assertEqual(
                doc["metadata"]["anchors"]["comfyui"],
                EXPECTED_CURRENT_ANCHORS["comfyui"],
            )
            self.assertEqual(
                doc["metadata"]["anchors"]["comfy_desktop"],
                EXPECTED_CURRENT_ANCHORS["comfy_desktop"],
            )
            self.assertEqual(doc["metadata"]["host_surfaces"], EXPECTED_HOST_SURFACES)
            self.assertEqual(doc["metadata"]["evidence"]["updated_by"], "test")

    def test_refresh_workflow_rejects_incomplete_apply_without_mutating_matrix(self):
        src = REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            baseline = src.read_text(encoding="utf-8")
            matrix.write_text(baseline, encoding="utf-8")

            result = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors={
                    **EXPECTED_CURRENT_ANCHORS,
                    "comfy_desktop": "unknown",
                },
                apply=True,
                updated_by="test",
                today=date(2026, 7, 30),
            )

            self.assertFalse(result.ok)
            payload = result.to_dict()
            self.assertFalse(payload["stages"]["publish"]["updated"])
            self.assertIn("R90_PUBLISH_REJECTED", payload["decision_codes"])
            self.assertEqual(matrix.read_text(encoding="utf-8"), baseline)

    def test_refresh_workflow_refuses_incomplete_schema_v2_publish(self):
        legacy_metadata = {
            "schema_version": 1,
            "last_validated_date": "2026-07-01",
            "policy": {"warn_age_days": 30, "max_age_days": 45},
            "anchors": {
                key: value
                for key, value in EXPECTED_CURRENT_ANCHORS.items()
                if key != "comfy_desktop"
            },
        }
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            original = (
                "# Compatibility Matrix\n\n"
                "```openclaw-compat-matrix-meta\n"
                + json.dumps(legacy_metadata)
                + "\n```\n\nbody\n"
            )
            matrix.write_text(original, encoding="utf-8")
            result = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors={
                    **legacy_metadata["anchors"],
                    "comfy_desktop": "unknown",
                },
                apply=True,
                updated_by="test",
                today=date(2026, 7, 30),
            )

            self.assertFalse(result.ok)
            self.assertFalse(result.stages["publish"]["updated"])
            codes = {
                entry["code"]
                for entry in result.stages["validate"]["after"]["violations"]
            }
            self.assertIn("R90_META_ANCHOR_UNRESOLVED", codes)
            self.assertEqual(matrix.read_text(encoding="utf-8"), original)

    def test_operator_doctor_warns_when_matrix_stale(self):
        with tempfile.TemporaryDirectory() as td:
            pack_root = Path(td)
            matrix_path = pack_root / "docs" / "release"
            matrix_path.mkdir(parents=True, exist_ok=True)
            matrix_path.joinpath("compatibility_matrix.md").write_text(
                (
                    "# Compatibility Matrix\n\n"
                    "```openclaw-compat-matrix-meta\n"
                    + json.dumps(
                        {
                            "schema_version": 2,
                            "last_validated_date": "2020-01-01",
                            "policy": {"warn_age_days": 1, "max_age_days": 2},
                            "anchors": dict(EXPECTED_CURRENT_ANCHORS),
                            "host_surfaces": EXPECTED_HOST_SURFACES,
                        }
                    )
                    + "\n```\n\nbody\n"
                ),
                encoding="utf-8",
            )
            report = DoctorReport()
            check_compatibility_matrix_governance(report, pack_root)
            checks = {c.name: c for c in report.checks}
            self.assertIn("compatibility_matrix_governance", checks)
            self.assertEqual(checks["compatibility_matrix_governance"].severity, "warn")
            self.assertEqual(
                report.environment["compat_matrix_validation_code"], "R90_MATRIX_STALE"
            )

    def test_operator_doctor_reports_host_surface_contract(self):
        report = DoctorReport()
        check_compatibility_matrix_governance(report, REPO_ROOT)
        checks = {c.name: c for c in report.checks}
        self.assertIn("compatibility_matrix_host_surface_contract", checks)
        self.assertEqual(
            checks["compatibility_matrix_host_surface_contract"].severity, "pass"
        )
        self.assertEqual(
            report.environment["compat_desktop_embedded_frontend_status"], "lagging"
        )
        self.assertEqual(
            report.environment["compat_comfy_desktop_generation"], "managed_install"
        )
        self.assertEqual(
            report.environment["compat_comfy_desktop_hosted_version_mode"],
            "installation_specific",
        )

    def test_r166_desktop_runtime_lane_matches_recorded_desktop_anchor_contract(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        contract = build_host_surface_contract(
            doc["metadata"]["anchors"],
            published_surfaces=doc["metadata"]["host_surfaces"],
        )
        self.assertTrue(contract["ok"], msg=contract)
        desktop_surface = contract["surfaces"]["desktop"]
        self.assertEqual(desktop_surface["frontend_parity"]["status"], "lagging")
        self.assertEqual(desktop_surface["embedded_frontend_version"], "1.43.18")
        self.assertTrue(
            (
                REPO_ROOT / "tests" / "e2e" / "specs" / "desktop_host_parity.spec.js"
            ).exists(),
            msg="R166 executable desktop host parity lane is missing",
        )

    def test_script_smoke_emits_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            matrix.write_text(
                (REPO_ROOT / "docs" / "release" / "compatibility_matrix.md").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            out = Path(td) / "evidence.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "compatibility_matrix_refresh.py"),
                    "--matrix-path",
                    str(matrix),
                    "--anchor-comfyui",
                    "core-x",
                    "--anchor-frontend",
                    "fe-x",
                    "--anchor-desktop",
                    "desk-x",
                    "--anchor-comfy-desktop",
                    EXPECTED_CURRENT_ANCHORS["comfy_desktop"],
                    "--output",
                    str(out),
                    "--pretty",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("stages", payload)
            self.assertIn("collect", payload["stages"])
            self.assertIn("R90_PUBLISH_DRY_RUN", payload["decision_codes"])


class TestR254ReferenceBaselineEvidence(unittest.TestCase):
    """R254: source review, repository validation and real-host proof stay disjoint."""

    def _schema3(self, **overrides):
        metadata = {
            "schema_version": 3,
            "last_validated_date": "2026-09-06",
            "policy": {"warn_age_days": 30, "max_age_days": 45},
            "anchors": dict(EXPECTED_CURRENT_ANCHORS),
            "host_surfaces": json.loads(json.dumps(EXPECTED_HOST_SURFACES)),
            "reference_baselines": json.loads(json.dumps(EXPECTED_REFERENCE_BASELINES)),
            "evidence_states": {
                "source_review": {
                    "state": "reviewed",
                    "evidence_id": "source-review-20260905",
                    "run_id": None,
                },
                "repository_validation": {
                    "state": "validated",
                    "evidence_id": "repo-validation-20260906",
                    "run_id": "windows-full-gate-20260906",
                },
                "real_host": {
                    "state": "pending",
                    "evidence_id": None,
                    "run_id": None,
                },
            },
        }
        metadata.update(overrides)
        return metadata

    def _codes(self, metadata):
        validation = validate_metadata(metadata, today=date(2026, 9, 6))
        return validation, {entry["code"] for entry in validation["violations"]}

    def test_repo_matrix_keeps_real_host_validation_pending(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        states = doc["metadata"]["evidence_states"]
        self.assertEqual(states["real_host"]["state"], "pending")
        self.assertIsNone(states["real_host"]["run_id"])
        self.assertIsNone(states["real_host"]["evidence_id"])
        self.assertEqual(states["source_review"]["state"], "reviewed")
        self.assertEqual(states["repository_validation"]["state"], "validated")

    def test_repo_matrix_separates_frontend_source_head_from_release(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        frontend = doc["metadata"]["reference_baselines"]["comfyui_frontend"]
        self.assertNotEqual(frontend["source_head"], frontend["release_tag_commit"])
        self.assertEqual(frontend["release_version"], "1.54.3")
        core = doc["metadata"]["reference_baselines"]["comfyui"]
        self.assertNotEqual(core["source_head"], core["tag_commit"])
        self.assertEqual(core["bundled_frontend_version"], "1.51.9")

    def test_schema_v3_metadata_validates(self):
        validation, codes = self._codes(self._schema3())
        self.assertTrue(validation["ok"], msg=validation["violations"])
        self.assertEqual(codes, set())

    def test_schema_v2_metadata_remains_supported(self):
        # R254 added the new objects additively; a schema-2 document must not
        # start failing just because schema 3 exists.
        legacy = {
            "schema_version": 2,
            "last_validated_date": "2026-09-06",
            "policy": {"warn_age_days": 30, "max_age_days": 45},
            "anchors": dict(EXPECTED_CURRENT_ANCHORS),
            "host_surfaces": json.loads(json.dumps(EXPECTED_HOST_SURFACES)),
        }
        validation, codes = self._codes(legacy)
        self.assertTrue(validation["ok"], msg=validation["violations"])
        self.assertEqual(codes, set())

    def test_unknown_evidence_state_fails_closed(self):
        metadata = self._schema3()
        metadata["evidence_states"]["real_host"]["state"] = "probably_fine"
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_STATE_UNKNOWN", codes)

    def test_missing_evidence_state_fails_closed(self):
        metadata = self._schema3()
        del metadata["evidence_states"]["real_host"]
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_ENTRY_MISSING", codes)

    def test_validated_real_host_requires_a_run_id(self):
        metadata = self._schema3()
        metadata["evidence_states"]["real_host"] = {
            "state": "validated",
            "evidence_id": "real-host-20260906",
            "run_id": None,
        }
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_RUN_ID_REQUIRED", codes)

    def test_pending_evidence_cannot_carry_a_fabricated_run(self):
        metadata = self._schema3()
        metadata["evidence_states"]["real_host"]["run_id"] = "imagined-run-1"
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_PENDING_RUN_ID", codes)

    def test_source_review_cannot_claim_validated(self):
        metadata = self._schema3()
        metadata["evidence_states"]["source_review"]["state"] = "validated"
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_STATE_NOT_ALLOWED", codes)

    def test_evidence_identifiers_must_be_distinct(self):
        metadata = self._schema3()
        metadata["evidence_states"]["repository_validation"][
            "evidence_id"
        ] = "source-review-20260905"
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_EVIDENCE_ID_SHARED", codes)

    def test_collapsed_frontend_subjects_are_rejected(self):
        metadata = self._schema3()
        frontend = metadata["reference_baselines"]["comfyui_frontend"]
        frontend["release_tag_commit"] = frontend["source_head"]
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_BASELINE_SUBJECT_COLLAPSE", codes)

    def test_short_or_malformed_baseline_commits_are_rejected(self):
        metadata = self._schema3()
        metadata["reference_baselines"]["comfyui"]["source_head"] = "31dfbd4c"
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_BASELINE_FIELD_FORMAT", codes)

    def test_missing_reference_baselines_are_rejected(self):
        metadata = self._schema3()
        del metadata["reference_baselines"]
        validation, codes = self._codes(metadata)
        self.assertFalse(validation["ok"])
        self.assertIn("R254_BASELINES_MISSING", codes)

    def test_projection_is_bounded_and_never_upgrades_unknown_state(self):
        metadata = self._schema3()
        metadata["evidence_states"]["real_host"]["state"] = "definitely_validated"
        projection = build_reference_evidence_projection(metadata)
        self.assertEqual(projection["real_host"], "unknown")
        self.assertEqual(projection["source_review"], "reviewed")
        self.assertEqual(projection["repository_validation"], "validated")
        self.assertEqual(
            set(projection),
            {
                "schema_version",
                "source_review",
                "repository_validation",
                "real_host",
                "core_version",
                "core_bundled_frontend_version",
                "frontend_release_version",
            },
        )

    def test_projection_exposes_no_internal_or_local_detail(self):
        doc = read_matrix_document(
            REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        )
        serialized = json.dumps(build_reference_evidence_projection(doc["metadata"]))
        forbidden_fragments = (
            "." + "planning",
            "reference/" + "docs",
            "/mnt/",
            ".tmp",
            chr(92),
        )
        for forbidden in forbidden_fragments:
            self.assertNotIn(forbidden, serialized)

    def test_projection_degrades_on_empty_metadata(self):
        projection = build_reference_evidence_projection(None)
        self.assertEqual(projection["real_host"], "unknown")
        self.assertEqual(projection["source_review"], "unknown")
        self.assertEqual(projection["core_version"], "unknown")


if __name__ == "__main__":
    unittest.main()
