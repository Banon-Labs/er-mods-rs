#!/usr/bin/env python3
"""Carry the 1.16.2 Ghidra names onto their paired 1.17 addresses.

The 1.17 runtime dump has no curated names -- every ELDEN RING function in it is `FUN_<addr>`.
Every name, signature and parameter type this workspace has ever recovered lives only in the
1.16.2 project. A confident pair is therefore worth a name.

Four name classes, kept apart because only one of them is worth carrying:

  curated     a real name someone recovered (`GetScadutreeBlessing`, `InitMainHeap`). The payload.
  demangled   a name Ghidra's own demangler/FID produced from the binary (`ceilf`, `parse_digit`).
              1.17 already has its own; carrying them adds nothing and can only conflict.
  funclet     `Unwind@<addr>` / `Catch_All@<addr>`. The name ENCODES a 1.16.2 address, so carrying
              it verbatim onto 1.17 would write a false address into a symbol. Re-stamped with
              the 1.17 address instead, or skipped.
  auto        `FUN_*` / `thunk_FUN_*`. Nothing to carry.

Confidence is carried with the name, not stripped off it: each row keeps the rule, the shape tier
and whether the two bodies were byte-equal, so a consumer can take only the tier it trusts.

  python3 scripts/carry-names-1162-to-1170.py --pairs DIR/topo-pairs.pickle \
      --funcs-1162 funcs-1162.tsv --funcs-1170 funcs-1170.tsv --out DIR/names-1170.tsv
"""
import argparse
import collections
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classify(name):
    if name.startswith("FUN_") or name.startswith("thunk_FUN_") or name.startswith("SUB_"):
        return "auto"
    if name.startswith("Unwind@") or name.startswith("Catch_All@") or name.startswith("Catch@"):
        return "funclet"
    if name.startswith("FID_conflict:"):
        return "demangled"
    return "curated"


def load(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            out[int(p[0], 16)] = p[2]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--funcs-1162", required=True)
    ap.add_argument("--funcs-1170", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--signatures", action="store_true",
                    help="also fetch each curated function's signature from the 1.16.2 daemon")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    P = pickle.load(open(a.pairs, "rb"))
    pair, origin = P["pair"], P["origin"]
    n162 = load(a.funcs_1162)
    n170 = load(a.funcs_1170)

    sig = {}
    if a.signatures:
        sys.path.insert(0, os.path.join(ROOT, "scripts", "ghidra"))
        from mcp_query import query  # noqa: E402
        todo = [va for va, nm in n162.items()
                if classify(nm) == "curated" and va in pair]
        for i, va in enumerate(todo):
            try:
                r = query("getFunctionByAddress", {"address": "%x" % va},
                          port=a.port, timeout=30)
                res = r.get("result") or {}
                sig[va] = res.get("signature", "")
            except Exception as exc:
                print(f"signature fetch failed at 0x{va:x}: {exc}", file=sys.stderr)
            if i % 2000 == 0:
                print(f"  signatures {i}/{len(todo)}", flush=True)

    tally = collections.Counter()
    covered = collections.Counter()
    rows = []
    for va, nm in sorted(n162.items()):
        k = classify(nm)
        tally[k] += 1
        b = pair.get(va)
        if b is None:
            continue
        covered[k] += 1
        if k == "auto":
            continue
        target_now = n170.get(b, "")
        if k == "funclet":
            carried = nm.split("@")[0] + "@%x" % b
        else:
            carried = nm
        o = origin.get(va, ("SEED", 0, "-", "-"))
        rows.append((b, carried, va, nm, o[0], o[2] if len(o) > 2 else "-",
                     o[3] if len(o) > 3 else "-", target_now, sig.get(va, "")))

    print("1.16.2 name classes, and how many are paired onto a 1.17 address:")
    for k in ("curated", "demangled", "funclet", "auto"):
        print(f"  {k:10s} {tally[k]:7d}  paired {covered[k]:7d} "
              f"({100.0*covered[k]/max(1,tally[k]):.1f}%)")
    cur = [r for r in rows if classify(r[3]) == "curated"]
    already = sum(1 for r in cur if classify(r[7]) == "curated")
    print(f"\ncurated names carried onto 1.17: {len(cur)}"
          f"   (of which 1.17 already had a curated name: {already})")
    by = collections.Counter((r[4], r[5], r[6]) for r in cur)
    for k, v in by.most_common():
        print(f"  {k}: {v}")

    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("# 1.17 VA\tcarried name\t1.16.2 VA\t1.16.2 name\trule\tshape tier\tbyte"
                 "\t1.17 name today\t1.16.2 signature\n")
        for r in sorted(rows):
            fh.write("0x%x\t%s\t0x%x\t%s\t%s\t%s\t%s\t%s\t%s\n" % r)
    print(f"\nwrote {a.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
