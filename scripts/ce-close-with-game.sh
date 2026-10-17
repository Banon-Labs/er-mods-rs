#!/bin/bash
# Close every Cheat Engine running in Elden Ring's Wine prefix once the game is gone (a crash or
# a quit), so no orphaned Cheat Engine window is left behind. Installed as the user service
# ce-close-with-game.service. A Cheat Engine in any other prefix is never touched.
#
# Nothing here samples. While a game runs, the service blocks on that process's pidfd, which
# becomes readable the moment it exits. While none runs, it blocks on PipeWire's event stream
# (`pactl subscribe`) until a new playback stream appears: the game opens one as it boots, and so
# does Cheat Engine (audio-stream-watch.sh mutes it), so either start wakes the service. A process
# start has no unprivileged event of its own; the netlink process connector needs CAP_NET_ADMIN.
PREFIX_MARK='compatdata/1245620/pfx'

scan() {
  game=()
  ce=()
  local d cmd
  for d in /proc/[0-9]*; do
    cmd=$( { tr '\0' ' ' <"$d/cmdline"; } 2>/dev/null) || continue
    case "$cmd" in
      *eldenring.exe*) game+=("${d#/proc/}") ;;
      *cheatengine*|*"Cheat Engine"*)
        tr '\0' '\n' <"$d/environ" 2>/dev/null | grep -q "^WINEPREFIX=.*$PREFIX_MARK" && ce+=("${d#/proc/}") ;;
    esac
  done
}

# Block until the pid exits. A pid that is already gone returns at once.
wait_exit() {
  python3 -c 'import os, select, sys
try:
    fd = os.pidfd_open(int(sys.argv[1]))
except ProcessLookupError:
    sys.exit(0)
select.select([fd], [], [])' "$1"
}

while true; do
  # Subscribe before scanning, so a game that starts between the scan and the read is still seen.
  coproc SUB { exec pactl subscribe; }
  scan
  if [ "${#game[@]}" -gt 0 ]; then
    kill "$SUB_PID" 2>/dev/null
    wait "$SUB_PID" 2>/dev/null
    wait_exit "${game[0]}"
    continue
  fi
  if [ "${#ce[@]}" -gt 0 ]; then
    echo "eldenring.exe is gone; closing Cheat Engine pids ${ce[*]}"
    kill "${ce[@]}" 2>/dev/null
  fi
  woke=0
  while read -r line <&"${SUB[0]}"; do
    case "$line" in
      *"'new' on sink-input #"*) woke=1; break ;;
    esac
  done
  kill "$SUB_PID" 2>/dev/null
  wait "$SUB_PID" 2>/dev/null
  if [ "$woke" = 0 ]; then
    # The event stream ended (no PulseAudio server, or it restarted). Exit rather than spin, and
    # let the service's Restart= bring this back once the server is up.
    echo "pactl subscribe ended; exiting" >&2
    exit 1
  fi
done
