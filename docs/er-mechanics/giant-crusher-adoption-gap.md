# Giant-Crusher: model rank against real adoption (RL 150, STR PvP)

Labels as in the other files here: **VERIFIED** = regulation value or EXE code, **MEASURED** =
computed by the commands below from the regulation and the corpus mirror, **INFERRED** =
consistent with the data but the consuming code was not traced, **COMMUNITY** = outside claim.
Nothing was launched.

## Answer

The model is not anticorrelated with adoption. Every column it reports correlates weakly with
what STR PvP builds actually carry (Spearman |rho| at most 0.32), damage and poise positively,
weight, roll frame and startup negatively. The Giant-Crusher gap is narrower than "our metric is
wrong": the model's nearest twin of Giant-Crusher is the Greatsword (R1 damage rank 7 vs 3, poise
6 vs 2, R1 startup and roll within 1 frame), and the Greatsword is the single most adopted STR
PvP primary weapon, 47 builds against Giant-Crusher's 2. What separates them is mostly outside the
R1 row the ranking reads:

1. Giant-Crusher's unique R2 moveset (`a197.tae`) is 15 frames slower to hit and 15 frames later
   to roll than the Greatsword R2, with less poise (MEASURED, INFERRED file choice).
2. 3.5 more weight: 144 of 312 corpus builds could swap Giant-Crusher in and keep medium roll,
   against 187 for the Greatsword (MEASURED).
3. Things the model does not represent at all: hitbox reach and swing shape, tracking, play
   speed, crits (the parry dagger is in 44% of these builds), and taste (COMMUNITY / not
   established).

Hyperarmor does not rescue Giant-Crusher either: with the PvP `saRate` applied, 22% of the
adoption-weighted pool attacks that land first break its R1 hyperarmor (greatsword-class R1s,
655 menu poise 2H x0.45 against about 81 poise plus +99), and the Greatsword's R1 lands 1.2 frames
earlier (`exchange.md`). An earlier version of this paragraph said none do; that figure left
`saRate` out.
Two defects were found that distort rankings generally (poise surplus, endurance floor); neither
moves Giant-Crusher's rank. The first is fixed.

## 1. Adoption side (MEASURED)

Filter `tag`: Strength tag, RL 140-160, `isPvE` false and no `PvE` tag, RL equal to the
attributes, deduplicated on (user, equipped tokens) the way `er-builds-embed.load_corpus` does.
Right hand = active-set position 0-2 (seals and shields sit at 3-5).

| filter | builds | users | Giant-Crusher, right hand | Giant-Crusher, primary slot | Greatsword, primary |
|---|---|---|---|---|---|
| Strength tag | 315 | 174 | 3 (1.0%, 95% CI 0.3-2.8%), 2 users | 2 / 282 | 47 / 282 (16.7%) |
| Strength + PvP tag (Invasions, Duels, Co-op/Gank, 2v2, Ladder) | 161 | 80 | 1 (0.6%) | 0 / 146 | 26 / 146 |
| STR 60+, any tags | 173 | 118 | 3 (1.7%) | 2 / 154 | 28 / 154 |

The brief's 6/357 counts any hand and does not drop duplicates or builds tagged `PvE` with
`isPvE` false: of those 6, two carry the `PvE` tag and one holds it only in the left hand. Four
of the six hold it in the left hand as well (hardswap or powerstance).

Top primary weapons (Strength tag, 282 builds with a primary): Greatsword 47, Icon Shield 32
(9 users), Cleanrot Knight's Sword 18, Shamshir 10, Lance 10, Spiralhorn Shield 9, Devonia's
Hammer 6, Fire Knight's Greatsword 6, Claymore 6, Ruins Greatsword 6, Zweihander 6. Across all
right-hand slots the Misericorde (a parry tool) is in 138 of 315 builds (43.8%).

Representativeness:
- Concentration: one user owns 29 of 315 builds (9%); 24 users with 3 or more builds own 137
  (43%). One vote per user: Giant-Crusher 2/174 (1.1%), Greatsword 41/174 (24%).
