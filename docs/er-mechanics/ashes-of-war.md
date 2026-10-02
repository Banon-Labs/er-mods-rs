# Ashes of war and weapon skills

Labels as in [attacks.md](attacks.md): **VERIFIED** = read in the executable (1.16.2 VAs from the
named Ghidra dump on :8765, shift 0 against `eldenring-deobf.bin`) or a value in the installed
1.17.1 `regulation.bin`; **TAE** = decoded from the player TimeAct files; **INFERRED** =
consistent with the data, consuming code not traced; **COMMUNITY** = outside claim (Smithbox row
names, wiki); **MEASURED** = counted in the planner corpus. Nothing was launched.

Tool: `scripts/er-mechanics-ashes.py` (commands at the end). `--selftest` passes 97/97.

## 0. In plain words

A weapon skill is an animation, and the animation is a timeline of events. Each event says "at
frame N, do X". There are only a few kinds of X that matter, so every skill in the game is a
combination of them:

- **Melee hitbox**: the weapon swings and hits like an R1 with a bigger motion value. Damage is
  your weapon's attack rating times that motion value. Lion's Claw is one 240% hit.
- **Bullet**: the game spawns an object (a "bullet" in FromSoftware's data, whether it is a
  projectile, a shockwave, a mist cloud or a lingering area). Storm Stomp, Hoarfrost Stomp, Ice
  Spear's spear, Flame of the Redmanes and Beast's Roar are bullets. A bullet does **not** use your
  attack rating times a percentage. It has a flat number (Storm Stomp: 50 physical, Hoarfrost: 40
  then 100 magic), multiplied by how far the weapon is upgraded and by the weapon's stat scaling.
  A +25 weapon quadruples it. Some bullets also replace the weapon's scaling with their own table
  (Storm Stomp scales STR and DEX at a fixed 15), so a Storm Stomp from a colossal weapon hits no
  harder than one from a dagger at the same upgrade and stats.
- **SpEffect** (a timed status): the skill puts a status on you (a buff) or, through a bullet, on
  whoever it touches (frostbite, bleed build-up, or Golden Vow's ally buff). Endure, Royal
  Knight's Resolve, Determination, the roars, Golden Vow and Barricade Shield are only this.
- **Hyperarmor window**: for some frames your poise is raised, so you are not staggered.
- **Invincibility frames**: Quickstep and Bloodhound's Step are nothing but i-frames and motion.
- **Parry window**: Parry, Buckler Parry, Golden Parry.

"Some release bullets and others have bullets and buffs" is exactly this: the skill's timeline
lists a melee event, a bullet event and a SpEffect event in any combination. Chilling Mist is a
buff on your weapon plus a thrust plus a frost mist; Waves of Darkness is two swings and a magic
wave; Hoarfrost Stomp is a frost bullet that leaves frostbite on its target.

Two rules decide how much a skill hits for:

1. Skill hits get their own motion values, and in PvP almost every skill hit (and its bullets) is
   multiplied by **0.8**, where an R1 is multiplied by 1.0 (the `FinalDamageRateParam` row the
   attack names).
2. FP: a skill costs its FP when the animation reaches the charge frame. If you start it with at
   least **half** its cost, you get the full skill and your FP drops by the cost, stopping at 0.
   With less than half, you get a weaker copy of the animation ("No FP" rows: Lion's Claw 138%
   instead of 240%, Endure's buff for 1 s instead of 3 s, Royal Knight's Resolve x1.08 instead of
   x1.4).

What your L2 does: two-handing uses the two-handed weapon's skill. One-handing uses the **left**
weapon's skill, unless that skill is marked "refer to the right hand" (`isRefRightArts`). Staves,
seals and 231 of the 278 skill rows carry that mark; the 47 that do not are the shield skills
(Parry, Carian Retaliation, Barricade Shield...), bow skills, Torch Attack, Firebreather and some
unique skills. So a shield's skill wins over the right weapon's, and almost anything else in the left
hand defers to the right weapon.

## 1. The chain

```
equipped weapon (+ mounted EquipParamGem)
  GetSwordArtsParamIdForWeapon 0x140673f70:  gem mounted ? EquipParamGem.swordArtsParamId
                                                         : EquipParamWeapon.swordArtsParamId
  which hand (FUN_14047f770):  2H -> that weapon; 1H -> left weapon's SwordArtsParam unless its
                               isRefRightArts != 0, then the right weapon's
SwordArtsParam  -> swordArtsTypeNew
  animation offset (FUN_14041aba0 case 0x12) = (600 + swordArtsTypeNew) * 1000000
  -> TimeAct a<600 + swordArtsTypeNew>.tae, animations 04xxxx (Lion's Claw type 0 -> a600)
  -> behavior script picks the animation: 040000 with enough FP, 040005 without, other ids for
     follow-ups, directions and grips
each animation event:
  1   AttackBehavior(judge)        melee hitbox, judge via ResolveBehaviorId 0x140652280
  2   BulletBehavior(judge)        bullet, judge via ResolveBehaviorId (0x140426e60)
  307 PCBehavior(flags, judge)     flag 8: like event 1; flag 4: BehaviorParam_PC[judge] as is
  5   CommonBehavior(row)          BehaviorParam_PC[row] as is
  330 WeaponArtFPConsumption       FP charge
  331 AddSpEffect_WeaponArts(a, b) SpEffect a if started with enough FP, else b
  66/67/401 AddSpEffect(id)        SpEffect on self
  795 hyperarmor window,  0 with JumpTable 8 invincibility
judge -> BehaviorParam_PC row (kind 3: rows 3xxxxxxxx; the weapon's behaviorVariationId, with
         the family and 300000000+judge%1000 fallbacks of attacks.md section 0)
  refType 0 -> AtkParam_Pc              (melee)
  refType 1 -> Bullet -> atkId_Bullet -> AtkParam_Pc, spEffectId0-4 on hit, HitBulletID /
               intervalCreateBulletId child bullets
  refType 2 -> SpEffectParam, applied to the attacker
```

- VERIFIED: every function named above, decompiled on :8765. The skill TimeAct number is computed
  in the EXE; the local ids 040000/040005 are not (no instruction carries 0x9c45 as an immediate),
  so the choice between them is the behavior script's (INFERRED from the TAE: 040005 lacks event
  330 and fires the rows Smithbox names `No FP`).
- TAE: 274 of 278 SwordArtsParam rows have their `a<600 + type>.tae`; the exceptions are rows
  104, 10, 1200, 1201, and files a660, a707, a938, a939 have no row.
- VERIFIED: only event 2 honours refType 1 and 2. Events 1, 5 and 307 hand any refType other than
  0 to `FUN_1404428f0`, which finds no AtkParam and creates nothing. A refType 2 row reached from a
  bullet event goes to the attacker (`0x1403c1020 -> FUN_1403e8b70`).
- VERIFIED: 307 flag 4 with judge 500/504 (Quickstep, Bloodhound's Step, Lion's Claw, the slams)
  reads BehaviorParam_PC 500/504 -> AtkParam 300 (flat 30 physical) / 304 (nothing), both
  `hitSourceType` 1. It is a generic body hitbox shared by every weapon. The tool reports it as
  `body hitbox` and never counts it as a skill hit.
- Skill rows are mostly shared across weapons: Lion's Claw judge 3000 resolves to 300000000 on a
  Claymore (variation 318) and on a Greatsword (401) alike, through the third fallback. A few
  skills have per-category rows (Wild Strikes, Spinning Weapon).

## 2. Mounting and affinity

VERIFIED, `CanMountGemWithAffinityOnWeapon` 0x140d549d0:

1. `EquipParamWeapon.gemMountType` must be 1 or 2.
2. `CheckIfWepTypeCanEquipGem` 0x140d29e00 reads the gem's `canMountWep_<class>` bit chosen by
   `EquipParamWeapon.wepType` (a switch; the table is `WEP_TYPE_MOUNT_FLAG` in the tool:
   1 Dagger, 3 SwordNormal, 5 SwordLarge, 7 SwordGigantic, ... 95 BeastClaw).
3. Affinity: allowed when the weapon's `disableGemAttr` bit is set and the affinity is Standard;
   otherwise when the gem's `configurableWepAttr<NN>` bit for that affinity is set (24 bits at gem
   +0x30/+0x31/+0x33, `FUN_140d2a4d0`); otherwise only when it equals `defaultWepAttr`.
4. `EquipParamGem.rank` must not exceed `ReinforceParamWeapon.enableGemRank` at the weapon's level.
5. `restrictSpecialSwordArt` on the weapon refuses gems with `isSpecialSwordArt`.
6. `_CanMountGem` 0x14078a3f0 adds the whetblade gate: until the affinity's unlock event flag
   (`unlockEventFlagId + step * affinity`) is set, only `defaultWepAttr` is offered.

REGULATION: several EquipParamGem rows name the same ash. The low ids (40..191, 1000) carry
`sortId` 999999 and different mount bits (row 40 lets Lion's Claw onto daggers, row 10000 does
not); the tool uses the rows with a real sort order as the inventory items (INFERRED).

Affinity changes skill damage only through the weapon: the affinity picks the weapon row
(base + affinity x 100), which changes base attack and scaling. The ash's own damage rows do not
change with affinity.

## 3. FP cost

VERIFIED (`CSChrSwordArtsModule`, 1.16.2; the 1.17.1 copy of `HaveEnoughFP` is at 0x1404801c0
and reads 0.5 at 0x1432a1920):

- The behavior script reserves the skill with a cast number (0 R1, 1 R2, 2 L1, 3 L2) and a hand
  (`UpdateActiveAowFpStats` 0x14047fb10). Cost = `ceil(rate x SwordArtsParam.useMagicPoint_<cast>)`
  (`CalculateFpConsumption` 0x14068b220), `rate` = product of active
  `SpEffectParam.artsConsumptionRate` (the only multiplier; Carian Filigreed Crest works here).
  `-1` in a column means that input does not cast the skill.
- Enough FP = `fp > 0 and int(cost x 0.5) <= fp` (`CanCastAow` 0x14047fa20). Decided at
  reservation and frozen there.
- Event 330 (`ConsumeFp` 0x14047f640) spends the reserved cost if enough, clamped at 0 FP, once
  per reservation. Wild Strikes fires 330 on every swing because each swing is a new reservation.
- With too little FP the script plays the no-FP copy, and event 331 applies its second SpEffect.
  That second id is sometimes a penalty rather than a weaker buff: Wild Strikes' 865 (attack,
  stamina and poise damage x0.55), Igon's Drake Hunt's 120410 (x0.75 attack, x1.5 stamina use).
- TAE: Carian Retaliation has no event 330 at all. Its parry bullet 2650 -> 2651 applies 1515
  (`changeMpPoint` 8, `behaviorId` 2140) to you when it connects; INFERRED that the 8 FP are
  charged there, only on a successful parry.

## 4. Damage

VERIFIED, the attack-power builder `0x1406832a0` (attacks.md section 1), resolved for skills:

```
from the weapon (every bullet; melee unless the row says otherwise):
  attack_el = (base_el x MV_el / 100 + (isAddBaseAtk ? flat_el x baseAtkRate : 0)) x scaling_el
not from the weapon (melee row with hitSourceType != 0, event without flag 8):
  attack_el = flat_el x scaling of the weapon at AttackInfo+0xf0 (none for melee)
```

- `base_el` = weapon base attack x `ReinforceParamWeapon.*AtkRate`, `scaling_el` =
  `FUN_140690390`; `base x scaling` is the weapon's AR. `MV_el` = `AtkParam_Pc.atk*Correction`,
  `flat_el` = `atkPhys/atkMag/atkFire/atkThun/atkDark`.
- `k` in attacks.md is resolved: `FUN_140d53bf0` returns `ReinforceParamWeapon.baseAtkRate`
  (+0 1.0, +10 standard 2.2, +25 or somber +10 4.0), the same for every affinity.
- Bullets: `0x14038e380` fills the bullet's attack from the firing hand's weapon and never sets
  `attackNotFromWeapon`, so a bullet always takes the first branch. Skill bullet rows have MV 0
  and a flat value, so **a bullet's damage is flat x baseAtkRate x scaling, and the weapon's own
  attack rating does not enter**. Storm Stomp 30300870 is 50 physical, Hoarfrost 30300863 40
  magic, Ice Spear 300200075 230 magic, Flame of the Redmanes 30020930 180 fire.
- `AtkParam.overwriteAttackElementCorrectId` swaps the scaling table: Storm Stomp's bullet uses
  AttackElementCorrect 51030, which scales every element by STR and DEX at a fixed 15, whatever
  the weapon's own scaling. 138 of the 2314 skill AtkParam rows (ids 3xxxxxxx, 3xxxxxxxx, 6xxxxxxxx) override.
- Upgrade and affinity therefore both matter for bullets: upgrade through `baseAtkRate` and
  scaling, affinity through scaling only.
- `BulletParam.isAttackSFX` does not touch damage: Smithbox's paramdef describes it as "stays
  stuck in the character" (arrows).
- Melee skill hits have no skill multiplier of their own; the MV is the whole difference
  (Blood Tax 88/44/44/110 on a Claymore, Ice Spear's thrust 80, Lion's Claw 240).
- VERIFIED, buffs that reach a skill hit: `BehaviorParam.category` is copied to the attack and
  `IsApplicableForCategory` 0x140500930 filters SpEffects by their `wepParamChange`: category 1
  (R1) takes values other than 2/3/4; category 12 (most melee skill rows) takes 1 and the 0/5/6
  group; category 9 (Storm Stomp melee, Kick) the 0/5/6 group or 4; category 0 (skill bullets) the
  0/5/6 group only. A weapon buff with `wepParamChange` 1 (Royal Knight's Resolve, Cragblade,
  Braggart's Roar) therefore raises melee skill hits but not skill bullets. The `subCategory`
  mask (112 on skill rows) adds the skill-specific talismans and armor (Shard of Alexander
  312310, Warrior Jar Shard 312300; INFERRED that 112 means "skill").
- PvP (VERIFIED `CalculateDamageCorrections` 0x140684d70): when attacker and defender are both
  players, each element is multiplied by `FinalDamageRateParam[AtkParam.finalDamageRateId]` of
  the hit's own row (for a bullet, `atkId_Bullet`'s row). 1024 of the 3xxxxxxxx rows use 10000
  = **0.8**; R1s use 1.0. Flaming Strike's bullet uses 0.65, Spinning Slash 0.7. The weapon's
  `vsPlayerDmgCorrectRate_*` and the attacker/defender SpEffect `atk/defPlayerDmgCorrectRate_*`
  sums apply on top.
- Poise damage: melee skill hits follow attacks.md section 2 (`saWeaponDamage x
  atkSuperArmorCorrection`); the tool applies the same weapon formula to bullets (INFERRED).

## 5. Buffs

VERIFIED (`CSChrTaeAnimEvent::AddSpEffect` 0x14042bfd0, 331 at 0x14042e627, 401, and the expiry
path `RequestTimeReset` 0x1404faa00 / `FUN_1404fae40` / tick `FUN_140501020`):

- 66 and 67 (and 331) apply the SpEffect on the event's first frame and keep it alive while the
  event runs. It then lasts until **the later of the event's end and apply time +
  `effectEndurance`**; `effectEndurance` -1 never expires, 0 means only the event window.
  66 is sent over the network, 67 is local. 401 applies once and lasts `effectEndurance`.
- When it expires, `replaceSpEffectId` is applied if set.
- Most weapon-art buffs are a two-step chain: a short "trigger" row applied by 331 whose
  `cycleOccurrenceSpEffectId` produces the timed buff. Two parallel chains (`wepParamChange` 1
  and 2) are applied at once; `wepParamChange` is the filter of section 4 (INFERRED: 1 = right
  weapon's attacks, 2 = left weapon's).
