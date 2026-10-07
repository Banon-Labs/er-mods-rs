# Elden Ring attack rating and stat scaling

How a weapon's attack rating (AR), status build-up and catalyst spell buff come from the regulation
and the executable. The implementation is `scripts/er-mechanics-ar.py`. Its `--selftest` checks 44
values against an independent calculator and passes all 44.

Labels:
- **VERIFIED**: read from regulation params or from the executable.
- **INFERRED**: consistent with the data, but the code was not traced.
- **SITE**: the er-build-planner JS bundle (`~/.cache/er-build-planner/js/notifications-BSZ1DATO.js`
  around offset 3635000, and `router-B83O9Qb7.js` around 858000).
- **COMMUNITY**: ThomasJClark/elden-ring-weapon-calculator (`master`, `src/calculator/calculator.ts`,
  `src/regulationData.ts`, `src/buildData.ts`).

Every EXE address is a 1.16.2 VA from the named Ghidra dump on :8765. For 1.16.2 the dump, the
de-Arxan'd image and the live process all use the same VA. The installed game is 1.17.1, and all of
these functions sit below the rva `0xafefe9` boundary. `docs/recon/rva-map-1162-to-1170.functions.tsv`
maps them to 1.17: `0x690870`->`0x6916c0`, `0x690f30`->`0x691d80`, `0x690390`->`0x6911e0`,
`0x6832a0`->`0x6840f0`, `0x686670`->`0x6874c0`, `0x686ff0`->`0x687e40`, `0x7c0390`->`0x7c1210` and
`0x7c0430`->`0x7c12b0`. With capstone, 1.16.2 and `eldenring-deobf-1.17.1.bin` give the same
instruction count and the same mnemonics for `PerformCalcCorrection` (151/151) and `FUN_140690390`
(272/272). `PerformWeaponScaling` matches 97/98; the only difference is `int3` vs `nop` padding. On
1.17.1 its constants resolve to the same 20.0/100.0/1.0/-1.0, and the two-handing constant is still
1.5 (loaded at `0x140691349`). So the formula below holds for the installed build (VERIFIED).

## 0. The chain from a weapon to its numbers

```
EquipParamWeapon id = base + affinity*100 + level
  row           = floor(id/100)*100                                  EquipParamWeapon::GetEntry 0x140d54600
  reinforce row = row.reinforceTypeId + id % 100                     same function, tail
  AECP row      = row.attackElementCorrectId (AtkParam may override) 0x140690390, 0x14069051c
  graph per element = row.correctType_{Physics,Magic,Fire,Thunder,Dark}
  graph per status  = row.correctType_{Poison,Blood,Sleep,Madness}
```

- **Id layout (VERIFIED).** `GetEntry` looks up `(id/100)*100` and then
  `ReinforceParamWeapon::GetEntry(reinforceTypeId + id - (id/100)*100)`.
- **Affinity (VERIFIED from row names).** The affinity is `(id % 10000) / 100`, taken from the
  Uchigatana rows 9000000..9001200: 0 Standard, 1 Heavy, 2 Keen, 3 Quality, 4 Fire, 5 Flame Art,
  6 Lightning, 7 Sacred, 8 Magic, 9 Cold, 10 Poison, 11 Blood, 12 Occult.
- **What an affinity row changes (VERIFIED, params).** Each affinity is its own row, with its own
  `attackBase*`, `correct*`, `reinforceTypeId` (base + affinity*100: Keen Uchigatana has 200),
  `correctType_Physics` (Heavy 1, Keen 2, Quality 8, Occult 7) and `attackElementCorrectId`
  (Fire 10005, Poison/Blood/Occult 10013).
- **Standard vs somber (VERIFIED, params).** A reinforce type has rows `type+0 .. type+N`, and `N`
  is the max level. Type 0 runs to 25 and type 2200 to 10. `maxReinforceLevel` is not a reliable
  max: row 2210 (somber +10) carries 25. The script counts contiguous rows, which is also what the
  COMMUNITY build script does.
