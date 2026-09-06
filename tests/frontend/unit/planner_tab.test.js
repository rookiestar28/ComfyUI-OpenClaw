import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock } = vi.hoisted(() => ({
    apiMock: {
        listPlannerProfiles: vi.fn(),
    },
}));

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: apiMock,
}));

vi.mock("../../../web/openclaw_utils.js", () => ({
    showError: vi.fn(),
    clearError: vi.fn(),
    showToast: vi.fn(),
    createRequestLifecycleController: vi.fn(() => ({
        begin: vi.fn(() => null),
        setStage: vi.fn(),
        end: vi.fn(),
        cancel: vi.fn(() => false),
    })),
}));

import { PlannerTab } from "../../../web/tabs/planner_tab.js";

describe("planner_tab", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        apiMock.listPlannerProfiles.mockReset();
    });

    it("loads planner profiles from the backend registry", async () => {
        apiMock.listPlannerProfiles.mockResolvedValue({
            ok: true,
            data: {
                profiles: [
                    { id: "Photo", label: "Photo Real" },
                    { id: "Sketch", label: "Sketch Draft" },
                ],
                default_profile: "Sketch",
            },
        });

        const container = document.createElement("div");
        await PlannerTab.render(container);

        const select = container.querySelector("#planner-profile");
        const options = [...select.querySelectorAll("option")].map((opt) => ({
            value: opt.value,
            text: opt.textContent,
        }));

        expect(options).toEqual([
            { value: "Photo", text: "Photo Real" },
            { value: "Sketch", text: "Sketch Draft" },
        ]);
        expect(select.value).toBe("Sketch");
    });

    it("falls back to built-in profiles when the API fails", async () => {
        apiMock.listPlannerProfiles.mockRejectedValue(new Error("network down"));

        const container = document.createElement("div");
        await PlannerTab.render(container);

        const select = container.querySelector("#planner-profile");
        const options = [...select.querySelectorAll("option")].map((opt) => opt.value);

        expect(options).toEqual(["SDXL-v1", "Flux-Dev"]);
        expect(select.value).toBe("SDXL-v1");
    });
});
