# Chainsaw glitch: why a skill survives a right-hand equip

The player holds Wild Strikes (SwordArtsParam 110, `swordArtsTypeNew` 10, FP costs L2 2 / R1 10 /
R2 15), swaps the off-hand slot, opens the menu, equips the Starscourge Greatsword (Starcaller Cry,
SwordArtsParam 1032, `swordArtsTypeNew` 232, L2 20 / R2 20), lets go of L2 in the menu, closes it
and holds L2 again. The HUD now says Starcaller Cry, but Wild Strikes keeps looping with the new
weapon's model and keeps costing FP.

The short answer: the game never stores "which skill is running" on the character. The running
skill exists only as the clip a behavior-graph selector picked when the skill's state was entered.
An equip change never touches the graph. Every other skill lookup (HUD, FP cost, HKS
`c_SwordArtsID`) re-reads the live weapon every frame, so they all move to Starcaller Cry while
the clip stays on Wild Strikes.

Labels: `VERIFIED` = read from the 1.16.2 executable (Ghidra :8765, shift 0) and the call edge or
store re-checked byte for byte in `eldenring-deobf-1.17.1.bin`; `DATA` = read from the player's
behavior graph, HKS or TAE files; `INFERRED` = follows from verified pieces, not traced end to end.
Addresses are 1.17.1 (the installed build), with 1.16.2 in brackets.

Data files caveat. `c0000.hks`, `common_define.hks`, `c0000.behbnd` and the `a610`/`a832` TAEs
come from the 2026-07-13 extraction (`~/er-extract/LOOK_HERE_ALL_ASSETS_20260713`,
`LOOK_HERE_WITCHY_RECURSIVE_20260713`), which predates 1.17. The 1.17.0 extraction
(`~/er-extract/1170-20260830`) holds only `msg`. Nothing in the 1.17.1 executable contradicts these
files, but they have not been re-extracted from 1.17.1.

## Answers

| question | answer |
|---|---|
| What holds the active skill | The skill-loop `CustomManualSelectorGenerator` (CMSG) node of the behavior graph: `argTaeId` (+0xec, e.g. 610040051 for Wild Strikes) and `currentGeneratorIndex` (+0x100). Written by `CustomManualSelector::activate` `0x1419b9980` [`0x1419b7b10`] and by `0x1419bb530` [`0x1419b96c0`]. `CSChrSwordArtsModule` on the character holds only FP bookkeeping. |
| Why the equip does not clear it | The equip path ends in the per-frame ChrAsm sync `0x140660f70` [`0x140660120`], which only sets `toughness->doBigUpdate`, calls `ResetInputQueue`, copies ChrAsm and strips weapon SpEffects. Nothing re-checks the skill, fires a behavior event or touches the CMSG. The stance-loop CMSGs never reselect after activation (`changeTypeOfSelectedIndexAfterActivate` = 0), and if they are re-entered with a skill that has no loop clip they fall back to child 0, which is Wild Strikes' `a610_040051`. |
| Why L2 must be released and re-held | While the menu blocks input, the pad manipulator calls `DisableAll`, which masks every action. `UpdateFromManipulator` keeps a disabled bit set for as long as the button stays physically held, so an L2 held through the menu stays dead (duration 0) after the menu closes. Only a release clears the mask; the next press makes `ActionDuration(L2)` (`env(1108, 3)`) climb again, which is the only thing the stance loop checks to keep looping. |
| Fix site | `CustomManualSelectorGenerator::update` `0x1419ba150` [`0x1419b82e0`]: for `offsetType` 0x12 (sword arts), compare the live arts offset from the resolver `0x14041b0d0` [`0x14041aba0`] against `argTaeId / 1000000` and end the node when they differ. Secondary: the name lookup `0x1419bb2f0` [`0x1419b9480`] must not fall back to child 0 for sword-arts nodes. |

## 1. What a right-hand change runs through

### Menu equip

`VERIFIED`. `EquipItemToChrAsmSlot` `0x140788ab0` [`0x140787c30`] writes only game data and sends
the network packet:

```c
CS::EquipGameData::SetEquipmentEntries(pEVar9,chrAsmSlot,puVar6,itemIdx,true,true,false);
...
BroadCastEquipmentChange(GLOBAL_WorldChrMan->mainPlayerIns);   // 0x140659ae0 [0x140658c90] -> BroadcastPacket12CharacterData
```

