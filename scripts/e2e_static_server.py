#!/usr/bin/env python3
"""Loopback-only static file server for the Playwright E2E harness.

Usage: python scripts/e2e_static_server.py <port>

Playwright's ``webServer`` block starts this script and waits for
``http://127.0.0.1:<port>/tests/e2e/test-harness.html``. It serves the repository root,
exactly like the ``python -m http.server`` command it replaces, with two differences:

* it binds ``127.0.0.1`` only, so the checkout is never served to the local network;
* it listens with a large accept backlog (see ``ACCEPT_BACKLOG``).

Standard library only, so the Python 3.10+ repository floor applies unchanged.
"""

from __future__ import annotations

import argparse
import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPBACK_HOST = "127.0.0.1"

# CRITICAL: keep this backlog large and keep the bind loopback-only.
# `python -m http.server` listens with the stdlib default backlog of 5. With Playwright's
# default worker count (one worker per two logical processors, each page loading about
# sixty module files) the mocked E2E suite failed on a 32-thread Windows host in 3 of 3
# runs, 7-8 failed tests each, most of them `net::ERR_CONNECTION_REFUSED`; it passed 3 of 3
# against this server. Windows refuses a connection once the accept queue is full, which
# is the likely mechanism. The change also narrowed the bind and quieted the log, so the
# backlog alone was not isolated. Lowering this value, or swapping the stdlib server back
# in, risks the same failures, and they look like flaky tests, not like a server fault.
ACCEPT_BACKLOG = 128


class QuietHandler(SimpleHTTPRequestHandler):
    """Static file handler without the per-request access line.

    Errors (404, malformed requests) still reach stderr through ``log_error``.
    """

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        return None


class E2EStaticServer(ThreadingHTTPServer):
    request_queue_size = ACCEPT_BACKLOG
    # Request threads must never keep the process alive once Playwright stops it.
    daemon_threads = True


def create_server(port: int, directory: Path = REPO_ROOT) -> E2EStaticServer:
    """Bind the server on loopback; ``port`` 0 asks the OS for a free port."""
    handler = functools.partial(QuietHandler, directory=str(directory))
    return E2EStaticServer((LOOPBACK_HOST, port), handler)


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"port must be an integer, got {value!r}"
        ) from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be 1-65535, got {port}")
    return port


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", type=_parse_port, help="loopback TCP port to listen on")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.port)
    except OSError as exc:
        print(
            f"[e2e-server] cannot bind {LOOPBACK_HOST}:{args.port}: {exc}",
            file=sys.stderr,
        )
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
