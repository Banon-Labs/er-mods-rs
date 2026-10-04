#!/usr/bin/env python3
"""The R3 weapon board of every weapon the item list can show, generated from the mechanics tools.

    python3 scripts/gen-r3-weapon-boards.py --jobs 3        # write the Rust (hours, resumable)
    python3 scripts/gen-r3-weapon-boards.py --only Misericorde   # one board, as Rust, to stdout
    python3 scripts/gen-r3-weapon-boards.py --check         # exit 1 when the file is stale
    python3 scripts/gen-r3-weapon-boards.py --selftest      # the text rules, on fixtures

Output: `crates/er-r3-view/src/weapon_boards.rs`, `WEAPON_BOARDS: &[(u32, Board)]` sorted by the
base weapon's `EquipParamWeapon` id, one `crate::board::Board` per weapon.

Where each part of a board comes from
-------------------------------------
These are the scripts `board.rs` names beside its hand-written Misericorde board, and this file
only arranges what they print:

| board field | source |
| --- | --- |
| `icon_id`, `class`, `name`, `rule` | `EquipParamWeapon` `iconId`, `wepType` (Smithbox's `WEP_TYPE` names), the row name, and `gemMountType` / `disableGemAttr` |
| `unique_intro`, `unique` | `er-mechanics-weapon-twins.py`: the comparable weapons (same class and build rules), their moveset match, and each advantage this weapon holds over them |
| `infusions_intro`, `infusions` | `er-mechanics-infusions.py`: `rank_text`, and the `top physical` / `top elemental` / `top status` rows |
| `speed` | `er-mechanics-weapon-card.py`: R1 first hit, reach and stamina, each ranked |
| `gear` | `er-mechanics-gear-synergy.py --top 6`: the powerstance pair, the critical gear when the critical is above the median, the six best rows after the setup discount, and Spear Talisman when any hit pierces |

The unique rows are this script's own arrangement of `weapon-twins`' advantages, and the rule is
the one the hand-written board followed: one row each for a motion value, a first-hit frame and a
build field (requirement, scaling, weight, stamina use), each the advantage held over the most
comparable weapons, ordered by that count. Numbered strings of one slot that share a value merge
("R2 #1 charged" and "R2 #2 charged", both 155, become "Charged R2s"). Up to four weapons are
named; more are counted ("seven of the nine other daggers that can be infused").

Which weapons
-------------
Every base row (`id % 10000 == 0`) the game's message files name (`er-builds-catalog.py`'s
`catalog.json`, as the optimizer reads it), less the classes `er-builds-optimize.py`'s RL 150
sweep skips (`SWEEP_SKIP_WEP_TYPES`): ammunition, consumables, bows, crossbows, ballistae, staves,
seals and perfume bottles. The infusion section ranks a weapon by one melee hit against every
other weapon's, and those classes are not in that ranking, so their board would carry a build
section measured on a hit the weapon is not used for. A weapon that a source script cannot
evaluate is left out and named in the generated header.

Cost, and why `--check` does not regenerate
--------------------------------------------
The infusion section runs the build optimizer at fifteen rune levels for every affinity, about
450 CPU-seconds per weapon, so a full run is hours. What the four scripts print for each weapon
is cached under `~/.cache/er-build-planner/r3-boards/<key>/` (override `ER_R3_BOARDS_CACHE`),
keyed by every other script in `scripts/`, so an interrupted run resumes and a change to this
file's wording re-renders without recomputing. `--check` therefore checks inputs
rather than output: `weapon_boards.inputs`, beside the module, records the sha256 of every
repository source file and every data file the generation opened (read with an audit hook and the
loaded modules, not listed by hand), and the module's header records the sha256 of that list and
of its own Rust body. A changed source or a hand-edited body makes the file stale. A data file is
not part of any commit: one that is absent (a machine with no game install) or that differs here
(the sweep was re-run from another checkout) is named by `--check` and does not fail it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
#: The main tree, when this checkout is one of its worktrees (`.worktrees/<name>` or
#: `.claude/worktrees/<name>`); the board cache is shared between them.
MAIN_ROOT = next((a for a in (REPO_ROOT.parent.parent, REPO_ROOT.parent.parent.parent)
                  if REPO_ROOT.parent in (a / ".worktrees", a / ".claude" / "worktrees")), REPO_ROOT)
OUT_RS = REPO_ROOT / "crates" / "er-r3-view" / "src" / "weapon_boards.rs"
REL_OUT = OUT_RS.relative_to(REPO_ROOT).as_posix()
SELF_REL = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
CACHE_ROOT = Path(os.environ.get("ER_R3_BOARDS_CACHE", Path.home() / ".cache" / "er-build-planner" / "r3-boards"))
WEP_TYPE_ENUM = (Path.home() / ".local/share/smithbox/app/Assets/PARAM/ER/Param Enums/WEP_TYPE.json")
#: A script's own scratch file (`tempfile`) is gone by the time the inputs are hashed, so it is no
#: input. Read once here: `gettempdir` opens files the first time, and calling it from inside the
#: audit hook re-enters its lock and hangs the process.
TEMP_DIR = tempfile.gettempdir()
GEAR_TOP = 6
#: Version of the board layout this script writes; bump it to invalidate every cached board.
LAYOUT = 1

WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen twenty").split()
ORDINALS = ("zeroth first second third fourth fifth sixth seventh eighth ninth tenth").split()
#: Plurals the trailing-s rule gets wrong.
PLURAL = {"Staff": "staves", "Torch": "torches", "Hand-to-Hand": "hand-to-hand arts",
          "Great Katana": "great katanas", "Ballista": "ballistae"}
STAT_LABEL = {"str": "STR", "dex": "DEX", "int": "INT", "fth": "FTH", "arc": "ARC"}
#: The game's `TAL.SUBCAT` noun for each move channel, where the board says it differently.
MOVE_TEXT = {104: "the final hit of a chain", 120: "two-handed attacks", 112: "its skill"}
STATUS_TEXT = {"bleed": "blood loss", "poison": "poison or rot", "scarlet_rot": "poison or rot",
               "madness": "madness", "sleep": "sleep"}


def word(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def num(v) -> str:
    """An int as itself, a float to three places without trailing zeros."""
    if isinstance(v, float):
        return f"{round(v, 3):g}"
    return str(v)


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def plural(cls: str) -> str:
    return PLURAL.get(cls, cls.lower() + "s")


def rule_of(gem_mount: int, disable_gem_attr: int) -> tuple[str, str]:
    """(the board's rule line, the clause that names the comparison population).

    Modal verbs on purpose, so the clause reads for one weapon and for nine."""
    if gem_mount == 2 and not disable_gem_attr:
        return "Can be infused", "that can be infused"
    if gem_mount == 2:
        return "Takes ashes of war; cannot be infused", "that can take ashes of war but not be infused"
    return "Unique skill; cannot be infused", "that cannot take ashes of war"


# --------------------------------------------------------------------------------------------
# unique rows, from `er-mechanics-weapon-twins.py --json`

LABEL_RE = re.compile(r"^(?P<twoh>2H )?(?P<base>.*?)(?: #(?P<num>\d+))?(?P<charged> charged)?$")
SHORT = {"running ": "run ", "rolling ": "roll ", "backstep ": "bstep ", "crouch ": "crouch ",
         "jump ": "jump ", "guard counter": "GC"}
BUILD_KEYS = {"requirement": ("{stat} {v}", "To wield it"), "scaling": ("{v} {stat}", "{stat} scaling")}


def parse_label(label: str):
    m = LABEL_RE.match(label)
    return bool(m["twoh"]), m["base"], int(m["num"]) if m["num"] else None, bool(m["charged"])


def slot_phrase(twoh: bool, base: str, nums: set, charged: bool) -> tuple[str, bool]:
    """("Charged R2s", plural) for merged numbered hits, ("First R2", singular) for one of them,
    and ("Jump R2", singular) for a slot that has no number."""
    noun = ("two-handed " if twoh else "") + ("charged " if charged else "") + base
    many = len(nums) > 1
    if len(nums) == 1:
        text = f"{ORDINALS[next(iter(nums))]} {noun}"
    else:
        text = noun + ("s" if many else "")
    return text[0].upper() + text[1:], many


def slot_key(twoh: bool, base: str, charged: bool) -> str:
    short = base
    for long, s in SHORT.items():
        short = short.replace(long, s)
    return ("2H " if twoh else "") + short + ("c" if charged else "")


def advantage_groups(comparable: list[dict]) -> list[dict]:
    """Every advantage over the comparable weapons, grouped: one group per slot (numbered hits
    merged when the value is the same) or per build field, holding the weapons it is held over."""
    groups: dict = {}
    for c in comparable:
        for adv in c["advantages"]:
            what, this = adv["what"], adv["this"]
            if what.endswith(" motion value"):
                kind, label = "mv", what[: -len(" motion value")]
            elif what.endswith(" first hit frame"):
                kind, label = "frame", what[: -len(" first hit frame")]
            else:
                kind, label = "build", what
            if kind == "build":
                key, parsed, n = (kind, label, this), None, None
            else:
                twoh, base, n, charged = parse_label(label)
                parsed = (twoh, base, charged)
                key = (kind, twoh, base, charged, this)
            g = groups.setdefault(key, {"kind": kind, "label": label, "parsed": parsed, "nums": set(),
                                        "this": this, "over": {}, "first": len(groups)})
            if n is not None:
                g["nums"].add(n)
            g["over"].setdefault(c["id"], {"name": c["name"], "values": []})["values"].append(adv["other"])
    return list(groups.values())


def who(group: dict, n: int, pop: str, cls: str) -> str:
    over = list(group["over"].values())
    k = len(over)
    if k <= 4:
        return join_names([o["name"] for o in over])
    if k == n:
        return f"all {word(n)} other {plural(cls)} {pop}"
    return f"{word(k)} of the {word(n)} other {plural(cls)} {pop}"


def against(group: dict) -> str:
    vals = sorted(v for o in group["over"].values() for v in o["values"])
    lo, hi = vals[0], vals[-1]
    return num(lo) if lo == hi else f"{num(lo)} to {num(hi)}"


def unique_line(group: dict, n: int, pop: str, cls: str) -> dict:
    this, tail = num(group["this"]), f"against {against(group)} on {who(group, n, pop, cls)}."
    if group["kind"] == "build":
        label = group["label"]
        stat, _, field = label.partition(" ")
        if field in BUILD_KEYS:
            key_fmt, lead = BUILD_KEYS[field]
            return {"key": key_fmt.format(stat=stat, v=this), "text": f"{lead.format(stat=stat)}, {tail}"}
        if label == "weight":
            return {"key": f"{this} wt", "text": f"Weight, {tail}"}
        if label == "stamina use x":
            return {"key": f"x{this} sp", "text": f"Stamina use, {tail}"}
        raise ValueError(f"no board text for the build advantage {label!r}")
    twoh, base, charged = group["parsed"]
    phrase, many = slot_phrase(twoh, base, group["nums"], charged)
    if group["kind"] == "mv":
        return {"key": f"{this} MV", "text": f"{phrase}, {tail}"}
    return {"key": f"{slot_key(twoh, base, charged)} f{this}",
            "text": f"{phrase} {'hit' if many else 'hits'} on frame {this}, {tail}"}


def unique_section(twins: dict, cls: str, pop: str) -> tuple[str, list[dict]]:
    comparable = twins["comparable"]
    n = len(comparable)
    if not n:
        return f"No other {cls.lower()} {pop}, so there is nothing to compare it against.", []
    noun = plural(cls) if n > 1 else cls.lower()
    intro = f"Against the {word(n) + ' ' if n > 1 else ''}other {noun} {pop}"
    twin_rows = [c for c in comparable if c["twin"]]
    if twin_rows:
        names = join_names([c["name"] for c in twin_rows])
        verb = "is its twin" if len(twin_rows) == 1 else "are its twins"
        intro += f"; {names} {verb}, sharing all {twin_rows[0]['slots']} moves."
    else:
        top = max(comparable, key=lambda c: c["moveset_match"] / max(c["slots"], 1))
        if top["moveset_match"]:
            intro += (f"; none is a twin, and {top['name']} shares the most of its moveset, "
                      f"{top['moveset_match']} of {top['slots']} moves.")
        else:
            intro += "; none shares any of its moveset."
    best = {}
    for g in advantage_groups(comparable):
        cur = best.get(g["kind"])
        if cur is None or len(g["over"]) > len(cur["over"]):
            best[g["kind"]] = g
    chosen = [best[k] for k in ("mv", "frame", "build") if k in best]
    chosen.sort(key=lambda g: -len(g["over"]))
    return intro, [unique_line(g, n, pop, cls) for g in chosen]


# --------------------------------------------------------------------------------------------
# gear rows, from `er-mechanics-gear-synergy.py --json`

def costs(survivability: float, other: list[str]) -> str:
    downs = ([f"{survivability * 100:.1f}% more damage taken"] if survivability >= 0.005 else []) + list(other)
    return f" Costs {', '.join(downs)}." if downs else ""


def setup_clause(discount: float) -> str:
    if discount >= 0.95:
        return ""
    share = "half" if round(discount * 20) == 10 else f"{discount * 100:.0f}%"
    return f", worth {share} for the setup they need"


def channel_text(c: dict, subcat: dict) -> str:
    pct = f"+{c['gain'] * 100:.0f}%"
    kind, _, arg = c["channel"].partition(":")
    if kind == "move":
        sc = int(arg)
        noun = MOVE_TEXT.get(sc) or f"{subcat.get(sc, arg)}s"
        return f"{pct} on {noun}{setup_clause(c['discount'])}"
    if kind == "pierce":
        return f"{pct} counter-hit damage; {c['engagement'] * 100:.0f}% of its hits pierce"
    if kind == "status":
        return f"{pct} damage after {STATUS_TEXT.get(arg, arg)} procs nearby"
    if kind == "successive":
        return f"{pct} on successive hits"
    if kind == "stat":
        return f"{pct} from its {arg}"
    if kind == "element":
        return f"{pct} {arg} damage"
    return f"{pct} {c['channel']}"


def gear_line(row: dict, subcat: dict) -> str:
    """The channels in the script's order, those discounted for a setup after the rest."""
    chans = sorted(row["channels"], key=lambda c: c["discount"] < 0.95)
    parts = []
    for c in chans:
        t = channel_text(c, subcat)
        if t not in parts:
            parts.append(t)
    return ". ".join(parts) + "." + costs(row["survivability_cost"], row["other_costs"])


def crit_line(name: str, crit: dict, g: dict) -> str:
    place, tied, of = crit["rank"]
    rank = f"the highest of {of}" if place == 1 and not tied else crit["rank_text"]
    median = (crit["median_throwAtkRate"] + 100) * 0.01
    owner = f"{name}'" if name.endswith("s") else f"{name}'s"
    return (f"+{g['gain'] * 100:.0f}% critical damage. {owner} critical is x{crit['multiplier']:.2f}, {rank} "
            f"weapons that can crit; the median is x{median:.2f}." + costs(g["survivability_cost"], g["other_costs"]))


def gear_section(gs: dict, name: str, subcat: dict) -> list[dict]:
    out = []
    ps = gs.get("powerstance")
    if ps:
        out.append({"name": ps["heading"], "text": ps["text"]})
    crit = gs["critical"]
    if crit.get("above_normal"):
        for g in crit["gear"]:
            out.append({"name": g["name"], "text": crit_line(name, crit, g)})
    for r in gs["gear"]:
        out.append({"name": r["name"], "text": gear_line(r, subcat)})
    return out


# --------------------------------------------------------------------------------------------
# speed rows, from `er-mechanics-weapon-card.py`, and infusion rows

def speed_section(card: dict) -> list[dict]:
    out = []
    rt = card["rank_text"]
    f = card["r1_first_hit_frame"]
    if f is not None:
        text = f"R1 hits on frame {f:g} ({rt['r1_first_hit_frame']})"
        if card["r1_reach_m"] is not None:
            text += f" and reaches {card['r1_reach_m']:.1f} m ({rt['r1_reach_m']})"
        out.append({"key": f"{f:g} f", "text": text + "."})
    one, two = card["r1_stamina_one_handed"], card["r1_stamina_two_handed"]
    if one is not None:
        text = f"Stamina per R1 one-handed ({rt['r1_stamina_one_handed']})"
        if two is not None:
            text += f", {two} two-handed ({rt['r1_stamina_two_handed']})"
        out.append({"key": f"{one} sp", "text": text + "."})
    return out


def infusion_section(inf: dict) -> tuple[str, list[dict]]:
    by = inf["by_category"]
    rows = [{"key": kind.capitalize(), "text": f"{by[kind]['affinity']}. {by[kind]['text']}"}
            for kind in ("physical", "elemental", "status") if kind in by]
    intro = inf["rank_text"] or "Not in the RL 150 ranking of every weapon's best build."
    return intro, rows


# --------------------------------------------------------------------------------------------
# the sources, one process's worth

def _mod(name: str, fname: str):
    s = importlib.util.spec_from_file_location(name, HERE / fname)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


class Sources:
    """The four scripts and the populations they rank against, built once per process."""

    def __init__(self):
        self.tw = _mod("er_mechanics_weapon_twins", "er-mechanics-weapon-twins.py")
        self.card = _mod("er_mechanics_weapon_card_gen", "er-mechanics-weapon-card.py")
        self.inf = _mod("er_mechanics_infusions", "er-mechanics-infusions.py")
        self.gs = _mod("er_mechanics_gear_synergy", "er-mechanics-gear-synergy.py")
        self._memoize_optimizer()
        self.reg = self.tw.ATT.Regulation()
        self.rc = self.card.REACH.Reach()
        self.card_pop = self.card.population(self.rc)
        self.gd, self.gpop, self.gear, self.gtables = self.gs.build()
        self.sweep = self.inf.load_sweep()
        self.subcat = self.gs.TAL.SUBCAT

    def _memoize_optimizer(self):
        """Output-identical caches over the infusion path: the corpus window of an RL is the same
        file read for every weapon, and `Builder.best` at RL 150 is asked twice per affinity."""
        opt = self.inf.OPT
        rows_of = opt.corpus_rows
        memo: dict = {}

        def corpus_rows(mirror, lo, hi):
            key = (str(mirror), lo, hi)
            if key not in memo:
                memo[key] = rows_of(mirror, lo, hi)
            return memo[key]

        opt.corpus_rows = corpus_rows
        best = self.inf.Builder.best

        def best_once(builder, rl, affinity, fl, dfn):
            cache = builder.__dict__.setdefault("_best", {})
            if (rl, affinity) not in cache:
                cache[(rl, affinity)] = best(builder, rl, affinity, fl, dfn)
            return cache[(rl, affinity)]

        self.inf.Builder.best = best_once

    def raw(self, wid: int, name: str) -> dict:
        """What the four scripts print for one weapon, as `--json` would. This is what is cached,
        so a change to how a board is worded needs no recomputation."""
        if self.reg.find_weapon(name) != wid:
            raise ValueError(f"the name {name!r} resolves to row {self.reg.find_weapon(name)}, not {wid}")
        tw = self.tw.twins(self.reg, wid)
        for r in tw:
            r["advantages"] = self.tw.advantages(self.reg, wid, r["id"])
        inf = self.inf.report(name)
        card = self.card.speed_and_cost(name, self.rc, self.card_pop)
        gs = self.gs.rank(self.gd, self.gpop, self.gear, self.gtables, name, GEAR_TOP)
        out = {"twins": {"comparable": tw},
               "infusions": {"by_category": inf["by_category"], "rank_text": inf["rank_text"]},
               "card": card,
               "gear": {"powerstance": gs["powerstance"], "critical": gs["critical"], "gear": gs["gear"]},
               "subcat": {str(k): v for k, v in self.subcat.items()}}
        return json.loads(json.dumps(out, default=str))


def board(raw: dict, name: str, row: dict, cls: str) -> dict:
    rule, pop = rule_of(row["gemMountType"], row["disableGemAttr"])
    unique_intro, unique = unique_section(raw["twins"], cls, pop)
    inf_intro, infusions = infusion_section(raw["infusions"])
    subcat = {int(k): v for k, v in raw["subcat"].items()}
    return {"icon_id": row["iconId"], "class": cls.upper(), "name": name, "rule": rule,
            "unique_intro": unique_intro, "unique": unique, "infusions_intro": inf_intro,
            "infusions": infusions, "speed": speed_section(raw["card"]),
            "gear": gear_section(raw["gear"], name, subcat)}


def weapon_list() -> tuple[list[tuple[int, str, dict, str]], list[tuple[int, str, str]]]:
    """([(id, name, row, class)], [(id, name, why skipped)]): every base row the item list names."""
    pr = _mod("er_param_read", "er-param-read.py")
    opt_src = (HERE / "er-builds-optimize.py").read_text()
    skip_types = _sweep_skip_types(opt_src)
    catalog = Path.home() / ".cache" / "er-build-planner" / "catalog.json"
    real = set(json.loads(catalog.read_text())["armament"].values())
    rows = pr.rows(pr.param_bytes(pr.load(), "EquipParamWeapon"),
                   ["wepType", "iconId", "gemMountType", "disableGemAttr"], strict=False)[0]
    names = pr.row_names("EquipParamWeapon")
    types = {int(o["Key"]): o["Names"][0]["Text"] for o in json.loads(WEP_TYPE_ENUM.read_text())["Options"]}
    keep, skipped = [], []
    for r in rows:
        wid, nm = r["id"], names.get(r["id"])
        if wid % 10000 or not nm or nm.startswith("[") or wid not in real:
            continue
        if r["wepType"] in skip_types:
            skipped.append((wid, nm, f"class {types.get(r['wepType'], r['wepType'])} is not in the RL 150 sweep"))
            continue
        keep.append((wid, nm, r, types[r["wepType"]]))
    return keep, skipped


def _sweep_skip_types(src: str) -> set[int]:
    """`SWEEP_SKIP_WEP_TYPES` read out of the optimizer's source, so the two never disagree."""
    m = re.search(r"^AMMO_WEP_TYPES = (\{[^}]*\})", src, re.M)
    s = re.search(r"^SWEEP_SKIP_WEP_TYPES = AMMO_WEP_TYPES \| (\{[^}]*\})", src, re.M)
    if not m or not s:
        raise SystemExit("er-builds-optimize.py no longer spells SWEEP_SKIP_WEP_TYPES the way this script reads it")
    return set(ast.literal_eval(m[1])) | set(ast.literal_eval(s[1]))


# --------------------------------------------------------------------------------------------
# inputs, cache and staleness

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key() -> str:
    """Every script in `scripts/` but this one, whose wording a cached weapon does not depend on,
    and the layout version."""
    h = hashlib.sha256(f"layout {LAYOUT}\n".encode())
    for p in sorted(HERE.glob("*.py")):
        if p.resolve() != Path(__file__).resolve():
            h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:16]


