Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location $root

Write-Host "[tests] repo: $root"

# Cache isolation for pre-commit + black
$env:PRE_COMMIT_HOME = if ($env:PRE_COMMIT_HOME) { $env:PRE_COMMIT_HOME } else { "$root\.tmp\pre-commit-win" }
$env:BLACK_CACHE_DIR = if ($env:BLACK_CACHE_DIR) { $env:BLACK_CACHE_DIR } else { "$root\.tmp\black-cache" }
New-Item -ItemType Directory -Force $env:PRE_COMMIT_HOME | Out-Null
New-Item -ItemType Directory -Force $env:BLACK_CACHE_DIR | Out-Null

function Require-Cmd($cmd) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    throw "[tests] ERROR: missing command: $cmd"
  }
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][scriptblock]$Command
  )
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "[tests] ERROR: $Label failed with exit code $LASTEXITCODE"
  }
}

function Get-GitDiffSnapshot {
  param([switch]$Cached)
  # IMPORTANT: compare both worktree and index; pre-commit can mutate staged files while exiting 0.
  if ($Cached) {
    return (& git diff --cached --binary -- . | Out-String)
  }
  return (& git diff --binary -- . | Out-String)
}

function Assert-PreCommitDidNotMutateRepo {
  param(
    # IMPORTANT: git diff snapshots can be empty strings on a clean repo/index.
    # Do not mark these as mandatory non-empty inputs or the Windows full gate
    # fails before it can compare pre-commit mutation state.
    [AllowEmptyString()][string]$BeforeWorktree = "",
    [AllowEmptyString()][string]$BeforeIndex = ""
  )

  $afterWorktree = Get-GitDiffSnapshot
  $afterIndex = Get-GitDiffSnapshot -Cached
  if ($BeforeWorktree -ne $afterWorktree -or $BeforeIndex -ne $afterIndex) {
    throw "[tests] ERROR: pre-commit hooks modified tracked files (worktree or index). Review/stage the hook changes, then rerun the acceptance gate.`n$(& git status --short | Out-String)"
  }
}

function Assert-CleanPublicWorktree {
  # CRITICAL: tracked diff snapshots do not include untracked deliverables. A full
  # acceptance gate must bind to one clean committed candidate, not local-only files.
  $statusLines = @(& git status --porcelain --untracked-files=all)
  if ($LASTEXITCODE -ne 0) {
    throw "[tests] ERROR: unable to inspect Git worktree state"
  }
  if ($statusLines.Count -gt 0) {
    throw "[tests] ERROR: acceptance requires a clean committed candidate. Commit or remove public changes, then rerun.`n$($statusLines -join [Environment]::NewLine)"
  }
}

Assert-CleanPublicWorktree

Require-Cmd node
Require-Cmd npm

function Ensure-NpmDeps {
  # IMPORTANT: acceptance must reconcile the complete lockfile; file-presence
  # shortcuts can silently reuse an invalid or stale development dependency tree.
  Write-Host "[tests] Reconciling frontend dependencies via npm ci ..."
  Invoke-Checked "npm ci" { npm ci }
}

# Prefer project-local virtualenv to avoid global PATH / cache conflicts on Windows.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
function New-ProjectVenv {
  Write-Host "[tests] Creating project venv at $root\.venv ..."
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
  }
  elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
  }
  else {
    throw "[tests] ERROR: no bootstrap Python found (need py or python)"
  }
}

function Test-VenvPython {
  param([string]$PythonExe)
  if (-not (Test-Path $PythonExe)) {
    return $false
  }
  try {
    & $PythonExe -c "import sys; print(sys.executable)" | Out-Null
    return $true
  }
  catch {
    return $false
  }
}

function Test-VenvCfgWindowsCompatible {
  $cfg = Join-Path $root ".venv\pyvenv.cfg"
  if (-not (Test-Path $cfg)) {
    return $false
  }
  try {
    $content = Get-Content $cfg -Raw
    # WSL/Linux-built venvs typically contain POSIX home paths (e.g. /usr/bin)
    if ($content -match "home\s*=\s*/") {
      return $false
    }
    return $true
  }
  catch {
    return $false
  }
}

