# 03 — Claude Code Integration

Claude Code is the **most complex** integration by a wide margin (`runners/claude.py` is ~3900
lines vs ~500 for Gemini). It is the only engine with a **bidirectional control channel** that
lets the user approve/deny tool calls interactively from Telegram, plus plan mode, AskUserQuestion
option buttons, and parent-initiated MCP catalog nudges.

Three files:

| File | Role |
|------|------|
| `src/untether/runners/claude.py` (~3900 lines) | `ClaudeRunner`, subprocess/PTY, control channel, translation |
| `src/untether/schemas/claude.py` (~296 lines) | msgspec structs for every stream-json line + control message |
| `src/untether/telegram/commands/claude_control.py` (~412) | Telegram callback handler (approve/deny/discuss/chat) |

`ClaudeRunner(ResumeTokenMixin, JsonlSubprocessRunner)` (`claude.py:2041`) overrides `command`,
`build_args`, `stdin_payload`, `env`, `translate`, `run_impl`, and the JSONL iteration loop; the
rest comes from the base (see [02](./02-cli-integration-model.md)).

## Two spawn modes

The runner picks one of two modes via `_effective_permission_mode()` (`claude.py:2073`), which
returns `run_options.permission_mode or self.permission_mode` (per-chat override wins):

1. **Interactive / control-channel mode** (a permission mode is set) — no `-p`; stdin stays open
   for the bidirectional control protocol.
2. **Non-interactive mode** (no permission mode) — `-p`; prompt on argv; stdin closed after send.

### `build_args` (`claude.py:2201`, wrapped by `:2267`)

Interactive prelude (`claude.py:2208`):
```python
args = ["--output-format", "stream-json", "--input-format", "stream-json", "--verbose"]
```
Non-interactive prelude (`claude.py:2216`):
```python
args = ["-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose"]
```

Then common flags, in a deliberate order (`claude.py:2230–2260`):
- `*self.extra_args` — user CLI flags (inserted after the I/O prelude, `#407`).
- Resume: `--continue` if `resume.is_continue`, else `["--resume", resume.value]`.
- `["--model", model]` — `run_options.model` overrides `self.model`.
- `["--effort", reasoning]` from `run_options.reasoning`.
- `["--allowedTools", ...]` — comma-joined.
- `--dangerously-skip-permissions` if configured.

Permission-mode tail (`claude.py:2253`):
```python
if effective_mode is not None:
    cli_mode = "plan" if effective_mode == "auto" else effective_mode
    args += ["--permission-mode", cli_mode, "--permission-prompt-tool", "stdio"]
    # prompt is sent via stdin, NOT argv
else:
    args += ["--", prompt]
```

Two subtleties:
- Untether's **`"auto"`** permission mode maps to the CLI's **`plan`** mode; the "auto-approve
  ExitPlanMode" behaviour is enforced by Untether itself, not the CLI.
- **`--permission-prompt-tool stdio`** is what turns on the bidirectional control channel.

**Reserved flags** — `_RESERVED_FLAGS` / `_RESERVED_PREFIXES` (`claude.py:75–95`) block users from
putting `-p`, `--output-format`, `--input-format`, `--resume`/`-r`, `--continue`/`-c`,
`--permission-mode`, `--permission-prompt-tool` into `[claude].extra_args`; `build_runner` raises
`ConfigError` (`claude.py:3596`).

### stdin payload (`claude.py:2276`)

In control-channel mode the prompt is **not** on argv — it's sent as a two-line JSONL blob on
stdin: an `initialize` control_request plus a `user` message:

```python
{"type":"control_request","request_id":f"init_{id(self)}",
 "request":{"subtype":"initialize","hooks":None}}
{"type":"user","session_id":resume.value if resume else "",
 "message":{"role":"user","content":prompt},"parent_tool_use_id":None}
```

Non-interactive mode returns `None` (prompt is on argv).

