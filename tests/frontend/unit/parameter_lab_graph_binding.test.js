import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./support/host_api_stub.js";
import { app } from "./support/host_app_stub.js";

const hostCalls = vi.hoisted(() => ({ fetch: null, banners: [] }));

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: {
        _path: (path) => path,
        fetch: (path, options) => hostCalls.fetch(path, options),
    },
}));
vi.mock("../../../web/openclaw_ui.js", () => ({
    openclawUI: { showBanner: (kind, text) => hostCalls.banners.push({ kind, text }) },
}));

import { PARAMETER_LAB_RECEIPT_KEY } from "../../../web/openclaw_parameter_lab_receipt.js";
import { ParameterLabTab } from "../../../web/tabs/parameter_lab_tab.js";

const SWITCH_MESSAGE = "The workflow changed. Return to the original workflow or rebuild the plan.";
const PLAN_SWITCH_MESSAGE =
    "The workflow changed while the plan was being built. Rebuild the plan from the current workflow.";

function makeGraph(id, seed) {
    return {
        id,
        _nodes: [
            {
                id: 10,
                type: "KSampler",
                title: "Sampler",
                widgets: [{ name: "seed", type: "number", value: seed, options: {} }],
            },
        ],
        serialize() {
            const data = { nodes: [], extra: {} };
            this.onSerialize?.(data);
            return data;
        },
    };
}

function collectWidgets(graph) {
    return graph._nodes.flatMap((node) => node.widgets || []);
}

// A host either keeps one graph object per workflow (the active object is replaced) or reconfigures
// one shared graph object with the other workflow (only its id and nodes change).
function installHost(model) {
    const bus = new EventTarget();
    Object.assign(api, {
        addEventListener: bus.addEventListener.bind(bus),
        removeEventListener: bus.removeEventListener.bind(bus),
        dispatchCustomEvent: (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail })),
    });

    const first = makeGraph("workflow-a", 100);
    const second = makeGraph("workflow-b", 200);
    const shared = makeGraph("workflow-a", 100);
    shared._nodes = first._nodes;
    const definitions = {
        first: { id: "workflow-a", nodes: first._nodes },
        second: { id: "workflow-b", nodes: second._nodes },
    };
    const host = {
        model,
        first,
        second,
        shared,
        queueCalls: 0,
        sweepRequests: 0,
        runUpdates: [],
        afterFirstQueue: null,
        inPreamble: null,
        duringPlanRequest: null,
        activeGraph: () => app.rootGraph ?? app.graph,
        activate(which) {
            if (model === "per-workflow") {
                const next = which === "first" ? first : second;
                app.graph = next;
                app.rootGraph = next;
                return;
            }
            shared.id = definitions[which].id;
            shared._nodes = definitions[which].nodes;
            app.graph = shared;
            app.rootGraph = shared;
        },
        seed: (which) => definitions[which].nodes[0].widgets[0].value,
    };
    host.activate("first");

    Object.assign(app, {
        processingQueue: false,
        queueItems: [],
        nextRequestId: 1,
        async queuePrompt(number, batchCount = 1) {
            host.queueCalls += 1;
            const requestId = this.nextRequestId++;
            this.queueItems.push({ requestId, number, batchCount });
            api.dispatchCustomEvent("promptQueueing", { requestId, batchCount });
            if (this.processingQueue) return false;

            this.processingQueue = true;
            // This host collects its widget hooks when the queue call starts, before its async
            // preamble; that is the situation in which a tab switch can still reach beforeQueued.
            const hooked = collectWidgets(host.activeGraph());
            await Promise.resolve();
            host.inPreamble?.();
            try {
                while (this.queueItems.length) {
                    const request = this.queueItems.pop();
                    let queuedCount = 0;
                    for (let index = 0; index < request.batchCount; index += 1) {
                        hooked.forEach((widget) => widget.beforeQueued?.({ isPartialExecution: false }));
                        const workflow = host.activeGraph().serialize();
                        if (!workflow.extra?.[PARAMETER_LAB_RECEIPT_KEY]?.prompt_id) {
                            throw new Error("missing receipt marker");
                        }
                        hooked.forEach((widget) => widget.afterQueued?.({ isPartialExecution: false }));
                        queuedCount += 1;
                    }
                    api.dispatchCustomEvent("promptQueued", {
                        requestId: request.requestId,
                        batchCount: queuedCount,
                        number: request.number,
                    });
                }
            } finally {
                this.processingQueue = false;
            }
            if (host.afterFirstQueue) {
                const callback = host.afterFirstQueue;
                host.afterFirstQueue = null;
                callback();
            }
            return true;
        },
    });

    hostCalls.fetch = async (path, options = {}) => {
        if (path.endsWith("/lab/sweep")) {
            host.sweepRequests += 1;
            host.duringPlanRequest?.();
            return {
                ok: true,
                status: 200,
                data: {
                    plan: {
                        experiment_id: "exp_binding",
                        dimensions: [{ node_id: 10, widget_name: "seed", values: [111, 222], strategy: "grid" }],
                        runs: [{ "10.seed": 111 }, { "10.seed": 222 }],
                    },
                },
            };
        }
        if (path.includes("/lab/experiments/exp_binding/runs/")) {
            host.runUpdates.push({
                path: path.split("/runs/")[1],
                body: JSON.parse(options.body || "{}"),
            });
            return { ok: true, status: 200, data: {} };
        }
        return { ok: false, status: 404 };
    };
    return host;
}

