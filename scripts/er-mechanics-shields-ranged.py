#!/usr/bin/env python3
"""Shields and ranged weapons for the weapon plan (`er-mechanics-combo-loadout.py`).

    python3 scripts/er-mechanics-shields-ranged.py shields --build <build.json> \
        --main "Executioner's Greataxe" --main-affinity Heavy
    python3 scripts/er-mechanics-shields-ranged.py ranged --build <build.json> --r1-reach 3.6
    python3 scripts/er-mechanics-shields-ranged.py --selftest

The user asked for both on 2026-10-09 ("Now we need to figure out how shields come in and bows").
Labels follow `docs/er-mechanics/attacks.md`: `VERIFIED`, `TAE`, `MEASURED`, `INFERRED`,
`COMMUNITY`.

Shields (`ShieldModel`), one candidate per wieldable small, medium and great shield, each
affinity tried:

* Guard, `guard`: the share of the PvP corpus's opening hit the raised shield stops, over the
  RL 100-150 window's builds (`er-mechanics-powerstance-guard.blocker_corpus`, `MEASURED`). Per
  build its right-hand R1 #1 as held (`Blockers.opening_hits`) goes into
  `er-mechanics-powerstance-guard.block_hit` (`VERIFIED` formulas): value = 1 - min(1, pass +
  stamina drained / my max stamina), pass = the chip fraction of each element weighted by that
  element's share of the hit's attack (the attacker's AR at its own stats x the hit's motion
  values x its weapon's vsPlayer rate) and halved on a repel. A repel the main-hand pick's R1 #1
  lands into before the attacker can roll adds `SCORE_REPEL_PUNISH` (`repel_punishes`). This is
  `Blockers.own_guard` with the elements in: that one passes physical only. `guard_phys` keeps
  the physical-only reading beside it.
* Weight: a shield is only picked when the main-hand pick, the shield, the worn armor and the
  equipped talismans stay at medium roll or better, or no worse than without the shield
  (`er-mechanics-resources.Model.weight_type`, `VERIFIED` thresholds). Among those, the best
  `guard` of each size class is picked.
* Ash. Guard and parry ashes are scored as HP per answered corpus opening against a plain block
  of the same opening (`net_hp`), so a parry and a guard buff share one scale:
  - parry (Parry, Buckler Parry, Golden Parry, Carian Retaliation, Thops's Barrier, Storm Wall;
    any skill whose opening carries an AttackType 64 hitbox): per opening, `catch` is
    `er-mechanics-parry.Catcher` (user, 2026-10-10: "Model parrying"): the skill's hitbox and
    JumpTable 119 frames on this shield against the attacker's JumpTable 5 windows in real frames,
    half a reaction press (reaction floor, scalar timing spread) and half a read anywhere in the
    windup. The parrier only parries an opening a JumpTable 5 window guards and blocks the rest,
    so per parryable opening net = (catch x riposte HP - (1 - catch) x H) - (-(1 - g) x H + p x
    R1 HP), and 0 on the others: the parry's outcome against the block's, g the shield's
    per-opening `guard` without the punish credit, p its punish credit, H the opening's HP on me
    (my stats, worn armor and talismans, `er-mechanics-defense`), riposte HP and R1 HP the
    main-hand pick's riposte and R1 #1 on the crits reference defender. `catch` is reported over
    the parryable openings. Range (Golden Parry's larger hitbox), Carian Retaliation's spell
    return and the barriers' projectile walls are not modelled.
  - guard buff (Barricade Shield): the SpEffect the skill fires on this shield (806 small, 801
    medium, 811 great, `TAE` + regulation) raises the defender's repel by
    `guardDefFlickPowerRate` and multiplies the stamina a block costs by `guardStaminaMult`
    (field consumers `INFERRED` from their names); net = (g with the buff - g) x H + (p with the
    buff - p) x R1 HP per opening blocked while it runs (one cast lasts 10 s).
  - damaging ashes (Shield Bash, Shield Crash, ...) are valued by the plan's own machinery in
    `er-mechanics-combo-loadout.py` from an `er-mechanics-ash-table.py` sweep of the picked
    shields, cast one-handed (the left-hand cast playing the same clip is `INFERRED`).
  - Holy Ground and Vow of the Indomitable have neither a parry hitbox, a guard buff nor damage:
    no measured value, never picked.

Ranged (`RangedModel`), every wieldable light bow, bow, greatbow, crossbow and ballista:

* Shot timing (`TAE` + `c0000.behbnd` + `COMMUNITY` `c0000.hks`): a bow's R1 plays
  `AttackArrowRightStart` (036010) to its end and then `AttackArrowRightFire` (036000), whose
  BulletBehavior event fires the arrow; a crossbow or ballista plays
  `AttackCrossbowBothRightStart` (037110) then `...Fire` (037100). Each clip is read from the
  weapon's own motion category (`er-mechanics-attacks.motion_category`, the CMSG weapon-category
  offset). `startup` = start clip length + the bullet event's frame; `roll` = start clip length +
  the fire clip's roll cancel. A crossbow's reload before a second shot is not counted.
* Shot damage (`VERIFIED` attack-power builder 0x1406832a0, attacks.md section 1): the fire
  event's judge resolves against the ammunition's behavior variation (BehaviorParam_PC 105000300
  for Arrow), and each element's attack = (bow base + ammo base) x motion value x the bow's stat
  scaling, then the RL 150 reference defender (`er-mechanics-ash-table.Table`), plus the ammo's
  status as HP (`Table.hit_buildup`). `shot_dps` = (damage + status HP) / `startup` seconds, the
  sweep's clock (damage over the time to the last scored frame), so a shot and a skill compare.
  Each ammunition row's atk rows count once per shot: a spread or explosive bolt's extra bullets
  are taken to fan out rather than stack on one target (`INFERRED`, as `skill_hits` takes it).
* Ash: every mountable bow skill, its bullet events resolved against the ammunition (then the
  bow, when the ammunition has no row) and its swings (a crossbow's Kick) against the bow,
  scored like the sweep: damage over the skill's last scored frame (`dps`). A skill's status is
  not counted.
* `value` = max(`shot_dps`, best ash `dps`) x the plan's reach weight against the main-hand
  pick's R1 #1 reach (`er-mechanics-combo-loadout.REACH_GAIN_K`); the best value of each class.
* The plan (`ranged_plan`, `seal_pick`) takes only the viable picks of `data/ranged-picks.toml`
  the build wields, one greatbow, the listed copy counts, each pick's own skill or assigned ash,
  and the lightest sacred seal the build wields for the listed incantation.
* Ammunition: the planner build stores four equipped ammunition names (`items.ammo`: `arrow1`,
  `arrow2`, `bolt1`, `bolt2`, read off the build JSON); arrow slots also take great arrows and
  bolt slots greatbolts (`COMMUNITY`). So `arrow1` is the arrow worth most on the picked bow and
  light bow, `arrow2` the picked greatbow's great arrow, `bolt1` the crossbow's bolt, `bolt2` the
  ballista's greatbolt.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import struct
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
STATS = ("str", "dex", "int", "fth", "arc")
ELEMENTS = ("physical", "magic", "fire", "lightning", "holy")
VS_PLAYER = {"physical": "Physics", "magic": "Magic", "fire": "Fire", "lightning": "Thunder", "holy": "Dark"}
SHIELD_TYPES = {65: "small", 67: "medium", 69: "great"}
RANGED_TYPES = {50: "light bow", 51: "bow", 53: "greatbow", 55: "crossbow", 56: "ballista"}
#: Ammunition `wepType` (`regulation-ammo-census.py`) -> the planner slot class.
AMMO_TYPES = {81: "arrow", 83: "greatarrow", 85: "bolt", 86: "greatbolt"}
#: Sacred seal `wepType` (every row named "... Seal" in EquipParamWeapon carries it).
SEAL_TYPE = 61
#: Which ammunition each ranged class fires (`COMMUNITY`: a greatbow only great arrows, a
#: ballista only greatbolts).
AMMO_OF = {"light bow": "arrow", "bow": "arrow", "greatbow": "greatarrow", "crossbow": "bolt",
           "ballista": "greatbolt"}
#: The PvP corpus window the user measured shield and bow use in (2026-10-09: RL 100-150, 1942
#: builds).
RL_WINDOW = (100, 150)
#: Clip ids of the shot (`c0000.behbnd` CMSG animIds, `er-behbnd-tree.py --state ...`).
BOW_START, BOW_FIRE = 36010, 36000
CROSSBOW_START, CROSSBOW_FIRE = 37110, 37100
#: TimeAct event types: 1 AttackBehavior, 2 BulletBehavior.
EV_ATTACK, EV_BULLET = 1, 2
#: AttackBehavior AttackType 64 marks a parry hitbox (`er-mechanics-crits.ATTACK_TYPE_PARRY`).
ATTACK_TYPE_PARRY = 64
NPC_PREFIX = "[NPC]"
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


def guard_mod():
    return _load("er_mechanics_powerstance_guard", "er-mechanics-powerstance-guard.py")


def crits_mod():
    return _load("er_mechanics_crits", "er-mechanics-crits.py")


def table_mod():
    return _load("er_mechanics_ash_table", "er-mechanics-ash-table.py")


def parry_mod():
    return _load("er_mechanics_parry", "er-mechanics-parry.py")


def resources_mod():
    return _load("er_mechanics_resources", "er-mechanics-resources.py")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def worn(build):
    """(armor names, talisman names) the build has equipped."""
    armor = [s["name"] for p in ("head", "body", "arms", "legs")
             for s in ((build.get("protectors") or {}).get(p) or {}).get("slots", []) if s.get("equipSet")]
    tal = [s["name"] for s in (build.get("talismans") or {}).get("slots", []) if s.get("equipSet")]
    return armor, tal


def phys_affinity(stats):
    """The plain physical infusion of the build's main stat, kept on a tie of everything else."""
    return "Heavy" if stats["str"] >= stats["dex"] else "Keen"


