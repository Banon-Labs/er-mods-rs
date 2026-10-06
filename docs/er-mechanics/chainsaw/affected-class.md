# Chainsaw glitch: the affected class

The bug: a right-hand skill that keeps attacking while L2 is held goes on attacking after the
right-hand weapon is swapped mid-skill through the equipment menu, and the new weapon plays the
borrowed animation. This page answers three things offline. Which skills repeat while held. Which
weapons carry them. What the game checks about the weapon in the swinging hand while the loop
runs.

Labels:

- `HKS`: read from the player behavior script `action/script/c0000.hks` with
  `scripts/er-hks-disasm.py`. Line numbers are the script's own debug lines.
- `BEH`: read from the behavior graph `c0000.behbnd` with `scripts/er-behbnd-attack-map.py`.
- `TAE`: read from the player TimeAct `a<cat>.tae`.
- `REG`: read from the installed regulation (1.17.1) with `scripts/er-param-read.py`.
- `VERIFIED`: an executable function already pinned in `er-mechanics-ashes.py` /
  `er-mechanics-attacks.py`.
- `INFERRED`: follows from the pieces above but is not traced end to end.

The HKS line numbers, behavior graph and TimeAct below were first read from the 2026-07-13
extract (`~/er-extract/LOOK_HERE_ALL_ASSETS_20260713`, `LOOK_HERE_WITCHY_RECURSIVE_20260713`),
which predates 1.17. They were re-checked on 2026-10-04 against the installed 1.17.1 game:
`c0000.hks`, `c0000.behbnd.dcx` and the `c0000*.anibnd.dcx` binders were extracted with the same
Nuxe headless extractor into `~/er-extract/1171-20261004` (log beside it), and the behbnd and
`c0000.anibnd.dcx` unpacked with WitchyBND into `~/er-extract/1171-20261004-witchy/chr`. The rule
and everything this page draws from it are unchanged:

- `IsAttackStanceArts` is identical: the same seven ids, still at line 689.
- `DrawStanceRightLoop_Upper_onUpdate`, `DrawStanceRightStart_Upper_onUpdate`,
  `DrawStanceRightEnd_Upper_onUpdate`, `ArtsStanceCommonFunction`, `CrossbowStanceCommonFunction`
  and `GetEquipType` are instruction-for-instruction identical (line numbers aside). So the L2
  release exit, the FP-out exits for 25 and 239, and the bow/crossbow diversion are unchanged.
- The stance states play the same clips: Start 040050/055, Loop 040051/056, End 040053/054/058.
- The five attack-stance skill TimeActs (`a610`, `a611`, `a625`, `a839`, `a909`) are
  byte-identical. Ids 340 and 341 have no TimeAct in `c0000.anibnd` in either copy.

What 1.17 did change, all for two new skills and none on the attack-stance path:

- `IsStanceArts` adds 372 (Muleta, SwordArtsParam 1200). Its 040051 has 0 hits (`a972.tae`, new),
  so it is a pose stance and does not join the class.
- `ExecArtsStance` goes straight to `Event_DrawStanceRightLoop` when SpEffect 191001 is active, as
  it already did for 19921 (1.17.1 line 2645).
