# OpenClaw Connector

The **OpenClaw Connector** (`connector`) is a standalone process that allows you to control your local ComfyUI instance remotely via chat platforms like **Telegram**, **Discord**, **LINE**, **WhatsApp**, **WeChat Official Account**, **KakaoTalk (Kakao i Open Builder)**, **Slack**, and **Feishu/Lark**.

Per the product boundary contract, the connector is an **optional attached subsystem**. The primary published artifact of this repo remains the **ComfyUI custom node pack**, and the connector augments that package rather than replacing it.

Current extraction decision: keep the connector **in-repo** as an optional attached subsystem for now. OpenClaw does **not** currently treat a standalone connector package/repo as a supported distribution; see [ADR-0003](adr/ADR-0003-connector-extraction-feasibility-and-seams.md).

## How It Works

The connector runs alongside ComfyUI on your machine.

1. It connects outbound to Telegram/Discord (polling/gateway).
2. LINE/WhatsApp/WeChat/KakaoTalk/Slack use inbound webhooks (HTTPS required), while Feishu/Lark can run in webhook mode or long-connection mode with a separate callback ingress path for interactive actions.
3. It talks to ComfyUI via `localhost`.
4. It relays commands and status updates securely.

**Security**:

- **Transport Model**: Telegram/Discord are outbound. LINE/WhatsApp/WeChat/KakaoTalk/Slack require inbound HTTPS webhook endpoints. Feishu/Lark supports webhook ingress or long-connection transport, but interactive callbacks still use a bounded local HTTPS callback path.
- **Allowlist/Trust Model**: Allowlists define trusted senders/channels. Non-allowlisted senders are treated as untrusted (for example, `/run` is approval-routed instead of auto-executed). A non-empty chat/channel allowlist also restricts scheduled-run delivery targets.
- **Strict Profile Gate**: In `public` deployment or `hardened` runtime posture, enabling connector ingress without platform allowlist coverage is fail-closed at startup/deployment checks.
- **Local Secrets**: Bot tokens are stored in your local environment, never sent to ComfyUI.
- **Admin Boundary**: Control-plane actions call admin endpoints on the local OpenClaw server and require connector-side admin token configuration for admin command paths.
- **Reply Visibility**: Shared visibility policy can suppress text-only silent/internal/tool-only/no-mention replies without suppressing approval cards, action buttons, or the underlying trust checks.

### Installation and callback contract baseline

OpenClaw now includes a platform-agnostic baseline for multi-workspace connector lifecycle and interactive callback security:

- installation registry stores normalized records:
  - `platform`, `workspace_id`, `installation_id`, `token_refs`, `status`, `updated_at`
- token material is kept in encrypted server-side secret storage; registry and diagnostics expose token references only
- workspace resolution is fail-closed on missing/ambiguous/inactive/stale bindings
- installation diagnostics can also surface stable health states such as `ok`, `invalid_token`, `revoked`, `workspace_unbound`, and `degraded`
- interactive callback contract enforces signed envelope checks, timestamp window, payload-hash validation, replay/idempotency guardrails, and command-policy mapping (`public`/`run`/`admin`) with explicit force-approval outcomes for untrusted `run` callbacks
- connector replay handling acknowledges duplicate committed events as no-ops while allowing retryable failures before delivery commit to be retried
- text reply visibility is resolved through one connector policy for direct-message, shared-chat, thread, internal-delivery, and tool-only contexts; suppressed text is logged/diagnostic and treated as successful no-op delivery

Admin diagnostics APIs:

- `GET /openclaw/connector/installations`
- `GET /openclaw/connector/installations/{installation_id}`
- `GET /openclaw/connector/installations/resolve?platform=<platform>&workspace_id=<workspace_id>`
- `GET /openclaw/connector/installations/audit`
- `GET /openclaw/connector/extraction-contract`

Extraction diagnostics note:

- `/openclaw/connector/extraction-contract` is an admin-only structural metadata route for maintainers and operators. It returns the current packaging recommendation, candidate extraction options, seam families, and blockers, but it does **not** expose live token or installation-state details beyond the existing diagnostics routes above.
- The extraction contract also includes the static service-env SecretRef propagation policy. It is not a live environment dump and does not expose token values.

Slack multi-workspace notes:

- Slack OAuth installs bind one workspace per installation record and persist only encrypted token refs.
- OAuth callback state is single-use and replay/invalid-state callbacks fail closed instead of reusing a prior install session.
- `GET /openclaw/connector/installations/resolve?platform=slack&workspace_id=<team_id>` returns the fail-closed resolution view for a specific Slack workspace.
- `GET /openclaw/connector/installations` diagnostics may include per-install health metadata plus aggregate `health_counts`.
- Slack lifecycle events such as `tokens_revoked`, `app_uninstalled`, and rate-limit degradation update installation health so outbound replies fail closed or degrade predictably for the affected workspace.
- In multi-workspace mode, outbound replies and delayed result deliveries resolve the bot token by workspace binding and keep Slack thread context when replying back to the originating conversation.
- Slack interactive callbacks use the configured interactions path, signature verification, replay/idempotency checks, and connector policy mapping before accepting action payloads.
- Slack text replies honor the shared reply-visibility policy when context metadata is available; channel no-mention or tool-only text can be suppressed while Block Kit/action responses remain deliverable.

Feishu / Lark notes:

- Feishu bindings can be declared with a single app pair or a multi-account `OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON` manifest; each binding resolves to one normalized installation record with account/workspace identity.
- The connector supports both `feishu` and `lark` API domains through one shared binding contract, so region-specific app hosts do not require a different adapter.
- Websocket-mode Feishu deployments still host a callback route so interactive approval cards and command buttons remain available when message ingress itself is long-connection based.
- Feishu callback actions are signed, replay-guarded, tenant-aware, and deduplicated. Untrusted actors pressing run-affecting buttons are downgraded to approval flow instead of executing directly.
- Feishu text replies honor the shared reply-visibility policy when context metadata is available; group no-mention or tool-only text can be suppressed while interactive cards remain deliverable.