- Recency: 2023 4, 2024 111, 2025 101, 2026 99. The Giant-Crusher builds are 2025-05, 2026-02,
  2026-03, so the gap is not an old-patch artifact.
- It is a planner of published intentions, not a match log. Tags are self-assigned.
- Weapons outside the grease sweep (uniques, shields, seals): 136 of 487 right-hand slots
  (27.9%), and 125 of 315 builds (39.7%) carry at least one. The ranking cannot see them
  (Icon Shield, Devonia's Hammer, Ruins Greatsword, Sword of Light, Greatsword of Solitude).
- Grease: 47 of 315 builds (14.9%) carry any grease; Drawstring Dragonbolt 15 (4.8%). Heavy is
  the most common infusion on sweep weapons.
- Defenders: menu poise percentiles 10/25/50/75/90 = 54/76/88/109/119 (Bull-Goat applied when
  the planner computed it). Endurance 30/39/46/50/53. Max stamina median 155. STR median 58,
  90th percentile 75. Equip load: 275 of 312 medium roll, median 60% of max. Armor alone
  median 47.0.

## 2. The model's columns against adoption (MEASURED)

Spearman rho between a weapon's primary-slot count and its 2H R1 #1 column, 161 sweep weapons
(48 adopted). "Adopted only" drops the zeros.

| column (sign: higher is better for the player) | all | adopted only |
|---|---|---|
| R1 damage | +0.32 | +0.19 |
| R2 damage | +0.31 | +0.15 |
| R1 poise | +0.29 | +0.24 |
| share of defenders one R1 staggers | +0.29 | +0.25 |
| R1 hyperarmor | +0.28 | +0.19 |
| R1 damage per chain cycle | +0.02 | -0.05 |
| earlier R1 startup | -0.20 | -0.23 |
| earlier roll | -0.26 | -0.27 |
| lower stamina | -0.21 | -0.18 |
| lower weight | -0.31 | -0.17 |

Counting any right-hand slot instead (Misericorde included) every rho shrinks toward zero
(damage +0.23, weight -0.21). The model's top 20 by R1 damage hold 77 of 282 primaries, 47 of
them the Greatsword; the bottom half holds 45. So the model is weakly aligned, not inverted, and
no column explains adoption on its own: the players pick heavy weapons and fast ones, and the
middle is empty.

### The twin comparison (MEASURED, 2H Heavy + lightning grease, RL 150)

| | Giant-Crusher | Greatsword |
|---|---|---|
| primary builds (users holding it in any right-hand slot) | 2 (2) | 47 (41) |
| R1 #1 damage (mean over 1,074 PvP defenders, RL 140-160) | 771 | 680 |
| R1 #1 PvP poise (menu x `saRate`), share of STR defenders staggered | 819, 100% | 655, 100% |
| R1 #1 first hit / next / roll, real frames | 17.9 / 30.3 / 35.9 | 16.7 / 32.7 / 37.7 |
| R1 active frames | 3 | 4 |
| R2 #1 first hit / next / roll, poise | 27.2 / 44.2 / 49.2, 635 | 17 / 37 / 39, 721 |
| crouch R1 first hit / next / roll, type | 14 / 37 / 35 (rolling R1 anim), strike | 16.3 / 32.3 / 33.3 (0.86 play speed), pierce: counter 644 to 735 |

Re-measured 2026-09-29 with play speed, imports, gated hitboxes, the 252/253 fix, `saRate` and
the corpus defenders. The earlier clip-time rows (R1 22 / 37 / 43 and 21 / 37 / 42) are
superseded.
| R1 stamina (R1s per median 155 bar) | 31 (5.0) | 27 (5.7) |
| weight; builds that keep medium roll after the swap | 26.5; 144 / 312 | 23.0; 187 / 312 |
| damage type (R1) | strike | standard |
| R1 hyperarmor, PvP poise-damage multiplier inside it | +99, 0.45 (rows x1) | +90, 0.65 (rows x0) |
| PvP HP-damage multiplier inside it | 0.825 | 0.925 |

Everything on the R1 row is a wash or favors Giant-Crusher on paper. The R2 row, weight and the
exchange are where they differ, and all three favor the Greatsword: with `saRate` applied 22% of
first-landing pool attacks break GC's R1 hyperarmor, and the Greatsword's R1 lands first
(`exchange.md`).

## 3. Candidate causes, tested

| candidate | finding | label | cause of the gap? |
|---|---|---|---|
| Ranking metric (one-slot damage and poise) | rho with adoption +0.32 / +0.29: a real but small signal. Speed columns are separate and each carries a similar small signal of the opposite sign | MEASURED | partly: the metric is not wrong, it is incomplete |
| Poise surplus | GC R1 234 menu poise against a defender maximum near 121 (90th percentile 119). 60 of 161 2H weapons already stagger 90%+ of STR defenders; ranking raw poise rewards surplus. Against all 1396 builds in the window, 112 of 322 combos stagger 99%+ | MEASURED | model defect (fixed, section 4); does not move GC |
| Recovery and punishability | GC R1 roll 43 (rank 317/322), 18 frames after the hit; Greatsword 42. Same class, same punish window | MEASURED (TAE, VERIFIED consumer) | no, not against its twin; yes against light weapons |
| Hyperarmor trades vs PvP `unk1` and `saRate` | With `saRate` and `unk1` 0.45 both applied (they multiply on one path, `FUN_140486bf0`, bd `pvp-poise-damage-expression-sarate-unk1-2026-09-29`), greatsword-class R1s (504 menu 1H, 655 2H, x0.45 = 227 / 295) exceed about 81 poise plus GC's +99, and 22% of the pool attacks that land first break GC's R1 hyperarmor. The Greatsword 2H R1 lands at 16.7, before GC's 17.9, and keeps 98% trade-through (`exchange.md`). The earlier "none do" left `saRate` out | MEASURED; multiply VERIFIED | partly: the twins separate on the 1.2-frame startup gap in the exchange, not on hyperarmor |
| Poise breakpoints against real defenders | GC R1 staggers 100% of STR defenders; the median weapon's 2H R1 staggers 21% | MEASURED | no; GC reaches every breakpoint |
| Stamina vs corpus endurance | 5.0 GC R1s per median bar vs 5.7 Greatsword | MEASURED | minor |
| Weight and equip load | model builds use END 33 (all-builds median); STR PvP median END is 46-48. At END 33 base max load is 81.5, so GC plus median armor (73.5) is heavy roll. Raising END to medium roll (GC END 50, STR 83 to 66) costs GC 4.5% R1 damage (760 to 726), rank unchanged; Greatsword END 47, 664 to 640 | MEASURED | model defect, fixed 2026-09-29 (section 4.1: END 48 for GC, charge 2.0% vs 1.4%); weight separates the twins by fit (144 vs 187 here, 24% vs 48% of STR PvP kits) far more than by damage |
| Reach and tracking | not modelled. Hit capsules run between weapon dummy polys whose positions live in the weapon model, not the params; AtkParam radius GC 0.5 vs Greatsword 0.4 (row 2300200 vs 400200). Colossal swords sweep horizontally and colossal weapons slam vertically | radius VERIFIED (regulation); swing shape COMMUNITY | likely, not measured |
| Play speed | modelled since 2026-09-29 (TAE 608). Greatsword crouch R1 plays at 0.86 and lands at 16.3, after GC's crouch R1 at 14; the crouch's edge is the pierce counter (x1.15), not speed | VERIFIED consumer (reach.md section 5) | no speed edge for the Greatsword |
| GC R2 file choice (`spAtkcategory`) | GC is the only weapon with `spAtkcategory` 197; `a197.tae` holds exactly the eight R2 animations 030500-030515 and 032500-032515 and fires the R2 judges; `behaviorVariationId` 2304 has its own BehaviorParam rows for exactly those R2 judges (plus jump R2, counter, 89x). Reading R2 from `a031` instead would give 2H R2 #1 startup 16, not 32. The code that picks the file was not traced; the only named consumer found, `CSAiFunc::GetWepSpAtkCategoryNo` 0x1403004a0, is the AI's | data consistent: INFERRED | yes, if the choice is right; the R2 is GC's largest measured deficit |
| Weapon skill / ash | GC is infusable and takes ashes like the Greatsword; nothing to separate them in the data read here | INFERRED | no evidence |
| FinalDamageRateParam `saRate` | GC R1 3.5, Greatsword R1 3.5, Longsword R1 2.2, dagger 1.35. It is the PvP poise-damage multiplier (consumer traced); the 51-poise breakpoint is PvE only. Applied in `er-builds-pvp.py` | VERIFIED | no: both twins stagger every defender |
| Grease assumption | 14.9% of builds carry any grease. Stripping the grease from every build (same affinity and stats) leaves GC first among single-hit R1s (760 to 718) | MEASURED | no |
| Uniques excluded from the sweep | 27.9% of right-hand slots, 39.7% of builds | MEASURED | no for GC's rank; yes for the adoption denominator |
| Co-timed extra hitbox on great spears | Lance and Messmer Soldier's Spear R1 fire judge 5000-5002 on the same frames as the main hit (BehaviorParam 50000000x through the variation-0 fallback, AtkParam 1703001-3, MV 200/300/410, unnamed). Those hitboxes carry a nonzero stateInfo gate at Args+0xe and fire only while a SpEffect with stateInfo 187 is active, so they are skipped since 2026-09-29 | VERIFIED gate | was a defect, fixed |

## 4. Model changes

- Fixed: `scripts/er-builds-pvp.py` now reports `stag%` per slot, the share of the RL window's
  corpus builds whose menu poise is below the hit's, and `--sort stagger` ranks by it (ties by
  damage). Raw `--sort poise` is kept. `python3 scripts/er-mechanics-attacks.py --selftest`
  still passes 43/43.
