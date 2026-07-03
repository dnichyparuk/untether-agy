# Feasibility Report — Adding an Antigravity CLI Runner to Untether

**Status:** Proposal / feasibility analysis · **Date:** 2026-07-03 · **Antigravity CLI version examined:** 1.0.16 (released 2026-07-02)

This report evaluates whether Untether can support Google's **Antigravity CLI** (binary `agy`) as a
new engine runner, by mapping the CLI's *current* capabilities against the integration features
Untether's existing runners rely on. It concludes with a concrete, scoped implementation proposal.

> ## ⚠️ EMPIRICAL UPDATE (2026-07-03) — several conclusions below are OVERTURNED
>
> This report was written from docs + changelog. It was then **validated on a host running
> `agy` 1.0.16**, and the live behaviour is materially better than the docs implied. See the
> full lab write-up in [`agy-probes/EXPERIMENT-REPORT.md`](./agy-probes/EXPERIMENT-REPORT.md)
> and [`agy-probes/EVIDENCE-1.0.16.md`](./agy-probes/EVIDENCE-1.0.16.md). Key reversals:
>
> - **Structured output EXISTS.** `agy -p "…" --output-format json` returns a JSON result
>   envelope `{conversation_id, status, response, duration_seconds, num_turns, usage{…}}`.
>   `--output-format` is a real flag, just **hidden from `--help`**. → "plain text only" is wrong.
> - **Session id IS surfaced.** `conversation_id` is in the envelope and `--conversation <id>`
>   resume round-trips (verified). → the Issue #7 blocker does **not** apply to 1.0.16.
> - **No PTY needed.** Pipe (non-TTY) output is not suppressed on Linux 1.0.16.
> - **Token usage IS available** (no USD). Auto-approve flag is **`--dangerously-skip-permissions`**
>   (there is no `--yes`).
> - **Still true:** the envelope is terminal-only (a single blob), so there are **no streaming
>   `ActionEvent`s / live tool progress**, and there is **no interactive control channel**.
>
> **Revised verdict:** a real non-interactive runner is now **close to Gemini-runner parity**
> (real resume token + answer + token usage, no PTY), minus live progress and USD cost — far
> better than the "minimal, degraded" conclusion in the sections below. Sections §2a onward are
> retained as the original doc-based analysis; where they conflict with the empirical findings,
> **the empirical findings win.**

> Cross-reference: the integration substrate this report measures against is documented in
> [02 — CLI Integration Model](./02-cli-integration-model.md); the non-interactive-engine pattern
> in [04 — Gemini CLI Integration](./04-gemini-integration.md) and
> [05 — Other Engines](./05-other-engines.md); orchestration in
> [06 — Orchestration & Transport](./06-orchestration-and-transport.md).

---

## 1. Executive summary & verdict

**Verdict: Feasible, but only as a *minimal non-interactive* runner — materially more degraded
than any engine Untether ships today, and requiring one technique no non-Claude runner currently
uses (a PTY).**

Two decisive facts about the current `agy` (v1.0.16) drive this:

1. **No structured output.** `agy -p` (headless "print" mode) emits **plain text only**. There is
   **no `--output-format json`** that works — passing it is rejected with
   `flags provided but not defined: -output-format`; there is **no result envelope** (no
   `session_id`, cost, turn count, or per-tool events).
   [[non-TTY article]](#ref-nontty) [[Hermes ref]](#ref-hermes) [[search]](#ref-search2)
2. **No session ID is surfaced in headless mode.** The conversation identifier "is never surfaced
   in stdout, stderr, or any documented file," so headless callers cannot capture a token to resume
   a *specific* session. Only `--continue` (most-recent, **machine-global**) works unattended.
   [[Issue #7]](#ref-issue7)

A third fact makes even the plain-text path non-trivial:

3. **Non-TTY output suppression.** `agy` checks at startup whether stdout is a TTY; when stdout is a
   pipe (which is exactly how `JsonlSubprocessRunner` spawns every engine), it **disables its
   renderer and can return exit 0 with no output at all** ("succeeded but did nothing"). The
   documented workaround is to allocate a **pseudo-terminal** (`script`/`unbuffer`) and strip ANSI.
   [[non-TTY article]](#ref-nontty)

Consequently, `JsonlSubprocessRunner` — whose whole design assumes newline-delimited JSON on a
piped stdout — cannot be used as-is. The core UntetherEvent stream (`StartedEvent → ActionEvent* →
CompletedEvent`) can only be *partially* satisfied: a synthetic `StartedEvent` (self-generated
token) and a single `CompletedEvent` carrying the plain-text answer. **No live tool/file progress
(`ActionEvent`s), no cost/usage, no per-session resume, no interactive approval, no plan mode, no
AskUserQuestion** are possible against the current CLI.

**Recommendation:** implement a **thin, clearly-labelled experimental runner** now (answer-only,
PTY-wrapped, `/continue`-only resume), and gate the richer capabilities behind two upstream
milestones to watch: a stable **`--output-format json`** and **Issue #7** (headless conversation-ID
emission). Both are exactly the primitives Gemini CLI already exposes and that Untether's Gemini
runner depends on — so if Google brings `agy` to Gemini-CLI parity, a full-featured runner becomes
straightforward.

---

## 2. Methodology & source reliability

The **official documentation** at `antigravity.google/docs/cli-*` is a client-side-rendered SPA;
automated fetches return only the page shell, so flag-level facts below are corroborated from a
combination of:

- **Primary (high confidence):** the GitHub repository, its `CHANGELOG.md`, and issue tracker
  (`google-antigravity/antigravity-cli`).
- **Secondary (medium confidence):** an operational agent-skill reference (Hermes/Nous), a
  hands-on guide (DEV Community), and two focused engineering write-ups (Antigravity Lab).
- **Tertiary (low confidence, flagged inline):** a "design before it hits CI" article whose
  `antigravity run --prompt-file` syntax is **not** corroborated by the changelog/issue tracker and
  is treated as aspirational.

Where sources conflict (notably authentication), the conflict is called out explicitly. All URLs
are in [§10 References](#10-references). Facts are tagged with a source marker like [[Issue #7]](#ref-issue7).

---

## 2a. Source validation against the cloned repository

The repository `google-antigravity/antigravity-cli` was cloned to `../antigravity-cli/` (relative to
the repo root) and inspected. **Finding: the repo ships no CLI source code** — only `README.md`,
`CHANGELOG.md` (all 16 versions, 1.0.0–1.0.16), a demo GIF, and `examples/{statusline,title}/`
integration scripts. The `agy` binary itself is closed-source, distributed via `install.sh`. So
"validate against source code" resolves to validating against the **vendor-authored changelog and
example hooks** — which is nonetheless the strongest primary evidence available and it corroborates
(and sharpens) the findings above.

### Confirmed by the vendor changelog (`antigravity-cli/CHANGELOG.md`)

| Claim | Verdict | Evidence (line) |
|---|---|---|
| Binary is `agy` | ✅ confirmed | `agy changelog` (`:175`) |
| Headless one-shot `-p` / `--print` | ✅ confirmed | `:21`, `:102`, `:151`, `:158` |
| **Non-TTY stdout is silently discarded** | ✅ **confirmed (vendor-acknowledged)** | "print mode … outputs were silently discarded when run in non-TTY environments (such as pipes or subprocesses)" (`:21`) — **fixed *on Windows* in 1.0.16**; POSIX behaviour is version-dependent and must be tested |
| Headless resume via `--conversation`/`-c` with `-p` | ✅ confirmed | `:102` |
| `--model` + `models` subcommand | ✅ confirmed | `:155` |
| `--sandbox` works in headless `-p` | ✅ confirmed | `:151` |
| `--project` / `--new-project` | ✅ confirmed | `:48` |
| Config/state under `~/.gemini/antigravity-cli/` | ✅ confirmed | `:52`, `:111`, `:158`, `:170` |
| Conversations stored as SQLite (`.db`/`.db-wal`) | ✅ confirmed | `:162`, `:168` |
| `-p` runs write **metadata to a cache dir** (`~/.gemini/antigravity-cli/cache`) | ✅ confirmed | `:158` |
| MCP via `mcp_config.json` (+ `url`, timeouts) | ✅ confirmed | `:161`, `:185` |

### Refuted / not corroborated (third-party only — confidence downgraded)

A full-text search of the changelog (all versions) found **zero** occurrences of the following;
they appear only in third-party write-ups and should be treated as **unverified**:

| Third-party claim | Changelog evidence | Corrected reading |
|---|---|---|
| `--output-format json` / `stream-json` / any JSON output | **0 hits** | **No structured output exists in any released version** — the "plain text only" finding is now backed by strong primary negative evidence |
| `--yes` auto-approve flag | **0 hits** | Auto-approval is via **permission *modes*** — `"always proceeds"` for subagents (`:30`), `proceed-in-sandbox` (`:209`) — **not** a `--yes` flag. The exact headless bypass flag is unconfirmed |
| `--print-timeout`, `--prompt-file`, `--add-dir`, `--no-color` | **0 hits** | unconfirmed by vendor; do not rely on these flag names without `agy --help` on the host |
| `ANTIGRAVITY_API_KEY` / any API-key auth | **0 hits** | **No API-key auth appears in any vendor source.** Auth is keyring → Google OAuth (→ GCP for enterprise). The headless API-key path is a third-party claim only, and sources even disagree on the var name — treat as **not available until proven** |

### New nuance #1 — `agy` *does* emit rich structured JSON, but only via TUI hooks

`examples/statusline/statusline.sh` and `examples/title/title.sh` read a **JSON payload on stdin**
(the CLI's statusline/title **hook** channel) with a genuinely rich schema:

```
agent_state            # "idle" | "thinking" | "working" | "tool_use" | "initializing"
context_window.used_percentage
vcs.branch, vcs.dirty
sandbox.enabled
artifact_count, task_count, subagents[]
model.display_name
workspace.current_dir, terminal_width
```

This is the closest thing to structured telemetry `agy` produces — it even exposes a `tool_use`
state. **But it is a TUI-rendering hook, not the `-p` result stream**, and is (almost certainly)
not invoked in headless mode. It carries *coarse state*, **not** tool details, file paths, tokens,
or the answer text. So it does **not** rescue the headless integration — though it hints that the
agent engine has structured internals that a future `--output-format json` could expose. (Verdict
on the core finding stands; this is context, not a contradiction.)

### New nuance #2 — a fragile resume path may exist via the cache/SQLite

Because `-p` writes **metadata to `~/.gemini/antigravity-cli/cache`** (`:158`) and conversations to
**SQLite** (`:162`/`:168`), a wrapper could in principle recover a conversation ID by reading those
files — exactly the "parse undocumented state files" workaround acknowledged in
[Issue #7](#ref-issue7) as *fragile and incomplete*. This is not a supported interface and should
not be built on; it only slightly softens "resume is impossible" to "resume is unsupported and
hacky."

**Net effect on the verdict:** unchanged. The two decisive blockers (§7) are now *confirmed by the
vendor's own changelog* rather than inferred, and several third-party flag claims are demoted. The
"minimal non-interactive runner" proposal (§8) stands, with the auto-approve mechanism restated as
a *permission mode* rather than a `--yes` flag, and the API-key auth path removed as unsubstantiated.

## 3. What the Antigravity CLI is

> "Antigravity CLI brings the reasoning, execution, and orchestration capabilities of Antigravity
> agent harness directly into your terminal." — repository description [[GitHub]](#ref-github)

- **Binary / command:** `agy` [[Hermes ref]](#ref-hermes) [[DEV guide]](#ref-dev)
- **Install:** `curl -fsSL https://antigravity.google/cli/install.sh | bash` (macOS/Linux);
  PowerShell/CMD variants for Windows [[GitHub]](#ref-github)
- **Version examined:** 1.0.16, 2026-07-02; ~1.4k stars, active issue tracker [[GitHub]](#ref-github)
- **Models:** Gemini 3.x (Flash/Pro), Claude Sonnet/Opus, GPT-OSS 120B, and custom models
  [[DEV guide]](#ref-dev)
- **Config/state dir:** `~/.gemini/antigravity-cli/` (settings, logs, conversations); conversations
  stored as **SQLite `.db`** since 1.0.4 [[Hermes ref]](#ref-hermes) [[CHANGELOG]](#ref-changelog)
- **Positioning:** a full interactive TUI agent (like Gemini CLI / Claude Code) with a headless
  "print" mode bolted on; headless is clearly **less mature** than the TUI.

---

## 4. Antigravity CLI capability facts (as of 1.0.16)

Each row notes the evidence and our confidence.

| Capability | State in `agy` 1.0.16 | Evidence · confidence |
|---|---|---|
| Headless one-shot | `agy -p "<prompt>"` / `--print`; prompt passed as **argv** | [[DEV]](#ref-dev) [[Hermes]](#ref-hermes) [[CHANGELOG]](#ref-changelog) · high |
| Prompt via file | `--prompt-file <path>` claimed by one article; **not** corroborated | [[design article]](#ref-design) · low |
| Output format | **Plain text only.** `--output-format json` **rejected / not defined**; no result envelope | [[non-TTY]](#ref-nontty) [[Hermes]](#ref-hermes) [[search]](#ref-search2) · high |
| Streaming | No JSONL/stream-json event stream | inferred from above · high |
| Non-TTY behaviour | Detects non-TTY stdout and **suppresses output** (exit 0, empty) | [[non-TTY]](#ref-nontty) · high |
| Model selection | `--model <name>` / `-m`; `models` subcommand (added 1.0.5) | [[CHANGELOG]](#ref-changelog) [[DEV]](#ref-dev) [[Hermes]](#ref-hermes) · high |
| Resume (specific) | `--conversation <id>` — but **ID is never surfaced** in headless output | [[Issue #7]](#ref-issue7) [[Hermes]](#ref-hermes) · high |
| Resume (latest) | `--continue` / `-c` — **machine-global** most-recent conversation | [[Issue #7]](#ref-issue7) [[CHANGELOG]](#ref-changelog) · high |
| Auto-approve | via **permission *modes*** (`always-proceed`, `proceed-in-sandbox`), **not** a `--yes` flag (changelog has none); `--sandbox`; `/permissions` | [[CHANGELOG]](#ref-changelog) (`:30`,`:209`) · med — `--yes`/`--dangerously-skip-permissions` are third-party only, **unconfirmed** |
| Permission modes | `request-review`, `always-proceed`, `strict`, `proceed-in-sandbox`; strict rule-matching default (1.0.13) | [[Hermes]](#ref-hermes) [[CHANGELOG]](#ref-changelog) · med |
| Timeout | `--print-timeout <dur>` (default `5m`) claimed; **no `--max-turns`**; both **unconfirmed** by changelog | [[Hermes]](#ref-hermes) · low |
| Interactive control channel | **None** — approval is via flags or TUI slash commands only | [[Hermes]](#ref-hermes) · high |
| MCP | `mcp_config.json` (+ `url`, timeouts); `/mcp` **interactive only**; `agy plugin …` | [[CHANGELOG]](#ref-changelog) [[Hermes]](#ref-hermes) · med |
| Extra flags | `--no-color`, `--add-dir`, `--project`/`--new-project`, `--log-file`, `--sandbox` | [[CHANGELOG]](#ref-changelog) [[Hermes]](#ref-hermes) · med |
| Auth | **OS keyring → Google OAuth sign-in** (SSH prints an auth URL); GCP for enterprise. **No API-key path in any vendor source** (changelog: 0 API-key hits); third-party API-key claims conflict (`ANTIGRAVITY_API_KEY` vs `GEMINI_API_KEY` vs none) and are **unverified** | [[GitHub]](#ref-github) [[CHANGELOG]](#ref-changelog) · high (vendor) / low (API-key claims) |

---

## 5. What Untether's runner substrate requires

From [02 — CLI Integration Model](./02-cli-integration-model.md), a normal engine must provide:

1. A binary (`command()`) and argv (`build_args()`). ✔ trivially satisfiable.
2. **Newline-delimited JSON on stdout**, decoded per line (`decode_jsonl`) and mapped by
   `translate()`. ✘ `agy` emits plain text.
3. Enough signal to emit the **3-event contract**:
   - a `StartedEvent` carrying a **`ResumeToken(engine, value)`** (the session id) — used to seed
     the session lock and the resume footer;
   - `ActionEvent`s for tool calls / file changes / notes (the live progress);
   - one `CompletedEvent` with the **answer** and, ideally, **usage/cost**.
4. Spawn compatibility: the base `run_impl` spawns with `stdout=PIPE` (a non-TTY). ✘ triggers
   `agy`'s output suppression.

Optional-but-common features layered on top: interactive approval (Claude only), `/continue`
resume, model/permission footer, cost tracking, MCP catalog observability.

---

## 6. Feature support matrix — Untether ⇄ Antigravity

Legend: ✅ supported · 🟡 partial/degraded · ❌ blocked by current `agy` · ➖ N/A.

| Untether runner feature | Antigravity `agy` 1.0.16 | Notes |
|---|---|---|
| `command()` / `build_args()` | ✅ | `agy -p <prompt>` + `--model`/`--sandbox` (auto-approve via permission mode) |
| JSONL stdout → `translate()` | ❌ | plain text only; no per-line JSON. The `JsonlSubprocessRunner` machinery (`decode_jsonl`, schema, `_classify_jsonl_event`) does not apply |
| `StartedEvent` with real session id | ❌ | no session id surfaced; must synthesize a placeholder token (precedent: Pi self-generates, see [05](./05-other-engines.md)) |
| `ActionEvent`s (live tool/file progress) | ❌ | no structured per-tool events in headless text output |
| `CompletedEvent.answer` | 🟡 | available as accumulated plain text — **only after a PTY workaround** |
| `CompletedEvent.usage` (cost/tokens) | ❌ | no usage envelope; cost tracking + budget alerts won't populate |
| Resume — `/continue` (latest) | 🟡 | `--continue` works but is **machine-global most-recent** → unsafe across concurrent chats ([Issue #7]) |
| Resume — specific session token | ❌ | no ID emitted; can't round-trip a `ResumeToken.value` |
| Session locking (`SessionLockMixin`) | 🟡 | works on a synthetic token, but a global `--continue` undermines its guarantee |
| Model / permission footer (`meta`) | ✅ | `--model` known at launch → put in `StartedEvent.meta` |
| Interactive approval (buttons) | ❌ | no control channel; approval is permission-mode-driven or TUI-only |
| Plan mode / `ExitPlanMode` | ❌ | Claude-specific protocol; `agy` has none |
| AskUserQuestion | ❌ | no equivalent headless protocol |
| Auto-continue (stuck-after-tool_result) | ➖ | Claude-specific (relies on JSONL `last_event_type`); inapplicable |
| Non-interactive spawn (PIPE stdout) | ❌→🟡 | must allocate a **PTY** (like Claude's legacy PTY mode) to defeat non-TTY suppression |
| Env allowlist, worktrees, preamble, outbox delivery | ✅ | engine-agnostic orchestration ([06](./06-orchestration-and-transport.md)) — work unchanged |
| MCP catalog refresh/observability (#365) | ❌ | Claude-control-channel-specific |
| Headless authentication | 🟡 | operator must pre-authenticate the keyring on the host, or supply an API key (env var **unconfirmed**); browser OAuth won't work unattended |

**Score:** of the capabilities that distinguish Untether's runners, roughly ✅ 4 · 🟡 4 · ❌ 8.
The ✅/🟡 set is enough for a basic "send prompt, get answer" bridge; the ❌ set is everything that
makes the experience rich.

---

## 7. The two decisive blockers (deep dive)

### 7.1 Non-TTY output suppression → mandatory PTY

`agy` disables its renderer when stdout isn't a terminal and can exit 0 with **no output**
[[non-TTY]](#ref-nontty). Untether's base `run_impl` always uses `stdout=PIPE`
([02 §run loop](./02-cli-integration-model.md)), so a naïve runner would reliably capture nothing.

The documented fix is a pseudo-terminal:

```bash
# Linux (util-linux script): -e propagates the child exit code
script -qec 'agy -p "…"' /dev/null | sed -r 's/\x1B\[[0-9;]*[A-Za-z]//g' | tr -d '\r'
```

Untether already contains the ingredients: **`ClaudeRunner` opens a PTY** (`pty.openpty()` +
`tty.setraw`) in its legacy mode ([03 §PTY vs PIPE](./03-claude-integration.md)). The Antigravity
runner would need the *stdout* side on a PTY (Claude uses the PTY for *stdin* control). This is
novel for a non-Claude runner and means the Antigravity runner cannot simply reuse the base
`run_impl` — it must override spawning (allocate a PTY for the child's stdout, read from the master
fd, strip ANSI/CR) rather than inherit the PIPE path.

### 7.2 No structured output / no session id → degraded events & resume

Because output is opaque text:
- There is nothing to translate into `ActionEvent`s → **no live progress** (the Telegram message
  would sit "working…" then show the final answer).
- There is no `usage` → **cost tracking/budgets stay empty** (acceptable — some engines already
  provide partial usage — but worth noting).
- There is no session id → the `ResumeToken.value` can only be a **self-generated placeholder**
  (Pi precedent), and specific-session resume is impossible. `--continue` is the only unattended
  resume and it is machine-global, so in a multi-chat deployment it can resume the *wrong* chat's
  conversation ([Issue #7]). Recommendation: **support `/continue` only with a loud caveat**, or
  disable resume entirely for correctness.

---

## 8. Proposed solution — a minimal `AntigravityRunner`

### 8.1 Shape

Follow the standard registration path from
[02 §Registration](./02-cli-integration-model.md#registration-enginebackend--python-entry-points):

- New module `src/untether/runners/antigravity.py` exporting
  `BACKEND = EngineBackend(id="antigravity", build_runner=…, cli_cmd="agy",
  install_cmd="curl -fsSL https://antigravity.google/cli/install.sh | bash")`.
- Register the entry point in `pyproject.toml` under
  `[project.entry-points."untether.engine_backends"]`:
  `antigravity = "untether.runners.antigravity:BACKEND"`.
- Add `src/untether/schemas/antigravity.py` only if/when structured output lands (not needed for
  the text-only v1).
- Reference docs under `docs/reference/runners/antigravity/` and tests mirroring
  `tests/test_*_runner.py` (per `.claude/rules/runner-development.md`).

### 8.2 Because it is text-not-JSONL, do NOT lean on `JsonlSubprocessRunner`'s decode path

Two viable structures:

**Option A (recommended): a text-oriented runner that overrides the spawn+read path.**
Subclass `ResumeTokenMixin` + `BaseRunner` (or `JsonlSubprocessRunner` but override `run_impl`),
and:
1. `command()` → `"agy"`; `build_args()` → `["-p", prompt, "--model", model,
   ("--sandbox" if configured), ("--continue" if resume.is_continue)]`. **Do not** hardcode `--yes`
   or `--print-timeout` (unconfirmed by the vendor changelog — verify with `agy --help` first);
   configure the auto-approve **permission mode** (`always-proceed` / `proceed-in-sandbox`) via
   `settings.json` or the confirmed flag once known.
2. Override spawning to allocate a **PTY for the child stdout** (mirror `ClaudeRunner`'s
   `pty.openpty()`/`tty.setraw` handling, but on the output side), read the master fd to EOF,
   strip ANSI + CR.
3. Emit a **synthetic `StartedEvent`** at first output (or at spawn) with a self-generated
   `ResumeToken(engine="antigravity", value=<uuid>, is_continue=…)` (Pi precedent) and
   `meta={"model": model}` for the footer.
4. On process exit, emit exactly one `CompletedEvent(ok=(rc==0 and non-empty), answer=<clean text>,
   resume=<token>, error=…)`. Honour the non-TTY-empty-output failure mode: **empty output with
   rc 0 must be treated as an error**, not success (per the "succeeded but did nothing" trap).

**Option B:** subclass `JsonlSubprocessRunner` and hack `decode_jsonl` to buffer text and emit
nothing until `stream_end_events`. Cleaner in theory but fights the base class's line/JSON
assumptions and the non-TTY spawn; not recommended.

### 8.3 Config (`[antigravity]`)

`build_runner(config, config_path)` validates and passes through:
`model: str | None`, `sandbox: bool = false`, `permission_mode: str | None` (maps to the
confirmed `always-proceed`/`proceed-in-sandbox` mode via `settings.json`), `extra_args:
list[str]`, `resume_enabled: bool = false` (default off because `--continue` is machine-global).
Avoid a `print_timeout`/`auto_approve` flag until confirmed against `agy --help`. Env handling
should reuse the allowlist policy (`utils/env_policy.py`) like Pi does.

### 8.4 What v1 explicitly does NOT do

No `ActionEvent` progress, no cost/usage, no interactive approval / plan mode / AskUserQuestion, no
per-session resume, no MCP observability, no auto-continue. The `/continue` command is either
disabled or shipped with a documented "resumes the host's most-recent conversation across all
chats" warning.

### 8.5 Authentication guidance (deployment)

Because Untether runs headless as a service, the operator must make `agy` non-interactively
authenticated on each host. **The vendor changelog and README document only keyring → Google OAuth
(→ GCP for enterprise) — no API-key path exists in any vendor source** (the changelog has zero
API-key references; third-party API-key claims even disagree on the variable name). Therefore the
supported approach is to **pre-authenticate the OS keyring once** (interactive login) on each host
so the daemon inherits the saved session. **Do not** assume the Claude-style "pop the API key for
subscription billing" trick applies — treat headless API-key auth as unavailable until proven with
`agy --help`/official docs [[README]](#ref-github) [[CHANGELOG]](#ref-changelog).

---

## 9. Roadmap triggers — when to upgrade the runner

Watch two upstream signals; each unlocks a tier:

| Upstream change | Unlocks in Untether |
|---|---|
| Stable **`--output-format json`** (streaming JSONL like Gemini CLI) | real `translate()`, `ActionEvent` live progress, `usage`/cost, drop the PTY hack — i.e. bring the runner to **Gemini-runner parity** ([04](./04-gemini-integration.md)) |
| **Issue #7** — headless conversation-ID emission | real `ResumeToken.value`, per-session resume, safe multi-chat `/continue`, meaningful session locking |
| A documented headless auth env var | reliable unattended auth without keyring pre-seeding |

If both of the first two land, the Antigravity runner collapses to essentially a clone of the
Gemini runner with a different binary and flag names — a low-effort, high-fidelity integration.
Until then, the honest ceiling is a **text-only, answer-only** bridge.

---

## 9a. Side-by-side with the existing Gemini runner

The **Gemini CLI runner** ([04](./04-gemini-integration.md), `src/untether/runners/gemini.py`) is
the closest existing analog — same vendor (Google), same non-interactive posture — and is exactly
the template an Antigravity runner would aspire to. The comparison below shows *why the Gemini
runner works today and an Antigravity runner can't reach the same fidelity yet*: the two engines
diverge on precisely the primitives Untether depends on.

| Dimension | Gemini runner (`gemini`) — **works today** | Antigravity runner (`agy`) — **current ceiling** |
|---|---|---|
| Binary | `gemini` | `agy` |
| Base class | `GeminiRunner(ResumeTokenMixin, JsonlSubprocessRunner)` — inherits base `run_impl` | can't inherit base `run_impl` (PIPE stdout ⇒ suppressed); must override spawn for a **PTY** |
| Prompt passing | `--prompt=<sanitized>` (argv); `stdin_payload=None` | `-p "<prompt>"` (argv); stdin `< /dev/null` |
| **Output** | `--output-format stream-json` → **JSONL events** | **plain text only**; `--output-format` rejected |
| **Event schema** | `schemas/gemini.py` msgspec union (`init/message/tool_use/tool_result/result/error`) | none — no schema, nothing to decode |
| `translate()` | maps each event → Started / Action / Completed (~170 lines) | no per-line translate; synthesize Started + one Completed from buffered text |
| **StartedEvent** | real `ResumeToken` from `init.session_id` | synthetic self-generated token (Pi precedent) |
| **ActionEvents** (live progress) | ✅ `tool_use`→started, `tool_result`→completed; snake_case names via `_TOOL_NAME_MAP` | ❌ none (no structured tool events) |
| **CompletedEvent.answer** | accumulated assistant `message` text from the pipe | accumulated **PTY** text, ANSI/CR-stripped |
| **usage / cost** | ✅ `_build_usage(stats)` — tokens, `duration_ms`, `total_cost_usd` | ❌ none |
| Model selection | `--model <m>` (run-option override) | `--model`/`-m` (same idea) |
| Approval (headless) | `--approval-mode yolo` + `--skip-trust` (headless-trust fix, #471) | permission mode (`always-proceed`/`proceed-in-sandbox`) + `--sandbox` |
| **Resume — latest** | `--resume latest` (`is_continue`) | `--continue` — but **machine-global** (unsafe multi-chat, Issue #7) |
| **Resume — specific** | `--resume <value>` (round-trips real session id) | `--conversation <id>` exists, but **id never surfaced** ⇒ can't round-trip |
| Resume footer regex | `_RESUME_RE` matches `` `gemini --resume <token>` `` | would match a synthetic token only; no real session to point at |
| State object | `GeminiStreamState` (pending_actions, last_text, session_id, emitted_started, model, saw_result) | minimal text accumulator + generated token |
| Terminal fallbacks | `process_error_events` / `stream_end_events` (3 cases incl. "no session_id", "no result") | simpler; must treat **empty-output + rc 0 as failure** (non-TTY trap) |
| Interactivity | none (neither has Claude's control channel) | none |
| Headless auth | Google auth / `GEMINI_API_KEY` | keyring→OAuth; API-key env **unconfirmed** |
| Footer meta | `{"model", "permissionMode"}` | `{"model"}` only |

**Bottom line:** an Antigravity runner is, feature-for-feature, **"the Gemini runner minus JSONL,
minus a surfaced session id, minus PIPE-compatibility."** Those three subtractions remove live
progress, usage/cost, and reliable resume, and add a PTY requirement the Gemini runner never needs.

Conversely, this makes the upgrade path crisp: **if `agy` ships a stable `--output-format
stream-json` and resolves [Issue #7](#ref-issue7) (headless conversation-ID), an Antigravity runner
becomes almost a line-for-line copy of `gemini.py`** with the flag names swapped (`--prompt=`→`-p`,
`--resume`→`--continue`/`--conversation`, `--approval-mode yolo`→ Antigravity permission mode) and
the PTY workaround deleted. The Gemini runner is therefore both the honest measuring stick *and* the eventual
implementation blueprint.

## 10. References

<a id="ref-github"></a>**[GitHub]** google-antigravity/antigravity-cli — repository (description, install, v1.0.16):
https://github.com/google-antigravity/antigravity-cli

<a id="ref-changelog"></a>**[CHANGELOG]** antigravity-cli `CHANGELOG.md` (versions 1.0.0–1.0.16; `--model`/`models` 1.0.5, headless print-mode resume `--conversation`/`-c` 1.0.9, `--sandbox` propagation 1.0.6, SQLite conversations 1.0.4, `/permissions` 1.0.5, strict rule matching 1.0.13, MCP `url`/timeouts):
https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md

<a id="ref-issue7"></a>**[Issue #7]** "feat(--print): emit per-conversation ID so headless callers can resume specific sessions" — confirms conversation ID is never surfaced in stdout/stderr/files; `--continue` is machine-global:
https://github.com/google-antigravity/antigravity-cli/issues/7

<a id="ref-overview"></a>**[Official docs]** Antigravity CLI overview / using / features / settings (JS-rendered SPA; not machine-scrapable — listed for completeness):
https://antigravity.google/docs/cli-overview · https://antigravity.google/docs/cli-using · https://antigravity.google/docs/cli-features · https://antigravity.google/docs/cli-settings

<a id="ref-hermes"></a>**[Hermes ref]** Hermes Agent — "Antigravity CLI (agy)" operational skill (binary, flags, plain-text output "no `--output-format json`", auth via keyring, sandbox/permission modes, config paths):
https://hermes-agent.nousresearch.com/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-antigravity-cli

<a id="ref-nontty"></a>**[non-TTY article]** Antigravity Lab — "Running the Antigravity CLI (agy) Headless in CI: Working Around the Non-TTY stdout Problem" (output suppression on non-TTY; `script`/`unbuffer` PTY workaround; ANSI stripping; `--output-format` rejected):
https://antigravitylab.net/en/articles/integrations/antigravity-cli-agy-headless-non-tty-stdout-ci

<a id="ref-design"></a>**[design article]** Antigravity Lab — "Running Antigravity CLI Headless: Design Before It Hits CI and cron" (`--yes`, `--no-color`, `--output json`, `--prompt-file`, `.status`/`.error` — treat `run`/`--prompt-file` as *unconfirmed*):
https://antigravitylab.net/en/articles/integrations/antigravity-cli-headless-non-interactive-ci-design

<a id="ref-dev"></a>**[DEV guide]** DEV Community (Arindam) — "Antigravity CLI: A Hands-On Guide to Google's Terminal Coding Agent" (binary `agy`, `-p`, `-m`/`--model`, `--continue`/`--conversation`, slash commands, models list):
https://dev.to/arindam_1729/antigravity-cli-a-hands-on-guide-to-googles-terminal-coding-agent-5bc7

<a id="ref-migration"></a>**[migration]** Prompt Genius — "Migration Guide: Gemini CLI to Antigravity CLI" (HTTP 403 at fetch time; listed as a lead for scripting/headless parity comparison):
https://www.promptgenius.net/blog/gemini-cli-to-antigravity-migration-guide

<a id="ref-statusline"></a>**[statusline]** Guillaume Laforge — "Customizing Antigravity CLI: Title and Status Line" (config/behaviour corroboration):
https://glaforge.dev/posts/2026/06/07/customizing-antigravity-cli-title-and-statusline/

<a id="ref-search2"></a>**[search]** Web search corroboration that `agy -p` returns plain text and `--output-format json` is unstable/undefined (aggregated result set, July 2026).

---

*Prepared as a scoped feasibility assessment. All CLI facts reflect Antigravity CLI 1.0.16
(2026-07-02) and should be re-verified against `agy --help` on the target host before
implementation, since the headless surface is changing rapidly.*
