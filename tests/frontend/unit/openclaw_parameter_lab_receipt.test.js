import { describe, expect, it, vi } from "vitest";

import {
  PARAMETER_LAB_RECEIPT_KEY,
  createParameterLabReceiptCoordinator,
} from "../../../web/openclaw_parameter_lab_receipt.js";
import { captureGraphBinding } from "../../../web/openclaw_graph_host.js";

class FakeApi extends EventTarget {
  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

function createFakeGraph(id = "workflow-a") {
  return {
    id,
    extra: { preserved: true },
    onSerialize: vi.fn(),
    serialize() {
      const data = { nodes: [], extra: { ...this.extra } };
      this.onSerialize?.(data);
      return data;
    },
  };
}

function createHostFixture({ updateUi = () => Promise.resolve() } = {}) {
  const api = new FakeApi();
  const submissions = [];
  const beforeQueuedSpy = vi.fn();
  const afterQueuedSpy = vi.fn();
  const widget = {
    beforeQueued: beforeQueuedSpy,
    afterQueued: afterQueuedSpy,
  };
  const graph = createFakeGraph();
  const hooks = { afterPreamble: null };
  let nextRequestId = 1;
  let nextForeignId = 1;
  const app = {
    graph,
    rootGraph: graph,
    processingQueue: false,
    queueItems: [],
    async queuePrompt(number, batchCount = 1) {
      const requestId = nextRequestId++;
      this.queueItems.push({ requestId, number, batchCount });
      api.emit("promptQueueing", { requestId, batchCount });
      if (this.processingQueue) return false;

      this.processingQueue = true;
      await Promise.resolve();
      // Models the host's async preamble, during which the user can switch workflow tabs.
      hooks.afterPreamble?.();
      try {
        while (this.queueItems.length) {
          const request = this.queueItems.pop();
          let queuedCount = 0;
          for (let index = 0; index < request.batchCount; index += 1) {
            widget.beforeQueued?.({ isPartialExecution: false });
            const workflow = (this.rootGraph ?? this.graph).serialize();
            const marker = workflow.extra?.[PARAMETER_LAB_RECEIPT_KEY];
            const promptId = marker?.prompt_id || `foreign-${nextForeignId++}`;
            submissions.push({
              requestId: request.requestId,
              promptId,
              marker: marker || null,
            });
            widget.afterQueued?.({ isPartialExecution: false });
            void updateUi();
            queuedCount += 1;
          }
          api.emit("promptQueued", {
            requestId: request.requestId,
            batchCount: queuedCount,
            number: request.number,
          });
        }
      } finally {
        this.processingQueue = false;
      }
      return true;
    },
  };
  function switchWorkflow(next) {
    app.graph = next;
    app.rootGraph = next;
  }
  return {
    api,
    app,
    graph,
    hooks,
    switchWorkflow,
    submissions,
    widget,
    beforeQueuedSpy,
    afterQueuedSpy,
  };
}

describe("Parameter Lab authoritative queue receipt", () => {
  it("settles the matching native receipt while a detached host queue UI refresh remains pending", async () => {
    let uiSettled = false;
    let finishUi;
    const uiRefresh = new Promise((resolve) => { finishUi = resolve; })
      .then(() => { uiSettled = true; });
    const updateUi = vi.fn(() => uiRefresh);
    const fixture = createHostFixture({ updateUi });
    const id = "44444444-4444-4444-8444-444444444444";
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app, api: fixture.api, uuidFactory: () => id, timeoutMs: 1000,
    });
    const receipt = await coordinator.queue({ experimentId: "exp-delayed-ui", runId: "0", widget: fixture.widget, binding: captureGraphBinding(fixture.app) });
    expect(receipt.promptId).toBe(id);
    expect(fixture.submissions).toHaveLength(1);
    expect(fixture.beforeQueuedSpy).toHaveBeenCalledTimes(1);
    expect(fixture.afterQueuedSpy).toHaveBeenCalledTimes(1);
    expect(updateUi).toHaveBeenCalledTimes(1);
    expect(uiSettled).toBe(false);
    receipt.release();
    coordinator.dispose();
    finishUi();
    await uiRefresh;
  });

