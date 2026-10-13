# Troubleshooting

This guide keeps the longer operational troubleshooting steps out of the README.

## UI shows Backend Not Loaded / endpoints return 404

This usually means ComfyUI did not load the Python side of the pack, or route registration failed during startup.

Steps:

1. Check ComfyUI startup logs for import errors while loading the custom node pack. Search for `openclaw`, `Route registration failed`, or `ModuleNotFoundError`.
2. Confirm the pack folder is directly under `custom_nodes/` and contains `__init__.py`.
3. Run the smoke import check inside the same Python environment ComfyUI uses:

```bash
python scripts/openclaw_smoke_import.py
# or
python scripts/openclaw_smoke_import.py --verbose
```

4. Manually verify the endpoints used by the Settings tab:
   - `GET /api/openclaw/health`
   - `GET /api/openclaw/config`
   - `GET /api/openclaw/logs/tail?n=50`

Notes:

- If your pack folder name is not `comfyui-openclaw`, the smoke script may need `OPENCLAW_PACK_IMPORT_NAME=your-folder-name`.
- If imports fail with a `services.*` module error, check for name collisions with other custom nodes and prefer package-relative imports.

## Imported workflow shows missing OpenClaw nodes

Current builds expose node portability metadata so "missing custom node" can be diagnosed separately from a generic import failure.

What to check:

1. Open the Explorer / inventory diagnostics view or inspect `/openclaw/preflight/inventory`.
2. Confirm whether the workflow references `openclaw:*` nodes that are not present on the current host.
3. Look for portability/replacement guidance rather than renaming nodes blindly.
4. If Explorer shows inactive-branch suppressed findings, inspect them as context but do not treat them as active blockers unless the corresponding branch is enabled.

Notes:

- Compatibility class names such as `Moltbot*` still exist for older workflows, but the canonical portability contract is anchored on `openclaw:*` node identities.
- Current shipped nodes use the `openclaw` category in ComfyUI; seeing older `moltbot` category text usually means the installed pack is stale or ComfyUI has not been restarted after update.
- Current diagnostics may include deterministic replacement hints when an unavailable OpenClaw node can degrade to a more portable workflow pattern.
- Muted or bypassed root nodes and subgraph branches are separated into suppressed diagnostics when the workflow payload includes enough frontend metadata. Plain API prompt JSON remains deterministic, but it may not contain frontend ancestry needed to identify inactive subgraph context.
- If no portability guidance is present and the pack itself is loaded correctly, treat that as a real contract gap rather than assuming the workflow can be repaired by arbitrary JSON edits.

## Operator Doctor

Run the built-in diagnostic tool to verify environment readiness (libraries, permissions, contract files):

```bash
python scripts/operator_doctor.py
# Or check JSON output:
python scripts/operator_doctor.py --json
```

Explorer / inventory note:

- `/openclaw/preflight/inventory` is snapshot-first on current builds.
- A response showing `scan_state=refreshing` or `stale=true` does not necessarily mean the inventory path is broken; it can mean the cached snapshot was returned quickly while a deeper model scan continues in the background.
- Treat `last_error` as the primary signal that the background scan actually failed.

## Jobs list or connector `/jobs` reports authorization or backend errors

`GET /openclaw/jobs` is an Admin-only bounded read model. For direct API/browser calls,
send the configured Admin token and use only the documented `status`, `workflow_id`,
`sort_by`, `sort_order`, `limit`, and `offset` query fields.

Interpret results as follows:

- HTTP 200 with `jobs: []` is an authoritative empty snapshot.
- HTTP 401/403 means Admin authentication or tenant authorization failed; verify
  `OPENCLAW_ADMIN_TOKEN`, and for connector commands also verify
  `OPENCLAW_CONNECTOR_ADMIN_TOKEN` plus the sender's Admin allowlist/class policy.
