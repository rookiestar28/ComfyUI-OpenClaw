# CodeQL and Secret Scanning Policy Reference

<!-- CURRENT-TEST-GOVERNANCE:START -->
## Current Acceptance Boundary

- Pure text/documentation changes and version-field-only `pyproject.toml` updates require no plan,
  independent reviewer, test contract, Full Gate, E2E, or Hosted CI run.
- For non-exempt work, the Windows Full Gate is authoritative. CodeQL and secret-scanning workflows
  are supplemental hosted security diagnostics; they are not ordinary acceptance prerequisites and
  do not bind evidence to a pushed commit.
- When an active security item explicitly requires closure of an existing GitHub alert, the hosted
  rescan is item-scoped external evidence only and remains separate from repository-wide acceptance.
<!-- CURRENT-TEST-GOVERNANCE:END -->

Date: 2026-04-08
Scope: Repository planning guidance for GitHub-native security scanning policy during and after the residual alert wave.

Current status:

- the repository now uses the committed advanced CodeQL workflow as its authoritative scanner baseline
- GitHub `Code scanning` and `Secret scanning` were both brought back to `0` open findings during the `S91` closeout

## 1. Why CodeQL Belongs in GitHub Actions

This repository has a large security surface:

- Python backend services
- JavaScript frontend and test helpers
- GitHub Actions workflows
- connector ingress paths
- filesystem and model-management flows

The residual GitHub findings demonstrate that static security analysis is catching issues that ordinary local happy-path tests do not reliably surface.

The correct home for CodeQL in this repository is the GitHub Actions security-validation layer, not the mandatory local development loop.

## 2. Local vs Remote Validation Boundary

Local validation should focus on:

- targeted regressions for the changed bug surface
- repo-local contract tests
- the smallest credible transaction seam for each fix

GitHub-hosted validation should own:

- CodeQL scans
- code-scanning alert lifecycle
- long-running static dataflow analysis
- alert triage over the default branch
- secret-scanning closure workflow when a finding is confirmed to be historical or non-live

## 3. Recommended CodeQL Rollout Model

Recommended rollout order:

1. enable repository-native CodeQL in GitHub Actions
2. start in visibility/baseline mode
3. review new findings against changed files first
4. graduate to stricter gating only after the backlog is reduced

Recommended initial policy:

- languages: Python, JavaScript/TypeScript, GitHub Actions
- query suite: begin with standard security queries; expand only if runtime/cost remains acceptable
- gating: report-only at first, then fail on new high-severity findings after baseline stabilization

## 4. Secret Scanning Policy

Secret-scanning findings must be handled differently from CodeQL findings:

- removing or editing repository content does not automatically guarantee closure
- historical example values can continue to alert
- closure requires provenance review

Before closing a secret-scanning alert:

1. determine whether the value was ever real or was always an example / placeholder
2. confirm whether any rotation or revocation is required
3. avoid copying raw secret material into planning docs, issue text, or commit messages
4. record the closure rationale in planning and implementation evidence

## 5. Residual-Wave Review Checklist

For each remaining GitHub Security family:

- identify whether the finding is a true vulnerability, a scanner-visible dangerous pattern, or a probable false positive
- prefer code changes that make the safe boundary obvious to both humans and scanners
- add a hot-spot comment at the fix point when regression risk is high
- add targeted regression coverage if a local seam exists
- for an item explicitly closing an existing GitHub alert, verify the hosted rescan; ordinary
  repository acceptance does not require a push or rescan
- dismiss only as a last resort, with recorded rationale
