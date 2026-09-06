import { describe, expect, it } from "vitest";
import {
    appendChildren,
    applyLegacyClassAliases,
    buildLegacyAliasClassTokens,
    createDomElement,
    makeEl,
    normalizeLegacyClassTokens,
    normalizeLegacyClassNames,
    parseJsonSafe,
    parseJsonOrThrow,
    queryRequired,
    isAbortError,
} from "../../../web/openclaw_utils.js";

describe("openclaw_utils", () => {
    it("creates elements with class and text", () => {
        const el = makeEl("div", "openclaw-card", "Hello");
        expect(el.tagName).toBe("DIV");
        expect(el.className).toBe("openclaw-card");
        expect(el.textContent).toBe("Hello");
    });

    it("creates declarative DOM elements without treating text as HTML", () => {
        const child = createDomElement("span", {
            className: "openclaw-label",
            text: "<strong>Run</strong>",
            dataset: { role: "primary" },
            attrs: { title: "Run command", "aria-live": "polite" },
        });
        const root = createDomElement("div", {
            className: "openclaw-row",
            children: [child],
        });

        expect(root.className).toBe("openclaw-row");
        expect(root.firstElementChild).toBe(child);
        expect(child.textContent).toBe("<strong>Run</strong>");
        expect(child.innerHTML).toBe("&lt;strong&gt;Run&lt;/strong&gt;");
        expect(child.dataset.role).toBe("primary");
        expect(child.getAttribute("aria-live")).toBe("polite");
    });

    it("appends only defined child nodes", () => {
        const root = document.createElement("div");
        const first = document.createElement("span");
        const second = document.createElement("button");

        appendChildren(root, [first, null, undefined, second]);

        expect(Array.from(root.children)).toEqual([first, second]);
    });

    it("queries required elements with stable owner context", () => {
        const root = createDomElement("section", {
            children: [createDomElement("button", { attrs: { id: "run" }, text: "Run" })],
        });

        expect(queryRequired(root, "#run", "Planner tab").textContent).toBe("Run");
        expect(() => queryRequired(root, "#missing", "Planner tab")).toThrow(
            "Planner tab missing required selector: #missing"
        );
    });

    it("normalizes duplicate legacy class tokens", () => {
        expect(
            normalizeLegacyClassTokens("openclaw-btn moltbot-btn openclaw-btn-primary moltbot-btn-primary openclaw-btn")
        ).toBe("openclaw-btn openclaw-btn-primary");
    });

    it("normalizes class names in a subtree", () => {
        document.body.innerHTML = `
            <section class="openclaw-panel moltbot-panel">
                <button class="openclaw-btn moltbot-btn openclaw-btn-primary moltbot-btn-primary">Run</button>
            </section>
        `;
        const root = document.body.firstElementChild;
        normalizeLegacyClassNames(root);
        expect(root.className).toBe("openclaw-panel");
        expect(root.querySelector("button").className).toBe("openclaw-btn openclaw-btn-primary");
    });

    it("derives runtime legacy aliases from canonical tokens", () => {
        expect(buildLegacyAliasClassTokens("openclaw-panel openclaw-btn")).toBe(
            "openclaw-panel openclaw-btn moltbot-panel moltbot-btn"
        );
    });

    it("applies runtime legacy aliases across a subtree", () => {
        document.body.innerHTML = `
            <section class="openclaw-panel">
                <button class="openclaw-btn openclaw-btn-primary">Run</button>
            </section>
        `;
        const root = document.body.firstElementChild;
        applyLegacyClassAliases(root);
        expect(root.className).toBe("openclaw-panel moltbot-panel");
        expect(root.querySelector("button").className).toBe(
            "openclaw-btn openclaw-btn-primary moltbot-btn moltbot-btn-primary"
        );
    });

    it("returns fallback data for invalid JSON", () => {
        const parsed = parseJsonSafe("{bad", { safe: true });
        expect(parsed.ok).toBe(false);
        expect(parsed.value).toEqual({ safe: true });
        expect(parsed.error).toBeInstanceOf(Error);
    });

    it("throws with the provided parse message", () => {
        expect(() => parseJsonOrThrow("{bad", "Broken payload")).toThrow(/Broken payload/);
    });

    it("detects abort errors by name", () => {
        expect(isAbortError(new DOMException("Cancelled", "AbortError"))).toBe(true);
        expect(isAbortError(new Error("boom"))).toBe(false);
    });
});
