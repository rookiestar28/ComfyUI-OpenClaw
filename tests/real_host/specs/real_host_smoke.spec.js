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
 * HOTSPOT: selectors here must be owned by the host or by OpenClaw, never by the
 * mocked harness. `#sidebar-tab-comfyui-openclaw`, for example, exists only
 * because our own mock creates it; the real frontend mounts a custom tab into a
 * bare `<div>` with no id. The mount is therefore found by the attribute OpenClaw
 * itself stamps on it. Borrowing a selector from the harness would produce a spec
 * that fails on every real host while passing in review.
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
  parseHostWebRoot,
  resolveSubject,
} from '../helpers/real_host_subjects.js';

const REPO_ROOT = path.resolve(process.cwd());
const POLICY = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'tests', 'real_host_smoke_policy.json'), 'utf-8'),
);
const SUBJECT = resolveSubject(POLICY, process.env.OPENCLAW_REAL_HOST_SUBJECT ?? 'bundled');
const HOST_LOG_PATH = process.env.OPENCLAW_REAL_HOST_LOG ?? '';

const OPENCLAW_TAB_ID = 'comfyui-openclaw';
const PEER_TAB_ID = 'openclaw-smoke-peer';
// OpenClaw stamps this on whatever container the host hands it, so it is the one
// mount marker that exists on a real host.
const OPENCLAW_MOUNT = '[data-openclaw-host-surface]';
// The root element OpenClaw builds inside that mount.
const OPENCLAW_ROOT = '.openclaw-sidebar-container';
const PEER_CONTENT = '#openclaw-smoke-peer-content';

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

/**
 * Open a sidebar tab through the host's own store.
 *
 * `toggleSidebarTab` is a toggle, not a setter: calling it on the already-active
 * tab closes the panel. It is therefore only called when the tab is not already
 * active, and a missing API throws rather than silently doing nothing, because a
 * silent no-op would turn every downstream assertion into a timeout with no
 * explanation.
 */
async function activateSidebarTab(page, tabId) {
  await page.waitForFunction(
    () => Boolean(window.app?.extensionManager?.sidebarTab),
    null,
    { timeout: 90_000 },
  );
  await page.evaluate((id) => {
    const store = window.app.extensionManager.sidebarTab;
    if (typeof store.toggleSidebarTab !== 'function') {
      throw new Error('host sidebar store exposes no toggleSidebarTab');
    }
    const active =
      store.activeSidebarTabId?.value ?? store.activeSidebarTabId ?? null;
    if (active !== id) {
      store.toggleSidebarTab(id);
    }
  }, tabId);
}

async function openOpenClawSidebar(page) {
  await page.goto('/');
  await activateSidebarTab(page, OPENCLAW_TAB_ID);
  await page.waitForSelector(OPENCLAW_MOUNT, { timeout: 60_000 });
  await page.waitForSelector(OPENCLAW_ROOT, { timeout: 60_000 });
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

    const hostLogText = readHostLog();
    const failures = detectSubjectMismatch({
      subject: SUBJECT,
      reportedFrontendVersion,
      hostLogText,
      // Read back from the host's own log, never echoed from the policy that
      // requested the subject.
      resolvedWebRoot: parseHostWebRoot(hostLogText),
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

    const stamp = await page.getAttribute(OPENCLAW_MOUNT, 'data-openclaw-host-surface');
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

    // The settings pane renders its own scroll region; that id is owned by
    // OpenClaw, unlike any class the harness happens to add.
    await page.click('.openclaw-tabs .openclaw-tab:has-text("Settings")');
    await expect(page.locator('#openclaw-settings-scroll')).toBeVisible({ timeout: 30_000 });
  });

  test('the sidebar reaches its floor and keeps every control inside the boundary', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const geometry = await page.evaluate(() => {
      const rect = (element) => {
        if (!element) {
          return null;
        }
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: box.width };
      };
      // The rightmost control has to be measured, not guessed by position: the
      // tab count varies with capabilities, so a fixed nth-child would silently
      // measure a middle tab and miss an overflow at the real right edge.
      const tabs = Array.from(document.querySelectorAll('.openclaw-tabs .openclaw-tab'));
      const rightmost = tabs.reduce((furthest, candidate) => {
        if (furthest === null) {
          return candidate;
        }
        return candidate.getBoundingClientRect().right > furthest.getBoundingClientRect().right
          ? candidate
          : furthest;
      }, null);
      return {
        tabCount: tabs.length,
        panel: rect(document.querySelector('.side-bar-panel')),
        content: rect(document.querySelector('.sidebar-content-container')),
        mount: rect(document.querySelector('[data-openclaw-host-surface]')),
        rightmostControl: rect(rightmost),
      };
    });

    expect(geometry.tabCount).toBeGreaterThan(0);
    const failures = evaluateSidebarGeometry(geometry, POLICY.geometry.sidebar_min_width_px);
    expect(failures, failures.join('\n')).toEqual([]);
  });

  test('a promoted widget round-trips with identifiers the host assigned', async ({ page }) => {
    await openOpenClawSidebar(page);

    const widget = await page.evaluate(async () => {
      const graph = window.app?.graph;
      const node = graph?.nodes?.find(
        (candidate) => Array.isArray(candidate.widgets) && candidate.widgets.length > 0,
      );
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
      // The annotated form is a flat entry: result[0] is the string itself, not
      // a nested array. A nested fixture normalizes to nothing and would make
      // this check vacuously report "no refs" rather than exercise the path.
      const refs = module.extractHistoryOutputRefs({
        outputs: {
          9: {
            result: ['scene.glb [temp]'],
          },
        },
      });
      const first = refs?.[0] ?? null;
      if (first === null) {
        return null;
      }
      const params = new URLSearchParams(first.viewParams ?? {});
      const response = await fetch(`/api/view?${params.toString()}`, { method: 'HEAD' });
      return {
        viewUrl: `/api/view?${params.toString()}`,
        // The host answers for a temp path it does not have with 404; what this
        // check proves is which directory was addressed, so any answer that is
        // not a server error counts as the route having been reached.
        visible: response.status < 500,
        directoryType: first.type,
      };
    });

    const failures = evaluateAnnotatedTempResult(result);
    expect(failures, failures.join('\n')).toEqual([]);
  });

  test('OpenClaw releases the shared mount to another custom tab with no destroy callback', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const before = await page.getAttribute(OPENCLAW_MOUNT, 'data-openclaw-host-surface');
    expect(before).toBe('standalone_frontend');

    await activateSidebarTab(page, PEER_TAB_ID);
    await page.waitForSelector(PEER_CONTENT, { timeout: 30_000 });

    const after = await page.evaluate(
      (selectors) => ({
        peerMounted:
          document.querySelector(selectors.peer)?.dataset.openclawSmokePeer === 'mounted',
        openclawMarkerRemoved: document.querySelector(selectors.mount) === null,
        openclawRootRemoved: document.querySelector(selectors.root) === null,
      }),
      { peer: PEER_CONTENT, mount: OPENCLAW_MOUNT, root: OPENCLAW_ROOT },
    );

    expect(after.peerMounted).toBe(true);
    expect(after.openclawMarkerRemoved).toBe(true);
    expect(after.openclawRootRemoved).toBe(true);
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
