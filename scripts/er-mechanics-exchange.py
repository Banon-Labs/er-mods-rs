#!/usr/bin/env python3
"""Startup, hyperarmor trades and stamina of one attack slot against the PvP corpus's own attacks.

    python3 scripts/er-mechanics-exchange.py --rl 150 --pool                 # the opponent pool
    python3 scripts/er-mechanics-exchange.py --rl 150 --weapon Giant-Crusher --grip both
    python3 scripts/er-mechanics-exchange.py --selftest

Write-up: `docs/er-mechanics/exchange.md`. `er-builds-pvp.py` shows startup, hyperarmor and
stamina per slot but its score reads none of them; this module turns each into a score factor
measured against the attacks the RL window's PvP builds actually carry.

The opponent pool (`opponent_pool`): every PvP build of the RL window (the same filter as
`er-builds-pvp.is_pvp`), deduplicated on (user, equipped tokens) as `er-builds-embed.load_corpus`
does, contributes one attack: the R1 #1 of its first right-hand weapon that has one, in the
build's grip, from `er-mechanics-attacks.weapon_attacks`. Each build keeps its own armor poise,
so the pool is adoption weighted and poise and weapon stay paired. Its per-build numbers:

* first hit frame (real frames, main judge or an earlier separate sweep hit),
* PvP poise damage in menu units: `poise_damage x 10 x FinalDamageRateParam.saRate`, the same
  expression `er-builds-pvp.slot_hit` uses,
* hyperarmor windows (TAE 795): frames, bonus in menu units, ToughnessParam `unk1`.

The exchange (`slot_exchange`): both players start an attack on the same frame. Whoever's strike
frame is earlier strikes. The strike frame (`strike_frame`, both sides) is the frame the hitbox
first touches a defender `STRIKE_DISTANCE_M` (2.5 m, `INFERRED`) straight ahead, from
`er-mechanics-reach` `front_contact_frame_real`; when the reach module has no such contact, the
first hit frame plus the weapon class's median delay from first hit to that contact
(`er-mechanics-reach.class_fallback`, `INFERRED`), never the bare first hit, which would win
exchanges early. A horizontal sweep opens its window off to the side and reaches the front
later: Giant-Crusher 2H R1 17.9 at window open, 18.9 at 2.5 m; Greatsword 2H R1 16.7 -> 18.2. the struck side is interrupted only when that hit's PvP poise
damage (times the struck side's `unk1` when its hyperarmor window covers the frame) reaches its
armor poise plus the window's bonus; otherwise both hits land. This is
`er-mechanics-frame-advantage.trade` over the whole pool. The attacker's own armor poise is not
known for a sweep build, so it is drawn from the same corpus poise distribution.

    win   = I hit first and interrupt them
    loss  = they hit first and interrupt me
    trade = both hits land (same frame, or the first hit did not break the struck side)
    net   = P(win) - P(loss)                            in [-1, 1]
    f_exchange = 1 + EXCHANGE_WEIGHT * net
    f_startup  = 1 + EXCHANGE_WEIGHT * (P(first) - P(second))   poise ignored: first hit wins
    f_hyper    = f_exchange / f_startup                 what poise and hyperarmor change

`f_startup * f_hyper == f_exchange`, so the score multiplies `f_exchange` once (or the two parts,
never all three).

Stamina (`stamina_budget`, exchange.md section 3): the share of the slot's time-limited damage
rate that one stamina bar plus regeneration sustains over a fight window. The attacker repeats
the slot from a full bar (corpus median `computed.maxStamina`), chaining at its commitment `T`
while the bar allows and keeping enough to roll out afterwards; regeneration is 45/s
(`VERIFIED`, `PlayerIns::GetStaminaRecoverySpeed`), scaled per frame by the animation's TAE 225
`SetSPRegenRatePercent` (`VERIFIED`: 0% over most of every attack clip, 20% while guarding):

    N(W)      = swings started inside the window W (the last one pro rata)
    f_stamina = N(W) * T / W                          in (0, 1], no exponent, no clamp

`rate x f_stamina` is then the damage per second the build can keep up over W, which is where
damage per stamina enters: once the bar is spent, the swing rate is set by cost / regen.

Labels: `VERIFIED` = regulation value or traced EXE code, `MEASURED` = computed here from the
regulation and the corpus, `INFERRED` = a modelling choice. Every weight below is `INFERRED`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/er-build-planner"


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ATK = _sibling("er-mechanics-attacks")
EMBED = _sibling("er-builds-embed")
PR = ATK.PR

#: Internal poise units are menu units / 10 (docs/er-mechanics/attacks.md section 2).
POISE_MENU = 10.0
#: The opponent's attack in the exchange (INFERRED: the opener every moveset has; which attack a
#: real opponent throws is not in the corpus).
OPPONENT_SLOT = "r1_1"
#: Planner tags that mark a build as made for player fights (`er-builds-pvp.PVP_TAGS`).
PVP_TAGS = {"Invasions", "Duels", "Co-op/Gank", "2v2", "Ladder", "Fishing"}
#: Right-hand positions of the active weapon set (`er-builds-adoption-gap.py`: seals and
#: shields sit at 3-5).
RIGHT_HAND = (0, 1, 2)
LEFT_HAND = (3, 4, 5)
#: `EquipParamWeapon.wepType` of the pure catalysts: 57 staves, 61 seals.
CATALYST_TYPES = (57, 61)

#: Weights, all `INFERRED`. The exchange factor spans 1 -+ EXCHANGE_WEIGHT, the same span as the
#: frame-advantage factor at +-30 frames (`er-builds-pvp.SCORE_ADV_WEIGHT`).
EXCHANGE_WEIGHT = 0.25
#: The retired swings-per-bar factor `clamp((per bar / pool per bar) ** STAMINA_EXP, STAMINA_CLAMP)`,
#: kept only as `stamina_factor_swings` for comparison and because `er-builds-pvp.py` still names
#: both constants. Nothing in the score reads them.
STAMINA_EXP = 0.25
STAMINA_CLAMP = (0.8, 1.1)

#: Stamina regeneration (docs/er-mechanics/exchange.md section 3). `FUN_1404016d0` (1.16.2) adds
#: `GetStaminaRecoverySpeed x SPRegenRatePercent / 100 x staminaRecoveryModifier` scaled by the
#: frame's `FD4Time` each tick, carrying the fraction, unless `ChrCtrlModifier` `actionFlags` bit
#: 0x40 is set. `PlayerIns::GetStaminaRecoverySpeed` 0x1406566b0 returns the float at 0x143b33c08
#: (45.0, its only reader) plus the SpEffect `staminaRecoverChangeSpeed` sum; NpcParam
#: `staminaRecoverBaseVel` feeds only `EnemyIns::GetStaminaRecoverySpeed` 0x1404cf9e0, never a
#: player. `VERIFIED`; that `FD4Time` counts seconds is `INFERRED`.
STAMINA_REGEN_PER_S = 45.0
#: TAE event 225 `SetSPRegenRatePercent`: the inline case at 0x14042e3b1 of `ExecuteThreadOne`
#: stores Args[0] into `ChrCtrlModifier+0x14` (`SPRegenRatePercent`), which
#: `ChrCtrlModifierData::Reset` 0x1403c3b60 sets back to 100 every frame (`VERIFIED`).
TAE_SP_REGEN = 225
#: Lowest stamina `CSChrDataModule::SetStamina` 0x140438490 stores (-0x32, `VERIFIED`): an action
#: started with 1 stamina left can dig down to -50, and the hole must regenerate before a roll.
STAMINA_FLOOR = -50
#: Stamina the entry of an entry slot costs on top of the attack: HKS `common_define.hks`
#: `STAMINA_REDUCE_ROLLING` -12, `STAMINA_REDUCE_BACKSTEP` -8, `STAMINA_REDUCE_JUMP` -10, charged by
#: `AddStamina` in `ExecEvasion` / `ExecJump` (`VERIFIED`).
ENTRY_STAMINA = {"roll_r1": 12, "bstep_r1": 8, "jump_r1": 10, "jump_r2": 10}
#: Rolling needs stamina above 0 (`GetEvasionRequest` compares `env(1001)` with `STAMINA_MINIMUM`
#: 0, `VERIFIED`), so an attacker keeps a roll after a swing while stamina before it exceeds the
#: cost. Holding that reserve is the attacker's policy in the fight window (`INFERRED`).
#: The window over which damage is summed (`INFERRED`: a model length, not a weight on the result;
#: exchange.md section 3 prints 5, 10 and 20 s).
FIGHT_WINDOW_S = 10.0

POOL_VERSION = 5                  # 5: 'offhand', the left hand per profile
#: Slots whose entry is a roll or backstep: their invincibility is not modelled, so their
#: exchange is neutral. Crouch and running attacks have no invincibility (`slot_exchange`).
INVINCIBLE_ENTRY_SLOTS = ("roll_r1", "bstep_r1")


# --------------------------------------------------------------------------------------------
# the opponent pool

def is_pvp(build: dict) -> bool:
    """`er-builds-pvp.is_pvp`, restated so this module does not import the ranking."""
    if build.get("isPvE") is True:
        return False
    return build.get("isPvE") is False or bool(set(build.get("tags") or []) & PVP_TAGS)


def plain_name(name: str) -> str:
    """The planner writes `Miséricorde`; the regulation row names drop the accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))


