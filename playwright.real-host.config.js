// @ts-check
// Configuration for the pinned real-host frontend compatibility smoke lane.
//
// This is deliberately a separate config from the mocked harness. The harness
// config points at `tests/e2e/specs` and starts its own static server; the
// real-host specs must never be collected by it, because there is no real host
// during repository acceptance and a spec that can only skip is not evidence.
//
// The host is already running when this config is used: `scripts/real_host_smoke.py`
// starts it, waits for its health route, and tears it down afterwards. There is
// no `webServer` block here on purpose, so a stray invocation cannot start one.
const { defineConfig } = require('@playwright/test');

const baseURL = process.env.OPENCLAW_REAL_HOST_BASE_URL;
if (!baseURL) {
  throw new Error(
    'OPENCLAW_REAL_HOST_BASE_URL is required; run this config through scripts/real_host_smoke.py',
  );
}

if (!/^http:\/\/(?:127\.0\.0\.1|\[::1\]):\d+$/.test(baseURL)) {
  // The lane binds loopback only. Refusing anything else here means a
  // misconfigured run fails instead of quietly pointing at another host.
  throw new Error(`OPENCLAW_REAL_HOST_BASE_URL must be a loopback origin, got ${baseURL}`);
}

module.exports = defineConfig({
  testDir: 'tests/real_host/specs',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '.tmp/real-host-smoke/report.json' }]],
  outputDir: '.tmp/real-host-smoke/artifacts',
  use: {
    baseURL,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
  },
});