### Multi-tenant boundary behavior

When backend multi-tenant mode is enabled (`OPENCLAW_MULTI_TENANT_ENABLED=1`):

- installation records are tenant-owned (`tenant_id`) and diagnostics are tenant-scoped
- resolution rejects cross-tenant matches fail-closed (`tenant_mismatch` path)
- admin diagnostics calls can pass tenant context via token context and/or `X-OpenClaw-Tenant-Id` (or your configured `OPENCLAW_TENANT_HEADER`)
- missing tenant context currently falls back to `default` tenant for compatibility unless stricter caller paths are used

## Supported Platforms

- **Telegram**: Long-polling (instant response), including forum topic reply context for immediate replies and delayed result delivery.
- **Discord**: Gateway WebSocket (instant response).
- **LINE**: Webhook (requires inbound HTTPS).
- **WhatsApp**: Webhook (requires inbound HTTPS).
- **WeChat Official Account**: Webhook (requires inbound HTTPS).
- **KakaoTalk (Kakao i Open Builder)**: Webhook (requires inbound HTTPS).
- **Slack (Events API)**: Webhook (requires inbound HTTPS).
- **Feishu / Lark**: Webhook or long-connection transport; interactive callbacks require inbound HTTPS for the callback route.

## Setup

### 1. Requirements

- Python 3.10+
- `aiohttp` (installed with ComfyUI-OpenClaw)

### 2. Configuration

Set the following environment variables (or put them in a `.env` file if you use a loader):

**Common:**

- `OPENCLAW_CONNECTOR_URL`: URL of your ComfyUI (default: `http://127.0.0.1:8188`)
- `OPENCLAW_CONNECTOR_DEBUG`: Set to `1` for verbose logs.
- `OPENCLAW_CONNECTOR_ADMIN_USERS`: Comma-separated list of user IDs allowed to run admin commands (for example `/stop`, `/cancel`, approvals, schedules). Admin users are also treated as trusted senders for `/run`.
- `OPENCLAW_CONNECTOR_ADMIN_TOKEN`: Admin token sent to OpenClaw (`X-OpenClaw-Admin-Token`).
- `OPENCLAW_LOG_TRUNCATE_ON_START`: Optional backend runtime flag. Set `1` to clear `openclaw.log` once at backend startup to avoid stale-history noise in UI log panels.
- `OPENCLAW_MULTI_TENANT_ENABLED`: Optional backend mode toggle. If `1`, connector diagnostics and installation resolution become tenant-scoped.
- `OPENCLAW_TENANT_HEADER`: Optional tenant header key (default `X-OpenClaw-Tenant-Id`) used when calling tenant-scoped backend APIs.

**Admin token behavior:**

- Connector admin command paths require `OPENCLAW_CONNECTOR_ADMIN_TOKEN` to be set in connector runtime.
- If the OpenClaw server has `OPENCLAW_ADMIN_TOKEN` configured, `OPENCLAW_CONNECTOR_ADMIN_TOKEN` must match it or admin calls return HTTP 403.
- Without `OPENCLAW_CONNECTOR_ADMIN_TOKEN`, admin command flows (`/approve`, `/reject`, `/trace`, schedules) are blocked by connector policy before upstream calls, and the scheduled-run delivery worker does not start.

**SecretRef service environment behavior:**

- Service/sidecar launch helpers may preserve structured env-backed SecretRef metadata for connector credential variables such as platform bot tokens and signing secrets.
- Diagnostics show only the config path, env var name, source, status, and reason. They do not show raw token values from the installing shell.
- Raw secret strings, legacy `secretref-env:<NAME>` markers, unsupported env names, and gateway/admin auth env vars are rejected instead of being written into service metadata.
- Runtime-only auth secrets such as `OPENCLAW_CONNECTOR_ADMIN_TOKEN`, `OPENCLAW_WORKER_TOKEN`, and bridge device tokens must be provided by the runtime environment or a local secret manager rather than persisted through the connector service-env SecretRef boundary.

**Telegram:**

- `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN`: Your Bot Token (from @BotFather).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS`: Comma-separated list of User IDs (e.g. `123456, 789012`).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`: Comma-separated list of Chat/Group IDs.
- Telegram forum topics are preserved when Telegram provides `message_thread_id`; command replies and delayed result delivery are sent back to the same topic. Manually configured delivery contexts must use numeric topic/thread IDs.

**Discord:**

- `OPENCLAW_CONNECTOR_DISCORD_TOKEN`: Your Bot Token (from Discord Developer Portal).
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS`: Comma-separated User IDs.
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`: Comma-separated Channel IDs the bot should listen in.