- **"Holy" (VERIFIED).** In code and params, holy is `Dark`: `attackBaseDark`, `darkAtkRate`,
  `correctType_Dark`, AECP `*_byDark`. Element indices are 0 physical, 1 magic, 2 fire,
  3 lightning (`Thunder`), 4 holy (`Dark`). This follows from the correctType offsets that
  `FUN_140690390` picks by index: `+0xec`, `+0x17d`, `+0x17e`, `+0x17f`, `+0x18e`.

## 1. Base attack per element (EXE)

`FUN_1406832a0` computes one hit's damage for all 5 elements. For each element (the
non-arrow branch of the decompile, which calls `FUN_140690390` once per element):

```
hit[e] = ( (attackBase[e] * reinforce.<e>AtkRate  [+ arrow term])  * AtkParam.atk<e>Correction * 0.01
           + isAddBaseAtk ? reinforce.baseAtkRate * AtkParam.atk<e> : 0 )
         * M[e] * param_5 * ctx-rate[e] * ctx-vector[e] * throwRate * FUN_140691320(...)
         + ctx-add[e]
```

- `attackBase[e] * <e>AtkRate` is the upgraded base (VERIFIED).
  `ReinforceParamWeapon.physicsAtkRate` at +25 on type 0 is 2.45, so Uchigatana 115 becomes 281.75.
- `AtkParam.atk<e>Correction * 0.01` is the motion value (VERIFIED multiply; constant
  `0x3c23d70a` = 0.01).
- `M[e]` is the stat multiplier from section 2 (VERIFIED). The rest are per-attack context terms;
  section 7 says what writes each one.

So **AR[e] = base[e] * M[e]**, where `base[e] = attackBase[e] * reinforce.<e>AtkRate`. The planner's
"base + scaling" split is `base` and `base*(M-1)`. The status screen is assumed to evaluate the same
product with MV 100 (INFERRED; the menu path was not traced).

A second weapon row (`param_4`) supplies extra base when it is set and differs from the first. That
row is used for scaling when `weaponCategory == 13` (`0x1406834af`). This is the bow/arrow path by
its shape (INFERRED). The script models bows from the bow row alone, as COMMUNITY does.

## 2. Stat multiplier per element (EXE)

### 2a. One stat: `PerformWeaponScaling` 0x140690870

Arguments: `(requirement, stat, rate, graphId, name, _, statMult, useRawStatForGraph)`.

```
eff = trunc(stat * statMult)
if requirement - eff > 0 and requirement > 0:                            # requirement unmet
    short = min((1 - eff/requirement) * 100, 20.0)                       # 20.0 @ 0x143b33d80
    k     = (100.0 - 100) / (1 - 20*20)          = 0                      # 100.0 @ 0x143b33d84
    out   = (short^2 * k + 100) / 100 - PlayerCommonParam.lowStatus_AtkPowDown
          = 1 - 0.4 = 0.6                                                 # floor 0
elif rate > 0:
    out = 1 + rate/100 * PerformCalcCorrection(useRaw ? stat : eff, graphId) / 100
else:
    out = 1
```

- **The penalty is a flat 0.6 (VERIFIED).** The shortfall curve is disabled, because the two
  constants make `k = 0`. `DAT_143d69950` (a sign flip) is 0 in the image. `lowStatus_AtkPowDown`
  is 0.4 in the single `PlayerCommonParam` row.
- **The requirement check runs before the rate check (VERIFIED).** A stat the AECP flags for an
  element but the weapon scales at 0 (Heavy Uchigatana DEX) still costs 40% when its requirement
  is unmet. The COMMUNITY calculator does the same.
- **Rate (VERIFIED).** `rate = (AECP overwrite >= 0 ? overwrite : EquipParamWeapon.correct<Stat>)
  * ReinforceParamWeapon.correct<Stat>Rate`. The getters are `0x140d53db0` (STR, +0x24 * reinforce
  +0x1c), `0x140d53c60` (DEX, +0x28 * +0x20), `GetCorrectMagic`, `0x140d53cb0` (FTH) and
  `GetWeaponArcaneScaling` `0x140d53d00` (arc, `correctLuck * correctLuckRate` +0x60). The per-hit
  path adds a context int (`ctx+0xb4..+0xc4`, converted to float) to the rate: the SpEffect
  `change*Point` sum, after the reinforce multiply and before the `rate > 0` test (section 7).

