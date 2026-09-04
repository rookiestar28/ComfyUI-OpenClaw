"""Low-mock transactional coverage for connector trust boundaries."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api.webhook_validate import webhook_validate_handler
from connector.config import ConnectorConfig
from connector.contract import CommandResponse
from connector.platforms.discord_gateway import DiscordGateway
from connector.platforms.line_webhook import LINEWebhookServer
from connector.platforms.slack_webhook import SLACK_SIGNING_VERSION, SlackWebhookServer
from connector.platforms.telegram_polling import TelegramPolling
from connector.results_poller import ResultsPoller
from models.schemas import MAX_BODY_SIZE

WEBHOOK_TOKEN = "r247-synthetic-webhook-token"
LINE_SECRET = "r247-synthetic-line-secret"
SLACK_SECRET = "r247-synthetic-slack-secret"


class _RouterCapture:
    def __init__(self):
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return CommandResponse(text="")


def _line_signature(body: bytes) -> str:
    digest = hmac.new(LINE_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _slack_signature(body: bytes, timestamp: str) -> str:
    base = f"{SLACK_SIGNING_VERSION}:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(
        SLACK_SECRET.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{SLACK_SIGNING_VERSION}={digest}"


class TestHttpIngressTransactions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_r247_ingress_")
        self.env = patch.dict(
            os.environ,
            {
                "MOLTBOT_STATE_DIR": self.tmp.name,
                "OPENCLAW_WEBHOOK_AUTH_MODE": "bearer",
                "OPENCLAW_WEBHOOK_BEARER_TOKEN": WEBHOOK_TOKEN,
            },
            clear=False,
        )
        self.env.start()

        self.line_router = _RouterCapture()
        line_config = ConnectorConfig(
            line_channel_secret=LINE_SECRET,
            line_channel_access_token="r247-synthetic-line-access",
            state_path=str(Path(self.tmp.name) / "line-state.json"),
        )
        self.line = LINEWebhookServer(line_config, self.line_router)

        self.slack_router = _RouterCapture()
        slack_config = ConnectorConfig(
            slack_bot_token="xoxb-r247-synthetic",
            slack_signing_secret=SLACK_SECRET,
            slack_require_mention=False,
            state_path=str(Path(self.tmp.name) / "slack-state.json"),
        )
        self.slack = SlackWebhookServer(slack_config, self.slack_router)

        app = web.Application(client_max_size=2 * MAX_BODY_SIZE)
        app.router.add_post("/validate", webhook_validate_handler)
        app.router.add_post("/line", self.line.handle_webhook)
        app.router.add_post("/slack", self.slack.handle_event)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    async def test_canonical_webhook_contract_rejects_untrusted_shapes(self):
        wrong_type = await self.client.post(
            "/validate",
            data=b"not-json",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(wrong_type.status, 415)
        self.assertEqual((await wrong_type.json())["error"], "unsupported_media_type")

        oversized = await self.client.post(
            "/validate",
            data=b"{" + b'"pad":"' + (b"x" * (MAX_BODY_SIZE + 1)) + b'"}',
            headers={
                "Authorization": f"Bearer {WEBHOOK_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(oversized.status, 413)
        self.assertEqual((await oversized.json())["error"], "payload_too_large")

        malformed = await self.client.post(
            "/validate",
            data=b"{",
            headers={
                "Authorization": f"Bearer {WEBHOOK_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(malformed.status, 400)
        self.assertEqual((await malformed.json())["error"], "invalid_json")

        unauthorized = await self.client.post(
            "/validate",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(unauthorized.status, 401)
        self.assertEqual(
            (await unauthorized.json())["error"], "missing_authorization_header"
        )

    async def test_line_signature_parser_freshness_and_replay_transaction(self):
        now_ms = int(time.time() * 1000)
        payload = {
            "events": [
                {
                    "type": "message",
                    "timestamp": now_ms,
                    "webhookEventId": "line-r247-1",
                    "replyToken": "00000000000000000000000000000000",
                    "source": {"type": "user", "userId": "line-user-r247"},
                    "message": {"type": "text", "text": "/status"},
                }
            ]
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Line-Signature": _line_signature(body),
        }

        accepted = await self.client.post("/line", data=body, headers=headers)
        self.assertEqual(accepted.status, 200)
        self.assertEqual(len(self.line_router.requests), 1)
        self.assertEqual(self.line_router.requests[0].platform, "line")

        duplicate = await self.client.post("/line", data=body, headers=headers)
        self.assertEqual(duplicate.status, 403)
        self.assertEqual(len(self.line_router.requests), 1)

        invalid = await self.client.post(
            "/line",
            data=body,
            headers={"X-Line-Signature": "invalid"},
        )
        self.assertEqual(invalid.status, 401)

        malformed_body = b"{"
        malformed = await self.client.post(
            "/line",
            data=malformed_body,
            headers={"X-Line-Signature": _line_signature(malformed_body)},
        )
        self.assertEqual(malformed.status, 400)

        stale_payload = json.loads(json.dumps(payload))
        stale_payload["events"][0]["webhookEventId"] = "line-r247-stale"
        stale_payload["events"][0]["timestamp"] = now_ms - 301_000
        stale_body = json.dumps(stale_payload, separators=(",", ":")).encode("utf-8")
        stale = await self.client.post(
            "/line",
            data=stale_body,
            headers={"X-Line-Signature": _line_signature(stale_body)},
        )
        self.assertEqual(stale.status, 403)

    async def test_slack_signature_parser_freshness_and_replay_transaction(self):
        payload = {
            "type": "event_callback",
            "event_id": "slack-r247-1",
            "event": {
                "type": "message",
                "text": "/status",
                "user": "U_R247",
                "channel": "D_R247",
                "ts": "1609459200.1",
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _slack_signature(body, timestamp),
        }

        accepted = await self.client.post("/slack", data=body, headers=headers)
        self.assertEqual(accepted.status, 200)
        duplicate = await self.client.post("/slack", data=body, headers=headers)
        self.assertEqual(duplicate.status, 200)
        self.assertEqual(len(self.slack_router.requests), 1)
        self.assertEqual(self.slack_router.requests[0].platform, "slack")

        invalid = await self.client.post(
            "/slack",
            data=body,
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": "v0=invalid",
            },
        )
        self.assertEqual(invalid.status, 401)

        stale_timestamp = str(int(time.time()) - 301)
        stale = await self.client.post(
            "/slack",
            data=body,
            headers={
                "X-Slack-Request-Timestamp": stale_timestamp,
                "X-Slack-Signature": _slack_signature(body, stale_timestamp),
            },
        )
        self.assertEqual(stale.status, 401)

        malformed_body = b"{"
        malformed_timestamp = str(int(time.time()))
        malformed = await self.client.post(
            "/slack",
            data=malformed_body,
            headers={
                "X-Slack-Request-Timestamp": malformed_timestamp,
                "X-Slack-Signature": _slack_signature(
                    malformed_body, malformed_timestamp
                ),
            },
        )
        self.assertEqual(malformed.status, 400)


class TestProtocolIngressTransactions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_r247_protocol_")
        self.config = ConnectorConfig(
            discord_bot_token="discord-r247-synthetic",
            telegram_bot_token="telegram-r247-synthetic",
            state_path=str(Path(self.tmp.name) / "connector-state.json"),
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_gateway_and_polling_shapes_route_without_network(self):
        discord_router = _RouterCapture()
        discord = DiscordGateway(self.config, discord_router)
        discord._send_response = AsyncMock()
        await discord._process_message(
            {
                "id": "discord-message-r247",
                "channel_id": "discord-channel-r247",
                "content": "/status",
                "author": {
                    "id": "discord-user-r247",
                    "username": "synthetic-user",
                    "bot": False,
                },
            }
        )
        await discord._process_message(
            {
                "id": "discord-bot-r247",
                "channel_id": "discord-channel-r247",
                "content": "/status",
                "author": {"id": "bot-r247", "bot": True},
            }
        )
        self.assertEqual(len(discord_router.requests), 1)
        self.assertEqual(discord_router.requests[0].platform, "discord")
        discord._send_response.assert_awaited_once()

        telegram_router = _RouterCapture()
        telegram = TelegramPolling(self.config, telegram_router)
        telegram._send_response = AsyncMock(return_value=True)
        processed = await telegram._process_update(
            {
                "update_id": 247,
                "message": {
                    "message_id": 247,
                    "text": "/status",
                    "chat": {"id": 2470},
                    "from": {"id": 2471, "username": "synthetic-user"},
                },
            }
        )
        ignored = await telegram._process_update({"update_id": 248})
        self.assertTrue(processed)
        self.assertTrue(ignored)
        self.assertEqual(len(telegram_router.requests), 1)
        self.assertEqual(telegram_router.requests[0].platform, "telegram")
        telegram._send_response.assert_awaited_once()

    async def test_result_delivery_and_visibility_suppression_are_bounded(self):
        client = SimpleNamespace(
            get_history=AsyncMock(
                return_value={
                    "ok": True,
                    "data": {"prompt-r247": {"outputs": {}}},
                }
            )
        )
        platform = SimpleNamespace(send_message=AsyncMock(), send_image=AsyncMock())
        poller = ResultsPoller(self.config, client, {"discord": platform})

        await poller._poll_job(
            "prompt-r247",
            "discord",
            "channel-r247",
            "sender-r247",
        )
        platform.send_message.assert_awaited_once()

        await poller._send_text(
            "discord",
            "channel-r247",
            "must stay hidden",
            delivery_context={"reply_visibility": "internal"},
        )
        platform.send_message.assert_awaited_once()


class TestConnectorRegistryDecisions(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_or_incomplete_slack_config_never_constructs_adapter(self):
        import connector.__main__ as connector_main

        class _Client:
            def __init__(self, _config):
                self.closed = False

            async def start(self):
                return None

            async def get_health(self):
                return {"ok": False, "error": "synthetic-offline"}

            async def close(self):
                self.closed = True

        class _Poller:
            def __init__(self, _config, _client, _platforms):
                self.stopped = False

            async def start(self):
                raise asyncio.CancelledError()

            async def stop(self):
                self.stopped = True

        class _Router:
            def __init__(self, _config, _client, *, poller):
                self.poller = poller

        for config in (
            ConnectorConfig(
                slack_bot_token="xoxb-r247-synthetic",
                slack_signing_secret=SLACK_SECRET,
                slack_mode="invalid",
            ),
            ConnectorConfig(
                slack_bot_token="xoxb-r247-synthetic",
                slack_signing_secret=None,
                slack_mode="events",
            ),
        ):
            with self.subTest(
                mode=config.slack_mode, secret=config.slack_signing_secret
            ):
                with (
                    patch.object(connector_main, "load_config", return_value=config),
                    patch.object(connector_main, "OpenClawClient", _Client),
                    patch.object(connector_main, "ResultsPoller", _Poller),
                    patch.object(connector_main, "CommandRouter", _Router),
                    patch.object(
                        connector_main,
                        "SlackWebhookServer",
                        side_effect=AssertionError(
                            "disabled Slack adapter constructed"
                        ),
                    ) as slack_constructor,
                ):
                    await connector_main.main()
                slack_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
