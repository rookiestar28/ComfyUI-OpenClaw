// @ts-check
const { defineConfig } = require('@playwright/test');

const e2ePort = process.env.OPENCLAW_E2E_PORT || '3000';
const e2eBase = `http://127.0.0.1:${e2ePort}`;

function isWslDrvFs() {
  return process.platform === 'linux' && !!process.env.WSL_DISTRO_NAME && process.cwd().startsWith('/mnt/');
}

function resolveWorkers() {
  const raw = process.env.OPENCLAW_PLAYWRIGHT_WORKERS;
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isInteger(parsed) || parsed < 1) {
      throw new Error(
        `OPENCLAW_PLAYWRIGHT_WORKERS must be a positive integer, got '${raw}'`,
      );
    }
    return parsed;
  }

  // CRITICAL: WSL on /mnt/* has repeatable E2E harness instability under
  // high parallelism; cap workers to keep the repo acceptance gate deterministic.
  if (isWslDrvFs()) {
    return 1;
  }

  return undefined;
}

function resolveTimeoutMs() {
  const raw = process.env.OPENCLAW_PLAYWRIGHT_TIMEOUT_MS;
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isInteger(parsed) || parsed < 1) {
      throw new Error(
        `OPENCLAW_PLAYWRIGHT_TIMEOUT_MS must be a positive integer, got '${raw}'`,
      );
    }
    return parsed;
  }

  // IMPORTANT: WSL on /mnt/* can push single-test wall time well past 30s even
  // with one worker, especially when a spec reloads the harness inside one test.
  if (isWslDrvFs()) {
    return 60_000;
  }

  return 30_000;
}

const timeoutMs = resolveTimeoutMs();

module.exports = defineConfig({
  testDir: 'tests/e2e/specs',
  timeout: timeoutMs,
  retries: 0,
  workers: resolveWorkers(),
  use: {
    baseURL: `${e2eBase}/tests/e2e/`,
    headless: true,
  },
  webServer: {
    // IMPORTANT: allow overriding port for environments where 3000 is blocked/reserved.
    // CRITICAL: do not go back to `python -m http.server` (backlog 5, all interfaces). With the
    // default worker count on a high-core Windows host the suite then fails with
    // net::ERR_CONNECTION_REFUSED; scripts/e2e_static_server.py keeps a large accept backlog
    // and binds 127.0.0.1 only.
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')} scripts/e2e_static_server.py ${e2ePort}`,
    url: `${e2eBase}/tests/e2e/test-harness.html`,
    reuseExistingServer: true,
    timeout: timeoutMs,
  },
});