if (-not (Test-Path $venvPython)) {
  New-ProjectVenv
}
elseif (-not (Test-VenvCfgWindowsCompatible)) {
  Write-Host "[tests] WARN: existing .venv was created from non-Windows interpreter; recreating ..."
  Remove-Item -Recurse -Force ".venv"
  New-ProjectVenv
}
elseif (-not (Test-VenvPython -PythonExe $venvPython)) {
  Write-Host "[tests] WARN: existing .venv is invalid for current OS/interpreter; recreating ..."
  Remove-Item -Recurse -Force ".venv"
  New-ProjectVenv
}

if (-not (Test-VenvPython -PythonExe $venvPython)) {
  throw "[tests] ERROR: project venv python is not runnable: $venvPython"
}

$hasPreCommit = $true
& $venvPython -m pre_commit --version | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasPreCommit = $false
}
if (-not $hasPreCommit) {
  Write-Host "[tests] Installing pre-commit into project venv ..."
  Invoke-Checked "pip install pre-commit" { & $venvPython -m pip install -U pip pre-commit }
}

$hasAiohttp = $true
& $venvPython -c "import aiohttp" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasAiohttp = $false
}
if (-not $hasAiohttp) {
  Write-Host "[tests] Installing aiohttp into project venv ..."
  Invoke-Checked "pip install aiohttp" { & $venvPython -m pip install aiohttp }
}

$hasCrypto = $true
& $venvPython -c "import Cryptodome" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasCrypto = $false
}
if (-not $hasCrypto) {
  Write-Host "[tests] Installing pycryptodomex into project venv (R82 AES) ..."
  Invoke-Checked "pip install pycryptodomex" { & $venvPython -m pip install pycryptodomex }
}

$hasCryptography = $true
& $venvPython -c "import cryptography" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasCryptography = $false
}
if (-not $hasCryptography) {
  Write-Host "[tests] Installing cryptography into project venv (S57 Fernet AEAD) ..."
  Invoke-Checked "pip install cryptography" { & $venvPython -m pip install cryptography }
}

$hasDefusedXml = $true
& $venvPython -c "import defusedxml" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasDefusedXml = $false
}
if (-not $hasDefusedXml) {
  # IMPORTANT: keep Windows full-test bootstrap aligned with requirements.txt.
  Write-Host "[tests] Installing defusedxml into project venv (S85 fail-closed XML parsing) ..."
  Invoke-Checked "pip install defusedxml" { & $venvPython -m pip install defusedxml }
}

$hasCoverageTomlSupport = $true
# CRITICAL: keep this aligned with CI/pre-push bootstrap so Python 3.10 lanes
# install coverage[toml] before the shared backend coverage gate runs.
& $venvPython -c "import sys, importlib.util; has_coverage = importlib.util.find_spec('coverage') is not None; has_toml = sys.version_info >= (3, 11) or importlib.util.find_spec('tomli') is not None; sys.exit(0 if has_coverage and has_toml else 1)" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $hasCoverageTomlSupport = $false
}
if (-not $hasCoverageTomlSupport) {
  Write-Host "[tests] Installing coverage[toml] into project venv (R184 backend coverage gate) ..."
  Invoke-Checked "pip install coverage[toml]" { & $venvPython -m pip install "coverage[toml]" }
}

$qualityToolsReady = $true
& $venvPython -c "import json, sys; from importlib.metadata import version; p=json.load(open('tests/static_analysis_policy.json', encoding='utf-8')); sys.exit(0 if all(version(name)==cfg['version'] for name,cfg in p['tools'].items()) else 1)" | Out-Null
if ($LASTEXITCODE -ne 0) {
  $qualityToolsReady = $false
}
if (-not $qualityToolsReady) {
  Write-Host "[tests] Installing pinned Ruff/Mypy into project venv ..."
  Invoke-Checked "pip install quality tools" { & $venvPython -m pip install -r requirements-quality.txt }
}

