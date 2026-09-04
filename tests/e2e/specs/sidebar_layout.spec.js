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
});
