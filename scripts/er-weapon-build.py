#!/usr/bin/env python3
"""A whole planner build made around one weapon, uploaded as a new `?b=` link.

    python3 scripts/er-weapon-build.py "Poleblade of the Bud"
    python3 scripts/er-weapon-build.py Uchigatana --rl 125 --two-handed
    python3 scripts/er-weapon-build.py "Poleblade of the Bud" --dry-run    # stop before the pipeline
    python3 scripts/er-weapon-build.py "Poleblade of the Bud" --no-upload  # every pass, no upload
    python3 scripts/er-weapon-build.py --affinity Heavy --rl 125 --fight duel --host zerk   # run on zerk
    python3 scripts/er-weapon-build.py --affinity Heavy --rl 125 --host zerk --list-sync   # what it copies
    python3 scripts/er-weapon-build.py --selftest

`--host <name>` (default `ER_WEAPON_BUILD_HOST`, else `local`) runs the same command on that machine
(`run_on_host`). The machines share no files, only path spellings, so it first copies `scripts/`,
`data/`, the duel tables, every outside input the scripts name (`HOME_INPUTS`), the planner cache
and the run's own directory to the same paths there; runs it under nice in the memory and cores
`gen-r3-weapon-boards.py` would grant there (`run_grant`), with its output streamed to this
process's; and copies the run's directory and the cache back. Stopping the local process stops the
host's run: there it runs under `supervise`, in its own process group, which is killed when the
ssh session closes or this process's heartbeats stop arriving (`run_guarded`). The local process lives until that
last copy ends and ssh's own command line names no script, so a tab follows a remote run with the
same `kitty-tail-task.py --match er-weapon-build.py --match '--fight duel'` as a local one.

Each part comes from the tool that owns it:

| part | source |
| --- | --- |
| the weapon's affinity, grease and level | `er-builds-optimize.weapon_best`: the spread that hits hardest with the weapon alone, whose hit is the yardstick below |
| class and stats | `kit_spread`: every requirement met, Vigor from the window's PvP pool, Mind and Endurance following the spread (`spend_floored`), and every other point where it adds most to the build's value (`KitScorer`): the weapon's hit over its best, plus the kit's share (`Kit`), one equal part each |
| talismans | `er-mechanics-gear-synergy.rank`: its talisman rows, best first, one per `accessoryGroup`; slots it leaves empty take what builds holding the weapon wear most (`er-builds-optimize.gear`) |
| Vigor, Endurance and armor | `joint_pick`, one choice: every Vigor around the window's floor and every Endurance up to a few past the least that fits, each with the damage stats the remaining points buy and each armor set from `er-mechanics-armor-targets.ArmorModel` that keeps medium roll with the talismans and the weapons the build carries and reaches `melee_poise_floor` of the armor's own poise (an HP piece such as Imp Head (Lion) offered both worn and not), scored by damage x HP / the window's mean PvP hit taken. Light roll is never a candidate in a duel (ladder ban). Sets below the poise floor are scored and reported, not chosen |
| the gear's weapon (no weapon named) | `kit_lead`: the `AffinityKit` class lead the cross-class main-hand score ranks best, from an earlier pass's roles; after this run's first pass, `roles_lead` over `class_leads` at the affinity, which re-picks the talismans and tears and runs another pass when it differs |
| what the build carries | the pipeline's own plan (`--no-upload`): its heaviest equip set, both hands and their swaps (`heaviest_set`). The first pass can only price the weapon alone, so when the plan's heaviest set needs more Endurance the stats are re-chosen and the pipeline runs again, until a plan's heaviest set fits what it was budgeted for (`MAX_ROUNDS`); only then is anything uploaded. Before each pass, `weight_cost` prices every carried weight from 0 to `WEIGHT_MAX` in `joint_pick`'s damage x EHP (`<prefix>-weight-cost.json`), and the loadout chooses the equip sets against it (`--weight-cost`), so a heavy set is taken only when it is worth the stats it costs |
| the other weapons' affinity | `er-builds-optimize.weapons_for` at the final stats: the affinity most of its top ten rows take |
| the rest of the inventory, the equip sets, the upload | `er-mechanics-build-pipeline.sh --focus <weapon>:<affinity>` over `--base`, which supplies the face, great rune and the items the pipeline does not set |

The kit is why the stats are not the weapon's own optimum (user, 2026-10-14: "if I only have enough
stats for 3 weapons, the build is bust and a user doesn't know ahead of time what stats to use").
It is every weapon the every-class grease sweep at this RL builds, grouped by weapon class; a
class's share is the best weapon the spread can use over the best weapon the class has, each
weapon counted at its own best damage there, and the kit's share is the mean over classes. A
weapon is usable when it is wieldable (every requirement met one-handed) or semi-wieldable
(every requirement but STR met one-handed, and STR met two-handed) -- the user's definitions,
2026-10-14. Bows, crossbows, staves and seals are not in the sweep and so not in the kit.

A staff or seal gets no poise floor: the user's floor is for melee builds (2026-10-13: "less
than 89 poise without bullgoat, unless it was a mage build").

`--fight duel` also makes the build ladder legal by name (`ladder_bans`, the tables
`er-builds-ladder.py` judges public builds with). A banned focus weapon or `--spell` stops the run
before anything is chosen; otherwise every banned item is left out of its picker instead of
being removed afterwards: banned weapons from the kit the stats are chosen for, talismans, tears,
armor, the pouch's goods and spells, and the loadout's weapons and skills (`loadout_exclude`,
Muleta among them once a talisman raises ash damage). The upload then runs with `--ladder` and
refuses a build that still carries a banned item in any equip set. Rules that need params or
play to judge (guard above 90%, two-hand-only bans, flask drinks per round) are not checked.

Files go to `--dir` (default `~/.cache/er-build-planner/weapon-builds/<weapon>-rl<rl>/`): the base
build and, per stat block (`<weapon>-<hash of the stats>`), `-gear.json`, `-build.json` (what was
chosen and from what) and the pipeline's own files. The pipeline skips a step whose output is
newer than its inputs, so a re-run picks up where a stopped one left off. A pass runs for about
an hour from empty caches; the link prints last.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "er-mechanics-build-pipeline.sh"
#: The pipeline's own default base build (`BASE_ID` in `er-mechanics-build-pipeline.sh`).
BASE_ID = "188e74da444029"
TALISMAN_SLOTS = 4
#: Rows of `weapons_for` whose most common affinity the rest of the inventory takes.
AFFINITY_ROWS = 10
#: Talisman suggestions read from the build embedding when synergy leaves slots empty.
FALLBACK_TALISMANS = 20
#: Pipeline passes at most: each one after the first runs because the plan's heaviest equip set
#: needed more Endurance than the last pass budgeted for.
MAX_ROUNDS = 4
#: Vigor `joint_pick` tries around the window's Vigor floor: below it to buy damage, above it for HP.
VIG_BELOW, VIG_ABOVE = 12, 8
#: Endurance `joint_pick` tries past the least at which a floor-poise set keeps medium roll, from
#: the starting class's own Endurance up (the sets below the floor are scored for the report).
END_ABOVE = 8
#: A duel's opening talismans, before the weapon's own ranking fills the last slot (user,
#: 2026-10-18): Ritual Shield at full HP, Crimson Amber +3 so a swap down the medallion line can
#: put the player back at full HP, and Bull-Goat's on top of armor that reaches the poise floor
#: alone. Measured on the RL 125 PvP corpus the same day, their first sets wear them 23.8%, 14.5%
#: and 27.8% of the time; a planner page records no mid-fight swap, which is how this kit is used.
DUEL_TALISMANS = ("Ritual Shield Talisman", "Crimson Amber Medallion +3", "Bull-Goat's Talisman")
#: Each swap after a hit: the next lower medallion makes the lower max HP the full HP again, so
#: Ritual Shield works once more. The swap is done in the equip menu, in the medallion's own slot,
#: so these are carried right after +3 in the talisman inventory, one d-pad press apart (user,
#: 2026-10-18), and are in no equip set.
DUEL_MEDALLION_SWAPS = ("Crimson Amber Medallion +2", "Crimson Amber Medallion +1", "Crimson Amber Medallion")
#: `EquipParamWeapon` requirement field per planner stat key.
REQUIREMENT_FIELDS = {"str": "properStrength", "dex": "properAgility", "int": "properMagic",
                      "fth": "properFaith", "arc": "properLuck"}


#: The live view (the status line and the pipeline frame redrawn in place) only on a terminal.
TTY = sys.stdout.isatty()
#: Colour always, a redirected run included: its output file is shown in a terminal
#: (`scripts/kitty-tail-task.py`), user 2026-10-19: "Why would I want to not force color?". Never
#: under `NO_COLOR`; `weapon-build.log` always gets plain text.
COLOR = not os.environ.get("NO_COLOR")
RESET, BOLD = ("\033[0m", "\033[1m") if COLOR else ("", "")
GREEN, YELLOW, RED, GREY = ("\033[32m", "\033[33m", "\033[31m", "\033[90m") if COLOR else ("",) * 4
ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")
#: Seconds between redraws of the pipeline's live view (`er-mechanics-pipeline-watch.frame`).
REDRAW_SECONDS = 2.0
#: Columns of the in-place bar line a redirected run writes (`Ticker`): `$COLUMNS`, else the 80
#: `shutil` assumes for a stream that is not a terminal, so the line fits any terminal that wide.
BAR_COLUMNS = shutil.get_terminal_size().columns
#: Cells of the bar in that line: room for the stage, its time in, its counter and the counter's age
#: after it at 80 columns (`redundant status 1m02s 300/400, no progress for 14m` is 51).
BAR_LINE_WIDTH = 10
_LOG = []


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}h{s // 60 % 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


#: The run's phases in order, as the status line numbers them: (key, text its ✓ line holds).
PHASES = (("base", "base build"), ("spread", "own best spread"), ("kit", "stats for the whole kit"),
          ("talismans", "talismans"), ("buffs", "great rune and crystal tears"),
          ("armor", "armor and Endurance"), ("affinity", "affinity for the other weapons"),
          ("cost", "what carried weight costs"), ("pass", "pass"), ("pouch", "tools, flasks and spells"), ("upload", "upload"))
#: Seconds a phase takes when no earlier run in `weapon-build.log` says (the pass is the 1h49m
#: measured on Poleblade of the Bud, 2026-10-14).
PHASE_DEFAULT_S = {"base": 1, "spread": 5, "kit": 5, "talismans": 180, "buffs": 15, "armor": 15, "affinity": 1, "cost": 120,
                   "pass": 6540, "pouch": 20, "upload": 10}
CLOCK = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_OUT = threading.RLock()
#: {pipeline step id: seconds} of the latest finished pass in `weapon-build.log` (`step_history`).
STEP_HISTORY = {}


def clock_seconds(text: str) -> float | None:
    m = CLOCK.fullmatch(text)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def phase_history(lines) -> dict:
    """{phase key: seconds} from earlier runs' ✓ lines (`PHASES`), the latest run's winning."""
    out = {}
    for line in lines:
        line = ANSI.sub("", line).strip()
        if not line.startswith("✓ "):
            continue
        secs = clock_seconds(line.rsplit(" ", 1)[-1])
        key = next((k for k, text in PHASES if (line[2:].startswith(text) if k == "pass" else text in line)), None)
        if key and secs is not None:
            out[key] = secs
    return out


class Progress:
    """Where the run is: phase n of `PHASES`, how long the phase has run against how long it took
    last time, and the whole run's share done and time left by those same expectations."""

    def __init__(self, expected: dict):
        self.expected = {**PHASE_DEFAULT_S, **expected}
        self.spent = 0.0
        self.key, self.t0, self.part = None, None, None
        #: Seconds left of the phase measured from its own counters (`pass_left`), which replace
        #: the last run's time while they are known: a step that was cached last time and computes
        #: from empty now would otherwise hold the time left still for as long as it runs.
        self.left = None
        #: The phase's running step or stage has no time to go on (never timed, no counter): what
        #: is left is then `rest_known` seconds plus an unknown share, shown as `~30s+?`, never as
        #: the seconds a weight of one would claim.
        self.unknown, self.rest_known = False, 0.0
        self.passes = 0

    def start(self, key):
        self.key, self.t0, self.part, self.left = key, time.monotonic(), None, None
        self.unknown, self.rest_known = False, 0.0
        self.passes += key == "pass"

    def stop(self):
        if self.t0 is not None:
            self.spent += time.monotonic() - self.t0
        self.key, self.t0 = None, None

    def numbers(self) -> dict:
        """The phase's number, name, share done, elapsed and expected seconds, and the run's share
        done and seconds left."""
        keys = [k for k, _ in PHASES]
        n = keys.index(self.key) + 1
        el = time.monotonic() - self.t0
        exp = self.expected[self.key]
        if self.left is not None:
            exp = el + self.left
        frac = (el / exp if self.left is not None else self.part if self.part is not None
                else min(0.99, el / exp)) if exp else 0.0
        now_left = self.rest_known if self.unknown else max(0.0, exp * (1 - frac))
        later = sum(self.expected[k] for k in keys[n:] if k != "upload" or not NO_UPLOAD[0])
        done = self.spent + el
        total = done + now_left + later
        what = dict(PHASES)[self.key] + (f" {self.passes} of at most {MAX_ROUNDS}" if self.key == "pass" else "")
        return {"n": n, "what": what, "frac": frac, "el": el, "exp": exp,
                "run": done / total if total else 0.0, "left": total - done, "unknown": self.unknown}

    @staticmethod
    def left_text(x: dict) -> str:
        """`~45s`, or with an unknown share `~30s+?` (`?` alone when nothing else is left)."""
        if not x["unknown"]:
            return f"~{clock(x['left'])}"
        return f"~{clock(x['left'])}+?" if x["left"] >= 1 else "?"

    def line(self) -> str:
        if self.key is None:
            return ""
        x = self.numbers()
        exp = "?" if x["unknown"] else f"~{clock(x['exp'])}"
        return (f"{BOLD}[{x['n']}/{len(PHASES)}] {x['what']}{RESET}  {GREEN}{100 * x['frac']:.0f}%{RESET} of it, "
                f"{clock(x['el'])} of {exp}  {GREY}|{RESET}  run {GREEN}~{100 * x['run']:.0f}%{RESET}, "
                f"{self.left_text(x)} left")

    def bar_line(self, bar, detail: str = "", pulse: str = "") -> str:
        """One short line for a run whose output is a file shown in a terminal, redrawn in place
        with a carriage return: the run's bar first, so cutting it to the screen keeps the bar.
        `pulse` (`er-mechanics-pipeline-watch.pulse_text`: the running stage's counter and how long
        ago it last moved) comes straight after the bar and the bar is `BAR_LINE_WIDTH` wide, so
        both fit in the 80 columns a redirected run is cut to: they are what tells a live pass
        from a stalled one."""
        if self.key is None:
            return ""
        x = self.numbers()
        return (f"{bar(x['run'], BAR_LINE_WIDTH)} {GREEN}{100 * x['run']:.0f}%{RESET} {self.left_text(x)} left  "
                + (f"{pulse}  " if pulse else "")
                + f"{BOLD}[{x['n']}/{len(PHASES)}] {x['what']}{RESET} {100 * x['frac']:.0f}%"
                + (f"  {GREY}|{RESET}  {detail}" if detail else ""))


PROGRESS = Progress({})
NO_UPLOAD = [False]


class Ticker:
    """Redraws the status line in place on a terminal while a phase runs, between `say` lines."""

    def __init__(self):
        self.shown = False
        self.paused = False
        #: Off a terminal: what the running pass is on (`run_pass`), shown after the bar.
        self.detail = ""
        #: Off a terminal: the running stage's counter and its age (`pulse_text`), after the bar.
        self.pulse = ""
        self.bar = None
        self.stopped = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        # Nothing signals "a second went by" for the clocks the line shows, so the redraw period is
        # the cadence; `stopped` ends the loop at once instead of a period later.
        while not self.stopped.wait(REDRAW_SECONDS):
            if not TTY and PROGRESS.key and self.bar:
                # A redirected run is still read in a terminal (`kitty-tail-task.py` tails it), and
                # `tail` passes a carriage return through, so the bar line is redrawn in place every
                # period instead of the run printing nothing for a whole step (user, 2026-10-19).
                # The terminal's width is unknown here, so the line is cut to the fallback width
                # and never wraps (a wrapped line is not returned to by a carriage return).
                with _OUT:
                    out = getattr(sys.stdout, "real", sys.stdout)
                    out.write("\r\033[K" + fit_frame(PROGRESS.bar_line(self.bar, self.detail, self.pulse),
                                                     BAR_COLUMNS, 2))
                    out.flush()
                    self.shown = True
            if TTY and not self.paused and PROGRESS.key:
                with _OUT:
                    out = getattr(sys.stdout, "real", sys.stdout)
                    size = shutil.get_terminal_size()
                    out.write("\r\033[K" + fit_frame(PROGRESS.line(), size.columns, 2))
                    out.flush()
                    self.shown = True


TICKER = None


class StatusOut:
    """stdout or stderr on a terminal: clears the status line before anything else is written to
    it, so a sibling module's own print (the interrupt cache's warning goes to stderr) lands on a
    clean row instead of after the status text."""

    def __init__(self, real):
        self.real = real

    def write(self, s):
        with _OUT:
            if s and TICKER and TICKER.shown:
                getattr(sys.stdout, "real", sys.stdout).write("\r\033[K")
                TICKER.shown = False
            return self.real.write(s)

    def flush(self):
        self.real.flush()

    def __getattr__(self, name):
        return getattr(self.real, name)


def say(text: str, mark: str = " ", colour: str = "") -> None:
    """One line of the run's account: coloured on a terminal, plain in `weapon-build.log`."""
    line = f"{colour}{mark}{RESET} {text}"
    with _OUT:
        print(line, flush=True)
    for fh in _LOG:
        fh.write(ANSI.sub("", line) + "\n")
        fh.flush()


class phase:
    """`with phase("talismans", "talismans"):` prints a ▶ line when the step starts and a ✓ line
    with how long it took when it ends, or ✗ when it raised (a `sys.exit` included); `key` (one of
    `PHASES`) is what the status line counts it as."""

    def __init__(self, label: str, key: str | None = None):
        self.label, self.key = label, key

    def __enter__(self):
        self.t0 = time.monotonic()
        say(f"{BOLD}{self.label}{RESET} ...", "▶", YELLOW)
        if self.key:
            PROGRESS.start(self.key)
        return self

    def __exit__(self, kind, *_):
        if self.key:
            PROGRESS.stop()
        say(f"{self.label} {GREY}{clock(time.monotonic() - self.t0)}{RESET}", "✗" if kind else "✓",
            RED if kind else GREEN)
        return False


def fit_frame(text: str, cols: int, rows: int) -> str:
    """`text` cut to a `cols` x `rows` terminal: each line to `cols - 1` visible characters (escape
    sequences kept, a reset added where a line is cut), and a frame taller than `rows - 1` lines
    to its first line and its last ones. Cursor-up cannot move above the top of the screen and a
    wrapped line takes two rows, so an uncut frame leaves its first lines behind on every redraw."""
    def clip(line):
        out, seen, i = [], 0, 0
        while i < len(line):
            m = ANSI.match(line, i)
            if m:
                out.append(m.group())
                i = m.end()
                continue
            if seen >= cols - 1:
                out.append(RESET)
                break
            out.append(line[i])
            seen += 1
            i += 1
        return "".join(out)
    lines = [clip(line) for line in text.splitlines()]
    if rows > 2 and len(lines) > rows - 1:
        lines = lines[:1] + lines[len(lines) - (rows - 2):]
    return "\n".join(lines)


STEP_LINE = re.compile(r"^\s*✓\s+(\d+[ab]?)\s+.+?\s{2,}(\S+)\s*$")


def write_if_changed(path: Path, text: str) -> None:
    """Write `text` unless `path` already holds it: the pipeline skips a step whose output is newer
    than its inputs, so rewriting an identical input would redo every step after it."""
    if not (path.exists() and path.read_text(encoding="utf-8") == text):
        path.write_text(text, encoding="utf-8")


def step_history(lines) -> dict:
    """{step id: seconds} from the ✓ step lines of finished passes in `weapon-build.log` (each
    pass's last view is written there), the latest pass winning. The pipeline's own logs are no
    use for this: a pass with the same stats overwrites its log, and a stopped one never ends."""
    out = {}
    for line in lines:
        m = STEP_LINE.match(ANSI.sub("", line))
        if m:
            secs = clock_seconds(m[2])
            if secs is not None:
                out[m[1]] = secs
    return out


def pass_progress(lines, plan: list[str], hist: dict, now: dt.datetime) -> tuple[float, int, str]:
    """(share of the pass done, step number, step id) from a running pipeline log's `lines`: the
    steps before the current one by their `hist` seconds (one each with no history), the current
    one by its elapsed time, at most 99% of what it took last time."""
    heads = [(m[2], m[1]) for m in (re.match(r"^== (\d\d:\d\d:\d\d) (\S+)", line) for line in lines) if m]
    if not heads:
        return 0.0, 0, ""
    w = {s: hist.get(s, 1.0) for s in plan}
    total = sum(w.values()) or 1.0
    sid, t = heads[-1]
    if sid not in plan:
        return 1.0, len(plan), sid
    idx = plan.index(sid)
    started = dt.datetime.combine(now.date(), dt.time.fromisoformat(t))
    el = (now - started).total_seconds() % 86400
    return (sum(w[s] for s in plan[:idx]) + min(el, 0.99 * w[sid])) / total, idx + 1, sid


def step_share(plan: list[str], hist: dict, sid: str, frac: float) -> float:
    """The pass's share done with step `sid` `frac` of the way through, the steps weighed as
    `pass_progress` weighs them (their `hist` seconds, one each with no history)."""
    w = {s: hist.get(s, 1.0) for s in plan}
    total = sum(w.values()) or 1.0
    return (sum(w[s] for s in plan[:plan.index(sid)]) + min(1.0, frac) * w[sid]) / total


