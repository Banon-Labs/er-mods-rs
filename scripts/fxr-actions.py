#!/usr/bin/env python3
"""List the action types an Elden Ring FXR (version 5) uses, and say whether it carries a light.

    python3 scripts/fxr-actions.py <f000800023.fxr> [...]
    python3 scripts/fxr-actions.py --id 800023 [--id 812201]     # search the extract corpus
    python3 scripts/fxr-actions.py --selftest

Header (FXR3 layout, version 5): "FXR\\0", u16 0, u16 version, i32 1, i32 id, then
(offset, count) pairs for sections 1..11 from 0x10. Section 6 (pair at 0x38) is the action table,
0x40 bytes per action, the action type as the u16 at +0x00 of each entry. Action types follow the
FXR action enum (EvenTorset/fxr `ActionType`): 609 PointLight, 11000 SpotLight, 600 PointSprite,
603 BillboardEx, 10000 GPUStandardParticle, 10014 RichModel, among others.

The checks are structural: the magic, the version, the id in the header against the file name, and
that the action table ends where section 7 begins. A file that fails any of them is reported,
never guessed at.
"""
import argparse
import collections
import os
import re
import struct
import sys

LIGHTS = {609: "PointLight", 11000: "SpotLight"}
NAMES = {
    600: "PointSprite", 601: "Line", 602: "QuadLine", 603: "BillboardEx",
    604: "MultiTextureBillboardEx", 605: "Model", 606: "Tracer", 607: "Distortion",
    608: "RadialBlur", 609: "PointLight", 10000: "GPUStandardParticle",
    10001: "GPUStandardCorrectParticle", 10002: "LightShaft", 10003: "GPUSparkParticle",
    10008: "GPUSparkCorrectParticle", 10012: "DynamicTracer", 10013: "WaterInteraction",
    10014: "LensFlare", 10015: "RichModel", 11000: "SpotLight",
}
ACTION_SIZE = 0x40
DEFAULT_CORPUS = os.path.join(
    os.environ.get("ER_EXTRACT_ROOT", os.path.expanduser("~/er-extract")),
    "LOOK_HERE_WITCHY_RECURSIVE_20260713", "sharded", "sfx",
)


def parse(blob):
    """(fxr id, Counter of action types) or raise ValueError naming the failed check."""
    if len(blob) < 0x70 or blob[:4] != b"FXR\0":
        raise ValueError("not an FXR")
    version = struct.unpack_from("<H", blob, 6)[0]
    if version != 5:
        raise ValueError("FXR version %d, expected 5" % version)
    fxr_id = struct.unpack_from("<i", blob, 0x0C)[0]
    s6_off, s6_count = struct.unpack_from("<ii", blob, 0x38)
    s7_off = struct.unpack_from("<i", blob, 0x40)[0]
    if s6_count < 0 or s6_off + s6_count * ACTION_SIZE != s7_off:
        raise ValueError("action table 0x%x x %d does not end at section 7 0x%x"
                         % (s6_off, s6_count, s7_off))
    types = collections.Counter(
        struct.unpack_from("<H", blob, s6_off + i * ACTION_SIZE)[0] for i in range(s6_count))
    return fxr_id, types


def find(corpus, fxr_id):
    want = "f%09d.fxr" % fxr_id
    hits = []
    for directory, _, files in os.walk(corpus):
        if want in files:
            hits.append(os.path.join(directory, want))
    return hits


def report(path):
    blob = open(path, "rb").read()
    try:
        fxr_id, types = parse(blob)
    except ValueError as e:
        print("%s: %s" % (path, e))
        return False
    m = re.search(r"f0*(\d+)\.fxr$", path)
    if m and int(m.group(1)) != fxr_id:
        print("%s: header id %d does not match the file name" % (path, fxr_id))
        return False
    lights = {LIGHTS[t]: n for t, n in types.items() if t in LIGHTS}
    named = ", ".join("%s(%d)x%d" % (NAMES.get(t, "?"), t, n) for t, n in sorted(types.items()))
    print("%s: id %d, %d actions, light: %s\n  %s" % (
        path, fxr_id, sum(types.values()), lights or "none", named))
    return True


def selftest():
    head = bytearray(0x70)
    head[:4] = b"FXR\0"
    struct.pack_into("<HHii", head, 4, 0, 5, 1, 800023)
    struct.pack_into("<ii", head, 0x38, 0x70, 2)
    struct.pack_into("<i", head, 0x40, 0x70 + 2 * ACTION_SIZE)
    acts = bytearray(2 * ACTION_SIZE)
    struct.pack_into("<H", acts, 0, 609)
    struct.pack_into("<H", acts, ACTION_SIZE, 603)
    fxr_id, types = parse(bytes(head + acts))
    assert fxr_id == 800023 and types == {609: 1, 603: 1}, (fxr_id, types)
    bad = bytearray(head)
    struct.pack_into("<i", bad, 0x40, 0x70 + 3 * ACTION_SIZE)
    try:
        parse(bytes(bad + acts))
        raise AssertionError("a mis-sized action table must be refused")
    except ValueError:
        pass
    print("selftest: ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--id", type=int, action="append", default=[])
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    paths = list(a.files)
    for fxr_id in a.id:
        hits = find(a.corpus, fxr_id)
        if not hits:
            print("id %d: no f%09d.fxr under %s" % (fxr_id, fxr_id, a.corpus))
        paths += hits
    ok = all([report(p) for p in paths]) if paths else False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
