#!/usr/bin/env bash
# Probe 04 — model selection & output streaming behavior.
#  - `agy models` (may need auth, no model call): what does it list?
#  - `--model <name>`: does it accept a model override?  (LIVE)
#  - streaming: does `-p` output arrive incrementally or all-at-once?  (LIVE)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir
hdr "04 models & streaming"

# --- models subcommand (SAFE-ish: lists models, no generation) --------------
capture "models_list" -- "$AGY_BIN" models
if [ -s "$RUN_DIR/models_list.out" ]; then
  ok "agy models -> $(wc -l <"$RUN_DIR/models_list.out" | tr -d ' ') lines"
  record "models.listed" "true"
  strip_ansi <"$RUN_DIR/models_list.out" | grep -viE '^\s*$' | head -20 >"$RUN_DIR/models_clean.txt"
else
  warn "agy models produced no output (needs auth? subcommand name differs?)"
  record "models.listed" "false"
fi

# --- everything below invokes the model -------------------------------------
require_live "probe 04 (model/stream)" || { ok "models probe done (LIVE parts skipped)"; exit 0; }
make_workdir

# --- --model acceptance -----------------------------------------------------
hdr "4.1 --model override"
# pick the first listed model if we captured any, else a plausible default
model="$(head -n1 "$RUN_DIR/models_clean.txt" 2>/dev/null | awk '{print $1}')"
model="${model:-gemini-3-pro}"
record "models.tried" "$model"
capture_pty "model_try" "cd '$AGY_WORKDIR' && '$AGY_BIN' -p '$PROBE_PROMPT' --model '$model'"
if grep -qiE 'not defined|unknown|invalid|no such model|unsupported' "$RUN_DIR/model_try.err" "$RUN_DIR/model_try.out" 2>/dev/null; then
  warn "--model '$model' rejected (see model_try.err)"
  record "flag.model.runtime" "rejected"
elif grep -q "$PROBE_TOKEN" "$RUN_DIR/model_try.clean" 2>/dev/null; then
  ok "--model '$model' accepted and produced output"
  record "flag.model.runtime" "accepted"
else
  warn "--model '$model' ran but marker missing"
  record "flag.model.runtime" "ran_no_marker"
fi

# --- streaming: per-line timestamps -----------------------------------------
hdr "4.2 streaming behavior"
# Ask for a few lines so we can see whether they trickle out over time.
stream_prompt="Print exactly three lines, one word per line: alpha, then beta, then gamma."
ts_cmd="cat"
have ts && ts_cmd="ts -s %.s"   # moreutils: prefix each line with seconds-since-start
# shellcheck disable=SC2016
capture_pty "stream_run" "cd '$AGY_WORKDIR' && '$AGY_BIN' -p '$stream_prompt' | $ts_cmd"
if have ts && [ -s "$RUN_DIR/stream_run.out" ]; then
  span="$(strip_ansi <"$RUN_DIR/stream_run.out" | awk 'NF{print $1}' | sort -n | awk 'NR==1{a=$1} {b=$1} END{if(a!=""){printf "%.2f", b-a}else print "0"}')"
  record "stream.timespan_s" "${span:-0}"
  if awk -v s="${span:-0}" 'BEGIN{exit !(s>1.0)}'; then
    ok "output spanned ${span}s across lines => likely STREAMED incrementally"
    record "stream.incremental" "likely"
  else
    warn "output arrived within ${span}s => likely ALL-AT-ONCE (buffered)"
    record "stream.incremental" "unlikely"
  fi
else
  warn "install moreutils ('ts') for streaming timing; captured raw only"
  record "stream.incremental" "unknown_no_ts"
fi

ok "models & streaming probe complete"
