// CRITICAL: this tab module is loaded under /extensions/<pack>/web/tabs/*.js.
// Must resolve ComfyUI core app from /scripts/app.js via ../../../ prefix.
import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { openclawApi } from "../openclaw_api.js";
import {
    findComparableWidget,
    getGraphNodeCatalog,
    getGraphWidgetCatalog,
    getGraphWidgetValueCandidates,
    resolveGraphWidget,
} from "../openclaw_graph_host.js";
import {
    PARAMETER_LAB_POLICY,
    filterParameterLabCandidates,
    validateParameterLabDimensions,
    validateParameterLabRequestBody,
    validateParameterLabScalar,
    validateParameterLabWorkflow,
} from "../openclaw_parameter_lab_policy.js";
import { createParameterLabReceiptCoordinator } from "../openclaw_parameter_lab_receipt.js";
import { openclawUI } from "../openclaw_ui.js";

/**
 * F52: Parameter Lab Tab
 * Allows users to configure and run bounded parameter sweeps.
 * F50: Includes "Compare Models" wizard.
 */
export const ParameterLabTab = {
    id: "parameter-lab",
    // IMPORTANT: TabManager expects a CSS icon class; using emoji text here hides the tab icon.
    icon: "pi pi-sliders-h",
    title: "Parameter Lab",
    tooltip: "Run experiments with parameter sweeps",

    // State
    dimensions: [],
    plan: null,
    experimentId: null,
    isRunning: false,
    results: [],
    _receiptCoordinator: null,
    _runController: null,
    _activePromptIds: null,
    _queuedRunCount: 0,
    _queueingComplete: false,

    render(container) {
        this._disposeRunRuntime();
        container.innerHTML = "";
        container.className = "openclaw-tab-content openclaw-tab-content moltbot-tab-content openclaw-lab-container openclaw-lab-container moltbot-lab-container";

        // 1. Header / Toolbar
        const header = document.createElement("div");
        header.className = "openclaw-lab-header openclaw-lab-header moltbot-lab-header";
        header.innerHTML = `
            <div class="openclaw-lab-title-wrap openclaw-lab-title-wrap moltbot-lab-title-wrap">
                <h3>Parameter Lab</h3>
                <p>Build bounded sweeps and compare model variants directly from canvas.</p>
            </div>
            <div class="openclaw-lab-actions openclaw-lab-actions moltbot-lab-actions">
                <button id="lab-history" class="openclaw-btn openclaw-btn moltbot-btn has-icon openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn" title="View History">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">\uD83D\uDCDC</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">History</span>
                </button>
                <div class="openclaw-separator openclaw-separator moltbot-separator"></div>
                <button id="lab-compare-models" class="openclaw-btn openclaw-btn moltbot-btn has-icon openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn" title="Wizard: Compare Models">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">\u2696\uFE0F</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">Compare Models</span>
                </button>
                <div class="openclaw-separator openclaw-separator moltbot-separator"></div>
                <button id="lab-add-dim" class="openclaw-btn openclaw-btn moltbot-btn openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">&#x2795;</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">+ Dimension</span>
                </button>
                <button id="lab-generate" class="openclaw-btn openclaw-btn moltbot-btn openclaw-lab-action-btn openclaw-lab-action-btn moltbot-lab-action-btn">
                    <span class="openclaw-lab-action-icon openclaw-lab-action-icon moltbot-lab-action-icon">&#x1F9ED;</span>
                    <span class="openclaw-lab-action-label openclaw-lab-action-label moltbot-lab-action-label">Generate Plan</span>
                </button>
            </div>
        `;
        container.appendChild(header);
        this.container = container;

        const main = document.createElement("div");
        main.className = "openclaw-lab-main openclaw-lab-main moltbot-lab-main";
        container.appendChild(main);

        // 2. Configuration Area (Dimensions)
        const configCard = document.createElement("section");
        configCard.className = "openclaw-lab-card openclaw-lab-card moltbot-lab-card";
        configCard.innerHTML = `
            <div class="openclaw-lab-card-head openclaw-lab-card-head moltbot-lab-card-head">
                <h4>Dimensions</h4>
                <span class="openclaw-lab-meta openclaw-lab-meta moltbot-lab-meta" id="lab-dimension-count">0 configured</span>
            </div>
        `;
        const configArea = document.createElement("div");
        configArea.className = "openclaw-lab-config openclaw-lab-config moltbot-lab-config";
        configCard.appendChild(configArea);
        main.appendChild(configCard);
        this.configContainer = configArea;
        this.dimensionCountEl = configCard.querySelector("#lab-dimension-count");

        // 3. Plan / Results Area
        const resultsCard = document.createElement("section");
        resultsCard.className = "openclaw-lab-card openclaw-lab-card moltbot-lab-card";
        resultsCard.innerHTML = `
            <div class="openclaw-lab-card-head openclaw-lab-card-head moltbot-lab-card-head">
                <h4>Plan & Results</h4>
                <span class="openclaw-lab-meta openclaw-lab-meta moltbot-lab-meta">Live status</span>
            </div>
        `;
        const resultsArea = document.createElement("div");
        resultsArea.className = "openclaw-lab-results openclaw-lab-results moltbot-lab-results";
        resultsCard.appendChild(resultsArea);
        main.appendChild(resultsCard);
        this.resultsContainer = resultsArea;

        // Bind Events
        container.querySelector("#lab-add-dim").onclick = () => {
            this.setActiveToolbarButton("lab-add-dim");
            this.addDimensionUI();
        };
        container.querySelector("#lab-generate").onclick = () => {
            this.setActiveToolbarButton("lab-generate");
            this.generatePlan();
        };
        container.querySelector("#lab-compare-models").onclick = () => {
            this.setActiveToolbarButton("lab-compare-models");
            this.showCompareWizard();
        };
        container.querySelector("#lab-history").onclick = () => {
            this.setActiveToolbarButton("lab-history");
            this.showHistory();
        };

        // Start without forced selection state.
        this.setActiveToolbarButton(null);

        // Initial Render
        this.renderDimensions();

        // F50: Listen for Compare Request (once)
        if (!this._listeningForCompare) {
            const onCompare = (e) => {
                const node = e.detail.node;
                if (node) this.showCompareWizard(node);
            };
            window.addEventListener("openclaw:lab:compare", onCompare);
            // Legacy event name for compatibility.
            window.addEventListener("moltbot:lab:compare", onCompare);
            this._listeningForCompare = true;
        }
    },

    dispose() {
        const hadRuntime = Boolean(
            this._receiptCoordinator || this._runController || this.es
        );
        this._disposeRunRuntime();
        return hadRuntime;
    },

    _disposeRunRuntime() {
        this._runController?.abort();
        this._runController = null;
        this._receiptCoordinator?.dispose();
        this._receiptCoordinator = null;
        this._activePromptIds = null;
        this._queuedRunCount = 0;
        this._queueingComplete = false;
        this.es?.close?.();
        this.es = null;
        this.isRunning = false;
    },

    async showHistory() {
        this.resultsContainer.innerHTML = "<div class='openclaw-loading openclaw-loading moltbot-loading'>Loading history...</div>";
        try {
            const res = await openclawApi.fetch(openclawApi._path("/lab/experiments"));
            if (res.ok && res.data) {
                this.renderHistoryList(res.data.experiments);
            } else {
                this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Failed to load history.</div>";
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Error: " + e.message + "</div>";
        }
    },

    setActiveToolbarButton(buttonId) {
        if (!this.container) return;
        this.container.querySelectorAll(".openclaw-lab-action-btn").forEach((btn) => {
            btn.classList.toggle("active", buttonId ? btn.id === buttonId : false);
        });
    },

    renderHistoryList(experiments) {
        this.resultsContainer.innerHTML = "";
        const header = document.createElement("div");
        header.className = "openclaw-lab-plan-header openclaw-lab-plan-header moltbot-lab-plan-header";
        header.innerHTML = `<h4>Experiment History</h4><span>${experiments.length} Records</span>`;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "openclaw-lab-run-list openclaw-lab-run-list moltbot-lab-run-list";

        if (experiments.length === 0) {
            list.innerHTML = "<div class='openclaw-hint openclaw-hint moltbot-hint'>No history found. Run a sweep or compare to see results here.</div>";
        }

        experiments.forEach(exp => {
            const item = document.createElement("div");
            item.className = "openclaw-lab-run-item openclaw-lab-run-item moltbot-lab-run-item";
            const dateStr = new Date(exp.created_at * 1000).toLocaleString();
            item.innerHTML = `
                <span class="run-idx">${exp.id.slice(0, 8)}</span>
                <span class="run-params">${dateStr}</span>
                <span class="run-status">${exp.completed_count}/${exp.run_count} runs</span>
                <button class="openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon load-exp" title="Load Details">\u2192</button>
             `;
            item.querySelector(".load-exp").onclick = () => this.loadExperiment(exp.id);
            list.appendChild(item);
        });
        this.resultsContainer.appendChild(list);
    },

    async loadExperiment(expId) {
        this.resultsContainer.innerHTML = "<div class='openclaw-loading openclaw-loading moltbot-loading'>Loading details...</div>";
        try {
            const res = await openclawApi.fetch(openclawApi._path(`/lab/experiments/${expId}`));
            if (res.ok && res.data) {
                this.plan = res.data.experiment;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='openclaw-error openclaw-error moltbot-error'>Failed to load experiment.</div>";
        }
    },

    // --- Dynamic Data Helpers ---

    _coerceSelectedNodeId(nodeId) {
        if (nodeId === null || nodeId === undefined || nodeId === "") {
            return null;
        }
        const raw = String(nodeId);
        return /^\d+$/.test(raw) ? parseInt(raw, 10) : raw;
    },

    getNodeCatalog() {
        return getGraphNodeCatalog(app.graph).map((entry) => ({
            id: entry.id,
            title: entry.displayTitle,
            type: entry.type,
        }));
    },

    getWidgetCatalog(nodeId) {
        return getGraphWidgetCatalog(app.graph, nodeId).map((widget) => ({
            name: widget.name,
            type: widget.type,
            value: widget.value,
            options: widget.options,
        }));
    },

    getValueCandidates(nodeId, widgetName) {
        return getGraphWidgetValueCandidates(app.graph, nodeId, widgetName);
    },

    addDimensionUI(defaults = null) {
        if (this.dimensions.length >= PARAMETER_LAB_POLICY.maxSweepDimensions) {
            openclawUI.showBanner("error", "Parameter Lab validation failed: too_many_dimensions");
            return false;
        }
        // Add a default blank dimension or use defaults
        // Allow migration from legacy values_str if needed
        const newDim = defaults || {
            node_id: null,
            widget_name: "",
            values: [], // Primary state
            values_str: "", // Legacy/Fallback
            strategy: "grid"
        };

        // Migration: if values_str exists but values is empty, parse it?
        // Done lazily at render time or generation time.
        // Better to canonicalize here if defaults provided.
        if (defaults && defaults.values_str && (!defaults.values || defaults.values.length === 0)) {
            newDim.values = defaults.values_str.split(",").map(s => s.trim()).filter(Boolean);
        }

        this.dimensions.push(newDim);
        this.renderDimensions();
        return true;
    },

    removeDimension(index) {
        this.dimensions.splice(index, 1);
        this.renderDimensions();
    },

    renderDimensions() {
        this.configContainer.innerHTML = "";
        if (this.dimensionCountEl) {
            this.dimensionCountEl.textContent = `${this.dimensions.length} configured`;
        }

        // "Refresh" button (lightweight, just re-renders to pick up graph changes)
        const toolbar = document.createElement("div");
        toolbar.className = "openclaw-lab-config-toolbar openclaw-lab-config-toolbar moltbot-lab-config-toolbar";
        const refreshBtn = document.createElement("button");
        refreshBtn.className = "openclaw-btn-text openclaw-btn-text moltbot-btn-text";
        refreshBtn.id = "lab-refresh-graph";
        refreshBtn.title = "Refresh from Canvas";
        refreshBtn.textContent = "\u21BB Refresh Options";
        refreshBtn.onclick = () => this.renderDimensions();
        toolbar.appendChild(refreshBtn);
        this.configContainer.appendChild(toolbar);

        if (this.dimensions.length === 0) {
            const hint = document.createElement("div");
            hint.className = "openclaw-hint openclaw-hint moltbot-hint";
            hint.textContent = "No dimensions configured. Add one or use 'Compare Models'.";
            this.configContainer.appendChild(hint);
            return;
        }

        const nodeCatalog = this.getNodeCatalog();

        this.dimensions.forEach((dim, idx) => {
            // Migration: Check before rendering
            if ((!dim.values || dim.values.length === 0) && dim.values_str) {
                const migrated = dim.values_str.split(",").map(s => s.trim()).filter(Boolean);
                if (migrated.length > 0) dim.values = migrated;
            }

            const row = document.createElement("div");
            row.className = "openclaw-lab-dim-row openclaw-lab-dim-row moltbot-lab-dim-row dynamic";

            // 1. Node Selector
            const nodeGroup = document.createElement("div");
            nodeGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group narrow";
            nodeGroup.innerHTML = `<label>Node</label>`;
            const nodeSelect = document.createElement("select");
            nodeSelect.className = "dim-node-select";

            const defaultOpt = document.createElement("option");
            defaultOpt.value = "";
            defaultOpt.textContent = "Select Node...";
            nodeSelect.appendChild(defaultOpt);

            nodeCatalog.forEach(n => {
                const opt = document.createElement("option");
                opt.value = String(n.id);
                opt.textContent = `[${n.id}] ${n.title}`;
                if (String(dim.node_id) === String(n.id)) opt.selected = true;
                nodeSelect.appendChild(opt);
            });

            nodeSelect.onchange = (e) => {
                const newVal = this._coerceSelectedNodeId(e.target.value);
                if (newVal !== null) {
                    dim.node_id = newVal;
                    dim.widget_name = ""; // Reset widget on node change
                    dim.values = [];      // Reset values
                    this.renderDimensions();
                }
            };
            nodeGroup.appendChild(nodeSelect);
            row.appendChild(nodeGroup);

            // 2. Widget Selector (Dependent)
            const widgetGroup = document.createElement("div");
            widgetGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group narrow";
            widgetGroup.innerHTML = `<label>Widget</label>`;
            const widgetSelect = document.createElement("select");
            widgetSelect.className = "dim-widget-select";

            if (dim.node_id) {
                const widgets = this.getWidgetCatalog(dim.node_id);
                const wDefaultOpt = document.createElement("option");
                wDefaultOpt.value = "";
                wDefaultOpt.textContent = "Select Widget...";
                widgetSelect.appendChild(wDefaultOpt);

                widgets.forEach(w => {
                    const opt = document.createElement("option");
                    opt.value = w.name;
                    opt.textContent = `${w.name} (${w.type})`;
                    if (dim.widget_name === w.name) opt.selected = true;
                    widgetSelect.appendChild(opt);
                });
            } else {
                const disabledOpt = document.createElement("option");
                disabledOpt.value = "";
                disabledOpt.textContent = "Select Node first";
                disabledOpt.disabled = true;
                disabledOpt.selected = true;
                widgetSelect.appendChild(disabledOpt);
                widgetSelect.disabled = true;
            }

            widgetSelect.onchange = (e) => {
                dim.widget_name = e.target.value;
                dim.values = []; // Reset val on widget change
                this.renderDimensions();
            };
            widgetGroup.appendChild(widgetSelect);
            row.appendChild(widgetGroup);

            // 3. Value Management (Candidates + Chips)
            const valueGroup = document.createElement("div");
            valueGroup.className = "openclaw-form-group openclaw-form-group moltbot-form-group wide dynamic-values";
            valueGroup.innerHTML = `<label>Values</label>`;

            const valueControls = document.createElement("div");
            valueControls.className = "dim-value-controls";

            // Candidate Dropdown
            const candidateSelect = document.createElement("select");
            candidateSelect.className = "dim-candidate-select";
            let candidates = [];
            if (dim.node_id && dim.widget_name) {
                candidates = this.getValueCandidates(dim.node_id, dim.widget_name);
                const cDefaultOpt = document.createElement("option");
                cDefaultOpt.value = "";
                cDefaultOpt.textContent = "Add option...";
                candidateSelect.appendChild(cDefaultOpt);

                candidates.forEach(c => {
                    const opt = document.createElement("option");
                    // CRITICAL: keep DOM-construction + textContent; do not switch back to dynamic innerHTML interpolation.
                    // Use stringified value for option value to ensure it works in HTML
                    opt.value = String(c);
                    opt.textContent = String(c);
                    candidateSelect.appendChild(opt);
                });

                candidateSelect.onchange = (e) => {
                    if (e.target.value) {
                        // Attempt to preserve type from candidate list?
                        // Candidates are mixed types. The value in option is stringified.
                        // Fix: match original candidate by string comparison
                        const match = candidates.find(c => String(c) === e.target.value);
                        const valToAdd = match !== undefined ? match : e.target.value;

                        if (!dim.values) dim.values = [];
                        if (!dim.values.includes(valToAdd)) {
                            dim.values.push(valToAdd);
                            this.renderDimensions();
                        }
                        e.target.value = ""; // Reset
                    }
                };
            } else {
                candidateSelect.disabled = true;
                candidateSelect.innerHTML = `<option>...</option>`;
            }
            valueControls.appendChild(candidateSelect);

            // Manual Input (for floats, non-enums)
            const manualInput = document.createElement("input");
            manualInput.type = "text";
            manualInput.className = "dim-manual-input";
            manualInput.placeholder = "Custom val";
            manualInput.onkeydown = (e) => {
                if (e.key === "Enter") {
                    const val = manualInput.value.trim();
                    if (val) {
                        // Try parse number/bool
                        let typedVal = val;
                        if (val === "true") typedVal = true;
                        else if (val === "false") typedVal = false;
                        else if (!isNaN(parseFloat(val)) && isFinite(val) && !val.match(/[a-zA-Z]/)) typedVal = parseFloat(val);

                        if (!dim.values) dim.values = [];
                        const scalarValidation = validateParameterLabScalar(typedVal);
                        if (!scalarValidation.ok) {
                            openclawUI.showBanner(
                                "error",
                                `Parameter Lab validation failed: ${scalarValidation.reason}`
                            );
                            return;
                        }
                        if (dim.values.length >= PARAMETER_LAB_POLICY.maxValuesPerDimension) {
                            openclawUI.showBanner(
                                "error",
                                "Parameter Lab validation failed: too_many_values"
                            );
                            return;
                        }
                        if (dim.values.some((existing) => String(existing) === String(typedVal))) {
                            openclawUI.showBanner(
                                "error",
                                "Parameter Lab validation failed: duplicate_ambiguous_value"
                            );
                            return;
                        }
                        dim.values.push(typedVal);
                        this.renderDimensions();
                    }
                }
            };
            valueControls.appendChild(manualInput);

            valueGroup.appendChild(valueControls);

            // Chips Container
            const chips = document.createElement("div");
            chips.className = "dim-value-chips";
            (dim.values || []).forEach((v, vIdx) => {
                const chip = document.createElement("span");
                chip.className = "openclaw-chip openclaw-chip moltbot-chip";
                // IMPORTANT: render value via textContent to avoid UI injection/markup breakage from workflow-provided strings.
                chip.textContent = String(v) + " ";

                const rmBtn = document.createElement("span");
                rmBtn.className = "chip-rm";
                rmBtn.dataset.idx = vIdx;
                rmBtn.textContent = "x";
                rmBtn.onclick = (e) => {
                    dim.values.splice(vIdx, 1);
                    this.renderDimensions();
                };
                chip.appendChild(rmBtn);
                chips.appendChild(chip);
            });
            valueGroup.appendChild(chips);

            // Legacy fallback removed (handled at start of loop)

            row.appendChild(valueGroup);

            // Remove Button
            const rmBtn = document.createElement("button");
            rmBtn.className = "openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon remove-dim";
            rmBtn.textContent = "x";
            rmBtn.title = "Remove Dimension";
            rmBtn.onclick = () => this.removeDimension(idx);
            row.appendChild(rmBtn);

            this.configContainer.appendChild(row);
        });
    },

    // F50: Compare Models Wizard
    showCompareWizard(targetNode = null) {
        // 1. Scan for loader nodes if no target provided
        let target = targetNode ? findComparableWidget(app.graph, targetNode) : null;
        if (!target) {
            const compareTargets = getGraphNodeCatalog(app.graph)
                .filter((entry) =>
                    entry.node?.type === "CheckpointLoaderSimple" ||
                    entry.node?.type === "LORALoader" ||
                    entry.node?.type === "UNETLoader"
                )
                .map((entry) => findComparableWidget(app.graph, entry.node))
                .filter(Boolean);
            if (compareTargets.length === 0) {
                openclawUI.showBanner("warning", "No Checkpoint/LoRA loaders found in workflow.");
                return;
            }
            [target] = compareTargets;
        }

        if (!target?.widget) {
            openclawUI.showBanner("error", `Could not find model widget on node ${targetNode?.id ?? "unknown"}`);
            return;
        }

        // Reset dimensions
        if (this.dimensions.length > 0) {
            if (!confirm("This will clear current dimensions. Continue?")) return;
        }
        this.dimensions = [];

        // Add dimension pre-filled
        const options = filterParameterLabCandidates(target.widget.options?.values || []);
        let initialValues = [];
        if (options.length > 0) {
            // Pick top 2 as example
            initialValues = options.slice(0, 2);
        }

        this.addDimensionUI({
            node_id: this._coerceSelectedNodeId(target.nodeId),
            widget_name: target.widgetName,
            values: initialValues,
            values_str: initialValues.join(", "), // Legacy fallback
            strategy: "compare"
        });

        openclawUI.showBanner(
            "info",
            `Setup comparison for Node ${target.nodeId} (${target.nodeEntry.title}). Edit values to select models.`
        );
    },

    async generatePlan() {
        const params = this.dimensions.map(d => {
            return {
                node_id: d.node_id,
                widget_name: d.widget_name,
                values: d.values,
                strategy: d.strategy || "grid"
            };
        });
        const dimensionValidation = validateParameterLabDimensions(params);
        if (!dimensionValidation.ok) {
            openclawUI.showBanner(
                "error",
                `Parameter Lab validation failed: ${dimensionValidation.reason}`
            );
            return;
        }

        const hasCompare = params.some(p => p.strategy === "compare");
        if (hasCompare && params.length !== 1) {
            openclawUI.showBanner(
                "error",
                "Compare mode supports exactly one comparison dimension."
            );
            return;
        }
        if (hasCompare && params[0].values.length > PARAMETER_LAB_POLICY.maxCompareItems) {
            openclawUI.showBanner(
                "error",
                "Parameter Lab validation failed: too_many_values"
            );
            return;
        }

        try {
            const graphJson = JSON.stringify(app.graph.serialize());
            const workflowValidation = validateParameterLabWorkflow(graphJson);
            if (!workflowValidation.ok) {
                openclawUI.showBanner(
                    "error",
                    `Parameter Lab validation failed: ${workflowValidation.reason}`
                );
                return;
            }

            let path;
            let payload;
            if (hasCompare) {
                const compare = params[0];
                openclawUI.showBanner("info", "Generating compare plan...");
                path = "/lab/compare";
                payload = {
                    workflow_json: graphJson,
                    items: compare.values,
                    node_id: compare.node_id,
                    widget_name: compare.widget_name
                };
            } else {
                openclawUI.showBanner("info", "Generating sweep plan...");
                path = "/lab/sweep";
                payload = {
                    workflow_json: graphJson,
                    params: params
                };
            }
            const requestValidation = validateParameterLabRequestBody(payload);
            if (!requestValidation.ok) {
                openclawUI.showBanner(
                    "error",
                    `Parameter Lab validation failed: ${requestValidation.reason}`
                );
                return;
            }
            const res = await openclawApi.fetch(openclawApi._path(path), {
                method: "POST",
                body: JSON.stringify(payload)
            });

            if (res.ok && res.data) {
                this.plan = res.data.plan;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
                openclawUI.showBanner("success", `Plan generated: ${this.plan.runs.length} runs.`);
            } else {
                openclawUI.showBanner("error", "Failed to generate plan: " + (res.error || "Unknown"));
            }
        } catch {
            openclawUI.showBanner(
                "error",
                "Parameter Lab validation failed: invalid_payload"
            );
        }
    },

    renderPlan() {
        this.resultsContainer.innerHTML = "";
        if (!this.plan) return;

        const header = document.createElement("div");
        header.className = "openclaw-lab-plan-header openclaw-lab-plan-header moltbot-lab-plan-header";
        header.innerHTML = `
            <h4>Experiment: ${this.experimentId.slice(0, 8)}</h4>
            <span>${this.plan.runs.length} Runs</span>
            <button id="lab-run-all" class="openclaw-btn openclaw-btn moltbot-btn primary">Run Experiment</button>
        `;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "openclaw-lab-run-list openclaw-lab-run-list moltbot-lab-run-list";

        this.plan.runs.forEach((run, idx) => {
            const item = document.createElement("div");
            item.className = "openclaw-lab-run-item openclaw-lab-run-item moltbot-lab-run-item";
            item.innerHTML = `
                <span class="run-idx">#${idx + 1}</span>
                <span class="run-params">${JSON.stringify(run).slice(0, 50)}...</span>
                <span class="run-status ${run.status || 'pending'}">${run.status || 'Pending'}</span>
                <button class="openclaw-btn-icon openclaw-btn-icon moltbot-btn-icon replay-run" title="Replay (Apply Values)">\u21A9\uFE0F</button>
            `;
            item.dataset.idx = idx;
            item.querySelector(".replay-run").onclick = (e) => {
                e.stopPropagation();
                this.replayRun(run);
            };
            list.appendChild(item);
        });

        this.resultsContainer.appendChild(list);

        // F50: Side-by-Side Comparison Layout
        if (this.plan.dimensions.some(d => d.strategy === "compare")) {
            this.resultsContainer.classList.add("openclaw-lab-compare-mode", "moltbot-lab-compare-mode");
        } else {
            this.resultsContainer.classList.remove("openclaw-lab-compare-mode", "moltbot-lab-compare-mode");
        }

        this.resultsContainer.querySelector("#lab-run-all").onclick = () => this.runExperiment();
    },

    async runExperiment() {
        if (this.isRunning) return;
        this._disposeRunRuntime();
        this.isRunning = true;
        openclawUI.showBanner("info", "Starting experiment...");

        const items = this.resultsContainer.querySelectorAll(".openclaw-lab-run-item");
        this._runController = new AbortController();
        this._activePromptIds = new Set();
        this._queuedRunCount = 0;
        this._queueingComplete = false;
        try {
            this._receiptCoordinator = createParameterLabReceiptCoordinator({
                app,
                api,
            });
        } catch (_error) {
            this.isRunning = false;
            openclawUI.showBanner(
                "error",
                "Parameter Lab queue receipt is unavailable."
            );
            return;
        }
        const signal = this._runController.signal;

        try {
            for (let i = 0; i < this.plan.runs.length; i++) {
                if (signal.aborted) break;

                const run = this.plan.runs[i];
                const item = items[i];
                const statusSpan = item.querySelector(".run-status");

                statusSpan.className = "run-status running";
                statusSpan.textContent = "Queuing...";

                try {
                    // 1. Apply overrides
                    const receiptWidget = this.applyOverrides(run);
                    if (!receiptWidget) {
                        throw new Error("receipt_widget_required");
                    }

                    // 2. Queue through the host-owned path and bind its exact receipt.
                    const receipt = await this._receiptCoordinator.queue({
                        experimentId: this.experimentId,
                        runId: String(i),
                        widget: receiptWidget,
                        signal,
                    });
                    if (signal.aborted) {
                        receipt.release();
                        break;
                    }

                    run.prompt_id = receipt.promptId;
                    this._activePromptIds.add(receipt.promptId);
                    this._queuedRunCount += 1;
                    statusSpan.textContent =
                        "Queued (" + receipt.promptId.slice(0, 4) + ")";
                    await this._updateRun(i, {
                        status: "queued",
                        output: { prompt_id: receipt.promptId },
                    });

                    receipt.subscribeLifecycle((event) => {
                        if (signal.aborted) return;
                        this._handleRunLifecycle({
                            event,
                            runIndex: i,
                            statusSpan,
                        });
                    });
                } catch (error) {
                    if (signal.aborted) break;
                    statusSpan.className = "run-status error";
                    statusSpan.textContent = "Queue Failed";
                    await this._updateRun(i, { status: "failed" });
                    console.error("[OpenClaw] Parameter Lab queue failed", {
                        code: error?.code || "queue_failed",
                    });
                }

                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        } finally {
            this._queueingComplete = true;
            if (!signal.aborted) {
                if (this._queuedRunCount > 0 && this._activePromptIds.size > 0) {
                    openclawUI.showBanner(
                        "success",
                        "All runs queued. Monitoring progress..."
                    );
                } else if (this._queuedRunCount > 0) {
                    this.isRunning = false;
                    openclawUI.showBanner(
                        "success",
                        "All experiment runs finished."
                    );
                } else {
                    this.isRunning = false;
                    openclawUI.showBanner(
                        "error",
                        "No experiment runs were queued."
                    );
                }
            }
        }
    },

    async _updateRun(runIndex, payload) {
        try {
            await openclawApi.fetch(
                openclawApi._path(
                    `/lab/experiments/${this.experimentId}/runs/${runIndex}`
                ),
                {
                    method: "POST",
                    body: JSON.stringify(payload),
                }
            );
        } catch (_error) {
            console.warn("[OpenClaw] Parameter Lab run update failed");
        }
    },

    _handleRunLifecycle({ event, runIndex, statusSpan }) {
        if (!this._activePromptIds?.has(event.promptId)) return;
        if (event.type === "execution_start") {
            statusSpan.className = "run-status running";
            statusSpan.textContent = "Running";
            void this._updateRun(runIndex, { status: "running" });
            return;
        }

        const succeeded = event.type === "execution_success";
        statusSpan.className = succeeded ? "run-status success" : "run-status error";
        statusSpan.textContent = succeeded ? "Completed" : "Failed";
        void this._updateRun(runIndex, {
            status: succeeded ? "completed" : "failed",
        });
        this._activePromptIds.delete(event.promptId);
        if (this._queueingComplete && this._activePromptIds.size === 0) {
            this.isRunning = false;
            openclawUI.showBanner(
                "success",
                "All experiment runs finished."
            );
        }
    },

    replayRun(run) {
        if (confirm("Apply these parameter values to the current workflow?")) {
            try {
                this.applyOverrides(run);
                openclawUI.showBanner("success", "Values applied to nodes.");
            } catch {
                openclawUI.showBanner(
                    "error",
                    "Cannot apply values: a target node or widget is missing, invalid or ambiguous. Rebuild the plan from the current workflow."
                );
            }
        }
    },

    applyOverrides(run) {
        const unavailable = () => Object.assign(new Error("override_target_unavailable"), { code: "override_target_unavailable" });
        if (!run || typeof run !== "object" || Array.isArray(run)) {
            throw unavailable();
        }
        // IMPORTANT: resolve the entire run before writes. Per-key writes or a
        // getNodeById fallback can partially apply a stale or ambiguous plan.
        const updates = Object.entries(run).filter(([key]) => key !== "prompt_id" && key !== "status").map(([key, value]) => {
            const separatorIndex = key.indexOf(".");
            if (separatorIndex <= 0) throw unavailable();
            const nodeId = key.slice(0, separatorIndex);
            const widgetName = key.slice(separatorIndex + 1);
            if (!validateParameterLabDimensions([{ node_id: nodeId, widget_name: widgetName, values: [value] }]).ok) {
                throw unavailable();
            }
            const resolved = resolveGraphWidget(app.graph, nodeId, widgetName);
            if (!resolved?.widget) throw unavailable();
            return { widget: resolved.widget, value };
        });
        if (updates.length === 0) throw unavailable();
        updates.forEach(({ widget, value }) => { widget.value = value; });
        return updates[0].widget;
    }
};
