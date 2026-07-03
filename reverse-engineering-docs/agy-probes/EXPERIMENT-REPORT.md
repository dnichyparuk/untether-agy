# Antigravity CLI (`agy`) — Experiment Report

**Purpose.** Empirically capture `agy`'s data structures, requests, and responses to validate
the doc-derived claims in [`../antigravity-cli-runner-feasibility.md`](../antigravity-cli-runner-feasibility.md)
and determine what an Untether runner could actually consume.

| | |
|---|---|
| **Host** | Linux/WSL2 (`x86_64`) |
| **Binary** | `~/.local/bin/agy` |
| **Version** | **1.0.16** (matches the version the feasibility report analysed) |
| **Auth** | pre-authenticated (OS keyring / Google session) |
| **Date** | 2026-07-03 |
| **Model for live calls** | `Gemini 3.5 Flash (Low)` (cheapest) unless noted |
| **Token discipline** | tiny marker prompts, cheapest model, ~7 short model calls total |

Raw artifacts for every invocation live under `results/<timestamp>/` (`.out`/`.err`/`.meta`/
`.hex`). A condensed evidence sheet is in [`EVIDENCE-1.0.16.md`](./EVIDENCE-1.0.16.md).

---

## 1. Methodology

1. **Safe-first.** All flag/version/help/model-list discovery uses **zero model calls**.
2. **Parse-only flag discrimination.** `agy` (Go `flag` package) rejects unknown flags with
   `flags provided but not defined: -X` and lists *all* undefined flags in one line. To detect a
   *hidden* flag we append a guaranteed-undefined trailing flag and check whether the candidate
   names itself in the error — no model call needed.
3. **Isolated workspace.** Every live prompt runs in a fresh `mktemp -d` with `</dev/null` so
   `agy` cannot edit real files or block on input.
4. **Minimal prompts** ("reply with the single word OK") on the cheapest model to bound cost.

---

## 2. Experiment log (requests → responses)

Each entry: **request** (exact command) → **response** (verbatim, ANSI-stripped) + exit code.

### E1 — version  *(no model call)*
```
$ agy --version
1.0.16
```
Exit 0. `--version` → **stdout**.

### E2 — root help  *(no model call)*
```
$ agy --help          # NOTE: output goes to STDERR, exit 0
```
Response: see §3.1 (the full flag table). `--help`/`-h`/`help` all print usage to **stderr**.

### E3 — unknown-flag rejection  *(no model call)*
```
$ agy --bogus-flag-xyz
flags provided but not defined: -bogus-flag-xyz
Usage of agy: ...
```
Proves the parser rejects undefined flags → **absence from `--help` = not defined** … *unless the
flag is hidden* (E4).

### E4 — hidden-flag discrimination for `--output-format`  *(no model call)*
```
$ agy --output-format                        →  flag needs an argument: -output-format
$ agy --output-format json --agyprobe-undef  →  flags provided but not defined: -agyprobe-undef
$ agy --output json --x                       →  flags provided but not defined: -output -x
$ agy --stream-json --x                       →  flags provided but not defined: -stream-json -x
```
**`--output-format <value>` is DEFINED but omitted from `--help`.** `--output`, `--json`,
`--stream-json`, `--format`, `--verbose`, `--debug` are genuinely undefined.

### E5 — model catalog  *(no model call)*
```
$ agy models
Gemini 3.5 Flash (Medium) / (High) / (Low)
Gemini 3.1 Pro (Low) / (High)
Claude Sonnet 4.6 (Thinking)
Claude Opus 4.6 (Thinking)
GPT-OSS 120B (Medium)
```
Exit 0, **stdout**. Model names include a parenthetical tier, e.g. `Gemini 3.1 Pro (High)`.

### E6 — plain `-p` on a pipe (non-TTY), no format  *(live)*
```
$ agy -p "reply with the single word PING" --model "Gemini 3.5 Flash (Low)"
PING
```
Exit 0. **Plain text**, produced fine through a `sed` pipe → **non-TTY output is NOT suppressed**
on this host/version; **no PTY required**.

### E7 — `--output-format json` result envelope  *(live — decisive)*
```
$ agy -p "reply with the single word OK" --output-format json --model "Gemini 3.5 Flash (Low)"
{"conversation_id":"c0d91872-52f3-4ff8-bc71-965b7a264c66","status":"SUCCESS",
 "response":"OK\n","duration_seconds":2.155,"num_turns":1,
 "usage":{"input_tokens":16791,"output_tokens":4,"thinking_tokens":0,"total_tokens":16795}}
```
Exit 0. A **single valid JSON object** (not JSONL) on a pipe. See §3.4 for the schema.

