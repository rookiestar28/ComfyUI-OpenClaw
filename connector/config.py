"""
Connector Configuration (F29).
Loads environment variables and validates allowlists.
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath
from typing import Dict, List, Optional, Set

logger = logging.getLogger("ComfyUI-OpenClaw.connector.config")

DEFAULT_DELIVERY_MAX_IMAGES = 4
MIN_DELIVERY_MAX_IMAGES = 1
MAX_DELIVERY_MAX_IMAGES = 16

DEFAULT_DELIVERY_MAX_BYTES = 10 * 1024 * 1024
MIN_DELIVERY_MAX_BYTES = 64 * 1024
MAX_DELIVERY_MAX_BYTES = 50 * 1024 * 1024

DEFAULT_DELIVERY_TIMEOUT_SEC = 600
MIN_DELIVERY_TIMEOUT_SEC = 30
MAX_DELIVERY_TIMEOUT_SEC = 3600

DEFAULT_LINE_BIND_PORT = 8099
DEFAULT_WHATSAPP_BIND_PORT = 8098
DEFAULT_WECHAT_BIND_PORT = 8097
DEFAULT_KAKAO_BIND_PORT = 8096
DEFAULT_SLACK_BIND_PORT = 8095
DEFAULT_FEISHU_BIND_PORT = 8094
MIN_BIND_PORT = 1
MAX_BIND_PORT = 65535

DEFAULT_SLACK_OAUTH_STATE_TTL_SEC = 600
MIN_SLACK_OAUTH_STATE_TTL_SEC = 60
MAX_SLACK_OAUTH_STATE_TTL_SEC = 3600

DEFAULT_RATE_LIMIT_USER_RPM = 10
DEFAULT_RATE_LIMIT_CHANNEL_RPM = 30
MIN_RATE_LIMIT_RPM = 1
MAX_RATE_LIMIT_RPM = 600

DEFAULT_MAX_COMMAND_LENGTH = 4096
MIN_MAX_COMMAND_LENGTH = 128
MAX_MAX_COMMAND_LENGTH = 32768

DEFAULT_MEDIA_TTL_SEC = 300
MIN_MEDIA_TTL_SEC = 60
MAX_MEDIA_TTL_SEC = 86400

DEFAULT_MEDIA_MAX_MB = 8
MIN_MEDIA_MAX_MB = 1
MAX_MEDIA_MAX_MB = 64


def _warn_default_env(
    env_key: str, raw_value: str, *, default: int, reason: str
) -> None:
    logger.warning(
        "Connector env %s=%r %s; using default %s.",
        env_key,
        raw_value,
        reason,
        default,
    )


def _warn_clamped_env(
    env_key: str,
    raw_value: str,
    *,
    bound_name: str,
    bound_value: int,
    resolved: int,
) -> None:
    logger.warning(
        (
            "Connector env %s=%r is below %s %s; clamped to %s."
            if bound_name == "minimum"
            else "Connector env %s=%r is above %s %s; clamped to %s."
        ),
        env_key,
        raw_value,
        bound_name,
        bound_value,
        resolved,
    )


def _load_bounded_int_env(
    env_key: str,
    *,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
    clamp: bool = True,
) -> int:
    raw_value = os.environ.get(env_key)
    if raw_value is None:
        return default
    raw_value = raw_value.strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        _warn_default_env(
            env_key,
            raw_value,
            default=default,
            reason="is not a valid integer",
        )
        return default

    if minimum is not None and value < minimum:
        if clamp:
            _warn_clamped_env(
                env_key,
                raw_value,
                bound_name="minimum",
                bound_value=minimum,
                resolved=minimum,
            )
            return minimum
        _warn_default_env(
            env_key,
            raw_value,
            default=default,
            reason=f"is outside supported range {minimum}..{maximum or 'inf'}",
        )
        return default

    if maximum is not None and value > maximum:
        if clamp:
            _warn_clamped_env(
                env_key,
                raw_value,
                bound_name="maximum",
                bound_value=maximum,
                resolved=maximum,
            )
            return maximum
        _warn_default_env(
            env_key,
            raw_value,
            default=default,
            reason=f"is outside supported range {minimum or '-inf'}..{maximum}",
        )
        return default

    return value


class CommandClass(str, Enum):
    PUBLIC = "public"  # status, help, tools
    RUN = "run"  # run (subject to approval flow)
    ADMIN = "admin"  # sensitive ops


@dataclass
class CommandPolicy:
    """
    R80: Authorization Matrix.
    Defines who can run what.
    """

    # Default AllowFrom lists (User IDs)
    # If empty for a class, it falls back to role checks (e.g. is_admin, is_trusted)
    allow_from: Dict[CommandClass, Set[str]] = field(default_factory=dict)

    # Command -> Class overrides
    # e.g. {"/custom": CommandClass.ADMIN}
    command_overrides: Dict[str, CommandClass] = field(default_factory=dict)


@dataclass
class ConnectorConfig:
    # OpenClaw Connection
    openclaw_url: str = "http://127.0.0.1:8188"
    admin_token: Optional[str] = None  # To call admin endpoints

    # Results Delivery
    delivery_enabled: bool = True
    delivery_max_images: int = DEFAULT_DELIVERY_MAX_IMAGES
    delivery_max_bytes: int = DEFAULT_DELIVERY_MAX_BYTES
    delivery_timeout_sec: int = DEFAULT_DELIVERY_TIMEOUT_SEC

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_allowed_users: List[int] = field(default_factory=list)
    telegram_allowed_chats: List[int] = field(default_factory=list)

    # Discord
    discord_bot_token: Optional[str] = None
    discord_allowed_users: List[str] = field(default_factory=list)
    discord_allowed_channels: List[str] = field(default_factory=list)

    # LINE
    line_channel_secret: Optional[str] = None
    line_channel_access_token: Optional[str] = None
    line_allowed_users: List[str] = field(default_factory=list)
    line_allowed_groups: List[str] = field(default_factory=list)
    line_bind_host: str = "127.0.0.1"
    line_bind_port: int = DEFAULT_LINE_BIND_PORT
    line_webhook_path: str = "/line/webhook"

    # WhatsApp
    whatsapp_access_token: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    whatsapp_app_secret: Optional[str] = None  # For signature verification
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_allowed_users: List[str] = field(default_factory=list)
    whatsapp_bind_host: str = "127.0.0.1"
    whatsapp_bind_port: int = DEFAULT_WHATSAPP_BIND_PORT
    whatsapp_webhook_path: str = "/whatsapp/webhook"

    # WeChat Official Account (R74/S31/F43)
    wechat_token: Optional[str] = None
    wechat_app_id: Optional[str] = None
    wechat_app_secret: Optional[str] = None
    wechat_encoding_aes_key: Optional[str] = None  # R82: AES encrypted mode
    wechat_allowed_users: List[str] = field(default_factory=list)
    wechat_bind_host: str = "127.0.0.1"
    wechat_bind_port: int = DEFAULT_WECHAT_BIND_PORT
    wechat_webhook_path: str = "/wechat/webhook"

    # KakaoTalk (F44 Phase A)
    kakao_enabled: bool = False
    kakao_bind_host: str = "127.0.0.1"
    kakao_bind_port: int = DEFAULT_KAKAO_BIND_PORT
    kakao_webhook_path: str = "/kakao/webhook"
    kakao_allowed_users: List[str] = field(default_factory=list)

    # Slack (F56 / S67)
    slack_bot_token: Optional[str] = None
    slack_signing_secret: Optional[str] = None
    slack_allowed_users: List[str] = field(default_factory=list)
    slack_allowed_channels: List[str] = field(default_factory=list)
    slack_bind_host: str = "127.0.0.1"
    slack_bind_port: int = DEFAULT_SLACK_BIND_PORT
    slack_webhook_path: str = "/slack/events"
    slack_interactions_path: str = "/slack/interactions"
    slack_require_mention: bool = True
    slack_reply_in_thread: bool = True
    slack_mode: str = "events"  # F57: events | socket
    slack_app_token: Optional[str] = None  # F57: required in socket mode (xapp-...)
    slack_client_id: Optional[str] = None
    slack_client_secret: Optional[str] = None
    slack_oauth_redirect_uri: Optional[str] = None
    slack_oauth_install_path: str = "/slack/install"
    slack_oauth_callback_path: str = "/slack/oauth/callback"
    slack_oauth_scopes: List[str] = field(
        default_factory=lambda: [
            "app_mentions:read",
            "channels:history",
            "chat:write",
            "files:write",
            "groups:history",
            "im:history",
            "mpim:history",
        ]
    )
    slack_oauth_state_ttl_sec: int = DEFAULT_SLACK_OAUTH_STATE_TTL_SEC

    # Feishu / Lark (F67)
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_account_id: Optional[str] = None
    feishu_default_account_id: Optional[str] = None
    feishu_workspace_id: Optional[str] = None
    feishu_workspace_name: Optional[str] = None
    feishu_bindings_json: Optional[str] = None
    feishu_allowed_users: List[str] = field(default_factory=list)
    feishu_allowed_chats: List[str] = field(default_factory=list)
    feishu_bind_host: str = "127.0.0.1"
    feishu_bind_port: int = DEFAULT_FEISHU_BIND_PORT
    feishu_webhook_path: str = "/feishu/events"
    feishu_callback_path: str = "/feishu/callback"
    feishu_domain: str = "feishu"  # feishu | lark
    feishu_mode: str = "websocket"  # websocket | webhook
    feishu_require_mention: bool = True
    feishu_reply_in_thread: bool = True

    # Privileged Access (ID match across platforms; Telegram Int vs Discord Str handled by router)
    admin_users: List[str] = field(default_factory=list)

    # Media Host (F33)
    public_base_url: Optional[str] = None
    media_path: str = "/media"
    media_ttl_sec: int = DEFAULT_MEDIA_TTL_SEC
    media_max_mb: int = DEFAULT_MEDIA_MAX_MB

    # Security (F32)
    rate_limit_user_rpm: int = (
        DEFAULT_RATE_LIMIT_USER_RPM  # Requests per minute per user
    )
    rate_limit_channel_rpm: int = (
        DEFAULT_RATE_LIMIT_CHANNEL_RPM  # Requests per minute per channel
    )
    max_command_length: int = (
        DEFAULT_MAX_COMMAND_LENGTH  # Max characters in a single command
    )
    llm_max_tokens_per_request: int = 1024  # LLM token budget

    # R80: Command Auth Policy
    command_policy: CommandPolicy = field(default_factory=CommandPolicy)

    # Global
    debug: bool = False
    state_path: Optional[str | PurePath] = None

    def __repr__(self):
        """R117: redact secret/token/key fields in logs and debug output."""
        d = self.__dict__.copy()
        for k in d:
            if "token" in k or "secret" in k or "key" in k:
                if d[k]:
                    d[k] = "***REDACTED***"
        fields = ", ".join(f"{k}={v!r}" for k, v in d.items())
        return f"{self.__class__.__name__}({fields})"


def load_config() -> ConnectorConfig:
    """Load configuration from environment variables."""
    cfg = ConnectorConfig()

    cfg.openclaw_url = os.environ.get(
        "OPENCLAW_CONNECTOR_URL", "http://127.0.0.1:8188"
    ).rstrip("/")
    cfg.admin_token = os.environ.get("OPENCLAW_CONNECTOR_ADMIN_TOKEN")
    cfg.debug = os.environ.get("OPENCLAW_CONNECTOR_DEBUG", "0") == "1"
    cfg.state_path = os.environ.get("OPENCLAW_CONNECTOR_STATE_PATH")

    # Delivery
    cfg.delivery_max_images = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_DELIVERY_MAX_IMAGES",
        default=DEFAULT_DELIVERY_MAX_IMAGES,
        minimum=MIN_DELIVERY_MAX_IMAGES,
        maximum=MAX_DELIVERY_MAX_IMAGES,
    )
    cfg.delivery_max_bytes = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_DELIVERY_MAX_BYTES",
        default=DEFAULT_DELIVERY_MAX_BYTES,
        minimum=MIN_DELIVERY_MAX_BYTES,
        maximum=MAX_DELIVERY_MAX_BYTES,
    )
    cfg.delivery_timeout_sec = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC",
        default=DEFAULT_DELIVERY_TIMEOUT_SEC,
        minimum=MIN_DELIVERY_TIMEOUT_SEC,
        maximum=MAX_DELIVERY_TIMEOUT_SEC,
    )

    # Telegram
    cfg.telegram_bot_token = os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_TOKEN")
    if t_users := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS"):
        cfg.telegram_allowed_users = [
            int(u.strip()) for u in t_users.split(",") if u.strip().isdigit()
        ]
    if t_chats := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS"):
        cfg.telegram_allowed_chats = [
            int(u.strip())
            for u in t_chats.split(",")
            if u.strip().lstrip("-").isdigit()
        ]

    # Discord
    cfg.discord_bot_token = os.environ.get("OPENCLAW_CONNECTOR_DISCORD_TOKEN")
    if d_users := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS"):
        cfg.discord_allowed_users = [u.strip() for u in d_users.split(",") if u.strip()]
    if d_chans := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS"):
        cfg.discord_allowed_channels = [
            u.strip() for u in d_chans.split(",") if u.strip()
        ]

    # LINE
    cfg.line_channel_secret = os.environ.get("OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET")
    cfg.line_channel_access_token = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN"
    )
    if l_users := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS"):
        cfg.line_allowed_users = [u.strip() for u in l_users.split(",") if u.strip()]
    if l_groups := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS"):
        cfg.line_allowed_groups = [u.strip() for u in l_groups.split(",") if u.strip()]

    cfg.line_bind_host = os.environ.get("OPENCLAW_CONNECTOR_LINE_BIND", "127.0.0.1")
    cfg.line_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_LINE_PORT",
        default=DEFAULT_LINE_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.line_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_PATH", "/line/webhook"
    )

    # WhatsApp
    cfg.whatsapp_access_token = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN"
    )
    cfg.whatsapp_verify_token = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN"
    )
    cfg.whatsapp_app_secret = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET")
    cfg.whatsapp_phone_number_id = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_PHONE_NUMBER_ID"
    )
    if wa_users := os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS"):
        cfg.whatsapp_allowed_users = [
            u.strip() for u in wa_users.split(",") if u.strip()
        ]
    cfg.whatsapp_bind_host = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_BIND", "127.0.0.1"
    )
    cfg.whatsapp_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_WHATSAPP_PORT",
        default=DEFAULT_WHATSAPP_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.whatsapp_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_PATH", "/whatsapp/webhook"
    )

    # WeChat Official Account (R74/S31/F43)
    cfg.wechat_token = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_TOKEN")
    cfg.wechat_app_id = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_APP_ID")
    cfg.wechat_app_secret = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_APP_SECRET")
    cfg.wechat_encoding_aes_key = os.environ.get(
        "OPENCLAW_CONNECTOR_WECHAT_ENCODING_AES_KEY"
    )
    if wc_users := os.environ.get("OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS"):
        cfg.wechat_allowed_users = [u.strip() for u in wc_users.split(",") if u.strip()]
    cfg.wechat_bind_host = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_BIND", "127.0.0.1")
    cfg.wechat_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_WECHAT_PORT",
        default=DEFAULT_WECHAT_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.wechat_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_WECHAT_PATH", "/wechat/webhook"
    )

    # KakaoTalk (F44)
    if os.environ.get("OPENCLAW_CONNECTOR_KAKAO_ENABLED", "").lower() == "true":
        cfg.kakao_enabled = True

    cfg.kakao_bind_host = os.environ.get("OPENCLAW_CONNECTOR_KAKAO_BIND", "127.0.0.1")
    cfg.kakao_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_KAKAO_PORT",
        default=DEFAULT_KAKAO_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.kakao_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_KAKAO_PATH", "/kakao/webhook"
    )
    if ku := os.environ.get("OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS"):
        cfg.kakao_allowed_users = [u.strip() for u in ku.split(",") if u.strip()]

    # Slack (F56 / S67)
    cfg.slack_bot_token = os.environ.get("OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN")
    cfg.slack_signing_secret = os.environ.get("OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET")
    if su := os.environ.get("OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS"):
        cfg.slack_allowed_users = [u.strip() for u in su.split(",") if u.strip()]
    if sc := os.environ.get("OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS"):
        cfg.slack_allowed_channels = [u.strip() for u in sc.split(",") if u.strip()]
    cfg.slack_bind_host = os.environ.get("OPENCLAW_CONNECTOR_SLACK_BIND", "127.0.0.1")
    cfg.slack_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_SLACK_PORT",
        default=DEFAULT_SLACK_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.slack_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_PATH", "/slack/events"
    )
    cfg.slack_interactions_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH", "/slack/interactions"
    )
    if (
        os.environ.get("OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION", "").lower()
        == "false"
    ):
        cfg.slack_require_mention = False
    if (
        os.environ.get("OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD", "").lower()
        == "false"
    ):
        cfg.slack_reply_in_thread = False
    cfg.slack_mode = os.environ.get("OPENCLAW_CONNECTOR_SLACK_MODE", "events").lower()
    cfg.slack_app_token = os.environ.get("OPENCLAW_CONNECTOR_SLACK_APP_TOKEN")
    cfg.slack_client_id = os.environ.get("OPENCLAW_CONNECTOR_SLACK_CLIENT_ID")
    cfg.slack_client_secret = os.environ.get("OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET")
    cfg.slack_oauth_redirect_uri = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_REDIRECT_URI"
    )
    cfg.slack_oauth_install_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_INSTALL_PATH", "/slack/install"
    )
    cfg.slack_oauth_callback_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH", "/slack/oauth/callback"
    )
    if slack_scopes := os.environ.get("OPENCLAW_CONNECTOR_SLACK_OAUTH_SCOPES"):
        parsed_scopes = [
            scope.strip() for scope in slack_scopes.split(",") if scope.strip()
        ]
        if parsed_scopes:
            cfg.slack_oauth_scopes = parsed_scopes
    cfg.slack_oauth_state_ttl_sec = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_STATE_TTL_SEC",
        default=DEFAULT_SLACK_OAUTH_STATE_TTL_SEC,
        minimum=MIN_SLACK_OAUTH_STATE_TTL_SEC,
        maximum=MAX_SLACK_OAUTH_STATE_TTL_SEC,
    )

    # Feishu / Lark (F67)
    cfg.feishu_app_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_APP_ID")
    cfg.feishu_app_secret = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_APP_SECRET")
    cfg.feishu_verification_token = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN"
    )
    cfg.feishu_encrypt_key = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ENCRYPT_KEY")
    cfg.feishu_account_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ACCOUNT_ID")
    cfg.feishu_default_account_id = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_DEFAULT_ACCOUNT_ID"
    )
    cfg.feishu_workspace_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_ID")
    cfg.feishu_workspace_name = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_NAME"
    )
    cfg.feishu_bindings_json = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON")
    if fu := os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS"):
        cfg.feishu_allowed_users = [u.strip() for u in fu.split(",") if u.strip()]
    if fc := os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS"):
        cfg.feishu_allowed_chats = [u.strip() for u in fc.split(",") if u.strip()]
    cfg.feishu_bind_host = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_BIND", "127.0.0.1")
    cfg.feishu_bind_port = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_FEISHU_PORT",
        default=DEFAULT_FEISHU_BIND_PORT,
        minimum=MIN_BIND_PORT,
        maximum=MAX_BIND_PORT,
        clamp=False,
    )
    cfg.feishu_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_PATH", "/feishu/events"
    )
    cfg.feishu_callback_path = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH", "/feishu/callback"
    )
    cfg.feishu_domain = (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_DOMAIN", "feishu").strip() or "feishu"
    )
    cfg.feishu_mode = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_MODE", "websocket"
    ).lower()
    if (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_REQUIRE_MENTION", "").lower()
        == "false"
    ):
        cfg.feishu_require_mention = False
    if (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_REPLY_IN_THREAD", "").lower()
        == "false"
    ):
        cfg.feishu_reply_in_thread = False

    # Admin
    if admins := os.environ.get("OPENCLAW_CONNECTOR_ADMIN_USERS"):
        cfg.admin_users = [u.strip() for u in admins.split(",") if u.strip()]

    # Security (F32)
    cfg.rate_limit_user_rpm = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM",
        default=DEFAULT_RATE_LIMIT_USER_RPM,
        minimum=MIN_RATE_LIMIT_RPM,
        maximum=MAX_RATE_LIMIT_RPM,
    )
    cfg.rate_limit_channel_rpm = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_RATE_LIMIT_CHANNEL_RPM",
        default=DEFAULT_RATE_LIMIT_CHANNEL_RPM,
        minimum=MIN_RATE_LIMIT_RPM,
        maximum=MAX_RATE_LIMIT_RPM,
    )
    cfg.max_command_length = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH",
        default=DEFAULT_MAX_COMMAND_LENGTH,
        minimum=MIN_MAX_COMMAND_LENGTH,
        maximum=MAX_MAX_COMMAND_LENGTH,
    )

    # Media Host (F33)
    cfg.public_base_url = os.environ.get("OPENCLAW_CONNECTOR_PUBLIC_BASE_URL")
    cfg.media_path = os.environ.get("OPENCLAW_CONNECTOR_MEDIA_PATH", "/media")
    cfg.media_ttl_sec = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_MEDIA_TTL_SEC",
        default=DEFAULT_MEDIA_TTL_SEC,
        minimum=MIN_MEDIA_TTL_SEC,
        maximum=MAX_MEDIA_TTL_SEC,
    )
    cfg.media_max_mb = _load_bounded_int_env(
        "OPENCLAW_CONNECTOR_MEDIA_MAX_MB",
        default=DEFAULT_MEDIA_MAX_MB,
        minimum=MIN_MEDIA_MAX_MB,
        maximum=MAX_MEDIA_MAX_MB,
    )

    # R80: Command Auth Policy
    import json

    # 1. Overrides (JSON dict)
    if overrides_json := os.environ.get("OPENCLAW_COMMAND_OVERRIDES"):
        try:
            overrides = json.loads(overrides_json)
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    try:
                        # Normalize command key (lowercase, ensure leading slash)
                        k = k.strip().lower()
                        if not k.startswith("/"):
                            k = "/" + k

                        # Map string value to enum
                        if isinstance(v, str):
                            v = CommandClass(v.lower())
                        cfg.command_policy.command_overrides[k] = v
                    except ValueError:
                        pass  # Invalid enum value, ignore
        except json.JSONDecodeError:
            pass  # Invalid JSON, ignore

    # 2. AllowFrom Lists (start with empty sets)
    # Env vars: OPENCLAW_COMMAND_ALLOW_FROM_ADMIN=user1,user2
    #           OPENCLAW_COMMAND_ALLOW_FROM_RUN=user3
    #           OPENCLAW_COMMAND_ALLOW_FROM_PUBLIC=...
    for cmd_class in CommandClass:
        env_key = f"OPENCLAW_COMMAND_ALLOW_FROM_{cmd_class.value.upper()}"
        if val := os.environ.get(env_key):
            users = {u.strip() for u in val.split(",") if u.strip()}
            if users:
                if cmd_class not in cfg.command_policy.allow_from:
                    cfg.command_policy.allow_from[cmd_class] = set()
                cfg.command_policy.allow_from[cmd_class].update(users)

    return cfg
