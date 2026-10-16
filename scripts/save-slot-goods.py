#!/usr/bin/env python3
"""Which goods a save slot holds, in its common inventory and its key items, offline.

Cuts the slot with `er-save-planner-doc.py`'s `Slot` and walks the inventory section past the
common entries into the key item list that follows them (a u32 count, then `KEY_ENTRIES`
`{handle, quantity, sort id}` triples). A goods entry's handle is `0xB0000000 | row`.

    python3 scripts/save-slot-goods.py <save> <slot> [row ...]          # those rows only
    python3 scripts/save-slot-goods.py <save> <slot> --all              # every goods entry
    python3 scripts/save-slot-goods.py <save> <slot> --diff <save2> <slot2>

Only reads files.
"""

import importlib.util
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_save_planner_doc", HERE / "er-save-planner-doc.py")
doc = importlib.util.module_from_spec(spec)
sys.modules["er_save_planner_doc"] = doc
spec.loader.exec_module(doc)

GOODS_HANDLE = 0xB0000000
KEY_ENTRIES = 0x180


def entries(path, slot):
    s = doc.Slot(pathlib.Path(path), slot)
    base = s.sections["inventory"]
    common = s.inventory()
    key_base = base + 4 + doc.COMMON_ENTRIES * doc.INVENTORY_ENTRY + 4
    key = []
    for i in range(KEY_ENTRIES):
        handle, qty, sort_id = struct.unpack_from("<III", s.body, key_base + doc.INVENTORY_ENTRY * i)
        if handle not in doc.EMPTY:
            key.append((handle, qty, sort_id))
    return s, common, key


def table(path, slot):
    _, common, key = entries(path, slot)
    out = {}
    for where, rows in (("common", common), ("key", key)):
        for handle, qty, sort_id in rows:
            out[(where, handle)] = (qty, sort_id)
    return out


def describe(handle):
    kind = handle & doc.CATEGORY_MASK
    if kind == GOODS_HANDLE:
        return f"goods {handle & doc.ROW_MASK}"
    return f"handle {handle:#010x}"


def main():
    path, slot = sys.argv[1], int(sys.argv[2])
    rest = sys.argv[3:]
    if rest and rest[0] == "--diff":
        a, b = table(path, slot), table(rest[1], int(rest[2]))
        for k in sorted(set(a) | set(b)):
            if a.get(k, (None,))[0] != b.get(k, (None,))[0]:
                print(f"{k[0]:6} {describe(k[1]):24} {a.get(k, ('-',))[0]!s:>6} -> {b.get(k, ('-',))[0]!s:>6}")
        return
    want = None if (rest and rest[0] == "--all") else {int(r) for r in rest}
    _, common, key = entries(path, slot)
    print(f"{path} slot {slot}: common {len(common)} entries, key {len(key)} entries")
    for where, rows in (("common", common), ("key", key)):
        for handle, qty, sort_id in rows:
            if handle & doc.CATEGORY_MASK != GOODS_HANDLE:
                continue
            row = handle & doc.ROW_MASK
            if want is None or row in want:
                print(f"  {where:6} goods {row:>8} qty {qty} sort {sort_id}")


if __name__ == "__main__":
    main()
