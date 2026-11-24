import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const hostStub = (name) =>
    fileURLToPath(new URL(`./tests/frontend/unit/support/${name}`, import.meta.url));

export default defineConfig({
    resolve: {
        alias: [
            // Anchored on purpose: only the exact host script specifiers that web modules import
            // (`../scripts/app.js`, `../../../scripts/api.js`, ...), never a prefix or a lookalike.
            { find: /^(?:\.\.\/)+scripts\/app\.js$/, replacement: hostStub("host_app_stub.js") },
            { find: /^(?:\.\.\/)+scripts\/api\.js$/, replacement: hostStub("host_api_stub.js") },
        ],
    },
    test: {
        environment: "jsdom",
        setupFiles: ["./tests/frontend/unit/setup.js"],
        include: ["tests/frontend/unit/**/*.test.js"],
        restoreMocks: true,
        clearMocks: true,
    },
});
