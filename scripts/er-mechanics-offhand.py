#!/usr/bin/env python3
"""Left weapons ranked as off-hands: the off-hand L1 scored as a move of its own.

    python3 scripts/er-mechanics-offhand.py                      # every class, axes first (~5 min)
    python3 scripts/er-mechanics-offhand.py --class axe --top 10
    python3 scripts/er-mechanics-offhand.py --json > offhand.json
    python3 scripts/er-mechanics-offhand.py --selftest

Write-up: `docs/er-mechanics/combo.md` section 9. Labels as the sibling docs use them.

`er-mechanics-combo.py --sweep` orders pairs by one guaranteed follow-up's damage at one stat line,
so it can only say which heavy L1 lands after a colossal sword's rolling R1. This module scores the
left weapon itself, one row per base melee weapon whose L1 is an off-hand attack, from the RL
window's PvP corpus (`er-builds-pvp.pvp_corpus` filter, deduplicated on user and equipped tokens):

* Frames (`TAE`, `er-mechanics-combo.offhand_attacks`): startup = the L1 #1's first active frame,
  `next` = the frame L1 #2 can start, `roll` = the first roll frame, and the L1 #1 -> #2 link
  (`er-mechanics-combo.Model.link`: gap against the escape of the stagger it plays).
* Damage and poise (`er-builds-pvp.slot_hit` on the corpus defenders), Standard at max upgrade, at
  the median damage stats of the corpus builds that meet the weapon's requirements (`typical`);
  `eligible` is that share of the corpus. Stagger = share of the corpus whose poise it breaks.
* Stamina (`er-mechanics-exchange.slot_stamina`): the cost and `f_stamina`, the share of a
  fight window the L1 can keep swinging from the median bar.
* Reach (`er-mechanics-reach.attack_reach`, the L1 clip's own pose): `contact_centre_m`.
* Weight: `fit` = share of corpus builds that stay at medium roll at their own Endurance with this
  weapon in place of their heaviest left-hand item (`er-builds-optimize.end_for_load`, each build's
  own load, rate and Endurance bonus, `MEASURED`).
* Links: every right-hand weapon the corpus carries, weighted by how many builds carry it. With
  each, L1 is `dual` (powerstance, `er-mechanics-combo.left_mode`) or an off-hand attack; for the
  off-hand ones the best right-hand opener -> L1 #1 link is taken by landing chance (stagger share
  x `on_break` chance, 1 true, 0.5 tie, `er-mechanics-combo.roll_out_p` roll-out-able). `p_link`
  is the corpus-weighted mean of that chance, `true_share` the weighted share with a true link.

Score (`INFERRED` weights, each factor printed):

    score = slot_score(L1 as an opener) x fit x (1 + p_link x dmg / FIGHT_REF_DAMAGE)

`slot_score` is `er-builds-pvp`'s (damage per committed frame, reach, frame advantage, stagger,
startup and stamina through `f_exchange`/`f_stamina`); the link factor adds the follow-up's
expected damage to an engagement whose opener deals the status model's reference hit
(`er-mechanics-status.FIGHT_REF_DAMAGE`, the median best-slot damage).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PVP = _load("er-builds-pvp")
COMBO = _load("er-mechanics-combo")
OPT, EMBED, AR, ATK, EXCH, FA = PVP.OPT, PVP.OPT.EMBED, PVP.AR, PVP.ATK, PVP.EXCH, COMBO.FA
STATUS = _load("er-mechanics-status")
ASH = _load("er-mechanics-ashes")
CACHE = Path.home() / ".cache/er-build-planner"
DAMAGE_STATS = ("str", "dex", "int", "fth", "arc")
LEFT_HAND, RIGHT_HAND = (3, 4, 5), (0, 1, 2)
#: `wepType` of the classes `--class` names (`MEASURED` from the regulation rows of Battle Axe,
#: Greataxe and the rest; the labels are the in-game class names).
CLASSES = {"axe": (17,), "greataxe": (19,)}


# --------------------------------------------------------------------------------------------
# corpus


def corpus(mirror: Path, lo: int, hi: int) -> list[dict]:
    """PvP builds of RL `lo`..`hi`, one per (user, equipped tokens): damage stats, Endurance,
    right-hand and left-hand weapon names (plain), and the load numbers the fit test needs."""
    weapon_w, _, plain = OPT._weights()
    out, seen = [], set()
    for line in mirror.open():
        row = json.loads(line)
        b = row["build"]
        if not PVP.is_pvp(b):
            continue
        st = EMBED.stats_of(b)
        if st is None or not lo <= st["rl"] <= hi:
            continue
        key = (row.get("user"), tuple(EMBED.tokens(b)))
        if key in seen:
            continue
        seen.add(key)
        active = EMBED.active_set(b, "weapons")
        right, left = [], []
        for s in (b.get("inventory") or {}).get("slots") or []:
            es = s.get("equipSet")
            pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get("equipIndex")
            if pos is None or not s.get("name"):
                continue
            name = plain(s["name"])
            (right if pos in RIGHT_HAND else left if pos in LEFT_HAND else []).append(name)
        prof = OPT.load_profile(b)
        rw = [weapon_w.get(n) for n in right]
        lw = [weapon_w.get(n) for n in left]
        load = None
        if prof is not None and None not in rw and None not in lw:
            load = {**prof, "total": prof["rest"] + (max(rw) if rw else 0.0), "left_max": max(lw, default=0.0)}
        out.append({"stats": {k: st[k] for k in DAMAGE_STATS}, "end": st["vit"], "right": right, "left": left,
                    "load": load, "cls": b.get("characterClass")})
    return out


def fit_share(builds: list[dict], weight: float) -> float | None:
    """Share of builds with a known load that stay at medium roll at their own Endurance with
    `weight` in place of their heaviest left-hand item."""
    known = [b for b in builds if b["load"]]
    if not known:
        return None
    ok = 0
    for b in known:
        L = b["load"]
        need = OPT.end_for_load(L["total"] - L["left_max"] + weight, L["rate"], L["end_bonus"])
        ok += need <= b["end"]
    return ok / len(known)


# --------------------------------------------------------------------------------------------
# scoring


class Scorer:
    def __init__(self, rl: int = 150, window: int = 10, mirror: Path = CACHE / "builds.jsonl"):
        self.model = COMBO.Model(rl=rl, window=window, mirror=mirror)
        self.reg = self.model.reg
        self.tables = AR.Tables(None)
        self.pvp_t = PVP.PvpTables()
        self.defenders = PVP.Defenders(PVP.pvp_corpus(mirror, rl - window, rl + window))
        self.poises = self.defenders.poise
        self.builds = corpus(mirror, rl - window, rl + window)
        self.pool = EXCH.Pool(EXCH.opponent_pool(self.reg, mirror, rl - window, rl + window))
        self.names = {}
        for wid in COMBO.base_weapons(self.reg):
            self.names.setdefault(self.reg.weapon_names[wid], wid)
        self.right_freq = Counter()
        for b in self.builds:
            for n in set(b["right"]):
                if n in self.names:
                    self.right_freq[self.names[n]] += 1
        self._rc = None

    def candidates(self) -> list[int]:
        out = []
        for wid in self.names.values():
            cat = self.reg.weapon[wid]["wepmotionCategory"]
            if cat in COMBO.LEFT_NO_ATTACK or cat in COMBO.psg().GUARD_LEFT_ONE_HAND:
                continue
            if self.model.offhand(wid).get("left_1"):
                out.append(wid)
        return out

    def requirement(self, wid: int) -> dict:
        w = self.tables.weapons[wid]
        return {"str": w["properStrength"], "dex": w["properAgility"], "int": w["properMagic"],
                "fth": w["properFaith"], "arc": w["properLuck"]}

    def requirement_cost(self, wid: int, builds: list[dict] | None = None) -> dict:
        """Levels a weapon's requirements cost: per build, the points short in each stat
        (`requirement`, EquipParamWeapon `proper*`, one-handed), summed. Over `builds` (default the
        corpus): the share needing none, the mean levels, the mean among those who need some, and
        the starting classes' (`CharaInitParam` 3000-3009 base stats) shortfall at level 1."""
        need = self.requirement(wid)
        pool = builds if builds is not None else self.builds
        lv = [sum(max(0, need[k] - b["stats"][k]) for k in DAMAGE_STATS) for b in pool]
        short = [x for x in lv if x]
        return {"need": {k: v for k, v in need.items() if v}, "eligible": sum(1 for x in lv if not x) / len(lv),
                "mean_levels": statistics.fmean(lv), "mean_levels_if_short": statistics.fmean(short) if short else 0.0,
                "classes": {c: sum(max(0, need[k] - base[k]) for k in DAMAGE_STATS) for c, base in self.classes().items()}}

    def classes(self) -> dict:
        """{starting class: base damage stats} from `CharaInitParam` 3000-3009 (`VERIFIED`)."""
        if "_classes" not in self.__dict__:
            files = ATK.PR.load(None)
            rows, _, _ = ATK.PR.rows(ATK.PR.param_bytes(files, "CharaInitParam"), None)
            names = ATK.PR.row_names("CharaInitParam")
            field = {"str": "baseStr", "dex": "baseDex", "int": "baseMag", "fth": "baseFai", "arc": "baseLuc"}
            self._classes = {names[r["id"]].removeprefix("Class - "): {k: r[f] for k, f in field.items()}
                             for r in rows if 3000 <= r["id"] <= 3009}
        return self._classes

    def typical_stats(self, wid: int) -> tuple[dict, float]:
        need = self.requirement(wid)
        ok = [b["stats"] for b in self.builds if all(b["stats"][k] >= need[k] for k in DAMAGE_STATS)]
        pool = ok or [b["stats"] for b in self.builds]
        return ({k: int(statistics.median(s[k] for s in pool)) for k in DAMAGE_STATS},
                len(ok) / len(self.builds) if self.builds else 0.0)

    def reach(self, wid: int, row: dict) -> float | None:
        R = COMBO.reach_module()
        if self._rc is None:
            self._rc = R.Reach()
        cat, anim = COMBO._clip_ref(row)
        try:
            r = R.attack_reach(self._rc, wid, row["slot"], row["label"], row["judge"], anim, "one", clip=(cat, anim))
        except (KeyError, ValueError, IndexError, TypeError):
            return None
        return (r or {}).get("contact_centre_m")

    def links(self, left: int) -> dict:
        """Corpus-weighted link numbers of `left` across the right-hand weapons the corpus carries."""
        tot = dual = off = p_sum = true_w = rollable_w = 0.0
        best_right = []
        for right, f in self.right_freq.items():
            if not self.model.rows(right):
                continue
            mode = COMBO.left_mode(self.reg, right, left)
            tot += f
            if mode == "dual":
                dual += f
                continue
            if mode != "offhand":
                continue
            off += f
            best, has_true, has_roll = 0.0, False, False
            for lk in self.model.cross_links(right, left):
                if lk["next"] != "left_1":
                    continue
                stag = lk.get("stagger") or 0.0
                side = {}
                for s in ("on_break", "on_intact"):
                    v = lk[s].get("verdict")
                    side[s] = (COMBO.roll_out_p(lk["lead_in"], lk["next_first_hit"]) if v == "roll-out-able"
                               else COMBO.VERDICT_P.get(v, 0.0))
                p = (1.0 - stag) * side["on_intact"] + stag * side["on_break"]
                best = max(best, p)
                has_true |= lk["on_break"].get("verdict") == "true" and stag > 0
                has_roll |= lk["on_break"].get("verdict") == "roll-out-able" and stag > 0
            p_sum += f * best
            true_w += f * has_true
            rollable_w += f * (has_roll and not has_true)
            best_right.append((f * best, self.reg.weapon_names[right]))
        best_right.sort(reverse=True)
        return {"right_weight": tot, "dual_share": dual / tot if tot else 0.0,
                "offhand_share": off / tot if tot else 0.0,
                "p_link": p_sum / tot if tot else 0.0, "true_share": true_w / tot if tot else 0.0,
                "rollable_only_share": rollable_w / tot if tot else 0.0,
                "top_rights": [n for _, n in best_right[:3]]}

    def affinities(self, wid: int) -> list[str]:
        """The affinities `wid` exists in (a unique weapon has Standard only)."""
        return [a for i, a in enumerate(AR.AFFINITIES) if wid + i * 100 in self.tables.weapons]

    def affinity_mix(self, wid: int, best: bool) -> list[tuple[float, str, dict]]:
        """[(share, affinity, stats)]: Standard at the eligible builds' median stats, or with `best`
        the eligible builds split by their highest damage stat, each group at its median stats and
        the affinity whose L1 #1 deals the most there (the choice a build of that kind makes)."""
        need = self.requirement(wid)
        ok = [b["stats"] for b in self.builds if all(b["stats"][k] >= need[k] for k in DAMAGE_STATS)]
        pool = ok or [b["stats"] for b in self.builds]
        if not best:
            return [(1.0, "Standard", {k: int(statistics.median(s[k] for s in pool)) for k in DAMAGE_STATS})]
        groups: dict = {}
        for s in pool:
            groups.setdefault(max(DAMAGE_STATS, key=lambda k: s[k]), []).append(s)
        l1 = self.model.offhand(wid)["left_1"]
        out = []
        for g in groups.values():
            stats = {k: int(statistics.median(s[k] for s in g)) for k in DAMAGE_STATS}
            aff = max(self.affinities(wid), key=lambda a: self.hit(wid, a, stats, l1)["dmg"])
            out.append((len(g) / len(pool), aff, stats))
        return out

    def hit(self, wid: int, aff: str, stats: dict, row: dict) -> dict:
        key = (wid, aff, tuple(stats.values()), row["slot"])
        cache = self.__dict__.setdefault("_hits", {})
        if key not in cache:
            aid = wid + AR.AFFINITIES.index(aff) * 100
            level = self.tables.max_level(self.tables.weapons[aid]["reinforceTypeId"])
            r = AR.attack_rating(self.tables, self.reg.weapon_names[wid], aff, level, stats, False)
            ar_by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in PVP.ELEMENTS}
            h = PVP.slot_hit(self.pvp_t, self.reg, wid, row, ar_by, self.defenders)
            h.pop("guard_part")
            cache[key] = h
        return cache[key]

    def mixed_hit(self, wid: int, mix, row: dict) -> dict:
        """`hit` averaged over `mix` for damage; frames, poise and stamina are the row's own."""
        hs = [(w, self.hit(wid, a, s, row)) for w, a, s in mix]
        h = dict(hs[0][1])
        h["dmg"] = sum(w * x["dmg"] for w, x in hs)
        return h

    def counter_before(self, frames: float, react: bool) -> float:
        """Share of (corpus build, reaction delay) pairs whose own R1 #1 lands within `frames`
        frames: the victim's fastest counter (`er-mechanics-exchange` pool startups), started after
        his reaction plus two network legs when `react`, else at once (a buffered counter)."""
        starts = self.pool.startup[self.pool.build_prof]
        delays = COMBO._ASHES.reaction_delays() if react else [(0.0, 1.0)]
        return sum(w * float((starts + d < frames).mean()) for d, w in delays)

    def chain(self, wid: int, mix, roll_catch: bool = False) -> dict | None:
        """The off-hand L1 chain thrown on its own (combo.md section 10): hits L1 #1..#n, each
        started at the previous one's `l1_start`, while the stamina bar lasts. Each gap continues
        with chance `q` = stagger share x (the break verdict's chance x no counter out of the
        stagger) + (1 - stagger share) x no reactive counter, x 1 when the pushed victim is still
        inside the next hit's contact from body range (`er-mechanics-combo.reach_check`). The
        attacker throws hit k+1 only when hit k landed; when k+1 is escaped it is his whiff, and the
        engagement ends at its roll frame."""
        rows = self.model.offhand(wid)
        seq = []
        for key, *_ in COMBO.OFFHAND:
            if key not in rows:
                break
            seq.append(rows[key])
        if not seq:
            return None
        COMBO.roll_out_p(0.0, 10)  # loads the reaction model
        hits = [self.mixed_hit(wid, mix, r) for r in seq]
        bar, spent, n = self.pool.bar, 0.0, 0
        for h in hits:
            if spent + (h["stamina"] or 0) > bar and n:
                break
            spent += h["stamina"] or 0
            n += 1
        start, starts = 0.0, [0.0]
        gaps, qs, verdicts, reaches = [], [], [], []
        for k in range(n - 1):
            a, b = seq[k], seq[k + 1]
            s = a.get("l1_start")
            if s is None:
                n = k + 1
                break
            lk = self.model.link(a, b, s, 0.0, "l1")
            stag = sum(p < hits[k]["poise"] for p in self.poises) / len(self.poises) if self.poises else 0.0
            brk = lk["on_break"]
            v = brk.get("verdict")
            p_brk = {"true": 1.0, "tie": 0.5}.get(v, COMBO.roll_out_p(0.0, b["hit_windows"][0][0])
                                                  if v == "roll-out-able" else 0.0)
            if brk.get("escape"):
                # A counter out of the stagger: the attack gate one frame before the roll gate
                # (combo.md section 0, middle stagger 24 vs 25), pressed at once (`INFERRED`).
                p_brk *= 1.0 - self.counter_before(lk["gap"] - (brk["escape"] - 1), react=False)
            p_int = 1.0 - self.counter_before(lk["gap"], react=True)
            q = stag * p_brk + (1.0 - stag) * p_int
            try:
                rc = COMBO.reach_check(self.model, wid, wid, lk)
            except (OSError, KeyError, ValueError, IndexError, TypeError):
                rc = {"error": "no clip"}
            # No geometry (an HKX clip that is not unpacked): contact is assumed kept.
            body = (rc.get("body") or {}).get("reaches", True) if "error" not in rc else True
            q *= 1.0 if body else 0.0
            start += s
            starts.append(start)
            gaps.append(lk["gap"])
            qs.append(q)
            verdicts.append(v)
            reaches.append(body)
        seq, hits = seq[:n], hits[:n]
        land, dmg, poise, commit = 1.0, 0.0, 0.0, 0.0
        for k in range(n):
            dmg += land * hits[k]["dmg"]
            poise += land * hits[k]["poise"]
            if k == n - 1:
                commit += land * (starts[k] + (hits[k]["roll"] or hits[k]["next"] or 0.0))
            else:
                nxt = hits[k + 1]
                commit += land * (1.0 - qs[k]) * (starts[k + 1] + (nxt["roll"] or nxt["next"] or 0.0))
                land *= qs[k]
        # Roll-catch (section 10b): the waiting share of defenders (`er-mechanics-ashes.REACT_SHARE`)
        # rolls L1 #1. A caught roll is taken to start the same chain again from the catching hit,
        # and the attacker to throw the whole chain meanwhile (both `INFERRED`).
        rc = self.roll_catch(wid, seq, n) if roll_catch else None
        if rc:
            r = ASH.REACT_SHARE * rc["evade_l1"]
            full = starts[n - 1] + (hits[n - 1]["roll"] or hits[n - 1]["next"] or 0.0)
            dmg_c, poise_c = dmg, poise
            dmg = (1.0 - r) * dmg_c + r * rc["p_catch_any"] * dmg_c
            poise = (1.0 - r) * poise_c + r * rc["p_catch_any"] * poise_c
            commit = (1.0 - r) * commit + r * full
        last = seq[n - 1]
        adv = {}
        for key, broken in (("adv", False), ("adv_stagger", True)):
            react = self.model.react(FA.reaction_level(self.model.fa, last["atk_row"], broken))
            a = FA.advantage(last, react) if react else None
            adv[key] = a["advantage"] if a else None
        return {"hits": n, "stamina_hits": n, "gaps": gaps, "q": [round(x, 3) for x in qs], "verdicts": verdicts,
                "reaches": reaches, "dmg": dmg, "poise": poise, "commit": commit,
                "dps": dmg / commit * 30.0 if commit else None, "poise_ps": poise / commit * 30.0 if commit else None,
                "mean_gap": statistics.fmean(gaps) if gaps else None,
                "whiff_recovery": (hits[0]["roll"] or 0.0) - (hits[0]["startup"] or 0.0), "adv": adv,
                "roll_catch": rc}

    def _geometry(self, wid: int, row: dict) -> dict | None:
        """L1 row geometry (`er-mechanics-reach`): `contact` (m from the clip's start position),
        `lunge` (root motion to the hit) and `moved(t)` (forward root motion at real frame `t`,
        `INFERRED` no play-speed window, as `er-mechanics-combo.reach_check`)."""
        cache = self.__dict__.setdefault("_geo", {})
        key = (wid, row["slot"])
        if key in cache:
            return cache[key]
        R = COMBO.reach_module()
        pose = R._pose_module()
        if self._rc is None:
            self._rc = R.Reach()
        cat, anim = COMBO._clip_ref(row)
        out = None
        try:
            r = R.attack_reach(self._rc, wid, row["slot"], row["label"], row["judge"], anim, "one", clip=(cat, anim))
            if pose is not None and r and r.get("contact_centre_m") is not None:
                hc, ha = R.hkx_source(cat, anim)
                pose.root_motion(hc, ha, 0.0)  # an unpacked clip is required; raises when absent
                arc = r.get("coverage_arc_eff_deg")
                out = {"contact": r["contact_centre_m"], "lunge": r.get("root_motion_to_hit_m") or 0.0,
                       "half_arc": 180.0 if arc is None else arc / 2.0,
                       "moved": lambda t, hc=hc, ha=ha: -pose.root_motion(hc, ha, max(0.0, t) / 30.0)[2]}
        except (KeyError, ValueError, IndexError, TypeError, OSError):
            out = None
        cache[key] = out
        return out

    def roll_catch(self, wid: int, seq: list, n: int) -> dict | None:
        """A defender who rolls L1 #1 of the chain (combo.md section 10b). He starts each roll at
        a roll start `rs` from the L1 clip becoming visible: the reaction model's nine delays
        (`er-mechanics-ashes.reaction_delays`) plus a buffered roll on frame 0, equal weight
        (`INFERRED`). The medium roll (`er-mechanics-disengage.tool`): i-frames 0-13, the next roll
        on 21, 12 stamina, travel away from the attacker. Hit k+1 catches him when its active
        window overlaps his recovery [roll + i-frames, next roll) and he is inside its contact.
        Otherwise he rolls again on reaction to hit k+1 (never before the chain gate), until he
        is out of range, the chain ends, or his stamina bar is spent. He starts at body range: the
        L1 #1 lunge plus two idle front radii (the user's "still close")."""
        D = COMBO._load("er_mechanics_disengage", "er-mechanics-disengage.py") \
            if "_dis" not in self.__dict__ else self._dis
        self._dis = D
        roll = D.tool("roll medium")
        geo = [self._geometry(wid, r) for r in seq[:n]]
        if roll is None or None in geo or n < 2:
            return None
        R = COMBO.reach_module()
        front = (R.defender_hurtbox("idle") or {"front_m": 0.3})["front_m"]
        COMBO.roll_out_p(0.0, 10)
        starts_rs = [(0.0, 0.1)] + [(d, 0.9 * w) for d, w in COMBO._ASHES.reaction_delays()]
        ifr, gate, cost = roll["iframes"], roll["chain"], 12
        away = roll["away"]

        def away_at(t):
            return away[min(max(int(t), 0), len(away) - 1)]
        # Clip k's start, attacker position and hit window, on the chain's clock.
        t0, pos, clips = 0.0, 0.0, []
        for k in range(n):
            h0, h1 = seq[k]["hit_windows"][0]
            clips.append({"start": t0, "pos": pos, "hit": (t0 + h0, t0 + h1), "geo": geo[k]})
            s = seq[k].get("l1_start")
            if s is None:
                break
            pos += geo[k]["moved"](s)
            t0 += s
        d0 = geo[0]["lunge"] + 2 * front
        bar = self.pool.bar
        caught_first, caught_any, rolls_escape, w_esc = 0.0, 0.0, 0.0, 0.0
        per_dir = {}
        # Roll direction relative to the attacker, equal weight (`INFERRED`): away, to the side,
        # toward (through him). The attacker's forward travel is taken to track the defender, so it
        # shortens the straight-line distance (`INFERRED`).
        dirs = {"away": (1.0, 0.0), "side": (0.0, 1.0), "toward": (-1.0, 0.0)}
        for (rs0, w0), (dname, (ux, uy)) in ((x, y) for x in starts_rs for y in dirs.items()):
            w = w0 / len(dirs)
            h0, h1 = clips[0]["hit"]
            if not (rs0 <= h0 and rs0 + ifr >= h1):
                continue  # this roll does not evade L1 #1 (too late, or i-frames over before it)
            rolls, rs, dpos, stam = 1, rs0, 0.0, bar - cost
            result = None

            def where(travel, c):
                # A roll toward the attacker stops at body contact, two idle front radii in front
                # of him: characters collide, so it cannot pass through (`INFERRED`).
                x = d0 + ux * travel
                if ux < 0 and uy == 0:
                    x = max(x, c["pos"] + 2 * front)
                return x, uy * travel

            def distance(travel, c):
                return math.hypot(*where(travel, c)) - c["pos"]

            def in_arc(travel, c):
                # The defender's bearing from the attacker's facing at the hit, against half the
                # hit's effective arc (`er-mechanics-reach.coverage_factor` arc_eff: the swept
                # footprint plus the late turn on either side). A side roll leaves the line the
                # straight-line distance alone measures.
                x, y = where(travel, c)
                return math.degrees(math.atan2(abs(y), x - c["pos"])) <= c["geo"]["half_arc"]
            for k in range(1, len(clips)):
                c = clips[k]
                a, b = c["hit"]
                nxt = max(rs + gate, c["start"] + (rs0 if rs0 > 0 else 0.0))
                vuln = (rs + ifr, nxt)
                travel = dpos + away_at(a - rs)
                inside = distance(travel, c) <= c["geo"]["contact"]
                # In range and open but beside or behind the swing, this hit misses him and he rolls
                # again on the next one, as below.
                if inside and in_arc(travel, c) and a < vuln[1] and b > vuln[0]:
                    result = ("caught", k)
                    break
                if not inside:
                    # Out of this hit's contact: the attacker gains at most one L1's lunge per
                    # hit against a roll's 3 m, so he is taken as gone (`INFERRED`).
                    result = ("escaped", rolls)
                    break
                if stam <= 0:
                    result = ("caught", k)
                    break
                # He rolls again when hit k+1 shows, not before the chain gate.
                dpos += away_at(nxt - rs)
                rs = nxt
                rolls += 1
                stam -= cost
            if result is None:
                result = ("escaped", rolls)
            d = per_dir.setdefault(dname, [0.0, 0.0])
            d[1] += w
            if result[0] == "caught":
                caught_any += w
                caught_first += w if result[1] == 1 else 0.0
                d[0] += w
            else:
                rolls_escape += w * result[1]
                w_esc += w
        evaders = sum(w for rs0, w in starts_rs
                      if rs0 <= clips[0]["hit"][0] and rs0 + ifr >= clips[0]["hit"][1])
        return {"evade_l1": evaders, "p_catch_next": caught_first / evaders if evaders else 0.0,
                "p_catch_any": caught_any / evaders if evaders else 0.0,
                "by_direction": {k: round(v[0] / v[1], 3) if v[1] else None for k, v in per_dir.items()},
                "rolls_to_escape": rolls_escape / w_esc if w_esc else None, "start_m": round(d0, 2)}

    def score(self, wid: int, with_links: bool = True, chain: bool = False, best_aff: bool = False,
              roll_catch: bool = False) -> dict | None:
        rows = self.model.offhand(wid)
        l1 = rows.get("left_1")
        if l1 is None:
            return None
        stats, eligible = self.typical_stats(wid)
        name = self.reg.weapon_names[wid]
        mix = self.affinity_mix(wid, best_aff)
        hit = self.mixed_hit(wid, mix, l1)
        hit["next"] = l1.get("l1_start")
        hit["stagger"] = sum(p < hit["poise"] for p in self.poises) / len(self.poises) if self.poises else 0.0
        adv = {}
        for key, broken in (("adv", False), ("adv_stagger", True)):
            react = self.model.react(FA.reaction_level(self.model.fa, l1["atk_row"], broken))
            a = FA.advantage(l1, react) if react else None
            adv[key] = a["advantage"] if a else None
        slot = {**hit, **adv, "reach": self.reach(wid, l1), "parryable": False, "status": {}}
        try:
            slot["exchange"] = EXCH.slot_exchange(self.pool, self.reg, wid, l1, slot, 0.0)
        except (KeyError, TypeError, ValueError):
            slot["exchange"] = None
        sc = PVP.slot_score(slot, 0.0)
        pressure = None
        if chain:
            pressure = self.chain(wid, mix, roll_catch)
            if pressure and pressure["commit"]:
                # The chain as one engagement: its expected damage over its expected commitment,
                # the last thrown hit's frame advantage; reach, contest and stamina as L1 #1's.
                syn = {**slot, "dmg": pressure["dmg"], "roll": pressure["commit"], "next": None,
                       "adv": pressure["adv"]["adv"], "adv_stagger": pressure["adv"]["adv_stagger"]}
                sc = PVP.slot_score(syn, 0.0)
        chain = None
        if rows.get("left_2"):
            lk = self.model.link(l1, rows["left_2"], l1.get("l1_start"), 0.0, "l1")
            if lk:
                chain = {"gap": lk["gap"], "escape": lk["on_break"].get("escape"),
                         "verdict": lk["on_break"].get("verdict")}
        weight = self.tables.weapons[wid]["weight"]
        fit = fit_share(self.builds, weight)
        links = self.links(wid) if with_links else None
        f_link = 1.0 + (links["p_link"] if links else 0.0) * hit["dmg"] / STATUS.FIGHT_REF_DAMAGE
        total = (sc["score"] if sc else 0.0) * (fit if fit is not None else 1.0) * f_link
        ex = slot["exchange"] or {}
        return {"weapon": name, "id": wid, "wep_type": self.reg.weapon[wid]["wepType"], "anim": l1["anim"],
                "stats": stats, "eligible": round(eligible, 3), "startup": hit["startup"], "next": hit["next"],
                "roll": hit["roll"], "chain": chain, "dmg": round(hit["dmg"], 1), "poise": round(hit["poise"], 1),
                "stagger": round(hit["stagger"], 3), "stamina": hit["stamina"],
                "f_stamina": ex.get("f_stamina"), "f_exchange": ex.get("f_exchange"),
                "reach": slot["reach"], "weight": weight, "fit": fit, "links": links,
                "slot_score": sc["score"] if sc else None, "commit": sc["commit"] if sc else None,
                "f_adv": sc and sc["f_adv"], "f_reach": sc and sc["f_reach"], "f_stag": sc and sc["f_stagger"],
                "f_link": f_link, "score": total, "pressure": pressure,
                "affinity_mix": [(round(w, 3), a) for w, a, _ in mix], "affinities": len(self.affinities(wid)),
                "ashes": self.ash_count(wid)}

    def ash_count(self, wid: int) -> int | None:
        """How many skills the weapon can fire at Standard, max level (its own included)."""
        try:
            if "_ash_t" not in self.__dict__:
                self._ash_t = ASH.AshTables(None)
            level = self.tables.max_level(self.tables.weapons[wid]["reinforceTypeId"])
            return len(ASH.mountable_skills(self._ash_t, wid, 0, level))
        except (KeyError, SystemExit, TypeError):
            return None


