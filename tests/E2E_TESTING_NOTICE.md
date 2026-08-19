All E2E tests must be performed using the standard procedures defined in
`tests/E2E_TESTING_SOP.md`.

<!-- CURRENT-TEST-GOVERNANCE:START -->
## Current Governance Scope

A change limited to pure text/documentation files, a version-field-only `pyproject.toml` update, or
both does not enter this E2E workflow and requires no planning, roadmap item, record/log,
independent review, documentation test contract, browser installation, or full gate. Behavior-
bearing metadata changes do not qualify. For non-exempt work, applicable E2E runs through the
authoritative Windows Full Gate. Hosted CI repetitions are optional diagnostics and are not
acceptance prerequisites or pushed-commit evidence. Explicit item-scoped live/supported-host checks
remain separate when required.
<!-- CURRENT-TEST-GOVERNANCE:END -->

Mandatory testing-design rule:

- E2E tests must be designed to reproduce real user-visible failures and catch bugs early, not merely to pass validation.
- Do not add pass-only E2E checks that cannot fail for the bug class under review.
- For every user-reported or high-risk frontend regression, ask which E2E assertion would have caught it before release, then add or update that assertion.
Exception:
- strictly documentation-only changes do not require entering the E2E workflow
- this exception does not apply once application code, test code, scripts, configs, or generated artifacts change

Scope note:
- `tests/E2E_TESTING_SOP.md` is frontend Playwright harness SOP.
- Backend real-E2E lanes (`tests.test_r122_real_backend_lane`, `tests.test_r123_real_backend_model_list_lane`) are governed by `tests/TEST_SOP.md`.

For public/admin/webhook/connector or other user-facing transaction changes, acceptance evidence must include at least one transaction-level probe that verifies the actual submitted outcome; route load or redirect-only evidence is not sufficient on its own.
<!-- ROOKIEUI-GLOBAL-E2E-NOTICE:START -->
## RookieUI-Derived Global E2E Notice

All E2E tests must follow `tests/E2E_TESTING_SOP.md`. Full acceptance workflow and gate order remain defined by `tests/TEST_SOP.md`.

Mandatory testing-design rule:

- E2E tests must be designed to reproduce real user-visible failures and catch bugs early, not merely to pass validation.
- Do not add pass-only E2E checks that cannot fail for the bug class under review.
- For every user-reported or high-risk frontend regression, ask which E2E assertion would have caught it before release, then add or update that assertion.

Exception:

- strictly documentation-only changes do not require entering the E2E workflow
- once code/tests/scripts/config/runtime files change, this exception does not apply

For transaction-sensitive features, acceptance evidence must include at least one action-level assertion of final outcome, not route-load evidence only.
<!-- ROOKIEUI-GLOBAL-E2E-NOTICE:END -->