- Fixed 2026-09-29: the great-spear co-timed hitboxes are stateInfo-gated and skipped (section 3).
- Fixed 2026-09-29: the R2 file choice is traced (`FUN_1403f1d40`: `spAtkcategory`, then
  `wepmotionCategory`), so the `a197` R2 is VERIFIED.
- Fixed 2026-09-29: the grease sweep is regenerated with same-archetype PvP floors and the
  medium-roll Endurance, covers fixed-affinity weapons, and carries a weight charge per row
  (section 4.1).
- Stale doc: `attacks.md` section 4 lists Giant-Crusher 2H charged R2 as f58-62 with R1 87,
  roll 87; those are `a031` numbers. The tool now reads `a197` (f58-60, R1 80, roll 87, clip
  130).

### 4.1 Sweep regeneration, fixed-affinity weapons and the weight charge

Command: `python3 scripts/er-builds-optimize.py --grease-sweep 150-200 --floors pvp --roll medium`
(the defaults), written to `~/.cache/er-build-planner/grease-sweep-dlc-drawstring-150-200.jsonl`.
27 minutes on 16 jobs under a load average near 50.

**Floors (MEASURED).** The old cache held END 33 or 34 on every row, a floor from all builds
of the window and no roll constraint. The new rows take VIG, MND and END from PvP builds of
the weapon's archetype and raise END until the pool's median kit is at medium roll. At RL 150
END is now 38 (10th-30th percentile of rows) to 45; STR PvP floor VIG 58, MND 10, END 45. Damage
of the same 3,564 (weapon, grip, RL) rows moved by 0.969-0.996 of the old value at RL 150 (10th
to 90th percentile) and by 1.000-1.026 at RL 200, where the new floors are lower. Giant-Crusher
2H: END 33 to 48, STR 83 to 73, R1 #1-basis damage 652 to 633. Greatsword 2H: END 45, STR 74,
591 to 580. Giant-Crusher stays first by one-hit damage among 2H weapons; the Greatsword goes
from 5th of 162 to 6th of 411.

