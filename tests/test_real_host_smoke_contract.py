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
import re
import subprocess
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from scripts.real_host_smoke import (
    ADMIN_TOKEN_ENV,
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
        self.assertIn("release_digest_pinned=true", rendered)
        self.assertNotIn(POLICY["not_executed"]["frontend_source_head"], rendered)


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

    def test_the_spec_switches_tabs_through_an_api_the_host_provides(self):
        self.assertIn("toggleSidebarTab", self.spec)
        # A setter by this name does not exist on the host store; relying on it
        # would leave the real call path untested.
        self.assertNotIn("setSidebarTab", self.spec)

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

    def test_the_lane_authenticates_the_host_it_binds_explicitly(self):
        # services/security_gate.py treats any --listen as exposure and raises
        # rather than warns, so a host started without a token loads no OpenClaw
        # at all and every assertion in this lane would pass over an absent
        # product.
        captured = self._start_and_capture_env()

        self.assertIn("--listen", captured["command"])
        token = captured["env"][ADMIN_TOKEN_ENV]
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 16)

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
