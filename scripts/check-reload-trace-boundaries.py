#!/usr/bin/env python3
"""Is each er-reload-trace target an INSTRUCTION BOUNDARY in the 1.17 image?

`audit-1170-hook-targets.py --patch_safe` decodes FORWARD from the address, which
always succeeds on some byte stream. The sharper question for a stale detour is
whether the address is where an instruction actually STARTS -- because if it is not,
MinHook's five-byte JMP truncates the instruction that straddles it and the tail
becomes garbage the moment that code runs.

Answered by disassembling linearly from the enclosing `.pdata` function start.

Run: uv run --with capstone python3 scripts/check-reload-trace-boundaries.py [1170|1162]
"""

import bisect
import importlib.util
import os
import struct
import sys

import capstone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "aud", os.path.join(ROOT, "scripts", "audit-1170-hook-targets.py")
)
aud = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aud)
BASE = aud.BASE

sys.path.insert(0, os.path.join(ROOT, "scripts"))
_rt = importlib.util.spec_from_file_location(
    "rt", os.path.join(ROOT, "scripts", "audit-reload-trace-targets.py")
)
rt = importlib.util.module_from_spec(_rt)
_rt.loader.exec_module(rt)

FAILED8 = {0x67A420, 0x67A520, 0x82FAF0, 0x9A4670, 0x81EAD0, 0x7ACB00}


def pdata_ranges(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    off = pe + 24 + optsz
    entry = next(
        (
            blob[off + i * 40 : off + (i + 1) * 40]
            for i in range(nsec)
            if blob[off + i * 40 : off + i * 40 + 8].rstrip(b"\0") == b".pdata"
        ),
        None,
    )
    vsz, vaddr, rsz, _ = struct.unpack_from("<IIII", entry, 8)
    out = []
    for at in range(vaddr, vaddr + max(vsz, rsz), 12):
        begin, end, _u = struct.unpack_from("<III", blob, at)
        if begin and end > begin:
            out.append((begin, end))
    out.sort()
    return out


def main():
    # Default 1.17; pass `1162` to CALIBRATE -- the same test on the build the RVAs
    # came from must come back all-ENTRY, or the test itself is what is broken.
    which = sys.argv[1] if len(sys.argv) > 1 else "1170"
    image = aud.IMAGE_1162 if which == "1162" else aud.IMAGE_1170
    blob = open(image, "rb").read()
    ranges = pdata_ranges(blob)
    starts = [r[0] for r in ranges]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    mid_insn, mid_func, ok_entry = [], [], []
    for name, rva in rt.RVAS:
        i = bisect.bisect_right(starts, rva) - 1
        if i < 0 or rva >= ranges[i][1]:
            verdict, detail = "NO-PDATA-FUNC", "not inside any .pdata function"
            mid_func.append((name, rva, detail))
        else:
            fbeg, fend = ranges[i]
            boundary = False
            for insn in md.disasm(blob[fbeg:fend], BASE + fbeg):
                if insn.address - BASE == rva:
                    boundary = True
                    break
                if insn.address - BASE > rva:
                    break
            if rva == fbeg:
                verdict, detail = "ENTRY", f"is .pdata entry 0x{fbeg:x}"
                ok_entry.append((name, rva, detail))
            elif boundary:
                verdict = "MID-FUNCTION"
                detail = f"boundary, but +0x{rva - fbeg:x} into func 0x{fbeg:x}"
                mid_func.append((name, rva, detail))
            else:
                verdict = "MID-INSTRUCTION"
                detail = f"NOT an insn boundary; +0x{rva - fbeg:x} into func 0x{fbeg:x}"
                mid_insn.append((name, rva, detail))
        flag = "REFUSED8" if rva in FAILED8 else "PATCHED "
        print(f"{flag} {name:31s} 0x{rva:07x}  {verdict:16s} {detail}")

    print()
    print(f"{which} verdicts over all 40: ENTRY={len(ok_entry)} "
          f"MID-FUNCTION={len(mid_func)} MID-INSTRUCTION={len(mid_insn)}")
    pat_mid_insn = [x for x in mid_insn if x[1] not in FAILED8]
    pat_mid_func = [x for x in mid_func if x[1] not in FAILED8]
    pat_entry = [x for x in ok_entry if x[1] not in FAILED8]
    print(f"of the 34 MinHook ACTUALLY PATCHED: ENTRY={len(pat_entry)} "
          f"MID-FUNCTION={len(pat_mid_func)} MID-INSTRUCTION={len(pat_mid_insn)}")
    if pat_mid_insn:
        print("\nPATCHED MID-INSTRUCTION (five-byte JMP truncates a live instruction):")
        for n, r, d in pat_mid_insn:
            print(f"  {n:31s} 0x{r:07x}  {d}")


if __name__ == "__main__":
    main()
