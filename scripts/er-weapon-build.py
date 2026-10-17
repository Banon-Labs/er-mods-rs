#!/usr/bin/env python3
"""A whole planner build made around one weapon, uploaded as a new `?b=` link.

    python3 scripts/er-weapon-build.py "Poleblade of the Bud"
    python3 scripts/er-weapon-build.py Uchigatana --rl 125 --two-handed
    python3 scripts/er-weapon-build.py "Poleblade of the Bud" --dry-run    # stop before the pipeline
    python3 scripts/er-weapon-build.py "Poleblade of the Bud" --no-upload  # every pass, no upload
    python3 scripts/er-weapon-build.py --selftest

Each part comes from the tool that owns it:

| part | source |
| --- | --- |
| the weapon's affinity, grease and level | `er-builds-optimize.weapon_best`: the spread that hits hardest with the weapon alone, whose hit is the yardstick below |
| class and stats | `kit_spread`: every requirement met, Vigor from the window's PvP pool, Mind and Endurance following the spread (`spend_floored`), and every other point where it adds most to the build's value (`KitScorer`): the weapon's hit over its best, plus the kit's share (`Kit`), one equal part each |
| talismans | `er-mechanics-gear-synergy.rank`: its talisman rows, best first, one per `accessoryGroup`; slots it leaves empty take what builds holding the weapon wear most (`er-builds-optimize.gear`) |
| armor | `er-mechanics-armor-targets.ArmorModel`: the set that keeps least of the window's PvP damage with at least `melee_poise_floor` of the armor's own poise, at medium roll with the talismans and the weapons the build carries. When no set fits, Endurance rises a point at a time and the stats are chosen again with it pinned |
| what the build carries | the pipeline's own plan (`--no-upload`): its heaviest equip set, both hands and their swaps (`heaviest_set`). The first pass can only price the weapon alone, so when the plan's heaviest set needs more Endurance the stats are re-chosen and the pipeline runs again, until a plan's heaviest set fits what it was budgeted for (`MAX_ROUNDS`); only then is anything uploaded |
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

Files go to `--dir` (default `~/.cache/er-build-planner/weapon-builds/<weapon>-rl<rl>/`): the base
build and, per stat block (`<weapon>-<hash of the stats>`), `-gear.json`, `-build.json` (what was
chosen and from what) and the pipeline's own files. The pipeline skips a step whose output is
newer than its inputs, so a re-run picks up where a stopped one left off. A pass runs for about
an hour from empty caches; the link prints last.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
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
#: `EquipParamWeapon` requirement field per planner stat key.
REQUIREMENT_FIELDS = {"str": "properStrength", "dex": "properAgility", "int": "properMagic",
                      "fth": "properFaith", "arc": "properLuck"}


#: Colour only on a terminal, and never under `NO_COLOR`; `weapon-build.log` always gets plain text.
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RESET, BOLD = ("\033[0m", "\033[1m") if COLOR else ("", "")
GREEN, YELLOW, RED, GREY = ("\033[32m", "\033[33m", "\033[31m", "\033[90m") if COLOR else ("",) * 4
ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")
#: Seconds between redraws of the pipeline's live view (`er-mechanics-pipeline-watch.frame`).
REDRAW_SECONDS = 2.0
_LOG = []


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}h{s // 60 % 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


#: The run's phases in order, as the status line numbers them: (key, text its ✓ line holds).
PHASES = (("base", "base build"), ("spread", "own best spread"), ("kit", "stats for the whole kit"),
          ("talismans", "talismans"), ("buffs", "great rune and crystal tears"),
          ("armor", "armor and Endurance"), ("affinity", "affinity for the other weapons"),
          ("pass", "pass"), ("pouch", "tools, flasks and spells"), ("upload", "upload"))
#: Seconds a phase takes when no earlier run in `weapon-build.log` says (the pass is the 1h49m
#: measured on Poleblade of the Bud, 2026-10-14).
PHASE_DEFAULT_S = {"base": 1, "spread": 5, "kit": 5, "talismans": 180, "buffs": 15, "armor": 15, "affinity": 1,
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
        self.passes = 0

    def start(self, key):
        self.key, self.t0, self.part = key, time.monotonic(), None
        self.passes += key == "pass"

    def stop(self):
        if self.t0 is not None:
            self.spent += time.monotonic() - self.t0
        self.key, self.t0 = None, None

    def line(self) -> str:
        if self.key is None:
            return ""
        keys = [k for k, _ in PHASES]
        n = keys.index(self.key) + 1
        el = time.monotonic() - self.t0
        exp = self.expected[self.key]
        frac = self.part if self.part is not None else min(0.99, el / exp) if exp else 0.0
        now_left = max(0.0, exp * (1 - frac))
        later = sum(self.expected[k] for k in keys[n:] if k != "upload" or not NO_UPLOAD[0])
        done = self.spent + el
        total = done + now_left + later
        what = dict(PHASES)[self.key] + (f" {self.passes} of at most {MAX_ROUNDS}" if self.key == "pass" else "")
        return (f"{BOLD}[{n}/{len(PHASES)}] {what}{RESET}  {GREEN}{100 * frac:.0f}%{RESET} of it, "
                f"{clock(el)} of ~{clock(exp)}  {GREY}|{RESET}  run {GREEN}~{100 * done / total:.0f}%{RESET}, "
                f"~{clock(total - done)} left")


PROGRESS = Progress({})
NO_UPLOAD = [False]


class Ticker:
    """Redraws the status line in place on a terminal while a phase runs, between `say` lines."""

    def __init__(self):
        self.shown = False
        self.paused = False
        self.stopped = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        # Nothing signals "a second went by" for the clocks the line shows, so the redraw period is
        # the cadence; `stopped` ends the loop at once instead of a period later.
        while not self.stopped.wait(REDRAW_SECONDS):
            if COLOR and not self.paused and PROGRESS.key:
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


def run_pass(cmd: list[str], d: Path, prefix: str, watch) -> int:
    """Run one pipeline pass with its output in `<prefix>-pipeline.log`, the file
    `er-mechanics-pipeline-watch.py` reads, and redraw that tool's live view in place (a progress
    bar per step, elapsed time and what is left) until the pass ends; off a terminal, print only
    the last view."""
    log = d / f"{prefix}-pipeline.log"
    hist = STEP_HISTORY
    plan = [s["id"] for s in watch.steps(d / prefix) if s["id"] != "10"]
    labels = {s["id"]: s["label"] for s in watch.steps(d / prefix)}
    if hist:
        PROGRESS.expected["pass"] = sum(v for k, v in hist.items() if k in plan)
    PROGRESS.start("pass")
    if TICKER:
        TICKER.paused = True
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
        drawn = 0
        while True:
            # The pipeline's exit ends the wait; a redraw period without it redraws the frame.
            try:
                proc.wait(timeout=REDRAW_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            if COLOR:
                now = dt.datetime.now()
                PROGRESS.part, n, sid = pass_progress(watch.read_lines(log), plan, hist, now)
                head = PROGRESS.line() + (f"  {GREY}|{RESET}  step {n} of {len(plan)}: {labels.get(sid, sid)}"
                                          if n else "")
                size = shutil.get_terminal_size()
                text = fit_frame(head + "\n" + watch.frame(d, prefix, now), size.columns, size.lines)
                with _OUT:
                    sys.stdout.write((f"\033[{drawn}F\033[J" if drawn else "") + text + "\n")
                    sys.stdout.flush()
                drawn = text.count("\n") + 1
    PROGRESS.stop()
    if TICKER:
        TICKER.paused = False
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
    """The pipeline's `--stats`: planner keys, `vit` being Endurance, and the RL they sum to."""
    return ",".join(f"{k}={stats[k]}" for k in ("vig", "mnd", "vit", "str", "dex", "int", "fth", "arc")) + f",rl={rl}"


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


