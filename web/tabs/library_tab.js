import { openclawApi } from "../openclaw_api.js";
import { tabManager } from "../openclaw_tabs.js";
import {
    showError,
    clearError,
    parseJsonOrThrow,
    normalizeLegacyClassNames,
} from "../openclaw_utils.js";
import {
    normalizeLibraryCategory,
    filterLibraryItems,
    getLibraryApplyTarget,
} from "./library_tab_state.js";
import { escapeHtml } from "../openclaw_text_safety.js";

function renderApplyButtons(preset) {
    if (preset.category === "prompt") {
        return `
            <button class="openclaw-btn openclaw-btn-sm openclaw-btn-primary" data-action="apply" data-id="${preset.id}">Plan</button>
            <button class="openclaw-btn openclaw-btn-sm openclaw-btn-primary" data-action="apply-refiner" data-id="${preset.id}">Refine</button>
        `;
    }
    if (preset.category === "params") {
        return `<button class="openclaw-btn openclaw-btn-sm openclaw-btn-primary" data-action="apply" data-id="${preset.id}">Use</button>`;
    }
    return "";
}

function renderPresetItem(preset) {
    return `
        <div class="openclaw-list-item" style="padding: 10px; border-bottom: 1px solid var(--openclaw-color-border); display: flex; justify-content: space-between; align-items: center; gap: 10px;">
            <div>
                <div style="font-weight: bold;">${escapeHtml(preset.name)}</div>
                <div style="font-size: var(--openclaw-font-sm); color: var(--openclaw-color-fg-muted); margin-top:4px;">
                    <span class="openclaw-badge" style="background:#555; color:#eee;">${escapeHtml(preset.category)}</span>
                </div>
            </div>
            <div style="display: flex; gap: 5px; flex-wrap: wrap; justify-content: flex-end;">
                ${renderApplyButtons(preset)}
                <button class="openclaw-btn openclaw-btn-sm" data-action="edit" data-id="${preset.id}">Edit</button>
                <button class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-action="delete" data-id="${preset.id}">Del</button>
            </div>
        </div>
    `;
}

function renderPackItem(pack) {
    return `
        <div class="openclaw-list-item" style="padding: 10px; border-bottom: 1px solid var(--openclaw-color-border); display: flex; justify-content: space-between; align-items: center; gap: 10px;">
            <div>
                <div style="font-weight: bold;">${escapeHtml(pack.name)} <span style="font-weight:normal; opacity:0.7">v${escapeHtml(pack.version)}</span></div>
                <div style="font-size: var(--openclaw-font-sm); color: var(--openclaw-color-fg-muted); margin-top:4px;">
                    <span class="openclaw-badge" style="background:#2c4f7c; color:#eee;">${escapeHtml(pack.type)}</span>
                    <span style="margin-left:6px;">by ${escapeHtml(pack.author)}</span>
                </div>
            </div>
            <div style="display: flex; gap: 5px; flex-wrap: wrap; justify-content: flex-end;">
                <button class="openclaw-btn openclaw-btn-sm" data-action="export-pack" data-name="${pack.name}" data-ver="${pack.version}">Export</button>
                <button class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-action="delete-pack" data-name="${pack.name}" data-ver="${pack.version}">Uninst</button>
            </div>
        </div>
    `;
}

