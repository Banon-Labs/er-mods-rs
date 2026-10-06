# Fallback test plan: how a player reaches each borrowed-clip row

Companion to `cmsg-fallbacks.md`. For each row there, this page says which state the player has to
reach, with which input, FP, hand and grip, whether a mid-skill weapon swap is needed and on which
clip it has to land, which weapon to use, and what the passive clip logger
(`scripts/frida/skill-clip-trace.js`) should print when the fallback fires and when it does not.
`DrawStanceNoSyncLoop` is left to the separate trace.

Labels:

- `VERIFIED`: read in the code. HKS = the 1.17.1 behavior script
  `~/er-extract/1171-20261004/action/script/c0000.hks` (line numbers are its own); graph = the
  1.17.1 `c0000.hkx` (state trees and selector bindings printed by `scripts/er-behbnd-tree.py`).
- `DATA`: read from the 1.17.1 TimeActs or regulation (`scripts/er-fallback-routes.py`,
  `scripts/er-behbnd-cmsg-fallbacks.py`).
- `INFERRED`: follows from the two, not watched in game.

Reproduce:

```
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-tree.py --state DrawStanceRightStart --state DrawStanceRightLoop_Upper
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-tree.py --var IsEnoughArtPointsL2_DrawStanceRightEnd
python3 /home/banon/projects/er-mods-rs/scripts/er-fallback-routes.py --windows 623:40000 --windows 817:40000
python3 /home/banon/projects/er-mods-rs/scripts/er-fallback-routes.py --skill 318 --motion 57
python3 /home/banon/projects/er-mods-rs/scripts/er-fallback-routes.py --sweep
ER_HKS_FILE=$HOME/er-extract/1171-20261004/action/script/c0000.hks python3 /home/banon/projects/er-mods-rs/scripts/er-hks-disasm.py --dump SetSwordArtsPointInfo
```

`er-hks-disasm.py` defaults to the 2026-07-13 copy of `c0000.hks`, which predates 1.17. Set
`ER_HKS_FILE` as above. The functions this page relies on were compared instruction by instruction
between the two copies; the differences are the 1.17 additions listed in `affected-class.md` and
none of them touches the paths below.

## Summary

| # | Row | How to reach it | Swap? | Weapons | Logger, fallback fires | Logger, it does not |
|---|---|---|---|---|---|---|
| 1a | Stance start, lower body | Start the stance while moving, stop moving before the start clip ends | yes, on the first frame of the source start clip | Battle Axe 14000000 (Wild Strikes) -> Starscourge Greatsword 4050000 | `a839_040050` with Starscourge held | `a610_040050` only, then `a839_040051` |
| 1b | Stance loop while moving | Measured stance loop, then move while still holding L2 | yes (same swap as the measured loop) | same | `a839_040052` | `a839_040051` only |
| 2 | Stance no-FP loop | Moon-and-Fire Stance at FP 0-4, swap on its start | yes, on `a918_040055` | Rellana's Twin Blades 67520000 -> Starscourge 4050000 | `a610_040056` for about a frame, then `a839_040051` | `a918_040056` (no swap) |
| 3a | ComboEnd no-FP | Stormcaller, L2 again at 2.53-3.13 s, FP below 10 at the second press | yes, on `a623_040000` | Longsword 2000000 + Stormcaller 12300 -> Starscourge 4050000 | `a603_040015` | `a002_040010` with FP 10 or more; `a623_040010` with no swap |
| 3b | ComboEnd twinblade (_24) | Same, on a twinblade | yes, on `a623_042400` | Twinblade 10000000 + Stormcaller 12300 -> Starscourge | `a603_042410` (FP 10+) or `a603_042415` (FP < 10) | `a623_042410` with no swap |
| 3c | ComboEnd backhand (_58) | Sword Dance, L2 again at 1.50-2.17 s | yes, on `a624_045800` | Backhand Blade 64500000 + Sword Dance 12400 -> Starscourge | `a603_045810` / `a603_045815` | `a624_045810` with no swap |
| 3d | ComboEnd_2 (Bloodboon `a834_040020`, Stormcaller `a623_042420`) | Not reachable by a swap the equip gate accepts, and no skill reaches it without one | - | - | - | - |
| 3e | ComboEnd _02 / _03 | Needs idle category 2 or 3; which equipment gives that was not traced | - | - | - | - |
| 4 | Half charge, early release | Glintstone Dart, release L2 at 0.43-0.87 s | yes, on `a817_040000` | Glintstone Kris 1070000 -> Meteoric Ore Blade 9030000 | `a605_040001` (or `a605_040006` short of FP) | `a817_040001` with no swap; `a666_040001` if the target is not a half-blend skill |
| 5 | Spinning Chain, 0 < FP < cost | Use it at FP 1-7 | no | Flail 13010000 | never `a839_040055` / `a610_04005x` | `a625_040050`, `a625_040051` (`a625_040052` moving) |
| 6 | Thrusting shield, heavy special | Barbaric Roar or War Cry buff, then R2 with the shield | weapon change after the roar, no timing | Battle Axe 14000000 + Barbaric Roar 65000 (or War Cry 65100), Dueling Shield 62500000 | `a030_030600`/`030601` (Roar), `a022_030620`/`030621` (War Cry); two-handed `a030_032600` / `a022_032620` | `a057_030500` (normal heavy: the buff did not survive the change) |
| 7 | Scythe left heavy 5 | Not reachable: a scythe's off-hand chain stops at 4 | - | control: Grave Scythe 19010000 left, Longsword 2000000 right | - | control shows `a050_035000`..`035030`, then `a050_035000` again |