### 2b. The curve: `PerformCalcCorrection` 0x140690f30

```
row = CalcCorrectGraph[graphId]           (GetCalcCorrectGraphParam 0x140d34270; missing row -> return x)
x   = min(x, stageMaxVal4)
if x <= 0: return stageMaxGrowVal0
i   = first stage in 0..3 with x <= stageMaxVal[i+1]
t   = (x - stageMaxVal[i]) / (stageMaxVal[i+1] - stageMaxVal[i])
adj = adjPt_maxGrowVal[i]
adj >= 0:  g = grow[i] + t^adj * (grow[i+1]-grow[i])
adj <  0:  g = grow[i] + (1 - (1-t)^(-adj)) * (grow[i+1]-grow[i])
return clamp(g, min(grow[i],grow[i+1]), max(grow[i],grow[i+1]))
```

- The row offsets are VERIFIED: the decompile reads `pad-0x4c` = `stageMaxVal`, `-0x38` =
  `stageMaxGrowVal` and `-0x24` = `adjPt`, with `pad` at +0x4c of the 0x50-byte row.
- `init_inclination_soul`, `adjustment_value`, `boundry_*` are not read.
- The final clamp is INFERRED. The decompile computes both bounds, but Ghidra renders the in-range
  return slot as a stack temp.
- The stage boundary is inclusive on the upper end (`x <= stageMaxVal[i+1]`).
- The output is a percent. Graph 0 at 18/60/80/150 gives 25/75/90/110.
- **Two-handing cap (VERIFIED + params).** Physical graphs 0, 1, 2, 7 and 8 end at
  `stageMaxVal4 = 150`, and 99 STR two-handed is trunc(148.5) = 148, so there is no cap below 148.
  Graphs ending at 99 (e.g. 16, catalyst physical) get nothing above 99. The SITE clamps 2H STR to
  150, and COMMUNITY evaluates each graph up to 148.

### 2c. Five stats into one element: `FUN_140690390` (per hit) / `FUN_140686670`, `FUN_140686ff0` (stat only)

```
AECP entry per (element, stat) = {enabled, overwrite, influence}         FUN_140d23280
    enabled   = is<Stat>Correct_by<Element> bit
    overwrite = (float) overwrite<Stat>CorrectRate_by<Element>   (-1 = unset; "set" is 0 <= v, FUN_140d23ae0)
    influence = Influence<Stat>CorrectRate_by<Element> * 0.01
for each stat: m_s = enabled ? influence * PerformWeaponScaling(...) : 1
M = any(m_s < 1) ? min(1, m_s...)                                       0x140690818 minss chain
                 : 1 + sum(m_s - 1)                                      0x1406907e0 subss/addss chain
```

- Every stat uses the element's graph (`correctType_<Element>`), not a per-stat graph (VERIFIED:
  one `uVar6` is passed to all five calls). So Keen Uchigatana's physical graph 2 applies to both
  STR and DEX.
- **Unmet requirements.** One unmet requirement on an enabled stat takes the element to 0.6 of
  base, whatever the other stats are (VERIFIED). An influence below 100 can also pull `m_s` below 1
  and switch the whole element to the min branch (VERIFIED from code). Several AECP rows carry
  influence  100 (40000, 42310/42311, 50001..52000). In the 1.17.1 regulation, no weapon's own
  `attackElementCorrectId` points at one of them (VERIFIED, params): they are reachable only
  through `AtkParam.overwriteAttackElementCorrectId`. COMMUNITY ignores influence and SITE applies
  it (`r-1+o*a*c*r`).
- **Weapons with an AECP overwrite (VERIFIED, params).** The Smithscript affinity rows
  20010103..20010105 set `overwrite{Magic,Faith}CorrectRate_byPhysics = 5`. The self-test covers
  Magic Smithscript Greathammer.
