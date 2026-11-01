# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
    "comfyui": "7c4390a6 (v0.43.0-21-g7c4390a6f / pyproject 0.43.0)",
    "comfyui_frontend": "1.60.0 (aa4e48c08d / v1.60.0-209-gaa4e48c08d)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-source-review-20261101",
    "updated_at": "2026-11-01T00:00:00+08:00",
    "updated_by": "pinned-subject-source-review"
  },
  "evidence_states": {
    "real_host": {
      "evidence_id": null,
      "run_id": null,
      "state": "pending"
    },
    "repository_validation": {
      "evidence_id": null,
      "run_id": null,
      "state": "pending"
    },
    "source_review": {
      "evidence_id": "source-review-20261101",
      "run_id": null,
      "state": "reviewed"
    }
  },
  "historical_evidence": [
    {
      "evidence_states": {
        "real_host": {
          "evidence_id": "host-evidence-20261013-01",
          "run_id": "host-pair-20261013-01",
          "state": "validated"
        },
        "repository_validation": {
          "evidence_id": "repo-validation-20261013-host-requalification",
          "run_id": "windows-full-gate-20261013-host-requalification",
          "state": "validated"
        },
        "source_review": {
          "evidence_id": "source-review-20261013",
          "run_id": null,
          "state": "reviewed"
        }
      },
      "last_validated_date": "2026-10-13",
      "reference_baselines": {
        "comfyui": {
          "bundled_frontend_version": "1.55.14",
          "project_version": "0.39.0",
          "source_describe": "v0.39.0-49-g8010b48d2",
          "source_head": "8010b48d2c1be986613f8e8c761527d205bd756f",
          "tag": "v0.39.0",
          "tag_commit": "b0b743566f65daafc423b4fea8a2fbda94b3384a"
        },
        "comfyui_frontend": {
          "package_version": "1.57.0",
          "release_tag": "v1.57.0",
          "release_tag_commit": "cdc18a92d227a965810b08928963124959951908",
          "release_version": "1.57.0",
          "source_describe": "v1.57.0-309-g0e1a2fbbe4",
          "source_head": "0e1a2fbbe4d5ed1eeefb70a3fd6f0d805331d3e8"
        }
      }
    }
  ],
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
  "last_validated_date": null,
  "matrix_version": "v0.3.1",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "reference_baselines": {
    "comfyui": {
      "bundled_frontend_version": "1.57.6",
      "project_version": "0.43.0",
      "source_describe": "v0.43.0-21-g7c4390a6f",
      "source_head": "7c4390a6f70cd8eb41014d6af52969397a752fc4",
      "tag": "v0.43.0",
      "tag_commit": "426f5eea52a7ece1dd599fff4cb6cd1f352ddd24"
    },
    "comfyui_frontend": {
      "package_version": "1.60.0",
      "release_tag": "v1.60.0",
      "release_tag_commit": "5cb2899fc4bc5034513fbafae72b39265c4a213e",
      "release_version": "1.60.0",
      "source_describe": "v1.60.0-209-gaa4e48c08d",
      "source_head": "aa4e48c08d364dcd36071131455a7aec40b852ac"
    }
  },
  "schema_version": 3
}
```

This document tracks current reference anchors and separately records source, repository, and real-host evidence for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Reference Subject | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `7c4390a6` source-review anchor (`v0.43.0-21-g7c4390a6f`; project `0.43.0`) | Older tagged snapshots | Tag `v0.43.0` is `426f5eea`, 21 commits behind source, and pins frontend `1.57.4`; source pins `1.57.6`. Both new host subjects remain pending. |
| **ComfyUI Frontend** | Release `1.60.0`, tag `v1.60.0` at `5cb2899f` | Source `aa4e48c08d` (`v1.60.0-209-gaa4e48c08d`), package `1.60.0` | Release and source are distinct. Publisher-reported `dist.zip` size/SHA256 are pinned; downloaded bytes and served files must be verified before live claims. Source-only queue UI refresh, status retries and extension-loader changes are not release behavior. |
| **Legacy Desktop** | `0.9.4 (core 0.22.3 / frontend 1.43.18)` reference anchor | Legacy fixed bundle may lag standalone frontend | Preserve the recorded fixed-bundle contract for existing parity coverage |
| **Current Comfy-Desktop** | `1.0.32-rc.1` reference anchor (`85e28b7a`; `v1.0.32-rc.1-3-g85e28b7`) | Hosted component versions vary by installation | Treat the managed-install generation separately; do not infer fixed core/frontend versions from the application release |
| **Python** | 3.13 | 3.10-3.12 compatibility targets; 3.14 best effort | Current executed baseline is the local Windows Full Gate on Python 3.13. 3.10-3.12 are exercised by the scheduled exact-version matrix, and 3.10 also by the routine per-push backend job, so they are compatibility targets by support commitment rather than for want of testing. Only the matrix emits the dated, commit-bound artifact the 14-day currency rule reads, so its artifacts remain the sole promotion evidence; a passing push corroborates but does not promote. 3.10 requires reassessment on 2026-10-31; below 3.10 is unsupported |
| **Torch** | 2.1.2+ | 1.13+ | CUDA 11.8/12.1 verified |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: current sidebar integration contract remains compatible with the standalone frontend reference anchor, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Promoted-widget evidence scope**: early real-host runs exercised an ordinary widget with constructed source fields. The paired runs on the previous core `0.37.0` subjects and on the refreshed core `0.39.0` subjects exercised a host-created subgraph binding, an OpenClaw Parameter Lab edit, projected value readback and serialized inner prompt value on both frontend subjects of each pair. The raw inner widget seed need not change when the promoted host value owns execution.
- **Evidence states**: new source subjects were reviewed on 2026-11-01. Repository validation and real-host qualification are pending with no current run identifiers or validation date. Source review and target-authored simulations do not establish live compatibility.
- **Historical real-host evidence**: the 2026-10-13 campaign `host-pair-20261013-01` / `host-evidence-20261013-01` validated core `0.39.0` at `8010b48d`, bundled frontend `1.55.14` and standalone release `1.57.0`. Both passed eleven browser cases, including sidebar splitter width survival, on loopback with the bounded custom-node set. Its repository gate and date are scoped to those old subjects in `historical_evidence`; they do not qualify the new pins. The earlier core `0.37.0` pair remains historical too. Neither Desktop nor the later frontend source head has live qualification.
- **Legacy Desktop host surface**: Desktop `0.9.4` embeds frontend `1.43.18`, which lags the standalone frontend `1.60.0` release reference. Validate this fixed bundle against its own anchor.
- **Current Comfy-Desktop host surface**: application `1.0.32-rc.1` is a managed-install generation. Its hosted ComfyUI and frontend versions are `installation_specific`; the application anchor must not be cross-wired into fixed hosted-version claims.

## Residual Host-Contract Decisions

- **Additive preview metadata**: `preview_id` does not alter filename-backed `/view` refs. The host strips `asset_preview` before client/history/cache output; OpenClaw does not fetch that URL or introduce a `previews` directory type.
- **SaveImage output refs**: OpenClaw consumes runtime `/history` output refs and does not infer graph-rewrite behavior from output-node socket shape. `SaveImage` output sockets are allowed to exist without changing the normalized output-ref contract.
- **3D output refs**: 3D refs remain media-aware output refs. Current Load3D-family and Save3D nodes report only the standard `3d` item, with `camera_info` and `model_3d_info` alongside it; Load3D-family refs use the `temp` directory. The Preview3D node still reports the legacy `result` item. Older hosts can report one saved file in both `3d` and legacy `result` for the same node; Python history parsing and browser output normalization emit one canonical ref for it, while distinct files, host directory types, and nodes remain separate. Optional per-file `metadata` (kind, dimensions, duration) does not change the normalized ref. File-like refs and optional hash-backed 3D refs stay on the bounded `/view` preview contract; clients without a 3D renderer should show an explicit fallback/link surface.
- **HDR image output refs**: `.exr` and `.hdr` image refs stay on the bounded `/view` source-preview contract but render as explicit fallback/link surfaces unless a client implements a safe HDR-specific viewer.
- **File-backed text output refs**: allowlisted text files under the host `files` output key normalize to text refs on the existing `/view` route. Job Monitor uses same-origin, redirect-free, strict MIME/UTF-8 streaming with fixed 5-second, 64-KiB transfer, and 4,096-character display limits; failures remain source-link fallbacks and content is never interpreted as HTML or Markdown.
- **Promoted widget source scope and structured widgets**: OpenClaw graph helpers preserve host-shaped promoted-widget source metadata and keep non-numeric node IDs stable. Backend preflight remains a conservative model-key whitelist; structured `COLORS` / `BOUNDING_BOXES` inputs and frontend source metadata are not treated as model references, and OpenClaw does not claim full host frontend active-scope parity without a richer graph-instance contract.
- **Asset dimensions and grouped assets**: typed width/height metadata and grouped multi-download behavior are host-frontend display/download concerns. They do not change OpenClaw fetch routing, and asset-service-only identifiers remain explicit `asset_api_required` states rather than implicit `/api/assets` fetches.
- **Asset loader paths and model tags**: current host asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, advertised by `/features.supports_model_type_tags`. OpenClaw does not upload through or directly consume `/api/assets`, so these schema facts do not change the existing `/history` + `/view` contract.
- **Sidebar registration**: prefer the current `sidebarTab.registerSidebarTab` host API and retain the deprecated `extensionManager.registerSidebarTab` fallback for older or desktop-embedded frontend hosts.
- **Node runtime policy**: the locked OpenClaw Vite/Vitest harness requires `^20.19.0 || ^22.12.0 || >=24.0.0`. A dependency-free shared guard reads package.json and checks preflight, Windows/Linux full scripts, pre-push and direct npm test entries before dependency/bootstrap/browser work. Select a stable Node 24 runtime when remediation is needed. The reviewed host frontend development workspace declares `node >=26.8.2 <27`, a separate contract; OpenClaw does not build the host frontend workspace. CI uses the compatible Node 20 line. See `tests/TEST_SOP.md` and `tests/E2E_TESTING_SOP.md` for the project procedure.

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
