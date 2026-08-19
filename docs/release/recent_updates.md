# Recent Updates History

This file preserves the longer update archive that was previously embedded directly in the README.
The README now keeps only the most recent 3-5 update summaries and links here for the full historical record.

Newest entries appear first.

Current release notes: [v1.1.0](v1.1.0.md).

<details>

<summary><strong>Current host support and startup verification refreshed</strong></summary>

- Refreshed the active references to ComfyUI `3aba3dae` / `0.33.0` and standalone frontend
  `1.52.1`, while retaining legacy Desktop `0.9.4` as a fixed bundle and current
  Comfy-Desktop `1.0.32-rc.1` as a managed-install host with installation-specific components.
- Aligned the public runtime policy around validated Python 3.10-3.13 support, best-effort Python
  3.14 compatibility, and an unsupported floor below Python 3.10.
- Added loader-realistic regression coverage that exercises the packaged entrypoint with a ready
  host server and verifies real route-family registration on the first startup attempt without
  retry or process-state leakage.
- Expanded the existing adversarial classifier to cover the exact startup loader, route-bootstrap,
  registration, contract, and import-fallback owners without broadening unrelated path matching.

</details>

<details>

<summary><strong>Startup, security posture, and architecture boundaries hardened</strong></summary>

- Added a dependency-light production source verifier to pre-commit. It parses tracked Python
  imports without importing application modules and fails on unknown ownership, forbidden
  dependency direction, cycles, and unreviewed dynamic imports.
- Patched transitive frontend development dependencies `ws`, `postcss`, and resolver-owned
  `nanoid` without changing the root manifest or runtime dependency boundary. Windows/Linux
  full-test, pre-push, and CI security paths now run a fresh `npm ci` and block high/critical
  findings across the complete production and development dependency tree.
- Replaced coarse startup reporting with typed, redacted phase and state outcomes, bounded retry
  and timing metadata, and explicit optional warmup results.
- Consolidated process-static deployment and security decisions into one immutable, secret-free
  effective posture snapshot reused by startup, control-plane, and surface guards.
- Moved startup lifecycle, route registration, and effective posture implementations into focused
  service-domain owner packages while preserving legacy module identity aliases.
- Replaced the public systemd environment file with an `.env.example`-style template and retained
  a hard version-control boundary around secret-bearing environment files.

</details>

<details>

<summary><strong>Host alignment, Parameter Lab, and native workflow ownership refreshed</strong></summary>

- Split host metadata between legacy fixed-bundle Desktop and current managed-install
  Comfy-Desktop. Presence of `window.__comfyDesktop2` identifies the current host generation but
  never authorizes privileged bridge capability calls.
- Excluded ComfyUI's `datasets` user-data root from model inventory and Model Manager destination
  handling so training data is not treated as managed model weights.
- Bounded Parameter Lab creation and persistence to string, boolean, integer, and finite-number
  values, with byte/count limits, grid-only sweep validation, and explicit rejection of nested or
  ambiguous values.
- Correlated Parameter Lab queue ownership through reviewed `promptQueueing` / `promptQueued`
  request IDs and transient workflow receipts, failing closed on unsupported, malformed, busy, or
  ambiguous host queue boundaries.
- Recognized advanced 3D `result` references as bounded source links without consuming later
  metadata or rendering binary content.
- Documented native ComfyUI ownership for video/webcam inputs, audio and text-to-speech flows, and
  the Graph/Workflows workspace instead of introducing duplicate OpenClaw node or workspace stacks.

</details>

<details>

<summary><strong>Maintainability, scale safeguards, and verification governance strengthened</strong></summary>

- Added a pinned incremental Ruff/Mypy policy to local, pre-commit, and CI validation. Existing
  debt remains explicitly governed while new production-path findings fail the gate.
- Established deterministic scale baselines for 10,000-record jobs history, bounded connector
  summaries, and 1,024 frontend output refs. Exact call counts, payload bounds, and stable digests
  are enforced; host-sensitive elapsed time remains advisory.
- Classified and hardened selected config, connector, and platform-adapter exception boundaries,
  preserving cancellation, compatibility fallback, public status mapping, and redacted logging.
- Decomposed API route and configuration ownership, connector command dispatch, Slack and Feishu
  ingress/installation/delivery seams, and frontend API/Settings ownership behind stable facades.
  Public routes, patch seams, security controls, singleton identity, DOM structure, and host
  lifecycle behavior remain contract-tested.
- Promoted the backend coverage floor from 45% to 55% only after reconstructing two consecutive
  release snapshots, retaining full-suite artifact hashes and all required hotspot percentages,
  and assigning targeted regression owners. Incomplete, nonconsecutive, malformed, or atomically
  mismatched promotion evidence now fails closed.

</details>

<details>

<summary><strong>Secure jobs visibility and connector summaries completed</strong></summary>

- Replaced the placeholder jobs listing with an Admin-only, versioned in-process read
  model over current ComfyUI queue/history state, including five lifecycle states and
  bounded status/workflow filters, sorting, pagination, and source/scan diagnostics.
- Reduced every listed job to an allowlisted summary and excluded raw prompts, workflows,
  execution errors, tracebacks, current inputs/outputs, tenant/client/trace identifiers,
  reasoning, and internal content from successful responses and audit details.
- Preserved authoritative empty snapshots while distinguishing unsupported host contracts
  (HTTP 501) from unavailable or malformed snapshots (HTTP 503), so failures cannot look
  like an empty queue.
- Added an Admin-class connector `/jobs` summary that validates contract version 1,
  renders bounded aggregate counts and short IDs, keeps the raw payload out of the chat
  LLM, and permits only a coarse queue-count fallback for explicit 501/503 conditions.

</details>

<details>

<summary><strong>Host compatibility reference anchors refreshed</strong></summary>

- Refreshed the active compatibility baseline to ComfyUI `1377a2f7` (`v0.27.0-47-g1377a2f7`, pyproject `0.27.0`) and standalone frontend `1.48.1` (`ceb5ae1eba`, `v1.48.1-1-gceb5ae1eba`).
- Kept Desktop pinned separately at `0.9.4` with core `0.22.3` and embedded frontend `1.43.18`, explicitly lagging the standalone frontend reference.
- Documented current host asset `loader_path` and namespaced model-tag schema without adopting direct `/api/assets` runtime access or changing OpenClaw's Node.js 18+ test policy.
- Added bounded Job Monitor previews for allowlisted text refs emitted under the host `files` output key, using only same-origin `/view`, strict textual MIME/UTF-8 streaming limits, literal DOM text, and explicit source-link fallback states.

</details>

<details>

<summary><strong>Host compatibility, output previews, media safety, and graph guards refreshed</strong></summary>

