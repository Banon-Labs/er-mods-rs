#!/usr/bin/env python3
"""Weapon, powerstance and ash vectors at one stat block (docs/er-mechanics/weapon-embedding.md).

    python3 scripts/er-mechanics-weapon-embed.py --build <build.json> --ash-rows <rows.json> \
        --out <embed.json> [--reach-cache <reach.json>] [--no-reach] [--no-powerstance]
    python3 scripts/er-mechanics-weapon-embed.py --selftest

One JSON record per key, nothing measured here that another script already measures:

- weapon vectors, keyed (weapon, affinity, grip, stat block) for every named base weapon of the
  classes in `er-mechanics-combo-loadout.CLASS` and every affinity a player can give it
  (`er-builds-optimize.affinities`). Grips: `1H` (right hand one-handed), `2H`, `L1` (left hand,
  the off-hand L1 chain). Per attack slot (`er-mechanics-attacks.SLOTS_ONE_HAND`, the off-hand
  slots of `er-mechanics-powerstance-guard.OFFHAND_SLOTS`): startup, active frames, last hit,
  roll cancel, next-attack cancel, reach (`er-mechanics-reach.reach_summary`; the off-hand slots
  through `attack_reach(..., hand='left')`, the weapon on `L_Weapon`),
  stamina, motion values, hits, poise damage, hyperarmor start, hold range and feint window
  (below), each hit's frame and PvP poise damage and the slot's hyperarmor windows (`trade_fields`,
  for `er-mechanics-ash-interrupt.ChainTrades`), attack by damage type, PvP damage against the
  fixed defender, status build-up and the
  HP that build-up is worth (`er-mechanics-ash-table.Table.status_hp`). A slot the weapon lacks is
  absent. Summaries come from the slots: R1 chain recovery, chain motion value per second and
  stamina per R1 (`er-mechanics-moveset-sweep.chain_metrics`), R1 #1 reach, chain damage per
  second, and `damage`: one motion-value-100 hit against the defender, which is what the
  scaling role ranks.
- powerstance vectors, keyed by both weapons with their affinities and the stat block, for every
  pair `er-mechanics-powerstance-guard.can_powerstance` allows whose powerstance animations
  exist (`powerstance_rows` returns a slot). Each hand is taken at its own best-damage
  one-handed affinity. The animations are the right weapon's `wepmotionCategory` file, never an
  `spAtkcategory` override, so two Shamshirs use the curved sword set.
- ash vectors, keyed (ash, weapon, affinity, grip, stat block): the rows of an
  `er-mechanics-ash-table.py --json` sweep at this stat block (`--ash-rows`), with the hold range
  and feint window of the skill's opening added and their value
  (`er-mechanics-combo-loadout.value`). A fixed-skill unique's skill is its only ash vector.

Frames are real 30 fps frames from the slot's start for right-hand and off-hand slots (the
off-hand rows of `powerstance-guard._slot_row` are carried through `clip_to_real`); the
powerstance slots are read by `_slot_row` as they are, in clip frames.

Feint features:

- `hold`: [earliest, latest] frame a held attack can be released. An R2 (and its charged form)
  releases from the Start clip's first `R2_RELEASE_SPEFFECT` frame (`release_lead_in`) until the
  Start clip ends (the charged R2 is what plays then). A skill holds between its follow-up
  windows (`er-mechanics-ashes.skill_followups`, ashes-of-war.md section 16): the earliest and
  latest frame of the opening a follow-up press starts the next animation. Absent otherwise.
- `feint`: frames before the slot's first active hit frame in which a TAE event 300
  (`ActivateChrActionFlagEarly`) opens a cancel into a roll, guard, R1, R2 or L1
  (`FEINT_CANCEL_IDS`); 0 = committed from frame 0.

Fun (decisions 8-10): every option is a (base weapon, affinity) pair. A mechanic's `share` is the
fraction of all options in the game that have it, and its rarity is 1 - share. Mechanics:

- `status:<status>`: a status the weapon applies; a damage-over-time status is also told apart by
  its tick and duration (`er-mechanics-status.proc_effect` against the target), so Deadly Poison's
  16.7 HP/s for 30 s is not the common 8.3 HP/s for 90 s;
- `scaling:<element>-<stat>`: a stat that scales an element of the weapon (AttackElementCorrect
  flag and a positive reinforced rate). Physical-FTH is on weapons whose own row flags it (Erdsteel
  Dagger, the Smithscript weapons): the Sacred and Flame Art affinities scale only their holy or
  fire part with FTH (AttackElementCorrect 10000, checked on Longsword);
- `ash:<skill>`: a skill with a damaging hit the option can carry (`er-mechanics-ashes.
  mountable_skills`); steps and buffs are not movesets and are left out.

A vector's `fun` is the rarity of its rarest mechanic. A mechanic counts as fun when its share is
at most `FUN_MAX_SHARE`; picking them per class is `er-mechanics-class-roles.py`'s job.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import re
import struct
import sys
import time
import types
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, fname):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / fname)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


AT = _load("er_mechanics_ash_table", "er-mechanics-ash-table.py")
S = AT.S
A, OPT, AR = S.A, S.OPT, S.AR
DEF = OPT.DEF
ST = _load("er_mechanics_status", "er-mechanics-status.py")
PG = _load("er_mechanics_powerstance_guard", "er-mechanics-powerstance-guard.py")
MS = _load("er_mechanics_moveset_sweep", "er-mechanics-moveset-sweep.py")
COMBO = _load("er_mechanics_combo_loadout", "er-mechanics-combo-loadout.py")
ATK = PG.ATK


def _intr():
    """`er-mechanics-interrupt.py`, for its member and window readers (`trade_fields`)."""
    return _load("er_mechanics_interrupt", "er-mechanics-interrupt.py")


def trade_fields(hits, windows):
    """The per-hit fields `er-mechanics-ash-interrupt.chain_cast` reads off a slot (issue
    er-effects-rs-q0r9): `hit_at` each hit's first frame, `hit_poise` its PvP poise damage in menu
    units (`poise_damage x 10 x FinalDamageRateParam.saRate`, `er-mechanics-interrupt._slot_member`),
    and `ha` the slot's TAE 795 windows [start, end, bonus (menu), poise-taken multiplier, floor
    share, HP-taken multiplier] (`er-mechanics-interrupt._window` plus ToughnessParam `unk2`, the
    tuple `er-mechanics-ash-interrupt.simulate` reads). `hits` [(frame, menu poise)], frames on the
    slot's own clock."""
    return {"hit_at": [rnd(f, 2) for f, _ in hits], "hit_poise": [rnd(p, 2) for _, p in hits],
            "ha": [[rnd(x, 3) for x in w] for w in sorted(windows)]}


def tae_windows(reg, events, frame, weapon_of):
    """`trade_fields` windows of every TAE 795 event in `events`: `frame(seconds)` puts its ends on
    the slot's clock, `weapon_of(event)` names the weapon whose `toughnessCorrectRate` the bonus
    takes (Args byte 2 picks the hand, `er-mechanics-powerstance-guard._slot_row`)."""
    INTR = _intr()
    out = []
    for e in events or ():
        if e.type != ATK.TAE_TOUGHNESS:
            continue
        ratio = struct.unpack_from("<f", e.params, 4)[0]
        w = INTR._window(reg, reg.weapon[weapon_of(e)], e.params[0], e.params[1], ratio,
                         (frame(e.start), frame(e.end)))
        out.append(w + [reg.toughness.get(e.params[0], {}).get("unk2") or 1.0])
    return out

#: Planner build 188e74da444029 (STR 18 / DEX 80, RL150).
DEFAULT_STATS = {"str": 18, "dex": 80, "int": 7, "fth": 8, "arc": 9}
GRIPS = ("1H", "2H", "L1")
ELEMENTS = A.ELEMENTS
MV_KEY = {"physical": "mv_phys", "magic": "mv_mag", "fire": "mv_fire", "lightning": "mv_light",
          "holy": "mv_holy"}
#: The grease a greasable row is also scored with: the DLC drawstring tier (+135 flat, 25 s), the
#: tier of the build's own Drawstring Royal Magic Grease.
GREASE_TIER = "dlc-drawstring"
GREASE_FLAT = OPT.GREASES[GREASE_TIER]
#: A skill's weapon buff is one adding element attack like a grease does (Lightning Slash's
#: `thunderAttackPower` 85 under `wepParamChange`).
WEAPON_BUFF_FIELDS = ("magicAttackPower", "fireAttackPower", "thunderAttackPower", "darkAttackPower")
#: The actions a feint can turn into (`er-mechanics-attacks.RECOVERY_ACTIONS`): an attack, a roll
#: or a guard. Each is open where its input window and its cancel window overlap, the cancel coming
#: from a JumpTable event or an event 300 (`recovery_windows`). Measured 2026-10-08 over 2314
#: weapon slots (every sixth base weapon, both grips): none opens before its first hit, by either
#: event, so a weapon's feints are its R2 hold.
FEINT_ACTIONS = ("r1", "r2", "dodge", "guard")
#: The left hand reads `a048` when neither the weapon's `spAtkcategory` nor its
#: `wepmotionCategory` TAE binds the animation (`FUN_1403f1f70`, `VERIFIED` in
#: er-mechanics-attacks; `er-mechanics-combo.LEFT_HAND_FALLBACK_CATEGORY`).
LEFT_HAND_FALLBACK_CATEGORY = 48
#: A mechanic is fun when at most this share of all (weapon, affinity) options has it. Measured
#: 2026-10-08 over 2346 options: physical-FTH 3.6%, physical-INT 2.9%, Deadly Poison 0.6%, while
#: each elemental infusion's own route is 7-17%, physical-ARC 23% and bleed about a quarter.
FUN_MAX_SHARE = 0.05
#: The code that measures a world reach. Reach is geometry and animation, the same at every stat
#: block, and it was 551 of a 693 s embedding (measured 2026-10-14), so reaches are kept across
#: runs in a file named for a hash of this code (`default_reach_cache`).
REACH_SOURCES = ("er-mechanics-weapon-embed.py", "er-mechanics-reach.py", "er-mechanics-attacks.py",
                 "er-hkx-pose.py", "er-param-read.py", "er-tae-event-scan.py")
