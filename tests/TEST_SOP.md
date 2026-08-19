# Test SOP

<!-- CURRENT-TEST-GOVERNANCE:START -->
## Current Change-Governance Authority

This section supersedes narrower documentation exceptions, platform-selection rules, and Hosted CI
acceptance wording elsewhere in this file.

- Pure text/documentation changes and version-field-only `pyproject.toml` updates do not require
  planning, a roadmap item, records/logs, independent review, a documentation test contract, E2E,
  or the full repository gate. Use proportionate text/TOML/metadata checks only when useful.
- Dependency, build, tool-configuration, entry-point, runtime-compatibility, or other
  behavior-bearing `pyproject.toml` changes are not version-only fast-path changes.
- For non-exempt work, a passing Windows Full Gate is the authoritative repository-wide test
  result. Neither push nor Hosted CI is required, and acceptance evidence does not need to bind to a
  pushed commit. Linux/WSL and Hosted CI runs are optional diagnostics unless the current item
  explicitly requires platform-specific evidence.
- Required item-scoped security, live-host, provider, migration, release, or publication checks
  remain additive.
<!-- CURRENT-TEST-GOVERNANCE:END -->

### Problem-First Test Design Rule (Mandatory)

All test scripts, test harnesses, and validation flows must be designed first to reproduce real failures and catch bugs early.

The purpose of testing is to expose defects, regressions, drift, and broken assumptions before users hit them. Tests must not be designed merely to produce a green validation result, satisfy a checklist, or prove that a happy path still passes. Do not waste validation time on pass-only checks that cannot fail for the bug class under review.

Every bugfix or high-risk change must start from the question: "Which test would have caught this before release?" If the existing gate missed the bug, update the targeted test or SOP flow so the same class of bug fails deterministically next time.
This document defines the **mandatory test workflow** for this repo. Run it **before every push** (unless you explicitly document why you are skipping).

## Acceptance Rule (SOP)

Every implementation plan must include the **full test validation procedure** in its final stage. A plan is **not accepted** until all tests in this SOP pass **without errors** and the results are recorded (date + environment + command log reference).

For bugfix and hotfix work, the final full gate is necessary but not sufficient. Acceptance must also prove the reported failure was reproduced and pinned with a targeted regression.

### Documentation-only exception

If a change is **strictly documentation-only**, the test workflow in this SOP is not required.

This exception applies only when all touched files are documentation/planning/SOP content and the change does **not** modify:
- application code
- test code or test fixtures
- scripts/tooling behavior
- generated contracts/specs/artifacts
- configuration or runtime behavior

If a documentation edit is bundled with any non-documentation change, this exception does not apply.

## Bugfix / Hotfix Acceptance Model (Mandatory)

Bugfix and hotfix acceptance follows `Reproduce -> Pin -> Sweep`.

1. `Reproduce`
   - Capture pre-fix failure evidence with the smallest credible seam: a failing targeted test, deterministic script, or live/runtime evidence if local reproduction is not feasible.
   - If the bug is first observed in a deployed environment, record the concrete failing behavior before patching and map it to the repo seam that should have caught it.
2. `Pin`
   - Add or update a targeted regression that encodes the root cause.
   - The regression must fail against the broken behavior and pass only after the fix; validating nearby copy or an unrelated happy path is not sufficient.
3. `Sweep`
   - Run the mandatory full workflow below after the targeted regression is green, so collateral regressions are still caught.

Required guardrails:
- Implementation plans for bugfix/hotfix work must explicitly describe the `Reproduce -> Pin -> Sweep` path in the test plan.
- Implementation records for bugfix/hotfix work must separately record:
  - pre-fix reproduction evidence
  - post-fix targeted regression evidence
  - final full-gate evidence
- If the defect is primarily static-analysis or CI-policy drift (for example workflow token scope or other repo-configuration security boundaries), acceptance must still add a repo-local regression seam over the changed contract surface; do not rely on an external scanner as the only proof.
- If a critical seam is usually mocked (`auth`, `webhook verification`, `connector callback`, `runtime/deploy env`, `provider/queue boundary`, or similar), acceptance must include at least one unmocked test or scripted probe through the real seam.
- For stateful or production-facing transaction flows (`login`, `webhook ingress`, `connector callback`, `approval/admin actions`, `model download/import`, or similar), acceptance must verify the actual transaction outcome, not only page load, redirect presence, or route reachability.
- If the original failure cannot be replayed locally after the fix is in progress, the record must explain why and preserve the best available pre-fix evidence instead of pretending the bug never had a failing state.
- A change is not accepted if the record only says `full gate passed` without showing how the specific bug was reproduced and pinned.

## Prerequisites

