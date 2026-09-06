/**
 * Shared HTML text-escaping owner for OpenClaw frontend surfaces.
 *
 * IMPORTANT: this module must stay import-free. The standalone remote admin
 * console imports it, and pulling in `openclaw_utils.js` would drag the
 * notification-store singleton (and its import-time localStorage read) onto a
 * page that deliberately loads only its own API client and host-surface stamp.
 *
 * IMPORTANT: this is the single production `escapeHtml`. Do not reintroduce a
 * local copy in a tab or console module; the render-safety policy verifier
 * rejects new owners. Escaping is defense in depth for the string-template
 * renderers that remain - prefer `textContent` and DOM construction for new code.
 */

/**
 * Escape text for interpolation into an HTML string template.
 *
 * CRITICAL: `&` is replaced first so a later rule cannot double-encode the
 * ampersand it just produced. Both quote forms are escaped so the result is
 * also safe inside a single- or double-quoted attribute value.
 *
 * CRITICAL: the argument is normalized with `String(value ?? "")` before
 * replacement. An earlier `if (!text) return ""` variant threw
 * `TypeError: text.replace is not a function` on numeric input, which was
 * reachable through pack `version` fields, and silently dropped `0`/`false`.
 *
 * @param {unknown} value - Arbitrary untrusted value.
 * @returns {string} Escaped text safe for HTML text and quoted attribute contexts.
 */
export function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
