"""
S11 secret provider chain.

Provides deterministic API-key resolution via pluggable providers:
- env
- optional 1Password CLI
- encrypted server-side secret store
- default none
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, Protocol

from .env_aliases import get_env_value

try:
    from .config_layers import GENERIC_LLM_API_KEY_ENV_KEYS
except ImportError:
    from services.config_layers import GENERIC_LLM_API_KEY_ENV_KEYS  # type: ignore

try:
    from .providers.catalog import get_provider_info
except ImportError:
    from services.providers.catalog import get_provider_info  # type: ignore

try:
    from .tenant_context import (
        DEFAULT_TENANT_ID,
        get_current_tenant_id,
        is_multi_tenant_enabled,
        normalize_tenant_id,
    )
except ImportError:
    from services.tenant_context import (  # type: ignore
        DEFAULT_TENANT_ID,
        get_current_tenant_id,
        is_multi_tenant_enabled,
        normalize_tenant_id,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.services.secret_providers")

_TRUTHY = {"1", "true", "yes", "on"}
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VAULT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FIELD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def _env_value(
    primary: str, legacy: str, default: Optional[str] = None
) -> Optional[str]:
    return get_env_value(primary, aliases=(legacy,), default=default)


class SecretProvider(Protocol):
    source: str

    def get_secret(self, provider: str, tenant_id: str) -> Optional[str]: ...


class EnvSecretProvider:
    source = "env"

    def _provider_env_candidates(self, provider: str) -> list[str]:
        info = get_provider_info(provider)
        if not info or not info.env_key_name:
            return []

        candidates: list[str] = []
        if info.env_key_name.startswith("MOLTBOT_"):
            candidates.append(info.env_key_name.replace("MOLTBOT_", "OPENCLAW_", 1))
        candidates.append(info.env_key_name)
        return candidates

    def get_secret(self, provider: str, tenant_id: str) -> Optional[str]:
        # Provider-specific first
        provider_candidates = self._provider_env_candidates(provider)
        if provider_candidates:
            value = get_env_value(
                provider_candidates[0], aliases=provider_candidates[1:]
            )
            if value:
                return value

        # Generic fallback
        return get_env_value(
            GENERIC_LLM_API_KEY_ENV_KEYS[0],
            aliases=GENERIC_LLM_API_KEY_ENV_KEYS[1:],
        )


class OnePasswordSecretProvider:
    source = "onepassword"

    def _enabled(self) -> bool:
        return _is_truthy(
            _env_value("OPENCLAW_1PASSWORD_ENABLED", "MOLTBOT_1PASSWORD_ENABLED", "0")
        )

    def _command(self) -> str:
        command = _env_value("OPENCLAW_1PASSWORD_CMD", "MOLTBOT_1PASSWORD_CMD", "op")
        return str(command or "").strip()

    def _allowed_commands(self) -> set[str]:
        raw = _env_value(
            "OPENCLAW_1PASSWORD_ALLOWED_COMMANDS",
            "MOLTBOT_1PASSWORD_ALLOWED_COMMANDS",
            "",
        )
        return {
            token.strip().lower()
            for token in str(raw or "").split(",")
            if token and token.strip()
        }

    def _vault(self) -> str:
        return str(
            _env_value("OPENCLAW_1PASSWORD_VAULT", "MOLTBOT_1PASSWORD_VAULT", "") or ""
        ).strip()

    def _item_template(self) -> str:
        default_template = "openclaw/{provider}"
        if is_multi_tenant_enabled():
            default_template = "openclaw/{tenant}/{provider}"
        return str(
            _env_value(
                "OPENCLAW_1PASSWORD_ITEM_TEMPLATE",
                "MOLTBOT_1PASSWORD_ITEM_TEMPLATE",
                default_template,
            )
            or ""
        ).strip()

    def _field(self) -> str:
        return str(
            _env_value("OPENCLAW_1PASSWORD_FIELD", "MOLTBOT_1PASSWORD_FIELD", "api_key")
            or ""
        ).strip()

    def _timeout_sec(self) -> float:
        raw = _env_value(
            "OPENCLAW_1PASSWORD_TIMEOUT_SEC", "MOLTBOT_1PASSWORD_TIMEOUT_SEC", "5"
        )
        try:
            value = float(raw or "5")
        except (TypeError, ValueError):
            value = 5.0
        return max(1.0, min(value, 30.0))

    def is_available(self) -> bool:
        if not self._enabled():
            return False

        allowed = self._allowed_commands()
        if not allowed:
            # CRITICAL: explicit allowlist is required when 1Password provider is enabled.
            logger.warning(
                "S11: 1Password enabled but OPENCLAW_1PASSWORD_ALLOWED_COMMANDS is empty; fail-closed."
            )
            return False

        command = self._command()
        if not command:
            logger.warning("S11: 1Password command is empty; fail-closed.")
            return False
        command_name = Path(command).name.lower()
        if command_name not in allowed:
            logger.warning(
                "S11: 1Password command '%s' not in allowlist; fail-closed.",
                command_name,
            )
            return False

        vault = self._vault()
        field = self._field()
        template = self._item_template()
        if not vault or not _VAULT_RE.fullmatch(vault):
            logger.warning("S11: 1Password vault is missing/invalid; fail-closed.")
            return False
        if not field or not _FIELD_RE.fullmatch(field):
            logger.warning("S11: 1Password field is invalid; fail-closed.")
            return False
        if "{provider}" not in template:
            logger.warning(
                "S11: 1Password item template must include '{provider}'; fail-closed."
            )
            return False
        if is_multi_tenant_enabled() and "{tenant}" not in template:
            logger.warning(
                "S49/S11: 1Password item template must include '{tenant}' in multi-tenant mode; fail-closed."
            )
            return False
        return True

    def _build_ref(self, provider: str, tenant_id: str) -> Optional[str]:
        if not _PROVIDER_ID_RE.fullmatch(provider):
            logger.warning(
                "S11: Invalid provider id for 1Password lookup; fail-closed."
            )
            return None
        template = self._item_template()
        item = template.format(provider=provider, tenant=tenant_id)
        if not item or not _ITEM_RE.fullmatch(item) or ".." in item:
            logger.warning("S11: 1Password item name is invalid; fail-closed.")
            return None
        return f"op://{self._vault()}/{item}/{self._field()}"

    def _read_ref(self, ref: str, provider: str) -> Optional[str]:
        command = self._command()
        args = [command, "read", ref]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self._timeout_sec(),
                check=False,
            )
        except FileNotFoundError:
            logger.warning("S11: 1Password command not found; fail-closed.")
            return None
        except subprocess.TimeoutExpired:
            logger.warning(
                "S11: 1Password lookup timed out for provider '%s'; fail-closed.",
                provider,
            )
            return None
        except Exception as exc:
            logger.warning(
                "S11: 1Password lookup error for provider '%s' (%s); fail-closed.",
                provider,
                type(exc).__name__,
            )
            return None

        if completed.returncode != 0:
            logger.warning(
                "S11: 1Password lookup failed for provider '%s' (exit=%s); fail-closed.",
                provider,
                completed.returncode,
            )
            return None

        value = (completed.stdout or "").strip()
        return value or None

    def get_secret(self, provider: str, tenant_id: str) -> Optional[str]:
        if not self.is_available():
            return None

        provider_ref = self._build_ref(provider, tenant_id)
        if provider_ref:
            value = self._read_ref(provider_ref, provider)
            if value:
                return value

        if provider != "generic":
            generic_ref = self._build_ref("generic", tenant_id)
            if generic_ref:
                return self._read_ref(generic_ref, "generic")
        return None


class ServerStoreSecretProvider:
    source = "server_store"

    def get_secret(self, provider: str, tenant_id: str) -> Optional[str]:
        try:
            from .secret_store import get_secret_store
        except ImportError:
            from services.secret_store import get_secret_store  # type: ignore

        try:
            store = get_secret_store()
            value = store.get_secret(provider, tenant_id=tenant_id)
            if value:
                return value
            if provider != "generic":
                return store.get_secret("generic", tenant_id=tenant_id)
        except Exception as exc:
            logger.debug(
                "S25/S11: secret store lookup failed (non-fatal): %s",
                exc,
            )
        return None


def get_secret_providers() -> list[SecretProvider]:
    """Return key-resolution providers in deterministic order."""
    return [
        EnvSecretProvider(),
        OnePasswordSecretProvider(),
        ServerStoreSecretProvider(),
    ]


def resolve_provider_secret(
    provider: str, tenant_id: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve a provider secret through the configured provider chain.

    Returns:
        (secret, source) where source in {"env","onepassword","server_store"} or None.
    """
    effective_tenant = normalize_tenant_id(
        tenant_id or get_current_tenant_id() or DEFAULT_TENANT_ID
    )

    for resolver in get_secret_providers():
        secret = resolver.get_secret(provider, effective_tenant)
        if secret:
            return secret, resolver.source
    return None, None