- Python 3.10+ (CI uses 3.10/3.11)
- Node.js 18+ (CI uses 20)
- `pre-commit` installed: `python -m pip install pre-commit`
- Backend test deps available in the same interpreter (`numpy`, `pillow`, `aiohttp`)
- Frontend deps installed: `npm ci`

## Environment Sanity (Required Guardrails)

- **Python interpreter must be consistent** for all test commands.
  - Verify: `python -c "import sys; print(sys.executable)"`
  - If you use conda or venv, ensure the same interpreter runs unit tests and connector tests.
- **Project venv recommended**: use an OS-specific local venv to avoid mixed dependencies.
  - Linux/WSL recommended path: `.venv-wsl` (especially when Windows also uses `.venv` in the same repo)
  - Other environments: `.venv`
  - Create: `python -m venv .venv-wsl` (WSL) or `python -m venv .venv`
  - Activate (bash): `source .venv-wsl/bin/activate` (or `.venv/bin/activate`)
  - Activate (pwsh): `.\.venv\Scripts\Activate.ps1`
  - If tests fail due to missing deps in CI parity, rerun in the project venv used by scripts and record that in the implementation record.
- **Node version must be 18+** before E2E:
  - Verify: `node -v`
  - If mismatch in WSL, use the Node 18 path specified below.

## Environment Parity Guardrails (CI Safety)

To avoid local vs CI mismatches:

- **Do not hard-import optional deps in tests** (e.g. `aiohttp`) unless the test explicitly installs them.
- If a test needs a module that may be missing in CI, **use a stub** (e.g. `sys.modules["services.foo"]=stub`) or patch the **module-level import location** used by the code under test.
- If a test truly requires an optional dependency, mark it with a **clear skip** when the dep is unavailable.
- Record the environment in the implementation record (OS, Python, Node, and any extras installed) so mismatches are visible.

## Dependency Parity Preflight (R120)

Before running tests or deploying, validation of the build environment is required to ensure parity.

- **Run the preflight check**:

  ```bash
  python scripts/preflight_check.py
  ```

- **Checks performed**:
  - Python version (>=3.10)
  - Node.js version (>=18.0.0)
  - Essential Python dependencies (cryptography, defusedxml)
  - Optional: Use `--strict` to fail on warnings.

Failed preflight checks must be resolved before proceeding with full test suites.

## CodeQL Policy

- Repository-native CodeQL configuration lives in `.github/workflows/codeql.yml`.
- CodeQL is a GitHub Actions security-validation lane, not a mandatory local pre-push command.
- When changing CodeQL workflow policy, permissions, or language coverage:
  - add or update a repo-local regression seam over the workflow contract
  - verify the workflow after push from GitHub Actions / Security results instead of trying to run full CodeQL locally by default

## Verification Governance Additions (R110 / R112)

- **R110 (skip governance)**:
  - Backend unit-test runs in SOP must include:
    - `--enforce-skip-policy tests/skip_policy.json`
  - Skip report artifact is expected at `.tmp/unit_skip_report.json` (or custom `--skip-report` path).
  - A pass result with skip-policy violations is invalid; treat as failure.
  - Public MAE hard-guarantee suites must be no-skip in both local SOP runs and CI:
    - `tests.test_s60_mae_route_segmentation`
    - `tests.test_s60_routes_startup_gate`
    - `tests.security.test_endpoint_drift`
  - Real-backend low-mock lane must be no-skip in CI:
    - `tests.test_r122_real_backend_lane`
    - `tests.test_r123_real_backend_model_list_lane` (model-list loopback SSRF regression lane)
  - SSRF pinning regression parity lane must be no-skip:
    - `tests.test_s70_ssrf_pinning_regression`

- **R112 (security triple-assert)**:
  - For security reject/degrade paths, tests should assert all three signals:
    - HTTP status
    - machine-readable response code (`code` or `error`)
    - audit contract (`action` + `outcome`, and status/reason when applicable)
  - Do not approve security-path tests that assert status only.

## Offline / Restricted Network Pre-commit (Fail Fast)

If your environment cannot reach GitHub, `pre-commit` may hang while installing hook repos.
Use **one** of the following, and record it in the implementation record:

1) **Preferred**: run once with network to populate the cache
   - `pre-commit install --install-hooks`
   - Subsequent runs will use cache without network.
2) **Proxy**: configure `https_proxy` / `http_proxy` for GitHub access.
3) **Fail-fast guard**: if GitHub access is blocked, stop and fix connectivity or use cached hooks.
   - Do not mark pre-commit as "passed" unless the hooks complete successfully.

Do **not** switch hooks to `repo: local` unless CI is updated to match, or you will reintroduce local/CI divergence.

