# Powerstance, off-hand attacks and blocking

Labels as in `attacks.md`. **VERIFIED** = a regulation value (installed 1.17.1 `regulation.bin`)
or code read out of the executable. EXE addresses are 1.16.2 VAs from the named Ghidra dump on
:8765, identical to `eldenring-deobf.bin` (shift 0). The running game is 2.7.2.0, so find an
address on `eldenring-deobf-1.17.2.bin` by its bytes before using it there
(`docs/game-build.md`). **TAE** = decoded from `c0000.anibnd -> tae/a<cat>.tae` or the
`c0000.behbnd` behavior graph (`scripts/er-behbnd-attack-map.py`). **MEASURED** = counted over
the planner corpus `~/.cache/er-build-planner/builds.jsonl`. **INFERRED** = consistent with the
data, consumer not traced. **COMMUNITY** = outside source: Smithbox's decompiled `c0000.hks`
(`Documentation/ER/c0000.hks`, `LUA/Enums.txt`), WitchyBND's `TAE.Template.ER.xml` and Smithbox
row names. Nothing was launched.

Tool: `scripts/er-mechanics-powerstance-guard.py` (imports `er-mechanics-attacks.py` and
`er-mechanics-ar.py`). `--selftest`: 33 passed and 1 skipped without `ER_BEHBND_JSON`; the skip
is the three behavior-graph checks (section 6).

## 0. Two corrections to `attacks.md`

That file is owned by another agent and was not edited. Its claims that these corrections
replace:

- **Judge ranges (TAE).** Section 0 files 600-7xx as paired or powerstance attacks and 800-895
  as mounted. It is the other way round:
  - `c0000.behbnd`: `RideAttack_R_Top/02/03` play 038000/038010/038020 and
    `RideAttack_L_Top*` play 0390xx. Those clips fire 600/605/610 and 700/705/710. 630/635
    are the mounted charged R2 and 632 is the uncharged one.
  - `AttackDualLight1..6` play 034000..034050 and fire 800..836.
  - `AttackLeftLight1` and `AttackLeftHeavy1..6` play 035000..035050 and fire 400..450.
- **Guard level (VERIFIED).** Section 3's "guard level against a guard" is not the guard-break
  test. 0x14068c080 is the attacker's repel (bounce) value. Its -5/-10 "hand term" is weapon
  durability status: `FUN_1406568c0` reads `PlayerIns.equipmentDurabilityStatuses[slot]`,
  status 1 adds -5.0 (0x1429e5c30) and status 2 adds -10.0 (0x1429ce438). Guard break is
  decided by stamina alone (section 3).
- **AR (VERIFIED).** Section 1's "second weapon's base" term in 0x1406832a0 is the bow that
  launched an arrow or bolt, not the other hand's weapon.
  - `InitAttackStruct` 0x14038cd60 sets `AttackInfo+0xf0 = -1`, and the melee hit filler
    `FUN_14068ffa0` never writes it.
  - The status screen `FUN_14065c120` puts the launcher there for `weaponCategory` 10/11.
  - Every melee hit therefore uses exactly one weapon.

## 1. Which weapon a hit uses

VERIFIED (EXE `CSChrTaeAnimEvent::AttackBehavior` 0x1404266d0):

```
slot = GetAttackReferenceHandSlot(chr)                       0x140655500
if (Args[+0xd] != 0) slot = (Args[+0xd] == 1) + LeftWeaponSlot   1 = right, 2 = left
ResolveBehaviorId(chr, judge = Args[+8], slot)                0x140652280
FUN_1404428f0(..., slot, ...)                                 hitbox + stamina charge
```

- Byte +0xd of the AttackBehavior args is WitchyBND's `Source`: 0 default, 1 force right hand,
  2 force left hand (COMMUNITY name, VERIFIED use).
- TAE, counted over a20..a35 and several more: in the 0340xx powerstance clips, 185 hitboxes
  carry Source 1 and 178 carry Source 2. The only 800-range hitboxes with Source 0 are 12 in the
  falling-jump loop 034560. In 0350xx all 67 off-hand hitboxes carry Source 0.