- **Two-handing (VERIFIED).** When the attack context byte `+0xf5` is set, the STR call gets
  `statMult = 1.5` (`0x143b33d7c`). DEX, INT, FTH and ARC always get 1.0 (`__real_3f800000`).
  `AtkParam.isDisableBothHandsAtkBonus` (+0x18a bit 2) is passed as `useRawStatForGraph`: the
  graph then reads raw STR, but the requirement check still uses `trunc(STR*1.5)`. That quirk is
  VERIFIED from code; no weapon test exercises it.
- **Two-handing predicate (COMMUNITY).** What sets `+0xf5` was not traced. COMMUNITY gives the
  bonus to any two-handed weapon except paired ones (`isDualBlade`, 55 base rows in 1.17.1, e.g.
  Starscourge Greatsword). Bows, greatbows, crossbows and ballistae (`wepType` 50/51/53/55/56)
  always get it. The script follows this rule.
- **Per-attack AECP (VERIFIED).** `AtkParam.overwriteAttackElementCorrectId` (+0x198), when >= 0,
  replaces the weapon's AECP for that attack.

## 3. Status build-up (arcane)

- **Only four statuses scale (VERIFIED).** `FUN_1406832a0` ends with four calls to the arcane
  wrapper `FUN_140690b40` (requirement `properLuck` +0x195, stat `player+0x2a8`, rate
  `GetWeaponArcaneScaling`, `statMult` 1.0 from `0x14329e678`, raw flag 0). The graphs are
  `correctType_Poison` (+0x18f), `correctType_Blood` (+0x194), `correctType_Sleep` (+0x23e) and
  `correctType_Madness` (+0x23f), stored at out+0x18, +0x20, +0x2c and +0x30. Frost, scarlet rot
  and death blight get no multiplier. The multiplier includes the same 0.6 requirement penalty.
- **Base build-up (COMMUNITY + params).** For each slot `N` in 0..2 the base is
  `SpEffectParam[spEffectBehaviorIdN + ReinforceParamWeapon.spEffectId(N+1)].<status>AttackPower`.
  Examples: Blood Uchigatana 105050 + `spEffectId1` (0..25 on type 1100) is bleed 57..82. Poison
  Uchigatana has 6406 (bleed 38, offset `spEffectId1` stays 0) plus 106000 + `spEffectId2` (25 at
  +25), which is poison 95. The ids and values are VERIFIED from params. The slot-to-offset pairing
  was not traced in code.
- **Final status value (INFERRED).** `status = base * arcaneMultiplier`. The multiplier is
  VERIFIED; the consumer that multiplies the SpEffect value by it was not read. It matches
  COMMUNITY on every tested status (e.g. Poison Uchigatana +25, ARC 30: 95 * 1.1142 = 105.85).

## 4. Catalyst spell buff (EXE)

- **Sorcery (VERIFIED).** `FUN_1407c0390`: if `enableMagic`, the buff is
  `100 * FUN_140686ff0(...)`, the stat-only magic-element multiplier.
- **Incantation (VERIFIED).** `FUN_1407c0430`: if `enableMiracle`, the buff is
  `100 * FUN_140686670(...)`, the holy-element multiplier.
- The stat-only variants pass `statMult = 1.0`, so two-handing never changes the buff (VERIFIED).
  They use the weapon's own AECP, and element and graph come from `correctType_Magic` or
  `correctType_Dark`. An unmet requirement on an enabled stat gives 60.
- Glintstone Staff +25 at INT 80 gives 329.5, and Finger Seal +25 at FTH 60 gives 286.75, both
  equal to COMMUNITY.
- **How a spell hit uses the buff (SITE + COMMUNITY, not traced).** The SITE's `spellAR` gives
  `flat * sum(spellBuff terms)`, and COMMUNITY gives `100 * totalScaling`. The spell's
  `AtkParam.atk<e>` is taken as the flat value, multiplied by `buff/100` (INFERRED). The
  MagicParam -> Bullet -> AtkParam chain was not traced.

## 5. PvP

- **Weapon fields (VERIFIED, params).** `EquipParamWeapon.vsPlayerDmgCorrectRate_*` sits at
  +0x1f8..+0x214, +0x278/+0x27c and +0x288/+0x28c. All of them are 1.0 on all 3636 rows of the
  1.17.1 regulation, so they are currently inert. Their reader was not located.
