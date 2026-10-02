# Status build-up in PvP: bleed, frost, poison, scarlet rot, sleep, madness, death blight

Calculator: `scripts/er-mechanics-status.py` (`--selftest` 76/76). Labels follow the other docs in
this directory: **VERIFIED** (the 1.16.2 Ghidra dump on :8765, byte-checked in
`eldenring-deobf-1.17.1.bin`, or a regulation value), **INFERRED**, **COMMUNITY**, **MEASURED**
(the planner corpus, `~/.cache/er-build-planner/builds.jsonl`). Nothing was launched. Params are
the installed 1.17.1 `regulation.bin`.

## Answers first

| question | answer | label |
|---|---|---|
| Where does per-hit build-up come from? | 13 hit SpEffect slots plus one grease/weapon-buff row. Slots 5..7 are `EquipParamWeapon.spEffectBehaviorId0..2 + ReinforceParamWeapon.spEffectId1..3` (the pairing the AR doc left open is now read from code) | VERIFIED |
| Which status does a row apply? | Only the one its `stateInfo` names (2 poison, 5 rot, 6 bleed, 116 death blight, 260 frost, 436 sleep, 437 madness); the matching `*AttackPower` is the amount | VERIFIED |
| Does arcane scale it? | Poison, bleed, sleep, madness: the arcane multiplier of `FUN_1406832a0` (same code as `er-mechanics-ar.py`). Frost, rot, death blight: never | VERIFIED |
| Does the arcane multiplier also scale grease and AtkParam SpEffects? | Yes. One per-status vector multiplies every row applied by the hit, so Blood Grease on an arcane-scaling weapon gets the weapon's bleed multiplier | VERIFIED |
| Does the attack scale it? | Not by motion value. By `AtkParam.statusAilmentAtkPowerCorrectRate / 100` when the row has `isUseStatusAilmentAtkPowerCorrect` (every weapon status row does), and also by `statusAilmentAtkPowerCorrectRate_byPoint / 100` for the AtkParam's own SpEffects and the grease row | VERIFIED |
| Unmet requirements? | x0.2 on all status build-up when STR (x1.5 two-handed), DEX, INT or FTH is short. Arcane is not checked here; an unmet arcane requirement acts through the arcane multiplier's own penalty instead | VERIFIED |
| PvP-specific rate? | Only `EquipParamWeapon.vsPlayerDmgCorrectRate_{Poison,Disease,Blood,Curse,Freeze,Sleep,Madness}`, applied when both sides count as PvP. All are 1.0 in 1.17.1, so PvP build-up equals PvE build-up. `FinalDamageRateParam` and the SpEffect `atkPlayerDmgCorrectRate_*` do not touch status | VERIFIED + MEASURED |
| What happens on proc? | The same SpEffect row stays applied: bleed 15% max HP + 100 (+200 on 54 rows, 7% + 30 on 3); frost 10% + 30 and x1.2 damage taken for 30 s; poison 0.07% + 7 per second for 90 s (0.14% + 14 for 30 s on 28 rows); rot 0.18% + 15 per second for 90 s; madness 15% + 100 HP and 10% + 30 FP; sleep 10% + 30 FP and SpEffect 102371 | VERIFIED values, tick count INFERRED |
| Does the gauge build while the status is active? | No. Every status row is `spCategory` 10003..10010 (one per status), and `CheckApplyConditions` refuses a row of category >= 10000 while a live entry has that category (`FUN_1404fc690`). The refused hit returns before the resist module, so the gauge, reset to full by the proc, stays full until the effect expires (section 1a) | VERIFIED |
| Does `effectTargetPlayer` 0 stop a row building up on a player? | No. Nothing in either image reads `effectTargetSelf`..`effectTargetGhost` (byte +0x15f), and 3570 rows have all seven at 0, Morgott's Great Rune (max HP x1.25 on the player) among them. The applied gate is `effectTargetOpposeTarget` (a hostile victim) / `FriendlyTarget` / `SelfTarget` against the attacker-victim team relation, the same test that lets the hit damage. Chilling Mist 880/881/829, Hoarfrost Stomp 1800, DMGS 1724 and Frostbite 6700 all have it set, and so do all 530 status rows a weapon, buff, grease or `AtkParam_Pc` reaches (section 1b) | VERIFIED |
| Does a re-proc refresh, stack or get ignored? | It cannot happen: the row is refused before the gauge sees it, so no refresh, no second instance, no extra build-up. Poison and rot are separate categories (10004, 10005), so one of each can run at once; the 30 s and 90 s poison rows share 10004 | VERIFIED |
| Proc damage in PvP? | No PvP branch in the HP-change code: maxHP * changeHpRate / 100 + changeHpPoint, scaled only by the defender's `*DamageRate` (bleed/frost/sleep/madness; 100 on players) | VERIFIED |
| Resistance and decay? | Gauge starts at the defender's resistance, loses the int of each row's build-up, procs when it drops below 1, then resets to full (overflow lost). It refills every frame at `resistRecoverPoint_*_Player`: bleed 7, frost 5, sleep 5, poison 4, rot 4, madness 4, death blight 3 points per second. No delay before refill | VERIFIED (per-second INFERRED) |
| Does resistance rise after each proc? | Only through NpcParam `resistCorrectId_*` (ResistCorrectParam). A human player's PlayerIns gets no NpcParam when its creation id is 0, so no rise in PvP | VERIFIED code, INFERRED that human players are created with id 0 |
| What does a bolus do? | Its SpEffect chain ends on a row of the status's stateInfo with build-up -99999; a negative amount passes the defender-rate step unchanged and the gauge clamps to full. No bolus grants resistance. Poison and rot boluses also end the active ailment | refill VERIFIED (EXE + REGULATION); ending the ailment GAME TEXT, consumer of the parent row's stateInfo 10/11 not traced |
| How long does a bolus take? | Effect on frame 31 of `a000_050000` (TAE event 65 `ConsumeCurrentGoods`); after a hit, an item can start at frame 0 (poise held, additive flinch), 12 (small), 25 (middle), 35 (large), 40 (push) | TAE, Nyasu 1.17 agrees on 31 (COMMUNITY) |
| Do talismans change status? | Defender: `change*ResistPoint` adds to resistance (horn charms, Mottled Necklace, Ailment Talisman), already inside the planner's resistances. Attacker: nothing raises build-up or proc HP; the exultations raise damage after a nearby proc | VERIFIED; exultation trigger INFERRED (section 9) |
| Does a proc make the victim react? | Sleep and madness force their own clip (`a000_005840`, `a000_005850`) through any poise, hyperarmor or guard. Bleed and frost turn a hit the victim's poise held into a small stagger (roll on frame 10), unless Stamp stance, Endure, Oath of Vengeance or Seppuku is active. Poison and rot do nothing (section 11) | VERIFIED bit and env plumbing, COMMUNITY branch logic with its constants read in the installed bytecode, TAE timings |
| How much does a shield block? | On a guarded hit that does not break the guard, each row's build-up is multiplied by `1 - min(1, max(0, GuardResist * ReinforceRate / 100)) * cancel` and floored at 0, where `GuardResist` is the guarding weapon's `*GuardResist`, `ReinforceRate` its `ReinforceParamWeapon.*GuardResistRate` / `*GuardDefRate` (1.0 or 0.95, constant over upgrade levels), and `cancel` = `(100 + attacker weapon.guardCutCancelRate) / 100 * (100 + AtkParam.guardCutCancelRate) / 100`. A Buckler (19) lets 81% through. The `*_MaxCorrect` DEX bonus is in the code but multiplied by CalcCorrectGraph 162, which is flat 0. No PvP term (section 1c) | VERIFIED |
| Can a roll avoid the proc? | The HP of a proc is zeroed while the victim's roll i-frames are on, but in PvP the hit packet still builds up and procs, and the forced reaction still interrupts the roll (section 11) | VERIFIED code, frame order INFERRED |

## 1. The chain, per hit (EXE)

1.16.2 address first, 1.17.1 second. The 1.17.1 addresses come from
`scripts/map-rvas-1162-to-1170.py` (every one below 0xafefe9, so 1.17.0 == 1.17.1) and were
byte-checked where noted.

