"""
OpenClaw Path Configuration (legacy-compatible).
"""

from pathlib import Path

from .env_aliases import get_env_value

# Base state directory (repo-local)
# Users can override via OPENCLAW_STATE_DIR (legacy: MOLTBOT_STATE_DIR).
_env_dir = get_env_value("OPENCLAW_STATE_DIR")
_default_new = Path("openclaw_state").resolve()
_default_legacy = Path("moltbot_state").resolve()

STATE_DIR = (
    Path(_env_dir).resolve()
    if _env_dir
    else (
        _default_legacy
        if (_default_legacy.exists() and not _default_new.exists())
        else _default_new
    )
)

# Legacy alias (do not remove: may be referenced externally)
MOLTBOT_STATE_DIR = STATE_DIR


def get_state_dir() -> Path:
    """Ensure state dir exists and return path."""
    if not STATE_DIR.exists():
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fallback for permission issues? Or just fail.
            # Fail closed.
            pass
    return STATE_DIR


def get_presets_dir() -> Path:
    """Get Presets directory."""
    path = get_state_dir() / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path
