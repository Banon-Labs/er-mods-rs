#!/usr/bin/env python3
"""Parrying in both directions: my parry tools against the PvP corpus, and the corpus's parry tools
against my weapons.

    python3 scripts/er-mechanics-parry.py --build <build.json> --main "Executioner's Greataxe" \
        --main-affinity Heavy [--weapons-from <embed.json>] --json <out.json>
    python3 scripts/er-mechanics-parry.py --selftest

The user asked for it on 2026-10-10 ("Model parrying"): a parry model for the PvP build planner,
both directions, wired into the plan. Labels as `docs/er-mechanics/attacks.md`: `VERIFIED`, `TAE`,
`MEASURED`, `INFERRED`, `COMMUNITY`.

Mechanism (`er-mechanics-crits`, `VERIFIED` `FUN_14044a910`): a hit is parried only while the
attacker's animation has JumpTable 5 open. Two routes reach that test:

    hitbox   the parry skill's AttackBehavior with AttackType 64 touches the attacker;
    contact  the attacker's hit touches a parrier whose JumpTable 119 is open, and the hit's
             AtkParam_Pc.isDisableParry is 0.

A full scan of the player TimeActs (`TAE`, 2026-10-10) finds JumpTable 119 only in the parry skill
files a692-a699 and in a002/a003 anim 040392 (an AttackType 64 judge 591 clip of a weapon
category; which skill plays it is not resolved). No guard, block or guard-counter animation opens
it, so there is no just-guard route for a player: the guard-counter path cannot parry. Golden
Retaliation (SwordArtsParam 1196, a796) has neither an AttackType 64 hitbox nor JumpTable 119 and
is left out. No parry clip carries a TAE 608 play-speed window or an event 66 counter-frame
SpEffect, so its clip frames are real frames and a failed parry is not a counter hit.

Parry tools (`tools`): every parry skill (`er-mechanics-crits.PARRY_SWORD_ARTS`) on every base
weapon the build wields one-handed that can carry it, as its own skill or mounted from an ash a
player can obtain (`er-mechanics-ashes.mountable_skills`, `obtainable_skills`), cast from the left hand while the right hand is held one-handed: L2 fires the
left weapon's skill then (`er-mechanics-ashes.active_skill`, `VERIFIED` `FUN_14047f770`). Per tool:
the hitbox and JumpTable 119 frames of the skill's opening on that carrier (`TAE`), the parry
hitbox's radius and dummy poly (AtkParam_Pc, regulation), the clip's dodge cancel (the recovery a
whiff sits in), the BehaviorParam stamina cost and the skill's FP.

Corpus attacks (`Corpus`): the RL 100-150 PvP builds (`er-mechanics-powerstance-guard
.blocker_corpus`, the window `er-mechanics-shields-ranged` uses). Per build, its right-hand weapon
as held (`is2h`) and every attack slot of it (`er-mechanics-attacks.weapon_attacks`) plus the skill
its L2 fires. Slots fall in families, `r1` (the R1 chain), `r2` (R2s and charged R2s), `move`
(running, rolling, backstep and crouch attacks), `jump` and `skill`; each family has an equal share
of the build's attacks and slots share their family equally (`INFERRED`, the convention of
`er-mechanics-interrupt.POOL_FAMILIES` with the build's own skill added as a fifth family: no match
log exists to weigh them). A skill that does not attack (a parry, a buff) drops its family. Per
attack: the first hit frame, JumpTable 5 windows in real frames (`TAE`, through the clip's play
speed), isDisableParry, and the first hit's HP on me (the attacker's AR at his stats, the hit's
motion values, the weapon's vsPlayer rate, my defense and absorption: `er-mechanics-defense`).

Catch (`catch_intervals`, `p_catch`). Frame 0 is the attack's cue: the start of its clip. An R2's
hit frames are on its released clip, so its cue is the release the attacker chose (the lead-in
before it is the hold, `release_lead_in`), which is how a held attack hides its timing. A press at
frame `p` catches when

    hitbox   [p + s, p + e] meets a JumpTable 5 window [js, je] no later than the first hit h:
             p in [js - e, min(je, h) - s]
    contact  isDisableParry 0, JumpTable 5 open at h, JumpTable 119 open at h:
             p in [h - e119, h - s119]

with (s, e) the parry hitbox frames and (s119, e119) its JumpTable 119 frames; a window that opens
after the first hit guards a later swing and is ignored (the first swing would land first). Two
kinds of press, mixed by `er-mechanics-ashes.REACT_SHARE` (0.5, `INFERRED`):

- reaction (`p_react`): the parrier sees the cue and aims his press at frame `c`, chosen to catch
  most often; he cannot press before his reaction delay `d` (lognormal, median
  `REACTION_MEDIAN_S` 0.25 s, log sd 0.2, plus two `NETWORK_ONE_WAY_S` 0.05 s legs:
  `er-mechanics-ashes.reaction_delays`, the same nine quantiles). The press lands at
  max(d, c + eps), eps normal with sd sqrt(`TIMING_SD_S`^2 + (`TIMING_CV` x c)^2): the dodge
  timing error of `er-mechanics-ashes.DODGE_TIMING_SD_S` plus a scalar timing term, so the longer
  the attack's telegraph the less exact the press (`INFERRED`: interval timing error grows with the
  interval, the scalar property of human timing; 0.10 is a modelling choice, no game data says
  it). So a fast hit is lost to the reaction floor and a slow or delayed one to the timing spread.
- read (`p_read`): a press committed before the cue, landing uniformly anywhere from the attack's
  cue to its first hit; the share of [0, h] the catch set covers (`er-mechanics-shields-ranged`'s
  timed half, kept).

Range and angle are not modelled: the hitbox route is taken to reach whenever the attack does
(the parry hitbox is a 0.55 m sphere at dummy poly 131 for every parry judge 591 row; Golden
Parry's larger judge-3690 hitbox shows only through its longer JumpTable 119 window).

Payoff (my side, per attempt): a catch is a riposte with my main-hand pick, one-handed
(`er-mechanics-crits.crit_damage` on the crits reference defender, as `ShieldModel` takes it; the
riposte depends on my weapon, not the parry tool); a miss eats the attack's first hit (H). The
parrier knows the opponent's weapon and only parries an attack that has a JumpTable 5 window
guarding its first hit; anything else he blocks with the tool's own guard (`INFERRED`). So per
attack, `ev_abs` = c x R - (1 - c) x H when parryable, -(1 - g) x H otherwise, g the tool's block
value (`er-mechanics-powerstance-guard.block_hit`: element chip and stamina drain against my
stamina, the `ShieldModel.guard` base without its repel punish credit). `ev_raw` is the parry
alone over parryable attacks. Each is reported over the corpus openings (each build's R1 #1, what
`ShieldModel` answers) and over the full attack mix.

Cost (their side, per attack I throw): carry x attempt x catch x riposte, where

- carry = the share of corpus builds whose L2 is a parry skill (`MEASURED`);
- catch = `p_catch` of my attack against the corpus's parry tools, weighted by how often each
  (skill, carrier) is carried (`MEASURED` mix, `TAE` frames);
- attempt = `er-mechanics-crits.PARRY_LAND` (0.10, `INFERRED`) / the corpus's mean catch over its
  own parryable attacks against the same tool mix: a carrier tries a parry on an attack at a fixed
  rate, set so the corpus-average landed share stays the land rate the crit factor already uses;
- riposte = the mean over carriers of their right-hand weapon's one-handed riposte on me.

`weapons` holds that cost per weapon and grip (1H, 2H, L1) for `er-mechanics-class-roles.py`;
`ash_cost` gives it per skill cast for `er-mechanics-combo-loadout.py`.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import statistics
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_MODS = {}


def _load(key, fname):
    if key not in _MODS:
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, HERE / fname)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[key] = mod
            spec.loader.exec_module(mod)
        _MODS[key] = mod
    return _MODS[key]


def CR():
    return _load("er_mechanics_crits", "er-mechanics-crits.py")


def G():
    return _load("er_mechanics_powerstance_guard", "er-mechanics-powerstance-guard.py")


def AS():
    return _load("er_mechanics_ashes", "er-mechanics-ashes.py")


def SR():
    return _load("er_mechanics_shields_ranged", "er-mechanics-shields-ranged.py")


STATS = ("str", "dex", "int", "fth", "arc")
ELEMENTS = ("physical", "magic", "fire", "lightning", "holy")
VS_PLAYER = {"physical": "Physics", "magic": "Magic", "fire": "Fire", "lightning": "Thunder", "holy": "Dark"}
#: TAE: event type 0 JumpTable, its id at Args+0; 5 "Get-Parried Window", 119 force-parry mode.
TAE_JUMP_TABLE, JT_GET_PARRIED, JT_FORCE_PARRY = 0, 5, 119
#: Scalar timing coefficient: timing sd grows by this share of the aimed interval (`INFERRED`).
TIMING_CV = 0.10
#: Aim points are tried every half real frame (the 60 Hz update, `er-mechanics-ashes.DODGE_STEP`).
AIM_STEP = 0.5
#: Attack families, equal shares (`INFERRED`, module docstring).
FAMILIES = ("r1", "r2", "move", "jump", "skill")
MOVE_SLOTS = ("run_r1", "run_r2", "roll_r1", "bstep_r1", "crouch_r1")
#: The off-hand L1 chain slots (`er-mechanics-powerstance-guard.OFFHAND_SLOTS`).
LEFT_SLOTS = ("left_1", "left_2", "left_3", "left_4", "left_5", "left_6")
_ND = statistics.NormalDist()


def family_of(slot):
    """The attack family of a `weapon_attacks` slot key, or None (guard counters need a blocked
    hit first; powerstance slots are not the pool's attacks)."""
    k = slot.removeprefix("2h_")
    if k.startswith("r1_"):
        return "r1"
    if k.startswith("r2_"):
        return "r2"
    if k in MOVE_SLOTS:
        return "move"
    if k.startswith("jump_"):
        return "jump"
    if k in LEFT_SLOTS:
        return "l1"
    return None


# ---------------------------------------------------------------------------------------------
# frames


def jt_windows(events, jid, to_real=None):
    """[(start, end)] real frames of every JumpTable `jid` event, through the clip's play speed."""
    ATK = CR().ATTACKS
    to_real = to_real or ATK.clip_to_real(events)
    return [(ATK.real_frame(to_real(e.start)), ATK.real_frame(to_real(e.end))) for e in events
            if e.type == TAE_JUMP_TABLE and struct.unpack_from("<i", e.params, 0)[0] == jid]


def attack_of_row(ct, row):
    """{'jt5', 'hits', 'disable'} of one `weapon_attacks` row (or an off-hand row): real frames
    from the clip start, every swing's first frame (own and other judges' hitboxes that hit)."""
    ATK = CR().ATTACKS
    cat, anim = (int(x) for x in row["tae_entry"][1:].split("_"))
    _, _, events = ATK.resolve_events(cat, anim)
    jt5 = tuple(jt_windows(events, JT_GET_PARRIED)) if events else ()
    hits = sorted(s for s, _ in (row.get("hit_windows") or [])
                  + [x["frames"] for x in row.get("other_hitboxes") or [] if x.get("hits")])
    disable = bool(ct.atk.get(row["atk_row"], {}).get("isDisableParry", 1))
    return {"jt5": jt5, "hits": tuple(hits), "disable": disable}


def offhand_attack(ct, r):
    """The same for an `er-mechanics-powerstance-guard.offhand_rows` row (clip frames carried to
    real ones as `er-mechanics-weapon-embed` does for the L1 chain)."""
    ATK = CR().ATTACKS
    cat, anim = (int(x) for x in r["anim"][1:].split("_"))
    _, _, events = ATK.resolve_events(cat, anim)
    if not events:
        return None
    to_real = ATK.clip_to_real(events)

    def rf(clip):
        return ATK.real_frame(to_real(clip / ATK.TAE_FPS))
    jt5 = tuple(jt_windows(events, JT_GET_PARRIED, to_real))
    hits = tuple(sorted(rf(h["frames"][0]) for h in r["hits"]))
    disable = bool(ct.atk.get(r["hits"][0]["atk_row"], {}).get("isDisableParry", 1))
    return {"jt5": jt5, "hits": hits, "disable": disable}


def parryable(atk):
    """A JumpTable 5 window guards the attack's first swing."""
    return bool(atk and atk["hits"] and any(js - 1 <= atk["hits"][0] for js, _ in atk["jt5"]))


# ---------------------------------------------------------------------------------------------
# catch


def catch_intervals(atk, tool):
    """Merged [(lo, hi)] press frames that parry `atk` (module docstring). `tool`:
    {'hitbox': (s, e), 'contact': (s119, e119) or None}."""
    if not atk or not atk["hits"] or not atk["jt5"]:
        return []
    h0 = atk["hits"][0]
    s, e = tool["hitbox"]
    c = tool.get("contact")
    spans = []
    for js, je in atk["jt5"]:
        # Each window guards the first swing that starts with or after it (crits' rule, 1 frame
        # of slack); only the first swing's window counts.
        if js - 1 > h0:
            continue
        spans.append((js - e, min(je, h0) - s))
        if c and not atk["disable"] and js - 1 <= h0 <= je:
            spans.append((h0 - c[1], h0 - c[0]))
    out = []
    for lo, hi in sorted(x for x in spans if x[1] >= x[0]):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _cdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, float) / math.sqrt(2.0)))


