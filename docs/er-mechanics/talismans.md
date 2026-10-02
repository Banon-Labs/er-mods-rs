# Elden Ring talismans: what each one changes, and when

Every equippable talisman, followed from `EquipParamAccessory` through its SpEffect rows to the
code that reads each field. The calculator is `scripts/er-mechanics-talismans.py`, and the
per-talisman table in section 6 is its `--markdown` output. Nothing was launched.

Labels, as in the rest of `docs/er-mechanics/`:
- **VERIFIED**: a regulation value (installed 1.17.1 `regulation.bin`, `scripts/er-param-read.py`),
  or read out of the executable (named 1.16.2 Ghidra dump on `:8765`, carried to 1.17.1).
- **INFERRED**: fits the data and the names, but the consumer was not traced.
- **COMMUNITY**: outside claim. **GAME TEXT**: the item's own `AccessoryInfo` line.

Addresses are `1.16.2 / 1.17.1`. The SpecialEffect getters have no `.pdata` entry and sit at
`+0xdd0` on 1.17.1 (byte-checked). Correction to `grease.md`: `FUN_140d24b10` is `0x140d26290` on
1.17.1. The `0x140d26220` given there is its 1.17.0 address; the function is above the `0xafefe9`
boundary.

## 0. The answer for a PvP melee (STR) build

A hit's weapon part, per element `e`, with the talisman terms marked:

```
damage[e] = curve(AR[e] * power_rate[e] * MV, DEF[e]) * absorption[e] * ...
            * damage_rate[e]            # SpEffect *AttackRate, truncated to a whole percent
            * damage_correct[e]         # atkPlayerDmgCorrectRate (vs players) or atkEnemy (vs NPCs)
            * counter[e] * counter_rate # on a counter-hit only (Spear Talisman)
```

These are the talisman numbers that apply against players, in the order of how often STR PvP
builds wear them (section 7):

| talisman | vs players | applies to | evidence |
|---|---|---|---|
| Two-Handed Sword Talisman | x1.10 (x1.15 vs NPCs), all elements | every attack whose AtkParam carries subcategory 120: two-handed R1, R2, charged, running, rolling, jump and guard-counter attacks on 403 of 434 weapons | VERIFIED |
| Bull-Goat's Talisman | poise x1/0.75 = x1.333 | always | VERIFIED |
| Great-Jar's Arsenal / Erdtree's Favor +2 / Crimson Amber +3 | load x1.19 / HP x1.04, stamina x1.10, load x1.08 / HP x1.10 | always | VERIFIED (resources.md) |
| Blue-Feathered Branchsword | damage taken x0.5 | HP <= 19.999999% of max | VERIFIED |
| Ritual Shield Talisman | damage taken x0.7 | HP exactly full | VERIFIED |
| Shard of Alexander / Warrior Jar Shard | x1.15 / x1.10 post-defense, all elements | skills (112) and charged skills (111) | VERIFIED |
| Spear Talisman | counter-hit rate x1.15 (1.15 -> 1.3225 on weapon attacks) | counter-hits, only on elements whose counter rate is above 1, which the counter rows make thrust only | VERIFIED |
| Two-Headed Turtle Talisman | stamina regen +10/s over a base of 45 | always | VERIFIED |
| Red-Feathered Branchsword | x1.20 | HP <= 19.999999% | VERIFIED |
| Rotten Winged Sword Insignia | x1.06 / 1.08 / 1.13 / 1.13 | successive-hit stages 1-4 | VERIFIED |
| Ritual Sword Talisman | x1.10 | HP exactly full | VERIFIED |
| Lacerating Crossed-Tree | x1.075 (x1.15 NPCs) | running attacks (122) | VERIFIED |
| Retaliatory Crossed-Tree | x1.12 (x1.17 NPCs) | rolling, backstep and crouch R1 (121) | VERIFIED |
| Millicent's Prosthesis | DEX +5; x1.04 / 1.06 / 1.11 / 1.11 | successive-hit stages | VERIFIED |
| Claw Talisman | x1.075 (x1.15 NPCs) | jump attacks (102), both grips | VERIFIED |
| Axe Talisman | x1.10 post-defense | charged R2 (100), both grips | VERIFIED |
| Curved Sword Talisman | x1.20 post-defense | guard counters (103) | VERIFIED |
| Dagger Talisman | x1.16 (1.17 in the param, truncated) | criticals | VERIFIED |
| Hammer Talisman | a blocker's stamina loss x1.4 (on ADI+0x28, before the guard boost) | every weapon hit into a guard | VERIFIED |
| Twinblade Talisman | x1.45 | the last R1 of a chain (104: r1_3 on 105 weapons, r1_4 on 178, ...) | VERIFIED |
| Blade of Mercy | x1.12 for 20 s | after landing a critical | VERIFIED |
| Rellana's Cameo | x1.45 for 10 s | stance attacks after holding the stance (stance weapons only) | VERIFIED TAE |
| Kindred of Rot's / Lord of Blood's Exultation | x1.20 / x1.12 for 20 s | rot or poison / blood loss within 7 m | VERIFIED; what fires the presence bullet INFERRED |
| Dragoncrest Greatshield Talisman | physical taken x0.95 (x0.80 from NPCs) | always | VERIFIED |
| Crucible Scale Talisman | all damage taken x0.7 | while receiving a critical | VERIFIED |
| Talisman of Lord's Bestowal | poise x1/0.65 = x1.54 | during the flask animation | VERIFIED TAE |
| Radagon's Soreseal | STR/DEX/VIG/END +5, all damage taken x1.15 | always | VERIFIED |
| Scorpion charms | element x1.08 dealt, physical taken x1.15 | always | VERIFIED |

Notes that change a PvP model:

- **Two-Handed Sword and Claw stack.** A 2H jump attack carries both 102 and 120, so it gets
  1.10 x 1.075 = 1.1825 vs players. Every family is a plain product over applicable effects.
- **Most "attack" talismans are PvP-nerfed through a second column.** The Claw family uses
  `atkPlayerDmgCorrectRate` for players and `atkEnemyDmgCorrectRate` for NPCs, with separate values.
  The `*AttackRate` talismans (Axe, Shard, Curved Sword, Dagger, Godfrey) use one value for both.
- **Blue Dancer Charm does nothing for a STR build.** It scales by absolute equipped weight and is
  gone at 30 weight (section 2c).
- **Winged Sword Insignia and Rotten Winged Sword Insignia share one counter** (both stateInfo 303).
- **Ritual Sword and Ritual Shield need HP exactly full**, and the branchswords need HP strictly
  below 20% (section 3a).

## 1. What a talisman is, and coverage (VERIFIED)

`EquipParamAccessory` has 157 rows. A talisman is a row with `refCategory 2`, whose `refId` is a
`SpEffectParam` row, plus `residentSpEffectId1..4` (Millicent's Prosthesis, Winged Sword Insignia,
Roar Medallion, Companion Jar and Talisman of All Crucibles use those). From there the script
follows every SpEffect link field: `cycleOccurrenceSpEffectId`, `replaceSpEffectId`,
`accumuOverFireId`, `accumuUnderFireId`, `spiritDeathSpEffectId`, `applyIdOnGetSoul` and
`atkOccurrenceSpEffectId`. It also adds rows the regulation names `[Talisman] <name>...` that no
field links to, because code, `AtkParam.spEffectId*` or TimeAct starts them (section 3). One row
waits on a stateInfo that only its talisman sets: Godfrey Icon's 330901.

**154 talismans covered.** Three rows are skipped:
- 6100 Entwining Umbilical Cord: its text is `[ERROR]...`, a cut item.
- 204000 and 999999999: they have no `AccessoryName`.

`accessoryGroup` is shared within each +N family and by Arsenal Charm / Great-Jar's Arsenal. The
script counts one talisman per group, as the game refuses the second (INFERRED, resources.md).

## 2. Where each field family enters the hit (VERIFIED unless marked)

### 2a. Which attacks: subcategories and the hand gate

`CheckMagicSubCategoryChangeMask` `0x140d50880 / 0x140d52630`:
- **SpEffect mask.** `GetMagicSubCategoryChangeMask` `0x140d50a30 / 0x140d527e0` starts from a
  256-bit zero constant (`0x143d67a08 / 0x143d6ba78`). It sets bit `v` for each nonzero
  `magicSubCategoryChange1/2/3` (`+0x13a`, `+0x13b`, `+0x328`).
- **The check.** A mask that is still zero passes, so 0 means no restriction. Otherwise the check
  is `(spMask & attackMask) != 0`: any of the SpEffect's values matches any of the attack's.
- **Attack mask.** `FUN_140d22eb0 / 0x140d245f0` builds it from `AtkParam.subCategory1..4`
  (`+0x4e`, `+0x4f`, `+0x1bc`, `+0x1bd`). `FUN_14068ffa0 / 0x140690df0` ORs it into
  `AttackInfo+0x14`.

`IsApplicableForCategory` `0x140500930 / 0x140501700` first applies a context gate on
`BehaviorParam.category`:

| category | rule |
|---|---|
| 1 | right-hand context: rejects `wepParamChange` 2, 3, 4 |
| 2 | left-hand context: rejects 1, 3, 4 |
| 12 | two-handed context: accepts 1, 0/5/6, and 2 only when the left weapon is held two-handed |
| 3 / 4 / 10 | spell contexts: need the row's `magParamChange` / `miracleParamChange` / `shamanParamChange` |
| 9 | 0/5/6 or 4 (kick) |
| 11 | rejects 3, 4 |

Then `throwAttackParamChange` requires the throw bit (the attack is a critical).

**Which slots carry which subcategory** is regulation data. The script resolves every named base
weapon's moveset through `er-mechanics-attacks.py` (458 weapons) and reads the AtkParam row. The
majority sets are below; `--slot-survey` re-runs this, and the selftest re-derives the table.

