import { beforeEach, describe, expect, it, vi } from "vitest";

import { OpenClawNotificationCenter } from "../../../web/openclaw_notification_center.js";

function createNotificationsStub() {
    const listeners = new Set();
    const state = {
        entries: [],
        acknowledged: [],
        dismissed: [],
    };
    return {
        state,
        subscribe(listener) {
            listeners.add(listener);
            listener(state.entries);
            return () => listeners.delete(listener);
        },
        push(entries) {
            state.entries = entries;
            listeners.forEach((listener) => listener(entries));
        },
        acknowledge(id) {
            state.acknowledged.push(id);
        },
        dismiss(id) {
            state.dismissed.push(id);
        },
    };
}

describe("openclaw_notification_center", () => {
    beforeEach(() => {
        document.body.innerHTML = "";
    });

    it("renders unread badge and routes dismiss/open actions", () => {
        const notifications = createNotificationsStub();
        const onAction = vi.fn();
        const center = new OpenClawNotificationCenter({ notifications, onAction });
        document.body.appendChild(center.buildToggle());
        document.body.appendChild(center.buildPanel());

        notifications.push([
            {
                id: "ntf-1",
                source: "model-manager",
                severity: "error",
                message: "search: search_failed",
                updated_at: "2026-03-20T00:00:00Z",
                count: 1,
                acknowledged_at: null,
                dismissed_at: null,
                action: {
                    label: "Open Model Manager",
                    type: "tab",
                    payload: "model-manager",
                },
            },
        ]);

        center.toggle();
        expect(document.querySelector(".openclaw-notification-badge").textContent).toBe("1");

        document.querySelector('[data-notification-action="open"]').click();
        expect(notifications.state.acknowledged).toEqual(["ntf-1"]);
        expect(onAction).toHaveBeenCalledWith(
            expect.objectContaining({ payload: "model-manager", type: "tab" })
        );

        document.querySelector('[data-notification-action="dismiss"]').click();
        expect(notifications.state.dismissed).toEqual(["ntf-1"]);
    });

    it("escapes notification message content in rendered HTML", () => {
        const notifications = createNotificationsStub();
        const center = new OpenClawNotificationCenter({ notifications });
        document.body.appendChild(center.buildToggle());
        document.body.appendChild(center.buildPanel());

        notifications.push([
            {
                id: "ntf-escape",
                source: "<source>",
                severity: "warning",
                message: '<img src=x onerror="boom">',
                updated_at: "2026-03-20T00:00:00Z",
                count: 2,
                acknowledged_at: null,
                dismissed_at: null,
                action: null,
            },
        ]);

        center.toggle();
        const messageNode = document.querySelector(".openclaw-notification-message");
        expect(messageNode.innerHTML).not.toContain("<img");
        expect(messageNode.textContent).toContain('<img src=x onerror="boom">');
    });
});
