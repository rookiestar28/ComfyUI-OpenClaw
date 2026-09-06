import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: {
        getCapabilities: vi.fn(),
        fetch: vi.fn(),
        _path: vi.fn((path) => path),
    },
}));

vi.mock("../../../web/openclaw_tabs.js", () => ({
    tabManager: {
        tabs: {},
        activateTab: vi.fn(),
    },
}));

const { OpenClawActions } = await import("../../../web/openclaw_actions.js");

describe("OpenClawActions", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("shows a blocked toast and warning banner when an action is disabled", async () => {
        const ui = {
            showBanner: vi.fn(),
            showConfirm: vi.fn(),
        };
        const tabs = {
            tabs: { "job-monitor": true },
            activateTab: vi.fn(),
        };
        const actions = new OpenClawActions(ui, {
            tabs,
            capabilities: {
                actions: {
                    doctor: {
                        enabled: false,
                        mutating: false,
                        blocked_reason: "Use control plane.",
                    },
                },
            },
        });

        await actions.openDoctor();

        expect(ui.showBanner).toHaveBeenCalledWith(
            "warning",
            "Action 'doctor' is disabled by policy."
        );
        expect(document.body.querySelector(".openclaw-blocked-toast")).not.toBeNull();
    });

    it("dispatches compare events for both modern and legacy listeners", () => {
        const ui = {
            showBanner: vi.fn(),
            showConfirm: vi.fn(),
        };
        const tabs = {
            tabs: {},
            activateTab: vi.fn(),
        };
        const modernListener = vi.fn();
        const legacyListener = vi.fn();
        window.addEventListener("openclaw:lab:compare", modernListener);
        window.addEventListener("moltbot:lab:compare", legacyListener);

        const actions = new OpenClawActions(ui, {
            tabs,
            capabilities: { actions: {} },
        });

        actions.openCompare({ id: 7, title: "Sampler" });
        vi.runAllTimers();

        expect(tabs.activateTab).toHaveBeenCalledWith("parameter-lab");
        expect(modernListener).toHaveBeenCalledTimes(1);
        expect(legacyListener).toHaveBeenCalledTimes(1);

        window.removeEventListener("openclaw:lab:compare", modernListener);
        window.removeEventListener("moltbot:lab:compare", legacyListener);
    });
});
