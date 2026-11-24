import { describe, expect, it, vi } from "vitest";
import {
    HOST_CORE_REFERENCE,
    HOST_REAL_VALIDATION_STATE,
    HOST_SURFACES,
    HOST_SURFACE_REFERENCES,
    OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES,
    acquireHostSurfaceMetadata,
    getHostSurfaceCapabilities,
    resolveHostSurface,
    stampHostSurfaceMetadata,
} from "../../../web/openclaw_host_surface.js";

// A reviewed frontend head and its release may be one commit only when the describe is
// exactly the release tag; a head ahead of its release needs a distinct revision and a
// "<tag>-N-g<sha>" describe. Returns the rules the pair breaks.
function headReleaseViolations(reference) {
    const sameRevision = reference.sourceRevision === reference.releaseRevision;
    const exactTag = reference.sourceDescribe === reference.releaseTag;
    const aheadDescribe = reference.sourceDescribe.startsWith(`${reference.releaseTag}-`);
    const violations = [];
    if (sameRevision && !exactTag) violations.push("collapsed");
    if (exactTag && !sameRevision) violations.push("exact_tag_with_distinct_head");
    if (!sameRevision && !exactTag && !aheadDescribe) {
        violations.push("distinct_head_without_commits_ahead_describe");
    }
    return violations;
}

