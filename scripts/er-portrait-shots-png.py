#!/usr/bin/env python3
"""Turn the raw render-target copies `scripts/frida/portrait-shots.js` writes into cropped PNGs.

Each shot is three files: `<name>.color.bin` and `<name>.depth.bin` (subresource 0 of the profile
renderer's colour and depth targets, rows `*_row_pitch` bytes apart) and `<name>.json` (size,
DXGI formats, the silhouette box the agent measured, the camera it used). The background is keyed
out from depth -- every texel equal to the cleared value in the corner -- so the PNG carries
alpha, and it is cropped to the silhouette with a margin.

    uv run --with numpy --with pillow python3 scripts/er-portrait-shots-png.py ~/.cache/er-portrait-shots/thops
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# DXGI formats the agent accepts for colour: R8G8B8A8 (28, 29 sRGB) and B8G8R8A8 (87, 91 sRGB).
BGRA_FORMATS = {87, 91}
# Fraction of the crop's larger side left around it.
MARGIN = 0.06
# Crops that are not the whole silhouette: metres below the crown, and width over height. The
# portrait stops at mid-chest; the face shots just under the chin.
CROPS = {
    "portrait": (0.55, 0.8),
    "face_three_quarter": (0.30, 0.85),
    "face_profile": (0.30, 0.85),
}
# Rows below the crown that are only head, for centring a crop on it.
HEAD_ROWS_M = 0.20


def rows(raw: np.ndarray, pitch: int, h: int) -> np.ndarray:
    """`h` rows of `pitch` elements. The copy footprint leaves the last row unpadded, so pad it."""
    out = np.zeros(pitch * h, dtype=raw.dtype)
    out[: min(raw.size, out.size)] = raw[: out.size]
    return out.reshape(h, pitch)


def load(stem: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    meta = json.loads(stem.with_suffix(".json").read_text())
    w, h = meta["width"], meta["height"]
    raw = np.frombuffer(stem.with_suffix(".color.bin").read_bytes(), dtype=np.uint8)
    color = rows(raw, meta["color_row_pitch"], h)[:, : w * 4].reshape(h, w, 4)
    if meta["format"] in BGRA_FORMATS:
        color = color[:, :, [2, 1, 0, 3]]
    words = np.frombuffer(stem.with_suffix(".depth.bin").read_bytes(), dtype=np.uint32)
    pitch = meta["depth_row_pitch"] // 4
    depth = rows(words, pitch, h)[:, :w]
    return color, depth, meta


def convert(stem: Path) -> Path:
    color, depth, meta = load(stem)
    mask = depth != depth[0, 0]
    if not mask.any():
        raise SystemExit(f"{stem.name}: the depth target holds nothing but the clear value")
    rgba = color.copy()
    rgba[:, :, 3] = np.where(mask, 255, 0).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    shape = CROPS.get(stem.name)
    if shape is not None:
        # Measured in metres from the top of the head, using the scale the agent framed with: a
        # fixed height below the crown, and a width centred on the head rows only, so shoulders and
        # robes do not widen a face crop.
        depth_m, aspect = shape
        px = meta["px_per_m"]
        bottom = min(top + int(depth_m * px), mask.shape[0] - 1)
        head = np.nonzero(mask[top : top + int(HEAD_ROWS_M * px)].any(axis=0))[0]
        centre = (head.min() + head.max()) // 2
        half = int(aspect * (bottom - top) / 2)
        left, right = centre - half, centre + half
    pad = int(MARGIN * max(bottom - top, right - left))
    box = (max(left - pad, 0), max(top - pad, 0), min(right + pad, mask.shape[1]), min(bottom + pad, mask.shape[0]))
    out = stem.with_suffix(".png")
    Image.fromarray(rgba).crop(box).save(out)
    # The same crop over the render's own background, for places that drop alpha: a Steam
    # screenshot is stored as a JPEG.
    Image.fromarray(color[:, :, :3]).crop(box).save(stem.with_suffix(".jpg"), quality=95)
    print(f"{out}  {box[2] - box[0]}x{box[3] - box[1]}  silhouette {right - left}x{bottom - top}  format {meta['format']}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dir", type=Path, help="a directory the agent wrote shots into")
    args = parser.parse_args()
    stems = sorted({p.with_name(p.name.split(".")[0]) for p in args.dir.glob("*.color.bin")})
    if not stems:
        print(f"no shots in {args.dir}", file=sys.stderr)
        return 1
    for stem in stems:
        convert(stem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
