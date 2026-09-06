import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
    BUNDLED_SUBJECT,
    FRONTEND_FALLBACK_LOG_MARKER,
    STANDALONE_RELEASE_SUBJECT,
    SubjectError,
    assertSubjectRunnable,
    buildHostArgs,
    detectSubjectMismatch,
    evaluateAnnotatedTempResult,
    evaluatePromotedWidget,
    evaluateSidebarGeometry,
    evidenceUpdateIsAllowed,
    classifyFailedRequests,
    isContentlessSubresourceEcho,
    isHostOwnedConsoleMessage,
    partitionBrowserErrors,
    parseHostWebRoot,
    resolveSubject,
} from "../../../tests/real_host/helpers/real_host_subjects.js";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const POLICY = JSON.parse(
    readFileSync(path.join(REPO_ROOT, "tests", "real_host_smoke_policy.json"), "utf-8"),
);

describe("real host frontend subjects", () => {
    it("resolves the two pinned subjects and rejects anything else", () => {
        expect(resolveSubject(POLICY, BUNDLED_SUBJECT).frontend_version).toBe("1.51.9");
        expect(resolveSubject(POLICY, STANDALONE_RELEASE_SUBJECT).frontend_version).toBe("1.54.3");
        expect(() => resolveSubject(POLICY, "nightly")).toThrow(SubjectError);
    });

    it("carries a pinned, well-formed release digest in the tracked policy", () => {
        const subject = resolveSubject(POLICY, STANDALONE_RELEASE_SUBJECT);

        expect(subject.release_asset_sha256).toMatch(/^[0-9a-f]{64}$/);
        expect(() => assertSubjectRunnable(subject)).not.toThrow();
    });

    it("still refuses a release subject whose digest is missing", () => {
        // The pin can be removed and a later subject may arrive without one, so
        // the refusal stays covered independently of today's policy value.
        const subject = resolveSubject(POLICY, STANDALONE_RELEASE_SUBJECT);

        expect(() => assertSubjectRunnable({ ...subject, release_asset_sha256: null })).toThrow(
            /no pinned sha256/,
        );
    });

    it("accepts the release subject once a real digest is pinned, and only then", () => {
        const base = resolveSubject(POLICY, STANDALONE_RELEASE_SUBJECT);

        expect(() =>
            assertSubjectRunnable({ ...base, release_asset_sha256: "a".repeat(64) }),
        ).not.toThrow();
        for (const bad of ["", "not-a-digest", "A".repeat(64), "a".repeat(63)]) {
            expect(() => assertSubjectRunnable({ ...base, release_asset_sha256: bad })).toThrow(
                SubjectError,
            );
        }
    });

    it("never gates the bundled subject on a release digest it does not have", () => {
        expect(() => assertSubjectRunnable(resolveSubject(POLICY, BUNDLED_SUBJECT))).not.toThrow();
    });
});

describe("real host startup arguments", () => {
    it("binds loopback and cpu explicitly and requests the release only for that subject", () => {
        const bundled = buildHostArgs(POLICY, resolveSubject(POLICY, BUNDLED_SUBJECT), {
            port: 18188,
        });
        const release = buildHostArgs(POLICY, resolveSubject(POLICY, STANDALONE_RELEASE_SUBJECT), {
            port: 18189,
        });

        expect(bundled).toEqual([
            "--cpu",
            "--disable-auto-launch",
            "--port",
            "18188",
            "--listen",
            "127.0.0.1",
        ]);
        expect(bundled).not.toContain("--front-end-version");
        expect(release.slice(-2)).toEqual([
            "--front-end-version",
            "Comfy-Org/ComfyUI_frontend@v1.54.3",
        ]);
    });

    it("rejects arguments that would widen exposure or bypass version resolution", () => {
        const subject = resolveSubject(POLICY, BUNDLED_SUBJECT);

        for (const arg of ["--enable-cors-header", "--tls-keyfile", "--front-end-root"]) {
            expect(() => buildHostArgs(POLICY, subject, { port: 18188, extraArgs: [arg] })).toThrow(
                /forbidden/,
            );
        }
        for (const arg of ["--listen", "--port"]) {
            expect(() => buildHostArgs(POLICY, subject, { port: 18188, extraArgs: [arg] })).toThrow(
                /may not be repeated/,
            );
        }
    });

    it("rejects a non-loopback bind host and a privileged or invalid port", () => {
        const subject = resolveSubject(POLICY, BUNDLED_SUBJECT);
        const exposed = { ...POLICY, runtime: { ...POLICY.runtime, bind_host: "0.0.0.0" } };

        expect(() => buildHostArgs(exposed, subject, { port: 18188 })).toThrow(/not a loopback/);
        for (const port of [80, 0, -1, 70000, 8188.5, "8188"]) {
            expect(() => buildHostArgs(POLICY, subject, { port })).toThrow(SubjectError);
        }
    });
});

