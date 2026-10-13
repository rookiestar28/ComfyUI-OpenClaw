# OpenClaw API Contract (v1)

> **Status**: normative
> **Version**: 1.0.15
> **Date**: 2026-07-10

This document defines the public API contract for OpenClaw. It serves as the authoritative baseline for client compatibility and breaking change policies.

## 0. Tenant Boundary Context

Default behavior remains single-tenant compatible (`tenant_id=default`).

When `OPENCLAW_MULTI_TENANT_ENABLED=1`:

- tenant context may be supplied by token context and/or request header (`X-OpenClaw-Tenant-Id`, configurable)
- token/header mismatch is rejected with `403` (`tenant_mismatch`)
- connector installation resolution is tenant-scoped and rejects cross-tenant matches fail-closed (diagnostic conflict path)
- current admin/API handlers keep compatibility behavior by defaulting missing tenant context to `default` tenant

## 1. Route Inventory

All new integrations should use the `/openclaw/` prefix. Use of `/moltbot/` is deprecated.

### 1.0 UI Entry Points

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/admin` | `/moltbot/admin` | None* | Standalone remote admin console HTML shell (mobile-friendly). |

`*` The page shell can be loaded directly, but all backend write operations from the console still enforce Admin token and remote-admin policy.

### 1.1 Core Observability & System

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/health` | `/moltbot/health` | None | System status, uptime, dependencies, and startup lifecycle diagnostics. |
| `GET` | `/capabilities` | `/moltbot/capabilities` | None | Feature flags and supported extensions (includes optional UX/runtime features such as assist streaming support). |
| `GET` | `/logs/tail` | `/moltbot/logs/tail` | Observability | Tail recent log lines (rate-limited). |
| `GET` | `/trace/{prompt_id}` | `/moltbot/trace/{id}` | Observability | Get execution trace by prompt ID. |
| `GET` | `/events` | `/moltbot/events` | Observability | List recent job lifecycle events (JSON polling fallback; includes pagination/scan diagnostics). |
| `GET` | `/events/stream` | `/moltbot/events/stream` | Observability | SSE stream of job lifecycle events with resume support. |
| `GET` | `/config` | `/moltbot/config` | Observability | Read-only view of sanitized provider config. |
| `PUT` | `/config` | `/moltbot/config` | Admin | Update system configuration. |
| `GET` | `/jobs` | `/moltbot/jobs` | Admin | List recent jobs through the versioned bounded in-process jobs read model. |
| `POST` | `/preflight` | `/moltbot/preflight` | Admin | Analyze a workflow or API prompt payload for missing nodes/models and portability diagnostics. |
| `GET` | `/preflight/inventory` | `/moltbot/preflight/inventory` | Admin | Snapshot-first inventory of nodes/models for operator diagnostics, including refresh-state metadata. |

Jobs list contract:

- `GET /openclaw/jobs` and its browser/legacy aliases are Admin-only and return
  `contract_version: 1` from the bounded in-process ComfyUI jobs adapter.
- Supported query fields are `status`, `workflow_id`, `sort_by`, `sort_order`, `limit`,
  and `offset`. Status values are `pending`, `in_progress`, `completed`, `failed`, and
  `cancelled`; sorting supports `created_at` or `execution_duration` with `asc`/`desc`.
- The default/maximum page sizes are 50/200 and the source/offset window is capped at
  10,000. Successful responses contain only `ok`, `contract_version`, `jobs`,
  `pagination`, `source`, and `scan` at the top level.
- Job summaries allow only `id`, `status`, bounded priority/timestamps, `outputs_count`,
  and bounded `workflow_id`. `preview_output` is never included, and list responses never
  include raw prompts, workflows, execution errors, tracebacks, current inputs/outputs,
  tenant/client/trace identifiers, reasoning, or internal content.