Two things to sort out before rows 6 and 7 can be logged at all:

- **The logger drops them.** `skill-clip-trace.js` returns early unless the category is 600-999
  (`if (cat < 600 || cat >= 1000) return;`). Rows 6 and 7 are weapon-category clips (`a030`, `a022`, `a057`, `a050`). Widen that
  filter (or add the categories) before those runs. Not edited here: the file hot-reloads into any
  watcher that has it loaded.
- **Swaps only land on the first frame of a skill.** `equip-gate.md`: every SwordArts state's
  update runs `ArtsCommonFunction`, which calls `act(163)` before it looks at any input, and
  `act(163)` sets the `0x10` bit that shuts the equip gate (`VERIFIED`, HKS line 6828 and the gate
  predicate). The driver's measured
  window is the one frame between the selector choosing the first clip and the next player tick.
  A follow-up clip (ComboEnd, a stance loop) is selected inside a tick that already ran
  `act(163)`, so a swap aimed at it is refused (`INFERRED`). Every swap below is therefore "on the
  first clip of the source skill", which is what `chainsaw-driver.js` does with `pivotCommit: fe`
  and `sourceArtsType`.

## Rows

### 1. Stance start (040050) and loop while moving (040052) -> Spinning Wheel `a839`

State graph (`VERIFIED`, graph):

- `DrawStanceRightStart` (lower and upper machines) is a selector on
  `IsEnoughArtPointsL2_DrawStanceRightStart`: index 0 `DrawStanceRightStart_CMSG` (040050, child 0
  `a839_040050`, 13 hits), index 1 the no-FP node (040055).
- `DrawStanceRightLoop_Upper` is a selector on `IsEnoughArtPointsL2_DrawStanceRightEnd`; index 0 is
  `DrawStanceRightLoop_Upper_Selector`, bound to `LocomotionState`: 0 = `DrawStanceRightLoop_CMSG00`
  (040051), 1 = a blend holding `DrawStanceLoopMove_CMSG_Upper` (040052, child 0 `a839_040052`,
  10 hits). All of these have changeType 0, so they read the skill category when they activate.

Script (`VERIFIED`, HKS):

- `ExecArtsStance` (2602-2653) picks `UPPER` when `MoveStart(LOWER, Event_Move)` is true, i.e.
  the stance starts while moving, and fires `Event_DrawStanceRightStart` on that layer only.
- `DrawStanceRightStart_Upper_onUpdate` (11443-11529) calls `HalfBlendLowerCommonFunction` with
  `Event_DrawStanceRightStart`. When the lower body stops moving, `ExecStopHalfBlend` (489-501) sets
  `LocomotionState` 0 and fires the start event on `LOWER`. That activates the lower-machine
  `DrawStanceRightStart` fresh, so its CMSG reads the category of whatever is held at that moment.
- At the start's end the same function sends `c_SwordArtsID` 10, 11, 340, 341, 309 to
  `DrawStanceNoSyncLoop` and every other id to `DrawStanceRightLoop`. It reads the id at that
  moment, so after a swap the target's id decides. Starcaller Cry (232) goes to `DrawStanceRightLoop`,
  which is the measured `a839_040051`.