**Fixed-affinity weapons (MEASURED).** The sweep now takes three kinds, `kind` in each row
(`sweep_kind`): `greasable` (the original 162 weapons, rows unchanged in method), `unique` (no
ash of war, its one affinity and its own skill; 180 weapons) and `ungreasable` (ash-of-war
weapons none of whose affinities takes grease: shields, hand-to-hand arts, Smithscript; 69
weapons). A fixed-affinity weapon is tried ungreased and, when its row has `isEnhance`, greased
(18 of 360 unique rows at RL 150). Left out on purpose: bows, crossbows, ballistae, staves,
seals, ammunition, throwables, perfume bottles and Unarmed, whose damage is not a melee hit.
Coverage of the Strength-tag corpus (section 1): right-hand slots outside the sweep 136/487
(27.9%) to 21/487 (4.3%), builds with one or more 125/315 (39.7%) to 21/315 (6.7%). What is left
is seals (Frenzied Flame 5), bows and perfume bottles. Top 2H uniques by one-hit damage at RL
150: Great Club 626, Troll's Hammer 568, Anvil Hammer 549, Ruins Greatsword 547.

**Weight charge (design INFERRED, numbers MEASURED).** `weight_charge` in
`er-builds-optimize.py` charges weight as the damage the Endurance costs. For each kit of the
floor pool (398 STR PvP builds at RL 140-160) it takes the Endurance that kit needs to keep
medium roll with this weapon swapped in for its heaviest right-hand weapon, never below the
floor, and prices each Endurance point by rerunning the sweep's spread walk with that
Endurance (grid of 3, linear between). The row's `weight.factor` is the pool's mean damage
over the damage at the Endurance the row was built at, so the median-kit cost already inside the
row's damage is not charged twice. It also reports `fit`, the share of kits that keep medium
roll at their own Endurance, which is the accessibility figure of section 1. The formula agrees
with the planner: the model's max load equals the planner's `maxEquipLoad` on the builds
checked, and 371 of 398 STR PvP kits (93%) are at medium roll with their own weapon by the
model against 366 by the planner, 393 in agreement.

