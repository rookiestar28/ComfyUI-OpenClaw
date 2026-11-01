import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('Parameter Lab - Dynamic Dimensions', () => {
    test.beforeEach(async ({ page }) => {
        // 1. Setup mock environment
        await mockComfyUiCore(page);
        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);

        // 2. Inject mock graph with nodes and widgets
        await page.evaluate(() => {
            window.app.graph = {
                _nodes: [
                    {
                        id: 10,
                        type: "KSampler",
                        title: "My Sampler",
                        widgets: [
                            { name: "seed", type: "number", value: 1234, options: {} },
                            { name: "steps", type: "number", value: 20, options: { values: [20, 30, 40] } },
                            { name: "sampler_name", type: "combo", value: "euler", options: { values: ["euler", "ddim", "uni_pc"] } },
                            {
                                name: "video_edit",
                                type: "VIDEO_EDIT",
                                value: { trim: [0, 1] },
                                options: { values: [{ trim: [0, 1] }, ["structured"]] }
                            }
                        ]
                    },
                    {
                        id: 20,
                        type: "CheckpointLoader",
                        title: "Load Model",
                        widgets: [
                            { name: "ckpt_name", type: "combo", value: "base.ckpt", options: { values: ["base.ckpt", "v2.ckpt", "xl.ckpt"] } }
                        ]
                    }
                ],
                getNodeById(id) { return this._nodes.find(n => n.id === id); },
                serialize() { return { "test_graph": true }; }
            };
        });

        // 3. Open Parameter Lab
        await clickTab(page, 'Parameter Lab');
    });

    test('can select node, widget, and add values via dropdown', async ({ page }) => {
        // Add Dimension
        await page.click('#lab-add-dim');
        await expect(page.locator('.openclaw-lab-dim-row.dynamic')).toBeVisible();

        // Select Node (KSampler id=10)
        await page.selectOption('.dim-node-select', { value: '10' });

        // Select Widget (sampler_name)
        await page.selectOption('.dim-widget-select', { value: 'sampler_name' });

        // Verify candidates are populated
        const candidates = page.locator('.dim-candidate-select option');
        await expect(candidates).toHaveCount(4); // "Add option..." + 3 values

        // Select a candidate "ddim"
        await page.selectOption('.dim-candidate-select', { value: 'ddim' });

        // Verify chip added
        await expect(page.locator('.openclaw-chip >> text=ddim')).toBeVisible();

        // Select another "uni_pc"
        await page.selectOption('.dim-candidate-select', { value: 'uni_pc' });
        await expect(page.locator('.openclaw-chip >> text=uni_pc')).toBeVisible();

        // Verify remove chip
        await page.click('.openclaw-chip:has-text("ddim") .chip-rm');
        await expect(page.locator('.openclaw-chip >> text=ddim')).not.toBeVisible();
    });

    test('can add custom manual values', async ({ page }) => {
        await page.click('#lab-add-dim');

        // Select Node (KSampler id=10)
        await page.selectOption('.dim-node-select', { value: '10' });

        // Select Widget (seed)
        await page.selectOption('.dim-widget-select', { value: 'seed' });

        // Type custom value
        await page.fill('.dim-manual-input', '9999');
        await page.press('.dim-manual-input', 'Enter');

        // Verify chip
        await expect(page.locator('.openclaw-chip >> text=9999')).toBeVisible();
    });

    test('rejects oversized manual scalar values before chip state mutation', async ({ page }) => {
        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '10' });
        await page.selectOption('.dim-widget-select', { value: 'seed' });

        await page.fill('.dim-manual-input', '界'.repeat(5462));
        await page.press('.dim-manual-input', 'Enter');

        await expect(page.locator('.openclaw-chip')).toHaveCount(0);
        await expect(page.locator('.openclaw-banner')).toContainText('scalar_string_too_large');
    });

    test('does not offer structured widget values as ambiguous object candidates', async ({ page }) => {
        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '10' });
        await page.selectOption('.dim-widget-select', { value: 'video_edit' });

        const candidates = page.locator('.dim-candidate-select option');
        await expect(candidates).toHaveCount(1);
        await expect(candidates).not.toContainText('[object Object]');
    });

    test('caps dimensions before creating ambiguous experiment state', async ({ page }) => {
        for (let index = 0; index < 9; index += 1) {
            await page.click('#lab-add-dim');
        }

        await expect(page.locator('.openclaw-lab-dim-row.dynamic')).toHaveCount(8);
        await expect(page.locator('.openclaw-banner')).toContainText('too_many_dimensions');
    });

    test('rejects an oversized serialized workflow before the API request', async ({ page }) => {
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            window.__labRequestCount = 0;
            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    window.__labRequestCount += 1;
                    return { ok: false, status: 400, error: 'unexpected_request' };
                }
                return originalFetch(url, options);
            };
            window.app.graph.serialize = () => ({ payload: 'x'.repeat(4 * 1024 * 1024 + 1) });
        });

        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '10' });
        await page.selectOption('.dim-widget-select', { value: 'seed' });
        await page.fill('.dim-manual-input', '1');
        await page.press('.dim-manual-input', 'Enter');
        await page.click('#lab-generate');

        await expect.poll(() => page.evaluate(() => window.__labRequestCount)).toBe(0);
        await expect(page.locator('.openclaw-banner')).toContainText('workflow_too_large');
    });

    test('redacts workflow serialization failures before the API request', async ({ page }) => {
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            window.__labRequestCount = 0;
            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    window.__labRequestCount += 1;
                    return { ok: false, status: 400, error: 'unexpected_request' };
                }
                return originalFetch(url, options);
            };
            window.app.graph.serialize = () => {
                throw new Error('secret=workflow-private-detail');
            };
        });

        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '10' });
        await page.selectOption('.dim-widget-select', { value: 'seed' });
        await page.fill('.dim-manual-input', '1');
        await page.press('.dim-manual-input', 'Enter');
        await page.click('#lab-generate');

        await expect.poll(() => page.evaluate(() => window.__labRequestCount)).toBe(0);
        await expect(page.locator('.openclaw-banner')).toContainText('invalid_payload');
        await expect(page.locator('.openclaw-banner')).not.toContainText('workflow-private-detail');
    });

    test('generates correct plan payload', async ({ page }) => {
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            window.__labSweepPayload = null;

            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    window.__labSweepPayload = JSON.parse(options?.body || '{}');
                    return {
                        ok: true,
                        status: 200,
                        data: {
                            plan: {
                                runs: [],
                                experiment_id: 'exp123'
                            }
                        }
                    };
                }
                return originalFetch(url, options);
            };
        });

        // Configure dimension
        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '20' }); // CheckpointLoader
        await page.selectOption('.dim-widget-select', { value: 'ckpt_name' });

        // Add value "v2.ckpt" via candidate
        await page.selectOption('.dim-candidate-select', { value: 'v2.ckpt' });

        // Add value "xl.ckpt" via candidate
        await page.selectOption('.dim-candidate-select', { value: 'xl.ckpt' });

        // Click Generate
        await page.click('#lab-generate');
        await expect
            .poll(() => page.evaluate(() => (window.__labSweepPayload ? 'ready' : 'pending')))
            .toBe('ready');

        // Verify payload
        const payload = await page.evaluate(() => window.__labSweepPayload);
        expect(payload).toBeTruthy();
        expect(payload.params).toHaveLength(1);
        expect(payload.params[0]).toEqual({
            node_id: 20,
            widget_name: 'ckpt_name',
            values: ['v2.ckpt', 'xl.ckpt'],
            strategy: 'grid'
        });
    });

    test('preserves non-numeric node ids through payload generation and replay apply', async ({ page }) => {
        await page.evaluate(() => {
            window.app.graph = {
                _nodes: [
                    {
                        id: "loader-alpha",
                        type: "CheckpointLoader",
                        title: "String Loader",
                        widgets: [
                            {
                                name: "ckpt_name",
                                type: "combo",
                                value: "base.ckpt",
                                options: { values: ["base.ckpt", "xl.ckpt"] }
                            }
                        ]
                    }
                ],
                getNodeById(id) {
                    return this._nodes.find((node) => String(node.id) === String(id));
                },
                serialize() { return { "string_graph": true }; }
            };
            window.confirm = () => true;
        });

        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            window.__labSweepPayload = null;

            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    const payload = JSON.parse(options?.body || '{}');
                    window.__labSweepPayload = payload;
                    return {
                        ok: true,
                        status: 200,
                        data: {
                            plan: {
                                experiment_id: 'exp_string_ids',
                                dimensions: payload.params,
                                runs: [
                                    { "loader-alpha.ckpt_name": "xl.ckpt" }
                                ]
                            }
                        }
                    };
                }
                return originalFetch(url, options);
            };
        });

        await page.click('#lab-add-dim');
        await expect(page.locator('.dim-node-select option[value="loader-alpha"]')).toHaveText('[loader-alpha] String Loader');
        await page.selectOption('.dim-node-select', { value: 'loader-alpha' });
        await page.selectOption('.dim-widget-select', { value: 'ckpt_name' });
        await page.selectOption('.dim-candidate-select', { value: 'xl.ckpt' });

        await page.click('#lab-generate');
        await expect
            .poll(() => page.evaluate(() => (window.__labSweepPayload ? 'ready' : 'pending')))
            .toBe('ready');

        const payload = await page.evaluate(() => window.__labSweepPayload);
        expect(payload.params[0]).toEqual({
            node_id: 'loader-alpha',
            widget_name: 'ckpt_name',
            values: ['xl.ckpt'],
            strategy: 'grid'
        });

        await page.click('.replay-run');
        await expect
            .poll(() =>
                page.evaluate(() =>
                    window.app.graph
                        .getNodeById('loader-alpha')
                        .widgets.find((widget) => widget.name === 'ckpt_name').value
                )
            )
            .toBe('xl.ckpt');
    });

    test('supports nested subgraph nodes and promoted widget candidates', async ({ page }) => {
        await page.evaluate(() => {
            const nestedLoader = {
                id: 7,
                type: "CheckpointLoaderSimple",
                title: "Nested Loader",
                widgets: [
                    {
                        name: "ckpt_name",
                        type: "combo",
                        value: "base.ckpt",
                        options: { values: ["base.ckpt", "xl.ckpt"] }
                    }
                ]
            };
            const subgraph = {
                _nodes: [nestedLoader],
                getNodeById(id) {
                    return this._nodes.find((node) => String(node.id) === String(id));
                }
            };

            window.app.graph = {
                _nodes: [
                    {
                        id: 50,
                        type: "SubgraphNode",
                        title: "Workflow Pack",
                        widgets: [
                            {
                                name: "ckpt_name",
                                type: "combo",
                                value: "base.ckpt",
                                options: {},
                                sourceNodeId: "7",
                                sourceWidgetName: "ckpt_name"
                            }
                        ],
                        subgraph
                    }
                ],
                getNodeById(id) {
                    return this._nodes.find((node) => String(node.id) === String(id));
                },
                serialize() { return { "nested_graph": true }; }
            };
        });

        await page.click('#lab-add-dim');
        await expect(page.locator('.dim-node-select option[value="50:7"]')).toHaveText('[50:7] Workflow Pack / Nested Loader');

        await page.selectOption('.dim-node-select', { value: '50' });
        await page.selectOption('.dim-widget-select', { value: 'ckpt_name' });

        const candidates = page.locator('.dim-candidate-select option');
        await expect(candidates).toHaveCount(3);
        await page.selectOption('.dim-candidate-select', { value: 'xl.ckpt' });
        await expect(page.locator('.openclaw-chip >> text=xl.ckpt')).toBeVisible();
    });

    test('uses an authoritative receipt when the host queue API returns only boolean', async ({ page }) => {
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            const { api } = await import('/scripts/api.js');
            const receiptMod = await import('/web/openclaw_parameter_lab_receipt.js');
            window.__labRunUpdates = [];
            window.__labQueueCalls = 0;
            window.__labSubmittedPromptIds = [];
            window.app.rootGraph = window.app.graph;
            window.app.processingQueue = false;
            window.app.queueItems = [];
            window.app.nextQueueRequestId = 1;
            window.app.graph.serialize = function () {
                const data = { nodes: [], extra: {} };
                this.onSerialize?.(data);
                return data;
            };
            window.app.queuePrompt = async function (number, batchCount = 1) {
                window.__labQueueCalls += 1;
                const requestId = this.nextQueueRequestId++;
                this.queueItems.push({ requestId, number, batchCount });
                api.dispatchCustomEvent('promptQueueing', { requestId, batchCount });
                if (this.processingQueue) return false;

                this.processingQueue = true;
                await Promise.resolve();
                try {
                    while (this.queueItems.length) {
                        const request = this.queueItems.pop();
                        let queuedCount = 0;
                        for (let index = 0; index < request.batchCount; index += 1) {
                            for (const node of this.graph._nodes) {
                                for (const widget of node.widgets || []) {
                                    widget.beforeQueued?.({ isPartialExecution: false });
                                }
                            }
                            const workflow = this.graph.serialize();
                            const marker =
                                workflow.extra?.[receiptMod.PARAMETER_LAB_RECEIPT_KEY];
                            if (!marker?.prompt_id) throw new Error('missing receipt marker');
                            delete workflow.extra[receiptMod.PARAMETER_LAB_RECEIPT_KEY];
                            window.__labSubmittedPromptIds.push(marker.prompt_id);
                            for (const node of this.graph._nodes) {
                                for (const widget of node.widgets || []) {
                                    widget.afterQueued?.({ isPartialExecution: false });
                                }
                            }
                            queuedCount += 1;
                        }
                        api.dispatchCustomEvent('promptQueued', {
                            requestId: request.requestId,
                            batchCount: queuedCount,
                            number: request.number
                        });
                    }
                } finally {
                    this.processingQueue = false;
                }
                return true;
            };

            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    return {
                        ok: true,
                        status: 200,
                        data: {
                            plan: {
                                experiment_id: 'exp_receipt',
                                dimensions: [
                                    {
                                        node_id: 10,
                                        widget_name: 'seed',
                                        values: [42],
                                        strategy: 'grid'
                                    }
                                ],
                                runs: [{ '10.seed': 42 }]
                            }
                        }
                    };
                }
                if (normalizedPath.includes('/lab/experiments/exp_receipt/runs/0')) {
                    window.__labRunUpdates.push(JSON.parse(options?.body || '{}'));
                    return { ok: true, status: 200, data: {} };
                }
                return originalFetch(url, options);
            };
        });

        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '10' });
        await page.selectOption('.dim-widget-select', { value: 'seed' });
        await page.fill('.dim-manual-input', '42');
        await page.press('.dim-manual-input', 'Enter');
        await page.click('#lab-generate');
        await expect(page.locator('#lab-run-all')).toBeVisible();
        await page.click('#lab-run-all');

        await expect.poll(() => page.evaluate(() => window.__labQueueCalls)).toBe(1);
        await expect(page.locator('.openclaw-lab-run-item .run-status')).toContainText('Queued');
        const updates = await page.evaluate(() => window.__labRunUpdates);
        expect(updates).toEqual([
            expect.objectContaining({
                status: 'queued',
                output: { prompt_id: expect.any(String) }
            })
        ]);
        const submitted = await page.evaluate(() => window.__labSubmittedPromptIds);
        expect(submitted).toHaveLength(1);
        expect(updates[0].output.prompt_id).toBe(submitted[0]);

        await page.evaluate(async (promptId) => {
            const { api } = await import('/scripts/api.js');
            api.dispatchCustomEvent('execution_start', { prompt_id: promptId });
        }, submitted[0]);
        await expect(page.locator('.openclaw-lab-run-item .run-status')).toHaveText('Running');
        await expect.poll(() => page.evaluate(() => window.__labRunUpdates)).toEqual([
            expect.objectContaining({ status: 'queued' }),
            { status: 'running' }
        ]);

        await page.evaluate(async (promptId) => {
            const { api } = await import('/scripts/api.js');
            api.dispatchCustomEvent('execution_success', {
                prompt_id: '00000000-0000-4000-8000-000000000000'
            });
            api.dispatchCustomEvent('execution_success', { prompt_id: promptId });
            api.dispatchCustomEvent('execution_error', { prompt_id: promptId });
        }, submitted[0]);
        await expect(page.locator('.openclaw-lab-run-item .run-status')).toHaveText('Completed');
        await expect.poll(() => page.evaluate(() => window.__labRunUpdates)).toEqual([
            expect.objectContaining({ status: 'queued' }),
            { status: 'running' },
            { status: 'completed' }
        ]);

        await expect(page.locator('.openclaw-banner')).toContainText(
            'All experiment runs finished.'
        );

        await page.evaluate(async () => {
            const { api } = await import('/scripts/api.js');
            const watchedEvents = new Set([
                'promptQueueing',
                'promptQueued',
                'execution_start',
                'execution_success',
                'execution_error',
                'execution_interrupted'
            ]);
            const widget = window.app.graph.getNodeById(10).widgets[0];
            const originalAddEventListener = api.addEventListener.bind(api);
            const originalRemoveEventListener = api.removeEventListener.bind(api);
            window.__labDisposeProbe = {
                listenerBalance: 0,
                originalBeforeQueued: widget.beforeQueued,
                originalAfterQueued: widget.afterQueued
            };
            api.addEventListener = (type, callback, options) => {
                if (watchedEvents.has(type)) {
                    window.__labDisposeProbe.listenerBalance += 1;
                }
                return originalAddEventListener(type, callback, options);
            };
            api.removeEventListener = (type, callback, options) => {
                if (watchedEvents.has(type)) {
                    window.__labDisposeProbe.listenerBalance -= 1;
                }
                return originalRemoveEventListener(type, callback, options);
            };
            window.app.processingQueue = false;
            window.app.queuePrompt = function (_number, batchCount = 1) {
                const requestId = this.nextQueueRequestId++;
                api.dispatchCustomEvent('promptQueueing', { requestId, batchCount });
                this.processingQueue = true;
                return new Promise(() => {});
            };
        });

        await page.click('#lab-run-all');
        await expect.poll(() => page.evaluate(() => {
            const widget = window.app.graph.getNodeById(10).widgets[0];
            const probe = window.__labDisposeProbe;
            return {
                callbacksWrapped:
                    widget.beforeQueued !== probe.originalBeforeQueued &&
                    widget.afterQueued !== probe.originalAfterQueued,
                listenerBalance: probe.listenerBalance
            };
        })).toEqual({ callbacksWrapped: true, listenerBalance: 6 });

        await clickTab(page, 'Settings');
        await expect.poll(() => page.evaluate(() => {
            const widget = window.app.graph.getNodeById(10).widgets[0];
            const probe = window.__labDisposeProbe;
            return {
                callbacksRestored:
                    widget.beforeQueued === probe.originalBeforeQueued &&
                    widget.afterQueued === probe.originalAfterQueued,
                listenerBalance: probe.listenerBalance,
                parameterPaneChildren:
                    document.querySelector('#openclaw-tab-parameter-lab')?.childElementCount
            };
        })).toEqual({
            callbacksRestored: true,
            listenerBalance: 0,
            parameterPaneChildren: 0
        });
    });
});


