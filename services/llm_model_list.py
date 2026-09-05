"""
R150 model-list helper seam.

Centralizes cache, provider resolution, and outbound fetch helpers so
`api.config` can stay focused on HTTP/auth flow while preserving its legacy
test compatibility surface.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

if __package__ and "." in __package__:
    from .env_aliases import EnvLookupMode, get_env_value
    from .import_fallback import import_module_dual
else:
    from services.env_aliases import EnvLookupMode, get_env_value
    from services.import_fallback import import_module_dual  # type: ignore

_MODEL_LIST_CACHE: OrderedDict = OrderedDict()
_MODEL_LIST_TTL_SEC = 600
_MODEL_LIST_MAX_ENTRIES = 16


@dataclass(frozen=True)
class ModelListTarget:
    provider: str
    base_url: str
    tenant_id: str
    cache_key: tuple
    api_key: str | None
    requires_api_key: bool
    allow_private_network: bool = False


def _providers_catalog_module():
    return import_module_dual(
        __package__,
        ".providers.catalog",
        "services.providers.catalog",
    )


def _provider_keys_module():
    return import_module_dual(
        __package__,
        ".providers.keys",
        "services.providers.keys",
    )


def _safe_io_module():
    return import_module_dual(
        __package__,
        ".safe_io",
        "services.safe_io",
    )


def build_model_cache_key(provider: str, base_url: str, tenant_id: str) -> tuple:
    if str(tenant_id).strip().lower() in ("", "default"):
        return (provider, base_url)
    return (tenant_id, provider, base_url)


def cache_put(key: tuple, models: list) -> None:
    if key in _MODEL_LIST_CACHE:
        _MODEL_LIST_CACHE.move_to_end(key)
    _MODEL_LIST_CACHE[key] = (time.time(), models)
    while len(_MODEL_LIST_CACHE) > _MODEL_LIST_MAX_ENTRIES:
        _MODEL_LIST_CACHE.popitem(last=False)


def cache_get(key: tuple):
    entry = _MODEL_LIST_CACHE.get(key)
    if entry is None:
        return None
    ts, models = entry
    if (time.time() - ts) >= _MODEL_LIST_TTL_SEC:
        return None
    _MODEL_LIST_CACHE.move_to_end(key)
    return entry


def get_stale_cached_models(key: tuple):
    return _MODEL_LIST_CACHE.get(key)


def get_llm_allowed_hosts() -> set[str]:
    allowed_hosts_str = get_env_value("OPENCLAW_LLM_ALLOWED_HOSTS", default="") or ""
    env_hosts = {h.lower().strip() for h in allowed_hosts_str.split(",") if h.strip()}
    catalog = _providers_catalog_module()
    return set(catalog.get_default_public_llm_hosts()) | env_hosts


def format_llm_ssrf_error(exc: Exception) -> str:
    detail = str(exc)
    return (
        f"SSRF policy blocked outbound URL: {detail}. "
        "OPENCLAW_LLM_ALLOWED_HOSTS only allows additional exact public hosts; "
        "private/reserved IP targets still require "
        "scoped allow_private_network=true for the configured LLM target or "
        "OPENCLAW_ALLOW_INSECURE_BASE_URL=1. Wildcard '*' entries are not supported."
    )


def llm_insecure_override_enabled() -> bool:
    val = get_env_value(
        "OPENCLAW_ALLOW_INSECURE_BASE_URL",
        mode=EnvLookupMode.PRESENCE,
    )
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def extract_models_from_payload(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return sorted({model for model in out if model})

    models = payload.get("models")
    if isinstance(models, list):
        out = []
        for item in models:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return sorted({model for model in out if model})

    return []


def resolve_model_list_target(
    provider_override: str,
    effective: dict,
    tenant_id: str,
) -> ModelListTarget:
    catalog = _providers_catalog_module()
    keys = _provider_keys_module()

    provider = provider_override or str(effective.get("provider") or "openai").lower()
    runtime_base_url = str(effective.get("base_url") or "").strip()
    info = catalog.get_provider_info(provider)
    if not info:
        raise ValueError(f"Unknown provider: {provider}")

    if info.api_type != catalog.ProviderType.OPENAI_COMPAT:
        raise TypeError("Model list is only supported for OpenAI-compatible providers.")

    raw_base_url = runtime_base_url if runtime_base_url else info.base_url
    # IMPORTANT: model discovery is one of the primary Ollama debug hotspots.
    # Keep provider-aware base URL normalization here so legacy root URLs still
    # resolve to the OpenAI-compatible `/v1/models` endpoint instead of 404.
    base_url = catalog.normalize_provider_base_url(provider, raw_base_url)
    if not base_url:
        raise ValueError(f"No base URL configured for provider '{provider}'.")

    return ModelListTarget(
        provider=provider,
        base_url=base_url,
        tenant_id=tenant_id,
        cache_key=build_model_cache_key(provider, base_url, tenant_id),
        api_key=keys.get_api_key_for_provider(provider, tenant_id=tenant_id),
        requires_api_key=bool(keys.requires_api_key(provider)),
        allow_private_network=bool(effective.get("allow_private_network", False)),
    )


def validate_model_list_target(
    target: ModelListTarget,
    controls: dict,
    *,
    allow_insecure_base_url: bool,
) -> None:
    safe_io = _safe_io_module()
    safe_io.validate_outbound_url(
        target.base_url,
        allow_hosts=controls.get("allow_hosts"),
        allow_any_public_host=bool(controls.get("allow_any_public_host")),
        allow_loopback_hosts=controls.get("allow_loopback_hosts"),
        allow_insecure_base_url=allow_insecure_base_url,
        allow_private_network=bool(controls.get("allow_private_network")),
        policy=safe_io.STANDARD_OUTBOUND_POLICY,
    )


def fetch_remote_model_list(
    target: ModelListTarget,
    controls: dict,
    *,
    pack_version: str,
    allow_insecure_base_url: bool,
) -> list[str]:
    safe_io = _safe_io_module()
    request_headers = {
        "User-Agent": f"ComfyUI-OpenClaw/{pack_version}",
        "Accept": "application/json",
    }
    if target.api_key:
        request_headers["Authorization"] = f"Bearer {target.api_key}"

    payload = safe_io.safe_request_json(
        method="GET",
        url=f"{target.base_url.rstrip('/')}/models",
        json_body=None,
        headers=request_headers,
        timeout_sec=10,
        policy=safe_io.STANDARD_OUTBOUND_POLICY,
        allow_hosts=controls.get("allow_hosts"),
        allow_any_public_host=bool(controls.get("allow_any_public_host")),
        allow_loopback_hosts=controls.get("allow_loopback_hosts"),
        allow_insecure_base_url=allow_insecure_base_url,
        allow_private_network=bool(controls.get("allow_private_network")),
    )
    models = extract_models_from_payload(payload)
    cache_put(target.cache_key, models)
    return models