| RL 150, STR PvP pool | Giant-Crusher (26.5) | Greatsword (23.0) |
|---|---|---|
| kits that keep medium roll at their own END after the swap (`fit`) | 24% | 48% |
| kits that need END above the floor 45 | 62% | 50% |
| mean END needed | 51.1 | 49.3 |
| 2H damage weightless (END 45) / at the row's END / pool mean | 639 / 633 (END 48) / 626 | 580 / 580 (END 45) / 571 |
| total weight cost from END 45 to the pool mean | 2.0% | 1.4% |
| `factor` (charge on top of the row) | 0.989 | 0.986 |

Across all 9,042 rows the factor runs 0.947 to 1.000, median 0.996. So priced through
Endurance, the twins' 3.5 weight differs by about 0.6% of damage, while the share of existing
kits that can take the weapon without a respec halves (48% to 24%). The damage cost of weight is
small; if weight is what keeps Giant-Crusher out of builds, it acts through the respec or the
armor and poise a player would give up, which this charge does not model (INFERRED).
`fit` is therefore reported but kept out of the charge: it is an adoption statistic, and
putting it in the score would rank by what players already carry.

Integration into `er-builds-pvp.py --sort score` belongs to that file's owner: multiply each
slot's combined score by the sweep row's `weight.factor` (1.0 when the row has none), and
show `fit`. Its `build_for(greased_only=True)` must skip rows with neither a greased nor a
Quality configuration, which fixed-affinity rows now are.

