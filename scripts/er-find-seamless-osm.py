#!/usr/bin/env python3
"""Find Seamless's option-menu object, and through it the session, by a content signature.

Every numeric attempt to recognise the session has lost. The last one latched an axis-aligned
bounding box whose `CRITICAL_SECTION` read `OwningThread=0xff7fffeeff7fffee`, because a float
buffer can hold any number a shape test asks for. A string cannot: `er-invasion-warp` records
`OSM_TAG = b"seamless"` at `OSM_TAG_OFFSET = 0x68` of the option-menu object, so the eight bytes
themselves are the evidence.

From a tag hit at `H` the chain is fixed by `crates/er-invasion-warp/src/local_invasion_filter`:

    osm      = H - 0x68
    session  = *(osm + 0x58)            NEXT_OBJECT_OFFSET
    state    = *(session + 0x150)       V201_SESSION_STATE_OFFSET, 0x01 idle / 0x0e searching
    mutex    =   session + 0x100        the `_Mtx_internal_imp_t` `ersc+0x25850` locks

The session is then put to the same `CRITICAL_SECTION` test that `lock_report.rs` uses, because
that is the test the bounding box failed: `_Mtx_internal_imp_t` carries `int _Type` at +0, the
`CRITICAL_SECTION` at +8, `long _Thread_id` at +0x48 and `int _Count` at +0x4c.

Read-only, through `/proc/<pid>/mem`. Nothing is attached, injected or written, so this cannot
disturb a live session the way a Frida reload can.
"""

from __future__ import annotations

import argparse
import glob
import re
import struct
import sys

TAG = b"seamless"
OSM_TAG_OFFSET = 0x68
NEXT_OBJECT_OFFSET = 0x58
SESSION_STATE_OFFSET = 0x150
SESSION_MUTEX_OFFSET = 0x100
STATE_NAMES = {0x01: "idle", 0x0E: "searching"}

# `_Mtx_internal_imp_t`, then `RTL_CRITICAL_SECTION` inside it.
MTX_CRITICAL_SECTION_OFFSET = 0x08
CS_LOCK_COUNT_OFFSET = MTX_CRITICAL_SECTION_OFFSET + 0x08
CS_RECURSION_COUNT_OFFSET = MTX_CRITICAL_SECTION_OFFSET + 0x0C
CS_OWNING_THREAD_OFFSET = MTX_CRITICAL_SECTION_OFFSET + 0x10
CS_OWNING_THREAD_MAX = 0x10_0000


