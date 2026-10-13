# Security Key and Token Lifecycle SOP

This SOP defines operational procedures for three security-critical materials:

1. Registry signature trust roots (`S61`)
2. Secrets-at-rest encryption key (`S57`)
3. Bridge device tokens (`S58`)

Use this runbook for routine rotation, emergency revocation, and disaster recovery.

Scope note:
- If `S11` optional 1Password provider is enabled, provider API keys can be sourced from local 1Password instead of `secrets.enc.json`. In that mode, this SOP still governs the local encryption key lifecycle for any secrets that remain in server-side store.

## 1. State Artifacts

All paths are relative to `OPENCLAW_STATE_DIR` (legacy fallback: `MOLTBOT_STATE_DIR`):

| Material | File(s) | Purpose |
| --- | --- | --- |
| Registry trust roots | `registry/trust/trust_roots.json` | Key IDs, fingerprints, validity windows, revocation state |
| Secrets-at-rest key | `secrets.key` | Envelope encryption key for `secrets.enc.json` |
| Encrypted secret store | `secrets.enc.json` | Encrypted provider secrets |
| Bridge token registry | `bridge_tokens.json` | Device token lifecycle state and audit trail |

Optional startup log hygiene for incident drills:
- Set `OPENCLAW_LOG_TRUNCATE_ON_START=1` before restart when you need a clean `openclaw.log` timeline.

Optional `S11` local secret-manager settings (if used):
- `OPENCLAW_1PASSWORD_ENABLED=1`
- `OPENCLAW_1PASSWORD_ALLOWED_COMMANDS=<allowlisted executables>`
- `OPENCLAW_1PASSWORD_CMD=op`
- `OPENCLAW_1PASSWORD_VAULT=<vault>`

## 2. Global Rules

1. Always take a timestamped backup before any lifecycle operation.
2. Never rotate all three materials in one change window.
3. Require two-person review for revoke or disaster-recovery actions.
4. Record exact commands and outputs in the implementation record/change ticket.

## 3. Registry Trust Root Governance (`S61`)

### 3.1 Planned rotation (overlap window)

1. Add the new signer key as an additional trust root.
2. Start signing new artifacts with the new key while old key remains active.
3. Verify both key paths pass signature verification.
4. Revoke old key after rollout completion.

Example add/revoke flow:

```bash
python - <<'PY'
import os, time
from services.registry_quarantine import TrustRoot, TrustRootStore

state_dir = os.environ["OPENCLAW_STATE_DIR"]
store = TrustRootStore(state_dir)

# Add new root (replace key_id/public_key_pem)
store.add_root(TrustRoot(
    key_id="k-2026q1",
    public_key_pem="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
    valid_from=time.time(),
))

# Revoke old root after cutover
store.revoke_root("k-2025q4", reason="planned rotation complete")
print("active_roots=", [r.key_id for r in store.get_active_roots()])
PY
```

### 3.2 Emergency revocation

1. Revoke compromised `key_id` immediately.
2. Switch registry policy to strict mode if not already strict (`OPENCLAW_REGISTRY_POLICY=strict`).
3. Reject/quarantine artifacts signed only by revoked key.

### 3.3 Disaster recovery

1. Restore `registry/trust/trust_roots.json` from last known good backup.
2. Verify expected fingerprints before enabling registry sync.
3. Run `tests.test_s61_registry_signature` before production rollout.

## 4. Secrets-at-Rest Key Governance (`S57`)

### 4.1 Planned key rotation

1. Back up `secrets.key` and `secrets.enc.json`.
2. Decrypt existing envelope with old key.
3. Generate a new key and re-encrypt the same secret payload.
4. Restart service and verify secret reads.

Reference script (run in maintenance window; requires `cryptography` installed):

