/**
 * Pure decision logic for the pinned real-host frontend smoke lane.
 *
 * The Playwright spec and the bootstrap runner both need the same answers: which
 * frontend subject is being exercised, whether the host quietly served a
 * different one, whether the sidebar kept its floor, and whether a compatibility
 * evidence refresh is allowed. Keeping those answers here - free of Playwright,
 * of the filesystem, and of any network - lets them be unit-tested with real
 * inputs instead of inspected as text inside a browser runner.
 */

export const BUNDLED_SUBJECT = "bundled";
export const STANDALONE_RELEASE_SUBJECT = "standalone_release";

/**
 * The host logs this exact sentence and then serves the bundled frontend when a
 * requested frontend version cannot be resolved for any reason. A subject that
 * asked for a specific release and sees this line did not test that release.
 */
export const FRONTEND_FALLBACK_LOG_MARKER = "Falling back to the default frontend.";

/**
 * The host prints the directory it actually resolved the frontend from.
 *
 * This has to be read back from the host, not computed from the same policy the
 * lane used to request the subject. Comparing a policy value against itself
 * would look like a third signal while being incapable of ever disagreeing.
 */
export const HOST_WEB_ROOT_LOG_PREFIX = "[Prompt Server] web root:";

export function parseHostWebRoot(logText) {
    const lines = String(logText ?? "").split(/\r?\n/);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
        const at = lines[index].indexOf(HOST_WEB_ROOT_LOG_PREFIX);
        if (at !== -1) {
            const value = lines[index].slice(at + HOST_WEB_ROOT_LOG_PREFIX.length).trim();
            return value === "" ? null : value;
        }
    }
    return null;
}

/**
 * The host prints the origin it is actually serving on.
 *
 * This is the lane's proof of its own bind. It replaced an assertion that
 * `--listen 127.0.0.1` was in the argv, which proved only what the lane asked
 * for: an argv guard would still pass if a future core pin changed the default
 * bind, and this one would not. A missing line is a failure, never a pass, for
 * the same reason `detectSubjectMismatch` refuses to treat a missing web root as
 * agreement.
 */
export const HOST_BIND_LOG_PREFIX = "To see the GUI go to:";

export function parseHostBindOrigin(logText) {
    const lines = String(logText ?? "").split(/\r?\n/);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
        const at = lines[index].indexOf(HOST_BIND_LOG_PREFIX);
        if (at === -1) {
            continue;
        }
        const value = lines[index].slice(at + HOST_BIND_LOG_PREFIX.length).trim();
        if (value === "") {
            return null;
        }
        try {
            return new URL(value).hostname;
        } catch {
            return null;
        }
    }
    return null;
}

export class SubjectError extends Error {}

export function resolveSubject(policy, subjectId) {
    const subject = policy?.subjects?.[subjectId];
    if (!subject) {
        const known = Object.keys(policy?.subjects ?? {}).sort().join(", ");
        throw new SubjectError(`unknown frontend subject ${subjectId}; known subjects: ${known}`);
    }
    return subject;
}

/**
 * A subject that names a release asset must also name that asset's digest.
 *
 * The digest is pinned from the publisher's own release metadata. Failing closed
 * on a missing or malformed one keeps an unverified artifact from ever being
 * presented as release evidence, which still matters: the pin can be removed, and
 * a subject added later may arrive without one.
 */
export function assertSubjectRunnable(subject) {
    if (!subject.release_asset_name) {
        return;
    }
    const digest = subject.release_asset_sha256;
    if (typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) {
        throw new SubjectError(
            `subject ${subject.id} names release asset ${subject.release_asset_name} but no pinned ` +
                "sha256; pin the digest from an authorized download before running this subject",
        );
    }
}

