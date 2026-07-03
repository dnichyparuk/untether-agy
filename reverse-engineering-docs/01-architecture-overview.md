# 01 — Architecture Overview

This document explains the layers, the end-to-end data flow, and the boot sequence. Read it
first; the per-engine docs assume this map.

## The mental model

Untether is a long-running process that:

1. **Polls Telegram** for updates (messages, button callbacks).
2. **Classifies** each message: a slash-command, a cancel, a resume, or a prompt.
3. For a prompt, **selects an engine** and **spawns its CLI as a subprocess**.
4. **Reads the subprocess's JSONL stdout**, translating each line into a normalized
   `UntetherEvent`.
5. **Renders those events** into a single, continuously-edited Telegram progress message
   (plus a final answer message).
6. For Claude only, **feeds tool-approval decisions back** into the subprocess over a
   bidirectional control channel.

Everything downstream of "spawn the subprocess" is engine-agnostic. The only per-engine code is
how you build the CLI argv (`build_args`) and how you translate that engine's JSONL dialect into
`UntetherEvent`s (`translate`). See [02 — CLI Integration Model](./02-cli-integration-model.md).

## Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│ Telegram Bot API                                                      │
└───────────────┬─────────────────────────────────────────────────────┘
                │ getUpdates / sendMessage / editMessageText
┌───────────────▼─────────────────────────────────────────────────────┐
│ Transport layer            src/untether/telegram/                     │
│   TelegramClient (client_api.py)  – raw Bot API                       │
│   TelegramTransport (bridge.py)   – outbox, coalescing, rate limit    │
│   TelegramPresenter (bridge.py)   – renders RenderedMessage           │
│   loop.py                         – poll → route → dispatch           │
└───────────────┬─────────────────────────────────────────────────────┘
                │ ResolvedMessage / RunContext / RunOptions
┌───────────────▼─────────────────────────────────────────────────────┐
│ Orchestration              src/untether/runner_bridge.py              │
│   handle_message()   – THE orchestrator                               │
│   ProgressEdits      – live-editing loop + stall watchdogs            │
│   preamble injection, auto-continue, cost tracking                    │
└───────────────┬─────────────────────────────────────────────────────┘
                │ runner.run(prompt, resume_token) → async UntetherEvents
