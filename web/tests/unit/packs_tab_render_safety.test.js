import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, utilsMock } = vi.hoisted(() => ({
    apiMock: {
        getPacks: vi.fn(),
        importPack: vi.fn(),
        exportPack: vi.fn(),
        uninstallPack: vi.fn(),
    },
    utilsMock: {
        clearError: vi.fn(),
        showError: vi.fn(),
    },
}));

vi.mock("../../openclaw_api.js", () => ({ openclawApi: apiMock }));
vi.mock("../../openclaw_utils.js", () => utilsMock);

import { PacksTab } from "../../tabs/packs_tab.js";

const HOSTILE_NAME = 'pack<img src=x onerror="globalThis.__openclawXss=1">';

async function renderPacks(packs) {
    apiMock.getPacks.mockResolvedValue({ ok: true, data: { packs } });
    const container = document.createElement("div");
    document.body.appendChild(container);
    PacksTab.render(container);
    for (let i = 0; i < 6; i += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
    return container;
}

describe("S104 packs tab render safety", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        delete globalThis.__openclawXss;
        Object.values(apiMock).forEach((fn) => fn.mockReset());
        Object.values(utilsMock).forEach((fn) => fn.mockReset());
    });

    it("renders a numeric pack version instead of throwing", async () => {
        // CRITICAL: the removed per-file helper called `.replace` on the raw
        // argument and threw `TypeError` for a numeric `version`, which blanked
        // the whole pack list. Keep a non-string field in this fixture.
        const container = await renderPacks([
            { name: "alpha", version: 2, type: "workflow", author: "tester" },
        ]);

        const list = container.querySelector("#pack-list") || container;
        expect(list.textContent).toContain("alpha");
        expect(list.textContent).toContain("v2");
        expect(utilsMock.showError).not.toHaveBeenCalled();
        expect(container.querySelector('button[data-action="export"]')?.dataset.version).toBe("2");
    });

    it("keeps a hostile pack name literal in text and in button attributes", async () => {
        const container = await renderPacks([
            { name: HOSTILE_NAME, version: "1.0.0", type: "workflow", author: "tester" },
        ]);

        expect(container.querySelectorAll("img")).toHaveLength(0);
        expect(globalThis.__openclawXss).toBeUndefined();
        expect(container.textContent).toContain(HOSTILE_NAME);

        const exportButton = container.querySelector('button[data-action="export"]');
        expect(exportButton.dataset.name).toBe(HOSTILE_NAME);
        for (const attribute of exportButton.getAttributeNames()) {
            expect(attribute.toLowerCase().startsWith("on")).toBe(false);
        }
    });
});