/**
 * Build the host argv for one subject.
 *
 * HOTSPOT: `--listen` must not be passed, and the reason is not obvious enough to
 * survive a well-meaning edit. OpenClaw decides network exposure from
 * `"--listen" in sys.argv` and never reads the value, so `--listen 127.0.0.1` -
 * which is only a spelling of the host's own default bind - is classified as
 * network-exposed, and the product then treats exposure without authentication as a
 * fatal startup error, so the runner would have to configure an admin token; and
 * once one is configured, `require_admin_token` demands a matching header and
 * `resolve_token_info` downgrades loopback from ADMIN to INTERNAL. The browser
 * holds no token, so every admin-class route answers 403 and every test in the
 * spec fails on its request audit. That is precisely what workflow run
 * 34109004949 did. Omitting the flag gives the identical bind with the security
 * posture of a default operator install.
 *
 * The bind is therefore no longer asserted here. It is read back out of the
 * host's own startup line by `parseHostBindOrigin` and checked there, which is
 * what the host did rather than what the lane asked for.
 *
 * Any argument the policy forbids, and any `--listen` or `--port` a caller tries
 * to add, is rejected rather than filtered, so a caller cannot widen exposure or
 * bypass the frontend version resolution this lane exists to exercise.
 */
export function buildHostArgs(policy, subject, { port, extraArgs = [] } = {}) {
    if (!Number.isInteger(port) || port < 1024 || port > 65535) {
        throw new SubjectError(`port must be an unprivileged integer port, got ${port}`);
    }
    if (!policy.runtime.allowed_bind_hosts.includes(policy.runtime.bind_host)) {
        throw new SubjectError(`bind host ${policy.runtime.bind_host} is not a loopback address`);
    }
    const forbidden = new Set(policy.runtime.forbidden_args);
    for (const arg of extraArgs) {
        if (forbidden.has(arg)) {
            throw new SubjectError(`argument ${arg} is forbidden for this lane`);
        }
        if (arg === "--listen" || arg === "--port") {
            throw new SubjectError(`argument ${arg} is controlled by the lane and may not be supplied`);
        }
    }
    const args = [...policy.runtime.required_args, "--port", String(port)];
    if (subject.front_end_version_arg) {
        args.push("--front-end-version", subject.front_end_version_arg);
    }
    return [...args, ...extraArgs];
}

/**
 * Report every independent reason the observed host is not the requested subject.
 *
 * All three signals are returned together rather than short-circuiting, because
 * a fallback that trips only one of them is the interesting case: it means the
 * lane's other detectors would have missed it.
 *
 * Every input must be something the host reported: the version the browser sees,
 * the host's own log, and the web root parsed out of that log. A caller that
 * passes back the policy value it requested turns the third check into a
 * comparison of one constant against itself, so `resolvedWebRoot` is documented
 * as host-observed and a missing value is treated as a failure rather than a
 * pass.
 */
export function detectSubjectMismatch({
    subject,
    reportedFrontendVersion,
    hostLogText = "",
    resolvedWebRoot = null,
}) {
    const failures = [];

    if (reportedFrontendVersion !== subject.frontend_version) {
        failures.push(
            `browser reported frontend ${reportedFrontendVersion ?? "(none)"}, expected ` +
                `${subject.frontend_version}`,
        );
    }

    if (subject.front_end_version_arg && hostLogText.includes(FRONTEND_FALLBACK_LOG_MARKER)) {
        failures.push(
            `host fell back to its bundled frontend instead of serving ${subject.frontend_version}`,
        );
    }

    if (subject.web_root_relative) {
        if (resolvedWebRoot === null || resolvedWebRoot === "") {
            failures.push(
                "host never reported the web root it resolved, so the served frontend is unverified",
            );
        } else {
            const normalized = String(resolvedWebRoot).replace(/\\/g, "/").replace(/\/+$/, "");
            if (!normalized.endsWith(subject.web_root_relative)) {
                failures.push(
                    `host served web root ${resolvedWebRoot}, expected one ending in ` +
                        subject.web_root_relative,
                );
            }
        }
    }

    return failures;
}

/**
 * The sidebar must reach its floor and keep every control inside the measured
 * boundary. A host or user width already wider than the floor must be preserved,
 * never reduced to it, so the check is a lower bound rather than an equality.
 */
