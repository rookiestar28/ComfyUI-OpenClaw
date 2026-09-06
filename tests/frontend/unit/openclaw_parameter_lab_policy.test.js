import { describe, expect, it } from "vitest";
import {
    PARAMETER_LAB_POLICY,
    filterParameterLabCandidates,
    validateParameterLabDimensions,
    validateParameterLabRequestBody,
    validateParameterLabScalar,
    validateParameterLabWorkflow,
} from "../../../web/openclaw_parameter_lab_policy.js";

describe("openclaw_parameter_lab_policy", () => {
    it("freezes the versioned backend-parity limit contract", () => {
        expect(PARAMETER_LAB_POLICY).toEqual({
            version: "1.0",
            maxRequestBytes: 5 * 1024 * 1024,
            maxWorkflowUtf8Bytes: 4 * 1024 * 1024,
            maxSweepDimensions: 8,
            maxValuesPerDimension: 50,
            maxNodeIdUtf8Bytes: 128,
            maxWidgetNameUtf8Bytes: 256,
            maxScalarStringUtf8Bytes: 16 * 1024,
            maxPlanUtf8Bytes: 8 * 1024 * 1024,
            maxSweepCombinations: 50,
            maxCompareItems: 8,
        });
        expect(Object.isFrozen(PARAMETER_LAB_POLICY)).toBe(true);
    });

    it("filters structured, non-finite, oversized, and presentation-ambiguous candidates", () => {
        expect(
            filterParameterLabCandidates([
                { rich: true },
                ["structured"],
                null,
                Number.NaN,
                Number.POSITIVE_INFINITY,
                1,
                "1",
                true,
                "true",
                "界".repeat(5462),
                "valid",
            ])
        ).toEqual([1, true, "valid"]);
    });

    it("validates dimensions with stable content-free reasons", () => {
        expect(
            validateParameterLabDimensions([
                { node_id: "loader-alpha", widget_name: "seed", values: [1, false, "x"] },
            ])
        ).toEqual({ ok: true, reason: "" });

        const invalidCases = [
            [[], "dimensions_required"],
            [
                Array.from({ length: 9 }, (_, index) => ({
                    node_id: index,
                    widget_name: "seed",
                    values: [index],
                })),
                "too_many_dimensions",
            ],
            [
                [{ node_id: "bad.id", widget_name: "seed", values: [1] }],
                "invalid_node_id",
            ],
            [
                [{ node_id: 1, widget_name: "seed", values: [1, "1"] }],
                "duplicate_ambiguous_value",
            ],
            [
                [
                    { node_id: 1, widget_name: "seed", values: [1] },
                    { node_id: "1", widget_name: "seed", values: [2] },
                ],
                "duplicate_dimension",
            ],
        ];
        for (const [dimensions, reason] of invalidCases) {
            expect(validateParameterLabDimensions(dimensions)).toEqual({ ok: false, reason });
        }
    });

    it("validates manual scalar entries before UI state mutation", () => {
        expect(validateParameterLabScalar("valid")).toEqual({ ok: true, reason: "" });
        expect(validateParameterLabScalar({ rich: true })).toEqual({
            ok: false,
            reason: "invalid_scalar_value",
        });
        expect(validateParameterLabScalar("界".repeat(5462))).toEqual({
            ok: false,
            reason: "scalar_string_too_large",
        });
    });

    it("measures workflow and request limits in UTF-8 bytes", () => {
        expect(validateParameterLabWorkflow("{}")).toEqual({ ok: true, reason: "" });
        expect(validateParameterLabWorkflow("界".repeat(1398102))).toEqual({
            ok: false,
            reason: "workflow_too_large",
        });

        expect(
            validateParameterLabRequestBody({
                workflow_json: "{}",
                params: [{ node_id: 1, widget_name: "seed", values: [1] }],
            })
        ).toEqual({ ok: true, reason: "" });
        expect(
            validateParameterLabRequestBody({
                workflow_json: "{}",
                params: [
                    {
                        node_id: 1,
                        widget_name: "seed",
                        values: ["x".repeat(5 * 1024 * 1024)],
                    },
                ],
            })
        ).toEqual({ ok: false, reason: "payload_too_large" });
    });
});