```bash
python - <<'PY'
import os
import json
import shutil
from pathlib import Path
from cryptography.fernet import Fernet
from services import secrets_encryption as enc

state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
old_key_path = state_dir / "secrets.key"
store_path = state_dir / "secrets.enc.json"

shutil.copy2(old_key_path, state_dir / "secrets.key.bak")
shutil.copy2(store_path, state_dir / "secrets.enc.json.bak")

envelope = enc.EncryptedEnvelope.from_dict(json.loads(store_path.read_text(encoding="utf-8")))
old_key = old_key_path.read_bytes().strip()
secrets = enc.decrypt_secrets(envelope, old_key)

new_key = Fernet.generate_key()
old_key_path.write_bytes(new_key)
new_envelope = enc.encrypt_secrets(secrets, new_key)
store_path.write_text(json.dumps(new_envelope.to_dict(), indent=2), encoding="utf-8")
print("rotated secrets.key and re-encrypted secrets.enc.json")
PY
```

### 4.2 Emergency handling

1. If key exposure is suspected, rotate `secrets.key` immediately.
2. Re-issue high-risk credentials upstream (provider/API tokens), then update store.

### 4.3 Disaster recovery

1. If `secrets.key` is lost and no backup exists, encrypted secrets are unrecoverable by design.
2. Re-provision all provider secrets from source systems.
3. Delete stale `secrets.enc.json`, then repopulate secrets through approved flows.

## 5. Bridge Token Lifecycle Governance (`S58`)

### 5.1 Planned rotation

1. Issue/rotate per device with bounded overlap (max 30 minutes).
2. Verify new token works before overlap expiry.
3. Revoke old token after cutover.

Example rotation flow:

```bash
python - <<'PY'
import os
from services.bridge_token_lifecycle import get_token_store

store = get_token_store(os.environ["OPENCLAW_STATE_DIR"])
active = store.list_tokens(device_id="device-a", active_only=True)
old = active[0]
new_token, old_token = store.rotate_token(old.token_id, overlap_sec=300, ttl_sec=3600)
print("new_token_id=", new_token.token_id)
print("old_overlap_until=", old_token.overlap_until)
PY
```

### 5.2 Emergency revocation

1. Revoke compromised token IDs immediately (`revoke_token`).
2. If blast radius is unclear, disable bridge ingress (`OPENCLAW_BRIDGE_ENABLED=0`) until re-issued tokens are deployed.
3. Validate audit trail contains revoke events.

### 5.3 Disaster recovery

1. Restore `bridge_tokens.json` from backup if corruption is detected.
2. If integrity is uncertain, revoke all active tokens and re-issue per device.
3. Validate with `tests.test_s58_bridge_token_lifecycle` and `tests.test_s58_bridge_auth_integration`.

## 6. Drill Automation (Evidence-Generating, Optional but Recommended)

In addition to the manual procedures above, OpenClaw provides a local/CI-safe drill runner that simulates lifecycle incidents and emits machine-readable evidence.

Script:
- `scripts/run_crypto_lifecycle_drills.py`

Supported scenarios:
- `planned_rotation`
- `emergency_revoke`
- `key_loss_recovery`
- `token_compromise`

Example commands:

```bash
python scripts/run_crypto_lifecycle_drills.py --pretty
python scripts/run_crypto_lifecycle_drills.py --scenarios planned_rotation,emergency_revoke --output crypto_drills.json --pretty
```

Evidence bundle contract (JSON):
- top-level fields include `schema_version`, `bundle`, `state_dir`, and `drills`
- each drill record includes:
  - `operation`
  - `scenario`
  - `precheck`
  - `result`
  - `rollback_status`
  - `artifacts`
  - `decision_codes`
  - `fail_closed_assertions`

Operational notes:
- This drill runner is for verification/training/evidence collection and does not replace maintenance-window production rotation procedures.
- Use an isolated or temporary state directory unless you intentionally want artifacts written to a specific test state path.
- Store drill evidence alongside change tickets or implementation records when lifecycle readiness is part of acceptance criteria.

## 7. Validation Gate After Any Lifecycle Change

From a source checkout of the repository (registry installs do not include the test suite), run at minimum:

```bash
python scripts/run_unittests.py --module tests.test_s58_bridge_token_lifecycle
python scripts/run_unittests.py --module tests.test_s61_registry_signature
python scripts/run_unittests.py --module tests.test_s57_secrets_encryption
python scripts/run_unittests.py --module tests.test_s60_routes_startup_gate
python scripts/run_unittests.py --module tests.security.test_endpoint_drift
```

Then run the full gate from `tests/TEST_SOP.md` before rollout.
