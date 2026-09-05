import unittest
from unittest.mock import patch

from services.metrics import metrics


class _FakeLoop:
    def __init__(self):
        self.calls = []

    async def run_in_executor(self, executor, fn):
        self.calls.append((executor, fn))
        return fn()


class TestR129ExecutorLaneSplit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        metrics.reset()

    async def test_run_in_thread_uses_llm_lane_by_default(self):
        import services.async_utils as async_utils

        loop = _FakeLoop()
        with patch("services.async_utils.asyncio.get_running_loop", return_value=loop):
            out = await async_utils.run_in_thread(lambda x: x + 1, 2)

        self.assertEqual(out, 3)
        self.assertEqual(len(loop.calls), 1)
        self.assertIs(loop.calls[0][0], async_utils._LLM_EXECUTOR)

        counters = metrics.get_all()
        self.assertEqual(counters["executor_llm_submitted"], 1)
        self.assertEqual(counters["executor_llm_started"], 1)
        self.assertEqual(counters["executor_llm_completed"], 1)

    async def test_run_io_in_thread_uses_io_lane(self):
        import services.async_utils as async_utils

        loop = _FakeLoop()
        with patch("services.async_utils.asyncio.get_running_loop", return_value=loop):
            out = await async_utils.run_io_in_thread(lambda: "ok")

        self.assertEqual(out, "ok")
        self.assertEqual(len(loop.calls), 1)
        self.assertIs(loop.calls[0][0], async_utils._IO_EXECUTOR)

        counters = metrics.get_all()
        self.assertEqual(counters["executor_io_submitted"], 1)
        self.assertEqual(counters["executor_io_started"], 1)
        self.assertEqual(counters["executor_io_completed"], 1)

    async def test_wait_bucket_counter_increments_when_queue_wait_is_high(self):
        import services.async_utils as async_utils

        loop = _FakeLoop()
        with (
            patch("services.async_utils.asyncio.get_running_loop", return_value=loop),
            patch("services.async_utils.time.perf_counter", side_effect=[100.0, 100.4]),
        ):
            out = await async_utils.run_in_thread(lambda: "ok")

        self.assertEqual(out, "ok")
        counters = metrics.get_all()
        self.assertGreaterEqual(counters["executor_llm_wait_ms_total"], 400)
        self.assertEqual(counters["executor_llm_wait_over_250ms"], 1)

    def test_worker_count_parser_falls_back_on_invalid_or_out_of_range_values(self):
        import services.async_utils as async_utils

        with patch.dict("os.environ", {"OPENCLAW_LLM_EXECUTOR_WORKERS": "abc"}):
            parsed = async_utils._parse_worker_count(
                (("OPENCLAW_LLM_EXECUTOR_WORKERS", ()),),
                6,
                minimum=1,
                maximum=12,
            )
            self.assertEqual(parsed, 6)

        with patch.dict("os.environ", {"OPENCLAW_LLM_EXECUTOR_WORKERS": "99"}):
            parsed = async_utils._parse_worker_count(
                (("OPENCLAW_LLM_EXECUTOR_WORKERS", ()),),
                6,
                minimum=1,
                maximum=12,
            )
            self.assertEqual(parsed, 6)

    def test_executor_diagnostics_shape(self):
        import services.async_utils as async_utils

        diag = async_utils.get_executor_diagnostics()
        self.assertIn("llm", diag)
        self.assertIn("io", diag)
        self.assertIn("workers", diag["llm"])
        self.assertIn("submitted", diag["llm"])
        self.assertIn("workers", diag["io"])
        self.assertIn("submitted", diag["io"])
