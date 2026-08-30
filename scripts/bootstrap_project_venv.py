#!/usr/bin/env python3
"""Create, validate, and export the project-local Python used by GitHub Actions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class ProjectVenvBootstrapError(RuntimeError):
    """Raised when the project-local virtual environment cannot be trusted."""


def project_venv_python(venv_dir: Path) -> Path:
    """Return the platform-specific interpreter owned by ``venv_dir``."""

    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _validated_venv_dir(repo_root: Path) -> Path:
    configured = repo_root / ".venv"
    resolved = configured.resolve()
    if resolved.parent != repo_root or resolved.name != ".venv":
        raise ProjectVenvBootstrapError(
            f"project venv must resolve directly below repository root: {configured}"
        )
    return resolved


def _create_venv(repo_root: Path, venv_dir: Path, bootstrap_python: Path) -> None:
    if venv_dir.exists():
        return

    try:
        resolved_bootstrap = bootstrap_python.resolve(strict=True)
    except OSError as exc:
        raise ProjectVenvBootstrapError(
            f"bootstrap Python is not available: {bootstrap_python}"
        ) from exc

    try:
        # CRITICAL: keep --copies. A POSIX symlink can resolve back to toolcache Python,
        # breaking subprocess isolation even though PATH appears to point at .venv.
        subprocess.run(
            [
                str(resolved_bootstrap),
                "-m",
                "venv",
                "--copies",
                str(venv_dir),
            ],
            cwd=str(repo_root),
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProjectVenvBootstrapError(
            f"unable to create project venv at {venv_dir}"
        ) from exc


def _validate_interpreter(repo_root: Path, venv_dir: Path) -> Path:
    venv_python = project_venv_python(venv_dir)
    if not venv_python.is_file():
        raise ProjectVenvBootstrapError(
            f"existing project venv has no runnable interpreter: {venv_python}"
        )

    try:
        probe = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import pathlib,sys; print(pathlib.Path(sys.executable).resolve())",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProjectVenvBootstrapError(
            f"project venv interpreter is not runnable: {venv_python}"
        ) from exc

    output_lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    if len(output_lines) != 1:
        raise ProjectVenvBootstrapError(
            "project venv interpreter returned an invalid identity probe"
        )

    actual_python = Path(output_lines[0]).resolve()
    expected_python = venv_python.resolve()
    if actual_python != expected_python or venv_dir not in actual_python.parents:
        raise ProjectVenvBootstrapError(
            "project venv interpreter resolves outside repository-local .venv"
        )
    return expected_python


def bootstrap_project_venv(
    repo_root: Path,
    github_path_file: Path,
    *,
    bootstrap_python: Path | None = None,
) -> Path:
    """Ensure ``<repo>/.venv`` is valid and export only its executable directory."""

    resolved_root = repo_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ProjectVenvBootstrapError(
            f"repository root is not a directory: {resolved_root}"
        )

    venv_dir = _validated_venv_dir(resolved_root)
    _create_venv(
        resolved_root,
        venv_dir,
        bootstrap_python or Path(sys.executable),
    )
    venv_dir = _validated_venv_dir(resolved_root)
    executable_dir = _validate_interpreter(resolved_root, venv_dir).parent

    try:
        with github_path_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{executable_dir}\n")
    except OSError as exc:
        raise ProjectVenvBootstrapError(
            "unable to export project venv through the GitHub Actions path file"
        ) from exc

    return executable_dir


def main() -> int:
    github_path = os.environ.get("GITHUB_PATH")
    if not github_path:
        print(
            "::error::GITHUB_PATH is required for project-venv bootstrap",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    try:
        executable_dir = bootstrap_project_venv(repo_root, Path(github_path))
    except ProjectVenvBootstrapError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(f"Project-local Python ready: {executable_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
