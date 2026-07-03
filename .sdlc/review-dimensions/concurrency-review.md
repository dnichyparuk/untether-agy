---
name: concurrency-review
description: "Reviews anyio async task lifecycle, per-session locking, and atomic state-file writes"
triggers:
  - "**/runner.py"
  - "**/runner_bridge.py"
  - "**/scheduler.py"
  - "**/loop_scheduler.py"
  - "**/triggers/manager.py"
  - "**/triggers/cron.py"
  - "**/telegram/loop.py"
  - "**/telegram/at_scheduler.py"
  - "**/telegram/progress_persistence.py"
  - "**/telegram/offset_persistence.py"
  - "**/triggers/run_once_state.py"
  - "**/utils/json_state.py"
  - "**/runners/**"
skip-when:
  - "**/tests/**"
severity: high
max-files: 40
model: sonnet
---

# Concurrency Review

Untether is built on anyio (structured concurrency) with long-lived background tasks
(Telegram polling, cron scheduler, webhook server, `/at` delayed runs) and per-session
subprocess locking. Review for race conditions and lifecycle bugs specific to this design.

## Checklist

- [ ] `SessionLockMixin` locks are acquired **before** yielding the first `StartedEvent` on a
      fresh run, and released in a `finally` block — no path can leave a lock held after a crash
- [ ] New background tasks are scoped to a task group with a clear owner/lifetime — no task is
      spawned without a way to cancel it (mirror `_cancel_chat_tasks()` in `topics.py`)
- [ ] JSON state files written from a request/event handler (`active_progress.json`,
      `last_update_id.json`, run_once cron state, offset persistence) use an atomic
      write-temp-then-rename pattern, not an in-place write that a concurrent reader could see
      half-written
- [ ] `DebouncedOffsetWriter`-style debounced writers flush on shutdown, not just on the timer —
      no update-id or state loss on graceful restart
- [ ] Cron/webhook hot-reload (`TriggerManager` mutable state) does not race with an in-flight
      cron firing or webhook dispatch — the atomic config swap must not leave a half-updated view
- [ ] No new polling loop busy-waits without a sleep/backoff
- [ ] Signal handlers (SIGTERM drain) don't deadlock waiting on a lock held by the task being
      drained
- [ ] Semaphore/lock keys derived from `WeakValueDictionary` don't leak across process restarts
      or get resurrected with stale state

## Severity Guide

| Finding | Severity |
|---------|----------|
| Session lock not released on error path | critical |
| Non-atomic write to a state file read concurrently | high |
| Orphaned/uncancellable background task | high |
| Hot-reload race that can apply a half-updated config | high |
| Busy-wait loop without backoff | medium |
| Debounced writer that can lose data on ungraceful shutdown | medium |
| Minor lock-scope broadening (held longer than necessary) | low |
