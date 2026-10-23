#!/usr/bin/env python3
"""Say whether an address is a function entry, a leaf with no unwind data, or mid-function.

Why this exists
---------------
A detour licence is a row of `crates/er-game-base/src/detour_allowlist.rs`, and MinHook writes five
bytes at the address it names. Two very different populations have no `.pdata` record of their
own:

  * a leaf function. The x64 ABI lets a function omit unwind data when it allocates no stack and
    calls nothing, so ELDEN RING's many small getters and `jmp` thunks have no `.pdata` row at
    all. Hooking one is fine.
  * an address in the middle of a function. A captured return address, a byte-patch site, a
    constant derived by subtracting a shift that did not apply. Hooking one means MinHook
    overwrites five bytes mid-body.

The distinguishing question is not "is there a `.pdata` row at this address" but "is this address
inside some other function's declared extent". That is what this answers, against the 2.7.2.0
image (`eldenring-deobf-1.17.2.bin`, located by `scripts/er_game_image.py`).

A `.pdata` row is not always a function either. MSVC splits a function into chunks and gives each
chunk its own row; every row after the first carries `UNW_FLAG_CHAININFO` and begins in the middle
of the function. Such a begin is classified `CHAINED`, never `ENTRY`, and `--fail-on-mid` fails on it
the same way: MinHook would patch a chunk whose prologue lives somewhere else. On 2.7.2.0
`0x1401a8ac6` is one, a continuation of the function at `0x1401a8a00`.

The precedent that made this a gate (2026-08-30): a row that compared `identical 1.000` over 56
instructions was merged into the detour table while being +0x360 inside another function, with
the real entry already written down in the tree. A mid-function address produces a better-looking
comparison than a real entry does: it sits in the middle of a stable neighbourhood, so a
normalised comparison runs long and agrees everywhere. On 2.7.2.0 that address is `0x140aedc50`,
inside `0x140aed8f0..0x140aedd37`, and the entry it was meant to name is `0x140aedd40`.

Usage
    python3 scripts/classify-entry-kind.py 0x140aedc50 0x140aedd40
    python3 scripts/classify-entry-kind.py --allowlist crates/er-game-base/src/detour_allowlist.rs
    python3 scripts/classify-entry-kind.py --fail-on-mid      # gate mode, the detour allowlist
    python3 scripts/classify-entry-kind.py --selftest

To see the neighbourhood of an address this flags, `scripts/pdata-enclosing-function.py
1172:<va>` prints the surrounding `.pdata` records.
"""

import argparse
import bisect
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work
import function_extent  # noqa: E402 - same; it has no import side effects and needs no capstone here

ROOT = str(er_game_image.REPO)
BASE = er_game_image.BASE

ENTRY = "ENTRY"
LEAF = "LEAF"
MID = "MID-FUNCTION"
CHAINED = "CHAINED"
# The kinds that must not carry a detour; `--fail-on-mid` fails on each.
REFUSED = (MID, CHAINED)

# The licence table: every rva a constant detour may target. A mid-function row in it is a licence
# to write five bytes into the middle of a live function.
ALLOWLIST = os.path.join(ROOT, "crates", "er-game-base", "src", "detour_allowlist.rs")
_ROW = re.compile(r"^\s*(0x[0-9a-fA-F_]+)\s*,", re.MULTILINE)


def spans_from_image(image):
    """Sorted `(begin, end)` rva pairs for every function the image's `.pdata` declares.

    A record that does not fit inside the image is dropped, as `er_game_image.FlatImage` does:
    the de-Arxan'd image carries a few whose begin is past the end of the file.
    """
    e_lfanew = struct.unpack_from("<I", image, 0x3C)[0]
    magic = struct.unpack_from("<H", image, e_lfanew + 24)[0]
    directories = e_lfanew + 24 + (112 if magic == 0x20B else 96)
    table_rva, table_size = struct.unpack_from("<II", image, directories + 3 * 8)
    out = []
    for offset in range(table_rva, table_rva + table_size, 12):
        begin, end, _unwind = struct.unpack_from("<III", image, offset)
        if 0 < begin < end <= len(image):
            out.append((begin, end))
    out.sort()
    return out


def next_entry(spans, rva):
    """The first declared function start strictly after `rva`, or `None`."""
    index = bisect.bisect_right(spans, (rva, 1 << 62))
    return spans[index][0] if index < len(spans) else None


