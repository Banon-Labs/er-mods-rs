#!/usr/bin/env python3
"""Generic offline PARAM dumper: decrypt `regulation.bin`, lay rows out with the
ER paramdef, print the field table and/or decoded row values.

Stages 1-4 (AES-256-CBC -> DCX/zstd -> BND4 -> PARAM) are lifted from
`scripts/regulation-params.py` and `scripts/npcparam-fp-stamina-census.py`, which
are validated against this install. This generalises them to any param + any
paramdef so a schema/value question can be answered without Smithbox or dotnet.

    python3 scripts/dump-param-rows.py LockCamParam --fields --ids
    python3 scripts/dump-param-rows.py NpcParam --only hit --row 45000000
    python3 scripts/dump-param-rows.py --list-params

Paramdefs come from the local Smithbox asset tree (override with --paramdef or
$ER_PARAMDEF_DIR). Decoded PARAM blobs are cached under $TMPDIR to keep repeat
queries under the 30s shell cap; artifacts never land in the repo.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from compression import zstd

# SoulsFormats `RegulationKey.EldenRing`.
REGULATION_KEY = "99BFFC366A6BC8C6F5827D093602D676C42892A01C207FB024D3AF4E493FEF99"

DEFAULT_REGULATION = os.path.join(
    os.environ.get(
        "ER_GAME_DIR",
        os.path.expanduser("~/.local/share/Steam/steamapps/common/ELDEN RING/Game"),
    ),
    "regulation.bin",
)

DEFAULT_PARAMDEF_DIR = os.environ.get(
    "ER_PARAMDEF_DIR",
    os.path.expanduser("~/.local/share/smithbox/app/Assets/PARAM/ER/Defs"),
)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "er-param-cache")

TYPES = {
    "s8": (1, "<b"), "u8": (1, "<B"), "dummy8": (1, "<B"),
    "s16": (2, "<h"), "u16": (2, "<H"),
    "s32": (4, "<i"), "u32": (4, "<I"),
    "f32": (4, "<f"), "angle32": (4, "<f"), "f64": (8, "<d"),
}


def decrypt(path):
    """Stage 1: AES-256-CBC, IV = the file's first 16 bytes."""
    raw = open(path, "rb").read()
    iv, ciphertext = raw[:16], raw[16:]
    ciphertext = ciphertext[: len(ciphertext) // 16 * 16]
    with tempfile.TemporaryDirectory() as work:
        enc = os.path.join(work, "ct")
        dec = os.path.join(work, "pt")
        open(enc, "wb").write(ciphertext)
        subprocess.run(
            ["openssl", "enc", "-d", "-aes-256-cbc", "-nopad",
             "-K", REGULATION_KEY, "-iv", iv.hex(), "-in", enc, "-out", dec],
            check=True, timeout=25,
        )
        plain = open(dec, "rb").read()
    if plain[:4] != b"DCX\0":
        raise SystemExit(f"decrypt produced {plain[:8]!r}, not a DCX -- wrong key or file")
    return plain


def dcx_unpack(dcx):
    """Stage 2: DCX header (big-endian) wrapping a zstd payload."""
    uncompressed = struct.unpack_from(">I", dcx, 0x1C)[0]
    compressed = struct.unpack_from(">I", dcx, 0x20)[0]
    data_offset = struct.unpack_from(">I", dcx, 0x14)[0]
    if dcx[0x24:0x28] != b"DCP\0" or dcx[0x28:0x2C] != b"ZSTD":
        raise SystemExit(f"unexpected DCX compression {dcx[0x24:0x2C]!r}")
    out = zstd.decompress(dcx[data_offset:data_offset + compressed])
    if len(out) != uncompressed or out[:4] != b"BND4":
        raise SystemExit(f"DCX payload is {len(out)} bytes of {out[:4]!r}")
    return out


def bnd4_entries(bnd):
    """Stage 3: BND4 directory -> {name: bytes}."""
    count = struct.unpack_from("<i", bnd, 0x0C)[0]
    files = {}
    for index in range(count):
        header = 0x40 + index * 0x24
        size = struct.unpack_from("<q", bnd, header + 0x10)[0]
        data_offset = struct.unpack_from("<I", bnd, header + 0x18)[0]
        name_offset = struct.unpack_from("<I", bnd, header + 0x20)[0]
        end = name_offset
        while bnd[end:end + 2] != b"\x00\x00":
            end += 2
        files[bnd[name_offset:end].decode("utf-16-le")] = bnd[data_offset:data_offset + size]
    return files


def param_rows(param):
    """Stage 4: PARAM row index -> [(id, data_offset)] plus ParamdefDataVersion."""
    row_count = struct.unpack_from("<H", param, 0x0A)[0]
    data_version = struct.unpack_from("<H", param, 0x08)[0]
    rows = []
    for index in range(row_count):
        base = 0x40 + index * 24
        rows.append((
            struct.unpack_from("<i", param, base)[0],
            struct.unpack_from("<q", param, base + 8)[0],
        ))
    return rows, data_version


DEF_RE = re.compile(
    r"^\s*(?P<type>\w+)\s+(?P<name>\w+)\s*"
    r"(?:\[\s*(?P<count>\d+)\s*\])?\s*"
    r"(?::\s*(?P<bits>\d+))?\s*"
    r"(?:=\s*(?P<default>.*))?$"
)


def parse_paramdef(path):
    """ER paramdef -> (fields{name:(offset,type)}, row_size, ordered field tuples).

    Bitfield packing follows SoulsFormats: consecutive `type name:bits` share one
    storage unit while the normalised type matches and the bits still fit.
    """
    root = ET.parse(path).getroot()
    fields, order = {}, []
    offset, bit_offset, bit_type = 0, -1, None
    for field in root.find("Fields"):
        raw = field.get("Def")
        match = DEF_RE.match(raw)
        if not match:
            raise SystemExit(f"unparsed paramdef field: {raw!r}")
        ftype, name = match.group("type"), match.group("name")
        count = int(match.group("count") or 1)
        bits = match.group("bits")
        default = (match.group("default") or "").strip() or None
        display = field.findtext("DisplayName") or ""
        description = field.findtext("Description") or ""
        if ftype in ("fixstr", "fixstrW"):
            width = count * (2 if ftype == "fixstrW" else 1)
            bit_offset = -1
            fields.setdefault(name, (offset, ftype))
            order.append((offset, ftype, name, count, None, 1, display, description, default))
            offset += width
            continue
        if ftype not in TYPES:
            raise SystemExit(f"unknown paramdef type {ftype!r} in {raw!r}")
        width, _fmt = TYPES[ftype]
        if bits is None:
            bit_offset = -1
            fields.setdefault(name, (offset, ftype))
            order.append((offset, ftype, name, count, None, width, display, description, default))
            offset += width * count
        else:
            bits = int(bits)
            normalised = "u8" if ftype == "dummy8" else ftype
            if bit_offset == -1 or bit_type != normalised or bit_offset + bits > width * 8:
                bit_offset, bit_type, unit = 0, normalised, offset
                offset += width
            else:
                unit = offset - width
            fields.setdefault(name, (unit, f"{ftype}:{bits}@{bit_offset}"))
            order.append((unit, ftype, name, count, (bits, bit_offset), width, display, description, default))
            bit_offset += bits
    return fields, offset, order


def read_field(blob, base, offset, ftype, count, bitinfo):
    if ftype in ("fixstr", "fixstrW"):
        return "<str>"
    width, fmt = TYPES[ftype]
    if bitinfo:
        bits, bit_offset = bitinfo
        value = struct.unpack_from(fmt, blob, base + offset)[0]
        return (value >> bit_offset) & ((1 << bits) - 1)
    if count > 1:
        return [struct.unpack_from(fmt, blob, base + offset + i * width)[0] for i in range(count)]
    value = struct.unpack_from(fmt, blob, base + offset)[0]
    return round(value, 4) if ftype in ("f32", "angle32", "f64") else value


def load_param(param_name, regulation):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, param_name + ".param")
    if os.path.exists(cache):
        return open(cache, "rb").read()
    files = bnd4_entries(dcx_unpack(decrypt(regulation)))
    key = next((n for n in files
                if n.rsplit("\\", 1)[-1].removesuffix(".param") == param_name), None)
    if key is None:
        raise SystemExit(f"{param_name} not in regulation ({len(files)} params)")
    open(cache, "wb").write(files[key])
    return files[key]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("param", nargs="?")
    parser.add_argument("--regulation", default=DEFAULT_REGULATION)
    parser.add_argument("--paramdef")
    parser.add_argument("--fields", action="store_true", help="print the field table")
    parser.add_argument("--ids", action="store_true", help="print every row id")
    parser.add_argument("--row", type=int, action="append", default=[])
    parser.add_argument("--only", action="append", default=[],
                        help="regex filter on field name (repeatable)")
    parser.add_argument("--list-params", action="store_true")
    args = parser.parse_args()

    if args.list_params:
        for name in sorted(bnd4_entries(dcx_unpack(decrypt(args.regulation)))):
            print(name)
        return 0
    if not args.param:
        parser.error("param name required")

    paramdef = args.paramdef or os.path.join(DEFAULT_PARAMDEF_DIR, args.param + ".xml")
    param = load_param(args.param, args.regulation)
    rows, data_version = param_rows(param)
    _fields, computed, order = parse_paramdef(paramdef)

    offsets = sorted(offset for _, offset in rows)
    deltas = {}
    for a, b in zip(offsets, offsets[1:]):
        deltas[b - a] = deltas.get(b - a, 0) + 1
    stride = max(deltas, key=deltas.get) if deltas else 0

    print(f"PARAM {args.param}: {len(rows)} rows, ids "
          f"{min(r for r, _ in rows)}..{max(r for r, _ in rows)}, "
          f"ParamdefDataVersion={data_version}")
    print(f"row stride measured={stride}(0x{stride:x}) paramdef={computed}(0x{computed:x}) "
          f"{'AGREE' if stride == computed else 'DISAGREE'}")

    drift = None
    for offset, ftype, name, _c, _bi, width, *_ in order:
        if width == 1 or ftype in ("dummy8", "u8", "s8"):
            continue
        if offset % width:
            drift = (offset, name)
            break
    print("paramdef drift point: " + (f"0x{drift[0]:x} ({drift[1]})" if drift else "none"))

    patterns = [re.compile(p, re.I) for p in args.only]
    selected = [f for f in order if not patterns or any(p.search(f[2]) for p in patterns)]

    if args.fields:
        for offset, ftype, name, count, bitinfo, _w, display, description, default in selected:
            spelled = ftype + (f"[{count}]" if count > 1 else "") + (f":{bitinfo[0]}" if bitinfo else "")
            print(f"  0x{offset:04x} {spelled:<10} {name:<40} def={str(default):<8} "
                  f"{display} | {description}")

    if args.ids:
        print("ROW IDS: " + " ".join(str(r) for r, _ in rows))

    row_index = dict(rows)
    for wanted in args.row:
        base = row_index.get(wanted)
        if base is None:
            print(f"row {wanted} ABSENT")
            continue
        parts = []
        for offset, ftype, name, count, bitinfo, width, *_ in selected:
            if ftype == "dummy8" or offset + width * count > stride:
                continue
            parts.append(f"{name}={read_field(param, base, offset, ftype, count, bitinfo)}")
        print(f"ROW {wanted}: " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
