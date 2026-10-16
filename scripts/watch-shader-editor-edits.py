#!/usr/bin/env python3
"""Watch the Shaders panel's edits reach the picture (design 14, P0.2b).

Runs beside a live game and prints one line whenever something the P0.2b proof asks about moves:
the panel's counters in er-cel-shade-telemetry.json (`editor_edit_callbacks`, `editor_pastes`,
`editor_code_saves`, `editor_param_edits`, `editor_ui_panics`), the variant that wrote the probe's
tonemap region, and the mix an inverting layer is drawn at.

The mix is read off the pixels, not the file. A layer computing `lerp(c, 1 - c, a)` leaves every
sample with `post - pre = a * (1 - 2 * pre)`, so each channel far enough from mid-grey yields `a`,
and their median is the slider value the GPU drew. It is printed beside `param.<name>` of the same
layer in project.ershade, which is the value the panel saved, so a slider proof is three lines whose
two numbers agree at three different values.

    python3 scripts/watch-shader-editor-edits.py --layer 1 --param Amount
    python3 scripts/watch-shader-editor-edits.py --selftest

It ends when the game does. Each verdict is printed once, when its condition first holds:

* `slider: pass` -- at three saved values at least `MIN_STEP` apart, the pixels' mix matched the
  saved value within `MIX_TOLERANCE` on a snapshot where the tonemap region advanced. Sorted by the
  saved value, the measured mixes are then sorted too, which is the monotonic relation the design
  asks for.
* `edit: pass` -- edit callbacks above zero, a code save from the panel, and after it a tonemap
  variant id this watcher had not seen.
* `paste: pass` -- the paste counter rose, and the tonemap region then advanced on
  `PASTE_ALIVE_SNAPSHOTS` further snapshots, so the game and the probe both outlived the paste.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import er_run_lib  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_shader_editor_probe", SCRIPTS / "check-shader-editor-probe.py"
)
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
# The checker's dataclasses look their module up by name while the class is being made.
sys.modules[_spec.name] = probe
_spec.loader.exec_module(probe)

# The panel's counters, by their telemetry names (`exports.rs`), and the short names printed.
COUNTERS = (
    ("editor_edit_callbacks", "edits"),
    ("editor_pastes", "pastes"),
    ("editor_code_saves", "code_saves"),
    ("editor_project_saves", "project_saves"),
    ("editor_param_edits", "param_edits"),
    ("editor_stack_edits", "stack_edits"),
    ("editor_ui_panics", "ui_panics"),
)
# A channel this close to mid-grey moves too little under an inversion to say what the mix is.
MIN_LEVER = 0.2
# Channels needed before a mix is reported.
MIN_CHANNELS = 24
# How far the pixels' mix may sit from the saved value and still be the same value.
MIX_TOLERANCE = 0.02
# Saved values closer than this count as one value.
MIN_STEP = 0.05
SLIDER_VALUES = 3
PASTE_ALIVE_SNAPSHOTS = 3
VERDICTS = {"slider", "edit", "paste"}
# One slice of a wait; a write in the directory ends it at once.
WAIT_SLICE_SECONDS = 0.5
REGION = "tonemap"


def fitted_mix(samples) -> tuple[float, float, int] | None:
    """(median mix, median absolute deviation, channels used), or None with too few channels."""
    values = []
    for sample in samples:
        if sample.pre is None or sample.post is None:
            continue
        for before, after in zip(sample.pre, sample.post):
            lever = 1.0 - 2.0 * before
            if abs(lever) >= MIN_LEVER:
                values.append((after - before) / lever)
    if len(values) < MIN_CHANNELS:
        return None
    values.sort()
    median = values[len(values) // 2]
    spread = sorted(abs(value - median) for value in values)[len(values) // 2]
    return median, spread, len(values)


LAYER_HEADER = re.compile(r"^\s*\[\[layer\]\]\s*$")
KEY_VALUE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=\s*(.+?)\s*(?:#.*)?$")


def saved_param(text: str, layer: int, name: str) -> float | None:
    """`param.<name>` of the layer whose `id` is `layer`, as project.ershade holds it."""
    blocks: list[dict[str, str]] = []
    for line in text.splitlines():
        if LAYER_HEADER.match(line):
            blocks.append({})
            continue
        match = KEY_VALUE.match(line)
        if match and blocks:
            blocks[-1][match.group(1)] = match.group(2)
    for block in blocks:
        if block.get("id") == str(layer):
            try:
                return float(block.get(f"param.{name}", ""))
            except ValueError:
                return None
    return None


class Verdicts:
    """The three P0.2b conditions, fed one snapshot at a time."""

    def __init__(self) -> None:
        self.matched: dict[float, float] = {}
        self.seen_variants: set[int] = set()
        self.baseline_saves: int | None = None
        self.saved_after: int | None = None
        self.paste_baseline: int | None = None
        self.paste_alive = 0
        self.paste_seen = False
        self.done: set[str] = set()

    def feed(
        self,
        counters: dict[str, int],
        variant: int | None,
        advanced: bool,
        mix: float | None,
        saved: float | None,
    ) -> list[str]:
        out: list[str] = []
        if advanced and mix is not None and saved is not None and abs(mix - saved) <= MIX_TOLERANCE:
            if all(abs(saved - value) >= MIN_STEP for value in self.matched):
                self.matched[saved] = mix
        if "slider" not in self.done and len(self.matched) >= SLIDER_VALUES:
            pairs = sorted(self.matched.items())
            mixes = [mix for _, mix in pairs]
            if mixes == sorted(mixes):
                self.done.add("slider")
                shown = ", ".join(f"{s:.2f}->{m:.3f}" for s, m in pairs)
                out.append(f"slider: pass saved->drawn [{shown}]")

        saves = counters.get("code_saves", 0)
        if self.baseline_saves is None:
            self.baseline_saves = saves
            if variant is not None:
                self.seen_variants.add(variant)
        elif saves > self.baseline_saves and self.saved_after is None:
            self.saved_after = saves
        if (
            "edit" not in self.done
            and self.saved_after is not None
            and counters.get("edits", 0) > 0
            and variant is not None
            and variant not in self.seen_variants
        ):
            self.done.add("edit")
            out.append(
                f"edit: pass edit_callbacks={counters.get('edits', 0)} "
                f"code_saves={saves} new_variant=0x{variant:08x}"
            )
        if variant is not None and "edit" in self.done:
            self.seen_variants.add(variant)
        elif variant is not None and self.saved_after is None:
            self.seen_variants.add(variant)

        pastes = counters.get("pastes", 0)
        if self.paste_baseline is None:
            self.paste_baseline = pastes
        elif pastes > self.paste_baseline:
            self.paste_seen = True
        if self.paste_seen and "paste" not in self.done and advanced:
            self.paste_alive += 1
            if self.paste_alive >= PASTE_ALIVE_SNAPSHOTS:
                self.done.add("paste")
                out.append(
                    f"paste: pass pastes={pastes} probe advanced on {self.paste_alive} "
                    "snapshots after it"
                )
        return out


def read_document(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--telemetry", type=pathlib.Path)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--project", type=pathlib.Path, help="project.ershade; default beside the game")
    parser.add_argument("--layer", type=int, default=1)
    parser.add_argument("--param", default="Amount")
    parser.add_argument(
        "--until-pass",
        action="store_true",
        help="exit as soon as all three verdicts have passed, instead of when the game does",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    path = probe.resolve_telemetry(args.telemetry, args.artifact_dir)
    project = args.project or er_run_lib.game_dir() / "er-shader-editor" / "project.ershade"
    print(f"telemetry={path} project={project}", flush=True)
    verdicts = Verdicts()
    last_written: int | None = None
    last_frame: int | None = None
    last_line: tuple | None = None
    with er_run_lib.DirectoryWatch(path.parent) as watch:
        while er_run_lib.find_game_pids():
            document = read_document(path)
            written = document.get("written_ms") if isinstance(document, dict) else None
            if written is None or written == last_written:
                probe.block(watch, WAIT_SLICE_SECONDS)
                continue
            last_written = written
            try:
                snapshot = probe.parse_snapshot(document)
            except ValueError as error:
                print(f"unreadable telemetry: {error}", flush=True)
                continue
            raw = document.get("counters") or {}
            counters = {short: int(raw.get(name, 0)) for name, short in COUNTERS}
            region = snapshot.regions.get(REGION)
            variant = region.variant_id if region is not None else None
            frame = region.frame if region is not None else None
            advanced = frame is not None and last_frame is not None and frame != last_frame
            last_frame = frame
            fit = fitted_mix(region.samples) if region is not None and advanced else None
            mix = fit[0] if fit is not None else None
            try:
                saved = saved_param(project.read_text(encoding="utf-8"), args.layer, args.param)
            except OSError:
                saved = None
            line = (
                variant,
                None if mix is None else round(mix, 2),
                saved,
                tuple(counters.values()),
                advanced,
            )
            if line != last_line:
                last_line = line
                drawn = "-" if fit is None else f"{fit[0]:.3f}(mad {fit[1]:.3f}, n={fit[2]})"
                shown_variant = "-" if variant is None else f"0x{variant:08x}"
                counts = " ".join(f"{short}={value}" for short, value in counters.items())
                print(
                    f"present={snapshot.present} {REGION}_variant={shown_variant} "
                    f"advancing={int(advanced)} drawn_mix={drawn} saved_{args.param}={saved} "
                    f"{counts}",
                    flush=True,
                )
            for verdict in verdicts.feed(counters, variant, advanced, mix, saved):
                print(verdict, flush=True)
            if args.until_pass and verdicts.done >= VERDICTS:
                print("all verdicts passed", flush=True)
                return 0
    print("game exited", flush=True)
    return 0 if verdicts.done >= VERDICTS else 1


def selftest() -> int:
    failed = 0

    def check(name: str, ok: bool) -> None:
        nonlocal failed
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
        failed += 0 if ok else 1

    grid = probe.grid_colours(3)

    def samples(mix: float):
        return [
            probe.Sample(
                index=index,
                pre=colour,
                post=tuple(c + mix * (1.0 - 2.0 * c) for c in colour),
                aux=0,
            )
            for index, colour in enumerate(grid)
        ]

    for mix in (0.0, 0.3, 0.75, 1.0):
        fit = fitted_mix(samples(mix))
        check(f"a lerp towards the inverse at {mix} reads {mix}", fit is not None and abs(fit[0] - mix) < 1e-9)
    grey = [probe.Sample(index=i, pre=(0.5, 0.5, 0.5), post=(0.5, 0.5, 0.5), aux=0) for i in range(64)]
    check("mid-grey says nothing about the mix", fitted_mix(grey) is None)

    text = (
        "format = 1\n\n[[layer]]\nid = 2\nparam.Amount = 0.9\n\n"
        "[[layer]]\nid = 1\nname = \"Proof\"\nparam.Amount = 0.35  # saved\n"
    )
    check("the named layer's parameter is read", saved_param(text, 1, "Amount") == 0.35)
    check("another layer's is not", saved_param(text, 2, "Amount") == 0.9)
    check("a missing layer reads None", saved_param(text, 7, "Amount") is None)

    zero = {short: 0 for _, short in COUNTERS}
    v = Verdicts()
    lines: list[str] = []
    lines += v.feed(zero, 0x04000001, True, 0.30, 0.30)
    lines += v.feed(zero, 0x04000001, True, 0.30, 0.30)
    lines += v.feed(zero, 0x04000001, True, 0.61, 0.60)
    check("two values are not a slider proof", not any(line.startswith("slider") for line in lines))
    lines += v.feed(zero, 0x04000001, True, 0.90, 0.90)
    check("three matched values are", any(line.startswith("slider: pass") for line in lines))
    w = Verdicts()
    w.feed(zero, 0x04000001, True, 0.3, 0.3)
    w.feed(zero, 0x04000001, True, 0.5, 0.3)
    w.feed(zero, 0x04000001, False, 0.6, 0.6)
    check("a mismatch or a stalled region does not count", len(w.matched) == 1)

    e = Verdicts()
    out = e.feed(zero, 0x04000002, True, None, None)
    out += e.feed({**zero, "edits": 4}, 0x04000002, True, None, None)
    check("callbacks with no save are not an edit proof", not out)
    out += e.feed({**zero, "edits": 9, "code_saves": 1}, 0x04000002, True, None, None)
    check("a save before its variant is not either", not out)
    out += e.feed({**zero, "edits": 9, "code_saves": 1}, 0x04000003, True, None, None)
    check("a new variant after a save is", any(line.startswith("edit: pass") for line in out))

    p = Verdicts()
    out = p.feed(zero, 1, True, None, None)
    out += p.feed({**zero, "pastes": 1}, 1, True, None, None)
    out += p.feed({**zero, "pastes": 1}, 1, False, None, None)
    out += p.feed({**zero, "pastes": 1}, 1, True, None, None)
    check("two advancing snapshots after a paste are not enough", not out)
    out += p.feed({**zero, "pastes": 1}, 1, True, None, None)
    check("three are", any(line.startswith("paste: pass") for line in out))

    print(f"{'PASS' if not failed else 'FAIL'}: {failed} failure(s)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