# --------------------------------------------------------------------------------------------
# corpus comparison


def apply_relative_speed(rows: list[dict], tau: float) -> None:
    """`er-builds-pvp.relative_speed` on the off-hand L1: each row's score times
    `exp(-(startup - the fastest L1 of its wepType among rows) / tau)`, kept as `f_speed`."""
    fastest = {}
    for r in rows:
        if r["startup"] is not None:
            fastest[r["wep_type"]] = min(fastest.get(r["wep_type"], r["startup"]), r["startup"])
    for r in rows:
        f = PVP.relative_speed(r["startup"], fastest.get(r["wep_type"]), tau)
        r["f_speed"] = f
        r["score"] = r["score"] * f


def corpus_left_usage(s: Scorer) -> tuple[Counter, Counter]:
    """(off-hand uses, powerstance uses) of each left weapon: a left-hand item counts as off-hand
    when its L1 is an off-hand attack with the build's first right-hand weapon."""
    off, dual = Counter(), Counter()
    for b in s.builds:
        r = next((s.names[n] for n in b["right"] if n in s.names), None)
        for n in set(b["left"]):
            wid = s.names.get(n)
            if wid is None:
                continue
            mode = COMBO.left_mode(s.reg, r, wid) if r is not None else "offhand"
            (dual if mode == "dual" else off)[wid] += mode in ("dual", "offhand")
    return off, dual


