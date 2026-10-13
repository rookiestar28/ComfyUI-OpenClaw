"""
OpenClaw API Client (F29 Remediation).
Handles communication with the local ComfyUI instance.
"""

import json
import logging
import uuid
from typing import Optional
from urllib.parse import quote

from .config import ConnectorConfig

logger = logging.getLogger(__name__)


def _create_session():
    """
    Create an aiohttp ClientSession if available.

    NOTE:
    - The ComfyUI runtime includes `aiohttp`, but unit test environments may not.
    - Keep the import lazy so `python -m unittest discover ...` can run without aiohttp installed.
    """
    try:
        import aiohttp  # type: ignore
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "aiohttp is required for the chat connector network client. "
            "Install aiohttp (or run inside a ComfyUI environment)."
        ) from e
    return aiohttp.ClientSession()


class OpenClawClient:
    def __init__(self, config: ConnectorConfig):
        self.base_url = config.openclaw_url
        self.headers = {
            "User-Agent": "OpenClaw-Connector/0.1.0",
        }
        if config.admin_token:
            self.headers["X-OpenClaw-Admin-Token"] = config.admin_token

        self.session = None

    async def start(self):
        """Initialize shared session."""
        if not self.session:
            self.session = _create_session()

    async def close(self):
        """Close shared session."""
        if self.session:
            await self.session.close()

    async def _request(
        self, method: str, path: str, json_data: dict = None, timeout: int = 10
    ) -> dict:
        url = f"{self.base_url}{path}"
        session = self.session

        # Fallback if start() wasn't called (e.g. tests)
        local_session = False
        if not session:
            session = _create_session()
            local_session = True

        try:
            async with session.request(
                method, url, headers=self.headers, json=json_data, timeout=timeout
            ) as resp:
                result = {"ok": resp.status in (200, 201, 202), "status": resp.status}

                try:
                    data = await resp.json()
                    result["data"] = data
                    if not result["ok"]:
                        result["error"] = (
                            data.get("error")
                            or data.get("message")
                            or f"HTTP {resp.status}"
                        )
                except:
                    result["data"] = {}
                    if not result["ok"]:
                        try:
                            result["error"] = f"HTTP {resp.status}: {await resp.text()}"
                        except:
                            result["error"] = f"HTTP {resp.status}"

                return result
        except Exception as e:
            logger.error(f"Request failed {method} {path}: {type(e).__name__}: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            if local_session:
                await session.close()

    # --- Observability ---

    async def get_openclaw_config(self) -> dict:
        """Fetch OpenClaw runtime config (provider, model, base_url, etc.)."""
        return await self._request("GET", "/openclaw/config")

    async def get_templates(self) -> dict:
        """Fetch available templates (ids + metadata)."""
        return await self._request("GET", "/openclaw/templates")

    async def chat_llm(
        self,
        system: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict:
        """Run a server-side LLM chat (uses backend config + keys)."""
        # NOTE: Must call backend so UI-stored secrets are available (connector has no access).
        payload = {
            "system": system,
            "user_message": user_message,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # NOTE: LLM calls can exceed the default 10s HTTP timeout.
        return await self._request("POST", "/openclaw/llm/chat", payload, timeout=120)

    async def get_health(self) -> dict:
        res = await self._request("GET", "/openclaw/health")
        # Health endpoint might return nested structure, but we just want the wrapper
        return res

    async def get_prompt_queue(self) -> dict:
        res = await self._request("GET", "/api/prompt")
        # Standard ComfyUI api/prompt returns {exec_info: ...} on success
        if res.get("ok"):
            # Normalize ComfyUI response to standard wrapper if needed,
            # but our wrapper put body in 'data'.
            # Return just data to be combatible with standard expectations or keep wrapper?
            # Wrapper is {"ok": true, "data": {...}}
            return res
        return res

    async def get_history(self, prompt_id: str) -> dict:
        return await self._request("GET", f"/history/{prompt_id}")

    async def get_trace(self, prompt_id: str) -> dict:
        # F29 Phase 3 Introspection
        # Admin-only typically, gives detailed execution trace/logs for a job
        return await self._request("GET", f"/openclaw/trace/{prompt_id}")

    async def get_jobs(self) -> dict:
        # F29 Phase 3 Introspection
        # Returns active jobs / queue summary
        return await self._request("GET", f"/openclaw/jobs")

    # --- Execution ---

    async def submit_job(
        self, template_id: str, inputs: dict, require_approval: bool = False
    ) -> dict:
        # Remediation: Use /triggers/fire with admin token
        data = {
            "template_id": template_id,
            "inputs": inputs,
            "trace_id": str(uuid.uuid4()),
            "require_approval": require_approval,
        }
        return await self._request("POST", "/openclaw/triggers/fire", data)

    async def cancel_job(self, job_id: str) -> dict:
        encoded_job_id = quote(str(job_id), safe="")
        return await self._request("POST", f"/api/jobs/{encoded_job_id}/cancel", {})

    async def cancel_jobs(self, job_ids: list[str]) -> dict:
        return await self._request("POST", "/api/jobs/cancel", {"job_ids": job_ids})

    async def interrupt_output(self, prompt_id: Optional[str] = None) -> dict:
        # No prompt_id means explicit global interrupt. A prompt_id is targeted.
        payload = {"prompt_id": str(prompt_id)} if prompt_id else {}
        return await self._request("POST", "/api/interrupt", payload)

    async def get_view(
        self, filename: str, subfolder: str = "", type: str = "output"
    ) -> Optional[bytes]:
        """Download image/file from ComfyUI /view endpoint."""
        params = {"filename": filename, "subfolder": subfolder, "type": type}
        url = f"{self.base_url}/view"

        session = self.session
        local_session = False
        if not session:
            session = _create_session()
            local_session = True

        try:
            async with session.get(url, params=params, headers=self.headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.warning(f"get_view failed: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"get_view error: {e}")
            return None
        finally:
            if local_session:
                await session.close()

    # --- Approvals ---

    async def get_approvals(self) -> dict:
        # Remediation: Correct endpoint and shape
        res = await self._request("GET", "/openclaw/approvals?status=pending")
        if res.get("ok"):
            # Flatten: backend wrapper {"approvals": [], ...} is inside res["data"]
            data = res.get("data", {})
            if "approvals" in data:
                return {
                    "ok": True,
                    "items": data.get("approvals", []),
                    "count": data.get("count"),
                    "pending_count": data.get("pending_count"),
                }
        return res

    async def get_approval(self, approval_id: str) -> dict:
        res = await self._request("GET", f"/openclaw/approvals/{approval_id}")
        if res.get("ok"):
            data = res.get("data", {})
            if "approval" in data:
                return {"ok": True, "approval": data.get("approval")}
        return res

    async def approve_request(
        self, approval_id: str, auto_execute: bool = True
    ) -> dict:
        return await self._request(
            "POST",
            f"/openclaw/approvals/{approval_id}/approve",
            {"auto_execute": auto_execute},
        )

    async def reject_request(self, approval_id: str, reason: str = "") -> dict:
        return await self._request(
            "POST", f"/openclaw/approvals/{approval_id}/reject", {"reason": reason}
        )

    # --- Schedules ---

    async def get_schedules(self) -> dict:
        res = await self._request("GET", "/openclaw/schedules")
        if res.get("ok"):
            # Backend returns {"schedules": [...]}
            data = res.get("data", {})
            return {"ok": True, "schedules": data.get("schedules", [])}
        return res

    async def run_schedule(self, schedule_id: str) -> dict:
        return await self._request("POST", f"/openclaw/schedules/{schedule_id}/run")

    async def claim_schedule_deliveries(
        self, platforms: list[str], limit: int, lease_sec: int
    ) -> dict:
        """Lease pending schedule deliveries for the given platforms (admin token)."""
        return await self._request(
            "POST",
            "/openclaw/schedule-deliveries/claim",
            {"platforms": list(platforms), "limit": limit, "lease_sec": lease_sec},
        )

    async def ack_schedule_delivery(
        self,
        delivery_id: str,
        lease_token: str,
        outcome: str,
        reason: str | None = None,
    ) -> dict:
        """Record the outcome of a leased schedule delivery."""
        body: dict = {"lease_token": lease_token, "outcome": outcome}
        if reason is not None:
            body["reason"] = reason
        return await self._request(
            "POST",
            f"/openclaw/schedule-deliveries/{quote(delivery_id, safe='')}/ack",
            body,
        )
