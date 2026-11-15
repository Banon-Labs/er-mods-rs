#!/usr/bin/env python3
"""What a skill that hurts its own user costs: the HP it takes, the full-HP gear it switches off,
and the cast the opponent punishes while it plays.

    python3 scripts/er-mechanics-self-damage.py Seppuku --weapon Estoc --grip 2H \
        --talismans "Ritual Shield Talisman,Crimson Amber Medallion +3" --max-hp 2594
    python3 scripts/er-mechanics-self-damage.py --selftest

User, 2026-10-19: Seppuku (and any ash whose SpEffect deals damage to its user) costs the HP it
takes; the talisman and gear that only work at full or high HP stop working once it has; and its
windup before the buff applies is a cast the opponent punishes. `er-mechanics-combo-loadout.py`
charges all three against the row (`set_self_costs`, `cast_weights`), and `er-weapon-build.py`
takes a full-HP talisman out of a duel's opening set when a self-damaging copy the plan carries
leaves it worth nothing.

# Self-damage (`self_damage`)

Every SpEffect a skill's TimeAct applies lands on its user. A row of the paid-for animation
(`er-mechanics-ashes.main_anim`), or a row linked from it (not `atkOccurrenceSpEffectId`, which
lands on whoever the weapon hits), with a positive `changeHpRate` (percent of max HP) or
`changeHpPoint` takes `changeHpRate / 100 x max HP + changeHpPoint` per application. Seppuku's
1753 (`MEASURED`, 1.17.1 regulation): 15% + 100 at cast frame 31, `effectEndurance` 0.1 s,
`motionInterval` 0.2 s. Bloody Slash's 1763 is 5% + 50 and Assassin's Gambit's 1765 is 35.

Applications per event: 1 + floor(span / `motionInterval`), the span being the longer of the
TimeAct window and the row's `effectEndurance` (`INFERRED` from the field names: no tick in a row
whose span is shorter than its interval, which is every self-damage row in the regulation). A row
reached through a `cycleOccurrenceSpEffectId` link is counted the same way, once per event, not
once per interval of its parent (`INFERRED`; it undercounts Fire's Deadly Sin, whose 40 s aura
1790000 cycles its 3 HP row 1790002 every 0.25 s, and that spell scores no hit anyway).

## How the game takes the HP (`VERIFIED`, 2.7.2.0 `eldenring-deobf-1.17.2.bin`, 2026-11-14)

- Per ticking entry, `0x1405008f0` reads `changeHpRate` (row +0x9c) and `0x140500920`
  `changeHpPoint` (row +0xa0), each times the entry's four rates (+0x50..+0x5c). `0x1404fc7d0`
  (1.16.2 `FUN_1404fb920`) sums them over the entries whose flag bit 4 is set this frame, scaling
  only rows of `stateInfo` 14, 6, 260, 436 and 437 by the holder's damage rates (status.md
  section 3: 100 on players).
- `0x1404f8bb0` (1.16.2 `FUN_1404f7d00`) turns the sums into `trunc(-(maxHP x rate x 0.01 +
  point))` (0.01 at `0x1432a3ba4`). So `changeHpRate` is a share of the holder's own max HP and
  `changeHpPoint` is flat; there is no PvP branch.
- `0x1404413b0` hands that to `0x140436ad0`: new HP = HP (+0x138) + delta, and only when that is
  <= 0 and `0x140437d30` holds is it set to 1. `0x140437d30` holds for a ChrDataModule flag
  (+0x19b bit 0, +0x19a bit 7), the global debug no-death flags (`0x140437a60`), or an active
  SpEffect of `stateInfo` 143, or 235 on a player. No HP-cost row below carries either stateInfo
  or `noDead`, so a skill's or spell's own cost can kill its user.
- When: TAE event 66 applies its SpEffect on the event's first frame (ashes-of-war.md section 5),
  whether or not the cast hits: Bloody Slash's 1763 at f15 of its opening, Blood Blade's 1760 at
  f18, Seppuku's 1753 at f31, Assassin's Gambit's 1765 at f44. A spell's
  `Bullet.spEffectIDForShooter` lands on the caster when the bullet is made (`INFERRED` from the field
  name, consumer not traced), so Briars of Sin pays at each of its casts' launches.
- A status row on the caster is gauged like a hit's: the event-66 handler `0x14042c5b0` ->
  `0x1403e8dc0` -> `0x1403e8e70` -> `0x1403fb0a0`, which runs `SpecialEffect::Apply`
  (`0x1404fb790`) and then the holder's `CSChrResistModule` dispatch `0x14043e0e0`, removing the
  entry (`0x1404f7970`) when the gauge held. Every ChrIns apply wrapper found (`0x1403e8e70`,
  `0x1403e91c0`, `0x1403e92b0`) goes through `0x1403fb0a0`. So such a row costs its HP only on
  the application whose build-up empties the caster's gauge (`status_cost`).
- Ticks: `0x140501ed0` fires a tick (flags 4, `0x140502070`, which adds `motionInterval` to the
  entry's timer at +0x4c) whenever that timer is <= 0. Whether a new entry's timer starts at 0 was
  not traced, so the applications rule above stays `INFERRED`.

## Status rows (`status_cost`)

A row whose `stateInfo` names a status and whose build-up field for it is positive costs its HP
change only on a proc:

- build-up of at least `SURE_BUILDUP` (Seppuku's 1753, `bloodAttackPower` 9999): it procs on
  application, one proc (`INFERRED`: no player resistance reaches 9999);
- otherwise, given the caster's resistance to that status, the cast's build-up over it is the
  share of a proc it buys, at most one (`INFERRED`: the gauge's refill between casts and the
  build-up past a proc, which the game throws away, are left out);
- with no resistance given, it is not priced and is listed in `unpriced` with its build-up. No
  model here carries its attacker line's own resistances, and the corpus median is a usage figure,
  never a line's (AGENTS.md), so Vyke's Frenzyflame Thrust (1782, madness 4 a tick), the frenzy
  incantations and the bleed half of Briars of Sin and the thorn sorceries are unpriced today.

# Full-HP gear (`hp_gated_rows`, `gated_loss`)

A worn talisman row with `conditionHpRate` >= 0 works only at or above that share of max HP
(`er-mechanics-talismans._hp_gate`, `VERIFIED` FUN_140502150): Ritual Shield 340900 (100%,
`defPlayerDmgCorrectRate` 0.7) and Ritual Sword 320500 (100%, `atkPlayerDmgCorrectRate` 1.1) in
1.17.1. Without a heal, which the duel ladder bans (10-3 "All Methods of HP Recovery"), HP the cast
took is not regained, so such a row covers fewer of the fight's hits:

    covered(hp) = 0                                   hp < p x max HP
                = 1 + floor((hp - p x max HP) / hit)  otherwise

the opponent's hits landing while HP is still at or above the gate, each `hit` = `B_HIT_HP` (the
pool's mean R1 #1, `er-mechanics-exchange.OPPONENT_HP_FALLBACK`) times the row's own cut. A
defense row loses `(covered(max) - covered(max - cost)) x hit x (1 - cut)`. An attack row loses its
bonus on the hits I land in that span: `FIGHT_WIN_SHARE / (1 - FIGHT_WIN_SHARE)` of mine per hit
of his (`er-mechanics-buffs`, `INFERRED` 0.5 there, so one each), each the same mean hit
(`INFERRED`: a mirror opponent). The cast is taken at full HP, before the first exchange, which is
when a buff is cast (`INFERRED`). Rows that need low HP (`conditionHp`, the Branchswords) would
switch on; they are not credited, since nothing models a player staying at 20% HP. The medallion
swaps a duel carries (`er-weapon-build.DUEL_MEDALLION_SWAPS`) are not credited either: each step
of the line is a 1-2% `maxHpRate` change (`MEASURED`: +3 1.10, +2 1.08, +1 1.07, +0 1.06),
below Seppuku's 15% + 100.

# The cast the opponent punishes (`cast_punish`)

A cast with no hit (a buff, camouflage) leaves the opponent nothing to roll, so the waiting reply of
`er-mechanics-ash-whiff` becomes a plain punish, on the same opponent pool, reaction quantiles and
HP rates (`whiff` with no hits, which returns None):

- hold: he presses at his reaction `r_k` (from `-lead` for a two-handed row's grip change), closes
  the gap the interrupt model gives him (`er-mechanics-neutral.arrival`), and each of his hits
  before my R1 gate takes `B_HIT_HP` x the HP rate of my window then;
- chase: `er-mechanics-backstab.circle` on this skill's own clip gives the seconds from his
  reaction to the backstab position (its `open_s` less its `REACTION_S`), from
  `er-mechanics-backstab.START_DISTANCE_M` in front, with the run from the engagement distance to
  there added (`arrival`); he backstabs for `B_BACKSTAB_HP` (`er-mechanics-ash-whiff.backstab_hp`)
  when that lands before my first roll frame and the skill does not make me unthrowable
  (`skill_blocks_backstab`);
- per pool row and reaction he takes whichever of the two is worse for me, hold on a tie, the
  rule `er-mechanics-ash-whiff` uses for its plans;
- the buff is lost only when he stops the cast before the frame its TAE applies the buff
  SpEffect (the interrupt profile's `fx_frames`; War Cry's event 331 adds 1810 at cast f17 on a
  1H Stone Club): a hold whose hits before that frame break my poise (`interrupted_before`), or
  a backstab landing before it. Then the punish also takes what the buff adds (`buff_hp`); a hit
  after that frame takes the hit only (user, 2026-10-19).

`out` = what the row delivers less that punish, so `er-mechanics-ash-whiff.net` and `combined`
take it unchanged. Steps (`er-mechanics-ash-table.ROLLING_ARTS`) are not casts: they are
invincible and move away, which the hold rule does not model; nor are the `NO_HIT_KEEP` skills,
valued by their own models.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FPS = 30.0
#: Element suffixes of the `def/atkPlayerDmgCorrectRate_*` fields (the game's "dark" is holy).
EL_SUFFIX = ("Physics", "Magic", "Fire", "Thunder", "Dark")
#: SpEffect links followed to the rows that land on the holder: `er-mechanics-ashes.SPEFFECT_LINKS`
#: without `atkOccurrenceSpEffectId`, which lands on whoever the weapon hits.
SELF_LINKS = ("replaceSpEffectId", "cycleOccurrenceSpEffectId")
#: The build-up field of each status `stateInfo` (status.md: a row applies only the status its
#: `stateInfo` names, `VERIFIED`).
STATUS_FIELD = {2: "poizonAttackPower", 5: "diseaseAttackPower", 6: "bloodAttackPower", 116: "curseAttackPower",
                260: "freezeAttackPower", 436: "sleepAttackPower", 437: "madnessAttackPower"}
#: A status row with at least this build-up procs on application (module docstring, `INFERRED`).
SURE_BUILDUP = 9999
#: `Magic.refCategory` values (`er-mechanics-spells.REF_*`): a Bullet row, a SpEffect row.
REF_BULLET, REF_SPEFFECT = 1, 2


def _sib(name: str, fname: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / fname)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules[name]


def _whiff():
    return _sib("er_mechanics_ash_whiff", "er-mechanics-ash-whiff.py")


# --------------------------------------------------------------------------------------------
# self-damage

def row_hp(row: dict, max_hp: float) -> float:
    """HP one application of SpEffect `row` takes from its holder (0 for a heal or no change)."""
    rate, point = row.get("changeHpRate", 0.0) or 0.0, row.get("changeHpPoint", 0) or 0
    return max(0.0, rate) / 100.0 * max_hp + max(0, point)


def applications(row: dict, span_s: float) -> int:
    """Applications of a row held `span_s` seconds (module docstring, `INFERRED` tick rule)."""
    span = max(span_s, row.get("effectEndurance", 0.0) or 0.0)
    iv = row.get("motionInterval", 0.0) or 0.0
    return 1 + int(span / iv + 1e-9) if iv > 0 else 1


def self_rows(sp: dict, sid: int, seen=None, depth: int = 0) -> list[int]:
    """SpEffect `sid` and the rows it links to that land on its holder, that take HP from it."""
    seen = set() if seen is None else seen
    r = sp.get(sid)
    if r is None or sid in seen or depth > 3:
        return []
    seen.add(sid)
    out = [sid] if row_hp(r, 1.0) > 0 else []
    for k in SELF_LINKS:
        v = r.get(k, -1)
        if isinstance(v, int) and v > 0:
            out += self_rows(sp, v, seen, depth + 1)
    return out


def row_status(row: dict):
    """(build-up field, build-up per application) of a status row (module docstring), or None for
    a row that takes its HP on every tick."""
    f = STATUS_FIELD.get(row.get("stateInfo", 0))
    b = (row.get(f, 0) or 0) if f else 0
    return (f, b) if b > 0 else None


def status_cost(row: dict, n: int, resist: dict | None):
    """(procs bought, why unpriced or None) for `n` applications of status row `row` on its caster
    (`status_cost` in the module docstring): 1 at `SURE_BUILDUP`, else the build-up over the
    caster's `resist[field]` up to 1, else 0 and the reason."""
    f, b = row_status(row)
    if b >= SURE_BUILDUP:
        return 1.0, None
    r = (resist or {}).get(f)
    if r:
        return min(1.0, n * b / float(r)), None
    return 0.0, f"{f} {b} x {n} on the caster: a proc needs the caster's own resistance"


