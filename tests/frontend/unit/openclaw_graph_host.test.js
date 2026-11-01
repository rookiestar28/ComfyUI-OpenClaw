import { describe, expect, it } from "vitest";
import {
    findComparableWidget,
    getGraphNodeEntry,
    getGraphNodeCatalog,
    getGraphWidgetCatalog,
    getGraphWidgetValueCandidates,
    resolveGraphWidget,
} from "../../../web/openclaw_graph_host.js";

describe("host store-backed widget values", () => {
    it("reads and writes a prototype accessor without copying an obsolete own value", () => {
        const store = new Map([["value", "base.ckpt"]]);
        class StoreWidget {
            get value() { return store.get("value"); }
            set value(value) { store.set("value", value); }
        }
        const widget = Object.assign(new StoreWidget(), { name: "ckpt_name", type: "combo" });
        const graph = { _nodes: [{ id: 7, widgets: [widget] }] };
        const resolved = resolveGraphWidget(graph, "7", "ckpt_name");
        expect(Object.hasOwn(widget, "value")).toBe(false);
        expect(resolved.widget.value).toBe("base.ckpt");
        store.set("value", "updated.ckpt");
        expect(resolved.widget.value).toBe("updated.ckpt");
        resolved.widget.value = "selected.ckpt";
        expect(store.get("value")).toBe("selected.ckpt");
    });
});

function createGraphFixture() {
    const nestedLoader = {
        id: 7,
        type: "CheckpointLoaderSimple",
        title: "Nested Loader",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: { values: ["base.ckpt", "xl.ckpt"] },
            },
        ],
    };
    const nestedSampler = {
        id: 8,
        type: "KSampler",
        title: "Nested Sampler",
        widgets: [
            {
                name: "seed",
                type: "number",
                value: 1234,
                options: { values: [1234, 4321] },
            },
        ],
    };
    const subgraph = {
        _nodes: [nestedLoader, nestedSampler],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
    const subgraphHost = {
        id: 50,
        type: "SubgraphNode",
        title: "Workflow Pack",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: {},
                sourceNodeId: "7",
                sourceWidgetName: "ckpt_name",
            },
            {
                name: "seed",
                type: "number",
                value: 1234,
                options: {},
                sourceNodeId: "8",
                sourceWidgetName: "seed",
            },
        ],
        subgraph,
    };
    const rootSampler = {
        id: 10,
        type: "KSampler",
        title: "Root Sampler",
        widgets: [
            {
                name: "steps",
                type: "number",
                value: 20,
                options: { values: [20, 30] },
            },
        ],
    };

    return {
        _nodes: [rootSampler, subgraphHost],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
}

function createHostShapedGraphFixture() {
    const nestedLoader = {
        id: "source-loader",
        type: "CheckpointLoaderSimple",
        title: "Nested String Loader",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: { values: ["base.ckpt", "xl.ckpt"] },
            },
        ],
    };
    const subgraph = {
        _nodes: [nestedLoader],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
    const host = {
        id: "host-pack",
        type: "SubgraphNode",
        title: "String Workflow Pack",
        widgets: [
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: {},
                sourceNodeId: "source-loader",
                sourceExecutionId: "host-pack:source-loader",
                sourceWidgetName: "ckpt_name",
            },
        ],
        subgraph,
    };
    const stringNode = {
        id: "loader-alpha",
        type: "ColorAndBoxNode",
        title: "String Node",
        widgets: [
            {
                name: "palette",
                type: "COLORS",
                value: ["#ff0000", "#00ff00"],
                options: {},
            },
            {
                name: "regions",
                type: "BOUNDING_BOXES",
                value: [{ x: 1, y: 2, width: 3, height: 4 }],
                options: {},
            },
            {
                name: "ckpt_name",
                type: "combo",
                value: "base.ckpt",
                options: { values: ["base.ckpt", "xl.ckpt"] },
            },
        ],
    };

    return {
        _nodes: [host, stringNode],
        getNodeById(id) {
            return this._nodes.find((node) => String(node.id) === String(id));
        },
    };
}

