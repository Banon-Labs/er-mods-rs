# Powerstance, off-hand attacks and blocking

Labels as in `attacks.md`. **VERIFIED** = a regulation value (installed 1.17.1 `regulation.bin`)
or code read out of the executable. EXE addresses are 1.16.2 VAs from the named Ghidra dump on
:8765, identical to `eldenring-deobf.bin` (shift 0). Carry an address through
`scripts/map-rvas-1162-to-1170.py` and `scripts/map-rvas-1170-to-1171.py` before using it
against the running 1.17.1 game. **TAE** = decoded from `c0000.anibnd -> tae/a<cat>.tae` or the
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
- That env 225 `GetEquipWeaponCategory` returns `wepmotionCategory` is INFERRED. The HKS enum
  values 20..52 equal the `wepmotionCategory` numbering exactly: Dagger 20, Longsword 23,
  Greatsword 26, Giant-Crusher 31, shields 47/48/49. The DLC categories 60 (light greatsword),
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
   def >= atk  ->  info+0x264 = repelled, HP damage *= SpEffect flick cut (FUN_1404f7280)
stamina    FUN_140684540 (PlayerIns::CalculateGuardStaminaDepletion 0x140651f50)
   g   = clamp((statBonus + staminaGuardDef * Reinforce.staminaGuardDefRate + 1.0)
               * (1 + guardAtk.guardStaminaCutRate/100) * SpEffect, 0, 100)    0 if stats unmet
   dmg = (1 - g/100) * atkStam * SpEffect + guardBehavior.stamina
   two-handed guard: dmg *= 0.9, or 0.7 when the guard weapon's weaponCategory == 12 (shields)
   both players: dmg *= FinalDamageRateParam[finalDamageRateId].staminaRate (+0x14)
   stamina <= dmg  ->  guard break (info+0x259); reaction 0x3e9 instead of 3
chip       CalculateGuardDamage 0x140689460, per element e
   pass(e) = (100 - (1 + typeCut/100) * (cut_e * Reinforce.cutRate_e + statBonus)
              * durability * (1 + guardAtk.guardRate/100) * info+0x5c) / 100
   HP(e)   = CalculateDefense(e) * pass(e)                     CalculateDamageBasic 0x1406849d0
