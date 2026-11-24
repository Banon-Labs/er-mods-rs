#!/usr/bin/env python3
"""Put talismans in a character's five accessory slots, offline, in a copy of an ER0000.sl2.

The fifth slot is ChrAsm slot 21 (`AccessoryCovenant`), which the equip menu never targets but
which `PlayerIns::InitializeSpEffects` reads as a fifth talisman (bd
hidden-5th-talisman-3rd-arrow-only-reachable-by-save-edit-2026-10-23). The save stores three
22-entry equip arrays one after another, and every one of them is written for each slot:

    EquipData   u32[22]            inventory index (0x180 + position in the held common items)
    ChrAsm      u32[7] + u32[22]   arm style and active slots, then the param ids
    ChrAsm2     u32[22]            gaitem handles (0xa0000000 | accessory id for talismans)

The slot layout up to the held inventory is ClayAmore's (ER-Save-Editor `SaveSlot::read`): the
gaitem map is 0x1400 variable-length records, then PlayerGameData (0x1b0), 0xd0 bytes, the three
arrays above, then EquipInventoryData with 0xa80 common and 0x180 key items of 12 bytes each. A
talisman needs no gaitem record; it is one inventory entry {handle, quantity 1, acquisition
sort id}.

The source file is read and never written; the output path must differ from it.

Usage:
  er-save-talismans.py <src.sl2> <dst.sl2> --slot N --talismans 7000,1002,1001,1000,1042
  er-save-talismans.py <src.sl2> --inspect
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("save_slot_oracle", HERE / "save-slot-oracle.py")
assert _spec and _spec.loader
oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle)

MD5_LEN = 0x10
GAITEM_COUNT = 0x1400
PGD_SIZE = 0x1B0
AFTER_PGD_GAP = 0xD0
EQUIP_SLOTS = 22
CHRASM_HEADER_U32 = 7
COMMON_ITEMS = 0xA80
KEY_ITEMS = 0x180
ITEM_SIZE = 12
HELD_EQUIP_INDEX_BASE = 0x180
ACCESSORY_SLOTS = (17, 18, 19, 20, 21)
ACCESSORY_HANDLE = 0xA0000000
EMPTY_HANDLES = (0, 0xFFFFFFFF)
PGD_UNLOCKED_TALISMAN_SLOTS = 0xBE
MAX_UNLOCKED_TALISMAN_SLOTS = 3


def gaitem_record_size(item_id: int) -> int:
    if item_id != 0 and item_id & 0xF0000000 == 0:
        return 8 + 13  # weapon: unk, unk, ash-of-war handle, one byte
    if item_id != 0 and item_id & 0xF0000000 == 0x10000000:
        return 8 + 8  # protector
    return 8


class Slot:
    """Offsets of the sections this tool reads or writes, inside one slot body (after the MD5)."""

    def __init__(self, body: bytearray):
        self.body = body
        pos = 0x20
        for _ in range(GAITEM_COUNT):
            item_id = struct.unpack_from("<I", body, pos + 4)[0]
            pos += gaitem_record_size(item_id)
        self.pgd = pos
        pos += PGD_SIZE + AFTER_PGD_GAP
        self.equip_data = pos
        pos += EQUIP_SLOTS * 4
        self.chr_asm = pos + CHRASM_HEADER_U32 * 4
        pos += (CHRASM_HEADER_U32 + EQUIP_SLOTS) * 4
        self.chr_asm2 = pos
        pos += EQUIP_SLOTS * 4
        self.common_count = pos
        self.common_items = pos + 4
        pos += 4 + COMMON_ITEMS * ITEM_SIZE
        self.key_count = pos
        pos += 4 + KEY_ITEMS * ITEM_SIZE
        self.next_equip_index = pos
        self.next_sort_id = pos + 4

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.body, off)[0]

    def put(self, off: int, value: int) -> None:
        struct.pack_into("<I", self.body, off, value & 0xFFFFFFFF)

    def name(self) -> str:
        return oracle.player_game_data_name(bytes(self.body), self.pgd)

    def item(self, pos: int) -> tuple[int, int, int]:
        return struct.unpack_from("<III", self.body, self.common_items + pos * ITEM_SIZE)

    def equip(self, slot: int) -> tuple[int, int, int]:
        return (
            self.u32(self.equip_data + slot * 4),
            self.u32(self.chr_asm + slot * 4),
            self.u32(self.chr_asm2 + slot * 4),
        )


def slot_entry(data: bytes, slot: int) -> dict:
    wanted = f"USER_DATA{slot:03d}"
    for entry in oracle.bnd4_entries(data):
        if entry["name"] == wanted:
            return entry
    raise SystemExit(f"{wanted} not found")


def check_equip_consistency(s: Slot) -> list[str]:
    """Every occupied slot's inventory index must point at the entry holding its gaitem handle."""
    problems = []
    for slot in range(EQUIP_SLOTS):
        idx, param, handle = s.equip(slot)
        if handle in EMPTY_HANDLES or idx in EMPTY_HANDLES:
            continue
        pos = idx - HELD_EQUIP_INDEX_BASE
        if not 0 <= pos < COMMON_ITEMS:
            problems.append(f"slot {slot}: index {idx:#x} outside the held common items")
            continue
        held = s.item(pos)[0]
        if held != handle:
            problems.append(f"slot {slot}: index {idx:#x} holds {held:#x}, ChrAsm2 says {handle:#x}")
    return problems


