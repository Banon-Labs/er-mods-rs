#!/usr/bin/env python3
"""Every ash of war one weapon can carry, ranked for PvP and written as an HTML page.

    python3 scripts/er-mechanics-ash-table.py "Guardian's Swordspear" --out <file.html>
    python3 scripts/er-mechanics-ash-table.py Wakizashi --affinity Keen --grip 1H \
        --stats str=18,dex=80,int=7,fth=8,arc=9 \
        --armor "Verdigris Gauntlets,Verdigris Armor,Verdigris Greaves" --bull-goat --out <file.html>

Each mountable ash is put in one category:

- `melee bullet` (`MELEE_BULLET`): its projectile needs the blade to connect, so the whole cast is
  scored;
- `bullet`: only its projectiles that travel more than `MIN_TRAVEL_M` from where they spawn are
  scored, never the blade hit of the cast;
- `non-bullet`: no projectile, the whole cast is scored.

Damage is PvP damage against the RL150 reference defender (`er-mechanics-ash-stats.py`). Ashes are
ranked by damage per second from the start of the cast to its last scored frame (TAE frames, 30
fps), so windup counts against them. Hyperarmor is the first window of the FP animation:
effective PvP poise = `1000 * (armor poise * proCorrectionRate + correctionRate * weapon
toughnessCorrectRate)`, divided by 0.75 with Bull-Goat's Talisman and by the ToughnessParam row's
PvP poise-damage rate (attacks.md, hyperarmor). Each hit group's damage is split by where its
attack comes from: the attack at +0 before stat scaling, what the weapon's upgrade adds, and each
stat's share of the scaling.

Extra columns in `--json` rows, for `er-mechanics-combo-loadout.py`:

- `utility`: an ash with no damaging hit (steps, buffs) is scored by the fastest follow-up pressed at
  its R1 gate (`FOLLOW_UP_SLOTS`, the gate from `er-mechanics-disengage.clip`): `first` is the
  frame that R1 lands, `last` the frame a roll is possible, `dps` its damage over `last`. A buff
  adds the damage its buff adds on the carrier (`BUFF_MODEL`, `Table.buff_gain`): `dmg` is the
  follow-up (`follow_dmg`) plus that, and `buff` holds the reading;
- `string`: the cast followed by R1 #1..#k at the R1 gate (no earlier than the cast's last hit),
  the k with the best damage per second, timed to the last R1's roll cancel; `score` is that for a
  quick weapon (`QUICK_R1_CANCEL`) and `dps` otherwise;
- `area`: ground covered in m^2, damaging bullets swept along their path at their largest radius
  plus the blade's part: for a melee bullet its angle-weighted swept volume over the defender's
  height (`Table.area`), otherwise world reach x effective arc;
- `status_hp`: per status, the HP the cast plus the string's R1s are worth against a 60 VIG RL150
  target (`TARGET_HP`), damage-over-time procs cut to one 30 s battle
  (`er-mechanics-status.BATTLE_SECONDS`).

`--variants` also sweeps Blood, Cold and Poison for weapons with an innate status and Cold for
weapons that mount Waves of Darkness; `--status-weapons` sweeps the status ones for the weapons it
names whatever their innate status; `--extra-weapons` adds weapons outside the build.

Ashes left out: those in `data/ash-blacklist.toml`, unique ones (an EquipParamGem row mountable on
fewer than two weapon classes) and unobtainable ones (every row for the skill has sortId 999999).
"""
from __future__ import annotations

import argparse
import collections
import html
import importlib.util
import json
import math
import sys
import tomllib
import unicodedata
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("er_mechanics_ash_stats", HERE / "er-mechanics-ash-stats.py")
S = sys.modules.get("er_mechanics_ash_stats") or importlib.util.module_from_spec(spec)
if "er_mechanics_ash_stats" not in sys.modules:
    sys.modules["er_mechanics_ash_stats"] = S
    spec.loader.exec_module(S)
A, OPT, AR = S.A, S.OPT, S.AR

BLACKLIST = HERE.parent / "data" / "ash-blacklist.toml"
PREFERENCES = HERE.parent / "data" / "ash-preferences.toml"
API = "https://er-inventory-api.nyasu.business/inventories"
UA = {"User-Agent": "er-mods-rs build-import (+github.com/Banon-Labs)"}
MELEE_BULLET = {"Lightning Slash", "Phantom Slash", "Stormcaller"}
MIN_TRAVEL_M = 1.2
UNOBTAINABLE_SORT_ID = 999999
BULL_GOAT_CUT = 0.75
#: PvP poise damage of a longsword 1H R1 #1 in menu units (attacks.md, defender pools).
LONGSWORD_R1_POISE = 110.0
FPS = 30.0
SOURCES = ("fixed", "weapon level", "str", "dex", "int", "fth", "arc")
LABEL = {"fixed": "fixed (+0)", "weapon level": "weapon level", "str": "STR", "dex": "DEX",
         "int": "INT", "fth": "FTH", "arc": "ARC"}
CATEGORIES = {"bullet": f"Bullet (bullet damage only, travels &gt;{MIN_TRAVEL_M} m)",
              "non-bullet": "Non-bullet (whole cast)",
              "melee bullet": "Melee bullet (whole cast, blade must connect)",
              "utility": "Steps and buffs (no damage; scored by the fastest R1 out of them: damage = that R1 "
                         "plus, for a buff, what the buff adds to the hits it covers; first = the R1 lands, "
                         "last = a roll is possible)"}
REACH_HEADER = {"bullet": "bullet reach min / max (m)",
                "non-bullet": "weapon reach from start position (m)",
                "melee bullet": "reach (m): blade from start position; bullet min / max",
                "utility": "weapon reach from start position (m)"}
#: A weapon whose 1H R1 #1 can be cancelled into R1 #2 by this frame is quick: its damaging ashes
#: rank by the ash + R1 string (`string`), every other weapon's by the cast alone.
QUICK_R1_CANCEL = 18
#: R1 slots of a string, in order (`er-mechanics-attacks.SLOTS_ONE_HAND`).
R1_SLOTS = ("r1_1", "r1_2", "r1_3", "r1_4", "r1_5", "r1_6")
#: Follow-ups tried after a no-damage ash, all pressed at the ash's R1 gate. A jump attack has no
#: gate in the ash clips and is not tried.
#:
#: A step plays the rolling R1 (`STEP_FOLLOW_UP_SLOTS`), traced in the 1.17.1 `c0000.hks`:
#: `GetSwordArtsRequestNew` turns a skill in `IsRollingArts` (`ROLLING_ARTS`) into
#: `SWORDARTS_REQUEST_RIGHT_STEP`; `ExecAttack` sends that to `W_SwordArtsRolling` (or `_SelfTrans`
#: / `_SelfTrans2` when chained), and `SwordArtsRolling_onUpdate` hands `EvasionCommonFunction` the
#: R1 event `W_AttackRightLightStep`, which `c0000.behbnd` maps to `AttackRightLightStep`, clip
#: 030300 (032300 two-handed). The exception is `SwordArtsRollingDirection ~= 0`: a locked-on step
#: back or sideways plays `W_AttackRightBackstep` (030400) instead. Not locked on, the direction is
#: always 0, which is also the 040080 opening these tables score. The crouch R1
#: (`AttackRightLightStealth`, 030310) is not reachable from a step.
#:
#: A buff is still the faster of the plain and the rolling R1 (`INFERRED`): `SwordArtsOneShot_onUpdate`
#: defaults to `W_AttackRightLight1` but switches to the rolling R1 or R1 #2 for listed skills, and
#: which buffs run in that state is not traced.
FOLLOW_UP_SLOTS = ("r1_1", "roll_r1")
STEP_FOLLOW_UP_SLOTS = ("roll_r1",)
#: A buff ash is also worth the damage its buff adds on its own carrier (user, 2026-10-10:
#: "Determination is worse than RKR, but that's scales off AR which is poor on claws?";
#: `Table.buff_gain`). The rows are the skill's own SpEffects (`er-mechanics-ashes.skill_buffs`,
#: walked through `er-mechanics-buffs.Buffs.skill_rows`), each read through
#: `Buffs.attack_context` with the follow-up's hand, grip, physical type and AtkParam
#: `spEffectAtkPowerCorrectRate_by*`, so a rate, a flat add, a scaling-rate add and a post-defense
#: rate all go through the carrier's own AR and the RL150 defender. Measured in the 1.17.1
#: regulation (`MEASURED`): Determination 1691 and Royal Knight's Resolve 1701 are
#: `atkPlayerDmgCorrectRate` 1.3 and 1.4 (after defense) for 10 s, `stateInfo` 384; War Cry 1811
#: and Barbaric Roar 1681 `physicsAttackPowerRate` 1.075 plus `changeStrengthPoint` 5 for 40 s;
#: Cragblade 1821 `physicsAttackPowerRate` 1.15 for 60 s; Braggart's Roar 1861 1.1 for 60 s;
#: Seppuku 1755 `physicsAttackPower` 30 plus `atkOccurrenceSpEffectId` 1756 (`bloodAttackPower`
#: 30 on whoever is hit) for 60 s; Golden Vow 1730 `atkPlayerDmgCorrectRate` 1.025 for 45 s.
#:
#: Which hits a buff covers:
#:
#: - a next-hit row (`Buffs.one_hit`: `stateInfo` 384/385) covers the follow-up only. Nothing on
#:   the buff row ends it (`deleteCriteriaDamage` 0); the AtkParam rows carry SpEffect
#:   1665/1667, which the hit sends back to the attacker and which, gated on 384/385, overwrites
#:   the weapon-buff slot (`VERIFIED`, buffs.md section 8). So the first registered hit spends it,
#:   and the follow-up the row is timed on is that hit. A heavier next attack (a charged R2) would
#:   need its own gate out of the buff, which is not traced, so it is not tried;
#: - a timed row covers the hits in the row's own clock plus, for the rest of one engagement
#:   window (`BUFF_WINDOW_S`, or the buff's duration if shorter), the carrier's R1 chain at its
#:   best damage per second (R1 #1..#k, `Table.string`), each R1 buffed. Later engagements the
#:   buff still covers are not counted: the cast is charged once, against one engagement, the
#:   way every damaging ash's score is one cast's;
#: - a row with a duration under `Buffs`' `REFRESHED_ROW_S` that is not next-hit gets nothing, as
#:   `Buffs.uptime` gives it none, and a row with `throwAttackParamChange` applies to critical hits
#:   only: Determination's 1694 and Royal Knight's Resolve's 1704, `*AttackRate` 0.75, duration 0.
#:
#: Each copy is valued alone. Every weapon-buff ash here is `spCategory` 162 (163 on the left
#: hand), one live entry per hand, so two of them never stack and a grease displaces them;
#: Golden Vow is 160 and stacks with them (`er-mechanics-buffs.stack`). Not valued: the
#: damage-taken cuts (Golden Vow 0.985, Braggart's Roar 0.9), `staminaAttackRate` (guard
#: stamina damage) and poise rates.
#:
#: buff-v3 (user, 2026-10-19: "War Cry changes the R2"): while a `HEAVY_SPECIAL_SPEFFECTS` row is
#: on, the R2 is the skill's own heavy (`Table.special_r2`), so a timed buff's window is the
#: better of the buffed R1 chain and that R2 string, each on its own frames and hits
#: (`Table.special_string`), against the plain R1 chain. Interrupt enters through
#: `Table.chain_net` when the caller supplies one (`er-mechanics-combo-loadout.set_buff_values`
#: does, from `er-mechanics-ash-interrupt.simulate`); the sweep alone compares raw HP a second.
BUFF_MODEL = "buff-v3"
#: SpEffects whose presence swaps the R2 events for the skill's heavy special, with the
#: `AttackRightHeavySpecialType` each writes (`VERIFIED`, c0000.hks `ExecAttack` heavy branch,
#: docs/er-mechanics/chainsaw/fallback-test-plan.md section 6): Barbaric Roar 1681/1686, War Cry
#: 1811/1816, 1716, 1721 and 102101. War Cry's TAE event 331 adds 1810, which links to 1811.
HEAVY_SPECIAL_SPEFFECTS = {1681: 0, 1686: 0, 1716: 0, 1721: 0, 102101: 0, 1811: 1, 1816: 1}
#: One-handed (Start #1, End #1, Start #2, End #2) clips per special type; two-handed + 2000.
#: Start #1 is `VERIFIED` (`AttackRightHeavySpecial1Start_CMSG` 030600, `...Warrior1Start_CMSG`
#: 030620). The End and #2 ids are `INFERRED` from the numbering of the plain heavy (030500 Start,
#: 030505 End, 030510/030515 #2) and from the clips' TAE: each Start carries the release SpEffect
#: 100280 (War Cry's at f11) and its slow multi-hit swing, each End one hit and a
#: roll cancel (Stone Club a033_030625: judge 3981, MV 135, hit f15, hyperarmor f0-25).
HEAVY_SPECIAL_ANIMS = {0: (30600, 30605, 30610, 30615), 1: (30620, 30625, 30630, 30635)}
#: The plain R2's clips in the same order (`er-mechanics-attacks.SLOTS_ONE_HAND`: Heavy1Start
#: 030500, Heavy1End 030505, Heavy2Start 030510, Heavy2End 030515).
PLAIN_HEAVY_ANIMS = (30500, 30505, 30510, 30515)
#: The engagement window a timed buff is valued over, in s: `er-mechanics-buffs.ENGAGEMENT_SECONDS`
#: (5 s, `INFERRED` there, the status module's engagement spacing), restated so a change there is
#: seen here.
BUFF_WINDOW_S = 5.0
#: `swordArtsTypeNew` values `IsRollingArts` accepts (Quickstep 155, Bloodhound's Step 156,
#: Raging Beast 273, Blind Spot 276, Dynastic Sickleplay 313).
ROLLING_ARTS = (155, 156, 273, 276, 313)
#: Skills that deal no damage, are not a step and add no buff the buff model measures, and still
#: do something to a player. Every other skill like that does nothing in PvP and gets no row
#: (`pvp_utility`): user, 2026-10-18, after White Shadow's Lure, which only draws enemy attention,
#: was picked into a duel build as a fast "utility" ash on the strength of the R1 after it.
NO_HIT_KEEP = {
    "Parry": "deflects a hit; valued by the parry model (er-mechanics-parry.py), not as a buff",
    "Lifesteal Fist": "a grab whose hit and heal are not timed as hits here, so it reads as no-hit",
}
#: Skills whose effect is camouflage, with the SpEffect rows that carry it. The link is recorded,
#: not read: Assassin's Gambit's TAE applies only 1765 (a 0.1 s, 35 HP cost), and 1766/1767 come
#: from outside the events `skill_profile` reads. The distance it hides you at is read from the
#: rows' `SpEffectVfxParam` (`camouflage_m`), so the loadout can drop it from a fight whose arena
#: never gets that far apart (`er-mechanics-combo-loadout.py --arena-m`).
CAMOUFLAGE_SKILLS = {"Assassin's Gambit": (1767,)}


