# Feature Flag Policy

This document lists the feature flags that control **risky capabilities** in ComfyUI-OpenClaw.
Users should audit these flags before deploying to a public or untrusted network.

> [!WARNING]
> Enabling these flags increases the attack surface. Ensure you have read the [Security Policy](../SECURITY.md) and use appropriate network controls (e.g., Tailscale, Reverse Proxy with Auth).

## Risk Levels

- **Low**: Safe for most deployments.
- **Medium**: Exposure risk if misconfigured; requires token auth.
- **High**: Significant risk; enables remote execution or bypasses safety checks.

---

## Runtime Flags

| Flag | Default | Risk | Description |
| :--- | :--- | :--- | :--- |
| `OPENCLAW_CONNECTOR_ADMIN_TOKEN` | *None* | **Medium** | Required for admin commands (stop/approve/trace) and for scheduled-run delivery. If missing, admin commands fail safe and the connector does not start the delivery worker; pending deliveries expire after 24 hours. |
| `OPENCLAW_ALLOW_REMOTE_ADMIN` | `0` | **High** | Be careful! Allows admin actions from non-loopback IPs if token is present (including writes from `/openclaw/admin` remote console). Default is loopback-only for admin. |
| `OPENCLAW_ENABLE_EXTERNAL_TOOLS` | `0` | **High** | Enables admin-gated external tool listing/execution routes. Requires reviewed tool allowlist and sandbox policy; keep disabled on public surfaces unless explicitly justified. |
| `OPENCLAW_BRIDGE_ENABLED` | `0` | **High** | Enables the sidecar bridge for remote orchestration. Requires `OPENCLAW_BRIDGE_DEVICE_TOKEN` (and in public posture also mTLS + device allowlist controls). |
| `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` | `0` | **High** | Bypasses the known-host allowlist for LLM `base_url`. Allows SSRF to public IPs. |
| `OPENCLAW_LLM_ALLOW_PRIVATE_NETWORK` | `0` | **High** | Allows the configured LLM `base_url` host to resolve to private/reserved IPs while preserving exact-host scope and DNS pinning. |
| `OPENCLAW_ALLOW_INSECURE_BASE_URL` | `0` | **Critical** | Allows HTTP (non-HTTPS) or private IP `base_url` for LLM. Risk of internal network scanning (SSRF). |
| `OPENCLAW_MULTI_TENANT_ENABLED` | `0` | **High** | Enables fail-closed tenant boundary mode. Requests without valid tenant context can be rejected by design. |
| `OPENCLAW_TENANT_HEADER` | `X-OpenClaw-Tenant-Id` | **Low** | Customizes tenant header extraction key for multi-tenant mode. Keep stable across all clients/proxies. |
| `OPENCLAW_MULTI_TENANT_ALLOW_DEFAULT_FALLBACK` | `0` | **High** | Weakens strict tenant requirement by allowing implicit default tenant fallback when context is missing. |
| `OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK` | `0` | **Medium** | Allows tenant config lookup to fall back to global config when tenant branch is absent. |
| `OPENCLAW_MULTI_TENANT_ALLOW_LEGACY_SECRET_FALLBACK` | `0` | **Medium** | Allows tenant secret lookup to fall back to legacy unscoped secret keys during migration. |
| `OPENCLAW_1PASSWORD_ENABLED` | `0` | **Medium** | Enables optional local 1Password CLI key lookup. Requires explicit command allowlist (`OPENCLAW_1PASSWORD_ALLOWED_COMMANDS`) and vault config; fail-closed when misconfigured. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT` | `1` | **Low** | Per-tenant execution concurrency cap used only in multi-tenant mode. |
| `OPENCLAW_LOG_TRUNCATE_ON_START` | `0` | **Low** | Operational log hygiene toggle. If `1`, truncates active `openclaw.log` at startup (once per process). |
| `OPENCLAW_CONNECTOR_DISCORD_TOKEN` | *None* | **Medium** | Presence enables Discord Bot gateway. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` | *None* | **Medium** | Presence enables LINE webhook listener. Requires a public HTTPS endpoint. |
| `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN` | *None* | **Low** | Presence enables Telegram long-polling. Outbound only. |
| `OPENCLAW_OBSERVABILITY_TOKEN` | *None* | **Low** | Protects `/openclaw/logs/tail` and `/openclaw/config`. Recommended for all remote deployments. |

---

## Enabling Policy

1. **Documentation**: Any PR adding a new risky flag MUST update this table.
2. **Default**: All high-risk flags MUST default to `0` (Disabled).
3. **Validation**: CI tests should run with default flags (secure) and verify risky features are unreachable.
