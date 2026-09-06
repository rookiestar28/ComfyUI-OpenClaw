import { openclawApi } from "../openclaw_api.js";
import { clearError, showError, showToast } from "../openclaw_utils.js";
import { escapeHtml } from "../openclaw_text_safety.js";

const _ACTIVE_STATES = new Set(["queued", "running"]);

function normalizeString(value) {
    return String(value ?? "").trim();
}

function parseTagList(raw) {
    const tags = [];
    for (const token of String(raw ?? "").split(",")) {
        const clean = token.trim().toLowerCase();
        if (!clean || tags.includes(clean)) continue;
        tags.push(clean);
        if (tags.length >= 24) break;
    }
    return tags;
}

function hasRequiredDownloadContract(item) {
    const downloadUrl = normalizeString(item?.download_url);
    const sha = normalizeString(item?.sha256).toLowerCase();
    const provenance = item?.provenance || {};
    const hasSha = sha.length === 64 && /^[0-9a-f]+$/.test(sha);
    const hasProvenance = Boolean(
        normalizeString(provenance.publisher) &&
        normalizeString(provenance.license) &&
        normalizeString(provenance.source_url)
    );
    return Boolean(downloadUrl && hasSha && hasProvenance);
}

function asProgressPercent(task) {
    const ratio = Number(task?.progress ?? 0);
    if (!Number.isFinite(ratio) || ratio < 0) return 0;
    if (ratio > 1) return 100;
    return Math.round(ratio * 100);
}

function buildSearchItemCard(item, index) {
    const installed = Boolean(item?.installed);
    const queueReady = !installed && hasRequiredDownloadContract(item);
    const tags = Array.isArray(item?.tags) ? item.tags.join(", ") : "";
    const statusHtml = installed
        ? '<span class="openclaw-chip" style="background:#24421f;border:1px solid #35662d;color:#98d08d;">Installed</span>'
        : '<span class="openclaw-chip" style="background:#2d2d2d;border:1px solid #555;color:#ddd;">Catalog</span>';
    const missingHtml = queueReady
        ? ""
        : '<div style="margin-top:6px;font-size:12px;color:#d4a35e;">Missing download contract fields (url/sha256/provenance).</div>';
    const queueBtn = installed
        ? ""
        : `<button class="openclaw-btn openclaw-btn openclaw-btn-sm" data-action="queue" data-index="${index}" ${queueReady ? "" : "disabled"}>Queue Download</button>`;
    return `
        <div class="openclaw-card openclaw-card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                <div style="min-width:0;">
                    <div style="font-weight:600;word-break:break-word;">${escapeHtml(item?.name || "(unnamed)")}</div>
                    <div style="font-size:12px;color:var(--moltbot-color-fg-muted);margin-top:2px;">
                        id=${escapeHtml(item?.id || "")} | type=${escapeHtml(item?.model_type || "checkpoint")} | source=${escapeHtml(item?.source_label || item?.source || "unknown")}
                    </div>
                    <div style="font-size:12px;color:#999;margin-top:2px;word-break:break-all;">
                        ${escapeHtml(item?.download_url || "")}
                    </div>
                    ${tags ? `<div style="font-size:12px;color:#999;margin-top:2px;">tags: ${escapeHtml(tags)}</div>` : ""}
                    ${missingHtml}
                </div>
                <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;">
                    ${statusHtml}
                    ${queueBtn}
                </div>
            </div>
        </div>
    `;
}

