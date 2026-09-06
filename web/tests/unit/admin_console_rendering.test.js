import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mountAdminConsole } from "../../admin_console_app.js";

const ROOT = path.resolve(import.meta.dirname, "../../..");

// IMPORTANT: mount the real shipped shell instead of a hand-written fixture so a
// renamed element ID or a dropped panel fails here instead of silently skipping
// the render-safety assertions.
function loadAdminConsoleShell() {
    const html = fs.readFileSync(path.join(ROOT, "web/admin_console.html"), "utf8");
    const shell = html.match(/<div class="shell">[\s\S]*?<\/div>\s*<script/);
    if (!shell) {
        throw new Error("admin_console.html no longer exposes a .shell container");
    }
    return shell[0].replace(/<script$/, "");
}

const HOSTILE_IMG = '<img src=x onerror="globalThis.__openclawXss=1">';
const HOSTILE_SCRIPT = '"><script>globalThis.__openclawXss=1</script>';
const HOSTILE_ATTR = "' onmouseover='globalThis.__openclawXss=1";

const HOSTILE_RUN = {
    run_id: `run-${HOSTILE_IMG}`,
    status: `failed${HOSTILE_SCRIPT}`,
    schedule_id: `sched-${HOSTILE_ATTR}`,
    template_id: `tmpl-${HOSTILE_IMG}`,
    started_at: `2026-09-06${HOSTILE_SCRIPT}`,
};

const HOSTILE_APPROVAL = {
    approval_id: `apr-${HOSTILE_IMG}`,
    template_id: `tmpl-${HOSTILE_SCRIPT}`,
    source: `chat-${HOSTILE_ATTR}`,
};

const HOSTILE_SCHEDULE = {
    schedule_id: `sch-${HOSTILE_ATTR}`,
    name: `nightly-${HOSTILE_IMG}`,
    enabled: true,
    trigger_type: `cron${HOSTILE_SCRIPT}`,
    template_id: `tmpl-${HOSTILE_IMG}`,
};

const HOSTILE_ERROR = `backend_failure ${HOSTILE_IMG}`;

function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

function createFetchRouter(overrides = {}) {
    const calls = [];
    const impl = vi.fn(async (url, options = {}) => {
        calls.push({ url: String(url), method: options.method || "GET", body: options.body });
        const target = String(url);
        for (const [suffix, handler] of Object.entries(overrides)) {
            if (target.includes(suffix)) {
                return handler(target, options);
            }
        }
        if (target.includes("/health")) {
            return jsonResponse({
                ok: true,
                pack: { name: "ComfyUI-OpenClaw", version: "test" },
                config: { provider: "openai", llm_key_configured: false },
                stats: { approvals_pending: 0, queue_depth: 0, observability: { total_dropped: 0 } },
                control_plane: { mode: "test" },
                deployment_profile: "unit",
                uptime_sec: 1,
            });
        }
        if (target.includes("/logs/tail")) {
            return jsonResponse({ ok: true, tail: "" });
        }
        if (target.includes("/runs")) {
            return jsonResponse({ ok: true, runs: [HOSTILE_RUN] });
        }
        if (target.includes("/approvals")) {
            return jsonResponse({ ok: true, approvals: [HOSTILE_APPROVAL] });
        }
        if (target.includes("/schedules")) {
            return jsonResponse({ ok: true, schedules: [HOSTILE_SCHEDULE] });
        }
        if (target.includes("/config")) {
            return jsonResponse({ ok: true, config: { provider: "openai" } });
        }
        return jsonResponse({ ok: true });
    });
    return { impl, calls };
}

