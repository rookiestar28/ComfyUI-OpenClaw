"""
Webhook Authentication Module.
S2: ChatOps/webhook auth + least privilege.

Supports:
- Bearer token auth (Authorization: Bearer <token>)
- HMAC signature auth (X-OpenClaw-Signature: sha256=<hex>) (legacy: X-Moltbot-Signature)
"""

import hashlib
import hmac
import logging
from typing import Mapping, Optional, Protocol, Tuple

from .env_aliases import EnvLookupMode, get_env_value
from .legacy_compat import (
    WEBHOOK_NONCE_HEADERS,
    WEBHOOK_SIGNATURE_HEADERS,
    WEBHOOK_TIMESTAMP_HEADERS,
    get_header_alias_value,
)

logger = logging.getLogger("ComfyUI-OpenClaw.services.webhook_auth")


# Shared auth exception type used by API handlers.
class AuthError(Exception):
    pass


# Environment variable names
ENV_AUTH_MODE = "OPENCLAW_WEBHOOK_AUTH_MODE"
LEGACY_ENV_AUTH_MODE = "MOLTBOT_WEBHOOK_AUTH_MODE"
ENV_BEARER_TOKEN = "OPENCLAW_WEBHOOK_BEARER_TOKEN"
LEGACY_ENV_BEARER_TOKEN = "MOLTBOT_WEBHOOK_BEARER_TOKEN"
ENV_HMAC_SECRET = "OPENCLAW_WEBHOOK_HMAC_SECRET"
LEGACY_ENV_HMAC_SECRET = "MOLTBOT_WEBHOOK_HMAC_SECRET"
ENV_REQUIRE_REPLAY_PROTECTION = "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION"
LEGACY_ENV_REQUIRE_REPLAY_PROTECTION = "MOLTBOT_WEBHOOK_REQUIRE_REPLAY_PROTECTION"

# Auth modes
AUTH_MODE_BEARER = "bearer"
AUTH_MODE_HMAC = "hmac"
AUTH_MODE_BEARER_OR_HMAC = "bearer_or_hmac"


class RequestLike(Protocol):
    """Minimal request interface used by auth checks (keeps unit tests independent of aiohttp)."""

    headers: Mapping[str, str]


def _env_get(primary: str, legacy: str, default: Optional[str] = None) -> Optional[str]:
    """Get env var value (prefers new names, falls back to legacy). Respects empty-string overrides."""
    return get_env_value(
        primary,
        aliases=(legacy,),
        mode=EnvLookupMode.PRESENCE,
        default=default,
    )


def get_auth_mode() -> str:
    """Get configured auth mode."""
    return (
        _env_get(ENV_AUTH_MODE, LEGACY_ENV_AUTH_MODE, AUTH_MODE_BEARER)
        or AUTH_MODE_BEARER
    ).lower()


def get_bearer_token() -> Optional[str]:
    """Get configured bearer token (secret)."""
    return _env_get(ENV_BEARER_TOKEN, LEGACY_ENV_BEARER_TOKEN)


def get_hmac_secret() -> Optional[bytes]:
    """Get configured HMAC secret (secret)."""
    secret = _env_get(ENV_HMAC_SECRET, LEGACY_ENV_HMAC_SECRET)
    return secret.encode("utf-8") if secret else None


def is_auth_configured() -> bool:
    """Check if any auth is configured."""
    mode = get_auth_mode()

    if mode == AUTH_MODE_BEARER:
        return get_bearer_token() is not None
    elif mode == AUTH_MODE_HMAC:
        return get_hmac_secret() is not None
    elif mode == AUTH_MODE_BEARER_OR_HMAC:
        return get_bearer_token() is not None or get_hmac_secret() is not None

    return False


def should_require_replay_protection() -> bool:
    """Check if replay protection is strictly required (S36 fail-closed default)."""
    # S36: Default to strict (1). Use "0" or "false" to opt-out (legacy compat).
    val = (
        _env_get(
            ENV_REQUIRE_REPLAY_PROTECTION, LEGACY_ENV_REQUIRE_REPLAY_PROTECTION, "1"
        )
        or "1"
    ).lower()
    return val in ("1", "true", "yes")


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_bearer(request: RequestLike) -> Tuple[bool, str]:
    """
    Verify Bearer token authentication.

    Returns: (is_valid, error_message)
    """
    expected = get_bearer_token()
    if not expected:
        return False, "bearer_not_configured"

    auth_header = request.headers.get("Authorization", "")

    # Must use Authorization header, not query params
    if not auth_header:
        return False, "missing_authorization_header"

    # Must be Bearer scheme
    if not auth_header.startswith("Bearer "):
        return False, "invalid_auth_scheme"

    token = auth_header[7:]  # Remove "Bearer " prefix

    if not token:
        return False, "empty_token"

    if not constant_time_compare(token, expected):
        return False, "invalid_token"

    return True, ""