def _ignored(path: str) -> bool:
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, "/proc", "/dev", "/sys", "/usr", "/etc",
                str(CACHE_ROOT), TEMP_DIR}
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes) or "__pycache__" in path


def record_opened(sink: set):
    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            try:
                p = os.path.realpath(os.fsdecode(args[0]))
            except (TypeError, ValueError):
                return
            mode = args[1] if len(args) > 1 else "r"
            if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
                return
            if not _ignored(p) and os.path.isfile(p):
                sink.add(p)
    sys.addaudithook(hook)

    # A script loaded by path does not go through the hook (its source is read by `open_code`,
    # or not at all when `__pycache__` is current) and is often never put in `sys.modules`, so
    # the path it is loaded from is recorded where every one of these scripts asks for it.
    by_path = importlib.util.spec_from_file_location

    def spec_from_file_location(name, location=None, *args, **kwargs):
        if location is not None:
            sink.add(os.path.realpath(os.fspath(location)))
        return by_path(name, location, *args, **kwargs)

    importlib.util.spec_from_file_location = spec_from_file_location


def display_path(p: str) -> str:
    """Repository files repo-relative; others under the home directory as `~/...`."""
    try:
        return Path(p).relative_to(REPO_ROOT).as_posix()
    except ValueError:
        home = str(Path.home())
        return "~" + p[len(home):] if p.startswith(home + "/") else p


