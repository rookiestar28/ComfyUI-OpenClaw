"""Verify selected broad-exception boundaries and repository-wide ratchets.

The selected-module contract classifies broad catches at high-value boundaries.
Repository-wide pass-only and runtime-stdout contracts separately freeze two
mechanically detectable legacy patterns without pretending every historical
broad catch can already satisfy a global BLE001-style rule.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

VALID_CLASSIFICATIONS = {
    "allowed_boundary_guard",
    "needs_narrowing",
    "needs_follow_up_test_coverage",
}
VALID_COVERAGE_MODES = {"all_broad_catches", "selected_scopes"}


@dataclass(frozen=True)
class BroadCatch:
    path: str
    line: int
    scope: str
    catch_type: str


@dataclass(frozen=True)
class ScopedCall:
    path: str
    line: int
    scope: str


class _BroadCatchVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path.as_posix()
        self.scope_stack: list[str] = []
        self.catches: list[BroadCatch] = []
        self.pass_only_catches: list[BroadCatch] = []
        self.print_calls: list[ScopedCall] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        catch_type = _catch_type_name(node.type)
        if catch_type in {"bare", "Exception", "BaseException"}:
            catch = BroadCatch(
                path=self.path,
                line=node.lineno,
                scope=".".join(self.scope_stack) or "<module>",
                catch_type=catch_type,
            )
            self.catches.append(catch)
            if (
                catch_type == "Exception"
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                self.pass_only_catches.append(catch)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self.print_calls.append(
                ScopedCall(
                    path=self.path,
                    line=node.lineno,
                    scope=".".join(self.scope_stack) or "<module>",
                )
            )
        self.generic_visit(node)


def _catch_type_name(node: ast.expr | None) -> str:
    if node is None:
        return "bare"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Tuple):
        names = {_catch_type_name(item) for item in node.elts}
        if "BaseException" in names:
            return "BaseException"
        if "Exception" in names:
            return "Exception"
    return ""


def iter_broad_catches(path: Path) -> Iterable[BroadCatch]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    visitor = _BroadCatchVisitor(path)
    visitor.visit(tree)
    return tuple(visitor.catches)


def iter_pass_only_broad_catches(path: Path) -> Iterable[BroadCatch]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    visitor = _BroadCatchVisitor(path)
    visitor.visit(tree)
    return tuple(visitor.pass_only_catches)


def iter_runtime_prints(path: Path) -> Iterable[ScopedCall]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    visitor = _BroadCatchVisitor(path)
    visitor.visit(tree)
    return tuple(visitor.print_calls)


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_ratchet_contract(
    repo_root: Path,
    contract: Any,
    *,
    entries_key: str,
    label: str,
    iterator: Callable[[Path], Iterable[BroadCatch | ScopedCall]],
) -> list[str]:
    failures: list[str] = []
    if not isinstance(contract, dict) or set(contract) != {"roots", entries_key}:
        return [f"{label} must contain roots and {entries_key}"]

    roots = contract.get("roots")
    if (
        not isinstance(roots, list)
        or not roots
        or any(
            not isinstance(root, str)
            or not root
            or Path(root).is_absolute()
            or ".." in Path(root).parts
            for root in roots
        )
        or len(roots) != len(set(roots))
    ):
        return [f"{label} roots must be unique safe repository-relative paths"]

    normalized_roots = tuple(Path(root).as_posix().rstrip("/") for root in roots)
    for root in normalized_roots:
        if not (repo_root / root).is_dir():
            failures.append(f"{label} root does not exist: {root}")

    entries = contract.get(entries_key)
    if not isinstance(entries, list):
        failures.append(f"{label} {entries_key} must be a list")
        return failures

    expected: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "scope",
            "expected_count",
            "reason",
            "review_after",
        }:
            failures.append(f"{label} {entries_key}[{index}] keys must match schema")
            continue
        rel_path = entry.get("path")
        scope = entry.get("scope")
        expected_count = entry.get("expected_count")
        reason = entry.get("reason")
        review_after = entry.get("review_after")
        if not isinstance(rel_path, str):
            failures.append(f"{label} {entries_key}[{index}] path must be a string")
            continue
        path_obj = Path(rel_path)
        normalized_path = path_obj.as_posix()
        if (
            path_obj.is_absolute()
            or ".." in path_obj.parts
            or path_obj.suffix != ".py"
            or not any(
                normalized_path == root or normalized_path.startswith(root + "/")
                for root in normalized_roots
            )
        ):
            failures.append(f"{label} unsafe owned path: {rel_path}")
            continue
        if not (repo_root / path_obj).is_file():
            failures.append(f"{label} owned path does not exist: {rel_path}")
        if not isinstance(scope, str) or not scope:
            failures.append(f"{label} {rel_path}: scope must be non-empty")
            continue
        if not isinstance(expected_count, int) or expected_count < 1:
            failures.append(f"{label} {rel_path}:{scope}: expected_count must be >= 1")
            continue
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"{label} {rel_path}:{scope}: missing reason")
        try:
            if not isinstance(review_after, str):
                raise TypeError
            review_date = date.fromisoformat(review_after)
        except (TypeError, ValueError):
            failures.append(f"{label} {rel_path}:{scope}: invalid review_after")
        else:
            if review_date < date.today():
                failures.append(f"{label} {rel_path}:{scope}: review_after is expired")
        key = (normalized_path, scope)
        if key in expected:
            failures.append(f"{label} duplicate owned scope: {rel_path}:{scope}")
        expected[key] = expected_count

    actual: Counter[tuple[str, str]] = Counter()
    locations: dict[tuple[str, str], list[int]] = {}
    for root in normalized_roots:
        for path in sorted((repo_root / root).rglob("*.py")):
            rel_path = path.relative_to(repo_root).as_posix()
            try:
                calls = tuple(iterator(path))
            except (OSError, SyntaxError, UnicodeError):
                failures.append(f"{label} could not parse: {rel_path}")
                continue
            for call in calls:
                key = (rel_path, call.scope)
                actual[key] += 1
                locations.setdefault(key, []).append(call.line)

    for (rel_path, scope), count in sorted(actual.items()):
        if (rel_path, scope) not in expected:
            line = min(locations[(rel_path, scope)])
            failures.append(f"unowned {label} at {rel_path}:{line} in {scope}")
        elif expected[(rel_path, scope)] != count:
            failures.append(
                f"{label} count drift at {rel_path}:{scope}: "
                f"expected {expected[(rel_path, scope)]}, found {count}"
            )
    for (rel_path, scope), count in sorted(expected.items()):
        if (rel_path, scope) not in actual:
            failures.append(
                f"stale {label} entry at {rel_path}:{scope}: expected {count}, found 0"
            )
    return failures


def validate_exception_boundary_policy(
    repo_root: Path,
    policy: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if set(policy) != {
        "version",
        "selected_modules",
        "pass_only_contract",
        "stdout_contract",
    }:
        failures.append("policy root keys must match the version 3 schema")
    if policy.get("version") != 3:
        failures.append("policy version must equal 3")
    modules = policy.get("selected_modules")
    if not isinstance(modules, dict) or not modules:
        return ["policy selected_modules must be a non-empty object"]

    for rel_path, module_policy in sorted(modules.items()):
        rel_path_obj = Path(rel_path)
        if (
            rel_path_obj.is_absolute()
            or ".." in rel_path_obj.parts
            or rel_path_obj.suffix != ".py"
        ):
            failures.append(f"{rel_path}: unsafe selected module path")
            continue
        if not isinstance(module_policy, dict):
            failures.append(f"{rel_path}: module policy must be an object")
            continue
        path = repo_root / rel_path
        if not path.is_file():
            failures.append(f"{rel_path}: selected module does not exist")
            continue

        allowed = module_policy.get("broad_catches")
        if not isinstance(allowed, list):
            failures.append(f"{rel_path}: broad_catches must be a list")
            continue

        coverage = module_policy.get("coverage")
        if coverage not in VALID_COVERAGE_MODES:
            failures.append(f"{rel_path}: invalid coverage mode {coverage!r}")
            continue
        expected_module_keys = {"coverage", "broad_catches"}
        if coverage == "selected_scopes":
            expected_module_keys.add("selected_scopes")
        if set(module_policy) != expected_module_keys:
            failures.append(f"{rel_path}: module keys must match coverage schema")
        selected_scopes_raw = module_policy.get("selected_scopes", [])
        if coverage == "selected_scopes":
            if (
                not isinstance(selected_scopes_raw, list)
                or not selected_scopes_raw
                or any(
                    not isinstance(scope, str) or not scope
                    for scope in selected_scopes_raw
                )
                or len(selected_scopes_raw) != len(set(selected_scopes_raw))
            ):
                failures.append(
                    f"{rel_path}: selected_scopes must be a unique non-empty string list"
                )
                selected_scopes: set[str] = set()
            else:
                selected_scopes = set(selected_scopes_raw)
        else:
            if selected_scopes_raw:
                failures.append(
                    f"{rel_path}: all_broad_catches must not declare selected_scopes"
                )
            selected_scopes = set()

        entries_by_scope: dict[str, dict[str, Any]] = {}
        for index, entry in enumerate(allowed):
            if not isinstance(entry, dict):
                failures.append(f"{rel_path}: broad_catches[{index}] must be an object")
                continue
            if set(entry) != {
                "scope",
                "expected_count",
                "classification",
                "reason",
                "regression_owner",
                "review_after",
            }:
                failures.append(
                    f"{rel_path}: broad_catches[{index}] entry keys must match schema"
                )
            scope = entry.get("scope")
            classification = entry.get("classification")
            reason = entry.get("reason")
            regression_owner = entry.get("regression_owner")
            review_after = entry.get("review_after")
            if not isinstance(scope, str) or not scope:
                failures.append(f"{rel_path}: broad_catches[{index}] missing scope")
                continue
            if scope in entries_by_scope:
                failures.append(f"{rel_path}: duplicate broad-catch scope {scope}")
            entries_by_scope[scope] = entry
            if classification not in VALID_CLASSIFICATIONS:
                failures.append(
                    f"{rel_path}:{scope}: invalid classification {classification!r}"
                )
            if not isinstance(reason, str) or not reason.strip():
                failures.append(f"{rel_path}:{scope}: missing reason")
            if not isinstance(regression_owner, str) or not regression_owner.strip():
                failures.append(f"{rel_path}:{scope}: missing regression_owner")
            else:
                owner_path = Path(regression_owner)
                if (
                    owner_path.is_absolute()
                    or ".." in owner_path.parts
                    or not owner_path.parts
                    or owner_path.parts[0] != "tests"
                    or owner_path.suffix != ".py"
                ):
                    failures.append(
                        f"{rel_path}:{scope}: regression_owner must be a safe tests/*.py path"
                    )
                elif not (repo_root / owner_path).is_file():
                    failures.append(
                        f"{rel_path}:{scope}: regression_owner does not exist"
                    )
            try:
                if not isinstance(review_after, str):
                    raise TypeError
                review_date = date.fromisoformat(review_after)
            except (TypeError, ValueError):
                failures.append(f"{rel_path}:{scope}: invalid review_after")
            else:
                if review_date < date.today():
                    failures.append(f"{rel_path}:{scope}: review_after is expired")
            if coverage == "selected_scopes" and scope not in selected_scopes:
                failures.append(f"{rel_path}:{scope}: entry is outside selected_scopes")

        catches = tuple(iter_broad_catches(path))
        governed_catches = (
            catches
            if coverage == "all_broad_catches"
            else tuple(catch for catch in catches if catch.scope in selected_scopes)
        )
        counts = Counter(catch.scope for catch in governed_catches)
        if coverage == "selected_scopes":
            for stale_scope in sorted(selected_scopes - set(counts)):
                failures.append(
                    f"{rel_path}:{stale_scope}: selected scope has no broad catch"
                )
        for catch in governed_catches:
            if catch.scope not in entries_by_scope:
                failures.append(
                    f"{rel_path}:{catch.line}: undocumented broad catch in {catch.scope}"
                )

        for scope, entry in entries_by_scope.items():
            expected_count = entry.get("expected_count", 1)
            if not isinstance(expected_count, int) or expected_count < 1:
                failures.append(f"{rel_path}:{scope}: expected_count must be >= 1")
                continue
            actual_count = counts.get(scope, 0)
            if actual_count != expected_count:
                failures.append(
                    f"{rel_path}:{scope}: expected {expected_count} broad catch(es), found {actual_count}"
                )

    failures.extend(
        _validate_ratchet_contract(
            repo_root,
            policy.get("pass_only_contract"),
            entries_key="grandfathered",
            label="pass-only broad catch",
            iterator=iter_pass_only_broad_catches,
        )
    )
    failures.extend(
        _validate_ratchet_contract(
            repo_root,
            policy.get("stdout_contract"),
            entries_key="allowed",
            label="runtime print",
            iterator=iter_runtime_prints,
        )
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--policy",
        default="tests/exception_boundary_policy.json",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    policy_path = repo_root / args.policy
    failures = validate_exception_boundary_policy(repo_root, load_policy(policy_path))
    if failures:
        for failure in failures:
            print(f"EXCEPTION-BOUNDARY-FAIL: {failure}")
        return 1
    print("EXCEPTION-BOUNDARY-PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
