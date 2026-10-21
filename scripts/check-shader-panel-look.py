#!/usr/bin/env python3
"""Check the Shaders panel in a capture of the game against its design tokens, by numbers.

# What is read

Two things, and nothing is looked at by a person:

* The telemetry file er-cel-shade writes once a second (`src/exports.rs`). Its `panel` object is
  the panel's last frame (`er-overlay-theme`'s `FrameRecord`, with the panel's own part names,
  labels and reference column in `src/menu/panel_record.rs`): whether it is open or collapsed,
  its dock, tab, scale and display size, the fonts the overlay host built for it, and where each
  named part landed as `[x, y, width, height]` in Dear ImGui's display pixels.
* One capture of the game window by `scripts/capture-er-window.py`. It shrinks the window to fit
  854x480 and writes whatever its output path names: a quality-35 JPEG for `.jpg`, the same shrink
  lossless for `.ppm` -- the one to use here, see below -- or, where ImageMagick is missing, a
  full-size PNG. Its `<name>.txt` note carries the window's geometry.

The capture is the window's client area, the same surface Dear ImGui draws on, so a display pixel
maps to the image by the ratio of their sizes, per axis. The image must have the display's aspect
(and so must the window, when the note names it); a crop, a letterbox or a capture of something
else does not, and the run is inconclusive rather than judged against the wrong pixels.

# What is checked

For each part, the colour of an inset box placed where the mockups draw fill, held to its token.
On every tab:

* `header` -- `#1A1C21`, between the title and the Dock label (or the code view's note).
* `nav_active` -- `#3A3020` and `nav_inactive` -- `#1A1C21`: the section tabs, below their rounded
  tops, label included.
* `panel_body` -- `#111216` at 0.94 over the frame: the nav row right of the last tab.

And the tab's own, required on the tab the telemetry says was shown (`tab`, and `sub` for the
Layers tab's sub-tab), judged elsewhere only when the frame drew them:

* Layers, any sub-tab: `stack_selected_row` -- `#3A3020`, between the layer's name and its end.
* Layers, Parameters: `add_layer_button` -- `#D8A84E`, the whole button inside a small inset.
* Layers, Code: `compile_button` -- `#D8A84E`.
* Look: `look_preset_save` -- `#D8A84E`, Save as preset; `look_slider` -- `#1F222A`, the first
  knob's track, its 12-pixel grab a small share of it.
* Diagnostics: `measure_cost_button` -- `#D8A84E` faded to `UNAVAILABLE_ALPHA` over the body, as
  a control whose phase has not come is drawn.
* Hunt: `hunt_list` -- the body's colour inside the family list's border: the mockup's box has
  no fill of its own.
* `sub_nav_active` (`#3A3020`) is judged when the frame drew it and is never required.
* `reference_panel` -- `#17181D`, the Code sub-tab's reference column below its search field and
  summary line, is required whenever the telemetry's `reference` object says the column was open
  (`reference.open`), on whatever tab, and judged when the frame drew it.

The telemetry's `reference` object is the column's state as that frame drew it -- open, its
search field focused, its stage, its query's length, the entries it kept and all it has -- and is
printed on the `reference` line. A DLL older than the column writes none, which reads as closed.

What the capture's 854x480 shrink cannot hold is not judged: a 1-pixel rule becomes under half a
pixel, and 11- to 13-pixel text a few pixels of smear, so rules, borders, headings, table
alignment and type faces are left to the telemetry's rects and fonts.

The colour is the box's dominant one (`dominant_colour`), not its median: at 854x480 a 28-pixel
tab is 12 image pixels tall and its label covers most of them, so a plain median sits between ink
and fill. Each channel may then differ from the token by 10/255 on an opaque fill and 20/255 on
the translucent body, which the frame shows through at 6 per cent. A tab or button nearer its
hovered or held colour than its resting one is inconclusive, since the pointer hid the resting
colour. Box sizes are pixels at 1080p times the panel's scale; a box left with fewer than
`MIN_SAMPLE_PIXELS` image pixels, or with no colour holding `MIN_DOMINANT_SHARE` of it, is
inconclusive rather than judged on noise.

Use a `.ppm` capture. The selftest renders the mockup's panel with the vendored fonts and puts it
through the capture's own ImageMagick step: lossless, every part comes back within 3 of its token
(the body within 10); through the quality-35 JPEG the + Layer button comes back 10 or 11 under its
gold, because each 8x8 block of it holds label and fill both. So in a JPEG a part with a label in
its box is inconclusive, not failed, up to `LOSSY_LABEL_SLACK` past the tolerance. Dear ImGui's
default look misses every restyled part by 55 to 181 either way.

The fonts: the telemetry must say the panel drew in its own fonts, with a nonzero handle from the
host for every face and every face built, at the scale they were built for. A `font_scale` away
from 1 means the display changed size after the panel asked for its fonts -- they are asked for
once, and the host cannot give a request back -- so every glyph is a scaled bitmap and blurred.

# Timing

The telemetry describes the frame before its write and the capture an instant, so the two are
tied: the first telemetry write made after the capture must land within `--max-lag` of it, and
the panel must be the same in that write and the one beside it -- the write after it for
`--image`, the write before the capture for `--capture` (which makes the capture itself and so
brackets it). The waits block on an inotify watch of the telemetry's directory, as
`check-shader-editor-probe.py` does, and `--timeout` only bounds a DLL that has stopped writing. A
closed or collapsed panel is inconclusive. The telemetry is found the way the probe checker finds
it: `--telemetry`, else `--artifact-dir`, else the launcher's redirect, else the newest copy.

    python3 scripts/check-shader-panel-look.py --capture /tmp/run/panel-look.ppm
    python3 scripts/check-shader-panel-look.py --image /tmp/run/panel-look.ppm
    python3 scripts/check-shader-panel-look.py --selftest

# Output and exit code

Numbers only: the telemetry and image used, one `part=` line per part with its box, dominant
colour, share, token and difference, one `fonts` line, then `verdict=pass`, `verdict=fail` or
`verdict=inconclusive` with a `why=` token. Exit 0 on a pass, 1 on a fail, 2 when the run cannot
decide. Nothing about the image is printed beyond the sampled numbers.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib.util
import io
import json
import math
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

# The inotify watch, shared with the launchers.
import er_run_lib  # noqa: E402

PROBE_CHECKER = SCRIPTS / "check-shader-editor-probe.py"
CAPTURE_SCRIPT = SCRIPTS / "capture-er-window.py"
CRATE = REPO / "crates" / "er-cel-shade"
# The look is the shared theme crate's: its tokens, its sizes and its vendored faces.
THEME_CRATE = REPO / "crates" / "er-overlay-theme"
THEME_RS = THEME_CRATE / "src" / "lib.rs"
PANEL_RECORD_RS = CRATE / "src" / "menu" / "panel_record.rs"
EXPORTS_RS = CRATE / "src" / "exports.rs"
FONTS = THEME_CRATE / "fonts"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2

# The design tokens, as the theme's `lib.rs` writes them; the selftest reads that file and holds
# these to it.
TOKENS = {
    "HEADER_BG": 0x1A1C21,
    "TAB_BG": 0x1A1C21,
    "TAB_HOVER_BG": 0x24272E,
    "TAB_ACTIVE_BG": 0x3A3020,
    "SELECTED_ROW_BG": 0x3A3020,
    "PRIMARY_BG": 0xD8A84E,
    "PRIMARY_HOVER_BG": 0xF0C978,
    "PRIMARY_ACTIVE_BG": 0xC39441,
    "PANEL_BG": 0x111216,
    "INPUT_BG": 0x1F222A,
    "INPUT_HOVER_BG": 0x252932,
    "INPUT_ACTIVE_BG": 0x2A2F3A,
    "COMPLETION_BG": 0x17181D,
}
# The panel background's alpha, `theme::BACKGROUND_ALPHA`.
BACKGROUND_ALPHA = 0.94
# What a control whose phase has not come keeps of its colours, `theme::UNAVAILABLE_ALPHA`.
UNAVAILABLE_ALPHA = 0.5
# How far the panel window's font scale may be from 1 before its glyphs count as scaled.
FONT_SCALE_TOLERANCE = 0.02

# Sizes at 1080p, as the theme writes them (held by the selftest): the header bar, the section
# nav's top padding and tab height, and a tab's top corner radius.
HEADER_HEIGHT = 32.0
NAV_PAD_TOP = 8.0
NAV_TAB_HEIGHT = 28.0
TAB_RADIUS = 4.0

# Where the boxes sit, in 1080p pixels from the part's rectangle, read off Main.dc.html and
# Code.dc.html with room for the host's default font, which is a little wider than Plex.
# The header: past the 12-pixel padding, "Shaders" (49 wide in either font) and the 8-pixel gap;
# short of the right side's Dock label, its combo (85 for "Floating" in Plex, 94 in the default
# font), and the two 24-pixel icon buttons with their gaps (8 + 24 + 8 + 24 + 8 + 94 + 6 + 28 =
# 200).
HEADER_LEFT_CLEAR = 96.0
HEADER_RIGHT_CLEAR = 216.0
# The code view's header has the note "widened to 720 px for code; Layers goes back to 460" where
# the Dock combo was: 72 for the buttons and the note, 283 in Plex and 364 in the default font.
HEADER_CODE_RIGHT_CLEAR = 452.0
# Rows the box keeps off the bar's top and its rule.
BAR_INSET = 5.0
# A tab or a button: off its sides and bottom, and off its top past the rounded corners.
TAB_SIDE_INSET = 3.0
TAB_TOP_INSET = TAB_RADIUS + 1.0
BUTTON_INSET = 3.0
# The nav row right of its last tab: 10 of padding and four tabs, which end at 275 in Plex and 287
# in the default font.
NAV_TABS_CLEAR = 300.0
NAV_RIGHT_CLEAR = 8.0
NAV_ROW_INSET = 3.0
# The selected row: right of the first 40 per cent, where a name of up to about 21 characters has
# ended, and short of the solo button and a timing such as "24 ms" (8 + 35 + 8 + 22 = 73 from the
# right in the default font, 71 in JetBrains Mono).
ROW_NAME_SHARE = 0.40
ROW_RIGHT_CLEAR = 96.0
ROW_INSET = 5.0
# The reference column: below its border and padding (1 + 8), its 24-pixel search field, a 6-pixel
# gap, its summary line (16 in Plex, 18 in the default font) and another gap, so only the list's
# rows are sampled; their names and signatures are a share of the card's fill, as a label is of a
# tab.
REFERENCE_TOP_CLEAR = 1.0 + 8.0 + 24.0 + 6.0 + 18.0 + 6.0
REFERENCE_INSET = 4.0

# Per channel, in 1/255: an opaque fill, and the body the frame shows through.
OPAQUE_TOLERANCE = 10
TRANSLUCENT_TOLERANCE = 20
# Image pixels kept off a box's edges after it is mapped, against the shrink's and the JPEG's
# smear, and the fewest pixels a box may be left with.
EDGE_GUARD = 1
MIN_SAMPLE_PIXELS = 12
# The dominant colour's histogram bins, per channel, and the share of a box its cluster must hold
# before the box counts as filled rather than mixed.
DOMINANT_BIN = 8
# The selftest's rendered panel puts its active tab's share at 0.16 to 0.25 through the JPEG, so
# this is set under that; it stops only a box over something with no fill at all.
MIN_DOMINANT_SHARE = 0.10
# A JPEG capture's allowance on a part whose box holds a label, past which it fails and within
# which it is inconclusive. Quality 35 at 854x480 leaves 8x8 blocks that each hold both label
# and fill, and their quantised detail darkens a light fill between the glyphs: the selftest's
# rendered + Layer button measures 10 or 11 below its gold through the capture's own JPEG and 0
# through the same shrink kept lossless. Within the allowance the capture cannot tell a right
# fill from one a little off; a `.ppm` capture can.
LOSSY_LABEL_SLACK = 8
# How far the image's aspect, and the window's, may be from the display's: integer rounding of a
# shrink is well under this, and a crop is far over it.
ASPECT_TOLERANCE = 0.01
# Display pixels a rectangle may stray past the display's edge before the mapping is in doubt.
DISPLAY_SLACK = 1.0

# Every telemetry wait of a run together: two or three writes a second apart, and the capture.
DEFAULT_TIMEOUT_SECONDS = 8.0
# The cap on `--timeout`, the repo's limit on any non-game wait (scripts/check-no-timeouts.py).
MAX_TIMEOUT_SECONDS = 30.0
# One slice of a wait: an inotify event ends it at once; this only bounds a quiet directory.
WAIT_SLICE_SECONDS = 0.5
# The DLL writes once a second, so the first write after a capture is under a second behind it.
DEFAULT_MAX_LAG_SECONDS = 3.0
# Bounds on the helpers this runs.
CAPTURE_TIMEOUT_SECONDS = 30.0
MAGICK_TIMEOUT_SECONDS = 20.0
# How far a file's time may trail `time.time_ns()` for the same instant: the kernel stamps files
# from its coarse clock, which lags by up to a tick.
COARSE_CLOCK_NS = 20_000_000


class TelemetryError(ValueError):
    """A telemetry document this reader cannot use."""


class PictureError(ValueError):
    """A capture this reader cannot decode."""


def probe_checker():
    """`scripts/check-shader-editor-probe.py` as a module: the telemetry's name and schema, where
    this run's file is, and the inotify wait."""
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


