"""Summarise a vs-patch sweep: per-path survival, per-material pass agreement, velocity outputs.

Research prototype for the shader-editor vertex question. Reads, from one sweep directory:
`index.tsv` (hash, kind, size, debug name), `results*.jsonl` (sweep_one.sh lines),
`pos2_orig.tsv`, `pos2_wobble.tsv` and `pos2_lift.tsv` (pos_slice.py batch output), `outputs_orig.tsv`
(velo_outputs.py). Prints counts only.

Usage: summarize.py <sweep dir>
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

PASS = re.compile(
    r"^(?P<base>.*?)_(?P<pass>Gbuf|GBuf|Dpt|DptA|Velo|Fwd|Pnt|PntCol|FurDir|Spec)"
    r"(?P<skin>_Skin)?(?:_FT)?(?:@\[cl\])?\.vpo\.pdb$")


def container_ok(c: dict | None, allow_editor_cbv: bool) -> bool:
    if not c:
        return False
    base = (c["signed"] and c["model"] and c["ISG1_exact"] and c["OSG1_exact"] and c["interp"]
            and c["sfi0_subset"])
    if allow_editor_cbv:
        return base and all(tuple(b[:4]) == (2, 42, 0, 0) for b in c["binds_extra"])
    return base and c["binds_subset"]


def main() -> int:
    root = Path(sys.argv[1])
    names = {}
    for line in (root / "index.tsv").read_text().splitlines():
        h, kind, size, name = line.split("\t")
        if kind == "1":
            names[h.removesuffix(".dxil")] = name
    rows = {}
    for path in sorted(root.glob("results*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows[r["hash"]] = r

    def tsv(name: str) -> dict[str, str]:
        out = {}
        for line in (root / name).read_text().splitlines():
            path, value = line.split("\t", 1)
            out[Path(path).parent.name] = value
        return out

    pos_o, pos_w, outs = tsv("pos2_orig.tsv"), tsv("pos2_wobble.tsv"), tsv("outputs_orig.tsv")
    pos_l = tsv("pos2_lift.tsv")
    # The sweep's own per-shader `pos_same` came from a pos_slice.py that overflowed the
    # recursion limit on long chains; recompute it from the batch hashes.
    for h, r in rows.items():
        if r.get("lift"):
            r["lift"]["pos_same"] = h in pos_l and pos_l[h] == pos_o.get(h) and not pos_l[
                h].startswith(("none", "error"))
    n = len(rows)
    print(f"vertex shaders with results: {n} of {len(names)}")
    print("path a, DXIL patch:")
    print("  round trip assembled:", sum(r["rt_asm"] == 0 for r in rows.values()),
          " IR text identical after round trip:", sum(r["rt_ir_same"] for r in rows.values()),
          " container checks:", sum(container_ok(r["rt"], False) for r in rows.values()))
    print("  wobble patch applies:", sum(r["patch_rc"] == 0 for r in rows.values()),
          " assembled+signed:", sum(r["w_asm"] == 0 for r in rows.values()),
          " dxv:", sum(r["w_dxv"] == 0 for r in rows.values()),
          " container checks (binds = original + b0 space42):",
          sum(container_ok(r["w"], True) for r in rows.values()))
    reasons = collections.Counter(r["patch_err"].split(":", 1)[-1].strip()[:60]
                                  for r in rows.values() if r["patch_rc"] != 0)
    print("  not applied:", dict(reasons))
    for h in sorted(h for h, r in rows.items() if r["patch_rc"] != 0)[:40]:
        print("    ", h, names.get(h, "?"))
    lifts = [r["lift"] for r in rows.values() if r.get("lift")]
    print("path b, lift DXIL -> SPIR-V -> HLSL -> DXIL:")
    print("  dxil-spirv:", sum(x["spv"] == 0 for x in lifts),
          " spirv-cross:", sum(x["sc"] == 0 for x in lifts),
          " semantics restored:", sum(x["fix"] == 0 for x in lifts),
          " dxc:", sum(x["cc"] == 0 for x in lifts), " dxv:", sum(x["dxv"] == 0 for x in lifts))
    print("  container checks:", sum(container_ok(x["cmp"], False) for x in lifts),
          " OSG1 exact:", sum(bool(x["cmp"]) and x["cmp"]["OSG1_exact"] for x in lifts),
          " position expression identical:", sum(x["pos_same"] for x in lifts),
          " both:", sum(container_ok(x["cmp"], False) and x["pos_same"] for x in lifts))
    print("  wobble variant compiles:", sum(x["wob_cc"] == 0 for x in lifts),
          " wobble container checks:", sum(container_ok(x["wob"], True) for x in lifts))
    stage = collections.Counter()
    for x in lifts:
        if x["fix"] != 0:
            stage["fix: " + re.sub(r"\d+", "N", x["err"].strip())[:60]] += 1
        elif x["cc"] != 0:
            stage["dxc: " + re.sub(r"\d+", "N", x["err"].strip().split("\n")[0])[:70]] += 1
        elif not x["cmp"]["OSG1_exact"]:
            stage["OSG1 differs"] += 1
        elif not container_ok(x["cmp"], False):
            stage["other container check"] += 1
        elif not x["pos_same"]:
            stage["position expression differs"] += 1
    print("  losses by first failing step:", dict(stage.most_common()))
    ms = [x["dxc_ms"] for x in lifts if x["cc"] == 0]
    if ms:
        ms.sort()
        print(f"  dxc process wall ms p50 {ms[len(ms)//2]} p95 {ms[int(len(ms)*0.95)]}")
    rt = sorted(r["rt_ms"] for r in rows.values())
    print(f"  (a) round trip process wall ms p50 {rt[len(rt)//2]} p95 {rt[int(len(rt)*0.95)]}")

    groups = collections.defaultdict(list)
    unmatched = 0
    for h, name in names.items():
        m = PASS.match(name)
        if not m:
            unmatched += 1
            continue
        groups[(m.group("base"), bool(m.group("skin")))].append((m.group("pass"), h))
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"materials (base, skinned): {len(groups)}, with two or more pass VS: {len(multi)}, "
          f"names without a pass suffix: {unmatched}")
    agree_o = agree_w = agree_o_main = agree_w_main = main_groups = 0
    split_passes = collections.Counter()
    for (base, skin), members in multi.items():
        ho = {pos_o.get(h) for _, h in members}
        hw = {pos_w.get(h) for _, h in members}
        agree_o += len(ho) == 1
        agree_w += len(hw) == 1 and None not in hw and not any(
            v.startswith("error") or v == "none" for v in hw if v)
        main = [(p, h) for p, h in members if p in ("Gbuf", "GBuf", "Dpt", "DptA", "Fwd", "Velo")]
        if len(main) > 1:
            main_groups += 1
            mo = {pos_o.get(h) for _, h in main}
            mw = {pos_w.get(h) for _, h in main}
            agree_o_main += len(mo) == 1
            agree_w_main += len(mw) == 1
            if len(mo) > 1:
                ref = collections.Counter(pos_o.get(h) for _, h in main).most_common(1)[0][0]
                for p, h in main:
                    if pos_o.get(h) != ref:
                        split_passes[p] += 1
    print(f"  all passes share one position expression: original {agree_o}/{len(multi)}, "
          f"patched {agree_w}/{len(multi)}")
    print(f"  main-view passes only (Gbuf/Dpt/DptA/Fwd/Velo): original {agree_o_main}/"
          f"{main_groups}, patched {agree_w_main}/{main_groups}; passes that differ from their "
          f"group: {dict(split_passes)}")
    patched_breaks = sum(
        1 for members in multi.values()
        if len({pos_o.get(h) for _, h in members}) == 1
        and len({pos_w.get(h) for _, h in members}) != 1)
    print(f"  groups the patch split (agreed before, differ after): {patched_breaks}")

    velo = [h for h, name in names.items() if "_Velo" in name]
    shapes = collections.Counter()
    for h in velo:
        cells = outs.get(h, "")
        prev_only = [c for c in cells.split() if c.endswith(":-P")]
        both = [c for c in cells.split() if c.endswith(":CP")]
        shapes[(len(prev_only), len(both))] += 1
    print(f"velocity-pass VS: {len(velo)}; (outputs reading only the previous view-projection, "
          f"outputs reading both): {dict(shapes)}")
    with_velo = sum(1 for members in groups.values() if any(p == "Velo" for p, _ in members))
    print(f"materials with a velocity-pass VS in this dump: {with_velo} of {len(groups)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