describe("openclaw_host_surface", () => {
    it("publishes exact standalone and two-generation desktop references", () => {
        expect(HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend]).toEqual({
            frontendVersion: "1.61.8",
            sourceRevision: "388973acb5",
            sourceDescribe: "v1.61.8",
            releaseVersion: "1.61.8",
            releaseTag: "v1.61.8",
            releaseRevision: "388973acb5",
        });
        expect(HOST_SURFACE_REFERENCES[HOST_SURFACES.desktop]).toEqual({
            desktopVersion: "0.9.4",
            coreVersion: "0.22.3",
            embeddedFrontendVersion: "1.43.18",
            standaloneFrontendVersion: "1.61.8",
            frontendParity: "lagging",
            generation: "legacy_fixed_bundle",
            hostedVersionMode: "fixed",
        });
        expect(HOST_SURFACE_REFERENCES[HOST_SURFACES.comfyDesktop]).toEqual({
            desktopVersion: "1.0.32-rc.1",
            sourceRevision: "85e28b7a",
            sourceDescribe: "v1.0.32-rc.1-3-g85e28b7",
            generation: "managed_install",
            hostedVersionMode: "installation_specific",
            coreVersion: null,
            frontendVersion: null,
        });
    });

    it("treats electron bridge presence as desktop host surface", () => {
        const hostSurface = resolveHostSurface({
            win: { electronAPI: { getPlatform() {} } },
        });
        expect(hostSurface).toBe(HOST_SURFACES.desktop);
    });

    it.each([
        {
            label: "explicit standalone over both bridges",
            app: { openclawHostSurface: "standalone_frontend" },
            win: { __comfyDesktop2: {}, electronAPI: {} },
            expected: HOST_SURFACES.standaloneFrontend,
        },
        {
            label: "explicit current Desktop over legacy bridge",
            app: { hostSurface: "current_desktop" },
            win: { electronAPI: {} },
            expected: HOST_SURFACES.comfyDesktop,
        },
        {
            label: "legacy desktop compatibility alias over current bridge",
            app: { openclawHostSurface: "desktop" },
            win: { __comfyDesktop2: {} },
            expected: HOST_SURFACES.desktop,
        },
        {
            label: "window explicit managed-install hint",
            app: null,
            win: { __OPENCLAW_HOST_SURFACE__: "managed_install", electronAPI: {} },
            expected: HOST_SURFACES.comfyDesktop,
        },
        {
            label: "localhost distribution over current bridge",
            app: null,
            win: { __DISTRIBUTION__: "localhost", __comfyDesktop2: {} },
            expected: HOST_SURFACES.standaloneFrontend,
        },
        {
            label: "current distribution over legacy bridge",
            app: null,
            win: { __DISTRIBUTION__: "comfy_desktop", electronAPI: {} },
            expected: HOST_SURFACES.comfyDesktop,
        },
        {
            label: "legacy distribution over current bridge",
            app: null,
            win: { __DISTRIBUTION__: "desktop", __comfyDesktop2: {} },
            expected: HOST_SURFACES.desktop,
        },
        {
            label: "current bridge over legacy bridge",
            app: null,
            win: { __comfyDesktop2: {}, electronAPI: {} },
            expected: HOST_SURFACES.comfyDesktop,
        },
        {
            label: "legacy bridge",
            app: null,
            win: { electronAPI: {} },
            expected: HOST_SURFACES.desktop,
        },
        {
            label: "unknown hint continues to current bridge",
            app: { openclawHostSurface: "future-host" },
            win: { __comfyDesktop2: {} },
            expected: HOST_SURFACES.comfyDesktop,
        },
        {
            label: "no signal",
            app: null,
            win: {},
            expected: HOST_SURFACES.standaloneFrontend,
        },
    ])("uses deterministic precedence for $label", ({ app, win, expected }) => {
        expect(resolveHostSurface({ app, win })).toBe(expected);
    });

    it("recognizes the current bridge without reading or invoking its members", () => {
        const isRemote = vi.fn(() => false);
        const reads = [];
        const bridge = new Proxy(
            { isRemote },
            {
                get(target, property, receiver) {
                    reads.push(String(property));
                    return Reflect.get(target, property, receiver);
                },
            }
        );

        expect(resolveHostSurface({ win: { __comfyDesktop2: bridge } })).toBe(
            HOST_SURFACES.comfyDesktop
        );
        expect(reads).toEqual([]);
        expect(isRemote).not.toHaveBeenCalled();
    });

    it("short-circuits lower-precedence host getters after an explicit match", () => {
        const lowerSignalRead = vi.fn(() => {
            throw new Error("lower-precedence-private-value");
        });
        const app = Object.defineProperties(
            {},
            {
                openclawHostSurface: {
                    value: "standalone_frontend",
                },
                hostSurface: {
                    get: lowerSignalRead,
                },
            }
        );
        const win = Object.defineProperty({}, "__OPENCLAW_HOST_SURFACE__", {
            get: lowerSignalRead,
        });

        expect(resolveHostSurface({ app, win })).toBe(
            HOST_SURFACES.standaloneFrontend
        );
        expect(lowerSignalRead).not.toHaveBeenCalled();
    });

    it("contains a revoked proxy bridge as a malformed signal", () => {
        const { proxy, revoke } = Proxy.revocable({}, {});
        revoke();

        expect(
            resolveHostSurface({
                win: { __comfyDesktop2: proxy, electronAPI: {} },
            })
        ).toBe(HOST_SURFACES.desktop);
    });

    it.each([null, "desktop", 1, true, [], () => {}])(
        "ignores malformed current bridge value %#",
        (value) => {
            expect(
                resolveHostSurface({
                    win: { __comfyDesktop2: value, electronAPI: {} },
                })
            ).toBe(HOST_SURFACES.desktop);
        }
    );

    it("contains throwing host getters and proxies without exposing their errors", () => {
        const app = Object.defineProperty({}, "openclawHostSurface", {
            get() {
                throw new Error("private-app-getter-value");
            },
        });
        const win = Object.defineProperties(
            {},
            {
                __OPENCLAW_HOST_SURFACE__: {
                    get() {
                        throw new Error("private-window-hint-value");
                    },
                },
                __DISTRIBUTION__: {
                    get() {
                        throw new Error("private-distribution-value");
                    },
                },
                __comfyDesktop2: {
                    get() {
                        throw new Error("private-current-bridge-value");
                    },
                },
                electronAPI: {
                    value: {},
                },
            }
        );

        expect(resolveHostSurface({ app, win })).toBe(HOST_SURFACES.desktop);
        expect(
            resolveHostSurface({
                win: new Proxy(
                    {},
                    {
                        get() {
                            throw new Error("private-proxy-value");
                        },
                    }
                ),
            })
        ).toBe(HOST_SURFACES.standaloneFrontend);
    });

    it("prefers explicit standalone host hints over generic runtime defaults", () => {
        const hostSurface = resolveHostSurface({
            app: { openclawHostSurface: "standalone_frontend" },
            win: { __comfyDesktop2: {}, electronAPI: { getPlatform() {} } },
        });
        expect(hostSurface).toBe(HOST_SURFACES.standaloneFrontend);
    });

    it("retains bridge capability for explicit legacy and current Desktop callers", () => {
        expect(
            getHostSurfaceCapabilities({
                app: { openclawHostSurface: "desktop" },
                win: { electronAPI: {} },
            }).supportsElectronBridge
        ).toBe(true);
        expect(
            getHostSurfaceCapabilities({
                app: { openclawHostSurface: "comfy_desktop" },
                win: { __comfyDesktop2: {} },
            }).supportsElectronBridge
        ).toBe(true);
    });

    it("derives legacy Desktop capabilities and stamps fixed-bundle metadata", () => {
        const container = document.createElement("div");
        const capabilities = stampHostSurfaceMetadata(container, {
            win: { electronAPI: { getPlatform() {} } },
        });

        expect(capabilities).toEqual({
            hostSurface: HOST_SURFACES.desktop,
            isDesktop: true,
            supportsElectronBridge: true,
            desktopGeneration: "legacy_fixed_bundle",
            desktopBridgeKind: "electron_api",
            hostedVersionMode: "fixed",
            reference: HOST_SURFACE_REFERENCES[HOST_SURFACES.desktop],
        });
        expect(container.dataset.openclawHostSurface).toBe("desktop");
        expect(container.dataset.openclawDesktopHost).toBe("true");
        expect(container.dataset.openclawReferenceFrontend).toBe("1.61.8");
        expect(container.dataset.openclawCurrentDesktopVersion).toBe("1.0.32-rc.1");
        expect(container.dataset.openclawCurrentDesktopGeneration).toBe("managed_install");
        expect(container.dataset.openclawCurrentDesktopHostedVersionMode).toBe(
            "installation_specific"
        );
        expect(container.dataset.openclawDesktopGeneration).toBe("legacy_fixed_bundle");
        expect(container.dataset.openclawDesktopBridgeKind).toBe("electron_api");
        expect(container.dataset.openclawDesktopHostedVersionMode).toBe("fixed");
        expect(container.dataset.openclawDesktopVersion).toBe("0.9.4");
        expect(container.dataset.openclawDesktopCoreVersion).toBe("0.22.3");
        expect(container.dataset.openclawDesktopEmbeddedFrontend).toBe("1.43.18");
        expect(container.dataset.openclawDesktopFrontendParity).toBe("lagging");
    });

    it("stamps managed-install Desktop without fabricating hosted versions", () => {
        const container = document.createElement("div");
        const capabilities = stampHostSurfaceMetadata(container, {
            win: { __comfyDesktop2: {} },
        });

        expect(capabilities).toEqual({
            hostSurface: HOST_SURFACES.comfyDesktop,
            isDesktop: true,
            supportsElectronBridge: true,
            desktopGeneration: "managed_install",
            desktopBridgeKind: "comfy_desktop2",
            hostedVersionMode: "installation_specific",
            reference: HOST_SURFACE_REFERENCES[HOST_SURFACES.comfyDesktop],
        });
        expect(container.dataset.openclawHostSurface).toBe("comfy_desktop");
        expect(container.dataset.openclawDesktopHost).toBe("true");
        expect(container.dataset.openclawReferenceFrontend).toBe("");
        expect(container.dataset.openclawDesktopGeneration).toBe("managed_install");
        expect(container.dataset.openclawDesktopBridgeKind).toBe("comfy_desktop2");
        expect(container.dataset.openclawDesktopHostedVersionMode).toBe(
            "installation_specific"
        );
        expect(container.dataset.openclawDesktopVersion).toBe("1.0.32-rc.1");
        expect(container.dataset.openclawDesktopCoreVersion).toBe("");
        expect(container.dataset.openclawDesktopEmbeddedFrontend).toBe("");
        expect(container.dataset.openclawDesktopFrontendParity).toBe("");
    });

    it("falls back to standalone frontend when desktop-only signals are absent", () => {
        expect(
            getHostSurfaceCapabilities({
                win: {},
            })
        ).toEqual({
            hostSurface: HOST_SURFACES.standaloneFrontend,
            isDesktop: false,
            supportsElectronBridge: false,
            desktopGeneration: null,
            desktopBridgeKind: null,
            hostedVersionMode: null,
            reference: HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend],
        });
    });

    it("clears stale Desktop metadata when a container is restamped as standalone", () => {
        const container = document.createElement("div");
        stampHostSurfaceMetadata(container, {
            win: { electronAPI: {} },
        });
        stampHostSurfaceMetadata(container, {
            win: {},
        });

        expect(container.dataset.openclawDesktopGeneration).toBe("");
        expect(container.dataset.openclawDesktopBridgeKind).toBe("");
        expect(container.dataset.openclawDesktopHostedVersionMode).toBe("");
        expect(container.dataset.openclawDesktopVersion).toBe("");
        expect(container.dataset.openclawDesktopCoreVersion).toBe("");
        expect(container.dataset.openclawDesktopEmbeddedFrontend).toBe("");
        expect(container.dataset.openclawDesktopFrontendParity).toBe("");
    });

    it("restores absent and pre-existing owned metadata exactly and remains idempotent", () => {
        const container = document.createElement("div");
        container.setAttribute("data-openclaw-host-surface", "host-owned-value");
        container.setAttribute("data-unrelated-owner", "preserve-me");

        const lease = acquireHostSurfaceMetadata(container, { win: {} });
        expect(lease.capabilities.hostSurface).toBe(HOST_SURFACES.standaloneFrontend);
        expect(container.getAttribute("data-openclaw-host-surface")).toBe(
            "standalone_frontend"
        );
        expect(
            OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES.every((name) =>
                container.hasAttribute(name)
            )
        ).toBe(true);

        lease.dispose();
        lease.dispose();

        expect(container.getAttribute("data-openclaw-host-surface")).toBe(
            "host-owned-value"
        );
        for (const name of OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES.slice(1)) {
            expect(container.hasAttribute(name)).toBe(false);
        }
        expect(container.getAttribute("data-unrelated-owner")).toBe("preserve-me");
    });
});

