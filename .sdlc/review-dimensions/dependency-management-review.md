---
name: dependency-management-review
description: "Reviews pyproject.toml/uv.lock changes for lockfile consistency and justified new dependencies"
triggers:
  - "pyproject.toml"
  - "uv.lock"
  - ".github/dependabot.yml"
  - ".github/workflows/prerelease-deps.yml"
skip-when: []
severity: medium
model: haiku
---

# Dependency Management Review

Review dependency changes for lockfile consistency and whether new packages are justified.

## Checklist

- [ ] `uv.lock` is updated consistently with any `pyproject.toml` dependency change — no
      divergence (`uv lock --check` is part of this project's release checklist)
- [ ] New runtime dependencies are justified — this project's runner additions have historically
      been kept dependency-free (new engines wrap an external CLI, not a new Python package)
- [ ] New dependencies are pinned to a reasonable range, not `*` or unbounded
- [ ] No package with a known critical CVE is introduced (cross-check against the existing
      `pip-audit` CI job's intent)
- [ ] Dev-only dependencies aren't added to the runtime dependency list
- [ ] Entry-point additions under `[project.entry-points.*]` don't imply an undeclared new
      dependency
- [ ] `dependabot.yml` grouping/scheduling changes don't accidentally widen the auto-merge scope
      beyond the documented patch/minor-only policy for Python deps

## Severity Guide

| Finding | Severity |
|---------|----------|
| Package with a known critical CVE added | critical |
| `uv.lock` diverges from `pyproject.toml` | high |
| Unjustified new runtime dependency for a change that shouldn't need one | medium |
| Dev dependency placed in the runtime dependency list | medium |
| Unbounded (`*`/`latest`) version specifier | medium |
| Dependabot auto-merge scope silently widened | high |
| Minor version-range tightening with no stated reason | low |
