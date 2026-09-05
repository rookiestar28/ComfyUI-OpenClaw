"""
R139 layered configuration primitives.

This module provides a small, dependency-light resolver used by runtime config
to unify precedence handling without forcing a big-bang migration.
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from .env_aliases import EnvLookupMode, resolve_env

SOURCE_ENV = "env"
SOURCE_RUNTIME_OVERRIDE = "runtime_override"
SOURCE_PERSISTED = "persisted"
SOURCE_DEFAULT = "default"

LLM_ENV_MAPPINGS: Dict[str, Tuple[str, str]] = {
    "provider": ("OPENCLAW_LLM_PROVIDER", "MOLTBOT_LLM_PROVIDER"),
    "model": ("OPENCLAW_LLM_MODEL", "MOLTBOT_LLM_MODEL"),
    "base_url": ("OPENCLAW_LLM_BASE_URL", "MOLTBOT_LLM_BASE_URL"),
    "timeout_sec": ("OPENCLAW_LLM_TIMEOUT", "MOLTBOT_LLM_TIMEOUT"),
    "max_retries": ("OPENCLAW_LLM_MAX_RETRIES", "MOLTBOT_LLM_MAX_RETRIES"),
    "fallback_models": ("OPENCLAW_FALLBACK_MODELS", "MOLTBOT_FALLBACK_MODELS"),
    "fallback_providers": (
        "OPENCLAW_FALLBACK_PROVIDERS",
        "MOLTBOT_FALLBACK_PROVIDERS",
    ),
    "max_failover_candidates": (
        "OPENCLAW_MAX_FAILOVER_CANDIDATES",
        "MOLTBOT_MAX_FAILOVER_CANDIDATES",
    ),
    "allow_private_network": (
        "OPENCLAW_LLM_ALLOW_PRIVATE_NETWORK",
        "MOLTBOT_LLM_ALLOW_PRIVATE_NETWORK",
    ),
}

GENERIC_LLM_API_KEY_ENV_KEYS = (
    "OPENCLAW_LLM_API_KEY",
    "MOLTBOT_LLM_API_KEY",
    "CLAWDBOT_LLM_API_KEY",
)
ADMIN_TOKEN_ENV_KEYS = ("OPENCLAW_ADMIN_TOKEN", "MOLTBOT_ADMIN_TOKEN")
OBS_TOKEN_ENV_KEYS = ("OPENCLAW_OBSERVABILITY_TOKEN", "MOLTBOT_OBSERVABILITY_TOKEN")

_RUNTIME_OVERRIDE_LOCK = Lock()
_RUNTIME_OVERRIDES: Dict[str, Dict[str, Any]] = {}


def get_first_present_env(
    keys: Iterable[str], *, env: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """Return the first env value by presence (not truthiness)."""
    ordered_keys = tuple(keys)
    if not ordered_keys:
        return None
    # Preserve the historical empty-mapping fallback at this compatibility facade.
    env_map = env or os.environ
    return resolve_env(
        ordered_keys[0],
        aliases=ordered_keys[1:],
        mode=EnvLookupMode.PRESENCE,
        env=env_map,
        warn_legacy=env is None,
    ).value


def get_preferred_env_value(
    primary: str, legacy: str, *, env: Optional[Mapping[str, str]] = None
) -> Tuple[Optional[str], bool]:
    """
    Return value by primary->legacy precedence and whether legacy path was used.

    Presence-based semantics are intentional so explicit empty-string values still
    count as a deliberate override.
    """
    env_map = env or os.environ
    result = resolve_env(
        primary,
        aliases=(legacy,) if legacy else (),
        mode=EnvLookupMode.PRESENCE,
        env=env_map,
        # This compatibility facade reports provenance to its caller; warning ownership remains
        # with that caller until its migration selects a central resolver policy explicitly.
        warn_legacy=False,
    )
    return result.value, result.used_legacy


def get_runtime_overrides(section: str) -> Dict[str, Any]:
    """Get a shallow copy of runtime overrides for a section."""
    with _RUNTIME_OVERRIDE_LOCK:
        return dict(_RUNTIME_OVERRIDES.get(section, {}))


def set_runtime_overrides(section: str, updates: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge runtime overrides for a section. `None` value removes the key."""
    with _RUNTIME_OVERRIDE_LOCK:
        current = dict(_RUNTIME_OVERRIDES.get(section, {}))
        for key, value in updates.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        if current:
            _RUNTIME_OVERRIDES[section] = current
        else:
            _RUNTIME_OVERRIDES.pop(section, None)
        return dict(current)


def clear_runtime_overrides(section: str, keys: Optional[Iterable[str]] = None) -> None:
    """Clear runtime overrides for a section or specific keys in the section."""
    with _RUNTIME_OVERRIDE_LOCK:
        if keys is None:
            _RUNTIME_OVERRIDES.pop(section, None)
            return
        current = _RUNTIME_OVERRIDES.get(section)
        if not current:
            return
        for key in keys:
            current.pop(key, None)
        if not current:
            _RUNTIME_OVERRIDES.pop(section, None)


def resolve_layered_config(
    *,
    ordered_keys: Iterable[str],
    defaults: Mapping[str, Any],
    persisted: Optional[Mapping[str, Any]] = None,
    runtime_overrides: Optional[Mapping[str, Any]] = None,
    env_getter: Optional[Callable[[str], Optional[Any]]] = None,
    normalize_value: Optional[Callable[[str, Any, str], Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Resolve layered values with deterministic precedence.

    Precedence (highest to lowest): env > runtime_override > persisted > default.
    """
    persisted_map = dict(persisted or {})
    runtime_map = dict(runtime_overrides or {})
    effective: Dict[str, Any] = {}
    sources: Dict[str, str] = {}

    for key in ordered_keys:
        value = defaults.get(key)
        source = SOURCE_DEFAULT

        if key in persisted_map:
            value = persisted_map.get(key)
            source = SOURCE_PERSISTED

        if key in runtime_map:
            value = runtime_map.get(key)
            source = SOURCE_RUNTIME_OVERRIDE

        if env_getter is not None:
            env_value = env_getter(key)
            if env_value is not None:
                value = env_value
                source = SOURCE_ENV

        if normalize_value is not None:
            value = normalize_value(key, value, source)

        effective[key] = value
        sources[key] = source

    return effective, sources
