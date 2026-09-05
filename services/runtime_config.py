"""
Runtime Config Service (R21/S13/R70).
Compatibility facade for non-secret LLM configuration precedence, validation,
and persistence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.runtime_config")

try:
    from .config_layers import clear_runtime_overrides as _clear_runtime_overrides
    from .config_layers import get_runtime_overrides as _get_runtime_overrides
    from .config_layers import resolve_layered_config
    from .config_layers import set_runtime_overrides as _set_runtime_overrides
except ImportError:
    try:
        from services.config_layers import (
            clear_runtime_overrides as _clear_runtime_overrides,  # type: ignore
        )
        from services.config_layers import (
            get_runtime_overrides as _get_runtime_overrides,  # type: ignore
        )
        from services.config_layers import resolve_layered_config  # type: ignore
        from services.config_layers import (
            set_runtime_overrides as _set_runtime_overrides,  # type: ignore
        )
    except ImportError:
        _RUNTIME_OVERRIDES: Dict[str, Dict[str, Any]] = {}

        def _get_runtime_overrides(section):  # type: ignore
            return dict(_RUNTIME_OVERRIDES.get(section, {}))

        def _set_runtime_overrides(section, updates):  # type: ignore
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

        def _clear_runtime_overrides(section, keys=None):  # type: ignore
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

        def resolve_layered_config(  # type: ignore
            *,
            ordered_keys,
            defaults,
            persisted=None,
            runtime_overrides=None,
            env_getter=None,
            normalize_value=None,
        ):
            persisted = dict(persisted or {})
            runtime_overrides = dict(runtime_overrides or {})
            effective = {}
            sources = {}
            for key in ordered_keys:
                value = defaults.get(key)
                source = "default"
                if key in persisted:
                    value = persisted.get(key)
                    source = SOURCE_PERSISTED
                if key in runtime_overrides:
                    value = runtime_overrides.get(key)
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


try:
    from .runtime_config_policy import (
        ALLOWED_LLM_KEYS,
        DEFAULTS,
        ENV_MAPPINGS,
        LLM_KEY_ORDER,
        SOURCE_ENV,
        SOURCE_PERSISTED,
        SOURCE_RUNTIME_OVERRIDE,
        SSRFError,
    )
    from .runtime_config_policy import env_flag as _env_flag_impl
    from .runtime_config_policy import get_admin_token, get_apply_semantics
    from .runtime_config_policy import get_env_value as _get_env_value_impl
    from .runtime_config_policy import (
        get_llm_egress_controls,
        get_scheduler_config,
        get_settings_schema_map,
        is_config_write_enabled,
        is_loopback_client,
    )
    from .runtime_config_policy import merge_config_value as _merge_config_value_impl
    from .runtime_config_policy import normalize_llm_layer_value, validate_admin_token
    from .runtime_config_policy import (
        validate_config_update as _validate_config_update_impl,
    )
    from .runtime_config_policy import validate_outbound_url
except ImportError:
    from services.runtime_config_policy import (
        ALLOWED_LLM_KEYS,
        DEFAULTS,
        ENV_MAPPINGS,
        LLM_KEY_ORDER,
        SOURCE_ENV,
        SOURCE_PERSISTED,
        SOURCE_RUNTIME_OVERRIDE,
        SSRFError,
    )
    from services.runtime_config_policy import (
        env_flag as _env_flag_impl,  # type: ignore
    )
    from services.runtime_config_policy import get_admin_token, get_apply_semantics
    from services.runtime_config_policy import get_env_value as _get_env_value_impl
    from services.runtime_config_policy import (
        get_llm_egress_controls,
        get_scheduler_config,
        get_settings_schema_map,
        is_config_write_enabled,
        is_loopback_client,
    )
    from services.runtime_config_policy import (
        merge_config_value as _merge_config_value_impl,
    )
    from services.runtime_config_policy import (
        normalize_llm_layer_value,
        validate_admin_token,
    )
    from services.runtime_config_policy import (
        validate_config_update as _validate_config_update_impl,
    )
    from services.runtime_config_policy import validate_outbound_url

try:
    from .runtime_config_projection import RuntimeConfig, build_runtime_config_snapshot
except ImportError:
    from services.runtime_config_projection import (  # type: ignore
        RuntimeConfig,
        build_runtime_config_snapshot,
    )

try:
    from .runtime_config_store import (
        DEFAULT_TENANT_ID,
        get_default_config_file,
        get_runtime_override_section,
        load_file_config,
        resolve_active_tenant_id,
        save_file_config,
        tenant_llm_config_view,
    )
except ImportError:
    from services.runtime_config_store import (  # type: ignore
        DEFAULT_TENANT_ID,
        get_default_config_file,
        get_runtime_override_section,
        load_file_config,
        resolve_active_tenant_id,
        save_file_config,
        tenant_llm_config_view,
    )

try:
    from .runtime_guardrails import get_runtime_guardrails_snapshot
except ImportError:
    from services.runtime_guardrails import (
        get_runtime_guardrails_snapshot,  # type: ignore
    )


# CRITICAL: keep CONFIG_FILE as a facade-owned compatibility seam because tests
# and callers patch `services.runtime_config.CONFIG_FILE` directly.
CONFIG_FILE = get_default_config_file()


def _get_env_value(key: str) -> Optional[str]:
    return _get_env_value_impl(key, logger=logger)


def _env_flag(primary: str, legacy: str, default: bool = False) -> bool:
    return _env_flag_impl(primary, legacy, default)


def _runtime_override_section(tenant_id: Optional[str] = None) -> str:
    return get_runtime_override_section(tenant_id)


def _load_file_config() -> Dict[str, Any]:
    return load_file_config(CONFIG_FILE, logger=logger)


def _save_file_config(config: Dict[str, Any]) -> bool:
    return save_file_config(CONFIG_FILE, config, logger=logger)


def _merge_config_value(base: Any, patch: Any, key: str = "") -> Any:
    return _merge_config_value_impl(base, patch, key=key)


def get_runtime_overrides(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    return _get_runtime_overrides(_runtime_override_section(tenant_id))


def set_runtime_overrides(
    updates: Dict[str, Any], tenant_id: Optional[str] = None
) -> Tuple[bool, list]:
    sanitized, errors = validate_config_update(updates)
    if errors:
        return False, errors
    _set_runtime_overrides(_runtime_override_section(tenant_id), sanitized)
    return True, []


def clear_runtime_overrides(
    keys: Optional[List[str]] = None, tenant_id: Optional[str] = None
) -> None:
    _clear_runtime_overrides(_runtime_override_section(tenant_id), keys=keys)


def get_effective_config(
    tenant_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    active_tenant = resolve_active_tenant_id(tenant_id)
    file_blob = _load_file_config()
    file_config = tenant_llm_config_view(file_blob, active_tenant)
    runtime_overrides = get_runtime_overrides(active_tenant)

    ordered_keys = list(LLM_KEY_ORDER) + [
        key for key in sorted(ALLOWED_LLM_KEYS) if key not in ENV_MAPPINGS
    ]
    return resolve_layered_config(
        ordered_keys=ordered_keys,
        defaults=DEFAULTS["llm"],
        persisted=file_config,
        runtime_overrides=runtime_overrides,
        env_getter=_get_env_value,
        normalize_value=normalize_llm_layer_value,
    )


def get_settings_schema() -> dict:
    return get_settings_schema_map()


def get_runtime_guardrails() -> Dict[str, Any]:
    return get_runtime_guardrails_snapshot()


def validate_config_update(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], list]:
    return _validate_config_update_impl(
        updates,
        validate_url=validate_outbound_url,
        ssrf_error_type=SSRFError,
    )


def update_config(
    updates: Dict[str, Any], tenant_id: Optional[str] = None
) -> Tuple[bool, list]:
    sanitized, errors = validate_config_update(updates)
    if errors:
        return False, errors

    if not sanitized:
        return True, []

    tenant_id = resolve_active_tenant_id(tenant_id)
    file_config = _load_file_config()
    if tenant_id == DEFAULT_TENANT_ID:
        if "llm" not in file_config:
            file_config["llm"] = {}
        target = file_config["llm"]
    else:
        tenants = file_config.get("tenants")
        if not isinstance(tenants, dict):
            tenants = {}
            file_config["tenants"] = tenants
        tenant_cfg = tenants.get(tenant_id)
        if not isinstance(tenant_cfg, dict):
            tenant_cfg = {}
            tenants[tenant_id] = tenant_cfg
        if "llm" not in tenant_cfg or not isinstance(tenant_cfg.get("llm"), dict):
            tenant_cfg["llm"] = {}
        target = tenant_cfg["llm"]

    for key, value in sanitized.items():
        target[key] = _merge_config_value(target.get(key), value, key=key)

    if _save_file_config(file_config):
        logger.info("Updated config: %s (tenant=%s)", list(sanitized.keys()), tenant_id)
        return True, []
    return False, ["Failed to save config file"]


def get_config() -> RuntimeConfig:
    return build_runtime_config_snapshot(
        get_effective_config=get_effective_config,
        get_runtime_guardrails_snapshot=get_runtime_guardrails_snapshot,
        env_flag=_env_flag,
        get_admin_token=get_admin_token,
    )
