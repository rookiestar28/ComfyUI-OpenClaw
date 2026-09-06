"""
R90: Compatibility matrix governance helpers.

Machine-readable metadata and refresh workflow primitives for
`docs/release/compatibility_matrix.md`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

META_BLOCK_TAG = "openclaw-compat-matrix-meta"
DEFAULT_WARN_AGE_DAYS = 30
DEFAULT_MAX_AGE_DAYS = 45
CURRENT_SCHEMA_VERSION = 3
# CRITICAL: schema 2 stays explicitly supported. R254 added `reference_baselines`
# and `evidence_states` additively; a schema-2 document is still a valid, if
# less precise, matrix and must not start failing validation.
SUPPORTED_SCHEMA_VERSIONS = (2, 3)
ANCHOR_KEYS = ("comfyui", "comfyui_frontend", "desktop", "comfy_desktop")

# R254: source review, repository validation and real-host validation are three
# separate evidence states. Conflating them is what let a reviewed source
# checkout read as a validated running host.
EVIDENCE_KEYS = ("source_review", "repository_validation", "real_host")
EVIDENCE_STATES = ("pending", "reviewed", "validated", "failed")
ALLOWED_EVIDENCE_STATES: dict[str, tuple[str, ...]] = {
    "source_review": ("pending", "reviewed"),
    "repository_validation": ("pending", "validated", "failed"),
    "real_host": ("pending", "validated", "failed"),
}
# A non-pending evidence state must name the run that produced it.
EVIDENCE_RUN_REQUIRED_STATES = ("validated", "failed")

REFERENCE_BASELINE_KEYS = ("comfyui", "comfyui_frontend")
REFERENCE_BASELINE_FIELDS: dict[str, tuple[str, ...]] = {
    "comfyui": (
        "source_head",
        "source_describe",
        "project_version",
        "tag",
        "tag_commit",
        "bundled_frontend_version",
    ),
    "comfyui_frontend": (
        "source_head",
        "source_describe",
        "package_version",
        "release_version",
        "release_tag",
        "release_tag_commit",
    ),
}
DEFAULT_HOST_SURFACES: dict[str, dict[str, Any]] = {
    "desktop": {
        "generation": "legacy_fixed_bundle",
        "anchor_key": "desktop",
        "hosted_version_mode": "fixed",
        "core_version": "0.22.3",
        "frontend_version": "1.43.18",
    },
    "comfy_desktop": {
        "generation": "managed_install",
        "anchor_key": "comfy_desktop",
        "hosted_version_mode": "installation_specific",
        "core_version": None,
        "frontend_version": None,
    },
}

META_BLOCK_RE = re.compile(
    r"```" + re.escape(META_BLOCK_TAG) + r"\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)
SEMVER_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+)")
COMFYUI_ANCHOR_RE = re.compile(
    r"^[0-9a-fA-F]{7,40}\s+\(v[^\s/]+\s+/\s+pyproject\s+\d+\.\d+\.\d+\)$"
)
FRONTEND_ANCHOR_RE = re.compile(
    r"^\d+\.\d+\.\d+\s+\([0-9a-fA-F]{7,40}\s+/\s+v[^\s)]+\)$"
)
DESKTOP_ANCHOR_RE = re.compile(
    r"^(?P<desktop>\d+\.\d+\.\d+)\s+\(core\s+(?P<core>\d+\.\d+\.\d+)\s+/\s+frontend\s+(?P<frontend>\d+\.\d+\.\d+)\)$"
)
COMFY_DESKTOP_ANCHOR_RE = re.compile(
    r"^(?P<desktop>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s+"
    r"\((?P<revision>[0-9a-fA-F]{7,40})\s+/\s+(?P<describe>v[^\s)]+)\)$"
)
# R254: baseline commits are recorded in full so a short-SHA collision cannot
# silently retarget the reviewed subject.
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DESCRIBE_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
EXACT_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
EVIDENCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _utc_now().date().isoformat()


def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_metadata() -> Dict[str, Any]:
    today = _today_iso()
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "matrix_version": "v0.2.1",
        "last_validated_date": today,
        "policy": {
            "warn_age_days": DEFAULT_WARN_AGE_DAYS,
            "max_age_days": DEFAULT_MAX_AGE_DAYS,
        },
        "anchors": {key: "unknown" for key in ANCHOR_KEYS},
        "host_surfaces": copy.deepcopy(DEFAULT_HOST_SURFACES),
        # IMPORTANT: bootstrap metadata is deliberately incomplete so missing
        # facts surface as violations instead of silently reading as validated.
        # Empty baselines flag; all-pending evidence is the honest starting state.
        "reference_baselines": {},
        "evidence_states": {
            key: {"state": "pending", "evidence_id": None, "run_id": None}
            for key in EVIDENCE_KEYS
        },
        "evidence": {
            "evidence_id": f"compat-matrix-{today.replace('-', '')}",
            "updated_at": _utc_now().isoformat(),
            "updated_by": "manual",
        },
    }


def format_metadata_block(metadata: Dict[str, Any]) -> str:
    return (
        f"```{META_BLOCK_TAG}\n"
        + json.dumps(metadata, indent=2, sort_keys=True)
        + "\n```\n"
    )


def extract_metadata_block(
    text: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
    """
    Extract JSON metadata block.

    Returns: (metadata, issues, raw_json_text)
    """
    match = META_BLOCK_RE.search(text)
    if not match:
        return None, ["R90_META_BLOCK_MISSING"], None

    raw = match.group("body").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["R90_META_BLOCK_INVALID_JSON"], raw
    if not isinstance(parsed, dict):
        return None, ["R90_META_BLOCK_NOT_OBJECT"], raw
    return parsed, [], raw


def replace_metadata_block(text: str, metadata: Dict[str, Any]) -> str:
    block = format_metadata_block(metadata)
    if META_BLOCK_RE.search(text):
        return META_BLOCK_RE.sub(lambda _m: block.rstrip("\n"), text, count=1)

    # Insert after first heading if present; otherwise prepend.
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("# "):
            return "".join(lines[: idx + 1] + ["\n", block] + lines[idx + 1 :])
    return block + text


def _body_without_meta(text: str) -> str:
    return META_BLOCK_RE.sub("", text).strip()


def read_matrix_document(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    metadata, issues, raw = extract_metadata_block(text)
    return {
        "path": str(p),
        "text": text,
        "metadata": metadata,
        "issues": issues,
        "raw_metadata": raw,
        "body_sha256": hashlib.sha256(
            _body_without_meta(text).encode("utf-8")
        ).hexdigest(),
        "has_meta": metadata is not None,
    }


def _mapping_field(source: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a nested mapping, or an empty one when absent or the wrong shape.

    IMPORTANT: keeps the projection total. A malformed nested object degrades to
    empty rather than raising, so diagnostics stay available while the validator
    is the surface that reports the defect.
    """
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _validate_reference_baselines(baselines: Any) -> list[dict[str, Any]]:
    """R254: typed, bounded source/tag/release facts for each upstream subject."""
    violations: list[dict[str, Any]] = []
    if not isinstance(baselines, dict):
        return [
            {
                "code": "R254_BASELINES_MISSING",
                "message": "Missing reference_baselines object",
            }
        ]

    unknown = sorted(set(baselines) - set(REFERENCE_BASELINE_KEYS))
    for key in unknown:
        violations.append(
            {
                "code": "R254_BASELINE_UNKNOWN",
                "message": f"Unknown reference_baselines key: {key}",
            }
        )

    field_patterns = {
        "source_head": FULL_COMMIT_RE,
        "source_describe": DESCRIBE_RE,
        "project_version": EXACT_SEMVER_RE,
        "package_version": EXACT_SEMVER_RE,
        "release_version": EXACT_SEMVER_RE,
        "bundled_frontend_version": EXACT_SEMVER_RE,
        "tag": TAG_RE,
        "release_tag": TAG_RE,
        "tag_commit": FULL_COMMIT_RE,
        "release_tag_commit": FULL_COMMIT_RE,
    }

    for key in REFERENCE_BASELINE_KEYS:
        entry = baselines.get(key)
        if not isinstance(entry, dict):
            violations.append(
                {
                    "code": "R254_BASELINE_MISSING",
                    "message": f"Missing reference_baselines.{key}",
                }
            )
            continue
        for field_name in REFERENCE_BASELINE_FIELDS[key]:
            value = entry.get(field_name)
            if not isinstance(value, str) or not value.strip():
                violations.append(
                    {
                        "code": "R254_BASELINE_FIELD_MISSING",
                        "message": f"Missing reference_baselines.{key}.{field_name}",
                    }
                )
            elif field_patterns[field_name].match(value.strip()) is None:
                violations.append(
                    {
                        "code": "R254_BASELINE_FIELD_FORMAT",
                        "message": f"Malformed reference_baselines.{key}.{field_name}",
                    }
                )

    frontend = baselines.get("comfyui_frontend")
    if isinstance(frontend, dict):
        source_head = str(frontend.get("source_head", "")).strip()
        release_commit = str(frontend.get("release_tag_commit", "")).strip()
        # CRITICAL: the reviewed source head is ahead of the release tag. If the
        # two collapse into one value the matrix can no longer distinguish
        # "reviewed" from "reproducible", which is the whole point of R254.
        if source_head and source_head == release_commit:
            violations.append(
                {
                    "code": "R254_BASELINE_SUBJECT_COLLAPSE",
                    "message": (
                        "comfyui_frontend source_head and release_tag_commit must "
                        "stay distinct subjects"
                    ),
                }
            )
        describe = str(frontend.get("source_describe", "")).strip()
        release_tag = str(frontend.get("release_tag", "")).strip()
        if describe and release_tag and not describe.startswith(f"{release_tag}-"):
            violations.append(
                {
                    "code": "R254_BASELINE_DESCRIBE_MISMATCH",
                    "message": (
                        "comfyui_frontend source_describe must derive from its "
                        "release_tag"
                    ),
                }
            )
    return violations


