"""
S25: Server-side Secret Store

Secure persistence for API keys and sensitive configuration values.
Designed for localhost-only, single-user scenarios where UI secret entry is more convenient than ENV vars.

Security principles:
- Never log secret values
- Best-effort file permissions (0600 on POSIX)
- Clear source tracking (env vs server_store)
- S57: At-rest encryption via Fernet AEAD (secrets.enc.json)
- Automatic migration from legacy plaintext secrets.json

Storage location: {STATE_DIR}/secrets.enc.json
"""

import json
import logging
import os
import stat
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .env_aliases import get_env_value

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


try:
    from .state_dir import get_state_dir
except ImportError:
    from state_dir import get_state_dir

logger = logging.getLogger("ComfyUI-OpenClaw.services.secret_store")

# S57: Encrypted store filename (replaces legacy secrets.json)
SECRET_STORE_FILE = "secrets.enc.json"
LEGACY_STORE_FILE = "secrets.json"


def _get_encryption_module():
    """Lazy-load secrets_encryption to avoid circular imports."""
    try:
        from . import secrets_encryption

        return secrets_encryption
    except ImportError:
        import secrets_encryption  # type: ignore

        return secrets_encryption


class SecretStore:
    """
    Server-side secret storage with encrypted file-based persistence.

    Thread-safe for read/write operations.
    S57: All secrets are encrypted at rest using Fernet AEAD.
    """

    def __init__(self, state_dir: Optional[str] = None):
        """
        Initialize secret store.

        Args:
            state_dir: Override state directory (for testing)
        """
        self._state_dir = Path(state_dir) if state_dir else Path(get_state_dir())
        self._store_path = self._state_dir / SECRET_STORE_FILE
        self._legacy_path = self._state_dir / LEGACY_STORE_FILE
        self._secrets: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._encryption_key: Optional[bytes] = None
        self._load()

    def _get_key(self) -> bytes:
        """Get or load the encryption key."""
        if self._encryption_key is None:
            enc = _get_encryption_module()
            self._encryption_key = enc._load_or_create_key(self._state_dir)
        return self._encryption_key

    def _resolve_tenant_id(self, tenant_id: Optional[str]) -> str:
        if tenant_id is not None:
            return normalize_tenant_id(tenant_id)
        if is_multi_tenant_enabled():
            try:
                return normalize_tenant_id(get_current_tenant_id())
            except Exception:
                return DEFAULT_TENANT_ID
        return DEFAULT_TENANT_ID

    def _tenant_key(self, provider_id: str, tenant_id: Optional[str] = None) -> str:
        tenant = self._resolve_tenant_id(tenant_id)
        if tenant == DEFAULT_TENANT_ID:
            return provider_id
        return f"tenant::{tenant}::{provider_id}"

    def _allow_legacy_fallback(self) -> bool:
        value = get_env_value(
            "OPENCLAW_MULTI_TENANT_ALLOW_LEGACY_SECRET_FALLBACK", default="0"
        )
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _migrate_legacy(self) -> bool:
        """Migrate legacy plaintext secrets.json to encrypted format."""
        if not self._legacy_path.exists():
            return False
        if self._store_path.exists():
            # Both exist — encrypted store takes precedence
            return False

        try:
            with open(self._legacy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False

            self._secrets = data
            self._save()

            # Rename legacy file to prevent re-migration
            backup_path = self._legacy_path.with_suffix(".json.bak")
            self._legacy_path.rename(backup_path)
            logger.info(
                f"S57: Migrated {len(data)} secrets from plaintext to encrypted store "
                f"(legacy file renamed to {backup_path.name})"
            )
            return True
        except Exception as e:
            logger.error(f"S57: Failed to migrate legacy secrets: {e}")
            return False

    def _load(self) -> None:
        """Load secrets from disk (encrypted)."""
        with self._lock:
            # Try encrypted store first
            if self._store_path.exists():
                try:
                    if self._store_path.stat().st_size == 0:
                        logger.warning(
                            "S57: Encrypted store file empty (likely crashed write)"
                        )
                        return

                    with open(self._store_path, "r", encoding="utf-8") as f:
                        envelope_data = json.load(f)

                    # Decrypt the envelope
                    enc = _get_encryption_module()
                    envelope = enc.EncryptedEnvelope.from_dict(envelope_data)
                    key = self._get_key()
                    self._secrets = enc.decrypt_secrets(envelope, key)
                    logger.info(
                        f"S57: Loaded {len(self._secrets)} secrets from encrypted store"
                    )
                    return
                except Exception as e:
                    logger.error(f"S57: Failed to load encrypted store: {e}")
                    return

            # Try legacy migration
            if self._migrate_legacy():
                return

            # No store exists
            logger.debug(
                "S25: Secret store file not found (will be created on first write)"
            )

    def _save(self) -> None:
        """Save secrets to disk with encryption."""
        with self._lock:
            try:
                # Ensure state dir exists
                self._state_dir.mkdir(parents=True, exist_ok=True)

                # Encrypt secrets
                enc = _get_encryption_module()
                key = self._get_key()
                envelope = enc.encrypt_secrets(self._secrets, key)
                envelope_dict = envelope.to_dict()

                # Write atomically (temp file + rename)
                temp_path = self._store_path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(envelope_dict, f, indent=2)

                # Best-effort file permissions (POSIX only)
                try:
                    if hasattr(os, "chmod"):
                        os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
                except Exception as e:
                    logger.warning(
                        f"S25: Failed to set file permissions (non-fatal): {e}"
                    )

                # Atomic rename
                temp_path.replace(self._store_path)

                # Never log secret values
                logger.info(f"S57: Saved {len(self._secrets)} secrets (encrypted)")
            except Exception as e:
                logger.error(f"S57: Failed to save encrypted store: {e}")
                raise

    def get_secret(
        self, provider_id: str, tenant_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Get secret for provider.

        Args:
            provider_id: Provider identifier (e.g., "openai", "anthropic", "generic")

        Returns:
            Secret value or None if not found
        """
        tenant = self._resolve_tenant_id(tenant_id)
        scoped_key = self._tenant_key(provider_id, tenant)
        with self._lock:
            value = self._secrets.get(scoped_key)
            if value:
                return value
            # S49: Optional compatibility fallback (explicit only) when tenant data
            # has not been migrated yet.
            if tenant != DEFAULT_TENANT_ID and self._allow_legacy_fallback():
                return self._secrets.get(provider_id)
            return None

    def set_secret(
        self, provider_id: str, secret: str, tenant_id: Optional[str] = None
    ) -> None:
        """
        Set secret for provider.

        Args:
            provider_id: Provider identifier
            secret: Secret value (API key, token, etc.)
        """
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("Secret must be non-empty string")

        tenant = self._resolve_tenant_id(tenant_id)
        scoped_key = self._tenant_key(provider_id, tenant)
        with self._lock:
            self._secrets[scoped_key] = secret.strip()
            self._save()

        # Never log secret value
        logger.info(
            "S25/S49: Set secret for provider '%s' (tenant=%s)", provider_id, tenant
        )

    def clear_secret(self, provider_id: str, tenant_id: Optional[str] = None) -> bool:
        """
        Clear secret for provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if secret was removed, False if not found
        """
        tenant = self._resolve_tenant_id(tenant_id)
        scoped_key = self._tenant_key(provider_id, tenant)
        with self._lock:
            if scoped_key in self._secrets:
                del self._secrets[scoped_key]
                self._save()
                logger.info(
                    "S25/S49: Cleared secret for provider '%s' (tenant=%s)",
                    provider_id,
                    tenant,
                )
                return True
            return False

    def clear_all(self) -> int:
        """
        Clear all secrets.

        Returns:
            Number of secrets cleared
        """
        with self._lock:
            count = len(self._secrets)
            self._secrets.clear()
            self._save()
            logger.info(f"S25: Cleared all secrets ({count} total)")
            return count

    def get_status(self, tenant_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get secret status (NO SECRET VALUES).

        Returns:
            Dict of {provider_id: {configured: bool, source: "server_store"}}
        """
        tenant = self._resolve_tenant_id(tenant_id)
        prefix = f"tenant::{tenant}::"
        with self._lock:
            status = {}
            for key in self._secrets.keys():
                provider_id = None
                if tenant == DEFAULT_TENANT_ID:
                    if key.startswith("tenant::"):
                        continue
                    provider_id = key
                elif key.startswith(prefix):
                    provider_id = key[len(prefix) :]
                if not provider_id:
                    continue
                status[provider_id] = {"configured": True, "source": "server_store"}
            return status


# Singleton instance
_store_instance: Optional[SecretStore] = None


def get_secret_store(state_dir: Optional[str] = None) -> SecretStore:
    """
    Get singleton secret store instance.

    Args:
        state_dir: Override state directory (for testing)

    Returns:
        SecretStore instance
    """
    global _store_instance
    if _store_instance is None or state_dir is not None:
        _store_instance = SecretStore(state_dir=state_dir)
    return _store_instance
