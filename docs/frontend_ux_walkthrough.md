# Frontend UX Walkthrough (ComfyUI-OpenClaw)

This document summarizes the current OpenClaw sidebar UI structure and how to verify it after changes.

## UI Structure

- Entry: `web/openclaw.js` registers the extension; host sidebar registration is routed through `web/openclaw_sidebar_registration.js` so current ComfyUI sidebar-store hosts and older frontend facade hosts share one compatibility path.
- Shell: `web/openclaw_ui.js` now acts as the composition root for the sidebar shell and public singleton exports.
- Actions: `web/openclaw_actions.js` owns submit/cancel/retry wiring and guarded action routing for the shell.
- Queue monitor: `web/openclaw_queue_monitor.js` owns queue polling lifecycle and transient banner/status updates used by the shell.
- Event/task polling: admin-console and model/task views consume deterministic delta metadata (`effective_since_seq`, `next_since_seq`, reset/truncation hints) instead of assuming every refresh is a full snapshot.
- Notification center: `web/openclaw_notification_center.js` owns persistent in-app notification storage, dedupe, acknowledge, dismiss, and deep-link behavior.
- Banner runtime: `web/openclaw_banner_manager.js` owns transient banner state and shell-facing banner transitions.
- Tabs: `web/openclaw_tabs.js` manages tab registration, rendering, remount safety, and optional
  pending-render disposal before switching panes.
- API: `web/openclaw_api.js` owns normalized transport, session, timeout, retry, and singleton
  behavior. Config, generation, resource, model, and event endpoint families live in focused
  `web/openclaw_api_*.js` owner modules behind the same public API (legacy Moltbot endpoints still
  work).
- Settings: `web/tabs/settings_tab.js` composes status, LLM, secrets, logs, and DOM owner modules.
  Its lifecycle owner invalidates stale async generations and clears scheduled work when the tab
  is disposed, preventing late responses from mutating a remounted pane.
- Host surface: `web/openclaw_host_surface.js` resolves standalone frontend, legacy fixed-bundle
  Desktop, and current managed-install Comfy-Desktop separately, then stamps explicit metadata so
  generation-specific behavior stays testable.
- Output refs: `web/openclaw_asset_refs.js` normalizes classic history refs, optional `asset_hash`/`hash` refs when host metadata is present, and current previewable media groups (`images`, `video`, `audio`, `3d`, bounded inline or file-backed `text`) onto one media-aware contract. Allowlisted text files under the host `files` key stay on same-origin `/view` and use a 5-second, 64-KiB streaming, strict textual-MIME/UTF-8 reader with a 4,096-character display cap. HDR `.exr` / `.hdr` image refs show source-preview fallback links instead of normal thumbnails, text reaches the DOM only as literal text, and asset-service-only refs remain explicit fallback states instead of silently auto-fetching `/api/assets`.
- Styles: `web/openclaw.css` provides shared design tokens and component classes.
- Errors and compatibility helpers: `web/openclaw_utils.js` provides `showError()` / `clearError()` plus runtime legacy-class alias helpers used to keep canonical `openclaw-*` markup compatible with existing `moltbot-*` selectors.

Refactor note:
- `web/openclaw_ui.js` should stay focused on shell composition, shared singleton ownership, and exports.
- New shell behaviors should prefer the extracted action/queue modules unless they truly belong to top-level shell assembly.
- New API methods should be added to the matching route-family owner rather than growing the
  transport facade; keep `openclawApi` as the only shared singleton.
- New Settings behavior should stay in the matching status/LLM/secrets/logs/DOM owner and use the
  shared generation lifecycle for delayed or asynchronous UI changes.
- New tab markup should use canonical `openclaw-*` classes; legacy `moltbot-*` aliases are generated centrally at runtime instead of being duplicated in each template.
- New host sidebar registration changes should stay in `web/openclaw_sidebar_registration.js` rather than duplicating ComfyUI frontend API detection inside the extension entrypoint.
- Host-sensitive behaviors should consume the shared host-surface helper rather than inferring desktop vs standalone frontend from ad-hoc globals.
- Graph/widget flows should preserve host-shaped promoted-widget source metadata and non-numeric node IDs, including Parameter Lab replay/apply paths.
- Parameter Lab flows should keep scalar/count/byte validation aligned with the backend policy and
  use exact request-ID queue receipts; they must not infer prompt ownership from a globally recent
  prompt when the host request boundary is unsupported or ambiguous.
- Output preview flows should consume the shared asset-ref normalizer rather than assembling `/view` URLs independently in each tab, treating non-image or HDR media as broken images, or silently widening runtime behavior to direct `/api/assets` fetches.
- Explorer/preflight consumers should treat inventory diagnostics as snapshot-first and surface `snapshot_ts`, `scan_state`, `stale`, and `last_error` instead of blocking the UI on full rescans.
- Explorer/preflight rendering should keep actionable missing-node/model failures separate from suppressed inactive-branch findings returned by the backend.

## Feature Gating (Capabilities)

- Backend exposes `GET /openclaw/capabilities` (legacy `/moltbot/capabilities` still works).
- Frontend fetches capabilities during setup and conditionally registers tabs:
  - `assist_planner` → Planner
  - `assist_refiner` → Refiner
  - `assist_streaming` → enable Planner/Refiner incremental live preview (fallback remains non-streaming)
  - `scheduler` → Variants (current gating)
  - `presets` → Library
  - `approvals` → Approvals

