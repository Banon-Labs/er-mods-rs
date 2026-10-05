# Chainsaw glitch: damage source and FP cost of a borrowed skill

The bug: hold Wild Strikes (SwordArtsParam 110), swap the right-hand weapon through the equipment
menu mid-skill (for example to the Starscourge Greatsword, whose own skill is Starcaller Cry 1032),
and the loop keeps going with the new weapon's model while FP drains per repeat. This page answers
two questions with static RE only (no launch, no Frida):

- (5) Which weapon, attack rating, scaling, AtkParam row and motion value each repeat's hit uses.
- (6) Which skill row the per-repeat FP charge is read from.

Labels: `VERIFIED` = read in the executable (1.16.2 Ghidra :8765 decompile, then byte-checked in
`eldenring-deobf-1.17.1.bin`) or a value in the installed 1.17.1 `regulation.bin`; `INFERRED` =
follows from verified pieces, not traced end to end; `HKS` = the installed compiled `c0000.hks`
read with `scripts/er-hks-disasm.py`. Every address is 1.17.1 with 1.16.2 in brackets. All of
them sit below rva `0xafefe9`, so 1.17.1 equals 1.17.0 here; the 1.16.2 -> 1.17 step went through
`scripts/map-rvas-1162-to-1170.py`, and every 1.17.1 instruction quoted below was read out of the
1.17.1 image.

## Answer

| question | answer | label |
|---|---|---|
| Weapon a chainsaw hit is computed from | the weapon in the hand at the moment that swing's hit window opens: the new weapon | `VERIFIED` |
| Attack rating and scaling | the new weapon's: its id with upgrade and affinity, its scaling, its durability, the current two-handing state | `VERIFIED` |
| AtkParam row and motion values | Wild Strikes' rows, chosen by the judge id in the Wild Strikes clip and the new weapon's `behaviorVariationId` family | `VERIFIED` |
| Anything captured when the skill started | nothing on the damage path; one attack info is built per hit window | `VERIFIED` |
| FP charged per repeat | the new weapon's skill row (Starcaller Cry 1032), looked up fresh at each reservation; no skill id is cached | `VERIFIED` |
| Which HKS call makes the per-swing reservation | not pinned; every path that can reserve passes only (cast, hand) | `INFERRED` |

Concretely, swapping a Battle Axe +25 (Wild Strikes is its default skill) for the Starscourge
Greatsword +10 at STR 50 / DEX 30 / INT 24:

| loop hit | Battle Axe, normal Wild Strikes | chainsaw on Starscourge Greatsword |
|---|---|---|
| swing 1 (judge 3500) | AtkParam 301400800, MV 126: 527.4 AR x 1.26 = **664.5** standard physical | AtkParam 301401800, MV 107: 577.8 x 1.07 = **618.2** physical + 236.5 x 1.07 = **253.1** magic, **871.3** total |
| swing 2 (judge 3510) | AtkParam 301400810, MV 119: **627.6** | AtkParam 301401810, MV 114: **658.7** physical + **269.7** magic, **928.3** total |
| FP per swing | 2 (110 `useMagicPoint_L2`) | 20 (1032 `useMagicPoint_L2`) |

The numbers are attack before defense, from `scripts/er-mechanics-ar.py` (attack rating) times the
AtkParam correction (motion value), which is how `0x1406840f0` [`FUN_1406832a0`] combines them
(attacks.md section 1). The other factors in that product (durability, SpEffect rates, the
throw/attack-type terms) have the same form on both sides. Defense then applies per element, so the
magic part meets magic defense separately. Both rows use `finalDamageRateId` 10000 and
`atkAttribute` 253, which resolves to the weapon's `atkAttribute` (3, standard, for both weapons).

So the chainsaw hit is exactly "Wild Strikes performed by the Starscourge Greatsword": the
Starscourge's attack rating, split into physical and magic, times Wild Strikes' generic motion
values. It is not the original axe's damage, and it is not Starcaller Cry's damage. The motion
value drops from 126/119 to 107/114 only because axes have their own Wild Strikes rows and colossal
swords do not (section 2). Starting from a weapon outside families 7, 11, 13 and 14 (for example a
greataxe or great hammer), the AtkParam rows are the same before and after the swap, and only the
weapon's attack rating changes.

## 1. Damage: every input comes from the hand at hit-window time

### 1a. The TimeAct attack event resolves the row through the current weapon

