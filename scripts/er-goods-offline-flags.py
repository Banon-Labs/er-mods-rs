#!/usr/bin/env python3
"""Read the three invasion fingers' `disable_offline` flags out of the running game, read-only.

This is the proof that a fix for the greyed-out fingers has not taken the player out of the
Seamless matchmaking pool.

`lobby_key` is `sha256(B + A + SALT32)`, and `B` is a fingerprint of the param data the game has
loaded -- not of `regulation.bin` on disk. Measured 2026-09-16: the three rows at their shipped
`0x63/0xe3/0x63` give `B = 76DFB8C5A838F5A3`; with `disable_offline` cleared to `0x43/0xc3/0x43`
they give `B = 76DFB8C5A838F603`, a different key, advertised and filtered on, matching nobody.
The file was byte-identical throughout, so an untouched file proves nothing. Only the loaded bytes
do, and this reads them.

`B` moved by exactly `+0x60` while the bytes fell by `0x60`, so the summed delta printed here is
the quantity that matters: `+0` means these rows have not moved the key.

It opens `/proc/<pid>/mem` and seeks -- nothing is injected, no thread suspended, no code runs in
the game. Safe on a session the user is playing. See `scripts/er-live-fields.py` for why this is
never `frida.attach()`.

    python3 scripts/er-goods-offline-flags.py
    python3 scripts/er-goods-offline-flags.py --selftest
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys

# `SoloParamRepository`, whose pointer the image holds at this rva. Read live on 1.17.1.
PARAM_REPOSITORY_RVA = 0x3D85F58
# `EquipParamGoods` is the fourth table in the repository's array of 9-qword descriptors.
GOODS_TABLE_INDEX = 3
DESCRIPTOR_QWORDS = 9
REPOSITORY_TABLES_OFFSET = 0x88
# `_EQUIP_PARAM_GOODS_ST + 0x48` packs eight flags; bit 5 is `disable_offline`.
GOODS_FLAGS_OFFSET = 0x48
DISABLE_OFFLINE_BIT = 1 << 5
# Row header: count at `+0x0a`, then 24-byte index entries from `+0x40`, each `{id, _, offset}`.
ROW_COUNT_OFFSET = 0x0A
ROW_INDEX_OFFSET = 0x40
ROW_INDEX_STRIDE = 24
# Bloody Finger, Festering Bloody Finger, Recusant Finger, and the byte each ships with.
SHIPPED_FLAGS = {102: 0x63, 111: 0xE3, 112: 0x63}


def find_pid(explicit: int | None) -> int:
    """The running `eldenring.exe`, without `pgrep` -- AGENTS.md bans name-grepping for this."""
    if explicit is not None:
        return explicit
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"eldenring.exe" not in cmdline or b"start_protected_game" in cmdline:
            continue
        # A launcher names the game on its command line without ever loading it. Only the process
        # that has the module mapped can be read, and picking the wrong one reports "not mapped",
        # which reads as "the game is not running".
        try:
            maps = (entry / "maps").read_text(errors="replace")
        except OSError:
            continue
        if any(line.rstrip().endswith("eldenring.exe") for line in maps.splitlines()):
            return int(entry.name)
    raise SystemExit("no process has eldenring.exe mapped")


class Reader:
    """`/proc/<pid>/mem`, seeked. Opened read-only and never written to."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.mem = open(f"/proc/{pid}/mem", "rb", 0)  # noqa: SIM115 -- lifetime is the whole run
        self.base = self._module_base()

    def _module_base(self) -> int:
        base = None
        with open(f"/proc/{self.pid}/maps", errors="replace") as maps:
            for line in maps:
                if line.rstrip().endswith("eldenring.exe"):
                    start = int(line.split("-", 1)[0], 16)
                    base = start if base is None else min(base, start)
        if base is None:
            raise SystemExit(f"eldenring.exe is not mapped in pid {self.pid}")
        return base

    def read(self, addr: int, count: int) -> bytes:
        self.mem.seek(addr)
        data = self.mem.read(count)
        if data is None or len(data) != count:
            raise SystemExit(f"short read of {count} bytes at 0x{addr:x}")
        return data

    def qword(self, addr: int) -> int:
        return struct.unpack("<Q", self.read(addr, 8))[0]


def goods_rows(reader: Reader) -> dict[int, tuple[int, int]]:
    """`{row id: (row address, flag byte)}` for the three fingers, walked the way the engine does."""
    repository = reader.qword(reader.base + PARAM_REPOSITORY_RVA)
    descriptor = REPOSITORY_TABLES_OFFSET + GOODS_TABLE_INDEX * DESCRIPTOR_QWORDS * 8
    table = reader.qword(repository + descriptor)
    blob = reader.qword(reader.qword(table + 0x80) + 0x80)
    count = struct.unpack("<H", reader.read(blob + ROW_COUNT_OFFSET, 2))[0]

    found: dict[int, tuple[int, int]] = {}
    for index in range(count):
        entry = blob + ROW_INDEX_OFFSET + index * ROW_INDEX_STRIDE
        row_id = struct.unpack("<I", reader.read(entry, 4))[0]
        if row_id in SHIPPED_FLAGS:
            row = blob + reader.qword(entry + 8)
            found[row_id] = (row, reader.read(row + GOODS_FLAGS_OFFSET, 1)[0])
    return found


def selftest() -> int:
    """Everything that needs no game: the arithmetic the verdict rests on."""
    shipped = sum(SHIPPED_FLAGS.values())
    cleared = sum(flag & ~DISABLE_OFFLINE_BIT for flag in SHIPPED_FLAGS.values())
    assert shipped - cleared == 0x60, hex(shipped - cleared)
    # The two captured preimages differ by exactly that, sign reversed.
    assert 0x76DFB8C5A838F603 - 0x76DFB8C5A838F5A3 == 0x60
    assert all(flag & DISABLE_OFFLINE_BIT for flag in SHIPPED_FLAGS.values())
    print("selftest: ok -- clearing all three costs 0x60, the measured preimage delta")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="print the verdict line only")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    rows = goods_rows(Reader(find_pid(args.pid)))
    missing = sorted(set(SHIPPED_FLAGS) - set(rows))
    if missing:
        print(f"param tables are not up yet: rows {missing} are not present")
        return 2

    delta = 0
    for row_id in sorted(rows):
        row, flag = rows[row_id]
        shipped = SHIPPED_FLAGS[row_id]
        delta += flag - shipped
        if not args.quiet:
            gate = "clear, item usable" if not flag & DISABLE_OFFLINE_BIT else "set, item greyed"
            state = "shipped" if flag == shipped else f"changed from {shipped:#04x}"
            print(f"  goods {row_id:3d}  0x{row:x}+0x48 = {flag:#04x}  "
                  f"disable_offline {gate}  ({state})")

    if delta == 0:
        print(f"summed byte delta {delta:+d} -- these rows have not moved lobby_key")
        return 0
    print(f"summed byte delta {delta:+d} -- lobby_key has moved; this player matches nobody")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
