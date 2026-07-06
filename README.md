<h1 align="center">re-untether</h1>

<p align="center">
  <strong>A Telegram bridge for AI coding agents.</strong><br>
  Issue tasks by voice or text, stream progress in real time, and approve changes remotely.
</p>

<p align="center">
  Supported engines:
  <a href="https://docs.anthropic.com/en/docs/claude-code">Claude Code</a> ·
  <a href="https://github.com/openai/codex">Codex</a> ·
  <a href="https://github.com/opencode-ai/opencode">OpenCode</a> ·
  <a href="https://github.com/nicholasgasior/pi">Pi</a> ·
  <a href="https://github.com/google-gemini/gemini-cli">Gemini CLI</a> ·
  <a href="https://ampcode.com">Amp</a> ·
  <a href="https://antigravity.google">Antigravity</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License" /></a>
</p>

<p align="center">
  <a href="#installation">Installation</a> ·
  <a href="#features">Features</a> ·
  <a href="#supported-engines">Engines</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#contributing">Contributing</a>
</p>

---

> **Fork note:** This is a fork of the original [littlebearapps/untether](https://github.com/littlebearapps/untether). It adds support for Google's [Antigravity](https://antigravity.google) CLI (`agy`) as a coding-agent engine, alongside the engines the upstream project already supports. See the [Antigravity runner reference](docs/reference/runners/antigravity/runner.md) and [configuration](docs/reference/config.md#antigravity) for details. All other functionality tracks upstream. The project is distributed as the `untether` package and installs an `untether` command.

---

## Overview

AI coding agents require a terminal, but the operator does not need to remain at one. re-untether runs on your machine and connects your agents to a Telegram bot. You send a task from any device — by voice or text — and observe the agent working in real time. When the agent requests permission, you approve it with an inline button; when it finishes, you read the result. No desk, SSH session, or screen sharing is required.

<p align="center">
  <img src="docs/assets/screenshots/hero-collage.jpg" alt="Sending tasks by voice, approving changes remotely, and configuring from Telegram" width="100%" />
</p>
<p align="center"><sub>Feature availability varies by engine — see <a href="#engine-compatibility">engine compatibility</a>.</sub></p>

Key characteristics:

- **Local execution** — agents run on your computer or server as usual; re-untether only bridges them to Telegram.
- **Remote operation** — any device with Telegram, including [Telegram Web](https://web.telegram.org), can start and review tasks.
- **Background execution** — a task continues after you close Telegram, lose connectivity, or your device powers off; results are available when you return.
- **Voice input** — voice notes are transcribed via a configurable Whisper-compatible endpoint.
- **Project and engine switching** — repositories, branches, and engines are selected from within the same chat, with no restart required.
- **Remote control** — budgets, cost tracking, and interactive approval buttons allow agents to run unattended.

---

## Installation

re-untether is not published to PyPI. Install it directly from this repository.

Using [uv](https://docs.astral.sh/uv/) (recommended):

```sh
uv tool install git+https://github.com/dnichyparuk/re-untether.git
```

Using pipx:

```sh
pipx install git+https://github.com/dnichyparuk/re-untether.git
```

Using pip (into the active environment):

```sh
pip install --no-cache-dir git+https://github.com/dnichyparuk/re-untether.git
```

Each command places the `untether` command on your `PATH`. Run the setup wizard to create a Telegram bot, choose a workflow mode, and connect your chat:

```sh
untether
```

For local development, install from a clone as an editable checkout:

```sh
git clone https://github.com/dnichyparuk/re-untether.git
cd re-untether
uv sync
uv run untether
```

**Tip:** If you already have a bot token, pass it directly: `untether --bot-token YOUR_TOKEN`.

---

## Quick start

After running `untether` and completing the setup wizard, send a message to your bot:

> fix the failing tests in src/auth

The agent runs on your machine, streams progress to Telegram, and you can reply to continue the conversation.

The wizard offers three **workflow modes**:

| Mode | Description |
|------|-------------|
| **Assistant** | Ongoing chat — messages auto-resume your session. Use `/new` to start fresh. |
| **Workspace** | Forum topics — each topic is bound to a project and branch with an independent session. |
| **Handoff** | Reply-to-continue — resume lines are shown for copying to a terminal. |

See [Choose a mode](docs/how-to/choose-a-mode.md) and the [conversation modes tutorial](docs/tutorials/conversation-modes.md) for guidance, and the [help guides](#documentation) for detailed setup, engine configuration, and troubleshooting.

---

## Features

- **Progress streaming** — observe the agent in real time, including tool calls, file changes, and elapsed time.
- **Interactive permissions** — approve plan transitions and answer clarifying questions with inline option buttons; tools auto-execute, with progressive cooldown after "Pause & Outline Plan".
- **Plan mode** — toggle per chat with `/planmode`: full manual approval, auto-approved transitions, or no plan phase.
- **Projects and worktrees** — register repositories with `untether init`, target them with `/myproject @feat/thing`, and run branches in isolated worktrees in parallel.
- **Clone from Telegram** — `/clone <repo-url> [--dir <path>] [@<branch>]` clones a GitHub repository with native git, auto-registers it as a project, and (in a forum-enabled group) creates a bound topic.
- **New project from Telegram** — `/project <name>` creates an empty local project directory, auto-registers it, and (in a forum-enabled group) creates a bound topic.
- **Cost and usage tracking** — per-run and daily budgets, `/usage` breakdowns, and optional auto-cancel keep spending visible.
- **Actionable error hints** — informative messages for API outages, rate limits, billing errors, and network failures, with resume guidance.
- **Model and mode metadata** — every completed message reports the model with version, effort level, and permission mode (e.g. `opus 4.6 · medium · plan`) across all engines.
- **Voice notes** — tasks can be dictated instead of typed; re-untether transcribes them via a configurable Whisper-compatible endpoint.
- **Cross-environment resume** — start a session in your terminal and resume it from Telegram with `/continue`; supported for Claude Code, Codex, OpenCode, Pi, Gemini, and Antigravity (`agy`) ([guide](docs/how-to/cross-environment-resume.md)).
- **File transfer** — upload files with `/file put` and download with `/file get`; agents can also deliver files by writing to `.untether-outbox/` during a run, which are sent as Telegram documents on completion.
- **Graceful recovery** — orphan progress messages are cleaned up on restart; stall detection with CPU-aware diagnostics; auto-continue for Claude Code sessions that exit prematurely.
- **Scheduled tasks** — cron expressions with timezone support, webhook triggers, one-shot delays (`/at 30m <prompt>`), `run_once` crons, a master pause/resume toggle, and hot-reload configuration. `/ping` shows a per-chat trigger summary; trigger-initiated runs show provenance in the footer (`cron:<id>` / `webhook:<id>` / `at:<token>`); `/stats` reports a per-engine triggered-versus-manual breakdown.
- **Autonomous loops (Claude only)** — opt-in observation of Claude Code's `/loop` and `ScheduleWakeup`; iterations are re-fired after the subprocess exits so loops continue between turns. Disabled by default; enable per chat via `/config`. Cost is guarded by `[cost_budget]` and runaway safety by `[loop]` (maximum iterations, total duration, expiry).
- **Forum topics** — map Telegram topics to projects and branches.
- **Session export** — `/export` produces markdown or JSON transcripts.
- **File browser** — `/browse` navigates project files with inline buttons.
- **Inline settings** — `/config` opens an in-place settings menu to toggle plan mode, ask mode, approval policy (Codex), approval mode (Gemini), verbose output, engine, model, reasoning, and listen mode; a dedicated Triggers page lists per-chat crons and webhooks with last-fired times and a master pause/resume toggle.
- **Hot-reload configuration** — edits to `untether.toml` apply within approximately one second, covering triggers, voice transcription, allowed-user lists, watchdog timing, progress verbosity, file-transfer and outbox configuration, and per-engine overrides. Only `bot_token`, `chat_id`, `session_mode`, `topics`, and `message_overflow` require a restart. The engine-subprocess environment allowlist can be extended via `[security] env_extra_allow` and `env_extra_prefix_allow` to thread credential-manager tokens (1Password, Doppler, Vault, and similar) without forking.
- **Plugin system** — extend the tool with custom engines, transports, and commands.
- **Plugin compatibility** — Claude Code plugins detect re-untether sessions via the `UNTETHER_SESSION` environment variable, which prevents hooks from interfering with Telegram output; compatible with [PitchDocs](https://github.com/littlebearapps/lba-plugins) and other Claude Code plugins.
- **Session statistics** — `/stats` reports per-engine run counts, action totals, and duration across today, this week, and all time.
- **Three workflow modes** — assistant (ongoing chat with auto-resume), workspace (forum topics bound to projects and branches), or handoff (reply-to-continue with terminal resume lines); see [Choose a mode](docs/how-to/choose-a-mode.md).

---

## Supported engines

| Engine | Install | Suited to |
|--------|---------|-----------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `npm i -g @anthropic-ai/claude-code` | Complex refactors, architecture, long context |
| [Codex](https://github.com/openai/codex) | `npm i -g @openai/codex` | Fast edits, shell commands, quick fixes |
| [OpenCode](https://github.com/opencode-ai/opencode) | `npm i -g opencode-ai@latest` | 75+ providers via Models.dev, local models |
| [Pi](https://github.com/mariozechner/pi-coding-agent) | `npm i -g @mariozechner/pi-coding-agent` | Multi-provider authentication, conversational use |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `npm i -g @google/gemini-cli` | Google Gemini models, configurable approval mode |
| [Amp](https://ampcode.com) | `npm i -g @sourcegraph/amp` | Sourcegraph's AI coding agent, mode selection |
| [Antigravity](https://antigravity.google) | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | Google's `agy` CLI; non-interactive structured-result runs, keyring/OAuth authentication |

**Authentication:** Existing Claude or ChatGPT subscriptions may be used, so no additional API keys are required unless API billing is preferred. Antigravity authenticates via the operating-system keyring (Google OAuth); run `agy` once interactively on the host to sign in before headless use.

### Engine compatibility

| Feature | Claude Code | Codex CLI | OpenCode | Pi | Gemini CLI | Amp | Antigravity |
|---------|:-----------:|:---------:|:--------:|:--:|:----------:|:---:|:-----------:|
| **Progress streaming** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | —⁷ |
| **Session resume** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Model override** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅¹ | ✅ |
| **Model in footer** | ✅ | ✅ | ✅ | — | ✅ | — | ✅⁸ |
| **Approval mode in footer** | ✅ | ~⁴ | — | — | ~² | — | ✅ |
| **Voice input** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Verbose progress** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | —⁷ |
| **Error hints** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Preamble injection** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Cost tracking** | ✅ | ~³ | ✅ | ~³ | ~³ | ~³ | ~³ |
| **Interactive permissions** | ✅ | — | — | — | — | — | — |
| **Approval policy** | ✅ | ~⁴ | — | — | ~² | — | —⁹ |
| **Plan mode** | ✅ | — | — | — | — | — | — |
| **Ask mode (option buttons)** | ✅ | — | — | — | — | — | — |
| **Diff preview** | ✅ | — | — | — | — | — | — |
| **Auto-approve safe tools** | ✅ | — | — | — | — | — | — |
| **Progressive cooldown** | ✅ | — | — | — | — | — | — |
| **Subscription usage** | ✅ | — | — | — | — | — | — |
| **Reasoning/effort levels** | ✅ | ✅ | — | — | — | — | —¹⁰ |
| **Device re-auth (`/auth`)** | — | ✅ | — | — | — | — | — |
| **Context compaction** | — | — | — | ✅ | — | — | — |
| **Cross-env resume (`/continue`)** | ✅ | ✅ | ✅ | ✅⁵ | ✅ | —⁶ | ✅¹¹ |

¹ Amp model override maps to `--mode` (deep/free/rush/smart).
² Defaults to full access (`--approval-mode=yolo`, all tools auto-approved); toggle via `/config` to edit files (`auto_edit`, files permitted but no shell) or read-only; pre-run policy, not interactive mid-run approval.
³ Token usage counts only — no USD cost reporting.
⁴ Toggle via `/config` between full auto (default) and safe (`--ask-for-approval=untrusted`, untrusted tools blocked); pre-run policy, not interactive mid-run approval.
⁵ Pi requires `provider = "openai-codex"` in engine config for OAuth subscriptions in headless mode.
⁶ Amp requires an explicit thread ID; there is no "most recent" mode.
⁷ Antigravity returns a single result envelope at completion (no intermediate event stream), so the message shows "working…" followed by the final answer — no live or verbose progress.
⁸ The result envelope has no model field and `agy` silently ignores an invalid `--model`, so the footer reflects the *configured* model.
⁹ Permission stance (`auto_approve` / `sandbox`) is fixed at spawn time via config; `agy` has no interactive approval channel through re-untether.
¹⁰ The reasoning tier is baked into the model name (e.g. `Gemini 3.1 Pro (High)`); there is no separate effort flag.
¹¹ `agy --continue` resumes the machine-most-recent conversation (machine-global, not per-project); per-session resume via the message footer is preferred.

Claude effort levels: `low`, `medium`, `high`, `xhigh`, `max` (`xhigh` requires Claude Code v2.1.114+).

---

## Commands

| Command | Description |
|---------|-------------|
| `/cancel` | Stop the running agent |
| `/agent` | Show or set the engine for this chat |
| `/model` | Override the model for an engine |
| `/planmode` | Toggle plan mode (on/auto/off) |
| `/usage` | Show API costs for the current session (`/usage debug` shows fetch state, OAuth expiry, schema-mismatch counter) |
| `/export` | Export the session transcript |
| `/browse` | Browse project files |
| `/clone <repo-url> [--dir <path>] [@<branch>]` | Clone a GitHub repository and auto-register it as a project; in a forum-enabled group it also creates a bound topic ([guide](docs/how-to/projects.md#bootstrap-a-repo-from-telegram-with-clone)) |
| `/project <name>` | Create an empty local project directory and auto-register it; in a forum-enabled group it also creates a bound topic ([guide](docs/how-to/projects.md#bootstrap-a-new-project-from-telegram-with-project)) |
| `/new` | Cancel running tasks and clear stored sessions |
| `/continue` | Resume the most recent CLI session in this project ([guide](docs/how-to/cross-environment-resume.md)) |
| `/file put/get` | Transfer files |
| `/topic` | Create or bind forum topics |
| `/restart` | Gracefully restart re-untether (drains active runs first) |
| `/verbose` | Toggle verbose progress mode (tool details) |
| `/config` | Interactive settings menu (plan mode, ask mode, verbose, engine, model, reasoning, listen, approval mode, cost and usage); a Triggers page lists crons and webhooks with a master pause/resume toggle |
| `/ctx` | Show or update project/branch context |
| `/reasoning` | Set the reasoning-level override |
| `/listen` | Set the group-chat listen mode (`all` / `mentions` / `clear`); `/trigger` remains as a deprecated alias |
| `/stats` | Per-engine session statistics (today/week/all-time) |
| `/auth` | Codex device re-authentication |
| `/at 30m <prompt>` | Schedule a one-shot delayed run (60s–24h; `/cancel` to drop) |
| `/ping` | Health check and uptime (shows a per-chat trigger summary, if any) |
| `/health` | System snapshot: RAM/swap, process diagnostics, trigger counts, today's API cost, uptime |

Prefix any message with `/<engine>` to select an engine for that task, or `/<project>` to target a repository:

> /claude /myproject @feat/auth implement OAuth2

---

## Configuration

re-untether reads `~/.untether/untether.toml`. The setup wizard creates this file, or it can be configured manually:

```toml
default_engine = "codex"

[transports.telegram]
bot_token = "123456789:ABC..."
chat_id = 123456789
session_mode = "chat"

[projects.myapp]
path = "~/dev/myapp"
default_engine = "claude"

# `/clone <repo-url>` — clone a GitHub repository from Telegram and auto-register it
[clone]
enabled = true
root = "~/untether-projects"
allowed_hosts = ["github.com"]

# `/project <name>` — register a new empty local project from Telegram
[new_project]
enabled = true
root = "~/untether-projects"

[cost_budget]
enabled = true
max_cost_per_run = 2.00
max_cost_per_day = 10.00
```

See the [full configuration reference](docs/reference/config.md) for all options.

**Warning:** Never commit `untether.toml` — it contains your bot token. The default location (`~/.untether/`) keeps it outside your repositories.

---

## Upgrading

Reinstall from the repository:

```sh
uv tool install --force git+https://github.com/dnichyparuk/re-untether.git
# or, for a pipx installation:
pipx install --force git+https://github.com/dnichyparuk/re-untether.git
```

For an editable clone, pull and re-sync:

```sh
git pull
uv sync
```

Then restart to apply the change, preferably from Telegram so active runs are drained first:

```sh
/restart
```

Alternatively, restart from the terminal (press Ctrl+C first if it is already running):

```sh
untether
```

> **Note:** If a systemd service is configured on Linux, use `systemctl --user restart untether` instead.

---

## Requirements

- **Python 3.12+** — `uv python install 3.14`
- **uv** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- At least one agent CLI on `PATH`: `claude`, `codex`, `opencode`, `pi`, `gemini`, `amp`, or `agy`

---

## Documentation

Full documentation is available in the [`docs/`](docs/) directory.

### Getting started

- [Install and onboard](docs/tutorials/install.md) — setup wizard walkthrough
- [First run](docs/tutorials/first-run.md) — send your first task
- [Conversation modes](docs/tutorials/conversation-modes.md) — assistant, workspace, and handoff
- [Projects and branches](docs/tutorials/projects-and-branches.md) — multi-repository workflows
- [Multi-engine workflows](docs/tutorials/multi-engine.md) — switching between agents

### How-to guides

- [Interactive approval](docs/how-to/interactive-approval.md) — approve and deny tool calls from Telegram
- [Plan mode](docs/how-to/plan-mode.md) — control plan transitions and progressive cooldown
- [Cost budgets](docs/how-to/cost-budgets.md) — per-run and daily budget limits
- [Inline settings](docs/how-to/inline-settings.md) — the `/config` button menu
- [Voice notes](docs/how-to/voice-notes.md) — dictate tasks from your phone
- [File browser](docs/how-to/browse-files.md) — `/browse` inline navigation
- [Session export](docs/how-to/export-sessions.md) — markdown and JSON transcripts
- [Verbose progress](docs/how-to/verbose-progress.md) — tool-detail display
- [Group chats](docs/how-to/group-chat.md) — multi-user and listen modes
- [Context binding](docs/how-to/context-binding.md) — per-chat project/branch binding
- [Webhooks and cron](docs/how-to/webhooks-and-cron.md) — automated runs from external events
- [Update](docs/how-to/update.md) — upgrade to the latest version
- [Uninstall](docs/how-to/uninstall.md) — remove the CLI, config, and state files

### Engine guides

- [Claude Code](docs/reference/runners/claude/runner.md) — permission modes, plan mode, cost tracking, interactive approvals
- [Codex](docs/reference/runners/codex/exec-json-cheatsheet.md) — profiles, extra args, exec mode
- [OpenCode](docs/reference/runners/opencode/runner.md) — model selection, 75+ providers, local models
- [Pi](docs/reference/runners/pi/runner.md) — multi-provider auth, model and provider selection
- [Gemini CLI](docs/reference/runners/gemini/runner.md) — Google Gemini models, approval-mode passthrough
- [Amp](docs/reference/runners/amp/runner.md) — mode selection, thread management
- [Antigravity](docs/reference/runners/antigravity/runner.md) — the `agy` CLI, structured-result runs, resume, capability tier

### Reference

- [Configuration reference](docs/reference/config.md) — full walkthrough of `untether.toml`
- [Troubleshooting](docs/how-to/troubleshooting.md) — common issues and solutions
- [Architecture](docs/explanation/architecture.md) — how the components fit together

---

## Security and access

re-untether runs on your machine and bridges your agents to Telegram. The following summarises what it accesses:

| Category | Resource | Details |
|----------|----------|---------|
| **Network** | Telegram Bot API (`api.telegram.org`) | Core transport — always active during operation |
| **Network** | Whisper-compatible endpoint | Voice transcription — disabled by default, opt-in via config |
| **Network** | Agent APIs (Anthropic, OpenAI, and others) | Called by agent subprocesses, not by re-untether directly |
| **Filesystem** | `~/.untether/untether.toml` | Config file containing the bot token — protect with `chmod 600` |
| **Filesystem** | `~/.untether/*.json` | Chat preferences, session state, usage statistics |
| **Filesystem** | `.untether-outbox/` | Agent-delivered files (optional, per-project) |
| **Filesystem** | `/file put` upload paths | User-initiated uploads from Telegram, written to configured destinations (default: project working directory) |
| **Filesystem** | Webhook `file_write` action | When configured, webhooks can write POST bodies to disk at admin-defined paths (deny-globs apply) |
| **Network** | Webhook `http_forward` action | When configured, webhooks can forward payloads to admin-defined URLs (SSRF-protected) |
| **Processes** | Agent CLIs (`claude`, `codex`, and others) | Spawned as subprocesses with your user permissions; agents have full filesystem access in their working directory |
| **Credentials** | Telegram bot token | Stored in the config file (plaintext TOML) |
| **Credentials** | API keys | Read from environment variables, never stored by re-untether |

re-untether performs no telemetry, analytics, phone-home, auto-updates, or privilege escalation. Sensitive tokens (bot token, OpenAI keys, GitHub tokens) are automatically [redacted from logs](docs/how-to/security.md).

Under your direction, spawned agents, `/file put`, the outbox, and webhook actions can access paths outside `~/.untether/` — this is intended. Use [`allowed_user_ids`](docs/how-to/security.md), file deny-globs, and webhook authentication to control who can trigger these flows.

---

## Contributing

To report a bug or propose an idea, [open an issue](https://github.com/dnichyparuk/re-untether/issues).

To contribute code, see [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and guidelines.

---

## Acknowledgements

re-untether is a fork of [littlebearapps/untether](https://github.com/littlebearapps/untether), which is itself a fork of [takopi](https://github.com/banteg/takopi) by [@banteg](https://github.com/banteg). takopi provided the original Telegram-to-Codex bridge; upstream Untether extended it with interactive permission control, multi-engine support, plan mode, and cost tracking.

---

## Licence

[MIT](LICENSE) — original work by [Little Bear Apps](https://github.com/littlebearapps).
