import { filterParameterLabCandidates } from "./openclaw_parameter_lab_policy.js";

const MAX_PROMOTED_WIDGET_DEPTH = 24;
const COMPARE_WIDGET_NAMES = new Set(["ckpt_name", "lora_name", "unet_name"]);

function normalizeNodeId(nodeId) {
    if (nodeId === null || nodeId === undefined || nodeId === "") {
        return null;
    }
    return String(nodeId);
}

function getDirectGraphNodes(graph) {
    if (!graph || typeof graph !== "object") {
        return [];
    }
    if (Array.isArray(graph._nodes)) {
        return graph._nodes;
    }
    if (Array.isArray(graph.nodes)) {
        return graph.nodes;
    }
    return [];
}

function getNodeTitle(node, fallbackId) {
    return node?.title || node?.type || `Node ${fallbackId}`;
}

function getNodeWidgets(node) {
    return Array.isArray(node?.widgets) ? node.widgets : [];
}

function getNodeSubgraph(node) {
    return node?.subgraph && typeof node.subgraph === "object" ? node.subgraph : null;
}

function isPromotedWidgetView(widget) {
    return !!widget && typeof widget === "object" && widget.sourceNodeId !== undefined && widget.sourceWidgetName !== undefined;
}

function buildNodeEntry(node, graph, executionId, pathTitles) {
    const rawId = normalizeNodeId(node?.id);
    const title = getNodeTitle(node, rawId || executionId);
    return {
        id: executionId,
        rawId,
        title,
        displayTitle: pathTitles.join(" / "),
        type: node?.type || title,
        node,
        graph,
        pathTitles,
        isNested: pathTitles.length > 1,
    };
}

function findDirectNodeByRawId(graph, rawId) {
    const normalized = normalizeNodeId(rawId);
    if (!normalized) {
        return null;
    }
    const matches = getDirectGraphNodes(graph).filter((node) => normalizeNodeId(node?.id) === normalized);
    return matches.length === 1 ? matches[0] : null;
}

function getChildNodeEntry(hostEntry, rawChildId) {
    const subgraph = getNodeSubgraph(hostEntry?.node);
    if (!subgraph) {
        return null;
    }
    const child = findDirectNodeByRawId(subgraph, rawChildId);
    if (!child) {
        return null;
    }
    const rawId = normalizeNodeId(child.id);
    const title = getNodeTitle(child, rawId || rawChildId);
    const executionId = `${hostEntry.id}:${rawId}`;
    return buildNodeEntry(child, subgraph, executionId, [...hostEntry.pathTitles, title]);
}

function findWidgetByIdentity(widgets, widgetName, sourceNodeId) {
    if (!Array.isArray(widgets) || !widgetName) {
        return null;
    }

    if (sourceNodeId !== null && sourceNodeId !== undefined && sourceNodeId !== "") {
        const normalizedSourceId = String(sourceNodeId);
        return (
            widgets.find(
                (entry) =>
                    isPromotedWidgetView(entry) &&
                    normalizeNodeId(entry.disambiguatingSourceNodeId ?? entry.sourceNodeId) === normalizedSourceId &&
                    (entry.sourceWidgetName === widgetName || entry.name === widgetName)
            ) || null
        );
    }

    return widgets.find((entry) => entry?.name === widgetName || entry?.sourceWidgetName === widgetName) || null;
}

function resolvePromotedWidget(entry, widget) {
    let currentEntry = entry;
    let currentWidget = widget;

    for (let depth = 0; depth < MAX_PROMOTED_WIDGET_DEPTH; depth += 1) {
        if (!isPromotedWidgetView(currentWidget)) {
            return {
                hostEntry: entry,
                nodeEntry: currentEntry,
                node: currentEntry.node,
                widget: currentWidget,
                promotedDepth: depth,
            };
        }

        const sourceEntry = getChildNodeEntry(currentEntry, currentWidget.sourceNodeId);
        if (!sourceEntry) {
            return null;
        }

        const sourceWidget = findWidgetByIdentity(
            getNodeWidgets(sourceEntry.node),
            currentWidget.sourceWidgetName,
            currentWidget.disambiguatingSourceNodeId
        );
        if (!sourceWidget) {
            return null;
        }

        currentEntry = sourceEntry;
        currentWidget = sourceWidget;
    }

    return null;
}

