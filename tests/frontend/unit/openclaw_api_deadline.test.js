import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { hostFetch } = vi.hoisted(() => ({ hostFetch: vi.fn() }));
vi.mock("../../../web/openclaw_comfy_api.js", () => ({
    fetchApi: hostFetch,
    fileURL: (route) => route,
}));

let OpenClawAPI;
let nativeFetch;

function response(status, data = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 404 ? "Not Found" : "OK",
        headers: { get: () => "application/json" },
        text: async () => JSON.stringify(data),
    };
}

function untilAbort(_route, options) {
    return new Promise((_, reject) => {
        options.signal.addEventListener("abort", () => {
            reject(new DOMException("Request aborted", "AbortError"));
        }, { once: true });
    });
}

describe("OpenClawAPI host deadline contract", () => {
    beforeEach(async () => {
        nativeFetch = vi.fn();
        vi.stubGlobal("fetch", nativeFetch);
        hostFetch.mockReset();
        ({ OpenClawAPI } = await import("../../../web/openclaw_api.js"));
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it("normalizes a host TimeoutError without retrying a GET", async () => {
        hostFetch.mockRejectedValue(new DOMException("Fetch timeout", "TimeoutError"));

        const result = await new OpenClawAPI().fetch("/openclaw/health", { timeout: 90_000 });

        expect(result).toMatchObject({ ok: false, status: 0, error: "timeout" });
        expect(hostFetch).toHaveBeenCalledTimes(1);
        expect(nativeFetch).not.toHaveBeenCalled();
    });

    it("preserves caller cancellation even when an adapter reports TimeoutError", async () => {
        const caller = new AbortController();
        const remove = vi.spyOn(caller.signal, "removeEventListener");
        hostFetch.mockImplementation(async () => {
            caller.abort();
            throw new DOMException("Fetch timeout", "TimeoutError");
        });

        const result = await new OpenClawAPI().fetch("/openclaw/health", {
            signal: caller.signal,
            timeout: 90_000,
        });

        expect(result.error).toBe("cancelled");
        expect(hostFetch).toHaveBeenCalledTimes(1);
        expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    });

    it.each(["old", "new"].flatMap((hostKind) => [10_000, 30_000, 60_000, 75_000]
        .map((timeout) => [hostKind, timeout])))(
        "keeps the %s host adapter under the %i ms product deadline",
        async (hostKind, timeout) => {
            vi.useFakeTimers();
            hostFetch.mockImplementation((route, options) => {
                if (hostKind === "new" && options.timeoutMs !== null) {
                    return Promise.race([
                        untilAbort(route, options),
                        new Promise((_, reject) => setTimeout(
                            () => reject(new DOMException("Fetch timeout", "TimeoutError")), 60_000,
                        )),
                    ]);
                }
                return untilAbort(route, options);
            });

            const resultPromise = new OpenClawAPI().fetch("/openclaw/health", { timeout });
            await vi.advanceTimersByTimeAsync(timeout);
            const result = await resultPromise;

            expect(result.error).toBe("timeout");
            expect(hostFetch).toHaveBeenCalledTimes(1);
            expect(hostFetch.mock.calls[0][1]).toMatchObject({ timeoutMs: null });
            expect(vi.getTimerCount()).toBe(0);
        },
    );

    it("retains one GET retry for ordinary network failure and no retry for POST", async () => {
        hostFetch.mockRejectedValueOnce(new TypeError("temporary network error"))
            .mockResolvedValueOnce(response(200, { ok: true }));
        const api = new OpenClawAPI();

        expect((await api.fetch("/openclaw/health")).ok).toBe(true);
        expect(hostFetch).toHaveBeenCalledTimes(2);

        hostFetch.mockClear();
        hostFetch.mockRejectedValue(new TypeError("write failed"));
        expect((await api.fetch("/openclaw/config", { method: "POST" })).error).toBe("network_error");
        expect(hostFetch).toHaveBeenCalledTimes(1);
    });

    it("keeps 404 route fallback and strips host-only options from native fetch", async () => {
        hostFetch.mockResolvedValue(response(404));
        nativeFetch.mockResolvedValue(response(200, { source: "native" }));
        const api = new OpenClawAPI();
        const headers = api._adminTokenHeaders("synthetic-token");

        const result = await api.fetch("/openclaw/health", { headers });

        expect(result).toMatchObject({ ok: true, data: { source: "native" } });
        expect(hostFetch.mock.calls.every(([, options]) => options.timeoutMs === null)).toBe(true);
        expect(nativeFetch.mock.calls[0][1]).not.toHaveProperty("timeoutMs");
        expect(nativeFetch.mock.calls[0][1].headers).toEqual(headers);
    });

    it("keeps canonical-to-legacy 404 fallback on the host adapter", async () => {
        hostFetch.mockResolvedValueOnce(response(404))
            .mockResolvedValueOnce(response(200, { source: "legacy" }));

        const result = await new OpenClawAPI().fetch("/openclaw/health");

        expect(result).toMatchObject({ ok: true, data: { source: "legacy" } });
        expect(hostFetch.mock.calls.map(([route]) => route)).toEqual([
            "/openclaw/health", "/moltbot/health",
        ]);
        expect(nativeFetch).not.toHaveBeenCalled();
    });

    it("does not swallow caller cancellation during native 404 fallback", async () => {
        const caller = new AbortController();
        hostFetch.mockResolvedValue(response(404));
        nativeFetch.mockImplementation(async () => {
            caller.abort();
            throw new DOMException("Cancelled", "AbortError");
        });

        const result = await new OpenClawAPI().fetch("/openclaw/health", {
            signal: caller.signal,
        });

        expect(result.error).toBe("cancelled");
        expect(nativeFetch).toHaveBeenCalledTimes(1);
    });

    it("normalizes a streaming header TimeoutError and uses the product deadline", async () => {
        hostFetch.mockRejectedValue(new DOMException("Fetch timeout", "TimeoutError"));
        const api = new OpenClawAPI();

        const result = await api.streamSSEPost("/openclaw/assist/planner/stream", {}, { timeout: 75_000 });

        expect(result.error).toBe("timeout");
        expect(hostFetch).toHaveBeenCalledTimes(1);
        expect(hostFetch.mock.calls[0][1].timeoutMs).toBeNull();
    });

    it("bounds streaming headers with its own timer and removes caller listeners", async () => {
        vi.useFakeTimers();
        const caller = new AbortController();
        const remove = vi.spyOn(caller.signal, "removeEventListener");
        hostFetch.mockImplementation(untilAbort);

        const resultPromise = new OpenClawAPI().streamSSEPost(
            "/openclaw/assist/planner/stream", {}, { signal: caller.signal, timeout: 75_000 },
        );
        await vi.advanceTimersByTimeAsync(75_000);
        const result = await resultPromise;

        expect(result.error).toBe("timeout");
        expect(hostFetch.mock.calls[0][1].timeoutMs).toBeNull();
        expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
        expect(vi.getTimerCount()).toBe(0);
    });
});
