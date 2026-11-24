import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { app } from "../../../scripts/app.js";
import {
    buildContextToolboxItems,
    hostCollectsNodeMenuItems,
    registerContextToolbox,
} from "../../../web/extensions/context_toolbox.js";
import { installCurrentHostNodeMenu, installLegacyHostNodeMenu } from "./support/host_node_menu_emulation.js";

const { actions, hasComparableWidget } = vi.hoisted(() => ({
    actions: {
        openExplorer: vi.fn(),
        openDoctor: vi.fn(),
        openQueue: vi.fn(),
        openCompare: vi.fn(),
        openSettings: vi.fn(),
    },
    hasComparableWidget: vi.fn(),
}));

vi.mock("../../../web/openclaw_ui.js", () => ({ openclawActions: actions }));
vi.mock("../../../web/openclaw_graph_host.js", () => ({ hasComparableWidget }));

const INSPECT = "\uD83D\uDD0D OpenClaw: Inspect";
const DOCTOR = "\uD83D\uDC89 OpenClaw: Doctor";
const QUEUE = "\u23F3 OpenClaw: Queue Status";
const COMPARE = "\u2696\uFE0F OpenClaw: Compare...";
const SETTINGS = "\u2699\uFE0F OpenClaw: Settings";

const BASE_ITEMS = Object.freeze([{ content: "Properties" }, { content: "Remove" }]);
const NODE = Object.freeze({ type: "CheckpointLoaderSimple" });
const entry = (content) => ({ content, callback: expect.any(Function) });

/** The host's own options come first; everything after them is what OpenClaw added. */
const addedByOpenClaw = (menu) => menu.slice(BASE_ITEMS.length);

let warn;

beforeEach(() => {
    warn = vi.fn();
    globalThis.LGraphCanvas = class LGraphCanvas {};
    app.graph = { id: "graph" };
    hasComparableWidget.mockReturnValue(false);
});

afterEach(async () => {
    // A registration that is still loading its actions must finish before the host it was made on goes away.
    await toolboxSettled();
    delete globalThis.LGraphCanvas;
    delete app.graph;
    delete app.registerExtension;
    delete app.collectNodeMenuItems;
});

function currentHost() {
    return installCurrentHostNodeMenu({ app, canvasClass: globalThis.LGraphCanvas, baseItems: BASE_ITEMS, warn });
}

function legacyHost() {
    return installLegacyHostNodeMenu({ app, canvasClass: globalThis.LGraphCanvas, baseItems: BASE_ITEMS });
}

const GENERATIONS = [
    ["with the node-menu hook", currentHost],
    ["without the node-menu hook", legacyHost],
];

/** The toolbox loads its actions with a dynamic import; wait for the import and for what follows it. */
async function toolboxSettled() {
    await vi.dynamicImportSettled();
    await new Promise((resolve) => setTimeout(resolve, 0));
}

function toolboxExtension(host) {
    return host.extensions.find((extension) => extension.name === "OpenClaw.ContextToolbox");
}

/**
 * OpenClaw's real startup shape. Its main extension is registered first, and that extension's own async
 * `setup` registers the toolbox after it has awaited something. The host takes its extension list when the
 * pass starts, so the toolbox is not in it and the host never calls the toolbox's own `setup`.
 */
async function bootLikeOpenClaw(host) {
    app.registerExtension({
        name: "OpenClaw",
        async setup() {
            await new Promise((resolve) => setTimeout(resolve, 0));
            registerContextToolbox();
        },
    });
    await host.runSetup();
    await toolboxSettled();
}

describe("emulated host generations (negative control)", () => {
    it("warns, naming the extension, when an extension assigns getNodeMenuOptions during its setup", async () => {
        const host = currentHost();
        app.registerExtension({
            name: "Example.Patcher",
            setup() {
                const original = globalThis.LGraphCanvas.prototype.getNodeMenuOptions;
                globalThis.LGraphCanvas.prototype.getNodeMenuOptions = function patched() {
                    return original.apply(this, arguments);
                };
            },
        });

        await host.runSetup();

        expect(warn).toHaveBeenCalledTimes(1);
        expect(String(warn.mock.calls[0][0])).toContain(
            '[DEPRECATED]%c Monkey-patching getNodeMenuOptions is deprecated. (Extension: "Example.Patcher")',
        );
        expect(host.prototypeAssignmentsByExtensions()).toBe(1);
    });

    it.each(GENERATIONS)(
        "never calls the setup of an extension that is registered while the pass runs (%s)",
        async (_label, makeHost) => {
            const host = makeHost();
            const lateSetup = vi.fn();
            app.registerExtension({
                name: "Example.Main",
                async setup() {
                    await new Promise((resolve) => setTimeout(resolve, 0));
                    app.registerExtension({ name: "Example.Late", setup: lateSetup });
                },
            });

            await host.runSetup();

            expect(host.extensions.map((extension) => extension.name)).toEqual(["Example.Main", "Example.Late"]);
            expect(lateSetup).not.toHaveBeenCalled();
        },
    );
});

