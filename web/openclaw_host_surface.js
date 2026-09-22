/**
 * R164: Explicit frontend host-surface detection helpers.
 * Keep desktop-vs-standalone assumptions centralized so extension code does not
 * silently treat the desktop bundle as identical to standalone frontend HEAD.
 */

export const HOST_SURFACES = Object.freeze({
    standaloneFrontend: "standalone_frontend",
    desktop: "desktop",
    comfyDesktop: "comfy_desktop",
});

/**
 * R254: ComfyUI core reference facts.
 *
 * CRITICAL: `sourceRevision` is a reviewed checkout, `tagRevision` is the
 * reproducible tag baseline, and `bundledFrontendVersion` is the frontend the
 * core manifest pins. These are three different subjects; do not collapse them.
 */
export const HOST_CORE_REFERENCE = Object.freeze({
    sourceRevision: "e638023d",
    sourceDescribe: "v0.37.0-9-ge638023d",
    version: "0.37.0",
    tag: "v0.37.0",
    tagRevision: "73c9bad4",
    bundledFrontendVersion: "1.53.6",
});

/**
 * Real-host validation state for the pinned core and two frontend subjects.
 *
 * CRITICAL: source review and repository validation are not runtime proof. This
 * state is backed by the paired pinned-host campaign in the compatibility matrix;
 * do not extend it to the later frontend source head or Desktop surfaces.
 */
export const HOST_REAL_VALIDATION_STATE = "validated";

export const HOST_SURFACE_REFERENCES = Object.freeze({
    [HOST_SURFACES.standaloneFrontend]: Object.freeze({
        frontendVersion: "1.55.11",
        sourceRevision: "23559a8f86",
        sourceDescribe: "v1.55.11-104-g23559a8f86",
        // The reproducible release, 104 commits behind the reviewed source head.
        releaseVersion: "1.55.11",
        releaseTag: "v1.55.11",
        releaseRevision: "3a851363",
    }),
    [HOST_SURFACES.desktop]: Object.freeze({
        desktopVersion: "0.9.4",
        coreVersion: "0.22.3",
        embeddedFrontendVersion: "1.43.18",
        standaloneFrontendVersion: "1.55.11",
        frontendParity: "lagging",
        generation: "legacy_fixed_bundle",
        hostedVersionMode: "fixed",
    }),
    [HOST_SURFACES.comfyDesktop]: Object.freeze({
        desktopVersion: "1.0.32-rc.1",
        sourceRevision: "85e28b7a",
        sourceDescribe: "v1.0.32-rc.1-3-g85e28b7",
        generation: "managed_install",
        hostedVersionMode: "installation_specific",
        coreVersion: null,
        frontendVersion: null,
    }),
});

function normalizeSurfaceName(surface) {
    if (surface === HOST_SURFACES.desktop || surface === "desktop") {
        return HOST_SURFACES.desktop;
    }
    if (
        surface === HOST_SURFACES.comfyDesktop ||
        surface === "current_desktop" ||
        surface === "managed_install"
    ) {
        return HOST_SURFACES.comfyDesktop;
    }
    if (surface === "legacy_desktop" || surface === "legacy_fixed_bundle") {
        return HOST_SURFACES.desktop;
    }
    if (
        surface === HOST_SURFACES.standaloneFrontend ||
        surface === "standalone" ||
        surface === "standalone_frontend" ||
        surface === "localhost"
    ) {
        return HOST_SURFACES.standaloneFrontend;
    }
    return null;
}

function safeRead(target, property) {
    if (target === null || target === undefined) return undefined;
    try {
        return target[property];
    } catch {
        return undefined;
    }
}

function isBridgeObject(value) {
    if (value === null || typeof value !== "object") return false;
    try {
        return !Array.isArray(value);
    } catch {
        return false;
    }
}

function bridgeKindForSurface(hostSurface) {
    if (hostSurface === HOST_SURFACES.comfyDesktop) return "comfy_desktop2";
    if (hostSurface === HOST_SURFACES.desktop) return "electron_api";
    return null;
}

