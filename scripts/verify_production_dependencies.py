"""Verify the repository's source-level production dependency contract.

The verifier deliberately uses only Git metadata and Python's standard-library
parser. It never imports analyzed modules.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

POLICY_PATH = "tests/architecture_dependency_policy.json"
MAX_FINDINGS = 50
_TOP_LEVEL_KEYS = {
    "schema_version",
    "review",
    "tracked_roots",
    "domains",
    "allowed_dependencies",
    "compatibility_exceptions",
    "facade_contracts",
    "accepted_cycles",
    "dynamic_imports",
    "import_fallback_contract",
}
_REVIEW_KEYS = {
    "owner",
    "reviewed_at",
    "next_review_by",
    "static_analysis_policy_schema",
}
_EXCEPTION_KEYS = {
    "importer",
    "imported",
    "owner",
    "rationale",
    "review_condition",
}
_FACADE_KEYS = {
    "facade",
    "implementation",
    "owner",
    "rationale",
    "review_condition",
}
_CYCLE_KEYS = {"modules", "owner", "rationale", "review_condition"}
_DYNAMIC_KEYS = {
    "path",
    "scope",
    "callee",
    "target_kind",
    "target",
    "owner",
    "rationale",
    "review_condition",
}
_IMPORT_FALLBACK_CONTRACT_KEYS = {
    "production_roots",
    "repository_roots",
    "finalized_candidate_count",
    "finalized_site_count",
    "finalized_repository_site_count",
    "finalized_alternate_site_count",
    "expected_live_candidate_count",
    "inventory",
}
_IMPORT_FALLBACK_ENTRY_KEYS = {
    "path",
    "classification",
    "baseline_site_count",
    "site_count",
    "repository_site_count",
    "alternate_site_count",
    "owner",
    "rationale",
    "review_condition",
}
_IMPORT_FALLBACK_CLASSIFICATIONS = {
    "migration_required",
    "mixed_migration_required",
    "approved_alternate_dependency",
    "migrated",
}
_CANONICAL_DUAL_IMPORT_HELPER_MODULE = "services.import_fallback"
_CANONICAL_DUAL_IMPORT_HELPERS = frozenset({"import_attrs_dual", "import_module_dual"})
_DYNAMIC_IMPORT_CALLEES = frozenset(
    {"__import__", "importlib.import_module", "import_module"}
) | frozenset(
    f"{_CANONICAL_DUAL_IMPORT_HELPER_MODULE}.{helper}"
    for helper in _CANONICAL_DUAL_IMPORT_HELPERS
)
_METADATA_KEYS = ("owner", "rationale", "review_condition")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODULE_HEAD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, order=True)
class Finding:
    """A deterministic, content-free policy finding."""

    rule_id: str
    path: str
    line: int = 0
    subject: str = ""

    @property
    def code(self) -> str:
        """Compatibility alias for callers using diagnostic terminology."""

        return self.rule_id

    @property
    def identity(self) -> str:
        """Return the bounded identity without exposing source content."""

        return self.subject

    def render(self) -> str:
        return render_findings((self,))


@dataclass(frozen=True, order=True)
class DynamicImport:
    path: str
    scope: str
    callee: str
    target_kind: str
    target: str
    line: int = 0

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.path,
            self.scope,
            self.callee,
            self.target_kind,
            self.target,
        )


@dataclass(frozen=True)
class ImportFallbackEntry:
    path: str
    classification: str
    baseline_site_count: int
    site_count: int
    repository_site_count: int
    alternate_site_count: int


@dataclass(frozen=True)
class ImportFallbackContract:
    production_roots: tuple[str, ...]
    repository_roots: frozenset[str]
    finalized_candidate_count: int
    finalized_site_count: int
    finalized_repository_site_count: int
    finalized_alternate_site_count: int
    expected_live_candidate_count: int
    inventory: Mapping[str, ImportFallbackEntry]


@dataclass(frozen=True, order=True)
class ImportFallbackSite:
    path: str
    category: str
    line: int


@dataclass(frozen=True)
class Analysis:
    owned_paths: tuple[str, ...]
    static_edges: tuple[tuple[str, str], ...]
    dynamic_imports: tuple[DynamicImport, ...]
    cycles: tuple[tuple[str, ...], ...]
    findings: tuple[Finding, ...]


@dataclass
class _PolicyContext:
    tracked_files: set[str]
    owned_paths: set[str]
    path_domains: dict[str, str]
    path_modules: dict[str, str]
    module_paths: dict[str, str]
    allowed_dependencies: dict[str, set[str]]
    compatibility_exceptions: set[tuple[str, str]]
    facade_contracts: set[tuple[str, str]]
    accepted_cycles: set[frozenset[str]]
    dynamic_imports: dict[tuple[str, str, str, str, str], Mapping[str, Any]]
    import_fallback_contract: ImportFallbackContract | None


def _finding(
    rule_id: str, path: str = ".", *, line: int = 0, subject: str = ""
) -> Finding:
    return Finding(rule_id=rule_id, path=path, line=line, subject=subject)


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if re.match(r"^[A-Za-z]:", value):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value == path.as_posix()
        and "." not in path.parts
        and ".." not in path.parts
    )


def _within_root(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root.rstrip('/')}/")


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__init__"


def _tracked_python_files(repo_root: Path) -> tuple[set[str], list[Finding]]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--", "*.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        return set(), [_finding("TRACKED_DISCOVERY_FAILED")]
    return (
        {
            line.strip().replace("\\", "/")
            for line in result.stdout.splitlines()
            if line.strip()
        },
        [],
    )


def _validate_review_metadata(
    entry: Mapping[str, Any],
    *,
    path: str,
    findings: list[Finding],
) -> None:
    if any(
        not isinstance(entry.get(key), str) or not str(entry.get(key)).strip()
        for key in _METADATA_KEYS
    ):
        findings.append(_finding("POLICY_REVIEW_METADATA", subject=path))


def _nonnegative_policy_count(
    value: Any,
    *,
    subject: str,
    findings: list[Finding],
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", subject=subject))
        return 0
    return value


def _validate_import_fallback_contract(
    value: Any,
    *,
    valid_roots: Sequence[str],
    owned_paths: set[str],
    findings: list[Finding],
) -> ImportFallbackContract | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID"))
        return None
    for key in sorted(set(value) - _IMPORT_FALLBACK_CONTRACT_KEYS):
        findings.append(
            _finding("POLICY_UNKNOWN_KEY", subject=f"import_fallback_contract.{key}")
        )

    production_value = value.get("production_roots")
    production_roots: list[str] = []
    if not isinstance(production_value, list) or not production_value:
        findings.append(
            _finding(
                "IMPORT_FALLBACK_POLICY_INVALID",
                subject="import_fallback_contract.production_roots",
            )
        )
    else:
        for index, root in enumerate(production_value):
            subject = f"import_fallback_contract.production_roots[{index}]"
            if (
                not isinstance(root, str)
                or root not in valid_roots
                or root in production_roots
            ):
                findings.append(
                    _finding("IMPORT_FALLBACK_POLICY_INVALID", subject=subject)
                )
                continue
            production_roots.append(root)

    repository_value = value.get("repository_roots")
    repository_roots: set[str] = set()
    if not isinstance(repository_value, list) or not repository_value:
        findings.append(
            _finding(
                "IMPORT_FALLBACK_POLICY_INVALID",
                subject="import_fallback_contract.repository_roots",
            )
        )
    else:
        for index, root in enumerate(repository_value):
            subject = f"import_fallback_contract.repository_roots[{index}]"
            if (
                not isinstance(root, str)
                or not _MODULE_HEAD_RE.fullmatch(root)
                or root in repository_roots
            ):
                findings.append(
                    _finding("IMPORT_FALLBACK_POLICY_INVALID", subject=subject)
                )
                continue
            repository_roots.add(root)

    count_names = (
        "finalized_candidate_count",
        "finalized_site_count",
        "finalized_repository_site_count",
        "finalized_alternate_site_count",
        "expected_live_candidate_count",
    )
    counts = {
        name: _nonnegative_policy_count(
            value.get(name),
            subject=f"import_fallback_contract.{name}",
            findings=findings,
        )
        for name in count_names
    }

    inventory_value = value.get("inventory")
    if not isinstance(inventory_value, list):
        findings.append(
            _finding(
                "IMPORT_FALLBACK_POLICY_INVALID",
                subject="import_fallback_contract.inventory",
            )
        )
        inventory_value = []
    inventory: dict[str, ImportFallbackEntry] = {}
    for index, raw_entry in enumerate(inventory_value):
        subject = f"import_fallback_contract.inventory[{index}]"
        if not isinstance(raw_entry, Mapping):
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", subject=subject))
            continue
        for key in sorted(set(raw_entry) - _IMPORT_FALLBACK_ENTRY_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(raw_entry, path=subject, findings=findings)
        path_value = raw_entry.get("path")
        if not _safe_relative_path(path_value):
            findings.append(_finding("PATH_UNSAFE", subject=subject))
            continue
        path = str(path_value)
        if path in inventory:
            findings.append(_finding("IMPORT_FALLBACK_DUPLICATE", path=path))
            continue
        if path not in owned_paths:
            findings.append(_finding("IMPORT_FALLBACK_PATH_UNOWNED", path=path))
        if not any(_within_root(path, root) for root in production_roots):
            findings.append(_finding("IMPORT_FALLBACK_PATH_OUTSIDE", path=path))
        classification = str(raw_entry.get("classification", ""))
        if classification not in _IMPORT_FALLBACK_CLASSIFICATIONS:
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        baseline_count = _nonnegative_policy_count(
            raw_entry.get("baseline_site_count"),
            subject=f"{subject}.baseline_site_count",
            findings=findings,
        )
        site_count = _nonnegative_policy_count(
            raw_entry.get("site_count"),
            subject=f"{subject}.site_count",
            findings=findings,
        )
        repository_count = _nonnegative_policy_count(
            raw_entry.get("repository_site_count"),
            subject=f"{subject}.repository_site_count",
            findings=findings,
        )
        alternate_count = _nonnegative_policy_count(
            raw_entry.get("alternate_site_count"),
            subject=f"{subject}.alternate_site_count",
            findings=findings,
        )
        if site_count != repository_count + alternate_count:
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        if baseline_count < site_count:
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        if classification == "migrated":
            if site_count != 0 or baseline_count <= 0:
                findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        elif baseline_count != site_count:
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        if classification == "migration_required" and repository_count <= 0:
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        if classification == "mixed_migration_required" and (
            repository_count <= 0 or alternate_count <= 0
        ):
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        if classification == "approved_alternate_dependency" and (
            repository_count != 0 or alternate_count <= 0
        ):
            findings.append(_finding("IMPORT_FALLBACK_POLICY_INVALID", path=path))
        inventory[path] = ImportFallbackEntry(
            path=path,
            classification=classification,
            baseline_site_count=baseline_count,
            site_count=site_count,
            repository_site_count=repository_count,
            alternate_site_count=alternate_count,
        )

    baseline_sites = sum(entry.baseline_site_count for entry in inventory.values())
    live_paths = sum(entry.site_count > 0 for entry in inventory.values())
    baseline_repository_sites = sum(
        entry.repository_site_count
        + (
            entry.baseline_site_count - entry.site_count
            if entry.classification == "migrated"
            else 0
        )
        for entry in inventory.values()
    )
    baseline_alternate_sites = sum(
        entry.alternate_site_count for entry in inventory.values()
    )
    expected_totals = {
        "finalized_candidate_count": len(inventory),
        "finalized_site_count": baseline_sites,
        "finalized_repository_site_count": baseline_repository_sites,
        "finalized_alternate_site_count": baseline_alternate_sites,
        "expected_live_candidate_count": live_paths,
    }
    for name, expected in expected_totals.items():
        if counts[name] != expected:
            findings.append(
                _finding(
                    "IMPORT_FALLBACK_INVENTORY_COUNT",
                    subject=f"{name}:{counts[name]}:{expected}",
                )
            )
    if counts["finalized_site_count"] != (
        counts["finalized_repository_site_count"]
        + counts["finalized_alternate_site_count"]
    ):
        findings.append(
            _finding(
                "IMPORT_FALLBACK_INVENTORY_COUNT",
                subject="finalized_site_category_sum",
            )
        )

    return ImportFallbackContract(
        production_roots=tuple(production_roots),
        repository_roots=frozenset(repository_roots),
        finalized_candidate_count=counts["finalized_candidate_count"],
        finalized_site_count=counts["finalized_site_count"],
        finalized_repository_site_count=counts["finalized_repository_site_count"],
        finalized_alternate_site_count=counts["finalized_alternate_site_count"],
        expected_live_candidate_count=counts["expected_live_candidate_count"],
        inventory=inventory,
    )


def _validate_policy(
    repo_root: Path,
    policy: Mapping[str, Any],
    tracked_files: Iterable[str] | None,
) -> tuple[_PolicyContext, list[Finding]]:
    findings: list[Finding] = []
    unknown_keys = set(policy) - _TOP_LEVEL_KEYS
    for key in sorted(unknown_keys):
        findings.append(_finding("POLICY_UNKNOWN_KEY", subject=key))
    if policy.get("schema_version") != 1:
        findings.append(_finding("POLICY_SCHEMA_VERSION"))

    review = policy.get("review")
    if not isinstance(review, Mapping):
        findings.append(_finding("POLICY_REVIEW_METADATA", subject="review"))
    else:
        for key in sorted(set(review) - _REVIEW_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"review.{key}"))
        if not isinstance(review.get("owner"), str) or not review["owner"].strip():
            findings.append(_finding("POLICY_REVIEW_METADATA", subject="review.owner"))
        parsed_dates: dict[str, date] = {}
        for key in ("reviewed_at", "next_review_by"):
            try:
                parsed_dates[key] = date.fromisoformat(str(review.get(key, "")))
            except ValueError:
                findings.append(
                    _finding("POLICY_REVIEW_METADATA", subject=f"review.{key}")
                )
        if (
            len(parsed_dates) == 2
            and parsed_dates["next_review_by"] < parsed_dates["reviewed_at"]
        ):
            findings.append(
                _finding("POLICY_REVIEW_METADATA", subject="review.date_order")
            )

    if tracked_files is None:
        discovered, discovery_findings = _tracked_python_files(repo_root)
        findings.extend(discovery_findings)
    else:
        discovered = {
            str(path).replace("\\", "/")
            for path in tracked_files
            if str(path).endswith(".py")
        }

    roots_value = policy.get("tracked_roots")
    roots = roots_value if isinstance(roots_value, list) else []
    if not isinstance(roots_value, list) or not roots:
        findings.append(_finding("ROOTS_INVALID"))
    valid_roots: list[str] = []
    seen_roots: set[str] = set()
    for index, value in enumerate(roots):
        subject = f"tracked_roots[{index}]"
        if not _safe_relative_path(value):
            findings.append(_finding("PATH_UNSAFE", subject=subject))
            continue
        root = str(value)
        if root in seen_roots:
            findings.append(_finding("ROOT_DUPLICATE", path=root))
            continue
        seen_roots.add(root)
        valid_roots.append(root)
        candidate = repo_root / root
        try:
            candidate.resolve().relative_to(repo_root.resolve())
        except ValueError:
            findings.append(_finding("PATH_UNSAFE", path=root))
            continue
        if not candidate.exists():
            findings.append(_finding("ROOT_MISSING", path=root))

    domains_value = policy.get("domains")
    domains = domains_value if isinstance(domains_value, Mapping) else {}
    if not domains:
        findings.append(_finding("DOMAINS_INVALID"))
    valid_domain_names = {
        str(name)
        for name in domains
        if isinstance(name, str) and _DOMAIN_RE.fullmatch(name)
    }
    for name in domains:
        if name not in valid_domain_names:
            findings.append(_finding("DOMAIN_UNKNOWN", subject=str(name)))

    owned_paths: set[str] = set()
    path_domains: dict[str, str] = {}
    path_modules: dict[str, str] = {}
    module_paths: dict[str, str] = {}
    for domain_name, entries in domains.items():
        if domain_name not in valid_domain_names:
            continue
        if not isinstance(entries, list):
            findings.append(_finding("OWNERSHIP_INVALID", subject=str(domain_name)))
            continue
        for index, value in enumerate(entries):
            subject = f"domains.{domain_name}[{index}]"
            if not _safe_relative_path(value):
                findings.append(_finding("PATH_UNSAFE", subject=subject))
                continue
            path = str(value)
            if not path.endswith(".py"):
                findings.append(_finding("OWNERSHIP_INVALID", path=path))
                continue
            if path in path_domains:
                findings.append(_finding("OWN_DUPLICATE", path=path))
                continue
            path_domains[path] = str(domain_name)
            owned_paths.add(path)
            if not any(_within_root(path, root) for root in valid_roots):
                findings.append(_finding("OWN_OUTSIDE_ROOT", path=path))
            if path not in discovered:
                findings.append(_finding("OWN_NOT_TRACKED", path=path))
            candidate = repo_root / path
            try:
                candidate.resolve().relative_to(repo_root.resolve())
            except ValueError:
                findings.append(_finding("PATH_UNSAFE", path=path))
                continue
            if not candidate.is_file():
                findings.append(_finding("OWN_MISSING", path=path))
            module = _module_name(path)
            if module in module_paths:
                findings.append(
                    _finding("OWN_MODULE_COLLISION", path=path, subject=module)
                )
            else:
                path_modules[path] = module
                module_paths[module] = path

    tracked_in_roots = {
        path
        for path in discovered
        if any(_within_root(path, root) for root in valid_roots)
    }
    for path in sorted(tracked_in_roots - owned_paths):
        findings.append(_finding("OWN_UNOWNED_MODULE", path=path))

    allowed_value = policy.get("allowed_dependencies")
    allowed_raw = allowed_value if isinstance(allowed_value, Mapping) else {}
    if not isinstance(allowed_value, Mapping):
        findings.append(_finding("DEPENDENCIES_INVALID"))
    for domain in sorted(valid_domain_names - set(allowed_raw)):
        findings.append(_finding("DOMAIN_DIRECTION_MISSING", subject=domain))
    for domain in sorted(set(allowed_raw) - valid_domain_names):
        findings.append(_finding("DOMAIN_UNKNOWN", subject=str(domain)))
    allowed_dependencies: dict[str, set[str]] = {}
    for domain in sorted(valid_domain_names):
        values = allowed_raw.get(domain, [])
        if not isinstance(values, list):
            findings.append(_finding("DEPENDENCIES_INVALID", subject=domain))
            values = []
        accepted: set[str] = set()
        for target in values:
            if target not in valid_domain_names:
                findings.append(
                    _finding(
                        "DOMAIN_UNKNOWN",
                        subject=f"{domain}->{target}",
                    )
                )
            else:
                accepted.add(str(target))
        allowed_dependencies[domain] = accepted

    compatibility_exceptions: set[tuple[str, str]] = set()
    exception_entries = policy.get("compatibility_exceptions")
    if not isinstance(exception_entries, list):
        findings.append(_finding("EXCEPTIONS_INVALID"))
        exception_entries = []
    for index, entry in enumerate(exception_entries):
        subject = f"compatibility_exceptions[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(_finding("EXCEPTIONS_INVALID", subject=subject))
            continue
        for key in sorted(set(entry) - _EXCEPTION_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(entry, path=subject, findings=findings)
        edge = (str(entry.get("importer", "")), str(entry.get("imported", "")))
        if edge in compatibility_exceptions:
            findings.append(_finding("DEP_DUPLICATE_EXCEPTION", subject=subject))
        compatibility_exceptions.add(edge)
        if edge[0] not in module_paths or edge[1] not in module_paths:
            findings.append(_finding("DEP_EXCEPTION_MODULE_UNKNOWN", subject=subject))

    facade_contracts: set[tuple[str, str]] = set()
    facade_entries = policy.get("facade_contracts", [])
    if not isinstance(facade_entries, list):
        findings.append(_finding("FACADES_INVALID"))
        facade_entries = []
    for index, entry in enumerate(facade_entries):
        subject = f"facade_contracts[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(_finding("FACADES_INVALID", subject=subject))
            continue
        for key in sorted(set(entry) - _FACADE_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(entry, path=subject, findings=findings)
        edge = (
            str(entry.get("facade", "")),
            str(entry.get("implementation", "")),
        )
        if not edge[0] or not edge[1] or edge[0] == edge[1]:
            findings.append(_finding("FACADES_INVALID", subject=subject))
            continue
        if edge in facade_contracts:
            findings.append(_finding("FACADE_DUPLICATE", subject=subject))
        facade_contracts.add(edge)
        if edge[0] not in module_paths or edge[1] not in module_paths:
            findings.append(_finding("FACADE_MODULE_UNKNOWN", subject=subject))

    accepted_cycles: set[frozenset[str]] = set()
    cycle_entries = policy.get("accepted_cycles")
    if not isinstance(cycle_entries, list):
        findings.append(_finding("CYCLES_INVALID"))
        cycle_entries = []
    for index, entry in enumerate(cycle_entries):
        subject = f"accepted_cycles[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(_finding("CYCLES_INVALID", subject=subject))
            continue
        for key in sorted(set(entry) - _CYCLE_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(entry, path=subject, findings=findings)
        modules = entry.get("modules")
        if (
            not isinstance(modules, list)
            or len(modules) < 2
            or any(not isinstance(module, str) for module in modules)
        ):
            findings.append(_finding("CYCLES_INVALID", subject=subject))
            continue
        cycle = frozenset(modules)
        if len(cycle) != len(modules):
            findings.append(_finding("CYCLE_DUPLICATE_MODULE", subject=subject))
        if cycle in accepted_cycles:
            findings.append(_finding("CYCLE_DUPLICATE_BASELINE", subject=subject))
        accepted_cycles.add(cycle)
        if any(module not in module_paths for module in cycle):
            findings.append(_finding("CYCLE_MODULE_UNKNOWN", subject=subject))

    dynamic_imports: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
    dynamic_entries = policy.get("dynamic_imports")
    if not isinstance(dynamic_entries, list):
        findings.append(_finding("DYNAMIC_INVALID"))
        dynamic_entries = []
    for index, entry in enumerate(dynamic_entries):
        subject = f"dynamic_imports[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(_finding("DYNAMIC_INVALID", subject=subject))
            continue
        for key in sorted(set(entry) - _DYNAMIC_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(entry, path=subject, findings=findings)
        path_value = entry.get("path")
        if not _safe_relative_path(path_value):
            findings.append(_finding("PATH_UNSAFE", subject=subject))
            continue
        dynamic_path = str(path_value)
        if dynamic_path not in owned_paths:
            findings.append(_finding("DYNAMIC_PATH_UNOWNED", path=dynamic_path))
        target_kind = entry.get("target_kind")
        identity = (
            dynamic_path,
            str(entry.get("scope", "")),
            str(entry.get("callee", "")),
            str(target_kind),
            str(entry.get("target", "")),
        )
        if (
            not identity[1]
            or identity[2] not in _DYNAMIC_IMPORT_CALLEES
            or target_kind not in {"literal", "expression"}
            or not identity[4]
        ):
            findings.append(_finding("DYNAMIC_INVALID", path=dynamic_path))
        if identity in dynamic_imports:
            findings.append(_finding("DYNAMIC_DUPLICATE", path=dynamic_path))
        dynamic_imports[identity] = entry

    import_fallback_contract = _validate_import_fallback_contract(
        policy.get("import_fallback_contract"),
        valid_roots=valid_roots,
        owned_paths=owned_paths,
        findings=findings,
    )

    context = _PolicyContext(
        tracked_files=discovered,
        owned_paths=owned_paths,
        path_domains=path_domains,
        path_modules=path_modules,
        module_paths=module_paths,
        allowed_dependencies=allowed_dependencies,
        compatibility_exceptions=compatibility_exceptions,
        facade_contracts=facade_contracts,
        accepted_cycles=accepted_cycles,
        dynamic_imports=dynamic_imports,
        import_fallback_contract=import_fallback_contract,
    )
    return context, findings


def _resolve_relative_import(
    current_module: str,
    current_path: str,
    node: ast.ImportFrom,
) -> str:
    if not node.level:
        return node.module or ""
    is_package = current_path.endswith("/__init__.py") or current_path == "__init__.py"
    if current_path == "__init__.py":
        package_parts: list[str] = []
    else:
        package_parts = (
            current_module.split(".") if is_package else current_module.split(".")[:-1]
        )
    ascend = node.level - 1
    if ascend > len(package_parts):
        prefix: list[str] = []
    elif ascend:
        prefix = package_parts[:-ascend]
    else:
        prefix = package_parts
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


class _SourceVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        module: str,
        module_paths: Mapping[str, str],
    ) -> None:
        self.path = path
        self.module = module
        self.module_paths = module_paths
        self.edges: set[tuple[str, str]] = set()
        self.dynamic_imports: list[DynamicImport] = []
        self.scope: list[str] = []
        self.builtins_aliases: set[str] = {"builtins"}
        self.builtin_import_aliases: set[str] = {"__import__"}
        self.importlib_aliases: set[str] = {"importlib"}
        self.import_module_aliases: set[str] = set()
        self.dual_import_helper_aliases: dict[str, str] = {}
        self.findings: list[Finding] = []

    def _add_edge(self, imported: str) -> None:
        # IMPORTANT: require an exact owned module. Falling back to the nearest
        # package turns missing optional submodules into false dependency edges.
        target = imported if imported in self.module_paths else None
        if target and target != self.module:
            self.edges.add((self.module, target))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "builtins":
                self.builtins_aliases.add(alias.asname or alias.name)
            if alias.name == "importlib":
                self.importlib_aliases.add(alias.asname or alias.name)
            self._add_edge(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _resolve_relative_import(self.module, self.path, node)
        if base == _CANONICAL_DUAL_IMPORT_HELPER_MODULE:
            # IMPORTANT: trust helper targets only through proven canonical imports;
            # matching a same-name local function would fabricate architecture edges.
            for alias in node.names:
                if alias.name in _CANONICAL_DUAL_IMPORT_HELPERS:
                    self.dual_import_helper_aliases[alias.asname or alias.name] = (
                        alias.name
                    )
        if node.level == 0 and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    self.import_module_aliases.add(alias.asname or alias.name)
        if node.level == 0 and node.module == "builtins":
            for alias in node.names:
                if alias.name == "__import__":
                    self.builtin_import_aliases.add(alias.asname or alias.name)
        exact_children: list[str] = []
        for alias in node.names:
            candidate = f"{base}.{alias.name}" if base else alias.name
            if candidate in self.module_paths:
                exact_children.append(candidate)
        if exact_children:
            for candidate in exact_children:
                self._add_edge(candidate)
        elif base:
            self._add_edge(base)

    def _visit_scoped(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node)

    @staticmethod
    def _bound_helper_argument(
        node: ast.Call,
        *,
        position: int,
        keyword: str,
    ) -> tuple[ast.expr | None, bool]:
        # IMPORTANT: a starred positional or keyword unpack can bind the target twice;
        # guessing through it would let a canonical helper hide an architecture edge.
        if any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            item.arg is None for item in node.keywords
        ):
            return None, False
        positional = node.args[position] if len(node.args) > position else None
        keyword_values = [item.value for item in node.keywords if item.arg == keyword]
        if len(keyword_values) > 1 or (positional is not None and keyword_values):
            return None, False
        if positional is not None:
            return positional, True
        if keyword_values:
            return keyword_values[0], True
        return None, False

    @staticmethod
    def _string_literal(node: ast.expr | None) -> str | None:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value
        ):
            return node.value
        return None

    def _normalized_relative_helper_target(self, value: str) -> str | None:
        level = len(value) - len(value.lstrip("."))
        if not level:
            return value
        # IMPORTANT: package __init__ owns its own relative-import base; dropping that
        # component creates a false mismatch and blocks a valid governed helper edge.
        is_package = self.path.endswith("/__init__.py") or self.path == "__init__.py"
        package_parts = self.module.split(".")
        if not is_package:
            package_parts = package_parts[:-1]
        ascend = level - 1
        if ascend > len(package_parts):
            return None
        prefix = package_parts[:-ascend] if ascend else package_parts
        suffix = value[level:].split(".") if value[level:] else []
        return ".".join([*prefix, *suffix])

    def _record_dual_import_helper(self, node: ast.Call, helper_name: str) -> None:
        relative_node, relative_valid = self._bound_helper_argument(
            node,
            position=1,
            keyword="relative_module",
        )
        absolute_node, absolute_valid = self._bound_helper_argument(
            node,
            position=2,
            keyword="absolute_module",
        )
        if not relative_valid or not absolute_valid:
            self.findings.append(
                _finding(
                    "DUAL_IMPORT_HELPER_TARGET_INVALID",
                    path=self.path,
                    line=node.lineno,
                    subject=helper_name,
                )
            )
            return

        absolute_module = self._string_literal(absolute_node)
        if absolute_module is None:
            if isinstance(absolute_node, ast.Constant):
                self.findings.append(
                    _finding(
                        "DUAL_IMPORT_HELPER_TARGET_INVALID",
                        path=self.path,
                        line=node.lineno,
                        subject=helper_name,
                    )
                )
                return
            target = (
                absolute_node.id
                if isinstance(absolute_node, ast.Name)
                else f"<{type(absolute_node).__name__}>"
            )
            self.dynamic_imports.append(
                DynamicImport(
                    path=self.path,
                    scope=".".join(self.scope) or "<module>",
                    callee=f"{_CANONICAL_DUAL_IMPORT_HELPER_MODULE}.{helper_name}",
                    target_kind="expression",
                    target=target,
                    line=node.lineno,
                )
            )
            return

        relative_module = self._string_literal(relative_node)
        if relative_module is None:
            self.findings.append(
                _finding(
                    "DUAL_IMPORT_HELPER_TARGET_INVALID",
                    path=self.path,
                    line=node.lineno,
                    subject=helper_name,
                )
            )
            return
        normalized_relative = self._normalized_relative_helper_target(relative_module)
        if normalized_relative != absolute_module:
            self.findings.append(
                _finding(
                    "DUAL_IMPORT_HELPER_TARGET_MISMATCH",
                    path=self.path,
                    line=node.lineno,
                    subject=f"{relative_module}->{absolute_module}",
                )
            )
        self._add_edge(absolute_module)

    def visit_Call(self, node: ast.Call) -> None:
        callee = ""
        if isinstance(node.func, ast.Name):
            helper_name = self.dual_import_helper_aliases.get(node.func.id)
            if helper_name:
                self._record_dual_import_helper(node, helper_name)
            if node.func.id in self.builtin_import_aliases:
                callee = "__import__"
            elif node.func.id in self.import_module_aliases:
                callee = "import_module"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "__import__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.builtins_aliases
        ):
            callee = "__import__"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.importlib_aliases
        ):
            callee = "importlib.import_module"
        if callee:
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                target_kind = "literal"
                target = node.args[0].value
            elif node.args:
                target_kind = "expression"
                argument = node.args[0]
                target = (
                    argument.id
                    if isinstance(argument, ast.Name)
                    else f"<{type(argument).__name__}>"
                )
            else:
                target_kind = "expression"
                target = "<missing>"
            self.dynamic_imports.append(
                DynamicImport(
                    path=self.path,
                    scope=".".join(self.scope) or "<module>",
                    callee=callee,
                    target_kind=target_kind,
                    target=target,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)


def _exception_type_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for element in node.elts:
            names.update(_exception_type_names(element))
        return names
    return set()


def _recovery_import_heads(statements: Sequence[ast.stmt]) -> tuple[set[str], bool]:
    heads: set[str] = set()
    has_relative = False
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Import):
                heads.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    has_relative = True
                elif node.module:
                    heads.add(node.module.split(".", 1)[0])
                else:
                    heads.update(alias.name.split(".", 1)[0] for alias in node.names)
    return heads, has_relative


def _observe_import_fallback_sites(
    path: str,
    tree: ast.AST,
    repository_roots: frozenset[str],
) -> tuple[ImportFallbackSite, ...]:
    sites: list[ImportFallbackSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _exception_type_names(handler.type).intersection(
                {"ImportError", "ModuleNotFoundError"}
            ):
                continue
            heads, has_relative = _recovery_import_heads(handler.body)
            if not heads and not has_relative:
                continue
            category = (
                "repository" if heads.intersection(repository_roots) else "alternate"
            )
            sites.append(
                ImportFallbackSite(
                    path=path,
                    category=category,
                    line=handler.lineno,
                )
            )
    return tuple(sites)


def _validate_live_import_fallbacks(
    contract: ImportFallbackContract,
    sites: Sequence[ImportFallbackSite],
) -> list[Finding]:
    findings: list[Finding] = []
    observed: dict[str, list[ImportFallbackSite]] = defaultdict(list)
    for site in sites:
        observed[site.path].append(site)

    for path in sorted(set(observed) - set(contract.inventory)):
        findings.append(_finding("IMPORT_FALLBACK_UNCLASSIFIED", path=path))

    for path, entry in sorted(contract.inventory.items()):
        path_sites = observed.get(path, [])
        repository_count = sum(site.category == "repository" for site in path_sites)
        alternate_count = sum(site.category == "alternate" for site in path_sites)
        site_count = len(path_sites)
        if entry.classification == "migrated" and site_count:
            findings.append(_finding("IMPORT_FALLBACK_REGRESSION", path=path))
            continue
        if (
            site_count != entry.site_count
            or repository_count != entry.repository_site_count
            or alternate_count != entry.alternate_site_count
        ):
            findings.append(
                _finding(
                    "IMPORT_FALLBACK_COUNT_DRIFT",
                    path=path,
                    subject=(
                        f"{entry.site_count}:{entry.repository_site_count}:"
                        f"{entry.alternate_site_count}->{site_count}:"
                        f"{repository_count}:{alternate_count}"
                    ),
                )
            )
        if (
            (
                entry.classification == "approved_alternate_dependency"
                and repository_count
            )
            or (entry.classification == "migration_required" and not repository_count)
            or (
                entry.classification == "mixed_migration_required"
                and (not repository_count or not alternate_count)
            )
        ):
            findings.append(_finding("IMPORT_FALLBACK_CLASSIFICATION", path=path))

    live_candidate_count = sum(bool(path_sites) for path_sites in observed.values())
    if live_candidate_count != contract.expected_live_candidate_count:
        findings.append(
            _finding(
                "IMPORT_FALLBACK_LIVE_COUNT",
                subject=(
                    f"{contract.expected_live_candidate_count}->{live_candidate_count}"
                ),
            )
        )
    return findings


def _strongly_connected_components(
    modules: Iterable[str],
    edges: Iterable[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for importer, imported in edges:
        adjacency[importer].add(imported)
    next_index = 0
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        nonlocal next_index
        indices[module] = next_index
        low_links[module] = next_index
        next_index += 1
        stack.append(module)
        on_stack.add(module)
        for imported in sorted(adjacency[module]):
            if imported not in indices:
                visit(imported)
                low_links[module] = min(low_links[module], low_links[imported])
            elif imported in on_stack:
                low_links[module] = min(low_links[module], indices[imported])
        if low_links[module] != indices[module]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in sorted(modules):
        if module not in indices:
            visit(module)
    return tuple(sorted(components))


def analyze_repository(
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    tracked_files: Iterable[str] | None = None,
) -> Analysis:
    """Analyze a repository without importing or executing its source modules."""

    repo_root = repo_root.resolve()
    context, findings = _validate_policy(repo_root, policy, tracked_files)
    edges: set[tuple[str, str]] = set()
    dynamic_imports: list[DynamicImport] = []
    import_fallback_sites: list[ImportFallbackSite] = []
    for path in sorted(context.owned_paths):
        source_path = repo_root / path
        if not source_path.is_file() or path not in context.path_modules:
            continue
        try:
            # IMPORTANT: tokenize.open handles encoding cookies and existing UTF-8 BOMs
            # without rewriting source or importing production modules.
            with tokenize.open(source_path) as source_file:
                tree = ast.parse(source_file.read(), filename=path)
        except (OSError, SyntaxError, UnicodeError) as exc:
            findings.append(
                _finding("SOURCE_PARSE", path=path, subject=type(exc).__name__)
            )
            continue
        visitor = _SourceVisitor(
            path=path,
            module=context.path_modules[path],
            module_paths=context.module_paths,
        )
        visitor.visit(tree)
        edges.update(visitor.edges)
        dynamic_imports.extend(visitor.dynamic_imports)
        findings.extend(visitor.findings)
        contract = context.import_fallback_contract
        if contract and any(
            _within_root(path, root) for root in contract.production_roots
        ):
            import_fallback_sites.extend(
                _observe_import_fallback_sites(
                    path,
                    tree,
                    contract.repository_roots,
                )
            )

    for importer, imported in sorted(edges):
        importer_path = context.module_paths.get(importer, ".")
        importer_domain = context.path_domains.get(importer_path)
        imported_path = context.module_paths.get(imported, ".")
        imported_domain = context.path_domains.get(imported_path)
        if not importer_domain or not imported_domain:
            continue
        allowed = imported_domain in context.allowed_dependencies.get(
            importer_domain, set()
        )
        exception = (importer, imported) in context.compatibility_exceptions
        if not allowed and not exception:
            findings.append(
                _finding(
                    "DEP_FORBIDDEN_DIRECTION",
                    path=importer_path,
                    subject=f"{importer}->{imported}",
                )
            )
    for importer, imported in sorted(context.compatibility_exceptions):
        if (importer, imported) not in edges:
            path = context.module_paths.get(importer, ".")
            findings.append(
                _finding(
                    "DEP_STALE_EXCEPTION",
                    path=path,
                    subject=f"{importer}->{imported}",
                )
            )

    for facade, implementation in sorted(context.facade_contracts):
        if (facade, implementation) not in edges:
            path = context.module_paths.get(facade, ".")
            findings.append(
                _finding(
                    "FACADE_STALE",
                    path=path,
                    subject=f"{facade}->{implementation}",
                )
            )
        if (implementation, facade) in edges:
            path = context.module_paths.get(implementation, ".")
            findings.append(
                _finding(
                    "FACADE_REVERSE_DEPENDENCY",
                    path=path,
                    subject=f"{implementation}->{facade}",
                )
            )

    cycles = _strongly_connected_components(context.module_paths, edges)
    current_cycle_sets = {frozenset(cycle) for cycle in cycles}
    for cycle in cycles:
        if frozenset(cycle) not in context.accepted_cycles:
            path = context.module_paths.get(cycle[0], ".")
            findings.append(_finding("CYCLE_NEW", path=path, subject="|".join(cycle)))
    for accepted_cycle in sorted(
        context.accepted_cycles, key=lambda item: sorted(item)
    ):
        if accepted_cycle not in current_cycle_sets:
            first = sorted(accepted_cycle)[0] if accepted_cycle else ""
            path = context.module_paths.get(first, ".")
            findings.append(
                _finding(
                    "CYCLE_STALE",
                    path=path,
                    subject="|".join(sorted(accepted_cycle)),
                )
            )

    current_dynamic = {site.identity: site for site in dynamic_imports}
    for identity, site in sorted(current_dynamic.items()):
        if identity not in context.dynamic_imports:
            rule_id = (
                "DYNAMIC_UNREGISTERED_LITERAL"
                if site.target_kind == "literal"
                else "DYNAMIC_UNREGISTERED_EXPRESSION"
            )
            findings.append(
                _finding(
                    rule_id,
                    path=site.path,
                    line=site.line,
                    subject=f"{site.scope}:{site.callee}",
                )
            )
    for identity in sorted(context.dynamic_imports):
        if identity not in current_dynamic:
            path, scope, callee, _, _ = identity
            findings.append(
                _finding(
                    "DYNAMIC_STALE",
                    path=path,
                    subject=f"{scope}:{callee}",
                )
            )

    if context.import_fallback_contract is not None:
        findings.extend(
            _validate_live_import_fallbacks(
                context.import_fallback_contract,
                import_fallback_sites,
            )
        )

    return Analysis(
        owned_paths=tuple(sorted(context.owned_paths)),
        static_edges=tuple(sorted(edges)),
        dynamic_imports=tuple(sorted(dynamic_imports)),
        cycles=cycles,
        findings=tuple(sorted(set(findings))),
    )


def verify_repository(
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    tracked_files: Iterable[str] | None = None,
) -> tuple[Finding, ...]:
    return analyze_repository(
        repo_root,
        policy,
        tracked_files=tracked_files,
    ).findings


def evaluate_repository(
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    tracked_files: Iterable[str] | None = None,
) -> list[Finding]:
    """Compatibility facade returning the deterministic findings as a list."""

    return list(verify_repository(repo_root, policy, tracked_files=tracked_files))


def render_findings(findings: Sequence[Finding]) -> str:
    lines: list[str] = []
    for finding in sorted(findings):
        location = finding.path
        if finding.line:
            location = f"{location}:{finding.line}"
        # Security boundary: CLI output is limited to rule IDs and repository-relative
        # locations. Internal graph identities remain available to in-process tests.
        lines.append(f"{finding.rule_id} {location}")
    return "\n".join(lines)


def _load_policy(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("policy root must be an object")
    return value


def run_cli(
    repo_root: Path,
    policy_path: Path,
    *,
    max_findings: int = MAX_FINDINGS,
) -> tuple[int, list[str]]:
    """Run the bounded CLI contract without printing or leaking host paths."""

    repo_root = repo_root.resolve()
    policy_path = policy_path if policy_path.is_absolute() else repo_root / policy_path
    try:
        policy_path.resolve().relative_to(repo_root)
    except ValueError:
        return 2, ["POLICY_PATH_OUTSIDE ."]
    try:
        policy = _load_policy(policy_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 2, [f"POLICY_JSON_INVALID {POLICY_PATH}"]

    findings = verify_repository(repo_root, policy)
    if not findings:
        return 0, ["DEPENDENCY_POLICY_PASS ."]

    limit = max(1, min(int(max_findings), MAX_FINDINGS))
    visible = findings[:limit]
    lines = [finding.render() for finding in visible]
    omitted = len(findings) - len(visible)
    if omitted:
        lines.append(f"FINDINGS_TRUNCATED - {omitted} omitted")
    return 1, lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-findings", type=int, default=MAX_FINDINGS)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    policy_path = args.policy or (repo_root / POLICY_PATH)
    exit_code, lines = run_cli(
        repo_root,
        policy_path,
        max_findings=args.max_findings,
    )
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
