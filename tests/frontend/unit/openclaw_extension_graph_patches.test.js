import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = path.resolve(import.meta.dirname, "../../../web");

// The members ComfyUI's graph bridge owns on a graph instance and on LGraph.prototype. An
// extension that replaces one of them during init/setup interferes with that bridge, so
// OpenClaw must patch none of them: that is what keeps the host bridge from ever reporting
// OpenClaw as an extension that patched its graph.
//
// ALLOWANCE: `onSerialize` is deliberately NOT in this list. The receipt coordinator wraps
// `graph.onSerialize` for one queue call at a time and restores it (see
// openclaw_parameter_lab_receipt.js). That is a queue-time callback, not a setup patch, and not
// a member of the bridge contract. If the coordinator ever began replacing `serialize` itself,
// this scan must fail, not be extended to forgive it.
const BRIDGE_MEMBERS = Object.freeze([
  "add",
  "remove",
  "configure",
  "serialize",
  "onNodeAdded",
  "onNodeRemoved",
  "beforeChange",
  "afterChange",
  "convertToSubgraph",
  "clear",
]);

const MEMBERS = BRIDGE_MEMBERS.join("|");
// A graph-like target: `LGraph`, `LGraph.prototype`, or any dotted path ending in a name that
// contains "graph" (graph, rootGraph, app.graph, this.canvas.graph, subgraph ...).
const TARGET = String.raw`(?:LGraph(?:\.prototype)?|(?:[\w$]+\.)*[\w$]*[gG]raph[\w$]*)`;
const PROPERTY_ASSIGNMENT = new RegExp(String.raw`(?<![\w$])${TARGET}\.(?:${MEMBERS})\s*=(?![=>])`, "g");
const BRACKET_ASSIGNMENT = new RegExp(
  String.raw`(?<![\w$])${TARGET}\[\s*(['"\`])(?:${MEMBERS})\1\s*\]\s*=(?![=>])`,
  "g",
);
const DEFINE_PROPERTY = new RegExp(
  String.raw`Object\.defineProperty\(\s*${TARGET}\s*,\s*(['"\`])(?:${MEMBERS})\1`,
  "g",
);

/**
 * Removes line and block comments without touching comment-looking text inside strings, so a
 * commented-out patch is not reported and a string such as "http://x" does not hide code.
 */
function stripComments(source) {
  let out = "";
  let index = 0;
  let quote = null;
  while (index < source.length) {
    const char = source[index];
    const next = source[index + 1];
    if (quote) {
      out += char;
      if (char === "\\") {
        out += next ?? "";
        index += 2;
        continue;
      }
      if (char === quote) quote = null;
      index += 1;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      quote = char;
      out += char;
      index += 1;
      continue;
    }
    if (char === "/" && next === "/") {
      while (index < source.length && source[index] !== "\n") index += 1;
      continue;
    }
    if (char === "/" && next === "*") {
      const end = source.indexOf("*/", index + 2);
      index = end === -1 ? source.length : end + 2;
      out += " ";
      continue;
    }
    out += char;
    index += 1;
  }
  return out;
}

/** Returns the source text of every bridge-member patch found in `source`. */
function findBridgeMemberPatches(source) {
  const code = stripComments(source);
  return [PROPERTY_ASSIGNMENT, BRACKET_ASSIGNMENT, DEFINE_PROPERTY].flatMap((pattern) =>
    [...code.matchAll(pattern)].map((match) => match[0].trim()),
  );
}

function listWebScripts() {
  return readdirSync(WEB_ROOT, { recursive: true })
    .map((entry) => String(entry).split(path.sep).join("/"))
    .filter((entry) => entry.endsWith(".js"))
    .sort();
}

describe("OpenClaw extension graph patches", () => {
  describe("the scan detects a bridge-member patch (sensitivity)", () => {
    const violations = {
      "an instance member assigned in setup": `
        app.registerExtension({ name: "x", async setup() { app.graph.onNodeAdded = () => {}; } });`,
      "a prototype member": "LGraph.prototype.configure = function () {};",
      "a wrapped serialize on the root graph": "app.rootGraph.serialize = wrap(app.rootGraph.serialize);",
      "a bracket assignment": 'graph["afterChange"] = noop;',
      "a defineProperty replacement": 'Object.defineProperty(app.graph, "convertToSubgraph", { value() {} });',
      "a patch after a URL on the same line": 'const u = "http://x"; this.graph.clear = noop;',
      "a patch next to a comment marker inside a string": 'const s = "/* not a comment"; graph.remove = noop;',
    };
    for (const [name, source] of Object.entries(violations)) {
      it(`reports ${name}`, () => {
        expect(findBridgeMemberPatches(source), source).not.toEqual([]);
      });
    }

    const allowed = {
      "the queue-time onSerialize wrapper": "graph.onSerialize = wrapper;",
      "a call, not an assignment": "const added = graph.add(node);",
      "a comparison": "if (graph.clear === undefined || graph.add == null) return;",
      "a read of serialize": "const snapshot = graph.serialize();",
      "a line comment": "// graph.add = noop;",
      "a block comment": "/* app.graph.remove = noop; */",
      "a canvas prototype outside the bridge contract": "LGraphCanvas.prototype.getNodeMenuOptions = fn;",
      "a non-graph target": "registry.add = noop;",
    };
    for (const [name, source] of Object.entries(allowed)) {
      it(`ignores ${name}`, () => {
        expect(findBridgeMemberPatches(source), source).toEqual([]);
      });
    }
  });

  it("scans the shipped extension scripts and finds them", () => {
    const scripts = listWebScripts();
    // Guards against a scan that silently covers nothing.
    expect(scripts.length).toBeGreaterThan(40);
    expect(scripts).toContain("openclaw.js");
    expect(scripts).toContain("openclaw_parameter_lab_receipt.js");
    expect(scripts).toContain("extensions/context_toolbox.js");
  });

  it("patches no LGraph member the host bridge owns, in any shipped script", () => {
    // The scan covers all of web/, a superset of the extension init/setup paths: no OpenClaw
    // module has a reason to replace these members at any time.
    const findings = {};
    for (const script of listWebScripts()) {
      const patches = findBridgeMemberPatches(readFileSync(path.join(WEB_ROOT, script), "utf8"));
      if (patches.length) findings[script] = patches;
    }
    expect(findings).toEqual({});
  });

  it("keeps the documented onSerialize allowance real and outside the bridge list", () => {
    expect(BRIDGE_MEMBERS).not.toContain("onSerialize");
    const receipt = readFileSync(path.join(WEB_ROOT, "openclaw_parameter_lab_receipt.js"), "utf8");
    // The coordinator wraps and restores onSerialize for one queue call; it never replaces serialize.
    expect(receipt).toContain("graph.onSerialize = wrapper;");
    expect(receipt).toContain("graph.onSerialize = previous;");
    expect(findBridgeMemberPatches(receipt)).toEqual([]);
  });
});
