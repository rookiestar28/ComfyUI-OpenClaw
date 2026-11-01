import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { mockComfyUiCore, waitForOpenClawReady } from '../utils/helpers.js';

async function captureSidebarEvidence(page, testInfo, name) {
  const screenshot = await page.screenshot();
  await testInfo.attach(name, { body: screenshot, contentType: 'image/png' });
  const evidenceDir = path.resolve(process.cwd(), '.tmp', 'r256-sidebar-evidence');
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(path.join(evidenceDir, `${name}.png`), screenshot);
}

async function readSidebarGeometry(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('.side-bar-panel');
    const content = document.querySelector('.sidebar-content-container');
    const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
    const rightmostControl = document.querySelector('.openclaw-tabs .openclaw-tab:nth-child(4)');
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      return { left: box.left, right: box.right, width: box.width };
    };
    return {
      panel: rect(panel),
      content: rect(content),
      mount: rect(mount),
      rightmostControl: rect(rightmostControl),
      inline: {
        panelMinWidth: panel.style.minWidth,
        panelWidth: panel.style.width,
        panelFlexBasis: panel.style.flexBasis,
      },
    };
  });
}

async function mountNativeSidebar(page, width) {
  return page.evaluate(async (width) => {
    const panel = document.querySelector('.side-bar-panel');
    const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
    const tab = window.__openclawMockSidebarTab;
    tab.destroy();
    mount.replaceChildren();
    const parent = panel.parentElement;
    // Use native host allocation without the legacy harness important min-width overrides.
    parent.id = 'native-sidebar-panels';
    parent.style.cssText = 'display:flex; width:1200px; height:720px';
    panel.style.cssText = `flex:${width} 1 0px; min-width:0; overflow:hidden`;
    panel.querySelector('.sidebar-content-container').style.cssText = 'width:100%; height:100%; min-width:0; overflow:hidden';
    panel.setAttribute('data-panel', '');
    panel.setAttribute('data-panel-id', 'sidebar');
    panel.setAttribute('data-panel-group-id', 'canvas');
    const canvas = document.createElement('div');
    canvas.id = 'native-canvas';
    canvas.style.cssText = `flex:${1200 - width} 1 0px; min-width:0`;
    parent.appendChild(canvas);
    const baseline = panel.getBoundingClientRect().width;
    tab.render();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    return { baseline, mounted: panel.getBoundingClientRect().width, marker: mount.getAttribute('data-openclaw-layout') };
  }, width);
}