def here_path(p: str) -> Path:
    """A path a shard recorded, as it is in this checkout. The cache is shared across checkouts
    of the repository (the main tree and every `.claude/worktrees/<name>`), while a shard records
    real paths, so a repository file recorded by a run in another checkout is carried into this
    one by its path relative to that checkout's root. Measured 2026-10-03: a run in a worktree
    that was removed afterwards left `.../.claude/worktrees/agent-.../scripts/er-builds-embed.py`
    in the cache, and the next render in the main tree died on it. The other direction too: a
    render in `.worktrees/target-bars-pr` on 2026-10-04 recorded five main-tree scripts as data
    inputs, because the main tree's root is not this checkout's."""
    for marker in ("/.claude/worktrees/", "/.worktrees/"):
        if marker in p:
            rest = p.split(marker, 1)[1]
            if "/" in rest:
                return REPO_ROOT / rest.split("/", 1)[1]
    if MAIN_ROOT != REPO_ROOT and p.startswith(str(MAIN_ROOT) + "/"):
        return REPO_ROOT / Path(p).relative_to(MAIN_ROOT)
    return resolve_display(p)


def resolve_display(p: str) -> Path:
    if p.startswith("~/"):
        return Path.home() / p[2:]
    q = Path(p)
    return q if q.is_absolute() else REPO_ROOT / q


