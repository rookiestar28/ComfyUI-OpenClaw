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
 * The digest is deliberately unset in the tracked policy: pinning it requires
 * downloading the asset, which is an outward-facing fetch this repository does
 * not perform without authorization. Failing closed keeps an unauthorized or
 * unverified artifact from ever being presented as release evidence.
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
 * The bind address is passed explicitly rather than left to the host default,
 * because the host accepts a bare `--listen` that means every interface; relying
 * on a default would make the lane's exposure depend on an upstream choice. Any
 * argument the policy forbids, and any second `--listen`, is rejected rather
 * than filtered, so a caller cannot widen exposure or bypass the frontend
 * version resolution this lane exists to exercise.
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
            throw new SubjectError(`argument ${arg} is already set by the lane and may not be repeated`);
        }
    }
    const args = [
        ...policy.runtime.required_args,
        "--port",
        String(port),
        "--listen",
        policy.runtime.bind_host,
    ];
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

/**
 * A promoted widget read back from a real host must carry the host's own
 * identifiers. Empty or placeholder source fields mean the smoke fabricated the
 * link rather than observing one, which is the failure this check exists for.
 */
export function evaluatePromotedWidget(widget) {
    const failures = [];
    if (!widget || typeof widget !== "object") {
        return ["no promoted widget was read back from the host"];
    }
    for (const field of ["sourceNodeId", "sourceWidgetName"]) {
        const value = widget[field];
        if (typeof value !== "string" || value.trim() === "") {
            failures.push(`promoted widget ${field} was not populated by the host`);
            continue;
        }
        if (/^(?:unknown|placeholder|test|fake|mock)/i.test(value.trim())) {
            failures.push(`promoted widget ${field} looks fabricated: ${value}`);
        }
    }
    if (failures.length === 0 && widget.value === undefined) {
        failures.push("promoted widget carried no value to write back");
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
