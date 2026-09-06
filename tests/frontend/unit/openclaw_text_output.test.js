import { afterEach, describe, expect, it, vi } from "vitest";

import {
    TEXT_OUTPUT_MAX_BYTES,
    TEXT_OUTPUT_MAX_CHARS,
    TEXT_OUTPUT_TIMEOUT_MS,
    loadBoundedTextOutput,
} from "../../../web/openclaw_text_output.js";

const encoder = new TextEncoder();

function makeHeaders(values = {}) {
    const normalized = new Map(
        Object.entries(values).map(([key, value]) => [key.toLowerCase(), String(value)])
    );
    return {
        get(name) {
            return normalized.get(String(name).toLowerCase()) ?? null;
        },
    };
}

function makeResponse(chunks, {
    contentType = "text/plain; charset=utf-8",
    contentLength = null,
    ok = true,
    status = 200,
    redirected = false,
    url = "/api/view?filename=result.txt&type=output",
    withStream = true,
} = {}) {
    const queue = chunks.map((chunk) => (
        chunk instanceof Uint8Array ? chunk : encoder.encode(String(chunk))
    ));
    const reader = {
        read: vi.fn(async () => (
            queue.length ? { done: false, value: queue.shift() } : { done: true, value: undefined }
        )),
        cancel: vi.fn(async () => undefined),
    };
    const text = vi.fn(async () => "UNBOUNDED_READER_MUST_NOT_RUN");
    const arrayBuffer = vi.fn(async () => new ArrayBuffer(0));
    const headers = {
        "content-type": contentType,
        ...(contentLength == null ? {} : { "content-length": contentLength }),
    };
    return {
        ok,
        status,
        redirected,
        url: new URL(url, window.location.href).href,
        headers: makeHeaders(headers),
        body: withStream ? { getReader: () => reader } : null,
        text,
        arrayBuffer,
        __reader: reader,
    };
}

afterEach(() => {
    vi.useRealTimers();
});

