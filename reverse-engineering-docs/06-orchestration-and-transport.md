# 06 — Orchestration, Transport & Config

This is the glue between "a Telegram message arrived" and "spawn the right engine and stream it
back." Read [01 — Architecture Overview](./01-architecture-overview.md) first for the call chain;
this doc drills into the orchestrator, engine selection, the agent preamble, auto-continue, the
transport, and the config model.

## The orchestrator: `runner_bridge.handle_message` (`runner_bridge.py:2873`)

`handle_message` is the single function that turns a resolved prompt into a completed run. Roughly:

1. Resolve the runner (via the router / `TransportRuntime`) and the `ProgressEdits` renderer.
2. Seed progress meta — including `trigger_source` (⏰ `cron:`/`at:` or ⚡ `webhook:` icon,
   `runner_bridge.py:2908`) and per-run watchdog thresholds (`:2968–2997`).
3. `_strip_resume_lines` the incoming text, then **`_apply_preamble`** it (`:2901`).
4. `run_runner_with_cancel(...)` (`:2704`) drives `runner.run(prompt, resume_token)` and feeds each
   `UntetherEvent` to `ProgressEdits`.
5. On completion: render the answer, append the resume footer, clean up ephemeral messages, deliver
   `.untether-outbox/` files, track cost.
6. Evaluate **auto-continue** (below) and possibly recurse.

`ProgressEdits` (`runner_bridge.py:934`) owns the live-editing loop (`run` `:1016`, `on_event`
`:2477`), a heartbeat (`:1030`), and the stall watchdogs (`_stall_monitor` `:1132`). It is what
converts the stream of events into a single continuously-edited Telegram message.

## Engine selection per message

Selection precedence (highest wins):

1. **Resume token engine** — a resume pins its own engine (`run_job`, `loop.py:1730`). Replying to
   a message with a `` `gemini --resume …` `` footer forces Gemini.
2. **Explicit `engine_override`** — e.g. an `/engine`-style directive or a per-command override.
3. **Per-project default** — `ProjectSettings.default_engine` (`settings.py:210`).
4. **Global default** — `settings.default_engine` (default `"codex"`), resolved by
   `resolve_default_engine` (`runtime_loader.py:59`).

The machinery:
- `TransportRuntime.resolve_engine()` (`transport_runtime.py:117`) applies precedence 2–4.
- `resolve_engine_defaults` folds in per-chat/topic defaults (called at `loop.py:2003`).
- `TransportRuntime.resolve_runner()` (`transport_runtime.py:291`) → `AutoRouter.entry_for_engine`
  / `entry_for(resume_token)` (`router.py:76`/`:83`) returns the `RunnerEntry`; `_run_engine`
  reads `entry.runner` (`executor.py:209`).
- `AutoRouter` also owns resume parsing across engines: `extract_resume` / `resolve_resume`
  (`router.py:98`/`:107`) walk **every** runner's regex, so a reply's resume footer selects the
  correct engine automatically.

The router itself is built once at boot (`build_router`, `runtime_loader.py:75`): for each
installed `EngineBackend` it calls `backend.build_runner(engine_cfg, config_path)`, checks
`shutil.which(backend.cli_cmd)` for CLI presence, assigns an `EngineStatus`
(`ok`/`missing_cli`/`bad_config`/`load_error`), and wraps everything in an `AutoRouter`
(`router.py:43`).

## The agent preamble (telling agents they're on Telegram)

Every user prompt is prefixed with a preamble so the agent behaves well over Telegram.

- `_DEFAULT_PREAMBLE` (`runner_bridge.py:361–413`) tells the agent: it runs via Untether on
  Telegram; **the user can only see final assistant text** (tool calls, thinking, file contents,
  terminal output are invisible); do not `systemctl restart untether` (config hot-reloads, and a
  restart would kill the session issuing it); `ExitPlanMode` plans must be concise 3–5 bullets;
  every completing response must end with a `## Summary` structure; deliver files by writing them
  to `.untether-outbox/`.