describe("real host subject identity", () => {
    const release = () => ({
        ...JSON.parse(JSON.stringify(POLICY.subjects.standalone_release)),
    });

    it("passes only when every identity signal agrees", () => {
        expect(
            detectSubjectMismatch({
                subject: release(),
                reportedFrontendVersion: "1.54.3",
                hostLogText: "Using existing copy of specific frontend version tag",
                resolvedWebRoot: "/tmp/comfy/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.3",
            }),
        ).toEqual([]);
    });

    it("fails the release subject when the host silently served the bundled frontend", () => {
        const failures = detectSubjectMismatch({
            subject: release(),
            reportedFrontendVersion: "1.51.9",
            hostLogText: `Failed to initialize frontend: timeout\n${FRONTEND_FALLBACK_LOG_MARKER}`,
            resolvedWebRoot: "/tmp/comfy/comfyui_frontend_package/static",
        });

        expect(failures).toHaveLength(3);
        expect(failures.join("\n")).toMatch(/browser reported frontend 1\.51\.9/);
        expect(failures.join("\n")).toMatch(/fell back to its bundled frontend/);
        expect(failures.join("\n")).toMatch(/expected one ending in/);
    });

    it("still fails when only one signal trips, which is the case that matters", () => {
        const goodRoot = "/tmp/comfy/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.3";

        expect(
            detectSubjectMismatch({
                subject: release(),
                reportedFrontendVersion: "1.54.3",
                hostLogText: FRONTEND_FALLBACK_LOG_MARKER,
                resolvedWebRoot: goodRoot,
            }),
        ).toEqual([`host fell back to its bundled frontend instead of serving 1.54.3`]);

        expect(
            detectSubjectMismatch({
                subject: release(),
                reportedFrontendVersion: "1.54.3",
                hostLogText: "",
                resolvedWebRoot: "/tmp/comfy/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.2",
            }),
        ).toHaveLength(1);
    });

    it("accepts a windows web root and reports a missing version as a mismatch", () => {
        expect(
            detectSubjectMismatch({
                subject: release(),
                reportedFrontendVersion: "1.54.3",
                hostLogText: "",
                resolvedWebRoot: "C:\\r\\web_custom_versions\\Comfy-Org_ComfyUI_frontend\\1.54.3",
            }),
        ).toEqual([]);

        expect(
            detectSubjectMismatch({
                subject: release(),
                reportedFrontendVersion: null,
                hostLogText: "",
                resolvedWebRoot: "/tmp/comfy/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.3",
            }),
        ).toEqual(["browser reported frontend (none), expected 1.54.3"]);
    });

    it("does not apply release-only checks to the bundled subject", () => {
        expect(
            detectSubjectMismatch({
                subject: POLICY.subjects.bundled,
                reportedFrontendVersion: "1.51.9",
                hostLogText: FRONTEND_FALLBACK_LOG_MARKER,
                resolvedWebRoot: null,
            }),
        ).toEqual([]);
    });
});

describe("real host web root observation", () => {
    it("reads the web root the host actually reported", () => {
        const log = [
            "[Prompt Server] some earlier line",
            "[Prompt Server] web root: /tmp/c/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.3",
            "later noise",
        ].join("\n");

        expect(parseHostWebRoot(log)).toBe(
            "/tmp/c/web_custom_versions/Comfy-Org_ComfyUI_frontend/1.54.3",
        );
    });

    it("takes the last report and tolerates carriage returns", () => {
        const log = "[Prompt Server] web root: /first\r\n[Prompt Server] web root: /second\r\n";

        expect(parseHostWebRoot(log)).toBe("/second");
    });

    it("returns null when the host never reported one", () => {
        for (const log of ["", "nothing here", "[Prompt Server] web root:   ", null, undefined]) {
            expect(parseHostWebRoot(log)).toBeNull();
        }
    });

    it("treats an unreported web root as a failure, never as a pass", () => {
        const release = JSON.parse(JSON.stringify(POLICY.subjects.standalone_release));
        const failures = detectSubjectMismatch({
            subject: release,
            reportedFrontendVersion: "1.54.3",
            hostLogText: "",
            resolvedWebRoot: parseHostWebRoot(""),
        });

        expect(failures).toEqual([
            "host never reported the web root it resolved, so the served frontend is unverified",
        ]);
    });
});

