import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

async function routeTransientOpenClawEntryFailures(page, failureCount) {
  let remainingLogicalFailures = failureCount;
  let activeAttempt = null;
  let failActiveAttempt = false;
  let failedLogicalAttempts = 0;

  // IMPORTANT: keep this query-agnostic; Windows CI must intercept the harness
  // retry seam even when the dynamic import URL carries cache-busting params.
  // IMPORTANT: fail by logical attempt, not raw request count; Chromium may retry
  // one module transport internally without advancing the harness retry loop.
  await page.route('**/web/openclaw.js**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/web/openclaw.js') {
      await route.fallback();
      return;
    }

    const attempt = Number.parseInt(
      url.searchParams.get('openclaw_harness_attempt') || '',
      10,
    );
    if (!Number.isInteger(attempt) || attempt < 1) {
      await route.fallback();
      return;
    }

    if (attempt !== activeAttempt) {
      activeAttempt = attempt;
      failActiveAttempt = remainingLogicalFailures > 0;
      if (failActiveAttempt) {
        remainingLogicalFailures -= 1;
        failedLogicalAttempts += 1;
      }
    }

    if (failActiveAttempt) {
      await route.fulfill({
        status: 503,
        contentType: 'text/javascript',
        body: '// Injected transient module-fetch failure.',
      });
      return;
    }

    await route.fallback();
  });

  return {
    failedLogicalAttempts: () => failedLogicalAttempts,
  };
}

