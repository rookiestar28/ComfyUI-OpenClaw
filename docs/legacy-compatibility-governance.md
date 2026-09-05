# Legacy Compatibility Governance

OpenClaw keeps selected legacy compatibility aliases so older workflows, browser extensions, and deployment scripts have a predictable migration path. New integrations should use the canonical OpenClaw names.

Compatibility aliases are governed by explicit status, review cadence, telemetry, and removal criteria. An alias is not removed just because a canonical replacement exists; removal requires usage evidence and regression coverage.

## Status Labels

- `deprecated-observed`: the alias is still accepted, emits telemetry or warnings where practical, and should move to the canonical surface.
- `retained-compatibility`: the alias remains available for older workflows or deployments, with review based on diagnostics, tests, and operator reports.

## Review Policy

Every legacy alias has:

- a review cadence in days
- a telemetry or evidence signal
- a review trigger
- concrete removal criteria

Removal requires all of these conditions:

- no observed compatibility usage for two consecutive review windows
- a documented canonical migration path
- targeted regression coverage and release notes for the removal

## Governed Aliases

| Key | Surface | Legacy alias | Canonical surface | Status | Telemetry or evidence |
| --- | --- | --- | --- | --- | --- |
| `api-path-moltbot-prefix` | API path | `/moltbot/*` and `/api/moltbot/*` | `/openclaw/*` and `/api/openclaw/*` | `deprecated-observed` | `legacy_api_hits` |
| `header-x-moltbot-aliases` | Header | `X-Moltbot-*` request headers | `X-OpenClaw-*` request headers | `deprecated-observed` | `legacy_api_hits` and warning logs |
| `environment-moltbot-prefix` | Environment | `MOLTBOT_*` environment variables | `OPENCLAW_*` environment variables | `retained-compatibility` | configuration diagnostics and warning logs |
| `ui-class-moltbot-prefix` | UI class | `moltbot-*` CSS classes and local UI keys | `openclaw-*` CSS classes and local UI keys | `retained-compatibility` | frontend compatibility helper tests and operator reports |
| `workflow-node-moltbot-classes` | Workflow node | `Moltbot*` node class aliases | `OpenClaw*` node classes and `openclaw` node category | `retained-compatibility` | workflow portability diagnostics and node-registration regression tests |

The historical `moltbot` node category is no longer the current display category. Current shipped nodes use `openclaw`; legacy workflow compatibility is preserved through the `Moltbot*` class aliases rather than through legacy category metadata.

Environment compatibility is resolved through one audited registry. A legacy key that wins a
process lookup emits one content-free warning per key per process; canonical winners, absent keys,
and explicitly supplied environment mappings are silent. Compatibility lookup mode is
setting-specific so empty-value behavior remains stable. For the generic LLM credential, the order
is `OPENCLAW_LLM_API_KEY`, `MOLTBOT_LLM_API_KEY`, then `CLAWDBOT_LLM_API_KEY`.
`CLAWDBOT_GATEWAY_TOKEN` is not a supported environment alias.

## Operator Visibility

Legacy API path requests expose deprecation response headers when the response type supports headers:

- `Deprecation: true`
- `X-OpenClaw-Compatibility-Key`
- `X-OpenClaw-Compatibility-Status`
- `X-OpenClaw-Compatibility-Telemetry`
- `X-OpenClaw-Canonical-Path`

Use these headers with server logs and `legacy_api_hits` to decide whether a deployment still depends on legacy route aliases.