def cost_rows(sp: dict, applied, resist: dict | None = None) -> dict:
    """{`pct`, `flat`, `rows` [{id, frame, n, pct, flat, procs?}], `unpriced` [{id, frame, why}]}:
    what `applied` [(SpEffect id, first frame, span in s)] take from their holder, as `pct` percent
    of max HP plus `flat` (`cost_at`). Each id is followed to the rows that land on the holder
    (`self_rows`); a status row is priced by `status_cost`."""
    rows, unpriced, pct, flat = [], [], 0.0, 0.0
    for sid, f0, span in applied:
        for i in self_rows(sp, sid):
            r = sp[i]
            n = applications(r, span)
            rate, point = max(0.0, r.get("changeHpRate", 0.0) or 0.0), max(0, r.get("changeHpPoint", 0) or 0)
            k, why = (1.0, None) if row_status(r) is None else status_cost(r, n, resist)
            if why:
                unpriced.append({"id": i, "frame": f0, "why": why})
                continue
            # A status row's HP is its proc's, once per proc; any other row's is per tick.
            m = n if row_status(r) is None else k
            rows.append({"id": i, "frame": f0, "n": n, "pct": rate * m, "flat": point * m,
                         **({} if row_status(r) is None else {"procs": round(k, 4)})})
            pct += rate * m
            flat += point * m
    return {"pct": pct, "flat": flat, "rows": rows, "unpriced": unpriced}