def weapon_ids() -> dict:
    """{regulation row name: base weapon id} (lowest id with `id % 10000 == 0`)."""
    out = {}
    for wid, name in sorted(PR.row_names("EquipParamWeapon").items()):
        if name and wid % 10000 == 0 and name not in out:
            out[name] = wid
    return out


def _right_hand(build: dict, positions=RIGHT_HAND) -> list[str]:
    active = EMBED.active_set(build, "weapons")
    worn = []
    for s in (build.get("inventory") or {}).get("slots") or []:
        es = s.get("equipSet")
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get("equipIndex")
        if pos in positions and s.get("name"):
            worn.append((pos, plain_name(s["name"])))
    return [n for _, n in sorted(worn)]


class SaRates:
    """`FinalDamageRateParam[AtkParam_Pc.finalDamageRateId].saRate` per AtkParam row (`VERIFIED`
    consumer 0x140486bf0, docs/er-mechanics/attacks.md section 2)."""

    def __init__(self):
        files = PR.load(None)
        atk, _, _ = PR.rows(PR.param_bytes(files, "AtkParam_Pc"), ["finalDamageRateId"])
        fr, _, _ = PR.rows(PR.param_bytes(files, "FinalDamageRateParam"), ["saRate"])
        self.atk = {r["id"]: r["finalDamageRateId"] for r in atk}
        self.fr = {r["id"]: r["saRate"] for r in fr}

    def __call__(self, atk_row) -> float:
        fid = self.atk.get(atk_row, -1)
        return self.fr.get(fid, 1.0) if fid is not None and fid >= 0 else 1.0


def hyper_windows(atk: dict, shift: float = 0.0) -> list[tuple]:
    """[(start, end, bonus in menu units, PvP poise-damage multiplier)] of one attack's TAE 795
    windows, real frames, moved by `shift`."""
    return [(h["frames"][0] + shift, h["frames"][1] + shift, h["poise_bonus"] * POISE_MENU,
             h.get("pvp_poise_damage_taken") or 1.0) for h in atk.get("hyperarmor") or []]


def first_hit(atk: dict) -> float | None:
    """First hit frame: the main judge's first window or an earlier separate sweep hit (the rule
    `er-builds-pvp.py` applies when it sums `other_hitboxes`)."""
    frames = [w[0] for w in atk.get("hit_windows") or []]
    frames += [o["frames"][0] for o in atk.get("other_hitboxes") or [] if o.get("sweep_hit", True)]
    return min(frames) if frames else None


def stamina_total(reg, weapon_id: int, atk: dict) -> int:
    """Stamina one use of the attack charges. `FUN_1404428f0` charges `stamina_cost` each time an
    AttackBehavior event creates a hitbox (`VERIFIED`, attacks.md section 3), so the main judge's
    cost is counted once per window and every extra damaging hitbox adds its own (the summation
    is `INFERRED` from that; 53 of 1515 one-handed slots have more than one event)."""
    total = atk["stamina_cost"] * max(1, len(atk.get("hit_windows") or []))
    for o in atk.get("other_hitboxes") or []:
        n = ATK.attack_numbers(reg, weapon_id, o["judge"])
        if n:
            total += n["stamina_cost"]
    return total


def attack_profile(reg, sa: SaRates, weapon_id: int, two: bool, slot: str = OPPONENT_SLOT) -> dict | None:
    """The exchange numbers of one weapon's slot, or None when it has no hit."""
    rows = ATK.weapon_attacks(reg, weapon_id, "both" if two else "one")
    key = ("2h_" if two else "") + slot
    atk = next((r for r in rows if r["slot"] == key), None)
    if atk is None or first_hit(atk) is None:
        return None
    rate = sa(atk["atk_row"])
    # The opponent strikes at the same point the scored slot does (`strike_frame`): its front
    # contact `STRIKE_DISTANCE_M` ahead when the reach module has a pose for it, else its first hit.
    reach = _reach_module().reach_summary(weapon_id, "both" if two else "one").get(key) or {}
    fc = reach.get("front_contact_frame_real")
    start, source = strike_frame({"startup": first_hit(atk), "front_contact": fc}, 0.0,
                                 None if _has_contact(fc) else _fallback_delay(weapon_id, key))
    return {"startup": start, "startup_source": source, "first_hit": first_hit(atk),
            "poise": atk["poise_damage"] * POISE_MENU * rate, "sa_rate": rate,
            "hyper": hyper_windows(atk), "stamina": stamina_total(reg, weapon_id, atk)}


_REACH = []


def _reach_module():
    if not _REACH:
        _REACH.append(_sibling("er-mechanics-reach"))
    return _REACH[0]


def _corpus_builds(mirror: Path, rl_lo: int, rl_hi: int) -> tuple[list[dict], Counter]:
    out, seen, why = [], set(), Counter()
    for line in mirror.open():
        row = json.loads(line)
        b = row["build"]
        st = EMBED.stats_of(b)
        if st is None or not rl_lo <= st["rl"] <= rl_hi or not is_pvp(b):
            continue
        if sum(st[k] for k in EMBED.ATTRS) - EMBED.LEVEL_OFFSET != st["rl"]:
            why["RL disagrees with attributes"] += 1
            continue
        key = (row.get("user"), tuple(EMBED.tokens(b)))
        if key in seen:
            why["duplicate"] += 1
            continue
        seen.add(key)
        c = b.get("computed") or {}
        po = c.get("poise") or {}
        out.append({"right": _right_hand(b), "left": _right_hand(b, LEFT_HAND), "two": bool(b.get("is2h")),
                    "poise": po.get("altered", po.get("original")), "stamina": c.get("maxStamina")})
    return out, why


#: Modules whose output the cached pool holds: a change to any of them rebuilds it. Measured
#: 2026-09-29: `er-mechanics-reach.py` changed its `front_contact_frame_real` between two
#: ranking runs, and the pool cached before the change kept the old frames while the scored
#: slots read the new ones (676 of 13303 slots got a different `f_exchange`).
POOL_SOURCES = ("er-mechanics-exchange.py", "er-mechanics-reach.py", "er-mechanics-attacks.py")


def _source_stamp() -> str:
    h = hashlib.sha256()
    for name in POOL_SOURCES:
        h.update((HERE / name).read_bytes())
    return h.hexdigest()[:16]


