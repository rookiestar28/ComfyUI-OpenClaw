import { describe, expect, it, vi } from "vitest";

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: { _path: (path) => path, fetch: async () => ({ ok: false }) },
}));
vi.mock("../../../web/openclaw_ui.js", () => ({ openclawUI: { showBanner: () => {} } }));

import config from "../../../vitest.config.mjs";
import { app } from "./support/host_app_stub.js";
import { api } from "./support/host_api_stub.js";

describe("host script resolution for unit tests", () => {
    const aliases = config.resolve.alias;
    const aliasFor = (specifier) => aliases.find((entry) => entry.find.test(specifier));

    it("maps only the exact host script specifiers to the stubs", () => {
        expect(aliases).toHaveLength(2);
        for (const entry of aliases) {
            expect(entry.find).toBeInstanceOf(RegExp);
            expect(entry.find.source.startsWith("^")).toBe(true);
            expect(entry.find.source.endsWith("$")).toBe(true);
        }
        expect(aliasFor("../../../scripts/app.js")?.replacement).toMatch(/host_app_stub\.js$/);
        expect(aliasFor("../../scripts/app.js")?.replacement).toMatch(/host_app_stub\.js$/);
        expect(aliasFor("../../../scripts/api.js")?.replacement).toMatch(/host_api_stub\.js$/);
        expect(aliasFor("../scripts/api.js")?.replacement).toMatch(/host_api_stub\.js$/);
    });

    it("leaves lookalike and unrelated specifiers alone", () => {
        for (const specifier of [
            "../../../scripts/app.js?raw",
            "../../../scripts/app.json",
            "../../../scripts/app.js.map",
            "../../../scripts/ui/app.js",
            "../../../my_scripts/app.js",
            "../../../scripts/application.js",
            "scripts/app.js",
            "/scripts/app.js",
            "./scripts/app.js",
            "../openclaw_api.js",
            "../../../scripts/api.jsx",
        ]) {
            expect(aliasFor(specifier), specifier).toBeUndefined();
        }
    });

    it("resolves the real tab's host imports to the shared stub objects", async () => {
        const { ParameterLabTab } = await import("../../../web/tabs/parameter_lab_tab.js");
        app.graph = { _nodes: [{ id: 7, type: "Probe", title: "Probe node", widgets: [] }] };
        try {
            expect(ParameterLabTab.getNodeCatalog().map((node) => node.id)).toEqual(["7"]);
        } finally {
            delete app.graph;
        }
        expect(api).toEqual({});
    });
});
