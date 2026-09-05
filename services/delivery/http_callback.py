"""
F13 — HTTP Callback Delivery Adapter.
Delivers messages via HTTP POST to callback URLs.
Hardened by S21: Uses safe_io for SSRF protection.
"""

import asyncio
import logging
from typing import Optional, Set

from ..async_utils import run_io_in_thread
from ..chatops.transport_contract import DeliveryMessage, DeliveryTarget, TransportType
from ..env_aliases import get_env_value
from ..safe_io import STANDARD_OUTBOUND_POLICY, SSRFError, safe_request_json

logger = logging.getLogger("ComfyUI-OpenClaw.delivery.http_callback")

# Environment config (S21: Exact host allowlist)
ENV_BRIDGE_CALLBACK_HOST_ALLOWLIST = "OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST"
LEGACY_ENV_BRIDGE_CALLBACK_HOST_ALLOWLIST = "MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST"


def get_callback_host_allowlist() -> Optional[Set[str]]:
    """
    Get allowlisted callback hosts (exact match).
    Returns None if no allowlist (which means DENY ALL).
    """
    hosts_str = get_env_value(
        ENV_BRIDGE_CALLBACK_HOST_ALLOWLIST,
        aliases=(LEGACY_ENV_BRIDGE_CALLBACK_HOST_ALLOWLIST,),
        default="",
    )
    if not hosts_str:
        return None
    return set(h.lower().strip() for h in hosts_str.split(",") if h.strip())


class HttpCallbackAdapter:
    """
    Delivery adapter for HTTP POST callbacks.
    Used for sidecar callback delivery (F13).
    """

    def supports(self, target: DeliveryTarget) -> bool:
        """Support webhook transport type."""
        return target.transport == TransportType.WEBHOOK

    async def deliver(self, target: DeliveryTarget, message: DeliveryMessage) -> bool:
        """
        Deliver message via HTTP POST.

        Args:
            target: Delivery target (target_id = callback URL)
            message: Message to deliver

        Returns:
            True if POST succeeded
        """
        callback_url = target.target_id

        # S21: Fail closed if no allowlist
        allow_hosts = get_callback_host_allowlist()
        if not allow_hosts:
            logger.warning(
                "Callback delivery denied: No allowlist configured (OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST or legacy MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST)."
            )
            return False

        # Build payload
        payload = {
            "text": message.text,
            "files": message.files or [],
            "thread_id": target.thread_id,
            "mode": target.mode,
        }

        # Use safe_io.safe_request_json (sync) via thread
        try:
            # CRITICAL (R129): callback delivery should stay in the IO lane so
            # LLM spikes do not starve outbound delivery.
            await run_io_in_thread(
                safe_request_json,
                method="POST",
                url=callback_url,
                json_body=payload,
                allow_hosts=allow_hosts,
                timeout_sec=30,
                policy=STANDARD_OUTBOUND_POLICY,
            )
            return True
        except SSRFError as e:
            logger.warning(f"Callback blocked by SSRF protection: {e}")
            return False
        except Exception as e:
            logger.warning(f"Callback POST failed: {e}")
            return False