describe("real host sidebar geometry", () => {
    const floor = POLICY.geometry.sidebar_min_width_px;
    const box = (left, width) => ({ left, width, right: left + width });

    it("accepts the floor and accepts a wider host width without reducing it", () => {
        for (const width of [floor, floor + 240]) {
            expect(
                evaluateSidebarGeometry(
                    {
                        panel: box(0, width),
                        content: box(0, width),
                        mount: box(0, width),
                        rightmostControl: box(width - 80, 72),
                    },
                    floor,
                ),
            ).toEqual([]);
        }
    });

    it("fails each element that falls below the floor", () => {
        const failures = evaluateSidebarGeometry(
            {
                panel: box(0, floor - 1),
                content: box(0, 320),
                mount: box(0, floor),
                rightmostControl: box(0, 40),
            },
            floor,
        );

        expect(failures).toHaveLength(2);
        expect(failures[0]).toMatch(/panel measured 559px/);
        expect(failures[1]).toMatch(/content measured 320px/);
    });

    it("fails a control that overflows the measured boundary", () => {
        const failures = evaluateSidebarGeometry(
            {
                panel: box(0, floor),
                content: box(0, floor),
                mount: box(0, floor),
                rightmostControl: box(floor - 20, 60),
            },
            floor,
        );

        expect(failures).toEqual([
            `rightmost control ends at ${floor + 40}px, past the ${floor}px sidebar boundary`,
        ]);
    });

    it("treats unmeasurable geometry as failure rather than success", () => {
        expect(evaluateSidebarGeometry({}, floor)).toEqual([
            "sidebar panel geometry was not measurable",
            "sidebar content geometry was not measurable",
            "sidebar mount geometry was not measurable",
            "rightmost OpenClaw control was not measurable",
        ]);
        expect(evaluateSidebarGeometry(null, floor)).toHaveLength(4);
    });
});

describe("real host promoted widget", () => {
    it("accepts identifiers the host actually assigned", () => {
        expect(
            evaluatePromotedWidget({ sourceNodeId: "14", sourceWidgetName: "seed", value: 7 }),
        ).toEqual([]);
    });

    it("rejects missing, blank, and fabricated identifiers", () => {
        expect(evaluatePromotedWidget(null)).toEqual([
            "no promoted widget was read back from the host",
        ]);
        expect(evaluatePromotedWidget({ sourceNodeId: "14", value: 1 })).toEqual([
            "promoted widget sourceWidgetName was not populated by the host",
        ]);
        expect(
            evaluatePromotedWidget({ sourceNodeId: "  ", sourceWidgetName: "seed", value: 1 }),
        ).toEqual(["promoted widget sourceNodeId was not populated by the host"]);
        expect(
            evaluatePromotedWidget({
                sourceNodeId: "mock-node-1",
                sourceWidgetName: "placeholder",
                value: 1,
            }),
        ).toHaveLength(2);
    });

    it("rejects a widget with no value to write back", () => {
        expect(evaluatePromotedWidget({ sourceNodeId: "14", sourceWidgetName: "seed" })).toEqual([
            "promoted widget carried no value to write back",
        ]);
    });
});

describe("real host annotated temp result", () => {
    it("accepts a visible result whose view link requests the temp directory", () => {
        expect(
            evaluateAnnotatedTempResult({
                viewUrl: "/api/view?filename=scene.glb&type=temp&subfolder=",
                visible: true,
            }),
        ).toEqual([]);
    });

    it("rejects a result served from the output directory or hidden from the monitor", () => {
        expect(
            evaluateAnnotatedTempResult({
                viewUrl: "/api/view?filename=scene.glb&type=output",
                visible: true,
            }),
        ).toEqual(["annotated temp result requested type=output, expected temp"]);
        expect(
            evaluateAnnotatedTempResult({ viewUrl: "/api/view?filename=scene.glb", visible: false }),
        ).toHaveLength(2);
        expect(evaluateAnnotatedTempResult({ viewUrl: "" })).toEqual([
            "annotated temp result produced no view link",
        ]);
    });
});

