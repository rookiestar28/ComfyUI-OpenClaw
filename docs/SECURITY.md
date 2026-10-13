# Security Policy

## Quick Links

- Deployment profiles and checklists: [Security Deployment Guide](security_deployment_guide.md)
- Runtime startup hardening behavior: [Runtime Hardening and Startup](runtime_hardening_and_startup.md)
- Pre-exposure checklist: [Security Checklist](security_checklist.md)
- Deployment self-check command:
  - `python scripts/check_deployment_profile.py --profile local|lan|public`

## Supported Versions

Only the latest version of ComfyUI-OpenClaw is supported for security updates.

| Version                  | Supported          |
| ------------------------ | ------------------ |
| Latest published release | :white_check_mark: |
| All earlier releases     | :x:                |

## Reporting a Vulnerability

Please report security vulnerabilities by creating a **private** issue on GitHub if possible, or contact the maintainers directly. Do not open public issues for sensitive security flaws.

### Disclosure Workflow and SLA

Private reporting workflow:
1. Submit a private report with repro steps, affected version, and impact.
2. Maintainers triage and confirm impact.
3. Fix and mitigation guidance are prepared.
4. Advisory is published with affected-range + fixed-version metadata.

Target SLA:
- initial acknowledgement: within 72 hours
- triage status update: within 7 calendar days
- coordinated disclosure target: within 30 days after confirmed impact
  - timeline may be extended for high-complexity fixes; status updates are still required

Advisory publication policy:
- advisories are tracked in `docs/release/security_advisories.json`
- Security Doctor surfaces advisory applicability (`affected`, `mitigation`) for the running version
- high-severity affected posture should be treated as priority upgrade work

### Telemetry Opt-out Contract (S9)

Security anomaly telemetry is minimal and audit-focused by default. If operators must disable this emission path, use:

```bash
export OPENCLAW_TELEMETRY_OPT_OUT=1
# Legacy compatibility:
# export MOLTBOT_TELEMETRY_OPT_OUT=1
```

Trade-off:
- with opt-out enabled, security anomaly audit events are not emitted
- use only when required by policy/privacy constraints and keep compensating controls in place

---

# Safe Deployment Guide

OpenClaw is a powerful extension that interacts with LLMs and the filesystem (via ComfyUI). **By default, it is designed for local (localhost) use.** Exposing it to the public internet requires careful configuration.

## ⚠️ Warning

**Do NOT expose your ComfyUI instance directly to the public internet** (for example via direct port-forwarding) without a secure reverse proxy or VPN.

## Shared Listener Boundary (Critical)

OpenClaw and ComfyUI share the same HTTP listener/port.

This means:

1. Protecting `/openclaw/*` routes does not automatically protect ComfyUI-native routes.
2. Public reverse-proxy policy must enforce path-level allow/deny and network ACL boundaries.
3. Public posture requires explicit operator acknowledgement that these boundaries are in place.

High-risk ComfyUI-native routes to deny on public edges unless intentionally required:

- `/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`
- `/api/prompt`, `/api/history*`, `/api/view*`, `/api/upload*`, `/api/ws`

## Recommended Deployment

1. **Localhost (Default)**: Use on your own machine. No extra config needed.
2. **VPN / Tailscale**: Best for private remote access.
3. **SSH Tunnel**: `ssh -L 8188:localhost:8188 user@remote`

## Reverse Proxy Setup (Advanced)

If you must expose OpenClaw via a reverse proxy (Nginx, Caddy, Cloudflare Tunnel), you MUST configure the following:

### 1. Token Boundaries

Logs (`/openclaw/logs/tail`) and Config (`/openclaw/config`) are restricted to loopback clients by default. (Legacy `/moltbot/*` endpoints are also supported.) To allow remote access via proxy, set a secure token:

```bash
export OPENCLAW_OBSERVABILITY_TOKEN="your-secure-random-token-here"
export OPENCLAW_ADMIN_TOKEN="your-secure-random-admin-token-here"
# Legacy compatibility (optional):
# export MOLTBOT_OBSERVABILITY_TOKEN="your-secure-random-token-here"
# export MOLTBOT_ADMIN_TOKEN="your-secure-random-admin-token-here"
```