- HTTP 501 `jobs_host_contract_unsupported` means the active ComfyUI host does not expose
  the required queue/history helper contract.
- HTTP 503 `jobs_backend_unavailable` means the host snapshot was unavailable or malformed;
  it must not be treated as an empty queue.

The connector `/jobs` command returns fixed, content-free failures. It can show a bounded
coarse queue-count fallback only for the explicit 501/503 conditions above; authorization,
unknown-version, malformed, or oversized responses do not fall back and never echo the raw
upstream payload.

## External tool execution is disabled or fails with sandbox diagnostics

External tools are disabled by default and require an admin boundary plus an explicit feature flag.

Checklist:

1. Confirm the feature flag is enabled only for the deployment that needs it:
   - `OPENCLAW_ENABLE_EXTERNAL_TOOLS=true`
2. Confirm the request is authenticated as an admin when using:
   - `GET /openclaw/tools`
   - `POST /openclaw/tools/{name}/run`
3. Confirm the tool definition exists in the allowlist:
   - default allowlist: package-owned `data/tools_allowlist.json`, which ships with no tools
   - custom allowlist: set `OPENCLAW_TOOLS_CONFIG_PATH=/path/to/tools_allowlist.json`
   - definitions whose program contains a `{placeholder}` or is a relative path such as `bin/tool` are skipped at load; check the startup log for `Skipping tool definition`
4. If the result or logs report `sandbox_runtime_unavailable`, do not bypass hardened mode blindly:
   - make the sandbox runtime available, then set `OPENCLAW_TOOL_SANDBOX_RUNTIME_AVAILABLE=1`
   - or keep tooling disabled until the deployment can fail closed safely
5. If the result or logs report `interpreter_missing`, install the executable referenced by the tool allowlist or update the command path. Bare program names are looked up only in absolute `PATH` entries, not in the working directory; use an absolute path for an interpreter that is not on `PATH`.
6. If the result or logs report `timeout`, review the command behavior before increasing the tool's `timeout_sec`.
7. If the result or logs report `workspace_violation`, move inputs under the configured filesystem allowlist or update the tool sandbox policy.

Notes:

- Tool scratch/temp paths default to the configured state directory's `tool_sandbox/`.
- `OPENCLAW_TOOL_SANDBOX_DIR` can override the scratch path for reviewed deployments.
- Runtime cache and sandbox scratch paths are generated state, not package resources.
- OpenClaw does not automatically repair, migrate, or delete runtime dependency caches.

## Jobs preview shows an explicit media or asset fallback state

Current OpenClaw builds keep `/history` + `/view` as the supported runtime preview contract for job results.

If a result ref only exposes an upstream asset-service identifier and cannot be represented through `/view`, OpenClaw keeps that ref explicit instead of silently guessing a direct `/api/assets` fetch.

What this means:

- `asset_api_required` is a bounded compatibility state, not a generic parser failure.
- Classic history refs should continue to preview normally even when hash metadata is absent. Optional hash-backed refs exposed as `asset_hash` or `hash` still map onto `/view` when ComfyUI host metadata provides them.
- Current media-aware outputs can include `images`, `video`, `audio`, `3d`, bounded inline text, and allowlisted file-backed text; normal images render as thumbnails, HDR `.exr` / `.hdr` image refs render as explicit source-preview fallback links, and text renders as literal bounded content. A file-backed text response that is oversized, slow, redirected, non-text, invalid UTF-8, or unavailable as a safe browser stream remains an explicit source-link fallback instead of exposing response details.
- If an operator workflow starts depending on direct asset-service identifiers, treat that as a contract gap and review [`docs/asset_api_adoption_decision.md`](asset_api_adoption_decision.md) before widening the runtime dependency.

## Verify audit-chain continuity after restart or rotation

Use the retained-chain verifier:

```bash
python scripts/verify_audit_chain.py
```

JSON output:

```bash
python scripts/verify_audit_chain.py --json
```

