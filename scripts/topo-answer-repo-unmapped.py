#!/usr/bin/env python3
"""Ask the call-graph topology map about the 1.16.2 addresses this workspace still cannot map.

Collects every `*_RVA`-shaped constant declared under `crates/`, subtracts the ledgers that
already answer one (`rva-map-1162-to-1170.functions.tsv`, `.verified.tsv`, `.needed.tsv`,
`.tsv`), and reports what the topology pairing says about the remainder -- with the rule and the
tier that carried it, because a LOOSE-tier row measured 12-18% wrong and must never be used.

  python3 scripts/topo-answer-repo-unmapped.py --pairs DIR/topo-pairs.pickle
"""
import argparse
import collections
import os
import pickle
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x140000000
RECON = os.path.join(ROOT, "docs", "recon")
CONST_RE = re.compile(
    r"\b([A-Z0-9_]*RVA[A-Z0-9_]*)\s*:\s*(?:usize|u64|u32)\s*=\s*(0x[0-9a-fA-F_]+)")


def repo_consts():
    out = collections.defaultdict(set)
    for root, dirs, files in os.walk(os.path.join(ROOT, "crates")):
        dirs[:] = [d for d in dirs if d not in ("target", ".git")]
        for f in files:
            if not f.endswith(".rs"):
                continue
            path = os.path.join(root, f)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in CONST_RE.finditer(text):
                out[int(m.group(2).replace("_", ""), 16)].add(
                    (m.group(1), os.path.relpath(path, ROOT)))
    return out


def ledger_rvas():
    have = {}
    for fn, is_va in (("rva-map-1162-to-1170.functions.tsv", False),
                      ("rva-map-1162-to-1170.needed.tsv", False),
                      ("rva-map-1162-to-1170.verified.tsv", True),
                      ("rva-map-1162-to-1170.tsv", True)):
        p = os.path.join(RECON, fn)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].startswith("0x"):
                continue
            if not parts[1].startswith("0x"):
                continue
            a = int(parts[0], 16)
            b = int(parts[1], 16)
            if is_va:
                a -= BASE
                b -= BASE
            have.setdefault(a, (b, fn))
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--tsv", default=None)
    a = ap.parse_args()
    pairs = pickle.load(open(a.pairs, "rb"))
    pair, origin = pairs["pair"], pairs["origin"]
    consts = repo_consts()
    have = ledger_rvas()

    rows = []
    agree = disagree = 0
    for rva in sorted(consts):
        va = rva + BASE
        names = sorted(n for n, _ in consts[rva])
        files = sorted({f for _, f in consts[rva]})
        got = pair.get(va)
        o = origin.get(va, ("-", 0, "-"))
        led = have.get(rva)
        if led:
            if got is not None:
                if got - BASE == led[0]:
                    agree += 1
                else:
                    disagree += 1
                    rows.append((rva, got, o, names, files, "LEDGER-DISAGREE:0x%x(%s)" % led))
            continue
        rows.append((rva, got, o, names, files, "unmapped"))

    newly = [r for r in rows if r[5] == "unmapped" and r[1] is not None]
    still = [r for r in rows if r[5] == "unmapped" and r[1] is None]
    conflict = [r for r in rows if r[5] != "unmapped"]
    print(f"repo *_RVA constants: {len(consts)} distinct addresses")
    print(f"already answered by a ledger: {len(consts) - len(newly) - len(still) - len(conflict)}"
          f"  (topology agrees {agree}, disagrees {disagree})")
    print(f"UNMAPPED and now answered by topology: {len(newly)}")
    print(f"UNMAPPED and still unanswered:         {len(still)}")
    print()
    for rva, got, o, names, files, note in newly:
        print("0x%-9x -> 0x%-11x %-6s tier=%-6s %s   [%s]"
              % (rva + BASE, got, o[0], o[2] if len(o) > 2 else "-",
                 ",".join(names)[:60], files[0]))
    if conflict:
        print("\n--- topology disagrees with an existing ledger row ---")
        for rva, got, o, names, files, note in conflict:
            print("0x%-9x topo 0x%-11x %-6s %-6s  %s   %s"
                  % (rva + BASE, got, o[0], o[2] if len(o) > 2 else "-", note, ",".join(names)[:50]))
    if still:
        print("\n--- still unanswered ---")
        for rva, got, o, names, files, note in still:
            print("0x%-9x  %s   [%s]" % (rva + BASE, ",".join(names)[:60], files[0]))
    if a.tsv:
        with open(a.tsv, "w", encoding="utf-8") as fh:
            fh.write("# 1.16.2 VA\t1.17 VA\trule\ttier\tconstant(s)\tfile\tnote\n")
            for rva, got, o, names, files, note in rows:
                fh.write("0x%x\t%s\t%s\t%s\t%s\t%s\t%s\n"
                         % (rva + BASE, ("0x%x" % got) if got else "-", o[0],
                            o[2] if len(o) > 2 else "-", ",".join(names), files[0], note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