Then configure your proxy or client to send the header `X-OpenClaw-Obs-Token: your-secure-random-token-here` (legacy: `X-Moltbot-Obs-Token`).

### 1.1 Reasoning and Internal Content Redaction Boundary

Operator-facing payloads strip provider reasoning / thinking traces and explicitly marked internal maintenance/helper prompt content by default across:

- assist responses
- event / SSE payloads
- trace responses
- callback payloads
- connector trace/debug replies
- audit event payload/meta fields

Internal maintenance/helper prompt content has no public or debug reveal path. Privileged reasoning reveal is limited to provider reasoning / thinking traces only.

### 1.2 Reasoning Debug Reveal Boundary (Local-only)

There is a privileged local-debug reveal path for troubleshooting, but it is fail-closed unless **all** of the following are true:

- request explicitly opts in via `X-OpenClaw-Debug-Reveal-Reasoning: 1` or `?debug_reasoning=1`
- server-side debug switch is enabled with `OPENCLAW_DEBUG_REASONING_REVEAL=1`
- request is admin-authorized
- client IP resolves to loopback
- deployment profile is `local` or `lan`
- runtime profile is not hardened

Operational rules:

- do not enable `OPENCLAW_DEBUG_REASONING_REVEAL` on public deployments
- treat any successful reveal as privileged debugging activity and review related audit events (`reasoning.debug_reveal`)
- the reveal path appends debug reasoning payloads only for the privileged request; default operator outputs remain redacted

### 2. Trusted Proxy Attribution

If using a reverse proxy, OpenClaw needs to know the *real* client IP for rate limiting enforcement.

Configure your proxy to send `X-Forwarded-For`, then configure trusted proxy ranges:

```bash
export OPENCLAW_TRUST_X_FORWARDED_FOR=1
export OPENCLAW_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
# Legacy compatibility (optional):
# export MOLTBOT_TRUST_X_FORWARDED_FOR=1
# export MOLTBOT_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
```

### 3. Public Profile Boundary Acknowledgement

For public profile deployments, you must explicitly acknowledge that reverse-proxy path controls and network ACL boundaries are already enforced:

```bash
export OPENCLAW_DEPLOYMENT_PROFILE=public
export OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1
# Legacy compatibility (optional):
# export MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1
```

If this acknowledgement is missing in public profile, deployment profile checks fail with `DP-PUBLIC-008`.

### 4. Connector Allowlist Fail-Closed (Public/Hardened)

Connector ingress posture is fail-closed in strict profiles:

- if connector platform ingress is active (Telegram/Discord/LINE/WhatsApp/WeChat/Kakao/Slack/Feishu)
- and matching allowlist variables are missing
- startup/deployment checks fail closed (`DP-PUBLIC-009` for public profile)

Operational requirement:

- never enable connector platform tokens/enable flags in public or hardened posture without platform allowlist coverage.

### 4.1 Interactive Callback Contract Baseline (Connector)

For interactive connector callbacks (actions/modals/workflow style payloads), the shared callback contract is fail-closed by default:

- signed envelope is required (`signature`, `timestamp`, `request_id`, `workspace_id`, `action_type`, `payload_hash`)
- stale timestamp, replay/duplicate request ID, payload-hash mismatch, or unknown action type are rejected
- workspace-to-installation resolution is fail-closed on missing/ambiguous/inactive/stale-token-ref binding
- policy mapping is explicit (`public`/`run`/`admin`) and untrusted `run` callbacks degrade to approval instead of direct privileged execution
- duplicate committed connector events are acknowledged without re-running completed actions, while retryable failures before action/delivery commit can be retried
- reply visibility is policy-driven; text-only silent/internal/tool-only/no-mention replies can be suppressed without suppressing approval cards, action buttons, allowlist checks, or callback replay checks

