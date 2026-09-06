import { beforeEach, describe, expect, it, vi } from "vitest";
import {
    composeFetchWrappersOnce,
    getFetchWrapperMeta,
    withAbortPassthrough,
    withGetRetry,
    withPreconnectHint,
} from "../../../web/openclaw_fetch_wrappers.js";

describe("openclaw_fetch_wrappers", () => {
    beforeEach(() => {
        document.head.innerHTML = "";
    });

    it("does not stack the same decorator chain twice", async () => {
        const fetchFn = vi.fn(async () => ({ ok: true }));
        const decorators = [withAbortPassthrough(), withGetRetry({ retries: 1 })];

        const wrapped = composeFetchWrappersOnce(fetchFn, decorators);
        const wrappedAgain = composeFetchWrappersOnce(wrapped, decorators);

        expect(wrappedAgain).toBe(wrapped);
        expect(getFetchWrapperMeta(wrappedAgain)).toMatchObject({
            baseFetch: fetchFn,
            appliedCount: 1,
            chainIds: ["abort_passthrough", "retry_get_1"],
        });

        await wrappedAgain("/health");
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });

    it("adds one preconnect link per origin", async () => {
        const fetchFn = vi.fn(async () => ({ ok: true }));
        const wrapped = composeFetchWrappersOnce(fetchFn, [withPreconnectHint()]);

        await wrapped("https://example.com/api/one");
        await wrapped("https://example.com/api/two");

        const links = [...document.head.querySelectorAll('link[rel="preconnect"]')];
        expect(links).toHaveLength(1);
        expect(links[0].href).toContain("https://example.com");
    });

    it("retries GET once on network failure", async () => {
        const fetchFn = vi
            .fn()
            .mockRejectedValueOnce(new Error("temporary"))
            .mockResolvedValueOnce({ ok: true, status: 200 });
        const wrapped = composeFetchWrappersOnce(fetchFn, [withGetRetry({ retries: 1 })]);

        const result = await wrapped("/health", { method: "GET" });

        expect(result).toMatchObject({ ok: true, status: 200 });
        expect(fetchFn).toHaveBeenCalledTimes(2);
    });

    it("does not retry non-GET requests", async () => {
        const fetchFn = vi.fn().mockRejectedValue(new Error("no retry"));
        const wrapped = composeFetchWrappersOnce(fetchFn, [withGetRetry({ retries: 2 })]);

        await expect(wrapped("/config", { method: "POST" })).rejects.toThrow("no retry");
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });

    it("does not retry abort errors", async () => {
        const fetchFn = vi.fn().mockRejectedValue(new DOMException("Cancelled", "AbortError"));
        const wrapped = composeFetchWrappersOnce(fetchFn, [withGetRetry({ retries: 2 })]);

        await expect(wrapped("/health")).rejects.toThrow(/Cancelled/);
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });
});
