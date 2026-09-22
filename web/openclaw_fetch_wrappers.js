/**
 * R96: Fetch wrapper composition helpers with idempotence guard.
 *
 * Prevents duplicate wrapper stacking when bootstrap code runs multiple times.
 */

const WRAP_META = Symbol.for("openclaw.fetch_wrapper_meta");
const PRECONNECTED_ORIGINS = new Set();

function getDecoratorId(decorator, index) {
    if (typeof decorator?.id === "string" && decorator.id) return decorator.id;
    if (typeof decorator?.name === "string" && decorator.name) return decorator.name;
    return `decorator_${index}`;
}

function arraysEqual(a = [], b = []) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
        if (a[i] !== b[i]) return false;
    }
    return true;
}

export function getFetchWrapperMeta(fetchFn) {
    return fetchFn?.[WRAP_META] || null;
}

export function composeFetchWrappersOnce(fetchFn, decorators = []) {
    if (typeof fetchFn !== "function") {
        throw new TypeError("fetchFn must be a function");
    }
    if (!Array.isArray(decorators)) {
        throw new TypeError("decorators must be an array");
    }

    const chainIds = decorators.map(getDecoratorId);
    const existingMeta = getFetchWrapperMeta(fetchFn);
    if (existingMeta && arraysEqual(existingMeta.chainIds, chainIds)) {
        return fetchFn;
    }

    let wrapped = fetchFn;
    decorators.forEach((decorator, index) => {
        if (typeof decorator !== "function") {
            throw new TypeError(`decorator at index ${index} must be a function`);
        }
        wrapped = decorator(wrapped);
    });

    const baseMeta = existingMeta || { baseFetch: fetchFn, appliedCount: 0, chainIds: [] };
    Object.defineProperty(wrapped, WRAP_META, {
        value: {
            baseFetch: baseMeta.baseFetch || fetchFn,
            appliedCount: (baseMeta.appliedCount || 0) + 1,
            chainIds,
        },
        enumerable: false,
        configurable: false,
        writable: false,
    });
    return wrapped;
}

/**
 * Decorator: ensures signal is passed through when options contain one.
 * (Useful when other decorators clone/normalize init objects.)
 */
export function withAbortPassthrough() {
    const decorator = async (next, input, init = {}) => next(input, { ...init });
    const wrapper = (next) => (input, init) => decorator(next, input, init);
    wrapper.id = "abort_passthrough";
    return wrapper;
}

/**
 * Decorator: best-effort browser preconnect hint for HTTP(S) origins.
 */
export function withPreconnectHint() {
    const wrapper = (next) => (input, init) => {
        try {
            const url = typeof input === "string" ? input : (input?.url || "");
            if (url && typeof document !== "undefined" && /^https?:\/\//i.test(url)) {
                const origin = new URL(url, window.location.href).origin;
                if (!PRECONNECTED_ORIGINS.has(origin)) {
                    PRECONNECTED_ORIGINS.add(origin);
                    const link = document.createElement("link");
                    link.rel = "preconnect";
                    link.href = origin;
                    document.head.appendChild(link);
                }
            }
        } catch {
            // best-effort only
        }
        return next(input, init);
    };
    wrapper.id = "preconnect_hint";
    return wrapper;
}

/**
 * Decorator: single retry for idempotent GET requests on network failures.
 */
export function withGetRetry({ retries = 1 } = {}) {
    const wrapper = (next) => async (input, init = {}) => {
        const method = String(init?.method || "GET").toUpperCase();
        const shouldRetry = method === "GET" && retries > 0;
        let lastErr;
        const attempts = shouldRetry ? retries + 1 : 1;
        for (let i = 0; i < attempts; i += 1) {
            try {
                return await next(input, init);
            } catch (err) {
                // IMPORTANT: host header timeouts and an expired product signal are terminal.
                // Retrying either can duplicate work after the request deadline.
                const isDeadline = err?.name === "AbortError" || err?.name === "TimeoutError" || init?.signal?.aborted;
                if (isDeadline || i >= attempts - 1) throw err;
                lastErr = err;
            }
        }
        throw lastErr || new Error("fetch_retry_exhausted");
    };
    wrapper.id = `retry_get_${retries}`;
    return wrapper;
}