def run_shard(shard: int, of: int, cache: Path) -> int:
    opened: set = set()
    record_opened(opened)
    keep, _ = weapon_list()
    mine = [w for i, w in enumerate(keep) if i % of == shard]
    # The first weapon of every class first, then the second of each, so a reader of a run still
    # in progress sees every class early.
    per_class: dict = {}
    order = {}
    for w in mine:
        per_class[w[3]] = per_class.get(w[3], 0) + 1
        order[w[0]] = (per_class[w[3]], w[0])
    todo = sorted((w for w in mine if not (cache / f"{w[0]}.json").exists()), key=lambda w: order[w[0]])
    print(f"shard {shard}/{of}: {len(mine)} weapons, {len(todo)} to compute", flush=True)
    src = Sources() if todo else None
    for i, (wid, name, row, cls) in enumerate(todo, 1):
        t = time.time()
        try:
            out = {"ok": True, "raw": src.raw(wid, name)}
        except (Exception, SystemExit) as e:                   # one weapon a source cannot evaluate
            out = {"ok": False, "why": f"{type(e).__name__}: {e}".splitlines()[0][:200]}
        tmp = cache / f"{wid}.json.tmp"
        tmp.write_text(json.dumps(out, indent=1, sort_keys=True))
        tmp.replace(cache / f"{wid}.json")
        print(f"shard {shard}: {i}/{len(todo)} {name} {'ok' if out['ok'] else out['why']} "
              f"{time.time() - t:.0f}s", flush=True)
    # The scripts themselves: `importlib` reads a module's source through `open_code`, which the
    # hook above does not see, and some are imported only part-way through a weapon.
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and os.path.realpath(f).startswith(str(REPO_ROOT) + "/"):
            opened.add(os.path.realpath(f))
    # A union with the last run's: a resumed shard that computed nothing opened none of the
    # sources' data files, and those files are still what its cached weapons were read from.
    seen = cache / f"opened-{shard}.json"
    if seen.exists():
        opened |= set(json.loads(seen.read_text()))
    seen.write_text(json.dumps(sorted(opened)))
    return 0


