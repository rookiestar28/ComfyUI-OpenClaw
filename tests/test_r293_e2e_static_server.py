"""Contract tests for the Playwright E2E harness web server."""

from __future__ import annotations

import contextlib
import io
import re
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from scripts import e2e_static_server

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = "/tests/e2e/test-harness.html"
MIN_ACCEPT_BACKLOG = 128


class E2EStaticServerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Port 0 lets the OS choose a free port, so the test never collides with a
        # real E2E run that already owns 3000.
        self.server = e2e_static_server.create_server(0)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_binds_loopback_only(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_accept_backlog_is_large_enough_for_parallel_workers(self) -> None:
        self.assertGreaterEqual(self.server.request_queue_size, MIN_ACCEPT_BACKLOG)

    def test_serves_the_harness_page_from_the_repository_root(self) -> None:
        with urllib.request.urlopen(self.base_url + HARNESS_PATH, timeout=10) as reply:
            self.assertEqual(reply.status, 200)
            body = reply.read()
        self.assertEqual(
            body, (REPO_ROOT / "tests" / "e2e" / "test-harness.html").read_bytes()
        )

    def test_successful_requests_do_not_write_access_log_lines(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with urllib.request.urlopen(
                self.base_url + HARNESS_PATH, timeout=10
            ) as reply:
                reply.read()
        self.assertEqual(captured.getvalue(), "")

    def test_missing_files_still_report_404(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(
                    self.base_url + "/tests/e2e/does-not-exist.html", timeout=10
                )
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()


class E2EStaticServerCommandTests(unittest.TestCase):
    def test_playwright_config_starts_the_loopback_server_script(self) -> None:
        config = (REPO_ROOT / "playwright.config.js").read_text(encoding="utf-8")
        match = re.search(r"command:\s*`([^`]*)`", config)
        self.assertIsNotNone(match, "webServer.command template literal not found")
        command = match.group(1)
        self.assertIn("scripts/e2e_static_server.py ${e2ePort}", command)
        self.assertNotIn("http.server", command)
        self.assertTrue((REPO_ROOT / "scripts" / "e2e_static_server.py").is_file())


if __name__ == "__main__":
    unittest.main()