```
FUN_140d24b10 / (stores at 0x140d264f8, 0x140d26520)   build AttackDamageInfo from the AtkParam
  +0x13c = AtkParam.statusAilmentAtkPowerCorrectRate * 0.01
  +0x140 = AtkParam.statusAilmentAtkPowerCorrectRate_byPoint * 0.01
  +0x74[13] hit SpEffect ids:
     0..4   five consecutive AtkParam ints (spEffectId0..4; from FUN_140d24440)
     5..7   weapon[+0x44+4k] + reinforce[+0x50+k]   = spEffectBehaviorIdk + spEffectId(k+1)
            (only when the hit is from the weapon: byte at [rbp+0xd4] == 0)
     8..9   -1
     10..12 from the weapon gaitem (FUN_140673e80; not identified)
     all -1 when AtkParam.disableHitSpEffect
  +0x120..+0x138 rawStatus = PlayerIns vtable +0x358 (0x140651d40) -> FUN_1406832a0 out[6..12]:
     arcane multiplier (FUN_140690b40) for poison/bleed/sleep/madness, 1.0 for the rest,
     times the weapon durability factor
  +0xb0 = FUN_14068d1b0 / 0x14068e000: HasStatsForWeapon ? 1.0 : 0.2  (0x14329e64c / 0x1432a190c)

AttackDamageInfo::CalculateDamageCorrections 0x140684d70 / 0x140685bc0
  finalStatuses[s] = rawStatus[s] * (both ShouldUsePvPDamage ? weapon.vsPlayerDmgCorrectRate_<s> : 1)

CalculateDamage2 0x1404483b0 / 0x140448910, per hit SpEffect slot i with row R:
  ctx[s]   = finalStatuses[s]
  ctx[0xe] = dmg+0x244 (hit-part rate, 1.0 unless a damaged part is hit) * dmg+0xb0
             if R.isUseStatusAilmentAtkPowerCorrect (row +0x259 bit 0):
               i >= 5: * dmg+0x13c                      (cmp r12d,5 at 0x140448d1a / 0x14044927a)
               i <  5: * dmg+0x140 * dmg+0x13c
  then the attacker's grease row: first active attacker SpEffect with stateInfo 152/153
  (FUN_1404f71e0) -> its atkOccurrenceSpEffectId, with both rates like slots 0..4

FUN_1403fade0 / 0x1403fb010: SpecialEffect::Apply(R) on the victim, then
FUN_14043daf0 / 0x14043e050 (CSChrResistModule):
  status = dispatch on R.stateInfo
  amount = R.<status>AttackPower (FUN_140d4ffd0) * ctx[0xe] * ctx[s]
  defender immune if any active SpEffect has the status's disable flag (FUN_1404f9f90)
  amount = FUN_14043e630: max(0, (1 - guardCut) * amount * product(defender SpEffect *DefDamageRate))
           (row +0x360..+0x378, FUN_140d501a0 / 0x140d51f50); guardCut only on a guarded hit,
           read from the guarding weapon (section 1c)
  FUN_14043d8a0 / 0x14043de00(status, (int)amount):
     gauge -= amount; if gauge < 1: proc, count = min(count + 1, 5), NPC ResistCorrect raise,
     gauge = resistance; return "keep the SpEffect"
     else gauge = min(resistance, gauge); return "remove the SpEffect"
```

Consequences worth stating plainly:

- Every row is truncated to an int separately, before it is subtracted.
- The proc effect is the row that emptied the gauge. There is no separate "hemorrhage" row.
- The gauge resets to full on a proc, so build-up past the threshold is thrown away.
- Attacker SpEffects never scale status: the SpEffect attack-power vector (`FUN_1404f3c60`)
  multiplies element damage only.

## 1a. A status that is already active (EXE + REGULATION)

`FUN_1403fade0 / 0x1403fb010` calls `CS::SpecialEffect::Apply 0x1404fa8e0 / 0x1404fb6b0` on the
victim first and returns false when it returns < 0, before `FUN_14043daf0` runs. `Apply` returns
-1 when `CheckApplyConditions 0x1404fc4e0 / 0x1404fd2b0` fails, and that check fails whenever
`FUN_1404fc690 / 0x1404fd460` does:

```
FUN_1404fc690: if new.spCategory >= 10000 (movzx r10d,[r11+0x13e]; mov eax,0x2710; cmp r10w,ax)
                 for each entry with (flags & 0x800c0003) == 0:
                   if entry.spCategory == new.spCategory -> refuse
```

The `mov eax, 0x2710; cmp r10w, ax` bytes are at 0x1404fc6a3 in `eldenring-deobf.bin` and at
0x1404fd473 in `eldenring-deobf-1.17.1.bin` (the only hit for the prologue there); the selftest
reads both.

| status | category | proc row effectEndurance = lockout (weapon rows) |
|---|---|---|
| poison | 10004 | 90 s (244), 30 s (28) |
| scarlet rot | 10005 | 90 s (50) |
| bleed | 10003 | 1 s (748); the grease rows 0.1 s |
| frost | 10007 | 30 s (250) |
| sleep | 10009 | 60 s (6), 10 s (3) |
| madness | 10010 | 1 s (20) |

(Regulation 1.17.1; the grease rows carry the same categories.) What follows:

- While a proc is live, a hit of that status does nothing to the gauge: not blocked-but-counted,
  not held. The gauge was reset to full by the proc and the per-frame refill keeps it clamped
  there, so when the entry expires the next proc needs a full resistance again.
- No refresh and no stack: the add path (`FUN_1404fd090`, buffs.md section 3) is never reached.
  Had it been reached, poison / rot / bleed would clash on stateInfo (R2 (b)) and R3
  (`FUN_140500c40`) has no slot rule for categories >= 10000, so the result would be the same
  refusal; R1 decides first.
- The bolus rows are category 0, so R1 does not refuse them. Their last row (3061 stateInfo 2,
  3071 stateInfo 5) would clash with a live poison / rot entry under R2 (b), which suggests the
  parent row (stateInfo 10 / 11) removes the ailment first (INFERRED; the game text says it cures).
- Only the live entry's own category matters: a live rot proc does not stop poison build-up.

## 1b. Who a row can land on: the target flags (EXE + REGULATION)

Question that prompted it: 11 of the 1512 status rows (`spCategory` 10003..10010) have
`effectTargetPlayer` 0, Chilling Mist's 880/881/829, Hoarfrost Stomp's 1800, DMGS 1724 and
Frostbite 6700 among them, all with `effectTargetAI` 1. If `effectTargetPlayer` meant "never on a
player character", Chilling Mist frost could not build up in PvP. It does not mean that, because
nothing reads it.

Paramdef (`SpEffect.xml`): `effectTargetSelf..Ghost` are byte +0x15f bits 0..6 (labels Suo Shu  Zi Fen  /
Wei Fang  / Di , Cao Zuo  PC / Cao Zuo  AI, Zhuang Tai  Sheng Cun  / Quan go-suto: affiliation self / ally / enemy, controlled by
a player / by AI, alive / any ghost). `effectTargetAttacker` is +0x160 bit 1. The newer set is
byte +0x16c: bit 0 `effectTargetOpposeTarget` (*Di Dui , hostile), 1 `FriendlyTarget`, 2
`SelfTarget`, 3 `PcHorse`, 4 `PcDeceased`.

What the code tests, 1.16.2 / 1.17.1 (all below 0xafefe9; the selftest byte-checks the two
+0x16c reads in both images):