export function evaluateSidebarGeometry(geometry, minWidthPx) {
    const failures = [];
    for (const part of ["panel", "content", "mount"]) {
        const box = geometry?.[part];
        if (!box || typeof box.width !== "number") {
            failures.push(`sidebar ${part} geometry was not measurable`);
            continue;
        }
        if (box.width < minWidthPx) {
            failures.push(`sidebar ${part} measured ${box.width}px, below the ${minWidthPx}px floor`);
        }
    }

    const control = geometry?.rightmostControl;
    const boundary = geometry?.content ?? geometry?.panel;
    if (!control || typeof control.right !== "number") {
        failures.push("rightmost OpenClaw control was not measurable");
    } else if (boundary && typeof boundary.right === "number" && control.right > boundary.right) {
        failures.push(
            `rightmost control ends at ${control.right}px, past the ${boundary.right}px sidebar boundary`,
        );
    }

    return failures;
}

/** Require a connected host input, projection and inner prompt readback. */
export function evaluatePromotedWidget(widget) {
    const failures = [];
    if (!widget || typeof widget !== "object") {
        return ["no promoted widget was read back from the host"];
    }
    const requiredText = [
        "hostNodeId", "innerNodeId", "connectedNodeId", "widgetName",
        "connectedInputName", "hostInputWidgetId", "projectedWidgetId", "productEditWidgetId",
    ];
    for (const field of requiredText) {
        if (typeof widget[field] !== "string" || widget[field].trim() === "") {
            failures.push(`promoted widget ${field} was not read from the host`);
        }
    }
    // IMPORTANT: current host projections do not expose the old source fields.
    // A plain widget can fake those fields; require a real inner link and prompt value.
    if (widget.bindingConnected !== true ||
        widget.connectedNodeId !== widget.innerNodeId ||
        widget.connectedInputName !== widget.widgetName) {
        failures.push("promoted widget has no connected inner widget binding");
    }
    if (widget.hostInputWidgetId !== widget.projectedWidgetId ||
        widget.hostInputWidgetId !== widget.productEditWidgetId) {
        failures.push("promoted widget identifiers disagree across host input, projection and product edit");
    }
    if (widget.editedValue === undefined || widget.promotedValue !== widget.editedValue) {
        failures.push("OpenClaw edit did not reach the promoted widget value");
    }
    if (widget.editedValue === undefined || widget.promptValue !== widget.editedValue) {
        failures.push("OpenClaw edit did not reach the serialized inner prompt input");
    }
    return failures;
}

/**
 * An annotated temporary result must stay visible and must be fetched from the
 * host's temporary directory, not from its permanent output directory.
 */
export function evaluateAnnotatedTempResult(result) {
    const failures = [];
    if (!result || typeof result.viewUrl !== "string" || result.viewUrl === "") {
        return ["annotated temp result produced no view link"];
    }
    let parsed;
    try {
        parsed = new URL(result.viewUrl, "http://127.0.0.1");
    } catch {
        return [`annotated temp result view link is not a URL: ${result.viewUrl}`];
    }
    if (parsed.searchParams.get("type") !== "temp") {
        failures.push(
            `annotated temp result requested type=${parsed.searchParams.get("type") ?? "(none)"}, expected temp`,
        );
    }
    if (result.visible !== true) {
        failures.push("annotated temp result was not visible in the job monitor");
    }
    return failures;
}

/**
 * Compatibility evidence may only advance from a run that actually happened.
 *
 * The tracked matrix rejects a validated state without a run identifier and a
 * pending state that names one; this mirrors that rule at the point the lane
 * would write, so a lane bug cannot produce a document the governance check has
 * to catch afterwards.
 */
export function evidenceUpdateIsAllowed(policy, { state, runId, evidenceId }) {
    const failures = [];
    const requiresRun = policy.evidence.states_requiring_run_id.includes(state);
    const hasRun = typeof runId === "string" && runId.trim() !== "";
    const hasEvidenceId = typeof evidenceId === "string" && evidenceId.trim() !== "";

    if (requiresRun && !hasRun) {
        failures.push(`evidence state ${state} requires a run identifier`);
    }
    if (!requiresRun && hasRun) {
        failures.push(`evidence state ${state} must not name a run identifier`);
    }
    if (requiresRun && !hasEvidenceId) {
        failures.push(`evidence state ${state} requires an evidence identifier`);
    }
    if (!requiresRun && hasEvidenceId) {
        failures.push(`evidence state ${state} must not name an evidence identifier`);
    }
    return { allowed: failures.length === 0, failures };
}

