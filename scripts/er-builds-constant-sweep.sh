#!/usr/bin/env bash
# Sensitivity sweep of an assumed constant of the PvP ranking (er-builds-pvp.py), each point
# scored against corpus adoption by er-builds-score-adoption.py.
#
#   bash scripts/er-builds-constant-sweep.sh <out-dir> <name>="<flags>" [<name>="<flags>" ...]
#   bash scripts/er-builds-constant-sweep.sh /tmp/x e3="--engagement-seconds 3" e5="--engagement-seconds 5"
#
# Points run two at a time (shared machine), each with --jobs ${SWEEP_JOBS:-4}, niced. Writes
# <out-dir>/rank-<name>.json, adopt-<name>.{json,txt} (score-adoption --full-only) and appends to
# <out-dir>/progress.txt. Long: run it in the background. Compare the points with
# er-builds-sweep-compare.py.
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
out=$1; shift
mkdir -p "$out"
jobs=${SWEEP_JOBS:-4}
one() {
  local n=$1 flags=$2 s rc
  s=$(date +%s)
  # shellcheck disable=SC2086  # flags are deliberately word-split
  nice -n 19 ionice -c 3 python3 "$repo/scripts/er-builds-pvp.py" --rl 150 --sort score --json \
    --jobs "$jobs" $flags > "$out/rank-$n.json" 2> "$out/rank-$n.err"
  rc=$?
  echo "$n rank rc=$rc $(( $(date +%s) - s ))s" >> "$out/progress.txt"
  nice -n 19 python3 "$repo/scripts/er-builds-score-adoption.py" --full-only --pvp "$out/rank-$n.json" \
    --json "$out/adopt-$n.json" > "$out/adopt-$n.txt" 2>&1
  echo "$n adoption rc=$?" >> "$out/progress.txt"
}
while [ $# -gt 0 ]; do
  one "${1%%=*}" "${1#*=}" &
  if [ $# -gt 1 ]; then one "${2%%=*}" "${2#*=}" & shift; fi
  shift
  wait
done
echo done >> "$out/progress.txt"
