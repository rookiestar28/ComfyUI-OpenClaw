# Security Deployment Guide

This guide is the single entry point for secure deployment of ComfyUI-OpenClaw across three deployment profiles:

- `local`: single-user localhost
- `lan`: private LAN / trusted subnet
- `public`: internet-facing behind reverse proxy

Use this guide together with:

- [SECURITY.md](SECURITY.md)
- `docs/deploy/local-only.md`
- `docs/deploy/lan.md`
- `docs/deploy/reverse-proxy.md`
- `docs/security_checklist.md`

## 0. Pre-deployment Disclaimer (Public Exposure)

Before using OpenClaw in any internet-facing setup, you must explicitly accept:

1. This project is local-first by design; exposing it to public networks increases attack surface.
2. This guide reduces risk but does not guarantee security, compliance, or incident-free operation.
3. The operator/deployer is responsible for network isolation, auth boundaries, key management, monitoring, and incident response.
4. If you cannot satisfy the `public` profile baseline and checklist, do not deploy publicly. Use `local` or private/VPN-only access instead.
5. High-risk capabilities (external tools, registry sync, transforms, remote admin) must remain disabled unless there is a reviewed and time-bounded operational requirement.
6. If external tools are enabled, review the definitions in the file named by `OPENCLAW_TOOLS_CONFIG_PATH` (the shipped `data/tools_allowlist.json` is empty), the sandbox policy, and runtime diagnostics before exposing the deployment beyond localhost.

## 0.1 Shared-Port Boundary Statement (Critical)

OpenClaw runs inside ComfyUI and shares the same HTTP listener/port.

This means:

1. Protecting `/openclaw/*` endpoints does **not** automatically protect ComfyUI native endpoints.
2. If your public edge forwards raw ComfyUI upstream traffic broadly, attackers may still reach native ComfyUI surfaces.
3. Public deployment must enforce path-level allow/deny policy at reverse proxy (and network ACL), not just OpenClaw tokens.

High-risk ComfyUI-native paths to explicitly deny on public edges (unless intentionally required):

- direct paths: `/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`
- API-shim paths: `/api/prompt`, `/api/history*`, `/api/view*`, `/api/upload*`, `/api/ws`

Notes:

- Exact ComfyUI route shape can vary by version and shim behavior. Use deny rules that cover both direct and `/api/*` forms.
- If you intentionally expose full ComfyUI UI to users, apply a separate hardened admin/user plane design and do not rely on OpenClaw route auth alone.

## 1. Profile Matrix

| Profile | Intended Use | Minimum Security Baseline |
|---|---|---|
| `local` | single operator on same machine | no remote admin, no proxy trust, high-risk features disabled unless explicitly needed |
| `lan` | trusted private network | admin + observability token, webhook auth + replay protection, remote admin opt-in, risky features off |
| `public` | internet-facing reverse proxy | strict token boundaries, trusted proxy config, remote admin off, control-plane split required, risky features off, webhook auth fail-closed, connector allowlist fail-closed |

## 2. Self-check Command

Validate current environment variables against a deployment profile:

```bash
python scripts/check_deployment_profile.py --profile local
python scripts/check_deployment_profile.py --profile lan
python scripts/check_deployment_profile.py --profile public
```

Machine-readable output:

```bash
python scripts/check_deployment_profile.py --profile public --json
```

The command exits with non-zero status when policy failures are found.
Use `--strict-warnings` if you want warnings to fail the check in hardened pipelines:

```bash
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

Optional operational log hygiene (all profiles):

```bash
# Clear active openclaw.log once at startup (useful to avoid stale UI log noise)
OPENCLAW_LOG_TRUNCATE_ON_START=1
```

## 3. Local (Single-user)

### 3.1 Pasteable config template

```bash
# Recommended baseline
OPENCLAW_ALLOW_REMOTE_ADMIN=0
OPENCLAW_TRUST_X_FORWARDED_FOR=0
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_DEBUG_REASONING_REVEAL=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0
OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0