describe("openclaw_graph_host", () => {
    it("builds catalog entries for nested subgraph nodes", () => {
        const graph = createGraphFixture();
        const catalog = getGraphNodeCatalog(graph);

        expect(catalog.map((entry) => entry.id)).toEqual(["10", "50", "50:7", "50:8"]);
        expect(catalog.find((entry) => entry.id === "50:7")?.displayTitle).toBe(
            "Workflow Pack / Nested Loader"
        );
    });

    it("resolves promoted widget catalogs and candidate values from the nested source widget", () => {
        const graph = createGraphFixture();
        const widgetCatalog = getGraphWidgetCatalog(graph, "50");
        const promotedWidget = widgetCatalog.find((entry) => entry.name === "ckpt_name");

        expect(promotedWidget?.isPromoted).toBe(true);
        expect(promotedWidget?.resolvedNodeId).toBe("50:7");
        expect(getGraphWidgetValueCandidates(graph, "50", "ckpt_name")).toEqual([
            "base.ckpt",
            "xl.ckpt",
        ]);
    });

    it("finds compare targets through promoted widget metadata", () => {
        const graph = createGraphFixture();
        const compareTarget = findComparableWidget(graph, "50");
        const resolved = resolveGraphWidget(graph, "50", "ckpt_name");

        expect(compareTarget?.nodeId).toBe("50:7");
        expect(compareTarget?.widgetName).toBe("ckpt_name");
        expect(resolved?.nodeEntry.id).toBe("50:7");
        expect(resolved?.widget.name).toBe("ckpt_name");
    });

    it("preserves host-shaped sourceExecutionId metadata for promoted widgets", () => {
        const graph = createHostShapedGraphFixture();
        const widgetCatalog = getGraphWidgetCatalog(graph, "host-pack");
        const promotedWidget = widgetCatalog.find((entry) => entry.name === "ckpt_name");
        const resolved = resolveGraphWidget(graph, "host-pack", "ckpt_name");

        expect(promotedWidget).toMatchObject({
            isPromoted: true,
            sourceNodeId: "source-loader",
            sourceExecutionId: "host-pack:source-loader",
            sourceWidgetName: "ckpt_name",
            resolvedNodeId: "host-pack:source-loader",
            resolvedWidgetName: "ckpt_name",
        });
        expect(resolved?.nodeEntry.id).toBe("host-pack:source-loader");
        expect(getGraphWidgetValueCandidates(graph, "host-pack", "ckpt_name")).toEqual([
            "base.ckpt",
            "xl.ckpt",
        ]);
    });

    it("edits a store-backed host projection without inventing source fields", () => {
        const store = { value: 512 };
        const projection = {
            name: "width",
            type: "number",
            options: { min: 1, max: 8192 },
            get value() { return store.value; },
            set value(next) { store.value = next; },
        };
        Object.defineProperty(projection, "widgetId", {
            value: "root/14/width", enumerable: false,
        });
        const source = { id: 7, widgets: [{ name: "width", value: 512 }] };
        const host = {
            id: 14,
            type: "SubgraphNode",
            widgets: [projection],
            inputs: [{ name: "width", widgetId: projection.widgetId }],
            subgraph: { _nodes: [source] },
        };
        const graph = { _nodes: [host] };

        expect(Object.keys(projection)).not.toContain("widgetId");
        expect(getGraphWidgetCatalog(graph, "14")[0].value).toBe(512);
        const resolved = resolveGraphWidget(graph, "14", "width");
        expect(resolved?.widget).toBe(projection);
        resolved.widget.value = 513;
        expect(projection.value).toBe(513);
        expect(source.widgets[0].value).toBe(512);
    });

    it("keeps non-numeric node ids stable and catalogs new structured widget types", () => {
        const graph = createHostShapedGraphFixture();
        const catalog = getGraphNodeCatalog(graph);
        const widgetCatalog = getGraphWidgetCatalog(graph, "loader-alpha");
        const compareTarget = findComparableWidget(graph, "loader-alpha");

        expect(catalog.map((entry) => entry.id)).toContain("loader-alpha");
        expect(widgetCatalog.map((entry) => [entry.name, entry.type])).toEqual([
            ["palette", "COLORS"],
            ["regions", "BOUNDING_BOXES"],
            ["ckpt_name", "combo"],
        ]);
        expect(resolveGraphWidget(graph, "loader-alpha", "palette")?.nodeEntry.id).toBe("loader-alpha");
        expect(compareTarget?.nodeId).toBe("loader-alpha");
    });

    it("omits structured and presentation-ambiguous values from Parameter Lab candidates", () => {
        const graph = createHostShapedGraphFixture();
        const node = graph.getNodeById("loader-alpha");
        node.widgets.push({
            name: "video_edit",
            type: "VIDEO_EDIT",
            value: { trim: [0, 1] },
            options: {
                values: [
                    { trim: [0, 1] },
                    ["structured"],
                    null,
                    Number.NaN,
                    1,
                    "1",
                    true,
                    "true",
                    "valid",
                ],
            },
        });

        expect(getGraphWidgetValueCandidates(graph, "loader-alpha", "video_edit")).toEqual([
            1,
            true,
            "valid",
        ]);
    });
});