- Who a bullet's on-hit SpEffect lands on: the tool treats a row as an effect on the target when
  its `effectTargetSelf` is 0 or it carries status build-up, an HP drain or damage cut rates above
  1 (Hoarfrost's frostbite 1800, Ice Spear's 1530, the mist clouds' 881/883), and as a buff to you
  and allies otherwise (Golden Vow 1730, whose bullet has `isHitBothTeam`). INFERRED from the
  flags and names; the flag is not reliable for events 66/67 (marker 430 has it 0 and lands on
  the player). It cannot be: the game never reads `effectTargetSelf`..`effectTargetGhost`. The
  target test is `effectTargetOpposeTarget` / `FriendlyTarget` / `SelfTarget` against the team
  relation of attacker and victim (status.md section 1b).
- A buff that only lives while its event runs (effectEndurance 0, no timed link) is a "stance
  effect" here: Stamp's 6340, Raptor of the Mists' 1570, Wild Strikes' 6362.

What the popular buffs do (REGULATION; `x0.6 player damage taken` means
`defPlayerDmgCorrectRate_*` = 0.6):

| skill | FP | rows | effect | lasts |
|---|---|---|---|---|
| Endure | 9 | 1650 (1655 on the 040200 variant) / no FP 1651 (1656) | x0.6 damage taken from players (x0.55 from NPCs), `dmgLv_*` 1 on every reaction level (INFERRED: the smallest flinch), `defFlickPower` 35; no FP: x0.95 / x0.9 on fewer reaction levels | 3.0 s (3.5 s) from f4, about f94; no FP 1.0 s (1.5 s, or the event end if later) |
| Royal Knight's Resolve | 15 | 1700/1702 -> 1701/1703; no FP 1705 -> 1706 | x1.4 damage dealt to players (`atkPlayerDmgCorrectRate_*`; x1.8 to NPCs), x4 stamina damage, `atkFlickPower` 100; while active it cycles 1704, x0.75 on critical hits; no FP: x1.08 | 10 s |
| Determination | 10 | 1690/1692 -> 1691/1693; no FP 1695 -> 1696 | x1.3 damage to players (x1.6 to NPCs), x3 stamina damage, criticals x0.75 (1694); no FP: x1.05 | 10 s |
| Braggart's Roar | 16 | 1860/1862 -> 1861/1863; no FP 1866 | physical attack x1.1, x0.9 damage taken (players and NPCs), stamina recovery +10; no FP x1.075 / +7 / x0.95 | 60 s; no FP 6 s |
| Barbaric Roar / War Cry | 16 | 1680/1682, 1810/1812 -> 1681/1683, 1811/1813 | physical attack x1.075, `changeStrengthPoint` 5 (meaning not traced) | 40 s; no FP the same for 6 s |
| Cragblade | 16 | 1820/1822 -> 1821/1823; no FP 1826 | physical attack x1.15, stamina damage x1.5, poise damage x1.1; no FP x1.1 / x1.2 / x1.05 | 60 s; no FP 6 s |
| Sacred Order / Shared Order | 18 / 20 | 1841/1843 -> 1849; 1870 -> 1876 (self), ally hitbox 300000820 `spEffectId1` 1871 -> 1877 | `weakDmgRateB` x2.0 (x1.5 for Shared; INFERRED: vs undead), x1.025 vs players, x1.1 vs NPCs (allies x1.075) | 60 s |
| Shriek of Sorrow | 19 | four 331 tiers at f17-20, `conditionHp` none/85/55/30 | physical x1.075 / 1.1 / 1.15 / 1.2 vs players (x1.1 .. x1.25 vs NPCs); INFERRED that a tier fires only below that HP% | 40 s |
| Raptor of the Mists | 6 | 1570 (stance effect) | every damage cut and `def*DmgCorrectRate` 0: no damage taken while the event runs (f0-20), then the leap with i-frames f0-18 | the event |
| Assassin's Gambit | 5 | 1765 | `changeHpPoint` 35 every 0.2 s over f44-47; the stealth itself is in no row the tool reads | - |
| Golden Vow | 40 | area bullet -> 1730 on self and allies | x1.025 damage to players (x1.115 to NPCs), x0.985 damage taken from players (x0.925 from NPCs) | 45 s |
| Barricade Shield | 12 | 800 -> 801 | guard boost x1.8, guard stamina cost x0.5 | 10 s |
| Seppuku | 4 | 1754/1757 -> 1755/1758, self-bleed 1753 | +30 flat physical attack, and every hit adds bleed 30 (`atkOccurrenceSpEffectId` 1756); 1753 bleeds you once (`bloodAttackPower` 9999: 15% + 100 HP, INFERRED); without FP only the self-bleed | 60 s |
| Stamp (either) | 5 | 6340 (stance effect) | every damage cut rate 0.5 (half damage taken) | only while the stance event runs |
| Holy Ground | 30 | two 35 s area bullets 2070/2072 at f60, each pulsing 2071/2073 every 0.1 s -> 1640, 1641 | every damage taken x0.8 (cut rates and `defPlayerDmgCorrectRate`); 1641 restores 5 HP every 0.3 s (`changeHpPoint` -5) | 2.9 s per pulse, so while you stand in it |
| Sacred Blade / Flaming Strike R2 / Lightning Slash | 19 / 10 / 10 | 820 -> 821, 1775 -> 1776, 1675 -> 1676 | +90 holy (x2 vs undead), +90 fire, +85 lightning on the weapon | 40 s |
| Chilling / Poisonous Mist | 14 | 825 -> 826, 830 -> 831 | frost / poison 60 per hit on the weapon (on-attack rows 880 / 882, both AtkParam rates applied; corrected from 30, status.md section 11). 880 builds frost on players: its `effectTargetPlayer` 0 is a flag the game never reads, and its `effectTargetOpposeTarget` 1 is what lets it land on a hostile player (status.md section 1b) | 40 s |

Flame of the Redmanes is not a buff: it is a fire bullet (180 flat fire, section 4).

## 6. Hyperarmor during skills

TAE event 795 names a ToughnessParam row; the bonus is `100 x correctionRate x weapon
toughnessCorrectRate` internal (x10 for menu units), with the PvP-only reductions of attacks.md
section 2 (`unk1` poise damage taken, `unk2` HP damage taken while the window runs).

| row | correctionRate | floor % | PvP poise dmg taken | PvP HP dmg taken | used by (opening animation) |
|---|---|---|---|---|---|
| 120 | 2.0 | 80 | 0.65 | 0.925 | Giant Hunt f19-62, Sword Dance f12-54, Chilling Mist f12-77 |
| 165 | 0.15 | 15 | 0.5 | 0.925 | Kick f14-32 |
| 168 | 0.18 | 18 | 0.5 | 0.925 | Wild Strikes f0-41 |
| 180 | 0.3 | 30 | 0.5 | 0.925 | Storm Stomp f12-29, Hoarfrost's no-FP stomp |
| 201 | 0.5 | 50 | 0.5 | 0.825 | Prelate's Charge f15-55, Waves of Darkness f15-60, Ground Slam f10-26 |

Rows seen in skill TimeActs but not in attacks.md: 18, 50, 160, 165, 168, 170, 175, 200, 201,
205. When the event's `Args` byte 1 is not 1 or 2 there is no weapon term: the window only
refills poise to the row's floor and applies its PvP rates (Golden Slam's no-FP row 200).

On a Greatsword (`toughnessCorrectRate` 0.09) row 120 adds 180 menu poise; row 180 adds 27. The
bonus scales with the weapon, so the same skill on a dagger (0.011) adds almost nothing.
TAE: Lion's Claw (a600), Endure, Royal Knight's Resolve, Ice Spear, Hoarfrost Stomp (FP) and
Flaming Strike have no event 795 in their opening animation. Whether Lion's Claw gets hyperarmor
some other way is not established here.

### Steps (TAE)

Quickstep and Bloodhound's Step are only motion, invincibility (JumpTable 8), marker 430 and
the zero-damage body hitbox 504 (f3-20). Invincible frames per direction animation:

| animations | Quickstep (3 FP) | Bloodhound's Step (5 FP) |
|---|---|---|
| 040080-040083 | f0-9 | f0-10 |
| 040180-040183 | f0-8 | f0-8 |
| 040580-040583 | f0-9 (040580 also f0-15) | f0-10 (040580 also f0-16) |
| 040680-040683 | f0-8 | f0-8 |
| without-FP copies (+5) | f0-3 | 04008x+5: f0-5; the others none |

