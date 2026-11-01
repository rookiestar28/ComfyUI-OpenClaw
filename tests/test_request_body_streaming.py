"""Deterministic fragmented ingress, independent of socket buffering and OS."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import os
import time
import unittest
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.aiohttp_compat import read_bounded_body
from services.request_contracts import MAX_BODY_SIZE

CONSUMERS = (
    ("api.webhook_validate", "webhook_validate_handler", "error"),
    ("api.webhook_submit", "webhook_submit_handler", "error"),
    ("api.webhook", "webhook_handler", "code"),
    ("api.preflight_handler", "preflight_handler", "error"),
)


class FragmentedStream:
    """A read(n) source returning short chunks and then real EOF."""

    def __init__(self, body, chunk_size=7, error=None):
        self.body = body
        self.chunk_size = chunk_size
        self.error = error
        self.offset = 0
        self.budgets = []

    async def read(self, n):
        if n <= 0:
            raise AssertionError("body reads must have a positive finite budget")
        self.budgets.append(n)
        if self.error is not None:
            raise self.error
        chunk = self.body[self.offset : self.offset + min(n, self.chunk_size)]
        self.offset += len(chunk)
        return chunk


class FragmentedHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, consumer, stream, *, authenticated=False):
        module_name, handler_name, _ = consumer
        module = importlib.import_module(module_name)
        request = SimpleNamespace(
            headers={"Content-Type": "application/json"}, content=stream
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(module, "check_rate_limit", return_value=True)
            )
            stack.enter_context(
                patch("services.surface_guard.check_surface", return_value=None)
            )
            if module_name == "api.preflight_handler":
                auth = stack.enter_context(
                    patch.object(
                        module,
                        "require_admin_token",
                        return_value=(False, "probe_denied"),
                    )
                )
                downstream = stack.enter_context(
                    patch.object(module, "run_preflight_check")
                )
            else:
                auth = stack.enter_context(
                    patch.object(
                        module,
                        "require_auth",
                        return_value=(authenticated, "probe_denied"),
                    )
                )
                downstream = stack.enter_context(
                    patch.object(module, "WebhookJobRequest")
                )
            response = await getattr(module, handler_name)(request)
        return response, auth, downstream

    async def test_overflow_is_rejected_before_auth_or_downstream_work(self):
        body = b'{"pad":"' + b"x" * MAX_BODY_SIZE + b'"}'
        for consumer in CONSUMERS:
            with self.subTest(consumer=consumer[0]):
                stream = FragmentedStream(body, chunk_size=4093)
                response, auth, downstream = await self.invoke(consumer, stream)
                self.assertEqual(response.status, 413)
                self.assertEqual(
                    json.loads(response.body)[consumer[2]], "payload_too_large"
                )
                auth.assert_not_called()
                downstream.assert_not_called()
                self.assertEqual(stream.offset, MAX_BODY_SIZE + 1)
                self.assertTrue(all(0 < n <= MAX_BODY_SIZE + 1 for n in stream.budgets))

    async def test_below_and_equal_cap_reach_auth_with_complete_body(self):
        for size in (MAX_BODY_SIZE - 1, MAX_BODY_SIZE):
            body = b'{"pad":"' + b"x" * (size - 10) + b'"}'
            self.assertEqual(len(body), size)
            for consumer in CONSUMERS:
                with self.subTest(size=size, consumer=consumer[0]):
                    stream = FragmentedStream(body, chunk_size=4093)
                    response, auth, downstream = await self.invoke(consumer, stream)
                    self.assertEqual(
                        response.status,
                        403 if consumer[0].endswith("preflight_handler") else 401,
                    )
                    auth.assert_called_once()
                    if not consumer[0].endswith("preflight_handler"):
                        self.assertEqual(auth.call_args.args[1], body)
                    downstream.assert_not_called()
                    self.assertEqual(stream.offset, size)

    async def test_empty_and_fragmented_malformed_json_keep_error_contract(self):
        for body in (b"", b'{"broken":'):
            for consumer in CONSUMERS:
                with self.subTest(body=body, consumer=consumer[0]):
                    response, _, downstream = await self.invoke(
                        consumer, FragmentedStream(body), authenticated=True
                    )
                    self.assertEqual(response.status, 400)
                    self.assertEqual(
                        json.loads(response.body)[consumer[2]], "invalid_json"
                    )
                    downstream.assert_not_called()

    async def test_read_errors_keep_existing_contract(self):
        for consumer in CONSUMERS:
            with self.subTest(consumer=consumer[0]):
                response, auth, downstream = await self.invoke(
                    consumer,
                    FragmentedStream(b"", error=OSError("synthetic read failure")),
                )
                self.assertEqual(response.status, 400)
                expected = (
                    "invalid_json"
                    if consumer[0].endswith("preflight_handler")
                    else "read_error"
                )
                self.assertEqual(json.loads(response.body)[consumer[2]], expected)
                auth.assert_not_called()
                downstream.assert_not_called()

    async def test_cancellation_propagates(self):
        for consumer in CONSUMERS:
            with self.subTest(consumer=consumer[0]):
                with self.assertRaises(asyncio.CancelledError):
                    await self.invoke(
                        consumer, FragmentedStream(b"", error=asyncio.CancelledError())
                    )


class BoundedReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_overflow_byte_stops_without_consuming_the_tail(self):
        for size in (MAX_BODY_SIZE + 1, MAX_BODY_SIZE * 2):
            with self.subTest(size=size):
                stream = FragmentedStream(b"x" * size, chunk_size=4093)
                result = await read_bounded_body(stream, MAX_BODY_SIZE)
                self.assertEqual(len(result), MAX_BODY_SIZE + 1)
                self.assertEqual(stream.offset, MAX_BODY_SIZE + 1)

    async def test_zero_cap_and_early_eof(self):
        self.assertEqual(await read_bounded_body(FragmentedStream(b""), 0), b"")
        self.assertEqual(await read_bounded_body(FragmentedStream(b"xy"), 0), b"x")
        self.assertEqual(
            await read_bounded_body(FragmentedStream(b"abc", chunk_size=1), 10), b"abc"
        )

    async def test_negative_cap_is_rejected_without_reading(self):
        stream = FragmentedStream(b"abc")
        with self.assertRaises(ValueError):
            await read_bounded_body(stream, -1)
        self.assertEqual(stream.offset, 0)


class _ObservedContent:
    def __init__(self, content, first_read):
        self.content = content
        self.first_read = first_read

    async def read(self, n):
        chunk = await self.content.read(n)
        self.first_read.set()
        return chunk


class _ObservedRequest:
    def __init__(self, request, first_read):
        self.request = request
        self.content = _ObservedContent(request.content, first_read)

    def __getattr__(self, name):
        return getattr(self.request, name)


class ChunkedHttpTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "OPENCLAW_WEBHOOK_AUTH_MODE": "bearer",
                    "OPENCLAW_WEBHOOK_BEARER_TOKEN": "synthetic-stream-token",
                    "OPENCLAW_WEBHOOK_HMAC_SECRET": "synthetic-stream-secret",
                    "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
                },
            )
        )
        for module in ("api.webhook", "api.webhook_validate"):
            self.stack.enter_context(
                patch(f"{module}.check_rate_limit", return_value=True)
            )
        self.first_read = asyncio.Event()
        from api.webhook import webhook_handler
        from api.webhook_validate import webhook_validate_handler

        async def legacy(request):
            return await webhook_handler(_ObservedRequest(request, self.first_read))

        async def validate(request):
            return await webhook_validate_handler(
                _ObservedRequest(request, self.first_read)
            )

        app = web.Application(client_max_size=2 * MAX_BODY_SIZE)
        app.router.add_post("/webhook", legacy)
        app.router.add_post("/validate", validate)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def post_fragmented(self, route, body, headers):
        async def chunks():
            yield body[:7]
            # Release the remainder only after the server's real short read.
            await asyncio.wait_for(self.first_read.wait(), timeout=5)
            yield body[7:]

        return await asyncio.wait_for(
            self.client.post(
                route,
                data=chunks(),
                headers={"Content-Type": "application/json", **headers},
            ),
            timeout=10,
        )

    async def test_chunked_oversize_returns_413(self):
        body = b'{"pad":"' + b"x" * MAX_BODY_SIZE + b'"}'
        response = await self.post_fragmented(
            "/validate", body, {"Authorization": "Bearer synthetic-stream-token"}
        )
        self.assertEqual(response.status, 413)
        self.assertEqual((await response.json())["error"], "payload_too_large")

    async def assert_accepted_transaction(self, headers):
        payload = {
            "version": 1,
            "template_id": "stream_probe",
            "profile_id": "synthetic",
        }
        body = json.dumps(payload).encode()
        response = await self.post_fragmented("/webhook", body, headers(body))
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["normalized"]["template_id"], "stream_probe")
        self.assertEqual(result["normalized"]["profile_id"], "synthetic")

    async def test_chunked_valid_bearer_transaction_is_accepted(self):
        await self.assert_accepted_transaction(
            lambda _: {"Authorization": "Bearer synthetic-stream-token"}
        )

    async def test_chunked_hmac_checks_complete_body_with_replay_protection(self):
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_AUTH_MODE": "hmac"}):

            def headers(body):
                digest = hmac.new(
                    b"synthetic-stream-secret", body, hashlib.sha256
                ).hexdigest()
                return {
                    "X-OpenClaw-Signature": f"sha256={digest}",
                    "X-OpenClaw-Timestamp": str(int(time.time())),
                    "X-OpenClaw-Nonce": uuid.uuid4().hex,
                }

            await self.assert_accepted_transaction(headers)