function detectedBridgeKindForSurface(hostSurface, win) {
    const bridgeKind = bridgeKindForSurface(hostSurface);
    if (bridgeKind === "comfy_desktop2") {
        return isBridgeObject(safeRead(win, "__comfyDesktop2"))
            ? bridgeKind
            : null;
    }
    if (bridgeKind === "electron_api") {
        return isBridgeObject(safeRead(win, "electronAPI")) ? bridgeKind : null;
    }
    return null;
}

function inspectHostSurface(options = {}) {
    const app = safeRead(options, "app");
    const explicitWindow = safeRead(options, "win");
    const win =
        explicitWindow === undefined ? safeRead(globalThis, "window") : explicitWindow;

    for (const [target, property] of [
        [app, "openclawHostSurface"],
        [app, "hostSurface"],
        [win, "__OPENCLAW_HOST_SURFACE__"],
    ]) {
        const surface = normalizeSurfaceName(safeRead(target, property));
        if (surface) {
            return {
                hostSurface: surface,
                detectedBridgeKind: detectedBridgeKindForSurface(surface, win),
            };
        }
    }

    const distributionSurface = normalizeSurfaceName(
        safeRead(win, "__DISTRIBUTION__")
    );
    if (distributionSurface) {
        return {
            hostSurface: distributionSurface,
            detectedBridgeKind: detectedBridgeKindForSurface(
                distributionSurface,
                win
            ),
        };
    }

    // CRITICAL: bridge detection must remain presence-only. Reading members or calling
    // methods can cross privileged Desktop IPC and privacy boundaries.
    if (isBridgeObject(safeRead(win, "__comfyDesktop2"))) {
        return {
            hostSurface: HOST_SURFACES.comfyDesktop,
            detectedBridgeKind: "comfy_desktop2",
        };
    }
    if (isBridgeObject(safeRead(win, "electronAPI"))) {
        return {
            hostSurface: HOST_SURFACES.desktop,
            detectedBridgeKind: "electron_api",
        };
    }

    return {
        hostSurface: HOST_SURFACES.standaloneFrontend,
        detectedBridgeKind: null,
    };
}

export function resolveHostSurface(options = {}) {
    return inspectHostSurface(options).hostSurface;
}

export function getHostSurfaceCapabilities(options = {}) {
    const { hostSurface, detectedBridgeKind } = inspectHostSurface(options);
    const reference = HOST_SURFACE_REFERENCES[hostSurface] || {};
    const isDesktop =
        hostSurface === HOST_SURFACES.desktop ||
        hostSurface === HOST_SURFACES.comfyDesktop;
    const desktopBridgeKind = bridgeKindForSurface(hostSurface);
    return {
        hostSurface,
        isDesktop,
        supportsElectronBridge: isDesktop && detectedBridgeKind === desktopBridgeKind,
        desktopGeneration: isDesktop ? reference.generation || null : null,
        desktopBridgeKind,
        hostedVersionMode: isDesktop ? reference.hostedVersionMode || null : null,
        reference,
    };
}

export const OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES = Object.freeze([
    "data-openclaw-host-surface",
    "data-openclaw-desktop-host",
    "data-openclaw-reference-frontend",
    "data-openclaw-current-desktop-version",
    "data-openclaw-current-desktop-generation",
    "data-openclaw-current-desktop-hosted-version-mode",
    "data-openclaw-desktop-generation",
    "data-openclaw-desktop-bridge-kind",
    "data-openclaw-desktop-hosted-version-mode",
    "data-openclaw-desktop-version",
    "data-openclaw-desktop-core-version",
    "data-openclaw-desktop-embedded-frontend",
    "data-openclaw-desktop-frontend-parity",
    // R254: source-review versus reproducible-release facts, kept as separate
    // attribute names so no existing attribute silently changes meaning.
    "data-openclaw-core-source-revision",
    "data-openclaw-core-version",
    "data-openclaw-core-tag-revision",
    "data-openclaw-core-bundled-frontend",
    "data-openclaw-frontend-source-revision",
    "data-openclaw-frontend-release-version",
    "data-openclaw-frontend-release-revision",
    "data-openclaw-real-host-validation",
]);