- `Update` (18787-18889) writes `LocomotionState = GetLocomotionState()` every frame, so moving
  while in the loop switches the upper loop to the move blend and activates
  `DrawStanceLoopMove_CMSG_Upper`.

Test 1a (`INFERRED` outcome): Battle Axe 14000000 (Wild Strikes) in the right hand, Starscourge
Greatsword 4050000 as the target. Hold the stick so the character is moving, run the measured
pivot so the swap commits on the frame `a610_040050` is selected, and release the stick before
that clip ends (1.07 s, `DATA`). Expected: `a610_040050` (upper body, Battle Axe), then
`a839_040050` with Starscourge held (lower body), then `a839_040051`. Not firing: no second
040050 entry, only `a610_040050` then `a839_040051`. If the stick is never released, 1a cannot
fire; that is the negative control.

Test 1b: the measured loop (drive-watch27), then move while still holding L2. Expected
`a839_040052` with Starscourge held. Not firing: only `a839_040051`.

### 2. Stance no-FP loop and loop-move (040056 / 040057) -> Wild Strikes `a610`

Which skill can select the no-FP nodes at all (`VERIFIED`):

- The no-FP start, loop and end nodes sit behind selectors bound to
  `IsEnoughArtPointsL2_DrawStanceRightStart` (start) and `IsEnoughArtPointsL2_DrawStanceRightEnd`
  (loop, loop-move and end), graph.
- `SetSwordArtsPointInfo` (HKS 739-804) is the only writer of both. It computes a no-FP flag from
  `env(344, arm, hand)` and writes it to the start variable only when the hand's skill
  (`env(326, hand)`) is 309 (Unending Dance) or 318 (Moon-and-Fire Stance), and to the loop/end
  variable only when it is 318. Every other skill gets 0 written.
- So only Moon-and-Fire Stance (318, Rellana's Twin Blades 67520000, `DATA`) ever plays the no-FP
  loop. Its own `a918_040056` / `a918_040057` are children, so without a swap nothing falls back.
  Wild Strikes, Spinning Strikes, Spinning Wheel and Unending Dance have no-FP loop clips that the
  graph never selects.
- For 318 the FP check uses the R1 cost, not L2: `ExecArtsStance` calls
  `SetSwordArtsPointInfo(ACTION_ARM_R1, ...)` when `c_SwordArtsID == 318`. Moon-and-Fire's R1 cost
  is 10 (`DATA`), and the game's full-skill rule is FP above 0 and at least half the cost
  (`CanCastAow`, `er-mechanics-ashes.py`), so FP 0-4 sets the flag (`INFERRED`: that `env(344)` is
  that rule was not traced). It is not an attack stance, so `ExecArtsStance` does not refuse FP 0.

The catch (`VERIFIED`): `DrawStanceRightLoop_Upper_onUpdate` (11531) calls
`SetSwordArtsPointInfo` every frame with the current skill. After a swap the current skill is the
target's, so the loop variable is rewritten to 0 on the loop's first update and the selector moves
to the normal loop node.

Test (`INFERRED` outcome): Rellana's Twin Blades one-handed in the right hand, left hand empty, FP
0-4. Pivot so the swap to Starscourge commits on `a918_040055`. Expected: `a918_040055`, then
`a610_040056` once (about one frame), then `a839_040051`; while moving `a610_040057` then
`a839_040052`. Not firing: `a839_040051` straight away (the loop update ran before the loop node
activated). No swap: `a918_040055`, `a918_040056`. The logger folds repeats per frame, so a single
`a610_040056` line is the whole signal.

### 3. Combo finishers (`SwordArtsOneShotComboEnd`, `_2`, `_02`, `_03`, `_24`, `_58`)

How a follow-up starts (`VERIFIED`, HKS):

- During `SwordArtsOneShot` the follow-up is opened by SpEffects the skill's TimeAct applies.
  `GetSwordArtsRequestNew` (703-734) turns an L2 press into `SWORDARTS_REQUEST_RIGHT_COMBO_1` while
  SpEffect 100052 is active and `COMBO_2` while 100053 is active. It checks `IsStanceArts` first,
  so a stance skill held at that moment starts a stance instead.
- `SwordArtsOneShot_onUpdate` (12232-12347) does the same for R1 (100054 -> ComboEnd, 100055 ->
  ComboEnd_2) and R2 (100050 -> ComboEnd, 100051 -> ComboEnd_2).
- `ExecAttack` sends `COMBO_1` to `W_SwordArtsOneShotComboEnd` and `COMBO_2` to
  `W_SwordArtsOneShotComboEnd_2` (2039-2058). No FP check stands in the way; it calls
  `SetSwordArtsPointInfo(ACTION_ARM_R2, ...)` first, which sets `IsEnoughArtPointsR2` (or
  `IsEnoughArtPointsR2_2`) from the held skill's R2 cost.
- Graph: `SwordArtsOneShotComboEnd` = selector on `IsEnoughArtPointsR2` (0 normal, 1 no-FP), then a
  selector on `SwordArtsOneShotComboCategory`: 0 -> 040010, 1 -> _02 (040210), 2 -> _03, 3 -> _24
  (042410), 4 -> _42, 5 -> _58 (045810), 6 -> _59. `_2` is the same shape on 040020, with
  `_24` = 042420. All of these CMSGs have changeType 1.
- `SwordArtsOneShotComboCategory` is written once, when the skill starts (`ExecAttack`, line 1901),
  from `GetSwordArtsDiffCategory(c_SwordArtsID, env(358), env(225, hand))`: 2 -> 1, 3 -> 2, 24 -> 3,
  42/55/62 -> 4, 58 -> 5, 57 -> 6, anything else 0. `GetSwordArtsDiffCategory` lives in
  `common_define.hks` (read from the 2026-07-13 copy; the 1.17.1 copy was not extracted): it
  returns the first of the shield variant, the hand's weapon category (`env(225)`, the
  `WEAPON_CATEGORY_*` id, which equals `wepmotionCategory`), or the idle category (`env(358)`)
  that the skill's `SwordArtsCategory` table lists. So 24 = twinblade, 58 = backhand blade, 57 =
  thrusting shield; 2 and 3 come from the idle category.

