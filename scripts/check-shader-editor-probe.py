#!/usr/bin/env python3
"""Check what er-cel-shade's pixel probe says, from the telemetry file the DLL writes (design 14).

# What is read

Once a second the DLL's worker replaces `er-cel-shade-telemetry.json` (`src/exports.rs`): the
stats, `stage_active`, and the probe as the CPU read it back three presents behind -- each written
region's header (frame, variant id, size) and its 8x8 grid of samples, each with the colour before
the stage's layers (`pre`), after them (`post`) and one more word (`aux`). The stock cel layers
are layers like any other (design 17), so at the albedo and tonemap regions `pre` is the game's
own colour and their cel is in `post`; at the AO region `pre` still has the light bands and
outlines the port draws. A colour proof there stages its layer soloed, or with the cel layers of
its stage off, so `post` is the proof layer's work alone.

The game's own passes never write the probe. A region whose frame keeps advancing therefore proves
a variant of that port is bound, and one that stops proves the game's own pass is. Nothing here
needs a screenshot.

This waits for the file to be rewritten before taking each snapshot, so a dead game's last file
never passes: the first snapshot is a write made after this started, and each later one a write
made after the one before. The waits block on an inotify watch of the file's directory; the
`--timeout` only bounds a DLL that has stopped writing.

    python3 scripts/check-shader-editor-probe.py --expect live
    python3 scripts/check-shader-editor-probe.py --expect vanilla
    python3 scripts/check-shader-editor-probe.py --expect invert --variant-id 0x03000002
    python3 scripts/check-shader-editor-probe.py --expect masked-invert:depth:0.5..1 --region ao
    python3 scripts/check-shader-editor-probe.py --expect tint:3
    python3 scripts/check-shader-editor-probe.py --expect cost
    python3 scripts/check-shader-editor-probe.py --expect gpu-cost
    python3 scripts/check-shader-editor-probe.py --expect gpu-cost --baseline <telemetry.json> \
        --region tonemap --min-rise-us 100
    python3 scripts/check-shader-editor-probe.py --expect vertex-r1
    python3 scripts/check-shader-editor-probe.py --expect vertex-d
    python3 scripts/check-shader-editor-probe.py --selftest

# Expectations

* `live` -- every expected region was written in every snapshot, at most `--within` presents
  behind the present it was read at, and its frame advanced between each pair of snapshots. The
  expected regions are those whose stage has content (`stage_active`) and has a port variant
  (`ports_ready`), or `--region`. `--kinds` additionally requires each named port kind to have
  been the writer of its region. Fails while vanilla is set or a hook has faulted.
* `vanilla` -- the DLL reports vanilla in every snapshot, `redirected_binds` did not move, and
  every region that was ever written is now more than `--within` presents old and older than in
  the first snapshot.
* `identity` -- the one `--region` (tonemap by default) is not redirected because its stage has
  no content: vanilla is clear, the stage's bit is clear in `stage_active`, and the region has
  stopped advancing the same way `vanilla` asks. Design 14, P0.2a item 4.
* `invert` -- `post = 1 - pre` within 2/255 on at least `--min-samples` samples of a live region,
  and `pre` varies across the grid so the relation could have failed.
* `red` -- `post = (pre.r, 0, 0)` within 2/255 on at least `--min-samples` samples, with enough
  samples carrying green or blue for red to differ from the identity.
* `masked-invert:<mask>` -- `post = 1 - pre` on the samples the mask selects and `post = pre` on
  the rest, both to float precision, with at least one sample in each class.
* `tint:<bit>` -- on the tonemap samples whose aux carries selector bit `<bit>`, `post = pre *
  --tint` within 2/255; elsewhere `post = pre` to float precision. At least one sample must carry
  the bit, or the run is inconclusive (design 14, P3).
* `cost` -- the hook cost of design section 12 (design 14, P0.1 item 6), from the telemetry's
  per-detour timings, each a sum of nanoseconds over one call in `hook_sample_every` per recording
  thread. Each timing's mean, less `timer_floor_ns`, is held to its budget: `SetPipelineState` 20
  ns, `Reset`, `ClearState` and `Close` 40 ns, and a `Close` that records the probe copy 2 us. A
  timing with fewer than 16 timed calls is reported and not judged, except `SetPipelineState`,
  whose shortage makes the run inconclusive. Per frame, all of it together must stay within 0.2
  ms and `SetPipelineState` within 0.05 ms, counting each timed call for `hook_sample_every`.
* `gpu-cost` -- each port's GPU time a frame (design section 12, `src/gpu_time.rs`), from the
  telemetry's `gpu_time`: the timer exists and has a tick rate, its frame count advanced across
  the snapshots, spans were begun meanwhile, and in every expected region (as for `live`) some
  port kind of it reports a mean above 0 -- a port drawn every frame costs something. Each
  kind's mean is held to `--gpu-budget-us` (design 12: 1500). Fails with the timer off;
  inconclusive while vanilla is set (nothing is timed then), when more than a tenth of the spans
  begun meanwhile came back late (the read lag is too short for this GPU), or when any were
  dropped or refused. With `--baseline <telemetry.json>`, a copy of the file taken before a
  change, each `--kinds` port kind and each `--region` stage must also have risen by at least
  `--min-rise-us` over the baseline's mean: the A/B that gives a layer's own cost.

# Vertex expectations

The vertex proof of `docs/plans/er-shader-editor-vertex.md`, section 9, with a vertex layer live.
`vertex-d`, `vertex-f` and `vertex-s` drive the run themselves through the editor's control file,
`er-shader-editor/vertex-proof` (`--editor-dir`): the proof mode on, and the vertex stage held off
and let on in phases. The DLL then keeps every present's copy of the probe's vertex proof region,
and its cel worker appends them to `er-cel-shade-vertex-proof.jsonl` beside the telemetry, which
these read. The control file is put back as it was at the end, so with none before the proof mode
goes off again. Each run stays within the 30 s limit on any wait.

* `vertex-d` -- oracle D, with `proof_lift` (Hold up on): `--cycles` off/on cycles of
  `--phase-seconds` each. A geometry sample moves when its mean depth in an on phase differs from
  the off phase before it by more than 4 times its noise, in 90% of the cycles. Pass: at least half
  the geometry samples move. Comparing the off phases among themselves is the run's control: more
  than 2% moving there means the scene moved by itself, and the run is inconclusive.
* `vertex-f` -- oracle F, with `proof_lift_parity`: 2 s off, then 12 s on. Each frame's depth less
  the mean of its neighbours classifies it up or down per sample. Fail when more than 1% of the
  judged frames are mixed (under 95% of the moving samples agreeing) or the hole rate (normal
  agreement under 0.5 where calibration had over 0.9) rises over calibration's by more than 0.5%;
  inconclusive under `--frames` judged frames. The share against the AO port's own frame parity is
  printed and not judged.
* `vertex-s` -- oracle S, with `proof_lift` at Lift 50 and Hold up on: only the `Dpt` and `DptA`
  pipelines are redirected. Pass: at least 4 samples keep their depth (change under 1e-4 of it)
  while their light changes by at least a fifth in every cycle, correlated with the stage at 0.9.
  Fewer is inconclusive: the view needs shadows across it.
* `vertex-r1` -- gate R1 from the telemetry's `r1` counters between snapshots: fail when any
  present's command lists bound parameter blocks of different presents. A time-varying vertex
  layer can then disagree with itself across the passes of one frame; a static one cannot.

`--variant-id` and `--newer-than` add a condition on the region's header for the colour checks:
the exact id, or the same port kind with a larger build number in the low 24 bits.

# Masks

The selector text a layer's `select` holds, as `crates/er-cel-shade/src/editor/mask.rs` writes
and reads it, so a proof can be given the very text the layer uses: terms separated by spaces or
`&`, each optionally negated with a leading `!`, all of which must select a sample. A `~<n>px`
feather is read and left out; a feathered edge blends the samples on it, which `--max-mismatch`
can allow for.

* `everything`
* `depth:<lo>..<hi>`, with an optional `m` -- at `ao`, the pixel's view depth in metres, which the
  Lighting port writes into the aux (`er_probe_lighting_aux`, `-1` on the sky, which no depth range
  selects); at `albedo` and `tonemap`, the resolved plane's view depth as a half float in the high
  16 bits (design 5.4, from P2).
* `sky`, `geometry` -- at `ao`, the aux's sky marker, exactly as the Lighting stage's own sky test
  decides; at `albedo` and `tonemap`, the resolved plane's flags, bits 9 and 8 of the aux (design
  5.4, from P2).
* `luma:<lo>..<hi>` -- the Rec. 709 luminance of `pre`.
* `rect:<x0>,<y0>..<x1>,<y1>` (or `rect:<x0>,<y0>,<x1>,<y1>`) -- the sample's grid centre, as a
  screen coordinate from 0 to 1.
* `facing:<lo>..<hi>` -- read, but the probe carries no normal, so every sample is undecided and
  the run inconclusive.
* `bits:<mask>` -- any of the selector bits in the aux's low byte (P3); the checker's own.

A low bound above the high one is swapped, as the editor does.

# Output and exit code

Numbers only: one `key=value` line per snapshot set and per region, then `verdict=pass`,
`verdict=fail` or `verdict=inconclusive` with a `why=` token. Exit 0 on a pass, 1 on a fail,
2 when the telemetry or the scene cannot decide.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import struct
import sys
import tempfile
import time
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

# The inotify watch and the game's directory, shared with the launchers.
import er_run_lib  # noqa: E402

# The one table of run artifacts and how a reader finds this run's copy.
from er_artifact_env import ARTIFACT_ENV, RUN_ARTIFACT_DIR_ENV, resolve_artifact  # noqa: E402

TELEMETRY_ENV = "ER_QUICKLOAD_CEL_SHADE_TELEMETRY_PATH"
TELEMETRY_NAME = ARTIFACT_ENV[TELEMETRY_ENV]
# The `"schema"` the DLL writes; a different number is a layout this reader does not know.
SCHEMA = 1

# The probe's geometry and readback ring, mirrored from `crates/er-cel-shade/src/probe.rs`. The
# selftest reads that file and holds these to it.
GRID = 8
SAMPLES = GRID * GRID
READ_LAG_PRESENTS = 3
READBACK_SLOTS = 8

# How many presents behind a live region's header may be. The CPU reads the readback slot three
# presents behind the copy, so a variant bound every frame reads 3 or a little more; a slot that no
# copy reached since the ring last went round is at least 3 + 8 = 11 behind.
LIVE_WITHIN = READ_LAG_PRESENTS + READBACK_SLOTS - 1
# A stalled region's age grows by one per present only once the ring has gone round: for the first
# 8 presents after the last copy, each present reads a slot written 8 earlier and the age sits at 11.
# Fewer presents than this between the first and last snapshot cannot show a stall.
MIN_PRESENTS_FOR_STALL = READBACK_SLOTS + 1

# Snapshots taken by default: two intervals, so "advanced" means advanced twice.
SNAPSHOTS = 3
# Design 14, P0.2a item 2: the relation holds on at least 48 of the 64 samples.
MIN_SAMPLES = 48
# Design 14: within 2/255 for the colour relations.
COLOUR_TOLERANCE = 2 / 255
# "Exactly", for the masked relations: fp32 rounding of `lerp(x, y, 1)` and of `1 - x`, scaled by
# the value's magnitude because the light buffer's colours are not bounded by 1.
EXACT_TOLERANCE = 1e-5
# `pre`'s luminance must vary at least this much across the grid, or every relation is trivially
# met or trivially missed and the check proves nothing.
MIN_PRE_VARIANCE = 1e-6
# Samples on which a colour relation's expected value differs from `pre` by more than twice the
# tolerance. Fewer than this and the relation cannot be told from the identity.
MIN_DISTINGUISHED = 8

DEFAULT_TIMEOUT_SECONDS = 12.0
# The cap on `--timeout`, the repo's limit on any non-game wait (scripts/check-no-timeouts.py).
MAX_TIMEOUT_SECONDS = 30.0
# One slice of a wait: an inotify event ends it at once; this only bounds a quiet directory.
WAIT_SLICE_SECONDS = 0.5

# The tint `tint:<bit>` multiplies by, unless `--tint` names another.
DEFAULT_TINT = (1.0, 0.0, 1.0)

REGIONS = ("ao", "albedo", "tonemap")
# `Stage::bit()` of the stage each region belongs to: Lighting, Albedo, Tonemap.
STAGE_BITS = {"ao": 1 << 0, "albedo": 1 << 1, "tonemap": 1 << 2}
# `PortKind`, in order: the number is the variant id's top byte and the `ports_ready` index.
PORT_KINDS = ("ao", "ao_ssr", "cmb_albedo", "tonemap", "tonemap_lut")
KIND_REGION = ("ao", "ao", "albedo", "tonemap", "tonemap")

# The resolved plane's word (design 5.4): selector bits in the low byte, then the flags, then the
# view depth as a half float.
# Design section 12's budgets for what one call adds, in nanoseconds, by the telemetry's `hooks`
# names, which are `Timing::name` in crates/er-cel-shade/src/cmdlist.rs.
COST_BUDGET_NS = {
    "SetPipelineState": 20,
    "Reset": 40,
    "ClearState": 40,
    "Close": 40,
    "CloseProbeCopy": 2_000,
}
# Section 12 per frame: every hook together, and `SetPipelineState` alone.
FRAME_BUDGET_NS = 200_000
SET_PSO_FRAME_BUDGET_NS = 50_000
# A timing with fewer timed calls than this is reported but not judged.
MIN_COST_SAMPLES = 16

# Design section 12's budget for one port's GPU time a frame, in microseconds, and how many
# presents behind the DLL reads its timestamps: `PORT_BUDGET_US` and `READ_LAG_PRESENTS` in
# crates/er-cel-shade/src/gpu_time.rs, which the selftest reads.
GPU_PORT_BUDGET_US = 1500.0
GPU_READ_LAG_PRESENTS = 5
# The stage each region belongs to, by the names the telemetry's `gpu_time.stages` carries.
REGION_STAGE = {"ao": "lighting", "albedo": "albedo", "tonemap": "tonemap"}
# A rise smaller than this over a `--baseline` is not called a rise by default.
DEFAULT_MIN_RISE_US = 50.0
# More late spans than this share of the spans begun means the read lag is too short.
MAX_LATE_SHARE = 0.1

RESOLVED_BITS_MASK = 0xFF
RESOLVED_GEOMETRY_FLAG = 1 << 8
RESOLVED_SKY_FLAG = 1 << 9

U32 = 1 << 32
HALF_U32 = 1 << 31

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2

# The vertex proof (docs/plans/er-shader-editor-vertex.md, section 9). The names are the Rust's:
# `PROOF_FILE_NAME` and `Control::FILE_NAME` in crates/er-cel-shade/src/vertex/mod.rs, the editor's
# directory in editor/project.rs, and the pass tokens in vertex/names.rs, whose order is each
# pass's bit. The selftest holds them to those files.
VERTEX_EXPECTATIONS = ("vertex-d", "vertex-f", "vertex-s", "vertex-r1")
PROOF_NAME = "er-cel-shade-vertex-proof.jsonl"
CONTROL_NAME = "vertex-proof"
EDITOR_DIRECTORY = "er-shader-editor"
PASS_TOKENS = ("Gbuf", "GBuf", "Dpt", "DptA", "Velo", "Fwd", "Pnt", "PntCol", "FurDir", "Spec")
ALL_PASSES = (1 << len(PASS_TOKENS)) - 1
# Oracle S moves the depth pipelines alone: every shadow caster's shadow, and nothing the main view
# draws in its G-buffer.
SHADOW_PASSES = (1 << PASS_TOKENS.index("Dpt")) | (1 << PASS_TOKENS.index("DptA"))
# Probed frames left out at the start of each run of one vertex state: a present reads the copy
# written READ_LAG_PRESENTS before it, the switch reaches the stage bits at the next present, and
# the design skips two more after each edge.
SKIP_AFTER_SWITCH = READ_LAG_PRESENTS + 3
# A sample moved when its depth changed by more than this many times its noise.
NOISE_FACTOR = 4.0
# The least noise a sample is given, relative to its depth: a still scene can repeat a depth to
# the bit, and no noise at all would call any rounding a move.
NOISE_FLOOR_RELATIVE = 1e-4
# Oracle D: a sample moves in at least this share of the cycles; at least this share of the
# geometry samples moves; and comparing the off phases among themselves moves at most this share,
# which is the run's own control: a scene that moves with the layer off proves nothing.
CYCLE_SHARE = 0.9
D_MOVING_SHARE = 0.5
D_NULL_SHARE = 0.02
MIN_GEOMETRY = 16
# Oracle F: a frame is mixed when fewer than this share of its moving samples agree; at most this
# share of frames may be mixed; a hole is a normal agreement under F_HOLE_DOT where calibration
# was over F_CALIBRATED_DOT, and the hole rate may rise at most F_HOLE_EXCESS over calibration's.
F_AGREE = 0.95
F_MIXED_SHARE = 0.01
F_HOLE_DOT = 0.5
F_CALIBRATED_DOT = 0.9
F_HOLE_EXCESS = 0.005
F_FRAMES = 600
F_MIN_MOVING = 8
F_MIN_CALIBRATION_FRAMES = 30
# Oracle S: at least this many samples keep their depth to this relative change while their light
# changes by at least this share, in phase with the layer to this correlation.
S_MIN_SAMPLES = 4
S_DEPTH_KEPT = 1e-4
S_LIGHT_CHANGE = 0.2
S_CORRELATION = 0.9
# R1: presents between the first and the last snapshot before a share of mixed frames means much.
R1_MIN_FRAMES = 60
# How the vertex runs are paced. Each run stays inside MAX_TIMEOUT_SECONDS.
DEFAULT_CYCLES = 5
S_DEFAULT_CYCLES = 4
DEFAULT_PHASE_SECONDS = 1.5
CALIBRATION_SECONDS = 2.0
F_SECONDS = 12.0
# The cel worker appends the proof once a second; the run waits this long for the last append.
FLUSH_WAIT_SECONDS = 2.5


class TelemetryError(ValueError):
    """A telemetry document this reader cannot use."""


# Telemetry


@dataclass(frozen=True)
class Sample:
    index: int
    # `None` when the DLL wrote `null`: a NaN or an infinity, which JSON cannot spell.
    pre: tuple[float, float, float] | None
    post: tuple[float, float, float] | None
    aux: int

    @property
    def uv(self) -> tuple[float, float]:
        """The grid point's centre as a screen coordinate, the way `er_probe_record` places it."""
        row, col = divmod(self.index, GRID)
        return ((2 * col + 1) / (2 * GRID), (2 * row + 1) / (2 * GRID))


