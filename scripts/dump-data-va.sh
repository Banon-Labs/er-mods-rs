#!/usr/bin/env bash
# Dump raw bytes from a .data/.rdata VA in the installed eldenring.exe.
# The exe is not a flat image: each section's file offset differs from its rva (on 2.7.2.0
# `.rdata` sits 0x1600 and `.data` 0x2400 below their rvas in the file; on 1.16.2 it was 0xe00
# and 0x1200), so the VA is mapped through the exe's own section table before reading.
# The de-Arxan'd flat image (eldenring-deobf-1.17.2.bin) holds the same bytes at offset == rva.
# Usage: dump-data-va.sh <VA hex, e.g. 0x143d00000> [num_bytes_dec, default 176]
# ER_EXE overrides the exe path.
set -euo pipefail
EXE="${ER_EXE:-$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game/eldenring.exe}"
VA="$1"
NB="${2:-176}"
va_dec=$((VA))
# The VA a byte at file offset 0 would have if its section's mapping held for the whole file.
adjust=$(python3 - "$EXE" "$va_dec" <<'PY'
import struct
import sys

path, va = sys.argv[1], int(sys.argv[2])
rva = va - 0x140000000
with open(path, "rb") as f:
    head = f.read(0x1000)
pe = struct.unpack_from("<I", head, 0x3C)[0]
count = struct.unpack_from("<H", head, pe + 6)[0]
optional = struct.unpack_from("<H", head, pe + 20)[0]
entry = pe + 24 + optional
for _ in range(count):
    vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", head, entry + 8)
    if vaddr <= rva < vaddr + max(vsize, rsize):
        print(0x140000000 + vaddr - rptr)
        sys.exit(0)
    entry += 40
sys.exit(f"dump-data-va: rva {rva:#x} is in no section of {path}")
PY
)
objdump -s -b binary -m i386:x86-64 \
  --adjust-vma="$adjust" \
  --start-address=$((va_dec)) \
  --stop-address=$((va_dec + NB)) \
  "$EXE"
