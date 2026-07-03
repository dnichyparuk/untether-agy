# 04 — Gemini CLI Integration

Gemini is the cleanest example of a **non-interactive** engine — it exercises the full
`JsonlSubprocessRunner` template with none of Claude's control-channel complexity. If you want to
see the integration pattern end-to-end, read this doc alongside
[02 — CLI Integration Model](./02-cli-integration-model.md).

Two files:

| File | Role |
|------|------|
| `src/untether/runners/gemini.py` (~575 lines) | `GeminiRunner`, argv, `translate`, terminal handling |
| `src/untether/schemas/gemini.py` (~59 lines) | msgspec structs for `--output-format stream-json` |

Integrates the [Gemini CLI](https://github.com/google-gemini/gemini-cli). Backend id `"gemini"`;
install `npm install -g @google/gemini-cli`.

## CLI invocation

`command()` → `self.gemini_cmd` (default `"gemini"`, `gemini.py:334`).

### `build_args` (`gemini.py:337`)

Exact construction, in order:

```python
run_options = get_run_options()
args = []
if resume is not None:
    if resume.is_continue:  args += ["--resume", "latest"]      # /continue → latest session
    else:                   args += ["--resume", resume.value]  # resume a specific session
args += ["--output-format", "stream-json"]
model = run_options.model if (run_options and run_options.model) else self.model
if model:               args += ["--model", str(model)]
if run_options and run_options.permission_mode:
    args += ["--approval-mode", run_options.permission_mode]
else:                   args += ["--approval-mode", "yolo"]     # default: full auto-approve
if self.skip_trust:     args.append("--skip-trust")
args.append(f"--prompt={self.sanitize_prompt(prompt)}")
```

| Flag | Meaning |
|------|---------|
| `--output-format stream-json` | always — the JSONL mode the schema parses |
| `--resume <value>` / `--resume latest` | resume a specific session / the last one (continue) |
| `--model <m>` | only when set; per-run `run_options.model` overrides configured `self.model` |
| `--approval-mode yolo` | default; overridable via `run_options.permission_mode` |
| `--skip-trust` | passed by default (`skip_trust=True`) — see below |
| `--prompt=<sanitized>` | the prompt as a single argv element (**not** stdin) |

`stdin_payload` returns `None` (`gemini.py:366`) — Gemini takes the prompt on argv, so the base run
loop just closes stdin after spawn.

### Why `--skip-trust` (`#471`)

Gemini CLI refuses to run from any directory not listed in `~/.gemini/trustedFolders.json` — even
under `--approval-mode yolo`. Untether is always headless, so there is no way to interactively
"trust" a folder. `--skip-trust` is passed by default for the same reason `yolo` is. Operators who
want the project-local extension/MCP trust gate enforced can set `[gemini] skip_trust = false`
(`gemini.py:322` comment; validated in `build_runner`).

### Resume line

`format_resume(token)` → `` `gemini --resume <value>` `` (`gemini.py:329`). The same shape is
parsed back out of assistant text by `_RESUME_RE` (`gemini.py:54`), so replying to a message
containing that footer resumes the right Gemini session (via `AutoRouter`).

## The stream-json event schema (`schemas/gemini.py`)

Tagged union on the `type` field, `forbid_unknown_fields=False`:

```
GeminiEvent = Init | Message | ToolUse | ToolResult | GeminiResult | Error   schemas/gemini.py:52
```

| Struct (tag) | Fields |
|--------------|--------|
| `Init` (`init`) | `session_id`, `model`, `timestamp` |
| `Message` (`message`) | `role`, `content`, `delta`, `timestamp` |
| `ToolUse` (`tool_use`) | `tool_name`, `tool_id`, `parameters`, `timestamp` |
| `ToolResult` (`tool_result`) | `tool_id`, `status`, `output`, `timestamp` |
| `GeminiResult` (`result`) | `status`, `stats`, `timestamp` |
| `Error` (`error`) | `message`, `timestamp` |

`decode_event(line)` → `msgspec.json.Decoder(GeminiEvent)` (`schemas/gemini.py:57`).

## `translate` — event mapping

The runner's `translate` (`gemini.py:397`) first builds `meta` (model + a human permission-mode
label: `yolo` → "full access", `auto_edit` → "edit files", read-only omitted) then delegates to the
module-level `translate_gemini_event` (`gemini.py:140`):