def cost_at(cost: dict | None, max_hp: float | None) -> float:
    """HP `cost` (`cost_rows`' `pct` and `flat`) takes from a holder of `max_hp`: 0 for no cost."""
    if not cost:
        return 0.0
    return cost.get("pct", 0.0) / 100.0 * float(max_hp or 0.0) + cost.get("flat", 0.0)


def event_applied(events) -> list:
    """[(SpEffect id, first frame, span in s)] of the TimeAct SpEffect events in `events`
    (`er-mechanics-ashes.skill_profile` entries)."""
    out = []
    for e in events:
        if e.get("kind") != "speffect" or not e.get("speffect"):
            continue
        f0, f1 = e["frames"][0], e["frames"][1] if e["frames"][1] is not None else e["frames"][0]
        out.append((e["speffect"]["id"], f0, (f1 - f0) / FPS))
    return out


def self_damage(sp: dict, events, max_hp: float, resist: dict | None = None) -> dict:
    """{`hp`, `rows` [{id, frame, per, n}], `frame` (first), `pct`, `flat`, `unpriced`}: the HP a
    cast's own SpEffect events take from its user. `events` are `er-mechanics-ashes.skill_profile`
    entries of the paid-for animation; `sp` is the raw SpEffectParam table; `resist` the caster's
    status resistances ({build-up field: resistance}), for `status_cost`."""
    c = cost_rows(sp, event_applied(events), resist)
    rows = [{"id": r["id"], "frame": r["frame"], "n": r["n"],
             "per": round(cost_at(r, max_hp) / (r["n"] if "procs" not in r else 1), 1)} for r in c["rows"]]
    return {"hp": round(cost_at(c, max_hp), 1), "rows": rows, "pct": c["pct"], "flat": c["flat"],
            "unpriced": c["unpriced"], "frame": min((r["frame"] for r in rows), default=None)}


_SKILL_COST = {}


