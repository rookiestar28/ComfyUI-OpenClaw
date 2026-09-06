from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

OPENAPI_SYNC_TRIGGER_PATHS = {
    "docs/openapi.yaml",
    "docs/release/api_contract.md",
    "scripts/generate_openapi_spec.py",
    "services/openapi_generation.py",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def _get_staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [
        _normalize_path(line)
        for line in result.stdout.splitlines()
        if _normalize_path(line)
    ]


def should_validate_openapi(paths: Iterable[str]) -> bool:
    normalized = {_normalize_path(path) for path in paths if _normalize_path(path)}
    if not normalized:
        return False
    return any(path in OPENAPI_SYNC_TRIGGER_PATHS for path in normalized)


def _load_generate_openapi_yaml() -> Callable[[str | Path | None], str]:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from services.openapi_generation import generate_openapi_yaml

    return generate_openapi_yaml


def validate_openapi_sync(
    *,
    openapi_path: str | Path | None = None,
    contract_path: str | Path | None = None,
    generate_openapi_yaml: Callable[[str | Path | None], str] | None = None,
) -> tuple[bool, str]:
    root = _repo_root()
    openapi_file = (
        Path(openapi_path) if openapi_path else root / "docs" / "openapi.yaml"
    )
    contract_file = (
        Path(contract_path)
        if contract_path
        else root / "docs" / "release" / "api_contract.md"
    )
    if not openapi_file.exists():
        return False, f"[OpenClaw] OpenAPI sync check failed: missing {openapi_file}"

    generator = generate_openapi_yaml or _load_generate_openapi_yaml()
    expected = generator(contract_file)
    actual = openapi_file.read_text(encoding="utf-8")
    if actual == expected:
        return True, ""

    message = "\n".join(
        [
            "[OpenClaw] Commit/push blocked: docs/openapi.yaml is out of sync.",
            "",
            "Generated specs must not be edited by hand without regenerating them.",
            "",
            "Fix:",
            "  1. Update generator/contract inputs as needed.",
            "  2. Regenerate with: python scripts/generate_openapi_spec.py",
            "  3. Review and stage docs/openapi.yaml together with the source change.",
        ]
    )
    return False, message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guard that docs/openapi.yaml stays in sync with the generator."
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Only validate when staged changes touch OpenAPI generator/spec sources.",
    )
    args = parser.parse_args(argv)

    if args.staged and not should_validate_openapi(_get_staged_paths()):
        return 0

    ok, message = validate_openapi_sync()
    if ok:
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