- An authoritative empty snapshot is HTTP 200 with `jobs: []`. Missing host helpers use
  HTTP 501 `jobs_host_contract_unsupported`; unavailable or malformed snapshots use HTTP
  503 `jobs_backend_unavailable`. These failures are never converted into empty success.

Reasoning-content redaction contract:

- operator-visible trace and events payloads strip provider reasoning / thinking-like fields and explicitly marked internal maintenance/helper content by default
- audit event payload/meta fields follow the same internal-content and reasoning-like redaction boundary before retention
- privileged reveal is opt-in only and requires:
  - request header `X-OpenClaw-Debug-Reveal-Reasoning: 1` or query `debug_reasoning=1`
  - server-side enablement via `OPENCLAW_DEBUG_REASONING_REVEAL=1`
  - admin authorization
  - loopback source
  - non-hardened runtime profile
  - deployment profile `local` or `lan`
- clients MUST treat reveal behavior as debug-only and MUST NOT depend on reasoning payload presence in normal operation
- explicitly marked internal maintenance/helper content has no public or debug reveal path

Inventory diagnostics contract:

- `/preflight/inventory` is snapshot-first and may return before deep scan work finishes
- clients SHOULD treat `snapshot_ts`, `scan_state`, `stale`, and `last_error` as first-class diagnostics fields rather than assuming a blocking full-rescan model

Preflight workflow diagnostics contract:

- `POST /openclaw/preflight` accepts both API prompt dictionaries and frontend workflow JSON when supplied by operator tooling
- response summaries distinguish actionable `missing_nodes` / `missing_models` from `suppressed_missing_nodes` / `suppressed_missing_models`
- suppressed findings represent muted or bypassed root nodes or subgraph branches when the submitted workflow shape provides enough frontend ancestry metadata
- clients SHOULD display suppressed findings as informational context rather than blocking workflow readiness

History and output-ref contract:

- history/output consumers SHOULD treat the normalized output-ref contract as media-aware
- current previewable output groups are `images`, `video`, `audio`, `3d`, bounded inline `text`, and allowlisted file-backed text refs from the host `files` key
- file-like refs that can be represented through `/view` remain on the bounded `/history` + `/view` preview path
- file-backed text admission is limited to `.txt`, `.md`, `.markdown`, `.json`, `.csv`, `.yaml`, `.yml`, `.xml`, and `.log`; clients MUST build the URL from normalized filename/subfolder/type fields rather than trust a history-provided URL
- browser text previews MUST remain same-origin `/view` GET requests, reject redirects and active/ambiguous MIME types, use strict UTF-8, stream at most 64 KiB within 5 seconds, display at most 4,096 characters, and degrade to a source link when safe streaming is unavailable
- fetched text MUST be inserted as literal text; HTML, Markdown, SVG/XML, ANSI, or script interpretation is not part of this contract
- `asset_hash` / `hash` metadata is optional because current ComfyUI host asset hashing is opt-in through `--enable-asset-hashing`; clients MUST NOT require hashes for normal filename-backed previews
- refs with `asset_hash` or `hash` values, when host metadata provides them, preview through `/view?filename=blake3:...`
- refs that only expose upstream asset-service identifiers remain explicit `asset_api_required` states; clients MUST NOT silently infer direct `/api/assets` fetching from that marker
- HDR `.exr` / `.hdr` image refs remain image refs but should be rendered as explicit source-preview fallback links unless the client implements a safe HDR-specific viewer
- legacy callback/image-only consumers may continue using image-only extraction paths; non-image media refs should be rendered as explicit fallback/link/text surfaces unless the client implements a safe media-specific renderer

### 1.2 Webhooks & Triggers

