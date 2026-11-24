# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfy_desktop": "1.0.32-rc.1 (85e28b7a / v1.0.32-rc.1-3-g85e28b7)",
    "comfyui": "da8bd08e (v0.46.0-46-gda8bd08e4 / pyproject 0.46.0)",
    "comfyui_frontend": "1.61.8 (388973acb5 / v1.61.8)",
    "desktop": "0.9.4 (core 0.22.3 / frontend 1.43.18)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-source-review-20261124",
    "updated_at": "2026-11-24T19:39:51+08:00",
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
      "evidence_id": "source-review-20261124",
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
    },
    {
      "evidence_states": {
        "real_host": {
          "evidence_id": "host-evidence-20261101-isolated-01",
          "run_id": "host-pair-20261101-isolated-01",
          "state": "validated"
        },
        "repository_validation": {
          "evidence_id": "repo-validation-20261101-host-alignment",
          "run_id": "windows-full-gate-20261101-host-alignment",
          "state": "validated"
        },
        "source_review": {
          "evidence_id": "source-review-20261101",
          "run_id": null,
          "state": "reviewed"
        }
      },
      "last_validated_date": "2026-11-01",
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
  "matrix_version": "v0.3.2",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "reference_baselines": {
    "comfyui": {
      "bundled_frontend_version": "1.59.11",
      "project_version": "0.46.0",
      "source_describe": "v0.46.0-46-gda8bd08e4",
      "source_head": "da8bd08e48deee74d9f6c3bc00aed365b00ec209",
      "tag": "v0.46.0",
      "tag_commit": "a277b5bf213e8a44fa34817b7770b54f86ef4cea"
    },
    "comfyui_frontend": {
      "package_version": "1.61.8",
      "release_tag": "v1.61.8",
      "release_tag_commit": "388973acb56af768df63749b90627ac767c31be0",
      "release_version": "1.61.8",
      "source_describe": "v1.61.8",
      "source_head": "388973acb56af768df63749b90627ac767c31be0"
    }
  },
  "schema_version": 3
}
```

This document tracks current reference anchors and separately records source, repository, and real-host evidence for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Reference Subject | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `da8bd08e` source-review anchor (`v0.46.0-46-gda8bd08e4`; project `0.46.0`) | Older tagged snapshots | Tag `v0.46.0` is `a277b5bf`, 46 commits behind source, and pins frontend `1.59.6`; source pins `1.59.11`. The tags `v0.46.1`, `v0.46.2` and `v0.46.3` sit on a release branch that is not an ancestor of the source and also pin `1.59.6`. No subject of this row has live host qualification yet; the tag, the three patch tags and bundle `1.59.6` were not executed. |
| **ComfyUI Frontend** | Release `1.61.8`, tag `v1.61.8` at `388973ac` | Package `1.61.8`; the reviewed source head `388973acb5` is the tagged release itself | Source and release are one commit, so no source-only behavior is claimed. Publisher-reported `dist.zip` size/SHA256 are pinned and were not downloaded here; downloaded bytes and served files must be verified before live claims. The bundled frontend `1.59.11` comes from the frontend `core/1.59` line and does not carry the per-workflow graph changes of the `1.61.x` release, so the two subjects do not share one graph model. Patch `1.59.14` of that line was published after the core source was reviewed; it was not executed and does not replace the bundle the core pins. |
| **Legacy Desktop** | `0.9.4 (core 0.22.3 / frontend 1.43.18)` reference anchor | Legacy fixed bundle may lag standalone frontend | Preserve the recorded fixed-bundle contract for existing parity coverage |
| **Current Comfy-Desktop** | `1.0.32-rc.1` reference anchor (`85e28b7a`; `v1.0.32-rc.1-3-g85e28b7`) | Hosted component versions vary by installation | Treat the managed-install generation separately; do not infer fixed core/frontend versions from the application release |
| **Python** | 3.13 | 3.10-3.12 compatibility targets; 3.14 best effort | Current executed baseline is the local Windows Full Gate on Python 3.13. 3.10-3.12 are exercised by the scheduled exact-version matrix, and 3.10 also by the routine per-push backend job, so they are compatibility targets by support commitment rather than for want of testing. Only the matrix emits the dated, commit-bound artifact the 14-day currency rule reads, so its artifacts remain the sole promotion evidence; a passing push corroborates but does not promote. 3.10 requires reassessment on 2026-10-31; below 3.10 is unsupported |
| **Torch** | `2.10.0+cpu` in the last isolated host campaign (2026-11-01, historical) | Other CPU/CUDA combinations | Both frontend subjects of that campaign were exercised without a GPU; it makes no CUDA compatibility claim, and the refreshed subjects have no campaign yet. |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: the sidebar integration contract is qualified live only for the historical subjects below, and the refreshed standalone anchor is pending live qualification, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Promoted-widget evidence scope**: early real-host runs exercised an ordinary widget with constructed source fields. The historical core `0.37.0` and `0.39.0` pairs, and the core `0.43.0` pair of 2026-11-01, exercised a host-created subgraph binding, an OpenClaw Parameter Lab edit, projected value readback and serialized inner prompt value on both frontend subjects. The `0.43.0` pair additionally verified colon-containing IDs, shared subgraph instances, target-only edits/replay and collision rejection without writes. None of this has been exercised against the refreshed subjects. The raw inner widget seed need not change when the promoted host value owns execution.
- **Evidence states**: the new source subjects were reviewed on 2026-11-24, and source review is the only evidence recorded for them. The Windows repository gate and the paired isolated real-host campaign are pending, and `last_validated_date` stays null until a campaign is recorded. Source review and target-authored simulations do not establish live compatibility.
- **Historical real-host evidence**: the 2026-11-01 campaign `host-pair-20261101-isolated-01` / `host-evidence-20261101-isolated-01` validated core `0.43.0` at `7c4390a6`, bundled frontend `1.57.6` and standalone release `1.60.0`. It passed all fifteen browser cases on each frontend subject, with no skips or retries; the served index and entry module matched verified artifact files. The isolated host used Python `3.11.2` and CPU-only PyTorch `2.10.0+cpu`, which does not replace the exact-version backend matrix, and no GPU or provider jobs were run. Its repository gate and date are scoped to those subjects in `historical_evidence`. The 2026-10-13 campaign `host-pair-20261013-01` / `host-evidence-20261013-01` validated core `0.39.0` at `8010b48d`, bundled frontend `1.55.14` and standalone release `1.57.0`. Both passed eleven browser cases, including sidebar splitter width survival, on loopback with the bounded custom-node set. Its repository gate and date are scoped to those old subjects in `historical_evidence` as well. Neither campaign qualifies the new pins. The earlier core `0.37.0` pair remains historical too. Neither Desktop nor the unexecuted alternatives (the published core tag and its patch tags, and frontend patch `1.59.14`) have live qualification.
- **Legacy Desktop host surface**: Desktop `0.9.4` embeds frontend `1.43.18`, which lags the standalone frontend `1.61.8` release reference. Validate this fixed bundle against its own anchor.
- **Current Comfy-Desktop host surface**: application `1.0.32-rc.1` is a managed-install generation. Its hosted ComfyUI and frontend versions are `installation_specific`; the application anchor must not be cross-wired into fixed hosted-version claims.

## Residual Host-Contract Decisions

- **Additive preview metadata**: `preview_id` does not alter filename-backed `/view` refs. The host strips `asset_preview` before client/history/cache output; OpenClaw does not fetch that URL or introduce a `previews` directory type.
- **SaveImage output refs**: OpenClaw consumes runtime `/history` output refs and does not infer graph-rewrite behavior from output-node socket shape. `SaveImage` output sockets are allowed to exist without changing the normalized output-ref contract.
- **3D output refs**: 3D refs remain media-aware output refs. Current Load3D-family and Save3D nodes report only the standard `3d` item, with `camera_info` and `model_3d_info` alongside it; Load3D-family refs use the `temp` directory. The Preview3D node still reports the legacy `result` item. Older hosts can report one saved file in both `3d` and legacy `result` for the same node; Python history parsing and browser output normalization emit one canonical ref for it, while distinct files, host directory types, and nodes remain separate. Optional per-file `metadata` (kind, dimensions, duration) does not change the normalized ref. File-like refs and optional hash-backed 3D refs stay on the bounded `/view` preview contract; clients without a 3D renderer should show an explicit fallback/link surface.
- **HDR image output refs**: `.exr` and `.hdr` image refs stay on the bounded `/view` source-preview contract but render as explicit fallback/link surfaces unless a client implements a safe HDR-specific viewer.
- **File-backed text output refs**: allowlisted text files under the host `files` output key normalize to text refs on the existing `/view` route. Job Monitor uses same-origin, redirect-free, strict MIME/UTF-8 streaming with fixed 5-second, 64-KiB transfer, and 4,096-character display limits; failures remain source-link fallbacks and content is never interpreted as HTML or Markdown.
- **Promoted widget source scope and structured widgets**: OpenClaw graph helpers preserve complete node IDs and host-shaped promoted-widget source metadata. Exact colon-containing IDs take precedence over path interpretation; shared definitions require an instance path, and ambiguous edits/replays fail before writes. Backend preflight remains a conservative model-key whitelist; structured `COLORS` / `BOUNDING_BOXES` inputs and frontend source metadata are not treated as model references. This does not claim full host frontend active-scope parity.
- **Asset dimensions and grouped assets**: typed width/height metadata and grouped multi-download behavior are host-frontend display/download concerns. They do not change OpenClaw fetch routing, and asset-service-only identifiers remain explicit `asset_api_required` states rather than implicit `/api/assets` fetches.
- **Asset loader paths and model tags**: current host asset metadata may expose `loader_path`; model uploads require `model_type:<folder_name>` tags, advertised by `/features.supports_model_type_tags`. OpenClaw does not upload through or directly consume `/api/assets`, so these schema facts do not change the existing `/history` + `/view` contract.
- **Sidebar registration**: prefer the current `sidebarTab.registerSidebarTab` host API and retain the deprecated `extensionManager.registerSidebarTab` fallback for older or desktop-embedded frontend hosts.
- **Sidebar sizing**: both frontend releases of the last qualified pair use native panels. OpenClaw recognizes
  the complete host panel contract, leaves ancestor sizing to the host, and preserves splitter
  changes across tab switches. Native layouts wrap model controls at narrow widths; the legacy
  width fallback applies only to older panel hosts.
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
