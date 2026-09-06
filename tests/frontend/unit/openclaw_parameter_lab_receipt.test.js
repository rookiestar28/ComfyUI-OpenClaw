import { describe, expect, it, vi } from "vitest";

import {
  PARAMETER_LAB_RECEIPT_KEY,
  createParameterLabReceiptCoordinator,
} from "../../../web/openclaw_parameter_lab_receipt.js";

class FakeApi extends EventTarget {
  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

function createHostFixture() {
  const api = new FakeApi();
  const submissions = [];
  const beforeQueuedSpy = vi.fn();
  const afterQueuedSpy = vi.fn();
  const widget = {
    beforeQueued: beforeQueuedSpy,
    afterQueued: afterQueuedSpy,
  };
  const graph = {
    extra: { preserved: true },
    onSerialize: vi.fn(),
    serialize() {
      const data = { nodes: [], extra: { ...this.extra } };
      this.onSerialize?.(data);
      return data;
    },
  };
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
      try {
        while (this.queueItems.length) {
          const request = this.queueItems.pop();
          let queuedCount = 0;
          for (let index = 0; index < request.batchCount; index += 1) {
            widget.beforeQueued?.({ isPartialExecution: false });
            const workflow = graph.serialize();
            const marker = workflow.extra?.[PARAMETER_LAB_RECEIPT_KEY];
            const promptId = marker?.prompt_id || `foreign-${nextForeignId++}`;
            submissions.push({
              requestId: request.requestId,
              promptId,
              marker: marker || null,
            });
            widget.afterQueued?.({ isPartialExecution: false });
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
  return {
    api,
    app,
    graph,
    submissions,
    widget,
    beforeQueuedSpy,
    afterQueuedSpy,
  };
}

describe("Parameter Lab authoritative queue receipt", () => {
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
    });
    const second = coordinator.queue({
      experimentId: "exp-two",
      runId: "0",
      widget: fixture.widget,
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
    });
    expect(fixture.app.processingQueue).toBe(false);

    const second = await coordinator.queue({
      experimentId: "exp-sequential",
      runId: "1",
      widget: fixture.widget,
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