def skill_cost(t, sid: int, wid: int, level: int = 0, grip="one", stance=None, follow_anim=None) -> dict:
    """`cost_rows` of one cast of skill `sid` on weapon `wid`: the TimeAct SpEffect events of its
    paid-for animation (`er-mechanics-ashes.main_anim`) and, for a follow-up, of the follow-up's
    clip `follow_anim`, plus the `spEffectIDForShooter` of each bullet those clips launch (once per
    launch, as `spell_cost`). Memoised per (sid, wid, level, grip, stance, follow_anim)."""
    key = (sid, wid, level, str(grip), stance, follow_anim)
    if key not in _SKILL_COST:
        A = _sib("er_mechanics_ashes", "er-mechanics-ashes.py")
        prof = A.skill_profile(t, sid, wid, level, grip, stance)
        events = list(prof["anims"].get(A.main_anim(prof), []))
        if follow_anim is not None and follow_anim != A.main_anim(prof):
            events += prof["anims"].get(follow_anim, [])
        applied = event_applied(events)
        for e in events:
            b = e.get("bullet") if e.get("kind") == "bullet" else None
            if isinstance(b, dict) and b.get("shooter_speffect"):
                applied.append((b["shooter_speffect"], e["frames"][0], 0.0))
        _SKILL_COST[key] = cost_rows(t.speffect, applied)
    return _SKILL_COST[key]


def spell_cost(sp: dict, bullets: dict, magic: dict, fired) -> dict:
    """`cost_rows` of one spell cast: for each fired `CastHighlightedMagic` event (frame, ref n),
    the `spEffectIDForShooter` of the ref's Bullet row (`refCategory` 1, applied once per event,
    `INFERRED`: the field's consumer was not traced) or the ref's own SpEffect (`refCategory` 2).
    `bullets` and `magic` are the Bullet and Magic rows by id (`magic` the spell's own row)."""
    applied = []
    for f, n in fired:
        cat, ref = magic.get(f"refCategory{n}"), magic.get(f"refId{n}", -1)
        if not ref or ref <= 0:
            continue
        if cat == REF_BULLET:
            s = (bullets.get(ref) or {}).get("spEffectIDForShooter", -1)
            if s and s > 0:
                applied.append((s, f, 0.0))
        elif cat == REF_SPEFFECT:
            applied.append((ref, f, 0.0))
    return cost_rows(sp, applied)


def skill_hurts_user(t, sid: int) -> bool:
    """Whether any clip of the skill applies a row that takes HP from its user (a cheap filter
    before reading each carrier's opening)."""
    A = _sib("er_mechanics_ashes", "er-mechanics-ashes.py")
    import struct
    for events in (A.skill_tae(t, sid) or {}).values():
        for e in events:
            if e.type in A.EV_SPEFFECT:
                for s in struct.unpack_from("<ii", e.params, 0):
                    if s > 0 and self_rows(t.speffect, s):
                        return True
    return False


# --------------------------------------------------------------------------------------------
# full-HP gear

def hp_gated_rows(tal, names) -> list[dict]:
    """[{name, id, gate, cut, bonus}]: the rows of the worn talismans `names` that work only at or
    above `gate` (share of max HP, `conditionHpRate` / 100). `cut` is the mean over elements of
    the damage-taken rate it applies (`defPlayerDmgCorrectRate_*` x `*DamageCutRate`), `bonus`
    the mean attack rate (`atkPlayerDmgCorrectRate_*`); 1 where the row has none. `tal` is
    `er-mechanics-talismans.Talismans()`."""
    out, groups = [], set()
    for n in names:
        tl = tal.get(n) if n else None
        if tl is None or tl.group in groups:
            continue
        groups.add(tl.group)
        for s in tl.speffects:
            f = s.fields
            if f.get("conditionHpRate", -1) < 0:
                continue
            cut = sum(f.get(f"defPlayerDmgCorrectRate_{e}", 1.0) for e in EL_SUFFIX) / len(EL_SUFFIX)
            cut *= f.get("neutralDamageCutRate", 1.0)
            bonus = sum(f.get(f"atkPlayerDmgCorrectRate_{e}", 1.0) for e in EL_SUFFIX) / len(EL_SUFFIX)
            if abs(cut - 1.0) < 1e-6 and abs(bonus - 1.0) < 1e-6:
                continue
            out.append({"name": tl.name, "id": s.id, "gate": f["conditionHpRate"] / 100.0,
                        "cut": round(cut, 4), "bonus": round(bonus, 4)})
    return out


def covered(hp: float, max_hp: float, gate: float, hit: float) -> int:
    """The opponent's hits that land while HP is at or above `gate` x `max_hp`, from `hp`."""
    floor = gate * max_hp
    if hp < floor - 1e-6 or hit <= 0:
        return 0
    return 1 + int((hp - floor) / hit + 1e-9)


def gated_loss(cost: float, max_hp: float, rows: list[dict], hit: float, win: float) -> dict:
    """{`hp`, `rows` [{name, id, own, lost}]}: what the gated rows stop saving and adding once a
    cast has taken `cost` HP at full HP (module docstring). `own` is what the row is worth over
    the fight without the cast, `lost` what the cast takes off it."""
    mine = win / (1.0 - win) if win < 1.0 else 0.0
    out, total = [], 0.0
    for r in rows:
        h = hit * r["cut"]
        per = hit * (1.0 - r["cut"]) + mine * hit * (r["bonus"] - 1.0)
        own = covered(max_hp, max_hp, r["gate"], h) * per
        lost = own - covered(max_hp - cost, max_hp, r["gate"], h) * per
        out.append({"name": r["name"], "id": r["id"], "own": round(own, 1), "lost": round(lost, 1)})
        total += lost
    return {"hp": round(total, 1), "rows": out}


