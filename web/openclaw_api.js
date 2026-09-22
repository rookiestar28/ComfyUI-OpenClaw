/**
 * OpenClaw API Wrapper (R7)
 * Provides consistent fetch usage, timeout handling, and type-safe response shapes.
 */
import { OpenClawSession } from "./openclaw_session.js";
import { fetchApi, fileURL } from "./openclaw_comfy_api.js";
import { API_PREFIXES, buildAdminTokenHeaders, getApiPathCandidates } from "./openclaw_compat.js";
import { isAbortError, linkAbortSignal, parseJsonSafe } from "./openclaw_utils.js";
import { configApiMethods } from "./openclaw_api_config.js";
import { eventApiMethods } from "./openclaw_api_events.js";
import { generationApiMethods } from "./openclaw_api_generation.js";
import { modelApiMethods } from "./openclaw_api_models.js";
import { resourceApiMethods } from "./openclaw_api_resources.js";
import {
    composeFetchWrappersOnce,
    withAbortPassthrough,
    withGetRetry,
    withPreconnectHint,
} from "./openclaw_fetch_wrappers.js";

/**
 * @typedef {Object} OpenClawFetchOptions
 * @property {number=} timeout Request timeout in milliseconds.
 * @property {AbortSignal=} signal Optional caller-owned cancellation signal.
 * @property {string=} method HTTP method.
 * @property {HeadersInit=} headers Request headers.
 * @property {BodyInit|null=} body Request body.
 */

/**
 * @typedef {Object} OpenClawFetchSuccess
 * @property {true} ok
 * @property {number} status
 * @property {*} data Parsed JSON value or response text.
 */

/**
 * @typedef {Object} OpenClawFetchFailure
 * @property {false} ok
 * @property {number} status HTTP status, or 0 for network/timeout/cancelled failures.
 * @property {string} error Stable error code/message.
 * @property {*=} data Parsed error payload or response text.
 * @property {string=} detail Low-level error detail for diagnostics.
 */

/**
 * @typedef {OpenClawFetchSuccess|OpenClawFetchFailure} OpenClawFetchResult
 */

function classifyTransportError(err, cancelledByCaller, timedOut) {
    if (cancelledByCaller) return "cancelled";
    if (timedOut || err?.name === "TimeoutError") return "timeout";
    return isAbortError(err) ? "cancelled" : "network_error";
}

export class OpenClawAPI {
    constructor() {
        this._capabilitiesCache = null;
        this._capabilitiesCacheTs = 0;

        // R96: Compose fetch wrappers exactly once per fetch instance to avoid
        // duplicate retry/preconnect/abort decoration on repeated bootstrap.
        this._decoratedFetchApi = composeFetchWrappersOnce(fetchApi, [
            withAbortPassthrough(),
            withPreconnectHint(),
            withGetRetry({ retries: 1 }),
        ]);
        this._decoratedNativeFetch = composeFetchWrappersOnce(fetch.bind(window), [
            withAbortPassthrough(),
            withPreconnectHint(),
            withGetRetry({ retries: 1 }),
        ]);
    }

    /**
     * Gets the admin token from session storage (if available).
     */
    _getAdminToken() {
        return OpenClawSession.getAdminToken() || "";
    }

    _path(suffix) {
        return `${API_PREFIXES.canonical}${suffix}`;
    }

    _candidatePaths(url) {
        return getApiPathCandidates(url);
    }

    async _fetchWithCandidates(url, options = {}) {
        let response = null;
        const candidates = this._candidatePaths(url);
        const nativeOptions = { ...options };
        delete nativeOptions.timeoutMs;
        // IMPORTANT: OpenClaw owns the deadline at both call sites. Disable only the
        // newer host's header timer; leaking timeoutMs into native fetch breaks fallback parity.
        const hostOptions = { ...nativeOptions, timeoutMs: null };

        for (const candidate of candidates) {
            response = await this._decoratedFetchApi(candidate, hostOptions);
            if (response.status !== 404) break;
        }

        if (response && response.status === 404 && typeof url === "string") {
            for (const candidate of candidates) {
                try {
                    response = await this._decoratedNativeFetch(fileURL(candidate), nativeOptions);
                    if (response.status !== 404) break;
                } catch (err) {
                    // IMPORTANT: an expired product signal ends fallback probing.
                    // Swallowing it reports the prior 404 instead of cancellation/timeout.
                    if (nativeOptions.signal?.aborted || isAbortError(err) || err?.name === "TimeoutError") {
                        throw err;
                    }
                    // Ignore ordinary network failure and continue fallback probes.
                }
            }
        }
        return response;
    }

    _adminTokenHeaders(token) {
        return buildAdminTokenHeaders(token || this._getAdminToken());
    }

    /**
     * Generic fetch wrapper with timeout and error normalization.
     * @param {string} url - The URL to fetch
     * @param {OpenClawFetchOptions} options - Fetch options
     * @returns {Promise<OpenClawFetchResult>}
     */
    async fetch(url, options = {}) {
        const { timeout = 10000, signal: externalSignal, ...fetchOptions } = options;

        // R38-Lite: Support both internal timeout and external abort signal
        const controller = new AbortController();
        let timedOut = false;
        let cancelledByCaller = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);

        // R55: Shared abort linkage helper (consistent cancel semantics)
        const detachExternalAbort = linkAbortSignal(
            externalSignal,
            controller,
            () => {
                cancelledByCaller = true;
            }
        );