def candidates(reg, art, stats, types):
    """[(name, base id, wepType, wieldable, affinities)] of every named base weapon of `types`.
    Ranged weapons are always two-handed, so their STR counts x1.5 (`er-mechanics-ar`)."""
    out = []
    affinities = guard_mod()._ar_module().AFFINITIES
    for wid in sorted(reg.weapon):
        name = reg.weapon_names.get(wid)
        w = art.weapons.get(wid)
        if wid % 10000 or not name or not w or name.startswith(NPC_PREFIX) or w["wepType"] not in types:
            continue
        two = w["wepType"] in RANGED_TYPES
        need = {"str": w["properStrength"], "dex": w["properAgility"], "int": w["properMagic"],
                "fth": w["properFaith"], "arc": w.get("properLuck", 0)}
        ok = all(stats[k] * (1.5 if k == "str" and two else 1.0) >= need[k] for k in need)
        affs = [a for i, a in enumerate(affinities) if wid + 100 * i in reg.weapon]
        out.append((name, wid, w["wepType"], ok, affs))
    return out


# ---------------------------------------------------------------------------------------------
# Shields


class ShieldModel:
    """Guard and guard-ash values of every wieldable shield against the corpus's opening hits.

    `main` / `main_affinity`: the main-hand pick the shield is carried with; its 1H R1 #1 is the
    repel punish and its riposte the parry's payoff."""

    def __init__(self, build, main, main_affinity, rl=RL_WINDOW, mirror=None):
        G = guard_mod()
        self.G, self.CR = G, crits_mod()
        self.ARm = G._ar_module()
        self.reg = G.Tables()
        self.art = self.ARm.Tables(None)
        self.build = build
        self.stats = {k: build["stats"][k] for k in STATS}
        self.stamina = float(build["computed"]["maxStamina"])
        self.armor, self.talismans = worn(build)
        self.main, self.main_affinity = main, main_affinity
        mwid = self.art.find_weapon(main, main_affinity)
        level = self.art.max_level(self.art.weapons[mwid]["reinforceTypeId"])
        r1 = next((r for r in G.ATK.weapon_attacks(self.reg, mwid, "one", level) if r["slot"] == "r1_1"), None)
        self.startup = (r1.get("hit_windows") or [[None]])[0][0] if r1 else None
        self.main_weight = self.art.weapons[mwid]["weight"]
        ct = self.CR.load_tables()
        self.ct = ct
        rip = self.CR.crit_damage(ct, main, "riposte", main_affinity, level, self.stats, False,
                                  defender=self.CR.reference_defender(ct))
        self.riposte_hp = rip["damage"]
        # The main-hand R1 #1 on the same defender: what a repel the R1 punishes is worth.
        self.r1_hp = 0.0
        if r1:
            ar = self.ARm.attack_rating(self.art, main, main_affinity, level, self.stats, False)
            attack = {el: ar["damage"].get(el, {}).get("total", 0.0) * r1[mv] / 100.0
                      * self.art.weapons[mwid].get(f"vsPlayerDmgCorrectRate_{VS_PLAYER[el]}", 1.0)
                      for el, _, _, mv in G.ELEMENT_CUTS}
            self.r1_hp = self.CR.DEF.damage(attack, 100.0, self.CR.reference_defender(ct),
                                            r1.get("phys_type", "standard"), G.final_rate_row(self.reg, r1))["total"]
        builds = G.blocker_corpus(mirror, *rl)
        self.n_builds = len(builds)
        self.bl = G.Blockers(self.reg, builds)
        opening = self.bl.opening_hits(builds)
        me = self.CR.DEF.defender(ct.defense, {"vig": build["stats"]["vig"], "mnd": build["stats"]["mnd"],
                                                "end": build["stats"]["vit"], **self.stats},
                                  armor=self.armor, talismans=self.talismans, pvp=True, strict=False)
        PM = parry_mod()
        ar_cache, atk_cache = {}, {}
        keys, self.uhits = {}, []
        idx, weights, hp, attacks = [], [], [], []
        for b, h in zip(builds, opening):
            if h is None:
                continue
            wid = h["weapon"]
            slot = G._active_slots(b).get(0)
            aff = self.ARm.AFFINITIES[(wid % 10000) // 100]
            base = wid - wid % 10000
            two = bool(b.get("is2h"))
            lvl = self.bl._level(wid, slot, b)
            st = {k: int((b.get("stats") or {}).get(k) or 0) for k in STATS}
            ak = (wid, lvl, two, tuple(st.values()))
            if ak not in ar_cache:
                try:
                    r = self.ARm.attack_rating(self.art, base, aff, lvl, st, two)
                    ar_cache[ak] = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
                except (KeyError, SystemExit, ValueError):
                    ar_cache[ak] = None
            ar = ar_cache[ak]
            if ar is None:
                continue
            row = self.art.weapons.get(wid) or self.art.weapons[base]
            attack = {el: ar[el] * h[mv] / 100.0 * row.get(f"vsPlayerDmgCorrectRate_{VS_PLAYER[el]}", 1.0)
                      for el, _, _, mv in G.ELEMENT_CUTS}
            total = sum(attack.values())
            if total <= 0:
                continue
            hit = dict(h)
            if hit.get("phys_type") in G.PHYS_ATTRIBUTE:
                hit["atk_attribute"] = G.PHYS_ATTRIBUTE[hit["phys_type"]]
            k = (hit.get("atk_row"), hit.get("atk_attribute"), hit["stamina_damage"], hit["guard_level_base"], wid)
            if k not in keys:
                keys[k] = len(self.uhits)
                self.uhits.append(hit)
            idx.append(keys[k])
            weights.append([attack[el] / total for el in ELEMENTS])
            dmg = self.CR.DEF.damage(attack, 100.0, me, hit.get("phys_type", "standard"),
                                     G.final_rate_row(self.reg, hit))
            hp.append(dmg["total"])
            ak = (h["tae_entry"], h["atk_row"])
            if ak not in atk_cache:
                atk_cache[ak] = PM.attack_of_row(ct, h)
            attacks.append(atk_cache[ak])
        self.idx = np.array(idx, int)
        self.w = np.array(weights, float)
        self.hp = np.array(hp, float)
        # Per opening: JumpTable 5 windows and swing starts in real frames, isDisableParry
        # (`er-mechanics-parry.attack_of_row`).
        self.attacks = attacks
        self.parryable = np.array([PM.parryable(x) for x in attacks], bool)
        self.catcher = PM.Catcher()
        self._arrays = {}
        log(f"shields: {len(idx)} of {self.n_builds} corpus openings resolved ({len(self.uhits)} distinct hits), "
            f"main {main_affinity} {main} R1 f{self.startup}, riposte {self.riposte_hp:.0f} HP")

    # -- guard ---------------------------------------------------------------------------------

    def _per_hit(self, g, repel_rate=1.0, stamina_mult=1.0):
        """Arrays over the distinct opening hits: element pass fractions, chip HP rate, stamina,
        repelled and the blocker's R1 frame after the reaction."""
        key = (g["weapon"], g["level"], repel_rate, stamina_mult)
        if key not in self._arrays:
            G = self.G
            if repel_rate != 1.0:
                g = dict(g, repel_value=int(g["repel_value"] * repel_rate))
            n = len(self.uhits)
            frac, rate, stam = np.zeros((n, len(ELEMENTS))), np.zeros(n), np.zeros(n)
            rep, ready = np.zeros(n, bool), np.zeros(n)
            for u, h in enumerate(self.uhits):
                r = G.block_hit(self.reg, h, {}, g, pvp=True)
                frac[u] = [max(0.0, r["chip_fraction"][el]) for el in ELEMENTS]
                rate[u], stam[u], rep[u] = r["chip_hp_rate"], r["stamina_to_blocker"] * stamina_mult, r["repelled"]
                ready[u] = G.blocker_ready(r["reaction"])[0]
            self._arrays[key] = (frac, rate, stam, rep, ready)
        return self._arrays[key]

    def guard(self, g, repel_rate=1.0, stamina_mult=1.0):
        """{'guard', 'guard_phys', 'base' (per opening, no punish), 'repel', 'drain'} of one
        `shield_guard` dict."""
        frac, rate, stam, rep, ready = self._per_hit(g, repel_rate, stamina_mult)
        f, r, s = frac[self.idx], rate[self.idx], stam[self.idx]
        drain = s / self.stamina
        base = 1.0 - np.minimum(1.0, (self.w * f).sum(axis=1) * r + drain)
        phys = 1.0 - np.minimum(1.0, f[:, 0] * r + drain)
        punish = np.zeros(len(self.idx))
        if self.startup is not None:
            punish = self.G.SCORE_REPEL_PUNISH * (rep[self.idx] & (ready[self.idx] + self.startup
                                                                  < self.G.REPELLED_ATTACKER_FREE))
        return {"guard": float((base + punish).mean()), "guard_phys": float((phys + punish).mean()),
                "base": np.clip(base, 0.0, 1.0), "punish": punish, "repel": float(rep[self.idx].mean()),
                "drain": float(np.minimum(1.0, drain).mean())}

    def fits(self, shield_weight):
        """(fits, load ratio with the shield): medium roll or better with the main-hand pick, the
        shield, the worn armor and the equipped talismans, or no worse than without the shield.
        Cached by weight: `shields()` asks once per affinity."""
        cache = self.__dict__.setdefault("_fits", {})
        if shield_weight not in cache:
            R = resources_mod()
            m = self.__dict__.setdefault("_resources", R.Model())
            cap = float(self.build["computed"]["maxEquipLoad"])
            without = m.burden([self.main_weight], self.armor, self.talismans)
            with_ = m.burden([self.main_weight, shield_weight], self.armor, self.talismans)
            tier = R.Model.weight_type(with_, cap)
            cache[shield_weight] = (tier <= max(2, R.Model.weight_type(without, cap)), with_ / cap)
        return cache[shield_weight]

    def shields(self):
        """Every wieldable shield, each at its best-guarding affinity (a tie keeps the build's
        physical infusion), with its guard and load."""
        out = []
        pa = phys_affinity(self.stats)
        self._affs = {}
        for name, wid, wt, ok, affs in candidates(self.reg, self.art, self.stats, SHIELD_TYPES):
            if not ok:
                continue
            self._affs[name] = (wt, affs)
            best = None
            for aff in affs:
                x = self.entry(name, wt, aff)
                key = (x["guard"], aff == pa, aff == "Standard")
                if best is None or key > best[0]:
                    best = (key, x)
            out.append(best[1])
        return out

    def entry(self, name, wt, aff):
        """One shield at one affinity: its guard against the corpus, weight and load."""
        aid = self.art.find_weapon(name, aff)
        lvl = self.art.max_level(self.art.weapons[aid]["reinforceTypeId"])
        g = self.G.shield_guard(self.reg, aid, lvl)
        v = self.guard(g)
        weight = self.art.weapons[aid]["weight"]
        fit, ratio = self.fits(weight)
        return {"weapon": name, "affinity": aff, "level": lvl, "size": SHIELD_TYPES[wt],
                "weight": weight, "fits": fit, "load": round(ratio, 3),
                "guard": round(v["guard"], 4), "guard_phys": round(v["guard_phys"], 4),
                "repel": round(v["repel"], 3), "drain": round(v["drain"], 3),
                "phys_cut": round(g["cuts"]["physical"], 1),
                "element_cut": round(sum(g["cuts"][el] for el in ELEMENTS[1:]) / 4, 1),
                "cuts": {el: round(g["cuts"][el], 1) for el in ELEMENTS},
                "_g": g, "_v": v, "_id": aid}

    def best_cut(self, size, element):
        """Every fitting `size` shield at every affinity, highest guard cut against `element`
        first (user, 2026-10-11: "for our shield with carian retaliation, lets find a small
        shield that has the highest magic guard")."""
        out = []
        for name, (wt, affs) in self._affs.items():
            if SHIELD_TYPES[wt] != size:
                continue
            for aff in affs:
                aid = self.art.find_weapon(name, aff)
                lvl = self.art.max_level(self.art.weapons[aid]["reinforceTypeId"])
                out.append((self.G.shield_guard(self.reg, aid, lvl)["cuts"][element], name, wt, aff))
        for _, name, wt, aff in sorted(out, key=lambda x: -x[0]):
            x = self.entry(name, wt, aff)
            if x["fits"]:
                yield x

    def infused_for(self, shield, element):
        """`shield` re-infused for the damage its job faces: the affinity with the highest guard
        cut against `element` (user, 2026-10-11: "If I have a shield that I want to put carian
        retaliation on for spells, then I'm going to magic infuse it so when I fail parries, I
        take less damage. Same thing with physical damage"). A tie keeps the affinity that
        guards the corpus best, so `physical` leaves a shield's `shields()` pick as it is."""
        wt, affs = self._affs[shield["weapon"]]
        best = shield
        for aff in affs:
            if aff == shield["affinity"]:
                continue
            aid = self.art.find_weapon(shield["weapon"], aff)
            lvl = self.art.max_level(self.art.weapons[aid]["reinforceTypeId"])
            if self.G.shield_guard(self.reg, aid, lvl)["cuts"][element] > best["cuts"][element]:
                best = self.entry(shield["weapon"], wt, aff)
        return best

    # -- guard and parry ashes -------------------------------------------------------------------

    def catch(self, tool):
        """Per opening, the share of parry presses that land (`er-mechanics-parry.Catcher`);
        `tool` = {'hitbox': (s, e), 'contact': (s119, e119) or None} in real frames. 0 on an
        opening no JumpTable 5 window guards."""
        return np.array([self.catcher(a, tool) if p else 0.0 for a, p in zip(self.attacks, self.parryable)])

    def ash_options(self, shield, t, A):
        """[{ash, kind, net_hp, ...}] of the guard and parry skills `shield` can carry (`t`, `A`:
        the ashes tables and module the sweep uses), best first."""
        aid = shield["_id"]
        base = aid - aid % 10000
        aff_i = self.ARm.AFFINITIES.index(shield["affinity"])
        blacklist = tomllib.loads(table_mod().BLACKLIST.read_text())
        out = []
        g, v = shield["_g"], shield["_v"]
        own = t.reg.weapon[base]["swordArtsParamId"]
        mountable = parry_mod().obtainable_skills(t)
        for sid in A.mountable_skills(t, base, aff_i, shield["level"]):
            name = t.arts_name(sid)
            # An ash no item carries (Buckler Parry's gem has sortId 999999) is only its own
            # weapon's skill.
            if name in blacklist or (sid != own and sid not in mountable):
                continue
            prof = A.skill_profile(t, sid, base, shield["level"], "one")
            acts = prof["anims"].get(A.main_anim(prof), [])
            parry = [x["frames"] for x in acts if x["kind"] == "parry"]
            flick = _speffect_field(acts, "guardDefFlickPowerRate")
            if parry:
                tool = parry_mod().tool_frames(t, sid, base, shield["level"])
                c = self.catch(tool)
                # Parry: + riposte on a catch, the whole hit on a miss; block: the chip and
                # stamina the guard lets through, plus the R1 a punishable repel lands. An opening
                # no JumpTable 5 window guards is blocked, so it nets 0.
                net = np.where(self.parryable,
                               (c * self.riposte_hp - (1.0 - c) * self.hp)
                               - (-(1.0 - v["base"]) * self.hp + v["punish"] * self.r1_hp), 0.0)
                par = self.parryable
                out.append({"ash": name, "kind": "parry", "hitbox": list(tool["hitbox"]),
                            "contact": list(tool["contact"]) if tool["contact"] else None,
                            "catch": round(float(c[par].mean()), 3) if par.any() else 0.0,
                            "parryable": round(float(par.mean()), 3), "net_hp": round(float(net.mean()), 1)})
            elif flick:
                mult = _speffect_field(acts, "guardStaminaMult") or 1.0
                vb = self.guard(g, flick, mult)
                net = (vb["base"] - v["base"]) * self.hp + (vb["punish"] - v["punish"]) * self.r1_hp
                out.append({"ash": name, "kind": "guard buff", "repel_rate": flick, "stamina_mult": mult,
                            "guard_buffed": round(vb["guard"], 4), "net_hp": round(float(net.mean()), 1)})
        return sorted(out, key=lambda x: -x["net_hp"])

    def summary(self):
        return {"builds": self.n_builds, "openings": int(len(self.idx)), "stamina": self.stamina,
                "main": self.main, "main_affinity": self.main_affinity, "main_r1": self.startup,
                "riposte_hp": round(self.riposte_hp), "r1_hp": round(self.r1_hp),
                "opening_hp_mean": round(float(self.hp.mean()), 1)}


def _speffect_field(acts, field):
    """The first value of `field` in the SpEffects (linked rows included) the actions fire."""
    def walk(s):
        if not s:
            return None
        if s["effects"].get(field):
            return s["effects"][field]
        for c in s["links"].values():
            v = walk(c)
            if v:
                return v
        return None
    for x in acts:
        v = walk(x.get("speffect"))
        if v:
            return v
    return None


def shield_picks(model, t, A):
    """{size: shield dict} of the best fitting shield per size class, each with `ashes`."""
    rows = model.shields()
    picks = {}
    for x in sorted(rows, key=lambda x: -x["guard"]):
        if x["fits"] and x["size"] not in picks:
            picks[x["size"]] = x
    for x in picks.values():
        x["ashes"] = model.ash_options(x, t, A)
    return picks, rows


#: Parry ashes whose job is spells (user, 2026-10-11, Carian Retaliation "for spells"), and the
#: element a shield carrying one is infused against. The spell return itself is not modelled
#: (module docstring); every other parry's job is the corpus's melee openings, physical.
SPELL_PARRY_ASHES = {"Carian Retaliation": "magic"}
#: The ash the block shield carries: a left-hand skill `IsShieldArts` rejects leaves L2 to the
#: right hand (`c0000.hks` `GetSwordArtInfo`), and No Skill is skill type 255, which it rejects.
NO_SKILL = "No Skill"


def per_weight(rows, size):
    """The fitting shield of `size` that blocks most per unit of weight (`guard` / weight)."""
    return max((x for x in rows if x["size"] == size and x["fits"]),
               key=lambda x: (x["guard"] / x["weight"], x["guard"]), default=None)


def shield_plan(model, t, A):
    """[{job, shield, ash, ...}]: the Shields section (user, 2026-10-11: "I would only have one
    shield, medium, that has no skill, and have it maximize block to weight ratio").

    - block: the medium shield with the best `guard` per unit of weight, with No Skill, so L2
      stays the main hand's skill;
    - one copy per parry job, melee and spells: the parry ash with the best `net_hp` on any size
      class's `per_weight` carrier; the melee copy is that carrier infused against physical
      (`infused_for`), the spell copy the shield of the carrier's size that guards the job's
      element best at any affinity and mounts the ash (`best_cut`). A guard buff's job is the block shield's and a damaging shield skill would
      take L2 from the main hand, so neither gets a copy."""
    rows = model.shields()
    plan = []
    block = per_weight(rows, "medium")
    if block:
        plan.append({"job": "block", "shield": block, "ash": NO_SKILL})
    jobs = {}
    for size in SHIELD_TYPES.values():
        carrier = per_weight(rows, size)
        if not carrier:
            continue
        for o in model.ash_options(carrier, t, A):
            if o["kind"] != "parry":
                continue
            job = "spells" if o["ash"] in SPELL_PARRY_ASHES else "melee"
            if o["net_hp"] > 0 and (job not in jobs or o["net_hp"] > jobs[job][1]["net_hp"]):
                jobs[job] = (carrier, o)
    for job in ("melee", "spells"):
        if job not in jobs:
            continue
        carrier, o = jobs[job]
        if job == "spells":
            # The spell shield is the one of the carrier's size that guards the job's element
            # best, at any affinity, among those that mount the ash.
            element = SPELL_PARRY_ASHES[o["ash"]]
            for shield in model.best_cut(carrier["size"], element):
                found = next((x for x in model.ash_options(shield, t, A) if x["ash"] == o["ash"]), None)
                if found:
                    o = found
                    break
            else:
                shield = model.infused_for(carrier, element)
        else:
            shield = model.infused_for(carrier, "physical")
        if shield is not carrier and shield["_id"] != carrier["_id"]:
            o = next(x for x in model.ash_options(shield, t, A) if x["ash"] == o["ash"])
        plan.append({"job": job, "shield": shield, "ash": o["ash"], "option": o})
    return plan, rows


# ---------------------------------------------------------------------------------------------
# Ranged


class RangedModel:
    """Shots and skills of every wieldable bow, crossbow and ballista with every ammunition."""

    def __init__(self, build, armor=None, bull_goat=True):
        T = table_mod()
        self.T, self.A = T, T.A
        self.stats = {k: build["stats"][k] for k in STATS}
        armor = armor or worn(build)[0] or ["Verdigris Gauntlets", "Verdigris Armor", "Verdigris Greaves"]
        self.tab = T.Table("Longbow", "Standard", self.stats, armor, bull_goat)
        self.t = self.tab.t
        self.ATK = sys.modules[type(self.t.reg).__module__]
        self.art = self.tab.art
        self.blacklist = self.tab.blacklist
        self.ammo = collections.defaultdict(list)
        for name, wid, wt, ok, _ in candidates(self.t.reg, self.art, self.stats, AMMO_TYPES):
            self.ammo[AMMO_TYPES[wt]].append((name, wid))
        self._ctx = {}

    def ctx(self, bow, ammo):
        """The attack-power builder's view of an arrow: the bow's and the ammunition's base
        attack summed, the bow's stat scaling (attacks.md section 1)."""
        key = (bow, ammo)
        if key not in self._ctx:
            bc = self.A.WeaponContext(bow, "Standard", stats=self.stats, two_handed=True, ar_tables=self.art)
            am = self.ARm().attack_rating(self.art, ammo, "Standard", 0, self.stats)["damage"]
            base = {el: bc.base_by[el] + am.get(el, {}).get("base", 0.0) for el in ELEMENTS}
            self._ctx[key] = SimpleNamespace(base_by=base, multiplier=bc.multiplier, base_atk_rate=bc.base_atk_rate,
                                             two_handed=True, ar_by=bc.ar_by)
        return self._ctx[key]

    def bow_ctx(self, bow):
        key = (bow, None)
        if key not in self._ctx:
            self._ctx[key] = self.A.WeaponContext(bow, "Standard", stats=self.stats, two_handed=True,
                                                  ar_tables=self.art)
        return self._ctx[key]

    def ARm(self):
        return guard_mod()._ar_module()

    def _clip(self, w, anim):
        cat = self.ATK.motion_category(w, anim, right_hand_fallback=False)
        _, _, events = self.ATK.resolve_events(cat, anim)
        return cat, events

    def _walk(self, tree, ctx, frame, out, seen):
        A = self.A
        if not tree:
            return
        if tree["atk_row"] not in seen:
            seen.add(tree["atk_row"])
            attack = A.hit_attack(ctx, tree, True)
            if sum(attack.values()) > 0:
                out.append({"kind": "bullet", "frame": frame, "atk_row": tree["atk_row"], "bullet": tree["bullet"],
                            "root_bullet": None, "attack": attack, "final_rate_id": tree.get("final_rate_id", -1),
                            "count": 1})
        for c in tree["children"].values():
            self._walk(c, ctx, frame, out, seen)

    def status_hp(self, ammo, hits):
        try:
            ST, st, _ = self.tab.status_tables()
            ws = ST.weapon_status(st, ammo, "Standard", 0, self.stats, True)
        except (KeyError, SystemExit, ValueError, TypeError):
            return {}
        build, first = {}, {}
        for h in hits:
            b, f = self.tab.hit_buildup(st, ws, h["atk_row"], h["bullet"])
            for k, v in b.items():
                build[k] = build.get(k, 0) + v
                first.setdefault(k, f[k])
        return self.tab.buildup_hp(ws, build, first)

    def shot(self, bow_name, ammo_name):
        """One R1 shot of `bow_name` loaded with `ammo_name` (module docstring), or None."""
        t, ATK, A = self.t, self.ATK, self.A
        bow, ammo = t.find_weapon(bow_name), t.find_weapon(ammo_name)
        w = t.reg.weapon[bow]
        start, fire = ((CROSSBOW_START, CROSSBOW_FIRE) if w["wepType"] in (55, 56) else (BOW_START, BOW_FIRE))
        scat, sev = self._clip(w, start)
        fcat, fev = self._clip(w, fire)
        if not sev or not fev:
            return None
        s_real = ATK.clip_to_real(sev)
        clip = ATK.clip_length(scat, start)
        start_len = ATK.real_frame(s_real(clip[0])) if clip else ATK.real_frame(
            s_real(max(e.end for e in sev if e.end < 1e5)))
        f_real = ATK.clip_to_real(fev)
        bullets = sorted((e for e in fev if e.type == EV_BULLET), key=lambda e: e.start)
        if not bullets:
            return None
        e = bullets[0]
        judge = struct.unpack_from("<i", e.params, 8)[0]
        res = A.resolve_judge(t, ammo, judge, 0, via_bullet=True)
        if res.get("kind") != "bullet" or not res.get("bullet"):
            return None
        fb = ATK.real_frame(f_real(e.start))
        rec = ATK.recovery_details(w, fire, fev, [(fb, fb)], category=fcat, to_real=f_real)
        roll = rec["cancel_frame"].get("dodge")
        hits = []
        self._walk(res["bullet"], self.ctx(bow_name, ammo_name), fb, hits, set())
        if not hits:
            return None
        dmg = A.pvp_damage(t, bow, hits, self.tab.defender, self.T.OPT.DEF)
        status = self.status_hp(ammo_name, hits)
        reach = self.tab.reach(res["bullet"]["bullet"])
        startup = round(start_len + fb, 1)
        roll_free = round(start_len + (roll if roll is not None else rec["anim_frames"] or 0), 1)
        total = dmg + sum(status.values())
        return {"ammo": ammo_name, "dmg": round(dmg), "status_hp": status, "startup": startup,
                "roll": roll_free, "reach": reach[1] if reach else None,
                "shot_dps": round(total / (startup / 30.0)) if startup else 0}

    def skills(self, bow_name, ammo_name):
        """[{ash, dmg, first, last, dps, reach}] of every mountable skill of the bow with this
        ammunition, as the sweep scores a bullet ash (damage to its last scored frame)."""
        t, A, T = self.t, self.A, self.T
        bow, ammo = t.find_weapon(bow_name), t.find_weapon(ammo_name)
        fixed = t.reg.weapon[bow].get("gemMountType") == 0
        level = self.art.max_level(self.art.weapons[self.art.find_weapon(bow_name, "Standard")]["reinforceTypeId"])
        ctx = self.ctx(bow_name, ammo_name)
        out = []
        for sid in A.mountable_skills(t, bow, 0, level):
            name = t.arts_name(sid)
            if name in self.blacklist or (not fixed and (self.tab.classes.get(sid, 0) < 2
                                                         or sid not in self.tab.obtainable)):
                continue
            try:
                anim = A.main_anim(A.skill_profile(t, sid, bow, level, "both"))
                # Bullets resolve against the ammunition (its arrow with the bow's base added),
                # a swing (a crossbow's Kick) against the bow with its own attack.
                shots = [h for h in A.skill_hits(t, ammo, sid, ctx, 0, anim=anim) if h["kind"] == "bullet"]
                if not shots:
                    shots = [h for h in A.skill_hits(t, bow, sid, ctx, level, anim=anim) if h["kind"] == "bullet"]
                swings = [h for h in A.skill_hits(t, bow, sid, self.bow_ctx(bow_name), level, anim=anim)
                          if h["kind"] != "bullet"]
                hits = shots + swings
            except (KeyError, SystemExit, ValueError, TypeError) as e:
                log(f"ranged: {bow_name} {name}: {e!r}")
                continue
            if not hits:
                continue
            dmg = A.pvp_damage(t, bow, hits, self.tab.defender, T.OPT.DEF)
            first, last = min(h["frame"] for h in hits), max(h["frame"] for h in hits)
            spans = [r for r in (self.tab.reach(h["root_bullet"]) for h in hits if h.get("root_bullet")) if r]
            out.append({"ash": name, "own": fixed, "dmg": round(dmg), "first": first, "last": last,
                        "dps": round(dmg / (last / 30.0)) if last else round(dmg),
                        "reach": max(r[1] for r in spans) if spans else None})
        return sorted(out, key=lambda x: -x["dps"])

    def bows(self, r1_reach=None, reach_k=0.10, only=None):
        """Per wieldable ranged weapon (of the names in `only`, when given): its best shot (best
        ammunition), its best skill and `value` (module docstring)."""
        out = []
        for name, wid, wt, ok, _ in candidates(self.t.reg, self.art, self.stats, RANGED_TYPES):
            if only is not None and name not in only:
                continue
            if not ok:
                log(f"ranged: {name}: not wieldable at the build's stats")
                continue
            cls = RANGED_TYPES[wt]
            shots = [s for s in (self.shot(name, a) for a, _ in self.ammo[AMMO_OF[cls]]) if s]
            if not shots:
                log(f"ranged: {name}: no shot resolved")
                continue
            best = max(shots, key=lambda s: s["shot_dps"])
            skills = self.skills(name, best["ammo"])
            top = skills[0] if skills else None
            reach = max([best["reach"] or 0.0] + [s["reach"] or 0.0 for s in skills[:1]])
            reach_w = 1 + reach_k * max(0.0, math.log(reach / r1_reach)) if r1_reach and reach else 1.0
            out.append({"weapon": name, "class": cls, "affinity": "Standard", "shot": best,
                        "shots": {s["ammo"]: s["shot_dps"] for s in shots}, "skills": skills,
                        "ash": top["ash"] if top else None, "own_skill": bool(top and top["own"]),
                        "reach_w": round(reach_w, 3),
                        "value": round(max(best["shot_dps"], top["dps"] if top else 0) * reach_w)})
        return out


def ranged_picks(rows):
    """({class: row} best value per class, {planner ammo slot: ammunition name})."""
    picks = {}
    for x in sorted(rows, key=lambda x: -x["value"]):
        picks.setdefault(x["class"], x)
    ammo = {}
    arrows = collections.Counter()
    for c in ("bow", "light bow"):
        if c in picks:
            for a, v in picks[c]["shots"].items():
                arrows[a] += v
    if arrows:
        ammo["arrow1"] = arrows.most_common(1)[0][0]
    for slot, c in (("arrow2", "greatbow"), ("bolt1", "crossbow"), ("bolt2", "ballista")):
        if c in picks:
            ammo[slot] = picks[c]["shot"]["ammo"]
    return picks, ammo


RANGED_PICKS = HERE.parent / "data/ranged-picks.toml"
#: The top-level keys of `data/ranged-picks.toml`.
PICKS_TOP_KEYS = ("viable", "excluded_ashes", "one_per_class")


def load_picks(path=RANGED_PICKS):
    """`data/ranged-picks.toml`, with the order of its keys not mattering: TOML puts a key
    written under a `[table]` or `[[array]]` header into that table, so a top-level key placed
    after `[[place]]`, `[copies]` or `[seal]` is moved back to the top level (user, 2026-10-11:
    "I'd update the toml parser so its not dependent on user ordering of their toml")."""
    cfg = tomllib.loads(Path(path).read_text())
    tables = [t for v in cfg.values() for t in (v if isinstance(v, list) else [v]) if isinstance(t, dict)]
    for t in tables:
        for k in PICKS_TOP_KEYS:
            if k in t:
                if k in cfg:
                    sys.exit(f"{path}: {k} is set twice")
                cfg[k] = t.pop(k)
    return cfg


def ranged_plan(model, r1_reach=None, reach_k=0.10, path=RANGED_PICKS):
    """([{weapon, class, ash, skill, shot, value}], ammo): one copy per viable pick of
    `data/ranged-picks.toml` the build wields, best value first. A pick with its own skill keeps
    it; one that mounts ashes takes the ash `[[place]]` assigns to its class while copies of it
    remain (best value carrier first), otherwise its best skill outside `excluded_ashes`. The
    ammunition is `ranged_picks`' over the viable picks."""
    cfg = load_picks(path)
    excluded = set(cfg.get("excluded_ashes", []))
    single = set(cfg.get("one_per_class", []))
    rows = model.bows(r1_reach, reach_k, only=set(cfg["viable"]))
    left = {(p["class"], p["ash"]): p.get("copies", 1) for p in cfg.get("place", [])}
    out, seen = [], set()
    for x in sorted(rows, key=lambda x: -x["value"]):
        if x["class"] in single and x["class"] in seen:
            log(f"ranged: {x['weapon']} dropped, one {x['class']} per build")
            continue
        seen.add(x["class"])
        skills = [s for s in x["skills"] if s["ash"] not in excluded]
        own = next((s for s in skills if s["own"]), None)
        pick = own
        if pick is None:
            placed = [s for s in skills if left.get((x["class"], s["ash"]), 0) > 0]
            pick = placed[0] if placed else (skills[0] if skills else None)
            if placed:
                left[x["class"], pick["ash"]] -= 1
        out.append({"weapon": x["weapon"], "class": x["class"], "ash": pick["ash"] if pick else None,
                    "skill": pick, "shot": x["shot"], "shots": x["shots"], "value": x["value"],
                    "reach_w": x["reach_w"], "copies": cfg.get("copies", {}).get(x["weapon"], 1)})
    for (cls, ash), n in left.items():
        if n > 0:
            log(f"ranged: no {cls} kept for the build mounts {ash}")
    _, ammo = ranged_picks([x for x in rows if x["weapon"] in {o["weapon"] for o in out}])
    return out, ammo


def seal_pick(build, path=RANGED_PICKS):
    """{weapon, spell, castable, need} or None: the `[seal]` of `data/ranged-picks.toml`, the
    lightest sacred seal the build wields, then the one needing the fewest attribute points, and
    whether the build has the spell's casting requirements (Magic `requirementFaith`,
    `requirementIntellect`, `requirementLuck`)."""
    cfg = load_picks(path).get("seal")
    if not cfg:
        return None
    ARm = guard_mod()._ar_module()
    art = ARm.Tables(None)
    st = {k: build["stats"][k] for k in STATS}
    need_of = lambda w: {"str": w["properStrength"], "dex": w["properAgility"], "int": w["properMagic"],
                         "fth": w["properFaith"], "arc": w.get("properLuck", 0)}
    seals = [(art.names[i], w) for i, w in art.weapons.items()
             if i % 10000 == 0 and w["wepType"] == SEAL_TYPE and art.names.get(i)
             and not art.names[i].startswith(NPC_PREFIX) and all(st[k] >= v for k, v in need_of(w).items())]
    if not seals:
        return None
    name, _ = min(seals, key=lambda s: (s[1]["weight"], sum(need_of(s[1]).values())))
    EPR = ARm.EPR
    rows, _, _ = EPR.rows(EPR.param_bytes(EPR.load(None), "Magic"), None)
    catalog = json.loads((Path.home() / ".cache/er-build-planner/catalog.json").read_text())["spell"]
    spell = next(r for r in rows if r["id"] == catalog[cfg["spell"].lower()])
    need = {"fth": spell["requirementFaith"], "int": spell["requirementIntellect"], "arc": spell["requirementLuck"]}
    return {"weapon": name, "spell": cfg["spell"], "need": need,
            "castable": all(st[k] >= v for k, v in need.items())}


# ---------------------------------------------------------------------------------------------
# CLI


def _clean(x):
    return {k: v for k, v in x.items() if not k.startswith("_")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", choices=("shields", "ranged"))
    ap.add_argument("--build", type=Path)
    ap.add_argument("--main", help="shields: the main-hand pick the shield is carried with")
    ap.add_argument("--main-affinity", default="Heavy")
    ap.add_argument("--r1-reach", type=float, help="ranged: the main-hand pick's R1 #1 reach in m")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.mode or not a.build:
        ap.error("mode and --build required")
    b = json.loads(a.build.read_text())
    if a.mode == "shields":
        if not a.main:
            ap.error("--main required")
        m = ShieldModel(b, a.main, a.main_affinity)
        T = table_mod()
        tab = T.Table(a.main, a.main_affinity, m.stats, worn(b)[0] or ["Verdigris Armor"], True)
        picks, rows = shield_picks(m, tab.t, T.A)
        for size in ("small", "medium", "great"):
            print(f"{size}:")
            for x in sorted((r for r in rows if r["size"] == size), key=lambda r: -r["guard"])[:6]:
                print(f"  {x['affinity']} {x['weapon']} +{x['level']}: guard {x['guard']:.3f} (phys only "
                      f"{x['guard_phys']:.3f}), repel {x['repel']:.2f}, drain {x['drain']:.2f}, cut {x['phys_cut']}/"
                      f"{x['element_cut']}, weight {x['weight']}, load {x['load']:.1%}{'' if x['fits'] else ' (heavy roll)'}")
            p = picks.get(size)
            if p:
                for o in p["ashes"]:
                    print(f"    {o['ash']}: {o['kind']}, net {o['net_hp']} HP"
                          + (f", catch {o['catch']}" if "catch" in o else f", guard buffed {o.get('guard_buffed')}"))
        plan, _ = shield_plan(m, tab.t, T.A)
        print("plan:")
        for x in plan:
            p, o = x["shield"], x.get("option")
            print(f"  {x['job']}: {p['affinity']} {p['weapon']} ({p['size']}, weight {p['weight']}) {x['ash']}: guard "
                  f"{p['guard']:.3f}, {p['guard'] / p['weight']:.3f} per weight unit, cuts {p['cuts']}"
                  + (f"; net {o['net_hp']} HP, catch {o['catch']}" if o else ""))
        out = {"summary": m.summary(), "picks": {k: _clean(v) for k, v in picks.items()},
               "plan": [dict(x, shield=_clean(x["shield"])) for x in plan],
               "shields": [_clean(x) for x in rows]}
    else:
        m = RangedModel(b)
        rows = m.bows(a.r1_reach)
        picks, ammo = ranged_picks(rows)
        for x in sorted(rows, key=lambda x: (x["class"], -x["value"])):
            s = x["shot"]
            print(f"{x['class']}: {x['weapon']} value {x['value']} (shot {s['ammo']} {s['dmg']} dmg "
                  f"{s['status_hp'] or ''} f{s['startup']} roll f{s['roll']} reach {s['reach']} -> {s['shot_dps']}/s; "
                  f"ash {x['ash']} {x['skills'][0]['dps'] if x['skills'] else '-'}/s)")
        print("picks:", {k: v["weapon"] for k, v in picks.items()}, "ammo:", ammo)
        plan, plan_ammo = ranged_plan(m, a.r1_reach)
        print("plan:")
        for x in plan:
            sk = x["skill"]
            print(f"  {x['class']}: {x['weapon']} {x['ash']} value {x['value']} (shot {x['shot']['ammo']} "
                  f"{x['shot']['shot_dps']}/s" + (f", skill {sk['dps']}/s" if sk else "") + ")")
        print("plan ammo:", plan_ammo)
        out = {"picks": picks, "ammo": ammo, "bows": rows}
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

    # The catch on one synthetic opening (`er-mechanics-parry.Catcher`, its own selftest has the
    # rule): JumpTable 5 over f2-20, first hit f20, a f4-6 hitbox, no contact route. Presses from
    # -4 to 16 catch; the read covers [0, 16] of 20 frames; a reaction at 10 with no spread
    # catches, one at 18 does not.
    PM = parry_mod()
    m = object.__new__(ShieldModel)
    m.attacks = [{"jt5": ((2, 20),), "hits": (20, 30), "disable": True}]
    m.parryable = np.array([True])
    m.catcher = PM.Catcher(react_share=0.5, delays=[(10.0, 0.5), (18.0, 0.5)], sd_frames=0.01, cv=0.0)
    check("catch, f4-6 hitbox", round(float(m.catch({"hitbox": (4, 6), "contact": None})[0]), 4), 0.65)
    m.attacks = [{"jt5": (), "hits": (20,), "disable": False}]
    m.parryable = np.array([False])
    check("no JumpTable 5: never caught", float(m.catch({"hitbox": (4, 6), "contact": None})[0]), 0.0)

    rows = [{"class": "bow", "value": 10, "shots": {"Arrow": 5, "Fire Arrow": 9}, "shot": {"ammo": "Fire Arrow"}},
            {"class": "light bow", "value": 8, "shots": {"Arrow": 6, "Fire Arrow": 1}, "shot": {"ammo": "Arrow"}},
            {"class": "greatbow", "value": 3, "shots": {}, "shot": {"ammo": "Great Arrow"}},
            {"class": "crossbow", "value": 4, "shots": {}, "shot": {"ammo": "Bolt"}}]
    picks, ammo = ranged_picks(rows)
    check("arrow1 is worth most over the bow and the light bow", ammo,
          {"arrow1": "Arrow", "arrow2": "Great Arrow", "bolt1": "Bolt"})

    # Regulation: the Barricade Shield SpEffect per size class, and a bow shot resolving through
    # the arrow's behavior variation.
    T = table_mod()
    t = T.S.LEV.Levers().t
    for sid, rate, mult in ((806, 3.0, 0.5), (801, 1.8, 0.5), (811, 1.29, 0.7)):
        e = t.speffect_summary(sid)["effects"]
        check(f"SpEffect {sid} repel x / stamina x", (e.get("guardDefFlickPowerRate"), e.get("guardStaminaMult")),
              (rate, mult))
    res = T.A.resolve_judge(t, t.find_weapon("Arrow"), 300, 0, via_bullet=True)
    check("Arrow fire judge 300 -> BehaviorParam_PC", (res["behavior_row"], res["kind"]), (105000300, "bullet"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