def pvp_utility(name, art_type, buff) -> bool:
    """Whether a skill with no hits of its own is worth a row: a step (`ROLLING_ARTS`), a buff
    with a measured gain, a `NO_HIT_KEEP` skill, or a `CAMOUFLAGE_SKILLS` one."""
    return art_type in ROLLING_ARTS or bool(buff) or name in NO_HIT_KEEP or name in CAMOUFLAGE_SKILLS


_VFX = {}


def camouflage_m(sids, vfx=None, sp=None):
    """The distance in m past which SpEffects `sids` hide their wearer fully: each row's
    `SpEffectVfxParam` `useCamouflage` with the far end of its fade band (`camouflageBeginDist` /
    `camouflageEndDist`, Assassin's Gambit 17 / 12), 0.0 for a band of -1 (hidden at any
    distance), the nearest over the rows; None when no row camouflages."""
    if vfx is None or sp is None:
        if not _VFX:
            B = _buffs_mod()
            files = B.PR.load(None)

            def rows(stem, fields):
                return {r["id"]: r for r in B.PR.rows(B.PR.param_bytes(files, stem), fields)[0]}
            _VFX["sp"] = rows("SpEffectParam", {"vfxId"})
            _VFX["vfx"] = rows("SpEffectVfxParam", {"useCamouflage", "camouflageBeginDist", "camouflageEndDist"})
        vfx, sp = _VFX["vfx"], _VFX["sp"]
    out = []
    for s in sids:
        v = vfx.get((sp.get(s) or {}).get("vfxId"))
        if v and v["useCamouflage"]:
            far = max(v["camouflageBeginDist"], v["camouflageEndDist"])
            out.append(0.0 if far < 0 else float(far))
    return min(out) if out else None


#: The proc target: 60 VIG at RL150 (user, 2026-10-08), resistances the RL150 PvP corpus median.
TARGET_HP = 1900
#: Fight model status is valued in (`er-mechanics-buffs.FIGHT_KINDS`, `--fight`): its `pressure`,
#: the opponents on the target at once, limits the target's room to back off and bolus
#: (`er-mechanics-status.sequence_hp`). Set by `main` before the sweep forks its workers; None is
#: `er-mechanics-buffs.DEFAULT_FIGHT`.
FIGHT = None
#: Grid cell for the hit-area union, metres.
AREA_CELL_M = 0.25


_MODS = {}


def _sibling(name, fname):
    if name not in _MODS:
        _MODS[name] = sys.modules.get(name)
        if _MODS[name] is None:
            s = importlib.util.spec_from_file_location(name, HERE / fname)
            _MODS[name] = importlib.util.module_from_spec(s)
            sys.modules[name] = _MODS[name]
            s.loader.exec_module(_MODS[name])
    return _MODS[name]


def _disengage():
    return _sibling("er_mechanics_disengage", "er-mechanics-disengage.py")


def _status_mod():
    return _sibling("er_mechanics_status", "er-mechanics-status.py")


def _buffs_mod():
    return _sibling("er_mechanics_buffs", "er-mechanics-buffs.py")


def string_scored(x):
    """True when the row's `score` is its string's damage per second (a quick weapon's damaging
    ash), so its clock ends at the string's last roll cancel instead of at `last`."""
    st = x.get("string")
    return bool(x["cat"] != "utility" and x.get("quick") and st and x["score"] == st["dps"])


def ha_windows(tae, shift=0.0):
    """The hyperarmor windows of one `er-mechanics-attacks.tae_details` record moved by `shift`, as
    `er-mechanics-ash-interrupt.simulate` takes them: bonus in menu units
    (`er-mechanics-exchange.POISE_MENU` 10), no floor (`INFERRED`)."""
    return [(shift + h["frames"][0], shift + h["frames"][1], h["poise_bonus"] * 10.0,
             h.get("pvp_poise_damage_taken") or 1.0, 0.0, h.get("pvp_hp_damage_taken") or 1.0)
            for h in tae.get("hyperarmor") or []]


def buff_hits(x, slots):
    """(`first_hits`, `rep`, `end_s`, `chain`) of a cast-relative sweep row for `Table.buff_gain`,
    from its grip's `r1_slots`: a utility row's follow-up (`follow_up`) on its own clock; a
    damaging row's string R1s when it is scored on the string, R1 #1 as its next attack. None
    when the slots have no such hit."""
    by = {s["slot"]: s for s in slots}
    chain = [s for s in slots if s["slot"] in R1_SLOTS]
    if x["cat"] == "utility":
        f = by.get(x.get("follow_up"))
        return ([f], f, x["last"] / FPS, chain) if f else None
    rep = by.get("r1_1") or next((s for s in slots if s["slot"] in FOLLOW_UP_SLOTS), None)
    if rep is None:
        return None
    if string_scored(x):
        return chain[:x["string"]["r1s"]], rep, x["string"]["end"] / FPS, chain
    return [], rep, x["last"] / FPS, chain


def apply_buff(x, gain):
    """Fold `Table.buff_gain` into a cast-relative row: the HP the buff adds over the row's own
    clock (cast start to the string's last roll cancel when the row is scored on its string, else
    to `last`) is added to that clock's damage per second, `score` with it, and the build-up it
    adds joins `status_hp`. `gain` gets `rate` (HP a second added) and `share` (its part of the
    new `score`, which `er-mechanics-combo-loadout.status_rate` takes back out so the status
    clock stays the plain one). `dmg` keeps the cast's (a utility row's: the follow-up's) own
    damage. A row already carrying `buff` is left alone."""
    if "buff" in x:
        return x
    x["buff"] = gain
    if not gain:
        return x
    rate = 0.0
    if gain["added"]:
        st = x.get("string")
        on_string = string_scored(x)
        clock = (st["end"] if on_string else x["last"]) / FPS
        rate = gain["added"] / clock if clock else 0.0
        if on_string:
            x["string"] = dict(st, dps=round(st["dps"] + rate))
            x["score"] = x["string"]["dps"]
        else:
            x["dps"] = round(x["dps"] + rate)
            x["score"] = round(x["score"] + rate)
    gain["rate"] = round(rate, 1)
    gain["share"] = round(rate / x["score"], 4) if x["score"] else 0.0
    if gain["status_hp"]:
        st = dict(x.get("status_hp") or {})
        for k, v in gain["status_hp"].items():
            st[k] = st.get(k, 0) + v
        x["status_hp"] = st
    return x


def unapply_buff(x):
    """Take `apply_buff` back out of a row, so a sweep valued under an older `BUFF_MODEL` can be
    valued again: its `rate` off the clock it went into and its build-up off `status_hp`. The
    figures `apply_buff` rounded can come back 1 off."""
    gain = x.pop("buff", None)
    if not gain:
        return x
    rate = gain.get("rate") or 0.0
    if rate:
        st = x.get("string")
        if string_scored(x):
            x["string"] = dict(st, dps=round(st["dps"] - rate))
            x["score"] = x["string"]["dps"]
        else:
            x["dps"] = round(x["dps"] - rate)
            x["score"] = round(x["score"] - rate)
    if gain.get("status_hp"):
        st = dict(x.get("status_hp") or {})
        for k, v in gain["status_hp"].items():
            st[k] = st.get(k, 0) - v
        x["status_hp"] = {k: v for k, v in st.items() if v}
    return x


_DEFENDER = []


def window_defender() -> dict:
    """The RL 150 window's median defender (`er-builds-optimize.bracket_defender` over the corpus's
    builds of RL 140-160), read once per process. It has no weapon in it; building it through
    `er-mechanics-infusions.Builder(weapon).setup(150)` on every weapon switch re-decoded the
    regulation and re-read the corpus, 2 s a switch and 315 switches in one build's sweep
    (measured 2026-10-14)."""
    if not _DEFENDER:
        _DEFENDER.append(OPT.bracket_defender(OPT.corpus_rows(OPT.CACHE / "builds.jsonl", 140, 160)))
    return _DEFENDER[0]


