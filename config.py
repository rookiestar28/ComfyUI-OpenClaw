import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

try:
    from .services.env_aliases import EnvLookupMode
    from .services.env_aliases import get_env_value as resolve_env_value
except ImportError:
    from services.env_aliases import EnvLookupMode
    from services.env_aliases import get_env_value as resolve_env_value

# R139: centralized env-alias helpers for config surface compatibility.
try:
    from .services.config_layers import (
        GENERIC_LLM_API_KEY_ENV_KEYS,
        get_first_present_env,
    )
    from .services.effective_config import get_effective_llm_api_key
except Exception:
    try:
        from services.config_layers import (  # type: ignore
            GENERIC_LLM_API_KEY_ENV_KEYS,
            get_first_present_env,
        )
        from services.effective_config import get_effective_llm_api_key  # type: ignore
    except Exception:
        GENERIC_LLM_API_KEY_ENV_KEYS = (
            "OPENCLAW_LLM_API_KEY",
            "MOLTBOT_LLM_API_KEY",
            "CLAWDBOT_LLM_API_KEY",
        )

        def get_first_present_env(keys, *, env=None):  # type: ignore
            ordered_keys = tuple(keys)
            if not ordered_keys:
                return None
            return resolve_env_value(
                ordered_keys[0],
                aliases=ordered_keys[1:],
                mode=EnvLookupMode.PRESENCE,
                env=env,
            )

        def get_effective_llm_api_key(provider=None, tenant_id=None):  # type: ignore
            return get_first_present_env(GENERIC_LLM_API_KEY_ENV_KEYS)


# Pack metadata
PACK_NAME = "ComfyUI-OpenClaw"
PACK_START_TIME = time.time()


def _extract_toml_section(text: str, header: str) -> Optional[str]:
    pattern = re.compile(
        rf"(?ms)^\ufeff?\[{re.escape(header)}\]\s*$\n(?P<body>.*?)(?=^\[|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return None
    return match.group("body")


def _extract_toml_string_assignment(section_text: str, key: str) -> Optional[str]:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s*=\s*['\"]([^'\"]+)['\"]\s*$",
        section_text,
    )
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _parse_pyproject_version_text(
    text: str, *, prefer_tomllib: bool = True
) -> Optional[str]:
    if prefer_tomllib:
        try:
            from tomllib import loads as _toml_loads  # type: ignore
        except Exception:
            _toml_loads = None

        if _toml_loads:
            try:
                data = _toml_loads(text)
                ver = data.get("project", {}).get("version")
                if ver:
                    return str(ver).strip() or None
            except Exception:
                pass

    # IMPORTANT: keep this fallback section-bounded.
    # Matching any `version = ...` outside `[project]` silently reports the wrong build.
    project_section = _extract_toml_section(text, "project")
    if project_section is None:
        return None
    return _extract_toml_string_assignment(project_section, "version")


def _read_pyproject_version_from_path(
    pyproject_path: os.PathLike[str] | str, *, prefer_tomllib: bool = True
) -> Optional[str]:
    """
    Read version from pyproject.toml ([project].version) as the single source of truth.

    Uses a lightweight regex parse to avoid non-stdlib TOML dependencies.
    """
    try:
        pyproject_path = os.fspath(pyproject_path)
        if not os.path.exists(pyproject_path):
            return None
        with open(pyproject_path, "r", encoding="utf-8") as f:
            text = f.read()
        return _parse_pyproject_version_text(
            text,
            prefer_tomllib=prefer_tomllib,
        )
    except Exception:
        return None


def _read_pyproject_version() -> Optional[str]:
    pack_dir = os.path.dirname(os.path.abspath(__file__))
    return _read_pyproject_version_from_path(os.path.join(pack_dir, "pyproject.toml"))


# Version: single source of truth is pyproject.toml (line 4 in this repo).
PACK_VERSION = _read_pyproject_version() or "0.1.0"

# Environment variable for the API key
ENV_API_KEY = GENERIC_LLM_API_KEY_ENV_KEYS[0]
LEGACY_ENV_API_KEY = GENERIC_LLM_API_KEY_ENV_KEYS[1]
LEGACY2_ENV_API_KEY = GENERIC_LLM_API_KEY_ENV_KEYS[2]

# Data directory (R11: use portable state directory)
try:
    # Prefer package-relative import (ComfyUI loads custom nodes by file loader)
    from .services.state_dir import (  # type: ignore
        get_log_path,
        get_state_dir,
        peek_log_path,
        peek_state_dir,
    )
except Exception:
    try:
        # Fallback for unit tests / direct sys.path imports
        from services.state_dir import (
            get_log_path,
            get_state_dir,
            peek_log_path,
            peek_state_dir,
        )
    except Exception:
        get_state_dir = None
        get_log_path = None
        peek_state_dir = None
        peek_log_path = None

if peek_state_dir and peek_log_path:
    DATA_DIR = peek_state_dir()
    LOG_FILE = peek_log_path()
else:
    # Last-resort fallback during early import or if state_dir is unavailable
    PACK_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(PACK_DIR, "data")
    LOG_FILE = os.path.join(DATA_DIR, "openclaw.log")

_IMPORT_DATA_DIR = DATA_DIR
_IMPORT_LOG_FILE = LOG_FILE

# IMPORTANT: startup log truncation must run once per process.
# Multiple module-level loggers call setup_logger(); repeated truncation would
# erase fresh logs emitted after the first logger initialization.
_LOG_TRUNCATE_APPLIED = False


def _is_env_enabled(*keys: str) -> bool:
    if not keys:
        return False
    # IMPORTANT: this is intentionally TRUTHY_ANY. An explicit false canonical flag must
    # not suppress a true legacy log-truncation flag during the compatibility window.
    return (
        resolve_env_value(
            keys[0],
            aliases=keys[1:],
            mode=EnvLookupMode.TRUTHY_ANY,
        )
        is not None
    )


def _maybe_truncate_log_on_start(logger: logging.Logger) -> None:
    global _LOG_TRUNCATE_APPLIED
    if _LOG_TRUNCATE_APPLIED:
        return
    if not _is_env_enabled(
        "OPENCLAW_LOG_TRUNCATE_ON_START", "MOLTBOT_LOG_TRUNCATE_ON_START"
    ):
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "w", encoding="utf-8"):
            pass
        logger.info(
            f"Startup log truncation applied for {LOG_FILE} "
            "(OPENCLAW_LOG_TRUNCATE_ON_START=1)"
        )
        _LOG_TRUNCATE_APPLIED = True
    except Exception as e:
        logger.warning(f"Failed to truncate startup log file {LOG_FILE}: {e}")


