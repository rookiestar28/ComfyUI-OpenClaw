"""R213 authoritative jobs read-model and route contract tests."""

from __future__ import annotations

import json
import os
import sys
import time
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:  # pragma: no cover
    web = None
    TestClient = TestServer = None


def _request(*, query: dict[str, str] | None = None):
    request = MagicMock()
    request.remote = "127.0.0.1"
    request.headers = {"Sec-Fetch-Site": "same-origin"}
    request.query = query or {}
    request.path = "/openclaw/jobs"
    return request


def _decode(response) -> dict:
    return json.loads(response.text)


def _queue_record(
    prompt_id: str = "job-running",
    *,
    priority: int = 1,
    create_time: int = 100,
    workflow_id: str = "workflow-a",
    tenant_id: str | None = "default",
):
    extra_data = {
        "create_time": create_time,
        "extra_pnginfo": {"workflow": {"id": workflow_id}},
    }
    if tenant_id is not None:
        extra_data["openclaw"] = {"tenant_id": tenant_id}
    return (
        priority,
        prompt_id,
        {"1": {"class_type": "SecretPromptNode"}},
        extra_data,
        ["1"],
        {"client_id": "secret-client"},
    )


def _history_record(
    prompt_id: str,
    *,
    outcome: str,
    create_time: int,
    start_time: int,
    end_time: int,
    workflow_id: str = "workflow-a",
    tenant_id: str | None = "default",
):
    extra_data = {
        "create_time": create_time,
        "extra_pnginfo": {"workflow": {"id": workflow_id}},
    }
    if tenant_id is not None:
        extra_data["openclaw"] = {"tenant_id": tenant_id}
    messages = [["execution_start", {"timestamp": start_time}]]
    if outcome == "completed":
        status_str = "success"
        messages.append(["execution_success", {"timestamp": end_time}])
    elif outcome == "cancelled":
        status_str = "error"
        messages.append(["execution_interrupted", {"timestamp": end_time}])
    else:
        status_str = "error"
        messages.append(
            [
                "execution_error",
                {"timestamp": end_time, "traceback": "secret-traceback"},
            ]
        )
    return {
        "prompt": (
            10,
            prompt_id,
            {"1": {"class_type": "SecretHistoryNode"}},
            extra_data,
            ["1"],
        ),
        "status": {"status_str": status_str, "messages": messages},
        "outputs": {
            "1": {
                "images": [
                    {
                        "filename": "secret-output.png",
                        "type": "output",
                    }
                ]
            }
        },
    }


class _PromptQueueFixture:
    def __init__(self, *, running=None, queued=None, history=None, failure=None):
        self.running = list(running or [])
        self.queued = list(queued or [])
        self.history = dict(history or {})
        self.failure = failure

    def get_current_queue_volatile(self):
        if self.failure:
            raise self.failure
        return (self.running, self.queued)

    def get_history(self):
        if self.failure:
            raise self.failure
        return self.history


def _normalize_queue(item, status: str) -> dict:
    priority, prompt_id, prompt, extra_data, _ = item
    return {
        "id": prompt_id,
        "status": status,
        "priority": priority,
        "create_time": extra_data.get("create_time"),
        "outputs_count": 0,
        "previewable_outputs_count": 0,
        "workflow_id": extra_data.get("extra_pnginfo", {})
        .get("workflow", {})
        .get("id"),
        "prompt": prompt,
        "extra_data": extra_data,
        "preview_output": {"filename": "secret-queue-preview.png"},
    }


def _normalize_history(prompt_id: str, item: dict) -> dict:
    priority, _, prompt, extra_data, _ = item["prompt"]
    status = item.get("status", {})
    status_str = status.get("status_str")
    messages = status.get("messages", [])
    interrupted = any(entry[0] == "execution_interrupted" for entry in messages)
    normalized_status = (
        "completed"
        if status_str == "success"
        else "cancelled" if interrupted else "failed"
    )
    start_time = None
    end_time = None
    for event_name, event_data in messages:
        if event_name == "execution_start":
            start_time = event_data.get("timestamp")
        elif event_name in {
            "execution_success",
            "execution_error",
            "execution_interrupted",
        }:
            end_time = event_data.get("timestamp")
    return {
        "id": prompt_id,
        "status": normalized_status,
        "priority": priority,
        "create_time": extra_data.get("create_time"),
        "execution_start_time": start_time,
        "execution_end_time": end_time,
        "outputs_count": 1,
        "previewable_outputs_count": 1,
        "workflow_id": extra_data.get("extra_pnginfo", {})
        .get("workflow", {})
        .get("id"),
        "prompt": prompt,
        "extra_data": extra_data,
        "preview_output": {
            "filename": "secret-output.png",
            "content": "secret-user-output",
        },
        "execution_error": {"traceback": "secret-traceback"},
    }


