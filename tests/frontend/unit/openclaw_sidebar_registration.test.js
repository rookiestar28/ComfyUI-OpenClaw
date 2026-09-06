import { describe, expect, it, vi } from "vitest";

import { registerOpenClawSidebar } from "../../../web/openclaw_sidebar_registration.js";

describe("openclaw sidebar registration", () => {
    const tab = {
        id: "comfyui-openclaw",
        type: "custom",
        title: "OpenClaw",
    };

    it("prefers the current sidebar store API when available", () => {
        const current = vi.fn();
        const legacy = vi.fn();

        const result = registerOpenClawSidebar(
            {
                extensionManager: {
                    sidebarTab: { registerSidebarTab: current },
                    registerSidebarTab: legacy,
                },
            },
            tab
        );

        expect(result).toEqual({ ok: true, api: "sidebarTab.registerSidebarTab" });
        expect(current).toHaveBeenCalledWith(tab);
        expect(legacy).not.toHaveBeenCalled();
    });

    it("falls back to the deprecated facade for older frontend hosts", () => {
        const legacy = vi.fn();

        const result = registerOpenClawSidebar(
            {
                extensionManager: {
                    registerSidebarTab: legacy,
                },
            },
            tab
        );

        expect(result).toEqual({ ok: true, api: "extensionManager.registerSidebarTab" });
        expect(legacy).toHaveBeenCalledWith(tab);
    });

    it("reports a missing sidebar API without swallowing the legacy menu fallback path", () => {
        const result = registerOpenClawSidebar({ extensionManager: {} }, tab);

        expect(result.ok).toBe(false);
        expect(result.api).toBe("missing");
        expect(result.error).toBeInstanceOf(Error);
    });
});
