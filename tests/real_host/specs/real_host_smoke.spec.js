/**
 * Pinned real-host frontend compatibility smoke.
 *
 * Every assertion here is about a surface a real ComfyUI host owns and the
 * mocked harness can only imitate: which frontend the host actually served, that
 * OpenClaw registered into the host's sidebar, that its floor survives the host's
 * own layout, that a width the host's splitter writes while OpenClaw is mounted
 * survives a tab switch, that a promoted widget connects a host input to an inner node and
 * survives an OpenClaw edit and prompt serialization, that an annotated temporary
 * result is fetched from the temporary directory, and that OpenClaw releases the shared mount when the
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

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  buildHostArgs,
  classifyFailedRequests,
  detectSubjectMismatch,
  evaluateAnnotatedTempResult,
  evaluateHostWidthOwnership,
  evaluatePromotedWidget,
  evaluateSidebarGeometry,
  parseHostBindOrigin,
  parseHostWebRoot,
  partitionBrowserErrors,
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
// How long the served frontend gets to announce its own version before the
// subject is treated as unidentifiable.
const FRONTEND_VERSION_BUDGET_MS = 30_000;
// How far the sizing case drags the host gutter. Wide enough to be unmistakable,
// small enough to stay clear of the canvas minimum on the default 1280px viewport.
const SIDEBAR_DRAG_DELTA_PX = 120;

/**
 * Find the path the host actually serves OpenClaw's modules under.
 *
 * `reference/ComfyUI/nodes.py` registers a web directory under the pyproject
 * `project_name` only when `tool.comfy.web` is set; OpenClaw sets `WEB_DIRECTORY`
 * instead, so it registers under the *module* name, which is the directory a user
 * installed it into. Hardcoding the lane's own `CUSTOM_NODE_DIR_NAME` therefore
 * only works on hosts the lane installed itself, and 404s on a real installation
 * under the repository's own name. The path is read back from the host instead.
 */
async function readExtensionBases(page) {
  return page.evaluate(async () => {
    const directoryOf = (entries, suffix) => {
      const marker = entries.find(
        (entry) => typeof entry === 'string' && entry.endsWith(suffix),
      );
      return marker ? marker.slice(0, marker.lastIndexOf('/')) : null;
    };
    const response = await fetch('/extensions');
    if (!response.ok) {
      return { openclaw: null, peer: null };
    }
    const entries = await response.json();
    return {
      openclaw: directoryOf(entries, '/openclaw_asset_refs.js'),
      peer: directoryOf(entries, '/peer_sidebar_tab.js'),
    };
  });
}

async function resolveOpenClawExtensionBase(page) {
  const { openclaw } = await readExtensionBases(page);
  if (!openclaw) {
    throw new Error('the host serves no OpenClaw modules under /extensions');
  }
  return openclaw;
}

// Resolved once from the host and reused, so a failed page later in the run does
// not silently change how errors are attributed.
let cachedExtensionBases = null;

async function extensionBases(page) {
  if (cachedExtensionBases?.openclaw) {
    return cachedExtensionBases;
  }
  try {
    cachedExtensionBases = await readExtensionBases(page);
  } catch {
    return null;
  }
  return cachedExtensionBases?.openclaw ? cachedExtensionBases : null;
}

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

/** Inline sizing and measured width of the host's sidebar panel. */
async function readSidebarPanel(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('.side-bar-panel');
    if (!panel) {
      return null;
    }
    return {
      width: panel.getBoundingClientRect().width,
      inlineMinWidth: panel.style.getPropertyValue('min-width'),
      inlineFlexBasis: panel.style.getPropertyValue('flex-basis'),
    };
  });
}

/**
 * Let deferred layout work land before measuring: OpenClaw re-applies its floor in
 * the next animation frame, and the host re-pins widths after its next tick.
 */
async function settleFrames(page) {
  await page.evaluate(
    () =>
      new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 50)));
      }),
  );
}

