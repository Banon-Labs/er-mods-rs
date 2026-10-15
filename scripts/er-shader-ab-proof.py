#!/usr/bin/env python3
"""Window A/B for the shader editor: the picture with the redirect against the game's own.

# What it proves (design 14, P0.1 items 4 and 5)

The probe proves which pipeline is bound; this proves the pixels on screen change with it. It
loads `scripts/frida/shader-editor-stats.js` into the game, switches the DLL's mode through that
agent's `setMode` RPC, waits for the present counter to advance by `--settle` (10) or more under
the new mode, and photographs the game window with `scripts/capture-er-window.py`, which validates
the exact window and fails closed rather than photograph anything else. Two phases:

* `cel`: mode 0 (normal) against mode 1 (vanilla), alternated `--alternations` (6) times. Passes
  when the smallest difference between a cel and a vanilla photograph is at least 3x the largest
  difference between two photographs of the same mode. Cel is the AO ports' light bands and
  outlines and the stock cel layers (design 17); a look whose only content is a layer needs the
  editor's compiler to have built it, or the phase sees no stage content.
* `fidelity`: mode 2 (ports redirected with the knobs at their off values and every layer off, the
  cel layers among them) against mode 1, one port kind
  at a time, so a failure names its port: the mode value carries the kind's bit in its second
  byte, and the DLL then redirects that kind alone. A kind passes when the largest cross-mode
  difference is at most 1.5x the largest same-mode one: its hand-ported pass then draws what the
  game's own does, to within the scene's own frame-to-frame noise. A kind no bind was redirected
  for while it settled is not drawn in this scene and is skipped, not passed; `--kinds` picks the
  kinds, and by default every kind with a variant is tried.

  The kinds that fail are then marked unfaithful through the DLL's
  `er_shader_editor_set_unfaithful_v1`, which keeps the mark in `er-cel-shade.toml`: outside a
  fidelity test a marked kind is drawn as the game draws it (design 14, P0.1 item 5). The mark
  becomes exactly the kinds that failed among those tested, and kinds not tested keep theirs.
  `--no-mark` reports without marking; `--set-unfaithful <kinds>` sets the mark by hand, an empty
  list clearing it.

Alternating, rather than taking all of one mode and then all of the other, puts the camera's and
the world's drift into the same-mode pairs as much as into the cross-mode ones, so the ratio
compares the mode against the scene's own movement.

A difference is the fraction of pixels whose largest channel difference exceeds `--threshold`
(2 of 255), after each photograph is reduced to `--size` (320x180) with a box filter. Every pair
is computed; the matrix, the smallest cross-mode, the largest same-mode and their ratio are
printed. The photographs are artifacts for the user. This script reads them only as numbers, and
nothing here shows one to anyone.

# What makes a run invalid rather than failed

Each photograph is bracketed by the stats: the mode must read back as the one set, vanilla must be
set exactly in mode 1, and `redirected_binds` must not move while a vanilla photograph is taken and
must move while any other is. A panic-chord press, a hook fault, a cel look with no stage content,
a capture that failed closed, or a scene that moved under a phase (two photographs of one mode
differing in more than a quarter of their pixels) makes the run invalid (exit 2), never a pass. The mode found at
start is restored on every way out, including a `SIGTERM`; the agent holds no hook, so nothing else
is left behind however the run ends. The capture tool switches to the game's workspace and raises
its window for each photograph, so the desktop moves while this runs.

# Usage

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-shader-ab-proof.py                  # both phases
    uv run --with frida python3 scripts/er-shader-ab-proof.py --phases cel
    uv run --with frida python3 scripts/er-shader-ab-proof.py --stats          # one stats read
    uv run --with frida python3 scripts/er-shader-ab-proof.py --set-mode 1     # leave it vanilla
    uv run --with frida python3 scripts/er-shader-ab-proof.py --phases fidelity --kinds tonemap
    uv run --with frida python3 scripts/er-shader-ab-proof.py --set-unfaithful ''   # clear marks
    python3 scripts/er-shader-ab-proof.py --selftest

`--set-mode` waits for the settle presents and prints the stats either side, so P0.1's vanilla step
is `--set-mode 1`, then `scripts/check-shader-editor-probe.py --expect vanilla`, then
`--set-mode 0`. The agent installs no hook, so this session may sit beside a watcher's.

The waits block on an inotify watch of the telemetry file's directory, which the DLL rewrites once
a second; each wake reads the present counter through the RPC. The timeouts bound a game that has
stopped presenting, and are never the synchronisation.

Exit 0 when every phase passes, 1 when one fails, 2 when the run is invalid. A fidelity phase in
which no kind was drawn is invalid.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import math
import pathlib
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

# The inotify watch, the game's directory and the run-state root, shared with the launchers.
import er_run_lib  # noqa: E402

AGENT = SCRIPTS / "frida" / "shader-editor-stats.js"
CAPTURE = SCRIPTS / "capture-er-window.py"
PROBE_CHECKER = SCRIPTS / "check-shader-editor-probe.py"
EVIDENCE_SCRIPT = SCRIPTS / "er-frida-evidence.py"
EXPORTS_RS = REPO / "crates" / "er-cel-shade" / "src" / "exports.rs"
REPLACE_RS = REPO / "crates" / "er-cel-shade" / "src" / "replace.rs"

SERVER_ADDRESS = "127.0.0.1:27042"

MODE_NORMAL = 0
MODE_VANILLA = 1
MODE_FIDELITY = 2
MODE_NAMES = ("normal", "vanilla", "fidelity")
# `safety::MODE_KINDS_SHIFT`: a fidelity mode value carries the kinds it redirects from this bit.
MODE_KINDS_SHIFT = 8

# `PortKind::key` in crates/er-cel-shade/src/replace.rs, in `PortKind` order; a kind's bit is
# 1 << its index. The selftest reads them from the Rust.
PORT_KINDS = ("ao", "ao_ssr", "cmb_albedo", "tonemap", "tonemap_lut")
ALL_KINDS = (1 << len(PORT_KINDS)) - 1

# Phase name -> (the mode under test, the mode it is compared with, what passing means). The
# fidelity phase runs once per kind, with that kind's bit added to its mode value.
PHASES = {
    "cel": (MODE_NORMAL, MODE_VANILLA, "differ"),
    "fidelity": (MODE_FIDELITY, MODE_VANILLA, "match"),
}
# Design 14, P0.1 item 4: the smallest cel-against-vanilla difference is at least this many times
# the largest same-mode one.
CEL_MIN_RATIO = 3.0
# Design 14, P0.1 item 5: the largest fidelity-against-vanilla difference is at most this many
# times the largest same-mode one.
FIDELITY_MAX_RATIO = 1.5
# A phase whose same-mode pairs differ in more than this fraction of pixels is invalid, not judged:
# the scene moved, so neither ratio says anything about the mode.
SCENE_MOVED_SAME = 0.25

ALTERNATIONS = 6
# Presents to let pass under a new mode before photographing it. The parameter ring is 8 deep and
# the first frame after a switch may still be in flight, so 10 is past both.
SETTLE_PRESENTS = 10
# A pixel differs when its largest channel difference exceeds this, of 255. Measured 2026-10-14 on
# a dark night scene (mean luma 0.14), photographs reduced to 320x180: the Comic ink look against
# vanilla separated 4.07x at 2 and 0.02x at 10, because cel moves most pixels of a dark picture by
# less than 10 and the box filter averages its band edges away; the four fidelity kinds read 1.00
# to 1.02x at 2. JPEG noise sits under 2.
PIXEL_THRESHOLD = 2
COMPARE_SIZE = (320, 180)

# Caps on single waits and subprocesses, at or under the repo's 30-second limit
# (scripts/check-no-timeouts.py). A settle normally ends on the second telemetry write.
SETTLE_TIMEOUT_SECONDS = 15.0
CAPTURE_TIMEOUT_SECONDS = 30.0
CONVERT_TIMEOUT_SECONDS = 20.0
NODE_TIMEOUT_SECONDS = 20.0
ATTACH_TIMEOUT_SECONDS = 15.0
ATTACH_SLICE_SECONDS = 2.0
ENUMERATE_TIMEOUT_SECONDS = 6.0
WAIT_SLICE_SECONDS = 0.5

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INVALID = 2

# Where photographs go when the telemetry is not in a run directory of its own.
DEFAULT_IMAGE_ROOT = pathlib.Path.home() / ".cache" / "er-shader-ab"


class ProofInvalid(RuntimeError):
    """The run cannot answer the question: the state was not what the photograph needs."""


def load_checker():
    """`scripts/check-shader-editor-probe.py` as a module, for its telemetry resolution and wait.

    Loaded from its path because the file name carries hyphens and no `import` can spell it. It is
    registered in `sys.modules` before it runs, because its dataclasses look their module up there
    while they are being built.
    """
    name = "check_shader_editor_probe"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PROBE_CHECKER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PROBE_CHECKER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


# Images


def parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    """Width, height and RGB bytes of a binary PPM (`P6`, 8 bits per channel)."""
    fields: list[bytes] = []
    at = 0
    while len(fields) < 4:
        while at < len(data) and data[at : at + 1].isspace():
            at += 1
        if at < len(data) and data[at : at + 1] == b"#":
            end = data.find(b"\n", at)
            if end < 0:
                raise ValueError("a PPM comment runs to the end")
            at = end + 1
            continue
        start = at
        while at < len(data) and not data[at : at + 1].isspace() and data[at : at + 1] != b"#":
            at += 1
        if start == at:
            raise ValueError("a PPM header ends early")
        fields.append(data[start:at])
    if fields[0] != b"P6":
        raise ValueError(f"not a binary PPM: {fields[0][:8]!r}")
    try:
        width, height, maxval = (int(value) for value in fields[1:])
    except ValueError:
        raise ValueError("a PPM header field is not a number") from None
    if width <= 0 or height <= 0 or maxval != 255:
        raise ValueError(f"unsupported PPM {width}x{height} maxval {maxval}")
    at += 1  # the one whitespace byte after the maximum value
    pixels = data[at : at + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError(f"a PPM of {width}x{height} carries {len(pixels)} pixel bytes")
    return width, height, pixels


def ppm(width: int, height: int, pixels: bytes) -> bytes:
    return b"P6\n# er-shader-ab-proof\n%d %d\n255\n" % (width, height) + pixels


def load_pixels(image: pathlib.Path, size: tuple[int, int]) -> bytes:
    """The photograph as `size` RGB bytes, box-filtered by ImageMagick."""
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        raise ProofInvalid("ImageMagick (magick or convert) is needed to read the photographs")
    width, height = size
    try:
        result = subprocess.run(
            [magick, str(image), "-filter", "Box", "-resize", f"{width}x{height}!",
             "-depth", "8", "ppm:-"],
            capture_output=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProofInvalid(f"reading {image.name} timed out") from None
    if result.returncode != 0:
        raise ProofInvalid(f"reading {image.name}: magick exited {result.returncode}")
    got_width, got_height, pixels = parse_ppm(result.stdout)
    if (got_width, got_height) != size:
        raise ProofInvalid(f"{image.name} came back {got_width}x{got_height}")
    return pixels


def difference_fraction(a: bytes, b: bytes, threshold: int) -> float:
    """The fraction of pixels whose largest channel difference exceeds `threshold`."""
    if len(a) != len(b) or len(a) % 3:
        raise ValueError("the two images are not the same size")
    pixels = len(a) // 3
    if pixels == 0:
        return 0.0
    over = bytes(1 if abs(x - y) > threshold else 0 for x, y in zip(a, b))
    differing = sum(1 for at in range(0, len(over), 3) if over[at] | over[at + 1] | over[at + 2])
    return differing / pixels


def difference_matrix(images: list[bytes], threshold: int) -> list[list[float]]:
    matrix = [[0.0] * len(images) for _ in images]
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            matrix[i][j] = matrix[j][i] = difference_fraction(images[i], images[j], threshold)
    return matrix


@dataclass
class Judgement:
    same: list[float]
    cross: list[float]
    ratio: float
    passed: bool
    rule: str


def judge(expect: str, modes: list[int], matrix: list[list[float]]) -> Judgement:
    """Split the matrix into same-mode and cross-mode pairs and apply the phase's rule."""
    same, cross = [], []
    for i in range(len(modes)):
        for j in range(i + 1, len(modes)):
            (same if modes[i] == modes[j] else cross).append(matrix[i][j])
    if not same or not cross:
        raise ValueError("a phase needs two photographs of each mode")
    largest_same = max(same)
    if expect == "differ":
        smallest_cross = min(cross)
        if largest_same == 0.0:
            ratio = math.inf if smallest_cross > 0.0 else 0.0
        else:
            ratio = smallest_cross / largest_same
        return Judgement(same, cross, ratio, ratio >= CEL_MIN_RATIO, f">={CEL_MIN_RATIO:g}")
    largest_cross = max(cross)
    if largest_same == 0.0:
        ratio = 0.0 if largest_cross == 0.0 else math.inf
    else:
        ratio = largest_cross / largest_same
    return Judgement(same, cross, ratio, ratio <= FIDELITY_MAX_RATIO, f"<={FIDELITY_MAX_RATIO:g}")