@dataclass(frozen=True)
class Region:
    name: str
    variant_id: int
    frame: int
    width: int
    height: int
    stale: int
    samples: tuple[Sample, ...]

    @property
    def kind(self) -> int:
        return self.variant_id >> 24


@dataclass(frozen=True)
class GpuPort:
    """One port kind's GPU time, microseconds a frame; `None` while the DLL has no tick rate."""

    us_last: float | None
    us_mean: float | None
    spans_last: int
    bound_last: str


@dataclass(frozen=True)
class GpuTime:
    """The telemetry's `gpu_time` object (`render_gpu` in crates/er-cel-shade/src/exports.rs)."""

    frequency_hz: int
    source: str
    read_lag_presents: int
    budget_us: float
    frame: int | None
    frames: int
    unknown_frames: int
    mean_frames: int
    spans_begun: int
    spans_dropped: int
    late: int
    refused: int
    # By port kind name, and by stage name: (last, mean).
    ports: dict[str, GpuPort] = field(default_factory=dict)
    stages: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)


# What a document says of the GPU timer: no `gpu_time` key at all (a build before it), or the key
# written `null` (the timer is off).
GPU_ABSENT = "absent"
GPU_OFF = "off"


@dataclass(frozen=True)
class Snapshot:
    written_ms: int
    present: int
    mode: int
    vanilla: int
    stage_active: int
    hook_faulted: bool
    redirected_binds: int
    ports_ready: tuple[int, ...]
    regions: dict[str, Region] = field(default_factory=dict)
    # Per timing name: (nanoseconds, timed calls). Empty in a document without hook timings.
    hooks: dict[str, tuple[int, int]] = field(default_factory=dict)
    timer_floor_ns: int = 0
    sample_every: int = 0
    # The ports' GPU time, or GPU_ABSENT / GPU_OFF.
    gpu: GpuTime | str = GPU_ABSENT

    def age(self, region: str) -> int | None:
        """Presents between the read and the region's header frame, which holds the low 32 bits."""
        record = self.regions.get(region)
        if record is None:
            return None
        return (self.present - record.frame) % U32


def _colour(value) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3:
        raise TelemetryError(f"a colour is not three numbers: {value!r}")
    if any(channel is None for channel in value):
        return None
    if not all(isinstance(channel, (int, float)) for channel in value):
        raise TelemetryError(f"a colour is not three numbers: {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


def _int(document: dict, key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryError(f"{key} is not an integer: {value!r}")
    return value


def _micros(document: dict, key: str) -> float | None:
    value = document.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"{key} is not a number: {value!r}")
    return float(value)


def parse_gpu(value) -> GpuTime | str:
    """The `gpu_time` value of a document: GPU_OFF for `null`."""
    if value is None:
        return GPU_OFF
    if not isinstance(value, dict):
        raise TelemetryError(f"gpu_time is not an object: {value!r}")
    ports: dict[str, GpuPort] = {}
    for entry in value.get("ports") or []:
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if kind not in PORT_KINDS:
            raise TelemetryError(f"unknown gpu_time port kind {kind!r}")
        ports[kind] = GpuPort(
            us_last=_micros(entry, "us_last"),
            us_mean=_micros(entry, "us_mean"),
            spans_last=_int(entry, "spans_last"),
            bound_last=str(entry.get("bound_last")),
        )
    stages: dict[str, tuple[float | None, float | None]] = {}
    for entry in value.get("stages") or []:
        stage = entry.get("stage") if isinstance(entry, dict) else None
        if stage not in REGION_STAGE.values():
            raise TelemetryError(f"unknown gpu_time stage {stage!r}")
        stages[stage] = (_micros(entry, "us_last"), _micros(entry, "us_mean"))
    frame = value.get("frame")
    return GpuTime(
        frequency_hz=_int(value, "frequency_hz"),
        source=str(value.get("source")),
        read_lag_presents=_int(value, "read_lag_presents"),
        budget_us=_micros(value, "budget_us") or 0.0,
        frame=None if frame is None else _int(value, "frame"),
        frames=_int(value, "frames"),
        unknown_frames=_int(value, "unknown_frames"),
        mean_frames=_int(value, "mean_frames"),
        spans_begun=_int(value, "spans_begun"),
        spans_dropped=_int(value, "spans_dropped"),
        late=_int(value, "late"),
        refused=_int(value, "refused"),
        ports=ports,
        stages=stages,
    )


def parse_snapshot(document: dict) -> Snapshot:
    """The parts of one telemetry document the checks use."""
    if not isinstance(document, dict):
        raise TelemetryError("the telemetry is not a JSON object")
    if document.get("schema") != SCHEMA:
        raise TelemetryError(f"schema {document.get('schema')!r}, this reader knows {SCHEMA}")
    stats = document.get("stats")
    probe = document.get("probe")
    if not isinstance(stats, dict) or not isinstance(probe, dict):
        raise TelemetryError("no stats or probe object")
    ports_ready = stats.get("ports_ready")
    if not isinstance(ports_ready, list) or len(ports_ready) != len(PORT_KINDS):
        raise TelemetryError(f"ports_ready is not {len(PORT_KINDS)} counts: {ports_ready!r}")
    regions: dict[str, Region] = {}
    for entry in probe.get("regions") or []:
        name = entry.get("region")
        if name not in REGIONS:
            raise TelemetryError(f"unknown probe region {name!r}")
        samples = []
        for sample in entry.get("samples") or []:
            index = _int(sample, "index")
            if not 0 <= index < SAMPLES:
                raise TelemetryError(f"sample index {index} is off the grid")
            samples.append(
                Sample(
                    index=index,
                    pre=_colour(sample.get("pre")),
                    post=_colour(sample.get("post")),
                    aux=_int(sample, "aux"),
                )
            )
        regions[name] = Region(
            name=name,
            variant_id=_int(entry, "variant_id"),
            frame=_int(entry, "frame"),
            width=_int(entry, "width"),
            height=_int(entry, "height"),
            stale=_int(entry, "stale"),
            samples=tuple(samples),
        )
    hooks: dict[str, tuple[int, int]] = {}
    for entry in document.get("hooks") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("detour"), str):
            raise TelemetryError(f"a hook timing without a name: {entry!r}")
        hooks[entry["detour"]] = (_int(entry, "ns_sum"), _int(entry, "samples"))
    return Snapshot(
        written_ms=_int(document, "written_ms"),
        present=_int(stats, "presents"),
        mode=_int(stats, "mode"),
        vanilla=_int(stats, "vanilla"),
        stage_active=_int(document, "stage_active"),
        hook_faulted=bool(document.get("hook_faulted")),
        redirected_binds=_int(stats, "redirected_binds"),
        ports_ready=tuple(int(count) for count in ports_ready),
        regions=regions,
        hooks=hooks,
        timer_floor_ns=_int(document, "timer_floor_ns") if "timer_floor_ns" in document else 0,
        sample_every=_int(document, "hook_sample_every") if "hook_sample_every" in document else 0,
        gpu=parse_gpu(document["gpu_time"]) if "gpu_time" in document else GPU_ABSENT,
    )


def read_snapshot(path: pathlib.Path) -> Snapshot:
    return parse_snapshot(json.loads(path.read_text(encoding="utf-8")))


def resolve_telemetry(
    explicit: pathlib.Path | None = None, artifact_dir: pathlib.Path | None = None
) -> pathlib.Path:
    """Where this run's telemetry is.

    An explicit path wins, then a run directory the caller names, then the launcher's own redirect.
    Otherwise the newest copy among every run directory and the game directory: the DLL rewrites
    its file once a second, so the newest one is the run that is writing now, and an older copy is
    a finished run's last word.
    """
    if explicit is not None:
        return explicit
    game = er_run_lib.game_dir()
    if artifact_dir is not None:
        return resolve_artifact(TELEMETRY_NAME, game, prefer=artifact_dir)
    if os.environ.get(TELEMETRY_ENV):
        return pathlib.Path(os.environ[TELEMETRY_ENV])
    candidates = []
    if os.environ.get(RUN_ARTIFACT_DIR_ENV):
        candidates.append(pathlib.Path(os.environ[RUN_ARTIFACT_DIR_ENV]) / TELEMETRY_NAME)
    if er_run_lib.RUN_STATE_ROOT.is_dir():
        candidates.extend(er_run_lib.RUN_STATE_ROOT.glob(f"*/{TELEMETRY_NAME}"))
    candidates.append(game / TELEMETRY_NAME)
    newest: tuple[float, pathlib.Path] | None = None
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, candidate)
    return newest[1] if newest is not None else game / TELEMETRY_NAME


def block(watch: er_run_lib.DirectoryWatch, seconds: float) -> None:
    """Block for at most `seconds`, returning early on a write in the watched directory.

    Without inotify, the wait is a pidfd on the game, which ends early if the game exits -- the one
    other event worth waking for -- and otherwise on the slice.
    """
    if watch.available:
        watch.wait(seconds)
        return
    pids = er_run_lib.find_game_pids()
    er_run_lib.wait_for_exit(pids[0] if pids else os.getpid(), seconds)


def current_written_ms(path: pathlib.Path) -> int | None:
    try:
        return read_snapshot(path).written_ms
    except (OSError, ValueError):
        return None


def collect(
    path: pathlib.Path, count: int, timeout: float, fresh_after: int | None
) -> list[Snapshot]:
    """Up to `count` snapshots, each from a write after the last; the first after `fresh_after`.

    `fresh_after` is the `written_ms` of the file as it stood when the caller began, so the first
    snapshot is never a write that happened before. Fewer than `count` come back when the DLL stops
    writing before `timeout`.
    """
    return [
        parse_snapshot(document)
        for document in collect_documents(path, count, timeout, fresh_after, parse_snapshot)
    ]


def collect_documents(
    path: pathlib.Path, count: int, timeout: float, fresh_after: int | None, accept=None
) -> list[dict]:
    """[`collect`]'s documents as JSON objects; each must have a `written_ms`, and `accept`, when
    given, must take it without raising `ValueError`."""
    deadline = time.monotonic() + min(max(timeout, 0.0), MAX_TIMEOUT_SECONDS)
    documents: list[dict] = []
    seen = fresh_after
    with er_run_lib.DirectoryWatch(path.parent) as watch:
        while len(documents) < count:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                written = _int(document, "written_ms")
                if accept is not None:
                    accept(document)
            except (OSError, ValueError, AttributeError):
                document, written = None, None
            if document is not None and written != seen:
                documents.append(document)
                seen = written
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            block(watch, min(WAIT_SLICE_SECONDS, remaining))
    return documents


# Masks


# What the Lighting port writes in an `ao` sample's aux on the sky (`er_probe_lighting_aux`).
AO_SKY_DEPTH = -1.0


def depth_of(region: str, aux: int) -> float:
    """The depth a sample's aux carries in `region`'s units (see the module docs)."""
    if region == "ao":
        return struct.unpack("<f", struct.pack("<I", aux & 0xFFFF_FFFF))[0]
    return struct.unpack("<e", struct.pack("<H", (aux >> 16) & 0xFFFF))[0]


def is_sky(region: str, aux: int) -> bool:
    """Whether the sample is on the sky: at `ao` the Lighting stage's own test, which the port
    writes as a negative depth, and elsewhere the resolved plane's flag."""
    if region == "ao":
        return depth_of(region, aux) < 0
    return bool(aux & RESOLVED_SKY_FLAG)


def is_geometry(region: str, aux: int) -> bool:
    if region == "ao":
        depth = depth_of(region, aux)
        return depth >= 0 and math.isfinite(depth)
    return bool(aux & RESOLVED_GEOMETRY_FLAG)


def luma(colour: tuple[float, float, float]) -> float:
    return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]


