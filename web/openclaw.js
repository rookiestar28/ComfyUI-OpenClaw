/**
 * ComfyUI-OpenClaw Entry Point
 * Registers the extension and mounts the UI.
 */
import { app } from "../../../scripts/app.js";
import { openclawUI } from "./openclaw_ui.js";
import { installGlobalErrorHandlers } from "./global_error_handler.js";
import { openclawApi } from "./openclaw_api.js";
// CRITICAL: registerContextToolbox transitively imports app.js from web/extensions/context_toolbox.js.
// If that module uses a wrong relative path (e.g. ../../scripts/app.js), the import chain fails at module-load time
// and this whole extension never reaches setup(), which makes the OpenClaw sidebar disappear.
import { registerContextToolbox } from "./extensions/context_toolbox.js"; // F51
import { getCompatibleSettingValue } from "./openclaw_compat.js";
import { stampHostSurfaceMetadata } from "./openclaw_host_surface.js";
import { registerOpenClawSidebar } from "./openclaw_sidebar_registration.js";

// Tabs
import { tabManager } from "./openclaw_tabs.js";
import { settingsTab } from "./tabs/settings_tab.js";
import { jobMonitorTab } from "./tabs/job_monitor_tab.js";
import { PlannerTab } from "./tabs/planner_tab.js";
import { VariantsTab } from "./tabs/variants_tab.js";
import { RefinerTab } from "./tabs/refiner_tab.js";
import { LibraryTab } from "./tabs/library_tab.js";
import { ApprovalsTab } from "./tabs/approvals_tab.js";
import { ExplorerTab } from "./tabs/explorer_tab.js";
import { PacksTab } from "./tabs/packs_tab.js";
import { ParameterLabTab } from "./tabs/parameter_lab_tab.js"; // F52
import { ModelManagerTab } from "./tabs/model_manager_tab.js"; // F64
import { PngInfoTab } from "./tabs/png_info_tab.js"; // F70



function ensureCssInjected(id, href) {
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.type = "text/css";
    link.href = href;
    document.head.appendChild(link);
}

function ensureOpenClawCssInjected() {
    ensureCssInjected(
        "openclaw-styles",
        new URL("./openclaw.css", import.meta.url).href
    );
}

function ensureErrorBoundaryCssInjected() {
    ensureCssInjected(
        "openclaw-error-boundary-styles",
        new URL("./error_boundary.css", import.meta.url).href
    );
}

function installLegacyMenuButton() {
    const menuStrip = document.querySelector(".comfy-menu");

    const btn = document.createElement("button");
    btn.textContent = "🤖 OpenClaw";
    btn.style.cssText = `
        background: transparent;
        color: var(--fg-color, #ccc);
        border: none;
        cursor: pointer;
        font-size: 14px;
        padding: 5px 10px;
        margin-top: 10px;
        border-top: 1px solid var(--border-color, #444);
        width: 100%;
        text-align: left;
    `;

    btn.addEventListener("click", () => openclawUI.toggleFloatingPanel());

    if (menuStrip) {
        menuStrip.appendChild(btn);
    } else {
        document.body.appendChild(btn);
    }
}

async function registerSupportedTabs() {
    // 1. Always register Settings & Job Monitor (Core)
    tabManager.registerTab(settingsTab);
    tabManager.registerTab(jobMonitorTab);

    // 2. Fetch Capabilities
    let features = {};
    let capabilitiesKnown = false;
    try {
        const res = await openclawApi.getCapabilities();
        if (res.ok && res.data && res.data.features) {
            features = res.data.features;
            capabilitiesKnown = true;
        } else {
            console.warn("[OpenClaw] Failed to fetch capabilities, using defaults.");
        }
    } catch (e) {
        console.error("[OpenClaw] Error fetching capabilities:", e);
    }

    // 3. Conditionally Register (with a safe fallback)
    //
    // If capabilities are unavailable (404/pack mismatch/route registration failure),
    // we intentionally show the full tab set so users can see actionable errors
    // instead of “missing tabs”.
    const fallbackShowAll = !capabilitiesKnown;

    if (fallbackShowAll || features.assist_planner) tabManager.registerTab(PlannerTab);
    if (fallbackShowAll || features.assist_refiner) tabManager.registerTab(RefinerTab);
    if (fallbackShowAll || features.scheduler) tabManager.registerTab(VariantsTab);
    if (fallbackShowAll || features.presets) tabManager.registerTab(LibraryTab);
    if (fallbackShowAll || features.approvals) tabManager.registerTab(ApprovalsTab);
    if (fallbackShowAll || features.explorer || features.preflight || features.checkpoints) {
        tabManager.registerTab(ExplorerTab); // Explorer: inventory + preflight + snapshots
    }
    if (fallbackShowAll || features.packs) tabManager.registerTab(PacksTab);
    if (fallbackShowAll || features.model_manager) tabManager.registerTab(ModelManagerTab);
    if (fallbackShowAll || features.png_info) tabManager.registerTab(PngInfoTab);

    // F52: Parameter Lab
    // Always enabled for now, or check capability
    tabManager.registerTab(ParameterLabTab);

    console.log("[OpenClaw] Tabs registered based on capabilities:", Object.keys(tabManager.tabs).length);
}