Operational note:

- treat callback decision codes/audit trails as security evidence and investigate repeated reject patterns before enabling higher-risk interactive flows.

### 4.2 Multi-tenant Boundary Model (Fail-Closed)

When `OPENCLAW_MULTI_TENANT_ENABLED=1`, OpenClaw enforces explicit tenant boundaries across API and service paths.

Boundary rules:

- tenant context is resolved from token context and/or tenant header (`X-OpenClaw-Tenant-Id` by default)
- token/header mismatch is rejected (`tenant_mismatch`)
- connector installation diagnostics/resolution, config read/write, approvals, presets, template visibility, and secret lookup are tenant-scoped
- execution budgets add per-tenant concurrency enforcement (`OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT`)

Compatibility note:

- current admin/API handlers default missing tenant context to `default` for backward compatibility; stricter caller paths can enforce explicit tenant presence.

Compatibility toggles (use only during migration windows):

- `OPENCLAW_MULTI_TENANT_ALLOW_DEFAULT_FALLBACK=1`
- `OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK=1`
- `OPENCLAW_MULTI_TENANT_ALLOW_LEGACY_SECRET_FALLBACK=1`

Security recommendation:

- keep all fallback toggles disabled for steady-state multi-tenant production.

### 4.3 Optional Local Secret-manager Path (1Password CLI)

If `OPENCLAW_1PASSWORD_ENABLED=1`, provider key lookup can use local 1Password CLI as an optional backend source.

Fail-closed requirements:

- `OPENCLAW_1PASSWORD_ALLOWED_COMMANDS` must include the command basename in use
- `OPENCLAW_1PASSWORD_VAULT` and `OPENCLAW_1PASSWORD_FIELD` must be valid
- `OPENCLAW_1PASSWORD_ITEM_TEMPLATE` must include `{provider}`
- when multi-tenant mode is enabled, the template must also include `{tenant}`

Operational note:

- this path remains backend-only; frontend surfaces stay secret-blind.

### 5. Startup Gate Behavior

Startup security gates are fail-closed. Fatal startup gate/bootstrap failures abort route/worker registration and do not continue in a partial state.

Recommended preflight:

```bash
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

### 6. SSRF Protection

OpenClaw validates custom LLM `base_url` settings to prevent Server-Side Request Forgery (SSRF).
Webhook and trigger callbacks are delivered only to hosts listed in `OPENCLAW_CALLBACK_ALLOW_HOSTS` and pass the same private/reserved-IP checks.

* **Default**: known providers and localhost-safe paths are allowed.
* **Pinned connect contract**: on supported CPython versions (current baseline: 3.10+), the consolidated `safe_io` outbound executor dials resolved IPs directly for HTTP/HTTPS and keeps TLS `server_hostname` on the original host; the no-skip `tests.test_s70_ssrf_pinning_regression` lane is intended to fail loudly if stdlib connect behavior drifts.
* **Redirect handling**: redirect targets are revalidated against host allowlists, private/reserved-IP blocking, and pinned-connect rules before any follow-up connection is opened.
* **Custom base URL**:
  - requires explicit opt-in:

    ```bash
    export OPENCLAW_ALLOW_CUSTOM_BASE_URL=1
    # Legacy compatibility (optional):
    # export MOLTBOT_ALLOW_CUSTOM_BASE_URL=1
    ```
  - use strict allowlist:
    ```bash
    export OPENCLAW_LLM_ALLOWED_HOSTS="api.example.com,llm.example.com"
    ```
  - `OPENCLAW_LLM_ALLOWED_HOSTS` only permits additional exact public hosts; it does not bypass the private/reserved-IP block.
  - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1` widens to any public host only.
  - `allow_private_network=true` on the LLM setting allows only the configured provider `base_url` host to resolve to a private/reserved IP while keeping exact-host allowlists, scheme/port checks, and DNS pinning.
  - `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` is the explicit risk-acceptance override for HTTP or private/reserved IP targets.
  - the same scoped/private or insecure decision is enforced consistently for config validation, `/openclaw/llm/models`, and outbound provider requests.
  - wildcard values such as `OPENCLAW_LLM_ALLOWED_HOSTS="*"` are not supported.
  - avoid broad bypass flags in production (`OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST`, `OPENCLAW_ALLOW_INSECURE_BASE_URL`).