## Pre-commit Cache Repair (If Cache Is Corrupt)

Symptoms:

- `InvalidManifestError` or missing `.pre-commit-hooks.yaml`
- partial venv in pre-commit cache
- repeated install failures even after network is restored

Fix (choose one):

1) **Clear cache and re-install hooks (recommended)**
   - Linux/WSL:
     - `rm -rf ~/.cache/pre-commit`
     - `pre-commit install --install-hooks`
   - Windows (PowerShell):
     - `Remove-Item -Recurse -Force \"$env:USERPROFILE\\.cache\\pre-commit\"`
     - `pre-commit install --install-hooks`

2) **Set a clean cache location**
   - `set PRE_COMMIT_HOME=/path/to/new/cache`
   - `pre-commit install --install-hooks`

If GitHub is unreachable, the above will still fail; fix connectivity or configure a proxy first.

## Windows Lock-File Guardrail (Required on WinError 5)

When you see:

- `PermissionError: [WinError 5] Access is denied`
- failure deleting `...\\.cache\\pre-commit\\...\\Scripts\\*.exe`

this is usually a **locked executable**, not a logic error in hooks.

Use this exact sequence (PowerShell):

1) Stop active processes that may hold the file lock
   - `Get-Process pre-commit,python,git -ErrorAction SilentlyContinue | Stop-Process -Force`
2) Use a repo-local pre-commit cache (prevents repeated global-cache lock conflicts)
   - `$env:PRE_COMMIT_HOME = \"$PWD\\.tmp\\pre-commit-win\"`
3) Clean and rerun
   - `pre-commit clean`
   - `pre-commit run detect-secrets --all-files`
   - `pre-commit run --all-files --show-diff-on-failure`
4) If cleanup still fails, remove cache directory directly
   - `Remove-Item -Recurse -Force \"$env:PRE_COMMIT_HOME\"`
   - `New-Item -ItemType Directory -Force \"$env:PRE_COMMIT_HOME\" | Out-Null`
   - rerun step (3)

Rules:

- Do not run multiple pre-commit commands in parallel on Windows.
- Do not mark tests as passed if hooks were interrupted by lock errors.

### Windows PATH and Process Reality Checks

Use these checks before assuming the hook runner is broken:

1) `where pre-commit` can be empty in PowerShell even when module execution works.
   - Prefer:
     - `python -m pre_commit --version`
     - `Get-Command pre-commit -All`
2) If multiple Python installations exist, always run:
   - `python -m pre_commit ...`
   instead of relying on bare `pre-commit` resolution.
3) If process cleanup looks inconsistent, inspect actual command lines:
   - `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'pre-commit|detect-secrets|black' } | Select-Object ProcessId,ParentProcessId,Name,CommandLine`
4) `taskkill` may report "no running instance" when the PID already exited between scans.
   - Re-run the `Get-CimInstance` query above before deciding a process is still stuck.

## Required Pre-Push Workflow (Must Run)

### Optional: One-Command Full Test Scripts (Fastest)

Use these if you want a single command that runs **all required steps** (detect-secrets, pre-commit, unit tests, E2E). These scripts also handle the most common environment issues (Windows cache locks, Black cache, Node 18).
Scripts enforce a project-local venv and will bootstrap missing test tooling (`pre-commit`, and `aiohttp` where needed for imports).
R118 adversarial stage uses adaptive profile selection (`--profile auto`) and escalates to `extended` on high-risk diffs.
On WSL, scripts prefer `.venv-wsl`; on Windows they use `.venv`.
If the selected venv exists but is invalid for the current OS/interpreter, rerun via the script so it can recreate that venv.
Linux script includes an explicit offline fail-fast guard: if dependency bootstrap fails (for example `aiohttp` / `pre-commit` install), it stops with remediation hints instead of continuing with partial state.

- Linux/WSL:
  - `bash scripts/run_full_tests_linux.sh`
- Windows (PowerShell):
  - `powershell -File scripts/run_full_tests_windows.ps1`

### Optional automation (recommended)

Enable the repository-managed Git pre-push hook once:

```bash
git config core.hooksPath .githooks
```

Then every `git push` will run:

```bash
bash scripts/pre_push_checks.sh
```

`scripts/pre_push_checks.sh` is the CI-parity guard and must reconcile the exact
frontend lockfile with `npm ci`, then block high-severity findings across production
and development dependencies with `npm audit --audit-level=high`, before the 9
behavioral and governance stages:

1) `detect-secrets`
2) all `pre-commit` hooks
3) coverage governance check (`scripts/verify_quality_governance.py`)
4) test debt governance check (`scripts/verify_test_debt_governance.py`)
5) backend unit coverage gate (`scripts/run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json --coverage-json .tmp/coverage/backend_unit_coverage.json`)
6) backend real E2E lanes (`tests.test_r122_real_backend_lane` + `tests.test_r123_real_backend_model_list_lane`)
7) R121 retry partition contract (`tests.test_r121_retry_partition_contract`)
8) R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
9) frontend E2E (`npm test`)

IMPORTANT:

- Do not remove stage (3). If governance drift is not checked locally, coverage / mutation protections can silently weaken while the main test suite still looks green.
- Do not remove stage (4). If stale skip-policy or mutation allowlist debt is not checked locally, CI can silently accumulate unreviewed governance exceptions.
- Do not remove stage (5). If pre-push skips the shared backend coverage gate, local pushes can pass while GitHub CI fails later.
  - Stage (5) must remain the shared coverage-backed helper so the active `fail_under` floor is enforced locally and in CI with the same artifact path.
- Do not remove stage (6). If pre-push skips real-backend lanes, model-list/webhook wiring regressions can bypass local checks and fail later in CI.
- Do not remove stage (7) or stage (8). If pre-push skips retry partition or adversarial gates, verification hardening regressions can bypass local checks and fail later in CI.
- Do not downgrade stage (8) back to fixed smoke profile. Adaptive mode is required so high-risk diffs auto-escalate to `extended`.
- Keep dependency bootstrap in this script aligned with `.github/workflows/ci.yml` unit-test dependencies.

## R118 Adaptive Profile + Mutation Strictness (Required)

- Default gate command: `python scripts/run_adversarial_gate.py --profile auto --seed 42`.
- `auto` selection behavior:
  - `smoke` by default for non-hotspot diffs.
  - `extended` when changed files match high-risk patterns (security/authz/route-boundary paths).
- CI/local diff hints:
  - set `OPENCLAW_DIFF_BASE` and `OPENCLAW_DIFF_HEAD` for deterministic selection in automation.
- In `extended` runs triggered by high-risk changes, mutation gate enforces both:
  - global score threshold (`>= 80%` unless explicitly overridden), and
  - strict zero-survivor on changed high-risk files.
- Known equivalent survivors must be explicitly listed in `tests/mutation_survivor_allowlist.json`; non-allowlisted survivors fail the gate even if score threshold passes.

## Coverage Governance Baseline (Required)

- Coverage configuration lives in `pyproject.toml` and must keep:
  - `fail_under >= 55.0`
  - `show_missing = true`
  - `skip_covered = true`
- staged coverage ratchet policy (`tests/coverage_governance_policy.json`) lives in the repo and governs the current floor plus future ratchet targets.
  - `pyproject.toml` must keep `fail_under` aligned with the current stage floor declared there.
  - Planned ratchet targets are governance metadata, not implicit permission to change `fail_under` ad hoc.
- retained promotion evidence lives in `tests/coverage_promotion_reviews.json`.
  - the ratchet-55 promotion requires two consecutive ratchet-45 release-cycle reviews with
    immutable commit/artifact identity, all required hotspot percentages, and owned suites.
- backend unit coverage should be gathered with:

```bash
python scripts/run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json --coverage-json .tmp/coverage/backend_unit_coverage.json
```

  - This helper is the shared local/CI path that both enforces the active `fail_under` floor and emits the JSON artifact used by hotspot-family review reporting.
- Coverage governance summary report:

```bash
python scripts/report_coverage_governance.py --coverage-json <path-to-coverage.json>
```

  - This report is the governed hotspot-family review path for critical areas such as `safe_io`, security boundaries, connector config/ingress, and config/bootstrap seams.
  - It does not replace the enforced global gate; it provides the review surface needed before stage promotion.
- Governance drift check command:

```bash
python scripts/verify_quality_governance.py
```

- This check must stay in the standard local/full-test flow so threshold/config drift is caught before push.

## Test Debt Governance Baseline (Required)

- Governance drift check command:

```bash
python scripts/verify_test_debt_governance.py
```

- No-skip modules in `tests/skip_policy.json` must keep explicit metadata:
  - `reason`
  - `review_after`
- Mutation survivor allowlist entries in `tests/mutation_survivor_allowlist.json` must keep:
  - `reason`
  - `review_after`
- Both governance files must point only at live repo paths; stale module/file references are gate failures.
- `review_after` dates in the past are treated as actionable governance debt and must be refreshed or removed before acceptance.

1) Detect Secrets (baseline-based)

```bash
pre-commit run detect-secrets --all-files
```

1) Run all pre-commit hooks

```bash
pre-commit run --all-files --show-diff-on-failure
```

**IMPORTANT (must read): pre-commit "modified files" is a failure until committed**