def chain_root(spans, chained, rva):
    """The begin of the nearest non-chained row at or before `rva`: the function a chunk is part of."""
    index = bisect.bisect_right(spans, (rva, 1 << 62)) - 1
    while index >= 0 and spans[index][0] in chained:
        index -= 1
    return spans[index][0] if index >= 0 else None


def chained_from_image(image):
    """Every `.pdata` begin whose unwind info carries `UNW_FLAG_CHAININFO`."""
    return frozenset(function_extent.chained_begins(image))


def classify(spans, starts, rva, chained=frozenset()):
    """`(kind, detail)` for one rva against one image's function table.

    For a mid-function address the detail carries the two addresses a reader needs to act: the
    entry of the function it landed inside, and the next entry after it. Which of the two the row
    meant is a judgement this cannot make, so both are printed and neither is presented as the
    answer.

    `chained` holds the `.pdata` begins flagged `UNW_FLAG_CHAININFO`. Their rows are in `spans` and
    `starts` like any other, which is why they are checked first: a chunk's begin is the middle of
    a function whose entry is elsewhere.
    """
    if rva in chained:
        root = chain_root(spans, chained, rva)
        owner = f"the function at 0x{BASE + root:x}" if root is not None else "an earlier row"
        return CHAINED, (
            f"a chained-unwind continuation row (UNW_FLAG_CHAININFO) of {owner}; the prologue "
            "MinHook would need is there, not here"
        )
    if rva in starts:
        return ENTRY, "the image's own .pdata declares a function start here"
    index = bisect.bisect_right(spans, (rva, 1 << 62)) - 1
    if index >= 0 and spans[index][0] < rva < spans[index][1]:
        begin, end = spans[index]
        following = next_entry(spans, rva)
        detail = (
            f"inside 0x{BASE + begin:x}..0x{BASE + end:x} at +0x{rva - begin:x}"
            f"; enclosing entry 0x{BASE + begin:x}"
        )
        if following is not None:
            detail += f", next declared entry 0x{BASE + following:x}"
        return MID, detail
    return LEAF, "no .pdata entry and inside none"


def to_rva(value):
    return value - BASE if value >= BASE else value


def rows_from_allowlist(path):
    """Every rva in a `DETOUR_ALLOWLIST`-shaped Rust array (one `0x..,` per line)."""
    with open(path, encoding="utf-8") as handle:
        return [int(value.replace("_", ""), 16) for value in _ROW.findall(handle.read())]


def audit(table, paths, list_rows=False, out=sys.stdout):
    """Classify every row of every allowlist. Returns the mid-function findings.

    `table` is `(spans, starts, chained)` for one image, so a caller -- the selftest -- can drive
    the whole gate against a synthetic function table with no game image present.
    """
    spans, starts, chained = table
    findings = []
    for path in paths:
        counts = {ENTRY: 0, LEAF: 0, CHAINED: 0, MID: 0}
        rows = rows_from_allowlist(path)
        for rva in rows:
            kind, detail = classify(spans, starts, to_rva(rva), chained)
            counts[kind] += 1
            if list_rows:
                print(f"0x{BASE + to_rva(rva):x}  {kind:12s} {detail}", file=out)
            if kind in REFUSED:
                findings.append({"path": path, "rva": to_rva(rva), "kind": kind, "detail": detail})
        print(
            f"{os.path.relpath(path, ROOT)}: {len(rows)} rows -- {counts[ENTRY]} entry, "
            f"{counts[LEAF]} leaf, {counts[CHAINED]} CHAINED, {counts[MID]} MID-FUNCTION",
            file=out,
        )
        if not rows:
            # An empty table proves nothing about the rows it was supposed to hold.
            findings.append({"path": path, "rva": None, "detail": "the table yielded no rows"})
    return findings


def report(findings, out=sys.stderr):
    """Print each mid-function or chained row with the addresses needed to fix or drop it."""
    print(
        f"\n{len(findings)} problem row(s). A row is a licence: `resolve_detour_address` turns it\n"
        "into an address MinHook overwrites five bytes of, which is not survivable in the middle\n"
        "of a live function.",
        file=out,
    )
    for f in findings:
        print(f"\n  {os.path.relpath(f['path'], ROOT)}", file=out)
        if f["rva"] is None:
            print(f"    {f['detail']}", file=out)
            continue
        print(f"    row  0x{BASE + f['rva']:x} is {f['kind']}: {f['detail']}", file=out)
        print(
            f"    context: python3 scripts/pdata-enclosing-function.py 1172:0x{BASE + f['rva']:x}",
            file=out,
        )
    print(
        "\nEither re-derive the row at a real function entry, or drop it: an address the allowlist\n"
        "does not carry is refused loudly by er-hook, which is the outcome you want.",
        file=out,
    )