Windows (`DATA`, 1.17.1 TimeActs, seconds of clip time):

| clip | SpEffect | input | window |
|---|---|---|---|
| `a623_040000`, `a623_040005`, `a623_042400` (Stormcaller) | 100052 | L2 -> ComboEnd | 2.53-3.13 s |
| `a623_040010`, `a623_040015`, `a623_042410` | 100053 | L2 -> ComboEnd_2 | 1.40-2.00 s |
| `a603_040000` (Spinning Slash) | 100052 | L2 -> ComboEnd | 0.77-1.43 s |
| `a603_042400` | 100052 | L2 -> ComboEnd | 0.80-1.50 s |
| `a624_045800` (Sword Dance, backhand) | 100052 | L2 -> ComboEnd | 1.50-2.17 s |
| `a834_040000` / `a834_040010` (Bloodboon) | 100052 / 100053 | L2 | 2.33-3.67 s / 1.50-2.83 s |
| `a002_040010`, `a603_042410` | none | - | - |

Swap timing: the swap commits on the first clip of the source skill; the follow-up node then
reads the target's category. The target must not be a stance skill (else the L2 starts a stance).
Starcaller Cry (Starscourge Greatsword 4050000) is not, and its R2 cost is 20 (`DATA`), so FP 10
or more at the second press gives the normal node and FP 0-9 the no-FP node (`INFERRED`, same
half-cost rule). Stormcaller's start costs 9 FP: start with 19 or more for the normal branch, with
9-18 for the no-FP branch (FP between half and full cost plays the full skill and ends at 0).

- **3a, no-FP (040015) -> `a603_040015`.** Longsword 2000000 with Stormcaller 12300 (its built-in
  Square Off is a stance, so the ash is needed). Pivot the swap to Starscourge onto `a623_040000`,
  press L2 again at 2.53-3.13 s. Expected `a603_040015` (FP 0-9) or `a002_040010` (FP 10+; the
  generic clip, 0 hits). No swap: `a623_040010` / `a623_040015`.
- **3b, twinblade (_24) -> `a603_042410`.** Twinblade 10000000 with Stormcaller 12300, same
  sequence on `a623_042400`. Expected `a603_042410` (FP 10+) or `a603_042415`. No swap:
  `a623_042410`. That `SwordArtsCategory[23]` lists 24 is `INFERRED` from `a623` having the
  042400 clips.
