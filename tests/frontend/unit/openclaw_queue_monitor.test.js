import { describe, expect, it, vi } from "vitest";

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: {
        getHealth: vi.fn(),
        subscribeEvents: vi.fn(),
    },
}));

const { QueueMonitor } = await import("../../../web/openclaw_queue_monitor.js");

describe("QueueMonitor", () => {
    it("deduplicates repeated status banners within the ttl window", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 1000;
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => nowValue,
            setIntervalRef: vi.fn(),
        });

        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 2000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 7000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);

        expect(ui.showBanner).toHaveBeenCalledTimes(2);
        expect(ui.showBanner.mock.calls[0][0].severity).toBe("info");
    });

    it("reconnects the event stream when health checks recover from a disconnect", async () => {
        const ui = { showBanner: vi.fn() };
        const closedStream = { readyState: 2, close: vi.fn() };
        const subscribeEvents = vi.fn(() => ({ readyState: 1, close: vi.fn() }));
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents,
            },
            setIntervalRef: vi.fn(),
        });

        monitor.isConnected = false;
        monitor.es = closedStream;

        await monitor.checkHealth();

        expect(subscribeEvents).toHaveBeenCalledTimes(1);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_restored",
                severity: "success",
            })
        );
    });

    it("does not alert immediately for the first startup disconnect", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).not.toHaveBeenCalled();
        expect(monitor.isConnected).toBe(false);
    });

    it("alerts after sustained startup disconnect failures cross the grace threshold", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 0;
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => nowValue,
            setIntervalRef: vi.fn(),
            startupGraceMs: 1000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 500;
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 1500;
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("alerts immediately once a previously healthy backend disconnects", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents: vi.fn(),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        await monitor.checkHealth();
        ui.showBanner.mockClear();

        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("emits persistent failed-job notifications with a job-monitor jump action", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => 1000,
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({
            event_type: "failed",
            prompt_id: "prompt-12345678",
        });

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                severity: "error",
                persist: true,
                action: expect.objectContaining({
                    type: "tab",
                    payload: "job-monitor",
                }),
            })
        );
    });

    it("clears stale active prompt ids after reconnect when the queue snapshot no longer lists them", async () => {
        const ui = { showBanner: vi.fn() };
        const closedStream = { readyState: 2, close: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                getPromptQueue: vi.fn().mockResolvedValue({
                    ok: true,
                    data: {
                        queue_running: [],
                        queue_pending: [[1, "still-active"]],
                    },
                }),
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({ event_type: "running", prompt_id: "stale-job-1" });
        monitor.handleEvent({ event_type: "queued", prompt_id: "still-active" });
        monitor.isConnected = false;
        monitor.es = closedStream;

        await monitor.checkHealth();

        expect(monitor.activePromptIds.has("stale-job-1")).toBe(false);
        expect(monitor.activePromptIds.has("still-active")).toBe(true);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "job_reconnect_cleared_stale-jo",
                severity: "info",
            })
        );
    });

    it("preserves active prompt ids when reconnect queue refresh fails", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                getPromptQueue: vi.fn().mockResolvedValue({
                    ok: false,
                    error: "queue_unavailable",
                }),
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({ event_type: "running", prompt_id: "active-job" });
        monitor.isConnected = false;

        await monitor.checkHealth();

        expect(monitor.activePromptIds.has("active-job")).toBe(true);
        expect(ui.showBanner).not.toHaveBeenCalledWith(
            expect.objectContaining({
                id: expect.stringMatching(/^job_reconnect_cleared_/),
            })
        );
    });
});
