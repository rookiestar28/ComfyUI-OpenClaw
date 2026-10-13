"""
Connector Entrypoint (F29).
Runs the connector process properly.
"""

import asyncio
import logging
import sys

from .config import load_config
from .openclaw_client import OpenClawClient
from .platforms.discord_gateway import DiscordGateway
from .platforms.feishu_installation_manager import FeishuInstallationManager
from .platforms.feishu_long_connection import FeishuLongConnectionClient
from .platforms.feishu_webhook import FeishuWebhookServer
from .platforms.kakao_webhook import KakaoWebhookServer
from .platforms.line_webhook import LINEWebhookServer
from .platforms.slack_webhook import SlackWebhookServer
from .platforms.telegram_polling import TelegramPolling
from .platforms.wechat_webhook import WeChatWebhookServer
from .platforms.whatsapp_webhook import WhatsAppWebhookServer
from .results_poller import ResultsPoller
from .router import CommandRouter
from .schedule_delivery import ScheduleDeliveryWorker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("connector")


def _print_security_banner(config):
    """
    F32 WP1: Print security warning when allowlists are empty.
    Fail-closed: empty allowlists = all users treated as untrusted.
    """
    has_trusted_users = bool(
        config.telegram_allowed_users
        or config.telegram_allowed_chats
        or config.discord_allowed_users
        or config.discord_allowed_channels
        or config.line_allowed_users
        or config.line_allowed_groups
        or config.whatsapp_allowed_users
        or config.wechat_allowed_users
        or config.kakao_allowed_users
        or config.slack_allowed_users
        or config.feishu_allowed_users
    )
    has_admins = bool(config.admin_users)

    if not has_trusted_users:
        logger.warning("=" * 60)
        logger.warning("⚠️  SECURITY: No trusted users configured.")
        logger.warning("⚠️  All /run commands will require approval.")
        logger.warning("⚠️  Set OPENCLAW_CONNECTOR_*_ALLOWED_USERS to enable auto-exec.")
        logger.warning("=" * 60)

    if not has_admins:
        logger.warning("⚠️  No admin users configured (OPENCLAW_CONNECTOR_ADMIN_USERS).")
        logger.warning(
            "⚠️  Admin commands (/approve, /reject, etc.) will be unavailable."
        )

    if not config.admin_token:
        logger.warning("⚠️  No admin token configured (OPENCLAW_CONNECTOR_ADMIN_TOKEN).")
        logger.warning(
            "⚠️  Admin commands will fail if OpenClaw Server requires authentication."
        )