async function settle() {
    for (let i = 0; i < 12; i += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

async function mountWith(router) {
    vi.stubGlobal("fetch", router.impl);
    const console_ = mountAdminConsole(document);
    await settle();
    return console_;
}

function assertNoLiveMarkup(container, label) {
    expect(container.querySelectorAll("img"), `${label}: injected img`).toHaveLength(0);
    expect(container.querySelectorAll("script"), `${label}: injected script`).toHaveLength(0);
    for (const element of container.querySelectorAll("*")) {
        for (const attribute of element.getAttributeNames()) {
            expect(attribute.toLowerCase().startsWith("on"), `${label}: ${attribute}`).toBe(false);
        }
    }
}

describe("S104 admin console render safety", () => {
    beforeEach(() => {
        localStorage.clear();
        delete globalThis.__openclawXss;
        document.body.innerHTML = loadAdminConsoleShell();
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        document.body.replaceChildren();
        delete globalThis.__openclawXss;
    });

    it("renders hostile run fields as literal text", async () => {
        await mountWith(createFetchRouter());

        const runsList = document.getElementById("runsList");
        assertNoLiveMarkup(runsList, "runs");
        expect(runsList.textContent).toContain(HOSTILE_RUN.run_id);
        expect(runsList.textContent).toContain(HOSTILE_RUN.template_id);
        expect(runsList.textContent).toContain(HOSTILE_RUN.schedule_id);
        expect(globalThis.__openclawXss).toBeUndefined();
    });

    it("renders hostile approval fields as literal text", async () => {
        await mountWith(createFetchRouter());

        const approvalsList = document.getElementById("approvalsList");
        assertNoLiveMarkup(approvalsList, "approvals");
        expect(approvalsList.textContent).toContain(HOSTILE_APPROVAL.approval_id);
        expect(approvalsList.textContent).toContain(HOSTILE_APPROVAL.template_id);
        expect(approvalsList.textContent).toContain(HOSTILE_APPROVAL.source);
    });

    it("renders hostile schedule fields as literal text", async () => {
        await mountWith(createFetchRouter());

        const schedulesList = document.getElementById("schedulesList");
        assertNoLiveMarkup(schedulesList, "schedules");
        expect(schedulesList.textContent).toContain(HOSTILE_SCHEDULE.name);
        expect(schedulesList.textContent).toContain(HOSTILE_SCHEDULE.schedule_id);
        expect(schedulesList.textContent).toContain(HOSTILE_SCHEDULE.trigger_type);
    });

    it("renders hostile approval and schedule fetch errors as literal text", async () => {
        await mountWith(
            createFetchRouter({
                "/approvals": () => jsonResponse({ ok: false, error: HOSTILE_ERROR }, 500),
                "/schedules": () => jsonResponse({ ok: false, error: HOSTILE_ERROR }, 500),
            }),
        );

        const approvalsList = document.getElementById("approvalsList");
        const schedulesList = document.getElementById("schedulesList");
        assertNoLiveMarkup(approvalsList, "approvals error");
        assertNoLiveMarkup(schedulesList, "schedules error");
        expect(approvalsList.textContent).toContain(HOSTILE_ERROR);
        expect(schedulesList.textContent).toContain(HOSTILE_ERROR);
    });

    it("keeps empty-state text for runs, approvals and schedules", async () => {
        await mountWith(
            createFetchRouter({
                "/runs": () => jsonResponse({ ok: true, runs: [] }),
                "/approvals": () => jsonResponse({ ok: true, approvals: [] }),
                "/schedules": () => jsonResponse({ ok: true, schedules: [] }),
            }),
        );

        expect(document.getElementById("runsList").textContent).toContain("No run records.");
        expect(document.getElementById("approvalsList").textContent).toContain("No pending approvals.");
        expect(document.getElementById("schedulesList").textContent).toContain("No schedules configured.");
        expect(document.querySelectorAll("#runsList .tiny").length).toBeGreaterThan(0);
    });

    it("keeps approval and schedule actions wired to the exact backend routes", async () => {
        const router = createFetchRouter();
        await mountWith(router);

        const approveButton = document.querySelector("#approvalsList button");
        expect(approveButton?.textContent).toBe("Approve");
        approveButton.click();
        await settle();

        const approveCall = router.calls.find(
            (call) => call.method === "POST" && call.url.includes("/approve"),
        );
        expect(approveCall, "approve request").toBeTruthy();
        expect(approveCall.url).toContain(encodeURIComponent(HOSTILE_APPROVAL.approval_id));

        const toggleButton = document.querySelector("#schedulesList button");
        expect(toggleButton?.textContent).toBe("Toggle");
        toggleButton.click();
        await settle();

        const toggleCall = router.calls.find(
            (call) => call.method === "POST" && call.url.includes("/toggle"),
        );
        expect(toggleCall, "toggle request").toBeTruthy();
        expect(toggleCall.url).toContain(encodeURIComponent(HOSTILE_SCHEDULE.schedule_id));
    });
});
