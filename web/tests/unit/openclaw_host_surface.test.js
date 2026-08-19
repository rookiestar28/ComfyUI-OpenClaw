import { describe, expect, it, vi } from "vitest";
import {
    HOST_SURFACES,
    HOST_SURFACE_REFERENCES,
    getHostSurfaceCapabilities,
    resolveHostSurface,
    stampHostSurfaceMetadata,
} from "../../openclaw_host_surface.js";

describe("openclaw_host_surface", () => {
    it("publishes exact standalone and two-generation desktop references", () => {
        expect(HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend]).toEqual({
            frontendVersion: "1.52.1",
            sourceRevision: "569e65b30f",
        });
        expect(HOST_SURFACE_REFERENCES[HOST_SURFACES.desktop]).toEqual({
            desktopVersion: "0.9.4",
            coreVersion: "0.22.3",
            embeddedFrontendVersion: "1.43.18",
            standaloneFrontendVersion: "1.52.1",
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
        expect(container.dataset.openclawReferenceFrontend).toBe("1.52.1");
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
});