def _upstream_get_all_jobs(
    running,
    queued,
    history,
    status_filter=None,
    workflow_id=None,
    sort_by="created_at",
    sort_order="desc",
    limit=None,
    offset=0,
):
    allowed = set(
        status_filter or ("pending", "in_progress", "completed", "failed", "cancelled")
    )
    jobs = []
    if "in_progress" in allowed:
        jobs.extend(_normalize_queue(item, "in_progress") for item in running)
    if "pending" in allowed:
        jobs.extend(_normalize_queue(item, "pending") for item in queued)
    for prompt_id, item in history.items():
        job = _normalize_history(prompt_id, item)
        if job["status"] in allowed:
            jobs.append(job)
    if workflow_id:
        jobs = [job for job in jobs if job.get("workflow_id") == workflow_id]
    if sort_by == "execution_duration":
        key = lambda job: (job.get("execution_end_time") or 0) - (
            job.get("execution_start_time") or 0
        )
    else:
        key = lambda job: job.get("create_time") or 0
    jobs = sorted(jobs, key=key, reverse=sort_order == "desc")
    total = len(jobs)
    jobs = jobs[offset:]
    if limit is not None:
        jobs = jobs[:limit]
    return jobs, total


def _upstream_module(get_all_jobs=_upstream_get_all_jobs):
    module = types.ModuleType("comfy_execution.jobs")
    module.get_all_jobs = get_all_jobs
    return module


@contextmanager
def _host_contract(queue, *, get_all_jobs=_upstream_get_all_jobs):
    jobs_module = _upstream_module(get_all_jobs)
    package = types.ModuleType("comfy_execution")
    package.__path__ = []
    package.jobs = jobs_module
    server_module = types.ModuleType("server")
    server_module.PromptServer = SimpleNamespace(
        instance=SimpleNamespace(prompt_queue=queue)
    )
    with patch.dict(
        sys.modules,
        {
            "comfy_execution": package,
            "comfy_execution.jobs": jobs_module,
            "server": server_module,
        },
    ):
        yield jobs_module


def _load_read_model(testcase: unittest.TestCase):
    try:
        from services import jobs_read_model
    except ImportError:
        testcase.fail("services.jobs_read_model must implement the R213 adapter")
    return jobs_read_model


def _five_state_queue():
    return _PromptQueueFixture(
        running=[
            _queue_record("job-running", create_time=500, workflow_id="workflow-active")
        ],
        queued=[
            _queue_record(
                "job-pending",
                priority=2,
                create_time=400,
                workflow_id="workflow-active",
            )
        ],
        history={
            "job-completed": _history_record(
                "job-completed",
                outcome="completed",
                create_time=300,
                start_time=310,
                end_time=350,
                workflow_id="workflow-terminal",
            ),
            "job-failed": _history_record(
                "job-failed",
                outcome="failed",
                create_time=200,
                start_time=210,
                end_time=270,
                workflow_id="workflow-terminal",
            ),
            "job-cancelled": _history_record(
                "job-cancelled",
                outcome="cancelled",
                create_time=100,
                start_time=110,
                end_time=120,
                workflow_id="workflow-terminal",
            ),
        },
    )


@unittest.skipIf(web is None, "aiohttp not installed")
class TestR213PrefFixReproduction(unittest.IsolatedAsyncioTestCase):
    async def test_non_empty_prompt_queue_is_not_reported_as_stub_empty(self):
        """RED: the secured S100 route still ignores authoritative non-empty state."""

        from api import routes

        with (
            patch.dict(os.environ, {}, clear=True),
            _host_contract(_PromptQueueFixture(running=[_queue_record("job-running")])),
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "emit_audit_event", create=True),
        ):
            response = await routes.jobs_handler(_request())

        body = _decode(response)
        self.assertEqual(response.status, 200)
        self.assertNotIn("not_implemented", body)
        self.assertEqual(body["contract_version"], 1)
        self.assertEqual(body["jobs"][0]["id"], "job-running")


