#!/usr/bin/env python3
"""Bootstrap and run the pinned real-host frontend compatibility smoke lane.

The lane starts a real ComfyUI host at an exact pinned commit, installs this
repository into it as a scoped custom node, serves one of two explicitly pinned
frontend subjects, and hands the running host to Playwright. It exists to detect
host frontend drift that the mocked harness cannot see.

Four properties matter more than convenience and are enforced here rather than
described:

* The host is fetched into caller-supplied temporary space. Nothing is written
  to, read from, or executed out of a reference checkout.
* The standalone release subject refuses to run until its release asset digest is
  pinned from an authorized download. An unverified artifact must never be able
  to produce release evidence.
* Every external phase carries a finite deadline, and the host is always torn
  down, including when a phase fails.
* Compatibility evidence is only ever written from a completed run that carries a
  run identifier. Nothing in this module can move the matrix on its own.

Importing this module is inert. Running it performs network and process actions,
so it refuses to start unless an authorized caller says so explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "tests" / "real_host_smoke_policy.json"
PLAYWRIGHT_CONFIG = "playwright.real-host.config.js"
CUSTOM_NODE_DIR_NAME = "comfyui-openclaw"
PEER_FIXTURE_SOURCE = (
    REPO_ROOT / "tests" / "real_host" / "fixtures" / "openclaw_smoke_peer"
)
PEER_FIXTURE_DIR_NAME = "openclaw-smoke-peer"
AUTHORIZATION_ENV = "OPENCLAW_REAL_HOST_SMOKE_AUTHORIZED"
SHA256_HEX_LENGTH = 64
READINESS_POLL_SECONDS = 2.0
COPY_EXCLUSIONS = (
    ".git",
    ".venv",
    "node_modules",
    ".tmp",
    "reference",
    "REFERENCE",
    ".planning",
)


class SmokeError(RuntimeError):
    """The lane cannot run safely with the given inputs."""


@dataclass(frozen=True)
class HostPaths:
    """Where one lane run keeps the host it created."""

    workspace: Path

    @property
    def core(self) -> Path:
        return self.workspace / "ComfyUI"

    @property
    def custom_nodes(self) -> Path:
        return self.core / "custom_nodes"

    @property
    def log_file(self) -> Path:
        return self.workspace / "host.log"

    @property
    def artifact_dir(self) -> Path:
        return self.workspace / "artifacts"

    def web_root_for(self, subject: dict[str, Any]) -> Path | None:
        relative = subject.get("web_root_relative")
        return None if relative is None else self.core / relative


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SmokeError("policy document must be an object")
    policy: dict[str, Any] = parsed
    for field in ("core", "subjects", "runtime", "deadlines_seconds", "evidence"):
        if not isinstance(policy.get(field), dict):
            raise SmokeError(f"policy field {field} must be an object")
    return policy


def resolve_subject(policy: dict[str, Any], subject_id: str) -> dict[str, Any]:
    subject: object = policy["subjects"].get(subject_id)
    if not isinstance(subject, dict):
        known = ", ".join(sorted(policy["subjects"]))
        raise SmokeError(
            f"unknown frontend subject {subject_id}; known subjects: {known}"
        )
    resolved: dict[str, Any] = subject
    return resolved


def assert_subject_runnable(subject: dict[str, Any]) -> None:
    """Refuse a release subject whose artifact digest has not been pinned.

    The tracked policy ships this digest unset on purpose. Pinning it requires
    downloading the release asset, which is an outward-facing fetch this
    repository does not perform without authorization, and a guessed value would
    read as verification while proving nothing.
    """
    if not subject.get("release_asset_name"):
        return
    digest = subject.get("release_asset_sha256")
    if not isinstance(digest, str) or len(digest) != SHA256_HEX_LENGTH:
        raise SmokeError(
            f"subject {subject['id']} names release asset {subject['release_asset_name']} but no "
            "pinned sha256; pin the digest from an authorized download before running this subject"
        )
    if digest != digest.lower() or any(
        char not in "0123456789abcdef" for char in digest
    ):
        raise SmokeError(
            f"subject {subject['id']} sha256 must be lowercase hexadecimal"
        )


def build_host_args(
    policy: dict[str, Any], subject: dict[str, Any], port: int
) -> list[str]:
    """Assemble the host argv, asserting exposure rather than assuming a default.

    The host treats a bare listen flag as every interface, so the bind address is
    always stated explicitly instead of inherited from an upstream default that
    could change under the pin.
    """
    runtime = policy["runtime"]
    bind_host = runtime["bind_host"]
    if bind_host not in runtime["allowed_bind_hosts"]:
        raise SmokeError(f"bind host {bind_host} is not a loopback address")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise SmokeError(f"port must be an unprivileged integer port, got {port!r}")

    args = [*runtime["required_args"], "--port", str(port), "--listen", bind_host]
    front_end_arg = subject.get("front_end_version_arg")
    if front_end_arg:
        args += ["--front-end-version", front_end_arg]
    return args


def _run(
    command: list[str], *, cwd: Path | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def fetch_core(policy: dict[str, Any], paths: HostPaths, timeout: float) -> str:
    """Fetch exactly the pinned host commit into the run's own workspace.

    A single-commit fetch is used rather than a branch clone so the resolved head
    cannot drift with upstream, and the result is verified before anything else
    touches it.
    """
    core = policy["core"]
    expected_head = core["source_head"]
    paths.core.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--quiet", str(paths.core)], timeout=timeout)
    _run(
        ["git", "-C", str(paths.core), "remote", "add", "origin", core["repository"]],
        timeout=timeout,
    )
    _run(
        [
            "git",
            "-C",
            str(paths.core),
            "fetch",
            "--depth",
            "1",
            "origin",
            expected_head,
        ],
        timeout=timeout,
    )
    _run(
        ["git", "-C", str(paths.core), "checkout", "--quiet", expected_head],
        timeout=timeout,
    )
    return verify_core_checkout(paths.core, expected_head)


def verify_core_checkout(core_dir: Path, expected_head: str) -> str:
    """Confirm the host source is exactly the pinned commit before anything runs."""
    result = _run(["git", "-C", str(core_dir), "rev-parse", "HEAD"], timeout=60)
    head = result.stdout.strip()
    if head != expected_head:
        raise SmokeError(
            f"host source resolved to {head}, expected the pinned {expected_head}"
        )
    return head


def install_dependencies(paths: HostPaths, timeout: float) -> None:
    """Install the host's own declared requirements, and nothing beyond them."""
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(paths.core / "requirements.txt"),
        ],
        timeout=timeout,
    )


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_extract_release_asset(
    archive: Path, subject: dict[str, Any], destination: Path
) -> str:
    """Verify a downloaded release archive against its pin, then extract it.

    The host downloads this archive into an anonymous temporary file and discards
    it, so no artifact survives for a later hash. Verifying here, before the host
    ever sees the bytes, is the only point at which the digest can gate anything.
    Members are checked for path escape before extraction because the archive is
    third-party input.
    """
    assert_subject_runnable(subject)
    actual = digest_file(archive)
    expected = subject["release_asset_sha256"]
    if actual != expected:
        raise SmokeError(
            f"release asset digest mismatch for {subject['id']}: got {actual}, expected {expected}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            resolved = (destination / member).resolve()
            if resolved != root and root not in resolved.parents:
                raise SmokeError(
                    f"release archive member escapes its destination: {member}"
                )
        bundle.extractall(destination)
    return actual


def prepare_frontend_subject(
    subject: dict[str, Any], paths: HostPaths, asset_path: Path | None
) -> Path | None:
    """Seed the requested frontend so host start performs no download.

    Pre-seeding the versioned directory is what makes the digest meaningful: the
    host reuses an existing copy for a tagged version string, so it resolves the
    subject through its own frontend manager while the bytes are ones this lane
    verified. It also removes network flakiness from host start.
    """
    web_root = paths.web_root_for(subject)
    if web_root is None:
        return None
    if asset_path is None:
        raise SmokeError(
            f"subject {subject['id']} needs release asset {subject['release_asset_name']}; "
            "supply it with --release-asset from an authorized download"
        )
    verify_and_extract_release_asset(asset_path, subject, web_root)
    return web_root


def install_custom_nodes(paths: HostPaths) -> list[Path]:
    """Place this repository and the peer fixture beside each other in the host.

    The peer fixture exists so the lane can observe a custom-to-custom sidebar
    mount handover. A stock host offers only one custom tab, and a reference
    checkout may not be executed, so the second tab has to be one this repository
    owns.
    """
    paths.custom_nodes.mkdir(parents=True, exist_ok=True)
    installed = []
    for source, name in (
        (REPO_ROOT, CUSTOM_NODE_DIR_NAME),
        (PEER_FIXTURE_SOURCE, PEER_FIXTURE_DIR_NAME),
    ):
        target = paths.custom_nodes / name
        if target.exists() or target.is_symlink():
            raise SmokeError(f"custom node destination already exists: {target}")
        try:
            target.symlink_to(source, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Runners without symlink permission still work; the host only reads
            # these trees, so a filtered copy is equivalent.
            shutil.copytree(
                source, target, ignore=shutil.ignore_patterns(*COPY_EXCLUSIONS)
            )
        installed.append(target)
    return installed


def readiness_url(policy: dict[str, Any], port: int) -> str:
    runtime = policy["runtime"]
    host = runtime["bind_host"]
    bracketed = f"[{host}]" if ":" in host else host
    return f"http://{bracketed}:{port}{runtime['health_path']}"


def wait_for_host(
    url: str,
    deadline_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll the health route until it answers, with a hard deadline.

    The wait is bounded and polls an explicit readiness signal; it never sleeps a
    fixed guess and never retries forever, so a host that fails to start fails the
    lane instead of hanging it.
    """
    started = clock()
    last_error: Exception | None = None
    while clock() - started < deadline_seconds:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
                last_error = SmokeError(f"health route answered {response.status}")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
        sleep(READINESS_POLL_SECONDS)
    raise SmokeError(
        f"host did not become ready at {url} within {deadline_seconds}s: {last_error}"
    )


def start_host(
    policy: dict[str, Any], subject: dict[str, Any], paths: HostPaths, port: int
) -> subprocess.Popen[bytes]:
    """Launch the host with its log captured to a file the lane can read back.

    The log is not decoration: the fallback sentence the host prints when it
    cannot serve a requested frontend is one of the three signals that decides
    whether the release subject actually ran.
    """
    args = build_host_args(policy, subject, port)
    paths.artifact_dir.mkdir(parents=True, exist_ok=True)
    handle = paths.log_file.open("wb")
    return subprocess.Popen(
        [sys.executable, "main.py", *args],
        cwd=str(paths.core),
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def stop_host(process: subprocess.Popen[bytes], deadline_seconds: float) -> int | None:
    """Terminate the host, escalating to a kill so teardown is also bounded."""
    if process.poll() is not None:
        return process.returncode
    process.terminate()
    try:
        return process.wait(timeout=deadline_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=deadline_seconds)


def run_playwright(
    policy: dict[str, Any], subject: dict[str, Any], paths: HostPaths, port: int
) -> int:
    """Hand the running host to the real-host spec, isolated from the mocked lane."""
    env = dict(os.environ)
    env["OPENCLAW_REAL_HOST_BASE_URL"] = (
        f"http://{policy['runtime']['bind_host']}:{port}"
    )
    env["OPENCLAW_REAL_HOST_SUBJECT"] = subject["id"]
    env["OPENCLAW_REAL_HOST_LOG"] = str(paths.log_file)
    env["OPENCLAW_REAL_HOST_WEB_ROOT"] = str(paths.web_root_for(subject) or "")
    completed = subprocess.run(
        ["npx", "playwright", "test", "--config", PLAYWRIGHT_CONFIG],
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
        timeout=policy["deadlines_seconds"]["smoke"],
    )
    return completed.returncode


def evidence_update_is_allowed(
    policy: dict[str, Any], state: str, run_id: str | None, evidence_id: str | None
) -> list[str]:
    """Mirror the tracked matrix rule at the point this lane would write.

    The matrix already rejects a validated state with no run identifier and a
    pending state that names one. Checking the same rule here means a lane bug
    cannot produce a document that the governance check then has to catch.
    """
    failures: list[str] = []
    requires_run = state in policy["evidence"]["states_requiring_run_id"]
    has_run = isinstance(run_id, str) and run_id.strip() != ""
    has_evidence_id = isinstance(evidence_id, str) and evidence_id.strip() != ""

    if requires_run and not has_run:
        failures.append(f"evidence state {state} requires a run identifier")
    if not requires_run and has_run:
        failures.append(f"evidence state {state} must not name a run identifier")
    if requires_run and not has_evidence_id:
        failures.append(f"evidence state {state} requires an evidence identifier")
    if not requires_run and has_evidence_id:
        failures.append(f"evidence state {state} must not name an evidence identifier")
    return failures


def release_digest_is_pinned(policy: dict[str, Any], subject_id: str) -> bool:
    """Whether a release subject can run at all yet.

    The lane reports this rather than attempting the subject and failing, so an
    unpinned digest reads as "not verified yet" in the run summary instead of as
    a broken lane, while still never producing release evidence.
    """
    try:
        assert_subject_runnable(resolve_subject(policy, subject_id))
    except SmokeError:
        return False
    return True


def emit_pins(policy: dict[str, Any], output_path: Path | None) -> str:
    """Publish the pinned facts a workflow needs to decide what it may run."""
    lines = [
        f"core_head={policy['core']['source_head']}",
        f"bundled_frontend={policy['subjects']['bundled']['frontend_version']}",
        f"release_frontend={policy['subjects']['standalone_release']['frontend_version']}",
        "release_digest_pinned="
        + (
            "true"
            if release_digest_is_pinned(policy, "standalone_release")
            else "false"
        ),
    ]
    rendered = "\n".join(lines) + "\n"
    if output_path is not None:
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    return rendered


def preflight(policy: dict[str, Any], subject_id: str, port: int) -> dict[str, Any]:
    """Everything that can be checked before any external action is taken."""
    subject = resolve_subject(policy, subject_id)
    assert_subject_runnable(subject)
    build_host_args(policy, subject, port)
    if not PEER_FIXTURE_SOURCE.is_dir():
        raise SmokeError(f"peer fixture node is missing at {PEER_FIXTURE_SOURCE}")
    if not (REPO_ROOT / PLAYWRIGHT_CONFIG).is_file():
        raise SmokeError(
            f"real-host Playwright config is missing at {PLAYWRIGHT_CONFIG}"
        )
    return subject


def run_lane(
    policy: dict[str, Any],
    subject_id: str,
    port: int,
    workspace: Path,
    asset_path: Path | None,
) -> int:
    """Run one subject end to end, tearing the host down on every path."""
    subject = preflight(policy, subject_id, port)
    deadlines = policy["deadlines_seconds"]
    paths = HostPaths(workspace=workspace)

    resolved_head = fetch_core(policy, paths, deadlines["install"])
    print(f"REAL-HOST-SMOKE: core resolved to {resolved_head}")
    install_dependencies(paths, deadlines["install"])
    web_root = prepare_frontend_subject(subject, paths, asset_path)
    if web_root is not None:
        print(f"REAL-HOST-SMOKE: verified frontend release seeded at {web_root}")
    install_custom_nodes(paths)

    process = start_host(policy, subject, paths, port)
    try:
        wait_for_host(readiness_url(policy, port), deadlines["startup"])
        return run_playwright(policy, subject, paths, port)
    finally:
        stop_host(process, deadlines["teardown"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", help="Frontend subject id to exercise.")
    parser.add_argument(
        "--emit-pins",
        action="store_true",
        help="Print the pinned lane facts and exit without any external action.",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Append the pinned facts to this workflow output file.",
    )
    parser.add_argument("--port", type=int, default=18188)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Temporary directory for the host. Never a reference checkout.",
    )
    parser.add_argument(
        "--release-asset",
        type=Path,
        default=None,
        help="Locally downloaded release archive, verified against the pinned digest.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate inputs and exit without any network or process action.",
    )
    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
    except (SmokeError, OSError, json.JSONDecodeError) as exc:
        print(f"REAL-HOST-SMOKE-FAIL: {exc}")
        return 1

    if args.emit_pins:
        try:
            print(emit_pins(policy, args.github_output), end="")
        except (SmokeError, OSError, KeyError) as exc:
            print(f"REAL-HOST-SMOKE-FAIL: {exc}")
            return 1
        return 0

    if not args.subject:
        print("REAL-HOST-SMOKE-FAIL: --subject is required unless --emit-pins is given")
        return 1

    try:
        subject = preflight(policy, args.subject, args.port)
    except (SmokeError, OSError, json.JSONDecodeError) as exc:
        print(f"REAL-HOST-SMOKE-FAIL: {exc}")
        return 1

    if args.preflight_only:
        print(
            f"REAL-HOST-SMOKE-PREFLIGHT-PASS: subject={subject['id']} "
            f"frontend={subject['frontend_version']} core={policy['core']['source_head']}"
        )
        return 0

    if os.environ.get(AUTHORIZATION_ENV) != "1":
        print(
            "REAL-HOST-SMOKE-FAIL: running this lane fetches sources, installs packages and starts "
            f"a host, which requires explicit authorization; set {AUTHORIZATION_ENV}=1 only in an "
            "authorized run"
        )
        return 1

    if args.workspace is None:
        print(
            "REAL-HOST-SMOKE-FAIL: --workspace is required; the lane never runs in the repository"
        )
        return 1
    if (
        REPO_ROOT in args.workspace.resolve().parents
        or args.workspace.resolve() == REPO_ROOT
    ):
        print(
            "REAL-HOST-SMOKE-FAIL: --workspace must be outside the repository working tree"
        )
        return 1

    try:
        code = run_lane(
            policy, args.subject, args.port, args.workspace, args.release_asset
        )
    except (SmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"REAL-HOST-SMOKE-FAIL: {exc}")
        return 1

    print(
        "REAL-HOST-SMOKE-PASS"
        if code == 0
        else f"REAL-HOST-SMOKE-FAIL: spec exit {code}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
