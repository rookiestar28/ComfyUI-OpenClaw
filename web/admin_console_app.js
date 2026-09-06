import { createRemoteAdminApi, parseSseChunk } from "./admin_console_api.js";
import { stampHostSurfaceMetadata } from "./openclaw_host_surface.js";

function query(root, id) {
    return root.getElementById(id);
}

function now() {
    return new Date().toLocaleTimeString();
}

function setStatus(node, message, className = "") {
    node.textContent = message || "";
    node.className = `status${className ? ` ${className}` : ""}`;
}

function fillBox(node, value) {
    node.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

/**
 * S104: build a list detail row as text.
 *
 * CRITICAL: run/approval/schedule/error values arrive from backend records and
 * must reach the DOM through `textContent`. Do not restore template-literal
 * `innerHTML` interpolation here; the render-safety policy pins this file at
 * zero dynamic sinks.
 */
function textRow(root, text, className = "tiny") {
    const node = root.createElement("div");
    node.className = className;
    node.textContent = text;
    return node;
}

/** S104: build the emphasized identifier row as text. */
function boldRow(root, text) {
    const node = root.createElement("div");
    const label = root.createElement("b");
    label.textContent = text;
    node.appendChild(label);
    return node;
}

function createElements(root) {
    return {
        token: query(root, "token"),
        saveToken: query(root, "saveToken"),
        clearToken: query(root, "clearToken"),
        refreshAll: query(root, "refreshAll"),
        chips: query(root, "chips"),
        globalStatus: query(root, "globalStatus"),
        dashKv: query(root, "dashKv"),
        errorsBox: query(root, "errorsBox"),
        refreshRuns: query(root, "refreshRuns"),
        connectSse: query(root, "connectSse"),
        disconnectSse: query(root, "disconnectSse"),
        eventsStatus: query(root, "eventsStatus"),
        runsList: query(root, "runsList"),
        eventsBox: query(root, "eventsBox"),
        refreshApprovals: query(root, "refreshApprovals"),
        approvalsList: query(root, "approvalsList"),
        refreshSchedules: query(root, "refreshSchedules"),
        schedulesList: query(root, "schedulesList"),
        trigTemplate: query(root, "trigTemplate"),
        trigApproval: query(root, "trigApproval"),
        trigInputs: query(root, "trigInputs"),
        fireTrigger: query(root, "fireTrigger"),
        trigStatus: query(root, "trigStatus"),
        cfgProvider: query(root, "cfgProvider"),
        cfgModel: query(root, "cfgModel"),
        cfgBase: query(root, "cfgBase"),
        cfgTimeout: query(root, "cfgTimeout"),
        cfgRetries: query(root, "cfgRetries"),
        cfgKey: query(root, "cfgKey"),
        loadCfg: query(root, "loadCfg"),
        saveCfg: query(root, "saveCfg"),
        cfgStatus: query(root, "cfgStatus"),
        refreshDoctor: query(root, "refreshDoctor"),
        doctorBox: query(root, "doctorBox"),
        inventoryBox: query(root, "inventoryBox"),
        qaRetry: query(root, "qaRetry"),
        qaModels: query(root, "qaModels"),
        qaDrill: query(root, "qaDrill"),
        quickBox: query(root, "quickBox"),
    };
}

export function mountAdminConsole(root = document) {
    const view = root.defaultView || window;
    const metadataTarget = root.body || root.documentElement;
    if (metadataTarget) {
        // IMPORTANT: keep admin console host evidence explicit so desktop-host
        // regressions can be separated from standalone frontend drift in E2E.
        stampHostSurfaceMetadata(metadataTarget, { win: view });
    }
    const api = createRemoteAdminApi({
        fetchImpl: view.fetch.bind(view),
        storage: view.localStorage,
    });
    const elements = createElements(root);
    elements.token.value = api.getToken();

    function appendEvent(obj) {
        const seq = Number(obj?.seq);
        if (!Number.isNaN(seq)) {
            if (seq <= api.state.lastSeq) {
                return;
            }
            api.state.lastSeq = seq;
        }
        const line = `[${now()}] ${JSON.stringify(obj)}`;
        const lines = elements.eventsBox.textContent ? elements.eventsBox.textContent.split("\n") : [];
        lines.push(line);
        elements.eventsBox.textContent = lines.slice(-120).join("\n");
        elements.eventsBox.scrollTop = elements.eventsBox.scrollHeight;
    }

    async function loadDashboard() {
        const [healthRes, logsRes, schedulesRes, runsRes] = await Promise.all([
            api.request("/health"),
            api.request("/logs/tail?lines=120"),
            api.request("/schedules"),
            api.request("/runs?limit=20"),
        ]);
        if (!healthRes.ok) {
            setStatus(elements.globalStatus, `Health fetch failed: ${healthRes.error || "unknown"}`, "err");
            return;
        }

        const health = healthRes.data || {};
        const config = health.config || {};
        const stats = health.stats || {};
        const schedules = schedulesRes.ok && Array.isArray(schedulesRes.data?.schedules)
            ? schedulesRes.data.schedules
            : [];
        const runs = runsRes.ok && Array.isArray(runsRes.data?.runs)
            ? runsRes.data.runs
            : [];
        const enabledCount = schedules.filter((item) => Boolean(item.enabled)).length;
        const failedRuns = runs.filter((item) => String(item.status || "").toLowerCase().includes("fail")).length;

        elements.chips.innerHTML = "";
        [
            `Version ${(health.pack && health.pack.version) || "n/a"}`,
            `Provider ${config.provider || "n/a"}`,
            `API Key ${config.llm_key_configured ? "Configured" : "Missing"}`,
            `Schedules ${enabledCount}/${schedules.length}`,
            `Failed runs ${failedRuns}`,
            `Uptime ${Math.floor(Number(health.uptime_sec || 0))}s`,
        ].forEach((text) => {
            const chip = root.createElement("span");
            chip.className = "chip";
            chip.textContent = text;
            elements.chips.appendChild(chip);
        });

        const kv = {
            profile: health.deployment_profile || "n/a",
            control_plane: health.control_plane?.mode || "n/a",
            pack: health.pack?.name || "n/a",
            approvals_pending: Number(stats.approvals_pending || 0),
            queue_depth: Number(stats.queue_depth || 0),
            observability_dropped: Number(stats.observability?.total_dropped || 0),
        };
        elements.dashKv.innerHTML = "";
        Object.entries(kv).forEach(([key, value]) => {
            const keyNode = root.createElement("div");
            keyNode.className = "k";
            keyNode.textContent = key;
            const valueNode = root.createElement("div");
            valueNode.textContent = String(value);
            elements.dashKv.appendChild(keyNode);
            elements.dashKv.appendChild(valueNode);
        });

        const errorLines = logsRes.ok && typeof logsRes.data?.tail === "string"
            ? logsRes.data.tail
                .split(/\r?\n/)
                .filter((line) => /\b(error|traceback|fatal|critical)\b/i.test(line))
                .slice(-40)
            : [];
        fillBox(elements.errorsBox, errorLines.length ? errorLines.join("\n") : "No recent error lines.");
        elements.cfgKey.value = config.llm_key_configured ? "Configured" : "Missing";
        setStatus(elements.globalStatus, `Dashboard refreshed at ${now()}`, "ok");
    }

    async function refreshRuns() {
        const response = await api.request("/runs?limit=30");
        if (!response.ok) {
            setStatus(elements.eventsStatus, `Runs fetch failed: ${response.error || "unknown"}`, "err");
            return;
        }

        const runs = response.data?.runs || [];
        elements.runsList.innerHTML = "";
        if (!runs.length) {
            elements.runsList.innerHTML = '<div class="tiny">No run records.</div>';
            return;
        }

        runs.forEach((run) => {
            const node = root.createElement("div");
            node.className = "item";
            node.appendChild(boldRow(root, run.run_id || "run"));
            node.appendChild(textRow(
                root,
                `status=${run.status || "n/a"} schedule=${run.schedule_id || "n/a"}`,
            ));
            node.appendChild(textRow(
                root,
                `template=${run.template_id || "n/a"} at=${run.started_at || run.created_at || "n/a"}`,
            ));
            elements.runsList.appendChild(node);
        });
        setStatus(elements.eventsStatus, `Runs refreshed at ${now()}`, "ok");
    }

    async function pollEvents() {
        let polls = 0;
        let totalEvents = 0;
        let cursor = api.state.lastSeq;
        while (polls < 4) {
            const response = await api.request(`/events?since=${encodeURIComponent(String(cursor))}&limit=50`);
            if (!response.ok) {
                setStatus(elements.eventsStatus, `Events poll failed: ${response.error || "unknown"}`, "err");
                return;
            }
            const events = Array.isArray(response.data?.events) ? response.data.events : [];
            events.forEach(appendEvent);
            totalEvents += events.length;
            const nextSinceSeq = Number(response.data?.delta?.next_since_seq);
            cursor = Number.isFinite(nextSinceSeq) ? nextSinceSeq : api.state.lastSeq;
            polls += 1;
            if (!response.data?.delta?.truncated) break;
        }
        setStatus(elements.eventsStatus, `Polled ${totalEvents} events`, "ok");
    }

    function disconnectSse() {
        if (!api.state.sseAbort) return;
        api.state.sseAbort.abort();
        api.state.sseAbort = null;
        setStatus(elements.eventsStatus, "SSE disconnected", "warn-txt");
    }

    async function connectSse() {
        disconnectSse();
        const controller = new AbortController();
        api.state.sseAbort = controller;

        const response = await api.openStream("/events/stream", {
            headers: { Accept: "text/event-stream" },
            signal: controller.signal,
        });
        if (!response || !response.ok || !response.body) {
            setStatus(elements.eventsStatus, "SSE unavailable; polling fallback", "warn-txt");
            await pollEvents();
            return;
        }

        setStatus(elements.eventsStatus, "SSE connected", "ok");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        try {
            while (true) {
                const step = await reader.read();
                if (step.done) break;
                buffer += decoder.decode(step.value, { stream: true });
                const chunks = buffer.split(/\r?\n\r?\n/);
                buffer = chunks.pop() || "";
                chunks.forEach((chunk) => {
                    if (!chunk || chunk.startsWith(":")) return;
                    const event = parseSseChunk(chunk);
                    if (event) appendEvent(event);
                });
            }
        } catch (error) {
            if (!controller.signal.aborted) {
                setStatus(elements.eventsStatus, "SSE interrupted; polling fallback", "warn-txt");
                await pollEvents();
            }
            return error;
        }
        return null;
    }

    async function refreshApprovals() {
        const response = await api.request("/approvals?status=pending&limit=60&offset=0");
        elements.approvalsList.innerHTML = "";
        if (!response.ok) {
            elements.approvalsList.appendChild(textRow(
                root,
                `Approvals fetch failed: ${response.error || "unknown"}`,
                "tiny err",
            ));
            return;
        }

        const approvals = response.data?.approvals || [];
        if (!approvals.length) {
            elements.approvalsList.innerHTML = '<div class="tiny">No pending approvals.</div>';
            return;
        }

        approvals.forEach((approval) => {
            const item = root.createElement("div");
            item.className = "item";
            item.appendChild(boldRow(root, approval.approval_id || "approval"));
            item.appendChild(textRow(
                root,
                `template=${approval.template_id || "n/a"} source=${approval.source || "n/a"}`,
            ));
            const bar = root.createElement("div");
            bar.className = "tools";

            const approveButton = root.createElement("button");
            approveButton.textContent = "Approve";
            approveButton.onclick = async () => {
                const result = await api.request(`/approvals/${encodeURIComponent(approval.approval_id)}/approve`, {
                    method: "POST",
                    body: { actor: "remote_admin", auto_execute: true },
                });
                appendEvent({ event_type: "approval_approve", id: approval.approval_id, ok: result.ok, detail: result.data || result.error });
                await refreshApprovals();
                await refreshRuns();
            };

            const rejectButton = root.createElement("button");
            rejectButton.className = "danger";
            rejectButton.textContent = "Reject";
            rejectButton.onclick = async () => {
                const result = await api.request(`/approvals/${encodeURIComponent(approval.approval_id)}/reject`, {
                    method: "POST",
                    body: { actor: "remote_admin" },
                });
                appendEvent({ event_type: "approval_reject", id: approval.approval_id, ok: result.ok, detail: result.data || result.error });
                await refreshApprovals();
            };

            bar.appendChild(approveButton);
            bar.appendChild(rejectButton);
            item.appendChild(bar);
            elements.approvalsList.appendChild(item);
        });
    }

    async function refreshSchedules() {
        const response = await api.request("/schedules");
        elements.schedulesList.innerHTML = "";
        if (!response.ok) {
            elements.schedulesList.appendChild(textRow(
                root,
                `Schedules fetch failed: ${response.error || "unknown"}`,
                "tiny err",
            ));
            return;
        }

        const schedules = response.data?.schedules || [];
        if (!schedules.length) {
            elements.schedulesList.innerHTML = '<div class="tiny">No schedules configured.</div>';
            return;
        }

        schedules.forEach((schedule) => {
            const item = root.createElement("div");
            item.className = "item";
            item.appendChild(boldRow(root, schedule.name || schedule.schedule_id));
            item.appendChild(textRow(
                root,
                `id=${schedule.schedule_id} enabled=${Boolean(schedule.enabled)} trigger=${schedule.trigger_type || "n/a"}`,
            ));
            item.appendChild(textRow(root, `template=${schedule.template_id || "n/a"}`));
            const bar = root.createElement("div");
            bar.className = "tools";

            const toggleButton = root.createElement("button");
            toggleButton.className = "subtle";
            toggleButton.textContent = "Toggle";
            toggleButton.onclick = async () => {
                const result = await api.request(`/schedules/${encodeURIComponent(schedule.schedule_id)}/toggle`, {
                    method: "POST",
                });
                appendEvent({ event_type: "schedule_toggle", schedule_id: schedule.schedule_id, ok: result.ok, detail: result.data || result.error });
                await refreshSchedules();
                await loadDashboard();
            };

            const runButton = root.createElement("button");
            runButton.textContent = "Run Now";
            runButton.onclick = async () => {
                const result = await api.request(`/schedules/${encodeURIComponent(schedule.schedule_id)}/run`, {
                    method: "POST",
                });
                appendEvent({ event_type: "schedule_run", schedule_id: schedule.schedule_id, ok: result.ok, detail: result.data || result.error });
                await refreshRuns();
            };

            bar.appendChild(toggleButton);
            bar.appendChild(runButton);
            item.appendChild(bar);
            elements.schedulesList.appendChild(item);
        });
    }

    async function fireTrigger() {
        setStatus(elements.trigStatus, "Submitting trigger...", "warn-txt");
        const templateId = elements.trigTemplate.value.trim();
        if (!templateId) {
            setStatus(elements.trigStatus, "template_id is required", "err");
            return;
        }

        let inputs = {};
        if (elements.trigInputs.value.trim()) {
            try {
                inputs = JSON.parse(elements.trigInputs.value);
            } catch (error) {
                setStatus(elements.trigStatus, `inputs JSON parse error: ${String(error)}`, "err");
                return;
            }
        }

        const requireApprovalRaw = elements.trigApproval.value.trim().toLowerCase();
        const requireApproval = requireApprovalRaw === "true" || requireApprovalRaw === "1";
        const response = await api.request("/triggers/fire", {
            method: "POST",
            body: {
                template_id: templateId,
                inputs,
                require_approval: requireApproval,
            },
        });
        if (!response.ok) {
            setStatus(elements.trigStatus, `Trigger failed: ${response.error || "unknown"}`, "err");
            return;
        }

        setStatus(elements.trigStatus, "Trigger accepted", "ok");
        appendEvent({ event_type: "trigger_fire", detail: response.data });
        await refreshApprovals();
        await refreshRuns();
    }

    async function loadConfig() {
        const response = await api.request("/config");
        if (!response.ok) {
            setStatus(elements.cfgStatus, `Config read failed: ${response.error || "unknown"}`, "err");
            return;
        }

        const config = response.data?.config || {};
        elements.cfgProvider.value = config.provider || "";
        elements.cfgModel.value = config.model || "";
        elements.cfgBase.value = config.base_url || "";
        elements.cfgTimeout.value = config.timeout_sec != null ? String(config.timeout_sec) : "";
        elements.cfgRetries.value = config.max_retries != null ? String(config.max_retries) : "";
        setStatus(elements.cfgStatus, "Config loaded", "ok");
    }

    async function saveConfig() {
        const response = await api.request("/config", {
            method: "PUT",
            body: {
                provider: elements.cfgProvider.value.trim(),
                model: elements.cfgModel.value.trim(),
                base_url: elements.cfgBase.value.trim(),
                timeout_sec: Number(elements.cfgTimeout.value || "0") || 120,
                max_retries: Number(elements.cfgRetries.value || "0") || 0,
            },
        });
        if (!response.ok) {
            setStatus(elements.cfgStatus, `Config save failed: ${response.error || "unknown"}`, "err");
            fillBox(elements.quickBox, response.data || response);
            return;
        }
        setStatus(elements.cfgStatus, "Config saved", "ok");
        await loadDashboard();
    }

    async function refreshDoctor() {
        const [doctorRes, inventoryRes] = await Promise.all([
            api.request("/security/doctor"),
            api.request("/preflight/inventory"),
        ]);
        fillBox(elements.doctorBox, doctorRes.ok ? doctorRes.data : { error: doctorRes.error, status: doctorRes.status, data: doctorRes.data });
        fillBox(elements.inventoryBox, inventoryRes.ok ? inventoryRes.data : { error: inventoryRes.error, status: inventoryRes.status, data: inventoryRes.data });
    }

    async function qaRetry() {
        const runsRes = await api.request("/runs?status=failed&limit=1");
        if (!runsRes.ok) {
            fillBox(elements.quickBox, { action: "retry_failed", ok: false, error: runsRes.error, detail: runsRes.data });
            return;
        }

        const runs = runsRes.data?.runs || [];
        if (!runs.length) {
            fillBox(elements.quickBox, { action: "retry_failed", ok: false, error: "no_failed_run_found" });
            return;
        }
        const run = runs[0];
        if (!run.schedule_id) {
            fillBox(elements.quickBox, { action: "retry_failed", ok: false, error: "failed_run_has_no_schedule_id", run });
            return;
        }

        const response = await api.request(`/schedules/${encodeURIComponent(run.schedule_id)}/run`, {
            method: "POST",
        });
        fillBox(elements.quickBox, {
            action: "retry_failed",
            ok: response.ok,
            target_schedule: run.schedule_id,
            detail: response.data || response.error,
        });
        await refreshRuns();
    }

    async function qaModels() {
        const provider = elements.cfgProvider.value.trim();
        const suffix = provider ? `?provider=${encodeURIComponent(provider)}` : "";
        const response = await api.request(`/llm/models${suffix}`);
        fillBox(elements.quickBox, { action: "refresh_models", ok: response.ok, detail: response.data || response.error });
    }

    async function qaDrill() {
        if (!view.confirm("Run drill via Tools API? This is an admin action.")) return;
        const listResponse = await api.request("/tools");
        if (!listResponse.ok) {
            fillBox(elements.quickBox, { action: "run_drill", ok: false, error: listResponse.error, detail: listResponse.data });
            return;
        }
        const tool = (listResponse.data?.tools || []).find((item) => /drill|crypto/i.test(String(item.name || "")));
        if (!tool) {
            fillBox(elements.quickBox, {
                action: "run_drill",
                ok: false,
                error: "no_drill_tool_found",
                tools: (listResponse.data?.tools || []).map((item) => item.name),
            });
            return;
        }

        const response = await api.request(`/tools/${encodeURIComponent(tool.name)}/run`, {
            method: "POST",
            body: { args: { scenarios: "planned_rotation,token_compromise" } },
        });
        fillBox(elements.quickBox, {
            action: "run_drill",
            tool: tool.name,
            ok: response.ok,
            detail: response.data || response.error,
        });
    }

    async function refreshAll() {
        await Promise.all([
            loadDashboard(),
            refreshRuns(),
            refreshApprovals(),
            refreshSchedules(),
            loadConfig(),
            refreshDoctor(),
        ]);
    }

    elements.saveToken.onclick = () => {
        api.setToken(elements.token.value);
        setStatus(elements.globalStatus, "Token saved locally in this browser", "ok");
    };
    elements.clearToken.onclick = () => {
        api.clearToken();
        elements.token.value = "";
        setStatus(elements.globalStatus, "Token cleared", "warn-txt");
    };
    elements.refreshAll.onclick = refreshAll;
    elements.refreshRuns.onclick = refreshRuns;
    elements.connectSse.onclick = connectSse;
    elements.disconnectSse.onclick = disconnectSse;
    elements.refreshApprovals.onclick = refreshApprovals;
    elements.refreshSchedules.onclick = refreshSchedules;
    elements.fireTrigger.onclick = fireTrigger;
    elements.loadCfg.onclick = loadConfig;
    elements.saveCfg.onclick = saveConfig;
    elements.refreshDoctor.onclick = refreshDoctor;
    elements.qaRetry.onclick = qaRetry;
    elements.qaModels.onclick = qaModels;
    elements.qaDrill.onclick = qaDrill;
    view.addEventListener("beforeunload", disconnectSse);

    refreshAll();

    return {
        api,
        disconnectSse,
        refreshAll,
    };
}
