# Behavior-graph fallbacks: where the game plays a different clip than the context asks for

The chainsaw glitch ends with Starscourge Greatsword in hand playing Spinning Wheel's loop
`a839_040051` (drive-watch27). This page explains why that happens and lists every other place in
the player behavior graph (`c0000.behbnd.dcx`, 1.17.1) where the same rule puts another weapon's
or another skill's animation on screen.

Labels: `VERIFIED` = read from the game code (Ghidra dumps). `DATA` = read from the game files by
`scripts/er-behbnd-cmsg-fallbacks.py` (1.17.1 extract `~/er-extract/1171-20261004-witchy`).
`INFERRED` = follows from those two but nobody has watched it happen in game.

Reproduce:

```
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-cmsg-fallbacks.py --selftest   # 17 checks
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-cmsg-fallbacks.py --borrow     # skill nodes
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-cmsg-fallbacks.py --weapons    # weapon nodes
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-cmsg-fallbacks.py --cmsg DrawStanceRightLoop_CMSG
```

## 1. The rule: no matching child means child 0

`VERIFIED` in two builds: the 1.16.2 named dump (:8765) and the 1.17.0 dump (:8767). 1.17.1 has
the same code at +0x70.

`CustomManualSelectorGenerator` (CMSG) chooses one of its child generators when it activates:

1. `FUN_1419b9440` [1.17.0 `0x1419bb240`, 1.17.1 `0x1419bb2b0`] works out
   `id = resolve(offsetType, animId) + animId`. `FUN_1419ba0f0` calls the game's resolver only
   for offsetType 0xb and 0xd..0x12. Every other offsetType resolves to 0.
2. The name lookup `FUN_1419b9480` [1.17.0 `0x1419bb280`, 1.17.1 `0x1419bb2f0`] formats
   `a%03d_%06d` from that id. It takes the first child whose node name (`hkbNode::m_name`,
   +0x48) contains that string. If none does, it tries `a000_%06d`. If that fails too, it
   returns index 0. It returns `count - 1` only when the array is empty:

   ```c
   uVar7 = (short)iVar1 - 1;  uVar4 = 0;
   if ((short)uVar7 < 0) uVar4 = uVar7;     // empty array -> -1, otherwise 0
   ```
3. The node stores that index (+0x100) and stores `argTaeId` (+0xec), which it parses from the
   chosen child's clip name. So the TimeAct that fires (hitboxes, FP use, cancel windows) belongs
   to child 0's skill or weapon. The equipped weapon's own TimeAct is not used
   (`skill-survives-equip.md` section 2). The equipped weapon still decides how each hitbox
   resolves, through its `behaviorVariationId`.
4. A node reselects only if `changeTypeOfSelectedIndexAfterActivate` (+0xb6) is not 0. The
   values are 1 (on a self-transition) and 2 (on update). For any other node, the category is
   read once, when the node activates.

There is no "default" field. Fallback order is fixed in code: own child, then the `a000_` child,
then child 0. Because the lookup is a substring match on the node name, children named
`a839_040051.hkx` or `a610_040051_hkx_AutoSet_02` match.

The resolver `FUN_14041aba0` [1.17.1 `0x14041b0d0`] (`VERIFIED`, 1.16.2) maps each offsetType
to a category:

| offsetType | nodes in c0000 | category |
|---|---|---|
| 0x12 | 413 | skill: `600 + swordArtsTypeNew` |
| 0x11 | 194 | magic: `400 + refType` (when refType < 200) |
| 0xd | 183 | weapon category, hand 1 |
| 0xe | 62 | weapon category, hand 0 |
| 0x10 | 233 | weapon category, hand picked by arm style |
| 0xb | 2874 | idle category, hand 2 (+300 for the female variant when it exists) |
| 0xf | 58 | an animation-controller lookup (not traced) |
| 0 | 34 | none (category 0) |

The weapon-category cases go through `FUN_1403f1d40`, which has a fallback of its own that runs
before the CMSG lookup. It tries the weapon's `spAtkcategory`, then its `wepmotionCategory`, then
a fixed category (`a048` for hand 0, `a023` for hand 1). It keeps the first one the character's
animation set actually contains. Skill nodes (0x12) have no such step: the skill category goes
straight to the name lookup.

### The measured case

`DATA` + measured. `DrawStanceRightLoop_CMSG` (offsetType 0x12, animId 40051, changeType 0) has
26 children, and child 0 is `a839_040051` (Spinning Wheel). Neither `a832.tae` nor the node has
040051 for Starscourge's skill category, and the node has no `a000_040051` child either. So when
the loop node activates after the equip, it plays `a839_040051`, which drive-watch27 measured
(`839040051`). That one run is the only in-game confirmation of the rule. The code read above is
what proves it in general.