### env (`claude.py:2305`)

- Env is **allowlist-filtered** via `utils.env_policy.filtered_env` (`#198`).
- Sets `UNTETHER_SESSION=1`; `setdefault`s `CLAUDE_ENABLE_STREAM_WATCHDOG=1`,
  `CLAUDE_STREAM_IDLE_TIMEOUT_MS` (default `300000`), `MCP_TOOL_TIMEOUT=120000`,
  `MAX_MCP_OUTPUT_TOKENS=12000`.
- **Subscription billing**: if `use_api_billing` is not `True`, `ANTHROPIC_API_KEY` is popped
  (`claude.py:2352`) so the CLI uses the logged-in subscription rather than API billing.
- In `run_impl` the env is applied via `wrap_with_env_i(cmd, env)` — `env -i KEY=VAL …` — to block
  rc-files from re-introducing env (`#361`); the subprocess itself gets `env=None`. Args are
  redacted before logging (`redact_env_i_args` strips secrets and the trailing `-- <prompt>`).

## stream-json protocol and `translate`

### Schema (`schemas/claude.py`)

Every line decodes through `decode_stream_json_line` → `msgspec.json.Decoder(StreamJsonMessage)`.
`StreamJsonMessage` is a tagged union on `type` (`:276`):

```
StreamUserMessage | StreamAssistantMessage | StreamSystemMessage | StreamResultMessage
| StreamEventMessage | StreamControlRequest | StreamControlResponse
| StreamControlCancelRequest | StreamRateLimitMessage
```

Content blocks are a nested union `StreamContentBlock` (`:71`): `text`, `thinking`, `tool_use`,
`tool_result`, `server_tool_use` (Anthropic server-side tools like web_search, `#489`),
`advisor_tool_result` (`#489`). Notable resilience:
- `StreamToolResultBlock.content` accepts `str | dict | list[dict] | None` (`#501` — the CLI may
  emit a single content-block object).
- All structs are `forbid_unknown_fields=False`, so new/unknown fields are ignored, not fatal.

Control requests are a union on `subtype` (`schemas/claude.py:204`): `interrupt`, `can_use_tool`
(`tool_name`, `input`, `permission_suggestions`, `blocked_path`), `initialize`,
`set_permission_mode`, `hook_callback`, `mcp_message`, `rewind_files`.

### `translate` mapping (`translate_claude_event`, `claude.py:1130`)

A big `match` over the union:

- **`system` / init** (`:1138`) → collects `cwd/model/tools/permissionMode/output_style/
  apiKeySource/mcp_servers` into `meta`, builds `ResumeToken(engine="claude", value=session_id)`,
  returns `[factory.started(token, title=model, meta=meta)]`. Also calls `_maybe_audit_env`
  (`#361`) and `_capture_mcp_catalog` (`#365`).
- **`assistant`** (`:1174`) — iterates `message.content`:
  - `tool_use` / `server_tool_use` → `_tool_action` (`:476`) builds an `Action`, stored in
    `state.pending_actions[id]`; ExitPlanMode's `input.plan` captured to
    `state.last_exitplanmode_plan` (`#508`). Emits `action_started`.
  - `thinking` → `action_completed(kind="note", …, ok=True)`.
  - `text` → stored as `state.last_assistant_text` (fallback answer); if the session is in
    `_OUTLINE_PENDING` and text ≥ 200 chars, saved as `state.outline_text`. No event emitted.
- **`user`** (`:1262`) — only `tool_result` / `advisor_tool_result` blocks emit: pop the matching
  `pending_actions[tool_use_id]`, then `_tool_result_event` (`:805`) →
  `action_completed(ok=not is_error, detail={tool_use_id, result_preview, result_len, is_error})`.