def _number(text: str, term: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as error:
        raise ValueError(f"{term}: {error}") from None
    if not math.isfinite(value):
        raise ValueError(f"{term}: {text!r} is not a finite number")
    return value


def _range(text: str, term: str) -> tuple[float, float]:
    low, sep, high = text.partition("..")
    if not sep:
        raise ValueError(f"{term}: expected <lo>..<hi>")
    low_value, high_value = _number(low, term), _number(high, term)
    return (min(low_value, high_value), max(low_value, high_value))


def _rect(text: str, term: str) -> tuple[float, float, float, float]:
    """`x0,y0..x1,y1`, as the editor writes it, or the four numbers comma-separated."""
    if ".." in text:
        start, _, end = text.partition("..")
        corners = [start.split(","), end.split(",")]
        if any(len(corner) != 2 for corner in corners):
            raise ValueError(f"{term}: expected rect:<x0>,<y0>..<x1>,<y1>")
        parts = corners[0] + corners[1]
    else:
        parts = text.split(",")
        if len(parts) != 4:
            raise ValueError(f"{term}: expected rect:<x0>,<y0>..<x1>,<y1>")
    x0, y0, x1, y1 = (_number(part, term) for part in parts)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


@dataclass(frozen=True)
class Term:
    kind: str
    negate: bool
    args: tuple = ()

    def matches(self, sample: Sample, region: str) -> bool | None:
        """Whether the term selects `sample`; `None` when the sample cannot say."""
        hit: bool | None
        if self.kind == "everything":
            hit = True
        elif self.kind == "depth":
            depth = depth_of(region, sample.aux)
            if math.isnan(depth):
                hit = None
            elif is_sky(region, sample.aux):
                hit = False
            else:
                hit = self.args[0] <= depth <= self.args[1]
        elif self.kind == "luma":
            hit = None if sample.pre is None else self.args[0] <= luma(sample.pre) <= self.args[1]
        elif self.kind == "rect":
            u, v = sample.uv
            x0, y0, x1, y1 = self.args
            hit = x0 <= u <= x1 and y0 <= v <= y1
        elif self.kind == "sky":
            hit = is_sky(region, sample.aux)
        elif self.kind == "geometry":
            hit = is_geometry(region, sample.aux)
        elif self.kind == "facing":
            hit = None
        elif self.kind == "bits":
            hit = bool(sample.aux & self.args[0] & RESOLVED_BITS_MASK)
        else:
            raise ValueError(f"unknown mask term {self.kind}")
        if hit is None:
            return None
        return not hit if self.negate else hit


@dataclass(frozen=True)
class Mask:
    text: str
    terms: tuple[Term, ...]

    def matches(self, sample: Sample, region: str) -> bool | None:
        result = True
        for term in self.terms:
            hit = term.matches(sample, region)
            if hit is None:
                return None
            result = result and hit
        return result


FEATHER = re.compile(r"~(\d+(?:\.\d*)?|\.\d+)(?:px)?")


def parse_mask(text: str) -> Mask:
    """A selector in the editor's text form (see the module docs)."""
    terms = []
    for term in text.replace("&", " & ").split():
        if term == "&":
            continue
        if term.startswith("~"):
            if not FEATHER.fullmatch(term):
                raise ValueError(f"{term!r} is not a feather like ~2px")
            continue
        negate = term.startswith("!")
        term = term[1:] if negate else term
        name, _, rest = term.partition(":")
        if name in ("everything", "sky", "geometry"):
            if rest:
                raise ValueError(f"{name} takes no argument")
            terms.append(Term(name, negate))
        elif name == "depth":
            # Metres, as the editor writes it; the `m` is optional.
            terms.append(Term(name, negate, _range(rest.removesuffix("m"), term)))
        elif name in ("luma", "facing"):
            terms.append(Term(name, negate, _range(rest, term)))
        elif name == "rect":
            terms.append(Term(name, negate, _rect(rest, term)))
        elif name == "bits":
            try:
                bits = int(rest, 0)
            except ValueError:
                raise ValueError(f"{term}: expected bits:<mask>") from None
            if not 0 < bits <= RESOLVED_BITS_MASK:
                raise ValueError(f"{term}: selector bits are the low byte")
            terms.append(Term(name, negate, (bits,)))
        else:
            raise ValueError(f"unknown mask term {term!r}")
    if not terms:
        raise ValueError("an empty mask")
    return Mask(text, tuple(terms))


# Expectations


@dataclass(frozen=True)
class Expectation:
    kind: str
    mask: Mask | None = None
    bit: int | None = None

    @property
    def colour(self) -> bool:
        return self.kind in ("invert", "red", "masked-invert", "tint")


def parse_expectation(text: str) -> Expectation:
    if (
        text in ("live", "vanilla", "identity", "invert", "red", "cost", "gpu-cost")
        + VERTEX_EXPECTATIONS
    ):
        return Expectation(text)
    if text.startswith("masked-invert:"):
        return Expectation("masked-invert", mask=parse_mask(text[len("masked-invert:") :]))
    if text.startswith("tint:"):
        try:
            bit = int(text[len("tint:") :], 0)
        except ValueError:
            raise ValueError(f"{text}: expected tint:<bit>") from None
        if not 0 <= bit < 8:
            raise ValueError(f"{text}: a selector bit is 0 to 7")
        return Expectation("tint", bit=bit)
    raise ValueError(f"unknown expectation {text!r}")


def parse_kinds(text: str | None) -> list[int]:
    kinds = []
    for word in (text or "").split(","):
        word = word.strip()
        if not word:
            continue
        if word.isdigit() and int(word) < len(PORT_KINDS):
            kinds.append(int(word))
        elif word in PORT_KINDS:
            kinds.append(PORT_KINDS.index(word))
        else:
            raise ValueError(f"unknown port kind {word!r}; one of {', '.join(PORT_KINDS)}")
    return kinds


def parse_regions(text: str | None) -> list[str]:
    regions = []
    for word in (text or "").split(","):
        word = word.strip()
        if not word:
            continue
        if word not in REGIONS:
            raise ValueError(f"unknown region {word!r}; one of {', '.join(REGIONS)}")
        if word not in regions:
            regions.append(word)
    return regions


@dataclass
class Options:
    regions: list[str] = field(default_factory=list)
    kinds: list[int] = field(default_factory=list)
    within: int = LIVE_WITHIN
    min_samples: int = MIN_SAMPLES
    variant_id: int | None = None
    newer_than: int | None = None
    tint: tuple[float, float, float] = DEFAULT_TINT
    max_mismatch: int = 0
    gpu_budget_us: float = GPU_PORT_BUDGET_US
    # `gpu-cost`: a snapshot from before a change, and the rise it must show.
    baseline: Snapshot | None = None
    min_rise_us: float = DEFAULT_MIN_RISE_US


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    verdict: str = "pass"
    why: str = ""

    def fail(self, why: str) -> "Report":
        if self.verdict == "pass" or (self.verdict == "inconclusive" and why):
            self.verdict, self.why = "fail", why
        return self

    def inconclusive(self, why: str) -> "Report":
        if self.verdict == "pass":
            self.verdict, self.why = "inconclusive", why
        return self

    @property
    def exit_code(self) -> int:
        return {"pass": EXIT_PASS, "fail": EXIT_FAIL}.get(self.verdict, EXIT_INCONCLUSIVE)


def _join(values) -> str:
    return ",".join("-" if value is None else str(value) for value in values)


@dataclass(frozen=True)
class Liveness:
    """One region across the snapshots."""

    frames: tuple[int | None, ...]
    ages: tuple[int | None, ...]
    kinds: tuple[int | None, ...]
    advanced: int

    @property
    def pairs(self) -> int:
        return len(self.frames) - 1

    def live(self, within: int) -> bool:
        return (
            all(frame is not None for frame in self.frames)
            and all(age is not None and age <= within for age in self.ages)
            and self.advanced == self.pairs
        )

    @property
    def written(self) -> bool:
        return any(frame is not None for frame in self.frames)

    def stalled(self, within: int) -> bool:
        """No fresh copy reached the region: absent now, or old and getting older."""
        last, first = self.ages[-1], self.ages[0]
        if last is None:
            return True
        return last > within and last > (first if first is not None else -1)


def liveness(snapshots: list[Snapshot], region: str) -> Liveness:
    frames = tuple(
        snapshot.regions[region].frame if region in snapshot.regions else None
        for snapshot in snapshots
    )
    advanced = 0
    for before, after in zip(frames, frames[1:]):
        if before is not None and after is not None and 0 < (after - before) % U32 < HALF_U32:
            advanced += 1
    return Liveness(
        frames=frames,
        ages=tuple(snapshot.age(region) for snapshot in snapshots),
        kinds=tuple(
            snapshot.regions[region].kind if region in snapshot.regions else None
            for snapshot in snapshots
        ),
        advanced=advanced,
    )


def region_kinds(region: str) -> list[int]:
    return [kind for kind, owner in enumerate(KIND_REGION) if owner == region]


def auto_expected(snapshot: Snapshot) -> list[str]:
    """The regions a redirect should be writing: stage has content and a port variant exists."""
    return [
        region
        for region in REGIONS
        if snapshot.stage_active & STAGE_BITS[region]
        and any(snapshot.ports_ready[kind] > 0 for kind in region_kinds(region))
    ]


def snapshot_lines(snapshots: list[Snapshot]) -> list[str]:
    return [
        f"snapshots={len(snapshots)} presents={_join(s.present for s in snapshots)} "
        f"mode={_join(s.mode for s in snapshots)} vanilla={_join(s.vanilla for s in snapshots)} "
        f"stage_active={_join(s.stage_active for s in snapshots)} "
        f"redirected_binds={_join(s.redirected_binds for s in snapshots)} "
        f"ports_ready={_join(snapshots[-1].ports_ready)} "
        f"hook_faulted={int(any(s.hook_faulted for s in snapshots))}"
    ]


def region_line(region: str, lv: Liveness, **extra) -> str:
    tail = " ".join(f"{key}={value}" for key, value in extra.items())
    line = (
        f"region={region} frames={_join(lv.frames)} ages={_join(lv.ages)} "
        f"kinds={_join(lv.kinds)} advanced={lv.advanced}/{lv.pairs}"
    )
    return f"{line} {tail}" if tail else line


def _close(a: tuple[float, ...], b: tuple[float, ...], tolerance: float, scaled: bool) -> bool:
    for x, y in zip(a, b):
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        limit = tolerance * max(1.0, abs(x), abs(y)) if scaled else tolerance
        if abs(x - y) > limit:
            return False
    return True


def invert_of(colour: tuple[float, float, float]) -> tuple[float, float, float]:
    return (1.0 - colour[0], 1.0 - colour[1], 1.0 - colour[2])


def red_of(colour: tuple[float, float, float]) -> tuple[float, float, float]:
    return (colour[0], 0.0, 0.0)


def check_live(snapshots: list[Snapshot], options: Options) -> Report:
    report = Report(snapshot_lines(snapshots))
    last = snapshots[-1]
    expected = list(options.regions) or auto_expected(last)
    for kind in options.kinds:
        if KIND_REGION[kind] not in expected:
            expected.append(KIND_REGION[kind])
    for region in REGIONS:
        lv = liveness(snapshots, region)
        record = last.regions.get(region)
        identical = (
            sum(
                1
                for s in record.samples
                if s.pre is not None and s.post is not None and _close(s.pre, s.post, 0.0, False)
            )
            if record is not None
            else 0
        )
        report.lines.append(
            region_line(
                region,
                lv,
                expected=int(region in expected),
                live=int(lv.live(options.within)),
                samples=len(record.samples) if record else 0,
                post_eq_pre=identical,
            )
        )
        if region not in expected:
            continue
        if not lv.live(options.within):
            report.fail(f"{region}_not_live")
        elif not all(kind in region_kinds(region) for kind in lv.kinds):
            report.fail(f"{region}_written_by_foreign_kind")
    for kind in options.kinds:
        seen = liveness(snapshots, KIND_REGION[kind]).kinds
        report.lines.append(f"kind={kind} name={PORT_KINDS[kind]} seen={int(kind in seen)}")
        if kind not in seen:
            report.fail(f"kind_{PORT_KINDS[kind]}_not_seen")
    if any(s.vanilla for s in snapshots):
        report.fail("vanilla_set")
    if any(s.hook_faulted for s in snapshots):
        report.fail("hook_faulted")
    if not expected:
        report.inconclusive("no_stage_redirected")
    return report


def presents_advanced(snapshots: list[Snapshot]) -> int:
    return snapshots[-1].present - snapshots[0].present


def check_vanilla(snapshots: list[Snapshot], options: Options) -> Report:
    report = Report(snapshot_lines(snapshots))
    binds = snapshots[-1].redirected_binds - snapshots[0].redirected_binds
    advanced = presents_advanced(snapshots)
    report.lines.append(f"redirected_binds_delta={binds} presents_delta={advanced}")
    for region in options.regions or list(REGIONS):
        lv = liveness(snapshots, region)
        report.lines.append(
            region_line(
                region, lv, written=int(lv.written), stalled=int(lv.stalled(options.within))
            )
        )
        if lv.written and not lv.stalled(options.within):
            report.fail(f"{region}_still_advancing")
    if any(s.vanilla != 1 for s in snapshots):
        report.fail("vanilla_not_set")
    if binds != 0:
        report.fail("redirected_binds_moved")
    if advanced < MIN_PRESENTS_FOR_STALL:
        report.inconclusive("too_few_presents")
    return report


def single_region(options: Options) -> str:
    if len(options.regions) > 1:
        raise ValueError("this expectation checks one region")
    return options.regions[0] if options.regions else "tonemap"


def check_identity(snapshots: list[Snapshot], options: Options) -> Report:
    region = single_region(options)
    report = Report(snapshot_lines(snapshots))
    lv = liveness(snapshots, region)
    bit = STAGE_BITS[region]
    report.lines.append(
        region_line(
            region,
            lv,
            stage_bit=_join(int(bool(s.stage_active & bit)) for s in snapshots),
            written=int(lv.written),
            stalled=int(lv.stalled(options.within)),
        )
    )
    if any(s.vanilla for s in snapshots):
        report.fail("vanilla_set")
    if any(s.stage_active & bit for s in snapshots):
        report.fail(f"{region}_stage_has_content")
    if lv.written and not lv.stalled(options.within):
        report.fail(f"{region}_still_advancing")
    if presents_advanced(snapshots) < MIN_PRESENTS_FOR_STALL:
        report.inconclusive("too_few_presents")
    return report


def variant_conditions(record: Region, options: Options, report: Report) -> None:
    if options.variant_id is not None:
        report.lines.append(
            f"variant_id=0x{record.variant_id:08x} wanted=0x{options.variant_id & 0xFFFF_FFFF:08x}"
        )
        if record.variant_id != options.variant_id:
            report.fail("variant_id")
    if options.newer_than is not None:
        older = options.newer_than & 0xFFFF_FFFF
        newer = (record.variant_id >> 24) == (older >> 24) and (
            record.variant_id & 0xFF_FFFF
        ) > (older & 0xFF_FFFF)
        report.lines.append(f"variant_id=0x{record.variant_id:08x} newer_than=0x{older:08x}")
        if not newer:
            report.fail("variant_not_newer")


def pre_variance(samples: list[Sample]) -> float:
    values = [luma(s.pre) for s in samples if s.pre is not None]
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def check_colour(
    expectation: Expectation, snapshots: list[Snapshot], options: Options
) -> Report:
    region = single_region(options)
    report = Report(snapshot_lines(snapshots))
    lv = liveness(snapshots, region)
    live = lv.live(options.within)
    report.lines.append(region_line(region, lv, live=int(live)))
    if not live:
        return report.fail(f"{region}_not_live")
    record = snapshots[-1].regions[region]
    variant_conditions(record, options, report)
    samples = [s for s in record.samples if s.pre is not None and s.post is not None]
    variance = pre_variance(samples)
    if len(samples) < options.min_samples:
        report.lines.append(f"samples={len(samples)} needed={options.min_samples}")
        return report.inconclusive("too_few_samples")

    if expectation.kind in ("invert", "red"):
        relation = invert_of if expectation.kind == "invert" else red_of
        matched = sum(
            1 for s in samples if _close(s.post, relation(s.pre), COLOUR_TOLERANCE, False)
        )
        distinguished = sum(
            1 for s in samples if not _close(relation(s.pre), s.pre, 2 * COLOUR_TOLERANCE, False)
        )
        worst = max(
            (max(abs(p - e) for p, e in zip(s.post, relation(s.pre))) for s in samples),
            default=0.0,
        )
        report.lines.append(
            f"relation={expectation.kind} variant_id=0x{record.variant_id:08x} "
            f"samples={len(samples)} matched={matched} needed={options.min_samples} "
            f"distinguished={distinguished} pre_variance={variance:.6g} "
            f"largest_error={worst:.6g} tolerance={COLOUR_TOLERANCE:.6g}"
        )
        if matched < options.min_samples:
            report.fail("relation")
        if variance <= MIN_PRE_VARIANCE or distinguished < MIN_DISTINGUISHED:
            report.inconclusive("scene_cannot_tell")
        return report

    if expectation.kind == "tint":
        bit = expectation.bit if expectation.bit is not None else 0

        def selected(sample: Sample) -> bool | None:
            return bool((sample.aux >> bit) & 1)

        def changed(colour):
            return tuple(c * t for c, t in zip(colour, options.tint))

        marked_tolerance, marked_scaled = COLOUR_TOLERANCE, False
        label = f"tint:{bit}"
    else:
        mask = expectation.mask

        def selected(sample: Sample) -> bool | None:
            return mask.matches(sample, region)

        changed = invert_of
        marked_tolerance, marked_scaled = EXACT_TOLERANCE, True
        label = f"masked-invert:{mask.text}"

    counts = {"marked_ok": 0, "unmarked_ok": 0, "mismatched": 0, "ambiguous": 0, "undecided": 0}
    for sample in samples:
        hit = selected(sample)
        if hit is None:
            counts["undecided"] += 1
            continue
        after = changed(sample.pre)
        if _close(after, sample.pre, 2 * marked_tolerance, marked_scaled):
            counts["ambiguous"] += 1
            continue
        if hit:
            ok = _close(sample.post, after, marked_tolerance, marked_scaled)
        else:
            ok = _close(sample.post, sample.pre, EXACT_TOLERANCE, True)
        if ok:
            counts["marked_ok" if hit else "unmarked_ok"] += 1
        else:
            counts["mismatched"] += 1
    report.lines.append(
        f"relation={label} variant_id=0x{record.variant_id:08x} samples={len(samples)} "
        + " ".join(f"{key}={value}" for key, value in counts.items())
        + f" max_mismatch={options.max_mismatch} pre_variance={variance:.6g}"
    )
    if counts["mismatched"] > options.max_mismatch:
        report.fail("relation")
    if counts["marked_ok"] == 0:
        report.inconclusive("no_selected_sample")
    if counts["unmarked_ok"] == 0:
        report.inconclusive("no_unselected_sample")
    return report


def mean_added_ns(ns_sum: int, samples: int, floor: int) -> int | None:
    """What one timed call added, less the timer floor, as `exports::mean_ns` computes it."""
    if samples <= 0:
        return None
    return max(ns_sum // samples - floor, 0)


def check_cost(snapshots: list[Snapshot], options: Options) -> Report:
    """Design section 12's hook budgets, from the last snapshot's session totals."""
    last = snapshots[-1]
    report = Report(snapshot_lines(snapshots))
    report.lines.append(
        f"timer_floor_ns={last.timer_floor_ns} hook_sample_every={last.sample_every} "
        f"presents={last.present}"
    )
    if not last.hooks or last.sample_every <= 0:
        return report.inconclusive("no_hook_timings")
    frame_ns = 0
    set_pso_frame_ns = 0
    for name, budget in COST_BUDGET_NS.items():
        ns_sum, samples = last.hooks.get(name, (0, 0))
        mean = mean_added_ns(ns_sum, samples, last.timer_floor_ns)
        judged = mean is not None and samples >= MIN_COST_SAMPLES
        # Every timed call stands for `sample_every` calls; this timing's share of the hooks' time.
        added = max(ns_sum - last.timer_floor_ns * samples, 0) * last.sample_every
        frame_ns += added
        if name == "SetPipelineState":
            set_pso_frame_ns = added
        report.lines.append(
            f"timing={name} samples={samples} mean_ns={'-' if mean is None else mean} "
            f"budget_ns={budget} judged={int(judged)} "
            f"within={'-' if not judged else int(mean <= budget)}"
        )
        if judged and mean > budget:
            report.fail(f"{name}_over_budget")
        if name == "SetPipelineState" and not judged:
            report.inconclusive("too_few_set_pipeline_state_samples")
    presents = max(last.present, 1)
    per_frame = frame_ns // presents
    set_pso_per_frame = set_pso_frame_ns // presents
    report.lines.append(
        f"per_frame_ns={per_frame} budget_ns={FRAME_BUDGET_NS} "
        f"set_pipeline_state_per_frame_ns={set_pso_per_frame} budget_ns={SET_PSO_FRAME_BUDGET_NS}"
    )
    if per_frame > FRAME_BUDGET_NS:
        report.fail("frame_over_budget")
    if set_pso_per_frame > SET_PSO_FRAME_BUDGET_NS:
        report.fail("set_pipeline_state_frame_over_budget")
    if last.present <= 0:
        report.inconclusive("no_presents")
    return report


def _us(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def check_gpu_cost(snapshots: list[Snapshot], options: Options) -> Report:
    """Each port's GPU time a frame: present, advancing, nonzero where drawn, within budget."""
    report = Report(snapshot_lines(snapshots))
    first, last = snapshots[0].gpu, snapshots[-1].gpu
    if last == GPU_ABSENT:
        return report.inconclusive("no_gpu_time_in_telemetry")
    if last == GPU_OFF or not isinstance(last, GpuTime):
        return report.fail("gpu_timer_off")
    if any(s.vanilla for s in snapshots):
        # The detours return before the table lookup in Vanilla mode, so nothing is timed.
        return report.inconclusive("vanilla_set")
    begun = last.spans_begun - first.spans_begun if isinstance(first, GpuTime) else 0
    late = last.late - first.late if isinstance(first, GpuTime) else 0
    dropped = last.spans_dropped - first.spans_dropped if isinstance(first, GpuTime) else 0
    refused = last.refused - first.refused if isinstance(first, GpuTime) else 0
    advanced = last.frames - first.frames if isinstance(first, GpuTime) else 0
    report.lines.append(
        f"gpu frequency_hz={last.frequency_hz} source={last.source} "
        f"read_lag_presents={last.read_lag_presents} frame={last.frame} "
        f"frames_delta={advanced} mean_frames={last.mean_frames} spans_begun_delta={begun} "
        f"late_delta={late} dropped_delta={dropped} refused_delta={refused}"
    )
    expected = list(options.regions) or auto_expected(snapshots[-1])
    for kind in options.kinds:
        if KIND_REGION[kind] not in expected:
            expected.append(KIND_REGION[kind])
    for kind, name in enumerate(PORT_KINDS):
        port = last.ports.get(name)
        if port is None:
            report.fail(f"gpu_port_{name}_missing")
            continue
        within = port.us_mean is None or port.us_mean <= options.gpu_budget_us
        report.lines.append(
            f"gpu_port={name} us_last={_us(port.us_last)} us_mean={_us(port.us_mean)} "
            f"spans_last={port.spans_last} bound_last={port.bound_last} "
            f"budget_us={options.gpu_budget_us:g} within={int(within)}"
        )
        if not within:
            report.fail(f"{name}_over_gpu_budget")
        if kind in options.kinds and not (port.us_mean or 0.0) > 0.0:
            report.fail(f"kind_{name}_no_gpu_time")
    for region in REGIONS:
        stage = REGION_STAGE[region]
        stage_last, stage_mean = last.stages.get(stage, (None, None))
        drawn = any(
            (last.ports[PORT_KINDS[kind]].us_mean or 0.0) > 0.0
            for kind in region_kinds(region)
            if PORT_KINDS[kind] in last.ports
        )
        report.lines.append(
            f"gpu_stage={stage} region={region} us_last={_us(stage_last)} "
            f"us_mean={_us(stage_mean)} expected={int(region in expected)} drawn={int(drawn)}"
        )
        if region in expected and not drawn:
            report.fail(f"{region}_no_gpu_time")
    if options.baseline is not None:
        baseline = options.baseline.gpu
        if not isinstance(baseline, GpuTime):
            return report.inconclusive("baseline_has_no_gpu_time")
        # Each named port kind's mean and each named region's stage mean, before and now.
        rises: list[tuple[str, float | None, float | None]] = []
        for kind in options.kinds:
            name = PORT_KINDS[kind]
            before, after = baseline.ports.get(name), last.ports.get(name)
            rises.append(
                (name, before.us_mean if before else None, after.us_mean if after else None)
            )
        for region in options.regions:
            stage = REGION_STAGE[region]
            rises.append(
                (
                    stage,
                    baseline.stages.get(stage, (None, None))[1],
                    last.stages.get(stage, (None, None))[1],
                )
            )
        if not rises:
            return report.inconclusive("baseline_without_kinds_or_regions")
        for name, before, after in rises:
            rise = None if before is None or after is None else after - before
            report.lines.append(
                f"gpu_rise={name} baseline_us={_us(before)} now_us={_us(after)} "
                f"rise_us={_us(rise)} min_rise_us={options.min_rise_us:g}"
            )
            if rise is None:
                report.inconclusive(f"{name}_rise_unknown")
            elif rise < options.min_rise_us:
                report.fail(f"{name}_did_not_rise")
    if last.frequency_hz <= 0:
        report.fail("no_tick_rate")
    if advanced <= 0:
        report.fail("gpu_frames_not_advancing")
    if begun <= 0:
        report.fail("no_spans_begun")
    elif late > MAX_LATE_SHARE * begun:
        report.inconclusive("late_spans")
    if dropped > 0:
        report.inconclusive("spans_dropped")
    if refused > 0:
        report.inconclusive("spans_refused")
    if not expected:
        report.inconclusive("no_stage_redirected")
    return report


def evaluate(expectation: Expectation, snapshots: list[Snapshot], options: Options) -> Report:
    if not snapshots:
        return Report(["snapshots=0"]).inconclusive("telemetry_not_written")
    if expectation.kind == "cost":
        return check_cost(snapshots, options)
    if expectation.kind == "gpu-cost":
        return check_gpu_cost(snapshots, options)
    if expectation.kind == "live":
        return check_live(snapshots, options)
    if expectation.kind == "vanilla":
        return check_vanilla(snapshots, options)
    if expectation.kind == "identity":
        return check_identity(snapshots, options)
    return check_colour(expectation, snapshots, options)


def print_report(report: Report) -> None:
    for line in report.lines:
        print(line, flush=True)
    why = f" why={report.why}" if report.why else ""
    print(f"verdict={report.verdict}{why}", flush=True)


def parse_tint(text: str) -> tuple[float, float, float]:
    parts = [float(part) for part in text.split(",")]
    if len(parts) != 3 or not all(math.isfinite(part) for part in parts):
        raise ValueError("--tint takes r,g,b")
    return (parts[0], parts[1], parts[2])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--expect",
        help="live | vanilla | identity | invert | red | masked-invert:<mask> | tint:<bit> | cost "
        "| gpu-cost | vertex-d | vertex-f | vertex-s | vertex-r1",
    )
    parser.add_argument(
        "--editor-dir", type=pathlib.Path, help="the game's er-shader-editor directory"
    )
    parser.add_argument("--cycles", type=int, help="off/on cycles for vertex-d and vertex-s")
    parser.add_argument("--phase-seconds", type=float, default=DEFAULT_PHASE_SECONDS)
    parser.add_argument("--frames", type=int, default=F_FRAMES, help="frames vertex-f judges")
    parser.add_argument("--telemetry", type=pathlib.Path, help="the telemetry file itself")
    parser.add_argument("--artifact-dir", type=pathlib.Path, help="the run's artifact directory")
    parser.add_argument("--region", help=f"comma list of {', '.join(REGIONS)}")
    parser.add_argument("--kinds", help=f"comma list of {', '.join(PORT_KINDS)} or numbers")
    parser.add_argument("--within", type=int, default=LIVE_WITHIN)
    parser.add_argument("--snapshots", type=int, default=SNAPSHOTS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--variant-id", type=lambda text: int(text, 0))
    parser.add_argument("--newer-than", type=lambda text: int(text, 0))
    parser.add_argument("--tint", default=",".join(str(c) for c in DEFAULT_TINT))
    parser.add_argument("--max-mismatch", type=int, default=0)
    parser.add_argument("--gpu-budget-us", type=float, default=GPU_PORT_BUDGET_US)
    parser.add_argument(
        "--baseline", type=pathlib.Path, help="gpu-cost: a telemetry file taken before a change"
    )
    parser.add_argument("--min-rise-us", type=float, default=DEFAULT_MIN_RISE_US)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.expect:
        parser.error("--expect is required")
    try:
        expectation = parse_expectation(args.expect)
        options = Options(
            regions=parse_regions(args.region),
            kinds=parse_kinds(args.kinds),
            within=args.within,
            min_samples=args.min_samples,
            variant_id=args.variant_id,
            newer_than=args.newer_than,
            tint=parse_tint(args.tint),
            max_mismatch=args.max_mismatch,
            gpu_budget_us=args.gpu_budget_us,
            baseline=read_snapshot(args.baseline) if args.baseline is not None else None,
            min_rise_us=args.min_rise_us,
        )
        if expectation.kind not in ("live", "vanilla", "cost", "gpu-cost") + VERTEX_EXPECTATIONS:
            single_region(options)
        if expectation.kind in VERTEX_EXPECTATIONS:
            cycles = args.cycles or DEFAULT_CYCLES
            seconds = sum(
                s for _, s in vertex_schedule(expectation.kind, cycles, args.phase_seconds)
            )
            if cycles < 2 or args.phase_seconds <= 0:
                raise ValueError("--cycles is at least 2 and --phase-seconds above 0")
            if seconds + FLUSH_WAIT_SECONDS > MAX_TIMEOUT_SECONDS:
                raise ValueError(
                    f"{seconds:.1f} s of phases and {FLUSH_WAIT_SECONDS} s to flush pass the "
                    f"{MAX_TIMEOUT_SECONDS:.0f} s limit; fewer --cycles or --phase-seconds"
                )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    count = max(args.snapshots, 2)
    path = resolve_telemetry(args.telemetry, args.artifact_dir)
    if expectation.kind in VERTEX_EXPECTATIONS:
        print(f"telemetry={path}", flush=True)
        return run_vertex(expectation.kind, args, path)
    try:
        mtime_age = f"{time.time() - path.stat().st_mtime:.1f}"
    except OSError:
        mtime_age = "-"
    print(f"telemetry={path} file_age_s={mtime_age}", flush=True)
    snapshots = collect(path, count, args.timeout, current_written_ms(path))
    if len(snapshots) < count:
        print(f"snapshots={len(snapshots)}/{count} timeout_s={args.timeout}", flush=True)
        print("verdict=inconclusive why=telemetry_not_refreshing", flush=True)
        return EXIT_INCONCLUSIVE
    report = evaluate(expectation, snapshots, options)
    print_report(report)
    return report.exit_code


# Vertex proof


@dataclass(frozen=True)
class ProofSample:
    # `None` where the DLL wrote `null`: a value that was not finite.
    depth: float | None
    normal_dot: float | None
    occlusion: float | None
    light: float | None


@dataclass(frozen=True)
class ProofFrame:
    """One line of the proof file: one present's copy of the vertex proof region."""

    present: int
    frame: int
    time: float | None
    vertex_on: bool
    passes: int
    generation: int
    samples: dict[int, ProofSample]


def _number_or_none(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"not a number: {value!r}")
    return float(value)


def parse_proof_line(text: str) -> ProofFrame:
    """A line as `ProofFrame::to_json` writes it (crates/er-cel-shade/src/vertex/mod.rs)."""
    document = json.loads(text)
    if not isinstance(document, dict) or not isinstance(document.get("x"), list):
        raise TelemetryError("a proof line without samples")
    samples: dict[int, ProofSample] = {}
    for entry in document["x"]:
        if not isinstance(entry, list) or len(entry) != 5:
            raise TelemetryError(f"a proof sample is not five values: {entry!r}")
        index = entry[0]
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < SAMPLES:
            raise TelemetryError(f"a proof sample off the grid: {entry!r}")
        samples[index] = ProofSample(*(_number_or_none(value) for value in entry[1:]))
    if not isinstance(document.get("v"), bool):
        raise TelemetryError("a proof line without its vertex state")
    return ProofFrame(
        present=_int(document, "p"),
        frame=_int(document, "f"),
        time=_number_or_none(document.get("t")),
        vertex_on=document["v"],
        passes=_int(document, "m"),
        generation=_int(document, "g"),
        samples=samples,
    )


def read_proof(path: pathlib.Path, after_present: int) -> list[ProofFrame]:
    """Every frame in the proof file read after `after_present`, by present. A line cut short by
    a write in progress is left out."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    frames: dict[int, ProofFrame] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            frame = parse_proof_line(line)
        except (ValueError, TypeError):
            continue
        if frame.present > after_present:
            frames[frame.present] = frame
    return [frames[present] for present in sorted(frames)]


def runs(frames: list[ProofFrame], key) -> list[tuple[object, list[ProofFrame]]]:
    """The frames cut where `key` changes, each run less its first SKIP_AFTER_SWITCH frames; a run
    that leaves nothing is dropped."""
    cut: list[tuple[object, list[ProofFrame]]] = []
    for frame in frames:
        value = key(frame)
        if cut and cut[-1][0] == value:
            cut[-1][1].append(frame)
        else:
            cut.append((value, [frame]))
    return [(value, run[SKIP_AFTER_SWITCH:]) for value, run in cut if run[SKIP_AFTER_SWITCH:]]


def is_geometry_depth(depth: float | None) -> bool:
    return depth is not None and math.isfinite(depth) and depth > 0


def depths_of(frames: list[ProofFrame], index: int) -> list[float]:
    """The sample's finite depths, the sky's -1 included."""
    out = []
    for frame in frames:
        sample = frame.samples.get(index)
        if sample is not None and sample.depth is not None and math.isfinite(sample.depth):
            out.append(sample.depth)
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def geometry_samples(calibration: list[list[ProofFrame]]) -> list[int]:
    """Samples that are geometry in every calibration frame that has them."""
    out = []
    for index in range(SAMPLES):
        seen = [
            frame.samples[index].depth
            for run in calibration
            for frame in run
            if index in frame.samples
        ]
        if seen and all(is_geometry_depth(depth) for depth in seen):
            out.append(index)
    return out


def noise_of(calibration: list[list[ProofFrame]], index: int) -> float:
    """The sample's depth noise: the pooled spread within each calibration run, so a camera that
    drifts between runs does not count, floored at NOISE_FLOOR_RELATIVE of its depth."""
    squares, count, runs_used, level = 0.0, 0, 0, []
    for run in calibration:
        values = depths_of(run, index)
        if not values:
            continue
        mean = sum(values) / len(values)
        squares += sum((value - mean) ** 2 for value in values)
        count += len(values)
        runs_used += 1
        level.append(abs(mean))
    spread = math.sqrt(squares / (count - runs_used)) if count > runs_used else 0.0
    floor = NOISE_FLOOR_RELATIVE * max(max(level, default=0.0), 1.0)
    return max(spread, floor)


def cycles_of(phases: list[tuple[object, list[ProofFrame]]]) -> list[tuple[list, list]]:
    """Each off run followed at once by an on run."""
    return [
        (before[1], after[1])
        for before, after in zip(phases, phases[1:])
        if before[0] is False and after[0] is True
    ]


def moved(a: list[float], b: list[float], noise: float) -> bool:
    mean_a, mean_b = _mean(a), _mean(b)
    return mean_a is not None and mean_b is not None and abs(mean_a - mean_b) > NOISE_FACTOR * noise


def generations(frames: list[ProofFrame]) -> list[int]:
    return sorted({frame.generation for frame in frames if frame.vertex_on})


def check_vertex_d(frames: list[ProofFrame]) -> Report:
    """Oracle D: the samples on geometry move when the vertex stage comes on, and stay still when
    the off phases are compared among themselves."""
    report = Report([f"proof_frames={len(frames)}"])
    phases = runs(frames, lambda frame: frame.vertex_on)
    cycles = cycles_of(phases)
    offs = [run for on, run in phases if on is False]
    report.lines.append(
        f"runs={''.join('1' if on else '0' for on, _ in phases)} cycles={len(cycles)} "
        f"frames_on={sum(len(run) for on, run in phases if on)} "
        f"frames_off={sum(len(run) for run in offs)} generations={_join(generations(frames))}"
    )
    if not any(frame.vertex_on for frame in frames):
        return report.inconclusive("vertex_stage_never_on")
    if len(generations(frames)) > 1:
        return report.inconclusive("carrier_changed_during_the_run")
    if len(cycles) < 2:
        return report.inconclusive("too_few_cycles")
    geometry = geometry_samples(offs)
    need = math.ceil(CYCLE_SHARE * len(cycles))
    null_pairs = list(zip(offs, offs[1:]))
    null_need = math.ceil(CYCLE_SHARE * len(null_pairs))
    moving, null_moving = [], []
    for index in geometry:
        noise = noise_of(offs, index)
        hits = sum(
            moved(depths_of(off, index), depths_of(on, index), noise) for off, on in cycles
        )
        if hits >= need:
            moving.append(index)
        null_hits = sum(
            moved(depths_of(a, index), depths_of(b, index), noise) for a, b in null_pairs
        )
        if null_pairs and null_hits >= null_need:
            null_moving.append(index)
    share = len(moving) / len(geometry) if geometry else 0.0
    null_share = len(null_moving) / len(geometry) if geometry else 0.0
    report.lines.append(
        f"geometry={len(geometry)} moving={len(moving)} moving_share={share:.3f} "
        f"need={D_MOVING_SHARE} null_moving={len(null_moving)} null_share={null_share:.3f} "
        f"null_limit={D_NULL_SHARE}"
    )
    still = [index for index in geometry if index not in moving]
    report.lines.append(f"still_samples={_join(still) or '-'}")
    if len(geometry) < MIN_GEOMETRY:
        return report.inconclusive("too_few_geometry_samples")
    if len(null_pairs) < 1:
        return report.inconclusive("no_off_phases_to_compare")
    if null_share > D_NULL_SHARE:
        return report.inconclusive("scene_moves_with_the_stage_off")
    if share < D_MOVING_SHARE:
        return report.fail("too_few_moving_samples")
    return report


def check_vertex_f(frames: list[ProofFrame], need_frames: int = F_FRAMES) -> Report:
    """Oracle F, with `proof_lift_parity`: every moving sample of a frame agrees on the frame's
    state, and the geometry opens no more holes than calibration had."""
    report = Report([f"proof_frames={len(frames)}"])
    phases = runs(frames, lambda frame: frame.vertex_on)
    calibration = [run for on, run in phases if on is False]
    on_runs = [run for on, run in phases if on is True]
    calibration_frames = sum(len(run) for run in calibration)
    if not on_runs:
        return report.inconclusive("vertex_stage_never_on")
    if len(generations(frames)) > 1:
        return report.inconclusive("carrier_changed_during_the_run")
    if calibration_frames < F_MIN_CALIBRATION_FRAMES:
        report.lines.append(f"calibration_frames={calibration_frames}")
        return report.inconclusive("too_little_calibration")
    geometry = geometry_samples(calibration)
    noise = {index: noise_of(calibration, index) for index in geometry}
    # Each frame of an on run that has both neighbours: its depth less their mean, per sample.
    offsets: list[tuple[int, dict[int, float]]] = []
    for run in on_runs:
        by_frame = {frame.frame: frame for frame in run}
        for number, frame in sorted(by_frame.items()):
            before, after = by_frame.get(number - 1), by_frame.get(number + 1)
            if before is None or after is None:
                continue
            row = {}
            for index in geometry:
                values = [
                    sample.depth
                    for sample in (
                        f.samples.get(index) for f in (before, frame, after)
                    )
                    if sample is not None and sample.depth is not None
                ]
                if len(values) == 3 and all(math.isfinite(v) for v in values):
                    row[index] = values[1] - (values[0] + values[2]) / 2
            offsets.append((number, row))
    moving, direction = [], {}
    for index in geometry:
        seen = [row[index] for _, row in offsets if index in row]
        if not seen:
            continue
        big = sum(abs(value) > NOISE_FACTOR * noise[index] for value in seen)
        odd = sorted(row[index] for number, row in offsets if index in row and number % 2)
        if big >= CYCLE_SHARE * len(seen) and odd and odd[len(odd) // 2] != 0:
            moving.append(index)
            direction[index] = 1.0 if odd[len(odd) // 2] > 0 else -1.0
    # Up, down, or neither: a sample whose depth sits between its neighbours' agrees with no state,
    # which is what a sample a frame left behind looks like when its neighbours moved.
    judged = mixed = against_parity = 0
    for number, row in offsets:
        states = []
        for index in moving:
            if index not in row:
                continue
            value = row[index] * direction[index]
            limit = NOISE_FACTOR * noise[index]
            states.append(1 if value > limit else -1 if value < -limit else 0)
        if len(states) < F_MIN_MOVING:
            continue
        judged += 1
        up, down = states.count(1), states.count(-1)
        majority_up = up >= down
        agree = max(up, down) / len(states)
        if agree < F_AGREE:
            mixed += 1
        if majority_up != bool(number % 2):
            against_parity += 1
    # Holes: where calibration saw the G-buffer normal agree with the depth's own.
    def dot_mean(index: int) -> float | None:
        values = [
            frame.samples[index].normal_dot
            for run in calibration
            for frame in run
            if index in frame.samples
            and frame.samples[index].normal_dot is not None
            and frame.samples[index].normal_dot <= 1.0
        ]
        return _mean(values)

    eligible = [index for index in geometry if (dot_mean(index) or 0.0) > F_CALIBRATED_DOT]

    def hole_rate(runs_: list[list[ProofFrame]]) -> tuple[int, int]:
        holes = total = 0
        for run in runs_:
            for frame in run:
                for index in eligible:
                    sample = frame.samples.get(index)
                    if sample is None or not is_geometry_depth(sample.depth):
                        continue
                    if sample.normal_dot is None:
                        continue
                    total += 1
                    holes += sample.normal_dot < F_HOLE_DOT
        return holes, total

    holes_off, total_off = hole_rate(calibration)
    holes_on, total_on = hole_rate(on_runs)
    rate_off = holes_off / total_off if total_off else 0.0
    rate_on = holes_on / total_on if total_on else 0.0
    mixed_share = mixed / judged if judged else 0.0
    report.lines.append(
        f"calibration_frames={calibration_frames} geometry={len(geometry)} moving={len(moving)} "
        f"judged_frames={judged} need={need_frames} mixed={mixed} mixed_share={mixed_share:.4f} "
        f"limit={F_MIXED_SHARE}"
    )
    report.lines.append(
        f"hole_samples={len(eligible)} holes_on={holes_on}/{total_on} rate_on={rate_on:.4f} "
        f"holes_off={holes_off}/{total_off} rate_off={rate_off:.4f} excess_limit={F_HOLE_EXCESS}"
    )
    # Reported, not judged: frames whose state is not the AO port's own frame parity.
    report.lines.append(
        f"against_ao_parity={against_parity} "
        f"against_ao_parity_share={(against_parity / judged if judged else 0.0):.4f}"
    )
    if len(moving) < F_MIN_MOVING:
        return report.inconclusive("too_few_moving_samples")
    if judged and mixed_share > F_MIXED_SHARE and judged >= need_frames // 6:
        return report.fail("mixed_frames")
    if total_on and rate_on > rate_off + F_HOLE_EXCESS:
        return report.fail("holes")
    if judged < need_frames:
        return report.inconclusive("too_few_frames")
    return report


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def lights_of(frames: list[ProofFrame], index: int) -> list[float]:
    out = []
    for frame in frames:
        sample = frame.samples.get(index)
        if sample is not None and sample.light is not None and math.isfinite(sample.light):
            out.append(sample.light)
    return out


def check_vertex_s(frames: list[ProofFrame]) -> Report:
    """Oracle S, with only the depth pipelines moved and a large static lift: samples whose depth
    stays put while their light changes in step with the layer are a shadow that moved."""
    report = Report([f"proof_frames={len(frames)}"])
    phases = runs(frames, lambda frame: frame.vertex_on)
    cycles = cycles_of(phases)
    offs = [run for on, run in phases if on is False]
    on_frames = [frame for on, run in phases if on for frame in run]
    passes = sorted({frame.passes for frame in on_frames})
    report.lines.append(
        f"runs={''.join('1' if on else '0' for on, _ in phases)} cycles={len(cycles)} "
        f"passes={_join(passes)} want_passes={SHADOW_PASSES}"
    )
    if not on_frames:
        return report.inconclusive("vertex_stage_never_on")
    if passes != [SHADOW_PASSES]:
        return report.inconclusive("passes_not_narrowed_to_depth")
    if len(generations(frames)) > 1:
        return report.inconclusive("carrier_changed_during_the_run")
    if len(cycles) < 2:
        return report.inconclusive("too_few_cycles")
    geometry = geometry_samples(offs)
    shadows, depth_moved = [], []
    ordered = [(on, run) for on, run in phases if on in (True, False)]
    for index in geometry:
        noise = noise_of(offs, index)
        if any(moved(depths_of(off, index), depths_of(on, index), noise) for off, on in cycles):
            depth_moved.append(index)
        kept = True
        changed = True
        for off, on in cycles:
            d_off, d_on = _mean(depths_of(off, index)), _mean(depths_of(on, index))
            l_off, l_on = _mean(lights_of(off, index)), _mean(lights_of(on, index))
            if None in (d_off, d_on, l_off, l_on):
                kept = changed = False
                break
            if abs(d_on - d_off) > S_DEPTH_KEPT * max(abs(d_off), 1e-6):
                kept = False
            if abs(l_on - l_off) < S_LIGHT_CHANGE * max(abs(l_on), abs(l_off), 1e-6):
                changed = False
        indicator, light = [], []
        for on, run in ordered:
            value = _mean(lights_of(run, index))
            if value is not None:
                indicator.append(1.0 if on else 0.0)
                light.append(value)
        correlation = _correlation(indicator, light)
        if kept and changed and correlation is not None and abs(correlation) >= S_CORRELATION:
            shadows.append(index)
    report.lines.append(
        f"geometry={len(geometry)} shadow_samples={len(shadows)} need={S_MIN_SAMPLES} "
        f"samples={_join(shadows) or '-'}"
    )
    # Reported, not judged: whether moving only the depth pipelines moves the main view's depth,
    # which says which of them is a main-view prepass.
    report.lines.append(f"main_view_depth_moved={len(depth_moved)}/{len(geometry)}")
    if len(shadows) < S_MIN_SAMPLES:
        return report.inconclusive("too_few_shadow_samples")
    return report


def r1_of(document: dict) -> dict[str, int]:
    r1 = document.get("r1")
    if not isinstance(r1, dict):
        raise TelemetryError("no r1 object")
    return {
        key: _int(r1, key)
        for key in ("lists", "lists_straddling", "frames", "frames_mixed", "max_span")
    }


def check_vertex_r1(documents: list[dict]) -> Report:
    """Gate R1: between the first and the last snapshot, no present's lists bound blocks of two
    presents."""
    report = Report([f"snapshots={len(documents)}"])
    try:
        first, last = r1_of(documents[0]), r1_of(documents[-1])
    except (TelemetryError, IndexError):
        return report.inconclusive("no_r1_counters")
    delta = {key: last[key] - first[key] for key in ("lists", "lists_straddling", "frames")}
    mixed = last["frames_mixed"] - first["frames_mixed"]
    share = mixed / delta["frames"] if delta["frames"] > 0 else 0.0
    report.lines.append(
        f"r1 frames={delta['frames']} frames_mixed={mixed} mixed_share={share:.4f} "
        f"lists={delta['lists']} lists_straddling={delta['lists_straddling']} "
        f"max_span={last['max_span']}"
    )
    if delta["frames"] < R1_MIN_FRAMES:
        return report.inconclusive("too_few_presents")
    if delta["lists"] <= 0:
        return report.inconclusive("no_list_bound_a_block")
    if mixed > 0:
        return report.fail("frames_straddle_presents")
    return report


def vertex_status_line(document: dict) -> str:
    """The vertex stage as the telemetry has it: the carrier, the groups and the binds."""
    vertex = document.get("vertex")
    if not isinstance(vertex, dict):
        return "vertex=absent"
    groups = vertex.get("groups") or {}
    binds = vertex.get("binds_redirects") or {}
    redirected = ",".join(
        f"{token}:{pair[1]}/{pair[0]}"
        for token, pair in binds.items()
        if isinstance(pair, list) and len(pair) == 2 and pair[0]
    )
    return (
        f"vertex generation={vertex.get('generation')} layers={_join(vertex.get('layers') or [])} "
        f"stage_on={int(bool(vertex.get('stage_on')))} hold_off={int(bool(vertex.get('hold_off')))} "
        f"proof_mode={vertex.get('proof_mode')} passes={vertex.get('passes')} "
        f"groups={','.join(f'{k}:{v}' for k, v in groups.items()) or '-'} "
        f"pipelines_live={vertex.get('pipelines_live')}/{vertex.get('pipelines_wanted')} "
        f"vs_patched={vertex.get('vs_patched')} vs_refused={vertex.get('vs_refused')} "
        f"suspensions={vertex.get('suspensions')} redirects={redirected or '-'}"
    )


def control_text(phase: int, vertex_on: bool, passes: int) -> str:
    """The `vertex-proof` file for one phase, as `Control::parse` reads it. The phase number keeps
    two phases' files apart even when a file system's clock cannot."""
    lines = [f"# check-shader-editor-probe.py, phase {phase}", "proof = 1"]
    if passes != ALL_PASSES:
        names = [token for bit, token in enumerate(PASS_TOKENS) if passes & (1 << bit)]
        lines.append(f"passes = {','.join(names)}")
    lines.append(f"vertex = {'on' if vertex_on else 'off'}")
    return "\n".join(lines) + "\n"


def write_atomically(path: pathlib.Path, text: str) -> None:
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(text, encoding="utf-8")
    os.replace(pending, path)


def wait_until(watch: er_run_lib.DirectoryWatch, deadline: float) -> None:
    while (remaining := deadline - time.monotonic()) > 0:
        block(watch, min(WAIT_SLICE_SECONDS, remaining))


def drive(
    control: pathlib.Path,
    proof: pathlib.Path,
    schedule: list[tuple[bool, float]],
    passes: int,
    flush_seconds: float = FLUSH_WAIT_SECONDS,
) -> None:
    """Hold each phase of `schedule` -- the vertex stage on or off, for so many seconds -- through
    the control file, then wait for the cel worker to append the last of the proof. The control
    file is put back as it was whatever happens, which with none before switches the proof off."""
    try:
        before: str | None = control.read_text(encoding="utf-8")
    except OSError:
        before = None
    try:
        control.parent.mkdir(parents=True, exist_ok=True)
        with er_run_lib.DirectoryWatch(proof.parent) as watch:
            for phase, (vertex_on, seconds) in enumerate(schedule):
                write_atomically(control, control_text(phase, vertex_on, passes))
                wait_until(watch, time.monotonic() + seconds)
            try:
                size = proof.stat().st_size
            except OSError:
                size = -1
            deadline = time.monotonic() + flush_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    if proof.stat().st_size != size:
                        break
                except OSError:
                    pass
                block(watch, min(WAIT_SLICE_SECONDS, remaining))
    finally:
        if before is None:
            control.unlink(missing_ok=True)
        else:
            write_atomically(control, before)


def vertex_schedule(kind: str, cycles: int, phase_seconds: float) -> list[tuple[bool, float]]:
    if kind == "vertex-f":
        return [(False, CALIBRATION_SECONDS), (True, F_SECONDS)]
    return [(on, phase_seconds) for _ in range(cycles) for on in (False, True)]


def read_document(path: pathlib.Path) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def run_vertex(kind: str, args, telemetry: pathlib.Path) -> int:
    """The vertex expectations: R1 from the telemetry, D, F and S by driving the proof."""
    if kind == "vertex-r1":
        documents = collect_documents(
            telemetry, max(args.snapshots, 2), args.timeout, current_written_ms(telemetry)
        )
        if documents:
            print(vertex_status_line(documents[-1]), flush=True)
        report = check_vertex_r1(documents)
        print_report(report)
        return report.exit_code
    start = read_document(telemetry)
    if start is None:
        print("verdict=inconclusive why=telemetry_not_written", flush=True)
        return EXIT_INCONCLUSIVE
    print(vertex_status_line(start), flush=True)
    vertex = start.get("vertex") if isinstance(start.get("vertex"), dict) else {}
    if not vertex.get("generation") or not vertex.get("layers"):
        print("verdict=inconclusive why=no_vertex_carrier", flush=True)
        return EXIT_INCONCLUSIVE
    stats = start.get("stats") if isinstance(start.get("stats"), dict) else {}
    after_present = stats.get("presents") if isinstance(stats.get("presents"), int) else 0
    editor = args.editor_dir or er_run_lib.game_dir() / EDITOR_DIRECTORY
    control = editor / CONTROL_NAME
    proof = telemetry.with_name(PROOF_NAME)
    passes = SHADOW_PASSES if kind == "vertex-s" else ALL_PASSES
    cycles = args.cycles or (S_DEFAULT_CYCLES if kind == "vertex-s" else DEFAULT_CYCLES)
    schedule = vertex_schedule(kind, cycles, args.phase_seconds)
    print(
        f"control={control} proof={proof} after_present={after_present} "
        f"phases={len(schedule)} seconds={sum(s for _, s in schedule):.1f}",
        flush=True,
    )
    drive(control, proof, schedule, passes)
    frames = read_proof(proof, after_present)
    end = read_document(telemetry)
    if end is not None:
        print(vertex_status_line(end), flush=True)
    if kind == "vertex-d":
        report = check_vertex_d(frames)
    elif kind == "vertex-f":
        report = check_vertex_f(frames, args.frames)
    else:
        report = check_vertex_s(frames)
    print_report(report)
    return report.exit_code


# Selftest


def synthetic_region(
    name: str,
    frame: int,
    kind: int,
    samples: list[dict] | None = None,
    variant_low: int = 0,
) -> dict:
    return {
        "region": name,
        "kind": kind,
        "pass": None,
        "variant_id": (kind << 24) | variant_low,
        "frame": frame,
        "frame_age": 0,
        "width": 1920,
        "height": 1080,
        "max_x": 1919,
        "max_y": 1079,
        "stale": SAMPLES - len(samples or []),
        "samples": samples or [],
    }


def synthetic_doc(
    present: int,
    written_ms: int,
    regions: list[dict],
    *,
    mode: int = 0,
    vanilla: int = 0,
    stage_active: int = 7,
    ports_ready: tuple[int, ...] = (1, 0, 1, 0, 1),
    redirected_binds: int = 0,
    hook_faulted: bool = False,
    hooks: dict[str, tuple[int, int]] | None = None,
    timer_floor_ns: int = 20,
    sample_every: int = 256,
    gpu: dict | str | None = GPU_ABSENT,
) -> dict:
    document = {
        "schema": SCHEMA,
        "written_ms": written_ms,
        "mode_name": ["normal", "vanilla", "fidelity"][mode],
        "stats": {
            "presents": present,
            "mode": mode,
            "vanilla": vanilla,
            "ports_ready": list(ports_ready),
            "redirected_binds": redirected_binds,
        },
        "stage_active": stage_active,
        "hook_faulted": hook_faulted,
        "probe": {"present": present, "regions": regions},
    }
    if hooks is not None:
        document["hooks"] = [
            {"detour": name, "ns_sum": ns_sum, "samples": samples, "mean_ns": None}
            for name, (ns_sum, samples) in hooks.items()
        ]
        document["timer_floor_ns"] = timer_floor_ns
        document["hook_sample_every"] = sample_every
    if gpu != GPU_ABSENT:
        document["gpu_time"] = gpu
    return document


def synthetic_gpu(
    frames: int,
    begun: int,
    means: dict[str, float],
    *,
    late: int = 0,
    dropped: int = 0,
    refused: int = 0,
    frequency: int = 1_000_000_000,
) -> dict:
    """A `gpu_time` object as `render_gpu` writes it, each kind's last frame equal to its mean."""
    ports = [
        {
            "kind": name,
            "index": at,
            "us_last": means.get(name, 0.0),
            "us_mean": means.get(name, 0.0),
            "spans_last": int(means.get(name, 0.0) > 0),
            "bound_last": "variant" if means.get(name, 0.0) > 0 else "none",
            "ticks_last": 0,
        }
        for at, name in enumerate(PORT_KINDS)
    ]
    stages = []
    for region in REGIONS:
        total = sum(means.get(PORT_KINDS[kind], 0.0) for kind in region_kinds(region))
        stages.append({"stage": REGION_STAGE[region], "us_last": total, "us_mean": total})
    return {
        "frequency_hz": frequency,
        "source": "game_direct_queue",
        "read_lag_presents": GPU_READ_LAG_PRESENTS,
        "budget_us": GPU_PORT_BUDGET_US,
        "history_frames": 32,
        "frame": frames + 7,
        "frames": frames,
        "unknown_frames": 0,
        "mean_frames": 32,
        "spans_begun": begun,
        "spans_dropped": dropped,
        "chunks": 0,
        "resolves": 0,
        "late": late,
        "refused": refused,
        "ports": ports,
        "stages": stages,
    }


def grid_colours(seed: int = 0) -> list[tuple[float, float, float]]:
    """64 distinct colours in 0..1 with plenty of green and blue, varied across the grid."""
    colours = []
    for index in range(SAMPLES):
        r = ((index * 37 + seed * 11) % 64) / 63
        g = ((index * 23 + 7) % 64) / 63
        b = ((index * 53 + 3) % 64) / 63
        colours.append((round(r, 6), round(g, 6), round(b, 6)))
    return colours


def samples_with(post_of, aux_of=lambda index: 0, colours=None) -> list[dict]:
    colours = colours or grid_colours()
    return [
        {"index": index, "pre": list(pre), "post": list(post_of(index, pre)), "aux": aux_of(index)}
        for index, pre in enumerate(colours)
    ]


def series(
    regions_at, presents=(100, 140, 180), *, binds=(0, 500, 1000), **kwargs
) -> list[Snapshot]:
    """Snapshots at `presents`, each region built by `regions_at(present)`."""
    return [
        parse_snapshot(
            synthetic_doc(present, 1000 * at, regions_at(present), redirected_binds=bind, **kwargs)
        )
        for at, (present, bind) in enumerate(zip(presents, binds))
    ]


def live_regions(present: int, names=REGIONS, kinds=(0, 2, 4)) -> list[dict]:
    return [synthetic_region(name, present - 3, kind) for name, kind in zip(names, kinds)]


def rust_constant(text: str, name: str) -> str | None:
    match = re.search(rf"pub const {name}: \w+ = ([^;]+);", text)
    return match.group(1).strip() if match else None


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def selftest() -> int:
    results: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        results.append((label, bool(ok)))

    def verdict(expect: str, snapshots, **options) -> str:
        return evaluate(parse_expectation(expect), snapshots, Options(**options)).verdict

    # The constants are the Rust's, read from the sources rather than trusted.
    crate = REPO / "crates" / "er-cel-shade" / "src"
    probe_rs = (crate / "probe.rs").read_text(encoding="utf-8")
    replace_rs = (crate / "replace.rs").read_text(encoding="utf-8")
    exports_rs = (crate / "exports.rs").read_text(encoding="utf-8")
    check("GRID is probe.rs's", rust_constant(probe_rs, "GRID") == str(GRID))
    check(
        "the read lag is probe.rs's",
        rust_constant(probe_rs, "READ_LAG_PRESENTS") == str(READ_LAG_PRESENTS),
    )
    check(
        "the ring size is probe.rs's",
        rust_constant(probe_rs, "READBACK_SLOTS") == str(READBACK_SLOTS),
    )
    names = re.findall(r'Self::\w+ => "(\w+)",', probe_rs[probe_rs.index("pub const fn name") :])
    check("the regions are probe.rs's, in order", tuple(names[:3]) == REGIONS)
    enum = re.search(r"pub enum PortKind \{([^}]*)\}", replace_rs)
    variants = re.findall(r"(\w+) = (\d+),", enum.group(1)) if enum else []
    check(
        "the port kinds are replace.rs's, numbered as there",
        [(snake(name), int(number)) for name, number in variants]
        == [(name, at) for at, name in enumerate(PORT_KINDS)],
    )
    stage_fn = replace_rs[replace_rs.index("pub const fn stage(self) -> Stage") :]
    stage_fn = stage_fn[: stage_fn.index("\n    }\n")]
    stage_of = {}
    for arm, stage in re.findall(r"((?:Self::\w+(?: \| )?)+) => Stage::(\w+)", stage_fn):
        for variant in re.findall(r"Self::(\w+)", arm):
            stage_of[snake(variant)] = stage
    stage_region = {"Lighting": "ao", "Albedo": "albedo", "Tonemap": "tonemap"}
    check(
        "each port kind's region is its stage's, as replace.rs maps them",
        [stage_region.get(stage_of.get(name, ""), "") for name in PORT_KINDS] == list(KIND_REGION),
    )
    stage_enum = re.search(r"pub enum Stage \{([^}]*)\}", replace_rs)
    stage_numbers = dict(re.findall(r"(\w+) = (\d+),", stage_enum.group(1))) if stage_enum else {}
    check(
        "stage bits are 1 << the Stage number, as Stage::bit computes them",
        "1 << self as u32" in replace_rs
        and {stage_region[k]: 1 << int(v) for k, v in stage_numbers.items()} == STAGE_BITS,
    )
    check(
        "the file name and redirect variable are exports.rs's",
        f'TELEMETRY_FILE_NAME: &str = "{TELEMETRY_NAME}"' in exports_rs
        and f'TELEMETRY_PATH_ENV: &str = "{TELEMETRY_ENV}"' in exports_rs,
    )
    check(
        "the schema is the one exports.rs writes",
        f'"{{\\n  \\"schema\\": {SCHEMA},\\n"' in exports_rs,
    )
    cmdlist_rs = (crate / "cmdlist.rs").read_text(encoding="utf-8")
    timing_fn = cmdlist_rs[cmdlist_rs.index("impl Timing {") :]
    timing_fn = timing_fn[timing_fn.index("pub const fn name(self)") :]
    timing_fn = timing_fn[: timing_fn.index("\n    }\n")]
    check(
        "the cost budgets are named by cmdlist.rs's timings, in order",
        re.findall(r'Self::\w+ => "(\w+)",', timing_fn) == list(COST_BUDGET_NS),
    )
    check(
        "exports.rs writes the timings, the floor and the sampling rate the cost check reads",
        '\\"hooks\\": [' in exports_rs
        and '\\"timer_floor_ns\\": {},' in exports_rs
        and '\\"hook_sample_every\\": {SAMPLE_EVERY},' in exports_rs
        and rust_constant(cmdlist_rs, "SAMPLE_EVERY") == "256",
    )
    check("a live region is under one ring's worth of age", LIVE_WITHIN == 10)

    # A document in the exact text shape the Rust renders, NaN as null and -0 included.
    rendered = (
        '{\n  "schema": 1,\n  "written_ms": 12345,\n  "mode_name": "fidelity",\n'
        '  "stats": {\n    "presents": 100,\n    "mode": 2,\n    "vanilla": 0,\n'
        '    "ports_ready": [1, 1, 1, 0, 1],\n    "redirected_binds": 7,\n'
        '    "probe_frame": [97, 0, 96],\n    "create_ms_last": null\n  },\n'
        '  "stage_active": 7,\n  "hook_faulted": true,\n'
        '  "probe": {\n    "present": 100,\n    "regions": [\n'
        '      {"region": "ao",\n       "kind": 1, "pass": "GXLightDeferredAOWithSsr",\n'
        '       "variant_id": 16777216, "frame": 97, "frame_age": 3,\n'
        '       "width": 1920, "height": 1080, "max_x": 1919, "max_y": 1079, "stale": 62,\n'
        '       "samples": [\n'
        '        {"index": 0, "pre": [0.25, 0.5, 1], "post": [0.75, 0.5, 0], "aux": 1065353216},\n'
        '        {"index": 9, "pre": [null, null, -0], "post": [1e-40, 3.4e38, -2.5], "aux": 0}]}\n'
        "    ]\n  }\n}\n"
    )
    parsed = parse_snapshot(json.loads(rendered))
    ao = parsed.regions["ao"]
    check(
        "the Rust's text parses, NaN read as an unknown colour",
        parsed.present == 100
        and parsed.hook_faulted
        and ao.kind == 1
        and ao.samples[1].pre is None
        and ao.samples[0].post == (0.75, 0.5, 0.0)
        and parsed.age("ao") == 3
        and parsed.age("tonemap") is None,
    )
    check("depth at ao is the aux's float bits", depth_of("ao", ao.samples[0].aux) == 1.0)
    check(
        "depth at tonemap is the half float in the high bits",
        depth_of("tonemap", (0x4900 << 16) | 0x2FF) == 10.0,
    )
    check("a grid centre sits mid-cell", Sample(9, None, None, 0).uv == (3 / 16, 3 / 16))
    for bad in (
        {"schema": 2},
        {"schema": 1, "stats": {}, "probe": {}},
        json.loads(rendered.replace('"ao"', '"sky"', 1)),
        json.loads(rendered.replace('"index": 9', '"index": 64')),
    ):
        try:
            parse_snapshot(bad)
            check(f"a bad document is refused ({str(bad)[:30]})", False)
        except TelemetryError:
            check(f"a bad document is refused ({str(bad)[:30]})", True)

    # live
    fresh = series(live_regions)
    check("live: every expected region advancing passes", verdict("live", fresh) == "pass")
    check(
        "live: the expected set follows stage_active and ports_ready",
        auto_expected(fresh[-1]) == ["ao", "albedo", "tonemap"]
        and auto_expected(
            parse_snapshot(synthetic_doc(9, 1, [], stage_active=0b101, ports_ready=(0, 1, 1, 0, 0)))
        )
        == ["ao"],
    )
    stuck = series(
        lambda p: live_regions(p, ("ao", "albedo"), (0, 2))
        + [synthetic_region("tonemap", 97, 4)]
    )
    check("live: one stalled region fails", verdict("live", stuck) == "fail")
    late = series(lambda p: [synthetic_region("ao", p - LIVE_WITHIN - 1, 0)], stage_active=1)
    check("live: a region one ring stale fails although it advances", verdict("live", late) == "fail")
    edge = series(lambda p: [synthetic_region("ao", p - LIVE_WITHIN, 0)], stage_active=1)
    check("live: a region exactly --within behind passes", verdict("live", edge) == "pass")
    check(
        "live: vanilla set fails",
        verdict("live", series(live_regions, vanilla=1)) == "fail",
    )
    check(
        "live: a faulted hook fails",
        verdict("live", series(live_regions, hook_faulted=True)) == "fail",
    )
    check(
        "live: nothing redirected is inconclusive",
        verdict("live", series(lambda p: [], stage_active=0)) == "inconclusive",
    )
    check(
        "live: a stage without content is not expected",
        verdict("live", series(lambda p: live_regions(p, ("ao",), (0,)), stage_active=1))
        == "pass",
    )
    check(
        "live: --kinds needs that kind as the writer",
        verdict("live", fresh, kinds=[1]) == "fail" and verdict("live", fresh, kinds=[0]) == "pass",
    )
    check(
        "live: a region written by another stage's kind fails",
        verdict("live", series(lambda p: [synthetic_region("ao", p - 3, 3)], stage_active=1))
        == "fail",
    )
    wrapped = series(
        lambda p: [synthetic_region("ao", (p - 3) % U32, 0)],
        presents=(U32 - 20, U32 + 20, U32 + 60),
        stage_active=1,
    )
    check("live: the 32-bit header frame wraps", verdict("live", wrapped) == "pass")
    check(
        "live: --region names the expected regions",
        verdict("live", series(lambda p: [], stage_active=0), regions=["tonemap"]) == "fail",
    )

    # vanilla: the slots keep the frames copied before the switch, at 97 and earlier.
    def stalled(present: int) -> list[dict]:
        return [synthetic_region("ao", 95 + (present % 3), 0), synthetic_region("tonemap", 97, 4)]

    gone = series(stalled, presents=(140, 180, 220), binds=(900, 900, 900), mode=1, vanilla=1)
    check("vanilla: stale regions with binds flat pass", verdict("vanilla", gone) == "pass")
    moving = series(stalled, presents=(140, 180, 220), binds=(900, 900, 901), mode=1, vanilla=1)
    check("vanilla: a single redirected bind fails", verdict("vanilla", moving) == "fail")
    check(
        "vanilla: a region still advancing fails",
        verdict("vanilla", series(live_regions, binds=(5, 5, 5), mode=1, vanilla=1)) == "fail",
    )
    check(
        "vanilla: the flag clear fails",
        verdict("vanilla", series(stalled, presents=(140, 180, 220), binds=(0, 0, 0))) == "fail",
    )
    check(
        "vanilla: too few presents to show a stall is inconclusive",
        verdict(
            "vanilla",
            series(stalled, presents=(140, 142, 144), binds=(0, 0, 0), mode=1, vanilla=1),
        )
        == "inconclusive",
    )
    young = series(
        lambda p: [synthetic_region("ao", 132, 0)],
        presents=(140, 141, 142),
        binds=(0, 0, 0),
        vanilla=1,
    )
    check(
        "vanilla: an age that has not grown past --within is not a stall",
        not liveness(young, "ao").stalled(LIVE_WITHIN),
    )
    check(
        "vanilla: a region never written passes",
        verdict(
            "vanilla",
            series(lambda p: [], presents=(140, 180, 220), binds=(0, 0, 0), vanilla=1),
        )
        == "pass",
    )

    # identity
    tonemap_off = series(
        lambda p: [synthetic_region("ao", p - 3, 0), synthetic_region("tonemap", 60, 3)],
        stage_active=0b011,
    )
    check(
        "identity: the stage off and its region stalled passes",
        verdict("identity", tonemap_off) == "pass",
    )
    check(
        "identity: the stage bit set fails",
        verdict(
            "identity",
            series(lambda p: [synthetic_region("tonemap", 60, 3)], stage_active=0b100),
        )
        == "fail",
    )
    check(
        "identity: the region still advancing fails",
        verdict("identity", series(live_regions, stage_active=0b011)) == "fail",
    )
    check(
        "identity: vanilla set fails",
        verdict("identity", series(lambda p: [], stage_active=0, vanilla=1)) == "fail",
    )
    check(
        "identity: --region picks the region",
        verdict("identity", tonemap_off, regions=["ao"]) == "fail",
    )

    # invert and red
    def colour_series(post_of, aux_of=lambda index: 0, region="tonemap", kind=4, low=0):
        return series(
            lambda p: [
                synthetic_region(region, p - 3, kind, samples_with(post_of, aux_of), low)
            ]
        )

    def inverted(index, pre):
        return invert_of(pre)

    def nudged(index, pre):
        return tuple(min(1.0, c + 1.5 / 255) for c in invert_of(pre))

    check("invert: 1 - pre passes", verdict("invert", colour_series(inverted)) == "pass")
    check(
        "invert: 1.5/255 off is within tolerance",
        verdict("invert", colour_series(nudged)) == "pass",
    )

    def mostly(count):
        def post(index, pre):
            return invert_of(pre) if index < count else pre

        return post

    check(
        "invert: 48 matching samples pass and 47 fail",
        verdict("invert", colour_series(mostly(48))) == "pass"
        and verdict("invert", colour_series(mostly(47))) == "fail",
    )
    check("invert: the identity fails", verdict("invert", colour_series(lambda i, p: p)) == "fail")
    flat = series(
        lambda p: [
            synthetic_region(
                "tonemap",
                p - 3,
                4,
                samples_with(inverted, colours=[(0.3, 0.3, 0.3)] * SAMPLES),
            )
        ]
    )
    check("invert: a flat scene is inconclusive", verdict("invert", flat) == "inconclusive")
    sparse = series(
        lambda p: [
            synthetic_region(
                "tonemap", p - 3, 4, samples_with(inverted, colours=grid_colours()[:40])
            )
        ]
    )
    check(
        "invert: fewer samples than --min-samples is inconclusive, not a fail",
        verdict("invert", sparse) == "inconclusive"
        and verdict("invert", sparse, min_samples=40) == "pass",
    )
    check(
        "invert: a stalled region fails before the colours are read",
        verdict(
            "invert",
            series(lambda p: [synthetic_region("tonemap", 50, 4, samples_with(inverted))]),
        )
        == "fail",
    )
    versioned = colour_series(inverted, low=7)
    check(
        "invert: --variant-id must match exactly",
        verdict("invert", versioned, variant_id=(4 << 24) | 7) == "pass"
        and verdict("invert", versioned, variant_id=(4 << 24) | 6) == "fail",
    )
    check(
        "invert: --newer-than needs the same kind and a larger build",
        verdict("invert", versioned, newer_than=(4 << 24) | 6) == "pass"
        and verdict("invert", versioned, newer_than=(4 << 24) | 7) == "fail"
        and verdict("invert", versioned, newer_than=(3 << 24) | 1) == "fail",
    )
    check(
        "invert: --region reaches another region",
        verdict("invert", colour_series(inverted, region="ao", kind=0), regions=["ao"]) == "pass",
    )
    check("red: (pre.r, 0, 0) passes", verdict("red", colour_series(lambda i, p: red_of(p))) == "pass")
    check("red: the identity fails", verdict("red", colour_series(lambda i, p: p)) == "fail")
    reds_only = series(
        lambda p: [
            synthetic_region(
                "tonemap",
                p - 3,
                4,
                samples_with(
                    lambda i, pre: red_of(pre),
                    colours=[(i / 63, 0.0, 0.0) for i in range(SAMPLES)],
                ),
            )
        ]
    )
    check(
        "red: a scene with no green or blue is inconclusive",
        verdict("red", reds_only) == "inconclusive",
    )

    # masked-invert
    def depth_aux(index: int) -> int:
        return struct.unpack("<I", struct.pack("<f", index / 63))[0]

    def masked(predicate):
        def post(index, pre):
            return invert_of(pre) if predicate(index) else pre

        return post

    near = colour_series(masked(lambda i: i / 63 >= 0.5), depth_aux, region="ao", kind=0)
    check(
        "masked-invert: a depth range at ao passes",
        verdict("masked-invert:depth:0.5..1", near, regions=["ao"]) == "pass",
    )
    check(
        "masked-invert: the complement does not",
        verdict("masked-invert:!depth:0.5..1", near, regions=["ao"]) == "fail",
    )
    check(
        "masked-invert: a wrong boundary fails",
        verdict("masked-invert:depth:0.4..1", near, regions=["ao"]) == "fail",
    )
    one_off = colour_series(
        masked(lambda i: i / 63 >= 0.5 and i != 40), depth_aux, region="ao", kind=0
    )
    check(
        "masked-invert: one wrong sample fails, unless --max-mismatch allows it",
        verdict("masked-invert:depth:0.5..1", one_off, regions=["ao"]) == "fail"
        and verdict("masked-invert:depth:0.5..1", one_off, regions=["ao"], max_mismatch=1)
        == "pass",
    )
    left = colour_series(masked(lambda i: i % GRID < GRID // 2))
    check(
        "masked-invert: a screen rectangle passes",
        verdict("masked-invert:rect:0,0,0.5,1", left) == "pass",
    )
    check(
        "masked-invert: a conjunction narrows the mask",
        verdict(
            "masked-invert:rect:0,0,0.5,1 & !rect:0,0.5,1,1",
            colour_series(masked(lambda i: i % GRID < GRID // 2 and i // GRID < GRID // 2)),
        )
        == "pass",
    )
    check(
        "masked-invert: everything inverted is one class, inconclusive",
        verdict("masked-invert:everything", colour_series(inverted)) == "inconclusive",
    )
    check(
        "masked-invert: a luminance range passes",
        verdict(
            "masked-invert:luma:0.5..2",
            colour_series(masked(lambda i: luma(grid_colours()[i]) >= 0.5)),
        )
        == "pass",
    )

    def sky_aux(index: int) -> int:
        return RESOLVED_SKY_FLAG if index < 16 else RESOLVED_GEOMETRY_FLAG

    check(
        "masked-invert: the resolved plane's sky flag passes",
        verdict("masked-invert:sky", colour_series(masked(lambda i: i < 16), sky_aux)) == "pass"
        and verdict("masked-invert:geometry", colour_series(masked(lambda i: i >= 16), sky_aux))
        == "pass",
    )

    # Design 14, P0.2a item 5: a Lighting layer limited to `depth:0..10m`, as the editor writes
    # the selector, checked against what the AO port writes in its aux -- the view depth in
    # metres, -1 on the sky (`er_probe_lighting_aux`). The top row is sky; the rest runs 0 to 30 m.
    def metres(index: int) -> float:
        return AO_SKY_DEPTH if index < GRID else (index - GRID) * 30 / (SAMPLES - GRID - 1)

    def metres_aux(index: int) -> int:
        return struct.unpack("<I", struct.pack("<f", metres(index)))[0]

    def within_10m(index: int) -> bool:
        return 0 <= metres(index) <= 10

    lighting = colour_series(masked(within_10m), metres_aux, region="ao", kind=0)
    check(
        "masked-invert: the editor's depth:0..10m at ao passes, the sky left out",
        verdict("masked-invert:depth:0..10m", lighting, regions=["ao"]) == "pass",
    )
    check(
        "masked-invert: the editor's inverted, feathered form reads the same mask",
        verdict(
            "masked-invert:!depth:0..10m ~2px",
            colour_series(masked(lambda i: not within_10m(i)), metres_aux, region="ao", kind=0),
            regions=["ao"],
        )
        == "pass",
    )
    check(
        "masked-invert: a depth range read as raw depth would fail",
        verdict("masked-invert:depth:0..1", lighting, regions=["ao"]) == "fail",
    )
    check(
        "masked-invert: at ao the sky and geometry come from the aux's sky marker",
        verdict(
            "masked-invert:sky",
            colour_series(masked(lambda i: i < GRID), metres_aux, region="ao", kind=0),
            regions=["ao"],
        )
        == "pass"
        and verdict(
            "masked-invert:geometry",
            colour_series(masked(lambda i: i >= GRID), metres_aux, region="ao", kind=0),
            regions=["ao"],
        )
        == "pass",
    )
    check(
        "masked-invert: the editor's rect form passes like the comma form",
        verdict("masked-invert:rect:0,0..0.5,1", left) == "pass",
    )
    check(
        "masked-invert: facing cannot be told from the probe, inconclusive",
        verdict("masked-invert:facing:0..0.3", lighting, regions=["ao"]) == "inconclusive",
    )

    # One grammar with the editor: every selector `editor/mask.rs` holds itself to reads here,
    # with the same bounds where its own test states them.
    mask_rs = (REPO / "crates/er-cel-shade/src/editor/mask.rs").read_text(encoding="utf-8")

    def test_body(name: str) -> str:
        return mask_rs.partition(f"fn {name}")[2].partition("#[test]")[0]

    # The two tests that assert the editor reads their selectors with no warning.
    rendered_texts = re.findall(
        r'"([^"]*)"', test_body("every_mask_renders_back_to_itself").partition("]")[0]
    )
    parsed_texts = re.findall(r'parse\("([^"]+)"\)', test_body("the_design_examples_parse"))
    check(
        "the editor's mask tests hold selectors to read",
        len(rendered_texts) >= 7 and len(parsed_texts) >= 4,
    )
    unread = []
    for text in rendered_texts + parsed_texts:
        try:
            parse_mask(text)
        except ValueError as error:
            unread.append(f"{text}: {error}")
    check(f"every selector the editor writes reads here ({'; '.join(unread)})", not unread)
    check(
        "the editor's own bounds read the same",
        parse_mask("depth:0..10m").terms[0].args == (0.0, 10.0)
        and parse_mask("rect:0.5,1..0,0").terms[0].args == (0.0, 0.0, 0.5, 1.0)
        and parse_mask("!luma:0.5..2 ~2px").terms[0].negate
        and parse_mask("depth:1..0").terms[0].args == (0.0, 1.0),
    )

    # tint
    def tinted(bit):
        def post(index, pre):
            return tuple(c * t for c, t in zip(pre, DEFAULT_TINT)) if index % 5 == 0 else pre

        return post

    def bit_aux(bit):
        return lambda index: (1 << bit) if index % 5 == 0 else 0

    check("tint: the marked samples tinted passes", verdict("tint:3", colour_series(tinted(3), bit_aux(3))) == "pass")
    check(
        "tint: the wrong bit fails",
        verdict("tint:2", colour_series(tinted(3), bit_aux(3))) == "fail",
    )
    check(
        "tint: no sample carries the bit, inconclusive",
        verdict("tint:3", colour_series(lambda i, p: p)) == "inconclusive",
    )
    check(
        "tint: an unmarked sample changed fails",
        verdict(
            "tint:3",
            colour_series(lambda i, p: invert_of(p) if i == 1 else tinted(3)(i, p), bit_aux(3)),
        )
        == "fail",
    )

    # cost: a session of 4000 presents at the census's rates, every timing's raw sum including
    # the 20 ns floor.
    def timed(calls_per_frame: float, raw_mean: int, presents: int = 4000) -> tuple[int, int]:
        samples = int(calls_per_frame * presents / 256)
        return samples * raw_mean, samples

    def cost(hooks, presents: int = 4000, **extra) -> str:
        doc = synthetic_doc(presents, 1, [], hooks=hooks, **extra)
        return verdict("cost", [parse_snapshot(doc)])

    census = {
        "SetPipelineState": timed(1760, 30),
        "Reset": timed(105, 45),
        "ClearState": (0, 0),
        "Close": timed(100, 50),
        "CloseProbeCopy": timed(5, 900),
    }
    check("cost: the census's rates within every budget pass", cost(census) == "pass")
    check(
        "cost: a SetPipelineState over 20 ns fails",
        cost({**census, "SetPipelineState": timed(1760, 45)}) == "fail",
    )
    check(
        "cost: a Close that copies is held to 2 us, apart from a plain one",
        cost({**census, "CloseProbeCopy": timed(5, 1900)}) == "pass"
        and cost({**census, "CloseProbeCopy": timed(5, 2500)}) == "fail"
        and cost({**census, "Close": timed(100, 61)}) == "fail",
    )
    check(
        "cost: a timing with too few timed calls is not judged",
        cost({**census, "CloseProbeCopy": (5 * 9000, 5)}) == "pass",
    )
    check(
        "cost: without SetPipelineState samples the run cannot tell",
        cost({**census, "SetPipelineState": (0, 0)}) == "inconclusive",
    )
    check(
        "cost: every call within its budget can still be too much per frame",
        cost({**census, "SetPipelineState": timed(20_000, 39)}) == "fail",
    )
    check(
        "cost: telemetry without hook timings is inconclusive",
        verdict("cost", [parse_snapshot(synthetic_doc(4000, 1, []))]) == "inconclusive",
    )
    report = evaluate(parse_expectation("cost"), [parse_snapshot(synthetic_doc(
        4000, 1, [], hooks=census))], Options())
    check(
        "cost: each timing prints its mean less the floor, and the frame its total",
        "timing=SetPipelineState samples=27500 mean_ns=10 budget_ns=20 judged=1 within=1"
        in report.lines
        and any(line.startswith("per_frame_ns=") for line in report.lines),
    )

    # gpu-cost: the constants are gpu_time.rs's, and the shape is the one exports.rs renders.
    gpu_rs = (crate / "gpu_time.rs").read_text(encoding="utf-8")
    budget_rs = rust_constant(gpu_rs, "PORT_BUDGET_US")
    check(
        "the GPU budget and read lag are gpu_time.rs's",
        budget_rs is not None
        and float(budget_rs) == GPU_PORT_BUDGET_US
        and rust_constant(gpu_rs, "READ_LAG_PRESENTS") == str(GPU_READ_LAG_PRESENTS),
    )
    check(
        "exports.rs writes the gpu_time keys the gpu-cost check reads",
        all(
            needle in exports_rs
            for needle in (
                '"  \\"gpu_time\\": "',
                '\\"frequency_hz\\": {}',
                '\\"spans_begun\\": {}',
                '\\"late\\": {}',
                '\\"refused\\": {}',
                '{{\\"kind\\": \\"{}\\", \\"index\\": {}, \\"us_last\\": ',
                '\\"stages\\": [',
                '{{\\"stage\\": \\"{}\\", \\"us_last\\": ',
            )
        ),
    )
    # The exact text the Rust test renders (`the_telemetry_carries_each_ports_gpu_time`).
    rendered_gpu = json.loads(
        '{"frequency_hz": 1000000000, "source": "game_direct_queue", "read_lag_presents": 5, '
        '"budget_us": 1500, "history_frames": 32, "frame": 41, "frames": 1, "unknown_frames": 0, '
        '"mean_frames": 1, "spans_begun": 9, "spans_dropped": 1, "chunks": 4, "resolves": 3, '
        '"late": 0, "refused": 0, "ports": ['
        '{"kind": "ao", "index": 0, "us_last": 1600.000, "us_mean": 1600.000, "spans_last": 1, '
        '"bound_last": "game", "ticks_last": 1600000}, '
        '{"kind": "tonemap", "index": 3, "us_last": 412.500, "us_mean": 412.500, '
        '"spans_last": 2, "bound_last": "variant", "ticks_last": 412500}], "stages": ['
        '{"stage": "lighting", "us_last": 1600.000, "us_mean": 1600.000}, '
        '{"stage": "tonemap", "us_last": null, "us_mean": null}]}'
    )
    parsed_gpu = parse_gpu(rendered_gpu)
    check(
        "the Rust's gpu_time parses, null read as unknown",
        isinstance(parsed_gpu, GpuTime)
        and parsed_gpu.ports["tonemap"].us_mean == 412.5
        and parsed_gpu.ports["ao"].bound_last == "game"
        and parsed_gpu.stages["tonemap"] == (None, None)
        and parsed_gpu.frame == 41
        and parse_gpu(None) == GPU_OFF,
    )
    for bad in ({"ports": [{"kind": "bloom"}]}, [1], {"frequency_hz": "1"}):
        try:
            parse_gpu(bad)
            check(f"a bad gpu_time is refused ({str(bad)[:30]})", False)
        except TelemetryError:
            check(f"a bad gpu_time is refused ({str(bad)[:30]})", True)

    # A session drawing AO, the albedo pass and the LUT tonemap every frame (the live_regions
    # kinds), 30 frames read between snapshots and three spans a frame.
    drawn = {"ao": 410.0, "cmb_albedo": 120.0, "tonemap_lut": 300.0}

    def gpu_series(
        means=drawn,
        *,
        frames=(100, 130, 160),
        begun=(300, 390, 480),
        late=(0, 0, 0),
        dropped=(0, 0, 0),
        refused=(0, 0, 0),
        off=False,
        **kwargs,
    ) -> list[Snapshot]:
        return [
            parse_snapshot(
                synthetic_doc(
                    present,
                    1000 * at,
                    live_regions(present),
                    gpu=None
                    if off
                    else synthetic_gpu(
                        frames[at],
                        begun[at],
                        means,
                        late=late[at],
                        dropped=dropped[at],
                        refused=refused[at],
                    ),
                    **kwargs,
                )
            )
            for at, present in enumerate((200, 230, 260))
        ]

    check(
        "gpu-cost: ports drawn every frame cost something, pass",
        verdict("gpu-cost", gpu_series()) == "pass",
    )
    report = evaluate(parse_expectation("gpu-cost"), gpu_series(), Options())
    stage_line = "gpu_stage=tonemap region=tonemap us_last=300.0"
    check(
        "gpu-cost: each port and stage prints its microseconds",
        "gpu_port=tonemap_lut us_last=300.0 us_mean=300.0 spans_last=1 bound_last=variant "
        "budget_us=1500 within=1" in report.lines
        and any(line.startswith(stage_line) for line in report.lines)
        and any(
            "frames_delta=60" in line and "spans_begun_delta=180" in line for line in report.lines
        ),
    )
    check(
        "gpu-cost: an expected region whose ports cost nothing fails",
        verdict("gpu-cost", gpu_series({**drawn, "tonemap_lut": 0.0})) == "fail",
    )
    check(
        "gpu-cost: a stage without content is not expected to cost anything",
        verdict("gpu-cost", gpu_series({**drawn, "tonemap_lut": 0.0}, stage_active=3)) == "pass",
    )
    check(
        "gpu-cost: a port over 1.5 ms fails, unless the budget is raised for the experiment",
        verdict("gpu-cost", gpu_series({**drawn, "ao": 1600.0})) == "fail"
        and verdict("gpu-cost", gpu_series({**drawn, "ao": 1600.0}), gpu_budget_us=2000.0)
        == "pass",
    )
    check(
        "gpu-cost: --kinds names a kind that must cost something",
        verdict("gpu-cost", gpu_series(), kinds=[4]) == "pass"
        and verdict("gpu-cost", gpu_series(), kinds=[3]) == "fail",
    )
    check("gpu-cost: the timer off fails", verdict("gpu-cost", gpu_series(off=True)) == "fail")
    check(
        "gpu-cost: a build with no gpu_time is inconclusive",
        verdict("gpu-cost", series(live_regions)) == "inconclusive",
    )
    check(
        "gpu-cost: vanilla times nothing, inconclusive",
        verdict("gpu-cost", gpu_series(vanilla=1)) == "inconclusive",
    )
    check(
        "gpu-cost: frames not advancing fail",
        verdict("gpu-cost", gpu_series(frames=(100, 100, 100))) == "fail"
        and verdict("gpu-cost", gpu_series(begun=(300, 300, 300))) == "fail",
    )
    check(
        "gpu-cost: late, dropped or refused spans make it inconclusive",
        # A tenth of the 180 spans begun is 18 late.
        verdict("gpu-cost", gpu_series(late=(0, 10, 18))) == "pass"
        and verdict("gpu-cost", gpu_series(late=(0, 10, 19))) == "inconclusive"
        and verdict("gpu-cost", gpu_series(dropped=(0, 0, 1))) == "inconclusive"
        and verdict("gpu-cost", gpu_series(refused=(0, 1, 1))) == "inconclusive",
    )
    baseline = gpu_series()[-1]
    heavier = gpu_series({**drawn, "tonemap_lut": 520.0})
    check(
        "gpu-cost: against a baseline, a heavier layer's stage and kind rise",
        verdict("gpu-cost", heavier, baseline=baseline, regions=["tonemap"], min_rise_us=100.0)
        == "pass"
        and verdict("gpu-cost", heavier, baseline=baseline, kinds=[4], min_rise_us=100.0)
        == "pass",
    )
    check(
        "gpu-cost: a rise under --min-rise-us fails, and a baseline names what must rise",
        verdict("gpu-cost", heavier, baseline=baseline, regions=["tonemap"], min_rise_us=300.0)
        == "fail"
        and verdict("gpu-cost", heavier, baseline=baseline) == "inconclusive"
        and verdict(
            "gpu-cost", heavier, baseline=series(live_regions)[-1], regions=["tonemap"]
        )
        == "inconclusive",
    )

    # Parsing refuses what it cannot mean.
    for bad in (
        "masked-invert:",
        "masked-invert:depth:0.5",
        "masked-invert:depth:0..inf",
        "masked-invert:rect:0,0,1",
        "masked-invert:rect:0,0..1",
        "masked-invert:depth:0..1 ~two",
        "masked-invert:bits:256",
        "masked-invert:fog",
        "tint:8",
        "tint:x",
        "glow",
    ):
        try:
            parse_expectation(bad)
            check(f"{bad!r} is refused", False)
        except ValueError:
            check(f"{bad!r} is refused", True)
    check(
        "port kinds parse by name or number",
        parse_kinds("ao_ssr,4") == [1, 4] and parse_regions("ao,ao,tonemap") == ["ao", "tonemap"],
    )

    # The vertex proof: the names are the Rust's.
    vertex_rs = (crate / "vertex" / "mod.rs").read_text(encoding="utf-8")
    names_rs = (crate / "vertex" / "names.rs").read_text(encoding="utf-8")
    project_rs = (crate / "editor" / "project.rs").read_text(encoding="utf-8")
    process_rs = (crate / "vertex" / "process.rs").read_text(encoding="utf-8")
    check(
        "the proof file and the control file are vertex/mod.rs's",
        f'PROOF_FILE_NAME: &str = "{PROOF_NAME}"' in vertex_rs
        and f'FILE_NAME: &str = "{CONTROL_NAME}"' in vertex_rs,
    )
    check(
        "the editor's directory is project.rs's",
        f'pub const DIRECTORY: &str = "{EDITOR_DIRECTORY}"' in project_rs,
    )
    token_fn = names_rs[names_rs.index("pub const fn token(self)") :]
    token_fn = token_fn[: token_fn.index("\n    }\n")]
    check(
        "the pass tokens are names.rs's, in bit order",
        tuple(re.findall(r'Self::\w+ => "(\w+)",', token_fn)) == PASS_TOKENS
        and "1 << self as u32" in names_rs,
    )
    check(
        "the control file's keys are the ones Control::parse reads",
        all(f'"{key}" =>' in vertex_rs for key in ("proof", "passes", "vertex"))
        and '"on" => control.hold_off = false' in vertex_rs,
    )
    check(
        "process.rs writes the r1 counters the R1 check reads",
        all(
            f'\\"{key}\\": {{}}' in process_rs
            for key in ("lists", "lists_straddling", "frames", "frames_mixed", "max_span")
        ),
    )
    check(
        "the skip covers the read lag and two frames after an edge",
        SKIP_AFTER_SWITCH >= READ_LAG_PRESENTS + 2,
    )
    rust_line = '{"p":103,"f":100,"t":1.5,"v":true,"m":1023,"g":2,"x":[[4,14,null,1,2.5]]}'
    check(
        "the proof line parsed here is the one mod.rs's own test renders",
        rust_line.replace('"', '\\"') in vertex_rs,
    )
    line = parse_proof_line(rust_line)
    check(
        "a proof line parses, a value that was not finite read as unknown",
        line.present == 103
        and line.frame == 100
        and line.vertex_on
        and line.passes == ALL_PASSES
        and line.generation == 2
        and line.samples == {4: ProofSample(14.0, None, 1.0, 2.5)},
    )
    for bad in ('{"p":1}', '{"p":1,"f":1,"v":1,"m":0,"g":0,"x":[[64,1,1,1,1]]}', "[1]"):
        try:
            parse_proof_line(bad)
            check(f"a bad proof line is refused ({bad[:24]})", False)
        except (ValueError, TypeError):
            check(f"a bad proof line is refused ({bad[:24]})", True)
    check(
        "the oracle S control file narrows to the depth passes",
        control_text(3, False, SHADOW_PASSES)
        == "# check-shader-editor-probe.py, phase 3\nproof = 1\npasses = Dpt,DptA\nvertex = off\n"
        and "passes" not in control_text(0, True, ALL_PASSES),
    )

    def jitter(index: int, frame: int) -> float:
        """A deterministic +-0.001 m of depth noise."""
        return (((index * 7919 + frame * 104729) % 2001) - 1000) / 1_000_000

    def proof(states, depth_of, *, lag=0, passes=ALL_PASSES, generation=1, dot_of=None,
              light_of=None) -> list[ProofFrame]:
        """Frames as the DLL writes them: `states` is (vertex on, frames) runs, and the first `lag`
        frames of each run still show the state before it."""
        frames, present, previous = [], 1000, False
        for on, count in states:
            for at in range(count):
                present += 1
                frame = present - READ_LAG_PRESENTS
                shown = on if at >= lag else previous
                x = [
                    [
                        index,
                        depth_of(index, frame, shown),
                        dot_of(index, frame, shown) if dot_of else 0.95,
                        1,
                        light_of(index, frame, shown) if light_of else 1.0,
                    ]
                    for index in range(SAMPLES)
                ]
                frames.append(parse_proof_line(json.dumps(
                    {"p": present, "f": frame, "t": frame / 60, "v": on, "m": passes,
                     "g": generation, "x": x}
                )))
            previous = on
        return frames

    def lifted(moving: int, lift: float = 0.3, drift: float = 0.0):
        def depth(index: int, frame: int, on: bool) -> float:
            if index >= 48:
                return -1.0
            up = lift if on and index < moving else 0.0
            return 10 + index * 0.25 + jitter(index, frame) + up + drift * frame
        return depth

    d_states = [(False, 30), (True, 30)] * 5
    check(
        "vertex-d: most geometry moving with the stage passes",
        check_vertex_d(proof(d_states, lifted(40))).verdict == "pass",
    )
    check(
        "vertex-d: the stale frames after each switch are left out",
        check_vertex_d(proof(d_states, lifted(40), lag=READ_LAG_PRESENTS)).verdict == "pass",
    )
    check(
        "vertex-d: too few moving samples fails",
        check_vertex_d(proof(d_states, lifted(10))).verdict == "fail",
    )
    report = check_vertex_d(proof(d_states, lifted(40, drift=0.002)))
    check(
        "vertex-d: a scene that moves with the stage off is inconclusive, never a pass",
        (report.verdict, report.why) == ("inconclusive", "scene_moves_with_the_stage_off"),
    )
    check(
        "vertex-d: a stage that never came on is inconclusive",
        check_vertex_d(proof([(False, 300)], lifted(40))).why == "vertex_stage_never_on",
    )
    check(
        "vertex-d: a view of sky only is inconclusive",
        check_vertex_d(proof(d_states, lambda i, f, on: -1.0)).why == "too_few_geometry_samples",
    )
    changed = proof(d_states[:4], lifted(40)) + [
        dataclass_replace(frame, generation=2) for frame in proof(d_states[4:], lifted(40))
    ]
    check(
        "vertex-d: a carrier rebuilt during the run is inconclusive",
        check_vertex_d(changed).why == "carrier_changed_during_the_run",
    )

    def parity(moving: int, lift: float = 0.3, flip_every: int = 0):
        def depth(index: int, frame: int, on: bool) -> float:
            if index >= 48:
                return -1.0
            up = on and frame % 2 == 1
            if on and flip_every and frame % flip_every == 0 and index < moving // 2:
                up = not up
            return 10 + index * 0.25 + jitter(index, frame) + (lift if up and index < moving else 0)
        return depth

    f_states = [(False, 60), (True, 700)]
    report = check_vertex_f(proof(f_states, parity(40)))
    check("vertex-f: every pass of every frame agreeing passes", report.verdict == "pass")
    check(
        "vertex-f: the share against the AO port's parity is printed",
        any("against_ao_parity=0 " in line for line in report.lines),
    )
    check(
        "vertex-f: a frame some of whose samples stay behind is mixed, and fails",
        check_vertex_f(proof(f_states, parity(40, flip_every=20))).why == "mixed_frames",
    )
    check(
        "vertex-f: holes opened over calibration's fail",
        check_vertex_f(
            proof(f_states, parity(40), dot_of=lambda i, f, on: 0.2 if on and f % 10 == 0 else 0.95)
        ).why
        == "holes",
    )
    check(
        "vertex-f: too few frames is inconclusive",
        check_vertex_f(proof([(False, 60), (True, 200)], parity(40))).why == "too_few_frames",
    )
    check(
        "vertex-f: no calibration is inconclusive",
        check_vertex_f(proof([(False, 10), (True, 700)], parity(40))).why
        == "too_little_calibration",
    )
    check(
        "vertex-f: a static lift has no moving samples to judge",
        check_vertex_f(proof(f_states, lifted(40))).why == "too_few_moving_samples",
    )

    def shadow(count: int):
        return lambda index, frame, on: 0.3 if on and index < count else 1.0

    s_states = [(False, 30), (True, 30)] * 4
    check(
        "vertex-s: shadows that move while the depth stays pass",
        check_vertex_s(
            proof(s_states, lifted(0), light_of=shadow(6), passes=SHADOW_PASSES)
        ).verdict
        == "pass",
    )
    check(
        "vertex-s: too few shadow samples is inconclusive, not a failure",
        check_vertex_s(
            proof(s_states, lifted(0), light_of=shadow(2), passes=SHADOW_PASSES)
        ).why
        == "too_few_shadow_samples",
    )
    check(
        "vertex-s: light that changes where the depth moved is not a shadow",
        check_vertex_s(
            proof(s_states, lifted(40), light_of=shadow(6), passes=SHADOW_PASSES)
        ).why
        == "too_few_shadow_samples",
    )
    check(
        "vertex-s: passes that were not narrowed to the depth pipelines are inconclusive",
        check_vertex_s(proof(s_states, lifted(0), light_of=shadow(6))).why
        == "passes_not_narrowed_to_depth",
    )

    def r1_doc(frames: int, mixed: int, lists: int = 500) -> dict:
        return {
            "written_ms": frames,
            "r1": {"lists": lists, "lists_straddling": mixed, "frames": frames,
                   "frames_mixed": mixed, "max_span": 1 if mixed else 0},
        }

    check(
        "vertex-r1: no present binding two blocks passes",
        check_vertex_r1([r1_doc(100, 0, 0), r1_doc(220, 0, 900)]).verdict == "pass",
    )
    check(
        "vertex-r1: a present whose lists bound two blocks fails",
        check_vertex_r1([r1_doc(100, 0, 0), r1_doc(220, 2, 900)]).why
        == "frames_straddle_presents",
    )
    check(
        "vertex-r1: too few presents, no lists or no counters is inconclusive",
        check_vertex_r1([r1_doc(100, 0, 0), r1_doc(120, 0, 900)]).why == "too_few_presents"
        and check_vertex_r1([r1_doc(100, 0, 9), r1_doc(220, 0, 9)]).why
        == "no_list_bound_a_block"
        and check_vertex_r1([{"written_ms": 1}, {"written_ms": 2}]).why == "no_r1_counters",
    )
    check(
        "the vertex status line reads the telemetry's vertex object",
        vertex_status_line({"vertex": {"generation": 2, "layers": [1], "groups": {"live": 3},
                                       "binds_redirects": {"Gbuf": [10, 9], "Dpt": [0, 0]}}})
        .startswith("vertex generation=2 layers=1 ")
        and "groups=live:3" in vertex_status_line({"vertex": {"groups": {"live": 3}}})
        and "redirects=Gbuf:9/10" in vertex_status_line(
            {"vertex": {"binds_redirects": {"Gbuf": [10, 9], "Dpt": [0, 0]}}}
        ),
    )
    check(
        "every vertex run fits the limit on a wait",
        all(
            sum(s for _, s in vertex_schedule(kind, DEFAULT_CYCLES, DEFAULT_PHASE_SECONDS))
            + FLUSH_WAIT_SECONDS
            <= MAX_TIMEOUT_SECONDS
            for kind in VERTEX_EXPECTATIONS
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        control, proof_file = root / "editor" / CONTROL_NAME, root / PROOF_NAME
        proof_file.write_text(
            json.dumps({"p": 5, "f": 2, "t": None, "v": False, "m": 1023, "g": 0, "x": []})
            + "\n"
            + json.dumps({"p": 9, "f": 6, "t": 0.1, "v": True, "m": 1023, "g": 1, "x": []})
            + "\n{\"p\":10,\"f\"",
            encoding="utf-8",
        )
        read = read_proof(proof_file, 5)
        check(
            "the proof reader keeps the frames after the start and drops a cut line",
            [frame.present for frame in read] == [9],
        )
        drive(control, proof_file, [(False, 0.05), (True, 0.05)], ALL_PASSES, flush_seconds=0.05)
        check("a run leaves no control file where there was none", not control.exists())
        control.write_text("proof = 0\n", encoding="utf-8")
        drive(control, proof_file, [(True, 0.05)], SHADOW_PASSES, flush_seconds=0.05)
        check(
            "a run puts a control file it found back as it was",
            control.read_text(encoding="utf-8") == "proof = 0\n",
        )

    # The file reader: each snapshot is a write after the last, and a quiet file times out.
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / TELEMETRY_NAME
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(synthetic_doc(100, 5, live_regions(100))), encoding="utf-8")
        pending.replace(path)
        got = collect(path, 1, 1.0, fresh_after=4)
        check("a write after fresh_after is taken", len(got) == 1 and got[0].present == 100)
        check("the current write is the one to beat", current_written_ms(path) == 5)
        started = time.monotonic()
        got = collect(path, 2, 0.3, fresh_after=4)
        waited = time.monotonic() - started
        check(
            "a file nobody rewrites gives one snapshot and ends on the timeout",
            len(got) == 1 and 0.25 <= waited < 2.0,
        )
        check("an unchanged write is not taken again", collect(path, 1, 0.1, fresh_after=5) == [])
        check(
            "an explicit path wins the resolution",
            resolve_telemetry(path) == path,
        )

    failed = [label for label, ok in results if not ok]
    for label, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