| Gemini event | → UntetherEvent | Notes (`gemini.py`) |
|--------------|-----------------|---------------------|
| `Init` | **StartedEvent** (once) | `:150` — captures `session_id`/`model` into state; guarded by `state.emitted_started`; builds `ResumeToken(engine="gemini", value=session_id)`, merges model into meta |
| `ToolUse` | **ActionEvent(started)** | `:179` — requires non-empty `tool_id`; normalizes name via `_TOOL_NAME_MAP` then `tool_kind_and_title`; stores in `state.pending_actions[tool_id]`; `file_change` extracts path into `detail["changes"]` |
| `ToolResult` | **ActionEvent(completed)** | `:204` — pops the pending action; `ok = (status == "success")`; truncates `output` to a 500-char `detail["output_preview"]` |
| `Message` (assistant) | *(state only)* | `:233` — appends `content` to `state.last_text`; user messages ignored; **no event** — this text becomes the final answer |
| `GeminiResult` | **CompletedEvent** | `:243` — sets `saw_result=True`; `answer = last_text`; usage via `_build_usage(stats)`; `ok = (status == "success")`, else `error="gemini result status: <status>"` |
| `Error` | **CompletedEvent(ok=False)** | `:282` — `error = message or "gemini error"`, answer = accumulated text |
| *(unknown)* | *(nothing)* | `:304` — logs `gemini.event.unrecognised` |

### Tool-name normalization (`_TOOL_NAME_MAP`, `gemini.py:58`)

Gemini uses snake_case tool names; Untether maps them to its shared vocabulary before
`tool_kind_and_title`:

```
read_file→read  edit_file→edit  write_file→write  web_search→websearch
web_fetch→webfetch  list_dir→ls  find_files→glob  search_files→grep
```

Path extraction uses `path_keys=("file_path","path","filePath")`.

### Usage (`_build_usage`, `gemini.py:115`)

From `GeminiResult.stats`: `input_tokens`/`output_tokens` (+ `cached` → `cache_read_tokens`) into
`usage["usage"]`; plus `duration_ms` and `total_cost_usd` when present.

## State & lifecycle

`new_state()` → `GeminiStreamState()` (`gemini.py:375`), a `@dataclass(slots=True)`:

```python
pending_actions: dict[str, Action]  # tool_id → started action, drained on tool_result
last_text: str | None               # accumulated assistant text (the eventual answer)
note_seq: int                       # note sequence counter
session_id: str | None              # captured from Init
emitted_started: bool               # guards the single StartedEvent
model: str | None                   # captured from Init
saw_result: bool                    # whether a result event arrived (drives stream-end fallback)
```

`start_run` is a no-op. Terminal handling (base-class hooks):

- `process_error_events(rc, …)` (`gemini.py:456`) — on non-zero rc, message with rc label +
  session + stderr excerpt → note + `CompletedEvent(ok=False)`.
- `stream_end_events(…)` (`gemini.py:486`) — three cases: no session captured → fail; `saw_result`
  true → ok (defensive; the result already emitted its own completed); session but no result →
  fail "finished without a result event".
- `decode_error_events` (`gemini.py:433`) — `msgspec.DecodeError` lines are logged and dropped;
  `invalid_json_events` (`gemini.py:387`) emits a diagnostic note.

## `BACKEND` export

`build_runner(config, config_path)` (`gemini.py:537`) validates `[gemini] model` (str) and
`skip_trust` (bool, default `True`), sets `session_title` to the model name (or `"gemini"`),
returns a `GeminiRunner`.

```python
BACKEND = EngineBackend(id="gemini", build_runner=build_runner,
                        install_cmd="npm install -g @google/gemini-cli")   # gemini.py:570
```

## Contrast with Claude

| Aspect | Gemini | Claude |
|--------|--------|--------|
| Interactivity | none — one-shot | bidirectional control channel |
| stdin | closed after spawn (prompt on argv) | kept open (control responses) |
| PTY | never | legacy PTY mode |
| Approval | broad flags (`yolo` / `--skip-trust`) | per-tool user approval buttons |
| Plan mode / AskUserQuestion | not supported | supported |
| `translate` complexity | ~170 lines, 6 event types | ~800 lines + control-request decision engine |
