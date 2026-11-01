import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REMEDIATION = 'Select a stable Node 24 runtime with your version manager, verify node -v, then retry.';

function parseVersion(value) {
  const match = typeof value === 'string' && /^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(value);
  if (!match) return null;
  const parts = match.slice(1).map(Number);
  return parts.every(Number.isSafeInteger) ? parts : null;
}

function compare(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index] ? 1 : -1;
  }
  return 0;
}

function parsePolicy(policy) {
  if (typeof policy !== 'string' || policy.length > 256 || !policy.trim()) {
    throw new Error(`Invalid package.json engines.node policy. ${REMEDIATION}`);
  }
  return policy.split('||').map((branch) => {
    const match = /^(\^|>=)(\d+\.\d+\.\d+)$/.exec(branch.trim());
    const minimum = match && parseVersion(match[2]);
    if (!minimum || (match[1] === '^' && minimum[0] === 0)) {
      throw new Error(`Unsupported package.json engines.node policy format. ${REMEDIATION}`);
    }
    return { operator: match[1], minimum };
  });
}

export function readNodePolicy() {
  return JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).engines?.node;
}

export function isSupportedNodeVersion(version, policy = readNodePolicy()) {
  const branches = parsePolicy(policy);
  const parsed = parseVersion(version);
  if (!parsed) return false;
  // IMPORTANT: the locked Vite/Vitest intersection excludes older minors and
  // entire major lines. A major-only floor silently accepts incompatible tools.
  return branches.some(({ operator, minimum }) => compare(parsed, minimum) >= 0 &&
    (operator === '>=' || parsed[0] === minimum[0]));
}

export function ensureSupportedNode(version = process.versions.node, policy = readNodePolicy()) {
  if (!isSupportedNodeVersion(version, policy)) {
    throw new Error(`Unsupported Node ${version || '(unknown)'}. Required: ${policy}. ${REMEDIATION}`);
  }
  return version;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const version = ensureSupportedNode();
    console.log(`[OpenClaw] Node ${version}; supported policy: ${readNodePolicy()}`);
  } catch (error) {
    console.error(`[OpenClaw] ${error.message}`);
    process.exitCode = 1;
  }
}