# --------------------------------------------------------------------------------------------
# Rust

def rust_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rust_board(b: dict, indent: str = "") -> str:
    def lines(rows):
        return "&[" + ", ".join(f"Line {{ key: {rust_str(r['key'])}, text: {rust_str(r['text'])} }}" for r in rows) + "]"

    gear = "&[" + ", ".join(f"Gear {{ name: {rust_str(g['name'])}, text: {rust_str(g['text'])} }}" for g in b["gear"]) + "]"
    return (f"Board {{ icon_id: {b['icon_id']}, class: {rust_str(b['class'])}, name: {rust_str(b['name'])}, "
            f"rule: {rust_str(b['rule'])}, unique_intro: {rust_str(b['unique_intro'])}, unique: {lines(b['unique'])}, "
            f"infusions_intro: {rust_str(b['infusions_intro'])}, infusions: {lines(b['infusions'])}, "
            f"speed: {lines(b['speed'])}, gear: {gear} }}")


def rustfmt(text: str) -> str:
    run = subprocess.run(["rustfmt", "--edition", "2024", "--emit", "stdout"], input=text,
                         capture_output=True, text=True, timeout=25, check=False)
    if run.returncode != 0:
        raise SystemExit(f"rustfmt refused the generated module:\n{run.stderr}")
    return run.stdout


