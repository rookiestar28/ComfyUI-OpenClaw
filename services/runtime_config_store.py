"""
Persisted config storage and tenant-resolution helpers for runtime_config.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from .env_aliases import get_env_value

try:
    from .runtime_guardrails import strip_runtime_only_config_fields
except ImportError:
    from services.runtime_guardrails import (
        strip_runtime_only_config_fields,  # type: ignore
    )

try:
    from .tenant_context import (
        DEFAULT_TENANT_ID,
        get_current_tenant_id,
        is_multi_tenant_enabled,
        normalize_tenant_id,
    )
except ImportError:
    try:
        from services.tenant_context import (  # type: ignore
            DEFAULT_TENANT_ID,
            get_current_tenant_id,
            is_multi_tenant_enabled,
            normalize_tenant_id,
        )
    except ImportError:
        DEFAULT_TENANT_ID = "default"

        def get_current_tenant_id():  # type: ignore
            return DEFAULT_TENANT_ID

        def is_multi_tenant_enabled():  # type: ignore
            return False

        def normalize_tenant_id(value):  # type: ignore
            return str(value or DEFAULT_TENANT_ID).strip().lower() or DEFAULT_TENANT_ID


def get_default_config_file() -> str:
    try:
        # CRITICAL: keep path resolution import-safe; do not call get_state_dir()
        # here or plain imports recreate state on disk during bootstrap/tests.
        from .state_dir import peek_state_dir

        return os.path.join(peek_state_dir(), "config.json")
    except ImportError:
        try:
            from services.state_dir import peek_state_dir  # type: ignore

            return os.path.join(peek_state_dir(), "config.json")
        except ImportError:
            return os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "data",
                "config.json",
            )


def resolve_active_tenant_id(tenant_id: Optional[str] = None) -> str:
    if not is_multi_tenant_enabled():
        return DEFAULT_TENANT_ID
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    try:
        return normalize_tenant_id(tenant_id)
    except Exception:
        return DEFAULT_TENANT_ID


def get_runtime_override_section(tenant_id: Optional[str] = None) -> str:
    resolved = resolve_active_tenant_id(tenant_id)
    if resolved == DEFAULT_TENANT_ID:
        return "llm"
    return f"llm::{resolved}"


def tenant_llm_config_view(
    config_blob: Dict[str, Any], tenant_id: str
) -> Dict[str, Any]:
    llm_global = config_blob.get("llm", {})
    if tenant_id == DEFAULT_TENANT_ID:
        return llm_global if isinstance(llm_global, dict) else {}

    tenants = config_blob.get("tenants", {})
    tenant_cfg = {}
    if isinstance(tenants, dict):
        tenant_cfg = tenants.get(tenant_id, {})
    tenant_llm = tenant_cfg.get("llm", {}) if isinstance(tenant_cfg, dict) else {}
    if isinstance(tenant_llm, dict) and tenant_llm:
        return tenant_llm
    if _allow_tenant_config_fallback() and isinstance(llm_global, dict):
        return llm_global
    return {}


def load_file_config(config_file: str, *, logger: logging.Logger) -> Dict[str, Any]:
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                sanitized, notices = strip_runtime_only_config_fields(raw)
                if notices:
                    logger.warning(
                        "S66: Ignoring runtime-only guardrail keys from persisted config (%s)",
                        ", ".join(n.get("path", "?") for n in notices),
                    )
                return sanitized
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load config file: %s", exc)
    return {}


def save_file_config(
    config_file: str,
    config: Dict[str, Any],
    *,
    logger: logging.Logger,
) -> bool:
    try:
        config_to_save, notices = strip_runtime_only_config_fields(config)
        if notices:
            logger.warning(
                "S66: Stripped runtime-only guardrail keys before config save (%s)",
                ", ".join(n.get("path", "?") for n in notices),
            )
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as fh:
            json.dump(config_to_save, fh, indent=2)
        logger.info("Saved config to %s", config_file)
        return True
    except OSError as exc:
        logger.error("Failed to save config file: %s", exc)
        return False


def _allow_tenant_config_fallback() -> bool:
    value = get_env_value("OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK", default="0")
    return str(value).strip().lower() in ("1", "true", "yes", "on")