| slot | 1H | 2H |
|---|---|---|
| r1_1, r1_2 | none | 120 |
| last R1 of a chain | 104 (r1_3 on 105 weapons, r1_4 on 178, r1_5 on 80, r1_6 on 64) | 104 + 120 |
| r2_1, r2_2 | none | 120 |
| r2_1c, r2_2c (charged) | 100 | 100 + 120 |
| run_r1, run_r2 | 122 | 122 + 120 |
| roll_r1, bstep_r1, crouch_r1 | 121 | 121 + 120 |
| jump_r1, jump_r2 | 102 | 102 + 120 |
| counter (guard counter) | 103 | 103 + 120 |

- 120 is missing from the 2H rows of 31 of 434 weapons. Pass `weapon_id` to `multipliers()` and it
  reads that weapon's own row.
- Crouch R1 fires the rolling R1 judge (attacks.md), which is why it carries 121.

### 2b. The four multiplier families

| family | fields | accumulated in | enters the damage | talismans |
|---|---|---|---|---|
| `*AttackPowerRate` | `physicsAttackPowerRate` `+0x48`, slash/blow/thrust/neutral `+0x220..+0x22c`, element rates | `FUN_1404f4520 / 0x1404f52f0` -> `AttackInfo+0x6c..+0x8c` | multiplies AR before the defense curve (`FUN_1406832a0 / 0x1406840f0`) | Blue Dancer (by its own path, 2c) |
| `*AttackRate` | `physicsAttackRate` `+0x38`, magic `+0x3c`, fire `+0x40`, thunder `+0x44`, dark `+0x1dc`, physical sub-type `+0x210..+0x21c` | same accumulator -> `AttackInfo+0x90..+0xb0` | `FUN_140d24a30 / 0x140d261b0` stores `(short)(int)(rate * 100)` in the damage record; `CalculateDamage` `0x1404472b0 / 0x140447810` multiplies it in after `CalculateDefense` (the `unk1` term of defense.md 0) | Axe, Lance, Curved Sword, Warrior Jar, Shard of Alexander, Godfrey, Graven/Canvas (spells), Arrow's Sting, Companion Jar, Perfumer's, Roar, Dagger |
| `atk{Player,Enemy}DmgCorrectRate_*` | `+0x28c..+0x29c` / `+0x2a0..+0x2b0` | `CalculateAtkPlayerDmgCorrectRates` `0x1404f5080 / 0x1404f5e50`, `CalculateAtkEnemyDmgCorrectRates` `0x1404f4390 / 0x1404f5160` | `CalculateDamageCorrections` `0x140684d70 / 0x140685bc0`, after the defense curve; player rates when `ShouldUsePvPDamage(defender)` | Claw, Two-Handed Sword, Crossed-Trees, Twinblade, Shattered Stone, Ritual Sword, branchswords, exultations, Blade of Mercy, Rellana, Crusade, Dried Bouquet, Winged Sword stages, scorpions (element) |
| `staminaAttackRate` | `+0xc8` | `FUN_1404f8fc0 / 0x1404f9d90` | stamina damage `FUN_14068aa80 / 0x14068b8d0` = `(attackBaseStamina * staminaAtkRate * atkStamCorrection/100 [+ atkStam]) * product` | Hammer |

- **Truncation.** The `*AttackRate` product is stored as a whole percent through float32:
  `1.17f * 100 = 116.99999` becomes 116, so Dagger Talisman is x1.16 in play. 1.04, 1.08, 1.10,
  1.15 and 1.20 survive intact.
- **Filters.** The two correction-rate functions filter only on `(flags & 0x800c0003) == 0` and
  `IsApplicableForCategory`; they skip no stateInfo.
- **Not scaled.** None of the four families multiplies flat SpEffect adds such as greases.
- **Stacking.** Every family is a plain product over applicable entries.

### 2c. The stateInfo special cases on the attack side

- **197, Spear Talisman.**
  - The accumulator skips 197 (`0x1404f4848`).
  - On the defender side, `CalculateDefenseModifiers` `0x1404f53e0 / 0x1404f61b0` builds the
    counter-hit rate from the defender's stateInfo 110 entries. For physical it uses the cut rate
    of the hit's damage type.
  - If any element's rate is above 1, `ADI+0x267 |= 0x80` and `FUN_1404f5310 / 0x1404f60e0`
    multiplies in the attacker's 197 entries' `*AttackRate` for those elements.
  - The thrust restriction is data, not code: counter rows 31 and 45 raise only
    `thrustDamageCutRate` (1.3 / 1.15), and 99008 raises everything to 1.4.
  - So an ordinary thrusting counter goes from x1.15 to x1.3225. Player weapon attacks apply row
    45 through TAE event 66 (5,940 events); row 31 (x1.3, so x1.495 with the talisman) is applied
    only by the 30 events in motion category `a938` (defense.md section 2b).
- **315 / 316, Blue Dancer Charm (and one unowned DLC row 20382100 on 316).**
  - The accumulator skips both. `FUN_1406832a0` calls `FUN_1404f3c60 / 0x1404f4a30` with
    `x = min(W, 100) / 100`.
  - W is absolute equipped weight from `CalculateEquipmentWeight` `0x140247b80`: all six weapon
    slots, armor and talismans, arrows not counted.
  - `FUN_1404f3450 / 0x1404f4220` gives `1 + (rate - 1) * CalcCorrectGraph[50](x)` (316 uses graph
    51, which rises with weight). Graph 50 is (0, 1) (0.08, 0.9) (0.16, 0.6) (0.2, 0.25) (0.3, 0),
    linear.
  - So the charm is x1.15 at 0 weight, x1.135 at 8, x1.09 at 16, x1.0375 at 20, and x1 at 30 or
    more. It applies to all five elements of the weapon part, in either hand.
- **367 + `throwAttackParamChange`, Dagger Talisman.**
  - The throw bit (`AttackInfo+0x109`) is the gate. `FUN_1406832a0` uses the same flag to apply
    the weapon's critical `throwAtkRate`.
  - No code compares stateInfo with 367 (byte search), so 367 is a label (INFERRED).
  - What sets `+0x109` was not found.

## 3. Conditions and triggers (VERIFIED unless marked)

### 3a. HP conditions

The HP check is `FUN_1405012a0 / 0x140502070`, reached from `SpecialEffect::TriggerHpRateEffects`
`0x1404f6dd0 / 0x1404f7ba0` on the chr update (`FUN_1403ffd10 / 0x140400000`).
- `hpRate = (float)hp / (float)hpMax`.
- `conditionHp` passes when `hpRate <= conditionHp * 0.01f`. `20 * 0.01f` is 0.19999999f, so
  exactly 20% does not count.
- `conditionHpRate` passes when `hpRate >= conditionHpRate * 0.01f`. 100 gives exactly 1.0f, so it
  needs full HP.