`CSChrTaeAnimEvent::AttackBehavior` `0x140426c20` [`0x1404266d0`], TimeAct event 1, decompile:

```c
equipmentSlot = (*param_1->chrIns->_vfptr->GetAttackReferenceHandSlot)(param_1->chrIns);
if ((0x19 < param_2->contentVersion) && (cVar1 != '\0'))
    equipmentSlot = (uint)(cVar1 == '\x01') + LeftWeaponSlot;
iVar6 = (*pCVar3->_vfptr->ResolveBehaviorId)(pCVar3, puVar4[2], equipmentSlot, 0);
...
FUN_1404428f0(pCVar3->componentContainer->damage, local_80, iVar6, *puVar4, puVar4[1],
              equipmentSlot, 1, param_2->Args, local_88, fVar9);
```

1.17.1 bytes: `140426cad call *0x238(%rax)` (hand slot), `140426cde call *0x320(%rax)`
(`ResolveBehaviorId`), `140426d4d call 0x140442e50`.

`PlayerIns::ResolveBehaviorId` `0x1406530d0` [`0x140652280`]:

```c
iVar3 = (*((param_1->chrIns)._vfptr)->GetEquipmentEntry)(&param_1->chrIns, equipmentSlot);
...
::EquipParamWeapon::GetEntry(&local_48, iVar3);
uVar1 = (local_48.equipParamRow)->behaviorVariationId;
iVar3 = (iVar5 * 100000 + uVar1) * 1000 + iVar6;               // own row
if (row missing && 999 < behaviorId)
    iVar3 = ((int)uVar1 / 100 + iVar5 * 1000) * 100000 + iVar6; // family row
    if (that is missing) iVar3 = iVar5 * 100000000 + iVar6;      // generic row
```

1.17.1 bytes: `1406530ea call *0x318(%rax)` (`IsValidBehaviorJudgeID`), `140653108 call
*0x228(%rax)` (`GetEquipmentEntry`). Vtable slot `+0x228` is `PlayerIns::GetEquipmentEntryParamId`
`0x1406577b0` [`0x140656960`]: the two data references to `0x140656960` in 1.16.2 are
`0x142a49700` and `0x142a7cd68`, and `0x142a49700` is `+0x228` from the `ReplayGhostIns` vtable base
`0x142a494d8` (attacks.md section 4). So the BehaviorParam row, and through it the AtkParam id, is
picked from the weapon in that slot when the event fires.

### 1b. One attack info per hit window, filled from the current weapon

`0x140442e50` [`FUN_1404428f0`] looks up that BehaviorParam row and fills a fresh `AttackInfo`:

```c
lVar7 = FUN_140526f40(GLOBAL_DmgMan, *puVar9);
if ((lVar7 != 0) && (*(int *)(lVar7 + 0x38) == param_3)) {   // same row still live: reuse
    ... goto LAB_140442e79;
}
...
LookupBehaviorParam(&local_198, param_3, local_198.isPCBegavior);
InitAttackStruct(&local_188);
iVar5 = (local_198.paramRow)->refId;                          // AtkParam id (refType 0)
FUN_14068ffa0(&local_188, pCVar8, iVar5, param_3, uVar10, param_6, uVar11, param_7);
...
uVar4 = FUN_140526430(GLOBAL_DmgMan, &local_188, *puVar9, param_4);
```

The only reuse is within one event while the resolved row is unchanged; a new swing is a new event
activation and builds a new attack info. Nothing on this path reads the skill that was active when
L2 was first pressed.

`0x140690df0` [`FUN_14068ffa0`], the melee attack-info fill:

```c
param_1->behaviorParamRefId = param_3;            // AtkParam id
param_1->field3_0x10 = param_7;                   // BehaviorParam.category
param_1->field52_0xe4 = param_6;                  // equipment slot
iVar11 = (*param_2->_vfptr->GetEquipmentEntry)(param_2, param_6);   // 110000 if (mask & 0x60)
param_1->weaponParamId2 = iVar11;                 // +0xe8
bVar9 = CS::ChrIns::IsTwoHanding(param_2);
param_1->isTwoHanding = bVar9;                    // +0xf5
GetAtkParam(local_58, ..., param_1->behaviorParamRefId);
FUN_1404f4520(param_2->specialEffect, param_1);   // SpEffect sums, read now
```