If capabilities are unavailable, the full tab set is registered to surface actionable errors (instead of “missing tabs”).
If `assist_streaming` is unavailable or the stream transport degrades, Planner/Refiner automatically fall back to the existing non-stream request path.

## Host-Surface Contract

- OpenClaw treats standalone `ComfyUI_frontend`, legacy fixed-bundle `desktop`, and current
  managed-install `comfy_desktop` as distinct frontend host surfaces.
- The sidebar stamps its resolved host surface and refreshed host-reference metadata at mount time so desktop bundle drift is explicit in diagnostics and regression tests.
- The standalone Remote Admin Console stamps the same host-surface metadata on its document root,
  including legacy Desktop `0.9.4`, fixed core `0.22.3`, embedded frontend `1.43.18`, and lagging
  parity relative to standalone frontend `1.52.1`. It also exposes current Comfy-Desktop
  `1.0.32-rc.1` with `installation_specific` hosted versions. Presence of
  `window.__comfyDesktop2` identifies that host generation only; it does not authorize privileged
  capability calls or inspect bridge members.
- Graph/widget compatibility code should route through shared host helpers to keep nested-subgraph and promoted-widget behavior aligned with current upstream host semantics, including preserving source metadata and string-shaped node IDs.

## Standalone Remote Admin Console

- Entry route: `GET /openclaw/admin` (legacy `GET /moltbot/admin` still works).
- HTML shell: `web/admin_console.html`
- Runtime app module: `web/admin_console_app.js`
- Runtime API module: `web/admin_console_api.js`
- Purpose: mobile-friendly standalone operations UI for non-sidebar workflows.
- Security model:
  - The page itself is a static shell and can render without authentication.
  - All write APIs still enforce backend admin policy (`X-OpenClaw-Admin-Token` and remote policy such as `OPENCLAW_ALLOW_REMOTE_ADMIN`).
- Runtime behaviors:
  - Dashboard summary + health/config snapshots
  - Jobs/Events polling + SSE stream connect/fallback
  - Delta-aware polling cursors for events and managed-task refresh loops
  - Approvals/Schedules/Triggers control actions
  - Config read/partial write and diagnostics access
  - Quick Actions (retry/refresh/drill) remain backend-authorized

## Remote Console Manual Checks

1. Open `http://<host>:<port>/openclaw/admin` from desktop and phone browsers.
2. Save an admin token via the console and verify protected actions succeed.
3. Clear token and verify write actions fail with explicit auth/policy errors.
4. Connect SSE, then trigger a run; verify event stream updates and fallback polling still works.
5. Confirm there is no blank/overflow breakage on narrow mobile widths.
6. If you are validating desktop parity, confirm the page root resolves the expected host-surface metadata instead of silently defaulting to standalone assumptions.

## Quick Manual Checks

1. Open ComfyUI and confirm OpenClaw appears in the sidebar.
2. Switch between all visible tabs multiple times (and reopen the sidebar if possible) and ensure panes do not go blank.
3. Confirm the sidebar host-surface metadata resolves correctly for the current environment instead of defaulting silently.
4. Planner: click **Plan Generation** with minimal input and confirm either live preview/stage updates appear (when streaming is supported) or a readable fallback result/error appears.
5. Refiner: click **Refine Prompts** (with or without image) and confirm either live preview/stage updates appear (when streaming is supported) or a readable fallback result/error appears.
6. Jobs: verify output previews still resolve for classic history refs, optional hash-backed refs when host metadata is present, and supported media-aware refs (`images`, `video`, `audio`, `3d`, bounded inline/file-backed `text`); allowlisted text files should show literal bounded content or a deterministic source-link fallback, HDR `.exr` / `.hdr` image refs should render as explicit source-preview fallback links, asset-service-only refs should stay explicit as a bounded fallback state, and repeated polls should not duplicate rows after reconnect/resume.
7. Parameter Lab: verify bounded scalar sweep/compare values queue with an exact request receipt,
   and verify unsupported structured values or unknown host queue-event shapes fail visibly without
   assigning another prompt's lifecycle.
8. Explorer: verify preflight inventory can show `refreshing` / `stale` / `error` state without freezing the tab while deep scan work continues, and verify inactive-branch suppressed findings render separately from actionable failures.
9. Library/Approvals: if backend endpoints are not enabled, confirm the UI shows a clear error state (no crashes).
10. If you simulate/fake a stream failure in dev tools, confirm Planner/Refiner retry through the classic non-stream path without duplicate submits or broken loading state.

## E2E (Playwright) Checks

- Run: `npm test`
- Tests live in: `tests/e2e/specs/`
- Harness: `tests/e2e/test-harness.html` (mocks ComfyUI core + basic OpenClaw API calls)
- Harness bootstrap now retries one transient `openclaw.js` module-fetch failure before surfacing a hard load error, so CI-only first-request flakiness does not get misreported as a permanent sidebar failure.
- Web helper/self-test harness: `web/tests/e2e-harness.html` (includes frontend helper and wrapper idempotence checks)
- Frontend unit contracts also freeze API exports/signatures, singleton identity, Settings DOM
  identities, owner direction, and stale-generation disposal across the decomposed modules.
- Desktop host parity lane: `tests/e2e/specs/desktop_host_parity.spec.js` verifies standalone vs desktop host evidence separately and covers both sidebar and Remote Admin host-sensitive behavior under the shared harness shims.
- When investigating suspected harness flakes locally, prefer `npm run test:stress -- <spec>` so the same shared bootstrap path is exercised repeatedly without changing the default `npm test` contract.