def render_body(boards: list[tuple[int, dict]]) -> str:
    """One board per line, kept that way by `rustfmt::skip`: rustfmt would spread the table over
    31,000 lines, past `check-rust-file-sizes.py`'s limit, and a line per weapon also makes a
    regeneration's diff read weapon by weapon."""
    items = "".join(f"    ({wid}, {rust_board(b)}),\n" for wid, b in boards)
    src = ("use crate::board::{Board, Gear, Line};\n\n"
           "/// Every board, sorted by base weapon id so a lookup can binary-search it.\n"
           "#[rustfmt::skip]\n"
           f"pub static WEAPON_BOARDS: &[(u32, Board)] = &[\n{items}];\n")
    out = rustfmt(src)
    if out != src:
        raise SystemExit("rustfmt changed the generated module; the layout above no longer matches its output")
    return out


BEGIN = "// ---- generated body below; its sha256 is recorded above ----\n"
OUT_INPUTS = OUT_RS.with_suffix(".inputs")
REL_INPUTS = OUT_INPUTS.relative_to(REPO_ROOT).as_posix()


def render(boards, skipped, sources, data) -> tuple[str, str]:
    """(the Rust module, the input list beside it). The input list is its own file because it
    names every TAE and HKX file the attack scripts read, some 2,700 lines."""
    body = render_body(boards)
    inputs = "".join(f"source {h} {p}\n" for p, h in sources) + "".join(f"data {h} {p}\n" for p, h in data)
    head = [
        f"//! Generated by `{SELF_REL}`; do not edit by hand.",
        "//!",
        f"//! Regenerate: `python3 {SELF_REL} --jobs 3` (hours; each weapon is cached, so it resumes).",
        f"//! Stale?     `python3 {SELF_REL} --check` compares the inputs in `{OUT_INPUTS.name}`.",
        "//!",
        f"//! {len(boards)} weapons. Left out:",
    ]
    head += [f"//! - {wid} {nm}: {why}" for wid, nm, why in skipped] or ["//! - none"]
    head.append("")
    head.append(f"// inputs-sha256 {hashlib.sha256(inputs.encode()).hexdigest()}")
    head.append(f"// body-sha256 {hashlib.sha256(body.encode()).hexdigest()}")
    return "\n".join(head) + "\n" + BEGIN + body, inputs


def parse_header(text: str) -> tuple[str | None, str | None, str]:
    head, sep, body = text.partition(BEGIN)
    if not sep:
        return None, None, ""
    i = re.search(r"^// inputs-sha256 ([0-9a-f]{64})$", head, re.M)
    b = re.search(r"^// body-sha256 ([0-9a-f]{64})$", head, re.M)
    return i[1] if i else None, b[1] if b else None, body


def staleness(text: str, inputs: str) -> tuple[list[str], list[str], list[str]]:
    """(why the file is stale, which data files are absent here, which differ here).

    Only the committed half can make the file stale: the body and the repository sources. A data
    file lives outside the repository, under one machine's home directory, and is rewritten by
    runs in any checkout -- the RL 150 grease sweep was re-run from a newer branch on 2026-10-04,
    and every older branch's boards then failed this check with nothing on that branch changed. So
    a data file that differs here is reported beside the absent ones, never counted as stale."""
    inputs_sha, body_sha, body = parse_header(text)
    why, absent, moved = [], [], []
    if body_sha is None or inputs_sha is None:
        return ["no generated header"], absent, moved
    if hashlib.sha256(body.encode()).hexdigest() != body_sha:
        why.append("the body was edited after generation")
    if hashlib.sha256(inputs.encode()).hexdigest() != inputs_sha:
        why.append(f"{OUT_INPUTS.name} does not match the module it was written with")
    sources = re.findall(r"^source ([0-9a-f]{64}) (.+)$", inputs, re.M)
    data = re.findall(r"^data ([0-9a-f]{64}) (.+)$", inputs, re.M)
    if not sources:
        why.append("the header lists no source files")
    for h, p in sources:
        f = resolve_display(p)
        if not f.exists():
            why.append(f"source {p} is gone")
        elif sha256_file(f) != h:
            why.append(f"source {p} changed")
    for h, p in data:
        f = resolve_display(p)
        if not f.exists():
            absent.append(p)
        elif sha256_file(f) != h:
            moved.append(p)
    return why, absent, moved


def check() -> int:
    if not OUT_RS.exists():
        print(f"gen-r3-weapon-boards: {REL_OUT} does not exist; generate it", file=sys.stderr)
        return 1
    if not OUT_INPUTS.exists():
        print(f"gen-r3-weapon-boards: {REL_INPUTS} does not exist; generate it", file=sys.stderr)
        return 1
    why, absent, moved = staleness(OUT_RS.read_text(), OUT_INPUTS.read_text())
    if moved:
        print(f"gen-r3-weapon-boards: {len(moved)} data file(s) differ here from the generation, not "
              "counted as stale (regenerate if this copy is meant to be the input): "
              + ", ".join(moved[:5]) + (" ..." if len(moved) > 5 else ""))
    if absent:
        print(f"gen-r3-weapon-boards: {len(absent)} data file(s) absent here, not compared: "
              + ", ".join(absent[:5]) + (" ..." if len(absent) > 5 else ""))
    if why:
        print(f"gen-r3-weapon-boards: {REL_OUT} is stale:\n  " + "\n  ".join(why)
              + f"\nregenerate with: python3 {SELF_REL} --jobs 3", file=sys.stderr)
        return 1
    print(f"gen-r3-weapon-boards: {REL_OUT} is current")
    return 0


