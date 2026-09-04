#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

trap 'echo "[pre-push] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[pre-push] repo: $ROOT_DIR"

# Pre-commit cache strategy:
# - Always use repo-local cache to avoid readonly / locked user-level caches.
# - Windows is especially prone to cache lock issues (WinError 5), so we keep
#   the cache local and aggressively reset it when manifest/permission errors occur.
UNAME_S="$(uname -s || true)"
case "$UNAME_S" in
  MINGW*|MSYS*|CYGWIN*)
    # CRITICAL (Windows): do NOT use the user-level ~/.cache/pre-commit path.
    # It frequently leaves locked .exe files (WinError 5) and blocks hook cleanup.
    export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit-win}"
    mkdir -p "$PRE_COMMIT_HOME"
    # Keep Black cache local to avoid AppData lock/permission errors.
    export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
    mkdir -p "$BLACK_CACHE_DIR"
    ;;
  *)
    export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
    mkdir -p "$PRE_COMMIT_HOME"
    export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
    mkdir -p "$BLACK_CACHE_DIR"
    ;;
esac

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[pre-push] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

pip_install_or_fail() {
  local why="$1"
  shift
  if "$VENV_PY" -m pip install "$@"; then
    return 0
  fi
  echo "[pre-push] ERROR: failed to install dependency ($why): $*" >&2
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
  echo "[pre-push] ERROR: pre-commit hooks modified tracked files (worktree or index)." >&2
  echo "[pre-push] Please review/stage the hook fixes, then push again." >&2
  git status --short
  cleanup_precommit_snapshots
  exit 1
}

assert_clean_public_worktree() {
  # CRITICAL: tracked diff snapshots do not include untracked deliverables. A push
  # gate must validate one clean committed candidate, not local-only files.
  local status
  if ! status="$(git status --porcelain --untracked-files=all)"; then
    echo "[pre-push] ERROR: unable to inspect Git worktree state" >&2
    exit 1
  fi
  if [ -n "$status" ]; then
    echo "[pre-push] ERROR: validation requires a clean committed candidate." >&2
    echo "[pre-push] Commit or remove public changes, then retry." >&2
    printf '%s\n' "$status" >&2
    exit 1
  fi
}

assert_clean_public_worktree

is_wsl() {
  grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

select_venv_dir() {
  # Explicit override for advanced/local setups.
  if [ -n "${OPENCLAW_TEST_VENV:-}" ]; then
    echo "$OPENCLAW_TEST_VENV"
    return 0
  fi

  case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*)
      echo "$ROOT_DIR/.venv"
      ;;
    *)
      # IMPORTANT:
      # In WSL, prefer dedicated Linux venv to avoid mixing with Windows .venv.
      if is_wsl; then
        echo "$ROOT_DIR/.venv-wsl"
      else
        echo "$ROOT_DIR/.venv"
      fi
      ;;
  esac
}

resolve_venv_python() {
  case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*)
      echo "$VENV_DIR/Scripts/python.exe"
      ;;
    *)
      echo "$VENV_DIR/bin/python"
      ;;
  esac
}

is_venv_python_healthy() {
  local venv_py="$1"
  # CRITICAL: on Git Bash/Windows, `test -x` is unreliable for `.exe`.
  # Use existence + actual interpreter execution probe instead.
  # DO NOT replace with `-x` checks; that regression previously caused false
  # "invalid venv" detection and fallback to the wrong interpreter.
  [ -f "$venv_py" ] || return 1
  "$venv_py" -c "import sys; print(sys.executable)" >/dev/null 2>&1
}

