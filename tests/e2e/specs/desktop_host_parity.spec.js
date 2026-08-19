import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import {
  clickTab,
  mockCompatApprovalsList,
  mockComfyUiCore,
  mockRemoteAdminBaseline,
  waitForAdminConsoleReady,
  waitForOpenClawReady,
} from '../utils/helpers.js';

const pendingApproval = {
  approval_id: 'apr-r166-001',
  template_id: 'desktop_host_smoke',
  status: 'pending',
  requested_at: '2026-04-01T12:00:00Z',
  source: 'desktop-host-harness',
  inputs: { prompt: 'desktop host parity' },
};

async function captureParityEvidence(page, testInfo, name) {
  const screenshot = await page.screenshot();
  await testInfo.attach(name, {
    body: screenshot,
    contentType: 'image/png',
  });
  const evidenceDir = path.resolve(process.cwd(), '.tmp', 'desktop-host-parity-evidence');
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(path.join(evidenceDir, `${name}.png`), screenshot);
}

test.describe('Desktop host parity lane', () => {
  test('keeps standalone sidebar evidence separate from desktop host evidence', async ({ page }) => {
    await mockComfyUiCore(page, { hostSurface: 'standalone_frontend' });
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);

    const host = page.locator('#sidebar-tab-comfyui-openclaw');
    await expect(host).toHaveAttribute('data-openclaw-host-surface', 'standalone_frontend');
    await expect(host).toHaveAttribute('data-openclaw-desktop-host', 'false');
    await expect(host).toHaveAttribute('data-openclaw-reference-frontend', '1.52.1');
    await expect(host).toHaveAttribute('data-openclaw-current-desktop-version', '1.0.32-rc.1');
    await expect(host).toHaveAttribute('data-openclaw-current-desktop-generation', 'managed_install');
    await expect(host).toHaveAttribute(
      'data-openclaw-current-desktop-hosted-version-mode',
      'installation_specific',
    );
  });

  test('boots the legacy sidebar under desktop host signals and keeps approvals interactive', async ({ page }, testInfo) => {
    await mockComfyUiCore(page, { hostSurface: 'desktop' });
    await mockCompatApprovalsList(page, [pendingApproval]);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);

    const host = page.locator('#sidebar-tab-comfyui-openclaw');
    await expect(host).toHaveAttribute('data-openclaw-host-surface', 'desktop');
    await expect(host).toHaveAttribute('data-openclaw-desktop-host', 'true');
    await expect(host).toHaveAttribute('data-openclaw-reference-frontend', '1.52.1');
    await expect(host).toHaveAttribute('data-openclaw-current-desktop-version', '1.0.32-rc.1');
    await expect(host).toHaveAttribute('data-openclaw-current-desktop-generation', 'managed_install');
    await expect(host).toHaveAttribute(
      'data-openclaw-current-desktop-hosted-version-mode',
      'installation_specific',
    );
    await expect(host).toHaveAttribute('data-openclaw-desktop-generation', 'legacy_fixed_bundle');
    await expect(host).toHaveAttribute('data-openclaw-desktop-bridge-kind', 'electron_api');
    await expect(host).toHaveAttribute('data-openclaw-desktop-hosted-version-mode', 'fixed');
    await expect(host).toHaveAttribute('data-openclaw-desktop-version', '0.9.4');
    await expect(host).toHaveAttribute('data-openclaw-desktop-core-version', '0.22.3');
    await expect(host).toHaveAttribute('data-openclaw-desktop-embedded-frontend', '1.43.18');
    await expect(host).toHaveAttribute('data-openclaw-desktop-frontend-parity', 'lagging');

    await clickTab(page, 'Approvals');
    await expect(page.locator('#apr-list')).toContainText('apr-r166-001');
    await expect(page.locator('#apr-list')).toContainText('desktop_host_smoke');
    expect(await page.evaluate(() => window.__openclawBridgeActivity)).toEqual({
      memberReads: [],
      calls: [],
    });
    await captureParityEvidence(page, testInfo, 'legacy-desktop-sidebar');
  });

  test('boots the current Desktop sidebar without bridge access and keeps approvals interactive', async ({ page }, testInfo) => {
    await mockComfyUiCore(page, { hostSurface: 'comfy_desktop' });
    await mockCompatApprovalsList(page, [pendingApproval]);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);

    const host = page.locator('#sidebar-tab-comfyui-openclaw');
    await expect(host).toHaveAttribute('data-openclaw-host-surface', 'comfy_desktop');
    await expect(host).toHaveAttribute('data-openclaw-desktop-host', 'true');
    await expect(host).toHaveAttribute('data-openclaw-reference-frontend', '');
    await expect(host).toHaveAttribute('data-openclaw-desktop-generation', 'managed_install');
    await expect(host).toHaveAttribute('data-openclaw-desktop-bridge-kind', 'comfy_desktop2');
    await expect(host).toHaveAttribute(
      'data-openclaw-desktop-hosted-version-mode',
      'installation_specific',
    );
    await expect(host).toHaveAttribute('data-openclaw-desktop-version', '1.0.32-rc.1');
    await expect(host).toHaveAttribute('data-openclaw-desktop-core-version', '');
    await expect(host).toHaveAttribute('data-openclaw-desktop-embedded-frontend', '');

    await clickTab(page, 'Approvals');
    await expect(page.locator('#apr-list')).toContainText('apr-r166-001');
    await expect(page.locator('#apr-list')).toContainText('desktop_host_smoke');
    expect(await page.evaluate(() => window.__openclawBridgeActivity)).toEqual({
      memberReads: [],
      calls: [],
    });
    await captureParityEvidence(page, testInfo, 'current-desktop-sidebar');
  });

  test('stamps legacy desktop host metadata on the admin console and refreshes approvals', async ({ page, baseURL }, testInfo) => {
    await mockRemoteAdminBaseline(page, {
      hostSurface: 'desktop',
      approvals: [pendingApproval],
    });
    await page.goto(new URL('/web/admin_console.html', baseURL).toString());

    await expect(page.locator('body')).toHaveAttribute('data-openclaw-host-surface', 'desktop');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-host', 'true');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-reference-frontend', '1.52.1');
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-current-desktop-version',
      '1.0.32-rc.1',
    );
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-current-desktop-generation',
      'managed_install',
    );
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-current-desktop-hosted-version-mode',
      'installation_specific',
    );
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-desktop-generation',
      'legacy_fixed_bundle',
    );
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-desktop-bridge-kind',
      'electron_api',
    );
    await expect(page.locator('body')).toHaveAttribute(
      'data-openclaw-desktop-hosted-version-mode',
      'fixed',
    );
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-version', '0.9.4');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-core-version', '0.22.3');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-embedded-frontend', '1.43.18');
    await expect(page.locator('body')).toHaveAttribute('data-openclaw-desktop-frontend-parity', 'lagging');

    await waitForAdminConsoleReady(page);
    await page.locator('#refreshApprovals').click();
    await expect(page.locator('#approvalsList')).toContainText('apr-r166-001', { timeout: 15000 });
    await expect(page.locator('#approvalsList')).toContainText('desktop_host_smoke', { timeout: 15000 });
    expect(await page.evaluate(() => window.__openclawBridgeActivity)).toEqual({
      memberReads: [],
      calls: [],
    });
    await captureParityEvidence(page, testInfo, 'legacy-desktop-admin');
  });

  test('stamps current Desktop metadata on the admin console and refreshes approvals', async ({ page, baseURL }, testInfo) => {
    await mockRemoteAdminBaseline(page, {
      hostSurface: 'comfy_desktop',
      approvals: [pendingApproval],
    });
    await page.goto(new URL('/web/admin_console.html', baseURL).toString());

    const body = page.locator('body');
    await expect(body).toHaveAttribute('data-openclaw-host-surface', 'comfy_desktop');
    await expect(body).toHaveAttribute('data-openclaw-desktop-host', 'true');
    await expect(body).toHaveAttribute('data-openclaw-reference-frontend', '');
    await expect(body).toHaveAttribute('data-openclaw-desktop-generation', 'managed_install');
    await expect(body).toHaveAttribute('data-openclaw-desktop-bridge-kind', 'comfy_desktop2');
    await expect(body).toHaveAttribute(
      'data-openclaw-desktop-hosted-version-mode',
      'installation_specific',
    );
    await expect(body).toHaveAttribute('data-openclaw-desktop-version', '1.0.32-rc.1');
    await expect(body).toHaveAttribute('data-openclaw-desktop-core-version', '');
    await expect(body).toHaveAttribute('data-openclaw-desktop-embedded-frontend', '');

    await waitForAdminConsoleReady(page);
    await page.locator('#refreshApprovals').click();
    await expect(page.locator('#approvalsList')).toContainText('apr-r166-001', { timeout: 15000 });
    await expect(page.locator('#approvalsList')).toContainText('desktop_host_smoke', { timeout: 15000 });
    expect(await page.evaluate(() => window.__openclawBridgeActivity)).toEqual({
      memberReads: [],
      calls: [],
    });
    await captureParityEvidence(page, testInfo, 'current-desktop-admin');
  });
});