/**
 * Split browser errors into the ones this product is answerable for and the rest.
 *
 * The lane was written for a host holding only OpenClaw and the peer fixture, where
 * "no console errors at all" is the right assertion. On a real installation the host
 * also loads whatever else the user installed, and those packs are noisy: missing
 * assets, double registration, and their own exceptions. Failing on those reports a
 * problem that is not the product's, which makes the check useless exactly where it
 * would be most valuable.
 *
 * The assertion is therefore scoped rather than dropped. An error is attributable
 * when it names the path this host serves OpenClaw's own modules from, or names the
 * peer fixture the lane installed; everything else is returned separately so the run
 * can still record it as context. Anything that cannot be attributed either way is
 * treated as attributable, because an unexplained error in a lane that owns the page
 * is not something to discard silently.
 */
export function partitionBrowserErrors(errors, { extensionBase, peerBase = "", policy } = {}) {
    if (!extensionBase) {
        throw new SubjectError(
            "partitionBrowserErrors needs the extension base the host actually serves; " +
                "guessing it is how the hardcoded-path defect happened",
        );
    }
    const ours = [];
    const foreign = [];
    const host = [];
    const echoes = [];
    for (const raw of errors ?? []) {
        const text = String(raw);
        // Only once a policy is supplied is there a request classifier to defer
        // to. Without one, nothing here may be set aside, because the caller has
        // given this function no second place for the failure to be judged.
        if (policy) {
            if (isContentlessSubresourceEcho(text)) {
                echoes.push(text);
                continue;
            }
            if (isHostOwnedConsoleMessage(policy, text)) {
                host.push(text);
                continue;
            }
        }
        const namesAnotherExtension =
            /\/extensions\/[^/\s"']+/.test(text) &&
            !text.includes(extensionBase) &&
            !(peerBase && text.includes(peerBase));
        if (namesAnotherExtension) {
            foreign.push(text);
        } else if (text.includes(extensionBase) || (peerBase && text.includes(peerBase))) {
            ours.push(text);
        } else if (/\bopenclaw\b/i.test(text)) {
            ours.push(text);
        } else {
            // Unattributable. Kept on the failing side on purpose.
            ours.push(text);
        }
    }
    return { ours, foreign, host, echoes };
}

/**
 * The console text a browser emits when a subresource fails.
 *
 * It carries no URL. That is the whole reason this module classifies requests
 * rather than console prose: the only object that knows which file failed is the
 * network event, and judging the console echo as well would count one failure
 * twice while still being unable to say what it was.
 */
const CONTENTLESS_SUBRESOURCE_ECHO = /^Failed to load resource:/;

export function isContentlessSubresourceEcho(text) {
    return CONTENTLESS_SUBRESOURCE_ECHO.test(String(text ?? "").trim());
}

function pathOf(url) {
    const raw = String(url ?? "");
    try {
        return new URL(raw).pathname;
    } catch {
        // Not absolute. Take everything before the query, which is what the
        // pinned entries are written against.
        const cut = raw.search(/[?#]/);
        return cut === -1 ? raw : raw.slice(0, cut);
    }
}

function hostOwnedRequestPaths(policy) {
    const entries = policy?.host_owned_noise?.requests ?? [];
    return entries.map((entry) => String(entry?.path ?? "")).filter(Boolean);
}

/**
 * Failure kinds that are the browser giving up on a request, not a server answering badly.
 *
 * Declared as kinds rather than as paths on purpose. The path allowlist already
 * excused two `net::ERR_ABORTED` entries, but only incidentally: it matches on
 * path, and those two paths happened to be listed for their 404s. A third core
 * path aborted on the first CI run and was charged to this product. A list that
 * enumerates instances is always one instance short, so the kind is pinned
 * instead of a third path.
 */
function hostOwnedAbortKinds(policy) {
    const entries = policy?.host_owned_noise?.aborted_request_kinds ?? [];
    return entries.map((entry) => String(entry?.error_text ?? "")).filter(Boolean);
}

/**
 * Recognise a frontend log line the host emits about itself.
 *
 * These have no companion request and name no extension, so exact text is the
 * only signal there is. Matching is exact rather than substring: a message that
 * merely quotes one of these while reporting something else is not excused.
 */
export function isHostOwnedConsoleMessage(policy, text) {
    const subject = String(text ?? "").trim();
    if (!subject) {
        return false;
    }
    const entries = policy?.host_owned_noise?.console_messages ?? [];
    return entries.some((entry) => String(entry?.text ?? "").trim() === subject);
}

/**
 * Decide who owns each failed request.
 *
 * Ownership is settled before the host allowlist is ever consulted, so a URL
 * under this product's extension base is ours no matter what the allowlist says.
 * That ordering is what makes a crafted path such as
 * `/extensions/ComfyUI-OpenClaw/api/userdata/user.css` safe by construction
 * rather than by the allowlist patterns being written carefully.
 */
function pathAndQueryOf(url) {
    const raw = String(url ?? "");
    try {
        const parsed = new URL(raw);
        return `${parsed.pathname}${parsed.search}`;
    } catch {
        const cut = raw.indexOf("#");
        return cut === -1 ? raw : raw.slice(0, cut);
    }
}

export function classifyFailedRequests(
    requests,
    { extensionBase, peerBase = "", policy, expectedUrls = [] } = {},
) {
    if (!extensionBase) {
        throw new SubjectError(
            "classifyFailedRequests needs the extension base the host actually serves; " +
                "guessing it is how the hardcoded-path defect happened",
        );
    }
    const hostPaths = hostOwnedRequestPaths(policy);
    const abortKinds = new Set(hostOwnedAbortKinds(policy));
    // A check may deliberately provoke a request it knows will fail - probing
    // which directory the host addresses, for instance. Such a request is
    // declared by the check that causes it, matched whole including its query,
    // and scoped to that check alone. That is narrower than any allowlist: it
    // cannot excuse a failure nobody asked for.
    const expected = new Set(
        (expectedUrls ?? []).map((entry) => pathAndQueryOf(entry)).filter(Boolean),
    );
    const ours = [];
    const foreign = [];
    const host = [];
    const declared = [];

    for (const request of requests ?? []) {
        const url = typeof request === "string" ? request : String(request?.url ?? "");
        const label = typeof request === "string" ? url : String(request?.label ?? url);
        const errorText = typeof request === "string" ? "" : String(request?.errorText ?? "");
        const path = pathOf(url);

        if (expected.has(pathAndQueryOf(url))) {
            declared.push(label);
            continue;
        }
        if (path.startsWith(extensionBase) || (peerBase && path.startsWith(peerBase))) {
            ours.push(label);
            continue;
        }
        const otherExtension = /^\/extensions\/[^/]+\//.exec(path);
        if (otherExtension) {
            foreign.push(label);
            continue;
        }
        // Only reachable once the request is known not to be ours: an abort on one
        // of our own modules is still our failure. Matched on the structured
        // failure text the browser reported, never on the formatted label - the
        // same reason this classifier stopped reading console prose.
        if (errorText && abortKinds.has(errorText)) {
            host.push(label);
            continue;
        }
        // Only now may the allowlist speak. Equality or a path-segment boundary,
        // never a bare substring, so `/api/userdata-of-ours` is not excused by an
        // entry pinning `/api/userdata`.
        const excused = hostPaths.some(
            (pinned) => path === pinned || path.startsWith(`${pinned}/`),
        );
        if (excused) {
            host.push(label);
            continue;
        }
        // Unattributable. Kept on the failing side on purpose.
        ours.push(label);
    }

    return { ours, foreign, host, declared };
}