class TestJobsReadModel(unittest.TestCase):
    def test_exact_envelope_five_states_and_forbidden_fields(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        with _host_contract(_five_state_queue()):
            body = model.read_jobs(normalize_jobs_query({}), tenant_id="default")

        self.assertEqual(
            set(body),
            {"ok", "contract_version", "jobs", "pagination", "source", "scan"},
        )
        self.assertTrue(body["ok"])
        self.assertEqual(body["contract_version"], 1)
        self.assertEqual(
            [job["status"] for job in body["jobs"]],
            ["in_progress", "pending", "completed", "failed", "cancelled"],
        )
        self.assertEqual(
            body["source"],
            {"adapter": "comfy_execution.jobs", "authority": "in_process"},
        )
        self.assertEqual(
            body["scan"],
            {
                "window": 10000,
                "examined": 5,
                "excluded": 0,
                "malformed": 0,
                "truncated": False,
            },
        )
        allowed = {
            "id",
            "status",
            "priority",
            "create_time",
            "execution_start_time",
            "execution_end_time",
            "outputs_count",
            "workflow_id",
        }
        self.assertTrue(all(set(job) <= allowed for job in body["jobs"]))
        encoded = json.dumps(body, sort_keys=True)
        for forbidden in (
            "preview_output",
            "previewable_outputs_count",
            "secret-output.png",
            "secret-user-output",
            "secret-traceback",
            "SecretPromptNode",
            "SecretHistoryNode",
            "extra_data",
            "execution_error",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_filters_sorts_pagination_and_warnings_are_deterministic(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        queue = _five_state_queue()
        cases = (
            (
                {
                    "status": "failed",
                    "sort_by": "created_at",
                    "sort_order": "desc",
                },
                ["job-failed"],
            ),
            (
                {
                    "workflow_id": "workflow-terminal",
                    "sort_by": "execution_duration",
                    "sort_order": "asc",
                },
                ["job-cancelled", "job-completed", "job-failed"],
            ),
            (
                {"sort_by": "created_at", "sort_order": "asc", "limit": "2"},
                ["job-cancelled", "job-failed"],
            ),
        )
        with _host_contract(queue):
            for raw_query, expected_ids in cases:
                with self.subTest(raw_query=raw_query):
                    body = model.read_jobs(
                        normalize_jobs_query(raw_query), tenant_id="default"
                    )
                    self.assertEqual([job["id"] for job in body["jobs"]], expected_ids)

            page = model.read_jobs(
                normalize_jobs_query(
                    {"limit": "99999", "offset": "999999999999999999"}
                ),
                tenant_id="default",
            )
        self.assertEqual(page["pagination"]["limit"], 200)
        self.assertEqual(page["pagination"]["offset"], 10000)
        self.assertEqual(page["pagination"]["total"], 5)
        self.assertFalse(page["pagination"]["has_more"])
        self.assertEqual(
            {item["code"] for item in page["pagination"]["warnings"]},
            {"R95_LIMIT_CLAMPED", "R95_OFFSET_CLAMPED"},
        )
        self.assertNotIn("999999999999999999", json.dumps(page))

    def test_authoritative_empty_is_distinct_from_failures(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        with _host_contract(_PromptQueueFixture()):
            body = model.read_jobs(normalize_jobs_query({}), tenant_id="default")
        self.assertTrue(body["ok"])
        self.assertEqual(body["jobs"], [])
        self.assertEqual(body["pagination"]["total"], 0)
        self.assertFalse(body["scan"]["truncated"])

    def test_multi_tenant_filters_raw_records_before_upstream_normalization(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        queue = _PromptQueueFixture(
            running=[
                _queue_record("job-exact", tenant_id="team-a"),
                _queue_record("job-foreign", tenant_id="team-b"),
                _queue_record("job-unmarked", tenant_id=None),
                ("malformed",),
            ]
        )
        observed = {}

        def observing_get_all_jobs(running, queued, history, **kwargs):
            observed["ids"] = [item[1] for item in running]
            return _upstream_get_all_jobs(running, queued, history, **kwargs)

        with (
            patch.dict(os.environ, {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}),
            _host_contract(queue, get_all_jobs=observing_get_all_jobs),
        ):
            body = model.read_jobs(normalize_jobs_query({}), tenant_id="team-a")

        self.assertEqual(observed["ids"], ["job-exact"])
        self.assertEqual([job["id"] for job in body["jobs"]], ["job-exact"])
        self.assertEqual(body["scan"]["excluded"], 3)
        self.assertEqual(body["scan"]["malformed"], 1)

    def test_malformed_raw_is_counted_but_malformed_normalized_fails_closed(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        query = normalize_jobs_query({})
        queue = _PromptQueueFixture(queued=[("bad",), _queue_record("job-valid")])
        with _host_contract(queue):
            body = model.read_jobs(query, tenant_id="default")
        self.assertEqual([job["id"] for job in body["jobs"]], ["job-valid"])
        self.assertEqual(body["scan"]["malformed"], 1)
        self.assertEqual(body["scan"]["excluded"], 1)

        def malformed_normalized(*args, **kwargs):
            return ([{"id": "job-bad", "status": ["pending"]}], 1)

        with _host_contract(
            _PromptQueueFixture(queued=[_queue_record()]),
            get_all_jobs=malformed_normalized,
        ):
            with self.assertRaises(model.JobsBackendUnavailable) as ctx:
                model.read_jobs(query, tenant_id="default")
        self.assertEqual(ctx.exception.code, "jobs_backend_unavailable")

    def test_unsupported_and_unavailable_boundaries_are_distinct(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        query = normalize_jobs_query({})
        missing_module = ModuleNotFoundError(
            "missing upstream", name="comfy_execution.jobs"
        )
        with patch.object(
            model.importlib,
            "import_module",
            side_effect=missing_module,
        ):
            with self.assertRaises(model.JobsHostContractUnsupported) as ctx:
                model.read_jobs(query, tenant_id="default")
        self.assertEqual(ctx.exception.code, "jobs_host_contract_unsupported")

        with _host_contract(_PromptQueueFixture(), get_all_jobs=None):
            with self.assertRaises(model.JobsHostContractUnsupported):
                model.read_jobs(query, tenant_id="default")

        with _host_contract(SimpleNamespace()):
            with self.assertRaises(model.JobsHostContractUnsupported):
                model.read_jobs(query, tenant_id="default")

        with patch.dict(sys.modules, {"server": None}):
            with patch.object(
                model.importlib, "import_module", return_value=_upstream_module()
            ):
                with self.assertRaises(model.JobsBackendUnavailable):
                    model.read_jobs(query, tenant_id="default")

        with _host_contract(_PromptQueueFixture(failure=RuntimeError("secret"))):
            with self.assertRaises(model.JobsBackendUnavailable) as ctx:
                model.read_jobs(query, tenant_id="default")
        self.assertEqual(str(ctx.exception), "Jobs backend is unavailable.")
        self.assertNotIn("secret", str(ctx.exception))

    def test_maximum_history_window_keeps_response_bounded(self):
        model = _load_read_model(self)
        from services.jobs_security import normalize_jobs_query

        history = {
            f"job-{index:05d}": _history_record(
                f"job-{index:05d}",
                outcome="completed",
                create_time=index,
                start_time=index + 1,
                end_time=index + 2,
            )
            for index in range(10001)
        }
        started = time.perf_counter()
        with _host_contract(_PromptQueueFixture(history=history)):
            body = model.read_jobs(normalize_jobs_query({}), tenant_id="default")
        elapsed = time.perf_counter() - started

        self.assertLess(
            elapsed, 10.0, "bounded 10k read should not grow pathologically"
        )
        self.assertEqual(body["scan"]["examined"], 10000)
        self.assertTrue(body["scan"]["truncated"])
        self.assertEqual(body["pagination"]["total"], 10000)
        self.assertEqual(len(body["jobs"]), 50)
        self.assertTrue(body["pagination"]["has_more"])
        self.assertLess(len(json.dumps(body)), 100000)


@unittest.skipIf(web is None, "aiohttp not installed")
class TestJobsHandlerAdapterErrors(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_and_backend_failures_have_safe_audits(self):
        from api import routes

        model = _load_read_model(self)
        cases = (
            (
                model.JobsHostContractUnsupported(),
                501,
                "jobs_host_contract_unsupported",
                "unsupported",
            ),
            (
                model.JobsBackendUnavailable(),
                503,
                "jobs_backend_unavailable",
                "error",
            ),
        )
        for failure, status, code, outcome in cases:
            with self.subTest(code=code):
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(routes, "check_rate_limit", return_value=True),
                    patch.object(routes, "read_jobs", side_effect=failure, create=True),
                    patch.object(routes, "emit_audit_event", create=True) as audit,
                ):
                    response = await routes.jobs_handler(_request())
                self.assertEqual(response.status, status)
                self.assertEqual(_decode(response), {"ok": False, "error": code})
                call = audit.call_args.kwargs
                self.assertEqual(call["action"], "jobs.list")
                self.assertEqual(call["outcome"], outcome)
                self.assertEqual(call["details"], {"reason": code})
                self.assertNotIn("secret", json.dumps(call["details"]))

    async def test_invalid_query_never_invokes_adapter(self):
        from api import routes

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(routes, "check_rate_limit", return_value=True),
            patch.object(routes, "read_jobs", create=True) as read_jobs,
            patch.object(routes, "emit_audit_event", create=True),
        ):
            response = await routes.jobs_handler(_request(query={"status": "mystery"}))
        self.assertEqual(response.status, 400)
        self.assertEqual(_decode(response)["error"], "jobs_query_invalid")
        read_jobs.assert_not_called()


class _NoopRoutes:
    @staticmethod
    def _decorator(_path):
        return lambda handler: handler

    get = post = put = delete = _decorator


@unittest.skipIf(TestClient is None, "aiohttp test utilities not installed")
class TestJobsLowMockTransaction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from api import routes

        self.routes = routes
        self.app = web.Application()
        self.prompt_queue = _five_state_queue()
        self.server_fixture = SimpleNamespace(app=self.app, routes=_NoopRoutes())
        routes.register_dual_route(
            self.server_fixture, "GET", "/openclaw/jobs", routes.jobs_handler
        )
        routes.register_dual_route(
            self.server_fixture, "GET", "/moltbot/jobs", routes.jobs_handler
        )
        self.test_server = TestServer(self.app)
        self.client = TestClient(self.test_server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_real_aiohttp_route_and_all_aliases_are_equivalent(self):
        bodies = []
        with (
            patch.dict(os.environ, {}, clear=True),
            _host_contract(self.prompt_queue),
            patch.object(self.routes, "check_rate_limit", return_value=True),
            patch.object(self.routes, "emit_audit_event", create=True) as audit,
        ):
            for path in (
                "/openclaw/jobs",
                "/api/openclaw/jobs",
                "/moltbot/jobs",
                "/api/moltbot/jobs",
            ):
                response = await self.client.get(
                    path, headers={"Sec-Fetch-Site": "same-origin"}
                )
                self.assertEqual(response.status, 200)
                bodies.append(await response.json())
                if "moltbot" in path:
                    self.assertEqual(response.headers["Deprecation"], "true")
                    self.assertEqual(
                        response.headers["X-OpenClaw-Canonical-Path"],
                        path.replace("moltbot", "openclaw"),
                    )

        self.assertTrue(all(body == bodies[0] for body in bodies[1:]))
        self.assertEqual(bodies[0]["contract_version"], 1)
        self.assertEqual(bodies[0]["jobs"][0]["id"], "job-running")
        self.assertEqual(len(audit.call_args_list), 4)
        for call in audit.call_args_list:
            details = call.kwargs["details"]
            self.assertEqual(call.kwargs["outcome"], "allow")
            self.assertEqual(details["reason"], "jobs_listed")
            self.assertEqual(
                set(details),
                {"reason", "returned_count", "excluded_count", "malformed_count"},
            )
            self.assertNotIn("job-running", json.dumps(details))


class TestR213PublicContract(unittest.TestCase):
    def test_public_contract_describes_versioned_authoritative_jobs(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        contract = (root / "docs/release/api_contract.md").read_text(encoding="utf-8")
        self.assertIn("> **Version**: 1.0.15", contract)
        self.assertIn("versioned bounded in-process jobs read model", contract)
        self.assertNotIn("compatibility stub until the bounded read adapter", contract)
        self.assertIn("jobs_host_contract_unsupported", contract)
        self.assertIn("jobs_backend_unavailable", contract)
        self.assertIn("`preview_output` is never included", contract)

        openapi = (root / "docs/openapi.yaml").read_text(encoding="utf-8")
        jobs_block = openapi.split("  /jobs:\n", 1)[1].split("\n  /", 1)[0]
        self.assertIn('version: "1.0.15"', openapi)
        self.assertIn("versioned bounded in-process jobs read model", jobs_block)
        self.assertIn('x-openclaw-auth: "Admin"', jobs_block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