Notes:

- The verifier checks the current `audit.log` and any retained rotated audit segments in the state directory.
- When no audit chain key is supplied from environment/config, OpenClaw persists `audit.log.key` so verification still works across restart and rotation.
- Treat verification failure as an audit-integrity incident until proven otherwise.

## Webhooks return `403 auth_not_configured`

Set webhook auth environment variables as described in the README quick-start section, then restart ComfyUI.

## LLM model list shows `HTTP 403 ... Private/reserved IP blocked: 127.0.0.1`

This usually means your OpenClaw build is older than the local-loopback SSRF fix. For local providers, `127.0.0.1` and `localhost` are valid targets and should not require insecure SSRF flags.

Checklist:

1. Update OpenClaw to the latest release.
2. For Ollama:
   - run `ollama serve`
   - verify `http://127.0.0.1:11434/api/tags` is reachable on the same machine
3. In OpenClaw Settings:
   - Provider: `Ollama (Local)` or `LM Studio (Local)`
   - Base URL: leave empty to use the provider default, or set a loopback URL explicitly
   - Provider defaults:
     - `Ollama (Local)` -> `http://127.0.0.1:11434/v1`
     - `LM Studio (Local)` -> `http://localhost:1234/v1`
   - If an older saved Ollama URL is still set to `http://127.0.0.1:11434`, update it to `/v1` or clear the field so the built-in default can be applied
4. Keep these flags disabled:
   - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0`
   - `OPENCLAW_ALLOW_INSECURE_BASE_URL=0`

## Remote Admin can open, but custom LLM on `192.168.x.x` is still blocked

This is expected under the current SSRF policy.

- `OPENCLAW_ALLOW_REMOTE_ADMIN=1` only allows remote admin access; it does not relax outbound LLM egress rules.
- `OPENCLAW_LLM_ALLOWED_HOSTS` only extends the exact-host allowlist for custom public hosts.
- Private/reserved IP targets such as `192.168.x.x`, `10.x.x.x`, and `172.16.x.x` remain blocked unless the scoped `allow_private_network` LLM setting is enabled for the configured target, or `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` is also set.
- `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1` does not allow private/reserved IPs.
- `OPENCLAW_LLM_ALLOWED_HOSTS=*` is not a wildcard and will not bypass the policy.

Correct setup flow:

1. If you are using a built-in local provider (`ollama`, `lmstudio`), keep it on loopback only and use the provider default or `localhost` / `127.0.0.1` / `::1`.
2. If you need a custom public LLM host, set:
   - `OPENCLAW_ALLOW_CUSTOM_BASE_URL=1`
   - `OPENCLAW_LLM_ALLOWED_HOSTS=<exact-host>` or `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1`
3. If you intentionally need a LAN/private-IP target, prefer enabling `allow_private_network` only for that configured LLM target. Use `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` only when you intentionally accept the broader SSRF risk, then fully restart ComfyUI.
4. On Windows portable, set environment variables in the same launcher that starts `python_embeded\\python.exe`, or restart after `setx` / System Properties changes.
5. Verify the effective value in the same embedded Python runtime:

```bat
python_embeded\python.exe -c "import os; print(repr(os.environ.get('OPENCLAW_LLM_ALLOWED_HOSTS')))"
```

Safer alternative:

- keep the LLM behind a reviewed public HTTPS reverse proxy and allowlist that public host, instead of enabling private-network or insecure overrides
- on current builds, the scoped private-network setting and insecure override are both applied consistently by Remote Admin validation and `/openclaw/llm/models`

## Admin Token: server-side vs UI

`OPENCLAW_ADMIN_TOKEN` is a server-side environment variable.

- The Settings UI can use an Admin Token for authenticated requests.
- The UI cannot set or persist the server token itself.

For full setup steps, see the main README quick-start section and [`tests/TEST_SOP.md`](../tests/TEST_SOP.md).
