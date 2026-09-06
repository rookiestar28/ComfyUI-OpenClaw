// @ts-check
import { expect, test } from '@playwright/test';
import {
  mockRemoteAdminBaseline,
  waitForAdminConsoleReady,
} from '../utils/helpers.js';

// Hostile fixtures exercise the three escape-relevant contexts: element markup,
// an event-handler attribute, and a quote break-out.
const HOSTILE_IMG = '<img src=x onerror="window.__openclawXss=1">';
const HOSTILE_ATTR = "' onmouseover='window.__openclawXss=1";

const HOSTILE_RUN = {
  run_id: `run-001${HOSTILE_IMG}`,
  status: 'failed',
  schedule_id: `sch-001${HOSTILE_ATTR}`,
  template_id: `render_portrait${HOSTILE_IMG}`,
  started_at: '2026-09-06T00:00:00Z',
};

const HOSTILE_APPROVAL = {
  approval_id: `apr-001${HOSTILE_IMG}`,
  template_id: `render_portrait${HOSTILE_IMG}`,
  source: `chat${HOSTILE_ATTR}`,
  status: 'pending',
};

const HOSTILE_SCHEDULE = {
  schedule_id: `sch-001${HOSTILE_ATTR}`,
  name: `nightly${HOSTILE_IMG}`,
  enabled: true,
  trigger_type: 'cron',
  template_id: `render_portrait${HOSTILE_IMG}`,
};

const HOSTILE_ERROR = `upstream_unavailable ${HOSTILE_IMG}`;

async function openAdminConsole(page, baseURL, options) {
  await mockRemoteAdminBaseline(page, options);
  await page.goto(new URL('/web/admin_console.html', baseURL).toString());
  await waitForAdminConsoleReady(page);
}

/** Assert no live markup and no XSS side effect anywhere on the page. */
async function expectInertPage(page, selector) {
  await expect(page.locator(`${selector} img`)).toHaveCount(0);
  await expect(page.locator(`${selector} script`)).toHaveCount(0);

  const handlerAttributes = await page.locator(selector).evaluate((root) => {
    const found = [];
    for (const element of root.querySelectorAll('*')) {
      for (const name of element.getAttributeNames()) {
        if (name.toLowerCase().startsWith('on')) found.push(name);
      }
    }
    return found;
  });
  expect(handlerAttributes).toEqual([]);
  expect(await page.evaluate(() => window.__openclawXss)).toBeUndefined();
}

test.describe('S104 admin console render safety', () => {
  test('renders hostile run, approval and schedule fields as literal text', async ({ page, baseURL }) => {
    await openAdminConsole(page, baseURL, {
      approvals: [HOSTILE_APPROVAL],
      runs: [HOSTILE_RUN],
      schedules: [HOSTILE_SCHEDULE],
    });

    await page.locator('#refreshRuns').click();
    await page.locator('#refreshApprovals').click();
    await page.locator('#refreshSchedules').click();

    await expect(page.locator('#runsList')).toContainText(HOSTILE_RUN.run_id, { timeout: 15000 });
    await expect(page.locator('#approvalsList')).toContainText(HOSTILE_APPROVAL.approval_id, { timeout: 15000 });
    await expect(page.locator('#schedulesList')).toContainText(HOSTILE_SCHEDULE.name, { timeout: 15000 });

    await expectInertPage(page, '#runsList');
    await expectInertPage(page, '#approvalsList');
    await expectInertPage(page, '#schedulesList');
  });

  test('renders a hostile backend error string as literal text', async ({ page, baseURL }) => {
    await openAdminConsole(page, baseURL, {
      approvalsStatus: 500,
      schedulesStatus: 500,
      listError: HOSTILE_ERROR,
    });

    await page.locator('#refreshApprovals').click();
    await page.locator('#refreshSchedules').click();

    await expect(page.locator('#approvalsList')).toContainText(HOSTILE_ERROR, { timeout: 15000 });
    await expect(page.locator('#schedulesList')).toContainText(HOSTILE_ERROR, { timeout: 15000 });
    await expect(page.locator('#approvalsList .err')).toHaveCount(1);

    await expectInertPage(page, '#approvalsList');
    await expectInertPage(page, '#schedulesList');
  });

  test('keeps the approve action submitting the exact hostile approval id', async ({ page, baseURL }) => {
    await openAdminConsole(page, baseURL, { approvals: [HOSTILE_APPROVAL] });

    /** @type {string[]} */
    const submitted = [];
    await page.route('**/approvals/**/approve', async (route) => {
      submitted.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.locator('#refreshApprovals').click();
    await expect(page.locator('#approvalsList button', { hasText: 'Approve' })).toHaveCount(1, { timeout: 15000 });
    await page.locator('#approvalsList button', { hasText: 'Approve' }).click();

    // Transaction-level assertion: the click must reach the backend with the
    // exact encoded identifier, not a truncated or markup-mangled one.
    await expect.poll(() => submitted.length, { timeout: 15000 }).toBe(1);
    expect(submitted[0]).toContain(encodeURIComponent(HOSTILE_APPROVAL.approval_id));
    await expectInertPage(page, '#approvalsList');
  });

  test('keeps empty-state text when the backend returns no records', async ({ page, baseURL }) => {
    await openAdminConsole(page, baseURL, {});

    await page.locator('#refreshRuns').click();
    await page.locator('#refreshApprovals').click();
    await page.locator('#refreshSchedules').click();

    await expect(page.locator('#runsList')).toContainText('No run records.', { timeout: 15000 });
    await expect(page.locator('#approvalsList')).toContainText('No pending approvals.', { timeout: 15000 });
    await expect(page.locator('#schedulesList')).toContainText('No schedules configured.', { timeout: 15000 });
  });
});
