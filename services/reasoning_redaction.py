"""
S72 reasoning-content redaction and privileged reveal helpers.

Default posture is fail-closed: reasoning-like payload fields are stripped from
operator-facing payloads unless an explicit local-debug reveal gate is allowed.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from typing import Any, Dict, Iterable

from .access_control import is_loopback
from .audit import emit_audit_event
from .env_aliases import get_env_value
from .internal_content import sanitize_internal_content
from .request_ip import get_client_ip
from .runtime_profile import get_runtime_profile, is_hardened_mode

REASONING_REVEAL_ENV = "OPENCLAW_DEBUG_REASONING_REVEAL"
LEGACY_REASONING_REVEAL_ENV = "MOLTBOT_DEBUG_REASONING_REVEAL"
REASONING_REVEAL_HEADER = "X-OpenClaw-Debug-Reveal-Reasoning"
LEGACY_REASONING_REVEAL_HEADER = "X-Moltbot-Debug-Reveal-Reasoning"
REASONING_REVEAL_QUERY = "debug_reasoning"

REASONING_KEYS = {
    "analysis",
    "analysis_text",
    "chain_of_thought",
    "cot",
    "internal_reasoning",
    "reasoning",
    "reasoning_content",
    "reasoning_text",
    "thinking",
    "thinking_text",
    "thought",
    "thoughts",
}
REASONING_BLOCK_TYPES = {
    "analysis",
    "reasoning",
    "reasoning_content",
    "thinking",
    "thought",
}

_DROP = object()


def _is_truthy_flag(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_key(key: Any) -> str:
    return str(key or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_reasoning_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized in REASONING_KEYS:
        return True
    return normalized.endswith("_reasoning") or normalized.endswith("_thinking")


def _is_reasoning_block(value: Dict[str, Any]) -> bool:
    block_type = _normalize_key(value.get("type"))
    return block_type in REASONING_BLOCK_TYPES


def _strip_reasoning(value: Any, *, include_reasoning: bool) -> Any:
    if isinstance(value, dict):
        if _is_reasoning_block(value):
            return copy.deepcopy(value) if include_reasoning else _DROP

        result: Dict[str, Any] = {}
        for key, raw_child in value.items():
            if _is_reasoning_key(key):
                if include_reasoning:
                    result[key] = copy.deepcopy(raw_child)
                continue

            child = _strip_reasoning(raw_child, include_reasoning=include_reasoning)
            if child is _DROP:
                continue
            result[key] = child
        return result

    if isinstance(value, list):
        result_list = []
        for item in value:
            child = _strip_reasoning(item, include_reasoning=include_reasoning)
            if child is _DROP:
                continue
            result_list.append(child)
        return result_list

    return copy.deepcopy(value)


def sanitize_operator_payload(value: Any, *, include_reasoning: bool = False) -> Any:
    """Strip internal and reasoning-like fields from visible payloads by default."""
    public_value = sanitize_internal_content(value)
    cleaned = _strip_reasoning(public_value, include_reasoning=include_reasoning)
    if cleaned is _DROP:
        return {}
    return cleaned


def extract_reasoning_payload(value: Any) -> Any:
    """Return a payload containing only reasoning-like fields/blocks, or None."""
    reasoning_only = _strip_reasoning(value, include_reasoning=True)
    sanitized = sanitize_operator_payload(reasoning_only, include_reasoning=True)
    without_non_reasoning = _extract_only_reasoning(sanitized)
    if without_non_reasoning in (None, {}, []):
        return None
    return without_non_reasoning


def _extract_only_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        if _is_reasoning_block(value):
            return copy.deepcopy(value)

        result: Dict[str, Any] = {}
        for key, raw_child in value.items():
            if _is_reasoning_key(key):
                result[key] = copy.deepcopy(raw_child)
                continue

            child = _extract_only_reasoning(raw_child)
            if child not in (None, {}, []):
                result[key] = child
        return result

    if isinstance(value, list):
        items = []
        for item in value:
            child = _extract_only_reasoning(item)
            if child not in (None, {}, []):
                items.append(child)
        return items

    return None


def resolve_reasoning_reveal(request: Any, *, admin_authorized: bool) -> Dict[str, Any]:
    """Evaluate the privileged reasoning reveal gate for a request."""
    headers = getattr(request, "headers", {}) or {}
    if not isinstance(headers, Mapping):
        headers = {}
    query = getattr(request, "query", {}) or {}
    if not isinstance(query, Mapping):
        query = {}
    requested = (
        _is_truthy_flag(headers.get(REASONING_REVEAL_HEADER))
        or _is_truthy_flag(headers.get(LEGACY_REASONING_REVEAL_HEADER))
        or _is_truthy_flag(query.get(REASONING_REVEAL_QUERY))
    )

    deployment_profile = (
        os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "").strip().lower() or "local"
    )
    runtime_profile = get_runtime_profile().value
    remote_addr = get_client_ip(request)
    env_enabled = _is_truthy_flag(
        get_env_value(
            REASONING_REVEAL_ENV,
            aliases=(LEGACY_REASONING_REVEAL_ENV,),
        )
    )

    if not requested:
        return {
            "requested": False,
            "allowed": False,
            "reason": "not_requested",
            "remote_addr": remote_addr,
            "runtime_profile": runtime_profile,
            "deployment_profile": deployment_profile,
        }

    if not env_enabled:
        reason = "debug_reveal_disabled"
    elif not admin_authorized:
        reason = "admin_required"
    elif is_hardened_mode():
        reason = "runtime_profile_hardened"
    elif deployment_profile not in {"local", "lan"}:
        reason = f"deployment_profile_{deployment_profile}"
    elif not is_loopback(remote_addr):
        reason = "loopback_required"
    else:
        reason = "allowed"

    return {
        "requested": True,
        "allowed": reason == "allowed",
        "reason": reason,
        "remote_addr": remote_addr,
        "runtime_profile": runtime_profile,
        "deployment_profile": deployment_profile,
    }


def audit_reasoning_reveal(
    request: Any,
    *,
    target: str,
    decision: Dict[str, Any],
    extra_details: Dict[str, Any] | None = None,
) -> None:
    """Emit an audit event for explicit reasoning reveal attempts."""
    if not decision.get("requested"):
        return
    details = {
        "requested": bool(decision.get("requested")),
        "allowed": bool(decision.get("allowed")),
        "reason": decision.get("reason"),
        "remote_addr": decision.get("remote_addr"),
        "runtime_profile": decision.get("runtime_profile"),
        "deployment_profile": decision.get("deployment_profile"),
    }
    if extra_details:
        details.update(extra_details)
    emit_audit_event(
        action="reasoning.debug_reveal",
        target=target,
        outcome="allow" if decision.get("allowed") else "error",
        status_code=200 if decision.get("allowed") else 403,
        details=details,
        request=request,
    )


def get_redacted_reasoning_debug(value: Any) -> Any:
    """
    Extract a reasoning-only payload and redact sensitive tokens/secrets inside it.
    """
    reasoning = extract_reasoning_payload(value)
    if reasoning in (None, {}, []):
        return None
    try:
        from .redaction import redact_json
    except Exception:
        return reasoning
    return redact_json(reasoning)


def strip_reasoning_keys(keys: Iterable[str]) -> list[str]:
    """Helper for tests/debug output."""
    return [key for key in keys if not _is_reasoning_key(key)]