def describe(data: bytes, slot: int) -> None:
    entry = slot_entry(data, slot)
    body = bytearray(data[entry["offset"] + MD5_LEN : entry["offset"] + entry["size"]])
    s = Slot(body)
    print(f"slot {slot}: name={s.name()!r} pgd={s.pgd:#x} "
          f"pouch={body[s.pgd + PGD_UNLOCKED_TALISMAN_SLOTS]} "
          f"common={s.u32(s.common_count)} next_idx={s.u32(s.next_equip_index):#x} "
          f"next_sort={s.u32(s.next_sort_id)}")
    for slot_no in ACCESSORY_SLOTS:
        idx, param, handle = s.equip(slot_no)
        print(f"  chrasm[{slot_no}] idx={idx:#x} param={param:#x} handle={handle:#x}")
    for problem in check_equip_consistency(s):
        print(f"  INCONSISTENT {problem}")


def edit(data: bytearray, slot: int, talismans: list[int]) -> Slot:
    entry = slot_entry(data, slot)
    start = entry["offset"]
    body = bytearray(data[start + MD5_LEN : start + entry["size"]])
    s = Slot(body)
    if problems := check_equip_consistency(s):
        raise SystemExit("layout check failed before editing:\n" + "\n".join(problems))

    count = s.u32(s.common_count)
    sort_id = s.u32(s.next_sort_id)
    free = [p for p in range(COMMON_ITEMS) if s.item(p)[0] in EMPTY_HANDLES]
    for slot_no, accessory in zip(ACCESSORY_SLOTS, talismans):
        handle = ACCESSORY_HANDLE | accessory
        held = [p for p in range(COMMON_ITEMS) if s.item(p)[0] == handle]
        if held:
            pos = held[0]
        else:
            pos = free.pop(0)
            struct.pack_into("<III", body, s.common_items + pos * ITEM_SIZE, handle, 1, sort_id)
            sort_id += 1
            count += 1
        s.put(s.equip_data + slot_no * 4, HELD_EQUIP_INDEX_BASE + pos)
        s.put(s.chr_asm + slot_no * 4, accessory)
        s.put(s.chr_asm2 + slot_no * 4, handle)

    s.put(s.common_count, count)
    s.put(s.next_sort_id, sort_id)
    last = max(s.u32(s.equip_data + n * 4) for n in ACCESSORY_SLOTS)
    if s.u32(s.next_equip_index) <= last:
        s.put(s.next_equip_index, last + 1)
    pouch = s.pgd + PGD_UNLOCKED_TALISMAN_SLOTS
    body[pouch] = max(body[pouch], MAX_UNLOCKED_TALISMAN_SLOTS)

    if problems := check_equip_consistency(s):
        raise SystemExit("layout check failed after editing:\n" + "\n".join(problems))
    data[start + MD5_LEN : start + entry["size"]] = body
    data[start : start + MD5_LEN] = hashlib.md5(body).digest()
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path, nargs="?")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--talismans", help="five EquipParamAccessory ids, slots 1-4 then the hidden fifth")
    ap.add_argument("--inspect", action="store_true")
    args = ap.parse_args()

    data = bytearray(args.src.read_bytes())
    if args.inspect:
        for slot in range(oracle.SLOT_COUNT):
            describe(bytes(data), slot)
        return
    if not args.dst or not args.talismans:
        ap.error("dst and --talismans are required unless --inspect")
    if args.dst.resolve() == args.src.resolve():
        ap.error("dst must differ from src; the source save is read-only")
    talismans = [int(x, 0) for x in args.talismans.split(",")]
    if len(talismans) != len(ACCESSORY_SLOTS):
        ap.error("give exactly five talismans")
    edit(data, args.slot, talismans)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_bytes(data)
    describe(bytes(data), args.slot)


if __name__ == "__main__":
    main()
