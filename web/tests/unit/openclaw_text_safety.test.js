import { describe, expect, it } from "vitest";

import { escapeHtml } from "../../openclaw_text_safety.js";

describe("openclaw_text_safety", () => {
    it("escapes every character that can break out of text or attribute context", () => {
        expect(escapeHtml(`<img src="x" onerror='boom'>&`)).toBe(
            "&lt;img src=&quot;x&quot; onerror=&#039;boom&#039;&gt;&amp;",
        );
    });

    it("renders a hostile payload as inert text when interpolated into markup", () => {
        const host = document.createElement("div");
        host.innerHTML = `<span title="${escapeHtml(`" onmouseover="boom`)}">${escapeHtml(
            '<script>boom</script>',
        )}</span>`;

        const span = host.querySelector("span");
        expect(host.querySelectorAll("script")).toHaveLength(0);
        expect(span.getAttributeNames()).toEqual(["title"]);
        expect(span.getAttribute("title")).toBe('" onmouseover="boom');
        expect(span.textContent).toBe("<script>boom</script>");
    });

    it("escapes the ampersand exactly once", () => {
        // CRITICAL: `&` must be replaced before `<`/`>`/quotes, otherwise the
        // entities produced by later rules get re-encoded.
        expect(escapeHtml("&amp;")).toBe("&amp;amp;");
        expect(escapeHtml("a & b < c")).toBe("a &amp; b &lt; c");

        const host = document.createElement("div");
        host.innerHTML = escapeHtml("Tom & Jerry <tag>");
        expect(host.textContent).toBe("Tom & Jerry <tag>");
    });

    it("normalizes nullish input to an empty string", () => {
        expect(escapeHtml(null)).toBe("");
        expect(escapeHtml(undefined)).toBe("");
        expect(escapeHtml("")).toBe("");
    });

    it("accepts non-string input that the removed per-file helpers could not", () => {
        // CRITICAL: the replaced `if (!text) return ""` helpers called
        // `.replace` on the raw argument and threw on numbers. Pack `version`
        // fields reach this helper as numbers, so keep the coercion.
        expect(() => escapeHtml(3)).not.toThrow();
        expect(escapeHtml(3)).toBe("3");
        expect(escapeHtml(0)).toBe("0");
        expect(escapeHtml(false)).toBe("false");
        expect(escapeHtml(true)).toBe("true");
    });
});