poise      poise damage *= (1 - saGuardCutRate/100) while guarded   FUN_14047f260
```

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
- **Guard rows are neutral.** The guard behavior is set by TAE JumpTable 3 (`Set Guard Type`,
  ArgB = judge). The judges are 460 and 470 in every weapon and shield TAE read, plus 480 in a
  few. Rows 460/470 carry guardBreakCorrection 100, guardStaminaCutRate 0, guardRate 0 and
  stamina 0 on every weapon and shield checked. Two differ:
  - 480 on small and medium shields: guardBreakCorrection 0, guardRate -50. That means no repel
    and half the guard cut.
  - Greatshield 461: guardStaminaCutRate -40.

  Which judge a plain raised guard uses is not traced. The tool defaults to 460, and 460 and 470
  give the same numbers.
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
  not repelled.
- 100%-physical shields (Brass, Fingerprint and every greatshield in the table) let no physical
  chip through. The Buckler (74%) passes 26% of each physical hit, after defense.
- A powerstance pair costs a blocker exactly the sum of its hits. A Greatsword pair L1 drains
  1.8x what one R1 does, because each hit has its own `atkStam` row (stamina damage 0.9x an R1's
  per hit).

Two-handed blocking: a shield held two-handed takes 0.7x stamina damage, any other weapon 0.9x
(VERIFIED multiply). That `weaponCategory` 12 is the shield set is VERIFIED from the regulation:
every Buckler, Kite and greatshield row reads 12.

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
- `shield_guard(reg, shield_id, level, guard_judge, two_handed)`.
- `block_hit(reg, hit, ar, guard, strength, pvp)`, which returns `repelled`,
  `stamina_to_blocker`, `chip_fraction` per element (apply after defense) and `chip_raw`.
- `block_matrix(reg, attack_rows, shields, ar, strength, blocker_stamina)`.
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
- `Blockers(reg, builds, curve)`: `.guard_of(build)`, `.distribution()`,
  `.slot_pressure(parts)`, `.opening_hits(builds)`, `.own_guard(guard, opening)`,
  `.one_hand_guard_mean(opening)`.
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

## 7. Not established

- Which guard judge (460, 470 or 480) a plain raised guard uses, and what 461 is. Every
  standard row is neutral, so the numbers above do not depend on it. 480 would halve the cut and
  remove the repel.
- `info+0x1fc` (multiplies `atkStam` before the guard) and `info+0x5c` (multiplies the guard
  cut). Both are taken as 1.0.
- That `AttackDamageInfo+0x28` is attacks.md's stamina-damage value. `FUN_14068aa80` has the
  right shape, but its return expression was lost in the decompile.
- The durability-condition multiplier's values for OK/AtRisk/Broken. It is taken as 1.0 at full
  durability.
- The repel outcome: which reaction the attacker plays (`Repelled_Small` 035910 or
  `Repelled_Large` 035920, probably chosen by HKS from `guardLevel`, which `GetGuardLevel`
  0x140655cf0 copies to `actionFlag+0x20`), the flick damage cut `FUN_1404f7280`, and whether a
  pair's second hit still lands after the first is repelled.
- The derived DamageModule override that sets `+0x25a` (the harder guard-break reaction for
  damageLevel 4/6/7/10).
- `ApplySuperArmorDamage`'s factor while guarded (`1 - saGuardCutRate/100` is INFERRED;
  `saGuardCutRate` is 0 on every row read anyway).
- Stamina cost per powerstance swing. The tool sums every distinct (AttackIndex, judge). That
  `FUN_1404428f0` charges each event is VERIFIED; that its early return never suppresses a
  charge here is INFERRED.
- Whether poise damage from the two hits of one swing stacks on the defender (each hit subtracts
  on its own; the refill timer was not read).
- What env 225 `GetEquipWeaponCategory` reads, and so whether categories 60-62 (light
  greatsword, great katana, beast claw) can powerstance. That the decompiled HKS matches the
  1.17.1 compiled script beyond its identifiers is also unverified.
- `AttackBothLeft1..3` (two-handing the left weapon) is also mapped to clips 034000..034020 by
  `er-behbnd-attack-map.py`. Whether that is the same clip or an id the behavior graph offsets
  per state was not checked.
- Whether live combat clears the 2H STR flag for `isDualBlade` weapons (only the menu paths do,
  VERIFIED).
- Mounted attacks (600-7xx) are placed but not tabled.
- Stamina regeneration while guarding. "Hits to break" assumes none.
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
PvP rate and the 2H factor, repel, and the pass share per element. Chip is the unguarded
per-element damage times the pass share. `CalculateDamageBasic` 0x1406849d0 multiplies
`victimGuardDefRate` into the same product as armor absorption, the counter rate and the
damage-cut SpEffects (VERIFIED), so chip is `er-builds-pvp`'s own damage array x the pass share.

Per blocker:
- `drain` = stamina lost / max stamina, capped at 1.
- `broken` when one attack from full stamina reaches max stamina (no regeneration).
- `value` of the blocked hit against a landed one: 0 when it bounces, else min(1, chip /
  unguarded + drain). Counting a full drain (a guard break) as one landed hit is INFERRED.

The factor is:

```
factor = 1 - SCORE_GUARD_BLOCK_RATE * mean over all builds of [can guard] * (1 - value)
```

`SCORE_GUARD_BLOCK_RATE` = 0.25 is INFERRED: the share of hits that meet a raised guard when the
defender has one. Nothing in the corpus measures it.

### The defensive side (`Blockers.own_guard`)

- The incoming hit is each window build's RH1 R1 #1, as it holds it: 2H when `is2h` is set.
- A guard's `own_guard` is the mean over those hits of 1 - min(1, physical pass share + stamina
  lost / median max stamina), and 1 for a hit it bounces.
- A two-handed configuration guards with its own weapon (HKS, above).
- A one-handed configuration guards with whatever its left hand holds, which the sweep does not
  choose. So it is compared with `one_hand_guard_mean`, the one-handing builds' own guards at
  0.087. That is low because 670 of the 798 one-handing builds (84%, the 2 unmatched names counted as no guard) cannot guard at all.
- The term is `1 + SCORE_GUARD_OWN_WEIGHT * (own - one_hand_guard_mean)`, with weight 0.1
  (INFERRED). One-handed configurations get 1.

### Examples (RL 140-160, attacker +25 Standard unless named, CLI damage without PvP weapon rate or grease)

| attack | stam | drain | break from full | bounce | chip share | value | pressure x own = factor |
|---|---|---|---|---|---|---|---|
| Giant-Crusher 2H R1 #1 (STR 66) | 158 | 88% | 55% | 0% | 31% | 0.92 | 1.043 |
| Giant-Crusher 2H charged R2 #1 | 369 | 100% | 98% | 0% | 31% | 1.00 | 1.050 |
| Heavy Greatsword 2H R1 #1 (STR 60) | 121 | 77% | 28% | 0% | 31% | 0.87 | 1.026 |
| Keen Dagger 1H R1 #1 (DEX 60) | 38 | 27% | 0% | 20% | 31% | 0.53 | 0.957 |
| Keen Dagger 1H charged R2 #1 | 77 | 54% | 3% | 0% | 31% | 0.75 | 0.977 |

Own guard: 2H Giant-Crusher stops 59% of the corpus opening hit and 2H Heavy Greatsword 48%.

### Not established here

- `SCORE_GUARD_BLOCK_RATE` and `SCORE_GUARD_OWN_WEIGHT`. Both are modelling weights, not
  measurements.
- Whether the planner's `is2h` is the stance a build fights and guards in.
- What a guard break is worth beyond one landed hit. The riposte-less stagger window was not
  traced.
- Repel outcomes. A bounced hit is valued 0, and whether it still drains stamina or chips is
  not traced (section 7).
- Categories 54 and 59 have no `WeaponCategoryID` row, so they would guard (nil is not `FALSE`).