- Some hooks (e.g. `end-of-file-fixer`, `trailing-whitespace`) intentionally **exit non-zero** when they auto-fix files.
- CI will fail if those fixes are not committed.
- Rule: keep re-running step (2) until it reports **no modified files**, and `git status --porcelain` is empty.

Typical loop:

```bash
pre-commit run --all-files --show-diff-on-failure
git status --porcelain
git diff
git add -A
git commit -m "Apply pre-commit autofixes"
pre-commit run --all-files --show-diff-on-failure
```

1) Backend unit tests (recommended; CI enforces)

```bash
MOLTBOT_STATE_DIR="$(pwd)/moltbot_state/_local_unit" python scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json
```

1) Backend real E2E lane (low-mock; recommended CI parity spot-check)

```bash
MOLTBOT_STATE_DIR="$(pwd)/moltbot_state/_local_backend_e2e_real" python scripts/run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
MOLTBOT_STATE_DIR="$(pwd)/moltbot_state/_local_backend_e2e_real" python scripts/run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
```

1) Frontend E2E (Playwright; CI enforces)

```bash
# Ensure you are using Node.js 18+ (CI uses 20).
node -v

# If you're on WSL and `node -v` is < 18, your shell may be picking up the distro Node
# (e.g. `/usr/bin/node`) instead of your user-installed Node. If you use `nvm`, do:
#   source ~/.nvm/nvm.sh
#   nvm use 18.20.8
# Then re-check:
#   node -v
#
# IMPORTANT: run `npm ci` with the same Node version you use for `npm test`.
npm ci

# Development tooling is part of the acceptance trust boundary.
npm audit --audit-level=high

# One-time browser install (recommended)
npx playwright install chromium

npm test
```

For OS-specific E2E setup (Windows/WSL temp-dir shims), see `tests/E2E_TESTING_SOP.md`.

## Chat Connector (Telegram / Discord / LINE) - Manual Test SOP

The chat connector runs as a **separate process** and talks to your local ComfyUI/OpenClaw via HTTP.

### Prereq: use the correct Python interpreter

The connector requires `aiohttp`. A common failure mode on Windows is:

- `pip show aiohttp` succeeds (installed in your conda env)
- but `python3 -m connector` uses a different Python (e.g. system Python) and crashes with `ModuleNotFoundError: aiohttp`

Sanity check:

```powershell
python -c "import sys; print(sys.executable)"
python -c "import aiohttp; print(aiohttp.__version__)"
```

Run the connector with **the same** interpreter:

```powershell
python -m connector
```

### Common env (all platforms)

- `OPENCLAW_CONNECTOR_URL`: ComfyUI base URL (default: `http://127.0.0.1:8188`)
- `OPENCLAW_CONNECTOR_ADMIN_TOKEN`: optional; required for admin endpoints if your server enforces it
- `OPENCLAW_CONNECTOR_DEBUG=1`: verbose logs (recommended while setting up allowlists)

### 1) Telegram (recommended first: no webhook/HTTPS required)

Minimum:

```powershell
$env:OPENCLAW_CONNECTOR_TELEGRAM_TOKEN="123456:ABC..."
$env:OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS="123456789"   # your Telegram user_id
$env:OPENCLAW_CONNECTOR_ADMIN_USERS="123456789"             # for admin-only commands
python -m connector
```

Test commands (in Telegram chat with the bot):

- `/help`
- `/status`
- `/jobs`
- `/run <template_id> key=value --approval`
- `/approvals`
- `/approve <approval_id>`

### 2) Discord (no webhook/HTTPS required; requires Message Content Intent)

In Discord Developer Portal, enable **Message Content Intent** for your bot, otherwise the connector can connect but will not receive message text.

Minimum:

```powershell
$env:OPENCLAW_CONNECTOR_DISCORD_TOKEN="discord_bot_token"
$env:OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS="your_discord_user_id"
$env:OPENCLAW_CONNECTOR_ADMIN_USERS="your_discord_user_id"
python -m connector
```

Optional allowlist by channel instead:

```powershell
$env:OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS="your_channel_id"
```

### 3) LINE (requires a public HTTPS webhook URL)

LINE is webhook-based: LINE servers must be able to `POST` into your connector.
Localhost (`127.0.0.1`) is not reachable from LINE, so you typically need **Cloudflare Tunnel** or **ngrok**.

Minimum:

```powershell
$env:OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET="line_channel_secret"
$env:OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN="line_channel_access_token"
$env:OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS="your_line_user_id"
$env:OPENCLAW_CONNECTOR_ADMIN_USERS="your_line_user_id"
python -m connector
```

Optional bind/port/path:

```powershell
$env:OPENCLAW_CONNECTOR_LINE_BIND="127.0.0.1"
$env:OPENCLAW_CONNECTOR_LINE_PORT="8099"
$env:OPENCLAW_CONNECTOR_LINE_PATH="/line/webhook"
$env:OPENCLAW_CONNECTOR_PUBLIC_BASE_URL="https://<public-host>" # Required for images
```

After starting the connector, expose it via tunnel and set the LINE webhook URL to:
`https://<public-host>/line/webhook`

If messages are ignored, enable debug and check allowlist logs (user/group/room IDs).

#### LINE Image Delivery (F33) - Quick Test

1) Ensure `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` is set to a **public HTTPS** URL.
2) Send `/run <template_id> <prompt> --approval` and approve if required.
3) On completion, the bot should push an image message to LINE.

If you receive a text fallback warning, the public URL is missing, not HTTPS, or unreachable from LINE.

## Templates + `/run` - Authoring & Validation SOP

`/run` does **not** take a ComfyUI workflow id. It takes a **`template_id`** that maps to a JSON workflow file.

### Where templates live

In this repo (and in your ComfyUI install), templates are loaded from:

- `data/templates/*.json` (the exported ComfyUI workflow in API format)
- `data/templates/manifest.json` (optional metadata: defaults, etc)

### Step-by-step: create a new template

1) Export a workflow JSON from ComfyUI (API format)

- Build your workflow in ComfyUI
- Export the workflow JSON (API format) to a file, e.g. `z.json`

1) Copy the exported file into the template directory

- Place it at: `data/templates/z.json`

1) Replace input values with placeholders

The renderer performs **strict placeholder substitution**:

- supported: a JSON string value exactly equal to `{{key}}`
  - Example: `"text": "{{positive_prompt}}"`
- not supported: partial substitutions
  - Example: `"text": "Prompt: {{positive_prompt}}"` (will not be replaced)

So for each field you want to make configurable via chat/webhook, replace the value with a placeholder:

- `{{positive_prompt}}`
- `{{negative_prompt}}`
- `{{seed}}`
- etc.

1) Add an entry to `manifest.json`

This step is **optional**. If you want defaults/metadata, add a new entry under `templates` in `data/templates/manifest.json`:

```json
"your_template_id": {
  "path": "z.json",
  "allowed_inputs": ["positive_prompt"],
  "defaults": {}
}
```

Rules:

- `your_template_id` becomes the identifier used by `/run your_template_id ...` (typically match the file name, e.g. `z`)
- `allowed_inputs` is **metadata only** (not enforced); it can be used by UIs/tools for hints
- `defaults` is optional but recommended (use `{}` if none)
- JSON cannot contain trailing commas

1) Restart ComfyUI

Not strictly required (the backend hot-reloads `manifest.json`), but restarting ComfyUI is still recommended after significant template changes.

### Validate templates are visible

Use the template quick-list endpoint:

- `GET /openclaw/templates`
- `GET /api/openclaw/templates` (browser-friendly)
- Diagnostics (when a template is unexpectedly missing):
  - `GET /api/openclaw/templates?debug=1` (shows which `manifest.json` path was actually loaded)

Expected response:

- `ok: true`
- `templates: [{ id, allowed_inputs, defaults }, ...]`

### Use `/run` from chat

**Free-text prompt support (no `key=value` needed):**

- `/run <template_id> <free text> seed=-1`
- Connector maps free-text to a prompt key:
  - If `manifest.json` `allowed_inputs` has exactly one key -> it uses that.
  - Otherwise prefers: `positive_prompt` -> `prompt` -> `text` -> `positive` -> `caption`.
  - If none match, defaults to `positive_prompt`.
- Ensure the template uses the same placeholder (e.g., `"text": "{{positive_prompt}}"`).

Once the template appears in `/openclaw/templates`, you can run it via chat:

- Run immediately:
  - `/run your_template_id positive_prompt="a cat" seed=123`
- Request approval:
  - `/run your_template_id positive_prompt="a cat" seed=123 --approval`

Unused keys have no effect unless the workflow contains a matching `{{key}}` placeholder.

## F53 Rewrite Recipe Library - Validation SOP

Use this flow to validate the `F53` guarded rewrite contract (`/openclaw/rewrite/recipes*`).

1) Create a recipe (admin token required)

```powershell
curl -X POST http://127.0.0.1:8188/openclaw/rewrite/recipes `
  -H "Content-Type: application/json" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN" `
  -d "{\"name\":\"rewrite-text\",\"operations\":[{\"path\":\"/1/inputs/text\",\"value\":\"{{topic}}\"}],\"constraints\":{\"required_inputs\":[\"topic\"]}}"
