# Elden Ring per-attack mechanics for a build optimizer

Labels: **VERIFIED** = a regulation value (installed 1.17.1 `regulation.bin`, read with
`scripts/er-param-read.py`) or code read out of the executable. EXE addresses are 1.16.2
VAs from the named Ghidra dump on :8765, identical to `eldenring-deobf.bin` (shift 0).
Every one of them has been carried to the installed 1.17.1 build; section 9 lists each
address with its 1.17.1 counterpart and how it was found.
**TAE** = decoded from the player TimeAct `c0000.anibnd.dcx -> tae/a<cat>.tae`
(`scripts/er-tae-event-scan.py` parser), or from the player behavior graph `c0000.behbnd`
(`scripts/er-behbnd-attack-map.py`). **INFERRED** = consistent with the data, but the consuming
code was not traced. **COMMUNITY** = an outside claim, such as Smithbox row names or
field descriptions, or a wiki breakpoint. Nothing was launched.

Tool: `python3 scripts/er-mechanics-attacks.py <weapon id|name> [--grip both] [--json]`
lists every attack slot with MV, poise damage, stamina cost, stamina damage, hit windows,
hyperarmor windows, the first frame each follow-up action can start (section 4) and the
clip length. Frames are real time, with the TAE 608 play speed applied; clip-time values sit
beside them as `*_clip` fields. `--selftest` passes 88/88 (section 7).

## 0. The chain from a weapon to its attacks

```
TAE anim a<wepmotionCategory>_<anim>  event type 1 (AttackBehavior 0x1404266d0), Args[2] = judge
  -> PlayerIns::ResolveBehaviorId 0x140652280 (weapon from the hand slot, fists 110000 if flags&0x60)
       kind = judge < 1000 ? 1 : judge / 1000
       row  = (kind*100000 + EquipParamWeapon.behaviorVariationId) * 1000 + judge % 1000
       if row missing: row = (variation/100 + kind*1000) * 100000 + judge % 1000      (family)
       if that is missing and judge >= 1000: row = kind*100000000 + judge % 1000
  -> BehaviorParam_PC[row]  refType 0 -> AtkParam_Pc[refId]; stamina; consumeSA; category
  -> AtkParam_Pc[refId]     motion values, poise, stamina-damage and guard corrections
```

- VERIFIED (EXE): the formula above is the decompile of 0x140652280.
  `IsValidBehaviorJudgeID` for the player (0x1406577b0) is `judge < 10000`. Variations of
  100000 or more resolve to -1. One quirk: for a judge below 1000, the family id is returned
  even when that row is also missing. Only judges of 1000 and above get the third, variation-0
  fallback.
- VERIFIED (regulation): the family fallback carries most weapons. Greatsword (4000000,
  variation 401) has no 100401000 row, so its R1s come from family 400 (`Default - Colossal
  Sword`). Only its R2 rows (401100..401315) are its own. The same holds for Claymore
  (318 -> 300) and Giant-Crusher (2304 -> 2300). Dagger, Longsword and Uchigatana have rows
  of their own for every slot. So an optimizer must implement the fallback and must not read
  `variation*1000+judge` alone.
- VERIFIED (EXE): `BehaviorParam::GetAtkParam` 0x140d240e0 returns an AtkParam only for
  refType 0. Refs of type 1 (bullet) and 2 (SpEffect) are identified only by data distribution
  (see bd `creature-attack-trigger-and-enumeration-join-2026-09-01`).
- TAE: the TAE file is `a<wepmotionCategory>.tae` with tae id `2000 + wepmotionCategory`
  (dagger 20 -> `a20.tae` id 2020). Every weapon of one motion category shares its animations.
  The numbers differ through `behaviorVariationId`.

### Judge-id numbering (TAE + behavior graph)

The judge fired by each animation was read from the TAE. The slot meaning is the
`c0000.behbnd` state that plays that clip. Two-handed = judge + 200 and animation + 2000.

| judge | anim | behavior state | slot |
|---|---|---|---|
| 0,10,20,30,40,50 | 030000..030050 | AttackRightLight1..6 | R1 chain. Length is set by the category: dagger 6, straight sword/katana 5, greatsword 4, colossal 3 |
| 100 / 110 | 030505 / 030515 | AttackRightHeavy1End / 2End | uncharged R2 #1 / #2 |
| 105 / 115 | 030500 / 030510 | AttackRightHeavy1Start / 2Start | fully charged R2 #1 / #2 (Smithbox names these rows `Heavy #1 Max`) |
| 120 / 125 | 030200 / 030210 | AttackRightLightDash / HeavyDash | running R1 / running R2 |
| 130 | 030300 | AttackRightLightStep | rolling R1 |
| 140 | 030400 | AttackRightBackstep | backstep R1 |
| 150 | 031030..031070 | Jump_LandAttack_Normal (031070) | jump R1 |
| 160 | 031230..031270 | Jump_LandAttack_Hard (031270) | jump R2 |
| 180 | 030700 | AttackRight{Heavy,Light}Counter | guard counter |
| 400-450 | 035000..035030 | | off-hand (Smithbox `Left 1H Light #n`) |
| 600-7xx | 0380xx / 0390xx | RideAttack_R_* / RideAttack_L_* | mounted attacks (TAE, `powerstance-guard.md`) |
| 800-895 | 034000..034050, 0342xx..0345xx | AttackDualLight1..6 | powerstance (TAE, `powerstance-guard.md`) |
| 5xx | 031719 etc. | ThrowBackStab / ThrowAtk | backstab (500), riposte (510), crit follow-ups (INFERRED from row names) |
| 3xxx | 0306xx | AttackRightHeavySpecial* | weapon skill: kind 3 -> BehaviorParam_PC 3xxxxxxxx (e.g. dagger 300100310 `[AOW] Blood Tax`) |

Not placed: judges 155/165 (same MV as 150/160, no TAE use found in a20/a23/a26/a31). An
earlier version of this table had 600-7xx as powerstance and 800-895 as mounted; the TAE and
behavior graph show the reverse.

## 1. Motion values

**MV = `AtkParam_Pc.atkPhysCorrection` (and atkMag/Fire/Thun/DarkCorrection per element), in
percent.** VERIFIED (EXE 0x1406832a0, the hit's attack-power builder, called from
`FUN_140651d40`):

```
phys = ((attackBasePhysics * ReinforceParamWeapon.physicsAtkRate [+ the bow's base, for an arrow])
        * atkPhysCorrection * 0.01            (0.01 = float at 0x14329e624, VERIFIED)
        + (isAddBaseAtk ? atkPhys * k : 0))
       * FUN_140690390(...)   stat-scaling factor (reads isDisableBothHandsAtkBonus)
       * durability factor * [+0x6c] * [+0x70..0x7c by attack type] * SpEffect * throwAtkRate term
       + flat adds [+0x48], [+0x4c..0x58]
```

Magic, fire, lightning and holy (`atkDarkCorrection`) follow the same shape with their own
base and correction. So a hit's damage is AR times MV/100, with the stat-scaling multiplier
applied after the MV. MV multiplies scaled AR, not only the base. VERIFIED.
- `isAddBaseAtk` is set on 558 of 11017 AtkParam_Pc rows (REGULATION). On those rows the
  flat NPC-style `atkPhys` / `atkSuperArmor` / `atkStam` / `guardAtkRate` are added on top.
- Not identified: the `[+0x6c]`, `[+0x70..0x7c]` and FUN_140691320 factors, and `k`
  (FUN_140d53bf0). `[+0x70..0x7c]` (and the flat add `[+0x4c..0x58]`) is picked by the byte at
  `+0x44` of the same struct, 0..3, the same slash/strike/pierce/standard index the damage type
  uses (VERIFIED as code shape; the writer of those floats was not found). The counter-hit bonus
  is not among them: it is a defender SpEffect (defense.md section 2b).
- Physical damage type per attack: `phys_type` in the tool, resolved from `atkAttribute` 252/253
  through the weapon's `atkAttribute2`/`atkAttribute` (VERIFIED, defense.md section 2a). Smithbox describes the `atk*Correction`
  fields as "PC only" and the `atkPhys`-style fields as "NPCs only" (COMMUNITY), which matches
  the two branches.
