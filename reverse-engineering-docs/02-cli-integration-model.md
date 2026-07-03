# 02 — CLI Integration Model (the shared substrate)

This is the heart of "how Untether integrates CLIs." Every engine plugs into the **same**
machinery; only two pieces are engine-specific (`build_args` and `translate`). Understand this
document and the per-engine docs become short.

## The normalized event model

All engines are reduced to three event types (`src/untether/model.py`, all frozen `slots`
dataclasses discriminated by a literal `type`):

```python
StartedEvent   engine, resume: ResumeToken, title, meta          model.py:48
ActionEvent    engine, action: Action, phase, ok, message, level model.py:57
CompletedEvent engine, ok, answer, resume, error, usage          model.py:68
UntetherEvent = StartedEvent | ActionEvent | CompletedEvent      model.py:78
```

Supporting types:

```python
ResumeToken(engine, value, is_continue=False)                    model.py:32
Action(id, kind, title, detail)                                  model.py:40
ActionKind = command | tool | file_change | web_search |
             subagent | note | turn | warning | telemetry        model.py:10
ActionPhase = started | updated | completed                      model.py:28
```

Engines never build these dataclasses by hand. They use **`EventFactory`** (`events.py:23`), an
`EngineId`-bound builder that caches the session token and enforces invariants at construction:

- `factory.started(token, *, title, meta)` — rejects a token for another engine or a changed
  session id (`events.py:41–56`).
- `factory.action_started / action_updated / action_completed(...)`.
- `factory.completed(...)` / `completed_ok(...)` / `completed_error(...)` — back-fills `resume`
  from the cached token if omitted (`events.py:150`).

## The base runner: `JsonlSubprocessRunner`

Class hierarchy (`src/untether/runner.py`):

```
Runner (Protocol)                          runner.py:1387   – structural interface
BaseRunner(SessionLockMixin)               runner.py:282
  └─ JsonlSubprocessRunner                 runner.py:383
        ├─ ClaudeRunner(ResumeTokenMixin, JsonlSubprocessRunner)   claude.py:2041
        ├─ GeminiRunner(ResumeTokenMixin, JsonlSubprocessRunner)   gemini.py:312
        ├─ CodexRunner / OpenCodeRunner / PiRunner / AmpRunner
        └─ MockRunner / ScriptRunner (tests; not subprocess-based)  mock.py
```

### Template methods (the per-engine hooks)

These are the overridable methods. Required ones raise `NotImplementedError` in the base:

| Method | `runner.py` | Purpose / default |
|--------|-------------|-------------------|
| `command() -> str` | `:433` | the binary name (**required**), e.g. `gemini` |
| `build_args(prompt, resume, *, state) -> list[str]` | `:439` | build argv (**required**) |
| `stdin_payload(prompt, resume, *, state) -> bytes \| None` | `:448` | default: `prompt.encode()` |
| `env(*, state) -> dict[str,str] \| None` | `:457` | default: `None` (inherit) |
| `new_state(prompt, resume) -> Any` | `:460` | default: `JsonlRunState()` |
| `start_run(prompt, resume, *, state)` | `:463` | pre-spawn hook, default no-op |
| `decode_jsonl(*, line: bytes) -> Any \| None` | `:531` | default: JSON parse w/ brace-recovery |
| `translate(data, *, state, resume, found_session) -> list[UntetherEvent]` | `:638` | **required** — the JSONL→event mapper |
| `tag() -> str` | `:436` | default `str(self.engine)` |

Error/fallback builders (overridable, sensible defaults — all produce a `note_event`):
`invalid_json_events` (`:509`), `decode_error_events` (`:558`), `translate_error_events` (`:570`),
`process_error_events(rc, …)` (`:586`, note + `CompletedEvent(ok=False)` when `rc != 0`),
`stream_end_events(…)` (`:615`, `CompletedEvent(ok=False)` when the stream closes without a
terminal event).

### The run loop (`run_impl`, `runner.py:1236`)

```
new_state(); start_run()
build cmd = [command(), *build_args(...)]; payload = stdin_payload(...); env = env(...)
_check_prespawn_ram_guard(resume)                       :993   (MemAvailable vs watchdog thresholds)
async with manage_subprocess(cmd, stdin/stdout/stderr=PIPE, env, cwd):   utils/subprocess.py:168
    _send_payload(proc, payload)                        :686   (write stdin, then close it)
    stream = JsonlStreamState(expected_session=resume)
    task group:
        drain_stderr(...)          – keep last 20 stderr lines
        _subprocess_watchdog(...)  – liveness + orphan-pipe kill
        async for evt in _iter_jsonl_events(stdout=...): yield evt
    reader_done.set(); await proc.stderr.aclose()
    rc = await proc.wait(); stream.proc_returncode = rc
    if did_emit_completed:  return
    elif rc != 0:           yield process_error_events(...)
    else:                   yield stream_end_events(...)
```

