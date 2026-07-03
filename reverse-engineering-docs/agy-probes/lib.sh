#!/usr/bin/env bash
# Shared helpers for the agy probe harness.
# Sourced by every 0N_*.sh probe and by run_all.sh / analyze.sh.
#
# Nothing here invokes the model on its own; the probes decide that.

# Intentionally NOT `set -e`: we want to capture failures, not abort on them.
set -uo pipefail

AGY_BIN="${AGY_BIN:-agy}"                 # override to test a specific binary/path
PROBE_TIMEOUT="${PROBE_TIMEOUT:-120}"     # seconds per invocation
LIVE="${LIVE:-0}"                          # 1 = allow model-invoking probes

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_ROOT="${RESULTS_ROOT:-$LIB_DIR/results}"

# A deterministic, low-cost marker prompt. Probes ask agy to echo it back so we
# can tell "real answer" from "empty / suppressed output" without parsing prose.
PROBE_TOKEN="${PROBE_TOKEN:-AGYPROBE_OK_7F3Q}"
PROBE_PROMPT="${PROBE_PROMPT:-Reply with exactly this token and nothing else: ${PROBE_TOKEN}}"

# agy's known config/state root (validated from CHANGELOG: ~/.gemini/antigravity-cli/)
AGY_HOME="${AGY_HOME:-$HOME/.gemini/antigravity-cli}"

# ---------------------------------------------------------------------------
# tiny utils
# ---------------------------------------------------------------------------
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'
c_red=$'\033[31m'; c_cya=$'\033[36m'; c_gry=$'\033[90m'

log()  { printf '%s[probe]%s %s\n' "$c_cya" "$c_reset" "$*" >&2; }
ok()   { printf '%s[ ok ]%s %s\n' "$c_grn" "$c_reset" "$*" >&2; }
warn() { printf '%s[warn]%s %s\n' "$c_ylw" "$c_reset" "$*" >&2; }
err()  { printf '%s[fail]%s %s\n' "$c_red" "$c_reset" "$*" >&2; }
hdr()  { printf '\n%s== %s ==%s\n' "$c_bold" "$*" "$c_reset" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

# Create/attach a run directory. Shared across probes when RUN_DIR is exported by
# run_all.sh; otherwise each probe makes its own timestamped dir.
init_run_dir() {
  if [ -n "${RUN_DIR:-}" ]; then mkdir -p "$RUN_DIR"; return; fi
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  RUN_DIR="$RESULTS_ROOT/$ts"
  mkdir -p "$RUN_DIR"
  export RUN_DIR
  log "results -> $RUN_DIR"
}

# record KEY VALUE  -> appended to findings.tsv (consumed by analyze.sh)
record() {
  printf '%s\t%s\n' "$1" "${2//$'\t'/ }" >> "$RUN_DIR/findings.tsv"
}

# ---------------------------------------------------------------------------
# capture: run a command, tee stdout/stderr/exit/duration/bytes to artifacts.
# stdin is fed from /dev/null so agy can never block waiting for input.
# usage: capture NAME -- CMD [ARGS...]
# ---------------------------------------------------------------------------
capture() {
  local name="$1"; shift
  [ "$1" = "--" ] && shift
  local out="$RUN_DIR/$name.out" er="$RUN_DIR/$name.err" mt="$RUN_DIR/$name.meta"
  local start end rc dur
  start="$(date +%s.%N)"
  if have timeout; then
    timeout --signal=TERM "$PROBE_TIMEOUT" "$@" >"$out" 2>"$er" </dev/null
    rc=$?
  else
    "$@" >"$out" 2>"$er" </dev/null
    rc=$?
  fi
  end="$(date +%s.%N)"
  dur="$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.3f", b-a}')"
  {
    printf 'cmd\t%s\n' "$*"
    printf 'exit\t%s\n' "$rc"
    printf 'duration_s\t%s\n' "$dur"
    printf 'stdout_bytes\t%s\n' "$(wc -c <"$out" | tr -d ' ')"
    printf 'stderr_bytes\t%s\n' "$(wc -c <"$er" | tr -d ' ')"
  } >"$mt"
  # small hexdump of the head of stdout to inspect control chars / encoding
  if have xxd; then head -c 512 "$out" | xxd >"$RUN_DIR/$name.hex" 2>/dev/null || true; fi
  log "[$name] exit=$rc ${dur}s out=$(wc -c <"$out" | tr -d ' ')B err=$(wc -c <"$er" | tr -d ' ')B"
  return "$rc"
}

