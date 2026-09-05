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
    "environment_alias_contract",
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
_ENV_ALIAS_CONTRACT_KEYS = {
    "production_roots",
    "central_owner",
    "supported_legacy_keys",
    "supported_dynamic_legacy_keys",
    "rejected_legacy_keys",
    "direct_read_exceptions",
}
_ENV_ALIAS_EXCEPTION_KEYS = {
    "path",
    "owner",
    "rationale",
    "review_condition",
}
_LEGACY_ENV_PREFIXES = ("MOLTBOT_", "CLAWDBOT_")
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


@dataclass(frozen=True)
class EnvironmentAliasContract:
    production_roots: tuple[str, ...]
    central_owner: str
    supported_legacy_keys: frozenset[str]
    supported_dynamic_legacy_keys: frozenset[str]
    rejected_legacy_keys: frozenset[str]
    direct_read_exceptions: frozenset[str]


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
    environment_alias_contract: EnvironmentAliasContract | None


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


def _validate_environment_alias_contract(
    raw_contract: Any,
    *,
    valid_roots: Sequence[str],
    owned_paths: set[str],
    findings: list[Finding],
) -> EnvironmentAliasContract | None:
    if raw_contract is None:
        return None
    if not isinstance(raw_contract, Mapping):
        findings.append(_finding("ENV_ALIAS_CONTRACT_INVALID"))
        return None
    for key in sorted(set(raw_contract) - _ENV_ALIAS_CONTRACT_KEYS):
        findings.append(
            _finding("POLICY_UNKNOWN_KEY", subject=f"environment_alias_contract.{key}")
        )

    roots_value = raw_contract.get("production_roots")
    roots: list[str] = []
    if not isinstance(roots_value, list) or not roots_value:
        findings.append(_finding("ENV_ALIAS_ROOTS_INVALID"))
    else:
        for index, value in enumerate(roots_value):
            subject = f"environment_alias_contract.production_roots[{index}]"
            if not _safe_relative_path(value) or value not in valid_roots:
                findings.append(_finding("ENV_ALIAS_ROOTS_INVALID", subject=subject))
                continue
            if value in roots:
                findings.append(_finding("ENV_ALIAS_ROOT_DUPLICATE", path=value))
                continue
            roots.append(value)

    central_value = raw_contract.get("central_owner")
    central_owner = str(central_value) if _safe_relative_path(central_value) else ""
    if not central_owner or central_owner not in owned_paths:
        findings.append(_finding("ENV_ALIAS_OWNER_INVALID", path=central_owner or "."))

    def legacy_key_set(field: str, *, required: bool) -> frozenset[str]:
        value = raw_contract.get(field)
        if not isinstance(value, list) or (required and not value):
            findings.append(_finding("ENV_ALIAS_KEYS_INVALID", subject=field))
            return frozenset()
        accepted: set[str] = set()
        for index, item in enumerate(value):
            subject = f"environment_alias_contract.{field}[{index}]"
            if (
                not isinstance(item, str)
                or not item.startswith(_LEGACY_ENV_PREFIXES)
                or item in {"MOLTBOT_", "CLAWDBOT_"}
            ):
                findings.append(_finding("ENV_ALIAS_KEYS_INVALID", subject=subject))
                continue
            if item in accepted:
                findings.append(_finding("ENV_ALIAS_KEY_DUPLICATE", subject=item))
            accepted.add(item)
        return frozenset(accepted)

    supported = legacy_key_set("supported_legacy_keys", required=True)
    supported_dynamic = legacy_key_set("supported_dynamic_legacy_keys", required=False)
    rejected = legacy_key_set("rejected_legacy_keys", required=False)
    for key in sorted(supported & rejected):
        findings.append(_finding("ENV_ALIAS_KEY_CONFLICT", subject=key))
    for key in sorted(supported_dynamic & (supported | rejected)):
        findings.append(_finding("ENV_ALIAS_KEY_CONFLICT", subject=key))

    exception_entries = raw_contract.get("direct_read_exceptions", [])
    exceptions: set[str] = set()
    if not isinstance(exception_entries, list):
        findings.append(_finding("ENV_ALIAS_EXCEPTIONS_INVALID"))
        exception_entries = []
    for index, entry in enumerate(exception_entries):
        subject = f"environment_alias_contract.direct_read_exceptions[{index}]"
        if not isinstance(entry, Mapping):
            findings.append(_finding("ENV_ALIAS_EXCEPTIONS_INVALID", subject=subject))
            continue
        for key in sorted(set(entry) - _ENV_ALIAS_EXCEPTION_KEYS):
            findings.append(_finding("POLICY_UNKNOWN_KEY", subject=f"{subject}.{key}"))
        _validate_review_metadata(entry, path=subject, findings=findings)
        path_value = entry.get("path")
        if not _safe_relative_path(path_value) or path_value not in owned_paths:
            findings.append(
                _finding("ENV_ALIAS_EXCEPTION_PATH_INVALID", subject=subject)
            )
            continue
        path = str(path_value)
        if path == central_owner or path in exceptions:
            findings.append(_finding("ENV_ALIAS_EXCEPTION_DUPLICATE", path=path))
        exceptions.add(path)

    return EnvironmentAliasContract(
        production_roots=tuple(roots),
        central_owner=central_owner,
        supported_legacy_keys=supported,
        supported_dynamic_legacy_keys=supported_dynamic,
        rejected_legacy_keys=rejected,
        direct_read_exceptions=frozenset(exceptions),
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
    environment_alias_contract = _validate_environment_alias_contract(
        policy.get("environment_alias_contract"),
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
        environment_alias_contract=environment_alias_contract,
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
        # IMPORTANT: nested __init__ owns its package base, while repo-root __init__ uses
        # a sentinel module name that is not a package component; confusing either case
        # creates false mismatches and blocks valid governed helper edges.
        if self.path == "__init__.py":
            package_parts: list[str] = []
        elif self.path.endswith("/__init__.py"):
            package_parts = self.module.split(".")
        else:
            package_parts = self.module.split(".")[:-1]
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


_ENV_ORIGIN_OS_MODULE = "os_module"
_ENV_ORIGIN_GETENV = "getenv"
_ENV_ORIGIN_MAPPING = "environ"
_ENV_ORIGIN_LEGACY_SIGNAL = "legacy_signal"
_ENV_ORIGIN_BUILTINS_MODULE = "builtins_module"
_ENV_ORIGIN_OTHER = "other"
_ENV_ORIGIN_UNBOUND = "unbound"
_ENV_LAZY_BUILTIN_NAMES = frozenset(
    {"enumerate", "filter", "iter", "map", "reversed", "zip"}
)
_EnvironmentControlPath = tuple[tuple[int, str], ...]
_EnvironmentBindingSource = ast.expr | tuple[ast.expr, ...]


@dataclass(frozen=True)
class _EnvironmentBindingEvent:
    position: tuple[int, int]
    control_path: _EnvironmentControlPath
    fixed_origins: frozenset[str] | None = None
    value: _EnvironmentBindingSource | None = None
    region_limited: bool = False
    replaces_branch: bool = False


@dataclass
class _EnvironmentScopeFacts:
    parent: int | None
    root: ast.AST
    base_origins: dict[str, set[str]]
    origins: dict[str, set[str]]
    assignments: list[tuple[str, _EnvironmentBindingSource]]
    binding_events: dict[str, list[_EnvironmentBindingEvent]]
    wildcard_import_events: list[_EnvironmentBindingEvent]
    global_names: set[str]
    nonlocal_names: set[str]


class _EnvironmentScopeIndex(ast.NodeVisitor):
    """Map environment reads to lexical scopes without executing production code."""

    def __init__(self, tree: ast.AST) -> None:
        self.scopes = [
            _EnvironmentScopeFacts(
                None,
                tree,
                defaultdict(set),
                defaultdict(set),
                [],
                defaultdict(list),
                [],
                set(),
                set(),
            )
        ]
        self.node_scopes: dict[int, int] = {}
        self.node_control_paths: dict[int, _EnvironmentControlPath] = {}
        self._event_origin_cache: dict[int, frozenset[str]] = {}
        self._resolving_events: set[int] = set()
        self._current = 0
        self._control_path: list[tuple[int, str]] = []
        self.visit(tree)

    def visit(self, node: ast.AST) -> Any:
        self.node_scopes[id(node)] = self._current
        self.node_control_paths[id(node)] = tuple(self._control_path)
        return super().visit(node)

    def _new_scope(self, root: ast.AST, parent: int) -> int:
        scope_id = len(self.scopes)
        self.scopes.append(
            _EnvironmentScopeFacts(
                parent,
                root,
                defaultdict(set),
                defaultdict(set),
                [],
                defaultdict(list),
                [],
                set(),
                set(),
            )
        )
        return scope_id

    def _visit_region(
        self, owner: ast.AST, label: str, nodes: Iterable[ast.AST]
    ) -> None:
        self._control_path.append((id(owner), label))
        for child in nodes:
            self.visit(child)
        self._control_path.pop()

    def _nested_lexical_parent(self) -> int:
        parent = self._current
        # IMPORTANT: function, lambda, comprehension, and nested-class name resolution skips class
        # namespaces. Treating a class attribute as a closure binding hides real module env reads.
        while isinstance(self.scopes[parent].root, ast.ClassDef):
            enclosing = self.scopes[parent].parent
            if enclosing is None:
                break
            parent = enclosing
        return parent

    def _visit_function_header(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)
        parent = self._current
        parent_control_path = self._control_path
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        for statement in node.body:
            self.visit(statement)
        self._control_path = parent_control_path
        self._current = parent

    def visit_TypeAlias(self, node: Any) -> None:
        parent = self._current
        parent_control_path = self._control_path
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self.visit(node.value)
        self._control_path = parent_control_path
        self._current = parent

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)
        parent = self._current
        parent_control_path = self._control_path
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        for statement in node.body:
            self.visit(statement)
        self._control_path = parent_control_path
        self._current = parent

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        parent = self._current
        parent_control_path = self._control_path
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        self.visit(node.body)
        self._control_path = parent_control_path
        self._current = parent

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        parent = self._current
        parent_control_path = self._control_path
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        for statement in node.body:
            self.visit(statement)
        self._control_path = parent_control_path
        self._current = parent

    def _visit_comprehension_scope(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        generators = node.generators
        if not generators:
            return
        parent = self._current
        parent_control_path = self._control_path
        # IMPORTANT: Python evaluates only the first comprehension iterable in the enclosing
        # scope. Moving it into the child scope lets the target shadow a real `os` read.
        self.visit(generators[0].iter)
        self._current = self._new_scope(node, self._nested_lexical_parent())
        self._control_path = []
        for index, generator in enumerate(generators):
            self.node_scopes[id(generator)] = self._current
            self.node_control_paths[id(generator)] = tuple(self._control_path)
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self._control_path = parent_control_path
        self._current = parent

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_scope(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_scope(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_scope(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_scope(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", node.body)
        self._visit_region(node, "else", node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", node.body)
        self._visit_region(node, "else", node.orelse)

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._visit_region(node, "body", (node.target, *node.body))
        self._visit_region(node, "else", node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_region(node, "try-body", node.body)
        for index, handler in enumerate(node.handlers):
            self._visit_region(node, f"try-handler:{index}", (handler,))
        self._visit_region(node, "try-else", node.orelse)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_TryStar(self, node: Any) -> None:
        self.visit_Try(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._visit_region(node, "body", (node.body,))
        self._visit_region(node, "else", (node.orelse,))

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not node.values:
            return
        self.visit(node.values[0])
        for index, value in enumerate(node.values[1:], start=1):
            self._visit_region(node, f"value:{index}", (value,))

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for index, case in enumerate(node.cases):
            self._visit_region(
                node,
                f"case:{index}",
                tuple(
                    child
                    for child in (case.pattern, case.guard, *case.body)
                    if child is not None
                ),
            )


def _environment_node_position(node: ast.AST) -> tuple[int, int]:
    return (
        int(getattr(node, "lineno", 0)),
        int(getattr(node, "col_offset", 0)),
    )


def _environment_end_position(node: ast.AST) -> tuple[int, int]:
    return (
        int(getattr(node, "end_lineno", getattr(node, "lineno", 0))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))),
    )


def _environment_control_paths_compatible(
    left: _EnvironmentControlPath, right: _EnvironmentControlPath
) -> bool:
    left_regions = dict(left)
    right_regions = dict(right)
    return all(
        owner not in right_regions
        or label == right_regions[owner]
        or (
            label == "try-body"
            and (
                right_regions[owner] == "try-else"
                or right_regions[owner].startswith("try-handler:")
            )
        )
        or (
            right_regions[owner] == "try-body"
            and (label == "try-else" or label.startswith("try-handler:"))
        )
        for owner, label in left_regions.items()
    )


def _environment_control_path_dominates(
    event: _EnvironmentControlPath, use: _EnvironmentControlPath
) -> bool:
    if len(event) > len(use):
        return False
    for index, (event_owner, event_label) in enumerate(event):
        use_owner, use_label = use[index]
        if event_owner != use_owner:
            return False
        if event_label == use_label:
            continue
        if (
            index == len(event) - 1
            and event_label == "try-body"
            and use_label == "try-else"
        ):
            continue
        return False
    return True


def _environment_event_origins(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    event: _EnvironmentBindingEvent,
) -> frozenset[str]:
    cache_key = id(event)
    cached = index._event_origin_cache.get(cache_key)
    if cached is not None:
        return cached
    if event.fixed_origins is not None:
        index._event_origin_cache[cache_key] = event.fixed_origins
        return event.fixed_origins
    if event.value is None or cache_key in index._resolving_events:
        return frozenset({_ENV_ORIGIN_OTHER})
    index._resolving_events.add(cache_key)
    try:
        origins: set[str] = set()
        sources = event.value if isinstance(event.value, tuple) else (event.value,)
        for source in sources:
            # CRITICAL: a comprehension's first iterable is evaluated in its enclosing scope.
            # Resolving that source in the target's child scope lets a same-name target shadow
            # its own canonical getter and silently bypass the raw environment-read policy.
            source_scope = index.node_scopes.get(id(source), scope_id)
            if _is_os_module_expression(source, index, source_scope):
                origins.add(_ENV_ORIGIN_OS_MODULE)
            if _is_getenv_expression(source, index, source_scope):
                origins.add(_ENV_ORIGIN_GETENV)
            if _is_env_mapping_expression(source, index, source_scope):
                origins.add(_ENV_ORIGIN_MAPPING)
            for builtin_name in _ENV_LAZY_BUILTIN_NAMES:
                if _is_proven_builtin_callable(
                    source, builtin_name, index, source_scope
                ):
                    origins.add(_builtin_callable_origin(builtin_name))
            if _contains_legacy_env_signal(source, index, source_scope):
                origins.add(_ENV_ORIGIN_LEGACY_SIGNAL)
        result = frozenset(origins or {_ENV_ORIGIN_OTHER})
        index._event_origin_cache[cache_key] = result
        return result
    finally:
        index._resolving_events.remove(cache_key)


def _environment_binding_states_at(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    name: str,
    node: ast.AST,
) -> set[str | None]:
    facts = index.scopes[scope_id]
    dominating_states: set[str | None] = {None}
    branch_states: dict[_EnvironmentControlPath, set[str | None]] = {}
    position = _environment_node_position(node)
    control_path = index.node_control_paths.get(id(node), ())
    events = sorted(
        (*facts.binding_events.get(name, ()), *facts.wildcard_import_events),
        key=lambda event: event.position,
    )
    for event in events:
        if event.position > position:
            break
        if event.region_limited and not _environment_control_path_dominates(
            event.control_path, control_path
        ):
            continue
        if not _environment_control_paths_compatible(event.control_path, control_path):
            continue
        origins = set(_environment_event_origins(index, scope_id, event))
        if _environment_control_path_dominates(event.control_path, control_path):
            dominating_states = set(origins)
            branch_states.clear()
            if not origins:
                dominating_states.add(None)
        else:
            # IMPORTANT: a successful delete after a same-region bind replaces that branch's
            # value. Other writes remain a conservative union because evaluating a later right
            # side can fail before rebinding and leave the earlier origin reachable.
            branch_origins: set[str | None] = set(origins)
            if not branch_origins:
                branch_origins.add(None)
            if event.replaces_branch or not any(
                label == "try-body" for _, label in event.control_path
            ):
                branch_states[event.control_path] = branch_origins
            else:
                branch_states.setdefault(event.control_path, set()).update(
                    branch_origins
                )
    return dominating_states.union(*(states for states in branch_states.values()))


def _environment_origins_at(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    name: str,
    node: ast.AST,
) -> set[str]:
    return {
        state
        for state in _environment_binding_states_at(index, scope_id, name, node)
        if state is not None
    }


def _scope_has_origin(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    name: str,
    origin: str,
    node: ast.AST,
) -> bool:
    current: int | None = scope_id
    use_scope = scope_id
    while current is not None:
        facts = index.scopes[current]
        if current != 0 and (
            name in facts.global_names or name in facts.nonlocal_names
        ):
            states = _environment_binding_states_at(index, current, name, node)
            if origin in states:
                return True
            if None not in states:
                return False
            current = 0 if name in facts.global_names else facts.parent
            continue
        if name in facts.origins:
            if current == use_scope:
                states = _environment_binding_states_at(index, current, name, node)
                if origin in states:
                    return True
                if isinstance(facts.root, ast.ClassDef) and None in states:
                    # IMPORTANT: class bodies resolve an absent/deleted local through the
                    # enclosing namespace. Stopping at any syntactic class binding hides the
                    # real module `os` used before, after a dead branch, or after `del`.
                    current = facts.parent
                    continue
                return False
            # IMPORTANT: an enclosing module binding is observed when deferred code runs, not
            # necessarily when it is defined. Retain every reachable origin so a later mutation
            # cannot hide a real process-environment read behind whole-scope ambiguity.
            return origin in facts.origins[name]
        current = facts.parent
    return False


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _static_literal_key(node: ast.AST) -> tuple[bool, object]:
    string_value = _static_string(node)
    if string_value is not None:
        return True, string_value
    if isinstance(node, ast.Constant):
        try:
            hash(node.value)
        except TypeError:
            pass
        else:
            return True, node.value
    if isinstance(node, ast.Tuple):
        values: list[object] = []
        for element in node.elts:
            is_static, value = _static_literal_key(element)
            if not is_static:
                return False, None
            values.append(value)
        return True, tuple(values)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
    ):
        value = node.operand.value
        return True, value if isinstance(node.op, ast.UAdd) else -value
    return False, None


def _static_subscript_selector(node: ast.AST) -> tuple[bool, object]:
    if not isinstance(node, ast.Slice):
        return _static_literal_key(node)
    values: list[int | None] = []
    for part in (node.lower, node.upper, node.step):
        if part is None:
            values.append(None)
            continue
        is_static, value = _static_literal_key(part)
        if not is_static or not isinstance(value, int):
            return False, None
        values.append(value)
    if values[2] == 0:
        return False, None
    return True, slice(*values)


def _static_dict_value_candidates(
    node: ast.AST,
    selector: object,
    index: _EnvironmentScopeIndex | None = None,
    scope_id: int = 0,
    use: ast.AST | None = None,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.expr, ...] | None:
    if isinstance(node, ast.Name) and index is not None and use is not None:
        guard = (scope_id, node.id)
        if guard in resolving:
            return None
        sources = _environment_binding_source_expressions_at(
            index, scope_id, node.id, use
        )
        if not sources:
            return None
        bound_candidates: list[ast.expr] = []
        for source in sources:
            source_scope = index.node_scopes.get(id(source), scope_id)
            nested = _static_dict_value_candidates(
                source,
                selector,
                index,
                source_scope,
                source,
                resolving | {guard},
            )
            if nested is None:
                return None
            bound_candidates.extend(nested)
        return tuple(bound_candidates)
    if not isinstance(node, ast.Dict):
        return None
    possible: list[ast.expr] = []
    unresolved_override = False
    for key, value in reversed(tuple(zip(node.keys, node.values, strict=True))):
        if key is None:
            nested = _static_dict_value_candidates(
                value, selector, index, scope_id, value, resolving
            )
            if nested is None:
                unresolved_override = True
                continue
            if nested:
                possible.extend(nested)
                if not unresolved_override:
                    return tuple(possible)
            continue
        is_static, key_value = _static_literal_key(key)
        if not is_static:
            unresolved_override = True
            continue
        if key_value == selector:
            possible.append(value)
            if not unresolved_override:
                return tuple(possible)
    if possible:
        return tuple(possible)
    return None if unresolved_override else ()


def _environment_binding_source_expressions_at(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    name: str,
    node: ast.AST,
) -> tuple[ast.expr, ...]:
    current: int | None = scope_id
    use_scope = scope_id
    while current is not None:
        facts = index.scopes[current]
        if current != 0 and (
            name in facts.global_names or name in facts.nonlocal_names
        ):
            current = 0 if name in facts.global_names else facts.parent
            continue
        if name not in facts.origins and name not in facts.binding_events:
            current = facts.parent
            continue
        events = facts.binding_events.get(name, ())
        if current != use_scope:
            return tuple(
                source
                for event in events
                if event.value is not None
                for source in (
                    event.value if isinstance(event.value, tuple) else (event.value,)
                )
            )
        position = _environment_node_position(node)
        control_path = index.node_control_paths.get(id(node), ())
        sources: list[ast.expr | None] = [None]
        branch_sources: dict[_EnvironmentControlPath, list[ast.expr | None]] = {}
        for event in events:
            if event.position > position:
                break
            if not _environment_control_paths_compatible(
                event.control_path, control_path
            ):
                continue
            event_sources: list[ast.expr | None]
            if event.value is None:
                event_sources = [None]
            elif isinstance(event.value, tuple):
                event_sources = list(event.value)
            else:
                event_sources = [event.value]
            if _environment_control_path_dominates(event.control_path, control_path):
                sources = event_sources
                branch_sources.clear()
            else:
                # IMPORTANT: on a non-try branch, reaching a later statement proves its earlier
                # assignment completed, so a later write replaces that branch's prior container.
                # Try-body writes remain unions because a caught exception can reach the use
                # without completing the later right-hand side or delete.
                if event.replaces_branch or not any(
                    label == "try-body" for _, label in event.control_path
                ):
                    branch_sources[event.control_path] = event_sources
                else:
                    branch_sources.setdefault(event.control_path, []).extend(
                        event_sources
                    )
        concrete = tuple(
            source
            for source in (
                *sources,
                *(
                    branch_source
                    for values in branch_sources.values()
                    for branch_source in values
                ),
            )
            if source is not None
        )
        if concrete or not isinstance(facts.root, ast.ClassDef):
            return concrete
        current = facts.parent
    return ()


def _expression_has_origin(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    origin: str,
) -> bool:
    return isinstance(node, ast.Name) and _scope_has_origin(
        index, scope_id, node.id, origin, node
    )


def _builtin_callable_origin(name: str) -> str:
    return f"builtin:{name}"


def _scope_resolves_only_to_origin(
    index: _EnvironmentScopeIndex,
    scope_id: int,
    name: str,
    origin: str,
    node: ast.AST,
) -> bool:
    current: int | None = scope_id
    use_scope = scope_id
    while current is not None:
        facts = index.scopes[current]
        if current != 0 and (
            name in facts.global_names or name in facts.nonlocal_names
        ):
            current = 0 if name in facts.global_names else facts.parent
            continue
        if name in facts.origins or name in facts.binding_events:
            if current == use_scope:
                states = _environment_binding_states_at(index, current, name, node)
                if states == {origin}:
                    return True
                if isinstance(facts.root, ast.ClassDef) and states == {None}:
                    current = facts.parent
                    continue
                return False
            return facts.origins.get(name, set()) == {origin}
        current = facts.parent
    return False


def _is_builtins_module_expression(
    node: ast.AST, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    if isinstance(node, ast.Name):
        return _scope_resolves_only_to_origin(
            index, scope_id, node.id, _ENV_ORIGIN_BUILTINS_MODULE, node
        )
    if isinstance(node, ast.NamedExpr):
        return _is_builtins_module_expression(node.value, index, scope_id)
    return False


def _is_proven_builtin_callable(
    node: ast.AST,
    name: str,
    index: _EnvironmentScopeIndex,
    scope_id: int,
) -> bool:
    if isinstance(node, ast.Name):
        states = _environment_binding_states_at(index, scope_id, node.id, node)
        if node.id == name and states.issubset({None, _ENV_ORIGIN_UNBOUND}):
            return True
        return _scope_resolves_only_to_origin(
            index, scope_id, node.id, _builtin_callable_origin(name), node
        )
    if (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and _is_builtins_module_expression(node.value, index, scope_id)
    ):
        return True
    if isinstance(node, ast.NamedExpr):
        return _is_proven_builtin_callable(node.value, name, index, scope_id)
    if isinstance(node, ast.IfExp):
        return all(
            _is_proven_builtin_callable(part, name, index, scope_id)
            for part in (node.body, node.orelse)
        )
    selections = _static_container_selections(node, index, scope_id)
    return bool(selections) and all(
        _is_proven_builtin_callable(
            selected, name, index, index.node_scopes.get(id(selected), scope_id)
        )
        for selected in selections
    )


def _is_proven_lazy_builtin_call(
    node: ast.Call, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    return any(
        _is_proven_builtin_callable(node.func, name, index, scope_id)
        for name in _ENV_LAZY_BUILTIN_NAMES
    )


def _static_container_selections(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.expr, ...]:
    if not isinstance(node, ast.Subscript):
        return ()
    is_static, selector = _static_subscript_selector(node.slice)
    if not is_static:
        return ()
    if isinstance(node.value, (ast.List, ast.Tuple)):
        elements = _resolved_static_sequence_elements(
            node.value, index, scope_id, node.value, resolving
        )
        if elements is None:
            # CRITICAL: an unresolved starred expansion can shift any explicit element into the
            # selected slot. Dropping the whole container lets `[*unknown, os.getenv][0]` bypass
            # direct-read governance; retain every visible value as a conservative candidate.
            return tuple(
                element.value if isinstance(element, ast.Starred) else element
                for element in node.value.elts
            )
        if isinstance(selector, int):
            try:
                return (elements[selector],)
            except IndexError:
                return ()
        if isinstance(selector, slice):
            sliced_elements = list(elements[selector])
            container_type = ast.List if isinstance(node.value, ast.List) else ast.Tuple
            container = container_type(elts=sliced_elements, ctx=ast.Load())
            return (ast.copy_location(container, node),)
        return ()
    if isinstance(node.value, ast.Dict) and not isinstance(selector, slice):
        # IMPORTANT: dict displays apply every unpack and duplicate key in source order, with the
        # last effective value winning. Skipping an unpack can both hide and invent an env reader.
        return (
            _static_dict_value_candidates(
                node.value, selector, index, scope_id, node.value, resolving
            )
            or ()
        )
    if isinstance(node.value, ast.Subscript):
        # CRITICAL: selection provenance is recursive. Resolving only the outermost subscript
        # lets nested dictionaries/lists and statically sliced aliases hide a direct env reader.
        nested_selections: list[ast.expr] = []
        for source in _static_container_selections(
            node.value, index, scope_id, resolving
        ):
            source_scope = index.node_scopes.get(id(source), scope_id)
            synthetic = ast.copy_location(
                ast.Subscript(value=source, slice=node.slice, ctx=ast.Load()), node
            )
            nested_selections.extend(
                _static_container_selections(synthetic, index, source_scope, resolving)
            )
        return tuple(nested_selections)
    if isinstance(node.value, ast.Name):
        guard = (scope_id, node.value.id)
        if guard in resolving:
            return ()
        bound_selections: list[ast.expr] = []
        for source in _environment_binding_source_expressions_at(
            index, scope_id, node.value.id, node
        ):
            source_scope = index.node_scopes.get(id(source), scope_id)
            synthetic = ast.copy_location(
                ast.Subscript(value=source, slice=node.slice, ctx=ast.Load()), node
            )
            bound_selections.extend(
                _static_container_selections(
                    synthetic, index, source_scope, resolving | {guard}
                )
            )
        return tuple(bound_selections)
    return ()


def _is_os_module_expression(
    node: ast.AST, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    if _expression_has_origin(node, index, scope_id, _ENV_ORIGIN_OS_MODULE):
        return True
    if isinstance(node, ast.NamedExpr):
        return _is_os_module_expression(node.value, index, scope_id)
    if isinstance(node, ast.IfExp):
        return any(
            _is_os_module_expression(part, index, scope_id)
            for part in (node.body, node.orelse)
        )
    if isinstance(node, ast.BoolOp):
        # Module objects are truthy: an `and` expression can return the module only when it is
        # last, while any `or` arm may be the selected result.
        values = node.values[-1:] if isinstance(node.op, ast.And) else node.values
        return any(_is_os_module_expression(part, index, scope_id) for part in values)
    return any(
        _is_os_module_expression(
            selected, index, index.node_scopes.get(id(selected), scope_id)
        )
        for selected in _static_container_selections(node, index, scope_id)
    )


def _is_env_mapping_expression(
    node: ast.AST, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    if _expression_has_origin(node, index, scope_id, _ENV_ORIGIN_MAPPING):
        return True
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and _is_os_module_expression(node.value, index, scope_id)
    ):
        return True
    if isinstance(node, ast.NamedExpr):
        return _is_env_mapping_expression(node.value, index, scope_id)
    if isinstance(node, ast.IfExp):
        return any(
            _is_env_mapping_expression(part, index, scope_id)
            for part in (node.body, node.orelse)
        )
    if isinstance(node, ast.BoolOp):
        return any(
            _is_env_mapping_expression(part, index, scope_id) for part in node.values
        )
    return any(
        _is_env_mapping_expression(
            selected, index, index.node_scopes.get(id(selected), scope_id)
        )
        for selected in _static_container_selections(node, index, scope_id)
    )


def _is_getenv_expression(
    node: ast.AST, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    if _expression_has_origin(node, index, scope_id, _ENV_ORIGIN_GETENV):
        return True
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "getenv"
        and _is_os_module_expression(node.value, index, scope_id)
    ):
        return True
    if isinstance(node, ast.NamedExpr):
        return _is_getenv_expression(node.value, index, scope_id)
    if isinstance(node, ast.IfExp):
        return any(
            _is_getenv_expression(part, index, scope_id)
            for part in (node.body, node.orelse)
        )
    if isinstance(node, ast.BoolOp):
        values = node.values[-1:] if isinstance(node.op, ast.And) else node.values
        return any(_is_getenv_expression(part, index, scope_id) for part in values)
    return any(
        _is_getenv_expression(
            selected, index, index.node_scopes.get(id(selected), scope_id)
        )
        for selected in _static_container_selections(node, index, scope_id)
    )


def _is_raw_env_call(
    node: ast.Call, index: _EnvironmentScopeIndex, scope_id: int
) -> bool:
    func = node.func
    if _is_getenv_expression(func, index, scope_id):
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"get", "__getitem__"}
        and _is_env_mapping_expression(func.value, index, scope_id)
    )


def _contains_legacy_env_signal(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
) -> bool:
    static_value = _static_string(node)
    if static_value is not None and static_value.startswith(_LEGACY_ENV_PREFIXES):
        return True
    for part in ast.walk(node):
        if part is not node:
            static_value = _static_string(part)
            if static_value is not None and static_value.startswith(
                _LEGACY_ENV_PREFIXES
            ):
                return True
        if isinstance(part, ast.Name) and (
            "legacy" in part.id.lower()
            or _scope_has_origin(
                index, scope_id, part.id, _ENV_ORIGIN_LEGACY_SIGNAL, part
            )
        ):
            return True
    return False


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        return {name for element in node.elts for name in _target_names(element)}
    return set()


def _type_alias_bound_name(node: ast.AST) -> str | None:
    if type(node).__name__ != "TypeAlias":
        return None
    target = getattr(node, "name", None)
    return target.id if isinstance(target, ast.Name) else None


def _pattern_capture_names(node: ast.pattern) -> set[str]:
    names: set[str] = set()
    for part in ast.walk(node):
        if isinstance(part, (ast.MatchAs, ast.MatchStar)) and part.name:
            names.add(part.name)
        elif isinstance(part, ast.MatchMapping) and part.rest:
            names.add(part.rest)
    return names


def _pattern_is_irrefutable(node: ast.pattern) -> bool:
    if isinstance(node, ast.MatchAs):
        return node.pattern is None or _pattern_is_irrefutable(node.pattern)
    if isinstance(node, ast.MatchOr):
        return any(_pattern_is_irrefutable(pattern) for pattern in node.patterns)
    return False


def _static_sequence_elements(node: ast.AST) -> tuple[ast.expr, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    elements: list[ast.expr] = []
    for element in node.elts:
        if not isinstance(element, ast.Starred):
            elements.append(element)
            continue
        nested = _static_sequence_elements(element.value)
        if nested is None:
            return None
        elements.extend(nested)
    return tuple(elements)


def _static_iterable_elements(node: ast.AST) -> tuple[ast.expr, ...] | None:
    sequence = _static_sequence_elements(node)
    if sequence is not None:
        return sequence
    if isinstance(node, ast.Dict):
        # IMPORTANT: iteration and sequence unpacking over a dict produce keys, never values.
        # Omitting dict keys erases a callable alias carried by a literal mapping key.
        keys: list[ast.expr] = []
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
                keys.append(key)
                continue
            nested = _static_iterable_elements(value)
            if nested is None:
                return None
            keys.extend(nested)
        return tuple(keys)
    if not isinstance(node, ast.Set):
        return None
    elements: list[ast.expr] = []
    for element in node.elts:
        if not isinstance(element, ast.Starred):
            elements.append(element)
            continue
        nested = _static_iterable_elements(element.value)
        if nested is None:
            return None
        elements.extend(nested)
    return tuple(elements)


def _resolved_static_sequence_elements(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    use: ast.AST,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.expr, ...] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        elements: list[ast.expr] = []
        for element in node.elts:
            if not isinstance(element, ast.Starred):
                elements.append(element)
                continue
            nested = _resolved_static_sequence_elements(
                element.value, index, scope_id, element.value, resolving
            )
            if nested is None:
                return None
            elements.extend(nested)
        return tuple(elements)
    if not isinstance(node, ast.Name):
        return None
    guard = (scope_id, node.id)
    if guard in resolving:
        return None
    sources = _environment_binding_source_expressions_at(index, scope_id, node.id, use)
    if len(sources) != 1:
        return None
    source = sources[0]
    source_scope = index.node_scopes.get(id(source), scope_id)
    return _resolved_static_sequence_elements(
        source, index, source_scope, source, resolving | {guard}
    )


def _resolved_static_iterable_elements(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    use: ast.AST,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.expr, ...] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elements: list[ast.expr] = []
        for element in node.elts:
            if not isinstance(element, ast.Starred):
                elements.append(element)
                continue
            nested = _resolved_static_iterable_elements(
                element.value, index, scope_id, element.value, resolving
            )
            if nested is None:
                return None
            elements.extend(nested)
        return tuple(elements)
    if isinstance(node, ast.Dict):
        # CRITICAL: dict iteration and unpack targeting consume effective keys. Resolve bound
        # mapping unpacks through the same use-site source chain instead of treating values as
        # iterable elements or collapsing an assigned mapping to an opaque callable origin.
        keys: list[ast.expr] = []
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
                keys.append(key)
                continue
            nested = _resolved_static_iterable_elements(
                value, index, scope_id, value, resolving
            )
            if nested is None:
                return None
            keys.extend(nested)
        return tuple(keys)
    if not isinstance(node, ast.Name):
        return None
    guard = (scope_id, node.id)
    if guard in resolving:
        return None
    sources = _environment_binding_source_expressions_at(index, scope_id, node.id, use)
    if not sources:
        return None
    resolved_elements: list[ast.expr] = []
    for source in sources:
        source_scope = index.node_scopes.get(id(source), scope_id)
        nested = _resolved_static_iterable_elements(
            source, index, source_scope, source, resolving | {guard}
        )
        if nested is None:
            return None
        resolved_elements.extend(nested)
    return tuple(resolved_elements)


def _unknown_binding_value(node: ast.AST) -> ast.Constant:
    return ast.copy_location(ast.Constant(value=None), node)


def _unpack_possible_sources(
    node: ast.expr,
    index: _EnvironmentScopeIndex | None = None,
    scope_id: int = 0,
    use: ast.AST | None = None,
) -> tuple[ast.expr, ...]:
    if index is not None and use is not None:
        resolved = _resolved_static_iterable_elements(node, index, scope_id, use)
        if resolved is not None:
            return resolved
    if isinstance(node, ast.Dict):
        return _static_iterable_elements(node) or ()
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return (node,)
    sources: list[ast.expr] = []
    for element in node.elts:
        if isinstance(element, ast.Starred):
            sources.extend(_unpack_possible_sources(element.value))
        else:
            sources.append(element)
    return tuple(sources)


def _target_value_bindings(
    target: ast.AST,
    value: ast.expr,
    index: _EnvironmentScopeIndex | None = None,
    scope_id: int = 0,
    use: ast.AST | None = None,
) -> list[tuple[str, _EnvironmentBindingSource]]:
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if isinstance(target, ast.Starred):
        return _target_value_bindings(target.value, value, index, scope_id, use)
    if isinstance(target, (ast.List, ast.Tuple)):
        values = (
            _resolved_static_sequence_elements(value, index, scope_id, use or value)
            if index is not None
            else _static_sequence_elements(value)
        )
        starred_indices = [
            index
            for index, element in enumerate(target.elts)
            if isinstance(element, ast.Starred)
        ]
        if (
            values is not None
            and not starred_indices
            and len(target.elts) == len(values)
        ):
            return [
                binding
                for target_element, value_element in zip(
                    target.elts, values, strict=True
                )
                for binding in _target_value_bindings(
                    target_element, value_element, index, scope_id, use
                )
            ]
        if values is not None and len(starred_indices) == 1:
            starred_index = starred_indices[0]
            suffix_count = len(target.elts) - starred_index - 1
            if len(values) >= len(target.elts) - 1:
                bindings: list[tuple[str, _EnvironmentBindingSource]] = []
                for target_element, value_element in zip(
                    target.elts[:starred_index],
                    values[:starred_index],
                    strict=True,
                ):
                    bindings.extend(
                        _target_value_bindings(
                            target_element, value_element, index, scope_id, use
                        )
                    )
                middle_end = len(values) - suffix_count if suffix_count else len(values)
                # IMPORTANT: an extended-unpack target receives a new list, not one of the
                # captured callables. Propagating a middle element as the target origin invents
                # an executable env-reader alias.
                middle_value = ast.copy_location(
                    ast.List(
                        elts=list(values[starred_index:middle_end]), ctx=ast.Load()
                    ),
                    value,
                )
                bindings.extend(
                    _target_value_bindings(
                        target.elts[starred_index],
                        middle_value,
                        index,
                        scope_id,
                        use,
                    )
                )
                if suffix_count:
                    for target_element, value_element in zip(
                        target.elts[-suffix_count:],
                        values[-suffix_count:],
                        strict=True,
                    ):
                        bindings.extend(
                            _target_value_bindings(
                                target_element,
                                value_element,
                                index,
                                scope_id,
                                use,
                            )
                        )
                return bindings
        if values is not None:
            # A statically ordered but arity-incompatible unpack raises before any target is
            # bound. Retain lexical shadowing without inventing a callable value after the error.
            return [
                (name, _unknown_binding_value(value)) for name in _target_names(target)
            ]
        possible_values = _unpack_possible_sources(value, index, scope_id, use or value)
        if possible_values:
            possible_source: _EnvironmentBindingSource = (
                possible_values[0] if len(possible_values) == 1 else possible_values
            )
            unordered_bindings: list[tuple[str, _EnvironmentBindingSource]] = []
            for target_element in target.elts:
                if isinstance(target_element, ast.Starred):
                    middle_value = ast.copy_location(
                        ast.List(elts=list(possible_values), ctx=ast.Load()), value
                    )
                    unordered_bindings.extend(
                        _target_value_bindings(
                            target_element,
                            middle_value,
                            index,
                            scope_id,
                            use,
                        )
                    )
                    continue
                unordered_bindings.extend(
                    (name, possible_source) for name in _target_names(target_element)
                )
            return unordered_bindings
        # An unresolved unpack still creates local bindings, but no individual target has a
        # statically proven value. Mark it unknown so an outer `os` alias is not resurrected.
        return [(name, _unknown_binding_value(value)) for name in _target_names(target)]
    return []


def _loop_target_value_bindings(
    target: ast.AST,
    iterable: ast.expr,
    index: _EnvironmentScopeIndex | None = None,
    scope_id: int = 0,
) -> list[tuple[str, _EnvironmentBindingSource]]:
    values = (
        _resolved_static_iterable_elements(iterable, index, scope_id, iterable)
        if index is not None
        else _static_iterable_elements(iterable)
    )
    if values is None:
        # An unresolved iterable alias may itself carry the governed dynamic-key or callable
        # provenance. Preserve that conservative union when no literal elements are available.
        return [(name, iterable) for name in _target_names(target)]
    by_name: dict[str, list[ast.expr]] = defaultdict(list)
    for value in values:
        for name, source in _target_value_bindings(
            target, value, index, scope_id, iterable
        ):
            if isinstance(source, tuple):
                by_name[name].extend(source)
            else:
                by_name[name].append(source)
    if not by_name:
        return [
            (name, _unknown_binding_value(iterable)) for name in _target_names(target)
        ]
    # IMPORTANT: loop/comprehension targets receive iterable elements, never the container
    # object. Preserve the union of statically reachable element origins to prevent one-element
    # alias loops from bypassing the raw-read policy.
    return [
        (name, sources[0] if len(sources) == 1 else tuple(sources))
        for name, sources in by_name.items()
    ]


def _node_is_statically_unreachable(
    node: ast.AST, parents: Mapping[int, ast.AST]
) -> bool:
    current = node
    while (parent := parents.get(id(current))) is not None:
        if isinstance(parent, (ast.If, ast.IfExp)) and isinstance(
            parent.test, ast.Constant
        ):
            truthy = bool(parent.test.value)
            in_dead_arm = (
                isinstance(parent, ast.If)
                and (
                    (current in parent.body and not truthy)
                    or (current in parent.orelse and truthy)
                )
            ) or (
                isinstance(parent, ast.IfExp)
                and (
                    (current is parent.body and not truthy)
                    or (current is parent.orelse and truthy)
                )
            )
            if in_dead_arm:
                return True
        if (
            isinstance(parent, ast.While)
            and isinstance(parent.test, ast.Constant)
            and not bool(parent.test.value)
            and current in parent.body
        ):
            return True
        current = parent
    return False


def _expression_resolves_to_node(
    node: ast.AST,
    target: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    resolving: frozenset[tuple[int, str, int]] = frozenset(),
) -> bool:
    if node is target:
        return True
    if isinstance(node, ast.NamedExpr):
        return _expression_resolves_to_node(
            node.value, target, index, scope_id, resolving
        )
    selections = _static_container_selections(node, index, scope_id)
    if selections:
        return any(
            _expression_resolves_to_node(
                selected,
                target,
                index,
                index.node_scopes.get(id(selected), scope_id),
                resolving,
            )
            for selected in selections
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        # CRITICAL: consumer provenance crosses runtime containers and starred expansion.
        # Treating `consume(*[generator])` as the list object hides execution of its walrus body.
        return any(
            _expression_resolves_to_node(
                element.value if isinstance(element, ast.Starred) else element,
                target,
                index,
                index.node_scopes.get(id(element), scope_id),
                resolving,
            )
            for element in node.elts
        )
    if isinstance(node, ast.Dict):
        return any(
            _expression_resolves_to_node(
                member,
                target,
                index,
                index.node_scopes.get(id(member), scope_id),
                resolving,
            )
            for member in (
                *tuple(key for key in node.keys if key is not None),
                *node.values,
            )
        )
    if isinstance(node, ast.IfExp):
        return any(
            _expression_resolves_to_node(part, target, index, scope_id, resolving)
            for part in (node.body, node.orelse)
        )
    if isinstance(node, ast.BoolOp):
        return any(
            _expression_resolves_to_node(part, target, index, scope_id, resolving)
            for part in node.values
        )
    if not isinstance(node, ast.Name):
        return False
    guard = (scope_id, node.id, id(target))
    if guard in resolving:
        return False
    for source in _environment_binding_source_expressions_at(
        index, scope_id, node.id, node
    ):
        source_scope = index.node_scopes.get(id(source), scope_id)
        if _expression_resolves_to_node(
            source, target, index, source_scope, resolving | {guard}
        ):
            return True
    return False


def _bound_generator_consumer_sources(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[tuple[ast.expr, str], ...]:
    if isinstance(node, ast.Attribute) and node.attr in {
        "__next__",
        "send",
        "throw",
    }:
        return ((node.value, node.attr),)
    if isinstance(node, ast.NamedExpr):
        return _bound_generator_consumer_sources(node.value, index, scope_id, resolving)
    selections = _static_container_selections(node, index, scope_id)
    if selections:
        # CRITICAL: bound generator methods remain callable when selected from a static
        # container. Following only direct attributes and names misses `[g.__next__][0]()`.
        return tuple(
            source
            for selected in selections
            for source in _bound_generator_consumer_sources(
                selected,
                index,
                index.node_scopes.get(id(selected), scope_id),
                resolving,
            )
        )
    if not isinstance(node, ast.Name):
        return ()
    guard = (scope_id, node.id)
    if guard in resolving:
        return ()
    sources: list[tuple[ast.expr, str]] = []
    for source in _environment_binding_source_expressions_at(
        index, scope_id, node.id, node
    ):
        source_scope = index.node_scopes.get(id(source), scope_id)
        sources.extend(
            _bound_generator_consumer_sources(
                source, index, source_scope, resolving | {guard}
            )
        )
    return tuple(sources)


def _effective_static_call_arguments(
    node: ast.Call,
    index: _EnvironmentScopeIndex,
    scope_id: int,
) -> tuple[tuple[ast.expr, ...], bool, bool | None]:
    positional: list[ast.expr] = []
    positional_exact = True
    for argument in node.args:
        if not isinstance(argument, ast.Starred):
            positional.append(argument)
            continue
        expanded = _resolved_static_sequence_elements(
            argument.value, index, scope_id, argument.value
        )
        if expanded is None:
            positional_exact = False
            continue
        positional.extend(expanded)

    has_keywords: bool | None = False
    unknown_keywords = False
    for keyword in node.keywords:
        if keyword.arg is not None:
            has_keywords = True
            continue
        if isinstance(keyword.value, ast.Dict) and not keyword.value.keys:
            continue
        unknown_keywords = True
    if has_keywords is False and unknown_keywords:
        has_keywords = None
    return tuple(positional), positional_exact, has_keywords


def _bound_generator_call_may_activate(
    method: str,
    node: ast.Call,
    index: _EnvironmentScopeIndex,
    scope_id: int,
) -> bool:
    positional, positional_exact, has_keywords = _effective_static_call_arguments(
        node, index, scope_id
    )
    if has_keywords is True:
        return False
    if method == "__next__":
        return not positional
    if method != "send":
        return False
    if not positional:
        # An opaque expansion may bind exactly one None argument, so it cannot safely suppress
        # a consumer. Fully resolved non-None or wrong-arity calls remain excluded below.
        return not positional_exact or has_keywords is None
    return (
        len(positional) == 1
        and isinstance(positional[0], ast.Constant)
        and positional[0].value is None
    )


def _generator_consumption_sites(
    candidate: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
) -> tuple[tuple[ast.expr, ast.AST], ...]:
    if isinstance(candidate, (ast.For, ast.AsyncFor, ast.comprehension)):
        return ((candidate.iter, candidate.iter),)
    if isinstance(candidate, ast.YieldFrom):
        return ((candidate.value, candidate),)
    if isinstance(candidate, (ast.Assign, ast.AnnAssign)):
        targets = (
            candidate.targets
            if isinstance(candidate, ast.Assign)
            else (candidate.target,)
        )
        value = candidate.value
        if value is not None and any(
            isinstance(target, (ast.List, ast.Tuple)) for target in targets
        ):
            return ((value, candidate),)
        return ()
    if not isinstance(candidate, ast.Call):
        return ()
    method_sites: list[tuple[ast.expr, ast.AST]] = []
    for receiver, method in _bound_generator_consumer_sources(
        candidate.func, index, scope_id
    ):
        if _bound_generator_call_may_activate(method, candidate, index, scope_id):
            method_sites.append((receiver, candidate))
        # A fresh generator's throw() and send(non-None) raise before entering its body. They
        # cannot be the first operation that activates a comprehension walrus target.
    if _is_proven_lazy_builtin_call(candidate, index, scope_id):
        return tuple(method_sites)
    arguments = tuple(
        argument.value if isinstance(argument, ast.Starred) else argument
        for argument in candidate.args
    ) + tuple(keyword.value for keyword in candidate.keywords)
    # CRITICAL: an unknown call receiving the original generator may consume it. Restricting
    # activation to a small builtin spelling list creates a policy bypass; the activation caller
    # excludes only proven lazy wrappers because constructing them does not advance it.
    return (*method_sites, *((argument, candidate) for argument in arguments))


def _walrus_comprehension_activation(
    tree: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    node: ast.NamedExpr,
    parents: Mapping[int, ast.AST],
) -> tuple[int, tuple[int, int], _EnvironmentControlPath] | None:
    scope = index.scopes[scope_id]
    owner = scope.root
    if not isinstance(
        owner, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    ):
        return (
            scope_id,
            _environment_end_position(node),
            index.node_control_paths.get(id(node), ()),
        )
    target_scope = scope.parent
    while target_scope is not None and isinstance(
        index.scopes[target_scope].root,
        (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
    ):
        target_scope = index.scopes[target_scope].parent
    if target_scope is None:
        return None
    definitely_executes = True
    reached_walrus = False
    for generator in owner.generators:
        if any(part is node for part in ast.walk(generator.iter)):
            reached_walrus = True
            break
        elements = _static_iterable_elements(generator.iter)
        if elements == ():
            return None
        if elements is None:
            definitely_executes = False
        for condition in generator.ifs:
            if any(part is node for part in ast.walk(condition)):
                reached_walrus = True
                break
            if isinstance(condition, ast.Constant) and not bool(condition.value):
                return None
            if not (isinstance(condition, ast.Constant) and condition.value is True):
                definitely_executes = False
        if reached_walrus:
            break
    activation_node: ast.AST = owner
    if isinstance(owner, ast.GeneratorExp):
        consumers: list[ast.AST] = []
        for candidate in ast.walk(tree):
            candidate_scope = index.node_scopes.get(id(candidate), target_scope)
            sites = _generator_consumption_sites(candidate, index, candidate_scope)
            if not sites or _node_is_statically_unreachable(candidate, parents):
                continue
            if index.node_scopes.get(id(candidate), 0) != target_scope or (
                not any(part is owner for part in ast.walk(candidate))
                and _environment_end_position(candidate)
                <= _environment_end_position(owner)
            ):
                continue
            for consumed, activation in sites:
                consumed_scope = index.node_scopes.get(id(consumed), target_scope)
                if _expression_resolves_to_node(consumed, owner, index, consumed_scope):
                    consumers.append(activation)
        if not consumers:
            return None
        activation_node = min(consumers, key=_environment_node_position)
    control_path = index.node_control_paths.get(id(activation_node), ())
    if not definitely_executes:
        control_path = (*control_path, (id(owner), "walrus-iteration"))
    # IMPORTANT: comprehension walrus targets belong to the nearest containing
    # non-comprehension scope, but only an executed iteration binds them. Treating eager-empty
    # and unconsumed generators as assignments invents aliases. Generator consumers must resolve
    # to the original generator value at their exact use site; name-only scans accept dead/rebound
    # `next()` calls and miss aliases, loops, and materialization.
    return target_scope, _environment_end_position(activation_node), control_path


def _build_environment_scope_index(tree: ast.AST) -> _EnvironmentScopeIndex:
    index = _EnvironmentScopeIndex(tree)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    pending_named_exprs: list[tuple[ast.NamedExpr, int]] = []
    for node in ast.walk(tree):
        scope_id = index.node_scopes.get(id(node), 0)
        facts = index.scopes[scope_id]
        control_path = index.node_control_paths.get(id(node), ())
        if isinstance(node, ast.Global):
            facts.global_names.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            facts.nonlocal_names.update(node.names)
        elif (type_alias_name := _type_alias_bound_name(node)) is not None:
            # IMPORTANT: Python 3.12+ `type X = ...` binds X at execution time. Ignoring the
            # target resurrects an earlier `os`/builtin and bypasses exact registry proof.
            facts.base_origins[type_alias_name].add(_ENV_ORIGIN_OTHER)
            facts.binding_events[type_alias_name].append(
                _EnvironmentBindingEvent(
                    _environment_end_position(node),
                    control_path,
                    frozenset({_ENV_ORIGIN_OTHER}),
                )
            )
            annotation_scope = next(
                (
                    candidate
                    for candidate, child in enumerate(index.scopes)
                    if child.root is node
                ),
                None,
            )
            if annotation_scope is not None:
                annotation_facts = index.scopes[annotation_scope]
                for type_param in getattr(node, "type_params", ()):
                    parameter_name = getattr(type_param, "name", None)
                    if not isinstance(parameter_name, str):
                        continue
                    # IMPORTANT: a type-alias RHS executes lazily in its annotation scope, where
                    # type parameters shadow enclosing imports. Resolving the RHS directly in the
                    # module/function/class scope invents environment reads such as `Alias[os]`.
                    annotation_facts.base_origins[parameter_name].add(_ENV_ORIGIN_OTHER)
                    annotation_facts.binding_events[parameter_name].append(
                        _EnvironmentBindingEvent(
                            (0, 0), (), frozenset({_ENV_ORIGIN_OTHER})
                        )
                    )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # IMPORTANT: an exception target shadows aliases only inside its handler and Python
            # deletes it automatically on exit. Letting the temporary binding escape the region
            # rejects a restored builtin and misclassifies post-handler environment reads.
            facts.base_origins[node.name].add(_ENV_ORIGIN_UNBOUND)
            facts.binding_events[node.name].append(
                _EnvironmentBindingEvent(
                    _environment_node_position(node),
                    control_path,
                    frozenset({_ENV_ORIGIN_OTHER}),
                    region_limited=True,
                )
            )
            # CRITICAL: CPython clears the exception target at handler exit even when the body
            # reassigns that same name. This final tombstone must replace all handler-branch
            # writes or a restored builtin remains falsely shadowed after the try statement.
            facts.binding_events[node.name].append(
                _EnvironmentBindingEvent(
                    (
                        _environment_end_position(node)[0],
                        _environment_end_position(node)[1] + 1,
                    ),
                    control_path,
                    frozenset({_ENV_ORIGIN_UNBOUND}),
                    replaces_branch=True,
                )
            )
        if (
            isinstance(node, ast.Match)
            and node.cases
            and _pattern_is_irrefutable(node.cases[0].pattern)
        ):
            for capture_name in _pattern_capture_names(node.cases[0].pattern):
                # IMPORTANT: an irrefutable first pattern captures before its guard and the name
                # survives guard failure and match exit. Confining it to the case region revives
                # an outer environment alias in later cases and after the statement.
                facts.base_origins[capture_name].add(_ENV_ORIGIN_OTHER)
                facts.binding_events[capture_name].append(
                    _EnvironmentBindingEvent(
                        _environment_end_position(node.cases[0].pattern),
                        control_path,
                        frozenset({_ENV_ORIGIN_OTHER}),
                    )
                )
        implicit_bindings: list[tuple[str, ast.AST]] = []
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            implicit_bindings.append((node.name, node))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            implicit_bindings.append((node.rest, node))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            implicit_bindings.extend(
                (name, item.optional_vars)
                for item in node.items
                if item.optional_vars is not None
                for name in _target_names(item.optional_vars)
            )
        for implicit_name, implicit_activation in implicit_bindings:
            # IMPORTANT: pattern captures and context-manager targets bind before their guarded
            # body executes. Omitting these implicit binders resurrects outer env aliases and
            # lets later registry replacement evade the immutable single-binding check.
            facts.base_origins[implicit_name].add(_ENV_ORIGIN_OTHER)
            facts.binding_events[implicit_name].append(
                _EnvironmentBindingEvent(
                    _environment_end_position(implicit_activation),
                    index.node_control_paths.get(id(implicit_activation), control_path),
                    frozenset({_ENV_ORIGIN_OTHER}),
                )
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            facts.base_origins[node.name].add(_ENV_ORIGIN_OTHER)
            facts.binding_events[node.name].append(
                _EnvironmentBindingEvent(
                    _environment_end_position(node),
                    control_path,
                    frozenset({_ENV_ORIGIN_OTHER}),
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == "os":
                    imported_origin = _ENV_ORIGIN_OS_MODULE
                elif alias.name == "builtins":
                    imported_origin = _ENV_ORIGIN_BUILTINS_MODULE
                else:
                    imported_origin = _ENV_ORIGIN_OTHER
                facts.base_origins[bound].add(imported_origin)
                facts.binding_events[bound].append(
                    _EnvironmentBindingEvent(
                        _environment_end_position(node),
                        control_path,
                        frozenset({imported_origin}),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == "os" and alias.name in {"getenv", "environ"}:
                    imported_origin = (
                        _ENV_ORIGIN_GETENV
                        if alias.name == "getenv"
                        else _ENV_ORIGIN_MAPPING
                    )
                    facts.base_origins[bound].add(imported_origin)
                    facts.binding_events[bound].append(
                        _EnvironmentBindingEvent(
                            _environment_end_position(node),
                            control_path,
                            frozenset({imported_origin}),
                        )
                    )
                elif (
                    node.module == "builtins" and alias.name in _ENV_LAZY_BUILTIN_NAMES
                ):
                    imported_origin = _builtin_callable_origin(alias.name)
                    facts.base_origins[bound].add(imported_origin)
                    facts.binding_events[bound].append(
                        _EnvironmentBindingEvent(
                            _environment_end_position(node),
                            control_path,
                            frozenset({imported_origin}),
                        )
                    )
                elif node.module == "os" and alias.name == "*":
                    for star_name, star_origin in (
                        ("getenv", _ENV_ORIGIN_GETENV),
                        ("environ", _ENV_ORIGIN_MAPPING),
                    ):
                        facts.base_origins[star_name].add(star_origin)
                        facts.binding_events[star_name].append(
                            _EnvironmentBindingEvent(
                                _environment_end_position(node),
                                control_path,
                                frozenset({star_origin}),
                            )
                        )
                elif alias.name == "*":
                    # CRITICAL: an unknown wildcard import can replace any existing name,
                    # including `frozenset` and immutable registry variables. Treat it as a
                    # scope-wide binding event; recording only the literal name `*` is fail-open.
                    facts.wildcard_import_events.append(
                        _EnvironmentBindingEvent(
                            _environment_end_position(node),
                            control_path,
                            frozenset({_ENV_ORIGIN_OTHER}),
                        )
                    )
                else:
                    facts.base_origins[bound].add(_ENV_ORIGIN_OTHER)
                    facts.binding_events[bound].append(
                        _EnvironmentBindingEvent(
                            _environment_end_position(node),
                            control_path,
                            frozenset({_ENV_ORIGIN_OTHER}),
                        )
                    )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            child_scope = next(
                (
                    candidate
                    for candidate, child in enumerate(index.scopes)
                    if child.root is node
                ),
                None,
            )
            if child_scope is not None:
                child_facts = index.scopes[child_scope]
                arguments = (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
                for argument in arguments:
                    child_facts.base_origins[argument.arg].add(_ENV_ORIGIN_OTHER)
                    child_facts.binding_events[argument.arg].append(
                        _EnvironmentBindingEvent(
                            (0, 0), (), frozenset({_ENV_ORIGIN_OTHER})
                        )
                    )
                if node.args.vararg is not None:
                    child_facts.base_origins[node.args.vararg.arg].add(
                        _ENV_ORIGIN_OTHER
                    )
                    child_facts.binding_events[node.args.vararg.arg].append(
                        _EnvironmentBindingEvent(
                            (0, 0), (), frozenset({_ENV_ORIGIN_OTHER})
                        )
                    )
                if node.args.kwarg is not None:
                    child_facts.base_origins[node.args.kwarg.arg].add(_ENV_ORIGIN_OTHER)
                    child_facts.binding_events[node.args.kwarg.arg].append(
                        _EnvironmentBindingEvent(
                            (0, 0), (), frozenset({_ENV_ORIGIN_OTHER})
                        )
                    )

        bindings: list[tuple[str, _EnvironmentBindingSource]] = []
        activation_node: ast.AST = node
        activation_position = _environment_end_position(node)
        event_control_path = control_path
        if isinstance(node, ast.Assign):
            bindings = [
                binding
                for target in node.targets
                for binding in _target_value_bindings(
                    target, node.value, index, scope_id, node.value
                )
            ]
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                bindings = _target_value_bindings(
                    node.target, node.value, index, scope_id, node.value
                )
        elif isinstance(node, ast.NamedExpr):
            # Walrus activation can depend on consumers and rebindings later in source order.
            # Defer it until ordinary binding events for the whole tree have been indexed.
            pending_named_exprs.append((node, scope_id))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bindings = _loop_target_value_bindings(
                node.target, node.iter, index, scope_id
            )
            activation_node = node.iter
            activation_position = _environment_end_position(activation_node)
            event_control_path = index.node_control_paths.get(
                id(node.target), control_path
            )
            if isinstance(node, ast.comprehension):
                # IMPORTANT: comprehension result expressions appear before their `for` clause
                # in source order but execute after target binding. A source-column timestamp
                # therefore hides canonical aliases used by the result expression.
                activation_position = (0, 0)
        if bindings:
            for target_name, binding_value in bindings:
                facts.assignments.append((target_name, binding_value))
                facts.binding_events[target_name].append(
                    _EnvironmentBindingEvent(
                        activation_position,
                        event_control_path,
                        value=binding_value,
                    )
                )
        elif isinstance(node, ast.Delete):
            for delete_target in node.targets:
                for name in _target_names(delete_target):
                    delete_origins = (
                        frozenset()
                        if isinstance(facts.root, ast.ClassDef)
                        and name not in facts.global_names
                        and name not in facts.nonlocal_names
                        else frozenset({_ENV_ORIGIN_UNBOUND})
                    )
                    facts.base_origins[name].update(
                        delete_origins or {_ENV_ORIGIN_OTHER}
                    )
                    facts.binding_events[name].append(
                        _EnvironmentBindingEvent(
                            _environment_end_position(node),
                            control_path,
                            delete_origins,
                            # A caught exception can leave a prior try-body binding live before
                            # this delete executes. Outside try bodies, reaching the later use
                            # proves the branch-local delete completed and replaced that value.
                            replaces_branch=not any(
                                label == "try-body" for _, label in control_path
                            ),
                        )
                    )
        elif isinstance(node, ast.AugAssign):
            for name in _target_names(node.target):
                facts.base_origins[name].add(_ENV_ORIGIN_OTHER)
                facts.binding_events[name].append(
                    _EnvironmentBindingEvent(
                        _environment_end_position(node),
                        control_path,
                        frozenset({_ENV_ORIGIN_OTHER}),
                    )
                )

    for facts in index.scopes:
        for events in facts.binding_events.values():
            events.sort(key=lambda event: event.position)
        facts.wildcard_import_events.sort(key=lambda event: event.position)

    # Seed exact import/binder origins before resolving deferred comprehension consumers. This is
    # enough to distinguish canonical builtins aliases from shadowed callables without pretending
    # that assignment-derived origins have already reached their later fixed point.
    index._event_origin_cache.clear()
    for facts in index.scopes:
        facts.origins = defaultdict(
            set, {name: set(origins) for name, origins in facts.base_origins.items()}
        )

    for node, original_scope_id in pending_named_exprs:
        activation = _walrus_comprehension_activation(
            tree, index, original_scope_id, node, parents
        )
        if activation is None:
            continue
        target_scope, activation_position, event_control_path = activation
        facts = index.scopes[target_scope]
        bindings = _target_value_bindings(
            node.target, node.value, index, original_scope_id, node.value
        )
        for target_name, binding_value in bindings:
            facts.assignments.append((target_name, binding_value))
            facts.binding_events[target_name].append(
                _EnvironmentBindingEvent(
                    activation_position,
                    event_control_path,
                    value=binding_value,
                )
            )

    for facts in index.scopes:
        for events in facts.binding_events.values():
            events.sort(key=lambda event: event.position)
        facts.wildcard_import_events.sort(key=lambda event: event.position)

    # IMPORTANT: pending comprehension activations scan later calls before the fixed-point origin
    # pass. Those provisional lookups can cache OTHER for newly added walrus events; retaining that
    # cache makes all but the last activated reader disappear from exact use-site resolution.
    index._event_origin_cache.clear()
    for facts in index.scopes:
        facts.origins = defaultdict(
            set, {name: set(origins) for name, origins in facts.base_origins.items()}
        )

    changed = True
    while changed:
        changed = False
        for scope_id, facts in enumerate(index.scopes):
            calculated = defaultdict(
                set,
                {name: set(origins) for name, origins in facts.base_origins.items()},
            )
            for target, value in facts.assignments:
                new_origins: set[str] = set()
                sources = value if isinstance(value, tuple) else (value,)
                for source in sources:
                    source_scope = index.node_scopes.get(id(source), scope_id)
                    if _is_os_module_expression(source, index, source_scope):
                        new_origins.add(_ENV_ORIGIN_OS_MODULE)
                    if _is_getenv_expression(source, index, source_scope):
                        new_origins.add(_ENV_ORIGIN_GETENV)
                    if _is_env_mapping_expression(source, index, source_scope):
                        new_origins.add(_ENV_ORIGIN_MAPPING)
                    for builtin_name in _ENV_LAZY_BUILTIN_NAMES:
                        if _is_proven_builtin_callable(
                            source, builtin_name, index, source_scope
                        ):
                            # IMPORTANT: canonical lazy builtins stay lazy through ordinary
                            # assignment chains. Collapsing `lazy = builtins.iter` to OTHER makes
                            # a non-consuming wrapper invent generator-walrus activation.
                            new_origins.add(_builtin_callable_origin(builtin_name))
                    # IMPORTANT: signal propagation is lexical. File-wide name sets made an
                    # unrelated function's `key` inherit a legacy loop target and caused false
                    # policy failures.
                    if _contains_legacy_env_signal(source, index, source_scope):
                        new_origins.add(_ENV_ORIGIN_LEGACY_SIGNAL)
                if not new_origins:
                    new_origins.add(_ENV_ORIGIN_OTHER)
                calculated[target].update(new_origins)
            if dict(calculated) != dict(facts.origins):
                facts.origins = calculated
                changed = True
    return index


def _legacy_key_from_expression(node: ast.AST) -> str | None:
    value = _static_string(node)
    if value is not None and value.startswith(_LEGACY_ENV_PREFIXES):
        return value
    return None


def _environment_call_key_nodes(
    node: ast.Call, index: _EnvironmentScopeIndex, scope_id: int
) -> tuple[ast.AST, ...]:
    candidates: list[ast.AST] = []
    for argument in node.args:
        if not isinstance(argument, ast.Starred):
            candidates.append(argument)
            break
        expanded = _resolved_static_sequence_elements(
            argument.value, index, scope_id, argument.value
        )
        if expanded is None:
            unordered = _resolved_static_iterable_elements(
                argument.value, index, scope_id, argument.value
            )
            if unordered is not None:
                if unordered:
                    # A set expansion is unordered; every element can become the effective key.
                    candidates.extend(unordered)
                    break
                continue
            # IMPORTANT: an unknown `*args` may be empty, so both its dynamic signal and a later
            # argument can supply the first effective key. Stopping at syntax position zero lets
            # empty expansions hide a governed legacy read.
            candidates.append(argument.value)
            continue
        if expanded:
            candidates.append(expanded[0])
            break
    for keyword in node.keywords:
        if keyword.arg == "key":
            candidates.append(keyword.value)
            continue
        if keyword.arg is not None:
            continue
        effective = _static_dict_value_candidates(
            keyword.value, "key", index, scope_id, keyword.value
        )
        if effective is not None:
            # CRITICAL: a `**` dict uses the same last-wins merge semantics as a subscript.
            # Walking every duplicate key invents reads that Python has already overwritten.
            candidates.extend(effective)
            continue
        # IMPORTANT: keyword unpacking is a real call-binding path. Preserve the expression as
        # a dynamic signal when its exact `key` entry cannot be resolved statically.
        candidates.append(keyword.value)
    return tuple(candidates)


def _observe_raw_legacy_env_reads(
    path: str,
    tree: ast.AST,
    contract: EnvironmentAliasContract,
) -> tuple[Finding, ...]:
    if path == contract.central_owner:
        return ()
    findings: list[Finding] = []
    known = (
        contract.supported_legacy_keys
        | contract.supported_dynamic_legacy_keys
        | contract.rejected_legacy_keys
    )
    scope_index = _build_environment_scope_index(tree)
    for node in ast.walk(tree):
        scope_id = scope_index.node_scopes.get(id(node), 0)
        key_nodes: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Call) and _is_raw_env_call(node, scope_index, scope_id):
            key_nodes = _environment_call_key_nodes(node, scope_index, scope_id)
        elif isinstance(node, ast.Subscript) and _is_env_mapping_expression(
            node.value, scope_index, scope_id
        ):
            key_nodes = (node.slice,)
        if not key_nodes:
            continue
        direct_keys = {
            key
            for key_node in key_nodes
            if (key := _legacy_key_from_expression(key_node)) is not None
        }
        if direct_keys:
            for key in sorted(direct_keys):
                findings.append(
                    _finding(
                        "ENV_ALIAS_DIRECT_READ",
                        path=path,
                        line=getattr(node, "lineno", 0),
                        subject=key,
                    )
                )
                if key not in known:
                    findings.append(
                        _finding(
                            "ENV_ALIAS_UNKNOWN_KEY",
                            path=path,
                            line=getattr(node, "lineno", 0),
                            subject=key,
                        )
                    )
        elif any(
            _contains_legacy_env_signal(key_node, scope_index, scope_id)
            for key_node in key_nodes
        ):
            findings.append(
                _finding(
                    "ENV_ALIAS_DYNAMIC_READ",
                    path=path,
                    line=getattr(node, "lineno", 0),
                    subject=type(key_nodes[0]).__name__,
                )
            )
    return tuple(findings)


def _statement_binds_name(node: ast.AST, name: str) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        return any(name in _target_names(target) for target in targets)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(
            (alias.asname or alias.name.split(".", 1)[0]) == name
            for alias in node.names
        )
    return _type_alias_bound_name(node) == name


def _static_integer(node: ast.AST) -> int | None:
    is_static, value = _static_literal_key(node)
    return value if is_static and isinstance(value, int) else None


def _iterable_is_definitely_empty(
    node: ast.AST,
    index: _EnvironmentScopeIndex | None,
    scope_id: int,
) -> bool:
    if index is not None:
        elements = _resolved_static_iterable_elements(node, index, scope_id, node)
    else:
        elements = _static_iterable_elements(node)
    if elements == ():
        return True
    if not isinstance(node, ast.Call) or index is None:
        return False
    if not _is_proven_builtin_callable(node.func, "range", index, scope_id):
        return False
    positional, positional_exact, has_keywords = _effective_static_call_arguments(
        node, index, scope_id
    )
    if (
        not positional_exact
        or has_keywords is not False
        or not 1 <= len(positional) <= 3
    ):
        return False
    values = tuple(_static_integer(argument) for argument in positional)
    if any(value is None for value in values):
        return False
    try:
        return len(range(*values)) == 0  # type: ignore[arg-type]
    except (OverflowError, ValueError):
        return False


def _resolved_generator_expressions(
    node: ast.AST,
    index: _EnvironmentScopeIndex,
    scope_id: int,
    resolving: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.GeneratorExp, ...]:
    if isinstance(node, ast.GeneratorExp):
        return (node,)
    if isinstance(node, ast.NamedExpr):
        return _resolved_generator_expressions(node.value, index, scope_id, resolving)
    selections = _static_container_selections(node, index, scope_id)
    if selections:
        return tuple(
            generator
            for selected in selections
            for generator in _resolved_generator_expressions(
                selected,
                index,
                index.node_scopes.get(id(selected), scope_id),
                resolving,
            )
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return tuple(
            generator
            for element in node.elts
            for generator in _resolved_generator_expressions(
                element.value if isinstance(element, ast.Starred) else element,
                index,
                index.node_scopes.get(id(element), scope_id),
                resolving,
            )
        )
    if isinstance(node, ast.Dict):
        return tuple(
            generator
            for member in (
                *tuple(key for key in node.keys if key is not None),
                *node.values,
            )
            for generator in _resolved_generator_expressions(
                member,
                index,
                index.node_scopes.get(id(member), scope_id),
                resolving,
            )
        )
    if isinstance(node, ast.IfExp):
        return tuple(
            generator
            for part in (node.body, node.orelse)
            for generator in _resolved_generator_expressions(
                part, index, scope_id, resolving
            )
        )
    if isinstance(node, ast.BoolOp):
        return tuple(
            generator
            for part in node.values
            for generator in _resolved_generator_expressions(
                part, index, scope_id, resolving
            )
        )
    if not isinstance(node, ast.Name):
        return ()
    guard = (scope_id, node.id)
    if guard in resolving:
        return ()
    return tuple(
        generator
        for source in _environment_binding_source_expressions_at(
            index, scope_id, node.id, node
        )
        for generator in _resolved_generator_expressions(
            source,
            index,
            index.node_scopes.get(id(source), scope_id),
            resolving | {guard},
        )
    )


def _comprehension_may_bind_name(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    name: str,
    index: _EnvironmentScopeIndex | None,
    scope_id: int,
    *,
    consumed: bool,
) -> bool:
    if not node.generators:
        return False
    first_iter = node.generators[0].iter
    if _statement_may_bind_name(first_iter, name, index, scope_id):
        return True
    if isinstance(node, ast.GeneratorExp) and not consumed:
        # Generator construction evaluates only the first iterable. Its filters, nested
        # iterables, element, and walrus targets remain dormant until a consumer advances it.
        return False
    for generator in node.generators:
        if generator.iter is not first_iter and _statement_may_bind_name(
            generator.iter, name, index, scope_id
        ):
            return True
        if _iterable_is_definitely_empty(generator.iter, index, scope_id):
            return False
        for condition in generator.ifs:
            if _statement_may_bind_name(condition, name, index, scope_id):
                return True
            if _static_truth_value(condition) is False:
                return False
    expressions: tuple[ast.AST, ...]
    if isinstance(node, ast.DictComp):
        expressions = (node.key, node.value)
    else:
        expressions = (node.elt,)
    return any(
        _statement_may_bind_name(expression, name, index, scope_id)
        for expression in expressions
    )


def _static_truth_value(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elements = _static_iterable_elements(node)
        return None if elements is None else bool(elements)
    if isinstance(node, ast.Dict):
        if any(key is not None for key in node.keys):
            return True
        return False if not node.keys else None
    if isinstance(node, ast.NamedExpr):
        return _static_truth_value(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _static_truth_value(node.operand)
        return None if operand is None else not operand
    if isinstance(node, ast.BoolOp):
        values = tuple(_static_truth_value(value) for value in node.values)
        if isinstance(node.op, ast.And):
            if False in values:
                return False
            return True if values and all(value is True for value in values) else None
        if True in values:
            return True
        return False if values and all(value is False for value in values) else None
    if isinstance(node, ast.IfExp):
        test = _static_truth_value(node.test)
        if test is not None:
            return _static_truth_value(node.body if test else node.orelse)
        body = _static_truth_value(node.body)
        orelse = _static_truth_value(node.orelse)
        return body if body is not None and body == orelse else None
    return None


def _definition_outer_scope_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda,
) -> tuple[ast.AST, ...]:
    if isinstance(node, ast.ClassDef):
        return (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        )
    arguments = node.args
    annotated = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(() if arguments.vararg is None else (arguments.vararg,)),
        *(() if arguments.kwarg is None else (arguments.kwarg,)),
    )
    annotations = tuple(
        argument.annotation for argument in annotated if argument.annotation is not None
    )
    common = (
        *arguments.defaults,
        *(default for default in arguments.kw_defaults if default is not None),
        *annotations,
    )
    if isinstance(node, ast.Lambda):
        return common
    return (
        *node.decorator_list,
        *common,
        *(() if node.returns is None else (node.returns,)),
        *getattr(node, "type_params", ()),
    )


def _scope_declares_global_name(nodes: Iterable[ast.AST], name: str) -> bool:
    def declares(node: ast.AST) -> bool:
        if isinstance(node, ast.Global):
            return name in node.names
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            return False
        return any(declares(child) for child in ast.iter_child_nodes(node))

    return any(declares(node) for node in nodes)


def _statement_may_bind_name(
    node: ast.AST,
    name: str,
    index: _EnvironmentScopeIndex | None = None,
    scope_id: int = 0,
) -> bool:
    if _statement_binds_name(node, name):
        return True
    if isinstance(node, ast.If):
        if _statement_may_bind_name(node.test, name, index, scope_id):
            return True
        test_truth = _static_truth_value(node.test)
        if test_truth is not None:
            selected = node.body if test_truth else node.orelse
            return any(
                _statement_may_bind_name(child, name, index, scope_id)
                for child in selected
            )
        return any(
            _statement_may_bind_name(child, name, index, scope_id)
            for child in (*node.body, *node.orelse)
        )
    if isinstance(node, (ast.For, ast.AsyncFor)):
        if _statement_may_bind_name(node.iter, name, index, scope_id):
            return True
        if _iterable_is_definitely_empty(node.iter, index, scope_id):
            return any(
                _statement_may_bind_name(child, name, index, scope_id)
                for child in node.orelse
            )
        return name in _target_names(node.target) or any(
            _statement_may_bind_name(child, name, index, scope_id)
            for child in (*node.body, *node.orelse)
        )
    if isinstance(node, ast.While):
        if _statement_may_bind_name(node.test, name, index, scope_id):
            return True
        if _static_truth_value(node.test) is False:
            return any(
                _statement_may_bind_name(child, name, index, scope_id)
                for child in node.orelse
            )
    if isinstance(node, ast.IfExp):
        if _statement_may_bind_name(node.test, name, index, scope_id):
            return True
        test_truth = _static_truth_value(node.test)
        candidates = (
            (node.body if test_truth else node.orelse,)
            if test_truth is not None
            else (node.body, node.orelse)
        )
        return any(
            _statement_may_bind_name(child, name, index, scope_id)
            for child in candidates
        )
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            if _statement_may_bind_name(value, name, index, scope_id):
                return True
            truth = _static_truth_value(value)
            if (isinstance(node.op, ast.And) and truth is False) or (
                isinstance(node.op, ast.Or) and truth is True
            ):
                return False
        return False
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return _comprehension_may_bind_name(
            node,
            name,
            index,
            scope_id,
            consumed=not isinstance(node, ast.GeneratorExp),
        )
    if isinstance(node, ast.Call) and index is not None:
        if _statement_may_bind_name(node.func, name, index, scope_id):
            return True
        consumed_generators: list[ast.GeneratorExp] = []
        if not _is_proven_lazy_builtin_call(node, index, scope_id):
            for argument in (
                *(
                    arg.value if isinstance(arg, ast.Starred) else arg
                    for arg in node.args
                ),
                *(keyword.value for keyword in node.keywords),
            ):
                consumed_generators.extend(
                    _resolved_generator_expressions(argument, index, scope_id)
                )
        for receiver, method in _bound_generator_consumer_sources(
            node.func, index, scope_id
        ):
            if _bound_generator_call_may_activate(method, node, index, scope_id):
                consumed_generators.extend(
                    _resolved_generator_expressions(receiver, index, scope_id)
                )
        if any(
            _comprehension_may_bind_name(
                generator, name, index, scope_id, consumed=True
            )
            for generator in consumed_generators
        ):
            return True
        return any(
            _statement_may_bind_name(child, name, index, scope_id)
            for child in (*node.args, *(keyword.value for keyword in node.keywords))
        )
    if isinstance(
        node, (ast.For, ast.AsyncFor, ast.comprehension)
    ) and name in _target_names(node.target):
        return True
    if isinstance(node, ast.NamedExpr) and name in _target_names(node.target):
        return True
    if isinstance(node, ast.ImportFrom) and any(
        alias.name == "*" for alias in node.names
    ):
        return True
    if isinstance(node, ast.ExceptHandler) and node.name == name:
        return True
    if isinstance(node, (ast.With, ast.AsyncWith)) and any(
        item.optional_vars is not None and name in _target_names(item.optional_vars)
        for item in node.items
    ):
        return True
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
        return True
    if isinstance(node, ast.MatchMapping) and node.rest == name:
        return True
    if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        # IMPORTANT: definition bodies have their own binding scope, but decorators, bases,
        # annotations, and defaults execute in the containing scope. Skipping both sides either
        # invents nested-body rebindings or misses `lambda x=(frozenset := fake): ...`.
        if any(
            _statement_may_bind_name(expression, name, index, scope_id)
            for expression in _definition_outer_scope_expressions(node)
        ):
            return True
        return (
            isinstance(node, ast.ClassDef)
            and _scope_declares_global_name(node.body, name)
            and any(
                _statement_may_bind_name(statement, name, index, scope_id)
                for statement in node.body
            )
        )
    return any(
        _statement_may_bind_name(child, name, index, scope_id)
        for child in ast.iter_child_nodes(node)
    )


def _module_try_delete_restores_builtin(
    tree: ast.Module,
    name: str,
    use: ast.AST,
    scope_index: _EnvironmentScopeIndex,
) -> bool:
    prior = [
        statement
        for statement in tree.body
        if _environment_node_position(statement) < _environment_node_position(use)
    ]
    for index, statement in enumerate(prior):
        if not isinstance(statement, ast.Try) or index == 0:
            continue
        if not _statement_binds_name(prior[index - 1], name):
            continue
        if (
            len(statement.body) != 1
            or not isinstance(statement.body[0], ast.Delete)
            or len(statement.body[0].targets) != 1
            or not isinstance(statement.body[0].targets[0], ast.Name)
            or statement.body[0].targets[0].id != name
        ):
            continue
        residual_nodes = (*statement.handlers, *statement.orelse, *statement.finalbody)
        if any(
            _statement_may_bind_name(child, name, scope_index, 0)
            for child in residual_nodes
        ):
            continue
        if any(
            _statement_may_bind_name(later, name, scope_index, 0)
            for later in prior[index + 1 :]
        ):
            continue
        # IMPORTANT: this proof is intentionally narrow: an immediately preceding unconditional
        # bind makes the sole try-body delete non-raising, no exit arm can rebind the name, and no
        # later statement can replace it before this exact use. Treating arbitrary or stale
        # try-body deletes as definite accepts a non-builtin registry constructor fail-open.
        return True
    return False


def _assigned_string_set(tree: ast.AST, assignment_name: str) -> frozenset[str] | None:
    if not isinstance(tree, ast.Module):
        return None
    scope_index = _build_environment_scope_index(tree)
    matches: list[ast.AST] = []
    for node in tree.body:
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == assignment_name
        ):
            value = node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == assignment_name
            and node.value is not None
        ):
            value = node.value
        if value is not None:
            matches.append(value)
    if len(matches) != 1:
        return None
    value = matches[0]
    module_facts = scope_index.scopes[0]
    if (
        len(module_facts.binding_events.get(assignment_name, ())) != 1
        or module_facts.wildcard_import_events
    ):
        return None
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and not value.keywords
        and len(value.args) <= 1
    ):
        return None
    # CRITICAL: the registry constructor must resolve to the builtin at this exact use. A
    # whole-module binding scan both trusted conditionally shadowed callees and rejected safe
    # assignments that occurred later, breaking registry-policy parity in both directions.
    constructor_states = _environment_binding_states_at(
        scope_index, 0, "frozenset", value.func
    )
    # IMPORTANT: deleting a module shadow restores builtin lookup, while deleting names such as
    # `os` leaves no canonical module fallback. Permit the unbound tombstone only for this known
    # builtin constructor; wildcard or conditional-shadow states remain unprovable.
    constructor_is_builtin = bool(constructor_states) and constructor_states.issubset(
        {None, _ENV_ORIGIN_UNBOUND}
    )
    if not constructor_is_builtin and not _module_try_delete_restores_builtin(
        tree, "frozenset", value.func, scope_index
    ):
        return None
    if not value.args:
        return frozenset()
    literal = value.args[0]
    if not isinstance(literal, (ast.Set, ast.List, ast.Tuple)):
        return None
    strings = [
        element.value
        for element in literal.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    # CRITICAL: registry-policy parity must come from one literal immutable assignment. Walking
    # arbitrary expressions allowed dead-branch canaries to impersonate runtime registry values.
    if len(strings) != len(literal.elts) or len(strings) != len(set(strings)):
        return None
    return frozenset(strings)


def _validate_live_environment_aliases(
    contract: EnvironmentAliasContract,
    source_trees: Mapping[str, ast.AST],
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    observed_exception_paths: set[str] = set()
    for path, tree in sorted(source_trees.items()):
        if not any(_within_root(path, root) for root in contract.production_roots):
            continue
        observed = _observe_raw_legacy_env_reads(path, tree, contract)
        if path in contract.direct_read_exceptions and observed:
            observed_exception_paths.add(path)
            continue
        findings.extend(observed)

    for path in sorted(contract.direct_read_exceptions - observed_exception_paths):
        findings.append(_finding("ENV_ALIAS_EXCEPTION_STALE", path=path))

    central_tree = source_trees.get(contract.central_owner)
    if central_tree is None:
        findings.append(
            _finding("ENV_ALIAS_OWNER_MISSING", path=contract.central_owner)
        )
        return tuple(findings)
    moltbot = _assigned_string_set(central_tree, "LEGACY_MOLTBOT_ENV_KEYS")
    clawdbot = _assigned_string_set(central_tree, "SUPPORTED_CLAWDBOT_ENV_KEYS")
    dynamic = _assigned_string_set(central_tree, "SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS")
    rejected = _assigned_string_set(central_tree, "REJECTED_LEGACY_ENV_KEYS")
    if moltbot is None or clawdbot is None or dynamic is None or rejected is None:
        findings.append(
            _finding("ENV_ALIAS_REGISTRY_UNREADABLE", path=contract.central_owner)
        )
        return tuple(findings)

    observed_supported = moltbot | clawdbot
    for key in sorted(contract.supported_legacy_keys - observed_supported):
        findings.append(
            _finding(
                "ENV_ALIAS_REGISTRY_MISSING", path=contract.central_owner, subject=key
            )
        )
    for key in sorted(observed_supported - contract.supported_legacy_keys):
        findings.append(
            _finding(
                "ENV_ALIAS_REGISTRY_UNREGISTERED",
                path=contract.central_owner,
                subject=key,
            )
        )
    for key in sorted(contract.supported_dynamic_legacy_keys ^ dynamic):
        findings.append(
            _finding(
                "ENV_ALIAS_DYNAMIC_KEYS_DRIFT",
                path=contract.central_owner,
                subject=key,
            )
        )
    for key in sorted(contract.rejected_legacy_keys ^ rejected):
        findings.append(
            _finding(
                "ENV_ALIAS_REJECTED_DRIFT", path=contract.central_owner, subject=key
            )
        )
    return tuple(findings)


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
    source_trees: dict[str, ast.AST] = {}
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
        source_trees[path] = tree
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

    if context.environment_alias_contract is not None:
        findings.extend(
            _validate_live_environment_aliases(
                context.environment_alias_contract,
                source_trees,
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