- **3c, backhand (_58) -> `a603_045810`.** Backhand Blade 64500000 with Sword Dance 12400, L2 again
  at 1.50-2.17 s of `a624_045800`. Expected `a603_045810` / `a603_045815`. No swap: `a624_045810`.
- **3d, ComboEnd_2 -> `a834_040020` (category 0) and `a623_042420` (_24): not reachable.**
  ComboEnd_2 needs a 100053 window in the ComboEnd clip that is playing. After a first-clip swap
  that clip is the fallback (`a002_040010`, `a603_042410`, ...), and none of those carry 100053
  (`DATA`). A swap on the ComboEnd clip itself is refused by the gate (see the summary). The
  `--sweep` over every skill clip found no skill whose own ComboEnd clip opens ComboEnd_2 without
  having its own ComboEnd_2 child (`DATA`), so there is no route without a swap either.
  `_24`'s only child is `a623_042420` anyway, so it would only ever borrow Stormcaller's own clip.
- **3e, _02 / _03: not planned.** They need idle category 2 or 3 (`env(358)`) and a skill whose
  `SwordArtsCategory` row lists it; which equipment gives that idle category was not traced.

### 4. `SwordArtsHalfChargeCancelEarly` (40001 / 40006) -> Charge Forth `a605_040001`

What "released early" means (`VERIFIED`, HKS `SwordArtsHalfOneShot_Upper_onUpdate` 12386-12489,
`SwordArtsOneShot_onUpdate` for the whole-body version):

- `env(1108, ACTION_ARM_L2) <= 0` (L2 not held; the same test the stance loop uses for release)
  while SpEffect 100285 is active. There is no frame-count threshold in the script; the window is
  the 100285 span in the skill's TimeAct. 100286 at release goes to `ChargeCancelLate` instead.
- In the half-blend state it then asks `IsHalfBlendArts(c_SwordArtsID)` for the skill held at that
  moment: true -> `Event_SwordArtsHalfChargeCancelEarly` (half machine: selector on
  `IsEnoughArtPointsL2`, child 0 `a605_040001` / no-FP `a605_040006`), false ->
  `W_SwordArtsChargeCancelEarly` (whole body, a category selector on `SwordArtsChargeCategory`,
  child 0 `a666_040001` Carian Grandeur, 1 hit).
- Half-blend skills (`IsHalfBlendArts`, 649-655): 20, 58, 168, 182, 183, 184, 199, 202, 203, 206,
  213, 217, 264, 328, 334, 335. Only 217 (Glintstone Dart) and 264 (Wall of Sparks) carry a 100285
  window (`DATA`), and both have their own 040001 child. So nothing reaches the borrowed Charge Forth
  clip without a swap.

Test (`INFERRED` outcome): Glintstone Kris 1070000 (Glintstone Dart, FP 10) one-handed right, left
empty. Pivot the swap to Meteoric Ore Blade 9030000 (Gravitas, a half-blend skill with no 040001
child) onto `a817_040000`, keep L2 held, release it at 0.43-0.87 s. Expected `a605_040001` (or
`a605_040006` if the start was short of FP). Controls: no swap -> `a817_040001`; target
Starscourge (not half-blend) -> `a666_040001`; L2 held past 0.87 s -> no cancel clip.

### 5. Spinning Chain (skill 125, swordArtsTypeNew 25) with 0 < FP < cost

`VERIFIED`, HKS and graph:

- `ExecArtsStance` refuses attack-stance skills only at `env(1001) <= 0` (FP 0), and requires L2
  held. At FP 1-7 it starts the stance.
- The no-FP start node is selected only for 309 and 318 (row 2). For 25 the start variable is
  written 0, so the normal start node plays Spinning Chain's own `a625_040050`. The no-FP loop is
  likewise 318 only, so the loop is `a625_040051` (moving `a625_040052`).
- `DrawStanceRightLoop_Upper_onUpdate` ends 25's loop when FP reaches 0 or below.

So `a839_040055` and `a610_040056` / `a610_040057` never play for Spinning Chain, whatever the FP.
Test: Flail 13010000 (L2 cost 8, `DATA`), at FP 1-3 (below half cost) and again at 4-7. Expected
both times `a625_040050`, `a625_040051`, then the end clip when FP runs out. A `a839_040055` line
would contradict this page.

### 6. Thrusting-shield heavy specials -> axe `a030` / claw `a022`