  it("correlates two overlapping runs across LIFO host work without tagging unrelated submission", async () => {
    const fixture = createHostFixture();
    const ids = [
      "11111111-1111-4111-8111-111111111111",
      "22222222-2222-4222-8222-222222222222",
    ];
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => ids.shift(),
      timeoutMs: 1_000,
    });

    const first = coordinator.queue({
      experimentId: "exp-one",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const second = coordinator.queue({
      experimentId: "exp-two",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const unrelated = fixture.app.queuePrompt(0, 1);

    const [firstReceipt, secondReceipt] = await Promise.all([first, second]);
    await unrelated;

    expect(firstReceipt.promptId).toBe("11111111-1111-4111-8111-111111111111");
    expect(secondReceipt.promptId).toBe("22222222-2222-4222-8222-222222222222");
    expect(fixture.submissions).toEqual([
      expect.objectContaining({ marker: null }),
      expect.objectContaining({
        promptId: "22222222-2222-4222-8222-222222222222",
        marker: {
          version: 1,
          prompt_id: "22222222-2222-4222-8222-222222222222",
        },
      }),
      expect.objectContaining({
        promptId: "11111111-1111-4111-8111-111111111111",
        marker: {
          version: 1,
          prompt_id: "11111111-1111-4111-8111-111111111111",
        },
      }),
    ]);
    expect(fixture.graph.extra).toEqual({ preserved: true });
    expect(fixture.graph.onSerialize).toHaveBeenCalledTimes(3);
    expect(fixture.beforeQueuedSpy).toHaveBeenCalledTimes(3);
    expect(fixture.afterQueuedSpy).toHaveBeenCalledTimes(3);
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 2,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: true,
    });

    firstReceipt.release();
    secondReceipt.release();
    coordinator.dispose();
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 0,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: false,
    });
  });

  it("routes only exact native lifecycle IDs and disposes terminal and late events", async () => {
    const fixture = createHostFixture();
    const promptId = "33333333-3333-4333-8333-333333333333";
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => promptId,
      timeoutMs: 1_000,
    });
    const receipt = await coordinator.queue({
      experimentId: "exp-life",
      runId: "7",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const observed = [];
    receipt.subscribeLifecycle((event) => observed.push(event.type));

    fixture.api.emit("execution_start", { prompt_id: "unrelated" });
    fixture.api.emit("execution_start", {
      prompt_id: promptId,
      private_node_value: "must-not-cross-receipt-boundary",
    });
    fixture.api.emit("execution_success", { prompt_id: promptId });
    fixture.api.emit("execution_error", { prompt_id: promptId });

    expect(observed).toEqual(["execution_start", "execution_success"]);
    expect(coordinator.debugSnapshot().lifecycleSubscriptions).toBe(0);
    expect(coordinator.debugSnapshot().activeAttempts).toBe(0);
    coordinator.dispose();
  });

  it("fails closed and releases ownership when the lifecycle consumer throws", async () => {
    const fixture = createHostFixture();
    const promptId = "35353535-3535-4535-8535-353535353535";
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => promptId,
      timeoutMs: 1_000,
    });
    const receipt = await coordinator.queue({
      experimentId: "exp-consumer-failure",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    receipt.subscribeLifecycle(() => {
      throw new Error("private lifecycle consumer detail");
    });

    expect(() =>
      fixture.api.emit("execution_start", { prompt_id: promptId }),
    ).not.toThrow();
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 0,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: false,
    });
    coordinator.dispose();
  });

  it("redacts raw lifecycle detail at the coordinator boundary", async () => {
    const fixture = createHostFixture();
    const promptId = "34343434-3434-4434-8434-343434343434";
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => promptId,
      timeoutMs: 1_000,
    });
    const receipt = await coordinator.queue({
      experimentId: "exp-private",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const observed = [];
    receipt.subscribeLifecycle((event) => observed.push(event));

    fixture.api.emit("execution_error", {
      prompt_id: promptId,
      exception_message: "secret=private-host-detail",
      node_id: "private-node",
    });

    expect(observed).toEqual([
      {
        type: "execution_error",
        promptId,
      },
    ]);
    expect(JSON.stringify(observed)).not.toContain("private-host-detail");
    coordinator.dispose();
  });

  it("settles the host owner before a sequential run opens the next queue window", async () => {
    const fixture = createHostFixture();
    const ids = [
      "36363636-3636-4636-8636-363636363636",
      "37373737-3737-4737-8737-373737373737",
    ];
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => ids.shift(),
      timeoutMs: 1_000,
    });

    const first = await coordinator.queue({
      experimentId: "exp-sequential",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    expect(fixture.app.processingQueue).toBe(false);

    const second = await coordinator.queue({
      experimentId: "exp-sequential",
      runId: "1",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });

    expect(first.promptId).toBe("36363636-3636-4636-8636-363636363636");
    expect(second.promptId).toBe("37373737-3737-4737-8737-373737373737");
    first.release();
    second.release();
    coordinator.dispose();
  });

  it("fails closed before enqueueing when the pre-existing host owner was not observed", async () => {
    const fixture = createHostFixture();
    fixture.app.processingQueue = true;
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => "44444444-4444-4444-8444-444444444444",
      timeoutMs: 1_000,
    });

    await expect(
      coordinator.queue({
        experimentId: "exp-busy",
        runId: "0",
        widget: fixture.widget,
        binding: captureGraphBinding(fixture.app),
      }),
    ).rejects.toMatchObject({ code: "host_queue_unobserved_busy" });
    expect(fixture.app.queueItems).toEqual([]);
    expect(fixture.submissions).toEqual([]);
    coordinator.dispose();
  });

  it("restores callbacks and rejects malformed UUID, cancellation, and dispose without leaks", async () => {
    const malformedFixture = createHostFixture();
    const malformed = createParameterLabReceiptCoordinator({
      app: malformedFixture.app,
      api: malformedFixture.api,
      uuidFactory: () => "not-a-uuid",
      timeoutMs: 1_000,
    });
    await expect(
      malformed.queue({
        experimentId: "exp-bad",
        runId: "0",
        widget: malformedFixture.widget,
        binding: captureGraphBinding(malformedFixture.app),
      }),
    ).rejects.toMatchObject({ code: "invalid_receipt_id" });
    malformed.dispose();

    const fixture = createHostFixture();
    const originalBefore = fixture.widget.beforeQueued;
    const originalAfter = fixture.widget.afterQueued;
    const controller = new AbortController();
    controller.abort();
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => "55555555-5555-4555-8555-555555555555",
      timeoutMs: 1_000,
    });
    await expect(
      coordinator.queue({
        experimentId: "exp-cancel",
        runId: "0",
        widget: fixture.widget,
        binding: captureGraphBinding(fixture.app),
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "attempt_cancelled" });
    coordinator.dispose();

    expect(fixture.widget.beforeQueued).toBe(originalBefore);
    expect(fixture.widget.afterQueued).toBe(originalAfter);
    expect(coordinator.debugSnapshot().installed).toBe(false);
  });

  it("buffers an exact fast terminal event until subscription and ignores it after release", async () => {
    const fixture = createHostFixture();
    const promptId = "66666666-6666-4666-8666-666666666666";
    fixture.afterQueuedSpy.mockImplementationOnce(() => {
      fixture.api.emit("execution_start", { prompt_id: promptId });
      fixture.api.emit("execution_success", { prompt_id: promptId });
    });
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => promptId,
      timeoutMs: 1_000,
    });

    const receipt = await coordinator.queue({
      experimentId: "exp-fast",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const observed = [];
    receipt.subscribeLifecycle((event) => observed.push(event.type));
    fixture.api.emit("execution_error", { prompt_id: promptId });

    expect(observed).toEqual(["execution_start", "execution_success"]);
    expect(coordinator.debugSnapshot().activeAttempts).toBe(0);
    coordinator.dispose();
  });

  it("rejects marker collision and original callback failure with exact restoration", async () => {
    const collision = createHostFixture();
    collision.graph.extra[PARAMETER_LAB_RECEIPT_KEY] = {
      version: 1,
      prompt_id: "77777777-7777-4777-8777-777777777777",
    };
    const collisionCoordinator = createParameterLabReceiptCoordinator({
      app: collision.app,
      api: collision.api,
      uuidFactory: () => "88888888-8888-4888-8888-888888888888",
      timeoutMs: 1_000,
    });
    await expect(
      collisionCoordinator.queue({
        experimentId: "exp-collision",
        runId: "0",
        widget: collision.widget,
        binding: captureGraphBinding(collision.app),
      }),
    ).rejects.toMatchObject({ code: "receipt_marker_collision" });
    collisionCoordinator.dispose();

    const callbackFailure = createHostFixture();
    const originalBefore = callbackFailure.widget.beforeQueued;
    const originalAfter = callbackFailure.widget.afterQueued;
    callbackFailure.beforeQueuedSpy.mockImplementationOnce(() => {
      throw new Error("private callback detail");
    });
    const failedCoordinator = createParameterLabReceiptCoordinator({
      app: callbackFailure.app,
      api: callbackFailure.api,
      uuidFactory: () => "99999999-9999-4999-8999-999999999999",
      timeoutMs: 1_000,
    });
    await expect(
      failedCoordinator.queue({
        experimentId: "exp-callback",
        runId: "0",
        widget: callbackFailure.widget,
        binding: captureGraphBinding(callbackFailure.app),
      }),
    ).rejects.toMatchObject({ code: "host_queue_failed" });
    expect(callbackFailure.widget.beforeQueued).toBe(originalBefore);
    expect(callbackFailure.widget.afterQueued).toBe(originalAfter);
    expect(failedCoordinator.debugSnapshot().installed).toBe(false);
    failedCoordinator.dispose();
  });

  it("bounds a missing host boundary with timeout cleanup", async () => {
    vi.useFakeTimers();
    try {
      const fixture = createHostFixture();
      fixture.app.queuePrompt = function () {
        const requestId = 1;
        fixture.api.emit("promptQueueing", {
          requestId,
          batchCount: 1,
        });
        this.processingQueue = true;
        return new Promise(() => {});
      };
      const originalBefore = fixture.widget.beforeQueued;
      const originalAfter = fixture.widget.afterQueued;
      const coordinator = createParameterLabReceiptCoordinator({
        app: fixture.app,
        api: fixture.api,
        uuidFactory: () => "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        timeoutMs: 25,
      });
      const pending = coordinator.queue({
        experimentId: "exp-timeout",
        runId: "0",
        widget: fixture.widget,
        binding: captureGraphBinding(fixture.app),
      });
      const rejection = expect(pending).rejects.toMatchObject({
        code: "receipt_timeout",
      });
      await vi.advanceTimersByTimeAsync(25);

      await rejection;
      coordinator.dispose();
      expect(fixture.widget.beforeQueued).toBe(originalBefore);
      expect(fixture.widget.afterQueued).toBe(originalAfter);
      expect(coordinator.debugSnapshot().installed).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("bounds unrelated request-boundary floods without losing exact lifecycle cleanup", async () => {
    const fixture = createHostFixture();
    const promptId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const coordinator = createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => promptId,
      timeoutMs: 1_000,
    });
    const receipt = await coordinator.queue({
      experimentId: "exp-boundary-flood",
      runId: "0",
      widget: fixture.widget,
      binding: captureGraphBinding(fixture.app),
    });
    const observed = [];
    receipt.subscribeLifecycle((event) => observed.push(event.type));

    for (let index = 0; index < 1_000; index += 1) {
      fixture.api.emit("promptQueueing", {
        requestId: 10_000 + index,
        batchCount: 1,
      });
    }

    expect(coordinator.debugSnapshot().pendingRequests).toBe(0);
    fixture.api.emit("execution_success", { prompt_id: promptId });
    expect(observed).toEqual(["execution_success"]);
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 0,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: false,
    });
    coordinator.dispose();
  });
});

