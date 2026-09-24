#!/usr/bin/env python3
"""Read the live quickbar: what the ten slots hold, and which one the pouch widget draws.

# The question this answers

An imported build fills every quick slot and the player still sees an empty first slot until they
take that item off and put it back. `er-build-import.log` already says the write landed --
`QUICK/POUCH/RUNE slot 22 (index 0) item 0x40000384 -> the position reads back 0x40000384 (OK)` --
so the array is not what is wrong, and the only other state in that path is
`EquipItemData::selectedQuickSlot`.

Static reading of the named 1.16.2 dump says the array and the selection are reconciled by
`FUN_140249a90`, not by the write. `EquipItemToChrAsmSlot` calls it after the same dispatcher the
importer calls, `UnequipItem` and `RemoveItem` call it too, and `GetSelectedQuickslotItemIndex`
returns `-1` for as long as the field holds `-1`. That predicts the selection stays `-1` across an
import that fills ten slots, and this is what turns the prediction into a measurement.

# Why this reads `/proc/<pid>/mem` and not Frida

The question is what a value holds, not which instruction wrote it, so nothing needs to run inside the
game. `scripts/er-live-fields.py` exists for exactly this and says so in its own header: a read
must never be able to destroy the session it is reading. This borrows its `find_pid` and
`read_window` rather than reimplementing them, and adds the one thing they do not do -- walking a
pointer chain instead of reading one flat address.

Measured 2026-09-23: a Frida server was brought up against a live session for this same question
and the game was gone 1.5 seconds later. That is not proof the server killed it -- the harness had
just driven the pause menu open, and the crash site was `eldenring.exe+0x251ef3c` -- but a read
that cannot be a suspect is worth more than one that has to be cleared.

# The chain, and where each number comes from

    GameDataMan            eldenring.exe + 0x3d61f98   (1.17; see below)
      -> PlayerGameData    + 0x08
      -> EquipGameData     + 0x2b0    inline, not a pointer
         equipmentEntries  + 0x348    ChrAsmEquipEntries, ints by ChrAsmSlot
      -> EquipItemData     + 0x288    inline
         quickSlotEntries  + 0x08     EquipDataEntry[10], 8 bytes each
         selectedQuickSlot + 0xa0

`GameDataMan` is the one address that needs translating. `er-game-base` stores rvas against 1.16.2
on purpose and `er_game_base::mem::game_data_addr` carries them to the running build; a script has
no such layer, so the 1.17 value is written here:

    docs/recon/rva-map-1162-to-1170.data.tsv:88   0x3d5df38 -> 0x3d61f98   642/642 witnesses

1.17.0 to 1.17.1 moved nothing outside `.text`, so the 1.17.0 data address is the running one. The
struct offsets are field offsets from the named 1.16.2 dump, which the `.text` shift does not
reach. `0x348` is the same constant `EQUIP_GAME_DATA_EQUIPMENT_ENTRIES` in `equip_native.rs`, and
that one is already runtime-proven by its neighbour `physicTears` at `840 + 156`.

# One reading per invocation

There is no event to block on -- a field in another process's memory changes without announcing
it -- and `scripts/check-no-timeouts.py` bans the poll loop that would paper over that. So this
takes one reading and exits, and repeated sampling belongs to whoever calls it, against a condition
they can actually name: the importer's log growing, a phase file appearing, the run ending. The
before/after pair this was written for came from calling it either side of an import.

Usage:
    python3 /home/banon/projects/er-mods-rs/scripts/er-quickbar-state.py
    python3 /home/banon/projects/er-mods-rs/scripts/er-quickbar-state.py --json
    python3 /home/banon/projects/er-mods-rs/scripts/er-quickbar-state.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
import time
from pathlib import Path

# `er-live-fields.py` carries a hyphen, so it cannot be imported by name. Loading it by path reuses
# its `find_pid` and `read_window` -- the two pieces that make this read unable to disturb the
# target -- rather than reimplementing them or renaming a file other tooling calls by its spelling.
_spec = importlib.util.spec_from_file_location(
    "er_live_fields", Path(__file__).resolve().parent / "er-live-fields.py"
)
if _spec is None or _spec.loader is None:  # pragma: no cover -- a missing sibling is a broken tree
    raise SystemExit("cannot load scripts/er-live-fields.py beside this script")
_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live)

PROCESS = "eldenring.exe"

GAME_DATA_MAN_RVA = 0x3D61F98
PLAYER_GAME_DATA_OFFSET = 0x08
EQUIP_GAME_DATA_OFFSET = 0x2B0
EQUIP_ITEM_DATA_OFFSET = 0x288
EQUIPMENT_ENTRIES_OFFSET = 0x348
QUICK_SLOT_ENTRIES_OFFSET = 0x08
SELECTED_QUICK_SLOT_OFFSET = 0xA0

# `ChrAsmSlot` 0x16..0x1f are quickItem1..10; the pouch follows at 0x20..0x25.
CHR_ASM_SLOT_QUICK_BASE = 0x16
QUICKBAR_SLOTS = 10
POUCH_SLOTS = 6
EQUIP_DATA_ENTRY_SIZE = 8

EMPTY = -1


def module_base(pid: int, name: str) -> int | None:
    """Base address of a mapped module, from the process's own map list."""
    want = name.lower()
    try:
        with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.rstrip().lower().endswith(want):
                    continue
                return int(line.split("-", 1)[0], 16)
    except OSError:
        return None
    return None


