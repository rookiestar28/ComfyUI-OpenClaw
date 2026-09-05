"""
State Directory Service (R11).
Provides portable, safe state directory paths for logs, caches, and persistence.
"""

import logging
import os
import sys
from typing import Optional

from .env_aliases import get_env_value

logger = logging.getLogger("ComfyUI-OpenClaw.services.state_dir")

# Environment variables to override state directory
STATE_DIR_ENV = "OPENCLAW_STATE_DIR"
LEGACY_STATE_DIR_ENV = "MOLTBOT_STATE_DIR"

# Default subdirectory names under user data
STATE_DIR_NAME = "comfyui-openclaw"
LEGACY_STATE_DIR_NAME = "comfyui-moltbot"


def _get_user_data_dir(subdir: Optional[str] = None) -> str:
    """
    Get the platform-appropriate user data directory.

    Returns:
        Path to user-writable application data directory.
    """
    subdir = subdir or STATE_DIR_NAME
    if sys.platform == "win32":
        # Windows: %LOCALAPPDATA%\{subdir}
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, subdir)
    elif sys.platform == "darwin":
        # macOS: ~/Library/Application Support/{subdir}
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", subdir
        )
    else:
        # Linux/Unix: ~/.local/share/{subdir} (XDG_DATA_HOME)
        base = os.environ.get(
            "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
        )
        return os.path.join(base, subdir)


def _resolve_state_dir_path() -> str:
    """Resolve the canonical state-dir path without creating it."""
    env_dir = get_env_value(STATE_DIR_ENV, aliases=(LEGACY_STATE_DIR_ENV,))
    if env_dir:
        return os.path.abspath(env_dir)

    new_dir = _get_user_data_dir(STATE_DIR_NAME)
    legacy_dir = _get_user_data_dir(LEGACY_STATE_DIR_NAME)
    return (
        legacy_dir
        if (os.path.exists(legacy_dir) and not os.path.exists(new_dir))
        else new_dir
    )


def peek_state_dir() -> str:
    """Return the canonical state-dir path without creating directories."""
    return _resolve_state_dir_path()


def get_state_dir() -> str:
    """
    Get the canonical state directory for all writable data.

    Priority:
    1. OPENCLAW_STATE_DIR environment variable (explicit override)
    2. (legacy) MOLTBOT_STATE_DIR environment variable (explicit override)
    2. Platform-appropriate user data directory

    The directory is created if it doesn't exist.

    Returns:
        Absolute path to state directory.
    """
    state_dir = _resolve_state_dir_path()

    # Ensure directory exists with appropriate permissions
    if not os.path.exists(state_dir):
        try:
            os.makedirs(state_dir, mode=0o700, exist_ok=True)
            logger.info(f"Created state directory: {state_dir}")
        except OSError as e:
            logger.error(f"Failed to create state directory: {e}")
            # Fallback to temp directory
            import tempfile

            state_dir = os.path.join(tempfile.gettempdir(), STATE_DIR_NAME)
            os.makedirs(state_dir, mode=0o700, exist_ok=True)
            logger.warning(f"Using fallback state directory: {state_dir}")

    return state_dir


def peek_log_path() -> str:
    """Return the canonical log-file path without creating directories."""
    state_dir = peek_state_dir()
    new_path = os.path.join(state_dir, "openclaw.log")
    legacy_path = os.path.join(state_dir, "moltbot.log")
    return (
        legacy_path
        if (os.path.exists(legacy_path) and not os.path.exists(new_path))
        else new_path
    )


def get_log_path() -> str:
    """Get the path for the log file."""
    state_dir = get_state_dir()
    new_path = os.path.join(state_dir, "openclaw.log")
    legacy_path = os.path.join(state_dir, "moltbot.log")
    return (
        legacy_path
        if (os.path.exists(legacy_path) and not os.path.exists(new_path))
        else new_path
    )


def get_cache_dir() -> str:
    """Get the path for cache files."""
    cache_dir = os.path.join(get_state_dir(), "cache")
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    return cache_dir


def get_db_path() -> str:
    """Get the path for the persistence database (future use)."""
    state_dir = get_state_dir()
    new_path = os.path.join(state_dir, "openclaw.db")
    legacy_path = os.path.join(state_dir, "moltbot.db")
    return (
        legacy_path
        if (os.path.exists(legacy_path) and not os.path.exists(new_path))
        else new_path
    )


# Module-level convenience for backwards compatibility
STATE_DIR = None


def ensure_state_dir() -> str:
    """
    Ensure state directory exists and return its path.
    Caches the result for performance.
    """
    global STATE_DIR
    if STATE_DIR is None:
        STATE_DIR = get_state_dir()
    return STATE_DIR
