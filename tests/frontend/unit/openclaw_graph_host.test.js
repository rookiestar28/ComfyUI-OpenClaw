import { describe, expect, it } from "vitest";
import {
    findComparableWidget,
    getGraphNodeCatalog,
    getGraphWidgetCatalog,
    getGraphWidgetValueCandidates,
    resolveGraphWidget,
} from "../../../web/openclaw_graph_host.js";

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