def read_u64(pid: int, addr: int) -> int | None:
    data = _live.read_window(pid, addr, 8)
    return None if data is None else struct.unpack("<Q", data)[0]


def read_i32(pid: int, addr: int) -> int | None:
    data = _live.read_window(pid, addr, 4)
    return None if data is None else struct.unpack("<i", data)[0]


def read_state(pid: int) -> dict:
    """Walk the chain and read both halves of the quickbar. Every step reports rather than guesses."""
    base = module_base(pid, PROCESS)
    if base is None:
        return {"ok": False, "why": f"{PROCESS} is not mapped in pid {pid}"}

    gdm = read_u64(pid, base + GAME_DATA_MAN_RVA)
    if gdm is None:
        return {"ok": False, "why": "GameDataMan slot is not readable"}
    if gdm == 0:
        return {"ok": False, "why": "GameDataMan is null -- no character loaded"}

    pgd = read_u64(pid, gdm + PLAYER_GAME_DATA_OFFSET)
    if pgd is None:
        return {"ok": False, "why": "PlayerGameData slot is not readable"}
    if pgd == 0:
        return {"ok": False, "why": "PlayerGameData is null -- no character loaded"}

    egd = pgd + EQUIP_GAME_DATA_OFFSET
    eid = egd + EQUIP_ITEM_DATA_OFFSET

    selected = read_i32(pid, eid + SELECTED_QUICK_SLOT_OFFSET)
    if selected is None:
        return {"ok": False, "why": "selectedQuickSlot is not readable"}

    entries = egd + EQUIPMENT_ENTRIES_OFFSET
    quick = []
    for i in range(QUICKBAR_SLOTS):
        value = read_i32(pid, entries + (CHR_ASM_SLOT_QUICK_BASE + i) * 4)
        if value is None:
            return {"ok": False, "why": f"quick slot {i} is not readable"}
        quick.append(value)
    pouch = []
    for i in range(POUCH_SLOTS):
        value = read_i32(pid, entries + (CHR_ASM_SLOT_QUICK_BASE + QUICKBAR_SLOTS + i) * 4)
        if value is None:
            return {"ok": False, "why": f"pouch slot {i} is not readable"}
        pouch.append(value)

    cached = []
    for i in range(QUICKBAR_SLOTS):
        value = read_i32(pid, eid + QUICK_SLOT_ENTRIES_OFFSET + i * EQUIP_DATA_ENTRY_SIZE)
        if value is None:
            return {"ok": False, "why": f"quickSlotEntries[{i}].index is not readable"}
        cached.append(value)

    return {
        "ok": True,
        "pid": pid,
        "selectedQuickSlot": selected,
        "quick": quick,
        "pouch": pouch,
        "cachedInventoryIndex": cached,
    }