class Table:
    def __init__(self, weapon, affinity, stats, armor_names, bull_goat):
        self.lev = S.LEV.Levers()
        self.art = AR.Tables(None)
        self.t = self.lev.t
        self.affinity, self.stats = affinity, stats
        self.set_weapon(weapon)
        self.bull_goat = bull_goat
        self.armor = self._armor(armor_names)
        self.blacklist = tomllib.loads(BLACKLIST.read_text())
        #: Casts left out by `untimed_hits`, counted per ash and reported once (`untimed_summary`)
        #: instead of a line per weapon and grip.
        self.untimed = collections.Counter()
        self.classes, self.obtainable = collections.defaultdict(int), set()
        flags = [f for f in next(iter(self.t.gem.values())) if f.startswith("canMountWep_")]
        for g in self.t.gem.values():
            sid = g.get("swordArtsParamId", -1)
            if sid and sid > 0:
                self.classes[sid] = max(self.classes[sid], sum(1 for f in flags if g[f]))
                if g["sortId"] != UNOBTAINABLE_SORT_ID:
                    self.obtainable.add(sid)
        self._travel = {}
        self._status = None
        self._buff = None
        #: `chain_net(hits, windows, gate)` -> HP the chain nets against the opponent's replies,
        #: `hits` [(frame, poise dealt, HP)] and `windows` as `er-mechanics-ash-interrupt.simulate`
        #: takes them; None compares raw HP (`special_string`).
        self.chain_net = None

    def set_weapon(self, weapon, affinity=None):
        self.weapon = weapon
        if affinity:
            self.affinity = affinity
        self.wid = self.t.find_weapon(weapon)
        self.defender = window_defender()
        self.level = self.art.max_level(self.t.reg.weapon[self.wid]["reinforceTypeId"])

    def fixed_skill(self, weapon=None):
        """True when the weapon cannot mount an ash of war (`EquipParamWeapon.gemMountType` 0), so
        its own skill is the only one it has: Bloodhound's Fang, Hand of Malenia, Reduvia, ..."""
        wid = self.t.find_weapon(weapon) if weapon else self.wid
        return self.t.reg.weapon[wid].get("gemMountType") == 0

    def has_affinity(self, weapon, affinity):
        try:
            self.art.find_weapon(weapon, affinity)
            return True
        except SystemExit:
            return False

    # ---- the ash + R1 string, follow-ups after a step or buff, hit area, status

    def r1_slots(self, two, ctx):
        """[(slot, first hit, R1 cancel, roll cancel, PvP damage, attack row)] of the R1 string."""
        atk_mod = sys.modules[type(self.t.reg).__module__]
        grip = "both" if two else "one"
        rows = {r["slot"].removeprefix("2h_"): r for r in atk_mod.weapon_attacks(self.t.reg, self.wid, grip, self.level)}
        out = []
        for key in R1_SLOTS + FOLLOW_UP_SLOTS:
            r = rows.get(key)
            if not r or not r.get("hit_windows") or not r.get("cancel_frame"):
                continue
            mv = {"physical": r["mv_phys"], "magic": r["mv_mag"], "fire": r["mv_fire"],
                  "lightning": r["mv_light"], "holy": r["mv_holy"]}
            hit = {"attack": {el: ctx.ar_by[el] * mv[el] / 100.0 for el in A.ELEMENTS},
                   "final_rate_id": self.t.atk_extra.get(r["atk_row"], {}).get("finalDamageRateId", -1),
                   "atk_row": r["atk_row"], "count": max(1, r.get("own_sweep_hits") or 1)}
            dmg = A.pvp_damage(self.t, self.wid, [hit], self.defender, OPT.DEF)
            c = r["cancel_frame"]
            out.append({"slot": key, "hit": r["hit_windows"][0][0], "r1": c.get("r1"), "roll": c.get("dodge"),
                        "dmg": dmg, "attack": r})
        return out

    @staticmethod
    def string(gate, slots, ash_dmg, ash_last):
        """Best (damage per second, R1 count, damage, end frame) of the ash followed by R1 #1..#k
        pressed at the ash's R1 gate, each next R1 at the previous one's R1 cancel; the clock stops
        at the last R1's roll cancel, or the ash's last hit if that is later."""
        chain = [s for s in slots if s["slot"] in R1_SLOTS]
        best = None
        t, dmg = gate, ash_dmg
        for k, s in enumerate(chain, 1):
            if s["roll"] is None:
                break
            dmg += s["dmg"]
            end = max(t + s["roll"], ash_last)
            cand = (dmg / (end / FPS), k, dmg, round(end, 1))
            if best is None or cand[0] > best[0]:
                best = cand
            if s["r1"] is None:
                break
            t += s["r1"]
        return best

    # ---- the R2 a heavy-special buff swaps in (`HEAVY_SPECIAL_SPEFFECTS`)

    def special_clip(self, anim):
        """(hits [(frame, attack row)], `tae_details`) of one heavy-special clip on this weapon, or
        None when it opens no damaging hit. Every judge the clip's TAE opens (not SpEffect-gated)
        with motion value counts, each window that opens a hit record once (Stone Club a033_030620:
        judges 3982 and 3983 are MV 0, 3980 MV 175 at f67), on real frames from the clip start."""
        atk_mod = sys.modules[type(self.t.reg).__module__]
        reg = self.t.reg
        cat = atk_mod.motion_category(reg.weapon[self.wid], anim, right_hand_fallback=False)
        _, _, events = atk_mod.resolve_events(cat, anim)
        if events is None:
            return None
        judges = list(dict.fromkeys(
            int.from_bytes(e.params[8:12], "little", signed=True) for e in events
            if e.type == atk_mod.TAE_ATTACK_BEHAVIOR
            and not int.from_bytes(e.params[atk_mod.ATTACK_STATE_GATE_OFFSET:atk_mod.ATTACK_STATE_GATE_OFFSET + 2],
                                   "little")))
        hits, tae = [], None
        for j in judges:
            n = atk_mod.attack_numbers(reg, self.wid, j, self.level)
            if not n or not n["can_hit_enemy"] or n["mv_phys"] + n["mv_mag"] + n["mv_fire"] + n["mv_light"] \
                    + n["mv_holy"] <= 0:
                continue
            d = atk_mod.tae_details(reg, self.wid, anim, j, cat)
            if not d or not d["hit_windows"]:
                continue
            tae = tae or d
            row = {**n, "own_sweep_hits": 1}
            hits += [(wd["frames"][0], row) for wd in d["hit_window_detail"] if wd["hits"]]
        return (sorted(hits, key=lambda h: h[0]), tae) if hits else None

    def special_r2(self, kind, two):
        """{slot: {"hits" [(frame, attack row)], "r2", "roll", "windows"}} of heavy-special type
        `kind` on this weapon and grip (None: the plain R2, `PLAIN_HEAVY_ANIMS`), frames from the
        R2 press. `r2_1` / `r2_2`: the Start clip up
        to its release SpEffect (`er-mechanics-attacks.release_lead_in`, released at the earliest
        frame, `INFERRED` as for the plain R2), then the End clip; `r2_1c`: Start #1 held through
        (charged). `windows` are the clip's hyperarmor as `er-mechanics-ash-interrupt.simulate`
        takes them, bonus in menu units (`er-mechanics-exchange.POISE_MENU` 10) and no floor
        (`INFERRED`)."""
        atk_mod = sys.modules[type(self.t.reg).__module__]
        w = self.t.reg.weapon[self.wid]
        ids = [a + (2000 if two else 0) for a in (PLAIN_HEAVY_ANIMS if kind is None else HEAVY_SPECIAL_ANIMS[kind])]

        def slot(got, lead):
            hits, tae = got
            c = tae["cancel_frame"] or {}
            wins = ha_windows(tae, lead)
            return {"hits": [(lead + f, a) for f, a in hits], "lead": lead,
                    "r2": lead + c["r2"] if c.get("r2") is not None else None,
                    "roll": lead + c["dodge"] if c.get("dodge") is not None else None, "windows": wins}

        out = {}
        for key, start, end in (("r2_1", ids[0], ids[1]), ("r2_2", ids[2], ids[3])):
            lead = atk_mod.release_lead_in(w, start)
            got = self.special_clip(end)
            if lead is not None and got is not None:
                out[key] = slot(got, float(lead[0]))
        got = self.special_clip(ids[0])
        if got is not None:
            out["r2_1c"] = slot(got, 0.0)
        return {k: v for k, v in out.items() if v["roll"] is not None}

    def hit_poise(self, attack):
        """PvP poise damage of one hit in menu units: `poise_damage` x 10 x its
        `FinalDamageRateParam.saRate` (`er-mechanics-exchange`'s expression)."""
        fid = self.t.atk_extra.get(attack["atk_row"], {}).get("finalDamageRateId", -1)
        sa = (self.t.final_rate.get(fid) or {}).get("saRate", 1.0) if fid is not None and fid >= 0 else 1.0
        return attack["poise_damage"] * 10.0 * sa

    def chain_rate(self, hits, end, windows, hp):
        """HP a second of a chain: `hits` [(frame, attack row)] each worth `hp(attack row)`, from
        the press to the roll cancel `end`, netted against the opponent through `chain_net`
        when one is set."""
        vals = [hp(a) for _, a in hits]
        if self.chain_net is not None:
            net = self.chain_net([(f, self.hit_poise(a), v) for (f, a), v in zip(hits, vals)], windows, end)
        else:
            net = sum(vals)
        return net / (end / FPS) if end else 0.0

    def special_string(self, sp, hp):
        """Best (HP a second, label, end frame) of the heavy-special R2s `sp` (`special_r2`): R2 #1
        alone, R2 #1 then R2 #2 pressed at #1's R2 cancel (`INFERRED`: buffered, as the R1
        strings are), or the charged R2 #1; each to its last roll cancel. None without one."""
        cands = []
        a = sp.get("r2_1")
        if a:
            cands.append(("R2", a["hits"], a["roll"], a["windows"]))
            b = sp.get("r2_2")
            if b and a["r2"] is not None:
                t = a["r2"]
                cands.append(("R2 x2", a["hits"] + [(t + f, x) for f, x in b["hits"]], t + b["roll"],
                              a["windows"] + [(t + s, t + e, *r) for s, e, *r in b["windows"]]))
        c = sp.get("r2_1c")
        if c:
            cands.append(("charged R2", c["hits"], c["roll"], c["windows"]))
        best = None
        for label, hits, end, wins in cands:
            r = self.chain_rate(hits, end, wins, hp)
            if best is None or r > best[0]:
                best = (r, label, end)
        return best

    def gates(self, sid, prof):
        D = _disengage()
        cat = A.SKILL_TAE_BASE + self.t.arts[sid]["swordArtsTypeNew"]
        anim = A.main_anim(prof)    # the opening this weapon and grip play
        return D.clip(cat, int(anim)) if anim is not None else None

    def area(self, hits, scored, cat, melee_reach_arc):
        """Union of the ground a damaging bullet sweeps (its player-relative path widened by its
        largest radius), plus the blade's part, in m^2.

        The blade's part of a `melee bullet` row is its angle-weighted swept volume
        (`er-mechanics-reach.blade_sweep`, `melee_reach_arc[3]`) divided by the idle defender's
        hurtbox height: the ground area of a defender-tall column holding the same volume, the
        same footing as the bullet's ground area, which counts its whole path at every height.
        Other rows keep the sector world reach x effective arc (`melee_reach_arc[0]`, `[2]`): the
        angle weight would score a thrust's blade near zero, which their ranking has not been
        checked against."""
        cells = set()
        k = AREA_CELL_M
        for root in {h["root_bullet"] for h in scored if h["kind"] == "bullet"}:
            bt = A.bullet_tree(self.t, root)
            for n, path, _, _ in (A.fly_tree(self.t, bt) if bt else None) or ():
                if not A._bullet_damages_self(n):
                    continue
                r = self.t.bullet.get(n["bullet"], {})
                rad = max(r.get("hitRadius", 0.0), r.get("hitRadiusMax", 0.0), k / 2)
                pts = [(x, z) for x, _, z in path]
                dense = []
                for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
                    steps = max(1, int(math.hypot(x1 - x0, z1 - z0) / k))
                    dense += [(x0 + (x1 - x0) * i / steps, z0 + (z1 - z0) * i / steps) for i in range(steps)]
                dense += pts[-1:]
                ri = int(rad / k) + 1
                for x, z in dense:
                    cx, cz = int(round(x / k)), int(round(z / k))
                    for i in range(-ri, ri + 1):
                        for j in range(-ri, ri + 1):
                            if (i * k) ** 2 + (j * k) ** 2 <= rad ** 2:
                                cells.add((cx + i, cz + j))
        bullet_area = len(cells) * k * k
        blade = 0.0
        height = self.defender_height()
        if cat == "melee bullet" and melee_reach_arc and melee_reach_arc[3] is not None and height:
            blade = melee_reach_arc[3] / height
        elif cat != "bullet" and melee_reach_arc and melee_reach_arc[0] and melee_reach_arc[2]:
            blade = math.pi * melee_reach_arc[0] ** 2 * melee_reach_arc[2] / 360.0
        return round(bullet_area + blade, 1)

    @staticmethod
    def defender_height():
        """Vertical span of the idle defender's hurtbox (m), or None without the pose decoder."""
        _, reach_mod, _ = A._pose()
        hb = reach_mod.defender_hurtbox("idle") if reach_mod else None
        return hb["top_m"] - hb["bottom_m"] if hb else None

    def status_tables(self):
        if self._status is None:
            ST = _status_mod()
            st = ST.Tables(ar_tables=self.art)
            rows = ST.corpus_rows(rl=150)
            d = ST.corpus_defender(rows)
            d["hp"] = TARGET_HP
            # The corpus as defenders, for `buildup_hp`'s engagement model, under `FIGHT`'s pressure.
            d["dfs"] = ST.Defenders(st, rows)
            B = _buffs_mod()
            d["pressure"] = B.FIGHT_KINDS[FIGHT or B.DEFAULT_FIGHT]["pressure"]
            self._status = (ST, st, d)
        return self._status

    def hit_buildup(self, st, ws, atk_row, bullet=None):
        """({status: build-up}, {status: first row that built it}) of one hit, from the 13 slots
        `FUN_140d24b10` fills (`VERIFIED`, 1.16.2 :8765):

        - slots 0..4: a bullet hit takes the Bullet row's `spEffectId0..4`
          (`BulletParamEntry::GetSpEffectIds` in the bullet-hit caller `FUN_140526230`), a swing
          its AtkParam's; scaled by the AtkParam's `statusAilmentAtkPowerCorrectRate` x `_byPoint`;
        - slots 5..7: the weapon's rows (`spEffectBehaviorId0..2` + reinforce `spEffectId1..3`),
          the same for a bullet as for a swing, scaled by `statusAilmentAtkPowerCorrectRate` alone
          (`CalculateDamage2` 0x1404483b0, the `< 5` branch).

        A scale applies only to a row with `isUseStatusAilmentAtkPowerCorrect`: every weapon and
        infusion status row has it, a skill bullet's own status rows (Hoarfrost 1800/1801, Ice
        Spear 1530) do not. The AtkParam is the hitting bullet's own `atkId_Bullet`: Waves of
        Darkness's waves (30000812) are scale 0, so a Cold weapon's frost and an innate bleed or
        rot never ride them."""
        ST = self.status_tables()[0]
        atk = st.atk.get(atk_row)
        if atk and atk["disableHitSpEffect"]:
            return {}, {}
        if bullet is not None:
            own = [self.t.bullet[bullet][f"spEffectId{i}"] for i in range(5)]
        else:
            own = [atk[f"spEffectId{i}"] for i in range(5)] if atk else []
        rows = [(rid, True) for rid in own if rid > 0] + [(s["speffect"], False) for s in ws["sources"]]
        out, first = {}, {}
        for rid, both in rows:
            r = ST._row_amount(st, ws, rid, atk, both, 1.0, None)
            if r:
                out[r[0]] = out.get(r[0], 0) + r[1]
                first.setdefault(r[0], rid)
        return out, first

    def status_hp(self, two, hits, string_slots):
        """{status: HP} the cast's hits plus the string's R1s are worth as one engagement that
        lands them all, per hit as `hit_buildup` reads it (`buildup_hp`)."""
        ws, build, first, seq = self.status_seq(two, hits, string_slots)
        return self.buildup_hp(ws, build, first, seq)

    def status_seq(self, two, hits, string_slots):
        """(weapon status, {status: total build-up}, {status: first row that built it}, [{status:
        build-up} per landed hit, in order]) of the cast's hits plus the string's R1s."""
        ST, st, d = self.status_tables()
        ws = ST.weapon_status(st, self.weapon, self.affinity, self.level, self.stats, two)
        build, first, seq = {}, {}, []
        for atk_row, n, bullet in ([(h["atk_row"], h.get("count", 1), h.get("bullet")) for h in hits]
                                   + [(s["attack"]["atk_row"], max(1, len(s["attack"].get("hit_windows") or [])),
                                       None) for s in string_slots]):
            b, f = self.hit_buildup(st, ws, atk_row, bullet)
            seq += [b] * n
            for name, v in b.items():
                build[name] = build.get(name, 0) + v * n
                first.setdefault(name, f[name])
        return ws, build, first, seq

    def buildup_hp(self, ws, build, first, seq=None):
        """{status: HP} of the build-up `build` ({status: amount}), landed as the hits `seq`
        ({status: amount} each; one hit carrying all of `build` when omitted), the proc row being
        the weapon's own for that status, else `first[status]`, the first row that built it.

        With the corpus defenders this is `er-mechanics-status.sequence_hp`: a proc counts only
        when the gauge empties, carriers of the bolus clear it after every engagement and
        everyone else keeps it minus the refill, so build-up short of a proc is worth only what
        the non-carriers accumulate. User, 2026-10-19: "If it takes 10 hits with a bleed weapon to
        build up bleed, that's 10 hits with a lower damage dealing weapon that may never proc
        bleed". Without them (an old shim) it is the linear model below."""
        ST, st, d = self.status_tables()
        if d.get("dfs") is not None:
            hp = ST.sequence_hp(st, ws, seq or [build], first, d["dfs"], pressure=d.get("pressure", 1.0))
            return {k: round(v) for k, v in hp.items() if round(v)}
        out = {}
        for name, b in build.items():
            resist = d["resist"][next(s[7] for s in ST.STATUSES if s[0] == name)]
            # The proc row: the weapon's own, else the first row that built the status (a skill
            # bullet's own frost or poison).
            src = ST.proc_row_for(st, ws, name, first[name])
            if not (src and resist):
                continue
            pe = ST.proc_effect(st, src, d["hp"])
            cut = 1.0
            if pe["ticks"] > 1 and pe["interval"] > 0 and pe["hp_total"]:
                # A damage-over-time proc (poison, rot) only counts the ticks inside one battle
                # (`er-mechanics-status.BATTLE_SECONDS`): the full duration is longer than a PvP
                # fight and is cured or outlasted.
                cut = min(pe["ticks"], int(ST.BATTLE_SECONDS / pe["interval"])) / pe["ticks"]
            out[name] = pe["hp_total"] * b / resist * cut
        return {k: round(v) for k, v in out.items() if v}

    # ---- what a buff ash adds on its own carrier (`BUFF_MODEL`)

    def buff_tables(self):
        """(buffs module, `Buffs`, {AtkParam_Pc row: by-point/rate/dmg}, {SpEffect:
        atkOccurrenceSpEffectId}), read once."""
        if self._buff is None:
            B = _buffs_mod()
            m = B.Buffs()
            pr = B.PR
            by = ("spEffectAtkPowerCorrectRate_byPoint", "spEffectAtkPowerCorrectRate_byRate",
                  "spEffectAtkPowerCorrectRate_byDmg")
            atk = {r["id"]: r for r in pr.rows(pr.param_bytes(m._files, "AtkParam_Pc"), list(by))[0]}
            occ = {r["id"]: r["atkOccurrenceSpEffectId"]
                   for r in pr.rows(pr.param_bytes(m._files, "SpEffectParam"), ["atkOccurrenceSpEffectId"])[0]}
            self._buff = (B, m, atk, occ)
        return self._buff

    def follow_damage(self, ctx, attack, pt, pre=None, flat=None, post=None):
        """PvP damage of one follow-up attack row (`r1_slots`' `attack`) on the RL150 defender, the
        way `er-builds-pvp.slot_hit` takes a buff: `pre[e]` scales the AR before the motion value,
        `flat[e]` is added after it, `post[e]` multiplies each element after defense. With none
        of them it is the slot's own `dmg`."""
        mv = {"physical": attack["mv_phys"], "magic": attack["mv_mag"], "fire": attack["mv_fire"],
              "lightning": attack["mv_light"], "holy": attack["mv_holy"]}
        w = self.t.reg.weapon[self.wid]
        hit = {el: (ctx.ar_by[el] * mv[el] / 100.0 * (pre or {}).get(el, 1.0) + (flat or {}).get(el, 0.0))
               * w.get(f"vsPlayerDmgCorrectRate_{A.VS_PLAYER[el]}", 1.0) for el in A.ELEMENTS}
        fid = self.t.atk_extra.get(attack["atk_row"], {}).get("finalDamageRateId", -1)
        fr = self.t.final_rate.get(fid) if fid >= 0 else None
        by = OPT.DEF.damage(hit, 100.0, self.defender, pt, fr)["by_type"]
        total = sum(v * (post or {}).get(el, 1.0) for el, v in by.items())
        if 0.0 < total < 1.0:
            total = math.ceil(total)
        return total * max(1, attack.get("own_sweep_hits") or 1)

    def buff_gain(self, sid, prof, two, ctx, first_hits, rep, end_s, chain=()):
        """What skill `sid`'s buff adds on this carrier (`BUFF_MODEL`), or None when the skill puts
        no buff on its user.

        `first_hits` are the `r1_slots` entries the row's own clock already holds after the buff
        is on (a utility row's follow-up, a quick weapon's string R1s; none for a cast scored
        alone), `rep` the next attack when the clock holds none (the follow-up, else R1 #1),
        `end_s` the end of the row's clock in seconds from cast start, and `chain` the weapon's
        R1 slots in order. A next-hit row covers the first of `first_hits`, or one `rep` hit when
        there is none and its duration outlasts `end_s`. A timed row covers every one of
        `first_hits` plus the R1 chain's buffed damage per second over the rest of the
        engagement window, min(duration, `BUFF_WINDOW_S`) - `end_s`. The cast's own hits are not
        counted as buffed (whether the buff event comes before them is not read).

        Returns {`rows` (SpEffects that change a hit, after the hand gate), `kind` ("next hit" /
        "timed", None when no row changes a hit), `duration_s`, `hits` covered inside the clock,
        `window_s` chain seconds covered after it, `per_hit` HP added to `rep`, `mult` buffed /
        plain `rep`, `added` HP over everything covered, `status_hp` the build-up its
        `atkOccurrenceSpEffectId` rows add, as HP}."""
        roots = [s["id"] for s in A.skill_buffs(self.t, prof) if s.get("effect") == "buff"]
        if not roots:
            return None
        B, m, atk_by, occ = self.buff_tables()
        groups = {}
        # `Buffs.skill_rows` keeps the rows that change a hit; a row whose only effect is an
        # `atkOccurrenceSpEffectId` (Chilling Mist's 826 -> 880, frost 60 on whoever is hit) is
        # kept too. The buff row's own `freezeAttackPower` 30 builds nothing: the hit path
        # dispatches on `stateInfo` (`er-mechanics-status.row_status`), and 826 is 152.
        for i in dict.fromkeys(i for r in roots for i, _ in m._closure([r])):
            if not (m.relevant(i) or occ.get(i, -1) > 0):
                continue
            row = m.sp[i]
            d = row["effectEndurance"]
            if row["throwAttackParamChange"]:
                continue
            if m.one_hit(i):
                groups.setdefault(("next hit", d), []).append(i)
            elif d < 0 or d >= B.REFRESHED_ROW_S:
                groups.setdefault(("timed", d), []).append(i)
        horizon_mean = sum(B.FIGHT_SECONDS) / len(B.FIGHT_SECONDS)
        ST, st, _ = self.status_tables()
        ws = ST.weapon_status(st, self.weapon, self.affinity, self.level, self.stats, two)
        ratio = [None]
        build, first, best = {}, {}, None
        out = {"model": BUFF_MODEL, "rows": [], "kind": None, "duration_s": 0.0, "hits": 0.0, "window_s": 0.0,
               "per_hit": 0.0, "added": 0.0, "status_hp": {}, "mult": 1.0}

        def gain(ids, slot):
            """(HP the rows add to one `slot` hit, the rows that passed the hand gate, the
            build-up they add {status: amount}, the slot's plain damage)."""
            attack = slot["attack"]
            pt = A.phys_type(self.t, self.wid, attack["atk_row"])
            by = atk_by.get(attack["atk_row"], {})
            c = m.attack_context(ids, pvp=True, phys_type=pt, two_handed=two,
                                 by_point=by.get("spEffectAtkPowerCorrectRate_byPoint", 100),
                                 by_rate=by.get("spEffectAtkPowerCorrectRate_byRate", 100),
                                 by_dmg=by.get("spEffectAtkPowerCorrectRate_byDmg", 100))
            used = [i for i, _ in c["entries"]]
            plain = self.follow_damage(ctx, attack, pt)
            if not used:
                return 0.0, used, {}, plain        # the hand gate refused it (the left-hand twin)
            pre = dict(c["ar_rate"])
            if any(c["stats"].values()) or any(c["rate_points"].values()):
                if ratio[0] is None:
                    ratio[0] = B.ar_stat_ratio(self.weapon, self.affinity, self.level, self.stats, two, self.art)
                sr = ratio[0](c["stats"], c["rate_points"])
                pre = {e: v * sr.get(e, 1.0) for e, v in pre.items()}
            post = {e: c["pvp_rate"][e] * c["atk_rate"][e] for e in A.ELEMENTS}
            add = self.follow_damage(ctx, attack, pt, pre, c["flat_add"], post) - plain
            status = {}
            for i in used:
                o = occ.get(i, -1)
                got = ST._row_amount(st, ws, o, st.atk.get(attack["atk_row"]), False, 1.0, None) if o > 0 else None
                if got:
                    status[got[0]] = status.get(got[0], 0) + got[1] * max(1, attack.get("own_sweep_hits") or 1)
                    first.setdefault(got[0], o)
            return add, used, status, plain

        # The R1 chain a timed buff covers after the row's clock: the k of R1 #1..#k with the best
        # damage per second from frame 0 to the last R1's roll cancel (`Table.string`).
        sc = self.string(0.0, chain, 0.0, 0.0) if chain else None
        run = list(chain[:sc[1]]) if sc else [rep]
        run_s = (sc[3] if sc else rep["roll"] or rep["hit"]) / FPS
        for (kind, d), ids in sorted(groups.items()):
            horizon = d if d >= 0 else horizon_mean
            window_s = max(0.0, min(horizon, BUFF_WINDOW_S) - end_s) if kind == "timed" else 0.0
            if kind == "next hit":
                covered = first_hits[:1] if first_hits else ([rep] if horizon > end_s else [])
                weights = [1.0] * len(covered)
            else:
                covered = list(first_hits) + run
                weights = [1.0] * len(first_hits) + [window_s / run_s if run_s else 0.0] * len(run)
            special = next((HEAVY_SPECIAL_SPEFFECTS[i] for i in ids if i in HEAVY_SPECIAL_SPEFFECTS), None)
            sp = self.special_r2(special, two) if kind == "timed" and special is not None and sc else {}
            if sp:
                # The window's R1 chain is weighed as a whole instead, against the R2 string the
                # buff swaps in (`BUFF_MODEL` buff-v3).
                covered, weights = covered[:len(first_hits)], weights[:len(first_hits)]
            added = 0.0
            for slot, w in zip(covered, weights):
                if w:
                    add, _, status, _ = gain(ids, slot)
                    added += add * w
                    for k, v in status.items():
                        build[k] = build.get(k, 0) + v * w
            spec_out = None
            if sp:
                hits, wins, t = [], [], 0.0
                for s in run:
                    hits.append((t + s["hit"], s["attack"]))
                    wins += ha_windows(s["attack"], t)
                    t += s["r1"] or 0.0
                cache = {}

                def hp(attack, buffed=True):
                    k = (attack["atk_row"], buffed)
                    if k not in cache:
                        a, _, _, p = gain(ids, {"attack": attack})
                        cache[k] = p + a if buffed else p
                    return cache[k]

                plain_r1 = self.chain_rate(hits, sc[3], wins, lambda x: hp(x, False))
                plain_r2 = self.special_string(self.special_r2(None, two), lambda x: hp(x, False))
                buffed_r1 = self.chain_rate(hits, sc[3], wins, hp)
                r2 = self.special_string(sp, hp)
                # Without the buff the window plays the better of the plain R1 chain and the plain
                # R2, with it the better of the buffed R1 chain and the swapped-in R2; a string
                # that nets less than nothing is not thrown (`INFERRED`), so each side is at least 0.
                base = max(0.0, plain_r1, plain_r2[0] if plain_r2 else 0.0)
                top = max(0.0, buffed_r1, r2[0] if r2 else 0.0)
                added += window_s * max(0.0, top - base)
                spec_out = {"type": special, "r2": r2[1] if r2 else None, "r2_end": r2[2] if r2 else None,
                            "r2_rate": round(r2[0], 1) if r2 else None, "r1_rate": round(buffed_r1, 1),
                            "plain_rate": round(plain_r1, 1),
                            "plain_r2_rate": round(plain_r2[0], 1) if plain_r2 else None,
                            "netted": self.chain_net is not None,
                            "window": "R2" if r2 and r2[0] > buffed_r1 else "R1"}
            rep_add, used, _, plain = gain(ids, rep)
            if not used:
                continue
            out["rows"] += used
            # `kind` .. `mult` describe the group that adds the most (one group on every buff ash
            # in the 1.17.1 regulation: the rows of one skill share a duration).
            if best is None or added > best:
                best = added
                out.update(kind=kind, duration_s=d, hits=round(sum(weights[:len(first_hits)]) if kind == "timed"
                                                                 else sum(weights), 2),
                           window_s=round(window_s, 2), per_hit=round(rep_add, 1),
                           mult=round((plain + rep_add) / plain, 4) if plain else 1.0)
                if spec_out:
                    out["special"] = spec_out
            out["added"] += added
        out["added"] = round(out["added"], 1)
        out["status_hp"] = self.buildup_hp(ws, build, first) if build else {}
        return out

    def _armor(self, names):
        names = set(names)
        rows = S.OPT.AR.EPR.rows(S.OPT.AR.EPR.param_bytes(S.OPT.AR.EPR.load(None), "EquipParamProtector"),
                                 None, strict=False)[0]
        row_names = S.OPT.AR.EPR.row_names("EquipParamProtector")
        found = {}
        for r in rows:
            n = row_names.get(r["id"])
            if n in names and n not in found:
                found[n] = r["toughnessCorrectRate"]
        missing = names - set(found)
        if missing:
            raise SystemExit(f"armor not found: {sorted(missing)}")
        return sum(found.values())

    def travel(self, root):
        if root not in self._travel:
            bt = A.bullet_tree(self.t, root)
            tree = A.fly_tree(self.t, bt) if bt else None
            if not tree:
                self._travel[root] = 0.0
            else:
                o = tree[0][1][0]
                self._travel[root] = max((math.dist(p, o) for n, pa, _, _ in tree
                                          if A._bullet_damages_self(n) for p in pa), default=0.0)
        return self._travel[root]

    def reach(self, root):
        """(min, max) horizontal distance from the player, in metres, over which a damaging bullet
        of this tree can touch a target: its path in `fly_tree`'s player-relative frame, widened by
        the larger of `hitRadius` and `hitRadiusMax`."""
        bt = A.bullet_tree(self.t, root)
        tree = A.fly_tree(self.t, bt) if bt else None
        lo, hi = math.inf, 0.0
        for n, path, _, _ in tree or ():
            if not A._bullet_damages_self(n):
                continue
            r = self.t.bullet.get(n["bullet"], {})
            rad = max(r.get("hitRadius", 0.0), r.get("hitRadiusMax", 0.0))
            for x, _, z in path:
                d = math.hypot(x, z)
                lo, hi = min(lo, max(0.0, d - rad)), max(hi, d + rad)
        return (lo, hi) if hi else None

    def melee_reach(self, sid, hits):
        """(world reach, root motion to that hit, effective arc, blade swept volume) for the cast's
        blade hits, from `er-mechanics-reach.skill_reach`. Reach and root motion (m) and arc (deg)
        are those of the farthest-reaching hit: forward distance of its farthest hit point from
        where the cast started, so a lunge or step is included. The volume (m^3,
        `blade_swept_volume_m3`) is summed over the cast's animations; one animation's row already
        covers every hit window in it, so within an animation the largest is kept rather than one
        per judge. None without a posed hit."""
        _, reach_mod, _ = A._pose()
        cat = A.SKILL_TAE_BASE + self.t.arts[sid]["swordArtsTypeNew"]
        best, volume = None, {}
        for anim, judge in {(h["anim"], h["judge"]) for h in hits if h["kind"] == "melee"}:
            row = reach_mod.skill_reach(self.wid, cat, anim, judge)
            if row and row.get("blade_swept_volume_m3") is not None:
                volume[anim] = max(volume.get(anim, 0.0), row["blade_swept_volume_m3"])
            if row and row.get("world_reach_m") is not None and (best is None or row["world_reach_m"] > best[0]):
                best = (round(row["world_reach_m"], 1), round(row.get("root_motion_to_hit_m") or 0.0, 1),
                        row.get("coverage_arc_eff_deg"))
        return best + (round(sum(volume.values()), 3) if volume else None,) if best else None

    def contexts(self, two):
        st = {k: self.stats[k] for k in S.DAMAGE_STATS}
        return (A.WeaponContext(self.weapon, self.affinity, stats=st, two_handed=two, ar_tables=self.art),
                A.WeaponContext(self.weapon, self.affinity, level=0, stats=st, two_handed=two,
                                ar_tables=self.art))

    @staticmethod
    def sources(ctx, ctx0, h):
        out = dict.fromkeys(SOURCES, 0.0)
        for el in A.ELEMENTS:
            terms = S.stat_terms(ctx, el, h["element_correct_override"], h["disable_2h"])
            if not h["from_weapon"]:
                a = h["attack"].get(el, 0.0)
                if not a:
                    continue
                if terms is None:
                    out["fixed"] += a
                    continue
                b = a / (1 + sum(terms.values()))
                out["fixed"] += b
                for s, v in terms.items():
                    out[s] += b * v
                continue
            part = ctx.base_by[el] * h["mv"].get(el, 0) / 100 + h["flat"].get(el, 0.0) * ctx.base_atk_rate
            part0 = ctx0.base_by[el] * h["mv"].get(el, 0) / 100 + h["flat"].get(el, 0.0) * ctx0.base_atk_rate
            if terms is None:
                m = ctx.multiplier(el, h["element_correct_override"], h["disable_2h"])
                out["fixed"] += part0 * m
                out["weapon level"] += (part - part0) * m
                continue
            out["fixed"] += part0
            out["weapon level"] += part - part0
            for s, v in terms.items():
                out[s] += part * v
        return out

    def hyperarmor(self, sid, two=False, stance=None):
        p = A.skill_profile(self.t, sid, self.wid, self.level, two, stance)
        an = p["anims"]
        key = A.main_anim(p)
        ha = [e for e in an.get(key, an.get(str(key), [])) if e["kind"] == "hyperarmor"]
        if not ha:
            return None
        e = min(ha, key=lambda e: e["frames"][0])
        tr = self.t.reg.toughness[e["toughness_row"]]
        weapon_term = (tr["correctionRate"] * self.t.reg.weapon[self.wid]["toughnessCorrectRate"]
                       if e["weapon_term"] in (1, 2) else 0.0)
        menu = 1000 * (self.armor * tr["proCorrectionRate"] + weapon_term)
        poise = menu / (BULL_GOAT_CUT if self.bull_goat else 1.0) / e["pvp_poise_damage_taken"]
        lead = A.lead_in(self.t, sid, p)
        return {"start": A.lead_in_ha_start(p, e["frames"][0] + lead) if lead else e["frames"][0],
                "end": max(h["frames"][1] for h in ha) + lead, "poise": round(poise)}

    def rows(self, grips):
        out = []
        for two in grips:
            need = OPT.requirements(self.art, self.weapon, self.affinity, self.level, two)
            if not all(self.stats[k] >= need[k] for k in self.stats):
                continue
            ctx, ctx0 = self.contexts(two)
            slots = self.r1_slots(two, ctx)
            r1_1 = next((s for s in slots if s["slot"] == "r1_1"), None)
            quick = bool(r1_1 and r1_1["r1"] is not None and r1_1["r1"] <= QUICK_R1_CANCEL)
            # A weapon that takes no ashes of war (`gemMountType` 0) is swept with its own skill; the
            # unique and unobtainable rules are about mounting a gem onto some other weapon.
            fixed = self.fixed_skill()
            for sid in A.mountable_skills(self.t, self.wid, AR.AFFINITIES.index(self.affinity), self.level):
                name = self.t.arts_name(sid)
                if name in self.blacklist or (not fixed and (self.classes.get(sid, 0) < 2
                                                             or sid not in self.obtainable)):
                    continue
                # A stance skill is swept once per button its stance hands over on (Unsheathe's
                # R1 slash 040060 and R2 slash 040070), each with the stance entry in front
                # (`A.lead_in`), one row per button (`variant`, `stance`): the player picks the
                # button at cast time, so the loadout values the ash on both
                # (`er-mechanics-combo-loadout.merge_stance_buttons`).
                stances = A.skill_profile(self.t, sid, self.wid, self.level, two)["stance_buttons"] or [None]
                for stance in stances:
                    sc = S.AshScorer(self.lev, self.weapon, self.affinity, sid, two, self.defender, None, stance)
                    hits = sc.hits(self.stats)
                    prof = A.skill_profile(self.t, sid, self.wid, self.level, two, stance)
                    clip = self.gates(sid, prof)
                    gate = clip["gates"]["r1"] if clip else None
                    if A.untimed_hits(prof, hits):
                        self.untimed[name] += 1
                        continue
                    # Frames are cast-relative: a cast that plays a clip before its opening
                    # (`A.lead_in`) adds it to the opening's hits and gate.
                    lead = A.lead_in(self.t, sid, prof)
                    if lead:
                        for h in hits:
                            h["frame"] = round(h["frame"] + lead, 1)
                        gate = round(gate + lead, 1) if gate is not None else None
                    if not hits:
                        # A step or buff: scored by the follow-up out of it (`FOLLOW_UP_SLOTS`).
                        if gate is None:
                            continue
                        step = self.t.arts[sid]["swordArtsTypeNew"] in ROLLING_ARTS
                        allowed = STEP_FOLLOW_UP_SLOTS if step else FOLLOW_UP_SLOTS
                        fu = [s for s in slots if s["slot"] in allowed and s["roll"] is not None]
                        if not fu:
                            continue
                        f = min(fu, key=lambda s: s["hit"])
                        land, roll = round(gate + f["hit"], 1), round(gate + f["roll"], 1)
                        ha = self.hyperarmor(sid, two, stance)
                        out.append({"ash": name, "weapon": self.weapon, "affinity": self.affinity, "reach": None,
                                    "melee_reach": None, "grip": "2H" if two else "1H", "cat": "utility",
                                    "dmg": round(f["dmg"]), "dps": round(f["dmg"] / (roll / FPS)),
                                    "first": land, "last": roll, "follow_up": f["slot"], "gate": gate,
                                    "ha": ha, "ha_r1s": int(ha["poise"] // LONGSWORD_R1_POISE) if ha else 0,
                                    "covers": False, "chain": prof["fp_cost"].get("R2", -1) != -1,
                                    "quick": quick, "score": round(f["dmg"] / (roll / FPS)), "area": 0.0,
                                    "string": None, "status_hp": self.status_hp(two, [], [f]), "contrib": {},
                                    **A.stance_row(prof)})
                        if lead:
                            out[-1]["lead"] = lead
                        # A buff adds what it buffs on this carrier (`BUFF_MODEL`).
                        if not step:
                            apply_buff(out[-1], self.buff_gain(sid, prof, two, ctx, *buff_hits(out[-1], slots)))
                        if not pvp_utility(name, self.t.arts[sid]["swordArtsTypeNew"], out[-1].get("buff")):
                            out.pop()
                        elif name in CAMOUFLAGE_SKILLS:
                            out[-1]["hidden_beyond_m"] = camouflage_m(CAMOUFLAGE_SKILLS[name])
                        continue
                    A.pvp_damage(self.t, self.wid, hits, self.defender, OPT.DEF)
                    bullets = [h for h in hits if h["kind"] == "bullet"]
                    if name in MELEE_BULLET:
                        cat, scored = "melee bullet", hits
                    elif not bullets:
                        cat, scored = "non-bullet", hits
                    else:
                        scored = [h for h in bullets if self.travel(h["root_bullet"]) > MIN_TRAVEL_M]
                        cat = "bullet"
                        if not scored:
                            # Bullets that never leave the blade's reach make a melee cast, scored on
                            # every hit: Romina's Purification's blooms travel 1.03 m beside its two
                            # swings, and dropping the cast left Poleblade of the Bud with no row.
                            if not any(h["kind"] == "melee" for h in hits):
                                continue
                            cat, scored = "non-bullet", hits
                    dmg = A.pvp_damage(self.t, self.wid, scored, self.defender, OPT.DEF)
                    A.pvp_damage(self.t, self.wid, hits, self.defender, OPT.DEF)
                    first, last = min(h["frame"] for h in scored), max(h["frame"] for h in scored)
                    contrib = {}
                    for h in hits:
                        if h["kind"] == "bullet" and cat == "bullet" and h not in scored:
                            continue
                        src = self.sources(ctx, ctx0, h)
                        atk = sum(src.values())
                        g = contrib.setdefault("bullet" if h["kind"] == "bullet" else "blade", dict.fromkeys(SOURCES, 0.0))
                        for k, v in src.items():
                            g[k] += h["pvp_damage_total"] * v / atk if atk else 0.0
                    ha = self.hyperarmor(sid, two, stance)
                    spans =[r for r in (self.reach(root) for root in {h["root_bullet"] for h in scored
                                                                       if h["kind"] == "bullet"}) if r]
                    reach = (round(min(r[0] for r in spans), 1), round(max(r[1] for r in spans), 1)) if spans else None
                    mr = self.melee_reach(sid, hits)
                    dps = round(dmg / (last / FPS)) if last else round(dmg)
                    # The string counts every hit of the cast, the blade too: it all lands in a string.
                    all_dmg = sum(h["pvp_damage_total"] for h in hits)
                    all_last = max(h["frame"] for h in hits)
                    # An R1 pressed before the cast's last hit would cut it off (a held skill's loop
                    # opens R1 at frame 0), so the string starts no earlier than that hit.
                    st = self.string(max(gate, all_last), slots, all_dmg, all_last) if gate is not None else None
                    used = []
                    if st:
                        used = [s for s in slots if s["slot"] in R1_SLOTS][:st[1]]
                    out.append({"ash": name, "weapon": self.weapon, "affinity": self.affinity, "reach": reach,
                                "melee_reach": mr if cat != "bullet" else None, "grip": "2H" if two else "1H",
                                "cat": cat, "dmg": round(dmg), "dps": dps, "first": first, "last": last,
                                "ha": ha, "ha_r1s": int(ha["poise"] // LONGSWORD_R1_POISE) if ha else 0,
                                "covers": bool(ha) and ha["start"] <= first and ha["end"] >= last,
                                "chain": prof["fp_cost"].get("R2", -1) != -1, "gate": gate, "quick": quick,
                                "string": ({"dps": round(st[0]), "r1s": st[1], "dmg": round(st[2]), "end": st[3]}
                                           if st else None),
                                "score": round(st[0]) if quick and st else dps,
                                "area": self.area(hits, scored, cat, mr),
                                "status_hp": self.status_hp(two, hits, used),
                                "contrib": {g: {k: round(v) for k, v in d.items()} for g, d in contrib.items()},
                                **A.stance_row(prof)})
                    if lead:
                        out[-1]["lead"] = lead
                    # A damaging ash that also buffs its user (Cragblade) adds what the buff adds to
                    # the hits after the cast (`BUFF_MODEL`).
                    bh = buff_hits(out[-1], slots)
                    if bh:
                        apply_buff(out[-1], self.buff_gain(sid, prof, two, ctx, *bh))
        return out


#: Status affinities swept for a weapon that already carries a status (user, 2026-10-08: extra or
#: matching statuses on a status weapon).
STATUS_AFFINITIES = ("Blood", "Cold", "Poison")
#: Waves of Darkness is also swept on Cold (user). The frost lands through its two swings
#: (AtkParam 300000810 / 300000811, scale 100 / 50) and the zero-damage AtkParam 0 carriers at the
#: impact point; the waves themselves (30000812) are scale 0 and build none (`hit_buildup`).
COLD_ASH_AFFINITY = ("Waves of Darkness", "Cold")


def variants(tab, weapon):
    """Extra affinities to sweep for `weapon`: the status ones when it has an innate status, and
    Cold when it can mount Waves of Darkness."""
    ST, st, _ = tab.status_tables()
    out = []
    try:
        if ST.weapon_status(st, weapon, "Standard", 0)["sources"]:
            out += list(STATUS_AFFINITIES)
    except SystemExit:
        return out
    wid = tab.t.find_weapon(weapon)
    waves = tab.t.find_arts(COLD_ASH_AFFINITY[0])
    cold = AR.AFFINITIES.index(COLD_ASH_AFFINITY[1])
    level = tab.art.max_level(tab.t.reg.weapon[wid]["reinforceTypeId"])
    if COLD_ASH_AFFINITY[1] not in out and waves in A.mountable_skills(tab.t, wid, cold, level):
        out.append(COLD_ASH_AFFINITY[1])
    return out


def families():
    return tomllib.loads(PREFERENCES.read_text()).get("family", [])


def drop_superseded(rows, multi):
    """Rows with only one member of each `data/ash-preferences.toml` family: the one whose first
    hit comes soonest, a tie going to the one that chains. Decided per weapon, or over all rows
    when `multi` (each ash judged by its earliest first hit anywhere). Returns the rows and
    {dropped ash: kept ash}."""
    dropped = {}
    scopes = [None] if multi else sorted({x["weapon"] for x in rows})
    for scope in scopes:
        mine = [x for x in rows if scope is None or x["weapon"] == scope]
        for fam in families():
            present = {}
            for x in mine:
                if x["ash"] in fam["members"]:
                    key = (x["first"], not x.get("chain"))
                    present[x["ash"]] = min(present.get(x["ash"], key), key)
            if len(present) > 1:
                keep = min(present, key=present.get)
                for ash in present:
                    if ash != keep:
                        dropped[(scope, ash)] = keep
    kept = [x for x in rows if (None if multi else x["weapon"], x["ash"]) not in dropped]
    return kept, {ash: keep for (_, ash), keep in dropped.items()}


def render(rows, title, subtitle, blacklist, dropped):
    multi = len({x["weapon"] for x in rows}) > 1
    H = (["ash", "bullet reach min / max (m)", "grip", "damage / s to last hit", "damage", "first hit", "last hit", "HA start", "HA end",
          "HA poise", "longsword R1s absorbed", "HA covers every hit"]
         + [f"bullet: {LABEL[s]}" for s in SOURCES] + [f"blade: {LABEL[s]}" for s in SOURCES])
    nb = 12

    def cls(i):
        return " bu" if nb <= i < nb + len(SOURCES) else " bd" if i >= nb + len(SOURCES) else ""

    def cells(x):
        ha = x["ha"] or {}
        bu, bd = x["contrib"].get("bullet", {}), x["contrib"].get("blade", {})
        bullet = f"{x['reach'][0]} / {x['reach'][1]}" if x.get("reach") else ""
        blade = (f"{x['melee_reach'][0]} ({x['melee_reach'][1]} from movement)"
                 if x.get("melee_reach") else "")
        reach = {"bullet": bullet, "non-bullet": blade,
                 "melee bullet": "; ".join(p for p in (blade and f"blade {blade}", bullet and f"bullet {bullet}")
                                           if p), "utility": ""}[x["cat"]]
        name = f"<b>{html.escape(x['ash'])}</b>"
        if x.get("variant"):
            name += f" <small>{html.escape(x['variant'])}</small>"
        if multi:
            name += f"<br><small>{html.escape(x['weapon'])}</small>"
        return ([name, reach, x["grip"], x["dps"], x["dmg"], x["first"], x["last"],
                 ha.get("start", ""), ha.get("end", ""), ha.get("poise", ""), x["ha_r1s"] if ha else "",
                 "yes" if x["covers"] else ""]
                + [bu.get(s) or "" for s in SOURCES] + [bd.get(s) or "" for s in SOURCES])

    def tr(v, pre=""):
        return "<tr>" + pre + "".join(f"<td class='{'' if i in (0, 2) else 'n'}{cls(i)}'>{c}</td>"
                                      for i, c in enumerate(v)) + "</tr>"

    def hdr(pre="", reach="reach (m)"):
        return "<tr>" + pre + "".join(f"<th class='{cls(i)}'>{reach if i == 1 else h}</th>"
                                      for i, h in enumerate(H)) + "</tr>"

    def ranked(c):
        r = sorted((x for x in rows if x["cat"] == c), key=lambda x: -x["dps"])
        if multi:
            seen = set()
            r = [x for x in r if not (x["ash"] in seen or seen.add(x["ash"]))]
        return r

    out = [f"<html><head><meta charset=utf-8><title>{html.escape(title)}</title><style>"
           "body{font-family:sans-serif;margin:24px}table{border-collapse:collapse;margin-bottom:24px}"
           "td,th{border:1px solid #888;padding:4px 8px}th{background:#ddd}td.n{text-align:right}"
           "th.bu{background:#cde}td.bu{background:#eef4ff}th.bd{background:#edc}td.bd{background:#fff4ec}"
           "</style></head><body>",
           f"<h2>{html.escape(title)}</h2><p>{html.escape(subtitle)}</p>",
           "<p>Ranked by damage per second to the last scored frame (30 fps). Contribution columns split each "
           "hit group's PvP damage by source: <b>fixed</b> = the attack at +0 before stat scaling, <b>weapon "
           "level</b> = what the upgrade adds to it, each stat = its share of the scaling. Blue = bullet hits, "
           "orange = blade hits; for bullet-category ashes the blade columns are shown but not counted. HA poise "
           "is effective PvP hyperarmor; a longsword 1H R1 deals 110.</p>",
           "<table>" + hdr("<th>variation</th>")]
    for c, label in CATEGORIES.items():
        r = ranked(c)
        if r:
            out.append(tr(cells(r[0]), f"<td>{label}</td>"))
    out.append("</table><h3>Every ash, by category</h3>")
    for c, label in CATEGORIES.items():
        out.append(f"<h4>{label}</h4><table>" + hdr(reach=REACH_HEADER[c]))
        out += [tr(cells(x)) for x in ranked(c)]
        out.append("</table>")
    out.append("<h3>Look-alike ashes hidden</h3><p>One per family in data/ash-preferences.toml: the one that "
               "hits first, a tie going to the one that chains.</p><table><tr><th>hidden</th><th>shown instead</th>"
               "</tr>" + "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>"
                                 for k, v in sorted(dropped.items())) + "</table>")
    out.append("<h3>Blacklisted</h3><table><tr><th>ash</th><th>reason</th></tr>"
               + "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in blacklist.items())
               + "</table><p>Unique and unobtainable ashes also excluded. Bullet frames are spawn frames; flight "
               "time is not counted.</p></body></html>")
    return "\n".join(out)


def selftest() -> int:
    """Status per hit on Cold Great Stars Waves of Darkness (1.17.1 regulation)."""
    tab = Table("Great Stars", "Cold", {"str": 30, "dex": 15, "int": 10, "fth": 10, "arc": 10},
                ["Verdigris Armor"], True)
    _, st, _ = tab.status_tables()
    ws = _status_mod().weapon_status(st, tab.weapon, tab.affinity, tab.level, tab.stats, True)
    fails = []

    def check(label, got, want):
        ok = got == want
        print(f"{'ok' if ok else 'FAIL'}  {label}: {got} (want {want})")
        if not ok:
            fails.append(label)

    srcs = {s["speffect"]: s for s in ws["sources"]}
    check("Cold Great Stars weapon rows", sorted(srcs), [6401, 107625])
    # 107600 + reinforce 925 spEffectId2 25 = frost 127; 6401 bleed 50; both PvP rate 1.0.
    frost, bleed = 127, 50
    wave = tab.hit_buildup(st, ws, 30000812, 2811)[0]
    check("wave 2811 (AtkParam 30000812, scale 0): frost", wave.get("frost", 0), 0)
    check("wave 2811: bleed", wave.get("bleed", 0), 0)
    check("swing 300000810 (scale 100): frost", tab.hit_buildup(st, ws, 300000810)[0].get("frost"), frost)
    check("swing 300000811 (scale 50): frost", tab.hit_buildup(st, ws, 300000811)[0].get("frost"), 63)
    check("swing 300000810: bleed", tab.hit_buildup(st, ws, 300000810)[0].get("bleed"), bleed)
    check("carrier 2810 (AtkParam 0, scale 100): frost", tab.hit_buildup(st, ws, 0, 2810)[0].get("frost"), frost)
    # A skill bullet's own row sits in slots 0..4 from the Bullet row, without the scale.
    hoar = next(b for b, r in sorted(tab.t.bullet.items()) if r["spEffectId0"] == 1800)
    hb = tab.hit_buildup(st, ws, tab.t.bullet[hoar]["atkId_Bullet"], hoar)[0]
    check(f"Hoarfrost bullet {hoar} own frost 1800 (+ weapon frost at scale 0)", hb.get("frost"), 70)
    buff_selftest(check)
    check("camouflage_m: Gambit 1767 hides past 17 m, Unseen Form 1467000 at any distance, its cost row 1765 none",
          [camouflage_m((1767,)), camouflage_m((1467000,)), camouflage_m((1765,))], [17.0, 0.0, None])
    # Wild Strikes plays a610_040050 (32 frames, hyperarmor from f10) before its loop 040051.
    ws_sid = tab.t.find_arts("Wild Strikes")
    ws_prof = A.skill_profile(tab.t, ws_sid, tab.wid, tab.level, False)
    check("Wild Strikes lead-in", A.lead_in(tab.t, ws_sid, ws_prof), 32.0)
    check("Wild Strikes hyperarmor start", tab.hyperarmor(ws_sid)["start"], 10)
    check("Sword Dance lead-in", A.lead_in(tab.t, tab.t.find_arts("Sword Dance"),
                                           A.skill_profile(tab.t, tab.t.find_arts("Sword Dance"), tab.wid,
                                                           tab.level, False)), 0.0)
    # Unsheathe is L2 into 040050, then R1 (cancel f8) -> 040060 hitting at clip f13, or R2
    # (cancel f6) -> 040070 hitting at f15. Both are swept, one row per button.
    tab.set_weapon("Uchigatana", "Keen")
    keep = tab.blacklist
    tab.blacklist = {tab.t.arts_name(s) for s in tab.t.arts} - {"Unsheathe"}
    un = [x for x in tab.rows([False]) if x["ash"] == "Unsheathe"]
    tab.blacklist = keep
    want = {"stance R1 040060": ("r1", 8.0, 21.0), "stance R2 040070": ("r2", 6.0, 21.0)}
    check("Unsheathe sweep rows: one per button, each with its own lead-in and first hit (8 + 13 / 6 + 15)",
          sorted((x.get("variant"), (x.get("stance"), x.get("lead"), x["first"]) == want.get(x.get("variant")))
                 for x in un), [(v, True) for v in sorted(want)])
    return 1 if fails else 0


def buff_selftest(check):
    """`BUFF_MODEL` on Heavy Stone Club, one-handed, STR 80 (1.17.1 regulation)."""
    stats = {"str": 80, "dex": 20, "int": 7, "fth": 8, "arc": 9}
    tab = Table("Stone Club", "Heavy", stats, ["Verdigris Armor"], True)
    ctx, _ = tab.contexts(False)
    slots = tab.r1_slots(False, ctx)
    r1 = next(s for s in slots if s["slot"] == "r1_1")
    check("follow_damage with no buff is the slot's own damage", round(tab.follow_damage(
        ctx, r1["attack"], A.phys_type(tab.t, tab.wid, r1["attack"]["atk_row"])), 3), round(r1["dmg"], 3))
    check("BUFF_WINDOW_S is er-mechanics-buffs.ENGAGEMENT_SECONDS", BUFF_WINDOW_S, _buffs_mod().ENGAGEMENT_SECONDS)
    chain = [s for s in slots if s["slot"] in R1_SLOTS]

    def gain(ash, end_s=2.0, first=True):
        sid = tab.t.find_arts(ash)
        prof = A.skill_profile(tab.t, sid, tab.wid, tab.level, False)
        return tab.buff_gain(sid, prof, False, ctx, [r1] if first else [], r1, end_s, chain)
    d = gain("Determination")
    # 1691 atkPlayerDmgCorrectRate 1.3 after defense; the 0.75 critical-hit row 1694 is left out.
    check("Determination: next hit, x1.3 on the follow-up", (d["kind"], d["mult"], d["hits"]), ("next hit", 1.3, 1.0))
    check("Determination: right-hand row only (1694 is critical-only)", d["rows"], [1691])
    k = gain("Royal Knight's Resolve")
    check("Royal Knight's Resolve: x1.4, so 4/3 of Determination's added HP",
          round(k["added"] / d["added"], 3), round(0.4 / 0.3, 3))
    check("Royal Knight's Resolve, no follow-up and a 12 s clock: its 10 s cover no hit",
          gain("Royal Knight's Resolve", end_s=12.0, first=False)["hits"], 0.0)
    check("Royal Knight's Resolve, no follow-up and a 2 s clock: the next landed hit",
          gain("Royal Knight's Resolve", first=False)["hits"], 1.0)
    w = gain("War Cry")
    check("War Cry: timed 40 s, the follow-up plus the R1 chain for the window's last 3 s",
          (w["kind"], w["duration_s"], w["hits"], w["window_s"]), ("timed", 40.0, 1.0, BUFF_WINDOW_S - 2.0))
    run = chain[:tab.string(0.0, chain, 0.0, 0.0)[1]]
    check("War Cry: added = follow-up gain x (1 + chain HP / follow-up HP x 3 s / chain time), within 1%",
          abs(w["added"] / (w["per_hit"] * (1 + sum(s["dmg"] for s in run) / r1["dmg"] * 3.0
                                              / (tab.string(0.0, chain, 0.0, 0.0)[3] / FPS))) - 1) < 0.01, True)
    check("War Cry with the clock past the window: the follow-up only",
          gain("War Cry", end_s=6.0)["added"], gain("War Cry", end_s=6.0)["per_hit"])
    # physicsAttackPowerRate 1.075 on the AR plus changeStrengthPoint 5: more than 1.075 on a STR
    # 80 club's AR, less than 1.075 x the AR ratio of 5 more STR scaling points would allow at most.
    check("War Cry: the STR scaling-rate add counts (mult above the bare 1.075)", w["mult"] > 1.08, True)
    check("Barbaric Roar is War Cry's rows (1681 = 1811 fields)", gain("Barbaric Roar")["added"], w["added"])
    c = gain("Cragblade")
    check("Cragblade: timed 60 s, physicsAttackPowerRate 1.15", (c["kind"], c["duration_s"], c["rows"]),
          ("timed", 60.0, [1821]))
    s = gain("Seppuku")
    check("Seppuku: bleed from atkOccurrenceSpEffectId 1756", sorted(s["status_hp"]), ["bleed"])
    cm = gain("Chilling Mist")
    check("Chilling Mist: frost from 826's atkOccurrenceSpEffectId 880, no damage",
          (sorted(cm["status_hp"]), cm["added"]), (["frost"], 0.0))
    check("Golden Vow: x1.025 PvP", gain("Golden Vow")["mult"], 1.025)
    sid = tab.t.find_arts("Flame of the Redmanes")
    check("Flame of the Redmanes puts no buff on its user",
          tab.buff_gain(sid, A.skill_profile(tab.t, sid, tab.wid, tab.level, False), False, ctx, [r1], r1, 2.0), None)
    x = {"cat": "utility", "quick": False, "string": None, "dmg": 300, "dps": 150, "score": 150, "last": 60.0,
         "status_hp": {}}
    apply_buff(x, {"added": 90.0, "status_hp": {}})
    check("apply_buff: added HP over the 2 s clock joins score", (x["score"], x["dmg"], x["buff"]["share"]),
          (195, 300, round(45 / 195, 4)))
    unapply_buff(x)
    check("unapply_buff: the row is back to its plain clock", (x["score"], x["dps"], "buff" in x), (150, 150, False))
    # War Cry's R2 (`HEAVY_SPECIAL_ANIMS` type 1): Stone Club a033_030620 released at f11, then
    # 030625's judge 3981 (MV 135) at f15 with hyperarmor f0-25; 030620 held is judge 3980 (MV 175).
    sp = tab.special_r2(1, False)
    check("special_r2: War Cry's R2 is the 030620 release at f11, then 030625's hit at f15",
          (sp["r2_1"]["lead"], sp["r2_1"]["hits"][0][0], sp["r2_1"]["hits"][0][1]["mv_phys"],
           sp["r2_1"]["windows"][0][:2]), (11.0, 26.0, 135, (11.0, 36.0)))
    check("special_r2: the charged R2 is 030620's one damaging judge (3982/3983 are MV 0)",
          [h[1]["mv_phys"] for h in sp["r2_1c"]["hits"]], [175])
    check("Barbaric Roar's R2 is another clip (type 0)", tab.special_r2(0, False)["r2_1"]["hits"][0][1]["atk_row"]
          != sp["r2_1"]["hits"][0][1]["atk_row"], True)
    check("War Cry records its R2 against the R1 chain", sorted(w["special"]), sorted(
        ["type", "r2", "r2_end", "r2_rate", "r1_rate", "plain_rate", "plain_r2_rate", "netted", "window"]))
    pr = tab.special_r2(None, False)
    check("special_r2(None): the plain R2 is 030500 released at f10, then 030505's hit at f12",
          (pr["r2_1"]["lead"], pr["r2_1"]["hits"][0][0], pr["r2_1"]["hits"][0][1]["mv_phys"]), (10.0, 22.0, 125))
    check("Golden Vow swaps no R2", "special" in gain("Golden Vow"), False)
    # The net decides the window: one that stops every hit at f26 or later leaves no R2 (its first
    # hit lands at f26), one that stops every chain without hyperarmor leaves no R1 chain.
    tab.chain_net = lambda hits, wins, end: sum(h for f, _, h in hits)
    raw = gain("War Cry")
    tab.chain_net = lambda hits, wins, end: sum(h for f, _, h in hits if f < 26)
    early = gain("War Cry")["special"]
    tab.chain_net = lambda hits, wins, end: sum(h for f, _, h in hits) if wins else 0.0
    armored = gain("War Cry")["special"]
    tab.chain_net = None
    check("chain_net: a pass-through net is the raw model", raw["added"], w["added"])
    check("chain_net: a net that stops the R2 leaves the R1 window, one that stops the R1s the R2",
          (early["netted"], early["window"], early["r2_rate"], armored["window"], armored["plain_rate"]),
          (True, "R1", 0.0, "R2", 0.0))
    # On a 2H Executioner's Greataxe the buffed R2 string outpaces the buffed R1 chain.
    tab.set_weapon("Executioner's Greataxe", "Heavy")
    ctx2, _ = tab.contexts(True)
    slots2 = tab.r1_slots(True, ctx2)
    r1b = next(s for s in slots2 if s["slot"] == "r1_1")
    sid = tab.t.find_arts("War Cry")
    g2 = tab.buff_gain(sid, A.skill_profile(tab.t, sid, tab.wid, tab.level, True), True, ctx2, [r1b], r1b, 2.0,
                       [s for s in slots2 if s["slot"] in R1_SLOTS])
    check("War Cry on a 2H Executioner's Greataxe: the window is the R2 string",
          (g2["special"]["window"], g2["special"]["r2_rate"] > g2["special"]["r1_rate"]), ("R2", True))
    tab.set_weapon("Stone Club", "Heavy")


#: What a sweep worker reads: the table and the sweep's options, set before the workers fork.
_SWEEP = {}


def _sweep_one(w):
    """(weapon, its rows, the untimed casts its rows added) for one weapon of a build sweep."""
    s, tab = _SWEEP, _SWEEP["tab"]
    affs = ["Standard"] if w in s["fixed"] else [s["affinity"]] + (variants(tab, w) if s["variants"] else [])
    if w not in s["fixed"] and w in s["status_ws"]:
        affs += [x for x in STATUS_AFFINITIES if x not in affs]
    before, rows = collections.Counter(tab.untimed), []
    for aff in affs:
        try:
            tab.set_weapon(w, aff)
            rows += tab.rows(s["grips"])
        except (KeyError, ValueError, SystemExit) as e:
            print(f"skipped {aff} {w}: {e!r}", file=sys.stderr)
    return w, rows, tab.untimed - before


def _sweep_all(todo, jobs):
    """`_sweep_one` of every weapon, in order. A weapon's rows read only its own table state, so
    `jobs` forked workers give the rows one process would, each worker on its own core."""
    if jobs <= 1 or len(todo) < 2:
        yield from map(_sweep_one, todo)
        return
    import multiprocessing as mp
    AI = _sibling("er_mechanics_ash_interrupt", "er-mechanics-ash-interrupt.py")
    cpus = AI.allowed_cpus()
    workers = cpus[1:] or cpus
    AI.throttle(cpus[:1])
    ctx = mp.get_context("fork")
    with ctx.Pool(min(jobs, len(workers)), initializer=AI._worker_init,
                  initargs=(workers, ctx.Value("i", 0))) as pool:
        yield from pool.imap(_sweep_one, todo)


def main() -> int:
    global FIGHT
    if "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("weapon", nargs="?", help="one weapon; omit with --build or --build-file")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--build", help="planner build id: every weapon in it saved with --affinity, best grip each")
    src.add_argument("--build-file", type=Path,
                     help="a planner build document (.json) read from disk instead of fetching --build, swept the "
                          "same way; an empty inventory.slots means no build weapons")
    ap.add_argument("--affinity", default="Keen")
    ap.add_argument("--grip", choices=("1H", "2H", "both"), default="1H")
    ap.add_argument("--stats", default="str=18,dex=80,int=7,fth=8,arc=9")
    ap.add_argument("--armor", default="Verdigris Gauntlets,Verdigris Armor,Verdigris Greaves",
                    help="comma-separated armor pieces worn")
    ap.add_argument("--bull-goat", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--json", type=Path, help="also write the rows here")
    ap.add_argument("--extra-weapons", default="",
                    help="comma-separated weapons swept with --affinity besides the build's (seed weapons)")
    ap.add_argument("--variants", action="store_true",
                    help=f"also sweep {', '.join(STATUS_AFFINITIES)} for weapons with an innate status, and "
                         f"{COLD_ASH_AFFINITY[1]} for weapons that mount {COLD_ASH_AFFINITY[0]}")
    ap.add_argument("--status-weapons", default="",
                    help=f"comma-separated weapons also swept with {', '.join(STATUS_AFFINITIES)} whatever "
                         "their innate status (a class role pick on a status affinity, "
                         "er-mechanics-class-roles.py main_hand/offhand)")
    ap.add_argument("--only-fixed", action="store_true",
                    help="with --build or --build-file: sweep only the weapons saved Standard (own skill or no "
                         "--affinity row)")
    ap.add_argument("--only-extra", action="store_true",
                    help="with --build or --build-file: sweep only the --extra-weapons that are not in the build")
    ap.add_argument("--jobs", type=int, default=1,
                    help="with --build or --build-file: sweep the weapons in this many forked workers, each "
                         "throttled like the interrupt pass (er-mechanics-ash-interrupt.throttle); the rows "
                         "come back in sweep order, so the table is the one a single process writes")
    ap.add_argument("--fight", choices=("duel", "invasion"),
                    help="fight model status is valued in (er-mechanics-buffs FIGHT_KINDS pressure)")
    a = ap.parse_args()
    FIGHT = a.fight
    stats = {k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))}
    armor = [n.strip() for n in a.armor.split(",") if n.strip()]
    grips = {"1H": (False,), "2H": (True,), "both": (False, True)}[a.grip]
    status_ws = {w.strip() for w in a.status_weapons.split(",") if w.strip()}
    if a.build or a.build_file:
        if a.build_file:
            doc = json.loads(a.build_file.read_text(encoding="utf-8"))
            slots = (doc.get("inventory") or {}).get("slots") or []
        else:
            req = urllib.request.Request(f"{API}/{a.build}", headers=UA)
            slots = json.load(urllib.request.urlopen(req, timeout=20))["inventory"]["slots"]
        weapons = list(dict.fromkeys(s["name"] for s in slots if (s.get("infusion") or "Standard") == a.affinity))
        if a.only_fixed or a.only_extra:
            weapons = []
        build_names = [unicodedata.normalize("NFKD", s["name"]).encode("ascii", "ignore").decode() for s in slots]
        extra = [w.strip() for w in a.extra_weapons.split(",") if w.strip()]
        if a.only_extra:
            extra = [w for w in extra if w not in build_names]
            build_names = []
        weapons += [w for w in extra if w not in weapons]
        tab = Table(weapons[0] if weapons else "Dagger", a.affinity, stats, armor, a.bull_goat)
        # Weapons that take no ashes of war, or have no --affinity row (Black Steel Twinblade,
        # Rotten Greataxe), are saved Standard and swept that way.
        fixed = []
        for w in build_names + [w for w in weapons if w not in build_names]:
            if w in fixed or (w in weapons and tab.has_affinity(w, a.affinity) and not tab.fixed_skill(w)):
                continue
            try:
                if tab.fixed_skill(w) or not tab.has_affinity(w, a.affinity):
                    fixed.append(w)
            except SystemExit:
                pass
        weapons = [w for w in weapons if w not in fixed]
        rows = []
        todo = weapons + fixed
        _SWEEP.update(tab=tab, fixed=set(fixed), status_ws=status_ws, affinity=a.affinity,
                      variants=a.variants, grips=grips)
        for i, (w, got, untimed) in enumerate(_sweep_all(todo, a.jobs), 1):
            rows += got
            if a.jobs > 1:
                tab.untimed.update(untimed)
            # `[i/N]` is the counter `er-mechanics-pipeline-watch.py` draws its bar from.
            print(f"[{i}/{len(todo)}] swept {w}: +{len(got)} rows", file=sys.stderr, flush=True)
        if tab.untimed:
            print(f"left out {sum(tab.untimed.values())} casts whose every hit is in a later clip the cast "
                  "reaches at an unmeasured time (`untimed_hits`): "
                  + ", ".join(f"{n} x{k}" for n, k in tab.untimed.most_common()), file=sys.stderr)
        title = f"{a.affinity} weapons in build {a.build or a.build_file}, {a.grip}"
    else:
        if not a.weapon:
            raise SystemExit("name a weapon or pass --build or --build-file")
        tab = Table(a.weapon, a.affinity, stats, armor, a.bull_goat)
        rows = tab.rows(grips)
        title = f"{a.affinity} {a.weapon}, {a.grip}"
    rows, dropped = drop_superseded(rows, multi=bool(a.build or a.build_file))
    # A build file may hold no weapon the sweep takes (a generated build's inventory is empty
    # until its upload): that is an empty table, written as `[]`, not a failure.
    if not rows and not a.build_file:
        raise SystemExit(f"no ash scored for {title} at {stats}")
    sub = (", ".join(f"{k.upper()} {v}" for k, v in stats.items()) + f"; armor {', '.join(armor)} "
           f"({round(tab.armor * 1000)} poise){' + Bull-Goat' + chr(39) + 's' if a.bull_goat else ''}")
    if a.json:
        a.json.write_text(json.dumps(rows))
    a.out.write_text(render(rows, title, sub, tab.blacklist, dropped))
    for c in CATEGORIES:
        best = max((x for x in rows if x["cat"] == c), key=lambda x: x["dps"], default=None)
        if best:
            print(f"{c}: {best['ash']} on {best['weapon']} {best['grip']} {best['dps']}/s, {best['dmg']} damage")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
