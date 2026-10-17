#!/usr/bin/env bash
# Run the CE voice probe while recording Elden Ring's own PipeWire output stream.
#
#   bash scripts/er-voice-capture-run.sh <out.wav> [extra er-voice-probe-ce.py args...]
#
# Window focus is never changed: the user may be playing. The game writes exact zeros while its
# window is unfocused, so the capture only counts when the game window (class steam_app_1245620,
# queried alone, nothing else printed) has focusHistoryID 0 both before and after the run; this
# prints that check, and a capture taken unfocused is void. The probe's own ring log is the
# focus-independent oracle.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:?usage: er-voice-capture-run.sh <out.wav> [probe args...]}"
shift
focus() { hyprctl clients -j | jq -r '[.[] | select(.class=="steam_app_1245620") | .focusHistoryID][0] // "none"'; }
serial="$(pw-dump | jq -r '.[] | select(.type=="PipeWire:Interface:Node")
  | select((.info.props["application.name"]//"")|test("ELDEN";"i"))
  | select(.info.props["media.class"]=="Stream/Output/Audio") | .info.props["object.serial"]' | head -n1)"
[ -n "$serial" ] || { echo "no Elden Ring output stream in PipeWire" >&2; exit 1; }
address() { hyprctl clients -j | jq -r '[.[] | select(.class=="steam_app_1245620") | .address][0] // ""'; }

# Focus changes during the run, from Hyprland's event socket: every `activewindowv2>>ADDRESS`
# event becomes one line, `<time> focused` or `<time> unfocused`, comparing the address with the
# game window's own. No other window's address or title is written anywhere.
focus_events_py=$(cat <<'PY'
import os, socket, sys, time
game = sys.argv[1].lower().removeprefix('0x')
path = os.path.join(os.environ['XDG_RUNTIME_DIR'], 'hypr', os.environ['HYPRLAND_INSTANCE_SIGNATURE'], '.socket2.sock')
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(path)
print(f'{time.time():.6f} {sys.argv[2]}', flush=True)
buf = b''
for chunk in iter(lambda: sock.recv(4096), b''):
    buf += chunk
    *lines, buf = buf.split(b'\n')
    for line in lines:
        if line.startswith(b'activewindowv2>>'):
            now = line.split(b'>>', 1)[1].decode().lower().removeprefix('0x')
            print(f'{time.time():.6f} {"focused" if now == game else "unfocused"}', flush=True)
PY
)

# Wait until the capture file has grown by $2 bytes past $1 (inotify on its directory), or until
# pw-record (pid $3) exits. Capped at 10 s.
grow_py=$(cat <<'PY'
import os, pathlib, select, sys, time
sys.path.insert(0, sys.argv[4])
import er_run_lib
out, target, pid = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
pidfd = os.pidfd_open(pid)
deadline = time.monotonic() + 10
with er_run_lib.DirectoryWatch(out.parent) as watch:
    while out.stat().st_size < target:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            sys.exit(f'{out} did not grow to {target} bytes within 10 s')
        ready, _, _ = select.select([watch.fd, pidfd], [], [], remaining)
        if pidfd in ready:
            sys.exit('pw-record exited before the tail was captured')
        if watch.fd in ready:
            os.read(watch.fd, 65536)
PY
)

before=$(focus)
echo "game focusHistoryID before: $before"
game_address=$(address)
pw-record --target "$serial" --rate 48000 --channels 2 "$out" &
rec=$!
echo "recording stream serial $serial to $out (pid $rec)"
[ "$before" = 0 ] && initial=focused || initial=unfocused
python3 -c "$focus_events_py" "$game_address" "$initial" > "$out.focus" &
watcher=$!
python3 "$repo/scripts/cheat-engine/er-voice-probe-ce.py" --voice-probe "$@" || echo "probe exit $?"
end=$(date +%s.%N)
kill "$watcher" 2>/dev/null || true
echo "game focusHistoryID after: $(focus)"
echo "focus during the run: $(awk -v end="$end" '
  NR > 1 { t[prev] += $1 - at; changes++ } { prev = $2; at = $1 }
  END { t[prev] += end - at; printf "focused %.2f s, unfocused %.2f s, %d focus changes", t["focused"], t["unfocused"], changes }' "$out.focus")"
# Half a second more of the game's output (48 kHz, 2 channels, s16), counted in the capture file
# itself rather than waited out, so the tail of the last voice frame is in the recording.
python3 -c "$grow_py" "$out" "$(( $(stat -c %s "$out") + 48000 * 2 * 2 / 2 ))" "$rec" "$repo/scripts" \
  || echo "tail wait: exit $?"
kill -INT "$rec"
wait "$rec" || true
echo "capture stopped"
