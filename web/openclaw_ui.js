/**
 * F7: OpenClaw UI Shell
 * Manages the main sidebar layout: Header, Tabs, Content.
 */
import { tabManager } from "./openclaw_tabs.js";
import { ErrorBoundary } from "./ErrorBoundary.js";
import { openclawApi } from "./openclaw_api.js";
import {
    applyLegacyClassAliases,
    normalizeLegacyClassNames,
} from "./openclaw_utils.js";
import { OpenClawActions } from "./openclaw_actions.js";
import { QueueMonitor } from "./openclaw_queue_monitor.js";
import { OpenClawNotificationCenter } from "./openclaw_notification_center.js";
import { OpenClawBannerManager } from "./openclaw_banner_manager.js";
import { acquireOpenClawSidebarLayout } from "./openclaw_sidebar_layout.js";

export class OpenClawUI {
    constructor() {
        this.container = null;
        this.boundary = new ErrorBoundary("OpenClawUI");
        this.floating = {
            panel: null,
            content: null,
        };
        this.sidebarLayoutDisposer = null;
        this.notificationCenter = new OpenClawNotificationCenter({
            onAction: (action) => this.handleAction(action),
        });
        this.bannerManager = new OpenClawBannerManager({
            onAction: (action) => this.handleAction(action),
        });
    }

    /**
     * Mount the UI into a provided container (sidebar render target).
     */
    mount(container) {
        this.unmount();
        this.container = container;
        const disposeLayout = acquireOpenClawSidebarLayout(container);
        this.sidebarLayoutDisposer = disposeLayout;
        this.boundary.run(container, () => {
            try {
                this._render(container);
            } catch (error) {
                this._releaseSidebarLayout(disposeLayout);
                throw error;
            }
        });
    }

    _releaseSidebarLayout(expectedDisposer = null) {
        if (expectedDisposer && this.sidebarLayoutDisposer !== expectedDisposer) return;
        const disposeLayout = this.sidebarLayoutDisposer;
        this.sidebarLayoutDisposer = null;
        disposeLayout?.();
    }

    unmount() {
        this._releaseSidebarLayout();
        this.container = null;
    }

    /**
     * Legacy fallback: toggle a floating panel (must not touch document.body directly).
     */
    toggleFloatingPanel() {
        if (!this.floating.panel) {
            const panel = document.createElement("div");
            panel.className = "openclaw-floating-panel";

            const close = document.createElement("button");
            close.className = "openclaw-floating-close";
            close.textContent = "\u00D7";
            close.title = "Close";
            close.addEventListener("click", () => {
                panel.classList.remove("visible");
                this.unmount();
            });

            const content = document.createElement("div");
            content.className = "openclaw-floating-content";

            panel.appendChild(close);
            panel.appendChild(content);
            document.body.appendChild(panel);

            this.floating.panel = panel;
            this.floating.content = content;
        }

        const panel = this.floating.panel;
        const content = this.floating.content;
        const isVisible = panel.classList.contains("visible");

        if (isVisible) {
            panel.classList.remove("visible");
            this.unmount();
            return;
        }

        panel.classList.add("visible");
        this.mount(content);
    }

