# Advanced Registry Sync and Constrained Transforms

This guide covers optional, high-control features that are disabled by default.

## Overview

- Remote registry sync uses quarantine and trust policy controls.
- Constrained transforms execute trusted Python modules with strict runtime limits.
- Both features are fail-closed when disabled.
- Optional operational log hygiene: `OPENCLAW_LOG_TRUNCATE_ON_START=1` clears stale `openclaw.log` at backend startup (once per process).

## Remote registry sync

Enable remote registry sync:

```bash
OPENCLAW_ENABLE_REGISTRY_SYNC=1
```

Trust policy:

- `OPENCLAW_REGISTRY_POLICY=audit` (default): records signature/provenance issues for review
- `OPENCLAW_REGISTRY_POLICY=strict`: rejects non-compliant artifacts

Behavior highlights:

- Quarantine lifecycle is persisted under the state directory:
  - `registry/quarantine/index.json`
- Entries are tracked with audit trail records.
- Anti-abuse controls include bounded dedupe windows and rate limiting.
- Integrity and policy checks are enforced before activation paths.

If registry sync is not enabled, registry operations fail closed.

## Constrained transforms

Enable constrained transforms:

```bash
OPENCLAW_ENABLE_TRANSFORMS=1
```

Runtime limits:

- `OPENCLAW_TRANSFORM_TIMEOUT` (seconds, default `5`)
- `OPENCLAW_TRANSFORM_MAX_OUTPUT` (bytes, default `65536`)
- `OPENCLAW_TRANSFORM_MAX_PER_REQUEST` (default `5`)

Trusted module paths:

- Default trusted directory: `data/transforms`
- Add extra trusted directories with `OPENCLAW_TRANSFORM_TRUSTED_DIRS`
  - Use OS path separator (`;` on Windows, `:` on Linux/macOS)

Security controls:

- Only `.py` modules are allowed
- Module size is capped
- Module hash is pinned at registration time
- Integrity is re-checked before execution
- Execution is bounded by timeout and output budget
- The worker process receives only an allowlisted environment (`PATH`, `PATHEXT`, `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `TEMP`, `TMP`, `TMPDIR`, `HOME`, `USERPROFILE`, `LANG`, `LC_ALL`, `LC_CTYPE`, `PYTHONIOENCODING`, `PYTHONUTF8`, `PYTHONHOME`, `PYTHONPATH`, `LD_LIBRARY_PATH`); transforms cannot read other variables

If transforms are disabled, transform execution is denied and mapping-only behavior continues.

## Example hardened operator profile

```bash
OPENCLAW_ENABLE_REGISTRY_SYNC=1
OPENCLAW_REGISTRY_POLICY=strict
OPENCLAW_ENABLE_TRANSFORMS=1
OPENCLAW_TRANSFORM_TIMEOUT=3
OPENCLAW_TRANSFORM_MAX_OUTPUT=32768
OPENCLAW_TRANSFORM_MAX_PER_REQUEST=3
```

## Rollout notes

1. Enable one feature at a time in a non-production environment.
2. Review logs and operator diagnostics after startup.
3. Keep strict policies for public or multi-tenant deployments.
4. Treat trusted transform directories as code deployment boundaries.
