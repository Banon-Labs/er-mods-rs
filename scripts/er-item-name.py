#!/usr/bin/env python3
"""Ask the game's own message files what a param row is called -- offline.

The exporter names every equipped item by handing its row id to the game's name getter, which is
one exact `MsgRepositoryImp::LookupEntry` into these same FMGs. So "would this id have exported?"
is answerable here, with no game running and nothing to contaminate: an id with no entry is an id
the exporter drops.

    python3 scripts/er-item-name.py WeaponName 16110000 16110200 16110217
    python3 scripts/er-item-name.py --list-fmg
    python3 scripts/er-item-name.py --refresh      # re-read the installed game's archives
    python3 scripts/er-item-name.py --selftest

The corpus is the cache `--refresh` writes, unpacked straight out of the installed game's
`Data*.bdt` by `er-shaderlab extract` (the SoulsFormats bridge under wine, which loads the game's
own Oodle library), so it is the text of the build on disk. The cache records the archives' sizes
and mtimes; `cache_state()` reports it stale once a patch changes them. `ER_MSG_CORPUS_ROOT`
points at another extraction instead.

`game_names(stem)` is what other scripts call: every id the game names, merged base -> dlc01 ->
dlc02, without the `[ERROR]` and dummy placeholders the files carry for unused ids.
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_DIR = os.environ.get(
    "ER_GAME_DIR", os.path.expanduser("~/.local/share/Steam/steamapps/common/ELDEN RING/Game")
)
CACHE = os.path.expanduser(os.environ.get("ER_MSG_CACHE", "~/.cache/er-mods-rs/msg/engus"))
DEFAULT_CORPUS = os.environ.get("ER_MSG_CORPUS_ROOT", CACHE)
SHADERLAB = os.path.join(ROOT, "target", "debug", "er-shaderlab")
# One extraction of an item bundle measured 22.7 s, nearly all of it wine and Oodle start-up.
EXTRACT_TIMEOUT_S = 30

# The DLC message files the game falls back to, in the order `GetWeaponName` and its siblings try
# them: base first, then dlc01, then dlc02.
BND_DIRS = ("item-msgbnd-dcx", "item_dlc01-msgbnd-dcx", "item_dlc02-msgbnd-dcx")
# Archive member prefix the bridge flattens into each extracted file name.
MEMBER_PREFIX = "N__GR_data_INTERROOT_win64_msg_engUS_"


def read_fmg(path):
    """Parse one FMG into {id: text}. Elden Ring ships version 2 with wide (64-bit) offsets."""
    data = open(path, "rb").read()
    big_endian = data[1] != 0
    endian = ">" if big_endian else "<"
    version = data[2]
    if version != 2:
        raise SystemExit(f"{path}: FMG version {version} is not the Elden Ring layout")
    wide = data[8] != 0
    group_count = struct.unpack_from(endian + "i", data, 0x0C)[0]
    string_count = struct.unpack_from(endian + "i", data, 0x10)[0]
    if wide:
        offsets_start = struct.unpack_from(endian + "q", data, 0x18)[0]
        groups_start = 0x28
        group_size = 0x10
        offset_size = 8
        offset_fmt = endian + "q"
    else:
        offsets_start = struct.unpack_from(endian + "i", data, 0x18)[0]
        groups_start = 0x1C
        group_size = 0x0C
        offset_size = 4
        offset_fmt = endian + "i"

    # Each group maps a contiguous id range onto consecutive entries of the string-offset table.
    entries = {}
    for index in range(group_count):
        base = groups_start + index * group_size
        offset_index, first_id, last_id = struct.unpack_from(endian + "iii", data, base)
        for step, row in enumerate(range(first_id, last_id + 1)):
            slot = offset_index + step
            if slot >= string_count:
                continue
            string_offset = struct.unpack_from(
                offset_fmt, data, offsets_start + slot * offset_size
            )[0]
            if string_offset == 0:
                entries[row] = None
                continue
            end = data.index(b"\x00\x00", string_offset)
            while (end - string_offset) % 2:
                end = data.index(b"\x00\x00", end + 1)
            entries[row] = data[string_offset:end].decode("utf-16-le")
    return entries


def load(corpus, stem):
    """Every FMG whose stem matches, across the base and DLC message bundles."""
    tables = []
    for directory in BND_DIRS:
        for suffix in ("", "_dlc01", "_dlc02"):
            path = os.path.join(corpus, directory, f"{stem}{suffix}.fmg")
            if os.path.exists(path):
                tables.append((path, read_fmg(path)))
    return tables


def is_placeholder(text):
    """Ids the game keeps for unused rows: empty, `[ERROR]...`, or a dummy."""
    return not text or text.startswith("[ERROR]") or "dummy" in text.lower()


def game_names(stem, corpus=None):
    """{id: name} for every id the game names in `stem`, later bundles winning."""
    out = {}
    for _, table in load(corpus or DEFAULT_CORPUS, stem):
        out.update({row: text for row, text in table.items() if not is_placeholder(text)})
    return out


def archive_stamp(game_dir=GAME_DIR):
    """Size and mtime of every archive header: a patch that rewrites the text changes them."""
    stamp = {}
    for name in sorted(os.listdir(game_dir)):
        if name.endswith(".bhd"):
            st = os.stat(os.path.join(game_dir, name))
            stamp[name] = [st.st_size, int(st.st_mtime)]
    return stamp


def cache_state(corpus=DEFAULT_CORPUS, game_dir=GAME_DIR):
    """'ok', 'missing' or 'stale' for the cache; an explicit `ER_MSG_CORPUS_ROOT` is taken as ok."""
    if corpus != CACHE:
        return "ok" if os.path.isdir(corpus) else "missing"
    try:
        recorded = json.load(open(os.path.join(corpus, "stamp.json")))
    except (OSError, ValueError):
        return "missing"
    try:
        return "ok" if recorded == archive_stamp(game_dir) else "stale"
    except OSError:
        return "ok"


def refresh(cache=CACHE, game_dir=GAME_DIR):
    """Unpack the three item bundles out of the installed archives into `cache`."""
    if not os.path.exists(SHADERLAB):
        raise SystemExit(f"{SHADERLAB} is not built: cargo build -p er-shaderlab")
    env = dict(os.environ, CARGO_MANIFEST_DIR=os.path.join(ROOT, "tools", "er-shaderlab"),
               ER_GAME_DIR=game_dir)
    parent = os.path.dirname(cache.rstrip("/"))
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="msg-", dir=parent)
    try:
        for directory in BND_DIRS:
            bundle = directory.replace("-msgbnd-dcx", ".msgbnd.dcx")
            out = os.path.join(staging, "raw", directory)
            os.makedirs(out)
            run = subprocess.run([SHADERLAB, "extract", f"/msg/engus/{bundle}", out], env=env,
                                 capture_output=True, text=True, timeout=EXTRACT_TIMEOUT_S)
            if run.returncode != 0:
                raise SystemExit(f"extract {bundle} failed ({run.returncode}):\n{run.stderr[-2000:]}")
            dest = os.path.join(staging, "corpus", directory)
            os.makedirs(dest)
            for name in os.listdir(out):
                if name.startswith(MEMBER_PREFIX) and name.endswith(".fmg"):
                    shutil.move(os.path.join(out, name), os.path.join(dest, name[len(MEMBER_PREFIX):]))
        with open(os.path.join(staging, "corpus", "stamp.json"), "w") as fh:
            json.dump(archive_stamp(game_dir), fh)
        if os.path.isdir(cache):
            shutil.rmtree(cache)
        os.makedirs(os.path.dirname(cache.rstrip("/")), exist_ok=True)
        shutil.move(os.path.join(staging, "corpus"), cache)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return cache


def _fmg_bytes(groups):
    """A version-2 wide FMG holding `groups` ([(first_id, [text or None, ...])]), for the selftest."""
    texts = [t for _, rows in groups for t in rows]
    groups_start, offsets_start = 0x28, 0x28 + 0x10 * len(groups)
    strings_start = offsets_start + 8 * len(texts)
    head = bytearray(b"\x00\x00\x02\x00" + b"\x00" * 4 + b"\x01" + b"\x00" * 3)
    head += struct.pack("<ii", len(groups), len(texts)) + b"\x00" * 4 + struct.pack("<q", offsets_start)
    head += b"\x00" * (groups_start - len(head))
    slot = 0
    for first, rows in groups:
        head += struct.pack("<iiii", slot, first, first + len(rows) - 1, 0)
        slot += len(rows)
    blob, offsets = bytearray(), []
    for text in texts:
        if text is None:
            offsets.append(0)
            continue
        offsets.append(strings_start + len(blob))
        blob += text.encode("utf-16-le") + b"\x00\x00"
    return bytes(head) + b"".join(struct.pack("<q", o) for o in offsets) + bytes(blob)


def selftest():
    corpus = tempfile.mkdtemp(prefix="fmg-selftest-")
    try:
        files = {
            ("item-msgbnd-dcx", "WeaponName"): [(100, ["Dagger", "[ERROR]", None]), (500, ["DLC dummy"])],
            ("item_dlc02-msgbnd-dcx", "WeaponName_dlc02"): [(100, ["Dagger (patched)"]), (900, ["New Blade"])],
        }
        for (directory, stem), groups in files.items():
            os.makedirs(os.path.join(corpus, directory), exist_ok=True)
            with open(os.path.join(corpus, directory, stem + ".fmg"), "wb") as fh:
                fh.write(_fmg_bytes(groups))
        raw = read_fmg(os.path.join(corpus, "item-msgbnd-dcx", "WeaponName.fmg"))
        assert raw == {100: "Dagger", 101: "[ERROR]", 102: None, 500: "DLC dummy"}, raw
        names = game_names("WeaponName", corpus)
        assert names == {100: "Dagger (patched)", 900: "New Blade"}, names
        assert cache_state(corpus) == "ok"
        assert cache_state(os.path.join(corpus, "absent")) == "missing"
    finally:
        shutil.rmtree(corpus)
    print("selftest ok")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fmg", nargs="?", default="WeaponName",
                        help="FMG stem, e.g. WeaponName / ProtectorName / AccessoryName / GoodsName / ArtsName")
    parser.add_argument("ids", nargs="*", type=int)
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument("--list-fmg", action="store_true", help="list the FMGs in the corpus and exit")
    parser.add_argument("--refresh", action="store_true",
                        help="unpack the item text out of the installed game into the cache and exit")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if args.refresh:
        print(f"wrote {refresh()}")
        return 0
    if args.list_fmg:
        for directory in BND_DIRS:
            path = os.path.join(args.corpus, directory)
            if os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    print(f"{directory}/{name}")
        return 0

    tables = load(args.corpus, args.fmg)
    if not tables:
        print(f"no {args.fmg}*.fmg under {args.corpus}", file=sys.stderr)
        return 2
    print(f"{args.fmg}: {sum(len(t) for _, t in tables)} entries across {len(tables)} file(s)")
    status = 0
    for row in args.ids:
        found = [(os.path.basename(path), table[row]) for path, table in tables if row in table]
        if not found:
            print(f"  {row}: NO ENTRY -- the name getter answers null, the exporter drops the slot")
            status = 1
            continue
        for name, text in found:
            print(f"  {row}: {text!r}   ({name})")
    return status


if __name__ == "__main__":
    sys.exit(main())