def spent_talismans(loss: dict) -> list[str]:
    """Talismans whose every gated row the cast leaves worth nothing (`lost` >= `own` > 0)."""
    by = {}
    for r in loss["rows"]:
        by.setdefault(r["name"], []).append(r["own"] > 0 and r["lost"] >= r["own"] - 1e-6)
    return [n for n, v in by.items() if v and all(v)]


# --------------------------------------------------------------------------------------------
# the cast the opponent punishes

def chase_frames(bs, t, wid: int, sid: int, D: float, k: float, horizon: float, limits: dict,
                 circle: dict | None = None) -> float | None:
    """Frames from the opponent's reaction to the backstab position behind a caster of skill
    `sid` on weapon `wid`, from `D` in front (module docstring), or None when `circle` never gets
    there inside `horizon` frames. `bs` is `er-mechanics-backstab`, `limits` its `throw_limits`.
    `circle`: that `bs.circle` result already taken, so several distances share one."""
    NEU = _whiff().NEU
    c = circle if circle is not None else bs.circle(t, wid, sid, horizon, limits)
    if c["open_s"] is None:
        return None
    run, _ = NEU.arrival([max(0.0, D - bs.START_DISTANCE_M)], k=k)
    return max(0.0, (c["open_s"] - bs.REACTION_S) * FPS) + float(run[0])


def interrupted_before(bt, valid, q, windows, apply: float, poise: float | None):
    """Per (pool row, reaction): whether his hits at `bt` (`valid` ones, poise damage `q`) break
    my cast before frame `apply`. Each hit before `apply` takes `q` x the poise rate of my window
    open then; the cast breaks at the first hit whose running total reaches `poise` plus that
    window's bonus (`INFERRED`: the running total, without `er-mechanics-ash-interrupt.simulate`'s
    window floor and recompute). `poise` None: any hit before `apply` breaks it."""
    import numpy as np
    W = _whiff()
    early = valid & (bt < apply)
    if poise is None:
        return early.any(-1)
    bonus, mult, _ = W._window_at(windows, np.where(early, bt, -1e9))
    cum = np.cumsum(np.where(early, q * mult, 0.0), -1)
    return (early & (cum >= poise + bonus)).any(-1)


def cast_punish(opp, windows, gate: float, D: float, full: float, lead: float = 0.0,
                chase: float | None = None, roll_gate: float | None = None, blocks: bool = False,
                bs_hp: float | None = None, fx: float | None = None, buff_hp: float = 0.0,
                poise: float | None = None) -> dict:
    """The waiting reply to a cast with no hit (module docstring): `windows` and `gate` as the
    interrupt profile's, `D` the engagement distance, `full` the HP the row delivers, `chase` the
    frames from his reaction to the backstab position (`chase_frames`), `roll_gate` my first roll
    frame, `blocks` the skill makes me unthrowable, `bs_hp` the backstab. Keys as
    `er-mechanics-ash-whiff.whiff` returns them, `p_dodge` 0 (nothing to dodge).

    `fx` is the frame the cast applies its effect (the interrupt profile's `fx_frames`: the TAE
    event that adds the buff SpEffect, War Cry's 1810). A reply that breaks the cast before it
    (`interrupted_before`, with my `poise`), or a backstab that lands before it, also costs
    `buff_hp`, what the buff adds (`lost_buff` is that share); a hit after it costs the hit only,
    since the buff is already on (user, 2026-10-19). The backstab always interrupts
    (`INFERRED`)."""
    import numpy as np
    W = _whiff()
    AI, NEU, EXCH = W.AI, W.NEU, W.EXCH
    r = np.asarray(opp.offs, float) - lead                                   # (Q,)
    gap = np.maximum(0.0, D - np.maximum(EXCH.STRIKE_DISTANCE_M, opp.reach))
    approach, _ = NEU.arrival(gap, k=opp.k)                                  # (R,)
    bt = (r[None, :] + approach[:, None])[..., None] + opp.t[:, None, :]     # (R, Q, H)
    valid = opp.m[:, None, :] & (bt < gate)
    _, _, hpm = W._window_at(windows, bt)
    hold = np.where(valid, AI.B_HIT_HP * hpm, 0.0).sum(-1)                  # (R, Q)
    back = np.zeros_like(hold)
    back_early = np.zeros(hold.shape, bool)
    if chase is not None and bs_hp and not blocks and roll_gate is not None:
        f = r + chase
        _, _, hb = W._window_at(windows, f)
        back = np.broadcast_to(np.where(f < roll_gate, bs_hp * hb, 0.0)[None, :], hold.shape)
        back_early = np.broadcast_to(((f < roll_gate) & (f < fx))[None, :] if fx is not None
                                     else np.zeros(f.shape, bool)[None, :], hold.shape)
    hold_early = (interrupted_before(bt, valid, opp.q[:, None, :], windows, fx, poise)
                  if fx is not None and buff_hp else np.zeros(hold.shape, bool))
    # He takes whichever plan is worse for me, the lost buff included.
    hold_all = hold + np.where(hold_early, buff_hp, 0.0)
    back_all = back + np.where(back_early, buff_hp, 0.0)
    stab = back_all > hold_all
    taken = np.where(stab, back_all, hold_all)
    lost = np.where(stab, back_early, hold_early)
    Wt = np.broadcast_to(opp.w[:, None] / len(r), taken.shape)
    punish = float((Wt * taken).sum())
    lost_share = float((Wt * lost).sum())
    return {"T": float(fx if fx is not None else 0.0), "lead": lead, "gate": gate, "full": full,
            "p_dodge": 0.0, "punish_hp": punish, "late": 0.0, "stopped": 0.0, "trade": 0.0,
            "backstab": float((Wt * stab).sum()), "miss": 1.0, "strafe": 0.0, "skill": 0.0,
            "skill_top": None, "geometry": 0, "cast": 1, "lost_buff": lost_share,
            "lost_hp": lost_share * buff_hp, "out": full - punish}


