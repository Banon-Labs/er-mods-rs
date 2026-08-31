#!/usr/bin/env python3
"""Cross-check the 1.16.2 -> 1.17 DATA map's VTABLE rows against MSVC RTTI identity.

WHY THIS IS A SECOND OPINION AND NOT A REPEAT
---------------------------------------------
`map-data-rvas-1162-to-1170.py` carries a datum by the CODE that references it: it maps the
referencing function onto 1.17 and re-reads the displacement. Every row it produces therefore
depends on the function map being right about that one function. RTTI depends on none of that.
A vtable's `[base-8]` qword points at its CompleteObjectLocator, whose TypeDescriptor holds the
class's mangled name -- metadata the compiler emitted, present in both images, and read here
with no pairing, no signature matching and no shared input with the voting method.

So when both agree, two methods with disjoint failure modes agree. When they disagree, the
reference vote is wrong or the row is not a vtable, and either way it is worth a stop.

WHAT COUNTS AS PROOF, AND WHAT ONLY COUNTS AS CORROBORATION
-----------------------------------------------------------
A mangled name that occurs EXACTLY ONCE per image identifies its vtable outright: PROVEN.
ELDEN RING has plenty of names that occur several times (base subobject vtables, and 5,616 of
the 10,202 are `std::_Func_impl` / lambda functors whose names repeat). For those the name alone
is not an identity, so the check falls back to the ORDINAL: if the source is the k-th vtable
carrying that name in 1.16.2, the destination must be the k-th carrying it in 1.17. Both images
hold the same 10,202 vtables in the same relative order, so the ordinal is meaningful -- but it
is an ordering argument, not a unique key, so it is reported as ORDINAL rather than PROVEN.

A row whose source is not a vtable at all (a plain global, a table of pointers) is N/A here.
That is most of the map, and it is why this tool does not replace the voting one.

USAGE
  python3 scripts/verify-data-rvas-by-rtti.py                 # check the tracked data map
  python3 scripts/verify-data-rvas-by-rtti.py --deltas        # + whole-.rdata delta census
  python3 scripts/verify-data-rvas-by-rtti.py --selftest
"""

import argparse
import collections
import os
import re
import subprocess
import sys

# MSVC stamps each translation unit's ANONYMOUS NAMESPACE with a per-build hash, so
# `?A0x7c8d539b` in 1.16.2 is `?A0x8fca6706` in 1.17 for the same namespace. Comparing the
# raw name therefore fails on a class that is otherwise byte-identical, and it fails SILENTLY
# as a "no counterpart" rather than as a mismatch. Measured on the tracked map:
# `SELECTOR_STEP_VTABLE_RVA` is `MenuJobWithContext<LoadJobContext@?A0x7c8d539b,
# lambda_1af212c9...>` in 1.16.2 and the identical name with `?A0x8fca6706` in 1.17 -- the
# LAMBDA hash is stable across builds, only the namespace tag moves. Nothing else about the
# name is touched; two genuinely different classes still differ.
ANON_NAMESPACE = re.compile(r"\?A0x[0-9a-f]{8}")


def canonical(name):
    return ANON_NAMESPACE.sub("?A0x@ANON@", name)

BASE = 0x140000000
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OLD_IMAGE = os.path.join(REPO, "eldenring-deobf.bin")
NEW_IMAGE = os.path.join(REPO, "eldenring-deobf-1.17.bin")
DATA_MAP = os.path.join(REPO, "docs", "recon", "rva-map-1162-to-1170.data.tsv")
SCAN = os.path.join(REPO, "scripts", "rtti-scan-all.py")


