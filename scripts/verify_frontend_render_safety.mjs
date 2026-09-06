/**
 * S104: verify the pinned frontend render-safety baseline.
 *
 * The repository still renders some tabs through HTML string templates. This
 * verifier does not try to prove those templates safe; the rendered-DOM tests do
 * that. It pins how many *dynamic* HTML sinks each file is allowed to have so a
 * new unescaped interpolation cannot be added quietly, and it enforces that
 * exactly one production `escapeHtml` owner exists.
 *
 * Classification of an `innerHTML` assignment:
 *   - `clearing`  RHS is an empty string literal
 *   - `constant`  RHS is a string or template literal with no substitution
 *   - `dynamic`   anything else (substitution, concatenation, variable, call)
 *
 * Only `dynamic` sinks are counted. Counts must match the policy exactly:
 * an increase is a regression, and a decrease means the policy must be
 * ratcheted down in the same change that earned it.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const POLICY_PATH = path.join(ROOT, "tests", "frontend_render_safety_policy.json");
const SCAN_ROOT = path.join(ROOT, "web");
const EXCLUDED_DIRS = new Set(["tests", "node_modules", "docs"]);
const ESCAPE_OWNER = "web/openclaw_text_safety.js";

const SINK_PROPERTIES = ["innerHTML", "outerHTML"];
const SINK_METHODS = ["insertAdjacentHTML", "write", "writeln"];

function toPosix(value) {
    return value.split(path.sep).join("/");
}

function listSources(dir) {
    const found = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (EXCLUDED_DIRS.has(entry.name)) continue;
            found.push(...listSources(full));
        } else if (entry.isFile() && entry.name.endsWith(".js")) {
            found.push(full);
        }
    }
    return found.sort();
}

function skipWhitespaceAndComments(source, index) {
    let i = index;
    while (i < source.length) {
        const char = source[i];
        if (char === " " || char === "\t" || char === "\r" || char === "\n") {
            i += 1;
        } else if (char === "/" && source[i + 1] === "/") {
            while (i < source.length && source[i] !== "\n") i += 1;
        } else if (char === "/" && source[i + 1] === "*") {
            const end = source.indexOf("*/", i + 2);
            i = end === -1 ? source.length : end + 2;
        } else {
            break;
        }
    }
    return i;
}

function readStringLiteral(source, index) {
    const quote = source[index];
    let i = index + 1;
    let body = "";
    while (i < source.length) {
        const char = source[i];
        if (char === "\\") {
            body += source[i + 1] ?? "";
            i += 2;
            continue;
        }
        if (char === quote) {
            return { end: i + 1, body };
        }
        body += char;
        i += 1;
    }
    return { end: source.length, body };
}

/** Read a template literal and report whether it contains a `${...}` substitution. */
function readTemplateLiteral(source, index) {
    let i = index + 1;
    let substituted = false;
    let body = "";
    while (i < source.length) {
        const char = source[i];
        if (char === "\\") {
            body += source[i + 1] ?? "";
            i += 2;
            continue;
        }
        if (char === "`") {
            return { end: i + 1, substituted, body };
        }
        if (char === "$" && source[i + 1] === "{") {
            substituted = true;
            let depth = 1;
            i += 2;
            while (i < source.length && depth > 0) {
                if (source[i] === "{") depth += 1;
                else if (source[i] === "}") depth -= 1;
                else if (source[i] === "`") {
                    i = readTemplateLiteral(source, i).end - 1;
                } else if (source[i] === '"' || source[i] === "'") {
                    i = readStringLiteral(source, i).end - 1;
                }
                i += 1;
            }
            continue;
        }
        body += char;
        i += 1;
    }
    return { end: source.length, substituted, body };
}

/**
 * Report whether the expression continues past a leading literal.
 *
 * CRITICAL: `node.innerHTML = "<b>" + value` starts with a substitution-free
 * literal but is a dynamic sink. Classifying on the first literal alone would
 * let concatenation and member calls through the ratchet.
 */
function continuesExpression(source, index) {
    const next = skipWhitespaceAndComments(source, index);
    return ["+", "?", ".", "[", "|", "&"].includes(source[next]);
}

function lineOf(source, index) {
    let line = 1;
    for (let i = 0; i < index && i < source.length; i += 1) {
        if (source[i] === "\n") line += 1;
    }
    return line;
}

