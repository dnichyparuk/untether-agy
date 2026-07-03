#!/usr/bin/env bash
# Probe 03 — session id surfacing & resume mechanics.
# LIVE: runs one prompt, then inspects whether a conversation/session id is
# discoverable (stdout? stderr? new files under ~/.gemini/antigravity-cli/?),
# and whether --continue / --conversation work headlessly.
#
# This tests the Issue #7 finding ("conversation id never surfaced in stdout/
# stderr/any documented file") empirically.
#
# SQLite gate: reading agy's conversation .db files is OPT-IN. By the user's
# instruction, we validate everything possible from `agy --help` + stdout/stderr
# first, and only touch SQLite when explicitly enabled with AGY_PROBE_SQLITE=1.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir
hdr "03 session & resume"
require_live "probe 03" || exit 0
make_workdir

AGY_PROBE_SQLITE="${AGY_PROBE_SQLITE:-0}"   # 0 = do NOT read .db files (default)

snapshot() { # snapshot NAME -> file list + sqlite conversation ids (best effort)
  local tag="$1"
  ( cd "$AGY_HOME" 2>/dev/null && find . -type f -printf '%T@ %p\n' 2>/dev/null | sort ) \
    >"$RUN_DIR/home_$tag.txt" 2>/dev/null || : >"$RUN_DIR/home_$tag.txt"
  if [ "$AGY_PROBE_SQLITE" = "1" ] && have sqlite3; then
    : >"$RUN_DIR/convids_$tag.txt"
    while IFS= read -r db; do
      # dump any table/column that looks like an id from each conversation db
      sqlite3 "$db" '.tables' 2>/dev/null | tr ' ' '\n' | while IFS= read -r tbl; do
        [ -z "$tbl" ] && continue
        sqlite3 "$db" "SELECT * FROM $tbl LIMIT 5;" 2>/dev/null \
          | grep -oiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|conv[_-][0-9a-z]+' \
          >>"$RUN_DIR/convids_$tag.txt" 2>/dev/null || true
      done
    done < <(find "$AGY_HOME" -maxdepth 3 -name '*.db' 2>/dev/null)
    sort -u "$RUN_DIR/convids_$tag.txt" -o "$RUN_DIR/convids_$tag.txt" 2>/dev/null || true
  fi
}

hdr "3.1 baseline snapshot"
snapshot before
record "session.dbs_before" "$(find "$AGY_HOME" -maxdepth 3 -name '*.db' 2>/dev/null | wc -l | tr -d ' ')"

hdr "3.2 run one prompt (pty, to defeat suppression)"
capture_pty "sess_run" "cd '$AGY_WORKDIR' && '$AGY_BIN' -p '$PROBE_PROMPT'"

hdr "3.3 look for a session/conversation id in the run output"
idrx='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|conv[_-][0-9A-Za-z]+|session[_ -]?id'
if grep -qiE "$idrx" "$RUN_DIR/sess_run.clean" "$RUN_DIR/sess_run.err" 2>/dev/null; then
  ok "an id-like string appears in run output — resume-by-id MIGHT be feasible from stdout"
  grep -oiE "$idrx" "$RUN_DIR/sess_run.clean" "$RUN_DIR/sess_run.err" 2>/dev/null | sort -u >"$RUN_DIR/ids_in_output.txt"
  record "session.id_in_output" "true"
else
  warn "no id-like string in stdout/stderr (matches Issue #7: id not surfaced)"
  record "session.id_in_output" "false"
fi

hdr "3.4 diff config dir (did a new conversation file appear?)"
snapshot after
diff <(cut -d' ' -f2- "$RUN_DIR/home_before.txt") <(cut -d' ' -f2- "$RUN_DIR/home_after.txt") \
  >"$RUN_DIR/home_diff.txt" 2>/dev/null || true
newfiles="$(grep -c '^> ' "$RUN_DIR/home_diff.txt" 2>/dev/null || echo 0)"
record "session.new_files_after_run" "$newfiles"
[ "$newfiles" -gt 0 ] 2>/dev/null && ok "$newfiles new file(s) under $AGY_HOME (state persisted; id may be recoverable via SQLite — fragile)" \
                                  || warn "no new files detected under $AGY_HOME"
if [ "$AGY_PROBE_SQLITE" != "1" ]; then
  warn "SQLite inspection disabled (default). Enable only after reviewing help findings: AGY_PROBE_SQLITE=1"
  record "session.sqlite_probe" "disabled"
elif have sqlite3; then
  comm -13 "$RUN_DIR/convids_before.txt" "$RUN_DIR/convids_after.txt" >"$RUN_DIR/new_conv_ids.txt" 2>/dev/null || true
  record "session.new_conv_ids" "$(wc -l <"$RUN_DIR/new_conv_ids.txt" 2>/dev/null | tr -d ' ')"
fi

hdr "3.5 --continue (resumes most-recent; note: machine-global per Issue #7)"
capture_pty "sess_continue" "cd '$AGY_WORKDIR' && '$AGY_BIN' -p 'What token did I just ask you to repeat? Answer with just the token.' --continue"
if grep -q "$PROBE_TOKEN" "$RUN_DIR/sess_continue.clean" 2>/dev/null; then
  ok "--continue recalled prior context (token remembered) => /continue is viable"
  record "resume.continue" "works"
else
  warn "--continue did NOT recall the token (context not carried, or different behavior)"
  record "resume.continue" "no_recall"
fi

ok "session & resume probe complete"