- **SpEffect PvP rates (VERIFIED).**
  `CS::SpecialEffect::CalculateAtkPlayerDmgCorrectRates` (`0x1404f5080`) multiplies
  `SpEffectParam.atkPlayerDmgCorrectRate_{Physics,Magic,Fire,Thunder,Dark}` over every active,
  applicable SpEffect. This is where the per-weapon-class PvP reductions live.
- **Which hits count as PvP (VERIFIED).** `ShouldUsePvPDamage` (`0x140682d50`) is true for a
  player, or for an NPC whose `NpcParam.isCalculatePvPDamage` is set.

## 6. Validation

`python3 scripts/er-mechanics-ar.py --selftest` covers two kinds of reference:

- **Curve points.** 9 points read straight off `CalcCorrectGraph` rows 0 and 6.
- **Weapon values.** 35 values from the COMMUNITY calculator, run unmodified under deno against its
  own `public/regulation-vanilla-v1.17.js` on 2026-09-29. That is independent code and an
  independent param extraction; this repo reads the installed 1.17.1 regulation.
- **Coverage.** The weapon cases cover:
  - standard, Keen and Heavy affinities;
  - the unmet-requirement penalty, including one met only through the 2H STR bonus;
  - 2H STR at 99 on graph 0;
  - sorcery and incantation buffs;
  - a somber weapon (Moonveil +10);
  - Sacred, Occult, Poison, Blood and Cold affinities;
  - an AECP overwrite (Magic Smithscript);
  - a bow (always 2H) and a paired weapon (no 2H bonus);
  - Rivers of Blood fire plus bleed.
- **Result.** 44/44 pass, within 0.02 or 1e-4 relative. The values agree to the printed precision,
  so the 1.17.0 -> 1.17.1 regulation change did not touch these rows.
- **Rate adds (section 7).** 16 more checks: the twelve roar / War Cry rows carry
  `changeStrengthPoint` 5 and nothing else of the five, and the AR gain matches
  `base x add/100 x graph/100` on Giant-Crusher (+5 and +2.5 at 80 STR) and Sword of Night (STR
  scaling 0, AECP flags STR physical; magic unchanged). 60/60 pass.
- **SITE check.** The SITE's `Rl.getScalingPerAttribute` / `getScalingMultiplier` /
  `getBaseAndScaledEffect` implement the same structure. It uses base = `damage * reinforcement`,
  a -0.4 penalty on any unmet requirement of an enabled stat, and
  `influence-1 + override*reinforceScaling*graph*influence`. There are two SITE deviations from the
  EXE:
  - `spellBuff` returns all zeros when any stat is below any requirement. The EXE penalises per
    element instead.
  - 2H STR is `min(floor(str*1.5), 150)`, gated on an unknown predicate `tn(a)`.

## 7. The per-hit context terms (EXE + REGULATION)

Read 2026-10-01 against the 1.16.2 named dump (addresses are 1.16.2). Each term of the section 1
formula, who writes it, and what it is on a player's melee hit:

| term | writer and meaning | value on a PvP melee hit |
|---|---|---|
| `ctx+0x6c..+0x8c` (`ctx-rate[e]`) | `FUN_1404f4520`, the SpEffect accumulator: the product of `*AttackPowerRate` over the attacker's active SpEffects, which `FUN_1406832a0` multiplies into element e (VERIFIED) | modelled by `er-mechanics-buffs.py` (buffs.md section 2, talismans.md) |
| `ctx+0xb4..+0xc4` (rate adds) | the same accumulator sums `changeStrengthPoint` (+0x240), `changeAgilityPoint` (+0x244), `changeMagicPoint` (+0x248), `changeFaithPoint` (+0x24c) and `changeLuckPoint` (+0x250) as ints (getters `0x1404ff690/610/670/630/650`), with no byPoint/byRate correction (VERIFIED) | see below; modelled |
| `FUN_1404f3c60` (`ctx-vector[e]`) | a factor only from rows with `stateInfo` 315 / 316: Blue Dancer Charm and one DLC row (VERIFIED) | modelled (talismans.md) |
| `FUN_140691320` | `EquipParamWeapon.isHeroPointCorrect` (+0x107 bit 0) gate; when set, a scale from `PlayerGameData+0x60`: 1.05 + 0.025(L-1) for L in [1,3), 1.1 + 0.0157(L-3) for [3,10), 1.21 for [10,99] (VERIFIED) | 1.0: no weapon row in the regulation sets the flag |

