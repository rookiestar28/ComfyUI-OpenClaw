import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
    OPENCLAW_SIDEBAR_MIN_WIDTH_PX,
    acquireOpenClawSidebarLayout,
} from "../../../web/openclaw_sidebar_layout.js";

function setMeasuredWidth(element, initialWidth) {
    element.getBoundingClientRect = vi.fn(() => {
        const inlineWidth = Number.parseFloat(element.style.width);
        const width = Number.isFinite(inlineWidth) ? inlineWidth : initialWidth;
        return { bottom: 0, height: 0, left: 0, right: width, top: 0, width, x: 0, y: 0 };
    });
}

function buildHost({ panelWidth = 340, contentWidth = panelWidth, mountWidth = contentWidth } = {}) {
    document.body.innerHTML = `
        <div class="side-bar-panel p-splitterpanel">
            <div class="sidebar-content-container">
                <div id="mount"></div>
            </div>
        </div>
    `;
    const panel = document.querySelector(".side-bar-panel");
    const content = document.querySelector(".sidebar-content-container");
    const mount = document.querySelector("#mount");
    setMeasuredWidth(panel, panelWidth);
    setMeasuredWidth(content, contentWidth);
    setMeasuredWidth(mount, mountWidth);
    return { content, mount, panel };
}

describe("OpenClaw sidebar layout ownership", () => {
    let deferred;
    let cancelled;

    beforeEach(() => {
        deferred = new Map();
        cancelled = [];
        let nextId = 1;
        vi.stubGlobal("requestAnimationFrame", vi.fn((callback) => {
            const id = nextId++;
            deferred.set(id, callback);
            return id;
        }));
        vi.stubGlobal("cancelAnimationFrame", vi.fn((id) => {
            cancelled.push(id);
            deferred.delete(id);
        }));
    });

    afterEach(() => {
        document.body.replaceChildren();
        vi.unstubAllGlobals();
    });

    it("allocates width and flex-basis only when the measured host owner is below 560px", () => {
        const { content, mount, panel } = buildHost();

        const dispose = acquireOpenClawSidebarLayout(mount);

        expect(OPENCLAW_SIDEBAR_MIN_WIDTH_PX).toBe(560);
        expect(panel.style.minWidth).toBe("560px");
        expect(panel.style.width).toBe("560px");
        expect(panel.style.flexBasis).toBe("560px");
        expect(content.style.minWidth).toBe("560px");
        expect(content.style.width).toBe("560px");
        expect(mount.style.minWidth).toBe("560px");
        dispose();
    });

    it("does not shrink or replace an above-floor width and flex-basis", () => {
        const { content, mount, panel } = buildHost({ panelWidth: 720, contentWidth: 720, mountWidth: 720 });
        panel.style.width = "720px";
        panel.style.flexBasis = "65%";
        content.style.width = "720px";

        const dispose = acquireOpenClawSidebarLayout(mount);

        expect(panel.style.width).toBe("720px");
        expect(panel.style.flexBasis).toBe("65%");
        expect(content.style.width).toBe("720px");
        dispose();
    });

    it("uses the p-splitterpanel fallback and degrades safely when wrappers are absent", () => {
        document.body.innerHTML = '<div class="p-splitterpanel"><div id="fallback"></div></div>';
        const panel = document.querySelector(".p-splitterpanel");
        const mount = document.querySelector("#fallback");
        setMeasuredWidth(panel, 320);
        setMeasuredWidth(mount, 320);

        const disposeFallback = acquireOpenClawSidebarLayout(mount);
        expect(panel.style.width).toBe("560px");
        disposeFallback();

        const floating = document.createElement("div");
        setMeasuredWidth(floating, 400);
        expect(() => acquireOpenClawSidebarLayout(floating)()).not.toThrow();
    });

    it("discovers wrappers attached before the bounded deferred pass", () => {
        const mount = document.createElement("div");
        setMeasuredWidth(mount, 340);
        const dispose = acquireOpenClawSidebarLayout(mount);
        const callback = [...deferred.values()][0];

        const { content, panel } = buildHost();
        content.replaceChildren(mount);
        callback();

        expect(panel.style.width).toBe("560px");
        expect(content.style.width).toBe("560px");
        dispose();
    });

    it("cancels delayed work before exact restoration and remains idempotent", () => {
        const { content, mount, panel } = buildHost();
        panel.style.setProperty("min-width", "17px", "important");
        panel.style.width = "41%";
        panel.style.flexBasis = "calc(41% - 2px)";
        content.style.minWidth = "9px";
        content.style.width = "39%";
        mount.style.minWidth = "3px";
        setMeasuredWidth(panel, 340);
        setMeasuredWidth(content, 340);

        const observedAtCancel = [];
        cancelAnimationFrame.mockImplementation((id) => {
            observedAtCancel.push(panel.style.width);
            cancelled.push(id);
            deferred.delete(id);
        });
        const dispose = acquireOpenClawSidebarLayout(mount);
        const staleCallback = [...deferred.values()][0];

        dispose();
        dispose();
        staleCallback();

        expect(observedAtCancel).toEqual(["560px"]);
        expect(panel.style.minWidth).toBe("17px");
        expect(panel.style.getPropertyPriority("min-width")).toBe("important");
        expect(panel.style.width).toBe("41%");
        expect(panel.style.flexBasis).toBe("calc(41% - 2px)");
        expect(content.style.minWidth).toBe("9px");
        expect(content.style.width).toBe("39%");
        expect(mount.style.minWidth).toBe("3px");
        expect(cancelled).toHaveLength(1);
    });

    it("restores exact styles over ten lifecycle cycles", () => {
        const { content, mount, panel } = buildHost();
        panel.style.cssText = "color: red; min-width: 12px; width: 45%; flex-basis: 45%";
        content.style.cssText = "width: 44%; min-width: 8px";
        mount.style.cssText = "height: 100%; min-width: 4px";
        const originals = [panel.style.cssText, content.style.cssText, mount.style.cssText];

        for (let cycle = 0; cycle < 10; cycle += 1) {
            const dispose = acquireOpenClawSidebarLayout(mount);
            dispose();
            expect([panel.style.cssText, content.style.cssText, mount.style.cssText]).toEqual(originals);
        }
    });

    it("keeps a width the host pins while mounted and releases only OpenClaw's own writes", () => {
        const { content, mount, panel } = buildHost();
        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(panel.style.flexBasis).toBe("560px");

        // The host splitter sizer pins the dragged width on resize end.
        panel.style.flexBasis = "700px";
        panel.style.flexGrow = "0";
        panel.style.flexShrink = "0";
        dispose();

        expect(panel.style.flexBasis).toBe("700px");
        expect(panel.style.flexGrow).toBe("0");
        expect(panel.style.width).toBe("");
        expect(panel.style.minWidth).toBe("");
        expect(content.style.width).toBe("");
        expect(content.style.minWidth).toBe("");
        expect(mount.style.minWidth).toBe("");
    });

    it("never rewrites a property it did not write when releasing an above-floor panel", () => {
        const { mount, panel } = buildHost({ panelWidth: 720, contentWidth: 720, mountWidth: 720 });
        panel.style.flexBasis = "600px";
        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(panel.style.flexBasis).toBe("600px");

        panel.style.flexBasis = "700px";
        dispose();

        expect(panel.style.flexBasis).toBe("700px");
        expect(panel.style.minWidth).toBe("");
    });

    it("keeps a host min-width rewrite together with its priority", () => {
        const { mount, panel } = buildHost();
        panel.style.setProperty("min-width", "12px", "important");
        const dispose = acquireOpenClawSidebarLayout(mount);
        panel.style.setProperty("min-width", "600px", "important");
        dispose();

        expect(panel.style.minWidth).toBe("600px");
        expect(panel.style.getPropertyPriority("min-width")).toBe("important");
        expect(panel.style.flexBasis).toBe("");
        expect(panel.style.width).toBe("");
    });

    it.each([
        ["left", '<div id="first-side-panel" class="side-bar-panel"><div class="sidebar-content-container"><div id="mount"></div></div></div><div id="last-side-panel"></div>'],
        ["right", '<div id="first-side-panel"></div><div id="last-side-panel" class="side-bar-panel"><div class="sidebar-content-container"><div id="mount"></div></div></div>'],
    ])("applies and releases the floor on a reka-shaped %s sidebar without PrimeVue classes", (_side, markup) => {
        document.body.innerHTML = markup;
        const panel = document.querySelector(".side-bar-panel");
        const content = document.querySelector(".sidebar-content-container");
        const mount = document.querySelector("#mount");
        setMeasuredWidth(panel, 320);
        setMeasuredWidth(content, 320);
        setMeasuredWidth(mount, 320);
        expect(document.querySelector(".p-splitterpanel")).toBeNull();

        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(panel.style.minWidth).toBe("560px");
        expect(panel.style.flexBasis).toBe("560px");
        expect(content.style.width).toBe("560px");
        dispose();

        expect([panel.style.cssText, content.style.cssText, mount.style.cssText]).toEqual(["", "", ""]);
    });

    it("leaves complete native percentage allocation untouched throughout mount and release", () => {
        const { content, mount, panel } = buildHost({ panelWidth: 312 });
        panel.setAttribute("data-panel", "");
        panel.setAttribute("data-panel-id", "sidebar");
        panel.setAttribute("data-panel-group-id", "canvas");
        panel.style.cssText = "flex: 28 1 0px; overflow: hidden";
        const originals = [panel, content, mount].map((node) => node.style.cssText);
        const writes = [panel, content, mount].map((node) => vi.spyOn(node.style, "setProperty"));
        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(mount.getAttribute("data-openclaw-layout")).toBe("host");
        expect([panel, content, mount].map((node) => node.style.cssText)).toEqual(originals);
        expect(deferred.size).toBe(0);
        for (const write of writes) expect(write).not.toHaveBeenCalled();
        panel.style.flexGrow = "40";
        dispose();
        dispose();
        expect(panel.style.flexGrow).toBe("40");
        expect(panel.style.flexBasis).toBe("0px");
        expect(mount.hasAttribute("data-openclaw-layout")).toBe(false);
    });

    it.each([null, "", "previous"])("restores the exact previous native marker %s", (previous) => {
        const { mount, panel } = buildHost();
        panel.setAttribute("data-panel", "");
        panel.setAttribute("data-panel-id", "sidebar");
        panel.setAttribute("data-panel-group-id", "canvas");
        if (previous !== null) mount.setAttribute("data-openclaw-layout", previous);
        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(mount.getAttribute("data-openclaw-layout")).toBe("host");
        dispose();
        expect(mount.getAttribute("data-openclaw-layout")).toBe(previous);
    });

    it("preserves a native marker overwritten by the incoming owner", () => {
        const { mount, panel } = buildHost();
        panel.setAttribute("data-panel", "");
        panel.setAttribute("data-panel-id", "sidebar");
        panel.setAttribute("data-panel-group-id", "canvas");
        const dispose = acquireOpenClawSidebarLayout(mount);
        mount.setAttribute("data-openclaw-layout", "next-owner");
        dispose();
        expect(mount.getAttribute("data-openclaw-layout")).toBe("next-owner");
    });

    it.each(["data-panel", "data-panel-id", "data-panel-group-id"])("keeps legacy allocation when %s is absent or empty", (missing) => {
        const { mount, panel } = buildHost();
        panel.setAttribute("data-panel", "");
        panel.setAttribute("data-panel-id", "sidebar");
        panel.setAttribute("data-panel-group-id", "canvas");
        panel.removeAttribute(missing);
        const dispose = acquireOpenClawSidebarLayout(mount);
        expect(panel.style.flexBasis).toBe("560px");
        expect(mount.hasAttribute("data-openclaw-layout")).toBe(false);
        dispose();
        if (missing !== "data-panel") {
            panel.setAttribute(missing, "");
            const release = acquireOpenClawSidebarLayout(mount);
            expect(panel.style.flexBasis).toBe("560px");
            release();
        }
    });

    it("keeps the JavaScript floor aligned with canonical and legacy CSS", () => {
        const webRoot = resolve(process.cwd(), "web");
        const canonical = readFileSync(`${webRoot}/styles/openclaw_core.css`, "utf8");
        const legacy = readFileSync(`${webRoot}/styles/openclaw_legacy_aliases.css`, "utf8");

        expect(canonical).toContain("--openclaw-sidebar-min-width: 560px");
        expect(canonical).toContain("min-width: var(--openclaw-sidebar-min-width)");
        expect(canonical).toContain("box-sizing: border-box");
        expect(legacy).toContain("--moltbot-sidebar-min-width: var(--openclaw-sidebar-min-width, 560px)");
        expect(legacy).toContain("min-width: var(--moltbot-sidebar-min-width)");
        expect(legacy).toContain("box-sizing: border-box");
    });
});
