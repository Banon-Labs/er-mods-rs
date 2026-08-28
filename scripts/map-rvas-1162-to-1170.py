#!/usr/bin/env python3
"""Map 1.16.2 game addresses onto ELDEN RING 1.17 by relocation-aware byte content.

ELDEN RING 1.17 (PE FileVersion 2.7.0.0) shipped 2026-08-27 and moved code. Every game address
in this workspace is a 1.16.2 RVA, and `er-hook`'s build gate now REFUSES to install a detour on
an unrecognised build rather than corrupt it -- so what used to be one crash per launch is a list
of addresses to re-point. This turns that list into a table.

WHY NOT `dump-deobf-shift.py` DIRECTLY
--------------------------------------
That tool maps `dump-exec.bin` <-> `eldenring-deobf.bin`, and its dump side is still the **1.16.1**
runtime dump: it is cross-version by two patches and its region table describes a shift staircase
that does not exist here. Only its matcher is reusable, so this script imports `map_va` and
`build_pattern` from it and supplies two properly-versioned images, with the region assist OFF --
there is no measured 1.16.2->1.17 region table, and an *estimate* is exactly the kind of
plausible-but-wrong address that lands mid-function.

WHAT A RESULT MEANS
-------------------
A mapping is a CANDIDATE, not a licence to hook. The matcher wildcards relocation-sensitive
operand bytes and requires a unique hit, which is strong, but a function whose body genuinely
changed in 1.17 can still match on its unchanged prologue while its behaviour differs -- and a
mid-function match is worse than no match. Before any mapped address is written into code, read
the 1.17 function and confirm it does the same job.

USAGE
    python3 scripts/map-rvas-1162-to-1170.py 0x1407ada40 0x14025f5f0
    python3 scripts/map-rvas-1162-to-1170.py --from-refusal-log <er-quickload-autoload-debug.log>
    python3 scripts/map-rvas-1162-to-1170.py --tsv docs/recon/rva-map-1162-to-1170.tsv 0x...

The refusal-log mode reads the addresses `er-hook` refused at runtime, which is the authoritative
work list: it is what this build actually tried to hook, not what someone thought it hooks.
"""

import argparse
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_IMAGE = os.path.join(ROOT, "eldenring-deobf.bin")
DST_IMAGE = os.path.join(ROOT, "eldenring-deobf-1.17.bin")
BASE = 0x140000000
# Signature lengths tried, longest first. A long signature is the strong evidence, but it also
# reaches past short functions into whatever follows them -- `GetScadutreeBlessing` is 25 bytes,
# so a 40-byte signature drags in the next function and fails to match when THAT moved. Falling
# back through shorter windows recovers those; the length that succeeded is reported, because a
# 16-byte match is materially weaker evidence than a 40-byte one.
SIGNATURE_LADDER = (40, 32, 24, 16)
# Signature length handed to the matcher when the caller pins one.
DEFAULT_SIGNATURE_BYTES = SIGNATURE_LADDER[0]
# Ground truth from the er-ersc-sigshim work: both were established by reading the LIVE 1.17
# process and disassembling both images, so they are facts, not predictions. `--selftest` asserts
# the mapper reproduces them -- a matcher that cannot re-derive a known answer cannot be trusted
# with an unknown one.
KNOWN_MAPPINGS = {
    # GetScadutreeBlessing(PlayerGameData*) -- the flag/override fields moved +0xab5/+0xab4 ->
    # +0xabd/+0xabc, so only the shape around them is stable.
    0x14025F5F0: 0x14025F5D0,
    # The call site in GetWwiseSettings that Seamless Co-op uses to locate GetAllocator.
    0x1422222D8: 0x142224238,
}
# The matcher searches +-window around the source offset first, then widens. 1.17 moved code by
# far more than the intra-version staircase this default was tuned for, so start wide.
DEFAULT_WINDOW = 0x200000