`VERIFIED`, HKS `ExecAttack` (heavy branch, 1432-1480) and graph:

- On an R2 request `ExecAttack` checks `env(1116, id)` for SpEffects 1681/1686 (Barbaric Roar, type
  0), 1811/1816 (War Cry, type 1), 1716, 1721 and 102101 (type 0). If one is active it writes
  `AttackRightHeavySpecialType` and swaps the heavy events: `W_AttackRightHeavy1Start` ->
  `W_AttackRightHeavySpecial1Start`, `...SubStart` -> `...Special1SubStart`, `Heavy2Start` ->
  `Special2Start` (and the two-handed `AttackBoth...` equivalents).
- Those states are selectors on `AttackRightHeavySpecialType`: type 0 ->
  `AttackRightHeavySpecial1Start_CMSG` (030600, child 0 `a030_030600`), type 1 ->
  `...Warrior1Start_CMSG` (030620, child 0 `a022_030620`). Offset type 0xd (right hand) and 0x10
  (two-handed). `a057` has the 0306xx/0326xx clips in its TimeAct (`DATA`) but no child.

Weapons (`DATA`): the three thrusting shields (Dueling 62500000, Carian 62510000, Ritual 62520000)
cannot mount Barbaric Roar or War Cry (`canMountWep_ThrustingShield` is 0), and Barbaric Roar /
War Cry are greyed out from the left hand. So the buff has to come from another weapon and outlast a
weapon change.

Test: right-hand slot 1 Battle Axe 14000000 with Barbaric Roar 65000 (or War Cry 65100), slot 2
Dueling Shield 62500000. Roar, switch to the shield, R2 within 40 s (1681's duration, `DATA`).
Expected `a030_030601` / `a030_030600` (Roar) or `a022_030621` / `a022_030620` (War Cry);
two-handed `a030_032600` / `a022_032620`. If the logger shows the shield's normal heavy
`a057_030500` / `a057_030501`, the buff did not survive the weapon change (`INFERRED` either way;
the `[Weapon]` name prefix on 1681 suggests it may not). Needs the logger filter widened.

### 7. Scythe left heavy 5 -> straight sword `a023_035040`

`VERIFIED`, HKS:

- The off-hand chain is L1 with a melee weapon in the left hand that neither pairs with the right
  nor guards (`combo.md`): `W_AttackLeftHeavy1` .. `5`, clips 035000 .. 035040.
- `AttackLeftHeavy4_onUpdate` (8925-8950) sends L1 to `W_AttackLeftHeavy5` only if
  `IsEnableNextAttack(4, HAND_LEFT)`, which is `4 < GetAttackMaxNumber(HAND_LEFT)` (2260-2273).
  `GetAttackMaxNumber` (2275-2365) returns 4 for `WEAPON_CATEGORY_LARGE_SCYTHE` (50). So a scythe's
  fifth L1 restarts at `AttackLeftHeavy1`. Its special cases (spAtk 249, 255, 257, 258) do not
  cover scythes (spAtk 225 or 0, `DATA`).
- `W_AttackLeftHeavy5` has no other sender (`--const`).

So the row is not reachable by input, and a swap would have to land between the
`AttackLeftHeavy4` update that fires the event (reading the source weapon's maximum) and the
`AttackLeftHeavy5` activation in the next graph update: no driver trigger exists for that point
(`INFERRED`). `a050_035040` exists in the TimeAct but is dead content.

Control that checks the claim: Grave Scythe 19010000 in the left hand, Longsword 2000000 in the
right, one-handed, five quick L1 presses. Expected `a050_035000`, `035010`, `035020`, `035030`, then
`a050_035000`. A `a023_035040` or `a050_035040` line would contradict it. Needs the logger filter
widened.

## Side findings

- `--sweep` also lists Torch Attack (`a617_040040`, `a617_040041`) opening ComboEnd with no child
  of its own, which would play `a603_040015` at low FP, and Shield Crash (`a698_045930`) and Muleta
  (`a972_040070`) opening ChargeCancelEarly variants without their own child. Whether those clips
  run under `SwordArtsOneShot` (the only state that reads the windows) was not checked.
- The no-FP stance clips of Wild Strikes, Spinning Strikes, Spinning Wheel, Unending Dance and
  Spinning Chain (`a6xx/a8xx/a9xx_040055-57`) are never selected (row 2).