# Ensure Node >= 18
$nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
if ($nodeMajor -lt 18) {
  Write-Host "[tests] WARN: Node < 18 detected. Trying nvm use 18..."
  if (Get-Command nvm -ErrorAction SilentlyContinue) {
    nvm use 18 | Out-Null
    $nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
  }
}
if ($nodeMajor -lt 18) {
  throw "[tests] ERROR: Node >=18 required, current=$(node -v)"
}

function Test-PortBindable {
  param([Parameter(Mandatory = $true)][int]$Port)

  $listener = $null
  try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
    $listener.Server.ExclusiveAddressUse = $true
    $listener.Start()
    return $true
  }
  catch {
    return $false
  }
  finally {
    if ($listener -ne $null) {
      $listener.Stop()
    }
  }
}

function Resolve-E2EPort {
  $requested = $env:OPENCLAW_E2E_PORT
  if ($requested) {
    [int]$requestedPort = 0
    if (-not [int]::TryParse($requested, [ref]$requestedPort)) {
      throw "[tests] ERROR: OPENCLAW_E2E_PORT must be an integer, got '$requested'"
    }
    if (-not (Test-PortBindable -Port $requestedPort)) {
      throw "[tests] ERROR: OPENCLAW_E2E_PORT=$requestedPort is not bindable on 127.0.0.1"
    }
    return $requestedPort
  }

  foreach ($candidate in @(3000, 3300, 3400, 3500, 3600)) {
    if (Test-PortBindable -Port $candidate) {
      return $candidate
    }
  }

  throw "[tests] ERROR: no bindable local port found for Playwright webServer (tried 3000,3300,3400,3500,3600)"
}

$selectedE2EPort = Resolve-E2EPort
$env:OPENCLAW_E2E_PORT = "$selectedE2EPort"
if ($selectedE2EPort -ne 3000) {
  Write-Host "[tests] WARN: port 3000 unavailable; using OPENCLAW_E2E_PORT=$selectedE2EPort for Playwright."
}
else {
  Write-Host "[tests] INFO: using OPENCLAW_E2E_PORT=$selectedE2EPort for Playwright."
}

Write-Host "[tests] Node version: $(node -v)"

$hygieneSnapshot = Join-Path $root ".tmp\workspace-hygiene-full.json"
Invoke-Checked "workspace hygiene snapshot" {
  & $venvPython scripts\check_workspace_hygiene.py snapshot --root $root --snapshot $hygieneSnapshot
}

Write-Host "[tests] 0/11 supply-chain hardening check"
Invoke-Checked "supply-chain hardening check" {
  & $venvPython scripts\check_supply_chain_hardening.py
}
Ensure-NpmDeps
Write-Host "[tests] 0.25/11 frontend dependency audit"
Invoke-Checked "npm audit" { npm audit --audit-level=high }

Write-Host "[tests] 0.5/11 static analysis policy"
Invoke-Checked "static analysis policy" {
  & $venvPython scripts/verify_static_analysis_policy.py
}

Write-Host "[tests] 0/8 R120 dependency preflight"
Invoke-Checked "preflight_check" { & $venvPython scripts\preflight_check.py --strict }

Write-Host "[tests] 1/8 detect-secrets"
Invoke-Checked "detect-secrets" { & $venvPython -m pre_commit run detect-secrets --all-files }

Write-Host "[tests] 2/8 pre-commit all hooks (pass 1: autofix)"
$preCommitWorktreeBefore = Get-GitDiffSnapshot
$preCommitIndexBefore = Get-GitDiffSnapshot -Cached
& $venvPython -m pre_commit run --all-files --show-diff-on-failure
if ($LASTEXITCODE -ne 0) {
  Write-Host "[tests] INFO: pre-commit reported changes/issues; running pass 2 verification..."
  Invoke-Checked "pre-commit all hooks (pass 2 verify)" { & $venvPython -m pre_commit run --all-files --show-diff-on-failure }
}
Assert-PreCommitDidNotMutateRepo -BeforeWorktree $preCommitWorktreeBefore -BeforeIndex $preCommitIndexBefore

Write-Host "[tests] 3/10 coverage governance check"
Invoke-Checked "coverage governance check" {
  & $venvPython scripts\verify_quality_governance.py
}