def generate(jobs: int) -> int:
    key = cache_key()
    cache = CACHE_ROOT / key
    cache.mkdir(parents=True, exist_ok=True)
    print(f"cache {cache}", flush=True)
    procs = []
    for i in range(jobs):
        log = open(cache / f"shard-{i}.log", "a")
        procs.append((subprocess.Popen([sys.executable, __file__, "--shard", str(i), "--of", str(jobs),
                                        "--cache", str(cache)], stdout=log, stderr=subprocess.STDOUT), log))
    failed = 0
    for p, log in procs:
        failed |= p.wait() != 0
        log.close()
    if failed:
        print(f"a shard failed; see {cache}/shard-*.log", file=sys.stderr)
        return 1
    if cache_key() != key:
        print("scripts/ changed during the run; run again", file=sys.stderr)
        return 1
    keep, skipped = weapon_list()
    boards = []
    for wid, name, row, cls in keep:
        got = json.loads((cache / f"{wid}.json").read_text())
        if not got["ok"]:
            skipped.append((wid, name, got["why"]))
            continue
        try:
            boards.append((wid, board(got["raw"], name, row, cls)))
        except ValueError as e:
            skipped.append((wid, name, f"ValueError: {e}"))
    opened: set = set()
    for f in cache.glob("opened-*.json"):
        opened |= {str(here_path(p)) for p in json.loads(f.read_text())}
    opened.add(str(Path(__file__).resolve()))
    sources, data = [], []
    for p in sorted(x for x in opened if not _ignored(x)):
        (sources if p.startswith(str(REPO_ROOT) + "/") else data).append((display_path(p), sha256_file(Path(p))))
    skipped.sort()
    rs, inputs = render(sorted(boards), skipped, sorted(sources), sorted(data))
    OUT_INPUTS.write_text(inputs)
    OUT_RS.write_text(rs)
    print(f"wrote {REL_OUT}: {len(boards)} boards, {len(skipped)} left out, "
          f"{len(sources)} source and {len(data)} data inputs")
    return 0


def only(name: str) -> int:
    keep, _ = weapon_list()
    hit = [w for w in keep if w[1].lower() == name.lower()]
    if not hit:
        raise SystemExit(f"{name!r} is not a weapon this script generates")
    wid, nm, row, cls = hit[0]
    b = board(Sources().raw(wid, nm), nm, row, cls)
    print(rustfmt(f"const _: Board = {rust_board(b)};\n"))
    return 0


# --------------------------------------------------------------------------------------------
# selftest: the text rules on a fixture shaped like Misericorde's `weapon-twins` and
# `gear-synergy` output, and the staleness check on a scratch file. No game data is read.

def _fixture_twins():
    def adv(what, this, other):
        return {"what": what, "this": this, "other": other}

    mv = [adv("R2 #1 charged motion value", 155, 150), adv("R2 #2 charged motion value", 155, 150)]
    f8 = adv("R2 #1 first hit frame", 8, 9)
    f26 = adv("R2 #1 charged first hit frame", 26, 27)
    rows = [("Main-gauche", 32, [adv("DEX requirement", 12, 15)] + mv),
            ("Dagger", 27, [f8, f26] + mv),
            ("Parrying Dagger", 27, [adv("DEX requirement", 12, 14), f8, f26] + mv),
            ("Celebrant's Sickle", 27, [f8, f26] + mv),
            ("Great Knife", 27, [adv("STR scaling", 15.0, 5.0), f8, f26] + mv),
            ("Wakizashi", 27, [adv("weight", 2.0, 3.0), adv("STR requirement", 7, 9),
                               adv("DEX requirement", 12, 13), f8, f26] + mv),
            ("Bloodstained Dagger", 27, [adv("STR requirement", 7, 9), f8, f26] + mv),
            ("Erdsteel Dagger", 27, [adv("FTH requirement", 0, 14)]),
            ("Fire Knight's Shortsword", 27, [adv("STR requirement", 7, 8), adv("DEX requirement", 12, 13)])]
    return {"comparable": [{"id": i, "name": n, "moveset_match": m, "slots": 36, "twin": False, "advantages": a}
                           for i, (n, m, a) in enumerate(rows)]}


