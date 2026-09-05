"""
Access Control Service (S14).
Provides secure-by-default access policies for observability endpoints.
"""

import datetime
import hmac
import ipaddress
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

try:
    from aiohttp import web
except ImportError:
    web = None

from .env_aliases import get_env_value
from .legacy_compat import (
    ADMIN_TOKEN_HEADERS,
    OBS_TOKEN_HEADERS,
    get_header_alias_value,
)
from .request_ip import get_client_ip
from .tenant_context import (
    DEFAULT_TENANT_ID,
    extract_tenant_from_headers,
    is_multi_tenant_enabled,
    normalize_tenant_id,
)

# S46: Scoped RBAC & Tiered Access
try:
    from .endpoint_manifest import AuthTier, get_metadata
except ImportError:
    # Fallback/Circular import handling
    class AuthTier:
        ADMIN = "admin"
        OBSERVABILITY = "obs"
        INTERNAL = "internal"
        PUBLIC = "public"
        WEBHOOK = "webhook"

    def get_metadata(handler):
        return None


logger = logging.getLogger("ComfyUI-OpenClaw.services.access_control")


def is_loopback(remote_addr: str) -> bool:
    """
    Check if the remote address is a loopback address.
    Supports IPv4 (127.0.0.0/8) and IPv6 (::1).
    """
    if not remote_addr:
        return False

    # Simple string checks for common cases
    if remote_addr == "127.0.0.1" or remote_addr == "::1" or remote_addr == "localhost":
        return True

    try:
        ip = ipaddress.ip_address(remote_addr)
        return ip.is_loopback
    except ValueError:
        # Invalid IP
        return False


def is_auth_configured() -> bool:
    """
    Check if Admin Token authentication is configured (S41).
    Returns True if OPENCLAW_ADMIN_TOKEN/MOLTBOT_ADMIN_TOKEN is non-empty.
    """
    # CRITICAL: retain NONEMPTY alias semantics here; canonical empty values must still
    # fall through to the legacy token or authentication behavior can silently weaken.
    val = get_env_value("OPENCLAW_ADMIN_TOKEN", default="") or ""
    return bool(val.strip())


def is_any_token_configured() -> bool:
    """
    Check if ANY authentication token is configured (Admin OR Observability).
    Used for S45 Startup Gate to assess if the instance has minimal protection.
    """
    if is_auth_configured():
        return True

    obs_val = get_env_value("OPENCLAW_OBSERVABILITY_TOKEN", default="") or ""
    return bool(obs_val.strip())


# --- S46 Token Infrastructure ---


@dataclass
class TokenInfo:
    token_id: str
    role: "AuthTier"
    scopes: Set[str] = field(default_factory=set)
    created_at: float = 0.0
    expires_at: Optional[float] = None
    tenant_id: str = DEFAULT_TENANT_ID

    def has_scope(self, required: str) -> bool:
        """Check if token has scope, supporting wildcards."""
        if "*" in self.scopes:
            return True
        if required in self.scopes:
            return True
        # Check prefixes (e.g. "read:*" matches "read:logs")
        for s in self.scopes:
            if s.endswith(":*"):
                prefix = s[:-2]
                if required.startswith(prefix + ":"):
                    return True
        return False