test.describe('Parameter Lab - Opaque Identities', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);
        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
        await clickTab(page, 'Parameter Lab');
    });

    for (const nested of [false, true]) {
        test(`selects, compares and replays ${nested ? 'nested' : 'root'} colon IDs`, async ({ page }) => {
            const executionId = nested ? 'pack:root:child:opaque' : 'insert:operation-1:root:node:7';
            await page.evaluate(async ({ nested, executionId }) => {
                const loader = { id: nested ? 'child:opaque' : executionId, type: 'CheckpointLoaderSimple', title: 'Opaque Loader',
                    widgets: [{ name: 'ckpt_name', type: 'combo', value: 'base.ckpt', options: { values: ['base.ckpt', 'xl.ckpt'] } }] };
                const direct = { _nodes: [loader] };
                window.app.graph = { _nodes: nested ? [{ id: 'pack:root', type: 'SubgraphNode', widgets: [], subgraph: direct }] : [loader],
                    serialize: () => ({ synthetic: true }), getNodeById(id) { return this._nodes.find((node) => String(node.id) === String(id)); } };
                window.__identityTarget = loader;
                window.confirm = () => true;
                const { openclawApi } = await import('/web/openclaw_api.js');
                const original = openclawApi.fetch.bind(openclawApi);
                openclawApi.fetch = async (url, options = {}) => {
                    if (String(url).endsWith('/lab/sweep')) {
                        const payload = JSON.parse(options.body);
                        window.__identityPayload = payload;
                        return { ok: true, data: { plan: { experiment_id: 'identity-plan', dimensions: payload.params, runs: [{ [`${executionId}.ckpt_name`]: 'xl.ckpt' }] } } };
                    }
                    return original(url, options);
                };
            }, { nested, executionId });
            await page.click('#lab-add-dim');
            await page.selectOption('.dim-node-select', executionId);
            await expect(page.locator('.dim-widget-select option[value="ckpt_name"]')).toHaveCount(1);
            await page.selectOption('.dim-widget-select', 'ckpt_name');
            await page.selectOption('.dim-candidate-select', 'xl.ckpt');
            await page.click('#lab-generate');
            await expect(page.locator('.replay-run')).toBeVisible();
            expect(await page.evaluate(() => window.__identityPayload.params[0])).toMatchObject({ node_id: executionId, widget_name: 'ckpt_name', values: ['xl.ckpt'] });
            await page.click('.replay-run');
            await expect.poll(() => page.evaluate(() => window.__identityTarget.widgets[0].value)).toBe('xl.ckpt');
            await expect(page.locator('.openclaw-banner')).toContainText('Values applied');
            await page.click('#lab-compare-models');
            await expect(page.locator('.dim-node-select')).toHaveValue(executionId);
            await expect(page.locator('.dim-widget-select')).toHaveValue('ckpt_name');
        });
    }

    for (const failure of ['ambiguous', 'missing', 'invalid']) {
        test(`rejects ${failure} replay before any override or direct lookup`, async ({ page }) => {
            await page.evaluate(async (failure) => {
                const make = (id) => ({ id, widgets: [{ name: 'seed', value: 1 }] });
                const control = make('control');
                const direct = make('a:b');
                const child = make('b');
                window.__identityNodes = [control, direct, child];
                window.__directLookups = 0;
                window.app.graph = { _nodes: [control, direct, { id: 'a', widgets: [], subgraph: { _nodes: [child] } }],
                    getNodeById(id) { window.__directLookups += 1; return this._nodes.find((node) => node.id === id); } };
                const bad = failure === 'ambiguous' ? 'a:b.seed' : failure === 'missing' ? 'absent.seed' : 'malformed';
                const { tabManager } = await import('/web/openclaw_tabs.js');
                const tab = tabManager.tabs.find((entry) => entry.id === 'parameter-lab');
                tab.experimentId = 'identity-reject';
                tab.plan = { dimensions: [], runs: [{ 'control.seed': 99, [bad]: 77 }] };
                tab.renderPlan();
                window.confirm = () => true;
            }, failure);
            await page.click('.replay-run');
            await expect(page.locator('.openclaw-banner')).toContainText('Cannot apply values');
            await expect(page.locator('.openclaw-banner')).not.toContainText('Values applied');
            expect(await page.evaluate(() => window.__identityNodes.map((node) => node.widgets[0].value))).toEqual([1, 1, 1]);
            expect(await page.evaluate(() => window.__directLookups)).toBe(0);
        });
    }
});