### E8 — resume round-trip via `--conversation <id>`  *(live)*
```
$ agy -p "What single word did I ask you to reply with a moment ago? Reply with just that word." \
      --conversation c0d91872-52f3-4ff8-bc71-965b7a264c66 --output-format json --model "Gemini 3.5 Flash (Low)"
{"conversation_id":"c0d91872-...","status":"SUCCESS","response":"OK\n",
 "duration_seconds":51.25,"num_turns":2,"usage":{"input_tokens":33758,"output_tokens":5,...}}
```
Exit 0. Same `conversation_id`, `num_turns` **incremented to 2**, prior "OK" **recalled** →
**per-session resume works and the id is surfaced** (refutes Issue #7 for 1.0.16).

### E9 — lenient value validation  *(live)*
```
$ agy --output-format zzznotreal -p "hi"      → plain-text greeting (invalid format value ignored)
$ agy -p "hi" --model "NoSuchModelXYZ" --output-format json
  {"conversation_id":"8d19cd8e-...","status":"SUCCESS","response":"I can help you build ...",
   "num_turns":1,"usage":{"input_tokens":16780,"output_tokens":344,"thinking_tokens":219,"total_tokens":17124}}
```
Exit 0 for both. **`agy` does not validate `--output-format` or `--model` values** — unknown
values fall back to defaults rather than erroring. `thinking_tokens` can be > 0.

### E10 — exit codes  *(live)*
Both plain `-p` and `--output-format json` success paths return **exit 0**. A *failure* envelope
(`status != "SUCCESS"`) was **not reproducible cheaply** (bad flag values are ignored per E9), so
the failure shape remains unconfirmed — see §3.6.

---

## 3. Data structures

### 3.1 `agy --help` — flag set (v1.0.16, printed to stderr)

| Flag | Alias | Description | Value |
|------|-------|-------------|-------|
| `--print` | `-p` | Run a single prompt non-interactively and print the response | — (headless one-shot) |
| `--prompt` | | Alias for `--print` | (string prompt) |
| `--prompt-interactive` | `-i` | Run an initial prompt interactively, then continue | (string) |
| `--continue` | `-c` | Continue the **most recent** conversation | — |
| `--conversation` | | Resume a previous conversation **by ID** | `<id>` |
| `--model` | | Model for the current CLI session | `<name>` (see E5) |
| `--sandbox` | | Run in a sandbox with terminal restrictions | — |
| `--dangerously-skip-permissions` | | **Auto-approve all tool permission requests** | — |
| `--print-timeout` | | Timeout for print-mode wait | duration (default `5m0s`) |
| `--project` / `--new-project` | | Set / create the session's project | `<id>` / — |
| `--add-dir` | | Add a directory to the workspace (repeatable) | `<path>` |
| `--log-file` | | Override CLI log file path | `<path>` |
| **`--output-format`** | | **(hidden — not in help)** structured output selector | e.g. `json` |

**Subcommands:** `changelog`, `help`, `install`, `models`, `plugin`/`plugins`, `update`.

### 3.2 Flag existence matrix (confirmed vs refuted)

| Flag | Method | Result |
|------|--------|--------|
| `-p`/`--print`, `--continue`/`-c`, `--conversation`, `--model`, `--sandbox`, `--project`, `--add-dir`, `--log-file`, `--print-timeout`, `--dangerously-skip-permissions` | in `--help` | **present** |
| `--output-format` | hidden-flag discriminator (E4) | **defined (hidden)** |
| `--yes` | absent from help; `flag.yes=absent` | **does not exist** (auto-approve = `--dangerously-skip-permissions`) |
| `--output`, `--json`, `--stream-json`, `--format` | discriminator (E4) | **not defined** |
| `--prompt-file`, `--no-color`, `--max-turns` | absent from help | **not defined** |

### 3.3 Model catalog (from `agy models`)
```
Gemini 3.5 Flash (Medium|High|Low), Gemini 3.1 Pro (Low|High),
Claude Sonnet 4.6 (Thinking), Claude Opus 4.6 (Thinking), GPT-OSS 120B (Medium)
```

### 3.4 JSON result envelope — `--output-format json` (SUCCESS)

A **single JSON object** emitted at completion (not a stream). Field types verified via `jq`:

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `conversation_id` | string (UUID) | `c0d91872-52f3-4ff8-bc71-965b7a264c66` | **stable across resume**; the resume token |
| `status` | string | `SUCCESS` | only `SUCCESS` observed (see §3.6) |
| `response` | string | `"OK\n"` | the assistant's answer text (trailing `\n`) |
| `duration_seconds` | number | `2.155205718` | wall-clock |
| `num_turns` | number | `1`, then `2` on resume | increments across a resumed conversation |
| `usage` | object | see 3.5 | token accounting |

Canonical example:
```json
{
  "conversation_id": "b1986577-117c-4820-9370-61405903353c",
  "status": "SUCCESS",
  "response": "OK\n",
  "duration_seconds": 1.24537189,
  "num_turns": 1,
  "usage": { "input_tokens": 16795, "output_tokens": 6, "thinking_tokens": 0, "total_tokens": 16801 }
}
```

### 3.5 `usage` sub-object

| Field | Type | Notes |
|-------|------|-------|
| `input_tokens` | number | large even for tiny prompts (~16.8k) — system prompt + context |
| `output_tokens` | number | |
| `thinking_tokens` | number | can be > 0 (e.g. 219) |
| `total_tokens` | number | sum |
| ~~`total_cost_usd`~~ | **absent** | **no cost in USD** — only tokens |

### 3.6 Failure envelope — UNCONFIRMED

Bad `--model`/`--output-format` values are silently ignored (E9), so a `status != "SUCCESS"`
response could not be produced cheaply. The failure shape (whether `status` carries an error code,
whether an `error` field appears, and the process exit code on failure) is **not yet observed** and
should be captured opportunistically from a genuinely failing run (e.g. quota exhaustion, a tool
denied under a strict permission mode, or a malformed request).

### 3.7 Statusline / title **hook** JSON (TUI-only — NOT the `-p` stream)

From the vendor `examples/{statusline,title}/*.sh`, `agy` pipes a rich state payload **on stdin to
a hook script** during interactive sessions. This is a *rendering* channel, not the headless
result stream, and is (almost certainly) not emitted in `-p` mode — but it documents the engine's
internal state model:

| Field | Type | Values / notes |
|-------|------|----------------|
| `agent_state` | string | `initializing` \| `idle` \| `thinking` \| `working` \| `tool_use` |
| `context_window.used_percentage` | number | |
| `vcs.branch`, `vcs.dirty` | string, bool | |
| `sandbox.enabled` | bool | |
| `artifact_count`, `task_count` | number | |
| `subagents` | array | length = active subagents |
| `model.display_name` | string | |
| `workspace.current_dir` | string | |
| `terminal_width` | number | |

It exposes a `tool_use` state but **not** tool details/paths/answer — coarse telemetry only.

---

## 4. Canonical request/response templates (for a runner)

```bash
# One-shot, structured (recommended):
agy -p "<PROMPT>" --output-format json --model "<MODEL>"
#   → {conversation_id, status, response, duration_seconds, num_turns, usage{...}}

# Resume the most recent conversation (machine-global):
agy -p "<PROMPT>" --output-format json --continue

# Resume a specific conversation (id captured from a prior envelope):
agy -p "<PROMPT>" --output-format json --conversation <conversation_id>

# Auto-approve tools (headless, non-interactive):
agy -p "<PROMPT>" --output-format json --dangerously-skip-permissions
```
All emit the §3.4 envelope; stdin should be `</dev/null`; exit 0 on success.

---

## 5. Mapping to the Untether event model

| Untether | Source in the `agy` JSON envelope |
|----------|-----------------------------------|
| `StartedEvent.resume` | `ResumeToken(engine="antigravity", value=conversation_id)` |
| `StartedEvent.meta` | `{"model": <configured model>}` |
| `ActionEvent`s | **none available** — envelope is terminal-only, no per-tool events |
| `CompletedEvent.answer` | `response` |
| `CompletedEvent.ok` | `status == "SUCCESS"` |
| `CompletedEvent.usage` | `{usage: {input_tokens, output_tokens, cache?, ...}}` (map tokens; **no USD**) |

Because the envelope arrives once at the end, the runner would parse a **single JSON document at
stream end** and emit `StartedEvent` + `CompletedEvent` together — no live `ActionEvent` progress.

---

## 6. Reconciliation vs the doc-based feasibility report

| Report claim (doc/changelog based) | Experiment result | Corrected? |
|---|---|---|
| "Plain text only; no structured output" | **`--output-format json` yields a full envelope** | ✅ overturned |
| "No session id surfaced (Issue #7)" | **`conversation_id` present; resume round-trips** | ✅ overturned |
| "Non-TTY output suppressed; PTY mandatory" | **pipe output works; no PTY needed** (1.0.16 Linux) | ✅ overturned |
| "No usage/cost" | **token usage present** (no USD) | 🟡 partly overturned |
| "Auto-approve via `--yes`" | **`--dangerously-skip-permissions`** (no `--yes`) | ✅ corrected |
| "No live tool progress (no ActionEvents)" | confirmed — envelope is terminal-only | ✅ holds |
| "No interactive control channel" | confirmed — approval is flag/mode-based | ✅ holds |

**Net:** a real non-interactive Antigravity runner is **substantially more feasible** than the
doc-based report concluded — close to Gemini-runner parity, minus streaming `ActionEvent`s and
USD cost.

---

## 7. Reproduce

```bash
cd reverse-engineering-docs/agy-probes
./run_all.sh            # safe: version/help/flags/models (no model calls)
./run_all.sh --live     # + output-shape, non-TTY, resume (cheap Flash-Low calls)
# results in results/<timestamp>/ ; capability-report.md summarises
```