def rgb_of(hex_value: int) -> tuple[int, int, int]:
    return ((hex_value >> 16) & 0xFF, (hex_value >> 8) & 0xFF, hex_value & 0xFF)


def over(colour: tuple[int, ...], under: tuple[int, ...], alpha: float) -> tuple[int, int, int]:
    """`colour` drawn at `alpha` over `under`."""
    red, green, blue = (round(alpha * c + (1 - alpha) * u) for c, u in zip(colour, under))
    return (red, green, blue)


# Colours the panel draws by fading a token rather than as one, from the tokens they fade.
DERIVED: dict[str, Callable[[], tuple[int, int, int]]] = {
    # A primary button drawn faded (`theme::unavailable`) over the body, the body taken at its
    # token as `panel_body` takes it: the frame showing through the body moves this by at most 8.
    "UNAVAILABLE_PRIMARY_BG": lambda: over(
        rgb_of(TOKENS["PRIMARY_BG"]), rgb_of(TOKENS["PANEL_BG"]), UNAVAILABLE_ALPHA
    ),
}


def token_rgb(name: str) -> tuple[int, int, int]:
    """A token's colour, or a derived one's."""
    derived = DERIVED.get(name)
    return derived() if derived is not None else rgb_of(TOKENS[name])


# Telemetry


@dataclass(frozen=True)
class Fonts:
    requested: bool
    request_scale: float | None
    # (face name, host handle, built in full), in `Face::ALL` order.
    faces: tuple[tuple[str, int, bool], ...]
    in_use: bool


@dataclass(frozen=True)
class Reference:
    """The Code sub-tab's reference column as a frame drew it (`theme::ReferenceSnapshot`)."""

    open: bool = False
    searching: bool = False
    # The stage its list is kept to, empty for every stage.
    stage: str = ""
    query_chars: int = 0
    shown: int = 0
    total: int = 0


@dataclass(frozen=True)
class Panel:
    draws: int
    present: int
    open: bool
    collapsed: bool
    dock: str
    tab: str
    # The Layers tab's sub-tab (`parameters`, `code` or `selector`), empty on another tab.
    sub: str
    code_view: bool
    scale: float
    # The panel window's font scale: 1 while its fonts were built for the display's size.
    font_scale: float
    display: tuple[float, float]
    fonts: Fonts
    rects: dict[str, tuple[float, float, float, float]]
    rects_refused: int
    reference: Reference = field(default_factory=Reference)

    @property
    def shown(self) -> str:
        """What the panel showed, as `Part.on` names it: the tab, and the Layers tab's sub-tab
        after a `/`."""
        return f"{self.tab}/{self.sub}" if self.tab == "layers" and self.sub else self.tab

    def geometry(self) -> tuple:
        """Everything that decides where a part is drawn and what it shows: equal in two writes
        means the panel did not move between them."""
        return (
            self.open,
            self.collapsed,
            self.dock,
            self.tab,
            self.sub,
            self.code_view,
            self.scale,
            self.display,
            self.fonts.in_use,
            tuple(sorted(self.rects.items())),
            self.reference.open,
            self.reference.stage,
        )


@dataclass(frozen=True)
class Write:
    """One telemetry file as the DLL left it: when, and its panel (`None` before it ever drew)."""

    mtime_ns: int
    written_ms: int
    panel: Panel | None


def _number(value, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"panel {key} is not a number: {value!r}")
    return float(value)


