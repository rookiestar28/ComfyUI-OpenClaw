export const OPENCLAW_SIDEBAR_MIN_WIDTH_PX = 560;

const OWNED_STYLE_PROPERTIES = Object.freeze([
    "min-width",
    "width",
    "flex-basis",
]);

function measureWidth(element) {
    const width = element?.getBoundingClientRect?.().width;
    return Number.isFinite(width) ? width : 0;
}

function captureInlineStyles(element) {
    const values = new Map();
    for (const property of OWNED_STYLE_PROPERTIES) {
        values.set(property, {
            priority: element.style.getPropertyPriority(property),
            value: element.style.getPropertyValue(property),
        });
    }
    return values;
}

function restoreInlineStyles(element, values) {
    for (const [property, original] of values) {
        if (original.value === "") {
            element.style.removeProperty(property);
        } else {
            element.style.setProperty(property, original.value, original.priority);
        }
    }
}

function scheduleDeferredPass(container, callback) {
    const view = container.ownerDocument?.defaultView ?? globalThis;
    if (
        typeof view.requestAnimationFrame === "function" &&
        typeof view.cancelAnimationFrame === "function"
    ) {
        const id = view.requestAnimationFrame(callback);
        return () => view.cancelAnimationFrame(id);
    }

    const id = view.setTimeout(callback, 0);
    return () => view.clearTimeout(id);
}

export function acquireOpenClawSidebarLayout(container) {
    if (!container || typeof container.closest !== "function" || !container.style) {
        throw new TypeError("OpenClaw sidebar layout requires an element container");
    }

    const captured = new Map();
    let disposed = false;
    let cancelDeferred = null;

    const own = (element) => {
        if (!element || captured.has(element)) return;
        // CRITICAL: capture every owned inline value before the first mutation. Capturing
        // later loses host/user sizing and makes destroy leak OpenClaw layout into another tab.
        captured.set(element, captureInlineStyles(element));
    };

    const apply = () => {
        if (disposed) return;

        const sidePanel = container.closest(".side-bar-panel");
        const splitterPanel = sidePanel ?? container.closest(".p-splitterpanel");
        const sidebarContent = container.closest(".sidebar-content-container");
        const floor = `${OPENCLAW_SIDEBAR_MIN_WIDTH_PX}px`;

        if (splitterPanel) {
            const startingWidth = measureWidth(splitterPanel);
            own(splitterPanel);
            splitterPanel.style.minWidth = floor;
            // CRITICAL: SplitterPanel owns visible allocation. A child/min-width-only edit
            // leaves percentage flex sizing narrow and clips the right side of OpenClaw.
            if (startingWidth < OPENCLAW_SIDEBAR_MIN_WIDTH_PX) {
                splitterPanel.style.width = floor;
                splitterPanel.style.flexBasis = floor;
            }
        }

        if (sidebarContent) {
            const startingWidth = measureWidth(sidebarContent);
            own(sidebarContent);
            sidebarContent.style.minWidth = floor;
            if (startingWidth < OPENCLAW_SIDEBAR_MIN_WIDTH_PX) {
                sidebarContent.style.width = floor;
            }
        }

        own(container);
        container.style.minWidth = floor;
    };

    apply();
    cancelDeferred = scheduleDeferredPass(container, () => {
        cancelDeferred = null;
        apply();
    });

    return function disposeOpenClawSidebarLayout() {
        if (disposed) return;
        disposed = true;
        // CRITICAL: cancel before restoring. Otherwise a stale frame can run after destroy
        // and reapply OpenClaw sizing to the next sidebar owner.
        cancelDeferred?.();
        cancelDeferred = null;
        for (const [element, values] of [...captured.entries()].reverse()) {
            restoreInlineStyles(element, values);
        }
        captured.clear();
    };
}