def pass_left(plan: list[str], hist: dict, sid: str, step_left: float | None) -> float | None:
    """Seconds left of the pass with step `sid` `step_left` seconds from done by its own counters:
    that plus the later steps' `hist` seconds. None when `step_left` is, or a later step has no
    history to go on."""
    later = plan[plan.index(sid) + 1:] if sid in plan else []
    if step_left is None or any(s not in hist for s in later):
        return None
    return step_left + sum(hist[s] for s in later)


def update_pass(lines, read_loadout, plan: list[str], hist: dict, now: dt.datetime, watch):
    """Set `PROGRESS.part` and `PROGRESS.left` from a running pass's pipeline log `lines` and, in
    the loadout step, the lines `read_loadout()` returns; returns (step number, step id, loadout
    lines, the loadout's `stage_progress` or None). The loadout's stage counters (the interrupt
    pass's `n/143 weapons, r/8640 rows` among them) say how far its stage is and how fast it goes,
    which the last run's times cannot when that stage was cached then and computes from empty now."""
    PROGRESS.part, n, sid = pass_progress(lines, plan, hist, now)
    loadout = read_loadout() if sid == "9" else []
    sp = watch.stage_progress(loadout, stage_elapsed(lines, now)) if loadout else None
    if sp and sid in plan:
        PROGRESS.part = step_share(plan, hist, sid, sp["frac"])
    PROGRESS.left = pass_left(plan, hist, sid, sp["left"]) if sp else None
    # No time to go on for the running stage (never timed, no counter) or the running step (no
    # history while other steps have one): the pass's time left is what the later stages and steps
    # are known to take plus an unknown share, never the seconds a weight of one would claim.
    PROGRESS.unknown = PROGRESS.left is None and (sp is not None or (bool(hist) and sid in plan and sid not in hist))
    later = plan[plan.index(sid) + 1:] if sid in plan else []
    PROGRESS.rest_known = (((sp["stage_left"] or 0.0) + sp["later_known"]) if sp else 0.0) + sum(
        hist.get(s, 0.0) for s in later)
    return n, sid, loadout, sp


def stage_elapsed(lines, now: dt.datetime | None = None) -> float:
    """Seconds since the pipeline log's latest `== HH:MM:SS` step head (0 before one)."""
    heads = [m[1] for m in (re.match(r"^== (\d\d:\d\d:\d\d) ", line) for line in lines) if m]
    if not heads:
        return 0.0
    now = now or dt.datetime.now()
    started = dt.datetime.combine(now.date(), dt.time.fromisoformat(heads[-1]))
    return (now - started).total_seconds() % 86400


def stream_line(lines, loadout_lines, plan: list[str], labels: dict, stage_re) -> str:
    """The step a running pass is on, as one plain line for a run whose output is a file: the
    latest `== HH:MM:SS <id>` head of the pipeline log, and during the loadout its latest
    `stage k/n: name` line. Empty before the first step."""
    heads = [m[1] for m in (re.match(r"^== \d\d:\d\d:\d\d (\S+)", line) for line in lines) if m]
    if not heads:
        return ""
    sid = heads[-1]
    n = plan.index(sid) + 1 if sid in plan else len(plan)
    line = f"step {n} of {len(plan)}: {labels.get(sid, sid)}"
    if sid == "9":
        stages = [m for m in (stage_re.match(x) for x in loadout_lines) if m]
        if stages:
            m = stages[-1]
            line += f", stage {m[1]}/{m[2]}: {m[3]}"
    return line


def pass_pulse(watch, pulse, step: dict | None, sp: dict | None, p: Path, now: dt.datetime,
               el: float | None = None, unit: bool = True) -> str:
    """The running stage (or step) and how long it has been in it (`el`, the step's seconds,
    outside the loadout), its counter and the counter's age, for the bar line
    (`er-mechanics-pipeline-watch.pulse_text`): `interrupt 30s 2492/8215 rows, last step 31s ago`,
    red `no progress for 14m` once the counter has stopped for many of its own gaps; without `unit`
    the counter's word is left out, for the 80-column bar line. Every loadout stage and every long
    step writes a counter (`er-mechanics-pipeline-watch.stage_counters`)."""
    if step is None:
        return ""
    text = watch.pulse_text(pulse, step, p, sp if step["kind"] == "stages" else None, now.timestamp(), el, unit)
    return text if COLOR else ANSI.sub("", text)


