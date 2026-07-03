# 05 — Other Engines (Codex, OpenCode, Pi, Amp, Mock)

All of these follow the same template as Gemini (see [02](./02-cli-integration-model.md) and
[04](./04-gemini-integration.md)): subclass `JsonlSubprocessRunner` + `ResumeTokenMixin`, override
`command/build_args/translate/new_state` + terminal hooks, and export a `BACKEND`. **None of them
is interactive** — only Claude has a control channel. Approval is handled by passing broad
auto-approve flags at spawn time, with Untether's own permission layer as the primary control.

Per-run overrides (model, reasoning, permission mode) reach each `build_args`/`translate` through
`get_run_options()` (`runners/run_options.py:45`) — a `ContextVar` populated per run.

## Comparative table

| Engine | Command + key flags | Prompt via | Event schema | Distinctive |
|--------|--------------------|------------|--------------|-------------|
| **Codex** | `codex … exec --json --skip-git-repo-check --color=never`; `--model`, `-c model_reasoning_effort=<r>`, `--ask-for-approval untrusted\|never`; resume `resume --last -` / `resume <v> -` | **stdin** (`-`) | thread/turn/item union (richest) | reasoning effort; `--profile`; unknown-item fallback |
| **OpenCode** | `opencode run --format json`; `--continue` / `--session <v>`; `--model` | after `--` (argv) | `step_start/step_finish/tool_use/text/error` | reads its own config for default model; per-step cost accumulation |
| **Pi** | `pi --print --mode json`; `--provider`, `--model`; `--continue` / `--session <v>` | trailing argv | large: session/agent/message/turn/tool/compaction | **self-generates the session file**; `--provider`; env allowlist |
| **Amp** | `amp -x <prompt> --stream-json`; `threads continue <v>`; `--mode <mode>`; `--dangerously-allow-all` | `-x` argv (opt. `--stream-json-input`) | **Claude-compatible** system/user/assistant/result | uses `--mode` not `--model`; subagent tracking |
| **Mock** | *(no subprocess)* | — | — | test-only; replays scripted events |

---

## Codex — `src/untether/runners/codex.py`

OpenAI's Codex CLI. Command `codex` (`:479`); install `npm install -g @openai/codex`.

`build_args` (`:482`) prepends `self.extra_args` (default `["-c","notify=[]"]`), then `--model` /
`-c model_reasoning_effort=<r>` from run options, approval `--ask-for-approval untrusted` (safe
mode) else `never`, then `exec --json --skip-git-repo-check --color=never`, then resume (`resume
--last -` for continue, `resume <value> -`, else trailing `-`). **The prompt is written on stdin**
(the trailing `-`). `find_exec_only_flag` (`:59`) blocks user `extra_args` from colliding with
Untether-managed flags.

Event model (`schemas/codex.py`) is the richest: `ThreadEvent` covers `thread.started` /
`turn.started|completed|failed` / `item.started|updated|completed` / `error`, and items are a
nested union (`agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`,
`collab_tool_call`, `web_search`, `todo_list`, `error`) with an `UnknownItem` manual fallback
(`schemas/codex.py:211`). `translate` (`:427`) uses a stored `EventFactory` + Python `match`:
`thread.started`→started; item events map by phase to action started/updated/completed;
`turn.completed`→`completed_ok`; `turn.failed`→`completed_error`. Notable: `agent_message` phase
distinguishes `commentary` (a note) from `final_answer` (the answer, chosen by
`_select_final_answer`), and `Reconnecting... N/M` stream errors become progress notes
(`_parse_reconnect_message`, `:69`).

## OpenCode — `src/untether/runners/opencode.py`

`sst/opencode`. Command `opencode` (`:423`); install `npm install -g opencode-ai@latest`.

`build_args` (`:426`): `run --format json`; resume `--continue` / `--session <v>`; `--model`; prompt
passed after `--` as a positional. Session ids look like `ses_XXXX`.

Event model (`schemas/opencode.py`): `step_start` / `step_finish` / `tool_use` / `text` / `error`,
each carrying `sessionID` + a loose `part: dict`. `translate` (`:219`): `step_start`→StartedEvent
(once); `tool_use` inspects `part.state.status` (`completed`/`error`/else) to emit
completed-ok/fail/started; `text`→accumulate; `step_finish` with `reason=="stop"`→CompletedEvent,
accumulating cost/tokens across steps (`_accumulate_step_cost`, `:109`); `error`→fail. Distinctive:
reads OpenCode's own `~/.config/opencode/opencode.json` for the default model
(`_read_opencode_default_model`, `:632`) so the footer shows it without an untether.toml override,
and falls back to `last_tool_error` as the answer when no text is emitted.