class TokenRegistry:
    """
    S46: Token Lifecycle Management.
    Currently In-Memory. Future: Database.
    """

    _tokens: Dict[str, TokenInfo] = {}  # secret -> TokenInfo

    @classmethod
    def issue(
        cls,
        role: "AuthTier",
        scopes: List[str],
        ttl_seconds: int = 0,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> Tuple[str, TokenInfo]:
        """Issue a new token."""
        secret = f"oc_{role.value}_{uuid.uuid4().hex}"
        now = datetime.datetime.now().timestamp()
        expires = (now + ttl_seconds) if ttl_seconds > 0 else None
        normalized_tenant = normalize_tenant_id(tenant_id)

        info = TokenInfo(
            token_id=f"kid-{uuid.uuid4().hex[:8]}",
            role=role,
            scopes=set(scopes),
            created_at=now,
            expires_at=expires,
            tenant_id=normalized_tenant,
        )
        cls._tokens[secret] = info
        return secret, info

    @classmethod
    def revoke(cls, token_id: str) -> bool:
        """Revoke a token by ID."""
        to_delete = [s for s, i in cls._tokens.items() if i.token_id == token_id]
        for s in to_delete:
            del cls._tokens[s]
        return len(to_delete) > 0

    @classmethod
    def lookup(cls, secret: str) -> Optional[TokenInfo]:
        return cls._tokens.get(secret)


def _resolve_header_tenant(request) -> str:
    if not is_multi_tenant_enabled():
        return DEFAULT_TENANT_ID
    headers = getattr(request, "headers", None)
    if not isinstance(headers, Mapping):
        return DEFAULT_TENANT_ID
    try:
        tenant = extract_tenant_from_headers(headers)
    except Exception:
        return DEFAULT_TENANT_ID
    # IMPORTANT: do not tighten this to `tenant and DEFAULT_TENANT_ID`.
    # Env-token auth must preserve explicit tenant header in multi-tenant mode.
    return tenant or DEFAULT_TENANT_ID


def resolve_token_info(request) -> Optional[TokenInfo]:
    """
    Resolve the request's authentication token into a TokenInfo object.
    1. Check TokenRegistry (Dynamic)
    2. Check Environment Variables (Static)
    """
    headers = getattr(request, "headers", None)
    if not isinstance(headers, Mapping):
        headers = {}

    # Extract token from headers
    client_token = ""
    client_token, _used_legacy_admin = get_header_alias_value(
        headers, ADMIN_TOKEN_HEADERS, logger=logger
    )
    if not client_token:
        client_token, _used_legacy_obs = get_header_alias_value(
            headers, OBS_TOKEN_HEADERS, logger=logger
        )

    request_tenant = _resolve_header_tenant(request)

    # 1. Registry Check
    if client_token:
        info = TokenRegistry.lookup(client_token)
        if info:
            return info

    # 2. Static Env Check (Legacy/Bootstrap)
    # Admin
    admin_token = (get_env_value("OPENCLAW_ADMIN_TOKEN", default="") or "").strip()

    if admin_token and client_token:
        if hmac.compare_digest(client_token, admin_token):
            # IMPORTANT: keep request_tenant propagation here.
            # Multi-tenant env-token requests must retain header-derived tenant context.
            return TokenInfo(
                token_id="env-admin",
                role=AuthTier.ADMIN,
                scopes={"*"},
                tenant_id=request_tenant,
            )

    # Observability
    obs_token = (
        get_env_value("OPENCLAW_OBSERVABILITY_TOKEN", default="") or ""
    ).strip()

    if obs_token and client_token:
        # Note: If admin header was sent but matched Obs token, we accept it as Obs role?
        # Ideally strict separation, but for now match value.
        if hmac.compare_digest(client_token, obs_token):
            return TokenInfo(
                token_id="env-obs",
                role=AuthTier.OBSERVABILITY,
                scopes={"read:*"},  # S46: Wildcard for Obs
                tenant_id=request_tenant,
            )

    # 3. Loopback
    remote = get_client_ip(request)
    if is_loopback(remote):
        is_admin_configured = bool(admin_token)
        if not is_admin_configured:
            return TokenInfo(
                token_id="local-admin",
                role=AuthTier.ADMIN,
                scopes={"*"},
                tenant_id=request_tenant,
            )
        else:
            return TokenInfo(
                token_id="local-internal",
                role=AuthTier.INTERNAL,
                scopes={"internal:call"},
                tenant_id=request_tenant,
            )

    return None


def get_current_auth_tier(request) -> AuthTier:
    """
    Determine the authentication tier of the current request.
    Hierarchy: ADMIN > OBSERVABILITY > INTERNAL > PUBLIC.
    """
    token_info = resolve_token_info(request)
    if token_info:
        return token_info.role

    # No token? Check if public or internal loopback without token (if allowed?)
    # Wait, resolve_token_info handles Loopback!
    # If resolve_token_info returns None, it is strictly PUBLIC (Remote, No Token).

    return AuthTier.PUBLIC


def verify_tier_access(request, required_tier: AuthTier) -> Tuple[bool, Optional[str]]:
    """
    Check if the request meets the required AuthTier.
    Enforces hierarchy: ADMIN > OBSERVABILITY > INTERNAL > PUBLIC.
    """
    current_tier = get_current_auth_tier(request)

    if required_tier == AuthTier.PUBLIC:
        return True, None

    # S46 Strict: Internal means "Local Network Only".
    # Even Admin cannot access Internal endpoints from remote.
    if required_tier == AuthTier.INTERNAL:
        # We need to re-verify source IP because get_current_auth_tier abstracts it away into Roles.
        # But wait, TokenInfo for Loopback has role=INTERNAL or ADMIN.
        # TokenInfo for Remote Admin has role=ADMIN.
        # If I am Remote Admin, my role is ADMIN.
        # If I access INTERNAL endpoint, logic:
        # if current == INTERNAL (Localhost): OK.
        # if current == ADMIN (Remote): Fail?

        # But wait, if Localhost is acting as Admin (Convenience Mode), role is ADMIN.
        # So checking `current_tier == INTERNAL` might fail for Localhost Admin!

        # We need to allow if underlying connection is Loopback.
        remote = get_client_ip(request)
        if is_loopback(remote):
            return True, None

        # R102 Hook
        try:
            from .security_telemetry import get_security_telemetry

            get_security_telemetry().record_auth_failure(remote)
        except ImportError:
            pass
        return False, "Internal (Loopback) access required."

    # Admin is allowed everything else
    if current_tier == AuthTier.ADMIN:
        return True, None

    if required_tier == AuthTier.ADMIN:
        # Admin required. Current is not Admin (checked above).
        # R102 Hook
        try:
            from .security_telemetry import get_security_telemetry

            remote = get_client_ip(request)
            get_security_telemetry().record_auth_failure(remote)
        except ImportError:
            pass
        return False, "Admin access required."

    if required_tier == AuthTier.OBSERVABILITY:
        if current_tier in (
            AuthTier.OBSERVABILITY,
            AuthTier.ADMIN,
        ):  # Admin covered, but explicit is fine
            return True, None
        # Internal Loopback?
        # get_current_auth_tier converts Loopback -> INTERNAL (or ADMIN).
        # If Loopback is INTERNAL, does it satisfy OBS?
        # Yes, Loopback should satisfy Obs.
        if current_tier == AuthTier.INTERNAL:
            return True, None

        # R102 Hook
        try:
            from .security_telemetry import get_security_telemetry

            remote = get_client_ip(request)
            get_security_telemetry().record_auth_failure(remote)
        except ImportError:
            pass
        return False, "Observability access required."

    # R102 Hook for generic failure
    try:
        from .security_telemetry import get_security_telemetry

        remote = get_client_ip(request)
        get_security_telemetry().record_auth_failure(remote)
    except ImportError:
        pass
    return False, f"Access denied. Required: {required_tier}, Current: {current_tier}"


def verify_scope_access(
    request, required_scopes: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    Verify that the request has ALL required scopes.
    """
    if not required_scopes:
        return True, None

    token_info = resolve_token_info(request)
    if not token_info:
        return False, "Authentication required for scoped access."

    missing = []
    for req in required_scopes:
        if not token_info.has_scope(req):
            missing.append(req)

    if missing:
        return False, f"Missing required scopes: {', '.join(missing)}"

    return True, None


def enforce_security(handler):
    """
    S46 Decorator: Per-handler scope enforcement.
    Wraps an aiohttp handler to enforce AuthTier and Scope requirements defined in metadata.
    """
    import functools

    @functools.wraps(handler)
    async def wrapper(request, *args, **kwargs):
        meta = get_metadata(handler)
        if not meta:
            # S99: Drift Detection - Unclassified endpoint!
            return web.Response(status=403, text="Access Denied: Unclassified Endpoint")

        # 1. Tier Check
        passed, err = verify_tier_access(request, meta.auth_tier)
        if not passed:
            return web.Response(status=403, text=f"Access Denied: {err}")

        # 2. Scope Check (S46)
        if meta.required_scopes:
            passed, err = verify_scope_access(request, meta.required_scopes)
            if not passed:
                return web.Response(status=403, text=f"Forbidden: {err}")

        return await handler(request, *args, **kwargs)

    return wrapper


# --- Legacy Support (Keep until refactor complete) ---
# CRITICAL: keep legacy wrappers behavior/message-compatible with S13/S14/S27 contracts.
# Do not replace these wrappers with direct tier/scope checks; loopback CSRF semantics would regress.


def require_observability_access(request) -> Tuple[bool, Optional[str]]:
    """
    Enforce S14 access control policy for observability endpoints.

    Keep legacy behavior/messages stable for existing handlers/tests:
    1. Loopback -> allow
    2. Valid observability token -> allow
    3. Otherwise -> deny
    """
    remote = get_client_ip(request)
    if is_loopback(remote):
        return True, None

    expected_token = (
        get_env_value("OPENCLAW_OBSERVABILITY_TOKEN", default="") or ""
    ).strip()
    if expected_token:
        client_token, _used_legacy = get_header_alias_value(
            request.headers, OBS_TOKEN_HEADERS, logger=logger
        )
        if hmac.compare_digest(client_token, expected_token):
            return True, None
        return False, "Invalid or missing observability token."

    return (
        False,
        "Remote access denied. Set OPENCLAW_OBSERVABILITY_TOKEN (or legacy MOLTBOT_OBSERVABILITY_TOKEN) to allow.",
    )


def require_admin_token(request) -> Tuple[bool, Optional[str]]:
    """
    Enforce token-based access for administrative/write actions.

    Keep S13/S27 legacy behavior stable:
    - If admin token configured -> require matching token header.
    - If no admin token configured -> allow loopback with same-origin CSRF check.
    - Deny remote by default.
    """
    remote = get_client_ip(request)
    expected_token = (get_env_value("OPENCLAW_ADMIN_TOKEN", default="") or "").strip()
    if expected_token:
        client_token, _used_legacy = get_header_alias_value(
            request.headers, ADMIN_TOKEN_HEADERS, logger=logger
        )
        if hmac.compare_digest(client_token, expected_token):
            return True, None
        return False, "Invalid admin token."

    # No token configured: loopback convenience with S27 CSRF protection.
    if is_loopback(remote):
        try:
            from .csrf_protection import is_same_origin_request
        except ImportError:
            try:
                from services.csrf_protection import is_same_origin_request
            except ImportError:
                logger.warning(
                    "S27: CSRF protection module missing, allowing loopback (unsafe)"
                )
                return True, None

        if not is_same_origin_request(request):
            return (
                False,
                "Cross-origin request denied (S33). Set OPENCLAW_ADMIN_TOKEN to use token-based auth.",
            )
        return True, None

    return (
        False,
        "Remote admin access denied. Set OPENCLAW_ADMIN_TOKEN (or legacy MOLTBOT_ADMIN_TOKEN) to allow.",
    )
