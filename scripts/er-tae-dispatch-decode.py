#!/usr/bin/env python3
"""Decode the Elden Ring TAE event-type dispatch tables out of the flat de-obfuscated image.

Two native functions dispatch a TAE event by its `eventType` int:

  * `ExecuteThreadOne`  @ 0x14042e1a0 -- four jump tables + a compare chain.
  * `ExecuteThreadTwo`  @ 0x14042f150 -- one jump table + a compare chain.

Each table is the MSVC two-level form: a byte index table selects a slot in a
dword table of RVAs, and the RVA is added to the image base to reach a small
trampoline that tail-jumps to the real handler.  This script walks both levels,
follows the trampoline with capstone, and prints `eventId -> handlerVA`.

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
DEFAULT_IMAGE = os.environ.get('ER_DEOBF_BIN', 'eldenring-deobf.bin')

#: (name, firstEventId, lastEventId, byteTableRVA or None, dwordTableRVA)
#: A `None` byte table means the dword table is indexed directly by (id - first).
TABLES = [
    ('ExecuteThreadOne', 0x000, 0x0EE, 0x42EDA0, 0x42ECE8),
    ('ExecuteThreadOne', 0x12E, 0x20A, 0x42EED4, 0x42EE90),
    ('ExecuteThreadOne', 0x259, 0x320, 0x42F060, 0x42EFB4),
    ('ExecuteThreadOne', 0x388, 0x38F, None,     0x42F128),
    ('ExecuteThreadTwo', 0x000, 0x0E5, 0x42F460, 0x42F404),
]

#: Ids handled by an explicit `cmp`/`jz` outside any table, transcribed from the
#: dispatchers' own disassembly.  Value is the trampoline VA to follow.
CHAIN = [
    ('ExecuteThreadOne', 0x12C, 0x14042E4CD),
    ('ExecuteThreadOne', 0x258, 0x14042E764),
    ('ExecuteThreadOne', 0x387, 0x14042EC1A),
    ('ExecuteThreadOne', 0x2770, 0x14042ECD8),
    ('ExecuteThreadTwo', 0x130, 0x14042F2AF),
    ('ExecuteThreadTwo', 0x133, 0x14042F302),
    ('ExecuteThreadTwo', 0x153, 0x14042F2F6),
    ('ExecuteThreadTwo', 0x154, 0x14042F2EA),
    ('ExecuteThreadTwo', 0x157, 0x14042F30E),
    ('ExecuteThreadTwo', 0x261, 0x14042F35A),
    ('ExecuteThreadTwo', 0x2E4, 0x14042F32C),
    ('ExecuteThreadTwo', 0x384, 0x14042F366),
    ('ExecuteThreadTwo', 0x385, 0x14042F3D8),
    ('ExecuteThreadTwo', 0x386, 0x14042F3CC),
    ('ExecuteThreadTwo', 0x2792, 0x14042F228),
    ('ExecuteThreadTwo', 0x2799, 0x14042F3C0),
    ('ExecuteThreadTwo', 0x279A, 0x14042F3B4),
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