1.17.1 bytes: `140690e76 call *0x228(%rax)` then `140690e7f mov %eax,0xe8(%rbx)`.

### 1c. The damage calculation reads the weapon out of that attack info

`0x140652b90` [`FUN_140651d40`], the player's attack-power vcall (`[chr vtable +0x358]`, attacks.md
section 4):

```c
uVar2 = *(undefined4 *)(param_3 + 0xe8);          // weaponParamId2: the held weapon
uVar3 = *(undefined4 *)(param_3 + 0xf0);          // weaponParamId1
fVar7 = CS::PlayerIns::GetWeaponDurability(param_1, equipEntryIndex);   // slot +0xe4 / +0xec
...
FUN_1406832a0(param_2, pPVar6, uVar2, uVar3, fVar7, param_3, bVar4, (param_1->chrIns).specialEffect);
```

1.17.1 bytes: `140652bcd mov 0xe4(%rdi),%ebx`, `140652bdb mov 0xe8(%rdi),%ebp`, `140652be9 mov
0xf0(%rdi),%r14d`. `0x1406840f0` [`FUN_1406832a0`] then builds attack power from that weapon id
(base x `ReinforceParamWeapon` rate x `atk<e>Correction` x 0.01 x stat multiplier ...; attacks.md
section 1). The same id reaches `AttackDamageInfo+0x144` (weapon-buff-leak.md section 1), which is
why the Frida run saw ADI+0x144 equal to the held weapon on melee hits.

### 1d. What the SwordArtsParam row does and does not decide

The skill row picks the TimeAct file (`(600 + swordArtsTypeNew) * 1000000`, ashes-of-war.md
section 1) and the FP cost (section 3 below). It is not read on the damage path above. Once a
Wild Strikes clip (`a610`, `swordArtsTypeNew` 10) is playing, its hit events carry Wild Strikes'
judges 3500/3510 (TAE, `er-mechanics-ashes.py skill "Wild Strikes"`), and those judges are resolved
against whatever weapon is in the hand. That the playing `a610` clip is not replaced when the
weapon changes is `INFERRED` from the user's observation that the loop continues; the animation
selection was not traced.

## 2. Which Wild Strikes rows a weapon gets (regulation 1.17.1)

`BehaviorParam_PC` holds Wild Strikes rows for five variation keys (`VERIFIED`, `er-param-read.py
BehaviorParam_PC`):

| rows | key | weapons (`behaviorVariationId / 100`) | AtkParam |
|---|---|---|---|
| 300700500..510 | family 7 | curved swords (wepType 9) | 3014008xx |
| 301100500..510 | family 11 | hammers (wepType 21) | 3014008xx |
| 301300500..510 | family 13 | flails (wepType 24) | 3014008xx |
| 301400500..510 | family 14 | axes (wepType 17) | 3014008xx |
| 300000500..510 | generic (third fallback) | everything else, including Starscourge Greatsword 4050000 (variation 405) | 3014018xx |

| AtkParam_Pc | MV (all five elements) | poise corr. | | AtkParam_Pc | MV | poise corr. |
|---|---|---|---|---|---|---|
| 301400800 | 126 | 100 | | 301401800 | 107 | 90 |
| 301400810 | 119 | 100 | | 301401810 | 114 | 90 |
| 301400801 / 802 | 52 / 183 | 100 / 400 | | 301401801 / 802 | 45 / 154 | 90 / 400 |
| 301400803 / 804 | 52 / 243 | 100 / 600 | | 301401803 / 804 | 44 / 203 | 90 / 600 |
| 301400805 (no FP) | 50 | 100 | | 301401805 (no FP) | 50 | 90 |

All rows: `hitSourceType` 0 (damage from the weapon), `isAddBaseAtk` 0, `atkAttribute` 253,
`finalDamageRateId` 10000, on-hit `spEffectId0` 6903. Default Wild Strikes weapons are axes
(Battle Axe, Jawbone Axe, Iron Cleaver, Ripple Blade, Celebrant's Cleaver, Sacrificial Axe,
Smithscript Axe) and the Great Omenkiller Cleaver (family 15, generic rows).

Poise damage per hit follows the same weapon: `saWeaponDamage` x `saWeaponAtkRate` x
`atkSuperArmorCorrection` x 0.01 (attacks.md section 2). Starscourge `saWeaponDamage` 6 at
correction 90 against Battle Axe 5 at correction 100. Stamina cost uses `BehaviorParam.stamina`
through `GetConsumeStaminaRate(..., weaponParamId2, ...)` in `0x140442e50`, so it also follows the
new weapon (the tool shows 9 vs 10 on swing 1 for Starscourge vs Greatsword).

