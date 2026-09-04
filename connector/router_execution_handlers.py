"""Owned run and interrupt command-family mixin."""

# ruff: noqa: UP006, UP035 -- preserve the frozen public annotations.
# mypy: disable-error-code="attr-defined,no-any-return"

import logging
from typing import Any, Dict, List

from .contract import CommandRequest, CommandResponse

logger = logging.getLogger(__name__)


class RouterExecutionMixin:
    async def _handle_run(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(
                text="Usage: /run <template_id> [prompt text] [key=value ...] [--approval]"
            )

        # Parse flags
        explicit_approval = False
        clean_args = []
        for arg in args:
            if arg in ("--require-approval", "--approval", "-a"):
                explicit_approval = True
            else:
                clean_args.append(arg)

        if not clean_args:
            return CommandResponse(text="Usage: /run <template_id> ...")

        template_id = clean_args[0]
        inputs: Dict[str, str] = {}
        free_text_parts: List[str] = []
        for arg in clean_args[1:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                inputs[k.strip()] = v.strip()
            else:
                free_text_parts.append(arg)

        # If user provided free text without key=value, treat it as the prompt.
        # We map it to a best-effort prompt key (prefers template metadata if available).
        if free_text_parts:
            prompt_key = await self._resolve_prompt_key(template_id)
            if prompt_key not in inputs:
                inputs[prompt_key] = " ".join(free_text_parts).strip()
            elif self.config.debug:
                logger.info(
                    "DEBUG /run free-text ignored (prompt key already set): %s",
                    prompt_key,
                )

        # NOTE: Debug-only payload logging for troubleshooting prompt mismatches.
        # Enable with OPENCLAW_CONNECTOR_DEBUG=1 to log template_id + inputs.
        if self.config.debug:
            logger.info(
                "DEBUG /run payload: template=%s inputs=%s approval_flag=%s trusted=%s",
                template_id,
                inputs,
                explicit_approval,
                self._is_trusted(req),
            )

        trusted = self._is_trusted(req)
        require_approval = explicit_approval or (not trusted)

        res = await self.client.submit_job(
            template_id, inputs, require_approval=require_approval
        )
        if res.get("ok"):
            data = res.get("data", {})
            trace_id = data.get("trace_id", "unknown")

            if data.get("pending"):
                approval_id = data.get("approval_id", "unknown")
                msg = f"[Approval Requested]\nID: {approval_id}\nTrace: {trace_id}"
                if "expires_at" in data:
                    msg += f"\nExpires: {data['expires_at']}"
                if self.poller:
                    # IMPORTANT:
                    # For untrusted users, approvals are done in the OpenClaw UI.
                    # We must start tracking the approval_id so we can map
                    # approval_id -> executed_prompt_id later and auto-deliver images.
                    self.poller.track_approval(
                        approval_id,
                        req.platform,
                        req.channel_id,
                        req.sender_id,
                        delivery_context=self._delivery_context(req),
                    )
                return CommandResponse(text=msg)
            else:
                prompt_id = data.get("prompt_id", "unknown")
                if self.poller:
                    self.poller.track_job(
                        prompt_id,
                        req.platform,
                        req.channel_id,
                        req.sender_id,
                        delivery_context=self._delivery_context(req),
                    )

                return CommandResponse(
                    text=f"[Job Submitted]\nID: {prompt_id}\nTemplate: {template_id}\nTrace: {trace_id}"
                )
        else:
            err = res.get("error", "Unknown error")
            return CommandResponse(text=f"[Submission Failed] Reason: {err}")

    async def _resolve_prompt_key(self, template_id: str) -> str:
        """
        Best-effort prompt key resolution.
        Prefer template metadata (allowed_inputs), then fall back to common names.
        """
        meta = await self._get_template_meta(template_id)
        allowed = meta.get("allowed_inputs") or []

        # If template explicitly declares a single input, use it.
        if isinstance(allowed, list) and len(allowed) == 1:
            return str(allowed[0])

        preferred = ("positive_prompt", "prompt", "text", "positive", "caption")
        if isinstance(allowed, list):
            for key in preferred:
                if key in allowed:
                    return key

        # Default fallback
        return "positive_prompt"

    async def _get_template_meta(self, template_id: str) -> Dict[str, Any]:
        if template_id in self._template_meta_cache:
            return self._template_meta_cache[template_id]
        try:
            res = await self.client.get_templates()
            if res.get("ok"):
                for item in res.get("templates", []) or []:
                    if item.get("id") == template_id:
                        self._template_meta_cache[template_id] = item
                        return item
        except Exception as exc:
            if self.config.debug:
                logger.info(
                    "connector.template_metadata_failed (error_type=%s)",
                    type(exc).__name__,
                )
        return {}

    async def _handle_interrupt(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        targets = self._parse_stop_targets(args)
        if not targets:
            res = await self.client.interrupt_output()
            if res.get("ok"):
                return CommandResponse(text="[Stop] Global Interrupt sent to ComfyUI.")
            return CommandResponse(text=f"[Stop Failed] {res.get('error')}")

        if len(targets) == 1:
            job_id = targets[0]
            res = await self.client.cancel_job(job_id)
            if res.get("ok"):
                return CommandResponse(
                    text=f"[Stop] Cancellation requested for job {job_id}."
                )

            # IMPORTANT: Targeted stops must never degrade to no-payload global
            # interrupt. Older-host fallback is allowed only with prompt_id set.
            if self._jobs_cancel_unsupported(res):
                fallback = await self.client.interrupt_output(prompt_id=job_id)
                if fallback.get("ok"):
                    return CommandResponse(
                        text=(
                            f"[Stop] Targeted interrupt sent for job {job_id} "
                            "(jobs cancel unsupported)."
                        )
                    )
                return CommandResponse(text=f"[Stop Failed] {fallback.get('error')}")

            return CommandResponse(text=f"[Stop Failed] {res.get('error')}")

        res = await self.client.cancel_jobs(targets)
        if res.get("ok"):
            return CommandResponse(
                text=f"[Stop] Cancellation requested for {len(targets)} jobs."
            )
        return CommandResponse(text=f"[Stop Failed] {res.get('error')}")

    @staticmethod
    def _parse_stop_targets(args: List[str]) -> List[str]:
        targets: List[str] = []
        for arg in args:
            for part in str(arg).split(","):
                target = part.strip()
                if target:
                    targets.append(target)
        return targets

    @staticmethod
    def _jobs_cancel_unsupported(res: Dict[str, Any]) -> bool:
        status = res.get("status")
        if status in (404, 405, 501):
            return True
        error = str(res.get("error", "")).lower()
        unsupported_markers = (
            "404",
            "not found",
            "method not allowed",
            "unsupported",
            "not implemented",
        )
        return any(marker in error for marker in unsupported_markers)
