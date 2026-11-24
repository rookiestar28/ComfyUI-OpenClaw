"""Contract for the pinned real-host frontend compatibility smoke lane.

The lane itself cannot run during repository acceptance: it needs a real host,
network access and authorization. What can be checked here is everything that
decides whether a future authorized run would mean anything - that the pins agree
with the tracked compatibility anchors, that an unverified release artifact
cannot produce evidence, that the host is started on loopback with finite
deadlines, that the lane never blocks a pull request, and that compatibility
evidence cannot advance without a run identifier.
"""

import copy
import io
import json
import re
import subprocess
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from scripts.real_host_smoke import (
    AUTHORIZATION_ENV,
    DANGEROUS_BIND_OVERRIDE_ENV,
    PEER_FIXTURE_SOURCE,
    HostPaths,
    SmokeError,
    assert_subject_runnable,
    build_host_args,
    emit_pins,
    evidence_update_is_allowed,
    fetch_core,
    load_policy,
    readiness_url,
    release_digest_is_pinned,
    resolve_subject,
    start_host,
    stop_host,
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


def _subjects_naming_the_reviewed_frontend_head(policy: dict) -> list[str]:
    """Runnable subjects that carry the reviewed frontend source head where they must not.

    The head may appear in a subject only as the standalone release's own
    `release_tag_commit`, and only when the policy says the reviewed head is the tagged
    release (its describe is exactly the release tag). Everything else, including every
    mention while the head is ahead of its release, is a leak.
    """
    not_executed = policy["not_executed"]
    head = not_executed["frontend_source_head"]
    leaks = []
    for subject in policy["subjects"].values():
        visible = dict(subject)
        if (
            subject.get("id") == "standalone_release"
            and visible.get("release_tag_commit") == head
            and not_executed.get("frontend_source_describe")
            == visible.get("release_tag")
        ):
            del visible["release_tag_commit"]
        if head in json.dumps(visible):
            leaks.append(subject["id"])
    return leaks


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

    def test_native_sidebar_floor_is_distinct_and_requires_host_owned_allocation(self):
        self.assertEqual(POLICY["geometry"]["native_sidebar_min_width_px"], 312)
        spec = (REPO_ROOT / "tests/real_host/specs/real_host_smoke.spec.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("expect(mounted.native).toBe(true)", spec)
        self.assertIn("expect(mounted.layoutMode).toBe('host')", spec)
        self.assertIn("Math.abs(mounted.width - baseline.width)", spec)
        self.assertIn("Math.abs(mounted.width - hostOnly.width)", spec)
        self.assertIn(
            "POLICY.geometry.native_sidebar_min_width_px, HOST_WIDTH_TOLERANCE_PX", spec
        )


class TestUnverifiedReleaseArtifactCannotRun(unittest.TestCase):
    """An artifact nobody verified must never be able to produce release evidence."""

    def test_the_tracked_release_digest_is_pinned_and_well_formed(self):
        release = resolve_subject(POLICY, "standalone_release")
        digest = release["release_asset_sha256"]

        self.assertIsInstance(digest, str)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertTrue(release_digest_is_pinned(POLICY, "standalone_release"))

    def test_the_pinned_digest_says_where_it_came_from(self):
        release = resolve_subject(POLICY, "standalone_release")
        source = release["release_asset_digest_source"]

        # A pinned hash with no stated provenance cannot be re-checked by anyone
        # later, which is most of what makes it worth pinning.
        self.assertIn(release["release_tag"], source)
        self.assertIn(release["release_asset_name"], source)
        self.assertIsInstance(release["release_asset_size_bytes"], int)
        self.assertGreater(release["release_asset_size_bytes"], 0)

    def test_a_release_subject_without_a_digest_still_fails_closed(self):
        # The pin can be removed and a later subject may arrive without one, so
        # the refusal has to stay covered independently of today's policy value.
        release = dict(resolve_subject(POLICY, "standalone_release"))
        release["release_asset_sha256"] = None

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
            ],
        )
        # No --listen. It restates the host's own default bind, but the exposure
        # heuristic reads the flag and not its value, so passing it forces an admin
        # token the browser cannot present. See the HOTSPOT note on build_host_args.
        self.assertNotIn("--listen", bundled)
        self.assertNotIn("--listen", release)
        self.assertNotIn("--front-end-version", bundled)
        self.assertEqual(
            release[-2:], ["--front-end-version", "Comfy-Org/ComfyUI_frontend@v1.61.8"]
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

        self.assertEqual(
            set(deadlines), {"fetch", "install", "startup", "smoke", "teardown"}
        )
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
    def test_the_matrix_reports_the_refreshed_pair_as_validated(self):
        states = _matrix_metadata()["evidence_states"]["real_host"]

        # The paired campaign on the refreshed subjects ran, so the lane's recorded
        # state, the matrix and the frontend module all carry the same validated state
        # and the same identifiers.
        self.assertEqual(states["state"], POLICY["evidence"]["current_state"])
        self.assertEqual(states["state"], "validated")
        self.assertEqual(states["run_id"], "host-pair-20261124-isolated-01")
        self.assertEqual(states["evidence_id"], "host-evidence-20261124-isolated-01")
        self.assertEqual(POLICY["evidence"]["run_id"], "host-pair-20261124-isolated-01")
        self.assertEqual(
            POLICY["evidence"]["evidence_id"], "host-evidence-20261124-isolated-01"
        )
        self.assertEqual(
            evidence_update_is_allowed(
                POLICY, states["state"], states["run_id"], states["evidence_id"]
            ),
            [],
        )

    def test_the_previous_campaign_survives_only_as_scoped_history(self):
        history = _matrix_metadata()["historical_evidence"][-1]

        self.assertEqual(history["last_validated_date"], "2026-11-01")
        self.assertEqual(
            history["evidence_states"]["real_host"]["run_id"],
            "host-pair-20261101-isolated-01",
        )
        self.assertNotEqual(
            history["reference_baselines"]["comfyui"]["source_head"],
            POLICY["core"]["source_head"],
        )
        # A pending state may not borrow the history's identifiers.
        self.assertEqual(
            evidence_update_is_allowed(
                POLICY,
                "pending",
                history["evidence_states"]["real_host"]["run_id"],
                history["evidence_states"]["real_host"]["evidence_id"],
            ),
            [
                "evidence state pending must not name a run identifier",
                "evidence state pending must not name an evidence identifier",
            ],
        )

    def test_the_frontend_module_reports_validated_real_host_validation(self):
        surface = HOST_SURFACE_PATH.read_text(encoding="utf-8")

        self.assertIn('HOST_REAL_VALIDATION_STATE = "validated"', surface)
        self.assertNotIn('HOST_REAL_VALIDATION_STATE = "pending"', surface)

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

    def test_the_download_uses_the_prepared_release_tag_without_a_stale_literal(self):
        self.assertIn(
            "release_tag: ${{ steps.pins.outputs.release_tag }}", self.workflow
        )
        self.assertIn(
            "RELEASE_TAG: ${{ needs.prepare.outputs.release_tag }}", self.workflow
        )
        self.assertIn('gh release download "$RELEASE_TAG"', self.workflow)
        self.assertNotRegex(self.workflow, r"gh release download v\d+\.\d+\.\d+")

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

        self.assertEqual(_subjects_naming_the_reviewed_frontend_head(POLICY), [])
        self.assertNotIn(not_executed, WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_the_head_equals_the_release_commit_only_for_an_exact_tag_describe(self):
        not_executed = POLICY["not_executed"]
        release = POLICY["subjects"]["standalone_release"]

        same_commit = (
            not_executed["frontend_source_head"] == release["release_tag_commit"]
        )
        exact_tag = not_executed["frontend_source_describe"] == release["release_tag"]
        self.assertEqual(same_commit, exact_tag)
        if not same_commit:
            self.assertTrue(
                not_executed["frontend_source_describe"].startswith(
                    f"{release['release_tag']}-"
                )
            )

    def test_the_head_guard_still_catches_a_head_the_policy_should_not_name(self):
        distinct = "b" * 40

        # A distinct head named anywhere in a runnable subject.
        leaked = copy.deepcopy(POLICY)
        leaked["not_executed"]["frontend_source_head"] = distinct
        leaked["not_executed"]["frontend_source_describe"] = "v1.61.8-3-gbbbbbbb"
        self.assertEqual(_subjects_naming_the_reviewed_frontend_head(leaked), [])
        leaked["subjects"]["bundled"]["summary"] += f" {distinct}"
        self.assertEqual(
            _subjects_naming_the_reviewed_frontend_head(leaked), ["bundled"]
        )

        # A distinct head offered as the release's tag commit is a leak, not the exception.
        as_release = copy.deepcopy(leaked)
        as_release["subjects"]["bundled"]["summary"] = POLICY["subjects"]["bundled"][
            "summary"
        ]
        as_release["subjects"]["standalone_release"]["release_tag_commit"] = distinct
        self.assertEqual(
            _subjects_naming_the_reviewed_frontend_head(as_release),
            ["standalone_release"],
        )

        # The release's own commit is only exempt for an exact-tag describe.
        ahead = copy.deepcopy(POLICY)
        ahead["not_executed"]["frontend_source_describe"] = "v1.61.8-3-g388973a"
        self.assertEqual(
            _subjects_naming_the_reviewed_frontend_head(ahead), ["standalone_release"]
        )

        # The exception covers one field, not the rest of the subject.
        other_field = copy.deepcopy(POLICY)
        other_field["subjects"]["standalone_release"][
            "summary"
        ] += f" {POLICY['not_executed']['frontend_source_head']}"
        self.assertEqual(
            _subjects_naming_the_reviewed_frontend_head(other_field),
            ["standalone_release"],
        )

    def test_the_emitted_pins_expose_only_reproducible_facts(self):
        rendered = emit_pins(POLICY, None)

        self.assertIn(f"core_head={POLICY['core']['source_head']}", rendered)
        self.assertIn(
            f"release_tag={POLICY['subjects']['standalone_release']['release_tag']}",
            rendered,
        )
        self.assertIn("release_digest_pinned=true", rendered)
        self.assertNotIn(POLICY["not_executed"]["frontend_source_head"], rendered)

    def test_release_tag_output_rejects_missing_or_unsafe_policy_value(self):
        for tag in (None, "", "v1.55.11; echo unsafe", "v1.55.11\nother"):
            with self.subTest(tag=tag):
                changed = copy.deepcopy(POLICY)
                changed["subjects"]["standalone_release"]["release_tag"] = tag
                with self.assertRaises(SmokeError):
                    emit_pins(changed, None)


class TestUnexecutedAlternativesStayApartFromThePinnedSubjects(unittest.TestCase):
    """The published core release, its tag-only patch tags and a newer frontend patch.

    They were reviewed and recorded, never run. Each one is a different subject from
    what the lane pins, so none of them may be read as, or leak into, a runnable one.
    """

    def _alternative_commits(self) -> list[str]:
        not_executed = POLICY["not_executed"]
        return [
            not_executed["core_published_release"]["tag_commit"],
            *(
                entry["tag_commit"]
                for entry in not_executed["core_tag_only_patch_releases"]
            ),
        ]

    def test_the_published_core_release_is_a_different_subject_from_the_pinned_source(
        self,
    ):
        published = POLICY["not_executed"]["core_published_release"]
        core = POLICY["core"]
        baselines = _matrix_metadata()["reference_baselines"]["comfyui"]

        self.assertNotEqual(published["tag_commit"], core["source_head"])
        self.assertNotEqual(
            published["bundled_frontend_version"], core["bundled_frontend_version"]
        )
        self.assertNotEqual(
            published["package_wheel_sha256"],
            POLICY["subjects"]["bundled"]["package_wheel_sha256"],
        )
        self.assertEqual(published["tag"], baselines["tag"])
        self.assertEqual(published["tag_commit"], baselines["tag_commit"])
        self.assertRegex(published["package_wheel_sha256"], r"^[0-9a-f]{64}$")

    def test_tag_only_patch_releases_are_distinct_from_every_pinned_commit(self):
        not_executed = POLICY["not_executed"]
        patches = not_executed["core_tag_only_patch_releases"]
        commits = [entry["tag_commit"] for entry in patches]

        self.assertEqual(
            [entry["tag"] for entry in patches], ["v0.46.1", "v0.46.2", "v0.46.3"]
        )
        self.assertEqual(len(set(commits)), len(commits))
        for commit in commits:
            with self.subTest(commit=commit):
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertNotEqual(commit, POLICY["core"]["source_head"])
                self.assertNotEqual(
                    commit, not_executed["core_published_release"]["tag_commit"]
                )

    def test_no_unexecuted_alternative_is_offered_as_a_runnable_subject(self):
        newer = POLICY["not_executed"]["frontend_core_line_patch"]
        rendered_subjects = json.dumps(POLICY["subjects"])
        rendered_pins = emit_pins(POLICY, None)
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        published = POLICY["not_executed"]["core_published_release"]

        # The newer patch of the bundle's line never replaces the bundle the core pins.
        self.assertNotEqual(
            POLICY["subjects"]["bundled"]["frontend_version"], newer["version"]
        )
        self.assertEqual(
            POLICY["subjects"]["bundled"]["frontend_version"],
            POLICY["core"]["bundled_frontend_version"],
        )
        for text_name, text in (
            ("subjects", rendered_subjects),
            ("emitted pins", rendered_pins),
            ("workflow", workflow),
        ):
            for needle in (
                *self._alternative_commits(),
                published["package_wheel_sha256"],
                newer["version"],
            ):
                with self.subTest(text=text_name, needle=needle):
                    self.assertNotIn(needle, text)

    def test_publisher_reported_digests_say_they_were_not_downloaded_here(self):
        bundled = POLICY["subjects"]["bundled"]
        release = POLICY["subjects"]["standalone_release"]

        self.assertRegex(bundled["package_wheel_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(bundled["package_wheel_size_bytes"], 0)
        for source in (
            bundled["package_digest_source"],
            release["release_asset_digest_source"],
        ):
            with self.subTest(source=source[:40]):
                self.assertIn("publisher-reported on 2026-11-24", source)
                self.assertIn("not independently downloaded here", source)


class TestSpecUsesSurfacesThatExistOutsideTheMock(unittest.TestCase):
    """A real-host spec built on harness-only selectors fails on every real host.

    The mocked harness creates DOM that the real frontend does not: it gives the
    custom-tab mount an id, while the real frontend mounts a custom tab into a
    bare div. Borrowing those selectors produces a spec that reviews well and can
    never pass, so each surface the spec depends on is checked against product
    code here.
    """

    def setUp(self):
        self.spec = SPEC_PATH.read_text(encoding="utf-8")
        self.asset_refs = (REPO_ROOT / "web" / "openclaw_asset_refs.js").read_text(
            encoding="utf-8"
        )

    def test_the_spec_never_uses_the_harness_only_mount_id(self):
        harness_only = "sidebar-tab-comfyui-openclaw"
        product_files = list((REPO_ROOT / "web").rglob("*.js"))
        in_product = [
            path.name
            for path in product_files
            if "tests" not in path.parts
            and harness_only in path.read_text(encoding="utf-8")
        ]

        self.assertEqual(
            in_product,
            [],
            "the mount id is created by the mocked harness, not by the product",
        )
        # The spec is allowed to name it in prose - it explains why it is not
        # used - so only executable code is checked.
        code = re.sub(r"/\*.*?\*/", "", self.spec, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        hits = [line.strip() for line in code.splitlines() if harness_only in line]
        self.assertEqual(hits, [], f"harness-only selector used in spec code: {hits}")

    def test_the_spec_finds_the_mount_by_the_attribute_openclaw_itself_stamps(self):
        self.assertIn("[data-openclaw-host-surface]", self.spec)
        surface = (REPO_ROOT / "web" / "openclaw_host_surface.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("openclawHostSurface", surface)

    def test_every_asset_ref_function_the_spec_calls_is_exported_by_the_product(self):
        called = set(re.findall(r"module\.([A-Za-z0-9_]+)\(", self.spec))
        exported = set(
            re.findall(r"^export function ([A-Za-z0-9_]+)", self.asset_refs, re.M)
        )

        self.assertTrue(
            called, "the spec should exercise at least one asset-ref export"
        )
        self.assertEqual(called - exported, set(), f"exported: {sorted(exported)}")

    def test_the_spec_uses_a_settings_selector_the_product_actually_renders(self):
        settings = (REPO_ROOT / "web" / "tabs" / "settings_tab.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("openclaw-settings-scroll", self.spec)
        self.assertIn("openclaw-settings-scroll", settings)

    def test_the_rightmost_control_is_measured_rather_than_guessed_by_position(self):
        # The tab count varies with capabilities, so a fixed position would
        # silently measure a middle tab and miss an overflow at the right edge.
        self.assertNotIn("nth-child(4)", self.spec)
        self.assertIn("getBoundingClientRect().right", self.spec)

    def test_the_sizing_case_lets_the_host_write_the_width(self):
        # A width assigned by the spec proves only that OpenClaw keeps a value the
        # spec wrote; the real hazard is the host splitter's own write, so the case
        # drags the host gutter and never assigns a sizing style itself.
        for marker in (
            "survives a tab switch",
            '[role="separator"][data-orientation="horizontal"]',
            "page.mouse.down()",
            "page.mouse.up()",
            "evaluateHostWidthOwnership(",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.spec)
        code = re.sub(r"/\*.*?\*/", "", self.spec, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        assigned = re.findall(
            r"style\.(?:flexBasis|width|minWidth)\s*=|style\.setProperty\(", code
        )
        self.assertEqual(assigned, [], "the spec must not write sidebar sizing itself")

    def test_the_spec_switches_tabs_through_an_api_the_host_provides(self):
        self.assertIn("toggleSidebarTab", self.spec)
        # A setter by this name does not exist on the host store; relying on it
        # would leave the real call path untested.
        self.assertNotIn("setSidebarTab", self.spec)

    def test_the_promoted_probe_reaches_a_real_binding_and_product_edit_path(self):
        # A prior smoke accepted an ordinary widget after manufacturing source fields.
        # Keep its replacement isolated in this real-host spec, and require the
        # host graph/edit/prompt operations that the mocked harness cannot prove.
        for operation in (
            "app.loadGraphData(fixture",
            "graph.convertToSubgraph(",
            "input.connect(boundSlot, inner)",
            "ParameterLabTab.applyOverrides(",
            "app.graphToPrompt()",
            "app.loadGraphData(previous",
            "finally {",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, self.spec)
        self.assertNotIn("sourceNodeId: String(node.id)", self.spec)
        parameter_lab = (REPO_ROOT / "web" / "tabs" / "parameter_lab_tab.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("export const ParameterLabTab", parameter_lab)
        self.assertIn("applyOverrides(run)", parameter_lab)

    def test_the_peer_fixture_registers_the_way_the_product_does(self):
        peer = (PEER_FIXTURE_SOURCE / "web" / "peer_sidebar_tab.js").read_text(
            encoding="utf-8"
        )
        helper = (REPO_ROOT / "web" / "openclaw_sidebar_registration.js").read_text(
            encoding="utf-8"
        )

        for source in (peer, helper):
            self.assertIn("sidebarTab?.registerSidebarTab", source)
        # A missing API must throw, not optional-chain into a silent no-op.
        self.assertIn("no sidebar registration API", peer)


class TestTheWorkflowMenuAndWarningCasesDependOnProductSurfaces(unittest.TestCase):
    """The three cases that read product text must read text the product still writes.

    A case that waits for a message the product no longer shows would fail on every
    host, and an audit keyed to a stale extension name would pass on every host. Each
    string, selector and name the cases rely on is therefore checked against the
    product source here, where a drift is visible without a host.
    """

    def setUp(self):
        self.spec = SPEC_PATH.read_text(encoding="utf-8")
        self.helpers = (
            REPO_ROOT / "tests" / "real_host" / "helpers" / "real_host_subjects.js"
        ).read_text(encoding="utf-8")
        self.parameter_lab = (
            REPO_ROOT / "web" / "tabs" / "parameter_lab_tab.js"
        ).read_text(encoding="utf-8")
        self.toolbox = (
            REPO_ROOT / "web" / "extensions" / "context_toolbox.js"
        ).read_text(encoding="utf-8")

    def _js_list(self, name: str) -> list:
        block = re.search(
            rf"export const {name} = Object\.freeze\(\[(.*?)\]\);", self.helpers, re.S
        )
        self.assertIsNotNone(block, f"{name} is not exported as a frozen list")
        return re.findall(r'"([^"]+)"', block.group(1))

    def test_the_audited_extension_names_are_exactly_the_registered_ones(self):
        registered = set()
        for path in (REPO_ROOT / "web").rglob("*.js"):
            text = path.read_text(encoding="utf-8")
            registered.update(
                re.findall(r'registerExtension\(\{\s*name:\s*["\']([^"\']+)["\']', text)
            )

        self.assertTrue(registered, "the product registers no extension to audit")
        self.assertEqual(set(self._js_list("OPENCLAW_EXTENSION_NAMES")), registered)

    def test_the_expected_menu_entries_are_the_ones_the_toolbox_adds(self):
        added = re.findall(r'content:\s*"[^"]*?(OpenClaw: [^"]+)"', self.toolbox)

        self.assertEqual(
            sorted(self._js_list("OPENCLAW_NODE_MENU_ENTRIES")), sorted(added)
        )

    def test_the_binding_case_reads_messages_and_controls_the_product_provides(self):
        for text in (
            "The workflow changed. Return to the original workflow or rebuild the plan.",
            "Values applied to nodes.",
            '"Workflow changed"',
            "openclaw-lab-run-list",
            "replay-run",
            "lab-run-all",
            "lab-generate",
            "lab-add-dim",
            "dim-node-select",
            "dim-widget-select",
            "dim-manual-input",
        ):
            with self.subTest(text=text):
                self.assertIn(text, self.parameter_lab)
        banner = (REPO_ROOT / "web" / "openclaw_banner_manager.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("dataset.severity", banner)
        self.assertIn('.openclaw-banner[data-severity="error"]', self.spec)

    def test_the_declared_console_error_is_the_one_a_refused_run_writes(self):
        declared = re.search(r"const REFUSED_RUN_CONSOLE_ERROR = '([^']+)';", self.spec)

        self.assertIsNotNone(declared)
        self.assertIn(f'console.error("{declared.group(1)}"', self.parameter_lab)

    def test_the_warning_audit_control_does_not_name_an_extension(self):
        probe = re.search(r"const WARNING_COLLECTOR_PROBE = '([^']+)';", self.spec)

        self.assertIsNotNone(probe)
        for name in self._js_list("OPENCLAW_EXTENSION_NAMES"):
            self.assertNotIn(name, probe.group(1))
        self.assertNotIn("deprecated_extension_graph_patch", probe.group(1))

    def test_no_case_in_the_spec_is_skipped_or_isolated(self):
        code = re.sub(r"/\*.*?\*/", "", self.spec, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

        hits = re.findall(r"\btest\.(?:skip|fixme|only|fail|slow)\b|\.skip\(", code)
        self.assertEqual(hits, [], "a case may not be filtered to obtain a passing run")


class TestTheCopyStepExcludesWhatItClaims(unittest.TestCase):
    def test_the_repository_is_copied_with_exclusions_and_never_symlinked(self):
        source = (REPO_ROOT / "scripts" / "real_host_smoke.py").read_text(
            encoding="utf-8"
        )

        # A symlink of the repository root would place the git directory, the
        # virtualenv and the read-only reference checkout inside a live host.
        hits = [line.strip() for line in source.splitlines() if "symlink_to" in line]
        self.assertEqual(hits, [], f"repository must be copied, not symlinked: {hits}")
        self.assertIn("shutil.copytree", source)
        for excluded in ("reference", ".git", ".venv", ".planning"):
            self.assertIn(f'"{excluded}"', source)

    def test_this_repository_declares_dependencies_the_host_does_not_provide(self):
        own = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        source = (REPO_ROOT / "scripts" / "real_host_smoke.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("cryptography", own)
        # Installing only the host requirements would run the product in a
        # weaker environment than any real install.
        self.assertIn('REPO_ROOT / "requirements.txt"', source)

    def test_an_absolute_or_drive_relative_archive_member_is_refused(self):
        import hashlib
        import tempfile
        import zipfile as zf

        for member in ("/etc/passwd", "C:evil.txt", "C:\\Windows\\evil.txt"):
            with self.subTest(member=member):
                directory = Path(tempfile.mkdtemp())
                archive = directory / "dist.zip"
                buffer = io.BytesIO()
                with zf.ZipFile(buffer, "w") as bundle:
                    bundle.writestr(member, b"nope")
                archive.write_bytes(buffer.getvalue())

                release = dict(resolve_subject(POLICY, "standalone_release"))
                release["release_asset_sha256"] = hashlib.sha256(
                    archive.read_bytes()
                ).hexdigest()
                with self.assertRaises(SmokeError) as caught:
                    verify_and_extract_release_asset(
                        archive, release, directory / "out"
                    )
                self.assertIn("member", str(caught.exception))


class TestTeardownReleasesWhatItOpened(unittest.TestCase):
    def test_the_host_log_handle_is_closed_by_teardown(self):
        class FakeProcess:
            returncode = 0

            def poll(self):
                return 0

        handle = (Path(tempfile.mkdtemp()) / "host.log").open("wb")
        self.addCleanup(lambda: handle.closed or handle.close())

        stop_host(FakeProcess(), 1.0, handle)

        self.assertTrue(handle.closed)

    def test_teardown_closes_the_log_even_when_the_host_will_not_stop(self):
        class StubbornProcess:
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                return None

            def kill(self):
                self.returncode = -9

            def wait(self, timeout=None):
                if self.returncode is None:
                    raise subprocess.TimeoutExpired("host", timeout)
                return self.returncode

        handle = (Path(tempfile.mkdtemp()) / "host.log").open("wb")
        self.addCleanup(lambda: handle.closed or handle.close())

        stop_host(StubbornProcess(), 0.01, handle)

        self.assertTrue(handle.closed)


class TestTheFetchBudgetBoundsThePhase(unittest.TestCase):
    """A deadline that each call may spend in full does not bound the phase."""

    def test_the_budget_is_spent_down_across_the_git_calls(self):
        ticks = iter([0.0, 0.0, 10.0, 20.0, 30.0])
        handed = []

        def fake_run(command, *, cwd=None, timeout):
            handed.append(timeout)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            unittest.mock.patch("scripts.real_host_smoke._run", side_effect=fake_run),
            unittest.mock.patch(
                "scripts.real_host_smoke.verify_core_checkout", return_value="head"
            ),
        ):
            fetch_core(
                POLICY,
                HostPaths(workspace=Path(tempfile.mkdtemp())),
                100.0,
                clock=lambda: next(ticks),
            )

        self.assertEqual(handed, [100.0, 90.0, 80.0, 70.0])
        self.assertTrue(
            all(later <= earlier for earlier, later in zip(handed, handed[1:])),
            f"each call must receive what is left, got {handed}",
        )

    def test_an_exhausted_budget_stops_the_phase(self):
        ticks = iter([0.0, 0.0, 500.0])

        def fake_run(command, *, cwd=None, timeout):
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            unittest.mock.patch("scripts.real_host_smoke._run", side_effect=fake_run),
            self.assertRaises(SmokeError) as caught,
        ):
            fetch_core(
                POLICY,
                HostPaths(workspace=Path(tempfile.mkdtemp())),
                100.0,
                clock=lambda: next(ticks),
            )

        self.assertIn("exceeded its 100.0s budget", str(caught.exception))


class TestTheHostCanActuallyLoadTheProduct(unittest.TestCase):
    """A lane whose own startup argument makes the product refuse to load is useless."""

    def _start_and_capture_env(self):
        captured = {}

        class FakePopen:
            def __init__(self, command, **kwargs):
                captured["command"] = command
                captured["env"] = kwargs.get("env")

        with unittest.mock.patch("scripts.real_host_smoke.subprocess.Popen", FakePopen):
            workspace = Path(tempfile.mkdtemp())
            _process, handle = start_host(
                POLICY, resolve_subject(POLICY, "bundled"), HostPaths(workspace), 18188
            )
            handle.close()
        return captured

    def test_the_lane_leaves_the_host_on_its_default_loopback_bind(self):
        # This replaced a test asserting the opposite: that the lane passes
        # --listen and authenticates the host with a generated admin token. That
        # test pinned a workaround, not a product invariant, and it passed
        # continuously while the workaround made every browser assertion in the
        # lane unreachable - workflow run 34109004949 failed 7 of 9 tests in both
        # subjects with 403s the runner's own suite could not see.
        #
        # The guarantee the old test was really protecting is the third assertion
        # here: whatever this lane does, the host must still load OpenClaw. Without
        # --listen the host is not classified as exposed, so the product's own
        # startup gate is satisfied with no token at all and loopback resolves to
        # ADMIN - the
        # posture of a default operator install, which is what this lane measures.
        captured = self._start_and_capture_env()

        self.assertNotIn("--listen", captured["command"])
        self.assertNotIn("OPENCLAW_ADMIN_TOKEN", captured["env"])
        self.assertNotIn("MOLTBOT_ADMIN_TOKEN", captured["env"])

    def test_the_lane_never_configures_an_admin_token(self):
        # Guarded at the source as well as in the environment, the same way the
        # bind override is, because restoring the assignment is the single easiest
        # way to break this lane back to where it was and the failure would not
        # surface until the next scheduled run.
        source = Path("scripts/real_host_smoke.py").read_text(encoding="utf-8")
        assignments = [
            line.strip()
            for line in source.splitlines()
            if "ADMIN_TOKEN" in line and "=" in line.split("#", 1)[0]
        ]

        # Reported as the offending lines rather than by dumping the file, which
        # a plain substring assertion on a 700-line module does.
        self.assertEqual(assignments, [], f"admin token assigned at: {assignments}")
        self.assertNotIn("secrets.token", source)

    def test_the_lane_never_disables_the_products_own_bind_check(self):
        # The gate offers an override. A compatibility lane that used it would be
        # validating the product with the product's safety check switched off.
        captured = self._start_and_capture_env()

        self.assertNotIn(DANGEROUS_BIND_OVERRIDE_ENV, captured["env"])
        source = Path("scripts/real_host_smoke.py").read_text(encoding="utf-8")
        self.assertNotIn(f'{DANGEROUS_BIND_OVERRIDE_ENV}"] = ', source)
        self.assertNotIn("DANGEROUS_BIND_OVERRIDE_ENV] = ", source)


if __name__ == "__main__":
    unittest.main()


class TheLaneKnowsWhichNoiseTheHostOwns(unittest.TestCase):
    """The lane may excuse the host's own startup noise, but only on the record.

    Measured on an isolated host running this product and the peer fixture alone,
    a stock ComfyUI still produces four 404s for optional files it asks about and
    one frontend log line about its own initialization. The browser's console text
    for those 404s carries no URL, so before this block existed they were
    permanently unattributable and permanently charged to this product.
    """

    def setUp(self) -> None:
        with open(POLICY_PATH, encoding="utf-8") as handle:
            self.policy = json.load(handle)
        self.noise = self.policy.get("host_owned_noise")

    def test_the_excusable_set_is_pinned_in_the_policy(self) -> None:
        self.assertIsInstance(
            self.noise,
            dict,
            "host_owned_noise must live in the policy, not hardcoded in the spec, so "
            "that widening it is a reviewable change to a tracked file.",
        )
        self.assertTrue(self.noise.get("requests"))
        self.assertTrue(self.noise.get("console_messages"))

    def test_every_excused_item_says_why_it_is_the_hosts(self) -> None:
        for entry in self.noise["requests"]:
            self.assertTrue(
                str(entry.get("reason", "")).strip(),
                f"pinned request {entry.get('path')!r} carries no reason; an "
                "unexplained exclusion is how an allowlist rots",
            )
            self.assertTrue(str(entry.get("path", "")).startswith("/"))
        for entry in self.noise["console_messages"]:
            self.assertTrue(str(entry.get("reason", "")).strip())
            self.assertTrue(str(entry.get("text", "")).strip())

    def test_no_excused_request_belongs_to_this_product(self) -> None:
        for entry in self.noise["requests"]:
            path = str(entry["path"])
            self.assertFalse(
                path.startswith("/extensions/"),
                f"pinned request {path!r} sits under /extensions, so it could excuse a "
                "failure this product owns. Host-owned entries are core routes only.",
            )
            self.assertNotIn("openclaw", path.lower())

    def test_the_measurement_behind_the_pins_is_recorded(self) -> None:
        measured = str(self.noise.get("measured_on", ""))
        self.assertIn(
            "0.34.0",
            measured,
            "the policy must say which host version these entries were measured on, "
            "so a future reader can tell whether they still apply",
        )
