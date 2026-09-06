/** Verify the frozen R224 Settings/API frontend contract. */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { stableTextDigest } from "./contract_digest.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONTRACT_PATH = path.join(ROOT, "tests", "frontend", "fixtures", "frontend_decomposition_contract_r224.json");

function canonicalJson(value) {
    return `${JSON.stringify(value, null, 2)}\n`;
}

function read(relativePath) {
    return fs.readFileSync(path.join(ROOT, relativePath), "utf8");
}

function familySources(directory, prefix) {
    return fs.readdirSync(path.join(ROOT, directory))
        .filter((name) => name === `${prefix}.js` || name.startsWith(`${prefix}_`))
        .sort()
        .map((name) => read(path.join(directory, name)))
        .join("\n");
}

function uniqueSorted(values) {
    return [...new Set(values)].sort();
}

function matches(source, pattern, group = 1) {
    return [...source.matchAll(pattern)].map((match) => match[group]);
}

function methodSignatures(source) {
    const result = {};
    const pattern = /^\s{4}(?:async\s+)?([A-Za-z_$][\w$]*)\(([^)]*)\)\s*\{/gm;
    for (const match of source.matchAll(pattern)) {
        result[match[1]] = match[2].replace(/\s+/g, " ").trim();
    }
    return Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
}

function digest(relativePath) {
    return stableTextDigest(path.join(ROOT, relativePath));
}

export function buildContract() {
    const apiFacade = read("web/openclaw_api.js");
    const settingsFacade = read("web/tabs/settings_tab.js");
    const apiSources = familySources("web", "openclaw_api");
    const settingsSources = familySources("web/tabs", "settings_tab");
    return {
        schema_version: 1,
        api: {
            exports: matches(apiFacade, /^export\s+(?:class|const)\s+([A-Za-z_$][\w$]*)/gm),
            methods: methodSignatures(apiSources),
            constructor_state: uniqueSorted(matches(
                apiFacade,
                /^\s{8}this\.([A-Za-z_$][\w$]*)\s*=/gm,
            )),
            path_suffixes: uniqueSorted(matches(apiSources, /this\._path\("([^"]+)"\)/g)),
            compatibility_seams: [
                "fetch",
                "_fetchWithCandidates",
                "_capabilitiesCache",
                "_capabilitiesCacheTs",
                "streamSSEPost",
                "subscribeEvents",
            ],
        },
        settings: {
            exports: matches(settingsFacade, /^export\s+const\s+([A-Za-z_$][\w$]*)/gm),
            identity: {
                id: settingsFacade.match(/\bid:\s*"([^"]+)"/)?.[1] || "",
                title: settingsFacade.match(/\btitle:\s*"([^"]+)"/)?.[1] || "",
                icon: settingsFacade.match(/\bicon:\s*"([^"]+)"/)?.[1] || "",
            },
            dom_ids: uniqueSorted(matches(settingsSources, /id="(openclaw-[^"]+)"/g)),
            class_tokens: uniqueSorted(matches(settingsSources, /\b(openclaw-[a-z0-9-]+)\b/g)),
            section_headings: uniqueSorted(matches(
                settingsSources,
                /create(?:Collapsible)?Section\("([^"]+)"/g,
            )),
            compatibility_seams: ["settingsTab", "settingsTab.render"],
        },
        upstream_contract_digests: {
            "tests/api_route_contract_r220.json": digest("tests/api_route_contract_r220.json"),
            "tests/api_config_contract_r221.json": digest("tests/api_config_contract_r221.json"),
            "tests/platform_adapter_contract_r223.json": digest("tests/platform_adapter_contract_r223.json"),
        },
    };
}

export function verifyContract({ writeBaseline = false } = {}) {
    const actual = buildContract();
    if (writeBaseline) {
        fs.mkdirSync(path.dirname(CONTRACT_PATH), { recursive: true });
        fs.writeFileSync(CONTRACT_PATH, canonicalJson(actual), "utf8");
        return { ok: true, message: `FRONTEND-CONTRACT-WRITTEN:${CONTRACT_PATH}` };
    }
    const expected = JSON.parse(fs.readFileSync(CONTRACT_PATH, "utf8"));
    const ok = canonicalJson(actual) === canonicalJson(expected);
    return { ok, message: ok ? "FRONTEND-CONTRACT-PASS" : "FRONTEND-CONTRACT-FAIL" };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const result = verifyContract({ writeBaseline: process.argv.includes("--write-baseline") });
    console.log(result.message);
    process.exit(result.ok ? 0 : 1);
}
