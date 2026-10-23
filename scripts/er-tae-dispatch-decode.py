#!/usr/bin/env python3
"""Decode the Elden Ring TAE event-type dispatch tables out of the flat de-obfuscated image.

Two native functions dispatch a TAE event by its `eventType` int:

  * `ExecuteThreadOne`  @ 0x14042e780 -- four jump tables + a compare chain.
  * `ExecuteThreadTwo`  @ 0x14042f730 -- one jump table + a compare chain.

Each table is the MSVC two-level form: a byte index table selects a slot in a
dword table of RVAs, and the RVA is added to the image base to reach a small
trampoline that tail-jumps to the real handler.  This script walks both levels,
follows the trampoline with capstone, and prints `eventId -> handlerVA`.

Every address is 2.7.2.0 (`eldenring-deobf-1.17.2.bin`). Both dispatchers are the
1.16.2 ones moved by `+0x5e0`: decoded instruction by instruction they have the same
lengths and the same bytes outside displacement and branch fields, every table below
and every chain target moved by that delta, the byte tables are byte-identical and
every dword-table entry is the 1.16.2 one plus `0x5e0` (1.16.2 had the dispatchers at
0x14042e1a0 and 0x14042f150).

Run under uv so capstone is provisioned:

    uv run --with capstone python3 scripts/er-tae-dispatch-decode.py
    uv run --with capstone python3 scripts/er-tae-dispatch-decode.py --json out.json
"""
import argparse
import json
import os
import struct
import sys

IMAGE_BASE = 0x140000000


def find_image(name='eldenring-deobf-1.17.2.bin'):
    """`ER_DEOBF_BIN` when set, else `name` at the repo root or the nearest directory above it.

    The images are gitignored, so a linked worktree under `.worktrees/` reads the main
    checkout's.
    """
    if os.environ.get('ER_DEOBF_BIN'):
        return os.environ['ER_DEOBF_BIN']
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = root
    while True:
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
        if os.path.dirname(d) == d:
            return os.path.join(root, name)
        d = os.path.dirname(d)


DEFAULT_IMAGE = find_image()

#: (name, firstEventId, lastEventId, byteTableRVA or None, dwordTableRVA)
#: A `None` byte table means the dword table is indexed directly by (id - first).
TABLES = [
    ('ExecuteThreadOne', 0x000, 0x0EE, 0x42F380, 0x42F2C8),
    ('ExecuteThreadOne', 0x12E, 0x20A, 0x42F4B4, 0x42F470),
    ('ExecuteThreadOne', 0x259, 0x320, 0x42F640, 0x42F594),
    ('ExecuteThreadOne', 0x388, 0x38F, None,     0x42F708),
    ('ExecuteThreadTwo', 0x000, 0x0E5, 0x42FA40, 0x42F9E4),
]

#: Ids handled by an explicit `cmp`/`jz` outside any table, transcribed from the
#: dispatchers' own disassembly.  Value is the trampoline VA to follow.
CHAIN = [
    ('ExecuteThreadOne', 0x12C, 0x14042EAAD),
    ('ExecuteThreadOne', 0x258, 0x14042ED44),
    ('ExecuteThreadOne', 0x387, 0x14042F1FA),
    ('ExecuteThreadOne', 0x2770, 0x14042F2B8),
    ('ExecuteThreadTwo', 0x130, 0x14042F88F),
    ('ExecuteThreadTwo', 0x133, 0x14042F8E2),
    ('ExecuteThreadTwo', 0x153, 0x14042F8D6),
    ('ExecuteThreadTwo', 0x154, 0x14042F8CA),
    ('ExecuteThreadTwo', 0x157, 0x14042F8EE),
    ('ExecuteThreadTwo', 0x261, 0x14042F93A),
    ('ExecuteThreadTwo', 0x2E4, 0x14042F90C),
    ('ExecuteThreadTwo', 0x384, 0x14042F946),
    ('ExecuteThreadTwo', 0x385, 0x14042F9B8),
    ('ExecuteThreadTwo', 0x386, 0x14042F9AC),
    ('ExecuteThreadTwo', 0x2792, 0x14042F808),
    ('ExecuteThreadTwo', 0x2799, 0x14042F9A0),
    ('ExecuteThreadTwo', 0x279A, 0x14042F994),
]

#: A trampoline is a handful of stack-teardown instructions plus one tail jump.
MAX_TRAMPOLINE_INSNS = 24


def follow(md, image, va):
    """Return (handlerVA, kind) for a dispatch trampoline: the tail-jmp target,
    the first direct call if the case is handled inline, or the trampoline itself."""
    offset = va - IMAGE_BASE
    first_call = None
    for insn in md.disasm(image[offset:offset + 0x100], va):
        if insn.mnemonic == 'jmp':
            operand = insn.op_str
            if operand.startswith('0x'):
                return int(operand, 16), 'tail-jmp'
            return va, 'jmp-indirect'
        if insn.mnemonic == 'call' and insn.op_str.startswith('0x') and first_call is None:
            first_call = int(insn.op_str, 16)
        if insn.mnemonic == 'ret':
            return (first_call, 'inline-call') if first_call else (va, 'inline')
        if insn.address - va > MAX_TRAMPOLINE_INSNS * 8:
            break
    return va, 'undecoded'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', default=DEFAULT_IMAGE, help='flat de-obfuscated image')
    parser.add_argument('--json', help='also write the decode as JSON here')
    args = parser.parse_args()

    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    with open(args.image, 'rb') as handle:
        image = handle.read()

    rows = []
    for dispatcher, first, last, byte_rva, dword_rva in TABLES:
        for event_id in range(first, last + 1):
            index = (image[byte_rva + event_id - first] if byte_rva is not None
                     else event_id - first)
            rva = struct.unpack_from('<I', image, dword_rva + index * 4)[0]
            handler, kind = follow(md, image, IMAGE_BASE + rva)
            rows.append((event_id, dispatcher, handler, kind, index))
    for dispatcher, event_id, trampoline in CHAIN:
        handler, kind = follow(md, image, trampoline)
        rows.append((event_id, dispatcher, handler, kind, -1))

    rows.sort(key=lambda r: (r[0], r[1]))
    for event_id, dispatcher, handler, kind, index in rows:
        print('%-6d 0x%-4x %-16s 0x%09x %-12s slot=%d'
              % (event_id, event_id, dispatcher, handler, kind, index))
    print('# %d (eventId, dispatcher) pairs; %d distinct event ids; %d distinct handlers'
          % (len(rows), len({r[0] for r in rows}), len({r[2] for r in rows})))
    if args.json:
        with open(args.json, 'w') as handle:
            json.dump([{'eventId': r[0], 'dispatcher': r[1], 'handler': '%x' % r[2],
                        'kind': r[3], 'slot': r[4]} for r in rows], handle, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