def item(value: int) -> str:
    return "-1" if value == EMPTY else f"0x{value & 0xFFFFFFFF:08X}"


def verdict(state: dict) -> str:
    """The one sentence the measurement exists to produce."""
    filled = sum(1 for v in state["quick"] if v != EMPTY)
    selected = state["selectedQuickSlot"]
    if filled == 0:
        return "the quickbar is empty, so the selection says nothing yet"
    if selected == EMPTY:
        return (
            f"{filled} quick slot(s) hold an item and selectedQuickSlot is -1, so "
            "GetSelectedQuickslotItemIndex answers -1 and the pouch widget draws NOTHING"
        )
    if not 0 <= selected < QUICKBAR_SLOTS:
        return f"selectedQuickSlot is {selected}, which is outside 0..9 -- the field is junk"
    if state["quick"][selected] == EMPTY:
        return (
            f"selectedQuickSlot is {selected} and that slot is empty, so the widget draws nothing "
            "while other slots hold items"
        )
    return (
        f"{filled} quick slot(s) hold an item and selectedQuickSlot is {selected}, holding "
        f"{item(state['quick'][selected])} -- the widget has something to draw"
    )


def show(state: dict) -> None:
    if not state["ok"]:
        print(f"unreadable: {state['why']}")
        return
    print(f"pid {state['pid']}  selectedQuickSlot = {state['selectedQuickSlot']}")
    print("  quick  " + " ".join(item(v) for v in state["quick"]))
    print("  pouch  " + " ".join(item(v) for v in state["pouch"]))
    print("  cached " + " ".join(str(v) for v in state["cachedInventoryIndex"]))
    print("  -> " + verdict(state))


def selftest() -> int:
    """Check the reasoning, not the game: the verdict must separate the states it exists to tell apart."""
    cases = 0

    filled_no_selection = {
        "ok": True,
        "selectedQuickSlot": -1,
        "quick": [0x40000384] + [-1] * 9,
        "pouch": [-1] * POUCH_SLOTS,
        "cachedInventoryIndex": [460] + [-1] * 9,
    }
    assert "draws NOTHING" in verdict(filled_no_selection), verdict(filled_no_selection)
    cases += 1

    reconciled = dict(filled_no_selection, selectedQuickSlot=0)
    assert "has something to draw" in verdict(reconciled), verdict(reconciled)
    cases += 1

    # The mirror case: a selection that survives pointing at a slot nothing refilled.
    stale = {
        "ok": True,
        "selectedQuickSlot": 3,
        "quick": [0x40000384, -1, -1, -1, -1, -1, -1, -1, -1, -1],
        "pouch": [-1] * POUCH_SLOTS,
        "cachedInventoryIndex": [460] + [-1] * 9,
    }
    assert "that slot is empty" in verdict(stale), verdict(stale)
    cases += 1

    empty = {
        "ok": True,
        "selectedQuickSlot": -1,
        "quick": [-1] * QUICKBAR_SLOTS,
        "pouch": [-1] * POUCH_SLOTS,
        "cachedInventoryIndex": [-1] * QUICKBAR_SLOTS,
    }
    assert "says nothing yet" in verdict(empty), verdict(empty)
    cases += 1

    junk = dict(filled_no_selection, selectedQuickSlot=99)
    assert "outside 0..9" in verdict(junk), verdict(junk)
    cases += 1

    assert item(-1) == "-1"
    assert item(0x40000384) == "0x40000384"
    cases += 1

    print(f"selftest: {cases} cases passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pid", type=int, help="read this pid instead of resolving by name")
    parser.add_argument("--json", action="store_true", help="the reading as one JSON object")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    pid = args.pid or _live.find_pid(PROCESS)
    if pid is None:
        print(f"FAIL: no {PROCESS} process", file=sys.stderr)
        return 2

    state = read_state(pid)
    if args.json:
        print(json.dumps(dict(state, at=time.strftime("%H:%M:%S"))))
    else:
        show(state)
    return 0 if state["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
