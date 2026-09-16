#!/usr/bin/env python3
"""Dump raw PARAM row bytes from the installed `regulation.bin`, offline.

The question this exists for is "did something rewrite this row in memory": a mod that disables an
item by editing its param row leaves the live bytes and the on-disk bytes disagreeing, and the byte
that differs names the field without anyone having to guess which field it is first. Pair it with a
live dump (`scripts/frida/goods-row-live.js`) and diff the two.

Field NAMES need a paramdef this does not have, and that is fine for a diff: a differing offset is
located by number and looked up afterwards. Row stride comes from the row-entry table, the same way
`diff-regulation-params.py` derives it, so no paramdef is needed to know where a row ends either.

    python3 scripts/dump-param-row-bytes.py EquipParamGoods 102 111 112
    python3 scripts/dump-param-row-bytes.py --bytes 0x60 EquipParamGoods 102

Decrypt/unpack reuses `regulation-params.py`'s four stages (AES-256-CBC -> DCX/zstd -> BND4 ->
PARAM) rather than repeating them, so a format change fails loudly in the one place that owns it.
"""

import argparse
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The row index: a 24-byte entry per row, the id first and the row's data offset eight bytes in.
# Same layout crates/er-invasion-warp-core/src/map_piece.rs parses.
PARAM_ROW_COUNT_OFFSET = 0x0A
PARAM_ROW_INDEX_OFFSET = 0x40
PARAM_ROW_INDEX_STRIDE = 24
PARAM_ROW_DATA_OFFSET = 8


def _regulation_params():
    """Import `regulation-params.py`, whose hyphen makes it unimportable by name."""
    path = os.path.join(HERE, "regulation-params.py")
    spec = importlib.util.spec_from_file_location("regulation_params", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row_table(param):
    """Every row as `(id, data_offset)`, in file order."""
    count = struct.unpack_from("<H", param, PARAM_ROW_COUNT_OFFSET)[0]
    rows = []
    for index in range(count):
        entry = PARAM_ROW_INDEX_OFFSET + index * PARAM_ROW_INDEX_STRIDE
        row_id = struct.unpack_from("<i", param, entry)[0]
        data = struct.unpack_from("<q", param, entry + PARAM_ROW_DATA_OFFSET)[0]
        rows.append((row_id, data))
    return rows


def hexdump(blob, start, length):
    lines = []
    for offset in range(0, length, 16):
        chunk = blob[start + offset : start + offset + 16]
        if not chunk:
            break
        spelled = " ".join(f"{byte:02x}" for byte in chunk)
        lines.append(f"    +0x{offset:02x}  {spelled}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("param", help="param name, with or without the .param suffix")
    parser.add_argument("rows", nargs="+", type=int, help="row ids to dump")
    parser.add_argument(
        "--bytes",
        dest="count",
        default="0x60",
        help="bytes per row to print (default 0x60); accepts hex",
    )
    parser.add_argument("--regulation", default=None)
    args = parser.parse_args()

    rp = _regulation_params()
    regulation = args.regulation or rp.DEFAULT_REGULATION
    count = int(args.count, 0)

    files = rp.bnd4_entries(rp.dcx_unpack(rp.decrypt(regulation)))
    name = args.param if args.param.endswith(".param") else f"{args.param}.param"
    match = next((key for key in files if key.rsplit("\\", 1)[-1] == name), None)
    if match is None:
        print(f"no {name} in {regulation}", file=sys.stderr)
        return 1

    param = files[match]
    table = {row_id: offset for row_id, offset in row_table(param)}
    print(f"{args.param} from {regulation}  rows={len(table)}")
    status = 0
    for row_id in args.rows:
        offset = table.get(row_id)
        if offset is None:
            print(f"row {row_id}: NOT PRESENT")
            status = 1
            continue
        print(f"row {row_id} @ +0x{offset:x}")
        print(hexdump(param, offset, count))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
