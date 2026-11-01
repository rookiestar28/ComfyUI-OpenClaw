import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ensureSupportedNode } from './check-node-runtime.mjs';

try {
  ensureSupportedNode();
} catch (error) {
  console.error(`[OpenClaw] ${error.message}`);
  process.exit(1);
}

function parsePositiveInt(value, label) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive integer, got '${value}'`);
  }
  return parsed;
}

function isWSL() {
  return process.platform === 'linux' && !!process.env.WSL_DISTRO_NAME;
}

function isDrvFsCwd() {
  const cwd = process.cwd();
  return cwd.startsWith('/mnt/');
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function resolvePlaywrightCli() {
  const candidates = [
    path.join(process.cwd(), 'node_modules', 'playwright', 'cli.js'),
    path.join(process.cwd(), 'node_modules', '@playwright', 'test', 'cli.js'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function runPlaywright(args, { label }) {
  const cli = resolvePlaywrightCli();
  if (!cli) {
    console.error(
      `[OpenClaw] Failed to run ${label}: Playwright CLI not found. Did you run 'npm ci'?`,
    );
    process.exit(1);
  }
  return spawnSync(process.execPath, [cli, ...args], { stdio: 'inherit', env });
}

function ensurePlaywrightBrowsersIfNeeded() {
  // In CI, ensure Playwright browsers are installed; otherwise tests fail with exit code 1.
  if (!process.env.CI && process.env.OPENCLAW_PLAYWRIGHT_INSTALL !== '1') {
    return;
  }
  // Default to Chromium only (fast + matches CI workflow); allow override.
  const browsers = (process.env.OPENCLAW_PLAYWRIGHT_BROWSERS || 'chromium')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  const args = ['install', ...browsers];
  if (process.platform === 'linux' && process.env.OPENCLAW_PLAYWRIGHT_WITH_DEPS === '1') {
    args.push('--with-deps');
  }
  const res = runPlaywright(args, { label: 'Playwright install' });
  if (res.error) {
    console.error('[OpenClaw] Failed to run Playwright install:', res.error);
    process.exit(1);
  }
  if (res.status !== 0) {
    console.error(`[OpenClaw] Playwright install failed with exit code ${res.status}`);
    process.exit(res.status === undefined || res.status === null ? 1 : res.status);
  }
}

const env = { ...process.env };
const rawArgs = process.argv.slice(2);

function hasArg(args, name) {
  return args.some((arg) => arg === name || arg.startsWith(`${name}=`));
}

function resolveStressMode(args) {
  return args.includes('--stress') || env.OPENCLAW_PLAYWRIGHT_STRESS === '1';
}

function resolveRepeatEach() {
  if (!env.OPENCLAW_PLAYWRIGHT_REPEAT_EACH) {
    return 5;
  }
  return parsePositiveInt(env.OPENCLAW_PLAYWRIGHT_REPEAT_EACH, 'OPENCLAW_PLAYWRIGHT_REPEAT_EACH');
}

function resolveStressWorkers() {
  if (env.OPENCLAW_PLAYWRIGHT_STRESS_WORKERS) {
    return parsePositiveInt(
      env.OPENCLAW_PLAYWRIGHT_STRESS_WORKERS,
      'OPENCLAW_PLAYWRIGHT_STRESS_WORKERS',
    );
  }
  if (isWSL() && isDrvFsCwd()) {
    return 1;
  }
  return 2;
}

function buildPlaywrightArgs(args) {
  const scriptArgs = args.filter((arg) => arg !== '--stress');
  const playwrightArgs = ['test'];
  const stressMode = resolveStressMode(args);

  if (stressMode) {
    if (!hasArg(scriptArgs, '--repeat-each')) {
      playwrightArgs.push('--repeat-each', String(resolveRepeatEach()));
    }
    if (!hasArg(scriptArgs, '--workers')) {
      playwrightArgs.push('--workers', String(resolveStressWorkers()));
    }
    if (!hasArg(scriptArgs, '--reporter')) {
      playwrightArgs.push('--reporter', env.OPENCLAW_PLAYWRIGHT_STRESS_REPORTER || 'line');
    }
  }

  return [...playwrightArgs, ...scriptArgs];
}

if (isWSL() && isDrvFsCwd()) {
  const tmpDir = path.join(process.cwd(), '.tmp', 'playwright');
  ensureDir(tmpDir);
  env.TMPDIR = tmpDir;
  env.TMP = tmpDir;
  env.TEMP = tmpDir;
}

ensurePlaywrightBrowsersIfNeeded();

let res = runPlaywright(buildPlaywrightArgs(rawArgs), { label: 'Playwright' });
if (res.error) {
  console.error('[OpenClaw] Failed to run Playwright:', res.error);
  process.exit(1);
}
process.exit((res.status === undefined || res.status === null) ? 1 : res.status);