export function getGraphNodeCatalog(graph) {
    const entries = [];
    const ancestorGraphs = new WeakSet();

    function visit(currentGraph, parentExecutionId = "", parentPathTitles = []) {
        if (!currentGraph || typeof currentGraph !== "object" || ancestorGraphs.has(currentGraph)) {
            return;
        }
        // IMPORTANT: instances share definition graphs. Stop ancestor cycles only;
        // global deduplication hides later identities and can allow a wrong-node write.
        ancestorGraphs.add(currentGraph);

        for (const node of getDirectGraphNodes(currentGraph)) {
            const rawId = normalizeNodeId(node?.id);
            if (!rawId) {
                continue;
            }
            const title = getNodeTitle(node, rawId);
            const executionId = parentExecutionId ? `${parentExecutionId}:${rawId}` : rawId;
            const pathTitles = [...parentPathTitles, title];
            const entry = buildNodeEntry(node, currentGraph, executionId, pathTitles);
            entries.push(entry);

            const subgraph = getNodeSubgraph(node);
            if (subgraph && subgraph !== currentGraph) {
                visit(subgraph, executionId, pathTitles);
            }
        }
        ancestorGraphs.delete(currentGraph);
    }

    visit(graph);
    return entries;
}

export function getGraphNodeEntry(graph, nodeId) {
    const normalized = normalizeNodeId(nodeId);
    if (!normalized) {
        return null;
    }

    // IMPORTANT: legal local IDs contain colons. Match actual complete identities;
    // splitting or falling back after a collision can write to the wrong node.
    const catalog = getGraphNodeCatalog(graph);
    const exact = catalog.filter((entry) => entry.id === normalized);
    if (exact.length > 0) {
        return exact.length === 1 ? exact[0] : null;
    }
    const raw = catalog.filter((entry) => entry.rawId === normalized);
    return raw.length === 1 ? raw[0] : null;
}

export function getGraphNodeEntryByObject(graph, targetNode) {
    if (!targetNode || typeof targetNode !== "object") {
        return null;
    }
    const catalog = getGraphNodeCatalog(graph);
    const matches = catalog.filter((entry) => entry.node === targetNode);
    if (matches.length !== 1 || catalog.filter((entry) => entry.id === matches[0].id).length !== 1) {
        return null;
    }
    return matches[0];
}

export function resolveGraphWidget(graph, nodeRefOrId, widgetName, sourceNodeId = null) {
    // IMPORTANT: keep nested-subgraph and promoted-widget host resolution centralized here.
    // Replacing callers with direct graph._nodes/getNodeById/node.widgets access silently drops newer frontend host shapes.
    const entry =
        typeof nodeRefOrId === "object" && nodeRefOrId
            ? getGraphNodeEntryByObject(graph, nodeRefOrId)
            : getGraphNodeEntry(graph, nodeRefOrId);
    if (!entry || !widgetName) {
        return null;
    }

    const widget = findWidgetByIdentity(getNodeWidgets(entry.node), widgetName, sourceNodeId);
    if (!widget) {
        return null;
    }

    // IMPORTANT: a broken/cyclic promoted binding must fail closed; editing the
    // outer view instead can claim success without changing the execution value.
    return resolvePromotedWidget(entry, widget);
}

export function getGraphWidgetCatalog(graph, nodeId) {
    const entry = getGraphNodeEntry(graph, nodeId);
    if (!entry) {
        return [];
    }

    return getNodeWidgets(entry.node).map((widget) => {
        const widgetName = widget?.name || widget?.sourceWidgetName || "";
        const resolved = resolveGraphWidget(
            graph,
            entry.id,
            widgetName,
            widget?.disambiguatingSourceNodeId || null
        );
        return {
            name: widgetName,
            type: widget?.type || resolved?.widget?.type || "unknown",
            value: widget?.value,
            options: widget?.options,
            isPromoted: isPromotedWidgetView(widget),
            sourceNodeId: widget?.sourceNodeId,
            sourceExecutionId: widget?.sourceExecutionId,
            sourceWidgetName: widget?.sourceWidgetName,
            resolvedNodeId: resolved?.nodeEntry?.id || entry.id,
            resolvedWidgetName: resolved?.widget?.name || widgetName,
        };
    });
}

