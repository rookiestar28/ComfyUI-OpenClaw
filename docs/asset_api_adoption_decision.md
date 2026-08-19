# ComfyUI Asset API Adoption Decision (2026-04-16)

## 2026-08-19 reference anchor update

- Current reference anchor is ComfyUI `3aba3dae` (`v0.33.0-27-g3aba3dae`, pyproject `0.33.0`).
- SaveImage output sockets, 3D preview refs, typed asset dimensions, grouped asset downloads, and optional `hash` / `asset_hash` aliases do not change the no-go decision.
- ComfyUI asset hashing is host-side opt-in through `--enable-asset-hashing`, so normal filename-backed output refs must not require hash metadata.
- Current host asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, and `/features.supports_model_type_tags` advertises that contract. OpenClaw does not upload through or directly consume `/api/assets`, so these facts do not change the no-go decision.
- Current host asset responses derive `preview_url` from resolved file paths and support bounded tag filters; OpenClaw treats any incoming `preview_url` as untrusted metadata and continues to construct same-origin `/view` refs from validated fields.
- Frontend cloud distributions may enrich persisted outputs through `/api/jobs/{job_id}/assets`, but that route is optional/cloud-only and is not an OpenClaw runtime dependency.
- OpenClaw continues to use `/history` + `/view`; asset-service-only refs stay explicit `asset_api_required` states.

## 2026-06-12 reconfirmation

- Current output parsing is media-aware for ComfyUI result groups `images`, `video`, `audio`, `3d`, and bounded `text`.
- File-like media refs still use `/view` when they provide `filename`, or optional hash-backed preview metadata when the host provides it.
- HDR `.exr` / `.hdr` image refs stay on the `/view` source-preview contract but render as explicit fallback links because OpenClaw does not embed the host HDR viewer.
- Text output previews are bounded and rendered as text, not HTML.
- Asset-service-only identifiers remain explicit fallback states and still do not trigger automatic direct `/api/assets` fetches.

## 2026-05-31 reconfirmation

- Current host reference evidence shows upstream asset responses may expose optional `hash` alongside `asset_hash`.
- OpenClaw accepts `hash` as an alias for hash-backed previews when present, but still resolves those refs through `/view?filename=blake3:...`.
- This does not change the no-go decision for automatic direct `/api/assets` runtime fetches.

## Scope

- Goal: decide whether OpenClaw should adopt upstream `/api/assets` semantics as a normal runtime dependency beyond the bounded `/view` interoperability layer.

## Current baseline

- Current history/output-facing interop already accepts:
  - classic ComfyUI output refs (`filename`, `subfolder`, `type`)
  - optional asset-hash-backed refs that still resolve through `/view?filename=blake3:...` when host metadata is present
  - media-aware output groups (`images`, `video`, `audio`, `3d`, and bounded `text`)
  - HDR `.exr` / `.hdr` image refs as explicit `/view` source-preview fallback links, not normal thumbnails
- Current ComfyUI `3aba3dae` / `v0.33.0-27-g3aba3dae` / pyproject `0.33.0` reference facts:
  - `/api/assets*` routes exist, but operational use is feature-gated behind `--enable-assets`
  - content hashing is opt-in through `--enable-asset-hashing`, so normal filename-backed refs may omit `asset_hash` / `hash`
  - `/features` exposes the `assets` capability flag so hosts can report whether the asset system is enabled
  - frontend preview still resolves `blake3:...` asset hashes through `/view`, so hash-backed outputs do not require a direct `/api/assets` fetch
  - asset responses may expose optional `hash` alongside `asset_hash`; OpenClaw treats both as hash-backed preview aliases when present
  - asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, advertised by `/features.supports_model_type_tags`
- Current operator/runtime surfaces in scope:
  - sidebar `Jobs`
  - callback delivery payloads
  - history/result consumption paths derived from `services.comfyui_history`
- Current non-goal:
  - no gallery/explorer/runtime flow currently requires direct `/api/assets` fetches to stay functional.

## Decision

- **No-go for first-class `/api/assets` runtime adoption in phase 2.**
- OpenClaw keeps `/history` + `/view` as the supported runtime contract for normal output handling.
- Asset-api-only identifiers are treated as explicit unsupported contracts rather than implicit fetch targets.

## Rationale

1. Current OpenClaw output surfaces still succeed on the existing bounded `/view` contract, including optional asset-hash-backed refs when metadata exists.
2. Adding `/api/assets` as a normal dependency would widen runtime coupling to upstream host behavior without a demonstrated operator need in current features.
3. A silent fallback from `asset id only` to `/api/assets` would weaken boundary clarity and make host drift harder to reason about.

## Approved phase-2 seam

- Preserve current supported refs exactly:
  - classic refs -> `/view?filename=...&type=...`
  - optional asset-hash-backed refs -> `/view?filename=blake3:...` when metadata exists
  - file-like media refs -> `/view` fallback/link surfaces when preview metadata is present
  - HDR `.exr` / `.hdr` image refs -> explicit source-preview fallback links
  - bounded text refs -> escaped text surfaces, not HTML
- For refs that expose only asset-service identifiers and are not representable through `/view`:
  - keep them in normalized output payloads
  - mark them as `asset_api_required`
  - do not auto-fetch `/api/assets`
  - surface a bounded operator-facing message where relevant

## Re-open triggers

Revisit this decision only if one of the following becomes true:

1. A current operator-facing surface cannot complete its supported workflow without direct `/api/assets` semantics.
2. Upstream ComfyUI stops providing `/view`-compatible output metadata for supported runtime flows.
3. OpenClaw intentionally adds a new asset-management feature whose documented contract depends on asset-service metadata beyond hash-backed preview resolution.