def opponent_pool(reg, mirror: Path = CACHE / "builds.jsonl", rl_lo: int = 140, rl_hi: int = 160,
                  cache: bool = True) -> dict:
    """The adoption-weighted opponent pool of an RL window (module docstring).

    Returns {'profiles': {'<weapon>|<1h|2h>': attack_profile}, 'builds': [[profile key, menu
    poise]], 'poise': sorted corpus menu poise, 'bar': median max stamina, 'ref_per_bar': pool
    median R1s per bar, 'offhand': {profile key: {left weapon or '-': builds}}, 'dropped':
    reasons}. Cached beside the mirror, keyed on its size and
    mtime."""
    st = mirror.stat()
    stamp = [POOL_VERSION, st.st_size, int(st.st_mtime), rl_lo, rl_hi, OPPONENT_SLOT, _source_stamp()]
    # The stamp is in the name too, so checkouts of different sources (a worktree beside the main
    # tree, an A/B against a ref) keep their own file instead of rebuilding each other's.
    path = CACHE / f"exchange-pool-{rl_lo}-{rl_hi}-{stamp[-1]}.json"
    if cache and path.exists():
        got = json.loads(path.read_text())
        if got.get("stamp") == stamp:
            return got
    builds, why = _corpus_builds(mirror, rl_lo, rl_hi)
    ids = weapon_ids()
    sa = SaRates()
    profiles, memo, rows, offhand = {}, {}, [], {}
    for b in builds:
        chosen = None
        for name in b["right"]:
            if name not in ids:
                why["weapon not in regulation"] += 1
                continue
            key = f"{name}|{'2h' if b['two'] else '1h'}"
            if key not in memo:
                memo[key] = attack_profile(reg, sa, ids[name], b["two"])
            if memo[key] is not None:
                chosen = key
                break
        if chosen is None:
            why["no right-hand weapon with an R1 hit"] += 1
            continue
        if b["poise"] is None:
            why["no computed poise"] += 1
            continue
        profiles[chosen] = memo[chosen]
        rows.append([chosen, float(b["poise"])])
        # The left hand as fought with, each of a build's left weapons an equal share of it, for the
        # cross-hand follow-ups (`er-mechanics-disengage.offhand_escapes`). Two-handed: none.
        # Staves and seals are cast from and then switched off: the Frenzied Flame Seal's 264
        # builds record a spell 7 times, Bestial Vitality each time (FTH 12, the builds' median),
        # so a catalyst counts only when it is the whole left hand, and then as no off-hand.
        held = [] if b["two"] else b["left"]
        melee = [n for n in held if n not in ids or reg.weapon[ids[n]]["wepType"] not in CATALYST_TYPES]
        lefts = melee or ["-"]
        tally = offhand.setdefault(chosen, {})
        for name in lefts:
            tally[name] = tally.get(name, 0.0) + 1.0 / len(lefts)
    bars = [b["stamina"] for b in builds if b["stamina"]]
    bar = float(np.median(bars))
    per_bar = [bar / profiles[k]["stamina"] for k, _ in rows if profiles[k]["stamina"]]
    pool = {"stamp": stamp, "rl": [rl_lo, rl_hi], "profiles": profiles, "builds": rows, "offhand": offhand,
            "poise": sorted(p for _, p in rows), "bar": bar,
            "ref_per_bar": float(np.median(per_bar)), "corpus_builds": len(builds), "dropped": dict(why)}
    if cache:
        path.write_text(json.dumps(pool))
    return pool


#: The bound on the priced net (`priced_net`) when trades are priced (`Pool.trade_clamp`):
#: `INFERRED`, kept at 1 so the contest factor keeps the 1 -+ `EXCHANGE_WEIGHT` span it has
#: unpriced and pricing cannot double the contest's weight against the other factors. It binds on
#: 398 of 2371 family contests of the RL 150 ranking (exchange.md section 2a).
TRADE_CLAMP = 1.0
#: The damage of one opponent hit when the ranking has no row for its weapon and grip: the
#: pool-weighted mean of the RL 150 ranking's R1 #1 `dmg` (`er-mechanics-ashes.OPPONENT_FALLBACK`
#: 'hp', `MEASURED` 2026-09-29; its selftest checks the two agree).
OPPONENT_HP_FALLBACK = 388.0


def profile_key(weapon: str, two: bool) -> str:
    """The pool's profile key of a ranking row's weapon and grip."""
    return f"{plain_name(weapon)}|{'2h' if two else '1h'}"


class Pool:
    """`opponent_pool` as arrays: one entry per distinct profile, builds indexed into them.

    `sa_rate=False` divides every opponent's saRate back out, to compare with results made
    before `er-builds-pvp.py` applied it.

    Every row also carries `dmg`, the hit it lands on the scored player (`set_damage` reads it from
    a ranking, else `OPPONENT_HP_FALLBACK`), and every entry a `weight` (None: the builds count
    equally, as `opponent_pool` lists them). `weighted` builds a pool whose rows are (profile,
    opener) pairs and whose entries split each build over the openers it throws."""

    def __init__(self, pool: dict, sa_rate: bool = True):
        self.raw = pool
        self.keys = sorted(pool["profiles"])
        idx = {k: i for i, k in enumerate(self.keys)}
        prof = [pool["profiles"][k] for k in self.keys]
        self.startup = np.array([p["startup"] for p in prof], float)
        self.poise_dealt = np.array([p["poise"] / (1.0 if sa_rate else p.get("sa_rate") or 1.0)
                                     for p in prof], float)
        self.hyper = [p["hyper"] for p in prof]
        self.dmg = np.full(len(self.keys), OPPONENT_HP_FALLBACK)
        self.build_prof = np.array([idx[k] for k, _ in pool["builds"]], int)
        self.build_poise = np.array([p for _, p in pool["builds"]], float)
        self.weight = None
        # None: a trade counts 0 in the contest (the default); a number: trades are priced by
        # damage (`priced_net`) and the priced net is bounded by it.
        self.trade_clamp = None
        self.my_poise = np.sort(np.array(pool["poise"], float))
        self.n = len(self.build_prof)
        self.bar = pool["bar"]
        self.ref_per_bar = pool["ref_per_bar"]

    def set_damage(self, results: list, fallback: float = OPPONENT_HP_FALLBACK) -> float:
        """`dmg` per profile from an `er-builds-pvp` ranking: its row's `OPPONENT_SLOT` `dmg` (the
        sweep build's corpus-mean damage standing in for that player's own, as
        `er-mechanics-ashes.opponents_from_results` reads it), else `fallback`. Returns the share
        of entries matched."""
        by = {}
        for r in results:
            d = ((r.get("slots") or {}).get(OPPONENT_SLOT) or {}).get("dmg")
            if d:
                by[profile_key(r["weapon"], r["two"])] = float(d)
        # Pool keys are regulation names, already without accents.
        self.dmg = np.array([by.get(k, fallback) for k in self.keys], float)
        hit = np.array([k in by for k in self.keys])
        return self.mean(hit[self.build_prof])

    @classmethod
    def weighted(cls, base: "Pool", keys: list, rows: list, entries: list) -> "Pool":
        """A pool of (profile, opener) rows: `rows` [{'startup', 'poise', 'hyper', 'dmg'}] in `keys`
        order, `entries` [(row index, the build's menu poise, weight)]. The weights are normalised;
        the corpus poise, stamina bar and raw pool are `base`'s."""
        p = cls.__new__(cls)
        p.raw, p.keys = base.raw, list(keys)
        p.startup = np.array([r["startup"] for r in rows], float)
        p.poise_dealt = np.array([r["poise"] for r in rows], float)
        p.hyper = [list(r["hyper"]) for r in rows]
        p.dmg = np.array([r["dmg"] for r in rows], float)
        p.build_prof = np.array([e[0] for e in entries], int)
        p.build_poise = np.array([e[1] for e in entries], float)
        w = np.array([e[2] for e in entries], float)
        p.weight = w / w.sum()
        p.trade_clamp = getattr(base, "trade_clamp", None)
        p.my_poise, p.n, p.bar, p.ref_per_bar = base.my_poise, len(entries), base.bar, base.ref_per_bar
        return p

    def mean(self, x) -> float:
        """The mean of one value per entry: plain over the builds, or by `weight`."""
        x = np.asarray(x, float)
        return float(np.mean(x)) if self.weight is None else float(np.dot(self.weight, x))

    def startup_percentiles(self, qs=(10, 25, 50, 75, 90)) -> dict:
        s = self.startup[self.build_prof]
        return {q: float(np.percentile(s, q)) for q in qs}