describe("Parameter Lab receipt workflow binding", () => {
  const id = "55555555-5555-4555-8555-555555555555";

  function createBoundCoordinator(fixture) {
    return createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => id,
      timeoutMs: 1000,
    });
  }

  it("rejects a queue request that carries no workflow binding before touching the host", async () => {
    const fixture = createHostFixture();
    const serializer = fixture.graph.onSerialize;
    const queuePrompt = vi.spyOn(fixture.app, "queuePrompt");
    const coordinator = createBoundCoordinator(fixture);
    await expect(
      coordinator.queue({ experimentId: "exp-unbound", runId: "0", widget: fixture.widget }),
    ).rejects.toMatchObject({ code: "workflow_binding_required" });
    expect(queuePrompt).not.toHaveBeenCalled();
    expect(fixture.widget.beforeQueued).toBe(fixture.beforeQueuedSpy);
    expect(fixture.graph.onSerialize).toBe(serializer);
    expect(coordinator.debugSnapshot()).toMatchObject({ activeAttempts: 0, installed: false });
    coordinator.dispose();
  });

  it("rejects before any host call when the workflow already changed", async () => {
    const fixture = createHostFixture();
    const other = createFakeGraph("workflow-b");
    const otherSerializer = other.onSerialize;
    const queuePrompt = vi.spyOn(fixture.app, "queuePrompt");
    const coordinator = createBoundCoordinator(fixture);
    const binding = captureGraphBinding(fixture.app);
    fixture.switchWorkflow(other);
    await expect(
      coordinator.queue({ experimentId: "exp-switched", runId: "0", widget: fixture.widget, binding }),
    ).rejects.toMatchObject({ code: "workflow_changed" });
    expect(queuePrompt).not.toHaveBeenCalled();
    expect(fixture.widget.beforeQueued).toBe(fixture.beforeQueuedSpy);
    expect(other.onSerialize).toBe(otherSerializer);
    expect(coordinator.debugSnapshot()).toMatchObject({ activeAttempts: 0, installed: false });
    coordinator.dispose();
  });

  it("fails the run and arms nothing on the other workflow when a switch lands inside the host preamble", async () => {
    const fixture = createHostFixture();
    const other = createFakeGraph("workflow-b");
    const otherSerializer = other.onSerialize;
    const coordinator = createBoundCoordinator(fixture);
    const binding = captureGraphBinding(fixture.app);
    fixture.hooks.afterPreamble = () => fixture.switchWorkflow(other);

    await expect(
      coordinator.queue({ experimentId: "exp-preamble", runId: "0", widget: fixture.widget, binding }),
    ).rejects.toMatchObject({ code: "workflow_changed" });

    // The host still submitted what the other workflow serialized; it carries no marker and
    // nothing was recorded as this run's success.
    expect(fixture.submissions).toHaveLength(1);
    expect(fixture.submissions[0].marker).toBeNull();
    expect(other.onSerialize).toBe(otherSerializer);
    expect(coordinator.debugSnapshot()).toMatchObject({ activeAttempts: 0, lifecycleSubscriptions: 0 });
    coordinator.dispose();
  });
});