    _render(container) {
        container.innerHTML = "";
        container.className = "openclaw-sidebar-container";

        const header = document.createElement("div");
        header.className = "openclaw-header";

        const statusDot = document.createElement("div");
        statusDot.className = "openclaw-status-dot ok";
        statusDot.title = "System Status";
        this.statusDot = statusDot;

        const title = document.createElement("div");
        title.className = "openclaw-title";
        title.textContent = "OpenClaw";

        const badges = document.createElement("div");
        badges.className = "openclaw-badges";

        const versionSpan = document.createElement("span");
        versionSpan.className = "openclaw-version";
        versionSpan.textContent = "v...";

        const repoLink = document.createElement("a");
        repoLink.href = "https://github.com/rookiestar28/ComfyUI-OpenClaw";
        repoLink.target = "_blank";
        repoLink.className = "openclaw-repo-link";
        repoLink.title = "View on GitHub";
        repoLink.textContent = "View on GitHub";

        badges.appendChild(versionSpan);
        badges.appendChild(repoLink);
        badges.appendChild(this.notificationCenter.buildToggle());

        openclawApi.getHealth().then((res) => {
            if (res.ok && res.data) {
                const data = res.data;
                if (data.pack) {
                    versionSpan.textContent = `v${data.pack.version}`;
                }

                const cpMode = data?.control_plane?.mode || data?.deployment_profile || "local";
                const modeBadge = document.createElement("span");
                modeBadge.className = `openclaw-mode-badge openclaw-mode-${cpMode}`;
                modeBadge.textContent = cpMode.toUpperCase();
                modeBadge.title = `Control plane: ${cpMode}`;
                modeBadge.style.cssText = `
                    font-size: 10px; padding: 1px 6px; border-radius: 4px;
                    font-weight: 600; margin-left: 6px; letter-spacing: 0.5px;
                `;
                if (cpMode === "split") {
                    modeBadge.style.background = "#f59e0b";
                    modeBadge.style.color = "#1a1a2e";
                } else if (cpMode === "hardened") {
                    modeBadge.style.background = "#ef4444";
                    modeBadge.style.color = "#fff";
                } else {
                    modeBadge.style.background = "#22c55e";
                    modeBadge.style.color = "#1a1a2e";
                }
                badges.appendChild(modeBadge);
                this._controlPlaneMode = cpMode;

                this.checkExposure(data?.access_policy);

                const obs = data.stats?.observability;
                if (obs && obs.total_dropped > 0) {
                    this.showBanner(
                        "warning",
                        `\u26A0\uFE0F High load: ${obs.total_dropped} observability events dropped (Queue full). logs/traces might be incomplete.`
                    );
                }
            } else {
                versionSpan.textContent = "v?.?.?";
            }
        }).catch(() => {
            versionSpan.textContent = "v?.?.?";
        });

        header.appendChild(statusDot);
        header.appendChild(title);
        header.appendChild(badges);
        container.appendChild(header);
        container.appendChild(this.notificationCenter.buildPanel());

        const tabBar = document.createElement("div");
        tabBar.className = "openclaw-tabs";
        this.tabBar = tabBar;
        container.appendChild(tabBar);

        const contentArea = document.createElement("div");
        contentArea.className = "openclaw-content";
        this.contentArea = contentArea;
        container.appendChild(contentArea);

        tabManager.init(tabBar, contentArea);
        normalizeLegacyClassNames(container);
        applyLegacyClassAliases(container);
        this.bannerManager.bind(container, (action) => this.handleAction(action));
        this.notificationCenter.render();
    }

    toggleNotifications() {
        this.notificationCenter.toggle();
    }

    /**
     * S15: Check if OpenClaw is exposed remotely and warn user.
     */
    checkExposure(policy) {
        if (!policy) return;

        const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
        if (!isLocal) {
            const isProtected = policy.observability === "token" && policy.token_configured;
            if (!isProtected) {
                this.showBanner(
                    "warning",
                    "\u26A0\uFE0F Remote access detected; logs/config are protected unless you explicitly enable token-based access."
                );
            }
        }
    }

    /**
     * F49: Display a banner with id, severity, ttl, and optional action.
     * @param {Object} banner - { id, severity, message, source, ttl_ms, dismissible, action }
     */
    showBanner(banner) {
        this.bannerManager.showBanner(...arguments);
    }

    handleAction(action) {
        if (!action) return;

        switch (action.type) {
            case "url":
                window.open(action.payload, "_blank");
                break;
            case "tab":
                tabManager.activateTab(action.payload);
                break;
            case "action":
                if (openclawActions && openclawActions.dispatch) {
                    openclawActions.dispatch(action.payload);
                } else {
                    console.log("Action triggered:", action.payload);
                }
                break;
        }
    }

    /**
     * F51: Glassmorphism Confirmation Modal.
     * @param {Object} options - { title, message, fatal, onConfirm }
     */
    showConfirm({ title, message, fatal = false, onConfirm }) {
        const overlay = document.createElement("div");
        overlay.className = "openclaw-modal-overlay";

        const modal = document.createElement("div");
        modal.className = `openclaw-modal ${fatal ? "fatal" : ""}`;

        const h3 = document.createElement("h3");
        h3.textContent = title || "Confirm Action";

        const p = document.createElement("p");
        p.textContent = message || "Are you sure?";

        const buttons = document.createElement("div");
        buttons.className = "openclaw-modal-buttons";

        const cancelBtn = document.createElement("button");
        cancelBtn.className = "openclaw-btn secondary";
        cancelBtn.textContent = "Cancel";
        cancelBtn.onclick = () => overlay.remove();

        const confirmBtn = document.createElement("button");
        confirmBtn.className = `openclaw-btn ${fatal ? "danger" : "primary"}`;
        confirmBtn.textContent = "Confirm";
        confirmBtn.onclick = () => {
            overlay.remove();
            if (onConfirm) onConfirm();
        };

        buttons.appendChild(cancelBtn);
        buttons.appendChild(confirmBtn);

        modal.appendChild(h3);
        modal.appendChild(p);
        modal.appendChild(buttons);
        overlay.appendChild(modal);

        this.container.appendChild(overlay);
        normalizeLegacyClassNames(overlay);
        applyLegacyClassAliases(overlay);
    }
}

export const openclawUI = new OpenClawUI();
export const openclawActions = new OpenClawActions(openclawUI);
export { OpenClawActions } from "./openclaw_actions.js";
export { QueueMonitor } from "./openclaw_queue_monitor.js";

const monitor = new QueueMonitor(openclawUI);
monitor.start();
