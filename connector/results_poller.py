import asyncio
import logging
import time
from typing import Any, Dict, Optional

from .config import ConnectorConfig
from .contract import Platform
from .openclaw_client import OpenClawClient
from .reply_visibility import decide_reply_visibility

logger = logging.getLogger(__name__)


class ResultsPoller:
    """
    Polls ComfyUI history for completed jobs and triggers delivery.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        client: OpenClawClient,
        platforms: Dict[str, Platform],
    ):
        self.config = config
        self.client = client
        self.platforms = platforms  # map "telegram" -> TelegramPolling, etc.
        self.queue = (
            asyncio.Queue()
        )  # (prompt_id, platform_name, channel_id, sender_id, delivery_context)
        self.approval_queue = (
            asyncio.Queue()
        )  # (approval_id, platform_name, channel_id, sender_id, delivery_context)
        self.active_polls = {}  # prompt_id -> task
        self.active_approval_polls = {}  # approval_id -> task

    async def start(self):
        """Start the main queue consumer."""
        logger.info("ResultsPoller started.")
        await asyncio.gather(self._job_consumer(), self._approval_consumer())

    async def stop(self):
        """Graceful shutdown."""
        logger.info("ResultsPoller stopping...")
        for task in self.active_polls.values():
            task.cancel()
        for task in self.active_approval_polls.values():
            task.cancel()

        if self.active_polls:
            await asyncio.gather(*self.active_polls.values(), return_exceptions=True)
        if self.active_approval_polls:
            await asyncio.gather(
                *self.active_approval_polls.values(), return_exceptions=True
            )
        logger.info("ResultsPoller stopped.")

    def track_job(
        self,
        prompt_id: str,
        platform_name: str,
        channel_id: str,
        sender_id: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Enqueue a job for result monitoring."""
        if not prompt_id:
            return

        logger.info(f"Tracking job {prompt_id} for {platform_name} in {channel_id}")
        self.queue.put_nowait(
            (
                prompt_id,
                platform_name,
                channel_id,
                sender_id,
                dict(delivery_context or {}),
            )
        )

    def track_approval(
        self,
        approval_id: str,
        platform_name: str,
        channel_id: str,
        sender_id: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        if not approval_id:
            return
        if not self.config.admin_token:
            logger.warning(
                "Approval tracking requires OPENCLAW_CONNECTOR_ADMIN_TOKEN. Skipping."
            )
            asyncio.create_task(
                self._send_text(
                    platform_name,
                    channel_id,
                    "⚠️ Approval tracking requires OPENCLAW_CONNECTOR_ADMIN_TOKEN.",
                )
            )
            return
        logger.info(
            f"Tracking approval {approval_id} for {platform_name} in {channel_id}"
        )
        self.approval_queue.put_nowait(
            (
                approval_id,
                platform_name,
                channel_id,
                sender_id,
                dict(delivery_context or {}),
            )
        )

    async def _job_consumer(self):
        while True:
            item = await self.queue.get()
            try:
                prompt_id, platform_name, channel_id, sender_id, delivery_context = item
                task = asyncio.create_task(
                    self._poll_job(
                        prompt_id,
                        platform_name,
                        channel_id,
                        sender_id,
                        delivery_context,
                    )
                )
                self.active_polls[prompt_id] = task
                task.add_done_callback(
                    lambda t, pid=prompt_id: self.active_polls.pop(pid, None)
                )
            finally:
                self.queue.task_done()

    async def _approval_consumer(self):
        while True:
            item = await self.approval_queue.get()
            try:
                (
                    approval_id,
                    platform_name,
                    channel_id,
                    sender_id,
                    delivery_context,
                ) = item
                task = asyncio.create_task(
                    self._poll_approval(
                        approval_id,
                        platform_name,
                        channel_id,
                        sender_id,
                        delivery_context,
                    )
                )
                self.active_approval_polls[approval_id] = task
                task.add_done_callback(
                    lambda t, aid=approval_id: self.active_approval_polls.pop(aid, None)
                )
            finally:
                self.approval_queue.task_done()

    async def _poll_approval(
        self,
        approval_id: str,
        platform_name: str,
        channel_id: str,
        sender_id: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        start_time = time.time()
        delay = 2.0

        while (time.time() - start_time) < self.config.delivery_timeout_sec:
            try:
                res = await self.client.get_approval(approval_id)
                if res.get("ok"):
                    approval = res.get("approval") or {}
                    status = approval.get("status")
                    if status in ("rejected", "expired"):
                        await self._send_text(
                            platform_name,
                            channel_id,
                            f"❌ Approval {approval_id} {status}.",
                            delivery_context=delivery_context,
                        )
                        return
                    if status == "approved":
                        metadata = approval.get("metadata") or {}
                        # NOTE: executed_prompt_id is written by the server after UI approval.
                        # Keep this key stable; connector relies on it to auto-deliver images.
                        prompt_id = (
                            metadata.get("executed_prompt_id")
                            or metadata.get("prompt_id")
                            or approval.get("prompt_id")
                        )
                        if prompt_id:
                            self.track_job(
                                prompt_id,
                                platform_name,
                                channel_id,
                                sender_id,
                                delivery_context=delivery_context,
                            )
                            return
            except Exception as e:
                logger.debug(f"Approval poll failed for {approval_id}: {e}")

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, 15)

        await self._send_text(
            platform_name,
            channel_id,
            f"⚠️ Approval {approval_id} timed out waiting for execution.",
            delivery_context=delivery_context,
        )

    async def _poll_job(
        self,
        prompt_id: str,
        platform_name: str,
        channel_id: str,
        sender_id: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Poll history with backoff until complete or timeout."""
        job_data = await self.wait_for_job(prompt_id)
        if job_data is not None:
            await self._deliver_results(
                prompt_id,
                job_data,
                platform_name,
                channel_id,
                delivery_context=delivery_context,
            )
            return

        logger.warning(f"Job {prompt_id} timed out waiting for results.")
        await self._send_text(
            platform_name,
            channel_id,
            f"⚠️ Job {prompt_id} timed out waiting for results.",
            delivery_context=delivery_context,
        )

    async def wait_for_job(self, prompt_id: str) -> dict[str, Any] | None:
        """Poll history with backoff; return the job entry, or None after the timeout."""
        start_time = time.time()
        delay = 1.0

        while (time.time() - start_time) < self.config.delivery_timeout_sec:
            # Check history
            try:
                hist = await self.client.get_history(prompt_id)
                if hist.get("ok"):
                    # ComfyUI /history/{prompt_id} -> { "prompt_id": { ... } }
                    data = hist.get("data", {})
                    entry = data.get(prompt_id) if isinstance(data, dict) else None
                    if isinstance(entry, dict):
                        return entry
            except Exception as e:
                logger.debug(f"Poll check failed for {prompt_id}: {e}")

            # Backoff
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, 15)  # Cap at 15s
        return None

    async def deliver_job_results(
        self,
        prompt_id: str,
        job_data: dict[str, Any],
        platform_name: str,
        channel_id: str,
        *,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        """Send a finished job's images (or a no-output notice) to a platform target."""
        await self._deliver_results(
            prompt_id,
            job_data,
            platform_name,
            channel_id,
            delivery_context=delivery_context,
        )

    async def send_text(
        self,
        platform_name: str,
        channel_id: str,
        text: str,
        *,
        delivery_context: dict[str, Any] | None = None,
    ) -> None:
        """Send text through the platform adapter, honoring reply visibility policy."""
        await self._send_text(
            platform_name, channel_id, text, delivery_context=delivery_context
        )

    async def _deliver_results(
        self,
        prompt_id: str,
        job_data: dict,
        platform_name: str,
        channel_id: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Download images and send to platform."""
        outputs = job_data.get("outputs", {})
        if not outputs:
            logger.info(f"Job {prompt_id} has no outputs.")
            await self._send_text(
                platform_name,
                channel_id,
                f"✅ Job {prompt_id} finished (No output images).",
                delivery_context=delivery_context,
            )
            return

        images_to_send = []

        # Flatten outputs
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                images_to_send.extend(node_output["images"])

        # Check if actual images were found (filter non-outputs)

        if not images_to_send:
            logger.info(f"Job {prompt_id} finished but no images found.")
            await self._send_text(
                platform_name,
                channel_id,
                f"✅ Job {prompt_id} finished (No images).",
                delivery_context=delivery_context,
            )
            return

        images_to_send = images_to_send[: self.config.delivery_max_images]
        logger.info(f"Delivering {len(images_to_send)} images for {prompt_id}")

        platform = self.platforms.get(platform_name)
        if not platform:
            logger.error(f"Platform {platform_name} not loaded.")
            return

        for img_info in images_to_send:
            filename = img_info.get("filename")
            subfolder = img_info.get("subfolder", "")
            img_type = img_info.get("type", "output")

            # Download content
            content = await self.client.get_view(filename, subfolder, img_type)
            if not content:
                logger.warning(f"Failed to download {filename}")
                continue

            # Check size
            if len(content) > self.config.delivery_max_bytes:
                logger.warning(
                    f"Image {filename} too large ({len(content)} bytes). Skipping."
                )
                await self._send_text(
                    platform_name,
                    channel_id,
                    f"⚠️ Image {filename} skipped (too large).",
                    delivery_context=delivery_context,
                )
                continue

            # Send with error handling
            try:
                await platform.send_image(
                    channel_id,
                    content,
                    filename=filename,
                    delivery_context=delivery_context,
                )
            except Exception as e:
                logger.error(f"Failed to deliver image to {platform_name}: {e}")
                # Fallback text
                await self._send_text(
                    platform_name,
                    channel_id,
                    f"⚠️ Failed to send image: {filename}",
                    delivery_context=delivery_context,
                )

    async def _send_text(
        self,
        platform_name: str,
        channel_id: str,
        text: str,
        *,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        ctx = dict(delivery_context or {})
        decision = decide_reply_visibility(
            delivery_context=ctx,
            platform=platform_name,
            text=text,
        )
        if decision.suppressed:
            # IMPORTANT: a suppressed visible reply is a successful delivery no-op.
            logger.info(
                "Suppressed connector text reply platform=%s channel=%s reason=%s",
                platform_name,
                channel_id,
                decision.reason,
            )
            return
        platform = self.platforms.get(platform_name)
        if platform:
            try:
                await platform.send_message(
                    channel_id,
                    text,
                    delivery_context=ctx,
                )
            except Exception as e:
                logger.error(f"Failed to send text to {platform_name}: {e}")