**The rate add (VERIFIED).** `FUN_140690390` reads AttackInfo `+0xb4..+0xc4` and hands each to
the per-stat wrapper (STR: `FUN_140690c60`), which calls
`PerformWeaponScaling(req, stat, FUN_140d53db0(weapon, overwrite) + add, graph)`.
`FUN_140d53db0` is `(overwrite >= 0 ? overwrite : correctStrength) * ReinforceParamWeapon` rate, so
the add is in rate points after reinforcement. It sits before the `rate > 0` test, and the wrapper
only runs for a stat the AECP row enables, so a weapon with 0 STR scaling whose AECP flags STR for
an element gains STR scaling from it (Sword of Night). The requirement check is unchanged: an unmet
requirement still gives 0.6 and the add does nothing.

**Which rows carry it (REGULATION).** `changeStrengthPoint` 5 on Roar (841/843/846/848),
Barbaric / Milos Roar (1681/1683/1686/1688), War Cry (1811/1813/1816/1818), the unnamed 120000
and ten DLC rows 20000901..20000928. `changeMagicPoint` 100 / 30 on the unnamed
120500/120501/120510/120511. No row sets the agility, faith or luck add. The roar rows are the
weapon-buff slot (162 right hand, 163 left) and each also carries `physicsAttackPowerRate` 1.075.

**Size.** `dAR[e] = base[e] x add/100 x CalcCorrect(stat)/100` while the requirement is met.
Giant-Crusher +25 at 80 STR one-handed: AR 820.64 = 379.75 base + 440.89 scaling; the add is
379.75 x 0.05 x 0.90 = 17.09, AR 837.73, +2.1%, before the roar's x1.075 multiplies the whole
weapon part.

**Where the scripts use it.** `er-mechanics-ar.attack_rating(rate_adds=...)` (`--rate-adds str=5`)
adds it to the damage types; the spell buff (stat-only path) and the arcane status multiplier are
left without it. `er-mechanics-buffs.attack_context` sums it as `rate_points`, `kit_factors`
weights it by uptime, and `expected_attack` runs it through the weapon's own AR
(`ar_stat_ratio`), so the ranking's buff term and every skill-buff option carry it. The `--setup`
off-hand path folds a left roar's add into that option's `pre` on the left weapon's own AR.

## Not established

- The status-screen AR path. The per-hit function `FUN_1406832a0` is traced, and the menu is
  assumed to evaluate it with motion value 100 and no context buffs. Nor is it known whether the
  menu floors base and scaling separately or floors the total.
- What sets the two-handing byte `ctx+0xf5`. The paired-weapon exclusion and the always-2H bows are
  COMMUNITY claims.
- The pairing `spEffectBehaviorIdN` <-> `ReinforceParamWeapon.spEffectId(N+1)` for status
  build-up, and the consumer that multiplies the SpEffect's `*AttackPower` by the arcane
  multiplier.
- Who applies the unnamed rate-add rows 120000 and 120500..120511 (section 7). The named ones are
  the roars and War Cry.
- The bow/arrow combination (`weaponCategory == 13` branch) and arrow `attackBase`.
- How a spell's damage uses the catalyst buff (MagicParam / Bullet / AtkParam chain).
- The reader of `EquipParamWeapon.vsPlayerDmgCorrectRate_*` (all 1.0 today, so it has no effect on
  current numbers).
- The clamp at the end of `PerformCalcCorrection`. The shape is read, but the in-range return is
  garbled in the decompile; the self-test curve points agree.
- The `isAddBaseAtk` / `baseAtkRate` term, i.e. which attacks set it.