- Published host compatibility notes now pin the current ComfyUI, standalone frontend, and Desktop reference anchors while keeping Desktop embedded-frontend lag explicit.
- Output previews keep filename-backed refs first-class, accept optional `asset_hash` / `hash` metadata when present, and leave asset-service-only identifiers as explicit fallback states.
- LINE and WhatsApp connector media URLs now force dangerous active content such as SVG/HTML/JS/CSS/XML to download with no-sniff response headers while preserving safe image delivery.
- Job Monitor now treats HDR `.exr` and `.hdr` image outputs as explicit source-preview fallback links instead of normal thumbnails, matching the current host expectation without bundling a HDR viewer.
- Parameter Lab and graph-helper coverage now preserve non-numeric node IDs and promoted-widget source metadata, while structured color/box widget inputs stay out of missing-model diagnostics.

</details>

<details>

<summary><strong>Targeted connector cancellation and host contract guard coverage refreshed</strong></summary>

- Connector `/stop`, `/cancel`, and `/interrupt` commands now keep no-argument global interrupt explicit while routing supplied job IDs through targeted ComfyUI job cancellation.
- Single-job cancellation on older hosts can fall back only to targeted interrupt; multi-job cancellation failures no longer degrade into a global interrupt.
- Compatibility guard coverage now documents SaveImage-style output refs, 3D preview refs, typed asset dimensions, grouped asset behavior, sidebar registration fallback, and the OpenClaw Node.js runtime policy.
- OpenClaw keeps its own package/test harness on Node.js `>=18.0.0` while documenting that standalone ComfyUI frontend development may require a newer Node engine.

</details>

<details>

<summary><strong>Package hygiene, runtime cache ownership, and tool diagnostics tightened</strong></summary>

- Moved developer-only verification helpers out of the repository root and into the dedicated developer tooling area, keeping the custom-node package root focused on shipped package entrypoints and metadata.
- Made the default external-tool allowlist package-owned at `data/tools_allowlist.json`; custom allowlists are now explicitly routed through `OPENCLAW_TOOLS_CONFIG_PATH` instead of being accidentally masked by state-dir or bind-mounted source layouts.
- Added a dependency-light runtime hygiene contract that separates package resources, state-directory runtime cache/sandbox paths, and repo-local generated validation artifacts.
- Preserved the no-automatic-repair posture for runtime dependency caches: OpenClaw does not delete, migrate, or repair generated runtime dependency caches without an explicit future implementation.
- Added deterministic tool execution diagnostics for missing sandbox runtime, missing executable/interpreter, timeout, workspace/path violation, and process failures while keeping hardened missing-runtime behavior fail-closed and avoiding Docker or broader fallback execution.

</details>

<details>

<summary><strong>ComfyUI host compatibility, media outputs, model folders, and prompt attribution refreshed</strong></summary>

- Refreshed the published compatibility baseline for ComfyUI `51bf508a` (`v0.27.0-25-g51bf508a`, pyproject `0.27.0`), standalone frontend `1.47.6`, and Desktop `0.9.4` with core `0.22.3` plus embedded frontend `1.43.18`.
- Reconciled active prompt state after backend or SSE reconnects so completed prompts are not left in the active queue lane after a host recovery.
- Updated sidebar registration to prefer the current ComfyUI sidebar store API and keep the deprecated frontend facade as a compatibility fallback for older hosts.
- Aligned Model Manager and preflight diagnostics with current ComfyUI model folder names, including newer managed keys such as `gligen`, `latent_upscale_models`, `hypernetworks`, `photomaker`, `model_patches`, `geometry_estimation`, and `detection`, while retaining legacy aliases such as `clip` and `unet`.
- Made output parsing media-aware for current previewable result groups (`images`, `video`, `audio`, `3d`, and bounded `text`) while keeping image callbacks compatible and keeping asset-only identifiers as explicit fallback states instead of silently upgrading to `/api/assets`.
- OpenClaw prompt submissions now include stable `comfy_usage_source` attribution when missing, without overwriting caller-provided attribution or copying prompt/tenant/trace content into that field.

</details>

<details>

<summary><strong>Connector replay, reply visibility, and scheduled delivery behavior aligned with current chat workflows</strong></summary>

- Connector event handling now distinguishes duplicate committed actions from retryable pre-delivery failures across supported chat adapters, reducing accidental re-execution while still allowing safe retries.
- Reply visibility is now governed by a shared connector policy for direct messages, shared chats, threads, internal delivery, and tool-only contexts; suppressed text is logged as a successful no-op instead of a delivery failure.
- Telegram topics, Slack threads/workspaces, and Feishu account/workspace context are preserved for immediate replies and delayed result or approval follow-up, while approval/action buttons remain visible.

</details>

<details>

<summary><strong>Startup lifecycle diagnostics, connector SecretRef service boundaries, and internal prompt isolation aligned with the current runtime</strong></summary>

- Health diagnostics now distinguish required startup readiness, optional warmup degradation, and fatal startup failures; optional warmups run after route registration and no longer block baseline API availability.
- Connector/service launch planning now has a secret-blind env-backed SecretRef boundary that preserves supported connector credential references without expanding raw token values, while rejecting raw secrets, legacy marker strings, unsupported envs, and runtime-only auth tokens.
- Operator-visible and audit payload sanitization now removes explicitly marked internal maintenance/helper prompt content before normal reasoning redaction, while leaving ordinary user text intact.

</details>

<details>

<summary><strong>Host compatibility anchors and inactive-branch preflight diagnostics aligned with current ComfyUI hosts</strong></summary>

- Refreshed the published compatibility matrix for current ComfyUI, standalone frontend, and desktop reference anchors, keeping desktop embedded-frontend lag explicit instead of assuming standalone-frontend parity.
- Updated workflow portability and preflight diagnostics so muted or bypassed workflow branches are separated from actionable missing-node/model failures when frontend workflow metadata is available.
- Explorer now surfaces inactive-branch findings as suppressed diagnostics, so operators can still inspect them without treating them as current workflow blockers.
- Tightened repository ignore rules so public release documentation is not accidentally hidden from version control.

</details>

<details>

<summary><strong>Slack interactive callbacks, canonical node categories, and hardening governance aligned with the current runtime</strong></summary>

- Added Slack interactive callback handling for Block Kit actions, modal submissions, and workflow-style payloads, with signed ingress verification, replay/idempotency checks, bounded external errors, and policy-aware routing for run-affecting actions.
- Aligned shipped node metadata on the canonical `openclaw` category while keeping legacy `Moltbot*` class aliases available for existing workflows.
- Tightened node and frontend maintainability by moving batch-variant randomized seed imports to module scope and keeping tab DOM wiring on shared text-safe helper paths.
- Added explicit verification ownership for the `safe_io` and security-boundary hotspot families so future coverage ratchets depend on targeted regressions instead of broad coverage alone.
- Hardened exception-boundary governance around selected startup and connector paths so unexpected route/bootstrap or trust-parsing failures are surfaced instead of silently masked.

</details>

<details>

<summary><strong>Packaging boundaries, node portability guidance, config ownership seams, and connector extraction diagnostics aligned with the current runtime</strong></summary>

