# agy Probe Harness

A set of bash scripts that empirically probe the **Antigravity CLI (`agy`)** to
confirm or refute the doc/changelog-derived claims in
[`../antigravity-cli-runner-feasibility.md`](../antigravity-cli-runner-feasibility.md).

Everything is captured to a timestamped `results/<ts>/` directory (raw
`.out`/`.err`/`.meta`/`.hex`/`.clean` per invocation), then `analyze.sh` compiles
a `capability-report.md` mapping each result back to a feasibility claim.

> These scripts must run on a host where `agy` is installed **and authenticated**.
> They are not runnable in the Untether dev container.

## Methodology: `agy --help` first, SQLite last

By design (and per explicit instruction), validation proceeds in trust order:

1. **`agy --version` / `agy --help` / subcommand help** — zero-cost, no model
   calls. This is the **authoritative** check for flag existence (`-p`,
   `--output-format`, `--model`, `--continue`, `--conversation`, `--sandbox`,
   `--yes`, …). Run and review this before anything else.
2. **Live output probes** — a tiny marker prompt to observe real stdout: plain
   text vs JSON vs JSONL, and the non-TTY suppression behavior.
3. **Session/resume** — id surfacing via stdout/stderr + a *non-invasive* file
   listing of `~/.gemini/antigravity-cli/`.
4. **SQLite inspection — OPT-IN, LAST.** Reading agy's conversation `.db` files
   is **disabled by default** and only runs with `AGY_PROBE_SQLITE=1`, after the
   help-based findings have been reviewed.

## Safety

- **No model calls unless `--live`.** Help/version/flag discovery (probes 00, 01,
  and `agy models` in 04) are free. Probes 02/03 and the streaming part of 04
  invoke the model (auth + quota) and only run with `--live`.
- **Isolated workspace.** Live prompts `cd` into a fresh `mktemp -d` so `agy`
  cannot edit your repos. Prompts are trivial marker/echo tasks.
- **stdin blocked.** Every invocation gets `</dev/null` so `agy` can't hang
  waiting for interactive input.
- **Read-only intent.** The harness never writes to `~/.gemini/...`; it only
  lists files and (opt-in) reads SQLite.

## Usage

```bash
cd reverse-engineering-docs/agy-probes

# 1) SAFE first — validates every flag claim from `agy --help`, no model calls:
./run_all.sh
#   → review results/<ts>/capability-report.md  (the "Confirmed facts" table)

# 2) Then LIVE — observe real output shape, non-TTY behavior, resume:
./run_all.sh --live

# 3) Only if you want the fragile SQLite resume path investigated, LAST:
AGY_PROBE_SQLITE=1 ./run_all.sh --live
```

Run an individual probe:

```bash
./00_preflight.sh
./01_help_and_flags.sh          # the `agy --help` validation
LIVE=1 ./02_output_and_nontty.sh
```

## Environment knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `AGY_BIN` | `agy` | binary/path to probe |
| `LIVE` | `0` | `1` allows model-invoking probes (or pass `--live`) |
| `PROBE_TIMEOUT` | `120` | seconds per invocation (`timeout`) |
| `PROBE_PROMPT` | marker prompt | override the test prompt |
| `AGY_HOME` | `~/.gemini/antigravity-cli` | config/state dir to inspect |
| `AGY_PROBE_SQLITE` | `0` | `1` enables opt-in `.db` reads (probe 03) |
| `RESULTS_ROOT` | `./results` | where run dirs are written |

## Files

| File | Purpose | Model calls? |
|------|---------|-------------|
| `lib.sh` | shared capture/PTY/JSON/record helpers | no |
| `00_preflight.sh` | host tooling, `agy` presence, version, config dir | no |
| `01_help_and_flags.sh` | **`agy --help` dump + flag existence matrix** | no |
| `02_output_and_nontty.sh` | pipe vs PTY output, shape, `--output-format` | **yes** |
| `03_session_and_resume.sh` | id surfacing, file diff, `--continue`; SQLite opt-in | **yes** |
| `04_models_and_streaming.sh` | `agy models`, `--model`, streaming timing | models: no · rest: **yes** |
| `run_all.sh` | orchestrate → shared results dir | gated |
| `analyze.sh` | compile `capability-report.md` | no |

## Recommended host tooling

`jq` (JSON checks), `script` or `unbuffer` (PTY workaround), `xxd` (hexdumps),
`timeout` (GNU coreutils), `ts` (moreutils, streaming timing). Missing tools are
reported by `00_preflight.sh` and degrade gracefully.

## Interpreting the report

Open `results/<ts>/capability-report.md`. The **"Contested claims"** table is the
payoff — it settles each uncertain point (does `--output-format` exist? is output
plain text? is stdout suppressed on a pipe? is a session id surfaced?) and the
**"Runner feasibility implications"** section restates the go/no-go for the
minimal runner vs a Gemini-parity runner based on what was actually observed.