Write-Host "[tests] 4/10 test debt governance check"
Invoke-Checked "test debt governance check" {
  & $venvPython scripts\verify_test_debt_governance.py
}

Write-Host "[tests] 5/10 backend unit tests"
$env:MOLTBOT_STATE_DIR = "$root\.tmp\test-state\full\unit"
Invoke-Checked "backend unit tests" {
  & $venvPython scripts\run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests\skip_policy.json --coverage-json .tmp\coverage\backend_unit_coverage.json
}

Write-Host "[tests] 5.1/10 backend coverage hotspot report"
Invoke-Checked "backend coverage hotspot report" {
  & $venvPython scripts\report_coverage_governance.py --coverage-json .tmp\coverage\backend_unit_coverage.json
}

if ($env:OPENCLAW_IMPL_RECORD_PATH) {
  Write-Host "[tests] 5.5/10 implementation record lint (strict)"
  # IMPORTANT: strict mode is opt-in via OPENCLAW_IMPL_RECORD_PATH to avoid retroactive legacy record failures.
  Invoke-Checked "implementation record lint" {
    & $venvPython scripts\lint_implementation_record.py --path $env:OPENCLAW_IMPL_RECORD_PATH --strict
  }
}

Write-Host "[tests] 6/10 backend real E2E lanes (R122/R123)"
$env:MOLTBOT_STATE_DIR = "$root\.tmp\test-state\full\backend-e2e-real"
Invoke-Checked "backend real E2E lane R122" {
  & $venvPython scripts\run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests\skip_policy.json --max-skipped 0
}
Invoke-Checked "backend real E2E lane R123" {
  & $venvPython scripts\run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests\skip_policy.json --max-skipped 0
}

Write-Host "[tests] 7/10 R121 retry partition contract"
Invoke-Checked "R121 retry partition contract" {
  & $venvPython scripts\run_unittests.py --module tests.test_r121_retry_partition_contract --enforce-skip-policy tests\skip_policy.json --max-skipped 0
}

Write-Host "[tests] 8/10 Slack integration gates (R124/R125/R117/F57)"
Invoke-Checked "Slack integration gates" {
  & $venvPython scripts\run_unittests.py --module tests.test_r124_slack_ingress_contract --enforce-skip-policy tests\skip_policy.json --max-skipped 0
  & $venvPython scripts\run_unittests.py --module tests.test_r125_slack_real_backend_lane --enforce-skip-policy tests\skip_policy.json --max-skipped 0
  & $venvPython scripts\run_unittests.py --module tests.test_r117_observability_redaction_e2e --enforce-skip-policy tests\skip_policy.json --max-skipped 0
  & $venvPython scripts\run_unittests.py --module tests.test_r117_observability_redaction_endpoints --enforce-skip-policy tests\skip_policy.json --max-skipped 0
  & $venvPython scripts\run_unittests.py --module tests.test_f57_slack_transport_parity --enforce-skip-policy tests\skip_policy.json --max-skipped 0
  & $venvPython scripts\run_unittests.py --module tests.test_f57_slack_socket_mode_startup --enforce-skip-policy tests\skip_policy.json --max-skipped 0
}

Write-Host "[tests] 9/10 R118 adversarial gate (adaptive: smoke/extended)"
$env:MOLTBOT_STATE_DIR = "$root\.tmp\test-state\full\adversarial"
Invoke-Checked "R118 adversarial adaptive" {
  & $venvPython scripts\run_adversarial_gate.py --profile auto --seed 42 --artifact-dir .tmp\adversarial
}

Write-Host "[tests] 10/10 frontend E2E"
$env:OPENCLAW_PLAYWRIGHT_INSTALL = "1"
$env:OPENCLAW_PLAYWRIGHT_BROWSERS = "chromium"
# IMPORTANT: full-gate acceptance must provision Playwright browsers itself; do
# not assume a warmed local browser cache when running on fresh Windows hosts.
Invoke-Checked "frontend E2E" { npm test }

Invoke-Checked "workspace hygiene check" {
  & $venvPython scripts\check_workspace_hygiene.py check --root $root --snapshot $hygieneSnapshot
}
Assert-CleanPublicWorktree
Write-Host "[tests] PASS"
