import { openclawNotifications } from "./openclaw_notifications.js";
import { escapeHtml } from "./openclaw_text_safety.js";

function formatNotificationTime(value) {
    if (!value) return "";
    try {
        return new Date(value).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    } catch {
        return "";
    }
}

export class OpenClawNotificationCenter {
    constructor({ notifications = openclawNotifications, onAction = null } = {}) {
        this.notifications = notifications;
        this.onAction = onAction;
        this.nodes = null;
        this.notificationsOpen = false;
        this.snapshot = [];
        this.unsubscribe = this.notifications.subscribe((entries) => {
            this.snapshot = Array.isArray(entries) ? entries : [];
            this.render();
        });
    }

    setActionHandler(handler) {
        this.onAction = typeof handler === "function" ? handler : null;
    }

    buildToggle() {
        const button = document.createElement("button");
        button.className = "openclaw-notification-toggle";
        button.id = "openclaw-notification-toggle";
        button.type = "button";
        button.innerHTML = `
            <span class="openclaw-notification-toggle-label">Alerts</span>
            <span class="openclaw-notification-badge" hidden>0</span>
        `;
        button.addEventListener("click", () => this.toggle());

        this.nodes = this.nodes || {};
        this.nodes.toggle = button;
        this.nodes.badge = button.querySelector(".openclaw-notification-badge");
        return button;
    }

    buildPanel() {
        const panel = document.createElement("div");
        panel.className = "openclaw-notification-panel";
        panel.id = "openclaw-notification-panel";
        panel.hidden = true;
        panel.innerHTML = `
            <div class="openclaw-notification-panel-header">
                <div>
                    <div class="openclaw-notification-panel-title">Notification Center</div>
                    <div class="openclaw-notification-panel-subtitle">Persistent operator alerts and actions</div>
                </div>
                <button type="button" class="openclaw-notification-close" aria-label="Close notification center">x</button>
            </div>
            <div class="openclaw-notification-list"></div>
        `;

        panel.querySelector(".openclaw-notification-close").addEventListener("click", () => {
            this.close();
        });

        panel.querySelector(".openclaw-notification-list").addEventListener("click", (event) => {
            const button = event.target.closest("button[data-notification-action]");
            if (!button) return;
            const action = button.getAttribute("data-notification-action");
            const id = button.getAttribute("data-notification-id");
            if (!id) return;

            if (action === "ack") {
                this.notifications.acknowledge(id);
                return;
            }
            if (action === "dismiss") {
                this.notifications.dismiss(id);
                return;
            }
            if (action === "open") {
                const entry = this.snapshot.find((item) => item.id === id);
                if (!entry?.action || !this.onAction) return;
                this.notifications.acknowledge(id);
                this.onAction(entry.action);
            }
        });

        this.nodes = this.nodes || {};
        this.nodes.panel = panel;
        this.nodes.list = panel.querySelector(".openclaw-notification-list");
        return panel;
    }

    toggle() {
        this.notificationsOpen = !this.notificationsOpen;
        this.render();
    }

    close() {
        this.notificationsOpen = false;
        this.render();
    }

    render() {
        const badge = this.nodes?.badge;
        const panel = this.nodes?.panel;
        const list = this.nodes?.list;
        if (!badge || !panel || !list) return;

        const activeEntries = this.snapshot.filter((entry) => !entry.dismissed_at);
        const unreadCount = activeEntries.filter((entry) => !entry.acknowledged_at).length;
        badge.textContent = unreadCount > 99 ? "99+" : String(unreadCount);
        badge.hidden = unreadCount <= 0;
        panel.hidden = !this.notificationsOpen;

        if (activeEntries.length === 0) {
            list.innerHTML = '<div class="openclaw-notification-empty">No active operator notifications.</div>';
            return;
        }

        list.innerHTML = activeEntries.map((entry) => {
            const escapedId = escapeHtml(entry.id);
            const escapedMessage = escapeHtml(entry.message || "");
            const escapedSource = escapeHtml(entry.source || "system");
            const escapedSeverity = escapeHtml(entry.severity || "info");
            const escapedActionLabel = escapeHtml(entry.action?.label || "Open");
            const countHtml = entry.count > 1
                ? `<span class="openclaw-notification-count">x${escapeHtml(entry.count)}</span>`
                : "";
            const actionHtml = entry.action?.type && entry.action?.payload
                ? `<button type="button" class="openclaw-btn openclaw-btn-sm" data-notification-action="open" data-notification-id="${escapedId}" aria-label="Open notification action for ${escapedMessage}">${escapedActionLabel}</button>`
                : "";
            const ackLabel = entry.acknowledged_at ? "Acknowledged" : "Acknowledge";
            const ackDisabled = entry.acknowledged_at ? "disabled" : "";

            return `
                <div class="openclaw-notification-item openclaw-notification-${escapedSeverity}">
                    <div class="openclaw-notification-meta">
                        <span class="openclaw-notification-source">${escapedSource}</span>
                        <span class="openclaw-notification-time">${escapeHtml(formatNotificationTime(entry.updated_at))}</span>
                    </div>
                    <div class="openclaw-notification-message">${escapedMessage}</div>
                    <div class="openclaw-notification-footer">
                        <div class="openclaw-notification-state">
                            <span class="openclaw-notification-severity">${escapedSeverity}</span>
                            ${countHtml}
                        </div>
                        <div class="openclaw-notification-actions">
                            ${actionHtml}
                            <button type="button" class="openclaw-btn openclaw-btn-sm" data-notification-action="ack" data-notification-id="${escapedId}" aria-label="Acknowledge notification: ${escapedMessage}" ${ackDisabled}>${ackLabel}</button>
                            <button type="button" class="openclaw-btn openclaw-btn-sm openclaw-btn-danger" data-notification-action="dismiss" data-notification-id="${escapedId}" aria-label="Dismiss notification: ${escapedMessage}">Dismiss</button>
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    }

    dispose() {
        if (typeof this.unsubscribe === "function") {
            this.unsubscribe();
            this.unsubscribe = null;
        }
    }
}