- Made the supported packaging model explicit: the ComfyUI custom node pack remains the primary artifact, the embedded operator platform is the first-class runtime identity, and the connector stays an optional attached subsystem rather than a separate published package.
- Added a stable node portability contract so inventory/preflight diagnostics can expose OpenClaw node metadata and deterministic replacement hints when a workflow depends on nodes that are not available in the current host.
- Consolidated the remaining high-churn package-boundary import hotspots onto shared import-fallback helpers so minimal or partially optional environments degrade predictably instead of crashing on module import.
- Split runtime-config ownership into focused storage, policy, and operator-projection seams while keeping the public runtime-config facade and precedence contract stable for existing callers and operators.
- Added admin-only connector extraction diagnostics at `/openclaw/connector/extraction-contract` (with legacy `/moltbot/*` parity) so maintainers can query the current no-split recommendation, seam families, and blockers from one machine-readable source of truth.

</details>

<details>

<summary><strong>Verification governance, config bootstrap hygiene, and connector env hardening aligned with the current runtime</strong></summary>

- Promoted the staged coverage-ratchet baseline to the then-enforced `45%` floor, added retained review-cycle evidence for hotspot families, and wired backend coverage collection through one shared local/CI helper instead of ad hoc `fail_under` edits.
- Added focused connector and config/bootstrap hotspot regressions, reviewed the governed hotspot-family coverage summaries, and retired the temporary promotion-gap exceptions now that both promotion-blocking families are represented by explicit review evidence.
- Added fail-closed test-debt governance for no-skip modules and mutation-survivor allowlist entries, with explicit `reason` and `review_after` metadata now enforced by the standard full-test flow.
- Hardened pack metadata/version fallback parsing and made config/bootstrap imports side-effect-safe, so pack version fallback stays deterministic and importing config helpers no longer creates the state directory or log file before first real use.
- Added bounded connector numeric env parsing for delivery, media, timeout, rate-limit, command-length, OAuth TTL, and bind-port settings, so malformed values degrade to documented defaults or clamps with warnings instead of crashing startup.

</details>

<details>

<summary><strong>Output contract, outbound egress handling, Security Doctor structure, and audit verification tooling aligned with the current runtime</strong></summary>

- Kept `/history` + `/view` as the supported runtime output contract for current operator flows, and made asset-service-only refs stay explicit as a bounded fallback state instead of silently guessing a direct `/api/assets` fetch path.
- Consolidated outbound safe HTTP execution behind one shared `safe_io` executor seam so local-provider checks, connector callbacks, and redirect handling now follow the same SSRF-safe validation, pinning, and redirect re-check rules.
- Split Security Doctor internals into focused endpoint, runtime, connector, report, and remediation modules while keeping the operator-facing doctor API and remediation workflow unchanged.
- Added retained audit-chain verification tooling, including a persisted `audit.log.key` sidecar when no environment key is provided, so operators can verify the current audit log plus retained rotations after restart or log rotation.

</details>

<details>

<summary><strong>Provider URL parity and CI harness resilience tightened for local LLM defaults and Playwright bootstrap stability</strong></summary>

- Fixed the built-in `Ollama (Local)` provider default so OpenClaw's OpenAI-compatible requests now target the correct `/v1` surface by default, and existing loopback-root overrides are normalized onto the same bounded path instead of failing on `/models` or `/chat/completions` at the daemon root.
- Added a provider URL contract matrix that pins built-in provider defaults, adapter endpoint assembly, and bounded Ollama normalization in one regression lane so future `LM Studio`, `Ollama`, and custom OpenAI-compatible drift is caught before release.
- Hardened the shared Playwright harness bootstrap so a single transient `openclaw.js` module-fetch failure in CI is retried once instead of failing the whole UI load, while still surfacing real import/runtime errors as hard test failures.

</details>

<details>

<summary><strong>PNG Info sidebar workflow added with ComfyUI metadata extraction, better large-image handling, and lower-noise operator alerts</strong></summary>

- Added a new `PNG Info` sidebar tab with drag-and-drop, file picker, scoped paste, preview rendering, prompt copy actions, structured summary cards, and raw metadata inspection for saved generation images.
- Added backend metadata parsing for A1111 infotext and ComfyUI `prompt` / `workflow` metadata, including prompt/sampler/model/size extraction from standard ComfyUI graphs and a larger dedicated payload ceiling for original metadata-bearing images.
- Improved operator-facing UX by making large-image failures explain the metadata-preservation constraint more clearly, letting the PNG Info input area scroll with the rest of the content, and moving prompt copy surfaces to the top of the information area.
- Reduced noise in ComfyUI prompt extraction so generic custom `CLIPTextEncode*` nodes now prefer explicit prompt-bearing keys instead of surfacing parser/config strings as if they were prompt text.
- Tightened queue-monitor alert sensitivity so sidebar startup races no longer generate persistent disconnect noise unless the backend stays unavailable long enough to look like a real incident.

</details>

<details>

<summary><strong>Repo-native CodeQL baseline and residual GitHub Security verification chain completed</strong></summary>

- Added a versioned GitHub Actions `CodeQL` workflow that scans Python, JavaScript/TypeScript, and GitHub Actions on push, pull request, manual dispatch, and a weekly schedule, so static security analysis now has an explicit in-repo baseline instead of depending only on opaque UI configuration.
- Kept the rollout visibility-first: CodeQL is now a GitHub Actions security lane and documented CI boundary, but it is not treated as a new local mandatory full-SOP command; local acceptance stays seam-first while GitHub-hosted scanning owns the repository-wide static-analysis baseline.
- Closed the acceptance-gap that surfaced during the residual verification push by propagating `defusedxml` through `requirements.txt`, preflight checks, local acceptance bootstraps, and CI preflight installation, with a repo-local dependency-parity regression seam to prevent future drift.
- Re-ran the full existing pre-push acceptance gate successfully after the parity fix: detect-secrets, pre-commit, governance verification, backend full suites, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Security hardening wave completed across CI permissions, path boundaries, redaction, connector ingress, notification rendering, and GitHub security closure</strong></summary>

- Verified the minimal Vite development-tooling hotfix path already merged cleanly, so the repo now resolves the patched `vite` version without broadening the frontend toolchain scope.
- Added explicit least-privilege GitHub Actions `permissions:` declarations and a repo-local regression seam so workflow token scope drift is now treated as a tracked security regression instead of an implicit repository default.
- Hardened checkpoint, integrity, and managed model-transfer path handling to fail closed on invalid IDs, traversal markers, and rebased install targets, with focused regression coverage on every flagged filesystem sink.
- Replaced raw security-sensitive identifiers in bridge, auth, audit, proxy, and safe-IO diagnostics with stable redacted tags, and upgraded sensitive hashing paths to keyed constructions instead of plain or hardcoded hash inputs.
- Tightened connector ingress failure handling so WeChat rejects unsafe XML declarations before parser entry, while Slack and Feishu return bounded external failure text/codes instead of echoing raw exception detail.
- Added a targeted Playwright seam proving notification payloads render as escaped text rather than live markup, locking the production notification sink against future HTML-interpolation regressions.
- Completed the GitHub-side closeout for the same wave by switching the repository from GitHub code-scanning default setup to the versioned advanced CodeQL workflow, dismissing the final residual CodeQL false positives with recorded rationale, resolving the historical docs-only secret-scanning false positive, and bringing GitHub `Code scanning` / `Secret scanning` back to `0` open findings as of `2026-04-08`.

