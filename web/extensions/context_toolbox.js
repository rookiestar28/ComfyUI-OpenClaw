// CRITICAL: this module is served from /extensions/<pack>/web/extensions/*.js in ComfyUI.
// Keep ../../../scripts/app.js so it resolves to ComfyUI core /scripts/app.js (not /extensions/<pack>/scripts/app.js).
import { app } from "../../../scripts/app.js";
import { hasComparableWidget } from "../openclaw_graph_host.js";

/**
 * F51: In-Canvas Context Toolbox
 * Adds quick actions to the node context menu.
 */

/**
 * The entries OpenClaw adds to a node's context menu: a separator, then the actions.
 * A fresh list on every call, so a menu that changes its items never changes another menu.
 * Callbacks reach the actions only when clicked.
 */
export function buildContextToolboxItems(node, actions) {
    const items = [];
    items.push(null); // Separator

    // 1. Inspect in Explorer
    items.push({
        content: "\uD83D\uDD0D OpenClaw: Inspect",
        callback: () => {
            actions.openExplorer(node.type);
        }
    });

    // 2. Doctor / Stats
    items.push({
        content: "\uD83D\uDC89 OpenClaw: Doctor",
        callback: () => {
            actions.openDoctor();
        }
    });

    // 3. Queue / Status
    items.push({
        content: "\u23F3 OpenClaw: Queue Status",
        callback: () => {
            actions.openQueue("all");
        }
    });

    // F50: OpenClaw Compare
    // Only show if node has inputs/widgets that can be compared
    if (hasComparableWidget(app.graph, node)) {
        items.push({
            content: "\u2696\uFE0F OpenClaw: Compare...",
            callback: () => {
                actions.openCompare(node);
            }
        });
    }

    // 4. Settings
    items.push({
        content: "\u2699\uFE0F OpenClaw: Settings",
        callback: () => {
            actions.openSettings();
        }
    });

    // 5. History (if applicable)
    // items.push({ ... });

    return items;
}

/**
 * True when the host appends `getNodeMenuItems` results to node menus itself.
 *
 * Two signals, each present in the frontends this pack is validated against (v1.59.11 and v1.61.8)
 * and independent of the other: the hook consumer `app.collectNodeMenuItems`, and the accessor the
 * host installs on `LGraphCanvas.prototype.getNodeMenuOptions` to warn about patches. Reading a
 * property descriptor runs neither the getter nor the setter, so asking cannot itself log the warning.
 */
export function hostCollectsNodeMenuItems() {
    if (typeof app?.collectNodeMenuItems === "function") return true;
    const prototype = globalThis.LGraphCanvas?.prototype;
    if (!prototype) return false;
    return typeof Object.getOwnPropertyDescriptor(prototype, "getNodeMenuOptions")?.set === "function";
}

export function registerContextToolbox() {
    // Set by `setup` once the actions exist; read by the hook whenever a menu opens.
    let actions = null;
    let legacyPatchInstalled = false;

    app.registerExtension({
        name: "OpenClaw.ContextToolbox",
        // Current hosts call this for every node menu and append what it returns.
        getNodeMenuItems(node) {
            if (!actions || legacyPatchInstalled) return [];
            return buildContextToolboxItems(node, actions);
        },
        async setup() {
            // Wait for OpenClaw actions singleton to be available.
            const { openclawActions } = await import("../openclaw_ui.js");
            actions = openclawActions;

            // GUARD: exactly one of the hook above and the prototype patch below may register these
            // entries. Current hosts append `getNodeMenuItems` results themselves and make assigning
            // getNodeMenuOptions log "[DEPRECATED] Monkey-patching getNodeMenuOptions ... (Extension: ...)"
            // naming this extension, so patching there would warn and show every entry twice. The patch
            // is only the fallback for hosts that predate the hook. Do not turn this into an
            // unconditional patch, or into a hook-only path that leaves those older hosts without the menu.
            if (hostCollectsNodeMenuItems()) return;

            legacyPatchInstalled = true;
            const originalGetNodeMenuOptions = LGraphCanvas.prototype.getNodeMenuOptions;

            LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
                const options = originalGetNodeMenuOptions.apply(this, arguments);
                if (!options) return options;

                options.push(...buildContextToolboxItems(node, openclawActions));
                return options;
            };
        }
    });
}
