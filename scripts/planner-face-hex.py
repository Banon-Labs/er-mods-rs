#!/usr/bin/env python3
"""A planner build's appearance as the hex face buffer `lab_face` takes.

    python3 scripts/planner-face-hex.py <build.json> --template <hex>

The planner stores a character's look as `sliders.sliders`; the game keeps it as a 288-byte
`FaceDataBuffer` (magic `FACE`, version 4, size 288, then the payload). This writes the sliders
over a template buffer the same way `er_build_import_core::sliders::encode_into` does, from the
same layout table (crates/er-build-import-core/data/planner-sliders-layout.json): only
`buffer[12..276]` changes, so the template's header and tail survive. The template is any real
face buffer, for example the one scripts/frida/ai-lua/mods/moonrithyll.lua passes to `lab_face`.

`--selftest` checks the encoder against the Rust one's documented shapes without a build file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LAYOUT = REPO / "crates" / "er-build-import-core" / "data" / "planner-sliders-layout.json"

FACE_BUFFER_LEN = 288
PAYLOAD_OFFSET = 12
SLIDER_BYTES = 264
MAGIC = b"FACE"
FACE_MODEL_ID = "faceModelId"
MUSCULATURE_STEP = 100


def number(sliders: dict, key: str) -> int:
    """A key as a number; absent or wrongly shaped reads as zero, true as one (encode_into's rule)."""
    value = sliders.get(key)
    if value is True:
        return 1
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, min(int(value), 0xFFFFFFFF))


def encode_into(sliders: dict, buffer: bytearray, layout: list[dict]) -> None:
    if len(buffer) != FACE_BUFFER_LEN or bytes(buffer[:4]) != MAGIC:
        raise ValueError("template is not a 288-byte FACE buffer")
    base = PAYLOAD_OFFSET
    for field in layout:
        at = base + field["offset"]
        kind = field["type"]
        if kind == "colour":
            channels = sliders.get(field["key"])
            for i in range(3):
                v = channels[i] if isinstance(channels, list) and i < len(channels) else 0
                buffer[at + i] = max(0, min(int(v), 255)) if isinstance(v, (int, float)) else 0
        elif kind == "list":
            if field["key"] == FACE_MODEL_ID:
                musc = 1 if number(sliders, "musculature") != 0 else 0
                value = number(sliders, "boneStructure") + number(sliders, "age") + MUSCULATURE_STEP * musc
            else:
                value = number(sliders, field["key"])
            buffer[at:at + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")
        else:
            buffer[at] = number(sliders, field["key"]) & 0xFF


def load_layout() -> list[dict]:
    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
    end = max(f["offset"] + f["size"] for f in layout)
    if end != SLIDER_BYTES:
        raise ValueError(f"layout covers {end} bytes, expected {SLIDER_BYTES}")
    return layout


def selftest() -> int:
    layout = load_layout()
    template = bytearray(FACE_BUFFER_LEN)
    template[:4] = MAGIC
    template[4:8] = (4).to_bytes(4, "little")
    template[8:12] = FACE_BUFFER_LEN.to_bytes(4, "little")
    template[276:] = b"\xAB" * 12
    out = bytearray(template)
    encode_into({"age": 1, "boneStructure": 40, "musculature": 1, "skinColour": [64, 121, 26]}, out, layout)
    face_model = int.from_bytes(out[PAYLOAD_OFFSET:PAYLOAD_OFFSET + 4], "little")
    skin = next(f for f in layout if f["key"] == "skinColour")
    skin_at = PAYLOAD_OFFSET + skin["offset"]
    checks = {
        "faceModelId recomposes 141": face_model == 141,
        "colour written": list(out[skin_at:skin_at + 3]) == [64, 121, 26],
        "header kept": out[:12] == template[:12],
        "tail kept": out[276:] == template[276:],
    }
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", nargs="?", help="a planner build document (JSON, as GET /inventories/<id> returns it)")
    ap.add_argument("--template", help="hex of a real 288-byte face buffer")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.build or not args.template:
        ap.error("build and --template are required")
    doc = json.loads(pathlib.Path(args.build).read_text(encoding="utf-8"))
    sliders = (doc.get("sliders") or {}).get("sliders")
    if not isinstance(sliders, dict):
        print("build has no sliders.sliders", file=sys.stderr)
        return 1
    buffer = bytearray(bytes.fromhex(args.template))
    encode_into(sliders, buffer, load_layout())
    print(buffer.hex().upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
