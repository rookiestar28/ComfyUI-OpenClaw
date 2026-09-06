import { describe, expect, it } from "vitest";

import {
    buildReport,
    classifySinks,
    compareReport,
    readPolicy,
    verifyRenderSafety,
} from "../../../scripts/verify_frontend_render_safety.mjs";

function kinds(source) {
    return classifySinks(source).map((sink) => sink.kind);
}

describe("S104 frontend render-safety ratchet", () => {
    it("passes against the pinned baseline", () => {
        const result = verifyRenderSafety();
        expect(result.failures).toEqual([]);
        expect(result.ok).toBe(true);
    });

    it("keeps exactly one production escapeHtml owner", () => {
        expect(buildReport().escape_owners).toEqual(["web/openclaw_text_safety.js"]);
    });

    it("pins the repaired admin console at zero dynamic sinks", () => {
        expect(readPolicy().files["web/admin_console_app.js"].dynamic_sinks).toBe(0);
        expect(buildReport().files["web/admin_console_app.js"]).toBeUndefined();
    });

    it("separates clearing and reviewed constant markup from dynamic data", () => {
        expect(kinds('node.innerHTML = "";')).toEqual(["clearing"]);
        expect(kinds("node.innerHTML = ``;")).toEqual(["clearing"]);
        expect(kinds('node.innerHTML = \'<div class="tiny">No records.</div>\';')).toEqual(["constant"]);
        expect(kinds("node.innerHTML = `<div>static</div>`;")).toEqual(["constant"]);
    });

    it("classifies every interpolating or indirect assignment as dynamic", () => {
        expect(kinds("node.innerHTML = `<b>${value}</b>`;")).toEqual(["dynamic"]);
        expect(kinds('node.innerHTML = "<b>" + value + "</b>";')).toEqual(["dynamic"]);
        expect(kinds("node.innerHTML = markup;")).toEqual(["dynamic"]);
        expect(kinds("node.innerHTML = rows.map(render).join('');")).toEqual(["dynamic"]);
        expect(kinds('node.innerHTML += "<b>x</b>";')).toEqual(["dynamic"]);
        expect(kinds("node.outerHTML = `<b>${value}</b>`;")).toEqual(["dynamic"]);
        expect(kinds('node.insertAdjacentHTML("beforeend", markup);')).toEqual(["dynamic"]);
        expect(kinds("doc.write(markup);")).toEqual(["dynamic"]);
    });

    it("does not mistake a nested template for a constant", () => {
        // CRITICAL: a substitution containing its own template literal must not
        // terminate the scan early and downgrade the sink to `constant`.
        expect(kinds("node.innerHTML = `<b>${items.map((i) => `<i>${i}</i>`).join('')}</b>`;")).toEqual([
            "dynamic",
        ]);
    });

    it("ignores comments between the assignment and its value", () => {
        expect(kinds('node.innerHTML = /* reviewed */ "<div>ok</div>";')).toEqual(["constant"]);
        expect(kinds("node.innerHTML = // reviewed\n  `<b>${value}</b>`;")).toEqual(["dynamic"]);
    });

    it("fails when a pinned file gains a dynamic sink", () => {
        const policy = { files: { "web/a.js": { dynamic_sinks: 1 } } };
        const report = {
            escape_owners: ["web/openclaw_text_safety.js"],
            files: { "web/a.js": { dynamic_sinks: 2, lines: [10, 20] } },
        };

        const result = compareReport(report, policy);
        expect(result.ok).toBe(false);
        expect(result.message).toBe("RENDER-SAFETY-FAIL");
        expect(result.failures[0]).toContain("exceeds pinned 1");
    });

    it("fails when an unpinned file introduces a dynamic sink", () => {
        const result = compareReport(
            {
                escape_owners: ["web/openclaw_text_safety.js"],
                files: { "web/brand_new_tab.js": { dynamic_sinks: 1, lines: [7] } },
            },
            { files: {} },
        );

        expect(result.ok).toBe(false);
        expect(result.failures[0]).toContain("unpinned file");
    });

    it("fails when a pinned count is not ratcheted down after a repair", () => {
        const result = compareReport(
            { escape_owners: ["web/openclaw_text_safety.js"], files: {} },
            { files: { "web/a.js": { dynamic_sinks: 3 } } },
        );

        expect(result.ok).toBe(false);
        expect(result.failures[0]).toContain("ratchet the policy down");
    });

    it("fails when a second escapeHtml owner reappears", () => {
        const result = compareReport(
            {
                escape_owners: ["web/openclaw_text_safety.js", "web/tabs/packs_tab.js"],
                files: {},
            },
            { files: {} },
        );

        expect(result.ok).toBe(false);
        expect(result.failures[0]).toContain("exactly one production escapeHtml owner");
    });
});