- 2H MVs are rows of their own (dagger R1 1H 100 -> 2H 103; greatsword 100 -> 114). The 2H x1.5
  STR is applied inside the scaling factor, not in the MV (INFERRED from
  `isDisableBothHandsAtkBonus` being read there).

## 2. Poise damage, stagger and hyperarmor

### Attacker poise damage (VERIFIED, EXE 0x14068af30, called from FUN_140652fe0)

```
weapon attack:  poise = saWeaponDamage * ReinforceParamWeapon.saWeaponAtkRate
                        * atkSuperArmorCorrection * 0.01
                        + (isAddBaseAtk ? atkSuperArmor : 0)
                        * durability factor * SpEffect factor (FUN_1404f8990)
non-weapon:     poise = atkSuperArmor * SpEffect factor
```

`saWeaponAtkRate` is 1.0 on every ReinforceParamWeapon row (REGULATION), so upgrades do not
change poise damage. `EquipParamWeapon.saWeaponDamage` is the weapon's base: dagger 3.0,
straight sword/katana 5.0, Claymore 5.5, Greatsword 6.0, Giant-Crusher 7.5.

### Defender pools (VERIFIED)

The player has two modules. `CSChrSuperArmorModule` (`saDurability`,
`ApplySuperArmorDamage` 0x14047dea0) is the NPC poise bar. The player's poise is
`CSPlayerToughnessModule`:
- Max toughness = `100 * (sum of the 4 armor pieces' EquipParamProtector.toughnessCorrectRate
  * ToughnessParam[row].proCorrectionRate + (window active ? ToughnessParam[row].correctionRate
  * weapon toughnessCorrectRate : 0))`. Code: slot 4 0x140487d10, armor sum 0x140487db0, and the
  x100 from slot 3 0x1404870d0 (float 100.0 at 0x142bb55d8). Without a hyperarmor window the row
  is 0 (`correctionRate` 0, `proCorrectionRate` 1.0), so max = 100 x armor sum.
- A hit subtracts `damage[+0x100] * [+0x244] * cutRate * damageRatio` (0x140486bf0).
  `cutRate` (slot 7 0x1404879d0) is the product of the SpEffect `toughnessDamageCutRate` and the
  armor `toughnessDamageCutRate`. At 0 or below the stagger fires (`FUN_14047e540` on the SA
  module). That `+0x100` holds the poise damage computed above is INFERRED: it feeds both pools.
- Units, INFERRED/COMMUNITY: Bull-Goat pieces are 0.015/0.047/0.010/0.028 (REGULATION), and the
  menu poise of 15/47/10/28 is `x1000`. So internal toughness is menu/10, and attack poise in
  menu units is `x10`: dagger R1 30, straight sword R1 50, Greatsword R1 144. This matches the
  community "51 poise survives one straight-sword/katana R1" breakpoint (5.0 < 5.1 internally),
  which is a PvE breakpoint. Between players `FUN_140486bf0` multiplies by
  `FinalDamageRateParam.saRate`, so a Longsword/Uchigatana 1H R1 #1 deals 5.0 x 2.2 x 10 = 110 and
  a dagger 1H R1 #1 40.5, which matches the community 1.17 PvP poise table exactly
  (VERIFIED 2026-10-01, bd `pvp-poise-units-resolved-51-is-pve-breakpoint-2026-10-01`).
  The menu's own x1000 was not traced (the status menu reads the sum through
  `CalculateToughnessDamageCutRate` 0x140688d60, called from `CalculateStatus`).

### Hyperarmor (VERIFIED handler, TAE windows)

TAE event **795** (handler 0x14042c2e0) is the hyperarmor window:
- It sets `toughness+0x28 = 1` (active), sets `damageRatio = Args f32 @+4` (1.0 in every
  weapon window read), and stores ToughnessParam row = `Args byte 0` in `+0x1c`
  (`FUN_140486e30`). If `Args byte 1` is 1 or 2, it also sets `+0x128 =` the equipped weapon's
  `EquipParamWeapon.toughnessCorrectRate` (slot 5 0x1404879c0), with `byte 2` picking the hand.
  If byte 1 is 3, it uses the item's `refVirtualWepId` instead.
- On the next update (0x140486e50) max toughness is recomputed with that row. Current poise
  becomes `newMax - damage already taken`, clamped to at least
  `minToughness% * newMax` when `isNonEffectiveCorrectionForMin == 0`. So entering the window
  refills poise to at least 80% of the boosted max for rows 100-151. When the window closes
  (`+0x28` 0 while `+0x29` 1) the weapon term is zeroed and max is recomputed.
- **Hyperarmor bonus in internal units = 100 x correctionRate x weapon toughnessCorrectRate.**
  Rows in the weapon TAEs: 100/101 R1 (correctionRate 1.0), 110/111 uncharged R2 (1.0),
  120/121 charged R2 (**2.0**), 130/131 running/rolling/backstep R1 (0.75), 135/136 running R2
  (1.0), 140/141 jump R1 (0.75), 145/146 jump R2 (1.0), 150/151 guard counter (0.5),
  180/181 skills (0.3, minToughness 30). Weapon toughnessCorrectRate: dagger 0.011, straight
  sword/katana 0.015, Claymore 0.059, Greatsword 0.09, Giant-Crusher 0.099.
- Which attacks have it (TAE): a20 dagger has no 795 event at all. a23 straight sword and a29
  katana have it only on the 0306xx/0326xx skill animations (row 180). a25 greatsword has it
  on 2H attacks only (rows 100-150), plus skills. a26 colossal
  sword and a31 colossal weapon have it on every 1H and 2H attack (rows 101-151).