        try {
            // R26: Use ComfyUI shim (fetchApi) which handles base path automatically
            const response = await this._fetchWithCandidates(url, {
                ...fetchOptions,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            // Best-effort body parsing
            let data = null;
            const contentType = response?.headers?.get("content-type");
            let responseText = null;
            try {
                responseText = await response.text();
            } catch (e) { }

            if (contentType && contentType.includes("application/json") && typeof responseText === "string") {
                data = parseJsonSafe(responseText, null).value;
            } else {
                data = responseText;
            }

            if (!response || !response.ok) {
                // Return normalized error shape
                return {
                    ok: false,
                    status: response ? response.status : 0,
                    error: (data && data.error) || (response ? response.statusText : "request_failed") || "request_failed",
                    data,
                };
            }

            return {
                ok: true,
                status: response.status,
                data,
            };

        } catch (err) {
            clearTimeout(timeoutId);
            // Network or Timeout/Abort errors
            return {
                ok: false,
                status: 0,
                error: classifyTransportError(err, cancelledByCaller, timedOut),
                detail: err?.message,
            };
        } finally {
            detachExternalAbort();
        }
    }

    // --- Endpoints ---

    _parseSSEChunk(rawChunk) {
        const lines = rawChunk.split(/\r?\n/);
        let event = "message";
        const dataLines = [];
        for (const line of lines) {
            if (!line) continue;
            if (line.startsWith("event:")) {
                event = line.slice(6).trim() || "message";
            } else if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trim());
            }
        }
        if (!dataLines.length) return null;
        const joined = dataLines.join("\n");
        let data = null;
        try {
            data = JSON.parse(joined);
        } catch {
            data = { raw: joined };
        }
        return { event, data };
    }

    async streamSSEPost(url, payload, { signal = null, timeout = 60000, onEvent = null } = {}) {
        const controller = new AbortController();
        let timedOut = false;
        let cancelledByCaller = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);

        const detachExternalAbort = linkAbortSignal(signal, controller, () => {
            cancelledByCaller = true;
        });

        try {
            const response = await this._fetchWithCandidates(url, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    ...this._adminTokenHeaders(),
                },
                body: JSON.stringify(payload),
                signal: controller.signal,
            });

            if (!response || !response.ok) {
                clearTimeout(timeoutId);
                let data = null;
                try {
                    data = await response?.json?.();
                } catch {
                    try { data = await response?.text?.(); } catch { }
                }
                return {
                    ok: false,
                    status: response ? response.status : 0,
                    error: (data && data.error) || response?.statusText || "request_failed",
                    data,
                };
            }

            const finalEnvelope = { value: null };
            const dispatchEvent = (evt) => {
                if (!evt) return;
                if (evt.event === "final") {
                    finalEnvelope.value = evt.data;
                }
                if (typeof onEvent === "function") onEvent(evt);
            };

            if (!response.body || typeof response.body.getReader !== "function") {
                const text = await response.text();
                const chunks = text.split(/\r?\n\r?\n/);
                for (const chunk of chunks) dispatchEvent(this._parseSSEChunk(chunk));
            } else {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                const findBoundary = (text) => {
                    const idxCRLF = text.indexOf("\r\n\r\n");
                    const idxLF = text.indexOf("\n\n");
                    if (idxCRLF === -1) return { index: idxLF, len: 2 };
                    if (idxLF === -1) return { index: idxCRLF, len: 4 };
                    return idxCRLF < idxLF ? { index: idxCRLF, len: 4 } : { index: idxLF, len: 2 };
                };
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    let boundary = findBoundary(buffer);
                    while (boundary.index >= 0) {
                        const rawChunk = buffer.slice(0, boundary.index);
                        buffer = buffer.slice(boundary.index + boundary.len);
                        dispatchEvent(this._parseSSEChunk(rawChunk));
                        boundary = findBoundary(buffer);
                    }
                }
                buffer += decoder.decode();
                if (buffer.trim()) {
                    dispatchEvent(this._parseSSEChunk(buffer));
                }
            }

            clearTimeout(timeoutId);
            if (finalEnvelope.value?.ok) {
                return {
                    ok: true,
                    status: 200,
                    data: finalEnvelope.value.result,
                    stream: finalEnvelope.value.streaming || {},
                    envelope: finalEnvelope.value,
                };
            }
            if (finalEnvelope.value && finalEnvelope.value.ok === false) {
                return {
                    ok: false,
                    status: 500,
                    error: finalEnvelope.value.error || "stream_failed",
                    data: finalEnvelope.value,
                };
            }
            return { ok: false, status: 0, error: "stream_incomplete" };
        } catch (err) {
            clearTimeout(timeoutId);
            return {
                ok: false,
                status: 0,
                error: classifyTransportError(err, cancelledByCaller, timedOut),
                detail: err?.message,
            };
        } finally {
            clearTimeout(timeoutId);
            detachExternalAbort();
        }
    }

    /**
     * Run Prompt Planner.
     * @param {object} params - { profile, requirements, style_directives, seed }
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation (R38-Lite)
     */

}

Object.assign(
    OpenClawAPI.prototype,
    configApiMethods,
    generationApiMethods,
    resourceApiMethods,
    modelApiMethods,
    eventApiMethods,
);

export const openclawApi = new OpenClawAPI();