export function stampHostSurfaceMetadata(container, options = {}) {
    const capabilities = getHostSurfaceCapabilities(options);
    if (container?.dataset) {
        container.dataset.openclawHostSurface = capabilities.hostSurface;
        container.dataset.openclawDesktopHost = capabilities.isDesktop
            ? "true"
            : "false";
        container.dataset.openclawReferenceFrontend =
            capabilities.hostSurface === HOST_SURFACES.desktop
                ? capabilities.reference.standaloneFrontendVersion || ""
                : capabilities.reference.frontendVersion || "";
        const currentDesktopReference =
            HOST_SURFACE_REFERENCES[HOST_SURFACES.comfyDesktop];
        container.dataset.openclawCurrentDesktopVersion =
            currentDesktopReference.desktopVersion;
        container.dataset.openclawCurrentDesktopGeneration =
            currentDesktopReference.generation;
        container.dataset.openclawCurrentDesktopHostedVersionMode =
            currentDesktopReference.hostedVersionMode;
        container.dataset.openclawDesktopGeneration =
            capabilities.desktopGeneration || "";
        container.dataset.openclawDesktopBridgeKind =
            capabilities.desktopBridgeKind || "";
        container.dataset.openclawDesktopHostedVersionMode =
            capabilities.hostedVersionMode || "";
        container.dataset.openclawDesktopVersion = capabilities.isDesktop
            ? capabilities.reference.desktopVersion || ""
            : "";
        container.dataset.openclawDesktopCoreVersion = capabilities.isDesktop
            ? capabilities.reference.coreVersion || ""
            : "";
        container.dataset.openclawDesktopEmbeddedFrontend = capabilities.isDesktop
            ? capabilities.reference.embeddedFrontendVersion ||
              capabilities.reference.frontendVersion ||
              ""
            : "";
        container.dataset.openclawDesktopFrontendParity = capabilities.isDesktop
            ? capabilities.reference.frontendParity || ""
            : "";

        const standaloneReference =
            HOST_SURFACE_REFERENCES[HOST_SURFACES.standaloneFrontend];
        container.dataset.openclawCoreSourceRevision =
            HOST_CORE_REFERENCE.sourceRevision;
        container.dataset.openclawCoreVersion = HOST_CORE_REFERENCE.version;
        container.dataset.openclawCoreTagRevision = HOST_CORE_REFERENCE.tagRevision;
        container.dataset.openclawCoreBundledFrontend =
            HOST_CORE_REFERENCE.bundledFrontendVersion;
        container.dataset.openclawFrontendSourceRevision =
            standaloneReference.sourceRevision;
        container.dataset.openclawFrontendReleaseVersion =
            standaloneReference.releaseVersion;
        container.dataset.openclawFrontendReleaseRevision =
            standaloneReference.releaseRevision;
        // IMPORTANT: the paired campaign exercised standalone ComfyUI only;
        // stamping its verdict on Desktop would claim an untested host passed.
        container.dataset.openclawRealHostValidation = capabilities.isDesktop
            ? "pending"
            : HOST_REAL_VALIDATION_STATE;
    }
    return capabilities;
}

export function acquireHostSurfaceMetadata(container, options = {}) {
    const originals = new Map();
    if (
        container &&
        typeof container.hasAttribute === "function" &&
        typeof container.getAttribute === "function"
    ) {
        for (const name of OPENCLAW_HOST_SURFACE_ATTRIBUTE_NAMES) {
            originals.set(name, {
                present: container.hasAttribute(name),
                value: container.getAttribute(name),
            });
        }
    }

    // CRITICAL: capture every owned attribute before the first stamp. Capturing after
    // mutation makes no-destroy cleanup preserve OpenClaw markers on the next extension.
    const capabilities = stampHostSurfaceMetadata(container, options);
    let disposed = false;

    return {
        capabilities,
        dispose() {
            if (disposed) return;
            disposed = true;
            if (
                !container ||
                typeof container.setAttribute !== "function" ||
                typeof container.removeAttribute !== "function"
            ) {
                return;
            }
            for (const [name, original] of originals) {
                if (original.present) {
                    container.setAttribute(name, original.value ?? "");
                } else {
                    container.removeAttribute(name);
                }
            }
        },
    };
}