def selftest() -> int:
    pop = rule_of(2, 0)[1]
    intro, rows = unique_section(_fixture_twins(), "Dagger", pop)
    assert intro == ("Against the nine other daggers that can be infused; none is a twin, and Main-gauche "
                     "shares the most of its moveset, 32 of 36 moves."), intro
    assert rows == [
        {"key": "155 MV", "text": "Charged R2s, against 150 on seven of the nine other daggers that can be infused."},
        {"key": "R2 f8", "text": "First R2 hits on frame 8, against 9 on six of the nine other daggers that can "
                                 "be infused."},
        {"key": "DEX 12", "text": "To wield it, against 13 to 15 on Main-gauche, Parrying Dagger, Wakizashi and "
                                  "Fire Knight's Shortsword."}], rows
    one = {"comparable": [dict(_fixture_twins()["comparable"][0], twin=True, moveset_match=36)]}
    assert unique_section(one, "Dagger", pop)[0] == ("Against the other dagger that can be infused; Main-gauche "
                                                     "is its twin, sharing all 36 moves."), unique_section(one, "Dagger", pop)
    assert unique_section({"comparable": []}, "Staff", rule_of(0, 1)[1]) == (
        "No other staff that cannot take ashes of war, so there is nothing to compare it against.", [])
    assert plural("Staff") == "staves" and plural("Dagger") == "daggers"
    assert slot_phrase(False, "jump R2", set(), False) == ("Jump R2", False)
    assert slot_phrase(True, "R1", {2}, False) == ("Second two-handed R1", False)

    subcat = {100: "charged heavy attack", 104: "final chain attack", 121: "backstep / rolling attack",
              122: "dash attack"}

    def ch(channel, gain, discount=1.0, engagement=1.0):
        return {"channel": channel, "gain": gain, "discount": discount, "engagement": engagement}

    leda = {"channels": [ch("move:121", 0.05, 0.4983), ch("move:122", 0.05)], "survivability_cost": 0.009,
            "other_costs": []}
    assert gear_line(leda, subcat) == ("+5% on dash attacks. +5% on backstep / rolling attacks, worth half for "
                                       "the setup they need. Costs 0.9% more damage taken."), gear_line(leda, subcat)
    assert gear_line({"channels": [ch("move:104", 0.45)], "survivability_cost": 0, "other_costs": []},
                     subcat) == "+45% on the final hit of a chain."
    assert gear_line({"channels": [ch("pierce", 0.15, engagement=0.389)], "survivability_cost": 0,
                      "other_costs": []}, subcat) == "+15% counter-hit damage; 39% of its hits pierce."
    assert setup_clause(0.4) == ", worth 40% for the setup they need"
    crit = {"rank": [1, 0, 327], "rank_text": "1st highest of 327", "multiplier": 1.4, "median_throwAtkRate": 0}
    assert crit_line("Misericorde", crit, {"gain": 0.17, "survivability_cost": 0.0, "other_costs": []}) == (
        "+17% critical damage. Misericorde's critical is x1.40, the highest of 327 weapons that can crit; the "
        "median is x1.00.")
    assert crit_line("Hookclaws", crit, {"gain": 0.17, "survivability_cost": 0.0, "other_costs": []}).startswith(
        "+17% critical damage. Hookclaws' critical")
    card = {"r1_first_hit_frame": 10, "r1_reach_m": 3.04, "r1_stamina_one_handed": 9, "r1_stamina_two_handed": 12,
            "rank_text": {"r1_first_hit_frame": "tied 1st fastest of 451", "r1_reach_m": "tied 268th longest of 419",
                          "r1_stamina_one_handed": "tied 47th cheapest of 451",
                          "r1_stamina_two_handed": "tied 94th cheapest of 451"}}
    assert speed_section(card) == [
        {"key": "10 f", "text": "R1 hits on frame 10 (tied 1st fastest of 451) and reaches 3.0 m (tied 268th "
                                "longest of 419)."},
        {"key": "9 sp", "text": "Stamina per R1 one-handed (tied 47th cheapest of 451), 12 two-handed (tied 94th "
                                "cheapest of 451)."}]

    assert rust_str('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert _sweep_skip_types("AMMO_WEP_TYPES = {81, 83}\nSWEEP_SKIP_WEP_TYPES = AMMO_WEP_TYPES | {0, 57}\n") == {
        0, 57, 81, 83}

    # Staleness on a scratch tree: current, then a changed source, a hand edit, an absent data file.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src_file = Path(tmp) / "source.py"
        src_file.write_text("x = 1\n")
        body = "pub static X: u32 = 1;\n"
        inputs = f"source {sha256_file(src_file)} {src_file}\ndata {'0' * 64} {Path(tmp) / 'absent.bin'}\n"
        text = (f"// inputs-sha256 {hashlib.sha256(inputs.encode()).hexdigest()}\n"
                f"// body-sha256 {hashlib.sha256(body.encode()).hexdigest()}\n" + BEGIN + body)
        why, absent, moved = staleness(text, inputs)
        assert why == [] and absent == [str(Path(tmp) / "absent.bin")] and moved == [], (why, absent, moved)
        # A data file that moved is named, and the file is still current.
        data_file = Path(tmp) / "sweep.jsonl"
        data_file.write_text("a\n")
        with_data = inputs + f"data {sha256_file(data_file)} {data_file}\n"
        dtext = (f"// inputs-sha256 {hashlib.sha256(with_data.encode()).hexdigest()}\n"
                 f"// body-sha256 {hashlib.sha256(body.encode()).hexdigest()}\n" + BEGIN + body)
        assert staleness(dtext, with_data)[::2] == ([], []), staleness(dtext, with_data)
        data_file.write_text("b\n")
        assert staleness(dtext, with_data)[::2] == ([], [str(data_file)]), staleness(dtext, with_data)
        assert staleness(text.replace("= 1;", "= 2;"), inputs)[0] == ["the body was edited after generation"]
        assert staleness(text, inputs + "source x y\n")[0] == [
            f"{OUT_INPUTS.name} does not match the module it was written with"]
        src_file.write_text("x = 2\n")
        assert staleness(text, inputs)[0] == [f"source {src_file} changed"], staleness(text, inputs)
        assert staleness(body, inputs)[0] == ["no generated header"]
    assert render_body([(1, {"icon_id": 2, "class": "C", "name": "N", "rule": "R", "unique_intro": "U",
                             "unique": [], "infusions_intro": "I", "infusions": [], "speed": [], "gear": []})]
                       ).count("\n    (1, Board {") == 1
    print("selftest ok: unique, gear, crit and speed rows match the Misericorde board; staleness detected")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 when the generated file is stale")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", metavar="WEAPON", help="print one weapon's board as Rust")
    ap.add_argument("--jobs", type=int, default=3, help="weapons computed at once (each also uses every core)")
    ap.add_argument("--shard", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--of", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--cache", type=Path, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check:
        return check()
    if a.only:
        return only(a.only)
    if a.shard is not None:
        return run_shard(a.shard, a.of, a.cache)
    return generate(max(1, a.jobs))


if __name__ == "__main__":
    sys.exit(main())
