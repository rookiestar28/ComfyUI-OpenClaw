import { describe, expect, it, vi } from "vitest";
import {
    API_PREFIXES,
    STORAGE_KEYS,
    buildAdminTokenHeaders,
    buildRemoteAdminHeaders,
    getApiPathCandidates,
    getCompatibleSettingValue,
    getMirroredStorageValue,
    setMirroredStorageValue,
} from "../../../web/openclaw_compat.js";

describe("openclaw_compat", () => {
    it("builds canonical and legacy API path candidates", () => {
        expect(getApiPathCandidates("/openclaw/health")).toEqual([
            "/openclaw/health",
            "/moltbot/health",
        ]);
        expect(getApiPathCandidates("/moltbot/health")).toEqual([
            "/moltbot/health",
            "/openclaw/health",
        ]);
        expect(getApiPathCandidates("/history/demo")).toEqual(["/history/demo"]);
        expect(API_PREFIXES.canonical).toBe("/openclaw");
    });

    it("reads mirrored storage with primary precedence", () => {
        const storage = {
            getItem: vi.fn((key) => ({
                [STORAGE_KEYS.local.activeTab.primary]: "planner",
                [STORAGE_KEYS.local.activeTab.legacy]: "settings",
            })[key] ?? null),
        };

        expect(getMirroredStorageValue(storage, STORAGE_KEYS.local.activeTab)).toBe("planner");
    });

    it("writes and clears mirrored storage values", () => {
        const storage = {
            setItem: vi.fn(),
            removeItem: vi.fn(),
        };

        setMirroredStorageValue(storage, STORAGE_KEYS.local.activeTab, "library");
        expect(storage.setItem).toHaveBeenCalledWith("openclaw-active-tab", "library");
        expect(storage.setItem).toHaveBeenCalledWith("moltbot-active-tab", "library");

        setMirroredStorageValue(storage, STORAGE_KEYS.local.activeTab, "");
        expect(storage.removeItem).toHaveBeenCalledWith("openclaw-active-tab");
        expect(storage.removeItem).toHaveBeenCalledWith("moltbot-active-tab");
    });

    it("reads settings with canonical then legacy fallback", () => {
        const settings = {
            getSettingValue: vi.fn((key) => ({
                "OpenClaw.General.Enable": undefined,
                "Moltbot.General.Enable": false,
            })[key]),
        };

        expect(getCompatibleSettingValue(settings, "General.Enable", true)).toBe(false);
    });

    it("builds mirrored admin headers", () => {
        expect(buildAdminTokenHeaders("secret")).toEqual({
            "X-OpenClaw-Admin-Token": "secret",
            "X-Moltbot-Admin-Token": "secret",
        });
        expect(buildRemoteAdminHeaders("secret")).toEqual({
            "X-OpenClaw-Admin-Token": "secret",
            "X-Moltbot-Admin-Token": "secret",
            "X-OpenClaw-Obs-Token": "secret",
            "X-Moltbot-Obs-Token": "secret",
        });
    });
});