# Optional but recommended
OPENCLAW_ADMIN_TOKEN=change-this-local-admin-token
# Optional local CLI compatibility only (do not enable on LAN/public):
# OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=true
# Optional startup log hygiene
# OPENCLAW_LOG_TRUNCATE_ON_START=1
```

### 3.2 Checklist

1. Keep ComfyUI bound to localhost only.
2. Keep remote admin disabled.
3. Keep external tools/registry sync/transforms disabled unless explicitly needed.
4. For local LLM providers (Ollama/LM Studio), use loopback URLs only (`localhost`/`127.0.0.1`/`::1`); keep `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0` and `OPENCLAW_ALLOW_INSECURE_BASE_URL=0`.
5. `OPENCLAW_LLM_ALLOWED_HOSTS` is only for additional exact public hosts; it does not permit RFC1918/private LAN targets. Use the scoped `allow_private_network` LLM setting only when a reviewed local deployment needs a private target.
6. The same LLM SSRF contract applies consistently to config validation, `/openclaw/llm/models`, and outbound provider requests.
7. Keep `OPENCLAW_DEBUG_REASONING_REVEAL=0` unless you are doing short-lived local admin debugging and explicitly need privileged reasoning reveal.
8. Keep `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0` unless you explicitly need local CLI/no-origin compatibility.
9. Run:
   - `python scripts/check_deployment_profile.py --profile local`
10. If you enable optional high-risk features, document why and time-box the change.

## 4. LAN (Trusted Subnet)

### 4.1 Pasteable config template

```bash
OPENCLAW_ADMIN_TOKEN=change-this-admin-token
OPENCLAW_OBSERVABILITY_TOKEN=change-this-obs-token

# LAN mode allows remote admin intentionally, but only inside trusted network
OPENCLAW_ALLOW_REMOTE_ADMIN=1

# Webhook auth must be explicit
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=change-this-hmac-secret
OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1

# Keep risky expansion surfaces off by default
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_DEBUG_REASONING_REVEAL=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0
OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0
# Optional startup log hygiene
# OPENCLAW_LOG_TRUNCATE_ON_START=1
```

### 4.2 Checklist

1. Restrict host firewall to trusted LAN subnets only.
2. Use distinct admin and observability tokens.
3. Keep bridge/tools/registry/transforms disabled unless there is a reviewed requirement.
4. Keep `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0` for LAN deployments.
5. If your LLM is on another LAN/private-IP host, `OPENCLAW_LLM_ALLOWED_HOSTS` alone is not sufficient; use the scoped `allow_private_network` LLM setting for that configured target, or the broader insecure override only after review.
6. The same LLM SSRF contract applies consistently to config validation, `/openclaw/llm/models`, and outbound provider requests.
7. Run:
   - `python scripts/check_deployment_profile.py --profile lan`
8. Run the security diagnostics endpoint before production use:
   - `GET /openclaw/security/doctor` (admin boundary).
9. If using mobile/remote admin UI, expose `/openclaw/admin` only inside trusted LAN/VPN boundaries.

## 5. Public (Internet + Reverse Proxy)

### 5.1 Pasteable config template

```bash
OPENCLAW_ADMIN_TOKEN=change-this-admin-token
OPENCLAW_OBSERVABILITY_TOKEN=change-this-obs-token

# Public baseline: do not expose remote admin directly
OPENCLAW_ALLOW_REMOTE_ADMIN=0

# Public shared-surface boundary acknowledgement (S69).
# Set only after reverse-proxy path allowlist + network ACL deny ComfyUI-native
# high-risk paths and /api equivalents.
OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1

# Public baseline: enforce split control plane (S62/R106)
OPENCLAW_CONTROL_PLANE_MODE=split
OPENCLAW_CONTROL_PLANE_URL=https://control-plane.internal
OPENCLAW_CONTROL_PLANE_TOKEN=change-this-control-plane-token
OPENCLAW_CONTROL_PLANE_TIMEOUT=10

# Trust only known reverse proxy addresses
OPENCLAW_TRUST_X_FORWARDED_FOR=1
OPENCLAW_TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8

# Webhooks must be authenticated and replay-protected
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=change-this-hmac-secret
OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1

# Optional callback path must be tightly allowlisted (webhook callbacks; split mode refuses trigger callbacks)
OPENCLAW_CALLBACK_ALLOW_HOSTS=example.com,api.example.com

# If connector ingress is enabled in public posture, set platform allowlists.
# Otherwise startup/deployment checks fail closed.
# Example (Telegram):
# OPENCLAW_CONNECTOR_TELEGRAM_TOKEN=...
# OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS=123456