def _validate_evidence_states(states: Any) -> list[dict[str, Any]]:
    """R254: keep source review, repository validation and real-host proof disjoint."""
    violations: list[dict[str, Any]] = []
    if not isinstance(states, dict):
        return [
            {
                "code": "R254_EVIDENCE_MISSING",
                "message": "Missing evidence_states object",
            }
        ]

    unknown = sorted(set(states) - set(EVIDENCE_KEYS))
    for key in unknown:
        violations.append(
            {
                "code": "R254_EVIDENCE_UNKNOWN_KEY",
                "message": f"Unknown evidence_states key: {key}",
            }
        )

    evidence_ids: list[str] = []
    for key in EVIDENCE_KEYS:
        entry = states.get(key)
        if not isinstance(entry, dict):
            violations.append(
                {
                    "code": "R254_EVIDENCE_ENTRY_MISSING",
                    "message": f"Missing evidence_states.{key}",
                }
            )
            continue

        state = entry.get("state")
        if not isinstance(state, str) or state not in EVIDENCE_STATES:
            # CRITICAL: fail closed. An unknown or missing state must never be
            # read as validated by a later reader.
            violations.append(
                {
                    "code": "R254_EVIDENCE_STATE_UNKNOWN",
                    "message": f"Unknown evidence_states.{key}.state: {state!r}",
                }
            )
            continue
        if state not in ALLOWED_EVIDENCE_STATES[key]:
            violations.append(
                {
                    "code": "R254_EVIDENCE_STATE_NOT_ALLOWED",
                    "message": (
                        f"evidence_states.{key}.state {state!r} is not allowed for "
                        "this evidence kind"
                    ),
                }
            )
            continue

        run_id = entry.get("run_id")
        if state in EVIDENCE_RUN_REQUIRED_STATES:
            if not isinstance(run_id, str) or not run_id.strip():
                violations.append(
                    {
                        "code": "R254_EVIDENCE_RUN_ID_REQUIRED",
                        "message": (
                            f"evidence_states.{key}.state {state!r} requires a run_id"
                        ),
                    }
                )
        elif run_id not in (None, ""):
            # CRITICAL: a pending state carrying a run identifier is how a
            # fabricated real-host run would enter the matrix.
            violations.append(
                {
                    "code": "R254_EVIDENCE_PENDING_RUN_ID",
                    "message": f"evidence_states.{key} is pending but names a run_id",
                }
            )

        evidence_id = entry.get("evidence_id")
        if state == "pending":
            if evidence_id not in (None, ""):
                violations.append(
                    {
                        "code": "R254_EVIDENCE_PENDING_ID",
                        "message": (
                            f"evidence_states.{key} is pending but names an evidence_id"
                        ),
                    }
                )
        elif (
            not isinstance(evidence_id, str)
            or EVIDENCE_ID_RE.match(evidence_id) is None
        ):
            violations.append(
                {
                    "code": "R254_EVIDENCE_ID_FORMAT",
                    "message": f"Missing/malformed evidence_states.{key}.evidence_id",
                }
            )
        else:
            evidence_ids.append(evidence_id)

    duplicates = sorted({v for v in evidence_ids if evidence_ids.count(v) > 1})
    for duplicate in duplicates:
        violations.append(
            {
                "code": "R254_EVIDENCE_ID_SHARED",
                "message": (
                    f"evidence_id {duplicate!r} is shared across evidence kinds; "
                    "states must be independently traceable"
                ),
            }
        )
    return violations


