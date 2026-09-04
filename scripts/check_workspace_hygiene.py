#!/usr/bin/env python3
"""Detect new bounded workspace-root artifacts without reading or deleting content."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

SCHEMA_VERSION = 1
FORBIDDEN_ROOTS = (
    "MagicMock",
    "error.log",
    "audit.log",
    "connector_state.json",
    "media",
    "moltbot_state",
)

WorkspaceSnapshot = dict[str, tuple[str, ...]]


def _entry_fingerprint(root_label: str, kind: str, relative_path: str) -> str:
    payload = f"{root_label}\0{kind}\0{relative_path}".encode()
    return hashlib.sha256(payload).hexdigest()


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    return bool(checker and checker(path))


def _walk_entry_fingerprints(path: Path, *, root_label: str) -> tuple[str, ...]:
    if not os.path.lexists(path):
        return ()

    fingerprints: set[str] = set()

    def visit(current: Path, relative_path: str) -> None:
        try:
            entry_stat = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"cannot inspect forbidden root {root_label}") from exc

        if stat.S_ISLNK(entry_stat.st_mode) or _is_junction(current):
            kind = "link"
        elif stat.S_ISDIR(entry_stat.st_mode):
            kind = "dir"
        else:
            kind = "file"
        fingerprints.add(_entry_fingerprint(root_label, kind, relative_path))

        if kind != "dir":
            return
        try:
            with os.scandir(current) as scanner:
                children = sorted(scanner, key=lambda item: item.name)
        except OSError as exc:
            raise RuntimeError(f"cannot inspect forbidden root {root_label}") from exc
        for child in children:
            child_relative = (
                child.name if relative_path == "." else f"{relative_path}/{child.name}"
            )
            visit(Path(child.path), child_relative)

    visit(path, ".")
    return tuple(sorted(fingerprints))


def capture_workspace(root: Path) -> WorkspaceSnapshot:
    root = Path(root)
    if not root.is_dir():
        raise ValueError("workspace root must be an existing directory")
    return {
        label: _walk_entry_fingerprints(root / label, root_label=label)
        for label in FORBIDDEN_ROOTS
    }


def find_new_forbidden_roots(
    root: Path, before: Mapping[str, Sequence[str]]
) -> list[str]:
    current = capture_workspace(root)
    findings: list[str] = []
    for label in FORBIDDEN_ROOTS:
        previous_entries = set(before.get(label, ()))
        if set(current[label]) - previous_entries:
            findings.append(label)
    return findings


def _validated_snapshot(payload: object) -> WorkspaceSnapshot:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported workspace hygiene snapshot schema")
    roots = payload.get("roots")
    if not isinstance(roots, dict) or set(roots) != set(FORBIDDEN_ROOTS):
        raise ValueError(
            "workspace hygiene snapshot roots do not match the bounded policy"
        )

    result: WorkspaceSnapshot = {}
    for label in FORBIDDEN_ROOTS:
        values = roots[label]
        if not isinstance(values, list) or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in values
        ):
            raise ValueError(f"invalid snapshot entry set for {label}")
        result[label] = tuple(sorted(set(values)))
    return result


def write_snapshot(path: Path, snapshot: Mapping[str, Iterable[str]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "roots": {
            label: sorted(set(snapshot.get(label, ()))) for label in FORBIDDEN_ROOTS
        },
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_snapshot(path: Path) -> WorkspaceSnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validated_snapshot(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot/check bounded workspace-root artifact hygiene."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--root", required=True)
        subparser.add_argument("--snapshot", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    snapshot_path = Path(args.snapshot)

    try:
        if args.command == "snapshot":
            write_snapshot(snapshot_path, capture_workspace(root))
            print("WORKSPACE_HYGIENE_SNAPSHOT: PASS")
            return 0

        before = read_snapshot(snapshot_path)
        findings = find_new_forbidden_roots(root, before)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"WORKSPACE_HYGIENE_ERROR: {type(exc).__name__}", file=sys.stderr)
        return 2

    if findings:
        print(
            "WORKSPACE_HYGIENE_NEW_ROOTS: " + ", ".join(findings),
            file=sys.stderr,
        )
        return 1
    print("WORKSPACE_HYGIENE_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
