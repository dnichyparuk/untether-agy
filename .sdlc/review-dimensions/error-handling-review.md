---
name: error-handling-review
description: "Reviews runner error paths for the 3-event contract, stall diagnostics, and signal-death handling"
triggers:
  - "**/runners/**"
  - "**/error_hints.py"
  - "**/error_display.py"
  - "**/utils/error_display.py"
  - "**/runner_bridge.py"
  - "**/utils/proc_diag.py"
skip-when:
  - "**/tests/**"
severity: medium
model: sonnet
---

# Error Handling Review

Every Untether runner must satisfy the **3-event contract**: `StartedEvent` →
`ActionEvent*` → exactly one terminal `CompletedEvent`, even when the subprocess crashes,
produces no output, or emits malformed JSON. Review error paths against that invariant.

## Checklist

- [ ] Every code path that can end a run (normal completion, non-zero exit, empty stream,
      decode error, timeout) ultimately emits exactly one `CompletedEvent` — no path emits zero
      or more than one
- [ ] Process errors include an rc label and a stderr excerpt (mirror the existing
      `_rc_label` / `_stderr_excerpt` helper pattern) rather than a bare "failed"
- [ ] Malformed/undecodable JSONL lines are dropped with a warning log
      (`jsonl.msgspec.invalid`-style), not allowed to crash the run loop
- [ ] Auto-continue logic correctly suppresses on signal deaths (`rc=143`/SIGTERM,
      `rc=137`/SIGKILL) to avoid a death spiral under memory pressure
- [ ] Stall/liveness watchdog warnings don't double-fire for the same condition (repeat
      suppression while a child process is CPU-active)
- [ ] New engines without live per-tool progress (terminal-only result envelopes) don't get
      falsely flagged as hung by the watchdog — this needs verification, not just assumption
- [ ] No error path leaves a partially-written Telegram progress message without a terminal
      edit (message must resolve to something readable, not stay stuck on "working…")

## Severity Guide

| Finding | Severity |
|---------|----------|
| Contract violation — zero or duplicate `CompletedEvent`s | critical |
| Auto-continue retries after a signal death | high |
| Silent failure with no user-visible error | high |
| Missing stderr/rc context on process failure | medium |
| Watchdog double-fire for the same stall | medium |
| Cosmetic error message mismatch | low |
