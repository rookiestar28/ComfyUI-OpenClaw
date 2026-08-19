# CI Regression Policy

<!-- CURRENT-TEST-GOVERNANCE:START -->
## Current Test Governance

- Pure text/documentation changes and version-field-only `pyproject.toml` updates require no plan,
  independent reviewer, test contract, Full Gate, E2E, or Hosted CI run. Behavior-bearing
  `pyproject.toml` changes are not exempt.
- Non-exempt implementation work must pass the repository Windows Full Gate. That local result is
  the authoritative repository-wide acceptance evidence; push and Hosted CI are not prerequisites
  and need not bind evidence to a pushed commit.
- Hosted workflows, including CodeQL, are advisory diagnostics unless an active item explicitly
  makes a separate security, release, or publication boundary part of its acceptance criteria.
<!-- CURRENT-TEST-GOVERNANCE:END -->

All non-exempt pull requests must pass the Windows repository SOP gate before merge.

## Required Windows Full Gate Checks

| Check | Command | Purpose |
| --- | --- | --- |
| Secret detection | `pre-commit run detect-secrets --all-files` | Prevent secret leakage |
| Pre-commit hooks | `pre-commit run --all-files --show-diff-on-failure` | Enforce formatting and static checks |
| Production dependency boundary | `python scripts/verify_production_dependencies.py` | Parse tracked production imports without importing modules; block ownership, direction, cycle, and dynamic-import drift |
| Frontend dependency audit | `npm ci` then `npm audit --audit-level=high` | Reconcile the lockfile and fail on high/critical vulnerabilities across production and development dependencies |
| Backend dependency audit | `pip-audit -r requirements.txt` | Audit declared Python project dependencies without scanning unrelated CI runner/toolchain packages |
| Coverage governance | `python scripts/verify_quality_governance.py` | Fail closed on coverage-policy, mutation-threshold, SOP-guidance, and survivor-allowlist drift |
| Test debt governance | `python scripts/verify_test_debt_governance.py` | Fail closed on stale or under-documented skip-policy / mutation allowlist debt entries |
| Backend unit coverage gate | `python scripts/run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json --coverage-json .tmp/coverage/backend_unit_coverage.json` | Validate backend behavior, skip governance, and the active coverage floor from the shared local/CI artifact path |
| Adversarial gate | `python scripts/run_adversarial_gate.py --profile auto --seed 42` | Enforce adaptive fuzz/mutation verification with smoke=>extended escalation on high-risk diffs |
| Frontend E2E | `npm test` | Validate UI and frontend/backend integration |

## Advisory Hosted Checks

GitHub CodeQL in `.github/workflows/codeql.yml` may run on push, pull request, or schedule as
supplemental security diagnostics. It is not a repository-acceptance prerequisite and is not bound
to a pushed acceptance candidate.

## Public MAE Hard-Guarantee Suites

These suites are explicit no-skip CI gates to prevent route classification drift:

- `tests.test_s60_mae_route_segmentation`
- `tests.test_s60_routes_startup_gate`
- `tests.security.test_endpoint_drift`

If any of these fail or are skipped, CI must fail.

## Change Management Rule

If a change intentionally modifies contract behavior:

1. Update affected tests and docs in the same PR.
2. Record the behavior change and migration impact in release notes.
3. Keep security-path tests on triple-assert semantics (status + machine code + audit signal).

## Governance Baseline

- Coverage governance is part of the standard gate, not an optional reporting step.
- Dependency-audit governance is part of the Windows Full Gate:
  - Node audit must cover production and development dependencies because build and test tooling is part of the acceptance trust boundary.
  - A separate production-only audit may be retained as a runtime-boundary readback, but it is not a substitute for the full blocking audit.
  - Python audit must stay scoped to `requirements.txt`; env-wide bare `pip-audit` is out of contract because it can fail on tool-only transient packages that are not part of the repo dependency surface.
- GitHub Actions workflow files are part of the security boundary:
  - workflows using `GITHUB_TOKEN` must declare explicit least-privilege `permissions:` instead of relying on repository defaults
  - missing or broadened workflow token scope should be treated as CI-policy drift, not an acceptable implementation shortcut
  - CodeQL analysis must stay versioned in `.github/workflows/codeql.yml`; do not rely on UI-only default-setup drift for the repository baseline
  - CodeQL rollout remains visibility-first until the active backlog is burned down; treat new workflow findings as triage input, not an automatic merge blocker, unless the gating policy is explicitly tightened in roadmap/docs
- `pyproject.toml` must keep:
  - `fail_under >= 55.0`
  - `show_missing = true`
  - `skip_covered = true`
- staged coverage ratchet policy (`tests/coverage_governance_policy.json`) is the source of truth for:
  - current enforced floor
  - next planned ratchet target
  - hotspot families and any temporary exceptions
- `fail_under` must match the current stage floor declared in `tests/coverage_governance_policy.json`; do not ratchet the floor by editing `pyproject.toml` alone.
- Coverage hotspot review should use:
  - `python scripts/report_coverage_governance.py --coverage-json <path-to-coverage.json>`
- release-cycle promotion evidence must be retained in:
  - `tests/coverage_promotion_reviews.json`
  - ratchet-55 reviews must contain consecutive release boundaries, immutable commit and
    full-suite artifact identity, every required hotspot percentage, and owned regression suites
- backend coverage gate should use:
  - `python scripts/run_backend_coverage.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json --coverage-json .tmp/coverage/backend_unit_coverage.json`
- Test debt governance remains fail-closed:
  - no-skip modules in `tests/skip_policy.json` must keep explicit metadata (`reason` + `review_after`) and point at live test modules
  - mutation survivor allowlist entries must carry `review_after` dates and point at live repo files
  - review dates in the past are governance debt, not advisory comments
- Mutation governance remains adaptive:
  - smoke profile threshold: `20.0%`
  - extended profile threshold: `80.0%`
- Known equivalent mutation survivors must stay explicitly allowlisted in `tests/mutation_survivor_allowlist.json`; drift is a gate failure, not a warning.
