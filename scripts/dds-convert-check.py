#!/usr/bin/env python3
"""Check mushroom-man-builder's texture conversions against an independent decoder (Pillow).

Usage: dds-convert-check.py --builder <mushroom-man-builder> --out <dir> <character.tpf>...
       dds-convert-check.py --selftest

For every BC3 (`DXT5`, or DX10 `BC3_UNORM[_SRGB]`) texture in each TPF -- the format no Elden
Ring TPF holds, which the builder carries into BC7 for an albedo and BC1 for a specular map -- it
writes the source DDS into <out>, runs `mushroom-man-builder convert-dds <slot>` on it, decodes
both with Pillow and prints the largest and mean difference per channel. The slot comes from the
name: `_n` is a normal map (left out: DXT5nm becomes BC5, which Pillow does not compare channel for
channel), `_s` a specular map (`mask`, colour only), anything else an albedo (colour and alpha).
Exits 1 when a converted colour channel is more than 3 of 255 off anywhere, or a converted alpha
is more than 8 off on average. The TPFs come from `mushroom-man-builder extract-inputs`; nothing is
written into the repo. Needs Pillow: run under `uv run --with pillow`.

`--selftest` needs no game: it checks the TPF and DDS header readers on fabricated bytes.
"""

import argparse
import io
import struct
import subprocess
import sys
from pathlib import Path

DDS_FOURCC = 0x54
DDS_HEADER_END = 0x80
BC3_DXGI = (77, 78)
COLOUR_LIMIT = 3
ALPHA_MEAN_LIMIT = 8.0
# One convert-dds call on one texture; a 2048 square albedo converts in well under a second.
STEP_TIMEOUT_S = 30


def tpf_textures(data):
    """[(name, format byte, dds bytes)] of a TPF: 16-byte header, then 20-byte entries."""
    if data[:4] != b"TPF\0":
        raise ValueError("not a TPF")
    count = struct.unpack_from("<I", data, 8)[0]
    encoding = data[14]
    out = []
    for i in range(count):
        offset, size, fmt = struct.unpack_from("<IIB", data, 16 + 20 * i)
        name_at = struct.unpack_from("<I", data, 16 + 20 * i + 12)[0]
        if encoding == 1:
            end = name_at
            while data[end:end + 2] != b"\0\0":
                end += 2
            name = data[name_at:end].decode("utf-16-le")
        else:
            name = data[name_at:data.index(b"\0", name_at)].decode("ascii")
        out.append((name, fmt, data[offset:offset + size]))
    return out


def is_bc3(dds):
    fourcc = dds[DDS_FOURCC:DDS_FOURCC + 4]
    if fourcc == b"DXT5":
        return True
    return fourcc == b"DX10" and struct.unpack_from("<I", dds, DDS_HEADER_END)[0] in BC3_DXGI


def slot_of(name):
    stem = name.lower()
    if stem.endswith("_n"):
        return "normal"
    if stem.endswith("_s"):
        return "mask"
    return "albedo"


def compare(source_dds, converted_dds, slot):
    """Per channel (max, mean) absolute difference between the two decoded images."""
    from PIL import Image  # imported late: the selftest needs no Pillow

    a = Image.open(io.BytesIO(source_dds)).convert("RGBA")
    b = Image.open(io.BytesIO(converted_dds)).convert("RGBA")
    if a.size != b.size:
        raise ValueError(f"sizes differ: {a.size} and {b.size}")
    channels = "RGBA" if slot == "albedo" else "RGB"
    out = {}
    for channel in channels:
        x = a.getchannel(channel).tobytes()
        y = b.getchannel(channel).tobytes()
        diffs = [abs(p - q) for p, q in zip(x, y)]
        out[channel] = (max(diffs), sum(diffs) / len(diffs))
    return out


def check(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    seen = 0
    for tpf_path in args.tpfs:
        for name, fmt, dds in tpf_textures(Path(tpf_path).read_bytes()):
            if not is_bc3(dds):
                continue
            slot = slot_of(name)
            if slot == "normal":
                print(f"{name}: a normal map, left out")
                continue
            seen += 1
            source = out_dir / f"{name}.dds"
            converted = out_dir / f"{name}.{slot}.dds"
            source.write_bytes(dds)
            run = subprocess.run([args.builder, "convert-dds", slot, str(source), str(converted)],
                                 capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
            if run.returncode != 0:
                print(f"{name} ({slot}): convert-dds failed: {run.stderr.strip()}")
                failed += 1
                continue
            converted_dds = converted.read_bytes()
            diffs = compare(dds, converted_dds, slot)
            bad = [c for c, (worst, _) in diffs.items() if c != "A" and worst > COLOUR_LIMIT]
            if "A" in diffs and diffs["A"][1] > ALPHA_MEAN_LIMIT:
                bad.append("A")
            failed += bool(bad)
            fourcc = converted_dds[DDS_FOURCC:DDS_FOURCC + 4].decode("ascii", "replace")
            print(f"{name} ({slot}, TPF format {fmt} -> {fourcc}): "
                  + ", ".join(f"{c} max {w} mean {m:.2f}" for c, (w, m) in diffs.items())
                  + (f"  FAIL {''.join(bad)}" if bad else ""))
    print(f"{seen} BC3 textures compared, {failed} failed")
    return 1 if failed else 0


def selftest():
    ok = True

    def expect(label, cond, got=""):
        nonlocal ok
        print(f"{'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f": {got}"))
        ok &= bool(cond)

    def dds(fourcc, dxgi=None):
        header = bytearray(DDS_HEADER_END)
        header[:4] = b"DDS "
        header[DDS_FOURCC:DDS_FOURCC + 4] = fourcc
        if dxgi is not None:
            header += struct.pack("<5I", dxgi, 3, 0, 1, 0)
        return bytes(header) + bytes(16)

    expect("legacy DXT5 is BC3", is_bc3(dds(b"DXT5")))
    expect("DX10 BC3 sRGB is BC3", is_bc3(dds(b"DX10", 78)))
    expect("DX10 BC7 is not", not is_bc3(dds(b"DX10", 99)))
    expect("DXT1 is not", not is_bc3(dds(b"DXT1")))
    expect("slots", [slot_of(n) for n in ("c3330_n", "C3330_S", "c3330_a", "c3330")]
           == ["normal", "mask", "albedo", "albedo"])

    names = ["c1_a", "c1_s"]
    blobs = [dds(b"DXT5"), dds(b"DXT1")]
    name_bytes = [n.encode("utf-16-le") + b"\0\0" for n in names]
    entries_end = 16 + 20 * len(names)
    name_at = [entries_end, entries_end + len(name_bytes[0])]
    data_at = entries_end + sum(map(len, name_bytes))
    tpf = bytearray(b"TPF\0" + struct.pack("<II", sum(map(len, blobs)), len(names)) + bytes([0, 3, 1, 0]))
    for i, blob in enumerate(blobs):
        tpf += struct.pack("<IIBBBBII", data_at, len(blob), [5, 0][i], 0, 1, 0, name_at[i], 0)
        data_at += len(blob)
    tpf += b"".join(name_bytes) + b"".join(blobs)
    read = tpf_textures(bytes(tpf))
    expect("TPF names and formats", [(n, f) for n, f, _ in read] == [("c1_a", 5), ("c1_s", 0)], read)
    expect("TPF payloads", [d for _, _, d in read] == blobs)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tpfs", nargs="*")
    ap.add_argument("--builder")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.builder and args.out and args.tpfs):
        ap.error("--builder, --out and at least one TPF are needed")
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