describe("context toolbox on a host that has the node-menu hook", () => {
    it("adds its entries once, through the hook, and never assigns the prototype or warns", async () => {
        const host = currentHost();
        await bootLikeOpenClaw(host);
        const menu = host.menuFor(NODE);

        expect(menu.slice(0, BASE_ITEMS.length)).toEqual(BASE_ITEMS);
        expect(addedByOpenClaw(menu)).toEqual([null, entry(INSPECT), entry(DOCTOR), entry(QUEUE), entry(SETTINGS)]);
        expect(warn).not.toHaveBeenCalled();
        expect(host.prototypeAssignmentsByExtensions()).toBe(0);
    });

    it("adds nothing before the actions are available", () => {
        const host = currentHost();
        registerContextToolbox();

        expect(addedByOpenClaw(host.menuFor(NODE))).toEqual([]);
    });

    it("offers Compare between Queue Status and Settings only for a node with a comparable widget", async () => {
        hasComparableWidget.mockReturnValue(true);
        const host = currentHost();
        await bootLikeOpenClaw(host);

        expect(addedByOpenClaw(host.menuFor(NODE))).toEqual([
            null,
            entry(INSPECT),
            entry(DOCTOR),
            entry(QUEUE),
            entry(COMPARE),
            entry(SETTINGS),
        ]);
        expect(hasComparableWidget).toHaveBeenCalledWith(app.graph, NODE);
    });

    it("runs each entry's callback against the actions with the same arguments as before", async () => {
        hasComparableWidget.mockReturnValue(true);
        const host = currentHost();
        await bootLikeOpenClaw(host);
        const byLabel =new Map(addedByOpenClaw(host.menuFor(NODE)).filter(Boolean).map((item) => [item.content, item]));

        byLabel.get(INSPECT).callback();
        byLabel.get(DOCTOR).callback();
        byLabel.get(QUEUE).callback();
        byLabel.get(COMPARE).callback();
        byLabel.get(SETTINGS).callback();

        expect(actions.openExplorer).toHaveBeenCalledExactlyOnceWith(NODE.type);
        expect(actions.openDoctor).toHaveBeenCalledExactlyOnceWith();
        expect(actions.openQueue).toHaveBeenCalledExactlyOnceWith("all");
        expect(actions.openCompare).toHaveBeenCalledExactlyOnceWith(NODE);
        expect(actions.openSettings).toHaveBeenCalledExactlyOnceWith();
    });

    it("builds a fresh list on every menu, so one menu never changes another", async () => {
        const host = currentHost();
        await bootLikeOpenClaw(host);

        const first =addedByOpenClaw(host.menuFor(NODE));
        const second = addedByOpenClaw(host.menuFor(NODE));

        // Callbacks are closures made per build, so two builds agree on labels, not on function identity.
        expect(second.map((item) => item?.content)).toEqual(first.map((item) => item?.content));
        expect(second).not.toBe(first);
        expect(second[1]).not.toBe(first[1]);
        expect(second[1].callback).not.toBe(first[1].callback);
    });
});

describe("context toolbox on a host without the node-menu hook", () => {
    it("adds its entries once, through the prototype patch, with the leading separator", async () => {
        const host = legacyHost();
        await bootLikeOpenClaw(host);
        const menu = host.menuFor(NODE);

        expect(menu.slice(0, BASE_ITEMS.length)).toEqual(BASE_ITEMS);
        expect(addedByOpenClaw(menu)).toEqual([null, entry(INSPECT), entry(DOCTOR), entry(QUEUE), entry(SETTINGS)]);
    });

    it("offers Compare there too, between Queue Status and Settings", async () => {
        hasComparableWidget.mockReturnValue(true);
        const host = legacyHost();
        await bootLikeOpenClaw(host);

        expect(addedByOpenClaw(host.menuFor(NODE)).map((item) => item?.content)).toEqual([
            undefined,
            INSPECT,
            DOCTOR,
            QUEUE,
            COMPARE,
            SETTINGS,
        ]);
        expect(hasComparableWidget).toHaveBeenCalledWith(app.graph, NODE);
    });

    it("keeps the host's return value when the host returns no options", async () => {
        const host = legacyHost();
        const hostMethod = () => undefined;
        globalThis.LGraphCanvas.prototype.getNodeMenuOptions = hostMethod;

        await bootLikeOpenClaw(host);

        // The patch is in place, and it passes the host's own "no options" answer through.
        expect(globalThis.LGraphCanvas.prototype.getNodeMenuOptions).not.toBe(hostMethod);
        expect(host.menuFor(NODE)).toBeUndefined();
    });

    it("never answers the hook once the patch is installed, so no host can show the entries twice", async () => {
        const host = legacyHost();
        await bootLikeOpenClaw(host);

        // The patch is what shows the four actions here, so the hook has to stay silent.
        expect(addedByOpenClaw(host.menuFor(NODE)).filter(Boolean)).toHaveLength(4);
        expect(toolboxExtension(host).getNodeMenuItems(NODE)).toEqual([]);
    });
});

