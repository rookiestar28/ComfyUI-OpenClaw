import { beforeEach, describe, expect, it, vi } from "vitest";
import { TabManager } from "../../../web/openclaw_tabs.js";

function makeMount() {
    const tabsEl = document.createElement("div");
    const contentEl = document.createElement("div");
    document.body.appendChild(tabsEl);
    document.body.appendChild(contentEl);
    return { tabsEl, contentEl };
}

describe("TabManager", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
        localStorage.clear();
    });

    it("renders canonical tab buttons and creates matching panes", () => {
        const manager = new TabManager();
        const { tabsEl, contentEl } = makeMount();

        manager.registerTab({
            id: "settings",
            title: "Settings",
            icon: "openclaw-icon-settings",
            render(pane) {
                pane.textContent = "Settings loaded";
            },
        });
        manager.init(tabsEl, contentEl);

        const button = tabsEl.querySelector(".openclaw-tab");
        expect(button.querySelector(".openclaw-tab-icon").className).toContain(
            "openclaw-icon-settings"
        );
        expect(button.querySelector(".openclaw-tab-label").textContent).toBe("Settings");

        const pane = contentEl.querySelector("#openclaw-tab-settings");
        expect(pane.className).toContain("openclaw-tab-pane");
        expect(pane.classList.contains("active")).toBe(true);
        expect(pane.textContent).toBe("Settings loaded");
    });

    it("switches active state without duplicating panes", () => {
        const manager = new TabManager();
        const { tabsEl, contentEl } = makeMount();

        manager.registerTab({
            id: "settings",
            title: "Settings",
            render(pane) {
                pane.textContent = "Settings loaded";
            },
        });
        manager.registerTab({
            id: "jobs",
            title: "Jobs",
            render(pane) {
                pane.textContent = "Jobs loaded";
            },
        });
        manager.init(tabsEl, contentEl);
        manager.activateTab("jobs");

        expect(contentEl.querySelectorAll(".openclaw-tab-pane")).toHaveLength(2);
        expect(contentEl.querySelector("#openclaw-tab-settings").classList.contains("active")).toBe(false);
        expect(contentEl.querySelector("#openclaw-tab-jobs").classList.contains("active")).toBe(true);
        expect(contentEl.querySelector("#openclaw-tab-jobs").textContent).toBe("Jobs loaded");
    });

    it("disposes pending work before switching and rerenders when revisited", () => {
        const manager = new TabManager();
        const { tabsEl, contentEl } = makeMount();
        const render = vi.fn(pane => { pane.textContent = "pending"; });
        const dispose = vi.fn(() => true);
        manager.registerTab({ id: "settings", title: "Settings", render, dispose });
        manager.registerTab({ id: "jobs", title: "Jobs", render: () => {} });
        manager.init(tabsEl, contentEl);

        manager.activateTab("jobs");
        expect(dispose).toHaveBeenCalledWith(contentEl.querySelector("#openclaw-tab-settings"));
        expect(contentEl.querySelector("#openclaw-tab-settings").hasChildNodes()).toBe(false);

        manager.activateTab("settings");
        expect(render).toHaveBeenCalledTimes(2);
    });

    it("routes async render failures through the tab error boundary", async () => {
        const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
        const manager = new TabManager();
        const { tabsEl, contentEl } = makeMount();

        manager.registerTab({
            id: "broken",
            title: "Broken",
            render() {
                return Promise.reject(new Error("async tab failed"));
            },
        });
        manager.init(tabsEl, contentEl);
        await Promise.resolve();

        const pane = contentEl.querySelector("#openclaw-tab-broken");
        expect(pane.querySelector(".openclaw-error-boundary code").textContent).toContain(
            "async tab failed"
        );
        consoleSpy.mockRestore();
    });
});