describe("real host evidence gating", () => {
    it("allows a validated refresh only with both identifiers", () => {
        expect(
            evidenceUpdateIsAllowed(POLICY, {
                state: "validated",
                runId: "real-host-smoke-1234567890",
                evidenceId: "real-host-smoke-20260906",
            }).allowed,
        ).toBe(true);

        expect(
            evidenceUpdateIsAllowed(POLICY, {
                state: "validated",
                runId: "",
                evidenceId: "real-host-smoke-20260906",
            }).failures,
        ).toEqual(["evidence state validated requires a run identifier"]);
    });

    it("refuses a pending state that names a run, which is how a fabricated run would enter", () => {
        const result = evidenceUpdateIsAllowed(POLICY, {
            state: "pending",
            runId: "real-host-smoke-1234567890",
            evidenceId: "real-host-smoke-20260906",
        });

        expect(result.allowed).toBe(false);
        expect(result.failures).toEqual([
            "evidence state pending must not name a run identifier",
            "evidence state pending must not name an evidence identifier",
        ]);
    });

    it("accepts the state the repository currently ships", () => {
        expect(
            evidenceUpdateIsAllowed(POLICY, {
                state: POLICY.evidence.current_state,
                runId: null,
                evidenceId: null,
            }).allowed,
        ).toBe(true);
        expect(POLICY.evidence.current_state).toBe("pending");
    });
});

describe("browser error attribution", () => {
    const BASE = "/extensions/ComfyUI-OpenClaw";
    const PEER = "/extensions/openclaw-smoke-peer";

    it("refuses to guess the extension base", () => {
        expect(() => partitionBrowserErrors(["anything"], {})).toThrow(SubjectError);
    });

    it("keeps this product's own errors on the failing side", () => {
        const { ours, foreign } = partitionBrowserErrors(
            [
                `Failed to fetch dynamically imported module: http://h${BASE}/openclaw_ui.js`,
                "TypeError: openclaw sidebar mount is undefined",
            ],
            { extensionBase: BASE, peerBase: PEER },
        );

        expect(ours).toHaveLength(2);
        expect(foreign).toEqual([]);
    });

    it("sets other node packs aside instead of failing on them", () => {
        const { ours, foreign } = partitionBrowserErrors(
            [
                "Failed to fetch: http://h/extensions/ComfyUI-Impact-Pack/impact-sam-editor.js",
                "Failed to fetch: http://h/extensions/comfyui-rookieui/tests/helpers/x.js",
            ],
            { extensionBase: BASE, peerBase: PEER },
        );

        expect(ours).toEqual([]);
        expect(foreign).toHaveLength(2);
    });

    it("treats the peer fixture as part of this lane, not a stranger", () => {
        const { ours, foreign } = partitionBrowserErrors(
            [`Failed to fetch: http://h${PEER}/peer_sidebar_tab.js`],
            { extensionBase: BASE, peerBase: PEER },
        );

        expect(ours).toHaveLength(1);
        expect(foreign).toEqual([]);
    });

    it("keeps an unattributable error on the failing side", () => {
        // A bare error naming no extension could be the product's own. Discarding
        // it would be how a real regression disappears into third-party noise.
        const { ours, foreign } = partitionBrowserErrors(["Uncaught RangeError: bad index"], {
            extensionBase: BASE,
            peerBase: PEER,
        });

        expect(ours).toEqual(["Uncaught RangeError: bad index"]);
        expect(foreign).toEqual([]);
    });
});

describe("failed request attribution", () => {
    const BASE = "/extensions/ComfyUI-OpenClaw";
    const PEER = "/extensions/openclaw-smoke-peer";
    const options = { extensionBase: BASE, peerBase: PEER, policy: POLICY };

    it("refuses to classify without the base the host actually serves", () => {
        expect(() => classifyFailedRequests(["/anything"], { policy: POLICY })).toThrow(
            SubjectError,
        );
    });

    it("excuses the host's own optional-file requests, which the console text cannot name", () => {
        const { ours, host, foreign } = classifyFailedRequests(
            [
                "http://127.0.0.1:8199/api/userdata/user.css",
                "http://127.0.0.1:8199/user.css",
                "http://127.0.0.1:8199/api/userdata/comfy.templates.json",
                "http://127.0.0.1:8199/api/userdata?dir=subgraphs&recurse=true",
            ],
            options,
        );
        expect(ours).toEqual([]);
        expect(foreign).toEqual([]);
        expect(host).toHaveLength(4);
    });

    it("never excuses one of our own requests, even dressed as a host route", () => {
        // The adversarial case the roadmap named: a URL whose tail matches a
        // pinned host entry but which is served from our own extension base.
        const { ours, host } = classifyFailedRequests(
            [`http://127.0.0.1:8199${BASE}/api/userdata/user.css`],
            options,
        );
        expect(host).toEqual([]);
        expect(ours).toHaveLength(1);
    });

    it("does not excuse a path that merely starts with a pinned one", () => {
        const { ours, host } = classifyFailedRequests(
            ["http://127.0.0.1:8199/api/userdata-of-ours/secret.json"],
            options,
        );
        expect(host).toEqual([]);
        expect(ours).toHaveLength(1);
    });

    it("sets another pack's failed request aside", () => {
        const { ours, foreign } = classifyFailedRequests(
            ["http://127.0.0.1:8199/extensions/some-other-pack/thing.js"],
            options,
        );
        expect(ours).toEqual([]);
        expect(foreign).toHaveLength(1);
    });

    it("keeps an unattributable failed request on the failing side", () => {
        const { ours, host, foreign } = classifyFailedRequests(
            ["http://127.0.0.1:8199/some/route/nobody/claims"],
            options,
        );
        expect(host).toEqual([]);
        expect(foreign).toEqual([]);
        expect(ours).toHaveLength(1);
    });

    it("treats the peer fixture's own requests as ours, not as another pack", () => {
        const { ours, foreign } = classifyFailedRequests(
            [`http://127.0.0.1:8199${PEER}/peer_sidebar_tab.js`],
            options,
        );
        expect(foreign).toEqual([]);
        expect(ours).toHaveLength(1);
    });
});

