import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const helperPath = path.resolve(process.cwd(), "scripts", "contract_digest.mjs");
const temporaryDirectories = [];

afterEach(() => {
    for (const directory of temporaryDirectories.splice(0)) {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});

describe("contract digest portability", () => {
    it("normalizes text newlines without hiding content changes", async () => {
        expect(fs.existsSync(helperPath), "shared contract digest helper must exist").toBe(true);
        const { stableTextDigest } = await import(helperPath);
        const root = fs.mkdtempSync(path.join(os.tmpdir(), "openclaw-contract-digest-"));
        temporaryDirectories.push(root);
        const variants = {
            "lf.txt": "alpha\nbeta\n",
            "crlf.txt": "alpha\r\nbeta\r\n",
            "cr.txt": "alpha\rbeta\r",
        };
        for (const [name, content] of Object.entries(variants)) {
            fs.writeFileSync(path.join(root, name), content, "utf8");
        }
        fs.writeFileSync(path.join(root, "changed.txt"), "alpha\ngamma\n", "utf8");

        const normalized = new Set(
            Object.keys(variants).map((name) => stableTextDigest(path.join(root, name))),
        );
        expect(normalized.size).toBe(1);
        expect(stableTextDigest(path.join(root, "changed.txt"))).not.toBe(
            stableTextDigest(path.join(root, "lf.txt")),
        );
    });
});
