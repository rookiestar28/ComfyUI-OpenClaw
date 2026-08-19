"""S100 jobs endpoint authorization, tenant, privacy, and audit contract tests."""

from __future__ import annotations

import importlib
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

from services.endpoint_manifest import AuthTier, get_metadata
from tests.security_contract_assertions import assert_security_reject_contract

REPO_ROOT = Path(__file__).resolve().parents[1]


def _request(
    *,
    remote: str = "203.0.113.10",
    headers: dict[str, str] | None = None,
    path: str = "/openclaw/jobs",
    query: dict[str, str] | None = None,
):
    request = MagicMock()
    request.remote = remote
    request.headers = headers or {}
    request.query = query or {}
    request.path = path
    return request


def _decode(response) -> dict:
    return json.loads(response.text)


def _authoritative_empty_jobs() -> dict:
    return {
        "ok": True,
        "contract_version": 1,
        "jobs": [],
        "pagination": {
            "limit": 50,
            "offset": 0,
            "warnings": [],
            "total": 0,
            "has_more": False,
        },
        "source": {
            "adapter": "comfy_execution.jobs",
            "authority": "in_process",
        },
        "scan": {
            "window": 10000,
            "examined": 0,
            "excluded": 0,
            "malformed": 0,
            "truncated": False,
        },
    }


def _load_security_module(testcase: unittest.TestCase):
    try:
        return importlib.import_module("services.jobs_security")
    except ModuleNotFoundError:
        testcase.fail("services.jobs_security must define the S100 policy boundary")


