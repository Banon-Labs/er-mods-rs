#!/usr/bin/env bash
# Steam state for the launch preflight: running, signed in, and started on demand.
#
#   steam-running.sh              exit 0 if Steam runs ("steam: RUNNING"), 1 otherwise
#   steam-running.sh --signed-in  exit 0 if Steam runs and is signed in, 1 otherwise
#   steam-running.sh --ensure     start Steam if it is down, then wait up to 25 s for
#                                 its log to report the sign-in;
#                                 exit 0 signed in, 2 running but not signed in
#
# User directive 2026-10-26: launch Steam if needed, but before launching a game make sure it is
# signed in; if it is not, tell the user to sign in, name that as the blocker, and monitor until it
# clears (`until bash scripts/steam-running.sh --signed-in; do sleep 5; done` in the background).
# Starting Steam needs no login on this machine (saved credentials, `AutoLoginUser` set); a
# missing sign-in means an expired session or no network, and only the user can fix that.
#
# Signed in is read from Steam's own connection log: every line carries the client's logon state
# as `[Logged On, ...]`, `[Logged Off, ...]`, `[Connecting, ...]`, so the state on the newest line
# that carries one is the state now. Measured 2026-10-25: a session that lost its network kept writing
# `[Logged Off, 4, 0]` reconnect attempts for 40 minutes while the process stayed up, which a
# process check alone reads as healthy.
#
# The WSL branch below dates from the retired WSL2 + Windows Steam setup (bd
# steam-detection-wsl-false-negative-2026-07-18) and is inert on native Linux.
STEAM_CONNECTION_LOG="${STEAM_CONNECTION_LOG:-$HOME/.local/share/Steam/logs/connection_log.txt}"

steam_running() {
  # 1. native Linux Steam
  pgrep -x steam >/dev/null 2>&1 && return 0
  pgrep -x steamwebhelper >/dev/null 2>&1 && return 0
  # 2. Windows Steam seen from WSL via tasklist.exe
  if command -v tasklist.exe >/dev/null 2>&1; then
    tasklist.exe /FI "IMAGENAME eq steam.exe" 2>/dev/null | grep -qi 'steam.exe' && return 0
  fi
  return 1
}

# The logon state on the newest line of the connection log, e.g. "Logged On".
steam_logon_state() {
  [[ -r "$STEAM_CONNECTION_LOG" ]] || return 1
  # Not every line carries a state (`CClientJobGetClientUpdateHosts: ...` does not), so take the
  # newest line that does.
  tail -n 200 "$STEAM_CONNECTION_LOG" | sed -nE 's/^\[[^]]*\] \[([A-Za-z ]+), [0-9]+, [0-9]+\].*/\1/p' | tail -n 1
}

steam_signed_in() {
  steam_running || return 1
  [[ "$(steam_logon_state)" == "Logged On" ]]
}

steam_start() {
  steam_running && return 0
  # No `-silent`: it hides the window behind a tray icon, and this desktop has no tray, so a
  # silently started Steam has no window to bring back (2026-10-27).
  setsid -f steam </dev/null >/dev/null 2>&1
}

steam_ensure() {
  steam_start
  steam_signed_in && return 0
  # Wait for Steam's own log to report the logon, capped at 25 s (repo rule: no sleep polling,
  # no wait over 30 s). A sign-in that takes longer is the user's blocker, reported as exit 2.
  timeout 25 tail -n 0 -F "$STEAM_CONNECTION_LOG" 2>/dev/null | grep -q -m 1 '\[Logged On, '
  steam_signed_in && return 0
  return 2
}

# When executed directly (not sourced), behave as a predicate + print a line.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    --signed-in)
      if steam_signed_in; then echo "steam: SIGNED IN"; exit 0; fi
      echo "steam: NOT signed in (running=$(steam_running && echo yes || echo no), state=$(steam_logon_state || echo unknown))"
      exit 1 ;;
    --ensure)
      steam_ensure
      rc=$?
      if ((rc == 0)); then echo "steam: SIGNED IN"; exit 0; fi
      echo "steam: running but NOT signed in (state=$(steam_logon_state || echo unknown)) -- the user must sign in"
      exit 2 ;;
    "")
      if steam_running; then echo "steam: RUNNING"; exit 0; else echo "steam: NOT running"; exit 1; fi ;;
    *)
      echo "usage: steam-running.sh [--signed-in|--ensure]" >&2; exit 64 ;;
  esac
fi