async def main():
    logger.info("Initializing OpenClaw Connector (Phase 5)...")

    # 1. Config
    try:
        config = load_config()
    except Exception as e:
        logger.critical(f"Config load failed: {e}")
        return

    if config.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("connector").setLevel(logging.DEBUG)
        logger.debug("Debug mode enabled")

    # F32 WP1: Security warning banner when no trusted users configured
    _print_security_banner(config)

    # 2. Components
    client = OpenClawClient(config)
    await client.start()  # Start session

    # Shared Platforms Registry
    platforms = {}

    # Initialize Poller
    poller = ResultsPoller(config, client, platforms)

    # Initialize Router with Poller
    router = CommandRouter(config, client, poller=poller)

    tasks = []
    # Start Poller
    tasks.append(asyncio.create_task(poller.start()))

    line_server = None
    whatsapp_server = None
    wechat_server = None
    kakao_server = None
    slack_server = None
    feishu_server = None
    feishu_long_clients = []

    # 3. Platforms
    if config.telegram_bot_token:
        tg = TelegramPolling(config, router)
        platforms["telegram"] = tg
        tasks.append(asyncio.create_task(tg.start()))
    else:
        logger.info(
            "Telegram not configured (OPENCLAW_CONNECTOR_TELEGRAM_TOKEN missing)"
        )

    if config.discord_bot_token:
        dc = DiscordGateway(config, router)
        platforms["discord"] = dc
        tasks.append(asyncio.create_task(dc.start()))
    else:
        logger.info("Discord not configured (OPENCLAW_CONNECTOR_DISCORD_TOKEN missing)")

    if config.line_channel_secret and config.line_channel_access_token:
        line_server = LINEWebhookServer(config, router)
        platforms["line"] = line_server
        await line_server.start()
        # If only LINE is active, tasks will be empty. Add a sleeper to keep loop alive.
        if not tasks:
            tasks.append(
                asyncio.create_task(asyncio.sleep(3600 * 24 * 365))
            )  # Sleep forever
    elif config.line_channel_secret:
        logger.warning("LINE configured but Access Token missing. Skipping.")
    else:
        logger.info(
            "LINE not configured (OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET missing)"
        )

    if config.whatsapp_access_token and config.whatsapp_verify_token:
        whatsapp_server = WhatsAppWebhookServer(config, router)
        platforms["whatsapp"] = whatsapp_server
        await whatsapp_server.start()
        # If only WhatsApp is active, add sleeper
        if not tasks:
            tasks.append(asyncio.create_task(asyncio.sleep(3600 * 24 * 365)))
    elif config.whatsapp_access_token:
        logger.warning("WhatsApp configured but Verify Token missing. Skipping.")
    else:
        logger.info(
            "WhatsApp not configured (OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN missing)"
        )

    if config.wechat_token:
        wechat_server = WeChatWebhookServer(config, router)
        platforms["wechat"] = wechat_server
        await wechat_server.start()
        # If only WeChat is active, add sleeper
        if not tasks:
            tasks.append(asyncio.create_task(asyncio.sleep(3600 * 24 * 365)))
    else:
        logger.info("WeChat not configured (OPENCLAW_CONNECTOR_WECHAT_TOKEN missing)")

    if config.kakao_enabled:
        kakao_server = KakaoWebhookServer(config, router)
        platforms["kakao"] = kakao_server
        await kakao_server.start()
        if not tasks:
            tasks.append(asyncio.create_task(asyncio.sleep(3600 * 24 * 365)))
    else:
        logger.info("Kakao adapter disabled.")

    if config.slack_bot_token and config.slack_signing_secret:
        if config.slack_mode == "socket":
            # CRITICAL: Socket Mode must remain explicit opt-in; do not auto-fallback
            # from webhook mode or security/ingress assumptions can drift silently.
            from .platforms.slack_socket_mode import SlackSocketModeClient

            slack_server = SlackSocketModeClient(config, router)
        elif config.slack_mode == "events":
            slack_server = SlackWebhookServer(config, router)
        else:
            logger.error(
                "Invalid OPENCLAW_CONNECTOR_SLACK_MODE=%r. Expected 'events' or 'socket'.",
                config.slack_mode,
            )
            slack_server = None
            logger.error("Slack adapter startup aborted (fail-closed).")
            # Keep connector alive for other platforms; Slack remains disabled.
            # If Slack is the only platform, global no-platform guard below will exit.
            pass

        if slack_server is None:
            logger.warning("Slack adapter disabled due to invalid mode config.")
        else:
            platforms["slack"] = slack_server
            await slack_server.start()
            if not tasks:
                tasks.append(asyncio.create_task(asyncio.sleep(3600 * 24 * 365)))
    elif config.slack_bot_token:
        logger.warning("Slack configured but Signing Secret missing. Skipping.")
    else:
        logger.info("Slack not configured (OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN missing)")

    if config.feishu_bindings_json or (
        config.feishu_app_id and config.feishu_app_secret
    ):
        try:
            feishu_installation_manager = FeishuInstallationManager(config)
        except Exception as exc:
            logger.error("Feishu adapter startup aborted (fail-closed): %s", exc)
            feishu_installation_manager = None
        if config.feishu_mode == "webhook":
            if feishu_installation_manager is not None:
                feishu_server = FeishuWebhookServer(
                    config,
                    router,
                    installation_manager=feishu_installation_manager,
                )
        elif config.feishu_mode == "websocket":
            # CRITICAL: Feishu long-connection remains explicit opt-in; do not
            # auto-fallback across transports or ingress verification can drift.
            if feishu_installation_manager is not None:
                feishu_server = FeishuWebhookServer(
                    config,
                    router,
                    installation_manager=feishu_installation_manager,
                )
                binding_configs = feishu_installation_manager.binding_configs()
                if not binding_configs:
                    binding_configs = [config]
                for binding_config in binding_configs:
                    feishu_long_clients.append(
                        FeishuLongConnectionClient(
                            binding_config,
                            router,
                            installation_manager=feishu_installation_manager,
                            bound_account_id=str(
                                binding_config.feishu_account_id or ""
                            ).strip(),
                        )
                    )
        else:
            logger.error(
                "Invalid OPENCLAW_CONNECTOR_FEISHU_MODE=%r. Expected 'websocket' or 'webhook'.",
                config.feishu_mode,
            )
            feishu_server = None
            logger.error("Feishu adapter startup aborted (fail-closed).")

        if feishu_server is None and not feishu_long_clients:
            logger.warning("Feishu adapter disabled due to invalid mode config.")
        else:
            platforms["feishu"] = feishu_server or feishu_long_clients[0]
            if feishu_server is not None:
                await feishu_server.start()
            for feishu_long_client in feishu_long_clients:
                await feishu_long_client.start()
            if not tasks:
                tasks.append(asyncio.create_task(asyncio.sleep(3600 * 24 * 365)))
    elif config.feishu_app_id:
        logger.warning("Feishu configured but App Secret missing. Skipping.")
    else:
        logger.info("Feishu not configured (OPENCLAW_CONNECTOR_FEISHU_APP_ID missing)")

    if (
        not tasks
        and not line_server
        and not whatsapp_server
        and not wechat_server
        and not kakao_server
        and not slack_server
        and not feishu_server
        and not feishu_long_clients
    ):
        logger.error(
            "No platforms configured! Set TELEGRAM_TOKEN, DISCORD_TOKEN, "
            "LINE_SECRET, WHATSAPP_ACCESS_TOKEN, WECHAT_TOKEN, "
            "KAKAO_ENABLED, SLACK_BOT_TOKEN or FEISHU_APP_ID."
        )
        await client.close()
        return

    schedule_worker = ScheduleDeliveryWorker(config, client, poller, platforms)
    if schedule_worker.is_enabled():
        tasks.append(asyncio.create_task(schedule_worker.start()))
    else:
        logger.info(
            "Schedule delivery disabled (needs OPENCLAW_CONNECTOR_ADMIN_TOKEN and a "
            "Telegram, Discord, Slack or Feishu platform)."
        )

    # 4. Run Check
    logger.info(f"Connecting to ComfyUI at {config.openclaw_url}...")
    health = await client.get_health()
    if health.get("ok"):
        logger.info("✅ ComfyUI connection verified.")
    else:
        logger.warning(f"⚠️ Could not reach ComfyUI on startup: {health.get('error')}")

    # 5. Wait
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Connector stopping...")
    finally:
        if line_server:
            await line_server.stop()
        if whatsapp_server:
            await whatsapp_server.stop()
        if wechat_server:
            await wechat_server.stop()
        if kakao_server:
            await kakao_server.stop()
        if slack_server:
            await slack_server.stop()
        for feishu_long_client in feishu_long_clients:
            await feishu_long_client.stop()
        if feishu_server:
            await feishu_server.stop()
        await schedule_worker.stop()
        if poller:
            await poller.stop()
        await client.close()
        logger.info("Connector stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
