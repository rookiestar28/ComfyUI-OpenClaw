/** Render LLM configuration controls behind the stable Settings facade. */
import { addRow, createFormRow, createHelpButton, createSection } from "./settings_tab_dom.js";

export function renderSettingsLlm({ scroll, configRes, api, session, getAdminErrorMessage, isCurrent }) {
    // -- LLM Settings Section --
    const llmSec = createSection("LLM Settings");
    if (configRes.ok) {
        // R54: Null-safe destructuring with defaults
        const data = configRes.data || {};
        const config = data.config || {};
        const sources = data.sources || {};
        const providers = data.providers || [];
        // R53: Apply feedback (optional, for debug/toast later)
        const applyInfo = data.apply || {};
        // R70: Settings schema (for frontend validation)
        const schema = data.schema || {};


        // Provider dropdown
        const providerRow = createFormRow("Provider", sources.provider === "env");
        const providerSelect = document.createElement("select");
        providerSelect.className = "openclaw-input openclaw-input moltbot-input";
        providerSelect.disabled = sources.provider === "env";
        providers.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p.id;
            opt.textContent = p.label;
            if (p.id === config.provider) opt.selected = true;
            providerSelect.appendChild(opt);
        });
        providerRow.appendChild(providerSelect);
        llmSec.appendChild(providerRow);

        // R60: Reset model list when provider changes (avoids showing stale models from another provider).
        const resetModelList = () => {
            modelsLoaded = false;
            lastLoadedModels = [];
            modelSelect.innerHTML = "";
            modelDatalist.innerHTML = "";
            modelsStatus.textContent = "";
            modelsStatus.className = "openclaw-status openclaw-status moltbot-status";
            updateModelUiVisibility();
        };
        providerSelect.onchange = () => resetModelList();

        // Model input
        const modelRow = createFormRow("Model", sources.model === "env");
        const modelWrap = document.createElement("div");
        modelWrap.className = "openclaw-model-controls";
        modelWrap.style.display = "flex";
        modelWrap.style.gap = "8px";
        modelWrap.style.alignItems = "center";

        // Model selection UX:
        // - Default: free-text input (works even if model listing isn't supported).
        // - After "Load Models": show a real <select> for discoverability + still allow "Custom…".
        const modelInput = document.createElement("input");
        modelInput.type = "text";
        modelInput.className = "openclaw-input openclaw-input moltbot-input";
        modelInput.value = config.model || "";
        modelInput.disabled = sources.model === "env";
        modelInput.style.flex = "1";

        const modelSelect = document.createElement("select");
        modelSelect.className = "openclaw-input openclaw-input moltbot-input";
        modelSelect.disabled = sources.model === "env";
        modelSelect.style.flex = "1";
        modelSelect.style.display = "none"; // shown after models load

        const MODEL_CUSTOM = "__custom__";

        // Datalist for remote suggestions (used in custom/free-text mode)
        const modelListId = "openclaw-model-list";
        modelInput.setAttribute("list", modelListId);
        const modelDatalist = document.createElement("datalist");
        modelDatalist.id = modelListId;

        let lastLoadedModels = [];
        let modelsLoaded = false;

        const updateModelUiVisibility = () => {
            // IMPORTANT (UX): Users expect an actual dropdown after "Load Models" even if the current
            // model is not in the returned list (e.g., switching provider but model still set to an
            // old value like "gpt-4o-mini"). Keep the <select> visible and use "Custom…" as a bridge.
            const showSelect = modelsLoaded;
            const showInput = !modelsLoaded || modelSelect.value === MODEL_CUSTOM;

            modelSelect.style.display = showSelect ? "" : "none";
            modelInput.style.display = showInput ? "" : "none";
        };

        const populateModelSelect = (models) => {
            modelSelect.innerHTML = "";

            const customOpt = document.createElement("option");
            customOpt.value = MODEL_CUSTOM;
            customOpt.textContent = "Custom…";
            modelSelect.appendChild(customOpt);

            models.slice(0, 5000).forEach((m) => {
                const opt = document.createElement("option");
                opt.value = m;
                opt.textContent = m;
                modelSelect.appendChild(opt);
            });

            modelsLoaded = true;
            const current = (modelInput.value || "").trim();
            if (current && models.includes(current)) {
                modelSelect.value = current;
            } else {
                modelSelect.value = MODEL_CUSTOM;
            }
            updateModelUiVisibility();
        };

        modelSelect.onchange = () => {
            const v = modelSelect.value;
            if (v === MODEL_CUSTOM) {
                updateModelUiVisibility();
                modelInput.focus();
                return;
            }
            modelInput.value = v;
            updateModelUiVisibility();
        };

        const refreshModelsBtn = document.createElement("button");
        refreshModelsBtn.className = "openclaw-btn openclaw-btn moltbot-btn openclaw-btn-secondary openclaw-btn-secondary moltbot-btn-secondary";
        refreshModelsBtn.textContent = "Load Models";
        refreshModelsBtn.disabled = false;
        refreshModelsBtn.title = "Fetch remote model list (admin boundary).";

        const modelsStatus = document.createElement("div");
        modelsStatus.className = "openclaw-status openclaw-status moltbot-status";
        modelsStatus.style.minWidth = "120px";

        let tokenInput; // Will be set below

        refreshModelsBtn.onclick = async () => {
            const token = (tokenInput?.value || session.getAdminToken() || "").trim();
            refreshModelsBtn.disabled = true;
            modelsStatus.textContent = "Loading...";
            modelsStatus.className = "openclaw-status openclaw-status moltbot-status";

            const res = await api.getModelList(providerSelect.value, token);
            if (!isCurrent()) return;
            if (res.ok) {
                modelDatalist.innerHTML = "";
                const models = Array.isArray(res.data?.models) ? res.data.models : [];
                lastLoadedModels = models;
                models.slice(0, 5000).forEach(m => {
                    const opt = document.createElement("option");
                    opt.value = m;
                    modelDatalist.appendChild(opt);
                });
                populateModelSelect(models);
                modelsStatus.textContent = `✓ ${models.length} models`;
                modelsStatus.className = "openclaw-status openclaw-status moltbot-status ok";
            } else {
                const detail = [
                    res.status ? `HTTP ${res.status}` : null,
                    res.error || "Failed",
                ].filter(Boolean).join(" — ");
                modelsStatus.textContent = `✗ ${detail}`;
                modelsStatus.className = "openclaw-status openclaw-status moltbot-status error";
            }
            refreshModelsBtn.disabled = false;
        };

        modelWrap.appendChild(modelSelect);
        modelWrap.appendChild(modelInput);
        modelWrap.appendChild(refreshModelsBtn);
        modelWrap.appendChild(modelsStatus);

        modelRow.appendChild(modelWrap);
        modelRow.appendChild(modelDatalist);
        llmSec.appendChild(modelRow);

        // Base URL input
        const baseUrlRow = createFormRow("Base URL", sources.base_url === "env");
        const baseUrlInput = document.createElement("input");
        baseUrlInput.type = "text";
        baseUrlInput.className = "openclaw-input openclaw-input moltbot-input";
        baseUrlInput.value = config.base_url || "";
        baseUrlInput.placeholder = "Leave empty for provider default";
        baseUrlInput.disabled = sources.base_url === "env";
        baseUrlRow.appendChild(baseUrlInput);
        llmSec.appendChild(baseUrlRow);

        // R60: Reset model list when base URL changes (cache key includes base_url).
        baseUrlInput.onchange = () => resetModelList();

        // Timeout
        const timeoutRow = createFormRow("Timeout (sec)", sources.timeout_sec === "env");
        const timeoutInput = document.createElement("input");
        timeoutInput.type = "number";
        timeoutInput.className = "openclaw-input openclaw-input moltbot-input openclaw-input-sm openclaw-input-sm moltbot-input-sm";
        timeoutInput.value = config.timeout_sec || 120;
        timeoutInput.min = 5;
        timeoutInput.max = 300;
        timeoutInput.disabled = sources.timeout_sec === "env";
        timeoutRow.appendChild(timeoutInput);
        llmSec.appendChild(timeoutRow);

        // Max Retries
        const retriesRow = createFormRow("Max Retries", sources.max_retries === "env");
        const retriesInput = document.createElement("input");
        retriesInput.type = "number";
        retriesInput.className = "openclaw-input openclaw-input moltbot-input openclaw-input-sm openclaw-input-sm moltbot-input-sm";
        retriesInput.value = config.max_retries || 3;
        retriesInput.min = 0;
        retriesInput.max = 10;
        retriesInput.disabled = sources.max_retries === "env";
        retriesRow.appendChild(retriesInput);
        llmSec.appendChild(retriesRow);

        // --- Admin Token Section ---
        const tokenRow = createFormRow(
            "Admin Token",
            false,
            createHelpButton(
                "Admin Token",
                `
                <p>The Admin Token authorizes <b>write</b> actions (save config, test LLM, store keys).</p>
                <ul>
                  <li>If <code>OPENCLAW_ADMIN_TOKEN</code> (or legacy <code>MOLTBOT_ADMIN_TOKEN</code>) is set on the server, you must enter the same token here.</li>
                  <li>If no server token is configured, admin actions are allowed on <b>localhost only</b> (convenience mode).</li>
                  <li>Never expose ComfyUI/OpenClaw to the public internet without proper access controls.</li>
                </ul>
                <p><b>PowerShell</b>: <code>$env:OPENCLAW_ADMIN_TOKEN="your-secret-token"</code></p>
                <p><b>CMD</b>: <code>set OPENCLAW_ADMIN_TOKEN=your-secret-token</code></p>
                `
            )
        );
        tokenInput = document.createElement("input");
        tokenInput.type = "password";
        tokenInput.className = "openclaw-input openclaw-input moltbot-input";
        tokenInput.placeholder = "Enter OPENCLAW_ADMIN_TOKEN if required (localhost-only if not configured)";
        tokenInput.value = "";
        tokenInput.autocomplete = "off";

        const tokenClearBtn = document.createElement("button");
        tokenClearBtn.className = "openclaw-btn openclaw-btn moltbot-btn openclaw-btn-secondary openclaw-btn-secondary moltbot-btn-secondary";
        tokenClearBtn.textContent = "Clear";
        tokenClearBtn.style.marginLeft = "4px";
        tokenClearBtn.onclick = () => {
            tokenInput.value = "";
            session.setAdminToken("");
        };

        tokenRow.appendChild(tokenInput);
        tokenRow.appendChild(tokenClearBtn);
        llmSec.appendChild(tokenRow);

        // Status message area
        const statusDiv = document.createElement("div");
        statusDiv.className = "openclaw-status openclaw-status moltbot-status";
        llmSec.appendChild(statusDiv);

        // Buttons row
        const btnRow = document.createElement("div");
        btnRow.className = "openclaw-btn-row openclaw-btn-row moltbot-btn-row";

        // Save button
        const saveBtn = document.createElement("button");
        saveBtn.className = "openclaw-btn openclaw-btn moltbot-btn";
        saveBtn.textContent = "Save";
        saveBtn.onclick = async () => {
            const token = (tokenInput.value || session.getAdminToken() || "").trim();
            if (token) session.setAdminToken(token);

            saveBtn.disabled = true;
            statusDiv.textContent = "Saving...";
            statusDiv.className = "openclaw-status openclaw-status moltbot-status";

            const updates = {
                provider: providerSelect.value,
                model: modelInput.value,
                base_url: baseUrlInput.value,
                timeout_sec: parseInt(timeoutInput.value) || 120,
                max_retries: parseInt(retriesInput.value) || 3,
            };

            // R70: Client-side schema coercion (if schema available)
            if (schema && Object.keys(schema).length > 0) {
                for (const [k, v] of Object.entries(updates)) {
                    const def = schema[k];
                    if (!def) continue;
                    if (def.type === "int" && typeof v !== "number") {
                        updates[k] = parseInt(v) || def.default;
                    }
                }
            }

            const res = await api.putConfig(updates, token);
            if (!isCurrent()) return;
            // R53: Hot-Reload Feedback
            if (res.ok) {
                const apply = res.data?.apply || {};
                let msg = "✓ Saved!";

                if (apply.restart_required?.length > 0) {
                    msg += " Restart required for: " + apply.restart_required.join(", ");
                    statusDiv.className = "openclaw-status openclaw-status moltbot-status warning"; // Yellow/Orange
                } else if (apply.applied_now?.length > 0) {
                    msg += " Applied immediately (Hot Reload).";
                    statusDiv.className = "openclaw-status openclaw-status moltbot-status ok";
                } else {
                    // No changes or unknown
                    statusDiv.className = "openclaw-status openclaw-status moltbot-status ok";
                }
                statusDiv.textContent = msg;
            } else {
                const errorMsg = getAdminErrorMessage(res.error, res.status);
                statusDiv.textContent = `✗ ${res.errors?.join(", ") || errorMsg}`;
                statusDiv.className = "openclaw-status openclaw-status moltbot-status error";
            }
            saveBtn.disabled = false;
        };
        btnRow.appendChild(saveBtn);

        // Test button
        const testBtn = document.createElement("button");
        testBtn.className = "openclaw-btn openclaw-btn moltbot-btn openclaw-btn-secondary openclaw-btn-secondary moltbot-btn-secondary";
        testBtn.textContent = "Test Connection";

        // R54: Debounced Test Action to prevent spam
        // We use a separate handler because we need to manage button state (disabled/enabled)
        // which debounce interferes with if not careful.
        // Better strategy: Disable button immediately on click, re-enable after completion.
        // Debounce is less critical here if we disable the button, but good for "auto-test on change" (future).
        // For now, implementing "Disable while testing" is the better guard than generic debounce for a button click.
        testBtn.onclick = async () => {
            if (testBtn.disabled) return;

            const token = (tokenInput.value || session.getAdminToken() || "").trim();
            if (token) session.setAdminToken(token);

            testBtn.disabled = true;
            statusDiv.textContent = "Testing...";
            statusDiv.className = "openclaw-status openclaw-status moltbot-status";

            // IMPORTANT (provider mismatch): "Test Connection" must test the provider/model currently
            // selected in the UI, even if the user hasn't clicked Save yet. Otherwise, the backend
            // falls back to the effective config (often "openai") and produces confusing errors like:
            // "API key not configured for provider 'openai'" while the UI is set to Gemini.
            try {
                const res = await api.testLLM(token, {
                    provider: providerSelect.value,
                    model: modelInput.value,
                    base_url: baseUrlInput.value,
                    timeout_sec: parseInt(timeoutInput.value) || 120,
                    max_retries: parseInt(retriesInput.value) || 3,
                });
                if (!isCurrent()) return;
                if (res.ok) {
                    statusDiv.textContent = "✓ Success! " + (res.response ? `"${res.response}"` : "");
                    statusDiv.className = "openclaw-status openclaw-status moltbot-status ok";
                } else {
                    const errorMsg = getAdminErrorMessage(res.error, res.status);
                    statusDiv.textContent = `✗ ${errorMsg}`;
                    statusDiv.className = "openclaw-status openclaw-status moltbot-status error";
                }
            } finally {
                if (isCurrent()) testBtn.disabled = false;
            }
        };
        btnRow.appendChild(testBtn);

        llmSec.appendChild(btnRow);

        // API Key instructions
        const keyNote = document.createElement("div");
        keyNote.className = "openclaw-note openclaw-note moltbot-note";
        keyNote.innerHTML = `<b>API Key</b>: Use <code>OPENCLAW_LLM_API_KEY</code> (or provider-specific keys) via environment variable (recommended), or enable the UI Key Store below (server-side storage; never stored in browser).`;
        llmSec.appendChild(keyNote);

    } else {
        const detail = [
            configRes.status ? `HTTP ${configRes.status}` : null,
            configRes.error || "Failed to load config",
        ].filter(Boolean).join(" — ");
        addRow(llmSec, "Error", detail);
    }
    scroll.appendChild(llmSec);
}