</details>

<details>

<summary><strong>Desktop host parity lane, refreshed compatibility anchors, and live-backend mock parity completed</strong></summary>

- Added an executable desktop-host regression lane for the OpenClaw sidebar and Remote Admin Console, so desktop-specific runtime drift is now verified separately from standalone frontend assumptions instead of being left to unit-only host detection.
- Added shared Playwright host/runtime shims and remote-admin baseline mocks so desktop-host metadata, approvals refresh behavior, and host-sensitive UI evidence stay deterministic under the test harness.
- Refreshed the recorded compatibility anchors against the current reference ComfyUI, ComfyUI Frontend, and Desktop hosts, keeping desktop embedded-frontend lag explicit in the published compatibility matrix and governance checks.
- Updated the mocked live-backend parity lane so image-output surfaces now return deterministic mocked output artifacts, closing the remaining preview/result gap in the real-backend-style E2E contract.
- Re-validated the combined batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Feishu connector chain completed with long-connection transport, tenant-aware bindings, and interactive approval callbacks</strong></summary>

- Added a Feishu/Lark connector baseline that supports both long-connection and webhook ingress modes, keeps transport behavior aligned through the shared connector authorization model, and makes host-domain differences explicit through `feishu` vs `lark` account binding metadata instead of ad hoc runtime branching.
- Added Feishu account/workspace installation bindings with fail-closed resolution, tenant-aware diagnostics, normalized installation records, and support for multi-account binding manifests so one connector runtime can host more than one Feishu workspace contract safely.
- Added Feishu interactive-card callback handling for approval and command actions, including signed callback envelopes, stale/replay rejection, duplicate-action dedupe, actor-context mapping, and explicit approval downgrade when untrusted users press run-affecting actions.
- Updated the connector runtime so websocket-mode Feishu deployments also host the callback ingress surface, keeping interactive-card approvals available even when message ingress is handled over long connection instead of pure webhook mode.
- Re-validated the full Feishu batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Snapshot-first diagnostics, delta polling contracts, schema alignment, and optional-dependency import hardening completed</strong></summary>

- Moved Explorer inventory diagnostics onto a snapshot-first contract so `/openclaw/preflight/inventory` returns quickly with explicit `snapshot_ts`, `scan_state`, `stale`, and `last_error` metadata while deep refresh continues in the background.
- Hardened event and managed-download polling around deterministic cursor metadata, so operator surfaces can resume from `effective` and `next` sequence markers instead of relying on duplicate-prone full refresh loops.
- Unified webhook and managed-model request/documentation fixtures around one shared contract bundle, tightened model-import destination validation to reject traversal markers fail-closed, and kept the published API/OpenAPI surfaces aligned with the runtime validators.
- Removed the remaining import-time `aiohttp` traps from high-impact route/service modules by moving them onto one bounded compatibility seam, so minimal environments degrade deterministically at call time instead of crashing on module import.
- Re-validated the full batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Frontend host compatibility, asset-backed output interop, and CI audit alignment completed</strong></summary>

- Hardened frontend host compatibility against current standalone frontend and desktop bundle drift by moving graph/widget compatibility logic onto shared host helpers, adding explicit sidebar host-surface stamping, and surfacing desktop embedded-frontend parity through compatibility diagnostics instead of implicit assumptions.
- Added a bounded asset-output interoperability seam so classic ComfyUI history refs and newer asset-backed refs both resolve through the existing `/view` contract, preserving current temp/output behavior while allowing hash-backed previews where upstream metadata provides them.
- Updated output/history-facing frontend and backend parsers together, so `Jobs` previews, callback payload image refs, and history extraction follow one canonical path rather than duplicating view-URL assembly logic in separate layers.
- Refreshed compatibility anchors against the current reference repos and fixed the CI Python dependency audit path so the enforced audit checks declared project requirements instead of scanning unrelated runner/toolchain packages.
- Re-validated the implementation on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, strict implementation-record lint, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Exception-fidelity cleanup and verification-governance baseline completed</strong></summary>

- Preserved original traceback origins on the remaining planner/refiner/vision/config failure paths and aligned request-time default `LLMClient` refresh so runtime config hot-reload no longer mutates long-lived service state just to get a fresh client.
- Added explicit coverage governance in `pyproject.toml`, including the then-active `45%` `fail_under`, visible missing-line reporting, and skip-covered output, so baseline quality drift is no longer implicit.
- Added a stdlib-only governance verifier that fails closed when coverage config, adversarial mutation thresholds, SOP guidance, or mutation-survivor allowlist shape drift away from the enforced baseline.
- Wired the governance verifier into Linux/Windows full-test flows and the repo pre-push gate, keeping local CI-parity checks aligned with the enforced verification contract.
- Re-validated the full implementation on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Maintainability wave completed across routes, model operations, admin shell, and compatibility cleanup</strong></summary>

- Split route registration into focused route-family registrars while keeping one startup composition root and preserving legacy `/moltbot/*` plus `/api/*` fallback behavior.
- Split Model Manager internals into dedicated catalog, task-lifecycle, and transfer/security service slices without changing the accepted managed-download, resume, import, and recovery contract.
- Extracted sidebar notification/banner runtime and standalone admin-console browser logic into dedicated modules so the shell stays a composition root instead of a growing page-level hotspot.
- Centralized runtime generation of legacy `moltbot-*` class aliases and removed residual duplicated node image-helper wrappers, so canonical `openclaw-*` markup and shared image encoding logic now have one maintained path.
- Re-validated the full batch on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Slack multi-workspace installation flow completed, with final egress and notification-center hardening</strong></summary>

- Added Slack multi-workspace OAuth install/callback handling with single-use state validation, workspace-scoped installation binding, encrypted token refs, and workspace-aware reply routing for inbound events and delayed result delivery.
- Expanded connector diagnostics so Slack installation health now surfaces stable fail-closed states such as `ok`, `invalid_token`, `revoked`, `workspace_unbound`, and `degraded` without exposing token material.
- Moved Slack OAuth token exchange onto the same SSRF-safe outbound layer used by other protected network paths, closing the late-stage egress policy regression found during the full acceptance sweep.
- Fixed a notification-center persistence regression so dismissed model-manager failure alerts stay hidden after reload instead of being immediately re-created by repeated background refresh failures, while historical storage remains intact.
- Re-validated the final implementation on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Planning, startup/config hardening, compatibility governance, and frontend hotspot reduction batch</strong></summary>

