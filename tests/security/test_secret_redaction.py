import asyncio
import importlib
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import services.redaction as redaction_module
from services.audit import emit_audit_event
from services.idempotency_store import IdempotencyStore
from services.redaction import stable_redaction_tag
from services.request_ip import get_trusted_proxies
from services.safe_io import safe_request_json, safe_request_text_stream


class TestBridgeAuthRedaction(unittest.TestCase):
    def setUp(self):
        self.env_keys = [
            "OPENCLAW_BRIDGE_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "OPENCLAW_BRIDGE_MTLS_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_CERT_MAP",
        ]
        for key in self.env_keys:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in self.env_keys:
            os.environ.pop(key, None)

    def _reload_auth_module(self):
        import services.sidecar.auth as auth_module

        return importlib.reload(auth_module)

    def test_invalid_token_log_redacts_device_id(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "expected-token"
        auth_module = self._reload_auth_module()

        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "worker-1",
            "X-OpenClaw-Device-Token": "wrong-token",
        }

        with self.assertLogs("ComfyUI-OpenClaw.sidecar.auth", level="WARNING") as logs:
            is_valid, error, _ = auth_module.validate_device_token(req)

        self.assertFalse(is_valid)
        self.assertIn("invalid", error.lower())
        output = "\n".join(logs.output)
        self.assertNotIn("worker-1", output)
        self.assertIn("device:", output)

    def test_mtls_mismatch_log_redacts_cert_fingerprints(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "expected-token"
        os.environ["OPENCLAW_BRIDGE_MTLS_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_CERT_MAP"] = "worker-1:sha256_expected"
        auth_module = self._reload_auth_module()

        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "worker-1",
            "X-OpenClaw-Device-Token": "expected-token",
            "X-Client-Cert-Hash": "sha256_actual",
        }

        with self.assertLogs("ComfyUI-OpenClaw.sidecar.auth", level="WARNING") as logs:
            is_valid, error, _ = auth_module.validate_device_token(req)

        self.assertFalse(is_valid)
        self.assertIn("fingerprint mismatch", error.lower())
        output = "\n".join(logs.output)
        self.assertNotIn("worker-1", output)
        self.assertNotIn("sha256_actual", output)
        self.assertNotIn("sha256_expected", output)
        self.assertIn("cert:", output)


class TestBridgeWorkerRedaction(unittest.TestCase):
    def setUp(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "bridge-auth-sample"
        IdempotencyStore().clear()
        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

    def tearDown(self):
        for key in [
            "OPENCLAW_BRIDGE_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "MOLTBOT_BRIDGE_ENABLED",
            "MOLTBOT_BRIDGE_DEVICE_TOKEN",
        ]:
            os.environ.pop(key, None)
        IdempotencyStore().clear()

    def _make_auth_request(self, *, job_id: str, idempotency_key: str):
        req = MagicMock()
        req.method = "POST"
        req.path = f"/bridge/worker/result/{job_id}"
        req.headers = {
            "X-OpenClaw-Device-Id": "worker-1",
            "X-OpenClaw-Device-Token": "bridge-auth-sample",
            "X-OpenClaw-Scopes": "job:submit,job:status",
            "X-Idempotency-Key": idempotency_key,
        }
        req.query = {}
        req.match_info = {"job_id": job_id}
        req.json = AsyncMock(return_value={"status": "completed", "outputs": {}})
        return req

    def test_worker_result_cache_and_logs_redact_sensitive_ids(self):
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = self._make_auth_request(job_id="job-1", idempotency_key="idem-001")

        with self.assertLogs("ComfyUI-OpenClaw.api.bridge", level="INFO") as logs:
            resp = asyncio.run(handlers.worker_result_handler(req))

        self.assertEqual(resp.status, 201)
        self.assertEqual(
            handlers._worker_results["job-1"]["worker_id"],
            stable_redaction_tag("worker-1", label="device"),
        )
        output = "\n".join(logs.output)
        self.assertNotIn("worker-1", output)
        self.assertNotIn("device:", output)
        self.assertIn("Worker result accepted.", output)

    def test_duplicate_result_log_redacts_idempotency_key(self):
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req1 = self._make_auth_request(job_id="job-2", idempotency_key="idem-dup")
        req2 = self._make_auth_request(job_id="job-2", idempotency_key="idem-dup")

        asyncio.run(handlers.worker_result_handler(req1))

        with self.assertLogs("ComfyUI-OpenClaw.api.bridge", level="INFO") as logs:
            resp = asyncio.run(handlers.worker_result_handler(req2))

        self.assertEqual(resp.status, 200)
        output = "\n".join(logs.output)
        self.assertNotIn("idem-dup", output)
        self.assertIn("idem:", output)


class TestAuditRedaction(unittest.TestCase):
    def setUp(self):
        self.test_log = "test_s78_audit.log"
        self.test_key = f"{self.test_log}.key"
        self.path_patcher = patch("services.audit.AUDIT_LOG_PATH", self.test_log)
        self.hash_patcher = patch("services.audit._LAST_HASH", None)
        self.chain_key_patcher = patch("services.audit._AUDIT_CHAIN_KEY", None)
        self.tag_key_patcher = patch.object(
            redaction_module, "_REDACTION_TAG_KEY", b"s78-test-redaction-key"
        )
        self.path_patcher.start()
        self.hash_patcher.start()
        self.chain_key_patcher.start()
        self.tag_key_patcher.start()
        if os.path.exists(self.test_log):
            os.remove(self.test_log)
        if os.path.exists(self.test_key):
            os.remove(self.test_key)

    def tearDown(self):
        self.path_patcher.stop()
        self.hash_patcher.stop()
        self.chain_key_patcher.stop()
        self.tag_key_patcher.stop()
        if os.path.exists(self.test_log):
            os.remove(self.test_log)
        if os.path.exists(self.test_key):
            os.remove(self.test_key)

    def _read_entries(self):
        with open(self.test_log, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_audit_storage_redacts_actor_ip(self):
        emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="allow",
            status_code=200,
            details={"actor_ip": "1.2.3.4", "error": "Authorization: Bearer sk-secret"},
        )

        entries = self._read_entries()
        details = entries[0]["details"]
        self.assertNotIn("actor_ip", details)
        self.assertEqual(
            details["actor_ip_tag"], stable_redaction_tag("1.2.3.4", label="ip")
        )
        self.assertIn("***REDACTED***", details["error"])
        self.assertNotIn("1.2.3.4", json.dumps(entries[0]))

    def test_audit_storage_omits_token_derived_identifiers(self):
        class _Token:
            token_id = "adm-1"
            role = "admin"
            scopes = {"*"}

        emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="allow",
            token_info=_Token(),
            status_code=200,
            details={"actor_ip": "1.2.3.4"},
        )

        entries = self._read_entries()
        entry = entries[0]
        self.assertNotIn("token_id", entry)
        self.assertNotIn("token_tag", entry)
        self.assertNotIn("auth_context", entry)
        self.assertNotIn("role", entry)
        self.assertNotIn("scope", entry)
        self.assertNotIn("scopes", entry)
        self.assertNotIn("adm-1", json.dumps(entry))

    def test_audit_logger_omits_raw_token_id(self):
        class _Token:
            token_id = "adm-2"
            role = "admin"
            scopes = {"*"}

        with self.assertLogs("ComfyUI-OpenClaw.services.audit", level="INFO") as logs:
            emit_audit_event(
                action="config.update",
                target="config.json",
                outcome="allow",
                token_info=_Token(),
                status_code=200,
                details={"actor_ip": "1.2.3.4"},
            )

        output = "\n".join(logs.output)
        self.assertNotIn("adm-2", output)
        self.assertNotIn("auth=", output)
        self.assertIn("AUDIT action=config.update", output)