describe("R254 host reference baseline separation", () => {
    it("publishes core source, tag and bundled-frontend facts as distinct values", () => {
        expect(HOST_CORE_REFERENCE).toEqual({
            sourceRevision: "da8bd08e",
            sourceDescribe: "v0.46.0-46-gda8bd08e4",
            version: "0.46.0",
            tag: "v0.46.0",
            tagRevision: "a277b5bf",
            bundledFrontendVersion: "1.59.11",
        });
        expect(HOST_CORE_REFERENCE.sourceRevision).not.toBe(HOST_CORE_REFERENCE.tagRevision);
    });

    it("lets the reviewed frontend source head equal the release only for an exact-tag describe", () => {
        expect(headReleaseViolations(HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend])).toEqual([]);

        // The pinned reference is the exact-tag case: the head IS the tagged release.
        const standalone = HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend];
        expect(standalone.sourceRevision).toBe(standalone.releaseRevision);
        expect(standalone.sourceDescribe).toBe(standalone.releaseTag);
    });

    it("still rejects a collapsed or inconsistent head and release pair", () => {
        const ahead = {
            releaseTag: "v1.60.0",
            releaseRevision: "5cb2899f",
            sourceRevision: "aa4e48c08d",
            sourceDescribe: "v1.60.0-209-gaa4e48c08d",
        };
        // A head ahead of its release stays valid, with a distinct revision and describe.
        expect(headReleaseViolations(ahead)).toEqual([]);
        // The same revision under a commits-ahead describe collapses the two subjects.
        expect(headReleaseViolations({ ...ahead, sourceRevision: ahead.releaseRevision })).toEqual(["collapsed"]);
        // An exact-tag describe over a distinct head is a false claim of equality.
        expect(headReleaseViolations({ ...ahead, sourceDescribe: ahead.releaseTag })).toEqual([
            "exact_tag_with_distinct_head",
        ]);
        // A distinct head must still derive its describe from the release tag.
        expect(headReleaseViolations({ ...ahead, sourceDescribe: "v1.59.0-209-gaa4e48c08d" })).toEqual([
            "distinct_head_without_commits_ahead_describe",
        ]);
    });

    it("keeps real-host validation pending independently from source review", () => {
        // CRITICAL: source review and the local gate are not runtime proof. Only
        // an authorized real-host campaign on these exact subjects may change this.
        expect(HOST_REAL_VALIDATION_STATE).toBe("pending");
    });

    it("stamps the separated reference facts on every host surface", () => {
        for (const surface of Object.values(HOST_SURFACES)) {
            const container = document.createElement("div");
            stampHostSurfaceMetadata(container, { app: { hostSurface: surface }, win: {} });

            expect(container.dataset.openclawCoreSourceRevision, surface).toBe("da8bd08e");
            expect(container.dataset.openclawCoreVersion, surface).toBe("0.46.0");
            expect(container.dataset.openclawCoreTagRevision, surface).toBe("a277b5bf");
            expect(container.dataset.openclawCoreBundledFrontend, surface).toBe("1.59.11");
            expect(container.dataset.openclawFrontendSourceRevision, surface).toBe("388973acb5");
            expect(container.dataset.openclawFrontendReleaseVersion, surface).toBe("1.61.8");
            expect(container.dataset.openclawFrontendReleaseRevision, surface).toBe("388973acb5");
            // Both Desktop surfaces are pending by construction; the standalone surface is
            // pending because no campaign has run against the refreshed subjects.
            expect(container.dataset.openclawRealHostValidation, surface).toBe("pending");
        }
    });

    it("lists every new attribute in the acquire/restore contract", () => {
        const required = [
            "data-openclaw-core-source-revision",
            "data-openclaw-core-version",
            "data-openclaw-core-tag-revision",
            "data-openclaw-core-bundled-frontend",
            "data-openclaw-frontend-source-revision",
            "data-openclaw-frontend-release-version",
            "data-openclaw-frontend-release-revision",
            "data-openclaw-real-host-validation",
        ];
        for (const name of required) {
            expect(OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES, name).toContain(name);
        }
    });

    it("exposes no local path or internal record in the stamped metadata", () => {
        const container = document.createElement("div");
        stampHostSurfaceMetadata(container, {
            hostSurface: HOST_SURFACES.standaloneFrontend,
            win: {},
        });
        const serialized = JSON.stringify({ ...container.dataset });
        for (const forbidden of [".planning", "reference/docs", "/mnt/", ".tmp", String.fromCharCode(92)]) {
            expect(serialized, forbidden).not.toContain(forbidden);
        }
    });
});