export function getGraphWidgetValueCandidates(graph, nodeId, widgetName) {
    const resolved = resolveGraphWidget(graph, nodeId, widgetName);
    if (!resolved || !resolved.widget) {
        return [];
    }

    const opts =
        resolved.widget.options && Array.isArray(resolved.widget.options.values)
            ? [...resolved.widget.options.values]
            : [];
    if (!opts.some((candidate) => String(candidate) === String(resolved.widget.value))) {
        opts.unshift(resolved.widget.value);
    }
    return filterParameterLabCandidates(opts);
}

export function findComparableWidget(graph, nodeRefOrId) {
    const entry =
        typeof nodeRefOrId === "object" && nodeRefOrId
            ? getGraphNodeEntryByObject(graph, nodeRefOrId)
            : getGraphNodeEntry(graph, nodeRefOrId);
    if (!entry) {
        return null;
    }

    for (const widget of getNodeWidgets(entry.node)) {
        const widgetName = widget?.name || widget?.sourceWidgetName;
        if (!COMPARE_WIDGET_NAMES.has(widgetName)) {
            continue;
        }
        const resolved = resolveGraphWidget(
            graph,
            entry.id,
            widgetName,
            widget?.disambiguatingSourceNodeId || null
        );
        if (!resolved) {
            continue;
        }
        return {
            hostEntry: entry,
            nodeEntry: resolved.nodeEntry,
            widget: resolved.widget,
            hostWidgetName: widgetName,
            nodeId: resolved.nodeEntry.id,
            sourceExecutionId: widget?.sourceExecutionId,
            widgetName: resolved.widget.name || widgetName,
        };
    }

    return null;
}

export function hasComparableWidget(graph, nodeRefOrId) {
    return !!findComparableWidget(graph, nodeRefOrId);
}

const WORKFLOW_CHANGED = Object.freeze({ ok: false, reason: "workflow_changed" });

function holdWeakly(value) {
    // A released workflow graph must not be kept alive by a plan that outlives its tab.
    return typeof WeakRef === "function" ? new WeakRef(value) : { deref: () => value };
}

function readActiveWorkflow(app) {
    try {
        const workflow = app?.extensionManager?.workflow?.activeWorkflow;
        return workflow && typeof workflow === "object" ? workflow : null;
    } catch {
        return null;
    }
}

/**
 * Reads the graph of the workflow that is active right now, exactly once.
 * Newer hosts expose `rootGraphOrUndefined`; reading `rootGraph` before the host has
 * initialised logs a console error, so it is only read when that property is absent.
 */
export function readActiveWorkflowGraph(app) {
    if (!app || typeof app !== "object") {
        return null;
    }
    try {
        const graph = "rootGraphOrUndefined" in app ? app.rootGraphOrUndefined : (app.rootGraph ?? app.graph);
        return graph && typeof graph === "object" ? graph : null;
    } catch {
        return null;
    }
}

/** Captures the identity signals of the active workflow graph, or null when there is none. */
export function captureGraphBinding(app) {
    const graph = readActiveWorkflowGraph(app);
    if (!graph) {
        return null;
    }
    const workflow = readActiveWorkflow(app);
    return Object.freeze({
        graphRef: holdWeakly(graph),
        graphId: graph.id,
        workflowRef: workflow ? holdWeakly(workflow) : null,
    });
}

/**
 * Verifies that the active workflow is still the one the binding was captured from.
 * Returns `{ ok: true, graph }` with the bound graph, or `{ ok: false, reason: "workflow_changed" }`.
 */
export function verifyGraphBinding(binding, app) {
    // CRITICAL: a Parameter Lab plan must never write to, or queue from, a workflow other than the one it
    // was built from. Hosts differ: some keep one graph object per workflow (object identity changes), others
    // reconfigure one shared graph object (only `id` changes), and some expose the active workflow object.
    // Compare every signal that was captured. Resolving against the current graph alone, or dropping the
    // `id` comparison, silently retargets a stale plan onto whichever tab is open.
    const bound = typeof binding?.graphRef?.deref === "function" ? binding.graphRef.deref() : undefined;
    if (!bound) {
        return WORKFLOW_CHANGED;
    }
    const current = readActiveWorkflowGraph(app);
    if (current !== bound || current.id !== binding.graphId) {
        return WORKFLOW_CHANGED;
    }
    if (binding.workflowRef) {
        const boundWorkflow = typeof binding.workflowRef.deref === "function" ? binding.workflowRef.deref() : undefined;
        if (!boundWorkflow || readActiveWorkflow(app) !== boundWorkflow) {
            return WORKFLOW_CHANGED;
        }
    }
    return { ok: true, graph: bound };
}