function buildTaskCard(task) {
    const state = normalizeString(task?.state || "unknown");
    const pct = asProgressPercent(task);
    const bytesDownloaded = Number(task?.bytes_downloaded ?? 0);
    const totalBytes = Number(task?.total_bytes ?? 0);
    const detail = totalBytes > 0 ? `${bytesDownloaded}/${totalBytes}` : `${bytesDownloaded}`;
    let actionHtml = "";
    if (_ACTIVE_STATES.has(state)) {
        actionHtml = `<button class="openclaw-btn openclaw-btn openclaw-btn-sm openclaw-btn-danger openclaw-btn-danger" data-action="cancel-task" data-task-id="${escapeHtml(task?.task_id || "")}">Cancel</button>`;
    } else if (state === "completed" && !task?.imported) {
        actionHtml = `<button class="openclaw-btn openclaw-btn openclaw-btn-sm openclaw-btn-primary openclaw-btn-primary" data-action="import-task" data-task-id="${escapeHtml(task?.task_id || "")}">Import</button>`;
    }
    return `
        <div class="openclaw-card openclaw-card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                <div style="min-width:0;">
                    <div style="font-weight:600;word-break:break-word;">${escapeHtml(task?.name || task?.model_id || task?.task_id || "download-task")}</div>
                    <div style="font-size:12px;color:var(--moltbot-color-fg-muted);margin-top:2px;">
                        task=${escapeHtml(task?.task_id || "")} | state=${escapeHtml(state)} | progress=${pct}%
                    </div>
                    <div style="font-size:12px;color:#999;margin-top:2px;">
                        bytes=${escapeHtml(detail)}${task?.imported ? " | imported" : ""}
                    </div>
                    ${task?.error ? `<div style="font-size:12px;color:#de7e7e;margin-top:2px;">error=${escapeHtml(task.error)}</div>` : ""}
                </div>
                <div>${actionHtml}</div>
            </div>
        </div>
    `;
}

function buildInstallationCard(item) {
    return `
        <div class="openclaw-card openclaw-card" style="margin-bottom:8px;">
            <div style="font-weight:600;word-break:break-word;">${escapeHtml(item?.name || item?.model_id || "(unnamed)")}</div>
            <div style="font-size:12px;color:var(--moltbot-color-fg-muted);margin-top:2px;">
                model_id=${escapeHtml(item?.model_id || "")} | type=${escapeHtml(item?.model_type || "checkpoint")}
            </div>
            <div style="font-size:12px;color:#999;margin-top:2px;word-break:break-all;">
                path=${escapeHtml(item?.installation_path || "")}
            </div>
        </div>
    `;
}

function sortTasksForDisplay(tasks) {
    return [...tasks].sort((left, right) => Number(right?.created_at || 0) - Number(left?.created_at || 0));
}

export function mergeTaskDelta(existingTasks, deltaTasks) {
    const merged = new Map();
    for (const task of Array.isArray(existingTasks) ? existingTasks : []) {
        if (task?.task_id) {
            merged.set(task.task_id, task);
        }
    }
    for (const task of Array.isArray(deltaTasks) ? deltaTasks : []) {
        if (task?.task_id) {
            merged.set(task.task_id, task);
        }
    }
    return sortTasksForDisplay([...merged.values()]);
}

function resolveNextTaskCursor(payload, fallbackCursor = 0) {
    const delta = payload?.delta || {};
    const nextSinceSeq = Number(delta?.next_since_seq);
    if (Number.isFinite(nextSinceSeq) && nextSinceSeq >= 0) {
        return nextSinceSeq;
    }
    const taskSeqs = Array.isArray(payload?.tasks)
        ? payload.tasks.map((task) => Number(task?.change_seq)).filter((seq) => Number.isFinite(seq) && seq >= 0)
        : [];
    if (!taskSeqs.length) {
        return fallbackCursor;
    }
    return Math.max(fallbackCursor, ...taskSeqs);
}

