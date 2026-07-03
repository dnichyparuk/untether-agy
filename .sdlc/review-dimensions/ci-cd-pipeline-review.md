---
name: ci-cd-pipeline-review
description: "Reviews GitHub Actions workflows for the project's SHA-pinning, least-privilege, and release-automation conventions"
triggers:
  - ".github/workflows/**"
  - "cliff.toml"
  - "Justfile"
skip-when: []
severity: medium
max-files: 20
model: haiku
---

# CI/CD Pipeline Review

Untether's CI pins all third-party actions to full commit SHAs and uses top-level
least-privilege `permissions: {}`, with an automated tag-on-master → PyPI OIDC release flow.
Review workflow changes against these established conventions.

## Checklist

- [ ] Third-party action references use a full 40-char commit SHA, not a mutable tag
      (`@v3`, `@main`) — this repo's existing workflows are 100% SHA-pinned
- [ ] `permissions:` stays least-privilege — no new job grants broader-than-needed scopes
      without justification (top-level `permissions: {}` is the baseline)
- [ ] `auto-tag-on-master.yml` continues to skip pre-release versions (`rc`/`a`/`b`/`dev`
      suffixes) — a change here must not accidentally tag/release a staging build
- [ ] `release.yml`'s OIDC trusted-publishing flow to PyPI is not weakened (e.g. no reintroduced
      long-lived API token, no removed environment gate without an equivalent safeguard)
- [ ] `dependabot-auto-merge.yml`'s differentiated policy is preserved: GitHub Actions deps
      (CI-only) auto-merge for all bumps including major; Python deps (shipped in the wheel)
      auto-merge only for patch/minor, major bumps flagged for manual review
- [ ] New/changed jobs don't silently mask failures (`continue-on-error: true` on a job that
      should be a real gate)
- [ ] Cache keys (if any are added) are content-addressed (hash of `uv.lock`), not a static key
      that risks staleness
- [ ] Workflow triggers stay appropriately scoped — not `on: push` to every branch when a
      narrower trigger would do

## Severity Guide

| Finding | Severity |
|---------|----------|
| Action pin changed from SHA to a mutable tag | high |
| PyPI publish flow weakened (long-lived token reintroduced) | critical |
| `auto-tag-on-master.yml` no longer skips pre-releases | critical |
| Overly permissive `permissions:` grant | high |
| `continue-on-error` masking a real failure in a gating job | medium |
| Dependabot auto-merge policy loosened for major Python bumps | medium |
| Workflow trigger scoped too broadly | low |
