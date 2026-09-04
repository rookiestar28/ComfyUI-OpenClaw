"""Concrete filesystem path validation for connector-owned local state."""

from pathlib import PurePath
from typing import TypeAlias

PathInput: TypeAlias = str | PurePath


def require_concrete_path(value: object, *, field_name: str) -> PathInput:
    """Return a supported path value without invoking arbitrary path protocols."""
    # CRITICAL: validate before Path/os.path/open. Mock-like objects implement
    # __fspath__/__index__ and can create workspace trees or close process descriptors.
    if not isinstance(value, (str, PurePath)):
        raise TypeError(f"{field_name} must be a string or pathlib.PurePath")
    return value