def scene_moved(judgement: Judgement) -> str | None:
    """Why a phase's photographs cannot be compared, or None. Two photographs of one mode that
    differ almost everywhere mean the camera or the world moved under the series, and then any
    ratio passes or fails by chance. Measured 2026-10-14: a still scene's same-mode pairs differ in
    4 to 6% of pixels at the default threshold, and a phase the scene moved through read 99.9%."""
    largest_same = max(judgement.same)
    if largest_same > SCENE_MOVED_SAME:
        return (
            f"the scene moved: two photographs of one mode differ in {largest_same:.1%} of "
            f"pixels, over the {SCENE_MOVED_SAME:.0%} a comparison can see through; rerun on a "
            "still scene"
        )
    return None


def mode_value(mode: int, kinds: int = 0) -> int:
    """The mode export's value: the mode, and a fidelity test's kinds in the second byte."""
    return mode | (kinds << MODE_KINDS_SHIFT)


def base_mode(value: int) -> int:
    """The mode a mode value names, without a fidelity test's kinds."""
    return value & 0xFF


def kinds_text(bits: int | None) -> str:
    """A set of kind bits as `PortKind::key`s, or `none`."""
    if bits is None:
        return "-"
    names = [name for at, name in enumerate(PORT_KINDS) if bits >> at & 1]
    return ",".join(names) if names else "none"


def parse_kinds(text: str) -> list[int]:
    """Kind numbers from a comma list of `PortKind::key`s or numbers; an empty list is none."""
    kinds = []
    for word in (part.strip() for part in text.split(",")):
        if not word:
            continue
        if word.isdigit() and int(word) < len(PORT_KINDS):
            kind = int(word)
        elif word in PORT_KINDS:
            kind = PORT_KINDS.index(word)
        else:
            raise ValueError(f"unknown port kind {word!r}; one of {', '.join(PORT_KINDS)}")
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def kinds_bits(kinds: list[int]) -> int:
    return sum(1 << kind for kind in set(kinds))


def parse_mode_value(text: str) -> int:
    """A `--set-mode` value: 0, 1 or 2, a fidelity test's kinds allowed in the second byte. The DLL
    refuses anything else it is handed; this refuses what it can see is no mode."""
    value = int(text, 0)
    if not 0 <= value <= 0xFFFF or base_mode(value) not in (0, 1, 2):
        raise argparse.ArgumentTypeError("--set-mode takes 0, 1, 2, or 2 | kinds << 8")
    return value