def find_game_pid() -> int | None:
    """The Windows-side `eldenring.exe`, identified by its own mapped image rather than by name.

    Matching on the command line picks the me3 launcher, whose argv names `eldenring.exe` too --
    measured 2026-09-16, when this returned pid 2193146 and the scan read 30 MB of Linux-side heap
    and reported the tag missing. A detector that reads the wrong process cannot be told apart
    from one that reads the right process and finds nothing, so the identity test is the PE image
    base at 0x140000000 plus the largest mapping, which only the game has (8.16 GB against the
    launcher's 0.23 GB).
    """
    best = None
    for entry in glob.glob("/proc/[0-9]*"):
        try:
            with open(entry + "/maps", "r", encoding="utf-8", errors="replace") as handle:
                maps = handle.read()
        except OSError:
            continue
        if "140000000-" not in maps:
            continue
        try:
            with open(entry + "/cmdline", "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "eldenring.exe" not in cmdline.lower() or "start_protected_game" in cmdline:
            continue
        if "me3-launcher" in cmdline:
            continue
        mapped = 0
        for line in maps.splitlines():
            fields = line.split()
            if not fields:
                continue
            lo, hi = (int(part, 16) for part in fields[0].split("-"))
            mapped += hi - lo
        pid = int(entry.rsplit("/", 1)[1])
        if best is None or mapped > best[1]:
            best = (pid, mapped)
    if best is None:
        return None
    print(f"game pid {best[0]} ({best[1] / 1e9:.2f} GB mapped)")
    return best[0]


def writable_ranges(pid: int) -> list[tuple[int, int]]:
    ranges = []
    with open(f"/proc/{pid}/maps", "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 2 or "w" not in fields[1]:
                continue
            lo, hi = (int(part, 16) for part in fields[0].split("-"))
            if hi - lo > (1 << 32):
                continue
            ranges.append((lo, hi))
    return ranges


def critical_section_refusal(mem, mutex: int) -> str | None:
    """The same verdict `lock_report.rs` reaches, so both sides agree on what a real lock is."""
    try:
        lock_count = struct.unpack("<i", read_at(mem, mutex + CS_LOCK_COUNT_OFFSET, 4))[0]
        recursion = struct.unpack("<i", read_at(mem, mutex + CS_RECURSION_COUNT_OFFSET, 4))[0]
        owner = struct.unpack("<Q", read_at(mem, mutex + CS_OWNING_THREAD_OFFSET, 8))[0]
    except OSError as exc:
        return f"unreadable ({exc})"
    if lock_count < -1:
        return f"LockCount {lock_count} is below -1"
    if recursion < 0:
        return f"RecursionCount {recursion} is negative"
    if owner > CS_OWNING_THREAD_MAX:
        return f"OwningThread 0x{owner:x} is far too large"
    if (recursion == 0) != (owner == 0):
        return f"RecursionCount {recursion} and OwningThread 0x{owner:x} disagree"
    return None


# User-space on x86-64 stops at the 47-bit boundary, so anything above it is not a pointer and
# seeking to it raises OverflowError rather than returning nothing.
MAX_USER_ADDRESS = 0x7FFF_FFFF_FFFF


def read_at(mem, address: int, size: int) -> bytes:
    if not 0x1_0000 <= address <= MAX_USER_ADDRESS:
        raise OSError(f"0x{address:x} is not a user-space address")
    mem.seek(address)
    data = mem.read(size)
    if data is None or len(data) != size:
        raise OSError(f"short read at 0x{address:x}")
    return data


def scan(pid: int, verbose: bool) -> int:
    hits = []
    scanned = 0
    unreadable = 0
    with open(f"/proc/{pid}/mem", "rb", 0) as mem:
        for lo, hi in writable_ranges(pid):
            try:
                mem.seek(lo)
                blob = mem.read(hi - lo)
            except (OSError, ValueError, OverflowError):
                unreadable += 1
                continue
            if not blob:
                unreadable += 1
                continue
            scanned += len(blob)
            for match in re.finditer(re.escape(TAG), blob):
                hits.append(lo + match.start())

        print(
            f"scanned {scanned / 1e6:.1f} MB of writable memory "
            f"({unreadable} range(s) unreadable); tag {TAG!r} found at {len(hits)} address(es)"
        )
        accepted = []
        for hit in hits:
            osm = hit - OSM_TAG_OFFSET
            try:
                session = struct.unpack("<Q", read_at(mem, osm + NEXT_OBJECT_OFFSET, 8))[0]
            except OSError:
                if verbose:
                    print(f"  osm 0x{osm:x}: +0x58 unreadable")
                continue
            if not 0x1_0000 <= session <= MAX_USER_ADDRESS:
                if verbose:
                    print(f"  osm 0x{osm:x}: +0x58 = 0x{session:x}, not a pointer")
                continue
            try:
                state = struct.unpack("<I", read_at(mem, session + SESSION_STATE_OFFSET, 4))[0]
            except OSError:
                if verbose:
                    print(f"  osm 0x{osm:x} -> session 0x{session:x}: state unreadable")
                continue
            refusal = critical_section_refusal(mem, session + SESSION_MUTEX_OFFSET)
            verdict = "REFUSED: " + refusal if refusal else "lock is real"
            label = STATE_NAMES.get(state, f"0x{state:x}")
            print(f"  osm 0x{osm:x} -> session 0x{session:x}  state {label}  {verdict}")
            if refusal is None:
                accepted.append((osm, session, state))

    if not accepted:
        print("no option-menu object with a real lock -- Seamless has not built its menu yet")
        return 1
    print()
    for osm, session, state in accepted:
        print(f"ACCEPTED  osm=0x{osm:x}  session=0x{session:x}  state=0x{state:x}")
    return 0


def selftest() -> int:
    """The verdict logic, against the two records that actually mattered."""

    class FakeMem:
        def __init__(self, values):
            self.values = values

        def seek(self, address):
            self.address = address

        def read(self, size):
            return self.values.get((self.address, size))

    # The uninitialised bounding box that parked the game: both halves out of range.
    box = FakeMem(
        {
            (0x20_0000 + CS_LOCK_COUNT_OFFSET, 4): struct.pack("<i", -1),
            (0x20_0000 + CS_RECURSION_COUNT_OFFSET, 4): struct.pack("<i", -1),
            (0x20_0000 + CS_OWNING_THREAD_OFFSET, 8): struct.pack("<Q", 0xFF7FFFEEFF7FFFEE),
        }
    )
    assert critical_section_refusal(box, 0x20_0000) is not None, "the bounding box must be refused"

    # A free lock as `InitializeCriticalSection` leaves it.
    free = FakeMem(
        {
            (0x20_0000 + CS_LOCK_COUNT_OFFSET, 4): struct.pack("<i", -1),
            (0x20_0000 + CS_RECURSION_COUNT_OFFSET, 4): struct.pack("<i", 0),
            (0x20_0000 + CS_OWNING_THREAD_OFFSET, 8): struct.pack("<Q", 0),
        }
    )
    assert critical_section_refusal(free, 0x20_0000) is None, "a free lock must be accepted"

    # A held lock: a real thread id and a matching recursion count.
    held = FakeMem(
        {
            (0x20_0000 + CS_LOCK_COUNT_OFFSET, 4): struct.pack("<i", 0),
            (0x20_0000 + CS_RECURSION_COUNT_OFFSET, 4): struct.pack("<i", 1),
            (0x20_0000 + CS_OWNING_THREAD_OFFSET, 8): struct.pack("<Q", 0x1F40),
        }
    )
    assert critical_section_refusal(held, 0x20_0000) is None, "a held lock must be accepted"

    print("selftest ok -- the bounding box is refused, free and held locks are accepted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--verbose", action="store_true", help="say why each tag hit was rejected")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    pid = args.pid or find_game_pid()
    if pid is None:
        print("no eldenring.exe found", file=sys.stderr)
        return 2
    print(f"scanning eldenring.exe pid {pid}")
    return scan(pid, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
