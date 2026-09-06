import { beforeEach, describe, expect, it, vi } from "vitest";

import { OpenClawBannerManager } from "../../../web/openclaw_banner_manager.js";

describe("openclaw_banner_manager", () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <div class="openclaw-sidebar-container">
                <div class="openclaw-header"></div>
            </div>
        `;
    });

    it("persists warning/error banners and routes action callbacks", () => {
        const notifications = { notify: vi.fn() };
        const onAction = vi.fn();
        const manager = new OpenClawBannerManager({ notifications, onAction });
        const container = document.querySelector(".openclaw-sidebar-container");
        manager.bind(container, onAction);

        manager.showBanner({
            id: "bg-1",
            severity: "error",
            message: "Backend disconnected",
            ttl_ms: 0,
            action: {
                label: "Open Jobs",
                type: "tab",
                payload: "job-monitor",
            },
        });

        document.querySelector(".openclaw-banner-action").click();
        expect(onAction).toHaveBeenCalledWith(
            expect.objectContaining({ payload: "job-monitor", type: "tab" })
        );
        expect(notifications.notify).toHaveBeenCalledWith(
            expect.objectContaining({
                severity: "error",
                message: "Backend disconnected",
            })
        );
    });

    it("suppresses lower-priority banners while an error banner is active", () => {
        const notifications = { notify: vi.fn() };
        const manager = new OpenClawBannerManager({ notifications });
        const container = document.querySelector(".openclaw-sidebar-container");
        manager.bind(container);

        manager.showBanner({
            id: "err-1",
            severity: "error",
            message: "Critical",
            ttl_ms: 0,
        });
        manager.showBanner({
            id: "warn-1",
            severity: "warning",
            message: "Should be ignored",
            ttl_ms: 0,
        });

        expect(document.querySelector(".openclaw-banner").textContent).toContain("Critical");
        expect(document.querySelector(".openclaw-banner").textContent).not.toContain("Should be ignored");
    });
});