# Keep risky expansion surfaces off on public user plane
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_DEBUG_REASONING_REVEAL=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0
OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0
# Optional startup log hygiene
# OPENCLAW_LOG_TRUNCATE_ON_START=1
```

### 5.2 Checklist

1. Never expose raw ComfyUI port directly to the internet.
2. Enforce authentication at reverse proxy and application layers.
3. Enforce path-level boundary controls at reverse proxy:
   - allow only required OpenClaw routes
   - deny ComfyUI-native high-risk paths (`/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`) and `/api/*` equivalents.
4. Set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after step 3 and network ACL hardening are in place.
5. Enforce split control plane in public posture (`OPENCLAW_CONTROL_PLANE_MODE=split` + external URL/TOKEN).
6. Keep `OPENCLAW_DEBUG_REASONING_REVEAL=0`; privileged reasoning reveal is for local debugging only and must not be enabled on public user planes.
7. If any connector platform token/enable flag is configured, set corresponding platform allowlist vars before startup (`DP-PUBLIC-009` fail-closed).
8. For connector approvals/actions, verify duplicate platform retries do not re-run completed actions and text-only reply suppression does not hide approval/action controls.
9. Keep risky features disabled on public user-facing plane.
10. Keep `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0` in public deployments.
11. Verify split posture from capabilities:
   - `GET /openclaw/capabilities` and confirm `control_plane.mode=split`
12. Run:
    - `python scripts/check_deployment_profile.py --profile public`
13. Validate with project test and release gates before rollout:
    - `tests/TEST_SOP.md`
    - [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)
14. Ensure `/openclaw/admin` is blocked at public edge unless a separately hardened private admin plane is in place.

## 6. Bridge in Public Profile (only when absolutely required)

If bridge must be enabled in public profile, apply all of the following:

```bash
OPENCLAW_BRIDGE_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_TOKEN=change-this-bridge-token
OPENCLAW_BRIDGE_MTLS_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_CERT_MAP=device-a:sha256fingerprint
OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS=device-a
```

Also run:

```bash
python scripts/check_deployment_profile.py --profile public
```

The check fails if bridge is enabled without the mTLS/device-binding bundle.

## 7. Operational Red Lines

1. Do not use localhost convenience mode for shared/LAN/public deployments.
2. Do not enable `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=true` in shared/LAN/public deployments.
3. Do not enable `OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE` in production.
4. Do not enable `OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE` in production.
5. Do not enable `OPENCLAW_SPLIT_COMPAT_OVERRIDE` in production.
6. Do not enable external tools/registry sync/transforms on public user-facing plane by default.
7. Do not use wildcard-like trust posture for callback destinations.
8. Do not treat this as a "set and forget" deployment; re-run profile checks after every config change.

## 8. Mechanical Gate Integration (Startup + CI)

### 8.1 Startup gate command

Use this before route registration in hardened deployments:

```bash
python scripts/check_deployment_profile.py --profile "${OPENCLAW_DEPLOYMENT_PROFILE:-local}" --strict-warnings
```

If the command exits non-zero, startup should fail closed.
This validates deployment-profile posture (S56 baseline), but does not replace split-prerequisite checks.

### 8.2 CI gate command

Run all three profile checks in CI using fixture env files:

```bash
python scripts/check_deployment_profile.py --profile local --strict-warnings
python scripts/check_deployment_profile.py --profile lan --strict-warnings
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

Pair this with `tests/TEST_SOP.md` so deployment posture checks are validated alongside unit/E2E regressions.

### 8.3 Public split verification (S62)

For public deployments, also verify control-plane split prerequisites and active mode:

1. Confirm startup has no S62 fatal errors (missing `OPENCLAW_CONTROL_PLANE_URL` / `OPENCLAW_CONTROL_PLANE_TOKEN`).
2. Confirm runtime capability view reports split mode:
   - `GET /openclaw/capabilities`
   - `control_plane.mode` must be `split`

## 9. Public MAE Hard Guarantee

Public MAE enforcement is not only a route-registration rule. It is a startup + CI guarantee:

1. Startup gate blocks public/hardened posture violations before serving routes.
2. Route-plane classification drift tests fail when new endpoints are not classified.
3. CI runs MAE-critical suites as explicit no-skip gates:
   - `tests.test_s60_mae_route_segmentation`
   - `tests.test_s60_routes_startup_gate`
   - `tests.security.test_endpoint_drift`

Do not remove these suites from CI or skip-policy protection.

## 10. Key and Token Lifecycle Operations

Operational procedures for rotation/revocation/disaster recovery are documented in:

- `docs/security_key_lifecycle_sop.md`

Coverage includes trust-root/signer revocation, secrets-at-rest key lifecycle, and bridge token rotation.

This runbook is required for long-running public deployments and incident response readiness.