**Auth**: Requires configured webhook secret or Admin Token.

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/webhook` | `/moltbot/webhook` | Webhook Secret | Receive external alerts (schema validation only). |
| `POST` | `/webhook/submit` | `/moltbot/webhook/submit` | Webhook Secret | Validate and submit job from webhook payload. |
| `POST` | `/webhook/validate` | `/moltbot/webhook/validate` | Webhook Secret | Dry-run validation of webhook payload. |
| `POST` | `/triggers/fire` | `/moltbot/triggers/fire` | Admin | Fire an ad-hoc workflow trigger from external system. |

Trigger callbacks:

- `POST /triggers/fire` accepts an optional `callback` object with `url` (required, `http`/`https`, no embedded credentials, at most 2048 characters), `method` (`POST` or `PUT`), `headers` (at most 20 string pairs; routing and hop-by-hop headers such as `Host` are rejected) and `mode`. Other fields are rejected.
- An invalid `callback` returns HTTP `400` with code `callback_malformed`. When callback egress is blocked by the control plane (public profile in split mode), a request carrying `callback` returns the surface-guard `403` (`SURFACE_BLOCKED`). In both cases nothing is submitted and no approval is created.
- Results are delivered with the same executor, host allowlist (`OPENCLAW_CALLBACK_ALLOW_HOSTS`; no allowlist means no delivery), SSRF policy and retry budget as webhook callbacks.
- `scheduled` means the result watcher started, not that the host is allowlisted. The watcher polls history every 2 seconds for about five minutes; a job that finishes later gets no callback.
- Immediate executions that include a callback report `callback`: `scheduled`, `deduped` (a repeated trigger reuses the original delivery) or `not_submitted`. For approval-gated triggers, the callback is stored with the approval and dispatched when `POST /approvals/{id}/approve` executes it; that response reports `callback` as `scheduled`, `deduped`, `not_submitted`, `blocked` (egress blocked at execution time; the approved job still runs) or `invalid` (stored callback no longer passes validation).

ComfyUI prompt submission interoperability:

- OpenClaw-generated ComfyUI `/prompt` payloads include `extra_data.comfy_usage_source = "comfyui-openclaw"` when the caller has not supplied a value
- caller-provided `extra_data.comfy_usage_source` is preserved
- attribution is a stable product identifier and MUST NOT include prompt text, tenant ids, trace ids, URLs, tokens, or secrets
- existing `extra_data.openclaw` and legacy `extra_data.moltbot` metadata remain caller-owned except for OpenClaw tenant metadata insertion under `extra_data.openclaw.tenant_id`

### 1.3 Assist, LLM & Chat

**Assist Base Path**: `/openclaw/assist/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/assist/planner/profiles` | `/moltbot/assist/planner/profiles` | Admin/Local | List active planner profiles from registry for UI/node alignment. |
| `POST` | `/assist/planner` | `/moltbot/assist/planner` | Admin/Local | Planner structured prompt generation. |
| `POST` | `/assist/refiner` | `/moltbot/assist/refiner` | Admin/Local | Prompt refinement with optional image context. |
| `POST` | `/assist/planner/stream` | `/moltbot/assist/planner/stream` | Admin/Local | Optional SSE-style planner streaming response (`text/event-stream`) with staged progress + final payload. |
| `POST` | `/assist/refiner/stream` | `/moltbot/assist/refiner/stream` | Admin/Local | Optional SSE-style refiner streaming response (`text/event-stream`) with staged progress + final payload. |

Assist payload redaction contract:

- structured assist responses preserve final operator-visible answer fields but strip provider reasoning / chain-of-thought style fields and explicitly marked internal maintenance/helper content by default
- when the privileged reveal gate is allowed, debug reasoning is exposed only in a separate debug payload and not merged back into the normal structured answer fields
- explicitly marked internal maintenance/helper content is not exposed by the privileged reasoning reveal gate

### 1.3B Connector Installation Diagnostics

**Base Path**: `/openclaw/connector/`
**Auth**: Admin Token Required

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/connector/installations` | `/moltbot/connector/installations` | Admin | List redacted connector installations with lifecycle diagnostics. |
| `GET` | `/connector/installations/{installation_id}` | `/moltbot/connector/installations/{installation_id}` | Admin | Get one redacted connector installation record. |
| `GET` | `/connector/installations/resolve` | `/moltbot/connector/installations/resolve` | Admin | Run fail-closed workspace resolution diagnostics (`platform`, `workspace_id`). |
| `GET` | `/connector/installations/audit` | `/moltbot/connector/installations/audit` | Admin | List installation lifecycle audit evidence (redacted). |
| `GET` | `/connector/extraction-contract` | `/moltbot/connector/extraction-contract` | Admin | Get the machine-readable connector extraction recommendation, seam families, static service-env SecretRef propagation policy, and current blockers. |