describe("bounded text output loader", () => {
    it("streams same-origin /view text with fixed safe fetch options", async () => {
        const response = makeResponse(["hello ", "world"]);
        const fetchFn = vi.fn(async () => response);

        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn }
        );

        expect(result).toEqual({
            status: "success",
            content: "hello world",
            truncated: false,
            reason: "",
        });
        expect(fetchFn).toHaveBeenCalledWith(
            expect.stringMatching(/^http:\/\/localhost:\d+\/api\/view\?/),
            expect.objectContaining({
                method: "GET",
                credentials: "same-origin",
                redirect: "error",
                signal: expect.any(AbortSignal),
            })
        );
        expect(response.text).not.toHaveBeenCalled();
        expect(response.arrayBuffer).not.toHaveBeenCalled();
    });

    it("keeps active-content-looking text literal and enforces the display cap", async () => {
        const active = '<script>window.pwned=true</script> [link](javascript:alert(1))\u001b[31m';
        const longText = active + "x".repeat(TEXT_OUTPUT_MAX_CHARS + 100);
        const fetchFn = vi.fn(async () => makeResponse([longText], {
            contentType: "text/markdown",
            url: "/view?filename=result.md&type=output",
        }));

        const result = await loadBoundedTextOutput(
            "/view?filename=result.md&type=output",
            { fetchFn }
        );

        expect(result.status, JSON.stringify(result)).toBe("truncated");
        expect(result.truncated).toBe(true);
        expect(result.content).toHaveLength(TEXT_OUTPUT_MAX_CHARS);
        expect(result.content).toContain("<script>");
    });

    it("counts the display cap in Unicode code points without splitting characters", async () => {
        const emojiText = "😀".repeat(TEXT_OUTPUT_MAX_CHARS + 1);
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => makeResponse([emojiText])) }
        );

        expect(result.status).toBe("truncated");
        expect(Array.from(result.content)).toHaveLength(TEXT_OUTPUT_MAX_CHARS);
        expect(result.content.endsWith("😀")).toBe(true);
    });

    it.each([
        "application/json; charset=utf-8",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "text/csv",
        "text/xml",
    ])("accepts an explicit textual MIME: %s", async (contentType) => {
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => makeResponse(["safe"], { contentType })) }
        );
        expect(result.status).toBe("success");
    });

    it("rejects a non-UTF-8 charset declaration before reading the body", async () => {
        const response = makeResponse(["SECRET_WRONG_CHARSET"], {
            contentType: "text/plain; charset=iso-8859-1",
        });
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => response) }
        );
        expect(result.reason).toBe("mime_rejected");
        expect(response.__reader.read).not.toHaveBeenCalled();
    });

    it.each([
        "application/octet-stream",
        "text/html",
        "image/svg+xml",
        "application/xhtml+xml",
        "multipart/form-data",
        "",
    ])("rejects ambiguous or active MIME without reading the body: %s", async (contentType) => {
        const response = makeResponse(["SECRET_BODY"], { contentType });
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => response) }
        );
        expect(result).toEqual({
            status: "unavailable",
            content: "",
            truncated: false,
            reason: "mime_rejected",
        });
        expect(response.__reader.read).not.toHaveBeenCalled();
        expect(JSON.stringify(result)).not.toContain("SECRET_BODY");
    });

    it("rejects declared and streamed oversize responses", async () => {
        const declared = makeResponse(["ignored"], {
            contentLength: TEXT_OUTPUT_MAX_BYTES + 1,
        });
        const declaredResult = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => declared) }
        );
        expect(declaredResult.reason).toBe("oversized");
        expect(declared.__reader.read).not.toHaveBeenCalled();

        const streamed = makeResponse([
            new Uint8Array(TEXT_OUTPUT_MAX_BYTES),
            new Uint8Array([65]),
        ]);
        const streamedResult = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => streamed) }
        );
        expect(streamedResult.reason).toBe("oversized");
        expect(streamed.__reader.cancel).toHaveBeenCalled();
    });

    it("rejects an oversized single chunk before copying or iterating it", async () => {
        const iterator = vi.fn(function* iterator() {
            throw new Error("oversized chunk must not be copied");
        });
        const hugeChunk = {
            byteLength: TEXT_OUTPUT_MAX_BYTES + 1,
            BYTES_PER_ELEMENT: 1,
            [Symbol.toStringTag]: "Uint8Array",
            [Symbol.iterator]: iterator,
        };
        const response = makeResponse([]);
        response.body = {
            getReader: () => ({
                read: vi.fn()
                    .mockResolvedValueOnce({ done: false, value: hugeChunk })
                    .mockResolvedValueOnce({ done: true, value: undefined }),
                cancel: vi.fn(async () => undefined),
            }),
        };

        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => response) }
        );

        expect(result.reason).toBe("oversized");
        expect(iterator).not.toHaveBeenCalled();
    });

    it("rejects invalid UTF-8 without permissive replacement decoding", async () => {
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            {
                fetchFn: vi.fn(async () => makeResponse([
                    new Uint8Array([0xc3, 0x28]),
                ])),
            }
        );
        expect(result.reason).toBe("invalid_utf8");
        expect(result.content).toBe("");
    });

    it("degrades to link-only when safe streaming is unavailable", async () => {
        const response = makeResponse(["SECRET_BODY"], { withStream: false });
        const result = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => response) }
        );
        expect(result).toEqual({
            status: "link_only",
            content: "",
            truncated: false,
            reason: "stream_unavailable",
        });
        expect(response.text).not.toHaveBeenCalled();
        expect(response.arrayBuffer).not.toHaveBeenCalled();
    });

    it.each([
        "https://evil.example/view?filename=result.txt&type=output",
        "//evil.example/view?filename=result.txt&type=output",
        "/api/assets/result.txt",
        "/api/view/extra?filename=result.txt&type=output",
        "/api/view?url=https%3A%2F%2Fevil.example%2Fresult.txt",
        "/api/view?filename=result.txt&type=output&unknown=1",
        "/api/view?filename=..%2Fsecret.txt&type=output",
        "/api/view?filename=result.txt&type=unknown",
        "/api/view?filename=image.png&type=output",
        `/api/view?filename=${"x".repeat(1021)}.txt&type=output`,
        `/api/view?filename=result.txt&type=output&subfolder=${encodeURIComponent("../private")}`,
    ])("rejects arbitrary or non-view URLs before fetch: %s", async (url) => {
        const fetchFn = vi.fn();
        const result = await loadBoundedTextOutput(url, { fetchFn });
        expect(result.reason).toBe("invalid_url");
        expect(fetchFn).not.toHaveBeenCalled();
    });

    it("rejects redirected and non-success responses without reading bodies", async () => {
        const redirected = makeResponse(["SECRET_REDIRECT"], { redirected: true });
        const redirectResult = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => redirected) }
        );
        expect(redirectResult.reason).toBe("redirected");
        expect(redirected.__reader.read).not.toHaveBeenCalled();

        const failed = makeResponse(["SECRET_HTTP_BODY"], { ok: false, status: 500 });
        const failedResult = await loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn: vi.fn(async () => failed) }
        );
        expect(failedResult.reason).toBe("http_error");
        expect(failed.__reader.read).not.toHaveBeenCalled();
    });

    it("times out after the fixed budget and does not echo network errors", async () => {
        vi.useFakeTimers();
        const fetchFn = vi.fn((url, init) => new Promise((resolve, reject) => {
            init.signal.addEventListener("abort", () => {
                reject(new DOMException("SECRET_NETWORK_DETAIL", "AbortError"));
            }, { once: true });
        }));

        const resultPromise = loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn }
        );
        await vi.advanceTimersByTimeAsync(TEXT_OUTPUT_TIMEOUT_MS);
        const result = await resultPromise;

        expect(result.reason).toBe("timeout");
        expect(JSON.stringify(result)).not.toContain("SECRET_NETWORK_DETAIL");
    });

    it("honors caller cancellation without leaking error details", async () => {
        const external = new AbortController();
        const fetchFn = vi.fn((url, init) => new Promise((resolve, reject) => {
            init.signal.addEventListener("abort", () => {
                reject(new DOMException("SECRET_CANCEL_DETAIL", "AbortError"));
            }, { once: true });
        }));
        const resultPromise = loadBoundedTextOutput(
            "/api/view?filename=result.txt&type=output",
            { fetchFn, signal: external.signal }
        );

        external.abort();
        const result = await resultPromise;

        expect(result.reason).toBe("cancelled");
        expect(JSON.stringify(result)).not.toContain("SECRET_CANCEL_DETAIL");
    });
});