def build_reference_evidence_projection(
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Coarse, public-safe evidence projection for operator diagnostics.

    IMPORTANT: this returns states and upstream version facts only. It must never
    surface local paths, command logs, raw run content, or internal document
    names, because Operator Doctor output reaches operators over HTTP.
    """
    resolved: dict[str, Any] = metadata if isinstance(metadata, dict) else {}
    states = _mapping_field(resolved, "evidence_states")
    projection: dict[str, Any] = {"schema_version": resolved.get("schema_version")}

    for key in EVIDENCE_KEYS:
        entry = _mapping_field(states, key)
        state = entry.get("state")
        if not isinstance(state, str) or state not in ALLOWED_EVIDENCE_STATES[key]:
            # Fail closed: anything unrecognized degrades to `unknown`, never to
            # a validated-looking state.
            state = "unknown"
        projection[key] = state

    baselines = _mapping_field(resolved, "reference_baselines")
    core = _mapping_field(baselines, "comfyui")
    frontend = _mapping_field(baselines, "comfyui_frontend")
    projection["core_version"] = core.get("project_version") or "unknown"
    projection["core_bundled_frontend_version"] = (
        core.get("bundled_frontend_version") or "unknown"
    )
    projection["frontend_release_version"] = (
        frontend.get("release_version") or "unknown"
    )
    return projection


def validate_metadata(
    metadata: Optional[Dict[str, Any]], *, today: Optional[date] = None
) -> Dict[str, Any]:
    today = today or _utc_now().date()
    violations: List[Dict[str, Any]] = []
    if not isinstance(metadata, dict):
        return {
            "ok": False,
            "status": "invalid",
            "code": "R90_META_INVALID",
            "age_days": None,
            "violations": [{"code": "R90_META_MISSING", "message": "Metadata missing"}],
        }

    schema_version = metadata.get("schema_version")
    if schema_version == 1:
        violations.append(
            {
                "code": "R90_META_SCHEMA_UPGRADE_REQUIRED",
                "message": (
                    "schema_version 1 must be refreshed to schema_version "
                    f"{CURRENT_SCHEMA_VERSION}"
                ),
            }
        )
    elif schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        violations.append(
            {
                "code": "R90_META_SCHEMA_VERSION",
                "message": f"Unsupported schema_version: {schema_version!r}",
            }
        )

    last_validated = metadata.get("last_validated_date")
    parsed_last = (
        _parse_date(last_validated) if isinstance(last_validated, str) else None
    )
    if parsed_last is None:
        violations.append(
            {
                "code": "R90_META_LAST_VALIDATED_DATE",
                "message": "Missing/invalid last_validated_date (YYYY-MM-DD)",
            }
        )

    policy = metadata.get("policy")
    if not isinstance(policy, dict):
        policy = {}
        violations.append(
            {"code": "R90_META_POLICY", "message": "Missing policy object"}
        )

    try:
        warn_age_days = int(policy.get("warn_age_days", DEFAULT_WARN_AGE_DAYS))
    except Exception:
        warn_age_days = DEFAULT_WARN_AGE_DAYS
        violations.append(
            {"code": "R90_META_WARN_AGE", "message": "Invalid policy.warn_age_days"}
        )
    try:
        max_age_days = int(policy.get("max_age_days", DEFAULT_MAX_AGE_DAYS))
    except Exception:
        max_age_days = DEFAULT_MAX_AGE_DAYS
        violations.append(
            {"code": "R90_META_MAX_AGE", "message": "Invalid policy.max_age_days"}
        )
    if warn_age_days < 0 or max_age_days < 0 or warn_age_days > max_age_days:
        violations.append(
            {
                "code": "R90_META_AGE_POLICY_ORDER",
                "message": "Age policy must satisfy 0 <= warn_age_days <= max_age_days",
            }
        )

    anchors = metadata.get("anchors")
    if not isinstance(anchors, dict):
        anchors = {}
        violations.append(
            {"code": "R90_META_ANCHORS", "message": "Missing anchors object"}
        )
    else:
        unknown_anchor_keys = sorted(set(anchors) - set(ANCHOR_KEYS))
        for key in unknown_anchor_keys:
            violations.append(
                {
                    "code": "R90_META_ANCHOR_UNKNOWN",
                    "message": f"Unknown anchor key: {key}",
                }
            )
        anchor_patterns = {
            "comfyui": COMFYUI_ANCHOR_RE,
            "comfyui_frontend": FRONTEND_ANCHOR_RE,
            "desktop": DESKTOP_ANCHOR_RE,
            "comfy_desktop": COMFY_DESKTOP_ANCHOR_RE,
        }
        for key in ANCHOR_KEYS:
            if key not in anchors:
                violations.append(
                    {
                        "code": "R90_META_ANCHOR_MISSING",
                        "message": f"Missing anchors.{key}",
                    }
                )
            elif not isinstance(anchors[key], str) or not anchors[key].strip():
                violations.append(
                    {
                        "code": "R90_META_ANCHOR_INVALID",
                        "message": f"Invalid anchors.{key}",
                    }
                )
            elif anchors[key].strip() == "unknown":
                violations.append(
                    {
                        "code": "R90_META_ANCHOR_UNRESOLVED",
                        "message": f"Unresolved anchors.{key}",
                    }
                )
            elif anchor_patterns[key].match(anchors[key].strip()) is None:
                violations.append(
                    {
                        "code": "R90_META_ANCHOR_FORMAT",
                        "message": f"Malformed anchors.{key}",
                    }
                )

    if schema_version in SUPPORTED_SCHEMA_VERSIONS:
        host_surfaces = metadata.get("host_surfaces")
        if not isinstance(host_surfaces, dict):
            host_surfaces = {}
            violations.append(
                {
                    "code": "R90_META_HOST_SURFACES",
                    "message": "Missing host_surfaces object",
                }
            )
        for surface_name, expected in DEFAULT_HOST_SURFACES.items():
            surface = host_surfaces.get(surface_name)
            if not isinstance(surface, dict):
                violations.append(
                    {
                        "code": "R90_META_HOST_SURFACE_MISSING",
                        "message": f"Missing host_surfaces.{surface_name}",
                    }
                )
                continue
            for field_name in ("generation", "anchor_key", "hosted_version_mode"):
                if surface.get(field_name) != expected[field_name]:
                    violations.append(
                        {
                            "code": "R90_META_HOST_SURFACE_CONTRACT",
                            "message": (
                                f"host_surfaces.{surface_name}.{field_name} does not "
                                "match the supported generation contract"
                            ),
                        }
                    )
            if surface_name == "desktop":
                for field_name in ("core_version", "frontend_version"):
                    if surface.get(field_name) != expected[field_name]:
                        violations.append(
                            {
                                "code": "R90_META_HOST_SURFACE_CONTRACT",
                                "message": (
                                    f"host_surfaces.desktop.{field_name} must match "
                                    "the fixed legacy bundle"
                                ),
                            }
                        )
            elif (
                surface.get("core_version") is not None
                or surface.get("frontend_version") is not None
            ):
                violations.append(
                    {
                        "code": "R90_META_HOST_SURFACE_VERSION_MODE",
                        "message": (
                            "Managed-install Desktop hosted versions must remain "
                            "installation-specific"
                        ),
                    }
                )

    if schema_version == 3:
        violations.extend(
            _validate_reference_baselines(metadata.get("reference_baselines"))
        )
        violations.extend(_validate_evidence_states(metadata.get("evidence_states")))

    age_days: Optional[int] = None
    if parsed_last is not None:
        age_days = (today - parsed_last).days
        if age_days < 0:
            violations.append(
                {
                    "code": "R90_META_FUTURE_DATE",
                    "message": f"last_validated_date is in the future: {last_validated}",
                }
            )

    if violations:
        status = "invalid"
        code = "R90_META_INVALID"
    else:
        assert age_days is not None
        if age_days > max_age_days:
            status = "stale"
            code = "R90_MATRIX_STALE"
        elif age_days > warn_age_days:
            status = "warning"
            code = "R90_MATRIX_AGING"
        else:
            status = "fresh"
            code = "R90_MATRIX_FRESH"

    return {
        "ok": len(violations) == 0,
        "status": status,
        "code": code,
        "age_days": age_days,
        "warn_age_days": warn_age_days,
        "max_age_days": max_age_days,
        "violations": violations,
    }


def normalize_observed_anchors(
    *,
    comfyui: Optional[str] = None,
    comfyui_frontend: Optional[str] = None,
    desktop: Optional[str] = None,
    comfy_desktop: str | None = None,
) -> Dict[str, str]:
    return {
        "comfyui": (comfyui or "").strip() or "unknown",
        "comfyui_frontend": (comfyui_frontend or "").strip() or "unknown",
        "desktop": (desktop or "").strip() or "unknown",
        "comfy_desktop": (comfy_desktop or "").strip() or "unknown",
    }


def _extract_semver(anchor: Optional[str]) -> Optional[str]:
    if not isinstance(anchor, str):
        return None
    match = SEMVER_RE.search(anchor)
    if not match:
        return None
    return match.group("version")


def _parse_semver(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not isinstance(version, str):
        return None
    try:
        major, minor, patch = version.split(".")
        return int(major), int(minor), int(patch)
    except Exception:
        return None


def _compare_semver(left: Optional[str], right: Optional[str]) -> Optional[int]:
    left_tuple = _parse_semver(left)
    right_tuple = _parse_semver(right)
    if left_tuple is None or right_tuple is None:
        return None
    if left_tuple == right_tuple:
        return 0
    return 1 if left_tuple > right_tuple else -1


def build_host_surface_contract(
    published_anchors: Optional[Dict[str, Any]],
    *,
    published_surfaces: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    anchors = dict(published_anchors or {})
    surface_contracts = copy.deepcopy(
        published_surfaces
        if isinstance(published_surfaces, dict)
        else DEFAULT_HOST_SURFACES
    )
    standalone_anchor = str(anchors.get("comfyui_frontend", "unknown"))
    desktop_anchor = str(anchors.get("desktop", "unknown"))
    comfy_desktop_anchor = str(anchors.get("comfy_desktop", "unknown"))
    standalone_frontend_version = _extract_semver(standalone_anchor)

    desktop_match = DESKTOP_ANCHOR_RE.match(desktop_anchor)
    desktop_version = None
    desktop_core_version = None
    desktop_embedded_frontend_version = None
    comfy_desktop_version = None
    comfy_desktop_revision = None
    comfy_desktop_describe = None
    violations: List[Dict[str, str]] = []

    if desktop_anchor != "unknown" and desktop_match is None:
        violations.append(
            {
                "code": "R164_DESKTOP_ANCHOR_PARSE",
                "message": "Desktop anchor did not match the expected bundle format",
            }
        )
    elif desktop_match is not None:
        desktop_version = desktop_match.group("desktop")
        desktop_core_version = desktop_match.group("core")
        desktop_embedded_frontend_version = desktop_match.group("frontend")

    legacy_surface = surface_contracts.get("desktop")
    if not isinstance(legacy_surface, dict):
        violations.append(
            {
                "code": "R164_DESKTOP_SURFACE_MISSING",
                "message": "Legacy Desktop host-surface contract is missing",
            }
        )
        legacy_surface = {}
    elif legacy_surface != DEFAULT_HOST_SURFACES["desktop"]:
        violations.append(
            {
                "code": "R164_DESKTOP_SURFACE_DESCRIPTOR",
                "message": "Legacy Desktop host-surface contract is invalid",
            }
        )
    comfy_desktop_surface = surface_contracts.get("comfy_desktop")
    if not isinstance(comfy_desktop_surface, dict):
        violations.append(
            {
                "code": "R164_COMFY_DESKTOP_SURFACE_MISSING",
                "message": "Current Desktop host-surface contract is missing",
            }
        )
        comfy_desktop_surface = {}
    if comfy_desktop_surface.get("anchor_key") != "comfy_desktop":
        violations.append(
            {
                "code": "R164_COMFY_DESKTOP_ANCHOR_KEY",
                "message": "Current Desktop must reference the comfy_desktop anchor",
            }
        )
    if comfy_desktop_surface.get("generation") != "managed_install":
        violations.append(
            {
                "code": "R164_COMFY_DESKTOP_GENERATION",
                "message": "Current Desktop must use the managed_install generation",
            }
        )
    if (
        comfy_desktop_surface.get("hosted_version_mode") != "installation_specific"
        or comfy_desktop_surface.get("core_version") is not None
        or comfy_desktop_surface.get("frontend_version") is not None
    ):
        violations.append(
            {
                "code": "R164_COMFY_DESKTOP_HOSTED_VERSION_MODE",
                "message": (
                    "Current Desktop hosted core/frontend versions are "
                    "installation-specific and must not be fixed"
                ),
            }
        )

    comfy_desktop_match = COMFY_DESKTOP_ANCHOR_RE.match(comfy_desktop_anchor)
    if comfy_desktop_anchor != "unknown" and comfy_desktop_match is None:
        violations.append(
            {
                "code": "R164_COMFY_DESKTOP_ANCHOR_PARSE",
                "message": "Current Desktop anchor did not match the expected format",
            }
        )
    elif comfy_desktop_match is not None:
        comfy_desktop_version = comfy_desktop_match.group("desktop")
        comfy_desktop_revision = comfy_desktop_match.group("revision")
        comfy_desktop_describe = comfy_desktop_match.group("describe")

    compare_result = _compare_semver(
        desktop_embedded_frontend_version, standalone_frontend_version
    )
    if compare_result is None:
        desktop_frontend_status = "unknown"
    elif compare_result == 0:
        desktop_frontend_status = "in_sync"
    elif compare_result < 0:
        desktop_frontend_status = "lagging"
    else:
        desktop_frontend_status = "ahead"

    return {
        "ok": len(violations) == 0,
        "code": (
            "R164_HOST_SURFACES_READY"
            if not violations
            else "R164_HOST_SURFACE_CONTRACT_INVALID"
        ),
        "surfaces": {
            "standalone_frontend": {
                "anchor": standalone_anchor,
                "frontend_version": standalone_frontend_version,
            },
            "desktop": {
                "anchor": desktop_anchor,
                "desktop_version": desktop_version,
                "generation": legacy_surface.get("generation"),
                "hosted_version_mode": legacy_surface.get("hosted_version_mode"),
                "core_version": desktop_core_version,
                "embedded_frontend_version": desktop_embedded_frontend_version,
                "frontend_parity": {
                    "status": desktop_frontend_status,
                    "reference_frontend_version": standalone_frontend_version,
                },
            },
            "comfy_desktop": {
                "anchor": comfy_desktop_anchor,
                "application_version": comfy_desktop_version,
                "desktop_version": comfy_desktop_version,
                "source_revision": comfy_desktop_revision,
                "source_describe": comfy_desktop_describe,
                "generation": comfy_desktop_surface.get("generation"),
                "hosted_version_mode": comfy_desktop_surface.get("hosted_version_mode"),
                "core_version": comfy_desktop_surface.get("core_version"),
                "frontend_version": comfy_desktop_surface.get("frontend_version"),
            },
        },
        "violations": violations,
    }


def detect_anchor_drift(
    published_anchors: Optional[Dict[str, Any]],
    observed_anchors: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    drift: List[Dict[str, str]] = []
    pub = published_anchors or {}
    obs = observed_anchors or {}
    for key in ANCHOR_KEYS:
        published = str(pub.get(key, "unknown"))
        observed = str(obs.get(key, "unknown"))
        if observed == "unknown":
            continue
        if published == "unknown":
            drift.append(
                {
                    "anchor": key,
                    "status": "untracked",
                    "published": published,
                    "observed": observed,
                }
            )
            continue
        if published != observed:
            drift.append(
                {
                    "anchor": key,
                    "status": "drift",
                    "published": published,
                    "observed": observed,
                }
            )
    return {
        "ok": len(drift) == 0,
        "code": "R90_ANCHORS_IN_SYNC" if not drift else "R90_ANCHOR_DRIFT",
        "drift": drift,
    }


@dataclass
class RefreshWorkflowResult:
    ok: bool
    matrix_path: str
    run_date: str
    stages: Dict[str, Any]
    decision_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "matrix_path": self.matrix_path,
            "run_date": self.run_date,
            "stages": copy.deepcopy(self.stages),
            "decision_codes": list(self.decision_codes),
        }


def run_refresh_workflow(
    *,
    matrix_path: Path | str,
    observed_anchors: Optional[Dict[str, str]] = None,
    apply: bool = False,
    updated_by: str = "script",
    today: Optional[date] = None,
) -> RefreshWorkflowResult:
    p = Path(matrix_path)
    today = today or _utc_now().date()
    observed = dict(observed_anchors or normalize_observed_anchors())

    doc = read_matrix_document(p)
    metadata = (
        copy.deepcopy(doc["metadata"]) if isinstance(doc["metadata"], dict) else None
    )
    if metadata is None:
        metadata = _default_metadata()
        # Preserve compatibility for first adoption while making missing metadata visible.
        bootstrap_mode = True
    else:
        bootstrap_mode = False

    validate_before = validate_metadata(doc["metadata"], today=today)
    drift_before = detect_anchor_drift(metadata.get("anchors"), observed)

    collect_stage = {
        "matrix_exists": p.exists(),
        "metadata_present": doc["has_meta"],
        "body_sha256": doc["body_sha256"],
        "observed_anchors": observed,
        "doc_issues": list(doc["issues"]),
    }
    diff_stage = {
        "metadata_hash_before": (
            _json_hash(doc["metadata"]) if doc["metadata"] is not None else None
        ),
        "drift": drift_before,
        "bootstrap_metadata": bootstrap_mode,
    }
    validate_stage = {
        "before": validate_before,
    }

    publish_stage: Dict[str, Any] = {"mode": "dry-run", "updated": False}
    updated_text = doc["text"]
    metadata_after = copy.deepcopy(metadata)
    metadata_after["schema_version"] = CURRENT_SCHEMA_VERSION
    metadata_after.setdefault("policy", {})
    metadata_after.setdefault("anchors", {})
    metadata_after.setdefault("evidence", {})
    metadata_after["host_surfaces"] = copy.deepcopy(DEFAULT_HOST_SURFACES)
    metadata_after["last_validated_date"] = today.isoformat()
    for key in ANCHOR_KEYS:
        metadata_after["anchors"][key] = observed.get(key, "unknown")
    metadata_after["evidence"]["updated_by"] = updated_by
    metadata_after["evidence"]["updated_at"] = _utc_now().isoformat()
    metadata_after["evidence"][
        "evidence_id"
    ] = f"compat-matrix-refresh-{today.strftime('%Y%m%d')}"

    validate_after = validate_metadata(metadata_after, today=today)
    drift_after = detect_anchor_drift(metadata_after.get("anchors"), observed)
    validate_stage["after"] = validate_after

    # IMPORTANT: never publish unresolved or malformed host anchors.
    if apply and validate_after["ok"]:
        updated_text = replace_metadata_block(doc["text"], metadata_after)
        p.write_text(updated_text, encoding="utf-8")
        publish_stage = {
            "mode": "apply",
            "updated": True,
            "metadata_hash_after": _json_hash(metadata_after),
            "drift_after": drift_after,
            "body_sha256_after": hashlib.sha256(
                _body_without_meta(updated_text).encode("utf-8")
            ).hexdigest(),
        }
    elif apply:
        publish_stage = {
            "mode": "apply",
            "updated": False,
            "blocked_by": validate_after["code"],
            "metadata_preview_hash": _json_hash(metadata_after),
            "drift_after": drift_after,
        }
    else:
        publish_stage = {
            "mode": "dry-run",
            "updated": False,
            "metadata_preview_hash": _json_hash(metadata_after),
            "drift_after": drift_after,
        }

    decision_codes: List[str] = []
    decision_codes.append(validate_after["code"])
    decision_codes.append(drift_before["code"])
    if bootstrap_mode:
        decision_codes.append("R90_BOOTSTRAP_METADATA")
    if apply and validate_after["ok"]:
        decision_codes.append("R90_PUBLISH_APPLY")
    elif apply:
        decision_codes.append("R90_PUBLISH_REJECTED")
    else:
        decision_codes.append("R90_PUBLISH_DRY_RUN")

    ok = bool(validate_after["ok"])
    stages = {
        "collect": collect_stage,
        "diff": diff_stage,
        "validate": validate_stage,
        "publish": publish_stage,
    }
    return RefreshWorkflowResult(
        ok=ok,
        matrix_path=str(p),
        run_date=today.isoformat(),
        stages=stages,
        decision_codes=decision_codes,
    )