- `_apply_preamble(prompt)` (`runner_bridge.py:433`): if `PreambleSettings.enabled`, prepends
  `cfg.text` (override) or the default, returning `f"{text}\n\n---\n\n{prompt}"`. It also appends
  per-chat **AskUserQuestion guidance** based on `run_opts.ask_questions` (`:445`) — either "use
  AskUserQuestion with clear options" or "do NOT call AskUserQuestion; proceed with defaults."
- `_load_preamble_settings()` (`runner_bridge.py:416`) reads `settings.preamble` **fresh each
  call** (hot-reload). Config model `PreambleSettings` (`settings.py:259`): `enabled: bool = True`,
  `text: str | None = None`.
- **Injection point**: `handle_message` line `2901`, after `_strip_resume_lines` and before the
  prompt reaches the runner. It is therefore engine-agnostic — every engine receives the preamble.

## Auto-continue + signal-death suppression

Mitigates a Claude Code upstream bug (`#34142`/`#30333`) where the CLI exits after receiving tool
results without taking a turn.

- `_is_signal_death(rc)` (`runner_bridge.py:245`) — True for `rc < 0` (Python signal convention) or
  `rc > 128` (shell 128+N). Used to **suppress** auto-continue when the process was killed
  externally (SIGTERM/SIGKILL, e.g. earlyoom) rather than hitting the bug (which exits `rc=0`).
- `_should_auto_continue(...)` (`runner_bridge.py:258`) — True only when: NOT cancelled, `engine ==
  "claude"`, `last_event_type == "user"` (last raw JSONL was a tool_result), a resume value exists,
  NOT a signal death, and `auto_continued_count < max_retries`.
- Decision + recursion in `handle_message` (`:3143–3257`): reads
  `edits.stream.last_event_type`/`proc_returncode`; on trigger it (a) delivers outbox files from the
  dying subprocess **before** respawn so nothing is orphaned, (b) sends a 🔁 notice, (c) recursively
  calls `handle_message(text="continue", resume_token=…, _auto_continued_count += 1)`.
- Config `AutoContinueSettings` (`settings.py:266`): `enabled: bool = True`, `max_retries: int = 1`
  (bounded 0–3).

Distinct from auto-continue is the **live** stall watchdog: `_detect_stuck_after_tool_result`
(`runner_bridge.py:2031`) / `_handle_stuck_after_tool_result` (`:2124`), governed by
`WatchdogSettings` (`settings.py:277`).

## Transport & outbox

All Telegram writes (send/edit/delete) go through **`TelegramOutbox`** (`telegram/outbox.py`) via
the `TelegramTransport` (`telegram/bridge.py:216`) — never the Bot API directly from handlers. The
outbox handles coalescing, priority scheduling, and rate limiting (per-chat pacing; on HTTP 429 it
requeues unless superseded).

Transport-neutral types (`transport.py:11–31`): `RenderedMessage`, `SendOptions`, `MessageRef`. The
`Presenter` (`presenter.py:9`) and `Transport` (`transport.py:34`) protocols keep the orchestrator
independent of Telegram; the Telegram implementations are `TelegramPresenter` and
`TelegramTransport` (`telegram/bridge.py`).

Two outbox-adjacent features surface engine output as files:
- **Agent-initiated delivery** — agents write to `.untether-outbox/`; `telegram/outbox_delivery.py`
  scans, validates (deny-glob, size limit, file-count cap), sends as documents with 📎 captions, and
  cleans up on completion. Configured under `[transports.telegram.files]`.
- **Inline keyboards** — `RenderedMessage.extra["reply_markup"]["inline_keyboard"]`; approval
  transitions are detected via keyboard length changes, with a separate push notification when
  approval buttons appear. Callback data is ≤ 64 bytes (`prefix:action:id`) and answered promptly to
  clear the spinner.