// Host-contract characterization. These are target-owned simulations of how ComfyUI's boolean
// app.queuePrompt() and its request boundaries behave; they import nothing from a host and
// invent no host field. Each case pins what the coordinator does when the host breaks a
// boundary the receipt depends on, so a host change that removes a boundary fails here
// instead of producing a receipt that was never serialized.
describe("Parameter Lab host-contract characterization", () => {
  const firstId = "66666666-6666-4666-8666-666666666666";
  const secondId = "77777777-7777-4777-8777-777777777777";
  const thirdId = "88888888-8888-4888-8888-888888888888";

  function createCoordinator(fixture, ids) {
    let next = 0;
    return createParameterLabReceiptCoordinator({
      app: fixture.app,
      api: fixture.api,
      uuidFactory: () => ids[next++],
      timeoutMs: 1000,
    });
  }

  function expectNothingWritten(fixture, serializer, serialize) {
    // No receipt was written on the graph and nothing was serialized under the coordinator.
    expect(serialize).not.toHaveBeenCalled();
    expect(fixture.graph.extra).toEqual({ preserved: true });
    expect(fixture.graph.onSerialize).toBe(serializer);
    expect(fixture.submissions).toHaveLength(0);
    expect(fixture.widget.beforeQueued).toBe(fixture.beforeQueuedSpy);
    expect(fixture.widget.afterQueued).toBe(fixture.afterQueuedSpy);
  }

  it("fails closed with missing_request_boundary when queuePrompt returns false and announces no request", async () => {
    const fixture = createHostFixture();
    const serializer = fixture.graph.onSerialize;
    const serialize = vi.spyOn(fixture.graph, "serialize");
    // A host that refuses the call without ever emitting promptQueueing.
    fixture.app.queuePrompt = function () {
      return false;
    };
    const coordinator = createCoordinator(fixture, [firstId]);

    await expect(
      coordinator.queue({
        experimentId: "exp-no-request-boundary",
        runId: "0",
        widget: fixture.widget,
        binding: captureGraphBinding(fixture.app),
      }),
    ).rejects.toMatchObject({ code: "missing_request_boundary" });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expectNothingWritten(fixture, serializer, serialize);
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 0,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: false,
    });
    coordinator.dispose();
  });

  it("fails closed with missing_queued_boundary when a request is announced and queuePrompt returns false without promptQueued", async () => {
    const fixture = createHostFixture();
    const serializer = fixture.graph.onSerialize;
    const serialize = vi.spyOn(fixture.graph, "serialize");
    // The host announces the request, then gives up before queueing: no beforeQueued, no promptQueued.
    fixture.app.queuePrompt = function () {
      fixture.api.emit("promptQueueing", { requestId: 41, batchCount: 1 });
      return false;
    };
    const coordinator = createCoordinator(fixture, [firstId]);

    await expect(
      coordinator.queue({
        experimentId: "exp-no-queued-boundary",
        runId: "0",
        widget: fixture.widget,
        binding: captureGraphBinding(fixture.app),
      }),
    ).rejects.toMatchObject({ code: "missing_queued_boundary" });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expectNothingWritten(fixture, serializer, serialize);
    expect(coordinator.debugSnapshot()).toEqual({
      activeAttempts: 0,
      pendingRequests: 0,
      lifecycleSubscriptions: 0,
      installed: false,
    });
    coordinator.dispose();
  });

  it("keeps each receipt on its own plan graph when the host hands out a new graph object between two queue calls", async () => {
    const fixture = createHostFixture();
    const graphA = fixture.graph;
    const graphB = createFakeGraph("workflow-b");
    const serializerB = graphB.onSerialize;
    const coordinator = createCoordinator(fixture, [firstId, secondId, thirdId]);

    const bindingA = captureGraphBinding(fixture.app);
    const first = await coordinator.queue({
      experimentId: "exp-graph-a",
      runId: "0",
      widget: fixture.widget,
      binding: bindingA,
    });
    first.release();

    // The host now reports a different graph object, as a per-workflow host does after a tab switch.
    fixture.switchWorkflow(graphB);
    expect(fixture.app.rootGraph).toBe(graphB);
    expect(fixture.app.graph).toBe(graphB);

    const bindingB = captureGraphBinding(fixture.app);
    const second = await coordinator.queue({
      experimentId: "exp-graph-b",
      runId: "0",
      widget: fixture.widget,
      binding: bindingB,
    });
    second.release();

    // Each submission carries the marker of the run bound to the graph that was serialized.
    expect(fixture.submissions.map((entry) => entry.marker?.prompt_id)).toEqual([firstId, secondId]);
    expect(first.promptId).toBe(firstId);
    expect(second.promptId).toBe(secondId);

    // A plan built on graph A cannot be queued against graph B: nothing is written on B.
    const submissionsBefore = fixture.submissions.length;
    await expect(
      coordinator.queue({
        experimentId: "exp-graph-a-stale",
        runId: "1",
        widget: fixture.widget,
        binding: bindingA,
      }),
    ).rejects.toMatchObject({ code: "workflow_changed" });
    expect(fixture.submissions).toHaveLength(submissionsBefore);
    expect(graphB.onSerialize).toBe(serializerB);
    expect(graphB.extra).toEqual({ preserved: true });
    expect(graphA.extra).toEqual({ preserved: true });
    expect(coordinator.debugSnapshot()).toMatchObject({ activeAttempts: 0, installed: false });
    coordinator.dispose();
  });
});
