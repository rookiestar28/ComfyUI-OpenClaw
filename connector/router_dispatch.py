"""Owned command parsing, dispatch, and authorization mixin."""

# ruff: noqa: UP006, UP035, UP045 -- preserve the frozen public annotations.
# mypy: disable-error-code="attr-defined,no-any-return"

import logging
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import CommandClass
from .contract import CommandRequest, CommandResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouterRequestContext:
    """Immutable dispatch values for one authorized command attempt."""

    request: CommandRequest
    parsed_command: str
    canonical_command: str
    args: tuple[str, ...]
    command_class: CommandClass


class RouterDispatchMixin:
    async def handle(self, req: CommandRequest) -> CommandResponse:
        """Main dispatch loop."""
        text = req.text.strip()
        # NOTE: Debug-only raw message logging for troubleshooting parsing issues.
        # Enable with OPENCLAW_CONNECTOR_DEBUG=1. May include sensitive user content.
        if self.config.debug:
            logger.info(
                "DEBUG raw message: platform=%s user=%s chat=%s text=%r",
                req.platform,
                req.sender_id,
                req.channel_id,
                text,
            )

        # F32 WP2: Rate limiting
        if not self._rate_limiter.is_allowed(str(req.sender_id), str(req.channel_id)):
            return CommandResponse(
                text="[Rate Limited] Too many requests. Please wait a moment."
            )

        # F32 WP5: Command length limit
        if len(text) > self.config.max_command_length:
            return CommandResponse(
                text=f"[Error] Command too long ({len(text)} chars). Max: {self.config.max_command_length}."
            )

        try:
            # IMPORTANT (recurring usability bug):
            # Do not use `shlex.split()` directly for ChatOps commands that may include natural
            # language. In POSIX mode, `shlex` treats apostrophes (`'`) as quote delimiters, so
            # common contractions like "She's" trigger "unbalanced quotes" failures.
            #
            # We therefore only treat *double quotes* (`"`) as quoting characters, so users can
            # still do: positive_prompt="a prompt with spaces" while apostrophes remain safe.
            lexer = shlex.shlex(text, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.quotes = '"'
            parts = list(lexer)
        except ValueError:
            return CommandResponse(
                text="[Error] Parsing command arguments failed (unbalanced quotes?)."
            )

        if not parts:
            return CommandResponse(text="Empty command.")

        cmd = parts[0].lower()
        args = parts[1:]

        # Telegram group commands often include the bot username suffix, e.g. `/help@mybot`.
        # If we don't strip it, the command won't match our dispatch table and appears "dead"
        # even though polling is working.
        if (
            (req.platform or "").lower() == "telegram"
            and cmd.startswith("/")
            and "@" in cmd
        ):
            cmd = cmd.split("@", 1)[0]

        # Some users type `@bot /help` in group chats. Treat that as a command too.
        if cmd.startswith("@") and args and args[0].startswith("/"):
            cmd = args[0].lower()
            args = args[1:]

        # Dispatch Table
        handlers = {
            ("/status", "status"): (self._handle_status, CommandClass.PUBLIC),
            ("/help", "help", "/start"): (self._handle_help, CommandClass.PUBLIC),
            ("/run", "run"): (self._handle_run, CommandClass.RUN),
            ("/interrupt", "interrupt", "/cancel", "cancel", "/stop"): (
                self._handle_interrupt,
                CommandClass.ADMIN,
            ),  # Global interrupt => admin-only.
            ("/approvals", "approvals"): (
                self._handle_approvals_list,
                CommandClass.ADMIN,
            ),
            ("/approve", "approve"): (self._handle_approve, CommandClass.ADMIN),
            ("/reject", "reject"): (self._handle_reject, CommandClass.ADMIN),
            ("/schedules", "schedules"): (
                self._handle_schedules_list,
                CommandClass.ADMIN,
            ),
            ("/schedule", "schedule"): (
                self._handle_schedule_subcommand,
                CommandClass.ADMIN,
            ),
            # Phase 3 Introspection
            ("/history", "history"): (self._handle_history, CommandClass.PUBLIC),
            ("/trace", "trace"): (self._handle_trace, CommandClass.ADMIN),  # Admin only
            ("/jobs", "jobs", "queue"): (self._handle_jobs, CommandClass.ADMIN),
            # F30: Chat Assistant
            ("/chat", "chat"): (self._handle_chat, CommandClass.PUBLIC),
        }

        # Find Handler
        handler = None

        canonical_cmd = cmd  # Fallback
        for aliases, (func, cmd_class) in handlers.items():
            if cmd in aliases:
                handler = func
                default_class = cmd_class
                # R80 Remediation: Use canonical command (first alias) for policy checks
                # This prevents "run" vs "/run" bypass issues.
                # Convention: first alias is canonical (e.g. "/run").
                canonical_cmd = aliases[0] if isinstance(aliases, tuple) else aliases
                break

        if not handler:
            return CommandResponse(
                text=f"Unknown command: {cmd}. Type /help for options."
            )

        context = RouterRequestContext(
            request=req,
            parsed_command=cmd,
            canonical_command=canonical_cmd,
            args=tuple(args),
            command_class=default_class,
        )

        # R80: Centralized Authorization Gate
        # Pass canonical_cmd to ensure policy matches aliases correctly
        if auth_err := self._check_command_authz(
            context.canonical_command, context.request, context.command_class
        ):
            return auth_err

        # Execute
        try:
            return await handler(context.request, list(context.args))
        except Exception as exc:
            # CRITICAL: this is an external connector boundary. Never restore raw
            # exception/traceback text here; it leaks backend or prompt details.
            logger.error("connector.command_failed (error_type=%s)", type(exc).__name__)
            return CommandResponse(text="[Internal Error]")

    def _is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.config.admin_users

    def _delivery_context(self, req: CommandRequest) -> Dict[str, Any]:
        context: Dict[str, Any] = {}
        if getattr(req, "workspace_id", ""):
            context["workspace_id"] = str(req.workspace_id)
        if getattr(req, "thread_id", ""):
            context["thread_id"] = str(req.thread_id)
        return context

    def _check_command_authz(
        self, cmd: str, req: CommandRequest, default_class: CommandClass
    ) -> Optional[CommandResponse]:
        """
        R80: Verify command authorization policy.
        Returns None if allowed, or CommandResponse(text=error) if denied.
        """
        policy = self.config.command_policy

        # 1. Resolve Effective Class (Handle per-command overrides)
        # Note: 'cmd' here is the canonical parsed command string (lowercase), e.g., "/run" or "run"
        # The overrides dict might use "/run" or "run", we should check both or normalize.
        # Currently, the router logic normalized `cmd` from input (lines 90-101).
        # We'll check exact match against the override key.
        eff_class = policy.command_overrides.get(cmd, default_class)

        # 2. Check AllowFrom List (Explicit User Allow)
        # If an explicit AllowFrom list exists for this class, the user MUST be in it.
        # This takes precedence over role logic.
        allowed_users = policy.allow_from.get(eff_class)
        if allowed_users is not None and len(allowed_users) > 0:
            if str(req.sender_id) not in allowed_users:
                # If explicit allow-list is active, even admins must be in it?
                # Decision: YES, for strict compliance. If you want admins, add them to the list.
                # However, for usability, usually admins are implied.
                # Let's stick to "Explicit List Wins" for R80 strict mode.
                return CommandResponse(
                    text="[Access Denied] You are not in the allow-list for this command."
                )
            # If in list, proceed (bypass default role checks? No, usually allows)
            return None

        # 3. Default Role Logic
        if eff_class == CommandClass.ADMIN and not self._is_admin(req.sender_id):
            return CommandResponse(
                text="[Access Denied] This command requires Admin privileges."
            )

        # PUBLIC and RUN are allowed by default (RUN checks trust internally)
        return None

    def _is_trusted(self, req: CommandRequest) -> bool:
        """
        Trusted users can execute /run immediately.
        Untrusted users are routed to approval flow.
        """
        if self._is_admin(req.sender_id):
            return True

        platform = (req.platform or "").lower()
        sender_id = str(req.sender_id)
        channel_id = str(req.channel_id)

        if platform == "telegram":
            try:
                uid = int(sender_id)
            except ValueError:
                uid = None
            try:
                cid = int(channel_id)
            except ValueError:
                cid = None
            if uid is not None and uid in self.config.telegram_allowed_users:
                return True
            return cid is not None and cid in self.config.telegram_allowed_chats

        if platform == "discord":
            if sender_id in self.config.discord_allowed_users:
                return True
            return channel_id in self.config.discord_allowed_channels

        if platform == "line":
            if sender_id in self.config.line_allowed_users:
                return True
            return channel_id in self.config.line_allowed_groups

        if platform == "whatsapp":
            return sender_id in self.config.whatsapp_allowed_users

        if platform == "wechat":
            return sender_id in self.config.wechat_allowed_users

        if platform == "kakao":
            return sender_id in self.config.kakao_allowed_users

        if platform == "slack":
            if sender_id in self.config.slack_allowed_users:
                return True
            return channel_id in self.config.slack_allowed_channels

        if platform == "feishu":
            if sender_id in self.config.feishu_allowed_users:
                return True
            return channel_id in self.config.feishu_allowed_chats

        # Unknown platform: trust only admins
        return False

    # --- Handlers ---
