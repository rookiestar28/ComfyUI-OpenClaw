"""
F10 — Sidecar Bridge Authentication.
Device-token authentication for bridge endpoints.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Dict, Optional, Set, Tuple

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

from ..env_aliases import EnvLookupMode, get_env_value
from .bridge_contract import BridgeScope

try:
    from ..redaction import stable_redaction_tag
except ImportError:  # pragma: no cover
    from services.redaction import stable_redaction_tag  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.sidecar.auth")

# Environment configuration
ENV_BRIDGE_ENABLED = "OPENCLAW_BRIDGE_ENABLED"
LEGACY_ENV_BRIDGE_ENABLED = "MOLTBOT_BRIDGE_ENABLED"
ENV_BRIDGE_DEVICE_TOKEN = "OPENCLAW_BRIDGE_DEVICE_TOKEN"
LEGACY_ENV_BRIDGE_DEVICE_TOKEN = "MOLTBOT_BRIDGE_DEVICE_TOKEN"
ENV_BRIDGE_ALLOWED_DEVICE_IDS = "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS"
LEGACY_ENV_BRIDGE_ALLOWED_DEVICE_IDS = "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS"
# R104: mTLS Contract
ENV_BRIDGE_MTLS_ENABLED = "OPENCLAW_BRIDGE_MTLS_ENABLED"
ENV_BRIDGE_DEVICE_CERT_MAP = (
    "OPENCLAW_BRIDGE_DEVICE_CERT_MAP"  # device_id:fingerprint,...
)

# Headers
HEADER_DEVICE_ID = "X-OpenClaw-Device-Id"
LEGACY_HEADER_DEVICE_ID = "X-Moltbot-Device-Id"
HEADER_DEVICE_TOKEN = "X-OpenClaw-Device-Token"
LEGACY_HEADER_DEVICE_TOKEN = "X-Moltbot-Device-Token"
HEADER_SCOPES = "X-OpenClaw-Scopes"
LEGACY_HEADER_SCOPES = "X-Moltbot-Scopes"
# R104: mTLS Headers
HEADER_CLIENT_CERT_HASH = "X-Client-Cert-Hash"  # SHA256 fingerprint from proxy


def _device_tag(device_id: Optional[str]) -> str:
    return stable_redaction_tag(device_id, label="device")


def _cert_tag(cert_hash: Optional[str]) -> str:
    return stable_redaction_tag(cert_hash, label="cert")


def _env_get(primary: str, legacy: str, default: str = "") -> str:
    """Get env var value (prefers new names, falls back to legacy). Respects empty-string overrides."""
    return (
        get_env_value(
            primary,
            aliases=(legacy,),
            mode=EnvLookupMode.PRESENCE,
            default=default,
        )
        or ""
    )


def is_bridge_enabled() -> bool:
    """Check if bridge endpoints are enabled."""
    return _env_get(ENV_BRIDGE_ENABLED, LEGACY_ENV_BRIDGE_ENABLED, "").lower() in (
        "1",
        "true",
        "yes",
    )


def get_bridge_device_token() -> str:
    """Get the configured bridge device token."""
    return _env_get(ENV_BRIDGE_DEVICE_TOKEN, LEGACY_ENV_BRIDGE_DEVICE_TOKEN, "")


def get_allowed_device_ids() -> Optional[Set[str]]:
    """
    Get allowlisted device IDs.
    Returns None if no allowlist configured (all IDs allowed).
    """
    ids_str = _env_get(
        ENV_BRIDGE_ALLOWED_DEVICE_IDS, LEGACY_ENV_BRIDGE_ALLOWED_DEVICE_IDS, ""
    )
    if not ids_str:
        return None
    return set(id.strip() for id in ids_str.split(",") if id.strip())


def is_mtls_enabled() -> bool:
    """Check if mTLS enforcement is enabled."""
    return os.environ.get(ENV_BRIDGE_MTLS_ENABLED, "false").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def get_device_cert_map() -> Dict[str, str]:
    """
    Get map of device_id -> certificate fingerprint.
    Format: device_id:fingerprint,device_id2:fingerprint2
    """
    mapping_str = os.environ.get(ENV_BRIDGE_DEVICE_CERT_MAP, "")
    if not mapping_str:
        return {}

    result = {}
    for entry in mapping_str.split(","):
        if ":" in entry:
            parts = entry.split(":", 1)
            result[parts[0].strip()] = parts[1].strip()
    return result


def validate_mtls_binding(request: web.Request, device_id: str) -> Tuple[bool, str]:
    """
    R104: Validate mTLS certificate binding for the device.
    """
    if not is_mtls_enabled():
        return True, ""

    cert_hash = request.headers.get(HEADER_CLIENT_CERT_HASH, "")
    if not cert_hash:
        # Strict mode: mTLS enabled but no cert header -> fail
        return False, "Missing client certificate header (mTLS required)"

    cert_map = get_device_cert_map()
    expected_hash = cert_map.get(device_id)

    if not expected_hash:
        # Strict mode: mTLS enabled implies explicit device binding
        return False, "Device not bound to a certificate"

    # Constant-time comparison not strictly required for public fingerprints but good practice
    if not hmac.compare_digest(cert_hash, expected_hash):
        # IMPORTANT: keep bound-device diagnostics redacted; raw fingerprints are sensitive.
        logger.warning(
            "mTLS violation for %s presented=%s expected=%s",
            _device_tag(device_id),
            _cert_tag(cert_hash),
            _cert_tag(expected_hash),
        )
        return False, "Certificate fingerprint mismatch"

    return True, ""


def validate_device_token(
    request: web.Request, required_scope: Optional[BridgeScope] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Validate device authentication for bridge endpoints.

    Args:
        request: aiohttp request
        required_scope: Optional required scope (currently ignored; all scopes granted)

    Returns:
        Tuple of (is_valid, error_message, device_id)
    """
    # Check if bridge is enabled
    if not is_bridge_enabled():
        return False, "Bridge not enabled", None

    # Extract headers
    device_id = request.headers.get(HEADER_DEVICE_ID)
    if not device_id and request.headers.get(LEGACY_HEADER_DEVICE_ID):
        device_id = request.headers.get(LEGACY_HEADER_DEVICE_ID)
        try:
            from ..metrics import metrics

            if metrics:
                metrics.inc("legacy_api_hits")
        except ImportError:
            pass
        logger.warning(
            f"DEPRECATION WARNING: Legacy header {LEGACY_HEADER_DEVICE_ID} used. Please migrate to {HEADER_DEVICE_ID}."
        )

    device_token = request.headers.get(HEADER_DEVICE_TOKEN)
    if not device_token and request.headers.get(LEGACY_HEADER_DEVICE_TOKEN):
        device_token = request.headers.get(LEGACY_HEADER_DEVICE_TOKEN)
        try:
            from ..metrics import metrics

            if metrics:
                metrics.inc("legacy_api_hits")
        except ImportError:
            pass
        logger.warning(
            f"DEPRECATION WARNING: Legacy header {LEGACY_HEADER_DEVICE_TOKEN} used. Please migrate to {HEADER_DEVICE_TOKEN}."
        )

    if not device_id:
        return False, "Missing device ID", None

    if not device_token:
        return False, "Missing device token", None

    # S58: lifecycle-aware validation path.
    # CRITICAL: keep this check before legacy static-token fallback so revoked/
    # expired lifecycle tokens are deterministically denied on every bridge route.
    try:
        try:
            from ..bridge_token_lifecycle import get_token_store
        except ImportError:
            from services.bridge_token_lifecycle import get_token_store  # type: ignore

        required_scope_value = (
            required_scope.value
            if isinstance(required_scope, BridgeScope)
            else required_scope
        )
        lifecycle_result = get_token_store().validate_token(
            device_token, required_scope=required_scope_value
        )
    except Exception:
        logger.warning("S58 lifecycle validation unavailable, falling back.")
        lifecycle_result = None

    if lifecycle_result and lifecycle_result.ok and lifecycle_result.token:
        token_device_id = lifecycle_result.token.device_id
        if device_id and device_id != token_device_id:
            return False, "Device ID mismatch", None
        device_id = token_device_id
    elif lifecycle_result and lifecycle_result.reject_reason != "unknown_token":
        reason_map = {
            "token_revoked": "Token revoked",
            "token_expired": "Token expired",
            "overlap_window_expired": "Token overlap window expired",
            "insufficient_scope": "Missing required scope",
            "token_not_found": "Token not found",
        }
        return (
            False,
            reason_map.get(lifecycle_result.reject_reason, "Invalid device token"),
            None,
        )
    else:
        # Legacy static-token fallback for backward compatibility.
        expected_token = get_bridge_device_token()
        if not expected_token:
            logger.error(
                "Bridge enabled but OPENCLAW_BRIDGE_DEVICE_TOKEN (or legacy MOLTBOT_BRIDGE_DEVICE_TOKEN) not set"
            )
            return False, "Bridge misconfigured", None

        if not device_id:
            return False, "Missing device ID", None

        # Constant-time token comparison
        if not hmac.compare_digest(device_token, expected_token):
            logger.warning("Invalid device token from %s", _device_tag(device_id))
            return False, "Invalid device token", None

        # Scope validation (legacy header contract)
        if required_scope:
            scopes_header = request.headers.get(
                HEADER_SCOPES, ""
            ) or request.headers.get(LEGACY_HEADER_SCOPES, "")
            if not scopes_header:
                logger.warning(
                    "Bridge device %s missing required scopes header.",
                    _device_tag(device_id),
                )
                return False, "Missing X-OpenClaw-Scopes header", None

            granted_scopes = set(
                s.strip() for s in scopes_header.split(",") if s.strip()
            )
            if required_scope not in granted_scopes:
                logger.warning(
                    "Bridge device %s missing scope %s (granted_count=%d).",
                    _device_tag(device_id),
                    required_scope,
                    len(granted_scopes),
                )
                return False, f"Missing required scope: {required_scope}", None

    # Check allowlist if configured
    allowed_ids = get_allowed_device_ids()
    if allowed_ids is not None and device_id not in allowed_ids:
        logger.warning("Device ID not in allowlist: %s", _device_tag(device_id))
        return False, "Device not authorized", None

    # R104: mTLS Binding Check
    is_mtls_valid, mtls_error = validate_mtls_binding(request, device_id)
    if not is_mtls_valid:
        logger.warning(
            "mTLS validation failed for %s: %s", _device_tag(device_id), mtls_error
        )
        return False, mtls_error, None

    return True, "", device_id


def require_bridge_auth(
    request: web.Request, required_scope: Optional[BridgeScope] = None
) -> Tuple[bool, Optional[web.Response], Optional[str]]:
    """
    Middleware-style auth check for bridge endpoints.

    Args:
        request: aiohttp request
        required_scope: Optional required scope

    Returns:
        Tuple of (is_valid, error_response_or_none, device_id)
    """
    is_valid, error_msg, device_id = validate_device_token(request, required_scope)

    if not is_valid:
        status = (
            403 if "not enabled" in error_msg or "misconfigured" in error_msg else 401
        )
        return False, web.json_response({"error": error_msg}, status=status), None

    return True, None, device_id
