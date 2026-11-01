"""
R145 aiohttp compatibility helpers.

Keeps route/service modules importable in minimal environments where aiohttp is
not installed, while still failing explicitly when request/response helpers are
actually invoked.
"""

from __future__ import annotations

from typing import Any, Protocol


class _AsyncByteStream(Protocol):
    async def read(self, n: int) -> bytes: ...


async def read_bounded_body(stream: _AsyncByteStream, limit: int) -> bytes:
    """Read to EOF or one overflow byte, without retaining an unbounded body."""
    if limit < 0:
        raise ValueError("body limit must be non-negative")
    body = bytearray()
    # CRITICAL: read(n) may return a short chunk before EOF. Parsing/authenticating
    # that prefix causes false JSON/HMAC failures and misses oversized bodies.
    while len(body) <= limit:
        chunk = await stream.read(limit + 1 - len(body))
        if not chunk:
            break
        body.extend(chunk)
    return bytes(body)


def _missing_aiohttp(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("aiohttp not available")


class _UnavailableResponse:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        _missing_aiohttp()


class _UnavailableStreamResponse:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        _missing_aiohttp()


class _MissingAiohttpWeb:
    __openclaw_aiohttp_available__ = False
    Request = object
    Application = object
    Response = _UnavailableResponse
    StreamResponse = _UnavailableStreamResponse

    def json_response(self, *_args: Any, **_kwargs: Any) -> Any:
        _missing_aiohttp()

    def __getattr__(self, _name: str) -> Any:
        return _missing_aiohttp


MISSING_AIOHTTP_WEB = _MissingAiohttpWeb()


def import_aiohttp_web() -> Any:
    """
    Return `aiohttp.web` when available, otherwise a fail-fast shim.

    CRITICAL: only swallow ModuleNotFoundError for aiohttp itself. Broader
    ImportError handling can hide unrelated packaged-context regressions.
    """
    try:
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - minimal env path
        if exc.name != "aiohttp":
            raise
        return MISSING_AIOHTTP_WEB
    return web