`SetEquipmentEntries` `0x140249160` [`0x140249160`] stores the handle and item index into
`PlayerGameData.equipGameData.chrAsm` and sets a per-slot dirty byte at
`chrAsm.equipmentGaItemHandles + slot - 0x30`.

The character picks the change up on its next update, in the PlayerIns pre-update `0x140660f70`
[`FUN_140660120`]. This is the whole reaction to a ChrAsm change:

```c
iVar12 = CS::ChrAsm::GetEquipmentEntryParamId(param_1->chrAsm,~WeaponRight1);  // slot -2, left
iVar9  = CS::ChrAsm::GetEquipmentEntryParamId(param_1->chrAsm,None);           // slot -1, right
bVar5 = FUN_1403bec00(param_1->chrAsm,equipChrAsm);       // 22 gaitem handles equal?
if (!bVar5) {
  ((param_1->chrIns).componentContainer)->toughness->doBigUpdate = true;
  CS::CSChrActionRequestModule::ResetInputQueue(((param_1->chrIns).componentContainer)->actionRequest);
}
CS::ChrAsm::copy(param_1->chrAsm,equipChrAsm);
...
if ((iVar9 != iVar10) || <right slot dirty>) {
  FUN_1404f6c00((param_1->chrIns).specialEffect);         // right-weapon SpEffects
  FUN_14049c2e0(&(param_1->chrIns).chrSlotSys);
}
if ((iVar12 != iVar9) || <left slot dirty>) {
  CS::SpecialEffect::RemoveWepParam2And6SpEffects((param_1->chrIns).specialEffect);
  FUN_14049c270(&(param_1->chrIns).chrSlotSys);
}
```

1.17.1 call edges, from `eldenring-deobf-1.17.1.bin`:

```
14066104f call 0x1403bec10    ; handle compare  [0x1403bec00]
140661075 call 0x1404076a0    ; ResetInputQueue [0x140407170]
140661093 call 0x1403bf300    ; ChrAsm::copy    [0x1403bf2f0]
```

`ResetInputQueue` `0x1404076a0` [`0x140407170`] is the only input or action side effect:

```c
actionRequestModule->taeCancels = actionRequestModule->taeCancels | 1;   // 1404076a4 or dword [rcx+0x100],1
FUN_140405ee0(actionFlag);   // 0x140406410: clears actionFlag+0x214 bit 0, knockBackValue, +0x210
```

Bit 0 of `taeCancels` makes the next `UpdateFromManipulator` skip input queuing for one frame and
zero `queuedActionInputs` (section 4). It drops buffered presses. It does not end an animation,
touch the behavior module, the HKS or `CSChrSwordArtsModule`, and it does not compare anything to
`swordArtsTypeNew`.

### TimeAct weapon switch

`VERIFIED`. The d-pad switch is TAE event `SwitchWeapon`, handled by `0x14042d4d0`
[`FUN_14042cf80`] from `ExecuteThreadOne` `0x14042e6f0` [`0x14042e1a0`]. It calls
`SetChrAsmEquipSlotsIndex`, sets `armStyle = OneHanded`, `SetPrecisionShootingMode(false)`, removes
the weapon SpEffects of the switched hand, syncs ChrAsm to game data through `0x140659b10`
[`FUN_140658cc0`] and sets `PlayerIns+0x5d0 = 1`. It never touches the skill either. Because it
writes the character's ChrAsm first and then copies it to game data, the pre-update compare above
finds the two equal and does not even run `ResetInputQueue` for this path. `SetChrAsmEquipmentState`
`0x140426a70` [`0x140426520`] (TAE `SetWeaponStyle`) is the two-hand toggle and is likewise
skill-blind.

## 2. Where the "active skill" lives

### Not on the character

`VERIFIED`. `CSChrSwordArtsModule` (`ChrIns+0x190` component container, `+0x110`) is 0x20 bytes:

| offset | field | writer |
|---|---|---|
| +0x10 | `activeAowFPConsumption` | `UpdateActiveAowFpStats` `0x140480070` [`0x14047fb10`]; zeroed by `ConsumeFp` |
| +0x14 | `activeAowIsNoFpVersion` | same |
| +0x18 | FE display state | `HksAct` case `SetArtsPointFEDisplayState` |

There is no arts id, SwordArtsParam id or `swordArtsTypeNew` cached anywhere on it. Every
lookup goes through the live weapon in the hand:

```c
// CanCastAow 0x14047ff80 [0x14047fa20], UpdateActiveAowFpStats, FUN_14047fe90 [FUN_14047f930] ...
ChrIns::GetWeaponGaitemHandle(pCVar3,local_res20,hand);     // (hand != 0) - 2 -> slot -1 right / -2 left
::GaitemLookupResult::GetGaitemWeapon(&local_20,local_res20);
GaitemLookupResult::GetSwordArtsParamForWeapon(&local_20,&local_30);
```

`PlayerIns::GetWeaponGaitemHandleBySlot` `0x140657770` [`0x140656920`] reads
`ChrAsm::GetGaitemHandleBySlot(param_1->chrAsm, ...)`, which is the ChrAsm the pre-update just
overwrote.

### The HKS id is refreshed every frame

`VERIFIED` (executable) + `DATA` (HKS). `c0000.hks` `GetConstVariable` sets the global every frame:

```
21 [18646] GETGLOBAL_MEM  R0 'GetSwordArtInfo'
23 [18646] CALL_I_R1      A=0 B=1 C=R3
24 [18646] SETGLOBAL      R1 'c_SwordArtsHand'
25 [18646] SETGLOBAL      R0 'c_SwordArtsID'
```

and `GetSwordArtInfo` returns `env(326, hand)`. The env switch in `HksEnv` `0x140410d50`
[`0x140410820`] (1.16.2 tables: index `env - 0xdf`, byte table `0x1404133a4`, dword table
`0x140413168`) sends 326 to case 0x3d (1.16.2 listing):

```
1404115ff call 0x140bfe090                 ; arg 2 = hand
14041160a lea  r8d,[r14 - 0x2]             ; hand -> slot -1/-2
14041161d mov  rcx,[rcx + 0x110]           ; swordArts module
140411624 call 0x14047f930                 ; -> GetSwordArtsParamForWeapon -> swordArtsTypeNew
```

```c
// FUN_140673ef0 -> 0x140674d40 in 1.17.1
uVar2 = pSVar1->paramRow->swordArtsTypeNew;
```

So `c_SwordArtsID` is 10 before the equip and 232 the frame after. `INFERRED`: the HUD label
reads the same live weapon, which is why it flips while the animation does not (the HUD path was
not traced).

### The behavior graph is the only record

`VERIFIED`. Skill clips are chosen by `CustomManualSelectorGenerator` nodes whose `offsetType` is
0x12. The offset comes from a callback registered by `CSBehaviorImp` `0x140c456c0`
[`0x140c43f80`] (`FUN_140c1e8c0(GLOBAL_CSHkBehManager,&LAB_14041adf0)`), which reaches the resolver
`0x14041b0d0` [`FUN_14041aba0`]:

```c
case 0x12:
  piVar8 = (int *)FUN_14047f770(pCVar7->componentContainer->swordArts,local_res10);  // live swordArtsTypeNew
  iVar9 = (*piVar8 + 600) * 1000000;
```

1.17.1:

```
14041b2ce call 0x14047fcd0      ; active swordArtsTypeNew [FUN_14047f770]
14041b2d5 add  ecx, 0x258       ; +600
14041b2db imul ebx, ecx, 0xf4240
```

`FUN_1419b9440` (1.17.1 `0x1419bb2b0`) adds the node's `animId` and the name lookup
`0x1419bb2f0` [`FUN_1419b9480`] picks the child clip:

```c
FUN_141692f90(&local_b8,"a%03d_%06d",param_2 / 1000000,param_2 % 1000000);  // e.g. "a610_040051"
... first child whose name contains it -> index
FUN_141692f90(&local_b8,"a000_%06d",param_2 % 1000000);                    // second try
... else index = min(0, count - 1)  -> child 0
```

The chosen index and the TAE id are stored on the node:

```c
// activate 0x1419b9980 [0x1419b7b10]
uVar5 = FUN_1419b9440(param_1,param_2);
param_1->currentGeneratorIndex = uVar5;            // 1419b99e7 mov word ptr [rdi + 0x100], ax
// 0x1419bb530 [FUN_1419b96c0]
iVar1 = FUN_1419ba1b0(<selected clip name>);       // "a610_040051..." -> 610040051
param_1->argTaeId = iVar1;
```