@dataclass(frozen=True)
class Plan:
    """One alternated series: what is photographed against what, and what passing means."""

    label: str
    under_test: int
    reference: int
    expect: str
    # The port kind a fidelity series tests alone, or None for a series over every port.
    kind: int | None = None


def plans_for(phases: list[str], kinds: list[int]) -> list[Plan]:
    """The series the phases ask for: `cel` once, `fidelity` once per kind in `kinds`."""
    out = []
    for phase in phases:
        under_test, reference, expect = PHASES[phase]
        if phase == "fidelity":
            out.extend(
                Plan(
                    f"fidelity:{PORT_KINDS[kind]}",
                    mode_value(under_test, 1 << kind),
                    reference,
                    expect,
                    kind,
                )
                for kind in kinds
            )
        else:
            out.append(Plan(phase, under_test, reference, expect))
    return out


def alternate(plan: Plan, alternations: int) -> list[int]:
    return [plan.under_test, plan.reference] * alternations


def new_mark(before: int, tested: int, failed: int) -> int:
    """The unfaithful mark after a fidelity phase: exactly the failed kinds among those tested,
    and every kind not tested as it was."""
    return ((before & ~tested) | (failed & tested)) & ALL_KINDS


def labels_for(modes: list[int]) -> list[str]:
    seen: dict[int, int] = {}
    labels = []
    for mode in modes:
        labels.append(f"m{base_mode(mode)}.{seen.get(mode, 0)}")
        seen[mode] = seen.get(mode, 0) + 1
    return labels


def matrix_lines(labels: list[str], matrix: list[list[float]]) -> list[str]:
    width = max(6, max(len(label) for label in labels) + 1)
    lines = [" " * width + "".join(label.rjust(width) for label in labels)]
    for label, row in zip(labels, matrix):
        lines.append(label.ljust(width) + "".join(f"{value:{width}.3f}" for value in row))
    return lines


def summary_line(phase: str, judgement: Judgement) -> str:
    def stat(values, fn):
        return f"{fn(values):.4f}"

    ratio = "inf" if math.isinf(judgement.ratio) else f"{judgement.ratio:.3f}"
    return (
        f"phase={phase} same_max={stat(judgement.same, max)} "
        f"same_median={stat(judgement.same, statistics.median)} "
        f"cross_min={stat(judgement.cross, min)} "
        f"cross_median={stat(judgement.cross, statistics.median)} "
        f"cross_max={stat(judgement.cross, max)} ratio={ratio} needed{judgement.rule} "
        f"verdict={'pass' if judgement.passed else 'fail'}"
    )


# Capture


def captured_image(note: str, path: pathlib.Path) -> pathlib.Path:
    """The image a capture-er-window.py note names, or why there is none, in the note's words."""
    match = re.match(r"captured ER window .* -> (\S+) \(", note)
    if match is None:
        raise ProofInvalid("capture: " + (note.strip() or "no note was written"))
    return path.parent / match.group(1)


