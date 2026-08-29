#!/usr/bin/env python3
"""Score every cdylib on whether a STALE game address can still reach ELDEN RING 1.17 silently.

WHY THIS EXISTS
---------------
The game went 1.16.2 -> 1.17 on 2026-08-27 and every RVA in this workspace was written for 1.16.2.
`er_game_base::game_build` translates or REFUSES a known address, and `er-hook`'s `MhHook::new`
routes detours through it -- so a hook on a moved function fails loudly instead of corrupting the
image. That protection only covers addresses that actually go through it.

Three things a hand-built `base + SOME_RVA` can be used FOR, and they are not equally dangerous.
Counting them together overstates the problem by an order of magnitude, so this separates them:

  EXEC   `transmute(base + SOME_RVA)` and then call it. On 1.17 that is a call into whatever now
         occupies the address. Nothing refuses, nothing logs, and the game executes it. WORST.
         NOTE: a MAPPED constant used this way is just as broken as an unmapped one. The map knows
         the 1.17 destination; `base + RVA` never asks it, so the call still goes to the 1.16.2
         address. Sites are tagged `[UNMAPPED]` only to say whether the fix is one `game_rva()`
         call away or needs the address found first -- never to say the site is safe.
  WRITE  a raw store / `write_code_byte` at `base + SOME_RVA`. Corrupts the image for every later
         reader, not just this one caller.
  READ   `safe_read_usize(base + SOME_RVA)`, or an identity compare `vt != base + SOME_VTABLE_RVA`.
         Fault-safe by construction: a stale address yields a wrong answer or `None`, never a
         fault. This is the SILENT class -- the feature quietly stops working and nothing says so.
         Measured 2026-08-29: `TITLE_OWNER_VTABLE_RVA` is `CS::TitleStep` in 1.16.2 and nothing in
         1.17, and its three scans simply find no owner, forever, without a single log line.

A raw write PRECEDED BY A SIGNATURE CHECK is counted separately and is not a defect: comparing the
expected opcode before storing turns a stale address into a refusal. Measured 2026-08-29 --
`apply_splash_skip` did exactly that and logged `ABORT -- byte at 0x140b0c35d is 0x4a, expected
0x74` on a 1.17 image where the naive write would have smashed a `lea` displacement.

WHAT IT DOES NOT CLAIM
----------------------
Zero ungated sites is not "this DLL works on 1.17". It means no address can reach the game without
the gate having a say. Whether the mapped destinations are the RIGHT functions is
`scripts/verify-rva-map-1170.py`'s job, and whether the DLL then behaves is a runtime question --
the `runtime` column is read from a recorded results file, never inferred.

HOW THIS DIVIDES WITH `check-stale-rva-calls.py`
------------------------------------------------
That script already ratchets ungated CALLS repo-wide, keyed on (file, RVA constant). It is the
authority on that set and on converting it; this one deliberately does not re-litigate it. What
this adds is the two things a repo-wide call count cannot say:

  * PER-CDYLIB attribution. A flat repo-wide total hides one DLL regressing while another
    improves, and "does THIS DLL have 1.17 fixes" is a per-DLL question.
  * The WRITE and read/compare buckets, which that script does not measure at all. 0 ungated
    WRITEs across all 27 cdylibs is a safety property nothing else in the tree guards.

Refresh both baselines together when you bank an improvement.

THE RATCHET
-----------
`--check` compares the per-DLL counts against `docs/recon/dll-1170-ungated-ledger.tsv` and exits 1
if any went UP. That is the part that makes this a proof rather than a snapshot: 0 ungated WRITEs
across all 27 cdylibs is a real safety property today, and the ledger is what stops the next commit
from quietly taking it away. `--refresh` rewrites the ledger, so a deliberate increase shows up as
a reviewable diff instead of the invisible default.

USAGE
    python3 scripts/audit-1170-readiness.py
    python3 scripts/audit-1170-readiness.py --dll er-quickload
    python3 scripts/audit-1170-readiness.py --check     # the gate
    python3 scripts/audit-1170-readiness.py --refresh   # accept new counts
    python3 scripts/audit-1170-readiness.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEEDED = os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.needed.tsv")
RUNTIME_RESULTS = os.path.join(REPO, "docs", "recon", "dll-1170-runtime-results.json")
LEDGER = os.path.join(REPO, "docs", "recon", "dll-1170-ungated-ledger.tsv")

# `const NAME_RVA: usize = 0x...` -- the declaration form the map selector also keys on.
CONST = re.compile(r"const\s+([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)")
# `base + SOMETHING_RVA` / `image_base + SOMETHING_RVA` -- an address built by hand.
HAND_BUILT = re.compile(r"\b(?:base|image_base|module_base|game_base)\s*\+\s*([A-Za-z0-9_:]*RVA[A-Za-z0-9_]*)")
# The gate, in each of its spellings.
GATED = re.compile(r"\b(?:game_rva|resolve_game_address|resolve_detour_address|MhHook::new|game_ptr)\s*\(")
# A raw store through a pointer, or the byte-patch primitive.
#
# The third alternative is the one this audit was missing until 2026-08-29. Naming the pointer
# first (`let target = ...; *target = 1`) is a style, not a requirement, and the tree writes
# globals the other way round just as often:
#
#     *((base + TITLE_GLOBAL_ACCEPT_BYTE_RVA) as *mut u8) = 1;
#
# Six such stores existed while this audit reported ZERO ungated writes across all 27 cdylibs --
# and the ledger's whole claim is that zero. One of the six was the title's zero-input accept byte,
# writing to a stale 1.16.2 address on every 1.17 boot: the menu never opened, and nothing in the
# gate said a word, because a store to a moved global neither faults nor logs. The assignment can
# also sit on the next line, which is why this is matched against the window rather than one line.
RAW_WRITE = re.compile(
    r"write_code_byte|write_code_bytes|\*\s*target\s*=|\*\s*(?:addr|address|slot)\s*="
    r"|as\s*\*mut\s+[\w:]+\s*\)\s*="
)
# The address is turned into something callable. Matched against the text IMMEDIATELY BEFORE the
# `base + rva`, not a window: `is_in_state(sm, base + TITLE_STATE_DESC_LOOP_RVA)` sits two lines
# from a `transmute` and is a DATA pointer, and a window-based match filed 13 such arguments as
# executable code. A stale data pointer is still wrong -- the comparison silently never matches --
# but it is the read/compare hazard, not the execute one, and conflating them inflates the number
# that matters most.
EXEC_USE = re.compile(r"(?:transmute|as\s+\*const\s+fn|as\s+extern)\s*[(<]?\s*$")
# The address is only read through, or compared against. Fault-safe: wrong answer, never a fault.
READ_USE = re.compile(r"safe_read_|read_usize|read_u8|read_u32|!=|==|\.contains|matches!")
# Evidence that a write validates what it is overwriting first.
SIGNATURE_CHECK = re.compile(r"EXPECTED|expected|!=\s*[A-Z0-9_]*(?:JE|OPCODE|BYTE)|prologue|signature", re.I)


def crate_closure(name: str, pkgs: dict, seen: set | None = None) -> set:
    seen = seen if seen is not None else set()
    if name in seen or name not in pkgs:
        return seen
    seen.add(name)
    for dep in pkgs[name]["dependencies"]:
        if dep["kind"] is None and dep["name"] in pkgs:
            crate_closure(dep["name"], pkgs, seen)
    return seen


def crate_sources(pkgs: dict, crate: str) -> list[str]:
    root = os.path.dirname(pkgs[crate]["manifest_path"])
    out = []
    for base, _dirs, files in os.walk(os.path.join(root, "src")):
        out.extend(os.path.join(base, f) for f in files if f.endswith(".rs"))
    return out


def mapped_constants() -> set:
    names = set()
    try:
        with open(NEEDED, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("0x"):
                    parts = line.split("\t")
                    if len(parts) > 2:
                        names.add(parts[2].strip())
    except OSError:
        pass
    return names


def scan(paths: list[str], mapped: set) -> dict:
    """Count the ways a stale address could reach the game from this source set."""
    declared = {}
    buckets = {"exec": [], "write": [], "checked_write": [], "read": []}
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for match in CONST.finditer("\n".join(lines)):
            declared[match.group(1)] = int(match.group(2).replace("_", ""), 16)
        for index, line in enumerate(lines):
            if line.lstrip().startswith("//"):
                continue
            hand = HAND_BUILT.search(line)
            if not hand:
                continue
            name = hand.group(1).rsplit("::", 1)[-1]
            # A window, because the gate call and the use are often a line or two apart.
            window = "\n".join(lines[max(0, index - 3) : index + 4])
            if GATED.search(window):
                continue
            # What this OCCURRENCE is used for, judged from the text right before it.
            before = line[: hand.start()].rstrip()
            where = f"{os.path.relpath(path, REPO)}:{index + 1}"
            entry = (where, name + ("" if name in mapped else " [UNMAPPED]"))
            # Order matters: a write is a write even if the same window also reads, and an exec is
            # worse than a read. Only a window with NEITHER is the benign read/compare bucket.
            if RAW_WRITE.search(window):
                buckets["checked_write" if SIGNATURE_CHECK.search(window) else "write"].append(entry)
            elif EXEC_USE.search(before) or EXEC_USE.search(
                "\n".join(lines[max(0, index - 1) : index + 1]).rstrip()
            ):
                buckets["exec"].append(entry)
            else:
                buckets["read"].append(entry)
    unmapped = sorted(n for n in declared if n not in mapped)
    return {"declared": len(declared), "unmapped": unmapped, **buckets}


def runtime_results() -> dict:
    try:
        with open(RUNTIME_RESULTS, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def selftest() -> int:
    """Assert the regex set on facts established by the 2026-08-29 bisect."""
    failures = []
    # The splash-skip site is a raw write WITH a signature check -- it must not read as a defect.
    sample = """
    let target = (base + SPLASH_SKIP_RVA) as *mut u8;
    let existing = unsafe { *target };
    if existing != SPLASH_SKIP_EXPECTED_JE { return; }
    unsafe { *target = SPLASH_SKIP_REPLACEMENT_JG };
    """
    if not RAW_WRITE.search(sample):
        failures.append("RAW_WRITE missed a `*target = ` store")
    # The shape that went unseen for the whole 1.17 migration: cast-and-assign, no named pointer.
    if not RAW_WRITE.search("*((base + TITLE_GLOBAL_ACCEPT_BYTE_RVA) as *mut u8) = 1;"):
        failures.append("RAW_WRITE missed a cast-and-assign store")
    # Same store with the value on the following line, which is how rustfmt leaves the long ones.
    if not RAW_WRITE.search("*((module_base + SOME_RVA) as *mut u8) =\n    VALUE;"):
        failures.append("RAW_WRITE missed a cast-and-assign store split across lines")
    # A read through the same cast is NOT a write; misfiling it would inflate the count it guards.
    if RAW_WRITE.search("let after = unsafe { *((base + SOME_RVA) as *const u8) };"):
        failures.append("RAW_WRITE wrongly claimed a `*const` read is a store")
    if not SIGNATURE_CHECK.search(sample):
        failures.append("SIGNATURE_CHECK missed an EXPECTED-opcode compare")
    if not HAND_BUILT.search("let x = base + SOME_RVA;"):
        failures.append("HAND_BUILT missed `base + SOME_RVA`")
    if not EXEC_USE.search("unsafe { std::mem::transmute("):
        failures.append("EXEC_USE missed text ending in `transmute(`")
    if EXEC_USE.search("unsafe { is_in_state(sm, "):
        failures.append("EXEC_USE wrongly claimed a plain call argument is executable")
    if not READ_USE.search("if vt != base + FOO_VTABLE_RVA {"):
        failures.append("READ_USE missed an identity compare")
    if EXEC_USE.search("safe_read_usize(base + FOO_RVA)"):
        failures.append("EXEC_USE wrongly claimed a fault-safe read is executable")
    if not GATED.search("game_rva(FOO_RVA as u32)"):
        failures.append("GATED missed game_rva(")
    if not GATED.search("resolve_game_address(base + FOO_RVA, \"FOO\")"):
        failures.append("GATED missed resolve_game_address(")
    if not CONST.match("const GET_CURRENT_MAP_ID_RVA: usize = 0x5eefb0;"):
        failures.append("CONST did not parse a plain declaration")
    if len(mapped_constants()) < 100:
        failures.append(f"only {len(mapped_constants())} mapped constants read from {NEEDED}")
    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", help="report one cdylib in detail")
    parser.add_argument("--check", action="store_true", help="fail if any count rose above the ledger")
    parser.add_argument("--refresh", action="store_true", help="rewrite the ledger to current counts")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    meta = json.loads(
        subprocess.run(
            ["cargo", "metadata", "--no-deps", "--format-version", "1"],
            capture_output=True, text=True, cwd=REPO, check=True, timeout=30,
        ).stdout
    )
    pkgs = {p["name"]: p for p in meta["packages"]}
    cdylibs = sorted(
        p["name"] for p in meta["packages"] if any("cdylib" in t["kind"] for t in p["targets"])
    )
    mapped = mapped_constants()
    runtime = runtime_results()

    if args.dll:
        if args.dll not in pkgs:
            print(f"unknown crate {args.dll}", file=sys.stderr)
            return 1
        cdylibs = [args.dll]

    quiet = args.check or args.refresh
    if not quiet:
        print(f"{'cdylib':26s} {'crates':>6} {'consts':>6} {'unmapd':>6} {'EXEC':>5} {'WRITE':>5} {'ok-wr':>5} {'read':>5}  runtime")
    totals = {"exec": 0, "write": 0, "read": 0}
    measured = {}
    for name in cdylibs:
        closure = crate_closure(name, pkgs)
        paths = [p for c in closure for p in crate_sources(pkgs, c)]
        result = scan(paths, mapped)
        for key in totals:
            totals[key] += len(result[key])
        measured[name] = (len(result["exec"]), len(result["write"]))
        if quiet:
            continue
        print(
            f"{name:26s} {len(closure):6d} {result['declared']:6d} {len(result['unmapped']):6d} "
            f"{len(result['exec']):5d} {len(result['write']):5d} "
            f"{len(result['checked_write']):5d} {len(result['read']):5d}  {runtime.get(name, '-')}"
        )
        if args.dll:
            for label in ("exec", "write", "checked_write", "read"):
                for where, const in result[label]:
                    print(f"    {label:14s} {where}  ({const})")
            if result["unmapped"]:
                print(f"    unmapped constants: {', '.join(result['unmapped'])}")
    if args.refresh:
        with open(LEDGER, "w", encoding="utf-8") as handle:
            handle.write("# cdylib\tungated_exec\tungated_write\n")
            handle.write("# Written by scripts/audit-1170-readiness.py --refresh. A RATCHET: the\n")
            handle.write("# checker fails when a count RISES. Lower is the only direction that\n")
            handle.write("# passes silently. 0 ungated writes across every cdylib is a safety\n")
            handle.write("# property of this tree -- no DLL can corrupt the 1.17 image with a\n")
            handle.write("# stale address -- and this file is what keeps it true.\n")
            for name, counts in sorted(measured.items()):
                handle.write(f"{name}\t{counts[0]}\t{counts[1]}\n")
        print(f"wrote {os.path.relpath(LEDGER, REPO)} ({len(measured)} rows)")
        return 0

    if args.check:
        ledger = {}
        try:
            with open(LEDGER, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("#") or not line.strip():
                        continue
                    name, exec_n, write_n = line.split("\t")
                    ledger[name] = (int(exec_n), int(write_n))
        except OSError:
            print(f"[audit-1170] ERROR: no ledger at {os.path.relpath(LEDGER, REPO)}; run --refresh")
            return 1
        regressions = []
        for name, (exec_n, write_n) in sorted(measured.items()):
            was = ledger.get(name)
            if was is None:
                regressions.append(f"{name}: NEW cdylib with {exec_n} ungated exec / {write_n} ungated write")
            else:
                if exec_n > was[0]:
                    regressions.append(f"{name}: ungated EXEC {was[0]} -> {exec_n}")
                if write_n > was[1]:
                    regressions.append(f"{name}: ungated WRITE {was[1]} -> {write_n}")
        for line in regressions:
            print(f"[audit-1170] REGRESSION {line}")
        if regressions:
            print("[audit-1170] A hand-built `base + *_RVA` was added where the 1.17 gate cannot see it.")
            print("  Route it through `er_game_base::mem::game_rva` / `resolve_game_address` so a")
            print("  moved function REFUSES instead of executing whatever now sits at the address.")
            print("  If the increase is deliberate, accept it in one command and say why in the PR:")
            print("      python3 scripts/audit-1170-readiness.py --refresh")
            return 1
        improved = [n for n, c in measured.items() if n in ledger and (c[0] < ledger[n][0] or c[1] < ledger[n][1])]
        print(f"[audit-1170] ok -- no cdylib regressed" + (f"; {len(improved)} improved (run --refresh to bank it)" if improved else ""))
        return 0

    if not args.dll:
        print()
        print(f"TOTAL ungated EXEC {totals['exec']}, WRITE {totals['write']}, read/compare {totals['read']}")
        print("All three are hand-built `base + *_RVA` the gate never sees. EXEC executes a stale")
        print("address; WRITE corrupts the image; read/compare is fault-safe and merely goes")
        print("silently wrong. `ok-wr` is a write that checks the expected bytes first, which")
        print("turns a stale address into a refusal and is therefore NOT a defect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
