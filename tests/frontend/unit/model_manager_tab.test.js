import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, utilsMock } = vi.hoisted(() => ({
    apiMock: {
        searchModels: vi.fn(),
        listModelDownloadTasks: vi.fn(),
        listModelInstallations: vi.fn(),
        createModelDownloadTask: vi.fn(),
        cancelModelDownloadTask: vi.fn(),
        importDownloadedModel: vi.fn(),
    },
    utilsMock: {
        clearError: vi.fn(),
        showError: vi.fn(),
        showToast: vi.fn(),
    },
}));

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: apiMock,
}));

vi.mock("../../../web/openclaw_utils.js", () => utilsMock);

import { ModelManagerTab, mergeTaskDelta } from "../../../web/tabs/model_manager_tab.js";

describe("model_manager_tab", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        Object.values(apiMock).forEach((fn) => fn.mockReset());
        Object.values(utilsMock).forEach((fn) => fn.mockReset());
    });

    it("records persistent operator notifications when the initial search load fails", async () => {
        apiMock.searchModels.mockResolvedValue({
            ok: false,
            error: "search_failed",
        });
        apiMock.listModelDownloadTasks.mockResolvedValue({
            ok: true,
            data: { tasks: [] },
        });
        apiMock.listModelInstallations.mockResolvedValue({
            ok: true,
            data: { installations: [] },
        });

        const container = document.createElement("div");
        ModelManagerTab.render(container);
        await vi.waitFor(() => {
            expect(utilsMock.showError).toHaveBeenCalled();
        });

        expect(utilsMock.showError).toHaveBeenCalledWith(
            container,
            "search: search_failed"
        );
        expect(utilsMock.showToast).toHaveBeenCalledWith(
            "search: search_failed",
            "error",
            expect.objectContaining({
                persist: true,
                source: "model-manager",
                dedupeKey: "model-manager:refresh",
                action: expect.objectContaining({
                    payload: "model-manager",
                    type: "tab",
                }),
            })
        );
    });

    it("merges task deltas without duplicating existing rows", () => {
        const merged = mergeTaskDelta(
            [
                { task_id: "task-1", state: "running", created_at: 10, change_seq: 3 },
                { task_id: "task-2", state: "queued", created_at: 11, change_seq: 4 },
            ],
            [
                { task_id: "task-1", state: "completed", created_at: 10, change_seq: 5 },
                { task_id: "task-3", state: "queued", created_at: 12, change_seq: 6 },
            ]
        );

        expect(merged).toEqual([
            expect.objectContaining({ task_id: "task-3", state: "queued" }),
            expect.objectContaining({ task_id: "task-2", state: "queued" }),
            expect.objectContaining({ task_id: "task-1", state: "completed" }),
        ]);
    });

    it("lists only managed-install supported current ComfyUI folder keys", async () => {
        apiMock.searchModels.mockResolvedValue({
            ok: true,
            data: { items: [] },
        });
        apiMock.listModelDownloadTasks.mockResolvedValue({
            ok: true,
            data: { tasks: [] },
        });
        apiMock.listModelInstallations.mockResolvedValue({
            ok: true,
            data: { installations: [] },
        });

        const container = document.createElement("div");
        ModelManagerTab.render(container);
        await vi.waitFor(() => {
            expect(apiMock.searchModels).toHaveBeenCalled();
        });

        const options = Array.from(container.querySelectorAll("#mm-type option")).map(
            (option) => option.value
        );

        expect(options).toEqual(expect.arrayContaining([
            "text_encoders",
            "diffusion_models",
            "gligen",
            "latent_upscale_models",
            "hypernetworks",
            "photomaker",
            "model_patches",
            "geometry_estimation",
            "optical_flow",
            "detection",
        ]));
        expect(options).not.toContain("configs");
        expect(options).not.toContain("diffusers");
        expect(options).not.toContain("classifiers");
        expect(options).not.toContain("custom_nodes");
        expect(options).not.toContain("datasets");
    });
});
