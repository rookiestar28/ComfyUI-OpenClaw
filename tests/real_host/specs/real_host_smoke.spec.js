/**
 * Pinned real-host frontend compatibility smoke.
 *
 * Every assertion here is about a surface a real ComfyUI host owns and the
 * mocked harness can only imitate: which frontend the host actually served, that
 * OpenClaw registered into the host's sidebar, that its floor survives the host's
 * own layout, that a promoted widget carries identifiers the host assigned rather
 * than ones the test invented, that an annotated temporary result is fetched from
 * the temporary directory, and that OpenClaw releases the shared mount when the
 * host hands it to another custom tab without calling a destroy callback.
 *
 * The lane runs only through `scripts/real_host_smoke.py`, which starts the host,
 * waits for its health route, and tears it down. This file assumes a host is
 * already up at the configured loopback origin.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  buildHostArgs,
  detectSubjectMismatch,
  evaluateAnnotatedTempResult,
  evaluatePromotedWidget,
  evaluateSidebarGeometry,
  resolveSubject,
} from '../helpers/real_host_subjects.js';

const REPO_ROOT = path.resolve(process.cwd());
const POLICY = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'tests', 'real_host_smoke_policy.json'), 'utf-8'),
);
const SUBJECT = resolveSubject(POLICY, process.env.OPENCLAW_REAL_HOST_SUBJECT ?? 'bundled');
const HOST_LOG_PATH = process.env.OPENCLAW_REAL_HOST_LOG ?? '';
const HOST_WEB_ROOT = process.env.OPENCLAW_REAL_HOST_WEB_ROOT || null;
const OPENCLAW_MOUNT_ID = '#sidebar-tab-comfyui-openclaw';
const PEER_TAB_ID = 'openclaw-smoke-peer';

function readHostLog() {
  if (!HOST_LOG_PATH) {
    return '';
  }
  try {
    return readFileSync(HOST_LOG_PATH, 'utf-8');
  } catch {
    return '';
  }
}

async function openOpenClawSidebar(page) {
  await page.goto('/');
  await page.waitForFunction(() => Boolean(window.app?.extensionManager), null, { timeout: 90_000 });
  await page.evaluate((tabId) => {
    window.app.extensionManager.setSidebarTab?.(tabId) ??
      window.app.extensionManager.sidebarTab?.toggleSidebarTab?.(tabId);
  }, 'comfyui-openclaw');
  await page.waitForSelector(OPENCLAW_MOUNT_ID, { timeout: 60_000 });
}

test.describe(`real host frontend smoke (${SUBJECT.id})`, () => {
  const consoleErrors = [];
  const pageErrors = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors.length = 0;
    pageErrors.length = 0;
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => pageErrors.push(String(error)));
  });

  test('the host served the requested frontend subject and not a fallback', async ({ page }) => {
    await page.goto('/');
    const reportedFrontendVersion = await page.evaluate(async () => {
      const direct = window.__COMFYUI_FRONTEND_VERSION__;
      if (typeof direct === 'string' && direct !== '') {
        return direct;
      }
      const response = await fetch('/api/system_stats');
      if (!response.ok) {
        return null;
      }
      const stats = await response.json();
      return stats?.system?.comfyui_frontend_version ?? null;
    });

    const failures = detectSubjectMismatch({
      subject: SUBJECT,
      reportedFrontendVersion,
      hostLogText: readHostLog(),
      resolvedWebRoot: HOST_WEB_ROOT,
    });

    expect(failures, failures.join('\n')).toEqual([]);
  });

  test('evidence never names the reviewed frontend source head as executed', async () => {
    const notExecuted = POLICY.not_executed.frontend_source_head;

    // The host can reproduce a published release, not an arbitrary later commit.
    // Nothing this lane observes may be labelled with that commit.
    expect(SUBJECT.frontend_version).not.toBe(notExecuted);
    expect(readHostLog()).not.toContain(notExecuted);
    expect(JSON.stringify(SUBJECT)).not.toContain(notExecuted);
  });

  test('the host loads the plugin and stamps its sidebar surface', async ({ page }) => {
    await openOpenClawSidebar(page);

    const stamp = await page.getAttribute(OPENCLAW_MOUNT_ID, 'data-openclaw-host-surface');
    expect(stamp).toBe('standalone_frontend');

    const capabilities = await page.evaluate(async () => {
      const response = await fetch('/openclaw/capabilities');
      return { ok: response.ok, body: response.ok ? await response.json() : null };
    });
    expect(capabilities.ok).toBe(true);
    expect(capabilities.body).toBeTruthy();
  });

  test('the settings and jobs surfaces answer through the real host', async ({ page }) => {
    await openOpenClawSidebar(page);

    const jobs = await page.evaluate(async () => {
      const response = await fetch('/openclaw/jobs');
      return { ok: response.ok, body: response.ok ? await response.json() : null };
    });
    expect(jobs.ok).toBe(true);
    expect(Array.isArray(jobs.body?.jobs)).toBe(true);

    await page.click('.openclaw-tabs .openclaw-tab:has-text("Settings")');
    await expect(page.locator('.openclaw-settings')).toBeVisible({ timeout: 30_000 });
  });

  test('the sidebar reaches its floor and keeps every control inside the boundary', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const geometry = await page.evaluate(() => {
      const rect = (selector) => {
        const element = document.querySelector(selector);
        if (!element) {
          return null;
        }
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: box.width };
      };
      return {
        panel: rect('.side-bar-panel'),
        content: rect('.sidebar-content-container'),
        mount: rect('#sidebar-tab-comfyui-openclaw'),
        rightmostControl: rect('.openclaw-tabs .openclaw-tab:nth-child(4)'),
      };
    });

    const failures = evaluateSidebarGeometry(geometry, POLICY.geometry.sidebar_min_width_px);
    expect(failures, failures.join('\n')).toEqual([]);
  });

  test('a promoted widget round-trips with identifiers the host assigned', async ({ page }) => {
    await openOpenClawSidebar(page);

    const widget = await page.evaluate(async () => {
      const graph = window.app?.graph;
      const node = graph?.nodes?.find((candidate) => Array.isArray(candidate.widgets) && candidate.widgets.length > 0);
      if (!node) {
        return null;
      }
      const target = node.widgets[0];
      const original = target.value;
      const next = typeof original === 'number' ? original + 1 : `${original ?? ''}x`;
      target.value = next;
      return {
        sourceNodeId: String(node.id),
        sourceWidgetName: String(target.name),
        value: target.value,
        wroteBack: target.value === next,
      };
    });

    const failures = evaluatePromotedWidget(widget);
    expect(failures, failures.join('\n')).toEqual([]);
    expect(widget.wroteBack).toBe(true);
  });

  test('an annotated temporary result stays visible and is fetched from the temp directory', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const result = await page.evaluate(async () => {
      const module = await import('/extensions/comfyui-openclaw/openclaw_asset_refs.js');
      const refs = module.collectAssetRefs({
        outputs: {
          9: {
            result: [['scene.glb [temp]']],
          },
        },
      });
      const first = refs?.[0] ?? null;
      return first === null ? null : { viewUrl: first.viewUrl ?? first.url ?? '', visible: true };
    });

    const failures = evaluateAnnotatedTempResult(result);
    expect(failures, failures.join('\n')).toEqual([]);
  });

  test('OpenClaw releases the shared mount to another custom tab with no destroy callback', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const before = await page.getAttribute(OPENCLAW_MOUNT_ID, 'data-openclaw-host-surface');
    expect(before).toBe('standalone_frontend');

    await page.evaluate((tabId) => {
      window.app.extensionManager.setSidebarTab?.(tabId) ??
        window.app.extensionManager.sidebarTab?.toggleSidebarTab?.(tabId);
    }, PEER_TAB_ID);
    await page.waitForSelector('#openclaw-smoke-peer-content', { timeout: 30_000 });

    const after = await page.evaluate(() => {
      const mount = document.querySelector('.sidebar-content-container') ?? document.body;
      const peer = document.querySelector('#openclaw-smoke-peer-content');
      return {
        peerMounted: peer?.dataset.openclawSmokePeer === 'mounted',
        openclawMarkerRemoved: mount.querySelector('[data-openclaw-host-surface]') === null,
      };
    });

    expect(after.peerMounted).toBe(true);
    expect(after.openclawMarkerRemoved).toBe(true);
  });

  test.afterEach(async () => {
    expect(pageErrors, pageErrors.join('\n')).toEqual([]);
    expect(consoleErrors, consoleErrors.join('\n')).toEqual([]);
  });
});

test('the lane never widens its own exposure', () => {
  // A guard on the argv this run was built from, so a future edit that adds an
  // exposing argument fails here rather than on a live host.
  const args = buildHostArgs(POLICY, SUBJECT, { port: 18188 });

  expect(args).toContain('--cpu');
  expect(args.slice(args.indexOf('--listen'), args.indexOf('--listen') + 2)).toEqual([
    '--listen',
    '127.0.0.1',
  ]);
  for (const forbidden of POLICY.runtime.forbidden_args) {
    expect(args).not.toContain(forbidden);
  }
});
