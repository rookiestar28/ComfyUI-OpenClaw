"""Contract for the pinned real-host frontend compatibility smoke lane.

The lane itself cannot run during repository acceptance: it needs a real host,
network access and authorization. What can be checked here is everything that
decides whether a future authorized run would mean anything - that the pins agree
with the tracked compatibility anchors, that an unverified release artifact
cannot produce evidence, that the host is started on loopback with finite
deadlines, that the lane never blocks a pull request, and that compatibility
evidence cannot advance without a run identifier.
"""

import io
import json
import unittest
import zipfile
from pathlib import Path

from scripts.real_host_smoke import (
    AUTHORIZATION_ENV,
    PEER_FIXTURE_SOURCE,
    SmokeError,
    assert_subject_runnable,
    build_host_args,
    emit_pins,
    evidence_update_is_allowed,
    load_policy,
    readiness_url,
    release_digest_is_pinned,
    resolve_subject,
    verify_and_extract_release_asset,
    verify_core_checkout,
    wait_for_host,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "tests" / "real_host_smoke_policy.json"
POLICY = load_policy(POLICY_PATH)
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "real_host_smoke.yml"
MATRIX_PATH = REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
HOST_SURFACE_PATH = REPO_ROOT / "web" / "openclaw_host_surface.js"
REAL_HOST_CONFIG = REPO_ROOT / "playwright.real-host.config.js"
HARNESS_CONFIG = REPO_ROOT / "playwright.config.js"
SPEC_PATH = REPO_ROOT / "tests" / "real_host" / "specs" / "real_host_smoke.spec.js"


def _matrix_metadata() -> dict:
    from services.compatibility_matrix_governance import extract_metadata_block

    metadata, issues, _ = extract_metadata_block(
        MATRIX_PATH.read_text(encoding="utf-8")
    )
    if issues or metadata is None:
        raise AssertionError(f"compatibility matrix metadata is unreadable: {issues}")
    return metadata


class TestPinsAgreeWithTrackedAnchors(unittest.TestCase):
    """A lane pinned to different facts than the matrix would prove nothing."""

    def test_core_and_frontend_pins_match_the_compatibility_matrix(self):
        baselines = _matrix_metadata()["reference_baselines"]

        self.assertEqual(
            POLICY["core"]["source_head"], baselines["comfyui"]["source_head"]
        )
        self.assertEqual(
            POLICY["core"]["project_version"], baselines["comfyui"]["project_version"]
        )
        self.assertEqual(
            POLICY["core"]["bundled_frontend_version"],
            baselines["comfyui"]["bundled_frontend_version"],
        )
        self.assertEqual(
            POLICY["subjects"]["bundled"]["frontend_version"],
            baselines["comfyui"]["bundled_frontend_version"],
        )
        release = POLICY["subjects"]["standalone_release"]
        self.assertEqual(
            release["frontend_version"],
            baselines["comfyui_frontend"]["release_version"],
        )
        self.assertEqual(
            release["release_tag"], baselines["comfyui_frontend"]["release_tag"]
        )
        self.assertEqual(
            release["release_tag_commit"],
            baselines["comfyui_frontend"]["release_tag_commit"],
        )
        self.assertEqual(
            POLICY["not_executed"]["frontend_source_head"],
            baselines["comfyui_frontend"]["source_head"],
        )

    def test_the_frontend_module_publishes_the_same_pins(self):
        surface = HOST_SURFACE_PATH.read_text(encoding="utf-8")

        self.assertIn(POLICY["core"]["bundled_frontend_version"], surface)
        self.assertIn(
            POLICY["subjects"]["standalone_release"]["frontend_version"], surface
        )
        self.assertIn(POLICY["not_executed"]["frontend_source_head"][:10], surface)

    def test_the_sidebar_floor_matches_the_shipped_frontend_constant(self):
        layout = (REPO_ROOT / "web" / "openclaw_sidebar_layout.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            f"OPENCLAW_SIDEBAR_MIN_WIDTH_PX = {POLICY['geometry']['sidebar_min_width_px']}",
            layout,
        )


class TestUnverifiedReleaseArtifactCannotRun(unittest.TestCase):
    """An artifact nobody verified must never be able to produce release evidence."""

    def test_the_release_digest_ships_unset_and_blocks_that_subject(self):
        release = resolve_subject(POLICY, "standalone_release")

        self.assertIsNone(release["release_asset_sha256"])
        self.assertFalse(release_digest_is_pinned(POLICY, "standalone_release"))
        with self.assertRaises(SmokeError) as caught:
            assert_subject_runnable(release)
        self.assertIn("no pinned sha256", str(caught.exception))

    def test_the_bundled_subject_is_not_gated_on_a_digest_it_has_no_asset_for(self):
        assert_subject_runnable(resolve_subject(POLICY, "bundled"))
        self.assertTrue(release_digest_is_pinned(POLICY, "bundled"))

    def test_only_a_well_formed_lowercase_digest_is_accepted(self):
        release = dict(resolve_subject(POLICY, "standalone_release"))

        release["release_asset_sha256"] = "a" * 64
        assert_subject_runnable(release)
        for bad in ("", "A" * 64, "a" * 63, "a" * 65, "z" * 64, 12345, None):
            with self.subTest(digest=bad):
                release["release_asset_sha256"] = bad
                with self.assertRaises(SmokeError):
                    assert_subject_runnable(release)

    def test_a_digest_mismatch_refuses_to_extract(self):
        with self.subTest("mismatch"):
            archive = self._zip({"index.html": b"real"})
            release = dict(resolve_subject(POLICY, "standalone_release"))
            release["release_asset_sha256"] = "b" * 64
            with self.assertRaises(SmokeError) as caught:
                verify_and_extract_release_asset(
                    archive, release, archive.parent / "out"
                )
            self.assertIn("digest mismatch", str(caught.exception))

    def test_a_member_escaping_its_destination_refuses_to_extract(self):
        import hashlib

        archive = self._zip({"../escaped.txt": b"nope"})
        release = dict(resolve_subject(POLICY, "standalone_release"))
        release["release_asset_sha256"] = hashlib.sha256(
            archive.read_bytes()
        ).hexdigest()

        with self.assertRaises(SmokeError) as caught:
            verify_and_extract_release_asset(archive, release, archive.parent / "out")
        self.assertIn("escapes its destination", str(caught.exception))

    def _zip(self, members: dict) -> Path:
        import tempfile

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name, payload in members.items():
                bundle.writestr(name, payload)
        archive = directory / "dist.zip"
        archive.write_bytes(buffer.getvalue())
        return archive


class TestHostStartupIsBoundedAndLoopbackOnly(unittest.TestCase):
    def test_both_subjects_start_on_loopback_cpu_with_the_right_frontend_request(self):
        bundled = build_host_args(POLICY, resolve_subject(POLICY, "bundled"), 18188)
        release = build_host_args(
            POLICY, resolve_subject(POLICY, "standalone_release"), 18189
        )

        self.assertEqual(
            bundled,
            [
                "--cpu",
                "--disable-auto-launch",
                "--port",
                "18188",
                "--listen",
                "127.0.0.1",
            ],
        )
        self.assertNotIn("--front-end-version", bundled)
        self.assertEqual(
            release[-2:], ["--front-end-version", "Comfy-Org/ComfyUI_frontend@v1.54.3"]
        )
        for args in (bundled, release):
            for forbidden in POLICY["runtime"]["forbidden_args"]:
                self.assertNotIn(forbidden, args)

    def test_a_non_loopback_bind_host_or_privileged_port_is_refused(self):
        exposed = json.loads(json.dumps(POLICY))
        exposed["runtime"]["bind_host"] = "0.0.0.0"
        subject = resolve_subject(POLICY, "bundled")

        with self.assertRaises(SmokeError):
            build_host_args(exposed, subject, 18188)
        for port in (80, 0, -1, 70000, True, "18188", 18188.5):
            with self.subTest(port=port), self.assertRaises(SmokeError):
                build_host_args(POLICY, subject, port)

    def test_the_readiness_url_targets_the_loopback_health_route(self):
        self.assertEqual(
            readiness_url(POLICY, 18188), "http://127.0.0.1:18188/openclaw/health"
        )

    def test_every_phase_declares_a_finite_deadline(self):
        deadlines = POLICY["deadlines_seconds"]

        self.assertEqual(set(deadlines), {"install", "startup", "smoke", "teardown"})
        for phase, seconds in deadlines.items():
            with self.subTest(phase=phase):
                self.assertIsInstance(seconds, int)
                self.assertGreater(seconds, 0)
                self.assertLessEqual(seconds, 3600)

    def test_the_readiness_wait_gives_up_instead_of_polling_forever(self):
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        slept = []

        with self.assertRaises(SmokeError) as caught:
            wait_for_host(
                "http://127.0.0.1:9/openclaw/health",
                3.0,
                sleep=slept.append,
                clock=lambda: next(ticks),
            )

        self.assertIn("did not become ready", str(caught.exception))
        self.assertLessEqual(len(slept), 4)

    def test_a_core_checkout_at_the_wrong_commit_is_refused(self):
        with self.assertRaises(SmokeError) as caught:
            verify_core_checkout(REPO_ROOT, POLICY["core"]["source_head"])
        self.assertIn("expected the pinned", str(caught.exception))


class TestEvidenceCannotAdvanceWithoutARun(unittest.TestCase):
    def test_the_matrix_still_reports_real_host_evidence_as_pending(self):
        states = _matrix_metadata()["evidence_states"]["real_host"]

        self.assertEqual(states["state"], POLICY["evidence"]["current_state"])
        self.assertEqual(states["state"], "pending")
        self.assertIsNone(states["run_id"])
        self.assertIsNone(states["evidence_id"])

    def test_the_frontend_module_still_reports_real_host_validation_as_pending(self):
        surface = HOST_SURFACE_PATH.read_text(encoding="utf-8")

        self.assertIn('HOST_REAL_VALIDATION_STATE = "pending"', surface)

    def test_a_validated_state_requires_both_identifiers(self):
        self.assertEqual(
            evidence_update_is_allowed(
                POLICY, "validated", "run-1", "real-host-20260906"
            ),
            [],
        )
        self.assertEqual(
            evidence_update_is_allowed(POLICY, "validated", None, "real-host-20260906"),
            ["evidence state validated requires a run identifier"],
        )
        self.assertEqual(
            evidence_update_is_allowed(POLICY, "validated", "run-1", None),
            ["evidence state validated requires an evidence identifier"],
        )

    def test_a_pending_state_that_names_a_run_is_refused(self):
        failures = evidence_update_is_allowed(
            POLICY, "pending", "run-1", "real-host-20260906"
        )

        self.assertEqual(
            failures,
            [
                "evidence state pending must not name a run identifier",
                "evidence state pending must not name an evidence identifier",
            ],
        )

    def test_the_lane_mirrors_the_matrix_rule_rather_than_inventing_one(self):
        from services.compatibility_matrix_governance import (
            EVIDENCE_RUN_REQUIRED_STATES,
        )

        self.assertEqual(
            sorted(POLICY["evidence"]["states_requiring_run_id"]),
            sorted(EVIDENCE_RUN_REQUIRED_STATES),
        )


class TestTheLaneNeverBlocksOrEscalates(unittest.TestCase):
    """A compatibility lane that can block a change or mutate issues is a hazard."""

    def setUp(self):
        self.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_the_workflow_runs_only_on_schedule_and_manual_dispatch(self):
        for trigger in POLICY["lane"]["triggers"]:
            self.assertIn(trigger, self.workflow)
        for forbidden in POLICY["lane"]["forbidden_triggers"]:
            with self.subTest(trigger=forbidden):
                self.assertNotIn(f"\n  {forbidden}:", self.workflow)

    def test_the_workflow_holds_read_only_permissions_and_creates_no_issues(self):
        self.assertIn("permissions:\n  contents: read\n", self.workflow)
        for escalation in ("issues: write", "pull-requests: write", "contents: write"):
            with self.subTest(permission=escalation):
                self.assertNotIn(escalation, self.workflow)
        for action in ("create-issue", "github-script", "peter-evans"):
            with self.subTest(action=action):
                self.assertNotIn(action, self.workflow)

    def test_every_job_declares_a_timeout(self):
        jobs_block = self.workflow.split("\njobs:\n", 1)[1]
        job_names = [
            line.strip().rstrip(":")
            for line in jobs_block.splitlines()
            if line.startswith("  ")
            and not line.startswith("    ")
            and line.rstrip().endswith(":")
        ]

        self.assertEqual(
            job_names, ["prepare", "bundled-frontend", "standalone-release-frontend"]
        )
        self.assertEqual(self.workflow.count("timeout-minutes:"), len(job_names))

    def test_the_release_subject_runs_only_when_its_digest_is_pinned(self):
        self.assertIn(
            "if: needs.prepare.outputs.release_digest_pinned == 'true'", self.workflow
        )
        self.assertIn("--emit-pins", self.workflow)

    def test_the_workflow_authorizes_execution_explicitly_rather_than_by_default(self):
        self.assertEqual(self.workflow.count(f"{AUTHORIZATION_ENV}: '1'"), 2)

    def test_the_workflow_runs_the_preflight_before_any_external_action(self):
        self.assertEqual(self.workflow.count("--preflight-only"), 2)

    def test_diagnostics_are_uploaded_with_a_retention_bound(self):
        self.assertEqual(self.workflow.count("retention-days: 14"), 2)
        self.assertNotIn("openclaw_state/**", self.workflow)


class TestTheRealHostLaneStaysIsolated(unittest.TestCase):
    def test_the_mocked_harness_never_collects_the_real_host_spec(self):
        harness = HARNESS_CONFIG.read_text(encoding="utf-8")

        self.assertIn("testDir: 'tests/e2e/specs'", harness)
        self.assertNotIn("real_host", harness)
        self.assertFalse(
            (REPO_ROOT / "tests" / "e2e" / "specs" / "real_host_smoke.spec.js").exists()
        )

    def test_the_real_host_config_refuses_a_non_loopback_origin_and_starts_no_server(
        self,
    ):
        config = REAL_HOST_CONFIG.read_text(encoding="utf-8")

        self.assertIn("testDir: 'tests/real_host/specs'", config)
        self.assertIn("must be a loopback origin", config)
        self.assertNotIn("webServer:", config)

    def test_the_peer_fixture_exists_and_registers_no_executable_node(self):
        self.assertTrue(PEER_FIXTURE_SOURCE.is_dir())
        module = (PEER_FIXTURE_SOURCE / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("NODE_CLASS_MAPPINGS: dict[str, type] = {}", module)
        self.assertIn('WEB_DIRECTORY = "./web"', module)
        self.assertTrue((PEER_FIXTURE_SOURCE / "web" / "peer_sidebar_tab.js").is_file())

    def test_no_product_code_imports_the_peer_fixture(self):
        for root in ("api", "connector", "models", "nodes", "services", "web"):
            for path in (REPO_ROOT / root).rglob("*"):
                if path.suffix in {".py", ".js"} and path.is_file():
                    with self.subTest(path=path.name):
                        self.assertNotIn(
                            "openclaw_smoke_peer", path.read_text(encoding="utf-8")
                        )


class TestRuntimeEvidenceNeverNamesAnUnexecutedCommit(unittest.TestCase):
    def test_the_spec_asserts_the_reviewed_source_head_is_not_a_runtime_subject(self):
        spec = SPEC_PATH.read_text(encoding="utf-8")

        self.assertIn("not_executed.frontend_source_head", spec)
        self.assertIn("never names the reviewed frontend source head as executed", spec)

    def test_the_reviewed_source_head_is_not_offered_as_a_runnable_subject(self):
        not_executed = POLICY["not_executed"]["frontend_source_head"]

        for subject in POLICY["subjects"].values():
            with self.subTest(subject=subject["id"]):
                self.assertNotIn(not_executed, json.dumps(subject))
        self.assertNotIn(not_executed, WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_the_emitted_pins_expose_only_reproducible_facts(self):
        rendered = emit_pins(POLICY, None)

        self.assertIn(f"core_head={POLICY['core']['source_head']}", rendered)
        self.assertIn("release_digest_pinned=false", rendered)
        self.assertNotIn(POLICY["not_executed"]["frontend_source_head"], rendered)


if __name__ == "__main__":
    unittest.main()