async function openOpenClawSidebar(page) {
  await page.goto('/');
  await activateSidebarTab(page, OPENCLAW_TAB_ID);
  await page.waitForSelector(OPENCLAW_MOUNT, { timeout: 60_000 });
  await page.waitForSelector(OPENCLAW_ROOT, { timeout: 60_000 });
}

async function loadIdentityFixture(page, mode) {
  const base = await resolveOpenClawExtensionBase(page);
  return page.evaluate(async ({ base, mode }) => {
    const app = window.app;
    const previous = app.graph.serialize();
    window.__openclawIdentity = { previous };
    const leafId = mode === 'root' ? 'insert:operation-1:root:node:7' : 'child:opaque';
    const workflow = {
      last_node_id: 2, last_link_id: 0,
      nodes: [
        { id: leafId, type: 'CheckpointLoaderSimple', pos: [100, 100], size: [300, 120],
          flags: {}, order: 0, mode: 0, inputs: [],
          outputs: ['MODEL', 'CLIP', 'VAE'].map((type, slot_index) => ({ name: type, type, links: null, slot_index })),
          properties: {}, widgets_values: ['base.ckpt'] },
        { id: 'control', type: 'EmptyLatentImage', pos: [500, 100], size: [300, 120],
          flags: {}, order: 1, mode: 0, inputs: [],
          outputs: [{ name: 'LATENT', type: 'LATENT', links: null, slot_index: 0 }],
          properties: {}, widgets_values: [512, 512, 1] },
      ], links: [], groups: [], config: {}, extra: {}, version: 0.4,
    };
    if (await app.loadGraphData(workflow, true, true, null, { skipAssetScans: true }) === false) {
      throw new Error('host refused the synthetic identity workflow');
    }
    const graph = app.graph;
    const leaf = graph._nodes.find((node) => node.type === 'CheckpointLoaderSimple');
    const widget = leaf?.widgets?.find((item) => item.name === 'ckpt_name');
    const control = graph._nodes.find((node) => String(node.id) === 'control').widgets.find((item) => item.name === 'width');
    if (!widget || String(leaf.id) !== leafId) throw new Error('host did not preserve the colon-bearing node ID');
    // Only the fixture choices are synthetic; the graph, nodes and widgets stay host-owned.
    widget.options.values = ['base.ckpt', 'xl.ckpt'];
    widget.value = 'base.ckpt';
    let executionId = leafId;
    let firstId = null;
    let collision = null;
    if (mode !== 'root') {
      const { subgraph, node: first } = graph.convertToSubgraph(new Set([leaf]));
      if (subgraph._nodes.find((node) => node === leaf) !== leaf) throw new Error('host conversion replaced the fixture leaf');
      const second = first.clone();
      if (!second || second.subgraph !== subgraph) throw new Error('host clone did not reuse the real definition');
      second.id = 'later:instance';
      graph.add(second);
      firstId = `${first.id}:${leaf.id}`;
      executionId = `${second.id}:${leaf.id}`;
      if (mode === 'collision') {
        collision = leaf.clone();
        if (!collision) throw new Error('host could not clone the collision control');
        collision.id = executionId;
        graph.add(collision);
      }
    }
    const { tabManager } = await import(`${base}/openclaw_tabs.js`);
    const tab = tabManager.tabs.find((item) => item.id === 'parameter-lab');
    const graphHost = await import(`${base}/openclaw_graph_host.js`);
    Object.assign(window.__openclawIdentity, { leaf, widget, control, collision, executionId, firstId, tab, graphHost });
    tab.dimensions = [];
    tab.renderDimensions();
    return { executionId, firstId,
      catalogCount: graphHost.getGraphNodeCatalog(graph).filter((entry) => entry.node === leaf).length,
      objectAmbiguous: graphHost.getGraphNodeEntryByObject(graph, leaf) === null,
      rawAmbiguous: graphHost.getGraphNodeEntry(graph, leaf.id) === null };
  }, { base, mode });
}