┌───────────────▼─────────────────────────────────────────────────────┐
│ Runner layer               src/untether/runner.py + runners/*.py      │
│   JsonlSubprocessRunner (base) – spawn, read JSONL, enforce contract  │
│   ClaudeRunner / GeminiRunner / …                                     │
└───────────────┬─────────────────────────────────────────────────────┘
                │ argv + stdin ; JSONL on stdout
┌───────────────▼─────────────────────────────────────────────────────┐
│ External CLI subprocess    claude / gemini / codex / opencode / pi …  │
└─────────────────────────────────────────────────────────────────────┘
```

Two transport-neutral abstractions decouple orchestration from Telegram:
- **`Presenter`** protocol (`presenter.py:9`) — the orchestrator emits `RenderedMessage`s to it.
- **`Transport`** protocol (`transport.py:34`) — `send` / `edit` / `delete` over `MessageRef`s.

The Telegram implementations are `TelegramPresenter` (`telegram/bridge.py:51`) and
`TelegramTransport` (`telegram/bridge.py:216`); `RenderedMessage` / `SendOptions` / `MessageRef`
are the neutral data types (`transport.py:11–31`).

## End-to-end data flow (a prompt)

The concrete call chain — each arrow is a function/method call:

```
poll_updates()                        telegram/loop.py:557   (async generator; yields updates)
  → run_main_loop()                   telegram/loop.py:1300  (async for update in poller)
  → route_update()                    telegram/loop.py       (callback vs message split)
  → route_message()                   telegram/loop.py:2238  (classify: cancel/command/prompt)
  → _dispatch_pending_prompt()        telegram/loop.py:2086  (after forward/media coalescing)
  → dispatch_prompt_run()             telegram/loop.py:1990  (resolve engine + resume)
  → run_job()                         telegram/loop.py:1701  (resolve run_options, permission)
  → run_engine (_run_engine)          telegram/commands/executor.py:159
  → handle_message()                  runner_bridge.py:2873  (THE orchestrator)
  → run_runner_with_cancel()          runner_bridge.py:2704
  → runner.run(prompt, resume_token)  runner.py (BaseRunner → JsonlSubprocessRunner.run_impl)
  → ProgressEdits.on_event / .run     runner_bridge.py:934   (renders + edits Telegram msg)
  → transport.edit / transport.send   telegram/bridge.py:216
```

Key data objects that flow through the chain:

- **`ResolvedMessage`** (`transport_runtime.py:31`) — output of `TransportRuntime.resolve_message`
  (`transport_runtime.py:175`): `prompt`, `resume_token`, `engine_override`, `context`.
- **`RunContext`** (`context.py:6`) — `project`, `branch`, `trigger_source`, `permission_mode`.
- **`RunOptions` / `EngineRunOptions`** (`runners/run_options.py:9`) — per-run overrides (model,
  reasoning, permission mode, ask_questions, diff_preview, budget toggles), propagated through a
  `ContextVar` so the runner's `build_args`/`translate` can read them without threading.
- **`RunOutcome`** (`runner_bridge.py:2697`) — `cancelled`, `completed: CompletedEvent`, `resume`.

Message classification (`route_message`, `loop.py:2238` → `_classify_message`, `loop.py:670`):
cancel commands → `handle_cancel`; `/continue` builds a synthetic
`ResumeToken(is_continue=True)` (`loop.py:2283`); builtin slash commands → `_dispatch_builtin_command`
(`loop.py:314`); otherwise the text is buffered through `ForwardCoalescer` / `MediaGroupBuffer` and
becomes a prompt.

## The reverse path: events → Telegram

`runner.run()` is an **async generator** of `UntetherEvent`s. `ProgressEdits` (`runner_bridge.py:934`)
consumes them:

- `StartedEvent` → open/seed the progress message; store `meta` (model, permission mode) for the footer.
- `ActionEvent` → append/update a progress line (a tool call, file change, note, warning). If the
  action carries an `inline_keyboard` (Claude approval buttons), the message becomes interactive.
- `CompletedEvent` → render the final answer, append the resume footer, clean up ephemeral messages,
  deliver `.untether-outbox/` files.

`ProgressEdits` also runs **stall watchdogs** (`_stall_monitor`, `runner_bridge.py:1132`) and a
heartbeat, with thresholds injected per-run in `handle_message` (`runner_bridge.py:2968–2997`).

See [06 — Orchestration, Transport & Config](./06-orchestration-and-transport.md) for the
orchestrator internals.

## Boot sequence

Starting the daemon (`untether` with no subcommand, or `untether <engine>`):

```
pyproject.toml:  untether = "untether.cli:main"
cli/__init__.py: main()                     :183
  → create_app()                            :158   (builds the Typer app; one command per engine)
  → app_main()                              cli/run.py:333   (default callback)
  → _run_auto_router()                      cli/run.py:143   (the real boot)
       1. setup_logging + _resolve_setup_engine   (settings, allowlist, default engine, backend)
       2. _resolve_transport_id → get_transport("telegram")     transports.py:61
       3. transport_backend.check_setup(...) ; optional onboarding
       4. load_settings() → build_runtime_spec(...)             runtime_loader.py:226
       5. acquire_config_lock (single-instance lock by bot-token fingerprint)
       6. spec.to_runtime(...) → TransportRuntime
       7. transport_backend.build_and_run(runtime=..., ...)
  → TelegramBackend.build_and_run()         telegram/backend.py:203
       builds TelegramClient, TelegramTransport, MarkdownFormatter, TelegramPresenter,
       assembles ExecBridgeConfig + TelegramBridgeConfig, then anyio.run(run_loop)
  → run_main_loop()                         telegram/loop.py:1300
```

`build_runtime_spec()` (`runtime_loader.py:226`) is where engines become usable:

```
build_runtime_spec:
  resolve_plugins_allowlist(settings)         → [plugins].enabled list
  list_backend_ids(allowlist)                 → installed engine ids
  resolve_default_engine(...)                 → override | settings.default_engine | "codex"
  load_backends(...)                          → get_backend(id) per engine  (entry-point load)
  build_router(...)                           → per engine: backend.build_runner(cfg, path)
                                                 → RunnerEntry(engine, runner, status)
                                                 → AutoRouter
```

`AutoRouter` (`router.py:43`) then owns per-message engine selection and resume parsing.

Running `untether claude` instead of bare `untether` just sets `default_engine_override="claude"`
via `make_engine_cmd(engine_id)` (`cli/run.py:379`); the rest of the boot is identical.

## Where each concern lives (quick index)

| Concern | Where |
|---------|-------|
| Poll Telegram / route updates | `telegram/loop.py` |
| Classify message, dispatch | `telegram/loop.py` (`route_message`, `run_job`) |
| Engine selection | `transport_runtime.py` (`resolve_engine`), `router.py` (`AutoRouter`) |
| Spawn + drive subprocess | `runner.py` (`JsonlSubprocessRunner.run_impl`) |
| Per-engine argv + translation | `runners/<engine>.py` |
| Normalize events | `model.py`, `events.py` (`EventFactory`) |
| Orchestrate a run | `runner_bridge.py` (`handle_message`, `ProgressEdits`) |
| Render to Telegram | `telegram/bridge.py`, `markdown.py` |
| Interactive approval (Claude) | `runners/claude.py`, `telegram/commands/claude_control.py` |
| Config | `settings.py`, `config.py` |
| Entry-point loading | `plugins.py`, `engines.py`, `runtime_loader.py` |