def capture(path: pathlib.Path) -> pathlib.Path:
    """Photograph the validated game window into `path` and return the image written."""
    try:
        subprocess.run(
            [sys.executable, str(CAPTURE), str(path)],
            capture_output=True,
            text=True,
            timeout=CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProofInvalid(f"capturing {path.name} timed out") from None
    note = path.with_suffix(".txt")
    try:
        text = note.read_text(encoding="utf-8")
    except OSError:
        text = ""
    image = captured_image(text, path)
    if not image.is_file():
        raise ProofInvalid(f"capture named {image.name}, which is not there")
    return image


# Mode switches


def hold_problems(mode: int, settled: dict, captured: dict) -> list[str]:
    """What was wrong with the state across one photograph, from the stats either side of it.
    `mode` is the mode value set, a fidelity test's kinds included."""
    problems = []
    want_vanilla = 1 if base_mode(mode) == MODE_VANILLA else 0
    for when, stats in (("before", settled), ("after", captured)):
        if stats.get("mode") != mode:
            problems.append(f"mode read {stats.get('mode')} {when} the photograph, not {mode}")
        if stats.get("vanilla") != want_vanilla:
            problems.append(
                f"vanilla read {stats.get('vanilla')} {when} the photograph, not {want_vanilla}"
                + ("; the panic chord or a hook fault holds it" if want_vanilla == 0 else "")
            )
    binds = captured.get("redirected_binds", 0) - settled.get("redirected_binds", 0)
    if want_vanilla and binds != 0:
        problems.append(f"{binds} binds were redirected while vanilla was photographed")
    if not want_vanilla and binds <= 0:
        problems.append(
            "no bind was redirected while the photograph was taken; no stage has content"
            if mode == MODE_NORMAL
            else "no bind was redirected while the photograph was taken"
        )
    return problems


class Waiter:
    """Re-read the stats on every write in the telemetry directory until a condition holds."""

    def __init__(self, api, watch, block, timeout: float = SETTLE_TIMEOUT_SECONDS) -> None:
        self.api = api
        self.watch = watch
        self.block = block
        self.timeout = timeout

    def until(self, condition, what: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            stats = self.api.stats()
            if condition(stats):
                return stats
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProofInvalid(f"{what} did not happen within {self.timeout:g}s: {stats}")
            self.block(self.watch, min(WAIT_SLICE_SECONDS, remaining))


def switch(api, waiter: Waiter, mode: int, settle: int) -> tuple[dict, dict]:
    """Set `mode`, then wait for `settle` presents under it. Returns the switch and the stats."""
    result = api.set_mode(mode)
    if result.get("refused"):
        raise ProofInvalid(f"the DLL refused mode {mode}")
    start = result["stats"]["presents"]
    settled = waiter.until(
        lambda stats: stats["presents"] - start >= settle,
        f"{settle} presents after switching to mode {mode}",
    )
    return result, settled


def stats_line(prefix: str, stats: dict) -> str:
    keys = (
        "presents", "mode", "vanilla", "redirected_binds", "passthrough_port_binds",
        "ports_ready", "ports_off", "unfaithful_ports", "probe_frame", "variant_id",
        "graveyard_len", "leaked",
    )
    return prefix + " " + " ".join(
        f"{key}={','.join(map(str, stats[key])) if isinstance(stats.get(key), list) else stats.get(key)}"
        for key in keys
    )


@dataclass
class Shot:
    label: str
    mode: int
    image: pathlib.Path
    settled: dict
    captured: dict
    pixels: bytes = field(repr=False, default=b"")


def binds_while_settling(result: dict, settled: dict) -> int:
    """Binds redirected between a switch and the end of its settle: 0 for a kind not drawn."""
    return settled.get("redirected_binds", 0) - result["stats"].get("redirected_binds", 0)


def run_phase(
    plan: Plan,
    api,
    waiter: Waiter,
    out_dir: pathlib.Path,
    alternations: int,
    settle: int,
    threshold: int,
    size: tuple[int, int],
) -> tuple[Judgement | None, list[Shot], list[list[float]]]:
    """Photograph one series and judge it; no judgement for a kind this scene does not draw."""
    modes = alternate(plan, alternations)
    labels = labels_for(modes)
    if plan.kind is not None:
        result, settled = switch(api, waiter, plan.under_test, settle)
        binds = binds_while_settling(result, settled)
        print(f"phase={plan.label} binds_while_settling={binds}", flush=True)
        if binds <= 0:
            return None, [], []
    stem = plan.label.replace(":", "-")
    shots = []
    for label, mode in zip(labels, modes):
        _, settled = switch(api, waiter, mode, settle)
        image = capture(out_dir / f"{stem}-{label}-{MODE_NAMES[base_mode(mode)]}.jpg")
        captured = api.stats()
        problems = hold_problems(mode, settled, captured)
        if problems:
            raise ProofInvalid(f"{plan.label} {label}: " + "; ".join(problems))
        shots.append(Shot(label, mode, image, settled, captured))
        print(stats_line(f"shot={plan.label}:{label} image={image.name}", captured), flush=True)
    for shot in shots:
        shot.pixels = load_pixels(shot.image, size)
    matrix = difference_matrix([shot.pixels for shot in shots], threshold)
    judgement = judge(plan.expect, modes, matrix)
    moved = scene_moved(judgement)
    if moved:
        raise ProofInvalid(f"{plan.label}: {moved}")
    return judgement, shots, matrix


# Frida


def enumerate_bounded(dev, timeout_seconds: float = ENUMERATE_TIMEOUT_SECONDS):
    """`dev.enumerate_processes()` with a bound: a stale server answers its port and nothing else."""
    answer: list = []

    def ask() -> None:
        try:
            answer.append(dev.enumerate_processes())
        except Exception as exc:  # noqa: BLE001 -- re-raised below, whatever it was
            answer.append(exc)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if not answer:
        raise ProofInvalid(
            "the frida server accepted the connection but did not answer enumerate_processes; "
            f"restart it with python3 {SCRIPTS / 'er-frida-up.py'} --force"
        )
    if isinstance(answer[0], Exception):
        raise answer[0]
    return answer[0]


def attach(frida):
    """Attach through the Wine-side server, waiting on the game directory rather than a clock."""
    dev = frida.get_device_manager().add_remote_device(SERVER_ADDRESS)
    deadline = time.monotonic() + ATTACH_TIMEOUT_SECONDS
    with er_run_lib.DirectoryWatch(er_run_lib.game_dir()) as launch:
        while True:
            procs = [p for p in enumerate_bounded(dev) if p.name.lower() == "eldenring.exe"]
            if procs:
                return dev.attach(procs[0].pid), procs[0].pid
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not launch.available:
                break
            launch.wait(min(ATTACH_SLICE_SECONDS, remaining))
    raise ProofInvalid(
        f"no eldenring.exe visible to the frida server; run python3 {SCRIPTS / 'er-frida-up.py'} first"
    )


def record_evidence(pid: int, messages: int, seconds: float) -> None:
    """Record the session for the Rust-edit gate; nothing here may change the verdict."""
    try:
        spec = importlib.util.spec_from_file_location("er_frida_evidence", EVIDENCE_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.record(str(AGENT.relative_to(REPO)), pid, messages, seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"evidence not recorded: {exc}", file=sys.stderr, flush=True)


def image_dir(telemetry: pathlib.Path, explicit: pathlib.Path | None, stamp: str) -> pathlib.Path:
    """Beside the run's telemetry when it has a run directory, else under the user's cache."""
    if explicit is not None:
        return explicit
    try:
        telemetry.parent.resolve().relative_to(er_run_lib.RUN_STATE_ROOT.resolve())
        return telemetry.parent / f"shader-ab-{stamp}"
    except ValueError:
        return DEFAULT_IMAGE_ROOT / stamp


def parse_size(text: str) -> tuple[int, int]:
    width, sep, height = text.partition("x")
    if not sep or not width.isdigit() or not height.isdigit() or int(width) * int(height) == 0:
        raise argparse.ArgumentTypeError("--size takes <width>x<height>")
    return int(width), int(height)


def run(args) -> int:
    checker = load_checker()
    import frida  # imported here so --selftest runs without it

    try:
        session, pid = attach(frida)
    except (ProofInvalid, frida.ServerNotRunningError, frida.TransportError) as error:
        print(f"invalid: {error}", file=sys.stderr, flush=True)
        return EXIT_INVALID
    messages: list = []
    detached: list = []
    session.on("detached", lambda reason, *_: detached.append(reason))
    started = time.monotonic()
    restore: int | None = None
    api = None

    def on_terminate(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_terminate)
    try:
        script = session.create_script(AGENT.read_text(encoding="utf-8"))
        script.on("message", lambda message, _data: messages.append(message))
        script.load()
        api = script.exports_sync
        first = api.stats()
        print(stats_line("start", first), flush=True)
        if args.stats:
            print(json.dumps(first, sort_keys=True), flush=True)
            return EXIT_PASS
        if args.set_unfaithful is not None:
            marked = api.set_unfaithful(args.set_unfaithful)
            if marked.get("refused"):
                raise ProofInvalid(f"the DLL refused the unfaithful mark {args.set_unfaithful}")
            print(
                f"unfaithful_before={kinds_text(marked['before'])} "
                f"unfaithful_after={kinds_text(marked['stats']['unfaithful_ports'])}",
                flush=True,
            )
            return EXIT_PASS
        telemetry = checker.resolve_telemetry(args.telemetry, args.artifact_dir)
        print(f"telemetry={telemetry}", flush=True)
        with er_run_lib.DirectoryWatch(telemetry.parent) as watch:
            waiter = Waiter(api, watch, checker.block)
            if args.set_mode is not None:
                result, settled = switch(api, waiter, args.set_mode, args.settle)
                print(f"set_mode={args.set_mode} before={result['before']}", flush=True)
                print(stats_line("settled", settled), flush=True)
                return EXIT_PASS
            if first["vanilla"] and first["mode"] != MODE_VANILLA:
                raise ProofInvalid(
                    "vanilla is set by the panic chord or a hook fault, and mode 0 cannot clear it"
                )
            restore = first["mode"]
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_dir = image_dir(telemetry, args.out_dir, stamp)
            out_dir.mkdir(parents=True, exist_ok=True)
            print(f"images={out_dir}", flush=True)
            summary = {"size": list(args.size), "threshold": args.threshold, "phases": {}}
            verdicts = []
            kinds = args.kinds
            if kinds is None:
                kinds = [k for k, ready in enumerate(first["ports_ready"]) if ready > 0]
            tested, failed, skipped = 0, 0, []
            for plan in plans_for(args.phases, kinds):
                judgement, shots, matrix = run_phase(
                    plan, api, waiter, out_dir, args.alternations, args.settle,
                    args.threshold, args.size,
                )
                if judgement is None:
                    print(f"phase={plan.label} verdict=skipped why=not_drawn_in_this_scene", flush=True)
                    skipped.append(plan.label)
                    continue
                labels = [shot.label for shot in shots]
                print(
                    f"matrix phase={plan.label} size={args.size[0]}x{args.size[1]} "
                    f"threshold={args.threshold}",
                    flush=True,
                )
                for line in matrix_lines(labels, matrix):
                    print(line, flush=True)
                print(summary_line(plan.label, judgement), flush=True)
                verdicts.append(judgement.passed)
                if plan.kind is not None:
                    tested |= 1 << plan.kind
                    failed |= 0 if judgement.passed else 1 << plan.kind
                summary["phases"][plan.label] = {
                    "mode": plan.under_test,
                    "labels": labels,
                    "images": [shot.image.name for shot in shots],
                    "matrix": matrix,
                    "ratio": None if math.isinf(judgement.ratio) else judgement.ratio,
                    "rule": judgement.rule,
                    "passed": judgement.passed,
                    "stats": [{"settled": s.settled, "captured": s.captured} for s in shots],
                }
            summary["skipped"] = skipped
            if "fidelity" in args.phases:
                if tested == 0:
                    raise ProofInvalid(
                        "no port kind was drawn while its fidelity test settled; tried "
                        + kinds_text(kinds_bits(kinds))
                    )
                before = first["unfaithful_ports"]
                after = new_mark(before, tested, failed)
                summary["unfaithful"] = {"before": before, "after": after, "marked": False}
                if after != before and not args.no_mark:
                    marked = api.set_unfaithful(after)
                    if marked.get("refused"):
                        raise ProofInvalid(f"the DLL refused the unfaithful mark {after}")
                    summary["unfaithful"]["marked"] = True
                print(
                    f"fidelity tested={kinds_text(tested)} failed={kinds_text(failed)} "
                    f"unfaithful_before={kinds_text(before)} unfaithful_after={kinds_text(after)} "
                    f"marked={int(summary['unfaithful']['marked'])}",
                    flush=True,
                )
            (out_dir / "ab-proof.json").write_text(
                json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"verdict={'pass' if all(verdicts) else 'fail'}", flush=True)
            return EXIT_PASS if all(verdicts) else EXIT_FAIL
    except ProofInvalid as error:
        print(f"verdict=invalid why={error}", flush=True)
        return EXIT_INVALID
    except KeyboardInterrupt:
        print("verdict=invalid why=interrupted", flush=True)
        return EXIT_INVALID
    except frida.core.RPCException as error:
        # The agent's own refusal: most often `er_cel_shade.dll is not loaded in this process`.
        print(f"verdict=invalid why=agent: {error}", flush=True)
        return EXIT_INVALID
    except frida.InvalidOperationError as error:
        print(f"verdict=invalid why=session ended: {error} {detached}", flush=True)
        return EXIT_INVALID
    finally:
        if restore is not None and api is not None and not detached:
            try:
                back = api.set_mode(restore)
                print(f"restored mode={restore} vanilla={back['stats']['vanilla']}", flush=True)
            except Exception as error:  # noqa: BLE001 -- the verdict is already decided
                print(f"could not restore mode {restore}: {error}", file=sys.stderr, flush=True)
        record_evidence(pid, len(messages), time.monotonic() - started)
        if not detached:
            session.detach()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--phases", default="cel,fidelity", help="comma list of cel, fidelity")
    parser.add_argument("--alternations", type=int, default=ALTERNATIONS)
    parser.add_argument("--settle", type=int, default=SETTLE_PRESENTS)
    parser.add_argument("--threshold", type=int, default=PIXEL_THRESHOLD)
    parser.add_argument("--size", type=parse_size, default=COMPARE_SIZE)
    parser.add_argument("--out-dir", type=pathlib.Path)
    parser.add_argument("--telemetry", type=pathlib.Path)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--stats", action="store_true", help="print the stats once and leave")
    parser.add_argument(
        "--set-mode", type=parse_mode_value, help="switch, settle, leave: 0, 1, 2 or 2 | kinds << 8"
    )
    parser.add_argument(
        "--kinds", help=f"the fidelity phase's kinds, a comma list of {', '.join(PORT_KINDS)}"
    )
    parser.add_argument(
        "--no-mark", action="store_true", help="report fidelity failures without marking them"
    )
    parser.add_argument(
        "--set-unfaithful", metavar="KINDS", help="set the unfaithful mark to these kinds and leave"
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        args.kinds = None if args.kinds is None else parse_kinds(args.kinds)
        if args.set_unfaithful is not None:
            args.set_unfaithful = kinds_bits(parse_kinds(args.set_unfaithful))
    except ValueError as error:
        parser.error(str(error))
    if args.kinds == []:
        parser.error("--kinds names no kind")
    phases = [phase.strip() for phase in args.phases.split(",") if phase.strip()]
    unknown = [phase for phase in phases if phase not in PHASES]
    if unknown or not phases:
        parser.error(f"--phases takes {', '.join(PHASES)}")
    if args.alternations < 2:
        parser.error("--alternations needs at least 2, or there is no same-mode pair")
    if not 0 <= args.threshold < 255:
        parser.error("--threshold is 0 to 254")
    args.phases = phases
    return run(args)


# Selftest


def rust_layout() -> list[tuple[str, int, str, int]]:
    count = re.search(r"pub const COUNT: usize = (\d+);", REPLACE_RS.read_text(encoding="utf-8"))
    rows = re.findall(
        r'Field::new\("(\w+)", (\d+), "(\w+)", ([\w:]+)\)', EXPORTS_RS.read_text(encoding="utf-8")
    )
    return [
        (name, int(offset), ty, int(count.group(1)) if n == "PortKind::COUNT" else int(n))
        for name, offset, ty, n in rows
    ]


def agent_layout(source: str) -> list[tuple[str, int, str, int]]:
    block = source[source.index("const LAYOUT = [") :]
    block = block[: block.index("];")]
    return [
        (name, int(offset), ty, int(count))
        for name, offset, ty, count in re.findall(r"\['(\w+)', (\d+), '(\w+)', (\d+)\]", block)
    ]


def pack_stats(layout, values: dict) -> bytes:
    import struct

    codes = {"u64": "<Q", "u32": "<I", "f32": "<f"}
    sizes = {"u64": 8, "u32": 4, "f32": 4}
    out = bytearray(max(offset + sizes[ty] * count for _, offset, ty, count in layout))
    for name, offset, ty, count in layout:
        items = values[name] if count > 1 else [values[name]]
        for at, item in enumerate(items):
            struct.pack_into(codes[ty], out, offset + at * sizes[ty], item)
    return bytes(out)


# The line of node's output the harness's answer is on. Twice in about twenty selftests on
# 2026-10-15 node's stdout held a second line beside the harness's JSON, which the parse refused
# as `Extra data: line 2 column 1 (char 165)`; what wrote it was not caught (`node` on this
# machine is a proto shim, `~/.proto/shims/node`). So the answer is the one line that starts with
# this, and anything else on stdout is left alone.
NODE_RESULT_LINE = "er-shader-ab-proof-result "

# Runs the agent in node with Frida's globals stubbed: the export table, NativeFunction calls that
# copy a StatsV1 the selftest packed, a set-mode that writes the mode field, and a set-unfaithful
# that writes the unfaithful field at the offset the selftest passes.
NODE_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const memory = Buffer.from(input.stats_hex, 'hex');
const sent = [];
let mode = 0;
let unfaithful = 0;
const sandbox = {
  Process: {
    findModuleByName(name) {
      if (name !== 'er_cel_shade.dll') { return null; }
      return {
        name,
        getExportByName(exportName) {
          return { name: exportName, toString() { return '0x7ff0:' + exportName; } };
        },
      };
    },
  },
  Memory: {
    alloc(n) {
      const buffer = Buffer.alloc(n);
      return {
        buffer,
        readByteArray(k) { return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + k); },
        toString() { return 'out'; },
      };
    },
  },
  NativeFunction: function (address, ret, args) {
    if (address.name === 'er_shader_editor_stats_v1') {
      return (out) => { memory.copy(out.buffer); return memory.length; };
    }
    if (address.name === 'er_shader_editor_set_mode_v1') {
      return (m) => {
        if ((m & 0xff) > 2) { return 0xffffffff; }
        const before = mode;
        mode = m;
        memory.writeUInt32LE(m, 8);
        return before;
      };
    }
    if (address.name === 'er_shader_editor_set_unfaithful_v1') {
      return (p) => {
        if (p > 31) { return 0xffffffff; }
        const before = unfaithful;
        unfaithful = p;
        memory.writeUInt32LE(p, input.unfaithful_at);
        return before;
      };
    }
    throw new Error('unexpected export ' + address.name);
  },
  send(message) { sent.push(message); },
  rpc: {},
};
vm.createContext(sandbox);
vm.runInContext(input.source, sandbox);
const stats = sandbox.rpc.exports.stats();
const switched = sandbox.rpc.exports.setMode(1);
const refused = sandbox.rpc.exports.setMode(7);
const kind = sandbox.rpc.exports.setMode(2 | (8 << 8));
const marked = sandbox.rpc.exports.setUnfaithful(2);
const unmarked = sandbox.rpc.exports.setUnfaithful(64);
process.stdout.write('\n' + NODE_RESULT_PREFIX
  + JSON.stringify({ stats, switched, refused, kind, marked, unmarked, sent }) + '\n');
""".replace("NODE_RESULT_PREFIX", repr(NODE_RESULT_LINE))


def run_agent_in_node(source: str, stats_bytes: bytes, unfaithful_at: int) -> dict | None:
    node = shutil.which("node")
    if node is None:
        return None
    result = subprocess.run(
        [node, "-e", NODE_HARNESS],
        input=json.dumps(
            {"source": source, "stats_hex": stats_bytes.hex(), "unfaithful_at": unfaithful_at}
        ),
        capture_output=True,
        text=True,
        timeout=NODE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-400:])
    answers = [
        line[len(NODE_RESULT_LINE):]
        for line in result.stdout.splitlines()
        if line.startswith(NODE_RESULT_LINE)
    ]
    if len(answers) != 1:
        raise RuntimeError(f"node printed {len(answers)} answers: {result.stdout[-400:]!r}")
    return json.loads(answers[0])


class Lcg:
    """A small deterministic generator, so the synthetic photographs are the same every run."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFF_FFFF

    def next(self) -> int:
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFF_FFFF
        return self.state >> 8


def scene(
    width: int, height: int, *, posterize: bool, noise: float, seed: int, faint: int = 0
) -> bytes:
    """A gradient scene with `noise` of its pixels disturbed; `posterize` stands in for cel, and
    `faint` for cel on a dark picture, which moves every pixel by only that much."""
    rng = Lcg(seed)
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = (x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 96
            if posterize:
                r, g = (r // 64) * 64 + 32, (g // 64) * 64 + 32
            r = min(255, r + faint)
            if rng.next() % 10_000 < noise * 10_000:
                r, g, b = 255 - r, 255 - g, 255 - b
            out += bytes((r, g, b))
    return bytes(out)


def selftest() -> int:
    results: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        results.append((label, bool(ok)))

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    agent = AGENT.read_text(encoding="utf-8") if AGENT.is_file() else ""
    code = "\n".join(line for line in agent.splitlines() if not line.lstrip().startswith("//"))

    # The agent.
    check("the agent file exists", bool(agent))
    check(
        "the agent resolves all three exports by name from er_cel_shade.dll",
        "'er_cel_shade.dll'" in code
        and "'er_shader_editor_stats_v1'" in code
        and "'er_shader_editor_set_mode_v1'" in code
        and "'er_shader_editor_set_unfaithful_v1'" in code
        and ".getExportByName(name)" in code,
    )
    check("the agent calls them through NativeFunction", code.count("new NativeFunction(") == 3)
    check("the agent installs no Interceptor", "Interceptor" not in code)
    check(
        "the agent has no timer",
        all(api not in code for api in ("set" + "Timeout(", "set" + "Interval(")),
    )
    check(
        "the agent exposes rpc stats(), setMode(m) and setUnfaithful(p)",
        re.search(
            r"rpc\.exports = \{\s*stats: readStats,\s*setMode: setMode,"
            r"\s*setUnfaithful: setUnfaithful,\s*\};",
            code,
        )
        is not None,
    )
    layout = rust_layout()
    check("exports.rs's StatsV1 layout was read", len(layout) == 23)
    check("the agent's layout is exports.rs's, field for field", agent_layout(agent) == layout)
    stats_bytes = max(offset + (8 if ty == "u64" else 4) * n for _, offset, ty, n in layout)
    exports_rs = EXPORTS_RS.read_text(encoding="utf-8")
    check(
        "the agent's sizes and refusal are exports.rs's",
        stats_bytes == 200
        and f"pub const STATS_V1_BYTES: usize = {stats_bytes};" in exports_rs
        and f"const STATS_V1_BYTES = {stats_bytes};" in code
        and "pub const SET_MODE_REFUSED: u32 = u32::MAX;" in exports_rs
        and "const SET_MODE_REFUSED = 0xffffffff;" in code,
    )
    check(
        "the export names are exports.rs's",
        all(
            f"pub extern \"C\" fn {name}(" in exports_rs
            or f"pub unsafe extern \"C\" fn {name}(" in exports_rs
            for name in (
                "er_shader_editor_stats_v1",
                "er_shader_editor_set_mode_v1",
                "er_shader_editor_set_unfaithful_v1",
            )
        ),
    )

    # Port kinds and mode values, held to the Rust.
    replace_rs = REPLACE_RS.read_text(encoding="utf-8")
    key_fn = replace_rs[replace_rs.index("pub const fn key(self)") :]
    key_fn = key_fn[: key_fn.index("\n    }\n")]
    check(
        "the port kinds are PortKind::key's, in order",
        tuple(re.findall(r'Self::\w+ => "(\w+)",', key_fn)) == PORT_KINDS,
    )
    safety_rs = (REPO / "crates" / "er-cel-shade" / "src" / "safety.rs").read_text(encoding="utf-8")
    check(
        "the kind shift is safety.rs's",
        f"pub const MODE_KINDS_SHIFT: u32 = {MODE_KINDS_SHIFT};" in safety_rs,
    )
    check(
        "a mode value carries a fidelity test's kinds in its second byte",
        mode_value(MODE_FIDELITY, 1 << 3) == 0x0802
        and base_mode(0x0802) == MODE_FIDELITY
        and mode_value(MODE_VANILLA) == 1,
    )
    plans = plans_for(["cel", "fidelity"], [0, 3])
    check(
        "the fidelity phase is one series per kind, the cel phase one",
        [(p.label, p.under_test, p.reference, p.kind) for p in plans]
        == [
            ("cel", 0, 1, None),
            ("fidelity:ao", 0x0102, 1, 0),
            ("fidelity:tonemap", 0x0802, 1, 3),
        ],
    )
    check(
        "kinds parse by name or number, once each, and print by name",
        parse_kinds("tonemap, 1,tonemap,") == [3, 1]
        and parse_kinds("") == []
        and kinds_bits([3, 1, 3]) == 0b1010
        and kinds_text(0b1010) == "ao_ssr,tonemap"
        and kinds_text(0) == "none",
    )
    try:
        parse_kinds("sky")
        check("an unknown kind is refused", False)
    except ValueError:
        check("an unknown kind is refused", True)
    for good, value in (("1", 1), ("0x0802", 0x0802), ("2", 2)):
        check(f"--set-mode {good} parses", parse_mode_value(good) == value)
    for bad in ("3", "0x10000", "-1"):
        try:
            parse_mode_value(bad)
            check(f"--set-mode {bad} is refused", False)
        except argparse.ArgumentTypeError:
            check(f"--set-mode {bad} is refused", True)
    check(
        "the mark becomes the failed kinds among those tested, the rest as they were",
        new_mark(0b00010, tested=0b01001, failed=0b01000) == 0b01010
        and new_mark(0b01010, tested=0b01000, failed=0) == 0b00010
        and new_mark(0, tested=0b11111, failed=0b11111) == ALL_KINDS
        and new_mark(0b00001, tested=0b00010, failed=0b00100) == 0b00001,
    )
    check(
        "a kind with no bind redirected while it settled is not drawn",
        binds_while_settling({"stats": {"redirected_binds": 50}}, {"redirected_binds": 50}) == 0
        and binds_while_settling({"stats": {"redirected_binds": 50}}, {"redirected_binds": 61})
        == 11,
    )
    values = {
        "presents": 123_456_789_012,
        "mode": 0,
        "vanilla": 0,
        "rs_full": 7,
        "rs_cbv_probe": 1,
        "rs_cbv": 2,
        "rs_none": 3,
        "ports_ready": [1, 0, 1, 0, 1],
        "redirected_binds": 9_876_543,
        "passthrough_port_binds": 5,
        "set_pso_calls": 1 << 40,
        "hook_ns_sum": 123_456,
        "hook_ns_samples": 789,
        "list_slot_misses": 2,
        "graveyard_len": 4,
        "leaked": 0,
        "game_psos_released": 6,
        "probe_frame": [7, 0, 9],
        "variant_id": [1 << 24, 0, 4 << 24],
        "compile_ms_last": 0.0,
        "create_ms_last": 1.5,
        "unfaithful_ports": 0,
        "ports_off": 0b10000,
    }
    packed = pack_stats(layout, values)
    check("the packed stats are the size the DLL writes", len(packed) == stats_bytes)
    unfaithful_at = next(offset for name, offset, _, _ in layout if name == "unfaithful_ports")
    try:
        ran = run_agent_in_node(agent, packed, unfaithful_at)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        ran = {"error": str(error)}
    if ran is None:
        print("  skip  the agent under node: no node on this machine")
    else:
        stats = ran.get("stats", {})
        check(
            "under node, stats() decodes every field at its offset",
            all(stats.get(name) == value for name, value in values.items())
            and stats.get("bytes") == stats_bytes
            and stats.get("mode_name") == "normal",
        )
        switched = ran.get("switched", {})
        check(
            "under node, setMode(1) returns the mode before and the stats after",
            switched.get("before") == 0
            and switched.get("before_name") == "normal"
            and switched.get("refused") is False
            and switched.get("stats", {}).get("mode") == 1,
        )
        refused = ran.get("refused", {})
        check(
            "under node, a mode the DLL refuses reads as refused",
            refused.get("refused") is True and refused.get("before") is None,
        )
        kind = ran.get("kind", {})
        check(
            "under node, a fidelity test of one kind reads back its whole value, named by its mode",
            kind.get("stats", {}).get("mode") == 0x0802
            and kind.get("stats", {}).get("mode_name") == "fidelity"
            and kind.get("before_name") == "vanilla",
        )
        marked, unmarked = ran.get("marked", {}), ran.get("unmarked", {})
        check(
            "under node, setUnfaithful(p) returns the mark before and the stats after",
            marked.get("before") == 0
            and marked.get("refused") is False
            and marked.get("stats", {}).get("unfaithful_ports") == 2
            and unmarked.get("refused") is True
            and unmarked.get("stats", {}).get("unfaithful_ports") == 2,
        )
        events = [message.get("event") for message in ran.get("sent", [])]
        check(
            "under node, the agent says it is ready, then once per call",
            events
            == ["shader-editor-stats-ready"]
            + ["shader-editor-mode"] * 3
            + ["shader-editor-unfaithful"] * 2,
        )

    # Images.
    width, height = 48, 27
    base = scene(width, height, posterize=False, noise=0.0, seed=1)
    check("a PPM round-trips", parse_ppm(ppm(width, height, base)) == (width, height, base))
    for label, bad in (
        ("a text PPM", b"P3\n1 1\n255\n0 0 0\n"),
        ("a short PPM", ppm(width, height, base)[:-1]),
        ("a 16-bit PPM", b"P6\n1 1\n65535\n\x00\x00\x00\x00\x00\x00"),
        ("an empty file", b""),
    ):
        try:
            parse_ppm(bad)
            check(f"{label} is refused", False)
        except ValueError:
            check(f"{label} is refused", True)
    if shutil.which("magick") or shutil.which("convert"):
        with tempfile.TemporaryDirectory() as directory:
            plain = pathlib.Path(directory) / "plain.ppm"
            poster = pathlib.Path(directory) / "poster.ppm"
            plain.write_bytes(ppm(width, height, base))
            poster.write_bytes(
                ppm(width, height, scene(width, height, posterize=True, noise=0.0, seed=1))
            )
            try:
                small = load_pixels(plain, (16, 9))
                other = load_pixels(poster, (16, 9))
                loaded_ok = len(small) == 16 * 9 * 3 and difference_fraction(small, other, 10) > 0
            except (ProofInvalid, ValueError) as error:
                print(f"  load_pixels: {error}")
                loaded_ok = False
            check("ImageMagick reduces a photograph to the comparison size", loaded_ok)
    else:
        print("  skip  ImageMagick reduction: no magick or convert on this machine")
    one = bytes((10, 10, 10, 10, 10, 10))
    check("identical images differ nowhere", difference_fraction(base, base, 10) == 0.0)
    check(
        "a channel exactly at the threshold is not a difference, one past it is",
        difference_fraction(one, bytes((20, 10, 10, 10, 10, 10)), 10) == 0.0
        and difference_fraction(one, bytes((10, 10, 10, 10, 10, 21)), 10) == 0.5,
    )

    cel_plan, fidelity_plan = plans_for(["cel", "fidelity"], [3])

    def run_synthetic(cel_changes: bool, alternations: int = 6) -> tuple[Judgement, Judgement]:
        cel_modes = alternate(cel_plan, alternations)
        cel_images = [
            scene(width, height, posterize=cel_changes and mode == MODE_NORMAL, noise=0.01,
                  seed=10 + at)
            for at, mode in enumerate(cel_modes)
        ]
        fidelity_modes = alternate(fidelity_plan, alternations)
        fidelity_images = [
            scene(width, height, posterize=not cel_changes and base_mode(mode) == MODE_FIDELITY,
                  noise=0.01, seed=40 + at)
            for at, mode in enumerate(fidelity_modes)
        ]
        return (
            judge("differ", cel_modes, difference_matrix(cel_images, PIXEL_THRESHOLD)),
            judge("match", fidelity_modes, difference_matrix(fidelity_images, PIXEL_THRESHOLD)),
        )

    cel, fidelity = run_synthetic(cel_changes=True)
    check(
        f"synthetic: cel against vanilla passes (ratio {cel.ratio:.2f})",
        cel.passed and len(cel.same) == 30 and len(cel.cross) == 36,
    )
    check(f"synthetic: a faithful port passes (ratio {fidelity.ratio:.2f})", fidelity.passed)
    no_cel, unfaithful = run_synthetic(cel_changes=False)
    check(f"synthetic: cel that changes nothing fails (ratio {no_cel.ratio:.2f})", not no_cel.passed)
    check(
        f"synthetic: an unfaithful port fails (ratio {unfaithful.ratio:.2f})",
        not unfaithful.passed,
    )
    faint_modes = alternate(cel_plan, 6)
    faint_images = [
        scene(width, height, posterize=False, noise=0.01, seed=70 + at,
              faint=5 if mode == MODE_NORMAL else 0)
        for at, mode in enumerate(faint_modes)
    ]
    faint = judge("differ", faint_modes, difference_matrix(faint_images, PIXEL_THRESHOLD))
    faint_at_10 = judge("differ", faint_modes, difference_matrix(faint_images, 10))
    check(
        f"synthetic: cel that moves a dark picture by 5 of 255 passes (ratio {faint.ratio:.2f})",
        faint.passed,
    )
    check(
        f"synthetic: the same cel is missed at a threshold of 10 (ratio {faint_at_10.ratio:.2f}), "
        "the failure the default of 2 fixes",
        not faint_at_10.passed,
    )
    check(
        "the synthetic phases' scenes count as still",
        not any(scene_moved(j) for j in (cel, fidelity, no_cel, unfaithful, faint)),
    )
    moving = judge("match", [2, 1, 2, 1], [[0.0, 0.99, 0.999, 0.98], [0.99, 0.0, 0.98, 0.999],
                                           [0.999, 0.98, 0.0, 0.99], [0.98, 0.999, 0.99, 0.0]])
    check(
        "a fidelity phase whose scene moved passes its ratio and is still refused as moved",
        moving.passed and (scene_moved(moving) or "").startswith("the scene moved"),
    )
    check(
        "a same-mode difference at the ceiling is still a comparison, one past it is not",
        scene_moved(judge("match", [2, 1, 2, 1], [[0.0, 0.1, SCENE_MOVED_SAME, 0.1],
                                                   [0.1, 0.0, 0.1, SCENE_MOVED_SAME],
                                                   [SCENE_MOVED_SAME, 0.1, 0.0, 0.1],
                                                   [0.1, SCENE_MOVED_SAME, 0.1, 0.0]])) is None
        and scene_moved(judge("differ", [0, 1, 0, 1], [[0.0, 0.9, 0.26, 0.9],
                                                       [0.9, 0.0, 0.9, 0.0],
                                                       [0.26, 0.9, 0.0, 0.9],
                                                       [0.9, 0.0, 0.9, 0.0]])) is not None,
    )
    check(
        "a static scene: any cross difference over no noise is infinite, none is zero",
        judge("differ", [0, 1, 0, 1], [[0, 1, 0, 1], [1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 1, 0]]).ratio
        == math.inf
        and judge("match", [2, 1, 2, 1], [[0.0] * 4 for _ in range(4)]).ratio == 0.0,
    )
    matrix = difference_matrix([base, scene(width, height, posterize=True, noise=0, seed=2)], 10)
    check(
        "the matrix is symmetric with a zero diagonal",
        matrix[0][0] == 0.0 and matrix[0][1] == matrix[1][0] > 0.0,
    )
    check(
        "phases alternate the two modes",
        alternate(cel_plan, 3) == [0, 1, 0, 1, 0, 1]
        and alternate(fidelity_plan, 2) == [0x0802, 1, 0x0802, 1],
    )
    check(
        "labels count each mode, named by the mode without its kinds",
        labels_for([0, 1, 0, 1]) == ["m0.0", "m1.0", "m0.1", "m1.1"]
        and labels_for([0x0802, 1, 0x0802]) == ["m2.0", "m1.0", "m2.1"],
    )
    lines = matrix_lines(["m0.0", "m1.0"], [[0.0, 0.25], [0.25, 0.0]])
    check("the matrix prints one row per photograph", len(lines) == 3 and "0.250" in lines[1])

    # Capture notes.
    shot = pathlib.Path("/run/shots/cel-m0.0-normal.jpg")
    note = (
        "captured ER window class=steam_app_1245620 geom=0,0 2560x1440 -> cel-m0.0-normal.jpg "
        "({'class': 'steam_app_1245620'})\n"
    )
    check("a captured note names its image", captured_image(note, shot) == shot)
    for label, bad in (
        ("a fail-closed note", "capture fail-closed: no window class=steam_app_1245620\n"),
        ("a skipped note", "capture skipped: missing tool hyprctl=None grim=None\n"),
        ("no note", ""),
    ):
        try:
            captured_image(bad, shot)
            check(f"{label} makes the run invalid", False)
        except ProofInvalid:
            check(f"{label} makes the run invalid", True)

    # The state around a photograph.
    def stats(mode, vanilla, binds):
        return {"mode": mode, "vanilla": vanilla, "redirected_binds": binds, "presents": 1}

    check(
        "a clean cel photograph has no problem",
        hold_problems(MODE_NORMAL, stats(0, 0, 100), stats(0, 0, 900)) == [],
    )
    check(
        "a clean vanilla photograph has no problem",
        hold_problems(MODE_VANILLA, stats(1, 1, 900), stats(1, 1, 900)) == [],
    )
    check(
        "a redirected bind under vanilla is a problem",
        len(hold_problems(MODE_VANILLA, stats(1, 1, 900), stats(1, 1, 901))) == 1,
    )
    check(
        "no redirected bind under cel is a problem",
        len(hold_problems(MODE_NORMAL, stats(0, 0, 900), stats(0, 0, 900))) == 1,
    )
    check(
        "vanilla held by the panic chord under mode 0 is a problem",
        any("panic chord" in p for p in hold_problems(MODE_NORMAL, stats(0, 1, 0), stats(0, 1, 5))),
    )
    check(
        "a mode changed under the photograph is a problem",
        any("mode read 0" in p for p in hold_problems(MODE_FIDELITY, stats(2, 0, 1), stats(0, 0, 9))),
    )
    check(
        "a one-kind fidelity photograph is held to its whole mode value",
        hold_problems(0x0802, stats(0x0802, 0, 1), stats(0x0802, 0, 9)) == []
        and any(
            "mode read 2" in p for p in hold_problems(0x0802, stats(0x0802, 0, 1), stats(2, 0, 9))
        ),
    )

    # The settle wait: it re-reads on every wake and gives up only at its cap.
    class FakeApi:
        def __init__(self, presents):
            self.presents = list(presents)
            self.mode = 0

        def stats(self):
            value = self.presents.pop(0) if len(self.presents) > 1 else self.presents[0]
            return {"presents": value, "mode": self.mode, "vanilla": int(self.mode == 1),
                    "redirected_binds": 0}

        def set_mode(self, mode):
            before, self.mode = self.mode, mode
            return {"refused": False, "before": before, "stats": self.stats()}

    class FakeWatch:
        available = True
        wakes = 0

        def wait(self, _seconds):
            FakeWatch.wakes += 1
            return True

    def fake_block(watch, seconds):
        watch.wait(seconds)

    fake = FakeApi([100, 101, 105, 109, 111, 130])
    waiter = Waiter(fake, FakeWatch(), fake_block, timeout=5.0)
    result, settled = switch(fake, waiter, MODE_VANILLA, SETTLE_PRESENTS)
    check(
        "a switch settles on the first read 10 presents on",
        result["before"] == 0 and settled["presents"] == 111 and settled["mode"] == 1,
    )
    try:
        Waiter(FakeApi([5]), FakeWatch(), fake_block, timeout=0.05).until(
            lambda s: s["presents"] > 5, "a present"
        )
        check("a game that stops presenting makes the run invalid", False)
    except ProofInvalid:
        check("a game that stops presenting makes the run invalid", True)

    # Where the photographs go.
    run_dir = er_run_lib.RUN_STATE_ROOT / "br-selftest" / "er-cel-shade-telemetry.json"
    check(
        "photographs go beside a run directory's telemetry",
        image_dir(run_dir, None, "s") == run_dir.parent / "shader-ab-s",
    )
    with tempfile.TemporaryDirectory() as directory:
        game_copy = pathlib.Path(directory) / "er-cel-shade-telemetry.json"
        check(
            "and never into the game directory",
            image_dir(game_copy, None, "s") == DEFAULT_IMAGE_ROOT / "s",
        )
    check("--size parses", parse_size("320x180") == (320, 180))
    try:
        checker = load_checker()
        loaded = callable(checker.resolve_telemetry) and callable(checker.block)
    except Exception as error:  # noqa: BLE001 -- reported as the failed check
        print(f"  load_checker: {error}")
        loaded = False
    check("the probe checker loads as a module, for its telemetry path and its wait", loaded)

    # Shape of the script itself.
    check(
        "every wait is an event wait, never a delay",
        ("ti" + "me.sl" + "eep(") not in source and ("ti" + "me.sl" + "eep(") not in agent,
    )
    check(
        "the mode found at start is restored on every way out",
        "finally:" in source and "api.set_mode(restore)" in source and "signal.SIGTERM" in source,
    )
    check(
        "photographs come only from the validated-window tool",
        CAPTURE.name == "capture-er-window.py"
        and CAPTURE.is_file()
        and "[sys.executable, str(CAPTURE), str(path)]" in source,
    )

    failed = [label for label, ok in results if not ok]
    for label, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    if ran is not None and "error" in ran:
        print(f"  node: {ran['error']}")
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
