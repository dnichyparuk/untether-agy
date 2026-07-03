# Antigravity Runner — Implementation Spec

**Engine id:** `antigravity` · **CLI:** `agy` · **Target CLI version:** 1.0.16+ · **Status:** ready to implement

This spec defines a new Untether engine runner for Google's **Antigravity CLI (`agy`)**. It is
grounded in the empirically-verified CLI contract in
[`agy-probes/EXPERIMENT-REPORT.md`](./agy-probes/EXPERIMENT-REPORT.md) and follows the runner
substrate documented in [`02-cli-integration-model.md`](./02-cli-integration-model.md), closely
mirroring the Gemini runner ([`04-gemini-integration.md`](./04-gemini-integration.md)) and
`.claude/rules/runner-development.md`.

> **Capability tier:** *non-interactive, structured-result.* `agy` emits a **single JSON result
> envelope** at completion (not a streaming event feed). The runner therefore produces a real
> resume token, the answer, and token usage — but **no live `ActionEvent` progress** and **no
> interactive approval** (no control channel). This is close to Gemini-runner parity minus
> streaming and USD cost.

---

## 1. Scope

**In scope (v1):**
- Headless one-shot runs via `agy -p "<prompt>" --output-format json`.
- `StartedEvent` with a real `ResumeToken` (from `conversation_id`) + model meta.
- `CompletedEvent` with the answer, ok/error from `status`, and token usage.
- Resume: `/continue` (`--continue`) and per-session resume (`--conversation <id>`).
- Model selection, sandbox, auto-approve, print-timeout, extra dirs, extra args (config).