def _window_at(windows, frame):
    """(bonus, multiplier) of the window covering `frame`, else (0, 1). Half open, as
    `er-mechanics-frame-advantage.trade` reads it."""
    for s, e, bonus, mult in windows:
        if s <= frame < e:
            return bonus, mult
    return 0.0, 1.0


# --------------------------------------------------------------------------------------------
# one slot

def priced_net(pool, win_b, loss_b, dmg: float) -> float:
    """The contest's net with trades priced by damage (exchange.md section 2a): per entry k,
    `hp_k = (win_k + trade_k) x dmg - (loss_k + trade_k) x D_k` over the pair's mean hit
    `(dmg + D_k) / 2`, then the pool mean. Equal hits give `win - loss` exactly; a trade is worth
    what my hit outdamages theirs by."""
    d_k = pool.dmg[pool.build_prof]
    win_b, loss_b = np.asarray(win_b, float), np.asarray(loss_b, float)
    trade_b = 1.0 - win_b - loss_b
    hp = (win_b + trade_b) * dmg - (loss_b + trade_b) * d_k
    return pool.mean(hp / ((dmg + d_k) / 2.0))


def contest_factor(pool, win_b, loss_b, dmg: float | None) -> tuple[float, float | None]:
    """(factor, priced net or None): `1 + EXCHANGE_WEIGHT x net`, the net priced and bounded by
    `pool.trade_clamp` when that is set and the attack's `dmg` is known, else `win - loss`."""
    clamp = getattr(pool, "trade_clamp", None)
    if clamp is None or not dmg:
        return 1.0 + EXCHANGE_WEIGHT * (pool.mean(win_b) - pool.mean(loss_b)), None
    net = priced_net(pool, win_b, loss_b, float(dmg))
    return 1.0 + EXCHANGE_WEIGHT * max(-clamp, min(clamp, net)), net


def exchange(pool: Pool, startup: float, poise_dealt: float, hyper: list[tuple], dmg: float | None = None) -> dict:
    """Outcome shares of the simultaneous exchange of one attack against the pool. `dmg` is the
    attack's own hit, read only when the pool prices trades (`Pool.trade_clamp`)."""
    first = pool.startup > startup      # per profile: I hit first
    second = pool.startup < startup
    # I hit first at `startup`: the opponent's window at that frame decides.
    their = [_window_at(h, startup) for h in pool.hyper]
    their_bonus = np.array([b for b, _ in their])[pool.build_prof]
    their_mult = np.array([m for _, m in their])[pool.build_prof]
    breaks_them = poise_dealt * their_mult >= pool.build_poise + their_bonus
    # They hit first at their own startup: my window at that frame; my armor poise is drawn from
    # the corpus distribution, so the break chance is the share of it at or below dealt - bonus.
    mine = [_window_at(hyper, t) for t in pool.startup]
    dealt = pool.poise_dealt * np.array([m for _, m in mine])
    room = dealt - np.array([b for b, _ in mine])
    p_break_me = np.searchsorted(pool.my_poise, room, side="right") / len(pool.my_poise)
    f_b, s_b = first[pool.build_prof], second[pool.build_prof]
    win_b, loss_b = f_b & breaks_them, np.where(s_b, p_break_me[pool.build_prof], 0.0)
    win, loss = pool.mean(win_b), pool.mean(loss_b)
    p_first, p_second = pool.mean(f_b), pool.mean(s_b)
    trade = 1.0 - win - loss
    net, naive = win - loss, p_first - p_second
    f_ex, net_hp = contest_factor(pool, win_b, loss_b, dmg)
    if net_hp is None:
        f_ex = 1.0 + EXCHANGE_WEIGHT * net
    f_st = 1.0 + EXCHANGE_WEIGHT * naive
    priced = {} if net_hp is None else {"net_hp": net_hp}
    return {"p_first": p_first, "p_second": p_second, "p_same": 1.0 - p_first - p_second,
            "win": win, "loss": loss, "trade": trade, "net": net, **priced,
            # Of the exchanges the opponent strikes first, the share I keep swinging through.
            "trade_through": (1.0 - loss / p_second) if p_second else None,
            # Of the exchanges I strike first, the share my hit interrupts.
            "interrupts": (win / p_first) if p_first else None,
            "f_startup": f_st, "f_hyper": f_ex / f_st, "f_exchange": f_ex}


def stamina_factor_swings(pool: Pool, cost: float) -> dict:
    """The retired factor: swings per bar against the pool's, `** STAMINA_EXP`, clamped."""
    per_bar = pool.bar / cost if cost else None
    if per_bar is None:
        return {"per_bar": None, "f_stamina_swings": 1.0}
    f = (per_bar / pool.ref_per_bar) ** STAMINA_EXP
    return {"per_bar": per_bar, "f_stamina_swings": min(max(f, STAMINA_CLAMP[0]), STAMINA_CLAMP[1])}


# --------------------------------------------------------------------------------------------
# stamina: regeneration and the budget of a fight window

REGEN_PER_FRAME = STAMINA_REGEN_PER_S / 30.0    # real frames are 1/30 s (`ATK.TAE_FPS`)
_REGEN_MEMO: dict = {}


def regen_windows(atk: dict) -> list[tuple] | None:
    """[(start, end, percent)] real frames of the slot's TAE 225 events that set regeneration
    below 100%, from the start of its own clip; None when the clip has no TAE. Where two overlap
    the later in TAE order is taken (`INFERRED`: each running event rewrites the byte, in order)."""
    key = atk.get("tae_entry")
    if key in _REGEN_MEMO:
        return _REGEN_MEMO[key]
    out = None
    if key:
        cat, anim = (int(x) for x in key[1:].split("_"))
        _, _, events = ATK.resolve_events(cat, anim)
        if events is not None:
            real = ATK.clip_to_real(events)
            out = []
            for e in events:
                if e.type != TAE_SP_REGEN or not e.params:
                    continue
                pct = e.params[0]
                if pct >= 100:
                    continue
                # A few events end on a float sentinel (1e40): they run to the clip's end.
                end = real(e.end) * ATK.TAE_FPS if e.end < 1e6 else float("inf")
                out.append((real(e.start) * ATK.TAE_FPS, end, float(pct)))
    _REGEN_MEMO[key] = out
    return out


def commit_frames(atk: dict, hit: dict, entry: float = 0.0) -> float | None:
    """`er-builds-pvp.slot_score`'s commitment: entry plus the earlier of the same-button and the
    roll cancel frame (R2 lead-in included). Read from `hit` when it carries them, else from the
    attack row as `slot_hit` does."""
    ends = [hit.get(k) for k in ("next", "roll") if hit.get(k)]
    if not ends:
        cancel = atk.get("cancel_frame") or {}
        button = "r2" if atk["slot"].removeprefix("2h_").startswith("r2") else "r1"
        lead = atk.get("release_lead_in") or 0.0
        ends = [cancel[k] + lead for k in (button, "dodge") if cancel.get(k) is not None]
    return entry + min(ends) if ends else None


class RegenProfile:
    """Regeneration percent against time since a swing started: 0 over the entry and the R2
    charge lead-in (`INFERRED` for crouch and running entries; the roll and backstep clips set
    0% for 30 and 26 clip frames, `VERIFIED` in a000), then the slot clip's TAE 225 windows, and
    100% once the attacker walks off at the move-cancel frame. A slot with no TAE is taken to
    block regeneration until it can move (`INFERRED`)."""

    def __init__(self, windows, pre: float, move: float | None):
        self.pre = pre
        self.move = move
        self.windows = [(s + pre, e + pre, p) for s, e, p in windows] if windows is not None else None

    def pct(self, t: float, walked: bool) -> float:
        if t < self.pre:
            return 0.0
        if walked and self.move is not None and t >= self.move:
            return 100.0
        if self.windows is None:
            return 0.0 if self.move is None or t < self.move else 100.0
        got = 100.0
        for s, e, p in self.windows:
            if s <= t < e:
                got = p
        return got

    def cuts(self) -> list[float]:
        pts = {self.pre} | ({self.move} if self.move is not None else set())
        for s, e, _ in self.windows or []:
            pts |= {s, e}
        return sorted(p for p in pts if p != float("inf"))

    def gain(self, t0: float, t1: float, walked: bool) -> float:
        """Stamina regenerated between t0 and t1 frames after the swing started."""
        total, x = 0.0, t0
        for b in [c for c in self.cuts() if t0 < c < t1] + [t1]:
            total += (b - x) * REGEN_PER_FRAME * self.pct((x + b) / 2, walked) / 100.0
            x = b
        return total

    def time_to_gain(self, t0: float, amount: float) -> float:
        """Frames after t0 until `amount` has regenerated, walking off at the move cancel."""
        if amount <= 0:
            return 0.0
        x = t0
        for b in [c for c in self.cuts() if c > t0] + [float("inf")]:
            rate = REGEN_PER_FRAME * self.pct(x if b == float("inf") else (x + b) / 2, True) / 100.0
            if rate > 0 and (b == float("inf") or (b - x) * rate >= amount):
                return x + amount / rate - t0
            amount -= (b - x) * rate if b != float("inf") else 0.0
            x = b
        return float("inf")


