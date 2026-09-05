"""
Unified LLM Client with multi-provider support.
R16: Provider-agnostic facade that routes to appropriate adapters.
"""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .env_aliases import get_env_value

try:
    from ..config import setup_logger
except ImportError:
    from config import setup_logger

try:
    from .effective_config import (
        get_effective_llm_base_url,
        get_effective_llm_config,
        get_effective_llm_model,
        get_effective_llm_provider,
    )
except ImportError:
    from services.effective_config import (  # type: ignore
        get_effective_llm_base_url,
        get_effective_llm_config,
        get_effective_llm_model,
        get_effective_llm_provider,
    )

from .providers import anthropic, openai_compat
from .providers.catalog import (
    DEFAULT_MODEL_BY_PROVIDER,
    DEFAULT_PROVIDER,
    ProviderType,
    get_provider_info,
    normalize_provider_base_url,
)
from .providers.keys import get_api_key_for_provider, mask_api_key, requires_api_key
from .structured_logging import (
    configure_logger_for_structured_output,
    emit_structured_log,
)

logger = setup_logger("openclaw.LLMClient")
configure_logger_for_structured_output(logger)

# R23: Plugin system integration
import asyncio
import concurrent.futures

try:
    from .plugins.contract import RequestContext
    from .plugins.manager import plugin_manager

    PLUGINS_AVAILABLE = True
except ImportError:
    PLUGINS_AVAILABLE = False
    logger.warning("Plugin system not available (import failed)")


def get_configured_provider() -> str:
    """Get configured provider from the unified effective-config facade."""
    return get_effective_llm_provider()


def get_configured_model(provider: str) -> str:
    """Get configured model for a provider via unified effective-config facade."""
    return get_effective_llm_model(provider)


def get_configured_base_url(provider: str) -> str:
    """Get configured base URL via unified effective-config facade."""
    return get_effective_llm_base_url(provider)


