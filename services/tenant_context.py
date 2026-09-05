"""
S49 multi-tenant boundary context.

Central contract for tenant resolution, validation, and async context propagation.
Default mode remains single-tenant compatible.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .env_aliases import get_env_value

DEFAULT_TENANT_ID = "default"
TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TRUTHY = {"1", "true", "yes", "on"}

_CURRENT_TENANT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "openclaw_current_tenant", default=DEFAULT_TENANT_ID
)


class TenantBoundaryError(ValueError):
    """Raised when tenant context cannot be resolved or verified."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str = DEFAULT_TENANT_ID
    source: str = "default"
    multi_tenant: bool = False


def _is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def is_multi_tenant_enabled() -> bool:
    return _is_truthy(get_env_value("OPENCLAW_MULTI_TENANT_ENABLED", default="0"))


def allow_default_tenant_fallback() -> bool:
    return _is_truthy(
        get_env_value("OPENCLAW_MULTI_TENANT_ALLOW_DEFAULT_FALLBACK", default="0")
    )


def normalize_tenant_id(tenant_id: Any, *, field_name: str = "tenant_id") -> str:
    text = str(tenant_id or "").strip().lower()
    if not text:
        raise TenantBoundaryError("tenant_invalid", f"{field_name} must be non-empty")
    if not TENANT_ID_RE.fullmatch(text):
        raise TenantBoundaryError(
            "tenant_invalid",
            f"{field_name} must match {TENANT_ID_RE.pattern}",
        )
    return text


def get_tenant_header_names() -> tuple[str, ...]:
    configured = (
        get_env_value("OPENCLAW_TENANT_HEADER", default="X-OpenClaw-Tenant-Id")
        or "X-OpenClaw-Tenant-Id"
    ).strip()
    # Keep explicit legacy compatibility fallback.
    return tuple(
        dict.fromkeys(
            [configured, "X-OpenClaw-Tenant-Id", "X-Moltbot-Tenant-Id"]
        ).keys()
    )


def extract_tenant_from_headers(headers: Mapping[str, Any]) -> Optional[str]:
    for key in get_tenant_header_names():
        raw = headers.get(key) if headers else None
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return normalize_tenant_id(value, field_name=key)
    return None


def resolve_tenant_context(
    *,
    request: Optional[Any] = None,
    token_info: Optional[Any] = None,
    allow_default_when_missing: bool = False,
) -> TenantContext:
    """
    Resolve tenant context from token + request headers.

    Resolution order (multi-tenant mode):
    1) token_info.tenant_id
    2) request tenant header
    mismatch => fail-closed.
    """
    multi_tenant = is_multi_tenant_enabled()
    if not multi_tenant:
        return TenantContext(
            tenant_id=DEFAULT_TENANT_ID,
            source="single_tenant_mode",
            multi_tenant=False,
        )

    token_tenant = None
    if token_info is not None and getattr(token_info, "tenant_id", None):
        token_tenant = normalize_tenant_id(
            getattr(token_info, "tenant_id"), field_name="token_tenant_id"
        )

    header_tenant = None
    if request is not None and getattr(request, "headers", None) is not None:
        header_tenant = extract_tenant_from_headers(request.headers)

    if token_tenant and header_tenant and token_tenant != header_tenant:
        raise TenantBoundaryError(
            "tenant_mismatch",
            "Tenant mismatch between token context and request header.",
        )

    if token_tenant:
        return TenantContext(
            tenant_id=token_tenant,
            source="token",
            multi_tenant=True,
        )
    if header_tenant:
        return TenantContext(
            tenant_id=header_tenant,
            source="header",
            multi_tenant=True,
        )

    if allow_default_when_missing or allow_default_tenant_fallback():
        return TenantContext(
            tenant_id=DEFAULT_TENANT_ID,
            source="default_fallback",
            multi_tenant=True,
        )

    raise TenantBoundaryError(
        "tenant_required",
        "Tenant context required in multi-tenant mode.",
    )


def get_current_tenant_id() -> str:
    if not is_multi_tenant_enabled():
        return DEFAULT_TENANT_ID
    tenant_id = _CURRENT_TENANT.get()
    if not tenant_id:
        return DEFAULT_TENANT_ID
    return tenant_id


@contextlib.contextmanager
def tenant_scope(tenant_id: str):
    """Set tenant context in a contextvar scope (async-safe)."""
    normalized = normalize_tenant_id(tenant_id)
    token = _CURRENT_TENANT.set(normalized)
    try:
        yield normalized
    finally:
        _CURRENT_TENANT.reset(token)


@contextlib.contextmanager
def request_tenant_scope(
    *,
    request: Optional[Any] = None,
    token_info: Optional[Any] = None,
    allow_default_when_missing: bool = False,
):
    """
    Resolve + bind tenant context for current request processing scope.
    """
    ctx = resolve_tenant_context(
        request=request,
        token_info=token_info,
        allow_default_when_missing=allow_default_when_missing,
    )
    token = _CURRENT_TENANT.set(ctx.tenant_id)
    try:
        yield ctx
    finally:
        _CURRENT_TENANT.reset(token)
