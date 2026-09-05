"""
Central Redaction Service (S24).

Provides secure, bounded redaction for logs, traces, and audit events.
Prevents sensitive data leakage in observability outputs.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .env_aliases import get_env_value

logger = logging.getLogger("ComfyUI-OpenClaw.services.redaction")
_REDACTION_TAG_KEY: Optional[bytes] = None
_REDACTION_TAG_CACHE: Dict[Tuple[bytes, str], str] = {}
_REDACTION_TAG_LOCK = threading.Lock()

# Maximum input size for redact_text (prevents DoS)
MAX_TEXT_SIZE = 500_000  # 500KB

# Maximum recursion depth for redact_json
MAX_JSON_DEPTH = 10

# Redaction marker
REDACTED = "***REDACTED***"

# Default redaction patterns (mirroring OpenClaw's redact.ts)
# Ordered by priority: most specific first
DEFAULT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Authorization headers (Bearer, Basic)
    (
        re.compile(r"(Authorization:\s*Bearer\s+)[^\s]+", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(Authorization:\s*Basic\s+)[^\s]+", re.IGNORECASE), r"\1" + REDACTED),
    # API keys in headers (case-insensitive)
    (re.compile(r"(api[-_]?key:\s*)[^\s]+", re.IGNORECASE), r"\1" + REDACTED),
    (re.compile(r"(x-api-key:\s*)[^\s]+", re.IGNORECASE), r"\1" + REDACTED),
    # OpenAI-style keys (sk-, sess-, org-)
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}", re.IGNORECASE), REDACTED),
    (re.compile(r"\bsess-[a-zA-Z0-9]{20,}", re.IGNORECASE), REDACTED),
    (re.compile(r"\borg-[a-zA-Z0-9]{20,}", re.IGNORECASE), REDACTED),
    # Anthropic-style keys
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}", re.IGNORECASE), REDACTED),
    # Generic tokens/secrets
    (
        re.compile(r"(\btoken[\"']?\s*[:=]\s*[\"'])[^\"']+", re.IGNORECASE),
        r"\1" + REDACTED + '"',
    ),
    (
        re.compile(r"(\bsecret[\"']?\s*[:=]\s*[\"'])[^\"']+", re.IGNORECASE),
        r"\1" + REDACTED + '"',
    ),
    (
        re.compile(r"(\bpassword[\"']?\s*[:=]\s*[\"'])[^\"']+", re.IGNORECASE),
        r"\1" + REDACTED + '"',
    ),
    # PEM blocks (certificates/keys)
    (
        re.compile(
            r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----", re.IGNORECASE
        ),
        REDACTED,
    ),
    # JWT tokens (rough heuristic: 3 base64 segments separated by dots)
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    # R117: Slack token families.
    (re.compile(r"\bxoxb-[a-zA-Z0-9-]+", re.IGNORECASE), REDACTED),
    (re.compile(r"\bxapp-[a-zA-Z0-9-]+", re.IGNORECASE), REDACTED),
    (re.compile(r"\bxoxp-[a-zA-Z0-9-]+", re.IGNORECASE), REDACTED),
    (re.compile(r"\bxoxr-[a-zA-Z0-9-]+", re.IGNORECASE), REDACTED),
]

# Keys that should always be redacted in JSON (case-insensitive)
SENSITIVE_KEYS: Set[str] = {
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "secret_key",
    "secretkey",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "auth",
    "private_key",
    "privatekey",
    "session_id",
    "sessionid",
    "cookie",
    "cookies",
}


def stable_redaction_tag(value: Any, *, label: str = "value") -> str:
    """
    Build a deterministic, non-cleartext correlation tag for sensitive identifiers.

    The output is intentionally one-way and short so operators can correlate repeated
    values across logs without exposing the original identifier.
    """
    if value is None:
        return f"{label}:none"
    text = str(value).strip()
    if not text:
        return f"{label}:empty"
    # IMPORTANT: do not hash potentially sensitive identifiers here. CodeQL treats
    # even keyed hashes as weak-sensitive-data sinks for password/token-like inputs.
    key = _get_redaction_tag_key()
    cache_key = (key, text)
    with _REDACTION_TAG_LOCK:
        digest = _REDACTION_TAG_CACHE.get(cache_key)
        if digest is None:
            digest = secrets.token_hex(6)
            _REDACTION_TAG_CACHE[cache_key] = digest
    return f"{label}:{digest}"


def _get_redaction_tag_key() -> bytes:
    global _REDACTION_TAG_KEY
    if _REDACTION_TAG_KEY is None:
        raw = get_env_value("OPENCLAW_REDACTION_TAG_KEY")
        _REDACTION_TAG_KEY = raw.encode("utf-8") if raw else secrets.token_bytes(32)
    return _REDACTION_TAG_KEY


def redact_text(
    text: str, patterns: Optional[List[Tuple[re.Pattern, str]]] = None
) -> str:
    """
    Redact sensitive information from text using pattern matching.

    Args:
        text: Input text to redact.
        patterns: Optional custom patterns. Defaults to DEFAULT_PATTERNS.

    Returns:
        Redacted text.

    Raises:
        ValueError: If input exceeds MAX_TEXT_SIZE.
    """
    if not text:
        return text

    # Size bound check
    if len(text) > MAX_TEXT_SIZE:
        logger.warning(
            f"redact_text: Input size {len(text)} exceeds limit {MAX_TEXT_SIZE}"
        )
        raise ValueError(f"Input exceeds maximum size of {MAX_TEXT_SIZE} bytes")

    patterns = patterns or DEFAULT_PATTERNS
    result = text

    # Apply each pattern sequentially
    for pattern, replacement in patterns:
        try:
            result = pattern.sub(replacement, result)
        except Exception as e:
            # Pattern error should not crash redaction
            logger.error(f"redact_text: Pattern error: {e}")
            continue

    return result


def redact_json(
    value: Any,
    depth: int = 0,
    patterns: Optional[List[Tuple[re.Pattern, str]]] = None,
    max_depth: int = MAX_JSON_DEPTH,
) -> Any:
    """
    Recursively redact sensitive information in JSON-serializable structures.

    Redacts:
    - String values (using redact_text)
    - Keys in SENSITIVE_KEYS

    Args:
        value: JSON-serializable value (dict, list, str, etc).
        depth: Current recursion depth (internal use).
        patterns: Optional custom patterns for string redaction.
        max_depth: Maximum recursion depth.

    Returns:
        Redacted value (same type as input).
    """
    # Depth guard
    if depth > max_depth:
        logger.warning(f"redact_json: Max depth {max_depth} exceeded")
        return REDACTED

    # Dict: redact keys and recurse
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            # Check if key is sensitive (case-insensitive)
            key_lower = k.lower() if isinstance(k, str) else str(k).lower()
            if key_lower in SENSITIVE_KEYS:
                # Redact the entire value
                result[k] = REDACTED
            else:
                # Recurse into value
                result[k] = redact_json(v, depth + 1, patterns, max_depth)
        return result

    # List: recurse into each element
    elif isinstance(value, list):
        return [redact_json(item, depth + 1, patterns, max_depth) for item in value]

    # String: apply text redaction
    elif isinstance(value, str):
        # Avoid redacting very large strings (could be binary data)
        if len(value) > MAX_TEXT_SIZE:
            logger.warning(
                f"redact_json: String value exceeds {MAX_TEXT_SIZE}, truncating"
            )
            return REDACTED
        try:
            return redact_text(value, patterns)
        except ValueError:
            return REDACTED

    # Other types: pass through
    else:
        return value


def redact_dict_safe(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper around redact_json for dict inputs with error handling.

    Args:
        data: Input dictionary.

    Returns:
        Redacted dictionary. Returns original if error occurs.
    """
    try:
        return redact_json(data)  # type: ignore
    except Exception as e:
        logger.error(f"redact_dict_safe: Unexpected error: {e}")
        # Return original to avoid breaking callers
        return data
