"""
Validation, alias, and guardrail policy helpers for runtime_config.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from .env_aliases import EnvLookupMode
from .env_aliases import get_env_value as resolve_env_value

try:
    from .config_layers import (
        ADMIN_TOKEN_ENV_KEYS,
        LLM_ENV_MAPPINGS,
        SOURCE_ENV,
        SOURCE_PERSISTED,
        SOURCE_RUNTIME_OVERRIDE,
        get_first_present_env,
        get_preferred_env_value,
    )
except ImportError:
    from services.config_layers import (  # type: ignore
        ADMIN_TOKEN_ENV_KEYS,
        LLM_ENV_MAPPINGS,
        SOURCE_ENV,
        SOURCE_PERSISTED,
        SOURCE_RUNTIME_OVERRIDE,
        get_first_present_env,
        get_preferred_env_value,
    )

try:
    from .providers.catalog import (
        PROVIDER_CATALOG,
        get_default_public_llm_hosts,
        get_loopback_host_aliases,
        is_local_provider,
        list_providers,
        normalize_provider_id,
    )
except ImportError:
    try:
        from services.providers.catalog import (  # type: ignore
            PROVIDER_CATALOG,
            get_default_public_llm_hosts,
            get_loopback_host_aliases,
            is_local_provider,
            list_providers,
            normalize_provider_id,
        )
    except ImportError:
        PROVIDER_CATALOG = {}
        get_default_public_llm_hosts = lambda: set()  # type: ignore
        get_loopback_host_aliases = lambda _host: set()  # type: ignore
        is_local_provider = lambda _provider: False  # type: ignore
        list_providers = lambda: [  # type: ignore
            "openai",
            "anthropic",
            "openrouter",
            "gemini",
            "groq",
            "deepseek",
            "xai",
            "ollama",
            "lmstudio",
            "custom",
        ]
        normalize_provider_id = lambda value: str(value).strip().lower()  # type: ignore

try:
    from .runtime_guardrails import get_runtime_guardrails_snapshot
except ImportError:
    from services.runtime_guardrails import (
        get_runtime_guardrails_snapshot,  # type: ignore
    )

try:
    from .safe_io import SSRFError, validate_outbound_url
except ImportError:
    try:
        from services.safe_io import SSRFError, validate_outbound_url  # type: ignore
    except ImportError:

        class SSRFError(ValueError):
            pass

        def validate_outbound_url(url, **kwargs):  # type: ignore
            raise SSRFError(
                "Security dependencies missing: Cannot validate URL safety."
            )


try:
    from .settings_schema import coerce_dict as _schema_coerce
    from .settings_schema import get_schema_map
except ImportError:
    try:
        from services.settings_schema import (
            coerce_dict as _schema_coerce,  # type: ignore
        )
        from services.settings_schema import get_schema_map
    except ImportError:

        def _schema_coerce(updates):  # type: ignore
            return updates, []

        def get_schema_map():  # type: ignore
            return {}


ALLOWED_LLM_KEYS = {
    "provider",
    "model",
    "base_url",
    "allow_private_network",
    "timeout_sec",
    "max_retries",
    "fallback_models",
    "fallback_providers",
    "max_failover_candidates",
}

ALLOWED_SCHEDULER_KEYS = {
    "startup_jitter_sec",
    "max_runs_per_tick",
    "skip_missed_intervals",
    "execution_mode",
    "compute_error_disable_threshold",
}

DEFAULTS = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "",
        "allow_private_network": False,
        "timeout_sec": 120,
        "max_retries": 3,
        "fallback_models": [],
        "fallback_providers": [],
        "max_failover_candidates": 3,
    },
    "scheduler": {
        "startup_jitter_sec": 30,
        "max_runs_per_tick": 5,
        "skip_missed_intervals": False,
        "execution_mode": "auto",
        "compute_error_disable_threshold": 3,
    },
}

CONSTRAINTS = {
    "timeout_sec": (5, 300),
    "max_retries": (0, 10),
    "max_failover_candidates": (1, 5),
}

SCHEDULER_CONSTRAINTS = {
    "startup_jitter_sec": (0, 300),
    "max_runs_per_tick": (1, 100),
    "compute_error_disable_threshold": (1, 20),
}

ENV_MAPPINGS = dict(LLM_ENV_MAPPINGS)

SCHEDULER_ENV_MAPPINGS = {
    "startup_jitter_sec": ("OPENCLAW_SCHEDULER_STARTUP_JITTER_SEC", ""),
    "max_runs_per_tick": ("OPENCLAW_SCHEDULER_MAX_RUNS_PER_TICK", ""),
    "skip_missed_intervals": ("OPENCLAW_SCHEDULER_SKIP_MISSED", ""),
    "execution_mode": ("OPENCLAW_SCHEDULER_EXECUTION_MODE", ""),
    "compute_error_disable_threshold": (
        "OPENCLAW_SCHEDULER_COMPUTE_ERROR_DISABLE_THRESHOLD",
        "",
    ),
}

LLM_KEY_ORDER = tuple(ENV_MAPPINGS.keys())


def env_flag(primary: str, legacy: str, default: bool = False) -> bool:
    resolution = resolve_env_value(
        primary,
        aliases=(legacy,),
        mode=EnvLookupMode.PRESENCE,
    )
    if resolution is None:
        return default
    return str(resolution).strip().lower() in ("1", "true", "yes", "on")


def get_env_value(
    key: str,
    *,
    warned_legacy: set[str],
    logger,
) -> Optional[str]:
    env_vars = ENV_MAPPINGS.get(key)
    if not env_vars:
        return None
    primary, legacy = env_vars
    value, used_legacy = get_preferred_env_value(primary, legacy)
    if not used_legacy:
        return value
    if legacy not in warned_legacy:
        logger.warning(
            "Config: Using legacy environment variable %s. Please update to %s in future versions.",
            legacy,
            primary,
        )
        warned_legacy.add(legacy)
    return value


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def get_llm_egress_controls(
    provider: str, base_url: str, *, allow_private_network: bool = False
) -> Dict[str, Any]:
    """
    Build canonical outbound SSRF controls for LLM egress paths.

    IMPORTANT:
    Callers must reuse this same control set for both pre-validation and request-time
    validation. Diverging parameters caused the S65 loopback regression.
    """
    allowed_hosts_str = (
        resolve_env_value("OPENCLAW_LLM_ALLOWED_HOSTS", default="") or ""
    )
    env_hosts = {
        host.lower().strip() for host in allowed_hosts_str.split(",") if host.strip()
    }
    allowed_hosts = set(get_default_public_llm_hosts()) | env_hosts

    guardrails = get_runtime_guardrails_snapshot()
    provider_safety = guardrails.get("values", {}).get("provider_safety", {})
    default_allow_any = bool(
        provider_safety.get("allow_any_public_llm_host_default", False)
    )
    allow_any = env_flag(
        "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
        "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
        default=default_allow_any,
    )

    allow_loopback_hosts: Optional[set[str]] = None
    try:
        host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    except Exception:
        host = ""

    # CRITICAL: scoped private-network access is exact-host only. Do not widen
    # this into CIDR or wildcard allowlists, or LLM base URLs can bypass SSRF scope.
    if allow_private_network and host:
        allowed_hosts.add(host)

    # CRITICAL: local providers may use loopback only. Never widen this to
    # blanket private IPs or SSRF protections regress.
    if host and is_local_provider(provider):
        loopback_aliases = get_loopback_host_aliases(host)
        if loopback_aliases:
            allow_loopback_hosts = loopback_aliases
            allowed_hosts |= loopback_aliases

    return {
        "allow_hosts": (
            None if allow_any and not allow_private_network else allowed_hosts
        ),
        "allow_any_public_host": allow_any,
        "allow_loopback_hosts": allow_loopback_hosts,
        "allow_private_network": bool(allow_private_network),
    }


def get_scheduler_config() -> Dict[str, Any]:
    effective = {}
    defaults = DEFAULTS["scheduler"]

    for key in ALLOWED_SCHEDULER_KEYS:
        env_vars = SCHEDULER_ENV_MAPPINGS.get(key)
        if env_vars:
            primary, _ = env_vars
            value = os.environ.get(primary)
            if value is not None:
                if key == "skip_missed_intervals":
                    effective[key] = str(value).strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                elif key in SCHEDULER_CONSTRAINTS:
                    try:
                        value_int = int(value)
                        effective[key] = _clamp(value_int, *SCHEDULER_CONSTRAINTS[key])
                    except ValueError:
                        effective[key] = defaults[key]
                else:
                    effective[key] = value
                continue

        effective[key] = defaults.get(key)

    return effective


def normalize_llm_layer_value(key: str, value: Any, source: str) -> Any:
    if source == SOURCE_ENV:
        if key == "allow_private_network":
            return _coerce_bool(value)

        if key in ("fallback_models", "fallback_providers"):
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        if key in CONSTRAINTS:
            try:
                value_int = int(value)
            except (TypeError, ValueError):
                return DEFAULTS["llm"].get(key)
            min_val, max_val = get_constraint_range(key)
            return _clamp(value_int, min_val, max_val)

        return value

    if key in CONSTRAINTS and isinstance(value, (int, float)):
        min_val, max_val = get_constraint_range(key)
        return _clamp(int(value), min_val, max_val)
    if key == "allow_private_network":
        return _coerce_bool(value)
    return value


def validate_config_update(
    updates: Dict[str, Any],
    *,
    validate_url=validate_outbound_url,
    ssrf_error_type=SSRFError,
) -> Tuple[Dict[str, Any], list]:
    sanitized = {}
    errors = []

    coerced, coercion_errors = _schema_coerce(updates)
    if coercion_errors:
        errors.extend(coercion_errors)

    for key, value in coerced.items():
        if key not in ALLOWED_LLM_KEYS:
            errors.append(f"Unknown key: {key}")
            continue

        if key in CONSTRAINTS:
            if not isinstance(value, (int, float)):
                errors.append(f"{key} must be a number")
                continue
            min_val, max_val = get_constraint_range(key)
            value = _clamp(int(value), min_val, max_val)
        elif key == "provider":
            if not isinstance(value, str):
                errors.append("provider must be a string")
                continue
            value = normalize_provider_id(value)
            valid_providers = set(list_providers())
            if value not in valid_providers:
                errors.append(f"Unknown provider: {value}")
                continue
        elif key == "base_url":
            if not isinstance(value, str):
                errors.append("base_url must be a string")
                continue
            if value.strip() == "":
                sanitized[key] = ""
                continue

            provider_key = sanitized.get(
                "provider",
                coerced.get("provider", updates.get("provider", "custom")),
            )
            provider_key = (
                str(provider_key).lower() if isinstance(provider_key, str) else "custom"
            )
            known_provider = PROVIDER_CATALOG.get(provider_key)

            if not (known_provider and value == known_provider.base_url):
                if provider_key == "custom" and not env_flag(
                    "OPENCLAW_ALLOW_CUSTOM_BASE_URL",
                    "MOLTBOT_ALLOW_CUSTOM_BASE_URL",
                    default=False,
                ):
                    errors.append(
                        "Custom Base URL requires OPENCLAW_ALLOW_CUSTOM_BASE_URL=1 "
                        "(or legacy MOLTBOT_ALLOW_CUSTOM_BASE_URL=1)"
                    )
                    continue

                allow_private_network = bool(
                    coerced.get("allow_private_network", False)
                )
                controls = get_llm_egress_controls(
                    provider_key,
                    value,
                    allow_private_network=allow_private_network,
                )
                if (
                    is_local_provider(provider_key)
                    and not controls.get("allow_loopback_hosts")
                    and not controls.get("allow_private_network")
                ):
                    errors.append(
                        f"Local provider {provider_key} must use localhost URL"
                    )
                    continue

                try:
                    from .safe_io import STANDARD_OUTBOUND_POLICY
                except ImportError:
                    from services.safe_io import (
                        STANDARD_OUTBOUND_POLICY,  # type: ignore
                    )

                try:
                    validate_url(
                        value,
                        allow_hosts=controls.get("allow_hosts"),
                        allow_any_public_host=bool(
                            controls.get("allow_any_public_host")
                        ),
                        allow_loopback_hosts=controls.get("allow_loopback_hosts"),
                        allow_private_network=bool(
                            controls.get("allow_private_network")
                        ),
                        policy=STANDARD_OUTBOUND_POLICY,
                    )
                except ssrf_error_type as exc:
                    if not env_flag(
                        "OPENCLAW_ALLOW_INSECURE_BASE_URL",
                        "MOLTBOT_ALLOW_INSECURE_BASE_URL",
                        default=False,
                    ):
                        errors.append(
                            "Unsafe Base URL blocked (SSRF): "
                            f"{exc}. OPENCLAW_LLM_ALLOWED_HOSTS "
                            "(or legacy MOLTBOT_LLM_ALLOWED_HOSTS) only allows "
                            "additional exact public hosts; private/reserved IP "
                            "targets still require scoped allow_private_network=true "
                            "for the configured LLM target or "
                            "OPENCLAW_ALLOW_INSECURE_BASE_URL=1. Wildcard '*' "
                            "entries are not supported."
                        )
                        continue
        elif key == "model":
            if not isinstance(value, str):
                errors.append("model must be a string")
                continue

        sanitized[key] = value

    return sanitized, errors


def merge_config_value(base: Any, patch: Any, key: str = "") -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for child_key, child_value in patch.items():
            merged[child_key] = merge_config_value(
                merged.get(child_key), child_value, key=child_key
            )
        return merged

    if isinstance(base, list) and isinstance(patch, list):
        base_is_id_keyed = len(base) > 0 and all(
            isinstance(item, dict) and "id" in item for item in base
        )
        if base_is_id_keyed:
            patch_is_id_keyed = all(
                isinstance(item, dict) and "id" in item for item in patch
            )
            if not patch_is_id_keyed:
                return base

            merged_map: Dict[str, Any] = {item["id"]: dict(item) for item in base}
            for patch_item in patch:
                patch_id = patch_item["id"]
                if patch_id in merged_map:
                    merged_map[patch_id].update(patch_item)
                else:
                    merged_map[patch_id] = dict(patch_item)
            return list(merged_map.values())

        return patch

    return patch


def get_apply_semantics(updated_keys: list) -> Dict[str, list]:
    applied_now = []
    restart_required = []
    notes = []

    for key in updated_keys:
        if key in ALLOWED_LLM_KEYS:
            applied_now.append(key)
        elif key in ALLOWED_SCHEDULER_KEYS:
            restart_required.append(key)
            notes.append(f"{key} requires service restart to take effect.")
        else:
            restart_required.append(key)

    return {
        "applied_now": sorted(applied_now),
        "restart_required": sorted(restart_required),
        "notes": notes,
    }


def get_settings_schema_map() -> dict:
    return get_schema_map()


def is_config_write_enabled() -> bool:
    return True


def validate_admin_token(token: str) -> bool:
    expected = get_first_present_env(ADMIN_TOKEN_ENV_KEYS) or ""
    if not expected:
        return True
    return token == expected


def get_admin_token() -> str:
    return get_first_present_env(ADMIN_TOKEN_ENV_KEYS) or ""


def is_loopback_client(remote_addr: str) -> bool:
    return remote_addr in ("127.0.0.1", "::1", "localhost")


def _clamp(value: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, value))


def _s66_timeout_retry_caps() -> Tuple[int, int]:
    snapshot = get_runtime_guardrails_snapshot()
    timeout_caps = snapshot.get("values", {}).get("timeout_retry", {})
    timeout_cap = int(
        timeout_caps.get("llm_timeout_cap_sec", CONSTRAINTS["timeout_sec"][1])
    )
    retry_cap = int(
        timeout_caps.get("llm_max_retries_cap", CONSTRAINTS["max_retries"][1])
    )
    timeout_cap = min(timeout_cap, CONSTRAINTS["timeout_sec"][1])
    retry_cap = min(retry_cap, CONSTRAINTS["max_retries"][1])
    return timeout_cap, retry_cap


def get_constraint_range(key: str) -> Tuple[int, int]:
    min_val, max_val = CONSTRAINTS[key]
    if key == "timeout_sec":
        timeout_cap, _ = _s66_timeout_retry_caps()
        max_val = min(max_val, timeout_cap)
    elif key == "max_retries":
        _, retry_cap = _s66_timeout_retry_caps()
        max_val = min(max_val, retry_cap)
    return min_val, max_val