## 2. Two ways to reach a fallback

- **Swap during a skill (chainsaw shape).** The skill's first state starts with the source
  weapon. The equip lands. Then a later state activates its CMSG, which now reads the target
  weapon's category. Any target whose skill lacks that state's clip gets child 0. Nearly every
  skill lacks nearly every other skill's clips, so what plays is decided by the **state** the
  source skill passes through. The target weapon only has to lack its own clip for that state.
- **No swap at all.** A category that has the clip in its own TimeAct but no child in the node.
  The game plays child 0 in normal play whenever that category reaches that state.

## 3. Gameplay-relevant fallbacks

`DATA` for the node, child 0 and hit-event count. "Hit events" counts TimeAct type 1 (attack),
2 (bullet) and 307 (PC behavior) events in the clip, with imports followed. Reachability is
`INFERRED` unless marked measured. The stance chain (`ExecArtsStance`) is the only route that has
been traced in `c0000.hks`.

### 3a. Swap during a skill: follow-up states whose child 0 attacks

All of these except the stance loop have changeType 1 or are activated on state entry, so they
read the category when the follow-up starts. "Sources" are the skill categories that have their
own child, i.e. the skills that enter that state.

| State (node) | animId | Child 0 = clip played | Hit events | Sources (enter this state) | Evidence |
|---|---|---|---|---|---|
| `DrawStanceRightLoop` (`DrawStanceRightLoop_CMSG`) | 40051 | `a839_040051` Spinning Wheel loop | 8 | the stance skills the node's 26 children cover (Wild Strikes, Spinning Strikes, Spinning Chain, Unending Dance, Square Off, Unsheathe, bow stances, ...) | measured (drive-watch27) |
| `DrawStanceRightLoop_Upper` (`DrawStanceLoopMove_CMSG_Upper`) | 40052 | `a839_040052` Spinning Wheel loop while moving | 10 | same stance set | INFERRED |
| `DrawStanceRightStart` (`DrawStanceRightStart_CMSG`) | 40050 | `a839_040050` Spinning Wheel start | 13 | same stance set (start only re-reads on re-entry) | INFERRED |
| `DrawStanceNoSyncLoop(_Upper)` | 40051 / 40052 | `a610_040051` / `a610_040052` Wild Strikes loop | 2 | same stance set | INFERRED |
| stance no-FP loop / loop-move (4 nodes) | 40056 / 40057 | `a610_040056` / `a610_040057` Wild Strikes no-FP loop | 2 | Wild Strikes, Spinning Strikes, Spinning Wheel, Unending Dance, Moon-and-Fire Stance | INFERRED |
| `SwordArtsOneShotComboEnd` no-FP, `_02`, `_03`, `_24`, `_58` | 40015, 40210, 40310, 42410, 45810 (+5 no-FP) | `a603_*` Spinning Slash follow-up | 2 | Spinning Slash, Double Slash, Stamp (Sweep), Sword Dance, Stormcaller, Flaming Strike, Blood Blade, ... | INFERRED |
| `SwordArtsOneShotComboEnd_2` | 40020 / 40025 | `a834_040020` Bloodboon Ritual follow-up | 2 / 1 | 18 combo skills (Double Slash, Stormcaller, Storm Blade, Blood Blade, Dynast's Finesse, Waterfowl Dance, Bloodboon Ritual, ...) | INFERRED |
| `SwordArtsOneShotComboEnd_2` (`_24`) | 42420 | `a623_042420` Stormcaller follow-up | 4 | Stormcaller | INFERRED |
| `SwordArtsHalfChargeCancelEarly` | 40001 / 40006 | `a605_040001` Charge Forth early release | 2 | charged skills (Charge Forth, Carian Grandeur, Carian Greatsword, Black Flame Tornado, Shield Crash, Great-Serpent Hunt, Ordovis's Vortex, Siluria's Woe, Eochaid's Dancing Blade, Glintstone Dart, Thundercloud Form, ...) | INFERRED |
| `SwordArtsChargeCancelEarly` (`_58`, `_02`, `_03`) | 45901, 40201, 40301 | `a605_045901` Charge Forth, `a884_*` Flame Spear | 2 | Charge Forth / Flame Spear only | INFERRED |
| `SwordArtsOneShot` (`_111` / `_110`) | 40111 / 40110 | `a928_040111` / `a928_040110` Euporia Vortex | 38 / 14 | Euporia Vortex, Causality's Wrath | INFERRED, entry not traced |
| `SwordArtsBothLoopEnd`, `SwordArtsLeftLoopEnd` | 40034 / 40044 | `a801_*` Flame Spit end | 1 | Flame Spit, Tongues of Fire, Feeble Lord's Frenzied Flame | INFERRED |
| `SwordArtsBoth/LeftGuardCounter` | 40101 / 40102 | `a952_*` Revenge of the Night | 1 | Revenge of the Night | INFERRED |

`DATA` that keeps some states out of the table: the stance follow-up attacks
`DrawStanceRightAttackLight` / `Heavy` (40060, 40065, 40070) and `DrawStanceHalfRightAttackLight`
all have an `a000_` child. A skill without its own clip gets that generic clip, not another
skill's. `SwordArtsOneshotComboEnd_CMSG` (40010) falls back to `a002_040010`, which has 0 hit
events. `SwordArtsChargeCancelLate` and `SwordArtsStanceAttack*Start` fall back to `a999_*`, which
has no TimeAct entry.

### 3b. No swap: a category that has its own clip but no child

Checked over every skill TimeAct (a600-a999) and every player weapon. Only categories whose own
clip has hit events are listed. Many more are bare stubs: Last Rites `a853_045900`, Taker's
Flames `a814_0400x0`, Spinning Chain `a625_040000`/`040010`-`040025`, and the greatsword
`a026_030030`/`032030` entries all have 0 hit events, so their fallback changes nothing that
hits.

| Who | State (node) | Own clip (hit events) | Plays instead (hit events) | Gate | Evidence |
|---|---|---|---|---|---|
| Spinning Chain (skill 125, a625, Flail and others) without enough FP | `DrawStanceRightStart` (`DrawStanceRightStart_NoMP_CMSG`) | `a625_040055` (2) | `a839_040055` Spinning Wheel no-FP start (13) | `ExecArtsStance` refuses attack-stance skills only at FP <= 0 (`env(1001)`, hks line 2585), so 0 < FP < cost reaches it | DATA + INFERRED |
| same | `DrawStanceRightLoop` (`DrawStanceRightLoop_NoMP_CMSG`) and `_Upper` / loop-move | `a625_040056` (5), `a625_040057` (6) | `a610_040056` / `a610_040057` Wild Strikes no-FP loop (2) | the loop update ends skill 25 when FP runs out (`DrawStanceRightLoop_Upper_onUpdate`) | DATA + INFERRED |
| same | `DrawStanceRightEnd` (no-FP) | `a625_040058` (0) | `a839_040058` (0) | none that matters | DATA |
| Dueling Shield, Carian Thrusting Shield, Ritual Thrusting Shield (wepmotion 57) | `AttackRight/BothHeavySpecial1Start/End`, `...2Start/End` and the `Warrior` variants (offsetType 0xd / 0x10) | `a057_030600` (3), `a057_030605` (3), ... | `a030_*` (axe category, 2-3) or `a022_*` (claws, 1-5) | which weapons hks sends into these states was not traced | DATA, reachability unknown |
| Scythes (wepmotion 50: Scythe, Grave Scythe, Halo Scythe, Winged Scythe, Obsidian Lamina) | `AttackLeftHeavy5` (`AttackLeftHeavy5a00_CMSG`, offsetType 0xe) | `a050_035040` (2) | `a023_035040` straight sword (1) | not traced | DATA, reachability unknown |

Magic (offsetType 0x11) has the same shape, but none of it hits. `a449` and `a457` (riding
casts) have stub clips that fall back to `a999_*` / `a401_*` / `a407_*`, all with 0 hit events.

## 4. Limits of this sweep

- `DATA`: whether a category "has" an animation is read from its TimeAct. The game's check
  (`FUN_1403c4b80`) asks the loaded animation set instead. The `.hkx` binders in the extract do
  not cover every category, so they could not be used to cross-check.
- `DATA`: weapon-category resolution here only models `spAtkcategory`, then
  `wepmotionCategory`, then the per-hand fallback. Which physical hand is 0 and which is 1 was not
  traced. Both are modelled for 0x10.
- `INFERRED`: everything in 3a assumes the follow-up state activates after the equip lands, which
  is what the measured stance case did. Only the stance chain has been reproduced
  (drive-watch25-27). The others are candidates for `scripts/frida/chainsaw-driver.js`.
- The hks routing that decides which skills enter `SwordArtsOneShot_111/_110` and the
  heavy-special weapon states was not traced.