- Normalized the active maintainer planning surface and clarified the docs-only test-flow exemption in the project SOP guidance.
- Hardened route/bootstrap registration around a declarative manifest and centralized validation seam so startup wiring is less fragile under delayed readiness and import-order edge cases.
- Completed the next config-unification pass around one effective-config read facade, reducing precedence drift across backend and frontend-facing config consumers.
- Centralized legacy compatibility handling for backend headers and frontend API/storage fallbacks so deprecation behavior is explicit, shared, and regression-covered.
- Split the frontend shell hotspot and LLM model-list helper logic into smaller seams, then fixed the timer-binding regression uncovered during full-gate Playwright validation.

</details>

<details>

<summary><strong>Private-host LLM SSRF contract clarified across Remote Admin, docs, and deployment guidance</strong></summary>

- Clarified that `OPENCLAW_LLM_ALLOWED_HOSTS` only extends the exact public-host allowlist for custom LLM `base_url` values and does not permit private/reserved LAN targets by itself.
- Updated Remote Admin and model-refresh SSRF error messages so operators can distinguish public-host allowlisting, scoped private-network allowance, and the explicit insecure override for private-IP targets.
- Documented Windows portable env inheritance expectations, including the need to set variables before launching `python_embeded\python.exe`, restart after changes, and avoid unsupported wildcard entries such as `*`.
- Fixed request-time parity so Remote Admin validation, `/openclaw/llm/models`, and outbound provider requests now honor the same scoped private-network allowance or explicit insecure override for intentional private-host/HTTP LLM targets.
- Added a pre-commit autofix guard that regenerates `docs/openapi.yaml` when OpenAPI contract/generator inputs change, preventing generated-spec drift from surfacing only at push time.
- Added regression coverage for the clarified SSRF error contract and re-validated with the full SOP gate.

</details>

<details>

<summary><strong>Inventory indexing moved to snapshot-first refresh with background deep-scan</strong></summary>

- Changed `/openclaw/preflight/inventory` to return a fast snapshot first, then refresh inventory state in the background instead of blocking on full directory traversal.
- Added snapshot freshness/status metadata (`snapshot_ts`, `scan_state`, `stale`, `last_error`) so the API and explorer UI can surface refresh progress and degraded scan results explicitly.
- Added bounded traversal checkpoints and background refresh scheduling to reduce latency spikes on large model directories while keeping later reads convergent.
- Added backend regression coverage for snapshot, stale/error, and API-state transitions, then validated with the full SOP gate.

</details>

<details>

<summary><strong>Model Manager reliability upgrade: resumable downloads and restart-safe recovery</strong></summary>

- Added resumable managed download support using staged `.part` artifacts plus checkpoint metadata, so interrupted transfers can continue via HTTP Range when upstream contracts are compatible.
- Added deterministic fallback-to-full restart paths when resume preconditions fail (range unsupported, validator drift, content-range mismatch) without bypassing existing provenance/SHA256 import gates.
- Added persisted download task registry with startup recovery replay and bounded replay limit control (`OPENCLAW_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT`) to prevent unbounded restart churn.
- Added backend regression coverage for resume success, fallback behavior, and replay-limit overflow handling, then validated with the full SOP gate.

</details>

<details>

<summary><strong>Reasoning trace redaction hardening and privileged local-debug reveal gate</strong></summary>