def _flag(document: dict, key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise TelemetryError(f"panel {key} is not true or false: {value!r}")
    return value


def parse_reference(reference) -> Reference:
    """The panel's `reference` object; a DLL older than the column writes none, which is closed."""
    if reference is None:
        return Reference()
    if not isinstance(reference, dict):
        raise TelemetryError(f"panel reference is not an object: {reference!r}")
    stage = reference.get("stage", "")
    if not isinstance(stage, str):
        raise TelemetryError(f"panel reference stage is not a string: {stage!r}")
    return Reference(
        open=_flag(reference, "open"),
        searching=_flag(reference, "searching"),
        stage=stage,
        query_chars=int(_number(reference.get("query_chars"), "reference query_chars")),
        shown=int(_number(reference.get("shown"), "reference shown")),
        total=int(_number(reference.get("total"), "reference total")),
    )


def parse_panel(document: dict) -> Panel | None:
    """The `panel` object of one telemetry document, or `None` when it is `null`."""
    if not isinstance(document, dict):
        raise TelemetryError("the telemetry is not a JSON object")
    schema = probe_checker().SCHEMA
    if document.get("schema") != schema:
        raise TelemetryError(f"schema {document.get('schema')!r}, this reader knows {schema}")
    if "panel" not in document:
        raise TelemetryError("no panel key: a DLL older than the panel's telemetry")
    panel = document["panel"]
    if panel is None:
        return None
    if not isinstance(panel, dict):
        raise TelemetryError(f"panel is not an object: {panel!r}")
    fonts = panel.get("fonts")
    if not isinstance(fonts, dict) or not isinstance(fonts.get("faces"), list):
        raise TelemetryError(f"panel fonts are not an object with faces: {fonts!r}")
    faces = []
    for face in fonts["faces"]:
        if not isinstance(face, dict) or not isinstance(face.get("name"), str):
            raise TelemetryError(f"a font face without a name: {face!r}")
        handle = face.get("handle")
        if isinstance(handle, bool) or not isinstance(handle, int):
            raise TelemetryError(f"font {face['name']} handle is not an integer: {handle!r}")
        faces.append((face["name"], handle, _flag(face, "granted")))
    request_scale = fonts.get("request_scale")
    display = panel.get("display")
    if not isinstance(display, list) or len(display) != 2:
        raise TelemetryError(f"panel display is not two numbers: {display!r}")
    rects = {}
    raw_rects = panel.get("rects")
    if not isinstance(raw_rects, dict):
        raise TelemetryError(f"panel rects is not an object: {raw_rects!r}")
    for name, rect in raw_rects.items():
        if not isinstance(rect, list) or len(rect) != 4:
            raise TelemetryError(f"rect {name} is not four numbers: {rect!r}")
        x, y, w, h = (_number(value, f"rect {name}") for value in rect)
        rects[name] = (x, y, w, h)
    scale = panel.get("scale")
    font_scale = panel.get("font_scale")
    return Panel(
        reference=parse_reference(panel.get("reference")),
        draws=int(_number(panel.get("draws"), "draws")),
        present=int(_number(panel.get("present"), "present")),
        open=_flag(panel, "open"),
        collapsed=_flag(panel, "collapsed"),
        dock=str(panel.get("dock")),
        tab=str(panel.get("tab")),
        sub=str(panel.get("sub") or ""),
        code_view=_flag(panel, "code_view"),
        # `null` is a NaN or an infinity, which the checks treat as no scale at all.
        scale=_number(scale, "scale") if scale is not None else math.nan,
        font_scale=_number(font_scale, "font_scale") if font_scale is not None else math.nan,
        display=(_number(display[0], "display"), _number(display[1], "display")),
        fonts=Fonts(
            requested=_flag(fonts, "requested"),
            request_scale=_number(request_scale, "request_scale")
            if request_scale is not None
            else None,
            faces=tuple(faces),
            in_use=_flag(fonts, "in_use"),
        ),
        rects=rects,
        rects_refused=int(_number(panel.get("rects_refused", 0), "rects_refused")),
    )


def read_write(path: pathlib.Path) -> Write | None:
    """The file as it stands, or `None` while it is missing or half written."""
    try:
        before = path.stat().st_mtime_ns
        text = path.read_text(encoding="utf-8")
        after = path.stat().st_mtime_ns
    except OSError:
        return None
    if before != after:
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    written_ms = document.get("written_ms") if isinstance(document, dict) else None
    if isinstance(written_ms, bool) or not isinstance(written_ms, int):
        raise TelemetryError(f"written_ms is not an integer: {written_ms!r}")
    return Write(mtime_ns=after, written_ms=written_ms, panel=parse_panel(document))


def await_writes(path: pathlib.Path, after_ns: int, count: int, timeout: float) -> list[Write]:
    """Up to `count` writes of the file, the first made after `after_ns` (a file time in
    nanoseconds) and each after the one before. Fewer come back when the DLL stops writing
    before `timeout`."""
    probe = probe_checker()
    deadline = time.monotonic() + min(max(timeout, 0.0), MAX_TIMEOUT_SECONDS)
    taken: list[Write] = []
    floor = after_ns
    with er_run_lib.DirectoryWatch(path.parent) as watch:
        while len(taken) < count:
            write = read_write(path)
            if write is not None and write.mtime_ns > floor:
                taken.append(write)
                floor = write.mtime_ns
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            probe.block(watch, min(WAIT_SLICE_SECONDS, remaining))
    return taken


# The capture


@dataclass(frozen=True)
class Dominant:
    """A box's dominant colour, how many pixels the box holds, and how many are that colour."""

    colour: tuple[int, int, int]
    pixels: int
    cluster: int

    @property
    def share(self) -> float:
        return self.cluster / self.pixels if self.pixels else 0.0


def dominant_colour(pixels: list[tuple[int, int, int]]) -> Dominant:
    """The colour a box is filled with under whatever is drawn on it: the densest cube three
    `DOMINANT_BIN` bins wide in the box's colour histogram, and the per-channel median of the
    pixels in it.

    A plain median of the box is pulled toward a label once the capture is shrunk and compressed:
    at 854x480 a 13-pixel label spans most of a tab's rows, and the selftest's rendered panel
    measured its active tabs 19 to 33 off their fill that way. A label's pixels spread over every
    shade between ink and fill while the fill stays in one place, so the densest cube is the
    fill's. A wrong fill is just as dense, so it is found and fails.
    """
    if not pixels:
        return Dominant((0, 0, 0), 0, 0)
    counts: dict[tuple[int, int, int], int] = {}
    for red, green, blue in pixels:
        key = (red // DOMINANT_BIN, green // DOMINANT_BIN, blue // DOMINANT_BIN)
        counts[key] = counts.get(key, 0) + 1
    steps = (-1, 0, 1)

    def around(key: tuple[int, int, int]) -> int:
        return sum(
            counts.get((key[0] + dr, key[1] + dg, key[2] + db), 0)
            for dr in steps
            for dg in steps
            for db in steps
        )

    # Ties go to the denser bin itself, then to the lowest, so the answer does not depend on
    # the order the pixels were read in.
    best = max(counts, key=lambda key: (around(key), counts[key], tuple(-k for k in key)))
    cluster = [
        pixel
        for pixel in pixels
        if all(abs(channel // DOMINANT_BIN - centre) <= 1 for channel, centre in zip(pixel, best))
    ]
    colour = (
        statistics.median_low([pixel[0] for pixel in cluster]),
        statistics.median_low([pixel[1] for pixel in cluster]),
        statistics.median_low([pixel[2] for pixel in cluster]),
    )
    return Dominant(colour, len(pixels), len(cluster))


@dataclass
class Picture:
    """RGB bytes, row by row, top to bottom; `lossy` when they were decoded from a JPEG."""

    width: int
    height: int
    rgb: bytearray
    lossy: bool = False

    @classmethod
    def blank(cls, width: int, height: int, colour: tuple[int, int, int]) -> Picture:
        return cls(width, height, bytearray(bytes(colour) * (width * height)))

    def fill(self, box: tuple[int, int, int, int], colour: tuple[int, int, int]) -> None:
        x0, y0, x1, y1 = (
            max(0, box[0]),
            max(0, box[1]),
            min(self.width, box[2]),
            min(self.height, box[3]),
        )
        if x1 <= x0 or y1 <= y0:
            return
        run = bytes(colour) * (x1 - x0)
        for y in range(y0, y1):
            at = (y * self.width + x0) * 3
            self.rgb[at : at + len(run)] = run

    def pixels(self, box: tuple[int, int, int, int]) -> list[tuple[int, int, int]]:
        """The pixels in `[x0, x1) x [y0, y1)`."""
        x0, y0, x1, y1 = box
        out: list[tuple[int, int, int]] = []
        for y in range(y0, y1):
            row = self.rgb[(y * self.width + x0) * 3 : (y * self.width + x1) * 3]
            out.extend(zip(row[0::3], row[1::3], row[2::3]))
        return out

    def dominant(self, box: tuple[int, int, int, int]) -> Dominant:
        """The colour most of the box is filled with (`dominant_colour`)."""
        return dominant_colour(self.pixels(box))

    def ppm(self) -> bytes:
        return b"P6\n%d %d\n255\n" % (self.width, self.height) + bytes(self.rgb)


def parse_ppm(data: bytes) -> Picture:
    """A binary PPM (`P6`, 8 bits a channel), comments allowed in the header."""
    tokens: list[bytes] = []
    at = 0
    while len(tokens) < 4:
        while at < len(data) and data[at : at + 1].isspace():
            at += 1
        if data[at : at + 1] == b"#":
            while at < len(data) and data[at : at + 1] not in (b"\n", b"\r"):
                at += 1
            continue
        start = at
        while at < len(data) and not data[at : at + 1].isspace():
            at += 1
        if start == at:
            raise PictureError("a PPM header ends early")
        tokens.append(data[start:at])
    if tokens[0] != b"P6" or tokens[3] != b"255" or not (tokens[1] + tokens[2]).isdigit():
        raise PictureError(f"not an 8-bit binary PPM: {b' '.join(tokens)!r}")
    width, height = int(tokens[1]), int(tokens[2])
    at += 1  # the one whitespace byte after the maximum value
    pixels = data[at : at + width * height * 3]
    if len(pixels) != width * height * 3:
        raise PictureError(f"a PPM of {width}x{height} carries {len(pixels)} pixel bytes")
    return Picture(width, height, bytearray(pixels))


# A JPEG file's first three bytes.
JPEG_MAGIC = b"\xff\xd8\xff"


def load_picture(path: pathlib.Path) -> Picture:
    """The capture as RGB: a PPM directly, anything else through Pillow or else ImageMagick."""
    data = path.read_bytes()
    if data[:2] == b"P6":
        return parse_ppm(data)
    picture = _decode(path)
    picture.lossy = data[:3] == JPEG_MAGIC
    return picture


def _decode(path: pathlib.Path) -> Picture:
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                return Picture(rgb.width, rgb.height, bytearray(rgb.tobytes()))
        except OSError as error:
            raise PictureError(f"Pillow cannot read {path.name}: {error}") from None
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        raise PictureError(
            "no reader: Pillow is not importable and ImageMagick is not on PATH "
            "(`uv run --with pillow python3 scripts/check-shader-panel-look.py ...` provides one)"
        )
    try:
        result = subprocess.run(
            [magick, str(path), "-depth", "8", "ppm:-"],
            capture_output=True,
            timeout=MAGICK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise PictureError(f"reading {path.name} timed out") from None
    if result.returncode != 0:
        raise PictureError(f"reading {path.name}: magick exited {result.returncode}")
    return parse_ppm(result.stdout)


@dataclass(frozen=True)
class Note:
    """What `capture-er-window.py` wrote beside its capture."""

    captured: bool
    # The window's size, from `geom=X,Y WxH`; `None` when the note does not say.
    window: tuple[int, int] | None
    # The file the capture went to, and the note's first words when it did not capture.
    target: str | None
    summary: str


def read_note(image: pathlib.Path) -> Note | None:
    """The capture's note, when there is one written for this capture or a later one."""
    note = image.with_suffix(".txt")
    try:
        raw = note.read_bytes()
        note_ns = note.stat().st_mtime_ns
    except OSError:
        return None
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        # The capture script writes ASCII; anything else is not its note.
        text = "unreadable capture note"
    try:
        image_ns = image.stat().st_mtime_ns
    except OSError:
        image_ns = None
    # The note is written after the image; one older than the image is an earlier capture's.
    if image_ns is not None and note_ns < image_ns:
        return None
    if not text.startswith("captured"):
        return Note(False, None, None, " ".join(text.split()[:3]))
    geometry = re.search(r"geom=-?\d+,-?\d+ (\d+)x(\d+)", text)
    target = re.search(r"-> (\S+)", text)
    return Note(
        True,
        (int(geometry.group(1)), int(geometry.group(2))) if geometry else None,
        target.group(1) if target else None,
        "captured",
    )


# Parts


Rect = tuple[float, float, float, float]
Box = tuple[float, float, float, float]


def header_box(rect: Rect, panel: Panel) -> Box:
    x, y, w, _ = rect
    s = panel.scale
    right = HEADER_CODE_RIGHT_CLEAR if panel.code_view else HEADER_RIGHT_CLEAR
    return (
        x + HEADER_LEFT_CLEAR * s,
        y + BAR_INSET * s,
        x + w - right * s,
        y + (HEADER_HEIGHT - BAR_INSET) * s,
    )


def tab_box(rect: Rect, panel: Panel) -> Box:
    x, y, w, h = rect
    s = panel.scale
    return (
        x + TAB_SIDE_INSET * s,
        y + TAB_TOP_INSET * s,
        x + w - TAB_SIDE_INSET * s,
        y + h - TAB_SIDE_INSET * s,
    )


def body_box(rect: Rect, panel: Panel) -> Box:
    x, y, w, _ = rect
    s = panel.scale
    bottom = NAV_PAD_TOP + NAV_TAB_HEIGHT - NAV_ROW_INSET
    return (
        x + NAV_TABS_CLEAR * s,
        y + NAV_ROW_INSET * s,
        x + w - NAV_RIGHT_CLEAR * s,
        y + bottom * s,
    )


def row_box(rect: Rect, panel: Panel) -> Box:
    x, y, w, h = rect
    s = panel.scale
    return (
        x + w * ROW_NAME_SHARE,
        y + ROW_INSET * s,
        x + w - ROW_RIGHT_CLEAR * s,
        y + h - ROW_INSET * s,
    )


def button_box(rect: Rect, panel: Panel) -> Box:
    x, y, w, h = rect
    s = panel.scale
    return (
        x + BUTTON_INSET * s,
        y + BUTTON_INSET * s,
        x + w - BUTTON_INSET * s,
        y + h - BUTTON_INSET * s,
    )


def reference_box(rect: Rect, panel: Panel) -> Box:
    """The reference column's list: below its search field and summary, inside its border."""
    x, y, w, h = rect
    s = panel.scale
    return (
        x + REFERENCE_INSET * s,
        y + REFERENCE_TOP_CLEAR * s,
        x + w - REFERENCE_INSET * s,
        y + h - REFERENCE_INSET * s,
    )


@dataclass(frozen=True)
class Part:
    name: str
    # The `RECT_NAMES` entry the box is placed in.
    rect: str
    # A `TOKENS` or `DERIVED` name.
    token: str
    tolerance: int
    box: Callable[[Rect, Panel], Box]
    # Other states the part can be drawn in, each a (name, token): seeing one means the resting
    # colour was hidden, not wrong.
    states: tuple[tuple[str, str], ...] = ()
    # Where the part must have been drawn (`Panel.shown`): `*` on every tab. Elsewhere it is
    # judged when the frame drew it and its absence says nothing.
    on: tuple[str, ...] = ("*",)
    # The box holds a label, which a JPEG capture smears into the fill (`LOSSY_LABEL_SLACK`).
    labelled: bool = False
    # Required as well whenever this says the frame drew the part, whatever was shown.
    when: Callable[[Panel], bool] | None = None

    def required(self, panel: Panel) -> bool:
        return (
            "*" in self.on
            or panel.shown in self.on
            or (self.when is not None and self.when(panel))
        )


TAB_STATES = (("hovered", "TAB_HOVER_BG"),)
PRIMARY_STATES = (("hovered", "PRIMARY_HOVER_BG"), ("held", "PRIMARY_ACTIVE_BG"))
INPUT_STATES = (("hovered", "INPUT_HOVER_BG"), ("held", "INPUT_ACTIVE_BG"))
LAYERS_SUBS = ("layers/parameters", "layers/code", "layers/selector")

PARTS = (
    Part("header", "header", "HEADER_BG", OPAQUE_TOLERANCE, header_box),
    Part("nav_active", "nav_active", "TAB_ACTIVE_BG", OPAQUE_TOLERANCE, tab_box, labelled=True),
    Part(
        "nav_inactive",
        "nav_inactive",
        "TAB_BG",
        OPAQUE_TOLERANCE,
        tab_box,
        TAB_STATES,
        labelled=True,
    ),
    Part("panel_body", "nav", "PANEL_BG", TRANSLUCENT_TOLERANCE, body_box),
    Part(
        "stack_selected_row",
        "stack_selected_row",
        "SELECTED_ROW_BG",
        OPAQUE_TOLERANCE,
        row_box,
        on=LAYERS_SUBS,
    ),
    Part(
        "add_layer_button",
        "add_layer_button",
        "PRIMARY_BG",
        OPAQUE_TOLERANCE,
        button_box,
        PRIMARY_STATES,
        on=("layers/parameters",),
        labelled=True,
    ),
    Part(
        "sub_nav_active",
        "sub_nav_active",
        "TAB_ACTIVE_BG",
        OPAQUE_TOLERANCE,
        tab_box,
        on=(),
        labelled=True,
    ),
    Part(
        "compile_button",
        "compile_button",
        "PRIMARY_BG",
        OPAQUE_TOLERANCE,
        button_box,
        PRIMARY_STATES,
        on=("layers/code",),
        labelled=True,
    ),
    Part(
        "look_preset_save",
        "look_preset_save",
        "PRIMARY_BG",
        OPAQUE_TOLERANCE,
        button_box,
        PRIMARY_STATES,
        on=("look",),
        labelled=True,
    ),
    Part(
        "look_slider",
        "look_slider",
        "INPUT_BG",
        OPAQUE_TOLERANCE,
        button_box,
        INPUT_STATES,
        on=("look",),
    ),
    Part(
        "measure_cost_button",
        "measure_cost_button",
        "UNAVAILABLE_PRIMARY_BG",
        TRANSLUCENT_TOLERANCE,
        button_box,
        on=("diagnostics",),
        labelled=True,
    ),
    Part(
        "hunt_list",
        "hunt_list",
        "PANEL_BG",
        TRANSLUCENT_TOLERANCE,
        button_box,
        on=("hunt",),
        labelled=True,
    ),
    Part(
        "reference_panel",
        "reference_panel",
        "COMPLETION_BG",
        OPAQUE_TOLERANCE,
        reference_box,
        on=(),
        labelled=True,
        when=lambda panel: panel.reference.open,
    ),
)
PART_NAMES = tuple(part.name for part in PARTS)


def parse_parts(text: str | None) -> list[Part]:
    if not text:
        return list(PARTS)
    names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in names if name not in PART_NAMES]
    if unknown:
        raise ValueError(f"unknown part {unknown[0]!r}; known: {', '.join(PART_NAMES)}")
    # Named parts are required: asking for one is asking for it to be there.
    return [dataclasses.replace(part, on=("*",)) for part in PARTS if part.name in names]


# Evaluation


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    verdict: str = "pass"
    why: str = ""

    def fail(self, why: str) -> None:
        if self.verdict != "fail":
            self.verdict, self.why = "fail", why

    def inconclusive(self, why: str) -> None:
        if self.verdict == "pass":
            self.verdict, self.why = "inconclusive", why

    @property
    def exit_code(self) -> int:
        return {"pass": EXIT_PASS, "fail": EXIT_FAIL}.get(self.verdict, EXIT_INCONCLUSIVE)


def _fmt(values) -> str:
    return ",".join(f"{value:g}" if isinstance(value, float) else str(value) for value in values)


def image_box(box: Box, sx: float, sy: float) -> tuple[int, int, int, int]:
    """`box` in display pixels as whole image pixels inside it, less the edge guard."""
    x0, y0, x1, y1 = box
    return (
        math.ceil(x0 * sx) + EDGE_GUARD,
        math.ceil(y0 * sy) + EDGE_GUARD,
        math.floor(x1 * sx) - EDGE_GUARD,
        math.floor(y1 * sy) - EDGE_GUARD,
    )


def check_part(
    part: Part, panel: Panel, picture: Picture, sx: float, sy: float, report: Report
) -> None:
    rect = panel.rects.get(part.rect)
    if rect is None:
        if part.required(panel):
            report.lines.append(f"part={part.name} rect=missing result=inconclusive")
            report.inconclusive(f"rect_missing_{part.rect}")
        return
    dw, dh = panel.display
    x, y, w, h = rect
    if (
        w <= 0
        or h <= 0
        or x < -DISPLAY_SLACK
        or y < -DISPLAY_SLACK
        or x + w > dw + DISPLAY_SLACK
        or y + h > dh + DISPLAY_SLACK
    ):
        report.lines.append(f"part={part.name} rect={_fmt(rect)} result=inconclusive")
        report.inconclusive(f"rect_off_display_{part.rect}")
        return
    box = image_box(part.box(rect, panel), sx, sy)
    x0, y0, x1, y1 = box
    if x1 - x0 < 1 or y1 - y0 < 1 or (x1 - x0) * (y1 - y0) < MIN_SAMPLE_PIXELS:
        report.lines.append(
            f"part={part.name} rect={_fmt(rect)} box={_fmt(box)} result=inconclusive"
        )
        report.inconclusive(f"sample_too_small_{part.name}")
        return
    if x0 < 0 or y0 < 0 or x1 > picture.width or y1 > picture.height:
        report.lines.append(
            f"part={part.name} rect={_fmt(rect)} box={_fmt(box)} result=inconclusive"
        )
        report.inconclusive(f"box_outside_image_{part.name}")
        return
    found = picture.dominant(box)
    token = token_rgb(part.token)
    delta = max(abs(a - b) for a, b in zip(found.colour, token))
    # The resting colour and the states a part can be drawn in are close -- a hovered tab is 10
    # to 13 from a resting one -- so the nearest of them names what was seen.
    nearest_delta, nearest = delta, "pass"
    for state, state_token in part.states:
        off = max(abs(a - b) for a, b in zip(found.colour, token_rgb(state_token)))
        if off < nearest_delta:
            nearest_delta, nearest = off, state
    if found.share < MIN_DOMINANT_SHARE:
        result = "mixed"
    elif nearest_delta <= part.tolerance:
        result = nearest
    elif picture.lossy and part.labelled and delta <= part.tolerance + LOSSY_LABEL_SLACK:
        result = "lossy"
    else:
        result = "fail"
    report.lines.append(
        f"part={part.name} rect={_fmt(rect)} box={_fmt(box)} pixels={found.pixels} "
        f"dominant={_fmt(found.colour)} share={found.share:.2f} token={_fmt(token)} "
        f"delta={delta} tolerance={part.tolerance} result={result}"
    )
    if result == "fail":
        report.fail(f"colour_{part.name}")
    elif result != "pass":
        report.inconclusive(f"{part.name}_{result}")


def check_fonts(panel: Panel, report: Report) -> None:
    fonts = panel.fonts
    faces = " ".join(
        f"{name}:{handle}:{str(granted).lower()}" for name, handle, granted in fonts.faces
    )
    report.lines.append(
        f"fonts requested={str(fonts.requested).lower()} in_use={str(fonts.in_use).lower()} "
        f"font_scale={panel.font_scale:g} faces={faces or '-'}"
    )
    if not fonts.requested:
        report.fail("fonts_not_requested")
    elif not fonts.faces:
        report.fail("fonts_no_faces")
    elif not fonts.in_use:
        report.fail("fonts_not_in_use")
    elif not abs(panel.font_scale - 1.0) <= FONT_SCALE_TOLERANCE:
        # Written so that a NaN fails too.
        report.fail("fonts_scaled")
    for name, handle, granted in fonts.faces:
        if handle == 0:
            report.fail(f"font_refused_{name}")
        elif not granted:
            report.fail(f"font_not_built_{name}")


def evaluate(
    panel: Panel | None, picture: Picture, parts: list[Part], window: tuple[int, int] | None = None
) -> Report:
    """Judge one capture against the panel the telemetry says was drawn in it."""
    report = Report()
    if panel is None:
        report.inconclusive("panel_never_drawn")
        return report
    report.lines.append(
        f"panel open={str(panel.open).lower()} collapsed={str(panel.collapsed).lower()} "
        f"dock={panel.dock} shown={panel.shown} code_view={str(panel.code_view).lower()} "
        f"scale={panel.scale:g} display={_fmt(panel.display)} draws={panel.draws} "
        f"rects_refused={panel.rects_refused}"
    )
    reference = panel.reference
    report.lines.append(
        f"reference open={str(reference.open).lower()} "
        f"searching={str(reference.searching).lower()} stage={reference.stage or '-'} "
        f"query_chars={reference.query_chars} shown={reference.shown} total={reference.total}"
    )
    if not panel.open:
        report.inconclusive("panel_closed")
        return report
    if panel.collapsed:
        report.inconclusive("panel_collapsed")
        return report
    if not (math.isfinite(panel.scale) and panel.scale > 0):
        report.inconclusive("panel_scale_unknown")
        return report
    dw, dh = panel.display
    if not (dw > 0 and dh > 0):
        report.inconclusive("display_unknown")
        return report
    sx, sy = picture.width / dw, picture.height / dh
    report.lines.append(
        f"image={picture.width}x{picture.height} lossy={str(picture.lossy).lower()} "
        f"display={dw:g}x{dh:g} map={sx:.5f},{sy:.5f}"
        + (f" window={window[0]}x{window[1]}" if window else "")
    )
    if abs(sx - sy) > ASPECT_TOLERANCE * max(sx, sy):
        report.inconclusive("image_aspect_not_display")
        return report
    if window is not None:
        aspect = dw / dh
        if window[1] <= 0 or abs(window[0] / window[1] - aspect) > ASPECT_TOLERANCE * aspect:
            report.inconclusive("window_aspect_not_display")
            return report
    for part in parts:
        check_part(part, panel, picture, sx, sy, report)
    check_fonts(panel, report)
    return report


# The run


def capture(out: pathlib.Path) -> tuple[pathlib.Path | None, Note | None]:
    """A capture by `capture-er-window.py` into `out`, and the file it actually wrote: `None`
    unless that file and its note were both written by this capture, so a file an earlier capture
    left at the same path is never taken for this one."""
    out.parent.mkdir(parents=True, exist_ok=True)
    started_ns = time.time_ns()
    try:
        subprocess.run(
            [sys.executable, str(CAPTURE_SCRIPT), str(out)],
            capture_output=True,
            text=True,
            timeout=CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, None
    note = read_note(out)
    if note is None or not note.captured:
        return None, note
    image = out.with_name(note.target) if note.target else out
    try:
        fresh = min(image.stat().st_mtime_ns, out.with_suffix(".txt").stat().st_mtime_ns)
    except OSError:
        return None, note
    # File times are the kernel's coarse clock, a tick behind `time_ns`.
    return (image if fresh >= started_ns - COARSE_CLOCK_NS else None), note


def print_report(report: Report) -> None:
    for line in report.lines:
        print(line, flush=True)
    why = f" why={report.why}" if report.why else ""
    print(f"verdict={report.verdict}{why}", flush=True)


def stop(why: str) -> int:
    print(f"verdict=inconclusive why={why}", flush=True)
    return EXIT_INCONCLUSIVE


def run(args: argparse.Namespace, parts: list[Part]) -> int:
    probe = probe_checker()
    path = probe.resolve_telemetry(args.telemetry, args.artifact_dir)
    print(f"telemetry={path}", flush=True)
    deadline = time.monotonic() + min(max(args.timeout, 0.0), MAX_TIMEOUT_SECONDS)

    def left() -> float:
        return max(0.0, deadline - time.monotonic())

    beside: Write | None = None
    try:
        if args.capture is not None:
            # The write before the capture: the first one made after this started.
            earlier = await_writes(path, time.time_ns(), 1, left())
            if not earlier:
                return stop("telemetry_not_refreshing")
            beside = earlier[0]
            image, note = capture(args.capture)
            if image is None:
                print(f"capture={args.capture} note={note.summary if note else '-'}", flush=True)
                return stop("capture_failed")
        else:
            image = args.image
            note = read_note(image)
            if note is not None and not note.captured:
                print(f"capture={image} note={note.summary}", flush=True)
                return stop("capture_failed")
            if not image.exists() and note is not None and note.target:
                image = image.with_name(note.target)
        try:
            image_ns = image.stat().st_mtime_ns
        except OSError:
            return stop("capture_missing")
        print(f"capture={image}", flush=True)
        after = await_writes(path, image_ns, 1, left())
        if not after:
            return stop("no_telemetry_after_capture")
        lag = (after[0].mtime_ns - image_ns) / 1e9
        print(f"telemetry_lag_s={lag:.3f} max_lag_s={args.max_lag:g}", flush=True)
        if lag > args.max_lag:
            return stop("telemetry_after_capture_too_late")
        if beside is None:
            later = await_writes(path, after[0].mtime_ns, 1, left())
            if not later:
                return stop("telemetry_not_refreshing")
            beside = later[0]
    except TelemetryError as error:
        print(f"telemetry_error={' '.join(str(error).split())}", flush=True)
        return stop("telemetry_unreadable")
    report = judge(image, note, after[0], beside, parts)
    print_report(report)
    return report.exit_code


def judge(
    image: pathlib.Path, note: Note | None, after: Write, beside: Write, parts: list[Part]
) -> Report:
    """The capture judged against the first write after it, once the write beside it shows the
    panel did not change around the capture."""
    panel, other = after.panel, beside.panel
    if (panel is None) != (other is None) or (
        panel is not None and other is not None and panel.geometry() != other.geometry()
    ):
        report = Report()
        report.inconclusive("panel_changed_around_capture")
        return report
    try:
        picture = load_picture(image)
    except (OSError, PictureError) as error:
        report = Report([f"image_error={' '.join(str(error).split())}"])
        report.inconclusive("capture_unreadable")
        return report
    return evaluate(panel, picture, parts, note.window if note and note.captured else None)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--image", type=pathlib.Path, help="a capture capture-er-window.py made")
    source.add_argument("--capture", type=pathlib.Path, help="make the capture now, into this path")
    parser.add_argument("--telemetry", type=pathlib.Path, help="the telemetry file itself")
    parser.add_argument("--artifact-dir", type=pathlib.Path, help="the run's artifact directory")
    parser.add_argument("--parts", help=f"comma list of {', '.join(PART_NAMES)}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-lag", type=float, default=DEFAULT_MAX_LAG_SECONDS)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.image is None and args.capture is None:
        parser.error("--image or --capture is required")
    try:
        parts = parse_parts(args.parts)
    except ValueError as error:
        parser.error(str(error))
    return run(args, parts)


# Selftest


DISPLAY = (1920.0, 1080.0)
PANEL_LEFT = 1460.0
PANEL_WIDTH = 460.0


def fonts_ok(handles=(5, 6, 7, 8), granted=(True, True, True, True), in_use=True) -> dict:
    names = (
        "plex_sans_regular",
        "plex_sans_medium",
        "plex_sans_semibold",
        "jetbrains_mono_regular",
    )
    return {
        "requested": True,
        "request_scale": 1,
        "faces": [
            {"name": name, "handle": handle, "granted": built}
            for name, handle, built in zip(names, handles, granted)
        ],
        "in_use": in_use,
    }


def synthetic_doc(rects: dict, written_ms: int = 1000, **panel) -> dict:
    body = {
        "draws": 120,
        "present": 4000,
        "open": True,
        "collapsed": False,
        "dock": "right",
        "tab": "layers",
        "sub": "parameters",
        "code_view": False,
        "scale": 1,
        "font_scale": 1,
        "display": list(DISPLAY),
        "fonts": fonts_ok(),
        "reference": {
            "open": False,
            "searching": False,
            "stage": "",
            "query_chars": 0,
            "shown": 0,
            "total": 0,
        },
        "rects": {name: list(rect) for name, rect in rects.items()},
        "rects_refused": 0,
    }
    body.update(panel)
    return {"schema": probe_checker().SCHEMA, "written_ms": written_ms, "panel": body}


def mockup_rects() -> dict[str, Rect]:
    """Where Main.dc.html puts each part on a 1920x1080 display, right-docked at 460."""
    left = PANEL_LEFT + 1
    return {
        "panel": (PANEL_LEFT, 0.0, PANEL_WIDTH, 1080.0),
        "header": (left, 0.0, PANEL_WIDTH - 1, 33.0),
        "toolbar": (left, 33.0, PANEL_WIDTH - 1, 94.0),
        "nav": (left, 127.0, PANEL_WIDTH - 1, 37.0),
        "nav_active": (left + 10, 135.0, 64.0, 28.0),
        "nav_inactive": (left + 76, 135.0, 54.0, 28.0),
        "stack_selected_row": (left + 10, 250.0, 439.0, 28.0),
        "add_layer_button": (left + 10, 318.0, 66.0, 26.0),
    }


def flat_picture(
    rects: dict[str, Rect],
    size: tuple[int, int],
    colours: dict[str, tuple[int, int, int]] | None = None,
    display: tuple[float, float] = DISPLAY,
    frame: tuple[int, int, int] | None = None,
) -> Picture:
    """The panel drawn as bare fills at `size`, with no text, over a frame of bright bands -- so
    the 6 per cent showing through the body is not zero -- or of the one colour `frame`."""
    width, height = size
    sx, sy = width / display[0], height / display[1]
    picture = Picture.blank(width, height, frame or (0, 0, 0))
    if frame is None:
        band = max(1, height // 9)
        palette = ((200, 170, 120), (90, 140, 200), (60, 160, 80), (230, 230, 220), (150, 60, 50))
        for index in range(0, height, band):
            picture.fill((0, index, width, index + band), palette[(index // band) % len(palette)])

    def mapped(rect: Rect) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        return (
            math.floor(x * sx),
            math.floor(y * sy),
            math.ceil((x + w) * sx),
            math.ceil((y + h) * sy),
        )

    # The Hunt tab's list has no fill: the body shows inside it.
    fills = {
        "header": rgb_of(TOKENS["HEADER_BG"]),
        "nav_active": rgb_of(TOKENS["TAB_ACTIVE_BG"]),
        "nav_inactive": rgb_of(TOKENS["TAB_BG"]),
        "stack_selected_row": rgb_of(TOKENS["SELECTED_ROW_BG"]),
        "add_layer_button": rgb_of(TOKENS["PRIMARY_BG"]),
        "sub_nav_active": rgb_of(TOKENS["TAB_ACTIVE_BG"]),
        "compile_button": rgb_of(TOKENS["PRIMARY_BG"]),
        "look_preset_save": rgb_of(TOKENS["PRIMARY_BG"]),
        "look_slider": rgb_of(TOKENS["INPUT_BG"]),
        "measure_cost_button": token_rgb("UNAVAILABLE_PRIMARY_BG"),
        "reference_panel": rgb_of(TOKENS["COMPLETION_BG"]),
    }
    fills.update(colours or {})
    panel = rects.get("panel")
    if panel is not None:
        x0, y0, x1, y1 = mapped(panel)
        bg = rgb_of(TOKENS["PANEL_BG"])
        for y in range(max(0, y0), min(height, y1)):
            behind = picture.rgb[(y * width) * 3 : (y * width) * 3 + 3]
            blended = tuple(
                round(BACKGROUND_ALPHA * c + (1 - BACKGROUND_ALPHA) * b) for c, b in zip(bg, behind)
            )
            picture.fill((x0, y, x1, y + 1), blended)
    for name, colour in fills.items():
        if name in rects:
            picture.fill(mapped(rects[name]), colour)
    return picture


def rust_colour(theme_rs: str, name: str) -> int | None:
    match = re.search(rf"pub const {name}: \[f32; 4\] = rgba?\(0x([0-9A-Fa-f]{{6}})", theme_rs)
    return int(match.group(1), 16) if match else None


def rust_float(text: str, name: str) -> float | None:
    match = re.search(rf"const {name}: f32 = ([0-9.]+);", text)
    return float(match.group(1)) if match else None


def rust_pair(text: str, name: str) -> tuple[float, float] | None:
    match = re.search(rf"const {name}: \[f32; 2\] = \[([0-9.]+), ([0-9.]+)\];", text)
    return (float(match.group(1)), float(match.group(2))) if match else None


def selftest() -> int:
    results: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        results.append((label, bool(ok)))

    def verdict(panel_doc: dict, picture: Picture, parts=None, window=None) -> Report:
        return evaluate(parse_panel(panel_doc), picture, parts or list(PARTS), window)

    # The copies are the Rust's, read from the sources rather than trusted.
    theme_rs = THEME_RS.read_text(encoding="utf-8")
    panel_record_rs = PANEL_RECORD_RS.read_text(encoding="utf-8")
    exports_rs = EXPORTS_RS.read_text(encoding="utf-8")
    for name, value in TOKENS.items():
        check(f"{name} is the theme's #{value:06X}", rust_colour(theme_rs, name) == value)
    check(
        "BACKGROUND_ALPHA is the theme's",
        rust_float(theme_rs, "BACKGROUND_ALPHA") == BACKGROUND_ALPHA,
    )
    check(
        "UNAVAILABLE_ALPHA is the theme's",
        rust_float(theme_rs, "UNAVAILABLE_ALPHA") == UNAVAILABLE_ALPHA,
    )
    check(
        "every part's colour is a token or one derived from them",
        all(
            name in TOKENS or name in DERIVED
            for part in PARTS
            for name in [part.token, *(state for _, state in part.states)]
        ),
    )
    check("HEADER_HEIGHT is the theme's", rust_float(theme_rs, "HEADER_HEIGHT") == HEADER_HEIGHT)
    check(
        "NAV_TAB_HEIGHT is the theme's", rust_float(theme_rs, "NAV_TAB_HEIGHT") == NAV_TAB_HEIGHT
    )
    check("TAB_RADIUS is the theme's", rust_float(theme_rs, "TAB_RADIUS") == TAB_RADIUS)
    nav_pad = rust_pair(theme_rs, "NAV_PAD")
    check("the nav's top padding is the theme's", nav_pad is not None and nav_pad[1] == NAV_PAD_TOP)
    names = re.search(r"pub const RECT_NAMES: \[&str; \d+\] = \[([^\]]*)\]", panel_record_rs)
    rect_names = set(re.findall(r'"(\w+)"', names.group(1))) if names else set()
    check(
        "every part is placed in a rect panel_record.rs declares",
        all(part.rect in rect_names for part in PARTS),
    )
    render_panel = exports_rs[exports_rs.find("fn render_panel") :]
    keys = (
        "draws",
        "present",
        "open",
        "collapsed",
        "dock",
        "tab",
        "sub",
        "code_view",
        "scale",
        "font_scale",
        "display",
        "fonts",
        "requested",
        "request_scale",
        "faces",
        "name",
        "handle",
        "granted",
        "in_use",
        "reference",
        "searching",
        "stage",
        "query_chars",
        "shown",
        "total",
        "rects",
        "rects_refused",
    )
    check(
        "every panel key read is one exports.rs writes",
        all(f'\\"{key}\\"' in render_panel for key in keys),
    )
    check(
        "the panel is a top-level key of the telemetry",
        '"  \\"panel\\": "' in exports_rs,
    )

    rects = mockup_rects()
    size = (640, 360)
    good = flat_picture(rects, size)
    doc = synthetic_doc(rects)

    # The colours.
    report = verdict(doc, good)
    check("bare fills at their tokens pass", report.verdict == "pass")
    shown = parse_panel(doc)
    check(
        "every required part was judged",
        shown is not None
        and all(
            any(
                line.startswith(f"part={p.name} ") and "result=pass" in line
                for line in report.lines
            )
            for p in PARTS
            if p.required(shown)
        ),
    )
    wrong = flat_picture(rects, size, {"header": (0x29, 0x4A, 0x7A)})
    report = verdict(doc, wrong)
    check(
        "a header in Dear ImGui's title blue fails",
        report.verdict == "fail" and report.why == "colour_header",
    )
    edge = flat_picture(rects, size, {"stack_selected_row": (0x3A + 10, 0x30 - 10, 0x20 + 10)})
    check("a fill 10/255 off a token passes", verdict(doc, edge).verdict == "pass")
    edge = flat_picture(rects, size, {"stack_selected_row": (0x3A + 11, 0x30, 0x20)})
    check("a fill 11/255 off a token fails", verdict(doc, edge).verdict == "fail")
    # Over black the body is the token at 0.94 alone, 1 below it; over white, 14 above it.
    for frame in ((0, 0, 0), (255, 255, 255)):
        check(
            f"the body passes over a frame of {_fmt(frame)}",
            verdict(doc, flat_picture(rects, size, frame=frame)).verdict == "pass",
        )
    no_body = flat_picture({**rects, "panel": (0.0, 0.0, 1.0, 1.0)}, size)
    report = verdict(doc, no_body)
    check(
        "a body that is the frame fails",
        report.verdict == "fail" and report.why == "colour_panel_body",
    )
    hovered = flat_picture(rects, size, {"nav_inactive": rgb_of(TOKENS["TAB_HOVER_BG"])})
    report = verdict(doc, hovered)
    check(
        "a hovered tab is inconclusive",
        report.verdict == "inconclusive" and report.why == "nav_inactive_hovered",
    )
    held = flat_picture(rects, size, {"add_layer_button": rgb_of(TOKENS["PRIMARY_ACTIVE_BG"])})
    report = verdict(doc, held)
    check(
        "a held button is inconclusive",
        report.verdict == "inconclusive" and report.why == "add_layer_button_held",
    )
    both = flat_picture(
        rects, size, {"nav_inactive": rgb_of(TOKENS["TAB_HOVER_BG"]), "header": (0, 0, 0)}
    )
    check("a fail outranks an inconclusive", verdict(doc, both).verdict == "fail")
    code = {
        **rects,
        "header": (1200.0, 0.0, 720.0, 33.0),
        "compile_button": (1214.0, 700.0, 104.0, 26.0),
    }
    del code["add_layer_button"]
    code_doc = synthetic_doc(code, code_view=True, sub="code")
    report = verdict(code_doc, flat_picture(code, size))
    check("the code view's header is sampled clear of its note", report.verdict == "pass")
    no_compile = {name: rect for name, rect in code.items() if name != "compile_button"}
    report = verdict(
        synthetic_doc(no_compile, code_view=True, sub="code"), flat_picture(no_compile, size)
    )
    check(
        "the Code sub-tab needs Compile now and not + Layer",
        report.verdict == "inconclusive" and report.why == "rect_missing_compile_button",
    )
    selector = {name: rect for name, rect in rects.items() if name != "add_layer_button"}
    report = verdict(synthetic_doc(selector, sub="selector"), flat_picture(selector, size))
    check("the Selector sub-tab needs neither", report.verdict == "pass")

    # The other tabs: each is judged on its own parts and the bars, and needs no layer.
    bars = {name: rects[name] for name in ("panel", "header", "toolbar", "nav")}
    look = {
        **bars,
        "nav_active": (PANEL_LEFT + 77, 135.0, 50.0, 28.0),
        "nav_inactive": rects["nav_active"],
        "look_slider": (PANEL_LEFT + 120, 230.0, 250.0, 22.0),
        "look_preset_save": (PANEL_LEFT + 330, 420.0, 104.0, 24.0),
    }
    look_doc = synthetic_doc(look, tab="look", sub="")
    report = verdict(look_doc, flat_picture(look, size))
    check(
        "the Look tab passes on its own parts with no layer selected",
        report.verdict == "pass"
        and {"look_slider", "look_preset_save"} <= set(part_results(report)),
    )
    no_slider = {name: rect for name, rect in look.items() if name != "look_slider"}
    report = verdict(synthetic_doc(no_slider, tab="look", sub=""), flat_picture(no_slider, size))
    check(
        "the Look tab needs its slider",
        report.verdict == "inconclusive" and report.why == "rect_missing_look_slider",
    )
    hovered_track = flat_picture(look, size, {"look_slider": rgb_of(TOKENS["INPUT_HOVER_BG"])})
    check(
        "a track under the mouse is inconclusive",
        verdict(look_doc, hovered_track).why == "look_slider_hovered",
    )
    diagnostics = {
        **bars,
        "nav_active": (PANEL_LEFT + 175, 135.0, 98.0, 28.0),
        "nav_inactive": rects["nav_active"],
        "measure_cost_button": (PANEL_LEFT + 15, 330.0, 110.0, 26.0),
    }
    diagnostics_doc = synthetic_doc(diagnostics, tab="diagnostics", sub="")
    check(
        "Measure cost faded over the body passes",
        verdict(diagnostics_doc, flat_picture(diagnostics, size)).verdict == "pass",
    )
    pressable = flat_picture(
        diagnostics, size, {"measure_cost_button": rgb_of(TOKENS["PRIMARY_BG"])}
    )
    report = verdict(diagnostics_doc, pressable)
    check(
        "Measure cost in full gold, as if it could be pressed, fails",
        report.verdict == "fail" and report.why == "colour_measure_cost_button",
    )
    hunt = {
        **bars,
        "nav_active": (PANEL_LEFT + 128, 135.0, 46.0, 28.0),
        "nav_inactive": rects["nav_active"],
        "hunt_list": (PANEL_LEFT + 15, 260.0, 431.0, 64.0),
    }
    hunt_doc = synthetic_doc(hunt, tab="hunt", sub="")
    check(
        "the Hunt list with no fill of its own passes",
        verdict(hunt_doc, flat_picture(hunt, size)).verdict == "pass",
    )
    # The secondary button's `#2A2F3A`.
    filled = flat_picture(hunt, size, {"hunt_list": rgb_of(0x2A2F3A)})
    report = verdict(hunt_doc, filled)
    check(
        "a Hunt list filled as a button fails",
        report.verdict == "fail" and report.why == "colour_hunt_list",
    )

    # The Code sub-tab's reference column: required while the telemetry says it is open.
    open_reference = {
        "open": True,
        "searching": True,
        "stage": "tonemap_post",
        "query_chars": 4,
        "shown": 3,
        "total": 405,
    }
    beside = {**code, "reference_panel": (1610.0, 300.0, 296.0, 420.0)}
    beside_doc = synthetic_doc(beside, code_view=True, sub="code", reference=open_reference)
    report = verdict(beside_doc, flat_picture(beside, size))
    check(
        "an open reference column in its card's colour passes, and is judged",
        report.verdict == "pass" and "reference_panel" in part_results(report),
    )
    check(
        "the reference line carries the column's state",
        any(
            line == "reference open=true searching=true stage=tonemap_post query_chars=4 "
            "shown=3 total=405"
            for line in report.lines
        ),
    )
    # The code area's `#0B0C0F`.
    code_coloured = flat_picture(beside, size, {"reference_panel": (0x0B, 0x0C, 0x0F)})
    report = verdict(beside_doc, code_coloured)
    check(
        "a reference column in the code area's colour fails",
        report.verdict == "fail" and report.why == "colour_reference_panel",
    )
    unregistered = synthetic_doc(code, code_view=True, sub="code", reference=open_reference)
    report = verdict(unregistered, flat_picture(code, size))
    check(
        "an open reference column that drew no rectangle is inconclusive",
        report.verdict == "inconclusive" and report.why == "rect_missing_reference_panel",
    )
    closed_reference = {**open_reference, "open": False, "searching": False}
    closed_doc = synthetic_doc(code, code_view=True, sub="code", reference=closed_reference)
    report = verdict(closed_doc, flat_picture(code, size))
    check("a closed reference column is not required", report.verdict == "pass")
    older = synthetic_doc(code, code_view=True, sub="code")
    del older["panel"]["reference"]
    shown = parse_panel(older)
    check(
        "a DLL older than the column reads as closed",
        shown is not None and shown.reference == Reference(),
    )
    bad = synthetic_doc(code, reference={**open_reference, "open": "yes"})
    try:
        parse_panel(bad)
        refused = False
    except TelemetryError:
        refused = True
    check("a reference object of the wrong shape is refused", refused)

    # The dominant colour under a label: a third of the box is fill, the rest every shade from it
    # to the ink, which is where a plain median would land.
    fill, ink = rgb_of(TOKENS["PRIMARY_BG"]), (0x1A, 0x14, 0x06)
    label = [
        tuple(round(f + (i - f) * step / 59) for f, i in zip(fill, ink))
        for step in range(1, 60)
        for _ in range(2)
    ]
    found = dominant_colour([fill] * 60 + label)
    plain = tuple(statistics.median_low([p[c] for p in [fill] * 60 + label]) for c in range(3))
    check(
        "the fill under a label is found where a plain median is pulled off",
        found.colour == fill and max(abs(a - b) for a, b in zip(plain, fill)) > OPAQUE_TOLERANCE,
    )
    wrong = dominant_colour([(66, 150, 250)] * 60 + label)
    check("a wrong fill under the same label is found as itself", wrong.colour == (66, 150, 250))
    noise = [((i * 37) % 256, (i * 91) % 256, (i * 53) % 256) for i in range(400)]
    check("a box with no fill has a small share", dominant_colour(noise).share < MIN_DOMINANT_SHARE)

    # A JPEG's allowance, on labelled parts only.
    def off_by(part: str, colour: tuple[int, int, int], lossy: bool) -> Report:
        picture = flat_picture(rects, size, {part: colour})
        picture.lossy = lossy
        return verdict(doc, picture)

    # Off in blue, away from the hovered and held golds, which sit along the red and green.
    gold = rgb_of(TOKENS["PRIMARY_BG"])
    bluer = (gold[0], gold[1], gold[2] + 14)
    check(
        "a labelled part 14 off in a JPEG is inconclusive",
        off_by("add_layer_button", bluer, True).why == "add_layer_button_lossy",
    )
    check(
        "the same 14 in a lossless capture fails",
        off_by("add_layer_button", bluer, False).verdict == "fail",
    )
    bluest = (gold[0], gold[1], gold[2] + OPAQUE_TOLERANCE + LOSSY_LABEL_SLACK + 1)
    check(
        "past the allowance a JPEG fails",
        off_by("add_layer_button", bluest, True).verdict == "fail",
    )
    header = tuple(c + 14 for c in rgb_of(TOKENS["HEADER_BG"]))
    check("a part with no label has no allowance", off_by("header", header, True).verdict == "fail")

    # The fonts.
    report = verdict(synthetic_doc(rects, fonts=fonts_ok(in_use=False)), good)
    check("the default font fails", report.verdict == "fail" and report.why == "fonts_not_in_use")
    report = verdict(synthetic_doc(rects, fonts=fonts_ok(handles=(5, 6, 0, 8))), good)
    check(
        "a refused face fails",
        report.verdict == "fail" and report.why == "font_refused_plex_sans_semibold",
    )
    report = verdict(synthetic_doc(rects, fonts=fonts_ok(granted=(True, True, True, False))), good)
    check(
        "a face never built fails",
        report.verdict == "fail" and report.why == "font_not_built_jetbrains_mono_regular",
    )
    report = verdict(synthetic_doc(rects, font_scale=1.5), good)
    check(
        "fonts drawn scaled from the size they were built at fail",
        report.verdict == "fail" and report.why == "fonts_scaled",
    )
    check(
        "a font scale within the tolerance passes",
        verdict(synthetic_doc(rects, font_scale=1.01), good).verdict == "pass",
    )
    check(
        "no font scale in the telemetry fails",
        verdict(synthetic_doc(rects, font_scale=None), good).why == "fonts_scaled",
    )

    # What cannot be decided.
    report = verdict(synthetic_doc(rects, open=False), good)
    check(
        "a closed panel is inconclusive",
        report.verdict == "inconclusive" and report.why == "panel_closed",
    )
    report = verdict(synthetic_doc(rects, collapsed=True), good)
    check(
        "a collapsed panel is inconclusive",
        report.verdict == "inconclusive" and report.why == "panel_collapsed",
    )
    check(
        "a panel that never drew is inconclusive",
        evaluate(None, good, list(PARTS)).why == "panel_never_drawn",
    )
    missing = {name: rect for name, rect in rects.items() if name != "stack_selected_row"}
    report = verdict(synthetic_doc(missing), flat_picture(missing, size))
    check(
        "a missing rect is inconclusive",
        report.verdict == "inconclusive" and report.why == "rect_missing_stack_selected_row",
    )
    report = verdict(
        synthetic_doc(missing), flat_picture(missing, size), parse_parts("header,panel_body")
    )
    check("a part not asked for is not required", report.verdict == "pass")
    optional = {**rects, "sub_nav_active": (1471.0, 600.0, 90.0, 26.0)}
    check(
        "an optional part drawn is judged",
        verdict(
            synthetic_doc(optional), flat_picture(optional, size, {"sub_nav_active": (0, 0, 0)})
        ).verdict
        == "fail",
    )
    cropped = Picture(486, 360, bytearray(486 * 360 * 3))
    report = verdict(doc, cropped)
    check(
        "an image that is a crop of the display is inconclusive",
        report.verdict == "inconclusive" and report.why == "image_aspect_not_display",
    )
    tiny = flat_picture(rects, (64, 36))
    report = verdict(doc, tiny)
    check(
        "an image too small for the rects is inconclusive",
        report.verdict == "inconclusive" and report.why.startswith("sample_too_small_"),
    )
    off = {**rects, "header": (1900.0, 0.0, 460.0, 33.0)}
    report = verdict(synthetic_doc(off), good)
    check("a rect past the display is inconclusive", report.why == "rect_off_display_header")
    check(
        "a window of another aspect is inconclusive",
        verdict(doc, good, window=(1460, 1080)).why == "window_aspect_not_display",
    )
    check(
        "a window of the display's aspect is accepted",
        verdict(doc, good, window=(2560, 1440)).verdict == "pass",
    )
    check(
        "a doubled image maps the same",
        verdict(doc, flat_picture(rects, (3840, 2160))).verdict == "pass",
    )
    scaled = {
        name: (x * 4 / 3, y * 4 / 3, w * 4 / 3, h * 4 / 3) for name, (x, y, w, h) in rects.items()
    }
    report = verdict(
        synthetic_doc(scaled, scale=4 / 3, display=[2560, 1440]),
        flat_picture(scaled, (854, 480), display=(2560.0, 1440.0)),
    )
    check("a 1440p panel at scale 4/3 passes", report.verdict == "pass")
    check("an unknown part is refused", _raises(lambda: parse_parts("header,sidebar")))

    # The telemetry document.
    check(
        "null is no panel", parse_panel({"schema": probe_checker().SCHEMA, "panel": None}) is None
    )
    check("another schema is refused", _raises(lambda: parse_panel({"schema": 99, "panel": None})))
    check(
        "a DLL without the panel key is refused",
        _raises(lambda: parse_panel({"schema": probe_checker().SCHEMA})),
    )
    bad = synthetic_doc(rects)
    bad["panel"]["rects"]["header"] = [1, 2, 3]
    check("a three-number rect is refused", _raises(lambda: parse_panel(bad)))
    a = parse_panel(synthetic_doc(rects))
    b = parse_panel(synthetic_doc(rects, written_ms=2000, draws=200, present=5000))
    c = parse_panel(synthetic_doc({**rects, "header": (1461.0, 0.0, 459.0, 34.0)}))
    check(
        "draws and presents moving is the same panel",
        a is not None and b is not None and a.geometry() == b.geometry(),
    )
    check(
        "a rect moving is another panel",
        a is not None and c is not None and a.geometry() != c.geometry(),
    )

    # The image readers.
    with tempfile.TemporaryDirectory(prefix="panel-look-") as tmp:
        root = pathlib.Path(tmp)
        ppm = root / "panel.ppm"
        ppm.write_bytes(b"P6\n# a comment\n" + good.ppm()[3:])
        loaded = load_picture(ppm)
        check(
            "a PPM reads back pixel for pixel",
            (loaded.width, loaded.height, bytes(loaded.rgb))
            == (good.width, good.height, bytes(good.rgb)),
        )
        check("a truncated PPM is refused", _raises(lambda: parse_ppm(good.ppm()[:-1])))

        # The capture's note.
        image = root / "shot.jpg"
        image.write_bytes(b"x")
        note = image.with_suffix(".txt")
        note.write_text(
            "captured ER window class=steam_app_1245620 geom=0,0 2560x1440 -> shot.jpg ({})\n",
            encoding="utf-8",
        )
        read = read_note(image)
        check(
            "a captured note gives the window and the file",
            read is not None
            and read.captured
            and read.window == (2560, 1440)
            and read.target == "shot.jpg",
        )
        note.write_text(
            "capture fail-closed: no window class=steam_app_1245620\n", encoding="utf-8"
        )
        read = read_note(image)
        check("a fail-closed note is no capture", read is not None and not read.captured)
        stamp = image.stat().st_mtime_ns
        os.utime(note, ns=(stamp - 10**9, stamp - 10**9))
        check("a note older than the image is an earlier capture's", read_note(image) is None)

        # The waits.
        telemetry = root / "er-cel-shade-telemetry.json"
        telemetry.write_text(json.dumps(synthetic_doc(rects, written_ms=5)), encoding="utf-8")
        written = telemetry.stat().st_mtime_ns
        got = await_writes(telemetry, written - 1, 1, 0.5)
        check("a write after the mark is taken", len(got) == 1 and got[0].written_ms == 5)
        started = time.monotonic()
        got = await_writes(telemetry, written, 1, 0.3)
        waited = time.monotonic() - started
        check(
            "a file nobody rewrites gives nothing and ends on the timeout",
            got == [] and 0.25 <= waited < 2.0,
        )
        telemetry.write_text('{"schema": 1, "written_ms": 6, "pan', encoding="utf-8")
        check("a half-written file is not taken", await_writes(telemetry, 0, 1, 0.1) == [])
        telemetry.write_text(json.dumps({"schema": 1, "written_ms": "x"}), encoding="utf-8")
        check("a malformed write is refused", _raises(lambda: await_writes(telemetry, 0, 1, 0.1)))

        # A run end to end: a capture, then two writes, the first within the lag.
        image = root / "run.ppm"
        image.write_bytes(good.ppm())
        image_ns = image.stat().st_mtime_ns
        telemetry.write_text(json.dumps(synthetic_doc(rects, written_ms=7)), encoding="utf-8")
        os.utime(telemetry, ns=(image_ns + 400_000_000, image_ns + 400_000_000))
        args = argparse.Namespace(
            telemetry=telemetry,
            artifact_dir=None,
            image=image,
            capture=None,
            timeout=0.3,
            max_lag=DEFAULT_MAX_LAG_SECONDS,
        )
        code, out = _run_quiet(args)
        check(
            "a run with no second write is inconclusive",
            code == EXIT_INCONCLUSIVE and "why=telemetry_not_refreshing" in out,
        )
        os.utime(telemetry, ns=(image_ns + 5 * 10**9, image_ns + 5 * 10**9))
        code, out = _run_quiet(args)
        check(
            "a first write long after the capture is inconclusive",
            code == EXIT_INCONCLUSIVE and "why=telemetry_after_capture_too_late" in out,
        )
        os.utime(telemetry, ns=(image_ns - 10**8, image_ns - 10**8))
        code, out = _run_quiet(args)
        check(
            "a write older than the capture is not taken",
            code == EXIT_INCONCLUSIVE and "why=no_telemetry_after_capture" in out,
        )
        first = Write(image_ns + 1, 7, parse_panel(synthetic_doc(rects, written_ms=7)))
        second = Write(image_ns + 2, 8, parse_panel(synthetic_doc(rects, written_ms=8, draws=180)))
        check(
            "a capture between two writes of one panel passes",
            judge(image, None, first, second, list(PARTS)).verdict == "pass",
        )
        moved = Write(
            image_ns + 2,
            8,
            parse_panel(synthetic_doc({**rects, "nav_active": (1471.0, 136.0, 64.0, 28.0)})),
        )
        check(
            "a panel that moved between the writes is inconclusive",
            judge(image, None, first, moved, list(PARTS)).why == "panel_changed_around_capture",
        )
        closed = Write(image_ns + 2, 8, parse_panel(synthetic_doc(rects, open=False)))
        check(
            "a panel closed in the next write is inconclusive",
            judge(image, None, first, closed, list(PARTS)).why == "panel_changed_around_capture",
        )
        garbage = root / "garbage.jpg"
        garbage.write_bytes(b"\xff\xd8\xff not a picture")
        report = judge(garbage, None, first, second, list(PARTS))
        check("an unreadable capture is inconclusive", report.why == "capture_unreadable")

    # The panel rendered with its fonts, through the capture's own shrink and JPEG.
    rendered = render_selftest()
    if rendered is None:
        print(
            "  skip  rendered panel: Pillow is not importable here "
            "(`uv run --with pillow python3 scripts/check-shader-panel-look.py --selftest` runs it)"
        )
    else:
        results.extend(rendered)

    failed = [label for label, ok in results if not ok]
    for label, ok in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


def _raises(action: Callable[[], object]) -> bool:
    try:
        action()
    except ValueError:
        return True
    return False


def _run_quiet(args: argparse.Namespace) -> tuple[int, str]:
    """`run` with its lines collected rather than printed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = run(args, list(PARTS))
    return code, buffer.getvalue()


# The rendered half of the selftest


# Dear ImGui 1.89's default dark style, for the look the panel had before its theme: the window, the
# active title bar, a tab, the active tab, a selected row (Header, 0.31 alpha) and a button (0.40).
IMGUI_DEFAULT = {
    "window": ((15, 15, 15), 0.94),
    "title": (41, 74, 122),
    "tab": (46, 89, 148),
    "tab_active": (51, 105, 173),
    "header": ((66, 150, 250), 0.31),
    "button": ((66, 150, 250), 0.40),
}


# The parts whose fill the theme changed from Dear ImGui's default; the body's is within 7 of it.
RESTYLED = {"header", "nav_active", "nav_inactive", "stack_selected_row", "add_layer_button"}


def part_results(report: Report) -> dict[str, str]:
    """Each judged part's `result=` from a report's lines."""
    out = {}
    for line in report.lines:
        part = re.match(r"part=(\w+) .*result=(\w+)$", line)
        if part:
            out[part.group(1)] = part.group(2)
    return out


def margins(report: Report) -> str:
    """Each judged part's difference from its token and its share, for the selftest's log."""
    out = []
    for line in report.lines:
        part = re.match(r"part=(\w+) .*share=([\d.]+) .*delta=(\d+) .*result=(\w+)$", line)
        if part:
            out.append(f"{part.group(1)}:{part.group(3)}@{part.group(2)}:{part.group(4)}")
    return " ".join(out)


def capture_pipeline() -> tuple[str, str] | None:
    """The `-resize` and `-quality` capture-er-window.py hands ImageMagick, read from it."""
    text = CAPTURE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'"-resize", "([^"]+)", "-quality", "(\d+)"', text)
    return (match.group(1), match.group(2)) if match else None


def render_selftest() -> list[tuple[str, bool]] | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    results: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        results.append((label, bool(ok)))

    pipeline = capture_pipeline()
    check("capture-er-window.py's shrink and quality are found", pipeline is not None)
    resize, quality = pipeline or ("854x480>", "35")
    fit = re.match(r"(\d+)x(\d+)", resize)
    fit_size = (int(fit.group(1)), int(fit.group(2))) if fit else (854, 480)
    magick = shutil.which("magick") or shutil.which("convert")

    fonts = {
        "regular": FONTS / "IBMPlexSans-Regular.ttf",
        "medium": FONTS / "IBMPlexSans-Medium.ttf",
        "semibold": FONTS / "IBMPlexSans-SemiBold.ttf",
        "mono": FONTS / "JetBrainsMono-Regular.ttf",
    }

    def face(kind: str, size: int):
        return ImageFont.truetype(str(fonts[kind]), size)

    def render(style: str, hover_inactive: bool = False):
        themed = style == "theme"
        image = Image.new("RGB", (int(DISPLAY[0]), int(DISPLAY[1])))
        draw = ImageDraw.Draw(image)
        palette = ((200, 170, 120), (90, 140, 200), (60, 160, 80), (230, 230, 220), (150, 60, 50))
        for index, x in enumerate(range(0, int(DISPLAY[0]), 37)):
            draw.rectangle([x, 0, x + 36, int(DISPLAY[1]) - 1], fill=palette[index % len(palette)])
        for index, y in enumerate(range(0, int(DISPLAY[1]), 53)):
            draw.rectangle(
                [0, y, int(DISPLAY[0]) - 1, y + 7], fill=palette[(index + 2) % len(palette)]
            )
        bg, alpha = (
            (rgb_of(TOKENS["PANEL_BG"]), BACKGROUND_ALPHA) if themed else IMGUI_DEFAULT["window"]
        )
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(
            [int(PANEL_LEFT), 0, int(DISPLAY[0]) - 1, int(DISPLAY[1]) - 1],
            fill=(*bg, round(alpha * 255)),
        )
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)

        def blend(colour, over, a):
            return tuple(round(a * c + (1 - a) * o) for c, o in zip(colour, over))

        flat_bg = blend(bg, (0, 0, 0), alpha)
        rule = (0x2E, 0x32, 0x3C)
        text, quiet, secondary = (0xEC, 0xE8, 0xDF), (0xA7, 0xA3, 0x9A), (0xC9, 0xC5, 0xBB)
        field, kbd_border = (0x1F, 0x22, 0x2A), (0x3A, 0x3F, 0x4B)
        left, right = int(PANEL_LEFT) + 1, int(DISPLAY[0])
        rects: dict[str, Rect] = {"panel": (PANEL_LEFT, 0.0, PANEL_WIDTH, DISPLAY[1])}
        draw.line([(int(PANEL_LEFT), 0), (int(PANEL_LEFT), int(DISPLAY[1]) - 1)], fill=rule)

        # The header.
        header = rgb_of(TOKENS["HEADER_BG"]) if themed else IMGUI_DEFAULT["title"]
        draw.rectangle([left, 0, right - 1, 31], fill=header)
        draw.line([(left, 32), (right - 1, 32)], fill=rule)
        rects["header"] = (float(left), 0.0, float(right - left), 33.0)
        draw.text((left + 12, 16), "Shaders", font=face("semibold", 13), fill=text, anchor="lm")
        close = right - 8 - 24
        draw.line([(close + 7, 9), (close + 15, 17)], fill=quiet, width=2)
        draw.line([(close + 15, 9), (close + 7, 17)], fill=quiet, width=2)
        collapse = close - 8 - 24
        draw.line(
            [(collapse + 10, 10), (collapse + 14, 16), (collapse + 10, 22)], fill=quiet, width=2
        )
        combo_w = round(face("regular", 13).getlength("Floating") + 16 + 22)
        combo_x = collapse - 8 - combo_w
        draw.rounded_rectangle(
            [combo_x, 5, combo_x + combo_w, 27], radius=3, fill=field, outline=rule
        )
        draw.text((combo_x + 8, 16), "Right", font=face("regular", 13), fill=text, anchor="lm")
        dock_w = face("regular", 12).getlength("Dock")
        draw.text(
            (combo_x - 6 - dock_w, 16), "Dock", font=face("regular", 12), fill=quiet, anchor="lm"
        )

        # The toolbar: three lines of text, chips, a combo and buttons.
        y = 43
        draw.rectangle([left + 14, y + 2, left + 27, y + 15], fill=field, outline=kbd_border)
        x = left + 14 + 13 + 6
        draw.text((x, y + 8), "Vanilla", font=face("regular", 13), fill=text, anchor="lm")
        x += round(face("regular", 13).getlength("Vanilla")) + 10
        hint = face("regular", 12)
        for key, tail in (("`", " to compare"), ("Tab", " to hide this panel")):
            draw.text((x, y + 8), "hold ", font=hint, fill=quiet, anchor="lm")
            x += round(hint.getlength("hold "))
            key_w = round(face("mono", 11).getlength(key)) + 10
            draw.rounded_rectangle([x, y + 1, x + key_w, y + 16], radius=3, outline=kbd_border)
            draw.text((x + 5, y + 8), key, font=face("mono", 11), fill=text, anchor="lm")
            x += key_w
            draw.text((x, y + 8), tail, font=hint, fill=quiet, anchor="lm")
            x += round(hint.getlength(tail)) + 10
        y = 68
        draw.text((left + 14, y + 12), "Preset", font=face("regular", 13), fill=quiet, anchor="lm")
        draw.rounded_rectangle(
            [left + 62, y, right - 170, y + 24], radius=3, fill=field, outline=rule
        )
        draw.text(
            (left + 70, y + 12), "Warm dusk", font=face("regular", 13), fill=text, anchor="lm"
        )
        x = right - 164
        for label in ("Save", "Revert"):
            width = round(face("regular", 13).getlength(label)) + 20
            draw.rounded_rectangle(
                [x, y, x + width, y + 24], radius=3, fill=(0x2A, 0x2F, 0x3A), outline=kbd_border
            )
            draw.text((x + 10, y + 12), label, font=face("regular", 13), fill=text, anchor="lm")
            x += width + 6
        y = 100
        x = left + 14
        for words in ("dxc 1.9 helper, signed", "probe live", "hooks 0.08 ms/frame"):
            draw.text((x, y + 8), words, font=face("regular", 12), fill=quiet, anchor="lm")
            x += round(face("regular", 12).getlength(words)) + 12
        draw.line([(left, 126), (right - 1, 126)], fill=rule)
        rects["toolbar"] = (float(left), 33.0, float(right - left), 94.0)

        # The section nav.
        top = 135
        x = left + 10
        for index, label in enumerate(("Layers", "Look", "Hunt", "Diagnostics")):
            active = index == 0
            font = face("medium" if active else "regular", 13)
            width = round(font.getlength(label)) + 24
            if themed:
                fill = rgb_of(
                    TOKENS[
                        "TAB_ACTIVE_BG"
                        if active
                        else ("TAB_HOVER_BG" if hover_inactive and index == 1 else "TAB_BG")
                    ]
                )
            else:
                fill = IMGUI_DEFAULT["tab_active" if active else "tab"]
            draw.rounded_rectangle(
                [x, top, x + width - 1, top + 27],
                radius=4,
                fill=fill,
                corners=(True, True, False, False),
            )
            draw.text(
                (x + 12, top + 14),
                label,
                font=font,
                fill=(0xF3, 0xE2, 0xB8) if active else secondary,
                anchor="lm",
            )
            if active:
                rects["nav_active"] = (float(x), float(top), float(width), 28.0)
            elif "nav_inactive" not in rects:
                rects["nav_inactive"] = (float(x), float(top), float(width), 28.0)
            x += width + 2
        draw.line([(left, 163), (right - 1, 163)], fill=rule)
        rects["nav"] = (float(left), 127.0, float(right - left), 37.0)

        # The stack: two headings and three rows, the second selected.
        heading = face("semibold", 11)
        draw.text((left + 16, 183), "LIGHTING", font=heading, fill=quiet, anchor="lm")
        row_left, row_right = left + 10, right - 10
        draw.ellipse([row_left + 8, 202, row_left + 16, 210], fill=(0x5B, 0xC2, 0x7A))
        draw.text(
            (row_left + 24, 206), "Depth fog", font=face("regular", 13), fill=text, anchor="lm"
        )
        draw.text((row_right - 8, 206), "21 ms", font=face("mono", 11), fill=quiet, anchor="rm")
        draw.text((left + 16, 237), "TONEMAP POST", font=heading, fill=quiet, anchor="lm")
        top = 250
        if themed:
            row = rgb_of(TOKENS["SELECTED_ROW_BG"])
        else:
            row = blend(IMGUI_DEFAULT["header"][0], flat_bg, IMGUI_DEFAULT["header"][1])
        draw.rounded_rectangle([row_left, top, row_right - 1, top + 27], radius=3, fill=row)
        draw.ellipse([row_left + 8, top + 10, row_left + 16, top + 18], fill=(0x5B, 0xC2, 0x7A))
        draw.text(
            (row_left + 24, top + 14),
            "Warm grade",
            font=face("medium", 13),
            fill=(0xF3, 0xE2, 0xB8),
            anchor="lm",
        )
        timing = round(face("mono", 11).getlength("24 ms"))
        draw.text(
            (row_right - 8, top + 14), "24 ms", font=face("mono", 11), fill=secondary, anchor="rm"
        )
        solo = row_right - 8 - timing - 8 - 22
        draw.rounded_rectangle(
            [solo, top + 3, solo + 22, top + 25],
            radius=3,
            fill=(0x2A, 0x2F, 0x3A),
            outline=kbd_border,
        )
        draw.text((solo + 11, top + 14), "S", font=face("semibold", 11), fill=text, anchor="mm")
        rects["stack_selected_row"] = (
            float(row_left),
            float(top),
            float(row_right - row_left),
            28.0,
        )
        draw.ellipse([row_left + 8, 290, row_left + 16, 298], fill=(0x5E, 0x5E, 0x5E))
        draw.text(
            (row_left + 24, 294), "Vignette", font=face("regular", 13), fill=quiet, anchor="lm"
        )

        # The button bar.
        top = 318
        x = left + 10
        for index, label in enumerate(("+ Layer", "Up", "Down", "Dup", "Del")):
            primary = index == 0
            font = face("semibold" if primary else "regular", 13)
            width = round(font.getlength(label)) + 20
            if primary:
                fill = (
                    rgb_of(TOKENS["PRIMARY_BG"])
                    if themed
                    else blend(IMGUI_DEFAULT["button"][0], flat_bg, IMGUI_DEFAULT["button"][1])
                )
                draw.rounded_rectangle([x, top, x + width - 1, top + 25], radius=3, fill=fill)
                draw.text(
                    (x + 10, top + 13),
                    label,
                    font=font,
                    fill=(0x1A, 0x14, 0x06) if themed else text,
                    anchor="lm",
                )
                rects["add_layer_button"] = (float(x), float(top), float(width), 26.0)
            else:
                draw.rounded_rectangle(
                    [x, top, x + width - 1, top + 25],
                    radius=3,
                    fill=(0x2A, 0x2F, 0x3A),
                    outline=kbd_border,
                )
                draw.text((x + 10, top + 13), label, font=font, fill=text, anchor="lm")
            x += width + 4
        draw.line([(left, 354), (right - 1, 354)], fill=rule)

        # The inspector: labelled fields, a slider, and the sub-tabs.
        y = 367
        for column, (label, value) in enumerate(
            (("Name", "Warm grade"), ("Stage", "Tonemap post"))
        ):
            x = left + 14 + column * 222
            draw.text((x, y + 7), label, font=face("regular", 12), fill=quiet, anchor="lm")
            draw.rounded_rectangle([x, y + 18, x + 210, y + 42], radius=3, fill=field, outline=rule)
            draw.text((x + 8, y + 30), value, font=face("regular", 13), fill=text, anchor="lm")
        y = 430
        x = left + 14
        for index, label in enumerate(("Parameters", "Code", "Selector")):
            active = index == 0
            font = face("medium" if active else "regular", 13)
            width = round(font.getlength(label)) + 24
            fill = (
                rgb_of(TOKENS["TAB_ACTIVE_BG" if active else "TAB_BG"])
                if themed
                else IMGUI_DEFAULT["tab_active" if active else "tab"]
            )
            draw.rounded_rectangle(
                [x, y, x + width - 1, y + 25],
                radius=4,
                fill=fill,
                corners=(True, True, False, False),
            )
            draw.text(
                (x + 12, y + 13),
                label,
                font=font,
                fill=(0xF3, 0xE2, 0xB8) if active else secondary,
                anchor="lm",
            )
            if active:
                rects["sub_nav_active"] = (float(x), float(y), float(width), 26.0)
            x += width + 2
        draw.line([(left + 14, y + 26), (right - 15, y + 26)], fill=rule)
        draw.text((left + 14, 478), "LIFT", font=heading, fill=quiet, anchor="lm")
        draw.text(
            (left + 14, 506), "Strength", font=face("regular", 13), fill=secondary, anchor="lm"
        )
        draw.rounded_rectangle(
            [left + 92, 495, right - 64, 517], radius=3, fill=field, outline=rule
        )
        draw.rounded_rectangle(
            [left + 200, 497, left + 212, 515], radius=2, fill=rgb_of(TOKENS["PRIMARY_BG"])
        )
        draw.text((right - 14, 506), "0.60", font=face("mono", 12), fill=text, anchor="rm")
        return image, rects

    def captured(image, how: str, directory: pathlib.Path) -> Picture | None:
        """`image` as capture-er-window.py would leave it: `magick` shrinks to fit and writes a
        JPEG at its quality, or keeps the shrink lossless into a PPM; `pil` does the same shrink
        and JPEG with Pillow; `full` is the PNG it keeps when ImageMagick is missing."""
        source = directory / "frame.png"
        image.save(source)
        if how == "full":
            return load_picture(source)
        if how in ("magick", "magick-ppm"):
            if magick is None:
                return None
            out = directory / ("capture.jpg" if how == "magick" else "capture.ppm")
            command = [magick, str(source), "-resize", resize]
            if how == "magick":
                command += ["-quality", quality]
            result = subprocess.run(
                [*command, str(out)],
                capture_output=True,
                timeout=MAGICK_TIMEOUT_SECONDS,
                check=False,
            )
            return load_picture(out) if result.returncode == 0 else None
        shrink = min(fit_size[0] / image.width, fit_size[1] / image.height, 1.0)
        small = image.resize(
            (round(image.width * shrink), round(image.height * shrink)), Image.Resampling.LANCZOS
        )
        out = directory / "capture-pil.jpg"
        small.save(out, quality=int(quality))
        return load_picture(out)

    themed, rects = render("theme")
    default, default_rects = render("default")
    hovered, _ = render("theme", hover_inactive=True)
    doc = synthetic_doc(rects)
    with tempfile.TemporaryDirectory(prefix="panel-look-render-") as tmp:
        directory = pathlib.Path(tmp)
        for how in ("full", "magick", "magick-ppm", "pil"):
            picture = captured(themed, how, directory)
            if picture is None:
                print(f"  skip  rendered panel through {how}: no ImageMagick on this machine")
                continue
            report = evaluate(parse_panel(doc), picture, list(PARTS))
            print(f"  info  themed through {how}: {report.verdict} {margins(report)}")
            results_seen = part_results(report)
            if picture.lossy:
                # Through the JPEG a labelled part may land inside the allowance, never past it.
                ok = report.verdict == "pass" or (
                    report.verdict == "inconclusive"
                    and set(results_seen.values()) <= {"pass", "lossy"}
                )
                label = f"the themed panel through {how} passes or is inconclusive only as lossy"
            else:
                ok = report.verdict == "pass"
                label = f"the themed panel through {how} passes"
            check(label, ok)
            if not ok:
                for line in report.lines:
                    print(f"        {line}")
        for how in ("full", "magick", "pil"):
            picture = captured(default, how, directory)
            if picture is None:
                continue
            report = evaluate(parse_panel(synthetic_doc(default_rects)), picture, list(PARTS))
            print(f"  info  default look through {how}: {report.verdict} {margins(report)}")
            failed = {name for name, result in part_results(report).items() if result == "fail"}
            ok = report.verdict == "fail" and RESTYLED <= failed
            check(f"Dear ImGui's default look through {how} fails on every fill it changes", ok)
            if not ok:
                for line in report.lines:
                    print(f"        {line}")
        picture = captured(hovered, "magick" if magick else "pil", directory)
        if picture is not None:
            report = evaluate(parse_panel(doc), picture, list(PARTS))
            check(
                "a hovered tab through the capture is inconclusive",
                report.verdict == "inconclusive" and report.why == "nav_inactive_hovered",
            )
    return results


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