# capture_pty: same, but allocate a pseudo-terminal so agy sees a "TTY".
# This is the workaround for agy's non-TTY output suppression (CHANGELOG :21).
# usage: capture_pty NAME "single command string"
capture_pty() {
  local name="$1" cmdstr="$2"
  local out="$RUN_DIR/$name.out" er="$RUN_DIR/$name.err" mt="$RUN_DIR/$name.meta"
  local start end rc dur uname_s
  uname_s="$(uname -s 2>/dev/null || echo unknown)"
  start="$(date +%s.%N)"
  if have script; then
    if [ "$uname_s" = "Darwin" ]; then           # BSD script (macOS)
      script -q /dev/null /bin/sh -c "$cmdstr" >"$out" 2>"$er" </dev/null; rc=$?
    else                                          # util-linux script (-e propagates child rc)
      script -qec "$cmdstr" /dev/null >"$out" 2>"$er" </dev/null; rc=$?
    fi
  elif have unbuffer; then
    unbuffer /bin/sh -c "$cmdstr" >"$out" 2>"$er" </dev/null; rc=$?
  else
    warn "[$name] no 'script' or 'unbuffer' available; PTY probe skipped"
    printf 'cmd\t%s\nexit\tSKIPPED_NO_PTY\n' "$cmdstr" >"$mt"
    record "$name.pty" "skipped_no_pty_tool"
    return 127
  fi
  end="$(date +%s.%N)"
  dur="$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.3f", b-a}')"
  # strip ANSI + CR into a .clean sibling (PTY output is contaminated with escapes)
  strip_ansi <"$out" >"$RUN_DIR/$name.clean" 2>/dev/null || cp "$out" "$RUN_DIR/$name.clean"
  {
    printf 'cmd\t%s\n' "$cmdstr"
    printf 'exit\t%s\n' "$rc"
    printf 'duration_s\t%s\n' "$dur"
    printf 'stdout_bytes\t%s\n' "$(wc -c <"$out" | tr -d ' ')"
    printf 'clean_bytes\t%s\n' "$(wc -c <"$RUN_DIR/$name.clean" | tr -d ' ')"
  } >"$mt"
  log "[$name/pty] exit=$rc ${dur}s raw=$(wc -c <"$out" | tr -d ' ')B clean=$(wc -c <"$RUN_DIR/$name.clean" | tr -d ' ')B"
  return "$rc"
}

strip_ansi() { sed -r 's/\x1B\[[0-9;?]*[A-Za-z]//g; s/\x1B\][^\x07]*\x07//g' | tr -d '\r'; }

# is this file plausibly a single JSON document?
looks_like_json() {
  local f="$1"
  [ -s "$f" ] || return 1
  local first; first="$(tr -d '[:space:]' <"$f" | head -c1)"
  [ "$first" = "{" ] || [ "$first" = "[" ] || return 1
  have jq && jq -e . <"$f" >/dev/null 2>&1
}

# does this file look like JSONL (>=2 lines each parseable as JSON)?
looks_like_jsonl() {
  local f="$1"; have jq || return 2
  local n=0 good=0 line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    n=$((n+1))
    printf '%s' "$line" | jq -e . >/dev/null 2>&1 && good=$((good+1))
  done <"$f"
  [ "$n" -ge 2 ] && [ "$good" -eq "$n" ]
}

# grep the cached help text for a flag/token
help_has() { grep -qiE -- "$1" "$RUN_DIR/agy_help.txt" 2>/dev/null; }

require_live() {
  if [ "$LIVE" != "1" ]; then
    warn "$1 needs the model (auth + quota). Re-run with LIVE=1 or ./run_all.sh --live"
    return 1
  fi
  return 0
}

# an isolated scratch workspace so live prompts can't edit real repos
make_workdir() {
  AGY_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/agy-probe.XXXXXX")"
  export AGY_WORKDIR
  log "isolated workdir -> $AGY_WORKDIR"
}