**Out of scope (v1) — see §13/§16:**
- Live `ActionEvent` progress (tool/file activity) — envelope is terminal-only.
- Interactive permission buttons, plan mode, `ExitPlanMode`, AskUserQuestion (no control channel).
- USD cost / budget enforcement (envelope carries tokens only, no `total_cost_usd`).
- Auto-continue (Claude-specific), MCP catalog observability (#365).

---

## 2. Prerequisites

- `agy` on `PATH` (`curl -fsSL https://antigravity.google/cli/install.sh | bash`).
- **Pre-authenticated on the host.** `agy` auths via the OS keyring → Google OAuth; there is **no
  API-key env var**. Because Untether runs headless, the operator must complete an interactive
  `agy` login once per host so the daemon inherits the saved session. Document this as a hard
  prerequisite; a runner with no session will fail every run.
- CLI ≥ 1.0.16 (the version whose JSON envelope this spec targets).

---

## 3. Architecture & base-class choice

Use `JsonlSubprocessRunner` **unchanged** — despite `agy` not being a streaming engine. The result
envelope is a **single JSON object on one physical line**, so it fits the base run loop perfectly:
one line → `decode_jsonl` → `translate` → `[StartedEvent, CompletedEvent]`. No PTY, no override of
`run_impl`, no custom spawn.

```
AntigravityRunner(ResumeTokenMixin, JsonlSubprocessRunner)   # mirrors GeminiRunner
```

Contract compliance ([02 §3-event contract](./02-cli-integration-model.md)):
- `translate()` returns `[StartedEvent, CompletedEvent]` for the single envelope line.
- The base `_handle_jsonl_line` emits `StartedEvent` first (acquires the session lock on the fresh
  path), then `CompletedEvent` sets `did_emit_completed` and breaks → exactly one terminal event.
- If the process exits non-zero or produces no envelope, the base fallbacks
  (`process_error_events` / `stream_end_events`) synthesize the terminal `CompletedEvent`.

---

## 4. CLI invocation contract

`command()` → `self.agy_cmd` (default `"agy"`).

`build_args(prompt, resume, *, state)` builds argv (order mirrors `gemini.py:337`):

| Arg | When | Source |
|-----|------|--------|
| `-p <prompt>` | always | the (preamble-prefixed) prompt |
| `--output-format json` | always | **the linchpin** — yields the structured envelope |
| `--model <name>` | model set | `run_options.model` overrides `self.model`; full display name e.g. `"Gemini 3.1 Pro (High)"` |
| `--continue` | `resume.is_continue` | `/continue` (machine-global most-recent — see §7 caveat) |
| `--conversation <value>` | resume, not continue | `resume.value` = a prior `conversation_id` |
| `--sandbox` | `self.sandbox` | run with terminal restrictions |
| `--dangerously-skip-permissions` | `self.auto_approve` | headless auto-approve (no control channel; see §9) |
| `--print-timeout <dur>` | configured | e.g. `10m` (agy default `5m0s`) |
| `--add-dir <path>` | per extra dir | repeatable |
| `*self.extra_args` | configured | escape hatch |

`stdin_payload()` → `None` (prompt is on argv; base closes stdin so `agy` never blocks).

**Reserved flags** (reject in `[antigravity] extra_args`, mirror `claude.py:75`): `-p`, `--print`,
`--prompt`, `--output-format`, `--continue`, `-c`, `--conversation`, `--model`. This prevents users
from breaking the I/O contract.

Example (verified):
```bash
agy -p "reply with the single word OK" --output-format json --model "Gemini 3.5 Flash (Low)"
# → {"conversation_id":"…","status":"SUCCESS","response":"OK\n","duration_seconds":1.24,
#    "num_turns":1,"usage":{"input_tokens":16795,"output_tokens":6,"thinking_tokens":0,"total_tokens":16801}}
```

---

## 5. Data model — `src/untether/schemas/antigravity.py`

The envelope is **untagged** (no `type` field), so decode a single struct directly (contrast with
Gemini's tagged union). All fields optional + `forbid_unknown_fields=False` for forward-compat.

```python
"""Msgspec model for `agy --output-format json` result envelope (v1.0.16)."""
from __future__ import annotations
import msgspec


class AntigravityUsage(msgspec.Struct, forbid_unknown_fields=False):
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


class AntigravityResult(msgspec.Struct, forbid_unknown_fields=False):
    conversation_id: str | None = None
    status: str | None = None            # observed: "SUCCESS" (failure shape unconfirmed, §11)
    response: str | None = None          # the assistant answer text
    duration_seconds: float | None = None
    num_turns: int | None = None
    usage: AntigravityUsage | None = None
    error: str | None = None             # speculative — capture a real failure to confirm


_DECODER = msgspec.json.Decoder(AntigravityResult)


def decode_result(line: str | bytes) -> AntigravityResult:
    return _DECODER.decode(line)
```

---

## 6. Event translation — `src/untether/runners/antigravity.py`

`new_state()` → a tiny state object (no streaming to track):

```python
@dataclass(slots=True)
class AntigravityStreamState:
    session_id: str | None = None
    emitted_started: bool = False
    model: str | None = None
    saw_result: bool = False
    note_seq: int = 0
```

`translate(data, *, state, resume, found_session)` maps the one envelope to two events:

```python
def translate_antigravity_result(evt, *, title, state, meta):
    out = []
    state.saw_result = True
    conv = evt.conversation_id or ""
    resume = ResumeToken(engine="antigravity", value=conv)
    # StartedEvent (once) — carries the real resume token + model meta
    if not state.emitted_started:
        state.emitted_started = True
        out.append(StartedEvent(engine="antigravity", resume=resume, title=title, meta=meta or None))
    # CompletedEvent — answer/ok/usage from the envelope
    ok = (evt.status or "").upper() == "SUCCESS"
    answer = evt.response or ""
    usage = _build_usage(evt)         # see below
    error = None if ok else (evt.error or f"agy status: {evt.status}")
    out.append(CompletedEvent(engine="antigravity", ok=ok, answer=answer,
                              resume=resume, usage=usage, error=error))
    return out
```

Usage mapping (mirrors `gemini.py:115` shape; **no USD**, so cost budgets won't populate):

```python
def _build_usage(evt):
    u = evt.usage
    out = {}
    if u is not None:
        out["usage"] = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        if u.thinking_tokens is not None:
            out["usage"]["thinking_tokens"] = u.thinking_tokens
    if evt.duration_seconds is not None:
        out["duration_ms"] = int(evt.duration_seconds * 1000)
    if evt.num_turns is not None:
        out["num_turns"] = evt.num_turns
    return out or None
```

The runner's `translate()` wrapper builds `meta` (model + permission-mode label) exactly like
`gemini.py:397`, honouring `get_run_options()` overrides:

```python
def translate(self, data, *, state, resume, found_session):
    model = (get_run_options() and get_run_options().model) or self.model
    meta = {"model": str(model)} if model else None
    if self.auto_approve:
        meta = {**(meta or {}), "permissionMode": "full access"}
    elif self.sandbox:
        meta = {**(meta or {}), "permissionMode": "sandbox"}
    return translate_antigravity_result(data, title=self.session_title, state=state, meta=meta)

def decode_jsonl(self, *, line): return antigravity_schema.decode_result(line)
```

---

## 7. Session & resume

- **Resume token:** `ResumeToken(engine="antigravity", value=conversation_id)`. Verified: the
  envelope surfaces `conversation_id` and `--conversation <id>` round-trips (recalls prior context,
  `num_turns` increments).
- **`format_resume(token)`** → `` `agy --conversation <value>` ``.
- **`resume_re`** (module-level, mirror `gemini.py:54`):
  ```python
  _RESUME_RE = re.compile(r"(?im)^\s*`?agy\s+--conversation\s+(?P<token>[0-9a-f-]{8,})`?\s*$")
  ```
  so replying to a message with that footer resumes the right conversation (via `AutoRouter`).
- **`/continue`** → `ResumeToken(is_continue=True)` → `--continue`.
- **Session locking:** works via `SessionLockMixin` on the fresh path (lock acquired on first
  `StartedEvent`, which now carries a real id).

**⚠ Concurrency caveat:** `--continue` resumes the host's **most-recent conversation across all
chats** (confirmed in help + Issue #7). In a multi-chat deployment `/continue` can therefore
resume the *wrong* chat's conversation. **Mitigation:** prefer explicit per-session resume
(`--conversation <id>`, which the runner emits in every footer) and gate `/continue` behind a
config flag `resume_continue_enabled` (default `false`) or surface a one-time warning.

---

## 8. Config — `[antigravity]`

`build_runner(config, config_path)` validates and constructs the runner (mirror `gemini.py:537`):

| Key | Type | Default | Maps to |
|-----|------|---------|---------|
| `model` | str | none | `--model` (full display name, e.g. `"Gemini 3.1 Pro (High)"`) |
| `sandbox` | bool | `false` | `--sandbox` |
| `auto_approve` | bool | `true` | `--dangerously-skip-permissions` (see §9) |
| `print_timeout` | str | `"5m"` | `--print-timeout` |
| `add_dirs` | list[str] | `[]` | repeated `--add-dir` |
| `extra_args` | list[str] | `[]` | appended (reserved-flag checked) |
| `resume_continue_enabled` | bool | `false` | allow `/continue` despite the machine-global caveat |

Env handling: reuse the allowlist policy (`utils/env_policy.py`) like the Pi runner (`pi.py:478`).
No API key to inject; `agy` reads the keyring.

---

## 9. Permissions, approval & security

`agy` has **no control channel**, so Untether's interactive Approve/Deny buttons **cannot** gate
`agy`'s tools (unlike Claude). Like the other non-interactive engines, approval is decided at spawn:

- **`--dangerously-skip-permissions`** (`auto_approve=true`, default) — full auto-approve, analogous
  to Gemini's `--approval-mode yolo`. Lets `agy` edit files / run commands unattended.
- **`--sandbox`** — terminal-restricted execution; combine with auto-approve for a safer default.
- Granular permission *modes* (`always-proceed`, `strict`, `proceed-in-sandbox`) live in `agy`'s own
  `settings.json` and are out of band for v1.

**Security note (surface in docs + `[antigravity]` comments):** auto-approve grants the agent
unattended code execution. Recommend `sandbox=true` for shared hosts, and document that
`ExitPlanMode`/interactive gating is unavailable for this engine. The agent preamble
([06](./06-orchestration-and-transport.md)) still applies (Telegram context, `.untether-outbox/`).

---

## 10. Error handling & edge cases

| Case | Handling |
|------|----------|
| Process exits non-zero | `process_error_events(rc)` → note + `CompletedEvent(ok=False)` with rc label + stderr excerpt (mirror `gemini.py:456`) |
| No envelope emitted (empty stdout) | `stream_end_events` → `CompletedEvent(ok=False, error="agy produced no result envelope")` (defends the non-TTY-empty edge even though not seen on Linux 1.0.16) |
| Non-JSON line (warning noise) | `decode_error_events` drops `msgspec.DecodeError` lines (log `jsonl.msgspec.invalid`) like `gemini.py:433` |
| `status != "SUCCESS"` | `ok=False`, `error` from `status`/`error` field (**failure shape unconfirmed — verify on host**, §11 of the experiment report) |
| Huge response | JSON is one physical line; base line cap is 10 MB — a response > ~10 MB would truncate. Acceptable; note as a known limit |
| `--print-timeout` exceeded | `agy` exits; treated as process error |

---

## 11. Meta / footer

`StartedEvent.meta`:
- `model` → footer model name.
- `permissionMode` → `"full access"` (auto-approve) or `"sandbox"`.

`CompletedEvent.usage` carries tokens (`input`/`output`/`thinking`) + `duration_ms` + `num_turns`.
**No `total_cost_usd`** → `cost_tracker` won't compute cost/budget for this engine; document that
budgets are token-only for `agy` (or unsupported). `reasoning`/effort run-option does **not** map —
`agy` bakes the tier into the model name (`… (High)` / `(Low)` / `(Thinking)`), not a separate flag.

---

## 12. Feature support matrix (runner-level)

| Untether feature | Antigravity runner | Note |
|---|---|---|
| Answer delivery | ✅ | `response` |
| Resume (`/continue`) | 🟡 | works but machine-global — gated by config |
| Resume (per-session token) | ✅ | `--conversation <conversation_id>` |
| Model selection / footer | ✅ | `--model`, meta |
| Token usage | ✅ | in `usage` |
| USD cost / budgets | ❌ | no `total_cost_usd` |
| Live progress (`ActionEvent`s) | ❌ | terminal-only envelope |
| Interactive approval / plan mode / AskUserQuestion | ❌ | no control channel |
| Auto-continue (#34142) | ➖ | Claude-specific |
| MCP catalog observability (#365) | ❌ | Claude-specific |
| Preamble / outbox / worktrees / env allowlist | ✅ | engine-agnostic |
| Sandbox / auto-approve | ✅ | `--sandbox` / `--dangerously-skip-permissions` |

---

## 13. Registration

`src/untether/runners/antigravity.py` (end of file):
```python
BACKEND = EngineBackend(
    id="antigravity",
    build_runner=build_runner,
    install_cmd="curl -fsSL https://antigravity.google/cli/install.sh | bash",
)
```
`pyproject.toml`:
```toml
[project.entry-points."untether.engine_backends"]
antigravity = "untether.runners.antigravity:BACKEND"
```

---

## 14. Files to add / touch

| File | Action |
|------|--------|
| `src/untether/runners/antigravity.py` | new — `AntigravityRunner`, `translate_antigravity_result`, `build_runner`, `BACKEND` |
| `src/untether/schemas/antigravity.py` | new — `AntigravityResult`/`AntigravityUsage` + `decode_result` |
| `pyproject.toml` | add entry point |
| `docs/reference/runners/antigravity/runner.md` | new — CLI/protocol reference (promote this spec) |
| `docs/reference/runners/antigravity/*-cheatsheet.md` | new — the envelope schema (from the experiment report) |
| `tests/test_antigravity_runner.py` | new — see §15 |
| `tests/test_build_args.py` | extend — antigravity argv cases |
| `CLAUDE.md`, `README.md`, `AGENTS.md` | mention the new engine (per `.claude/rules/context-quality.md`) |
| `runner.py:_classify_jsonl_event` | optional — no tool_result stream, so leaving envelope as `"other"` is correct; no change needed |

---

## 15. Testing plan

Mirror `tests/test_gemini_runner.py`. Use a **fake `agy`** shell script emitting a known envelope
(no real CLI/auth/quota in unit tests):

```bash
#!/bin/bash
echo '{"conversation_id":"test-conv-123","status":"SUCCESS","response":"hello",
"duration_seconds":0.5,"num_turns":1,"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}'
```

Unit tests:
- `build_args`: fresh / `--continue` / `--conversation <id>` / `--model` / `--sandbox` /
  `--dangerously-skip-permissions` / `print_timeout` / reserved-flag rejection.
- `translate`: SUCCESS envelope → exactly `[StartedEvent, CompletedEvent]`; resume token = `conversation_id`;
  answer = `response`; usage tokens mapped; `duration_ms`, `num_turns` present.
- Failure: `status != "SUCCESS"` → `ok=False`, `error` set.
- `stream_end_events`: empty output → `ok=False` completed.
- `process_error_events`: rc≠0 → `ok=False` completed with rc label.
- `format_resume` / `extract_resume` round-trip via `_RESUME_RE`.
- 3-event contract assertion (`tests/testing-conventions`): `events[0]` Started, `events[-1]` Completed.

Integration (per `.claude/rules/runner-development.md`, U1–U4/U6/U7 against `@untether_dev_bot`):
one-shot answer, `/continue`, per-session resume via reply, model override, sandbox run. Keep
prompts tiny (token discipline). The probe harness (`agy-probes/`) can pre-flight the host.

---

## 16. Not supported now / future work

- **Live progress (`ActionEvent`s).** The JSON envelope is terminal-only. *Possible future:* tail
  `agy`'s `--log-file` or the SQLite conversation store to synthesize coarse progress
  (`agent_state` transitions exist in the statusline hook schema — see experiment report §3.7).
  Fragile/undocumented; defer.
- **USD cost / budgets.** Needs a token→price map (agy pricing) since no `total_cost_usd`.
- **Interactive approval / plan mode / AskUserQuestion.** Requires a control channel `agy` doesn't
  expose. Revisit only if Google ships one.
- **Streaming envelope.** If a future `agy` emits JSONL events (like Gemini's `stream-json`), add a
  tagged schema + per-event `translate` for real `ActionEvent`s → near-full Gemini parity.

---

## 17. Open questions — verify on host before/at implementation

1. **Failure envelope shape** — force a real failure (quota, denied tool, malformed request) and
   record whether `status` carries a non-SUCCESS code and/or an `error` field, and the exit code.
2. **`-p` prompt binding** — confirm `-p` consumes the prompt as its value vs a positional (observed
   as value form; keep the fake-CLI test faithful to whichever the real CLI uses).
3. **Very large / multi-part responses** — confirm the envelope stays single-line JSON (it should;
   newlines are `\n`-escaped) and does not stream multiple objects.
4. **Behaviour without `--dangerously-skip-permissions`** in `-p` mode when a tool needs approval —
   does it hang to `--print-timeout`, auto-deny, or error? Decide the safe default accordingly.
5. **Auth expiry** — how `agy` signals an expired keyring session in `--output-format json` (so the
   runner can surface a clear "re-authenticate `agy` on host" error).

---

## 18. Implementation checklist

- [ ] `schemas/antigravity.py` (`AntigravityResult`, `AntigravityUsage`, `decode_result`)
- [ ] `runners/antigravity.py` (`AntigravityRunner`, translate, `build_runner`, `BACKEND`)
- [ ] `pyproject.toml` entry point
- [ ] `tests/test_antigravity_runner.py` + extend `test_build_args.py`
- [ ] `docs/reference/runners/antigravity/` reference docs
- [ ] Update `CLAUDE.md` / `README.md` / `AGENTS.md` engine lists (context-quality rule)
- [ ] `uv run pytest tests/test_antigravity_runner.py -x` + `uv run ruff check src/`
- [ ] Resolve §17 open questions on a host with `agy` authenticated
- [ ] Integration tests via `@untether_dev_bot` before release
```
