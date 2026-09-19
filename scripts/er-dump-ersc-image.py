#!/usr/bin/env python3
"""Dump ersc.dll's decrypted image out of the running game, with every unreadable page recorded.

# Why this exists

Seamless ships Themida-packed: 1.6 MB of `.text` against an 11 MB packed `ERSC` section at rva
0x240000. Every static question about its networking -- who calls `JoinLobby`, what decides a
candidate host is acceptable, where the invade action's state machine lives -- dies in compressed
bytes. The packer decrypts in place at load, so the live module holds the real code.

The output is a flat image: file offset == rva, matching how `eldenring-deobf*.bin` is addressed in
this repo, so `scripts/find-deobf-bytes.py` and objdump work on it with `VA = 0x180000000 + offset`.

A manifest beside it names every page that could not be read. That distinguishes a hole from a run
of zeroes, which disassembly cannot.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-dump-ersc-image.py --out vendor-archive/seamless/ersc-2.0.1.runtime.bin
    uv run --with frida python3 scripts/er-dump-ersc-image.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
AGENT = HERE / "frida" / "dump-ersc-image.js"
PAGE = 0x1000
# Read a megabyte per round trip when the pages are good: one RPC per page across 13.8 MB is 3,400
# round trips and the round trip, not the read, is what costs.
CHUNK = 0x100000

_spec = importlib.util.spec_from_file_location("er_frida_watch", HERE / "er-frida-watch.py")
_watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_watch)


def selftest() -> int:
    agent = AGENT.read_text(encoding="utf-8")
    for name in ("info", "page", "chunk"):
        assert f"  {name} (" in agent, f"the agent must export {name}"
    assert "ersc.dll" in agent, "the agent must name the module it dumps"
    print("selftest ok -- dumper exports agree")
    return 0


def dump(script, size: int, out: pathlib.Path) -> dict:
    """Read the image chunk-first, falling back to per-page only where a chunk fails."""
    data = bytearray(size)
    holes: list[tuple[int, int]] = []
    offset = 0
    while offset < size:
        span = min(CHUNK, size - offset)
        blob = script.exports_sync.chunk(offset, span)
        if blob is not None:
            data[offset : offset + span] = blob
            offset += span
            continue
        # The chunk straddles an unmapped page; fall back so one bad page costs one page.
        end = offset + span
        while offset < end:
            page = script.exports_sync.page(offset)
            if page is None:
                holes.append((offset, PAGE))
            else:
                data[offset : offset + PAGE] = page
            offset += PAGE
    out.write_bytes(bytes(data))
    return {"bytes": size, "holes": holes, "hole_pages": len(holes)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="vendor-archive/seamless/ersc.runtime.bin")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    dev = _watch.device()
    try:
        pid = _watch.find_game_bounded(dev)
    except TimeoutError:
        print("the frida server did not answer -- restart it with --force", file=sys.stderr)
        return 2
    if pid is None:
        print("no eldenring.exe in the prefix", file=sys.stderr)
        return 1

    script = dev.attach(pid).create_script(AGENT.read_text(encoding="utf-8"))
    script.load()
    info = script.exports_sync.info()
    if info is None:
        print("ersc.dll is not loaded in that process", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"ersc.dll base={info['base']} size={info['size']:#x} -> {out}", flush=True)
    result = dump(script, int(info["size"]), out)
    manifest = out.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps({"module": info, "dump": result}, indent=1), encoding="utf-8"
    )
    print(
        f"wrote {result['bytes']:#x} bytes, {result['hole_pages']} unreadable page(s); "
        f"manifest {manifest}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