def stamina_budget(bar: float, cost: float, commit: float, prof: RegenProfile,
                   window_s: float = FIGHT_WINDOW_S, reserve: bool = True) -> dict:
    """Swings one attacker fits into `window_s`, repeating one slot from a full `bar`.

    A swing starts when the previous one's commitment is over and stamina exceeds the threshold:
    the swing's own cost with `reserve` (a roll stays possible afterwards), else 0 (any stamina
    starts it; that the attack gate is `> 0` is `INFERRED`, `ExecAttack` only resets the combo at
    or below 0). The cost is charged whole at the swing (the hitbox charge comes before any
    regeneration in every attack clip, whose TAE 225 window opens on frame 0) and floors at
    `STAMINA_FLOOR`. Between chained swings the clip's own regeneration applies; when stamina is
    short the attacker walks off at the move-cancel frame and waits. The last swing that does not
    fit counts pro rata. Returns swings, the factor `N * commit / W`, and the greedy burst: swings
    from a full bar until stamina is gone, and the frames after its last commitment before a roll
    is possible again (the defender's punish window beyond ordinary recovery)."""
    W = window_s * 30.0
    thr = cost if reserve and bar > cost else 0.0
    s, t, n, since = float(bar), 0.0, 0.0, None
    while t < W:
        if s <= thr:
            wait = prof.time_to_gain(since, thr - s + 1.0)
            t += wait
            s = thr + 1.0
            since = None
            continue
        if t + commit > W:
            n += (W - t) / commit
            break
        s = max(float(STAMINA_FLOOR), s - cost) + prof.gain(0.0, commit, False)
        s = min(s, float(bar))
        t += commit
        n += 1
        since = commit
    # The greedy burst from a full bar, chained, until a swing can no longer start.
    g, burst = float(bar), 0
    while g > 0 and burst < 1000:
        g = min(max(float(STAMINA_FLOOR), g - cost) + prof.gain(0.0, commit, False), float(bar))
        burst += 1
    lock = max(0.0, prof.time_to_gain(commit, 1.0 - g)) if g <= 0 else 0.0
    # The same burst keeping a roll: swings while stamina before them exceeds the cost.
    s, safe = float(bar), 0
    while s > cost and safe < 1000:
        s = min(s - cost + prof.gain(0.0, commit, False), float(bar))
        safe += 1
    return {"swings": n, "f_stamina": min(1.0, n * commit / W), "burst": burst, "burst_end": g,
            "burst_safe": safe, "lock_frames": lock}


def slot_stamina(pool: Pool, reg, weapon_id: int, atk: dict, hit: dict, entry: float = 0.0,
                 window_s: float | None = None) -> dict:
    """Stamina numbers of one slot: its cost (the entry's included), commitment, when its clip
    lets regeneration run again, the budget over the window, and the retired swings-per-bar
    factor beside it for comparison."""
    key = atk["slot"].removeprefix("2h_")
    cost = stamina_total(reg, weapon_id, atk) + ENTRY_STAMINA.get(key, 0)
    commit = commit_frames(atk, hit, entry)
    old = stamina_factor_swings(pool, stamina_total(reg, weapon_id, atk))
    if not cost or not commit:
        return {**old, "f_stamina": 1.0, "cost": cost, "commit": commit}
    lead = atk.get("release_lead_in") or 0.0
    move = (atk.get("cancel_frame") or {}).get("move")
    pre = entry + lead
    windows = regen_windows(atk)
    prof = RegenProfile(windows, pre, None if move is None else move + pre)
    b = stamina_budget(pool.bar, cost, commit, prof, FIGHT_WINDOW_S if window_s is None else window_s)
    regen_from = next((c for c in prof.cuts() if c >= pre and prof.pct(c + 1e-6, False) >= 100.0), None)
    out = {**old, "cost": cost, "commit": commit, "regen_blocked_until": regen_from,
           "move_frame": prof.move, **b}
    dmg = hit.get("dmg")
    if dmg:
        out["dmg_per_stamina"] = dmg / cost
        out["dmg_per_bar"] = dmg * pool.bar / cost
        out["dmg_window"] = dmg * b["swings"]
        out["dmg_burst_safe"] = dmg * b["burst_safe"]
    return out


#: Distance (m) straight ahead at which the slot's strike frame is read from
#: `er-mechanics-reach` `front_contact_frame_real` (`INFERRED`: a defender in front at a typical
#: engagement range; a horizontal sweep opens its window off to the side and reaches the front
#: later, e.g. Giant-Crusher 2H R1 17.9 at window open, 18.9 at 2.5 m).
STRIKE_DISTANCE_M = 2.5


def strike_frame(hit: dict, lead: float = 0.0, delay: float | None = None) -> tuple[float, str]:
    """(frame, source) the slot's hit reaches a defender `STRIKE_DISTANCE_M` ahead: the reach
    module's front-contact frame there plus the R2 release lead-in when it has one; else the
    slot's first hit frame (`startup`, lead-in already in) plus `delay`, the class-median frames
    from first hit to that contact (`er-mechanics-reach.class_fallback`, `INFERRED`). The first
    hit alone is only used when no delay is known: it is never later than the contact, so it
    would win exchanges a measured slot loses. `front_contact` keys may be floats or, after a JSON
    round trip, strings."""
    fc = hit.get("front_contact") or {}
    v = fc.get(STRIKE_DISTANCE_M, fc.get(str(STRIKE_DISTANCE_M)))
    if v is not None:
        return v + lead, "front contact"
    if delay is not None:
        return hit["startup"] + delay, "first hit + class median delay (INFERRED)"
    return hit["startup"], "first hit"


def _fallback_delay(weapon_id: int, slot: str) -> float | None:
    """The class-median first-hit-to-contact delay for a slot with no 2.5 m front contact."""
    grip = "both" if slot.startswith("2h_") else "one"
    fb = _reach_module().class_fallback(weapon_id, grip, slot, STRIKE_DISTANCE_M)
    return fb.get("front_contact_delay_real")


def _has_contact(fc: dict | None) -> bool:
    fc = fc or {}
    return fc.get(STRIKE_DISTANCE_M, fc.get(str(STRIKE_DISTANCE_M))) is not None


