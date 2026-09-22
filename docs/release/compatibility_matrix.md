# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
    "comfyui": "e638023d (v0.37.0-9-ge638023d / pyproject 0.37.0)",
    "comfyui_frontend": "1.55.11 (23559a8f86 / v1.55.11-104-g23559a8f86)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-host-qualification-20260922",
    "updated_at": "2026-09-22T17:44:47+08:00",
    "updated_by": "pinned-two-subject-host-qualification"
  },
  "evidence_states": {
    "real_host": {
      "evidence_id": "host-evidence-20260922-01",
      "run_id": "host-pair-20260922-01",
      "state": "validated"
    },
    "repository_validation": {
      "evidence_id": "repo-validation-20260922-host-refresh",
      "run_id": "windows-full-gate-20260922-host-refresh",
      "state": "validated"
    },
    "source_review": {
      "evidence_id": "source-review-20260922",
      "run_id": null,
      "state": "reviewed"
    }
  },
  "host_surfaces": {
    "comfy_desktop": {
      "anchor_key": "comfy_desktop",
      "core_version": null,
      "frontend_version": null,
      "generation": "managed_install",
      "hosted_version_mode": "installation_specific"
    },
    "desktop": {
      "anchor_key": "desktop",
      "core_version": "0.22.3",
      "frontend_version": "1.43.18",
      "generation": "legacy_fixed_bundle",
      "hosted_version_mode": "fixed"
    }
  },
  "last_validated_date": "2026-09-22",
  "matrix_version": "v0.3.0",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "reference_baselines": {
    "comfyui": {
      "bundled_frontend_version": "1.53.6",
      "project_version": "0.37.0",
      "source_describe": "v0.37.0-9-ge638023d",
      "source_head": "e638023d54497dbe0579565e5de4bb7076899592",
      "tag": "v0.37.0",
      "tag_commit": "73c9bad4d21e7addbe1d13bc92eee0f1431b017d"
    },
    "comfyui_frontend": {
      "package_version": "1.55.12",
      "release_tag": "v1.55.11",
      "release_tag_commit": "3a851363c48b233b3b576131dff6e1e92b0e1573",
      "release_version": "1.55.11",
      "source_describe": "v1.55.11-104-g23559a8f86",
      "source_head": "23559a8f86227837aef21d1da8b7aa8149f3b2cc"
    }
  },
  "schema_version": 3
}
```

This document tracks current reference anchors and separately records source, repository, and real-host evidence for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Reference Subject | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `e638023d` source-review anchor (`v0.37.0-9-ge638023d`; `pyproject.toml` version `0.37.0`) | Older tagged snapshots | Tag baseline `v0.37.0` is commit `73c9bad4`, 9 commits behind this source head. That tag pins bundled frontend `1.52.7`, while the supplied source head pins `1.53.6`; the smoke subject uses the source head. Neither source review nor tag identity is runtime proof. |
| **ComfyUI Frontend** | `1.55.11` release `v1.55.11` at commit `3a851363` (reproducible release subject) | Source-review head `23559a8f86` (`v1.55.11-104-g23559a8f86`, package `1.55.12`), 104 commits beyond the release tag | The release and later source head are distinct subjects. The release archive's downloaded bytes matched the pinned published size and SHA256 before host execution; the later source head has not run. Sidebar extension contract remains compatible; prefer the current sidebar store API with deprecated facade fallback. |
| **Legacy Desktop** | `0.9.4 (core 0.22.3 / frontend 1.43.18)` reference anchor | Legacy fixed bundle may lag standalone frontend | Preserve the recorded fixed-bundle contract for existing parity coverage |
| **Current Comfy-Desktop** | `1.0.32-rc.1` reference anchor (`85e28b7a`; `v1.0.32-rc.1-3-g85e28b7`) | Hosted component versions vary by installation | Treat the managed-install generation separately; do not infer fixed core/frontend versions from the application release |
| **Python** | 3.13 | 3.10-3.12 compatibility targets; 3.14 best effort | Current executed baseline is the local Windows Full Gate on Python 3.13. 3.10-3.12 are exercised by the scheduled exact-version matrix, and 3.10 also by the routine per-push backend job, so they are compatibility targets by support commitment rather than for want of testing. Only the matrix emits the dated, commit-bound artifact the 14-day currency rule reads, so its artifacts remain the sole promotion evidence; a passing push corroborates but does not promote. 3.10 requires reassessment on 2026-10-31; below 3.10 is unsupported |
| **Torch** | 2.1.2+ | 1.13+ | CUDA 11.8/12.1 verified |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: current sidebar integration contract remains compatible with the standalone frontend reference anchor, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Promoted-widget evidence scope**: historical real-host runs exercised an ordinary widget with constructed source fields. The refreshed paired run exercised a host-created subgraph binding, an OpenClaw Parameter Lab edit, projected value readback and serialized inner prompt value on both pinned frontend subjects. The raw inner widget seed need not change when the promoted host value owns execution.
- **Evidence states**: the metadata block records source review, repository validation, and real-host validation independently. The refreshed source identities have been reviewed; repository validation passed the local Windows Full Gate on 2026-09-22. The paired real-host campaign on the pinned core reports `validated` for bundled frontend `1.53.6` and standalone release `1.55.11`, with a campaign run identifier and separate subject receipts. This does not validate the later frontend source head or either Desktop surface.
- **Real-host lane execution status**: the earlier manual and workflow runs exercised historical core/frontend subjects. The refreshed paired campaign used core `e638023d`, the existing bundled package `1.53.6`, and a SHA256-checked standalone release archive for `1.55.11`. Both subjects passed the ten-case real-host browser spec on a loopback host, with zero skipped cases; the two host processes were stopped afterward. The historical results remain separate evidence.
- **Legacy Desktop host surface**: Desktop `0.9.4` embeds frontend `1.43.18`, which lags the standalone frontend `1.55.11` release reference. Validate this fixed bundle against its own anchor.
- **Current Comfy-Desktop host surface**: application `1.0.32-rc.1` is a managed-install generation. Its hosted ComfyUI and frontend versions are `installation_specific`; the application anchor must not be cross-wired into fixed hosted-version claims.

## Residual Host-Contract Decisions

- **SaveImage output refs**: OpenClaw consumes runtime `/history` output refs and does not infer graph-rewrite behavior from output-node socket shape. `SaveImage` output sockets are allowed to exist without changing the normalized output-ref contract.
- **3D output refs**: `Load3DAdvanced` and related 3D preview refs remain media-aware output refs. When one saved file appears in both `3d` and legacy `result` for the same node, Python history parsing and browser output normalization emit one canonical ref; distinct files, host directory types, and nodes remain separate. File-like refs and optional hash-backed 3D refs stay on the bounded `/view` preview contract; clients without a 3D renderer should show an explicit fallback/link surface.
- **HDR image output refs**: `.exr` and `.hdr` image refs stay on the bounded `/view` source-preview contract but render as explicit fallback/link surfaces unless a client implements a safe HDR-specific viewer.
- **File-backed text output refs**: allowlisted text files under the host `files` output key normalize to text refs on the existing `/view` route. Job Monitor uses same-origin, redirect-free, strict MIME/UTF-8 streaming with fixed 5-second, 64-KiB transfer, and 4,096-character display limits; failures remain source-link fallbacks and content is never interpreted as HTML or Markdown.
- **Promoted widget source scope and structured widgets**: OpenClaw graph helpers preserve host-shaped promoted-widget source metadata and keep non-numeric node IDs stable. Backend preflight remains a conservative model-key whitelist; structured `COLORS` / `BOUNDING_BOXES` inputs and frontend source metadata are not treated as model references, and OpenClaw does not claim full host frontend active-scope parity without a richer graph-instance contract.
- **Asset dimensions and grouped assets**: typed width/height metadata and grouped multi-download behavior are host-frontend display/download concerns. They do not change OpenClaw fetch routing, and asset-service-only identifiers remain explicit `asset_api_required` states rather than implicit `/api/assets` fetches.
- **Asset loader paths and model tags**: current host asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, advertised by `/features.supports_model_type_tags`. OpenClaw does not upload through or directly consume `/api/assets`, so these schema facts do not change the existing `/history` + `/view` contract.
- **Sidebar registration**: prefer the current `sidebarTab.registerSidebarTab` host API and retain the deprecated `extensionManager.registerSidebarTab` fallback for older or desktop-embedded frontend hosts.
- **Node runtime policy**: the standalone ComfyUI frontend development workspace currently declares `node >=26.8.2 <27`, but OpenClaw keeps its package engine at `>=18.0.0` because this custom-node package runs its own Playwright/Vitest harness and does not build the host frontend workspace. OpenClaw acceptance remains governed by `tests/TEST_SOP.md` and `tests/E2E_TESTING_SOP.md`, which require Node.js 18+ and CI-parity validation on the project test harness.

## Operating Systems

| OS | Status | CI Validation | Notes |
| :--- | :--- | :--- | :--- |
| **Windows 10/11** | ✅ Supported | Manual | Primary dev environment |
| **Linux (Ubuntu 22.04)** | ✅ Supported | Automated | CI environment |
| **macOS (Apple Silicon)** | ⚠️ Best Effort | None | Should work, not guaranteed |
| **WSL2** | ✅ Supported | None | Treated as Linux |

## Browser Support

| Browser | Minimum Version | Notes |
| :--- | :--- | :--- |
| **Chrome / Edge** | Latest - 2 | Primary target |
| **Firefox** | Latest - 2 | |
| **Safari** | Latest - 2 | |

## Hardware Recommendations

- **VRAM**: Minimum 8GB (for SDXL), 16GB recommended (for Flux).
- **RAM**: Minimum 16GB.
- **Disk**: SSD recommended for fast model loading.
