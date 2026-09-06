import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
    beginSettingsRender,
    disposeSettingsRender,
    finishSettingsRender,
} from "../../../web/tabs/settings_tab_lifecycle.js";

import {
    buildContract,
    verifyContract,
} from "../../../scripts/verify_frontend_decomposition_contract.mjs";

const ROOT = path.resolve(import.meta.dirname, "../../..");

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    document.body.replaceChildren();
});

describe("R224 frontend decomposition contract", () => {
    it("matches the frozen API and Settings contract byte-for-byte", () => {
        expect(verifyContract()).toEqual({ ok: true, message: "FRONTEND-CONTRACT-PASS" });
    });

    it("has substantive native owner modules", () => {
        const owners = [
            "web/openclaw_api_config.js",
            "web/openclaw_api_generation.js",
            "web/openclaw_api_resources.js",
            "web/openclaw_api_models.js",
            "web/openclaw_api_events.js",
            "web/tabs/settings_tab_lifecycle.js",
            "web/tabs/settings_tab_status.js",
            "web/tabs/settings_tab_llm.js",
            "web/tabs/settings_tab_secrets.js",
            "web/tabs/settings_tab_logs.js",
            "web/tabs/settings_tab_dom.js",
        ];
        for (const owner of owners) {
            const source = fs.readFileSync(path.join(ROOT, owner), "utf8");
            expect(source.split(/\r?\n/).length, owner).toBeGreaterThan(20);
            expect(source, owner).not.toContain('from "./openclaw_api.js"');
            expect(source, owner).not.toContain('from "./settings_tab.js"');
        }
    });

    it("keeps one API singleton and the upstream contracts", () => {
        const contract = buildContract();
        const facade = fs.readFileSync(path.join(ROOT, "web/openclaw_api.js"), "utf8");
        expect((facade.match(/new OpenClawAPI\(\)/g) || [])).toHaveLength(1);
        expect(Object.keys(contract.upstream_contract_digests)).toHaveLength(3);
    });

    it("rejects an earlier Settings generation after a remount", () => {
        vi.useFakeTimers();
        const staleContainer = document.createElement("div");
        const currentContainer = document.createElement("div");
        const stale = beginSettingsRender(staleContainer);
        const callback = vi.fn();
        stale.schedule(callback, 0);

        const current = beginSettingsRender(currentContainer);
        expect(stale.signal.aborted).toBe(true);
        expect(stale.isCurrent()).toBe(false);
        expect(current.isCurrent()).toBe(true);

        finishSettingsRender(current);
        expect(disposeSettingsRender(currentContainer)).toBe(false);
        vi.runAllTimers();
        expect(callback).not.toHaveBeenCalled();
    });
});