def slot_exchange(pool: Pool, reg, weapon_id: int, atk: dict, hit: dict, entry: float = 0.0) -> dict | None:
    """The three factors of one `er-builds-pvp` slot.

    `atk` is the `er-mechanics-attacks.weapon_attacks` row, `hit` the slot dict (`startup` with
    the R2 lead-in and any earlier sweep hit already applied, `poise` in menu units x saRate).
    `entry` is the frames spent before the slot's own clip (`er-builds-pvp.SCORE_ENTRY_FRAMES`).
    A rolling or backstep attack (`INVINCIBLE_ENTRY_SLOTS`) gets a neutral exchange, because the
    roll's invincibility frames are not modelled. A crouch or running attack has none, so it is
    exchanged like any slot, from standing: its first hit and hyperarmor windows move by `entry`
    (Giant-Crusher 2H crouch R1: first hit at frame 14, so 22 from standing, against the standing
    R1's 17.9; `INFERRED` that the exchange starts before the crouch or sprint). Its stamina
    factor applies either way. None when the slot has no hit frame."""
    if hit.get("startup") is None:
        return None
    lead = atk.get("release_lead_in") or 0.0
    st = slot_stamina(pool, reg, weapon_id, atk, hit, entry)
    if entry and atk["slot"].removeprefix("2h_") in INVINCIBLE_ENTRY_SLOTS:
        ex = {"f_startup": 1.0, "f_hyper": 1.0, "f_exchange": 1.0, "skipped": "roll invincibility"}
    else:
        delay = None if _has_contact(hit.get("front_contact")) else _fallback_delay(weapon_id, atk["slot"])
        start, source = strike_frame(hit, lead, delay)
        ex = exchange(pool, start + entry, hit["poise"], hyper_windows(atk, lead + entry), hit.get("dmg"))
        ex["strike_frame"], ex["strike_source"] = start + entry, source
    return {**ex, **st, "factor": ex["f_exchange"] * st["f_stamina"]}


# --------------------------------------------------------------------------------------------
# self test

