# Support Policy

## Support Tiers

### Tier 1: Fully Supported

**Definition**: Validated by CI/CD or core maintainers. Critical bugs block releases.

- **Environment**: Linux (Ubuntu 22.04), Windows 11.
- **Python**: 3.10, 3.11, 3.12, and 3.13.
- **ComfyUI host**: current compatibility-matrix reference anchor and close neighbors.
- **Frontend host**: current standalone frontend reference anchor for the sidebar extension contract.

### Tier 2: Best Effort

**Definition**: Should work, but not actively validated. Bugs fixed as resources allow.

- **Environment**: macOS, older Windows versions.
- **Python**: 3.14.
- **ComfyUI**: nightly builds and farther-from-anchor upstream drift.
- **Desktop host**: legacy fixed-bundle variants outside the recorded legacy anchor and current managed-install variants whose installed host components fall outside their own supported anchors.

### Tier 3: Unsupported

**Definition**: Known to be incompatible or end-of-life.

- **Python**: < 3.10.
- **OS**: Windows 7/8.

## Deprecation Policy

- **Notice Period**: Breaking changes will be announced 1 minor version in advance.
- **Legacy Support**: Deprecated features (e.g., legacy `MOLTBOT_` env vars) are supported for at least 1 major version cycle.

## Compatibility Anchor Policy

- The authoritative compatibility reference points are recorded in [`compatibility_matrix.md`](compatibility_matrix.md).
- `ComfyUI`, standalone `ComfyUI_frontend`, legacy `desktop`, and current `comfy_desktop` are tracked as separate anchors.
- Legacy Desktop is a fixed bundle and must be evaluated against its recorded core/frontend versions.
- Current Comfy-Desktop is a managed-install generation; hosted ComfyUI and frontend versions are installation-specific and must not be inferred from the application version.
- Upstream reference refreshes should update the matrix anchors before being treated as the new default support baseline.

## Reporting Issues

Please report issues on [GitHub Issues](https://github.com/rookiestar28/ComfyUI-OpenClaw/issues).
Include:

- OS and Python version
- ComfyUI version
- Workflow JSON (redacted)
- Logs (redacted)