bootstrap_venv() {
  local venv_py
  venv_py="$(resolve_venv_python)"
  if is_venv_python_healthy "$venv_py"; then
    echo "$venv_py"
    return 0
  fi

  if [ -e "$venv_py" ]; then
    echo "[pre-push] WARN: existing venv is invalid; recreating: $VENV_DIR" >&2
    rm -rf "$VENV_DIR"
  fi

  echo "[pre-push] INFO: creating project venv at $VENV_DIR ..." >&2
  case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*)
      # CRITICAL: on Git Bash, `python3` may resolve to MSYS `/usr/bin/python`,
      # which creates a broken Windows venv (`No Python at "/usr/bin\python.exe"`).
      # Always prefer Windows-native launchers/interpreters.
      if command -v py.exe >/dev/null 2>&1; then
        py.exe -3 -m venv "$VENV_DIR"
      elif [ -x "/c/Windows/py.exe" ]; then
        /c/Windows/py.exe -3 -m venv "$VENV_DIR"
      elif command -v python.exe >/dev/null 2>&1; then
        python.exe -m venv "$VENV_DIR"
      elif command -v py >/dev/null 2>&1; then
        py -3 -m venv "$VENV_DIR"
      else
        echo "[pre-push] ERROR: no Windows Python launcher found (py.exe/python.exe)." >&2
        exit 1
      fi
      ;;
    *)
      if command -v python3 >/dev/null 2>&1; then
        python3 -m venv "$VENV_DIR"
      elif command -v python >/dev/null 2>&1; then
        python -m venv "$VENV_DIR"
      else
        echo "[pre-push] ERROR: no bootstrap Python found (python3/python)." >&2
        exit 1
      fi
      ;;
  esac

  if ! is_venv_python_healthy "$venv_py"; then
    echo "[pre-push] ERROR: failed to initialize project venv: $VENV_DIR" >&2
    exit 1
  fi
  echo "$venv_py"
}

pre_commit_cmd() {
  "$VENV_PY" -m pre_commit "$@"
}

# CRITICAL: pre-push must always run pre-commit from project venv.
# Do not switch this back to global `pre-commit` command lookup.
# This prevents mixed global/user installs from hijacking hook execution.
VENV_DIR="$(select_venv_dir)"
VENV_PY="$(bootstrap_venv)"
if ! "$VENV_PY" -m pre_commit --version >/dev/null 2>&1; then
  echo "[pre-push] INFO: installing pre-commit into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required for pre-commit hooks" -U pip pre-commit
fi
if ! "$VENV_PY" -c "import black" >/dev/null 2>&1; then
  # Keep black in the same interpreter used by local black-single hook.
  echo "[pre-push] INFO: installing black into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required by black-single hook" black==24.1.1
fi

# IMPORTANT:
# Pre-push now runs backend unit tests. Keep minimal runtime deps aligned with
# CI unit-test job to avoid "passes locally, fails on GitHub" drift.
if ! "$VENV_PY" -c "import numpy, PIL" >/dev/null 2>&1; then
  echo "[pre-push] INFO: installing numpy/pillow into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required by unit tests" numpy pillow
fi
if ! "$VENV_PY" -c "import aiohttp" >/dev/null 2>&1; then
  echo "[pre-push] INFO: installing aiohttp into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required by unit tests/import paths" aiohttp
fi
if ! "$VENV_PY" -c "import cryptography" >/dev/null 2>&1; then
  echo "[pre-push] INFO: installing cryptography into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required for S57 secrets-at-rest encryption paths/tests" cryptography
fi
if ! "$VENV_PY" -c "import defusedxml" >/dev/null 2>&1; then
  # IMPORTANT: keep local/CI parity with requirements.txt and WeChat ingress tests.
  echo "[pre-push] INFO: installing defusedxml into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required for S85 fail-closed XML parsing paths/tests" defusedxml
fi
if ! "$VENV_PY" -c "import sys, importlib.util; has_coverage = importlib.util.find_spec('coverage') is not None; has_toml = sys.version_info >= (3, 11) or importlib.util.find_spec('tomli') is not None; sys.exit(0 if has_coverage and has_toml else 1)" >/dev/null 2>&1; then
  # CRITICAL: Python 3.10 coverage cannot read pyproject.toml without TOML
  # support; plain coverage here causes local pass/CI fail drift.
  echo "[pre-push] INFO: installing coverage[toml] into project venv ($VENV_DIR) ..." >&2
  pip_install_or_fail "required for backend coverage gate" "coverage[toml]"
fi

require_cmd npm

