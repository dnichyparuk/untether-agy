#!/usr/bin/env bash
# Orchestrate the agy probe suite into one timestamped results dir.
#
#   ./run_all.sh            # SAFE probes only (00, 01, 04-models) — no model calls
#   ./run_all.sh --live     # + LIVE probes (02, 03, 04-stream) — auth + quota used
#
# Env overrides: AGY_BIN, PROBE_TIMEOUT, PROBE_PROMPT, AGY_HOME, RESULTS_ROOT
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LIVE=0
for a in "$@"; do
  case "$a" in
    --live) LIVE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done
export LIVE

# one shared run dir for the whole suite
export RUN_DIR="${RESULTS_ROOT:-$HERE/results}/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
: >"$RUN_DIR/findings.tsv"
echo "[run_all] results -> $RUN_DIR  (LIVE=$LIVE)" >&2

run() { echo; echo ">>> $1" >&2; bash "$HERE/$1" || echo "[run_all] $1 exited $?" >&2; }

run 00_preflight.sh
run 01_help_and_flags.sh
run 04_models_and_streaming.sh    # models-list part is safe; stream part self-gates on LIVE
if [ "$LIVE" = "1" ]; then
  run 02_output_and_nontty.sh
  run 03_session_and_resume.sh
else
  echo "[run_all] skipping LIVE probes 02/03 (pass --live to enable)" >&2
fi

bash "$HERE/analyze.sh"
echo
echo "[run_all] DONE. Report: $RUN_DIR/capability-report.md" >&2
