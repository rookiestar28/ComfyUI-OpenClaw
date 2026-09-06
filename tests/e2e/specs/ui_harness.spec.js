const { test, expect } = require("@playwright/test");

test("web harness self-tests pass (includes R55 helpers)", async ({ page, baseURL }) => {
    const harnessUrl = new URL("/tests/frontend/e2e-harness.html", baseURL).toString();
    await page.goto(harnessUrl);

    await page.waitForFunction(() => window.__OPENCLAW_E2E_DONE__ === true, null, {
        timeout: 30000,
    });

    const results = await page.evaluate(() => window.__OPENCLAW_E2E_RESULTS__);
    expect(results.failed, JSON.stringify(results, null, 2)).toBe(0);
});