**LINE:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET`: LINE Channel Secret.
- `OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN`: LINE Channel Access Token.
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS`: Comma-separated User IDs (e.g. `U1234...`).
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS`: Comma-separated Group IDs (e.g. `C1234...`).
- `OPENCLAW_CONNECTOR_LINE_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_LINE_PORT`: Port (default `8099`).
- `OPENCLAW_CONNECTOR_LINE_PATH`: Webhook path (default `/line/webhook`).

**WhatsApp:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN`: Cloud API access token.
- `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`: Webhook verify token (used during setup).
- `OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET`: App secret for signature verification (recommended).
- `OPENCLAW_CONNECTOR_WHATSAPP_PHONE_NUMBER_ID`: Phone number ID used for outbound messages.
- `OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS`: Comma-separated sender `wa_id` values (phone numbers).
- `OPENCLAW_CONNECTOR_WHATSAPP_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PORT`: Port (default `8098`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PATH`: Webhook path (default `/whatsapp/webhook`).

**WeChat Official Account:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WECHAT_TOKEN`: WeChat server verification token (**required** for adapter startup).
- `OPENCLAW_CONNECTOR_WECHAT_APP_ID`: Official Account AppID (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`: Official Account AppSecret (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`: Comma-separated OpenID allowlist. Non-allowlisted users are treated as untrusted and routed through approval semantics for sensitive actions.
- `OPENCLAW_CONNECTOR_WECHAT_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WECHAT_PORT`: Port (default `8097`).
- `OPENCLAW_CONNECTOR_WECHAT_PATH`: Webhook path (default `/wechat/webhook`).

**KakaoTalk (Kakao i Open Builder):**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_KAKAO_ENABLED`: Set to `true` to enable Kakao webhook adapter.
- `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`: Comma-separated Kakao user IDs (`userRequest.user.id` / botUserKey). Non-allowlisted users are treated as untrusted and sensitive actions require approval.
- `OPENCLAW_CONNECTOR_KAKAO_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_KAKAO_PORT`: Port (default `8096`).
- `OPENCLAW_CONNECTOR_KAKAO_PATH`: Webhook path (default `/kakao/webhook`).

**Slack (Events API):**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN`: Optional legacy single-workspace Bot User OAuth Token (`xoxb-...`). When Slack OAuth is configured, per-workspace tokens are resolved from the installation registry instead.
- `OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET`: Signing Secret (from App Credentials).
- `OPENCLAW_CONNECTOR_SLACK_CLIENT_ID`: OAuth client ID for multi-workspace installation flow.
- `OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET`: OAuth client secret for multi-workspace installation flow.
- `OPENCLAW_CONNECTOR_SLACK_OAUTH_REDIRECT_URI`: Explicit OAuth callback URL. If omitted, connector derives it from `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` + callback path.
- `OPENCLAW_CONNECTOR_SLACK_OAUTH_INSTALL_PATH`: Local install route (default `/slack/install`).
- `OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH`: Local callback route (default `/slack/oauth/callback`).
- `OPENCLAW_CONNECTOR_SLACK_OAUTH_SCOPES`: Comma-separated bot scopes used for install URL generation.
- `OPENCLAW_CONNECTOR_SLACK_OAUTH_STATE_TTL_SEC`: TTL in seconds for single-use OAuth state tokens (default `600`, clamped to `60..3600`).
- `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS`: Comma-separated user IDs (e.g. `U12345, U67890`).
- `OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS`: Comma-separated channel IDs (e.g. `C12345`).
- `OPENCLAW_CONNECTOR_SLACK_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_SLACK_PORT`: Port (default `8095`).
- `OPENCLAW_CONNECTOR_SLACK_PATH`: Webhook path (default `/slack/events`).
- `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION`: `true` (default) to require `@Bot` mention in public channels.
- `OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD`: `true` (default) to reply in threads.

**Feishu / Lark:**

*(Long connection or webhook; callback ingress still requires inbound HTTPS if interactive cards are enabled)*

- `OPENCLAW_CONNECTOR_FEISHU_APP_ID`: App ID for the default Feishu/Lark binding.
- `OPENCLAW_CONNECTOR_FEISHU_APP_SECRET`: App secret for the default binding.
- `OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN`: Verification token for webhook event ingress.
- `OPENCLAW_CONNECTOR_FEISHU_ENCRYPT_KEY`: Optional encrypt key for encrypted webhook payloads.
- `OPENCLAW_CONNECTOR_FEISHU_ACCOUNT_ID`: Explicit account ID for the default binding.
- `OPENCLAW_CONNECTOR_FEISHU_DEFAULT_ACCOUNT_ID`: Fallback account ID when binding manifest entries omit one.
- `OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_ID`: Workspace or tenant identifier associated with the default binding.
- `OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_NAME`: Human-readable workspace name used in diagnostics.
- `OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON`: JSON list of account bindings for multi-account / multi-workspace setups.
- `OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS`: Comma-separated trusted user IDs.
- `OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS`: Comma-separated trusted chat IDs.
- `OPENCLAW_CONNECTOR_FEISHU_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_FEISHU_PORT`: Port (default `8094`).
- `OPENCLAW_CONNECTOR_FEISHU_PATH`: Event ingress route (default `/feishu/events`).
- `OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH`: Interactive callback route (default `/feishu/callback`).
- `OPENCLAW_CONNECTOR_FEISHU_DOMAIN`: API domain selector (`feishu` or `lark`).
- `OPENCLAW_CONNECTOR_FEISHU_MODE`: Transport mode (`websocket` default, or `webhook`).
- `OPENCLAW_CONNECTOR_FEISHU_REQUIRE_MENTION`: Set `false` to allow commands without explicit mention in shared chats.
- `OPENCLAW_CONNECTOR_FEISHU_REPLY_IN_THREAD`: Set `false` to disable reply threading when the source chat supports it.

**Result Delivery:**

- `OPENCLAW_CONNECTOR_DELIVERY_MAX_IMAGES`: Max completed images delivered per job (default `4`, clamped to `1..16`).
- `OPENCLAW_CONNECTOR_DELIVERY_MAX_BYTES`: Per-image delivery cap in bytes (default `10485760`, clamped to `65536..52428800`).
- `OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC`: Result delivery timeout in seconds (default `600`, clamped to `30..3600`).
- `OPENCLAW_CONNECTOR_SCHEDULE_DELIVERY_POLL_SEC`: How often the connector claims pending scheduled-run deliveries (default `15`, clamped to `5..300`).
- `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL`: Public HTTPS URL of your connector (e.g. `https://your-tunnel.example.com`). Required for sending images.
- `OPENCLAW_CONNECTOR_MEDIA_PATH`: URL path for serving temporary media (default `/media`).
- `OPENCLAW_CONNECTOR_MEDIA_TTL_SEC`: Image expiry in seconds (default `300`, clamped to `60..86400`).
- `OPENCLAW_CONNECTOR_MEDIA_MAX_MB`: Max image size in MB (default `8`, clamped to `1..64`).

**Connector numeric guardrails:**

- Platform bind ports (`OPENCLAW_CONNECTOR_LINE_PORT`, `...WHATSAPP_PORT`, `...WECHAT_PORT`, `...KAKAO_PORT`, `...SLACK_PORT`, `...FEISHU_PORT`) must stay within `1..65535`; invalid or out-of-range values fall back to the platform default port.
- `OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM`: Per-user connector rate limit (default `10`, clamped to `1..600`).
- `OPENCLAW_CONNECTOR_RATE_LIMIT_CHANNEL_RPM`: Per-channel connector rate limit (default `30`, clamped to `1..600`).
- `OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH`: Max accepted command text length (default `4096`, clamped to `128..32768`).

> **Note:** Media URLs are signed with a secret derived from `OPENCLAW_CONNECTOR_ADMIN_TOKEN` or a random key.
> To ensure URLs remain valid after connector restarts, **you must set `OPENCLAW_CONNECTOR_ADMIN_TOKEN`**.
> LINE and WhatsApp also **require** `public_base_url` to be HTTPS.
> WeChat currently supports text-first control. Image/media upload delivery is not implemented in phase 1.
> Kakao currently supports text-first control and quick replies. Rich media delivery is not enabled in the default Kakao webhook flow.
> Slack supports text responses and image uploads (via `files.upload` API).
> Feishu currently supports text replies plus interactive approval/command cards; richer card templates can be added on top of the same signed callback contract.

### Command authorization policy

Connector commands are evaluated through a centralized authorization policy with three command classes:

- `public`: low-risk status/help style commands
- `run`: execution commands such as `/run` (still subject to trust/approval behavior)
- `admin`: sensitive commands such as `/trace`, `/approvals`, `/approve`, `/reject`, and schedule controls

Default behavior:

- If no explicit allow-from list is configured for a command class, class-level defaults apply.
- `admin` commands require the sender to be in `OPENCLAW_CONNECTOR_ADMIN_USERS`.
- `public` and `run` commands still pass through each platform adapter's trust/allowlist checks.

Optional policy controls:

- `OPENCLAW_COMMAND_OVERRIDES`: JSON object mapping command name to class (`public`, `run`, `admin`).
- `OPENCLAW_COMMAND_ALLOW_FROM_PUBLIC`: comma-separated sender IDs.
- `OPENCLAW_COMMAND_ALLOW_FROM_RUN`: comma-separated sender IDs.
- `OPENCLAW_COMMAND_ALLOW_FROM_ADMIN`: comma-separated sender IDs.

Normalization rules:

- Command keys in `OPENCLAW_COMMAND_OVERRIDES` are normalized to lowercase.
- Missing leading `/` is added automatically.

Example:

```bash
OPENCLAW_COMMAND_OVERRIDES='{"run":"admin","/status":"public"}'
OPENCLAW_COMMAND_ALLOW_FROM_ADMIN=alice_id,bob_id
OPENCLAW_COMMAND_ALLOW_FROM_RUN=alice_id,ops_bot_id
```

If a class-level `OPENCLAW_COMMAND_ALLOW_FROM_*` list is set and non-empty, only listed IDs can run that class.

### Authoritative jobs summary

`/jobs` and its `jobs` / `queue` aliases are Admin-class commands. They require both an
authorized connector admin user and a configured `OPENCLAW_CONNECTOR_ADMIN_TOKEN` before
the connector calls `GET /openclaw/jobs`.

The connector validates jobs contract version 1 before rendering any reply:

- output contains aggregate snapshot/page counts plus at most five job IDs and statuses;
- displayed job IDs are capped at 24 characters and the complete reply is capped at 1,000
  characters;
- raw job records, prompts, workflows, outputs, errors, tracebacks, tenant identifiers,
  and the upstream payload are never sent to the chat LLM or copied into error messages;
- HTTP 401/403 returns a fixed authorization message without fallback;
- only explicit HTTP 501 `jobs_host_contract_unsupported` or HTTP 503
  `jobs_backend_unavailable` responses may fall back to a bounded coarse queue count;
- malformed, unknown-version, oversized, or inconsistent success payloads fail to a fixed
  content-free message.

Public `/status` remains separate: it can summarize health and the coarse ComfyUI queue,
but it does not fetch or forward the Admin-only jobs snapshot.

### Scheduled run delivery

Schedules with a Telegram, Discord, Slack, or Feishu/Lark delivery target are delivered by
the connector, which keeps the chat credentials. The connector starts the delivery worker
only when `OPENCLAW_CONNECTOR_ADMIN_TOKEN` is set and at least one of those platforms is
running. Every `OPENCLAW_CONNECTOR_SCHEDULE_DELIVERY_POLL_SEC` seconds it claims pending
deliveries for its running platforms, waits up to `OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC`
for the job result, and reports the outcome back to ComfyUI.

- Finished jobs are sent to the schedule's target (and thread, when set) like `/run` results.
- Failed jobs send a short failure notice only when the schedule keeps `failure_alert`
  enabled (the default); job errors and outputs are never copied into the notice.
- Results that do not arrive in time send a short timeout notice.
- A target outside the platform's allowlist (`OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`,
  `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`, `OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS`,
  `OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS`) is never sent and is reported as
  `undeliverable`; an empty allowlist does not restrict targets.
- Each delivery is leased to one connector at a time. If a connector stops after sending
  but before reporting, the delivery is offered again when the lease expires, so that one
  result can arrive twice.

Delivery state per run is visible in the schedule run history and through
`GET /openclaw/schedule-deliveries` (admin token).

### 3. Usage

#### Running the Connector

```bash
python -m connector
```

#### LINE Webhook Setup

Unlike Telegram/Discord which pull messages, LINE pushes webhooks to your connector.
Since the connector runs on `localhost` (default port 8099), you must expose it to the internet securely.

**Option A: Cloudflare Tunnel (Recommended)**

1. Install `cloudflared`.
2. Run: `cloudflared tunnel --url http://127.0.0.1:8099`
3. Copy the generated URL (e.g. `https://random-name.trycloudflare.com`).
4. In LINE Developers Console > Messaging API > Webhook settings:
   - Set URL to `https://<your-tunnel>/line/webhook` (or your custom path).
   - Enable "Use webhook".

**Option B: Reverse Proxy (Nginx/Caddy)**

- Configure your proxy to forward HTTPS traffic to `127.0.0.1:8099`.

#### WhatsApp Webhook Setup

WhatsApp Cloud API delivers webhooks to your connector. You must expose it via HTTPS.

1. Create a Meta app and add the WhatsApp product.
2. Add a phone number and note its **Phone Number ID**.
3. Configure the webhook URL: `https://<your-public-host>/whatsapp/webhook`.
4. Set the webhook **Verify Token** to match `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`.
5. Subscribe to `messages` events.
6. Ensure `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` is an HTTPS URL so media can be delivered.

If you run locally, use a secure tunnel (Cloudflare Tunnel or ngrok) and point it to `http://127.0.0.1:8098`.

#### WeChat Official Account Webhook Setup (Detailed)

WeChat Official Account pushes webhook requests to your connector. You must expose the WeChat endpoint publicly over HTTPS.

1. Prepare the required environment variables:

   ```bash
   OPENCLAW_CONNECTOR_WECHAT_TOKEN=replace-with-your-wechat-token
   OPENCLAW_CONNECTOR_WECHAT_APP_ID=replace-with-your-wechat-app-id
   OPENCLAW_CONNECTOR_WECHAT_APP_SECRET=replace-with-app-secret
   OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS=openid_1,openid_2
   OPENCLAW_CONNECTOR_WECHAT_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_WECHAT_PORT=8097
   OPENCLAW_CONNECTOR_WECHAT_PATH=/wechat/webhook
   ```

   Use descriptive placeholders for App IDs in docs and examples. Secret scanners can
   treat AppID-shaped samples as real credentials even when they are only documentation.

2. Start the connector:

   ```bash
   python -m connector
   ```

3. Expose the local webhook service to HTTPS (Cloudflare Tunnel or reverse proxy):
   - local upstream: `http://127.0.0.1:8097`
   - public path: `/wechat/webhook`
   - expected public URL: `https://<your-public-host>/wechat/webhook`

4. In WeChat Official Account backend (Developer settings / server config), configure:
   - URL: `https://<your-public-host>/wechat/webhook`
   - Token: same value as `OPENCLAW_CONNECTOR_WECHAT_TOKEN`
   - EncodingAESKey: set according to your WeChat backend requirement
   - Message encryption mode: use plaintext/compatible mode for this adapter path

5. Save/submit server config. WeChat will call your endpoint with verification query parameters.
   - expected success behavior: connector returns `echostr` and logs verification success
   - expected failure behavior: `403 Verification failed` if token/signature mismatches

6. Functional test:
   - follow your Official Account with a test user
   - send `/help` or `/status`
   - verify connector receives command and returns text reply

7. Verify trusted/untrusted behavior:
   - if sender OpenID is in `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`, `/run` can execute directly (subject to trust/approval policy and command policy)
   - if not allowlisted, sensitive actions are routed to approval flow

**WeChat-specific notes:**

- The adapter validates WeChat signature on every request and applies replay/timestamp checks.
- Timestamp skew outside policy window is rejected (`403 Stale Request`).
- XML payload parsing is bounded (size/depth/field caps) and fails closed on parser budget violations.
- DTD / ENTITY declarations are rejected before parser entry; the adapter does not attempt to recover from unsafe XML payloads.
- Runtime XML security gate is fail-closed: unsafe/missing parser baseline blocks ingress startup.
- Current command surface is text-first. Unsupported message/event types are ignored with success response.
- Proactive outbound API messaging requires both `OPENCLAW_CONNECTOR_WECHAT_APP_ID` and `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`.

#### KakaoTalk (Kakao i Open Builder) Webhook Setup (Detailed)

Kakao i Open Builder sends webhook requests to your connector Skill endpoint. You must expose the Kakao endpoint publicly over HTTPS.

1. Prepare the required environment variables:

   ```bash
   OPENCLAW_CONNECTOR_KAKAO_ENABLED=true
   OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS=kakao_user_id_1,kakao_user_id_2
   OPENCLAW_CONNECTOR_KAKAO_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_KAKAO_PORT=8096
   OPENCLAW_CONNECTOR_KAKAO_PATH=/kakao/webhook
   ```

2. Start the connector:

   ```bash
   python -m connector
   ```

3. Expose the local webhook service to HTTPS (Cloudflare Tunnel or reverse proxy):
   - local upstream: `http://127.0.0.1:8096`
   - public path: `/kakao/webhook`
   - expected public URL: `https://<your-public-host>/kakao/webhook`

4. In Kakao i Open Builder:
   - create/select your bot
   - create/select a Skill
   - set Skill server URL to `https://<your-public-host>/kakao/webhook`
   - deploy/publish the Skill scenario that calls this Skill endpoint

5. Functional test:
   - chat with your Kakao bot
   - send `/help` or `/status`
   - verify connector receives command and returns a SkillResponse (`version: 2.0`)

6. Verify trusted/untrusted behavior:
   - if sender `userRequest.user.id` is in `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`, `/run` can execute directly (subject to trust/approval policy and command policy)
   - if not allowlisted, sensitive actions are routed to approval flow

7. Optional first-time allowlist bootstrap:
   - temporarily leave `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS` empty
   - send a test message and check logs for `Untrusted Kakao message from user=<id>`
   - add that ID to allowlist and restart connector

**Kakao-specific notes:**

- Adapter is disabled unless `OPENCLAW_CONNECTOR_KAKAO_ENABLED=true`.
- Kakao webhook ingress is `POST` only on `OPENCLAW_CONNECTOR_KAKAO_PATH`.
- Replay protection is enabled: identical payloads within the replay window are acknowledged and not re-executed.
- Kakao command requests are normalized from:
  - `userRequest.user.id` -> `sender_id`
  - `userRequest.utterance` -> command text
- Response format follows Kakao SkillResponse v2.0 with text-first output and optional quick replies.
- If `aiohttp` is missing, the adapter is skipped at startup.

#### Slack Webhook Setup (Detailed)

Slack uses the Events API webhook mode in OpenClaw. You must expose the endpoint publicly over HTTPS.

1. **Create the Slack App**
   - Go to [api.slack.com/apps](https://api.slack.com/apps).
   - Create a new app (From scratch) and select your workspace.
   - In **Basic Information**, copy the **Signing Secret**.

2. **Configure OAuth Scopes and install**
   - Go to **OAuth & Permissions**.
   - Add bot scopes:
     - `chat:write`
     - `files:write`
     - `app_mentions:read`
     - `im:history` (DM support)
     - `channels:history` (public channel messages)
     - `groups:history` (private channel messages)
   - For legacy single-workspace mode, click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).
   - For multi-workspace mode, configure a redirect URL and let OpenClaw handle installs through its OAuth routes.

3. **Configure connector environment variables**

   ```bash
   OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET=your-signing-secret
   OPENCLAW_CONNECTOR_SLACK_CLIENT_ID=1234567890.1234567890
   OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET=replace-with-client-secret
   OPENCLAW_CONNECTOR_PUBLIC_BASE_URL=https://your-public-host
   OPENCLAW_CONNECTOR_SLACK_OAUTH_INSTALL_PATH=/slack/install
   OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH=/slack/oauth/callback
   OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH=/slack/interactions
   OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS=U12345,U67890
   OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS=C12345
   OPENCLAW_CONNECTOR_SLACK_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_SLACK_PORT=8095
   OPENCLAW_CONNECTOR_SLACK_PATH=/slack/events
   OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true
   OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD=true
   OPENCLAW_CONNECTOR_ADMIN_TOKEN=replace-with-openclaw-admin-token
   ```

   Notes:
   - Legacy single-workspace fallback can still set `OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN=xoxb-...`; multi-workspace mode no longer requires that token at startup if OAuth install flow is configured.
   - `OPENCLAW_CONNECTOR_ADMIN_TOKEN` must match server `OPENCLAW_ADMIN_TOKEN` if server-side admin token is enabled.
   - Slack ingress is fail-closed: invalid/missing signature, stale timestamp, and replayed events are rejected.
   - Slack interactive callbacks use the same signing-secret verification and route actions through the connector policy layer before executing run-affecting behavior.
   - OAuth callbacks also fail closed on invalid or replayed `state` values.
   - External OAuth/install failures intentionally use bounded generic text; inspect connector logs and installation diagnostics for redacted detail instead of expecting raw exception text in the callback response.

4. **Start connector and expose webhook endpoint**
   - Start connector: `python -m connector`
   - Expose local endpoint to public HTTPS (Cloudflare Tunnel/ngrok/reverse proxy):
     - local upstream: `http://127.0.0.1:8095`
     - public URL: `https://<public-host>/slack/events`
     - interactions URL: `https://<public-host>/slack/interactions`
     - install URL: `https://<public-host>/slack/install`
     - callback URL: `https://<public-host>/slack/oauth/callback`

5. **Enable Event Subscriptions and Interactivity**
   - Go to **Event Subscriptions** and enable events.
   - Set **Request URL** to `https://<public-host>/slack/events`.
   - Slack sends `url_verification`; connector responds automatically.
   - Add bot events:
     - `app_mention`
     - `message.channels`
     - `message.groups`
     - `message.im`
   - Go to **Interactivity & Shortcuts** and enable interactivity.
   - Set **Request URL** to `https://<public-host>/slack/interactions`.

6. **Invite and validate**
   - Open `https://<public-host>/slack/install` and complete the workspace install.
   - Invite the app to target channels: `/invite @YourBot`.
   - In channel: `@YourBot /status` (when `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true`).
   - In DM: `/help`.
   - For approval or action-capable replies, press a rendered Slack button and confirm the connector logs show a signed interaction accepted or a bounded policy rejection.
   - Verify connector logs show signed ingress accepted and replies delivered.
   - Verify `GET /openclaw/connector/installations` shows the Slack workspace binding and health state `ok`.
   - If you test uninstall/token-revoke scenarios, verify the installation health flips to `revoked` or `invalid_token` and that subsequent replies for that workspace fail closed until reinstalled.

7. **Security checklist before production**
   - Keep `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS`/`OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS` restricted.
   - Keep `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true` unless intentionally running command-style channels.
   - Rotate Slack signing secret and OAuth client secret on incident response.
   - Do not expose connector without HTTPS termination.

#### Slack Socket Mode Setup (Optional)

Use Socket Mode when you cannot expose a public HTTPS webhook endpoint.

1. **Enable Socket Mode in Slack**
   - Open your Slack App settings.
   - Go to **Socket Mode** and enable it.
   - Create an App-Level Token (`xapp-...`) with `connections:write`.

2. **Configure connector**

   ```bash
   OPENCLAW_CONNECTOR_SLACK_MODE=socket
   OPENCLAW_CONNECTOR_SLACK_APP_TOKEN=xapp-your-token
   # Signing secret remains required for parity/security checks.
   OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET=your-signing-secret
   # Either configure legacy single-workspace token...
   OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN=xoxb-your-token
   # ...or configure multi-workspace OAuth install flow:
   OPENCLAW_CONNECTOR_SLACK_CLIENT_ID=1234567890.1234567890
   OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET=replace-with-client-secret
   ```

3. **Start connector**
   - `python -m connector`
   - Expect log: `Slack Socket Mode connected.`

Notes:
- Socket Mode uses outbound WebSocket, so `OPENCLAW_CONNECTOR_SLACK_BIND`, `OPENCLAW_CONNECTOR_SLACK_PORT`, and `OPENCLAW_CONNECTOR_SLACK_PATH` are ignored in this mode.
- Startup is fail-closed if `OPENCLAW_CONNECTOR_SLACK_APP_TOKEN` is missing or does not start with `xapp-`.
- In multi-workspace mode, outbound replies still resolve the workspace-specific bot token from the installation registry even though the WebSocket connection itself uses the app-level token.

#### Feishu / Lark Setup (Detailed)

Feishu support can run in either long-connection (`websocket`) mode or webhook mode. Long-connection is usually the simpler default for message ingress, but interactive cards still need a reachable callback route if you want approval buttons and other signed actions.

1. **Create the Feishu or Lark app**
   - Create a bot app in the Feishu or Lark developer console.
   - Record the `App ID` and `App Secret`.
   - If you want webhook ingress, also configure the event subscription verification token.
   - If encrypted event delivery is enabled, record the encrypt key as well.

2. **Choose transport mode**
   - `OPENCLAW_CONNECTOR_FEISHU_MODE=websocket`
     - Uses long connection for message ingress.
     - Recommended when you do not want to expose the event route publicly.
   - `OPENCLAW_CONNECTOR_FEISHU_MODE=webhook`
     - Uses HTTPS webhook delivery for messages.
     - Requires a public HTTPS route for `OPENCLAW_CONNECTOR_FEISHU_PATH`.

3. **Configure the default binding**

   ```bash
   OPENCLAW_CONNECTOR_FEISHU_APP_ID=cli_xxx
   OPENCLAW_CONNECTOR_FEISHU_APP_SECRET=sec_xxx
   OPENCLAW_CONNECTOR_FEISHU_ACCOUNT_ID=acct-default
   OPENCLAW_CONNECTOR_FEISHU_DEFAULT_ACCOUNT_ID=acct-default
   OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_ID=tenant-alpha
   OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_NAME="Alpha Workspace"
   OPENCLAW_CONNECTOR_FEISHU_DOMAIN=feishu
   OPENCLAW_CONNECTOR_FEISHU_MODE=websocket
   OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
   OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS=oc_xxx,oc_yyy
   ```

4. **Optional: multi-account binding manifest**
   - Use `OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON` when one connector runtime should host more than one Feishu/Lark app or workspace binding.
   - Each entry may include:
     - `account_id`
     - `app_id`
     - `app_secret`
     - `workspace_id`
     - `workspace_name`
     - `verification_token`
     - `encrypt_key`
     - `domain`
     - `mode`

5. **Configure interactive callback ingress**
   - Set `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` to your public HTTPS origin.
   - Expose `OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH` (default `/feishu/callback`) through your reverse proxy or tunnel.
   - In long-connection mode this callback route is still required for interactive approval cards; message ingress transport does not remove callback security requirements.

6. **Start connector**
   - `python -m connector`
   - Expect logs showing the chosen Feishu mode and callback/event route bindings.

7. **Verify runtime behavior**
   - Run `/status` from an allowlisted Feishu/Lark user.
   - Run `/approvals` and confirm the reply renders approval buttons as an interactive card.
   - Click `Approve` or `Reject` on a test approval and verify the callback succeeds once, then duplicate clicks are deduped.

Notes:
- `OPENCLAW_CONNECTOR_FEISHU_DOMAIN=lark` switches outbound API host behavior without changing the rest of the connector contract.
- Untrusted users can still see bounded command responses, but run-affecting interactive actions are downgraded to approval flow instead of auto-executing.
- Callback signing secrets are resolved from the bound Feishu installation record; diagnostics expose binding state, not raw secret material.
- Callback/event wrapper failures intentionally return bounded external error codes; inspect logs and installation diagnostics for redacted detail instead of expecting stack traces in callback responses.

## Commands

**General:**

| Command | Description |
| :--- | :--- |
| `/status` | Check ComfyUI system status, logs, and queue size. |
| `/jobs` | View active jobs and queue summary. |
| `/history <id>` | View details of a finished job. |
| `/help` | Show available commands. |
| `/run <template> [k=v] [--approval]` | Submit a job. Use `--approval` to request approval gate instead of creating job immediately. |
| `/stop [job_id ...]` | Stop jobs. With no job IDs, sends an explicit global interrupt. With one or more IDs, requests targeted job cancellation through ComfyUI's jobs API; older single-job hosts may fall back to targeted interrupt. |
| `/cancel [job_id ...]`, `/interrupt [job_id ...]` | Aliases for `/stop` with the same targeted or global behavior. |

**Admin Only:**
*(Requires User ID in `OPENCLAW_CONNECTOR_ADMIN_USERS`)*

| Command | Description |
| :--- | :--- |
| `/trace <id>` | View raw execution logs/trace for a job. |
| `/approvals` | List pending approvals. |
| `/approve <id>` | Approve a pending request (triggers execution immediately). |
| `/reject <id> [reason]` | Reject a workflow. |
| `/schedules` | List schedules. |
| `/schedule run <id>` | Trigger a schedule immediately. The chat gets a confirmation; the run's result goes to the schedule's own delivery target, if it has one, not to this chat. |

## Usage Examples

### Approval Gated Run

1. **Submission (Admin)**:

   ```
   User: /run my-template steps=20 --approval
   Bot:  [Approval Requested]
         ID: apr_12345
         Trace: ...
         Expires: 2026-02-07T12:00:00Z
   ```

2. **Approval (Admin)**:

   ```
   User: /approve apr_12345
   Bot:  [Approved] apr_12345
         Executed: p_98765
   ```

### Common Failure Modes

- **(Not Executed)**:
  - If `/approve` returns `[Approved] ... (Not Executed)`, it means the request state was updated to Approved, but the job could not be autostarted.
  - **Reason**: Backend might lack a submit handler for this trigger type, or `auto_execute` failed. Check `openclaw` server logs.
  - **Action**: Manually run the job using the template/inputs from the approval request.

- **Access Denied**:
  - Sender is not in `OPENCLAW_CONNECTOR_ADMIN_USERS`.
  - Fix: Add ID to `.env` and restart connector.

- **No visible chat reply after a command**:
  - The command may have completed in a context where text-only replies are intentionally suppressed, such as internal delivery, tool-only handling, or a shared chat/channel without an active mention.
  - Fix: check connector logs and job/approval state. Approval cards and action buttons should still be delivered when the action requires visible operator input.

- **Duplicate platform event is acknowledged but not executed again**:
  - The connector has already committed the action and treats the retry/replay as a successful no-op.
  - Fix: check the original event, job, or approval record instead of resending the same action payload. Retry only failures that happened before delivery/action commit.

- **HTTP 403 (Admin Token)**:
  - Connector has the right user allowlist, but the upstream OpenClaw server rejected the Admin Token.
  - Fix: Ensure `OPENCLAW_CONNECTOR_ADMIN_TOKEN` matches the server's `OPENCLAW_ADMIN_TOKEN`.

- **Kakao requests not arriving**:
  - `OPENCLAW_CONNECTOR_KAKAO_ENABLED` is not `true`, or Kakao Skill URL/path does not match `OPENCLAW_CONNECTOR_KAKAO_PATH`.
  - Fix: set `OPENCLAW_CONNECTOR_KAKAO_ENABLED=true`, verify public HTTPS URL, and confirm Skill URL exactly matches `/kakao/webhook` (or your custom path).

- **Kakao returns empty/fallback response**:
  - Payload is malformed (missing `userRequest.user.id` / `userRequest.utterance`) or the adapter rejected malformed JSON.
  - Fix: validate Skill request payload shape in Open Builder test console and check connector logs for `Bad JSON` / payload errors.

- **Kakao `/run` always goes to approval**:
  - Sender is not in `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS` (or allowlist is empty).
  - Fix: capture `userRequest.user.id` from logs, add it to `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`, restart connector.

- **Slack Event Subscriptions verification fails**:
  - Request URL/path mismatch, connector not reachable, or `OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET` is wrong.
  - Fix: confirm public URL points to `/slack/events`, verify tunnel/proxy routes to `127.0.0.1:8095`, and re-check Signing Secret.

- **Slack commands ignored in channels**:
  - `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true` and message does not mention the bot.
  - Fix: mention bot explicitly (`@Bot /status`) or set `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=false` if policy allows.

- **Slack OAuth callback shows only a generic install failure**:
  - This is expected on current builds; callback responses intentionally suppress raw exception text.
  - Fix: inspect connector logs plus `GET /openclaw/connector/installations` / `/resolve` diagnostics to find the redacted root cause.

- **I need to know whether the connector is supported as a separate package/repo yet**:
  - Current builds intentionally keep the connector in-repo as an optional attached subsystem.
  - Fix: review `GET /openclaw/connector/extraction-contract` or [ADR-0003](adr/ADR-0003-connector-extraction-feasibility-and-seams.md) for the current no-split recommendation and the seam blockers that still need to be versioned first.

- **Feishu callback buttons fail with signature or stale-action errors**:
  - Callback route is not using the same bound app secret, request arrived too late, or the button payload was replayed.
  - Fix: verify binding diagnostics, public callback route, connector clock, and that the same action is not being resent by proxy/retry middleware.

- **Feishu long-connection messages work but card actions do nothing**:
  - `OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH` is not exposed publicly, or the callback URL is not routed to the connector bind host/port.
  - Fix: expose the callback route over HTTPS even when `OPENCLAW_CONNECTOR_FEISHU_MODE=websocket`.

- **Feishu `/run` action becomes approval instead of executing immediately**:
  - Callback actor is untrusted under current allowlist/policy mapping.
  - Fix: add the user/chat to `OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS` or `_ALLOWED_CHATS`, or keep the approval downgrade as the intended posture.

- **Feishu callback returns a bounded code such as `callback_rejected` or `event_rejected`**:
  - This is expected on current builds; callback wrappers intentionally avoid echoing raw exception detail to the caller.
  - Fix: inspect connector logs and installation/binding diagnostics for the redacted failure context.