def verify_hmac(request: RequestLike, raw_body: bytes) -> Tuple[bool, str]:
    """
    Verify HMAC signature authentication.

    Signature header: X-OpenClaw-Signature: sha256=<hex> (legacy: X-Moltbot-Signature)

    Returns: (is_valid, error_message)
    """
    secret = get_hmac_secret()
    if not secret:
        return False, "hmac_not_configured"

    sig_header, _used_legacy_sig = get_header_alias_value(
        request.headers, WEBHOOK_SIGNATURE_HEADERS, logger=logger
    )

    if not sig_header:
        return False, "missing_signature_header"

    # Parse signature (sha256=<hex>)
    if not sig_header.startswith("sha256="):
        return False, "invalid_signature_format"

    provided_sig = sig_header[7:]  # Remove "sha256=" prefix

    if not provided_sig:
        return False, "empty_signature"

    # Compute expected signature
    expected_sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided_sig.lower(), expected_sig.lower()):
        return False, "invalid_signature"

    # Replay Protection (S2.1)
    timestamp, _used_legacy_ts = get_header_alias_value(
        request.headers, WEBHOOK_TIMESTAMP_HEADERS, logger=logger
    )
    nonce, _used_legacy_nonce = get_header_alias_value(
        request.headers, WEBHOOK_NONCE_HEADERS, logger=logger
    )

    # Enforced if headers present OR if strictly required configuration
    should_enforce = timestamp or nonce or should_require_replay_protection()

    if should_enforce:
        if not timestamp:
            return False, "missing_timestamp"
        if not nonce:
            return False, "missing_nonce"

        try:
            ts = int(timestamp)
        except ValueError:
            return False, "invalid_timestamp"

        # Check drift (5 minutes)
        import time

        now = int(time.time())
        if abs(now - ts) > 300:
            # R102 Hook
            try:
                from .security_telemetry import get_security_telemetry

                get_security_telemetry().record_replay_rejection(
                    "timestamp_out_of_range"
                )
            except ImportError:
                pass
            return False, "timestamp_out_of_range"

        # Check nonce uniqueness
        IdempotencyStore = None
        try:
            from .idempotency_store import IdempotencyStore
        except ImportError:
            try:
                from services.idempotency_store import IdempotencyStore
            except ImportError:
                pass

        if IdempotencyStore:
            try:
                store = IdempotencyStore()
                # Nonce key
                nonce_key = f"nonce:{nonce}"
                # TTL should allow for the drift window (buffer)
                is_dup, _ = store.check_and_record(nonce_key, ttl=600)
                if is_dup:
                    return False, "nonce_used"
            except Exception as e:
                logger.error(f"Idempotency store check failed: {e}")
                if should_require_replay_protection():
                    return False, "internal_error"
                # Else proceed (allow open in legacy/relaxed mode - risk acceptance)
                pass
        else:
            logger.warning("IdempotencyStore not available for nonce check")
            # Fail closed if configured to require protection, otherwise warn
            if should_require_replay_protection():
                return False, "internal_error"

    return True, ""


def require_auth(request: RequestLike, raw_body: bytes) -> Tuple[bool, str]:
    """
    Require authentication based on configured mode.

    Returns: (is_valid, error_message)
    """
    if not is_auth_configured():
        logger.warning("Webhook auth not configured, denying request")
        return False, "auth_not_configured"

    mode = get_auth_mode()

    if mode == AUTH_MODE_BEARER:
        return verify_bearer(request)

    elif mode == AUTH_MODE_HMAC:
        return verify_hmac(request, raw_body)

    elif mode == AUTH_MODE_BEARER_OR_HMAC:
        # Try bearer first, then HMAC
        valid, error = verify_bearer(request)
        if valid:
            return True, ""

        valid, error = verify_hmac(request, raw_body)
        if valid:
            return True, ""

        return False, "invalid_credentials"

    return False, "unknown_auth_mode"


def get_auth_summary() -> dict:
    """Get auth configuration summary (no secrets)."""
    mode = get_auth_mode()
    return {
        "mode": mode,
        "bearer_configured": get_bearer_token() is not None,
        "hmac_configured": get_hmac_secret() is not None,
        "is_ready": is_auth_configured(),
    }
