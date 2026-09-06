import { openclawApi } from "../openclaw_api.js";
import { showError, clearError } from "../openclaw_utils.js";
import { escapeHtml } from "../openclaw_text_safety.js";

export const PacksTab = {
    id: "packs",
    title: "Packs",
    icon: "pi pi-box",

    render(container) {
        // --- 1. Static Layout ---
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-card" style="border-radius:0; border:none; border-bottom:1px solid var(--moltbot-color-border);">
                     <div class="openclaw-section-header">Asset Packs</div>
                     <div class="openclaw-error-box" style="display:none"></div>
                     <div class="openclaw-toolbar" style="margin-top:5px; display:flex; gap:5px; align-items:center;" id="pack-toolbar">
                        <input type="file" id="pack-import-input" accept=".zip" style="display:none">
                        <button class="openclaw-btn openclaw-btn-primary" id="pack-import-btn">Import Pack</button>
                        <button class="openclaw-btn openclaw-btn-sm" id="pack-refresh-btn" style="margin-left: auto;">
                            Refresh
                        </button>
                    </div>
                </div>

                <div id="pack-list" class="openclaw-scroll-area" style="padding:10px;">
                     <div class="openclaw-empty-state">Loading...</div>
                </div>
            </div>
        `;

        // --- 2. State & References ---
        const ui = {
            list: container.querySelector("#pack-list"),
            importBtn: container.querySelector("#pack-import-btn"),
            importInput: container.querySelector("#pack-import-input"),
            refreshBtn: container.querySelector("#pack-refresh-btn"),
        };

        // --- 3. View Logic ---

        const renderListItem = (pack) => {
            return `
                <div class="openclaw-card" style="margin-bottom: 10px; display: flex; justify-content: space-between; align-items: start;">
                    <div>
                        <div style="font-weight: bold; font-size: var(--moltbot-font-md); color: var(--moltbot-color-fg);">
                            ${escapeHtml(pack.name)} <span style="font-weight:normal; color:var(--moltbot-color-fg-muted);">v${escapeHtml(pack.version)}</span>
                        </div>
                        <div style="font-size: var(--moltbot-font-sm); color: var(--moltbot-color-fg-muted); margin-top: 4px;">
                            ${escapeHtml(pack.description || "No description")}
                        </div>
                        <div style="font-size: var(--moltbot-font-xs); color: #666; margin-top: 4px;">
                            Author: ${escapeHtml(pack.author || "Unknown")} • Type: ${escapeHtml(pack.type)}
                        </div>
                    </div>
                    <div style="display: flex; gap: 5px; flex-direction: column; align-items: flex-end;">
                        <button class="openclaw-btn openclaw-btn-sm" data-action="export" data-name="${escapeHtml(pack.name)}" data-version="${escapeHtml(pack.version)}">Export</button>
                        <button class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-action="delete" data-name="${escapeHtml(pack.name)}" data-version="${escapeHtml(pack.version)}">Uninstall</button>
                    </div>
                </div>
            `;
        };

        const renderList = (packs) => {
            if (!packs || packs.length === 0) {
                ui.list.innerHTML = '<div class="openclaw-empty-state">No packs installed.</div>';
                return;
            }
            ui.list.innerHTML = packs.map(renderListItem).join("");
        };

        // --- 4. Logic ---

        const loadPacks = async () => {
            clearError(container);
            ui.list.innerHTML = '<div style="padding: 10px; text-align: center;">Loading...</div>';

            try {
                const res = await openclawApi.getPacks();
                if (res.ok) {
                    renderList(res.data.packs || []);
                } else {
                    ui.list.innerHTML = '';
                    showError(container, res.error);
                }
            } catch (e) {
                ui.list.innerHTML = '';
                showError(container, "Failed to load packs: " + e.message);
            }
        };

        const handleImport = async (file) => {
            if (!file) return;
            const confirmOverwrite = confirm("Importing pack. Overwrite existing versions if present?");

            ui.importBtn.disabled = true;
            ui.importBtn.textContent = "Importing...";

            try {
                const res = await openclawApi.importPack(file, confirmOverwrite);
                if (res.ok) {
                    alert(`Pack ${res.data.pack.name} v${res.data.pack.version} installed successfully.`);
                    loadPacks();
                } else {
                    showError(container, `Import failed: ${res.error}`);
                }
            } catch (e) {
                showError(container, `Import failed: ${e.message}`);
            } finally {
                ui.importBtn.disabled = false;
                ui.importBtn.textContent = "Import Pack";
                ui.importInput.value = ""; // Reset
            }
        };

        const handleExport = async (name, version) => {
            // Trigger download via API client helper (which handles headers/blob)
            // Or use createObjectURL
            try {
                const res = await openclawApi.exportPack(name, version);
                if (res.ok) {
                    // Create object URL and click
                    const url = window.URL.createObjectURL(res.data);
                    const a = document.createElement("a");
                    a.style.display = "none";
                    a.href = url;
                    // Filename is usually in Content-Disposition but we can construct one
                    a.download = `${name}-${version}.zip`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                } else {
                    showError(container, `Export failed: ${res.error}`);
                }
            } catch (e) {
                showError(container, `Export failed: ${e.message}`);
            }
        };

        const handleDelete = async (name, version) => {
            if (!confirm(`Uninstall pack ${name} v${version}? This cannot be undone.`)) return;

            try {
                const res = await openclawApi.deletePack(name, version);
                if (res.ok) {
                    loadPacks();
                } else {
                    showError(container, `Uninstall failed: ${res.error}`);
                }
            } catch (e) {
                showError(container, `Uninstall failed: ${e.message}`);
            }
        };

        // --- 5. Event Binding ---

        ui.refreshBtn.onclick = loadPacks;
        ui.importBtn.onclick = () => ui.importInput.click();
        ui.importInput.onchange = (e) => handleImport(e.target.files[0]);

        ui.list.onclick = (e) => {
            const btn = e.target.closest("button[data-action]");
            if (!btn) return;

            const action = btn.dataset.action;
            const name = btn.dataset.name;
            const version = btn.dataset.version;

            if (action === "export") handleExport(name, version);
            else if (action === "delete") handleDelete(name, version);
        };

        // Initial Load
        loadPacks();
    }
};
