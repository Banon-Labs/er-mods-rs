#!/usr/bin/env python3
"""Check what er-cel-shade's pixel probe says, from the telemetry file the DLL writes (design 14).

# What is read

Once a second the DLL's worker replaces `er-cel-shade-telemetry.json` (`src/exports.rs`): the
stats, `stage_active`, and the probe as the CPU read it back three presents behind -- each written
region's header (frame, variant id, size) and its 8x8 grid of samples, each with the colour before
the user's layers (`pre`), after them (`post`) and one more word (`aux`).

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

`--variant-id` and `--newer-than` add a condition on the region's header for the colour checks:
the exact id, or the same port kind with a larger build number in the low 24 bits.

# Masks

Terms joined by `&`, each optionally negated with a leading `!`:

* `everything`
* `depth:<lo>..<hi>` -- the depth the region's aux carries, compared in its own units: at `ao`
  the aux is the raw depth buffer value (`asuint(depth)`), at `albedo` and `tonemap` the resolved
  plane's view depth as a half float in the high 16 bits (design 5.4, from P2).
* `sky`, `geometry` -- the resolved plane's flags, bits 9 and 8 of the aux (design 5.4, from P2).
* `bits:<mask>` -- any of the selector bits in the aux's low byte (P3).
* `luma:<lo>..<hi>` -- the Rec. 709 luminance of `pre`.
* `rect:<x0>,<y0>,<x1>,<y1>` -- the sample's grid centre, as a screen coordinate from 0 to 1.

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

RESOLVED_BITS_MASK = 0xFF
RESOLVED_GEOMETRY_FLAG = 1 << 8
RESOLVED_SKY_FLAG = 1 << 9

U32 = 1 << 32
HALF_U32 = 1 << 31

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2


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
    deadline = time.monotonic() + min(max(timeout, 0.0), MAX_TIMEOUT_SECONDS)
    snapshots: list[Snapshot] = []
    seen = fresh_after
    with er_run_lib.DirectoryWatch(path.parent) as watch:
        while len(snapshots) < count:
            try:
                snapshot = read_snapshot(path)
            except (OSError, ValueError):
                snapshot = None
            if snapshot is not None and snapshot.written_ms != seen:
                snapshots.append(snapshot)
                seen = snapshot.written_ms
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            block(watch, min(WAIT_SLICE_SECONDS, remaining))
    return snapshots


# Masks


def depth_of(region: str, aux: int) -> float:
    """The depth a sample's aux carries in `region`'s units (see the module docs)."""
    if region == "ao":
        return struct.unpack("<f", struct.pack("<I", aux & 0xFFFF_FFFF))[0]
    return struct.unpack("<e", struct.pack("<H", (aux >> 16) & 0xFFFF))[0]


def luma(colour: tuple[float, float, float]) -> float:
    return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]


def _range(text: str, term: str) -> tuple[float, float]:
    low, sep, high = text.partition("..")
    if not sep:
        raise ValueError(f"{term}: expected <lo>..<hi>")
    try:
        bounds = (float(low), float(high))
    except ValueError as error:
        raise ValueError(f"{term}: {error}") from None
    if not bounds[0] <= bounds[1]:
        raise ValueError(f"{term}: the low bound is above the high one")
    return bounds


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
            hit = None if math.isnan(depth) else self.args[0] <= depth <= self.args[1]
        elif self.kind == "luma":
            hit = None if sample.pre is None else self.args[0] <= luma(sample.pre) <= self.args[1]
        elif self.kind == "rect":
            u, v = sample.uv
            x0, y0, x1, y1 = self.args
            hit = x0 <= u <= x1 and y0 <= v <= y1
        elif self.kind == "sky":
            hit = bool(sample.aux & RESOLVED_SKY_FLAG)
        elif self.kind == "geometry":
            hit = bool(sample.aux & RESOLVED_GEOMETRY_FLAG)
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


def parse_mask(text: str) -> Mask:
    terms = []
    for raw in text.split("&"):
        term = raw.strip()
        negate = term.startswith("!")
        term = term[1:].strip() if negate else term
        name, _, rest = term.partition(":")
        if name in ("everything", "sky", "geometry"):
            if rest:
                raise ValueError(f"{name} takes no argument")
            terms.append(Term(name, negate))
        elif name in ("depth", "luma"):
            terms.append(Term(name, negate, _range(rest, term)))
        elif name == "rect":
            parts = rest.split(",")
            if len(parts) != 4:
                raise ValueError(f"{term}: expected rect:<x0>,<y0>,<x1>,<y1>")
            try:
                x0, y0, x1, y1 = (float(part) for part in parts)
            except ValueError as error:
                raise ValueError(f"{term}: {error}") from None
            if x0 > x1 or y0 > y1:
                raise ValueError(f"{term}: the rectangle is empty")
            terms.append(Term(name, negate, (x0, y0, x1, y1)))
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
    if text in ("live", "vanilla", "identity", "invert", "red", "cost"):
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


def evaluate(expectation: Expectation, snapshots: list[Snapshot], options: Options) -> Report:
    if not snapshots:
        return Report(["snapshots=0"]).inconclusive("telemetry_not_written")
    if expectation.kind == "cost":
        return check_cost(snapshots, options)
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
        help="live | vanilla | identity | invert | red | masked-invert:<mask> | tint:<bit> | cost",
    )
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
        )
        if expectation.kind not in ("live", "vanilla", "cost"):
            single_region(options)
    except ValueError as error:
        parser.error(str(error))
    count = max(args.snapshots, 2)
    path = resolve_telemetry(args.telemetry, args.artifact_dir)
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
    return document


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

    # Parsing refuses what it cannot mean.
    for bad in (
        "masked-invert:",
        "masked-invert:depth:1..0",
        "masked-invert:depth:0.5",
        "masked-invert:rect:0,0,1",
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