- Added a shared reasoning-redaction boundary helper so reasoning/thinking-like fields are stripped by default from assist responses, event/SSE payloads, trace responses, callback payloads, and connector-facing trace formatting.
- Added an explicit privileged reveal path that now requires request opt-in, server-side debug enablement, admin authorization, loopback source, and permissive local posture, with audit visibility for reveal attempts.
- Kept final user-visible answers intact while preventing internal reasoning traces from leaking through default operator-facing serializers.
- Closed a serializer compatibility regression found during full-gate validation and hardened WSL `/mnt/*` Playwright stability with environment-aware worker and readiness-timeout guardrails.
- Validated with the full SOP gate on WSL (detect-secrets, pre-commit, backend full suite, real-backend lanes, adversarial gate, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Embedded model operations UX update: new Model Manager tab and Parameter Lab icon fix</strong></summary>

- Added a dedicated `Model Manager` sidebar tab for model search, managed download task queueing, task lifecycle monitoring, and completed-task import into managed install paths.
- Added frontend regression coverage for the new tab flow (sidebar visibility/switching plus queue/import interaction path in Playwright E2E).
- Fixed the `Parameter Lab` tab icon contract by using a PrimeIcon class so the tab icon renders correctly in the sidebar.

</details>

<details>

<summary><strong>Multi-tenant isolation baseline, optional local secret sourcing, and layered config unification completed</strong></summary>

- Added a fail-closed tenant boundary model with tenant-scoped config/secret resolution, connector installation isolation, approvals/presets/templates visibility boundaries, and per-tenant execution concurrency caps.
- Added optional local 1Password CLI key sourcing with explicit enablement, command allowlist, template validation, and bounded fail-closed lookup behavior.
- Unified config precedence across runtime/config/provider call paths around a shared layered resolver (`env > runtime override > persisted > default`) with compatibility aliases preserved.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Optional local secret-manager baseline for safer key sourcing</strong></summary>

- Added a pluggable backend secret-provider chain for API keys (`env -> optional 1Password CLI -> encrypted server store -> none`) so operators can keep runtime keys out of plaintext deployment config where needed.
- Added fail-closed 1Password guardrails requiring explicit enablement, executable allowlist, command path validation, and bounded lookup timeout behavior.
- Added regression coverage for precedence resolution, allowlist/failure fallback behavior, and no-secret-leak logging expectations.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Today’s implementation roundup across frontend quality, planner contracts, and connector security baselines</strong></summary>

- Completed the frontend quality bundle by stabilizing canonical style ownership, adding baseline frontend unit coverage, and expanding regression coverage for Library/Approvals/admin-console parity.
- Completed SSRF pinning regression hardening with dedicated no-skip coverage for pinned connect paths, multi-IP failover ordering, and TLS wrap degradation branches.
- Completed planner profile/system-prompt externalization with validated file-backed registry loading, runtime-safe fallback/reload behavior, and synchronized profile sourcing across API, node, and Planner tab.
- Completed connector contract baseline with multi-workspace installation lifecycle registry, encrypted token references, fail-closed workspace resolution, and reusable interactive callback security decisions (signature/timestamp/hash/replay/idempotency/policy mapping) plus admin diagnostics APIs.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Connector multi-workspace installation and interactive callback contract baseline</strong></summary>

- Added a persistent connector installation registry with normalized installation identity (`platform`, `workspace_id`, `installation_id`, `token_refs`, `status`, `updated_at`) and explicit lifecycle transitions (`created`, `active`, `rotating`, `revoked`, `deactivated`, `uninstalled`).
- Enforced fail-closed workspace resolution for connector ingress (`missing`, `ambiguous`, `inactive`, and `stale token ref` bindings are rejected deterministically).
- Added reusable interactive callback security contract primitives (signed envelope, timestamp window, payload hash verification, replay/idempotency enforcement, ack/deferred callback lifecycle, and policy mapping to `public` / `run` / `admin` with explicit force-approval handling).
- Added admin read/diagnostic APIs for connector installation state, resolution evidence, and lifecycle audit visibility, with redacted outputs only.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Planner registry externalization with runtime-safe profile alignment</strong></summary>

- Moved planner profiles and the planner system prompt into validated file-backed defaults under `data/planner/`, with state-dir override precedence for operator-managed customization without source edits.
- Added a planner profile list API so the Assist planner route, Prompt Planner node, and Planner tab resolve profiles from one synchronized source-of-truth.
- Kept runtime behavior fail-closed with schema validation, prompt placeholder validation, embedded fallback defaults, and lazy reload on planner file changes.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Frontend quality baseline for Library and Approvals surfaces</strong></summary>

- Canonicalized active frontend styling ownership around `openclaw-*`, including shell/tab-manager cleanup and a deterministic split of `web/openclaw.css` into core and legacy-alias modules.
- Added a frontend unit-test lane with Vitest + jsdom plus baseline coverage for shared UI helpers and extracted Library tab state logic.
- Expanded Playwright coverage for `Library` and `Approvals`, including success/degraded paths and approvals parity between the sidebar and the Remote Admin Console.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Audit event clarity and connector ingress fail-closed hardening</strong></summary>

- Normalized audit helper behavior so config/secret/LLM-test convenience wrappers now emit one canonical audit event per action, reducing duplicate noise while preserving legacy compatibility paths.
- Added shared connector allowlist posture evaluation and enforced fail-closed startup behavior for public/hardened deployments when connector ingress is active without allowlist coverage.
- Kept local/permissive posture as warning-only, with synchronized visibility across startup checks, deployment profile checks, and Security Doctor diagnostics.
- Added focused regression coverage and completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Startup fail-closed bootstrap hardening and public boundary guardrail</strong></summary>

- Enforced strict fail-closed startup propagation so bootstrap security-gate failures are no longer logged-and-continued; route/worker registration now aborts deterministically on fatal startup failures.
- Added an explicit public deployment boundary acknowledgement contract:
  - `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` (legacy alias supported)
  - public profile gate now fails deterministically when this acknowledgement is missing.
- Added a dedicated Security Doctor boundary posture check and machine-readable environment marker so shared ComfyUI/OpenClaw surface risk is visible to operators.
- Synchronized deployment/operator docs for public boundary controls (reverse proxy path allowlist + network ACL requirements).
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Core runtime maintainability and contract hardening batch</strong></summary>

- Refactored startup/bootstrap responsibilities into clearer service slices to keep the entry path thin and easier to validate.
- Hardened provider adapter error contracts with safer HTTP error propagation and retry-after handling consistency.
- Replaced fragile JSON object extraction logic in LLM output parsing with stdlib decoder-based behavior for stronger edge-case resilience.
- Unified node/runtime consistency by converging shared image encoding helpers and internal node naming compatibility paths.
- Added and aligned regression coverage, then completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Security and reliability hotfix chain: startup gate cleanup, atomic audit writes, and clearer CSRF override posture</strong></summary>

- Cleaned up unreachable startup security-gate code after fatal raise paths, keeping fail-closed behavior explicit and reducing maintenance ambiguity.
- Hardened append-only audit integrity by making hash-chain write flow atomic under a process lock to avoid concurrent chain-fork risk.
- Added explicit startup warning when localhost no-origin override is enabled, plus a dedicated Security Doctor posture check/violation mapping for operator visibility.
- Added focused regression coverage for startup warning/doctor posture and audit lock path behavior.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Standalone remote admin mobile console for phone/desktop operations</strong></summary>

- Added an independent remote admin entry page at `/openclaw/admin` (legacy `/moltbot/admin`), separate from the ComfyUI side panel.
- Added a mobile-first admin console layout for operational flows:
  - dashboard (health, provider/key state, scheduler/runs summary, recent error lines)
  - jobs/events (recent runs + SSE connect/poll fallback)
  - approvals (approve/reject)
  - schedules/triggers (toggle/run/fire)
  - config (read + guarded write)
  - doctor/diagnostics and quick actions (retry/model refresh/drill via existing policy gates)
- Preserved backend security boundaries: remote write actions still require explicit admin-token and remote-admin policy conditions.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Executor lane split and callback I/O isolation for better saturation resilience</strong></summary>

- Added dedicated executor lanes for LLM vs I/O workloads with bounded worker controls.
- Migrated callback delivery and outbound HTTP callback paths to the I/O lane, reducing interference with LLM execution paths.
- Added queue/saturation diagnostics and executor metrics exposure in health/stat telemetry.
- Added targeted regression coverage for lane split behavior and callback I/O lane migration.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Runtime lifecycle consistency, structured logging opt-in, and generated OpenAPI spec</strong></summary>

- Completed a focused runtime operability and contract maturity batch with full SOP verification:
  - added graceful shutdown/reset consistency hooks so scheduler/failover runtime state flushes and resets are deterministic
  - added opt-in structured JSON logging for core execution paths (including queue submit and LLM client) with bounded metadata events
  - added machine-readable OpenAPI spec generation and committed `docs/openapi.yaml` for integrator/review tooling use
  - added regression coverage for runtime lifecycle state handling, structured logging behavior, and OpenAPI generation drift
  - completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Assist streaming UX and frontend fetch-wrapper safety hardening</strong></summary>

- Completed a focused assist UX + frontend transport reliability batch with full SOP verification:
  - added optional streaming assist paths for Planner/Refiner with incremental preview updates and staged progress events
  - added backend streaming endpoints for planner/refiner assist flows with capability-gated frontend enablement and safe fallback to the existing non-stream path
  - added frontend live preview rendering for Planner/Refiner while preserving cancel/stale-response safety behavior
  - added idempotent fetch-wrapper composition guards to prevent duplicate wrapper stacking during repeated frontend bootstrap/setup
  - added backend/parser/frontend regression coverage for streaming assist behavior and fetch-wrapper idempotence, plus full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Recent hardening and reliability improvements: runtime guardrails, crypto drills, compatibility governance, and safer management queries</strong></summary>

- Completed a focused reliability + operations hardening batch with full SOP verification:
  - consolidated shared frontend/backed helper paths to reduce duplicated cancellation, JSON parsing, and import-fallback logic
  - added runtime guardrails diagnostics/contract enforcement so runtime-only safety limits stay visible and cannot be persisted back into config
  - added cryptographic lifecycle drill automation with machine-readable evidence for rotation, revoke, key-loss recovery, and token-compromise scenarios
  - added compatibility matrix governance metadata plus a refresh workflow script and operator-doctor freshness/drift warnings
  - hardened management query pagination behavior with deterministic malformed-input handling, bounded scans, and clearer cursor diagnostics for admin/event list paths
  - completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Latest completion: automation composer endpoint, safer payload drafting, and full verification pass</strong></summary>

- Completed the automation payload composer flow for safe draft generation:
  - added a new admin-only compose endpoint for trigger/webhook payload drafts (generate-only, no execution side effects)
  - added strict server-side validation and normalization for trigger/webhook draft payloads
  - added tool-calling schema support for automation payload composition with deterministic fallback behavior
  - exposed composer capability flag for frontend/runtime feature probing
  - added and extended backend tests for API handler, composer service, schema/validator coverage, and capability contract
  - completed full validation gate pass (detect-secrets, pre-commit, backend test lanes, adversarial smoke gate, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Slack app support closeout: secure Events API ingress, connector parity, and no-skip verification lanes</strong></summary>

- Completed Slack implementation hardening chain with full SOP validation:
  - added Slack Events API adapter with signed ingress checks, replay/dedupe handling, bot-loop suppression, allowlist enforcement, and thread-aware reply delivery
  - wired Slack runtime policy into existing connector authorization boundaries so command trust behavior stays consistent with other platforms
  - added dedicated Slack verification lanes for ingress contract coverage and real-backend flow parity, both enforced by skip-policy and full-test scripts
  - added optional Slack Socket Mode fallback transport with fail-closed startup checks and transport-parity behavior aligned to Events API safety controls
  - expanded observability redaction coverage for Slack token families and added endpoint-level drift tests for logs/trace/config safety
  - aligned local full-test scripts so Slack phase-2 suites run explicitly as part of the Slack integration gate step
  - synchronized verification evidence through detect-secrets, pre-commit, backend unit + real lanes, adversarial gate, and frontend E2E full pass

</details>

<details>

<summary><strong>Post-Wave E closeout: Hardening chain completed</strong></summary>

- Completed on 2026-02 with full SOP validation:
  - Bundle A: established security invariants registry and startup/CI invariant gates, plus route-plane explicit-classification governance to prevent unmanaged endpoint exposure drift
  - Bundle B: converged outbound egress to a single safe path and added CI/local dependency parity preflight to prevent local-pass/CI-fail runtime drift
  - Bundle C: added adversarial verification execution gates (bounded fuzz + mutation smoke with artifacts) and dual-lane retry partition hardening for deterministic degrade/audit behavior
  - end-to-end verification evidence was synchronized across CI, local full-test scripts, and implementation records

</details>

<details>

<summary><strong>Wave E closeout: deployment guardrails, contract parity, and verification hardening chain completed</strong></summary>

- Completed Wave E with full SOP validation:
  - Bundle A delivered startup deployment gate enforcement and deployment-profile matrix parity, then locked critical operator flow parity (including degraded-path behavior)
  - Bundle B closed security contract parity gaps across token/mapping/route/signature state matrices and threat-intel resilience paths
  - Bundle C completed signed policy posture control, bounded security anomaly telemetry, deterministic adversarial fuzz harness coverage, and mutation-baseline evidence generation
  - full detect-secrets + pre-commit + backend unit + frontend E2E gate passed and evidence is recorded in the Bundle C implementation record

</details>

<details>

<summary><strong>Wave D closeout: control-plane split, ingress and supply-chain hardening, and verification governance baseline</strong></summary>

- Completed Wave D closeout full SOP validation:
  - enforced split-mode control-plane boundaries for public deployments while preserving embedded daily UX flows
  - finalized external control-plane adapter reliability behavior and split-mode degraded/blocked-action guidance
  - completed secrets-at-rest hardening v2 with split-compatible secret-reference behavior
  - closed bridge token lifecycle, legacy webhook ingress clamp, and public MAE route-plane enforcement gaps
  - replaced registry signature placeholder posture with trust-root based cryptographic verification and signer governance
  - established verification governance baseline with skip-budget enforcement, reject/degrade triple-assert contracts, and defect-first record lint gating
</details>

<details>

<summary><strong>Wave A/B/C closeout: stability baseline, high-risk security gates, and operator UX completion</strong></summary>

- Completed baseline runtime/config/connector stability improvements:
  - runtime provenance and manager-aware environment freshness checks
  - safer config merge behavior for object arrays
  - connector session invalidation resilience for 401/410 revoke paths
  - durable replay/idempotency storage for webhook/bridge flows
  - stricter outbound egress policy controls for callback and LLM targets
- Completed high-risk security and supply-chain hardening:
  - stronger external tool path resolution and allowlist enforcement
  - bridge/device binding hardening with mTLS validation controls
  - pack archive canonicalization and full manifest coverage enforcement
  - global DoS governance (quota/priority/storage controls)
  - signed release provenance pipeline and SBOM-integrity validation
- Completed Wave C operator UX and functionality closeout:
  - Wave C functionality closeout accepted on 2026-02-18 with full SOP validation
  - deterministic operator guidance banners and deep-link recovery behavior
  - capability-aware in-canvas quick actions with guarded mutation flow
  - Parameter Lab schema lock and bounded sweep/compare orchestration
  - compare winner-selection safety contract and expanded Wave C regression coverage

</details>

<details>

<summary><strong>Audit trail and external tool sandbox hardening closeout</strong></summary>

- Added non-repudiation audit coverage for sensitive config/secrets/tools/approvals/bridge and startup-dangerous-override paths.
- Standardized audit envelopes and append-only hash-chain logging to improve forensic traceability.
- Added stricter external tool sandbox controls:
  - hardened-mode fail-closed when sandbox posture/runtime is unsafe
  - explicit network allowlist requirement when tooling enables egress
  - pre-exec filesystem path allowlist enforcement for tool arguments
- Expanded security regression coverage for audit contract paths and sandbox policy enforcement.

</details>

<details>
<summary><strong>Endpoint inventory hardening and route drift detection coverage</strong></summary>

- Added explicit endpoint security metadata across API handlers so auth/risk posture is machine-readable and auditable.
- Added route inventory manifest generation to inspect registered API surfaces consistently.
- Added drift regression tests that fail when any registered endpoint is missing security metadata.
- Extended drift coverage to include optional bridge and packs routes to prevent false-green route scans.

</details>

<details>
<summary><strong>Operator UX improvements: context toolbox, parameter lab history/replay, and compare workflow baseline</strong></summary>

- Added in-canvas OpenClaw quick actions on node context menus: Inspect, Doctor, Queue Status, Compare, and Settings.
- Improved operator recovery flow by wiring quick actions to capability-aware targets with deterministic fallback guidance when optional endpoints are unavailable.
- Added Parameter Lab history flow so operators can browse saved experiments, load details, and replay run parameters back into the current graph.
- Added compare workflow baseline in Parameter Lab, including a dedicated compare endpoint with bounded fan-out and stricter payload validation.
- Expanded auth and regression coverage so compare routes remain admin-protected and route-registration drift is caught earlier.

</details>

<details>
<summary><strong>Pack security hardening: path traversal defense and strict API validation</strong></summary>

- Added path traversal protection for pack uninstall and pack path resolution.
- Hardened pack install path construction by validating pack metadata segments (`name`, `version`) and enforcing root-bounded path resolution.
- Added stricter input validation on pack API route handlers for pack lifecycle operations.
- Expanded regression coverage for traversal attempts and invalid input handling in pack flows.

</details>

<details>
<summary><strong>Runtime profile hardening and bridge startup compatibility checks</strong></summary>

- Added explicit runtime profiles with centralized resolution so startup behavior is deterministic across environments.
- Added a hardened startup security gate that fails closed when mandatory controls are not correctly configured.
- Added module capability boundaries so routes/workers only boot when their owning module is enabled.
- Added a bridge protocol handshake path with version compatibility checks during sidecar startup.
- Expanded regression coverage for profile resolution, startup gating, module boundaries, and bridge handshake behavior.

</details>

<details>
<summary><strong>Connector platform parity and sidecar worker runtime improvements</strong></summary>

- Added stronger KakaoTalk response handling:
  - strict QuickReply cap with safe truncation
  - empty-response guard to avoid invalid platform payloads
  - more predictable output shaping and sanitization behavior
- Added WeChat Official Account encrypted webhook support:
  - AES encrypted ingress (`encrypt_type=aes`) with signature verification and fail-closed decrypt/app-id validation
  - expanded event normalization coverage (`subscribe`, `unsubscribe`, `CLICK`, `VIEW`, `SCAN`)
  - deterministic dedupe behavior for event payloads without `MsgId`
  - bounded ACK-first flow with deferred reply handling for slow paths
- Added sidecar worker bridge alignment end-to-end:
  - worker poll/result/heartbeat bridge endpoints
  - contract-driven sidecar client endpoint resolution and idempotency header behavior
  - dedicated E2E test coverage for worker route registration, auth, and round-trip behavior

</details>

<details>
<summary><strong>Security Hardening: Auth/Observability boundaries, connector command controls, registry trust policy, transform isolation, integrity checks, and safe tooling controls</strong></summary>

- Delivered observability tier hardening with explicit sensitivity split:
  - Public-safe: `/openclaw/health`
  - Observability token: `/openclaw/config`, `/openclaw/events`, `/openclaw/events/stream`
  - Admin-only: `/openclaw/logs/tail`, `/openclaw/trace/{prompt_id}`, `/openclaw/secrets/status`, `/openclaw/security/doctor`
- Delivered constrained transform isolation hardening:
  - process-boundary execution via `TransformProcessRunner`
  - timeout/output caps and network-deny worker posture
  - feature-gated default-off behavior for safer rollout
- Delivered approval/checkpoint integrity hardening:
  - canonical JSON + SHA-256 integrity envelopes
  - tamper detection and fail-closed handling on integrity violations
  - migration-safe loading behavior for legacy persistence files
- Delivered external tooling execution policy:
  - allowlist-driven tool definitions (`data/tools_allowlist.json`)
  - strict argument validation, bounded timeout/output, and redacted output handling
  - gated by `OPENCLAW_ENABLE_EXTERNAL_TOOLS` plus admin access policy
- Extended security doctor coverage with wave-2 checks:
  - validates transform isolation posture
  - reports external tooling posture
  - verifies integrity module availability
- Auth-coverage contract tests were updated to include new tool routes and prevent future route-auth drift regressions.
- Added connector command authorization hardening:
  - separates command visibility from command execution privileges
  - centralizes per-command access checks to reduce cross-platform auth drift
  - supports explicit allow-list policy controls for sensitive command classes
  - adds operator-configurable command policy controls via `OPENCLAW_COMMAND_OVERRIDES` and `OPENCLAW_COMMAND_ALLOW_FROM_{PUBLIC|RUN|ADMIN}`
- Added registry anti-abuse controls for remote distribution paths:
  - bounded request-rate controls and deduplication windows reduce abuse and accidental hot loops
  - stale anti-abuse state pruning keeps long-running deployments stable
- Added registry preflight and trust-policy hardening:
  - static package safety checks are enforced before activation paths
  - policy-driven signature/trust posture supports audit and strict enforcement modes
  - registry trust mode is operator-controlled via `OPENCLAW_REGISTRY_POLICY` and preflight verification enforces fail-closed file-path requirements

</details>

<details>
<summary><strong>Sprint A: closes out with five concrete reliability and security improvements</strong></summary>

- Configuration save/apply now returns explicit apply metadata, so callers can see what was actually applied, what requires restart, and which effective provider/model is active.
- The Settings update flow adds defensive guards against stale or partial state, reducing accidental overwrites.
- Provider/model precedence is now deterministic across save, test, and chat paths, and prevents model contamination when switching providers.
- In localhost convenience mode (no admin token configured), chat requests enforce same-origin CSRF protection: same-origin requests are allowed, cross-origin requests are denied.
- Model-list fetching now uses a bounded in-memory cache keyed by provider and base URL, with a 5-minute TTL and LRU eviction cap to improve responsiveness and stability.

</details>

<details>
<summary><strong>Sprint B: ships security doctor diagnostics, registry quarantine gates, and constrained transforms defaults</strong></summary>

- Added the Security Doctor surface (`GET /openclaw/security/doctor`) for operator-focused security posture checks across endpoint exposure, token boundaries, SSRF posture, state-dir permissions, redaction drift, runtime mode, feature flags, and API key posture.
- Added optional remote pack registry quarantine controls with explicit lifecycle states, SHA256 integrity verification, bounded local persistence, and per-entry audit trail; this path remains disabled by default and fail-closed.
- Added optional constrained transform execution with trusted-directory + integrity pinning, timeout and output-size caps, and bounded chain execution semantics; transforms remain disabled by default and mapping-only behavior remains intact unless explicitly enabled.

</details>

<details>
<summary><strong>Settings contract, frontend graceful degradation, and provider drift governance</strong></summary>

- Enforced a strict settings write contract with schema-coerced values and explicit unknown-key rejection, reducing save/apply regressions across ComfyUI variants.
- Hardened frontend behavior to degrade safely when optional routes or runtime capabilities are unavailable, with clearer recovery hints instead of brittle failures.
- Added provider alias/deprecation governance and normalization coverage to reduce preset drift as upstream model IDs and endpoint shapes evolve.

</details>

<details>
<summary><strong>Mapping v1, job event stream, and operator doctor</strong></summary>

- Added webhook mapping engine v1 with declarative field mapping + type coercion, enabling external payload normalization without custom adapter code paths.
- Added real-time job event stream support via SSE (`/openclaw/events/stream`) with bounded buffering and polling fallback (`/openclaw/events`) for compatibility.
- Added Operator Doctor diagnostics tooling for runtime/deployment checks (Python/Node environment, state-dir posture, and contract readiness signals).

</details>

<details>
<summary><strong> Security doctor, registry quarantine, and constrained transforms</strong></summary>

- Added Security Doctor diagnostics surface (`GET /openclaw/security/doctor`) for operator-focused security posture checks and guarded remediation flow.
- Added optional remote registry quarantine lifecycle controls with integrity verification, bounded local persistence, and explicit trust/audit gates.
- Added optional constrained transform execution with integrity pinning, timeout/output caps, and bounded chain semantics; default posture remains disabled/fail-closed.

</details>
