/**
 * Exact Parameter Lab queue receipts over ComfyUI's boolean app.queuePrompt API.
 *
 * The coordinator observes the host request boundary, mirrors its reviewed LIFO
 * selection, and adds a transient UUID only to the matching serialized workflow.
 */

import { verifyGraphBinding } from "./openclaw_graph_host.js";

export const PARAMETER_LAB_RECEIPT_KEY = "__openclaw_parameter_lab_receipt__";
export const PARAMETER_LAB_RECEIPT_VERSION = 1;

const MAX_ACTIVE_ATTEMPTS = 64;
const MAX_TRACKED_REQUESTS = 128;
const MAX_BUFFERED_LIFECYCLE_EVENTS = 8;
const DEFAULT_TIMEOUT_MS = 30_000;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TERMINAL_EVENTS = new Set([
  "execution_success",
  "execution_error",
  "execution_interrupted",
]);
const LIFECYCLE_EVENTS = [
  "execution_start",
  "execution_success",
  "execution_error",
  "execution_interrupted",
];

export class ParameterLabReceiptError extends Error {
  constructor(code) {
    super(code);
    this.name = "ParameterLabReceiptError";
    this.code = code;
  }
}

function receiptError(code) {
  return new ParameterLabReceiptError(code);
}

function isPositiveInteger(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function normalizePromptId(value) {
  return typeof value === "string" && UUID_RE.test(value) ? value : "";
}

function safeCall(callback, thisArg, args) {
  if (typeof callback !== "function") return undefined;
  return callback.apply(thisArg, args);
}

class ParameterLabReceiptCoordinator {
  constructor({
    app,
    api,
    uuidFactory = () => globalThis.crypto.randomUUID(),
    timeoutMs = DEFAULT_TIMEOUT_MS,
  }) {
    if (!app || typeof app.queuePrompt !== "function") {
      throw receiptError("unsupported_host_queue");
    }
    if (
      !api ||
      typeof api.addEventListener !== "function" ||
      typeof api.removeEventListener !== "function"
    ) {
      throw receiptError("unsupported_host_events");
    }
    if (typeof uuidFactory !== "function") {
      throw receiptError("invalid_uuid_factory");
    }
    if (!isPositiveInteger(timeoutMs)) {
      throw receiptError("invalid_receipt_timeout");
    }

    this.app = app;
    this.api = api;
    this.uuidFactory = uuidFactory;
    this.timeoutMs = timeoutMs;
    this.disposed = false;
    this.installed = false;
    this.hostWindowOwned = false;
    this.requestTrackingInvalid = false;
    this.captureAttempt = null;
    this.nextAttemptId = 1;
    this.attempts = new Map();
    this.attemptsByPromptId = new Map();
    this.requests = new Map();
    this.pendingRequests = [];
    this.currentRequest = null;
    this.batchInFlight = null;
    this.hookWidget = null;
    this.originalBeforeQueued = undefined;
    this.originalAfterQueued = undefined;
    this.wrappedBeforeQueued = null;
    this.wrappedAfterQueued = null;

    this.onPromptQueueing = (event) => this._handlePromptQueueing(event);
    this.onPromptQueued = (event) => this._handlePromptQueued(event);
    this.lifecycleHandlers = new Map(
      LIFECYCLE_EVENTS.map((type) => [
        type,
        (event) => this._handleLifecycle(type, event),
      ]),
    );
  }

  debugSnapshot() {
    let lifecycleSubscriptions = 0;
    for (const attempt of this.attempts.values()) {
      if (typeof attempt.lifecycleCallback === "function") {
        lifecycleSubscriptions += 1;
      }
    }
    return {
      activeAttempts: this.attempts.size,
      pendingRequests: this.requests.size,
      lifecycleSubscriptions,
      installed: this.installed,
    };
  }

  queue({ experimentId, runId, widget, signal, binding } = {}) {
    if (this.disposed) {
      return Promise.reject(receiptError("coordinator_disposed"));
    }
    if (signal?.aborted) {
      return Promise.reject(receiptError("attempt_cancelled"));
    }
    if (!widget || typeof widget !== "object") {
      return Promise.reject(receiptError("receipt_widget_required"));
    }
    if (!binding || typeof binding !== "object") {
      return Promise.reject(receiptError("workflow_binding_required"));
    }
    if (!verifyGraphBinding(binding, this.app).ok) {
      return Promise.reject(receiptError("workflow_changed"));
    }
    if (this.attempts.size >= MAX_ACTIVE_ATTEMPTS) {
      return Promise.reject(receiptError("too_many_receipt_attempts"));
    }
    if (this.requestTrackingInvalid) {
      return Promise.reject(receiptError("request_tracking_unavailable"));
    }

    const promptId = normalizePromptId(this.uuidFactory());
    if (!promptId) {
      return Promise.reject(receiptError("invalid_receipt_id"));
    }
    if (this.attemptsByPromptId.has(promptId)) {
      return Promise.reject(receiptError("duplicate_receipt_id"));
    }

    const ownsNewWindow = !this.hostWindowOwned;
    if (ownsNewWindow && this.app.processingQueue !== false) {
      return Promise.reject(receiptError("host_queue_unobserved_busy"));
    }
    if (this.captureAttempt) {
      return Promise.reject(receiptError("request_capture_busy"));
    }

    try {
      this._install(widget);
    } catch (error) {
      return Promise.reject(error);
    }

    const attempt = this._createAttempt({
      experimentId,
      runId,
      promptId,
      signal,
      binding,
    });
    if (ownsNewWindow) this.hostWindowOwned = true;
    this.captureAttempt = attempt;

    let hostResult;
    try {
      hostResult = this.app.queuePrompt(0, 1);
    } catch (_error) {
      this.captureAttempt = null;
      this._failAttempt(attempt, "host_queue_failed");
      if (ownsNewWindow) this._finishHostWindow("host_queue_failed");
      return attempt.promise;
    }
    this.captureAttempt = null;

    if (attempt.requestId === null) {
      this._failAttempt(attempt, "missing_request_boundary");
    }

    const hostPromise = Promise.resolve(hostResult);
    if (ownsNewWindow) {
      hostPromise.then(
        () => this._finishHostWindow(),
        () => this._finishHostWindow("host_queue_failed"),
      );
    } else {
      hostPromise.catch(() => this._failAttempt(attempt, "host_queue_failed"));
    }
    return attempt.promise;
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const attempt of [...this.attempts.values()]) {
      this._failAttempt(attempt, "coordinator_disposed");
    }
    this.requests.clear();
    this.pendingRequests.length = 0;
    this.currentRequest = null;
    this.batchInFlight = null;
    this.captureAttempt = null;
    this.hostWindowOwned = false;
    this._uninstall();
  }

  _createAttempt({ experimentId, runId, promptId, signal, binding }) {
    const id = this.nextAttemptId++;
    const attempt = {
      id,
      experimentId: String(experimentId ?? ""),
      runId: String(runId ?? ""),
      promptId,
      binding,
      requestId: null,
      state: "pending",
      serializedCount: 0,
      lifecycleCallback: null,
      lifecycleBuffer: [],
      signal,
      abortHandler: null,
      timer: null,
      receipt: null,
      settled: false,
      resolve: null,
      reject: null,
      promise: null,
    };
    attempt.promise = new Promise((resolve, reject) => {
      attempt.resolve = resolve;
      attempt.reject = reject;
    });
    attempt.timer = setTimeout(
      () => this._failAttempt(attempt, "receipt_timeout"),
      this.timeoutMs,
    );
    if (signal) {
      attempt.abortHandler = () =>
        this._failAttempt(attempt, "attempt_cancelled");
      signal.addEventListener("abort", attempt.abortHandler, { once: true });
    }
    this.attempts.set(id, attempt);
    this.attemptsByPromptId.set(promptId, attempt);
    return attempt;
  }

  _install(widget) {
    if (this.installed) {
      if (widget !== this.hookWidget) {
        throw receiptError("receipt_widget_conflict");
      }
      return;
    }

    this.hookWidget = widget;
    this.originalBeforeQueued = widget.beforeQueued;
    this.originalAfterQueued = widget.afterQueued;
    const coordinator = this;
    this.wrappedBeforeQueued = function (...args) {
      const result = safeCall(coordinator.originalBeforeQueued, this, args);
      coordinator._handleBeforeQueued();
      return result;
    };
    this.wrappedAfterQueued = function (...args) {
      const result = safeCall(coordinator.originalAfterQueued, this, args);
      coordinator._handleAfterQueued();
      return result;
    };
    widget.beforeQueued = this.wrappedBeforeQueued;
    widget.afterQueued = this.wrappedAfterQueued;

    this.api.addEventListener("promptQueueing", this.onPromptQueueing);
    this.api.addEventListener("promptQueued", this.onPromptQueued);
    for (const [type, handler] of this.lifecycleHandlers) {
      this.api.addEventListener(type, handler);
    }
    this.installed = true;
  }

  _uninstall() {
    if (!this.installed) return;
    if (this.hookWidget?.beforeQueued === this.wrappedBeforeQueued) {
      this.hookWidget.beforeQueued = this.originalBeforeQueued;
    }
    if (this.hookWidget?.afterQueued === this.wrappedAfterQueued) {
      this.hookWidget.afterQueued = this.originalAfterQueued;
    }
    this.api.removeEventListener("promptQueueing", this.onPromptQueueing);
    this.api.removeEventListener("promptQueued", this.onPromptQueued);
    for (const [type, handler] of this.lifecycleHandlers) {
      this.api.removeEventListener(type, handler);
    }
    this.hookWidget = null;
    this.wrappedBeforeQueued = null;
    this.wrappedAfterQueued = null;
    this.originalBeforeQueued = undefined;
    this.originalAfterQueued = undefined;
    this.installed = false;
  }

  _handlePromptQueueing(event) {
    if (this.requestTrackingInvalid) return;
    const detail = event?.detail;
    const requestId = detail?.requestId;
    const batchCount = detail?.batchCount;
    if (
      !Number.isSafeInteger(requestId) ||
      requestId < 0 ||
      !isPositiveInteger(batchCount) ||
      this.requests.has(requestId)
    ) {
      if (this.captureAttempt) {
        this._failAttempt(this.captureAttempt, "invalid_request_boundary");
      }
      return;
    }
    if (this.requests.size >= MAX_TRACKED_REQUESTS) {
      this._invalidateRequestTracking();
      return;
    }

    const attempt = this.captureAttempt;
    if (attempt) {
      if (attempt.requestId !== null) {
        this._failAttempt(attempt, "duplicate_request_boundary");
        return;
      }
      attempt.requestId = requestId;
    }
    const request = {
      requestId,
      batchCount,
      remaining: batchCount,
      successful: 0,
      attempt,
    };
    this.requests.set(requestId, request);
    this.pendingRequests.push(request);
  }

  _handleBeforeQueued() {
    if (this.batchInFlight) {
      this._failRequest(this.batchInFlight, "request_batch_incomplete");
      this.currentRequest = null;
      this.batchInFlight = null;
    }
    if (!this.currentRequest) {
      this.currentRequest = this.pendingRequests.pop() ?? null;
    }
    const request = this.currentRequest;
    if (!request) return;
    this.batchInFlight = request;
    if (request.attempt?.state === "pending") {
      this._armSerialization(request.attempt);
    }
  }

  _handleAfterQueued() {
    const request = this.batchInFlight;
    if (!request) return;
    this.batchInFlight = null;
    request.successful += 1;
    request.remaining -= 1;
    if (
      request.attempt?.state === "pending" &&
      request.attempt.serializedCount !== request.successful
    ) {
      this._failAttempt(request.attempt, "receipt_not_serialized");
    }
  }

  _armSerialization(attempt) {
    const coordinator = this;
    // IMPORTANT: arm only on the graph this attempt was bound to. The host can switch workflows
    // between queuePrompt() and beforeQueued; arming whichever graph is active then would stamp the
    // receipt into the other workflow's submission and record it as this run's success.
    const verified = verifyGraphBinding(attempt.binding, this.app);
    if (!verified.ok) {
      this._failAttempt(attempt, "workflow_changed");
      return;
    }
    const graph = verified.graph;
    if (!graph || typeof graph.serialize !== "function") {
      this._failAttempt(attempt, "unsupported_graph_serialization");
      return;
    }
    const previous = graph.onSerialize;
    if (previous !== undefined && typeof previous !== "function") {
      this._failAttempt(attempt, "unsupported_graph_callback");
      return;
    }

    let armed = true;
    const wrapper = function (data) {
      if (!armed) return;
      armed = false;
      if (graph.onSerialize === wrapper) graph.onSerialize = previous;
      safeCall(previous, this, [data]);
      if (
        !data ||
        typeof data !== "object" ||
        !data.extra ||
        typeof data.extra !== "object" ||
        Object.prototype.hasOwnProperty.call(
          data.extra,
          PARAMETER_LAB_RECEIPT_KEY,
        )
      ) {
        coordinator._failAttempt(attempt, "receipt_marker_collision");
        throw receiptError("receipt_marker_collision");
      }
      data.extra[PARAMETER_LAB_RECEIPT_KEY] = {
        version: PARAMETER_LAB_RECEIPT_VERSION,
        prompt_id: attempt.promptId,
      };
      attempt.serializedCount += 1;
    };
    graph.onSerialize = wrapper;
    queueMicrotask(() => {
      if (!armed) return;
      armed = false;
      if (graph.onSerialize === wrapper) graph.onSerialize = previous;
    });
  }

  _handlePromptQueued(event) {
    const detail = event?.detail;
    const requestId = detail?.requestId;
    const request = this.requests.get(requestId);
    if (!request) return;
    if (
      request !== this.currentRequest ||
      request.remaining !== 0 ||
      detail?.batchCount !== request.successful
    ) {
      this._failRequest(request, "request_boundary_mismatch");
      return;
    }

    this.requests.delete(requestId);
    this.currentRequest = null;
    this.batchInFlight = null;
    const attempt = request.attempt;
    if (!attempt || attempt.state !== "pending") return;
    if (
      request.batchCount !== 1 ||
      request.successful !== 1 ||
      attempt.serializedCount !== 1
    ) {
      this._failAttempt(attempt, "receipt_count_mismatch");
      return;
    }
    this._acceptAttempt(attempt);
  }

  _acceptAttempt(attempt) {
    attempt.state = "accepted";
    clearTimeout(attempt.timer);
    if (attempt.signal && attempt.abortHandler) {
      attempt.signal.removeEventListener("abort", attempt.abortHandler);
    }
    const coordinator = this;
    const receipt = Object.freeze({
      promptId: attempt.promptId,
      requestId: attempt.requestId,
      subscribeLifecycle(callback) {
        if (typeof callback !== "function") {
          throw receiptError("invalid_lifecycle_callback");
        }
        if (!coordinator.attempts.has(attempt.id)) return () => {};
        attempt.lifecycleCallback = callback;
        coordinator._flushLifecycle(attempt);
        return () => {
          if (attempt.lifecycleCallback === callback) {
            attempt.lifecycleCallback = null;
          }
        };
      },
      release() {
        coordinator._releaseAttempt(attempt);
      },
    });
    attempt.receipt = receipt;
    if (!this.hostWindowOwned) {
      attempt.settled = true;
      attempt.resolve(receipt);
    }
  }

  _handleLifecycle(type, event) {
    const promptId = event?.detail?.prompt_id;
    if (typeof promptId !== "string") return;
    const attempt = this.attemptsByPromptId.get(promptId);
    if (!attempt || attempt.state === "failed") return;
    if (attempt.lifecycleBuffer.length >= MAX_BUFFERED_LIFECYCLE_EVENTS) {
      this._failAttempt(attempt, "lifecycle_buffer_exceeded");
      return;
    }
    // CRITICAL: host lifecycle payloads can contain node/error content.
    // The receipt boundary needs only the opaque prompt ID and event type.
    attempt.lifecycleBuffer.push(Object.freeze({ type, promptId }));
    if (attempt.state === "accepted" && attempt.lifecycleCallback) {
      this._flushLifecycle(attempt);
    }
  }

  _flushLifecycle(attempt) {
    while (
      this.attempts.has(attempt.id) &&
      attempt.lifecycleCallback &&
      attempt.lifecycleBuffer.length
    ) {
      const event = attempt.lifecycleBuffer.shift();
      try {
        attempt.lifecycleCallback(event);
      } catch (_error) {
        // CRITICAL: an internal UI consumer failure must not retain prompt ownership
        // or expose its potentially private exception detail through host dispatch.
        this._releaseAttempt(attempt);
        return;
      }
      if (TERMINAL_EVENTS.has(event.type)) {
        this._releaseAttempt(attempt);
        return;
      }
    }
  }

  _failRequest(request, code) {
    this.requests.delete(request.requestId);
    const pendingIndex = this.pendingRequests.indexOf(request);
    if (pendingIndex >= 0) this.pendingRequests.splice(pendingIndex, 1);
    if (request.attempt) this._failAttempt(request.attempt, code);
  }

  _invalidateRequestTracking() {
    // CRITICAL: dropping one LIFO boundary while retaining others could cross-assign
    // a later receipt, so overflow invalidates the whole request-correlation window.
    this.requestTrackingInvalid = true;
    const pendingAttempts = [...this.attempts.values()].filter(
      (attempt) => attempt.state === "pending",
    );
    this.requests.clear();
    this.pendingRequests.length = 0;
    this.currentRequest = null;
    this.batchInFlight = null;
    for (const attempt of pendingAttempts) {
      this._failAttempt(attempt, "request_boundary_overflow");
    }
    this._maybeUninstall();
  }

  _failAttempt(attempt, code) {
    if (!attempt || !this.attempts.has(attempt.id)) return;
    attempt.state = "failed";
    clearTimeout(attempt.timer);
    if (attempt.signal && attempt.abortHandler) {
      attempt.signal.removeEventListener("abort", attempt.abortHandler);
    }
    this.attempts.delete(attempt.id);
    this.attemptsByPromptId.delete(attempt.promptId);
    attempt.reject(receiptError(code));
    this._maybeUninstall();
  }

  _releaseAttempt(attempt) {
    if (!attempt || !this.attempts.has(attempt.id)) return;
    clearTimeout(attempt.timer);
    if (attempt.signal && attempt.abortHandler) {
      attempt.signal.removeEventListener("abort", attempt.abortHandler);
    }
    this.attempts.delete(attempt.id);
    this.attemptsByPromptId.delete(attempt.promptId);
    attempt.lifecycleCallback = null;
    attempt.lifecycleBuffer.length = 0;
    this._maybeUninstall();
  }

  _finishHostWindow(failureCode = "") {
    this.hostWindowOwned = false;
    if (failureCode) {
      for (const request of [...this.requests.values()]) {
        if (request.attempt) this._failAttempt(request.attempt, failureCode);
      }
    } else {
      for (const request of [...this.requests.values()]) {
        if (request.attempt) {
          this._failAttempt(request.attempt, "missing_queued_boundary");
        }
      }
    }
    this.requests.clear();
    this.pendingRequests.length = 0;
    this.currentRequest = null;
    this.batchInFlight = null;
    for (const attempt of this.attempts.values()) {
      if (attempt.state === "accepted" && attempt.receipt && !attempt.settled) {
        attempt.settled = true;
        attempt.resolve(attempt.receipt);
      }
    }
    this._maybeUninstall();
  }

  _maybeUninstall() {
    if (
      !this.hostWindowOwned &&
      this.attempts.size === 0 &&
      this.requests.size === 0
    ) {
      this._uninstall();
    }
  }
}

export function createParameterLabReceiptCoordinator(options) {
  return new ParameterLabReceiptCoordinator(options);
}