`argTaeId` drives `fireTAECallback(..., param_1->argTaeId, ...)` in `update`, so the TimeAct that
fires (hit boxes, `WeaponArtFPConsumption`, cancels) is the one belonging to the clip the node
picked, not to the weapon now in hand.

This node is the field that holds the active skill: CMSG `+0xec argTaeId` and `+0x100
currentGeneratorIndex`, written at activation.

## 3. Why the equip leaves Wild Strikes running

### The loop never reselects

`VERIFIED`. `CustomManualSelectorGenerator::update` `0x1419ba150` [`0x1419b82e0`] only reruns the
selection when `changeTypeOfSelectedIndexAfterActivate` (+0xb6) is not 0 (1.16.2 listing, the
first line re-checked in 1.17.1):

```
1419b8475 movzx eax, byte ptr [rdi + 0xb6]   ; 1.17.1: 1419ba2e5
1419b8484 test  al, al
1419b8486 jz    0x1419b8505                  ; 0 = NONE: keep the index chosen at activate
1419b8488 cmp   al, 0x1                      ; 1 = SELF_TRANSITION: reselect only on a self-transition
1419b848a jnz   0x1419b84d7                  ; 2 = UPDATE
```

`DATA`. Wild Strikes is a stance art (`IsStanceArts` and `IsAttackStanceArts` both list 10 in
`c0000.hks`), so it runs through `ExecArtsStance` -> `Event_DrawStanceRightStart` ->
`DrawStanceRightLoop` / `DrawStanceNoSyncLoop`. The loop clip is `a610_040051`
(600 + 10, anim 40051), and its TAE carries two `WeaponArtFPConsumption` events. Read from
`Behaviors/c0000.hkx` (offsets checked against Ghidra's `CustomManualSelectorGenerator`: `+0x48`
name, `+0x98` generators, `+0xa8` offsetType, `+0xac` animId, `+0xb6` changeType):

| CMSG | offsetType | animId | changeType | child 0 | `a832_*` child | `a000_*` child |
|---|---|---|---|---|---|---|
| `SwordArtsStanceNoSyncLoop_CMSG` | 0x12 | 40051 | 0 | `a610_040051` | no | no |
| `SwordArtsStanceNoSyncLoop_CMSG_Upper` | 0x12 | 40051 | 0 | `a610_040051` | no | no |
| `DrawStanceRightLoop_CMSG` | 0x12 | 40051 | 0 | `a839_040051` | no | no |
| `DrawStanceRightLoop_CMSG00` | 0x12 | 40051 | 1 | `a839_040051` | no | no |
| `DrawStanceRightLoop_NoMP_CMSG` | 0x12 | 40056 | 0 | `a610_040056` | no | no |
| `SwordArtsLoopLoop_CMSG` (generic loop) | 0x12 | 40003 | 1 | `a999_040003` | no | no |

Every stance-loop selector except `DrawStanceRightLoop_CMSG00` has changeType 0, so the clip it
picked at the start of the skill is held for as long as the state lasts, whatever is equipped.

### Re-entering does not help either

`DATA` + `VERIFIED`. `a832.tae` (Starcaller Cry) has animations 32000-33920 and 40000-40015, and
no 40051. If the loop node is re-activated after the equip, the lookup tries `a832_040051`, then
`a000_040051`, finds neither, and takes child 0. In both `SwordArtsStanceNoSyncLoop` selectors and
all the `NoMP` ones, child 0 is `a610_040051`: Wild Strikes again, now chosen by a fallback rather
than kept. `INFERRED`: `DrawStanceRightLoop_CMSG` would fall back to `a839_040051` instead, so the
reported Wild Strikes loop is the NoSync variant, either kept from before the equip or re-selected
by this fallback.

### FP keeps draining

`VERIFIED` + `DATA`. Both stance loops call `SetSwordArtsPointInfo(ACTION_ARM_L2, TRUE)` at the top
of every `onUpdate` (`DrawStanceRightLoop_Upper_onUpdate` line 11502,
`DrawStanceNoSyncLoop_Upper_onUpdate` line 11604). That runs `act(2016, ACTION_ARM_L2,
c_SwordArtsHand)`, which `HksAct` `0x14040d100` [`0x14040cbd0`] dispatches as
`ReserveArtsPointsUse` -> `UpdateActiveAowFpStats`:

```c
uVar2 = SwordArtsParamLookupResult::CalculateFpConsumption(&local_30,param_2,pCVar3->specialEffect);
param_1->activeAowFPConsumption = uVar2;        // 1404800e1 mov dword ptr [rsi + 0x10], eax
bVar1 = CanCastAow(param_1,param_2,param_3);
param_1->activeAowIsNoFpVersion = !bVar1;       // 1404800f6 mov byte ptr [rsi + 0x14], al
```

`ACTION_ARM_L2` is 3 (`common_define.hks`: `LOADK R0 3 / SETGLOBAL 'ACTION_ARM_L2'`), and
`CalculateFpConsumption` `0x14068c070` [`0x14068b220`] maps cast number 3 to
`useMagicPoint_L2`, read from the weapon in hand now. The TAE event type 330 is
`WeaponArtFPConsumption` (`ExecuteThreadOne` second switch: index `type - 0x12e`, byte table
`0x14042eed4`, dword table `0x14042ee90`, type 330 -> `0x14042e601` -> `jmp ConsumeFp`; 1.17.1
`14042eb72 jmp 0x14047fba0`). `ConsumeFp` `0x14047fba0` [`0x14047f640`] subtracts
`activeAowFPConsumption` and zeroes it. So each cycle of the Wild Strikes clip charges the
current weapon's L2 cost. `INFERRED`: after the equip that is Starcaller Cry's 20 instead of Wild
Strikes' 2 (the amount was not measured).

## 4. Why L2 must be released and re-held

### Menu open masks every action

`VERIFIED`. The player's pad manipulator update `0x1403daa90` [`FUN_1403daa80`]:

```c
bVar8 = FUN_140765e90(GLOBAL_CSMenuMan);   // menu input mode (CSMenuMan+0x1c & ~2) != 0
...
CS::CSChrActionRequestModule::DisableAll(pCVar11,true);   // disabledActionInputs = ~0
```

1.17.1: `1403daba7 call 0x140766d10`, `1403dac1d call 0x140408100`. Then
`UpdateFromManipulator` `0x140408190` [`0x140407c60`]:

```c
uVar1 = param_1->actionRequests;                              // raw buttons this frame
uVar12 = ~param_1->disabledActionInputs & uVar1;
param_1->actionRequests = uVar12;
param_1->newActionPresses = ~param_1->previousActionRequests & uVar12;
... for each of 16 actions:
  if ((param_1->actionRequests & bit) == 0) *pfVar4 = 0.0;     // actionTimers[i]
  else *pfVar4 = (param_2->base).time + *pfVar4;
...
if (((param_1->taeCancels & 1) == 0) && <not locked>) { <queue new presses> }
else { param_1->taeCancels &= ~1; param_1->queuedActionInputs = 0; }   // ResetInputQueue's effect
...
param_1->disabledActionInputs = param_1->disabledActionInputs & uVar1;  // stays set while held
```

While the menu is open, L2 reads as not held and `actionTimers[3]` is 0. After the menu closes,
`DisableAll` stops, but the last line keeps the L2 bit disabled for as long as L2 is physically
held. Only a release (raw bit 0) clears it. A fresh press then sets `newActionPresses` and the
timer climbs from 0.

### What the loop reads

`VERIFIED`: env ids resolved through the `HksEnv` 1001-2000 switch (index `env - 0x3e9`, byte table
`0x14041358c`, dword table `0x140413528`):

| env | case | call |
|---|---|---|
| 1106 | 8 | `CSChrActionRequestModule::ActionRequest(actionRequest, 1, arg)` `0x140407930` [`0x140407400`] |
| 1107 | 9 | `ActionCancelRequest` `0x140407ec0` [`0x140407990`] |
| 1108 | 10 | `GetActionDuration(actionRequest, arg) * FLOAT_14329e6f0` `0x1404077b0` [`0x140407280`] |
| 1114 | 16 | `DoesAnimExist` (`arg2 * 1000000 + arg3`) |
| 1116 | 18 | `HasSpecialEffectId` (`GetSpEffectID`) |
| 301 | 0x31 (other switch) | `ChrCtrlModifier` flag bit `arg` (`GetEventEzStateFlag`) |
| 326 | 0x3d | `swordArtsTypeNew` of the hand's weapon |
| 344 | 0x4d | `CanCastAow(castNumber, hand)` |

`DATA`. `DrawStanceRightLoop_Upper_onUpdate` ends the stance on the first frame L2 reads 0:

