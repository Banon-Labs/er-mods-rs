#!/usr/bin/env python3
"""Per-frame orientation of a weapon attack's hit capsules while they are live.

    python3 scripts/er-hitbox-angles.py Giant-Crusher 2h_r1_1
    python3 scripts/er-hitbox-angles.py Greatsword 2h_r1_1 --grip both

Reads the same pose samples `er-mechanics-reach.py` builds its footprint from (60 Hz over each hit
window, root motion included, model space with the attacker facing -Z at animation start) and
prints, for each sample of each damaging capsule:

* `tip az`: bearing of the capsule's far end from the attacker's start position, in degrees from
  straight ahead, positive to the attacker's right. A defender the attacker faced when pressing
  the button stands at 0.
* `axis az`: heading of the capsule axis (hilt end to far end) on the ground plane, same sign.
  0 means the blade points straight at that defender; 90 means it lies across the line to them.
* `pitch`: the capsule axis above (+) or below (-) horizontal.
* `tip h`: height of the far end above the attacker's feet at animation start (m).
* `tip r`: horizontal distance of the far end from the start position (m).

The capsule ends come from `er-mechanics-reach._capsule_points`, so the first and last sampled
points are the capsule's two dummy-poly ends. Real frames apply the clip's play speed.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REACH = _sibling("er-mechanics-reach")


def capture(weapon: str, grip: str) -> dict:
    """{slot: (profile row, [samples])} for every slot with pose samples."""
    runs = []
    orig = REACH.sweep_coverage

    def spy(samples):
        runs.append(samples)
        return orig(samples)

    REACH.sweep_coverage = spy
    try:
        # reach_profile builds its rows in order and calls sweep_coverage once per row that has
        # pose samples, so the captured sets line up with those rows.
        rows = REACH.reach_profile(weapon, grip)
    finally:
        REACH.sweep_coverage = orig
    with_pose = [r for r in rows if r.get("sweep_arc_deg") is not None]
    if len(with_pose) != len(runs):
        raise SystemExit(f"sample sets ({len(runs)}) do not match slots with a footprint ({len(with_pose)})")
    return {r["slot"]: (r, s) for r, s in zip(with_pose, runs)}


def angles(sample: dict) -> dict:
    """The orientation columns of one capsule sample (module docstring)."""
    pts = sample["points"]
    near, far = pts[0], pts[-1]
    if math.hypot(far[0], far[2]) < math.hypot(near[0], near[2]):
        near, far = far, near
    dx, dy, dz = far[0] - near[0], far[1] - near[1], far[2] - near[2]
    return {
        "tip_az": REACH._azimuth(far[0], far[2]),
        "axis_az": REACH._azimuth(dx, dz),
        "pitch": math.degrees(math.atan2(dy, math.hypot(dx, dz))),
        "tip_h": far[1],
        "tip_r": math.hypot(far[0], far[2]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapon")
    ap.add_argument("slot", nargs="?", help="slot key (e.g. 2h_r1_1); default every slot")
    ap.add_argument("--grip", choices=("one", "both"), default="both")
    a = ap.parse_args()
    got = capture(a.weapon, a.grip)
    for slot, (row, samples) in got.items():
        if a.slot and slot != a.slot:
            continue
        print(f"\n{a.weapon} {slot} ({row.get('label')}), window opens real f{row.get('first_hit_frame_real')}, "
              f"swing {row.get('swing_shape')}")
        print(f"  {'clip f':>7} {'shape':>5} {'tip az':>7} {'axis az':>8} {'pitch':>6} {'tip h':>6} {'tip r':>6}")
        for s in sorted(samples, key=lambda s: (s["shape"], s["t"])):
            g = angles(s)
            print(f"  {s['t'] * REACH.TAE_FPS:>7.1f} {s['shape']:>5} {g['tip_az']:>7.1f} {g['axis_az']:>8.1f} "
                  f"{g['pitch']:>6.1f} {g['tip_h']:>6.2f} {g['tip_r']:>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
