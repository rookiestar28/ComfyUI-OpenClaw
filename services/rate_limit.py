"""
Rate Limiting Service (S17 / R143).

Provides shared request-scoped rate-limit evaluation with hierarchical budgets and
machine-readable diagnostics while preserving the legacy bool-only helper.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from .env_aliases import get_env_value
from .request_ip import get_client_ip

try:
    from .access_control import resolve_token_info
except ImportError:
    from services.access_control import resolve_token_info  # type: ignore

try:
    from .tenant_context import DEFAULT_TENANT_ID, extract_tenant_from_headers
except ImportError:
    from services.tenant_context import (  # type: ignore
        DEFAULT_TENANT_ID,
        extract_tenant_from_headers,
    )

DEFAULT_RETRY_AFTER_SECONDS = 60
_REQUEST_CACHE_ATTR = "_openclaw_rate_limit_decisions"
_IP_SCALED_MULTIPLIER = 5.0


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit_type: str
    bucket: str
    scope: str
    retry_after_sec: int
    reason_code: str
    endpoint_class: str
    ip: str
    token_id: str = "anonymous"
    tenant_id: str = DEFAULT_TENANT_ID

    def to_payload(
        self, *, error: str = "rate_limit_exceeded", include_ok: bool = True
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "error": error,
            "code": "rate_limit_exceeded",
            "bucket": self.bucket,
            "scope": self.scope,
            "retry_after_sec": self.retry_after_sec,
            "reason_code": self.reason_code,
            "endpoint_class": self.endpoint_class,
        }
        if include_ok:
            payload["ok"] = False
        return payload


@dataclass(frozen=True)
class BucketPolicy:
    capacity: int
    tokens_per_second: float


@dataclass(frozen=True)
class RateLimitPolicy:
    principal: BucketPolicy
    tenant: BucketPolicy
    ip: BucketPolicy
    endpoint_class: BucketPolicy
    daily_cap_env: Optional[str] = None


class TokenBucket:
    """
    Thread-safe Token Bucket implementation.
    """

    def __init__(self, capacity: int, tokens_per_second: float):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.rate = max(0.0, float(tokens_per_second))
        self.last_update = time.time()
        self.lock = threading.Lock()

    def _refill_unlocked(self, now: float) -> None:
        elapsed = max(0.0, now - self.last_update)
        self.last_update = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

    def consume(self, amount: int = 1) -> bool:
        """
        Attempt to consume tokens.
        Returns True if successful, False if not enough tokens.
        """
        allowed, _retry_after = self.consume_with_diagnostics(amount)
        return allowed

    def consume_with_diagnostics(self, amount: int = 1) -> Tuple[bool, int]:
        """
        Attempt to consume tokens and return retry-after diagnostics on denial.
        """
        with self.lock:
            now = time.time()
            self._refill_unlocked(now)

            if self.tokens >= amount:
                self.tokens -= amount
                return True, 0

            if self.rate <= 0:
                return False, DEFAULT_RETRY_AFTER_SECONDS

            needed = amount - self.tokens
            retry_after = int(max(1, (needed / self.rate) + 0.999999))
            return False, retry_after


class DailyCounter:
    """UTC-day counter for optional daily caps."""

    def __init__(self) -> None:
        self._counts: Dict[str, Tuple[str, int]] = {}
        self._lock = threading.Lock()

    def check_and_increment(self, key: str, cap: int) -> Tuple[bool, int]:
        if cap <= 0:
            return True, 0
        day_key = _utc_day_key()
        with self._lock:
            current_day, current_count = self._counts.get(key, (day_key, 0))
            if current_day != day_key:
                current_day, current_count = day_key, 0
            if current_count >= cap:
                return False, _seconds_until_next_utc_day()
            self._counts[key] = (current_day, current_count + 1)
            return True, 0


class RateLimiter:
    """
    Manages hierarchical rate limits for different endpoint classes.
    """

    def __init__(self):
        self.buckets: Dict[str, Dict[str, TokenBucket]] = {}
        self.lock = threading.Lock()
        self.daily_counters = DailyCounter()
        self.policies = self._build_default_policies()
        # IMPORTANT: preserve the legacy tuple map; older callers still inspect defaults directly.
        self.defaults = {
            limit_type: (
                policy.principal.capacity,
                policy.principal.tokens_per_second,
            )
            for limit_type, policy in self.policies.items()
        }

    def _build_default_policies(self) -> Dict[str, RateLimitPolicy]:
        # Base limits preserve the old default as the principal bucket. Tenant/IP and
        # endpoint-class budgets widen above that so authenticated callers on a shared
        # IP do not collide immediately on the legacy IP-only bucket.
        def policy(
            base_capacity: int,
            *,
            daily_env: Optional[str] = None,
        ) -> RateLimitPolicy:
            base_rate = base_capacity / 60.0
            return RateLimitPolicy(
                principal=BucketPolicy(base_capacity, base_rate),
                tenant=BucketPolicy(base_capacity * 3, base_rate * 3),
                ip=BucketPolicy(base_capacity, base_rate),
                endpoint_class=BucketPolicy(base_capacity * 10, base_rate * 10),
                daily_cap_env=daily_env,
            )

        return {
            "webhook": policy(30, daily_env="OPENCLAW_RATE_LIMIT_WEBHOOK_DAILY_CAP"),
            "logs": policy(60),
            "admin": policy(20, daily_env="OPENCLAW_RATE_LIMIT_ADMIN_DAILY_CAP"),
            "bridge": policy(20, daily_env="OPENCLAW_RATE_LIMIT_BRIDGE_DAILY_CAP"),
            "connector": policy(
                20, daily_env="OPENCLAW_RATE_LIMIT_CONNECTOR_DAILY_CAP"
            ),
            "trigger": policy(60, daily_env="OPENCLAW_RATE_LIMIT_TRIGGER_DAILY_CAP"),
            "events": policy(30, daily_env="OPENCLAW_RATE_LIMIT_EVENTS_DAILY_CAP"),
        }

    def check(
        self,
        limit_type: str,
        ip: str,
        *,
        token_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        decision = self.evaluate(
            limit_type,
            ip,
            token_id=token_id,
            tenant_id=tenant_id,
        )
        return decision.allowed

    def evaluate(
        self,
        limit_type: str,
        ip: str,
        *,
        token_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> RateLimitDecision:
        ip = ip or "unknown"
        token_id = (token_id or "").strip() or "anonymous"
        tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT_ID
        policy = self.policies.get(limit_type, self.policies["webhook"])

        endpoint_decision = self._check_bucket(
            limit_type,
            bucket="endpoint_class",
            scope_value=limit_type,
            policy=policy.endpoint_class,
        )
        if not endpoint_decision.allowed:
            return endpoint_decision

        daily_cap = self._get_daily_cap(limit_type, policy.daily_cap_env)
        if daily_cap:
            principal_bucket, principal_scope = self._principal_scope(
                token_id=token_id,
                tenant_id=tenant_id,
                ip=ip,
            )
            daily_allowed, retry_after = self.daily_counters.check_and_increment(
                f"{limit_type}:{principal_bucket}:{principal_scope}",
                daily_cap,
            )
            if not daily_allowed:
                return RateLimitDecision(
                    allowed=False,
                    limit_type=limit_type,
                    bucket="daily",
                    scope=f"{principal_bucket}:{principal_scope}",
                    retry_after_sec=retry_after,
                    reason_code="daily_cap_exceeded",
                    endpoint_class=limit_type,
                    ip=ip,
                    token_id=token_id,
                    tenant_id=tenant_id,
                )

        if token_id != "anonymous":
            token_decision = self._check_bucket(
                limit_type,
                bucket="token_id",
                scope_value=token_id,
                policy=policy.principal,
                ip=ip,
                token_id=token_id,
                tenant_id=tenant_id,
            )
            if not token_decision.allowed:
                return token_decision

        if tenant_id != DEFAULT_TENANT_ID:
            tenant_decision = self._check_bucket(
                limit_type,
                bucket="tenant",
                scope_value=tenant_id,
                policy=policy.tenant,
                ip=ip,
                token_id=token_id,
                tenant_id=tenant_id,
            )
            if not tenant_decision.allowed:
                return tenant_decision

        ip_policy = policy.ip
        if token_id != "anonymous" or tenant_id != DEFAULT_TENANT_ID:
            ip_policy = BucketPolicy(
                capacity=int(max(1, round(policy.ip.capacity * _IP_SCALED_MULTIPLIER))),
                tokens_per_second=policy.ip.tokens_per_second * _IP_SCALED_MULTIPLIER,
            )
        ip_decision = self._check_bucket(
            limit_type,
            bucket="ip",
            scope_value=ip,
            policy=ip_policy,
            ip=ip,
            token_id=token_id,
            tenant_id=tenant_id,
        )
        if not ip_decision.allowed:
            return ip_decision

        return RateLimitDecision(
            allowed=True,
            limit_type=limit_type,
            bucket="allow",
            scope=f"endpoint_class:{limit_type}",
            retry_after_sec=0,
            reason_code="allowed",
            endpoint_class=limit_type,
            ip=ip,
            token_id=token_id,
            tenant_id=tenant_id,
        )

    def _check_bucket(
        self,
        limit_type: str,
        *,
        bucket: str,
        scope_value: str,
        policy: BucketPolicy,
        ip: str = "unknown",
        token_id: str = "anonymous",
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> RateLimitDecision:
        bucket_obj = self._get_bucket(
            limit_type,
            bucket=bucket,
            scope_value=scope_value,
            capacity=policy.capacity,
            rate=policy.tokens_per_second,
        )
        allowed, retry_after = bucket_obj.consume_with_diagnostics(1)
        return RateLimitDecision(
            allowed=allowed,
            limit_type=limit_type,
            bucket=bucket,
            scope=f"{bucket}:{scope_value}",
            retry_after_sec=retry_after if not allowed else 0,
            reason_code="burst_limit_exceeded" if not allowed else "allowed",
            endpoint_class=limit_type,
            ip=ip,
            token_id=token_id,
            tenant_id=tenant_id,
        )

    def _get_bucket(
        self,
        limit_type: str,
        *,
        bucket: str,
        scope_value: str,
        capacity: int,
        rate: float,
    ) -> TokenBucket:
        bucket_type = f"{limit_type}:{bucket}"
        with self.lock:
            typed = self.buckets.setdefault(bucket_type, {})
            if scope_value not in typed:
                typed[scope_value] = TokenBucket(capacity, rate)
            return typed[scope_value]

    def _get_daily_cap(self, limit_type: str, env_name: Optional[str]) -> Optional[int]:
        if not env_name:
            return None
        legacy_env = env_name.replace("OPENCLAW_", "MOLTBOT_", 1)
        raw = (get_env_value(env_name, aliases=(legacy_env,), default="") or "").strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _principal_scope(
        self, *, token_id: str, tenant_id: str, ip: str
    ) -> Tuple[str, str]:
        if token_id != "anonymous":
            return "token_id", token_id
        if tenant_id != DEFAULT_TENANT_ID:
            return "tenant", tenant_id
        return "ip", ip


def _utc_day_key(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def _seconds_until_next_utc_day(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    next_day = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    delta = int((next_day - now).total_seconds())
    return max(1, delta)


def resolve_rate_limit_context(request) -> Tuple[str, str, str]:
    """
    Resolve stable request scope identifiers without leaking raw secrets.
    """
    ip = get_client_ip(request) or "unknown"
    token_id = "anonymous"
    tenant_id = DEFAULT_TENANT_ID

    try:
        token_info = resolve_token_info(request)
    except Exception:
        token_info = None

    if token_info is not None and getattr(token_info, "token_id", None):
        token_id = str(getattr(token_info, "token_id") or "anonymous")
        tenant_id = str(
            getattr(token_info, "tenant_id", DEFAULT_TENANT_ID) or DEFAULT_TENANT_ID
        )

    headers = getattr(request, "headers", None)
    if isinstance(headers, Mapping):
        try:
            header_tenant = extract_tenant_from_headers(headers)
        except Exception:
            header_tenant = None
        if header_tenant:
            tenant_id = header_tenant

    return ip, token_id, tenant_id


# Global instance
rate_limiter = RateLimiter()


def _get_request_cache(request) -> Dict[str, RateLimitDecision]:
    cache = getattr(request, _REQUEST_CACHE_ATTR, None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            setattr(request, _REQUEST_CACHE_ATTR, cache)
        except Exception:
            return {}
    return cache


def evaluate_rate_limit(request, limit_type: str) -> RateLimitDecision:
    cache = _get_request_cache(request)
    if limit_type in cache:
        return cache[limit_type]

    ip, token_id, tenant_id = resolve_rate_limit_context(request)
    decision = rate_limiter.evaluate(
        limit_type,
        ip,
        token_id=token_id,
        tenant_id=tenant_id,
    )
    if cache is not None:
        cache[limit_type] = decision
    return decision


def get_cached_rate_limit_decision(
    request, limit_type: str
) -> Optional[RateLimitDecision]:
    cache = getattr(request, _REQUEST_CACHE_ATTR, None)
    if isinstance(cache, dict):
        decision = cache.get(limit_type)
        if isinstance(decision, RateLimitDecision):
            return decision
    return None


def check_rate_limit(request, limit_type: str) -> bool:
    """
    Helper to check rate limit from standard request object.

    Returns True if allowed, False if exceeded.
    """
    decision = evaluate_rate_limit(request, limit_type)
    return decision.allowed


def build_rate_limit_payload(
    request,
    limit_type: str,
    *,
    error: str = "rate_limit_exceeded",
    include_ok: bool = True,
) -> Dict[str, Any]:
    decision = get_cached_rate_limit_decision(request, limit_type)
    if decision is None:
        decision = RateLimitDecision(
            allowed=False,
            limit_type=limit_type,
            bucket="unknown",
            scope=f"endpoint_class:{limit_type}",
            retry_after_sec=DEFAULT_RETRY_AFTER_SECONDS,
            reason_code="rate_limit_exceeded",
            endpoint_class=limit_type,
            ip="unknown",
        )
    return decision.to_payload(error=error, include_ok=include_ok)


def build_rate_limit_response(
    request,
    limit_type: str,
    *,
    web_module,
    error: str = "rate_limit_exceeded",
    include_ok: bool = True,
):
    payload = build_rate_limit_payload(
        request,
        limit_type,
        error=error,
        include_ok=include_ok,
    )
    retry_after = str(payload.get("retry_after_sec", DEFAULT_RETRY_AFTER_SECONDS))
    return web_module.json_response(
        payload,
        status=429,
        headers={"Retry-After": retry_after},
    )