Which digit is which direction, and what the 0400/0401/0405/0406 groups select, is the behavior
script's and was not established. The last events end near f51-53 (Quickstep) and f63
(Bloodhound's Step).

## 7. Every ash of war, classified

`python3 scripts/er-mechanics-ashes.py list` (classes read from the animations that are paid
for, section 1). `buff`: a SpEffect that changes a fight stat and outlasts the animation.
`stance effect`: one that only lives while its TimeAct event runs (Stamp's half damage).
`bullet (status only)`: a bullet that deals no damage but puts status on whoever it touches.
`bullet (no damage)`: a bullet that does neither (a visual, a lure, a parry field). `i-frames`:
JumpTable 8 windows. `parry`: an event-1 hitbox of attack type 64.

| id | ash | FP L2/R2 | class | mounts on |
|---|---|---|---|---|
| 100 | Lion's Claw | 20/- | melee | 15 classes, Heavy default |
| 101 | Impaling Thrust | 9/- | melee | 15 |
| 102 | Piercing Fang | 16/- | melee | 12 |
| 103 | Spinning Slash | 6/12 | melee | 17 |
| 105 | Charge Forth | 16/- | melee | 6 (spears, halberds) |
| 106 | Stamp (Upward Cut) | 5/8 | melee + stance effect (half damage taken during the stance, then the attack) | 11 |
| 107 | Stamp (Sweep) | 5/8 | melee + stance effect | 11 |
| 108 | Blood Tax | 14/- | melee (each FP hit heals you, 1830/1831) | 14 |
| 109 | Repeating Thrust | 7/- | melee | 14 |
| 110 | Wild Strikes | 2/15 | melee + stance effect (6362 on the finishers); without FP 865 = attack x0.55 | 9 |
| 111 | Spinning Strikes | 2/15 | melee (+ a zero-damage bullet every 4 frames) | 3 |
| 112 | Double Slash | 6/3 | melee | 12 |
| 113 | Prelate's Charge | 7/7 | melee+bullet (fire trail) | 3 (colossal) |
| 116 | Giant Hunt | 16/- | melee | 9 |
| 117 | Torch Attack | 0/- | melee (fire) | 35 |
| 118 | Loretta's Slash | 14/- | melee | 5 |
| 119 | Poison Moth Flight | 7/- | melee | 8 |
| 120 | Spinning Weapon | 12/- | melee | 18 |
| 122 | Storm Assault | 22/- | melee+bullet | 6 |
| 123 | Stormcaller | 9/11 | melee+bullet | 17 |
| 124 | Sword Dance | 6/6 | melee | 16 |
| 200 | Glintblade Phalanx | 10/4 | bullet (R2: melee) | 16 |
| 201 | Sacred Blade | 19/- | buff (+90 holy on the weapon, 40 s) + melee + bullet | 23 |
| 202 | Ice Spear | 15/- | melee+bullet (frost on hit) | 4 (spears) |
| 203 | Glintstone Pebble | 8/4 | melee+bullet | 16 |
| 204 | Bloody Slash | 6/- | melee (costs 5% HP + 50; bleed 60 on hit) | 12 |
| 205 | Lifesteal Fist | 14/- | melee + heal (grab) | 4 |
| 207 | Eruption | 14/- | melee+bullet | 8 |
| 208 | Prayerful Strike | 20/- | melee | 6 |
| 209 | Gravitas | 13/- | melee+bullet | 23 |
| 210 | Storm Blade | 10/6 | melee+bullet | 11 |
| 212 | Earthshaker | 10/5 | melee | 5 |
| 213 | Golden Land | 16/5 | melee+bullet | 5 |
| 214 | Flaming Strike | 4/10 | L2 bullet; R2 buff (+90 fire, 40 s) + melee | 25 |
| 216 | Thunderbolt | 10/10 | bullet | 29 |
| 217 | Lightning Slash | 10/- | buff (+85 lightning, 40 s) + melee + bullet | 18 |
| 218 | Carian Grandeur | 26/- | melee | 12 |
| 219 | Carian Greatsword | 16/- | melee | 12 |
| 220 | Vacuum Slice | 14/- | melee+bullet | 15 |
| 221 | Black Flame Tornado | 30/- | melee+bullet | 5 |
| 222 | Sacred Ring of Light | 9/9 | melee+bullet | 3 |
| 223 | Firebreather | 8/- | bullet | 35 |
| 224 | Blood Blade | 3/3 | melee+bullet (costs 4% HP; bleed on hit) | 9 |
| 225 | Phantom Slash | 8/8 | bullet+melee | 4 |
| 226 | Spectral Lance | 9/- | bullet | 3 |
| 227 | Chilling Mist | 14/- | buff (frost 60 on the weapon, 40 s) + melee + status-only cloud (frost) | 23 |
| 228 | Poisonous Mist | 14/- | buff (poison 60 on the weapon, 40 s) + melee + status-only cloud (poison) | 23 |
| 300 | Shield Bash | 10/- | melee | shields |
| 301 | Barricade Shield | 12/- | buff | shields |
| 302 | Parry | 0/- | parry | 8 (small shields and some weapons) |
| 303 | Buckler Parry | 0/- | parry | 35 |
| 305 | Carian Retaliation | 8/- | parry + short SpEffect 1515 (`behaviorId` 2140, 0.1 s; what it fires is not traced) | shields |
| 306 | Storm Wall | 0/- | parry (+ wind field) | shields |
| 307 | Golden Parry | 4/- | parry | shields |
| 308 | Shield Crash | 12/- | melee | shields |
| 309 | Thops's Barrier | 0/- | parry (+ barrier) | shields |
| 400-406 | bow skills | 0/2..20 | not classified (fire ammunition, event 64) | bows |
| 501 | Hoarfrost Stomp | 10/- | bullet (frostbite on hit) | 30 |
| 502 | Storm Stomp | 6/- | bullet | 31 |
| 503 | Kick | 0/- | melee | 31 |
| 504 | Lightning Ram | 5/5 | melee | 30 |
| 505 | Flame of the Redmanes | 14/- | bullet | 30 |
| 506 | Ground Slam | 14/- | melee (leap, then the landing hits) | 31 |
| 507 | Golden Slam | 22/- | melee | 30 |
| 508 | Waves of Darkness | 16/5 | melee+bullet | 5 (colossal, greatswords) |
| 509 | Hoarah Loux's Earthshaker | 16/16 | melee | 31 |
| 600 | Determination | 10/- | buff | 28 |
| 601 | Royal Knight's Resolve | 15/- | buff | 28 |
| 602 | Assassin's Gambit | 5/- | buff | 7 |
| 603 | Golden Vow | 40/- | buff (area, allies too) | 30 |
| 604 | Sacred Order | 18/- | buff | 28 |
| 605 | Shared Order | 20/- | buff (allies) | 28 |
| 606 | Seppuku | 4/- | buff | 13 |
| 607 | Cragblade | 16/- | buff+melee | 27 |
| 650 | Barbaric Roar | 16/- | buff | 23 |
| 651 | War Cry | 16/- | buff | 23 |
| 652 | Beast's Roar | 10/- | bullet | 31 |
| 653 | Troll's Roar | 22/- | melee | 8 |
| 654 | Braggart's Roar | 16/- | buff | 23 |
| 700 | Endure | 9/- | buff | 31 |
| 701 | Vow of the Indomitable | 20/- | i-frames | shields |
| 702 | Holy Ground | 30/- | buff (area) | shields |
| 800 | Quickstep | 3/- | i-frames | 31 |
| 801 | Bloodhound's Step | 5/- | i-frames | 31 |
| 802 | Raptor of the Mists | 6/- | i-frames + stance effect | 31 |
| 850 | White Shadow's Lure | 15/- | bullet (no damage, a lure) | 30 |
| 2000-5050 | DLC ashes | see `list` | Blinkbolt i-frames+melee, Flame Skewer / Flame Spear / Carian Sovereignty / Ghostflame Call melee+bullet, Divine Beast Frost Stomp bullet, Savage Lion's Claw / Spinning Gravity Thrust melee, Shriek of Sorrow buff | varies |

Unique skills (the weapon's own `swordArtsParamId`, not an ash) are in `list` with the same
classes: 157 rows, from Moonlight Greatsword (buff) to Ruins Greatsword's Wave of Destruction
(melee+bullet).

## 8. Per-hit numbers at RL 150

`python3 scripts/er-mechanics-ashes.py pvp --rl 150 --skills ...`: each skill on its most common
weapon and affinity among the 161 STR PvP builds of RL 140-160 (`pvptag` filter, section 9), at
max upgrade, with the median stats of the builds that pair them; skills nobody pairs go on the
first reference weapon that accepts them (Greatsword, Claymore, ...) with a Heavy affinity if the
ash allows it. Defender: the median build of the window. Damage = after defense and absorption,
with `FinalDamageRateParam` and `vsPlayerDmgCorrectRate` applied; one target hit once per row.
Durability and counter-hit factors are left out. Poise in menu units.

| skill (weapon, grip) | FP | hits: frame, MV or flat, damage | poise | notes |
|---|---|---|---|---|
| Lion's Claw (Greatsword Heavy, 1H) | 20 | f50 MV 240: 841 | 360 | one hit; no-FP: MV 138 |
| Giant Hunt (Greatsword Heavy) | 16 | f31 MV 220: 771 | 360 | hyperarmor f19-62 (+180) |
| Stamp (Upward Cut) (Giant-Crusher Heavy) | 5 | f27 MV 215: 839 | 450 | half damage taken during the stance |
| Stamp (Sweep) (Greatsword Heavy) | 5 | f19 MV 92: 289, f27 MV 112: 369 | 90, 150 | |
| Wild Strikes (Great Stars Quality) | 2 per swing | f5 MV 107: 246, f24 MV 114: 266 | 63 each | loop 040051; R2 finishers 040060 (MV 45 + 154) and 040070 (MV 44 + 203) are further animations |
| Sword Dance (Shamshir Heavy, 2H) | 6 | f23 MV 120: 237, f35 MV 120: 237 | 60 each | hyperarmor row 120 |
| Spinning Slash (Rotten Crystal Sword) | 6 | f18 MV 110: 210 | 145 | PvP x0.7 |
| Impaling Thrust (Claymore Heavy) | 9 | f28 MV 187: 535 | 330 | |
| Piercing Fang (Knight's Greatsword Heavy) | 16 | f39 MV 212: 597 | 330 | |
| Spinning Weapon (Carian Regal Scepter) | 12 | 7 hits MV 70/58x5/70: 124 total | 12 each | the corpus pairing is a staff (AR 199) |
| Kick (Golem Fist Heavy) | 0 | f23 flat 30 phys + MV 30 holy: 17 | 60 | hyperarmor row 165 f14-32 |
| Ground Slam (Greatsword Heavy) | 14 | landing: flat 264 phys: 763, flat 175 phys: 475 | 360, 120 | the landing animation 040004 |
| Golden Slam (Greatsword Heavy) | 22 | flat 308 phys: 656, flat 220 holy: 494 | 360, 120 | |
| Hoarah Loux's Earthshaker (Brick Hammer Heavy) | 16 | f113 flat 220 phys: 610 | 200 | first counted hit of 040000 |
| Storm Stomp (Greatsword Heavy) | 6 | f22 bullet flat 50 phys: 88 | 100 | scales STR/DEX 15 (AECP 51030) |
| Hoarfrost Stomp (Greatsword Heavy) | 10 | f21 bullet flat 40 mag: 46, lingering flat 100 mag: 184 | 0 | frostbite build-up 70/110 on hit (1800/1801) |
| Flame of the Redmanes (Greatsword Heavy) | 14 | f30 bullet flat 180 fire: 534 | 100 | |
| Beast's Roar (Lance Magic) | 10 | f21 bullets flat 110 and 80 phys: 211, 142 | 60 each | |
| Ice Spear (Lance Heavy) | 15 | f15 MV 80: 176, f37 bullet flat 230 mag: 493 | 110, 250 | frostbite 160 on hit (1530) |
| Prelate's Charge (Duelist Greataxe Flame Art, 2H) | 7 | f36 MV 100 + flat 35 fire: 393, fire trail bullets 92 + 78 | 60, 180, 10 | hyperarmor row 201 f15-55 |
| Waves of Darkness (Greatsword Cold, 2H) | 16 | f37 MV 100 + flat 60 mag: 422, f53 MV 60: 137, f55 wave flat 120 mag: 234 | 60, 36, 80 | hyperarmor row 201 f15-60 |
| Flaming Strike (Lance Heavy) | 4 | f19 bullet flat 138 fire: 320 | 100 | PvP x0.65 |
| Storm Blade (Cleanrot Knight's Sword Heavy) | 10 | f27 MV 65: 113, f29 bullet flat 150 phys: 351 | 26, 75 | |
| Chilling Mist (Greatsword Heavy, 2H) | 14 | f51 MV 170: 619 | 120 | then 40 s of frost on the weapon (826) and a frost mist |
| Cragblade (Grave Scythe Heavy) | 16 | f19 MV 100: 239, f63 MV 80: 180 | 75, 50 | then the 60 s buff |
| Sacred Blade (Greatsword Heavy) | 19 | f51 MV 65: 186, f52 bullet flat 180 holy: 389 | 60, 130 | then +90 holy on the weapon for 40 s |
| Lightning Slash (Greatsword Heavy) | 10 | f23 flat 30 lightning: 24, f50 MV 170: 595, f59 bullets 120 + 60 lightning: 241 + 101 | 20, 180, 100 | then +85 lightning for 40 s; the 60 row shares a hit list (may not add) |
| Thunderbolt (Greatsword Heavy) | 10 | f37 bolt flat 120 lightning: 241, chain flat 60: 101 | 50, 0 | shared hit list: likely one hit per target (INFERRED) |
| Golden Land (Greatsword Heavy) | 16 | f37 MV 100: 321, f40 bullets 176 + 82 holy: 378 + 145, f55 MV 60: 168 | 60, 300, 10, 36 | |
| Phantom Slash (Twinblade) | 8 | f1 phantom bullet flat 176 phys: 381, f52 MV 132: 244 | 80, 75 | the phantom's hit is five bullet links down |
| Blood Tax (Claymore Heavy) | 14 | MV 88 / 44 / 44 / 110: 214 / 86 / 86 / 283 | 55, 22, 22, 55 | each FP hit heals you (1830/1831) |
| Flame Spear (Greatsword Heavy) | 19 | f50 MV 120: 396, f54 bullet flat 134 fire: 247 | 324, 60 | DLC |
| Spinning Gravity Thrust (Greatsword Heavy) | 26 | 4 x MV 33: 73 each, MV 115: 376 | 42x4, 60 | DLC |
| Endure, Royal Knight's Resolve, Determination, Braggart's/Barbaric Roar, War Cry, Golden Vow, Barricade Shield, Seppuku, Shriek of Sorrow, Holy Ground | see section 5 | no hit | | |
| Bloodhound's Step, Quickstep | 5, 3 | no hit (body hitbox 304 only) | | i-frames, section 6 |

## 9. Adoption in STR PvP builds, RL 140-160

`python3 scripts/er-mechanics-ashes.py adoption --rl 150 --filter pvptag`. MEASURED from
`~/.cache/er-build-planner/builds.jsonl` (`inventory.slots[].weaponArt`), with the filters and
deduplication of `er-builds-adoption-gap.py`: RL 140-160, not PvE, deduplicated on (user,
equipped items), active weapon set. `pvptag` = Strength tag plus a PvP tag (161 builds);
`str60` = STR 60 or more (173 builds). A slot whose `weaponArt` is empty or `No Skill` counts as
the weapon's own skill. `builds` counts a build once per skill.

| skill | class | pvptag builds (of 161) | str60 builds (of 173) | on |
|---|---|---|---|---|
| Bloodhound's Step | i-frames | 103 (64%) | 69 (40%) | Misericorde 67, Cleanrot Knight's Sword 12, Shamshir 8 |
| Endure | buff | 31 (19%) | 18 (10%) | Misericorde 28 |
| Parry | parry | 19 (12%) | 22 (13%) | Spiralhorn Shield 12 (its own skill) |
| Waves of Darkness | melee+bullet | 17 (11%) | 14 (8%) | Greatsword 16 |
| Carian Retaliation | parry | 13 (8%) | 16 (9%) | Iron Roundshield, Spiralhorn Shield |
| Flaming Strike | melee+bullet | 11 (7%) | 17 (10%) | Lance, Grave Scythe |
| Braggart's Roar | buff | 9 | 11 | Hand Axe |
| Royal Knight's Resolve | buff | 7 | 9 | Greatsword, Sword Lance |
| Storm Stomp | bullet | 7 | 5 | Zweihander, Greatsword |
| Sword Dance | melee | 7 | 13 | Shamshir |
| Beast's Roar | bullet | 6 | 4 | Hand Axe, Lance |
| Holy Ground | buff | 5 | 4 | Spiralhorn Shield |
| Chilling Mist | buff+melee+cloud | 4 | 7 | Greatsword |
| Quickstep | i-frames | 3 | 12 | Cinquedea, Misericorde |
| Storm Blade, Spinning Slash | | 4, 4 | 3, 5 | |
| Stamp (Upward Cut), Hoarah Loux's Earthshaker, Piercing Fang, Barbaric Roar | | 2 each | 2, 4, -, 4 | |
| Prelate's Charge, Kick | | 1 each | 0, 3 | |
| Lion's Claw | melee | 0 | 2 | |
| Wild Strikes, Giant Hunt, Hoarfrost Stomp, Ice Spear, Spinning Weapon | | 0 | 0-2 | |

The 118 `No Skill` builds are seals (Frenzied Flame Seal 93), the Icon Shield and staves: their
skill row 10 defers L2 to the right weapon, so they add nothing. Among the unique skills, Red
Bear's Claw (6), Sword of Light (4) and Devonia's Hammer (3) appear. The corpus is a small
sample: popular-in-general ashes such as Lion's Claw and Giant Hunt have no pvptag builds here.

The `L2` column the tool also prints applies the hand rule of section 1 to slot 0 (right) and
slot 3 (left). It is INFERRED: 43 shields sit at slot 0 in the Strength-tagged window, so the
planner's slot numbering does not map to hands as cleanly as `er-builds-adoption-gap.py` assumes.

## 10. Interface for the PvP ranking

```python
ASH = importlib (scripts/er-mechanics-ashes.py)
t = ASH.AshTables()
wid = t.find_weapon('Greatsword')                    # base id; affinity lives in ctx
sid = t.weapon_skill(wid, gem_id) or t.find_arts("Lion's Claw")
ctx = ASH.WeaponContext('Greatsword', 'Heavy', None, stats, two_handed)   # None = max level
hits = ASH.skill_hits(t, wid, sid, ctx, ctx.level)   # per hit: attack per element, MV, flat,
                                                     # frame, poise, final_rate_id, from_weapon
total = ASH.pvp_damage(t, wid, hits, defender, er_mechanics_defense)
buffs = ASH.skill_buffs(t, ASH.skill_profile(t, sid, wid))            # SpEffect summaries
on_target = ASH.skill_buffs(t, profile, on_target=True)               # frost, bleed on hit
fp = t.fp_cost(sid, 'L2', arts_rate); full = t.has_enough_fp(current_fp, fp)
l2 = t.active_skill(right_sid, left_sid, two_handed)
```

The scored form of all this is `skill_term` (section 13):

```python
rows = ASH.corpus_slots(mirror, rl - window, rl + window, 'pvp')
pairings = ASH.skill_pairings(t, rows)                       # once per run
choice = ASH.skill_choice(t, pairings, base_id, ASH.AFFINITIES.index(aff), level)
term = ASH.skill_term(t, base_id, choice, ctx, level, best_slot_score, fp_bar, engagements,
                      score_fn=slot_score, damage_fn=damage_fn, poises=poises)
term['score'], term['options'], term['buff_alternatives']
```

`defender` has the shape `er-builds-optimize.bracket_defender` returns. `skill_profile` has
every event of every animation (hyperarmor windows with PvP rates, i-frames, FP frame) for
startup and recovery scoring.

## 11. Selftest

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py --selftest`: 96 passed, 0
failed (2026-10-01; the table lists the original checks).

| check | reference |
|---|---|
| Lion's Claw mounts on a Claymore, not a Dagger; Bloodhound's Step on a Dagger | COMMUNITY |
| Claymore's own skill is Lion's Claw; a mounted gem wins | COMMUNITY; EXE 0x140673f70 |
| Greatsword wepType maps to `SwordGigantic` | EXE 0x140d29e00 |
| Lion's Claw is a600, one melee hit, MV 240, attack = AR x 2.4 | TAE; REGULATION 300300820; EXE 0x1406832a0 |
| Storm Stomp: bullet only, attack = 50 x baseAtkRate x scaling (AECP 51030), PvP row 10000 | COMMUNITY; EXE; REGULATION |
| FP: cost 20 plays in full with 10 FP, not with 9; 0.75 rate gives 15 | EXE 0x14047fc60, 0x14068b220 |
| L2 hand rule with a seal (defers) and a Parry shield (wins) | EXE 0x14047f770 |
| Endure: buff only, 1650, 3.0 s, x0.6 player damage | COMMUNITY; REGULATION |
| Hoarfrost: bullet, frost 1800/1801 on the target not the caster | REGULATION |
| Golden Vow: buff delivered by an area bullet, 1730 | COMMUNITY; REGULATION |
| Wild Strikes charges FP on each swing | TAE a610 040051 |
| Parry, Buckler Parry, Golden Parry classify as parry; Bloodhound's Step as i-frames only | COMMUNITY |
| Royal Knight's Resolve: buff | COMMUNITY |
| Sacred Blade buff+bullet+melee; Phantom Slash's deep bullet damages; Bloody Slash melee only (HP cost, bleed on the target); Poisonous Mist buff + status-only cloud + melee; Seppuku buff (self-bleed not a buff) | REGULATION + TAE, the cases the three audit subagents found misclassified |
| FP casts per bar: 88 FP pays for 6 casts of 16 (the last on half cost), 85 for 5; a free skill is unlimited | EXE 0x14047fc60 |
| Lion's Claw on a Claymore: roll free at real frame 62.8, R1 at 69.7 | TAE a600 040000, 608 speed |
| Skill choice: a unique weapon keeps its own skill; Lion's Claw corpus mass on a Dagger goes to the dagger's own skill; 20 own-weapon pairings get 20/25 | `can_mount`; section 13 shrinkage |
| `pvp_damage` takes a `damage_fn`; the skill term's score, option value and floor at the best slot; War Cry hands its roots 1810/1812 over as one alternative | section 13 |
| Medium roll i-frames f0-13 in four directions, R1 at 20, 3.65 m forward; Bloodhound's Step 040080-3 f0-10, R1 at 16-17, 4.49-5.24 m; Quickstep f0-9 | TAE + hkx, section 14a |
| Endure 1650 from f4 for 3 s, x0.6, reaction override; War Cry not defensive | REGULATION, section 14b |
| Out of reach, the hit is avoided for tau <= i-frames - active (roll 11, step 8 of 30); a 0.1 m attack never reaches a back step | section 14a |
| Endure, Bloodhound's Step and Parry utility HP and multiplier on a synthetic engagement; `skill_term` takes Endure as an option and floors the step at 0 | section 14 |
| `mountable_skills`: a unique weapon has only its own skill; Claymore Heavy mounts Lion's Claw and Stormcaller, a Dagger neither; an affinity Glintblade Phalanx does not allow keeps it off | EXE 0x140d549d0 via `can_mount` |
| Bullet flight: 10 m/s level for 0.5 s is 31 frames of travel; with gravity it lands; Lightning Slash's 200 m/s bolt drops (under 4 m); Divine Beast Frost Stomp hops to 9-14 m, not 160 | EXE flight rules (bd bullet-launch-geometry-for-skill-reach-2026-09-29); placement INFERRED |
| Ground Slam: the leap hands over at its falling hitbox, lead 49 frames | TAE a715; INFERRED landing |
| Lion's Claw on a Claymore: world reach 6.13 m from its own TimeAct; Hoarfrost Stomp a projectile over 5 m | `er-mechanics-reach.skill_reach`; `bullet_reach` |
| Wild Strikes: the 040050 wind-up is a 19-frame lead-in | TAE a610 |
| Best available is never below the corpus mix, and the corpus term is kept apart | section 15 |
| Knockback: held for ContTime, linear to 0 over DecTime, totals `knockbackDist`; every protector has knockBack 0 and row 1; KnockBackParam 1 large 0.09 / 1.2 s | EXE FUN_1404504e0 / FUN_1404508c0; REGULATION |
| Stormcaller at 2.5 m: poise 72 lands 4 of 6 (swing 3 escaped at DamageCount 2); poise that holds lands 2 (swing 1 and its bullet on one update) | section 15a |
| Small blow clip carries its target over 4 m back, large none; Thunderbolt's 200 m/s bolt is found on a defender at 2.5 m; Lightning Slash's bolt stops on the ground | hkx root motion (MEASURED); bullet flight |

## 12. Not established

- Section 13, the skill term:
  - Reach and coverage of a skill are measured since section 15 (melee from the skill TimeAct's
    pose, projectiles from an INFERRED Bullet model). The blockers' guard pressure and the
    exchange factor are still 1.0 for a skill.
  - The lead-in frames of a skill whose hit sits in a follow-up animation are INFERRED.
  - `SKILL_WEIGHT` and `SKILL_PAIRING_ALPHA` are modelling choices.
  - No corpus field records how often a player actually presses the skill.
- Section 14, utility ashes:
  - The dodge timing spread (`DODGE_TIMING_FRAMES`), the equal use of the four directions, and a
    second dodge catching R1 #2 at the single-hit share are modelling choices. The sign of
    Bloodhound's Step against the roll flips between spreads 30 and 45.
  - What the step does that the model has no state for: neutral spacing and gap closing,
    tracking and lock-on (marker 430, stateInfo 324, is not decoded), and the visual.
  - Which ten-block (0400x/0401x/0405x/0406x) the behavior script plays; 040580 has a second
    window f0-16 with a byte at Args+16 whose meaning is not established.
  - That L2 out of an attack reads "Cancel - LH Attack" (16), and which stamina branch is the
    short-FP one.
  - Endure: the priority between category-1001 rows, and whether a cast covers more than one
    engagement.
  - The opponent's attack is the exchange pool's R1 string only; R2s, running attacks and skills
    are not in it.

- Which animation the behavior script plays (040000 vs 040005, direction and grip variants such
  as Endure's 040200/044200/045900); the EXE only supplies the TimeAct number. The tool takes
  the first FP-charging animation as the opening.
- Lion's Claw hyperarmor: a600 has no event 795.
- Durability, counter-hit and the `[+0x6c]`/`[+0x70..0x7c]` factors of the builder
  (attacks.md section 8), and the exact form of the scaling for a bullet whose element the weapon
  has no base in.
- That `spawn+0x28/+0x2c` carry the firing hand's weapon through `CreateBulletSpawnData`, and
  that the damage manager passes the bullet's own AttackInfo (INFERRED).
- Poise damage of bullets (the tool uses the weapon formula).
- How many times a lingering bullet (Hoarfrost's mist, Holy Ground) hits one target; the tool
  counts each AtkParam row once, and a child bullet with its parent's row as the same wave.
- `wepParamChange` 1/2/3 meaning right weapon, left weapon, target (INFERRED from names and use).
- What `dmgLv_*` and `defFlickPower` do to incoming reactions (Endure), and what
  `changeStrengthPoint` 5 on the roars does.
- Bow skills (event 64, ammunition) are not classified; Igon's Drake Hunt's arrow judges 890/895
  do not resolve against the bow.
- Shared hit lists: Thunderbolt, Flaming Strike, Flame of the Redmanes and Hoarfrost use
  `isUseSharedHitList` with a 0.5-2 s hit record, so a target is probably hit once by the whole
  group; the tool still counts each distinct AtkParam row once. Glintblade Phalanx and Sacred
  Ring of Light do not share, so both their bullets may hit.
- Repeating bullets: Black Flame Tornado's 2430 has `shootInterval` 0.4 and a 0.4 s hit record
  over f40-95, so it probably hits 4-5 times; the tool shows one hit.
- Whether a bullet event that resolves to a refType 0 row creates anything (Holy Ground's no-FP
  040035 fires judges 3220/3222 that land on Spinning Slash rows).
- Carian Grandeur's no-FP copy 040005 still fires its full-charge judge 3602; Prayerful Strike's
  judge 2 does not resolve on a Claymore; Lifesteal Fist's grab damage is in no event read here
  (a throw param, INFERRED); Wild Strikes 040068 does not mirror 040063 the way the other
  no-FP copies do.
- Unsheathe and Square Off carry a chain 20382201 -> 20382204 (+45% damage vs players and NPCs
  for 10 s) that Smithbox names "[Talisman] Rellana's Cameo"; what gates it (markers 20382205 /
  20382206, `stateInfo` 504 / 502) is not established, so the tool lists it without a condition.
- Determination and Royal Knight's Resolve end on the next registered hit (VERIFIED, buffs.md
  section 8): player attack rows carry SpEffect 1665/1667, which the hit sends back to the
  attacker and which, when the attacker holds `stateInfo` 384/385, overwrites the weapon-buff
  slot. A contact with an i-framed target registers no hit and spends nothing.
- `effectTargetSelf` is not a reliable "cannot land on the caster" flag for events 66/67 (marker
  430 has it 0 and is applied to the player); the tool uses it only for bullet on-hit rows.
- Golden Vow's 1734 (`stateInfo` 152, the same value as Seppuku's buff) is shown as a marker.
- The planner slot numbering for the L2 column (section 9).
- 1.17.1: only the FP half-cost constant was re-read in the 1.17.1 image; the other functions are
  1.16.2 addresses.

## 13. The skill term of the PvP ranking

`skill_term` scores the weapon's skill the way `er-builds-pvp.py` scores an attack slot, and
weights it by how often the corpus mounts it and by how many times one FP bar can pay for it.
Before this, the ranking only printed the skill.

**Which skill (SITE, shrinkage INFERRED).** `skill_pairings` counts what every equipped slot of
the PvP corpus fires:
- The corpus is `corpus_slots` filter `pvp`: every build with `isPvE` false or a PvP tag. At RL
  140-160 that is 962 deduplicated builds, 1588 ash-capable slots and 347 weapons.
- A slot with no `weaponArt`, `No Skill` or its own skill counts as `own`; a named ash counts as
  that ash.
- Across ash-capable weapons: Bloodhound's Step 21.7%, own skill 17.3%, Endure 7.4%, Carian
  Retaliation 4.7%, Flaming Strike 2.9%, Sword Dance 2.8%, Waves of Darkness 2.8%, Parry 2.4%,
  Braggart's Roar 2.2%, Royal Knight's Resolve 1.9%.

`skill_choice` turns the counts into a probability per skill for one weapon, at one affinity and
level:
- It keeps only skills the weapon can carry there (`can_mount`, VERIFIED).
- Only 64 weapons have 10 or more slots, so a weapon's counts are shrunk toward its `wepType`
  class, and the class toward the corpus, each with `SKILL_PAIRING_ALPHA` = 5 pseudo-builds
  (INFERRED).
- The top `SKILL_CHOICE_TOP` = 6 are kept.
- A weapon that takes no ash gets its own skill with p 1.

Unique weapons use their own skill; infusable ones get what the corpus mounts. For Greatsword
Heavy that is Waves of Darkness 0.33, Royal Knight's Resolve 0.09, Poisonous Mist 0.09,
Bloodhound's Step 0.08, and so on. Bloodhound's Step does mount on a colossal sword in 1.17.1
(`canMountWep_SwordGigantic` 1), and the corpus has 5 Greatswords carrying it.

**Damage and commitment (VERIFIED formula, INFERRED animation choice).**
- **Damage.** The hits are `skill_hits` (section 4). `pvp_damage` sums them through the ranking's
  own corpus mean when it passes `damage_fn` (`corpus_hit`), so a skill hit and an R1 meet the
  same defenders.
- **Commitment.** It is read like a slot's (attacks.md section 4): the first real frame a roll
  or an R1 opens in the skill TimeAct's JumpTable windows, TAE 608 speed applied, at or after
  the last hit (`anim_recovery`, `skill_commit`).
- **Lead-in.** When the last hit is in a follow-up animation, the opening's first R1/R2 opening
  is added in front (Stamp: stance 040000, then 040010), or the opening's clip length when it has
  none. That is INFERRED.
- **Stagger.** The share of the corpus's poise below the skill's top hit poise x
  `FinalDamageRateParam.saRate`.
- **Score.** The dict `{dmg, roll, next, stagger}` goes to the ranking's own `slot_score`.

**FP economy (MEASURED + VERIFIED).**
- The FP bar is the median planner `computed.maxFP` of the window: 85 for the deduplicated `pvp`
  rows, and 88 over all 1118 PvP builds of RL 140-160 (`FP_BAR_DEFAULT`).
- `fp_uses` is how many casts one bar pays for: the full casts, plus one more when what is left
  is at least half the cost (the `has_enough_fp` rule). For example, 88 FP pays for 6 casts at
  16 FP.
- `share` = casts / the fight's landed hits (5, `er-mechanics-status.py`), capped at 1.
- The planner's cerulean flask count is 4 on all 1138 builds that carry one, which is the
  planner default. So mid-fight FP flasks cannot be measured and are left out.

**How it enters the score (INFERRED weight).** The skill is an option: a player uses it when it
beats the weapon's best slot, so a weak skill never lowers the weapon.

    gain_s  = max(0, score_s - best_slot_score)
    value   = sum over skills of p_s x share_s x gain_s
    score   = best_slot_score + SKILL_WEIGHT x value      (SKILL_WEIGHT 0.5)

Bloodhound's Step, Endure and the parries have no hit. Section 14 values them as utility options
when the ranking passes an engagement. Two kinds of skill are not scored as openers even when they have hits (`unscored`):
- **Parries**, because their hits need a caught attack first.
- **Skills with no FP-charging animation**, because their opening is not identified: `main_anim`
  falls back to the first animation.

Golden Retaliation is both (SwordArtsParam 1196, in `er-mechanics-crits.PARRY_SWORD_ARTS`, with
no event 330). Before this guard it scored 2450 and took the Erdtree Greatshield from rank 715 to
rank 4. At RL 150 the guard also drops Sea of Magma and Tongues of Fire. It is INFERRED that their
FP is charged somewhere this tool does not read. A buff skill hands its SpEffect roots to `er-mechanics-buffs.py` as a `buff_alternatives`
entry (p, FP casts), and that module turns them into the buff term (buffs.md section 10).

**In `er-builds-pvp.py`.** `Mechanics.skill_term` compares the skill against the weapon's moveset
score (`er-mechanics-moveset.moveset_score`), not against one slot. Each skill hit meets the same
corpus and the same buff factors as a slot. `skill_slot_extra` adds these to the skill's slot:
- frame advantage from its last hit (`er-mechanics-frame-advantage.advantage` on the skill
  TimeAct's cancel frames);
- parry exposure (`skill_parryable`: JumpTable 5 open in a hit animation, the crits module's test);
- the weapon's crit HP and parry HP;
- the 2H own-guard term.

The blockers' guard pressure stays 1.0. Reach and coverage were 1.0 here too until section 15,
which measures them from the skill TimeAct and the Bullet rows.

**Whole RL 150 ranking (MEASURED 2026-09-29, 820 weapon rows).** 153 rows gain from their skill.
The largest gains are:
- Marika's Hammer, Gold Breaker: +393 (rank 451 -> 34 for 2H, 426 -> 32 for 1H);
- Shadow Sunflower Blossom 1H, Headbutt: +269;
- Stormhawk Axe 1H, Thunderstorm: +239;
- Ripple Blade 1H, Wild Strikes at p 1.0: +221 (46 -> 6).

An infusable weapon's gain is small because most of its probability mass is on utility ashes
(Wild Strikes at p 0.08 adds 6-14 to the axes). The buff term scales almost every row by the same
0.946, p10-p90 0.938-0.949, so it moves ranks little.

**What it did to the three weapons checked first** (RL 150 sweep builds, `er-builds-pvp.py`'s
`slot_score` and corpus mean). The skill scores 130-690 against best-slot scores of 916-1706, so
`value` is 0 for Giant-Crusher, Greatsword and Claymore. Part of that gap is real: Stamp does
1020 over 76 frames, a Giant-Crusher R1 620 over 32. Part of it is the model: a skill gets
neutral reach, frame-advantage and parry factors (1.0), while a slot gets its measured ones,
which for these R1s multiply the rate by about 2 (section 12).

## 14. Utility ashes: what a skill with no scored hit is worth

Section 13 scored a skill only by its hits. The corpus mostly mounts skills that do not hit:
Bloodhound's Step 21.7% of ash-capable slots, Endure 7.4%, Carian Retaliation 4.7%, Parry 2.4%.
So an ash-capable weapon gained almost nothing from its skill, and the adoption check (below) kept
finding ash-capable weapons adopted about 1.22x more than uniques at the same score.

Section 14 values those skills mechanically. It does not fit anything to adoption. Each one is
turned into HP per engagement, the currency the ranking already uses (`slot_score` adds crit HP
and subtracts parry HP in the same place). The engagement is the weapon's best opener as the
ranking scored it:

    N = rate x commit / 30      HP per engagement of the best opener
    m = (N + hp) / N x C / (C + extra frames)
    option score = moveset score x m

The option then enters section 13 unchanged: `gain = max(0, option - moveset)`, weighted by the
corpus mount probability `p` (`skill_choice`, which already applies `can_mount`, VERIFIED) and the
FP share, and added at `SKILL_WEIGHT`. A unique weapon's own skill is valued the same way when it
is a dodge, a defensive buff or a parry. `utility_value` and `utility_multiplier` are in
`scripts/er-mechanics-ashes.py`, and `skill_engagement` is in `scripts/er-builds-pvp.py`.

### 14a. Dodges against a medium roll (TAE + hkx, MEASURED)

`evasion_motion` reads each animation: invincibility is the unconditional JumpTable 8 window from
frame 0; `ready` is the first R1 opening (attacks.md section 4); and the path is the hkx root
motion per real frame, with -Z forward. The medium roll's second window f13-16 carries stateInfo
290 at Args+14, which is the talisman state `er-mechanics-talismans` lists as "roll i-frames +3",
so it is left out (INFERRED gate). The table uses the opening's ten-block, without the no-FP
copies; which block the behavior script picks per grip or lock-on is not traced.

| animation | i-frames | R1 from | dodge from | end distance |
|---|---|---|---|---|
| medium roll a000 027110 / 111 / 112 / 113 (fwd, back, +X, -X) | f0-13 | 20 | 21 | 3.65 / 3.17 / 4.41 / 4.41 m |
| Bloodhound's Step a756 040080-040083 | f0-10 | 17 / 16 / 16 / 16 | 25 / 23 / 22 / 22 | 5.24 / 4.72 / 4.49 / 4.58 m |
| Quickstep a755 040080-040083 | f0-9 | 17 / 16 / 16 / 16 | 25 / 23 / 22 / 22 | 4.28 / 3.23 / 3.94 / 3.94 m |

Stamina does not separate them. c0000.hks charges a roll `STAMINA_REDUCE_ROLLING`, and it charges
the rolling arts (every one except swordArtsTypeNew 313, Dynastic Sickleplay)
`STAMINA_REDUCE_ARTS_QUICKSTEP`. common_define.hks sets both to -12 (VERIFIED, read with
`er-hks-disasm.py`). There is a branch that multiplies the step's charge by 1.5
(`STAMINA_CONSUMERATE_LOWSTATUS`); it is taken to be the short-FP branch because of the constant's
name (INFERRED).

The skill cannot evade earlier than a roll out of the player's own attack either. In the R1 and
R2 recoveries checked (Greatsword, Claymore, Uchigatana, Misericorde, Zweihander), "Cancel - LH
Attack" (16), which a left-hand action needs, opens on the same frame as "Cancel - Dodge" (26) or
later (TAE). That L2 reads id 16 is INFERRED.

**The engagement** (`Opponents.dodge_value`). The attack a dodge answers is each exchange-pool
build's own R1 #1, taken from the ranking's own rows: active frames, world reach and corpus-mean
damage (`opponents_from_results`). 95.3% of pool builds are matched; the rest take
`OPPONENT_FALLBACK`. When the HKS chains an R1 #2 (the slot's `combos`), the second hit is added
at `follow_up_gap` frames. Its reach is measured from where the attacker started: the attacker's
advance to the first hit (reach - weapon reach) plus R1 #2's reach. 95% of the pool chains one.
Pool-weighted means: active 3.2 frames, reach 3.69 m, 388 HP per hit, R1 #2 at 25 frames, 5.08 m
and 373 HP.
- The dodge starts tau frames before the first hit's first active frame, with tau uniform over
  `DODGE_TIMING_FRAMES` = 30 (INFERRED).
- The attacker stands `ENGAGE_DISTANCE_M` = 2.5 m straight ahead (the exchange module's strike
  distance).
- A hit is avoided when each of its active frames is inside the invincibility, or when the dodger
  (root motion applied, standing still after the clip) is farther away than the hit's reach.
- R1 #2 is avoided if it misses without another dodge, or if another dodge of the same kind catches
  it (that opponent's single-hit evade share).
- A punish, worth the opener's `dmg`, needs three things: the string was evaded with one dodge;
  the dodge ended within the opener's reach; and `ready + strike` came before the attacker's
  recovery after its last hit.
- The four directions are used equally (INFERRED).

**Value** = exchange `p_second` (the share of the pool that strikes before the opener, when a
dodge is the answer) x (HP with the step - HP with a medium roll). Nothing is added to the
commitment: the step replaces a roll.

Measured per direction (tau spread 30; per-opponent evade shares weighted by the pool):

| | fwd | back | +X | -X | mean HP avoided per string |
|---|---|---|---|---|---|
| roll: first hit avoided / R1 #2 misses without a second dodge | 0.36 / 0.05 | 1.00 / 0.76 | 0.84 / 0.30 | 0.85 / 0.29 | 519 |
| Bloodhound's Step | 0.27 / 0.06 | 1.00 / 0.97 | 0.83 / 0.42 | 0.84 / 0.46 | 514 |
| Quickstep | 0.23 / 0.05 | 0.98 / 0.79 | 0.77 / 0.23 | 0.77 / 0.23 | 458 |

The step's extra distance clears the chained R1 #2 far more often (back 0.97 against 0.76, lateral
about 0.44 against 0.30). Its three fewer i-frames lose about as much on the first hit, so
Bloodhound's Step comes out 5 HP per string below the roll. It lands above the roll once the
timing spread is 45 frames, and below it at 15 (the step gives 498, 514, 524 at spreads 15, 30,
45; the roll gives 599, 519, 520). A punish almost never happens in either case: the pool's R1s
recover 10.7 frames after their last hit, while a dodge is ready at 16-20 plus the opener's strike
frame. So in this model a dodge ash is worth a medium roll, give or take the timing assumption,
and the option floor gives it 0. Quickstep is 61 HP worse (9 i-frames, less distance).

### 14b. Endure (REGULATION + VERIFIED consumer)

The paid opening 040000 applies SpEffect 1650 through event 331 at f4 (TAE), for 3.0 s. The
SpEffect sets `defPlayerDmgCorrectRate_*` to 0.6 on every element, so it takes 40% less player
damage. It is `spCategory` 1001 with every `dmgLv_*` set to 1. With poise broken, the reaction
level comes from the category-1001 SpEffect (`er-mechanics-frame-advantage.py`, VERIFIED
0x140690250 path), and `REMAP[1]` = 0 is the additive flinch. So under Endure no hit interrupts.
That 1650 is the override that wins over any other category-1001 row is INFERRED: the priority
rule was not traced. The cast's R1 opens at frame 22. The FP cost is 9, so the median bar pays for
10 casts and the share is 1.

`defensive_buff` finds this generically: a buff root with a player damage-taken rate below 1, or a
category-1001 row whose `dmgLv_*` all remap to 0. War Cry is neither.

Per covered engagement:

    hp     = loss x dmg                                  losses become trades, the opener lands
           + (1 - win) x (1 - 0.6) x mean opponent hit   every hit taken is cut
    extra  = 4 frames                                    exposure before the buff lands (f4)

An engagement is covered, one per cast, when the 3 s outlast the cast (frame 22) by the opener's
strike frame (INFERRED). Only the frames before the buff lands are charged. The rest of the cast
is under Endure, and `er-mechanics-buffs` charges no cast time for any buff. On the RL 150 sweep
builds (2H unless noted) this gives: Greatsword (N 676, 32.7 frames) +151 HP, m 1.090; Claymore
(468, 25.0) +150, m 1.138; Uchigatana 1H (365, 16.0) +114, m 1.051; Misericorde 1H (R2 opener,
306, 25.0) +58, m 1.026.

### 14c. Parries (`er-mechanics-crits` terms)

Every weapon's slots already carry the corpus crit HP: `parry_tool x PARRY_LAND x exposure x
riposte`, which credits the attacker with parrying at the corpus rate of carrying a parry tool.
Carrying the parry on the weapon itself raises that rate to 1:

    hp = (1 - parry_tool) x PARRY_LAND x exposure x the weapon's riposte_eff_hp,   extra = 0

`PARRY_LAND` = 0.10 is the crits module's INFERRED net rate. No other weight is added here.

### 14d. The adoption check

`python3 scripts/er-mechanics-ashes.py check --pvp rank.json` (`ash_adoption_check`). It fits
log1p(primary adoption) ~ `wepType` class dummies + within-class score percentile + ash, where ash
is 1 unless the sweep's `kind` is `unique`. There is one row per weapon, from its best grip's
`moveset.score`, and a bootstrap over weapons (2000 resamples). This is a check, not a target:
nothing in section 14 reads it. It uses 411 weapons in 34 classes (the caller's 404 / 30 came from
a slightly different weapon set).

| ranking | ash coefficient | CI | percentile coefficient | ash / percentile |
|---|---|---|---|---|
| before section 14 (the earlier full run) | +0.200 (x1.22) | [+0.026, +0.341] | +0.242 | 0.83 |
| the section 14 run, utility options removed from its skill term | +0.200 (x1.22) | [+0.026, +0.341] | +0.242 | 0.83 |
| the section 14 run (MEASURED 2026-09-29, 10.9 min) | +0.205 (x1.23) | [+0.029, +0.347] | +0.215 | 0.95 |

The residual did not shrink. The mount mass that section 13 missed is mostly Bloodhound's Step,
and section 14a measures it as a medium roll. It gains on only 9 of the 285 rows where it is an
option (Quickstep on none of 20). The positive options are Endure (108 of 151 rows) and the
parries: Parry 141, Storm Wall 87, Golden Parry 86 and Carian Retaliation 40 rows. So what
section 14 does add goes to weapons that mount Endure or a parry. Those are not the ones the
corpus over-adopts, so the within-class score percentile loses a little of its fit (+0.242 ->
+0.215). What the step gives in PvP beyond an engagement against an R1 string (spacing, gap
closing, tracking) is not in the model; the residual is where that would show.

**Whole RL 150 ranking.** 245 of 820 rows move. Every move is upward, because the option floor
keeps it that way. Counted within the same run against the same run with utility options
removed, the largest moves are:
- Dragon Greatclaw 2H (own skill Endure, p 1): +126, rank 153 -> 55;
- Lucerne 1H / 2H (Endure p 0.33): +91 / +67, 580 -> 502 / 511 -> 424;
- Golem Fist 2H (Endure p 0.47): +86, 212 -> 118;
- the parry daggers and the Falchion (Parry p 0.28-0.36): +31 to +39 (Falchion 1H 163 -> 127,
  Great Knife 223 -> 183, Bloodstained Dagger 230 -> 189).

The top 20 is unchanged except that Iron Ball 2H moves 21 -> 20.

Great spears: Lance and Messmer Soldier's Spear gain nothing. Their mount mass is Bloodhound's
Step (0.31 on the Lance), Flaming Strike and the buffs, and the step is worth -27 HP against the
roll on the Lance's opener, which floors to 0. So they drift down as the Endure and parry weapons
rise: Lance 2H 472 -> 476, Messmer Soldier's Spear 2H 387 -> 389. The unique spears drift the
same way (Serpent-Hunter 2H 334 -> 341, Treespear 342 -> 349, Siluria's Tree 358 -> 361), so the
Lance against the uniques is unchanged.

## 15. Every mountable ash, and the skill's own reach

Section 13 scored only the ashes the corpus mounts on a weapon, weighted by mount rate, and
section 14 added the utility ashes. A damage ash a weapon can carry but the corpus rarely mounts
(Lion's Claw on an axe, Impaling Thrust on a Lance) was never scored. Section 15 closes two gaps:
it scores every ash the weapon can carry, and it gives every skill its own reach and coverage.

**The options (VERIFIED mount rule).** `mountable_skills(weapon, affinity, level)` is the
weapon's own skill, plus every ash-of-war item that `can_mount` accepts at the build's affinity
and level. That applies the weapon's `wepType` flag, the affinities the ash allows
(`configurableWepAttr*` / `defaultWepAttr`), gem rank and `restrictSpecialSwordArt`. A Claymore
at Heavy +25 has 67, a Lance at Heavy 59, and a unique weapon 1: its fixed skill. The build's
affinity is the sweep's. Moving the weapon to another affinity to reach an ash is not considered.

**Each option is scored exactly as section 13 scores a corpus option.** That means `skill_hits`
damage on the corpus defenders with the buffs, `skill_commit`, stagger, parry exposure, frame
advantage, crit and own-guard terms, the FP share, and for no-hit skills the section 14 utility
value. A unique weapon's own skill goes through the same path, so the comparison is fair.

**Option value (INFERRED).** The ash slot is a choice the player makes per matchup, so it is worth
its best option:

    value        = max over mountable skills of share x max(0, score - moveset score)
    weapon score = moveset score + SKILL_WEIGHT x value          (SKILL_WEIGHT 0.5, unchanged)

The section 13 term, sum over the corpus mix of p x share x gain, is kept as `value_corpus` /
`score_corpus` (the `skC` column of `er-builds-pvp.py`). It is shown only and is never scored.
Since the corpus mix is a subset of the mountable skills and its p sum to at most 1, `value` is
never below `value_corpus`.

**Reach and coverage (`skill_reach_factors`).** Each option now carries `reach` and `coverage`
into `slot_score`, the same columns a slot carries:
- **Melee.** `er-mechanics-reach.skill_reach` reads the skill TimeAct's animation that holds the
  first melee hit, with that hit's judge, exactly as a slot is read: pose, footprint and late
  turn. The numbers are MEASURED; see reach.md section 7b. Lion's Claw on a Claymore reaches
  6.13 m with coverage 0.917; Impaling Thrust on a Lance 6.42 m with 0.922.
- **Bullets.** `bullet_reach` flies the skill's bullet tree (`bullet_flight`), 60 steps a second.
  The flight rules are VERIFIED in the 1.16.2 code; bd
  `bullet-launch-geometry-for-skill-reach-2026-09-29` has the addresses.
  - **Per frame** (FUN_14039ac20 / FUN_14039f850): `v += (-g Y + a v_hat) dt`, then the speed is
    clamped to [`minVellocity`, `maxVellocity`], then `p += v dt`. The `gravity/accelInRange`
    pair applies while the distance flown is at most `dist`, the `OutRange` pair after it.
    Acceleration starts only after `accelTime`. The bullet also moves on the frame its life runs
    out.
  - **Shots.** Yaw = `shootAngle + i x shootAngleInterval`, elevation = `shootAngleXZ + i x
    shootAngleXInterval`. That positive elevation means up is INFERRED: Thunderbolt's 2081 at -90
    is the bolt coming down.
  - **Children.** A `HitBulletID` child is created when its parent hits or expires, at that point
    (FUN_14039bba0), and only if the child's `launchConditionType` allows that ending: 0 and 3
    always, 5 on a hit, 4 on expiry, 1 and 2 only on water. `EmittePosType` 2 starts 1.0 m higher
    and flies level (FUN_140390c20, constant .data 0x143b15b5c). An `intervalCreateBulletId`
    child starts at the parent's position when it spawns.
  - **Reach.** The farthest horizontal point any damaging bullet reaches, plus `max(hitRadius,
    hitRadiusMax)`.
  - **Placement (INFERRED).** The root starts 1.0 m ahead and 1.0 m up (`BULLET_ORIGIN_M`,
    `BULLET_ORIGIN_HEIGHT_M`). The launch dummy poly is not decoded.
  - **The world (INFERRED).** The ground is flat, a bullet hits it when falling at a height of
    `hitRadius` or less, and no character or target is hit on the way (no homing). The reach is
    how far the skill carries when it misses.
  - **Cap.** `BULLET_REACH_CAP_M` = 25 m. `slot_score`'s reach factor saturates at 5.6 m, so the
    cap changes only the shown value.
  - **Coverage.** The arc comes from the tree's `numShoot` fan plus the angle the radius subtends
    at the farthest point, or 360 when the radius encloses the caster. The late turn the TimeAct
    allows before the bullet event is added, through `coverage_factor`.

  The first version integrated speed over `life` and chained the children end to end, which read
  Divine Beast Frost Stomp at 160 m and Lightning Slash's 200 m/s bolt as 30 m of travel. With
  the verified rules (Claymore, 1 m origin included): Thunderbolt 25 m (cap; the forward bolt 2080
  flies 20 m), Vacuum Slice 19.5, Glintblade Phalanx 15.0, Carian Sovereignty 12.1, Divine Beast
  Frost Stomp 11.7, Hoarfrost Stomp 11.1, Flame of the Redmanes 10.5, Firebreather 6.7, Storm
  Stomp 3.6 (a 360-degree burst), Lightning Slash 3.2, Flaming Strike 3.2. A hand calculation
  of the same rules from the emit point (no 1 m origin) agrees to within that metre: Hoarfrost
  9.7, Divine Beast 10.5, Lightning Slash 2.1, Storm Stomp 2.6.
- **A skill with both.** Its reach is the larger of the two, and its coverage the larger that
  exists.
- **Neither measured.** The weapon's class median, the same stand-in an unposed slot gets
  (`class_fallback`, source `inferred`).
- **Cost.** Reach is measured only where it can change the answer. The neutral score times the
  largest reach x coverage factor `slot_score` allows (1.5 x 1.4) bounds the measured score, so an
  option whose bound cannot beat the best so far is left unmeasured (`reach_measured` false). The
  corpus options are always measured.

**Wild Strikes lead-in (TAE, rule INFERRED).** The first FP-charging animation of Wild Strikes is
the looping 040051. The 040050 wind-up in front of it has no hit and charges no FP, so section 13
scored the skill from 040051 and missed the wind-up. `skill_commit` now adds, as a lead-in, the
lowest animation of the opening's ten-block when that animation is not the opening, is not a
without-FP copy, and has no hit. For Wild Strikes that adds 19 frames: roll 29 -> 48 on a
Claymore. Before this fix, Wild Strikes was the Claymore's best option in `term` (789 against
Stormcaller's 502); after it, it scores 440.

**Leap hand-over (TAE, rule INFERRED).** When a lead-in animation (the opening in front of a
follow-up, or the wind-up above) has hitbox windows, the hand-over is taken at the start of the
last one to open. That is the falling hitbox, and a leap can only land once it is live, so its
start is the earliest landing on flat ground. Otherwise the hand-over is the first R1/R2 cancel,
and failing that the clip length, as in section 13. Two cases:
- **Ground Slam.** The body hitbox 500 opens at f49 of a715 040000, after the take-off box 504 at
  f23. Section 13 had used the clip length here, so the roll moves from 162 to 77.
- **Gold Breaker.** The no-damage hitbox of a829 040000 opens at f70. Section 13 counted no leap
  at all, so the roll moves from 29 to 76, and the score from 2026 to 392 on the section 13 run's
  Marika's Hammer.

The true landing frame depends on terrain and is decided by the behavior script, which is not
traced.

**Per-ash numbers (MEASURED 2026-09-29, `term`, STR 60 DEX 20 2H, median defender).** The
columns are damage per cast, the commitment to the first roll, reach, coverage, and the `term`
score (damage per second of commitment x reach x coverage). They are not the ranking's
`slot_score`, which also adds frame advantage, stagger, crit and guard terms.

| weapon | ash | dmg | roll | reach m | cov | term score |
|---|---|---|---|---|---|---|
| Claymore Heavy+25 | Ground Slam | 1337 | 77 | 2.61 | 0.70 | 521 |
| | Stormcaller | 1226 | 73 | 3.44 | 1.40 | 502 |
| | Impaling Thrust | 563 | 38 | 5.73 | 0.92 | 448 |
| | Lightning Slash | 863 | 65 | 3.90 | 0.82 | 417 |
| | Troll's Roar | 1058 | 77 | 5.12 | 1.12 | 412 |
| | Lion's Claw | 738 | 63 | 6.13 | 0.92 | 353 |
| | Giant Hunt | 677 | 60 | 5.10 | 0.93 | 338 |
| | Carian Grandeur | 903 | 111 | 7.01 | 0.95 | 243 |
| | Hoarfrost Stomp | 219 | 46 | 11.13 b | 1.09 | 152 |
| Lance Heavy+25 | Ground Slam | 1337 | 77 | 2.61 | 0.70 | 521 |
| | Stormcaller | 1101 | 73 | 4.12 | 1.40 | 451 |
| | Impaling Thrust | 526 | 38 | 6.42 | 0.92 | 419 |
| | Ice Spear | 690 | 54 | 14.2 b | 0.86 | 384 |
| | Repeating Thrust | 794 | 64 | 7.31 | 0.91 | 373 |
| | Golden Land | 900 | 80 | 25 b | 0.99 | 337 |

(`b` marks bullet-model reach.) The ashes the corpus mounts on the Claymore score no higher than
this: Storm Blade 373, Sword Dance 395, Lion's Claw 353.

**Whole RL 150 ranking (MEASURED 2026-09-29, 820 rows, 12.9 min; compared with the section 14
run by `compare`).**
- **Skill gain.** 599 rows now gain from their skill, against 245 before.
- **Best option per row.**
  - Stormcaller 178 rows, Shield Crash 107, Parry 50, Buckler Parry 32, Endure 22, Shield Bash
    22, Beast's Roar 18, Waves of Darkness 16.
  - 221 rows gain nothing.
  - Stormcaller is the ceiling for most infusable STR weapons. Its six hits (three swings, three
    wind bullets) were all assumed to land. Section 15a replaces that with a per-hit landing
    model, under which it lands 2-4.
- **Largest rises.** Infusable weapons whose moveset is weak and which now carry their best ash:
  - Smithscript Cirque 2H 575 -> 50 and Backhand Blade 2H 430 -> 19, both with Swift Slash;
  - Lance 1H 576 -> 212, Glaive 1H 533 -> 159, Nightrider Glaive 1H 536 -> 121, all with
    Stormcaller.
- **Largest falls.**
  - Marika's Hammer 38 -> 283 (2H). Gold Breaker's leap was counted as free in section 13; with
    the hand-over its roll is at 76, not 29.
  - Uniques with no scorable skill (Blasphemous Blade, Hand of Malenia, Golden Order Greatsword,
    Dragonscale Blade) lose about 180 places each, because the infusable weapons pass them.
- **Top 20.** New at the top: Alabaster Lord's Sword 2H 1 (Alabaster Lords' Pull), Stormhawk
  Axe 2H 2 (Thunderstorm), Cranial Vessel Candlestand 2H 4 (Surge of Faith). Dane's Footwork
  1H stays third with no skill gain. The axes with a Buckler Parry option hold 5-6 and 9.

**Great spears (wepType 28).**

| row | section 14 rank | now | base | score | best option | corpus term |
|---|---|---|---|---|---|---|
| Messmer Soldier's Spear 2H | 389 | 147 | 759 | 1123 | Stormcaller 1487 | +54 |
| Lance 2H | 476 | 162 | 710 | 1103 | Stormcaller 1495 | +13 |
| Messmer Soldier's Spear 1H | 550 | 205 | 623 | 1053 | Stormcaller 1482 | +77 |
| Lance 1H | 576 | 212 | 605 | 1047 | Stormcaller 1488 | +30 |
| Serpent-Hunter 2H (unique) | 341 | 386 | 735 | 873 | Great-Serpent Hunt 1011 | +138 |
| Siluria's Tree 2H (unique) | 361 | 471 | 658 | 795 | Siluria's Woe 1114 | +137 |
| Treespear 2H (unique) | 349 | 473 | 793 | 793 | none | 0 |

The Lance and Messmer Soldier's Spear were below every unique great spear except Siluria's Tree
1H. Now they are above all of them, by about 230 places. Impaling Thrust, the ash one would
expect, scores 1005 in the ranking on the Lance 2H (reach 6.42 m, coverage 0.92). It loses to
Stormcaller's six hits.

**Adoption check (section 14d).**

| ranking | ash coefficient | CI | percentile coefficient | ash / percentile |
|---|---|---|---|---|
| section 14 | +0.205 (x1.23) | [+0.029, +0.347] | +0.215 | 0.95 |
| section 15 | +0.106 (x1.11) | [-0.088, +0.273] | +0.368 | 0.29 |

The ash residual halved and its CI now includes 0. The within-class score percentile explains
more (+0.215 -> +0.368, CI [+0.068, +0.649]). Nothing here was fitted to adoption: the weights
are unchanged (`SKILL_WEIGHT` 0.5, `slot_score`'s factors), and the move comes from counting
ashes the corpus rarely mounts.

### 15a. Per-hit landing of a multi-hit skill

The run above counted every hit of a skill. `skill_landing` now decides hit by hit which ones land,
and the skill's scored damage is the sum over those. The every-hit number is kept as `dmg_all` /
`score_all` (the `all hits` column of `compare`, `all` / `all-sc` of `term`) and is never scored.

**Displacement: knockback (VERIFIED, 1.16.2).**
- `FUN_140d24b10` copies `AtkParam.knockbackDist` (+0x10) to `AttackDamageInfo+0x58`. The same
  function writes +0x40 attackParamId and +0x44 the pc/npc type, which ties the struct.
- `FUN_140446750` (from `ApplyDamage` 0x1404497d0, after the poise damage) starts the defender's
  knockback only when the poise-remapped level `FUN_140690250(level)` is not 0:
  `dist = (1 - clamp(resist)) x max(dot, 0.4) x info+0x58 x info+0x200`. A level-0 hit (poise held,
  SpEffect 6352) forces resist to 1, so it pushes nobody.
- A player's resist is the chest's `EquipParamProtector.knockBack x 0.01` (`FUN_140689ad0`). All
  838 protector rows carry 0, and `knockbackParamId` 1 (regulation).
- `FUN_1404504e0`, started with the damage animation (HksAct 0x14040d31d), reads
  `KnockBackParam[1]`: ContTime c (`FUN_140d3c900`, row + 4i) and DecTime d (`FUN_140d3c920`,
  row + 0x3c + 4i). The speed is `dist x clamp(c / (0.5 d + c), 0, 1) / c`, held for c, then
  scaled by remaining / d each frame (`FUN_1404508c0`). The defender moves exactly `dist`. Large:
  c 0.09 s, d 1.2 s.
- Root motion of the damage clips (MEASURED, er-hkx-pose): small, middle, large, push and minimum
  carry none; small blow `a000_005400` 4.4 m and exlarge `005450` 6.9 m.
- INFERRED: the attack direction is along the line to the defender (so the facing term is 1);
  `info+0x200` and the module's per-frame multiplier are 1.0; the animation type the behavior
  script passes per level is paired by the column names; knockback and root motion add.
- Hit stop freezes the attacker, scaled by the defender's `NpcParam.hitStopType`
  (`FUN_14044fce0`). Rows 0 and 1 have type 0, so a player target is taken to cause none
  (INFERRED: which row a player reads was not traced).

These pushes are small. Stormcaller's rows push 0.2-0.7 m; Repeating Thrust 0.6 m, its last thrust
1.5 m. Most lost hits are lost to the timing below, not to the distance.

**Can he leave (frame-advantage.md, COMMUNITY HKS + TAE).** The first hit that touches is the
opener. Every later hit lands only if it reaches him on the same 60 Hz update as the previous
landed hit, or before the first roll or guard frame of the last damage animation a landed hit
started (`er-mechanics-frame-advantage.reaction`). Once he can leave, no later hit counts.
- Poise damage (hit poise x `saRate`, the same rule as the stagger share) accumulates from full.
  A break plays the raw damage level and the poise refills; nothing regenerates within one skill
  (INFERRED).
- While poise holds, the level goes through SpEffect 6352: small to large become 0, an additive
  flinch that neither locks nor pushes and leaves a running damage animation running. Small blow,
  exlarge, fling, upper and breath still react.
- Every new damage animation restarts the lock and raises `DamageCount`, so the next roll gate is
  earlier (large 35 -> 15 -> 5 -> 0). Two hits on one update reach the behavior script once and
  count once (INFERRED: `ExecDamage` runs once per update; the later level is taken).

**Geometry (MEASURED pose, INFERRED bullet placement).** A melee hit uses its own window's pose
samples (`window_contacts` from `er-mechanics-reach.skill_reach`, the same shapes, held-back rule
and defender profile as `front_contact_times`). A bullet hit flies the skill's tree (`fly_tree`,
the section 15 flight rules, sampled every 0.1 m so a 200 m/s bolt cannot step over a body).
The defender stands at 1.5, 2.0, 2.5 and 3.0 m (`FRONT_CONTACT_DISTANCES_M`), plus whatever
earlier hits pushed him. The skill's damage is the mean over the distances it connects at and the
corpus poises. A hit in another animation than the first hit's is left out: the hand-over is not
traced. Where a hit has no pose or flight it touches when its window opens. Where no hit touches
at any distance, only the lock decides.

Two model fixes came with it:
- A falling bullet that steps below the ground now stops on it. Lightning Slash's bolt ended a
  frame at y = -2.3, so its 1.4 m blast was created underground and could hit nobody.
- `bullet_reach` now reads the same `fly_tree` as the contacts.

**Per hit on Claymore and Lance (Heavy+25 2H, STR 60 DEX 20, 907 corpus poises).** The numbers are
hits landed at 2.5 m, the mean over poises, and the damage as landed / all hits (median defender,
mean over the connecting distances). They come from `landing`, MEASURED 2026-09-29.

| skill | hits | at 2.5 m | Claymore dmg | Lance dmg | what stops it |
|---|---|---|---|---|---|
| Stormcaller | 6 | 4 (2 at 3.0 m) | 701 / 1226 | 630 / 1101 | swing 1 and its bullet share one update (large, roll at 35); swing 2 + bullet restart it at count 2 (roll at 15); swing 3 is 20 frames later |
| Repeating Thrust | 4 | 3 | 537 / 861 | 495 / 794 | the last thrust comes 11 frames after a middle at count 3 (roll at 5) |
| Blood Tax | 4 | 3 | 426 / 733 | 388 / 670 | same rows and timing as Repeating Thrust |
| Lightning Slash | 4 | 2 | 604 / 863 | 574 / 825 | the f23 hit reaches only 1.5 m; the secondary bolt never touches |
| Eruption | 4 | 2 (3 at 1.5 m) | 573 / 699 | 529 / 655 | its two eruption bullets land only at 1.5 m, one of them |
| Spinning Gravity Thrust | 5 | 2 (Lance 3) | 351 / 569 | 224 / 515 | each drill tick reaches only some distances, and the lock ends before the rest |
| Wild Strikes (040051) | 2 | 1 | 303 / 631 | 278 / 579 | small at count 1 rolls at 10; the next strike is 19 frames on |
| Ground Slam | 2 | 1 | 517 / 1337 | 517 / 1337 | the f0 landing box of 040004 touches no defender at 1.5-3 m |
| Golden Land | 4 | 1 | 313 / 907 | 279 / 872 | its 3.5 m blast is a second after the slam |
| Gravitas | 4 | 1 (3 at 3.0 m) | 263 / 526 | 251 / 504 | the pull bullets come 22 frames after a small |
| Storm Blade, Double Slash, Sword Dance | 2 | 2 | unchanged | unchanged | both hits inside the lock |

**How much the break test matters.** Every skill hit here breaks every corpus poise, because the
stagger rule multiplies by `saRate` (2.7 on skill rows). That is the unresolved poise-unit question
of frame-advantage.md section 9. Two checks at 2.5 m:
- Without `saRate`: Stormcaller still lands 4, and Repeating Thrust and Lightning Slash are
  unchanged.
- With a poise that never breaks: Stormcaller lands 2 (the first swing and its bullet, one
  update), Repeating Thrust and Lightning Slash 1 each.

So Stormcaller lands between 2 and 4 of its 6 hits, never all of them.

**Whole RL 150 ranking with 15a (MEASURED 2026-09-29, 820 rows, 19.3 min; `compare` against the
section 15 run).**
- **Skill gain.** 549 rows gain from their skill, against 599.
- **Best option per row.** Stormcaller is the best option on no row, against 178. It is scored
  876 on the Lance 2H (1495 with every hit) and 894 on the Claymore 2H (1529); the ranking's
  landed damage is 57% of the every-hit damage on both. The rows it held went to Parry 109,
  Endure 86, Shield Bash 82, Buckler Parry 76, Troll's Roar 41, Impaling Thrust 33, Flame of the
  Redmanes 25; 271 rows gain nothing.
- **Largest falls.** Skills whose every-hit count was a many-bullet tree: Cranial Vessel
  Candlestand 4 -> 417 (2H) and 8 -> 592 (1H), whose Surge of Faith counted 22 hits for 3762
  damage and lands 1-3 for 894; Stormhawk Axe 1H 7 -> 350 (Thunderstorm 6 hits -> 2). The Lance
  falls 162 -> 361 (2H) and 212 -> 429 (1H).
- **Largest rises.** About 100 places each for rows that simply stopped being passed (Wakizashi,
  Club, Devonia's Hammer, Gargoyle's Blackblade: base score unchanged).

**Great spears (wepType 28) with 15a.**

| row | section 15 rank | now | base | score | best option |
|---|---|---|---|---|---|
| Messmer Soldier's Spear 2H | 147 | 327 | 759 | 882 | Endure 1005 (Impaling Thrust also 1005) |
| Serpent-Hunter 2H (unique) | 386 | 341 | 735 | 873 | Great-Serpent Hunt 1011 |
| Lance 2H | 162 | 361 | 710 | 858 | Impaling Thrust 1005 |
| Messmer Soldier's Spear 1H | 205 | 400 | 623 | 826 | Endure 1029 |
| Serpent-Hunter 1H (unique) | 455 | 422 | 632 | 811 | Great-Serpent Hunt 989 |
| Lance 1H | 212 | 429 | 605 | 802 | Endure 999 |
| Siluria's Tree 2H (unique) | 471 | 439 | 658 | 795 | Siluria's Woe 1114 |
| Treespear 2H (unique) | 473 | 440 | 793 | 793 | none |

The Lance and Messmer Soldier's Spear fall back among the unique great spears. The Messmer
Soldier's Spear 2H still sits 14 places above the Serpent-Hunter 2H, and the Lance 2H 20 below it.
Their best ash is now Impaling Thrust or Endure, the one the section 15 text expected.

**Adoption check (section 14d) with 15a.**

| ranking | ash coefficient | CI | percentile coefficient | ash / percentile |
|---|---|---|---|---|
| section 15 | +0.106 (x1.11) | [-0.088, +0.273] | +0.368 | 0.29 |
| section 15a | +0.109 (x1.12) | [-0.083, +0.281] | +0.362, CI [+0.086, +0.624] | 0.30 |

The ash residual barely moves and its CI still includes 0.

**Not established (section 15).**
- Whether a player picks the best ash, as the option value assumes, or the one the corpus shows.
  The corpus term is kept beside it for that comparison.
- How many hits of a multi-hit skill land (15a). The poise units of the break test, which of two
  same-update levels the behavior script reads, the knockback direction and the level-to-column
  pairing are INFERRED. The defender is taken to leave at the first frame he can, and never to be
  hit while standing in a lock he could have left.
- Bullet placement, flat ground, and no homing or target (the flight rules are VERIFIED, the
  placement INFERRED).
- A leap's landing frame. It is taken at its falling hitbox.
- A skill's reach when its first hit is in a follow-up animation. The lead-in's movement is not
  added.
- Whether an affinity change to reach an ash is part of the choice. It is not modelled.
- Buff-only ashes. They still enter only through the corpus-weighted buff alternatives (closed
  by section 16b).

## 16. Reaction, buffs as options, and what a dodge ash buys

On great spears the corpus mounts Bloodhound's Step 30, Braggart's Roar 28, Flaming Strike 23,
Sacred Blade 14, Chilling Mist 10, Charge Forth 6, Stormcaller 5, Endure 1 and Impaling Thrust 0
(`ashrank`, every RL, `pvp` filter). Section 15a picked Impaling Thrust or Endure. Three gaps were
closed; each is switched off by its own flag of `er-builds-pvp.py`, so each can be measured alone.

### 16a. The reaction dodge (`--no-react`)

Nothing modelled a defender rolling on a visible wind-up: a hit landed whenever it was in range.
Now every attack, slot or skill, meets a defender who waits and dodges on reaction, at the share
`REACT_SHARE` = 0.5 of openers (`INFERRED`; the other half meet a defender who commits, the case
the exchange factor already scores).

- **The shapes (MEASURED).** A slot's hit windows are its `window_contacts` (reach.md 7b, now kept
  for slots: `slot_contacts`); a skill's are the ones its landing plays (`_hit_contacts`: pose
  samples, or the bullet tree flown every 0.1 m). Both become `contact_geometry` arrays.
- **The dodge (TAE + hkx, MEASURED).** The medium roll's four directions (`roll_motion`): i-frames
  f0-13 from its first frame, its root motion per real frame, R1 from f20. A start escapes when no
  sample touches him outside the i-frames, with the roll's path applied (`_catches`). Invincibility
  is not a shield that consumes the hit: a live window still open when the i-frames end catches
  him, so an early roll loses to a long or late live window.
- **When he rolls (INFERRED constants).** He cannot press before the cue plus his reaction:
  lognormal, median `REACTION_MEDIAN_S` 0.25 s, log sd 0.2, plus two network legs of
  `NETWORK_ONE_WAY_S` 0.05 s (he sees the attack late, and his roll reaches the attacker's machine
  late; that the attacker's machine decides the hit is community knowledge, not traced). Among the
  runs of escaping starts still open he aims at the middle of the one he is likeliest to hit, with a
  normal timing error `DODGE_TIMING_SD_S` 0.05 s (`dodge_presses`). Median delay: 10.5 real frames.
- **The cue (TAE, COMMUNITY HKS).** The clip's start, except where the attacker picks the timing:
  an R2 is read from its release clip (the charge is held as long as he likes), a skill follow-up
  from its own press, a stance or input-driven hand-over from the animation it hands to. Leaps and
  wind-ups that run by themselves are read from their start.
- **Worth.** A slot is worth its whole damage when caught (`INFERRED` for a multi-window slot). A
  skill caught first by hit k is worth the hits that open at or after k, each at its section 15a
  landing share at that distance. `land` = (1 - share) + share x reacted / unreacted multiplies the
  slot's damage and status in `slot_score`.
- **Whiff punish.** An evading dodger punishes with an R1 when his R1 cancel (20) plus the pool's R1
  strike frame (exchange pool, per build) comes before the attacker can roll, and he ends within
  `PUNISH_REACH_M` 3.69 m of where the attacker's root motion left him; it costs `PUNISH_HP` 388
  (both the pool means of `OPPONENT_FALLBACK`, MEASURED). `whiff_hp` is subtracted like `parry_hp`.
- **Feints and delays.** Only the evidence above is used: the R2 charge hold and the sword-art
  follow-up windows. No other feint is modelled.
- **Not counted twice with the contest.** The exchange factor's startup and hyperarmor part, or
  the interrupt factor that replaces it for the openers it covers (`--interrupt`, interrupt.md
  section 6), also rewards a fast startup. So the two split the defenders instead of multiplying:
  in `slot_score` the hit is worth `(1 - share) x f_contest + share x reacted / unreacted`, and the
  stamina part multiplies the whole score. Without a reaction term the contest multiplies the
  whole score, as before. A jump is read from its jump input, 6 frames before the landed clip
  (`SCORE_ENTRY_FRAMES`, moveset.md 6c).

The follow-ups themselves are options now (`skill_followups`, `skill_option(follow=...)`): TAE event
66 applying SpEffect 100050 / 100051 (R2) or 100054 / 100055 (R1) in the opening is what
`SwordArtsOneShot_onUpdate` (COMMUNITY c0000.hks) reads to send that button to
`W_SwordArtsOneShotComboEnd` / `_2`; the window is where that SpEffect, the button's input window and
its cancel window overlap. The follow-up animation is taken to be the opening + 10 / + 20
(`INFERRED`). Flaming Strike on a Lance: 100050 f30-44, R2 input 87 from f21, R2 cancel 116 f30-35
then 4 from f35, so R2 pressed at f30-44 plays 040010: the MV 178 swing (f18-20) and the 40 s fire
buff 1775/1777 at f6. The activation 040000 only fires the six 2003 flames (0.17 s at 8 m/s, 1.36 m)
and their 2004 patches (0.8 m radius, 0.6 s). The option "opening then R2" is scored with both
parts, each landed and reacted to from its own cue, the follow-up pressed at f30, FP 4 + 10.

### 16b. Buff ashes in the option pool (`--no-buff-options`)

A skill that buffs its user, in its opening or a follow-up, is also scored as a buff
(`buff_option`), and an option is worth the larger of share x gain and the buff's gain:

    buffed = the moveset score with the skill's buff rows held (er-builds-pvp _buff_moveset_fn)
    score  = buffed x (N + cut) / N x C / (C + recasts x cast / engagements)

- The buff factors are `er-mechanics-buffs.expected_attack` with the skill's roots at p 1 and the
  casts one FP bar pays for; every slot's main hitbox is hit again with them and the moveset scored
  again. When the rows are 162/163 (buffs.md section 10) the sweep's grease comes off: a roar or a
  blade replaces the grease.
- The first cast is made before contact (buffs.md section 10's rule, `INFERRED`) and costs nothing.
  A buff shorter than the fight is recast ceil(fight / duration) - 1 times, capped by the casts
  left, each charged its cast's first roll frame. Since 2026-10-01 the fight is 180..300 s
  (buffs.md section 10, `er-mechanics-buffs.recast_plan`, mean over 13 points; it was 25 s, so
  a 60 s roar was never recast), and a recast's frames come off the fight as a time factor,
  1 - recasts x cast / fight, the rule every buff shares. Before, they were spread over the 5
  engagements as extra commitment.
- `cut`: the buff's lowest PvP damage-taken rate over its cycled rows (Braggart's Roar 1861: 0.9)
  as section 14b charges it, (1 - win) x (1 - taken) x the opponents' mean landed hit x uptime.
- The corpus-weighted skill buffs leave the base, which would count them twice.

### 16c. What the dodge ash buys (`--dodge-timing uniform`, `--opponent-openers r1`)

Section 14a valued Bloodhound's Step only against R1 #1 strings, with the press uniform over 30
frames. Now the pool throws each moveset family's best engagement at its use share (`families`,
the opener and its first chained follow-up), the dodge is timed by the section 16a reaction model
per opponent (`Opponents._table`), and the punish checks the distance to where the attacker's
advance left him. Measured on the step (TAE a756 + hkx): i-frames f0-10, R1 from 16-17, 4.5-5.2 m;
the medium roll f0-13, R1 from 20, 3.2-4.4 m.

Result (MEASURED 2026-09-30): on the Lance 2H both dodges evade 71.3% of the pool's openers and
avoid 289 HP per string; the step punishes 0.4% of them, the roll 0.2%, so the step is worth
+0.2 HP. A reactive dodge starts close to the hit, and the pool's openers recover 10-25 frames
after it, which is shorter than any dodge's R1 cancel plus a strike. So from its frames and
distances the step buys nothing measurable here, and the section 16 validation does not move with
this gap. What it does in play that this does not model (approach from outside reach, spacing,
tracking) is still open.

**Correction (2026-10-01, er-effects-rs-8uha).** Until this date the `families` table had no jump
openers. `_opener_row` looked each family's opener up in the ranking row's stored slots, and the
jump openers (`jump_r1_f`, `jump_r2_f`, ...) are synthesized by `er-builds-pvp.jump_openers`, never
stored. So the jump family, the largest at 28.8% mean use share on the RL 150 ranking, was dropped
from every row, and the remaining shares were not renormalised (median retained share 0.707, min
0): a weapon whose players jump more counted for less as an opponent. Every result in this section
and in 16d was measured without it. Now `opponents_from_results` takes `slots_fn` (the ranking
passes the stored slots plus `jump_openers`), renormalises each key's shares over the openers that
resolve, and a jump opener starts at its `neutral_in` strike, counted from the jump input (22 f on
Alabaster Lord's Sword 2H), not at the landed clip's 2.5 m contact (17.5 f), which would make
every jump 4.5 frames too fast.

MEASURED on the RL 150 ranking (`er-opponent-family-pool-probe.py`, 945 pool builds): the table
grows from 868 to 1151 rows, the jump openers take 26.8% of its weight (0 before), and its
weighted means move from strike 14.96 f, reach 4.11 m, hit 425.7 to 16.85 f, 4.42 m, 428.2. The
opponents now open slower and from farther away, so a dodge has more to read and a punish has
farther to go. On a five-weapon run (Giant-Crusher, Uchigatana, Dagger, Lance, Great Stars, both
grips) no row's best skill option or score moves; only options that are not chosen change.

### 16d. Validation and the whole ranking (MEASURED 2026-09-30)

Nothing below was fitted. `ashrank` orders the nine great-spear ashes by the model's best score
for each (the larger of its hit, utility or buff score; every option measured, `--measure-all`)
against the corpus mounts above, for the Lance and the Messmer Soldier's Spear 2H, each as Heavy
STR 72 / DEX 14 and Keen STR 26 / DEX 66, with and without the lightning grease. Opponents come
from the section 15a ranking (`--opponents-from`). Spearman, mean of the eight rows:

| configuration | Spearman |
|---|---|
| section 15a (all three off, R1 opponents, uniform dodge timing) | -0.72 |
| 16a reaction (with follow-ups) only | -0.47 |
| 16b buff options only | -0.60 |
| 16c dodge timing and family openers only | -0.72 |
| all three | -0.36 |

Lance 2H Heavy with the grease, best score per ash, section 15a -> all three: Impaling Thrust
1005 -> 511, Endure 935 -> 619, Stormcaller 876 -> 470, Flaming Strike 839 -> 594 (its R2
follow-up), Braggart's Roar 0 -> 443, Bloodhound's Step 696 -> 380, Sacred Blade 713 -> 358,
Chilling Mist 667 -> 379, Charge Forth 104 -> 70. The order is now Endure, Flaming Strike,
Impaling Thrust, Stormcaller, Braggart's Roar, Bloodhound's Step, Chilling Mist, Sacred Blade,
Charge Forth. The correlation is still negative: Endure (1 mount) stays first, Bloodhound's Step
(30) gains nothing, and Sacred Blade and Chilling Mist lose to the grease they replace on a
greased build (their frost and anti-undead parts are not valued).

- **Flaming Strike against Impaling Thrust.** A waiting defender dodges both completely (p_evade
  1.0; the 2004 flame patches sit 1.4 m ahead with a 0.8 m radius and do not reach a defender at
  2.5 m after he has rolled). What reverses them is the follow-up: scored alone, the activation
  gives 138 flat fire (463), while activation then R2 at f30 lands the MV 178 swing as its own
  reaction event (594). Impaling Thrust has no follow-up and falls from 1005 to 511.
- **Why Flaming Strike scored low before.** Section 15a scored only the activation's six flames:
  the swing and the 40 s fire buff are in 040010, which only an R2 in f30-44 plays.
- **Buffs on a greased build.** Flaming Strike's and Sacred Blade's 90 flat fire / holy replace
  the lightning grease, so their buff scores (356, 358) sit below the greased base (379).
  Braggart's Roar's x1.1 physical and x0.9 damage taken beat the grease (443).

Whole RL 150 ranking (`compare-react.out`, against the same ranking with all three off,
`rank-base.json`; both include the jump families of moveset.md section 6):
- Ash adoption coefficient: +0.078 (CI [-0.108, +0.239]) -> +0.049 (CI [-0.138, +0.203]);
  within-class score percentile +0.434 -> +0.466. With `--interrupt` (interrupt.md section 6):
  +0.058 with saRate, +0.067 without.
- The reaction term lifts fast weapons: daggers, claws and katars rise 200-385 places (Raptor
  Talons 1H 432 -> 47, Hookclaws 2H 274 -> 24, Bloodstained Dagger 2H 320 -> 75), because a
  first contact near frame 11 comes before most reactions. Slow skills fall: Inseparable Sword
  1H 148 -> 483, Starscourge Greatsword 1H 144 -> 415, Axe of Godfrey 2H 76 -> 314.
- Best option per row: none 214, Parry 128, Endure 90, Sword Dance 62, Shield Bash 48.
- Great spears: Messmer Soldier's Spear 2H 194 -> 162 (Endure), Lance 2H 202 -> 252 (Storm
  Assault).

Not established (section 16): the reaction constants and `REACT_SHARE`; hit authority on the
attacker's machine; that every roll direction is used equally; the follow-up animation id rule;
that the first buff cast before contact is free; the status and anti-undead parts of weapon
buffs; any value of a dodge ash beyond evading and punishing the pool's openers.

## 17. The skill contest, the neutral game, and what overvalued Endure

Section 16 left the great-spear ash order anti-correlated with the corpus (Spearman -0.36): Endure
first with 1 mount, Bloodhound's Step (30) at the moveset score. Three changes, each measured
alone below. None is fitted to the corpus.

### 17a. A skill option pays the contest a slot pays (Swift Slash)

The whole RL 150 ranking with `--interrupt` (section 16d) put Backhand Blade 2H Keen first at 913,
its moveset 459 and Swift Slash's option 1367 (6 hits, 1222 damage after the reaction dodge,
roll at 55, reach 8.9 m). Checked against the data:

- **The hits (TAE a877 040000, VERIFIED hit-list rule, attacks.md).** Two melee events at f28-38,
  judges 6020 and 6021 on attack indices 0 and 1, so two hit lists: AtkParam 600000070 (MV 104,
  dummies 10120/10100, the right blade) and 600000071 (MV 104, dummies 11120/11100, the left
  blade). The Backhand Blade is `isDualBlade` 1, so the left blade exists. Only four ashes carry a
  hit on left-blade dummies alone (Raging Beast, Savage Claws, Blind Spot, Swift Slash), and they
  mount only on beast claws and backhand blades, both paired, so no single weapon is credited a
  blade it does not hold.
- **The bullets (regulation).** Four bullet events at f27/29/30/33 (dummy poly 9) fire 2070-2073:
  speed 0, life 0.54/0.44/0.34/0.24 s, no damage. Each expires into 2074 (`launchConditionType`
  0): 10 m/s for 0.2 s, radius 2.2 m, AtkParam 600000074 MV 160. All four burst at 1.34-1.44 s,
  f40-43: the delayed slashes.
- **`isUseSharedHitList` (Bullet +0x9b bit 2, VERIFIED 1.16.2).** `FUN_14038fe80` gives a shot
  entry (`CSBulletManager0x20Entry`, one per launch, `FUN_1403a5a10`) one DmgHitRecord named
  `share|bulletId:%d` when the flag or `attachEffectType` is set; `FUN_14051ced0` takes a fresh
  record from the free list every call, with no lookup by name. A `HitBulletID` child is launched
  through `FUN_1403a2c40` -> `FUN_1403a5a10`, a new entry with its own record. So the flag makes a
  `numShoot` fan hit a target once; it does not join the four separate launches. All four 2074s
  can hit.
- **Landing (section 15a).** On a defender who holds still all six land at 1.5-3.0 m: the f28
  blades break every corpus poise (150 x saRate), the middle reaction's lock outlasts f43, and the
  0.8 m knockback does not carry him out of a 2.2 m burst. Against the reaction dodge 60.7% evade
  (a lateral roll clears the bursts; a back roll is caught), land 0.696.
- **Reach.** The 8.9 m is the melee reach: the 6.4 m dash between f27 and f39 plus the blade. The
  bullet model places its roots 1 m ahead of the start (section 15, `INFERRED`), not along the
  dash, which would put the markers at 0.5-5.1 m; `f_reach` saturates at 5.6 m either way.

So the six hits are the data's. The modelling error was elsewhere: a skill option was scored with
contest and stamina factors of 1.0 and no equip-weight factor, which every slot pays. Swift Slash
has no hyperarmor and first touches a defender 2.5 m ahead at f28 or later, against the pool's
R1s at f13-18, so it loses nearly every committed exchange. Now `skill_contest_inputs` gives the
option its first 2.5 m contact (the landing's shapes and flights), that hit's poise x saRate, the
skill's TAE 795 windows and the melee events' stamina, and `er-builds-pvp.Mechanics.skill_exchange`
runs `er-mechanics-exchange.exchange` and `slot_stamina` on them. The option now carries its
landed damage and its reaction share (`react`) like a slot, so only the committed half meets the
contest (section 16a); without a contest the score is unchanged.

Swift Slash on Backhand Blade 2H Keen+lightning: 1367 -> 833, the weapon 913 -> 646 (1H 890 ->
621), which is rank 48 in the section 16 ranking's distribution. Its landed damage is unchanged.

### 17b. The neutral contest

The committed exchange now starts from the neutral game (neutral.md): both players outside both
reaches, the shorter reach closes the difference at run speed or with a roll, then first hit,
poise and hyperarmor as before. Reach becomes time (7.48 frames a metre). A great spear gains most:
Lance 2H R1 0.978 -> 1.153, Impaling Thrust (6.42 m) wins every committed exchange (0.779 ->
1.250). It enters `slot_score` as the contest, and under `--interrupt` as the ratio to the 2.5 m
exchange on top of the interrupt factor.

### 17c. What overvalued Endure

Section 14b valued Endure cast ahead of every engagement:

    hp = loss x dmg                          losses become trades (VERIFIED override)
       + (1 - win) x 0.4 x opponent hit      every hit taken is cut
    commit = 4 frames                         the frames before the buff lands

On the Lance 2H Heavy+lightning (best opener the forward jump R1) that is 64 HP of trades and 94
HP of cut per engagement, against a numerator of about 265 HP: the option multiplied the whole
moveset by 1.63 (619 against 379). Checked term by term:

- **FP (not it).** 9 FP against the corpus bar of 85: 9 casts, more than the fight's 5
  engagements; the share is 1 either way.
- **Duration against the engagement rate (part of it).** 3 s (`effectEndurance` 3.0), against 5 s
  between engagements (`ENGAGEMENT_SECONDS`; it was read as 25 s over 5 landed hits, and stays
  5 s now that the buffs' fight is 3 to 5 minutes). One cast covers at most one engagement, as
  section 14b assumed; but the coverage test (3 s outlast the cast's R1 at f22 by the opener's
  strike frame) assumes the engagement starts the moment the cast ends.
- **Visibility (the term that overvalued it).** SpEffect 1650 carries `vfxId` 8570: the buff is
  drawn on the caster. A defender who sees it start and backs off for 3 s gives up nothing: he
  moves back at the same run speed the caster chases at, and the next engagement was 5 s away
  anyway. Both parts of the term are exchange terms, and only a defender who commits into the buff
  pays them. Section 14b credited them on every engagement, for every defender, including the half
  that waits and dodges (`REACT_SHARE`) and would not have committed with or without Endure.
- **Hyperarmor already credited (partly).** Against the 2.5 m exchange the forward jump R1 loses
  27% of committed exchanges; from the neutral game it loses none (its reach outranges the pool),
  so the trades Endure buys there were already the opener's. The Messmer Soldier's Spear's best
  opener (its R2) still loses some: 6.6 HP of the old 168.

Now (`utility_value`, neutral.md section 4): cast ahead, the committed share only, and a visible
buff shorter than the engagement spacing is waited out, so it covers nothing (`INFERRED`). Cast as
an answer to the opponent's string (`Opponents.buff_answer_value`: pressed at his cue plus the
reaction, each hit that lands inside the buff taken at 0.6, the punish if the caster's R1 plus
strike comes before the attacker's recovery), it is compared with the medium roll it replaces: 78
HP against the roll's 260 per string on the Lance, so worse by 112 HP x `p_second`. Endure's option
now equals the moveset score. The section 14b number is kept as `legacy_hp`.

### 17d. Bloodhound's Step

Unchanged in substance: 0.2-0.4 HP over the roll per string. In the neutral race the step closes
sooner than running or the roll only above 2.27 m, and a great spear is almost never outreached
by that much (neutral.md section 5), so the best opener's `f_neutral` is the same with and without
it. The punish from outside reach (`Opponents.dodge_value` now lets the dodger run in) stays at
0.2% of strings.

### 17e. Validation (MEASURED 2026-09-30)

Nothing was fitted. Same eight rows as section 16d (Lance and Messmer Soldier's Spear 2H, Heavy
STR 72 / DEX 14 and Keen STR 26 / DEX 66, with and without the lightning grease, `--measure-all`,
opponents from `rank-react.json`), same nine ashes, corpus mounts over every RL.

| configuration | Spearman, mean of the eight 2H rows |
|---|---|
| section 16d (all three of section 16) | -0.36 |
| 17a skill contest + 17c Endure (`--no-neutral`) | +0.53 |
| 17a + 17b + 17c (neutral on) | +0.66 (per row 0.71, 0.71, 0.75, 0.75, 0.66, 0.68, 0.36, 0.63) |

Lance 2H Heavy+lightning, best score per ash, section 16d -> now (moveset 379 -> 462):

| ash (corpus mounts) | 16d | now |
|---|---|---|
| Flaming Strike (23), its R2 follow-up | 594 | 658 |
| Braggart's Roar (28), as a buff | 443 | 516 |
| Bloodhound's Step (30) | 380 | 462 (= moveset) |
| Chilling Mist (10) | 379 | 462 (= moveset) |
| Endure (1) | 619 | 462 (= moveset) |
| Sacred Blade (14) | 358 | 435 |
| Stormcaller (5) | 470 | 411 |
| Impaling Thrust (0) | 511 | 397 |
| Charge Forth (6) | 70 | 60 |

What moved it: Endure falls to the moveset (17c); Impaling Thrust and Stormcaller now pay the
committed exchange they lose at 2.5 m, and the stamina factor (Impaling Thrust 0.63), which
Flaming Strike's R2 follow-up and the buffs do not. The step, the mist and Endure tie at the
moveset, so the correlation comes from the others; Bloodhound's Step's 30 mounts are still not
explained (17d).

Whole RL 150 ranking with `--interrupt` (`rank-neutral-int.json` / `-nosa.json`, against section
16d's `rank-react-int.json` / `-nosa.json`; `rank-contest-int.json` is `--no-neutral`):

| ranking | ash coefficient | CI | percentile coefficient |
|---|---|---|---|
| section 16d, saRate / without | +0.058 / +0.067 | [-0.130, +0.215] / [-0.119, +0.228] | +0.451 / +0.430 |
| 17a + 17c only (saRate) | +0.093 | [-0.090, +0.241] | +0.400 |
| 17a + 17b + 17c, saRate / without | +0.031 / +0.028 | [-0.149, +0.182] / [-0.154, +0.181] | +0.517 / +0.520 |

- **Backhand Blade 2H Keen.** 913 (rank 1) -> 646 (13) with the contest alone -> 861 (3) with
  the neutral game: its moveset rises 459 -> 531, and Swift Slash's 8.9 m dash, raced from the
  neutral game, wins the committed exchanges it lost at 2.5 m (1191). The strike frame used is the
  2.5 m contact (neutral.md section 3), which favours a long lunge.
- **Great spears.** Messmer Soldier's Spear 2H 159 -> 90, Lance 2H 261 -> 126, Lance 1H 160 -> 137;
  Treespear 2H 373 -> 204, Serpent-Hunter 2H 385 -> 463. Best option: Storm Assault.
- **Largest moves** (138 of 820 rows move more than 100 places, mean 53). Up: long unique reaches
  with no skill gain (Treespear 1H 401 -> 168, Dragon-Hunter's Great Katana 2H 376 -> 144,
  Bloodhound's Fang 1H 241 -> 22, Bloodfiend's Sacred Spear 1H 473 -> 259). Down: short fast
  weapons the neutral race makes walk in (Raptor Talons 1H 66 -> 424, Estoc 1H 119 -> 372, Monk's
  Flamemace 2H 202 -> 455, Cipher Pata 1H 317 -> 568, Rapier 2H 139 -> 375), and Swift Slash on
  the Smithscript Cirque (133 -> 342).
- Best option per row: none 268, Flame of the Redmanes 123, Parry 118, Buckler Parry 94, Storm
  Assault 45, Sword Dance 34, Flame Skewer 26.

Not established (section 17): the neutral model's assumptions (neutral.md section 6); that a
visible buff shorter than the engagement spacing is always waited out; what Bloodhound's Step
buys beyond frames and distances.

## 18. Which ash a build mounts: slot and fight format (MEASURED 2026-09-30)

`python3 scripts/er-builds-ash-choice.py --pvp rank-par-base2.json` (nothing fitted, nothing fed
back). One choice per corpus slot on an ash-capable ranked weapon, among that weapon's
`skill_term.available`; PvP corpus, RL 140-160 (962 builds, 1462 choices) and every RL (3347,
4587). Tags are the planner's: Duels, Invasions, Co-op/Gank, 2v2.

**Where Bloodhound's Step sits.** Window: 343 mounts, 54 at equip index 0 (primary right hand),
229 at 1-2 (right-hand swap slots), 60 in the left hand; every RL 110 / 442 / 104 (Misericorde
360, Cleanrot Knight's Sword 75). Endure the same shape (21 / 92 / 7). Two thirds of the step is
a dedicated swap weapon, not the primary's skill, so no per-weapon term can carry it.

**Fight format.** Mount rate (mounts / slots that could take it), bootstrap over builds:

| skill | duels only | invasion or gank, no duels | builds carrying it, same split |
|---|---|---|---|
| Bloodhound's Step, RL 140-160 | 0.143 [0.054, 0.283] (n 26) | 0.442 [0.394, 0.489] (n 181) | 0.27 / 0.70 |
| Bloodhound's Step, every RL | 0.060 [0.031, 0.093] (n 148) | 0.324 [0.298, 0.348] (n 515) | 0.09 / 0.52 |
| Endure, every RL | 0.034 [0.013, 0.057] | 0.115 [0.094, 0.132] | 0.05 / 0.20 |
| Quickstep, every RL | 0.034 [0.016, 0.065] | 0.024 [0.014, 0.034] | 0.05 / 0.04 |
| Parry, RL 140-160 | 0.250 [0.129, 0.361] | 0.075 [0.042, 0.105] | 0.32 / 0.10 |

Great spears, every RL: Bloodhound's Step 23 primary + 7 left, none in a duels-only build.

So the step is a multiplayer tool. In 1v1 the corpus agrees with section 17d (the step is worth
a roll): duels-only builds rarely carry it. The model scores one attacker; what the step buys
against two pursuers, invisible f5-10 and 5.24 m away, is not in it.

**Across skills** (102 with exposure >= 30, Spearman with mount rate, window / every RL): model
value 0.33 / 0.28, dodge 0.31 / 0.44, parry 0.31 / 0.42, dodge distance 0.39 / 0.49, low FP cost
0.14 / 0.18, reach 0.03 / 0.15, invasion lift 0.53 / 0.36, gank lift 0.52 / 0.42.

**Conditional logit** (coef, 95% CI over builds, every RL; value = log option score over the
row's moveset score floored at 0.1): value alone +0.68 [+0.62, +0.73], pseudo-R2 0.028. With
own-skill, dodge, buff, parry, FP/10: value +0.80, dodge +0.67, FP -0.34, R2 0.097. By slot,
dodge is +0.03 [-0.16, +0.18] at the primary and +2.15 [+1.98, +2.36] at the swap slots; by
format +1.57 (invasion) and +1.74 (gank) against +0.59 (duels). FP cost is negative in every
subset (-0.28 to -0.80).

**Weapon-level ash check by format** (`--format-check`, primary adoption counted only in builds
of that format, RL 140-160, 1000 resamples):

| format | builds | ash coefficient | percentile coefficient |
|---|---|---|---|
| all (reproduces `check`: +0.014 there) | 962 | +0.010 [-0.158, +0.176] | +0.564 |
| duels only | 32 | +0.032 [-0.020, +0.084] | +0.034 |
| any duels | 168 | +0.039 [-0.059, +0.134] | +0.307 |
| invasion or gank, no duels | 217 | -0.050 [-0.155, +0.053] | +0.326 |

The weapon-level ash residual is zero in every format, so it has nothing left that a dodge term
could explain: the step's mass is on swap weapons and in multiplayer builds, outside both the
primary-adoption check and the 1v1 model. No scoring term was added. A multi-opponent dodge value
(two pursuers, the step's invisibility and distance) is the unmodelled mechanism; scoring it would
need a 2v1 engagement model, and the score it feeds would be a build-level swap-slot pick, not the
primary weapon's.

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py skill "Lion's Claw" --weapon Greatsword [--level 25] [--json]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py list [--mountable Claymore]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py adoption --rl 150 --window 10 --filter pvptag   # or tag, str60
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py pvp --rl 150 --skills "Lion's Claw,Storm Stomp"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py term --weapon Giant-Crusher --aff Heavy \
    --stats str=80,dex=12 --two [--best 1706] [--filter pvp] [--engagements 5]   # corpus mix, then every mountable skill
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py landing --weapon Claymore --aff Heavy \
    --stats str=60,dex=20 --two --skills "Stormcaller,Repeating Thrust"   # section 15a, per hit
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py check --pvp rank.json   # section 14d
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py compare --pvp rank.json --before old.json --cls Lance   # section 15
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py ashrank --pvp run.json --cls Lance \
    --names "Bloodhound's Step,Braggart's Roar,Flaming Strike" --rl 357 --window 356   # section 16d
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --weapon "Lance,Messmer Soldier's Spear" \
    --sort score --json --measure-all --opponents-from rank.json [--no-react] [--no-buff-options] \
    [--no-follow-ups] [--opponent-openers r1] [--dodge-timing uniform] [--interrupt m.json] [--no-neutral] \
    [--build-aff Keen --build-stats str=26,dex=66 --build-grease none]   # section 16
```

`skill` takes ~10 s (regulation decode), `list` and `pvp` ~30-60 s, so run those in the
background.