- New state `DrawStanceRightAttackHeavyCancel` (clips 040071/040076) and its HKS update function.
  `DrawStanceRightAttackLight/Heavy_onUpdate`, `SwordArtsOneShotComboEnd_onUpdate` and `ExecAttack`
  add Muleta follow-up branches gated on `c_SwordArtsID == 372` and SpEffect 191000;
  `SetSwordArtsPointInfo` and `ExecAttack` add a 373 (Causality's Wrath, row 1201) case.
- `SwordArtsOneShot_onUpdate` adds 131 (Royal Knight's Resolve) and 92 (Parry) to its opening id
  list. The `c_SwordArtsID + 600` TimeAct category is unchanged.
- `SetSwordArtsPointInfo` also adds 96 (Storm Wall) and 99 (Thops's Barrier). `ExecFallStart` adds
  an SpEffect 4070 check.
- Changed player TimeActs: `a00`, `a415`, `a692`, `a693`, `a696`, `a699`, `a953`; new `a269`,
  `a972`, `a973`.

Line numbers on this page are the 2026-07-13 copy's. In 1.17.1 they are the same up to line 694,
`ExecArtsStance` is 29 lines later (2602-2653), the three `DrawStanceRight*_Upper_onUpdate`
functions are 32 lines later (loop 11531-11632), and the `+ 600` is at line 12331. The regulation
rows are current. Every table below is printed by:

    python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-chainsaw-class.py [--json]
    python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-chainsaw-class.py --selftest   # 21 checks

## 1. The rule

A skill repeats while L2 is held when both of these are true:

1. Its `SwordArtsParam.swordArtsTypeNew` is one of the ids `IsAttackStanceArts` lists:
   **10, 11, 25, 239, 309, 340, 341** (`HKS` line 689, a chain of `EQ` returning `TRUE`).
2. Its loop clip `a<600+swordArtsTypeNew>_040051` carries a hitbox (`TAE` event 1 or 2).

No `SwordArtsParam` column alone separates these skills from the other stances. `swordArtsType` is
0 on all 278 rows (`REG`), and `artsSpeedType` is 1 on Wild Strikes but 2 on Spinning Chain. The `useMagicPoint_L1/L2/R1/R2`
split doesn't separate them either: Wild Strikes is 2/10/15 on L2/R1/R2 and Spinning Wheel is
3/-1/-1. The class is decided by the behavior script's id list, applied to `swordArtsTypeNew`.

How the rule is built:

- `c_SwordArtsID` is `swordArtsTypeNew`. `SwordArtsOneShot_onUpdate` (`HKS` line 12217) adds 600
  to it and passes the sum to `env(1114, ...)` as the TimeAct category. The skill TimeAct is
  `a<600 + swordArtsTypeNew>.tae` (`TAE`, `er-mechanics-ashes.py SKILL_TAE_BASE`).
- `ExecArtsStance` (`HKS` lines 2573-2623) starts every stance skill. For the attack-stance ids it
  requires FP above 0 (`env(1001)`, line 2585) and L2 held (`env(1106, ACTION_ARM_L2)`, line 2588).
  It then fires `Event_DrawStanceRightStart` (line 2619). When one-handing (`c_Style == HAND_RIGHT`)
  it refuses if the skill hand holds a bow, light bow, greatbow or ballista, or the left hand holds
  a crossbow or ballista (lines 2577-2581).
- The stance states play fixed clip ids (`BEH`):

  | event | state | clips |
  |---|---|---|
  | `W_DrawStanceRightStart` | DrawStanceRightStart | 040050, 040055 |
  | `W_DrawStanceRightLoop` | DrawStanceRightLoop | 040051, 040056 |
  | `W_DrawStanceRightEnd` | DrawStanceRightEnd | 040053, 040054, 040058 |
  | `W_DrawStanceRightAttackLight` / `Heavy` | R1/R2 follow-ups | 040060/040065, 040070/040075 |

- `DrawStanceRightLoop_Upper_onUpdate` (`HKS` lines 11499-11600) stays in the loop until one of
  these happens:
  - L2 is released: `env(1108, ACTION_ARM_L2) <= 0` or `env(1107, ACTION_ARM_L2)` (line 11588).
  - FP runs out while `c_SwordArtsID` is 239. That covers FP at 0 or below (line 11567) and
    `IsEnoughArtPointsL2 == 0` (line 11571).
  - FP is at 0 or below while `c_SwordArtsID` is 25 (line 11577).

  An R1/R2 follow-up also leaves the loop (`ArtsStanceCommonFunction`, line 11547). The only
  weapon checks are whether the skill hand holds a bow type (line 11510, which goes to the
  arrow-stance code) or a crossbow (line 11520, which goes to the crossbow code).

The other stance skills share these states (`IsStanceArts`, `HKS` line 672). Their 040051 holds
a pose and creates no hitbox, so holding L2 keeps them in place without attacking (`TAE`, measured by
the script):

Unsheathe (114), Square Off (115), Transient Moonlight (1178), Night-and-Flame Stance (1019),
Overhead Stance (4110), Wing Stance (4120), Moon-and-Fire Stance (5170), and the bow skills Through
and Through (400), Barrage (401), Mighty Shot (402), Enchanted Shot (404), Sky Shot (405), Rain of
Arrows (406), Radahn's Rain (1169), Repeating Fire (5340), Fan Shot (5360), Igon's Drake Hunt
(4210), Rancor Shot (5600) and, in 1.17.1, Muleta (1200) all have 0 hits on 040051. `SwordArts 121` (swordArtsTypeNew 21) has no 040051.

### Validation against the named skills and negatives

| skill | SwordArtsParam | swordArtsTypeNew | in `IsAttackStanceArts` | 040051 hits (`TAE`, on a carrier) |
|---|---|---|---|---|
| Wild Strikes | 110 | 10 | yes | 2 events, judges 3500, 3510 (Battle Axe) |
| Spinning Chain | 125 | 25 | yes | 3 events, judge 4004 (Nightrider Flail) |
| Spinning Wheel (Ghiza's Wheel) | 1039 | 239 | yes | 8 events, judges 3900, 3902 |
| Unending Dance (Dancing Blade of Ranah) | 5090 | 309 | yes | 12 events, judges 6671-6682 |
| Lion's Claw (negative) | 100 | 0 | no | no 040051 (one shot, `W_SwordArtsOneShot` 040000) |
| Quickstep (negative) | 800 | 155 | no | no 040051 (step clips 040080-040688) |
| Square Off (negative, a stance) | 115 | 15 | no (in `IsStanceArts`) | 040051 present, 0 hits |
| Starcaller Cry (the usual target's own skill) | 1032 | 232 | no | no 040051 |

The tutorial calls Ghiza's skill "Ghiza's Wheel" and Ranah's skill "Dancing Blade of Ranah", but
those are the weapon names. The skills are Spinning Wheel (`EquipParamWeapon` 23100000
`swordArtsParamId` 1039) and Unending Dance (7520000 `swordArtsParamId` 5090) (`REG`).

## 2. Every source skill and the weapons that carry it

`IsAttackStanceArts` has seven ids. Five of them have a `SwordArtsParam` row. Ids 340 and 341 have
no row in the 1.17.1 regulation, so no weapon can fire them (`REG`). Spinning Strikes (111) is a fifth
source the tutorial does not name.

"Locked" means the weapon has `gemMountType` 0, so no ash can replace its skill. "Ash slot" means
`gemMountType` 2. Ash mounting is `CanMountGemWithAffinityOnWeapon` 0x140d549d0 (`VERIFIED`):
`canMountWep_<class>` on the gem row selected by `EquipParamWeapon.wepType`
(`CheckIfWepTypeCanEquipGem` 0x140d29e00), checked at the gem's `defaultWepAttr` and the weapon's top
upgrade level. Base player weapons only (id a multiple of 10000, not `[NPC]`).

### Wild Strikes (SwordArtsParam 110, swordArtsTypeNew 10, TimeAct a610)

FP `useMagicPoint` L2/R1/R2 = 2/10/15.

The weapons that have it built in are all axes except one:

- Battle Axe 14000000, ash slot
- Jawbone Axe 14030000, ash slot
- Iron Cleaver 14040000, ash slot
- **Ripple Blade 14050000, locked**
- Celebrant's Cleaver 14060000, ash slot
- Sacrificial Axe 14110000, ash slot
- Smithscript Axe 14500000, ash slot
- Great Omenkiller Cleaver 15020000 (greataxe), ash slot

The ash is "Ash of War: Wild Strikes", `EquipParamGem` 11000 (`rank` 0, `isSpecialSwordArt` 0,
`defaultWepAttr` 1). Its mount flags are `AxeNormal`, `AxeLarge`, `Flail`, `GreatKatana`,
`HammerNormal`, `HammerLarge`, `SaberNormal`, `SaberLarge` and `SwordLarge`. It mounts on 72
weapons:

- **Axes (11):** Battle Axe, Forked Hatchet, Hand Axe, Jawbone Axe, Iron Cleaver, Celebrant's
  Cleaver, Highland Axe, Sacrificial Axe, Smithscript Axe, Messmer Soldier's Axe, Warped Axe
- **Greataxes (8):** Greataxe, Great Omenkiller Cleaver, Crescent Moon Axe, Longhaft Axe, Rusted
  Anchor, Executioner's Greataxe, Butchering Knife, Gargoyle's Great Axe
- **Flails (3):** Nightrider Flail, Flail, Chainlink Flail
- **Great katanas (2):** Great Katana, Reed Great Katana
- **Hammers (9):** Mace, Club, Curved Club, Warpick, Morning Star, Spiked Club, Hammer, Monk's
  Flamemace, Stone Club
- **Great hammers (12):** Large Club, Greathorn Hammer, Battle Hammer, Great Mace, Curved Great
  Club, Celebrant's Skull, Pickaxe, Great Stars, Brick Hammer, Rotten Battle Hammer, Smithscript
  Greathammer, Black Steel Greathammer
- **Curved swords (11):** Falchion, Beastman's Curved Sword, Shotel, Shamshir, Bandit's Curved
  Sword, Flowing Curved Sword, Scavenger's Curved Sword, Serpent-God's Curved Sword, Mantis Blade,
  Scimitar, Grossmesser
- **Curved greatswords (6):** Dismounter, Omen Cleaver, Monk's Flameblade, Beastman's Cleaver,
  Freyja's Greatsword, Hefty Scimitar
- **Greatswords (10):** Bastard Sword, Forked Greatsword, Iron Greatsword, Lordsworn's
  Greatsword, Knight's Greatsword, Flamberge, Banished Knight's Greatsword, Claymore, Gargoyle's
  Greatsword, Lizard Greatsword

### Spinning Strikes (SwordArtsParam 111, swordArtsTypeNew 11, TimeAct a611)

FP L2/R1/R2 = 2/10/15. 040051 has 13 hit events (judges 4010, 4020) on Short Spear.

No weapon has it built in. The ash is "Ash of War: Spinning Strikes", `EquipParamGem` 11100
(`defaultWepAttr` 3), with mount flags `SpearNormal`, `SpearAxe` and `Sickle`. It mounts on 24
weapons:

- **Spears (12):** Short Spear, Spear, Clayman's Harpoon, Partisan, Celebrant's Rib-Rake, Pike,
  Cross-Naginata, Spiked Spear, Iron Spear, Smithscript Spear, Swift Spear, Bloodfiend's Fork
- **Halberds (10):** Halberd, Pest's Glaive, Lucerne, Banished Knight's Halberd, Nightrider
  Glaive, Vulgar Militia Saw, Glaive, Guardian's Swordspear, Vulgar Militia Shotel, Gargoyle's
  Halberd
- **Reapers (2):** Scythe, Grave Scythe

### Spinning Chain (SwordArtsParam 125, swordArtsTypeNew 25, TimeAct a625)

FP L2/R1/R2 = 8/8/10.

Built in on Nightrider Flail 13000000, Flail 13010000 and Chainlink Flail 13040000, all with an ash
slot (`gemMountType` 2). There is no ash: no `EquipParamGem` row grants SwordArtsParam 125. So these
three flails are the only carriers. Mounting another ash on one of them removes the skill.

### Spinning Wheel (SwordArtsParam 1039, swordArtsTypeNew 239, TimeAct a839)

FP L2/R1/R2 = 3/-1/-1. Built in on **Ghiza's Wheel 23100000**, which is locked (`wepType` 41, the
`AxhammerLarge` mount class). There is no ash.

### Unending Dance (SwordArtsParam 5090, swordArtsTypeNew 309, TimeAct a909)

FP L2/R1/R2 = 2/-1/-1. Built in on **Dancing Blade of Ranah 7520000**, which is locked (`wepType`
9, a curved sword). There is no ash. Row 7680000 "[NPC] Dancing Blade of Ranah" carries the same
skill but is not a player item.

### Unique weapons whose skill is locked

There are three locked sources: Ripple Blade (Wild Strikes), Ghiza's Wheel (Spinning Wheel) and
Dancing Blade of Ranah (Unending Dance). The three flails carry Spinning Chain with an ash slot, so
they are not locked, but nothing else can carry that skill.

## 3. Which weapons can be the target

**The skill animation does not come from the weapon.** Weapon attacks play from
`a<wepmotionCategory>.tae`. Skill animations play from `a<600 + swordArtsTypeNew>.tae` (`HKS` line
12217, `TAE`). No stance clip is a per-category file, so a weapon of any motion category has every
clip the source skill needs. Weapon class gates the skill only through ash mounting, and a mid-skill
swap skips mounting entirely. (`INFERRED`: the behavior graph keeps playing the source's category
after the swap. The tutorial's observed result agrees, but nothing static shows when the TimeAct
category is resolved.)

**The hitboxes resolve against the target weapon.** A TimeAct attack judge resolves through
`PlayerIns::ResolveBehaviorId` 0x140652280 (`VERIFIED`) using the weapon's `behaviorVariationId`.
For judges above 999 it falls back to the shared row `kind * 100000000 + low`. All of the loop
judges are above 999: 3500/3510, 4004, 3900/3902, 4010/4020 and 6671-6682. So every weapon resolves
them to the same `BehaviorParam_PC` and `AtkParam_Pc` rows, scaled by its own attack rating. The
script measures this on Starscourge Greatsword (`behaviorVariationId` 405, wepmotionCategory 26):

| Wild Strikes judge | behavior row | AtkParam_Pc | physical MV |
|---|---|---|---|
| 3500 | 300000500 | 301401800 | 107 |
| 3510 | 300000510 | 301401810 | 114 |

**The behavior script excludes only bows and crossbows.** `GetEquipType(hand, ...)` is
`env(225, hand)` tested against the listed categories (`HKS` lines 263-280).
`DrawStanceRightLoop_Upper_onUpdate` diverts the loop if the skill-hand weapon is one of these:

| `WEAPON_CATEGORY_*` (wepmotionCategory) | wepType | where it goes |
|---|---|---|
| SMALL_ARROW (51) | 50 light bow, 51 bow | arrow stance (`ArrowCommonFunction`, line 11510) |
| ARROW (44) | 51 bow | arrow stance |
| LARGE_ARROW (45) | 53 greatbow | arrow stance |
| CROSSBOW (46) | 55 crossbow | crossbow stance (`CrossbowStanceCommonFunction`, line 11520) |

The mapping from motion category to `wepType` is `REG`. That `env(225)` returns
`wepmotionCategory` is `INFERRED` (bd `hks-weaponcategoryid-guard-table-2026-09-29`). Every other
class falls through to the normal loop: swords, colossal weapons, fists, staves (41), seals (also
41), torches (21) and shields (47-49, 57). No line of the loop update reads `c_SwordArtsID` to
check that the skill still matches the weapon in the hand. That missing check is the hole the
glitch uses.

One side effect of the FP exits comes straight from the HKS (`INFERRED` that `c_SwordArtsID`
follows the equipped weapon each frame, which the `c_` engine-provided globals suggest but nothing
here proves). The out-of-FP exit only fires while `c_SwordArtsID` is 25 or 239. After a swap,
`c_SwordArtsID` would be the target's skill (Starscourge Greatsword: 232). The release check at
line 11588 has no such condition, so it still applies.

## 4. The off-hand prerequisite

Which hand's skill L2 fires is decided by `FUN_14047f770` (`VERIFIED`, `AshTables.active_skill`).
Two-handing always uses the two-handed weapon's skill. One-handing uses the left weapon's skill
unless the left skill row has `isRefRightArts` 1. 47 of the 278 `SwordArtsParam` rows have
`isRefRightArts` 0 (`REG`), and a left weapon carrying one of these takes L2 for itself:

- No skill: `SwordArts 0` (0), `No Skill` (1)
- Shield skills: Shield Bash (300), Barricade Shield (301), Parry (302), Buckler Parry (303),
  Carian Retaliation (305), Storm Wall (306), Golden Parry (307), Shield Crash (308), Thops's
  Barrier (309), Shield Strike (8000)
- Bow skills: Through and Through (400), Barrage (401), Mighty Shot (402), Enchanted Shot (404),
  Sky Shot (405), Rain of Arrows (406), `SwordArts 407` (407)
- Others: Torch Attack (117), Firebreather (223), Vow of the Indomitable (701), Holy Ground (702),
  Flame Spit (1001), Tongues of Fire (1002), Viper Bite (1007), Bear Witness! (1011), Radahn's
  Rain (1169), Fires of Slumber (1195), Golden Retaliation (1196), Contagious Fury (1197), Igon's
  Drake Hunt (4210), Kick (4990), Sleep Evermore (5210), Moore's Charge (5230), Feeble Lord's
  Frenzied Flame (5330), Repeating Fire (5340), Fan Shot (5360), Discus Hurl (5370), Revenge of the
  Night (5440), Blindfold of Happiness (5460), `SwordArts 5461` (5461), Roaring Bash (5480),
  Impaling Thrust (5590), Rancor Shot (5600), `SwordArts 9991` and `9992`

Note that `No Skill` (1) is one of them. Row 10, the `No Skill` that seals, torches and bare hands
use, has `isRefRightArts` 1 and defers. Separately, `ExecArtsStance` refuses to start a stance while
one-handing if the left hand holds a crossbow or ballista (section 1).

## Not answered here

- Whether the equipment menu accepts a weapon change during the stance loop, and when the TimeAct
  category is re-read. Both happen inside the executable, not in the HKS. The tutorial's observed
  result is the only evidence, and confirming it needs a runtime trace.
- The script still defaults to the 2026-07-13 paths. To read the 1.17.1 copy, set
  `ER_HKS_FILE=$HOME/er-extract/1171-20261004/action/script/c0000.hks` and
  `ER_PLAYER_TAE_DIR=$HOME/er-extract/1171-20261004-witchy/chr/c0000-anibnd-dcx-wanibnd/INTERROOT_win64/chr/c0000/tae`.
  With both set, `--selftest` passes 21/21, and the report matches the 2026-07-13 one except for
  the added Muleta row.
