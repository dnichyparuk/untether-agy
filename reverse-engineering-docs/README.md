# Untether — Reverse-Engineering Documentation

> Reverse-engineered from source at commit `4009531` (v0.35.3). Line numbers are given as `file:line` for navigation — treat them as pointers, not contracts.

## What this project is

**Untether** is a Python **Telegram bridge for AI coding-agent CLIs**. It runs coding agents (Claude Code, Codex, OpenCode, Pi, Gemini CLI, Amp) as **local subprocesses**, streams their line-delimited JSON (JSONL) output, translates it into a small internal event model, and renders that as live-editing Telegram messages with interactive permission buttons, cost tracking, and progress.

It is a fork/superset of upstream [`banteg/takopi`](https://github.com/banteg/takopi), adding interactive permission control, plan-mode support, and many UX features. The published package name is `untether` (`pyproject.toml`), entry point `untether = "untether.cli:main"`.

```
Telegram  <->  TelegramPresenter  <->  RunnerBridge  <->  Runner (claude/codex/opencode/pi/gemini/amp)
                                            |
                                     ProgressTracker
```

The core insight: **every engine is a subprocess that speaks JSONL on stdout.** Untether normalizes each engine's dialect into a uniform 3-event contract (`StartedEvent` → `ActionEvent*` → `CompletedEvent`). Only Claude Code additionally supports a **bidirectional control channel** (over stdin/PTY) for interactive tool approval; all other engines run one-shot non-interactively with broad auto-approve flags.

## How to read these docs

| Doc | Read it to understand |
|-----|-----------------------|
| [01 — Architecture Overview](./01-architecture-overview.md) | The layers, the end-to-end data flow (Telegram → runner → Telegram), the boot sequence, and where each concern lives. **Start here.** |
| [02 — CLI Integration Model](./02-cli-integration-model.md) | The **generic** engine-integration mechanism: `JsonlSubprocessRunner` template methods, the 3-event contract, `EngineBackend` + entry-point registration, session locking, the tool-result classifier. This is the shared substrate all engines plug into. |
| [03 — Claude Code Integration](./03-claude-integration.md) | The **most complex** integration: two spawn modes (PIPE control-channel vs legacy PTY), the stream-json protocol, the interactive control channel, permission modes, plan mode, AskUserQuestion, and parent-initiated MCP catalog refresh. |
| [04 — Gemini CLI Integration](./04-gemini-integration.md) | A **representative non-interactive** integration end-to-end: CLI flags, the `stream-json` event shapes, translation, `--skip-trust`/`yolo` headless handling, resume. |
| [05 — Other Engines (Codex, OpenCode, Pi, Amp, Mock)](./05-other-engines.md) | Comparative summary of the remaining engines and what makes each distinct. |
| [06 — Orchestration, Transport & Config](./06-orchestration-and-transport.md) | The glue: `runner_bridge.handle_message`, engine selection per chat, the agent preamble, auto-continue, the Telegram transport/outbox, and the config model. |

### Proposals / feasibility

| Doc | Purpose |
|-----|---------|
| [Antigravity CLI Runner — Feasibility](./antigravity-cli-runner-feasibility.md) | Deep technical analysis of whether Google's Antigravity CLI (`agy`) can be added as an Untether runner: capability matrix vs existing runners, a scoped runner proposal, and upstream triggers. **Includes an empirical-update banner** — several doc-based conclusions were overturned by live testing (see below). |
| [Antigravity — Experiment Report](./agy-probes/EXPERIMENT-REPORT.md) | **Empirical lab report** for `agy` 1.0.16: all captured data structures (help flag set, `--output-format json` result envelope, `usage`, statusline hook JSON), request/response pairs, exit codes, the flag-existence matrix, and the mapping to Untether's event model. |
| [Antigravity — Probe Harness](./agy-probes/README.md) | Runnable bash scripts (`run_all.sh`) that re-derive the capability matrix against any installed `agy` — help/flag discovery (free), then gated live probes; emits a `capability-report.md`. |
| [Antigravity Runner — Implementation Spec](./antigravity-runner-spec.md) | **The build spec** for the new `antigravity` runner: base-class choice, exact `build_args`, the msgspec envelope schema, event translation, resume/session model, config keys, security, error handling, feature matrix, file layout, testing plan, and open questions. Grounded in the empirical envelope. |

## Key source map

| Area | Path |
|------|------|
| Internal event model | `src/untether/model.py`, `src/untether/events.py` |
| Base subprocess runner | `src/untether/runner.py` |
| Engine registry / entry points | `src/untether/backends.py`, `engines.py`, `plugins.py`, `runtime_loader.py` |
| Engine runners | `src/untether/runners/{claude,codex,opencode,pi,gemini,amp,mock}.py` |
| Engine JSONL schemas (msgspec) | `src/untether/schemas/{claude,codex,gemini,opencode,pi,amp}.py` |
| Central orchestrator | `src/untether/runner_bridge.py` |
| Engine selection / router | `src/untether/router.py`, `transport_runtime.py` |
| Telegram transport | `src/untether/telegram/` (`bridge.py`, `loop.py`, `backend.py`, `outbox.py`) |
| Claude control callbacks | `src/untether/telegram/commands/claude_control.py` |
| CLI entry point | `src/untether/cli/` (`__init__.py`, `run.py`) |
| Config model | `src/untether/settings.py`, `config.py` |

## Vocabulary

- **Engine** — an external coding-agent CLI (`claude`, `gemini`, …). Identified by an `EngineId` string.
- **Runner** — the Untether class that spawns and drives one engine's subprocess.
- **Backend** — an `EngineBackend` record (`id`, `build_runner`, `cli_cmd`, `install_cmd`) registered via a Python entry point; the factory that produces a Runner.
- **UntetherEvent** — the internal normalized event (`StartedEvent | ActionEvent | CompletedEvent`).
- **ResumeToken** — `(engine, value, is_continue)`; identifies a resumable CLI session and pins which engine handles a follow-up.
- **Control channel** — Claude-only bidirectional stdin/stdout protocol for interactive tool approval (`control_request` ↔ `control_response`).
- **Preamble** — text prepended to every user prompt telling the agent it is running on Telegram.

---

*These docs describe observed behaviour of the code as written. Where a design choice ties to a
specific upstream issue, the issue number is noted (e.g. `#365`).*
