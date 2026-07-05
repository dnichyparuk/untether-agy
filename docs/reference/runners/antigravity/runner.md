# Antigravity runner (`agy`)

The Antigravity runner integrates Google's [Antigravity CLI](https://antigravity.google/docs/cli-overview)
(`agy`) as a **non-interactive, structured-result** engine. Verified against `agy` 1.0.16.

## Capability tier

`agy -p "<prompt>" --output-format json` returns a **single JSON result envelope** at
completion — not a streaming event feed. The runner therefore emits:

- a real `ResumeToken` (from `conversation_id`),
- the answer (`response`),
- ok/error (from `status`),
- token usage (`usage`).

It does **not** produce live `ActionEvent` progress (no intermediate tool/file events),
interactive approval, plan mode, AskUserQuestion, or USD cost (the envelope carries tokens
only). agy has no bidirectional control channel, so approval is decided at spawn time.

## Prerequisites

- `agy` on `PATH` — `curl -fsSL https://antigravity.google/cli/install.sh | bash`.
- **Pre-authenticated on the host.** agy authenticates via the OS keyring → Google OAuth;
  there is no API-key environment variable. Because Untether runs headless, complete an
  interactive `agy` login once per host so the daemon inherits the saved session.

## CLI invocation

`command()` → `agy`. `build_args` (order):

| Arg | When |
|-----|------|
| `-p <sanitized prompt>` | always (prompt is sanitized so a leading `-` isn't parsed as a flag) |
| `--output-format json` | always — yields the structured envelope |
| `--model <name>` | `[antigravity] model` or per-run `/model` override; full display name, e.g. `"Gemini 3.1 Pro (High)"` |
| `--continue` | `/continue` — resumes the machine-most-recent conversation |
| `--conversation <id>` | resume a specific conversation (id from a prior envelope) |
| `--sandbox` | `[antigravity] sandbox = true` |
| `--dangerously-skip-permissions` | `[antigravity] auto_approve = true` (default) — headless auto-approve |
| `--print-timeout <dur>` | `[antigravity] print_timeout` (Untether default `15m`, overrides agy's own `5m0s`) |
| `--add-dir <path>` | repeated per `[antigravity] add_dirs` |

The prompt is passed on argv; stdin is closed (`stdin_payload()` → `None`). No PTY is used.
Environment is allowlist-filtered (`utils/env_policy.py`, #198) — the subprocess does not
inherit the full daemon environment.

## Configuration (`[antigravity]`)

| Key | Type | Default | Effect |
|-----|------|---------|--------|
| `model` | string | none | `--model` |
| `sandbox` | bool | `false` | `--sandbox` |
| `auto_approve` | bool | `true` | `--dangerously-skip-permissions` |
| `print_timeout` | string | `15m` | `--print-timeout` (overrides agy's own `5m0s`) |
| `add_dirs` | list[string] | `[]` | `--add-dir` (repeated) |
| `extra_args` | list[string] | `[]` | appended (Untether-managed flags rejected) |

Reserved flags (rejected in `extra_args`): `-p`, `--print`, `--prompt`, `--output-format`,
`--continue`, `-c`, `--conversation`, `--model`, `--dangerously-skip-permissions`, `--sandbox`.
The last two are derived from the `auto_approve` / `sandbox` config booleans, so allowing them
via `extra_args` could silently contradict the configured permission stance.

## Resume

- `format_resume` renders `` `agy --conversation <id>` ``; replying to a message with that
  footer resumes the conversation (via `AutoRouter`).
- `--continue` resumes the **machine-global** most-recent conversation — potentially the wrong
  chat in a multi-chat deployment. Prefer per-session resume (`--conversation <id>`), which the
  runner emits in every footer.

## Known limitations

- **No live progress** — the envelope is terminal-only, so the Telegram message shows
  "working…" then the final answer. A long healthy run is stdout-silent; tune the `[watchdog]`
  expectations for this engine accordingly.
- **agy's own print-timeout** — agy kills a headless run at its built-in `5m0s` by default.
  Untether raises this to `15m` (`--print-timeout 15m`) so long tasks aren't cut off mid-run;
  Untether's stall/liveness watchdogs are inert for a stdout-silent agy run, so this is the
  only timeout that governs it. Tune via `[antigravity] print_timeout` (Go duration syntax).
- **No USD cost / budgets** — tokens only.
- **Model footer may misreport** — the envelope has no `model` field and `agy` silently ignores
  an invalid `--model`; the footer reflects the *configured* model.

## Model catalog

`agy models` lists available models, e.g. `Gemini 3.5 Flash (Low|Medium|High)`,
`Gemini 3.1 Pro (Low|High)`, `Claude Sonnet 4.6 (Thinking)`, `Claude Opus 4.6 (Thinking)`,
`GPT-OSS 120B (Medium)`. The reasoning tier is baked into the model name — there is no separate
effort flag.
