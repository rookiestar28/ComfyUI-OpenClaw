# Release Checklist (DoD)

<!-- CURRENT-ACCEPTANCE-POLICY:START -->
## Current Acceptance Boundary

- Pure text/documentation changes and version-field-only `pyproject.toml` updates, when no release
  or publication action is being performed, require no plan, independent reviewer, test contract,
  Full Gate, E2E, or Hosted CI run.
- For non-exempt work, including an actual release candidate, the Windows Full Gate is the
  authoritative repository-wide result. Push and Hosted CI are not prerequisites and need not bind
  evidence to a pushed commit.
- Release-specific provenance, archive, registry, installation, rollback, and owner decision checks
  below remain additive when a release is actually performed.
<!-- CURRENT-ACCEPTANCE-POLICY:END -->

This document contains the authoritative checklist for releasing **ComfyUI-OpenClaw**.
A release candidate must pass **Gate A** to be considered for Public Release v1.
If the deployment enables remote control or bridge features, it must also pass **Gate B**.

> [!IMPORTANT]
> The validation workflow in `tests/TEST_SOP.md` is **mandatory** for all releases.

---

## Gate A: Public Release v1 Baseline (Required)

**Goal**: Safe-by-default for internet-exposed deployments (assuming they follow the deployment recipes in `docs/deploy/`).

### 1. Security & configuration

- [ ] **Admin Boundaries**:
  - [ ] Server-side admin write boundary uses `OPENCLAW_ADMIN_TOKEN` (legacy `MOLTBOT_ADMIN_TOKEN`).
  - [ ] Connector admin command paths use `OPENCLAW_CONNECTOR_ADMIN_TOKEN`, and must match server admin token when server admin auth is enabled.
- [ ] **Connector Ingress Defaults**: Platform adapters remain disabled unless required token/enable vars are configured (Telegram/Discord/LINE/WhatsApp/WeChat/Kakao/Slack/Feishu).
- [ ] **Connector Allowlists (Strict Posture)**: In `public` deployment or `hardened` runtime posture, active connector platforms must have allowlist coverage before startup (fail-closed; public check code `DP-PUBLIC-009`).
- [ ] **Connector Replay & Visibility**: Duplicate committed connector events are no-ops, retryable pre-commit failures can be retried, and text-only reply suppression does not hide approval/action controls.
- [ ] **Observability**: `/openclaw/logs/tail` and `/openclaw/config` require `OPENCLAW_OBSERVABILITY_TOKEN` (legacy: `MOLTBOT_OBSERVABILITY_TOKEN`) if accessed remotely, or are loopback-only.
- [ ] **SSRF**: LLM `base_url` defaults to known providers. Custom public URLs require `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1` or explicit allowlist; private/reserved IP targets require the scoped LLM private-network setting or the broader `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` override.
- [ ] **Public Boundary Contract (S69)**: for `OPENCLAW_DEPLOYMENT_PROFILE=public`, set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after reverse-proxy path allowlist + network ACL deny ComfyUI-native high-risk routes.
- [ ] **Budgets**: `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` (concurrency) and `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` (payloads) are enforced.
- [ ] **External Tools**: external tool execution is disabled unless explicitly required; if enabled, the package-owned/default or custom allowlist is reviewed, sandbox policy is explicit for hardened posture, and sandbox/interpreter/timeout/workspace diagnostics are deterministic.
- [ ] **Contracts**: API endpoints match `docs/release/api_contract.md`; Configuration follows `docs/release/config_secrets_contract.md`.

### 2. Documentation & Recipes

- [ ] **Deployment**: `docs/deploy/` contains recipes for:
  - [ ] Local-only (Default)
  - [ ] Tailscale (Recommended Remote)
  - [ ] LAN (Restricted)
- [ ] **Security**: [SECURITY.md](SECURITY.md) is up-to-date and linked from README.
- [ ] **Feature Flags**: `docs/release/feature_flags.md` accurately reflects the codebase defaults.

### 3. Validation (Must Pass)

Run the authoritative Windows Full Gate:

```powershell
# Windows
powershell -File scripts/run_full_tests_windows.ps1
```

Linux/WSL may be run as an optional diagnostic unless the active release item explicitly requires
that platform:

```bash
bash scripts/run_full_tests_linux.sh
```

These scripts execute the authoritative `tests/TEST_SOP.md` sequence, including fresh
lockfile reconciliation with `npm ci`, the blocking
`npm audit --audit-level=high` check across production and development dependencies,
secret scanning, pre-commit hooks, governance and backend lanes, adaptive adversarial
validation, and frontend Playwright E2E. A standalone `npm test` result is not a
substitute for the complete release gate.

If staged/manual execution is required, follow the explicit command order in
`tests/TEST_SOP.md`; do not maintain a shortened release-only sequence here.

---

## Gate B: Bridge / Remote Control Safety (Conditional)

**Goal**: Safe operation when `OPENCLAW_BRIDGE_ENABLED=1` or remote commands are active.

- [ ] **Explicit Enable**: Bridge features are off unless `OPENCLAW_BRIDGE_ENABLED=1` is set.
- [ ] **Auth**: Bridge endpoints require `OPENCLAW_BRIDGE_DEVICE_TOKEN` (legacy alias supported) and device pairing/scope checks.
- [ ] **CSRF**: State-changing endpoints (admin/bridge) enforce Origin checks or require Token on loopback.
- [ ] **Callback Safety**: Delivery targets are validated against DNS/IP allowlists (no internal network access).
- [ ] **DoD**: Operator docs include "Red Lines" (never expose Bridge port directly to internet without auth).

---

## Gate C: Supply Chain Provenance (R100)

**Goal**: Ensure integrity and traceability of release artifacts.

- [ ] **Provenance Generation**: Run `python scripts/generate_provenance.py dist/ dist/provenance.json` to create manifest and SBOM.
- [ ] **Verification**: Run `python scripts/verify_provenance.py dist/ dist/provenance.json` on staging environment to verify integrity and completeness.
- [ ] **Completeness**: Ensure `provenance.json` contains SHA256 for all distributed wheels/zips and correct git commit hash.

---

## Release Metadata

- [ ] **Version**: `pyproject.toml` version matches git tag.
- [ ] **Changelog**: Updated `CHANGELOG.md` (if present) with user-facing changes.
- [ ] **Migration**: If config/storage schema changed, explicit migration notes are in `docs/migration/` (Optional).

---

## Sign-off

- [ ] **Gate A Passed**: (Date/Initials)
- [ ] **Gate B Passed** (if applicable): (Date/Initials)
