import { describe, expect, it } from "vitest";
import {
    normalizeLibraryCategory,
    filterLibraryItems,
    getLibraryApplyTarget,
} from "../../../web/tabs/library_tab_state.js";

describe("library_tab_state", () => {
    it("normalizes all-like categories to null", () => {
        expect(normalizeLibraryCategory(null)).toBeNull();
        expect(normalizeLibraryCategory("all")).toBeNull();
        expect(normalizeLibraryCategory("packs")).toBe("packs");
    });

    it("filters items by case-insensitive name matches", () => {
        const items = [
            { name: "Portrait Prompt" },
            { name: "Landscape Params" },
            { name: "Workflow Pack" },
        ];

        expect(filterLibraryItems(items, "prompt")).toEqual([{ name: "Portrait Prompt" }]);
        expect(filterLibraryItems(items, "PaR")).toEqual([{ name: "Landscape Params" }]);
        expect(filterLibraryItems(items, "")).toEqual(items);
    });

    it("resolves apply targets from explicit target or category", () => {
        expect(getLibraryApplyTarget({ category: "prompt" })).toBe("planner");
        expect(getLibraryApplyTarget({ category: "params" })).toBe("variants");
        expect(getLibraryApplyTarget({ category: "general" }, "refiner")).toBe("refiner");
        expect(getLibraryApplyTarget({ category: "general" })).toBeNull();
    });
});