run_pre_commit_safe() {
  local tmp_log
  tmp_log="$(mktemp)"
  local lower_log
  lower_log="$(mktemp)"

  reset_cache() {
    echo "[pre-push] WARN: resetting pre-commit cache: ${PRE_COMMIT_HOME:-<unset>}" >&2
    if [ -n "${PRE_COMMIT_HOME:-}" ] && [ -d "$PRE_COMMIT_HOME" ]; then
      rm -rf "$PRE_COMMIT_HOME" 2>/dev/null || true

      # On Git for Windows, rm may fail on locked files. Retry via cmd.exe.
      if [ -d "$PRE_COMMIT_HOME" ] && command -v cygpath >/dev/null 2>&1 && command -v cmd.exe >/dev/null 2>&1; then
        local pre_commit_home_win
        pre_commit_home_win="$(cygpath -w "$PRE_COMMIT_HOME")"
        cmd.exe /c "rmdir /s /q \"$pre_commit_home_win\"" >/dev/null 2>&1 || true
      fi

      mkdir -p "$PRE_COMMIT_HOME"
    fi
  }

  if pre_commit_cmd "$@" 2>&1 | tee "$tmp_log"; then
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  tr '[:upper:]' '[:lower:]' < "$tmp_log" > "$lower_log" || true

  if grep -q "invalidmanifesterror" "$lower_log"; then
    echo "[pre-push] WARN: pre-commit cache manifest is corrupted; running clean + cache reset + single retry." >&2
    if ! pre_commit_cmd clean; then
      echo "[pre-push] WARN: 'pre-commit clean' failed; trying manual cache reset." >&2
      reset_cache
    fi
    reset_cache
    pre_commit_cmd "$@"
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  if grep -q "permissionerror" "$lower_log" || grep -q "winerror 5" "$lower_log" || grep -q "access is denied" "$lower_log"; then
    # CRITICAL: treat lock-file errors as cache corruption and self-heal once.
    # Avoid removing this block; without it, pre-push can hang/fail repeatedly on Windows.
    echo "[pre-push] WARN: pre-commit cache appears locked by another process; running cache reset + single retry." >&2
    reset_cache
    if [ -n "${BLACK_CACHE_DIR:-}" ] && [ -d "$BLACK_CACHE_DIR" ]; then
      rm -rf "$BLACK_CACHE_DIR" 2>/dev/null || true
      mkdir -p "$BLACK_CACHE_DIR"
    fi
    pre_commit_cmd "$@"
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  rm -f "$tmp_log"
  rm -f "$lower_log"
  return 1
}

# Ensure Node 18+ for Playwright/E2E.
# CI uses Node 20; local baseline is Node 18.
if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
  # shellcheck disable=SC1090
  . "${NVM_DIR}/nvm.sh"