export const LibraryTab = {
    id: "library",
    title: "Library",
    icon: "pi pi-book",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-card" style="border-radius:0; border:none; border-bottom:1px solid var(--openclaw-color-border);">
                    <div class="openclaw-section-header">Asset Library</div>
                    <div class="openclaw-error-box" style="display:none"></div>
                    <div class="openclaw-input-group">
                        <input type="text" id="lib-search" class="openclaw-input" placeholder="Search...">
                    </div>
                    <div class="openclaw-toolbar" style="margin-top:8px; display:flex; gap:5px;" id="lib-filter-btns">
                        <button class="openclaw-btn openclaw-btn-primary" data-cat="all">All</button>
                        <button class="openclaw-btn" data-cat="prompt">Prompts</button>
                        <button class="openclaw-btn" data-cat="params">Params</button>
                        <button class="openclaw-btn" data-cat="packs">Packs</button>
                        <button class="openclaw-btn" id="lib-new-btn" style="margin-left: auto;">+ New</button>
                    </div>
                    <input type="file" id="lib-pack-upload" accept=".zip" style="display:none">
                </div>

                <div id="lib-list" class="openclaw-scroll-area" style="padding:0;">
                    <div class="openclaw-empty-state">Loading...</div>
                </div>
            </div>

            <div id="lib-editor-overlay" class="openclaw-modal-overlay" style="display:none;">
                <div id="lib-editor" class="openclaw-modal">
                    <div class="openclaw-modal-header">
                        <span id="lib-editor-title">Edit Preset</span>
                        <input type="hidden" id="lib-edit-id">
                    </div>
                    <div class="openclaw-modal-body">
                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Name</label>
                            <input type="text" id="lib-edit-name" class="openclaw-input">
                        </div>
                        <br>
                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Category</label>
                            <select id="lib-edit-cat" class="openclaw-select">
                                <option value="general">General</option>
                                <option value="prompt">Prompt</option>
                                <option value="params">Params</option>
                            </select>
                        </div>
                        <br>
                        <div class="openclaw-input-group">
                            <label class="openclaw-label">Content (JSON)</label>
                            <textarea id="lib-edit-params-json" class="openclaw-textarea openclaw-textarea-md"></textarea>
                        </div>
                    </div>
                    <div class="openclaw-modal-footer">
                        <button class="openclaw-btn" id="lib-editor-cancel">Cancel</button>
                        <button class="openclaw-btn openclaw-btn-primary" id="lib-editor-save">Save</button>
                    </div>
                </div>
            </div>
        `;
        normalizeLegacyClassNames(container);

        const ui = {
            list: container.querySelector("#lib-list"),
            search: container.querySelector("#lib-search"),
            filters: container.querySelector("#lib-filter-btns"),
            newBtn: container.querySelector("#lib-new-btn"),
            packUpload: container.querySelector("#lib-pack-upload"),
            modal: {
                overlay: container.querySelector("#lib-editor-overlay"),
                title: container.querySelector("#lib-editor-title"),
                id: container.querySelector("#lib-edit-id"),
                name: container.querySelector("#lib-edit-name"),
                cat: container.querySelector("#lib-edit-cat"),
                content: container.querySelector("#lib-edit-params-json"),
                save: container.querySelector("#lib-editor-save"),
                cancel: container.querySelector("#lib-editor-cancel"),
            },
        };

        const currentState = {
            category: null,
            items: [],
        };

        const renderList = () => {
            const filtered = filterLibraryItems(currentState.items, ui.search.value);
            if (filtered.length === 0) {
                ui.list.innerHTML = '<div class="openclaw-empty-state">No items found.</div>';
                return;
            }

            ui.list.innerHTML = currentState.category === "packs"
                ? filtered.map(renderPackItem).join("")
                : filtered.map(renderPresetItem).join("");
            normalizeLegacyClassNames(ui.list);
        };

        const loadContent = async () => {
            clearError(container);
            ui.list.innerHTML = '<div style="padding: 20px; text-align: center;">Loading...</div>';

            const category = normalizeLibraryCategory(currentState.category);
            const res = category === "packs"
                ? await openclawApi.getPacks()
                : await openclawApi.listPresets(category);

            if (res.ok) {
                currentState.items = res.data || (res.packs ? res.packs : []);
                renderList();
            } else {
                ui.list.innerHTML = "";
                showError(container, res.error);
            }
        };

        const openModal = (preset = null) => {
            ui.modal.overlay.style.display = "flex";
            if (preset) {
                ui.modal.title.textContent = "Edit Preset";
                ui.modal.id.value = preset.id;
                ui.modal.name.value = preset.name;
                ui.modal.cat.value = preset.category;
                ui.modal.content.value = JSON.stringify(preset.content, null, 2);
                return;
            }

            ui.modal.title.textContent = "New Preset";
            ui.modal.id.value = "";
            ui.modal.name.value = "New Preset";
            ui.modal.cat.value = "general";
            ui.modal.content.value = "{}";
        };

        const closeModal = () => {
            ui.modal.overlay.style.display = "none";
        };

        const savePreset = async () => {
            const id = ui.modal.id.value;
            const name = ui.modal.name.value.trim();
            const category = ui.modal.cat.value;
            let content;

            try {
                content = parseJsonOrThrow(ui.modal.content.value, "Content must be valid JSON");
            } catch (error) {
                alert(error.message);
                return;
            }

            if (!name) {
                alert("Name required");
                return;
            }

            ui.modal.save.textContent = "Saving...";
            ui.modal.save.disabled = true;

            const res = id
                ? await openclawApi.updatePreset(id, { name, category, content })
                : await openclawApi.createPreset({ name, category, content });

            ui.modal.save.textContent = "Save";
            ui.modal.save.disabled = false;

            if (res.ok) {
                closeModal();
                loadContent();
            } else {
                alert(`Save failed: ${res.error}`);
            }
        };

        const deleteItem = async (idOrName, version = null) => {
            if (!confirm("Are you sure you want to delete this item?")) return;

            const res = currentState.category === "packs"
                ? await openclawApi.deletePack(idOrName, version)
                : await openclawApi.deletePreset(idOrName);

            if (res.ok) {
                loadContent();
            } else {
                alert(`Delete failed: ${res.error}`);
            }
        };

        function applyPreset(preset, explicitTarget = null) {
            const targetTabId = getLibraryApplyTarget(preset, explicitTarget);
            if (!targetTabId) return;

            tabManager.activateTab(targetTabId);
            setTimeout(() => {
                try {
                    const content = preset.content;
                    if (targetTabId === "planner") {
                        const pos = document.getElementById("planner-out-pos");
                        const neg = document.getElementById("planner-out-neg");
                        const resDiv = document.getElementById("planner-results");
                        if (resDiv) resDiv.style.display = "flex";
                        if (pos && content.positive) pos.value = content.positive;
                        if (neg && content.negative) neg.value = content.negative;
                    } else if (targetTabId === "variants") {
                        const baseParams = document.getElementById("var-base-params");
                        if (baseParams && content.params) {
                            baseParams.value = JSON.stringify(content.params, null, 2);
                        }
                    } else if (targetTabId === "refiner") {
                        const pos = document.getElementById("refiner-orig-pos");
                        const neg = document.getElementById("refiner-orig-neg");
                        if (pos && content.positive) pos.value = content.positive;
                        if (neg && content.negative) neg.value = content.negative;
                    }
                } catch (error) {
                    console.error("Failed to apply preset:", error);
                    alert("Error applying preset to target tab.");
                }
            }, 100);
        }

        ui.search.addEventListener("input", renderList);

        ui.filters.addEventListener("click", (event) => {
            const btn = event.target.closest("button[data-cat]");
            if (!btn) return;

            ui.filters
                .querySelectorAll("button[data-cat]")
                .forEach((button) => button.classList.remove("openclaw-btn-primary"));
            btn.classList.add("openclaw-btn-primary");

            currentState.category = btn.dataset.cat || null;
            ui.newBtn.textContent = currentState.category === "packs" ? "Import" : "+ New";
            loadContent();
        });

        ui.newBtn.addEventListener("click", () => {
            if (currentState.category === "packs") {
                ui.packUpload.click();
                return;
            }
            openModal();
        });

        ui.packUpload.addEventListener("change", async () => {
            const file = ui.packUpload.files[0];
            if (!file) return;

            ui.list.innerHTML = '<div style="padding: 20px; text-align: center;">Importing Pack...</div>';
            const res = await openclawApi.importPack(file, false);
            if (res.ok) {
                alert(`Imported ${res.data.pack.name} v${res.data.pack.version}`);
                loadContent();
            } else {
                showError(container, res.error);
                setTimeout(loadContent, 2000);
            }
            ui.packUpload.value = "";
        });

        ui.list.addEventListener("click", async (event) => {
            const btn = event.target.closest("button[data-action]");
            if (!btn) return;
            const action = btn.dataset.action;

            if (action === "edit") {
                const res = await openclawApi.getPreset(btn.dataset.id);
                if (res.ok) openModal(res.data);
                else showError(container, "Failed to load preset details");
            } else if (action === "delete") {
                await deleteItem(btn.dataset.id);
            } else if (action === "delete-pack") {
                await deleteItem(btn.dataset.name, btn.dataset.ver);
            } else if (action === "export-pack") {
                const res = await openclawApi.exportPack(btn.dataset.name, btn.dataset.ver);
                if (res.ok) {
                    const url = window.URL.createObjectURL(res.data);
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `${btn.dataset.name}-${btn.dataset.ver}.zip`;
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
                } else {
                    showError(container, res.error);
                }
            } else if (action === "apply" || action === "apply-refiner") {
                const res = await openclawApi.getPreset(btn.dataset.id);
                if (res.ok) {
                    applyPreset(res.data, action === "apply-refiner" ? "refiner" : null);
                } else {
                    showError(container, "Failed to load preset for apply");
                }
            }
        });

        ui.modal.cancel.addEventListener("click", closeModal);
        ui.modal.save.addEventListener("click", savePreset);
        ui.modal.overlay.addEventListener("click", (event) => {
            if (event.target === ui.modal.overlay) closeModal();
        });

        loadContent();
    },
};