Connector diagnostics contract notes:
- installation records may expose operator-safe health metadata under `installation.metadata.health` (for example `ok`, `invalid_token`, `revoked`, `degraded`) without exposing token material
- `/connector/installations` diagnostics may include aggregate `health_counts` in addition to lifecycle `status_counts`
- `/connector/installations/resolve` may expose a stable `health_code` alongside the legacy `reject_reason` so clients can distinguish `workspace_unbound` vs token-health failures without parsing status text
- `/connector/extraction-contract` is structural packaging metadata and static service-env SecretRef policy only; clients MUST NOT treat it as a live installation-health, live environment dump, or token-status feed

### 1.3C Model Management & Installations

**Base Path**: `/openclaw/`
**Auth**: Admin Token Required

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/models/search` | `/moltbot/models/search` | Admin | Search normalized model entries across managed installs and catalog sources. |
| `POST` | `/models/downloads` | `/moltbot/models/downloads` | Admin | Create a managed model download task with progress/cancel lifecycle. |
| `GET` | `/models/downloads` | `/moltbot/models/downloads` | Admin | List model download tasks with snapshot or delta cursor semantics (`since_seq`). |
| `GET` | `/models/downloads/{task_id}` | `/moltbot/models/downloads/{task_id}` | Admin | Get one model download task by id. |
| `POST` | `/models/downloads/{task_id}/cancel` | `/moltbot/models/downloads/{task_id}/cancel` | Admin | Cancel a queued or running model download task. |
| `POST` | `/models/import` | `/moltbot/models/import` | Admin | Import a completed managed download into the bounded install root after provenance and hash verification. |
| `GET` | `/models/installations` | `/moltbot/models/installations` | Admin | List managed model installations. |

Model-manager contract notes:
- `/models/downloads` supports `since_seq` cursor polling and may return deterministic delta metadata (`requested_since_seq`, `effective_since_seq`, `next_since_seq`, truncation/reset hints) alongside the task list
- `model_type` values SHOULD use current ComfyUI folder keys where applicable, including `text_encoders`, `diffusion_models`, `clip_vision`, `style_models`, `upscale_models`, `vae_approx`, `gligen`, `latent_upscale_models`, `hypernetworks`, `photomaker`, `model_patches`, `audio_encoders`, `background_removal`, `frame_interpolation`, `geometry_estimation`, `optical_flow`, and `detection`
- legacy aliases such as `ckpt`, `checkpoints`, `loras`, `controlnets`, `clip`, `text_encoder`, `unet`, `diffusion_model`, `upscale_model`, `latent_upscale_model`, `hypernetwork`, `model_patch`, and `audio_encoder` are normalized before filtering or import destination resolution
- current ComfyUI folder keys that are not managed model-file destinations fail closed for download creation: `configs` (configuration YAML), `diffusers` (folder-valued trees), `classifiers` (extensionless classifier artifacts), `custom_nodes` (executable plugin code), and `datasets` (user-managed training data)
- download creation requires structured provenance metadata (`publisher`, `license`, `source_url`) and a 64-char `expected_sha256`
- import keeps fail-closed destination/filename validation and re-checks the staged file hash before activation

### 1.3D LLM Management & Chat

**LLM Base Path**: `/openclaw/llm/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/chat` | `/moltbot/llm/chat` | Admin/Local | Unified chat interface for assistant interactions. |
| `POST` | `/test` | `/moltbot/llm/test` | Admin | Test LLM connectivity and configuration. |
| `GET` | `/models` | `/moltbot/llm/models` | Admin | List available models from configured provider. Request-time fetch uses the same SSRF contract as saved `base_url` validation, including scoped private-network allowance and the explicit insecure override for private-IP/HTTP targets. |

### 1.4 Templates & Assets

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/templates` | `/moltbot/templates` | Observability | List discovered template IDs and metadata. |
| `GET` | `/presets` | `/moltbot/presets` | Public/Admin* | List local presets (*depends on `OPENCLAW_PRESETS_PUBLIC_READ`). |
| `POST` | `/presets` | `/moltbot/presets` | Admin | Create a new preset. |
| `PUT` | `/presets/{id}` | `/moltbot/presets/{id}` | Admin | Update an existing preset. |
| `DELETE` | `/presets/{id}` | `/moltbot/presets/{id}` | Admin | Delete a preset. |
| `GET` | `/checkpoints` | `/moltbot/checkpoints` | Observability | List model checkpoints. |
| `POST` | `/checkpoints` | `/moltbot/checkpoints` | Admin | Create/copy a checkpoint. |
| `GET` | `/packs` | `/moltbot/packs` | Admin | List installed asset packs. |
| `POST` | `/packs/import` | `/moltbot/packs/import` | Admin | Import an asset pack (.zip). |
| `GET` | `/packs/export/...` | `/moltbot/packs/export...` | Admin | Download an asset pack. |

