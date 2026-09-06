import { beforeEach, describe, expect, it, vi } from "vitest";

import { createRemoteAdminApi, parseSseChunk } from "../../../web/admin_console_api.js";

describe("admin_console_api", () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it("stores the remote admin token and falls back from canonical to legacy paths on 404", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } }))
            .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, value: "legacy" }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            }));

        const api = createRemoteAdminApi({
            fetchImpl: fetchMock,
            storage: localStorage,
        });

        api.setToken("secret-token");
        const response = await api.request("/health");

        expect(api.getToken()).toBe("secret-token");
        expect(localStorage.getItem("openclaw_remote_admin_token")).toBe("secret-token");
        expect(response.ok).toBe(true);
        expect(response.data).toEqual({ ok: true, value: "legacy" });
        expect(fetchMock.mock.calls[0][0]).toBe("/openclaw/health");
        expect(fetchMock.mock.calls[1][0]).toBe("/moltbot/health");
    });

    it("parses SSE event chunks into payload objects", () => {
        const payload = parseSseChunk('event: queued\ndata: {"seq":7,"prompt_id":"abc"}\n\n');
        expect(payload).toEqual(
            expect.objectContaining({
                event_type: "queued",
                prompt_id: "abc",
                seq: 7,
            })
        );
    });
});
