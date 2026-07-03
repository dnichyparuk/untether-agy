# Empirical Evidence — agy 1.0.16 (captured on host)

Captured 2026-07-03 on Linux/WSL2, `agy` at `~/.local/bin/agy`, version **1.0.16**
(the exact version the feasibility report analysed). These raw observations
**supersede the doc/changelog-derived guesses** where they conflict.

## `agy --help` (writes to **stderr**, exit 0)

```
Usage of agy:
  --add-dir                       Add a directory to the workspace (repeatable) (default [])
  -c                              Short alias for --continue
  --continue                      Continue the most recent conversation
  --conversation                  Resume a previous conversation by ID
  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting
  -i                              Short alias for --prompt-interactive
  --log-file                      Override CLI log file path
  --model                         Model for the current CLI session
  --new-project                   Create a new project for this session
  -p                              Short alias for --print
  --print                         Run a single prompt non-interactively and print the response
  --print-timeout                 Timeout for print mode wait (default 5m0s)
  --project                       Project ID for the current CLI session
  --prompt                        Alias for --print
  --prompt-interactive            Run an initial prompt interactively and continue the session
  --sandbox                       Run in a sandbox with terminal restrictions enabled

Available subcommands:
  changelog  Show changelog and release notes
  help       Show help for subcommands
  install    Configure environment paths and shell settings
  models     List available models
  plugin     Manage plugins (install, uninstall, list, enable, disable)
  plugins    Alias for plugin
  update     Update CLI
```

## Hidden flag: `--output-format` EXISTS (not in --help)

Unknown flags are rejected (`agy --bogus` → `flags provided but not defined: -bogus`).
But:

```
$ agy --output-format                       # -> "flag needs an argument: -output-format"  (DEFINED)
$ agy --output-format json --agyprobe-undef # -> "not defined: -agyprobe-undef" only (output-format accepted)
$ agy --output json --x                      # -> "not defined: -output -x"     (--output NOT defined)
```

So `--output-format <value>` is a **real, defined, but undocumented** flag in 1.0.16.
`--output`, `--json`, `--stream-json`, `--format` are all genuinely undefined.

## `agy models` (stdout)

```
Gemini 3.5 Flash (Medium) / (High) / (Low)
Gemini 3.1 Pro (Low) / (High)
Claude Sonnet 4.6 (Thinking)
Claude Opus 4.6 (Thinking)
GPT-OSS 120B (Medium)
```

## `--output-format json` produces a STRUCTURED RESULT ENVELOPE

`agy -p "reply with the single word OK" --output-format json --model "Gemini 3.5 Flash (Low)"`
on a **plain pipe (non-TTY)**, exit 0, 231 bytes:

```json
{"conversation_id":"c0d91872-52f3-4ff8-bc71-965b7a264c66","status":"SUCCESS","response":"OK\n","duration_seconds":2.155205718,"num_turns":1,"usage":{"input_tokens":16791,"output_tokens":4,"thinking_tokens":0,"total_tokens":16795}}
```

Envelope fields observed: `conversation_id`, `status` (`SUCCESS`), `response` (the answer
text), `duration_seconds`, `num_turns`, `usage.{input_tokens, output_tokens,
thinking_tokens, total_tokens}`. **No `total_cost_usd`** (tokens only). It is a **single
JSON object** emitted at completion — not a streaming JSONL event feed.

## Resume round-trip WORKS (`--conversation <id>`)

Reusing the id above:

```
$ agy -p "What single word did I ask you to reply with a moment ago? ..." \
      --conversation c0d91872-... --output-format json --model "Gemini 3.5 Flash (Low)"
{"conversation_id":"c0d91872-52f3-4ff8-bc71-965b7a264c66","status":"SUCCESS","response":"OK\n","duration_seconds":51.25,"num_turns":2,"usage":{"input_tokens":33758,"output_tokens":5,...}}
```

`num_turns:2` and the recalled `OK` prove prior context was restored, and the same
`conversation_id` is echoed back. **Per-session resume is real** — the conversation id is
surfaced in stdout, refuting Issue #7's premise for this version.

## Non-TTY behaviour

Both `--output-format json` and plain `-p "reply with the single word PING"` produced
output through a `sed` pipe (**non-TTY stdout**); plain mode returned `PING`. **No output
suppression and no PTY needed** on this host/version. (The CHANGELOG's non-TTY discard fix
was Windows-specific; Linux 1.0.16 is fine.)

## Net effect on feasibility

This is **far better than the doc-based report concluded**. For 1.0.16 a real
non-interactive runner is viable with: a genuine `ResumeToken` from `conversation_id`,
`CompletedEvent.answer` from `response`, `ok` from `status`, token `usage`, and working
`--continue` + `--conversation` resume — **no PTY, no plain-text scraping**. The only real
gap vs the Gemini runner is that the JSON is a **single terminal envelope, not a streaming
event feed**, so there are **no intermediate `ActionEvent`s (no live tool/file progress)**
and **no `total_cost_usd`** (tokens only).