The update loop (`telegram/loop.py`) polls with a persisted `update_id`
(`telegram/offset_persistence.py`, `#287`) so restarts don't drop/duplicate updates, and integrates
`sd_notify` (`sdnotify.py`) for systemd `Type=notify`.

## Config model

TOML at `~/.untether/untether.toml` (`config.py:16`, overridable via `UNTETHER_CONFIG_PATH`),
loaded/validated by **`UntetherSettings`** (`settings.py:448`) — a pydantic `BaseSettings` with
`extra="allow"`, env prefix `UNTETHER__`, nested delimiter `__`, and a `TomlConfigSettingsSource`.

Top-level fields (`settings.py:456`):

| Field | Meaning |
|-------|---------|
| `default_engine` | global default engine (default `"codex"`) |
| `default_project` / `projects` | project registry (`ProjectSettings`) |
| `transport` / `transports` | active transport (default `"telegram"`) + settings |
| `plugins` | `PluginsSettings.enabled` — the entry-point **allowlist** |
| `preamble` | `PreambleSettings` (see above) |
| `auto_continue` / `watchdog` | see above |
| `cost_budget` / `loop` / `footer` / `progress` / `security` | run-time behaviour |

Engine/CLI-relevant sections:

- `[transports.telegram]` → `TelegramTransportSettings` (`settings.py:99`): `bot_token`
  (SecretStr), `chat_id`, `allowed_user_ids`, `allow_any_user`, `session_mode`
  (`stateless`|`chat`), `show_resume_line`, `message_overflow`, `topics`, `files`, voice settings.
  Restart-only keys enumerated at `settings.py:112`.
- `[projects.<alias>]` → `ProjectSettings` (`settings.py:210`): `path`, `worktrees_dir`,
  `default_engine` (per-project engine override), `worktree_base`, `chat_id` (per-chat routing).
- **Per-engine config**: `engine_config(engine_id)` (`settings.py:501`) supports both nested
  `[engines.claude]` and flat `[claude]` layouts; the returned dict is passed to
  `backend.build_runner`. This is how `[claude] model=…`, `[gemini] skip_trust=false`, `[codex]
  profile=…`, `[pi] provider=…`, etc. reach each runner.
- `[plugins].enabled` gates which entry points load (`resolve_plugins_allowlist`,
  `runtime_loader.py:50`).

Loaders: `load_settings` (`settings.py:655`), `load_settings_if_exists` (`:662`). Legacy top-level
`bot_token`/`chat_id` are rejected by a `model_validator` (`:474`).

## Plugin / entry-point loading (recap)

Three entry-point groups (`plugins.py:14`): `untether.engine_backends`,
`untether.transport_backends`, `untether.command_backends`. Each target module exposes a
module-level `BACKEND`. Loading: `_discover_entrypoints` (`:143`) selects + allowlist-filters +
validates + dedupes; `load_entrypoint` (`:235`) does `ep.load()` + validator + memoize;
`load_plugin_backend` (`:290`) maps failures to `ConfigError`. Errors are process-global and
surfaced by `untether plugins` and `doctor`. See
[02 — CLI Integration Model](./02-cli-integration-model.md#registration-enginebackend--python-entry-points)
for the engine-side detail.

## Trigger sources (cron / webhook / `/at`)

`RunContext.trigger_source` (`context.py:13`) is set by cron/webhook/`/at` dispatchers (under
`src/untether/triggers/` and `telegram/at_scheduler.py`); `handle_message` renders it into the
progress footer with ⏰/⚡ icons. `RunContext.permission_mode` (`context.py:18`) is a per-cron
permission override applied in `run_job` via `_apply_trigger_permission_override` (`loop.py:147`).
Runners themselves never set `meta["trigger"]` — that's reserved for the dispatchers (see
`.claude/rules/runner-development.md`).
