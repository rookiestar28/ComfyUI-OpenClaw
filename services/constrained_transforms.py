"""
F42 — Constrained Transform Engine.

Optional, auditable, opt-in execution model for advanced webhook payload transforms.
Transform modules execute ONLY from trusted directories with integrity pinning.

Runtime enforces strict limits:
- Timeout per transform
- Output size cap
- CPU/memory budget (best-effort)
- No arbitrary network/filesystem access
- Bounded audit schema for each transform stage

Default posture: DISABLED. Requires OPENCLAW_ENABLE_TRANSFORMS=1.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .transform_common import (
    _FEATURE_FLAG,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_TRANSFORMS_PER_REQUEST,
    DEFAULT_TRANSFORM_TIMEOUT_SEC,
    MAX_TRANSFORM_MODULE_SIZE_BYTES,
    TransformLimits,
    TransformRegistry,
    TransformRegistryError,
    TransformResult,
    TransformStatus,
    TrustedTransform,
    get_transform_registry,
    is_transforms_enabled,
)

logger = logging.getLogger("ComfyUI-OpenClaw.services.constrained_transforms")

# IMPORTANT:
# Keep compatibility exports in this module even after refactor to
# `services.transform_common`; tests and downstream imports still reference
# `services.constrained_transforms` directly.
__all__ = [
    "_FEATURE_FLAG",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MAX_TRANSFORMS_PER_REQUEST",
    "DEFAULT_TRANSFORM_TIMEOUT_SEC",
    "MAX_TRANSFORM_MODULE_SIZE_BYTES",
    "TransformLimits",
    "TransformRegistry",
    "TransformRegistryError",
    "TransformResult",
    "TransformStatus",
    "TrustedTransform",
    "TransformExecutor",
    "TransformTimeoutError",
    "get_transform_executor",
    "get_transform_registry",
    "is_transforms_enabled",
]


# ---------------------------------------------------------------------------
# Constrained executor
# ---------------------------------------------------------------------------


class TransformTimeoutError(Exception):
    """Raised when a transform exceeds its timeout budget."""

    pass


class TransformExecutor:
    """
    Legacy in-process transform executor (DEPRECATED — use TransformProcessRunner).

    WARNING: This executor runs transforms in a daemon thread within the host
    process.  Python threads cannot be forcefully terminated, so a malicious or
    runaway transform can escape the timeout constraint and continue executing
    indefinitely.  It also shares the host process address space, making
    network/filesystem isolation best-effort only.

    New code should always use ``TransformProcessRunner`` (S35) which executes
    transforms in an isolated subprocess with enforced timeouts via
    ``subprocess.run(timeout=…)`` and network access denied via socket
    monkeypatching.

    ``get_transform_executor()`` will refuse to fall back to this class and
    instead fail closed if ``TransformProcessRunner`` is unavailable.

    Enforces:
    - Timeout per transform (best-effort — thread cannot be killed)
    - Output size cap
    - Integrity verification before execution
    - No network/filesystem access (best-effort: module is pre-vetted)
    - Audit events for each stage
    """

    def __init__(
        self,
        registry: TransformRegistry,
        limits: Optional[TransformLimits] = None,
    ):
        self._registry = registry
        self._limits = limits or TransformLimits.from_env()

    def execute_transform(
        self,
        transform_id: str,
        input_data: Dict[str, Any],
        *,
        trace_id: str = "",
    ) -> TransformResult:
        """
        Execute a single registered transform within constraints.

        The transform module must export a `transform(input_data: dict) -> dict` function.
        """
        if not is_transforms_enabled():
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.DENIED.value,
                error=f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable.",
            )

        transform = self._registry.get_transform(transform_id)
        if not transform:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Transform '{transform_id}' not found in registry",
            )

        # Verify integrity before execution
        if not self._registry.verify_integrity(transform_id):
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.DENIED.value,
                error="Integrity verification failed — module may have been modified",
                audit={"reason": "integrity_check_failed", "trace_id": trace_id},
            )

        # Execute with timeout
        start_time = time.monotonic()
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, str] = {}

        def _run_transform():
            try:
                # Load the module dynamically
                spec = importlib.util.spec_from_file_location(
                    f"_transform_{transform_id}", transform.module_path
                )
                if not spec or not spec.loader:
                    error_holder["error"] = "Failed to load transform module"
                    return

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore

                # Must export a transform() function
                transform_fn = getattr(module, "transform", None)
                if not callable(transform_fn):
                    error_holder["error"] = (
                        "Module does not export a 'transform(input_data)' function"
                    )
                    return

                # Execute the transform
                output = transform_fn(input_data)

                if not isinstance(output, dict):
                    error_holder["error"] = (
                        f"Transform must return a dict, got {type(output).__name__}"
                    )
                    return

                result_holder["output"] = output

            except Exception as e:
                error_holder["error"] = str(e)

        # Run in a thread with timeout
        thread = threading.Thread(target=_run_transform, daemon=True)
        thread.start()
        thread.join(timeout=self._limits.timeout_sec)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        if thread.is_alive():
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.TIMEOUT.value,
                error=f"Transform exceeded timeout ({self._limits.timeout_sec}s)",
                duration_ms=elapsed_ms,
                audit={"timeout_sec": self._limits.timeout_sec, "trace_id": trace_id},
            )

        if error_holder:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=error_holder.get("error", "Unknown error"),
                duration_ms=elapsed_ms,
                audit={"trace_id": trace_id},
            )

        output = result_holder.get("output", {})

        # Check output size
        try:
            output_json = json.dumps(output, default=str)
            output_bytes = len(output_json.encode("utf-8"))
        except Exception:
            output_bytes = 0

        if output_bytes > self._limits.max_output_bytes:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Output exceeds size limit ({output_bytes} > {self._limits.max_output_bytes})",
                duration_ms=elapsed_ms,
                output_bytes=output_bytes,
                audit={"trace_id": trace_id},
            )

        return TransformResult(
            transform_id=transform_id,
            status=TransformStatus.SUCCESS.value,
            output=output,
            duration_ms=elapsed_ms,
            output_bytes=output_bytes,
            audit={"trace_id": trace_id},
        )

    def execute_chain(
        self,
        transform_ids: List[str],
        input_data: Dict[str, Any],
        *,
        trace_id: str = "",
    ) -> List[TransformResult]:
        """
        Execute a chain of transforms sequentially.

        Output of each transform becomes input for the next.
        Chain stops on first error/timeout/denial.
        """
        if not is_transforms_enabled():
            return [
                TransformResult(
                    transform_id="chain",
                    status=TransformStatus.DENIED.value,
                    error=f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable.",
                )
            ]

        if len(transform_ids) > self._limits.max_transforms_per_request:
            return [
                TransformResult(
                    transform_id="chain",
                    status=TransformStatus.DENIED.value,
                    error=f"Transform chain exceeds limit ({len(transform_ids)} > {self._limits.max_transforms_per_request})",
                )
            ]

        results: List[TransformResult] = []
        current_data = input_data

        for tid in transform_ids:
            result = self.execute_transform(tid, current_data, trace_id=trace_id)
            results.append(result)

            if result.status != TransformStatus.SUCCESS.value:
                # Stop chain on failure
                break

            # Pass output as input to next transform
            if result.output:
                current_data = result.output

        return results


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


_executor: Optional[TransformExecutor] = None


def get_transform_executor() -> TransformExecutor:
    """
    Get or create the global transform executor.

    Always returns a ``TransformProcessRunner`` instance (S35 process
    isolation).  If the subprocess runner cannot be imported, this function
    raises ``RuntimeError`` rather than falling back to the in-process
    ``TransformExecutor``, which cannot enforce timeouts or isolation.
    """
    global _executor
    if _executor is None:
        # Local import to avoid circular dependency
        try:
            from .transform_runner import TransformProcessRunner

            registry = get_transform_registry()
            _executor = TransformProcessRunner(registry)  # type: ignore
        except ImportError as e:
            # Fail closed: do NOT fall back to the thread-based executor.
            # The in-process TransformExecutor uses threading.Thread which
            # cannot be forcefully terminated, allowing a malicious transform
            # to escape timeout constraints and continue executing in the
            # host process indefinitely.
            logger.error(
                f"S35: Could not import transform_runner ({e}). "
                "Refusing to fall back to in-process executor for security reasons. "
                "Transforms will be unavailable until the subprocess runner is restored."
            )
            raise RuntimeError(
                "TransformProcessRunner unavailable — transforms disabled for security. "
                f"Import error: {e}"
            ) from e

    return _executor
