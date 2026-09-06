import { openclawApi } from "../openclaw_api.js";
import { clearError, copyToClipboard, showError } from "../openclaw_utils.js";
import { escapeHtml } from "../openclaw_text_safety.js";

function isImageFile(file) {
    return Boolean(file?.type?.startsWith("image/"));
}

function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
        reader.readAsDataURL(file);
    });
}

function firstImageFileFromList(files) {
    if (!files || typeof files.length !== "number") {
        return null;
    }
    for (const file of Array.from(files)) {
        if (isImageFile(file)) {
            return file;
        }
    }
    return null;
}

function humanizeLabel(key) {
    const mapping = {
        positive_prompt: "Prompt",
        negative_prompt: "Negative Prompt",
        source: "Source",
        info: "Info",
    };
    if (mapping[key]) {
        return mapping[key];
    }
    return String(key)
        .replace(/_/g, " ")
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatMetadataValue(value) {
    if (value == null) {
        return "";
    }
    if (typeof value === "string") {
        return value;
    }
    return JSON.stringify(value, null, 2);
}

function orderRawEntries(items) {
    const entries = Object.entries(items || {}).filter(([, value]) => value != null);
    entries.sort(([left], [right]) => {
        const leftRank = left === "prompt" ? 0 : left === "workflow" ? 1 : 2;
        const rightRank = right === "prompt" ? 0 : right === "workflow" ? 1 : 2;
        if (leftRank !== rightRank) {
            return leftRank - rightRank;
        }
        return left.localeCompare(right);
    });
    return entries;
}

function buildSummaryRows(result) {
    const parameters = result?.parameters || {};
    const rows = [
        ["source", result?.source || "unknown"],
    ];

    if (parameters.Steps) rows.push(["Steps", parameters.Steps]);
    if (parameters.Sampler) rows.push(["Sampler", parameters.Sampler]);
    if (parameters["CFG scale"]) rows.push(["CFG scale", parameters["CFG scale"]]);
    if (parameters.Seed) rows.push(["Seed", parameters.Seed]);
    if (parameters.Size) rows.push(["Size", parameters.Size]);
    if (parameters.Model) rows.push(["Model", parameters.Model]);
    if (parameters["Model hash"]) rows.push(["Model hash", parameters["Model hash"]]);

    for (const [key, value] of Object.entries(parameters)) {
        if (
            value == null ||
            key === "positive_prompt" ||
            key === "negative_prompt" ||
            key === "Size-1" ||
            key === "Size-2" ||
            rows.some(([existing]) => existing === key)
        ) {
            continue;
        }
        rows.push([key, value]);
    }

    return rows;
}

function formatPngInfoError(errorLike) {
    const code = errorLike?.error || errorLike?.data?.error || "";
    const detail = errorLike?.data?.detail || errorLike?.detail || "";
    if (code === "image_b64_too_large") {
        return detail || "The selected image exceeds the PNG Info upload limit. PNG Info must inspect the original metadata-bearing file without browser recompression.";
    }
    if (detail) {
        return detail;
    }
    if (code) {
        return code;
    }
    if (errorLike?.message) {
        return errorLike.message;
    }
    return String(errorLike || "pnginfo_request_failed");
}

function renderPromptBlock(title, value, actionId) {
    const body = escapeHtml(value || "");
    const copyButton = value
        ? `<button type="button" class="openclaw-btn openclaw-btn-sm" data-action="${actionId}">Copy</button>`
        : "";
    const emptyState = value ? "" : '<div class="openclaw-empty-state">No value detected.</div>';
    return `
        <div class="openclaw-card">
            <div class="openclaw-pnginfo-card-header">
                <div class="openclaw-section-header" style="margin-bottom:0;border-bottom:none;padding-bottom:0;">${escapeHtml(title)}</div>
                ${copyButton}
            </div>
            ${value
                ? `<pre class="openclaw-pnginfo-pre openclaw-pnginfo-pre-prompt">${body}</pre>`
                : emptyState}
        </div>
    `;
}

function renderSummaryGrid(rows) {
    if (!rows.length) {
        return '<div class="openclaw-empty-state">No structured generation fields detected.</div>';
    }

    return `
        <div class="openclaw-pnginfo-grid">
            ${rows.map(([key, value]) => `
                <div class="openclaw-pnginfo-grid-row">
                    <div class="openclaw-pnginfo-grid-key">${escapeHtml(humanizeLabel(key))}</div>
                    <div class="openclaw-pnginfo-grid-value">${escapeHtml(String(value))}</div>
                </div>
            `).join("")}
        </div>
    `;
}

function renderRawBlocks(items) {
    const entries = orderRawEntries(items);
    if (!entries.length) {
        return '<div class="openclaw-empty-state">No raw metadata blocks found.</div>';
    }

    return entries.map(([key, value]) => `
        <div class="openclaw-card">
            <div class="openclaw-pnginfo-card-header">
                <div class="openclaw-section-header" style="margin-bottom:0;border-bottom:none;padding-bottom:0;">${escapeHtml(humanizeLabel(key))}</div>
            </div>
            <pre class="openclaw-pnginfo-pre">${escapeHtml(formatMetadataValue(value))}</pre>
        </div>
    `).join("");
}

export const PngInfoTab = {
    id: "png-info",
    title: "PNG Info",
    icon: "pi pi-image",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-scroll-area">
                    <div class="openclaw-card">
                        <div class="openclaw-section-header">PNG Info</div>
                        <div class="openclaw-error-box" style="display:none"></div>
                        <div class="openclaw-pnginfo-top">
                            <div id="pnginfo-dropzone" class="openclaw-pnginfo-dropzone" tabindex="0" role="button" aria-label="PNG Info dropzone">
                                <input id="pnginfo-file-input" type="file" accept="image/*" hidden />
                                <div class="openclaw-pnginfo-dropzone-title">Drop an image here</div>
                                <div class="openclaw-pnginfo-dropzone-copy">Click to browse, or focus this panel and paste an image.</div>
                                <div class="openclaw-pnginfo-dropzone-actions">
                                    <button type="button" id="pnginfo-select-btn" class="openclaw-btn openclaw-btn-sm openclaw-btn-primary">Choose Image</button>
                                </div>
                            </div>
                            <div class="openclaw-card openclaw-pnginfo-preview-card">
                                <div class="openclaw-pnginfo-card-header">
                                    <div class="openclaw-section-header" style="margin-bottom:0;border-bottom:none;padding-bottom:0;">Preview</div>
                                    <div id="pnginfo-status" class="openclaw-status">Idle</div>
                                </div>
                                <div id="pnginfo-preview-empty" class="openclaw-empty-state">No image loaded yet.</div>
                                <img id="pnginfo-preview-image" class="openclaw-pnginfo-preview-image" alt="PNG Info preview" style="display:none" />
                            </div>
                        </div>
                        <div class="openclaw-note">
                            Read-only metadata inspection only. This tab does not send prompt data into other tabs or workflows.
                        </div>
                    </div>
                    <div id="pnginfo-empty-state" class="openclaw-empty-state">
                        Load an image to inspect embedded A1111 or ComfyUI metadata.
                    </div>
                    <div id="pnginfo-results" style="display:none;" class="openclaw-split-v">
                        <div class="openclaw-grid-2 openclaw-pnginfo-prompts">
                            <div id="pnginfo-positive"></div>
                            <div id="pnginfo-negative"></div>
                        </div>
                        <div id="pnginfo-summary-card" class="openclaw-card"></div>
                        <div>
                            <div class="openclaw-section-header">Raw Metadata</div>
                            <div id="pnginfo-raw" class="openclaw-split-v"></div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const ui = {
            dropzone: container.querySelector("#pnginfo-dropzone"),
            fileInput: container.querySelector("#pnginfo-file-input"),
            selectBtn: container.querySelector("#pnginfo-select-btn"),
            status: container.querySelector("#pnginfo-status"),
            previewEmpty: container.querySelector("#pnginfo-preview-empty"),
            previewImage: container.querySelector("#pnginfo-preview-image"),
            emptyState: container.querySelector("#pnginfo-empty-state"),
            results: container.querySelector("#pnginfo-results"),
            summary: container.querySelector("#pnginfo-summary-card"),
            positive: container.querySelector("#pnginfo-positive"),
            negative: container.querySelector("#pnginfo-negative"),
            raw: container.querySelector("#pnginfo-raw"),
        };

        const state = {
            previewUrl: "",
        };

        const setStatus = (text, variant = "") => {
            ui.status.textContent = text;
            ui.status.className = `openclaw-status${variant ? ` ${variant}` : ""}`;
        };

        const resetResults = () => {
            ui.emptyState.style.display = "block";
            ui.results.style.display = "none";
            ui.summary.innerHTML = "";
            ui.positive.innerHTML = "";
            ui.negative.innerHTML = "";
            ui.raw.innerHTML = "";
        };

        const setPreview = (dataUrl) => {
            state.previewUrl = dataUrl || "";
            if (state.previewUrl) {
                ui.previewImage.src = state.previewUrl;
                ui.previewImage.style.display = "block";
                ui.previewEmpty.style.display = "none";
            } else {
                ui.previewImage.removeAttribute("src");
                ui.previewImage.style.display = "none";
                ui.previewEmpty.style.display = "block";
            }
        };

        const renderResult = (result) => {
            const rows = buildSummaryRows(result);
            const positivePrompt = result?.parameters?.positive_prompt || "";
            const negativePrompt = result?.parameters?.negative_prompt || "";
            const rawItems = result?.items || {};

            ui.summary.innerHTML = `
                <div class="openclaw-pnginfo-card-header">
                    <div class="openclaw-section-header" style="margin-bottom:0;border-bottom:none;padding-bottom:0;">Generation Summary</div>
                    <span class="openclaw-pnginfo-source-badge openclaw-pnginfo-source-${escapeHtml(result?.source || "unknown")}">${escapeHtml((result?.source || "unknown").toUpperCase())}</span>
                </div>
                ${result?.info
                    ? `<div class="openclaw-note" style="margin-top:4px;">${escapeHtml(result.info)}</div>`
                    : ""}
                ${renderSummaryGrid(rows)}
            `;
            ui.positive.innerHTML = renderPromptBlock("Prompt", positivePrompt, "copy-positive");
            ui.negative.innerHTML = renderPromptBlock("Negative Prompt", negativePrompt, "copy-negative");
            ui.raw.innerHTML = renderRawBlocks(rawItems);
            ui.emptyState.style.display = "none";
            ui.results.style.display = "flex";

            const positiveCopyBtn = container.querySelector('[data-action="copy-positive"]');
            const negativeCopyBtn = container.querySelector('[data-action="copy-negative"]');
            if (positiveCopyBtn) {
                positiveCopyBtn.addEventListener("click", () => copyToClipboard(positivePrompt, positiveCopyBtn));
            }
            if (negativeCopyBtn) {
                negativeCopyBtn.addEventListener("click", () => copyToClipboard(negativePrompt, negativeCopyBtn));
            }
        };

        const handleFile = async (file) => {
            if (!isImageFile(file)) {
                showError(container, "Please choose a single image file.");
                setStatus("Unsupported file", "error");
                return;
            }

            clearError(container);
            resetResults();
            setStatus("Reading image...", "");

            try {
                const imageB64 = await readFileAsDataUrl(file);
                setPreview(imageB64);
                setStatus("Inspecting metadata...", "");
                const res = await openclawApi.parsePngInfo(imageB64);
                if (!res?.ok) {
                    throw new Error(formatPngInfoError(res));
                }
                renderResult(res.data || {});
                if (res?.data?.source === "unknown" && !Object.keys(res?.data?.items || {}).length) {
                    setStatus("No metadata found", "");
                } else {
                    setStatus("Metadata ready", "ok");
                }
            } catch (error) {
                resetResults();
                setStatus("Load failed", "error");
                showError(container, formatPngInfoError(error));
            }
        };

        const chooseFromFileList = async (files) => {
            const file = firstImageFileFromList(files);
            if (!file) {
                showError(container, "Please drop or paste an image file.");
                setStatus("No image detected", "error");
                return;
            }
            await handleFile(file);
        };

        const handleDrop = async (event) => {
            event.preventDefault();
            event.stopPropagation();
            ui.dropzone.classList.remove("is-dragover");
            await chooseFromFileList(event.dataTransfer?.files);
        };

        const handlePaste = async (event) => {
            const file = firstImageFileFromList(event.clipboardData?.files);
            if (!file) {
                return;
            }
            // IMPORTANT: keep paste scoped to the tab surface; do not promote this to a document-level handler.
            event.preventDefault();
            event.stopPropagation();
            await handleFile(file);
        };

        ui.selectBtn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            ui.fileInput.click();
        });
        ui.dropzone.addEventListener("click", () => {
            ui.dropzone.focus();
            ui.fileInput.click();
        });
        ui.dropzone.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                ui.fileInput.click();
            }
        });
        ui.fileInput.addEventListener("change", async () => {
            await chooseFromFileList(ui.fileInput.files);
            ui.fileInput.value = "";
        });

        ["dragenter", "dragover"].forEach((type) => {
            ui.dropzone.addEventListener(type, (event) => {
                event.preventDefault();
                event.stopPropagation();
                ui.dropzone.classList.add("is-dragover");
            });
        });
        ["dragleave", "dragend"].forEach((type) => {
            ui.dropzone.addEventListener(type, () => {
                ui.dropzone.classList.remove("is-dragover");
            });
        });
        ui.dropzone.addEventListener("drop", (event) => {
            void handleDrop(event);
        });
        ui.dropzone.addEventListener("paste", (event) => {
            void handlePaste(event);
        });
        container.addEventListener("paste", (event) => {
            void handlePaste(event);
        });

        setPreview("");
        resetResults();
    },
};