- **`result`** (`:1353`) — `ok = not is_error`; empty text falls back to `last_assistant_text`; on
  success `_prepend_exitplanmode_plan` re-emits the plan body when the post-approval answer is
  brief (`#510/#508`). Emits a supplementary `started(meta={"complete":"✓ turn complete"})` then
  exactly one `completed(ok, answer, resume, error, usage)`.
- **`rate_limit_event`** (`:1955`, `#349/#518`) → accumulates wait time, emits a start+complete
  `note` pair ("⏳ Rate limited — retrying in Ns").
- **`control_request`** → the decision engine (below). Unrecognized → `[]`.

The runner's own `translate` (`claude.py:3146`) calls the module-level function then registers the
runner in `_ACTIVE_RUNNERS` for any `StartedEvent` with a resume token.

Tool-name → `ActionKind` mapping is shared via `runners/tool_actions.py::tool_kind_and_title`
(invoked with `path_keys=("file_path","path")`) — the same helper Gemini and the others use.

## The interactive control channel

### Session registries (module-level, `claude.py:132–189`)

Because concurrent Claude sessions can share one runner instance, stdin routing is keyed by
session, not stored only on the runner:

| Registry | Maps | Purpose |
|----------|------|---------|
| `_ACTIVE_RUNNERS` | session_id → (runner, ts) | find the runner handling a callback |
| `_SESSION_STDIN` | session_id → stdin stream | write control responses to the right process |
| `_REQUEST_TO_SESSION` | request_id → session_id | route a Telegram callback to its session |
| `_REQUEST_TO_INPUT` | request_id → tool input | CLI requires `updatedInput` in allow responses |
| `_REQUEST_TO_TOOL_NAME` | request_id → tool name | tool-specific deny messages |
| `_HANDLED_REQUESTS` | LRU(200) of request_ids | dedupe double Telegram callbacks (`#197`) |
| `_DISCUSS_COOLDOWN` | session_id → (ts, deny_count) | plan-mode cooldown |
| `_DISCUSS_APPROVED` / `_PLAN_EXIT_APPROVED` / `_OUTLINE_PENDING` | session_id sets | plan-flow state |
| `_PENDING_ASK_REQUESTS` / `_ASK_QUESTION_FLOWS` | request_id → … | AskUserQuestion flow |

`is_session_alive(session_id)` = `session_id in _SESSION_STDIN` (`claude.py:192`).

### PTY vs PIPE (`run_impl`, `claude.py:3254`)

stdin transport is chosen at `claude.py:3321`:
- control-channel mode → `stdin_arg = subprocess.PIPE` (kept open for responses).
- legacy control-capable POSIX → `pty.openpty()` + `tty.setraw(master)`; slave fd passed as stdin,
  master fd stored in `self._pty_master_fd`, slave closed in the parent after spawn.
- otherwise → plain `PIPE`.

After the init+prompt send, stdin is kept open and captured **both** on `self._proc_stdin` and a
local `this_proc_stdin` (`claude.py:3382`) — the local copy is threaded everywhere to survive
concurrent overwrites.

Session-stdin registration happens in `_iter_jsonl_events` (`claude.py:2442`), **not** in
`translate` (because `self._proc_stdin` may be stale): on the first `StartedEvent` with a resume
token, `_SESSION_STDIN[session_id] = session_stdin` (`:2476`). This loop also drains three queues
after **every** line (even lines producing no events, to avoid deadlock): `_drain_auto_approve`,
`_drain_auto_deny`, `_drain_catalog_refresh`. It breaks immediately once `did_emit_completed`.

### Wire format & `write_control_response` (`claude.py:2080`)

```python
async def write_control_response(self, request_id, approved, *, deny_message=None) -> bool
```
- **Approve** → `{"behavior":"allow"}` + `updatedInput` from `_REQUEST_TO_INPUT.pop(id)`; if the
  tool is `ExitPlanMode` or a diff-preview tool, the session is added to `_PLAN_EXIT_APPROVED`.
