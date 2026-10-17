#!/bin/bash
# Print the identifying properties and mute state of every new playback stream, one line each,
# and mute any stream owned by a Cheat Engine process.
# The WirePlumber rule in ~/.config/wireplumber/wireplumber.conf.d/60-mute-cheat-engine.conf mutes
# by stream name; Wine names some streams after something else, so this also matches the owning
# process's command line (the pid namespace is shared with the game's container).
# Installed as the user service cheat-engine-mute.service.
#
# A stream's owning pid can arrive after its 'new' event. Rather than wait a fixed time for it,
# a stream with no pid yet is looked at again on each 'change' event for it, until one carries the
# pid or the stream is removed.
declare -A resolved=()

inspect() {
  local id=$1 info pid client action=""
  info=$(pactl list sink-inputs | awk -v id="$id" '
    /^Sink Input #/ { on = ($3 == "#" id) }
    on && /Mute:|application\.name|application\.id|application\.process\.(id|binary)|media\.role|media\.name/ { gsub(/^[ \t]+/, ""); printf "%s | ", $0 }')
  pid=$(sed -n 's/.*application\.process\.id = "\([0-9]*\)".*/\1/p' <<<"$info")
  if [ -z "$pid" ]; then
    # Native PipeWire clients put the pid on the client, not the stream.
    client=$(pactl list sink-inputs | awk -v id="$id" '/^Sink Input #/ { on = ($3 == "#" id) } on && /Client:/ { print $2; exit }')
    pid=$(pactl list clients | awk -v c="$client" '/^Client #/ { on = ($2 == "#" c) } on && /application\.process\.id/ { gsub(/"/, "", $3); print $3; exit }')
  fi
  if [ -z "$pid" ]; then
    [ "$2" = new ] && echo "new stream #$id: $info (no owning pid yet)"
    return
  fi
  resolved[$id]=1
  if tr '\0' ' ' </proc/"$pid"/cmdline 2>/dev/null | grep -qiE 'cheat ?engine'; then
    pactl set-sink-input-mute "$id" 1 && action="muted (Cheat Engine pid $pid)"
  fi
  echo "new stream #$id: $info $action"
}

pactl subscribe | while read -r line; do
  case "$line" in
    *"'new' on sink-input #"*) inspect "${line##*#}" new ;;
    *"'change' on sink-input #"*)
      id=${line##*#}
      [ -z "${resolved[$id]}" ] && inspect "$id" change
      ;;
    *"'remove' on sink-input #"*) unset "resolved[${line##*#}]" ;;
  esac
done
