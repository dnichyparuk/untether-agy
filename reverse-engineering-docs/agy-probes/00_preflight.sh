#!/usr/bin/env bash
# Probe 00 — preflight: environment, binary presence, version, auth signal.
# SAFE: no model calls. `agy --version` / help do not consume quota.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
init_run_dir
hdr "00 preflight"

# --- host tooling -----------------------------------------------------------
for t in jq script unbuffer xxd timeout sqlite3 ts; do
  if have "$t"; then ok "tool present: $t"; record "tool.$t" "present"
  else warn "tool missing: $t"; record "tool.$t" "missing"; fi
done
record "uname" "$(uname -a 2>/dev/null | tr '\t' ' ')"

# --- agy on PATH? -----------------------------------------------------------
if ! have "$AGY_BIN"; then
  err "'$AGY_BIN' not on PATH. Install: curl -fsSL https://antigravity.google/cli/install.sh | bash"
  record "agy.present" "false"
  exit 1
fi
record "agy.present" "true"
record "agy.path" "$(command -v "$AGY_BIN")"
ok "agy binary: $(command -v "$AGY_BIN")"

# --- version ----------------------------------------------------------------
capture "agy_version" -- "$AGY_BIN" --version
ver="$(head -n1 "$RUN_DIR/agy_version.out" 2>/dev/null | tr -d '\r')"
record "agy.version" "${ver:-unknown}"

# --- config/state dir presence (validated path from CHANGELOG) --------------
if [ -d "$AGY_HOME" ]; then
  ok "config dir exists: $AGY_HOME"
  record "home.present" "true"
  ( cd "$AGY_HOME" && find . -maxdepth 2 -type f 2>/dev/null | sort ) >"$RUN_DIR/home_tree_before.txt"
  record "home.file_count_before" "$(wc -l <"$RUN_DIR/home_tree_before.txt" | tr -d ' ')"
else
  warn "config dir absent: $AGY_HOME (agy may not have run yet / not authed)"
  record "home.present" "false"
fi

# --- auth signal (best-effort, no model call) -------------------------------
# We can't read the keyring, but 'agy --version' working + a config dir with a
# session hints at auth. A dedicated whoami/auth subcommand may exist; probe help
# in 01. Here we just record whether an obvious credential/session file exists.
authhint="unknown"
if [ -d "$AGY_HOME" ]; then
  if find "$AGY_HOME" -maxdepth 3 -iname '*session*' -o -iname '*cred*' -o -iname '*token*' 2>/dev/null | grep -q .; then
    authhint="session-file-present"
  fi
fi
record "auth.hint" "$authhint"
log "auth hint: $authhint (definitive check: run a LIVE probe and see if it errors on auth)"

ok "preflight complete"
