#!/usr/bin/env bash
# One shader through both research paths, printing one JSON line.
#
#   a) DXIL patch: dxc -dumpbin -> (plain | patch_vs_ll.py wobble) -> IDxcAssembler -> validator
#      signs in place -> dxv -> vscmp against the original.
#   b) lift: dxil-spirv (register bindings, invariant position) -> spirv-cross --hlsl -> dxc
#      -T vs_6_0 -> dxv -> vscmp against the original. The lift is only attempted when
#      SWEEP_LIFT=1, because it is the slow half.
#
# Usage: sweep_one.sh <path/to/hash.dxil> <work dir>
# Environment: DXC_DIR (default ~/tools/dxc), DXA (dxa_lite binary), DXIL_SPIRV, SPIRV_CROSS.
set -u
src=$1
work=$2
here=$(cd "$(dirname "$0")" && pwd)
DXC_DIR=${DXC_DIR:-$HOME/tools/dxc}
DXA=${DXA:?set DXA to the built dxa_lite}
h=$(basename "$src" .dxil)
w=$work/$h
mkdir -p "$w"

now_ms() { date +%s%3N; }
json_str() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()[:300]))'; }

t0=$(now_ms)
"$DXC_DIR/bin/dxc" -dumpbin "$src" -Fc "$w/orig.ll" > /dev/null 2> "$w/dumpbin.err"
dump_rc=$?
t1=$(now_ms)

rt_asm=-1; rt_ir_same=false; rt_cmp=null
if [ $dump_rc -eq 0 ]; then
  "$DXA" "$w/orig.ll" "$w/rt.dxil" 2> "$w/rt.err"; rt_asm=$?
  if [ $rt_asm -eq 0 ]; then
    "$DXC_DIR/bin/dxc" -dumpbin "$w/rt.dxil" -Fc "$w/rt.ll" > /dev/null 2>&1
    if cmp -s <(sed -n '/^target datalayout/,$p' "$w/orig.ll") \
              <(sed -n '/^target datalayout/,$p' "$w/rt.ll"); then rt_ir_same=true; fi
    rt_cmp=$(python3 "$here/vscmp.py" "$src" "$w/rt.dxil" --json)
  fi
fi
t2=$(now_ms)

patch_rc=-1; w_asm=-1; w_dxv=-1; w_cmp=null; patch_err='""'; w_err='""'
if [ $dump_rc -eq 0 ]; then
  python3 "$here/patch_vs_ll.py" "$w/orig.ll" > "$w/wobble.ll" 2> "$w/patch.err"; patch_rc=$?
  patch_err=$(json_str < "$w/patch.err")
  if [ $patch_rc -eq 0 ]; then
    t3=$(now_ms)
    "$DXA" "$w/wobble.ll" "$w/wobble.dxil" 2> "$w/wobble.err"; w_asm=$?
    t4=$(now_ms)
    w_err=$(json_str < "$w/wobble.err")
    if [ $w_asm -eq 0 ]; then
      "$DXC_DIR/bin/dxv" "$w/wobble.dxil" > "$w/wobble.dxv" 2>&1; w_dxv=$?
      w_cmp=$(python3 "$here/vscmp.py" "$src" "$w/wobble.dxil" --json)
    fi
  fi
fi

lift='null'
if [ "${SWEEP_LIFT:-0}" = 1 ] && [ $dump_rc -eq 0 ]; then
  lift=$(bash "$here/lift_one.sh" "$src" "$w")
fi

printf '{"hash":"%s","dump_rc":%d,"dump_ms":%d,"rt_asm":%d,"rt_ir_same":%s,"rt_ms":%d,"rt":%s,' \
  "$h" "$dump_rc" $((t1 - t0)) "$rt_asm" "$rt_ir_same" $((t2 - t1)) "$rt_cmp"
printf '"patch_rc":%d,"patch_err":%s,"w_asm":%d,"w_err":%s,"w_dxv":%d,"w":%s,"lift":%s}\n' \
  "$patch_rc" "$patch_err" "$w_asm" "$w_err" "$w_dxv" "$w_cmp" "$lift"