#: The reach file is rewritten at most this often while weapons are measured, and once at the end.
REACH_SAVE_S = 10.0


def default_reach_cache() -> Path:
    h = hashlib.sha256()
    for n in REACH_SOURCES:
        h.update((HERE / n).read_bytes())
    return OPT.CACHE / f"embed-reach-{h.hexdigest()[:16]}.json"


def stats_key(stats):
    return "/".join(f"{k}{stats[k]}" for k in AR.STATS)


def ascii_name(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def rnd(x, n=1):
    return None if x is None else round(x, n)


def merge_len(intervals, lo, hi):
    """Total length of the union of `intervals` inside [lo, hi)."""
    out, end = 0.0, lo
    for a, b in sorted((max(a, lo), min(b, hi)) for a, b in intervals):
        if b <= a:
            continue
        a = max(a, end)
        if b > a:
            out += b - a
            end = b
    return out


def feint_window(events, first_hit, weight_rate, real=True):
    """Frames before `first_hit` in which an event 300 opens a `FEINT_CANCEL_IDS` cancel; None
    when there is no hit to feint."""
    if first_hit is None or events is None:
        return None
    to_real = ATK.clip_to_real(events) if real else (lambda s: s)
    win = ATK.recovery_windows(events, weight_rate)
    spans = [(to_real(max(i[0], c[0])) * ATK.TAE_FPS, to_real(min(i[1], c[1])) * ATK.TAE_FPS)
             for key in FEINT_ACTIONS for i in win[key]["input"] for c in win[key]["cancel"]]
    return round(merge_len(spans, 0.0, first_hit), 1)


class Embed:
    """The tables, the two defenders and the per-weapon caches."""

    def __init__(self, stats, reach_cache=None, reach=True):
        self.stats = dict(stats)
        self.lev = S.LEV.Levers()
        self.t = self.lev.t                       # AshTables: final rates, atk extras, skills
        self.art = AR.Tables(None)
        self.pg = PG.Tables(None)
        self.st = ST.Tables(ar_tables=self.art)
        rows = OPT.corpus_rows(OPT.CACHE / "builds.jsonl", 140, 160)
        self.defender = OPT.bracket_defender(rows)
        st_rows = ST.corpus_rows(rl=150)
        d = ST.corpus_defender(st_rows)
        d["hp"] = AT.TARGET_HP
        # `Table.buildup_hp`'s engagement model reads the corpus as defenders, under the fight
        # model's pressure (`AT.FIGHT`, set from `--fight`).
        d["dfs"] = ST.Defenders(self.st, st_rows)
        B = AT._buffs_mod()
        d["pressure"] = B.FIGHT_KINDS[AT.FIGHT or B.DEFAULT_FIGHT]["pressure"]
        self.status_target = d
        self.reach_path = reach_cache
        self.reach_on = reach
        try:
            self.reach = json.loads(reach_cache.read_text()) if reach_cache else {}
        except (OSError, ValueError):
            self.reach = {}
        self._reach_saved, self._reach_dirty = time.time(), False
        self.base = [w for w in sorted(self.art.weapons) if w % 10000 == 0 and self.art.names.get(w)
                     and not self.art.names[w].startswith("[") and self.art.weapons[w]["wepType"] in COMBO.CLASS]
        self._moves = {}
        self._sa = None

    def sa(self):
        """`FinalDamageRateParam.saRate` per AtkParam row (`er-mechanics-exchange.SaRates`)."""
        if self._sa is None:
            self._sa = _intr().EXCH.SaRates()
        return self._sa

    # ---- moveset (affinity-independent)

    def slot_reach(self, name):
        if name not in self.reach and self.reach_on:
            RC = ATK._reach()
            out = {}
            for grip in ("one", "both"):
                try:
                    out.update({k: v.get("world_reach_m") for k, v in RC.reach_summary(name, grip).items()})
                except Exception as e:  # an input missing from the unpacked tree
                    print(f"{name} {grip}: no reach ({e!r})", file=sys.stderr)
            self.reach[name] = out
            self.save_reach()
        return self.reach.get(name, {})

    def save_reach(self, force=False):
        """Write the reaches to `reach_path` whole (a run reading it meanwhile never sees half a
        file), at most every `REACH_SAVE_S` unless `force`."""
        self._reach_dirty = True
        if not self.reach_path or (not force and time.time() - self._reach_saved < REACH_SAVE_S):
            return
        self.reach_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.reach_path.with_name(f"{self.reach_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.reach))
        os.replace(tmp, self.reach_path)
        self._reach_saved, self._reach_dirty = time.time(), False

    def left_reach(self, wid, key, judge, anim, cat):
        """World reach (m) of one off-hand L1 slot, the weapon held in the left hand, or None.
        Cached beside the right-hand reaches under `<name>|L1`."""
        name = self.art.names[wid]
        ck = f"{name}|L1"
        cached = self.reach.setdefault(ck, {})
        if key not in cached and self.reach_on:
            RC = ATK._reach()
            if getattr(self, "_rc", None) is None:
                self._rc = RC.Reach(None)
            w = self.pg.weapon[wid]
            # A slot the weapon's own files lack is read from the fallback file, as the moveset is.
            own = ATK.motion_category(w, anim, right_hand_fallback=False)
            clip = (cat, anim) if cat != own else None
            try:
                r = RC.attack_reach(self._rc, wid, key, key, judge, anim, "one", clip=clip, hand="left")
                cached[key] = r.get("world_reach_m") if r else None
            except Exception as e:  # an input missing from the unpacked tree
                print(f"{name} {key}: no reach ({e!r})", file=sys.stderr)
                cached[key] = None
            self.save_reach()
        return cached.get(key)

    def dual_reach(self, row):
        """World reach (m) of one powerstance slot row (`powerstance_rows`), or None: each hand's
        hits measured with that hand's own weapon (`attack_reach` `only_judges`), the farther of
        the two. Cached beside the other reaches under `<weapon>|PS`, per clip, hand and judges,
        so every pair sharing a weapon's hits in a clip reads them once. Before 2026-10-19 no
        dual attack had a reach, so the off-hand score left reach out wherever a powerstance was
        in the pool, and Beastman's Curved Sword's 20% shorter R1 went unseen beside Shamshir."""
        if not self.reach_on:
            return None
        cat, anim = (int(x) for x in row["anim"][1:].split("_"))
        by_hand = collections.defaultdict(set)
        for h in row["hits"]:
            if h.get("judge") is not None:
                by_hand[h["weapon"], h["hand"]].add(h["judge"])
        out = []
        for (wid, hand), judges in by_hand.items():
            name = self.art.names[wid]
            cached = self.reach.setdefault(f"{name}|PS", {})
            key = f"{row['anim']}|{hand}|{','.join(map(str, sorted(judges)))}"
            if key not in cached:
                RC = ATK._reach()
                if getattr(self, "_rc", None) is None:
                    self._rc = RC.Reach(None)
                try:
                    r = RC.attack_reach(self._rc, wid, row["slot"], row["slot"], min(judges), anim, "one",
                                        clip=(cat, anim), hand=hand, only_judges=judges)
                    cached[key] = r.get("world_reach_m") if r else None
                except Exception as e:  # an input missing from the unpacked tree
                    print(f"{name} {key}: no reach ({e!r})", file=sys.stderr)
                    cached[key] = None
                self.save_reach()
            if cached.get(key) is not None:
                out.append(cached[key])
        return max(out) if out else None

    def moveset(self, wid):
        """{grip: {slot: timing row}} for a base weapon: the right-hand rows of
        `weapon_attacks` (1H, 2H) and the off-hand L1 rows (L1), with hold and feint."""
        if wid in self._moves:
            return self._moves[wid]
        reg, w = self.pg, self.pg.weapon[wid]
        level = self.art.max_level(w["reinforceTypeId"])
        name = self.art.names[wid]
        reach = self.slot_reach(name)
        out = {}
        paired = two_hand_is_pair(self.art.weapons[self.art.find_weapon(name, "Standard")])
        for grip, g in (("1H", "one"), ("2H", "both")):
            rows = {r["slot"].removeprefix("2h_"): r for r in ATK.weapon_attacks(reg, wid, g, level)}
            slots = {}
            for key, r in rows.items():
                hw = r.get("hit_windows") or []
                others = [o["frames"] for o in r.get("other_hitboxes") or []]
                if not hw and not others:
                    continue
                # Per-hit frames, poise and the TAE 795 windows (`trade_fields`), read as the
                # interrupt matrix reads a member: each hit that opens a fresh hit record, the
                # other hand's of a paired two-handed swing included.
                mem = _intr()._slot_member(reg, self.sa(), wid, r, None)
                trade = trade_fields([(f, p * sa) for f, p, sa in mem["hits"]],
                                     [w + [h.get("pvp_hp_damage_taken") or 1.0]
                                      for w, h in zip(mem["windows"], r.get("hyperarmor") or [])]) if mem else {}
                if paired and grip == "2H" and others:
                    # A paired weapon two-handed swings both hands (`two_hand_is_pair`): the other
                    # hand's hitbox is a hit of the attack, not a stray one, so it counts toward
                    # the hits, the startup and the status like the slot's own.
                    r = dict(r, hit_windows=sorted(hw + others),
                             own_sweep_hits=max(1, r.get("own_sweep_hits") or 1) + len(others), other_hitboxes=[])
                    hw, others = r["hit_windows"], []
                first = hw[0][0] if hw else min(f[0] for f in others)
                cat, anim = (int(x) for x in r["tae_entry"][1:].split("_"))
                _, _, events = ATK.resolve_events(cat, anim)
                ha = r.get("hyperarmor") or []
                sl = MS.slim(r)
                sl.update(first_hit=first, last_hit=max([f[1] for f in hw + others]))
                slots[key] = {
                    "_row": r, "_slim": sl, "anim": r["anim"], "startup": first,
                    "active": rnd(sum(b - a for a, b in hw)), "last_hit": sl["last_hit"],
                    "roll_cancel": (r.get("cancel_frame") or {}).get("dodge"),
                    "next_cancel": (r.get("cancel_frame") or {}).get(key.split("_")[0] if key.startswith("r2") else "r1"),
                    "roll_after_hit": (r.get("recovery_after_hit") or {}).get("dodge"),
                    "reach": reach.get(("2h_" if grip == "2H" else "") + key),
                    "stamina": r["stamina_cost"], "mv": r["mv_phys"],
                    "mv_by": {el: r[k] for el, k in MV_KEY.items() if r[k]},
                    "hits": max(1, r.get("own_sweep_hits") or 1), "poise": rnd(r["poise_damage"]),
                    "hyperarmor": ha[0]["frames"][0] if ha else None,
                    "feint": feint_window(events, first, w["weaponWeightRate"]),
                    **trade,
                }
            for k in ("r2_1", "r2_2"):
                if k in slots and k + "c" in slots:
                    lead = slots[k]["_row"].get("release_lead_in")
                    hold = [lead, slots[k + "c"]["_row"].get("anim_frames")] if lead is not None else None
                    slots[k]["hold"] = slots[k + "c"]["hold"] = hold
            out[grip] = slots
        # Off-hand L1, a weapon in the left hand with no powerstance pair. `c0000.hks`
        # `GetAttackRequest`: L1 (ACTION_ARM_L1) with `IsEnableDualWielding` false and a left
        # weapon `IsWeaponCanGuard` refuses returns ATTACK_REQUEST_LEFT_HEAVY, which `ExecAttack`
        # sends to `W_AttackLeftHeavy1`; each `AttackLeftHeavy<n>_onUpdate` passes
        # `W_AttackLeftHeavy<n+1>` as the next L1 (`AttackLeftHeavy5` only when
        # `IsEnableNextAttack(5, HAND_LEFT)`, `AttackLeftHeavy6` back to 1). The states play
        # 035000..035050 (`OFFHAND_SLOTS`). The file is resolved like a right-hand attack's, so
        # a weapon whose `spAtkcategory` file has 035000 (Shamshir's a156, Celebrant's Cleaver's
        # a172) uses its own L1 chain, then `wepmotionCategory`, then a048. Imported entries
        # are followed (`resolve_events`).
        slots = {}
        for key, label, judge, anim, state in PG.OFFHAND_SLOTS:
            cat = ATK.motion_category(w, anim, right_hand_fallback=False)
            if not ATK._bound(cat, anim) and ATK._bound(LEFT_HAND_FALLBACK_CATEGORY, anim):
                cat = LEFT_HAND_FALLBACK_CATEGORY
            _, _, events = ATK.resolve_events(cat, anim)
            anims = {anim: events} if events else {}
            r = PG._slot_row(reg, key, label, anim, state, anims, cat, lambda s: wid, level,
                             lambda j, want=judge: j == want)
            if not r:
                continue
            ha = r["hyperarmor"]
            # `_slot_row` counts clip frames; the 035xxx entries carry TAE 608 play-speed windows
            # (a172_035010 1.29x, a030_035000 1.08x), so carry every frame to real 30 fps frames
            # as the right-hand slots are.
            to_real = ATK.clip_to_real(events)

            def rf(clip):
                return None if clip is None else ATK.real_frame(to_real(clip / ATK.TAE_FPS))

            first, last, dodge = rf(r["first_hit"]), rf(r["last_hit"]), rf(r["dodge"])
            slots[key] = {
                "_pg": r, "anim": r["anim"], "startup": first,
                "active": rnd(sum(rf(h["frames"][1]) - rf(h["frames"][0]) for h in r["hits"])),
                "last_hit": last, "roll_cancel": dodge, "next_cancel": rf(r["next_l1"]),
                "roll_after_hit": rnd(dodge - last) if dodge is not None else None,
                "reach": self.left_reach(wid, key, judge, anim, cat),
                "stamina": r["stamina_cost_sum"], "mv": r["mv_sum"],
                "mv_by": {el: sum(h[k] for h in r["hits"]) for el, k in MV_KEY.items()
                          if sum(h[k] for h in r["hits"])},
                "hits": r["hit_count"], "poise": rnd(r["poise_sum"]),
                "hyperarmor": rf(ha[0]["frames"][0]) if ha else None,
                "feint": feint_window(events, first, w["weaponWeightRate"]),
                **trade_fields([(rf(h["frames"][0]), h["poise_damage"] * _intr().POISE_MENU * self.sa()(h["atk_row"]))
                                for h in r["hits"]],
                               tae_windows(reg, events, lambda s: rf(s * ATK.TAE_FPS), lambda e: wid)),
            }
        out["L1"] = slots
        self._moves[wid] = out
        return out

    # ---- damage

    def ar(self, name, aff, grip):
        """(AR row, wieldable) for one grip; the left hand never gets the two-handed STR bonus."""
        level = self.art.max_level(self.art.weapons[self.art.find_weapon(name, aff)]["reinforceTypeId"])
        r = AR.attack_rating(self.art, name, aff, level, self.stats, grip == "2H")
        return r, not any(v["scaling"] < 0 for v in r["damage"].values()), level

    def hit_damage(self, wid, ar_by, mv_by, atk_row, count=1):
        hit = {"attack": {el: ar_by.get(el, 0.0) * mv_by.get(el, 0) / 100.0 for el in ELEMENTS},
               "final_rate_id": self.t.atk_extra.get(atk_row, {}).get("finalDamageRateId", -1),
               "atk_row": atk_row, "count": count}
        A.pvp_damage(self.t, wid, [hit], self.defender, DEF)
        return hit

    def status_shim(self, name, aff, level, two):
        sh = types.SimpleNamespace(weapon=name, affinity=aff, level=level, stats=self.stats, t=self.t,
                                   status_tables=lambda: (ST, self.st, self.status_target))
        # `Table.status_hp` reads each hit through `Table.hit_buildup`; without it every
        # status-carrying option raised AttributeError and dropped out of the embed (911 of them
        # in the 2026-10-08 STR80 run, Forked Hatchet among them).
        sh.hit_buildup = lambda *args: AT.Table.hit_buildup(sh, *args)
        # The same for `Table.buildup_hp`, which `status_hp` hands its totals to since the DoT
        # cut moved there.
        sh.buildup_hp = lambda *args: AT.Table.buildup_hp(sh, *args)
        sh.status_seq = lambda *args: AT.Table.status_seq(sh, *args)
        ws = ST.weapon_status(self.st, name, aff, level, self.stats, two)
        return sh, ws

    def weapon_vector(self, wid, aff):
        """{grip: record} for one (base weapon, affinity)."""
        name, base_row = self.art.names[wid], self.art.weapons[wid]
        moves = self.moveset(wid)
        out = {}
        for grip in GRIPS:
            r, ok, level = self.ar(name, aff, grip)
            two = grip == "2H"
            ar_by = {el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}
            sh, ws = self.status_shim(name, aff, level, two)
            slots, seqs = {}, {}
            for key, s in moves[grip].items():
                feat = {k: v for k, v in s.items() if not k.startswith("_") and v not in (None, {})}
                # Every element's correction is the physical one on almost every row; keep the
                # split only where it differs.
                if all(v == s["mv"] for v in s["mv_by"].values()):
                    feat.pop("mv_by", None)
                atk_row = s["_row"]["atk_row"] if "_row" in s else None
                if "_row" in s:
                    hit = self.hit_damage(wid, ar_by, s["mv_by"], atk_row, s["hits"])
                    feat["attack"] = {el: round(v) for el, v in hit["attack"].items() if v}
                    feat["damage"] = round(hit["pvp_damage_total"], 1)
                    status_rows = [{"attack": s["_row"]}]
                    status_hits = []
                else:
                    dmg = 0.0
                    att = collections.Counter()
                    for h in s["_pg"]["hits"]:
                        hit = self.hit_damage(wid, ar_by, {el: h[k] for el, k in MV_KEY.items()}, h["atk_row"])
                        dmg += hit["pvp_damage_total"]
                        att.update(hit["attack"])
                    feat["attack"] = {el: round(v) for el, v in att.items() if v}
                    feat["damage"] = round(dmg, 1)
                    status_rows = []
                    status_hits = [{"atk_row": h["atk_row"], "count": 1} for h in s["_pg"]["hits"]]
                if ws["sources"]:
                    _, _, first_row, seq = AT.Table.status_seq(sh, two, status_hits, status_rows)
                    seqs[key] = (seq, first_row)
                    bu = collections.Counter()
                    for row in [x["attack"] for x in status_rows] + status_hits:
                        bu.update(ST.use_buildup(self.st, ws, row))
                    feat["status_buildup"] = {k: round(v, 1) for k, v in bu.items() if v}
                slots[key] = feat
            for key, hp in chain_status(seqs, lambda seq, first: sh.buildup_hp(ws, {}, first, seq)).items():
                slots[key]["status_hp"] = hp
            first = moves[grip].get("r1_1") or moves[grip].get("left_1")
            d100 = greased = None
            row = self.art.weapons[self.art.find_weapon(name, aff)]
            greasable = bool(row.get("isEnhance"))
            if first:
                atk = first["_row"]["atk_row"] if "_row" in first else first["_pg"]["hits"][0]["atk_row"]
                mv100 = dict.fromkeys(ELEMENTS, 100)
                d100 = round(self.hit_damage(wid, ar_by, mv100, atk)["pvp_damage_total"], 1)
                # A row a grease can buff (`isEnhance`: Standard, Heavy, Keen, Quality; never an
                # elemental or status infusion) also takes the best DLC drawstring grease, added
                # flat to that element's attack as `er-mechanics-weapon-flex.py` does. Not in a
                # duel: the ladder bans every grease that adds attack (`er-builds-ladder.grease_ban`),
                # so `er-mechanics-class-roles` must not rank a weapon by one.
                if greasable and AT.FIGHT != "duel":
                    greased = max((round(self.hit_damage(wid, dict(ar_by, **{el: ar_by.get(el, 0.0) + GREASE_FLAT}),
                                                         mv100, atk)["pvp_damage_total"], 1), el)
                                  for el in OPT.GREASE_ELEMENTS)
            out[grip] = {
                "kind": "weapon", "weapon": name, "affinity": aff, "grip": grip,
                "stats": stats_key(self.stats), "class": COMBO.CLASS[base_row["wepType"]],
                "fixed_skill": base_row.get("gemMountType") == 0, "level": level, "wieldable": ok,
                "crit": base_row["throwAtkRate"],
                "moveset": [base_row["wepmotionCategory"], base_row["spAtkcategory"]],
                "ar": {el: round(v) for el, v in ar_by.items() if v}, "ar_total": round(r["total"]),
                "damage": d100, "greasable": greasable,
                "damage_greased": greased[0] if greased else None,
                "grease": OPT.GREASE_NAMES[GREASE_TIER][greased[1]] if greased else None,
                "status": sorted({s["status"] for s in ws["sources"]}),
                "slots": slots, "summary": summarise(moves[grip], slots, grip),
            }
        return out

    def powerstance(self, right, raff, left, laff):
        reg = self.pg
        lr = self.art.max_level(reg.weapon[right]["reinforceTypeId"])
        ll = self.art.max_level(reg.weapon[left]["reinforceTypeId"])
        # The rows (frames, hit weapons, attack rows) depend on the two base weapons and their top
        # levels only, so a pair embedded on several affinities reads its animations once.
        cache = self.__dict__.setdefault("_ps_rows", {})
        if (right, left) not in cache:
            cache[right, left] = PG.powerstance_rows(reg, right, left, {right: lr, left: ll})
        rows = cache[right, left]
        if not rows:
            return None
        hand, shim = {}, {}
        for wid, aff in ((right, raff), (left, laff)):
            r, ok, level = self.ar(self.art.names[wid], aff, "1H")
            hand[wid] = ({el: r["damage"].get(el, {}).get("total", 0.0) for el in ELEMENTS}, ok)
            shim[wid] = self.status_shim(self.art.names[wid], aff, level, False)
        anims = ATK.tae_animations(reg.weapon[right]["wepmotionCategory"]) or {}
        slots, seqs = {}, {}
        for r in rows:
            dmg, att = 0.0, collections.Counter()
            for h in r["hits"]:
                hit = self.hit_damage(h["weapon"], hand[h["weapon"]][0],
                                      {el: h[k] for el, k in MV_KEY.items()}, h["atk_row"])
                dmg += hit["pvp_damage_total"]
                att.update(hit["attack"])
            # Status build-up per hit through the hitting hand's own weapon and affinity: each hit
            # is that hand's weapon's (the AttackBehavior `Source` byte,
            # `er-mechanics-powerstance-guard`). Both hands fill one gauge, so the hits stay one
            # sequence in landing order; `chain_status` values it below.
            seq, first = [], {}
            for h in r["hits"]:
                sh, ws = shim[h["weapon"]]
                f, b = (AT.Table.status_seq(sh, False, [{"atk_row": h["atk_row"], "count": 1}], [])[2:]
                        if ws["sources"] else ({}, [{}]))
                seq += b
                for k, v in f.items():
                    first.setdefault(k, v)
            if any(seq):
                seqs[r["slot"]] = (seq, first)
            anim = int(r["anim"].split("_")[1])
            ha = r["hyperarmor"]
            slots[r["slot"]] = {k: v for k, v in {
                "anim": r["anim"], "startup": r["first_hit"],
                "active": sum(h["frames"][1] - h["frames"][0] for h in r["hits"]),
                "last_hit": r["last_hit"], "roll_cancel": r["dodge"], "next_cancel": r["next_l1"],
                "roll_after_hit": r["dodge"] - r["last_hit"] if r["dodge"] is not None else None,
                "stamina": r["stamina_cost_sum"], "mv": r["mv_sum"], "hits": r["hit_count"],
                "hands": [h["hand"] for h in r["hits"]], "poise": rnd(r["poise_sum"]),
                "hyperarmor": ha[0]["frames"][0] if ha else None,
                "feint": feint_window(anims.get(anim), r["first_hit"],
                                      reg.weapon[right]["weaponWeightRate"], real=False),
                "attack": {el: round(v) for el, v in att.items() if v}, "damage": round(dmg, 1),
                "reach": self.dual_reach(r),
                # Clip frames, as the slot's other frames (`_slot_row`); each hit's poise through
                # its own hand's weapon and AtkParam row.
                **trade_fields([(h["frames"][0], h["poise_damage"] * _intr().POISE_MENU * self.sa()(h["atk_row"]))
                                for h in r["hits"]],
                               tae_windows(reg, anims.get(anim), lambda s: round(s * ATK.TAE_FPS),
                                           lambda e: left if e.params[2] == PG.SOURCE_LEFT else right)),
            }.items() if v is not None}
        # The proc row is the right hand's own when it carries the status, else the hand that
        # built it first (`proc_row_for` falls back to `first`).
        rsh, rws = shim[right]
        for key, hp in chain_status(seqs, lambda seq, first: rsh.buildup_hp(rws, {}, first, seq)).items():
            if hp:
                slots[key]["status_hp"] = hp
        chain = [slots[k] for k in ("dual_1", "dual_2", "dual_3", "dual_4", "dual_5", "dual_6") if k in slots]
        return {"kind": "powerstance", "right": self.art.names[right], "right_affinity": raff,
                "left": self.art.names[left], "left_affinity": laff, "stats": stats_key(self.stats),
                "class": COMBO.CLASS[reg.weapon[right]["wepType"]],
                "wieldable": hand[right][1] and hand[left][1], "slots": slots,
                "summary": chain_summary(chain)}

    # ---- mechanics

    def option_mechanics(self, wid, aff):
        """{mechanic: label} of one (base weapon, affinity) option, ashes not included."""
        name = self.art.names[wid]
        row = self.art.weapons[self.art.find_weapon(name, aff)]
        level = self.art.max_level(row["reinforceTypeId"])
        rf = self.art.reinforce[row["reinforceTypeId"] + level]
        aecp = self.art.aecp.get(row["attackElementCorrectId"], {})
        out = {}
        for el, suf, bf, rfl, _ in AR.ELEMENTS:
            if not row[bf] * rf[rfl]:
                continue
            for s in AR.STATS:
                aname, cfield, rfield, _ = AR.STAT_FIELDS[s]
                if aecp.get(f"is{aname}Correct_by{suf}") and row[cfield] * rf[rfield] > 0:
                    out[f"scaling:{el}-{s}"] = f"{el} damage scales with {s.upper()}"
        ws = ST.weapon_status(self.st, name, aff, level, self.stats, False)
        for src in ws["sources"]:
            rid = ST.proc_row_for(self.st, ws, src["status"])
            if not rid:
                continue
            pe = ST.proc_effect(self.st, rid, AT.TARGET_HP)
            if pe["ticks"] > 1:
                # A damage-over-time proc is told apart by its tick and duration: Deadly Poison.
                sig = f"{pe['hp_per_tick']:.1f} HP/{pe['interval']:g}s x{pe['ticks']}"
                out[f"status:{src['status']} {sig}"] = f"{src['status']} proc: {sig}"
            else:
                # A one-shot proc of a different size is the same status (Blood affinity's bleed
                # does 485 HP to the target where most innate bleed does 385).
                out[f"status:{src['status']}"] = f"{src['status']} proc"
        return out

    def options(self):
        return [(b, a) for b in self.base for a in OPT.affinities(self.art, b)]

    def shares(self, with_ashes=True):
        """{mechanic: share of all options}, plus {mechanic: label}."""
        opts = self.options()
        count, labels = collections.Counter(), {}
        names = {}
        for b, a in opts:
            m = self.option_mechanics(b, a)
            labels.update(m)
            count.update(m.keys())
            if with_ashes:
                lv = self.art.max_level(self.art.weapons[self.art.find_weapon(self.art.names[b], a)]["reinforceTypeId"])
                for sid in A.mountable_skills(self.t, b, AR.AFFINITIES.index(a), lv):
                    names.setdefault(sid, self.t.arts_name(sid))
                for n in {names[sid] for sid in A.mountable_skills(self.t, b, AR.AFFINITIES.index(a), lv)}:
                    count[f"ash:{n}"] += 1
                    labels[f"ash:{n}"] = f"skill {n}"
        return {k: v / len(opts) for k, v in count.items()}, labels, len(opts)

    # ---- ash vectors

    def ash_vector(self, x, share):
        wid = self.t.find_weapon(x["weapon"])
        aff = x.get("affinity") or "Keen"
        lv = self.art.max_level(self.t.reg.weapon[wid]["reinforceTypeId"])
        sids = [s for s in A.mountable_skills(self.t, wid, AR.AFFINITIES.index(aff), lv)
                if self.t.arts_name(s) == x["ash"]]
        rec = {"kind": "ash", **{k: v for k, v in x.items() if k not in ("contrib",)},
               "affinity": aff, "stats": stats_key(self.stats), "value": round(COMBO.value(x), 1)}
        rec["class"] = COMBO.CLASS.get(self.t.reg.weapon[wid]["wepType"], "Other")
        if sids:
            sid = sids[0]
            fu = A.skill_followups(self.t, sid, wid, lv)
            if fu:
                rec["followups"] = fu
                rec["hold"] = [min(f["first"] for f in fu), max(f["last"] for f in fu)]
            else:
                rec["hold"] = None
            prof = A.skill_profile(self.t, sid, wid, lv)
            # A skill that buffs the weapon itself (Lightning Slash's +85 lightning, Flaming
            # Strike, Sacred Blade: a `wepParamChange` SpEffect) takes the slot a grease uses, so
            # on a greasable row it costs the grease while it lasts (`INFERRED`: one weapon buff at
            # a time is community knowledge, the replacement rule was not traced).
            rec["weapon_buff"] = any(
                act["kind"] == "speffect" and act.get("effect") == "buff" and any(
                    (lk or {}).get("effects", {}).get("wepParamChange") and any(
                        (lk or {}).get("effects", {}).get(f, 0) > 0 for f in WEAPON_BUFF_FIELDS)
                    for lk in [act.get("speffect") or {}] + list(((act.get("speffect") or {}).get("links") or {}).values()))
                for acts in prof["anims"].values() for act in acts)
            rec["greasable"] = bool(self.art.weapons[self.art.find_weapon(x["weapon"], aff)].get("isEnhance"))
            opening = A.main_anim(prof)
            tae = A.skill_tae(self.t, sid) or {}
            # A stance (Unsheathe's 040050, then the 040051/040052 loops): the skill's first
            # animation charges no FP, hits nothing and opens R1/R2 into the FP attacks. It is
            # released from the first frame an R1 or R2 opens there for as long as the skill
            # button is held, so the latest release is open-ended (None).
            stance = min(prof["anims"], default=None)
            if (stance is not None and stance != opening and stance in tae
                    and not any(act["kind"] in ("melee", "bullet") for act in prof["anims"][stance])):
                ev = tae[stance]
                win = ATK.recovery_windows(ev, self.t.reg.weapon[wid]["weaponWeightRate"])
                opens = [o for o in (ATK._first_open(win[b], True) for b in ("r1", "r2")) if o is not None]
                if opens:
                    rec["stance"] = stance
                    rec["hold"] = [ATK.real_frame(ATK.clip_to_real(ev)(min(opens))), None]
            events = tae.get(opening)
            first = None
            if events:
                to_real = ATK.clip_to_real(events)
                starts = [e.start for e in events if e.type == ATK.TAE_ATTACK_BEHAVIOR]
                first = ATK.real_frame(to_real(min(starts))) if starts else None
            if first is None and x["cat"] != "utility":
                first = x.get("first")
            rec["feint"] = feint_window(events, first, self.t.reg.weapon[wid]["weaponWeightRate"])
        m = f"ash:{x['ash']}"
        rec["mechanic"] = m if x["cat"] != "utility" else None
        rec["share"] = round(share.get(m, 0.0), 4) if rec["mechanic"] else None
        rec["fun"] = round(1.0 - share.get(m, 0.0), 4) if rec["mechanic"] else 0.0
        return rec


def chain_summary(chain):
    """R1/L1 chain summaries from slot features (`chain_metrics`'s timing rule: each next press at
    the previous step's earliest next-attack cancel)."""
    if not chain:
        return {}
    t, times, dmg = 0.0, [], 0.0
    for i, s in enumerate(chain):
        times.append(t + s["startup"])
        dmg += s.get("damage") or 0.0
        if i + 1 < len(chain):
            t += s.get("next_cancel") or s.get("len") or 0
    rec = [s.get("roll_after_hit") for s in chain if s.get("roll_after_hit") is not None]
    sta = [s["stamina"] for s in chain]
    out = {"steps": len(chain), "first_startup": chain[0]["startup"],
           "recovery": round(sum(rec) / len(rec), 1) if rec else None,
           "stamina": round(sum(sta) / len(sta), 1), "first_damage": chain[0].get("damage"),
           "first_reach": chain[0].get("reach")}
    if len(chain) > 1 and times[-1] > 0:
        out["damage_per_sec"] = round(dmg / (times[-1] / 30.0), 1)
        out["mv_per_sec"] = round(sum(s["mv"] * s["hits"] for s in chain) / (times[-1] / 30.0), 1)
    return out


def two_hand_is_pair(row):
    """True when two-handing this weapon plays its paired moveset (`EquipParamWeapon.isDualBlade`
    and `bothHandEquipable`): every claw, fist (Grafted Dragon excepted: it cannot be
    two-handed), hand-to-hand, perfume bottle, throwing dagger, backhand blade, beast claw and a
    few named weapons. Measured 2026-10-19: in each of those classes the two-handed clip
    (032000 + n) hits on the same frames as the powerstance clip (034000 + n), e.g. Raptor Talons
    a022_032000 f12-15 and f14-17 against a022_034000 f12-15 and f14-17. The same test as
    `er-mechanics-talisman-affinity.two_hand_is_pair`."""
    return bool(row.get("isDualBlade") and row.get("bothHandEquipable"))


def chain_status(seqs, hp):
    """{slot: {status: HP}} from {slot: ([{status: build-up} per landed hit], {status: first
    row})}, where `hp(seq, first)` values one engagement that lands `seq`
    (`er-mechanics-ash-table.Table.buildup_hp`).

    A numbered slot `<p>_<n>` lands after `<p>_1` .. `<p>_<n-1>` on the same gauge, so it is
    credited what it adds to that prefix, hp(prefix + its hits) - hp(prefix): a three-swing string
    that procs on the third swing credits the proc across the string instead of zero to each
    swing alone. Any other slot is its own engagement."""
    memo = {}

    def value(keys):
        if keys not in memo:
            seq, first = [], {}
            for k in keys:
                seq += seqs[k][0]
                for s, v in seqs[k][1].items():
                    first.setdefault(s, v)
            memo[keys] = hp(seq, first) if seq else {}
        return memo[keys]

    out = {}
    for key in seqs:
        m = re.fullmatch(r"(.+)_(\d+)", key)
        prefix = tuple(f"{m.group(1)}_{i}" for i in range(1, int(m.group(2)))) if m else ()
        if not all(k in seqs for k in prefix):
            prefix = ()
        whole, before = value(prefix + (key,)), value(prefix)
        out[key] = {s: round(v - before.get(s, 0)) for s, v in whole.items() if round(v - before.get(s, 0)) > 0}
    return out


def summarise(moves, slots, grip):
    """The ranking summaries of one grip, from its slots."""
    if grip == "L1":
        return chain_summary([slots[k] for k in ("left_1", "left_2", "left_3", "left_4", "left_5", "left_6")
                              if k in slots])
    slim = {k: s["_slim"] for k, s in moves.items()}
    c = MS.chain_metrics(slim, "") or {}
    chain = [slots[k] for k in ("r1_1", "r1_2", "r1_3", "r1_4", "r1_5", "r1_6") if k in slots]
    out = chain_summary(chain)
    if c:
        rec = [x for x in c["dodge_after_hit"] if x is not None]
        out.update(r1_recovery=round(sum(rec) / len(rec), 1) if rec else None,
                   r1_chain_dps=c.get("mv_per_sec_full_chain") or c.get("mv_per_sec_first3"),
                   stamina_per_r1=round(sum(c["stamina_per_step"]) / len(c["stamina_per_step"]), 1))
    out["r1_reach"] = (slots.get("r1_1") or {}).get("reach")
    return out


def fun_of(mechs, share):
    """(fun score, rarest mechanic) over a vector's mechanics."""
    if not mechs:
        return 0.0, None
    m = min(mechs, key=lambda k: share.get(k, 1.0))
    return round(1.0 - share.get(m, 1.0), 4), m


def role_affinities_1h(recs):
    """The affinities `er-mechanics-class-roles.role_affinities` scores a weapon on (each one with a
    status, and the best statusless one), of one base weapon's records `recs`, kept where the
    one-handed record is wieldable: a powerstance holds both weapons one-handed
    (`can_powerstance` refuses two-handing)."""
    affs, by = COMBO._class_roles().role_affinities(recs)
    return [aff for aff in affs if any(r["grip"] == "1H" and r["wieldable"] for r in by[aff])]


def powerstance_affinities(same, best, right_roles, left_roles):
    """[(right affinity, left affinity)] to embed one powerstance pair on: `best` (each weapon's
    best one-handed damage affinity, the record every pair always had, wieldable or not) first,
    then every pair of the two weapons' role affinities (`role_affinities_1h`), so a set's
    powerstance is scored on the affinities its hands are picked on rather than on Heavy alone.
    A weapon with itself (`same`) is one copy's affinity in both hands, so only matching pairs.
    Bounded by the role affinities, which are wieldable ones only: measured on the 2026-10-19
    heavy-kit stat block, 25,428 more records over 5,031 pairs (30,459 in all; the embedding
    grew from 51 MB to 124 MB)."""
    out = [tuple(best)]
    for ra in right_roles:
        for la in left_roles:
            if (ra, la) not in out and (not same or ra == la):
                out.append((ra, la))
    return out


#: Runs of neighbouring items each `pooled` worker takes, about: few enough that a run shares its
#: weapons' caches, enough that a slow run does not leave the other workers idle at the end.
POOL_RUNS = 8
#: What the pool workers read (`_weapon_work`, `_pair_work`, `_ash_work`), set before they fork:
#: the `Embed`, the mechanic shares and, in a worker, the reaches it has already handed back.
_JOB = {}


def _reach_news(e):
    """{reach key: entries} this worker measured since its last call. A worker writes no reach file
    (`_worker_start` clears its `reach_path`); `pooled` merges these into the caller's, which the
    caller writes, so the file has one writer."""
    seen = _JOB.setdefault("seen", {})
    out = {}
    for k, v in e.reach.items():
        if seen.get(k) != v:
            out[k] = seen[k] = dict(v) if isinstance(v, dict) else v
    return out


def _weapon_work(b):
    """([(record, (affinity, 1H damage) when its 1H record is wieldable)], reaches measured): one
    base weapon on every affinity, the records in the order one process appends them."""
    e, share = _JOB["e"], _JOB["share"]
    out = []
    for aff in OPT.affinities(e.art, b):
        try:
            vec = e.weapon_vector(b, aff)
        except Exception as ex:  # keep the sweep going; the failure is recorded
            print(f"{e.art.names[b]} {aff}: {ex!r}", file=sys.stderr)
            continue
        mechs = list(e.option_mechanics(b, aff))
        fun, m = fun_of(mechs, share)
        one = vec["1H"]
        best = (aff, one["damage"] or 0) if one["wieldable"] else None
        for grip, rec in vec.items():
            rec["mechanics"] = mechs
            rec["fun"], rec["fun_mechanic"] = fun, m
            out.append((rec, best if grip == "1H" else None))
    return out, _reach_news(e)


def _pair_work(job):
    """([(record, on a role affinity pair)], reaches measured): one powerstance pair on each of
    its affinity pairs."""
    e = _JOB["e"]
    r, l, affs, best = job
    out = []
    for ra, la in affs:
        try:
            rec = e.powerstance(r, ra, l, la)
        except Exception as ex:
            print(f"powerstance {e.art.names[r]} {ra} + {e.art.names[l]} {la}: {ex!r}", file=sys.stderr)
            continue
        if rec:
            out.append((rec, (ra, la) != best))
    return out, _reach_news(e)


def _ash_work(x):
    """(ash vector or None, reaches measured) of one ash-table row."""
    e = _JOB["e"]
    try:
        rec = e.ash_vector(x, _JOB["share"])
    except Exception as ex:
        print(f"ash {x['ash']} on {x['weapon']}: {ex!r}", file=sys.stderr)
        rec = None
    return rec, _reach_news(e)


def _worker_start(cores, counter):
    """A pool worker: a core of its own (`er-mechanics-ash-interrupt._worker_init`) and no reach file."""
    COMBO._ai()._worker_init(cores, counter)
    e = _JOB["e"]
    e.reach_path = None
    _JOB["seen"] = {k: dict(v) if isinstance(v, dict) else v for k, v in e.reach.items()}


def pooled(e, fn, items, jobs):
    """`fn` over `items`, results in the order of `items`, on `jobs` forked workers each pinned to
    a core of its own, as the loadout's pools are (`er-mechanics-ash-interrupt._worker_init`).
    `fn` returns (result, reaches measured); the reaches go into `e.reach` here and its file is
    written by this process alone. `jobs` 1 runs in this process. A result is the one the serial
    loop computes: the caches an `Embed` keeps per item (`_moves`, `_ps_rows`, the reaches) hold
    the same values whichever process fills them."""
    AI = COMBO._ai()
    cpus = AI.allowed_cpus()
    workers = cpus[1:] or cpus
    n = max(1, min(jobs, len(items), len(workers)))
    # The first item here either way: whatever it reads on first use (tables, the status target's
    # defenders) the forked workers then inherit instead of each reading it again.
    for x in items[:1] if n > 1 else items:
        yield fn(x)[0]
    if n <= 1:
        return
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    # The status sequence memo shared by the workers (`er-mechanics-status.share_seq_memo`): the
    # pairs repeat the build-up sequences one process would have simulated once.
    ST.share_seq_memo(ctx)
    # Neighbouring items share their weapons (the pairs run right weapon by right weapon), so each
    # worker takes runs of them and reads a weapon's animations and moveset once rather than once
    # per pair: `POOL_RUNS` runs a worker.
    rest = items[1:]
    chunk = max(1, len(rest) // (n * POOL_RUNS))
    with ctx.Pool(n, initializer=_worker_start, initargs=(workers, ctx.Value("i", 0))) as pool:
        for res, news in pool.imap(fn, rest, chunksize=chunk):
            for k, v in news.items():
                if isinstance(v, dict):
                    e.reach.setdefault(k, {}).update(v)
                else:
                    e.reach[k] = v
            if news:
                e.save_reach()
            yield res


#: The arguments an embedding reused by `--reuse` must have been made with, besides its stats.
REUSE_ARGS = ("weapons", "no_reach", "no_powerstance", "fight")


def stamp_path(out: Path) -> Path:
    """The file beside an embedding naming the code, stats and arguments that made it (`reusable`)."""
    return out.with_name(out.name + ".stamp.json")


def code_stamp() -> dict:
    """{file name: sha256} of every module of this directory the process has loaded: the code
    behind an embedding's weapon and powerstance records."""
    out = {}
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and Path(f).resolve().parent == HERE:
            out[Path(f).name] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def stamp_doc(a, stats, code) -> dict:
    return {"stats": stats_key(stats), "args": {k: getattr(a, k, None) for k in REUSE_ARGS}, "code": code}


def reuse_refusal(a, stats, doc) -> str | None:
    """Why a stamp `doc` (`stamp_doc`) does not vouch for an embedding at `stats` made with `a`,
    or None when it does: the same stats, the same `REUSE_ARGS`, and every module it names
    unchanged on disk."""
    if not doc or not doc.get("code"):
        return "no code recorded"
    want = stamp_doc(a, stats, None)
    if doc.get("stats") != want["stats"]:
        return f"stats {doc.get('stats')}"
    if doc.get("args") != want["args"]:
        return f"arguments {doc.get('args')}"
    stale = [f for f, h in doc["code"].items()
             if not (HERE / f).exists() or hashlib.sha256((HERE / f).read_bytes()).hexdigest() != h]
    return f"changed code {', '.join(stale)}" if stale else None


def reusable(a, stats, path: Path):
    """The embedding at `path` when its stamp (`stamp_path`) vouches for it (`reuse_refusal`), else
    None with the reason on stderr. The pipeline's step 6 reuses step 2's: the two runs differ only
    in `--ash-rows`, which nothing but the ash vectors reads, so the weapon and powerstance records
    are the ones step 6 would compute again (equal in both files of the 2026-10-19 heavy-kit pass,
    37 MB and 112 MB of JSON, and about 90 s of step 6's 108)."""
    try:
        doc = json.loads(stamp_path(path).read_text())
    except (OSError, ValueError):
        doc = None
    why = reuse_refusal(a, stats, doc)
    if why:
        print(f"reuse: not {path} ({why}); computing every record", file=sys.stderr, flush=True)
        return None
    return json.loads(path.read_text())


def build(a, stats):
    t0 = time.time()
    e = Embed(stats, a.reach_cache, not a.no_reach)
    print(f"tables {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
    share, labels, n_opts = e.shares()
    print(f"mechanics over {n_opts} options {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
    jobs = getattr(a, "jobs", 1) or 1
    # Read once here rather than once in every forked worker.
    e.sa()
    _JOB.update(e=e, share=share)
    only = {w.strip() for w in a.weapons.split(",")} if a.weapons else None
    base = reusable(a, stats, a.reuse) if getattr(a, "reuse", None) else None
    if base is not None:
        weapons, pstance = base["weapons"], base["powerstance"]
        print(f"reuse: {len(weapons)} weapon and {len(pstance)} powerstance records from {a.reuse} "
              f"{time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    else:
        weapons, pstance = build_weapons(a, e, only, jobs, t0)
    ashes = []
    seen = set()
    for path in a.ash_rows or ():
        todo = []
        for x in json.loads(path.read_text()):
            # A stance skill's buttons are rows of their own (`variant`); each is a vector.
            k = (x["weapon"], x.get("affinity", "Keen"), x["ash"], x["grip"], x.get("variant"))
            if (only and x["weapon"] not in only) or k in seen:
                continue
            seen.add(k)
            todo.append(x)
        ashes += [v for v in pooled(e, _ash_work, todo, jobs) if v is not None]
        print(f"ash vectors {len(ashes)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    ST.release_seq_memo()
    if e._reach_dirty:
        e.save_reach(force=True)
    return {"stats": stats, "stats_key": stats_key(stats), "fun_max_share": FUN_MAX_SHARE,
            "options": n_opts,
            "mechanics": {k: {"share": round(v, 5), "label": labels.get(k)} for k, v in sorted(share.items())},
            "weapons": weapons, "powerstance": pstance, "ashes": ashes}


def build_weapons(a, e, only, jobs, t0):
    """(weapon records, powerstance records): every base weapon on every affinity, then every
    powerstance pair, on `jobs` workers (`pooled`), in the order one process makes them."""
    weapons, best_aff, by_base = [], {}, collections.defaultdict(list)
    todo = [b for b in e.base if not only or e.art.names[b] in only]
    for i, (b, recs) in enumerate(zip(todo, pooled(e, _weapon_work, todo, jobs))):
        for rec, one in recs:
            weapons.append(rec)
            by_base[b].append(rec)
            if one and (b not in best_aff or one[1] > best_aff[b][1]):
                best_aff[b] = one
        print(f"[{i + 1}/{len(todo)}] {e.art.names[b]} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    pstance = []
    if not a.no_powerstance:
        cand = [(r, l) for r in todo for l in todo if PG.can_powerstance(e.pg, r, l)]
        role = {b: role_affinities_1h(recs) for b, recs in by_base.items()}
        jobs_in = []
        for r, l in cand:
            raff = best_aff.get(r, (OPT.affinities(e.art, r)[0],))[0]
            laff = best_aff.get(l, (OPT.affinities(e.art, l)[0],))[0]
            jobs_in.append((r, l, powerstance_affinities(r == l, (raff, laff), role.get(r, ()), role.get(l, ())),
                            (raff, laff)))
        n_extra = 0
        # Minutes with no `[i/N]` line otherwise (3m17s on 2026-10-19): `  i/N pairs` on stderr.
        progress = _load("er_mechanics_progress", "er-mechanics-progress.py")
        tick = progress.Count(len(jobs_in), "pairs")
        for recs in pooled(e, _pair_work, jobs_in, jobs):
            for rec, extra in recs:
                pstance.append(rec)
                n_extra += extra
            tick()
        print(f"powerstance {len(pstance)} records over {len(cand)} pairs ({len(pstance) - n_extra} on the "
              f"best-damage affinities, {n_extra} more on the role affinities) {time.time() - t0:.0f}s",
              file=sys.stderr, flush=True)
    return weapons, pstance


def same_moves(a, b, keys=("anim", "startup", "active", "last_hit", "roll_cancel", "next_cancel",
                           "stamina", "mv", "hits")):
    return all(a.get(k) == b.get(k) for k in keys)


def reach_coverage(e):
    """{(weapon, grip): reason} of every pool weapon (`Embed.base`) whose R1 #1 has no measured
    world reach for a reason that is not inherent (`er-mechanics-reach.r1_reach`), plus the
    inherent ones and the measured count. The pool is every base weapon, not one stat block's
    wieldable share: reach does not depend on stats, so a weapon outside one build's
    requirements is in another's."""
    RC = ATK._reach()
    rc = RC.Reach(None)
    gaps, inherent, measured = {}, {}, 0
    for b in e.base:
        for grip in ("one", "both"):
            reach, why, fixed = RC.r1_reach(rc, b, grip)
            if reach is not None:
                measured += 1
            elif fixed:
                inherent[(e.art.names[b], grip)] = why
            else:
                gaps[(e.art.names[b], grip)] = why
    return gaps, inherent, measured


def selftest():
    e = Embed(DEFAULT_STATS, None, reach=False)
    ok = True
    # Every pool weapon's R1 #1 has a measured reach, or a data-derived reason no measurement can
    # give one. Before the weapon-model rigs were posed, 17 whips, flails and rigged weapons (and
    # Dancing Blade of Ranah's 1H R1, an import entry with no clip of its own) had none, and
    # `ChainTrades` credited their strings at the default exchange distance.
    gaps, inherent, measured = reach_coverage(e)
    good = not gaps
    print(f"{'ok' if good else 'FAIL'}: R1 #1 world reach measured for {measured} weapon grips, "
          f"{len(inherent)} with an inherent reason ({collections.Counter(inherent.values()).most_common(3)}), "
          f"{len(gaps)} unmeasured: {sorted(gaps.items())[:10]}")
    ok &= good
    rc = ATK._reach().Reach(None)
    reach = {n: ATK._reach().r1_reach(rc, e.art.find_weapon(n, "Standard"), "one")[0]
             for n in ("Whip", "Chainlink Flail", "Nox Flowing Sword")}
    good = all(v is not None for v in reach.values()) and reach["Whip"] > reach["Chainlink Flail"]
    print(f"{'ok' if good else 'FAIL'}: rigged weapons posed through their own anibnd, whip outreaches "
          f"flail ({reach})")
    ok &= bool(good)
    sham, scim, falc = (e.art.find_weapon(n, "Standard") for n in ("Shamshir", "Scimitar", "Falchion"))
    ms, mc = e.moveset(sham), e.moveset(scim)
    r1 = [k for k in ms["1H"] if k.startswith("r1_")]
    good = r1 and set(r1) == {k for k in mc["1H"] if k.startswith("r1_")} and all(
        same_moves(ms["1H"][k], mc["1H"][k]) for k in r1) and all(
        ms["1H"][k]["anim"].startswith("a156_") for k in r1)
    print(f"{'ok' if good else 'FAIL'}: Shamshir and Scimitar share R1 slots {r1} (a156)")
    ok &= bool(good)
    ps = e.powerstance(sham, "Keen", sham, "Keen")
    pf = e.powerstance(falc, "Keen", falc, "Keen")
    good = ps and pf and set(ps["slots"]) == set(pf["slots"]) and all(
        same_moves(ps["slots"][k], pf["slots"][k]) for k in ps["slots"]) and not any(
        s["anim"].startswith("a156_") for s in ps["slots"].values())
    print(f"{'ok' if good else 'FAIL'}: Shamshir powerstance equals Falchion's "
          f"({sorted(ps['slots']) if ps else None})")
    ok &= bool(good)
    # Powerstance status HP rides the hitting hand: a Blood Dagger right and a Heavy Parrying Dagger
    # left bleed only on slots with a right-hand hit, and somewhere on the L1 string once its hits
    # add up to a proc (`chain_status`); both Heavy bleed nowhere. (A record keys its hands by
    # weapon, so one weapon in both hands is one affinity, as the sweep pairs it.)
    dag, pdag = e.art.find_weapon("Dagger", "Standard"), e.art.find_weapon("Parrying Dagger", "Standard")
    bh, hh = e.powerstance(dag, "Blood", pdag, "Heavy"), e.powerstance(dag, "Heavy", pdag, "Heavy")
    got = {k: bool((s.get("status_hp") or {}).get("bleed")) for k, s in bh["slots"].items()}
    right = {k: "right" in s["hands"] for k, s in bh["slots"].items()}
    good = (all(right[k] for k, v in got.items() if v) and any(got[k] for k in got if k.startswith("dual_"))
            and not any(s.get("status_hp") for s in hh["slots"].values()))
    print(f"{'ok' if good else 'FAIL'}: powerstance bleed on the Blood hand's hits only ({got})")
    ok &= bool(good)
    # Role affinities: Blood and Cold carry a status, Heavy is the best statusless one, Keen is
    # beaten by it, and Poison's one-handed record is not wieldable.
    def rec(aff, grip, dmg, status=(), wieldable=True):
        return {"affinity": aff, "grip": grip, "damage": dmg, "status": list(status), "wieldable": wieldable,
                "slots": {}}
    recs = [rec("Heavy", "1H", 100), rec("Keen", "1H", 90), rec("Blood", "1H", 80, ["bleed"]),
            rec("Cold", "1H", 80, ["frost"]), rec("Poison", "1H", 80, ["poison"], wieldable=False),
            rec("Poison", "2H", 80, ["poison"])]
    got = (sorted(role_affinities_1h(recs)),
           powerstance_affinities(False, ("Heavy", "Keen"), ["Blood", "Heavy"], ["Heavy"]),
           powerstance_affinities(True, ("Heavy", "Heavy"), ["Blood", "Heavy"], ["Blood", "Heavy"]))
    want = (["Blood", "Cold", "Heavy"], [("Heavy", "Keen"), ("Blood", "Heavy"), ("Heavy", "Heavy")],
            [("Heavy", "Heavy"), ("Blood", "Blood")])
    print(f"{'ok' if got == want else 'FAIL'}: powerstance role affinities {got}")
    ok &= got == want
    # Per-hit trade fields (`trade_fields`): one entry per hit, the first at the slot's startup,
    # poise in menu units times the AtkParam row's saRate, and the TAE 795 windows on the slot's
    # clock, the first opening at the slot's `hyperarmor` frame. Giant-Crusher 2H R1 #1 carries one,
    # the Shamshir's R1 none; the L1 and dual slots follow the same shape.
    gc = e.moveset(e.art.find_weapon("Giant-Crusher", "Standard"))
    g1, s1 = gc["2H"]["r1_1"], ms["1H"]["r1_1"]
    sa = e.sa()(g1["_row"]["atk_row"])
    lft = next(iter(ms["L1"].values()))
    duals = [s for s in ps["slots"].values() if "hit_at" in s]
    got = (len(g1["hit_at"]) == g1["hits"], g1["hit_at"][0] == g1["startup"],
           g1["hit_poise"][0] == rnd(g1["_row"]["poise_damage"] * _intr().POISE_MENU * sa, 2),
           bool(g1["ha"]) and g1["ha"][0][0] == g1["hyperarmor"] and len(g1["ha"][0]) == 6,
           s1["ha"] == [], lft["hit_at"][0] == lft["startup"],
           bool(duals) and all(len(s["hit_at"]) == s["hits"] == len(s["hit_poise"]) for s in duals))
    good = all(got)
    print(f"{'ok' if good else 'FAIL'}: trade fields per hit and per window {got}, Giant-Crusher 2H R1 "
          f"{g1['hit_at']} {g1['hit_poise']} {g1['ha']}")
    ok &= good
    share, labels, n = e.shares(with_ashes=False)
    fang = e.art.find_weapon("Venomous Fang", "Standard")
    mf = e.option_mechanics(fang, "Standard")
    deadly = [k for k in mf if k.startswith("status:poison")]
    plain = e.option_mechanics(scim, "Keen")
    ff, fm = fun_of(list(mf), share)
    pf_, pm = fun_of(list(plain), share)
    good = deadly and fm in deadly and share[fm] <= FUN_MAX_SHARE and ff > pf_
    print(f"{'ok' if good else 'FAIL'}: Venomous Fang fun {ff} ({fm}, share {share.get(fm)}) > "
          f"Keen Scimitar fun {pf_} ({pm}, share {share.get(pm)})")
    ok &= bool(good)
    # `pooled`: forked workers hand back what one process computes, in the order of the items.
    _JOB.update(e=e, share=share)
    # Dagger first, in this process; Uchigatana's build-up sequences are none of its own.
    few = [dag, e.art.find_weapon("Uchigatana", "Standard"), sham, scim]
    serial = list(pooled(e, _weapon_work, few, 1))
    ST._SEQ.clear()
    forked = list(pooled(e, _weapon_work, few, 3))
    memo = ST._SEQ
    shared = (len(memo.shared), len(memo.local)) if isinstance(memo, ST.SharedMemo) else None
    ST.release_seq_memo()
    # The workers' sequences reach the shared memo: it holds more than this process computed.
    good = (json.dumps(serial) == json.dumps(forked) and len(serial) == len(few) and bool(shared)
            and shared[0] > shared[1])
    print(f"{'ok' if good else 'FAIL'}: pooled weapon records on 3 workers equal one process's, in order, "
          f"through a status memo they share (shared, this process's: {shared})")
    ok &= good
    # `reuse_refusal`: a stamp vouches only for the same stats, arguments and unchanged modules.
    args = argparse.Namespace(weapons=None, no_reach=False, no_powerstance=False, fight="duel")
    code = code_stamp()
    doc = stamp_doc(args, DEFAULT_STATS, code)
    other = dict(DEFAULT_STATS, str=DEFAULT_STATS["str"] + 1)
    got = (reuse_refusal(args, DEFAULT_STATS, doc),
           reuse_refusal(args, other, doc) is not None,
           reuse_refusal(argparse.Namespace(**{**vars(args), "fight": "invasion"}), DEFAULT_STATS, doc) is not None,
           reuse_refusal(args, DEFAULT_STATS, {**doc, "code": {**code, Path(__file__).name: "0" * 64}}),
           reuse_refusal(args, DEFAULT_STATS, {**doc, "code": {}}), reuse_refusal(args, DEFAULT_STATS, None),
           Path(__file__).name in code and "er-mechanics-ash-table.py" in code)
    good = (got[0] is None and got[1] and got[2] and got[3] == f"changed code {Path(__file__).name}"
            and got[4] == got[5] == "no code recorded" and got[6])
    print(f"{'ok' if good else 'FAIL'}: reuse stamp refuses other stats, arguments and code {got}")
    ok &= good
    print("selftest", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", type=Path, help="planner build JSON; its stats are the stat block")
    ap.add_argument("--stats", help="override, e.g. str=18,dex=80,int=7,fth=8,arc=9")
    ap.add_argument("--ash-rows", type=Path, nargs="+",
                    help="er-mechanics-ash-table.py --json sweeps at this stat block; a row two share is kept once")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reach-cache", type=Path,
                    help="world reaches kept across runs (default: embed-reach-<code hash>.json in the "
                         "planner cache, shared by every stat block)")
    ap.add_argument("--no-reach", action="store_true")
    ap.add_argument("--no-powerstance", action="store_true")
    ap.add_argument("--weapons", help="comma-separated weapon names: embed only these (mechanic shares stay game-wide)")
    ap.add_argument("--fight", choices=("duel", "invasion"),
                    help="fight model status is valued in (er-mechanics-buffs FIGHT_KINDS pressure)")
    ap.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)),
                    help="pool workers for the weapons, powerstance pairs and ashes (`pooled`; at most the "
                         "allowed cores less one; 1: in this process)")
    ap.add_argument("--reuse", type=Path,
                    help="an embedding of the same stats whose stamp (`<file>.stamp.json`) names this code: "
                         "its weapon and powerstance records are taken as they are and only the ashes made")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    AT.FIGHT = a.fight
    if a.selftest:
        return selftest()
    if not a.out:
        ap.error("--out is required")
    stats = dict(DEFAULT_STATS)
    if a.build:
        bs = json.loads(a.build.read_text())["stats"]
        stats = {k: bs[k] for k in AR.STATS}
    if a.stats:
        stats.update({k: int(v) for k, v in (p.split("=") for p in a.stats.split(","))})
    if a.reach_cache is None and not a.no_reach:
        a.reach_cache = default_reach_cache()
    out = build(a, stats)
    # The old stamp goes first and the new one comes after the embedding, so a stamp never vouches
    # for a file that was not written whole.
    stamp_path(a.out).unlink(missing_ok=True)
    a.out.write_text(json.dumps(out, separators=(",", ":")))
    stamp_path(a.out).write_text(json.dumps(stamp_doc(a, stats, code_stamp()), indent=1))
    c = collections.Counter(r["grip"] for r in out["weapons"])
    print(f"weapon vectors {len(out['weapons'])} ({dict(c)}), powerstance {len(out['powerstance'])}, "
          f"ash {len(out['ashes'])} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
