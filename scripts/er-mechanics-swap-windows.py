#!/usr/bin/env python3
"""Print the TimeAct jump-table windows and hit frames of player clips, in real 30 fps frames.

Written to answer whether a d-pad weapon swap shortens the gap between powerstance attacks: it
lists, per clip, the input windows (9 L-hand, 87 common), the cancel windows (16 LH attack,
117 L1, 32 jump/crouch/weapon switch, 26 dodge, 11 move), their type-300 early openings and the
type-1 hit events, using the same readers as `er-mechanics-attacks.py` (import following, TAE 608
play speed, clip length from the hkx).

    python3 scripts/er-mechanics-swap-windows.py 23_34000 0_29031
    python3 scripts/er-mechanics-swap-windows.py --all 23_34000
"""
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('er_mechanics_attacks',
                                               os.path.join(HERE, 'er-mechanics-attacks.py'))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

NAMES = {1: 'in-R', 9: 'in-L', 87: 'in-common', 25: 'in-dodge', 21: 'in-guard', 30: 'in-goods',
         4: 'cx-R', 115: 'cx-R1', 116: 'cx-R2', 16: 'cx-L', 117: 'cx-L1', 26: 'cx-dodge',
         22: 'cx-guard', 32: 'cx-swap', 31: 'cx-goods', 11: 'cx-move', 78: 'cx-aimove',
         34: 'cx-generic', 29: 'cx-magic', 103: 'cx-qWA', 104: 'cx-WA', 107: 'cx-qgoods',
         108: 'in-qgoods', 105: 'in-qWA', 106: 'in-WA', 120: 'in-L1L2wp', 121: 'cx-L1L2wp',
         8: 'iframes', 35: 'in-1stR', 36: 'in-1stL', 54: 'no-inputs', 10: 'in-magic'}
KEY_IDS = {1, 9, 87, 4, 16, 117, 32, 26, 11, 54, 36}


def show(cat, anim, everything):
    c, a, ev = A.resolve_events(cat, anim)
    if ev is None:
        print(f'== a{cat:03d}_{anim:06d}: no TAE entry')
        return
    real = A.clip_to_real(ev)
    path = A.hkx_path(c, a)
    dur = A.hkx_duration(path) if path else None
    length = A.real_frame(real(dur[0])) if dur else '?'
    print(f'== a{cat:03d}_{anim:06d} (events from a{c:03d}_{a:06d}) clip {length}f')
    rows = []
    for e in ev:
        span = f'{A.real_frame(real(e.start)):>5}-{A.real_frame(real(e.end)):<5}'
        if e.type == A.TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            gate = struct.unpack_from('<H', e.params, A.JUMP_TABLE_STATE_GATE_OFFSET)[0]
            if everything or jid in KEY_IDS:
                note = f' gated on state info {gate}' if gate else ''
                rows.append((e.start, f'  {span} jt{jid:<4}{NAMES.get(jid, "")}{note}'))
        elif e.type == A.TAE_JUMP_TABLE_EARLY:
            jid, early_type, w0, w1 = struct.unpack_from('<hhff', e.params, 0)
            if everything or jid in KEY_IDS:
                rows.append((e.start, f'  {span} early jt{jid:<4}{NAMES.get(jid, "")} '
                                      f'type{early_type} w{w0}->{w1}'))
        elif e.type == A.TAE_ATTACK_BEHAVIOR:
            rows.append((e.start, f'  {span} hit'))
    for _, row in sorted(rows):
        print(row)


def main():
    args = sys.argv[1:]
    everything = '--all' in args
    for item in (x for x in args if x != '--all'):
        cat, anim = item.split('_')
        show(int(cat), int(anim), everything)


if __name__ == '__main__':
    main()
