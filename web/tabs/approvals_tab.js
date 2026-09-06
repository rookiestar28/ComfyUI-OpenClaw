import { openclawApi } from "../openclaw_api.js";
import {
    showError,
    clearError,
    normalizeLegacyClassNames,
} from "../openclaw_utils.js";
import { escapeHtml } from "../openclaw_text_safety.js";

function stringifyVal(value) {
    if (typeof value === "object" && value !== null) return "{...}";
    return String(value);
}

function renderInputsSummary(inputs) {
    if (!inputs) return "";
    const keys = Object.keys(inputs);
    if (keys.length === 0) return "No inputs";
    const preview = keys.slice(0, 2).map((key) => `${key}: ${stringifyVal(inputs[key])}`);
    if (keys.length > 2) preview.push(`+${keys.length - 2} more`);
    return escapeHtml(preview.join(", "));
}

function renderApprovalItem(request) {
    const statusClass = request.status;
    const isPending = request.status === "pending";

    return `
        <div class="openclaw-list-item" style="padding: 10px; border-bottom: 1px solid var(--openclaw-color-border); margin-bottom: 4px; border-left: 3px solid var(--openclaw-color-border);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
                <div>
                    <div style="font-weight: bold; font-size: var(--openclaw-font-md); color: var(--openclaw-color-fg);">
                        ${escapeHtml(request.template_id)}
                        <span style="font-size: var(--openclaw-font-xs); font-weight: normal; color: var(--openclaw-color-fg-muted); margin-left:8px;">${escapeHtml(request.approval_id)}</span>
                    </div>
                    <div style="font-size: var(--openclaw-font-sm); color: var(--openclaw-color-fg-muted); margin-top: 4px;">
                        Inputs: <span style="color: #aaa;">${renderInputsSummary(request.inputs)}</span>
                    </div>
                    <div style="font-size: var(--openclaw-font-xs); color: #666; margin-top: 4px;">
                        Requested: ${new Date(request.requested_at).toLocaleString()}
                        ${request.source ? `via ${escapeHtml(request.source)}` : ""}
                    </div>
                </div>
                <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 5px;">
                    <span class="openclaw-badge ${statusClass}">${request.status.toUpperCase()}</span>
                    <div style="display: flex; gap: 5px; margin-top: 5px; flex-wrap: wrap; justify-content: flex-end;">
                        <button class="openclaw-btn openclaw-btn-sm" data-action="details" data-id="${request.approval_id}">Details</button>
                        ${isPending ? `
                            <button class="openclaw-btn openclaw-btn-sm openclaw-btn-primary" data-action="approve" data-id="${request.approval_id}">Approve</button>
                            <button class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-action="reject" data-id="${request.approval_id}">Reject</button>
                        ` : ""}
                    </div>
                </div>
            </div>
        </div>
    `;
}

