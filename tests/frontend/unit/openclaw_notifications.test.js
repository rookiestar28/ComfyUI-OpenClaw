import { beforeEach, describe, expect, it } from "vitest";

import { OpenClawNotifications } from "../../../web/openclaw_notifications.js";

describe("OpenClawNotifications", () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it("deduplicates active entries by dedupe key and increments the count", () => {
        let nowValue = Date.parse("2026-03-19T00:00:00Z");
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => nowValue,
        });

        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search failed: search_failed",
            dedupeKey: "model-manager:search",
        });

        nowValue += 1_000;
        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search failed: search_failed",
            dedupeKey: "model-manager:search",
        });

        const entries = store.getEntries();
        expect(entries).toHaveLength(1);
        expect(entries[0].count).toBe(2);
        expect(entries[0].acknowledged_at).toBeNull();
    });

    it("persists acknowledgement and dismissal state in local storage", () => {
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => Date.parse("2026-03-19T00:00:00Z"),
        });

        const entry = store.notify({
            severity: "warning",
            source: "queue-monitor",
            message: "High load: dropped events",
            dedupeKey: "queue-monitor:backpressure",
        });

        store.acknowledge(entry.id);
        store.dismiss(entry.id);

        const reloaded = new OpenClawNotifications({
            storage: localStorage,
            now: () => Date.parse("2026-03-19T00:00:01Z"),
        });

        expect(reloaded.getEntries()).toHaveLength(0);
        expect(reloaded.getEntries({ includeDismissed: true })[0].dismissed_at).not.toBeNull();
        expect(reloaded.getEntries({ includeDismissed: true })[0].acknowledged_at).not.toBeNull();
    });

    it("does not resurrect an identical dismissed notification on repeated auto-refresh", () => {
        let nowValue = Date.parse("2026-03-19T00:00:00Z");
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => nowValue,
        });

        const entry = store.notify({
            severity: "error",
            source: "model-manager",
            message: "search: search_failed",
            dedupeKey: "model-manager:refresh",
        });

        store.dismiss(entry.id);
        expect(store.getEntries()).toHaveLength(0);

        nowValue += 1_000;
        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search: search_failed",
            dedupeKey: "model-manager:refresh",
        });

        expect(store.getEntries()).toHaveLength(0);
        const dismissed = store.getEntries({ includeDismissed: true });
        expect(dismissed).toHaveLength(1);
        expect(dismissed[0].dismissed_at).not.toBeNull();
    });
});