- PvP-only reductions (VERIFIED multiply, INFERRED scope). Both reductions below are applied
  only when attacker and defender each pass `IsMainPlayerIns || isChrEventIdlessThan9998`
  (the names are the dump's).
  - While the window is active, incoming poise damage is multiplied by ToughnessParam
    `unk1` (+0x10, 0x140d51970): 0.65 on rows x0, 0.45 on rows x1.
  - Incoming HP damage is multiplied by `unk2` (+0x14, 0x140d51950 via FUN_140486b10 in
    `CalculateDamageCorrections`): 0.925 on rows x0, 0.825 on rows x1. So colossal weapons
    take 17.5% less HP damage during their hyperarmor, and greatswords 2H take 7.5% less.
  - The paramdef leaves both columns unnamed.

## 3. Stamina

**Cost (VERIFIED).** `FUN_1404428f0` creates the hitbox when a TAE AttackBehavior fires. It then
reads `BehaviorParam_PC.stamina` and calls `PlayerIns::GetConsumeStaminaRate` 0x140651e90 ->
`PlayerGameData::GetConsumeStaminaRate` 0x140684430, and does `AddStamina(-result)`:

```
cost = (int)( BehaviorParam_PC.stamina
              * SpEffect consumeStaminaRate product (0x1404f7470)
              * (flags & 3 ? EquipParamWeapon.staminaConsumptionRate : 1)   AttackBehavior passes 1
              * (HasStatsForWeapon ? 1 : PlayerCommonParam.lowStatus_ConsumeStaminaRate = 1.5) )
```

`staminaConsumptionRate` is 1.0 for most weapons and above 1 for heavy ones (Greatsword 1.135,
Giant-Crusher 1.295, Prelate's Inferno Crozier 1.25). Examples: dagger R1 9, Longsword R1 12,
Greatsword R1 int(20 x 1.135) = 22, Giant-Crusher charged R2 int(44 x 1.295) = 56.
`EquipParamWeapon.attackBaseStamina` is **not** the cost; it is stamina damage (below). The
cost is charged at the moment the AttackBehavior event creates the hitbox, not at animation
start (VERIFIED order inside FUN_1404428f0).

**Stamina damage to a blocker (VERIFIED, 0x14068abd6..0x14068ac34):**
`attackBaseStamina * ReinforceParamWeapon.staminaAtkRate * atkStamCorrection * 0.01
+ (isAddBaseAtk ? atkStam : 0)`, times a SpEffect factor. `staminaAtkRate` grows with
reinforce level (1.0 -> 2.0 at max on the standard track), so upgrades raise it.

**Repel value against a guard (VERIFIED, 0x14068c080):**
`int(attackBaseRepel * guardAtkRateCorrection * 0.01 + (isAddBaseAtk ? guardAtkRate : 0)
+ clamp(STR - overStrength, 0, 10) + durability term)`. The durability term is -5.0
(0x1429e5c30) for a weapon at risk and -10.0 (0x1429ce438) for a broken one. Under-stat uses
`PlayerCommonParam.lowStatus_AtkGuardBreak` = 5. This decides whether the attack bounces off
the guard, not whether the guard breaks: it bounces when the defender's
`int(guardBreakCorrection / 100 * guardBaseRepel)` is at least as high (`FUN_140447180`). Guard
break depends on stamina alone. `overStrength` is 99 on every non-ammunition row, so the STR
term is always 0. Blocking is worked through in `powerstance-guard.md`.

## 4. Timing and multi-hit

- Hit windows are the start/end of TAE event type 1 in the slot's animation. TAE times are clip
  seconds; the tool converts them to real time (play speed, below) and reports frames at
  30 fps (INFERRED rate). Examples, real (clip): dagger R1 #1 f10-12 (10-12), Longsword R1 #1
  f13-16 (13-16), Greatsword R1 #1 f17.7-21.7 (22-26), Giant-Crusher 2H charged R2 f47.4-49.4
  (58-60).
- The 30 fps unit is a choice of unit, not a claim about the game loop. The clips themselves
  say what they were authored at: `hkaSplineCompressedAnimation.frameDuration` is 1/30 s in
  2805 of 2999 player clips sampled and 1/60 s in 191 (Uchigatana 2H R1 #1 is 1/60).
- A type-1 event whose u16 at Args+0xe is nonzero is skipped unless the attacker has a SpEffect
  with that stateInfo (VERIFIED, `AttackBehavior` 0x1404266d0, 1.16.2). In the 639 player TAEs
  189 of 8930 type-1 events carry it (a207 103, a037 76, a031 8, a788 2; TAE), all stateInfo
  187, which only SpEffect 1908 has and which nothing known applies. The tool leaves those
  hitboxes out, which removes the Lance / Messmer Soldier's Spear judge 5000-5005 extras.

### How many times one swing can hit one target (VERIFIED, 1.16.2)

A type-1 event's Args[1] (i32 at params +4) is an attack index: a slot in
`CSChrDamageModule`'s table of 0x10-byte entries (handle, claimed-this-frame byte).
`AttackBehavior` calls `FUN_1404428f0` on every frame the event runs, and the DmgMan attack in
that slot carries the list of targets it has already hit.

```
per contact  FUN_140523710: victim handle in attack+0x278 (own record) or +0x280 (bullet-shared,
             set only by FUN_1403960a0 via FUN_140527200) -> return, no damage   (FUN_14051c5d0)
after a hit  FUN_140524910: walk +0x290 to the root, add the handle to the root's and every
             linked child's record (FUN_140523540 / FUN_1405236d0)
record       FUN_14051ced0 from DmgHitRecordMan, lifetime 0.0 (xorps xmm2 at 0x140523638):
             no expiry while the attack lives; released on retirement (FUN_14051eef0)
per event    FUN_1404428f0: index already claimed this frame (cmp byte [rbx+4],0 at 0x14044299d)
             -> event dropped. Live attack with the same behavior id (cmp [rax+0x38],esi at
             0x1404429f6) -> kept, same record. Otherwise FUN_140527140 removes it, the handle
             is set to -1 (0x140442a6f) and FUN_140526430 creates a new attack: empty record
per frame    FUN_140445d30 (from the character update FUN_140401d80): every index nobody
             claimed this frame loses its attack; claim bytes cleared
```

The shapes one AtkParam row defines (hit0..hit15) are linked under one root by
`FUN_140525ef0` / `FUN_1405273f0` -> `FUN_14051ec90`, so they share a record. Nothing links
attacks of different indices, or an attack to the one it replaced. The victim-side checks on
the way to damage (`FUN_14051af50` -> `FUN_14044a910` -> `FUN_1404443e0`: `IsImmuneToAttack`
0x1403f3b90 and the team gate; `FUN_140444e90` -> `FUN_140445b20`, the stagger write) keep no
per-attacker memory. The AtkParam fields that the path reads are the team flags
(`opposeTarget`, `friendlyTarget`, `selfTarget` via `getTeamTypeRelationshipWithAtkParam`
0x14051a980) and `isDisableNoDamage` / `isInvalidatedByNoDamageInAir` (immunity); no field
suppresses a second attack's hit.

So a target can be hit once per hit record, and a new record starts when:

- (a) a different judge takes over an index (Milady running R1: 123 on index 0 over clip
  f16-18, then 120 on index 0 over f18-19, two records);
- (b) another index runs, at the same time or not (each index has its own record);
- the same judge fires again on an index after at least one frame without it.

Two back-to-back events with the same judge on the same index are one record, one hit.

The tool walks each animation at 60 Hz, clip time, and reports per hitbox `attack_index`,
`hit_records` (records the event opens), `follows_judge` (the different judge it took the index
from) and `hits` (`hit_records`, or 0 when the row cannot damage an opponent). Per slot,
`max_hits` sums them: the ceiling, VERIFIED. Whether each record lands is geometry, which the
tool does not model.

`sweep_hits` (and `sweep_hit` per hitbox, `own_sweep_hits` for the slot's own judge) leaves out
records with `follows_judge` set. INFERRED: such an event is the next segment of the same blade
sweep, and a target hit by the earlier segment is taken to be behind the blade. Milady splits
nearly every swing this way (R2 #2 on index 0: 113 f10-11, 110 f11-13, then after a gap 111
f21-23, 114 f23-25; sweep 2, max 4). What would prove it: count `FUN_140524910` calls for one swing into a stationary target
at runtime.

`can_hit_enemy` is `opposeTarget != 0`. Milady's judge 998 (index 1, AtkParam 6700998) has
all three team flags 0, so it counts 0: INFERRED, because the per-team-pair
`CSTeamTypeRelation::Validate` bodies behind `canTeamTypeHitAnother` were not read. It carries
`mapHitType` 0 and a dummy-poly 120 capsule, consistent with a wall-contact probe.

For a hit sum: take `own_sweep_hits` times the slot row, plus each `other_hitboxes` entry with
`sweep_hit` true; use `max_hits` / `hits` for the ceiling. The weapons checked before this
change (Dagger, Longsword, Greatsword, Uchigatana, Rivers of Blood, both grips) keep the old
count; Milady's running R1 goes from 3 (100 + 105 + 105) to sweep 1 (judge 123, MV 105), max 2.

### Play speed (VERIFIED, TAE 608)

TAE event 608 `AnimSpeedGradient` (0x140426420; 1.17.1 0x140426970) sets
`speed = start + (end - start) * progress` as the pending multiplier at
`CSChrBehaviorModule+0x15c4`; each behavior update latches it into `+0x15c0` and resets
`+0x15c4` to 1.0, and `CSChrBehaviorModule::Update` multiplies the behavior graph's dt by
`+0x15c0`. So inside a 608 window the clip runs `speed` times faster than real time. The EXE
reading and its byte checks are in `reach.md` and `scripts/er-mechanics-reach.py`.

Every frame the tool reports (hit windows, extra hitboxes, hyperarmor, counter windows,
`cancel_frame`, `input_open_frame`, `recovery_after_hit`, `anim_frames`) is
`30 * integral over the clip of dt / speed`, to 0.1 frame. The clip-time value is kept as
`hit_windows_clip`, `cancel_frame_clip`, `recovery_after_hit_clip`, `input_open_frame_clip`,
`anim_frames_clip`, and `frames_clip` inside hyperarmor, counter and extra-hitbox entries;
`speed_windows` lists the 608 windows in clip frames. The integral is taken in closed form
(`clip_to_real`); the selftest compares it with reach's numeric integral. Worked example:
Greatsword R1 #1 (`a026_030000`) plays at 1.34 over clip frames 0-17, so its clip-frame-22 hit
lands at 17 / 1.34 + 5 = 17.7 real frames, and every later frame moves 4.3 earlier.

- 1235 events 608 occur in the player TAEs, speeds 0.4 to 3.0, none overlapping (TAE). Most
  attack windows are constant (Greatsword 1.34, Giant-Crusher 2H R1 1.23 then 1.30, Erdsteel
  Dagger R2 1.25); six clips have a gradient (a027_031230: 0.6 -> 0.8).
- The damage-reaction and roll clips of `a00.tae` carry no 608 event, so the defender side of
  `er-mechanics-frame-advantage.py` needs no conversion.
- Not modelled: the one-update latch delay (+0x15c4 is copied on the next update) and any
  SpEffect speed change. Real frames are fractional because a 608 window rarely ends on a
  real frame boundary; the game acts on the first whole frame at or after the value.

### behaviorDataFactor is 1.0 in the shipped game

`CSChrBehaviorModule::Update` (1.16.2 0x14041d760, 1.17.1 0x14041dca0) computes the behavior
graph's dt as `dt * behaviorDataFactor * debugAnimSpeed * [+0x15c0]` and stores the factor
again at `+0x1768`. The factor was the one untraced term. It is a debug override, not a
per-weapon value (VERIFIED, EXE, both builds; the Arxan dispatch resolved with
`scripts/deobf-emulate.py`):

```
factor(behaviorData):                       1.16.2 thunk 0x140416270 -> 0x144cb547a
                                            1.17.1 thunk 0x1404167a0
  chr = behaviorData->owner                 GetChrOwner 0x14043ccf0 (1.17.1 0x14043d250)
  on  = chr->vtable[0x118]() ? GlobalDebugFlags.taeDebugPlayerEnableAnimePlaySpped   (+0x32)
                             : GlobalDebugFlags.taeDebugEnableAnimePlaySpped         (+0x2e)
                                            0x140513320 -> body 0x140534df5
                                            (1.17.1 0x140514120 -> 0x140e463cd)
  return on ? behaviorData->hksAnimationSpeedMultiplier (+0x310)
            : 1.0                           1.0 at 0x14329e678 (1.17.1 0x1432a1938)
```

- The two flag bytes are `GlobalDebugFlags` (0x143d661a0 in 1.16.2) +0x2e and +0x32, which
  the named dump calls `taeDebugEnableAnimePlaySpped` and `taeDebugPlayerEnableAnimePlaySpped`.
  In 1.17.1 they are 0x143d6a23e and 0x143d6a242. Both lie past `.data`'s raw data in both
  images, so the loader zero-fills them (VERIFIED, section table). A rip-relative scan of both
  code sections (`.text` and the Arxan `.text`) of both images finds two writers per build,
  1.16.2 0x141f43bfe / 0x141521eae and 1.17.1 0x14106a06e / 0x140166f67, and no stored
  pointer to the bytes in 1.16.2. The writers belong to a chain that passes each byte through a helper
  that builds the setting name `GameData.TaeDebugEnableAnimePlaySpped` (UTF-16 at 0x142beb120),
  the same pattern as every neighbouring `GameData.TaeDebug*` flag. No file in the game
  directory holds `GameData.` or `TaeDebug` (MEASURED, two levels deep). What that helper
  returns when no setting exists was not traced, so "the byte stays 0" is INFERRED.
- `CSChrBehaviorDataModule+0x310` is 1.0 from the constructor and the reset 0x1404146b0
  (1.17.1 0x140414be0). Its only other writer is TAE event 603, the template's
  `[ExePatch]DebugAnimSpeed` (handler 1.16.2 0x140428c90, 1.17.1 0x1404291e0; VERIFIED): with
  `Args[0]` = N > 0 it stores `(event end - event start) * 30.0 / N` (so the window would play
  in N frames), and with N <= 0 it stores 1.0. That +0x0/+0x8 of the event's time block are
  its start and end is INFERRED.
- Scale if the flag were on (TAE, hypothetical): 180 events 603 in the player TAEs, 133 of
  them in attack clips (104 animations in 18 motion categories, including Hand Axe/Forked
  Hatchet a170, Ripple Blade a171, Iron Cleaver a172, Milady a60, Great Katana a61, Beast Claw
  a62, rapiers a27 and axes a30). Their factors run from 0.36 to 14.0, and nothing resets the
  value when a window ends. A 14x startup in a shipped attack would be obvious, which fits the
  byte being off (INFERRED).

So the frame computation needs no change: every clip frame here is already real time under
TAE 608 alone. The selftest checks the gate, the 1.0 default, the zero-filled flag bytes and
the 603 store in both images (section 7).

### Which animation a slot reads

`FUN_1403f1d40` (VERIFIED, 1.16.2; 1.17.1 at 0x1403f1f70) resolves an attack animation as
`spAtkcategory * 1000000 + anim` when that is bound, else `wepmotionCategory * 1000000 + anim`,
else `23000000 + anim` for the right hand (`lea edi, [rax+0x15ef3c0]`) and `48000000 + anim` for
the left (`lea edi, [rax+0x2dc6c00]`). `motion_category` follows that order, reading "bound" as
"the TAE has an entry with that id" (INFERRED). The slot rows do not take the a023 fallback:
for the slots it would fill it gives bows an a023 R1 chain, staves and seals an a023 jump R1,
and weapons without their own crouch clip the straight-sword crouch R1. The last contradicts two
outside observations: er-frame-data.nyasu.business lists the crouch and rolling R1 as one attack
(COMMUNITY), and `a023_032310` opens JumpTable 5, which would make the two-handed Giant-Crusher
parryable against the rule that two-handed colossal attacks never are (COMMUNITY, checked in
`er-mechanics-crits.py --selftest`). So where the weapon's own categories lack 030310 the tool
keeps the rolling R1 (row field `crouch_fallback`), INFERRED: the behavior script's
`IsUseStealthAttack` gate was not read.

**Imported animations (TAE).** A TAE entry can borrow another entry's events: its mini-header
type 1 (`ImportOtherAnim`) names the source `category * 1000000 + anim`. The tool reads the
header with `er-mechanics-reach.tae_imports` and, when the entry carries no type-1 event of its
own, reads the source's events together with the entry's own (sound and effect events), as
`er-mechanics-crits.resolve_anim` does. Entries that import but carry their own type-1 events
(a240, a257, a68) are read as they are, so no hit is counted twice (INFERRED). The row's `anim`
is the source, `tae_entry` the entry played, `imported` says which. 1238 entries in the player
TAEs import; for the weapon attack slots this adds 40 rows on 5 weapons that were missing:
Fire Knight's Greatsword 10 (a263 -> a137/a135), Main-gauche 9 (a262 -> a101), Starscourge
Greatsword and both Greatswords of Radahn 7 each (a832's 2H R2s -> a026's 1H R2s). An entry
may also play another entry's clip (`ImportsHKX`, mini-header byte +0x19): the Greatsword crouch
R1 a026_032310 plays a026_032300's HKX, so its length is now read (68 clip, 70.3 real frames).
No crouch entry (030310 or 032310) in any category is an import, so the crouch fallback is
unchanged by following imports.

**Uncharged R2 lead-in (INFERRED).** An uncharged R2 is two clips: `Heavy<n>Start` (030500 /
030510) runs until R2 is up and the release is allowed, then `Heavy<n>End` (030505 / 030515)
plays the swing, and every frame on the R2 #1 / #2 rows is counted from the End clip. HKS
`AttackRightHeavy1Start_onUpdate` (Smithbox `c0000.hks` line 7768, COMMUNITY) releases when R2 is
up and `GetGeneralTAEFlag(TAE_FLAG_CHARGING) == 1 or GetSpEffectID(100280)`. The Start clips
apply SpEffect 100280 on TAE event 67 (Golem's Halberd a198_032500 f17-29), so the tool adds the
first frame of that event, in real time, as `release_lead_in`. `er-builds-pvp.py` adds it to the
startup, next and roll frames of the R2 #1 / #2 rows. 2H examples: Greatsword 4 + 17 = first hit
21, Golem's Halberd 17 + 9 = 26, Giant-Crusher 12.8 + 27.2 = 40, Longsword 7 + 8 = 15. It is
INFERRED as the earliest release: the `TAE_FLAG_CHARGING` event is not identified, and if it
opens earlier the lead-in is shorter.

### Recovery: when the next action can start

VERIFIED (EXE, 1.16.2). TAE event type 0 is `CSChrTaeAnimEvent::_ChrActionFlag` 0x1404275e0,
a switch on Args[0] (the JumpTable id) through the dword table at 0x140428650. The ids that
matter for recovery write two masks on `CSChrActionRequestModule`:

| id | case body | meaning |
|---|---|---|
| 1 | `AllowInputRHAttack` 0x1404300e0: `SetPossibleInputState` 0x140407b80: R1, R2, light kick, heavy kick | R1/R2 input accepted |
| 25 | `AllowInputDodge` 0x140430150: possible input `SP_MOVE`, `BACKSTEP`, `ROLLING`, `JUMP` | roll/backstep/jump input accepted |
| 21 | possible input `GUARD` | guard input accepted |
| 87 | 1 + 25 + guard, item, magic, change, special, L-hand inputs together | every input accepted |
| 4 | `SetAllowedCancelToActionState` R1, R2, light kick, heavy kick; `taeCancels \|= 0x210` | R1 and R2 may start |
| 115 | allowed cancel R1, light kick | R1 (chain) may start, R2 may not |
| 116 | allowed cancel R2, heavy kick | R2 may start |
| 26 | allowed cancel `SP_MOVE`, `BACKSTEP`, `ROLLING`, `JUMP` | roll/backstep/jump may start |
| 22 | allowed cancel `GUARD` | guard may start |
| 11 | `CancelMovement` 0x140407c00 (`taeCancels` bit 2), `taeCancels \|= 0x40` | walking may start |
| 78 | `CancelMovement` only | walking may start |
| 16, 117, 118, 103, 104 | allowed cancel L1/L2, L1, L2, quick weapon art, weapon art (the last two gated on `actionAnimationFlags & 0x7f8`) | off-hand and skill |
| 31, 32, 107 | allowed cancel item; style/weapon switch; item with `isEnhance` | not modelled by the tool |

The consumer is `CSChrActionRequestModule::UpdateFromManipulator` 0x140407c60, once per frame.
For each action bit, a new press is queued (`queuedActionInputs`) only while its
`possibleActionInputs` bit is set, and a queued action becomes ready (`cancelReadyActions`, or
the per-animation `ActionRequestQueue` entry, which `Init` 0x140407320 enables by default and
`FUN_140407ef0` fills with the same test) only while its `possibleActionCancels` bit is set
and `taeCancels & 0x800` is clear. Both masks are then zeroed, so a window is exactly the
frames the TAE event covers. `ActionRequest` 0x140407400, called by `HksEnv` 0x140410820 for
env `ActionRequest`, returns the ready bit; in the idle state (`actionAnimationFlags & 1`) it
returns the raw press instead. Walking goes through `MovementRequest` 0x1404078b0, which
returns `movementRequestFlags` bit 1, set only while `taeCancels` bit 2 (from `CancelMovement`)
is set and the stick is held. So:

- An action can start on the first frame where one of its input windows and one of its cancel
  windows overlap. A press made before the input window opens is dropped, not buffered; a
  press made inside the input window but before the cancel window is held and fires when the
  cancel window opens.
- Pairs used by the tool: R1 = input 1/87 with cancel 4/115; R2 = input 1/87 with cancel
  4/116; roll = input 25/87 with cancel 26; guard = input 21/87 with cancel 22; walk = cancel
  11/78 alone.
- The tool reports the ready frame. The state transition itself is the behavior script's
  (`c0000.hks` reads `env(ActionRequest, ...)`); that it transitions on the same frame the bit
  is ready is INFERRED, since the HKS bytecode was not read.

**Early activation (VERIFIED, event 300).** `ActivateChrActionFlagEarly` 0x140425ba0 handles
the same ids (4, 11, 16, 22, 26, 29, 31, 32, 103, 104, 107, 115, 116, 117, 118, ...; dispatch
0x14042639c/0x140426348). Args are `s16 id, s16 earlyType, f32 weightStart, f32 weightEnd`.
The id is opened while `earlyValue < weightStart + (weightEnd - weightStart) * progress`,
where `GetJuptableEarlyActivateValue` 0x14042f950 picks `earlyValue`: 1 equip load
(`PlayerIns::GetEquipLoad` 0x140655950), 2 `EquipParamWeapon.weaponWeightRate` of the attack
hand (`PlayerIns::GetWeaponWeightRate` 0x140655850), 3 a damage-module virtual, 4
`damageModule+0x64` (the dump calls this `ALWAYS_AS_EARLY_AS_POSSIBLE`), 5 casting speed, 6 a
debug value, and anything else 0.0.
- TAE, counted over the 0300xx-0329xx animations of a20, a23, a25, a26, a29 and a31: of the
  1233 events 300 on ids 4, 11, 22, 26, 115 and 116, 1209 use type 2 with weights 0 to 1.
  1149 of those start 2 frames before a type-0 event on the same id, and 60 have no later
  type-0 event (for example id 4 at f86 in a029_032000). The other 24 use type 4, 6 each on
  4, 11, 22 and 26, starting 3 frames ahead.
- REGULATION: `weaponWeightRate` is 0.0 on all 3636 EquipParamWeapon rows, so a type-2 event
  opens its id on its own first frame, for every weapon. The early open is therefore the
  normal case, 2 frames ahead of the type-0 window. The tool computes it from the weapon's
  actual `weaponWeightRate` anyway.
- In the same animations type 1 (equip load) appears only on id 107 (quick goods). The tool
  does not evaluate types 1, 3, 4 and 5 and counts them in `unresolved_early_events`; for
  those the type-0 window is the late bound.

**Also in the switch (VERIFIED, not used for recovery):** 8 i-frames
(`actionModifiersFlags |= 2`), 5 parry window (`|= 0x400`, byte arg), 24 `|= 0x100` (skips the
immunity test in `CalculateDamage2`), 7 `|= 0x8000`, 110 `|= 0x100000000`. The top of the
switch skips any type-0 event whose Args+0xe holds a SpEffect state-info id the character
does not have; none of the 2572 recovery-id events in the same animations of a20, a23, a25,
a26, a29, a31, a33 and a35 sets it.

**Animation length (TAE data).** The TAE does not store a length. The tool reads it from the
clip, `a<cat>_<anim>.hkx` in the unpacked `c0000_a*x.anibnd` shards, taking the
`hkaSplineCompressedAnimation` whose `(numFrames - 1) * frameDuration == duration`. The TAE
cancel windows often run past it (Uchigatana 2H R1 #1: clip 63 frames, windows end at 80), so
what happens after the clip ends is not decided by these windows.

Examples (first frame, real 30 fps frames from animation start, clip-time value in brackets
where the 608 play speed changes it; hit = hit window; clip = clip length):

| attack | 608 speed (clip f) | hit | R1 | R2 | roll | guard | walk | clip |
|---|---|---|---|---|---|---|---|---|
| Dagger R1 #1 | - | f10-12 | 14 | 16 | 17 | 18 | 22 | 40 |
| Longsword R1 #1 | - | f13-16 | 17 | 21 | 24 | 27 | 29 | 63 |
| Uchigatana R1 #1 | - | f13-16 | 16 | 20 | 24 | 26 | 32 | 69 |
| Uchigatana 2H R1 #1 | - | f14-17 | 17 | 25 | 26 | 26 | 35 | 63 |
| Uchigatana 2H R2 #1 | - | f13-16 | 28 | 24 | 27 | 28 | 34 | 59 |
| Erdsteel Dagger 2H R2 #1 (`a103_032505`) | 1.25 @0-10, 11-21 | f9.8-12.2 [12-15] | 22 [26] | 19 [23] | 23 [27] | 24 [28] | 27 [31] | 49 [53] |
| Greatsword R1 #1 | 1.34 @0-17 | f17.7-21.7 [22-26] | 33.7 [38] | 39.7 [44] | 35.7 [40] | 47.7 [52] | 55.7 [60] | 81.7 [86] |
| Greatsword 2H R1 #1 | 1.34 @0-17 | f16.7-20.7 [21-25] | 32.7 [37] | 38.7 [43] | 37.7 [42] | 44.7 [49] | 53.7 [58] | 78.7 [83] |
| Greatsword 2H crouch R1 (`a026_032310`) | 0.86 @0-14 | f16.3-18.3 [14-16] | 32.3 [30] | 35.3 [33] | 33.3 [31] | 35.3 [33] | 49.3 [47] | 70.3 [68] |
| Giant-Crusher 2H R1 #1 | 1.23 @0-22, 1.30 @26-39 | f17.9-20.9 [22-25] | 30.3 [37] | 34.9 [42] | 35.9 [43] | 45.9 [53] | 58.9 [66] | 72.9 [80] |
| Giant-Crusher 2H crouch R1 (rolling R1 `a031_032300`) | 1.36 @0-19 | f14-16 [19-21] | 37 [42] | 37 [42] | 35 [40] | 42 [47] | 56 [61] | 74 [79] |
| Giant-Crusher 2H R2 #1 charged (`a197_032500`) | 1.30 @12-58 | f47.4-49.4 [58-60] | 69.4 [80] | 65.4 [76] | 76.4 [87] | 83.4 [94] | 93.4 [104] | 119.4 [130] |

Recovery after the hit is the difference: Uchigatana 2H R1 #1 chains into R1 #2 on the frame
its hit ends (17 - 17 = 0) and can roll 9 frames later; Giant-Crusher 2H R1 #1 needs 9.4 real
frames to chain (12 clip) and 15 to roll (18 clip). A play-speed window that ends before the
hit shifts the hit and everything after it by the same amount, so recovery after the hit is
unchanged (Greatsword R1: 12 either way); a window that spans the hit (Giant-Crusher 2H R1's
second one) shortens the recovery itself.
- **Every regular R1/R2/running/rolling/backstep/jump/counter attack of the five categories
  read is one hit** (one type-1 event with the slot's judge). Extra type-1 events do exist:
  - A 1-frame judge-1 event after colossal hits (row `x00001`, MV 0, poise 0), which deals no
    damage. The tool drops zero-MV extras.
  - The falling jump loop (031060/031260, four 10-frame windows).
  - Skill animations (0306xx, two to four judges).
- One hit per target per event is INFERRED: FUN_1404428f0 keeps one damage slot per event
  (`Args[1]`), and returns early when that slot already holds the same behavior id.
- Per-hit status build-up and per-tick behaviour were not traced.

## 5. Representative weapons (regulation 1.17.1, `scripts/er-mechanics-attacks.py`)

Poise is internal units (x10 for menu units). Stam = stamina cost. HA = hyperarmor poise
bonus (internal) and its TAE window in real frames, clip frames in brackets (section 4, play
speed). All attacks listed are 1 hit.

| weapon (1H) | R1 #1 MV/poise/stam | R2 | charged R2 | running R1 | running R2 | jump R1 | jump R2 | HA on R1 |
|---|---|---|---|---|---|---|---|---|
| Dagger 1000000 | 100 / 3.0 / 9 | 120 / 6.0 / 15 | 150 / 18.0 / 22 | 105 / 3.0 / 10 | 120 / 6.0 / 15 | 107 / 4.5 / 8 | 122 / 12.0 / 14 | none |
| Longsword 2000000 | 100 / 5.0 / 12 | 125 / 10.0 / 20 | 160 / 30.0 / 30 | 105 / 5.0 / 15 | 120 / 10.0 / 20 | 107 / 7.5 / 10 | 127 / 20.0 / 20 | none |
| Uchigatana 9000000 | 100 / 5.0 / 12 | 125 / 10.0 / 20 | 160 / 30.0 / 30 | 105 / 5.0 / 15 | 120 / 10.0 / 20 | 110 / 7.5 / 10 | 130 / 20.0 / 20 | none |
| Claymore 3180000 | 100 / 10.18 / 15 | 125 / 13.2 / 25 | 165 / 33.0 / 35 | 105 / 10.18 / 18 | 120 / 11.0 / 25 | 107 / 10.18 / 12 | 130 / 22.0 / 25 | none (1H) |
| Greatsword 4000000 | 100 / 14.4 / 22 | 121 / 14.4 / 34 | 156 / 36.0 / 45 | 105 / 14.4 / 27 | 120 / 14.4 / 34 | 107 / 14.4 / 18 | 130 / 24.0 / 34 | +9.0 f7.5-25.7 [10-30] |
| Giant-Crusher 23110000 | 100 / 18.0 / 25 | 130 / 15.0 / 42 | 175 / 42.0 / 56 | 105 / 18.0 / 31 | 120 / 18.0 / 38 | 107 / 18.0 / 20 | 130 / 27.75 / 38 | +9.9 f7.8-34 [10-39] |
| Fire Knight's Greatsword 4520000 (R1s and R2s imported from a137/a135) | 100 / 14.4 / 14 | 125 / 14.4 / 29 | 165 / 36.0 / 38 | 105 / 14.4 / 23 | 120 / 14.4 / 29 | 107 / 14.4 / 15 | 130 / 24.0 / 29 | +9.0 f7.5-24.8 [9-27] |

| weapon (2H) | R1 #1 | R2 | charged R2 | running R1 | running R2 | jump R1 | jump R2 | HA R1 / charged R2 |
|---|---|---|---|---|---|---|---|---|
| Dagger | 103 / 3.9 / 11 | 125 / 6.6 / 18 | 155 / 19.8 / 27 | 110 / 3.9 / 12 | 125 / 6.6 / 18 | 112 / 5.85 / 10 | 127 / 13.2 / 17 | none |
| Longsword | 103 / 6.5 / 15 | 130 / 11.0 / 24 | 165 / 33.0 / 36 | 110 / 6.5 / 18 | 125 / 11.0 / 24 | 112 / 9.75 / 12 | 132 / 22.0 / 24 | none |
| Uchigatana | 103 / 6.5 / 13 | 130 / 11.0 / 24 | 165 / 33.0 / 36 | 110 / 6.5 / 18 | 125 / 11.0 / 24 | 115 / 9.75 / 12 | 135 / 22.0 / 24 | none |
| Claymore | 103 / 13.2 / 18 | 130 / 14.52 / 30 | 170 / 36.3 / 40 | 110 / 13.2 / 22 | 125 / 12.1 / 30 | 110 / 13.2 / 15 | 135 / 24.2 / 30 | +5.9 / +11.8 |
| Greatsword | 114 / 18.72 / 27 | 136 / 18.72 / 40 | 176 / 39.6 / 54 | 122 / 18.72 / 31 | 138 / 18.72 / 40 | 112 / 18.72 / 22 | 135 / 26.4 / 40 | +9.0 / +18.0 |
| Giant-Crusher | 114 / 23.4 / 31 | 137 / 16.5 / 51 | 187 / 45.75 / 67 | 124 / 23.4 / 36 | 137 / 23.4 / 46 | 112 / 23.4 / 25 | 135 / 30.75 / 46 | +9.9 / +19.8 |
| Fire Knight's Greatsword | 105 / 18.72 / 17 | 139 / 18.72 / 34 | 178 / 39.6 / 46 | 122 / 18.72 / 27 | 138 / 18.72 / 34 | 112 / 18.72 / 19 | 135 / 26.4 / 34 | +9.0 / +18.0 |

Observations from the data (VERIFIED as data, not as design intent):
- The R1 chain's last hit doubles poise. Dagger #6 and Longsword #5 use atkSuperArmorCorrection
  200 against 100.
- The colossal and greatsword family R1s carry corrections of 240 and 185, so their R1 poise
  exceeds their uncharged R2 poise for Giant-Crusher (18.0 vs 15.0).
- Uncharged and charged R2 differ mostly in poise (x3 on light weapons, x2.3-2.8 on heavy ones),
  and MV rises by about 30%.

## 6. How the tool uses this

`weapon_attacks(reg, weapon_id, grip, level)` resolves each slot's judge through the EXE
resolver, then computes the section 1-3 formulas from the rows. When
`ER_PLAYER_TAE_DIR` (default: the WitchyBND-unpacked `c0000-anibnd-dcx/.../tae`) exists, it
also reads the slot's animation for windows, hit count, extra damaging hitboxes and event 795
windows. Slots that the category's TAE never hits (for example R1 #5 on a colossal) are
dropped. An optimizer should multiply AR by `mv_*/100`, compare `poise_damage` against the
defender's `100 x armor sum`, and budget `stamina_cost`.

Damage type and counters per slot: `phys_type` (slash, strike, pierce, standard, or none) is the
physical type the hit carries after the 252/253 weapon lookup (defense.md section 2a).
`counter_windows` lists the TAE event-66 spans that apply a stateInfo-110 SpEffect to the
attacker (SpEffect 45 on player weapon attacks, 31 in `a938`): a pierce hit taken inside that
span, or within the SpEffect's 0.1 s endurance after it, does 1.15x physical damage (1.3 for 31),
defense.md section 2b. For Longsword R1 (`a023_030000`) it is frames 14-29 around the 13-16
hitbox; for Greatsword R1 (`a026_030000`) it is real frames 17.7-41.7 around 17.7-21.7. Over 396 slots of 12 weapons (dagger, straight/great/colossal swords, thrusting sword,
katana, spear, great spear, colossal weapon and more, both grips) every slot has one, and it starts
on the first hitbox frame in 368, one frame either side in 25, and 3-5 frames later in 3 (TAE). So
an attack is counter-able from its first active frame well into its recovery, not during its
wind-up: a hit during their wind-up is not a counter, a hit from the moment their hitbox comes
out is.

Recovery fields per slot (real frames at 30 fps from animation start, section 4 play speed;
each also as `<name>_clip`): `cancel_frame` (r1, r2,
dodge, guard, move: first frame the action can start, section 4), `recovery_after_hit` (that
frame minus the last hit frame), `input_open_frame` (first frame a press is accepted rather
than dropped), `unresolved_early_events`, `anim_frames` and `anim_frame_duration` from the
clip. `ER_PLAYER_HKX_ROOT` (default: the WitchyBND-unpacked `sharded/chr`) holds the clips;
without it the length is empty. For a chain, startup of the next attack counts from its own
animation start, so time between hits = `cancel_frame.r1` of this slot + the next slot's first
hit frame.

## 7. Selftest (`--selftest`, 105 passed, 0 failed, 0 skipped)

| check | reference |
|---|---|
| dagger R1 -> 100100000; Greatsword R1 -> 100400000 (fallback); dagger judge 3310 -> 300100310 | EXE decompile of 0x140652280 |
| dagger R1 #6 / charged R2 / 2H R1 #1 resolve to rows Smithbox names `1H Light #6`, `1H Heavy #1 Max`, `2H Light #1` | COMMUNITY: Smithbox Param Row Names (authored apart from this tool) |
| a20 dagger anims 030000..030050, 030500..030515 fire the slot judges | TAE a20.tae. This is a regression check: the slot table was built from these files |
| Longsword and Uchigatana R1 poise = 50 menu units (< 51) | COMMUNITY: the 51-poise PvE breakpoint (PvP multiplies by `saRate`) |
| Longsword R1 has no hyperarmor window; Greatsword R1 has one | COMMUNITY: straight swords have no R1 hyperarmor, colossals do |
| 0.01 at 0x14329e624 and 100.0 at 0x142bb55d8 | EXE `eldenring-deobf.bin` (1.16.2) |
| 1.17.1: 0.01 at 0x1432a18e4, 100.0 at 0x142bb86f8, -5.0 at 0x1429e8c30, -10.0 at 0x1429d1438, each also read through the rip operand of the re-found instruction (stamina damage 0x14068ba51, toughness slot 3 0x140487630, repel 0x14068d0d7 / 0x14068d0e1) | EXE `eldenring-deobf-1.17.1.bin` |
| behaviorDataFactor, both builds: the debug branch is `movss xmm0, [rdi+0x310]`, the other loads 1.0; the gate reads two bytes 4 apart under `cmovne`; both bytes are zero-filled `.data`; TAE 603 stores `(end - start) * 30.0 / Args[0]` at +0x310 | EXE `eldenring-deobf.bin` and `-1.17.1.bin`, sites in `FACTOR_SITES` |
| JumpTable cases 4/115/116/26 call `SetAllowedCancelToActionState` with R1+R2 / R1 only / R2 / `ROLLING`; 11 and 78 call `CancelMovement`; 1, 25, 87 call the input helpers | EXE: call targets and EDX read out of the case bodies behind the table at 0x140428650 |
| event 300 handles ids 4, 11, 26, 115, 116; early type 2 jumps through PlayerIns vtable +0x400 | EXE tables 0x14042639c/0x140426348 and 0x14042f9f0 |
| ids 1, 4, 11, 25, 26, 87, 115, 116 carry the names Input/Cancel RH Attack, LS Movement, Dodge, Common, R1, R2 | COMMUNITY: WitchyBND `TAE.Template.ER.xml` |
| a020_030000.hkx is 41 frames of 1/30 s | the clip's own `hkaSplineCompressedAnimation` fields |
| the atkAttribute resolver compares 0xfd then reads weapon +0x104, compares 0xfc then reads +0x191 | EXE bytes at 0x1406868e0 in `eldenring-deobf-1.17.1.bin` |
| EquipParamWeapon +0x104 / +0x191 are `atkAttribute` / `atkAttribute2`; the tool's 253/252 map agrees | Smithbox paramdef layout |
| enum 252 = "atkAttribute2 reference", 253 = "atkAttribute reference" | COMMUNITY: Smithbox `ATKPARAM_ATKATTR_TYPE` |
| `CalculateDefenseModifiers` compares stateInfo 0x6e | EXE bytes at 0x1404f631e (1.17.1) |
| SpEffect 45 is named `[HKS] Counter Frames`; Greatsword R1 applies it through event 66 | COMMUNITY: Smithbox row names; TAE a23.tae |
| Greatsword R1: 608 window 1.34 over clip 0-17, hit clip 22-26 -> real 17.7-21.7, roll cancel = clip - (17 - 17/1.34) | TAE a26.tae + arithmetic on the decoded window |
| a027_031230 (0.6 -> 0.8 gradient, then 1.3): clip frame 20 -> real, equal to the closed form and within 0.02 f of reach's numeric integral | arithmetic; `er-mechanics-reach.real_seconds` |
| a263_030000 has no events; Fire Knight's Greatsword R1 row exists, reads a137_030000, hit frames equal a137's own entry | TAE mini-header + a137.tae read directly |
| 189 gated type-1 events in the player TAEs, all stateInfo 187; the Lance R1 keeps no judge 5000-5005 extra | TAE, counted in the test |
| `FUN_1403f1d40` adds 23000000 for hand 1 and 48000000 for hand 0, in 1.16.2 and (0x230 higher) 1.17.1 | EXE bytes |
| hit-record sites: `cmp byte [rbx+4],0` 0x14044299d, `cmp [rax+0x38],esi` 0x1404429f6, `mov dword [rbx],-1` 0x140442a6f, `xorps xmm2,xmm2` 0x140523638 | EXE bytes, `eldenring-deobf.bin` (1.16.2) |
| walk: back-to-back same judge = 1 record; same judge after a 1-frame gap = 2; judge change on an index = new record with `follows_judge`; overlap on one index = first event holds it; two indices = 2 | synthetic events against the section 4 rule |
| Milady running R1: 998 on index 1 cannot hit an enemy, 123 on index 0 counts 1, 120 follows 123 on index 0; max 2, sweep 1 (judge 123) | TAE a60.tae + AtkParam_Pc `opposeTarget` |
| a031 lacks 032310 and a023 has it; `motion_category` takes a023, the Giant-Crusher 2H crouch R1 row stays on a031_032300; Greatsword keeps a026_032310; Longbow R1 does not take a023 | TAE entry lists |

## 8. Not established

- The remaining physical-damage factors: `[+0x6c]`, `[+0x70..0x7c]`, FUN_140691320, the
  `isAddBaseAtk` multiplier FUN_140d53bf0, and the durability factor's curve
  (`ItemEntryInfo::GetDurabilityConditionMultiplier`).
- `AttackDamageInfo+0x100` being the poise damage, and what `+0x244` holds.
- The menu x1000 for poise (the internal x100 is VERIFIED).
- That the ToughnessParam id is read from `+0x1c`. The decompile lost the register; that
  `FUN_140486e30` writes the 795 byte there is VERIFIED.
- When event 795's window end clears `+0x28` (the update path shows the clear, but not who
  writes 0).
- That the PvP-only predicate `isChrEventIdlessThan9998` means "player character".
- Stamina charged once per attack even when an animation fires several type-1 events for the
  same slot (the early return suggests so).
- That a segment which takes an index over from another judge misses a target the earlier
  segment hit (`sweep_hits`, section 4); the engine allows the hit (`max_hits`).
- `CSTeamTypeRelation::Validate` for each team pair, so what a row with all team flags 0 hits.
- The order the TAE runs events within one frame (taken as stored order; only matters when two
  events on one index overlap).
- Jump slots: 031030/031040/031050 carry the same judge as 031070 with a longer window
  (colossal 16-35). Which clip plays for a standing, forward or falling jump was not mapped,
  and the tool reports 031070's window.
- Judges 155/165 have no TAE placement here.
- Which animations the 30 SpEffect-31 counter events in `a938` belong to (which weapon class uses
  motion category 938), and whether HKS applies SpEffect 45 outside the TAE as its Smithbox name
  suggests.
- Play speed beyond TAE 608: SpEffect animation-speed changes were not read, and the
  one-update latch of +0x15c4 is not modelled. `CSChrBehaviorDebugAnimHelper::GetAnimationSpeed`,
  the other term of the dt multiply, was not traced. For behaviorDataFactor (section 4) the open
  part is what the `GameData.TaeDebug*` helper returns when no setting exists, and so whether
  anything can turn the byte on in a retail session.
- Whether the behavior script enters the crouch R1 state for weapons with no crouch clip of their
  own (`IsUseStealthAttack`); the tool assumes the rolling R1 (section 4).
- Import entries with type-1 events of their own (a240, a257, a68): whether the game adds the
  source's events to them.
- That the HKS behavior script transitions on the frame `ActionRequest` reports ready, and any
  extra conditions it adds (stamina, for instance). No runtime frame measurement was made.
- Early-activation types 1, 3, 4 and 5 (equip load, damage-module values, casting speed); the
  tool reports their count and uses the type-0 window. What `damageModule+0x64` holds.
- Who sets `taeCancels & 0x800` (blocks every cancel) and bit 1 (clears the input queue).
- What happens between the clip's end and the end of TAE windows that run past it.
- Whether a TAE animation that imports another clip (`ImportsHKX`) is timed by that clip; the
  tool now reads that clip's length (section 4), which is MEASURED, not traced.
- Off-hand, skill, item and weapon-switch cancels (16, 29, 31, 32, 103, 104, 107, 117, 118) are
  identified but not reported per slot.
- Whether 1.17.1 changed what the functions above do. Each was re-found in 1.17.1 (section 9)
  and its opening instructions match, but past those only the sites the selftest reads were
  compared.

## 9. The addresses in 1.17.1

Every 1.16.2 address this document cites, carried to the installed 1.17.1 build (2.7.1.0).
Code went through `scripts/map-rvas-1162-to-1170.py` and then the fixed 1.17.0 -> 1.17.1 rule
of `scripts/map-rvas-1170-to-1171.py` (+0x70 at or above rva 0xafefe9, which here moves only
the 0x140d... functions). Every row was then checked against `eldenring-deobf-1.17.1.bin`:
the first eight instructions of both sides, with addresses masked, match for every row
except the special cases named in the method column (MEASURED 2026-09-29).

The four `.rdata` constants moved and were re-found through the instructions that read them.
The old note that the 1.17 images "do not map `.rdata` as offset == RVA" was wrong: they do,
the constants simply sit at other addresses (`.rdata` starts 0x3000 higher in 1.17.1).

| constant | 1.16.2 | 1.17.1 | read by (1.17.1) |
|---|---|---|---|
| 0.01 | 0x14329e624 | 0x1432a18e4 | attack power 0x1406840f0 (5 sites), stamina damage 0x14068ba51, repel 0x14068d01a |
| 100.0 | 0x142bb55d8 | 0x142bb86f8 | toughness slot 3 0x140487630 |
| -5.0 | 0x1429e5c30 | 0x1429e8c30 | repel 0x14068d0e1 |
| -10.0 | 0x1429ce438 | 0x1429d1438 | repel 0x14068d0d7 |

Code (`masked signature` = unique masked-byte match; `anchor delta` = the nearest verified
anchor's shift, then the instruction check):

```
1.16.2       1.17.1       method
0x1403960a0  0x1403960b0  16-byte match
0x1403f1d40  0x1403f1f70  masked signature
0x1403f3b90  0x1403f3dc0  masked signature
0x140401d80  0x1404020f0  masked signature
0x140407320  0x140407850  masked signature
0x140407400  0x140407930  masked signature
0x1404078b0  0x140407de0  masked signature
0x140407b80  0x1404080b0  masked signature
0x140407c00  0x140408130  masked signature
0x140407c60  0x140408190  masked signature
0x140407ef0  0x140408420  masked signature
0x140410820  0x140410d50  12-byte match at the +0x530 of its neighbours
0x140416270  0x1404167a0  call site in Update 0x14041dca0
0x140425ba0  0x1404260f0  masked signature
0x140426348  0x140426898  jump table, every entry +0x550
0x14042639c  0x1404268ec  masked signature
0x140426420  0x140426970  masked signature
0x1404266d0  0x140426c20  anchor delta
0x1404275e0  0x140427b30  masked signature
0x140428650  0x140428ba0  jump table, every entry +0x550
0x14042c2e0  0x14042c830  masked signature
0x14042f950  0x14042fea0  masked signature
0x14042f9f0  0x14042ff40  jump table, every entry +0x550
0x1404300e0  0x140430630  masked signature
0x140430150  0x1404306a0  anchor delta
0x1404428f0  0x140442e50  masked signature
0x14044299d  0x140442efd  masked signature
0x1404429f6  0x140442f56  masked signature
0x140442a6f  0x140442fcf  masked signature
0x1404443e0  0x140444940  masked signature
0x140444e90  0x1404453f0  masked signature
0x140445b20  0x140446080  masked signature
0x140445d30  0x140446290  masked signature
0x140447180  0x1404476e0  masked signature
0x14044a910  0x14044ae70  anchor delta
0x14047dea0  0x14047e400  masked signature
0x14047e540  0x14047eaa0  masked signature
0x140486b10  0x140487070  masked signature
0x140486bf0  0x140487150  masked signature
0x140486e30  0x140487390  same two-instruction body (mov [rcx+0x1c], edx; ret)
0x140486e50  0x1404873b0  masked signature
0x1404870d0  0x140487630  toughness module vtable slot 3 (vtable 0x142a3ef30)
0x1404879c0  0x140487f20  toughness module vtable slot 5
0x1404879d0  0x140487f30  masked signature
0x140487d10  0x140488270  masked signature
0x140487db0  0x140488310  masked signature
0x1404f7470  0x1404f8240  anchor delta
0x1404f8990  0x1404f9760  anchor delta
0x14051a980  0x14051b780  masked signature
0x14051af50  0x14051bd50  masked signature
0x14051c5d0  0x14051d3d0  masked signature
0x14051ced0  0x14051dcd0  masked signature
0x14051ec90  0x14051fa90  masked signature
0x14051eef0  0x14051fcf0  anchor delta
0x140523540  0x140524340  masked signature
0x140523638  0x140524438  masked signature
0x1405236d0  0x1405244d0  masked signature
0x140523710  0x140524510  masked signature
0x140524910  0x140525710  masked signature
0x140525ef0  0x140526cf0  masked signature
0x140526430  0x140527230  anchor delta
0x140527140  0x140527f40  masked signature
0x140527200  0x140528000  masked signature
0x1405273f0  0x1405281f0  masked signature
0x140651d40  0x140652b90  masked signature
0x140651e90  0x140652ce0  masked signature
0x140652280  0x1406530d0  masked signature
0x140652fe0  0x140653e30  masked signature
0x140655850  0x1406566a0  masked signature
0x140655950  0x1406567a0  masked signature
0x1406577b0  0x140658600  same body (cmp edx, 0x270f; setbe al; ret)
0x1406832a0  0x1406840f0  anchor delta
0x140684430  0x140685280  masked signature
0x140688d60  0x140689bb0  masked signature
0x14068abd6  0x14068ba26  masked signature
0x14068ac34  0x14068ba84  anchor delta
0x14068af30  0x14068bd80  masked signature
0x14068c080  0x14068ced0  masked signature
0x140690390  0x1406911e0  masked signature
0x140691320  0x140692170  masked signature
0x140d240e0  0x140d25860  masked signature
0x140d51950  0x140d53700  anchor delta
0x140d51970  0x140d53720  anchor delta
0x140d53bf0  0x140d559a0  masked signature
```

The behaviorDataFactor addresses (section 4) were traced in each build separately rather
than mapped, because the Arxan stubs between them do not map.