```

1) Dry-run preview (must return structured `diff`, no side-effects)

```powershell
curl -X POST http://127.0.0.1:8188/openclaw/rewrite/recipes/<recipe_id>/dry-run `
  -H "Content-Type: application/json" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN" `
  -d "{\"workflow\":{\"1\":{\"inputs\":{\"text\":\"old\"}}},\"inputs\":{\"topic\":\"new\"}}"
```

1) Guarded apply check

- Without `confirm=true` must fail with `apply_requires_confirm` + `rollback_snapshot`.
- With `confirm=true` must return `applied_workflow` and `diff`.

```powershell
curl -X POST http://127.0.0.1:8188/openclaw/rewrite/recipes/<recipe_id>/apply `
  -H "Content-Type: application/json" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN" `
  -d "{\"workflow\":{\"1\":{\"inputs\":{\"text\":\"old\"}}},\"inputs\":{\"topic\":\"new\"},\"confirm\":true}"
```

## F54 Model Manager - Validation SOP

Use this flow to validate the `F54` model search/download/import contract (`/openclaw/models*`).

Preconditions:

- Configure one of:
  - `OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS=your-approved-host`
  - `OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC=1`
- Admin token is available in request headers.

1) Search baseline (normalized contract + deterministic filters)

```powershell
curl "http://127.0.0.1:8188/openclaw/models/search?limit=20&offset=0" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN"
```

1) Create download task

```powershell
curl -X POST http://127.0.0.1:8188/openclaw/models/downloads `
  -H "Content-Type: application/json" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN" `
  -d "{\"model_id\":\"example-model\",\"name\":\"Example Model\",\"model_type\":\"checkpoint\",\"source\":\"catalog\",\"source_label\":\"Catalog\",\"download_url\":\"https://your-approved-host/path/model.safetensors\",\"expected_sha256\":\"<sha256>\",\"provenance\":{\"publisher\":\"Example\",\"license\":\"OpenRAIL\",\"source_url\":\"https://your-approved-host/model\"}}"
```

1) Observe lifecycle + cancellation contracts

- List tasks: `GET /openclaw/models/downloads`
- Single task: `GET /openclaw/models/downloads/{task_id}`
- Cancel (queued/running): `POST /openclaw/models/downloads/{task_id}/cancel`
- Verify state transitions are bounded to:
  - `queued -> running -> completed`
  - or terminal `failed` / `cancelled`

1) Import completed task (policy-gated activation)

```powershell
curl -X POST http://127.0.0.1:8188/openclaw/models/import `
  -H "Content-Type: application/json" `
  -H "X-OpenClaw-Admin-Token: $env:OPENCLAW_ADMIN_TOKEN" `
  -d "{\"task_id\":\"<task_id>\",\"destination_subdir\":\"checkpoints\"}"