test.describe('OpenClaw sidebar layout ownership', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await mockComfyUiCore(page);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
  });

  test('allocates the 560px floor at the host width owner and keeps the fourth control visible', async ({ page }, testInfo) => {
    const geometry = await readSidebarGeometry(page);
    console.log(`R256_GEOMETRY=${JSON.stringify(geometry)}`);
    await captureSidebarEvidence(page, testInfo, 'sidebar-width-floor');

    expect(geometry.panel.width).toBeGreaterThanOrEqual(560);
    expect(geometry.content.width).toBeGreaterThanOrEqual(560);
    expect(geometry.mount.width).toBeGreaterThanOrEqual(560);
    expect(geometry.rightmostControl.right).toBeLessThanOrEqual(geometry.panel.right);
    expect(geometry.rightmostControl.right).toBeLessThanOrEqual(geometry.content.right);
  });

  test('retains comparable legacy-only RED and owned-layout GREEN visuals', async ({ page }, testInfo) => {
    await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      window.__openclawMockSidebarTab.destroy();
      panel.style.minWidth = '560px';
      content.style.minWidth = '560px';
      mount.style.minWidth = '560px';
      await new Promise((resolve) => requestAnimationFrame(resolve));
    });
    const before = await readSidebarGeometry(page);
    await captureSidebarEvidence(page, testInfo, 'sidebar-width-before-legacy-min-only');

    await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      panel.style.removeProperty('min-width');
      content.style.removeProperty('min-width');
      mount.style.removeProperty('min-width');
      window.__openclawMockSidebarTab.render();
      await new Promise((resolve) => requestAnimationFrame(resolve));
    });
    const after = await readSidebarGeometry(page);
    await captureSidebarEvidence(page, testInfo, 'sidebar-width-after-owned-layout');

    expect(before.panel.width).toBe(340);
    expect(before.rightmostControl.right).toBeGreaterThan(before.panel.right);
    expect(after.panel.width).toBe(560);
    expect(after.content.width).toBe(560);
    expect(after.rightmostControl.right).toBeLessThanOrEqual(after.panel.right);
    expect(after.rightmostControl.right).toBeLessThanOrEqual(after.content.right);
  });

  test('restores host styles across destroy, remount, and ten lifecycle cycles', async ({ page }, testInfo) => {
    const evidence = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      const tab = window.__openclawMockSidebarTab;
      const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
      const styles = () => ({
        panel: {
          flexBasis: panel.style.flexBasis,
          minWidth: panel.style.minWidth,
          width: panel.style.width,
        },
        content: {
          flexBasis: content.style.flexBasis,
          minWidth: content.style.minWidth,
          width: content.style.width,
        },
        mount: {
          flexBasis: mount.style.flexBasis,
          minWidth: mount.style.minWidth,
          width: mount.style.width,
        },
      });

      tab.destroy();
      await frame();
      const restored = styles();
      const restoredWidth = panel.getBoundingClientRect().width;
      const restoredMarker = mount.getAttribute('data-openclaw-host-surface');
      const restoredMountClass = mount.getAttribute('class');

      panel.style.width = '720px';
      panel.style.flexBasis = '720px';
      content.style.width = '720px';
      tab.render();
      await frame();
      const aboveFloor = styles();
      const aboveFloorWidth = panel.getBoundingClientRect().width;
      tab.destroy();

      panel.style.removeProperty('width');
      panel.style.removeProperty('flex-basis');
      content.style.removeProperty('width');
      for (let cycle = 0; cycle < 10; cycle += 1) {
        tab.render();
        await frame();
        tab.destroy();
        await frame();
      }
      const afterCycles = styles();
      const nextOwner = document.createElement('div');
      nextOwner.id = 'mock-next-sidebar-owner';
      mount.replaceChildren(nextOwner);
      await frame();
      await frame();

      return {
        aboveFloor,
        aboveFloorWidth,
        afterCycles,
        nextOwnerPresent: mount.firstElementChild?.id === 'mock-next-sidebar-owner',
        restored,
        restoredMarker,
        restoredMountClass,
        restoredWidth,
      };
    });

    await captureSidebarEvidence(page, testInfo, 'sidebar-layout-after-destroy');
    expect(evidence.restored).toEqual({
      panel: { flexBasis: '', minWidth: '', width: '' },
      content: { flexBasis: '', minWidth: '', width: '' },
      mount: { flexBasis: '', minWidth: '', width: '' },
    });
    expect(evidence.restoredWidth).toBe(340);
    expect(evidence.restoredMarker).toBeNull();
    expect(evidence.restoredMountClass).toBeNull();
    expect(evidence.aboveFloorWidth).toBe(720);
    expect(evidence.aboveFloor.panel.width).toBe('720px');
    expect(evidence.aboveFloor.panel.flexBasis).toBe('720px');
    expect(evidence.afterCycles).toEqual(evidence.restored);
    expect(evidence.nextOwnerPresent).toBe(true);
  });

  test('keeps a width the host pins while OpenClaw is mounted after the tab is destroyed', async ({ page }, testInfo) => {
    const evidence = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const tab = window.__openclawMockSidebarTab;
      const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
      await frame();
      const mounted = {
        flexBasis: panel.style.flexBasis,
        width: panel.getBoundingClientRect().width,
      };

      // The host splitter sizer pins the dragged width as rigid flex values on resize end.
      panel.style.flexBasis = '700px';
      panel.style.flexGrow = '0';
      panel.style.flexShrink = '0';
      await frame();
      tab.destroy();
      await frame();

      return {
        mounted,
        released: {
          flexBasis: panel.style.flexBasis,
          inlineMinWidth: panel.style.minWidth,
          inlineWidth: panel.style.width,
          width: panel.getBoundingClientRect().width,
        },
      };
    });

    await captureSidebarEvidence(page, testInfo, 'sidebar-host-pin-after-destroy');
    expect(evidence.mounted.flexBasis).toBe('560px');
    expect(evidence.mounted.width).toBeGreaterThanOrEqual(560);
    expect(evidence.released).toEqual({
      flexBasis: '700px',
      inlineMinWidth: '',
      inlineWidth: '',
      width: 700,
    });
  });

  test('applies and releases the floor on a reka-shaped sidebar without PrimeVue classes', async ({ page }) => {
    const evidence = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const tab = window.__openclawMockSidebarTab;
      const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
      tab.destroy();
      await frame();
      panel.classList.remove('p-splitterpanel');
      panel.id = 'first-side-panel';
      tab.render();
      await frame();
      const mounted = {
        flexBasis: panel.style.flexBasis,
        width: panel.getBoundingClientRect().width,
      };
      tab.destroy();
      await frame();
      return {
        mounted,
        primeVuePanels: document.querySelectorAll('.p-splitterpanel').length,
        released: {
          flexBasis: panel.style.flexBasis,
          minWidth: panel.style.minWidth,
          width: panel.style.width,
          measured: panel.getBoundingClientRect().width,
        },
      };
    });

    expect(evidence.primeVuePanels).toBe(0);
    expect(evidence.mounted.flexBasis).toBe('560px');
    expect(evidence.mounted.width).toBeGreaterThanOrEqual(560);
    expect(evidence.released).toEqual({ flexBasis: '', minWidth: '', width: '', measured: 340 });
  });

  test('restores acquired layout before rendering an error fallback', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const { openclawUI } = await import('/web/openclaw_ui.js');
      const panel = document.createElement('div');
      panel.className = 'side-bar-panel p-splitterpanel';
      panel.style.cssText = 'min-width: 11px; width: 43%; flex-basis: 43%';
      const content = document.createElement('div');
      content.className = 'sidebar-content-container';
      content.style.cssText = 'min-width: 7px; width: 42%';
      const mount = document.createElement('div');
      mount.style.minWidth = '3px';
      mount.className = 'host-error-mount';
      mount.setAttribute('data-host-owner', 'error-preserve');
      content.appendChild(mount);
      panel.appendChild(content);
      document.body.appendChild(panel);

      const originalRender = openclawUI._render;
      openclawUI._render = () => {
        throw new Error('bounded layout test failure');
      };
      try {
        openclawUI.mount(mount, { hostSurfaceOptions: { win: {} } });
      } finally {
        openclawUI._render = originalRender;
      }

      return {
        content: {
          minWidth: content.style.minWidth,
          width: content.style.width,
        },
        fallback: mount.querySelector('.openclaw-error-boundary')?.textContent,
        hostOwner: mount.getAttribute('data-host-owner'),
        marker: mount.getAttribute('data-openclaw-host-surface'),
        mountClass: mount.className,
        mountMinWidth: mount.style.minWidth,
        panel: {
          flexBasis: panel.style.flexBasis,
          minWidth: panel.style.minWidth,
          width: panel.style.width,
        },
      };
    });

    expect(result.panel).toEqual({ flexBasis: '43%', minWidth: '11px', width: '43%' });
    expect(result.content).toEqual({ minWidth: '7px', width: '42%' });
    expect(result.mountMinWidth).toBe('3px');
    expect(result.marker).toBeNull();
    expect(result.mountClass).toBe('host-error-mount');
    expect(result.hostOwner).toBe('error-preserve');
    expect(result.fallback).toContain('Something went wrong');
  });

  test('releases shared-mount ownership when the next custom extension renders without destroy', async ({ page }, testInfo) => {
    const before = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      const tab = window.__openclawMockSidebarTab;
      const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));

      tab.destroy();
      panel.style.cssText = 'min-width: 12px; width: 45%; flex-basis: 45%';
      content.style.cssText = 'min-width: 8px; width: 44%';
      mount.style.cssText = 'height: 100vh; min-width: 4px';
      mount.className = 'shared-host-mount';
      mount.setAttribute('data-host-owner', 'preserve-me');
      for (const name of [...mount.getAttributeNames()]) {
        if (name.startsWith('data-openclaw-')) mount.removeAttribute(name);
      }

      tab.render();
      await frame();
      return {
        className: mount.className,
        marker: mount.getAttribute('data-openclaw-host-surface'),
        rootExclusive: mount.children.length === 1
          && mount.firstElementChild?.classList.contains('openclaw-sidebar-container'),
        shellInsideRoot: ['.openclaw-header', '.openclaw-tabs', '.openclaw-content']
          .every((selector) => mount.firstElementChild?.querySelector(selector)),
        panelWidth: panel.style.width,
      };
    });
    await captureSidebarEvidence(page, testInfo, 'r257-sidebar-before-no-destroy-switch');

    const after = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      const foreign = document.createElement('section');
      foreign.id = 'foreign-custom-sidebar-owner';
      foreign.textContent = 'Foreign extension remains intact';
      window.__r257ForeignNode = foreign;

      // Mirrors current ExtensionSlot prop reuse: incoming render receives the same mount
      // and the outgoing OpenClaw destroy callback is not invoked first.
      mount.replaceChildren(foreign);
      await Promise.resolve();
      await new Promise((resolve) => requestAnimationFrame(resolve));

      return {
        className: mount.className,
        content: {
          flexBasis: content.style.flexBasis,
          minWidth: content.style.minWidth,
          width: content.style.width,
        },
        foreignIdentityPreserved: mount.firstElementChild === window.__r257ForeignNode,
        foreignText: foreign.textContent,
        hostOwner: mount.getAttribute('data-host-owner'),
        marker: mount.getAttribute('data-openclaw-host-surface'),
        mount: {
          flexBasis: mount.style.flexBasis,
          minWidth: mount.style.minWidth,
          width: mount.style.width,
        },
        panel: {
          flexBasis: panel.style.flexBasis,
          minWidth: panel.style.minWidth,
          width: panel.style.width,
        },
      };
    });
    await captureSidebarEvidence(page, testInfo, 'r257-sidebar-after-no-destroy-switch');

    console.log(`R257_SHARED_MOUNT_BEFORE=${JSON.stringify(before)}`);
    console.log(`R257_SHARED_MOUNT_AFTER=${JSON.stringify(after)}`);
    expect(after.marker).toBeNull();
    expect(after.className).toBe('shared-host-mount');
    expect(after.panel).toEqual({ flexBasis: '45%', minWidth: '12px', width: '45%' });
    expect(after.content).toEqual({ flexBasis: '', minWidth: '8px', width: '44%' });
    expect(after.mount).toEqual({ flexBasis: '', minWidth: '4px', width: '' });
    expect(after.hostOwner).toBe('preserve-me');
    expect(after.foreignIdentityPreserved).toBe(true);
    expect(after.foreignText).toBe('Foreign extension remains intact');

    expect(before.className).toBe('shared-host-mount');
    expect(before.marker).toBe('standalone_frontend');
    expect(before.panelWidth).toBe('560px');
    expect(before.rootExclusive).toBe(true);
    expect(before.shellInsideRoot).toBe(true);

    const repeated = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      const content = document.querySelector('.sidebar-content-container');
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      const tab = window.__openclawMockSidebarTab;
      const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
      const results = [];

      for (let cycle = 0; cycle < 10; cycle += 1) {
        tab.render();
        await frame();
        const foreign = document.createElement('section');
        foreign.dataset.cycle = String(cycle);
        foreign.textContent = `foreign-${cycle}`;
        mount.replaceChildren(foreign);
        await Promise.resolve();
        await frame();
        results.push({
          className: mount.className,
          contentWidth: content.style.width,
          foreignIdentityPreserved: mount.firstElementChild === foreign,
          marker: mount.getAttribute('data-openclaw-host-surface'),
          mountMinWidth: mount.style.minWidth,
          panelFlexBasis: panel.style.flexBasis,
          panelWidth: panel.style.width,
        });
      }
      return results;
    });

    expect(repeated).toHaveLength(10);
    for (const cycle of repeated) {
      expect(cycle).toEqual({
        className: 'shared-host-mount',
        contentWidth: '44%',
        foreignIdentityPreserved: true,
        marker: null,
        mountMinWidth: '4px',
        panelFlexBasis: '45%',
        panelWidth: '45%',
      });
    }
  });
  test('preserves native flex allocation and the latest host resize on peer handoff', async ({ page }, testInfo) => {
    const widths = await mountNativeSidebar(page, 336);
    await captureSidebarEvidence(page, testInfo, 'native-sidebar-mounted');
    expect(widths.baseline).toBeCloseTo(336, 0);
    expect(widths.marker).toBe('host');
    expect(Math.abs(widths.mounted - widths.baseline)).toBeLessThanOrEqual(2);
    const resized = await page.evaluate(async () => {
      const panel = document.querySelector('.side-bar-panel');
      panel.style.flexGrow = '480';
      document.querySelector('#native-canvas').style.flexGrow = '720';
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const dragged = panel.getBoundingClientRect().width;
      window.__openclawMockSidebarTab.destroy();
      const mount = document.querySelector('#sidebar-tab-comfyui-openclaw');
      const peer = document.createElement('section');
      peer.id = 'native-peer';
      mount.replaceChildren(peer);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return { dragged, released: panel.getBoundingClientRect().width, basis: panel.style.flexBasis,
        peerPreserved: mount.firstElementChild === peer, rootRemoved: !mount.querySelector('.openclaw-sidebar-container'),
        marker: mount.getAttribute('data-openclaw-layout') };
    });
    expect(resized.dragged).toBeCloseTo(480, 0);
    expect(resized.released).toBeCloseTo(resized.dragged, 0);
    expect(resized.basis).toBe('0px');
    expect(resized.peerPreserved).toBe(true);
    expect(resized.rootRemoved).toBe(true);
    expect(resized.marker).toBeNull();
  });

  for (const width of [312, 400, 560]) {
    test(`keeps native controls contained and keyboard-operable at ${width}px`, async ({ page }, testInfo) => {
      const allocation = await mountNativeSidebar(page, width);
      await captureSidebarEvidence(page, testInfo, `native-sidebar-${width}-shell`);
      expect(allocation.baseline).toBeCloseTo(width, 0);
      expect(allocation.mounted).toBeCloseTo(width, 0);
      expect(Math.abs(allocation.mounted - allocation.baseline)).toBeLessThanOrEqual(2);
      const modelInput = page.locator('input[list="openclaw-model-list"]');
      const modelSelect = modelInput.locator('..').locator('select');
      expect((await modelInput.boundingBox()).width).toBeGreaterThanOrEqual(120);
      await page.route('**/llm/models**', (route) => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['native-model', 'native-model-2'] }),
      }));
      await modelInput.fill('native-model');
      await page.getByRole('button', { name: 'Load Models', exact: true }).click();
      await expect(modelSelect).toBeVisible();
      await expect(modelSelect).toHaveValue('native-model');
      await expect(modelInput).toBeHidden();
      await modelSelect.selectOption('native-model-2');
      await expect(modelInput).toHaveValue('native-model-2');
      await modelSelect.selectOption('__custom__');
      await expect(modelInput).toBeVisible();
      expect((await modelInput.boundingBox()).width).toBeGreaterThanOrEqual(120);
      expect((await modelSelect.boundingBox()).width).toBeGreaterThanOrEqual(120);
      await modelInput.fill('custom-native-model');
      await expect(modelInput).toHaveValue('custom-native-model');
      await expect(modelInput.locator('..')).toContainText('2 models');
      const modelOverflow = await modelInput.locator('..').evaluate((row) => {
        const panel = row.closest('.side-bar-panel').getBoundingClientRect();
        return [...row.children].filter((node) => node.getClientRects().length).some((node) => {
          const box = node.getBoundingClientRect(); return box.left < panel.left - 1 || box.right > panel.right + 1;
        });
      });
      expect(modelOverflow).toBe(false);
      await captureSidebarEvidence(page, testInfo, `native-sidebar-${width}-model-controls`);
      await page.evaluate(() => {
        window.app.graph = {
          _nodes: [{ id: 10, type: 'KSampler', title: 'Sampler', widgets: [{ name: 'seed', type: 'number', value: 1, options: {} }] }],
          getNodeById(id) { return this._nodes.find((node) => String(node.id) === String(id)); },
          serialize() { return { version: 0.4, nodes: [] }; },
        };
      });
      await page.locator('.openclaw-tab').filter({ hasText: 'Parameter Lab' }).click();
      await page.locator('#lab-add-dim').focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('.openclaw-lab-dim-row.dynamic')).toHaveCount(1);
      await page.selectOption('.dim-node-select', '10');
      await page.selectOption('.dim-widget-select', 'seed');
      await page.locator('.dim-manual-input').fill('7');
      await page.locator('.dim-manual-input').press('Enter');
      await expect(page.locator('.openclaw-chip')).toContainText('7');
      const overflow = await page.evaluate(() => {
        const panel = document.querySelector('.side-bar-panel').getBoundingClientRect();
        return [...document.querySelectorAll('.openclaw-header button, .openclaw-tab, .openclaw-lab-actions button, .openclaw-lab-dim-row input, .openclaw-lab-dim-row select, .openclaw-lab-dim-row button')]
          .filter((element) => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
          .filter((element) => { const rect = element.getBoundingClientRect(); return rect.left < panel.left - 1 || rect.right > panel.right + 1; })
          .map((element) => element.id || element.className);
      });
      await captureSidebarEvidence(page, testInfo, `native-sidebar-${width}-lab`);
      expect(overflow).toEqual([]);
    });
  }

});
