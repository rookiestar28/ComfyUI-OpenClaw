# Runtime Hardening and Startup

This guide explains the startup security model and bridge compatibility behavior.

## What this covers

- Runtime profile selection
- Hardened startup enforcement behavior
- Typed startup lifecycle diagnostics
- Process-static effective security posture
- External tool sandbox diagnostics
- Module startup boundaries
- Bridge protocol handshake compatibility

## Runtime profile

Use `OPENCLAW_RUNTIME_PROFILE` to select startup posture:

- `minimal` (default): compatibility-first
- `hardened`: strict fail-closed startup checks

If the value is unknown, startup falls back to `minimal` with a warning.

You can verify the active profile through:

- `GET /openclaw/capabilities`
- `GET /moltbot/capabilities`

The response includes `runtime_profile`.

## Hardened startup enforcement

When `OPENCLAW_RUNTIME_PROFILE=hardened`, startup enforces mandatory controls and aborts on failure.

Current mandatory checks:

- Authentication is configured for privileged actions
- Unsafe egress bypass is not enabled:
  - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` must not bypass policy
  - `OPENCLAW_ALLOW_INSECURE_BASE_URL` must not bypass policy
- If webhook module is active, webhook auth mode must be configured
- Redaction service must be available
- Connector ingress allowlist coverage is enforced for strict posture:
  - `OPENCLAW_RUNTIME_PROFILE=hardened`: startup fails closed if an active connector platform has no allowlist
  - `OPENCLAW_DEPLOYMENT_PROFILE=public`: deployment/startup checks fail closed for the same condition (`DP-PUBLIC-009`)

In `minimal` mode, these checks are warning-first for local/LAN posture, but `public` deployment profile still enforces fail-closed policy checks.

### Bootstrap fail-closed propagation

Startup bootstrap no longer swallows fatal security-gate errors.
If a critical startup gate fails, initialization aborts deterministically instead of continuing with partial route registration.

### Startup lifecycle diagnostics

The health response includes a `startup` diagnostic object with:

- `schema_version`: diagnostic schema version
- `phase`: `package_import`, `required_initialization`, `host_wait`,
  `route_registration`, `complete`, or `optional_warmup`
- `state`: `starting`, `initializing`, `waiting_for_host`, `registering_routes`, `ready`,
  `degraded`, or `fatal`
- `reason_code`: stable, content-free transition reason
- `ready`: whether required route/service startup completed
- `degraded` and `fatal`: explicit terminal posture flags
- `attempt` and `max_attempts`: bounded host-wait retry progress
- `elapsed_ms`, `phase_elapsed_ms`, and `ready_elapsed_ms`: bounded lifecycle timing
- `warmups`: bounded optional warmup entries with name, state, reason, timeout, and duration

Required startup work still fails closed. Optional warmups such as model inventory refresh run after
route registration and do not block baseline API availability. A failed or timed-out optional
warmup changes the startup state to `degraded`; individual warmup states are `pending`, `running`,
`succeeded`, `failed`, or `timed_out`.

Optional warmup timeout can be tuned with:

- `OPENCLAW_STARTUP_WARMUP_TIMEOUT_SEC`
- legacy alias: `MOLTBOT_STARTUP_WARMUP_TIMEOUT_SEC`

### Effective security posture snapshot

During the process-wide route bootstrap, OpenClaw resolves deployment, runtime, connector,
control-plane, and surface decisions once into an immutable `EffectiveSecurityPosture` snapshot.
The snapshot records configuration presence and stable decision codes, not secret values.

Startup gates, control-plane policy, and surface authorization reuse this same object identity so
process-static security decisions cannot drift between modules. Request-dynamic controls such as
authentication, replay checks, and rate limiting still evaluate each request using their normal
runtime inputs.

The owner modules are:

- `services/bootstrap/lifecycle.py`
- `services/bootstrap/registration.py`
- `services/posture/effective.py`

Legacy imports remain identity-preserving aliases. See
[Service Domain Packages](architecture/service_domain_packages.md) for the ownership contract.

## Public deployment shared-surface acknowledgement

When running deployment profile checks for public posture (`OPENCLAW_DEPLOYMENT_PROFILE=public`),
you must explicitly acknowledge that upstream boundary controls are in place:

- `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1`

Why this exists:

- OpenClaw shares ComfyUI's listener/port.
- Route auth on `/openclaw/*` does not automatically protect ComfyUI-native high-risk routes.
- Public posture requires reverse-proxy path allowlist + network ACL boundaries.

Gate behavior:

- missing ack in public profile: deployment profile check fails (`DP-PUBLIC-008`)
- ack present: this boundary contract check passes

## Connector allowlist posture in strict profiles

Connector token/enable markers activate ingress posture checks for:

- Telegram, Discord, LINE, WhatsApp, WeChat, KakaoTalk, Slack, Feishu/Lark

When a platform is active, at least one platform-specific allowlist variable must be configured.

- `public` deployment profile: fail-closed (`DP-PUBLIC-009`)
- `hardened` runtime profile: fail-closed at startup gate
- non-strict local/LAN posture: warning posture in Security Doctor (`s32_allowlist_coverage`)

## External tool sandbox diagnostics

External tool execution is opt-in and remains admin-gated. Set `OPENCLAW_ENABLE_EXTERNAL_TOOLS=true` only for reviewed local/LAN workflows that need allowlisted CLI execution.

Current runtime behavior:

- the package-owned `data/tools_allowlist.json` ships with no tools, so enabling the feature flag alone runs nothing
- custom tool definitions must be supplied with `OPENCLAW_TOOLS_CONFIG_PATH`
- tool scratch/temp execution paths default to the state directory's `tool_sandbox/`
- `OPENCLAW_TOOL_SANDBOX_DIR` can override the scratch path for an explicitly reviewed deployment
- legacy `MOLTBOT_TOOL_SANDBOX_DIR` remains a compatibility alias

Hardened posture behavior:

- if `OPENCLAW_RUNTIME_PROFILE=hardened` and the sandbox runtime is marked unavailable, tool execution fails closed before `subprocess.run`
- tools without an explicit `sandbox` block fail closed in hardened mode
- network-enabled tools require `allow_network_hosts` in hardened mode
- filesystem allowlists are checked before execution; out-of-scope paths are blocked

Tool definition rules:

- `command` is a non-empty list of strings; arguments are passed as a list and never through a shell
- the first element is the program: a bare program name or an absolute path, with no `{placeholder}`; definitions that break this rule are skipped at load with a warning
- a bare program name is resolved only from absolute `PATH` entries, never from the working directory, and the absolute path is executed; use an absolute path when the interpreter you need is not on `PATH`
- on Windows, a program name without an extension resolves to `<name>.exe`, and `.bat`/`.cmd` programs are refused because `cmd.exe` would re-parse their arguments; call the interpreter explicitly instead
- every value must match its `args` pattern, and values containing control characters are rejected
- a value that starts an argument (for example `{path}` or `{name}.txt`) must not begin with `-`; put flag-like values after a fixed prefix such as `--offset={n}`
- with `allow_fs_read`/`allow_fs_write`, every value must resolve (after links) inside one of the listed directories; relative values are resolved against the tool's working directory, the sandbox workspace
- rejection messages and logs name the argument, not its value

Example definition file for `OPENCLAW_TOOLS_CONFIG_PATH`:

```json
{
    "tools": [
        {
            "name": "python_version",
            "description": "Print the interpreter version",
            "command": ["/opt/python/bin/python3", "--version"],
            "args": {},
            "timeout_sec": 5,
            "max_output_bytes": 1024,
            "sandbox": {"network": false, "allow_fs_read": [], "allow_fs_write": []}
        },
        {
            "name": "word_count",
            "description": "Count words in a file under the shared input folder",
            "command": ["wc", "-w", "{path}"],
            "args": {"path": "^[A-Za-z0-9_./-]+$"},
            "timeout_sec": 5,
            "max_output_bytes": 1024,
            "sandbox": {"network": false, "allow_fs_read": ["/srv/comfyui/input"], "allow_fs_write": []}
        }
    ]
}
```

Replace the paths with ones that exist on your host; on Windows use absolute paths such as `C:\\Python313\\python.exe` (escaped in JSON).

Common service-level diagnostic codes:

- `sandbox_runtime_unavailable`
- `sandbox_policy_missing`
- `network_hosts_missing`
- `interpreter_missing`
- `timeout`
- `workspace_violation`
- `process_failed`

These diagnostics are designed to help operators fix local runtime setup without silently falling back to broader host execution. OpenClaw does not currently install Docker images, repair sandbox runtimes, or auto-delete runtime dependency caches.

## Localhost no-origin override posture

`OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN` controls a localhost convenience escape hatch for clients
that do not send `Origin` / `Sec-Fetch-Site` headers.

- Default/unset: strict behavior remains active (no-origin requests are denied in convenience mode).
- When set to `true`: no-origin localhost requests are allowed in convenience mode.

Operational visibility:

- Startup emits an explicit warning when the override is enabled.
- Security Doctor reports this as an explicit posture check (`csrf_no_origin_override`, code `SEC-CSRF-001`).
- Startup audit includes a dedicated event so operators can trace when this override is active.

Security note:

- Keep this override disabled for shared/LAN/public deployments.
- Enable only for local CLI/tooling compatibility, and only as long as required.

## Module startup boundaries

Module enablement is decided during startup and then locked.

Current boundary behavior:

- Core, security, observability, scheduler, webhook, and connector modules are initialized at startup
- Bridge module initialization is conditional on `OPENCLAW_BRIDGE_ENABLED`
- If bridge is disabled, bridge route registration is skipped

## Bridge protocol handshake

Sidecar startup performs protocol compatibility negotiation with:

- `POST /bridge/handshake`

Request body:

```json
{ "version": 1 }
```

Response behavior:

- `200` when compatible
- `409` when incompatible (too old or too new)
- Includes compatibility metadata such as server version and minimum supported version

The sidecar bridge client executes this handshake during startup before worker polling.

## Recommended startup baseline

Use this as a starting point for hardened deployments:

```bash
OPENCLAW_RUNTIME_PROFILE=hardened
OPENCLAW_ADMIN_TOKEN=replace-with-strong-token
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=replace-with-strong-secret
OPENCLAW_BRIDGE_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_TOKEN=replace-with-bridge-device-token
# Optional: clear stale history in startup log views
OPENCLAW_LOG_TRUNCATE_ON_START=1
```

Then validate:

1. Restart ComfyUI and check startup logs for security gate result.
2. Call `GET /openclaw/capabilities` and confirm `runtime_profile`.
3. If sidecar is used, verify handshake succeeds before worker polling begins.
