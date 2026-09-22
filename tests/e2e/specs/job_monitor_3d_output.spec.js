import { expect, test } from '@playwright/test';
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from '../utils/helpers.js';

test('Job Monitor shows one tile for a saved 3D file with two host representations', async ({ page }) => {
    const jobId = 'job-saved-3d-output';
    await mockComfyUiCore(page);
    await page.route(`**/history/${jobId}`, async route => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                [jobId]: {
                    status: { status_str: 'success', completed: true },
                    outputs: {
                        save: {
                            result: ['3d/model_00001.glb', null, []],
                            '3d': [{ filename: 'model_00001.glb', subfolder: '3d', type: 'output' }],
                        },
                    },
                },
            }),
        });
    });
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'Jobs');
    await page.locator('input[placeholder="prompt_id"]').fill(jobId);
    await page.getByText('Add').click();

    const row = page.locator('.openclaw-job-row').first();
    await expect(row.locator('.openclaw-kv-val.ok')).toHaveText('completed');
    await expect(row.locator('.openclaw-job-output-media-fallback')).toHaveCount(1);
    await expect(row.locator('.openclaw-job-output-media-fallback')).toHaveAttribute(
        'title', 'model_00001.glb'
    );
});
