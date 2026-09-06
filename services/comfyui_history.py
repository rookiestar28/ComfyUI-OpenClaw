"""
ComfyUI History Service (F17).
Parses ComfyUI /history/{prompt_id} responses and extracts image output metadata.
"""

import json
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from .env_aliases import get_env_value

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:
    urlopen = None  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.comfyui_history")

COMFYUI_URL = get_env_value("OPENCLAW_COMFYUI_URL") or "http://127.0.0.1:8188"
HISTORY_TIMEOUT = 5
PREVIEWABLE_MEDIA_TYPES = ("images", "video", "audio", "3d", "text")
THREE_D_EXTENSIONS = (
    ".obj",
    ".fbx",
    ".gltf",
    ".glb",
    ".stl",
    ".ply",
    ".spz",
    ".splat",
    ".ksplat",
    ".usdz",
)
ADVANCED_3D_RESULT_MAX_ENTRIES = 8
ADVANCED_3D_RESULT_PATH_MAX_LENGTH = 1024
TEXT_PREVIEW_MAX_LENGTH = 1024
FILE_TEXT_EXTENSIONS = frozenset(
    {"txt", "md", "markdown", "json", "csv", "yaml", "yml", "xml", "log"}
)
FILE_OUTPUT_MAX_REFS = 64
FILE_OUTPUT_FIELD_MAX_LENGTH = 1024
# The ComfyUI host directory vocabulary (`folder_paths` / `IO.FolderType`).
# One definition serves file-text refs and the Advanced 3D annotation so the
# two consumers cannot drift apart.
HOST_DIRECTORY_TYPES = frozenset({"input", "output", "temp"})
ADVANCED_3D_ANNOTATION_RE = re.compile(
    r" \[(" + "|".join(sorted(HOST_DIRECTORY_TYPES)) + r")\]\Z"
)
ADVANCED_3D_RESULT_WIRE_MAX_LENGTH = ADVANCED_3D_RESULT_PATH_MAX_LENGTH + max(
    len(f" [{directory_type}]") for directory_type in HOST_DIRECTORY_TYPES
)


