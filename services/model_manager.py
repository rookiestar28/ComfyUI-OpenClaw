"""
F54/F65 model search/download/import service.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .env_aliases import get_env_value
from .job_events import JobEventType, get_job_event_store
from .model_manager_catalog import (
    collect_catalog_entries as _collect_catalog_entries_impl,
)
from .model_manager_catalog import (
    collect_install_entries as _collect_install_entries_impl,
)
from .model_manager_catalog import list_installations as _list_installations_impl
from .model_manager_catalog import load_installations as _load_installations_impl
from .model_manager_catalog import save_installations as _save_installations_impl
from .model_manager_catalog import search_models as _search_models_impl
from .model_manager_tasks import cancel_download_task as _cancel_download_task_impl
from .model_manager_tasks import (
    checkpoint_matches_task as _checkpoint_matches_task_impl,
)
from .model_manager_tasks import checkpoint_path as _checkpoint_path_impl
from .model_manager_tasks import emit as _emit_impl
from .model_manager_tasks import get_download_task as _get_download_task_impl
from .model_manager_tasks import list_download_tasks as _list_download_tasks_impl
from .model_manager_tasks import load_checkpoint as _load_checkpoint_impl
from .model_manager_tasks import load_tasks_from_disk as _load_tasks_from_disk_impl
from .model_manager_tasks import persist_tasks_locked as _persist_tasks_locked_impl
from .model_manager_tasks import progress as _progress_impl
from .model_manager_tasks import (
    recover_incomplete_tasks as _recover_incomplete_tasks_impl,
)
from .model_manager_tasks import save_checkpoint as _save_checkpoint_impl
from .model_manager_tasks import set_resume_status as _set_resume_status_impl
from .model_manager_tasks import tenant_ok as _tenant_ok_impl
from .model_manager_tasks import validators_match as _validators_match_impl
from .model_manager_transfer import assert_budget as _assert_budget_impl
from .model_manager_transfer import create_download_task as _create_download_task_impl
from .model_manager_transfer import download as _download_impl
from .model_manager_transfer import (
    import_downloaded_model as _import_downloaded_model_impl,
)
from .model_manager_transfer import normalize_tenant as _normalize_tenant_impl
from .model_manager_transfer import run_task as _run_task_impl
from .model_manager_transfer import (
    stream_response_to_part as _stream_response_to_part_impl,
)
from .model_manager_transfer import validate_provenance as _validate_provenance_impl
from .model_manager_transfer import validate_url_policy as _validate_url_policy_impl
from .safe_io import (
    STANDARD_OUTBOUND_POLICY,
    SSRFError,
    _build_pinned_opener,
    resolve_under_root,
    validate_outbound_url,
)
from .state_dir import get_state_dir
from .tenant_context import (
    DEFAULT_TENANT_ID,
    is_multi_tenant_enabled,
    normalize_tenant_id,
)

logger = logging.getLogger("ComfyUI-OpenClaw.services.model_manager")

STATE_SUBDIR = "model_manager"
CATALOG_SUBDIR = "catalog"
STAGING_SUBDIR = "staging"
INSTALLATIONS_FILE = "installations.json"
TASKS_FILE = "download_tasks.json"
CHECKPOINT_VERSION = 1
CHECKPOINT_SUFFIX = ".checkpoint.json"
DEFAULT_MODEL_TYPE = "checkpoint"
MODEL_TYPE_TO_SUBDIR = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "embedding": "embeddings",
    "embeddings": "embeddings",
    "text_encoders": "text_encoders",
    "diffusion_models": "diffusion_models",
    "clip_vision": "clip_vision",
    "style_models": "style_models",
    "upscale_models": "upscale_models",
    "vae_approx": "vae_approx",
    "gligen": "gligen",
    "latent_upscale_models": "latent_upscale_models",
    "hypernetworks": "hypernetworks",
    "photomaker": "photomaker",
    "model_patches": "model_patches",
    "audio_encoders": "audio_encoders",
    "background_removal": "background_removal",
    "frame_interpolation": "frame_interpolation",
    "geometry_estimation": "geometry_estimation",
    "optical_flow": "optical_flow",
    "detection": "detection",
}
MODEL_TYPE_EXCLUSION_REASONS = {
    "configs": "configuration YAML is not a managed model-weight destination",
    "diffusers": "diffusers is folder-valued and needs a directory-tree install design",
    "classifiers": "classifiers are extensionless and need a dedicated content policy",
    "custom_nodes": "custom_nodes are executable plugin code, not managed model files",
    "datasets": "datasets contain user-managed training data, not managed model weights",
}
MODEL_TYPE_ALIASES = {
    "config": "configs",
    "ckpt": "checkpoint",
    "checkpoints": "checkpoint",
    "loras": "lora",
    "controlnets": "controlnet",
    "control_net": "controlnet",
    "diffuser": "diffusers",
    "clip": "text_encoders",
    "text_encoder": "text_encoders",
    "unet": "diffusion_models",
    "diffusion_model": "diffusion_models",
    "upscale_model": "upscale_models",
    "latent_upscale_model": "latent_upscale_models",
    "hypernetwork": "hypernetworks",
    "photo_maker": "photomaker",
    "model_patch": "model_patches",
    "classifier": "classifiers",
    "audio_encoder": "audio_encoders",
    "geometry": "geometry_estimation",
    "detector": "detection",
}
ALLOWED_MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx"}


class ModelManagerError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class DownloadTask:
    task_id: str
    model_id: str
    name: str
    model_type: str
    source: str
    source_label: str
    download_url: str
    destination_subdir: str
    filename: str
    expected_sha256: str
    provenance: Dict[str, Any]
    tenant_id: str
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    bytes_downloaded: int = 0
    total_bytes: int = 0
    progress: float = 0.0
    cancel_requested: bool = False
    error: str = ""
    staged_path: str = ""
    computed_sha256: str = ""
    imported: bool = False
    installation_path: str = ""
    installation_record_id: str = ""
    resume_status: str = "not_started"
    recovery_attempts: int = 0
    last_checkpoint_at: float = 0.0
    change_seq: int = 0

    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "name": self.name,
            "model_type": self.model_type,
            "source": self.source,
            "source_label": self.source_label,
            "download_url": self.download_url,
            "destination_subdir": self.destination_subdir,
            "filename": self.filename,
            "expected_sha256": self.expected_sha256,
            "provenance": dict(self.provenance),
            "tenant_id": self.tenant_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "bytes_downloaded": self.bytes_downloaded,
            "total_bytes": self.total_bytes,
            "progress": self.progress,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
            "staged_path": self.staged_path,
            "computed_sha256": self.computed_sha256,
            "imported": self.imported,
            "installation_path": self.installation_path,
            "installation_record_id": self.installation_record_id,
            "resume_status": self.resume_status,
            "recovery_attempts": self.recovery_attempts,
            "last_checkpoint_at": self.last_checkpoint_at,
            "change_seq": self.change_seq,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DownloadTask":
        if not isinstance(payload, dict):
            raise ValueError("task payload must be an object")
        return cls(
            task_id=str(payload.get("task_id") or ""),
            model_id=str(payload.get("model_id") or ""),
            name=str(payload.get("name") or ""),
            model_type=_norm_model_type(str(payload.get("model_type") or "")),
            source=_norm_source(str(payload.get("source") or "")),
            source_label=str(payload.get("source_label") or ""),
            download_url=str(payload.get("download_url") or ""),
            destination_subdir=str(payload.get("destination_subdir") or ""),
            filename=str(payload.get("filename") or ""),
            expected_sha256=str(payload.get("expected_sha256") or ""),
            provenance=dict(payload.get("provenance") or {}),
            tenant_id=str(payload.get("tenant_id") or DEFAULT_TENANT_ID),
            state=str(payload.get("state") or "queued"),
            created_at=float(payload.get("created_at") or time.time()),
            updated_at=float(payload.get("updated_at") or time.time()),
            started_at=float(payload.get("started_at") or 0.0),
            finished_at=float(payload.get("finished_at") or 0.0),
            bytes_downloaded=max(0, int(payload.get("bytes_downloaded") or 0)),
            total_bytes=max(0, int(payload.get("total_bytes") or 0)),
            progress=max(0.0, min(1.0, float(payload.get("progress") or 0.0))),
            cancel_requested=bool(payload.get("cancel_requested")),
            error=str(payload.get("error") or ""),
            staged_path=str(payload.get("staged_path") or ""),
            computed_sha256=str(payload.get("computed_sha256") or ""),
            imported=bool(payload.get("imported")),
            installation_path=str(payload.get("installation_path") or ""),
            installation_record_id=str(payload.get("installation_record_id") or ""),
            resume_status=str(payload.get("resume_status") or "not_started"),
            recovery_attempts=max(0, int(payload.get("recovery_attempts") or 0)),
            last_checkpoint_at=float(payload.get("last_checkpoint_at") or 0.0),
            change_seq=max(0, int(payload.get("change_seq") or 0)),
        )


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_model_type_token(model_type: str) -> str:
    text = str(model_type or "").strip().lower()
    if not text:
        return ""
    return MODEL_TYPE_ALIASES.get(text, text)


def _model_type_exclusion_reason(model_type: str) -> str:
    return MODEL_TYPE_EXCLUSION_REASONS.get(_resolve_model_type_token(model_type), "")


def _norm_model_type(model_type: str) -> str:
    text = _resolve_model_type_token(model_type)
    if not text:
        return DEFAULT_MODEL_TYPE
    return text if text in MODEL_TYPE_TO_SUBDIR else "other"


def _norm_source(source: str) -> str:
    out = "".join(
        ch
        for ch in str(source or "unknown").strip().lower()
        if ch.isalnum() or ch in {"_", "-", "."}
    )
    return out[:48] or "unknown"


def _parse_hosts(raw: str) -> set[str]:
    out: set[str] = set()
    for item in str(raw or "").replace(";", ",").split(","):
        host = item.strip().lower().rstrip(".")
        if host:
            out.add(host)
    return out


def _is_sha256(value: str) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _sanitize_subdir(text: str) -> str:
    raw_parts = str(text or "").replace("\\", "/").split("/")
    if any(part in {".", ".."} for part in raw_parts if part):
        # CRITICAL: reject traversal markers instead of stripping them; silent cleanup weakens import-boundary guarantees.
        raise ModelManagerError(
            "invalid_destination",
            "destination_subdir must not contain traversal segments",
        )
    parts = [p for p in raw_parts if p]
    if not parts:
        raise ModelManagerError("invalid_destination", "destination_subdir is required")
    cleaned = []
    for part in parts:
        token = "".join(ch for ch in part if ch.isalnum() or ch in {"_", "-", "."})
        if not token:
            raise ModelManagerError(
                "invalid_destination", f"invalid destination segment: {part!r}"
            )
        cleaned.append(token)
    return "/".join(cleaned)


def _sanitize_filename(text: str) -> str:
    clean = (
        str(text or "").strip().replace("\\", "_").replace("/", "_").replace(" ", "_")
    )
    clean = "".join(
        ch for ch in clean if ch.isalnum() or ch in {"_", "-", ".", "(", ")", "[", "]"}
    )
    if not clean or clean in {".", ".."}:
        raise ModelManagerError("invalid_filename", "filename is invalid")
    ext = Path(clean).suffix.lower()
    if ext not in ALLOWED_MODEL_EXTENSIONS:
        raise ModelManagerError(
            "invalid_filename",
            "filename extension must be one of "
            + ", ".join(sorted(ALLOWED_MODEL_EXTENSIONS)),
        )
    return clean[:180]


def _filename_from_url(url: str) -> str:
    seg = (urlparse(url).path or "").rstrip("/").split("/")[-1]
    if not seg:
        raise ModelManagerError("invalid_filename", "URL has no terminal filename")
    return _sanitize_filename(seg)


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(
        prefix=f"{path.name}.tmp.", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except Exception:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # type: ignore[attr-defined]
    except Exception:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _parse_content_range(value: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if not text.lower().startswith("bytes "):
        return -1, -1, -1
    body = text[6:]
    if "/" not in body or "-" not in body:
        return -1, -1, -1
    range_part, total_part = body.split("/", 1)
    start_part, end_part = range_part.split("-", 1)
    try:
        start = int(start_part.strip())
        end = int(end_part.strip())
        total = int(total_part.strip()) if total_part.strip() != "*" else -1
    except Exception:
        return -1, -1, -1
    if start < 0 or end < start:
        return -1, -1, -1
    if total != -1 and total <= end:
        return -1, -1, -1
    return start, end, total


class ModelManager:
    def __init__(
        self, *, state_root: Optional[Path] = None, install_root: Optional[Path] = None
    ):
        self.state_root = Path(state_root or (Path(get_state_dir()) / STATE_SUBDIR))
        self.catalog_dir = self.state_root / CATALOG_SUBDIR
        self.staging_dir = self.state_root / STAGING_SUBDIR
        self.installations_path = self.state_root / INSTALLATIONS_FILE
        self.tasks_path = self.state_root / TASKS_FILE
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        root_env = (
            get_env_value("OPENCLAW_MODEL_INSTALL_ROOT", default="") or ""
        ).strip()
        # CRITICAL: keep explicit branch order. A compact inline ternary here can
        # accidentally ignore injected test/runtime install_root overrides.
        if install_root is not None:
            resolved_install_root = Path(install_root)
        elif root_env:
            resolved_install_root = Path(os.path.abspath(root_env))
        else:
            resolved_install_root = Path(get_state_dir()) / "models"
        self.install_root = resolved_install_root
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.allow_hosts = _parse_hosts(
            get_env_value("OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS", default="") or ""
        )
        self.allow_any_public = _truthy(
            get_env_value("OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC", default="0")
            or "0"
        )
        self.allow_loopback_hosts = _parse_hosts(
            get_env_value("OPENCLAW_MODEL_DOWNLOAD_ALLOW_LOOPBACK_HOSTS", default="")
            or ""
        )
        self.max_workers = self._read_int(
            (
                "OPENCLAW_MODEL_DOWNLOAD_MAX_CONCURRENCY",
                "MOLTBOT_MODEL_DOWNLOAD_MAX_CONCURRENCY",
            ),
            2,
            1,
            4,
        )
        self.max_active = self._read_int(
            ("OPENCLAW_MODEL_DOWNLOAD_MAX_ACTIVE", "MOLTBOT_MODEL_DOWNLOAD_MAX_ACTIVE"),
            16,
            1,
            128,
        )
        self.timeout_sec = self._read_int(
            (
                "OPENCLAW_MODEL_DOWNLOAD_TIMEOUT_SEC",
                "MOLTBOT_MODEL_DOWNLOAD_TIMEOUT_SEC",
            ),
            120,
            5,
            3600,
        )
        self.recovery_replay_limit = self._read_int(
            (
                "OPENCLAW_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT",
                "MOLTBOT_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT",
            ),
            32,
            0,
            256,
        )
        self._lock = threading.Lock()
        self._tasks: Dict[str, DownloadTask] = {}
        self._futures: Dict[str, Future] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._task_change_seq = 0
        self._last_tasks_persist_at = 0.0
        self._download_task_cls = DownloadTask
        self._download_cancelled_cls = DownloadCancelled
        self._ssrf_error_cls = SSRFError
        self._default_tenant_id = DEFAULT_TENANT_ID
        self._model_type_to_subdir = MODEL_TYPE_TO_SUBDIR
        self._threading_event_factory = threading.Event
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="openclaw-model-download"
        )
        self._load_tasks_from_disk()
        self._recover_incomplete_tasks()

    @staticmethod
    def _read_int(
        keys: tuple[str, ...], default: int, minimum: int, maximum: int
    ) -> int:
        raw = get_env_value(keys[0], aliases=keys[1:])
        if raw is None or str(raw).strip() == "":
            return default
        try:
            val = int(str(raw).strip())
        except Exception:
            return default
        if val < minimum or val > maximum:
            return default
        return val

    @staticmethod
    def _error(code: str, detail: str, status: int = 400) -> ModelManagerError:
        return ModelManagerError(code, detail, status)

    @staticmethod
    def _norm_model_type(value: str) -> str:
        return _norm_model_type(value)

    @staticmethod
    def _model_type_exclusion_reason(value: str) -> str:
        return _model_type_exclusion_reason(value)

    @staticmethod
    def _norm_source(value: str) -> str:
        return _norm_source(value)

    @staticmethod
    def _is_sha256(value: str) -> bool:
        return _is_sha256(value)

    @staticmethod
    def _sanitize_subdir(value: str) -> str:
        return _sanitize_subdir(value)

    @staticmethod
    def _sanitize_filename(value: str) -> str:
        return _sanitize_filename(value)

    @staticmethod
    def _filename_from_url(value: str) -> str:
        return _filename_from_url(value)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return _file_sha256(path)

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        _safe_unlink(path)

    @staticmethod
    def _parse_content_range(value: str) -> tuple[int, int, int]:
        return _parse_content_range(value)

    @staticmethod
    def _is_multi_tenant_enabled() -> bool:
        return is_multi_tenant_enabled()

    @staticmethod
    def _normalize_tenant_id(value: str) -> str:
        return normalize_tenant_id(value)

    @staticmethod
    def _validate_outbound_download_url(
        url: str,
        *,
        allow_hosts: Optional[set[str]],
        allow_any_public_host: bool,
        allow_loopback_hosts: Optional[set[str]],
    ) -> tuple[str, str, int, list[str]]:
        return validate_outbound_url(
            url,
            allow_hosts=allow_hosts,
            allow_any_public_host=allow_any_public_host,
            allow_loopback_hosts=allow_loopback_hosts,
            policy=STANDARD_OUTBOUND_POLICY,
        )

    @staticmethod
    def _build_pinned_download_opener(pinned_ips: list[str]) -> Any:
        return _build_pinned_opener(pinned_ips)

    @staticmethod
    def _resolve_install_target(root: str, rel_target: str) -> str:
        return resolve_under_root(root, rel_target)

    def _persist_tasks_locked(self, *, force: bool = False) -> None:
        _persist_tasks_locked_impl(
            manager=self, force=force, atomic_json_write=_atomic_json_write
        )

    def _load_tasks_from_disk(self) -> None:
        _load_tasks_from_disk_impl(
            manager=self, task_from_dict=DownloadTask.from_dict, logger=logger
        )

    def _recover_incomplete_tasks(self) -> None:
        _recover_incomplete_tasks_impl(manager=self)

    @staticmethod
    def _checkpoint_path(part_path: Path) -> Path:
        return _checkpoint_path_impl(
            part_path=part_path, checkpoint_suffix=CHECKPOINT_SUFFIX
        )

    def _load_checkpoint(self, checkpoint_path: Path) -> Dict[str, Any]:
        return _load_checkpoint_impl(checkpoint_path=checkpoint_path)

    @staticmethod
    def _checkpoint_matches_task(
        task: DownloadTask, checkpoint: Dict[str, Any], partial_bytes: int
    ) -> bool:
        return _checkpoint_matches_task_impl(
            task=task,
            checkpoint=checkpoint,
            partial_bytes=partial_bytes,
            checkpoint_version=CHECKPOINT_VERSION,
        )

    @staticmethod
    def _validators_match(
        checkpoint: Dict[str, Any], response_etag: str, response_last_modified: str
    ) -> bool:
        return _validators_match_impl(
            checkpoint=checkpoint,
            response_etag=response_etag,
            response_last_modified=response_last_modified,
        )

    def _save_checkpoint(
        self,
        checkpoint_path: Path,
        task: DownloadTask,
        *,
        bytes_downloaded: int,
        total_bytes: int,
        etag: str,
        last_modified: str,
    ) -> None:
        _save_checkpoint_impl(
            manager=self,
            checkpoint_path=checkpoint_path,
            task=task,
            bytes_downloaded=bytes_downloaded,
            total_bytes=total_bytes,
            etag=etag,
            last_modified=last_modified,
            checkpoint_version=CHECKPOINT_VERSION,
            atomic_json_write=_atomic_json_write,
        )

    def _set_resume_status(self, task_id: str, status: str) -> None:
        _set_resume_status_impl(manager=self, task_id=task_id, status=status)

    def _tenant_ok(self, record_tenant: str, request_tenant: Optional[str]) -> bool:
        return _tenant_ok_impl(
            record_tenant=record_tenant,
            request_tenant=request_tenant,
            default_tenant_id=DEFAULT_TENANT_ID,
            is_multi_tenant_enabled=is_multi_tenant_enabled,
            normalize_tenant_id=normalize_tenant_id,
        )

    def _emit(self, task: DownloadTask) -> None:
        _emit_impl(
            task=task,
            event_type_cls=JobEventType,
            event_store_getter=get_job_event_store,
        )

    def _bump_task_change_seq_locked(self, task: DownloadTask) -> int:
        self._task_change_seq += 1
        task.change_seq = self._task_change_seq
        return task.change_seq

    def _load_installations(self) -> List[Dict[str, Any]]:
        return _load_installations_impl(installations_path=self.installations_path)

    def _save_installations(self, rows: List[Dict[str, Any]]) -> None:
        _save_installations_impl(
            installations_path=self.installations_path,
            atomic_json_write=_atomic_json_write,
            rows=rows,
        )

    def _collect_install_entries(
        self, tenant_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        return _collect_install_entries_impl(
            manager=self,
            tenant_id=tenant_id,
            default_tenant_id=DEFAULT_TENANT_ID,
            norm_model_type=_norm_model_type,
            norm_source=_norm_source,
        )

    def _collect_catalog_entries(
        self, tenant_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        return _collect_catalog_entries_impl(
            manager=self,
            tenant_id=tenant_id,
            default_tenant_id=DEFAULT_TENANT_ID,
            norm_model_type=_norm_model_type,
            norm_source=_norm_source,
        )

    def search_models(
        self,
        *,
        query: str = "",
        source: str = "",
        model_type: str = "",
        installed: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return _search_models_impl(
            manager=self,
            query=query,
            source=source,
            model_type=model_type,
            installed=installed,
            limit=limit,
            offset=offset,
            tenant_id=tenant_id,
            norm_source=_norm_source,
            norm_model_type=_norm_model_type,
            default_tenant_id=DEFAULT_TENANT_ID,
        )

    def _validate_url_policy(self, url: str) -> None:
        _validate_url_policy_impl(manager=self, url=url)

    def _validate_provenance(self, provenance: Dict[str, Any]) -> Dict[str, Any]:
        return _validate_provenance_impl(manager=self, provenance=provenance)

    def _normalize_tenant(self, tenant_id: Optional[str]) -> str:
        return _normalize_tenant_impl(manager=self, tenant_id=tenant_id)

    def _assert_budget(self) -> None:
        _assert_budget_impl(manager=self)

    def create_download_task(
        self,
        *,
        model_id: str,
        name: str,
        model_type: str,
        source: str,
        source_label: str,
        download_url: str,
        expected_sha256: str,
        provenance: Dict[str, Any],
        destination_subdir: Optional[str] = None,
        filename: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return _create_download_task_impl(
            manager=self,
            model_id=model_id,
            name=name,
            model_type=model_type,
            source=source,
            source_label=source_label,
            download_url=download_url,
            expected_sha256=expected_sha256,
            provenance=provenance,
            destination_subdir=destination_subdir,
            filename=filename,
            tenant_id=tenant_id,
        )

    def _run_task(self, task_id: str) -> None:
        _run_task_impl(manager=self, task_id=task_id)

    def _download(
        self, task: DownloadTask, cancel_event: threading.Event
    ) -> tuple[str, str]:
        return _download_impl(manager=self, task=task, cancel_event=cancel_event)

    def _stream_response_to_part(
        self,
        *,
        opener: Any,
        task: DownloadTask,
        cancel_event: threading.Event,
        part: Path,
        checkpoint: Path,
        digest: "hashlib._Hash",
        resume_from: int,
        checkpoint_data: Dict[str, Any],
    ) -> tuple[int, int, str, str, str]:
        return _stream_response_to_part_impl(
            manager=self,
            opener=opener,
            task=task,
            cancel_event=cancel_event,
            part=part,
            checkpoint=checkpoint,
            digest=digest,
            resume_from=resume_from,
            checkpoint_data=checkpoint_data,
        )

    def _progress(self, task_id: str, downloaded: int, total: int) -> None:
        _progress_impl(
            manager=self, task_id=task_id, downloaded=downloaded, total=total
        )

    def list_download_tasks(
        self,
        *,
        tenant_id: Optional[str] = None,
        state: str = "",
        limit: int = 100,
        offset: int = 0,
        since_seq: Optional[int] = None,
    ) -> Dict[str, Any]:
        return _list_download_tasks_impl(
            manager=self,
            tenant_id=tenant_id,
            state=state,
            limit=limit,
            offset=offset,
            since_seq=since_seq,
        )

    def get_download_task(
        self, task_id: str, *, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return _get_download_task_impl(
            manager=self, task_id=task_id, tenant_id=tenant_id
        )

    def cancel_download_task(
        self, task_id: str, *, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return _cancel_download_task_impl(
            manager=self, task_id=task_id, tenant_id=tenant_id
        )

    def import_downloaded_model(
        self,
        *,
        task_id: str,
        tenant_id: Optional[str] = None,
        destination_subdir: Optional[str] = None,
        filename: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return _import_downloaded_model_impl(
            manager=self,
            task_id=task_id,
            tenant_id=tenant_id,
            destination_subdir=destination_subdir,
            filename=filename,
            tags=tags,
        )

    def list_installations(
        self,
        *,
        tenant_id: Optional[str] = None,
        model_type: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        return _list_installations_impl(
            manager=self,
            tenant_id=tenant_id,
            model_type=model_type,
            limit=limit,
            offset=offset,
            norm_model_type=_norm_model_type,
            default_tenant_id=DEFAULT_TENANT_ID,
        )


model_manager = ModelManager()