describe("console messages the host owns", () => {
    it("recognises the contentless subresource echo, which names no file", () => {
        expect(
            isContentlessSubresourceEcho(
                "Failed to load resource: the server responded with a status of 404 (Not Found)",
            ),
        ).toBe(true);
        expect(isContentlessSubresourceEcho("Uncaught TypeError: x is not a function")).toBe(
            false,
        );
    });

    it("matches a pinned host message exactly, not by substring", () => {
        expect(
            isHostOwnedConsoleMessage(POLICY, "ComfyApp graph accessed before initialization"),
        ).toBe(true);
        expect(
            isHostOwnedConsoleMessage(
                POLICY,
                "OpenClaw broke because ComfyApp graph accessed before initialization",
            ),
        ).toBe(false);
    });

    it("defers the echo to the request classifier and sets the host's own line aside", () => {
        const { ours, host, echoes } = partitionBrowserErrors(
            [
                "Failed to load resource: the server responded with a status of 404 (Not Found)",
                "ComfyApp graph accessed before initialization",
                "Uncaught RangeError: bad index",
            ],
            { extensionBase: "/extensions/ComfyUI-OpenClaw", policy: POLICY },
        );
        expect(echoes).toHaveLength(1);
        expect(host).toEqual(["ComfyApp graph accessed before initialization"]);
        expect(ours).toEqual(["Uncaught RangeError: bad index"]);
    });

    it("sets nothing aside when no policy is supplied, because there is no second judge", () => {
        const { ours, host, echoes } = partitionBrowserErrors(
            [
                "Failed to load resource: the server responded with a status of 404 (Not Found)",
                "ComfyApp graph accessed before initialization",
            ],
            { extensionBase: "/extensions/ComfyUI-OpenClaw" },
        );
        expect(host).toEqual([]);
        expect(echoes).toEqual([]);
        expect(ours).toHaveLength(2);
    });
});

describe("probes a check declares for itself", () => {
    const BASE = "/extensions/ComfyUI-OpenClaw";
    const PROBE = "/api/view?filename=scene.glb&type=temp";

    it("sets aside exactly the request the check said it would provoke", () => {
        const { ours, declared } = classifyFailedRequests(
            [{ url: `http://127.0.0.1:8199${PROBE}`, label: `404 HEAD ${PROBE}` }],
            { extensionBase: BASE, policy: POLICY, expectedUrls: [PROBE] },
        );
        expect(ours).toEqual([]);
        expect(declared).toHaveLength(1);
    });

    it("matches the whole request including its query, not just the route", () => {
        // Declaring the temp probe must not excuse a different view request that
        // happens to hit the same route.
        const { ours, declared } = classifyFailedRequests(
            ["http://127.0.0.1:8199/api/view?filename=real_output.png&type=output"],
            { extensionBase: BASE, policy: POLICY, expectedUrls: [PROBE] },
        );
        expect(declared).toEqual([]);
        expect(ours).toHaveLength(1);
    });

    it("excuses nothing when a check declares nothing", () => {
        const { ours, declared } = classifyFailedRequests(
            [`http://127.0.0.1:8199${PROBE}`],
            { extensionBase: BASE, policy: POLICY },
        );
        expect(declared).toEqual([]);
        expect(ours).toHaveLength(1);
    });
});
