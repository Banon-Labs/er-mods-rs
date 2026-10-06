#!/usr/bin/env python3
"""List EMEVD events whose instructions carry a given int32 argument. Read-only.

Reads decompressed `.emevd` files (Elden Ring, 64-bit "EVD" layout). The
extracted corpus lives under
`~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/event/`.

    python3 scripts/er-emevd-find.py 9621 <file.emevd>...
    python3 scripts/er-emevd-find.py 9621 <file.emevd> --dump-event 90005300

Per hit it prints the event id, the instruction index, `bank[id]` and the raw
int32 view of the argument block, then every instruction of that event.
Instruction names (for example 2004[08] = SetSpEffect) are not decoded here;
look them up in an EMEDF.

Layout, header offsets as i64 pairs (count, offset): events 0x10, instructions
0x20, parameters 0x50, arguments 0x70. Event record 0x30 bytes: id, instruction
count, instruction offset, parameter count, parameter offset, restart u32.
Instruction record 0x20 bytes: bank u32, id u32, args length i64, args offset
i64. Parameter record 0x20 bytes: instruction index, target byte, source byte,
byte count.
"""
import struct
import sys


def parse(path):
    d = open(path, 'rb').read()
    if d[:4] != b'EVD\0':
        raise SystemExit(f'{path}: not a decompressed EMEVD (magic {d[:4]!r})')

    def q(o):
        return struct.unpack_from('<q', d, o)[0]

    ev_n, ev_off = q(0x10), q(0x18)
    ins_off = q(0x28)
    par_off = q(0x58)
    args_off = q(0x78)
    events = []
    for i in range(ev_n):
        o = ev_off + i * 0x30
        eid, icount, ioff, pcount, poff = struct.unpack_from('<qqqqq', d, o)
        rest = struct.unpack_from('<I', d, o + 0x28)[0]
        instrs = []
        for k in range(icount):
            io = ins_off + ioff + k * 0x20
            bank, iid, alen, aoff = struct.unpack_from('<IIqq', d, io)
            raw = d[args_off + aoff: args_off + aoff + alen]
            ints = [struct.unpack_from('<i', raw, j)[0] for j in range(0, len(raw) - 3, 4)]
            instrs.append((bank, iid, ints))
        params = [struct.unpack_from('<qqqq', d, par_off + poff + k * 0x20)
                  for k in range(pcount)]
        events.append((eid, rest, instrs, params))
    return events


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return
    dump = None
    if '--dump-event' in argv:
        i = argv.index('--dump-event')
        dump = int(argv[i + 1])
        del argv[i:i + 2]
    val = int(argv[0])
    for f in argv[1:]:
        for eid, rest, instrs, params in parse(f):
            hits = [k for k, (_, _, ints) in enumerate(instrs) if val in ints]
            for k in hits:
                b, i, ints = instrs[k]
                print(f'{f} event {eid} instr {k}: {b}[{i:02d}] {ints}')
            if (dump is None and hits) or eid == dump:
                print(f'  -- event {eid} restart={rest} params={params}')
                for k, (b, i, ints) in enumerate(instrs):
                    print(f'     {k:3d} {b}[{i:02d}] {ints}')


if __name__ == '__main__':
    main()