def p_react(iv, delays, sd_frames, cv=TIMING_CV):
    """The best aim's catch share of a reacting parrier (module docstring): press at
    max(d, c + eps), d over `delays` [(frames, weight)], eps ~ N(0, sqrt(sd^2 + (cv c)^2))."""
    if not iv:
        return 0.0
    lo_all, hi_all = min(a for a, _ in iv), max(b for _, b in iv)
    cs = np.arange(math.floor(lo_all) - 4.0, math.ceil(hi_all) + 4.0 + 1e-9, AIM_STEP)
    sig = np.sqrt(sd_frames ** 2 + (cv * np.maximum(cs, 0.0)) ** 2)
    tot = np.zeros_like(cs)
    for d, w in delays:
        inside = any(a <= d <= b for a, b in iv)
        p = _cdf((d - cs) / sig) if inside else np.zeros_like(cs)
        for a, b in iv:
            lo = max(a, d)
            if b > lo:
                p = p + np.clip(_cdf((b - cs) / sig) - _cdf((lo - cs) / sig), 0.0, None)
        tot += w * p
    return float(min(1.0, tot.max()))


def p_read(iv, h0):
    """Share of [0, h0] the catch set covers: a press committed anywhere in the windup."""
    if not iv or not h0 or h0 <= 0:
        return 0.0
    covered = sum(max(0.0, min(hi, h0) - max(lo, 0.0)) for lo, hi in iv)
    return min(1.0, covered / h0)


