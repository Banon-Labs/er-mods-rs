#!/usr/bin/env python3
"""Compile er-cel-shade's HLSL into the DXIL blobs the DLL embeds, or check they are current.

The DLL embeds `crates/er-cel-shade/shaders/compiled/*.dxil` with `include_bytes!`, so building
it needs no shader compiler. Those blobs are compiled here from the HLSL beside them, with dxc's
own validator signing each container -- a native D3D12 runtime refuses unsigned DXIL, while
vkd3d-proton accepts either, so an unsigned blob would pass every Linux run and fail on Windows.

No game bytes are committed: the root signature the original passes embed is not attached,
because every target PSO is created with an explicit `pRootSignature` and that one wins (the DLL
logs it per swap).

Usage:
    python3 scripts/build-cel-shaders.py            # compile and write the blobs
    python3 scripts/build-cel-shaders.py --check    # recompile to a temp dir and compare
    python3 scripts/build-cel-shaders.py --selftest

dxc is found at `$DXC`, else `~/tools/dxc/bin/dxc` (the layout `er-shaderlab setup` installs). With
no dxc, `--check` skips with exit 0, the same line `check-game-version-supported.py` draws for an
absent game.
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADERS = REPO / "crates" / "er-cel-shade" / "shaders"
COMPILED = SHADERS / "compiled"
TIMEOUT_SECONDS = 20

# The validator version stamped into each container's `dx.valver`. The game's own passes carry 1.6
# (`dxc -dumpbin` on the dumped originals) and it ships no Agility SDK, so on Windows the system's
# D3D12 runtime is the one that reads these blobs. Matching the game keeps ours inside what that
# runtime already accepts from the shaders they replace; dxc 1.9 would otherwise stamp 1.9.
VALIDATOR_VERSION = "1.6"

# The look is not compiled in: every shader reads it from the `CelLook` constant buffer
# (`shaders/cel_look.hlsli`), which the in-game menu fills. The only defines left pick which of
# the game's two variants of a pass a source compiles to.
#
# output name -> (source, defines).
TARGETS = {
    "light_deferred_ao.dxil": ("light_deferred_ao.hlsl", []),
    "light_deferred_ao_ssr.dxil": ("light_deferred_ao.hlsl", ["CEL_SSR"]),
    "light_cmb_albedo.dxil": ("light_cmb_albedo.hlsl", []),
    "tonemap.dxil": ("tonemap.hlsl", []),
    "tonemap_lut.dxil": ("tonemap.hlsl", ["CEL_LUT"]),
}


def find_dxc() -> Path | None:
    candidates = [os.environ.get("DXC"), str(Path.home() / "tools" / "dxc" / "bin" / "dxc")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def container_is_signed(blob: bytes) -> bool:
    """A DXContainer's 16-byte digest after the `DXBC` magic is all zero until a validator signs it."""
    return blob[:4] == b"DXBC" and blob[4:20] != bytes(16)


def container_parts(blob: bytes) -> list[str]:
    count = struct.unpack_from("<I", blob, 28)[0]
    offsets = struct.unpack_from(f"<{count}I", blob, 32)
    return [blob[offset:offset + 4].decode() for offset in offsets]


def compile_all(dxc: Path, out_dir: Path) -> dict[str, bytes]:
    out_dir.mkdir(parents=True, exist_ok=True)
    blobs = {}
    for name, (source, defines) in TARGETS.items():
        out = out_dir / name
        argv = [str(dxc), "-T", "ps_6_0", "-E", "ps_main", "-O3", "-Qstrip_debug",
                "-validator-version", VALIDATOR_VERSION]
        for define in defines:
            argv += ["-D", define]
        argv += [str(SHADERS / source), "-Fo", str(out)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if proc.returncode != 0:
            raise SystemExit(f"dxc failed for {name}:\n{proc.stderr}")
        blob = out.read_bytes()
        if not container_is_signed(blob):
            raise SystemExit(f"{name}: dxc produced an unsigned container; is libdxil.so beside dxc?")
        if "RTS0" in container_parts(blob):
            raise SystemExit(f"{name}: carries a root signature part, which this build must not embed")
        blobs[name] = blob
    return blobs


def selftest() -> int:
    failures = []
    # magic, digest, version (u16 major, u16 minor), total size, part count, offsets, parts.
    unsigned = b"DXBC" + bytes(16) + struct.pack("<HHII", 1, 0, 40, 1) + struct.pack("<I", 36) + b"DXIL"
    signed = b"DXBC" + b"\x01" * 16 + unsigned[20:]
    if container_is_signed(unsigned):
        failures.append("an all-zero digest must read as unsigned")
    if not container_is_signed(signed):
        failures.append("a non-zero digest must read as signed")
    if container_parts(unsigned) != ["DXIL"]:
        failures.append("the part table is read from offset 28")
    for name, (source, _) in TARGETS.items():
        if not (SHADERS / source).is_file():
            failures.append(f"{name}: source {source} is missing")
    for failure in failures:
        print(f"selftest FAIL: {failure}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed blobs are stale")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    dxc = find_dxc()
    if dxc is None:
        if args.check:
            print("build-cel-shaders: no dxc ($DXC or ~/tools/dxc/bin/dxc); skipping the staleness check")
            return 0
        print("build-cel-shaders: no dxc found; set $DXC", file=sys.stderr)
        return 1
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = compile_all(dxc, Path(tmp))
        stale = [name for name, blob in fresh.items()
                 if not (COMPILED / name).is_file() or (COMPILED / name).read_bytes() != blob]
        if stale:
            print(f"build-cel-shaders: stale -> {', '.join(stale)}; run "
                  f"python3 {REPO}/scripts/build-cel-shaders.py", file=sys.stderr)
            return 1
        print(f"build-cel-shaders: {len(fresh)} blob(s) current")
        return 0
    for name, blob in compile_all(dxc, COMPILED).items():
        print(f"{COMPILED / name}  {len(blob)} bytes  signed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
