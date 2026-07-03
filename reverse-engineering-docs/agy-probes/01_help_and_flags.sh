#!/usr/bin/env bash
# Probe 01 — help & flag discovery.
# SAFE: dumps --help for the root command and subcommands, then checks which of
# the flags our feasibility report cares about actually appear in the help text.
# This is the authoritative, zero-cost way to confirm/refute the doc claims.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir
hdr "01 help & flags"

# --- root help --------------------------------------------------------------
# NOTE: agy prints usage to STDERR (Go flag convention), exit 0. Combine BOTH
# streams so the flag matrix sees the text.
: >"$RUN_DIR/agy_help.txt"
for h in "--help" "-h" "help"; do
  base="help_${h//-/}"
  capture "$base" -- "$AGY_BIN" $h
  if [ -s "$RUN_DIR/$base.out" ] || [ -s "$RUN_DIR/$base.err" ]; then
    cat "$RUN_DIR/$base.out" "$RUN_DIR/$base.err" 2>/dev/null | strip_ansi >"$RUN_DIR/agy_help.txt"
    record "help.variant" "$h"
    break
  fi
done
[ -s "$RUN_DIR/agy_help.txt" ] && ok "captured root help ($(wc -l <"$RUN_DIR/agy_help.txt" | tr -d ' ') lines)" \
                               || warn "no help text captured"

# --- subcommand help (best-effort; names from CHANGELOG) --------------------
for sub in models plugin changelog config auth; do
  capture "help_sub_${sub}" -- "$AGY_BIN" "$sub" --help
done

# --- flag existence matrix --------------------------------------------------
# Left = feasibility-report claim; we confirm via presence in the help text.
# Format of each check: label|regex
checks=(
  "print_p|(^|[[:space:]])-p([[:space:]]|,)|--print"
  "output_format|--output-format|--output([[:space:]]|=)"
  "json_output|json"
  "model|--model|(^|[[:space:]])-m([[:space:]]|,)"
  "continue|--continue|(^|[[:space:]])-c([[:space:]]|,)"
  "conversation|--conversation"
  "sandbox|--sandbox"
  "yes|--yes"
  "dangerously_skip|--dangerously-skip-permissions"
  "print_timeout|--print-timeout"
  "prompt_file|--prompt-file"
  "add_dir|--add-dir"
  "no_color|--no-color"
  "project|--project|--new-project"
  "max_turns|--max-turns"
  "log_file|--log-file"
)
hdr "flag presence (from help text)"
for entry in "${checks[@]}"; do
  key="${entry%%|*}"; rx="${entry#*|}"
  if help_has "$rx"; then ok  "flag present : $key"; record "flag.$key" "present"
  else                    warn "flag absent  : $key"; record "flag.$key" "absent"; fi
done

# --- hidden-flag discriminator for --output-format (parse-only, NO model) ----
# agy lists ALL undefined flags in one "flags provided but not defined: ..." line
# and rejects unknown flags. Trick: append a guaranteed-undefined trailing flag.
#   - if the error line names `-output-format` -> it is UNDEFINED
#   - if it names only the trailing junk    -> `-output-format` is DEFINED (hidden)
# In 1.0.16 this flag is DEFINED but omitted from --help.
flag_defined() { # flag_defined <flagname> [takes_value]
  local fl="$1" tv="${2:-1}" junk="agyprobe_undefZ"
  if [ "$tv" = 1 ]; then
    capture "trial_$fl" -- "$AGY_BIN" "--$fl" x "--$junk"
  else
    capture "trial_$fl" -- "$AGY_BIN" "--$fl" "--$junk"
  fi
  local line
  line="$(grep -iE 'not defined' "$RUN_DIR/trial_$fl.err" "$RUN_DIR/trial_$fl.out" 2>/dev/null | head -1)"
  # DEFINED iff the "not defined" line does NOT mention this flag's own name
  echo "$line" | grep -qE -- "-$fl([[:space:]]|$)" && return 1 || return 0
}
if flag_defined "output-format" 1; then
  ok "--output-format is DEFINED (hidden from --help) — structured output IS available"
  record "trial.output_format" "defined_hidden"
else
  warn "--output-format NOT defined on this version (plain-text only)"
  record "trial.output_format" "not_defined"
fi
# also confirm the confirmed auto-approve flag (help lists it; not --yes)
help_has -- '--dangerously-skip-permissions' \
  && record "flag.autoapprove" "dangerously-skip-permissions" \
  || record "flag.autoapprove" "unknown"

ok "help & flag discovery complete"