`er-builds-pvp.py --rl 150 --sort score` on the new sweep (MEASURED, 19:21 2026-09-29; that
file was being changed by other work between the two runs, so the rank change mixes its edits
with this sweep): 822 (weapon, grip) rows against 324 before, 15 fixed-affinity weapons in the
top 50 (Claws of Night 2H 4th, Alabaster Lord's Sword 2H 9th), Giant-Crusher 2H 1st before and
28th now, the Greatsword 2H 22nd before and 91st now. Multiplying each weapon's best-slot score
by `weight.factor` moves nothing more than 18 places: Giant-Crusher 2H stays 28th, the
Greatsword 2H goes to 97th. Adopted uniques: Devonia's Hammer 2H 85th, Bloodhound's Fang 96th,
Ruins Greatsword 135th, Icon Shield 768th (best slot a running R2, and the pvp script finds no
skill for it; not traced).

## 5. Ranked causes

1. Heavy-weapon class economy, shared with the twin: roll 35.9 and first hit 17.9 real frames
   (Greatsword 37.7 and 16.7). That explains why colossal weapons as a class are rare (wepType 41, 4.5% of
   right-hand slots), not why GC loses to the Greatsword (MEASURED).
2. GC's slower unique R2 (27.2 vs 17 first hit, roll 49.2 vs 39) and its extra 3.5 weight
   (medium roll fit 144 vs 187 of 312). Both are in the data and the ranking reads neither
   (MEASURED; file choice VERIFIED). Model defect in scope: the ranking is one slot at a time. A weapon
   score should combine R1 and R2 and charge weight through the equip-load constraint.
3. Reach, swing shape and tracking (play speed is modelled since 2026-09-29; reach from
   `er-mechanics-reach.py` is a factor of `er-builds-pvp.py --sort score`). The hitbox capsule is set by the
   weapon model's dummy polys, which this toolchain does not read (not established;
   COMMUNITY for the swing shape).
4. Meta and taste: Misericorde carried in 44% of builds (a parry skill equipped in only 11.4% of
   STR PvP builds), the Greatsword crouch R1's pierce counter, the Greatsword's popularity beyond any
   measured edge, and a corpus where 24 users hold 43% of builds (COMMUNITY / INFERRED).
5. Model defects that did not change GC's position: poise surplus, the great-spear hitbox sum
   (both fixed) and the END floor (fixed, sweep regenerated 2026-09-29, section 4.1).

## Commands

```bash
python3 scripts/er-builds-optimize.py --grease-sweep 150-200    # section 4.1, about 30 min
python3 scripts/er-builds-optimize.py --selftest                 # weight_charge, sweep_kind
python3 scripts/er-builds-pvp.py --rl 150 --json > pvp150.json
python3 scripts/er-builds-adoption-gap.py --pvp pvp150.json         # sections 1-2, trade, fits
python3 scripts/er-builds-pvp.py --rl 150 --weapon Giant-Crusher
python3 scripts/er-builds-pvp.py --rl 150 --weapon Greatsword
python3 scripts/er-builds-pvp.py --rl 150 --slot r2_1 --top 400      # R2 ranks
python3 scripts/er-builds-pvp.py --rl 150 --sort stagger --top 400   # stag% ties
python3 scripts/er-mechanics-attacks.py Giant-Crusher --grip both --json
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"SpAtk"}'
```

One-off checks, run as read-only snippets against the same modules:
- `a197` vs `a031` windows: `tae_animations(197)` and `tae_animations(31)` from
  `er-mechanics-attacks.py`, type-1 events of anims 030500-032515; BehaviorParam rows with
  `id // 1000 == 102304`.
- Grease stripped: `slot_hit(..., grease=None)` for every sweep row's `build_for` result.
- Endurance: raise `vit` until `calc_correct(GRAPH_EQUIP_LOAD, vit) * 0.699 >= weight + 47.0`
  (`er-mechanics-resources.py`), take the points from the highest damage stat, rescore R1 #1.
- `saRate`: `FinalDamageRateParam[AtkParam_Pc.finalDamageRateId]` times R1 #1 poise against the
  STR defender poise list.
- Intervals: Wilson 95%.