class Catcher:
    """`p_catch(atk, tool)`, cached on the attack's and the tool's frames."""

    def __init__(self, react_share=None, delays=None, sd_frames=None, cv=TIMING_CV):
        A = AS()
        self.rs = A.REACT_SHARE if react_share is None else react_share
        self.delays = delays if delays is not None else A.reaction_delays()
        self.sd = sd_frames if sd_frames is not None else A.DODGE_TIMING_SD_S * A.TAE_FPS
        self.cv = cv
        self._c = {}

    def parts(self, atk, tool):
        key = (atk["jt5"], atk["hits"][:1] if atk else (), atk["disable"] if atk else True,
               tool["hitbox"], tool.get("contact"))
        if key not in self._c:
            iv = catch_intervals(atk, tool)
            r = p_react(iv, self.delays, self.sd, self.cv)
            t = p_read(iv, atk["hits"][0] if atk and atk["hits"] else None)
            self._c[key] = (r, t, self.rs * r + (1.0 - self.rs) * t)
        return self._c[key]

    def __call__(self, atk, tool):
        return self.parts(atk, tool)[2]

    def mix(self, atk, tools):
        """Catch against a weighted tool mix [(tool, weight)]."""
        return sum(w * self(atk, tl) for tl, w in tools)


# ---------------------------------------------------------------------------------------------
# parry tools


def tool_frames(t, sid, wid, level=0):
    """The parry tool `sid` on weapon `wid`, one-handed: {'anim', 'hitbox', 'contact', 'judge',
    'atk_row', 'stamina', 'fp', 'length', 'dodge'} in real frames, or None when its opening has
    no AttackType 64 hitbox."""
    A = AS()
    ATK = CR().ATTACKS
    prof = A.skill_profile(t, sid, wid, level, "one")
    anim = A.main_anim(prof)
    acts = prof["anims"].get(anim, [])
    par = [x for x in acts if x["kind"] == "parry"]
    if not par:
        return None
    events = (A.skill_tae(t, sid) or {}).get(anim) or []
    to_real = ATK.clip_to_real(events)

    def rf(clip):
        return ATK.real_frame(to_real(clip / ATK.TAE_FPS))
    hb = (rf(min(x["frames"][0] for x in par)), rf(max(x["frames"][1] for x in par)))
    jt = jt_windows(events, JT_FORCE_PARRY, to_real)
    contact = (min(a for a, _ in jt), max(b for _, b in jt)) if jt else None
    cat = int(prof["tae"][1:])
    w = t.reg.weapon[wid]
    dodge = length = None
    try:
        rec = ATK.recovery_details(w, anim, events, [hb], category=cat, to_real=to_real)
        dodge, length = rec["cancel_frame"].get("dodge"), rec.get("anim_frames")
    except (KeyError, TypeError, ValueError, SystemExit):
        pass
    return {"skill": prof["name"], "sid": sid, "anim": f"{prof['tae']}_{anim:06d}", "hitbox": hb,
            "contact": contact, "judge": par[0]["judge"], "atk_row": par[0].get("atk_row"),
            "stamina": par[0].get("stamina_cost"), "fp": A.skill_fp(t, sid), "dodge": dodge, "length": length}


def obtainable_skills(t):
    """SwordArtsParam ids some ash-of-war item with a real sortId carries: the ones a player can
    mount (`er-mechanics-ash-table`'s rule; Buckler Parry's gem is sortId 999999, so it is only
    ever the Buckler's own skill)."""
    unob = SR().table_mod().UNOBTAINABLE_SORT_ID
    return {g["swordArtsParamId"] for g in t.gem.values()
            if g.get("swordArtsParamId", -1) > 0 and g["sortId"] != unob}


def hitbox_shape(ct_atk_rows, atk_row):
    """(radius m, dummy poly) of a parry AtkParam_Pc row's first hitbox (regulation)."""
    r = ct_atk_rows.get(atk_row) or {}
    return r.get("hit0_Radius"), r.get("hit0_DmyPoly1")


# ---------------------------------------------------------------------------------------------
# the corpus