class TestStableRedactionTag(unittest.TestCase):
    def test_tag_is_keyed_and_stable_for_same_process_key(self):
        with patch.object(
            redaction_module, "_REDACTION_TAG_KEY", b"fixed-redaction-key"
        ):
            first = stable_redaction_tag("worker-1", label="device")
            second = stable_redaction_tag("worker-1", label="device")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("device:"))
        self.assertNotIn("worker-1", first)

    def test_tag_changes_when_key_changes(self):
        with patch.object(redaction_module, "_REDACTION_TAG_KEY", b"key-a"):
            first = stable_redaction_tag("worker-1", label="device")
        with patch.object(redaction_module, "_REDACTION_TAG_KEY", b"key-b"):
            second = stable_redaction_tag("worker-1", label="device")

        self.assertNotEqual(first, second)


class TestDefensiveLogRedaction(unittest.TestCase):
    def test_invalid_trusted_proxy_log_omits_raw_value(self):
        with patch.dict(
            os.environ,
            {"OPENCLAW_TRUSTED_PROXIES": "127.0.0.1,not-a-network"},
            clear=False,
        ):
            with self.assertLogs(
                "ComfyUI-OpenClaw.services.request_ip", level="WARNING"
            ) as logs:
                get_trusted_proxies()

        output = "\n".join(logs.output)
        self.assertIn("Invalid trusted proxy entry ignored.", output)
        self.assertNotIn("not-a-network", output)

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_json_log_omits_disallowed_header_name(
        self, mock_validate, mock_build
    ):
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"ok": true}'

        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build.return_value = mock_opener

        with self.assertLogs(
            "ComfyUI-OpenClaw.services.safe_io", level="DEBUG"
        ) as logs:
            out = safe_request_json(
                method="POST",
                url="https://example.com/test",
                json_body={"x": 1},
                headers={"Secret-Header": "blocked"},
                allow_hosts={"example.com"},
            )

        self.assertTrue(out["ok"])
        output = "\n".join(logs.output)
        self.assertIn("Skipping disallowed outbound header.", output)
        self.assertNotIn("Secret-Header", output)

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_stream_log_omits_disallowed_header_name(
        self, mock_validate, mock_build
    ):
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        class _FakeStreamResponse:
            def __init__(self):
                self.headers = {}
                self._lines = [b"data: ok\n", b""]

            def getcode(self):
                return 200

            def readline(self, _max_bytes):
                return self._lines.pop(0)

            def close(self):
                return None

        mock_opener = MagicMock()
        mock_opener.open.return_value = _FakeStreamResponse()
        mock_build.return_value = mock_opener

        with self.assertLogs(
            "ComfyUI-OpenClaw.services.safe_io", level="DEBUG"
        ) as logs:
            lines = list(
                safe_request_text_stream(
                    method="POST",
                    url="https://example.com/stream",
                    json_body={"x": 1},
                    headers={"Secret-Header": "blocked"},
                    allow_hosts={"example.com"},
                )
            )

        self.assertEqual(lines, ["data: ok\n"])
        output = "\n".join(logs.output)
        self.assertIn("Skipping disallowed outbound header.", output)
        self.assertNotIn("Secret-Header", output)
