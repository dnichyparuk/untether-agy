---
name: documentation-quality-review
description: "Reviews AI-context files and docs for cross-file consistency, path verification, and version/command accuracy per this project's own context-quality rule"
triggers:
  - "**/*.md"
  - "CLAUDE.md"
  - "AGENTS.md"
  - "GEMINI.md"
  - ".cursorrules"
  - ".github/copilot-instructions.md"
  - "docs/**"
skip-when:
  - "CHANGELOG.md"
severity: low
model: sonnet
---

# Documentation Quality Review

This project has its own documented rule for AI context files
(`.claude/rules/context-quality.md`) — this dimension operationalizes exactly that rule
at review time, plus the FAQ-specific rules in `.claude/rules/help-faq.md`.

## Checklist

- [ ] **Cross-file consistency**: if `CLAUDE.md` was updated, check whether the same fact
      needs updating in `AGENTS.md`, `GEMINI.md`, `.cursorrules`, and
      `.github/copilot-instructions.md` (they must agree on language/framework version, key
      commands, directory structure, and critical rules)
- [ ] **Path verification**: every file path referenced in a context/doc change actually exists
      on disk — no reference to a deleted, renamed, or moved file
- [ ] **Version accuracy**: referenced runtime/framework versions match `pyproject.toml`
      (`requires-python`, dependency versions)
- [ ] **Command accuracy**: every command mentioned (test, build, lint, deploy) is runnable as
      written — cross-check against `pyproject.toml` scripts / `Justfile` targets
- [ ] If `docs/faq/faq.md` changed: every `## ` heading is question-shaped (ends with `?` or
      starts with How/What/Why/When/Where/Can/Do/Does/Is/Are/Should/Will), no `TODO`/
      `[placeholder]`/`TBD` text, and at least 7 Q/A pairs remain
      (see `.claude/rules/help-faq.md`)
- [ ] New features/commands are reflected in `CLAUDE.md`'s feature list and, where relevant,
      the FAQ (per the `.claude/rules/release-discipline.md` "FAQ touch-up check")
- [ ] No stale reference to a removed engine, command, or config key

## Severity Guide

| Finding | Severity |
|---------|----------|
| Referenced file path does not exist | medium |
| Cross-file inconsistency (one context file updated, others left stale) | medium |
| Command shown as runnable but is not (typo, removed script) | medium |
| Version mismatch vs. `pyproject.toml` | low |
| FAQ H2 not question-shaped, or contains placeholder text | low |
| Missing FAQ update for a user-facing feature change | low |
| Minor wording/style inconsistency | info |