function mountTab() {
    ParameterLabTab.dispose();
    Object.assign(ParameterLabTab, {
        dimensions: [],
        plan: null,
        experimentId: null,
        results: [],
        isRunning: false,
        _graphBinding: null,
    });
    const container = document.createElement("div");
    document.body.replaceChildren(container);
    ParameterLabTab.render(container);
    ParameterLabTab.dimensions = [{ node_id: 10, widget_name: "seed", values: [111, 222], strategy: "grid" }];
}

async function runSweep() {
    vi.useFakeTimers();
    try {
        const done = ParameterLabTab.runExperiment();
        await vi.runAllTimersAsync();
        await done;
    } finally {
        vi.useRealTimers();
    }
}

const runStatuses = () =>
    [...ParameterLabTab.resultsContainer.querySelectorAll(".run-status")].map((span) => span.textContent);
const lastBanner = () => hostCalls.banners.at(-1);

describe.each(["per-workflow", "shared-graph"])("Parameter Lab workflow binding on a %s host", (model) => {
    let host;

    beforeEach(() => {
        hostCalls.banners.length = 0;
        host = installHost(model);
        vi.stubGlobal("confirm", () => true);
        mountTab();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
        ParameterLabTab.dispose();
        for (const key of Object.keys(app)) delete app[key];
        for (const key of Object.keys(api)) delete api[key];
        document.body.replaceChildren();
    });

    it("runs a whole sweep against the originating workflow when nothing switches", async () => {
        await ParameterLabTab.generatePlan();
        expect(ParameterLabTab.plan.runs).toHaveLength(2);
        await runSweep();

        expect(host.queueCalls).toBe(2);
        expect([host.seed("first"), host.seed("second")]).toEqual([222, 200]);
        expect(runStatuses().every((text) => text.startsWith("Queued"))).toBe(true);
        expect(lastBanner()).toEqual({ kind: "success", text: "All runs queued. Monitoring progress..." });
    });

    it("stops a sweep that loses its workflow without writing to or queueing the other one", async () => {
        await ParameterLabTab.generatePlan();
        host.afterFirstQueue = () => host.activate("second");
        await runSweep();

        expect(host.queueCalls).toBe(1);
        expect([host.seed("first"), host.seed("second")]).toEqual([111, 200]);
        expect(runStatuses()[1]).toBe("Workflow changed");
        expect(host.runUpdates).toEqual([
            { path: "0", body: expect.objectContaining({ status: "queued" }) },
            { path: "1", body: { status: "failed" } },
        ]);
        expect(lastBanner()).toEqual({ kind: "error", text: SWITCH_MESSAGE });
    });

    it("fails the run and stops when the workflow changes inside the host queue preamble", async () => {
        await ParameterLabTab.generatePlan();
        const secondSerializer = host.second.onSerialize;
        host.inPreamble = () => host.activate("second");
        await runSweep();

        expect(host.queueCalls).toBe(1);
        expect([host.seed("first"), host.seed("second")]).toEqual([111, 200]);
        expect(host.second.onSerialize).toBe(secondSerializer);
        expect(runStatuses()[0]).toBe("Workflow changed");
        expect(host.runUpdates).toEqual([{ path: "0", body: { status: "failed" } }]);
        expect(lastBanner()).toEqual({ kind: "error", text: SWITCH_MESSAGE });
    });

    it("refuses a replay after a switch with zero writes and allows it again after returning", async () => {
        await ParameterLabTab.generatePlan();
        host.activate("second");
        ParameterLabTab.replayRun(ParameterLabTab.plan.runs[0]);
        expect([host.seed("first"), host.seed("second")]).toEqual([100, 200]);
        expect(lastBanner()).toEqual({ kind: "error", text: SWITCH_MESSAGE });

        host.activate("first");
        ParameterLabTab.replayRun(ParameterLabTab.plan.runs[0]);
        expect([host.seed("first"), host.seed("second")]).toEqual([111, 200]);
        expect(lastBanner()).toEqual({ kind: "success", text: "Values applied to nodes." });
    });

    it("discards a plan whose generation was interrupted by a switch", async () => {
        host.duringPlanRequest = () => host.activate("second");
        await ParameterLabTab.generatePlan();

        expect(host.sweepRequests).toBe(1);
        expect(ParameterLabTab.plan).toBeNull();
        expect(ParameterLabTab.resultsContainer.querySelector("#lab-run-all")).toBeNull();
        expect(lastBanner()).toEqual({ kind: "error", text: PLAN_SWITCH_MESSAGE });
    });

    it("binds a loaded experiment to the workflow that is active when it loads", async () => {
        hostCalls.fetch = async (path) =>
            path.endsWith("/lab/experiments/exp_loaded")
                ? {
                      ok: true,
                      status: 200,
                      data: {
                          experiment: {
                              experiment_id: "exp_binding",
                              dimensions: [{ node_id: 10, widget_name: "seed", values: [111], strategy: "grid" }],
                              runs: [{ "10.seed": 111 }],
                          },
                      },
                  }
                : { ok: false, status: 404 };
        await ParameterLabTab.loadExperiment("exp_loaded");
        expect(ParameterLabTab.plan.runs).toHaveLength(1);

        host.activate("second");
        ParameterLabTab.replayRun(ParameterLabTab.plan.runs[0]);
        expect([host.seed("first"), host.seed("second")]).toEqual([100, 200]);
        expect(lastBanner()).toEqual({ kind: "error", text: SWITCH_MESSAGE });

        host.activate("first");
        ParameterLabTab.replayRun(ParameterLabTab.plan.runs[0]);
        expect([host.seed("first"), host.seed("second")]).toEqual([111, 200]);
    });

    it("builds the plan from the graph it is bound to, not from the deprecated graph alias", async () => {
        // The alias can point at the graph being edited (for example a subgraph) while the
        // binding is taken from the root graph; the plan must be serialized from the latter.
        const decoy = makeGraph("decoy", 999);
        let decoySerializations = 0;
        decoy.serialize = () => {
            decoySerializations += 1;
            return { nodes: [], extra: {} };
        };
        app.graph = decoy;

        await ParameterLabTab.generatePlan();

        expect(decoySerializations).toBe(0);
        expect(ParameterLabTab.plan.runs).toHaveLength(2);
        expect(host.sweepRequests).toBe(1);
    });

    it("refuses to build a plan, with no request, when the host has no active workflow graph", async () => {
        app.graph = undefined;
        app.rootGraph = undefined;

        await ParameterLabTab.generatePlan();

        expect(host.sweepRequests).toBe(0);
        expect(ParameterLabTab.plan).toBeNull();
        expect(lastBanner()).toEqual({
            kind: "error",
            text: "Parameter Lab validation failed: invalid_payload",
        });
    });
});