def _pick_string(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick_asset_hash(image_ref: Dict[str, Any]) -> str:
    asset_hash = _pick_string(image_ref, "asset_hash", "hash")
    if asset_hash:
        return asset_hash

    nested = image_ref.get("asset")
    if isinstance(nested, dict):
        return _pick_string(nested, "asset_hash", "hash")
    return ""


def _pick_asset_api_id(image_ref: Dict[str, Any]) -> str:
    asset_api_id = _pick_string(image_ref, "asset_id")
    if asset_api_id:
        return asset_api_id

    nested = image_ref.get("asset")
    if isinstance(nested, dict):
        return _pick_string(nested, "asset_id", "id")
    return ""


def _has_3d_extension(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in THREE_D_EXTENSIONS)


def _normalize_text_content(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    text = str(value)
    if text == "":
        return None
    truncated = len(text) > TEXT_PREVIEW_MAX_LENGTH
    if truncated:
        text = text[:TEXT_PREVIEW_MAX_LENGTH]
    return {
        "filename": "",
        "subfolder": "",
        "type": "output",
        "media_type": "text",
        "asset_hash": "",
        "asset_api_id": "",
        "asset_api_required": False,
        "resolution": "inline_text",
        "view_url": "",
        "content": text,
        "text_truncated": truncated,
    }


def _has_unsafe_file_characters(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_unsafe_advanced_3d_characters(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


def _split_advanced_3d_annotation(raw_path: str) -> tuple[str, str]:
    """Separate the canonical ComfyUI trailing directory annotation.

    HOTSPOT: `PreviewUI3DAdvanced` reports `<path> [input|output|temp]`, so the
    annotation must be separated *before* the 3D extension is validated. Checking
    the extension first sees a name ending in `]` and drops every current ComfyUI
    3D preview. Everything after this split - length, character, traversal,
    segment and extension checks - applies to the canonical path only, and the
    returned type is always one of the `HOST_DIRECTORY_TYPES` literals, never
    attacker-supplied text.

    Requiring the single ASCII separator is deliberately stricter than the host's
    `folder_paths.annotated_filepath()`, which also accepts `scene.glb[output]`
    and then truncates a real path character. Do not relax the marker to match it.
    """
    match = ADVANCED_3D_ANNOTATION_RE.search(raw_path)
    if not match:
        return raw_path, "output"
    return raw_path[: match.start()], match.group(1)


def _normalize_advanced_3d_result(result: Any) -> dict[str, Any] | None:
    if (
        not isinstance(result, list)
        or not result
        or len(result) > ADVANCED_3D_RESULT_MAX_ENTRIES
    ):
        return None

    raw_path = result[0]
    if (
        not isinstance(raw_path, str)
        or len(raw_path) > ADVANCED_3D_RESULT_WIRE_MAX_LENGTH
    ):
        return None

    # HOTSPOT: see _split_advanced_3d_annotation. The wire bound above admits
    # the longest annotation; the canonical bound below still guards the path.
    canonical_path, directory_type = _split_advanced_3d_annotation(raw_path)

    normalized_path = canonical_path.replace("\\", "/")
    if (
        not normalized_path
        or len(normalized_path) > ADVANCED_3D_RESULT_PATH_MAX_LENGTH
        or _has_unsafe_advanced_3d_characters(normalized_path)
        or normalized_path.startswith("/")
        or any(marker in normalized_path for marker in (":", "%", "?", "#"))
    ):
        return None

    segments = normalized_path.split("/")
    if any(
        not segment or segment in {".", ".."} or segment != segment.strip()
        for segment in segments
    ):
        return None

    filename = segments[-1]
    if not _has_3d_extension(filename):
        return None

    # SECURITY: result metadata is untrusted and may contain private host state.
    # Inspect only the validated path at index zero; never project later entries.
    return normalize_history_output_ref(
        {
            "filename": filename,
            "subfolder": "/".join(segments[:-1]),
            "type": directory_type,
        },
        "3d",
    )


def _normalize_file_text_ref(output_ref: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(output_ref, dict):
        return None

    raw_filename = output_ref.get("filename")
    if not isinstance(raw_filename, str):
        return None
    if len(raw_filename) > FILE_OUTPUT_FIELD_MAX_LENGTH:
        return None
    filename = raw_filename.strip()
    if (
        not filename
        or len(filename) > FILE_OUTPUT_FIELD_MAX_LENGTH
        or _has_unsafe_file_characters(filename)
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
    ):
        return None

    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in FILE_TEXT_EXTENSIONS:
        return None

    raw_subfolder = output_ref.get("subfolder", "")
    if not isinstance(raw_subfolder, str):
        return None
    if len(raw_subfolder) > FILE_OUTPUT_FIELD_MAX_LENGTH:
        return None
    subfolder = raw_subfolder.strip()
    if (
        len(subfolder) > FILE_OUTPUT_FIELD_MAX_LENGTH
        or _has_unsafe_file_characters(subfolder)
        or "\\" in subfolder
        or subfolder.startswith("/")
        or any(part in {".", ".."} for part in subfolder.split("/") if part)
    ):
        return None

    raw_type = output_ref.get("type", "output")
    if not isinstance(raw_type, str):
        return None
    output_type = raw_type.strip() or "output"
    if output_type not in HOST_DIRECTORY_TYPES:
        return None

    # SECURITY: file-backed text refs are attacker-influenced. Build only the
    # existing encoded /view contract from validated fields; never trust raw URLs.
    params = {"filename": filename, "type": output_type}
    if subfolder:
        params["subfolder"] = subfolder

    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": output_type,
        "media_type": "text",
        "asset_hash": "",
        "asset_api_id": "",
        "asset_api_required": False,
        "resolution": "view",
        "view_url": f"{COMFYUI_URL}/view?{urlencode(params)}",
        "content": "",
        "text_truncated": False,
    }


def normalize_history_output_ref(
    output_ref: Any, media_type: str = "images"
) -> Optional[Dict[str, Any]]:
    resolved_media_type = (
        media_type if media_type in PREVIEWABLE_MEDIA_TYPES else "images"
    )

    if not isinstance(output_ref, dict):
        if resolved_media_type == "text":
            return _normalize_text_content(output_ref)
        if (
            resolved_media_type == "3d"
            and isinstance(output_ref, str)
            and _has_3d_extension(output_ref)
        ):
            output_ref = {"filename": output_ref, "type": "output", "subfolder": ""}
        else:
            return None

    declared_media_type = _pick_string(output_ref, "media_type", "mediaType")
    if declared_media_type in PREVIEWABLE_MEDIA_TYPES:
        resolved_media_type = declared_media_type

    text_content = _pick_string(output_ref, "content", "text")
    if resolved_media_type == "text" and text_content:
        text_ref = _normalize_text_content(text_content)
        if text_ref:
            return text_ref

    asset_hash = _pick_asset_hash(output_ref)
    asset_api_id = _pick_asset_api_id(output_ref)
    named_filename = _pick_string(output_ref, "filename", "name")
    filename = named_filename or asset_hash or asset_api_id
    subfolder = _pick_string(output_ref, "subfolder")
    img_type = _pick_string(output_ref, "type") or "output"

    if not filename:
        return None

    asset_api_required = bool(asset_api_id and not asset_hash and not named_filename)
    view_url = ""
    resolution = "asset_api_required" if asset_api_required else "view"

    if not asset_api_required:
        # IMPORTANT: keep OpenClaw on the bounded /view contract. Optional
        # asset-hash metadata still resolves through /view when hosts provide it;
        # do not escalate asset-api-only identifiers into implicit /api/assets
        # runtime fetches.
        if asset_hash:
            params = {"filename": asset_hash}
        else:
            params = {"filename": filename, "type": img_type}
            if subfolder:
                params["subfolder"] = subfolder
        view_url = f"{COMFYUI_URL}/view?{urlencode(params)}"

    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": img_type,
        "media_type": resolved_media_type,
        "asset_hash": asset_hash,
        "asset_api_id": asset_api_id,
        "asset_api_required": asset_api_required,
        "resolution": resolution,
        "view_url": view_url,
        "content": "",
        "text_truncated": False,
    }


def normalize_history_image_ref(image_ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return normalize_history_output_ref(image_ref, "images")


def fetch_history(prompt_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch history for a given prompt_id from ComfyUI.
    Returns the history item dict if found, else None.
    """
    url = f"{COMFYUI_URL}/history/{prompt_id}"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=HISTORY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get(prompt_id)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as e:
        logger.warning(f"Failed to fetch history for {prompt_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching history: {e}")
        return None


def extract_output_refs(history_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract previewable media outputs from a history item."""
    results = []
    outputs = history_item.get("outputs", {})

    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for media_type in PREVIEWABLE_MEDIA_TYPES:
            refs = node_output.get(media_type, [])
            if not isinstance(refs, list):
                continue
            for ref in refs:
                normalized = normalize_history_output_ref(ref, media_type)
                if normalized:
                    results.append(normalized)

        advanced_3d_ref = _normalize_advanced_3d_result(node_output.get("result"))
        if advanced_3d_ref:
            results.append(advanced_3d_ref)

        file_refs = node_output.get("files")
        if isinstance(file_refs, list) and len(file_refs) <= FILE_OUTPUT_MAX_REFS:
            for ref in file_refs:
                normalized = _normalize_file_text_ref(ref)
                if normalized:
                    results.append(normalized)

    return results


def extract_images(history_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract image outputs from a history item.
    Returns list of normalized image refs.
    """
    return [
        ref
        for ref in extract_output_refs(history_item)
        if ref.get("media_type") == "images"
    ]


def get_job_status(history_item: Optional[Dict[str, Any]]) -> str:
    """
    Determine job status from history item.
    Returns: 'pending', 'running', 'completed', 'error', 'unknown'.
    """
    if history_item is None:
        return "pending"  # Not yet in history

    status = history_item.get("status", {})
    status_str = status.get("status_str", "")

    if status_str == "success":
        return "completed"
    elif status_str == "error":
        return "error"
    elif history_item.get("outputs"):
        return "completed"

    return "unknown"
