#!/usr/bin/env bash
# Probe 02 — output format & the non-TTY suppression blocker.
# LIVE: invokes the model with a tiny marker prompt. Requires LIVE=1.
#
# Answers the two decisive questions for a runner:
#   (a) Does `agy -p` produce output on a PIPE (non-TTY), or is it suppressed?
#   (b) Is the output plain text, JSON, or JSONL?
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir
hdr "02 output & non-TTY"
require_live "probe 02" || exit 0
make_workdir

runp() { ( cd "$AGY_WORKDIR" && "$@" ); }   # run agy in the isolated workdir

# --- (1) direct PIPE: this is exactly how JsonlSubprocessRunner spawns agy ----
hdr "2.1 direct pipe (non-TTY stdout)"
capture "pipe_plain" -- env -C "$AGY_WORKDIR" "$AGY_BIN" -p "$PROBE_PROMPT" 2>/dev/null \
  || capture "pipe_plain" -- "$AGY_BIN" -p "$PROBE_PROMPT"   # fallback if env -C unsupported
pbytes="$(wc -c <"$RUN_DIR/pipe_plain.out" | tr -d ' ')"
prc="$(grep -m1 '^exit' "$RUN_DIR/pipe_plain.meta" | cut -f2)"
if [ "$pbytes" -eq 0 ] 2>/dev/null; then
  if [ "$prc" = "0" ]; then
    err "NON-TTY SUPPRESSION CONFIRMED: empty stdout with exit 0 ('succeeded but did nothing')"
    record "output.nontty_pipe" "empty_rc0_suppressed"
  else
    warn "empty stdout, exit=$prc (error, not necessarily suppression)"
    record "output.nontty_pipe" "empty_rc_nonzero"
  fi
else
  ok "pipe produced $pbytes bytes (no full suppression on this version/OS)"
  record "output.nontty_pipe" "produced_${pbytes}B"
fi
grep -q "$PROBE_TOKEN" "$RUN_DIR/pipe_plain.out" 2>/dev/null \
  && { record "output.pipe_has_token" "true"; ok "marker token present in pipe output"; } \
  || record "output.pipe_has_token" "false"

# --- (2) PTY: the documented workaround --------------------------------------
hdr "2.2 pty (allocated terminal)"
capture_pty "pty_plain" "cd '$AGY_WORKDIR' && '$AGY_BIN' -p '$PROBE_PROMPT'"
if [ -f "$RUN_DIR/pty_plain.clean" ]; then
  cbytes="$(wc -c <"$RUN_DIR/pty_plain.clean" | tr -d ' ')"
  record "output.pty_clean_bytes" "$cbytes"
  grep -q "$PROBE_TOKEN" "$RUN_DIR/pty_plain.clean" 2>/dev/null \
    && { record "output.pty_has_token" "true"; ok "marker token present in PTY output ($cbytes B clean)"; } \
    || { record "output.pty_has_token" "false"; warn "marker token NOT found in PTY output"; }
fi

# --- (3) classify the output shape ------------------------------------------
hdr "2.3 output shape (plain text vs JSON vs JSONL)"
best="$RUN_DIR/pipe_plain.out"
[ "$pbytes" -eq 0 ] 2>/dev/null && best="$RUN_DIR/pty_plain.clean"
if [ -s "$best" ]; then
  if looks_like_jsonl "$best"; then    shape="jsonl"
  elif looks_like_json "$best"; then   shape="json"
  else                                 shape="plain_text"; fi
else
  shape="empty"
fi
record "output.shape" "$shape"
case "$shape" in
  plain_text) warn "output shape = PLAIN TEXT (confirms: no structured events; ActionEvents impossible)";;
  json)       ok   "output shape = single JSON blob (result envelope may exist!)";;
  jsonl)      ok   "output shape = JSONL stream (Gemini-parity possible!)";;
  empty)      err  "output shape = EMPTY (see suppression above)";;
esac

# --- (4) THE decisive test: --output-format json result envelope (live) ------
# On agy 1.0.16 this yields a single JSON object:
#   {conversation_id, status, response, duration_seconds, num_turns, usage{...}}
hdr "2.4 --output-format json result envelope (live)"
( cd "$AGY_WORKDIR" && timeout "$PROBE_TIMEOUT" "$AGY_BIN" -p "$PROBE_PROMPT" \
    --output-format json --model "${PROBE_MODEL:-Gemini 3.5 Flash (Low)}" </dev/null ) \
    >"$RUN_DIR/json_env.out" 2>"$RUN_DIR/json_env.err"
record "json.exit" "$?"
if grep -qiE 'not defined|unknown flag' "$RUN_DIR/json_env.err" 2>/dev/null; then
  record "output.json_flag" "rejected"; warn "--output-format json rejected on this version"
elif looks_like_json "$RUN_DIR/json_env.out"; then
  record "output.json_flag" "produced_json_envelope"
  ok "--output-format json => structured envelope (Gemini-class runner feasible)"
  if have jq; then
    for k in conversation_id status response num_turns; do
      v="$(jq -r --arg k "$k" '.[$k] // "—"' "$RUN_DIR/json_env.out" 2>/dev/null | head -c 80)"
      record "json.$k" "$v"
    done
    record "json.has_usage"     "$(jq -e '.usage' "$RUN_DIR/json_env.out" >/dev/null 2>&1 && echo true || echo false)"
    record "json.has_cost_usd"  "$(jq -e '.total_cost_usd // .usage.total_cost_usd' "$RUN_DIR/json_env.out" >/dev/null 2>&1 && echo true || echo false)"
    record "json.total_tokens"  "$(jq -r '.usage.total_tokens // "—"' "$RUN_DIR/json_env.out" 2>/dev/null)"
    record "session.id_via_json" "$(jq -e '.conversation_id' "$RUN_DIR/json_env.out" >/dev/null 2>&1 && echo true || echo false)"
    ok "envelope: conversation_id=$(jq -r '.conversation_id//"—"' "$RUN_DIR/json_env.out" 2>/dev/null | head -c 12)… total_tokens=$(jq -r '.usage.total_tokens//"—"' "$RUN_DIR/json_env.out" 2>/dev/null)"
  fi
elif looks_like_jsonl "$RUN_DIR/json_env.out"; then
  record "output.json_flag" "produced_jsonl_stream"; ok "--output-format json => JSONL stream"
else
  record "output.json_flag" "accepted_but_not_json"; warn "json flag accepted but output not JSON"
fi

ok "output & non-TTY probe complete"