### 1.5 Schedules & Approvals

**Base Path**: `/openclaw/`
**Auth**: Admin Token Required

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/schedules` | List all schedules. |
| `POST` | `/schedules` | Create a new schedule. |
| `GET` | `/schedules/{id}` | Get schedule details. |
| `PUT` | `/schedules/{id}` | Update a schedule. |
| `DELETE` | `/schedules/{id}` | Delete a schedule. |
| `POST` | `/schedules/{id}/run` | Manually trigger a schedule. |
| `GET` | `/schedules/{id}/runs` | Get run history for a schedule. |
| `GET` | `/schedule-deliveries` | List schedule delivery intents (`state`, `platform` and `limit` filters). |
| `POST` | `/schedule-deliveries/claim` | Lease pending schedule deliveries for the listed connector platforms. |
| `POST` | `/schedule-deliveries/{delivery_id}/ack` | Record the outcome of a leased schedule delivery. |
| `GET` | `/approvals` | List pending approvals (includes pagination/scan diagnostics; bounded serialization scan on malformed records). |
| `POST` | `/approvals/{id}/approve` | Approve a pending request. |
| `POST` | `/approvals/{id}/reject` | Reject a pending request. |

Schedule `delivery` is normalized before persistence. Supported fields are
`platform`, `target_id` (legacy aliases such as `channel_id` are accepted),
`thread_id` (aliases such as `thread_ts`, `topic_id`, and `message_thread_id`
are accepted), `workspace_id`, `account_id`, `mode`, and `failure_alert`.
Omitting `delivery` on update preserves the existing target, `delivery: null`
clears it, and `{"enabled": false}` or `{"mode": "none"}` records explicit
no-delivery. Invalid delivery targets are rejected before persistence with
bounded codes: `delivery_malformed`, `delivery_ambiguous`, or
`delivery_unsupported`.

When a run with an enabled delivery target submits a job, the server records one
durable delivery intent per run (a retried tick reuses it). The chat connector
claims intents, sends the finished result through its own platform adapter, and
acknowledges the outcome; the ComfyUI process never holds chat credentials.

- Intent states: `pending`, `claimed`, and the terminal `sent` (handed to the
  platform adapter), `job_failed`, `timed_out`, `undeliverable`, and `expired`
  (never claimed within 24 hours). Reasons are bounded codes such as
  `job_error`, `result_timeout`, `target_not_allowed`, `platform_unavailable`,
  `connector_error`, and `no_connector_adapter` (`webhook` and `custom`
  targets).
- `claim` accepts `platforms` (`telegram`, `discord`, `slack`, `feishu`),
  `limit` (1-10), and `lease_sec` (60-7200). Each returned intent carries a
  one-time `lease_token`; an intent is offered again only after its lease
  expires.
- `ack` accepts `lease_token`, `outcome` (`sent`, `job_failed`, `timed_out`,
  `undeliverable`), and `reason`. Repeating the accepted acknowledgement returns
  `already_acknowledged`; a stale or wrong lease token returns `409`.
- Claim and ack use the connector rate-limit class; listing uses the admin
  class. Responses carry normalized target identifiers only.
- Run history (`GET /schedules/{id}/runs`, `GET /runs`) includes a `delivery`
  summary (`delivery_id`, `state`, `reason`) for runs that recorded an intent.
- A connector that stops after sending but before acknowledging can resend the
  result once the lease expires; chat platforms offer no idempotent send.

### 1.5A External Tools

**OpenClaw path prefix**: `/openclaw/`
**Legacy Base Path**: `/moltbot/`
**Auth**: Admin Token Required
**Feature flag**: `OPENCLAW_ENABLE_EXTERNAL_TOOLS=true`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/tools` | `/moltbot/tools` | Admin Token Required | List allowed external tools and their declared sandbox metadata. |
| `POST` | `/tools/{name}/run` | `/moltbot/tools/{name}/run` | Admin Token Required | Execute a named allowlisted external tool with validated arguments. |