# --------------------------------------------------------------------------------------------

def selftest() -> int:
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # Self-damage rows: Seppuku's 1753 shape, a heal and a linked self-damage row.
    sp = {1753: {"changeHpRate": 15.0, "changeHpPoint": 100, "effectEndurance": 0.1, "motionInterval": 0.2,
                 "stateInfo": 6, "bloodAttackPower": 9999},
          1755: {"changeHpRate": 0.0, "changeHpPoint": 0, "effectEndurance": 60.0, "motionInterval": 0.0,
                 "cycleOccurrenceSpEffectId": 1756, "atkOccurrenceSpEffectId": 1753},
          1756: {"changeHpRate": 0.0, "changeHpPoint": 10, "effectEndurance": 1.0, "motionInterval": 0.25},
          1641: {"changeHpRate": -5.0, "changeHpPoint": -10, "effectEndurance": 0.1, "motionInterval": 0.0}}
    ev = [{"kind": "speffect", "frames": (31.0, 34.0), "speffect": {"id": 1753}}]
    got = self_damage(sp, ev, 2594.0)
    check(got["hp"] == round(0.15 * 2594 + 100, 1) and got["frame"] == 31.0,
          "Seppuku's 1753 takes 15% of max HP plus 100, once")
    got = self_damage(sp, [{"kind": "speffect", "frames": (0.0, 3.0), "speffect": {"id": 1755}}], 2594.0)
    check(got["hp"] == 50.0 and [r["id"] for r in got["rows"]] == [1756],
          "a linked self row ticks 1 + 1.0 / 0.25 times; the on-hit link lands on the target, not here")
    check(self_damage(sp, [{"kind": "speffect", "frames": (0.0, 3.0), "speffect": {"id": 1641}}], 2594.0)["hp"] == 0.0,
          "a heal takes no HP")
    check(self_damage(sp, [{"kind": "melee", "frames": (31.0, 34.0)}], 2594.0)["hp"] == 0.0,
          "a non-damaging cast (no self row) costs nothing")
    # HP cost as a share of max HP plus flat (`cost_rows`, `cost_at`): Bloody Slash's 1763 shape.
    sp.update({1763: {"changeHpRate": 5.0, "changeHpPoint": 50, "effectEndurance": 0.1, "motionInterval": 0.2,
                      "stateInfo": 0},
               1782: {"changeHpRate": 15.0, "changeHpPoint": 100, "effectEndurance": 1.0, "motionInterval": 5.0,
                      "stateInfo": 437, "madnessAttackPower": 4},
               1490000: {"changeHpRate": 0.0, "changeHpPoint": 80, "effectEndurance": 0.0, "motionInterval": 0.0,
                         "replaceSpEffectId": 1490001},
               1490001: {"changeHpRate": 15.0, "changeHpPoint": 100, "effectEndurance": 0.0, "motionInterval": 0.0,
                         "stateInfo": 6, "bloodAttackPower": 30}})
    c = cost_rows(sp, [(1763, 15.0, 0.0)])
    check((c["pct"], c["flat"], cost_at(c, 2000.0), cost_at(c, 1000.0)) == (5.0, 50, 150.0, 100.0),
          "Bloody Slash's 1763: 5% of max HP plus 50, so 150 at 2000 HP and 100 at 1000")
    check(cost_at(cost_rows(sp, [(1641, 0.0, 0.0)]), 2000.0) == 0.0 and cost_at(None, 2000.0) == 0.0,
          "a heal or no cost row prices 0")
    mad = cost_rows(sp, [(1782, f, 0.0) for f in (10.0, 12.0, 14.0, 16.0, 18.0)])
    check(cost_at(mad, 2000.0) == 0.0 and len(mad["unpriced"]) == 5,
          "a small madness build-up on the caster is unpriced without the caster's resistance, never charged in full")
    mad = cost_rows(sp, [(1782, f, 0.0) for f in (10.0, 12.0, 14.0, 16.0, 18.0)], {"madnessAttackPower": 400})
    check(abs(cost_at(mad, 2000.0) - 5 * 4 / 400 * (0.15 * 2000 + 100)) < 1e-9 and not mad["unpriced"],
          "with a resistance it is the build-up's share of one proc")
    br = cost_rows(sp, [(1490000, 11.0, 0.0)])
    check((br["flat"], [r["id"] for r in br["rows"]], [u["id"] for u in br["unpriced"]]) == (80, [1490000], [1490001]),
          "Briars of Sin's shooter row: 80 HP, and its replace link's bleed build-up is unpriced")
    mg = {"refCategory1": REF_BULLET, "refId1": 10490000, "refCategory2": REF_SPEFFECT, "refId2": 1763,
          "refCategory3": 0, "refId3": 300}
    sc = spell_cost(sp, {10490000: {"spEffectIDForShooter": 1490000}}, mg, [(11.0, 1), (22.0, 2), (30.0, 3)])
    check((sc["pct"], sc["flat"]) == (5.0, 130),
          "spell_cost: a bullet ref's shooter row and a SpEffect ref's row, an AtkParam ref nothing")

    # Full-HP gear: Ritual Shield's shape, the opponent hit 388.
    shield = [{"name": "Ritual Shield Talisman", "id": 340900, "gate": 1.0, "cut": 0.7, "bonus": 1.0}]
    sword = [{"name": "Ritual Sword Talisman", "id": 320500, "gate": 1.0, "cut": 1.0, "bonus": 1.1}]
    half = [{"name": "Half", "id": 1, "gate": 0.5, "cut": 0.7, "bonus": 1.0}]
    hit, mx = 388.0, 2594.0
    check(covered(mx, mx, 1.0, hit * 0.7) == 1 and covered(mx - 1, mx, 1.0, hit * 0.7) == 0,
          "a full-HP row covers the first hit, and none once any HP is gone")
    loss = gated_loss(489.1, mx, shield, hit, 0.5)
    check(abs(loss["hp"] - hit * 0.3) < 0.1 and loss["rows"][0]["own"] == loss["rows"][0]["lost"],
          "Ritual Shield loses the 30% it saves on the first hit: its whole value")
    check(spent_talismans(loss) == ["Ritual Shield Talisman"], "a spent full-HP talisman is named")
    check(gated_loss(0.0, mx, shield, hit, 0.5)["hp"] == 0.0 and not spent_talismans(gated_loss(0.0, mx, shield, hit, 0.5)),
          "a non-damaging cast leaves the talisman whole")
    check(abs(gated_loss(489.1, mx, sword, hit, 0.5)["hp"] - hit * 0.1) < 0.1,
          "Ritual Sword loses its 10% on the one hit of mine before his first")
    hl = gated_loss(489.1, mx, half, hit, 0.5)
    n0 = covered(mx, mx, 0.5, hit * 0.7)
    n1 = covered(mx - 489.1, mx, 0.5, hit * 0.7)
    check(n0 == 5 and n1 == 3 and abs(hl["hp"] - (n0 - n1) * hit * 0.3) < 0.1 and not spent_talismans(hl),
          "a 50% row loses only the hits the cost pushes under its gate, and is not spent")

    # The cast the opponent punishes, on a toy pool.
    import numpy as np
    from types import SimpleNamespace
    W = _whiff()
    hp = W.AI.B_HIT_HP
    opp = SimpleNamespace(offs=np.array([10.0, 30.0]), w=np.array([1.0]), reach=np.array([3.0]), k=7.5,
                          t=np.array([[15.0, 30.0]]), q=np.array([[60.0, 60.0]]), m=np.array([[True, True]]))
    # No approach (reach 3 m >= D 2.5 m). Hits at r+15, r+30: r 10 -> 25, 40; r 30 -> 45, 60.
    p = cast_punish(opp, [], 50.0, 2.5, 300.0)
    check(abs(p["punish_hp"] - 0.5 * (2 + 1) * hp) < 1e-6 and p["p_dodge"] == 0.0
          and abs(W.net(p) - (300.0 - p["punish_hp"]) / 300.0) < 1e-9,
          "a no-hit cast is punished by every hit before its gate, nothing dodged")
    check(cast_punish(opp, [], 20.0, 2.5, 300.0)["punish_hp"] == 0.0, "a gate before his first hit is safe")
    p = cast_punish(opp, [], 50.0, 2.5, 300.0, chase=20.0, roll_gate=45.0, bs_hp=1000.0)
    check(abs(p["backstab"] - 0.5) < 1e-9 and abs(p["punish_hp"] - 0.5 * (1000.0 + hp)) < 1e-6,
          "he backstabs where he gets behind before my roll gate, else strikes")
    p = cast_punish(opp, [], 50.0, 2.5, 300.0, chase=20.0, roll_gate=45.0, bs_hp=1000.0, blocks=True)
    check(p["backstab"] == 0.0, "an unthrowable skill is not backstabbed")
    p = cast_punish(opp, [(0.0, 100.0, 0.0, 1.0, 0.0, 0.5)], 50.0, 2.5, 300.0)
    check(abs(p["punish_hp"] - 0.5 * 3 * hp * 0.5) < 1e-6, "a window's HP rate scales the punish")
    check(W.combined(1.0, W.net(cast_punish(opp, [], 50.0, 2.5, 300.0))) < 1.0,
          "the punished cast lowers the combined multiplier")
    # The buff applied at f35: his first hit at 25 (reaction 10) beats it, at 45 (reaction 30) not.
    p = cast_punish(opp, [], 50.0, 2.5, 300.0, fx=35.0, buff_hp=200.0)
    check(abs(p["lost_buff"] - 0.5) < 1e-9 and abs(p["punish_hp"] - (0.5 * 3 * hp + 0.5 * 200.0)) < 1e-6,
          "a hit before the buff's frame costs the buff too; a hit after it costs the hit only")
    check(cast_punish(opp, [], 50.0, 2.5, 300.0, fx=20.0, buff_hp=200.0)["lost_buff"] == 0.0,
          "a buff applied before his first hit is never lost")
    check(cast_punish(opp, [], 50.0, 2.5, 300.0, fx=35.0, buff_hp=200.0, poise=100.0)["lost_buff"] == 0.0
          and abs(cast_punish(opp, [], 50.0, 2.5, 300.0, fx=35.0, buff_hp=200.0, poise=50.0)["lost_buff"]
                  - 0.5) < 1e-9,
          "a hit my poise holds does not stop the cast; one that breaks it does")
    check(cast_punish(opp, [(0.0, 30.0, 100.0, 1.0, 0.0, 1.0)], 50.0, 2.5, 300.0, fx=35.0, buff_hp=200.0,
                      poise=50.0)["lost_buff"] == 0.0,
          "a hyperarmor window's bonus holds the hit through the windup")
    p = cast_punish(opp, [], 50.0, 2.5, 300.0, chase=20.0, roll_gate=45.0, bs_hp=1000.0, fx=35.0, buff_hp=200.0)
    check(abs(p["backstab"] - 0.5) < 1e-9 and abs(p["lost_buff"] - 0.5) < 1e-9
          and abs(p["punish_hp"] - 0.5 * (1000.0 + 200.0 + hp)) < 1e-6,
          "a backstab before the buff's frame takes the buff with it")
    check(cast_punish(opp, [], 50.0, 2.5, 300.0, fx=35.0)["punish_hp"]
          == cast_punish(opp, [], 50.0, 2.5, 300.0)["punish_hp"],
          "a cast with no buff value loses nothing at its effect frame")
    print(f"{'FAIL' if fails else 'ok'}: {len(fails)} failed")
    return 1 if fails else 0


