"""Central environment-alias registry and lookup contract."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

logger = logging.getLogger("ComfyUI-OpenClaw.services.env_aliases")


class EnvLookupMode(str, Enum):
    """Explicit legacy-compatible value-selection semantics."""

    PRESENCE = "presence"
    NONEMPTY = "nonempty"
    TRUTHY_ANY = "truthy_any"


@dataclass(frozen=True, slots=True)
class EnvAliasSpec:
    canonical: str
    aliases: tuple[str, ...]
    sensitive: bool


@dataclass(frozen=True, slots=True)
class EnvResolution:
    value: str | None
    selected_key: str | None
    used_legacy: bool


LEGACY_MOLTBOT_ENV_KEYS = frozenset(
    {
        "MOLTBOT_1PASSWORD_ALLOWED_COMMANDS",
        "MOLTBOT_1PASSWORD_CMD",
        "MOLTBOT_1PASSWORD_ENABLED",
        "MOLTBOT_1PASSWORD_FIELD",
        "MOLTBOT_1PASSWORD_ITEM_TEMPLATE",
        "MOLTBOT_1PASSWORD_TIMEOUT_SEC",
        "MOLTBOT_1PASSWORD_VAULT",
        "MOLTBOT_ADMIN_TOKEN",
        "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
        "MOLTBOT_ALLOW_CUSTOM_BASE_URL",
        "MOLTBOT_ALLOW_INSECURE_BASE_URL",
        "MOLTBOT_ALLOW_REMOTE_ADMIN",
        "MOLTBOT_ANTHROPIC_API_KEY",
        "MOLTBOT_APPROVAL_TTL_SEC",
        "MOLTBOT_AUDIT_CHAIN_KEY",
        "MOLTBOT_AUDIT_CHAIN_KEY_PATH",
        "MOLTBOT_AUDIT_LOG_PATH",
        "MOLTBOT_AUDIT_MAX_BACKUPS",
        "MOLTBOT_AUDIT_MAX_BYTES",
        "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS",
        "MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST",
        "MOLTBOT_BRIDGE_DEVICE_TOKEN",
        "MOLTBOT_BRIDGE_ENABLED",
        "MOLTBOT_CALLBACK_ALLOWLIST",
        "MOLTBOT_CALLBACK_ALLOW_HOSTS",
        "MOLTBOT_CALLBACK_MAX_RETRIES",
        "MOLTBOT_CALLBACK_TIMEOUT_SEC",
        "MOLTBOT_COMFYUI_URL",
        "MOLTBOT_CUSTOM_API_KEY",
        "MOLTBOT_DEBUG_REASONING_REVEAL",
        "MOLTBOT_DEEPSEEK_API_KEY",
        "MOLTBOT_DEV_MODE",
        "MOLTBOT_DIAGNOSTICS",
        "MOLTBOT_FALLBACK_MODELS",
        "MOLTBOT_FALLBACK_PROVIDERS",
        "MOLTBOT_GEMINI_API_KEY",
        "MOLTBOT_GROQ_API_KEY",
        "MOLTBOT_IO_EXECUTOR_WORKERS",
        "MOLTBOT_LLM_ALLOWED_HOSTS",
        "MOLTBOT_LLM_ALLOW_PRIVATE_NETWORK",
        "MOLTBOT_LLM_API_KEY",
        "MOLTBOT_LLM_BASE_URL",
        "MOLTBOT_LLM_EXECUTOR_WORKERS",
        "MOLTBOT_LLM_MAX_RETRIES",
        "MOLTBOT_LLM_MODEL",
        "MOLTBOT_LLM_PROVIDER",
        "MOLTBOT_LLM_TIMEOUT",
        "MOLTBOT_LOG_FORMAT",
        "MOLTBOT_LOG_TRUNCATE_ON_START",
        "MOLTBOT_MAX_FAILOVER_CANDIDATES",
        "MOLTBOT_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC",
        "MOLTBOT_MODEL_DOWNLOAD_ALLOW_HOSTS",
        "MOLTBOT_MODEL_DOWNLOAD_ALLOW_LOOPBACK_HOSTS",
        "MOLTBOT_MODEL_DOWNLOAD_MAX_ACTIVE",
        "MOLTBOT_MODEL_DOWNLOAD_MAX_CONCURRENCY",
        "MOLTBOT_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT",
        "MOLTBOT_MODEL_DOWNLOAD_TIMEOUT_SEC",
        "MOLTBOT_MODEL_INSTALL_ROOT",
        "MOLTBOT_MULTI_TENANT_ALLOW_CONFIG_FALLBACK",
        "MOLTBOT_MULTI_TENANT_ALLOW_DEFAULT_FALLBACK",
        "MOLTBOT_MULTI_TENANT_ALLOW_LEGACY_SECRET_FALLBACK",
        "MOLTBOT_MULTI_TENANT_ENABLED",
        "MOLTBOT_OBSERVABILITY_TOKEN",
        "MOLTBOT_OPENAI_API_KEY",
        "MOLTBOT_OPENROUTER_API_KEY",
        "MOLTBOT_PRESETS_PUBLIC_READ",
        "MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
        "MOLTBOT_REDACTION_TAG_KEY",
        "MOLTBOT_REQUIRE_APPROVAL_FOR_TRIGGERS",
        "MOLTBOT_SECURITY_DANGEROUS_BIND_OVERRIDE",
        "MOLTBOT_STARTUP_WARMUP_TIMEOUT_SEC",
        "MOLTBOT_STATE_DIR",
        "MOLTBOT_STRICT_LOCALHOST_AUTH",
        "MOLTBOT_STRUCTURED_LOGS",
        "MOLTBOT_TELEMETRY_OPT_OUT",
        "MOLTBOT_TENANT_HEADER",
        "MOLTBOT_THREAD_POOL_WORKERS",
        "MOLTBOT_TOOL_SANDBOX_DIR",
        "MOLTBOT_TRUSTED_PROXIES",
        "MOLTBOT_TRUST_X_FORWARDED_FOR",
        "MOLTBOT_WEBHOOK_AUTH_MODE",
        "MOLTBOT_WEBHOOK_BEARER_TOKEN",
        "MOLTBOT_WEBHOOK_HMAC_SECRET",
        "MOLTBOT_WEBHOOK_REQUIRE_REPLAY_PROTECTION",
        "MOLTBOT_WEBHOOK_SECRET",
        "MOLTBOT_XAI_API_KEY",
    }
)

SUPPORTED_CLAWDBOT_ENV_KEYS = frozenset({"CLAWDBOT_LLM_API_KEY"})
SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS = frozenset(
    {
        "MOLTBOT_RATE_LIMIT_ADMIN_DAILY_CAP",
        "MOLTBOT_RATE_LIMIT_BRIDGE_DAILY_CAP",
        "MOLTBOT_RATE_LIMIT_CONNECTOR_DAILY_CAP",
        "MOLTBOT_RATE_LIMIT_EVENTS_DAILY_CAP",
        "MOLTBOT_RATE_LIMIT_TRIGGER_DAILY_CAP",
        "MOLTBOT_RATE_LIMIT_WEBHOOK_DAILY_CAP",
    }
)
REJECTED_LEGACY_ENV_KEYS = frozenset({"CLAWDBOT_GATEWAY_TOKEN"})
_SUPPORTED_LEGACY_ENV_KEYS = (
    LEGACY_MOLTBOT_ENV_KEYS
    | SUPPORTED_CLAWDBOT_ENV_KEYS
    | SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS
)
_SENSITIVE_KEY_PARTS = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "CERT")
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _canonical_for_moltbot(legacy: str) -> str:
    return "OPENCLAW_" + legacy.removeprefix("MOLTBOT_")


def _is_sensitive_key(key: str) -> bool:
    return any(part in key for part in _SENSITIVE_KEY_PARTS)


def _build_registry() -> Mapping[str, EnvAliasSpec]:
    specs: dict[str, EnvAliasSpec] = {}
    for legacy in sorted(LEGACY_MOLTBOT_ENV_KEYS):
        canonical = _canonical_for_moltbot(legacy)
        aliases: tuple[str, ...] = (legacy,)
        if canonical == "OPENCLAW_LLM_API_KEY":
            aliases += ("CLAWDBOT_LLM_API_KEY",)
        specs[canonical] = EnvAliasSpec(
            canonical=canonical,
            aliases=aliases,
            sensitive=_is_sensitive_key(canonical),
        )
    return MappingProxyType(specs)


ENV_ALIAS_REGISTRY = _build_registry()

_warning_lock = threading.Lock()
_warned_legacy_keys: set[str] = set()


def _normalize_mode(mode: EnvLookupMode | str) -> EnvLookupMode:
    if isinstance(mode, EnvLookupMode):
        return mode
    return EnvLookupMode(str(mode))


def _select_value(
    env: Mapping[str, str],
    keys: Iterable[str],
    mode: EnvLookupMode,
) -> tuple[str | None, str | None]:
    for key in keys:
        if key not in env:
            continue
        value = env.get(key)
        if mode is EnvLookupMode.PRESENCE:
            return value, key
        if mode is EnvLookupMode.NONEMPTY and value:
            return value, key
        if (
            mode is EnvLookupMode.TRUTHY_ANY
            and str(value or "").strip().lower() in _TRUTHY_VALUES
        ):
            return value, key
    return None, None


def _warn_legacy_once(
    legacy: str,
    canonical: str,
    *,
    target_logger: logging.Logger,
) -> None:
    # IMPORTANT: dedupe must remain process-wide and locked. Moving state into callers or checking
    # outside the lock reintroduces duplicate warning storms under concurrent request paths.
    with _warning_lock:
        if legacy in _warned_legacy_keys:
            return
        _warned_legacy_keys.add(legacy)
    # CRITICAL: log key names only; values and value-derived metadata can expose credentials.
    target_logger.warning(
        "OPENCLAW_LEGACY_ENV_ALIAS_USED legacy=%s canonical=%s",
        legacy,
        canonical,
    )


def resolve_env(
    canonical: str,
    *,
    aliases: Iterable[str] | None = None,
    mode: EnvLookupMode | str = EnvLookupMode.NONEMPTY,
    default: str | None = None,
    env: Mapping[str, str] | None = None,
    warn_legacy: bool | None = None,
    target_logger: logging.Logger | None = None,
) -> EnvResolution:
    """Resolve a canonical key and ordered aliases without exposing values in diagnostics."""

    process_environment = env is None
    env_map = os.environ if process_environment else env
    assert env_map is not None
    spec = ENV_ALIAS_REGISTRY.get(canonical)
    ordered_aliases = (
        tuple(aliases) if aliases is not None else (spec.aliases if spec else ())
    )
    rejected = tuple(
        alias for alias in ordered_aliases if alias in REJECTED_LEGACY_ENV_KEYS
    )
    unknown_legacy = tuple(
        alias
        for alias in ordered_aliases
        if alias.startswith(("MOLTBOT_", "CLAWDBOT_"))
        and alias not in _SUPPORTED_LEGACY_ENV_KEYS
        and alias not in REJECTED_LEGACY_ENV_KEYS
    )
    # CRITICAL: explicit aliases are a reviewed dynamic-family seam, not an escape hatch for
    # rejected or unknown legacy keys. Accepting CLAWDBOT_GATEWAY_TOKEN here silently revived it.
    if rejected or unknown_legacy:
        invalid = (*rejected, *unknown_legacy)
        raise ValueError(f"unsupported legacy environment alias: {invalid[0]}")
    normalized_mode = _normalize_mode(mode)
    value, selected_key = _select_value(
        env_map,
        (canonical, *ordered_aliases),
        normalized_mode,
    )
    if selected_key is None:
        return EnvResolution(default, None, False)

    used_legacy = selected_key != canonical
    should_warn = process_environment if warn_legacy is None else bool(warn_legacy)
    if used_legacy and should_warn:
        _warn_legacy_once(
            selected_key,
            canonical,
            target_logger=target_logger or logger,
        )
    return EnvResolution(value, selected_key, used_legacy)


def get_env_value(
    canonical: str,
    *,
    aliases: Iterable[str] | None = None,
    mode: EnvLookupMode | str = EnvLookupMode.NONEMPTY,
    default: str | None = None,
    env: Mapping[str, str] | None = None,
    warn_legacy: bool | None = None,
    target_logger: logging.Logger | None = None,
) -> str | None:
    """Return only the resolved value for callers that do not need provenance."""

    return resolve_env(
        canonical,
        aliases=aliases,
        mode=mode,
        default=default,
        env=env,
        warn_legacy=warn_legacy,
        target_logger=target_logger,
    ).value


def reset_warning_state_for_tests() -> None:
    """Clear process warning state for isolated tests only."""

    with _warning_lock:
        _warned_legacy_keys.clear()


__all__ = [
    "ENV_ALIAS_REGISTRY",
    "LEGACY_MOLTBOT_ENV_KEYS",
    "REJECTED_LEGACY_ENV_KEYS",
    "SUPPORTED_CLAWDBOT_ENV_KEYS",
    "SUPPORTED_DYNAMIC_MOLTBOT_ENV_KEYS",
    "EnvAliasSpec",
    "EnvLookupMode",
    "EnvResolution",
    "get_env_value",
    "reset_warning_state_for_tests",
    "resolve_env",
]
