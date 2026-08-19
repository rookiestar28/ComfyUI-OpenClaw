import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

const TEST_OUTPUT_PNG = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIW2NkYGD4DwABBAEAe7YQDgAAAABJRU5ErkJggg==',
    'base64'
);

test.describe('R107 Live Backend Parity', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        // Mock common endpoints
        await page.route('**/openclaw/config', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, config: {}, apply: {} }) });
        });
        await page.route('**/openclaw/logs/tail*', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, content: [] }) });
        });
        await page.route('**/openclaw/health', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, pack: { version: 'test' } }) });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('Planner (Submit) critical path - Success', async ({ page }) => {
        // Mock Planner API
        await page.route('**/openclaw/assist/planner', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    positive: "A beautiful landscape",
                    negative: "ugly, blurry",
                    params: { width: 1024, height: 1024 }
                })
            });
        });

        await clickTab(page, 'Planner');

        // Check initial state
        await expect(page.locator('#planner-run-btn')).toBeVisible();

        // Run Plan
        await page.locator('#planner-run-btn').click();

        // Verify result population
        await expect(page.locator('#planner-out-pos')).toHaveValue("A beautiful landscape");
        await expect(page.locator('#planner-out-neg')).toHaveValue("ugly, blurry");
    });

    test('Job Monitor (Status/Results) critical path', async ({ page }) => {
        const jobId = "job-123-abc";

        // Mock History (Polling)
        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                images: [{ filename: "test_img.png", type: "output" }]
                            }
                        }
                    }
                })
            });
        });

        // Mock Trace
        await page.route(`**/openclaw/trace/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    trace: {
                        trace_id: "trace-xyz",
                        events: [{ event: "queued", ts: 1700000000 }, { event: "completed", ts: 1700000010 }]
                    }
                })
            });
        });
        await page.route('**/view**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            if (
                request.method() !== 'GET'
                || url.searchParams.get('filename') !== 'test_img.png'
                || url.searchParams.get('type') !== 'output'
            ) {
                await route.fallback();
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'image/png',
                body: TEST_OUTPUT_PNG,
            });
        });

        await clickTab(page, 'Jobs');

        // Add Job
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        // Assert Job Row Appears
        const jobRow = page.locator('.openclaw-job-row').first();
        await expect(jobRow).toBeVisible();
        await expect(jobRow).toContainText(jobId.substring(0, 16));

        // Wait for status to become completed (polling)
        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });

        // Assert Image Output
        await expect(page.locator('img[src*="test_img.png"]')).toBeVisible();
    });

    test('Job Monitor keeps asset hashing optional and asset API no-go explicit', async ({ page }) => {
        const jobId = "job-asset-phase2";
        let assetApiCalls = 0;
        let jobsAssetsCalls = 0;
        let hostilePreviewCalls = 0;

        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                images: [
                                    {
                                        filename: "filename-only.png",
                                        type: "output",
                                        preview_url: "https://evil.example/hostile-preview.png",
                                        asset: {
                                            id: "asset-without-hash",
                                        },
                                    },
                                    {
                                        filename: "preview.png",
                                        type: "temp",
                                        hash: "blake3:abc123",
                                    },
                                    {
                                        asset: {
                                            id: "asset-only-42",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                }),
            });
        });

        await page.route('**/openclaw/trace/**', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'not_found' }),
            });
        });

        await page.route('**/api/assets**', async route => {
            assetApiCalls += 1;
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'asset_api_should_not_be_called' }),
            });
        });

        await page.route(`**/api/jobs/${jobId}/assets**`, async route => {
            jobsAssetsCalls += 1;
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'jobs_assets_should_not_be_called' }),
            });
        });

        await page.route('**://evil.example/**', async route => {
            hostilePreviewCalls += 1;
            await route.fulfill({
                status: 500,
                contentType: 'text/plain',
                body: 'hostile_preview_should_not_be_requested',
            });
        });

        await page.route('**/view**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            const filename = url.searchParams.get('filename');
            const isFilenameOnlyPreview = (
                request.method() === 'GET'
                && filename === 'filename-only.png'
                && url.searchParams.get('type') === 'output'
            );
            const isHashBackedPreview = (
                request.method() === 'GET'
                && filename === 'blake3:abc123'
                && !url.searchParams.has('type')
                && !url.searchParams.has('subfolder')
            );
            if (!isFilenameOnlyPreview && !isHashBackedPreview) {
                await route.fallback();
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'image/png',
                body: TEST_OUTPUT_PNG,
            });
        });

        await clickTab(page, 'Jobs');
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });
        const filenamePreview = page.locator('img[src*="filename-only.png"]').first();
        await expect(filenamePreview).toBeVisible();
        const filenamePreviewUrl = new URL(await filenamePreview.getAttribute('src'), page.url());
        expect(filenamePreviewUrl.origin).toBe(new URL(page.url()).origin);
        expect(filenamePreviewUrl.pathname).toMatch(/\/view$/);
        expect(filenamePreviewUrl.searchParams.get('filename')).toBe('filename-only.png');
        await expect(page.locator('img[src*="blake3%3Aabc123"]')).toBeVisible();
        await expect(page.locator('.openclaw-job-output-fallback')).toContainText('Asset API output requires /api/assets');
        expect(assetApiCalls).toBe(0);
        expect(jobsAssetsCalls).toBe(0);
        expect(hostilePreviewCalls).toBe(0);
    });

    test('Job Monitor surfaces non-image media outputs as safe fallbacks', async ({ page }) => {
        const jobId = "job-media-refs";
        let assetApiCalls = 0;

        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                video: [{ filename: "clip.webm", type: "output" }],
                                audio: [{ filename: "sound.wav", type: "output" }],
                                "3d": ["mesh.glb"],
                                text: ["hello from text output"],
                            },
                        },
                    },
                }),
            });
        });

        await page.route('**/openclaw/trace/**', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'not_found' }),
            });
        });

        await page.route('**/api/assets**', async route => {
            assetApiCalls += 1;
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'asset_api_should_not_be_called' }),
            });
        });

        await clickTab(page, 'Jobs');
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        const jobRow = page.locator('.openclaw-job-row').first();
        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });
        await expect(jobRow.locator('img')).toHaveCount(0);
        await expect(jobRow.locator('.openclaw-job-output-media-fallback')).toHaveCount(3);
        await expect(jobRow.locator('.openclaw-job-output-media-fallback')).toContainText([
            'video output available',
            'audio output available',
            '3d output available',
        ]);
        await expect(jobRow.locator('.openclaw-job-output-text')).toContainText('hello from text output');
        expect(assetApiCalls).toBe(0);
    });

    test('Job Monitor renders official advanced 3d results as one bounded view link', async ({ page }) => {
        const jobId = "job-advanced-3d-result";
        const metadataCanary = "metadata-value-must-not-project";
        let assetApiCalls = 0;

        await page.evaluate(() => {
            window.__openclawOpenedUrls = [];
            window.open = (url, target) => {
                window.__openclawOpenedUrls.push({ url, target });
                return null;
            };
        });

        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                result: [
                                    "models/scene one.splat",
                                    { camera: metadataCanary },
                                    [{ model: metadataCanary }],
                                ],
                            },
                            "10": {
                                result: ["../private.glb"],
                            },
                        },
                    },
                }),
            });
        });

        await page.route('**/openclaw/trace/**', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'not_found' }),
            });
        });

        await page.route('**/api/assets**', async route => {
            assetApiCalls += 1;
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'asset_api_should_not_be_called' }),
            });
        });

        await clickTab(page, 'Jobs');
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        const jobRow = page.locator('.openclaw-job-row').first();
        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });
        const fallbacks = jobRow.locator('.openclaw-job-output-media-fallback');
        await expect(fallbacks).toHaveCount(1);
        await expect(fallbacks).toContainText('3d output available');
        await expect(jobRow).not.toContainText(metadataCanary);
        await expect(jobRow.locator('img, canvas')).toHaveCount(0);

        await fallbacks.click();
        const opened = await page.evaluate(() => {
            const entry = window.__openclawOpenedUrls[0];
            if (!entry) return null;
            const parsed = new URL(entry.url, window.location.origin);
            return {
                origin: parsed.origin,
                currentOrigin: window.location.origin,
                pathname: parsed.pathname,
                filename: parsed.searchParams.get("filename"),
                subfolder: parsed.searchParams.get("subfolder"),
                type: parsed.searchParams.get("type"),
                target: entry.target,
            };
        });
        expect(opened).toEqual({
            origin: opened.currentOrigin,
            currentOrigin: opened.currentOrigin,
            pathname: expect.stringMatching(/\/view$/),
            filename: "scene one.splat",
            subfolder: "models",
            type: "output",
            target: "_blank",
        });
        expect(assetApiCalls).toBe(0);
    });

    test('Job Monitor renders HDR image outputs as explicit fallbacks', async ({ page }) => {
        const jobId = "job-hdr-refs";
        let hdrViewRequests = 0;

        await page.evaluate(() => {
            window.__openclawOpenedUrls = [];
            window.open = (url, target) => {
                window.__openclawOpenedUrls.push({ url, target });
                return null;
            };
        });

        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                images: [
                                    { filename: "scene.exr", type: "output" },
                                    { filename: "studio.hdr", type: "output" },
                                    { filename: "normal.png", type: "output" },
                                ],
                            },
                        },
                    },
                }),
            });
        });

        await page.route('**/openclaw/trace/**', async route => {
            await route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ error: 'not_found' }),
            });
        });

        await page.route('**/view**', async route => {
            const url = new URL(route.request().url());
            const filename = url.searchParams.get('filename');
            if (filename === 'scene.exr' || filename === 'studio.hdr') {
                hdrViewRequests += 1;
            }
            if (filename !== 'normal.png') {
                await route.fallback();
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: 'image/png',
                body: TEST_OUTPUT_PNG,
            });
        });

        await clickTab(page, 'Jobs');
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        const jobRow = page.locator('.openclaw-job-row').first();
        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });
        await expect(jobRow.locator('img')).toHaveCount(1);
        await expect(jobRow.locator('img[src*="normal.png"]')).toBeVisible();
        await expect(jobRow.locator('img[src*="scene.exr"]')).toHaveCount(0);
        await expect(jobRow.locator('img[src*="studio.hdr"]')).toHaveCount(0);
        await expect(jobRow.locator('.openclaw-job-output-hdr-fallback')).toHaveCount(2);
        await expect(jobRow.locator('.openclaw-job-output-hdr-fallback')).toContainText([
            'HDR output available',
            'HDR output available',
        ]);
        expect(hdrViewRequests).toBe(0);

        await jobRow.locator('.openclaw-job-output-hdr-fallback').first().click();
        const openedUrls = await page.evaluate(() => window.__openclawOpenedUrls);
        expect(openedUrls).toEqual([
            expect.objectContaining({
                url: expect.stringContaining('scene.exr'),
                target: '_blank',
            }),
        ]);
    });

    test('Degraded Adapter / Fail Handling', async ({ page }) => {
        // Mock Planner Failure (503 Service Unavailable)
        await page.route('**/openclaw/assist/planner', async route => {
            await route.fulfill({
                status: 503,
                contentType: 'application/json',
                body: JSON.stringify({
                    ok: false,
                    error: "service_unavailable",
                    detail: "Backend overload"
                })
            });
        });

        await clickTab(page, 'Planner');
        await page.locator('#planner-run-btn').click();

        // Assume error handling shows a text in the container or valid error box
        // Checking openclaw_utils.js showError implementation would be precise,
        // but typically it creates an element with error text.
        // Let's look for the error message text.
        await expect(page.getByText('service_unavailable')).toBeVisible();
    });
});