def run_pass(cmd: list[str], d: Path, prefix: str, watch) -> int:
    """Run one pipeline pass with its output in `<prefix>-pipeline.log`, the file
    `er-mechanics-pipeline-watch.py` reads, and redraw that tool's live view in place (a progress
    bar per step, elapsed time and what is left) until the pass ends; off a terminal, print only
    the last view."""
    log = d / f"{prefix}-pipeline.log"
    hist = STEP_HISTORY
    plan = [s["id"] for s in watch.steps(d / prefix) if s["id"] != "10"]
    labels = {s["id"]: s["label"] for s in watch.steps(d / prefix)}
    by_id = {s["id"]: s for s in watch.steps(d / prefix)}
    # Measured here, where the logs are: a `--host` run computes the line on the host and the
    # local end only streams it, so the age is the host's own and no clock crosses machines.
    pulse = watch.Pulse(REDRAW_SECONDS)

    def read_loadout():
        return watch.read_lines(d / f"{prefix}-loadout.log")
    if hist:
        PROGRESS.expected["pass"] = sum(v for k, v in hist.items() if k in plan)
    PROGRESS.start("pass")
    if TICKER:
        TICKER.paused = True
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
        drawn = 0
        streamed = None
        while True:
            # The pipeline's exit ends the wait; a redraw period without it redraws the frame.
            try:
                proc.wait(timeout=REDRAW_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            if not TTY:
                # Off a terminal (a file tailed in a kitty tab) the ticker redraws one bar line in
                # place; this keeps its share and detail current and prints each new step and
                # loadout stage as a line of its own. The pass otherwise looked frozen for its
                # whole length (user, 2026-10-19).
                lines = watch.read_lines(log)
                now = dt.datetime.now()
                _, sid, loadout, sp = update_pass(lines, read_loadout, plan, hist, now, watch)
                line = stream_line(lines, loadout, plan, labels, watch.STAGE)
                # The counter's word left out: the bar line is cut to 80 columns, and the age after
                # the counter is the part that must survive the cut.
                beat = pass_pulse(watch, pulse, by_id.get(sid), sp, d / prefix, now, stage_elapsed(lines, now),
                                  unit=False)
                if TICKER:
                    TICKER.bar = watch.bar
                    TICKER.pulse = beat
                    TICKER.detail = line + (f" {100 * sp['frac']:.0f}%" if sp else "")
                if line and line != streamed:
                    streamed = line
                    say(line, "·", GREY)
                continue
            if TTY:
                now = dt.datetime.now()
                lines = watch.read_lines(log)
                n, sid, _, sp = update_pass(lines, read_loadout, plan, hist, now, watch)
                beat = pass_pulse(watch, pulse, by_id.get(sid), sp, d / prefix, now, stage_elapsed(lines, now))
                head = PROGRESS.line() + (f"  {GREY}|{RESET}  {beat}" if beat else "") + (
                    f"  {GREY}|{RESET}  step {n} of {len(plan)}: {labels.get(sid, sid)}" if n else "")
                size = shutil.get_terminal_size()
                text = fit_frame(head + "\n" + watch.frame(d, prefix, now), size.columns, size.lines)
                with _OUT:
                    sys.stdout.write((f"\033[{drawn}F\033[J" if drawn else "") + text + "\n")
                    sys.stdout.flush()
                drawn = text.count("\n") + 1
    PROGRESS.stop()
    if TICKER:
        TICKER.paused = False
        TICKER.detail = ""
        TICKER.pulse = ""
    text = watch.frame(d, prefix, dt.datetime.now())
    if drawn:
        sys.stdout.write(f"\033[{drawn}F\033[J")
    for line in text.splitlines():
        say(line if COLOR else ANSI.sub("", line), "")
    return proc.returncode


def _mod(name: str, fname: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def stats_text(stats: dict, rl: int) -> str:
    """The pipeline's `--stats`: the eight attributes and the RL they sum to."""
    return ",".join(f"{k}={stats[k]}" for k in ("vig", "mnd", "end", "str", "dex", "int", "fth", "arc")) + f",rl={rl}"


def fill_talismans(ranked: list[str], fallback: list[str], group_of, slots: int = TALISMAN_SLOTS) -> list[str]:
    """Up to `slots` talismans: `ranked` first, then `fallback`, never two of one `accessoryGroup`.
    `group_of(name)` is the name's group, None for a talisman in none, and raises KeyError for a
    name that is not a talisman, which skips it."""
    picks, groups = [], set()
    for name in ranked + fallback:
        if len(picks) == slots:
            break
        try:
            g = group_of(name)
        except KeyError:
            continue
        if name in picks or (g and g in groups):
            continue
        picks.append(name)
        if g:
            groups.add(g)
    return picks


def first_fit(end: int, cap: int, budget_at, pick):
    """(Endurance, set) for the least Endurance from `end` up to `cap` at which `pick(budget_at(e))`
    finds a set, or None."""
    for e in range(end, cap + 1):
        found = pick(budget_at(e))
        if found:
            return e, found
    return None


def damage_term(spread: dict) -> float:
    """The damage half of `joint_pick`'s product: around no weapon, the mean over weapon classes of
    the best usable hit over its capped hit (`AffinityKit`); around a weapon, its hit over its own
    best. The kit's share is not in it: every spread `joint_pick` compares already maximises it."""
    if "affinity_share" in spread:
        return spread["affinity_share"]
    return spread["focus_hit"] / spread["focus_best"]


def joint_pick(vigs, ends, damage_at, options_at, survive, cache: dict | None = None) -> dict:
    """Vigor, Endurance and the armor set chosen together (user, 2026-10-18: "the builds are very
    endurance heavy compared to the average, and we aren't hitting STR breakpoint. We probably also
    need to consider effective HP"), by damage x HP / mean hit taken.

    The product is the duel's own exchange: the fight goes to whoever needs fewer hits, my hits to
    kill them scale with 1 / my damage and their hits to kill me with my HP / their mean hit on me,
    so one per cent of damage is worth one per cent of effective HP and no weight is chosen by hand.

    `damage_at(vig, end)` is (damage, spread) for the spread with those two pinned, or None; the
    damage stats depend only on how many points Vigor and Endurance leave, so it is asked once per
    sum. `options_at(end)` is the armor sets that keep medium roll at that Endurance, each a dict
    with `floored` (it meets the poise floor) and `wear` (the HP piece it carries, or None).
    `survive(stats, option)` is (max HP, mean pool hit taken).

    Returns `best` (floored sets only), `any_poise` (the best ignoring the floor, for reporting what
    the floor costs) and `by_wear` (the best floored candidate per HP piece, None for none), each
    {vig, end, damage, spread, stats, option, hp, hit, ehp, value}, or None where nothing fits.

    `cache` ({vig + end: damage_at result}) is shared between calls that differ only in what the
    armor budget carries (`weight_cost`): the damage stats do not see the weapons' weight. It also
    keeps `survive`'s answers under ("survive", vig, end, pieces): the stats are the cached spread's
    with that Vigor and Endurance, and a set's absorption is its pieces', so the carried weight only
    changes which sets are offered, never what one is worth."""
    dmg = {} if cache is None else cache
    best, free, by_wear = None, None, {}
    for e in ends:
        opts = options_at(e)
        if not opts:
            continue
        for v in vigs:
            if v + e not in dmg:
                dmg[v + e] = damage_at(v, e)
            got = dmg[v + e]
            if got is None:
                continue
            d, sp = got
            if sp["stats"]["vig"] + sp["stats"]["end"] != v + e:
                continue  # points no damage stat wanted went to survival, so the split is not free
            st = dict(sp["stats"], vig=v, end=e)
            for o in opts:
                sk = ("survive", v, e, tuple(o["pieces"]))
                if sk not in dmg:
                    dmg[sk] = survive(st, o)
                hp, hit = dmg[sk]
                c = {"vig": v, "end": e, "damage": d, "spread": sp, "stats": st, "option": o, "hp": hp,
                     "hit": hit, "ehp": hp / hit, "value": d * hp / hit}
                if free is None or c["value"] > free["value"]:
                    free = c
                if not o["floored"]:
                    continue
                if best is None or c["value"] > best["value"]:
                    best = c
                w = o.get("wear")
                if w not in by_wear or c["value"] > by_wear[w]["value"]:
                    by_wear[w] = c
    return {"best": best, "any_poise": free, "by_wear": by_wear}


#: Carried weights `weight_cost` prices, from 0 in `WEIGHT_STEP` steps up to `WEIGHT_MAX` or the
#: first weight no floor-poise set fits at medium roll. Two of the heaviest colossal weapons are
#: about 50; the loadout interpolates between the points (`er-mechanics-combo-loadout.WeightCost`).
WEIGHT_STEP, WEIGHT_MAX = 1.0, 50.0


def weight_cost(weights, value_at) -> dict:
    """{weights, value, raw}: `value_at(w)` (`joint_pick`'s best damage x EHP with `w` of weapons on
    the medium-roll budget, None where nothing fits) at each of `weights`, and each over the best of
    them. The walk stops at the first weight past a fitting one that fits nothing, since heavier
    only shrinks the budget further; a weight that fits nothing is 0. Not forced monotone: in a duel
    a light carry can be worth less than a heavier one, because light roll is banned and the armor
    has to make the weight up."""
    raw, fitted = [], False
    for w in weights:
        v = value_at(w)
        if v is None and fitted:
            break
        fitted = fitted or v is not None
        raw.append((w, v or 0.0))
    top = max((v for _, v in raw), default=0.0)
    return {"weights": [w for w, _ in raw], "raw": [round(v, 5) for _, v in raw],
            "value": [round(v / top, 5) if top else 0.0 for _, v in raw]}


def corpus_survival(rows: list[dict], arch: str | None = None) -> dict:
    """Median and mean Vigor and Endurance of the window's PvP builds (`er-builds-optimize.corpus_rows`
    rows), all of them and those of `arch` (`er-builds-optimize.archetype`), for the report."""
    out = {}
    pvp = [r for r in rows if r["pvp"]]
    for name, rs in (("pvp", pvp), (arch, [r for r in pvp if r["archetype"] == arch])):
        if name and rs:
            out[name] = {"builds": len(rs), **{f"{k}_{f.__name__}": round(f([r["stats"][k] for r in rs]), 1)
                                              for k in ("vig", "end") for f in (np.median, np.mean)}}
    return out


#: A grease sweep row's configurations (`er-builds-optimize._grease_one`): the best ungreased
#: affinity, the best greased one, greased Quality.
SWEEP_KEYS = ("elemental", "greased", "quality")


def fight_grease(fight: str) -> tuple:
    """(sweep row keys, `weapon_best` grease tier, sweep tier) a fight may assume. A duel assumes
    no grease: the ladder bans every grease that adds attack (`er-builds-ladder.grease_ban`, the
    planner site's "All Damage Greases"), and the status greases it leaves legal add none for the
    stat optimizer to score. So it reads the no-grease sweep (`--grease none`), whose `elemental`
    is the best ungreased build over every affinity, Standard, Heavy, Keen and Quality included.
    Elsewhere, the optimizer's default tier and its sweep."""
    if fight == "duel":
        return ("elemental",), None, "none"
    return SWEEP_KEYS, "dlc-drawstring", "dlc-drawstring"


class Kit:
    """The weapons a build can carry besides its focus, by weapon class, from the every-class
    grease sweep at one RL (`er-mechanics-infusions.load_sweep`), each by its best configuration
    among `keys` (`fight_grease`)."""

    def __init__(self, opt, rows, tables, keys=SWEEP_KEYS):
        best = {}
        for row in rows:
            d = max((row[k]["dmg"] for k in keys if row.get(k)), default=None)
            if d is not None:
                wid = tables.find_weapon(row["weapon"])
                best[wid] = max(best.get(wid, 0.0), d)
        self.classes = sorted({tables.weapons[w]["wepType"] for w in best})
        need1, need2, share, cls = [], [], [], []
        top = {c: max(d for w, d in best.items() if tables.weapons[w]["wepType"] == c) for c in self.classes}
        for wid, d in sorted(best.items()):
            # The requirement fields, not `opt.requirements`: that asks the damage calculator, which
            # never sees a requirement in a stat the weapon does not scale with (59 of 480 weapons,
            # Smithscript Shield's INT and FTH 11 among them), and the user's definitions are about
            # the requirements the menu shows.
            row = tables.weapons[wid]
            need1.append([row[REQUIREMENT_FIELDS[k]] for k in opt.DAMAGE_STATS])
            # Two-handing counts STR as floor(STR x 1.5), so it is met from ceil(requirement / 1.5).
            need2.append(-(-2 * row[REQUIREMENT_FIELDS["str"]] // 3))
            share.append(d / top[tables.weapons[wid]["wepType"]])
            cls.append(self.classes.index(tables.weapons[wid]["wepType"]))
        self.stats = opt.DAMAGE_STATS
        assert self.stats[0] == "str", "usable() reads column 0 as STR"
        self.need1, self.need2 = np.array(need1), np.array(need2)
        self.share, self.cls = np.array(share), np.array(cls)

    def usable(self, st: dict):
        """(wieldable, semi-wieldable) masks over the kit's weapons."""
        have = np.array([st[k] for k in self.stats])
        met = self.need1 <= have
        wield = met.all(axis=1)
        semi = ~wield & met[:, 1:].all(axis=1) & (self.need2 <= st["str"])
        return wield, semi

    def value(self, st: dict) -> float:
        """The mean over classes of the best usable weapon's damage over the class's best."""
        wield, semi = self.usable(st)
        ok = wield | semi
        per = np.zeros(len(self.classes))
        np.maximum.at(per, self.cls[ok], self.share[ok])
        return float(per.mean())


class KitScorer:
    """`spend`'s scorer for a whole build: the focus weapon's hit over its best, plus the kit's
    value, one equal part each. No cap: a stat past the focus weapon's last soft cap can still open
    weapons, and the focus term is what makes it cost something."""

    def __init__(self, focus, focus_best: float, kit: Kit):
        self.focus, self.focus_best, self.kit = focus, focus_best, kit
        self.cap, self.floor = {}, {}

    def score(self, st: dict) -> float:
        # Of the damage stats alone (`opt.Scorer` and `Kit` read nothing else), so memoised on
        # them: `spend` asks for the same blocks over and over from every Vigor and Endurance.
        key = tuple(st[k] for k in self.kit.stats)
        memo = self.__dict__.setdefault("_memo", {})
        if key not in memo:
            memo[key] = self.focus.score(st) / self.focus_best + self.kit.value(st)
        return memo[key]


class AffinityKit:
    """The scorer of a build made around no weapon (user, 2026-10-18: "aren't we just going for
    highest Heavy breakpoint?"): per weapon class, the best usable weapon's hit at one affinity
    over that weapon's hit with every damage stat at the cap, the mean over classes, plus `Kit`'s
    share. Each class is judged on its `per_class` hardest-hitting weapons of the sweep at that
    affinity, which keeps a spend to seconds."""

    def __init__(self, opt, tables, sweep, kit: Kit, affinity: str, defender: dict, per_class: int = 3):
        self.kit, self.affinity, self.cap, self.floor = kit, affinity, {}, {}
        by_cls = {}
        for row in sweep:
            try:
                wid = tables.find_weapon(row["weapon"], affinity)
            except SystemExit:
                continue
            row_ = tables.weapons[wid]
            level = tables.max_level(row_["reinforceTypeId"])
            sc = opt.Scorer(tables, row["weapon"], affinity, level, False, "damage", defender)
            top = sc.score({k: opt.STAT_CAP for k in opt.DAMAGE_STATS})
            if top <= 0:
                continue
            need = {k: row_[REQUIREMENT_FIELDS[k]] for k in opt.DAMAGE_STATS}
            by_cls.setdefault(row_["wepType"], []).append((top, row["weapon"], level, sc, need))
        self.classes = {c: sorted(xs, key=lambda x: -x[0])[:per_class] for c, xs in by_cls.items()}

    def usable(self, need: dict, st: dict) -> bool:
        return all(st[k] >= v for k, v in need.items())

    def hits(self, st: dict) -> dict:
        """{class: (share, weapon)} of each class's best usable weapon at `st`."""
        out = {}
        for c, xs in self.classes.items():
            got = [(sc.score(st) / top, w) for top, w, _, sc, need in xs if self.usable(need, st)]
            if got:
                out[c] = max(got)
        return out

    def score(self, st: dict) -> float:
        # Of the damage stats alone (each `opt.Scorer`, `usable`'s requirements and `Kit` read
        # nothing else), so memoised on them: `joint_pick` respreads once per Vigor + Endurance and
        # every `spend` walks the same damage-stat blocks, 45% of a dry run's time before
        # (2026-10-20 heavy kit).
        key = tuple(st[k] for k in self.kit.stats)
        memo = self.__dict__.setdefault("_memo", {})
        if key not in memo:
            memo[key] = sum(s for s, _ in self.hits(st).values()) / len(self.classes) + self.kit.value(st)
        return memo[key]

    def lead(self, st: dict) -> tuple[str, int]:
        """The hardest-hitting usable weapon at `st` and its level: what talismans, tears and the
        pouch are scored around, since they each take one weapon."""
        best = max(((sc.score(st), w, lv) for xs in self.classes.values() for _, w, lv, sc, need in xs
                    if self.usable(need, st)), default=None)
        return (best[1], best[2]) if best else (None, 0)


_RES_MODELS = {}


def res_model(opt):
    """One `er-mechanics-resources.Model` per resources module, built on first use. Building one
    reads its five params out of the regulation, and `joint_pick` asks for a spread (`respread`)
    once per Vigor + Endurance and carried weight: on the 2026-10-20 heavy kit that rebuilt it for
    about a quarter of the dry run's time. A built model is never written to ("every query is pure
    arithmetic after that")."""
    key = id(opt.RES)
    if key not in _RES_MODELS:
        _RES_MODELS[key] = opt.RES.Model()
    return _RES_MODELS[key]


def affinity_spread(opt, rl: int, search: dict, scorer: AffinityKit, pins=None, classes=None) -> dict | None:
    """`kit_spread` for a build made around no weapon: the spread, over every starting class (or
    `classes`), that maximises `AffinityKit` from `search`'s floors (with `pins` fixed)."""
    model = res_model(opt)
    floor = dict(search["floors"], **(pins or {}))
    top = None
    for cls in classes or opt.RES.CLASS_ROWS:
        cls_level, base = model.class_base(cls)
        if rl < cls_level:
            continue
        start = {k: max(base[k], floor.get(k, 0)) for k in opt.STATS}
        spent = opt.spend_floored(start, rl, scorer, search["pool"], pinned=set(pins or ()))
        if spent is None:
            continue
        st, fl = spent
        value = scorer.score(st)
        if top is None or value > top["value"]:
            wield, semi = scorer.kit.usable(st)
            hits = scorer.hits(st)
            top = {"class": cls, "stats": st, "floors": fl, "value": round(value, 4),
                   "affinity_share": round(sum(s for s, _ in hits.values()) / len(scorer.classes), 4),
                   "class_leads": {str(c): [w, round(s, 3)] for c, (s, w) in hits.items()},
                   "kit": round(scorer.kit.value(st), 4), "wieldable": int(wield.sum()),
                   "semi_wieldable": int(semi.sum()), "kit_weapons": len(scorer.kit.share)}
    return top


def kit_spread(opt, tables, weapon: str, two_handed: bool, rl: int, search: dict, kit: Kit, pins=None,
               classes=None) -> dict | None:
    """The spread, over every starting class (or `classes`), that maximises `KitScorer` with the
    focus weapon at `search`'s best affinity, grease and level, its requirements met, and
    `search`'s floors (with `pins` fixed)."""
    best = search["results"][0]
    model = res_model(opt)
    focus = opt.Scorer(tables, weapon, best["affinity"], best["level"], two_handed, "damage", search["defender"],
                       best["grease"])
    need = opt.requirements(tables, weapon, best["affinity"], best["level"], two_handed)
    # The weapon itself is wieldable in the user's sense too: every requirement field met, STR
    # through two-handing only when the build two-hands it.
    row = tables.weapons[tables.find_weapon(weapon, best["affinity"])]
    for k, field in REQUIREMENT_FIELDS.items():
        req = -(-2 * row[field] // 3) if k == "str" and two_handed else row[field]
        need[k] = max(need.get(k, 0), req)
    floor = dict(search["floors"], **(pins or {}))
    top = None
    for cls in classes or opt.RES.CLASS_ROWS:
        cls_level, base = model.class_base(cls)
        if rl < cls_level:
            continue
        start = {k: max(base[k], floor.get(k, 0), need.get(k, 0)) for k in opt.STATS}
        scorer = KitScorer(focus, best["score"], kit)
        spent = opt.spend_floored(start, rl, scorer, search["pool"], pinned=set(pins or ()))
        if spent is None:
            continue
        st, fl = spent
        value = scorer.score(st)
        if top is None or value > top["value"]:
            wield, semi = kit.usable(st)
            top = {"class": cls, "stats": st, "floors": fl, "value": round(value, 4),
                   "focus_hit": round(focus.score(st), 1), "focus_best": round(best["score"], 1),
                   "kit": round(kit.value(st), 4), "wieldable": int(wield.sum()), "semi_wieldable": int(semi.sum()),
                   "kit_weapons": len(kit.share)}
    return top


def heaviest_set(plan: dict, tables) -> dict:
    """The plan's equip set whose weapons weigh most, both hands and their swaps
    (`er-mechanics-combo-loadout.py` writes `sets` as slot indices per hand). A covering set
    (`cover`: there so every plan weapon is in one set, `er-mechanics-combo-loadout.cover_sets`) is
    left out: the loadout already charged it `WeightCost` above the heaviest of the others, and
    budgeting the stats for a two-handed colossal weapon's own set would re-buy them for a set that
    only exists because the plan holds a copy of it."""
    top = None
    for s in plan["sets"]:
        if s.get("cover"):
            continue
        slots = [plan["slots"][i] for i in s["right"] + s["left"]]
        weight = sum(tables.weapons[tables.find_weapon(x["weapon"], x["affinity"])]["weight"] for x in slots)
        if top is None or weight > top["weight"]:
            top = {"name": s["name"], "weapons": [x["weapon"] for x in slots], "weight": round(weight, 1)}
    return top


def holds_weapon(plan: dict, weapon: str) -> bool:
    """Whether an equip set of the plan carries `weapon`, in either hand. A plan without it is not
    a build around it: on 2026-10-14 Poleblade of the Bud's only skill was dropped by the sweep and
    the upload went out with six sets and no Poleblade in any of them."""
    return any(plan["slots"][i]["weapon"] == weapon for s in plan["sets"] for i in s["right"] + s["left"])


def fetch_build(build_id: str) -> dict:
    up = _mod("planner_ash_loadout_upload", "planner-ash-loadout-upload.py")
    doc = up.request(f"{up.API}/{build_id}")
    for key in up.SERVER_FIELDS:
        doc.pop(key, None)
    return doc


def spell_rule(spell: str, weapon: str, stats: dict) -> dict:
    """`er-mechanics-cast-speed.best_loadout` for `spell`: the off-hand and talismans that most raise
    damage x startup speed / damage taken, cast from `weapon` when it can cast the spell."""
    cs = _mod("er_mechanics_cast_speed", "er-mechanics-cast-speed.py")
    m = cs.CastModel()
    sp = m.spell(spell)
    try:
        catalyst = weapon if m.can_cast(sp, weapon) else None
    except KeyError:
        catalyst = None
    short = {k: v for k, v in sp["requires"].items() if stats.get(k, 0) < v}
    if short:
        say(f"{sp['name']} needs {', '.join(f'{k.upper()} {v}' for k, v in short.items())}, over these stats",
            " ", YELLOW)
    rule = m.best_loadout(sp, stats, catalyst, slots=TALISMAN_SLOTS)
    say(f"{sp['name']} from {catalyst or 'no catalyst held'}: off-hand {rule['left'] or 'nothing that helps'}, "
        f"talismans {', '.join(rule['talismans']) or 'none'}: damage x{rule['damage']:g}, "
        f"{rule['frames_saved']:g} of {m.startup(sp, 1.0) - m.startup(sp, 0.0):g} start frames saved, "
        f"taken x{rule['incoming']:g}")
    return rule


#: Weapon slots on one inventory page (`er-mechanics-combo-loadout.PAGE`).
PAGE_SLOTS = 30
#: An invader build's most rows of shields spread through its pages as markers (user, 2026-10-18:
#: "1-3 rows of icon shields interspersed in the pages"); a ceiling, not a count: the loadout
#: places a row only where the shield beats the row it displaces over the invasion fight model
#: (`er-mechanics-combo-loadout.icon_shield_plan`).
ICON_SHIELD_ROWS = 3
#: Plan themes a duel page fills from after the equip sets' own copies, best first. Landmark
#: blocks (one weapon fifteen times), Fun and Fill copies are for browsing, not a duel; the
#: Classes, AOE and Status rows are variety, which a page has no room for, so each weapon class
#: gets its main-hand pick instead (`class_leads`).
DUEL_THEMES = ("Focus", "Crit", "Shields", "Ranged", "Quick", "Combos")
#: The farthest two duelists get apart, m (user, 2026-10-18: "Lets assume that in duels, the arena
#: sizes are 16.9m"); `er-mechanics-combo-loadout.py --arena-m`.
DUEL_ARENA_M = 16.9


def main_hand_pool(roles: dict, cr) -> tuple[dict, list[dict], dict]:
    """(`[class_pick_weights]`, every main-hand candidate's features, {(weapon, affinity): features})
    of an `er-mechanics-class-roles.py` roles file: the pool one cross-class score normalises over."""
    # The roles file's own weights: its crit weight follows the ruleset's legal parries (`cr.pick_weights`).
    weights = roles.get("weights") or cr.pick_weights()
    cands = [(w, aff, v["features"]) for w, rw in roles["weapons"].items()
             for aff, v in ((rw.get("main_hand") or {}).get("by_affinity") or {}).items()]
    return weights, [f for _, _, f in cands], {(w, aff): f for w, aff, f in cands}


def roles_lead(leads: list[dict], affinity: str | None = None, banned=lambda weapon: None) -> dict | None:
    """The weapon the gear is scored around once a pass's roles exist: the first of `leads`
    (`class_leads`, best first) at `affinity` when one is pinned, that `banned` gives no reason
    for. User, 2026-10-18, of the hardest hitter Prelate's Inferno Crozier: "a terrible weapon to
    build around"; the cross-class main-hand ranking already weighs moveset, reach and frames."""
    return next((x for x in leads if (not affinity or x["affinity"] == affinity) and not banned(x["weapon"])), None)


def kit_lead(hits: dict, roles: dict, cr, affinity: str) -> str | None:
    """Before this run's roles exist: of `AffinityKit.hits` ({class: (share, weapon)}), the weapon
    with the best cross-class `[class_pick_weights]` score in `roles` (an earlier pass's file; the
    moveset features do not move with the stats), or None when `roles` scores none of them."""
    weights, pool, feats = main_hand_pool(roles, cr)
    scored = [(cr.norm_score(feats[w, affinity], pool, cr.FEATURES, weights), w)
              for _, w in hits.values() if (w, affinity) in feats]
    return max(scored)[1] if scored else None


def class_leads(roles: dict, cr) -> list[dict]:
    """Each weapon class's main-hand pick (`er-mechanics-class-roles.py`: moveset, reach, frames,
    AR at the build's stats, ash value, crit, parry and perfect-block costs, weighted by
    `[class_pick_weights]`), best first by the same weighted score taken over every class's
    candidates at once, so classes compare on one scale (user, 2026-10-18: "we don't need a variety
    of weapons if a good one exists"). `cr` is the class-roles module."""
    weights, pool, feats = main_hand_pool(roles, cr)
    out = []
    for c, r in roles["classes"].items():
        mh = r.get("main_hand")
        if mh and (mh["weapon"], mh["affinity"]) in feats:
            out.append({"class": c, "weapon": mh["weapon"], "affinity": mh["affinity"], "class_score": mh["score"],
                        "score": round(cr.norm_score(feats[mh["weapon"], mh["affinity"]], pool, cr.FEATURES,
                                                     weights), 4)})
    return sorted(out, key=lambda x: -x["score"])


def duel_page(plan: dict, slots: int = PAGE_SLOTS, drop=lambda slot: False, leads=(),
              cls=lambda weapon: weapon) -> dict:
    """The plan cut to one page for a duelist (user, 2026-10-18: "multiple pages, and has multiple
    items repeated. It is unsuitable for a duelist"): every copy an equip set wears, then one copy
    of each weapon not yet on the page, by `DUEL_THEMES` order and plan order within a theme, then
    `leads` (`class_leads`) in order, each class whose weapon class (`cls`) is not on the page yet
    taking its pick's best plan copy, up to `slots`. The sets are renumbered onto the kept copies;
    a set whose copies do not all fit is dropped.

    Covering sets (`cover`, `er-mechanics-combo-loadout.cover_sets`) are not worn first: once the
    page's weapons are chosen, each weapon on it brings the set it is the best set of (`covers`),
    and the page grows past `slots` by whatever partner copies those sets need (user, 2026-10-19:
    loosen the item cap when that lets each weapon reach its best set). A weapon then on the page
    in a set keeps that copy only. Set copies come in plan order, where the loadout grouped each
    weapon beside its partners (`arrange_sets`). `page_growth` says how far past `slots` it went."""
    worn = [i for s in plan["sets"] if not s.get("cover") for i in s["right"] + s["left"]]
    keep = list(dict.fromkeys(worn))
    have = {plan["slots"][i]["weapon"] for i in keep}

    def theme(s):
        return s.get("theme", "").split(" (")[0]

    for t in DUEL_THEMES:
        mine = [i for i, s in enumerate(plan["slots"]) if theme(s) == t and not drop(s)]
        # A theme whose copies carry a value (Ranged, Quick, Combos) puts only its best weapon
        # on the page: one duel uses one bow at a time, so a second ranged weapon's value on top
        # of the first is nothing it adds (user, 2026-10-19: "you have more than one bow and
        # crossbow???? WHYYYY?"; seven ranged copies made the page). A theme without values
        # (a shield per job) keeps one copy of each weapon as before.
        if any(plan["slots"][i].get("value") for i in mine):
            mine = sorted(mine, key=lambda i: -(plan["slots"][i].get("value") or 0))[:1]
        for i in mine:
            s = plan["slots"][i]
            if len(keep) >= slots:
                break
            if s["weapon"] not in have:
                keep.append(i)
                have.add(s["weapon"])
    on_page = {cls(w) for w in have}
    for lead in leads:
        if len(keep) >= slots:
            break
        w = lead["weapon"]
        if cls(w) in on_page or w in have:
            continue
        copies = [i for i, s in enumerate(plan["slots"]) if s["weapon"] == w and not drop(s)]
        if not copies:
            continue
        mine = [i for i in copies if plan["slots"][i]["affinity"] == lead["affinity"]] or copies
        keep.append(max(mine, key=lambda i: plan["slots"][i].get("value", 0)))
        have.add(w)
        on_page.add(cls(w))
    keep = keep[:slots]
    page_ws = {plan["slots"][i]["weapon"] for i in keep}
    chosen = [s for s in plan["sets"] if not s.get("cover") and all(i in keep for i in s["right"] + s["left"])]
    chosen += [s for s in plan["sets"] if s.get("cover") and set(s.get("covers") or ()) & page_ws]
    in_sets = sorted({i for s in chosen for i in s["right"] + s["left"]})
    set_ws = {plan["slots"][i]["weapon"] for i in in_sets}
    rest = [i for i in keep if i not in in_sets and plan["slots"][i]["weapon"] not in set_ws]
    keep = in_sets + rest if any(s.get("cover") for s in chosen) else keep
    new = {old: n for n, old in enumerate(keep)}
    sets = [{**s, "right": [new[i] for i in s["right"]], "left": [new[i] for i in s["left"]]}
            for s in plan["sets"] if all(i in new for i in s["right"] + s["left"])]
    return {**plan, "slots": [plan["slots"][i] for i in keep], "sets": sets,
            "page_growth": {"cap": slots, "added": max(0, len(keep) - slots)}}


def ladder_bans():
    """`er-builds-ladder.py`: the duel ladder's ban tables and one `*_ban(name)` predicate per kind
    of item. `--fight duel` passes them to every picker, so nothing banned is ever a candidate."""
    return _mod("er_builds_ladder", "er-builds-ladder.py")


def duel_buff_ban(bans, focus: str, two_handed: bool, plan: dict):
    """`er-mechanics-pouch.pick`'s `buff_ban` for a duel: why a grease or weapon-buff spell is
    banned in the focus weapon's setup, or None. The focus is the main-hand weapon in its grip; the
    off hand is every left-hand weapon of a plan set whose right hand holds the focus (every
    left-hand weapon of the plan when no set does). A grease is judged by
    `bans.grease_setup_ban` (status rules and buff restrictions), a spell by `bans.buff_setup_ban`."""
    slots, sets = plan.get("slots") or [], plan.get("sets") or []
    grip = "2H" if two_handed else "1H"
    mine = [s for s in sets if any(slots[i]["weapon"] == focus for i in s.get("right", ()) if i < len(slots))]
    off = [(slots[i]["weapon"], slots[i].get("grip", "1H")) for s in (mine or sets) for i in s.get("left", ())
           if i < len(slots)]
    setups = [(focus, grip, "main")] + [(w, g, "off") for w, g in dict.fromkeys(off)]

    def ban(name, kind):
        for w, g, hand in setups:
            why = bans.buff_setup_ban(w, g, hand) if kind == "spell" else bans.grease_setup_ban(name, w, g, hand)
            if why:
                return why
        return None
    return ban


_WEAPON_BUFF = {}


def weapon_buff_ash(ash: str | None, weapon: str, grip: str) -> bool:
    """Whether skill `ash` on `weapon` in `grip` buffs the weapon: a SpEffect its paid-for animation
    puts on the caster (`er-mechanics-ashes.skill_buffs`, effect "buff") that is, or links to, a
    weapon-buff row (`spCategory` in `er-mechanics-buffs.WEAPON_BUFF_CATS`: War Cry 1810 -> 162,
    Lightning Slash 1675 -> 162). A body buff (Golden Vow 1730 -> 160) is not one. A skill the
    tables cannot profile counts as no buff."""
    key = (ash, weapon, grip)
    if key not in _WEAPON_BUFF:
        A = _mod("er_mechanics_ashes", "er-mechanics-ashes.py")
        cats = _mod("er_mechanics_buffs", "er-mechanics-buffs.py").WEAPON_BUFF_CATS
        t = _WEAPON_BUFF.get("tables") or _WEAPON_BUFF.setdefault("tables", A.AshTables())
        sp = t.speffect

        def weapon_row(sid, seen):
            if sid in seen or sid not in sp:
                return False
            seen.add(sid)
            return sp[sid].get("spCategory") in cats or any(
                weapon_row(sp[sid].get(k, -1), seen) for k in A.SPEFFECT_LINKS if (sp[sid].get(k) or -1) > 0)
        try:
            prof = A.skill_profile(t, t.find_arts(ash), t.find_weapon(weapon), 0, grip == "2H") if ash else None
        except (SystemExit, KeyError, StopIteration, TypeError):
            prof = None
        _WEAPON_BUFF[key] = bool(prof) and any(
            s.get("effect") == "buff" and weapon_row(s["id"], set()) for s in A.skill_buffs(t, prof))
    return _WEAPON_BUFF[key]


def duel_buff_ash_bans(bans, plan: dict, is_buff=weapon_buff_ash) -> dict:
    """{slot index: why} for every copy of `plan` whose skill buffs the weapon (`is_buff`) in a
    setup the ladder bans weapon buffs in (`bans.buff_setup_ban`, `10-3 [ BUFF RESTRICTIONS ]`: "grease,
    buff spell, buff skill"). A copy is judged in every set it is in, the way `duel_buff_ban` judges
    a grease: the set's right-hand copies as main hand in the copy's grip (and 1H too when the set
    holds a left weapon), its left-hand copies as off hand, and any banned setup in the set bans the
    buff on each of its copies (an off-hand dagger bans a buff on the right weapon too). A copy in no
    set is its own main-hand setup."""
    slots, sets = plan.get("slots") or [], plan.get("sets") or []
    groups = list(sets)
    in_set = {i for s in groups for i in s.get("right", []) + s.get("left", [])}
    groups = groups + [{"right": [i], "left": []} for i in range(len(slots)) if i not in in_set]
    out = {}
    for s in groups:
        right = [i for i in s.get("right", ()) if i < len(slots)]
        left = [i for i in s.get("left", ()) if i < len(slots)]
        setups = [(slots[i]["weapon"], g, "main") for i in right
                  for g in dict.fromkeys([slots[i].get("grip", "1H")] + (["1H"] if left else []))]
        setups += [(slots[i]["weapon"], "1H", "off") for i in left]
        why = next((r for w, g, h in setups if (r := bans.buff_setup_ban(w, g, h))), None)
        if not why:
            continue
        for i in right + left:
            x = slots[i]
            if i not in out and is_buff(x.get("ash"), x["weapon"], x.get("grip", "1H")):
                out[i] = f"{x.get('ash')} buffs the weapon: {why}"
    return out


def refuse_slots(plan: dict, bad) -> dict:
    """`plan` without the copies `bad` names and without every set that wears one; the remaining
    sets are renumbered onto the kept copies."""
    keep = [i for i in range(len(plan["slots"])) if i not in bad]
    new = {old: n for n, old in enumerate(keep)}
    sets = [{**s, "right": [new[i] for i in s["right"]], "left": [new[i] for i in s["left"]]}
            for s in plan["sets"] if all(i in new for i in s["right"] + s["left"])]
    return {**plan, "slots": [plan["slots"][i] for i in keep], "sets": sets}


def loadout_exclude(bans, talismans) -> dict:
    """`er-mechanics-combo-loadout.py --exclude` for a duel: the banned weapons and skills, Chilling
    Mist on Cold, and Muleta when a picked talisman raises ash damage."""
    ashes = set(bans.ASHES) | ({"Muleta"} if bans.muleta_ban(talismans) else set())
    return {"weapons": sorted(bans.WEAPONS | bans.REGEN | set(bans.class_bans())), "ashes": sorted(ashes),
            "rows": [list(r) for r in bans.ROW_BANS],
            "ash_affinity": [["Chilling Mist", "Cold"]]}


def talisman_spares(talismans: list[str], duel: bool = True) -> list[str]:
    """The medallions carried unequipped for Ritual Shield swaps (`DUEL_MEDALLION_SWAPS`): in a
    duel that wears Crimson Amber +3 beside Ritual Shield, otherwise none. Not for an invader:
    invaders wear +3 but rarely carry the lower ones, since a swap mid-fight risks taking a hit
    (user, 2026-10-18)."""
    return list(DUEL_MEDALLION_SWAPS) if duel and set(DUEL_TALISMANS[:2]) <= set(talismans) else []


def spent_by_self_damage(plan: dict, talismans: list[str]) -> dict:
    """{talisman: [skills]}: the worn talismans that a copy of the plan, worth something after its
    own cost, leaves worth nothing by taking HP from its user (`self_cost['spent']`,
    `er-mechanics-self-damage.spent_talismans`: a full-HP row such as Ritual Shield's). The copy's
    value already carries the talisman's loss (`er-mechanics-combo-loadout.set_self_costs`), so a
    copy still worth carrying is one the build would use, and the talisman is then worth nothing
    in its slot."""
    out = {}
    for s in plan.get("slots") or []:
        c = s.get("self_cost") or {}
        if s.get("value", 0) > 0:
            for n in c.get("spent") or ():
                if n in talismans and s.get("skill") not in out.setdefault(n, []):
                    out[n].append(s.get("skill"))
    return {n: v for n, v in out.items() if v}


def pick_talismans(opt, weapon: str, affinity: str, first: list[str] = (), banned=lambda name: None) -> list[str]:
    """`first` (the spell rule's picks) take their slots before the weapon's own ranking; a name
    `banned` gives a reason for is never picked."""
    syn = _mod("er_mechanics_gear_synergy", "er-mechanics-gear-synergy.py")
    d, pop, gear, tables = syn.build()
    ranked = list(first) + [r["name"] for r in syn.rank(d, pop, gear, tables, weapon, top=100)["gear"]
                            if r["kind"] == "talisman"]
    fallback = opt.gear(weapon, affinity, opt.CACHE / "embeddings.npz", FALLBACK_TALISMANS).get("talisman", [])

    def group_of(name):
        item = gear.items[name]
        if item["kind"] != "talisman" or banned(name):
            raise KeyError(name)
        g = item.get("group")
        return g if g and g > 0 else None

    return fill_talismans(ranked, fallback, group_of)


# --------------------------------------------------------------------------------------------
# Running on another host
#
# `--host zerk` runs this same command on zerk. The SSH plumbing, the venv and the grant come from
# `gen-r3-weapon-boards.py` (`boards()`), which runs its shards there the same way. The two
# machines share no files: each has its own /home on its own disk, and zerk's checkout is a copy,
# not a git repository (measured 2026-10-19). Only the paths match (`remote_python` refuses a host
# whose home differs), so every input is copied to its own path there first and the run's files
# are copied back to theirs after.

REPO_ROOT = HERE.parent
#: `er-builds-optimize.CACHE`, spelled here so a remote run can name its paths without loading the
#: optimizer; the selftest holds the two equal.
PLANNER_CACHE = Path.home() / ".cache" / "er-build-planner"
#: Where the build runs when `--host` is not given: `local`, or an SSH host name.
DEFAULT_HOST = os.environ.get("ER_WEAPON_BUILD_HOST", "local")
#: Repository trees the run reads, each mirrored with `--delete`: the scripts, `data/` (the ash
#: blacklist and preferences, `true-combos.toml`, `ranged-picks.toml`, `planner-item-names.json`)
#: and the duel tables `er-builds-ladder.py` and `er-mechanics-class-roles.py` parse out of the
#: Rust sources.
REPO_TREES = ("scripts", "data", "crates/er-duel-rules-core/src")
_WITCHY = "er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded"
_ALL_ASSETS = "er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script"
#: Inputs outside the repository, as the scripts that read them name them: (reader script, the
#: environment variable that overrides the path or None, the default path under the home
#: directory, a glob of the files read under it or None for one file, text the reader's source
#: holds when the default is current or None for the path itself). The selftest checks every row
#: against its reader, so a renamed extract or variable fails there rather than on the host.
#: Left out: `eldenring-deobf*.bin` (190 MB), which only the readers' selftests open, and the game
#: install, which the host has at the same Steam path.
HOME_INPUTS = (
    ("er-mechanics-attacks.py", "ER_PLAYER_TAE_DIR",
     f"{_WITCHY}/chr/c0000-anibnd-dcx/INTERROOT_win64/chr/c0000/tae", "*.tae", None),
    # `hkx_path` indexes every `.hkx` under each `c0000_*` shard; nothing else of the 11 GB tree.
    ("er-mechanics-attacks.py", "ER_PLAYER_HKX_ROOT", f"{_WITCHY}/chr", "c0000_*/**/*.hkx", None),
    ("er-mechanics-attacks.py", "ER_TAE_TEMPLATE_ER",
     "projects/WitchyBND/WitchyBND/Assets/Templates/TAE.Template.ER.xml", None, None),
    ("er-hkx-pose.py", "ER_PLAYER_SKELETON_HKX",
     f"{_WITCHY}/chr/c0000-anibnd-dcx/INTERROOT_win64/chr/c0000/hkx/Skeleton.hkx", None, None),
    ("er-hkx-pose.py", "ER_PLAYER_RAGDOLL_HKX", f"{_WITCHY}/chr/c0000-chrbnd-dcx/c0000.HKX", None, None),
    ("er-hkx-pose.py", "ER_PLAYER_HKXPWV", f"{_WITCHY}/chr/c0000-chrbnd-dcx/c0000.hkxpwv", None, "c0000.hkxpwv"),
    ("er-mechanics-reach.py", "ER_PLAYER_FLVER", f"{_WITCHY}/chr/c0000-chrbnd-dcx/c0000.flver", None, None),
    # `weapon_flver_path` opens only `WP_A_<model>.flver` out of each `wp_a_*` binder (16 GB tree).
    ("er-mechanics-reach.py", "ER_PARTS_ROOT", f"{_WITCHY}/parts", "**/wp_a_*-partsbnd-dcx/WP_A_*.flver", None),
    ("er-mechanics-reach.py", "ER_SMITHBOX_PARAM_DIR", ".local/share/smithbox/app/Assets/PARAM/ER", "**/*", None),
    ("er-param-read.py", "ER_PARAMDEF_DIR", ".local/share/smithbox/app/Assets/PARAM/ER/Defs", "**/*", None),
    ("er-param-read.py", "ER_PARAM_ROWNAME_DIR",
     ".local/share/smithbox/app/Assets/PARAM/ER/Param Row Names/English", "**/*", None),
    ("er-mechanics-jump.py", "ER_BEHBND_HKX", f"{_WITCHY}/chr/c0000-behbnd-dcx/Behaviors/c0000.hkx", None, None),
    ("er-behbnd-tree.py", "ER_C0000_BEHAVIOR_HKX",
     "er-extract/1171-20261004-witchy/chr/c0000-behbnd-dcx-wanibnd/Behaviors/c0000.hkx", None, None),
    ("er-mechanics-ashes.py", "ER_COMMON_DEFINE_HKS",
     "er-extract/1171-20261008-common-define/action/script/common_define.hks", None, None),
    ("er-mechanics-powerstance-guard.py", "ER_COMMON_DEFINE_HKS", f"{_ALL_ASSETS}/common_define.hks", None, None),
    ("er-mechanics-powerstance-guard.py", "ER_C0000_HKS",
     "projects/er-effects-rs-main-compare/.worktrees/no-death-loss-body/.deps/Smithbox/Documentation/ER/c0000.hks",
     None, "Documentation/ER/c0000.hks"),
    ("er-hks-weapon-category-table.py", None, f"{_ALL_ASSETS}/common_define.hks", None, None),
    ("er-hks-disasm.py", "ER_HKS_FILE", f"{_ALL_ASSETS}/c0000.hks", None, f"{_ALL_ASSETS}/c0000.hks"),
    ("er-mechanics-frame-advantage.py", "ER_PLAYER_HKS", f"{_ALL_ASSETS}/c0000.hks", None, None),
    # The item text `er-item-name.py` unpacked out of the archives; the archive headers it stamps
    # them against are the host's own, the same size and time as here (2026-10-19).
    ("er-item-name.py", "ER_MSG_CACHE", ".cache/er-mods-rs/msg/engus", "**/*", None),
)
#: The planner session `planner-ash-loadout-upload.py` signs its requests with, beside the game.
SESSION_FILE = ".local/share/Steam/steamapps/common/ELDEN RING/Game/er-build-planner-session.json"
#: Planner-cache entries the run does not read, as rsync patterns: other weapons' run directories
#: (the run's own is copied on its own), the board generator's cache, an early hand-run directory,
#: the stat-spread grids only `er-mechanics-build-spread.py` reads, and the scraper's login.
CACHE_EXCLUDES = ("/weapon-builds/", "/r3-boards/", "/poleblade/", "/spread-rl150*.npz", "/login.json")
#: Variables the run's own scripts read, passed on to the host when they are set here.
FORWARD_ENV = ("COLUMNS", "NO_COLOR", "ER_GAME_DIR", "ER_MSG_CORPUS_ROOT",
               *dict.fromkeys(env for _, env, *_ in HOME_INPUTS if env))
def boards():
    """`gen-r3-weapon-boards.py`, for its SSH, venv and grant helpers."""
    return _mod("gen_r3_weapon_boards", "gen-r3-weapon-boards.py")


def run_dir(a) -> Path:
    """The run's working directory: `--dir`, or `<planner cache>/weapon-builds/<title>-rl<rl>`."""
    title = a.weapon or f"{a.affinity} kit"
    return (a.dir.resolve() if a.dir else PLANNER_CACHE / "weapon-builds" / f"{slug(title)}-rl{a.rl}")


def forward_args(argv: list[str]) -> list[str]:
    """This run's arguments as the host is to get them: `--list-sync` dropped, `--host local` in
    place of `--host` (so an `ER_WEAPON_BUILD_HOST` set there cannot send the run on again), and
    `--dir` made absolute, since the host runs from the repository root."""
    out, skip = [], False
    for i, x in enumerate(argv):
        if skip:
            skip = False
        elif x == "--host":
            skip = True
        elif x.startswith("--host=") or x == "--list-sync":
            continue
        elif x == "--dir" and i + 1 < len(argv):
            out += ["--dir", str(Path(argv[i + 1]).resolve())]
            skip = True
        elif x.startswith("--dir="):
            out.append("--dir=" + str(Path(x[len("--dir="):]).resolve()))
        else:
            out.append(x)
    return out + ["--host", "local"]


def home_inputs(home: Path, env, upload: bool) -> list[Path]:
    """Every file outside the repository the run reads (`HOME_INPUTS`) that exists here, plus the
    planner session when the run uploads."""
    found = set()
    for _, var, default, pattern, _ in HOME_INPUTS:
        root = Path(env[var]) if var and env.get(var) else home / default
        if pattern is None:
            found.add(root)
        elif root.is_dir():
            found |= {p for p in root.glob(pattern) if p.is_file()}
    if upload:
        game = env.get("ER_GAME_DIR")
        found.add(Path(game) / Path(SESSION_FILE).name if game else home / SESSION_FILE)
    return sorted(p for p in found if p.is_file())


def rsync_argv(ssh: list[str], args: list[str], dry: bool = False) -> list[str]:
    return ["rsync", "-e", " ".join(ssh), *(["--dry-run", "--stats"] if dry else []), *args]


def sync_out(host: str, out: Path, listing: str) -> list[list[str]]:
    """rsync arguments that copy the run's inputs to `host`, each to its own path: the repository
    trees (mirrored), the files `home_inputs` names (from `listing`, one per line, relative to `/`),
    the planner cache less `CACHE_EXCLUDES`, and the run's own directory (its earlier passes)."""
    cmds = [["-a", "--delete", "--mkpath", f"{REPO_ROOT / t}/", f"{host}:{REPO_ROOT / t}/"] for t in REPO_TREES]
    # Files and their times only: rooted at `/`, the implied parent directories include `/home`,
    # whose times and modes the user cannot set (the boards script's copy, the same way).
    cmds.append(["-rlt", "--omit-dir-times", "--mkpath", f"--files-from={listing}", "/", f"{host}:/"])
    excludes = [f"--exclude={x}" for x in CACHE_EXCLUDES]
    cmds.append(["-a", "--mkpath", *excludes, f"{PLANNER_CACHE}/", f"{host}:{PLANNER_CACHE}/"])
    cmds.append(["-a", "--mkpath", f"{out}/", f"{host}:{out}/"])
    return cmds


def sync_back(host: str, out: Path) -> list[list[str]]:
    """rsync arguments that copy the run's files back: its directory, and what it added to the
    planner cache (interrupt simulations, reach and counter caches, a sweep it had to build).
    `--update` keeps a file changed here since the copy out."""
    excludes = [f"--exclude={x}" for x in CACHE_EXCLUDES]
    return [["-a", "--update", "--mkpath", f"{host}:{out}/", f"{out}/"],
            ["-a", "--update", "--mkpath", *excludes, f"{host}:{PLANNER_CACHE}/", f"{PLANNER_CACHE}/"]]


def run_grant(available: int, cores: int) -> tuple[int, int, str]:
    """(memory bytes, cores, why) the run is held to on a host with `available` bytes free and
    `cores` cores: the memory past the boards' desktop reserve and the cores less its spare ones.
    (0, 0, why) when not even one board shard's budget fits there (`grant_shards`)."""
    b = boards()
    fits, why = b.grant_shards(1, available, cores)
    if not fits:
        return 0, 0, why
    mem = available - b.DESKTOP_RESERVE_BYTES
    n = cores - b.SPARE_CORES
    return mem, n, (f"{available / b.GIB:.1f} GiB available less a {b.DESKTOP_RESERVE_BYTES // b.GIB} GiB desktop "
                    f"reserve leaves {mem / b.GIB:.1f} GiB; {cores} cores less {b.SPARE_CORES} leave {n}")


def pipeline_jobs(requested: int | None, capacity: tuple[int, int] | None, failure: str = "") -> tuple[int, str]:
    """(the pipeline's `--jobs`, why): `requested` when given (a `--host` run forwards its grant's
    cores this way, `with_jobs`), else the cores `run_grant` leaves on this machine's measured
    `capacity`. One job when the memory past the reserve holds not even one board shard, and the
    cores less the spare ones with a warning when the capacity could not be read."""
    if requested:
        return requested, f"--jobs {requested} given"
    b = boards()
    if capacity is None:
        n = max(1, (os.cpu_count() or 1) - b.SPARE_CORES)
        return n, f"warning: {failure}; {n} jobs from the core count alone"
    _, cores, why = run_grant(*capacity)
    return (cores, why) if cores else (1, f"{why}; one job")


def with_jobs(argv: list[str], cores: int) -> list[str]:
    """`argv` with `--jobs <cores>` added unless it names `--jobs` already: the host's run is pinned
    to the granted cores, so measuring there again would take the spare cores off twice."""
    if any(x == "--jobs" or x.startswith("--jobs=") for x in argv):
        return argv
    return argv + ["--jobs", str(cores)]


def remote_script(bin_dir: str, argv: list[str], mem: int, cores: int, unit: str | None,
                  env: dict | None = None) -> str:
    """The POSIX shell the host runs: this script with `argv`, from the repository root, with the
    venv's `python3` first on `PATH` (the pipeline calls `python3` by name), pinned to the first
    `cores` cores at the boards' nice, first in line for the OOM killer, and with a `unit` in a
    systemd scope capped at `mem` bytes with no swap. Exits with the run's status, or with
    the boards' `OOM_EXIT` when systemd recorded the scope as killed at its cap."""
    b = boards()
    run = (f"taskset -c 0-{cores - 1} nice -n {b.SHARD_NICE} python3 scripts/er-weapon-build.py "
           + " ".join(shlex.quote(x) for x in argv))
    head = [f"cd {shlex.quote(str(REPO_ROOT))} || exit 1", f"echo {b.SHARD_OOM_SCORE_ADJ} > /proc/self/oom_score_adj",
            f'export PATH={shlex.quote(bin_dir)}:"$PATH"']
    head += [f"export {k}={shlex.quote(v)}" for k, v in sorted((env or {}).items())]
    if unit is None:
        return "; ".join(head + [run, "exit $?"])
    return "; ".join(head + [
        f"systemd-run --user --scope --quiet --unit={unit} -p MemoryMax={mem} -p MemorySwapMax=0 "
        f"-p OOMPolicy=kill {run}", "rc=$?",
        f"r=$(systemctl --user show -p Result --value {unit} 2>/dev/null)",
        f"systemctl --user reset-failed {unit} 2>/dev/null",
        f'[ "$r" = oom-kill ] && exit {b.OOM_EXIT}', "exit $rc"])


def ssh_command(script: str) -> str:
    """The command line ssh carries: `script` base64-encoded and decoded by `sh` on the host. The
    host's login shell may be fish, and the local ssh process's command line then names neither
    this script nor its arguments, so `kitty-tail-task.py --match er-weapon-build.py` finds only
    the local process, which lives until the files are copied back."""
    blob = base64.b64encode(script.encode()).decode()
    return f"sh -c 'eval \"$(printf %s {blob} | base64 -d)\"'"


#: Seconds between the local process's heartbeats down the ssh session (`run_guarded`).
HEARTBEAT_SECONDS = 10
#: Seconds of silence after which the host's `supervise` takes the session for dead and kills the
#: run: a dropped network closes nothing, so EOF alone would leave the run going until TCP noticed.
REMOTE_SILENCE_SECONDS = 60
#: ssh's own liveness probe, so the local ssh also ends on a dropped network instead of hanging.
SSH_KEEPALIVE = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
#: Seconds a killed group gets between SIGTERM and SIGKILL.
KILL_GRACE_SECONDS = 10


def guarded_script(py: str, inner: str, unit: str | None, silence: int = REMOTE_SILENCE_SECONDS) -> str:
    """The shell ssh runs: `inner` (`remote_script`) under `supervise`, from the repository root."""
    return (f"cd {shlex.quote(str(REPO_ROOT))} || exit 1; exec {shlex.quote(py)} scripts/er-weapon-build.py "
            f"--supervise --silence {silence}" + (f" --unit {shlex.quote(unit)}" if unit else "")
            + f" -- sh -c {shlex.quote(inner)}")


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def group_members(pgid: int) -> list[int]:
    """The pids whose process group is `pgid`, read from /proc (field 5 of `stat`)."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        if len(fields) > 2 and int(fields[2]) == pgid and fields[0] != "Z":
            out.append(int(d))
    return out


def wait_group_gone(pgid: int, seconds: float) -> bool:
    """Whether every member of group `pgid` exits within `seconds`, waited on through each
    member's pidfd, which becomes readable when that process exits."""
    end = time.monotonic() + seconds
    while True:
        fds = []
        for pid in group_members(pgid):
            try:
                fds.append(os.pidfd_open(pid))
            except ProcessLookupError:
                pass
        if not fds:
            # Only zombies, if anything, are left: dead, and waiting on a parent that is not this one.
            return True
        try:
            left = end - time.monotonic()
            if left <= 0:
                return False
            select.select(fds, [], [], left)
        finally:
            for fd in fds:
                os.close(fd)


def kill_group(proc: subprocess.Popen, unit: str | None = None, grace: float = KILL_GRACE_SECONDS) -> None:
    """SIGTERM `proc`'s process group, SIGKILL whatever is left after `grace` seconds, and stop the
    systemd `unit` the run was scoped in, which also takes any worker that left the group."""
    pgid = proc.pid
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        proc.poll()
        if wait_group_gone(pgid, wait):
            break
    proc.poll()
    if unit:
        for cmd in (["systemctl", "--user", "stop", unit], ["systemctl", "--user", "reset-failed", unit]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass


def supervise(argv: list[str]) -> int:
    """`--supervise --silence <s> [--unit <u>] -- <cmd...>`, the host's end of `run_guarded`: run
    `cmd` in a new session (its own process group, so nothing here shares it), and kill that group
    (`kill_group`) when stdin -- the ssh session -- reaches EOF, when nothing arrives on it for
    `--silence` seconds, or on SIGTERM, SIGINT or SIGHUP. Exits with `cmd`'s status, or 128 plus
    the signal, or 129 when the session was lost."""
    ap = argparse.ArgumentParser(prog="er-weapon-build.py --supervise")
    ap.add_argument("--silence", type=float, default=REMOTE_SILENCE_SECONDS)
    ap.add_argument("--unit")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command to supervise")
    got: list[int] = []
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda n, _f: got.append(n))
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, start_new_session=True)
    fd = sys.stdin.fileno()
    last = time.monotonic()
    why = None
    while proc.poll() is None:
        if got:
            why = f"signal {got[0]}"
            break
        if time.monotonic() - last > a.silence:
            why = f"no heartbeat for {a.silence:.0f} s"
            break
        try:
            ready, _, _ = select.select([fd], [], [], 0.5)
        except OSError:
            ready = [fd]
        if ready:
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b""
            if not data:
                why = "the session closed"
                break
            last = time.monotonic()
    if why is None:
        return proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    print(f"remote: {why}; killing the run's process group {proc.pid}", file=sys.stderr, flush=True)
    kill_group(proc, a.unit)
    return 128 + got[0] if got else 129


class _Stopped(Exception):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def run_guarded(cmd: list[str], heartbeat: float = HEARTBEAT_SECONDS) -> tuple[int, int | None]:
    """Run `cmd` (ssh into `supervise`) with a heartbeat on its stdin every `heartbeat` seconds.
    On SIGTERM, SIGINT or SIGHUP, close that stdin -- the host's `supervise` sees EOF and kills
    the run's group -- wait for ssh to end, and only then let it go. Returns (status, the signal
    that stopped this process or None)."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    done = threading.Event()

    def beat():
        while not done.wait(heartbeat):
            try:
                proc.stdin.write(b"\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                return

    def stop(signum, _frame):
        raise _Stopped(signum)

    old = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    threading.Thread(target=beat, daemon=True).start()
    stopped = None
    try:
        try:
            rc = proc.wait()
        except _Stopped as e:
            stopped = e.signum
            for s in old:
                signal.signal(s, signal.SIG_IGN)
            done.set()
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                rc = proc.wait(timeout=KILL_GRACE_SECONDS + 10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    rc = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
    finally:
        done.set()
        for s, h in old.items():
            signal.signal(s, h)
    return rc, stopped


def rsync_stats(text: str) -> tuple[int, int]:
    """(files, bytes) an rsync `--stats` run transferred, or would have."""
    files = re.search(r"Number of regular files transferred: ([\d,]+)", text)
    size = re.search(r"Total transferred file size: ([\d,]+) bytes", text)
    return tuple(int(m[1].replace(",", "")) if m else 0 for m in (files, size))


def _rsync(b, args: list[str]) -> None:
    """One copy, waited on by its exit (`_run_to_exit`, as the boards' copies are)."""
    got = b._run_to_exit(rsync_argv(b.SSH, args))
    if got.returncode:
        raise SystemExit(f"rsync {' '.join(args[-2:])} failed: {got.stderr.strip()[:400]}")


def run_on_host(a, argv: list[str]) -> int:
    """Copy the inputs to `a.host`, run this command there, stream its output here, and copy its
    files back. The host's run is supervised (`run_guarded`, `supervise`): stopping this process
    with SIGTERM, SIGINT or SIGHUP, killing it outright, or losing the network kills the host's
    whole run, and nothing is copied back."""
    b = boards()
    host, out = a.host, run_dir(a)
    upload = not (a.dry_run or a.no_upload)
    files = home_inputs(Path.home(), os.environ, upload)
    with tempfile.NamedTemporaryFile("w", suffix=".files", delete=False) as listing:
        listing.write("".join(str(p).lstrip("/") + "\n" for p in files))
    try:
        outward = sync_out(host, out, listing.name)
        if a.list_sync:
            total = 0
            for args in outward:
                if not Path(args[-2].rstrip("/") or "/").exists():
                    print(f"(absent here) {args[-2]}")
                    continue
                got = b._run_to_exit(rsync_argv(b.SSH, args, dry=True))
                if got.returncode:
                    print(f"rsync --dry-run failed: {got.stderr.strip()[:400]}", file=sys.stderr)
                    return 1
                n, size = rsync_stats(got.stdout)
                total += size
                print(f"{size / 2**20:9.1f} MiB  {n:6d} files  {' '.join(args[-2:])}")
            print(f"{total / 2**20:9.1f} MiB in all to {host} ({len(files)} files outside the repository listed)")
            print("then: " + " ".join(rsync_argv(b.SSH, sync_back(host, out)[0])))
            return 0
        py, why = b.remote_python(host)
        if py is None:
            sys.exit(f"remote: {why}")
        capacity, failure = b.remote_capacity(host)
        if capacity is None:
            sys.exit(f"remote {host}: {failure}")
        mem, cores, why = run_grant(*capacity)
        if not mem:
            sys.exit(f"remote {host}: no room for the run: {why}")
        scoped, _ = b.remote_scope_works(host)
        print(f"remote {host}: {why}; " + (f"the run is capped at {mem / b.GIB:.1f} GiB with no swap" if scoped else
              "warning: systemd-run --user does not cap memory there, so the run is held only by nice and "
              "oom_score_adj"), flush=True)
        print(f"remote {host}: copying scripts, data, {len(files)} input files and the planner cache", flush=True)
        for args in outward:
            if Path(args[-2].rstrip("/") or "/").exists():
                _rsync(b, args)
    finally:
        os.unlink(listing.name)
    env = {k: os.environ[k] for k in FORWARD_ENV if os.environ.get(k)}
    env.setdefault("COLUMNS", str(BAR_COLUMNS))
    unit = f"er-weapon-build-{os.getpid()}.scope" if scoped else None
    fwd = with_jobs(forward_args(argv), cores)
    script = remote_script(str(Path(py).parent), fwd, mem, cores, unit, env)
    print(f"remote {host}: python3 scripts/er-weapon-build.py {' '.join(shlex.quote(x) for x in fwd)}",
          flush=True)
    rc, stopped = run_guarded(b.SSH + SSH_KEEPALIVE + [host, ssh_command(guarded_script(py, script, unit))])
    if stopped is not None:
        print(f"remote {host}: stopped by signal {stopped}; the host's run was killed (ssh exited {rc})",
              file=sys.stderr, flush=True)
        return 128 + stopped
    print(f"remote {host}: run exited {rc}"
          + (f" (killed at its {mem / b.GIB:.1f} GiB cap)" if rc == b.OOM_EXIT else
             " (the status ssh gives a dropped connection)" if rc == 255 else "")
          + f"; copying {out} and the planner cache back", flush=True)
    for args in sync_back(host, out):
        _rsync(b, args)
    print(f"remote {host}: files back in {out}", flush=True)
    return rc


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapon", nargs="?", help="the weapon the build is made around, as the game names it")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--two-handed", action="store_true", help="optimize the stats for two-handing it")
    ap.add_argument("--window", type=int, default=10, help="corpus RL window is rl +- this")
    ap.add_argument("--fight", choices=("invasion", "duel"), default="invasion",
                    help="fight model the gear is scored in (er-mechanics-buffs FIGHT_KINDS); invasion: "
                         "the invader against three hosts and two co-invaders, 10 s to 1 h. duel also "
                         "leaves out everything the ladder bans (`ladder_bans`) and refuses an upload "
                         "that breaks one")
    ap.add_argument("--base", default=BASE_ID, help="planner build id the face, great rune and items come from")
    ap.add_argument("--dir", type=Path, help="working directory (default under ~/.cache/er-build-planner)")
    ap.add_argument("--name", help="the uploaded build's name (default '<weapon> RL<rl>')")
    ap.add_argument("--affinity", help="pin the weapon and the rest of the inventory to this affinity")
    ap.add_argument("--pin", default="", help="stats held fixed, e.g. str=53,dex=21,int=33 "
                    "(er-mechanics-build-spread.py's pick); every other point is chosen as usual")
    ap.add_argument("--spell", action="append", default=[],
                    help="an attack spell the build is made around (repeatable); the first one's cast-speed "
                         "rule (er-mechanics-cast-speed.py) fills talisman slots before the weapon's ranking")
    ap.add_argument("--dry-run", action="store_true", help="choose everything and write the files, run no pipeline")
    ap.add_argument("--carried", type=float, help="weapon weight the first pass budgets for (default: the focus "
                    "weapon's, or 0 around no weapon); an earlier plan's heaviest set, to see its stats in a --dry-run")
    ap.add_argument("--no-upload", action="store_true", help="run every pipeline pass, then print the upload instead")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="run the whole build on this SSH host: copy the scripts, data and inputs there, run "
                         "it under nice within the host's memory and core grant, stream its output here, and "
                         "copy its files back (`run_on_host`). Default %(default)s, or ER_WEAPON_BUILD_HOST")
    ap.add_argument("--list-sync", action="store_true",
                    help="with --host: print what each copy would move (rsync --dry-run) and run nothing")
    ap.add_argument("--jobs", type=int, help="workers the pipeline's pools run (its --jobs and --interrupt-jobs); "
                    "default: the cores this machine's grant leaves (`pipeline_jobs`), and a --host run passes "
                    "the host's grant")
    ap.add_argument("--selftest", action="store_true")
    return ap


def main() -> int:
    if sys.argv[1:2] == ["--supervise"]:
        return supervise(sys.argv[2:])
    ap = parser()
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon and not a.affinity:
        ap.error("name a weapon, or give --affinity alone for a build around every weapon at it")
    if a.list_sync and a.host == "local":
        ap.error("--list-sync lists the copies to a --host; there are none for a local run")
    if a.host != "local":
        return run_on_host(a, sys.argv[1:])
    kit_mode = not a.weapon
    pins = {}
    for kv in filter(None, a.pin.split(",")):
        k, _, v = kv.partition("=")
        k = {"vit": "end"}.get(k, k)  # the planner's spelling of Endurance, still taken
        if k not in ("vig", "mnd", "end", *REQUIREMENT_FIELDS) or not v.isdigit():
            ap.error(f"--pin takes vig,mnd,end,{','.join(REQUIREMENT_FIELDS)} as key=value, not {kv!r}")
        pins[k] = int(v)

    armor_mod = _mod("er_mechanics_armor_targets", "er-mechanics-armor-targets.py")
    class_best = _mod("er_mechanics_class_best", "er-mechanics-class-best.py")
    inf = _mod("er_mechanics_infusions", "er-mechanics-infusions.py")
    up = _mod("planner_ash_loadout_upload", "planner-ash-loadout-upload.py")
    _mod("er_mechanics_gear_synergy", "er-mechanics-gear-synergy.py")
    rune_tears = _mod("er_mechanics_rune_tears", "er-mechanics-rune-tears.py")
    pouch = _mod("er_mechanics_pouch", "er-mechanics-pouch.py")
    watch = _mod("er_mechanics_pipeline_watch", "er-mechanics-pipeline-watch.py")
    # The siblings load `er-builds-optimize.py` themselves and each load replaces the module in
    # `sys.modules`; `optimize` pickles its worker by that name, so use the one registered last.
    opt = _mod("er_builds_optimize", "er-builds-optimize.py")
    tables = opt.AR.Tables(None)
    bans = ladder_bans() if a.fight == "duel" else None
    if a.weapon and bans:
        why = [x for x in [bans.weapon_ban(a.weapon) or bans.class_bans().get(a.weapon)]
               + [bans.spell_ban(s) for s in a.spell] if x]
        if why:
            sys.exit(f"the duel ladder bans {'; '.join(why)}, so no ladder build can be made around it")
    title = a.weapon or f"{a.affinity} kit"
    out = run_dir(a)
    out.mkdir(parents=True, exist_ok=True)
    global TICKER
    history = out / "weapon-build.log"
    past = history.read_text(encoding="utf-8").splitlines() if history.exists() else []
    PROGRESS.expected.update(phase_history(past))
    STEP_HISTORY.update(step_history(past))
    NO_UPLOAD[0] = a.no_upload
    # Off a terminal too: the bar line the ticker redraws there must be cleared before anything
    # else lands on its row.
    sys.stdout, sys.stderr = StatusOut(sys.stdout), StatusOut(sys.stderr)
    TICKER = Ticker()
    TICKER.bar = watch.bar
    _LOG.append(open(history, "a", encoding="utf-8"))
    say(f"{BOLD}{title}{RESET}, RL {a.rl}{', two-handed' if a.two_handed else ''}: files in {out}", "●")

    with phase(f"base build {a.base}", "base"):
        base = fetch_build(a.base)
        # The base's own weapons are another build's picks: `er-mechanics-combo-loadout.py` keeps
        # every weapon in `--build`'s inventory in its pool beside the role picks, so the default
        # base would hand this build the STR 18 / DEX 80 build's 100 weapons, 10 of them out of
        # reach at Poleblade of the Bud's stats. The upload rebuilds the inventory from the plan.
        base["inventory"] = {**(base.get("inventory") or {}), "slots": []}
        if bans:
            # The planner's ladder check counts every owned item, worn or not, and the base's own
            # armor and talismans are another build's (Briar, Rakshasa, Royal Remains, Shard of
            # Alexander were flagged, 2026-10-18). A duel owns only what this build equips, which
            # also keeps each inventory to about one page (user, same day).
            for part in ("head", "body", "arms", "legs"):
                base["protectors"][part] = {**base["protectors"][part], "slots": []}
            base["talismans"] = {**base["talismans"], "slots": []}
        base_path = out / "base.json"
        write_if_changed(base_path, json.dumps(base, indent=1))

    rows = opt.corpus_rows(opt.CACHE / "builds.jsonl", a.rl - a.window, a.rl + a.window)
    dated = opt.er_corpus.floor_counts(opt.CACHE / "builds.jsonl")
    say(f"corpus: {len(rows)} builds in RL {a.rl - a.window}-{a.rl + a.window}, out of {dated['kept']} mirror rows "
        f"saved since {dated['since']}; the post-DLC floor dropped {dated['dropped']} of "
        f"{dated['kept'] + dated['dropped']}", " ", GREY)
    # A duel's stats assume no banned grease anywhere: not in the kit, the heads or a search.
    sweep_keys, grease_tier, sweep_tier = fight_grease(a.fight)
    if not inf.sweep_path(a.rl, sweep_tier).exists():
        # The sweep is built once per RL and tier; a missing one is built here, waited on by its
        # exit, rather than stopping the run with a command to paste.
        cmd = inf.sweep_command(a.rl, sweep_tier, os.cpu_count() or 8)
        with phase(f"RL {a.rl} {sweep_tier} grease sweep (once per RL, minutes)"):
            if subprocess.Popen(cmd).wait():
                sys.exit(f"the sweep failed: {' '.join(cmd)}")
    sweep = inf.load_sweep(a.rl, sweep_tier)
    if bans:
        # A banned weapon is no reason to raise a stat, so the kit the stats are chosen for
        # holds only what a ladder build can carry.
        sweep = [r for r in sweep if not (bans.weapon_ban(r["weapon"]) or r["weapon"] in bans.class_bans())]
    kit = Kit(opt, sweep, tables, sweep_keys)
    affinity_kit = None
    if kit_mode:
        with phase(f"every {a.affinity} weapon", "spread"):
            # The floors (Vigor, the conditioning pool, the defender) come from the corpus around
            # the sweep's hardest-hitting weapon at the affinity; the stats are not its.
            heads = sorted(((max((r[k]["dmg"] for k in sweep_keys if r.get(k)),
                                 default=0), r["weapon"]) for r in sweep), reverse=True)
            for _, w in heads:
                search = opt.weapon_best(tables, rows, w, a.rl, a.two_handed, affinity=[a.affinity],
                                         grease_tier=grease_tier)
                if search["results"]:
                    break
            affinity_kit = AffinityKit(opt, tables, sweep, kit, a.affinity, search["defender"])
            say(f"{sum(map(len, affinity_kit.classes.values()))} {a.affinity} weapons in "
                f"{len(affinity_kit.classes)} classes; floors {search['floors']}")
    else:
        with phase(f"{a.weapon}'s own best spread", "spread"):
            search = opt.weapon_best(tables, rows, a.weapon, a.rl, a.two_handed,
                                     affinity=[a.affinity] if a.affinity else None, grease_tier=grease_tier)
            if not search["results"]:
                sys.exit(f"no class reaches RL {a.rl} with {a.weapon}'s requirements and floors {search['floors']}")
            best = search["results"][0]
            say(f"{best['affinity']} +{best['level']}, one hit {best['score']:.0f} at {stats_text(best['stats'], a.rl)}")

    focus = a.weapon  # the weapon mode's focus; in kit mode `a.weapon` becomes the gear's lead

    def respread(pins_, classes=None):
        if kit_mode:
            return affinity_spread(opt, a.rl, search, affinity_kit, pins=pins_, classes=classes)
        return kit_spread(opt, tables, focus, a.two_handed, a.rl, search, kit, pins=pins_, classes=classes)

    def show(sp):
        say(f"{sp['class']}: {BOLD}{stats_text(sp['stats'], a.rl)}{RESET}")
        if kit_mode:
            say(f"{a.affinity} classes {100 * sp['affinity_share']:.0f}% of their capped hit; kit "
                f"{100 * sp['kit']:.0f}%: {sp['wieldable']} wieldable and {sp['semi_wieldable']} "
                f"semi-wieldable of {sp['kit_weapons']}")
            return
        say(f"{a.weapon} hits {sp['focus_hit']:g}, {100 * sp['focus_hit'] / sp['focus_best']:.0f}% of its own "
            f"best {sp['focus_best']:g}; kit {100 * sp['kit']:.0f}%: {sp['wieldable']} wieldable and "
            f"{sp['semi_wieldable']} semi-wieldable of {sp['kit_weapons']}")

    with phase("stats for the whole kit", "kit"):
        spread = respread(pins or None)
        if spread is None:
            sys.exit(f"no class reaches RL {a.rl} with {title}'s requirements and floors {search['floors']}")
        show(spread)
    cr = _mod("er_mechanics_class_roles", "er-mechanics-class-roles.py")

    def set_lead(weapon, why) -> bool:
        """Score the gear around `weapon` (kit mode): talismans, tears and the pouch each take one
        weapon. It is not a focus; the pipeline picks the equip sets on its own. False when the
        weapon has no spread at these stats."""
        nonlocal best
        # Its own search, pinned to the spread: `search` keeps the floors every later re-spread
        # starts from, and pinned floors would leave no point to re-choose.
        found = opt.weapon_best(tables, rows, weapon, a.rl, a.two_handed, affinity=[a.affinity],
                                pins={k: spread["stats"][k] for k in opt.STATS}, grease_tier=grease_tier)["results"]
        if not found:
            return False
        a.weapon, best = weapon, found[0]
        say(f"gear scored around {a.weapon} ({best['affinity']} +{best['level']}, one hit {best['score']:.0f}): {why}")
        return True

    if kit_mode:
        # Not the hardest hitter (Prelate's Inferno Crozier, "a terrible weapon to build around",
        # user 2026-10-18): the class lead the cross-class main-hand score ranks first, read from
        # an earlier pass's roles until this run's own exist (`roles_lead` after each pass).
        prior = max(out.glob("*-roles.json"), key=lambda p: p.stat().st_mtime, default=None)
        lead = kit_lead(affinity_kit.hits(spread["stats"]), json.loads(prior.read_text(encoding="utf-8")), cr,
                        a.affinity) if prior else None
        if not (lead and set_lead(lead, f"the best {a.affinity} class lead by [class_pick_weights], moveset "
                                        f"features from {prior.name}")):
            set_lead(affinity_kit.lead(spread["stats"])[0],
                     "the hardest hit, since no earlier pass left class roles to rank the class leads by")

    talismans, buffs, great_rune, tears, rune_on, exclude_args = [], {}, None, [], None, []
    # {talisman: [skills]}: full-HP talismans a self-damaging copy of the plan leaves worth nothing
    # (`spent_by_self_damage`), kept out of the next pick.
    self_spent = {}

    def pick_gear():
        """Talismans, then the great rune and tears, around `a.weapon`; and the duel's loadout
        exclusion, which reads the talismans (Muleta)."""
        nonlocal talismans, buffs, great_rune, tears, rune_on, exclude_args
        with phase("talismans (gear synergy, a few minutes)", "talismans"):
            spell_picks = []
            if a.spell:
                rule = spell_rule(a.spell[0], a.weapon, spread["stats"])
                spell_picks = rule["added"]
            if a.fight == "duel":
                spell_picks = ([t for t in DUEL_TALISMANS if t not in self_spent]
                               + [t for t in spell_picks if t not in DUEL_TALISMANS])

            def banned(n):
                return ((bans.talisman_ban(n) if bans else None)
                        or (f"switched off by {', '.join(self_spent[n])}" if n in self_spent else None))
            talismans = pick_talismans(opt, a.weapon, best["affinity"], first=spell_picks, banned=banned)
            say(", ".join(talismans))

        # Before the armor: the great rune is part of the equip load and HP the armor is fitted to.
        with phase("great rune and crystal tears", "buffs"):
            buffs = rune_tears.pick(a.weapon, best["affinity"], best["level"], spread["stats"], a.two_handed,
                                    rl=a.rl, window=a.window, talismans=talismans, fight=a.fight,
                                    exclude=bans.TEARS if bans else ())
            great_rune, tears = buffs["great_rune"], list(buffs["crystal_tears"])
            # An invader's great rune is inert (buffs.md section 4), so the HP and equip load the
            # armor is fitted to leave it out; the planner build still carries it for hosting.
            rune_on = great_rune if rune_tears.B.FIGHT_KINDS[a.fight]["role"] == "host" else None
            say(f"{great_rune or 'no great rune'}{'' if rune_on or not great_rune else ' (inert for the invader)'}; "
                f"{' and '.join(tears)}")
            for item, notes in (buffs.get("unvalued") or {}).items():
                if notes:
                    say(f"{item}: {'; '.join(notes)}", " ", GREY)
        if bans:
            exclude_path = out / "ladder-exclude.json"
            write_if_changed(exclude_path, json.dumps(loadout_exclude(bans, talismans), indent=1))
            exclude_args = ["--exclude", str(exclude_path)]

    pick_gear()
    wid = tables.find_weapon(a.weapon)
    caster = class_best.kind(tables.weapons[wid]["wepType"]) == "casting"
    model = opt.RES.Model()
    corpus = corpus_survival(rows, opt.archetype(spread["stats"]))

    def budget_at(stats, armor, carried):
        def at(end):
            load = model.resources(dict(stats, end=end), talismans, (), rune_on)["max_equip_load"]
            return opt.MEDIUM_ROLL_MAX * load - carried - armor.talisman_weight
        return at

    floor = None
    no_armor = (bans.ARMOR | bans.REGEN) if bans else ()

    def load_at(stats, end):
        return model.resources(dict(stats, end=end), talismans, (), rune_on)["max_equip_load"]

    def describe(c):
        o = c["option"]
        return (f"Vigor {c['vig']}, Endurance {c['end']}{', ' + o['wear'] if o['wear'] else ''}: damage "
                f"{100 * c['damage']:.1f}% of capped, HP {c['hp']}, {c['ehp']:.2f} pool hits to die "
                f"({c['hit']:.0f} a hit), armor poise {o['armor_poise']:g}")

    fixed = {k: v for k, v in pins.items() if k not in ("vig", "end")}

    armor_models = {}

    def armor_model(stats):
        """`ArmorModel` at `stats` with the talismans and rune worn now, built once per such
        block: about 10 s each (2026-10-20), and every pass asks for its stats twice (the armor,
        then the weight cost). Nothing it holds is written but its own caches."""
        key = (tuple(sorted(stats.items())), tuple(talismans), rune_on, tuple(sorted(no_armor)))
        if key not in armor_models:
            armor_models[key] = armor_mod.ArmorModel(stats, talismans, rune_on, a.rl, a.window, exclude=no_armor)
        return armor_models[key]

    def armor_for(spread):
        nonlocal floor
        armor = armor_model(spread["stats"])
        if floor is None:
            floor = 0 if caster else armor.melee_floor
        return armor

    def joint_for(spread, armor, carried, cache=None, warn=True):
        """`joint_pick`'s result with `carried` weighing on the medium-roll budget, or None when no
        floor-poise set keeps medium roll at any Endurance. `cache` as `joint_pick`'s, for calls on
        the same spread."""
        at = budget_at(spread["stats"], armor, carried)
        wears = [None, *armor.hp_pieces]

        def pick(budget, fl, wear):
            # `Frontier.pick` slices its table with `[: floor(budget / step) + 1]`, which a negative
            # budget turns into a slice from the end, so it would hand back nearly the whole frontier:
            # the best set at no weight at all. Measured 2026-10-18 at 39 carried: a constant 8.59
            # damage x EHP, above the 8.00 at 0 carried.
            return armor.pick(budget, fl, wear) if budget >= 0 else None

        def options_at(e):
            budget, out_ = at(e), []
            for wear in wears:
                for fl in dict.fromkeys((floor, 0)):
                    o = pick(budget, fl, wear)
                    if o is None or o in out_:
                        continue
                    # The ladder bans light roll (under 30% equip load).
                    if bans and o["weight"] + carried + armor.talisman_weight <= \
                            opt.RES.WEIGHT_MEDIUM * load_at(spread["stats"], e):
                        continue
                    out_.append({**o, "floored": o["armor_poise"] >= floor})
            return out_

        base_end = model.class_base(spread["class"])[1]["end"]
        fit = first_fit(base_end, opt.STAT_CAP, at, lambda b: any(pick(b, floor, w) for w in wears))
        if fit is None:
            return None
        vfloor = search["floors"]["vig"]
        vigs = [pins["vig"]] if "vig" in pins else range(max(1, vfloor - VIG_BELOW), min(opt.STAT_CAP, vfloor + VIG_ABOVE) + 1)
        ends = range(base_end, min(opt.STAT_CAP, fit[0] + END_ABOVE) + 1)
        if "end" in pins and pins["end"] >= fit[0]:
            ends = [pins["end"]]
        elif "end" in pins and warn:
            say(f"--pin end={pins['end']} leaves no {floor}-poise set at medium roll; Endurance is chosen "
                f"freely", " ", YELLOW)

        def damage_at(v, e):
            sp = respread({**fixed, "vig": v, "end": e}, classes=[spread["class"]])
            return (damage_term(sp), sp) if sp else None

        def survive(st, o):
            return armor.hp(st, o["pieces"]), armor.hit(st, o)

        return joint_pick(vigs, ends, damage_at, options_at, survive, cache)

    def cost_table(spread, prefix):
        """`weight_cost` over 0..`WEIGHT_MAX` at this spread's class, written to
        `<prefix>-weight-cost.json` for the loadout (`--weight-cost`): what each carried weight
        costs in damage x EHP, so an equip set is chosen heavy only when it is worth its stats."""
        with phase("what carried weight costs in stats", "cost"):
            armor, cache = armor_for(spread), {}

            def value_at(w):
                res = joint_for(spread, armor, w, cache, warn=False)
                return res["best"]["value"] if res and res["best"] else None

            steps = int(WEIGHT_MAX / WEIGHT_STEP)
            table = weight_cost([round(i * WEIGHT_STEP, 3) for i in range(steps + 1)], value_at)
            table["class"], table["stats"] = spread["class"], spread["stats"]
            path = out / f"{prefix}-weight-cost.json"
            write_if_changed(path, json.dumps(table, indent=1))
            ws, vs = table["weights"], table["value"]
            say("damage x EHP over its best at " + ", ".join(
                f"{w:g}: {v:.2f}" for w, v in zip(ws, vs) if w % 5 == 0) + f" (to {ws[-1]:g}; {path.name})")
        return path

    def fit_armor(spread, carried, what):
        """(spread, armor set): Vigor, Endurance and the set chosen together by `joint_pick` with
        `carried` weighing on the medium-roll budget, the rest of the stats re-chosen around them."""
        with phase(f"Vigor, Endurance and armor for {what}", "armor"):
            armor = armor_for(spread)
            res = joint_for(spread, armor, carried)
            if res is None:
                sys.exit(f"no armor reaches {floor} poise at medium roll with {what} at any Endurance")
            top = res["best"]
            if top is None:
                sys.exit(f"no Vigor and Endurance leave a {floor}-poise set at medium roll with {what}")
            say(f"chosen: {describe(top)}", " ", BOLD)
            for wear, c in sorted(res["by_wear"].items(), key=lambda kv: -kv[1]["value"]):
                if c is not top:
                    say(f"  best {'with ' + wear if wear else 'without an HP piece'}: {describe(c)}: damage x EHP "
                        f"{100 * (c['value'] / top['value'] - 1):+.1f}%", " ", GREY)
            free = res["any_poise"]
            if free is not top:
                say(f"  best below the {floor} poise floor: {describe(free)}: damage x EHP "
                    f"{100 * (free['value'] / top['value'] - 1):+.1f}%; the floor stays, the user's rule for "
                    f"melee builds", " ", GREY)
            for name, c in corpus.items():
                say(f"  window {name.upper() if name != 'pvp' else 'PvP'} builds ({c['builds']}): Endurance median "
                    f"{c['end_median']:g} mean {c['end_mean']:g}, Vigor median {c['vig_median']:g} mean "
                    f"{c['vig_mean']:g}", " ", GREY)
            v, e, wear = top["vig"], top["end"], top["option"]["wear"]
            new = respread({**pins, "vig": v, "end": e})
            if new is None and fixed:
                # The `--pin` block plus the chosen Vigor and Endurance is past the RL, so the
                # pinned stats give way and are chosen again around them.
                say(f"--pin {a.pin} no longer fits at RL {a.rl} with Vigor {v} and Endurance {e}; choosing the "
                    f"stats again without it", " ", YELLOW)
                new = respread({"vig": v, "end": e})
            if new is None:
                sys.exit(f"no class reaches RL {a.rl} with Vigor {v}, Endurance {e} and {title}'s requirements")
            if new["stats"] != spread["stats"]:
                show(new)
                armor = armor_model(new["stats"])
            spread = new
            chosen = armor.pick(budget_at(spread["stats"], armor, carried)(e), floor, wear)
            if chosen is None:
                sys.exit(f"no {floor}-poise set fits at Endurance {e} once the stats are re-chosen around it")
            surv = armor.ehp(spread["stats"], chosen)
            chosen = {**chosen, **surv, "survival": {
                "damage": round(damage_term(spread), 4), "value": round(damage_term(spread) * surv["ehp"], 4),
                "alternatives": {str(w): {k: c[k] for k in ("vig", "end", "hp", "ehp", "value")}
                                 | {"pieces": c["option"]["pieces"]} for w, c in res["by_wear"].items()},
                "below_floor": {k: free[k] for k in ("vig", "end", "hp", "ehp", "value")}
                | {"pieces": free["option"]["pieces"], "armor_poise": free["option"]["armor_poise"]},
                "corpus": corpus}}
            say(f"{' / '.join(chosen['pieces'])}: weight {chosen['weight']:g}, poise {chosen['armor_poise']:g}, "
                f"keeps {100 * chosen['kept']:.1f}% of the window's PvP damage; HP {surv['hp']}, "
                f"{surv['ehp']:.2f} pool hits to die")
        return spread, chosen

    def other_affinity(spread):
        if a.affinity:
            return a.affinity
        with phase("affinity for the other weapons", "affinity"):
            dmg = {k: spread["stats"][k] for k in opt.DAMAGE_STATS}
            ranked = opt.weapons_for(tables, dmg, "damage", search["defender"], opt.CACHE / "catalog.json",
                                     "dlc-drawstring")
            other = Counter(r["affinity"] for r in ranked[:AFFINITY_ROWS]).most_common(1)[0][0]
            say(f"{other}: {', '.join(r['name'] for r in ranked[:3])} lead at these stats")
        return other

    name = a.name or f"{title} RL{a.rl}"

    def write_files(prefix, spread, chosen, other, carried):
        pieces = dict(zip(("head", "body", "arms", "legs"), chosen["pieces"]))
        up.planner_spelling([a.weapon] + talismans + [p for p in chosen["pieces"] if p != "-"] + tears
                            + [great_rune] * bool(great_rune))
        gear = {"protectors": [{"name": f"Medium roll, poise {chosen['armor_poise']:g}", **pieces}],
                "talismans": [{"name": a.weapon, "slots": talismans + [None] * (TALISMAN_SLOTS - len(talismans))}],
                "talisman_spares": {"after": DUEL_TALISMANS[1],
                                    "names": talisman_spares(talismans, duel=a.fight == "duel")},
                "great_rune": great_rune, "crystal_tears": tears}
        gear_path = out / f"{prefix}-gear.json"
        write_if_changed(gear_path, json.dumps(gear, indent=1))
        record = {"weapon": a.weapon, "rl": a.rl, "two_handed": a.two_handed, "affinity": best["affinity"],
                  "level": best["level"], "grease": best["grease"], "weapon_only": {"class": best["class"],
                  "stats": best["stats"], "hit": round(best["score"], 1)}, **spread,
                  "floor_scope": search["scope"], "conditioning_pool": search["pool_scope"],
                  "fight": a.fight, "talismans": talismans, "great_rune": great_rune, "crystal_tears": tears,
                  "buffs_rule": buffs["rule"], "armor": chosen, "poise_floor": floor, "carried_weight": carried,
                  "other_affinity": other, "base": a.base, "name": name, "prefix": prefix}
        (out / f"{prefix}-build.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return gear_path

    # The first pass prices the weapon alone; the pipeline's plan then says what the build carries.
    # Around no weapon, the gear's weapon is not carried for itself (user, 2026-10-18, of Prelate's
    # Inferno Crozier: "what a terrible weapon to build around"), so nothing is budgeted until the
    # plan's heaviest set says what is; a pass whose set does not fit chooses Vigor, Endurance and
    # the armor again with it on the budget.
    carried = 0.0 if kit_mode else tables.weapons[tables.find_weapon(a.weapon, best["affinity"])]["weight"]
    if a.carried is not None:
        carried = a.carried
    spread, chosen = fit_armor(spread, carried, f"{carried:g} of weapons" if a.carried is not None
                               else "no weapon" if kit_mode else f"{a.weapon} alone")
    other = other_affinity(spread)
    lead_from_roles = not kit_mode
    jobs, jobs_why = pipeline_jobs(a.jobs, *((None, "") if a.jobs else boards().local_capacity()))
    say(f"pipeline jobs: {jobs} ({jobs_why})", "●")
    for round_ in range(1, MAX_ROUNDS + 1):
        text = stats_text(spread["stats"], a.rl)
        # One prefix per stat block and fight model: the pipeline's freshness check reads files,
        # not `--stats`, and the status values in its files depend on the fight (`--fight`).
        prefix = f"{slug(title)}-{hashlib.sha1(f'{text}|{a.fight}'.encode()).hexdigest()[:8]}"
        gear_path = write_files(prefix, spread, chosen, other, carried)
        # Before every pass, at that pass's stats: the loadout weighs each equip set's weight
        # against what carrying it costs the build (`derive_combos`).
        cost_path = cost_table(spread, prefix)
        cmd = ["bash", str(PIPELINE), "--prefix", prefix, "--class", spread["class"], "--stats", text,
               "--affinity", other, "--name", name, "--weight-cost", str(cost_path),
               *([] if kit_mode else ["--focus", f"{a.weapon}:{best['affinity']}"]),
               "--gear", str(gear_path), "--base", a.base, "--base-json", str(base_path), "--dir", str(out),
               "--no-upload", "--fight", a.fight, "--jobs", str(jobs), *exclude_args,
               # An invader's pages carry rows of shields as markers (user, 2026-10-18); a duel is
               # one page with no room for them.
               *(["--icon-shields", str(ICON_SHIELD_ROWS)] if a.fight == "invasion" else []),
               *(["--arena-m", str(DUEL_ARENA_M)] if a.fight == "duel" else [])]
        if a.dry_run:
            say("pipeline (not run): " + " ".join(cmd), "●")
            return 0
        say(f"{BOLD}pass {round_} of at most {MAX_ROUNDS}{RESET}: the pipeline for {text}", "▶", YELLOW)
        t0 = time.monotonic()
        rc = run_pass(cmd, out, prefix, watch)
        if rc:
            say(f"pass {round_} failed (exit {rc}); its log is {out / (prefix + '-pipeline.log')}", "✗", RED)
            return rc
        say(f"pass {round_} {GREY}{clock(time.monotonic() - t0)}{RESET}", "✓", GREEN)
        plan_path = out / f"{prefix}-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if not kit_mode and not holds_weapon(plan, a.weapon):
            say(f"no equip set of the plan carries {a.weapon}, so it is not a build around it; the loadout "
                f"log says why: {out / (prefix + '-loadout.log')}", "✗", RED)
            return 1
        heavy = heaviest_set(plan, tables)
        fits = heavy["weight"] <= carried + 1e-6
        say(f"heaviest equip set {heavy['name']}: {', '.join(heavy['weapons'])}, weight {heavy['weight']:g} "
            f"({'within' if fits else 'over'} the {carried:g} budgeted)", " ", "" if fits else YELLOW)
        relead = False
        roles_path = out / f"{prefix}-roles.json"
        if not lead_from_roles and roles_path.exists():
            # Once only, so two passes' rankings cannot hand the lead back and forth.
            lead_from_roles = True
            top = roles_lead(class_leads(json.loads(roles_path.read_text(encoding="utf-8")), cr), a.affinity,
                             bans.weapon_ban if bans else lambda w: None)
            if top and top["weapon"] != a.weapon and set_lead(
                    top["weapon"], f"this pass's top {a.affinity} main-hand pick across classes ({top['class']}, "
                                   f"score {top['score']:g})"):
                pick_gear()
                relead = True
        spent = spent_by_self_damage(plan, talismans) if a.fight == "duel" and not self_spent else {}
        if spent:
            # Once only, like the lead above: the next pass's loadout no longer charges the cast
            # for the talisman, so its copies only gain.
            self_spent.update(spent)
            say("; ".join(f"{n} works only at full HP and {', '.join(s)} takes HP from its user: left out"
                          for n, s in spent.items()), " ", YELLOW)
            pick_gear()
            relead = True
        if fits and not relead:
            break
        carried = max(carried, heavy["weight"])
        new, chosen = fit_armor(spread, carried, f"{heavy['name']} ({carried:g}) equipped")
        if new["stats"] == spread["stats"] and not relead:
            gear_path = write_files(prefix, spread, chosen, other, carried)
            break
        spread = new
        other = other_affinity(spread)
    else:
        sys.exit(f"the heaviest equip set kept growing for {MAX_ROUNDS} rounds; the plans are in {out}")

    # After the plan: spells need a catalyst the plan holds. The pipeline's own gear file is a
    # freshness input of its first step, so these go into a separate file only the upload reads.
    if a.fight == "duel":
        full = json.loads(plan_path.read_text(encoding="utf-8"))
        # A buff skill is a weapon buff the ladder restricts like a grease (`10-3 [ BUFF RESTRICTIONS ]`),
        # so a copy carrying one in a banned setup is refused before the page is
        # cut, and the page takes the weapon's next copy instead.
        refused = duel_buff_ash_bans(bans, full)
        for i, why in sorted(refused.items()):
            s = full["slots"][i]
            say(f"refused {s['affinity']} {s['weapon']} {s.get('grip', '1H')} ({why})", " ", GREY)
        full = refuse_slots(full, refused)
        leads = class_leads(json.loads((out / f"{prefix}-roles.json").read_text(encoding="utf-8")),
                            _mod("er_mechanics_class_roles", "er-mechanics-class-roles.py"))
        # A seal or staff casts nothing a duel uses unless the build is made around a spell: heals
        # and body buffs are banned and the pouch carries no attack spell by default (user,
        # 2026-10-18: "why would I need a seal on this build if I can't heal?").
        page = duel_page(full, drop=lambda s: not a.spell and class_best.kind(
            tables.weapons[tables.find_weapon(s["weapon"])]["wepType"]) == "casting",
                         leads=leads, cls=lambda w: tables.weapons[tables.find_weapon(w)]["wepType"])
        plan_path = out / f"{prefix}-duel-plan.json"
        plan_path.write_text(json.dumps(page, indent=1), encoding="utf-8")
        say(f"duel page: {len(page['slots'])} of {len(full['slots'])} copies, {len(page['sets'])} of "
            f"{len(full['sets'])} equip sets, {page['page_growth']['added']} copies past the "
            f"{page['page_growth']['cap']}-slot page for the page weapons' best sets ({plan_path})")
    with phase("tools, flasks and spells", "pouch"):
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        held = list(dict.fromkeys(x["weapon"] for x in plan["slots"]))
        # The pouch is scored on top of the rune and tears already picked, so the base it reads
        # carries those rather than the template's.
        kit_items = pouch.pick(a.weapon, best["affinity"], best["level"], spread["stats"], held, a.two_handed,
                               rl=a.rl, window=a.window, talismans=talismans, fight=a.fight,
                               armor=[p for p in chosen["pieces"] if p != "-"],
                               base={**base, "greatRune": great_rune,
                                     "items": {**base.get("items", {}), "crystalTears": tears}},
                               assumed_tier=grease_tier,
                               **({"exclude": lambda n, kind: (bans.spell_ban if kind == "spell" else bans.tool_ban)(n),
                                   "buff_ban": duel_buff_ban(bans, a.weapon, a.two_handed, plan)}
                                  if bans else {}))
        say(f"tools {', '.join(t['name'] for t in kit_items['tools'])}; spells "
            f"{', '.join(kit_items['spells']) or 'none'}; flasks {kit_items['flasks']}")
        say("pots " + ", ".join(f"{p['name']} x{p['capacity']} ({p['hp']:.0f} HP a throw)"
                                for p in kit_items.get("pots", ())) + "; boluses "
            + (", ".join(b["name"] for b in kit_items.get("boluses", ()) if b["slotted"]) or "none"))
        for left_out in kit_items.get("skipped", ()):
            say(f"left out: {left_out['count']} {left_out['category']} ({left_out['why']})", " ", GREY)
        if bans:
            # The pouch always keeps the crimson flask, and the ladder bans every HP recovery
            # (10-3 "All Methods of HP Recovery"), so a duel carries cerulean only.
            dropped = [t["name"] for t in kit_items["tools"] if bans.tool_ban(t["name"])]
            kit_items["tools"] = [t for t in kit_items["tools"] if not bans.tool_ban(t["name"])]
            fl = kit_items["flasks"]
            kit_items["flasks"] = {**fl, "crimson": 0, "cerulean": fl["crimson"] + fl["cerulean"]}
            say(f"duel: left out {', '.join(dropped) or 'nothing'}; flasks {kit_items['flasks']}")
    gear = json.loads(gear_path.read_text(encoding="utf-8"))
    gear.update(tools=kit_items["tools"], spells=list(dict.fromkeys(list(a.spell) + kit_items["spells"])),
                flasks=kit_items["flasks"], tags="derive")
    gear_path = out / f"{prefix}-upload-gear.json"
    gear_path.write_text(json.dumps(gear, indent=1), encoding="utf-8")
    upload = ["python3", str(HERE / "planner-ash-loadout-upload.py"), "--base", a.base, "--plan", str(plan_path),
              "--class", spread["class"], "--stats", stats_text(spread["stats"], a.rl), "--name", name,
              "--gear", str(gear_path)] + ["--ladder"] * bool(bans)
    if a.no_upload:
        say("upload (not run): " + " ".join(upload), "●")
        return 0
    with phase("upload", "upload"):
        # Waited on by its exit: each of its requests carries its own 20 s timeout
        # (`planner-ash-loadout-upload.request`), and a cap on the whole could cut it off between
        # storing the build and reading it back.
        rc = subprocess.Popen(upload).wait()
    return rc


def remote_checks(check) -> None:
    """`--host`: the arguments, the input list, the copies and the remote wrapper, with no network."""
    b = boards()
    # Arguments.
    a = parser().parse_args(["--affinity", "Heavy", "--rl", "125", "--fight", "duel", "--host", "zerk"])
    check(a.host == "zerk" and run_dir(a) == PLANNER_CACHE / "weapon-builds" / "heavy-kit-rl125",
          "--host is parsed, and a kit run's directory is weapon-builds/heavy-kit-rl125 in the planner cache")
    check(parser().parse_args(["Uchigatana"]).host == DEFAULT_HOST and
          os.environ.get("ER_WEAPON_BUILD_HOST", "local") == DEFAULT_HOST,
          "--host defaults to ER_WEAPON_BUILD_HOST, else local")
    check(PLANNER_CACHE == _mod("er_builds_optimize", "er-builds-optimize.py").CACHE,
          "PLANNER_CACHE is er-builds-optimize's CACHE")
    fwd = forward_args(["--affinity", "Heavy", "--host", "zerk", "--list-sync", "--dir", "rel/d", "--host=zerk",
                        "--fight", "duel"])
    check(fwd == ["--affinity", "Heavy", "--dir", str(Path("rel/d").resolve()), "--fight", "duel", "--host", "local"],
          "forward_args drops --host and --list-sync, makes --dir absolute, and pins the host's run to local")
    check(forward_args(["X", "--dir=d"]) == ["X", f"--dir={Path('d').resolve()}", "--host", "local"],
          "forward_args makes --dir=<path> absolute too")
    # Every input row names a path its reader still defaults to, under the variable it reads.
    for reader, var, default, _, quote in HOME_INPUTS:
        src = (HERE / reader).read_text(encoding="utf-8")
        flat = re.sub(r"""['"]\s*\n\s*['"]""", "", src)  # adjacent literals split across lines
        check((quote or default) in flat and (var is None or f"'{var}'" in src or f'"{var}"' in src),
              f"HOME_INPUTS: {reader} reads {var or 'its default'} at ~/{default}")
    # The file list, on a made-up home.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        made = {}
        for rel in (f"{_WITCHY}/chr/c0000_a0x-anibnd-dcx/x/a000_000000.hkx", f"{_WITCHY}/chr/c0100-anibnd-dcx/x/a.hkx",
                    f"{_WITCHY}/parts/_chunk_0001/wp_a_0612-partsbnd-dcx/WP_A_0612.flver",
                    f"{_WITCHY}/parts/_chunk_0001/wp_a_0612-partsbnd-dcx/WP_A_0612.tpf",
                    f"{_WITCHY}/parts/_chunk_0001/am_f_0000-partsbnd-dcx/AM_F_0000.flver",
                    f"{_ALL_ASSETS}/c0000.hks", ".cache/er-mods-rs/msg/engus/stamp.json", SESSION_FILE,
                    "other/thing.txt"):
            made[rel] = home / rel
            made[rel].parent.mkdir(parents=True, exist_ok=True)
            made[rel].write_text("x")
        got = {p.relative_to(home).as_posix() for p in home_inputs(home, {}, upload=False)}
        check(got == {f"{_WITCHY}/chr/c0000_a0x-anibnd-dcx/x/a000_000000.hkx",
                      f"{_WITCHY}/parts/_chunk_0001/wp_a_0612-partsbnd-dcx/WP_A_0612.flver",
                      f"{_ALL_ASSETS}/c0000.hks", ".cache/er-mods-rs/msg/engus/stamp.json"},
              "home_inputs lists a c0000 clip, a weapon FLVER, an HKS and the item text, and no other "
              f"shard's clip, texture, armor model, session or stray file (got {sorted(got)})")
        check(SESSION_FILE in {p.relative_to(home).as_posix() for p in home_inputs(home, {}, upload=True)},
              "home_inputs adds the planner session when the run uploads")
        moved = home / "elsewhere/msg"
        moved.mkdir(parents=True)
        (moved / "f.fmg").write_text("x")
        check(moved / "f.fmg" in home_inputs(home, {"ER_MSG_CACHE": str(moved)}, upload=False),
              "home_inputs follows a variable set here to its path")
    # The copies.
    out = PLANNER_CACHE / "weapon-builds" / "heavy-kit-rl125"
    there = sync_out("zerk", out, "/tmp/l.files")
    ex = [f"--exclude={x}" for x in CACHE_EXCLUDES]
    check(there == [*[["-a", "--delete", "--mkpath", f"{REPO_ROOT / t}/", f"zerk:{REPO_ROOT / t}/"] for t in REPO_TREES],
                    ["-rlt", "--omit-dir-times", "--mkpath", "--files-from=/tmp/l.files", "/", "zerk:/"],
                    ["-a", "--mkpath", *ex, f"{PLANNER_CACHE}/", f"zerk:{PLANNER_CACHE}/"],
                    ["-a", "--mkpath", f"{out}/", f"zerk:{out}/"]],
          "sync_out mirrors scripts, data and the duel tables, copies the listed inputs, the cache less "
          "its exclusions and the run's own directory, each to the same path on the host")
    check(sync_back("zerk", out) == [["-a", "--update", "--mkpath", f"zerk:{out}/", f"{out}/"],
                                     ["-a", "--update", "--mkpath", *ex, f"zerk:{PLANNER_CACHE}/", f"{PLANNER_CACHE}/"]],
          "sync_back copies the run's directory and the cache home, keeping what changed here since")
    check(rsync_argv(b.SSH, ["-a", "s/", "h:s/"], dry=True)
          == ["rsync", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=5", "--dry-run", "--stats", "-a", "s/", "h:s/"],
          "rsync_argv reaches the host with the boards' batch-mode ssh")
    check(rsync_stats("Number of regular files transferred: 1,234\nTotal transferred file size: 5,678,901 bytes\n")
          == (1234, 5678901), "rsync_stats reads --stats' file count and size")
    # The grant, on zerk's numbers of 2026-10-19 (14054384 kB available, 12 cores).
    mem, cores, why = run_grant(14054384 * 1024, 12)
    check(mem == 14054384 * 1024 - 4 * b.GIB and cores == 10, f"run_grant on zerk: {why}")
    check(run_grant(int(6.9 * b.GIB), 12)[:2] == (0, 0), "run_grant: no room when one shard's 3 GiB does not fit")
    # The pipeline's jobs: the grant's cores, local and remote alike, never the pipeline's old 6.
    check(pipeline_jobs(None, (14054384 * 1024, 12))[0] == 10, "pipeline_jobs on zerk's capacity is its grant's 10 cores")
    check(pipeline_jobs(None, (64 * b.GIB, 32))[0] == 30, "pipeline_jobs on a 32-core machine is 30")
    check(pipeline_jobs(4, (64 * b.GIB, 32))[0] == 4, "pipeline_jobs keeps an explicit --jobs")
    check(pipeline_jobs(None, (int(6.9 * b.GIB), 12))[0] == 1, "pipeline_jobs: one job when one shard does not fit")
    n, why = pipeline_jobs(None, None, "cannot read /proc/meminfo")
    check(n == max(1, (os.cpu_count() or 1) - b.SPARE_CORES) and why.startswith("warning:"),
          "pipeline_jobs without a capacity: the cores less the spare ones, with a warning")
    fwd = with_jobs(forward_args(["--affinity", "Heavy", "--host", "zerk"]), 10)
    check(fwd == ["--affinity", "Heavy", "--host", "local", "--jobs", "10"],
          "with_jobs hands the host's run its grant's cores as --jobs")
    check(with_jobs(["--jobs", "3", "--host", "local"], 10) == ["--jobs", "3", "--host", "local"]
          and with_jobs(["--jobs=3"], 10) == ["--jobs=3"], "with_jobs keeps a --jobs the run was given")
    check(parser().parse_args(fwd).jobs == 10, "the host's run parses the forwarded --jobs")
    script = (HERE / "er-mechanics-build-pipeline.sh").read_text()
    check(re.search(r"^PREFIX=.* JOBS=\"\" ", script, re.M) and "JOBS=6" not in script,
          "the pipeline has no fixed --jobs default")
    # The wrapper and the line ssh carries.
    argv = ["--affinity", "Heavy", "--fight", "duel", "--host", "local"]
    scoped = remote_script("/h/venv/bin", argv, 10 * b.GIB, 10, "u.scope", {"COLUMNS": "120"})
    check("export PATH=/h/venv/bin:\"$PATH\"" in scoped and "export COLUMNS=120" in scoped
          and f"systemd-run --user --scope --quiet --unit=u.scope -p MemoryMax={10 * b.GIB} -p MemorySwapMax=0 "
              "-p OOMPolicy=kill taskset -c 0-9 nice -n 10 python3 scripts/er-weapon-build.py --affinity Heavy "
              "--fight duel --host local" in scoped
          and "echo 1000 > /proc/self/oom_score_adj" in scoped and f"exit {b.OOM_EXIT}" in scoped,
          "remote_script: venv first on PATH, capped scope, ten cores, nice 10, OOM-first")
    plain = remote_script("/h/venv/bin", argv, 0, 10, None)
    check("systemd-run" not in plain and plain.endswith("--host local; exit $?"), "remote_script without a scope")
    line = ssh_command(scoped)
    blob = re.search(r"printf %s (\S+) \|", line)[1]
    check(base64.b64decode(blob).decode() == scoped and "er-weapon-build" not in line and "--fight" not in line,
          "ssh_command carries the wrapper encoded, so `--match er-weapon-build.py` finds only the local process")
    run = subprocess.run(["sh", "-c", line.split(" ", 2)[2][1:-1].replace("eval", "printf %s", 1)],
                         capture_output=True, text=True, timeout=10)
    check(run.stdout == scoped, "sh decodes the carried line back to the wrapper")


def guard_checks(check) -> None:
    """`run_guarded` and `supervise` on this machine: the supervisor stands in for ssh plus the
    host, and a sleeping `sh` group for the run. Each way of stopping the local process must leave
    no member of that group alive."""
    me = [sys.executable, str(Path(__file__).resolve()), "--supervise"]

    def fake_host(tmp: Path, silence: float, body: str = "sleep 300 & sleep 300 & wait") -> tuple[list[str], int]:
        # The run reports its pgid through a FIFO this side holds open read-write, so the write
        # never blocks and the read is an event rather than a poll of a file.
        fifo = tmp / f"pgid-{time.monotonic_ns()}"
        os.mkfifo(fifo)
        fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        return me + ["--silence", str(silence), "--", "sh", "-c", f"echo $$ > {fifo}; {body}"], fd

    def pgid_of(fd: int) -> int | None:
        try:
            if not select.select([fd], [], [], 15)[0]:
                return None
            return int(os.read(fd, 64).split()[0])
        finally:
            os.close(fd)

    def guarded_child(cmd) -> int:
        """Fork a local process running `run_guarded(cmd)`; returns its pid."""
        pid = os.fork()
        if pid == 0:
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 2)
                rc, stopped = run_guarded(cmd, heartbeat=0.2)
                os._exit(128 + stopped if stopped is not None else rc)
            finally:
                os._exit(99)
        return pid

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGHUP, "SIGHUP"), (signal.SIGINT, "SIGINT"),
                          (signal.SIGKILL, "SIGKILL")):
            cmd, pgid_fd = fake_host(tmp, 30)
            parent = guarded_child(cmd)
            pgid = pgid_of(pgid_fd)
            alive = pgid is not None and _group_alive(pgid)
            os.kill(parent, sig)
            _, status = os.waitpid(parent, 0)
            gone = pgid is not None and wait_group_gone(pgid, 15)
            want = (os.WIFSIGNALED(status) if sig == signal.SIGKILL
                    else os.WIFEXITED(status) and os.WEXITSTATUS(status) == 128 + sig)
            check(alive and gone and want,
                  f"run_guarded: {name} to the local process kills the remote run's whole group "
                  f"(started {alive}, gone {gone}, status {status:#x})")
            if pgid is not None and _group_alive(pgid):
                os.killpg(pgid, signal.SIGKILL)
        # A dropped network: the session stays open and goes quiet.
        cmd, pgid_fd = fake_host(tmp, 1.5)
        sup = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pgid = pgid_of(pgid_fd)
        t0 = time.monotonic()
        rc = sup.wait(timeout=20)
        gone = pgid is not None and wait_group_gone(pgid, 5)
        check(gone and rc == 129 and time.monotonic() - t0 < 10,
              f"supervise: a session silent past --silence kills the run's group (exit {rc})")
        sup.stdin.close()
        # SIGTERM to the supervisor itself (the host's sshd or anything else stopping it).
        cmd, pgid_fd = fake_host(tmp, 30)
        sup = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pgid = pgid_of(pgid_fd)
        sup.terminate()
        rc = sup.wait(timeout=20)
        gone = pgid is not None and wait_group_gone(pgid, 5)
        check(gone and rc == 128 + signal.SIGTERM, f"supervise: SIGTERM kills the run's group (exit {rc})")
        sup.stdin.close()
        # Normal completion: heartbeats keep a run longer than --silence alive, and its status comes back.
        cmd, pgid_fd = fake_host(tmp, 1.0, "sleep 2.5; exit 7")
        rc, stopped = run_guarded(cmd, heartbeat=0.2)
        os.close(pgid_fd)
        check((rc, stopped) == (7, None),
              f"run_guarded: heartbeats hold a run past --silence and its exit status comes back ({rc}, {stopped})")
    script = guarded_script("/h/venv/bin/python", "echo hi; exit 3", "u.scope", 60)
    check(script == f"cd {shlex.quote(str(REPO_ROOT))} || exit 1; exec /h/venv/bin/python scripts/er-weapon-build.py "
                    "--supervise --silence 60 --unit u.scope -- sh -c 'echo hi; exit 3'",
          "guarded_script execs the supervisor from the repository root around the wrapper")


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    remote_checks(check)
    guard_checks(check)

    check(slug("Poleblade of the Bud") == "poleblade-of-the-bud" and slug("Bloodhound's Fang") == "bloodhound-s-fang",
          "slug keeps letters and digits and joins the rest with one dash")
    hist = phase_history(["✓ base build 188e74da444029 0s", "✓ talismans (gear synergy, a few minutes) 3m10s",
                          " ✓   9  loadout                      1h18m", "✓ pass 1 1h49m",
                          "✓ armor and Endurance for X alone 14s", "✓ talismans (gear synergy, a few minutes) 2m05s"])
    check(hist == {"base": 0, "talismans": 125, "pass": 6540, "armor": 14},
          "phase_history reads each phase's last ✓ time and skips the pipeline's own step lines")
    check(step_history([" ✓   9  loadout                      1h18m", " ✓  7a  true combos                  3m40s",
                        " –   10  upload                       up to date, skipped", "✓ pass 1 1h49m",
                        " ✓   9  loadout                      40m10s"]) == {"9": 2410, "7a": 220},
          "step_history reads each pipeline step's last ✓ time from the run log and nothing else")
    now = dt.datetime.combine(dt.date.today(), dt.time(12, 30, 0))
    got = pass_progress(["== 12:00:00 1 build file", "== 12:00:10 2 embedding", "== 12:20:00 9 loadout"],
                        ["1", "2", "9"], {"1": 10, "2": 1190, "9": 1800}, now)
    check(got == ((10 + 1190 + 600) / 3000, 3, "9"),
          "pass_progress counts the steps before the current one by their history and the current one by its time")
    stage_re = re.compile(r"^stage (\d+)/(\d+): (.+) \((\d+)s in\)$")
    check(stream_line([], [], ["1", "9"], {}, stage_re) == "", "stream_line is empty before the first step")
    check(stream_line(["== 12:00:00 1 build file"], ["stage 3/21: rows (5s in)"], ["1", "9"],
                      {"1": "build file"}, stage_re) == "step 1 of 2: build file",
          "stream_line names the current step and reads loadout stages only during the loadout")
    check(stream_line(["== 12:00:00 1 build file", "x", "== 12:20:00 9 loadout"],
                      ["stage 13/21: rows (5s in)", "stage 14/21: layout (2086s in)"], ["1", "9"],
                      {"9": "loadout"}, stage_re) == "step 2 of 2: loadout, stage 14/21: layout",
          "stream_line adds the loadout's latest stage")
    check(stage_elapsed(["== 12:00:00 1 a", "== 12:20:00 9 loadout"], now) == 600.0,
          "stage_elapsed counts from the latest step head")
    check(stage_elapsed([], now) == 0.0, "stage_elapsed is 0 before any step")
    check(step_share(["1", "2", "9"], {"1": 10, "2": 1190, "9": 1800}, "9", 0.5) == (1200 + 900) / 3000,
          "step_share weighs the steps before by their history and the current one by its share")
    # The interrupt pass computing from empty, where the last run had it cached (11 s): its
    # counter, rows weighted, sets the time left. Largest chunk first, so 13 of 143 weapons is
    # half the rows.
    watch = _mod("er_mechanics_pipeline_watch", "er-mechanics-pipeline-watch.py")
    head = ["== 12:00:00 1 build file", "== 12:00:00 9 loadout"]
    stages = ['stages: [["rows", 10], ["interrupt", 11], ["whiff", 100], ["write", 10]]',
              "stage 1/4: rows (0s in)", "stage 2/4: interrupt (10s in)"]
    plan9, hist9 = ["1", "9"], {"1": 5, "9": 131}
    PROGRESS.start("pass")
    got = update_pass(head, lambda: stages + ["  13/143 weapons, 4320/8640 rows"], plan9, hist9,
                      dt.datetime.combine(dt.date.today(), dt.time(12, 30, 0)), watch)
    first = PROGRESS.left
    x = PROGRESS.numbers()
    check(got[:2] == (2, "9") and abs(first - (1790 + 100 + 10)) < 1e-6 and abs(x["exp"] - x["el"] - first) < 1e-6,
          f"update_pass: the interrupt counter's rows set the pass's time left ({first})")
    naive = 1790 / (13 / 143) * (130 / 143)
    check(first < naive / 5, f"rows, not weapons, so the largest-first chunks do not over-state it ({naive:.0f})")
    update_pass(head, lambda: stages + ["  40/143 weapons, 6480/8640 rows"], plan9, hist9,
                dt.datetime.combine(dt.date.today(), dt.time(12, 40, 0)), watch)
    check(PROGRESS.left < first - 600, f"the time left falls as the counter moves ({PROGRESS.left:.0f})")
    update_pass(head[:1], lambda: stages, plan9, hist9, dt.datetime.combine(dt.date.today(), dt.time(12, 40, 0)), watch)
    check(PROGRESS.left is None, "update_pass leaves the last run's times in charge outside the loadout")
    PROGRESS.stop()
    check(pass_left(["1", "9", "x"], {"1": 5}, "9", 30.0) is None and pass_left(["1", "9", "x"], {"x": 7}, "9", 30.0)
          == 37.0, "pass_left adds the later steps' history, and gives up when one has none")
    PROGRESS.start("pass")
    PROGRESS.part = 0.5
    bl = ANSI.sub("", PROGRESS.bar_line(lambda f, w: f"<{f:.2f}>", "step 9 of 12: loadout"))
    check(bl.startswith("<") and "pass" in bl and "50%" in bl and bl.endswith("step 9 of 12: loadout"),
          "bar_line leads with the run's bar and ends with what the pass is on")
    # The line as the user's 80-column tail shows it, mid-interrupt: the counter and its age must
    # survive the cut, so they come straight after the bar.
    beat = "interrupt 2492/8215 rows, last step 31s ago"
    bl = PROGRESS.bar_line(watch.bar, "step 12 of 12: loadout, stage 3/5: interrupt 38%", beat)
    cut = ANSI.sub("", fit_frame(bl, 80, 2))
    check(beat in cut and len(cut) <= 79, f"bar_line keeps the counter and its age inside 80 columns ({cut})")
    stalled = "interrupt 2492/8215 rows, \033[31mno progress for 14m\033[0m"
    cut = ANSI.sub("", fit_frame(PROGRESS.bar_line(watch.bar, "x", stalled), 80, 2))
    check("no progress for 14m" in cut and len(cut) <= 79, f"a stalled counter's mark fits too ({cut})")
    PROGRESS.stop()
    # pass_pulse over a real loadout log, advanced across polls: the age runs from the advance.
    with tempfile.TemporaryDirectory() as tmp:
        pfx = Path(tmp) / "t"
        log9 = Path(f"{pfx}-loadout.log")
        step9 = next(s for s in watch.steps(pfx) if s["id"] == "9")
        pu = watch.Pulse(REDRAW_SECONDS)
        t0 = dt.datetime(2026, 10, 20, 12, 0, 0)
        beats = []
        for i, sec in enumerate((0, 10, 20, 30, 40)):
            body = stages + [f"  {20 + i}/143 weapons, {2492 + 100 * i}/8215 rows", "warning: x"]
            log9.write_text("\n".join(body) + "\n", encoding="utf-8")
            now_i = t0 + dt.timedelta(seconds=sec)
            beats.append(pass_pulse(watch, pu, step9, watch.stage_progress(body, 40.0), pfx, now_i))
        live = ANSI.sub("", pass_pulse(watch, pu, step9, watch.stage_progress(body, 40.0), pfx,
                                       t0 + dt.timedelta(seconds=71)))
        dead = ANSI.sub("", pass_pulse(watch, pu, step9, watch.stage_progress(body, 40.0), pfx,
                                       t0 + dt.timedelta(seconds=40 + 14 * 60)))
        check(beats[-1].startswith("interrupt 30s 2892/8215 rows, last step 0s ago")
              and live == "interrupt 30s 2892/8215 rows, last step 31s ago",
              f"pass_pulse shows the interrupt stage, its time in, its rows and the age of its last advance ({live})")
        check(dead == "interrupt 30s 2892/8215 rows, no progress for 14m",
              f"pass_pulse marks a counter quiet for many median gaps ({dead})")
        check(pass_pulse(watch, pu, None, None, pfx, t0) == "", "pass_pulse is empty before a step")

        # The 2026-10-20 run: "set coverage" never timed, no counter, the bar at `100% ~45s left`
        # for five minutes. Now its share is `?` until the stage's counter gives it one.
        quiet = ['stages: [["rows", 10], ["partners", 3], ["set coverage", null], ["write", 4]]',
                 "stage 1/4: rows (0s in)", "stage 2/4: partners (10s in)", "stage 3/4: set coverage (13s in)"]
        log9.write_text("\n".join(quiet) + "\n", encoding="utf-8")
        os.utime(log9, (t0.timestamp(), t0.timestamp()))
        head_q = ["== 12:00:00 1 build file", "== 12:00:00 9 loadout"]
        PROGRESS.start("pass")
        now_q = dt.datetime.combine(dt.date.today(), dt.time(12, 5, 43))
        _, sid_q, _, sp_q = update_pass(head_q, lambda: quiet, plan9, hist9, now_q, watch)
        x = PROGRESS.numbers()
        bl = ANSI.sub("", PROGRESS.bar_line(lambda f, w: "<bar>", ""))
        check(PROGRESS.unknown and x["unknown"] and "+? left" in bl and "~45s left" not in bl
              and abs(PROGRESS.rest_known - 4.0) < 1e-9,
              f"a stage with no history and no counter leaves its share as ?, not seconds ({bl})")
        PROGRESS.stop()
        PROGRESS.start("pass")
        update_pass(head, lambda: stages + ["  13/143 weapons, 4320/8640 rows"], plan9, hist9,
                    dt.datetime.combine(dt.date.today(), dt.time(12, 30, 0)), watch)
        check(not PROGRESS.unknown and "?" not in ANSI.sub("", PROGRESS.bar_line(lambda f, w: "<bar>", "")),
              "a stage with a counter keeps its seconds")
        PROGRESS.stop()
        # The same stage with its counter (`stage_counters` holds every stage to one): the bar line
        # shows the stage, its time in, its count and the count's age, inside 80 columns.
        pq = watch.Pulse(REDRAW_SECONDS)
        t_q = t0
        for i in range(150):
            el = (t_q - t0).total_seconds() + 30.0
            body = quiet + [f"  {i + 1}/400 weapons"]
            log9.write_text("\n".join(body) + "\n", encoding="utf-8")
            beat_q = pass_pulse(watch, pq, step9, watch.stage_progress(body, el), pfx, t_q, unit=False)
            t_q += dt.timedelta(seconds=REDRAW_SECONDS)
        # Then the counter stops: past many of its gaps the red mark, which must fit as well.
        t_dead = t_q + dt.timedelta(minutes=14)
        dead_q = pass_pulse(watch, pq, step9, watch.stage_progress(body, (t_dead - t0).total_seconds() + 30.0),
                            pfx, t_dead, unit=False)
        PROGRESS.start("pass")
        update_pass(head_q, lambda: body, plan9, hist9, now_q, watch)
        bl_q = ANSI.sub("", fit_frame(PROGRESS.bar_line(watch.bar, "step 2 of 2: loadout", beat_q), 80, 2))
        bl_d = ANSI.sub("", fit_frame(PROGRESS.bar_line(watch.bar, "step 2 of 2: loadout", dead_q), 80, 2))
        PROGRESS.stop()
        print(f"     counted: {bl_q}\n     stopped: {bl_d}")
        check("set coverage 5m15s 150/400, last step 0s ago" in bl_q and len(bl_q) <= 79,
              f"a counted stage shows its name, time in it, count and the count's age in 80 columns ({bl_q})")
        check("set coverage 19m17s 150/400, no progress for 14m" in bl_d and len(bl_d) <= 79,
              f"a stopped counter's red mark fits in 80 columns too ({bl_d})")
    frame = "\n".join(["head"] + [f"\033[32mstep {i} " + "x" * 30 + "\033[0m" for i in range(20)])
    fitted = fit_frame(frame, 20, 8).splitlines()
    check(len(fitted) == 7 and fitted[0] == "head" and ANSI.sub("", fitted[-1]).startswith("step 19")
          and max(len(ANSI.sub("", f)) for f in fitted) == 19,
          "fit_frame keeps the header and the last lines within the screen, each cut to one column short")
    st = {"vig": 58, "mnd": 8, "end": 31, "str": 15, "dex": 22, "int": 7, "fth": 8, "arc": 80}
    check(stats_text(st, 150) == "vig=58,mnd=8,end=31,str=15,dex=22,int=7,fth=8,arc=80,rl=150",
          "stats_text spells the planner keys in the pipeline's order and adds the RL")
    groups = {"A": 1, "A+1": 1, "B": 2, "C": None, "D": 3, "E": None}

    def group_of(n):
        return groups[n]

    check(fill_talismans(["A", "A+1", "B"], ["C", "D", "E"], group_of) == ["A", "B", "C", "D"],
          "a second talisman of one accessoryGroup is skipped, and the fallback fills the rest")
    check(fill_talismans(["A", "unknown"], ["A", "B"], group_of) == ["A", "B"],
          "a name the gear table does not know is skipped, and a repeat is not taken twice")
    check(fill_talismans([], [], group_of) == [], "no candidates leaves the slots empty")
    check(talisman_spares(list(DUEL_TALISMANS)) == ["Crimson Amber Medallion +2", "Crimson Amber Medallion +1",
                                                    "Crimson Amber Medallion"],
          "a duel wearing Ritual Shield and Amber +3 carries +2, +1 and +0 for the swaps")
    check(talisman_spares(["Shard of Alexander"]) == [], "no Ritual Shield, no spare medallions")
    check(talisman_spares(list(DUEL_TALISMANS), duel=False) == [], "an invader carries no spare medallions")
    sep = {"skill": "Seppuku", "value": 27, "self_cost": {"spent": ["Ritual Shield Talisman"]}}
    worn = list(DUEL_TALISMANS)
    check(spent_by_self_damage({"slots": [sep]}, worn) == {"Ritual Shield Talisman": ["Seppuku"]},
          "a full-HP talisman loses its value beside a self-damage copy the build would use")
    check(spent_by_self_damage({"slots": [dict(sep, value=0)]}, worn) == {},
          "a self-damage copy worth nothing after its cost is not one the build uses")
    check(spent_by_self_damage({"slots": [{"skill": "War Cry", "value": 190}]}, worn) == {},
          "a buff that does not hurt its user leaves the talismans alone")
    bans = ladder_bans()

    def ladder_group(n):
        if bans.talisman_ban(n):
            raise KeyError(n)
        return None

    check(fill_talismans(["Two-Handed Sword Talisman", "Blessed Dew Talisman", "Shard of Alexander",
                          "Dagger Talisman", "Lacerating Crossed-Tree"], [], ladder_group) == ["Lacerating Crossed-Tree"],
          "a duel's talisman picker skips the banned talismans (10-3 and 9-27) and a regen one")
    ex = loadout_exclude(bans, ["Erdtree's Favor"])
    check("Muleta" in ex["ashes"] and "Parry" in ex["ashes"] and {"Milady", "Buckler", "Pata", "Sword Lance"}
          <= set(ex["weapons"]) and ["Cleanrot Knight's Sword", None, "2H"] in ex["rows"],
          "the loadout exclusion holds the banned weapons, classes, skills and grip-only bans")
    doc = {"stats": {"rl": 150}, "inventory": {"slots": [
        {"name": "Uchigatana", "weaponArt": "Unsheathe", "equipIndex": 0},
        {"name": "Milady", "weaponArt": "Impaling Thrust", "equipSet": [None, 0]}]}}
    check(bans.check(doc) == ["RL 150"] and bans.check(doc, every=True, ladder_rl=None) == ["Milady"],
          "the upload gate judges every set's weapons and leaves the rune level to the caller")
    sets = {40: None, 41: None, 42: "set"}
    check(first_fit(30, 99, lambda e: 40 + max(0, e - 30), lambda b: sets.get(b)) == (32, "set"),
          "first_fit returns the least Endurance whose budget fits a set")
    check(first_fit(30, 31, lambda e: e, lambda b: None) is None, "first_fit is None when no Endurance up to the cap fits")
    kit = Kit.__new__(Kit)
    kit.stats, kit.classes = ("str", "dex", "int", "fth", "arc"), [3, 5]
    # Weapon 0 is wieldable at STR 20 DEX 12; weapon 1 needs STR 30, which two-handing meets at 20;
    # weapon 2 needs DEX 40 and is out of reach. Weapons 0 and 1 share class 3, weapon 2 is class 5.
    kit.need1 = np.array([[10, 10, 0, 0, 0], [30, 10, 0, 0, 0], [10, 40, 0, 0, 0]])
    kit.need2 = np.array([7, 20, 7])
    kit.share, kit.cls = np.array([0.5, 1.0, 1.0]), np.array([0, 0, 1])
    st = {"str": 20, "dex": 12, "int": 7, "fth": 8, "arc": 9}
    wield, semi = kit.usable(st)
    check(list(wield) == [True, False, False] and list(semi) == [False, True, False],
          "wieldable means every requirement met one-handed; semi-wieldable misses only STR, met two-handed")
    check(abs(kit.value(st) - 0.5) < 1e-9, "the kit's value is the mean over classes of the best usable share")
    check(abs(kit.value(dict(st, str=19)) - 0.25) < 1e-9, "one STR point short of two-handing drops the class to its weaker weapon")

    class Tables:
        weapons = {1: {"weight": 10.0}, 2: {"weight": 2.5}, 3: {"weight": 13.0}}

        @staticmethod
        def find_weapon(name, affinity=None):
            return {"Poleblade of the Bud": 1, "Forked Hatchet": 2, "Greatbow": 3}[name]

    plan = {"slots": [{"weapon": "Poleblade of the Bud", "affinity": "Standard"},
                      {"weapon": "Forked Hatchet", "affinity": "Occult"}, {"weapon": "Greatbow", "affinity": "Standard"}],
            "sets": [{"name": "Focus", "right": [0, 2], "left": [1]}, {"name": "Pair", "right": [0], "left": [1]}]}
    heavy = heaviest_set(plan, Tables)
    check(heavy == {"name": "Focus", "weapons": ["Poleblade of the Bud", "Greatbow", "Forked Hatchet"], "weight": 25.5},
          "heaviest_set sums both hands and their swaps and keeps the heaviest set")
    check(holds_weapon(plan, "Poleblade of the Bud") and not holds_weapon(plan, "Dagger"),
          "a plan holds the weapon only when an equip set carries it")
    # A powerstance on two copies of one weapon (`er-mechanics-combo-loadout.second_copy`): two
    # slots of Forked Hatchet, one per hand, weigh twice and both stay on the duel page.
    pair = {"slots": [{"weapon": "Forked Hatchet", "affinity": "Heavy", "theme": "Combos"},
                      {"weapon": "Forked Hatchet", "affinity": "Heavy", "theme": "Combos"},
                      {"weapon": "Greatbow", "affinity": "Standard", "theme": "Classes"}],
            "sets": [{"name": "Axe + Axe", "right": [0], "left": [1]}]}
    check(heaviest_set(pair, Tables)["weight"] == 5.0
          and duel_page(pair, slots=2)["sets"] == [{"name": "Axe + Axe", "right": [0], "left": [1]}],
          "two copies of one weapon: weight counted twice, both copies kept on the duel page")

    class Focus:
        def score(self, s):
            return 100.0 + s["arc"]

    sc = KitScorer(Focus(), 200.0, kit)
    check(abs(sc.score(st) - (109 / 200 + 0.5)) < 1e-9 and sc.cap == {},
          "KitScorer adds the focus hit over its best to the kit's value, with no stat cap")
    big = {"slots": [{"weapon": w, "theme": t} for w, t in
                     [("A", "Quick")] * 3 + [("F", "Focus"), ("F", "Focus")] + [("L", "Landmark: L x")] * 15
                     + [("B", "Combos"), ("C", "Classes"), ("A", "Classes"), ("M", "Crit")]],
           "sets": [{"name": "Focus", "right": [3], "left": [4]}, {"name": "Far", "right": [21], "left": [22]}]}
    pg = duel_page(big, slots=5)
    check([s["weapon"] for s in pg["slots"]] == ["F", "F", "C", "A", "M"] and len(pg["sets"]) == 2
          and pg["sets"][1] == {"name": "Far", "right": [2], "left": [3]},
          "duel_page fills one copy per weapon by theme priority, skips landmarks, and renumbers the sets")
    two = {"slots": [{"weapon": w, "affinity": f, "theme": "Classes", "value": v} for w, f, v in
                     (("S", "Heavy", 95), ("S", "Blood", 120), ("B", "Heavy", 108), ("P", "Heavy", 50))],
           "sets": []}
    pg = duel_page(two, slots=3, leads=[{"weapon": "P", "affinity": "Heavy"}, {"weapon": "S", "affinity": "Heavy"}],
                   cls=lambda w: "spear" if w == "P" else "curved")
    check([(s["weapon"], s["affinity"]) for s in pg["slots"]] == [("P", "Heavy"), ("S", "Heavy")],
          "duel_page takes each class's lead in lead order, on the lead's affinity, and no other weapon "
          "of that class")
    # Covering sets (`er-mechanics-combo-loadout.cover_sets`): C is on the page from Combos, its
    # best set C + K is in the plan's set block (slots 6, 7); K is not on the page, so the page grows
    # past its 4 slots for it, and C keeps the set copy only. Z's set brings nothing: Z is not on the
    # page. A covering set never sets the stats' weight budget.
    cov = {"slots": [{"weapon": w, "theme": t, "affinity": "Heavy"} for w, t in
                     [("F", "Focus"), ("F", "Focus"), ("C", "Combos"), ("A", "Classes"), ("Z", "Fun"),
                      ("Q", "Quick"), ("C", "Sets"), ("K", "Sets"), ("Z", "Sets")]],
           "sets": [{"name": "Focus", "right": [0], "left": [1]},
                    {"name": "C + K", "right": [6], "left": [7], "cover": True, "covers": ["C", "K"]},
                    {"name": "Z two-handed", "right": [8], "left": [], "cover": True, "covers": ["Z"]}]}
    pg = duel_page(cov, slots=4)
    check(([s["weapon"] for s in pg["slots"]], [s["name"] for s in pg["sets"]], pg["page_growth"]["added"],
           pg["sets"][1]["right"] + pg["sets"][1]["left"])
          == (["F", "F", "C", "K", "Q"], ["Focus", "C + K"], 1, [2, 3]),
          "duel_page: a page weapon brings its best set, the page grows by its partner, one copy of the weapon")

    class CovTables:
        weapons = {1: {"weight": 1.0}, 2: {"weight": 30.0}}

        @staticmethod
        def find_weapon(name, affinity=None):
            return 2 if name == "Z" else 1
    check(heaviest_set(cov, CovTables)["name"] == "Focus",
          "heaviest_set leaves the covering sets out of the stats' weight budget")
    check(damage_term({"affinity_share": 0.8, "kit": 0.98}) == 0.8
          and damage_term({"focus_hit": 300.0, "focus_best": 400.0, "kit": 0.9}) == 0.75,
          "damage_term is the affinity share around no weapon and the focus hit over its best around one")

    # A toy build at 100 points of Vigor + Endurance: damage falls 1% a point, HP rises 20 a Vigor
    # point to 60 and 5 after, and Endurance 40 is the least at which the 89-poise set fits; below
    # it only a 60-poise set does, which keeps more damage. "Lion" is worn at 40 and up, a piece
    # adding 2 Vigor that leaves less room for absorption.
    def damage_at(v, e):
        return 1.0 - 0.01 * (v + e - 90), {"stats": {"vig": v, "end": e, "str": 200 - v - e}}

    def options_at(e):
        # `pieces` names what `survive` reads (the HP piece and the absorption), as a real set's
        # pieces fix both, so the survive memo under them is sound for the toy too.
        if e < 40:
            opts = [{"floored": False, "wear": None, "kept": 0.70, "armor_poise": 60}]
        else:
            opts = [{"floored": True, "wear": None, "kept": 0.66 - 0.002 * (e - 40), "armor_poise": 89},
                    {"floored": True, "wear": "Lion", "kept": 0.68 - 0.002 * (e - 40), "armor_poise": 89}]
        return [{**o, "pieces": [o["wear"] or "-", f"kept {o['kept']!r}"]} for o in opts]

    def hp(v):
        return 1900 - 20 * (60 - v) if v <= 60 else 1900 + 5 * (v - 60)

    def survive(st, o):
        return hp(st["vig"] + 2 * (o["wear"] == "Lion")), 100.0 * o["kept"]

    got = joint_pick(range(50, 66), range(30, 46), damage_at, options_at, survive)
    b = got["best"]
    check(b["end"] == 40 and b["option"]["floored"],
          "joint_pick takes the least Endurance whose set meets the floor when more buys only a little absorption")
    check(b["vig"] + 2 * (b["option"]["wear"] == "Lion") < 60,
          "joint_pick trades Vigor for damage while a damage point is worth more than the HP it costs")
    check(all(c["value"] <= b["value"] for c in got["by_wear"].values()) and set(got["by_wear"]) == {None, "Lion"},
          "joint_pick keeps the best candidate per HP piece and the overall best is the best of them")
    check(got["any_poise"]["value"] >= b["value"] and got["any_poise"]["end"] < 40,
          "joint_pick reports the best set below the floor without choosing it")
    check(joint_pick([60], [30], damage_at, options_at, survive)["best"] is None,
          "joint_pick has no best when no floor-poise set fits")
    check(joint_pick([60], [40], lambda v, e: (1.0, {"stats": {"vig": v + 1, "end": e}}), options_at,
                     survive)["best"] is None,
          "a spread whose Vigor and Endurance are not the pinned split is skipped")
    calls = []

    def counted(v, e):
        calls.append(v + e)
        return damage_at(v, e)
    shared = {}
    joint_pick(range(55, 60), range(38, 42), counted, options_at, survive, shared)
    n1 = len(calls)
    joint_pick(range(55, 60), range(38, 42), counted, options_at, survive, shared)
    check(n1 > 0 and len(calls) == n1, "joint_pick asks damage_at nothing a shared cache already holds")
    # The survive memo: a shared cache asks `survive` once per (Vigor, Endurance, pieces), and a
    # call through it chooses exactly what a call with no cache does.
    asked = []

    def survive_counted(st, o):
        asked.append((st["vig"], st["end"], tuple(o["pieces"])))
        return survive(st, o)
    shared = {}
    first = joint_pick(range(50, 66), range(30, 46), damage_at, options_at, survive_counted, shared)
    n2 = len(asked)
    again = joint_pick(range(50, 66), range(30, 46), damage_at, options_at, survive_counted, shared)
    plain = joint_pick(range(50, 66), range(30, 46), damage_at, options_at, survive)
    check(n2 == len(set(asked)) > 0 and len(asked) == n2 and first == again == plain,
          "joint_pick asks survive once per Vigor, Endurance and pieces through a shared cache, and chooses "
          "what it chooses without one")
    wc = weight_cost([0, 1, 2, 3, 4, 5], lambda w: {0: None, 1: 4.0, 2: 5.0, 3: 2.5, 4: None, 5: 1.0}[w])
    check(wc["weights"] == [0, 1, 2, 3] and wc["value"] == [0.0, 0.8, 1.0, 0.5] and wc["raw"][2] == 5.0,
          "weight_cost: each weight over the best, 0 where nothing fits, and the walk stops at the first "
          "weight past a fitting one that fits nothing")
    rows_ = [{"pvp": True, "archetype": "str", "stats": {"vig": 60, "end": 36}},
             {"pvp": True, "archetype": "dex", "stats": {"vig": 50, "end": 20}},
             {"pvp": False, "archetype": "str", "stats": {"vig": 99, "end": 99}}]
    cs = corpus_survival(rows_, "str")
    check(cs["pvp"]["builds"] == 2 and cs["pvp"]["end_median"] == 28 and cs["str"]["vig_mean"] == 60
          and cs["str"]["builds"] == 1, "corpus_survival reads the PvP builds only, all and one archetype's")
    leads = [{"weapon": "Dagger", "affinity": "Blood", "class": "Dagger", "score": 0.73},
             {"weapon": "Twinblade", "affinity": "Heavy", "class": "Twinblade", "score": 0.72},
             {"weapon": "Shamshir", "affinity": "Heavy", "class": "Curved Sword", "score": 0.70}]
    check(roles_lead(leads, "Heavy")["weapon"] == "Twinblade" and roles_lead(leads)["weapon"] == "Dagger"
          and roles_lead(leads, "Heavy", banned=lambda w: w == "Twinblade" or None)["weapon"] == "Shamshir"
          and roles_lead(leads, "Cold") is None,
          "roles_lead takes the first lead at the pinned affinity that is not banned")
    cr = _mod("er_mechanics_class_roles", "er-mechanics-class-roles.py")
    # The movement R1s (`cr.R1_MOVE_FEATURES`) carry half the string's frame and damage weight
    # (`cr.main_weights`), so each weapon's are its string's: a slow string is a slow dash R1 too.
    def moves(st, rec, dmg):
        return {f: {"startup": st, "recovery": rec, "damage": dmg}[f.rsplit("_", 1)[1]] for f in cr.R1_MOVE_FEATURES}
    fast = {"r1_recovery": 20, "r1_reach": 2.5, "r1_startup": 12, "ar": 300, "r1_chain_dps": 100, "ash_value": 1.0,
            "stamina": 10, "crit": 400, **moves(12, 20, 100)}
    roles = {"weapons": {w: {"main_hand": {"by_affinity": {"Heavy": {"features": f}}}} for w, f in (
        ("Crozier", dict(fast, r1_recovery=40, r1_startup=30, ar=600, crit=600, r1_chain_dps=60,
                         **moves(30, 40, 60))),
        ("Twinblade", fast), ("Nothing", dict(fast, ar=1)))}, "classes": {}}
    check(kit_lead({"Hammer": (0.9, "Crozier"), "Twinblade": (0.8, "Twinblade")}, roles, cr, "Heavy") == "Twinblade"
          and kit_lead({"Spear": (0.9, "Unscored")}, roles, cr, "Heavy") is None,
          "kit_lead ranks the class leads by the weighted main-hand score, not by the hardest hit")
    # A duel assumes no banned grease (the 2026-10-19 upload refused Drawstring Dragonbolt Grease).
    check(fight_grease("duel") == (("elemental",), None, "none")
          and fight_grease("invasion") == (SWEEP_KEYS, "dlc-drawstring", "dlc-drawstring"),
          "a duel's stats assume no grease: the no-grease sweep's ungreased build and no weapon_best tier")
    infs = _mod("er_mechanics_infusions", "er-mechanics-infusions.py")
    check(infs.sweep_path(125, "none").name == "grease-sweep-none-125-125-every-class-cond-floors.jsonl"
          and infs.sweep_command(125, "none", 8)[-2:] == ["--grease", "none"],
          "the duel's sweep has its own file and a command that writes it with no grease")
    bans = ladder_bans()
    plan = {"slots": [{"weapon": "Shamshir", "grip": "2H"}, {"weapon": "Dagger", "grip": "1H"},
                      {"weapon": "Longsword", "grip": "1H"}],
            "sets": [{"right": [0], "left": [1]}, {"right": [2], "left": []}]}
    ban2h = duel_buff_ban(bans, "Shamshir", True, plan)
    check(ban2h("Poison Grease", "consumable") is not None and ban2h("Bloodflame Blade", "spell") is not None,
          "duel_buff_ban: an off-hand Dagger beside the focus bans every weapon buff, a grease or a spell")
    ban1h = duel_buff_ban(bans, "Longsword", False, plan)
    check(ban1h("Poison Grease", "consumable") is None and ban1h("Bloodflame Blade", "spell") is None,
          "duel_buff_ban: a 1H Longsword with an empty off hand allows a status grease and a buff spell")
    # A buff skill is a weapon buff (`10-3 [ BUFF RESTRICTIONS ]`), judged in each set it is in.
    check(weapon_buff_ash("War Cry", "Wakizashi", "1H") and not weapon_buff_ash("Storm Blade", "Wakizashi", "1H")
          and not weapon_buff_ash("Golden Vow", "Longsword", "1H"),
          "weapon_buff_ash: War Cry buffs the weapon (1810 -> 162); Storm Blade and Golden Vow (body, 160) do not")
    bplan = {"slots": [{"weapon": "Wakizashi", "grip": "1H", "ash": "War Cry"},
                       {"weapon": "Longsword", "grip": "2H", "ash": "War Cry"},
                       {"weapon": "Wakizashi", "grip": "1H", "ash": "Storm Blade"},
                       {"weapon": "Longsword", "grip": "1H", "ash": "War Cry"},
                       {"weapon": "Dagger", "grip": "1H", "ash": "Quickstep"}],
             "sets": [{"right": [1], "left": []}, {"right": [3], "left": [4]}]}
    got = duel_buff_ash_bans(bans, bplan)
    check(0 in got and 1 not in got and 2 not in got,
          "duel_buff_ash_bans: War Cry on a 1H main-hand Wakizashi is refused, on a 2H Longsword allowed, "
          "and Storm Blade (no buff) on a 1H Wakizashi allowed")
    check(3 in got and "Offhand Daggers" in got[3] and 4 not in got,
          "duel_buff_ash_bans: War Cry on a Longsword with a Dagger in the off hand is refused; the dagger's "
          "own Quickstep is no buff and stays")
    kept = refuse_slots(bplan, got)
    check([s["ash"] for s in kept["slots"]] == ["War Cry", "Storm Blade", "Quickstep"]
          and kept["sets"] == [{"right": [0], "left": []}],
          "refuse_slots: the refused copies and the sets wearing them go, the rest are renumbered")
    check(bans.tool_ban("Drawstring Dragonbolt Grease") is not None and bans.tool_ban("Poison Grease") is None,
          "the duel pouch's exclude (bans.tool_ban) refuses a damage grease and keeps a status grease")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
