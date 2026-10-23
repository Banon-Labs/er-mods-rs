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
whose span is shorter than its interval, which is every self-damage row in the regulation).
Seppuku's 1753 also carries `bloodAttackPower` 9999; that build-up is taken as this same row's
HP change (its `stateInfo` 6, the blood-loss state), not a second proc (`INFERRED`).

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


def self_damage(sp: dict, events, max_hp: float) -> dict:
    """{`hp`, `rows` [{id, frame, per, n}], `frame` (first)}: the HP a cast's own SpEffect events
    take from its user. `events` are `er-mechanics-ashes.skill_profile` entries of the paid-for
    animation; `sp` is the raw SpEffectParam table."""
    rows, hp = [], 0.0
    for e in events:
        if e.get("kind") != "speffect" or not e.get("speffect"):
            continue
        f0, f1 = e["frames"][0], e["frames"][1] if e["frames"][1] is not None else e["frames"][0]
        for i in self_rows(sp, e["speffect"]["id"]):
            n = applications(sp[i], (f1 - f0) / FPS)
            per = row_hp(sp[i], max_hp)
            rows.append({"id": i, "frame": f0, "per": round(per, 1), "n": n})
            hp += per * n
    return {"hp": round(hp, 1), "rows": rows, "frame": min((r["frame"] for r in rows), default=None)}


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
    sp = {1753: {"changeHpRate": 15.0, "changeHpPoint": 100, "effectEndurance": 0.1, "motionInterval": 0.2},
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skill", nargs="?")
    ap.add_argument("--weapon", default="Estoc")
    ap.add_argument("--grip", default="2H", choices=("1H", "2H"))
    ap.add_argument("--talismans", default="", help="comma-separated worn talismans")
    ap.add_argument("--max-hp", type=float, default=2292.5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
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
