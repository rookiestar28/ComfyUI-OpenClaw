#!/usr/bin/env python3
"""
R100: Generate Release Provenance
Generates a JSON manifest containing:
1. SHA256 checksums of all artifacts in the target directory.
2. Git commit hash (provenance).
3. Build timestamp.
4. SBOM (pip list).

Usage:
  python scripts/generate_provenance.py <artifact_dir> <output_json>
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from typing import Any


def compute_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_git_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"])
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "unknown"


def get_sbom() -> Any:
    try:
        # Simple SBOM: pip list
        output = subprocess.check_output(["pip", "list", "--format=json"]).decode(
            "utf-8"
        )
        return json.loads(output)
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="Generate Release Provenance")
    parser.add_argument("artifact_dir", help="Directory containing release artifacts")
    parser.add_argument("output_json", help="Path to write provenance.json")
    args = parser.parse_args()

    artifacts = {}
    if os.path.isdir(args.artifact_dir):
        for root, _, files in os.walk(args.artifact_dir):
            for file in files:
                if file == "provenance.json":
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, args.artifact_dir).replace(
                    "\\", "/"
                )
                artifacts[rel_path] = compute_sha256(full_path)

    provenance = {
        "meta": {
            "timestamp": time.time(),
            "git_commit": get_git_commit(),
            "generator": "scripts/generate_provenance.py",
        },
        "artifacts": artifacts,
        "sbom": get_sbom(),
    }

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2, sort_keys=True)

    print(f"Provenance generated at {args.output_json}")


if __name__ == "__main__":
    main()