describe.each(GENERATIONS)("context toolbox on a host %s, in the other registration orders", (_label, makeHost) => {
    it("adds its entries once when it is registered before the setup pass starts", async () => {
        const host = makeHost();
        registerContextToolbox();

        await host.runSetup();
        await toolboxSettled();

        expect(addedByOpenClaw(host.menuFor(NODE))).toEqual([null, entry(INSPECT), entry(DOCTOR), entry(QUEUE), entry(SETTINGS)]);
    });

    it("adds its entries once even when the host also calls the extension's own setup", async () => {
        const host = makeHost();
        await bootLikeOpenClaw(host);

        await toolboxExtension(host).setup?.();
        await toolboxSettled();

        expect(host.extensions.filter((extension) => extension.name === "OpenClaw.ContextToolbox")).toHaveLength(1);
        expect(addedByOpenClaw(host.menuFor(NODE))).toEqual([null, entry(INSPECT), entry(DOCTOR), entry(QUEUE), entry(SETTINGS)]);
    });
});

describe("telling the two host generations apart", () => {
    it("recognises a host that has the hook consumer and the warning accessor", () => {
        currentHost();

        expect(hostCollectsNodeMenuItems()).toBe(true);
    });

    it("recognises a host with only the consumer", () => {
        legacyHost();
        app.collectNodeMenuItems = () => [];

        expect(hostCollectsNodeMenuItems()).toBe(true);
    });

    it("recognises a host with only the warning accessor, and does not call it to find out", () => {
        const getter = vi.fn(() => () => []);
        const setter = vi.fn();
        Object.defineProperty(globalThis.LGraphCanvas.prototype, "getNodeMenuOptions", {
            configurable: true,
            get: getter,
            set: setter,
        });

        expect(hostCollectsNodeMenuItems()).toBe(true);
        expect(getter).not.toHaveBeenCalled();
        expect(setter).not.toHaveBeenCalled();
    });

    it("treats a plain prototype method with no consumer as a host without the hook", () => {
        legacyHost();

        expect(hostCollectsNodeMenuItems()).toBe(false);
    });

    it("does not throw when the host has no canvas class at all", () => {
        delete globalThis.LGraphCanvas;

        expect(hostCollectsNodeMenuItems()).toBe(false);
    });
});

describe("buildContextToolboxItems", () => {
    it("returns the separator and the entries in the order the menu has always had", () => {
        expect(buildContextToolboxItems(NODE, actions)).toEqual([null, entry(INSPECT), entry(DOCTOR), entry(QUEUE), entry(SETTINGS)]);
    });
});

// Last in the file on purpose: it swaps the actions module for one whose import fails, and it is the
// only describe that does, so nothing after it depends on the module being put back.
describe("context toolbox when its actions cannot be loaded", () => {
    const ACTIONS_MODULE = "../../../web/openclaw_ui.js";
    let logged;
    let unhandled;
    const onUnhandledRejection = (reason) => unhandled.push(reason);

    beforeAll(() => {
        // The module is cached after its first import, so drop it and make the next imports fail.
        vi.resetModules();
        vi.doMock(ACTIONS_MODULE, () => {
            throw new Error("actions unavailable");
        });
    });

    afterAll(() => {
        vi.doMock(ACTIONS_MODULE, () => ({ openclawActions: actions }));
        vi.resetModules();
    });

    beforeEach(() => {
        logged = vi.spyOn(console, "error").mockImplementation(() => {});
        unhandled = [];
        process.on("unhandledRejection", onUnhandledRejection);
    });

    afterEach(async () => {
        await toolboxSettled();
        process.off("unhandledRejection", onUnhandledRejection);
        logged.mockRestore();
    });

    it.each(GENERATIONS)(
        "logs once under the OpenClaw prefix, adds no entries and leaves no unhandled rejection (%s)",
        async (_label, makeHost) => {
            const host = makeHost();
            const original = globalThis.LGraphCanvas.prototype.getNodeMenuOptions;

            await bootLikeOpenClaw(host);
            await new Promise((resolve) => setTimeout(resolve, 0));

            expect(logged).toHaveBeenCalledTimes(1);
            expect(String(logged.mock.calls[0][0])).toContain("[OpenClaw]");
            expect(addedByOpenClaw(host.menuFor(NODE))).toEqual([]);
            expect(toolboxExtension(host).getNodeMenuItems(NODE)).toEqual([]);
            expect(globalThis.LGraphCanvas.prototype.getNodeMenuOptions).toBe(original);
            expect(unhandled).toEqual([]);
        },
    );
});