- So a powerstance L1 is two or three separate hits. Each is resolved against its own hand's
  weapon (`behaviorVariationId`, so a mixed pair uses each weapon's own rows), with that weapon's
  base, reinforce rate, scaling, poise (`0x14068af30` reads only `AttackInfo+0xe8`) and stamina
  damage. Nothing sums the two weapons.
- Two-hand STR bonus: `FUN_14068ffa0` sets `AttackInfo+0xf5 = ChrIns::IsTwoHanding` (0x1403f4930,
  `armStyle - LeftBothHands < 2`), and `FUN_140690390` applies x1.5 STR only then (VERIFIED).
  - Off-hand attacks are one-handed.
  - The HKS returns -1 from `IsEnableDualWielding` while two-handing (COMMUNITY), so powerstance
    hits are one-handed too.
  - The menu paths clear the bonus for `isDualBlade`; whether live combat does is not traced.
- Off-hand hits (Source 0 in `AttackLeft_SM`) use the left weapon for every term (VERIFIED:
  `+0xe8` = the left weapon, `+0xf0` = -1). That the reference hand is the left one in these
  states is INFERRED from the rows they resolve to: Smithbox names them
  `Default - Dagger - Left 1H Light #n`.

## 2. Powerstance and off-hand numbers

### Who can powerstance

The rule lives only in the behavior script. `IsEnableDualWielding` (COMMUNITY decompile, around
line 2417; the compiled vanilla HKS carries the same identifiers) works like this:

- It returns -1 while either hand is two-handed.
- Otherwise it needs `env(GetEquipWeaponCategory, R) == env(..., L)` for one of these
  categories: 20 dagger, 22 claw, 23 straight sword, 24 twinblade, 25 greatsword, 26 colossal
  sword, 27 thrusting sword, 28 curved sword, 29 katana, 30 axe, 31 colossal weapon, 32
  greataxe, 33 hammer, 34 flail, 35 great hammer, 36 spear, 37 great spear, 38 halberd, 39 heavy
  thrusting sword, 40 curved greatsword, 42 fist, 43 whip, 50 reaper, and the unnamed 53, 55-59.
- Shields (47-49), staves, seals, bows, crossbows, ballistae and torches never pair. No
  cross-category pair exists.
- There are two exceptions on `GetEquipWeaponSpecialCategoryNumber`, which is env 345 =
  `EquipParamWeapon.spAtkcategory` (VERIFIED, case at 0x1404119cb):
  - A dagger with 104 pairs only with another 104.
  - A katana also pairs with a left-hand 104.
  - In 1.17.1 only the Wakizashi carries 104 (VERIFIED regulation).
- Env 225 `GetEquipWeaponCategory` returns `wepmotionCategory` (VERIFIED 2026-10-19). `HksEnv`
  (0x140410820) sends 225 through jump table 0x1404133a4 (index env - 0xdf) to 0x140410e05,
  which calls ChrIns vtable slot 0x248. On `PlayerIns` (vtable 0x142a7cb40) that is 0x1406568b0,
  the int at index `hand` of the 12-byte array PlayerIns+0x630 points to. `FUN_1404d0eb0` fills
  its slots 0 and 1 from `FUN_1403beae0`, which reads `EquipParamWeapon+0xe7`,
  `wepmotionCategory` (1.16.2; 1.17.1 has the same read at 0x1403beaf0). So the menu class
  (`wepType`) does not decide: the Pickaxe is a great hammer (23) with motion category 32 and
  powerstances with greataxes such as the Rusted Anchor (19, 32), not with Great Stars (23, 35).
  The HKS enum values 20..52 equal the `wepmotionCategory` numbering exactly: Dagger 20,
  Longsword 23, Greatsword 26, Giant-Crusher 31, shields 47/48/49. The DLC categories 60 (light greatsword),
  61 (great katana) and 62 (beast claw) are not in the list. Either they cannot powerstance or
  the decompile predates them; this is not resolved.
- `isDualBlade` (env 340 `IsTwinSwords`) is not tested in `IsEnableDualWielding`.

### Tables (regulation 1.17.1, +25 for the reinforce-dependent columns)

Poise is internal (x10 = menu). Stam = stamina cost summed over hits. First/last = first and last
hit frame (30 fps). L1 = first frame the next L1 can start, using `Input - LH Attack` 9 or
`Input - Common` 87 with `Cancel - LH Attack` 16 or `Cancel - L1 Attack` 117 (the attacks.md
section 4 pairing). Roll = first roll frame. r/l = hand.

**Greatsword + Greatsword** (`dual Greatsword Greatsword`; Giant-Crusher's pair uses the same
clips, `a31` = `a26` frame for frame except its running/backstep poise split):

| slot | hits | per hit: hand judge MV poise frames | MV sum | poise sum | stam | L1 | roll | HA |
|---|---|---|---|---|---|---|---|---|
| L1 #1 | 2 | l 805 92 8.64 f23-26; r 800 92 8.64 f27-29 | 184 | 17.28 | 44 | 43 | 50 | f11-40 +9.0 |
| L1 #2 | 2 | r 810 94 f27-31; l 815 94 f27-30 | 188 | 17.28 | 44 | 41 | 50 | f13-40 +9.0 |
| L1 #3 | 2 | l 825 96 f36-40; r 820 96 f38-40 | 192 | 17.28 | 44 | 75 | 67 | f20-51 +9.0 |
| running L1 | 2 | r 860 100; l 865 100 | 200 | 17.28 | 54 | 52 | 52 | +6.75 |
| rolling L1 | 2 | r 870 85; l 875 85 (f22-24 both) | 170 | 17.28 | 36 | 47 | 51 | +6.75 |
| jump L1 | 2 | r 890 115 14.4; l 895 115 14.4 | 230 | 28.8 | 36 | 44 | 48 | +6.75 |

Against the same weapon's own attacks:
- R1 #1: MV 100, poise 14.4, stam 22, hit f22-26, next R1 f38.
- Off-hand L1 #1 (`offhand Greatsword`): MV 100, poise 14.4, stam 22, f22-26, next L1 f38,
  HA +9.0.

So the powerstance L1 is 1.84x the MV of an R1 for 2x the stamina. Each hit carries 60% of the
R1's poise, 8.64 against 14.4 (the paired rows use `atkSuperArmorCorrection` 144 against 240).
The chain is the same three swings long.

**Giant-Crusher** (`offhand Giant-Crusher`, `dual Giant-Crusher Giant-Crusher`):

| slot | hits | MV | poise | stam | frames | L1 | roll |
|---|---|---|---|---|---|---|---|
| off-hand L1 #1 | 1 | 100 | 18.0 | 25 | f22-25 | 40 | 53 |
| off-hand L1 #2 | 1 | 102 | 18.0 | 25 | f26-28 | 42 | 55 |
| off-hand L1 #3 | 1 | 108 | 18.0 | 25 | f27-30 | 64 | 59 |
| pair L1 #1 | 2 | 92 + 92 | 10.8 + 10.8 | 50 | f23-26, f27-29 | 42 | 51 |
| pair running L1 | 2 | 100 + 100 | 6.6 (r) + 15.0 (l) | 62 | f24-31 | 58 | 57 |

**Erdsteel Dagger** (`offhand "Erdsteel Dagger"`, `dual "Erdsteel Dagger" Dagger`). Its
`behaviorVariationId` 115 has no rows of its own, so every judge falls back to the Dagger
family (100). Its `spAtkcategory` 103 is not 104, so it pairs with any non-Wakizashi dagger.

| slot | hits | per hit | MV sum | poise sum | stam | first-last | L1 | roll |
|---|---|---|---|---|---|---|---|---|
| off-hand L1 #1..#5 | 1 | MV 100..104, poise 3.0 | 100..104 | 3.0 | 9 | f8-13 | 12-16 | 15-19 |
| off-hand L1 #6 | 1 | MV 110, poise 6.0 | 110 | 6.0 | 9 | f14-16 | 32 | 28 |
| pair L1 #1 | 3 | l 805 80 f10-13; l 806 80 f17-19; r 800 80 f25-28 | 240 | 5.4 | 18 | f10-28 | 29 | 31 |
| pair L1 #2 | 2 | r 810 98 f9-11; l 815 98 f12-14 | 196 | 3.6 | 16 | f9-14 | 15 | 21 |
| pair L1 #3 | 2 | l 825 100 f9-11; r 820 100 f17-19 | 200 | 3.6 | 16 | f9-19 | 20 | 25 |
| pair L1 #4 | 3 | l 835 84; l 836 84; r 830 84 | 252 | 7.2 | 18 | f12-26 | 40 | 37 |

Hits per L1 (TAE):
- Colossal sword, colossal weapon and greatsword pairs: 2 per swing, 3 swings.
- Dagger pairs: 3/2/2/3 over a 4-swing chain. The dagger chain has no L1 #5/#6 clip hit.

## 3. Blocking

### Chain (VERIFIED unless marked)

```
guard held? angle test only            FUN_140448fc0: cos(guardAngle) <= dot
repel?     FUN_140447180 (from CalculateDamage, unless info+0x267 & 2)
   atk = max(int(attackBaseRepel * guardAtkRateCorrection/100 [+ guardAtkRate]
                 + clamp(STR - overStrength, 0, 10) + durability term),        0x14068c080
             SpEffect GetAtkFlickPower)
   def = int(guardBreakCorrection/100 * guardBaseRepel * SpEffect guardDefFlickPowerRate)
         + clamp(STR - overStrength, 0, 10) + durability term                  FUN_14068c3c0
         (0 when the defender lacks the weapon's STR/DEX/INT/FTH requirement)
   def >= atk  ->  info+0x264 = repelled, HP damage *= min(clamp(1 - max SpEffect
                   flickDamageCutRate/100, 0, 1), GameSystemCommonParam.flickDamageCutRateSuccessGurad)
                   (FUN_1404f7280; 0.5 in 1.17.1). Applied last, after the stamina lines below:
                   a repelled hit still costs its full stamina and can still break the guard.
stamina    FUN_140684540 (PlayerIns::CalculateGuardStaminaDepletion 0x140651f50)
   info+0x28 = (attackBaseStamina * Reinforce.staminaAtkRate * atkStamCorrection/100
                [+ atkStam]) * prod(attacker SpEffect staminaAttackRate)    FUN_14068aa80
   g   = clamp((statBonus + staminaGuardDef * Reinforce.staminaGuardDefRate + 1.0)
               * (1 + guardAtk.guardStaminaCutRate/100) * SpEffect, 0, 100)    0 if stats unmet
   dmg = (1 - g/100) * info+0x28 * info+0x1fc (1.0) + guardBehavior.stamina
   two-handed guard: dmg *= 0.9, or 0.7 when the guard weapon's weaponCategory == 12 (shields)
   both players: dmg *= FinalDamageRateParam[finalDamageRateId].staminaRate (+0x14)
   stamina <= dmg  ->  guard break (info+0x259). On a player the breaking hit stays guarded
                   (CSPlayerDamageModule vtable 0x142a373b0 slot 6 returns 0), so chip applies.
chip       CalculateGuardDamage 0x140689460, per element e
   pass(e) = (100 - (1 + typeCut/100) * (cut_e * Reinforce.cutRate_e + statBonus)
              * durability * (1 + guardAtk.guardRate/100) * info+0x5c) / 100
   info+0x5c = (1 + weapon.guardCutCancelRate/100) * (1 + atk.guardCutCancelRate/100)
              (FUN_140652610; 1.0 when AttackInfo+0xf4 is set, which is not identified)
   HP(e)   = CalculateDefense(e) * pass(e)                     CalculateDamageBasic 0x1406849d0
poise      ApplySuperArmorDamage 0x14047dea0: damage = info+0x244 * info+0x100 * ... * (1 - f)
           * SpEffect rate, f = the module's vtable slot 4 called with info+0x258 (0x14047e0b5).
           CSPlayerSuperArmorModule's (vtable 0x142a3af68) is FUN_14047f260: the guard hand's
           EquipParamWeapon.saGuardCutRate (+0x280) x 0.01 when info+0x258 is set, else 0.
           CalculateDamage sets info+0x258 when the hit meets a raised guard (VERIFIED; 2.7.2.0:
           call 0x14047e6f5, vtable 0x142a3ff98 slot 4 = 0x14047f8a0). saGuardCutRate is 0 on
           every named weapon row but Unarmed (10); the other 20 nonzero rows have no name
           (11000-13000, 100000-101000, 98990000-99060000) (REGULATION, all 3,636 rows).
reaction   GUARD_REACTION_TABLE[guardLevel][damage level], 5x12 ints at 0x143b340b0
           (1.17.1 0x143b380c0): 1 small, 3 middle, 4 large (HKS COMMUNITY). The damage level
           is dmgLevel, or REMAP[dmgLevel_vsPlayer] when that is nonzero (frame-advantage.md 1).
regen      FUN_1404016d0: (45 + SpEffect staminaRecoverChangeSpeed sum)/s x TAE 225
           SetSPRegenRatePercent / 100 x staminaRecoveryModifier (the sum: Green Turtle 8,
           Two-Headed Turtle 10, talismans.md section 4; a held Great Turtle Shell 3, REGULATION;
           the modifier 0.8 at heavy load, 0.7 overloaded, else 1.0, section 8). Every
           GuardOn/GuardStart clip holds 20 for its whole length (TAE). A guard reaction
           regenerates nothing until the guard comes back: its HKS state calls act(110) every
           update, and a blocker holding L1 returns to the guard on the clip's first TAE
           JumpTable 22 frame, f16 / f32 / f53 (small / middle / large), after the clip's own
           TAE 225 0% span (f0-10 / f0-27 / f0-49) (VERIFIED code, TAE frames; section 7).
```

Hammer Talisman (320700 `staminaAttackRate` 1.4) therefore scales a blocked hit's stamina cost
before the guard boost, and so do Determination (1691/1693, 3.0), Royal Knight's Resolve
(1701/1703, 4.0) and Cragblade (1821/1823 1.5, 1826/1828 1.2), each on the hand its
`wepParamChange` names. The ADI field it scales is +0x28; +0x100 is the poise value
`ApplySuperArmorDamage` reads.

Constants read from the 1.16.2 image (selftest):
- 0.9 at 0x142a1a64c and 0.7 at 0x14329e66c (two-handed guard).
- 1.0 at 0x14329e678, added by `ADDSS XMM2, XMM6` at 0x140684735.
- 100.0 at 0x14329e6d8.
- -5.0 at 0x1429e5c30 and -10.0 at 0x1429ce438 (durability).

What the regulation makes of it (VERIFIED regulation):

- **`overStrength` is 99 on every non-ammunition row**, so `clamp(STR - 99, 0, 10)` is 0 and STR
  does not enter the repel test on either side.
- **Every stat bonus is 0.** `UNKAtkGuardBreak` 0x140689180 scales `*_MaxCorrect` by
  CalcCorrectGraph 160 (DEX, chip), 161 (DEX, elements) and 163 (STR x1.5 if two-handed, guard
  boost). All three graphs have every `stageMaxGrowVal` = 0. `staminaGuardDef_MaxCorrect` is
  also 0 on every weapon read.
- **Guard rows are neutral.** The guard behavior is set by TAE JumpTable 3 alone:
  `_ChrActionFlag` 0x1404275e0 at 0x140427683 raises the guard flag and stores ArgB as the judge
  in the same event. Rows 460/470 carry guardBreakCorrection 100, guardStaminaCutRate 0,
  guardRate 0 and stamina 0 on every weapon and shield checked. Which judge each clip sets (TAE,
  every player TAE scanned):
  - 460: every one-handed guard clip in a00/a02/a03 (`GuardOn`, `GuardStart`, just-guard starts,
    guard reactions).
  - 470: the same clips in a10-a16. That a1x is the two-handed guard is INFERRED from the 2H
    attack clips there. The tool uses 470 for a two-handed guard; the numbers equal 460's.
  - 461 / 471: only the thrusting-shield attacks (category 57) and Flame Spit / Tongues of Fire.
    So greatshield 461's guardStaminaCutRate -40 never applies to a plain block.
  - 480 (guardBreakCorrection 0, guardRate -50 on small and medium shields): the parry skills,
    TAE a692-a699.
- **Guard-cut cancel.** `EquipParamWeapon.guardCutCancelRate` is -50 on 88 rows (every affinity
  of the shotels, scythes, sickles, Nox Flowing Sword, Sword of Night, Obsidian Lamina and
  Bloodhound Claws), -30 on Claws of Night, 0 elsewhere. 168 `AtkParam_Pc` rows carry one too
  (Lucerne skill rows down to -80, Howl of Shabriri -100). A Shotel R1 into a 100%-physical
  shield passes 50% physical; into a Buckler (74%) 63%.
- **Repel chip.** `flickDamageCutRateSuccessGurad` is 0.5: a repelled hit deals half its
  guarded chip.
- **Attack rows** carry guardRate 0 and guardStaminaCutRate 0 on every Greatsword, Giant-Crusher
  and Dagger slot. `FinalDamageRateParam.staminaRate` is 1.25 on 376 of 377 rows and 0.5 on one.
- **Type cuts.** Slash/strike/pierce guard cuts are 0 on every shield row, so `typeCut` is 0.
- **Guard stats by shield class:**

| class (wepType) | guardBaseRepel | staminaGuardDef | physGuardCutRate | guardLevel | +25 staminaGuardDefRate |
|---|---|---|---|---|---|
| small shield (65) | 30 | 38-51 | 74-89 | 2 | 8000: 1.32 |
| medium shield (67) | 50 | 48-61 | 90-100 | 3 | 8100: 1.24 |
| greatshield (69) | 70 (Ant's Skull Plate 80) | 60-82 | 91-100 | 4 | 8200: 1.12 (8300/8600 differ) |
| colossal sword / weapon (7, 41) | 30 | 56 / 58 | 84 / 88 | 2 | 0: 1.2 |
| dagger / straight sword (1, 3) | 10 | 15 / 30 | 35 / 45 | 1 | 0: 1.2 |

Attacker `attackBaseRepel` for the same weapons (VERIFIED):
- 40: dagger, katana
- 60: straight sword, small shield
- 70: greatsword
- 80: colossal sword and colossal weapon

The 1H R1s and uncharged R2s use `guardAtkRateCorrection` 100-120. Charged R2s, rolling,
backstep and running attacks use 140-400, and 2H R1s use 500. Jump attacks and guard counters
carry `isAddBaseAtk` with `guardAtkRate` 3960/7920.

### Into a raised shield (+25 shields, attacker +25 Standard, STR 66 DEX 18 FTH 14, blocker 150 stamina)

Blocker stamina 150 is the median `maxStamina` of the Strength + PvP corpus (MEASURED: 150.7
over 420 builds, 155 over the 161 at RL 140-160). "Hits" is identical attacks until the guard
breaks with no stamina regeneration between them. Stamina is per attack, PvP x1.25, after the
`int()`.

| attack | Brass Shield (medium) stamina / hits | Fingerprint Stone Shield (great) stamina / hits | Buckler (small) stamina / hits, chip | bounces off |
|---|---|---|---|---|
| Greatsword R1 | 45 / 4 | 24 / 7 | 77 / 2, 26% | nothing |
| Greatsword 2H R1 | 54 / 3 | 29 / 6 | | nothing |
| Greatsword charged R2 #1 | 91 / 2 | 49 / 4 | 154 / 1 | nothing |
| Greatsword pair L1 #1 (2 hits) | 80 / 2 | 44 / 4 | | nothing |
| Giant-Crusher R1 | 65 / 3 | 35 / 5 | 110 / 2, 26% | nothing |
| Giant-Crusher 2H R1 | 78 / 2 | 42 / 4 | | nothing |
| Giant-Crusher charged R2 #1 | 156 / 1 | 85 / 2 | 266 / 1 | nothing |
| Giant-Crusher R1, blocker two-hands the greatshield | | 25 / 6 | | nothing |
| Erdsteel Dagger R1 | 17 / 9 | 9 / 17 | 29 / 6, 26% | medium and great shields (40 <= 50, 70) |
| Erdsteel Dagger charged R2 | 26 / 6 | 14 / 11 | 44 / 4 | nothing (guard level 160) |
| Dagger pair L1 #1 (3 hits) | 36 / 5 | 18 / 9 | | medium and great shields |

What the table says:

- A colossal weapon breaks a medium shield's guard in one charged R2 at 150 stamina, or in two
  2H R1s. A +25 greatshield takes two charged R2s and four or five R1s.
- Light weapons do not guard-break shields in any practical sense. Their R1s are also repelled
  by medium shields and greatshields (`attackBaseRepel` 40 against `guardBaseRepel` 50/70). A
  charged R2, running R2, jump attack or guard counter raises the dagger's value past 70 and is
  not repelled. A repelled hit still costs the stamina in the table and half its chip; what it
  costs the attacker is the repel clip: roll and R1 at f22 (small repel) or f27/f30 (large),
  against the blocker's R1 at f5 out of a small guard reaction. About 17 frames of punish.
- With Hammer Talisman (x1.4) the Greatsword R1 into Brass is 63 (3 hits instead of 4) and the
  Giant-Crusher 2H R1 109 (still 2). Royal Knight's Resolve (x4) on one R1 breaks most medium
  shields at 150 stamina.
- 100%-physical shields (Brass, Fingerprint and every greatshield in the table) let no physical
  chip through. The Buckler (74%) passes 26% of each physical hit, after defense.
- A powerstance pair costs a blocker exactly the sum of its hits. A Greatsword pair L1 drains
  1.8x what one R1 does, because each hit has its own `atkStam` row (stamina damage 0.9x an R1's
  per hit).

Two-handed blocking: a shield held two-handed takes 0.7x stamina damage, any other weapon 0.9x
(VERIFIED multiply). That `weaponCategory` 12 is the shield set is VERIFIED from the regulation:
every Buckler, Kite and greatshield row reads 12.

### 3a. Attacking with the guard up (2026-11-13)

The user's ground truth: thrusting shields can block and attack at the same time. How:

**The guard is TAE JumpTable 3 in the attack clip itself** (**TAE**, every player TAE of the
1.17.2 extract scanned; the same event and judge a raised guard sets, section 3). Its span is
when the guard is up, whatever the buttons do. The attack clips that carry one:

| clip | what reaches it | guard (judge, clip frames) | hit |
|---|---|---|---|
| a057_030000 / 030010 / 030020 / 030030 | thrusting shield R1 #1-#4 | 461 f14-45 / f0-7, f16-49 / f16-45 / f0-8, f18-55 | f13-16 / f15-19 / f14-18 / f16-20 |
| a057_030200 / 030300 / 030400 / 030700 | its running, rolling, backstep R1, guard counter | 461 f18-48 / f14-41 / f0-16, f22-59 / f22-55 | f17-20 / f14-16 / f22-25 / f20-24 |
| a057_032000 .. 032030, 032200 .. 032700 | the same, two-handed | 471 R1 #1 f14-55 (hit f13-17), ... | |
| a057_031030 .. 031272, 033030 .. 033282 | its jump attacks, both grips | 461 / 471 from the hit (f13-16) to the landing | |
| a057_032900 | `AttackBothLightGuard`: R1 with the thrusting shield two-handed, guarding, L1 held | **470 f0-42** | judge 200 (the 2H R1 #1's) f15-19 |
| a027 / a036 / a037 / a039 / a207 _030900 | `AttackRightWhileGuard`: R1 with a rapier, spear, great spear or heavy thrusting sword, guarding with the left weapon, L1 held | **460 f0-60 / f0-65 / f0-60 / f0-37 / f0-60** | judge 170 f11-14 / f12-16 / f16-19 / f13-16 / f16-19 |
| a057_030500, 030510, 032500, 032510 | its charged R2s | 460 / 470 f10-37 .. f10-41 | f47-53 |
| a113_030500 / 030510 (and 2H 032500 / 032510) | another category's charged R2s | 460 / 470 f0-45 / f0-31 | f52-55 / f38-41 |
| a690 / a959 (Shield Bash, Roaring Bash) | skill | 460 f10-55 | f34-42 / f34-45 |
| a698 / a924 (Shield Crash, Moore's Charge) | skill | 460 f25-105 | five hits f41-83 |
| a796 (Golden Retaliation), a952 (Revenge of the Night) | skill | 470/460 f5-36; f24-43 | bullets f5-8, f12-15; f24-30 |
| a801 / a802 (Flame Spit, Tongues of Fire) | skill | 461 f0-4; f0-35 | breath f4-7; f0-30 |
| a047 / a048 / a049 030200, 030400 (and 2H) | a shield's own running and backstep R1 | 460 / 470 within f0-18 | f15-23 |

So a thrusting shield's ordinary attacks guard from the hit frame through the recovery, not in
the windup: R1 #1 hits f13-16 and guards f14-45, past its roll cancel (f27). Only two states hold
the guard over the windup, and they are the ones the behavior script names for it.

**Reaching them** (**VERIFIED**, c0000.hks 1.17.2 decompile `GetAttackRequest` / `ExecAttack`,
behavior graph 1.17.1 via `er-behbnd-attack-map.py`):

- `AttackBothLightGuard` (032900): `ATTACK_REQUEST_BOTH_LIGHT` while `is_guard`, `env(1108,
  ACTION_ARM_L1) > 0` and `env(225, atk_hand) == WEAPON_CATEGORY_DUELING_SHIELD` (57). A
  two-handed thrusting shield guards on L1 (`WeaponCategoryID` {57, TRUE, TRUE}), so R1 while
  holding L1 is this poke.
- `AttackRightWhileGuard` (030900, 030901, 030904 by `IndexAttackWhileGuard`: 3 for a thrusting
  shield in the left hand, 2 for a torch, else the left weapon's guard style):
  `ATTACK_REQUEST_ATTACK_WHILE_GUARD`, returned for R1 while `is_guard` and L1 held when the
  right weapon is `WEAPON_CATEGORY_SPEAR` (36), `RAPIER` (27), `LARGE_SPEAR` (37) or
  `LARGE_RAPIER` (39). Its `_Activate` sets the attack hand right and the guard hand left.

**Which guard blocks.** `ExecAttack` sets the guard hand: the attacking hand by default (a
thrusting shield's R1 guards with itself), the attacking hand two-handed, the left hand for
`AttackRightWhileGuard`, the skill's hand for a skill (**VERIFIED**). A thrusting shield guards
like a medium shield (`guardLevel` 3, `guardBaseRepel` 10): Carian Thrusting Shield +25 cuts
physical 94, magic 64, fire 36, lightning 34, holy 35, guard boost 75.0 at 461 (its
`guardStaminaCutRate` +5) and 71.4 at 460/470; Dueling Shield 88/44/39/55/53; Ritual 82/42/35/52/50
(**REGULATION**, `shield_guard`).

**What a guarded hit does to the attack** (**VERIFIED**, `ExecDamage` / `ExecPassiveAction` /
`AttackCommonFunction` / `ArtsCommonFunction`): it takes the guard branch (`DAMAGE_TYPE_GUARD`),
never a stagger. From a state whose passive action passes `is_attackwhileguard` FALSE (every R1
state, every skill state) it plays `W_GuardDamageSmall` / `Middle` / `Large` (all body, by the
table above), which ends the attack. `AttackCommonFunction` passes TRUE only for `TO_GUARDON`,
which only `AttackRightWhileGuard_onUpdate` and `AttackBothLightGuard_onUpdate` pass: then
`ExecDamage` returns with no reaction and the attack goes on. A guard break plays `W_GuardBreak`
from any state. The guarded hit's poise damage is multiplied by `1 - saGuardCutRate / 100` of
the guarding weapon (`ApplySuperArmorDamage`, **VERIFIED**, section 3's poise row), and
`saGuardCutRate` is 0 on every thrusting shield, so it still builds in full.

**Against the RL 150 pool's R1s** (**MEASURED** 2026-11-13, `er-mechanics-spells.guard_block`, the
pool's 300 R1 #1 profiles read as their base weapons): through the Carian Thrusting Shield +25
two-handed (470) a pool R1 passes a median 6% of its raw damage (p90 31%, the elemental ones),
takes 7 / 15 / 24 stamina (p10 / 50 / 90, at most 35) of the pool's 146-stamina bar, and draws a
small or middle guard reaction. One R1 never breaks it.

Where the models read it: the punish model's strike (`er-mechanics-spells.strike_guard`,
attack-similarity.md section 2e, ashes-of-war.md section 16a) and the exchange
(`er-mechanics-exchange.exchange` `guard`, exchange.md section 2b).

## 4. Adoption in the planner corpus (MEASURED)

Setup:
- Filters are the ones `er-builds-adoption-gap.py` uses: not PvE, RL agrees with attributes,
  deduplicated on (user, equipped tokens).
- `pvptag` = Strength tag plus Invasions, Duels, Co-op/Gank, 2v2 or Ladder. `str60` = STR 60 or
  more, any tags.
- `equipIndex` 0-2 are Right Hand 1-3 and 3-5 are Left Hand 1-3 (VERIFIED in the planner's own
  bundle `~/.cache/er-build-planner/js`, which maps rh1..rh3 to 0..2 and lh1..lh3 to 3..5).
- A pair counts when a right slot and a left slot pass `can_powerstance`. "RH1 + LH1" counts the
  primary slots only, which is the pair a player holds at load.

| filter | builds (users) | shield in any left slot | medium | great | small | shield in LH1 | powerstance pair, any slots | pair in RH1 + LH1 | left hand empty |
|---|---|---|---|---|---|---|---|---|---|
| Strength + PvP, all RL | 420 (208) | 137 (78) | 51 | 34 | 57 | 94 | 56 (43) | 22 (20) | 72 |
| Strength + PvP, RL 140-160 | 161 (80) | 54 (36) | 13 | 19 | 26 | 33 | 18 (12) | 5 (4) | 24 |
| STR 60+, all RL | 508 (263) | 159 (101) | 61 | 49 | 53 | 99 | 77 (56) | 48 (38) | 94 |

- Shields: a third of Strength PvP builds carry one (137/420). The most common are Twinbird Kite
  Shield 35, Spiralhorn Shield 28, Icon Shield 20, Smithscript Shield 12. Fingerprint Stone
  Shield appears 4 times.
- The left hand is mostly a seal: Frenzied Flame Seal 161, Clawmark Seal 30. Among weapons,
  Cinquedea 25, Hand Axe 25 and Lance 19 lead.
- Powerstance is rare and not colossal. In the Strength + PvP set the pairs by category are:
  - great spear 19 builds (Lance + Lance 15)
  - straight sword 13 (Sword of Light pairs 6)
  - dagger 9
  - colossal weapon 4
  - axe 3
  - colossal sword 1

  In STR 60+ the colossal weapon count is 10 (8 users).

## 5. Tool

```
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py dual Greatsword Greatsword
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py offhand "Erdsteel Dagger"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py block Giant-Crusher --grip both --shield "Brass Shield" --shield "Fingerprint Stone Shield"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py block Greatsword Greatsword --attacks dual --blocker-stamina 150
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py adoption --filter pvptag --rl 140-160
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-attack-map.py ~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/c0000-behbnd-dcx --json > /tmp/beh.json   # about 2-3 min, background it
ER_BEHBND_JSON=/tmp/beh.json python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py --selftest
```

Functions for the PvP ranking (plain dicts):
- `powerstance_rows(reg, right_id, left_id, level)` and `offhand_rows(reg, weapon_id, level)`.
  Rows carry `hits` (per-hit hand, weapon, judge, MV per element, poise, stamina cost and
  damage, `atk_attribute`, frames), sums, first/last hit, `next_l1`, `dodge` and hyperarmor.
- `can_powerstance(reg, right, left, two_handing)`.
- `shield_guard(reg, shield_id, level, guard_judge, two_handed)`; the judge defaults to 460
  one-handed, 470 two-handed.
- `block_hit(reg, hit, ar, guard, strength, pvp, stamina_rate)`, which returns `repelled`,
  `stamina_to_blocker` (with the attacker's `staminaAttackRate` product `stamina_rate`),
  `chip_fraction` per element with the guard-cut cancel in (apply after defense),
  `chip_hp_rate` (0.5 on a repel), `guard_cut_cancel`, the blocker's `reaction` and `chip_raw`.
- `guard_cut_cancel(reg, hit)`, `guarded_damage_level(reg, hit)`, `guard_reaction(guardLevel,
  level)`, `blocker_ready(reaction)` and `repel_punishes(reaction, startup)`.
- `block_matrix(reg, attack_rows, shields, ar, strength, blocker_stamina, pvp, stamina_rate)`.
- `adoption(rl_lo, rl_hi, filter)`.

`reg = Tables()` extends `er-mechanics-attacks.Regulation`. AR comes from
`weapon_ar(name, level, stats)`, one-handed unless `two_handed=True`.

Corpus blockers (section 8):

```
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py blockers --rl 140-160
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-powerstance-guard.py pressure Giant-Crusher --grip both --rl 140-160 --stats str=66,dex=18,int=9,fth=14,arc=9
python3 /home/banon/projects/er-mods-rs/scripts/er-hks-weapon-category-table.py
```

Both corpus modes take 30-60 s (the opening-hit table resolves about 300 weapons' R1), so run
them in the background.

- `blocker_corpus(mirror, rl_lo, rl_hi)`: the builds `er-builds-pvp.pvp_corpus` keeps, whole.
- `Blockers(reg, builds, curve)`: `.guard_of(build)`, `.meets(wid, stats, two)`,
  `.distribution()`, `.slot_pressure(parts, block_rate, cycle)`, `.opening_hits(builds)`,
  `.startups()`, `.own_guard(guard, opening, stamina, startup)`, `.one_hand_guard_mean(opening)`,
  `.shield_options()`, `.best_left_shield(stats, opening, startup)`, `.carry_rates()`,
  `.carry_rate(wep_type)`, `.carried_left_shield(stats, opening, wep_type, startup)`.
- `guard_score_factor(pressure, own, own_ref)`.

## 6. Selftest

| check | reference |
|---|---|
| `AttackDualLight1` plays 034000, `RideAttack_R_Top` 038000, `AttackLeftLight1` 035000 | TAE `c0000.behbnd` (needs `ER_BEHBND_JSON`) |
| a26/a31 034000: judge 800 Source 1, 805 Source 2 | TAE a26.tae, a31.tae |
| dagger off-hand L1 #1 resolves to `Default - Dagger - Left 1H Light #1` | COMMUNITY Smithbox row names |
| `POWERSTANCE_CATEGORIES` equals the `IsEnableDualWielding` list | COMMUNITY Smithbox `c0000.hks` + `LUA/Enums.txt` |
| 0.9, 0.7, 1.0, 100.0, -5.0, -10.0 at their VAs; `F3 0F 58 D6` at 0x140684735 | EXE `eldenring-deobf.bin` (1.16.2) |
| `overStrength` 99 on every non-ammunition row; Wakizashi the only `spAtkcategory` 104 | regulation |
| Greatsword pairs with Greatsword, not with Giant-Crusher | COMMUNITY |
| direction checks: Giant-Crusher R1 into Brass drains more than Longsword R1 into Fingerprint; Longsword R1 (60) bounces off a greatshield (70); Giant-Crusher R1 (80) does not | sanity only, no outside number |
| `GUARD_LEFT_ONE_HAND` and `GUARD_REFUSED_TWO_HAND` equal `WeaponCategoryID` columns 2 and 3 | EXE-side data: `common_define.hks` bytecode via `scripts/er-hks-weapon-category-table.py` |
| `blocker_corpus` selects exactly `er-builds-pvp.pvp_corpus`'s builds at RL 140-160 | `er-builds-pvp.py` |
| synthetic blockers: guard kinds; `slot_pressure` stamina equals `block_matrix`; a 100% physical shield passes no physical chip; block rate 0 gives factor 1 | `block_hit`, regulation |
| direction checks: a 2H Greatsword R1 drains more and scores more guard pressure than a Dagger R1; a Dagger R1 bounces off Brass; a 2H Greatsword guard stops more than a 2H Dagger guard | sanity only |
| `staminaAttackRate` of 320700 (1.4), 1691/1693 (3.0), 1701/1703 (4.0), 1821 (1.5), 1826 (1.2) | regulation |
| Greatsword R1 into Brass 45 / 4 hits, 63 / 3 with Hammer; Giant-Crusher 2H R1 78 -> 109; a repelled Dagger R1 still costs 17 | the section 3 table |
| 88 weapon rows at guardCutCancelRate -50, 168 nonzero attack rows; Shotel R1 into Brass passes 50% (gcc 0.5); repel chip 0.5 | regulation |
| guard reaction table at 0x143b3b0d0 (2.7.2.0, the target of the `LEA RAX` at 0x14068821a; 1.16.2 had it at 0x143b340b0); `DAMAGE_LEVEL_REMAP` equals frame-advantage's | EXE image (`ER_DEOBF_BIN`, default `eldenring-deobf-1.17.2.bin`), `er-mechanics-frame-advantage.py` |
| no regen inside a short cycle, less drain over a long one; a punishable repel raises own guard; x4 drains more than x1 | definition / sanity only |
| equip load: Longsword + Brass Shield burden in single precision; 50% / 80% / 120% of max load give medium / heavy / overloaded and 1.0 / 0.8 / 0.7; no max load or an unknown armor name give -1 and 1.0; a heavy blocker regenerates 0.8 of a medium one; `equip_load`'s burden less the heaviest right weapon equals `er-builds-optimize.other_load` on every RL 140-160 row both read | REGULATION, resources.md section 5, `er-builds-optimize.py` |
| guard-reaction clips 019200/019210/019220 in a000, a002, a003, a010, a012, a013, a015: JumpTable 22 from f16 / f32 / f53, TAE 225 0% to f10 / f27 / f49; `GUARD_REACTION_REGEN_PAUSE` is the later of the two | TAE |
| `GuardDamage{Small,Middle,Large}_onUpdate` call `act(110)` then `DamageCommonFunction(TO_GUARDON, ...)`; it calls `ExecGuardOnCancelTiming`, whose first call is `env(243)`; the full list of `act(110)` callers | compiled 1.17.2 `c0000.hks` (`ER_HKS_FILE`), `er-hks-disasm.py` |
| the tick's bit-0x40 test and `mulss [rdi+0xe8]`; `Reset` zeroes `actionFlags`; the modifier writer's calls and its 3 -> 0.8, 4 -> 0.7, else 1.0; env 243 -> `GUARD` cancel bit + L1 held; JumpTable 22 sets bit 0x18 | EXE `eldenring-deobf-1.17.2.bin` |
| synthetic carry rates: the pooled share skips two-handers, 2 of 2 shrinks to 7/12 and 0 of 2 to 5/12 with a 0.5 pool, an unseen class gets the pool, carried own guard = best shield's x p | definition |
| RL 140-160 pooled shield-carry rate inside 12.9-17.8% | MEASURED corpus (calibration, 121 of 797) |

## 7. Not established

Settled since the first write-up (guard dig, 2026-10-01, static 1.16.2, key sites byte-matched in
1.17.1; section 3 has each): the guard judge (460 one-handed, 470 two-handed, 461 only
thrusting-shield attacks and two skills, 480 the parry skills); info+0x5c is the guard-cut
cancel; info+0x28 is the stamina damage with the attacker's `staminaAttackRate` in; the repel's
HP cut (0.5) and that a repel still charges stamina; the +0x25a override does not exist for
players (slot 6 returns 0); guard regeneration is 20%, per second of game time (exchange.md
section 3b); the guarded poise factor, `1 - saGuardCutRate / 100` (section 3, 2026-11-13); the
equip-load factor on a blocker's regeneration and how long a guard reaction stops it (both
2026-11-13, the next paragraphs).

**Equip load (VERIFIED 2.7.2.0, `eldenring-deobf-1.17.2.bin`; the 1.16.2 named dump has the same
block in `FUN_140660120`).** The regeneration tick (`FUN_140401ac0`) multiplies its rate by
`staminaRecoveryModifier` (`ChrIns+0xe8`): `mulss xmm6, [rdi+0xe8]` at 0x140401b81, xmm6 holding
`SPRegenRatePercent` / 100, and the product times `GetStaminaRecoverySpeed` (vtable +0x330, 45 + the
SpEffect sum) at 0x140401b95. So the factor scales the turtle adds too. The `PlayerIns` update
writes it every 1.0 s (timer `ChrIns+0x5d4`) for the main player or an NPC: burden
`CalculateEquipmentWeight` 0x140247b80 (called at 0x140661956), max load 0x14065ed20
(0x140661961), weight type 0x14068d8f0 (0x140661979: `CalculatePlayerWeight` 0x14068d0a0 and a
tail jump to `GetWeightType` 0x14068d930). Then `sub r15d, 3` at 0x1406619eb picks 0.8 (0x1429ccbc8)
for type 3 (heavy), 0.7 (0x1432a3bec) for 4 (overloaded) and 1.0 (0x1432a3bf8) for any other, and
0x140661a2d stores it. The selftest reads each of these sites. A blocker's stamina is taken to run on
its own client, where it is the main player (INFERRED). How the model reads each blocker's tier is
in section 8.

**The guard reaction's regeneration stop (VERIFIED code, TAE frames).**
- Every update of `GuardDamageSmall` / `Middle` / `Large` calls `act(110)` first (pc 3 of each
  `_onUpdate`, the 1.17.2 compiled `c0000.hks`, `er-hks-disasm.py --calls-to act`; its other
  callers are `AddStamina`, the three `_JustGuard` updates, `SpeedUpdate` and
  `RideMoveStaminaConsume`, and no `GuardOn` / `GuardStart` state).
- `act(110)` sets `actionFlags` bit 0x40, which the tick tests before it adds anything (0x140401b66),
  and `ChrCtrlModifierData::Reset` zeroes `actionFlags` every frame (0x1403c3ba2). So a guard
  reaction regenerates nothing for as long as its state lasts, whatever its clip's TAE 225 says.
- The state then calls `DamageCommonFunction(TO_GUARDON, ...)`, which ends in
  `ExecGuardOnCancelTiming`. Its first test is `env(243)` (pc 3).
- `HksEnv` sends 243 through byte table 0x140413964 (index 243 - 0xdf) and dword table 0x140413728
  to 0x14041151d. That case is true when bit 0x18 (`GUARD`) of the action-request module's
  `possibleActionCancels` (+0xa0) is set (0x140407c20, `bt` at 0x140407c47) and
  `GetActionDuration(ACTION_ARM_L1 = 2)` (0x140407840) is above 0, that is while L1 is held.
- TAE JumpTable 22 sets that bit: `_ChrActionFlag` case 22 (0x14042810c) passes `edx = 0x18` to
  `SetAllowedCancelToActionState` 0x1404080b0, which ORs it into +0xa0. `ExecGuard` then plays
  `W_GuardOn` while L1 is held and stamina is above 0.
- The 1.16.2 named dump has the same code (`HksEnv` 0x140410820 inner case 0xb,
  `_ChrActionFlag` 0x1404275e0 case 0x16, `FUN_140407660`, `GetActionDuration` 0x140407280).

So a blocker holding L1 regenerates nothing until its reaction clip's first JumpTable 22 frame.
Every guard style that has the clips (a000, a002, a003, a010, a012, a013, a015) has the same
frames, no 608 play speed and no early (300) event on id 22 (TAE, `guard_reaction_windows`,
selftest):

| reaction | clip | TAE 225 0% | JumpTable 22 from | pause before / now |
|---|---|---|---|---|
| small | 019200 | f0-10 | f16 | 10 / 16 |
| middle | 019210 | f0-27 | f32 | 27 / 32 |
| large | 019220 | f0-49 | f53 | 49 / 53 |

The cancel comes after the 0% span in all three, so the pause is the cancel frame whether or not
the reaction clip's own TAE 225 would still apply after the change of state. Taking the transition on
the window's first frame is INFERRED, as attacks.md section 4 takes every cancel: the HKS update that
sees the bit calls `act(110)` first, so regeneration may start one frame later. A guard break is
different: no `act(110)` in its state, so its pause is its TAE 225 0% span f0-50, then 100% until the
guard is raised again (`GUARD_BREAK_REGEN_PAUSE`, not used by `slot_pressure`).

- `info+0x1fc` (multiplies info+0x28 before the guard). Its only store found is 1.0 in
  `InitDamageStruct` 0x140528200; a writer through `memcpy` or another base cannot be ruled out,
  so 1.0 is INFERRED.
- What sets AttackInfo+0xf4, the bypass that makes info+0x5c 1.0.
- The durability-condition multiplier's values for OK/AtRisk/Broken. It is taken as 1.0 at full
  durability.
- Which repel the attacker plays: `W_Repelled_Small` (31910/33910/35910, roll and R1 f22) or
  `W_Repelled_Large` (31920/..., roll f27, R1 f30), chosen by the damage level the attacker
  receives, which was not traced. The tool uses the small one. Also whether a pair's second hit
  still lands after the first is repelled.
- The guard-break stagger's punish value: its clip gates are known (roll f43, R1 f55), who can
  hit in them was not measured.
- A guard's own time to become effective after the L1 press (combo.md).
- Guarded status build-up (`poisonGuardResist` etc.): not traced.
- Stamina cost per powerstance swing. The tool sums every distinct (AttackIndex, judge). That
  `FUN_1404428f0` charges each event is VERIFIED; that its early return never suppresses a
  charge here is INFERRED.
- Whether poise damage from the two hits of one swing stacks on the defender (each hit subtracts
  on its own; the refill timer was not read).
- Whether categories 60-62 (light greatsword, great katana, beast claw) can powerstance (env 225
  is resolved above; the open part is the HKS list). That the decompiled HKS matches the
  1.17.1 compiled script beyond its identifiers is also unverified.
- `AttackBothLeft1..3` (two-handing the left weapon) is also mapped to clips 034000..034020 by
  `er-behbnd-attack-map.py`. Whether that is the same clip or an id the behavior graph offsets
  per state was not checked.
- Whether live combat clears the 2H STR flag for `isDualBlade` weapons (only the menu paths do,
  VERIFIED).
- Mounted attacks (600-7xx) are placed but not tabled.
- "Hits to break" in section 3 assumes no regeneration between hits. The rate is 20% of 45/s
  while guarding, x 0.8 at heavy load and x 0.7 overloaded, and nothing through a guard reaction
  until the guard comes back (f16 / f32 / f53, above). A Greatsword R1 chain (about 38 frames per
  swing, small reaction) gives back about 6.6 stamina per 22-frame gap at medium load (7.8 for a
  Green Turtle blocker's 20% of 53/s), which does not change its 4 hits into Brass. A blocker's own
  regen items are read (talismans, a held Great Turtle Shell; section 8, `drain`); a buff's
  (Braggart's Roar) is not.
- Everything section 8 lists as INFERRED.

## 8. Corpus blockers and guard pressure

Built for `er-builds-pvp.py --sort score`, which scores each attack slot against the PvP builds of
an RL window. This section is the guard side of that corpus.

### Who can raise a guard, and with what

The rule comes from the behavior script:

- Guard hand (COMMUNITY decompile, `Guard_Activate`, `IsEnableGuard`): the left hand, or the
  right hand while `HAND_RIGHT_BOTH`.
- Eligibility is `IsWeaponCanGuard`, which looks the guard hand's category up in
  `WeaponCategoryID`. Column 2 applies while one-handing, column 3 while two-handing, and only an
  explicit `FALSE` refuses. A powerstance pair also refuses.
- `WeaponCategoryID` is VERIFIED. It was read out of the compiled
  `action/script/common_define.hks` by `scripts/er-hks-weapon-category-table.py`, and
  `TRUE`/`FALSE` are plain 1/0 globals in the same file (bd
  `hks-weaponcategoryid-guard-table-2026-09-29`).
  - One-handed: only torch 21 and shields 47, 48, 49 and 57 guard from the left hand. A bare
    left hand is category 42 `FIST` (VERIFIED regulation row 110000), and it cannot guard. Neither
    can a seal or staff (both 41) or any melee weapon.
  - Two-handed: everything guards except bow 44, greatbow 45, crossbow 46, light bow 51 and
    ballista 52.
- That the category is `wepmotionCategory` is INFERRED, as in section 2.

Per build (`Blockers.guard_of`):

- The guard weapon: LH1 of the active set, or RH1 when the planner's `is2h` is set.
- That `is2h` describes how the build fights and guards is INFERRED. It is the planner's AR
  toggle.
- Affinity and level: the slot's `infusion`, and `upgrade`, falling back to the build's
  `weaponUpgrade`. Somber weapons use +n = smithing +floor(2.5 n) (INFERRED).
- Stat requirements: `FUN_14068c3c0` and `FUN_140684540` drop repel and guard boost to 0 when the
  blocker lacks them (VERIFIED). STR counts x1.5 while two-handing (INFERRED for this check).
- Stamina: the planner's `computed.maxStamina`. Defense and absorption: `computed`, as
  `Defenders` in er-builds-pvp uses them.

RL 140-160, 1074 PvP builds (MEASURED; the selftest checks they are exactly
`er-builds-pvp.pvp_corpus`'s):

| stance | builds |
|---|---|
| bare left hand, one-handed: cannot guard | 268 |
| two-handed weapon | 261 |
| left catalyst: cannot guard | 211 |
| left melee weapon: cannot guard | 180 |
| shield, one-handed | 121 |
| two-handed shield | 11 |
| left bow: cannot guard | 9 |
| torch, one-handed | 7 |
| two-handed bow or crossbow: cannot guard | 4 |
| unmatched name | 2 |

- 37% of the window can raise a guard in its loaded stance: 261 + 121 + 11 + 7 = 400. The 261
  include 17 builds two-handing an empty right hand, which guard with the fist. The most common guards are 2H Heavy / Cold greatswords, Spiralhorn
  Shield, Icon Shield, Brass Shield and Banished Knight's Shield.
- Over the builds that can guard, the medians are guard boost 49, physical guard cut 70 and max
  stamina 147. 9 of them miss their guard's stat requirement.

### One slot against the distribution (`Blockers.slot_pressure`)

Each hit of the slot goes through `block_hit` (section 3) against every guard: stamina with the
attacker's `staminaAttackRate` (the ranking passes Hammer Talisman from `--talismans` and the
buff kits' uptime-weighted product, skill buffs included), the PvP rate and the 2H factor;
repel; the pass share per element with the attacker's guard-cut cancel; and the guard reaction.
Chip is the unguarded per-element damage times the pass share, halved on a repel.
`CalculateDamageBasic` 0x1406849d0 multiplies `victimGuardDefRate` into the same product as
armor absorption, the counter rate and the damage-cut SpEffects (VERIFIED), so chip is
`er-builds-pvp`'s own damage array x the pass share.

Per blocker:
- `drain` = (stamina lost - regeneration over the slot's cycle) / max stamina, capped at 1. The
  cycle is the slot's same-button recovery frame (the ranking passes it); the guard regenerates
  20% of (45 + its own add)/s x its own `staminaRecoveryModifier` over the frames of it past the
  frame its guard comes back out of the reaction, 16 / 32 / 53 (`guard_regen_per_frame`,
  `Blockers.regen_add`, `Blockers.recovery`, `GUARD_REACTION_REGEN_PAUSE`; section 7 has the code
  for both). The add is read off each corpus build's own
  equipment (`Blockers.equipment_regen_add`): its active talisman set (`worn_talismans`,
  `er-mechanics-talismans.stamina_regen_add`: Green Turtle 8, Two-Headed Turtle 10) and the two
  weapons it holds (`equipment_regen_adds`: the Great Turtle Shell, every affinity, resident SpEffect
  5311400, +3). Which weapons apply their resident SpEffects is VERIFIED on 2.7.2.0
  (`eldenring-deobf-1.17.2.bin`, names from the 1.16.2 dump): only the selected slot of each hand,
  whatever the grip. `PlayerIns::InitializeSpEffects` builds a `WepSpEffectEquipCtrl` on the
  player's ChrAsm with 4 entries (size call 0x140502450). The table at vtable 0x142a4f8b8 + 0x70
  maps them to ChrAsm special indices {-2, -1, -2, -1}; the last two are the Ash of War on each
  weapon. `GetParamId` 0x1405027c0 resolves each one with `ChrAsm::GetBySpecialIndex`
  0x1403be440: -2 gives the left hand's selected index x 2 and -1 gives the right hand's x 2 + 1.
  The selected index comes from `getSelectedWeaponSlotIndex` 0x1404c55c0, which returns
  `equipment[n + 1]` and never reads `equipment[0]`, the arm style. Other ChrAsm readers do check
  the arm style (the 1.16.2 `isDarkHand` getter `FUN_1403bfbc0`), so this path ignores grip on
  purpose. Two-handing therefore keeps the other hand's effect, a shield on the back included,
  and the other two slots of a hand apply nothing. The 1.16.2 scan of all 205 functions that call
  `EquipParamWeapon::GetEntry` found the resident fields (row +0x50/+0x54/+0x58) read only by this
  control and by the equip menu's status preview (`FUN_1407cac70`). The preview uses the same
  `GetBySpecialIndex(-2/-1)` test. The planner stores no drawn slot per hand, so the model reads
  positions 0 and 3, each hand's first slot (an INFERRED assumption about which slot the build
  fights with). No armor row carries one (REGULATION scan), and no buff is applied to a blocker (Braggart's
  Roar +10, Flame, Grant Me Strength +5 and the physick's Greenburst +15 are not read). A build
  with none of these regenerates 45/s: a regen item is a choice, never a default. Without a cycle,
  none.

  MEASURED 2026-11-13 (RL 140-160 corpus, 1,008 builds, 371 can raise a guard), a usage fact about
  the pool and no reason for anything: 114 builds (11.3%) carry a regen item, 91 the Two-Headed
  Turtle, 22 the Green Turtle and 1 the Great Turtle Shell; of the 371 that can guard, 46 (12.4%),
  37 at +10 and 9 at +8 (the shell's holder cannot). `slot_pressure` with the CLI's cycle (the
  same-button recovery), attacker +25 Standard STR 66 DEX 18 FTH 14, before (45/s for every blocker,
  `.worktrees/sim-interrupt`'s code) and after (each blocker's own add):

  | attack | cycle | regen before / after | drain before / after | factor before / after |
  |---|---|---|---|---|
  | Greatsword 2H R1 | 32.7 | 2.205 / 2.267 | 0.70335 / 0.70295 | 0.98458 / 0.98455 |
  | Greatsword 2H charged R2 | 70.2 | 13.455 / 13.814 | 0.93715 / 0.93640 | 0.99497 / 0.99490 |
  | Giant-Crusher 2H R1 | 30.3 | 1.485 / 1.528 | 0.87387 / 0.87364 | 0.99196 / 0.99194 |
  | Dagger R1 | 14 | 1.200 / 1.232 | 0.23647 / 0.23626 | 0.94726 / 0.94724 |
  | Carian Thrusting Shield R1 | 22 | 3.600 / 3.695 | 0.57980 / 0.57915 | 0.97430 / 0.97425 |
  | Longsword R1 #1 | 17 | 2.100 / 2.156 | 0.35400 / 0.35362 | 0.96134 / 0.96131 |

  The add moves the mean regeneration by 2-3% (an eighth of the blockers regenerating 18-22%
  faster) and the factor in the fifth decimal: the guard reaction's 0% pause leaves few frames of
  each cycle to regenerate in. (These "after" numbers predate the equip-load factor and the
  guard-return pause below.)

  **Equip load (2026-11-13).** Each blocker's tier is read off what its row holds
  (`Blockers.equip_load`, `load_type`): burden = `er-mechanics-resources.Model.burden`
  (`CalculateEquipmentWeight`, single precision) of every weapon in the active set's six positions
  (`EquipParamWeapon.weight` of the name's base row), the active armor set (`worn_protectors`) and
  the active talisman set; max load = the planner's `computed.maxEquipLoad`, which the game model
  reproduces on 99.45% of the corpus (resources.md section 8); the tier is `Model.weight_type`
  (`GetWeightType`, strict at 0.3, 0.7 and 1.0, resources.md section 5). The burden reads the same
  slots `er-builds-optimize.other_load` reads (the selftest checks they agree on every row both read).
  `Blockers.recovery` is then 0.8 at heavy, 0.7 overloaded, else 1.0. A row the model cannot read
  keeps 1.0, the value every blocker had before.

  MEASURED 2026-11-13, RL 140-160 corpus, a usage fact about the pool and no reason for anything:

  | tier | all 1,008 builds | the 371 that can guard |
  |---|---|---|
  | light (1) | 100 | 19 |
  | medium (2) | 856 | 329 |
  | heavy (3), x0.8 | 37 | 17 |
  | overloaded (4), x0.7 | 6 | 4 |
  | not read, x1.0 | 9 | 2 |

  The 9 unread rows: 8 store two weapons at one position of the active set, which no character can
  hold, so the six weapons the game would sum cannot be told apart; 1 names a talisman the
  regulation does not resolve. The blockers' mean modifier is 0.988.

  **Before / after both changes** (`slot_pressure` with the CLI's cycle, attacker +25 Standard STR
  66 DEX 18 FTH 14; before = this file's previous code, every blocker x1.0 and paused 10 / 27 / 49;
  each change alone in the middle columns):

  | attack | cycle | reaction over the 371 | regen before / load only / pause only / after | drain before / after | factor before / after |
  |---|---|---|---|---|---|
  | Greatsword 2H R1 | 32.7 | 335 middle, 36 small | 2.267 / 2.227 / 0.697 / 0.677 | 0.70294 / 0.71159 | 0.98455 / 0.98502 |
  | Carian Thrusting Shield R1 | 22 | 371 small | 3.695 / 3.650 / 1.848 / 1.825 | 0.57915 / 0.59101 | 0.97425 / 0.97497 |
  | Dagger R1 | 14 | 371 small | 1.232 / 1.217 / 0 / 0 | 0.23626 / 0.24480 | 0.94724 / 0.94794 |

  The pause is the change that matters: it cuts the mean regeneration by half (the thrusting shield)
  to 70% (the 2H Greatsword, whose middle reaction now leaves 0.7 of its 32.7-frame cycle) to all of
  it (a Dagger R1 recycles at f14, before the guard comes back at f16). The load factor alone takes
  1-2% off. Drain rises 0.85 to 1.19 points; stamina charged and break share are unchanged, since
  neither depends on regeneration; the factor moves in the fourth decimal.
- `broken` when one attack from full stamina reaches max stamina. A repel still charges its
  stamina, so a repelled hit can break.
- `value` of the blocked hit against a landed one: min(1, chip / unguarded + drain), minus
  `SCORE_REPEL_PUNISH` (1.0) when the hit is repelled and the blocker's own R1 #1 (its first hit
  frame from `opening_hits`) plus its R1 frame out of the guard reaction (5 small, 10 middle, 50
  large) lands before f22, when a small-repelled attacker can roll. Counting a full drain as one
  landed hit and a punish as one landed hit back are INFERRED.

The factor is:

```
factor = 1 - SCORE_GUARD_BLOCK_RATE * mean over all builds of [can guard] * (1 - value)
```

`SCORE_GUARD_BLOCK_RATE` = 0.25 is INFERRED: the share of hits that meet a raised guard when the
defender has one. Nothing in the corpus measures it.

### The defensive side (`Blockers.own_guard`)

- The incoming hit is each window build's RH1 R1 #1, as it holds it: 2H when `is2h` is set.
- A guard's `own_guard` is the mean over those hits of 1 - min(1, physical pass share + stamina
  lost / median max stamina), the pass share with the attacker's guard-cut cancel and halved on
  a repel, plus `SCORE_REPEL_PUNISH` on a repel the configuration's own R1 #1 punishes (the rule
  above). It can exceed 1.
- A two-handed configuration guards with its own weapon (HKS, above).
- A one-handed configuration guards with its left hand, which the sweep does not choose. The
  shield is the corpus shield (`shield_options`: every one-handed shield the window carries, at
  the highest level seen) whose requirements it meets one-handed and whose `own_guard` is highest
  (`best_left_shield`), and its own guard is that shield's times p, the share of the window's
  one-handing builds whose right hand is of the same `wepType` and who carry a shield in the
  active left hand at all (`carried_left_shield`, `carry_rates`; MEASURED). p is shrunk toward the
  pooled share with 10 pseudo-builds (the strength is INFERRED). At RL 140-160 the pool is 121 of
  797 (15.2%, 95% CI 12.9-17.8%); the raw class shares run from 4% (katana, 28 builds) through
  16% (straight sword, 38) and 20% (colossal sword, 113) to 34% (greatsword, 56). A one-hander without a shield cannot guard and counts as 0 in the reference too, so
  this is the class's expected own guard. A one-handed row that pairs a weapon in the left hand
  (`--paired-offhand`, `--paired-loop`, `--setup`) cannot guard, so its own guard is 0.
- The reference is `one_hand_guard_mean`, the one-handing builds' own guards, each with its own
  R1 as the punish: 0.098. It is low because 670 of the 798 one-handing builds (84%, the 2
  unmatched names counted as no guard) cannot guard at all.
- The term is `1 + SCORE_GUARD_OWN_WEIGHT * (own - one_hand_guard_mean)`, with weight 0.1
  (INFERRED). Before 2026-10-01 a one-handed configuration got 1 here, so a shield never counted.

### Calibration against adoption (`scripts/er-builds-guard-adoption.py`, RL 150, 2026-10-01)

The first version of the term gave every one-handed row its best shield outright. Rescoring that
ranking post hoc (it reproduces the stored scores to 4.5e-6) against corpus adoption, with the
within-class percentile coefficient and a grip test (Spearman of log 1H/2H score against the
corpus one-handed share, 51 weapons), paired bootstrap of 2000:

- The best-shield assumption overstated the one-handed own guard about sixfold (15% carry, and
  the top-50 one-handed weapons are carried with a shield in 25 of their 168 corpus builds). It
  moved the one-handed rows in the top 50 from 15 to 34; with the carry rate it is 19.
- `SCORE_GUARD_OWN_WEIGHT` is not pinned. With the carry rate every weight from 0 to 0.4 gives a
  percentile coefficient of 0.55-0.57 with overlapping CIs (0.511 before the guard model). Under
  best shield 0.4 was significantly worse than 0 (-0.128, CI -0.256 to -0.023 at P = 0), which
  is the only weight the data excludes, and it excludes it under the assumption replaced here.
- `SCORE_REPEL_PUNISH` is not pinned: every P from 0 to 1 is indistinguishable in both modes.
- The grip test is about +0.2 (CI -0.1 to +0.5) for every candidate: adoption cannot tell the
  guard variants apart on grip.

So both weights stay at 0.1 and 1 as INFERRED choices; the carry rate is the measured part.

### Examples (RL 140-160, attacker +25 Standard unless named, CLI damage without PvP weapon rate or grease)

| attack | stam | drain | break from full | bounce | punish | chip share | value | pressure x own = factor |
|---|---|---|---|---|---|---|---|---|
| Giant-Crusher 2H R1 #1 (STR 66) | 158 | 88% | 55% | 0% | 0% | 31% | 0.92 | 1.040 |
| Giant-Crusher 2H charged R2 #1 | 369 | 99% | 98% | 0% | 0% | 31% | 0.99 | 1.048 |
| Heavy Greatsword 2H R1 #1 (STR 60) | 121 | 76% | 28% | 0% | 0% | 31% | 0.86 | 1.023 |
| Keen Dagger 1H R1 #1 (STR 12, DEX 60) | 38 | 26% | 0% | 20% | 12% | 31% | 0.44 | 0.947 |
| Keen Dagger 1H charged R2 #1 | 77 | 49% | 3% | 0% | 0% | 31% | 0.72 | 0.972 |

Own guard: 2H Giant-Crusher 0.585, 2H Heavy Greatsword 0.469; the Keen Dagger's left hand is
Serpent Crest Shield +25 at 0.868 when carried, and 9.7% of the window's one-handed daggers carry a
shield, so its own guard is 0.084 (against the 0.098 reference). The dagger R1's value fell from
0.53 to 0.44 because 12% of the window now punishes its repel. With the best shield taken as
carried its factor was 1.021; with the carry rate it is 0.947, an own-guard term of 0.9986: a
dagger guards slightly less than the average one-hander.

### Not established here

- `SCORE_GUARD_BLOCK_RATE` and `SCORE_GUARD_OWN_WEIGHT`. Both are modelling weights, not
  measurements, and adoption does not pin the own weight (calibration above).
- Whether the planner's `is2h` is the stance a build fights and guards in.
- `SCORE_REPEL_PUNISH`, and which repel (small or large) the attacker plays; the small one's
  f22 is used, so the punish share is the lower of the two.
- Which shield a one-handed configuration carries when it carries one: the best corpus shield it
  can wield is assumed; only how often it carries one is measured (`carry_rates`).
- What a guard break is worth beyond one landed hit. The guard-break clip's gates are known
  (roll f43, R1 f55); its punish was not measured.
- The guard counter (an R2 out of a small or middle guard reaction) and an exchange where the
  defender raises a guard are not scored. An attacker's own guard during his attack is
  (section 3a): in the punish model's strike and in the exchange.
- Categories 54 and 59 have no `WeaponCategoryID` row, so they would guard (nil is not `FALSE`).