## 3. FP: a fresh lookup of the current weapon's skill at every reservation

### 3a. The charge spends a reserved number

`CSChrSwordArtsModule::ConsumeFp` `0x14047fba0` [`0x14047f640`], reached from TimeAct event 330
(`ExecuteThreadOne` `0x14042e6f0` [`0x14042e1a0`], 1.17.1 `14042eb72 jmp 0x14047fba0`):

```c
if (!isNoArtsPointConsume && param_1->activeAowIsNoFpVersion == false
    && 0 < (int)param_1->activeAowFPConsumption) {
    if (HaveEnoughFP(param_1, param_1->activeAowFPConsumption)) {
        CSChrDataModule::AddFp(..., -param_1->activeAowFPConsumption);
        param_1->activeAowFPConsumption = 0;
    }
}
```

1.17.1 bytes: `14047fbe3 cmpb $0x0,0x14(%rbx)`, `14047fbe9 mov 0x10(%rbx),%edx`, `14047fc18 movl
$0x0,0x10(%rbx)`. The charge zeroes the reservation, so every charged swing needs its own
reservation.

### 3b. The only writer of a non-zero reservation looks the weapon up again

`CSChrSwordArtsModule::UpdateActiveAowFpStats` `0x140480070` [`0x14047fb10`]:

```c
ChrIns::GetWeaponGaitemHandle(pCVar3, local_res8, param_3);          // hand
::GaitemLookupResult::GetGaitemWeapon(&local_20, local_res8);
GaitemLookupResult::GetSwordArtsParamForWeapon(&local_20, &local_30); // gem's skill, else weapon's
uVar2 = SwordArtsParamLookupResult::CalculateFpConsumption(&local_30, param_2, specialEffect);
param_1->activeAowFPConsumption = uVar2;                             // +0x10
bVar1 = CanCastAow(param_1, param_2, param_3);
param_1->activeAowIsNoFpVersion = !bVar1;                            // +0x14
```

1.17.1 bytes: `1404800c0 call 0x140674d80` (`GetSwordArtsParamForWeapon`), `1404800dc call
0x14068c070` (`CalculateFpConsumption`), `1404800e1 mov %eax,0x10(%rsi)`, `1404800f6 mov
%al,0x14(%rsi)`. Its arguments are a cast number and a hand. There is no skill-id parameter, so a
skill id cached at the skill's start cannot reach the cost.

Writers of `+0x10` in the module's code (`0x14047f400..0x14047fe00`, 1.16.2): the constructor
`FUN_14047f490` (`xor eax,eax; mov %eax,0x10(%rbx)`), `ConsumeFp` (zero), and
`UpdateActiveAowFpStats`. The only code caller of `UpdateActiveAowFpStats` is `HksAct` (1.16.2
`0x14040d793`, 1.17.1 `0x14040dcc3`); its two other references are `.pdata` and C++ EH state
tables.

`SwordArtsParamLookupResult::CalculateFpConsumption` `0x14068c070` [`0x14068b220`]:
`castNumber` 0..3 picks `useMagicPoint_R1 / R2 / L1 / L2`, then `ceilf(CalculateAowFpConsumptionRate
* cost)`. `GaitemLookupResult::GetSwordArtsParamIdForWeapon` `0x140674dc0` [`0x140673f70`] returns
the mounted gem's `swordArtsParamId` when a gem is mounted, else `EquipParamWeapon.swordArtsParamId`.

### 3c. The HKS entry points

`HksAct` act 2016 is the reservation. 1.17.1: the act dispatch in `HksAct` `0x14040d100`
[`0x14040cbd0`] does `add $0xfffff82e,%edx` (act - 2002) and indexes the table at `0x14040e818`
[`0x14040e2e8`]; entry 14 is `0x14040dc7d` [`0x14040d74d`], which runs:

```
14040dc91  mov $0x2,%edx ; call 0x140bff7d0      ; lua arg 2 = cast number
14040dca0  mov $0x3,%edx ; call 0x140bff7d0      ; lua arg 3 = hand
14040dcc3  call 0x140480070                     ; UpdateActiveAowFpStats
```

