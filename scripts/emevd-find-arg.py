#!/usr/bin/env python3
"""List the EMEVD instructions whose argument bytes contain a given dword, offline.

Reads an uncompressed Elden Ring `.emevd` (64-bit, little-endian, `EVD\\0`) and prints, for every
instruction whose argument block holds one of the values as an aligned little-endian dword, the
owning event id, the instruction's position, its `bank[id]`, and the argument block as dwords.
The parameter-substitution table is printed with it, so an argument the event fills from its
caller shows up as such.

    python3 scripts/emevd-find-arg.py <file.emevd> 9153 [value ...]
    python3 scripts/emevd-find-arg.py <file.emevd> --event 1110 [--event ...]

No DarkScript3 is needed: the bank/id pairs are printed raw. 2003[4] is AwardItemLot,
2003[43] DirectlyGivePlayerItem, 2003[66] SetEventFlag, 2000[0] InitializeEvent.
"""

import argparse
import struct
import sys


def parse(path):
    d = open(path, "rb").read()
    if d[:4] != b"EVD\x00":
        raise SystemExit(f"{path}: not an uncompressed EVD (magic {d[:4]!r})")
    (event_count, events_off, instr_count, instr_off, _u, _uo, layer_count, layer_off,
     param_count, param_off, _lc, _lo, args_len, args_off) = struct.unpack_from("<14Q", d, 0x10)
    events = []
    for i in range(event_count):
        eid, icount, ioff, pcount, poff, rest = struct.unpack_from("<QQQQqI", d, events_off + i * 0x30)
        instrs = []
        for j in range(icount):
            o = instr_off + ioff + j * 0x20
            bank, iid, alen, aoff, loff = struct.unpack_from("<IIQqq", d, o)
            raw = d[args_off + aoff:args_off + aoff + alen] if alen else b""
            instrs.append((bank, iid, raw))
        params = []
        for k in range(pcount):
            o = param_off + poff + k * 0x20
            iidx, tgt, src, size = struct.unpack_from("<QQQi", d, o)
            params.append((iidx, tgt, src, size))
        events.append((eid, rest, instrs, params))
    return events


def dwords(raw):
    return [struct.unpack_from("<i", raw, i)[0] for i in range(0, len(raw) - len(raw) % 4, 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("values", nargs="*", type=int)
    ap.add_argument("--event", action="append", type=int, default=[])
    a = ap.parse_args()
    for eid, rest, instrs, params in parse(a.path):
        if a.event and eid in a.event:
            print(f"event {eid} rest={rest} instructions={len(instrs)} params={params}")
            for j, (bank, iid, raw) in enumerate(instrs):
                print(f"  [{j:3}] {bank}[{iid:02}] {dwords(raw)}")
            continue
        for j, (bank, iid, raw) in enumerate(instrs):
            words = dwords(raw)
            if any(v in words for v in a.values):
                print(f"event {eid} [{j}] {bank}[{iid:02}] {words}")


if __name__ == "__main__":
    sys.exit(main())