def classmap(image, cache):
    """{rva: mangled_name} for every RTTI vtable in `image`, via rtti-scan-all.py."""
    if not os.path.exists(cache) or os.path.getmtime(cache) < os.path.getmtime(image):
        subprocess.run(
            [sys.executable, SCAN, cache, "--image", image],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    out = {}
    with open(cache, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            va, _, name = line.rstrip("\n").partition("\t")
            if name:
                out[int(va, 16) - BASE] = canonical(name)
    return out


def ordinals(cm):
    """{rva: (name, k)} -- k is the 0-based index of this vtable among same-named ones."""
    seen = collections.Counter()
    out = {}
    for rva in sorted(cm):
        name = cm[rva]
        out[rva] = (name, seen[name])
        seen[name] += 1
    return out


def by_key(cm):
    """{(name, k): rva}."""
    return {key: rva for rva, key in ordinals(cm).items()}


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 3:
                continue
            rows.append(
                (int(fields[0], 16), int(fields[1], 16), fields[2],
                 fields[3] if len(fields) > 3 else "")
            )
    return rows


def judge(src, dst, old_cm, new_cm, old_ord, new_key, old_names, new_names):
    """One row's verdict: (code, detail)."""
    if src not in old_cm:
        return "N/A", "source is not an RTTI vtable"
    name, k = old_ord[src]
    if old_names[name] == 1 and new_names.get(name, 0) == 1:
        expected = new_key.get((name, 0))
        if expected is None:
            return "MISSING", f"{name} has no 1.17 vtable"
        if expected == dst:
            return "PROVEN", name
        return "DISAGREE", f"{name} is at 0x{expected:x} in 1.17, map says 0x{dst:x}"
    expected = new_key.get((name, k))
    if expected is None:
        return "MISSING", f"{name}#{k} has no 1.17 counterpart"
    if expected == dst:
        return "ORDINAL", f"{name}#{k} (name occurs {old_names[name]}x/{new_names.get(name, 0)}x)"
    return "DISAGREE", f"{name}#{k} is at 0x{expected:x} in 1.17, map says 0x{dst:x}"


def run(rows, old_cm, new_cm, quiet=False):
    old_ord = ordinals(old_cm)
    new_key = by_key(new_cm)
    old_names = collections.Counter(old_cm.values())
    new_names = collections.Counter(new_cm.values())
    tally = collections.Counter()
    results = []
    for src, dst, const, note in rows:
        code, detail = judge(src, dst, old_cm, new_cm, old_ord, new_key, old_names, new_names)
        tally[code] += 1
        results.append((code, src, dst, const, note, detail))
        if not quiet and code != "N/A":
            print(f"  {code:9s} 0x{src:x} -> 0x{dst:x}  {const}  [{note}]  {detail}")
    return tally, results


def delta_census(old_cm, new_cm):
    """Delta for every uniquely-named vtable -- an RTTI-only view of how .rdata moved."""
    old_names = collections.Counter(old_cm.values())
    new_names = collections.Counter(new_cm.values())
    new_by_name = {name: rva for rva, name in new_cm.items()}
    census = collections.Counter()
    per_region = collections.defaultdict(collections.Counter)
    for rva, name in old_cm.items():
        if old_names[name] != 1 or new_names.get(name, 0) != 1:
            continue
        delta = new_by_name[name] - rva
        census[delta] += 1
        per_region[rva >> 20][delta] += 1
    return census, per_region


def bracket(rows, old_cm, new_cm, window=0x20000):
    """Bracket every NON-vtable row with the nearest RTTI vtables on each side.

    The data map's own `bracket` corroboration uses the data map's own anchors, so it cannot be a
    second opinion about them. RTTI anchors are a different population entirely -- 10,202 vtables
    the compiler labelled, paired here by mangled name with no reference to the map -- and they
    are dense enough through `.rdata` to put a real fence around a string or a pointer table that
    only one instruction reaches.

    A row AGREES when its delta equals a delta observed on BOTH sides of it. It is INSIDE when
    the delta merely falls within the neighbours' range (`.rdata` moves as a fine staircase, so
    landing between two different neighbour deltas is common and is not proof). It DISAGREES when
    the delta is outside the bracket entirely -- that is the shape of a row that jumped a
    discontinuity.
    """
    old_names = collections.Counter(old_cm.values())
    new_names = collections.Counter(new_cm.values())
    new_by_name = {name: rva for rva, name in new_cm.items()}
    anchors = sorted(
        (rva, new_by_name[name] - rva)
        for rva, name in old_cm.items()
        if old_names[name] == 1 and new_names.get(name, 0) == 1
    )
    positions = [rva for rva, _ in anchors]
    verdicts = []
    for src, dst, const, note in rows:
        if src in old_cm:
            continue
        delta = dst - src
        index = 0
        lo = hi = None
        import bisect

        index = bisect.bisect_left(positions, src)
        below = [anchors[i] for i in range(max(0, index - 8), index) if src - anchors[i][0] <= window]
        above = [anchors[i] for i in range(index, min(len(anchors), index + 8)) if anchors[i][0] - src <= window]
        if not below or not above:
            verdicts.append(("NO-ANCHOR", src, dst, const, note, "no RTTI vtable within window"))
            continue
        below_deltas = {d for _, d in below}
        above_deltas = {d for _, d in above}
        span_lo, span_hi = min(below_deltas | above_deltas), max(below_deltas | above_deltas)
        if delta in below_deltas and delta in above_deltas:
            code, detail = "AGREE", f"{delta:+#x} seen on both sides"
        elif span_lo <= delta <= span_hi:
            code, detail = "INSIDE", f"{delta:+#x} within [{span_lo:+#x}, {span_hi:+#x}]"
        else:
            code, detail = "DISAGREE", f"{delta:+#x} outside [{span_lo:+#x}, {span_hi:+#x}]"
        verdicts.append((code, src, dst, const, note, detail))
    return verdicts


def selftest(old_cm, new_cm):
    """Positive control AND a regressed control, so a green here cannot be vacuous."""
    rows = read_rows(DATA_MAP)
    tally, results = run(rows, old_cm, new_cm, quiet=True)
    checked = tally["PROVEN"] + tally["ORDINAL"]
    print(f"selftest: {len(rows)} rows, {checked} carry RTTI identity, {tally['N/A']} are not vtables")
    if checked == 0:
        print("FAIL: nothing was checked -- the filter matched no rows, so any verdict is vacuous")
        return 1
    if tally["DISAGREE"] or tally["MISSING"]:
        print(f"FAIL: {tally['DISAGREE']} DISAGREE, {tally['MISSING']} MISSING on the tracked map")
        return 1
    print(f"  positive control: {checked} real rows verified clean")

    # NEGATIVE CONTROL. Regress every checked row's destination onto the NEXT vtable in 1.17
    # and require the check to catch all of them. A matcher that has quietly stopped matching
    # -- an empty name table, a botched ordinal -- passes the green above and fails here.
    new_sorted = sorted(new_cm)
    following = {rva: new_sorted[i + 1] for i, rva in enumerate(new_sorted[:-1])}
    broken = []
    for code, src, dst, const, note, _ in results:
        if code in ("PROVEN", "ORDINAL") and dst in following:
            broken.append((src, following[dst], const, note))
    if not broken:
        print("FAIL: could not build a negative control -- nothing to regress")
        return 1
    bad_tally, _ = run(broken, old_cm, new_cm, quiet=True)
    if bad_tally["DISAGREE"] != len(broken):
        print(
            f"FAIL: negative control leaked -- {len(broken)} wrong destinations, "
            f"only {bad_tally['DISAGREE']} caught"
        )
        return 1
    print(f"  negative control: {len(broken)}/{len(broken)} shifted destinations rejected")
    print("selftest OK")
    return 0


def prove_selftest_catches_regression(old_cm, new_cm):
    """Break the matcher on purpose and require --selftest to FAIL.

    A green selftest only means something if a broken instrument would go red. This
    monkey-patches `judge` into a matcher that waves every vtable row through, re-runs the
    WHOLE selftest, and fails if that still passes. Eight instruments in this repo were caught
    reporting false greens on 2026-08-30, several because a filter matched nothing and an
    assertion then passed over the empty set; this is the check that would have caught them.
    """
    global judge
    real = judge

    def always_agree(src, dst, old_cm_, new_cm_, old_ord, new_key, old_names, new_names):
        return ("PROVEN", "REGRESSED-MATCHER") if src in old_cm_ else ("N/A", "not a vtable")

    judge = always_agree
    try:
        code = selftest(old_cm, new_cm)
    finally:
        judge = real
    if code == 0:
        print("FAIL: the selftest passed with a matcher that agrees with everything -- it is vacuous")
        return 1
    print("regression proof OK: a matcher that always agrees fails the selftest")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=DATA_MAP)
    ap.add_argument("--old", default=OLD_IMAGE)
    ap.add_argument("--new", default=NEW_IMAGE)
    ap.add_argument("--deltas", action="store_true", help="whole-.rdata RTTI delta census")
    ap.add_argument(
        "--bracket",
        action="store_true",
        help="fence the NON-vtable rows with RTTI vtable anchors (independent of the data map)",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--prove-selftest-catches-regression",
        action="store_true",
        help="break the matcher on purpose; the selftest must go red",
    )
    args = ap.parse_args()

    # The two de-Arxan'd images are gitignored (copyrighted), so a fresh checkout and CI simply do
    # not have them. SKIP at exit 0 rather than crash: a gate that cannot run must not be
    # indistinguishable from a gate that ran and failed.
    for path in (args.old, args.new, args.map):
        if not os.path.isfile(path):
            print(f"SKIP: missing {path}")
            return 0

    old_cm = classmap(args.old, "/tmp/er-rtti-1162.tsv")
    new_cm = classmap(args.new, "/tmp/er-rtti-1170.tsv")
    print(f"1.16.2: {len(old_cm)} vtables    1.17: {len(new_cm)} vtables")

    if args.prove_selftest_catches_regression:
        return prove_selftest_catches_regression(old_cm, new_cm)

    if args.selftest:
        return selftest(old_cm, new_cm)

    rows = read_rows(args.map)
    tally, _ = run(rows, old_cm, new_cm)
    print(f"\n{dict(tally)}")

    if args.bracket:
        print("\nRTTI-anchor bracket over non-vtable rows:")
        counts = collections.Counter()
        for code, src, dst, const, note, detail in bracket(rows, old_cm, new_cm):
            counts[code] += 1
            if code != "NO-ANCHOR":
                print(f"  {code:9s} 0x{src:x} -> 0x{dst:x}  {const}  [{note}]  {detail}")
        print(f"  {dict(counts)}")

    if args.deltas:
        census, per_region = delta_census(old_cm, new_cm)
        print("\nRTTI-only delta census over uniquely-named vtables:")
        for delta, count in sorted(census.items()):
            print(f"  {delta:+#x}  {count}")
        print("\nper 1 MiB region of 1.16.2 .rdata:")
        for region in sorted(per_region):
            spread = per_region[region]
            shown = ", ".join(f"{d:+#x}x{c}" for d, c in sorted(spread.items()))
            print(f"  0x{region << 20:x}: {shown}")

    return 1 if (tally["DISAGREE"] or tally["MISSING"]) else 0


if __name__ == "__main__":
    sys.exit(main())