def selftest():
    """Prove the two senses of "no `.pdata` row here" are told apart, and that the gate fires.

    The synthetic half runs anywhere: it builds a PE header and a `.pdata` table in memory, so the
    classification rule and the whole `--fail-on-mid` path are proven with no game image present.
    The image-backed half pins real 2.7.2.0 precedents and skips, saying so, when the image is
    absent.
    """
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # A minimal flat image: e_lfanew at 0x3c, PE32+ magic, data directory 3 -> our table. The deobf
    # images are flat (file offset == rva), which is what lets the table sit at its own rva.
    image = bytearray(0x4000)
    e_lfanew = 0x80
    struct.pack_into("<I", image, 0x3C, e_lfanew)
    struct.pack_into("<H", image, e_lfanew + 24, 0x20B)
    directories = e_lfanew + 24 + 112
    table_rva = 0x1000
    # The last row is a chunk of the function at 0x3000: its unwind info, at 0x3800, has `Version`
    # 1 and `Flags` `UNW_FLAG_CHAININFO` (4), packed as `1 | 4 << 3`.
    functions = [(0x2000, 0x2100), (0x2100, 0x2180), (0x3000, 0x3010), (0x3100, 0x3140)]
    unwind = {(0x3100, 0x3140): 0x3800}
    image[0x3800] = 1 | function_extent.UNWIND_CHAINED << 3
    # A record past the end of the image, the shape the real one carries four of.
    records = functions + [(0x2424894C, 0xED258D4C)]
    struct.pack_into("<II", image, directories + 3 * 8, table_rva, 12 * len(records))
    for index, (begin, end) in enumerate(records):
        struct.pack_into(
            "<III", image, table_rva + 12 * index, begin, end, unwind.get((begin, end), 0)
        )
    spans = spans_from_image(bytes(image))
    check("synthetic .pdata parses, out-of-image record dropped", spans, functions)
    starts = {b for b, _ in spans}
    chained = chained_from_image(bytes(image))
    check("the chained row is read off its unwind flags", chained, frozenset({0x3100}))

    check("declared start is ENTRY", classify(spans, starts, 0x2000)[0], ENTRY)
    check("shared boundary is ENTRY", classify(spans, starts, 0x2100)[0], ENTRY)
    # Sense one: a genuine leaf. No `.pdata` row, and inside nobody's extent.
    check("gap between functions is LEAF", classify(spans, starts, 0x2500)[0], LEAF)
    check("one past a function end is LEAF", classify(spans, starts, 0x2180)[0], LEAF)
    check("past the last function is LEAF", classify(spans, starts, 0x3010)[0], LEAF)
    # Sense two: inside a function. Five bytes written here corrupt a live body.
    kind, detail = classify(spans, starts, 0x2080)
    check("inside a declared function is MID", kind, MID)
    check(
        "MID detail names the containing extent, the offset and both candidate entries",
        detail,
        "inside 0x140002000..0x140002100 at +0x80; enclosing entry 0x140002000, "
        "next declared entry 0x140002100",
    )
    # Sense three: a `.pdata` begin that is a chunk, not a function. Without the unwind flags it
    # reads as a declared start, which is the miss this kind exists for.
    check("a chained begin without its flags reads as ENTRY", classify(spans, starts, 0x3100)[0], ENTRY)
    kind, detail = classify(spans, starts, 0x3100, chained)
    check("a chained begin is CHAINED", kind, CHAINED)
    check("...and names the function the chunk belongs to", "0x140003000" in detail, True)
    check("inside a chained chunk is still MID", classify(spans, starts, 0x3120, chained)[0], MID)

    # The gate end to end, on a table it has never seen: one clean row, one mid-function row and
    # one chained row, and then an empty table, which must not read as clean.
    synthetic = (spans, starts, chained)
    with tempfile.TemporaryDirectory() as scratch:
        table = os.path.join(scratch, "allowlist.rs")
        with open(table, "w", encoding="utf-8") as handle:
            handle.write("pub(crate) const DETOUR_ALLOWLIST: [u32; 3] = [\n")
            handle.write("    0x00002000, // `CLEAN_RVA`\n")
            handle.write("    0x00002080, // `MID_RVA`\n")
            handle.write("    0x00003100, // `CHAINED_RVA`\n")
            handle.write("];\n")
        empty = os.path.join(scratch, "empty.rs")
        with open(empty, "w", encoding="utf-8") as handle:
            handle.write("pub(crate) const DETOUR_ALLOWLIST: [u32; 0] = [];\n")
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            findings = audit(synthetic, [table], out=quiet)
            empty_findings = audit(synthetic, [empty], out=quiet)
    check(
        "gate finds exactly the mid-function row and the chained row",
        [(f["rva"], f["kind"]) for f in findings],
        [(0x2080, MID), (0x3100, CHAINED)],
    )
    check("an empty table is a finding, not a pass", len(empty_findings), 1)
    check("the real allowlist parses to rows", len(rows_from_allowlist(ALLOWLIST)) > 0, True)

    path = er_game_image.image_path()
    if path is None:
        print(f"selftest: image-backed half SKIPPED ({er_game_image.missing_reason()})")
    else:
        blob = path.read_bytes()
        real = spans_from_image(blob)
        real_starts = {b for b, _ in real}
        real_chained = chained_from_image(blob)
        # The chained precedent `check-detour-rva-coverage.py` also refuses: a continuation row
        # of the function at 0x1401a8a00.
        kind, detail = classify(real, real_starts, to_rva(0x1401A8AC6), real_chained)
        check("0x1401a8ac6 is CHAINED", kind, CHAINED)
        check("0x1401a8ac6 names 0x1401a8a00 as its function", "0x1401a8a00" in detail, True)
        # The precedent, carried to 2.7.2.0: +0x360 inside another function, with the real entry
        # next.
        kind, detail = classify(real, real_starts, to_rva(0x140AEDC50))
        check("0x140aedc50 is MID-FUNCTION", kind, MID)
        check(
            "0x140aedc50 names its container and the real entry",
            detail,
            "inside 0x140aed8f0..0x140aedd37 at +0x360; enclosing entry 0x140aed8f0, "
            "next declared entry 0x140aedd40",
        )
        # The second one: the byte after a `call`.
        check("0x14095a187 is MID-FUNCTION", classify(real, real_starts, to_rva(0x14095A187))[0], MID)
        # A real leaf, so the check is not merely refusing everything: a 0x10-byte
        # `mov/mov/jmp` thunk (CS::ChrIns::GetPhysicsHitHeight) with no `.pdata` row.
        check("0x1403efe50 is a genuine LEAF", classify(real, real_starts, to_rva(0x1403EFE50))[0], LEAF)
        check("0x140aedd40 is a declared ENTRY", classify(real, real_starts, to_rva(0x140AEDD40))[0], ENTRY)

    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("vas", nargs="*", help="addresses (VA or rva) to classify")
    parser.add_argument(
        "--allowlist",
        metavar="PATH",
        action="append",
        help="classify every row of a DETOUR_ALLOWLIST-shaped array; repeatable. Defaults to "
        "crates/er-game-base/src/detour_allowlist.rs.",
    )
    parser.add_argument(
        "--rows", action="store_true", help="list every row, not just the summary and the failures"
    )
    parser.add_argument(
        "--fail-on-mid",
        action="store_true",
        help="exit 1 if any row is MID-FUNCTION or CHAINED -- such a row must not carry a detour",
    )
    parser.add_argument("--selftest", action="store_true", help="prove the classification rule")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    path = er_game_image.image_path()
    if path is None:
        # The de-Arxan'd image is untracked by policy (copyrighted game bytes), so a checkout
        # without it cannot run this. Say so rather than fail: the selftest above still proves
        # the rule, and `scripts/dearxan-deobfuscate.rs` regenerates the image.
        print(f"entry-kind classification skipped: {er_game_image.missing_reason()}")
        return 0

    blob = path.read_bytes()
    spans = spans_from_image(blob)
    table = (spans, {b for b, _ in spans}, chained_from_image(blob))

    if args.vas:
        for text in args.vas:
            kind, detail = classify(table[0], table[1], to_rva(int(text, 16)), table[2])
            print(f"{text}: {kind} ({detail})")
        return 0

    findings = audit(table, args.allowlist or [ALLOWLIST], list_rows=args.rows)
    if findings:
        # The summaries went to stdout and the report goes to stderr; without this they interleave
        # and the failure block lands above the table it is about.
        sys.stdout.flush()
        report(findings)
        if args.fail_on_mid:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
