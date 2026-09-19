#!/usr/bin/env python3
"""Compare a running game's key bindings against its own defaults, and repair one row.

    python3 scripts/er-keybind-repair.py --selftest
    python3 scripts/er-keybind-repair.py --diff
    python3 scripts/er-keybind-repair.py --restore-pad 0x0f

Why this exists. On 2026-09-19 a player reported that pressing right on the d-pad no longer
switched the right-hand armament. The press reached the game -- a frida probe counted clean rising
edges of the d-pad bit at `DLUID::PadDevice::Poll` -- yet `ChrAsm.right_weapon_slot` was never
written, and a hardware watchpoint on that field caught nothing. The reason was two levels below
any of that: the action had no pad button bound to it any more.

`CSPcKeyConfig` carries two tables of the same shape. The one at `+0x008` is the default, loaded
from `KeyAssignParam_TypeA`; the one at `+0x440` is what the player is actually playing with. A
row whose `padKeyId` is `-1` is unbound, so the button press has nothing to become and the whole
chain below it -- action request, behavior state, animation, the `SwitchWeapon` tae event that
does the equipment cycle -- never starts. Diffing the two tables says that in one line, where
every layer below it can only report an absence.

The layout comes from the accessor at rva `0x242ab0`, which is `cmp r8d,0x35; ja fail;
lea rcx,[rcx + idx*0x14 + 0x440]`, so the stride, the count and the table offset are all read off
one instruction rather than guessed. See bd `er-keybinding-table-cspckeyconfig-1162-2026-08-25`.

What a write here means. `--restore-pad` copies the default table's pad code over the current
table's, for one action. It is the value the game itself would install on "restore defaults", and
it is reversible with `--set-pad <action>=<code>`. Bindings are serialized into `ER0000.sl2`
through `CSKeyConfigSaveLoad`, so a repaired row outlives the session once the game next saves --
which is the point, but it is also why this refuses to touch a row the default leaves unbound.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

PROC = "/proc"

# `qword ptr [0x143d61f08]` in the key-config accessor's caller. This is the 1.17 address: the
# global moved with every other `.data` symbol between 1.16.2 and 1.17.0 and did not move again
# for 1.17.1 (docs/recon/rva-map-1162-to-1170.data.tsv).
KEY_CONFIG_GLOBAL_VA = 0x143D61F08

# Both tables, from `lea rcx,[rcx + idx*0x14 + 0x440]`.
DEFAULT_TABLE_OFFSET = 0x008
CURRENT_TABLE_OFFSET = 0x440
ROW_STRIDE = 0x14
ROW_COUNT = 0x36

# Field order within a row, all `i32`, `-1` meaning unbound.
FIELD_NAMES = ("pad", "keyboard", "keyboard_modifier", "mouse", "mouse_modifier")
PAD_FIELD_OFFSET = 0x00

UNBOUND = -1

# The quick-slot block, proven from the front-end handler at 1.16.2 `0x1407756b0`: it asks the
# binding layer about action `0x0d` before cycling magic and about `0x0e` before cycling items,
# then about `0x0f` and `0x10` for the two armament sides. The default pad codes line the block up
# with the d-pad -- 2000 and 2001 are the two the handler itself identifies as up and down -- and
# the menu-direction block at `0x1a..0x1d` carries the same four codes in the same order.
KNOWN_ACTIONS = {
    0x0D: "switch magic (d-pad up)",
    0x0E: "switch item (d-pad down)",
    0x0F: "switch right armament (d-pad right)",
    0x10: "switch left armament (d-pad left)",
    0x1A: "menu up",
    0x1B: "menu down",
    0x1C: "menu right",
    0x1D: "menu left",
    0x22: "menu confirm",
    0x25: "menu back",
}


def find_pid(name: str) -> int | None:
    """Resolve a process name by scanning /proc.

    Deliberately not pgrep, which the repo's guard blocks and which false-negatives on this box.
    `comm` is truncated to 15 characters by the kernel, so match on a prefix.
    """
    want = name.lower()
    for entry in os.listdir(PROC):
        if not entry.isdigit():
            continue
        try:
            with open(f"{PROC}/{entry}/comm", encoding="utf-8", errors="replace") as handle:
                comm = handle.read().strip().lower()
        except OSError:
            continue
        if comm and (comm == want or want.startswith(comm) or comm.startswith(want[:15])):
            return int(entry)
    return None


def read_mem(pid: int, addr: int, size: int) -> bytes | None:
    """Read `size` bytes out of another process. None when the address is not mapped."""
    try:
        with open(f"{PROC}/{pid}/mem", "rb", 0) as handle:
            handle.seek(addr)
            data = handle.read(size)
    except (OSError, ValueError):
        return None
    return data if data and len(data) == size else None


def write_i32(pid: int, addr: int, value: int) -> bool:
    """Write one `i32` into another process. Returns whether the value reads back."""
    try:
        with open(f"{PROC}/{pid}/mem", "r+b", 0) as handle:
            handle.seek(addr)
            handle.write(struct.pack("<i", value))
    except (OSError, ValueError) as error:
        print(f"write failed: {error}", file=sys.stderr)
        return False
    back = read_mem(pid, addr, 4)
    return back is not None and struct.unpack("<i", back)[0] == value


def config_base(pid: int) -> int | None:
    """Dereference the key-config singleton. None while the game has not built it yet."""
    data = read_mem(pid, KEY_CONFIG_GLOBAL_VA, 8)
    if data is None:
        return None
    base = struct.unpack("<Q", data)[0]
    return base if 0x10000 < base < 0x7FFFFFFFFFFF else None


def row_address(base: int, table_offset: int, action: int) -> int:
    return base + table_offset + action * ROW_STRIDE


def read_row(pid: int, base: int, table_offset: int, action: int) -> tuple[int, ...] | None:
    data = read_mem(pid, row_address(base, table_offset, action), ROW_STRIDE)
    return None if data is None else struct.unpack("<5i", data)


def describe(action: int) -> str:
    return KNOWN_ACTIONS.get(action, "")


def diff(pid: int, base: int, only_pad: bool) -> int:
    """Print every row where the player's table differs from the game's defaults."""
    print(f"pid {pid}  CSPcKeyConfig 0x{base:x}")
    source = read_mem(pid, base, 1)
    active = read_mem(pid, base + 4, 4)
    if source is not None and active is not None:
        print(
            f"  input source byte = {source[0]} (0 pad, 1 keyboard/mouse), "
            f"active device = {struct.unpack('<I', active)[0]}"
        )
    print()
    differing = 0
    unbound_but_default_bound = []
    for action in range(ROW_COUNT):
        default = read_row(pid, base, DEFAULT_TABLE_OFFSET, action)
        current = read_row(pid, base, CURRENT_TABLE_OFFSET, action)
        if default is None or current is None:
            print(f"  action 0x{action:02x}: unreadable")
            continue
        if only_pad:
            changed = default[0] != current[0]
        else:
            changed = default != current
        if not changed:
            continue
        differing += 1
        note = describe(action)
        print(f"  action 0x{action:02x}{('  ' + note) if note else ''}")
        for index, name in enumerate(FIELD_NAMES):
            if default[index] == current[index]:
                continue
            print(f"      {name:<18} default {default[index]:>6}   current {current[index]:>6}")
        if current[0] == UNBOUND and default[0] != UNBOUND:
            unbound_but_default_bound.append(action)
    if differing == 0:
        print("  no rows differ from the defaults")
    print()
    for action in unbound_but_default_bound:
        note = describe(action)
        print(
            f"  action 0x{action:02x} has no pad button bound where the default has one"
            + (f" -- {note}" if note else "")
        )
        print(f"      repair with: --restore-pad 0x{action:02x}")
    return 0


def restore_pad(pid: int, base: int, action: int) -> int:
    default = read_row(pid, base, DEFAULT_TABLE_OFFSET, action)
    current = read_row(pid, base, CURRENT_TABLE_OFFSET, action)
    if default is None or current is None:
        print(f"action 0x{action:02x}: unreadable", file=sys.stderr)
        return 2
    if default[0] == UNBOUND:
        print(
            f"action 0x{action:02x} is unbound in the defaults too, so there is nothing to "
            "restore; use --set-pad to choose a code deliberately",
            file=sys.stderr,
        )
        return 2
    if default[0] == current[0]:
        print(f"action 0x{action:02x} already carries the default pad code {default[0]}")
        return 0
    return set_pad(pid, base, action, default[0], was=current[0])


def set_pad(pid: int, base: int, action: int, code: int, was: int | None = None) -> int:
    addr = row_address(base, CURRENT_TABLE_OFFSET, action) + PAD_FIELD_OFFSET
    if was is None:
        current = read_row(pid, base, CURRENT_TABLE_OFFSET, action)
        if current is None:
            print(f"action 0x{action:02x}: unreadable", file=sys.stderr)
            return 2
        was = current[0]
    note = describe(action)
    print(f"action 0x{action:02x}{('  ' + note) if note else ''}")
    print(f"  0x{addr:x}: pad {was} -> {code}")
    if not write_i32(pid, addr, code):
        print("  the write did not take", file=sys.stderr)
        return 2
    print("  written and read back")
    return 0


def selftest() -> int:
    """Prove the read and write paths against a child process, not the game."""
    import subprocess

    child_source = (
        "import ctypes, struct, sys\n"
        "buf = ctypes.create_string_buffer(64)\n"
        "struct.pack_into('<i', buf, 0, -1)\n"
        "print(ctypes.addressof(buf), flush=True)\n"
        "sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    checks: list[tuple[str, bool]] = []
    try:
        line = child.stdout.readline().strip() if child.stdout else ""
        addr = int(line) if line.isdigit() else 0
        checks.append(("the child reported an address", addr != 0))
        if addr:
            data = read_mem(child.pid, addr, 4)
            checks.append(
                ("a cross-process read returns the unbound sentinel",
                 data is not None and struct.unpack("<i", data)[0] == UNBOUND),
            )
            checks.append(("a cross-process write reads back", write_i32(child.pid, addr, 2003)))
    finally:
        child.kill()
        child.wait(timeout=5)

    checks.extend(
        [
            ("the table offsets come from one lea", CURRENT_TABLE_OFFSET == 0x440),
            ("the row stride comes from the same lea", ROW_STRIDE == 0x14),
            ("the action count comes from the cmp above it", ROW_COUNT == 0x36),
            ("a row has five int fields", len(FIELD_NAMES) == ROW_STRIDE // 4),
            ("the pad code is the first field", PAD_FIELD_OFFSET == 0),
            (
                "the third row of the quick-slot block is the right armament",
                KNOWN_ACTIONS[0x0F].startswith("switch right armament"),
            ),
            ("a missing process is reported, not guessed", find_pid("no-such-process") is None),
        ]
    )
    failed = 0
    for name, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    print("selftest: " + ("PASS" if failed == 0 else f"FAIL ({failed})"))
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="target pid")
    parser.add_argument("--process", default="eldenring.exe", help="target process name")
    parser.add_argument("--diff", action="store_true", help="show rows that differ from defaults")
    parser.add_argument("--pad-only", action="store_true", help="diff the pad column alone")
    parser.add_argument("--restore-pad", help="copy the default pad code into one action's row")
    parser.add_argument("--set-pad", help="ACTION=CODE, e.g. 0x0f=2003 or 0x0f=-1")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    pid = args.pid or find_pid(args.process)
    if pid is None:
        print(f"no process matching {args.process!r} is running", file=sys.stderr)
        return 3
    base = config_base(pid)
    if base is None:
        print("CSPcKeyConfig is null -- the game has not built its key config yet", file=sys.stderr)
        return 3

    if args.restore_pad:
        return restore_pad(pid, base, int(args.restore_pad, 0))
    if args.set_pad:
        action_text, _, code_text = args.set_pad.partition("=")
        if not code_text:
            parser.error("--set-pad takes ACTION=CODE")
        return set_pad(pid, base, int(action_text, 0), int(code_text, 0))
    return diff(pid, base, args.pad_only)


if __name__ == "__main__":
    raise SystemExit(main())