- **Deny** → `{"behavior":"deny","message": deny_message or "User denied"}`.
- Writes are wrapped as `{"type":"control_response","response":{"subtype":"success","request_id":…,
  "response": inner}}` and routed via `_REQUEST_TO_SESSION[id] → _SESSION_STDIN[session_id]`,
  preferring session stdin, then `self._proc_stdin`, then `self._pty_master_fd`.

Public entry point `send_claude_control_response(request_id, approved, *, deny_message)`
(`claude.py:3628`) is what the Telegram callback calls: dedupes against `_HANDLED_REQUESTS`, finds
the runner in `_ACTIVE_RUNNERS`, calls `write_control_response`, then records the id in the LRU.

**Cleanup** — `_cleanup_session_registries(session_id)` (`claude.py:3734`) is idempotent and
called from `process_error_events`, `stream_end_events`, and the `run_impl` `finally` (covers
cancel). It pops/discards every registry entry for the session.

## Permission modes, auto-approve/deny, plan mode

The `control_request` branch of `translate_claude_event` (`claude.py:1403–1954`) is the decision
engine. **Order matters**:

1. **Auto-approve housekeeping** (`:1444`) — `initialize`, `hook_callback`, `mcp_message`,
   `rewind_files`, `interrupt` → queued to `auto_approve_queue`, no user prompt (security
   rationale for `mcp_message`/`rewind_files` documented inline, `#380`).
2. **Auto-approve non-gated tools** (`:1466`) — `_TOOLS_REQUIRING_APPROVAL =
   {"ExitPlanMode","AskUserQuestion"}`; every other tool is auto-approved *unless* diff_preview is
   on AND tool ∈ `{"Edit","Write","Bash"}` AND the session hasn't already approved a plan (then it
   falls through so the user sees the diff).
3. **AskUserQuestion disabled** (`:1500`) — if `run_opts.ask_questions is False`, auto-deny with
   "proceed with reasonable defaults."
4. **ExitPlanMode in auto mode** (`:1521`) — if `state.auto_approve_exit_plan_mode` (set when
   effective mode == `"auto"`), auto-approve + add to `_PLAN_EXIT_APPROVED`.
5. **ExitPlanMode after post-outline approval** (`:1540`) — if session ∈ `_DISCUSS_APPROVED`,
   clear cooldown, add to `_PLAN_EXIT_APPROVED`, auto-approve.
6. **Discuss cooldown / outline gating** (`:1566`) — the plan-mode UX (below).
7. **Normal interactive request** (`:1690`) — build `warning_text` (tool + key input params +
   optional `_format_diff_preview`), register the request, emit a `warning`-kind action with an
   inline keyboard.

### Interactive approval buttons (`:1690`)

- Base row: **✅ Approve** / **❌ Deny** with `callback_data="claude_control:approve|deny:<request_id>"`.
- ExitPlanMode adds **📋 Pause & Outline Plan** → `claude_control:discuss:<request_id>`.
- Requests older than `CONTROL_REQUEST_TIMEOUT_SECONDS` (300 s) are auto-denied.

### Plan mode — "Pause & Outline Plan" + progressive cooldown

The distinctive Untether feature. When ExitPlanMode arrives and the user clicks **📋 Pause &
Outline Plan**, Untether denies it with a message asking Claude to write an outline first, and
starts a cooldown:

- `_cooldown_seconds(count) = min(30*count, 120)` (`claude.py:3683`).
- `set_discuss_cooldown` increments the count and adds the session to `_OUTLINE_PENDING`.
- `check_discuss_cooldown` returns an escalation message while inside the window; on expiry it
  zeroes the timestamp but **preserves the count** so the next retry escalates further.
- Rapid `ExitPlanMode` retries within the window are auto-denied.

Once Claude writes an outline (`text ≥ 200` chars while `_OUTLINE_PENDING`), the control request is
**held open** and synthetic **Approve Plan / Deny / Let's discuss** buttons are rendered (using the
real request_id when the request is live, or a `da:<session_id>` pseudo-id after an auto-deny). The
full outline text rides in `detail["outline_full_text"]` so the bridge sends it untruncated.