@unittest.skipIf(web is None, "aiohttp not installed")
class TestJobsHandlerSecurity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        from api import routes

        self.adapter = patch.object(
            routes, "read_jobs", return_value=_authoritative_empty_jobs()
        )
        self.adapter.start()
        self.addCleanup(self.adapter.stop)

    async def test_remote_without_token_is_denied_with_triple_assert(self):
        from api import routes

        request = _request()
        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            response = await routes.jobs_handler(request)

        assert_security_reject_contract(
            self,
            response=response,
            expected_status=403,
            expected_code="jobs_admin_required",
            audit_mock=audit,
            expected_action="jobs.list",
            expected_outcome="deny",
            expected_audit_status=403,
            expected_reason="jobs_admin_required",
        )

    async def test_configured_admin_token_matrix(self):
        from api import routes

        os.environ["OPENCLAW_ADMIN_TOKEN"] = "configured-admin-value"
        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            allowed = await routes.jobs_handler(
                _request(headers={"X-OpenClaw-Admin-Token": "configured-admin-value"})
            )
            denied = await routes.jobs_handler(
                _request(headers={"X-OpenClaw-Admin-Token": "wrong-value"})
            )

        self.assertEqual(allowed.status, 200)
        self.assertEqual(_decode(allowed)["contract_version"], 1)
        self.assertEqual(denied.status, 403)
        self.assertEqual(_decode(denied)["error"], "jobs_admin_required")
        outcomes = [call.kwargs.get("outcome") for call in audit.call_args_list]
        self.assertIn("allow", outcomes)
        self.assertIn("deny", outcomes)

    async def test_loopback_same_origin_and_cross_origin_match_admin_policy(self):
        from api import routes

        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True),
        ):
            same_origin = await routes.jobs_handler(
                _request(
                    remote="127.0.0.1",
                    headers={"Sec-Fetch-Site": "same-origin"},
                )
            )
            cross_origin = await routes.jobs_handler(
                _request(
                    remote="127.0.0.1",
                    headers={"Sec-Fetch-Site": "cross-site"},
                )
            )

        self.assertEqual(same_origin.status, 200)
        self.assertEqual(cross_origin.status, 403)
        self.assertEqual(_decode(cross_origin)["error"], "jobs_admin_required")

    async def test_multi_tenant_requires_explicit_context(self):
        from api import routes

        os.environ.update(
            {
                "OPENCLAW_ADMIN_TOKEN": "configured-admin-value",
                "OPENCLAW_MULTI_TENANT_ENABLED": "1",
            }
        )
        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            response = await routes.jobs_handler(
                _request(headers={"X-OpenClaw-Admin-Token": "configured-admin-value"})
            )

        assert_security_reject_contract(
            self,
            response=response,
            expected_status=403,
            expected_code="tenant_required",
            audit_mock=audit,
            expected_action="jobs.list",
            expected_outcome="deny",
            expected_audit_status=403,
            expected_reason="tenant_required",
        )

    async def test_multi_tenant_exact_header_context_is_allowed(self):
        from api import routes

        os.environ.update(
            {
                "OPENCLAW_ADMIN_TOKEN": "configured-admin-value",
                "OPENCLAW_MULTI_TENANT_ENABLED": "1",
            }
        )
        request = _request(
            headers={
                "X-OpenClaw-Admin-Token": "configured-admin-value",
                "X-OpenClaw-Tenant-Id": "team-a",
            }
        )
        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            response = await routes.jobs_handler(request)

        self.assertEqual(response.status, 200)
        allow = [
            call.kwargs
            for call in audit.call_args_list
            if call.kwargs.get("outcome") == "allow"
        ]
        self.assertEqual(len(allow), 1)
        self.assertEqual(
            allow[0]["details"],
            {
                "reason": "jobs_listed",
                "returned_count": 0,
                "excluded_count": 0,
                "malformed_count": 0,
            },
        )

    async def test_multi_tenant_invalid_and_mismatched_contexts_are_audited(self):
        from api import routes

        os.environ.update(
            {
                "OPENCLAW_ADMIN_TOKEN": "configured-admin-value",
                "OPENCLAW_MULTI_TENANT_ENABLED": "1",
            }
        )
        cases = (
            (
                _request(
                    headers={
                        "X-OpenClaw-Admin-Token": "configured-admin-value",
                        "X-OpenClaw-Tenant-Id": "invalid tenant",
                    }
                ),
                None,
                "tenant_invalid",
            ),
            (
                _request(headers={"X-OpenClaw-Tenant-Id": "team-b"}),
                SimpleNamespace(token_id="kid-test", tenant_id="team-a"),
                "tenant_mismatch",
            ),
        )
        for request, forced_token, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with (
                    patch.object(routes, "check_rate_limit", return_value=True),
                    patch.object(routes, "emit_audit_event", create=True) as audit,
                    patch.object(
                        routes,
                        "resolve_token_info",
                        wraps=routes.resolve_token_info,
                    ) as resolve,
                    patch.object(
                        routes,
                        "require_admin_token",
                        wraps=routes.require_admin_token,
                    ) as require,
                ):
                    if forced_token is not None:
                        resolve.return_value = forced_token
                        require.return_value = (True, None)
                    response = await routes.jobs_handler(request)

                assert_security_reject_contract(
                    self,
                    response=response,
                    expected_status=403,
                    expected_code=expected_code,
                    audit_mock=audit,
                    expected_action="jobs.list",
                    expected_outcome="deny",
                    expected_audit_status=403,
                    expected_reason=expected_code,
                )

    async def test_rate_limit_returns_standard_contract_and_safe_audit(self):
        from api import routes

        request = _request(
            remote="127.0.0.1", headers={"Sec-Fetch-Site": "same-origin"}
        )
        with (
            patch.object(routes, "check_rate_limit", return_value=False),
            patch.object(routes, "build_rate_limit_response") as rate_response,
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            rate_response.return_value = web.json_response(
                {"ok": False, "error": "jobs_rate_limited"}, status=429
            )
            response = await routes.jobs_handler(request)

        self.assertEqual(response.status, 429)
        self.assertEqual(_decode(response)["error"], "jobs_rate_limited")
        rate_response.assert_called_once()
        event = audit.call_args.kwargs
        self.assertEqual(event["action"], "jobs.list")
        self.assertEqual(event["outcome"], "rate_limit")
        self.assertEqual(event["details"], {"reason": "jobs_rate_limited"})

    async def test_invalid_query_returns_triple_assert_error(self):
        from api import routes

        request = _request(
            remote="127.0.0.1",
            headers={"Sec-Fetch-Site": "same-origin"},
            query={"status": "mystery"},
        )
        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True) as audit,
        ):
            response = await routes.jobs_handler(request)

        assert_security_reject_contract(
            self,
            response=response,
            expected_status=400,
            expected_code="jobs_query_invalid",
            audit_mock=audit,
            expected_action="jobs.list",
            expected_outcome="error",
            expected_audit_status=400,
            expected_reason="jobs_query_invalid",
        )

    async def test_all_primary_browser_and_legacy_aliases_share_contract(self):
        from api import routes

        os.environ["OPENCLAW_ADMIN_TOKEN"] = "configured-admin-value"
        server = MagicMock()
        server.routes.get = MagicMock()
        server.routes.post = MagicMock()
        server.routes.put = MagicMock()
        server.routes.delete = MagicMock()
        server.app.router.add_route = MagicMock()

        with (
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True),
        ):
            routes.register_routes(server)
            registered = {
                call.args[1]: call.args[2]
                for call in server.app.router.add_route.call_args_list
                if call.args[0] == "GET" and call.args[1].endswith("/jobs")
            }
            bodies = []
            for path in (
                "/openclaw/jobs",
                "/api/openclaw/jobs",
                "/moltbot/jobs",
                "/api/moltbot/jobs",
            ):
                response = await registered[path](
                    _request(
                        path=path,
                        headers={"X-OpenClaw-Admin-Token": "configured-admin-value"},
                    )
                )
                self.assertEqual(response.status, 200)
                bodies.append(_decode(response))
                if "moltbot" in path:
                    self.assertEqual(response.headers["Deprecation"], "true")
                    self.assertEqual(
                        response.headers["X-OpenClaw-Canonical-Path"],
                        path.replace("moltbot", "openclaw"),
                    )

        self.assertTrue(all(body == bodies[0] for body in bodies[1:]))


