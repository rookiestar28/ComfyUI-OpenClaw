"""
S35 Transform Isolation Runner.

Executes transforms in a separate process via `services.transform_worker`.
"""

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from .transform_common import (
    TransformLimits,
    TransformRegistry,
    TransformResult,
    TransformStatus,
)

logger = logging.getLogger("ComfyUI-OpenClaw.services.transform_runner")

# CRITICAL: the worker gets only what starting this interpreter needs. Do not return to
# copying os.environ with a name deny-list; passwords, DSNs and provider credentials whose
# names lack TOKEN/SECRET/KEY reached transform code that way.
_WORKER_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
    }
)


def _worker_environment() -> dict[str, str]:
    """Build the transform worker environment from the explicit allowlist."""
    # Windows variable names are case-insensitive; compare in upper case.
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _WORKER_ENV_ALLOWLIST
    }


class TransformProcessRunner:
    """
    Executes transforms in an isolated subprocess.
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
        Execute a single registered transform in a subprocess.
        """
        transform = self._registry.get_transform(transform_id)
        if not transform:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Transform '{transform_id}' not found in registry",
            )

        # Integrity Check (R77 pre-check)
        if not self._registry.verify_integrity(transform_id):
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.DENIED.value,
                error="Integrity verification failed — module modified",
                audit={"reason": "integrity_check_failed", "trace_id": trace_id},
            )

        start_time = time.monotonic()

        # Prepare Worker Command
        worker_script = os.path.join(os.path.dirname(__file__), "transform_worker.py")
        cmd = [sys.executable, worker_script, transform.module_path]

        # Prepare Input
        payload = {"input": input_data, "context": {"trace_id": trace_id}}
        input_json = json.dumps(payload)

        try:
            env = _worker_environment()

            # Subprocess Run
            proc = subprocess.run(
                cmd,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=self._limits.timeout_sec,
                env=env,
                check=False,  # We handle return codes
            )

            # Execution Time
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Handle Return Code
            if proc.returncode != 0:
                # Script crashed or printed error to stdout/stderr
                # Try to parse stdout error first
                error_msg = proc.stderr.strip() or "Process crashed with unknown error"
                try:
                    out_json = json.loads(proc.stdout)
                    if out_json.get("status") == "error":
                        error_msg = out_json.get("error", error_msg)
                except Exception:
                    pass

                return TransformResult(
                    transform_id=transform_id,
                    status=TransformStatus.ERROR.value,
                    error=f"Worker process failed (exit {proc.returncode}): {error_msg}",
                    duration_ms=elapsed_ms,
                    audit={"exit_code": proc.returncode, "trace_id": trace_id},
                )

            # Parse Output
            try:
                result_json = json.loads(proc.stdout)
            except json.JSONDecodeError:
                return TransformResult(
                    transform_id=transform_id,
                    status=TransformStatus.ERROR.value,
                    error="Worker returned invalid JSON output",
                    duration_ms=elapsed_ms,
                    audit={"raw_stdout": proc.stdout[:1000], "trace_id": trace_id},
                )

            if result_json.get("status") == "error":
                return TransformResult(
                    transform_id=transform_id,
                    status=TransformStatus.ERROR.value,
                    error=result_json.get("error", "Unknown worker error"),
                    duration_ms=elapsed_ms,
                    audit={
                        "trace_id": trace_id,
                        "traceback": result_json.get("traceback"),
                    },
                )

            output_data = result_json.get("output", {})
            output_bytes = len(json.dumps(output_data).encode("utf-8"))

            if output_bytes > self._limits.max_output_bytes:
                return TransformResult(
                    transform_id=transform_id,
                    status=TransformStatus.ERROR.value,
                    error=f"Output size limit exceeded ({output_bytes} > {self._limits.max_output_bytes})",
                    duration_ms=elapsed_ms,
                    output_bytes=output_bytes,
                    audit={"trace_id": trace_id},
                )

            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.SUCCESS.value,
                output=output_data,
                duration_ms=elapsed_ms,
                output_bytes=output_bytes,
                audit={"trace_id": trace_id, "isolation": "process"},
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.TIMEOUT.value,
                error=f"Transform timeout exceeded ({self._limits.timeout_sec}s)",
                duration_ms=elapsed_ms,
                audit={"trace_id": trace_id, "timeout": True},
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Runner exception: {str(e)}",
                duration_ms=elapsed_ms,
                audit={"trace_id": trace_id},
            )
