# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
    "comfyui": "31dfbd4c (v0.34.0-46-g31dfbd4c / pyproject 0.34.0)",
    "comfyui_frontend": "1.54.3 (9ff3fd7f0e / v1.54.3-21-g9ff3fd7f0e)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-refresh-20260906",
    "updated_at": "2026-09-06T14:10:00+08:00",
    "updated_by": "host-compatibility-baseline-refresh"
  },
  "evidence_states": {
    "real_host": {
      "evidence_id": null,
      "run_id": null,
      "state": "pending"
    },
    "repository_validation": {
      "evidence_id": "repo-validation-20260906",
      "run_id": "windows-full-gate-20260906",
      "state": "validated"
    },
    "source_review": {
      "evidence_id": "source-review-20260905",
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
  "last_validated_date": "2026-09-06",
  "matrix_version": "v0.3.0",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "reference_baselines": {
    "comfyui": {
      "bundled_frontend_version": "1.51.9",
      "project_version": "0.34.0",
      "source_describe": "v0.34.0-46-g31dfbd4c",
      "source_head": "31dfbd4ca0cb36ab6a573fc13daec8cc3a2e1e98",
      "tag": "v0.34.0",
      "tag_commit": "12d5279438bfefc058a269eae805ceab6047777f"
    },
    "comfyui_frontend": {
      "package_version": "1.54.3",
      "release_tag": "v1.54.3",
      "release_tag_commit": "b2f5587509d744d7779accce193db53f36a91d4a",
      "release_version": "1.54.3",
      "source_describe": "v1.54.3-21-g9ff3fd7f0e",
      "source_head": "9ff3fd7f0e36b810a621288ceaf6e74e3846bedd"
    }
  },
  "schema_version": 3
}
```

This document tracks the current reference anchors and validated environments for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Validated Range | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `31dfbd4c` source-review anchor (`v0.34.0-46-g31dfbd4c`; `pyproject.toml` version `0.34.0`) | Older tagged snapshots | Tag baseline `v0.34.0` is commit `12d52794`, 46 commits behind this source head. The core manifest pins bundled frontend package `1.51.9`, which is separate from the independently reviewed standalone frontend reference |
| **ComfyUI Frontend** | `1.54.3` release `v1.54.3` at commit `b2f55875` (reproducible) | Source-review head `9ff3fd7f0e` (`v1.54.3-21-g9ff3fd7f0e`), 21 commits beyond the release tag | The reproducible release and the later source head are distinct subjects. A release-version lane reproduces `1.54.3`; it does not execute the source head. Sidebar extension contract remains compatible; prefer the current sidebar store API with deprecated facade fallback |
| **Legacy Desktop** | `0.9.4 (core 0.22.3 / frontend 1.43.18)` reference anchor | Legacy fixed bundle may lag standalone frontend | Preserve the recorded fixed-bundle contract for existing parity coverage |
| **Current Comfy-Desktop** | `1.0.32-rc.1` reference anchor (`85e28b7a`; `v1.0.32-rc.1-3-g85e28b7`) | Hosted component versions vary by installation | Treat the managed-install generation separately; do not infer fixed core/frontend versions from the application release |
| **Python** | 3.13 | 3.10-3.12 compatibility targets; 3.14 best effort | Current executed baseline is the local Windows Full Gate on Python 3.13; scheduled/manual exact-version artifacts are required before promoting other targets; 3.10 requires reassessment on 2026-10-31; below 3.10 is unsupported |
| **Torch** | 2.1.2+ | 1.13+ | CUDA 11.8/12.1 verified |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: current sidebar integration contract remains compatible with the standalone frontend reference anchor, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Evidence states**: the metadata block records source review, repository validation, and real-host validation independently. Source review of a checkout is not runtime proof, and repository validation is the local Windows Full Gate result. Real-host validation stays `pending` until a lane run carrying a run identifier succeeds against the pinned anchor; a run executed by hand does not promote the state on its own, and no field may present the later frontend source head as an executed release.
- **Real-host lane execution status**: the pinned lane has been executed against a running ComfyUI `0.34.0` host and reported every check passing for both pinned frontend subjects, bundled `1.51.9` and standalone release `1.54.3`, each confirmed from the frontend's own reported version. That host was matched on release version rather than on the exact source review commit, and the run produced no run identifier, so `evidence_states.real_host` remains `pending` by design. The lane is demonstrably able to produce this evidence; recording it is a separate, authorized step.
- **Legacy Desktop host surface**: Desktop `0.9.4` embeds frontend `1.43.18`, which lags the standalone frontend `1.54.3` reference. Validate this fixed bundle against its own anchor.
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