```
271 [11588] env 1108 ACTION_ARM_L2      ; ActionDuration(L2)
277 [11588] LE   A=1 B=6 C=0  -> 289    ; <= 0 -> Event_DrawStanceRightEnd
279 [11588] env 1107 ACTION_ARM_L2 == TRUE -> 289
```

`DrawStanceNoSyncLoop_Upper_onUpdate` checks the same thing only inside a TimeAct window:

```
 96 [11642] env 301 0 == TRUE ?  else -> 160 (keep looping)
105 [11643] env 1108 ACTION_ARM_L2 <= 0          -> 153 Event_DrawStanceRightEnd
113 [11644] env 1107 ACTION_ARM_L2 == TRUE       -> 153
123 [11645] env 1001 (FP) <= 0                   -> 153
135 [11646] IsEnoughArtPointsL2 == 1 ? ...       -> 153 when out of FP
160 [11651] HalfBlendLowerCommonFunctionNoSync(Event_DrawStanceNoSyncLoop, ...)
```

Nothing in either loop compares the running clip with `c_SwordArtsID`; the start-of-skill check
(`ExecArtsStance` -> `IsAttackStanceArts(c_SwordArtsID)`) is never re-run while the loop lives.

`INFERRED`, mechanism of the re-hold: in the NoSync loop the hold is sampled only while TAE flag 0
is up. Between windows the loop runs on with L2 masked. If the menu closes with L2 still held, the
mask keeps `ActionDuration(L2)` at 0 and the next window ends the stance. Releasing in the menu
clears the mask, the re-press makes the duration positive before the next window, and the loop
continues. A new L2 press is not turned into Starcaller Cry because new skills start from
`ExecAttack` / `ExecArtsStance` in `ArtsStanceCommonFunction`, and the inferred reason they do not
fire here is that the Wild Strikes loop clip opens no L2 cancel window (TAE cancel flags not read).
That the off-hand swap is what moves the stance into the NoSync states (upper-body
`ExecWeaponChange` over a lower-body stance) is also inferred: `ArtsStanceCommonFunction` does run
`ExecWeaponChange` during the stance, but the graph transition was not traced.

## 5. Fix site

The one check belongs in `CustomManualSelectorGenerator::update` `0x1419ba150` [`0x1419b82e0`],
because it runs every frame for every sword-arts clip (all `offsetType` 0x12 nodes: OneShot, loop,
stance and NoMP variants) and it holds both halves of the comparison:

```c
if (node->offsetType == 0x12) {
  int live = FUN_1419bbf60(ctx, 0x12, 0);          // [FUN_1419ba0f0] -> resolver -> (typeNew + 600) * 1000000
  if (live / 1000000 != node->argTaeId / 1000000)  // the running clip's TAE family is a different skill
    <end the node>;                                // fire its endEvent (+0xc8) as the anim-end path already
                                                   // does with fireHkbEvent_C, or W_Idle when endEvent is -1
}
```

That catches the menu equip, the TAE weapon switch and any other route, and does not depend on the
HKS. `INFERRED`: which event to fire when a node has no `endEvent` is a design choice; the
anim-end branch in the same function already shows the safe way to fire one.

Second, the fallback in `0x1419bb2f0` [`FUN_1419b9480`] must not hand a sword-arts node child 0
when neither `a<600+type>_<anim>` nor `a000_<anim>` exists. As shipped, a skill with no stance loop
that is ever routed into a stance loop plays Wild Strikes (`a610`), so fixing only the update check
would still let a re-entry pick Wild Strikes. Returning "no clip" there and letting the state end
closes that route.

An HKS-only fix is also possible (`c0000.hks` is data): at the top of
`DrawStanceRightLoop_Upper_onUpdate`, `DrawStanceNoSyncLoop_Upper_onUpdate` and
`SwordArtsLoopLoop_onUpdate`, end the state when `IsStanceArts(c_SwordArtsID)` (or the loop's
DoesAnimExist test) is false. It needs one check per loop state, not one in total.

## Open items

- Re-extract `c0000.hks`, `c0000.behbnd` and the TAEs from the 1.17.1 archives and re-run the
  table in section 3.
- Read which TAE event sets `ChrCtrlModifier` flag 0 in `a610_040051` to time the NoSync L2 window.
- Read the L2 cancel flags of `a610_040051` to confirm why a new press does not start Starcaller Cry.
- Measure the per-cycle FP after the equip (2 or 20) to confirm the cost follows the new weapon.