### 6.1 Audit Chain Verification

OpenClaw keeps append-only audit evidence verifiable across restart and retained-log rotation.

Recommended operator check:

```bash
python scripts/verify_audit_chain.py --json
```

Notes:

- verification covers the active `audit.log` plus retained rotated segments in the state directory
- when an audit chain key is not supplied externally, OpenClaw persists a local `audit.log.key` sidecar so the retained chain stays verifiable after restart
- treat any verification failure as an integrity incident and investigate before trusting the retained audit trail

### 7. Rate Limiting

OpenClaw enforces internal rate limits:

* Webhooks: 30/min
* Logs: 60/min
* Admin: 20/min

### 8. Sidecar Bridge

OpenClaw supports a "Sidecar Bridge" for safe interaction with external bots (Discord/Slack).

* **Default**: **DISABLED**.
* **Enable**: Set `OPENCLAW_BRIDGE_ENABLED=1` (legacy `MOLTBOT_BRIDGE_ENABLED=1`).
* **Authentication**: Requires `OPENCLAW_BRIDGE_DEVICE_TOKEN` (legacy `MOLTBOT_BRIDGE_DEVICE_TOKEN`) (shared secret).
* **Network**: Bridge endpoints (`/bridge/*`) are sensitive. **Do not expose to public internet.** Use a private network (Tailscale) or restrict access via reverse proxy.
* **SSRF**: Callback delivery blocks internal IPs. To allow specific external callback hosts, set `OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST` (legacy: `MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST`).

## Security Checklist

* [ ] **HTTPS + Edge Auth**: reverse proxy enforces TLS and an additional auth boundary (SSO/Basic/IP ACL).
* [ ] **No direct public bind**: never expose raw ComfyUI/OpenClaw listener directly.
* [ ] **Token boundaries**: set `OPENCLAW_ADMIN_TOKEN` and `OPENCLAW_OBSERVABILITY_TOKEN` (legacy aliases acceptable).
* [ ] **Trusted proxy config**: set `OPENCLAW_TRUST_X_FORWARDED_FOR=1` and exact `OPENCLAW_TRUSTED_PROXIES`.
* [ ] **Public shared-surface ack**: for `OPENCLAW_DEPLOYMENT_PROFILE=public`, set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after proxy path allowlist + ACL are verified.
* [ ] **Public path deny rules**: block ComfyUI-native high-risk routes and `/api/*` equivalents unless explicitly required.
* [ ] **Connector strict-posture allowlists**: if connector ingress is active in `public` or `hardened`, ensure platform allowlists are set before startup (`DP-PUBLIC-009` for public profile).
* [ ] **External tools disabled by default**: keep `OPENCLAW_ENABLE_EXTERNAL_TOOLS=0` unless there is a reviewed need; if enabled, verify the tool allowlist, sandbox policy, and deterministic sandbox/interpreter/timeout/workspace diagnostics.
* [ ] **Multi-tenant boundary (if enabled)**: enforce one canonical tenant header path through proxy/app, keep fallback toggles disabled unless a migration window is actively in progress.
* [ ] **Audit integrity check**: run `python scripts/verify_audit_chain.py --json` after restart/rotation-sensitive maintenance and confirm retained audit logs still verify cleanly.
* [ ] **1Password guardrails (if enabled)**: require command allowlist + vault/template validation; in multi-tenant mode, include `{tenant}` in item template.
* [ ] **Startup gate preflight**: run `python scripts/check_deployment_profile.py --profile public --strict-warnings`.
* [ ] **Runtime diagnostics**: review `GET /openclaw/security/doctor` before exposure.
* [ ] **Least privilege host posture**: do not run as root/Administrator.
