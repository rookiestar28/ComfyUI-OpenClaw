# Threat Model & Trust Boundaries

This document outlines the security assumptions and trust boundaries for **ComfyUI-OpenClaw**.
Operators should use this to understand the risks of deployment.

## Trust Boundaries

### 1. The "Admin" Boundary

* **Who**: The person running ComfyUI (you).
* **Access**: Full filesystem access, process execution, and secret management.
* **Mechanism**: OS-level permissions + `OPENCLAW_CONNECTOR_ADMIN_TOKEN` (if remote).
* **Risk**: If compromised, attacker owns the machine.

### 2. The "Observability" Boundary

* **Who**: Monitoring tools or trusted dashboards.
* **Access**: Read-only logs (`/openclaw/logs/tail`), config (`/openclaw/config`), health.
* **Mechanism**: `OPENCLAW_OBSERVABILITY_TOKEN`.
* **Redaction**: Logs/Config are redacted by default to prevent secret leakage.
* **Reasoning/internal-content posture**: provider reasoning / thinking traces and explicitly marked internal maintenance/helper prompt content are stripped by default from operator-visible assist responses, event streams, trace responses, callback payloads, connector trace replies, and audit event payload/meta fields. Privileged reasoning reveal is local-debug only, admin-gated, auditable, and fail-closed outside permissive local posture; internal maintenance/helper prompt content has no public or debug reveal path.

### 3. The "Connector" Boundary (ChatOps)

* **Who**: Chat users (Telegram/Discord/LINE).
* **Access**:
  * **User**: `submit_job` (via Allowlisted templates), `query_status`.
  * **Admin (Chat)**: `approve_request`, `cancel_job`, `trace`, and privacy-minimized
    `list_jobs` summaries.
* **Mechanism**: Chat platform auth + OpenClaw User Allowlist (or `require_approval` policy).
* **Risk**: Spam/DoS (mitigated by Budgets + Rate Limits), Prompt Injection (mitigated by
  Template Constraints), or job metadata disclosure (mitigated by Admin-only authorization,
  allowlisted bounded fields, content-free errors, and keeping raw jobs payloads out of the
  chat LLM).

---

## Attack Surfaces

### Inbound (Server)

* **HTTP API**: `/openclaw/*`, `/moltbot/*`.
  * *Mitigation*: Loopback-only by default. Token auth for remote admin/observability.
* **Shared listener surface (OpenClaw + ComfyUI)**:
  * *Risk*: protecting `/openclaw/*` alone may still leave ComfyUI-native routes reachable when public proxy policy is broad.
  * *Mitigation*: enforce reverse-proxy path allowlist + network ACL; in public profile set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after those controls are verified.
* **Webhooks**: `/openclaw/webhook/*`.
  * *Mitigation*: Signature verification (HMAC) + Replay protection + Auth Token.

### Outbound (Client)

* **LLM Requests**: `POST` to `base_url`.
  * *Risk*: SSRF (Server-Side Request Forgery) to internal network.
  * *Mitigation*: Known-host allowlist by default. Custom URLs need explicit opt-in + DNS validation.
* **Callback Delivery**: `POST`/`PUT` results to webhook, trigger and approved-trigger callback URLs.
  * *Risk*: SSRF / Information Leakage.
  * *Mitigation*: exact-host allowlist (`OPENCLAW_CALLBACK_ALLOW_HOSTS`; none means no delivery) + DNS-safe validation (no private IPs) + operator-payload redaction, including reasoning/internal-content stripping by default. Split control-plane mode refuses trigger callbacks.
* **Scheduled Result Delivery**: the connector claims and acknowledges schedule deliveries through admin-plane routes and sends results to schedule-configured chat targets.
  * *Risk*: results reaching an unintended chat.
  * *Mitigation*: connector admin token required; platform chat/channel allowlists restrict targets; lease tokens are single-use and stored only as hashes.
* **Image Fetching**: `image_url` inputs.
  * *Mitigation*: SafeIO module (size limits, no file://).

---

## Assumptions

1. **Transport Security**: We assume HTTPS (TLS) is provided by a reverse proxy or tunnel (Tailscale/Cloudflare). OpenClaw serves HTTP.
2. **Local Host Security**: We assume the host machine is not already compromised.
3. **Secret Integrity**: Secrets in `os.environ` or `.env` are secure from non-admin users.

## "Red Lines" (Do Not Cross)

* **Never** expose the raw ComfyUI port (8188) to the public internet.
* **Never** run OpenClaw as `root` / Administrator.
* **Never** disable `OPENCLAW_CONNECTOR_ADMIN_TOKEN` on a publicly accessible instance.
