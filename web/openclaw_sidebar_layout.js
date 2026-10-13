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

function readInlineStyle(element, property) {
    return Object.freeze({
        priority: element.style.getPropertyPriority(property),
        value: element.style.getPropertyValue(property),
    });
}

function sameInlineStyle(left, right) {
    return left.value === right.value && left.priority === right.priority;
}

function captureInlineStyles(element) {
    const values = new Map();
    for (const property of OWNED_STYLE_PROPERTIES) {
        values.set(property, { original: readInlineStyle(element, property), written: null });
    }
    return values;
}

function releaseInlineStyles(element, values) {
    for (const [property, { original, written }] of values) {
        // CRITICAL: release only a value OpenClaw wrote and the element still holds. The host
        // splitter pins user-dragged widths (flex-basis) on resize end; restoring the captured
        // value over that pin snaps the sidebar back to its pre-OpenClaw width on tab switch.
        if (!written || !sameInlineStyle(readInlineStyle(element, property), written)) continue;
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

    const write = (element, property, value) => {
        element.style.setProperty(property, value);
        // Record the normalized read-back so release can tell OpenClaw's value from a host rewrite.
        captured.get(element).get(property).written = readInlineStyle(element, property);
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
            write(splitterPanel, "min-width", floor);
            // CRITICAL: SplitterPanel owns visible allocation. A child/min-width-only edit
            // leaves percentage flex sizing narrow and clips the right side of OpenClaw.
            if (startingWidth < OPENCLAW_SIDEBAR_MIN_WIDTH_PX) {
                write(splitterPanel, "width", floor);
                write(splitterPanel, "flex-basis", floor);
            }
        }

        if (sidebarContent) {
            const startingWidth = measureWidth(sidebarContent);
            own(sidebarContent);
            write(sidebarContent, "min-width", floor);
            if (startingWidth < OPENCLAW_SIDEBAR_MIN_WIDTH_PX) {
                write(sidebarContent, "width", floor);
            }
        }

        own(container);
        write(container, "min-width", floor);
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
            releaseInlineStyles(element, values);
        }
        captured.clear();
    };
}
