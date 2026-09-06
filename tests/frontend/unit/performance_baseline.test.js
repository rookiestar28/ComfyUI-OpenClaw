import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
    extractHistoryImageRefs,
    extractHistoryOutputRefs,
} from "../../../web/openclaw_asset_refs.js";


const policy = JSON.parse(readFileSync(
    resolve(process.cwd(), "tests/performance_baseline_policy.json"),
    "utf8",
));
const workload = policy.workloads.find((item) => item.id === "frontend_history_outputs");

function stableValue(value) {
    if (Array.isArray(value)) {
        return value.map(stableValue);
    }
    if (value && typeof value === "object") {
        return Object.fromEntries(
            Object.keys(value).sort().map((key) => [key, stableValue(value[key])]),
        );
    }
    return value;
}

function canonicalDigest(value) {
    return createHash("sha256")
        .update(JSON.stringify(stableValue(value)))
        .digest("hex");
}

function seededGenerator(seed) {
    let state = seed >>> 0;
    return () => {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        return state;
    };
}

function runFrontendProbe() {
    const next = seededGenerator(workload.seed);
    const outputs = {};
    const { nodes, refs_per_node: refsPerNode } = workload.input;
    for (let nodeIndex = 0; nodeIndex < nodes; nodeIndex += 1) {
        outputs[String(nodeIndex)] = {
            images: Array.from({ length: refsPerNode }, (_, refIndex) => ({
                filename: `scale-${nodeIndex.toString().padStart(3, "0")}-${refIndex}.png`,
                subfolder: `batch-${next() % 8}`,
                type: (next() % 2) === 0 ? "output" : "temp",
            })),
        };
    }

    const started = performance.now();
    const normalized = extractHistoryOutputRefs({ outputs });
    const images = extractHistoryImageRefs({ outputs });
    const elapsedMs = performance.now() - started;
    const serialized = JSON.stringify(normalized);
    return {
        deterministic: {
            input_refs: nodes * refsPerNode,
            normalized_outputs: normalized.length,
            image_outputs: images.length,
            serialized_bytes: Buffer.byteLength(serialized, "utf8"),
            digest: canonicalDigest(normalized),
        },
        elapsedMs,
    };
}

describe("R218 deterministic frontend scale baseline", () => {
    it("matches fixed cardinality, serialization, and digest budgets", () => {
        expect(policy.schema_version).toBe(1);
        expect(policy.timing.enforcement).toBe("advisory_only");
        expect(workload).toBeTruthy();

        const { deterministic, elapsedMs } = runFrontendProbe();
        const expected = workload.expected;
        for (const [key, value] of Object.entries(expected.exact)) {
            expect(deterministic[key]).toBe(value);
        }
        expect(deterministic.serialized_bytes).toBeLessThanOrEqual(
            expected.max_serialized_bytes,
        );
        expect(deterministic.digest).toBe(expected.digest_sha256);
        expect(elapsedMs).toBeGreaterThanOrEqual(0);
    });

    it("compares repeated deterministic output without enforcing elapsed time", () => {
        const first = runFrontendProbe();
        const second = runFrontendProbe();
        expect(first.deterministic).toEqual(second.deterministic);
        expect(first.elapsedMs).toBeGreaterThanOrEqual(0);
        expect(second.elapsedMs).toBeGreaterThanOrEqual(0);
    });
});