def selftest() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    # A synthetic pool: one fast light R1 (frame 10, poise 60) and one slow heavy R1 with
    # hyperarmor (frame 20, poise 300, window 5-25 +100 at 0.45), both at armor poise 80.
    syn = Pool({"profiles": {"fast": {"startup": 10.0, "poise": 60.0, "hyper": [], "stamina": 10},
                             "slow": {"startup": 20.0, "poise": 300.0, "hyper": [(5.0, 25.0, 100.0, 0.45)],
                                      "stamina": 30}},
                "builds": [["fast", 80.0], ["slow", 80.0]], "poise": [80.0, 80.0], "bar": 150.0,
                "ref_per_bar": 7.5})
    e = exchange(syn, 5.0, 100.0, [])
    check(e["p_first"] == 1.0 and abs(e["win"] - 0.5) < 1e-9 and abs(e["trade"] - 0.5) < 1e-9,
          f"frame 5, 100 poise: beats both, staggers the fast build, the slow one trades through its "
          f"window (100 x 0.45 < 80 + 100) ({e['win']}, {e['trade']})")
    e = exchange(syn, 30.0, 100.0, [])
    check(e["p_second"] == 1.0 and abs(e["loss"] - 0.5) < 1e-9,
          f"frame 30, no hyperarmor: the fast R1 (60) holds against 80 poise, the slow one (300) "
          f"breaks it ({e['loss']})")
    e2 = exchange(syn, 30.0, 100.0, [(0.0, 40.0, 300.0, 0.45)])
    check(e2["loss"] == 0.0 and e2["trade_through"] == 1.0,
          "the same slot with a +300 window at 0.45 trades through both")
    check(abs(e2["f_startup"] * e2["f_hyper"] - e2["f_exchange"]) < 1e-12,
          "f_startup x f_hyper equals f_exchange")
    e = exchange(syn, 10.0, 0.0, [])
    check(e["p_same"] == 0.5 and e["trade"] >= 0.5, "same first frame is a trade")
    check(exchange(syn, 5.0, 100.0, [])["f_exchange"] > exchange(syn, 15.0, 100.0, [])["f_exchange"]
          > exchange(syn, 30.0, 100.0, [])["f_exchange"], "earlier startup scores higher")
    rows = [{"startup": 10.0, "poise": 60.0, "hyper": [], "dmg": 300.0},
            {"startup": 20.0, "poise": 300.0, "hyper": [(5.0, 25.0, 100.0, 0.45)], "dmg": 600.0}]
    heavy = Pool.weighted(syn, ["fast", "slow"], rows, [(0, 80.0, 1.0), (1, 80.0, 3.0)])
    e = exchange(heavy, 5.0, 100.0, [])
    check(abs(e["win"] - 0.25) < 1e-12 and abs(e["trade"] - 0.75) < 1e-12,
          f"entry weights: the slow build thrown three times as often trades 3/4 of the time ({e['trade']})")
    # Priced trades, by hand: against `heavy` at frame 5 with 100 poise and a 600 hit, the fast
    # build (D 300, weight 1/4) is a win, (600 + 0) / 450; the slow one (D 600, 3/4) a trade,
    # (600 - 600) / 600. Net 1/4 x 4/3 = 1/3 against 1/4 unpriced.
    heavy.trade_clamp = 1.0
    p = exchange(heavy, 5.0, 100.0, [], 600.0)
    check(abs(p["net_hp"] - 1.0 / 3.0) < 1e-12 and abs(p["f_exchange"] - (1 + EXCHANGE_WEIGHT / 3.0)) < 1e-12
          and abs(p["f_startup"] * p["f_hyper"] - p["f_exchange"]) < 1e-12,
          f"priced trades: net 1/3 ({p['net_hp']:.4f}), f_startup x f_hyper still f_exchange")
    small = exchange(heavy, 5.0, 100.0, [], 150.0)
    check(small["net_hp"] < 0 < p["net_hp"] and small["win"] == p["win"],
          f"the same outcomes with a 150 hit lose the trades ({small['net_hp']:.3f})")
    big = exchange(heavy, 5.0, 100.0, [], 5000.0)
    check(big["net_hp"] > 1.0 and big["f_exchange"] == 1.0 + EXCHANGE_WEIGHT,
          f"the priced net is bounded by the clamp ({big['net_hp']:.3f} -> {big['f_exchange']})")
    heavy.trade_clamp = None
    check(exchange(heavy, 5.0, 100.0, [], 600.0)["f_exchange"] == 1.0 + EXCHANGE_WEIGHT * 0.25
          and "net_hp" not in exchange(heavy, 5.0, 100.0, [], 600.0),
          "unpriced (the default) a trade counts 0, whatever the damage")
    s_lo = stamina_factor_swings(syn, 40.0)["f_stamina_swings"]
    s_hi = stamina_factor_swings(syn, 15.0)["f_stamina_swings"]
    check(s_lo < 1.0 < s_hi and s_hi <= STAMINA_CLAMP[1],
          f"retired factor: cheaper attack scores higher ({s_lo:.3f}, {s_hi:.3f})")

    # The budget, by hand: bar 100, cost 40, commitment 30, regeneration 0% for 60 frames, walk
    # at 60. Two swings from the bar (100 -> 60 -> 20), then each wait is the rest of the block
    # plus the deficit at 1.5 a frame: 30 + 21 / 1.5 = 44, then 30 + 40 / 1.5 = 56.67 twice, and
    # the fifth swing starts at 277.33 with 22.67 of its 30 frames left: 4.756 swings in 300.
    prof = RegenProfile([(0.0, 60.0, 0.0)], 0.0, 60.0)
    b = stamina_budget(100.0, 40.0, 30.0, prof, 10.0)
    check(abs(b["swings"] - (4 + (300 - 277.0 - 1.0 / 3) / 30)) < 1e-6
          and abs(b["f_stamina"] - b["swings"] * 30 / 300) < 1e-12,
          f"stamina budget by hand: 4.756 swings in 10 s ({b['swings']:.4f})")
    check(b["burst"] == 3 and b["burst_end"] == -20.0 and abs(b["lock_frames"] - (30 + 21 / 1.5)) < 1e-9
          and b["burst_safe"] == 2,
          f"greedy burst 3 swings to -20, roll back after 30 + 21 / 1.5 frames; 2 swings keep a roll "
          f"({b['burst']}, {b['burst_end']}, {b['lock_frames']:.2f}, {b['burst_safe']})")
    free = stamina_budget(100.0, 1.0, 30.0, RegenProfile([], 0.0, None), 10.0)
    check(free["f_stamina"] == 1.0, "a slot the bar and regeneration always cover sustains its full rate")
    check(stamina_budget(100.0, 40.0, 30.0, RegenProfile([(0.0, 30.0, 0.0)], 0.0, 30.0), 10.0)["f_stamina"]
          > b["f_stamina"], "a shorter 0% window sustains more")
    check(RegenProfile([(0.0, 60.0, 20.0)], 0.0, None).gain(0.0, 60.0, False) == 60 * 1.5 * 0.2,
          "a 20% window regenerates a fifth of the base rate")

    # The regeneration constants, byte for byte (1.16.2 and the installed 1.17.1 when present).
    for image, fn in (("eldenring-deobf.bin", 0x1406566B0), ("eldenring-deobf-1.17.1.bin", 0x140657500)):
        path = HERE.parent / image
        if not path.exists():
            print("skip regeneration constant:", image, "absent")
            continue
        img = path.read_bytes()
        at = fn - 0x140000000 + 0x1C
        disp = int.from_bytes(img[at + 4:at + 8], "little", signed=True)
        target = fn + 0x1C + 8 + disp - 0x140000000
        rate = float(np.frombuffer(img[target:target + 4], "<f4")[0])
        check(img[at:at + 4] == bytes.fromhex("f30f1015") and rate == STAMINA_REGEN_PER_S,
              f"{image}: GetStaminaRecoverySpeed loads {rate} (movss at +0x1c)")
        if image == "eldenring-deobf.bin":
            check(img[0x42E3C3:0x42E3C6] == bytes.fromhex("884214"),
                  "TAE 225 stores Args[0] at ChrCtrlModifier+0x14 (0x14042e3c3)")
            check(img[0x3C3B89:0x3C3B8F] == bytes.fromhex("66c7410c6464"),
                  "ChrCtrlModifierData::Reset puts SP and FP regeneration back to 100 (0x1403c3b89)")
            case = [i + 9 for i in range(0x9B) if 0x140000000 + int.from_bytes(
                img[0x40E194 + img[0x40E24C + i] * 4:0x40E198 + img[0x40E24C + i] * 4], "little") == 0x14040CD9C]
            check(case == [110] and img[0x40CDAA:0x40CDAE] == bytes.fromhex("83481840"),
                  f"HksAct {case} (SetStaminaRecoveryDisabled) sets actionFlags bit 0x40")

    reg = ATK.Regulation(None)
    ids = weapon_ids()
    rows = ATK.weapon_attacks(reg, ids["Greatsword"], "both")
    r1 = next(r for r in rows if r["slot"] == "2h_r1_1")
    check(stamina_total(reg, ids["Greatsword"], r1) == 27,
          "Greatsword 2H R1 stamina 27 (giant-crusher-adoption-gap.md, int(stamina x 1.135))")
    sa = SaRates()
    check(abs(sa(r1["atk_row"]) - 3.5) < 1e-6, "Greatsword R1 saRate 3.5 (giant-crusher-adoption-gap.md)")

    mirror = CACHE / "builds.jsonl"
    if not mirror.exists():
        print("skip corpus checks: no mirror at", mirror)
        return 0 if ok else 1
    pool = Pool(opponent_pool(reg, mirror, 140, 160))
    check(pool.n > 500, f"RL 140-160 pool has {pool.n} builds")
    check(140 <= pool.bar <= 170, f"median max stamina {pool.bar:.1f} (adoption-gap STR filter: 155)")
    gc = next(r for r in ATK.weapon_attacks(reg, ids["Giant-Crusher"], "both") if r["slot"] == "2h_r1_1")
    gc_hit = {"startup": first_hit(gc), "poise": gc["poise_damage"] * POISE_MENU * sa(gc["atk_row"])}
    ex = slot_exchange(pool, reg, ids["Giant-Crusher"], gc, gc_hit)
    gw = regen_windows(gc)
    check(gw and gw[0][2] == 0.0 and gw[0][1] > ex["commit"] and ex["cost"] == 31 and ex["burst_safe"] == 4,
          f"Giant-Crusher 2H R1: 0% regeneration to real frame {gw[0][1] if gw else None:.1f}, past its "
          f"{ex['commit']} frame commitment, so chained R1s regenerate nothing; 31 a swing, 4 keep a roll")
    old =slot_exchange(Pool(pool.raw, sa_rate=False), reg, ids["Giant-Crusher"], gc,
                        dict(gc_hit, poise=gc_hit["poise"] / sa(gc["atk_row"])))
    check(old["trade_through"] is not None and old["trade_through"] >= 0.99,
          f"without saRate on either side, Giant-Crusher 2H R1 trades through "
          f"{100 * (old['trade_through'] or 0):.1f}% of the first-landing pool R1s (reproduces "
          f"giant-crusher-adoption-gap.md section 3: none break it)")
    bare = slot_exchange(pool, reg, ids["Giant-Crusher"], {**gc, "hyperarmor": []}, gc_hit)
    check(ex["trade_through"] > bare["trade_through"] and ex["f_hyper"] > bare["f_hyper"],
          f"hyperarmor raises the trade-through share ({100 * bare['trade_through']:.1f}% -> "
          f"{100 * ex['trade_through']:.1f}% with saRate applied)")
    dg = next(r for r in ATK.weapon_attacks(reg, ids["Dagger"], "one") if r["slot"] == "r1_1")
    dg_ex = slot_exchange(pool, reg, ids["Dagger"], dg,
                          {"startup": first_hit(dg), "poise": dg["poise_damage"] * POISE_MENU * sa(dg["atk_row"])})
    check(dg_ex["f_startup"] > ex["f_startup"], "a dagger R1 hits first more often than a Giant-Crusher R1")
    check(strike_frame({"startup": 17.9, "front_contact": {2.5: 18.9}}) == (18.9, "front contact")
          and strike_frame({"startup": 17.9, "front_contact": {"2.5": 18.9}}, 3.0) == (21.9, "front contact")
          and strike_frame({"startup": 17.9, "front_contact": {1.5: 12.0}}) == (17.9, "first hit")
          and strike_frame({"startup": 17.9, "front_contact": {1.5: 12.0}}, 0.0, 1.5)[0] == 19.4,
          "strike frame: front contact at 2.5 m (plus the R2 lead-in), else the first hit plus the "
          "class-median delay when one is known")
    shotel_fb = _reach_module().class_fallback(_reach_module().Reach().reg.find_weapon("Shotel"), "one", "r1_1")
    check((shotel_fb.get("front_contact_delay_real") or -1) >= 0 and "class 9" in shotel_fb["basis"].get(
          "front_contact_delay_real", ""),
          f"curved-sword class median delay for a missing contact is known and not negative "
          f"({shotel_fb.get('front_contact_delay_real')}, {shotel_fb['basis'].get('front_contact_delay_real')})")
    gc_reach = _reach_module().reach_summary(ids["Giant-Crusher"], "both").get("2h_r1_1") or {}
    fc = (gc_reach.get("front_contact_frame_real") or {}).get(STRIKE_DISTANCE_M)
    if fc is None:
        print("skip front-contact check: no pose for Giant-Crusher 2H R1")
    else:
        check(fc > first_hit(gc), f"Giant-Crusher 2H R1 reaches 2.5 m ahead after its window opens "
                                  f"({first_hit(gc)} -> {fc})")
    # Entry frames: a roll or backstep attack is neutral, a crouch or running attack is exchanged
    # from standing with its entry added to its first hit.
    gcc = next(r for r in ATK.weapon_attacks(reg, ids["Giant-Crusher"], "both") if r["slot"] == "2h_crouch_r1")
    gcc_hit = {"startup": first_hit(gcc), "poise": gcc["poise_damage"] * POISE_MENU * sa(gcc["atk_row"])}
    crouched = slot_exchange(pool, reg, ids["Giant-Crusher"], gcc, gcc_hit)
    standing = slot_exchange(pool, reg, ids["Giant-Crusher"], gcc, gcc_hit, 8.0)
    check("skipped" not in standing and standing["f_startup"] < crouched["f_startup"],
          f"a crouch R1 is exchanged, 8 entry frames later ({crouched['f_startup']:.3f} -> "
          f"{standing['f_startup']:.3f}; first hit {gcc_hit['startup']})")
    roll = next(r for r in ATK.weapon_attacks(reg, ids["Giant-Crusher"], "both") if r["slot"] == "2h_roll_r1")
    rex = slot_exchange(pool, reg, ids["Giant-Crusher"], roll, {"startup": first_hit(roll), "poise": 100.0}, 20.0)
    check(rex["f_exchange"] == 1.0 and rex.get("skipped"), "a rolling attack keeps a neutral exchange")
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------
# the ranking with these factors, before `er-builds-pvp.py` reads them itself