The 1.16.2 decompile names that case `ReserveArtsPointsUse`. HKS callers of `act(2016, ...)`:
`SetSwordArtsPointInfo` (line 741, `act(2016, cast, c_SwordArtsHand)`), `SwordArtsOneShot_onUpdate`
(line 12211, only for `c_SwordArtsID == 356`) and `RequestArtPointConsumption` (no static caller in
the script). None passes a skill id.

`HksEnv` `0x140410d50` [`0x140410820`] subtracts 223 and dispatches through the byte table at
`0x1404138d4` [`0x1404133a4`]:

- env 344 -> case `0x4d` -> `0x140411ea8` [`0x140411978`] -> `140411eee call 0x14047ff80`,
  `CanCastAow(cast, hand)` `0x14047ff80` [`0x14047fa20`], which also looks the hand's weapon up
  again. Enough FP is `fp > 0 and fp >= int(cost * 0.5)`.
- env 326 -> case `0x3d` -> `0x140411b27` [`0x1404115f7`] -> `140411b54 call 0x14047fe90`
  [`FUN_14047f930`] -> `0x140674d40` [`FUN_140673ef0`], the `swordArtsTypeNew` of the hand's
  current weapon skill.

`GetConstVariable` (HKS line 18646), run from `Update` every frame, sets `c_SwordArtsID, c_SwordArtsHand
= GetSwordArtInfo()`, and `GetSwordArtInfo` reads env 326 for the hand. So after the swap the
script's own skill id becomes 232 (Starcaller Cry) on the next frame. `SwordArtsLoopLoop_onUpdate`
treats 232 the same way as Wild Strikes' 10 (both fall through the 115/116/193 and 13 checks to
line 13240): the loop continues while L2 is held, env 1001 is positive, and either the
`IsEnoughArtPointsL2` variable is non-zero or `CanCastAow(L2, hand)` holds (HKS bytecode pcs
116-147). Env 1001 was not identified.

### 3d. What a repeat costs after the swap

Every reservation made after the swap reads SwordArtsParam 1032: `useMagicPoint_L2` 20,
`useMagicPoint_R2` 20, `useMagicPoint_R1` -1 (`VERIFIED`, `er-param-read.py SwordArtsParam`). The
loop reserves with `ACTION_ARM_L2`, so each charged repeat costs `ceil(rate x 20)`, ten times Wild
Strikes' 2 (`INFERRED` that the per-swing reservation is an L2 one; the variable and env checks
in the loop all name `ACTION_ARM_L2`). With less than 10 FP, `CanCastAow` fails, the reservation is
marked no-FP and `ConsumeFp` charges nothing, and the loop's continue test then depends on the
`IsEnoughArtPointsL2` variable. A reservation made before the swap and spent after it charges the
pre-swap number (2) once: the reserved number, not the row, is what `ConsumeFp` reads.

The R1 column of 1032 is -1, so an R1 cast reserved on the new weapon is -1, which `ConsumeFp`'s
`0 < cost` test skips; such a reservation charges nothing.

## 4. Not established

- Which HKS call issues the per-swing reservation in the Wild Strikes loop. The loop's own
  `onUpdate` does not call `SetSwordArtsPointInfo` for type 10 or 232; the candidates are
  `ExecAttack`'s `SWORDARTS_REQUEST_*` branches (reached through `ArtsCommonFunction`) and the
  behavior graph. It does not change the answer, because every reservation path is act 2016 with
  (cast, hand).
- Why the `a610` clip keeps playing after the swap (the animation choice, `swordArtsTypeNew` 232
  -> `a832`, and the behavior graph's handling of a running state were not traced).
- The live per-hit values. A Frida check would read `AttackInfo+0xe8` and `behaviorParamRefId` (the AtkParam
  id; its offset was not read) in `0x140690df0` on each loop swing after a swap, and
  `CSChrSwordArtsModule+0x10` after each `0x140480070`.

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py skill "Wild Strikes" --weapon "Starscourge Greatsword"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ar.py "Starscourge Greatsword" --level 10 --stats str=50,dex=30,int=24,fth=10,arc=10
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py SwordArtsParam --row 1032 --fields useMagicPoint_L2,useMagicPoint_R2,useMagicPoint_R1,swordArtsTypeNew
python3 /home/banon/projects/er-mods-rs/scripts/er-hks-disasm.py --dump SwordArtsLoopLoop_onUpdate
```