describe("opaque graph execution identities", () => {
    function fixture(id, parent = null) {
        const widget = { name: "ckpt_name", type: "combo", value: "base.ckpt", options: { values: ["base.ckpt", "xl.ckpt"] } };
        const node = { id, type: "CheckpointLoaderSimple", widgets: [widget] };
        const inner = { _nodes: [node] };
        return { node, widget, graph: parent === null ? inner : { _nodes: [{ id: parent, subgraph: inner, widgets: [] }] },
            executionId: parent === null ? id : `${parent}:${id}` };
    }

    it.each([
        ["insert:operation-1:root:node:7", null],
        ["child:operation:7", "pack:root"],
        [7, null],
        ["loader-alpha", null],
        [7, "pack"],
    ])("resolves actual catalog identity %s under %s across all consumers", (id, parent) => {
        const { graph, node, widget, executionId } = fixture(id, parent);
        const key = String(executionId);
        expect(getGraphNodeCatalog(graph).some((entry) => entry.id === key)).toBe(true);
        expect(getGraphNodeEntry(graph, key)?.node).toBe(node);
        expect(resolveGraphWidget(graph, key, "ckpt_name")?.widget).toBe(widget);
        expect(getGraphWidgetCatalog(graph, key).map((entry) => entry.name)).toEqual(["ckpt_name"]);
        expect(getGraphWidgetValueCandidates(graph, key, "ckpt_name")).toEqual(["base.ckpt", "xl.ckpt"]);
        expect(findComparableWidget(graph, key)?.nodeId).toBe(key);
    });

    it("keeps a unique raw colon ID fallback and rejects duplicate raw IDs", () => {
        const { graph, node } = fixture("child:opaque", "pack");
        expect(getGraphNodeEntry(graph, "child:opaque")?.node).toBe(node);
        const other = fixture("child:opaque", "other");
        graph._nodes.push(...other.graph._nodes);
        expect(getGraphNodeEntry(graph, "child:opaque")).toBeNull();
        expect(getGraphNodeEntry(graph, "pack:child:opaque")?.node).toBe(node);
        expect(getGraphNodeEntry(graph, "other:child:opaque")?.node).toBe(other.node);
    });

    it("prefers a unique complete identity over nested raw aliases", () => {
        const root = fixture("loader:opaque");
        const nested = fixture("loader:opaque", "pack");
        const graph = { _nodes: [...root.graph._nodes, ...nested.graph._nodes] };
        expect(getGraphNodeEntry(graph, "loader:opaque")?.node).toBe(root.node);
        expect(getGraphNodeEntry(graph, "pack:loader:opaque")?.node).toBe(nested.node);
    });

    it("preserves multilevel colon paths and disambiguated promoted sources", () => {
        const first = fixture("source:A");
        const selected = fixture("source:B");
        const inner = { id: "inner:opaque", subgraph: { _nodes: [first.node, selected.node] }, widgets: [
            { name: "ckpt_name", sourceNodeId: "source:A", sourceWidgetName: "ckpt_name" },
            { name: "ckpt_name", sourceNodeId: "source:B", sourceWidgetName: "ckpt_name" },
        ] };
        const graph = { _nodes: [{ id: "pack:root", subgraph: { _nodes: [inner] }, widgets: [
            { name: "ckpt_name", sourceNodeId: "inner:opaque", sourceWidgetName: "ckpt_name", disambiguatingSourceNodeId: "source:B" },
        ] }] };
        const executionId = "pack:root:inner:opaque:source:B";
        expect(getGraphNodeEntry(graph, executionId)?.node).toBe(selected.node);
        expect(resolveGraphWidget(graph, "pack:root", "ckpt_name")?.widget).toBe(selected.widget);
        expect(findComparableWidget(graph, "pack:root")?.nodeId).toBe(executionId);
        expect(getGraphWidgetValueCandidates(graph, "pack:root", "ckpt_name")).toEqual(["base.ckpt", "xl.ckpt"]);
        expect(first.widget.value).toBe("base.ckpt");
    });

    it("rejects root versus nested execution collisions including object lookups", () => {
        const direct = fixture("a:b");
        const nested = fixture("b", "a");
        const graph = { _nodes: [...direct.graph._nodes, ...nested.graph._nodes] };
        expect(getGraphNodeCatalog(graph).filter((entry) => entry.id === "a:b")).toHaveLength(2);
        expect(getGraphNodeEntry(graph, "a:b")).toBeNull();
        expect(resolveGraphWidget(graph, "a:b", "ckpt_name")).toBeNull();
        expect(resolveGraphWidget(graph, direct.node, "ckpt_name")).toBeNull();
        expect(getGraphWidgetCatalog(graph, "a:b")).toEqual([]);
        expect(getGraphWidgetValueCandidates(graph, "a:b", "ckpt_name")).toEqual([]);
        expect(findComparableWidget(graph, direct.node)).toBeNull();
        expect(direct.widget.value).toBe("base.ckpt");
        expect(nested.widget.value).toBe("base.ckpt");
    });

    it("rejects duplicate direct IDs and missing targets", () => {
        const first = fixture("duplicate");
        const second = fixture("duplicate");
        const graph = { _nodes: [first.node, second.node] };
        expect(getGraphNodeEntry(graph, "duplicate")).toBeNull();
        expect(getGraphNodeEntry(graph, "missing")).toBeNull();
        expect(resolveGraphWidget(graph, "missing", "ckpt_name")).toBeNull();
    });

    it("resolves promoted child colon IDs, but never falls back from a broken binding", () => {
        const target = fixture("child:opaque", "pack");
        const host = target.graph._nodes[0];
        host.widgets = [{ name: "ckpt_name", value: "outer-view", sourceNodeId: "child:opaque", sourceWidgetName: "ckpt_name" }];
        expect(resolveGraphWidget(target.graph, "pack", "ckpt_name")?.widget).toBe(target.widget);
        host.subgraph._nodes.push({ ...target.node });
        expect(resolveGraphWidget(target.graph, "pack", "ckpt_name")).toBeNull();
        host.subgraph._nodes = [];
        expect(resolveGraphWidget(target.graph, "pack", "ckpt_name")).toBeNull();
        expect(host.widgets[0].value).toBe("outer-view");
    });

    it("fails closed for cyclic and over-depth promoted bindings", () => {
        const widget = { name: "ckpt_name", value: "untouched", sourceNodeId: "loop", sourceWidgetName: "ckpt_name" };
        const node = { id: "loop", widgets: [widget] };
        const cycle = { _nodes: [node] };
        node.subgraph = cycle;
        expect(getGraphNodeCatalog(cycle)).toHaveLength(1);
        expect(resolveGraphWidget(cycle, "loop", "ckpt_name")).toBeNull();
        let graph = { _nodes: [{ id: "leaf", widgets: [{ name: "ckpt_name", value: "leaf-value" }] }] };
        let childId = "leaf";
        for (let index = 0; index < 25; index += 1) {
            const id = `parent${index}`;
            graph = { _nodes: [{ id, subgraph: graph, widgets: [{ name: "ckpt_name", value: "outer-view", sourceNodeId: childId, sourceWidgetName: "ckpt_name" }] }] };
            childId = id;
        }
        expect(resolveGraphWidget(graph, childId, "ckpt_name")).toBeNull();
        expect(widget.value).toBe("untouched");
    });
});
