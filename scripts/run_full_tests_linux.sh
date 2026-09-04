#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

trap 'echo "[tests] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

echo "[tests] repo: $ROOT_DIR"

# Cache isolation for pre-commit + black
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
mkdir -p "$PRE_COMMIT_HOME" "$BLACK_CACHE_DIR"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[tests] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

is_wsl() {
  grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

select_venv_dir() {
  # Explicit override for advanced/local setups.
  if [ -n "${OPENCLAW_TEST_VENV:-}" ]; then
    echo "$OPENCLAW_TEST_VENV"
    return 0
  fi
  # IMPORTANT:
  # In WSL, prefer dedicated Linux venv to avoid clashing with Windows .venv.
  if is_wsl; then
    echo "$ROOT_DIR/.venv-wsl"
  else
    echo "$ROOT_DIR/.venv"
  fi
}

pip_install_or_fail() {
  local why="$1"
  shift
  if "$VENV_PY" -m pip install "$@"; then
    return 0
  fi
  echo "[tests] ERROR: failed to install dependency ($why): $*" >&2
  echo "[tests] HINT: check internet/proxy, then retry the script." >&2
  echo "[tests] HINT: if offline, pre-install into venv manually: $VENV_PY -m pip install $*" >&2
  exit 1
}

capture_precommit_snapshots() {
  # IMPORTANT: compare both worktree and index; pre-commit can mutate staged files while exiting 0.
  PRECOMMIT_WORKTREE_SNAPSHOT="$(mktemp)"
  PRECOMMIT_INDEX_SNAPSHOT="$(mktemp)"
  git diff --binary -- . >"$PRECOMMIT_WORKTREE_SNAPSHOT"
  git diff --cached --binary -- . >"$PRECOMMIT_INDEX_SNAPSHOT"
}

cleanup_precommit_snapshots() {
  rm -f "${PRECOMMIT_WORKTREE_SNAPSHOT:-}" "${PRECOMMIT_INDEX_SNAPSHOT:-}" \
    "${PRECOMMIT_WORKTREE_SNAPSHOT_AFTER:-}" "${PRECOMMIT_INDEX_SNAPSHOT_AFTER:-}"
}

precommit_changed_repo_state() {
  PRECOMMIT_WORKTREE_SNAPSHOT_AFTER="$(mktemp)"
  PRECOMMIT_INDEX_SNAPSHOT_AFTER="$(mktemp)"
  git diff --binary -- . >"$PRECOMMIT_WORKTREE_SNAPSHOT_AFTER"
  git diff --cached --binary -- . >"$PRECOMMIT_INDEX_SNAPSHOT_AFTER"
  ! cmp -s "$PRECOMMIT_WORKTREE_SNAPSHOT" "$PRECOMMIT_WORKTREE_SNAPSHOT_AFTER" || \
    ! cmp -s "$PRECOMMIT_INDEX_SNAPSHOT" "$PRECOMMIT_INDEX_SNAPSHOT_AFTER"
}

report_precommit_repo_drift_and_exit() {
  echo "[tests] ERROR: pre-commit hooks modified tracked files (worktree or index)." >&2
  echo "[tests] Review/stage the hook changes, then rerun the acceptance gate." >&2
  git status --short
  cleanup_precommit_snapshots
  exit 1
}

assert_clean_public_worktree() {
  # CRITICAL: tracked diff snapshots do not include untracked deliverables. A full
  # acceptance gate must bind to one clean committed candidate, not local-only files.
  local status
  if ! status="$(git status --porcelain --untracked-files=all)"; then
    echo "[tests] ERROR: unable to inspect Git worktree state" >&2
    exit 1
  fi
  if [ -n "$status" ]; then
    echo "[tests] ERROR: acceptance requires a clean committed candidate." >&2
    echo "[tests] Commit or remove public changes, then rerun." >&2
    printf '%s\n' "$status" >&2
    exit 1
  fi
}

assert_clean_public_worktree

require_cmd node
require_cmd npm

ensure_npm_deps() {
  # IMPORTANT: acceptance must reconcile the complete lockfile; file-presence
  # shortcuts can silently reuse an invalid or stale development dependency tree.
  echo "[tests] Reconciling frontend dependencies via npm ci ..."
  npm ci
}

# Always use project-local venv to avoid global interpreter / tool drift.
VENV_DIR="$(select_venv_dir)"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[tests] Creating project venv at $VENV_DIR ..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV_DIR"
  elif command -v python >/dev/null 2>&1; then
    python -m venv "$VENV_DIR"
  else
    echo "[tests] ERROR: no bootstrap Python found (need python3 or python)" >&2
    exit 1
  fi
fi

if ! "$VENV_PY" -m pre_commit --version >/dev/null 2>&1; then
  echo "[tests] Installing pre-commit into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for detect-secrets and hook validation" -U pip pre-commit
fi

if ! "$VENV_PY" -c "import aiohttp" >/dev/null 2>&1; then
  echo "[tests] Installing aiohttp into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required by import paths used in unit tests" aiohttp
fi

if ! "$VENV_PY" -c "import cryptography" >/dev/null 2>&1; then
  echo "[tests] Installing cryptography into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for S57 secrets-at-rest encryption tests" cryptography
fi

if ! "$VENV_PY" -c "import json, sys; from importlib.metadata import version; p=json.load(open('tests/static_analysis_policy.json', encoding='utf-8')); sys.exit(0 if all(version(name)==cfg['version'] for name,cfg in p['tools'].items()) else 1)" >/dev/null 2>&1; then
  echo "[tests] Installing pinned Ruff/Mypy into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for static-analysis policy" -r requirements-quality.txt
fi

if ! "$VENV_PY" -c "import defusedxml" >/dev/null 2>&1; then
  # IMPORTANT: keep local full-test bootstrap aligned with requirements.txt.
  echo "[tests] Installing defusedxml into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for S85 fail-closed XML parsing tests" defusedxml
fi

NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  # Best-effort: try to use nvm if available
  if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR}/nvm.sh"
  elif [ -s "${HOME}/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nvm/nvm.sh"
  fi
  if command -v nvm >/dev/null 2>&1; then
    nvm use 18 >/dev/null 2>&1 || true
  fi
  NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
fi

if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[tests] ERROR: Node >=18 required, current=$(node -v)" >&2
  echo "[tests] Hint: source ~/.nvm/nvm.sh && nvm use 18" >&2
  exit 1
fi

can_bind_local_port() {
  local port="$1"
  node -e "const net=require('net'); const port=Number(process.argv[1]); const server=net.createServer(); server.unref(); server.once('error', ()=>process.exit(1)); server.listen({host:'127.0.0.1', port, exclusive:true}, ()=>server.close(()=>process.exit(0)));" "$port" >/dev/null 2>&1
}

resolve_e2e_port() {
  if [ -n "${OPENCLAW_E2E_PORT:-}" ]; then
    if ! can_bind_local_port "$OPENCLAW_E2E_PORT"; then
      echo "[tests] ERROR: OPENCLAW_E2E_PORT=$OPENCLAW_E2E_PORT is not bindable on 127.0.0.1." >&2
      echo "[tests] Hint: unset OPENCLAW_E2E_PORT or set another port (example: 3300)." >&2
      exit 1
    fi
    echo "$OPENCLAW_E2E_PORT"
    return 0
  fi

  local candidate
  for candidate in 3000 3300 3400 3500 3600; do
    if can_bind_local_port "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  echo "[tests] ERROR: no bindable local port found for Playwright webServer (tried 3000,3300,3400,3500,3600)." >&2
  exit 1
}

E2E_PORT="$(resolve_e2e_port)"
export OPENCLAW_E2E_PORT="$E2E_PORT"
if [ "$E2E_PORT" != "3000" ]; then
  echo "[tests] WARN: port 3000 unavailable; using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
else
  echo "[tests] INFO: using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
fi

echo "[tests] Node version: $(node -v)"

HYGIENE_SNAPSHOT="$ROOT_DIR/.tmp/workspace-hygiene-full.json"
"$VENV_PY" scripts/check_workspace_hygiene.py snapshot --root "$ROOT_DIR" --snapshot "$HYGIENE_SNAPSHOT"

echo "[tests] 0/11 supply-chain hardening check"
"$VENV_PY" scripts/check_supply_chain_hardening.py

ensure_npm_deps

echo "[tests] 0.25/11 frontend dependency audit"
npm audit --audit-level=high

echo "[tests] 0.5/11 static analysis policy"
"$VENV_PY" scripts/verify_static_analysis_policy.py

echo "[tests] 0/9 R120 dependency preflight"
"$VENV_PY" scripts/preflight_check.py --strict

echo "[tests] 1/9 detect-secrets"
"$VENV_PY" -m pre_commit run detect-secrets --all-files

echo "[tests] 2/9 pre-commit all hooks (pass 1: autofix)"
capture_precommit_snapshots
if "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure; then
  :
else
  echo "[tests] INFO: pre-commit reported changes/issues; running pass 2 verification..."
  "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure
fi
if precommit_changed_repo_state; then
  report_precommit_repo_drift_and_exit
fi
cleanup_precommit_snapshots

echo "[tests] 3/10 coverage governance check"
"$VENV_PY" scripts/verify_quality_governance.py

echo "[tests] 4/10 test debt governance check"
"$VENV_PY" scripts/verify_test_debt_governance.py

echo "[tests] 5/10 backend unit tests"
export MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/full/default"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/full/unit" "$VENV_PY" scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json

if [ -n "${OPENCLAW_IMPL_RECORD_PATH:-}" ]; then
  echo "[tests] 5.5/10 implementation record lint (strict)"
  # IMPORTANT: strict mode is opt-in via OPENCLAW_IMPL_RECORD_PATH to avoid retroactive legacy record failures.
  "$VENV_PY" scripts/lint_implementation_record.py --path "$OPENCLAW_IMPL_RECORD_PATH" --strict
fi

echo "[tests] 6/10 backend real E2E lanes (R122/R123)"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/full/backend-e2e-real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/full/backend-e2e-real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 7/10 R121 retry partition contract"
"$VENV_PY" scripts/run_unittests.py --module tests.test_r121_retry_partition_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 8/10 Slack integration gates (R124/R125/R117/F57)"
"$VENV_PY" scripts/run_unittests.py --module tests.test_r124_slack_ingress_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r125_slack_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r117_observability_redaction_e2e --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r117_observability_redaction_endpoints --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_f57_slack_transport_parity --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_f57_slack_socket_mode_startup --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 9/10 R118 adversarial gate (adaptive: smoke/extended)"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/full/adversarial" \
  "$VENV_PY" scripts/run_adversarial_gate.py --profile auto --seed 42 --artifact-dir .tmp/adversarial

echo "[tests] 10/10 frontend E2E"
# IMPORTANT: full-gate acceptance must provision Playwright browsers itself; do
# not assume a warmed local browser cache when running on fresh WSL/Linux hosts.
OPENCLAW_PLAYWRIGHT_INSTALL=1 OPENCLAW_PLAYWRIGHT_BROWSERS=chromium npm test

"$VENV_PY" scripts/check_workspace_hygiene.py check --root "$ROOT_DIR" --snapshot "$HYGIENE_SNAPSHOT"
assert_clean_public_worktree
echo "[tests] PASS"
