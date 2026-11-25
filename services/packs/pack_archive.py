import json
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from typing import Optional

from .pack_manifest import (
    MAX_FILE_SIZE_MB,
    MAX_MANIFEST_FILES,
    PackError,
    validate_manifest_integrity,
    validate_pack_metadata,
)
from .pack_types import PackMetadata

MAX_COMPRESSION_RATIO = 100

# The two dynamic-execution calls the code-safety scan rejects, in the order they are
# reported when one member holds both. Each name is matched as a plain byte search
# followed by an opening parenthesis, whitespace allowed.
#
# GUARD: do not write either call out as literal call syntax anywhere in this module,
# and do not add a word boundary to the match.
# - Source scanners (the Comfy Registry's among them) read literal call syntax in
#   shipped code as dynamic execution, so a deny-list spelled as the calls it denies
#   gets this file flagged for the very code it blocks. The patterns are therefore
#   built from the bare names.
# - The match must stay a superset of a plain byte search for the call: longer names
#   (a prefix before the name) and attribute calls on a model object were rejected
#   before and must stay rejected, and whitespace before the parenthesis must not
#   slip past. A lookbehind or boundary assertion would quietly let those through.
_BLOCKED_CALLS = (
    (b"exec", "Dynamic execution (exec)"),
    (b"eval", "Dynamic execution (eval)"),
)
_BLOCKED_CALL_PATTERNS = tuple(
    (re.compile(re.escape(name) + rb"\s*\("), message)
    for name, message in _BLOCKED_CALLS
)


class PackArchive:
    @staticmethod
    def _is_safe_path(base_dir: str, rel_path: str) -> bool:
        # Prevent path traversal
        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(os.path.join(base_dir, rel_path))
        return abs_target.startswith(abs_base)

    # extract_pack removed (superseded by S39-aware version below)

    @staticmethod
    def create_pack_archive(source_dir: str, output_zip: str):
        """
        Creates a zip archive from source_dir.
        Does NOT re-generate manifest (assumes it exists and is correct).
        """
        if not os.path.exists(source_dir):
            raise PackError("Source directory does not exist")

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            files_to_add = []
            for root, _, files in os.walk(source_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_dir)
                    files_to_add.append((full_path, rel_path))

            # Deterministic order
            files_to_add.sort(key=lambda x: x[1])

            for full_path, rel_path in files_to_add:
                # Deterministic metadata (timestamp)
                # ZipInfo requires a tuple (year, month, day, hour, min, sec)
                # We use a fixed epoch for reproducibility, or file mtime?
                # Plan says "regenerate manifest deterministically".
                # If we use file mtime, it changes if we touch files.
                # Using fixed timestamp ensures identical binary hash for identical content.
                # But standard zip tools use mtime.
                # Let's use 1980-01-01 00:00:00 (DOS epoch)
                zinfo = zipfile.ZipInfo(rel_path)
                zinfo.date_time = (1980, 1, 1, 0, 0, 0)
                zinfo.compress_type = zipfile.ZIP_DEFLATED

                # Set regular file permissions (0o644)
                # External attr: (0o100644 << 16) = 0x81A40000
                zinfo.external_attr = 0x81A40000

                with open(full_path, "rb") as f:
                    zf.writestr(zinfo, f.read())

    @staticmethod
    def _check_code_safety(zf: zipfile.ZipFile) -> None:
        """
        S39: Static analysis for dangerous patterns in code files.
        Raises PackError if violations found.

        Every ``.py`` member is searched as bytes, so a call is also found inside a
        string or a comment; that is accepted for a strict preflight. Members are
        checked in archive order and the blocked calls in table order, so a member
        that holds both is reported for the first call in the table.
        """
        for info in zf.infolist():
            if not info.filename.endswith(".py"):
                continue
            with zf.open(info) as f:
                content = f.read()
            for pattern, message in _BLOCKED_CALL_PATTERNS:
                if pattern.search(content):
                    raise PackError(
                        f"Code safety violation in {info.filename}: {message}"
                    )

    @staticmethod
    def extract_pack(zip_path: str, target_dir: str) -> PackMetadata:
        """
        Safely extracts a pack archive to target_dir.
        1. Checks limits (count/size).
        2. checks for symlinks/unsafe paths.
        3. Checks code safety (S39).
        4. Extracts.
        5. Validates manifest.json (integrity).
        6. Validates pack.json (schema).
        Returns the parsed PackMetadata.
        """
        if not os.path.exists(zip_path):
            raise PackError("Archive not found")

        with zipfile.ZipFile(zip_path, "r") as zf:
            # 1. Pre-flight Check
            infos = zf.infolist()
            if len(infos) > MAX_MANIFEST_FILES:
                raise PackError(
                    f"Too many files in archive ({len(infos)} > {MAX_MANIFEST_FILES})"
                )

            total_size = sum(i.file_size for i in infos)
            if total_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise PackError(f"Archive content too large ({total_size} bytes)")

            # Check compression ratio
            compressed_size = sum(i.compress_size for i in infos)
            if compressed_size > 0:
                ratio = total_size / compressed_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise PackError(
                        f"Compression ratio too high ({ratio:.1f} > {MAX_COMPRESSION_RATIO})"
                    )

            # 2. Safety Check
            for info in infos:
                # S53: Unicode normalization to prevent homoglyph attacks (e.g. fullwidth dots)
                # Normalize to NFKC to catch compatibility characters like '．．' -> '..'
                norm_name = unicodedata.normalize("NFKC", info.filename)

                if (
                    norm_name.startswith("/")
                    or ".." in norm_name
                    or "\\" in norm_name
                    or ":" in norm_name  # Block drive-relative paths (C:foo)
                    or any(c < " " for c in norm_name)  # Control chars
                ):
                    raise PackError(
                        f"Unsafe filename: {info.filename} (normalized: {norm_name})"
                    )

                # Check for symlinks (S_IFLNK - 0xA000)
                # ZipInfo.external_attr: upper 16 bits are Unix permissions
                attr = info.external_attr >> 16
                if (attr & 0xF000) == 0xA000:
                    raise PackError(f"Symlinks not allowed: {info.filename}")

            # 3. Code Safety Check (S39)
            PackArchive._check_code_safety(zf)

            # 4. Extract to temp dir first
            with tempfile.TemporaryDirectory() as tmp_dir:
                zf.extractall(tmp_dir)

                # ... rest of validation ...
                manifest_path = os.path.join(tmp_dir, "manifest.json")
                pack_json_path = os.path.join(tmp_dir, "pack.json")

                if not os.path.exists(manifest_path):
                    raise PackError("Missing manifest.json")
                if not os.path.exists(pack_json_path):
                    raise PackError("Missing pack.json")

                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    with open(pack_json_path, "r", encoding="utf-8") as f:
                        pack_meta = json.load(f)
                except json.JSONDecodeError:
                    raise PackError("Invalid JSON in manifest or pack definition")

                # Validate Metadata Schema
                validate_pack_metadata(pack_meta)

                # Validate Integrity
                integrity_errors = validate_manifest_integrity(tmp_dir, manifest)
                if integrity_errors:
                    raise PackError(
                        f"Integrity check failed: {'; '.join(integrity_errors)}"
                    )

                # Safe to move to target
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                os.makedirs(target_dir, exist_ok=True)

                shutil.copytree(tmp_dir, target_dir, dirs_exist_ok=True)

                return pack_meta  # type: ignore
