#!/usr/bin/env bash
# Run sweep_one.sh over every vertex shader in a vkd3d-proton dump, in parallel.
#
# Usage: sweep_all.sh <dump dir> <list of hashes, one per line> <work dir> > results.jsonl
# Environment: as sweep_one.sh and lift_one.sh. Four workers: more pushed the terminal scope into an OOM kill.
set -u
dump=$1
list=$2
work=$3
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$work"
sed "s|^|$dump/|; s|\$|.dxil|" "$list" \
  | xargs -P 4 -I{} bash "$here/sweep_one.sh" {} "$work"
