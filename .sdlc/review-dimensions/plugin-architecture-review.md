---
name: plugin-architecture-review
description: "Reviews new/changed engine and transport backends against the entry-point plugin contract"
triggers:
  - "**/plugins.py"
  - "**/runtime_loader.py"
  - "**/backends.py"
  - "**/backends_helpers.py"
  - "**/engines.py"
  - "**/runners/*.py"
  - "**/schemas/*.py"
  - "pyproject.toml"
skip-when:
  - "**/tests/**"
severity: medium
model: sonnet
---

# Plugin Architecture Review

Untether registers engine and transport backends via Python entry points
(`untether.engine_backends`, `untether.transport_backends`) rather than a manifest file.
Review new or modified backends against this contract.

## Checklist

- [ ] A new `BACKEND = EngineBackend(id=..., build_runner=..., ...)` export has an `id` that
      **exactly matches** its `pyproject.toml` entry-point key (e.g. `antigravity = "..."` must
      equal `BACKEND.id == "antigravity"`) — a mismatch breaks `get_backend()` resolution silently
- [ ] The entry-point registration is added under the correct group (`untether.engine_backends`
      for agent CLIs, `untether.transport_backends` for delivery channels) — not mixed up
- [ ] New runners subclass `JsonlSubprocessRunner` (or `ResumeTokenMixin` + it) and implement
      the template methods (`command`, `build_args`, `translate`, `new_state`, error hooks)
      rather than duplicating the base run loop / 3-event-contract enforcement
- [ ] A new backend does not require changes to unrelated runners or to the base runner class
      — the plugin boundary should stay isolated (if it does require base-class changes, that's
      worth flagging, not necessarily blocking)
- [ ] New schemas (`schemas/*.py`) use `forbid_unknown_fields=False` (or an equivalent
      forward-compat stance) unless there's a documented reason to be strict
- [ ] `uv lock --check` is expected to pass after a `pyproject.toml` entry-point addition (no
      new runtime dependency should be implied by an entry-point-only change)
- [ ] Reserved/Untether-managed CLI flags are rejected in any user-configurable `extra_args`
      list, consistent with the pattern in existing runners (e.g. `claude.py`, `gemini.py`)

## Severity Guide

| Finding | Severity |
|---------|----------|
| Entry-point id mismatch with `BACKEND.id` (breaks resolution) | critical |
| New runner bypasses the 3-event contract enforcement | high |
| New runner duplicates/reimplements base run-loop logic unnecessarily | medium |
| Entry point registered under the wrong group | high |
| `extra_args` allows overriding a Untether-managed flag | high |
| Schema missing forward-compat stance without justification | low |
| Missing/incomplete reference docs for a new backend | low |