elif [ -s "${HOME}/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "${HOME}/.nvm/nvm.sh"
fi

if command -v nvm >/dev/null 2>&1; then
  if [ -f ".nvmrc" ]; then
    if ! nvm use >/dev/null 2>&1; then
      echo "[pre-push] WARN: nvm use (.nvmrc) failed; using current node in PATH." >&2
    fi
  else
    if ! nvm use 18 >/dev/null 2>&1; then
      echo "[pre-push] WARN: nvm use 18 failed; using current node in PATH." >&2
    fi
  fi
fi

require_cmd node
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[pre-push] ERROR: Node >=18 required, current=$(node -v)" >&2
  echo "[pre-push] Hint: install nvm and run 'nvm use 18'." >&2
  exit 1
fi

can_bind_local_port() {
  local port="$1"
  node -e "const net=require('net'); const port=Number(process.argv[1]); const server=net.createServer(); server.unref(); server.once('error', ()=>process.exit(1)); server.listen({host:'127.0.0.1', port, exclusive:true}, ()=>server.close(()=>process.exit(0)));" "$port" >/dev/null 2>&1
}

resolve_e2e_port() {
  if [ -n "${OPENCLAW_E2E_PORT:-}" ]; then
    if ! can_bind_local_port "$OPENCLAW_E2E_PORT"; then
      echo "[pre-push] ERROR: OPENCLAW_E2E_PORT=$OPENCLAW_E2E_PORT is not bindable on 127.0.0.1." >&2
      echo "[pre-push] Hint: unset OPENCLAW_E2E_PORT or set another port (example: 3300)." >&2
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

  echo "[pre-push] ERROR: no bindable local port found for Playwright webServer (tried 3000,3300,3400,3500,3600)." >&2
  exit 1
}

E2E_PORT="$(resolve_e2e_port)"
export OPENCLAW_E2E_PORT="$E2E_PORT"
if [ "$E2E_PORT" != "3000" ]; then
  echo "[pre-push] WARN: port 3000 unavailable; using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
else
  echo "[pre-push] INFO: using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
fi

echo "[pre-push] Node version: $(node -v)"
HYGIENE_SNAPSHOT="$ROOT_DIR/.tmp/workspace-hygiene-pre-push.json"
"$VENV_PY" scripts/check_workspace_hygiene.py snapshot --root "$ROOT_DIR" --snapshot "$HYGIENE_SNAPSHOT"
echo "[pre-push] 0/10 supply-chain hardening check"
"$VENV_PY" scripts/check_supply_chain_hardening.py
echo "[pre-push] 0.25/10 frontend dependency install and audit"
# IMPORTANT: never accept a warmed or manually changed node_modules tree.
npm ci
npm audit --audit-level=high
echo "[pre-push] 0/7 R120 dependency preflight"
"$VENV_PY" scripts/preflight_check.py --strict
echo "[pre-push] 1/7 detect-secrets"
run_pre_commit_safe run detect-secrets --all-files

echo "[pre-push] 2/7 pre-commit all hooks (pass 1)"
capture_precommit_snapshots
if run_pre_commit_safe run --all-files --show-diff-on-failure; then
  :
else
  echo "[pre-push] INFO: pre-commit reported changes/issues; running pass 2 verification..." >&2
  if run_pre_commit_safe run --all-files --show-diff-on-failure; then
    if ! git diff --quiet -- .; then
      echo "[pre-push] ERROR: hooks auto-fixed files during pre-push." >&2
      echo "[pre-push] Please review, commit the fixes, then push again." >&2
      git status --short
      exit 1
    fi
    echo "[pre-push] WARN: first pass failed but second pass succeeded without local file changes." >&2
  else
    exit 1
  fi
fi
if precommit_changed_repo_state; then
  report_precommit_repo_drift_and_exit
fi
cleanup_precommit_snapshots

# IMPORTANT: generated spec drift must fail before backend tests so docs-only
# edits cannot hide until deep in the pre-push unit suite.
"$VENV_PY" scripts/check_openapi_sync.py

echo "[pre-push] 3/9 coverage governance check"
"$VENV_PY" scripts/verify_quality_governance.py

echo "[pre-push] 4/9 test debt governance check"
"$VENV_PY" scripts/verify_test_debt_governance.py

echo "[pre-push] 5/9 backend unit tests"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/pre-push/unit" \
  "$VENV_PY" scripts/run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json --coverage-json .tmp/coverage/backend_unit_coverage.json

echo "[pre-push] 5.1/9 backend coverage hotspot report"
"$VENV_PY" scripts/report_coverage_governance.py --coverage-json .tmp/coverage/backend_unit_coverage.json

if [ -n "${OPENCLAW_IMPL_RECORD_PATH:-}" ]; then
  echo "[pre-push] 5.5/9 implementation record lint (strict)"
  "$VENV_PY" scripts/lint_implementation_record.py --path "$OPENCLAW_IMPL_RECORD_PATH" --strict
fi

echo "[pre-push] 6/9 backend real E2E lanes (R122/R123)"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/pre-push/backend-e2e-real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/pre-push/backend-e2e-real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[pre-push] 7/9 R121 retry partition contract"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/pre-push/retry-partition" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r121_retry_partition_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[pre-push] 8/9 R118 adversarial gate (adaptive: smoke/extended)"
MOLTBOT_STATE_DIR="$ROOT_DIR/.tmp/test-state/pre-push/adversarial" \
  "$VENV_PY" scripts/run_adversarial_gate.py --profile auto --seed 42 --artifact-dir .tmp/adversarial

echo "[pre-push] 9/9 npm test (Playwright)"
npm test

"$VENV_PY" scripts/check_workspace_hygiene.py check --root "$ROOT_DIR" --snapshot "$HYGIENE_SNAPSHOT"
assert_clean_public_worktree
echo "[pre-push] PASS"