Tool execution contract notes:

- tools are disabled unless `OPENCLAW_ENABLE_EXTERNAL_TOOLS` is truthy
- tool definitions load from package-owned `data/tools_allowlist.json` (shipped empty) unless `OPENCLAW_TOOLS_CONFIG_PATH` is set
- argument values that contain control characters, would start an argument with `-`, fail their pattern, or fall outside a declared filesystem allowlist are rejected before any process starts; the `error` text names the argument, not its value
- in public/split high-risk surfaces, tool execution can be blocked by the surface guard even when the feature flag is enabled
- execution responses preserve the current HTTP payload shape: failed runs return `ok=false`, `tool`, `error`, redacted `output`, `exit_code`, and `duration_ms`
- the service-level tool runner classifies common local failures with stable diagnostics such as `sandbox_runtime_unavailable`, `interpreter_missing`, `timeout`, and `workspace_violation`; clients should still follow this API document for the current HTTP response shape

### 1.6 Bridge (Sidecar)

**Base Path**: `/bridge/`
**Auth**: Bridge Auth (Device Check)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Bridge status and connectivity check. |
| `POST` | `/submit` | Submit job from sidecar to core. |
| `POST` | `/deliver` | Outbound delivery from core to sidecar (via callback). |

---

## 2. Status & Error Semantics

API responses MUST adhere to the following status codes and envelope format.

### 2.1 Standard Envelope

All JSON responses (success or error) share a common structure:

```json
{
  "ok": boolean,
  "error": "string (optional)",
  "detail": "string (optional)",
  "trace_id": "string (optional)",
  "data": { ... } // Success payload
}
```

### 2.2 Status Codes