## Pi — `src/untether/runners/pi.py`

`@mariozechner/pi-coding-agent`. Command `pi` (`:443`); install `npm install -g
@mariozechner/pi-coding-agent`; `cli_cmd="pi"`.

`build_args` (`:446`): `[*extra_args] --print --mode json`; optional `--provider`; `--model`; then
`--continue` / `--session <state.resume.value>`; prompt as a trailing positional. **`--provider`
is a first-class config option.**

Event model (`schemas/pi.py`) is the largest vocabulary: `session`, `agent_start/end`,
`message_start/update/end`, `turn_start/end`, `tool_execution_start/update/end`,
`auto_compaction_start/end`, `auto_retry_start/end`. `translate` (`:190`): `session`→StartedEvent;
tool exec start/end→action started/completed; `message_end` (assistant) accumulates text/usage and
emits a **supplementary StartedEvent** carrying the real model+provider once (`#225`); `agent_end`
→CompletedEvent; compaction events surface as notes.

**Two things make Pi unusual:**
1. Pi **generates its own session file** for fresh runs — `new_state` (`:492`) calls
   `_new_session_path` (`:612`), a UUID-named `.jsonl` under `~/.pi/agent/sessions/` (mode 0700) —
   rather than receiving a session id from the CLI. It then "promotes" the runtime session id from
   the `session` event into a short token (`_maybe_promote_session_id`, `:112`).
2. It is the **only runner overriding `env()`** (`:478`) — applies an env allowlist filter
   (`#198`) so the subprocess doesn't inherit the full parent environment.

## Amp — `src/untether/runners/amp.py`

Sourcegraph Amp. Command `amp` (`:337`); install `npm install -g @sourcegraph/amp`.

`build_args` (`:340`): resume via `threads continue <v>` (non-continue only); optional
`--dangerously-allow-all` (default off); **`--mode <mode>`** (Amp uses *mode* — deep/free/rush/smart
— not `--model`; a run-option "model" override is treated as a mode); `--stream-json`; optional
`--stream-json-input`; prompt via `-x <prompt>`.

Event model (`schemas/amp.py`) is **Claude-Code-compatible** stream-json: `system` (subtype init),
`user`, `assistant`, `result`; session ids `T-<uuid>`. `translate` (`:150`): `system/init`
→StartedEvent; `assistant` content blocks — `text` accumulates, `tool_use`→action started (tracks
`parent_tool_use_id` for subagents); `user` `tool_result` blocks→action completed;
`result`→CompletedEvent with accumulated Anthropic-style usage (incl. cache tokens) and
`total_cost_usd`. Because the wire format mirrors Claude's, `_classify_jsonl_event` handles Amp and
Claude with the same `user`/`tool_result` logic (see [02](./02-cli-integration-model.md)).

## Mock — `src/untether/runners/mock.py`

**Not a real engine and not a subprocess** — subclasses `SessionLockMixin, ResumeTokenMixin,
Runner` directly (`:70`); no CLI, no schema, no `build_args`/`translate`, **no `BACKEND` export**.
Used to exercise the event pipeline deterministically in tests:

- **`MockRunner`** (`:70`) replays a fixed list of pre-built `UntetherEvent`s between a synthesized
  `StartedEvent` and `CompletedEvent`, acquiring a session lock via `lock_for(token)`.
- **`ScriptRunner`** (`:133`) drives a scripted step machine — `Emit | Advance | Sleep | Wait |
  Return | Raise | ErrorReturn` — with a virtual clock, sleeps, event-waits, and explicit
  ok/error terminal returns; records `calls` for assertions.

## Cross-engine invariants

- **Resume**: every real engine emits `ResumeToken(engine=<id>, value=<session>)`, renders a
  `` `<cmd> …token` `` hint via `format_resume`, and parses it back with a module-level
  `_RESUME_RE`. `is_continue` selects the CLI's "latest/last/continue" mechanism (Gemini `--resume
  latest`, Codex `resume --last`, OpenCode `--continue`, Pi `--continue`, Amp skips
  `threads continue`).
- **Answer**: all accumulate assistant text into a `last_text`-style field surfaced as
  `CompletedEvent.answer`.
- **Footer meta**: each `translate` computes `meta={"model": …}` (plus provider/mode/permission
  label per engine) for the Telegram footer.
- **Interactivity**: Gemini, Codex, OpenCode, Pi, Amp are one-shot non-interactive subprocesses;
  approval is via broad flags (`--approval-mode yolo`, `--ask-for-approval never`,
  `--dangerously-allow-all`). Only Claude gates tools interactively.