def spearman(a: list[float], b: list[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


# --------------------------------------------------------------------------------------------
# output


def _row(r: dict) -> str:
    lk = r["links"] or {}
    ch = r["chain"] or {}
    return (f"{r['weapon']:<28}{r['score']:7.1f} = {r['slot_score'] or 0:6.1f} x fit {r['fit'] or 0:4.2f} x link "
            f"{r['f_link']:4.2f} x speed {r.get('f_speed', 1.0):4.2f} | st {r['startup']:>4} next {r['next']!s:>4} roll {r['roll']!s:>4} "
            f"L1>L1 {ch.get('gap', '-')!s:>4}/{ch.get('escape', '-')!s:<3}{(ch.get('verdict') or '-')[:4]:<5}| "
            f"dmg {r['dmg']:5.0f} poise {r['poise']:4.0f} stag {r['stagger']:4.2f} stam {r['stamina']:>3} "
            f"fS {r['f_stamina'] or 0:4.2f} fX {r['f_exchange'] or 0:4.2f} reach {r['reach'] or 0:4.2f} "
            f"wt {r['weight']:4.1f} elig {r['eligible']:4.2f} | pL {lk.get('p_link', 0):4.2f} "
            f"true {lk.get('true_share', 0):4.2f} dual {lk.get('dual_share', 0):4.2f}"
            + _pressure_cell(r))


def _pressure_cell(r: dict) -> str:
    p = r.get("pressure")
    if not p:
        return ""
    agg = Counter()
    for w, a in r.get("affinity_mix") or []:
        agg[a] += w
    aff = ",".join(f"{a} {w:.0%}" for a, w in agg.most_common() if w >= 0.1)
    return (f" | chain {p['hits']} hits gaps {[round(g, 1) for g in p['gaps']]} q {p['q']} "
            f"dmg {p['dmg']:.0f} commit {p['commit']:.1f} dps {p['dps'] or 0:.0f} poise/s {p['poise_ps'] or 0:.0f} "
            f"whiff {p['whiff_recovery']:.1f} | aff {aff} ({r.get('affinities')}) ashes {r.get('ashes')}")


def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)
    check(abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12
          and abs(spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-12, "spearman: +1 and -1 on monotone data")
    check(abs(spearman([1, 2, 2, 3], [1, 2, 2, 3]) - 1.0) < 1e-12, "spearman: ties take their mean rank")
    s = Scorer()
    axe = s.names["Battle Axe"]
    check(s.reg.weapon[axe]["wepType"] in CLASSES["axe"] and s.reg.weapon[s.names["Greataxe"]]["wepType"]
          in CLASSES["greataxe"], "CLASSES: Battle Axe is an axe, Greataxe a greataxe")
    r = s.score(axe, with_links=False)
    check(r and r["startup"] == 13 and r["next"], f"Battle Axe L1: first hit on frame 13 real (combo.md 3) ({r and r['startup']})")
    check(r and 0.0 <= r["fit"] <= 1.0 and r["weight"] > 0, f"fit is a share ({r and r['fit']})")
    heavy = fit_share(s.builds, 30.0)
    light = fit_share(s.builds, 1.0)
    check(heavy is not None and heavy <= light, f"a heavier left weapon never fits more builds ({light} >= {heavy})")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--class", dest="klass", choices=sorted(CLASSES), help="only this weapon class")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--relative-speed", type=float, metavar="TAU",
                    help="multiply by exp(-(L1 startup - class fastest) / TAU) (`apply_relative_speed`)")
    ap.add_argument("--from-json", type=Path, help="re-rank a previous --json output instead of scoring again")
    ap.add_argument("--chain", action="store_true",
                    help="score the whole L1 chain thrown on its own as the move (`Scorer.chain`, combo.md 10)")
    ap.add_argument("--req-cost", help="comma-separated weapons: the levels their requirements cost (combo.md 10e)")
    ap.add_argument("--req-fit", action="store_true",
                    help="multiply each score by the share of corpus builds that meet the requirements")
    ap.add_argument("--roll-catch", action="store_true",
                    help="with --chain: the waiting defenders roll L1 #1 and the chain may catch the roll "
                         "(`Scorer.roll_catch`, combo.md 10b)")
    ap.add_argument("--best-affinity", action="store_true",
                    help="each build kind takes the affinity whose L1 deals the most (`affinity_mix`)")
    ap.add_argument("--jobs", type=int, default=PVP.default_jobs(),
                    help="score candidates in this many forked workers (default: cores - 1); 1 is serial")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    PVP.lower_priority()
    if a.selftest:
        return selftest()
    if a.req_cost:
        s = Scorer(a.rl, a.window)
        names = [n.strip() for n in a.req_cost.split(",") if n.strip()]
        carriers = {n: [b for b in s.builds if n in b["left"]] for n in names}
        print("starting classes (CharaInitParam, damage stats):", s.classes())
        print("corpus starting class:", Counter(b["cls"] for b in s.builds).most_common(8))
        for n in names:
            wid = s.names[n]
            c = s.requirement_cost(wid)
            print(f"\n{n}: needs {c['need']}; corpus builds meeting it {c['eligible']:.3f}, mean levels short "
                  f"{c['mean_levels']:.2f} ({c['mean_levels_if_short']:.2f} among the short); levels short at "
                  f"class start {c['classes']}")
            for m in names:
                if m != n and carriers[m]:
                    cm = s.requirement_cost(wid, carriers[m])
                    print(f"  over the {len(carriers[m])} builds carrying {m} in the left hand: meeting "
                          f"{cm['eligible']:.3f}, mean levels short {cm['mean_levels']:.2f}")
            vag = [b for b in s.builds if b["cls"] == "Vagabond"]
            cv = s.requirement_cost(wid, vag)
            print(f"  over the {len(vag)} Vagabond builds: meeting {cv['eligible']:.3f}, mean levels short "
                  f"{cv['mean_levels']:.2f}; their left-hand carry {sum(n in b['left'] for b in vag)}")
        return 0
    if a.from_json:
        rows = json.load(a.from_json.open())["rows"]
        builds = json.load(a.from_json.open())["builds"]
        if a.klass:
            rows = [r for r in rows if r["wep_type"] in CLASSES[a.klass]]
    else:
        s = Scorer(a.rl, a.window)
        builds = len(s.builds)
        cands = s.candidates()
        if a.klass:
            cands = [w for w in cands if s.reg.weapon[w]["wepType"] in CLASSES[a.klass]]
        def score(w):
            return s.score(w, chain=a.chain, best_aff=a.best_affinity, roll_catch=a.roll_catch)
        scored = list(map(score, cands)) if a.jobs <= 1 else PVP._fork_map(score, cands, a.jobs)
        rows = [r for r in scored if r]
        off, dual = corpus_left_usage(s)
        for r in rows:
            r["corpus_offhand"], r["corpus_dual"] = off.get(r["id"], 0), dual.get(r["id"], 0)
    if a.relative_speed:
        apply_relative_speed(rows, a.relative_speed)
    if a.req_fit:
        for r in rows:
            r["f_req"] = r["eligible"]
            r["score"] = r["score"] * r["eligible"]
    rows.sort(key=lambda r: -r["score"])
    rho_all = spearman([r["score"] for r in rows], [r["corpus_offhand"] for r in rows])
    used = [r for r in rows if r["corpus_offhand"]]
    rho_used = spearman([r["score"] for r in used], [r["corpus_offhand"] for r in used])
    if a.json:
        print(json.dumps({"rl": a.rl, "builds": builds, "rho_all": rho_all, "rho_used": rho_used,
                          "rows": rows}, indent=1))
        return 0
    print(f"RL {a.rl}+-{a.window}: {builds} PvP builds, {len(rows)} left weapons with an off-hand L1")
    groups = [("axe", CLASSES["axe"]), ("greataxe", CLASSES["greataxe"])] if not a.klass else [(a.klass, CLASSES[a.klass])]
    for label, types in groups:
        print(f"\n{label}s:")
        for r in [r for r in rows if r["wep_type"] in types][:a.top]:
            print("  " + _row(r) + f" | corpus off {r['corpus_offhand']} dual {r['corpus_dual']}")
    if not a.klass:
        print("\nall classes:")
        for i, r in enumerate(rows[:a.top], 1):
            print(f"{i:>4} " + _row(r) + f" | corpus off {r['corpus_offhand']}")
    by_corpus = sorted(rows, key=lambda r: -r["corpus_offhand"])
    print("\ncorpus off-hand use (left item whose L1 is an off-hand attack with the build's right weapon):")
    rank = {r["id"]: i for i, r in enumerate(rows, 1)}
    for r in by_corpus[:a.top]:
        print(f"  {r['weapon']:<28} {r['corpus_offhand']:>4} builds  model rank {rank[r['id']]:>4} "
              f"score {r['score']:6.1f}")
    print(f"\nspearman(score, corpus off-hand use): all {len(rows)} = {rho_all and round(rho_all, 3)}; "
          f"used ({len(used)}) = {rho_used and round(rho_used, 3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