RANK_MODES = ("none", "all", "exchange", "stamina", "stamina-swings", "all-swings")


def rank_with_factors(mode: str, argv: list[str]) -> int:
    """Run `er-builds-pvp.py --sort score --no-exchange` with `slot_score` multiplied by this
    module's factors, patching the loaded module in memory only. `mode`: none (no factor), all
    (f_exchange x f_stamina), exchange, stamina, and the retired swings-per-bar factor as
    stamina-swings / all-swings. `--no-exchange` is passed so the ranking's own call of
    `slot_exchange` does not apply the factors a second time. It wraps `slot_hit` to keep each
    slot's attack row and weapon id beside the slot. `--dump <path>` writes, per (weapon, grip,
    slot) of `DUMP_WEAPONS`, the stamina numbers of its best-scoring row."""
    if mode not in RANK_MODES:
        raise SystemExit(f"--rank takes one of {RANK_MODES}")
    global FIGHT_WINDOW_S
    if "--stamina-window" in argv:
        i = argv.index("--stamina-window")
        FIGHT_WINDOW_S, argv = float(argv[i + 1]), argv[:i] + argv[i + 2:]
    dump = None
    if "--dump" in argv:
        i = argv.index("--dump")
        dump, argv = Path(argv[i + 1]), argv[:i] + argv[i + 2:]
    pvp = _sibling("er-builds-pvp")
    reg = pvp.ATK.Regulation(None)
    rl = int(argv[argv.index("--rl") + 1]) if "--rl" in argv else 150
    pool = Pool(opponent_pool(reg, pvp.CACHE / "builds.jsonl", rl - 10, rl + 10))
    names = {wid: name for name, wid in weapon_ids().items()}
    records: dict = {}
    orig_hit, orig_score = pvp.slot_hit, pvp.slot_score

    def slot_hit(tables, reg_, weapon_id, attack, *rest, **kw):
        out = orig_hit(tables, reg_, weapon_id, attack, *rest, **kw)
        out["_exchange_src"] = (weapon_id, attack)
        return out

    def slot_score(s, entry=0.0):
        sc = orig_score(s, entry)
        if sc is None or "_exchange_src" not in s:
            return sc
        wid, atk = s["_exchange_src"]
        x = slot_exchange(pool, reg, wid, atk, s, entry)
        if x is None:
            return sc
        f_old = x.get("f_stamina_swings", 1.0)
        f = {"none": 1.0, "all": x["factor"], "exchange": x["f_exchange"], "stamina": x["f_stamina"],
             "stamina-swings": f_old, "all-swings": x["f_exchange"] * f_old}[mode]
        name = names.get(wid)
        if dump is not None and name in DUMP_WEAPONS:
            key = (name, "2h" if atk["slot"].startswith("2h_") else "1h", atk["slot"].removeprefix("2h_"))
            if key not in records or sc["score"] > records[key]["base_score"]:
                records[key] = {"weapon": name, "grip": key[1], "slot": key[2], "base_score": sc["score"],
                                "rate": sc["rate"], "dmg": s["dmg"],
                                **{k: x.get(k) for k in STAMINA_REPORT_KEYS},
                                "f_exchange": x["f_exchange"], "score_new": sc["score"] * x["f_stamina"],
                                "score_old": sc["score"] * f_old}
        return dict(sc, score=sc["score"] * f)

    pvp.slot_hit, pvp.slot_score = slot_hit, slot_score
    sys.argv = ["er-builds-pvp.py", "--sort", "score", "--no-exchange", *argv]
    code = pvp.main()
    if dump is not None:
        dump.write_text(json.dumps({"mode": mode, "argv": argv, "bar": pool.bar,
                                    "fight_window_s": FIGHT_WINDOW_S,
                                    "regen_per_s": STAMINA_REGEN_PER_S,
                                    "slots": sorted(records.values(), key=lambda r: (r["weapon"], r["grip"], r["slot"]))},
                                   indent=1))
    return code


#: Weapons whose slots `--dump` records (exchange.md section 3's table).
DUMP_WEAPONS = ("Giant-Crusher", "Greatsword", "Hand Axe", "Claymore", "Lance")
STAMINA_REPORT_KEYS = ("cost", "commit", "regen_blocked_until", "move_frame", "per_bar", "f_stamina_swings",
                       "f_stamina", "swings", "burst", "burst_end", "burst_safe", "lock_frames",
                       "dmg_per_stamina", "dmg_per_bar", "dmg_window", "dmg_burst_safe")


# --------------------------------------------------------------------------------------------
# command line

def main() -> int:
    if "--rank" in sys.argv:
        i = sys.argv.index("--rank")
        return rank_with_factors(sys.argv[i + 1], sys.argv[1:i] + sys.argv[i + 2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rl", type=int, default=150)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--pool", action="store_true", help="print the opponent pool")
    ap.add_argument("--weapon", help="print every slot of one weapon")
    ap.add_argument("--grip", choices=("one", "both"), default="both")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    reg = ATK.Regulation(None)
    raw = opponent_pool(reg, a.mirror, a.rl - a.window, a.rl + a.window, cache=not a.no_cache)
    pool = Pool(raw)
    if a.pool or not a.weapon:
        print(f"RL {a.rl - a.window}-{a.rl + a.window}: {pool.n} opponent builds of {raw['corpus_builds']} "
              f"PvP builds, {len(pool.keys)} distinct {OPPONENT_SLOT} profiles; dropped {raw['dropped']}")
        print(f"  first hit percentiles {pool.startup_percentiles()}; median max stamina {pool.bar:.1f}; "
              f"pool median {OPPONENT_SLOT} per bar {pool.ref_per_bar:.2f}")
        counts = Counter(pool.keys[i] for i in pool.build_prof)
        for k, c in counts.most_common(15):
            p = raw["profiles"][k]
            ha = "; ".join(f"f{s:.0f}-{e:.0f} +{b:.0f} x{m:.2f}" for s, e, b, m in p["hyper"]) or "-"
            print(f"  {k:<40}{c:>5}  first {p['startup']:>5.1f}  poise {p['poise']:>6.0f}  "
                  f"stam {p['stamina']:>3}  HA {ha}")
    if a.weapon:
        ids = weapon_ids()
        wid = ids[a.weapon]
        sa = SaRates()
        print(f"\n{a.weapon} {a.grip}: exchange against the pool (win/trade/loss, P first, trade-through, "
              f"factors)")
        for atk in ATK.weapon_attacks(reg, wid, a.grip):
            t = first_hit(atk)
            if t is None:
                continue
            hit = {"startup": t + (atk.get("release_lead_in") or 0.0),
                   "poise": atk["poise_damage"] * POISE_MENU * sa(atk["atk_row"])}
            x = slot_exchange(pool, reg, wid, atk, hit)
            tt = "-" if x.get("trade_through") is None else f"{100 * x['trade_through']:.0f}%"
            print(f"  {atk['slot']:<16} first {hit['startup']:>5.1f}  poise {hit['poise']:>5.0f}  "
                  f"win {100 * x['win']:>3.0f}% trade {100 * x['trade']:>3.0f}% loss {100 * x['loss']:>3.0f}%  "
                  f"P1st {100 * x['p_first']:>3.0f}%  thru {tt:>5}  stam {x.get('cost') or 0:>3} "
                  f"per bar {x['per_bar'] or 0:>4.1f}  regen@{x.get('regen_blocked_until') or 0:>5.1f} "
                  f"swings/{FIGHT_WINDOW_S:g}s {x.get('swings') or 0:>5.2f}  "
                  f"f_st {x['f_startup']:.3f} f_ha {x['f_hyper']:.3f} f_stam {x['f_stamina']:.3f} "
                  f"(old {x.get('f_stamina_swings', 1.0):.3f}) = {x['factor']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