- **Approve Plan** → session added to `_DISCUSS_APPROVED`; the next ExitPlanMode auto-approves.
- **Deny** → cooldown cleared, no approval flag.
- **Let's discuss** → the control request is **never answered** (held open) so Claude stays alive
  while the user reads; the 300 s safety timeout eventually cleans up stale held requests.

### AskUserQuestion flow (`:1849`)

Claude's `AskUserQuestion` is never auto-approved. Untether parses `input.questions[]`, sets
`warning_text = "❓ Question 1 of N: …"`, creates an `AskQuestionState` in `_ASK_QUESTION_FLOWS`,
and renders up to 4 **option buttons** (`aq:opt:N`) plus **Other (type reply)** (`aq:other`).
Answering:
- `answer_ask_question_with_options(request_id)` (`claude.py:3826`) — **approve** with
  `updatedInput["answers"]` populated from the chosen option(s); supports sequential multi-question
  flows.
- `answer_ask_question(request_id, answer)` (`claude.py:3792`) — for a free-text reply, **deny**
  with the user's text embedded so Claude reads it as the answer.

## Telegram side — `claude_control.py`

`ClaudeControlCommand` (id `"claude_control"`, `answer_early=True`). `early_answer_toast`
(`:83`) clears the button spinner immediately with `_EARLY_TOASTS` (`approve→"Approved"`,
`deny→"Denied"`, `discuss→"Outlining plan..."`, `chat→"Let's discuss..."`). `handle()` parses
`action:request_id`:

- **discuss** (`:126`) — deny with `_DISCUSS_DENY_MESSAGE`, `set_discuss_cooldown`, send "📋 Asked
  Claude Code to outline the plan", store the ref in `_DISCUSS_FEEDBACK_REFS`.
- **chat** → `_handle_chat` (`:313`) — the "Let's discuss" hold-open path.
- **approve/deny** (`:171`) — the `da:` synthetic path toggles `_DISCUSS_APPROVED`/outline cleanup;
  the real-request path picks `_EXIT_PLAN_DENY_MESSAGE` vs `_DENY_MESSAGE` by tool name, calls
  `send_claude_control_response`, clears cooldown, deletes outline messages.

## Parent-initiated control requests — MCP catalog refresh (`#365`)

Untether can also **push** control_requests *to* the CLI (not just respond). On a `tool_result`,
if `state.notify_catalog_refresh` is on and the debounce interval elapsed, it enqueues
`f"ut_catalog_refresh_{session}_{seq}"` (`claude.py:1338`). `_drain_catalog_refresh` (`claude.py:2597`)
writes, fire-and-forget:

```python
{"type":"control_request","request_id":req_id,"request":{"subtype":"mcp_status"}}
```

Untether registers no pending response and ignores the eventual `control_response` — the goal is
just to nudge the CLI's MCP catalog. The `ut_<feature>_<session>_<seq>` namespace avoids colliding
with Claude's own `req_*` ids. Companion observability: `_capture_mcp_catalog` (`claude.py:1066`)
logs `catalog_staleness.detected` once per (session, server, status) when `system.init` reports a
non-`connected` MCP server.

## `BACKEND` export

`build_runner(config, config_path)` (`claude.py:3566`) reads `[claude]`: `model`, `allowed_tools`
(default `["Bash","Read","Edit","Write"]`), `dangerously_skip_permissions`, `use_api_billing`,
`permission_mode`, validated `extra_args`. It resolves the binary via `shutil.which("claude")` and
returns a `ClaudeRunner`.

```python
BACKEND = EngineBackend(id="claude", build_runner=build_runner,
                        install_cmd="npm install -g @anthropic-ai/claude-code")   # claude.py:3620
```
