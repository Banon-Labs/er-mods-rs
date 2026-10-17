#!/usr/bin/env python3
"""Turn Smithbox param CSVs into the shadow-row plan scripts/frida/lod-shadow.js reads.

    er-lod-shadow-plan.py --grass <GrassLodRangeParam.csv> --parts <PartsDrawParam.csv>
                          [--defs <Smithbox Defs dir>] [--out target/lod-shadow/plan.json]

lod-shadow.js never writes a param row. It keeps a private copy of the GrassLodRangeParam (66) and
PartsDrawParam (68) tables, applies this plan to the copy, and hands the copy's rows to the game's
row getters. The plan is every value the CSV names, as `[offset, f32]` per row id; values equal to
vanilla are harmless to write into the copy.

Column mapping (the Nexus "LOD Restored" 2022 CSVs carry `UnkN` headers):
  GrassLodRangeParam  all six columns, 1:1 with the paramdef.
  PartsDrawParam      columns 0-11 lv01_BorderDist..tex_lv01_PlayDist, column 12 skipped (the
                      enableCrossFade bitfield), columns 13-17 drawDist, drawFadeRange,
                      shadowDrawDist, shadowFadeRange, motionBlur_BorderDist; the rest skipped
                      (they pack s8 bytes as u32 and do not line up with the 1.17.1 layout).
"""

import argparse
import csv
import importlib.util
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("offsets", os.path.join(REPO, "scripts", "er-paramdef-offsets.py"))
offsets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offsets)

GRASS_COLUMNS = ["LOD0_range", "LOD0_play", "LOD1_range", "LOD1_play", "LOD2_range", "LOD2_play"]
PARTS_COLUMNS = [
    "lv01_BorderDist", "lv01_PlayDist", "lv12_BorderDist", "lv12_PlayDist", "lv23_BorderDist",
    "lv23_PlayDist", "lv34_BorderDist", "lv34_PlayDist", "lv45_BorderDist", "lv45_PlayDist",
    "tex_lv01_BorderDist", "tex_lv01_PlayDist", "-", "drawDist", "drawFadeRange", "shadowDrawDist",
    "shadowFadeRange", "motionBlur_BorderDist",
]


def plan_for(csv_path, def_path, index, names):
    with open(def_path, encoding="utf-8") as handle:
        allf = list(offsets.fields(handle.read()))
    row_size = allf[-1][0]
    by_name = {name: (off, ty) for off, bit, _bits, ty, name in allf[:-1] if bit is None}
    for n in names:
        if n != "-" and by_name[n][1] != "f32":
            raise SystemExit(f"{n} is {by_name[n][1]}, the plan only carries f32")
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        table = list(csv.reader(handle))
    rows = {}
    for row in table[1:]:
        values = []
        for name, raw in zip(names, row[2:]):
            if name == "-" or raw == "":
                continue
            values.append([by_name[name][0], float(raw)])
        rows[str(int(row[0]))] = values
    return {"index": index, "rowSize": row_size, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grass", required=True)
    parser.add_argument("--parts", required=True)
    parser.add_argument("--defs", default=os.path.expanduser("~/.local/share/smithbox/app/Assets/PARAM/ER/Defs"))
    parser.add_argument("--out", default=os.path.join(REPO, "target", "lod-shadow", "plan.json"))
    args = parser.parse_args()
    plan = {"params": [
        plan_for(args.grass, os.path.join(args.defs, "GrassLodRangeParam.xml"), 66, GRASS_COLUMNS),
        plan_for(args.parts, os.path.join(args.defs, "PartsDrawParam.xml"), 68, PARTS_COLUMNS),
    ]}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(plan, handle)
    for p in plan["params"]:
        n = sum(len(v) for v in p["rows"].values())
        print(f"param {p['index']}: {len(p['rows'])} rows, {n} values, row size {p['rowSize']:#x}")
    print(args.out)


if __name__ == "__main__":
    main()
