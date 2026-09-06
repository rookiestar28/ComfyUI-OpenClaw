import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, utilsMock } = vi.hoisted(() => ({
    apiMock: {
        parsePngInfo: vi.fn(),
    },
    utilsMock: {
        clearError: vi.fn(),
        copyToClipboard: vi.fn(),
        showError: vi.fn(),
    },
}));

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: apiMock,
}));

vi.mock("../../../web/openclaw_utils.js", () => utilsMock);

import { PngInfoTab } from "../../../web/tabs/png_info_tab.js";

describe("png_info_tab", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        apiMock.parsePngInfo.mockReset();
        Object.values(utilsMock).forEach((fn) => fn.mockReset());

        class MockFileReader {
            readAsDataURL(file) {
                this.result = `data:${file.type};base64,ZmFrZQ==`;
                queueMicrotask(() => this.onload?.());
            }
        }
        globalThis.FileReader = MockFileReader;
    });

    it("parses selected images and renders structured metadata with copy actions", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: true,
            data: {
                source: "a1111",
                info: "A1111 metadata detected.",
                parameters: {
                    positive_prompt: "portrait lighting",
                    negative_prompt: "lowres",
                    Steps: "28",
                    Model: "demoModel",
                },
                items: {
                    workflow: {
                        nodes: [{ id: 1, type: "KSampler" }],
                    },
                },
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "meta.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(apiMock.parsePngInfo).toHaveBeenCalledTimes(1);
        });

        const scrollArea = container.querySelector(".openclaw-scroll-area");
        const firstCard = scrollArea.firstElementChild;
        expect(firstCard.querySelector("#pnginfo-dropzone")).toBeTruthy();
        expect(firstCard.querySelector("#pnginfo-preview-image")).toBeTruthy();

        const results = container.querySelector("#pnginfo-results");
        expect(results.firstElementChild.className).toContain("openclaw-pnginfo-prompts");

        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("demoModel");
        expect(container.querySelector("#pnginfo-positive").textContent).toContain("portrait lighting");
        expect(container.querySelector("#pnginfo-negative").textContent).toContain("lowres");
        expect(container.querySelector("#pnginfo-raw").textContent).toContain('"type": "KSampler"');

        container.querySelector('[data-action="copy-positive"]').click();
        expect(utilsMock.copyToClipboard).toHaveBeenCalledWith(
            "portrait lighting",
            expect.any(HTMLButtonElement)
        );

        container.querySelector('[data-action="copy-negative"]').click();
        expect(utilsMock.copyToClipboard).toHaveBeenCalledWith(
            "lowres",
            expect.any(HTMLButtonElement)
        );
    });

    it("shows an empty metadata status when the backend returns no metadata blocks", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: true,
            data: {
                source: "unknown",
                info: "",
                parameters: {},
                items: {},
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "plain.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(container.querySelector("#pnginfo-status").textContent).toBe("No metadata found");
        });

        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("UNKNOWN");
        expect(container.querySelector("#pnginfo-raw").textContent).toContain("No raw metadata blocks found.");
    });

    it("renders ComfyUI semantic extraction fields without hiding raw metadata", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: true,
            data: {
                source: "comfyui",
                info: "ComfyUI metadata detected. Extracted prompt and sampler fields from saved graph.",
                parameters: {
                    positive_prompt: "Global: cinematic portrait\nLocal: sharp details",
                    negative_prompt: "blurry",
                    Steps: 30,
                    Sampler: "dpmpp_2m",
                    Scheduler: "karras",
                    Model: "sdxl-base.safetensors",
                },
                items: {
                    prompt: {
                        10: {
                            class_type: "KSamplerAdvanced",
                        },
                    },
                    workflow: {
                        nodes: [{ id: 10, type: "KSamplerAdvanced" }],
                    },
                },
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "comfy.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(container.querySelector("#pnginfo-status").textContent).toBe("Metadata ready");
        });

        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("COMFYUI");
        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("sdxl-base.safetensors");
        expect(container.querySelector("#pnginfo-summary-card").textContent).toContain("dpmpp_2m");
        expect(container.querySelector("#pnginfo-positive").textContent).toContain("cinematic portrait");
        expect(container.querySelector("#pnginfo-negative").textContent).toContain("blurry");
        expect(container.querySelector("#pnginfo-raw").textContent).toContain('"class_type": "KSamplerAdvanced"');
    });

    it("shows a friendly oversize message instead of the raw backend code", async () => {
        apiMock.parsePngInfo.mockResolvedValue({
            ok: false,
            error: "image_b64_too_large",
            data: {
                detail: "image_b64 exceeds the PNG Info limit (64 MiB). PNG Info must inspect the original metadata-bearing file without browser recompression.",
            },
        });

        const container = document.createElement("div");
        PngInfoTab.render(container);

        const fileInput = container.querySelector("#pnginfo-file-input");
        const file = new File(["x"], "huge.png", { type: "image/png" });
        Object.defineProperty(fileInput, "files", {
            configurable: true,
            value: [file],
        });

        fileInput.dispatchEvent(new Event("change"));

        await vi.waitFor(() => {
            expect(container.querySelector("#pnginfo-status").textContent).toBe("Load failed");
        });

        expect(utilsMock.showError).toHaveBeenCalledWith(
            container,
            "image_b64 exceeds the PNG Info limit (64 MiB). PNG Info must inspect the original metadata-bearing file without browser recompression."
        );
    });
});