class Corpus:
    """The RL window's attacks as arrays, their HP on my build, and its parry carriers."""

    def __init__(self, build, rl=None, mirror=None, log=None):
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        g = G()
        cr = CR()
        A = AS()
        sr = SR()
        rl = rl or sr.RL_WINDOW
        self.g, self.cr, self.A = g, cr, A
        self.reg = g.Tables()
        self.ARm = g._ar_module()
        self.art = self.ARm.Tables(None)
        self.ct = cr.load_tables()
        self.t = A.AshTables()
        self.build = build
        self.stats = {k: build["stats"][k] for k in STATS}
        self.stamina = float(build["computed"]["maxStamina"])
        self.armor, self.talismans = sr.worn(build)
        self.me = cr.DEF.defender(self.ct.defense, {"vig": build["stats"]["vig"], "mnd": build["stats"]["mnd"],
                                                    "end": build["stats"]["vit"], **self.stats},
                                  armor=self.armor, talismans=self.talismans, pvp=True, strict=False)
        builds = g.blocker_corpus(mirror, *rl)
        self.n_builds = len(builds)
        self.bl = g.Blockers(self.reg, builds)
        self.art_ids = {}
        for sid in self.t.arts:
            self.art_ids.setdefault(self.t.arts_name(sid), sid)
        self.parry_sids = set(cr.PARRY_SWORD_ARTS)
        # Per attack instance: build index, family, slot, weight, attack dict, HP on me, block hit.
        self.rows = []
        self.carriers = []     # (build index, sid, carrier base id, level)
        self._attack_cache, self._ar_cache, self._skill_cache = {}, {}, {}
        for i, b in enumerate(builds):
            try:
                self._add_build(i, b)
            except (KeyError, SystemExit, ValueError, TypeError, ZeroDivisionError) as e:
                self.log(f"parry corpus: build {i} skipped: {e!r}")
        n_att = len({r["build"] for r in self.rows})
        self.log(f"parry corpus: {self.n_builds} builds, {n_att} with attacks, {len(self.rows)} attack rows, "
                 f"{len(self.carriers)} parry carriers")

    # -- per build --------------------------------------------------------------------------

    def _ar(self, wid, lvl, two, st):
        key = (wid, lvl, two, tuple(st.values()))
        if key not in self._ar_cache:
            aff = self.ARm.AFFINITIES[(wid % 10000) // 100]
            try:
                r = self.ARm.attack_rating(self.art, wid - wid % 10000, aff, lvl, st, two)
                self._ar_cache[key] = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
            except (KeyError, SystemExit, ValueError):
                self._ar_cache[key] = None
        return self._ar_cache[key]

    def _rows_of(self, wid, grip, lvl):
        key = (wid, grip, lvl)
        if key not in self._attack_cache:
            out = []
            try:
                rows = self.g.ATK.weapon_attacks(self.reg, wid, grip, lvl) or []
            except (KeyError, SystemExit, TypeError):
                rows = []
            for r in rows:
                fam = family_of(r["slot"])
                if fam is None or fam == "l1":
                    continue
                try:
                    atk = attack_of_row(self.ct, r)
                except (KeyError, SystemExit, TypeError, ValueError):
                    continue
                if not atk["hits"]:
                    continue
                out.append((r, fam, atk))
            self._attack_cache[key] = out
        return self._attack_cache[key]

    def _hp(self, row, wid, ar):
        G_ = self.g
        w = self.art.weapons.get(wid) or self.art.weapons[wid - wid % 10000]
        attack = {el: ar[el] * row[mv] / 100.0 * w.get(f"vsPlayerDmgCorrectRate_{VS_PLAYER[el]}", 1.0)
                  for el, _, _, mv in G_.ELEMENT_CUTS}
        tot = sum(attack.values())
        if tot <= 0:
            return None, None
        hp = self.cr.DEF.damage(attack, 100.0, self.me, row.get("phys_type", "standard"),
                                G_.final_rate_row(self.reg, row))["total"]
        return hp, [attack[el] / tot for el in ELEMENTS]

    def _active_skill(self, b, slots):
        """(sid, hand slot, hand weapon id) of the skill the build's L2 fires."""
        def skill(slot):
            if not slot:
                return None, None
            wid = self.bl._weapon_id(slot)
            if wid is None:
                return None, None
            art = slot.get("weaponArt")
            sid = self.art_ids.get(art) if art and art != "No Skill" else None
            if sid is None:
                sid = self.reg.weapon[wid].get("swordArtsParamId")
            return sid, wid
        rs, rw = skill(slots.get(0))
        ls, lw = skill(slots.get(3))
        two = bool(b.get("is2h"))
        sid = self.t.active_skill(rs, ls, two)
        if sid is None:
            return None, None, None
        return (sid, slots.get(0), rw) if sid == rs and (two or ls is None or sid != ls) else (sid, slots.get(3), lw)

    def _skill_attack(self, sid, slot, wid, b, st, two):
        """(attack dict, HP on me, element weights) of the build's L2 skill's first hit, or None."""
        A, t = self.A, self.t
        lvl = self.bl._level(wid, slot, b)
        key = (sid, wid, lvl, two, tuple(st.values()))
        if key in self._skill_cache:
            return self._skill_cache[key]
        out = None
        base = wid - wid % 10000
        aff = self.ARm.AFFINITIES[(wid % 10000) // 100]
        try:
            prof = A.skill_profile(t, sid, base, lvl, "both" if two else "one")
            anim = A.main_anim(prof)
            ctx = A.WeaponContext(base, aff, lvl, st, two, ar_tables=self.art)
            hits = A.skill_hits(t, base, sid, ctx, lvl, anim=anim)
            if hits:
                first = min(hits, key=lambda h: h["frame"])
                events = (A.skill_tae(t, sid) or {}).get(anim) or []
                ATK = self.cr.ATTACKS
                to_real = ATK.clip_to_real(events)

                def rf(clip):
                    return ATK.real_frame(to_real(clip / ATK.TAE_FPS))
                hp = A.pvp_damage(t, base, [dict(first)], self.me, self.cr.DEF)
                tot = sum(first["attack"].values()) or 1.0
                melee = first["kind"] == "melee"
                atk = {"jt5": tuple(jt_windows(events, JT_GET_PARRIED, to_real)) if melee else (),
                       "hits": tuple(sorted(rf(h["frame"]) for h in hits if h["kind"] == "melee")) if melee
                       else (rf(first["frame"]),),
                       "disable": bool(self.ct.atk.get(first["atk_row"], {}).get("isDisableParry", 1))}
                out = (atk, hp, [first["attack"].get(el, 0.0) / tot for el in ELEMENTS], prof["name"])
        except (KeyError, SystemExit, ValueError, TypeError, StopIteration, IndexError, AttributeError):
            out = None
        self._skill_cache[key] = out
        return out

    def _add_build(self, i, b):
        g = self.g
        slots = g._active_slots(b)
        st = {k: int((b.get("stats") or {}).get(k) or 0) for k in STATS}
        two = bool(b.get("is2h"))
        sid, hslot, hwid = self._active_skill(b, slots)
        if sid in self.parry_sids and hwid is not None:
            self.carriers.append((i, sid, hwid - hwid % 10000, self.bl._level(hwid, hslot, b)))
        slot = slots.get(0)
        wid = self.bl._weapon_id(slot) if slot else None
        if wid is None:
            return
        grip = "both" if two else "one"
        lvl = self.bl._level(wid, slot, b)
        ar = self._ar(wid, lvl, two, st)
        if ar is None:
            return
        fam_rows = collections.defaultdict(list)
        for r, fam, atk in self._rows_of(wid, grip, lvl):
            hp, wel = self._hp(r, wid, ar)
            if hp is None:
                continue
            hit = dict(r, weapon=wid)
            if hit.get("phys_type") in g.PHYS_ATTRIBUTE:
                hit["atk_attribute"] = g.PHYS_ATTRIBUTE[hit["phys_type"]]
            fam_rows[fam].append({"build": i, "family": fam, "slot": r["slot"].removeprefix("2h_"),
                                  "weapon": wid, "atk": atk, "hp": hp, "wel": wel, "hit": hit,
                                  "opening": r["slot"].removeprefix("2h_") == "r1_1"})
        if sid is not None and sid not in self.parry_sids and hwid is not None:
            sk = self._skill_attack(sid, hslot, hwid, b, st, two)
            if sk:
                atk, hp, wel, name = sk
                # The skill's block stands in as the build's R1 #1 (`INFERRED`).
                r1 = next((x for x in fam_rows.get("r1", []) if x["opening"]), None)
                fam_rows["skill"].append({"build": i, "family": "skill", "slot": name, "weapon": hwid, "atk": atk,
                                          "hp": hp, "wel": wel, "hit": r1["hit"] if r1 else None, "opening": False})
        if not fam_rows:
            return
        for fam, xs in fam_rows.items():
            for x in xs:
                x["w"] = 1.0 / len(fam_rows) / len(xs)
                self.rows.append(x)

    # -- views ----------------------------------------------------------------------------------

    def weights(self, openings=False):
        """Per row: its share of all attacks thrown (builds equal), or of the openings only."""
        if openings:
            w = np.array([1.0 if r["opening"] else 0.0 for r in self.rows])
        else:
            w = np.array([r["w"] for r in self.rows])
        return w / w.sum() if w.sum() else w

    def carried_tools(self):
        """[(tool frames, share of carriers)] and {skill: carriers}: the corpus's parry tool mix."""
        tools, cnt, by = {}, collections.Counter(), collections.Counter()
        for _, sid, base, lvl in self.carriers:
            key = (sid, base)
            if key not in tools:
                try:
                    tools[key] = tool_frames(self.t, sid, base, 0)
                except (KeyError, SystemExit, ValueError, TypeError):
                    tools[key] = None
            if tools[key]:
                cnt[key] += 1
                by[tools[key]["skill"]] += 1
        n = sum(cnt.values())
        return [(tools[k], v / n) for k, v in cnt.most_common()], dict(by.most_common())

    def riposte_on_me(self):
        """Mean over carriers of their right-hand weapon's one-handed riposte on me."""
        vals, cache = [], {}
        builds = self.bl._builds
        for i, *_ in self.carriers:
            b = builds[i]
            slot = self.g._active_slots(b).get(0)
            wid = self.bl._weapon_id(slot) if slot else None
            if wid is None:
                continue
            st = {k: int((b.get("stats") or {}).get(k) or 0) for k in STATS}
            lvl = self.bl._level(wid, slot, b)
            key = (wid, lvl, tuple(st.values()))
            if key not in cache:
                aff = self.ARm.AFFINITIES[(wid % 10000) // 100]
                try:
                    cache[key] = self.cr.crit_damage(self.ct, wid - wid % 10000, "riposte", aff, lvl, st, False,
                                                     defender=self.me)["damage"]
                except (KeyError, SystemExit, StopIteration, TypeError, ValueError):
                    cache[key] = None
            if cache[key]:
                vals.append(cache[key])
        return float(np.mean(vals)) if vals else 0.0, len(vals)


# ---------------------------------------------------------------------------------------------
# block baseline


def block_base(g, reg, rows, guard, stamina):
    """Per row, the `ShieldModel.guard` base of blocking it with `guard`: 1 - min(1, element chip
    weighted by the hit's element shares + stamina drained / my stamina). Rows with no hit: 0."""
    out = np.zeros(len(rows))
    cache = {}
    for i, r in enumerate(rows):
        h = r.get("hit")
        if h is None:
            continue
        k = (h.get("atk_row"), h.get("atk_attribute"), h.get("stamina_damage"), h.get("guard_level_base"),
             h.get("weapon"))
        if k not in cache:
            res = g.block_hit(reg, h, {}, guard, pvp=True)
            cache[k] = ([max(0.0, res["chip_fraction"][el]) for el in ELEMENTS], res["chip_hp_rate"],
                        res["stamina_to_blocker"])
        frac, rate, stam = cache[k]
        out[i] = 1.0 - min(1.0, sum(w * f for w, f in zip(r["wel"], frac)) * rate + stam / stamina)
    return np.clip(out, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------
# the model


class ParryModel:
    """My parry tools against the corpus, and the corpus's tools against my attacks."""

    def __init__(self, build, main, main_affinity, rl=None, mirror=None, log=None):
        self.C = Corpus(build, rl, mirror, log)
        self.log = self.C.log
        self.catcher = Catcher()
        C = self.C
        self.main, self.main_affinity = main, main_affinity
        mwid = C.art.find_weapon(main, main_affinity)
        level = C.art.max_level(C.art.weapons[mwid]["reinforceTypeId"])
        self.riposte_hp = C.cr.crit_damage(C.ct, main, "riposte", main_affinity, level, C.stats, False,
                                           defender=C.cr.reference_defender(C.ct))["damage"]
        self.hp = np.array([r["hp"] for r in C.rows])
        self.par = np.array([parryable(r["atk"]) for r in C.rows])
        self.w_mix, self.w_open = C.weights(False), C.weights(True)
        self.tool_mix, self.carry_by = C.carried_tools()
        self.carry = len(C.carriers) / C.n_builds if C.n_builds else 0.0
        self.rip_me, self.rip_n = C.riposte_on_me()
        # Attempt rate: the corpus's mean catch over its own parryable attacks, against its own tool
        # mix, set to land `PARRY_LAND` (module docstring).
        cm = np.array([self.catcher.mix(r["atk"], self.tool_mix) if p else 0.0
                       for r, p in zip(C.rows, self.par)])
        wp = self.w_mix * self.par
        self.mean_catch = float((cm * wp).sum() / wp.sum()) if wp.sum() else 0.0
        self.attempt = min(1.0, C.cr.PARRY_LAND / self.mean_catch) if self.mean_catch else 0.0
        self.log(f"parry: carry {self.carry:.3f} ({dict(self.carry_by)}), corpus mean catch {self.mean_catch:.3f}, "
                 f"attempt {self.attempt:.3f}, riposte on me {self.rip_me:.0f} HP over {self.rip_n} carriers, "
                 f"my riposte {self.riposte_hp:.0f} HP")

    # -- my tools ------------------------------------------------------------------------------

    def catch_rows(self, tool):
        return np.array([self.catcher(r["atk"], tool) if p else 0.0 for r, p in zip(self.C.rows, self.par)])

    def evaluate(self, tool, guard=None):
        """EV numbers of one tool (module docstring); `guard` a `shield_guard` dict of its carrier."""
        c = self.catch_rows(tool)
        H, R = self.hp, self.riposte_hp
        parry = c * R - (1.0 - c) * H
        out = {}
        g = block_base(self.C.g, self.C.reg, self.C.rows, guard, self.C.stamina) if guard else np.zeros(len(H))
        for tag, w in (("open", self.w_open), ("mix", self.w_mix)):
            wp = w * self.par
            out[f"parryable_{tag}"] = round(float(wp.sum()), 3)
            out[f"catch_{tag}"] = round(float((c * wp).sum() / wp.sum()), 3) if wp.sum() else 0.0
            out[f"ev_raw_{tag}"] = round(float((parry * wp).sum() / wp.sum()), 1) if wp.sum() else 0.0
            out[f"ev_abs_{tag}"] = round(float((np.where(self.par, parry, -(1.0 - g) * H) * w).sum()), 1)
            out[f"block_{tag}"] = round(float((-(1.0 - g) * H * w).sum()), 1)
        fam = {}
        for f in FAMILIES:
            m = np.array([r["family"] == f for r in self.C.rows]) & self.par
            wf = self.w_mix * m
            if wf.sum():
                fam[f] = round(float((c * wf).sum() / wf.sum()), 3)
        out["catch_by_family"] = fam
        return out

    def tools(self):
        """Every parry tool the build can hold in the left hand, evaluated on its best-guarding
        carrier per (skill, opening clip)."""
        C, t, A = self.C, self.C.t, self.C.A
        sr = SR()
        shapes = _shapes()
        mountable = obtainable_skills(t)
        rows = []
        seen = {}
        for wid in sorted(C.reg.weapon):
            name = C.reg.weapon_names.get(wid)
            w = C.art.weapons.get(wid)
            if wid % 10000 or not name or not w or name.startswith(sr.NPC_PREFIX):
                continue
            need = {"str": w["properStrength"], "dex": w["properAgility"], "int": w["properMagic"],
                    "fth": w["properFaith"], "arc": w.get("properLuck", 0)}
            if not all(C.stats[k] >= need[k] for k in need):
                continue
            fixed = t.reg.weapon[wid].get("swordArtsParamId")
            try:
                sids = set(A.mountable_skills(t, wid, 0, C.art.max_level(w["reinforceTypeId"]))) & mountable
            except (KeyError, SystemExit, TypeError, ValueError):
                sids = set()
            sids |= {fixed}
            for sid in sorted(sids & C.parry_sids):
                try:
                    tl = tool_frames(t, sid, wid, 0)
                except (KeyError, SystemExit, TypeError, ValueError) as e:
                    self.log(f"parry tool {name} {sid}: {e!r}")
                    continue
                if not tl:
                    continue
                lvl = C.art.max_level(w["reinforceTypeId"])
                guard = C.g.shield_guard(C.reg, wid, lvl)
                weight = w["weight"]
                key = (sid, tl["anim"])
                rows.append({"weapon": name, "wid": wid, "type": t.reg.weapon[wid]["wepType"], "weight": weight,
                             "fixed": sid == fixed, "tool": tl, "guard": guard, "level": lvl})
                seen.setdefault(key, []).append(rows[-1])
        out = []
        openings = [r for r, o in zip(C.rows, self.w_open) if o > 0]
        ns = self._fits_ns()
        gb_cache, fit_cache = {}, {}
        for key, xs in seen.items():
            # The carrier that blocks best stands for the clip (the parry frames are the clip's).
            best = None
            for x in xs:
                if x["wid"] not in gb_cache:
                    gb_cache[x["wid"]] = float(block_base(C.g, C.reg, openings, x["guard"], C.stamina).mean()) \
                        if openings else 0.0
                if x["weight"] not in fit_cache:
                    fit_cache[x["weight"]] = sr.ShieldModel.fits(ns, x["weight"])
                gb, fit = gb_cache[x["wid"]], fit_cache[x["weight"]]
                if best is None or (fit[0], gb) > (best[1][0], best[0]):
                    best = (gb, fit, x)
            gb, fit, x = best
            ev = self.evaluate(x["tool"], x["guard"])
            radius, dummy = hitbox_shape(shapes, x["tool"]["atk_row"])
            tl = x["tool"]
            out.append({"skill": tl["skill"], "anim": tl["anim"], "carrier": x["weapon"], "fixed": x["fixed"],
                        "carriers": sorted({y["weapon"] for y in xs}), "class": x["type"],
                        "hitbox": list(tl["hitbox"]), "contact": list(tl["contact"]) if tl["contact"] else None,
                        "radius": radius, "dummy_poly": dummy, "dodge": tl["dodge"], "length": tl["length"],
                        "stamina": tl["stamina"], "fp": tl["fp"], "weight": x["weight"], "fits": fit[0],
                        "load": round(fit[1], 3), "guard_open": round(gb, 3), **ev})
        return sorted(out, key=lambda r: -r["ev_abs_open"])

    def _fits_ns(self):
        """A stand-in with the attributes `ShieldModel.fits` reads, for the main-hand pick."""
        from types import SimpleNamespace
        C = self.C
        mwid = C.art.find_weapon(self.main, self.main_affinity)
        return SimpleNamespace(build=C.build, main_weight=C.art.weapons[mwid]["weight"], armor=C.armor,
                               talismans=C.talismans)

    # -- their tools on my attacks ----------------------------------------------------------------

    def cost(self, atk):
        """(HP per use, catch) of one of my attacks against the corpus's carriers (module docstring)."""
        if not parryable(atk):
            return 0.0, 0.0
        c = self.catcher.mix(atk, self.tool_mix)
        return self.carry * self.attempt * c * self.rip_me, c

    def weapon_costs(self, name):
        """{grip: {'cost', 'catch', 'exposure', 'slots'}} of one weapon: the family-weighted mean
        over its attack slots (r1, r2, move, jump equal; the skill is costed per cast by
        `ash_cost`), 1H and 2H from `weapon_attacks`, L1 from the off-hand rows."""
        C = self.C
        wid = C.reg.find_weapon(name)
        out = {}
        for grip, key in (("one", "1H"), ("both", "2H")):
            try:
                rows = C.g.ATK.weapon_attacks(C.reg, wid, grip) or []
            except (KeyError, SystemExit, TypeError):
                rows = []
            fams = collections.defaultdict(list)
            for r in rows:
                fam = family_of(r["slot"])
                if fam in (None, "l1"):
                    continue
                try:
                    atk = attack_of_row(C.ct, r)
                except (KeyError, SystemExit, TypeError, ValueError):
                    continue
                if atk["hits"]:
                    fams[fam].append((r["slot"].removeprefix("2h_"), atk))
            out[key] = self._agg(fams)
        try:
            left = C.g.offhand_rows(C.reg, wid)
        except (KeyError, SystemExit, TypeError):
            left = []
        fams = collections.defaultdict(list)
        for r in left:
            atk = offhand_attack(C.ct, r)
            if atk and atk["hits"]:
                fams["l1"].append((r["slot"], atk))
        out["L1"] = self._agg(fams)
        return out

    def _agg(self, fams):
        if not fams:
            return None
        cost = catch = expo = 0.0
        slots = {}
        for fam, xs in fams.items():
            for slot, atk in xs:
                w = 1.0 / len(fams) / len(xs)
                c_hp, c = self.cost(atk)
                cost += w * c_hp
                catch += w * c
                expo += w * parryable(atk)
                slots[slot] = {"parryable": parryable(atk), "catch": round(c, 3), "cost": round(c_hp, 2)}
        return {"cost": round(cost, 2), "catch": round(catch, 3), "exposure": round(expo, 3), "slots": slots}

    def summary(self):
        fam = {}
        for f in FAMILIES:
            m = np.array([r["family"] == f for r in self.C.rows])
            w = self.w_mix * m
            if w.sum():
                fam[f] = {"share": round(float(w.sum()), 3), "parryable": round(float((w * self.par).sum() / w.sum()), 3),
                          "hp": round(float((w * self.hp).sum() / w.sum()), 1)}
        return {"builds": self.C.n_builds, "attack_rows": len(self.C.rows), "carry": round(self.carry, 4),
                "carry_by_skill": self.carry_by, "carriers": len(self.C.carriers),
                "tool_mix": [{"skill": tl["skill"], "anim": tl["anim"], "hitbox": list(tl["hitbox"]),
                              "contact": list(tl["contact"]) if tl["contact"] else None, "share": round(w, 4)}
                             for tl, w in self.tool_mix],
                "mean_catch": round(self.mean_catch, 4), "attempt": round(self.attempt, 4),
                "parry_land": self.C.cr.PARRY_LAND, "riposte_on_me": round(self.rip_me, 1),
                "riposte_carriers": self.rip_n, "my_riposte": round(self.riposte_hp, 1),
                "main": self.main, "main_affinity": self.main_affinity,
                "opening_parryable": round(float((self.w_open * self.par).sum()), 3),
                "mix_parryable": round(float((self.w_mix * self.par).sum()), 3), "families": fam,
                "react_share": self.catcher.rs, "timing_sd_frames": self.catcher.sd, "timing_cv": self.catcher.cv}


# ---------------------------------------------------------------------------------------------
# consumers (class roles, the plan)


def corpus_tools(summary):
    """[(tool, share)] from a `--json` summary's `tool_mix`."""
    return [({"hitbox": tuple(x["hitbox"]), "contact": tuple(x["contact"]) if x["contact"] else None}, x["share"])
            for x in summary["tool_mix"]]


def ash_attack(t, sid, wid, level=0, grip="one"):
    """The attack dict of one skill cast's first hit (bullets: never parryable), or None."""
    A = AS()
    ATK = CR().ATTACKS
    ct_atk = _ct().atk
    prof = A.skill_profile(t, sid, wid, level, grip)
    anim = A.main_anim(prof)
    acts = [x for x in prof["anims"].get(anim, []) if x["kind"] in ("melee", "bullet")]
    if not acts:
        return None
    events = (A.skill_tae(t, sid) or {}).get(anim) or []
    to_real = ATK.clip_to_real(events)

    def rf(clip):
        return ATK.real_frame(to_real(clip / ATK.TAE_FPS))
    first = min(acts, key=lambda x: x["frames"][0])
    if first["kind"] != "melee":
        return {"jt5": (), "hits": (rf(first["frames"][0]),), "disable": True}
    hits = tuple(sorted(rf(x["frames"][0]) for x in acts if x["kind"] == "melee"))
    return {"jt5": tuple(jt_windows(events, JT_GET_PARRIED, to_real)), "hits": hits,
            "disable": bool(ct_atk.get(first.get("atk_row"), {}).get("isDisableParry", 1))}


_CT = []


def _ct():
    if not _CT:
        _CT.append(CR().load_tables())
    return _CT[0]


def ash_cost(summary, atk, catcher=None):
    """(HP per cast, catch) the corpus's parriers take off one skill cast (module docstring)."""
    if not parryable(atk):
        return 0.0, 0.0
    catcher = catcher or Catcher()
    c = catcher.mix(atk, corpus_tools(summary))
    return summary["carry"] * summary["attempt"] * c * summary["riposte_on_me"], c


# ---------------------------------------------------------------------------------------------
# CLI


def _shapes():
    """{atk row: AtkParam_Pc row with its hitbox fields}, for `hitbox_shape`."""
    cr = CR()
    files = cr.PR.load(None)
    rows = cr.PR.rows(cr.PR.param_bytes(files, "AtkParam_Pc"), ["hit0_Radius", "hit0_DmyPoly1", "isDisableParry"])[0]
    return {r["id"]: r for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", type=Path)
    ap.add_argument("--main", help="my main-hand pick (the riposte)")
    ap.add_argument("--main-affinity", default="Heavy")
    ap.add_argument("--weapons-from", type=Path,
                    help="er-mechanics-weapon-embed.py output: cost every wieldable weapon in it, per grip")
    ap.add_argument("--mirror", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.build or not a.main:
        ap.error("--build and --main required")
    b = json.loads(a.build.read_text())
    m = ParryModel(b, a.main, a.main_affinity, mirror=a.mirror)
    summ = m.summary()
    print(f"corpus: {summ['builds']} builds, {summ['attack_rows']} attacks; parryable: openings "
          f"{summ['opening_parryable']:.2f}, mix {summ['mix_parryable']:.2f}; carry {summ['carry']:.3f} "
          f"{summ['carry_by_skill']}; attempt {summ['attempt']:.3f}; their riposte on me {summ['riposte_on_me']:.0f}")
    for f, v in summ["families"].items():
        print(f"  {f}: share {v['share']}, parryable {v['parryable']}, mean HP on me {v['hp']}")
    tools = m.tools()
    print(f"my tools (riposte {m.riposte_hp:.0f} HP with {a.main_affinity} {a.main}):")
    for x in tools:
        print(f"  {x['skill']} ({x['anim']}) on {x['carrier']}{'' if x['fits'] else ' (heavy)'}: hitbox f{x['hitbox']} "
              f"119 f{x['contact']} r{x['radius']} dodge f{x['dodge']} stam {x['stamina']} fp {x['fp']}; catch "
              f"open {x['catch_open']} mix {x['catch_mix']} {x['catch_by_family']}; ev_raw {x['ev_raw_open']}/"
              f"{x['ev_raw_mix']}, ev_abs {x['ev_abs_open']}/{x['ev_abs_mix']} (block {x['block_open']}/"
              f"{x['block_mix']}, guard {x['guard_open']})")
    out = {"summary": summ, "tools": tools}
    if a.weapons_from:
        emb = json.loads(a.weapons_from.read_text())
        names = sorted({r["weapon"] for r in emb["weapons"] if r["wieldable"]})
        out["weapons"] = {}
        for k, n in enumerate(names):
            try:
                out["weapons"][n] = m.weapon_costs(n)
            except (KeyError, SystemExit, TypeError, ValueError) as e:
                m.log(f"parry weapons: {n}: {e!r}")
            if k % 50 == 0:
                m.log(f"parry weapons: {k} of {len(names)}")
        top = sorted(((v["1H"]["cost"] if v.get("1H") else 0.0, n) for n, v in out["weapons"].items()), reverse=True)
        print("highest 1H parry cost per attack:", ", ".join(f"{n} {c:.1f}" for c, n in top[:8]))
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


def selftest() -> int:
    fails = []

    def check(label, got, want):
        ok = got == want
        print(f"{'ok' if ok else 'FAIL'}  {label}: {got!r} (want {want!r})")
        if not ok:
            fails.append(label)

    tool = {"hitbox": (4, 6), "contact": (4, 6)}
    # JumpTable 5 f6-14, first hit f14, hit row parryable by contact.
    atk = {"jt5": ((6, 14),), "hits": (14, 30), "disable": False}
    check("intervals: hitbox route [6-6, 14-4] and contact [14-6, 14-4] merge", catch_intervals(atk, tool), [(0, 10)])
    check("isDisableParry 1 keeps the hitbox route only",
          catch_intervals(dict(atk, disable=True), tool), [(0, 10)])
    late = {"jt5": ((20, 28),), "hits": (14, 26), "disable": False}
    check("a window opening after the first hit guards a later swing", catch_intervals(late, tool), [])
    gp = {"hitbox": (4, 7), "contact": (4, 10)}
    check("Golden Parry's longer 119 window widens the contact span",
          catch_intervals({"jt5": ((10, 14),), "hits": (14,), "disable": False}, gp), [(3, 10)])
    check("same row with isDisableParry 1", catch_intervals({"jt5": ((10, 14),), "hits": (14,), "disable": True}, gp),
          [(3, 10)])
    check("the contact route alone when the hitbox span is empty",
          catch_intervals({"jt5": ((13, 13),), "hits": (13,), "disable": False}, {"hitbox": (4, 4), "contact": (2, 9)}),
          [(4, 11)])
    check("read: [0, 10] of a 14-frame windup", round(p_read([(0, 10)], 14), 4), round(10 / 14, 4))
    # Reaction: one delay at 5, no spread: aim inside [0, 10] and catch always.
    check("react: a reaction before the window closes catches", round(p_react([(0, 10)], [(5.0, 1.0)], 0.01, 0.0), 3),
          1.0)
    check("react: a reaction after it never does", p_react([(0, 10)], [(11.0, 1.0)], 0.01, 0.0), 0.0)
    # Timing spread grows with the aimed interval: the same 4-frame window late in a long windup
    # is caught less than early in a short one.
    early = p_react([(10, 14)], [(5.0, 1.0)], 1.5)
    later = p_react([(60, 64)], [(5.0, 1.0)], 1.5)
    check("react: a long telegraph is harder (scalar timing)", early > later, True)
    check("react: no cv, no difference", round(p_react([(10, 14)], [(5.0, 1.0)], 1.5, 0.0), 6),
          round(p_react([(60, 64)], [(5.0, 1.0)], 1.5, 0.0), 6))
    cat = Catcher(react_share=0.5, delays=[(5.0, 1.0)], sd_frames=0.01, cv=0.0)
    check("catch: half reaction, half read", round(cat(atk, tool), 4), round(0.5 * 1.0 + 0.5 * 10 / 14, 4))
    check("no JumpTable 5: never parryable", (parryable({"jt5": (), "hits": (10,), "disable": False}),
                                              cat({"jt5": (), "hits": (10,), "disable": False}, tool)), (False, 0.0))
    check("family of slots", [family_of(s) for s in ("2h_r1_2", "r2_1c", "roll_r1", "jump_r2", "counter", "left_3")],
          ["r1", "r2", "move", "jump", None, "l1"])

    # Regulation and TimeAct: the Brass Shield's Parry and Golden Parry clips.
    t = AS().AshTables()
    brass = t.find_weapon("Brass Shield")
    p = tool_frames(t, 302, brass)
    check("Parry on a medium shield: a692_040000, hitbox f4-6, JumpTable 119 f4-6",
          (p["anim"], p["hitbox"], p["contact"]), ("a692_040000", (4, 6), (4, 6)))
    gp_ = tool_frames(t, 307, brass)
    check("Golden Parry: hitbox f4-7 (judge 3690), JumpTable 119 f4-10",
          (gp_["hitbox"], gp_["contact"], gp_["judge"]), ((4, 7), (4, 10), 3690))
    buck = tool_frames(t, 303, t.find_weapon("Buckler"))
    check("Buckler Parry: hitbox f4-9", buck["hitbox"], (4, 9))
    ob = obtainable_skills(t)
    check("Buckler Parry is no obtainable ash; Parry and Carian Retaliation are",
          (303 in ob, 302 in ob, 305 in ob), (False, True, True))
    check("Golden Retaliation has no parry hitbox", tool_frames(t, 1196, t.find_weapon("Erdtree Greatshield")), None)
    ct = _ct()
    ex = t.find_weapon("Executioner's Greataxe")
    r1 = next(r for r in CR().ATTACKS.weapon_attacks(ct.reg, ex, "one", 25) if r["slot"] == "r1_1")
    a1 = attack_of_row(ct, r1)
    check("Executioner's Greataxe 1H R1 #1: parryable, isDisableParry 1", (parryable(a1), a1["disable"]), (True, True))
    flail = next(r for r in CR().ATTACKS.weapon_attacks(ct.reg, t.find_weapon("Flail"), "one") if r["slot"] == "r1_1")
    check("Flail R1 #1 has no JumpTable 5", parryable(attack_of_row(ct, flail)), False)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
