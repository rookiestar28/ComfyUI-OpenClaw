#!/usr/bin/env python3
"""
R120: Dependency parity preflight check.

Validates the build environment before deployment or test execution.
Checks:
1. Python version (>=3.10)
2. Node.js version (shared package.json engine policy)
3. Essential Python dependencies (cryptography, defusedxml)

Usage:
    python scripts/preflight_check.py [--strict]
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Minimum requirements
MIN_PYTHON_VERSION = (3, 10)
NODE_RUNTIME_GUARD = Path(__file__).resolve().with_name("check-node-runtime.mjs")

REQUIRED_PYTHON_PACKAGES = [
    ("cryptography", "41.0"),
    ("defusedxml", "0.7.1"),
]

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def log_ok(msg: str):
    print(f"{GREEN}[OK]{RESET} {msg}")


def log_fail(msg: str):
    print(f"{RED}[FAIL]{RESET} {msg}")


def log_warn(msg: str):
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def check_python_version() -> bool:
    """Validate Python interpreter version."""
    current = sys.version_info[:3]
    if current < MIN_PYTHON_VERSION:
        log_fail(
            f"Python version {sys.version} is too old. Required: >={'.'.join(map(str, MIN_PYTHON_VERSION))}"
        )
        return False
    log_ok(f"Python version: {'.'.join(map(str, current))}")
    return True


def check_node_version() -> bool:
    """Delegate to the dependency-free guard used by npm and both full scripts."""
    try:
        output = (
            subprocess.check_output(
                ["node", str(NODE_RUNTIME_GUARD)],
                stderr=subprocess.STDOUT,
                timeout=10,
            )
            .decode("utf-8")
            .strip()
        )
        log_ok(output)
        return True
    except FileNotFoundError:
        log_fail(
            "Node.js not found in PATH. Select a stable Node 24 runtime, then retry."
        )
        return False
    except subprocess.CalledProcessError as exc:
        log_fail(exc.output.decode("utf-8", errors="replace").strip())
        return False
    except Exception as e:
        log_warn(f"Failed to check Node version: {e}")
        return False


def check_python_packages() -> bool:
    """Validate installed Python packages."""
    all_ok = True
    try:
        from importlib.metadata import PackageNotFoundError, version

        for pkg, min_ver in REQUIRED_PYTHON_PACKAGES:
            try:
                installed_ver = version(pkg)
                # Simple version compare (not fully semver compliant but enough for preflight)
                # Using pkg_resources or packaging.version is better but might add deps.
                # We'll just split by dot.

                # normalize versions
                def parse_ver(v_str):
                    return tuple(map(int, v_str.split(".")[:3]))

                if parse_ver(installed_ver) < parse_ver(min_ver):
                    log_fail(f"Package '{pkg}' version {installed_ver} < {min_ver}")
                    all_ok = False
                else:
                    log_ok(f"Package '{pkg}': {installed_ver} (>= {min_ver})")
            except PackageNotFoundError:
                log_fail(f"Package '{pkg}' not installed.")
                all_ok = False
            except ValueError:
                # Fallback for complex version strings
                log_warn(
                    f"Package '{pkg}' version {installed_ver} checked (complex format). Assuming OK."
                )

    except ImportError:
        log_warn(
            "importlib.metadata not available (Python < 3.8?). Skipping package checks."
        )

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Run dependency preflight checks.")
    parser.add_argument(
        "--strict", action="store_true", help="Fail with exit code 1 on any error"
    )
    args = parser.parse_args()

    print("Running R120 Dependency Preflight...")
    print("-" * 40)

    checks = [
        check_python_version(),
        check_node_version(),
        check_python_packages(),
    ]

    print("-" * 40)
    success = all(checks)

    if success:
        print(f"{GREEN}Preflight PASSED.{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}Preflight FAILED.{RESET}")
        if args.strict:
            sys.exit(1)
        # Non-strict mode (e.g. dev) might exit 0 or just warn
        sys.exit(1)


if __name__ == "__main__":
    main()