export const ModelManagerTab = {
    id: "model-manager",
    title: "Model Manager",
    icon: "pi pi-download",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel openclaw-panel">
                <div class="openclaw-card openclaw-card" style="border-radius:0;border:none;border-bottom:1px solid var(--moltbot-color-border);">
                    <div class="openclaw-section-header openclaw-section-header">Model Manager</div>
                    <div class="openclaw-error-box openclaw-error-box" style="display:none"></div>
                    <div class="openclaw-toolbar openclaw-toolbar" style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:6px;align-items:center;margin-top:6px;">
                        <input id="mm-query" class="openclaw-input openclaw-input" placeholder="Search id/name/tags..." />
                        <input id="mm-source" class="openclaw-input openclaw-input" placeholder="source (optional)" />
                        <select id="mm-type" class="openclaw-select openclaw-select">
                            <option value="">all types</option>
                            <option value="checkpoint">checkpoint</option>
                            <option value="lora">lora</option>
                            <option value="vae">vae</option>
                            <option value="controlnet">controlnet</option>
                            <option value="embedding">embedding</option>
                            <option value="text_encoders">text_encoders</option>
                            <option value="diffusion_models">diffusion_models</option>
                            <option value="clip_vision">clip_vision</option>
                            <option value="style_models">style_models</option>
                            <option value="upscale_models">upscale_models</option>
                            <option value="gligen">gligen</option>
                            <option value="latent_upscale_models">latent_upscale_models</option>
                            <option value="hypernetworks">hypernetworks</option>
                            <option value="photomaker">photomaker</option>
                            <option value="model_patches">model_patches</option>
                            <option value="audio_encoders">audio_encoders</option>
                            <option value="background_removal">background_removal</option>
                            <option value="frame_interpolation">frame_interpolation</option>
                            <option value="geometry_estimation">geometry_estimation</option>
                            <option value="optical_flow">optical_flow</option>
                            <option value="detection">detection</option>
                        </select>
                        <select id="mm-installed" class="openclaw-select openclaw-select">
                            <option value="">all</option>
                            <option value="false">catalog only</option>
                            <option value="true">installed only</option>
                        </select>
                        <button id="mm-search-btn" class="openclaw-btn openclaw-btn openclaw-btn-primary openclaw-btn-primary">Search</button>
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:6px;align-items:center;margin-top:6px;">
                        <input id="mm-destination-subdir" class="openclaw-input openclaw-input" placeholder="destination_subdir override (optional)" />
                        <input id="mm-filename" class="openclaw-input openclaw-input" placeholder="filename override (optional)" />
                        <input id="mm-tags" class="openclaw-input openclaw-input" placeholder="import tags (comma-separated)" />
                        <button id="mm-refresh-btn" class="openclaw-btn openclaw-btn">Refresh All</button>
                    </div>
                    <div style="font-size:12px;color:#999;margin-top:6px;">
                        Queue from catalog rows with full download contract, then import completed tasks into managed install root.
                    </div>
                </div>

                <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:10px;padding:10px;align-items:start;">
                    <div>
                        <div class="openclaw-section-header openclaw-section-header" style="margin-bottom:6px;">Search Results</div>
                        <div id="mm-search-results" class="openclaw-scroll-area openclaw-scroll-area">
                            <div class="openclaw-empty-state openclaw-empty-state">Loading...</div>
                        </div>
                    </div>
                    <div>
                        <div class="openclaw-section-header openclaw-section-header" style="margin-bottom:6px;">Download Tasks</div>
                        <div id="mm-tasks" class="openclaw-scroll-area openclaw-scroll-area">
                            <div class="openclaw-empty-state openclaw-empty-state">Loading...</div>
                        </div>
                    </div>
                    <div>
                        <div class="openclaw-section-header openclaw-section-header" style="margin-bottom:6px;">Installations</div>
                        <div id="mm-installations" class="openclaw-scroll-area openclaw-scroll-area">
                            <div class="openclaw-empty-state openclaw-empty-state">Loading...</div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const ui = {
            query: container.querySelector("#mm-query"),
            source: container.querySelector("#mm-source"),
            type: container.querySelector("#mm-type"),
            installed: container.querySelector("#mm-installed"),
            destinationSubdir: container.querySelector("#mm-destination-subdir"),
            filename: container.querySelector("#mm-filename"),
            tags: container.querySelector("#mm-tags"),
            searchBtn: container.querySelector("#mm-search-btn"),
            refreshBtn: container.querySelector("#mm-refresh-btn"),
            results: container.querySelector("#mm-search-results"),
            tasks: container.querySelector("#mm-tasks"),
            installations: container.querySelector("#mm-installations"),
        };

        const state = {
            items: [],
            tasks: [],
            installations: [],
            pollingTimer: null,
            taskSinceSeq: 0,
        };
        const modelManagerAction = {
            label: "Open Model Manager",
            type: "tab",
            payload: "model-manager",
        };

        const reportIssue = (message, dedupeKey) => {
            const text = String(message || "request_failed");
            showError(container, text);
            showToast(text, "error", {
                persist: true,
                source: "model-manager",
                dedupeKey,
                action: modelManagerAction,
            });
        };

        const renderResults = () => {
            if (!Array.isArray(state.items) || state.items.length === 0) {
                ui.results.innerHTML = '<div class="openclaw-empty-state openclaw-empty-state">No matching models.</div>';
                return;
            }
            ui.results.innerHTML = state.items.map((item, idx) => buildSearchItemCard(item, idx)).join("");
        };

        const renderTasks = () => {
            if (!Array.isArray(state.tasks) || state.tasks.length === 0) {
                ui.tasks.innerHTML = '<div class="openclaw-empty-state openclaw-empty-state">No download tasks.</div>';
                return;
            }
            ui.tasks.innerHTML = state.tasks.map((task) => buildTaskCard(task)).join("");
        };

        const renderInstallations = () => {
            if (!Array.isArray(state.installations) || state.installations.length === 0) {
                ui.installations.innerHTML = '<div class="openclaw-empty-state openclaw-empty-state">No managed installations.</div>';
                return;
            }
            ui.installations.innerHTML = state.installations.map((row) => buildInstallationCard(row)).join("");
        };

        const buildSearchParams = () => {
            const params = {
                q: normalizeString(ui.query.value),
                source: normalizeString(ui.source.value),
                model_type: normalizeString(ui.type.value),
                limit: 100,
                offset: 0,
            };
            const installed = normalizeString(ui.installed.value);
            if (installed === "true") params.installed = true;
            if (installed === "false") params.installed = false;
            return params;
        };

        const buildTaskOverrides = () => {
            const payload = {};
            const destinationSubdir = normalizeString(ui.destinationSubdir.value);
            const filename = normalizeString(ui.filename.value);
            if (destinationSubdir) payload.destination_subdir = destinationSubdir;
            if (filename) payload.filename = filename;
            return payload;
        };

        const buildImportOverrides = () => {
            const payload = {};
            const destinationSubdir = normalizeString(ui.destinationSubdir.value);
            const filename = normalizeString(ui.filename.value);
            const tags = parseTagList(ui.tags.value);
            if (destinationSubdir) payload.destination_subdir = destinationSubdir;
            if (filename) payload.filename = filename;
            if (tags.length) payload.tags = tags;
            return payload;
        };

        const loadSearch = async () => {
            const res = await openclawApi.searchModels(buildSearchParams());
            if (!res.ok) {
                throw new Error(res.error || "search_failed");
            }
            state.items = Array.isArray(res.data?.items) ? res.data.items : [];
            renderResults();
        };

        const loadTasks = async ({ delta = false } = {}) => {
            const params = { limit: 100, offset: 0 };
            if (delta && state.taskSinceSeq > 0) {
                params.since_seq = state.taskSinceSeq;
            }
            const res = await openclawApi.listModelDownloadTasks(params);
            if (!res.ok) {
                throw new Error(res.error || "tasks_list_failed");
            }
            const incomingTasks = Array.isArray(res.data?.tasks) ? res.data.tasks : [];
            state.tasks = params.since_seq != null
                ? mergeTaskDelta(state.tasks, incomingTasks)
                : sortTasksForDisplay(incomingTasks);
            state.taskSinceSeq = resolveNextTaskCursor(res.data, state.taskSinceSeq);
            renderTasks();
        };

        const loadInstallations = async () => {
            const res = await openclawApi.listModelInstallations({ limit: 100, offset: 0 });
            if (!res.ok) {
                throw new Error(res.error || "installations_list_failed");
            }
            state.installations = Array.isArray(res.data?.installations) ? res.data.installations : [];
            renderInstallations();
        };

        const refreshAll = async () => {
            clearError(container);
            const failures = [];
            const runOne = async (fn, label) => {
                try {
                    await fn();
                } catch (error) {
                    failures.push(`${label}: ${error?.message || String(error)}`);
                }
            };
            await runOne(loadSearch, "search");
            await runOne(loadTasks, "tasks");
            await runOne(loadInstallations, "installations");
            if (failures.length) {
                reportIssue(failures.join(" | "), "model-manager:refresh");
            }
        };

        const queueModelFromIndex = async (index) => {
            const item = state.items[index];
            if (!item) return;
            const payload = {
                model_id: item.id,
                name: item.name,
                model_type: item.model_type,
                source: item.source,
                source_label: item.source_label || item.source,
                download_url: item.download_url,
                expected_sha256: item.sha256,
                provenance: item.provenance || {},
                ...buildTaskOverrides(),
            };
            clearError(container);
            const res = await openclawApi.createModelDownloadTask(payload);
            if (!res.ok) {
                reportIssue(`queue failed: ${res.error || "request_failed"}`, "model-manager:queue");
                return;
            }
            showToast("Model download queued", "success", {
                persist: true,
                source: "model-manager",
                dedupeKey: "model-manager:queue-success",
                action: modelManagerAction,
            });
            await loadTasks({ delta: false });
        };

        const cancelTask = async (taskId) => {
            clearError(container);
            const res = await openclawApi.cancelModelDownloadTask(taskId);
            if (!res.ok) {
                reportIssue(`cancel failed: ${res.error || "request_failed"}`, "model-manager:cancel");
                return;
            }
            await loadTasks({ delta: false });
        };

        const importTask = async (taskId) => {
            clearError(container);
            const payload = {
                task_id: taskId,
                ...buildImportOverrides(),
            };
            const res = await openclawApi.importDownloadedModel(payload);
            if (!res.ok) {
                reportIssue(`import failed: ${res.error || "request_failed"}`, "model-manager:import");
                return;
            }
            showToast("Model imported", "success", {
                persist: true,
                source: "model-manager",
                dedupeKey: "model-manager:import-success",
                action: modelManagerAction,
            });
            await loadTasks({ delta: false });
            await loadInstallations();
            await loadSearch();
        };

        ui.searchBtn.onclick = () => {
            loadSearch().catch((error) => reportIssue(`search failed: ${error?.message || String(error)}`, "model-manager:search"));
        };
        ui.refreshBtn.onclick = () => {
            refreshAll().catch((error) => reportIssue(`refresh failed: ${error?.message || String(error)}`, "model-manager:refresh"));
        };
        ui.results.onclick = (event) => {
            const btn = event.target.closest("button[data-action='queue']");
            if (!btn) return;
            const index = Number(btn.getAttribute("data-index"));
            if (!Number.isFinite(index)) return;
            queueModelFromIndex(index).catch((error) => reportIssue(`queue failed: ${error?.message || String(error)}`, "model-manager:queue"));
        };
        ui.tasks.onclick = (event) => {
            const btn = event.target.closest("button[data-action]");
            if (!btn) return;
            const action = btn.getAttribute("data-action");
            const taskId = normalizeString(btn.getAttribute("data-task-id"));
            if (!taskId) return;
            if (action === "cancel-task") {
                cancelTask(taskId).catch((error) => reportIssue(`cancel failed: ${error?.message || String(error)}`, "model-manager:cancel"));
            } else if (action === "import-task") {
                importTask(taskId).catch((error) => reportIssue(`import failed: ${error?.message || String(error)}`, "model-manager:import"));
            }
        };

        // IMPORTANT: keep this bounded polling lightweight and tab-scoped.
        // Always-on global polling risks hidden background churn across remounts.
        state.pollingTimer = window.setInterval(() => {
            if (!document.body.contains(container)) {
                window.clearInterval(state.pollingTimer);
                state.pollingTimer = null;
                return;
            }
            const pane = container.closest(".openclaw-tab-pane");
            if (pane && !pane.classList.contains("active")) return;
            loadTasks({ delta: true }).catch(() => {
                // Poll refresh errors are surfaced by explicit refresh actions.
            });
        }, 3000);

        refreshAll().catch((error) => reportIssue(`initial load failed: ${error?.message || String(error)}`, "model-manager:initial-load"));
    },
};