class TestJobsTenantVisibility(unittest.TestCase):
    @staticmethod
    def _queue_record(owner=...):
        extra_data: dict = {}
        if owner is not ...:
            extra_data["openclaw"] = {"tenant_id": owner}
        return (1, "job-1", {"prompt": "secret"}, extra_data, ["1"])

    @staticmethod
    def _history_record(owner=...):
        extra_data: dict = {}
        if owner is not ...:
            extra_data["openclaw"] = {"tenant_id": owner}
        return {
            "prompt": (1, "job-1", {"prompt": "secret"}, extra_data, ["1"]),
            "outputs": {},
        }

    def test_single_tenant_keeps_native_unmarked_records(self):
        security = _load_security_module(self)
        record = self._queue_record()
        result = security.filter_visible_job_records(
            [record], source="queue", tenant_id="default", multi_tenant=False
        )
        self.assertEqual(result.records, (record,))
        self.assertEqual(result.excluded_count, 0)

    def test_multi_tenant_queue_matrix_is_exact_match_only(self):
        security = _load_security_module(self)
        exact = self._queue_record("team-a")
        records = [
            exact,
            self._queue_record("team-b"),
            self._queue_record(),
            self._queue_record(["team-a"]),
            ("malformed",),
        ]
        result = security.filter_visible_job_records(
            records, source="queue", tenant_id="team-a", multi_tenant=True
        )
        self.assertEqual(result.records, (exact,))
        self.assertEqual(result.excluded_count, 4)
        self.assertEqual(result.malformed_count, 2)

    def test_multi_tenant_history_matrix_is_exact_match_only(self):
        security = _load_security_module(self)
        exact = self._history_record("team-a")
        records = [
            exact,
            self._history_record("team-b"),
            self._history_record(),
            {"prompt": "malformed"},
        ]
        result = security.filter_visible_job_records(
            records, source="history", tenant_id="team-a", multi_tenant=True
        )
        self.assertEqual(result.records, (exact,))
        self.assertEqual(result.excluded_count, 3)
        self.assertEqual(result.malformed_count, 1)

    def test_unknown_source_fails_closed(self):
        security = _load_security_module(self)
        with self.assertRaises(security.JobsSecurityError) as ctx:
            security.filter_visible_job_records(
                [], source="unknown", tenant_id="team-a", multi_tenant=True
            )
        self.assertEqual(ctx.exception.code, "jobs_source_invalid")

    def test_registry_token_tenant_header_mismatch_fails_closed(self):
        security = _load_security_module(self)
        request = _request(headers={"X-OpenClaw-Tenant-Id": "team-b"})
        token = SimpleNamespace(token_id="kid-test", tenant_id="team-a")
        with patch.dict(os.environ, {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            with self.assertRaises(security.TenantBoundaryError) as ctx:
                with security.jobs_request_tenant_scope(request, token):
                    pass
        self.assertEqual(ctx.exception.code, "tenant_mismatch")


class TestJobsQueryContract(unittest.TestCase):
    def test_defaults_match_frozen_contract(self):
        security = _load_security_module(self)
        self.assertTrue(
            hasattr(security, "normalize_jobs_query"),
            "S100 must expose bounded jobs query normalization",
        )
        query = security.normalize_jobs_query({})
        self.assertIsNone(query.status)
        self.assertIsNone(query.workflow_id)
        self.assertEqual(query.sort_by, "created_at")
        self.assertEqual(query.sort_order, "desc")
        self.assertEqual(query.limit, 50)
        self.assertEqual(query.offset, 0)
        self.assertEqual(query.warnings, ())

    def test_limit_offset_clamp_and_warnings_are_bounded(self):
        security = _load_security_module(self)
        self.assertTrue(hasattr(security, "normalize_jobs_query"))
        query = security.normalize_jobs_query(
            {"limit": "999999", "offset": "999999999999999999999999999"}
        )
        self.assertEqual(query.limit, 200)
        self.assertEqual(query.offset, 10000)
        warnings = query.to_pagination()["warnings"]
        self.assertEqual(
            {warning["code"] for warning in warnings},
            {"R95_LIMIT_CLAMPED", "R95_OFFSET_CLAMPED"},
        )
        encoded = json.dumps(warnings, sort_keys=True)
        self.assertNotIn("999999999999999999999999999", encoded)
        self.assertNotIn('"raw"', encoded)

        invalid = security.normalize_jobs_query({"limit": "not-an-int"})
        self.assertEqual(invalid.limit, 50)
        self.assertEqual(
            invalid.to_pagination()["warnings"],
            [
                {
                    "code": "R95_INVALID_LIMIT",
                    "field": "limit",
                    "normalized": 50,
                }
            ],
        )

    def test_invalid_enums_filters_and_unknown_fields_fail_closed(self):
        security = _load_security_module(self)
        self.assertTrue(hasattr(security, "normalize_jobs_query"))
        cases = (
            {"status": "unknown"},
            {"sort_by": "priority"},
            {"sort_order": "sideways"},
            {"workflow_id": "x" * 129},
            {"unexpected": "field"},
        )
        for payload in cases:
            with self.subTest(field=next(iter(payload))):
                with self.assertRaises(security.JobsSecurityError) as ctx:
                    security.normalize_jobs_query(payload)
                self.assertEqual(ctx.exception.code, "jobs_query_invalid")

    def test_audit_detail_builder_is_content_free_for_all_outcomes(self):
        security = _load_security_module(self)
        self.assertTrue(
            hasattr(security, "build_jobs_audit_details"),
            "S100 must expose a content-free jobs audit detail builder",
        )
        for reason in (
            "jobs_listed",
            "jobs_admin_required",
            "jobs_rate_limited",
            "jobs_host_contract_unsupported",
            "jobs_backend_unavailable",
        ):
            with self.subTest(reason=reason):
                details = security.build_jobs_audit_details(
                    reason,
                    returned_count=5,
                    excluded_count=2,
                    malformed_count=1,
                    ignored_secret="job-secret-id",
                )
                self.assertEqual(details["reason"], reason)
                self.assertEqual(
                    set(details),
                    {
                        "reason",
                        "returned_count",
                        "excluded_count",
                        "malformed_count",
                    },
                )
                self.assertNotIn("job-secret-id", json.dumps(details))

        fallback = security.build_jobs_audit_details(
            "hostile-secret-reason",
            returned_count=10001,
            excluded_count=-2,
            malformed_count=True,
            another_count="4",
        )
        self.assertEqual(
            fallback,
            {
                "reason": "jobs_error",
                "returned_count": 10000,
                "excluded_count": 0,
            },
        )

    def test_audit_emitter_supports_degraded_outcomes_without_content(self):
        from api import routes

        self.assertTrue(hasattr(routes, "_emit_jobs_list_audit"))
        request = _request()
        with patch.object(routes, "emit_audit_event") as audit:
            routes._emit_jobs_list_audit(
                request=request,
                token_info=None,
                outcome="unsupported",
                status_code=501,
                reason="jobs_host_contract_unsupported",
                returned_count=0,
                job_id="job-secret-id",
            )
            routes._emit_jobs_list_audit(
                request=request,
                token_info=None,
                outcome="unexpected",
                status_code=503,
                reason="hostile-secret-reason",
                payload="secret-payload",
            )

        unsupported, fallback = [call.kwargs for call in audit.call_args_list]
        self.assertEqual(unsupported["outcome"], "unsupported")
        self.assertEqual(
            unsupported["details"],
            {
                "reason": "jobs_host_contract_unsupported",
                "returned_count": 0,
            },
        )
        self.assertEqual(fallback["outcome"], "error")
        self.assertEqual(fallback["details"], {"reason": "jobs_error"})
        self.assertNotIn(
            "secret", json.dumps([unsupported["details"], fallback["details"]])
        )


class TestJobsProjectionPrivacy(unittest.TestCase):
    def test_projection_is_allowlist_only_under_hostile_nested_input(self):
        security = _load_security_module(self)
        hostile = {
            "id": "job-safe",
            "status": "completed",
            "priority": 1,
            "create_time": 100,
            "execution_start_time": 110,
            "execution_end_time": 120,
            "outputs_count": 2,
            "previewable_outputs_count": 1,
            "workflow_id": "workflow-safe",
            "prompt": {"text": "secret-prompt"},
            "workflow": {"nodes": ["secret-workflow"]},
            "extra_data": {"openclaw": {"tenant_id": "secret-tenant"}},
            "preview_output": {"filename": "secret.png"},
            "execution_error": {"traceback": "secret-traceback"},
            "trace_id": "secret-trace",
            "client_id": "secret-client",
            "reasoning": {"thinking": "secret-reasoning"},
            "internal": {"maintenance": "secret-internal"},
        }
        projected = security.project_job_summary(hostile)
        self.assertEqual(
            set(projected),
            {
                "id",
                "status",
                "priority",
                "create_time",
                "execution_start_time",
                "execution_end_time",
                "outputs_count",
                "workflow_id",
            },
        )
        encoded = json.dumps(projected, sort_keys=True)
        for secret in (
            "secret-prompt",
            "secret-workflow",
            "secret-tenant",
            "secret.png",
            "secret-traceback",
            "secret-trace",
            "secret-client",
            "secret-reasoning",
            "secret-internal",
        ):
            self.assertNotIn(secret, encoded)

    def test_projection_rejects_invalid_required_and_oversized_fields(self):
        security = _load_security_module(self)
        cases = (
            ("empty-id", {"id": "", "status": "pending"}, "jobs_record_invalid"),
            (
                "unknown-status",
                {"id": "job", "status": "unknown"},
                "jobs_record_invalid",
            ),
            (
                "unhashable-status",
                {"id": "job", "status": ["pending"]},
                "jobs_record_invalid",
            ),
            (
                "oversized-id",
                {"id": "x" * 129, "status": "pending"},
                "jobs_record_invalid",
            ),
            (
                "huge-priority",
                {"id": "job", "status": "pending", "priority": 2**4096},
                "jobs_record_invalid",
            ),
            (
                "negative-output-count",
                {"id": "job", "status": "pending", "outputs_count": -1},
                "jobs_record_invalid",
            ),
        )
        for label, payload, code in cases:
            with self.subTest(label=label):
                try:
                    security.project_job_summary(payload)
                except security.JobsSecurityError as exc:
                    self.assertEqual(exc.code, code)
                except Exception as exc:  # pragma: no cover - RED diagnostic path
                    self.fail(
                        f"projection leaked {type(exc).__name__} instead of JobsSecurityError"
                    )
                else:
                    self.fail("invalid projection unexpectedly succeeded")


class TestJobsContractGovernance(unittest.TestCase):
    def test_endpoint_metadata_is_admin(self):
        from api.routes import jobs_handler

        metadata = get_metadata(jobs_handler)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.auth_tier, AuthTier.ADMIN)

    def test_public_contract_and_generated_openapi_are_admin(self):
        api_contract = (REPO_ROOT / "docs/release/api_contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "| `GET` | `/jobs` | `/moltbot/jobs` | Admin |",
            api_contract,
        )

        openapi = (REPO_ROOT / "docs/openapi.yaml").read_text(encoding="utf-8")
        jobs_block = openapi.split("  /jobs:\n", 1)[1].split("\n  /", 1)[0]
        self.assertIn('x-openclaw-auth: "Admin"', jobs_block)
        self.assertIn('x-openclaw-auth-tier: "admin"', jobs_block)
        self.assertIn("OpenClawAdminToken:", jobs_block)
        self.assertNotIn("OpenClawObservabilityToken:", jobs_block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
