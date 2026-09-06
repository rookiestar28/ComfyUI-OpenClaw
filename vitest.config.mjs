import { defineConfig } from "vitest/config";

export default defineConfig({
    test: {
        environment: "jsdom",
        setupFiles: ["./tests/frontend/unit/setup.js"],
        include: ["tests/frontend/unit/**/*.test.js"],
        restoreMocks: true,
        clearMocks: true,
    },
});
