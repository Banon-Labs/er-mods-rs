#!/usr/bin/env bash
# The `--previous` patch over every velocity-pass vertex shader of a finished sweep: patch,
# assemble and sign (dxa_lite), dxv, and the structural check in check_previous.py. Prints one
# line per shader.
#
# Usage: sweep_previous.sh <sweep dir>     (reads <sweep dir>/index.tsv and work/<hash>/orig.ll)
# Environment: DXC_DIR (default ~/tools/dxc), DXA (dxa_lite binary).
set -u
root=$1
here=$(cd "$(dirname "$0")" && pwd)
DXC_DIR=${DXC_DIR:-$HOME/tools/dxc}
DXA=${DXA:?set DXA to the built dxa_lite}
awk -F'\t' '$2 == 1 && $4 ~ /_Velo/ { sub(/\.dxil$/, "", $1); print $1 }' "$root/index.tsv" |
while read -r h; do
  w=$root/work/$h
  python3 "$here/patch_vs_ll.py" "$w/orig.ll" --previous > "$w/wprev.ll" 2> "$w/wprev.err"
  p=$?
  a=-1; v=-1; c=-
  if [ $p -eq 0 ]; then
    "$DXA" "$w/wprev.ll" "$w/wprev.dxil" 2>> "$w/wprev.err"; a=$?
    if [ $a -eq 0 ]; then
      "$DXC_DIR/bin/dxv" "$w/wprev.dxil" > /dev/null 2>&1; v=$?
      c=$(python3 "$here/check_previous.py" "$w/wobble.ll" "$w/wprev.ll")
    fi
  fi
  printf '%s\tpatch=%d\tasm=%d\tdxv=%d\t%s\t%s\n' "$h" "$p" "$a" "$v" "$c" \
    "$(tr '\n' ' ' < "$w/wprev.err" | cut -c1-120)"
done
