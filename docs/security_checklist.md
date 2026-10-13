# OpenClaw Connector Security Checklist

> **Complete this checklist before enabling public ingress (tunnel, reverse proxy, or direct exposure).**

## ✅ Pre-Deployment Checklist

### 1. Authentication & Trust

- [ ] Set `OPENCLAW_CONNECTOR_ADMIN_USERS` with at least one admin ID.
- [ ] Configure platform-specific allowlists:
  - Telegram: `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS` / `_ALLOWED_CHATS`
  - Discord: `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS` / `_ALLOWED_CHANNELS`
  - LINE: `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS` / `_ALLOWED_GROUPS`
  - WhatsApp: `OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS`
  - WeChat: `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`
  - KakaoTalk: `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`
  - Slack: `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS` / `_ALLOWED_CHANNELS`
  - Feishu/Lark: `OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS` / `_ALLOWED_CHATS`
- [ ] Verify startup banner shows "No trusted users" warning if allowlists are empty.
- [ ] For strict posture (`OPENCLAW_DEPLOYMENT_PROFILE=public` or `OPENCLAW_RUNTIME_PROFILE=hardened`), do not enable connector ingress without allowlists; startup/deployment checks fail closed.
- [ ] Verify duplicate or retried connector events are acknowledged without re-running completed actions.
- [ ] Verify reply-visibility suppression only applies to text-only silent/internal/tool-only/no-mention contexts and does not suppress approval cards or action buttons.

### 2. Webhook Security (LINE)

- [ ] HTTPS only — never expose webhook over HTTP.
- [ ] Verify `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` is set (signature verification).
- [ ] Consider using a randomized webhook path (e.g., `/line/webhook-abc123def`).

### 3. Rate Limiting

- [ ] Review default limits: 10 req/min per user, 30 req/min per channel.
- [ ] Adjust if needed: `OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM`, `_CHANNEL_RPM`.

### 4. Payload Limits

- [ ] Default max command length: 4096 chars.
- [ ] Adjust if needed: `OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH`.

### 5. Server API Access

- [ ] Keep ComfyUI on localhost (`--listen 127.0.0.1`) unless LAN access required.
- [ ] If exposing to LAN/Internet: set `OPENCLAW_ADMIN_TOKEN` environment variable.
- [ ] Never expose admin endpoints without token.
- [ ] For shared/LAN/public exposure, keep `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0` (or unset).
- [ ] For `OPENCLAW_DEPLOYMENT_PROFILE=public`, set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after reverse-proxy path allowlist + network ACL explicitly block ComfyUI-native high-risk routes.
- [ ] For `OPENCLAW_DEPLOYMENT_PROFILE=public`, if any connector platform token/enable flag is set, confirm corresponding allowlist coverage before startup (`DP-PUBLIC-009`).
- [ ] Keep `OPENCLAW_ENABLE_EXTERNAL_TOOLS=0` unless external tool execution is explicitly required; if enabled, review `OPENCLAW_TOOLS_CONFIG_PATH`, sandbox policy, and deterministic runtime diagnostics before exposure.
- [ ] Run `GET /openclaw/security/doctor` and verify no `csrf_no_origin_override` warning before exposure.
- [ ] Run `python scripts/verify_audit_chain.py --json` after restart/rotation-sensitive maintenance and confirm retained audit logs still verify cleanly.

### 6. Debug Mode

- [ ] `OPENCLAW_CONNECTOR_DEBUG=1` logs sensitive data — **disable in production**.
- [ ] Ensure no debug flags are set in production environment.
- [ ] Optional ops hygiene: if stale historical errors cause confusion in log viewers, use `OPENCLAW_LOG_TRUNCATE_ON_START=1` during controlled restart windows.

### 7. Tunnel / Reverse Proxy

- [ ] Use ngrok, Cloudflare Tunnel, or similar with TLS termination.
- [ ] Restrict access by IP if possible.
- [ ] Consider authentication layer (e.g., Cloudflare Access).

## ⚠️ Security Defaults

| Feature | Default | Effect |
|---------|---------|--------|
| Empty allowlists | Untrusted | All `/run` requires approval |
| Scheduled-run delivery | Needs connector admin token | Targets are unrestricted unless the platform chat/channel allowlist is set |
| Active connector without allowlist in strict posture (`public`/`hardened`) | Fail-closed | Startup/deployment checks block serving |
| No admin users | Limited | Admin commands unavailable |
| Rate limiting | Enabled | 10 req/min/user, 30 req/min/channel |
| Debug mode | Disabled | No sensitive logging |
| Replay protection | Enabled | LINE webhooks reject replays >5min old |
| Connector replay and reply visibility | Enabled | Duplicate committed events are no-ops; text-only suppressed replies do not suppress approval/action controls |
| Slack / Feishu interactive callbacks | Signed + deduped | Callback actions reject stale/replayed envelopes and degrade untrusted run actions to approval flow |

## 📞 Support

If you suspect a security issue, contact the maintainers via GitHub Issues (private for sensitive reports).
