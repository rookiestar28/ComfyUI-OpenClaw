# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
    "comfyui": "3aba3dae (v0.33.0-27-g3aba3dae / pyproject 0.33.0)",
    "comfyui_frontend": "1.52.1 (569e65b30f / v1.52.1-3-g569e65b30f)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-refresh-20260819",
    "updated_at": "2026-08-19T15:50:00+08:00",
    "updated_by": "host-reference-alignment"
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
  "last_validated_date": "2026-08-19",
  "matrix_version": "v0.2.9",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "schema_version": 2
}
```

This document tracks the current reference anchors and validated environments for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Validated Range | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `3aba3dae` reference anchor (`v0.33.0-27-g3aba3dae`; `pyproject.toml` version `0.33.0`) | Older tagged snapshots | Core manifest pins bundled frontend package `1.49.6`; this is separate from the independently reviewed standalone frontend reference |
| **ComfyUI Frontend** | `1.52.1` reference anchor (`569e65b30f`; `v1.52.1-3-g569e65b30f`) | Minor drift around the anchor | Sidebar extension contract remains compatible; prefer the current sidebar store API with deprecated facade fallback |
| **Legacy Desktop** | `0.9.4 (core 0.22.3 / frontend 1.43.18)` reference anchor | Legacy fixed bundle may lag standalone frontend | Preserve the recorded fixed-bundle contract for existing parity coverage |
| **Current Comfy-Desktop** | `1.0.32-rc.1` reference anchor (`85e28b7a`; `v1.0.32-rc.1-3-g85e28b7`) | Hosted component versions vary by installation | Treat the managed-install generation separately; do not infer fixed core/frontend versions from the application release |
| **Python** | 3.10, 3.11, 3.12, 3.13 | 3.14 | Below 3.10 is unsupported by package policy; 3.14 remains best effort and unvalidated |
| **Torch** | 2.1.2+ | 1.13+ | CUDA 11.8/12.1 verified |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: current sidebar integration contract remains compatible with the standalone frontend reference anchor, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Legacy Desktop host surface**: Desktop `0.9.4` embeds frontend `1.43.18`, which lags the standalone frontend `1.52.1` reference. Validate this fixed bundle against its own anchor.
- **Current Comfy-Desktop host surface**: application `1.0.32-rc.1` is a managed-install generation. Its hosted ComfyUI and frontend versions are `installation_specific`; the application anchor must not be cross-wired into fixed hosted-version claims.

## Residual Host-Contract Decisions

- **SaveImage output refs**: OpenClaw consumes runtime `/history` output refs and does not infer graph-rewrite behavior from output-node socket shape. `SaveImage` output sockets are allowed to exist without changing the normalized output-ref contract.
- **3D output refs**: `Load3DAdvanced` and related 3D preview refs remain media-aware output refs. File-like refs and optional hash-backed 3D refs stay on the bounded `/view` preview contract; clients without a 3D renderer should show an explicit fallback/link surface.
- **HDR image output refs**: `.exr` and `.hdr` image refs stay on the bounded `/view` source-preview contract but render as explicit fallback/link surfaces unless a client implements a safe HDR-specific viewer.
- **File-backed text output refs**: allowlisted text files under the host `files` output key normalize to text refs on the existing `/view` route. Job Monitor uses same-origin, redirect-free, strict MIME/UTF-8 streaming with fixed 5-second, 64-KiB transfer, and 4,096-character display limits; failures remain source-link fallbacks and content is never interpreted as HTML or Markdown.
- **Promoted widget source scope and structured widgets**: OpenClaw graph helpers preserve host-shaped promoted-widget source metadata and keep non-numeric node IDs stable. Backend preflight remains a conservative model-key whitelist; structured `COLORS` / `BOUNDING_BOXES` inputs and frontend source metadata are not treated as model references, and OpenClaw does not claim full host frontend active-scope parity without a richer graph-instance contract.
- **Asset dimensions and grouped assets**: typed width/height metadata and grouped multi-download behavior are host-frontend display/download concerns. They do not change OpenClaw fetch routing, and asset-service-only identifiers remain explicit `asset_api_required` states rather than implicit `/api/assets` fetches.
- **Asset loader paths and model tags**: current host asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, advertised by `/features.supports_model_type_tags`. OpenClaw does not upload through or directly consume `/api/assets`, so these schema facts do not change the existing `/history` + `/view` contract.
- **Sidebar registration**: prefer the current `sidebarTab.registerSidebarTab` host API and retain the deprecated `extensionManager.registerSidebarTab` fallback for older or desktop-embedded frontend hosts.
- **Node runtime policy**: the standalone ComfyUI frontend development workspace currently declares `node >=25 <26` and `pnpm >=11.3`, but OpenClaw keeps its package engine at `>=18.0.0` because this custom-node package runs its own Playwright/Vitest harness and does not build the host frontend workspace. OpenClaw acceptance remains governed by `tests/TEST_SOP.md` and `tests/E2E_TESTING_SOP.md`, which require Node.js 18+ and CI-parity validation on the project test harness.

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
