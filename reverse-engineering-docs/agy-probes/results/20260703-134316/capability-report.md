# agy Capability Probe — Report

- **Run:** `/mnt/c/projects-learn/untether-agy/reverse-engineering-docs/agy-probes/results/20260703-134316`
- **agy version:** 1.0.16  ·  **path:** /home/dnichyparuk/.local/bin/agy
- **host:** Linux INV-1097PL 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
- **LIVE probes:** no (help/flags only)

> Cross-reference each row with `../antigravity-cli-runner-feasibility.md`.

## Confirmed facts (flag presence from `agy --help`)

| Capability | Report claim | Probe result |
|---|---|---|
| Binary `agy` present | yes | true |
| `-p`/`--print` headless | yes | absent |
| `--model` + `models` | yes | flag=absent, models_listed=true |
| `--continue`/`-c` | yes | absent |
| `--conversation` | yes | absent |
| `--sandbox` | yes | absent |
| `--project`/`--new-project` | yes | absent |

## Contested claims (this is what the probes settle)

| Question | Report said | Probe result | Verdict |
|---|---|---|---|
| `--output-format` exists? | refuted (0 changelog hits) | help=absent, trial=accepted, live=— | ⚠ REVISIT — may exist |
| Output shape | plain text only | — | (needs LIVE) |
| Non-TTY suppression | blocker; PTY required | pipe=—, pty_token=— | (needs LIVE) |
| Session id surfaced? | no (Issue #7) | in_output=—, new_files=—, new_ids=— | (needs LIVE) |
| `--continue` recalls context? | works (machine-global) | — | (needs LIVE) |
| `--yes` auto-approve flag | refuted (permission modes instead) | help=absent | ✅ confirmed absent |
| Streaming output? | unknown | — (span=—s) | (needs LIVE+ts) |

## Runner feasibility implications

- **Plain-text only confirmed** — only the *minimal non-interactive* runner from §8 of the
  feasibility report is viable (synthetic Started + single Completed, no ActionEvents/usage).
- Non-TTY behavior not tested (run with `--live`).
- Resume: not fully tested (run with `--live`).

## Raw findings
```
agy.path	/home/dnichyparuk/.local/bin/agy
agy.present	true
agy.version	1.0.16
auth.hint	session-file-present
flag.add_dir	absent
flag.continue	absent
flag.conversation	absent
flag.dangerously_skip	absent
flag.json_output	absent
flag.log_file	absent
flag.max_turns	absent
flag.model	absent
flag.no_color	absent
flag.output_format	absent
flag.print_p	absent
flag.print_timeout	absent
flag.project	absent
flag.prompt_file	absent
flag.sandbox	absent
flag.yes	absent
home.file_count_before	10
home.present	true
models.listed	true
tool.jq	present
tool.script	present
tool.sqlite3	missing
tool.timeout	present
tool.ts	missing
tool.unbuffer	missing
tool.xxd	present
trial.output_format	accepted
uname	Linux INV-1097PL 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
```

_Artifacts (.out/.err/.meta/.hex/.clean) for every invocation are in this same directory._