test.describe('OpenClaw Sidebar', () => {
  test.beforeEach(async ({ page }) => {
    await mockComfyUiCore(page);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
  });

  test('renders header + tabs', async ({ page }) => {
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    await expect(page.locator('.openclaw-repo-link')).toContainText('View on GitHub');
  });

  test('switching tabs does not lose content', async ({ page }) => {
    // Click a few tabs and verify active pane is non-empty
    for (const t of ['Settings', 'Jobs', 'Planner', 'Variants', 'Refiner', 'Library', 'Approvals', 'Explorer', 'Packs', 'Model Manager', 'PNG Info']) {
      await clickTab(page, t);
      const active = page.locator('.openclaw-tab-pane.active');
      await expect(active).toBeVisible();
      await expect(active).not.toBeEmpty();
    }
  });

  test('default harness bootstrap provides stable Settings and Model Manager baselines', async ({ page }) => {
    await clickTab(page, 'Settings');
    await expect(page.locator('.openclaw-log-viewer')).not.toContainText('Failed to load logs');
    await expect(page.locator('details')).toContainText('ComfyUI: test');

    await clickTab(page, 'Model Manager');
    await expect(page.locator('#mm-search-results')).toContainText('No matching models.');
    await expect(page.locator('#mm-tasks')).toContainText('No download tasks.');
    await expect(page.locator('#mm-installations')).toContainText('No managed installations.');

    await clickTab(page, 'PNG Info');
    await expect(page.locator('#pnginfo-dropzone')).toContainText('Drop an image here');
    await expect(page.locator('#pnginfo-empty-state')).toContainText('Load an image to inspect');
  });

  test('Explorer preflight surfaces inactive-branch suppressed diagnostics', async ({ page }) => {
    await page.route('**/preflight', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() !== 'POST' || !url.pathname.endsWith('/preflight')) {
        await route.fallback();
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          summary: {
            missing_nodes: 0,
            missing_models: 0,
            invalid_inputs: 0,
            suppressed_missing_nodes: 2,
            suppressed_missing_models: 1,
          },
          missing_nodes: [],
          missing_models: [],
          suppressed_missing_nodes: [
            { node_id: '5:7', class_type: 'MoltbotPromptPlanner' },
            { node_id: '5:8', class_type: 'MissingCustomNode' },
          ],
          suppressed_missing_models: [
            { node_id: '5:8', type: 'checkpoints', name: 'missing-model.safetensors' },
          ],
          notes: [
            'Inactive subgraph branches were suppressed from actionable diagnostics.',
          ],
        }),
      });
    });

    await clickTab(page, 'Explorer');
    await page.locator('.openclaw-preflight-results').waitFor({ state: 'attached' });
    await page.locator('textarea').fill(JSON.stringify({ nodes: [] }));
    await page.getByRole('button', { name: 'Run Preflight' }).click();

    const results = page.locator('.openclaw-preflight-results');
    await expect(results).toContainText('Workflow Compatible');
    await expect(results).toContainText('Inactive Branch Findings Suppressed (3)');
    await expect(results).toContainText('MissingCustomNode');
    await expect(results).toContainText('missing-model.safetensors');
  });

  test('harness recovers from one transient openclaw entry fetch failure', async ({ page }) => {
    const entryRetry = await routeTransientOpenClawEntryFailures(page, 1);

    await page.reload();
    const readiness = await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    expect(entryRetry.failedLogicalAttempts()).toBe(1);
    expect(readiness.loadAttemptsByBoot[0]).toBeGreaterThanOrEqual(2);
    expect(readiness.loadAttemptsByBoot[0]).toBeLessThanOrEqual(4);
  });

  test('harness recovers from two transient openclaw entry fetch failures', async ({ page }) => {
    const entryRetry = await routeTransientOpenClawEntryFailures(page, 2);

    await page.reload();
    const readiness = await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    expect(entryRetry.failedLogicalAttempts()).toBe(2);
    expect(readiness.loadAttemptsByBoot[0]).toBeGreaterThanOrEqual(3);
    expect(readiness.loadAttemptsByBoot[0]).toBeLessThanOrEqual(4);
  });

  test('harness recovers from three transient openclaw entry fetch failures', async ({ page }) => {
    const entryRetry = await routeTransientOpenClawEntryFailures(page, 3);

    await page.reload();
    const readiness = await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    expect(entryRetry.failedLogicalAttempts()).toBe(3);
    expect(readiness.loadAttemptsByBoot[0]).toBe(4);
  });

  test('recovers when the first harness boot exhausts transient entry fetch retries', async ({ page }) => {
    const entryRetry = await routeTransientOpenClawEntryFailures(page, 4);

    await page.reload();
    const readiness = await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    expect(entryRetry.failedLogicalAttempts()).toBe(4);
    expect(readiness.loadAttemptsByBoot[0]).toBe(4);
    expect(readiness.harnessReloads).toBe(1);
    expect(await page.evaluate(() => window.__openclawTestLoadAttempts))
      .toBe(readiness.loadAttemptsByBoot[1]);
  });

  test('preserves first-boot retry evidence after a fourth transient fetch failure', async ({ page }) => {
    const entryRetry = await routeTransientOpenClawEntryFailures(page, 3);
    let injectedFourthFailure = false;
    await page.route('**/web/openclaw.js**', async (route) => {
      const url = new URL(route.request().url());
      if (
        url.pathname === '/web/openclaw.js'
        && url.searchParams.get('openclaw_harness_attempt') === '4'
        && !injectedFourthFailure
      ) {
        injectedFourthFailure = true;
        await route.fulfill({
          status: 503,
          contentType: 'text/javascript',
          body: '// Injected additional transient module-fetch failure.',
        });
        return;
      }
      await route.fallback();
    });

    await page.reload();
    const readiness = await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    expect(entryRetry.failedLogicalAttempts()).toBe(3);
    expect(injectedFourthFailure).toBe(true);
    expect(readiness.loadAttemptsByBoot[0]).toBe(4);
    expect(readiness.harnessReloads).toBe(1);
    expect(readiness.loadAttemptsByBoot).toHaveLength(2);
    expect(readiness.loadAttemptsByBoot[1]).toBeGreaterThanOrEqual(1);
    expect(readiness.loadAttemptsByBoot[1]).toBeLessThanOrEqual(4);
    expect(await page.evaluate(() => window.__openclawTestLoadAttempts))
      .toBe(readiness.loadAttemptsByBoot[1]);
    expect(Object.isFrozen(readiness)).toBe(true);
    expect(Object.isFrozen(readiness.loadAttemptsByBoot)).toBe(true);
  });
});