```
FUN_1403fade0 0x1403fade0 / 0x1403fb010 (victim, id, attacker)    attacker nulled unless a Chr
  CS::SpecialEffect::Apply 0x1404fa8e0 / 0x1404fb6b0
    CheckApplyConditions 0x1404fc4e0 / 0x1404fd2b0 (victim, attacker), all must hold:
      validateTeamTypeRelationshipWithSpEffect 0x14051a9f0 / 0x14051b7f0 (attacker, victim, row)
        -> canTeamTypeHitAnother 0x14051ac10 / 0x14051ba10(attacker, victim,
             oppose = +0x16c bit 0, friendly = bit 1, self = bit 2)
             CSTeamTypeRelation[team(attacker)][team(victim)]->Validate(flags, attacker == victim)
      FUN_1404fc690 R1 category (section 1a)
      +0x259 bit 7 isCheckAboveShadowTest -> victim not above the shadow-test threshold (FUN_1403f4950)
      +0x32b wetConditionDepth -> victim wetness
      +0x16c bit 3 effectTargetPcHorse -> IsSomeonesHorse(victim) 0x1403f4370
    then the vow-type check marks the entry, it does not refuse it
CalculateDamage2 slot loop, before the apply core:
  +0x160 bit 1 effectTargetAttacker -> FUN_1403e8b70 on the attacker, skipped on the victim (0x140448aed)
  +0x352 bit 6 -> skipped when the victim's NpcParam byte +0x26a is set (an NPC flag; a human
                  player has no NpcParam, section 2)
```

- **The damage check is the same test.** `FUN_1404443e0` (does this hit hurt the target) calls
  `getTeamTypeRelationshipWithAtkParam` 0x14051a980, which passes the `AtkParam` byte +0x81 bits
  0..2 (`opposeTarget`, `friendlyTarget`, `selfTarget`) to the same `canTeamTypeHitAnother` in the
  same (attacker, victim) order. So a status row with `effectTargetOpposeTarget` 1 lands on every
  victim an `opposeTarget`-only attack can damage, whichever team pair that is; host and invader
  are the pair a PvP hit damages (VERIFIED: same function, same inputs).
- **No reader of the legacy byte.** `scripts/find-deobf-field-access.py 0x15f` (which now also
  decodes `test`, `and`, `or`, `cmp` and the other ALU forms) finds six byte reads in each image,
  none of a SpEffect row: a `NetChrSetSync` copy, `GameSystemCommonParam`,
  two `EquipParamProtector` getters, and `FUN_140d507d0`, which reads bit 7 (`disableSleep`).
  `scripts/find-deobf-covering-loads.py 0x15f` lists every wider load reaching that byte (2750 in
  1.16.2, 2728 in 1.17.1); none of them shifts, masks or bit-tests a bit of it. VERIFIED for every
  `[reg + disp]` read; a read through a pointer already advanced into the row would not be found
  (INFERRED absent).
- **The regulation agrees.** 3570 rows have all seven legacy bits 0, and 3548 of them set one of
  the +0x16c bits. Among them are the Great Rune effect rows from 600 up, including Morgott's 620
  (`maxHpRate` 1.25) and Radahn's 610 (1.15), which act on the player. A gate on those bits would
  make them apply to nobody.
- Ghidra on the 1.16.2 dump numbers the bits of +0x16c and +0x259 from the wrong end: it prints
  the +0x16c bit 0..2 reads as `isIgnoreNoDamage` / `isWaitModeDelete` / `isContractSpEffectLife`
  and the +0x259 bit 7 test in `CheckApplyConditions` as `isUseStatusAilmentAtkPowerCorrect`.
  Read the shift and mask, not the name.

So for a human victim in PvP, the only flag of the set that decides anything is
`effectTargetOpposeTarget` (plus `effectTargetAttacker` and `effectTargetPcHorse`, which move the
row elsewhere). `effectTargetPlayer` and `effectTargetAI` decide nothing on either machine: the
apply core `FUN_1403e8c90` reaches the same `CheckApplyConditions` whichever client runs it
(which client applies a PvP hit is still the open question of section 2).

The calculator enforces it: `hostile_target_refusal(row)` drops a row with
`effectTargetOpposeTarget` 0, `effectTargetPcHorse` or `effectTargetAttacker` from
`hit_buildup` and `proc_row_for`. Of the 530 status rows reachable from `AtkParam_Pc`
`spEffectId0..4`, `EquipParamWeapon.spEffectBehaviorId0..2` + `ReinforceParamWeapon` (levels
0..25) and any row's `atkOccurrenceSpEffectId` (weapon buffs and greases), none is refused, so no
ranking number changes; the selftest asserts that list stays empty.

## 1c. A blocked hit: the shield's status cut (EXE + REGULATION)

1.16.2 address first, 1.17.1 second. The 1.17.1 functions were located with
`scripts/map-rvas-1162-to-1170.py` and then compared byte for byte; the only differences are
RIP-relative displacements, except where noted.

```
CalculateDamage 0x1404472b0 / 0x140447810
  AttackDamageInfo+0x258 (guarded) = 1 when the guard test FUN_140448fc0 passes,
  then = 0 again if stamina <= the guard's stamina damage and the guard-break vfunc says break.
  A guard-broken hit therefore builds up in full.                                   VERIFIED
FUN_140d24b10 (AttackDamageInfo build) -> AttackDamageInfo+0x5c = FUN_140686f30 / 0x140687d80:
  attacker (a ChrIns, or the owner of a bullet) vtable +0x398; 1.0 when there is none.
  PlayerIns +0x398 = FUN_140652610 / 0x140653460:
    cancel = (100 + attackerWeapon.guardCutCancelRate) * 0.01
           * (100 + AtkParam.guardCutCancelRate) * 0.01
    returns 1.0 instead when AttackDamageInfo+0xf4 and (+0x10a ? +0x10b : 1) are set (fields not identified)
CalculateDamage2 0x1404483b0 / 0x140448910: ctx+0x3c = dmg+0x5c (cancel), ctx+0x40 = dmg+0x258 (guarded)
FUN_14043daf0 / 0x14043e050, per status s (0 poison, 1 rot, 2 bleed, 3 death blight, 4 frost, 5 sleep, 6 madness):
  guardCut = guarded ? victim vtable +0x3e8 (s, cancel) : 0       (call [r8+0x3e8], 7 sites in 1.17.1)
  defRate  = FUN_1404f3d70 / 0x1404f4b40(victim SpEffects, s, guarded)
  amount   = FUN_14043e630 / 0x14043eb90(amount, guardCut, defRate)
           = max(0, (1 - guardCut) * amount * defRate)   (a NaN product returns the constant at 0x143c30638)
PlayerIns +0x3e8 = FUN_140655d80 / 0x140656bd0 (vtable 0x142a7cb40 in 1.16.2; slot +0x178 is the
0x1404f1180 section 2 names, which pins the table):
  weapon   = GetEquipmentEntry(GetGuardReferenceHandSlot())        the weapon that is guarding
  base     = (s8) weapon.<s>GuardResist * reinforce.<s>Rate        FUN_140d53f40 / 0x140d55cf0
  bonus    = HasStatsForWeapon ? FUN_140689180 / 0x140689fd0(maxCorrect, 162, effective DEX, properAgility) : 0
  guardCut = cancel * min(1, max(0, (base + bonus) * 0.01))
```

Field offsets, read from the code (the paramdef layout `dump-param-rows.py` uses drifts by one byte
from 0x181 on, so its labels for the last three bytes and the five `*_MaxCorrect` floats are off;
read raw bytes at these offsets):

| status | EquipParamWeapon `*GuardResist` (s8) | `*_MaxCorrect` (f32) | ReinforceParamWeapon rate (f32) |
|---|---|---|---|
| poison | +0x100 | +0x1bc | +0x3c `poisonGuardResistRate` |
| rot | +0x101 | +0x1c0 | +0x40 `diseaseGuardResistRate` |
| bleed | +0x102 | +0x1c4 | +0x44 `bloodGuardResistRate` |
| death blight | +0x103 | +0x1c8 | +0x48 `curseGuardResistRate` |
| frost | +0x196 | +0x1cc | +0x64 `freezeGuardDefRate` |
| sleep | +0x192 | +0xb0 | +0x74 `sleepGuardDefRate` |
| madness | +0x193 | +0xb4 | +0x78 `madnessGuardDefRate` |

The 1.17.1 getters are `0x140d55cf0` (guard resist, same seven `movzx` offsets), `0x140d55c70`
(`*_MaxCorrect`) and `0x140d58290` (reinforce rate). All three moved by +0x1db0, not the +0x1d40
the mapper proposed, so they were found by their byte patterns (one hit each).