async function restoreIdentityFixture(page) {
  return page.evaluate(async () => {
    const previous = window.__openclawIdentity?.previous;
    if (!previous) throw new Error('identity fixture has no restoration snapshot');
    const loaded = await window.app.loadGraphData(previous, true, true, null, { skipAssetScans: true });
    const comparable = (graph) => ({ nodes: graph.nodes, links: graph.links, groups: graph.groups });
    const restored = loaded !== false && JSON.stringify(comparable(window.app.graph.serialize())) === JSON.stringify(comparable(previous));
    delete window.__openclawIdentity;
    return restored;
  });
}

test.describe(`real host frontend smoke (${SUBJECT.id})`, () => {
  const consoleErrors = [];
  const pageErrors = [];
  // The browser's console text for a failed subresource carries no URL, so the
  // only object that knows which file failed is the network event. Collecting
  // these is what lets the run say "this 404 was the host's own optional
  // stylesheet" instead of charging an unnamed 404 to this product.
  const failedRequests = [];
  // A check that deliberately provokes a failing request declares it here, so
  // the run does not report the check's own probe as an unexplained failure.
  const declaredProbes = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors.length = 0;
    pageErrors.length = 0;
    failedRequests.length = 0;
    declaredProbes.length = 0;
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => pageErrors.push(String(error)));
    page.on('response', (response) => {
      if (response.status() >= 400) {
        failedRequests.push({
          url: response.url(),
          label: `${response.status()} ${response.request().method()} ${response.url()}`,
        });
      }
    });
    page.on('requestfailed', (request) => {
      const errorText = request.failure()?.errorText ?? '';
      failedRequests.push({
        url: request.url(),
        // The structured failure kind is carried alongside the human label, and
        // the classifier reads only this. Deciding ownership from the formatted
        // string would be the same mistake as deciding it from console prose.
        errorText,
        label: `FAILED ${request.method()} ${request.url()} :: ${errorText}`,
      });
    });
  });

  test('the host served the requested frontend subject and not a fallback', async ({ page, request }, testInfo) => {
    await page.goto('/');
    const reportedFrontendVersion = await page.evaluate(async (budgetMs) => {
      // The frontend assigns this global when its Vue application mounts, which
      // happens after navigation resolves - reading it immediately loses a race
      // on a real host and reports "(none)". Wait for it instead of guessing.
      const deadline = Date.now() + budgetMs;
      for (;;) {
        const direct = window.__COMFYUI_FRONTEND_VERSION__;
        if (typeof direct === 'string' && direct !== '') {
          return direct;
        }
        if (Date.now() >= deadline) {
          return null;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }, FRONTEND_VERSION_BUDGET_MS);

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
    const webRoot = parseHostWebRoot(hostLogText);
    expect(webRoot).toBeTruthy();
    const index = await request.get('/');
    expect(index.ok()).toBe(true);
    const bytes = await index.body();
    const digest = (value) => createHash('sha256').update(value).digest('hex');
    expect(digest(bytes)).toBe(digest(readFileSync(path.join(webRoot, 'index.html'))));
    const script = bytes.toString('utf8').match(/<script[^>]+src="([^"]+\.js)"/);
    expect(script, 'served index has no JavaScript entry').not.toBeNull();
    const url = new URL(script[1], 'http://127.0.0.1');
    expect(url.hostname).toBe('127.0.0.1');
    const asset = await request.get(url.pathname);
    expect(asset.ok()).toBe(true);
    const assetBytes = await asset.body();
    expect(digest(assetBytes)).toBe(digest(readFileSync(path.join(webRoot, url.pathname.replace(/^\//, '')))));
    await testInfo.attach('served-identity', { body: JSON.stringify({ frontend: reportedFrontendVersion,
      indexSha256: digest(bytes), entryPath: url.pathname, entrySha256: digest(assetBytes) }), contentType: 'application/json' });
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

  test('a sidebar width the host writes while OpenClaw is mounted survives a tab switch', async ({
    page,
  }) => {
    await page.goto('/');
    // Start on a tab OpenClaw does not own, so the host's own inline sizing is read
    // before OpenClaw captures anything.
    await activateSidebarTab(page, PEER_TAB_ID);
    await page.waitForSelector(PEER_CONTENT, { timeout: 30_000 });

    const layout = await page.evaluate(() => {
      const read = (id) =>
        window.app?.extensionManager?.setting?.get?.(id) ??
        window.app?.ui?.settings?.getSettingValue?.(id);
      return {
        unifiedWidth: read('Comfy.Sidebar.UnifiedWidth'),
        location: read('Comfy.Sidebar.Location'),
      };
    });
    // With per-tab widths the host itself resizes the panel on a tab switch, which
    // this case would misread as OpenClaw's doing. The host default is unified.
    expect(layout.unifiedWidth, 'this case needs Comfy.Sidebar.UnifiedWidth=true').toBe(true);

    const hostOnly = await readSidebarPanel(page);

    await activateSidebarTab(page, OPENCLAW_TAB_ID);
    await page.waitForSelector(OPENCLAW_MOUNT, { timeout: 60_000 });
    await page.waitForSelector(OPENCLAW_ROOT, { timeout: 60_000 });
    await settleFrames(page);
    const mounted = await readSidebarPanel(page);

    // Drag the host's own gutter, so the width is written by the host's splitter
    // code rather than by this test assigning a style.
    const gutter = await page.evaluate((location) => {
      const panel = document.querySelector('.side-bar-panel');
      const sibling =
        location === 'right' ? panel?.previousElementSibling : panel?.nextElementSibling;
      if (!sibling?.classList.contains('p-splitter-gutter')) {
        return null;
      }
      const box = sibling.getBoundingClientRect();
      return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }, layout.location);
    expect(gutter, 'the host renders no splitter gutter beside the sidebar').not.toBeNull();

    const direction = layout.location === 'right' ? -1 : 1;
    await page.mouse.move(gutter.x, gutter.y);
    await page.mouse.down();
    await page.mouse.move(gutter.x + direction * SIDEBAR_DRAG_DELTA_PX, gutter.y, { steps: 12 });
    await page.mouse.up();
    await settleFrames(page);
    const dragged = await readSidebarPanel(page);

    await activateSidebarTab(page, PEER_TAB_ID);
    await page.waitForSelector(PEER_CONTENT, { timeout: 30_000 });
    await page.waitForSelector(OPENCLAW_ROOT, { state: 'detached', timeout: 30_000 });
    await settleFrames(page);
    const released = {
      ...(await readSidebarPanel(page)),
      ...(await page.evaluate(
        (selectors) => ({
          peerMounted:
            document.querySelector(selectors.peer)?.dataset.openclawSmokePeer === 'mounted',
          openclawRootRemoved: document.querySelector(selectors.root) === null,
        }),
        { peer: PEER_CONTENT, root: OPENCLAW_ROOT },
      )),
    };

    const evidence = {
      dragDeltaPx: SIDEBAR_DRAG_DELTA_PX,
      hostOnly,
      mounted,
      dragged,
      released,
    };
    const failures = evaluateHostWidthOwnership(evidence, POLICY.geometry.sidebar_min_width_px);
    expect(failures, `${failures.join('\n')}\n${JSON.stringify(evidence)}`).toEqual([]);
  });

  test('a host-promoted widget reaches the inner prompt through OpenClaw', async ({ page }) => {
    await openOpenClawSidebar(page);
    const extensionBase = await resolveOpenClawExtensionBase(page);
    // Keep the fixture synthetic and local to the disposable browser workflow.
    const workflow = {
      last_node_id: 1,
      last_link_id: 0,
      nodes: [{
        id: 1, type: 'EmptyLatentImage', pos: [100, 100], size: [300, 120],
        flags: {}, order: 0, mode: 0, inputs: [],
        outputs: [{ name: 'LATENT', type: 'LATENT', links: null, slot_index: 0 }],
        properties: {}, widgets_values: [512, 512, 1],
      }],
      links: [], groups: [], config: {}, extra: {}, version: 0.4,
    };

    const widget = await page.evaluate(async ({ base, fixture }) => {
      const app = window.app;
      if (!app?.graph || typeof app.loadGraphData !== 'function' ||
          typeof app.graphToPrompt !== 'function') {
        throw new Error('host workflow APIs are unavailable');
      }
      const previous = app.graph.serialize();
      let evidence;
      let restored = false;
      try {
        const loaded = await app.loadGraphData(fixture, true, true, null, { skipAssetScans: true });
        if (loaded === false) throw new Error('host refused the synthetic workflow');
        const graph = app.graph;
        const source = graph._nodes?.find((node) => node.type === 'EmptyLatentImage');
        const innerWidget = source?.widgets?.find((entry) => entry.name === 'width');
        if (!source || !innerWidget) throw new Error('synthetic host node or width widget is missing');
        // The host owns the input-slot and projection lifecycle. No source fields are assigned.
        let innerSlot = source.getSlotFromWidget(innerWidget);
        if (!innerSlot) {
          innerSlot = source.addInput('width', 'INT');
          innerSlot.widget = { name: innerWidget.name };
        }
        const { subgraph, node: host } = graph.convertToSubgraph(new Set([source]));
        const inner = subgraph._nodes?.find((node) => String(node.id) === String(source.id));
        const boundSlot = inner?.getSlotFromWidget(innerWidget);
        if (!inner || !boundSlot) throw new Error('inner widget slot was not preserved');
        const input = subgraph.addInput('width', 'INT');
        const link = input.connect(boundSlot, inner);
        const hostInput = host.inputs.find((entry) => entry.name === 'width' && entry.widgetId);
        const projection = host.widgets?.find((entry) => entry.widgetId === hostInput?.widgetId);
        if (!link || !hostInput || !projection) {
          throw new Error('host did not create a connected promoted widget projection');
        }
        const { ParameterLabTab } = await import(`${base}/tabs/parameter_lab_tab.js`);
        const editedValue = 513;
        const productWidget = ParameterLabTab.applyOverrides({ [`${host.id}.width`]: editedValue });
        const prompt = await app.graphToPrompt();
        const observedLink = subgraph.getLink(link.id);
        evidence = {
          hostNodeId: String(host.id),
          innerNodeId: String(inner.id),
          connectedNodeId: observedLink ? String(observedLink.target_id) : null,
          widgetName: innerWidget.name,
          connectedInputName: inner.inputs[observedLink?.target_slot]?.widget?.name ?? null,
          hostInputWidgetId: String(hostInput.widgetId),
          projectedWidgetId: String(projection.widgetId),
          productEditWidgetId: productWidget?.widgetId ? String(productWidget.widgetId) : null,
          bindingConnected: observedLink === link && boundSlot.link === link.id,
          editedValue,
          promotedValue: projection.value,
          promptValue: prompt.output?.[`${host.id}:${inner.id}`]?.inputs?.width,
        };
      } finally {
        // A test failure may occur after graph conversion; always restore the prior workflow.
        const reloaded = await app.loadGraphData(previous, true, true, null, { skipAssetScans: true });
        const current = app.graph.serialize();
        const comparable = (graph) => ({ nodes: graph.nodes, links: graph.links, groups: graph.groups });
        restored = reloaded !== false &&
          JSON.stringify(comparable(current)) === JSON.stringify(comparable(previous));
      }
      return { ...evidence, restored };
    }, { base: extensionBase, fixture: workflow });

    const failures = evaluatePromotedWidget(widget);
    expect(failures, failures.join('\n')).toEqual([]);
    expect(widget.restored).toBe(true);
  });

  for (const placement of ['root', 'shared-nested']) {
    test(`colon-bearing ${placement} IDs select, compare and replay on the real graph`, async ({ page }) => {
      await openOpenClawSidebar(page);
      await page.click('.openclaw-tabs .openclaw-tab:has-text("Parameter Lab")');
      page.on('dialog', (dialog) => dialog.accept());
      try {
        const fixture = await loadIdentityFixture(page, placement);
        expect(fixture.catalogCount).toBe(placement === 'root' ? 1 : 2);
        if (placement !== 'root') {
          expect(fixture.objectAmbiguous).toBe(true);
          expect(fixture.rawAmbiguous).toBe(true);
          await expect.poll(() => page.evaluate(() => {
            const { graphHost, firstId } = window.__openclawIdentity;
            return graphHost.getGraphNodeEntry(window.app.graph, firstId)?.node === window.__openclawIdentity.leaf;
          })).toBe(true);
        }
        await page.click('#lab-add-dim');
        await expect(page.locator(`.dim-node-select option[value="${fixture.executionId}"]`)).toHaveCount(1);
        await page.selectOption('.dim-node-select', fixture.executionId);
        await page.selectOption('.dim-widget-select', 'ckpt_name');
        await page.selectOption('.dim-candidate-select', 'xl.ckpt');
        await page.evaluate(() => {
          const { tab, executionId } = window.__openclawIdentity;
          tab.showCompareWizard(executionId);
        });
        await expect(page.locator('.dim-node-select')).toHaveValue(fixture.executionId);
        await expect(page.locator('.dim-widget-select')).toHaveValue('ckpt_name');
        if (placement !== 'root') {
          await page.evaluate(() => {
            const { tab, leaf } = window.__openclawIdentity;
            tab.showCompareWizard(leaf);
          });
          await expect(page.locator('.openclaw-banner')).toContainText('Select a unique loader');
          await expect(page.locator('.dim-node-select')).toHaveValue(fixture.executionId);
        }
        await page.evaluate(() => {
          const { tab, executionId } = window.__openclawIdentity;
          tab.experimentId = 'host-identity';
          tab.plan = { dimensions: [], runs: [{ [`${executionId}.ckpt_name`]: 'xl.ckpt' }] };
          tab.renderPlan();
        });
        await page.click('.replay-run');
        await expect(page.locator('.openclaw-banner')).toContainText('Values applied');
        const edited = await page.evaluate(async () => {
          const { widget, control, executionId, firstId } = window.__openclawIdentity;
          const prompt = await window.app.graphToPrompt();
          return { value: widget.value, control: control.value,
            promptValue: prompt.output?.[executionId]?.inputs?.ckpt_name,
            firstPromptValue: firstId ? prompt.output?.[firstId]?.inputs?.ckpt_name : null };
        });
        expect(edited).toMatchObject({ value: 'xl.ckpt', control: 512, promptValue: 'xl.ckpt' });
        if (placement !== 'root') expect(edited.firstPromptValue).toBe('xl.ckpt');
      } finally {
        expect(await restoreIdentityFixture(page), 'host workflow restoration failed').toBe(true);
      }
    });
  }

  test('a collision with a later shared instance rejects replay before every write', async ({ page }) => {
    await openOpenClawSidebar(page);
    await page.click('.openclaw-tabs .openclaw-tab:has-text("Parameter Lab")');
    try {
      await loadIdentityFixture(page, 'collision');
      const before = await page.evaluate(() => {
        const { tab, executionId, widget, control, collision, graphHost } = window.__openclawIdentity;
        const values = [widget.value, control.value, collision.widgets.find((entry) => entry.name === 'ckpt_name').value];
        tab.experimentId = 'host-collision';
        tab.plan = { dimensions: [], runs: [{ 'control.width': 513, [`${executionId}.ckpt_name`]: 'xl.ckpt' }] };
        tab.renderPlan();
        return { values, ambiguous: graphHost.getGraphNodeEntry(window.app.graph, executionId) === null };
      });
      expect(before.ambiguous).toBe(true);
      await page.click('.replay-run');
      await expect(page.locator('.openclaw-banner')).toContainText('Cannot apply values');
      await expect(page.locator('.openclaw-banner')).not.toContainText('Values applied');
      expect(await page.evaluate(() => {
        const { widget, control, collision } = window.__openclawIdentity;
        return [widget.value, control.value, collision.widgets.find((entry) => entry.name === 'ckpt_name').value];
      })).toEqual(before.values);
    } finally {
      expect(await restoreIdentityFixture(page), 'host workflow restoration failed').toBe(true);
    }
  });

  test('left and right sidebars contain controls at both desktop viewports', async ({ page }, testInfo) => {
    await openOpenClawSidebar(page);
    const originalViewport = page.viewportSize();
    const originalLocation = await page.evaluate(() => window.app.ui.settings.getSettingValue('Comfy.Sidebar.Location'));
    const measurements = [];
    try {
      for (const viewport of [{ width: 1440, height: 1000 }, { width: 1920, height: 1080 }]) {
        await page.setViewportSize(viewport);
        for (const location of ['left', 'right']) {
          await page.evaluate(async (value) => {
            const settings = window.app.ui.settings;
            if (typeof settings.setSettingValueAsync !== 'function') throw new Error('host has no async setting setter');
            await settings.setSettingValueAsync('Comfy.Sidebar.Location', value);
          }, location);
          await activateSidebarTab(page, PEER_TAB_ID);
          await page.waitForSelector(PEER_CONTENT);
          await settleFrames(page);
          await page.screenshot({ path: testInfo.outputPath(`${location}-${viewport.width}-peer.png`) });
          await activateSidebarTab(page, OPENCLAW_TAB_ID);
          await page.waitForSelector(OPENCLAW_ROOT);
          await settleFrames(page);
          const geometry = await page.evaluate(() => {
            const rect = (selector) => {
              const box = document.querySelector(selector)?.getBoundingClientRect();
              return box ? { left: box.left, right: box.right, width: box.width } : null;
            };
            const controls = [...document.querySelectorAll('.openclaw-tabs .openclaw-tab')].map((node) => node.getBoundingClientRect());
            return { panel: rect('.side-bar-panel'), content: rect('.sidebar-content-container'),
              mount: rect('[data-openclaw-host-surface]'),
              rightmostControl: controls.length ? { right: Math.max(...controls.map((box) => box.right)) } : null };
          });
          expect(evaluateSidebarGeometry(geometry, POLICY.geometry.sidebar_min_width_px)).toEqual([]);
          expect(geometry.panel.left).toBeGreaterThanOrEqual(0);
          expect(geometry.panel.right).toBeLessThanOrEqual(viewport.width);
          expect((geometry.panel.left + geometry.panel.right) / 2 < viewport.width / 2).toBe(location === 'left');
          await page.screenshot({ path: testInfo.outputPath(`${location}-${viewport.width}-openclaw.png`) });
          await activateSidebarTab(page, PEER_TAB_ID);
          await page.waitForSelector(PEER_CONTENT);
          await expect(page.locator(OPENCLAW_ROOT)).toHaveCount(0);
          await activateSidebarTab(page, OPENCLAW_TAB_ID);
          await expect(page.locator(OPENCLAW_ROOT)).toBeVisible();
          measurements.push({ location, viewport, geometry });
        }
      }
      writeFileSync(testInfo.outputPath('sidebar-geometry.json'), JSON.stringify(measurements, null, 2));
    } finally {
      await page.evaluate(async (value) => window.app.ui.settings.setSettingValueAsync('Comfy.Sidebar.Location', value), originalLocation);
      await page.setViewportSize(originalViewport);
    }
  });

  test('an annotated temporary result stays visible and is fetched from the temp directory', async ({
    page,
  }) => {
    await openOpenClawSidebar(page);

    const extensionBase = await resolveOpenClawExtensionBase(page);
    const result = await page.evaluate(async (base) => {
      const module = await import(`${base}/openclaw_asset_refs.js`);
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
    }, extensionBase);

    // This check asks the host about a temp file it does not have, on purpose:
    // a 404 still proves which directory was addressed, which is the whole point
    // of the assertion. Declaring the probe keeps the run from reporting the
    // check's own deliberate request as an unexplained product failure.
    if (result?.viewUrl) {
      declaredProbes.push(result.viewUrl);
    }

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

  test.afterEach(async ({ page }) => {
    // A stock pinned host runs only OpenClaw and the peer fixture, so "no errors
    // at all" is right there. A real installation also runs whatever else the user
    // installed, and those packs emit their own missing assets and exceptions.
    // Failing on those reports a problem that is not this product's, which is
    // exactly where the check would otherwise be most valuable. The assertion is
    // scoped, not dropped: anything attributable to OpenClaw, to the peer fixture,
    // or to no extension at all still fails.
    const bases = await extensionBases(page);
    if (bases === null) {
      // The host could not say which path is ours, so nothing may be excused.
      expect(pageErrors, pageErrors.join('\n')).toEqual([]);
      expect(consoleErrors, consoleErrors.join('\n')).toEqual([]);
      return;
    }

    const options = {
      extensionBase: bases.openclaw,
      peerBase: bases.peer ?? '',
      policy: POLICY,
    };
    const requestOptions = { ...options, expectedUrls: declaredProbes };
    const fromPage = partitionBrowserErrors(pageErrors, options);
    const fromConsole = partitionBrowserErrors(consoleErrors, options);
    const fromRequests = classifyFailedRequests(failedRequests, requestOptions);
    const setAside = [...fromPage.foreign, ...fromConsole.foreign, ...fromRequests.foreign];
    if (setAside.length > 0) {
      // Recorded rather than discarded, so the run still shows what the host was
      // doing around the product.
      console.log(
        `[real-host] ${setAside.length} error(s) from other node packs, not attributed to ` +
          `${bases.openclaw}:\n  ${setAside.join('\n  ')}`,
      );
    }
    const hostOwned = [...fromPage.host, ...fromConsole.host, ...fromRequests.host];
    if (hostOwned.length > 0) {
      // Also recorded. If one of these ever stops being the host's own, the
      // pinned entry that excused it is named right here in the run output.
      console.log(
        `[real-host] ${hostOwned.length} item(s) excused as the host's own, per ` +
          `host_owned_noise in tests/real_host_smoke_policy.json:\n  ${hostOwned.join('\n  ')}`,
      );
    }

    expect(fromPage.ours, fromPage.ours.join('\n')).toEqual([]);
    expect(fromConsole.ours, fromConsole.ours.join('\n')).toEqual([]);
    expect(fromRequests.ours, fromRequests.ours.join('\n')).toEqual([]);
  });
});

test('the lane never widens its own exposure', () => {
  // A guard on the argv this run was built from, so a future edit that adds an
  // exposing argument fails here rather than on a live host.
  const args = buildHostArgs(POLICY, SUBJECT, { port: 18188 });

  expect(args).toContain('--cpu');
  // `--listen` is absent deliberately. It would only restate the host's own
  // default bind, but OpenClaw's exposure heuristic reads the flag and not its
  // value, so passing it forces the lane to configure an admin token the browser
  // cannot present. See the HOTSPOT note on buildHostArgs.
  expect(args).not.toContain('--listen');
  for (const forbidden of POLICY.runtime.forbidden_args) {
    expect(args).not.toContain(forbidden);
  }
});

test('the host reported binding to the loopback address this lane pins', () => {
  // The bind is proven from what the host printed, not from what the lane asked
  // for. This is the guard that replaced an argv assertion, and it is the one
  // that would catch a pinned core whose default bind had changed - which an
  // argv assertion, by construction, could not.
  const observed = parseHostBindOrigin(readHostLog());

  expect(observed, 'the host log carries no startup origin line').not.toBeNull();
  expect(observed).toBe(POLICY.runtime.bind_host);
  expect(POLICY.runtime.allowed_bind_hosts).toContain(observed);
});
