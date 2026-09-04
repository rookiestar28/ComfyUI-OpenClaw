import { describe, expect, it, vi } from "vitest";

import { watchOpenClawSidebarOwnership } from "../../openclaw_sidebar_ownership.js";

class FakeMutationObserver {
    static instances = [];

    constructor(callback) {
        this.callback = callback;
        this.disconnect = vi.fn();
        this.observe = vi.fn();
        FakeMutationObserver.instances.push(this);
    }
}

function buildOwnedMount() {
    const mount = document.createElement("div");
    const root = document.createElement("div");
    root.className = "openclaw-sidebar-container";
    mount.appendChild(root);
    return { mount, root };
}

describe("OpenClaw shared sidebar ownership", () => {
    it("ignores descendant mutations while the exact root remains exclusive", () => {
        FakeMutationObserver.instances = [];
        const { mount, root } = buildOwnedMount();
        const lost = vi.fn();
        const disconnect = watchOpenClawSidebarOwnership(mount, root, lost, {
            MutationObserverClass: FakeMutationObserver,
        });
        const observer = FakeMutationObserver.instances[0];

        root.appendChild(document.createElement("span"));
        observer.callback([]);

        expect(observer.observe).toHaveBeenCalledWith(mount, { childList: true });
        expect(lost).not.toHaveBeenCalled();
        disconnect();
        expect(observer.disconnect).toHaveBeenCalledTimes(1);
    });

    it("disconnects before notifying once when the exact root is replaced", () => {
        FakeMutationObserver.instances = [];
        const { mount, root } = buildOwnedMount();
        const events = [];
        const foreign = document.createElement("section");
        foreign.textContent = "foreign-state";
        watchOpenClawSidebarOwnership(
            mount,
            root,
            () => events.push("lost"),
            { MutationObserverClass: FakeMutationObserver }
        );
        const observer = FakeMutationObserver.instances[0];
        observer.disconnect.mockImplementation(() => events.push("disconnect"));

        mount.replaceChildren(foreign);
        observer.callback([]);
        observer.callback([]);

        expect(events).toEqual(["disconnect", "lost"]);
        expect(mount.firstChild).toBe(foreign);
        expect(foreign.textContent).toBe("foreign-state");
    });

    it("releases on a foreign direct sibling without modifying either root", () => {
        FakeMutationObserver.instances = [];
        const { mount, root } = buildOwnedMount();
        const foreign = document.createElement("aside");
        const lost = vi.fn();
        watchOpenClawSidebarOwnership(mount, root, lost, {
            MutationObserverClass: FakeMutationObserver,
        });

        mount.appendChild(foreign);
        FakeMutationObserver.instances[0].callback([]);

        expect(lost).toHaveBeenCalledTimes(1);
        expect([...mount.children]).toEqual([root, foreign]);
    });

    it("degrades to explicit lifecycle cleanup when MutationObserver is unavailable", () => {
        const { mount, root } = buildOwnedMount();
        const lost = vi.fn();
        const disconnect = watchOpenClawSidebarOwnership(mount, root, lost, {
            MutationObserverClass: null,
        });

        mount.replaceChildren(document.createElement("div"));
        expect(() => disconnect()).not.toThrow();
        expect(lost).not.toHaveBeenCalled();
    });
});