def addresses_from_refusal_log(path):
    """Game-image addresses `er-hook` refused, in first-seen order.

    This is the authoritative work list: what the build actually tried to hook, rather than what
    a reader of the source thinks it hooks.
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    seen = []
    for match in re.finditer(r"HOOK REFUSED \(\w+(?:::\w+)? (0x[0-9a-fA-F]+)\)", text):
        address = int(match.group(1), 16)
        # OS-DLL addresses are resolved through GetProcAddress and carry no version assumption;
        # the gate no longer refuses them, but logs from before it was narrowed still list them.
        if address < BASE or address >= BASE + 0x10000000:
            continue
        if address not in seen:
            seen.append(address)
    return seen


def build_masked_pattern(image, offset, want_bytes):
    """Decode from `offset` and return `(pattern, mask)` with every version-fragile operand byte
    wildcarded.

    Three classes of byte move when the game is patched even though the code is "the same":

    * RIP-relative displacements and branch targets -- the code moved, so the delta changed.
    * **Memory displacements on a register base** -- 1.17's dominant change. `GetScadutreeBlessing`
      is byte-identical except `[rcx+0xab5]` -> `[rcx+0xabd]`, because `PlayerGameData` grew 8
      bytes. The shared matcher in `dump-deobf-shift.py` does not mask these, which is why it
      cannot re-derive either mapping this repo already knows by hand.
    * Immediates -- ids and sizes get retuned across patches.

    What survives is opcode shape and register allocation, which is what identifies a function.
    """
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    pattern = bytearray()
    mask = bytearray()
    consumed = 0
    for insn in md.disasm(bytes(image[offset : offset + want_bytes * 3]), 0):
        if consumed >= want_bytes:
            break
        raw = bytearray(insn.bytes)
        keep = bytearray(b"\x01" * len(raw))
        encoding = getattr(insn, "encoding", None)
        if encoding is not None:
            for at, size in (
                (encoding.disp_offset, encoding.disp_size),
                (encoding.imm_offset, encoding.imm_size),
            ):
                if size:
                    for i in range(at, min(at + size, len(keep))):
                        keep[i] = 0
        pattern += raw
        mask += keep
        consumed += len(raw)
        # A `ret`/`jmp` is a natural end; stopping there keeps a short function's signature from
        # reaching into whatever follows it, which is exactly what defeated the 40-byte default.
        if insn.mnemonic in ("ret", "jmp"):
            break
    if not pattern:
        return None, None
    return bytes(pattern), bytes(mask)


def find_unique(haystack, pattern, mask):
    """Every offset where `pattern` matches under `mask`, capped so an over-wildcarded signature
    reports as ambiguous rather than silently taking the first of thousands."""
    anchor_at = next((i for i, keep in enumerate(mask) if keep), None)
    if anchor_at is None:
        return []
    # Anchor the scan on the longest run of kept bytes: `bytes.find` on that run is C-speed, and
    # the masked comparison then runs on a handful of candidates instead of 98 MB of image.
    best_start, best_len, run_start = 0, 0, None
    for i, keep in enumerate(mask + b"\x00"):
        if keep and run_start is None:
            run_start = i
        elif not keep and run_start is not None:
            if i - run_start > best_len:
                best_start, best_len = run_start, i - run_start
            run_start = None
    anchor = pattern[best_start : best_start + best_len]
    hits = []
    at = haystack.find(anchor)
    while at >= 0 and len(hits) <= 8:
        start = at - best_start
        if start >= 0 and start + len(pattern) <= len(haystack):
            window = haystack[start : start + len(pattern)]
            if all(not keep or window[i] == pattern[i] for i, keep in enumerate(mask)):
                hits.append(start)
        at = haystack.find(anchor, at + 1)
    return hits


def map_one(source, target, va, args):
    """Map one address by masked content. Returns `(new_va, note)`."""
    offset = va - BASE
    if offset < 0 or offset >= len(source):
        return None, "source VA outside the 1.16.2 image"
    lengths = [args.want] if args.want_pinned else list(SIGNATURE_LADDER)
    note = "no unique match"
    for length in lengths:
        pattern, mask = build_masked_pattern(source, offset, length)
        if pattern is None:
            return None, "source bytes did not decode"
        hits = find_unique(target, pattern, mask)
        kept = sum(1 for keep in mask if keep)
        if len(hits) == 1:
            return BASE + hits[0], f"unique, {len(pattern)}B signature, {kept}B fixed"
        if len(hits) > 1:
            # Two functions can share a shape -- ELDEN RING has more than one getter built exactly
            # like `GetScadutreeBlessing`. Report every candidate and let the second pass choose
            # by evidence; picking the nearest here looked reasonable and was measurably bad
            # (it resolved addresses to sites 0x400000 away, and only 2 of 51 results landed on a
            # function boundary).
            return None, ("candidates:" + ",".join(f"{BASE + h:#x}" for h in hits))
        note = f"no match at {len(pattern)}B"
    return None, note



# How far away a unique mapping may be and still speak for this address. The 1.16.2 -> 1.17 shift
# is locally constant but changes FAST: measured around `GetScadutreeBlessing`, a neighbour
# 0x320 bytes away shifts by -0x20 while one 0xb27 bytes away shifts by +0x10. So the anchor is
# the SINGLE nearest unique mapping, not a consensus of everything in a wide window -- an early
# version of this demanded unanimity over 0x40000 and resolved nothing.
REGION_RADIUS = 0x8000


def resolve_all(source, target, addresses, args):
    """Map every address, then use the unambiguous results to settle the ambiguous ones.

    Pass 1 keeps only signatures that matched exactly once -- strong evidence on its own. Pass 2
    takes each remaining address's candidate list and keeps the candidate whose delta equals the
    delta every nearby pass-1 mapping agrees on. Anything that survives neither pass is reported
    UNRESOLVED, which is the honest answer: a wrong address here is a mid-function detour, and a
    blank cell costs a Ghidra lookup while a wrong one costs a crash.
    """
    results = {}
    pending = {}
    for va in addresses:
        mapped, note = map_one(source, target, va, args)
        if mapped is not None:
            results[va] = (mapped, note)
        elif note.startswith("candidates:"):
            pending[va] = [int(c, 16) for c in note[len("candidates:"):].split(",")]
        else:
            results[va] = (None, note)

    def regional_delta(va):
        """Delta of the nearest uniquely-mapped address within `REGION_RADIUS`, or `None`."""
        anchors = [
            (abs(other - va), mapped - other)
            for other, (mapped, note) in results.items()
            if mapped is not None
            and note.startswith("unique")
            and abs(other - va) <= REGION_RADIUS
        ]
        return min(anchors)[1] if anchors else None

    for va, candidates in pending.items():
        delta = regional_delta(va)
        agreeing = [c for c in candidates if delta is not None and c - va == delta]
        if len(agreeing) == 1:
            results[va] = (
                agreeing[0],
                f"nearest-anchor delta {delta:+#x}, {len(candidates)} shape candidates",
            )
        else:
            results[va] = (
                None,
                f"UNRESOLVED: {len(candidates)} shape matches, none at the nearest anchor's "
                f"delta ({'no anchor nearby' if delta is None else f'{delta:+#x}'})",
            )
    return results

def selftest(source, target, args):
    """Assert the mapper reproduces the two mappings established by hand from the live process."""
    failures = []
    # `GetScadutreeBlessing` shares its shape with another getter, so it is only resolvable with a
    # nearby anchor -- exactly the situation the two-pass design exists for. 0x14025f2d0 maps
    # uniquely 0x320 bytes away with the same -0x20 delta; supplying it is what a real run gets
    # for free from the rest of the work list.
    anchor_fixture = 0x14025F2D0
    resolved = resolve_all(
        source, target, list(KNOWN_MAPPINGS) + [anchor_fixture], args
    )
    for old_va, expected in KNOWN_MAPPINGS.items():
        found, note = resolved[old_va]
        state = "ok  " if found == expected else "FAIL"
        print(f"  {state} {old_va:#x} -> expected {expected:#x}, got "
              f"{'UNMAPPED' if found is None else f'{found:#x}'}  ({note})")
        if found != expected:
            failures.append(old_va)
    if failures:
        print(f"selftest FAILED for {len(failures)} known mapping(s)")
        return 1
    print("selftest passed: both known 1.17 mappings re-derived from bytes alone")
    return 0

def main():
    parser = argparse.ArgumentParser(
        description="Map 1.16.2 addresses onto 1.17 by relocation-aware byte content."
    )
    parser.add_argument("vas", nargs="*", help="1.16.2 VAs (hex 0x... or decimal)")
    parser.add_argument(
        "--from-refusal-log",
        metavar="LOG",
        help="read the addresses er-hook refused from a runtime log",
    )
    parser.add_argument("--tsv", metavar="PATH", help="also write the table here")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="assert the mapper re-derives the two mappings established from the live 1.17 process",
    )
    parser.add_argument(
        "--bytes",
        dest="want",
        type=lambda s: int(s, 0),
        default=DEFAULT_SIGNATURE_BYTES,
        help=f"signature length in decoded bytes (default {DEFAULT_SIGNATURE_BYTES})",
    )
    parser.add_argument(
        "--window",
        type=lambda s: int(s, 0),
        default=DEFAULT_WINDOW,
        help=f"initial +- search window (default {DEFAULT_WINDOW:#x})",
    )
    args = parser.parse_args()

    for image in (SRC_IMAGE, DST_IMAGE):
        if not os.path.exists(image):
            sys.exit(
                f"missing image: {image}\n"
                "Generate with scripts/dearxan-deobfuscate.rs against the matching eldenring.exe "
                "(cargo run --release --example deobfuscate -- <exe> <out>)."
            )
    source = open(SRC_IMAGE, "rb").read()
    target = open(DST_IMAGE, "rb").read()
    # `--bytes` was given explicitly only if it differs from the ladder's first rung; that is the
    # signal to pin one length instead of walking the ladder.
    args.want_pinned = args.want != DEFAULT_SIGNATURE_BYTES

    if args.selftest:
        return selftest(source, target, args)

    wanted = [int(v, 0) for v in args.vas]
    if args.from_refusal_log:
        wanted += [a for a in addresses_from_refusal_log(args.from_refusal_log) if a not in wanted]
    if not wanted:
        sys.exit("no addresses: pass VAs or --from-refusal-log")

    resolved = resolve_all(source, target, wanted, args)
    rows = []
    mapped = 0
    for va in wanted:
        new_va, note = resolved[va]
        if new_va is None:
            rows.append((va, None, None, note))
        else:
            rows.append((va, new_va, new_va - va, note))
            mapped += 1

    width = max(len(f"{va:#x}") for va, *_ in rows)
    for va, new_va, delta, note in rows:
        if new_va is None:
            print(f"{va:#0{width}x}  ->  UNMAPPED           {note}")
        else:
            print(f"{va:#0{width}x}  ->  {new_va:#012x}  delta {delta:+#x}  ({note})")
    print(f"\nmapped {mapped}/{len(rows)}; every mapping is a CANDIDATE -- read the 1.17 function "
          "before hooking it")

    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as handle:
            handle.write("# 1.16.2 VA\t1.17 VA\tdelta\tmethod-or-error\n")
            handle.write(
                "# Generated by scripts/map-rvas-1162-to-1170.py. Candidates, not verified "
                "hook sites: a match on an unchanged prologue does not prove the body is\n"
                "# unchanged. Regenerate rather than hand-editing.\n"
            )
            for va, new_va, delta, note in rows:
                if new_va is None:
                    handle.write(f"{va:#x}\t-\t-\t{note}\n")
                else:
                    handle.write(f"{va:#x}\t{new_va:#x}\t{delta:+#x}\t{note}\n")
        print(f"wrote {args.tsv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
