#!/usr/bin/env bash
# Compile findings.tsv into a human-readable capability report that maps each
# empirical result back to a claim in ../antigravity-cli-runner-feasibility.md.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir

F="$RUN_DIR/findings.tsv"
R="$RUN_DIR/capability-report.md"
[ -f "$F" ] || { err "no findings.tsv in $RUN_DIR — run the probes first"; exit 1; }

get() { awk -F'\t' -v k="$1" '$1==k{v=$2} END{print (v==""?"—":v)}' "$F"; }

{
  echo "# agy Capability Probe — Report"
  echo
  echo "- **Run:** \`$RUN_DIR\`"
  echo "- **agy version:** $(get agy.version)  ·  **path:** $(get agy.path)"
  echo "- **host:** $(get uname)"
  echo "- **LIVE probes:** $([ "$LIVE" = 1 ] && echo yes || echo 'no (help/flags only)')"
  echo
  echo "> Cross-reference each row with \`../antigravity-cli-runner-feasibility.md\`."
  echo
  echo "## Confirmed facts (flag presence from \`agy --help\`)"
  echo
  echo "| Capability | Report claim | Probe result |"
  echo "|---|---|---|"
  echo "| Binary \`agy\` present | yes | $(get agy.present) |"
  echo "| \`-p\`/\`--print\` headless | yes | $(get flag.print_p) |"
  echo "| \`--model\` + \`models\` | yes | flag=$(get flag.model), models_listed=$(get models.listed) |"
  echo "| \`--continue\`/\`-c\` | yes | $(get flag.continue) |"
  echo "| \`--conversation\` | yes | $(get flag.conversation) |"
  echo "| \`--sandbox\` | yes | $(get flag.sandbox) |"
  echo "| \`--project\`/\`--new-project\` | yes | $(get flag.project) |"
  echo
  echo "## Contested claims (this is what the probes settle)"
  echo
  echo "| Question | Report said | Probe result | Verdict |"
  echo "|---|---|---|---|"
  printf '| `--output-format` exists? | refuted (0 changelog hits) | help=%s, trial=%s, live=%s | %s |\n' \
    "$(get flag.output_format)" "$(get trial.output_format)" "$(get output.json_flag)" \
    "$( [ "$(get trial.output_format)" = accepted ] || [ "$(get output.json_flag)" = produced_json ] && echo '⚠ REVISIT — may exist' || echo '✅ confirmed absent')"
  printf '| Output shape | plain text only | %s | %s |\n' \
    "$(get output.shape)" \
    "$(case "$(get output.shape)" in jsonl|json) echo '⚠ structured! revisit';; plain_text) echo '✅ matches';; *) echo '(needs LIVE)';; esac)"
  printf '| Non-TTY suppression | blocker; PTY required | pipe=%s, pty_token=%s | %s |\n' \
    "$(get output.nontty_pipe)" "$(get output.pty_has_token)" \
    "$(case "$(get output.nontty_pipe)" in empty_rc0_suppressed) echo '✅ confirmed — PTY needed';; produced*) echo '⚠ pipe works on this version';; *) echo '(needs LIVE)';; esac)"
  printf '| Session id surfaced? | no (Issue #7) | in_output=%s, new_files=%s, new_ids=%s | %s |\n' \
    "$(get session.id_in_output)" "$(get session.new_files_after_run)" "$(get session.new_conv_ids)" \
    "$(case "$(get session.id_in_output)" in true) echo '⚠ id found — resume-by-id feasible';; false) echo '✅ matches (fragile SQLite path only)';; *) echo '(needs LIVE)';; esac)"
  printf '| `--continue` recalls context? | works (machine-global) | %s | %s |\n' \
    "$(get resume.continue)" \
    "$(case "$(get resume.continue)" in works) echo '✅ /continue viable';; no_recall) echo '⚠ no recall';; *) echo '(needs LIVE)';; esac)"
  printf '| `--yes` auto-approve flag | refuted (permission modes instead) | help=%s | %s |\n' \
    "$(get flag.yes)" \
    "$([ "$(get flag.yes)" = present ] && echo '⚠ exists after all' || echo '✅ confirmed absent')"
  printf '| Streaming output? | unknown | %s (span=%ss) | %s |\n' \
    "$(get stream.incremental)" "$(get stream.timespan_s)" \
    "$(case "$(get stream.incremental)" in likely) echo 'incremental';; unlikely) echo 'buffered/all-at-once';; *) echo '(needs LIVE+ts)';; esac)"
  echo
  echo "## Runner feasibility implications"
  echo
  shape="$(get output.shape)"; jsonflag="$(get output.json_flag)"
  if [ "$shape" = jsonl ] || [ "$shape" = json ] || [ "$jsonflag" = produced_json ]; then
    echo "- **Structured output detected** — a Gemini-parity runner may be possible. Re-read"
    echo "  \`04-gemini-integration.md\` and design a real \`translate()\` + schema."
  else
    echo "- **Plain-text only confirmed** — only the *minimal non-interactive* runner from §8 of the"
    echo "  feasibility report is viable (synthetic Started + single Completed, no ActionEvents/usage)."
  fi
  case "$(get output.nontty_pipe)" in
    empty_rc0_suppressed) echo "- **PTY wrapper is mandatory** (pipe stdout is suppressed) — override the spawn path.";;
    produced*)            echo "- **Pipe stdout works on this version** — the PTY workaround may be unnecessary here; re-test after upgrades.";;
    *)                    echo "- Non-TTY behavior not tested (run with \`--live\`).";;
  esac
  case "$(get resume.continue)/$(get session.id_in_output)" in
    works/true)  echo "- Resume: both \`/continue\` and per-session resume look feasible — big upgrade over the report's assumption.";;
    works/false) echo "- Resume: \`/continue\` only (no per-session id) — ship \`/continue\` with the machine-global caveat.";;
    *)           echo "- Resume: not fully tested (run with \`--live\`).";;
  esac
  echo
  echo "## Raw findings"
  echo '```'
  sort "$F"
  echo '```'
  echo
  echo "_Artifacts (.out/.err/.meta/.hex/.clean) for every invocation are in this same directory._"
} >"$R"

ok "capability report -> $R"
have less >/dev/null && sed -n '1,60p' "$R" >&2 || cat "$R" >&2