| Code | Meaning | Usage |
| :--- | :--- | :--- |
| `200` | OK | Successful synchronous request. |
| `201` | Created | Resource created (schedules, presets). |
| `202` | Accepted | Async job submitted (pending execution or approval). |
| `400` | Bad Request | Schema validation failure, missing required fields. |
| `401` | Unauthorized | Missing or invalid authentication token. |
| `403` | Forbidden | Authenticated but permission denied (e.g., admin-only). |
| `404` | Not Found | Resource or route does not exist. |
| `409` | Conflict | Idempotency collision or state conflict. |
| `413` | Payload Too Large | Input size exceeds `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` or similar limits. |
| `429` | Too Many Requests | Rate limit or Execution Budget exceeded. |
| `500` | Internal Error | Unhandled server exception. |
| `501` | Not Implemented | Required current-host contract is unavailable (for example `jobs_host_contract_unsupported`). |
| `503` | Unavailable | Feature disabled or service not wired. |

Tenant-boundary error notes:
- tenant boundary violations use `403` with explicit codes such as `tenant_mismatch` and `tenant_invalid`.

### 2.3 SSE Endpoint Notes (Contractual Behavior)

- SSE endpoints return `Content-Type: text/event-stream`.
- Current SSE surfaces include:
  - `/openclaw/events/stream` (job lifecycle events)
  - optional `/openclaw/assist/planner/stream` and `/openclaw/assist/refiner/stream` (assist incremental preview path)
- Assist streaming emits event types from the following set:
  - `ready`
  - `stage`
  - `delta`
  - `final`
  - `error`
  - `keepalive`
- Clients MUST treat `final` as the source of truth for structured assist results. `delta` preview text is best-effort and may be truncated or differ from the final parsed payload.
- Event-stream and polling payloads redact provider reasoning / thinking traces and explicitly marked internal maintenance/helper content by default; reasoning reveal is debug-only and gated by the same privileged local-debug contract used by trace/assist surfaces.
- Clients SHOULD gracefully fall back to non-streaming assist endpoints when streaming capability is absent or streaming transport fails.

### 2.4 Pagination & Scan Diagnostics (Management Query Contract)

- `GET /openclaw/events` and `GET /openclaw/approvals` include deterministic pagination normalization.
- Responses may include `pagination` and `scan` diagnostic objects so clients/operators can detect:
  - normalized limit/offset/cursor values
  - stale/future cursor resets
  - bounded scan truncation or malformed-record skips
- `GET /openclaw/preflight/inventory` may return snapshot-state diagnostics so clients/operators can detect:
  - whether the response came from a previous cached snapshot
  - whether a background refresh is currently running
  - whether the current snapshot is stale or the last deep scan failed
- Backend/runtime errors outside pagination normalization are still surfaced explicitly (not silently swallowed).

---

## 3. Limits & Budgets

These limits are contractual and strictly enforced. Clients MUST handle `413` and `429` responses.

| Limit | Metric | Default | Configuration Key |
| :--- | :--- | :--- | :--- |
| **Concurrency (Global)** | In-flight jobs | 2 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` |
| **Concurrency (Webhook)** | In-flight jobs | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK` |
| **Concurrency (Bridge)** | In-flight jobs | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` |
| **Concurrency (Per-Tenant)** | In-flight jobs per tenant | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT` |
| **Payload Size** | Rendered workflow | 512KB | `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` |
| **Webhook Body** | Raw JSON body | 10MB | `MAX_BODY_SIZE` (internal constant) |
| **Trigger Inputs** | Input variables | 32KB | Hardcoded in `api/triggers.py` |
| **Log Tail** | Max lines | 500 | Hardcoded in `api/route_handlers.py` |

---

## 4. Deprecation Policy

### 4.1 Legacy Routes (`/moltbot/`)

- **Status**: Deprecated.
- **Policy**: Maintained for backward compatibility in v1.x.
- **Removal**: Scheduled for removal in v2.0.
- **Action**: Clients should migrate to `/openclaw/` prefixes immediately.

### 4.2 Legacy Config Keys

- **Status**: Deprecated.
- **Policy**: Read-only fallback. `OPENCLAW_*` keys take precedence.
- **Action**: Operators should rename `MOLTBOT_*` keys to `OPENCLAW_*`.