export const ApprovalsTab = {
    id: "approvals",
    title: "Approvals",
    icon: "pi pi-check-circle",

    render(container) {
        container.innerHTML = `
            <div class="openclaw-panel">
                <div class="openclaw-card" style="border-radius:0; border:none; border-bottom:1px solid var(--openclaw-color-border);">
                    <div class="openclaw-section-header">Approval Requests</div>
                    <div class="openclaw-error-box" style="display:none"></div>
                    <div class="openclaw-toolbar" style="margin-top:5px; display:flex; gap:5px; align-items:center;" id="apr-toolbar">
                        <div id="apr-filter-btns" style="display: flex; gap: 5px; flex-wrap: wrap;">
                            <button class="openclaw-btn openclaw-btn-primary" data-status="pending">Pending</button>
                            <button class="openclaw-btn" data-status="approved">Approved</button>
                            <button class="openclaw-btn" data-status="rejected">Rejected</button>
                            <button class="openclaw-btn" data-status="">All</button>
                        </div>
                        <button class="openclaw-btn openclaw-btn-sm" id="apr-refresh-btn" style="margin-left: auto;">Refresh</button>
                    </div>
                </div>

                <div id="apr-list" class="openclaw-scroll-area" style="padding:0;">
                    <div class="openclaw-empty-state">Loading...</div>
                </div>
            </div>

            <div id="apr-editor-overlay" class="openclaw-modal-overlay" style="display:none;">
                <div id="apr-details-modal" class="openclaw-modal" style="width: 600px;">
                    <div class="openclaw-modal-header">
                        <span id="apr-modal-title">Request Details</span>
                    </div>
                    <div class="openclaw-modal-body">
                        <div style="font-family: var(--openclaw-font-mono); font-size: var(--openclaw-font-xs); background: #111; padding: 10px; border: 1px solid #333; height: 300px; overflow: auto; white-space: pre-wrap;" id="apr-modal-content"></div>
                    </div>
                    <div class="openclaw-modal-footer">
                        <button class="openclaw-btn" id="apr-modal-close">Close</button>
                        <div id="apr-modal-actions" style="display:flex; gap:10px;"></div>
                    </div>
                </div>
            </div>
        `;
        normalizeLegacyClassNames(container);

        const ui = {
            list: container.querySelector("#apr-list"),
            filters: container.querySelector("#apr-filter-btns"),
            refreshBtn: container.querySelector("#apr-refresh-btn"),
            modal: {
                overlay: container.querySelector("#apr-editor-overlay"),
                content: container.querySelector("#apr-modal-content"),
                close: container.querySelector("#apr-modal-close"),
                actions: container.querySelector("#apr-modal-actions"),
            },
        };

        const currentState = {
            status: "pending",
            approvals: [],
        };

        const renderList = () => {
            if (currentState.approvals.length === 0) {
                ui.list.innerHTML = '<div class="openclaw-empty-state">No requests found.</div>';
                return;
            }
            ui.list.innerHTML = currentState.approvals.map(renderApprovalItem).join("");
            normalizeLegacyClassNames(ui.list);
        };

        const loadApprovals = async () => {
            clearError(container);
            ui.list.innerHTML = '<div style="padding: 10px; text-align: center;">Loading...</div>';

            const params = { limit: 100 };
            if (currentState.status) params.status = currentState.status;

            const res = await openclawApi.getApprovals(params);
            if (res.ok) {
                currentState.approvals = res.data.approvals || [];
                renderList();
            } else {
                ui.list.innerHTML = "";
                showError(container, res.error);
            }
        };

        const handleApprove = async (id) => {
            if (!confirm("Approve this request? It will be executed immediately.")) return;

            const res = await openclawApi.approveRequest(id, { autoExecute: true });
            if (res.ok) {
                alert(`Approved! ${res.data.executed ? `Executed as prompt ${res.data.prompt_id}` : "Marked approved."}`);
                loadApprovals();
            } else {
                showError(container, `Approval failed: ${res.error}`);
            }
        };

        const handleReject = async (id) => {
            if (!confirm("Reject this request?")) return;

            const res = await openclawApi.rejectRequest(id);
            if (res.ok) {
                loadApprovals();
            } else {
                showError(container, `Rejection failed: ${res.error}`);
            }
        };

        const closeModal = () => {
            ui.modal.overlay.style.display = "none";
        };

        const openModal = () => {
            ui.modal.overlay.style.display = "flex";
        };

        const showDetails = async (id) => {
            let request = currentState.approvals.find((approval) => approval.approval_id === id);
            if (!request) {
                const res = await openclawApi.getApproval(id);
                if (res.ok) request = res.data.approval;
            }
            if (!request) return;

            ui.modal.content.textContent = JSON.stringify(request, null, 2);
            if (request.status === "pending") {
                ui.modal.actions.innerHTML = `
                    <button class="openclaw-btn openclaw-btn-primary" id="apr-modal-approve">Approve</button>
                    <button class="openclaw-btn openclaw-btn-danger" id="apr-modal-reject">Reject</button>
                `;
                container.querySelector("#apr-modal-approve").onclick = () => {
                    handleApprove(id);
                    closeModal();
                };
                container.querySelector("#apr-modal-reject").onclick = () => {
                    handleReject(id);
                    closeModal();
                };
            } else {
                ui.modal.actions.innerHTML = "";
            }
            openModal();
        };

        ui.filters.addEventListener("click", (event) => {
            const btn = event.target.closest("button[data-status]");
            if (!btn || btn.parentElement !== ui.filters) return;

            ui.filters
                .querySelectorAll("button[data-status]")
                .forEach((button) => button.classList.remove("openclaw-btn-primary"));
            btn.classList.add("openclaw-btn-primary");
            currentState.status = btn.dataset.status;
            loadApprovals();
        });

        ui.refreshBtn.addEventListener("click", loadApprovals);
        ui.list.addEventListener("click", (event) => {
            const btn = event.target.closest("button[data-action]");
            if (!btn) return;
            const action = btn.dataset.action;
            const id = btn.dataset.id;

            if (action === "approve") handleApprove(id);
            else if (action === "reject") handleReject(id);
            else if (action === "details") showDetails(id);
        });

        ui.modal.close.addEventListener("click", closeModal);
        ui.modal.overlay.addEventListener("click", (event) => {
            if (event.target === ui.modal.overlay) closeModal();
        });

        loadApprovals();
    },
};