app.registerExtension({
    name: "ComfyUI-OpenClaw",

    async setup() {
        console.log("[OpenClaw] Extension loading...");

        // F26: Boot Diagnostics
        if (typeof fetchApi !== "function") {
            console.warn("[OpenClaw] ⚠️ Critical: fetchApi shim is missing. Backend calls may fail.");
        }
        if (!app) {
            console.warn("[OpenClaw] ⚠️ Critical: ComfyUI 'app' instance is missing.");
        } else {
            // Try to log version if available (ComfyUI doesn't standardly expose it in app object easily,
            // but we can log that we hooked it).
            console.log("[OpenClaw] Hooked into ComfyUI app instance.");
        }

        // Register Settings (F7)
        if (app?.ui?.settings?.addSetting) {
            app.ui.settings.addSetting({
                id: "OpenClaw.General.Enable",
                name: "Enable OpenClaw (requires restart)",
                type: "boolean",
                defaultValue: true,
            });
            app.ui.settings.addSetting({
                id: "OpenClaw.General.ErrorBoundaries",
                name: "Enable Error Boundaries (requires restart)",
                type: "boolean",
                defaultValue: true,
            });
            app.ui.settings.addSetting({
                id: "OpenClaw.Info",
                name: "ℹ️ Configure OpenClaw in the sidebar (left panel)",
                type: "text",
                defaultValue: "",
                attrs: { readonly: true, disabled: true },
            });
        }

        // Helper to read settings with backward compatibility
        const getSetting = (key, def) => {
            return getCompatibleSettingValue(app?.ui?.settings, key, def);
        };

        if (getSetting("General.Enable", true) === false) {
            console.log("[OpenClaw] Extension disabled via settings");
            return;
        }

        // Always inject base UI styles
        ensureOpenClawCssInjected();

        // Optional hardening (R6)
        if (getSetting("General.ErrorBoundaries", true)) {
            ensureErrorBoundaryCssInjected();
            installGlobalErrorHandlers();
        }

        // Register Tabs Dynamics
        try {
            await registerSupportedTabs();
        } catch (e) {
            console.error("[OpenClaw] Critical error registering tabs:", e);
            // Ensure core tabs are registered even if capabilities failed catastrophically
            tabManager.registerTab(settingsTab);
        }

        // Preferred: modern sidebar API
        try {
            // IMPORTANT: prefer the current sidebar store API while preserving the
            // deprecated extensionManager facade for desktop/older frontend bundles.
            const registration = registerOpenClawSidebar(app, {
                id: "comfyui-openclaw",
                icon: "pi pi-bolt",
                title: "OpenClaw",
                tooltip: "OpenClaw: AI assistant for ComfyUI",
                type: "custom",
                render: (container) => {
                    try {
                        // IMPORTANT: desktop bundles may lag standalone frontend; stamp
                        // the resolved host surface so host-sensitive regressions stay explicit.
                        stampHostSurfaceMetadata(container, { app, win: window });
                        openclawUI.mount(container);
                    } catch (renderError) {
                        openclawUI.unmount();
                        console.error("[OpenClaw] UI Mount Error:", renderError);
                        container.innerHTML = `<div style="padding:10px; color:red">UI Crash: ${renderError.message}</div>`;
                    }
                },
                destroy: () => openclawUI.unmount(),
            });
            if (!registration.ok) {
                throw registration.error;
            }
        } catch (e) {
            // Legacy fallback: left menu button + floating panel
            console.log("[OpenClaw] Sidebar API not found or failed, using legacy menu button:", e);
            installLegacyMenuButton();
        }

        // F51: Register Context Toolbox
        registerContextToolbox();

        console.log("[OpenClaw] Extension loaded.");
    },
});
