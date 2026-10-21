#!/usr/bin/env bash
# Lift one vertex shader DXIL -> SPIR-V -> HLSL -> DXIL and compare it with the original.
# Called by sweep_one.sh, which has already written <work>/orig.ll. Prints one JSON object.
#
# Usage: lift_one.sh <path/to/hash.dxil> <work dir for this hash>
# Environment: DXC_DIR, DXIL_SPIRV, SPIRV_CROSS.
set -u
src=$1
w=$2
here=$(cd "$(dirname "$0")" && pwd)
DXC_DIR=${DXC_DIR:-$HOME/tools/dxc}
DXIL_SPIRV=${DXIL_SPIRV:?set DXIL_SPIRV}
SPIRV_CROSS=${SPIRV_CROSS:?set SPIRV_CROSS}

now_ms() { date +%s%3N; }
t0=$(now_ms)
"$DXIL_SPIRV" "$src" --output "$w/lift.spv" --invariant-position --ssbo-srv \
  > "$w/lift.spv.err" 2>&1
spv=$?
sc=-1; fix=-1; cc=-1; dxv=-1; cmp=null; pos_same=false; pos_same_noflags=false
wob_cc=-1; wob_cmp=null
if [ $spv -eq 0 ]; then
  "$SPIRV_CROSS" "$w/lift.spv" --hlsl --shader-model 60 --output "$w/lift.hlsl" \
    > "$w/lift.sc.err" 2>&1
  sc=$?
fi
t1=$(now_ms)
if [ $sc -eq 0 ]; then
  python3 "$here/fix_lift_hlsl.py" "$src" "$w/lift.hlsl" --no-precise > "$w/lift.fixed.hlsl" \
    2> "$w/lift.fix.err"
  fix=$?
fi
t2=$(now_ms)
if [ $fix -eq 0 ]; then
  "$DXC_DIR/bin/dxc" -T vs_6_0 -E main -O3 -HV 2021 -validator-version 1.6 -Qstrip_debug \
    "$w/lift.fixed.hlsl" -Fo "$w/lift.dxil" > "$w/lift.dxc.err" 2>&1
  cc=$?
fi
t3=$(now_ms)
if [ $cc -eq 0 ]; then
  "$DXC_DIR/bin/dxv" "$w/lift.dxil" > "$w/lift.dxv" 2>&1; dxv=$?
  cmp=$(python3 "$here/vscmp.py" "$src" "$w/lift.dxil" --json)
  "$DXC_DIR/bin/dxc" -dumpbin "$w/lift.dxil" -Fc "$w/lift.ll" > /dev/null 2>&1
  python3 "$here/pos_slice.py" "$w/orig.ll" > "$w/orig.pos" 2>&1
  python3 "$here/pos_slice.py" "$w/lift.ll" > "$w/lift.pos" 2>&1
  if cmp -s "$w/orig.pos" "$w/lift.pos" && [ -s "$w/orig.pos" ]; then pos_same=true; fi
  if cmp -s <(sed 's/flags=[0-9a-f]* //' "$w/orig.pos") <(sed 's/flags=[0-9a-f]* //' "$w/lift.pos") \
     && [ -s "$w/orig.pos" ]; then pos_same_noflags=true; fi
  python3 "$here/fix_lift_hlsl.py" "$src" "$w/lift.hlsl" --no-precise --wobble \
    > "$w/lift.wobble.hlsl" 2> /dev/null && \
  "$DXC_DIR/bin/dxc" -T vs_6_0 -E main -O3 -HV 2021 -validator-version 1.6 -Qstrip_debug \
    "$w/lift.wobble.hlsl" -Fo "$w/lift.wobble.dxil" > "$w/lift.wobble.err" 2>&1
  wob_cc=$?
  if [ $wob_cc -eq 0 ]; then
    wob_cmp=$(python3 "$here/vscmp.py" "$src" "$w/lift.wobble.dxil" --json)
  fi
fi
err=$(cat "$w/lift.spv.err" "$w/lift.sc.err" "$w/lift.fix.err" "$w/lift.dxc.err" 2>/dev/null \
  | head -c 300 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
printf '{"spv":%d,"sc":%d,"fix":%d,"cc":%d,"dxv":%d,"pos_same":%s,"pos_same_noflags":%s,' \
  "$spv" "$sc" "$fix" "$cc" "$dxv" "$pos_same" "$pos_same_noflags"
printf '"lift_ms":%d,"fix_ms":%d,"dxc_ms":%d,"cmp":%s,"wob_cc":%d,"wob":%s,"err":%s}\n' \
  $((t1 - t0)) $((t2 - t1)) $((t3 - t2)) "$cmp" "$wob_cc" "$wob_cmp" "$err"