class LLMClient:
    """
    Unified LLM client supporting multiple providers.

    Supports:
    - OpenAI-compatible APIs (OpenAI, OpenRouter, Groq, DeepSeek, xAI, Gemini, Ollama, LM Studio)
    - Anthropic Messages API (Claude)
    - Timeout and retry with exponential backoff
    - Vision (images) for supported providers
    """

    # CRITICAL: keep this process-wide dedupe for missing-key warnings.
    # LLMClient is instantiated repeatedly (startup checks/UI polling paths).
    # Logging every init causes high-volume terminal spam for the same root cause.
    # Emit once per provider to preserve signal and avoid noisy regressions.
    _missing_api_key_warning_emitted: set[str] = set()

    def __init__(
        self,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        """
        Initialize LLM client.

        Args:
            provider: Provider name (e.g., "anthropic", "openai", "ollama")
            base_url: Override base URL
            model: Override model name
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for transient errors
        """
        # Load effective config through the R148 facade to keep all high-frequency
        # readers on one supported surface.
        eff_config, _ = get_effective_llm_config()

        self.provider = provider or eff_config.get("provider") or DEFAULT_PROVIDER

        # Resolve base_url: Arg > Config > Provider Default
        self.base_url = base_url or eff_config.get("base_url")
        if not self.base_url:
            info = get_provider_info(self.provider)
            if info:
                self.base_url = info.base_url
        # IMPORTANT: test-connection and normal OpenAI-compatible completions must
        # share the same Ollama `/v1` normalization path as model discovery. Do not
        # bypass this with provider-specific ad hoc request URL assembly.
        self.base_url = normalize_provider_base_url(
            self.provider, str(self.base_url or "")
        )

        # R57: Strict Precedence (Arg > Config > Default)
        # CRITICAL: Only inherit config['model'] if the effective provider matches config['provider'].
        # If user overrides provider (e.g. "openai") but config has ("anthropic", "claude-3"),
        # we must NOT use "claude-3" for "openai".

        config_provider = eff_config.get("provider")
        config_model = eff_config.get("model")

        if model:
            # 1. Explicit argument override
            self.model = model
        elif self.provider == config_provider:
            # 2. Config usage (provider matches) -> use config model
            self.model = config_model
        else:
            # 3. Provider mismatch (arg override vs config) -> do NOT use config model
            # Fallback to default for the *new* provider
            self.model = None

        # If we still have no model, try to get a default for the provider
        if not self.model:
            from .providers.catalog import DEFAULT_MODEL_BY_PROVIDER

            self.model = DEFAULT_MODEL_BY_PROVIDER.get(self.provider, "default")

        # R23 (plugin wiring) + R57 (precedence compatibility):
        # CRITICAL: keep model alias resolution in __init__.
        # Some callers instantiate LLMClient and execute immediately without calling Settings save flow,
        # and tests assert that "model.resolve" runs during initialization.
        # Removing this block regresses alias behavior (e.g., gpt4 -> gpt-4) and breaks unit tests.
        # CI guard: tests/test_llm_client_plugins.py::test_model_alias_resolution_on_init.
        if PLUGINS_AVAILABLE and self.model:
            try:
                from .plugins.async_bridge import run_async_in_sync_context

                resolve_ctx = RequestContext(
                    provider=self.provider,
                    model=str(self.model),
                    trace_id="init",
                )
                resolved_model = run_async_in_sync_context(
                    plugin_manager.execute_first(
                        "model.resolve", resolve_ctx, str(self.model)
                    )
                )
                if isinstance(resolved_model, str) and resolved_model.strip():
                    self.model = resolved_model.strip()
            except Exception as e:
                logger.warning(f"Model alias resolution failed (non-fatal): {e}")

        self.timeout = (
            timeout if timeout is not None else eff_config.get("timeout_sec", 120)
        )
        self.max_retries = (
            max_retries if max_retries is not None else eff_config.get("max_retries", 3)
        )
        self.allow_private_network = bool(
            eff_config.get("allow_private_network", False)
        )

        # Get provider info
        self.provider_info = get_provider_info(self.provider)
        if not self.provider_info:
            logger.warning(
                f"Unknown provider '{self.provider}', treating as OpenAI-compatible"
            )

        # Get API key
        self.api_key = get_api_key_for_provider(self.provider)

        # Validate key if required
        if requires_api_key(self.provider) and not self.api_key:
            # IMPORTANT: one-time warning per provider only (anti-spam guard).
            if self.provider not in self._missing_api_key_warning_emitted:
                logger.warning(f"No API key found for provider '{self.provider}'")
                self._missing_api_key_warning_emitted.add(self.provider)

    def _get_api_type(self) -> ProviderType:
        """Get the API type for the current provider."""
        if self.provider_info:
            return self.provider_info.api_type
        return ProviderType.OPENAI_COMPAT

    def _get_failover_candidates(
        self,
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """
        Get ordered list of (provider, model, base_url) tuples for failover.
        Priority: primary > fallback models (same provider) > fallback providers.

        Returns empty if no fallbacks configured (preserves existing behavior).
        """
        eff_config, _ = get_effective_llm_config()

        # Get failover config
        fallback_models = eff_config.get("fallback_models", [])
        fallback_providers = eff_config.get("fallback_providers", [])

        # R14: Use failover.get_failover_candidates for ordering
        try:
            from ..services.failover import get_failover_candidates
        except ImportError:
            from services.failover import get_failover_candidates

        # Get ordered candidates (provider, model)
        candidates_2d = get_failover_candidates(
            primary_provider=self.provider,
            primary_model=self.model,
            fallback_models=fallback_models if fallback_models else None,
            fallback_providers=fallback_providers if fallback_providers else None,
        )

        return self._resolve_candidate_base_urls(candidates_2d)

    def _resolve_candidate_base_urls(
        self, candidates_2d: List[Tuple[str, Optional[str]]]
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """Convert (provider, model) candidates into (provider, model, base_url)."""
        # Convert to (provider, model, base_url) tuples
        candidates_3d = []
        for provider, model in candidates_2d:
            # Resolve base_url for each candidate
            if provider == self.provider:
                # Same as primary, use configured base_url
                candidates_3d.append((provider, model, self.base_url))
            else:
                # Different provider, get default base_url
                info = get_provider_info(provider)
                base_url = info.base_url if info else None
                candidates_3d.append((provider, model, base_url))

        return candidates_3d

    def _sort_candidates_3d_by_health(
        self,
        candidates: List[Tuple[str, Optional[str], Optional[str]]],
        failover_state,
    ) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """R130: 3D candidate sort preserving original stable order tiebreak."""
        indexed = list(enumerate(candidates))
        indexed.sort(
            key=lambda item: (
                failover_state.get_health_score(item[1][0], item[1][1]),
                -item[0],
            ),
            reverse=True,
        )
        return [cand for _, cand in indexed]

    def _prepare_failover_execution(self) -> Dict[str, Any]:
        """R130 phase 1: prepare failover dependencies and candidate list."""
        try:
            from ..services.runtime_config import get_effective_config
        except ImportError:
            from services.runtime_config import get_effective_config

        try:
            from ..services.failover import (
                ErrorCategory,
                classify_cooldown,
                classify_error,
                get_cooldown_duration,
                get_failover_state,
                should_failover,
                should_retry,
            )
        except ImportError:
            from services.failover import (
                ErrorCategory,
                classify_cooldown,
                classify_error,
                get_cooldown_duration,
                get_failover_state,
                should_failover,
                should_retry,
            )

        eff_config, _ = get_effective_config()
        max_failover_candidates = eff_config.get("max_failover_candidates", 3)
        # NOTE: Keep at least 1 candidate; zero yields empty attempts and opaque errors.
        # CRITICAL: Do not remove this guard. It prevents "All 0 failover candidates exhausted".
        try:
            max_failover_candidates = int(max_failover_candidates)
        except (TypeError, ValueError):
            max_failover_candidates = 3
        if max_failover_candidates < 1:
            max_failover_candidates = 1

        failover_state = get_failover_state()
        raw_candidates_3d = self._get_failover_candidates()
        ordered_candidates_3d = self._sort_candidates_3d_by_health(
            raw_candidates_3d, failover_state
        )
        candidates_to_try = ordered_candidates_3d[:max_failover_candidates]

        return {
            "ErrorCategory": ErrorCategory,
            "classify_cooldown": classify_cooldown,
            "classify_error": classify_error,
            "get_cooldown_duration": get_cooldown_duration,
            "should_failover": should_failover,
            "should_retry": should_retry,
            "failover_state": failover_state,
            "candidates_to_try": candidates_to_try,
        }

    def _get_egress_controls(
        self, provider: str, base_url: Optional[str]
    ) -> Dict[str, Any]:
        """Build canonical SSRF controls for provider egress."""
        try:
            from ..services.runtime_config import get_llm_egress_controls
        except ImportError:
            from services.runtime_config import get_llm_egress_controls

        # IMPORTANT:
        # Keep provider egress controls centralized. Falling back to policy-only or
        # ad-hoc allowlists causes path drift and can reintroduce S65 regressions.
        return get_llm_egress_controls(
            provider,
            base_url or "",
            allow_private_network=self.allow_private_network,
        )

    def _allow_insecure_base_url(self) -> bool:
        return (
            get_env_value("OPENCLAW_ALLOW_INSECURE_BASE_URL") or ""
        ).strip().lower() in ("1", "true", "yes", "y", "on")

    def _validate_candidate_url(self, provider: str, base_url: Optional[str]) -> bool:
        """
        Validate base_url against S16/S16.1 SSRF policy.
        Returns True if safe to use, False if should skip candidate.
        """
        # Only validate when we have a base_url and it's not from a known provider
        if not base_url:
            return True

        # If provider has a known default base_url, assume it's safe
        info = get_provider_info(provider)
        if info and base_url == info.base_url:
            return True

        # Custom base_url - validate against SSRF policy (S16/S16.1)
        try:
            from ..services.safe_io import (
                STANDARD_OUTBOUND_POLICY,
                validate_outbound_url,
            )
        except ImportError:
            from services.safe_io import STANDARD_OUTBOUND_POLICY, validate_outbound_url

        try:
            controls = self._get_egress_controls(provider, base_url)

            # S16/S16.1/S51: Validate URL (raises on block).
            validate_outbound_url(
                base_url,
                allow_hosts=controls.get("allow_hosts"),
                allow_any_public_host=bool(controls.get("allow_any_public_host")),
                allow_loopback_hosts=controls.get("allow_loopback_hosts"),
                allow_insecure_base_url=self._allow_insecure_base_url(),
                allow_private_network=bool(controls.get("allow_private_network")),
                policy=STANDARD_OUTBOUND_POLICY,
            )
            return True
        except Exception as e:
            logger.warning(
                f"Failover candidate {provider} with base_url={base_url} "
                f"blocked by SSRF policy: {e}"
            )
            return False

    def _extract_status_code(self, error: Exception) -> Optional[int]:
        """Extract HTTP status code from exception message."""
        error_str = str(error)
        # Look for HTTP status codes (400-599)
        match = re.search(r"\b([45]\d{2})\b", error_str)
        return int(match.group(1)) if match else None

    def _execute_request(
        self,
        system: str,
        user_message: str,
        image_base64: Optional[str],
        image_media_type: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        streaming: bool = False,
        on_text_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute a single request attempt (factored out for failover)."""
        api_type = self._get_api_type()

        if api_type == ProviderType.ANTHROPIC:
            if tools or tool_choice:
                logger.debug(
                    "F25: tools/tool_choice provided but Anthropic provider does not support tool calling; ignoring."
                )
            return self._complete_anthropic(
                system,
                user_message,
                image_base64,
                image_media_type,
                temperature,
                max_tokens,
            )
        else:
            return self._complete_openai_compat(
                system,
                user_message,
                image_base64,
                image_media_type,
                temperature,
                max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                streaming=streaming,
                on_text_delta=on_text_delta,
            )

    def _execute_failover_candidates(
        self,
        *,
        phase: Dict[str, Any],
        system: str,
        user_message: str,
        image_base64: Optional[str],
        image_media_type: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
        trace_id: Optional[str],
        streaming: bool,
        on_text_delta: Optional[Callable[[str], None]],
    ) -> Dict[str, Any]:
        """R130 phase 2: execute failover/retry loop against prepared candidates."""
        ErrorCategory = phase["ErrorCategory"]
        classify_cooldown = phase["classify_cooldown"]
        classify_error = phase["classify_error"]
        get_cooldown_duration = phase["get_cooldown_duration"]
        should_failover = phase["should_failover"]
        should_retry = phase["should_retry"]
        failover_state = phase["failover_state"]
        candidates_to_try = phase["candidates_to_try"]

        last_error = None
        candidates_tried = 0

        # Save original config to restore later
        original_provider = self.provider
        original_model = self.model
        original_base_url = self.base_url
        original_api_key = self.api_key
        original_provider_info = self.provider_info

        try:
            for candidate_idx, (provider, model, base_url) in enumerate(
                candidates_to_try
            ):
                # Skip if in cooldown
                if failover_state.is_cooling_down(provider, model):
                    if candidate_idx < (len(candidates_to_try) - 1):
                        logger.info(
                            f"Skipping candidate {provider}/{model} (in cooldown)"
                        )
                        continue
                    logger.warning(
                        f"Candidate {provider}/{model} is in cooldown, but no alternatives remain; attempting anyway."
                    )

                # SSRF validation for custom base URLs
                if not self._validate_candidate_url(provider, base_url):
                    logger.warning(
                        f"Skipping candidate {provider}/{model} (SSRF policy violation)"
                    )
                    continue

                candidates_tried += 1

                # Temporarily switch to candidate configuration
                self.provider = provider
                self.model = model or original_model
                self.base_url = base_url
                self.provider_info = get_provider_info(provider)
                self.api_key = get_api_key_for_provider(provider)

                # Validate API key for this candidate
                if requires_api_key(provider) and not self.api_key:
                    logger.warning(f"No API key for candidate {provider}, skipping")
                    continue

                # Log failover attempt (only if not primary)
                if candidate_idx > 0:
                    logger.info(f"Trying failover candidate: {provider}/{self.model}")
                emit_structured_log(
                    logger,
                    level=logging.INFO,
                    event="llm.candidate.attempt",
                    fields={
                        "provider": provider,
                        "model": self.model,
                        "candidate_index": candidate_idx,
                        "streaming": bool(streaming),
                        "trace_id": trace_id,
                    },
                )

                # Per-candidate retry loop
                # R37: Check throttle before attempting candidate (best-effort).
                # Never hard-block the final candidate; if there are no alternatives, proceed.
                if not failover_state.can_attempt_now(provider, model):
                    if candidate_idx < (len(candidates_to_try) - 1):
                        logger.debug(
                            f"Throttling {provider}/{model} (min interval not met)"
                        )
                        continue  # Skip this candidate (try alternatives)
                    logger.debug(
                        f"Throttling {provider}/{model} (min interval not met), "
                        f"but no alternatives remain; proceeding."
                    )

                # R37: Mark attempt
                failover_state.mark_attempt(provider, model)

                # Retry loop for current candidate
                candidate_last_error = None
                for attempt in range(self.max_retries + 1):
                    if attempt > 0:
                        sleep_time = min(2**attempt, 8)  # Cap at 8 seconds
                        logger.info(
                            f"Retrying {provider}/{self.model} "
                            f"(attempt {attempt}/{self.max_retries}) in {sleep_time}s..."
                        )
                        emit_structured_log(
                            logger,
                            level=logging.INFO,
                            event="llm.candidate.retry",
                            fields={
                                "provider": provider,
                                "model": self.model,
                                "attempt": attempt,
                                "max_retries": self.max_retries,
                                "sleep_sec": sleep_time,
                                "trace_id": trace_id,
                            },
                        )
                        time.sleep(sleep_time)

                    try:
                        result = self._execute_request(
                            system,
                            user_message,
                            image_base64,
                            image_media_type,
                            temperature,
                            max_tokens,
                            tools=tools,
                            tool_choice=tool_choice,
                            streaming=streaming,
                            on_text_delta=on_text_delta,
                        )

                        # R37: Update health score on success
                        failover_state.update_health_score(
                            provider,
                            model,
                            category=ErrorCategory.UNKNOWN,  # Dummy category for success
                            is_success=True,
                        )

                        # Success! Log if we used a fallback
                        if candidate_idx > 0:
                            logger.info(
                                f"Failover successful to {provider}/{self.model}"
                            )
                            emit_structured_log(
                                logger,
                                level=logging.INFO,
                                event="llm.failover.success",
                                fields={
                                    "provider": provider,
                                    "model": self.model,
                                    "candidate_index": candidate_idx,
                                    "trace_id": trace_id,
                                },
                            )
                        else:
                            emit_structured_log(
                                logger,
                                level=logging.INFO,
                                event="llm.request.success",
                                fields={
                                    "provider": provider,
                                    "model": self.model,
                                    "candidate_index": candidate_idx,
                                    "trace_id": trace_id,
                                },
                            )

                        return result

                    except Exception as e:
                        candidate_last_error = e
                        status_code = self._extract_status_code(e)
                        cooldown_decision = classify_cooldown(e, status_code)
                        error_category, retry_after = classify_error(e, status_code)
                        logger.error(
                            f"Request failed for {provider}/{self.model}: {e} "
                            f"(category: {error_category.value}, status: {status_code})"
                        )
                        emit_structured_log(
                            logger,
                            level=logging.ERROR,
                            event="llm.request.failure",
                            fields={
                                "provider": provider,
                                "model": self.model,
                                "candidate_index": candidate_idx,
                                "category": error_category.value,
                                "cooldown_bucket": cooldown_decision.bucket,
                                "reason_code": cooldown_decision.reason_code,
                                "status_code": status_code,
                                "error_type": type(e).__name__,
                                "trace_id": trace_id,
                            },
                        )

                        # R37: Update health score on failure (before dedupe check)
                        failover_state.update_health_score(
                            provider, model, error_category, is_success=False
                        )

                        # Decide: retry same candidate or failover to next
                        if should_retry(error_category):
                            # Retry same candidate (continue retry loop)
                            last_error = e
                            continue

                        elif should_failover(error_category):
                            # R37: Check dedupe before setting cooldown/logging
                            if failover_state.should_suppress_duplicate(
                                provider, model, error_category
                            ):
                                # Duplicate within window - suppress spam
                                logger.debug(
                                    f"Suppressing duplicate {error_category.value} for {provider}/{model}"
                                )
                            else:
                                # New failure - set cooldown and log
                                duration = get_cooldown_duration(
                                    error_category, retry_after_override=retry_after
                                )
                                failover_state.set_cooldown(
                                    provider,
                                    model,
                                    cooldown_decision.reason_code,
                                    duration,
                                    reason_code=cooldown_decision.reason_code,
                                    bucket=cooldown_decision.bucket,
                                    retry_after_sec=retry_after,
                                )
                                logger.warning(
                                    f"Failover triggered for {provider}/{model}: "
                                    f"{cooldown_decision.reason_code} (cooldown: {duration}s)"
                                )
                                emit_structured_log(
                                    logger,
                                    level=logging.WARNING,
                                    event="llm.failover.triggered",
                                    fields={
                                        "provider": provider,
                                        "model": model,
                                        "category": error_category.value,
                                        "cooldown_bucket": cooldown_decision.bucket,
                                        "reason_code": cooldown_decision.reason_code,
                                        "cooldown_sec": duration,
                                        "retry_after_sec": retry_after,
                                        "trace_id": trace_id,
                                    },
                                )

                            last_error = e
                            break  # Exit retry loop, try next candidate

                        else:
                            # Fatal error (e.g., auth on first attempt), don't retry or failover
                            # But let's still try other candidates in case it's provider-specific
                            logger.error(f"Non-retryable error: {error_category.value}")
                            last_error = e
                            break

                # If we exhausted all retries for this candidate, continue to next
                last_error = candidate_last_error or last_error
        finally:
            # Always restore original configuration
            self.provider = original_provider
            self.model = original_model
            self.base_url = original_base_url
            self.api_key = original_api_key
            self.provider_info = original_provider_info

        # All candidates exhausted
        emit_structured_log(
            logger,
            level=logging.ERROR,
            event="llm.failover.exhausted",
            fields={
                "provider": original_provider,
                "model": original_model,
                "candidates_tried": candidates_tried,
                "trace_id": trace_id,
            },
        )
        raise last_error or RuntimeError(
            f"All {candidates_tried} failover candidates exhausted"
        )

    def complete(
        self,
        system: str,
        user_message: str,
        image_base64: Optional[str] = None,
        image_media_type: str = "image/png",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[
            List[Dict[str, Any]]
        ] = None,  # F25: Optional tool calling schemas
        tool_choice: Optional[str] = None,  # F25: Optional tool_choice (OpenAI-compat)
        trace_id: Optional[str] = None,  # R25: Trace context
        streaming: bool = False,  # R38: optional provider streaming path
        on_text_delta: Optional[Callable[[str], None]] = None,  # R38 callback
    ) -> Dict[str, Any]:
        """
        Send a completion request to the configured provider.

        Args:
            system: System prompt
            user_message: User message text
            image_base64: Optional base64-encoded image
            image_media_type: MIME type of image
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response

        Returns:
            {"text": str, "raw": dict}
        """
        if requires_api_key(self.provider) and not self.api_key:
            raise ValueError(f"API key not configured for provider '{self.provider}'")

        # R23: Param transforms + audit via plugins
        # Default safe bounds
        SAFE_BOUNDS = {
            "temperature": (0.0, 2.0, 0.7),  # (min, max, default)
            "max_tokens": (1, 128000, 4096),
        }

        # Initial params
        params = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        run_async_in_sync_context = None
        ctx = None

        if PLUGINS_AVAILABLE:
            try:
                from .plugins.async_bridge import (
                    run_async_in_sync_context as _run_async_in_sync_context,
                )

                run_async_in_sync_context = _run_async_in_sync_context
                ctx = RequestContext(
                    provider=self.provider,
                    model=self.model,
                    trace_id=trace_id or "unknown",
                )

                # Apply parameter transforms (params clamping, etc.)
                transformed = run_async_in_sync_context(
                    plugin_manager.execute_sequential("llm.params", ctx, params)
                )

                if transformed and isinstance(transformed, dict):
                    params = transformed
                else:
                    logger.warning(
                        f"Plugin transform returned invalid data: {transformed}, reverting to input"
                    )
            except Exception as e:
                logger.warning(f"Plugin param transform failed (non-fatal): {e}")

        # Enforce hard safety bounds (True Fail-Closed)
        # Regardless of whether plugin succeeded, failed, or returned garbage,
        # we ALWAYS clamp to safe ranges before proceeding.

        # Clamp Temperature
        t_min, t_max, t_def = SAFE_BOUNDS["temperature"]
        t_val = params.get("temperature", temperature)
        if not isinstance(t_val, (int, float)):
            t_val = t_def
        temperature = max(t_min, min(t_val, t_max))

        # Clamp Max Tokens
        m_min, m_max, m_def = SAFE_BOUNDS["max_tokens"]
        m_val = params.get("max_tokens", max_tokens)
        if not isinstance(m_val, int):
            m_val = m_def
        max_tokens = max(m_min, min(m_val, m_max))

        # Re-sync params for audit
        audit_params = {"temperature": temperature, "max_tokens": max_tokens}
        if PLUGINS_AVAILABLE and run_async_in_sync_context and ctx:
            # Audit request (fire-and-forget, never fails request)
            try:
                audit_payload = {
                    "provider": self.provider,
                    "model": self.model,
                    "params": audit_params,
                    "has_image": image_base64 is not None,
                }
                run_async_in_sync_context(
                    plugin_manager.execute_parallel(
                        "llm.audit_request", ctx, audit_payload
                    )
                )
            except Exception:
                pass  # Audit failures are non-fatal

        phase = self._prepare_failover_execution()
        return self._execute_failover_candidates(
            phase=phase,
            system=system,
            user_message=user_message,
            image_base64=image_base64,
            image_media_type=image_media_type,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            trace_id=trace_id,
            streaming=streaming,
            on_text_delta=on_text_delta,
        )

    def _complete_anthropic(
        self,
        system: str,
        user_message: str,
        image_base64: Optional[str],
        image_media_type: str,
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        """Complete using Anthropic Messages API."""
        egress_controls = self._get_egress_controls(self.provider, self.base_url)

        if image_base64:
            message = anthropic.build_vision_message(
                user_message, image_base64, image_media_type
            )
        else:
            message = {"role": "user", "content": user_message}

        return anthropic.make_request(
            base_url=self.base_url,
            api_key=self.api_key,
            messages=[message],
            model=self.model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self.timeout,
            allow_hosts=egress_controls.get("allow_hosts"),
            allow_any_public_host=bool(egress_controls.get("allow_any_public_host")),
            allow_loopback_hosts=egress_controls.get("allow_loopback_hosts"),
            allow_insecure_base_url=self._allow_insecure_base_url(),
            allow_private_network=bool(egress_controls.get("allow_private_network")),
        )

    def _complete_openai_compat(
        self,
        system: str,
        user_message: str,
        image_base64: Optional[str],
        image_media_type: str,
        temperature: float,
        max_tokens: int,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        streaming: bool = False,
        on_text_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Complete using OpenAI-compatible API."""
        egress_controls = self._get_egress_controls(self.provider, self.base_url)
        messages = [{"role": "system", "content": system}]

        if image_base64:
            messages.append(
                openai_compat.build_vision_message(
                    user_message, image_base64, image_media_type
                )
            )
        else:
            messages.append({"role": "user", "content": user_message})

        # R38: Streaming is optional and currently only enabled for non-tool
        # OpenAI-compatible text paths. Tool-call streaming deltas are not parsed yet.
        if streaming and not tools and not tool_choice:
            try:
                return openai_compat.make_request_stream(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    messages=messages,
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=self.timeout,
                    allow_hosts=egress_controls.get("allow_hosts"),
                    allow_any_public_host=bool(
                        egress_controls.get("allow_any_public_host")
                    ),
                    allow_loopback_hosts=egress_controls.get("allow_loopback_hosts"),
                    allow_insecure_base_url=self._allow_insecure_base_url(),
                    allow_private_network=bool(
                        egress_controls.get("allow_private_network")
                    ),
                    on_text_delta=on_text_delta,
                )
            except Exception as e:
                logger.info(
                    "R38: Streaming request unavailable/failed; falling back to non-streaming request: %s",
                    e,
                )

        return openai_compat.make_request(
            base_url=self.base_url,
            api_key=self.api_key,
            messages=messages,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self.timeout,
            tools=tools,
            tool_choice=tool_choice,
            allow_hosts=egress_controls.get("allow_hosts"),
            allow_any_public_host=bool(egress_controls.get("allow_any_public_host")),
            allow_loopback_hosts=egress_controls.get("allow_loopback_hosts"),
            allow_insecure_base_url=self._allow_insecure_base_url(),
            allow_private_network=bool(egress_controls.get("allow_private_network")),
        )

    def get_provider_summary(self) -> Dict[str, Any]:
        """Get a summary of the current provider configuration (no secrets)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_type": self._get_api_type().value,
            "key_configured": self.api_key is not None,
            "key_masked": mask_api_key(self.api_key) if self.api_key else None,
        }