`manage_subprocess` (`utils/subprocess.py:168`) sets `start_new_session=True` on POSIX (so the
PID equals the process-group id) and guarantees SIGTERM → 10 s → SIGKILL cleanup on exit.

stdout is read by `iter_bytes_lines` (`utils/streams.py`): a `BufferedByteReceiveStream` doing
`receive_until(b"\n", 10 MB)` (10 MB max line). Each raw line goes to `_handle_jsonl_line`.

### The per-line core (`_handle_jsonl_line`, `runner.py:849`)

1. If `did_emit_completed` → drop the line (log `runner.drop.jsonl_after_completed` once).
2. Update activity counters (`last_stdout_at`, `event_count`, `jsonl_seq`).
3. `_decode_jsonl_events`: `decode_jsonl` → on failure `decode_error_events`; on `None`
   `invalid_json_events`; on success `translate(...)` → on exception `translate_error_events`.
4. Populate diagnostics (`last_event_type`, `last_event_tool`, `recent_events` ring buffer);
   run `_classify_jsonl_event` and update the stuck-after-tool_result latch.
5. Iterate translated events, enforcing the 3-event contract (below).

## The 3-event contract and how it's enforced

**Every run emits exactly: one `StartedEvent`, then zero+ `ActionEvent`s, then exactly one
terminal `CompletedEvent`.** After `CompletedEvent`, all further JSONL is dropped.

Enforcement lives in `_handle_jsonl_line` (`runner.py:926–953`):

- **StartedEvent** → the subprocess `pid` is injected into `evt.meta`, then
  `handle_started_event` (`:648`) validates:
  - **engine match** — raises if `event.engine != self.engine`.
  - **session match** — if an `expected_session` (a resume) was given and it's not a "continue",
    `event.resume` must equal it; subsequent Started events must equal the already-found session.
  - **de-duplication** — the first Started emits; a duplicate with no `meta` is dropped; a
    duplicate *with* `meta` is re-emitted as supplementary metadata (used by Pi, `#225`, and by
    Claude to ship "turn complete" / late model info).

- **CompletedEvent** → the **terminal latch**: sets `stream.did_emit_completed = True`, appends
  the event, and `break`s. Combined with:
  - the stdout early-break once `did_emit_completed` (`#505`, `:982`) — stops reading so a child
    (MCP server, backgrounded shell) inheriting the stdout fd can't block on an EOF that never
    comes, and
  - the `run_impl` fallback (`process_error_events` / `stream_end_events` fire **only** when NOT
    `did_emit_completed`),

  this guarantees **exactly one** `CompletedEvent` per run, even on a crash or an abnormal exit.

## `JsonlStreamState` — per-subprocess state

`runner.py:330` (`@dataclass(slots=True)`). The trivial default `new_state` returns
`JsonlRunState` (`:325`, just a `note_seq`); subprocess runs use `JsonlStreamState`, which tracks:

- **Session**: `expected_session`, `found_session`, `did_emit_completed`, `jsonl_seq`.
- **Activity / stall diagnostics**: `last_stdout_at`, `last_event_type`, `last_event_tool`,
  `event_count`, `recent_events: deque(maxlen=10)`, `stderr_capture`, `proc_returncode`.
- **Liveness**: `liveness_stalls` canary counter.
- **Stuck-after-tool_result latch** (`#322`): `last_event_kind`, `last_tool_result_at`.
- **`engine_state: Any`** (`#346`) — opaque per-engine handle (e.g. Claude's background-task
  tracking) duck-typed by watchdogs.
- **Lifecycle state machine** (`#333`): `lifecycle_state` (`spawned` → …), transitions via
  `_transition_lifecycle` (`:391`) emitting idempotent `subprocess.state.<name>` logs.
- **`stall_suppression_counts`** (`#333`) — per-reason counters summarised in `session.summary`.

## Registration: `EngineBackend` + Python entry points

An engine advertises itself with a module-level `BACKEND` object:

```python
# src/untether/backends.py:20  (frozen slots dataclass)
EngineBackend(
    id: str,
    build_runner: Callable[[EngineConfig, Path], Runner],   # EngineConfig = dict[str, Any]
    cli_cmd: str | None = None,
    install_cmd: str | None = None,
)
```

Each engine module defines `BACKEND = EngineBackend(...)` plus a `build_runner(config,
config_path) -> Runner` factory. Registration is declared in `pyproject.toml`:

```toml
[project.entry-points."untether.engine_backends"]
codex    = "untether.runners.codex:BACKEND"
claude   = "untether.runners.claude:BACKEND"
opencode = "untether.runners.opencode:BACKEND"
pi       = "untether.runners.pi:BACKEND"
gemini   = "untether.runners.gemini:BACKEND"
amp      = "untether.runners.amp:BACKEND"
```

(Parallel groups exist for `untether.transport_backends` and `untether.command_backends`.)

**Resolution** (`engines.py`):
- `get_backend(engine_id, *, allowlist)` (`:20`) rejects reserved ids, then
  `load_plugin_backend(ENGINE_GROUP, engine_id, allowlist, validator=_validate_engine_backend)`.
- The validator (`engines.py:11`) asserts the loaded object is an `EngineBackend` and that
  `backend.id == entrypoint.name`.

**Generic loader** (`plugins.py`): `_discover_entrypoints` (`:143`) selects via
`importlib.metadata.entry_points().select(group=...)`, filters by the allowlist, validates ids,
detects duplicates; `load_entrypoint` (`:235`) does `ep.load()` + validator + memoization;
`load_plugin_backend` (`:290`) maps failures to `ConfigError`. Load errors are process-global and
inspectable (`get_load_errors`, surfaced by `untether plugins` and `doctor`).

**Adding a new engine** therefore reduces to: subclass `JsonlSubprocessRunner`, add a msgspec
schema, override `command/build_args/translate/new_state`, export a `BACKEND`, and register the
entry point. (See `.claude/rules/runner-development.md` in the repo for the checklist.)

## Session locking — `SessionLockMixin` (`runner.py:64`)

`BaseRunner` extends `SessionLockMixin`, which holds a
`WeakValueDictionary[str, anyio.Semaphore]` (idle locks GC away):

- `lock_for(token)` (`:68`) — lazily makes a `Semaphore(1)` keyed `f"{engine}:{value}"`.
- `run_with_resume_lock(...)` (`:80`) — if a resume token is present, wraps the run in the lock so
  **two concurrent resumes of the same session serialize** (a resumed CLI session isn't safe to
  run twice). No token → unlocked.

`run_locked` (`:290`) ties it together: resume path acquires the lock up front; the **fresh path**
(`:298`) acquires the lock lazily on the *first* `StartedEvent` (once the engine reveals its new
session id), closing the race where the id isn't known until the subprocess emits it.

`ResumeTokenMixin` (`:36`, mixed into concrete runners) handles the resume-line text:
`format_resume` (`:40`), `is_resume_line` (`:45`), `extract_resume(text)` (`:48`, scans the
engine's `resume_re` for the last token).

## `_classify_jsonl_event` — the tool-result classifier (`runner.py:184`)

An engine-agnostic function returning `"tool_result"`, `"assistant"`, or `"other"` (conservative:
unknown → `"other"`). It powers the **stuck-after-tool_result detector** (`#322`) by feeding
`stream.last_event_kind` / `last_tool_result_at`. It knows each engine's tool-result and
assistant-turn shapes:

- Claude / Amp: a `user` message whose content list contains a `tool_result` block.
- Pi: `tool_result` / `ToolExecutionEnd`.
- Codex: `item.completed`/`item.updated` with a tool-ish `item.type` and terminal status.
- OpenCode: `ToolUse` with `state.status ∈ {completed, error}`.
- Assistant signals: `assistant` / `message.updated` / `agent_message` clear the latch.

When you add an engine, extend this classifier with its tool-result and assistant shapes; runner
`translate` code stays untouched.

## PTY: base vs subclass

**The base `JsonlSubprocessRunner` has no PTY.** It always spawns with `stdin/stdout/stderr=PIPE`,
sends the payload once, and closes stdin. PTY handling is **entirely a `ClaudeRunner` concern**
(it overrides `run_impl`) — see [03 — Claude Code Integration](./03-claude-integration.md). All
non-Claude engines use the base `run_impl` verbatim: prompt on stdin (or argv), EOF, one-way
JSONL on stdout, no control channel.

## Data-flow summary

```
manage_subprocess spawn (start_new_session=True)          runner.py:1273
   ↓ stdout
iter_bytes_lines (receive_until \n, 10MB)                 utils/streams.py
   ↓ raw line
_iter_jsonl_events  (break once did_emit_completed, #505) runner.py:955
   ↓
_handle_jsonl_line                                        runner.py:849
   ├─ decode_jsonl → translate(...)   [engine hook]  → list[UntetherEvent]
   ├─ update JsonlStreamState + _classify_jsonl_event latch
   ├─ StartedEvent → inject pid, handle_started_event (engine/session/dedup)
   ├─ CompletedEvent → set did_emit_completed, break (terminal latch)
   └─ else append
   ↓ yield  (SessionLockMixin acquires lock on first StartedEvent)
run_impl → on stream close: process_error_events / stream_end_events if not completed
```
