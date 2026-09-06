const { test, expect } = require('@playwright/test');

test('E2E harness passes', async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/tests/frontend/e2e-harness.html`);

  await page.waitForFunction(() => window.__OPENCLAW_E2E_DONE__ === true, null, {
    timeout: 30_000,
  });

  const results = await page.evaluate(() => window.__OPENCLAW_E2E_RESULTS__);
  expect(results.failed, JSON.stringify(results, null, 2)).toBe(0);
});