class Kit:
    """The weapons a build can carry besides its focus, by weapon class, from the every-class
    grease sweep at one RL (`er-mechanics-infusions.load_sweep`)."""

    def __init__(self, opt, rows, tables):
        best = {}
        for row in rows:
            d = max((row[k]["dmg"] for k in ("elemental", "greased", "quality") if row.get(k)), default=None)
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
        return self.focus.score(st) / self.focus_best + self.kit.value(st)


def kit_spread(opt, tables, weapon: str, two_handed: bool, rl: int, search: dict, kit: Kit, pins=None) -> dict | None:
    """The spread, over every starting class, that maximises `KitScorer` with the focus weapon at
    `search`'s best affinity, grease and level, its requirements met, and `search`'s floors (with
    `pins` fixed)."""
    best = search["results"][0]
    model = opt.RES.Model()
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
    for cls in opt.RES.CLASS_ROWS:
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
    (`er-mechanics-combo-loadout.py` writes `sets` as slot indices per hand)."""
    top = None
    for s in plan["sets"]:
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


def pick_talismans(opt, weapon: str, affinity: str, first: list[str] = ()) -> list[str]:
    """`first` (the spell rule's picks) take their slots before the weapon's own ranking."""
    syn = _mod("er_mechanics_gear_synergy", "er-mechanics-gear-synergy.py")
    d, pop, gear, tables = syn.build()
    ranked = list(first) + [r["name"] for r in syn.rank(d, pop, gear, tables, weapon, top=100)["gear"]
                            if r["kind"] == "talisman"]
    fallback = opt.gear(weapon, affinity, opt.CACHE / "embeddings.npz", FALLBACK_TALISMANS).get("talisman", [])

    def group_of(name):
        item = gear.items[name]
        if item["kind"] != "talisman":
            raise KeyError(name)
        g = item.get("group")
        return g if g and g > 0 else None

    return fill_talismans(ranked, fallback, group_of)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapon", nargs="?", help="the weapon the build is made around, as the game names it")
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--two-handed", action="store_true", help="optimize the stats for two-handing it")
    ap.add_argument("--window", type=int, default=10, help="corpus RL window is rl +- this")
    ap.add_argument("--fight", choices=("invasion", "duel"), default="invasion",
                    help="fight model the gear is scored in (er-mechanics-buffs FIGHT_KINDS); invasion: "
                         "the invader against three hosts and two co-invaders, 10 s to 1 h")
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
    ap.add_argument("--no-upload", action="store_true", help="run every pipeline pass, then print the upload instead")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error("name a weapon")
    pins = {}
    for kv in filter(None, a.pin.split(",")):
        k, _, v = kv.partition("=")
        if k not in ("vig", "mnd", "vit", *REQUIREMENT_FIELDS) or not v.isdigit():
            ap.error(f"--pin takes vig,mnd,vit,{','.join(REQUIREMENT_FIELDS)} as key=value, not {kv!r}")
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
    wid = tables.find_weapon(a.weapon)
    out = a.dir or opt.CACHE / "weapon-builds" / f"{slug(a.weapon)}-rl{a.rl}"
    out.mkdir(parents=True, exist_ok=True)
    global TICKER
    history = out / "weapon-build.log"
    past = history.read_text(encoding="utf-8").splitlines() if history.exists() else []
    PROGRESS.expected.update(phase_history(past))
    STEP_HISTORY.update(step_history(past))
    NO_UPLOAD[0] = a.no_upload
    if COLOR:
        sys.stdout, sys.stderr = StatusOut(sys.stdout), StatusOut(sys.stderr)
    TICKER = Ticker()
    _LOG.append(open(history, "a", encoding="utf-8"))
    say(f"{BOLD}{a.weapon}{RESET}, RL {a.rl}{', two-handed' if a.two_handed else ''}: files in {out}", "●")

    with phase(f"base build {a.base}", "base"):
        base = fetch_build(a.base)
        # The base's own weapons are another build's picks: `er-mechanics-combo-loadout.py` keeps
        # every weapon in `--build`'s inventory in its pool beside the role picks, so the default
        # base would hand this build the STR 18 / DEX 80 build's 100 weapons, 10 of them out of
        # reach at Poleblade of the Bud's stats. The upload rebuilds the inventory from the plan.
        base["inventory"] = {**(base.get("inventory") or {}), "slots": []}
        base_path = out / "base.json"
        write_if_changed(base_path, json.dumps(base, indent=1))

    with phase(f"{a.weapon}'s own best spread", "spread"):
        rows = opt.corpus_rows(opt.CACHE / "builds.jsonl", a.rl - a.window, a.rl + a.window)
        search = opt.weapon_best(tables, rows, a.weapon, a.rl, a.two_handed,
                                 affinity=[a.affinity] if a.affinity else None)
        if not search["results"]:
            sys.exit(f"no class reaches RL {a.rl} with {a.weapon}'s requirements and floors {search['floors']}")
        best = search["results"][0]
        say(f"{best['affinity']} +{best['level']}, one hit {best['score']:.0f} at {stats_text(best['stats'], a.rl)}")
        kit = Kit(opt, inf.load_sweep(a.rl), tables)

    def show(sp):
        say(f"{sp['class']}: {BOLD}{stats_text(sp['stats'], a.rl)}{RESET}")
        say(f"{a.weapon} hits {sp['focus_hit']:g}, {100 * sp['focus_hit'] / sp['focus_best']:.0f}% of its own "
            f"best {sp['focus_best']:g}; kit {100 * sp['kit']:.0f}%: {sp['wieldable']} wieldable and "
            f"{sp['semi_wieldable']} semi-wieldable of {sp['kit_weapons']}")

    with phase("stats for the whole kit", "kit"):
        spread = kit_spread(opt, tables, a.weapon, a.two_handed, a.rl, search, kit, pins=pins or None)
        if spread is None:
            sys.exit(f"no class reaches RL {a.rl} with {a.weapon}'s requirements and floors {search['floors']}")
        show(spread)

    with phase("talismans (gear synergy, a few minutes)", "talismans"):
        spell_picks = []
        if a.spell:
            rule = spell_rule(a.spell[0], a.weapon, spread["stats"])
            spell_picks = rule["added"]
        talismans = pick_talismans(opt, a.weapon, best["affinity"], first=spell_picks)
        say(", ".join(talismans))

    # Before the armor: the great rune is part of the equip load and HP the armor is fitted to.
    with phase("great rune and crystal tears", "buffs"):
        buffs = rune_tears.pick(a.weapon, best["affinity"], best["level"], spread["stats"], a.two_handed,
                                rl=a.rl, window=a.window, talismans=talismans, fight=a.fight)
        great_rune, tears = buffs["great_rune"], list(buffs["crystal_tears"])
        # An invader's great rune is inert (buffs.md section 4), so the HP and equip load the
        # armor is fitted to leave it out; the planner build still carries it for hosting.
        rune_on = great_rune if rune_tears.B.FIGHT_KINDS[a.fight]["role"] == "host" else None
        say(f"{great_rune or 'no great rune'}{'' if rune_on or not great_rune else ' (inert for the invader)'}; "
            f"{' and '.join(tears)}")
        for item, notes in (buffs.get("unvalued") or {}).items():
            if notes:
                say(f"{item}: {'; '.join(notes)}", " ", GREY)

    caster = class_best.kind(tables.weapons[wid]["wepType"]) == "casting"
    model = opt.RES.Model()

    def budget_at(stats, armor, carried):
        def at(end):
            load = model.resources(dict(stats, vit=end), talismans, (), rune_on)["max_equip_load"]
            return opt.MEDIUM_ROLL_MAX * load - carried - armor.talisman_weight
        return at

    floor = None

    def fit_armor(spread, carried, what):
        """(spread, armor set) with the least Endurance from `spread`'s at which a `floor`-poise
        set keeps medium roll while `carried` weighs on it; a raised Endurance re-chooses the
        stats with it pinned."""
        nonlocal floor
        with phase(f"armor and Endurance for {what}", "armor"):
            armor = armor_mod.ArmorModel(spread["stats"], talismans, rune_on, a.rl, a.window)
            if floor is None:
                floor = 0 if caster else armor.melee_floor
            fit = first_fit(spread["stats"]["vit"], opt.STAT_CAP, budget_at(spread["stats"], armor, carried),
                            lambda b: armor.pick(b, floor))
            if fit is None:
                sys.exit(f"no armor reaches {floor} poise at medium roll with {what} at any Endurance")
            end, chosen = fit
            if end != spread["stats"]["vit"]:
                say(f"Endurance {spread['stats']['vit']} -> {BOLD}{end}{RESET}: the lightest {floor}-poise set "
                    f"needs it for medium roll", " ", YELLOW)
                spread = kit_spread(opt, tables, a.weapon, a.two_handed, a.rl, search, kit, pins={**pins, "vit": end})
                if spread is None and pins:
                    # The `--pin` block plus the raised Endurance is past the RL, so the pinned
                    # stats give way and are chosen again around the Endurance the armor needs.
                    say(f"--pin {a.pin} no longer fits at RL {a.rl} with Endurance {end}; choosing the stats "
                        f"again without it", " ", YELLOW)
                    spread = kit_spread(opt, tables, a.weapon, a.two_handed, a.rl, search, kit, pins={"vit": end})
                if spread is None:
                    sys.exit(f"no class reaches RL {a.rl} with Endurance {end} and {a.weapon}'s requirements")
                show(spread)
                armor = armor_mod.ArmorModel(spread["stats"], talismans, rune_on, a.rl, a.window)
                chosen = armor.pick(budget_at(spread["stats"], armor, carried)(end), floor)
                if chosen is None:
                    sys.exit(f"no {floor}-poise set fits at Endurance {end} once the stats are re-chosen around it")
            say(f"{' / '.join(chosen['pieces'])}: weight {chosen['weight']:g}, poise {chosen['armor_poise']:g}, "
                f"keeps {100 * chosen['kept']:.1f}% of the window's PvP damage")
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

    name = a.name or f"{a.weapon} RL{a.rl}"

    def write_files(prefix, spread, chosen, other, carried):
        pieces = dict(zip(("head", "body", "arms", "legs"), chosen["pieces"]))
        up.planner_spelling([a.weapon] + talismans + [p for p in chosen["pieces"] if p != "-"] + tears
                            + [great_rune] * bool(great_rune))
        gear = {"protectors": [{"name": f"Medium roll, poise {chosen['armor_poise']:g}", **pieces}],
                "talismans": [{"name": a.weapon, "slots": talismans + [None] * (TALISMAN_SLOTS - len(talismans))}],
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
    carried = tables.weapons[tables.find_weapon(a.weapon, best["affinity"])]["weight"]
    spread, chosen = fit_armor(spread, carried, f"{a.weapon} alone")
    other = other_affinity(spread)
    for round_ in range(1, MAX_ROUNDS + 1):
        text = stats_text(spread["stats"], a.rl)
        # One prefix per stat block: the pipeline's freshness check reads files, not `--stats`.
        prefix = f"{slug(a.weapon)}-{hashlib.sha1(text.encode()).hexdigest()[:8]}"
        gear_path = write_files(prefix, spread, chosen, other, carried)
        cmd = ["bash", str(PIPELINE), "--prefix", prefix, "--class", spread["class"], "--stats", text,
               "--affinity", other, "--name", name, "--focus", f"{a.weapon}:{best['affinity']}",
               "--gear", str(gear_path), "--base", a.base, "--base-json", str(base_path), "--dir", str(out),
               "--no-upload"]
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
        if not holds_weapon(plan, a.weapon):
            say(f"no equip set of the plan carries {a.weapon}, so it is not a build around it; the loadout "
                f"log says why: {out / (prefix + '-loadout.log')}", "✗", RED)
            return 1
        heavy = heaviest_set(plan, tables)
        fits = heavy["weight"] <= carried + 1e-6
        say(f"heaviest equip set {heavy['name']}: {', '.join(heavy['weapons'])}, weight {heavy['weight']:g} "
            f"({'within' if fits else 'over'} the {carried:g} budgeted)", " ", "" if fits else YELLOW)
        if fits:
            break
        carried = heavy["weight"]
        new, chosen = fit_armor(spread, carried, f"{heavy['name']} ({carried:g}) equipped")
        if new["stats"] == spread["stats"]:
            gear_path = write_files(prefix, spread, chosen, other, carried)
            break
        spread = new
        other = other_affinity(spread)
    else:
        sys.exit(f"the heaviest equip set kept growing for {MAX_ROUNDS} rounds; the plans are in {out}")

    # After the plan: spells need a catalyst the plan holds. The pipeline's own gear file is a
    # freshness input of its first step, so these go into a separate file only the upload reads.
    with phase("tools, flasks and spells", "pouch"):
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        held = list(dict.fromkeys(x["weapon"] for x in plan["slots"]))
        # The pouch is scored on top of the rune and tears already picked, so the base it reads
        # carries those rather than the template's.
        kit_items = pouch.pick(a.weapon, best["affinity"], best["level"], spread["stats"], held, a.two_handed,
                               rl=a.rl, window=a.window, talismans=talismans, fight=a.fight,
                               armor=[p for p in chosen["pieces"] if p != "-"],
                               base={**base, "greatRune": great_rune,
                                     "items": {**base.get("items", {}), "crystalTears": tears}})
        say(f"tools {', '.join(t['name'] for t in kit_items['tools'])}; spells "
            f"{', '.join(kit_items['spells']) or 'none'}; flasks {kit_items['flasks']}")
        say("pots " + ", ".join(f"{p['name']} x{p['capacity']} ({p['hp']:.0f} HP a throw)"
                                for p in kit_items.get("pots", ())) + "; boluses "
            + (", ".join(b["name"] for b in kit_items.get("boluses", ()) if b["slotted"]) or "none"))
        for left_out in kit_items.get("skipped", ()):
            say(f"left out: {left_out['count']} {left_out['category']} ({left_out['why']})", " ", GREY)
    gear = json.loads(gear_path.read_text(encoding="utf-8"))
    gear.update(tools=kit_items["tools"], spells=list(dict.fromkeys(list(a.spell) + kit_items["spells"])),
                flasks=kit_items["flasks"], tags="derive")
    gear_path = out / f"{prefix}-upload-gear.json"
    gear_path.write_text(json.dumps(gear, indent=1), encoding="utf-8")
    upload = ["python3", str(HERE / "planner-ash-loadout-upload.py"), "--base", a.base, "--plan", str(plan_path),
              "--class", spread["class"], "--stats", stats_text(spread["stats"], a.rl), "--name", name,
              "--gear", str(gear_path)]
    if a.no_upload:
        say("upload (not run): " + " ".join(upload), "●")
        return 0
    with phase("upload", "upload"):
        # Waited on by its exit: each of its requests carries its own 20 s timeout
        # (`planner-ash-loadout-upload.request`), and a cap on the whole could cut it off between
        # storing the build and reading it back.
        rc = subprocess.Popen(upload).wait()
    return rc


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

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
    frame = "\n".join(["head"] + [f"\033[32mstep {i} " + "x" * 30 + "\033[0m" for i in range(20)])
    fitted = fit_frame(frame, 20, 8).splitlines()
    check(len(fitted) == 7 and fitted[0] == "head" and ANSI.sub("", fitted[-1]).startswith("step 19")
          and max(len(ANSI.sub("", f)) for f in fitted) == 19,
          "fit_frame keeps the header and the last lines within the screen, each cut to one column short")
    st = {"vig": 58, "mnd": 8, "vit": 31, "str": 15, "dex": 22, "int": 7, "fth": 8, "arc": 80}
    check(stats_text(st, 150) == "vig=58,mnd=8,vit=31,str=15,dex=22,int=7,fth=8,arc=80,rl=150",
          "stats_text spells the planner keys in the pipeline's order and adds the RL")
    groups = {"A": 1, "A+1": 1, "B": 2, "C": None, "D": 3, "E": None}

    def group_of(n):
        return groups[n]

    check(fill_talismans(["A", "A+1", "B"], ["C", "D", "E"], group_of) == ["A", "B", "C", "D"],
          "a second talisman of one accessoryGroup is skipped, and the fallback fills the rest")
    check(fill_talismans(["A", "unknown"], ["A", "B"], group_of) == ["A", "B"],
          "a name the gear table does not know is skipped, and a repeat is not taken twice")
    check(fill_talismans([], [], group_of) == [], "no candidates leaves the slots empty")
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

    class Focus:
        def score(self, s):
            return 100.0 + s["arc"]

    sc = KitScorer(Focus(), 200.0, kit)
    check(abs(sc.score(st) - (109 / 200 + 0.5)) < 1e-9 and sc.cap == {},
          "KitScorer adds the focus hit over its best to the kit's value, with no stat cap")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
