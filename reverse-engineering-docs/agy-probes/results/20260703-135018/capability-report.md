# agy Capability Probe — Report

- **Run:** `/mnt/c/projects-learn/untether-agy/reverse-engineering-docs/agy-probes/results/20260703-135018`
- **agy version:** 1.0.16  ·  **path:** /home/dnichyparuk/.local/bin/agy
- **host:** Linux INV-1097PL 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
- **LIVE probes:** no (help/flags only)

> Cross-reference each row with `../antigravity-cli-runner-feasibility.md`.

## Confirmed facts (flag presence from `agy --help`)

| Capability | Report claim | Probe result |
|---|---|---|
| Binary `agy` present | yes | true |
| `-p`/`--print` headless | yes | present |
| `--model` + `models` | yes | flag=present, models_listed=true |
| `--continue`/`-c` | yes | present |
| `--conversation` | yes | present |
| `--sandbox` | yes | present |
| `--project`/`--new-project` | yes | present |

## Contested claims (this is what the probes settle)

| Question | Report said | Probe result | Verdict |
|---|---|---|---|
| `--output-format` exists? | refuted (0 changelog hits) | help=absent, trial=defined_hidden, live=— | ✅ confirmed absent |
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
flag.add_dir	present
flag.autoapprove	dangerously-skip-permissions
flag.continue	present
flag.conversation	present
flag.dangerously_skip	present
flag.json_output	absent
flag.log_file	present
flag.max_turns	absent
flag.model	present
flag.no_color	absent
flag.output_format	absent
flag.print_p	present
flag.print_timeout	present
flag.project	present
flag.prompt_file	absent
flag.sandbox	present
flag.yes	absent
help.variant	--help
home.file_count_before	34
home.present	true
models.listed	true
tool.jq	present
tool.script	present
tool.sqlite3	missing
tool.timeout	present
tool.ts	missing
tool.unbuffer	missing
tool.xxd	present
trial.output_format	defined_hidden
uname	Linux INV-1097PL 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
```

_Artifacts (.out/.err/.meta/.hex/.clean) for every invocation are in this same directory._