```

Expected:

- Import fails closed on missing/invalid provenance or SHA256 mismatch.
- Successful import returns installation record and appears in:
  - `GET /openclaw/models/installations`
  - `GET /openclaw/models/search?installed=true`


## F65 Resume + Restart Recovery - Validation SOP

Use this flow to validate resumable managed downloads and restart recovery behavior.

Preconditions:

- F54 preconditions still apply (`OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS` or `OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC=1`).
- Keep `OPENCLAW_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT` set to a bounded value (recommended default: `32`).

1) Resume contract checks

- Start a download task and interrupt while in `running` (cancel/process stop) so a `.part` + checkpoint remain.
- Re-run the same task context and verify:
  - when upstream supports `Range` + matching validators, task finishes with `resume_status=resumed_partial`.
  - when upstream does not honor range or validators drift, task still completes via deterministic full restart with fallback `resume_status`.

1) Restart recovery checks

- Leave one or more tasks in non-terminal state (`queued`/`running`) and restart backend process.
- Verify replay transition behavior:
  - non-terminal tasks are recovered into active queue (`recovering -> queued/running`).
  - replay overflow (beyond configured limit) is fail-closed with `error=recovery_replay_limit_exceeded`.

1) Non-regression checks

- Ensure import path still enforces SHA256 verification and provenance checks.
- Validate endpoint/auth matrix remains unchanged for `/openclaw/models/downloads*` and `/openclaw/models/import`.

## Admin Token & UI Usage (SOP)

**Key rule:** `OPENCLAW_ADMIN_TOKEN` is a **server-side environment variable**.
The UI can **use** an Admin Token for authenticated requests, but **cannot set or persist** the server token.

### Recommended setup (local only)

1) **Set server token (env)**

```powershell
$env:OPENCLAW_ADMIN_TOKEN="your_admin_token_here"
```

1) **Restart ComfyUI**
2) **Enter the same token in the Settings UI**
   - This only stores it in the browser session for API calls.

### Windows CMD (per-session)

```cmd
set OPENCLAW_ADMIN_TOKEN=your_admin_token_here
set OPENCLAW_LLM_API_KEY=your_api_key_here
set OPENCLAW_LLM_PROVIDER=gemini
```

### Windows CMD (persistent, user-level)

```cmd
setx OPENCLAW_ADMIN_TOKEN "your_admin_token_here"
setx OPENCLAW_LLM_API_KEY "your_api_key_here"
setx OPENCLAW_LLM_PROVIDER "gemini"
```

> After `setx`, open a **new** terminal session before launching ComfyUI.

### Security Notes

- Do **not** expose ComfyUI to the internet with UI-only tokens.
- Admin token must remain server-side and protected by OS/environment.

## WSL / Restricted Environments

If `pre-commit` fails due to cache permissions, run with a writable cache directory:

```bash
PRE_COMMIT_HOME=/tmp/pre-commit-cache pre-commit run --all-files --show-diff-on-failure
```

## Troubleshooting Quick Fixes

**Detect-secrets fails**

- Update `.secrets.baseline` (or mark known false positives) and avoid real-looking secrets in docs/tests.

**Playwright fails (missing browsers)**

- Install browsers: `npx playwright install chromium`

**E2E fails with "test harness failed to load"**

- Check the console error (module import/exports mismatch is the most common cause).
- Verify all referenced JS modules exist and export expected names.
<!-- ROOKIEUI-GLOBAL-TEST-SOP-RULES:START -->
## RookieUI-Derived Global Testing Rules

These rules preserve this repository's existing test lanes while adding the shared testing baseline used across this workspace.

### Required Reading Order

1. `tests/TEST_SOP.md`
2. `tests/E2E_TESTING_NOTICE.md`
3. `tests/E2E_TESTING_SOP.md`

### Acceptance Rule

A change is not accepted until required checks pass and evidence is recorded. Existing repo-specific gates remain authoritative; this section adds the shared minimum expectations.

Required shared gate:

1. `pre-commit run detect-secrets --all-files`
2. `pre-commit run --all-files --show-diff-on-failure`
3. backend/unit tests through the repo's documented runner, preferring `scripts/run_unittests.py` when present
4. frontend/E2E tests through the repo's documented Playwright or harness lane, usually `npm test` when a Node harness exists
5. targeted type/static validation when the changed surface has a typed frontend or equivalent static contract

If a repo has no frontend/E2E harness, the SOP must state the non-applicability and identify the replacement smoke, unit, or integration lane that catches the same user-facing risk.

### Problem-First Test Design Rule

All test scripts, test harnesses, and validation flows must be designed first to reproduce real failures and catch bugs early.

The purpose of testing is to expose defects, regressions, drift, and broken assumptions before users hit them. Tests must not be designed merely to produce a green validation result, satisfy a checklist, or prove that a happy path still passes. Do not waste validation time on pass-only checks that cannot fail for the bug class under review.

Every bugfix or high-risk change must start from the question: "Which test would have caught this before release?" If the existing gate missed the bug, update the targeted test or SOP flow so the same class of bug fails deterministically next time.

### Bugfix/Hotfix Rule (Reproduce -> Pin -> Sweep)

For bugfix/hotfix work, acceptance evidence must include:

1. pre-fix reproduction evidence
2. post-fix targeted regression evidence
3. final full-gate evidence

A green full gate alone is not sufficient bugfix evidence unless the record also shows how the specific failure was reproduced and pinned.

### Documentation-only Exception

If all touched files are documentation/planning text only and no code, tests, scripts, config, generated artifacts, dependency manifests other than a version-field-only `pyproject.toml` update, or runtime behavior changed, no full test execution is required. Once executable or runtime-affecting files change, this exception does not apply.

### Environment Guardrails

- Keep the Python interpreter consistent across all commands.
- Prefer a project-local virtual environment: `.venv` on Windows and `.venv-wsl` on WSL/Linux when the repo supports dual-OS validation.
- Do not mix global and venv-installed `pre-commit` accidentally.
- Node.js must be 18+ before running frontend/E2E tests.
- On Windows, prefer repo-local `PRE_COMMIT_HOME` to avoid cache lock issues.
- On WSL, if `python` is missing but `python3` exists, create a local shim before running Playwright or harness commands.
- If pre-commit modifies files, review/stage/commit those changes and rerun hooks until clean.

### Evidence Recording

Implementation records must include date/time, OS/environment, command log reference, and pass/fail result for each required stage. If a gate is intentionally skipped as non-applicable, record why and name the replacement validation lane.
<!-- ROOKIEUI-GLOBAL-TEST-SOP-RULES:END -->
