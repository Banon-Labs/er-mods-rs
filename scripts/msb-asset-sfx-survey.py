#!/usr/bin/env python3
"""Where a placed asset's effect comes from: model SFX param, an MSB SFX region, or neither.

For every MSB placement of the given models this prints the part's EntityID, its
AssetSfxParamRelativeID and UnkT1E (the two shorts at asset type data +0x1C/+0x1E that the game
reads to pick an AssetModelSfxParam row, see below), the AssetModelSfxParam row id that
selection yields, and the SFX regions (MSB Region/SFX, EffectID) within --radius metres of it.

    python3 scripts/msb-asset-sfx-survey.py AEG040_086 AEG030_196 AEG237_101 AEG020_993
    python3 scripts/msb-asset-sfx-survey.py --summary AEG020_993
    python3 scripts/msb-asset-sfx-survey.py --selftest

Row id rule, read out of the 1.16.2 dump: FUN_1406ca0b0 collects the non-negative shorts at
MsbPart type data +0x1C + 2*i, picks one at random, and FUN_1406c4630 returns
  (clamp(variant, 0, 1) + (modelId % 1000000) * 10) * 100 + clamp(relId, 0, 99)
with modelId AEGxxx_yyy -> xxx*1000 + yyy. Variant is the model-load argument; MSB placements
load variant 0, so the row is modelId*1000 + relId (AEG030_196, rel 0 -> 30196000).

Reads the WitchyBND MSB XML corpus ($ER_EXTRACT_ROOT, default
~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/map/mapstudio).
"""
import argparse
import collections
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

DEFAULT_ROOT = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713", "sharded", "map", "mapstudio",
)

TAG = {k: re.compile(r"<%s>([^<]*)</%s>" % (k, k)) for k in (
    "Name", "ModelName", "EntityID", "AssetSfxParamRelativeID", "UnkT1E", "EffectID",
    "StartDisabled")}
POS = re.compile(r"<Position>\s*<X>([^<]+)</X>\s*<Y>([^<]+)</Y>\s*<Z>([^<]+)</Z>", re.S)


def model_number(model):
    m = re.fullmatch(r"AEG(\d{3})_(\d{3})", model.upper())
    return int(m.group(1)) * 1000 + int(m.group(2)) if m else None


def model_sfx_row(model, rel, variant=0):
    """AssetModelSfxParam row id the game computes (FUN_1406c4630), or None for rel < 0."""
    if rel is None or rel < 0:
        return None
    num = model_number(model)
    if num is None:
        return None
    return (min(max(variant, 0), 1) + (num % 1000000) * 10) * 100 + min(max(rel, 0), 99)


def field(text, key):
    m = TAG[key].search(text)
    return m.group(1).strip() if m else None


def position(text):
    m = POS.search(text)
    return tuple(float(v) for v in m.groups()) if m else None


def msb_dirs(root):
    """Every `<msb>-msb-dcx` directory, whether it sits in a `_chunk_*` shard or at the root."""
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if name.endswith("-msb-dcx"):
            yield name[: -len("-msb-dcx")], path
        elif os.path.isdir(path) and name.startswith("_chunk"):
            for sub in sorted(os.listdir(path)):
                if sub.endswith("-msb-dcx"):
                    yield sub[: -len("-msb-dcx")], os.path.join(path, sub)


def scan_msb(job):
    msb, path, models = job
    parts = []
    adir = os.path.join(path, "Part", "Asset")
    if os.path.isdir(adir):
        for fn in os.listdir(adir):
            if not any(fn.upper().startswith(m) for m in models):
                continue
            text = open(os.path.join(adir, fn), encoding="utf-8-sig", errors="replace").read()
            model = (field(text, "ModelName") or "").upper()
            if model not in models:
                continue
            parts.append({
                "msb": msb, "name": field(text, "Name"), "model": model,
                "entity": int(field(text, "EntityID") or 0),
                "rel": int(field(text, "AssetSfxParamRelativeID") or -1),
                "rel2": int(field(text, "UnkT1E") or -1),
                "pos": position(text),
            })
    sfx = []
    if parts:
        sdir = os.path.join(path, "Region", "SFX")
        if os.path.isdir(sdir):
            for fn in os.listdir(sdir):
                text = open(os.path.join(sdir, fn), encoding="utf-8-sig", errors="replace").read()
                sfx.append({
                    "name": field(text, "Name"), "effect": int(field(text, "EffectID") or -1),
                    "entity": int(field(text, "EntityID") or 0),
                    "disabled": field(text, "StartDisabled"), "pos": position(text),
                })
    return msb, parts, sfx


def survey(root, models, radius, workers=16):
    models = {m.upper() for m in models}
    jobs = [(msb, path, models) for msb, path in msb_dirs(root)]
    out = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for msb, parts, sfx in pool.map(scan_msb, jobs):
            for p in parts:
                near = []
                for s in sfx:
                    if p["pos"] and s["pos"]:
                        d = math.dist(p["pos"], s["pos"])
                        if d <= radius:
                            near.append((round(d, 2), s))
                near.sort(key=lambda t: t[0])
                p["near_sfx"] = near
                out.append(p)
    return out


def selftest():
    assert model_number("AEG030_196") == 30196
    assert model_sfx_row("AEG030_196", 0) == 30196000
    assert model_sfx_row("AEG020_993", 30) == 20993030
    assert model_sfx_row("AEG020_993", 30, variant=1) == 20993130
    assert model_sfx_row("AEG237_101", -1) is None
    xml = ("<Asset><Name>AEG1</Name><ModelName>AEG030_196</ModelName><EntityID>12</EntityID>"
           "<Position>\n<X>1</X>\n<Y>2.5</Y>\n<Z>-3</Z></Position>"
           "<AssetSfxParamRelativeID>0</AssetSfxParamRelativeID><UnkT1E>-1</UnkT1E></Asset>")
    assert field(xml, "AssetSfxParamRelativeID") == "0" and field(xml, "UnkT1E") == "-1"
    assert position(xml) == (1.0, 2.5, -3.0)
    print("selftest: ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="*")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--radius", type=float, default=3.0)
    ap.add_argument("--summary", action="store_true", help="counts only")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.models:
        ap.error("no models given")
    if not os.path.isdir(a.root):
        print("no MSB corpus at %s" % a.root)
        return 1
    rows = survey(a.root, a.models, a.radius)
    by_model = collections.defaultdict(list)
    for p in rows:
        by_model[p["model"]].append(p)
    for model in sorted(by_model):
        ps = by_model[model]
        rels = collections.Counter((p["rel"], p["rel2"]) for p in ps)
        effects = collections.Counter(s["effect"] for p in ps for _, s in p["near_sfx"][:1])
        print("%s: %d placements; (rel, rel2) %s; nearest SFX region effect %s; with entity id %d"
              % (model, len(ps), dict(rels), dict(effects), sum(1 for p in ps if p["entity"])))
        if a.summary:
            continue
        for p in sorted(ps, key=lambda p: (p["msb"], p["name"] or "")):
            near = ", ".join("%s eff=%d d=%.2f ent=%d" % (s["name"], s["effect"], d, s["entity"])
                             for d, s in p["near_sfx"][:3])
            print("  %s %s ent=%d rel=%d rel2=%d row=%s pos=%s sfx=[%s]" % (
                p["msb"], p["name"], p["entity"], p["rel"], p["rel2"],
                model_sfx_row(model, p["rel"]), p["pos"], near))
    return 0


if __name__ == "__main__":
    sys.exit(main())