/** Classify every HTML sink assignment in one source file. */
export function classifySinks(source) {
    const sinks = [];
    const assignment = new RegExp(`\\.(${SINK_PROPERTIES.join("|")})\\s*(\\+?=)`, "g");
    for (const match of source.matchAll(assignment)) {
        const property = match[1];
        const compound = match[2] === "+=";
        const valueStart = skipWhitespaceAndComments(source, match.index + match[0].length);
        const char = source[valueStart];
        let kind;
        if (compound) {
            kind = "dynamic";
        } else if (char === '"' || char === "'") {
            const literal = readStringLiteral(source, valueStart);
            if (continuesExpression(source, literal.end)) kind = "dynamic";
            else kind = literal.body === "" ? "clearing" : "constant";
        } else if (char === "`") {
            const template = readTemplateLiteral(source, valueStart);
            if (template.substituted || continuesExpression(source, template.end)) kind = "dynamic";
            else kind = template.body === "" ? "clearing" : "constant";
        } else {
            kind = "dynamic";
        }
        sinks.push({ property, kind, line: lineOf(source, match.index) });
    }

    const call = new RegExp(`\\.(${SINK_METHODS.join("|")})\\s*\\(`, "g");
    for (const match of source.matchAll(call)) {
        sinks.push({ property: match[1], kind: "dynamic", line: lineOf(source, match.index) });
    }

    return sinks;
}

export function buildReport() {
    const files = {};
    const escapeOwners = [];
    for (const absolute of listSources(SCAN_ROOT)) {
        const relative = toPosix(path.relative(ROOT, absolute));
        const source = fs.readFileSync(absolute, "utf8");
        if (/\bfunction\s+escapeHtml\s*\(/.test(source) || /\bescapeHtml\s*=\s*(?:function|\()/.test(source)) {
            escapeOwners.push(relative);
        }
        const sinks = classifySinks(source);
        const dynamic = sinks.filter((sink) => sink.kind === "dynamic");
        if (dynamic.length) {
            files[relative] = { dynamic_sinks: dynamic.length, lines: dynamic.map((sink) => sink.line) };
        }
    }
    return { escape_owners: escapeOwners.sort(), files };
}

export function readPolicy() {
    return JSON.parse(fs.readFileSync(POLICY_PATH, "utf8"));
}

/**
 * Compare a scan report against a policy document.
 *
 * IMPORTANT: kept pure and exported so the ratchet's own failure modes are
 * testable without writing to the repository.
 */
export function compareReport(report, policy) {
    const failures = [];

    if (report.escape_owners.length !== 1 || report.escape_owners[0] !== ESCAPE_OWNER) {
        failures.push(
            `expected exactly one production escapeHtml owner (${ESCAPE_OWNER}), found: ` +
                `${report.escape_owners.join(", ") || "none"}`,
        );
    }

    const pinned = policy.files || {};
    for (const [file, entry] of Object.entries(pinned)) {
        const actual = report.files[file]?.dynamic_sinks || 0;
        if (actual > entry.dynamic_sinks) {
            failures.push(
                `${file}: ${actual} dynamic HTML sinks exceeds pinned ${entry.dynamic_sinks} ` +
                    `(lines ${report.files[file].lines.join(", ")})`,
            );
        } else if (actual < entry.dynamic_sinks) {
            failures.push(
                `${file}: ${actual} dynamic HTML sinks is below pinned ${entry.dynamic_sinks}; ` +
                    "ratchet the policy down in this change",
            );
        }
    }

    for (const [file, entry] of Object.entries(report.files)) {
        if (!(file in pinned)) {
            failures.push(
                `${file}: ${entry.dynamic_sinks} dynamic HTML sinks in an unpinned file ` +
                    `(lines ${entry.lines.join(", ")}); render through textContent/DOM construction`,
            );
        }
    }

    if (failures.length) {
        return { ok: false, message: "RENDER-SAFETY-FAIL", failures };
    }
    return { ok: true, message: "RENDER-SAFETY-PASS", failures: [] };
}

export function verifyRenderSafety() {
    return compareReport(buildReport(), readPolicy());
}

const isDirectRun = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectRun) {
    const result = verifyRenderSafety();
    if (!result.ok) {
        for (const failure of result.failures) {
            console.error(`[render-safety] ${failure}`);
        }
        console.error(result.message);
        process.exit(1);
    }
    console.log(result.message);
}
