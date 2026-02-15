import os
import re
import shutil
from typing import Dict, List, Optional

from ..safe_io import resolve_under_root
from .pack_archive import PackArchive, PackError
from .pack_types import PackMetadata

# Strict pattern for pack name and version path segments.
# Only allows alphanumerics, hyphens, underscores, and dots.
_SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _validate_pack_segment(value: str, label: str) -> None:
    """Validate a pack name or version segment against path traversal."""
    if not value:
        raise PackError(f"Pack {label} must not be empty")
    if not _SAFE_SEGMENT_RE.match(value):
        raise PackError(
            f"Pack {label} contains invalid characters: {value!r}. "
            f"Only alphanumerics, hyphens, underscores, and dots are allowed."
        )
    if value in (".", ".."):
        raise PackError(f"Pack {label} must not be '.' or '..'")


class PackRegistry:
    def __init__(self, state_dir: str):
        self.packs_dir = os.path.join(state_dir, "packs", "installed")
        os.makedirs(self.packs_dir, exist_ok=True)

    def install_pack(self, zip_path: str, overwrite: bool = False) -> PackMetadata:
        """
        Installs a pack from a zip file.
        1. safe extract to temp
        2. read metadata
        3. move to packs_dir/name/version
        """
        # We'll let extract_pack handle the extraction to a specific target
        # But we need metadata first to know WHERE to put it.
        # So we extract to a temp location first, which PackArchive already does internally?
        # No, PackArchive.extract_pack extracts to a *given* target.
        # We should extract to a temp staging area first.

        import tempfile

        with tempfile.TemporaryDirectory() as stage_dir:
            # Extract to staging to get metadata and validate
            meta = PackArchive.extract_pack(zip_path, stage_dir)

            name = meta["name"]
            version = meta["version"]

            # Validate name/version from zip metadata against path traversal.
            # A malicious pack.json could contain traversal sequences.
            _validate_pack_segment(name, "name")
            _validate_pack_segment(version, "version")
            target_dir = resolve_under_root(
                self.packs_dir, os.path.join(name, version)
            )

            if os.path.exists(target_dir):
                if not overwrite:
                    raise PackError(
                        f"Pack {name} v{version} already installed. Use overwrite=True."
                    )
                shutil.rmtree(target_dir)

            # Now move/copy from stage to target
            shutil.copytree(stage_dir, target_dir)

            return meta

    def uninstall_pack(self, name: str, version: str) -> bool:
        target_dir = os.path.join(self.packs_dir, name, version)
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
            # Clean up parent if empty
            parent = os.path.dirname(target_dir)
            if not os.listdir(parent):
                os.rmdir(parent)
            return True
        return False

    def list_packs(self) -> List[PackMetadata]:
        results = []
        if not os.path.exists(self.packs_dir):
            return []

        # Structure: packs_dir/name/version/pack.json
        for name in os.listdir(self.packs_dir):
            name_path = os.path.join(self.packs_dir, name)
            if not os.path.isdir(name_path):
                continue

            for version in os.listdir(name_path):
                ver_path = os.path.join(name_path, version)
                json_path = os.path.join(ver_path, "pack.json")

                if os.path.isfile(json_path):
                    try:
                        import json

                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            # Basic schema check could go here,/ but we assume installed packs are valid
                            results.append(data)
                    except Exception:
                        pass  # internal corruption or partial install
        return results

    def get_pack_path(self, name: str, version: str) -> Optional[str]:
        target_dir = os.path.join(self.packs_dir, name, version)
        if os.path.exists(target_dir):
            return target_dir
        return None