The DEX bonus (`FUN_140689180`):
`maxCorrect * CalcCorrect(162, DEX) / 100 * min(DEX - properAgility, cap - properAgility) / (cap - properAgility)`,
`cap` = `PlayerCommonParam.guardStatusCorrect_MaxStatusVal` (+0x36, 70 in 1.17.1). Graph 162 has
`stageMaxGrowVal0..4` all 0, so the bonus is 0 for every DEX and the `*_MaxCorrect` values (15 on
the Buckler, for all seven statuses) do nothing in the shipped regulation. VERIFIED (code + params).

Regulation (installed 1.17.1):

- Reinforcement does not scale the cut with upgrade level. Of 939 `ReinforceParamWeapon` rows, 731
  have all seven rates 1.0; types 400..800 have 0.95 on all seven, 900 / 1000 / 1100 have 0.95 on
  all but frost / poison and rot / bleed. Every type is constant over its +0..+25 rows. VERIFIED
- The Buckler (30000000) holds 19 for all seven statuses (raw bytes at the offsets above), so a
  guarded hit keeps `1 - 0.19` = 81% of each row's build-up before the int truncation. VERIFIED
- `guardCutCancelRate` is non-zero on 18 weapon bases: -50 on 1060000, 1130000, 2080000, 7020000,
  7100000, 9500000, 9680000, 9690000, 18130000, 19000000, 19010000, 19020000, 19060000, 19500000,
  19690000, 22020000 and -30 on 22500000, 22690000 (all their reinforce/affinity rows). A -50
  weapon halves the shield's cut (Buckler: 90.5% gets through). `AtkParam.guardCutCancelRate`
  multiplies in the same way; its rows were not counted. VERIFIED
- With a positive cancel the product can exceed 1; `FUN_14043e630` floors the result at 0, so a
  guard cannot add build-up. INFERRED that no shipped row reaches that.

What the guard branch does not do:

- `AtkParam.guardRate` and the weapon's physical `*GuardCutRate` / `guardLevel` play no part; only
  the per-status guard resist, the reinforce rate, the DEX bonus and `cancel` do. VERIFIED (the
  PlayerIns +0x3e8 body reads nothing else).
- No PvP branch: neither `FUN_140655d80` nor `FUN_14043e630` nor `FUN_140652610` tests the
  PvP state. The only PvP term in status build-up stays `vsPlayerDmgCorrectRate_*`, applied earlier
  on the attacker's side (section 1). VERIFIED for the code read; which client evaluates the guard
  in PvP is the same open item as the gauge's.
- Two defender SpEffect stateInfos only count on a guarded hit: `FUN_1404f3d70` skips rows of
  stateInfo 158 and 204 unless the hit is guarded, so their `*DefDamageRate` multiplies a blocked
  hit only. Which rows carry 158 / 204 was not enumerated. VERIFIED code
- Status 3 has a second dispatch, stateInfo 118 (`0x76`), that subtracts from the death-blight
  gauge with no guard and no defender rate. VERIFIED code; what applies stateInfo 118 not traced.
- An NPC guarding uses its own class's +0x3e8; only the PlayerIns override was read.

## 2. Decay and resistance (EXE + REGULATION)

`CSChrResistModule` (0xc0 bytes, vtable 0x142a361a0): `+0x10` gauge[7], `+0x2c` resistance[7],
`+0x48` refill remainder[7], `+0x64` proc count[7], `+0x80` ResistCorrect add[7], `+0x9c`
ResistCorrect rate[7], `+0xb8` proc bits. Index order: poison, rot, bleed, death blight, frost,
sleep, madness.

- Refill `FUN_14043e440 / 0x14043e9a0`, called from the per-frame update `FUN_1404016d0` with no
  condition: `gauge = clamp(gauge + FD4Time::SetScaled(rate, frameDelta) + remainder, 0,
  resistance)`, fractional part carried. Rate `FUN_14043e740 / 0x14043eca0`: the `_Player`
  column for the main player or a character whose vtable check passes (the dump names it
  `isChrEventIdlessThan9998`; the same check `CalculateDamage2` uses to class a dealer as a
  player), else `_Enemy` (5 for all seven).
- Resistance is `CalcTotalResistance` (defense.md section 5) through PlayerIns vtable +0x5b0
  (0x140655060). A change of resistance keeps the consumed part of the gauge (`FUN_14043e530`).
- NPC-only raise after a proc: `FUN_14043ea10` needs `GetNpcParam()`; PlayerIns returns
  `player_npcParam` (vtable +0x178 = 0x1404f1180), which the PlayerIns constructor
  (0x14064fe40) sets only when the creation `npcParamId != 0`. Human-controlled players are
  expected to have 0 (INFERRED; the creation path was not traced).

Which client runs the gauge in PvP is not traced. The damage path is the victim's
`CSChrDamageModule`, and the refill picks the `_Player` rates for the main player, so the victim's
own machine with player rates is the reading this doc uses (INFERRED).

## 3. What a proc costs (EXE + REGULATION)

`FUN_1404f7d00` -> `FUN_1404fb920`: per ticking entry, `maxHP * changeHpRate / 100 +
changeHpPoint` (as an int), scaled by the defender's `bloodDamageRate`, `freezeDamageRate`,
`sleepDamageRate`, `madnessDamageRate` (`FUN_1404fb3a0`, byte / 100). Those rates are 100 except on
SpEffects 95000..95031, which only NpcParam references (143 NPC rows carry 95000); they are the
boss proc-damage cuts, not a PvP rate.

| status | weapon rows | HP per proc | other effect |
|---|---|---|---|
| bleed | 691 | 15% + 100 | one tick (endurance 1 s, interval 2 s) |
| bleed | 54 | 15% + 200 | Blood-affinity "Low - Innate" rows among them |
| bleed | 3 | 7% + 30 | |
| frost | 250 | 10% + 30 | every `*DamageCutRate` 1.2 for 30 s: +20% damage taken |
| poison | 242 | 0.07% + 7 per s, 90 s | ~90 ticks (INFERRED from endurance / interval) |
| poison | 28 | 0.14% + 14 per s, 30 s | |
| scarlet rot | 50 | 0.18% + 15 per s, 90 s | |
| madness | 20 | 15% + 100 | 10% + 30 FP |
| sleep | 6 / 3 | none | 10% + 30 FP / 15% + 50 FP, cycles SpEffect 102371 / 102321 |
| death blight | 0 weapon rows | | skills only (e.g. Eclipse Shotel 1789 -> replace 70, stateInfo 117) |

At the RL 150 PvP median HP (1946) a 15% + 100 bleed is 392, a 15% + 200 bleed 492, a frost 225
plus the x1.2 window.

## 4. The RL 150 PvP defender (MEASURED)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --corpus --rl 150`: 1074
PvP builds of RL 140..160 (same PvP rule as `er-builds-optimize.is_pvp`) with a planner
`computed.resistances`. The planner's resistances agree with `er-mechanics-defense.py` on 92.6%
of values (defense.md section 7).

| group | statuses | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|---|
| immunity | poison, rot | 267 | 292 | 327 | 351 | 373 |
| robustness | bleed, frost | 247 | 292 | 332 | 381 | 418 |
| focus | sleep, madness | 188 | 228 | 256 | 299 | 349 |
| vitality | death blight | 225 | 250 | 271 | 284 | 305 |
| max HP | | 1704 | 1887 | 1946 | 2174 | 2470 |

Hits to proc from a full gauge, back to back (no refill), by per-hit build-up:

| build-up / hit | robustness p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|
| 29 (45 at rate 65) | 9 | 11 | 12 | 14 | 15 |
| 38 (Drawstring Blood Grease) | 7 | 8 | 9 | 11 | 11 |
| 45 (Uchigatana) | 6 | 7 | 8 | 9 | 10 |
| 55 (Bloodhound's Fang) | 5 | 6 | 7 | 7 | 8 |
| 76 (Rivers of Blood +10, ARC 60) | 4 | 4 | 5 | 6 | 6 |
| 80 (Drawstring Freezing Grease) | 4 | 4 | 5 | 5 | 6 |
| 108 (Blood Uchigatana +25, ARC 45) | 3 | 3 | 4 | 4 | 4 |
| 135 (Dark Moon GS + DS Freezing) | 2 | 3 | 3 | 3 | 4 |

The refill adds up over spaced hits: at bleed's 7 per second a 1.5 s gap returns 10 points, so
the 45-bleed Uchigatana R1 needs 10 hits at one per 1.5 s instead of 8 back to back
(`--interval 1.5`).

## 5. Expected damage per hit

Two equivalents, both returned by `status_per_hit`:

- `hp_per_hit` = proc HP * build-up / resistance. The proc spread evenly over the gauge; the
  right number to add to a hit's damage when ranking sustained pressure.
- `hp_per_hit_discrete` = proc HP / hits to proc. Whole hits from a full gauge; the right number
  for "this string procs or it does not".

Neither includes frost's +20% damage taken; `damage_taken_mult` (1.2) is returned so a caller can
apply it to the hits that land during the 30 s window.

## 6. Top STR/Quality PvP weapons at RL 150 (MEASURED + VERIFIED)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --top --rl 150`. Right-hand
melee weapons of the 1074 PvP builds, Heavy or Quality affinity, plus Standard ones in builds
where STR >= DEX or both are 30+. Each weapon at its max level with the median damage stats of
the builds that carry it, R1 #1, grip by majority, against the median robustness 332 and HP 1946.
Cells are build-up per R1, then R1s to proc and HP-equivalent per R1 (`hp_per_hit`) in brackets.
"DS" = Drawstring (38 bleed / 80 frost before scaling; the regular greases are 30 / 63).

| weapon | affinity | builds | grip | R1 rate | bleed/R1 | R1s (HP/R1) | frost/R1 | R1s (HP/R1) | + DS Blood Grease bleed | R1s (HP/R1) | + DS Freezing Grease frost | R1s (HP/R1) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Greatsword | Heavy | 35 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Cleanrot Knight's Sword | Heavy | 25 | 2H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Shamshir | Heavy | 9 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Lance | Heavy | 9 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Ordovis's Greatsword | Standard | 8 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Ruins Greatsword | Standard | 8 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Bloodhound's Fang | Standard | 7 | 2H | 100/100 | 55 | 7 (65) | 0 | - | 93 | 4 (110) | 80 | 5 (54) |
| Devonia's Hammer | Standard | 7 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Blasphemous Blade | Standard | 7 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Coded Sword | Standard | 7 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Greatsword of Damnation | Standard | 6 | 2H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Star Fist | Heavy | 6 | 1H | 100/100 | 45 | 8 (53) | 0 | - | 83 | 4 (98) | 80 | 5 (54) |
| Ancient Meteoric Ore Greatsword | Standard | 6 | 1H | 100/100 | 0 | - | 0 | - | 46 | 8 (54) | 80 | 5 (54) |
| Cipher Pata | Standard | 6 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Claymore | Heavy | 6 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Great Club | Standard | 6 | 2H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Sword of Light | Standard | 5 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Royal Greatsword | Standard | 5 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Sword Lance | Heavy | 5 | 2H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Banished Knight's Halberd | Heavy | 5 | 1H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Marais Executioner's Sword | Standard | 5 | 1H | 100/100 | 0 | - | 0 | - | 53 | 7 (63) | 80 | 5 (54) |
| Red Bear's Claw | Standard | 4 | 1H | 100/100 | 45 | 8 (53) | 0 | - | 83 | 4 (98) | 80 | 5 (54) |
| Dark Moon Greatsword | Standard | 4 | 1H | 100/100 | 0 | - | 55 | 7 (37) | 38 | 9 (45) | 135 | 3 (91) |
| Horned Warrior's Greatsword | Standard | 4 | 2H | 100/100 | 0 | - | 0 | - | 38 | 9 (45) | 80 | 5 (54) |
| Ripple Crescent Halberd | Standard | 4 | 1H | 100/100 | 0 | - | 0 | - | 97 | 4 (114) | 80 | 5 (54) |

What the table says:

- 20 of the 25 most used STR/Quality melee weapons carry no status at all; for them status is
  purely a grease decision. Drawstring Freezing Grease (80 per R1, 5 R1s, about 54 HP per R1 plus
  the x1.2 window) out-values Drawstring Blood Grease (38, 9 R1s, about 45 HP per R1) against the
  median, and the gap widens on high-robustness defenders because frost needs fewer hits.
- A grease's bleed or poison is multiplied by the weapon's own arcane multiplier: Ancient Meteoric
  Ore Greatsword (46), Marais (53) and Ripple Crescent Halberd (97) turn the same 38 into more.
  Frost never scales.
- Bloodhound's Fang, Star Fist and Red Bear's Claw add their innate bleed to the grease's; the
  sum procs in 4 R1s.
- The grease row and the weapon row are separate applications: when the weapon row empties the
  gauge first, the grease row's build-up lands on the refilled gauge. `hit_buildup` sums them,
  which can read one hit short in that case.

## 7. Per-attack rate: where it is not 100 (REGULATION)

Of 11017 `AtkParam_Pc` rows, `statusAilmentAtkPowerCorrectRate` is 100 on 7113, 65 on 1434, 0 on
737, 50 on 470, 80 on 174, 75 on 153, 200 on 94, 150 on 72. On weapon rows the non-100 values
cluster in judge ids 800..899 (65 on about 45 of each id), which are the powerstance attacks
(`AttackDualLight1..6`, TAE 034000..034050, `powerstance-guard.md`) and so build up status at 65%
per hit, and in 305/315 (50 or 65). R1 #1 of every weapon in section 6
and all 17 Uchigatana slots (R1 chain, R2s, running, rolling, backstep, jump, guard counter) are
100. Fist, claw, pata, hand-to-hand and
reverse-hand blade default rows carry 65 more broadly. `statusAilmentAtkPowerCorrectRate_byPoint`
(only for AtkParam SpEffects and greases) is 100 on 9481 rows.

## 8. Interface for the PvP ranking

`scripts/er-builds-pvp.py` builds `ATK.weapon_attacks()` rows per weapon; this module takes those
rows as they are.

```python
ST = _sibling("er-mechanics-status")        # same importlib helper er-builds-pvp.py uses
st_tables = ST.Tables(ar_tables=tables)      # reuse the AR.Tables already loaded
dfn = ST.corpus_defender(ST.corpus_rows(rl=a.rl, window=a.window))
ws = ST.weapon_status(st_tables, row["weapon"], b["aff"], level, stats, row["two"], pvp=True)
for atk in ATK.weapon_attacks(reg, wid, "both" if row["two"] else "one", level):
    s = ST.status_per_hit(st_tables, ws, atk, dfn, grease=None, interval=None)
    # s = {status: {'buildup', 'resistance', 'hits_to_proc', 'proc_hp', 'hp_per_hit',
    #               'hp_per_hit_discrete', 'damage_taken_mult', 'proc_speffect'}}
    extra = sum(v["hp_per_hit"] for v in s.values())
```

- `weapon_status(t, weapon, affinity, level, stats, two_handed, pvp)`: the weapon rows with
  arcane, vsPlayer and requirement factors; independent of the attack, so compute once per build.
- `hit_buildup(t, ws, atk_row, grease, part_rate, defender_rates)`: {status: int} one hit hands
  the gauge. `grease` takes a name from `Tables.greases()` (Blood, Freezing, Poison, Rot,
  Soporific and their Drawstring forms) or any on-attack SpEffect id (a weapon-buff skill such as
  Seppuku's 1756 works the same way).
- `status_per_hit(t, ws, attack, defender, grease, interval)`: multiplies by the attack's hit
  windows and extra hitboxes, then gauge and proc as above. `interval` switches on refill.
- `hits_to_proc(per_hit, resistance, interval, recover)`, `proc_effect(t, speffect_id, max_hp)`.
- `corpus_rows(path, rl, window, pvp_only)`, `corpus_defender(rows, q)`, `quantiles(vals)`.
  Rows also carry `tools` (quick item names, None when the build lists none), `talismans`,
  `tags` and `flasks` (the planner's `items.flasks`).
- `Defenders(t, rows, extra_resist)` (with `median_hp`, `fight_engagements`, `fight_hits`),
  `status_expected(t, ws, attack, dfs, grease, gap, react, stagger, chain, engagements, eng_s)`,
  `simulate(...)`, `engagement_lengths(chain)`, `combo_land(combo, stagger)`,
  `talisman_resist(names)`, `status_talismans()`, `bolus_share(t, rows)`, `goods_ready(level)`,
  `cure_frame()`, `cure_opportunity(gap, level)`, `exultation_uptime(t, name, status, gap)`:
  section 9.

The ranking no longer uses `status_per_hit`: it calls `status_expected` (section 9) with a
`Defenders` built from the same corpus rows.

## 9. Expected value over the corpus: refill, spread, talismans, boluses

`status_expected(t, ws, attack, dfs, grease, gap, react, stagger, chain)` is what
`scripts/er-builds-pvp.py --sort score` adds to damage. It models a fight as engagements, the
shape of how PvP defenders play: take the first hit, roll the follow-up, disengage, bolus if they
carry one. Each factor is labelled.

**Engagement (TAE verdicts, INFERRED shape).** One engagement lands the slot's hit plus each
follow-up that is a true combo: `er-mechanics-frame-advantage` `combos` (R1 #n -> #n+1, R2 #1 ->
#2), verdict `true` counted 1, `tie` 0.5, `no` 0 (`COMBO_LAND`), on a held poise or a broken one
by the hit's stagger share (`combo_land`). The first follow-up the defender can roll or guard
ends the engagement. Refill between the combo hits uses the combo gap. Measured on this tree, R1
#1 -> #2 is `no` for the Uchigatana on both a held and a broken poise (gap 18, roll at 10), so
most slots are one-hit engagements.

**Between engagements (VERIFIED refill, INFERRED timing).** `ENGAGEMENT_SECONDS` = 5 s of
neutral. A bolus carrier (the defender's carry, section below) uses it after every engagement:
gauge full, and a live poison / rot / frost ended then. So a carrier is only ever procced by an
engagement whose own build-up reaches their resistance. A non-carrier keeps the gauge, which
refills `resistRecoverPoint_*_Player` x 5 s (bleed 35, rot and poison 20, frost 25): a per-hit
build-up below that never procs them one hit at a time. Measuring the 5 s needs a timeline of
landed hits from real fights (a damage-hook log of invasions, or frame-counted footage).

**One live proc (VERIFIED, section 1a).** While a proc's entry is live, its status builds
nothing. The simulator (`simulate`) refuses those hits and starts the next proc from a full gauge
once the entry expires.

**Fight length (MEASURED inputs, INFERRED rule; schedule since 2026-10-01).**
`Defenders.fight_engagements` = the corpus median max HP / `FIGHT_REF_DAMAGE` rounded up: 1946 /
471.5 = 5 engagements (471.5 is the median `dmg` of the best slot of 324 weapons in the previous
ranking). That is one HP bar with no flask, and it stays as the default of this module's own
commands. The ranking replaces it with the landed-hit schedule of buffs.md section 10
(`er-mechanics-buffs.fight_hits`, set on `Defenders.fight_hits` by
`er-builds-pvp.Mechanics.set_fight`): per fight point, the hits to empty HP plus the crimson
flasks drunk (none in a duel, 10 in an invasion or gank), at most what the point's length leaves
room for at one won engagement in two. At RL 150 the default mix is 5 engagements on a quarter of
the points and 16..22 on the rest (mean 16.5). A proc is credited from its hit to the first of its
expiry, the carrier's cure and the last hit of the fight's last engagement. The status damage
itself is still left out of the kill.

**Per landed hit.** Every (defender group, carrier or not, engagement length) is simulated over
the fight; with a schedule, over each distinct engagement count, weighted by the points that
carry it. `hp_per_hit` = credited proc HP / landed hits (fight engagements x mean engagement
length), pooled over the counts and over all 1074 defenders. `fight_engagements` in the output
is the schedule's mean. `engagements_to_proc` (carrier / non-carrier) and `proc_share` are the
same simulation without a fight end. A longer fight credits more of a long DoT (rot, poison):
the Rotten Greataxe's rot (table below) rises from 0.7 HP per hit at 5 engagements to 25.5 at
10 and 54.8 at 20, so the schedule moves the status term as much as the buff term.

**Defender spread (MEASURED).** Every PvP build of the window is a defender, grouped by (planner
resistance of the status's group, bolus carry): 1074 builds at RL 150. The planner's resistance
already includes armor and talismans (defense.md section 7, 92.6% agreement with
`CalcTotalResistance`). Proc HP uses each build's max HP.

**Talismans.** Defender side (VERIFIED regulation, adoption MEASURED over 1074 builds):

| talisman | adds | worn |
|---|---|---|
| Stalwart Horn Charm +2 | bleed, frost +180 | 7 (0.7%) |
| Clarifying Horn Charm +2 | sleep, madness +230 | 4 (0.4%) |
| Immunizing Horn Charm +2 | poison, rot +180 | 2 (0.2%) |
| Mottled Necklace +2 | six statuses +100 | 1 (0.1%) |
| Ailment Talisman | +350 to a pair for 30-120 s after that status is applied | 1 (0.1%) |
| every other horn charm, Mottled Necklace, +1s, Prince of Death's Pustule/Cyst | 90-190 | 0 |

They are inside each build's resistance, so the spread already carries them; `talisman_resist()`
adds a list to every defender for what-ifs (`Defenders(t, rows, extra_resist)`). The Ailment
Talisman's post-proc +350 is not modelled (one build).

Attacker side: attacker SpEffects never scale status (section 1), and proc HP has no attacker
term (section 3), so no talisman raises build-up or proc damage. The status-triggered ones are
the exultations (Lord of Blood's 76 builds 7.1%, Kindred of Rot's 42 3.9%, Aged One's 7, St.
Trina's Smile 5): x1.12 / x1.2 damage for the buff's `effectEndurance` (20 s / 30 s) after a proc
within 7 m. With `--talismans`, `exultation_uptime` = procs per landed hit x hits landing within
the buff (mean engagement length / `ENGAGEMENT_SECONDS` per second), capped at 1, and `er-builds-pvp.exultation_factor` scales that slot's damage by
1 + uptime x (buffed / plain - 1). The presence gate is VERIFIED; that the defender's proc fires
the presence bullet is INFERRED; madness (Aged One's) and sleep (St. Trina's) are COMMUNITY.

**Boluses (VERIFIED effect, TAE timing, MEASURED carry, INFERRED behaviour).**

| bolus | status | chain | carried (of 357 builds that list quick items) |
|---|---|---|---|
| Stanching | bleed | 3050 (stateInfo 12) -> 3051 | 32.2% |
| Neutralizing | poison | 3060 (10) -> 3061 | 14.3% |
| Thawfrost | frost | 3092 (276) -> 3093 | 10.4% |
| Preserving | rot | 3070 (11) -> 3071 | 9.8% |
| Stimulating | sleep | 3055 (438) -> 3056 | 7.0% |
| Clarifying | madness | 3065 (439) -> 3066 | 5.6% |
| Rejuvenating | death blight | 3090 (stateInfo 118, the death blight refill path) | 2.8% |

Every chain's last row carries -99999 build-up. `FUN_14043e630` returns a non-positive amount
unchanged and `FUN_14043d8a0` subtracts it and clamps to the resistance: the gauge is full again.
717 of the 1074 builds list no quick items at all; the planner does not require them, so those
builds get the measured share as their carry, and builds that list items get 1 or 0.

When a carrier's bolus lands: `goods_ready(level) + 31` frames after the engagement's last hit,
where `level` is that hit's reaction with poise intact and broken (frame-advantage
`reaction_poise_intact` / `reaction_on_break`, weighted by the stagger share), plus
`DOT_CURE_DELAY` 15 frames to press it (INFERRED). A true combo is always shorter than that (the
defender's roll opens first; `er-builds-pvp.py --selftest` checks every true combo it builds), so
no cure happens inside an engagement and `cure_opportunity` survives only as that check.

DoT truncation: a poison or rot proc ticks once on application and then every `motionInterval`
(INFERRED schedule). Credited ticks stop at the carrier's cure, the entry's expiry or the end of
the fight, whichever is first. Bleed, frost and madness are one tick; frost's x1.2 window is not
counted.

What it does to the ranking (RL 150, 1074 defenders, 5-engagement fight, 5 s apart):

| slot | status | engagements to proc: carrier / non-carrier | status HP per hit, old -> new | old score (rank) | new score (rank) |
|---|---|---|---|---|---|
| Rotten Greataxe 2H R1 | rot 65 | never / 7.2 | 271 -> 1 | 2025 (1) | 1435 (10) |
| Antspur Rapier 2H R1 | rot 55 | never / 9.1 | 237 -> 0 | 1423 (18) | 825 (255) |
| Uchigatana 2H R1 | bleed 45 | never / 29.8 | 49 -> 0 | 1280 (39) | 1145 (57) |
| Cleanrot Knight's Sword 2H R1 | none (Heavy) | - | 0 -> 0 | 963 (182) | 963 (165) |

All four are one-hit engagements (no true R1 #1 -> #2). No single hit reaches a carrier's
resistance, so carriers are never procced. A non-carrier needs 7-30 engagements against a fight
of 5, so status almost never procs inside the fight, and a rot proc that does is credited 80 HP
on average instead of 1517. The old model credited every proc at chain speed with no lockout and
no fight end, which is what put rot on top.

The result rests on the two INFERRED constants. Rotten Greataxe / Antspur / Uchigatana status HP
per hit by fight length and neutral time (`status_expected(..., engagements=, eng_s=)`):

| fight engagements | 5 s neutral | 3 s neutral |
|---|---|---|
| 5 | 0.7 / 0.1 / 0 | 0.9 / 0.1 / 0 |
| 10 | 25.5 / 10.1 / 0 | 20.2 / 13.3 / 3.5 |
| 20 | 54.8 / 46.6 / 1.1 | 35.3 / 31.9 / 15.5 |

Even a 20-engagement fight (flasks included) leaves rot at about a fifth of the old credit.

## 10. Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --corpus --rl 150
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --top --rl 150
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py Uchigatana --affinity Blood \
    --level 25 --stats str=14,dex=20,arc=45 --all-slots
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py Claymore --affinity Heavy \
    --level 25 --stats str=60,dex=13 --grease "Drawstring Freezing Grease"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py Uchigatana --level 25 \
    --stats str=18,dex=40 --interval 1.5          # with gauge refill between hits
```

The selftest covers: the seven `_Player` refill rates; the 0.2 constant in both images; base
build-up against the 10 status values `er-mechanics-ar.py` checks against the community
calculator; that no weapon row carries a value outside its `stateInfo`; that every
`vsPlayerDmgCorrectRate` status value is 1.0; the gauge arithmetic; bleed and frost proc values;
the per-attack rate (45 -> 29 at 65); grease addition; the 0.2 requirement penalty; one bolus per
status ending on -99999; the cure frame 31 and item-use frames 12 / 25 / 35; that refill between
hits raises hits to proc; that every status row is one exclusive category per status and the
0x2710 compare is in both images; the simulator (a live proc refuses the next, a carrier facing
one-hit engagements below resistance never procs, a true-combo chain that reaches it does, a
non-carrier accumulates with decay or never procs when decay wins, DoT ticks cut by the fight end
and the cure, a new proc only after expiry); engagement lengths and `combo_land`; that no bolus
fits in a 20-frame gap; the fight length from the corpus; that true combos proc more per hit,
longer neutral means more engagements, and Stalwart Horn Charm +2 lowers the value; that
Preserving Boluses and a short fight cut a credited rot proc.

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-status.py --corpus --rl 150` also
prints the bolus carry shares and the status talismans worn.

## 11. What the victim does when a status procs, and the opening it makes

Tool: `scripts/er-mechanics-proc-opening.py` (`--selftest` 37/37, `--reactions`, `--rank`,
`--weapon`). Static RE on the 1.16.2 dump; the 1.17.1 addresses come from
`scripts/map-rvas-1162-to-1170.py` (all below 0xafefe9) and each listed site was byte-checked in
`eldenring-deobf-1.17.1.bin` by the selftest. Nothing was launched.

### The proc reaches the behavior script (VERIFIED)

```
FUN_14043d8a0 / 0x14043de00   proc: CSChrResistModule+0xb8 |= 1 << index   (or at 0x14043d904 / 0x14043de64)
                              index = poison 0, rot 1, bleed 2, death blight 3, frost 4, sleep 5, madness 6
ApplySpEffectStatusClearFlags 0x14043e250 / 0x14043e7b0: the same bits by stateInfo 2/5/6/116/260/436/437
HksEnv 0x140410820: env 409 (GetDamageSpecialAttribute) -> switch byte 0x1404133a4[409 - 223] = case 0x6b
  -> IsStatusClearFlagSet 0x14043e0d0 / 0x14043e630: test [resist+0xb8], 1 << arg
ResetStatusClearFlags 0x14043d970 / 0x14043ded0, called from the ChrIns update 0x1404011c0 / 0x1404014b0
```

### What the script plays (COMMUNITY decompile, constants checked in the installed bytecode)

`ExecDamage` is the first thing `ExecPassiveAction` runs, and every attack state
(`AttackCommonFunction`), roll state (`EvasionCommonFunction`) and stagger state calls that. It
tests the bits before the guard branch and before `GetBehaviorID(1)`:

| status | reaction | first roll / guard / R1 | clip | damage taken during it |
|---|---|---|---|---|
| sleep | `W_DamageSleepResist` -> `a000_005840`, whatever the level, poise, hyperarmor or guard | 66 / 69 / 66 | 110 | x1.2, SpEffect 54 (TAE 67, f0-115) |
| madness | `W_DamageMad` -> `a000_005850`, the same | 75 / 94 / 94 | 125 | x1.2, SpEffect 55 (TAE 67, f0-130); plus 15% + 100 HP |
| bleed, frost | a hit whose level is 0 (poise held, hyperarmor included) becomes `DAMAGE_LEVEL_SMALL` (`a000_0051xx`) | 10 (7 / 4 / 0 for the 2nd-4th consecutive stagger) / 10 / 7 | 34 | frost: x1.2 for 30 s (proc row cut rates) |
| poison, rot | none (no branch reads bits 0 and 1) | - | - | - |

- The bleed/frost promotion is skipped while SpEffect 6340 (Stamp stance), 1650 (Endure) or 1851
  (Oath of Vengeance) is active, or during the one-shot skill with `c_SwordArtsID` 136
  (`swordArtsTypeNew` 136 is Seppuku, whose self-bleed it must not stagger; reading the variable
  as `swordArtsTypeNew` is INFERRED). The installed 1.17.1 `c0000.hks` holds the same constant run
  (409, 5, `W_DamageSleepResist`, 6, `W_DamageMad`, 2, 4, ..., 136, 6340, 1650, 1851).
- A hit that already staggers keeps its own level; a bleed/frost proc adds nothing to it.
- A guarded bleed/frost proc goes on to the guard branch with level small, which plays the same
  `W_Repelled_Small` a guarded small hit does. A guarded sleep/madness proc plays the full clip.
- Sleep and madness clips neither count nor reset `DamageCount`, and carry no EzState flag 2..5,
  so their roll is the TAE window, read with `UseChainRecover` off (INFERRED).

### The user's observations against this

| observation | evidence says |
|---|---|
| a frost proc stuns the victim and opens him to follow-ups | Yes, but only by a small stagger: rollable on frame 10. No R1 or off-hand L1 chain in the RL 150 sweep is that fast (0 of 1537 frost and 0 of 910 bleed chains gain a follow-up at delay 0). At a 4-frame PvP delay the dagger-class chains (gaps 12-13) do: 451 frost and 121 bleed chains. What frost does reliably is the x1.2 for 30 s |
| sleep, madness, bleed and frost procs interrupt anything without large hyperarmor | Interrupt: yes. The hyperarmor exception: no. Sleep and madness ignore hyperarmor entirely; bleed and frost promote precisely the hits hyperarmor absorbed. Only Stamp stance, Endure, Oath of Vengeance and Seppuku are exempt, and only from bleed/frost |
| a roll avoids the proc damage of bleed, frost and madness but is still interrupted | Consistent with the code. Status HP is summed by `FUN_1404fb920` with `param_9` = PlayerIns vtable +0x1e8 (0x140656e90 -> `FUN_1403f3ca0`, true while `actionModifiersFlags` bit 1, the JumpTable 8 i-frames, is set); with it set, every positive HP change of that update is zeroed. On the victim's machine `CalculateDamage2` runs the status slots whatever the victim's own immunity (that only sets `+0x25f` and level 0, combo.md section 12), so the proc happens and the reaction branch interrupts the roll. Whether the HP tick runs before the behavior switch in that frame is INFERRED |

Frost's proc rows carry `deleteCriteriaDamage` 9: `FUN_1404f67c0` (called at the end of
`CalculateDamage2`) ends them on a hit whose `AttackDamageInfo+0x26` is 11, the HKS
`DAMAGE_ELEMENT_FIRE` value. So a fire hit takes the x1.2 and ends it (field meaning INFERRED
from the case values 8..11 matching magic, fire, lightning, holy).

Chilling Mist's weapon buff (826 right, 828 left) puts on-attack row 880 on each hit: frost 60,
not the 30 ashes-of-war.md section 5 said (corrected there). The mist cloud is row 829, frost 120.

### The formula

For any sequence of hits j with first hit frames T_j, build-up b_j of status s (any weapon, ash,
grease or skill buff, through `hit_buildup`), link landing chance w_j without a proc and damage
D_j:

```
E[opening_s] = sum_k P_proc(k) * [ (1 - p_iframe) * H_s
                                   + sum_{j>k} D_j * ( m_s(T_j - T_k) * L'_j(k) - L_j(k) )
                                   + (m_window - 1) * N_later(k) * D_engagement ]
L_j(k)  = prod_{i=k..j-1} w_i                               (no proc)
L'_j(k) = 1 while T_j - T_k < E_s + delay (0.5 on equality), then L'_{j-1} * w_{j-1}
E_s     = earliest roll or guard of the proc reaction above; for bleed/frost only on the
          held-poise share and scaled by 1 - p_exempt
m_s     = 1.2 for frost (30 s), 1.2 for sleep/madness inside the clip's SpEffect window, else 1
```

`P_proc(k)` is computed two ways: `string_shares` (every hit lands at T_j, the gauge refills
`resistRecoverPoint_*_Player` per second between hits, over the corpus resistances) and
`fight_shares` (section 9's engagement model: one engagement lands hit 1 and each true follow-up,
engagements 5 s apart, carriers bolus after each, a 5-engagement fight). `H_s` is the proc's HP at
the proc moment (one tick for poison and rot; their later ticks stay `status_expected`'s).
`delay` is the PvP hit-to-reaction delay of combo.md section 12.

### RL 150 (`--rank`, 822 sweep rows and 330 off-hand weapons at the combo reference stats)

E[opening] per chain over the fight, delay 0:

| weapon | hand | source | status | build-up/hit | P(proc) | lock | E[opening] | given a proc on hit 1 |
|---|---|---|---|---|---|---|---|---|
| Ripple Blade | 2H R1 | Drawstring Soporific Grease | sleep | 122 | 0.93 | 66 | 1413 | 1518 |
| Ripple Crescent Halberd | 2H R1 | Drawstring Soporific Grease | sleep | 110 | 0.93 | 66 | 939 | 1012 |
| Fingerprint Stone Shield (Heavy) | 2H R1 | innate | madness | 70 | 0.60 | 75 | 545 | 913 |
| St. Trina's Torch | 2H R1 | innate | sleep | 72 | 0.52 | 66 | 481 | 922 |
| Vyke's War Spear | 2H R1 | innate | madness | 65 | 0.37 | 75 | 481 | 1308 |
| Duelist Greataxe (Cold) | off-hand L1 | Chilling Mist | frost | 213 | 0.90 | 10 | 399 (203 proc, 196 from x1.2 later) | 442 |
| Cold greataxes / greatswords | off-hand L1 | Chilling Mist | frost | 187-213 | 0.90 | 10 | 350-394 | 390-436 |

- Sleep is the largest opening per proc: a 66-frame lock lets three to four chained R1s land at
  x1.2. Given a proc on R1 #1, Drawstring Soporific Grease on fast 2H chains is worth 1500-1935
  (Hand Axe 2H 1935). But at a grease's 40-45 per hit against the median focus 256, with 5 points
  per second of refill between one-hit engagements, it almost never procs in a 5-engagement fight
  (P 0.005); only arcane-scaled sources (Ripple weapons 110-122) and innate sleep/madness reach it.
- Frost (Chilling Mist on a Cold off-hand, or Freezing Grease) procs often but its lock is 10
  frames, so its value is the proc HP plus x1.2 on later engagements, not a follow-up. With a
  4-frame delay the fastest off-hand L1 chains add one: Wakizashi (Cold) + Chilling Mist 523
  (229 of it follow-up), Misericorde 481.
- Bleed at delay 4: claws (Bloodhound Claws Keen + Drawstring Blood Grease 342), Reduvia 266.

Files: `/tmp/claude-1000/-home-banon-projects-er-mods-rs/5fc1b460-61a1-4d80-9ef8-a0faec942af4/scratchpad/proc/rank-150.txt`
(and `.json`), `rank-150-d4.*` for delay 4.

## Not established

- What the bolus parent rows' stateInfo 10 / 11 / 12 / 276 / 438 / 439 do. The game text says
  the poison and rot boluses cure the ailment, which the DoT cut and the carrier's cleared lockout
  rely on; the code that removes the active row was not found.
- Which of the flag bits `0x800c0003` the R1 scan skips; that a kept proc entry carries none of
  them is INFERRED.
- The engagement constants: `ENGAGEMENT_SECONDS` 5 s, carriers using the bolus after every
  engagement, the `tie` weight 0.5, the 5-engagement fight (no flasks, no status damage counted),
  and that each follow-up's stagger share is the standalone hit's (poise damage accumulating over
  a chain is not modelled). Engagement lengths are drawn independently for each engagement.
- The HKS mapping from `goodsUseAnim` 0 to `a000_050000` (Nyasu's data places the bolus behavior
  ids on that clip), and whether a hit on a defender whose poise holds interrupts an item.
- The 15-frame delay before a carrier presses the bolus (section 9).
- Slots 10..12 (`FUN_140673e80` on the weapon gaitem): which rows they hold.
- Which client runs the victim's gauge in PvP, and so whose `_Player` / `_Enemy` column and whose
  frame time apply.
- That human players are created with `npcParamId` 0 (no ResistCorrect raise).
- The tick schedule of a SpEffect (how `effectEndurance` and `motionInterval` turn into ticks,
  including whether a 1 s / 2 s bleed ticks once).
- Which grease wins when two weapon buffs are active: `FUN_1404f71e0` returns the first applicable
  stateInfo 152/153 entry in the SpEffect list, so only one applies, but the list order was not
  read.
- The damaged-part rate at `+0x244` for players. (The guard branch is section 1c; its remaining
  unknowns are the `AttackDamageInfo+0xf4/+0x10a/+0x10b` bypass of `cancel`, the rows carrying
  stateInfo 158 / 204, and the NPC override of the guard-cut vfunc.)
- What stateInfo 117 (death blight's replacement row 70) does to a player.
- The disable-flag reader `FUN_140d507d0` reads bytes whose Ghidra names are stale; which
  SpEffects grant immunity in PvP was not enumerated.
- Section 11: whether the status HP tick (`FUN_140440dc0`) runs before or after the behavior
  script switches a rolling victim into the proc reaction in the same frame (decides whether the
  roll's i-frames still cover the proc HP); that `c_SwordArtsID` is `swordArtsTypeNew`; the
  meaning of `AttackDamageInfo+0x26`; the roll gate of the sleep/madness clips when
  `UseChainRecover` is still set from an earlier stagger. A runtime trace of
  `IsStatusClearFlagSet` (0x14043e630 on 1.17.1) and the HP tick on the victim's machine would
  settle the first.
