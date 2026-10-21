#!/usr/bin/env python3
"""Print the dummy polys of FLVER2 files: reference id, position, parent and attach bone.

An effect the game attaches "at dummy poly N" (AssetModelSfxParam dmypolyId, the EMEVD
asset-following SFX, SpawnOneShotSfx) lands on every dummy whose reference id is N, so this is
the offline check that a model has the dummy a param row names, and where on the model it sits.

    python3 scripts/flver-dummies.py <file.flver> [...]
    python3 scripts/flver-dummies.py --selftest

Layout (SoulsFormats FLVER2 / FLVER.Dummy): a 0x80-byte header with the dummy count at 0x14,
then 0x40-byte dummies. Per dummy: position f32x3 at 0x00, colour at 0x0C, forward f32x3 at
0x10, reference id s16 at 0x1C, parent bone s16 at 0x1E, upward f32x3 at 0x20, attach bone s16
at 0x2C, flag1 u8 at 0x2E, use-upward u8 at 0x2F.
"""
import argparse
import struct
import sys

DUMMY_SIZE = 0x40
HEADER_SIZE = 0x80


def dummies(blob):
    """List of dicts, one per dummy, or None when `blob` is not a little-endian FLVER2."""
    if len(blob) < HEADER_SIZE or blob[:6] != b"FLVER\0" or blob[6:8] != b"L\0":
        return None
    count = struct.unpack_from("<i", blob, 0x14)[0]
    if not 0 <= count <= 65536 or HEADER_SIZE + count * DUMMY_SIZE > len(blob):
        return None
    out = []
    for i in range(count):
        at = HEADER_SIZE + i * DUMMY_SIZE
        pos = struct.unpack_from("<3f", blob, at)
        fwd = struct.unpack_from("<3f", blob, at + 0x10)
        ref, parent = struct.unpack_from("<2h", blob, at + 0x1C)
        up = struct.unpack_from("<3f", blob, at + 0x20)
        attach = struct.unpack_from("<h", blob, at + 0x2C)[0]
        out.append({
            "index": i, "ref": ref, "pos": pos, "forward": fwd, "up": up,
            "parent_bone": parent, "attach_bone": attach,
        })
    return out


def fmt(v):
    return "(" + ", ".join("%.3f" % x for x in v) + ")"


def selftest():
    head = bytearray(HEADER_SIZE)
    head[:8] = b"FLVER\0L\0"
    struct.pack_into("<i", head, 0x14, 2)
    body = bytearray(2 * DUMMY_SIZE)
    struct.pack_into("<3f", body, 0, 0.0, 1.5, 0.25)
    struct.pack_into("<2h", body, 0x1C, 200, 0)
    struct.pack_into("<h", body, 0x2C, -1)
    struct.pack_into("<2h", body, DUMMY_SIZE + 0x1C, 201, -1)
    got = dummies(bytes(head + body))
    assert got is not None and len(got) == 2, got
    assert got[0]["ref"] == 200 and got[1]["ref"] == 201, got
    assert abs(got[0]["pos"][1] - 1.5) < 1e-6, got
    assert dummies(b"NOTFLVER" + bytes(0x100)) is None
    head2 = bytearray(head)
    head2[6:8] = b"B\0"
    assert dummies(bytes(head2 + body)) is None, "big-endian must be refused, not misread"
    print("selftest: ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.files:
        ap.error("no FLVER files given")
    status = 0
    for path in a.files:
        got = dummies(open(path, "rb").read())
        if got is None:
            print("%s: not a little-endian FLVER2" % path)
            status = 1
            continue
        print("%s: %d dummies" % (path, len(got)))
        for d in got:
            print("  #%d ref=%d pos=%s fwd=%s up=%s parent_bone=%d attach_bone=%d" % (
                d["index"], d["ref"], fmt(d["pos"]), fmt(d["forward"]), fmt(d["up"]),
                d["parent_bone"], d["attach_bone"]))
    return status


if __name__ == "__main__":
    sys.exit(main())