def list_costs(max_hp: float) -> list[dict]:
    """Every skill and spell of the regulation whose cast takes HP from its user: skills by their
    TimeAct (`skill_hurts_user`, every clip of `a<600 + swordArtsTypeNew>` read), spells by their
    `CastHighlightedMagic` refs (every clip of `a<400 + refType>`, `spell_cost`). Each row:
    {kind, id, name, clips: {clip: {pct, flat, unpriced}}, hp (the dearest clip at `max_hp`)}."""
    A = _sib("er_mechanics_ashes", "er-mechanics-ashes.py")
    import struct
    t = A.AshTables()
    files = A.PR.load(None)
    rows_, _, _ = A.PR.rows(A.PR.param_bytes(files, "Magic"))
    magic = {r["id"]: r for r in rows_}
    bullets = t.bullet
    mnames = A.PR.row_names("Magic")
    out = []

    def summary(c):
        return {"pct": c["pct"], "flat": c["flat"],
                "unpriced": sorted({u["why"] for u in c["unpriced"]})}
    for sid in sorted(t.arts):
        if not skill_hurts_user(t, sid):
            continue
        clips = {}
        for anim, events in sorted((A.skill_tae(t, sid) or {}).items()):
            applied = [(s, round(e.start * FPS, 1), max(0.0, e.end - e.start))
                       for e in events if e.type in A.EV_SPEFFECT
                       for s in struct.unpack_from("<ii", e.params, 0) if s > 0]
            c = cost_rows(t.speffect, applied)
            if c["rows"] or c["unpriced"]:
                clips[anim] = summary(c)
        out.append({"kind": "skill", "id": sid, "name": t.arts_name(sid), "clips": clips})
    for mid, m in sorted(magic.items()):
        refs = {n for n in range(1, 11) if m.get(f"refId{n}", -1) > 0}
        c0 = spell_cost(t.speffect, bullets, m, [(0.0, n) for n in sorted(refs)])
        if not (c0["rows"] or c0["unpriced"]):
            continue
        clips = {}
        cat = 400 + m["refType"]
        for anim in sorted(A.ATK.tae_animations(cat) or {}):
            fired = [(round(e.start * FPS, 1), e.params[8] + 1)
                     for e in (A.ATK.resolve_events(cat, anim)[2] or []) if e.type == 64 and len(e.params) > 8]
            c = spell_cost(t.speffect, bullets, m, fired)
            if c["rows"] or c["unpriced"]:
                clips[anim] = summary(c)
        out.append({"kind": "spell", "id": mid, "name": mnames.get(mid) or str(mid), "clips": clips})
    for r in out:
        r["hp"] = round(max((cost_at(c, max_hp) for c in r["clips"].values()), default=0.0), 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skill", nargs="?")
    ap.add_argument("--list", action="store_true",
                    help="every skill and spell whose cast takes HP from its user, per clip (--max-hp prices it)")
    ap.add_argument("--weapon", default="Estoc")
    ap.add_argument("--grip", default="2H", choices=("1H", "2H"))
    ap.add_argument("--talismans", default="", help="comma-separated worn talismans")
    ap.add_argument("--max-hp", type=float, default=2292.5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.list:
        for r in list_costs(a.max_hp):
            print(f"{r['kind']:<5} {r['id']:>8} {r['name']}: {r['hp']:.0f} HP at {a.max_hp:g} max HP (dearest clip)")
            for anim, c in r["clips"].items():
                print(f"      {anim:06d}  {c['pct']:g}% of max HP + {c['flat']:g}"
                      + (f"; unpriced: {'; '.join(c['unpriced'])}" if c["unpriced"] else ""))
        return 0
    if not a.skill:
        ap.error("name a skill, or --selftest")
    A = _sib("er_mechanics_ashes", "er-mechanics-ashes.py")
    t = A.AshTables()
    sid, wid = t.find_arts(a.skill), t.find_weapon(a.weapon)
    prof = A.skill_profile(t, sid, wid, 0, a.grip)
    sd = self_damage(t.speffect, prof["anims"].get(A.main_anim(prof), []), a.max_hp)
    print(f"{a.skill} on {a.weapon} {a.grip}: takes {sd['hp']:.0f} HP of {a.max_hp:g} from its user; rows {sd['rows']}")
    names = [n.strip() for n in a.talismans.split(",") if n.strip()]
    if names:
        tal = _sib("er_mechanics_talismans", "er-mechanics-talismans.py").Talismans()
        rows = hp_gated_rows(tal, names)
        W = _whiff()
        loss = gated_loss(sd["hp"], a.max_hp, rows, W.AI.B_HIT_HP, _sib("er_mechanics_buffs", "er-mechanics-buffs.py").FIGHT_WIN_SHARE)
        print(f"HP-gated rows worn: {rows}")
        print(f"switched off: {loss['hp']:.0f} HP over the fight ({loss['rows']}); spent: {spent_talismans(loss)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