def _ensure_log_targets() -> tuple[str, str]:
    global DATA_DIR, LOG_FILE
    if DATA_DIR != _IMPORT_DATA_DIR or LOG_FILE != _IMPORT_LOG_FILE:
        return DATA_DIR, LOG_FILE
    if get_state_dir and get_log_path:
        DATA_DIR = get_state_dir()
        LOG_FILE = get_log_path()
    return DATA_DIR, LOG_FILE


class RedactedFormatter(logging.Formatter):
    """
    Custom formatter to redact sensitive information (like API keys) from logs.
    """

    def __init__(self, sensitive_strings: list[str], fmt=None, datefmt=None, style="%"):
        super().__init__(fmt, datefmt, style)
        self.sensitive_strings = sensitive_strings

    def format(self, record):
        original = super().format(record)
        for s in self.sensitive_strings:
            if s:
                original = original.replace(s, "[REDACTED]")
        return original


def get_api_key() -> Optional[str]:
    """
    Retrieves the effective LLM API key via the unified config facade.

    This keeps logger redaction aligned with the same provider/key resolution
    path used by runtime consumers.
    """
    value = get_effective_llm_api_key()
    return value or None


def setup_logger(name: str = "ComfyUI-OpenClaw") -> logging.Logger:
    """
    Sets up a logger with redaction for the API key.
    Includes both console and file handlers with rotation.
    """
    logger = logging.getLogger(name)
    # CRITICAL: keep propagate disabled.
    # If this is changed to True, ComfyUI/root handlers re-emit the same record,
    # and terminal output regresses to duplicated spam:
    #   [openclaw.LLMClient] WARNING: ...
    #   No API key found for provider ...
    logger.propagate = False

    # Only add handler if not already added to avoid duplicates on reload
    if not logger.handlers:
        data_dir, log_file = _ensure_log_targets()
        _maybe_truncate_log_on_start(logger)
        api_key = get_api_key()
        sensitive = [api_key] if api_key else []
        formatter = RedactedFormatter(
            sensitive, fmt="[%(name)s] %(levelname)s: %(message)s"
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler with rotation (5MB, 3 backups)
        try:
            os.makedirs(data_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            # If file logging fails, continue with console only
            pass

        logger.setLevel(logging.INFO)

    return logger


# Global logger handle for compatibility; actual handler bootstrap is lazy.
logger = logging.getLogger("ComfyUI-OpenClaw")
