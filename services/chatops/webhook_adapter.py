"""
R20 — Webhook Transport Adapter (Reference Implementation / Stub).

NOTE: This is a REFERENCE ADAPTER for contract validation only.
In production, authentication should delegate to services/webhook_auth.py (S2).
The validate_auth() here is a simplified stub; it does NOT provide secure-by-default behavior.
"""

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from ..env_aliases import get_env_value
from .session_scope import build_scope_key
from .transport_contract import (
    DeliveryMessage,
    DeliveryTarget,
    TransportAdapter,
    TransportContext,
    TransportEvent,
    TransportType,
)


class WebhookTransportAdapter(TransportAdapter):
    """
    Reference adapter that wraps existing webhook ingress.
    Demonstrates R20 contract compliance without external dependencies.

    IMPORTANT: This is a stub/reference implementation.
    - validate_auth() is NOT secure-by-default; production should use webhook_auth.py
    - deliver() is a no-op stub
    """

    @property
    def transport_type(self) -> TransportType:
        return TransportType.WEBHOOK

    def parse_event(self, raw_payload: Dict[str, Any]) -> Optional[TransportEvent]:
        """
        Parse webhook payload into TransportEvent.

        Expected webhook format:
        {
            "event_id": "...",  # Optional, generated if missing
            "actor_id": "...",  # Optional, defaults to "webhook"
            "text": "...",      # Required: command or message
            "attachments": [...],  # Optional
        }
        """
        # Extract or generate event_id
        event_id = raw_payload.get("event_id")
        if not event_id:
            # Generate deterministic hash from payload for idempotency
            # Use json.dumps with sort_keys for consistent ordering
            try:
                payload_str = json.dumps(
                    raw_payload, sort_keys=True, separators=(",", ":")
                )
            except (TypeError, ValueError):
                # Fallback for non-serializable values
                payload_str = repr(raw_payload)
            event_id = hashlib.sha256(payload_str.encode()).hexdigest()[:16]

        # Extract text (required)
        text = raw_payload.get("text", "")
        if not text and "prompt" in raw_payload:
            text = raw_payload["prompt"]  # Legacy format

        # Extract attachments
        attachments = raw_payload.get("attachments", [])
        if isinstance(attachments, str):
            attachments = [{"url": attachments}]

        return TransportEvent(
            transport=TransportType.WEBHOOK,
            event_id=event_id,
            timestamp=raw_payload.get("timestamp", time.time()),
            actor_id=raw_payload.get("actor_id", "webhook"),
            text=text,
            attachments=attachments,
            raw=raw_payload,
        )

    def build_context(self, event: TransportEvent) -> TransportContext:
        """
        Build context for webhook event routing.
        """
        # Webhooks use callback_url as reply target
        callback_url = ""
        if event.raw:
            callback_url = event.raw.get("callback_url", "")

        # Build scope key (webhook + actor)
        scope_key = build_scope_key(
            transport=TransportType.WEBHOOK,
            channel_id=callback_url or "default",
            user_id=event.actor_id,
            include_user=True,
        )

        return TransportContext(
            transport=TransportType.WEBHOOK,
            scope_key=scope_key,
            reply_target=callback_url,
            actor_id=event.actor_id,
        )

    async def deliver(self, target: DeliveryTarget, message: DeliveryMessage) -> bool:
        """
        Deliver message to webhook target (STUB).

        For webhooks, this means POSTing to the callback_url.
        This is a stub—actual HTTP delivery would use aiohttp/httpx.
        """
        if not target.target_id:
            raise ValueError("Webhook delivery requires target_id (callback_url)")

        # In a real implementation, POST to target.target_id
        # For now, log and return success
        import logging

        logger = logging.getLogger("ComfyUI-OpenClaw.chatops.webhook_adapter")
        logger.info(
            f"[STUB] Would deliver to {target.target_id}: " f"{message.text[:50]}..."
        )

        return True

    def validate_auth(
        self, raw_payload: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Validate webhook authentication (STUB - NOT SECURE BY DEFAULT).

        WARNING: This is a reference implementation only.
        Production code should delegate to services/webhook_auth.py for:
        - Replay protection (S2)
        - Secure secret handling
        - Constant-time comparison

        Current stub behavior:
        - If OPENCLAW_WEBHOOK_SECRET (or legacy MOLTBOT_WEBHOOK_SECRET) not set: PASSES (insecure, for dev only)
        - If set: validates X-OpenClaw-Signature (legacy X-Moltbot-Signature) header
        """
        secret = get_env_value("OPENCLAW_WEBHOOK_SECRET", default="") or ""
        if not secret:
            # S22: Fail closed unless DEV_MODE is explicit
            dev_mode = get_env_value("OPENCLAW_DEV_MODE", default="0") == "1"
            if dev_mode:
                import logging

                logging.getLogger("ComfyUI-OpenClaw.chatops.webhook_adapter").warning(
                    "OPENCLAW_WEBHOOK_SECRET not set; webhook auth bypassed (DEV MODE ENABLED)"
                )
                return True
            else:
                return False  # Deny default

        signature = headers.get("X-OpenClaw-Signature", "") or headers.get(
            "X-Moltbot-Signature", ""
        )
        if not signature:
            return False

        # Compute expected signature
        try:
            payload_bytes = json.dumps(
                raw_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        except (TypeError, ValueError):
            return False

        expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

        return hmac.compare_digest(signature, expected)