The check is re-run continuously. A failing entry sits in state 1 and every damage product skips
it. No consumer of stateInfo 48/49 (the branchswords' labels) was found.

### 3b. Presence gates and event chains

- **`invocationConditionsStateChange1..3`** (`FUN_1405013b0 / 0x140502180`): the entry is enabled
  only while its owner has an enabled SpEffect whose stateInfo equals any of the three. It is
  re-checked continuously.
- **Per-frame update** (`FUN_1404fae40 / 0x1404fbc10`): an expired entry applies its
  `replaceSpEffectId`. A firing interval applies `cycleOccurrenceSpEffectId`; `ActivateInterval`
  `0x1405011c0 / 0x140501f90` re-arms every `motionInterval` seconds.
- **Criticals.** All 265 critical `AtkParam_Pc` rows (xx005xx) carry `spEffectId0..2` = 350501,
  350601 and 20382301, applied to the attacker:
  - Assassin's Crimson Dagger: 350501 (gated on 288) becomes 350502 after 1 s, healing 10% of
    max HP + 85 once.
  - Assassin's Cerulean Dagger: 350602 restores 15 FP.
  - Blade of Mercy: 20382301 (gated on 483) cycles 20382302, x1.12 players / x1.2 NPCs for 20 s
    (about 21 s effective).
- **Rune award** (`ApplyRuneGetEffects` `0x1404fa700 / 0x1404fb4d0`, stateInfo 199):
  - Taker's Cameo 350301 heals 3% + 30 once.
  - Ancestral Spirit's Horn 361101 restores 3 FP.
  - Crusade Insignia 20380501 gives x1.1 players / x1.15 NPCs for 20 s.
- **Spirit death** (`ChrIns::ApplySpiritDeathEffect` `0x1403fc450 / 0x1403fc680`, stateInfo
  475): Dried Bouquet gives 20381001, x1.2 for 30 s.
- **Exultations.** The talisman row is gated on a presence stateInfo and cycles its buff every
  tick while that presence lasts.

  | talisman | presence stateInfo (row) | presence bullet | buff | duration | vs NPCs | vs players |
  |---|---|---|---|---|---|---|
  | Lord of Blood's | 379 (501) | 1000 | 321601 | 20 s | x1.2 | x1.12 |
  | Kindred of Rot's | 380 (506) | 1005 | 321701 | 20 s | x1.2 | x1.2 |
  | Aged One's | 495 (503) | 1020 | 20380601 | 30 s | x1.2 | x1.12 |
  | St. Trina's Smile | 480 (508) | 1010 | 20381601 | 30 s | x1.2 | x1.12 |

  - The presence bullets have a 7 m radius and a 0.5 s life. They come from BehaviorParam
    2100/2105/2150/2160. That those fire on a status proc is INFERRED.
  - Which status row 508 is (sleep) is COMMUNITY.
- **Rellana's Cameo** (TAE a614/a615/a819/a878/a879):
  - The stance applies 20382205 (stateInfo 504). Frames 24-28 of 40050 apply 20382201 -> 20382202,
    which pulses 20382203: x1.45 for 0.2 s at a time.
  - Stance attacks 40060-40075 apply 20382206 (502), which turns the pulse into 20382204: x1.45 for
    10 s.
- **Talisman of Lord's Bestowal.** The a00 flask animations add 20382003 (gated on 496):
  `toughnessDamageCutRate 0.65`, poise x1.54 while drinking. The mounted flask animations
  (150110/111) also add 20382004, `saReceiveDamageRate 0`. What applies the Torrent rows
  20382015/20382016 was not found.
- **Godfrey Icon.**
  - The resident row is x1.15 `*AttackRate` on subcategories 110/111 (charged spells and skills).
  - TAE event 302 in a525 anims 45010/45110 applies 330901: 0.8 s, x1.15, subcategory 38. No
    `AtkParam_Pc` row carries 38, so what 330901 covers is unresolved.
- **Accumulators** (Winged Sword, Rotten Winged, Millicent's, Godskin Swaddling Cloth):
  - There is one counter per accumulator id (stateInfo - 303) for as long as a host row is present.
    `AtkParam.spEffectId` rows 6900-6909 (stateInfo 314) add `accumuVal` on each hit: +6 on 705
    rows (dagger 1H R1), +8 on 3546 (most attacks), +10 on 1797, +14 on 1254 (heavy and charged).
  - Hosts decay the counter: -1 every 0.5 s for Winged Sword and Millicent's, -1 every 0.8 s for
    Godskin.
  - Every tick, each host row whose `accumuOverVal` (17 / 30 / 45 / 60) is reached re-applies its
    boost row. Stages 1-3 linger 1.5 s; stage 4 has none.
  - At +8 per hit, the stages take 3 / 4 / 6 / 8 hits (+10: 2 / 3 / 5 / 6; +14: 2 / 3 / 4 / 5).
  - Godskin at 32 heals 3% + 30 once, and its `accumuVal -999` resets the counter.
  - Whether stage rows 1-3 (spCategory 120) and stage 4 (spCategory 20) are active together past
    60 is not established. The script applies only the stage passed as `successive_stage`.
  - **What one landed hit adds (VERIFIED, 2026-10-02).** Every hit record that lands adds its
    rows' `accumuVal` once, so each hitbox of a multi-hit swing counts. Backhand Blade's 2H R1 #1
    is two hit records (AtkParam 6400200 on index 0, 6400203 on index 1), both carrying
    `spEffectId0` 6902 (+6): +12 when both land. The chain, `1.16.2 / 1.17.1`:
    - `CalculateDamage2` `0x1404483b0 / 0x140448910` runs per landed hit and walks the 13
      SpEffect slots of the AttackDamageInfo (`mov ebx,0xd` `0x1404489eb / 0x140448f4b`). Slots
      0-4 are `AtkParam.spEffectId0..4` (`FUN_140d24440` reads `paramRow + 0x18 + 4i`,
      `0x140d24506 / 0x140d25c86`; all -1 when `disableHitSpEffect`).
    - A slot whose row has `effectTargetAttacker` (`+0x160` bit 1, `0x140448aed / 0x14044904d`)
      is applied to the attacker (`FUN_1403e8b70` -> `Apply` `0x1404fa8e0` -> `FUN_1404fd090`).
    - The 314 rows are zero-duration, so `FUN_1404ff010` sets `controlFlags & 2` on them, and
      `FUN_140500510 / 0x1405012e0` refuses to reuse such an entry: a second hit, even in the
      same frame, makes a new entry rather than refreshing the first.
    - Each frame `FUN_1404fae40` ticks every entry and then calls `FUN_1404fe4e0`, which adds
      `FUN_1404f70e0 / 0x1404f7eb0` (the sum of `accumuVal` `+0x184` over entries with stateInfo
      314 `+0x156` activated this update) to every accumulator whose host is present. An
      unconditional new entry starts in state 2, is activated on its first tick and removed on
      the next (`FUN_140500be0`): one count per entry.
    - The host's own `accumuVal` (-1) is added on every `ActivateInterval` (`0x1405011c0`); the
      counter is clamped at 0 (`FUN_1404fe170`) and has no upper clamp.
    - Each accumulator has one fire slot (`FUN_1404fe450`), written by every host whose threshold
      the counter meets; which stage row ends up applied when several are met depends on the host
      order in the entry list, which was not traced.
  - `scripts/er-mechanics-talisman-affinity.py` turns this into per-weapon numbers: counter gain
    per slot and per R1 string, gain per second, and the cold-start time to the first threshold
    (section 5).

### 3c. Defender-side gates

- **158 / 204, guarded hits only.** In `CalculateDefenseModifiers` these cut rates count only when
  `AttackDamageInfo+0x258` (guarded) is set. This covers Pearl Shield Talisman's left-hand 0.8
  non-physical cut and Greatshield Talisman's `guardStaminaMult`.
- **Guard stamina.** `CalculateGuardStaminaDepletion` `0x140651f50 / 0x140652da0` ->
  `FUN_140684540 / 0x140685390`:

  ```
  GB   = clamp((staminaGuardDef * staminaGuardDefRate + strTerm + 1) * (1 + AtkParam.guardStaminaCutRate/100)
               * product(guardStaminaCutRate), 0, 100)
  loss = ((1 - GB/100) * incoming * product(guardStaminaMult) + BehaviorParam.stamina)
         * (two-handing ? (shield 0.7 : 0.9) : 1)
  ```

  - In PvP the loss is further multiplied by `FinalDamageRateParam.staminaRate`.
  - Greatshield Talisman cuts the incoming part by 20%, not the fixed guard-reaction cost.
- **335, Crucible Scale / All Crucibles.** Excluded from the normal cut product. It is applied in
  `CalculateDamage` only when the received hit is a throw (`ADI+0xd9 == 2`) with
  `AtkParam.throwDamageAttribute == 1`, which covers the 97 "(Crit)" damage rows. It is x0.7 when
  you receive a critical.
- **290 / 473, Crucible Feather / Fine Crucible Feather / All Crucibles.**
  - The cut-rate penalty (x1.3 / x1.15 / x1.45) is always on.
  - The i-frames are TimeAct events gated on the stateInfo, from the pre-1.17 extraction:
    - 290 adds 3 frames on rolls 27100-27127 (0-13 becomes 0-16). Rolls 27140-27143 get no
      extension.
    - 473 gives backsteps 27000/27010/27020 i-frames on frames 0-7 and partial immunity on 7-11.
- **450, Crucible Knot.** On a head hit (part 0x1f, unguarded), `ADI+0x244` stays 1.0 instead of
  the headshot rate. That removes the headshot damage bonus and the poise bonus (`partsRate`).
  The headshot rate itself was not read.
- **Verdigris Discus.**
  - Rows 19986 (Heavy Weight) and 19987 (Overweight), both 0.1 s pulses: damage taken x0.925 / x0.85
    from players and x0.9 / x0.8 from NPCs.
  - Nothing in either executable image carries 19985/19986/19987 as an immediate, and no param
    links them. The trigger is probably the behavior script, which is not in the local extraction.
  - The script's bands, load ratio above 0.7 and above 1.0, are INFERRED from the row names and
    the load tiers.
- **Sharpshot Talisman.** 19991 is x1.08 players / x1.12 NPCs on ammunition subcategories, in
  0.1 s pulses. Its trigger was not found either. The GAME TEXT says "precision-aimed shots".

## 4. Resources, spells and utility (VERIFIED unless marked)

HP, FP, stamina, equip load and attribute rates are covered in `resources.md`.

| field (talisman) | code | semantics |
|---|---|---|
| `staminaRecoverChangeSpeed` (Green Turtle 8, Two-Headed Turtle 10) | `PlayerIns::GetStaminaRecoverySpeed` `0x1406566b0 / 0x140657500`; tick `FUN_1404016d0 / 0x140401a30` | `45.0 + sum`. The 45.0 is at `.rdata 0x143b33c08 / 0x143b37c18`. The sum is then scaled by the animation's SP regen percent (TAE 255), so it shrinks while guarding like the base does. That the rate is per second is INFERRED. |
| `toughnessDamageCutRate` (Bull-Goat 0.75, Lord's Bestowal 0.65) | defense.md 6 | poise divided by the product |
| `changeHpPoint` / `motionInterval` (Blessed Dew -2 / 1 s) | `FUN_1404fb920 / 0x1404fc6f0` | negative values heal: +2 HP per second |
| `changeMpPoint` (Blessed Blue Dew -1 / 2 s) | not traced | +1 FP every 2 s (INFERRED, same shape) |
| `changeHp/MpEstusFlaskCorrectRate` (Seeds 1.2, +1 1.3) | `FUN_1404fb3a0 / 0x1404fc170` | flask heal x rate. The FP side is INFERRED. |
| `extendLifeRate` 1.3, stateInfo 193 (Old Lord's) | `FUN_1404fae40`; drain in `FUN_140501020 / 0x140501df0` | `f = 1 + sum(rate - 1)`. Only rows with `isExtendSpEffectLife` (51: 38 incantation buffs, 11 sorceries, Commander's Standard and one unnamed) drain at `dt / f`. |
| `dexterityCancelSystemOnlyAddDexterity` (Radagon Icon 30, Beloved Stardust 99) | `CSPlayerMagicModule::GetCastingSpeed` `0x140453cc0 / 0x140454220` | cast speed from `clamp(DEX + bonus, analogDexterityMin, Max)`. 220 spells use 10/70 and 96 use 1/20. |
| `artsConsumptionRate` 0.75 (Carian Filigreed Crest) | `CalculateFpConsumption` `0x14068b220 / 0x14068c070` | skill FP = `ceil(rate * useMagicPoint)` |
| `magic/miracle/shamanConsumptionRate` 0.75 (Primal Glintstone Blade) | `GetMpCost` `0x140684940 / 0x140685790` | spell FP x0.75, picked by `MagicParam.ezStateBehaviorType` |
| `changeMagicSlot` 2 (Moon of Nokstella) | `GetMagicSlotsCount` `0x1406865d0 / 0x140687420` | +2 memory slots |
| `fallDamageRate` 0 (Longtail Cat) | `FUN_140686d30 / 0x140687b80` | fall damage x0. The lethal-height check was not traced. |
| `targetPriority` 0.1 (Shabriri's Woe) | `FUN_140434ed0 / 0x140435420` | added to AI target scoring. How much it changes targeting is INFERRED. |
| `hearingSearchEnemyRate` 0 (Crepus's Vial) | `FUN_1404f91b0 / 0x1404f9f80` | product, clamped 0..99. That this silences the wearer is INFERRED. |
| `soulRate` 1.2 (Gold Scarab) | `CalculateRuneRateBuffs` `0x1404f8e50 / 0x1404f9c20` | runes x1.2 |
| `itemDropRate` 0.75 (Silver Scarab, stateInfo 66) | `GetItemDropRateModifier` `0x140686500 / 0x140687350` | additive: `CC(ARC, graph 140) + 0.75`, where graph 140 is 1.0 at ARC 0 and 1.99 at 99. The x100 display is INFERRED. |
| `bowDistRate` 65 / 50 (Arrow's Reach / Soaring Sting) | `FUN_140d33bf0 / 0x140d35370` | range = `dist * ((weapon bowDistRate + 100)/100 + clamp(sum, -100, 999)/100)` |
| `change*ResistPoint` (horn charms, Mottled Necklace, Ailment Talisman) | defense.md 5 | added to resistance |
| `effectTarget*` bits | consumer not found | Blessed Blue Dew has all of them at 0 and is said to work (COMMUNITY), so they do not decide whether a talisman works on a phantom (INFERRED) |

The Ailment Talisman runs a monitor chain. When a status is applied to the wearer, it grants +350
resistance to that pair for 120 s (poison/rot), 45 s (bleed/frost) or 30 s (sleep/madness). It
also sets `-100` build-up of that status on the wearer's own row (`effectTargetEnemy`); what that
row does was not traced. If it reaches the wearer's own gauge, a negative amount refills it by
100: `FUN_14043e630` returns a non-positive amount unchanged and `FUN_14043d8a0` clamps the gauge
to the resistance, the same path the boluses' -99999 rows take (status.md section 9).

Status in the PvP ranking (status.md section 9, `MEASURED` over the 1074 RL 140-160 PvP builds):
the resistance talismans are rare (Stalwart Horn Charm +2 7 builds, Clarifying +2 4, Immunizing
+2 2, Mottled Necklace +2 1, Ailment Talisman 1, every other horn charm 0) and already inside the
planner's resistances, so the status model reads them through each build's resistance.
`er-mechanics-status.talisman_resist()` sums the always-on `change*ResistPoint` of a list for
what-ifs. No attacker talisman raises build-up or proc HP; the exultations (Lord of Blood's 76
builds, Kindred of Rot's 42) scale damage while a proc keeps them on, and
`scripts/er-builds-pvp.py --talismans` applies them by uptime (`exultation_factor`).

Sacrificial Twig (159), Furled Finger's and Host's Trick-Mirror, and Concealing Veil (466, crouch
state) change nothing a build model reads.

## 5. Using the script

```bash
python3 scripts/er-mechanics-talismans.py                           # one line per talisman
python3 scripts/er-mechanics-talismans.py --affects "damage vs players"
python3 scripts/er-mechanics-talismans.py --show "Rotten Winged Sword Insignia"
python3 scripts/er-mechanics-talismans.py --talismans "Two-Handed Sword Talisman,Claw Talisman" --slot jump_r2 --grip 2h
python3 scripts/er-mechanics-talismans.py --talismans "Crucible Scale Talisman" --incoming --ctx '{"being_critted": true}'
python3 scripts/er-mechanics-talismans.py --corpus                  # section 7
python3 scripts/er-mechanics-talismans.py --markdown                # section 6
python3 scripts/er-mechanics-talismans.py --selftest
```

The weapon side of each condition (the mechanics half of `scripts/er-builds-embed.py pairs`) is
`scripts/er-mechanics-talisman-affinity.py`: per base weapon and talisman, the share of the
moveset and default skill a subcategory gate matches, successive-counter gain and rate, pierce
share for Spear Talisman, the statuses the weapon builds for the exultations, whether the
weapon's TimeAct applies a presence gate (Rellana's Cameo), and element share for the scorpion
charms. `--weapon "<name>"` prints one weapon, `--json --out <file>` writes the matrix,
`--selftest` byte-checks the counter chain in both images.

- The module docstring documents `multipliers(t, talismans, ctx)` and
  `defender_modifiers(t, talismans, incoming)`.
- **Grips.** Every weapon feature is measured one-handed, two-handed and powerstanced (two
  copies, `dual`). Both hands' hits of a powerstance swing each add their own successive-counter
  entry (+8 and +8 on a Cross-Naginata L1). A weapon with `isDualBlade` and `bothHandEquipable`
  (fists, claws, hand-to-hand arts, perfume bottles, backhand blades and a few others) is
  powerstanced by itself, so its two-handed grip is its powerstance; Grafted Dragon needs two.
- **Chain final hits (Twinblade Talisman, 104).** `scripts/er-mechanics-chain-attacks.py` lists,
  per weapon and grip, the string slot carrying 104, the time from the first press to that hit
  at the earliest inputs, the string's loop period, and the chain hits' damage. The 104 rows are
  only string finishers: one- and two-handed R1 (judges 20-50, 220-250), powerstance L1 (8xx),
  off-hand L1 (420-450) and mounted R1/L1 #3 (610/710). No R2, no skill animation (04xxxx) and
  no ash of war carries one (`--skills`). Default skills whose TimeAct file also holds the
  weapon's own moveset (Golden Tempering, Starcaller Cry, Weed Cutter) contribute nothing: those
  03xxxx clips are the ordinary moves.
- **Gear ranking.** `er-mechanics-gear-synergy.py` scores Twinblade Talisman by chain damage per
  second in the weapon's best grip and the three successive-hit talismans by counter per second
  net of decay in its fastest grip, both including powerstance; `gen-r3-weapon-boards.py` writes
  those rows into `crates/er-r3-view/src/weapon_boards.rs`.
- **Timed buffs are off unless named.** Blade of Mercy, the exultations, Rellana, Crusade, Dried
  Bouquet and Lord's Bestowal apply only when named in `ctx['active']`. Successive-hit stages apply
  only through `successive_stage`. This is so a model never assumes a buff the fight did not earn.
- **Selftest.** It checks behaviour against independently grounded numbers:
  - the float32 HP thresholds;
  - Dagger's truncation to 1.16;
  - Blue Dancer at the five points of CalcCorrectGraph 50;
  - the counter, guard, critical and headshot gates;
  - the slot -> subcategory table, re-derived from all 458 weapons.

## 6. Every talisman

Generated by `python3 scripts/er-mechanics-talismans.py --markdown`.
- The effect column is VERIFIED regulation data.
- "trigger evidence" labels when the row is active.
- "damage vs players / enemies" is `atkPlayer/atkEnemyDmgCorrectRate`, "damage taken from ..." is
  `defPlayer/defEnemyDmgCorrectRate`, and "(*AttackRate)" is the post-defense rate of 2b.
- Rows with no field and no role (internal pulse rows) are left out.

| id | talisman | effect (`VERIFIED` regulation) | condition | system | trigger evidence |
|---|---|---|---|---|---|
| 1000 | Crimson Amber Medallion | 310000: HP: max HP x1.06 | always (equipped) | HP | always on: VERIFIED |
| 1001 | Crimson Amber Medallion +1 | 310010: HP: max HP x1.07 | always (equipped) | HP | always on: VERIFIED |
| 1002 | Crimson Amber Medallion +2 | 310020: HP: max HP x1.08 | always (equipped) | HP | always on: VERIFIED |
| 1010 | Cerulean Amber Medallion | 310100: FP: max FP x1.07 | always (equipped) | FP | always on: VERIFIED |
| 1011 | Cerulean Amber Medallion +1 | 310110: FP: max FP x1.09 | always (equipped) | FP | always on: VERIFIED |
| 1012 | Cerulean Amber Medallion +2 | 310120: FP: max FP x1.11 | always (equipped) | FP | always on: VERIFIED |
| 1020 | Viridian Amber Medallion | 310200: stamina: max stamina x1.11 | always (equipped) | stamina | always on: VERIFIED |
| 1021 | Viridian Amber Medallion +1 | 310210: stamina: max stamina x1.13 | always (equipped) | stamina | always on: VERIFIED |
| 1022 | Viridian Amber Medallion +2 | 310220: stamina: max stamina x1.15 | always (equipped) | stamina | always on: VERIFIED |
| 1030 | Arsenal Charm | 310300: equip load: max equip load x1.15 | always (equipped) | equip load | always on: VERIFIED |
| 1031 | Arsenal Charm +1 | 310310: equip load: max equip load x1.17 | always (equipped) | equip load | always on: VERIFIED |
| 1032 | Great-Jar's Arsenal | 310320: equip load: max equip load x1.19 | always (equipped) | equip load | always on: VERIFIED |
| 1040 | Erdtree's Favor | 310400: HP: max HP x1.03; stamina: max stamina x1.07; equip load: max equip load x1.05 | always (equipped) | HP, equip load, stamina | always on: VERIFIED |
| 1041 | Erdtree's Favor +1 | 310410: HP: max HP x1.035; stamina: max stamina x1.085; equip load: max equip load x1.065 | always (equipped) | HP, equip load, stamina | always on: VERIFIED |
| 1042 | Erdtree's Favor +2 | 310420: HP: max HP x1.04; stamina: max stamina x1.1; equip load: max equip load x1.08 | always (equipped) | HP, equip load, stamina | always on: VERIFIED |
| 1050 | Radagon's Scarseal | 310500: absorption: all damage taken x1.1 (*DamageCutRate); attributes: VIG +3, END +3, STR +3, DEX +3 | always (equipped) | absorption, attributes | always on: VERIFIED |
| 1051 | Radagon's Soreseal | 310510: absorption: all damage taken x1.15 (*DamageCutRate); attributes: VIG +5, END +5, STR +5, DEX +5 | always (equipped) | absorption, attributes | always on: VERIFIED |
| 1060 | Starscourge Heirloom | 310600: attributes: STR +5 | always (equipped) | attributes | always on: VERIFIED |
| 1070 | Prosthesis-Wearer Heirloom | 310700: attributes: DEX +5 | always (equipped) | attributes | always on: VERIFIED |
| 1080 | Stargazer Heirloom | 310800: attributes: INT +5 | always (equipped) | attributes | always on: VERIFIED |
| 1090 | Two Fingers Heirloom | 310900: attributes: FTH +5 | always (equipped) | attributes | always on: VERIFIED |
| 1100 | Silver Scarab | 311000: misc: item discovery field 0.75 | item discovery: adds itemDropRate to the ARC curve | misc | VERIFIED |
| 1110 | Gold Scarab | 311100: misc: runes x1.2 | label only (rune gain not gated on it) | misc | VERIFIED |
| 1140 | Moon of Nokstella | 311400: spells: memory slots +2 | always (equipped) | spells | always on: VERIFIED |
| 1150 | Green Turtle Talisman | 311500: stamina: stamina recovery +8/s | label only (regen is not gated on it) | stamina | VERIFIED |
| 1160 | Stalwart Horn Charm | 311600: resistance: bleed +90, frost +90 | always (equipped) | resistance | always on: VERIFIED |
| 1161 | Stalwart Horn Charm +1 | 311610: resistance: bleed +140, frost +140 | always (equipped) | resistance | always on: VERIFIED |
| 1170 | Immunizing Horn Charm | 311700: resistance: poison +90, rot +90 | always (equipped) | resistance | always on: VERIFIED |
| 1171 | Immunizing Horn Charm +1 | 311710: resistance: poison +140, rot +140 | always (equipped) | resistance | always on: VERIFIED |
| 1180 | Clarifying Horn Charm | 311800: resistance: sleep +140, madness +140 | always (equipped) | resistance | always on: VERIFIED |
| 1181 | Clarifying Horn Charm +1 | 311810: resistance: sleep +190, madness +190 | always (equipped) | resistance | always on: VERIFIED |
| 1190 | Prince of Death's Pustule | 311900: resistance: death +90 | always (equipped) | resistance | always on: VERIFIED |
| 1191 | Prince of Death's Cyst | 311910: resistance: death +140 | always (equipped) | resistance | always on: VERIFIED |
| 1200 | Mottled Necklace | 312000: resistance: poison +40, rot +40, bleed +40, frost +40, sleep +40, madness +40 | always (equipped) | resistance | always on: VERIFIED |
| 1201 | Mottled Necklace +1 | 312010: resistance: poison +60, rot +60, bleed +60, frost +60, sleep +60, madness +60 | always (equipped) | resistance | always on: VERIFIED |
| 1210 | Bull-Goat's Talisman | 312100: poise: poise x1.333 (damage to poise x0.75) | always (equipped) | poise | always on: VERIFIED |
| 1220 | Marika's Scarseal | 312200: absorption: all damage taken x1.1 (*DamageCutRate); attributes: MND +3, INT +3, FTH +3, ARC +3 | always (equipped) | absorption, attributes | always on: VERIFIED |
| 1221 | Marika's Soreseal | 312210: absorption: all damage taken x1.15 (*DamageCutRate); attributes: MND +5, INT +5, FTH +5, ARC +5 | always (equipped) | absorption, attributes | always on: VERIFIED |
| 1230 | Warrior Jar Shard | 312300: attack power: AR x1.1 (*AttackRate) | attack subcategory in {112 skill, 111 charged skill} | attack power | subcategory match VERIFIED |
| 1231 | Shard of Alexander | 312310: attack power: AR x1.15 (*AttackRate) | attack subcategory in {112 skill, 111 charged skill} | attack power | subcategory match VERIFIED |
| 1250 | Millicent's Prosthesis | 312500: attributes: DEX +5 | always (equipped) | attributes | always on: VERIFIED |
|  |  | 312505: damage vs players: all elements x1.04; damage vs enemies: all elements x1.04 | successive-hit stage 1 (threshold 17); accumulator 3 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 312506: damage vs players: all elements x1.06; damage vs enemies: all elements x1.06 | successive-hit stage 2 (threshold 30); accumulator 3 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 312507: damage vs players: all elements x1.11; damage vs enemies: all elements x1.11 | successive-hit stage 3 (threshold 45); accumulator 3 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 312508: damage vs players: all elements x1.11; damage vs enemies: all elements x1.11 | successive-hit stage 4 (threshold 60); accumulator 3 (successive hits) | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
| 2000 | Magic Scorpion Charm | 320000: damage vs players: magic x1.08; damage vs enemies: magic x1.12; damage taken from players: physical x1.15; damage taken from enemies: physical x1.1 | always (equipped) | damage taken from enemies, damage taken from players, damage vs enemies, damage vs players | always on: VERIFIED |
| 2010 | Lightning Scorpion Charm | 320100: damage vs players: lightning x1.08; damage vs enemies: lightning x1.12; damage taken from players: physical x1.15; damage taken from enemies: physical x1.1 | always (equipped) | damage taken from enemies, damage taken from players, damage vs enemies, damage vs players | always on: VERIFIED |
| 2020 | Fire Scorpion Charm | 320200: damage vs players: fire x1.08; damage vs enemies: fire x1.12; damage taken from players: physical x1.15; damage taken from enemies: physical x1.1 | always (equipped) | damage taken from enemies, damage taken from players, damage vs enemies, damage vs players | always on: VERIFIED |
| 2030 | Sacred Scorpion Charm | 320300: damage vs players: holy x1.08; damage vs enemies: holy x1.12; damage taken from players: physical x1.15; damage taken from enemies: physical x1.1 | always (equipped) | damage taken from enemies, damage taken from players, damage vs enemies, damage vs players | always on: VERIFIED |
| 2040 | Red-Feathered Branchsword | 320400: damage vs players: all elements x1.2; damage vs enemies: all elements x1.2 | HP <= 20% of max; label only; the HP field is the gate | damage vs enemies, damage vs players | HP gate VERIFIED; no stateInfo 48 consumer found |
| 2050 | Ritual Sword Talisman | 320500: damage vs players: all elements x1.1; damage vs enemies: all elements x1.1 | HP >= 100% of max | damage vs enemies, damage vs players | HP gate VERIFIED |
| 2060 | Spear Talisman | 320600: attack power: AR physical x1.15 (*AttackRate) | counter-hits: multiplies the counter-hit rate | attack power | VERIFIED |
| 2070 | Hammer Talisman | 320700: stamina damage: stamina damage to guarding target x1.4 | always (equipped) | stamina damage | always on: VERIFIED |
| 2080 | Winged Sword Insignia | 320800: (no field) | accumulator 1 (successive hits) | - | VERIFIED |
|  |  | 320804: damage vs players: all elements x1.03; damage vs enemies: all elements x1.03 | successive-hit stage 1 (threshold 17); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320805: damage vs players: all elements x1.05; damage vs enemies: all elements x1.05 | successive-hit stage 2 (threshold 30); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320806: damage vs players: all elements x1.1; damage vs enemies: all elements x1.1 | successive-hit stage 3 (threshold 45); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320807: damage vs players: all elements x1.1; damage vs enemies: all elements x1.1 | successive-hit stage 4 (threshold 60); accumulator 1 (successive hits) | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
| 2081 | Rotten Winged Sword Insignia | 320810: (no field) | accumulator 1 (successive hits) | - | VERIFIED |
|  |  | 320814: damage vs players: all elements x1.06; damage vs enemies: all elements x1.06 | successive-hit stage 1 (threshold 17); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320815: damage vs players: all elements x1.08; damage vs enemies: all elements x1.08 | successive-hit stage 2 (threshold 30); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320816: damage vs players: all elements x1.13; damage vs enemies: all elements x1.13 | successive-hit stage 3 (threshold 45); accumulator 1 (successive hits); lasts 1.5s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
|  |  | 320817: damage vs players: all elements x1.13; damage vs enemies: all elements x1.13 | successive-hit stage 4 (threshold 60); accumulator 1 (successive hits) | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED; VERIFIED |
| 2090 | Dagger Talisman | 320900: attack power: AR x1.17 (*AttackRate) | label; the throw gate decides (criticals only); throw (critical) attacks only | attack power | VERIFIED throw gate, no 367 compare found; throw gate VERIFIED |
| 2100 | Arrow's Reach Talisman | 321000: ranged: bow range +65% | bow range: added to the weapon bowDistRate | ranged | VERIFIED |
| 2110 | Blue Dancer Charm | 321100: attack power: physical AR x1.15 (*AttackPowerRate) | scaled by equipped weight, graph 50 | attack power | VERIFIED |
| 2120 | Twinblade Talisman | 321200: damage vs players: all elements x1.45; damage vs enemies: all elements x1.45 | attack subcategory in {104 final chain attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 2130 | Axe Talisman | 321300: attack power: AR x1.1 (*AttackRate) | attack subcategory in {100 charged heavy attack} | attack power | subcategory match VERIFIED |
| 2140 | Lance Talisman | 321400: attack power: AR x1.15 (*AttackRate) | attack subcategory in {101 horseback attack} | attack power | subcategory match VERIFIED |
| 2150 | Arrow's Sting Talisman | 321500: attack power: AR x1.1 (*AttackRate) | attack subcategory in {105 ammunition attack, 113 ranged skill, 118 ammunition on-hit} | attack power | subcategory match VERIFIED |
| 2160 | Lord of Blood's Exultation | 321600: (no field) | owner has an effect with stateInfo 379 | - | presence gate VERIFIED |
|  |  | 321601: damage vs players: all elements x1.12; damage vs enemies: all elements x1.2 | timed buff (ctx active 'lord_of_blood'); lasts 20s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED |
| 2170 | Kindred of Rot's Exultation | 321700: (no field) | owner has an effect with stateInfo 380 | - | presence gate VERIFIED |
|  |  | 321701: damage vs players: all elements x1.2; damage vs enemies: all elements x1.2 | timed buff (ctx active 'kindred_of_rot'); lasts 20s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED |
| 2180 | Claw Talisman | 321800: damage vs players: all elements x1.075; damage vs enemies: all elements x1.15 | attack subcategory in {102 jump attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 2190 | Roar Medallion | 321900: attack power: AR x1.15 (*AttackRate) | attack subcategory in {106 roar attack, 116 Shriek of Milos} | attack power | subcategory match VERIFIED |
|  |  | 321901: damage vs players: all elements x1.05; damage vs enemies: all elements x1.1 | attack subcategory in {107 breath attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 2200 | Curved Sword Talisman | 322000: attack power: AR x1.2 (*AttackRate) | attack subcategory in {103 guard counter} | attack power | subcategory match VERIFIED |
| 2210 | Companion Jar | 322100: attack power: AR x1.2 (*AttackRate) | attack subcategory in {108 thrown pot} | attack power | subcategory match VERIFIED |
|  |  | 20383000: attack power: AR x1.1 (*AttackRate) | attack subcategory in {131 hefty thrown pot} | attack power | subcategory match VERIFIED |
| 2220 | Perfumer's Talisman | 322200: attack power: AR x1.2 (*AttackRate) | attack subcategory in {109 perfume} | attack power | subcategory match VERIFIED |
| 3000 | Graven-School Talisman | 330000: attack power: AR x1.04 (*AttackRate) | spells only (wepParamChange 3 fails weapon contexts); spells / self | attack power | hand gate VERIFIED |
| 3001 | Graven-Mass Talisman | 330010: attack power: AR x1.08 (*AttackRate) | spells only (wepParamChange 3 fails weapon contexts); spells / self | attack power | hand gate VERIFIED |
| 3040 | Faithful's Canvas Talisman | 330400: attack power: AR x1.04 (*AttackRate) | spells only (wepParamChange 3 fails weapon contexts); spells / self | attack power | hand gate VERIFIED |
| 3050 | Flock's Canvas Talisman | 330500: attack power: AR x1.08 (*AttackRate) | spells only (wepParamChange 3 fails weapon contexts); spells / self | attack power | hand gate VERIFIED |
| 3060 | Old Lord's Talisman | 330600: spells: buff duration x1.3 | extends buffs that carry isExtendSpEffectLife (51 rows) | spells | VERIFIED |
| 3070 | Radagon Icon | 330700: spells: casting speed as +30 DEX | always (equipped) | spells | always on: VERIFIED |
| 3080 | Primal Glintstone Blade | 330800: HP: max HP x0.85; FP: sorcery FP cost x0.75; FP: incantation FP cost x0.75 | always (equipped) | FP, HP | always on: VERIFIED |
| 3090 | Godfrey Icon | 330900: attack power: AR x1.15 (*AttackRate) | attack subcategory in {110 charged spell, 111 charged skill}; a525 anims 45010/45110 apply 330901 | attack power | subcategory match VERIFIED; VERIFIED TAE |
|  |  | 330901: attack power: AR x1.15 (*AttackRate) | timed buff (ctx active 'godfrey_window'); attack subcategory in {38 ?}; owner has an effect with stateInfo 497; lasts 0.8s | attack power | subcategory match VERIFIED; presence gate VERIFIED |
| 4000 | Dragoncrest Shield Talisman | 340000: damage taken from players: physical x0.98; damage taken from enemies: physical x0.9 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4001 | Dragoncrest Shield Talisman +1 | 340010: damage taken from players: physical x0.97; damage taken from enemies: physical x0.87 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4002 | Dragoncrest Shield Talisman +2 | 340020: damage taken from players: physical x0.96; damage taken from enemies: physical x0.83 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4003 | Dragoncrest Greatshield Talisman | 340030: damage taken from players: physical x0.95; damage taken from enemies: physical x0.8 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4010 | Spelldrake Talisman | 340100: damage taken from players: magic x0.96; damage taken from enemies: magic x0.87 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4011 | Spelldrake Talisman +1 | 340110: damage taken from players: magic x0.95; damage taken from enemies: magic x0.83 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4012 | Spelldrake Talisman +2 | 340120: damage taken from players: magic x0.94; damage taken from enemies: magic x0.8 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4020 | Flamedrake Talisman | 340200: damage taken from players: fire x0.96; damage taken from enemies: fire x0.87 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4021 | Flamedrake Talisman +1 | 340210: damage taken from players: fire x0.95; damage taken from enemies: fire x0.83 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4022 | Flamedrake Talisman +2 | 340220: damage taken from players: fire x0.94; damage taken from enemies: fire x0.8 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4030 | Boltdrake Talisman | 340300: damage taken from players: lightning x0.96; damage taken from enemies: lightning x0.87 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4031 | Boltdrake Talisman +1 | 340310: damage taken from players: lightning x0.95; damage taken from enemies: lightning x0.83 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4032 | Boltdrake Talisman +2 | 340320: damage taken from players: lightning x0.94; damage taken from enemies: lightning x0.8 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4040 | Haligdrake Talisman | 340400: damage taken from players: holy x0.96; damage taken from enemies: holy x0.87 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4041 | Haligdrake Talisman +1 | 340410: damage taken from players: holy x0.95; damage taken from enemies: holy x0.83 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4042 | Haligdrake Talisman +2 | 340420: damage taken from players: holy x0.94; damage taken from enemies: holy x0.8 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4050 | Pearldrake Talisman | 340500: damage taken from players: magic x0.98, fire x0.98, lightning x0.98, holy x0.98; damage taken from enemies: magic x0.95, fire x0.95, lightning x0.95, holy x0.95 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4051 | Pearldrake Talisman +1 | 340510: damage taken from players: magic x0.97, fire x0.97, lightning x0.97, holy x0.97; damage taken from enemies: magic x0.93, fire x0.93, lightning x0.93, holy x0.93 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4052 | Pearldrake Talisman +2 | 340520: damage taken from players: magic x0.96, fire x0.96, lightning x0.96, holy x0.96; damage taken from enemies: magic x0.91, fire x0.91, lightning x0.91, holy x0.91 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 4060 | Crucible Scale Talisman | 340600: absorption: all damage taken x0.7 (*DamageCutRate) | only while receiving a critical | absorption | VERIFIED |
| 4070 | Crucible Feather Talisman | 340700: absorption: all damage taken x1.3 (*DamageCutRate) | roll i-frames +3 (TAE, pre-1.17 extraction); cut-rate penalty always on | absorption | VERIFIED |
| 4080 | Blue-Feathered Branchsword | 340800: damage taken from players: all elements x0.5; damage taken from enemies: all elements x0.5 | HP <= 20% of max; label only; the HP field is the gate | damage taken from enemies, damage taken from players | HP gate VERIFIED; no stateInfo 49 consumer found |
| 4090 | Ritual Shield Talisman | 340900: damage taken from players: all elements x0.7; damage taken from enemies: all elements x0.7 | HP >= 100% of max | damage taken from enemies, damage taken from players | HP gate VERIFIED |
| 4100 | Greatshield Talisman | 341000: guard: stamina lost when blocking x0.8 | only on a guarded hit | guard | VERIFIED |
| 4110 | Crucible Knot Talisman | 341100: (no field) | head hit keeps part multiplier 1.0 | - | VERIFIED |
| 5000 | Crimson Seed Talisman | 350000: HP: Crimson flask healing x1.2 | always (equipped) | HP | always on: VERIFIED |
| 5010 | Cerulean Seed Talisman | 350100: FP: Cerulean flask FP x1.2 | always (equipped) | FP | always on: VERIFIED |
| 5020 | Blessed Dew Talisman | 350200: HP: HP +2 every 1s | always (equipped) | HP | always on: VERIFIED |
| 5030 | Taker's Cameo | 350300: (no field) | on rune award (kill): applies applyIdOnGetSoul | - | VERIFIED |
|  |  | 350301: HP: HP +30 +3% max once | on rune award (a kill) | HP | on rune award: VERIFIED |
| 5040 | Godskin Swaddling Cloth | 350400: (no field) | accumulator 2 (successive hits) | - | VERIFIED |
|  |  | 350401: HP: HP +30 +3% max once | accumulator 2 (successive hits); lasts 1s | HP | VERIFIED |
| 5050 | Assassin's Crimson Dagger | 350500: (no field) | critical rows apply 350501 -> 350502 heal | - | VERIFIED params |
|  |  | 350502: HP: HP +85 +10% max once | owner has an effect with stateInfo 288; lasts 1s | HP | presence gate VERIFIED |
| 5060 | Assassin's Cerulean Dagger | 350600: (no field) | critical rows apply 350601 -> 350602 FP | - | VERIFIED params |
|  |  | 350602: FP: FP +15 once | owner has an effect with stateInfo 289; lasts 1s | FP | presence gate VERIFIED |
| 6000 | Crepus's Vial | 360000: misc: noise heard by enemies x0 | label (hearing) | misc | INFERRED |
| 6010 | Concealing Veil | 360100: (no field) | owner has an effect with stateInfo 466 | - | presence gate VERIFIED |
| 6020 | Carian Filigreed Crest | 360200: FP: skill FP cost x0.75 | always (equipped) | FP | always on: VERIFIED |
| 6040 | Longtail Cat Talisman | 360400: misc: fall damage x0 | always (equipped) | misc | always on: VERIFIED |
| 6050 | Shabriri's Woe | 360500: misc: enemy target priority 0.1 | always (equipped) | misc | always on: VERIFIED |
| 6060 | Daedicar's Woe | 360600: absorption: all damage taken x2 (*DamageCutRate) | always (equipped) | absorption | always on: VERIFIED |
| 6070 | Sacrificial Twig | 360700: (no field) | breaks on death, keeps runes | - | INFERRED |
| 6080 | Furled Finger's Trick-Mirror | 360800: (no field) | always (equipped) | - | always on: VERIFIED |
| 6090 | Host's Trick-Mirror | 360900: (no field) | always (equipped) | - | always on: VERIFIED |
| 6110 | Ancestral Spirit's Horn | 361100: (no field) | on rune award (kill): applies applyIdOnGetSoul | - | VERIFIED |
|  |  | 361101: FP: FP +3 once | on rune award (a kill) | FP | on rune award: VERIFIED |
| 7000 | Crimson Amber Medallion +3 | 20370000: HP: max HP x1.1 | always (equipped) | HP | always on: VERIFIED |
| 7010 | Cerulean Amber Medallion +3 | 20370100: FP: max FP x1.13 | always (equipped) | FP | always on: VERIFIED |
| 7020 | Viridian Amber Medallion +3 | 20370200: stamina: max stamina x1.17 | always (equipped) | stamina | always on: VERIFIED |
| 7030 | Two-Headed Turtle Talisman | 20370300: stamina: stamina recovery +10/s | label only (regen is not gated on it) | stamina | VERIFIED |
| 7040 | Stalwart Horn Charm +2 | 20370400: resistance: bleed +180, frost +180 | always (equipped) | resistance | always on: VERIFIED |
| 7050 | Immunizing Horn Charm +2 | 20370500: resistance: poison +180, rot +180 | always (equipped) | resistance | always on: VERIFIED |
| 7060 | Clarifying Horn Charm +2 | 20370600: resistance: sleep +230, madness +230 | always (equipped) | resistance | always on: VERIFIED |
| 7080 | Mottled Necklace +2 | 20370800: resistance: poison +100, rot +100, bleed +100, frost +100, sleep +100, madness +100 | always (equipped) | resistance | always on: VERIFIED |
| 7090 | Spelldrake Talisman +3 | 20370900: damage taken from players: magic x0.93; damage taken from enemies: magic x0.78 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 7100 | Flamedrake Talisman +3 | 20371000: damage taken from players: fire x0.93; damage taken from enemies: fire x0.78 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 7110 | Boltdrake Talisman +3 | 20371100: damage taken from players: lightning x0.93; damage taken from enemies: lightning x0.78 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 7120 | Golden Braid | 20371200: damage taken from players: holy x0.93; damage taken from enemies: holy x0.78 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 7130 | Pearldrake Talisman +3 | 20371300: damage taken from players: magic x0.95, fire x0.95, lightning x0.95, holy x0.95; damage taken from enemies: magic x0.89, fire x0.89, lightning x0.89, holy x0.89 | always (equipped) | damage taken from enemies, damage taken from players | always on: VERIFIED |
| 7140 | Crimson Seed Talisman +1 | 20371400: HP: Crimson flask healing x1.3 | always (equipped) | HP | always on: VERIFIED |
| 7150 | Cerulean Seed Talisman +1 | 20371500: FP: Cerulean flask FP x1.3 | always (equipped) | FP | always on: VERIFIED |
| 8000 | Blessed Blue Dew Talisman | 20380000: FP: FP +1 every 2s | label (FP regeneration) | FP | INFERRED |
| 8010 | Fine Crucible Feather Talisman | 20380100: absorption: all damage taken x1.15 (*DamageCutRate) | backstep i-frames (TAE); cut-rate penalty always on | absorption | VERIFIED |
| 8020 | Outer God Heirloom | 20380200: attributes: ARC +5 | always (equipped) | attributes | always on: VERIFIED |
| 8030 | Shattered Stone Talisman | 20380300: damage vs players: all elements x1.05; damage vs enemies: all elements x1.1 | attack subcategory in {127 stomp / kick} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8040 | Two-Handed Sword Talisman | 20380400: damage vs players: all elements x1.1; damage vs enemies: all elements x1.15 | attack subcategory in {120 two-handed attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8050 | Crusade Insignia | 20380500: (no field) | on rune award (kill): applies applyIdOnGetSoul | - | VERIFIED |
|  |  | 20380501: damage vs players: all elements x1.1; damage vs enemies: all elements x1.15 | timed buff (ctx active 'crusade'); lasts 20s | damage vs enemies, damage vs players | on rune award: VERIFIED |
| 8060 | Aged One's Exultation | 20380600: (no field) | owner has an effect with stateInfo 495 | - | presence gate VERIFIED |
|  |  | 20380601: damage vs players: all elements x1.12; damage vs enemies: all elements x1.2 | timed buff (ctx active 'aged_one'); lasts 30s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED |
| 8070 | Arrow's Soaring Sting Talisman | 20380700: attack power: AR x1.08 (*AttackRate) | attack subcategory in {105 ammunition attack, 113 ranged skill, 118 ammunition on-hit} | attack power | subcategory match VERIFIED |
|  |  | 20380701: ranged: bow range +50% | bow range: added to the weapon bowDistRate; lasts 0.1s | ranged | VERIFIED |
| 8090 | Pearl Shield Talisman | 20380900: (no field) | always (equipped) | - | always on: VERIFIED |
|  |  | 20380901: absorption: non-physical damage taken x0.8 (*DamageCutRate) | only on a guarded hit; left hand; lasts 0.1s | absorption | VERIFIED |
| 8100 | Dried Bouquet | 20381000: (no field) | spirit death gives spiritDeathSpEffectId | - | VERIFIED |
|  |  | 20381001: damage vs players: all elements x1.2; damage vs enemies: all elements x1.2 | timed buff (ctx active 'dried_bouquet'); lasts 30s | damage vs enemies, damage vs players | on spirit death: VERIFIED |
| 8110 | Smithing Talisman | 20381100: damage vs players: all elements x1.05; damage vs enemies: all elements x1.1 | attack subcategory in {119 thrown item} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8120 | Ailment Talisman | 20381200: (no field) | always (equipped) | - | always on: VERIFIED |
|  |  | 20381231: resistance: poison +350, rot +350 | lasts 120s | resistance | event chain from the talisman row: VERIFIED |
|  |  | 20381233: resistance: bleed +350, frost +350 | lasts 45s | resistance | event chain from the talisman row: VERIFIED |
|  |  | 20381261: status buildup: build-up on the wearer poison -100 | stateInfo 2; lasts 120s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381235: resistance: sleep +350, madness +350 | lasts 30s | resistance | event chain from the talisman row: VERIFIED |
|  |  | 20381262: status buildup: build-up on the wearer rot -100 | stateInfo 5; lasts 120s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381263: status buildup: build-up on the wearer bleed -100 | stateInfo 6; lasts 45s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381264: status buildup: build-up on the wearer frost -100 | stateInfo 260; lasts 45s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381265: status buildup: build-up on the wearer sleep -100 | stateInfo 436; lasts 30s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381266: status buildup: build-up on the wearer madness -100 | stateInfo 437; lasts 30s | status buildup | event chain from the talisman row: VERIFIED |
|  |  | 20381251: resistance: poison +350 | lasts 120s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
|  |  | 20381252: resistance: rot +350 | lasts 120s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
|  |  | 20381253: resistance: bleed +350 | lasts 30s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
|  |  | 20381254: resistance: frost +350 | lasts 45s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
|  |  | 20381255: resistance: sleep +350 | lasts 30s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
|  |  | 20381256: resistance: madness +350 | lasts 30s | resistance | started by AtkParam spEffectId / TAE: VERIFIED |
| 8130 | Retaliatory Crossed-Tree | 20381300: damage vs players: all elements x1.12; damage vs enemies: all elements x1.17 | attack subcategory in {121 backstep / rolling attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8140 | Lacerating Crossed-Tree | 20381400: damage vs players: all elements x1.075; damage vs enemies: all elements x1.15 | attack subcategory in {122 dash attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8150 | Sharpshot Talisman | 19990: (no field) | always (equipped) | - | always on: VERIFIED |
|  |  | 19991: damage vs players: all elements x1.08; damage vs enemies: all elements x1.12 | timed buff (ctx active 'sharpshot'); attack subcategory in {105 ammunition attack, 113 ranged skill, 118 ammunition on-hit}; lasts 0.1s | damage vs enemies, damage vs players | trigger not found; subcategory match VERIFIED |
| 8160 | St. Trina's Smile | 20381600: (no field) | owner has an effect with stateInfo 480 | - | presence gate VERIFIED |
|  |  | 20381601: damage vs players: all elements x1.12; damage vs enemies: all elements x1.2 | timed buff (ctx active 'st_trina'); lasts 30s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED |
| 8170 | Talisman of the Dread | 20381700: damage vs players: all elements x1.1; damage vs enemies: all elements x1.15 | attack subcategory in {123 magma attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8180 | Enraged Divine Beast | 20381800: damage vs players: all elements x1.1; damage vs enemies: all elements x1.1 | attack subcategory in {124 storm attack} | damage vs enemies, damage vs players | subcategory match VERIFIED |
| 8190 | Beloved Stardust | 20381900: spells: casting speed as +99 DEX | always (equipped) | spells | always on: VERIFIED |
|  |  | 20381901: absorption: all damage taken x1.3 (*DamageCutRate) | lasts 0.1s | absorption | always on: VERIFIED |
| 8200 | Talisman of Lord's Bestowal | 20382000: (no field) | flask animations apply 20382003 (and 20382004 mounted) | - | VERIFIED TAE |
|  |  | 20382003: poise: poise x1.538 (damage to poise x0.65) | timed buff (ctx active 'lords_bestowal'); owner has an effect with stateInfo 496 | poise | started by AtkParam spEffectId / TAE: VERIFIED; presence gate VERIFIED |
|  |  | 20382004: poise: poise damage taken x0 | timed buff (ctx active 'lords_bestowal_mounted'); owner has an effect with stateInfo 496 | poise | started by AtkParam spEffectId / TAE: VERIFIED; presence gate VERIFIED |
| 8210 | Verdigris Discus | 19985: (no field) | always (equipped) | - | always on: VERIFIED |
|  |  | 19986: damage taken from players: all elements x0.925; damage taken from enemies: all elements x0.9 | equip load ratio in (0.7, 1] (INFERRED band); lasts 0.1s | damage taken from enemies, damage taken from players | trigger not found; band from row name INFERRED |
|  |  | 19987: damage taken from players: all elements x0.85; damage taken from enemies: all elements x0.8 | equip load ratio in (1, 99] (INFERRED band); lasts 0.1s | damage taken from enemies, damage taken from players | trigger not found; band from row name INFERRED |
| 8220 | Rellana's Cameo | 20382200: (no field) | stance TAE applies 20382205 / 20382201 chain | - | VERIFIED TAE |
|  |  | 20382203: damage vs players: all elements x1.45; damage vs enemies: all elements x1.45 | timed buff (ctx active 'rellana_stance'); owner has an effect with stateInfo 504; lasts 0.2s | damage vs enemies, damage vs players | presence gate VERIFIED |
|  |  | 20382204: damage vs players: all elements x1.45; damage vs enemies: all elements x1.45 | timed buff (ctx active 'rellana'); owner has an effect with stateInfo 502; lasts 10s | damage vs enemies, damage vs players | presence gate VERIFIED |
| 8230 | Blade of Mercy | 20382300: (no field) | critical rows apply 20382301 -> 20382302 | - | VERIFIED params |
|  |  | 20382302: damage vs players: all elements x1.12; damage vs enemies: all elements x1.2 | timed buff (ctx active 'blade_of_mercy'); lasts 20s | damage vs enemies, damage vs players | event chain from the talisman row: VERIFIED |
| 8240 | Talisman of All Crucibles | 20382400: (no field) | always (equipped) | - | always on: VERIFIED |
|  |  | 20382402: absorption: all damage taken x0.7 (*DamageCutRate) | only while receiving a critical | absorption | VERIFIED |
|  |  | 20382404: absorption: all damage taken x1.45 (*DamageCutRate) | roll i-frames +3 (TAE, pre-1.17 extraction); cut-rate penalty always on | absorption | VERIFIED |

## 7. How often STR PvP builds wear each one

`python3 scripts/er-mechanics-talismans.py --corpus` counts talismans in the planner corpus
(`~/.cache/er-build-planner/builds.jsonl`, `build.talismans`, active set only). It uses
`er-builds-adoption-gap.py`'s filters and dedup:
- RL 140-160, not PvE;
- `pvptag`: Strength tag plus one of Invasions, Duels, Co-op/Gank, 2v2 or Ladder, 161 builds;
- `tag`: Strength tag, 315 builds;
- `str60`: STR 60 or more, 173 builds.

| talisman | pvptag | % | tag | str60 | PvP melee effect |
|---|---|---|---|---|---|
| Two-Handed Sword Talisman | 77 | 48% | 128 | 65 | x1.10 on every 2H attack |
| Bull-Goat's Talisman | 76 | 47% | 129 | 72 | poise x1.333 |
| Great-Jar's Arsenal | 75 | 47% | 156 | 83 | load x1.19 |
| Erdtree's Favor +2 | 68 | 42% | 129 | 61 | HP x1.04, stamina x1.10, load x1.08 |
| Crimson Amber Medallion +3 | 64 | 40% | 98 | 53 | HP x1.10 |
| Blue-Feathered Branchsword | 60 | 37% | 80 | 33 | taken x0.5 below 20% HP |
| Ritual Shield Talisman | 36 | 22% | 51 | 30 | taken x0.7 at full HP |
| Shard of Alexander | 22 | 14% | 61 | 35 | skills x1.15 |
| Spear Talisman | 18 | 11% | 33 | 24 | thrust counter x1.15 |
| Two-Headed Turtle Talisman | 15 | 9% | 39 | 22 | +10 stamina/s |
| Red-Feathered Branchsword | 14 | 9% | 33 | 22 | x1.2 below 20% HP |
| Rotten Winged Sword Insignia | 11 | 7% | 19 | 10 | x1.06-1.13 on successive hits |
| Ritual Sword Talisman | 9 | 6% | 19 | 10 | x1.1 at full HP |
| Lacerating Crossed-Tree | 8 | 5% | 15 | 10 | running attacks x1.075 |
| Retaliatory Crossed-Tree | 6 | 4% | 16 | 15 | roll/backstep/crouch attacks x1.12 |
| Millicent's Prosthesis | 6 | 4% | 13 | 7 | DEX +5, x1.04-1.11 on successive hits |
| Crimson Seed Talisman +1 | 5 | 3% | 6 | 7 | flask x1.3 |
| Godskin Swaddling Cloth | 5 | 3% | 8 | 4 | heal 3% + 30 per 32 counter |
| Blessed Dew Talisman | 4 | 2% | 6 | 5 | +2 HP/s |
| Dagger Talisman | 3 | 2% | 6 | 4 | criticals x1.16 |
| Greatshield Talisman | 3 | 2% | 9 | 4 | guard stamina x0.8 |
| Dragoncrest Greatshield Talisman | 3 | 2% | 16 | 15 | physical taken x0.95 |
| Roar Medallion | 3 | 2% | 5 | 3 | roars x1.15, breath x1.05 |
| Claw Talisman | 3 | 2% | 8 | 7 | jump attacks x1.075 |
| Godfrey Icon | 2 | 1% | 6 | 3 | charged skills x1.15 |
| Axe Talisman | 2 | 1% | 6 | 3 | charged R2 x1.10 |
| Kindred of Rot's Exultation | 2 | 1% | 11 | 4 | x1.2 near rot/poison |
| Lord of Blood's Exultation | 1 | 1% | 13 | 5 | x1.12 near blood loss |
| Curved Sword Talisman | 1 | 1% | 4 | 3 | guard counters x1.2 |

Every other talisman appears in 2 or fewer `pvptag` builds.

Things the corpus shows against the mechanics:
- **Two-Handed Sword Talisman is the most-worn talisman**, and it is also the largest always-on
  melee boost for a 2H STR build.
- **Claw Talisman is rare**, and it is x1.075 vs players on jump attacks only.
- **Axe Talisman is x1.10 on charged R2**, more per hit than Claw against players, and is also rare.
- **Hammer Talisman does not appear at all.**
- **The planner writes the same talisman names as the game's `AccessoryName`**, so every name
  resolves.

## 8. Not established

Settled 2026-10-01 (guard dig, powerstance-guard.md section 3):

- **Hammer Talisman.** The stamina damage it scales is `AttackDamageInfo+0x28`, which the ADI
  builder `FUN_140d24b10` fills from attacker vcall +0x368 -> `FUN_14068aa80`; that value times
  the info+0x1fc factor is the incoming side of `CalculateGuardStaminaDepletion`. So x1.4 lands on
  the whole pre-guard stamina number, before the blocker's guard boost. `ADI+0x100`, which this
  section used to name, is the poise value (`FUN_14068ad90`, read by `ApplySuperArmorDamage`).
- **Guard regen.** Every `GuardOn`/`GuardStart` clip sets TAE 225 `SetSPRegenRatePercent` to 20
  for its whole length, so a raised guard regenerates 20% of the normal rate, turtle-talisman adds
  included, and 0% through a guard reaction's first 10/27/49 frames.

Still open:

- **Critical flag.** What sets `AttackInfo+0x109`, the critical flag the accumulator reads.
- **Other damage factors.** The factors `ADI+0x1e8..` and `FUN_1403e9bb0` beside `*AttackRate`
  in `CalculateDamage`.
- **Winged Sword stages past 60.** Whether stage rows 1-3 and stage 4 are active together past a
  counter of 60, and whether spCategory 120 makes them overwrite each other.
- **Godfrey Icon's 330901.** Its subcategory 38 appears on no `AtkParam_Pc` row. Which weapon's a525
  anims 45010/45110 apply it is unknown.
- **Unfound triggers.** Nothing found applies Verdigris Discus 19986/19987 or Sharpshot 19991.
  The script's Verdigris bands are INFERRED.
- **Branchsword labels.** stateInfo 48/49 have no code consumer; the HP field alone gates them.
- **Exultation presence.** What fires the presence bullets (BehaviorParam 2100/2105/2150/2160),
  and which teams they hit.
- **Lord's Bestowal.** ChrActionFlag bit 0x100 (JumpTable 24 during its flask animation) has no
  reader found. What applies its Torrent rows 20382015/20382016 is also unknown.
- **Frame counts.** Every TAE frame count (roll and backstep i-frames, Rellana, Lord's Bestowal)
  comes from the 2026-07-13 extraction, which predates 1.17; they need a 1.17.1 re-read.
- **Headshots.** The headshot part multiplier value, and the meaning of part id 0x1f.
- **FP side.** The FP collectors for Blessed Blue Dew and Cerulean Seed.
- **`effectTarget*` bits.** Their consumer.
- **`accessoryGroup`.** Where the one-per-group equip rule is enforced (carried over from
  resources.md).
