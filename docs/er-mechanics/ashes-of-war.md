# Ashes of war and weapon skills

Labels as in [attacks.md](attacks.md): **VERIFIED** = read in the executable (1.16.2 VAs from the
named Ghidra dump on :8765, shift 0 against `eldenring-deobf.bin`) or a value in the installed
1.17.1 `regulation.bin`; **TAE** = decoded from the player TimeAct files; **INFERRED** =
consistent with the data, consuming code not traced; **COMMUNITY** = outside claim (Smithbox row
names, wiki); **MEASURED** = counted in the planner corpus. Nothing was launched.

Tool: `scripts/er-mechanics-ashes.py` (commands at the end). `--selftest` passes 149/149
(2026-11-10).

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
  `attackNotFromWeapon` at build time. A sorcery, incantation or consumable bullet does get it, at
  the hit: `FUN_14038e540` sets `BulletAttackInfo+0x198` for a spawn with a magic or goods id and
  `FUN_1403960a0` copies it into `+0xf4`, so those take the flat branch (attack-rating.md section
  4). A skill bullet (no magic or goods id) takes the first branch unless its spawn sets bit 2 of
  `+0x44`; which spawns set that bit is not read. Skill bullet rows have MV 0
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
  atkSuperArmorCorrection`); the tool applies the same weapon formula to bullets. The attacker
  poise builder FUN_14068af30 (1.16.2) takes the weapon formula unless the attack copy's `+0xf4`
  (`attackNotFromWeapon`) is set (`cmp byte [rsi+0xf4],0; jnz 0x14068b0d8` at 0x14068afa4, read
  2026-11-16 on :8765), and a skill bullet leaves `+0xf4` clear (no magic or goods id, above;
  bd goods-and-magic-bullets-take-flat-attack-branch-via-hit-copied-flag-2720-2026-11-09), so the
  formula holds for a skill bullet once its hit reaches FUN_14068af30. That a bullet hit goes
  through FUN_140d24b10 -> FUN_14068ad90 -> FUN_14068af30 as a melee hit does is not traced
  (INFERRED). Rosus's Summons: Rosus' Axe `saWeaponDamage` 5.0 x 150 / 100 = 7.5 per burst
  (75 menu units), x `saRate` 2.7 (FinalDamageRateParam 10000) = 202.5 in PvP.

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
| Barbaric Roar / War Cry | 16 | 1680/1682, 1810/1812 -> 1681/1683, 1811/1813 | physical attack x1.075, `changeStrengthPoint` 5: +5 on the weapon's reinforced STR scaling rate (attack-rating.md section 7; Giant-Crusher +25 at 80 STR: +17 AR, +2.1%) | 40 s; no FP the same for 6 s |
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
- What `dmgLv_*` and `defFlickPower` do to incoming reactions (Endure). (`changeStrengthPoint` 5
  on the roars is traced: attack-rating.md section 7.)
- Bow skills (event 64, ammunition) are not classified; Igon's Drake Hunt's arrow judges 890/895
  do not resolve against the bow.
- Shared hit lists: established since (section 20). A launch whose row has `isUseSharedHitList`
  or `attachEffectType` hits a target once with its whole tree; `cast_mask` lands one row per
  list. Glintblade Phalanx and Sacred Ring of Light do not share, so both their bullets may hit.
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
    (FUN_14039bba0), and only if the gate allows that ending: 0 and 3 always, 5 on a hit, 4 on
    expiry, 6 on a hit only, 1 and 2 only on water. On a hit the gate reads the parent's own
    `launchConditionType`, on expiry the child's, or the parent's again when its
    `attachEffectType` is 4 to 6 (FUN_14039da40 reads the state index +0x4a0, which `KillBullet`
    moves to the explosion state first; FUN_140d34100; bd er-effects-rs-rggx, `launches`). A
    parent that passes through characters makes no child on one (FUN_1403aba90). So Stars of
    Ruin's first legs (4) make their second leg only when they expire. `EmittePosType` 2 starts 1.0 m higher
    and flies level (FUN_140390c20, constant .data 0x143b15b5c). An `intervalCreateBulletId`
    child starts at the parent's position when it spawns.
  - **Reach.** The farthest horizontal point any damaging bullet reaches, plus `max(hitRadius,
    hitRadiusMax)`.
  - **Placement (`bullet_launch`, bd er-effects-rs-doax).** A root shot leaves the caster's
    DummyPoly (VERIFIED: its base matrix is the live dummy-poly matrix, FUN_140391850 ->
    FUN_1403f13c0). The bullet event (TAE 2) and the cast event (TAE 64) name it in their first int
    (TAE: 9 on all four Swift Slash bursts). It is placed by the clip's pose at the event's clip
    time, root motion and yaw included (MEASURED, er-hkx-pose): an id under 10000 that c0000.flver
    carries rides its attach bone (the reach model's body-shape rule; Swift Slash's 9 is 0.23 m ahead
    of the Pelvis), a 10xxx / 11xxx id is dummy xxx of the right / left weapon model on R_Weapon /
    L_Weapon (the weapon-shape rule). Anything else, or a shield's skill (cast from the off hand,
    which the skill model does not play, so the hand is not known), falls back to the root's
    position at the event plus 1.0 m ahead (`BULLET_ORIGIN_M`, INFERRED placeholder). A follow-up
    clip is placed from its own start, as its melee hits are.
  - **Which model an id names (VERIFIED 2026-11-10, 1.16.2).** `PlayerIns::GetLocationMdlDmypolyModifier`
    -> `CSChrAsmModelIns::GetDmyPoly` -> FUN_140b659a0: with t = (id / 1000) % 100, 0 is the body
    model (c0000), 1-4 ChrAsm slots 2-5, 10-13 (and 30-33) slots 11-14, 20-23 slots 7-10, and with
    `chrAsmArmStyle` 2 the 10-13 ids read the 20-23 slots; the dummy is id % 1000. That 10-13 are the
    right-hand weapon's models and 20-23 the left hand's, model 1 of a dual blade its twin, is
    INFERRED from that redirect and from AtkParam 600000071's 11120/11100 on a dual blade's left
    blade. So Bear Witness! 040040's 20300 is the left-hand weapon's dummy 300: unknown for the
    right-hand cast the model plays (placeholder), the Grafted Dragon's when it is in the left hand.
  - **Which way it starts (VERIFIED, 1.16.2).** The event's orientation byte (TAE 2 Args byte 0xc,
    `BulletBehavior` 0x140426e60; TAE 64 Args byte 5, FUN_140429db0) reaches FUN_1403f13c0 through
    FUN_1403c0730 / FUN_1403c0320. Through FUN_1403fb5f0 (the branch a player takes, INFERRED: the
    other two need the main player's `chrFlags1c5` bits or an event-entity check) byte 0 heads along
    the character's physics orientation, level (a bow's aim angles when `IsBulletAimAngleSet`), and
    any other byte along the dummy's own frame (FUN_1403f51a0: its matrix's third row). FUN_140390c20
    then turns the shot toward the lock-on target in 3D within `lockShootLimitAng` (FUN_14038f120)
    unless the byte is 1 (the aim point is the target's lock-on DummyPoly 220, 1.24 m up on an idle
    player, plus `targetYOffsetRange`, decoded 2026-11-12; since round 4 of bd er-effects-rs-7bye
    every flight of the model, the punish geometry's with the spell record counts', turns the whole
    3D frame). Skill bullet events: 625 with 0, 76 with 2; spell casts: 696 with 0, 62
    with 2 (the breaths, Comet Azur, Placidusax's Ruin), Dragonmaw's 1. A byte-2 shot leaves the dummy
    itself, height and pitch included, along its posed forward axis; that the matrix's third row is
    the FLVER dummy's forward vector (+0x10) carried by its bones is INFERRED (the FD4 location
    modifier that builds the matrix was not read) and agrees with what the attacks do: the bow
    skills' arrows leave within 5 degrees of the facing, Sky Shot's 80 degrees up, Light's eight
    beams spread round the caster and down, Firebreather's 28 degrees aside and mirrored on its
    other-side clip, the breaths within a few degrees of the facing. A byte-0 shot keeps the
    placeholder height, 1.0 m above the root (`BULLET_ORIGIN_HEIGHT_M`, INFERRED): the 3D lock-on
    turn would pitch it at the lock-on target's aim point, whose height is not decoded (the
    lock-on bullet below), and from a raised hand a level shot passes over every defender
    (Thunderbolt's DummyPoly 1 is 2.0 m up at its event, Glintblade Phalanx's 2.1 m). A byte-2 shot
    flies its decoded pitch with no vertical turn, so where that turn would bring it down onto the
    target the model under-credits it (Magma Breath's blob, `lockShootLimitAng` 35, now pitched
    5.7 degrees up from 1.37 m, lands at 9.3 m instead of 7.1 m).
  - **One shot per event (VERIFIED 2026-11-10, 1.16.2).** `BulletBehavior` keeps running on the
    calls after an event's first when Args byte 0xd is 1 (91 skill bullet events: Bear Witness!'s
    f49-80 breath, Firebreather, Dragonform Flame, Regal Roar, Light), but the shooter it calls,
    FUN_1403c2d60, spawns only when the request's `firstExecution` byte (+0x10) is set; on the
    later calls it skips the spawn and only calls `SetRendererVisibility(chr, 5)` (the same byte
    0xd). The cast handler FUN_140429db0 runs on every call with no such return, and its shooter
    FUN_1403c1450 has the same `firstExecution` gate. So each event fires its bullet once, at its
    start, as modelled; my 2026-11-10 note that such events respawn every update was wrong.
  - **Hit priority records (VERIFIED 1.16.2; modelled, bd er-effects-rs-dnok).**
    - *Slots and windows.* TAE event 341 (FUN_1404298d0, run by `ExecuteThreadOne`) finds or
      creates the `HitPriorityRecord` slot Args byte 0 of the caster's `ChrSlotSys` (0 below TAE
      version 0x25) and gives it an entry: a fresh `DmgHitRecord` ("priorityRecord",
      FUN_1404a47f0) with this frame's part of the event as its window (FUN_14049ad80 ->
      FUN_1404a49c0). On the event's later frames FUN_1404a5090 finds the same entry, so one record
      lives as long as the event runs without a gap; FUN_1404a4cb0 drops entries not refreshed.
    - *Who takes it.* An attack looks up the record whose window covers its event time
      (FUN_14049ad30 -> FUN_1404a4940) when its priority byte is not negative. A swing
      (`AttackBehavior` -> FUN_1404428f0, and the goods and magic paths FUN_140442eb0 and
      FUN_140443180) always uses slot 0 (`xor edx,edx` at 0x140442d61) with priority Args byte 0xc.
      A skill bullet (`BulletBehavior` -> FUN_1403c2d60) uses slot Args byte 0x10, which is the low
      byte of Args[4], with priority Args byte 0x12. A cast bullet (FUN_1403c1450) uses slot 0
      (`xor edx,edx` at 0x1403c14fd). A bullet carries the record from spawn data +0xa0
      (FUN_1403c10b0) through `BulletAttackInfo` +0x640 (FUN_14038e540) to bullet +0xaf0, and its
      attack takes it at +0x288 (FUN_1405271a0 in FUN_1403960a0). A swing sets +0x288 through
      FUN_140521970, and only while it is empty. The priority byte travels the same way into the
      attack's +0x155. The debug overlay prints it as "damage priority", and it is copied into
      every contact at +0x34 (FUN_140521c80).
    - *What a hit does.* Each frame, DmgMan (FUN_140527c10) first registers every attack's contacts
      in its record (FUN_140521bf0 -> FUN_14051b210). FUN_140521bf0 asks only that the attack
      holds a record (+0x288), that the contact's priority is not negative, and that the victim
      is a `Chr`: no team test, no damage test (bd er-effects-rs-xb3i). Per victim the record
      keeps the first registrant at the lowest priority and that attack's id; a strictly lower
      one replaces them and clears the landed flag (`jge` at 0x14051b304). Then DmgMan resolves
      the contacts in the same list order (FUN_140522590 -> FUN_140523710 -> FUN_14051b870). The
      entry's own id passes even after the flag is set (0x14051b8c9, when the id is not -1); any
      other attack passes only while the flag is clear and its priority equals the entry's. A
      pass sets the flag (FUN_14051b370, 0x140523aac) before FUN_14051af50 applies the hit
      (0x140523ab6), so a hit that the team gate (FUN_1404443e0), immunity or a zero-damage row
      then turns into nothing still spends the record. A refused attack marks the victim in its
      own lists (+0x278, and the bullet-shared +0x280; FUN_140524910 -> FUN_140523540, for its
      linked shapes too), so it never hits him later.
    - *Ids.* The id is attack +0x148 (params +0x110). A swing's is -1: FUN_14051d380's default,
      which nothing on the FUN_1404428f0 -> FUN_140526430 path writes, so a swing never passes as
      the entry's own. A bullet's is its launch: FUN_140d245a0 writes bullet +0xb08
      (`BulletAttackInfo` +0x658), which FUN_14038fe80 sets to the launch entry's id when it is
      -1 (a root: FUN_14038b9a0 stores -1) and which children keep. So a launch's `numShoot`
      copies and all its descendants are one attack to the record: once one of them holds the
      entry, every other lands through its own lists.
    - *Ties.* Registration and resolution walk DmgMan's list in one order, so of one frame's
      equal-priority contacts the first in the list lands. FUN_140525ef0 head-inserts a new
      attack and every FUN_140527d40 pass, which queues the shape casts FUN_140527c10 resolves,
      rebuilds the list by head-inserting as it walks, reversing it: of attacks created between
      two passes the one created first comes first (`INFERRED` only in that nothing else reorders
      the list). For attacks created on different frames the order flips every pass. Events of
      one frame are created in event-group order (GetActiveAnimEvent 0x14264e420), which is the
      stored order for 3248 of 3249 same-frame pairs of type 1, 2 and 64 events (`MEASURED`).
      So the attacks of one record land one hit per victim (a launch: all its rows), plus one
      more for each later contact with a strictly lower priority. The bullet-shared record at
      +0x280 that attacks.md names is a different one: the
      bullet's +0xae8 list, which it inherits from spawn data +0x98 or creates as
      "share|bulletId" when `isUseSharedHitList` or `attachEffectType` is set (FUN_14038fe80).
    - *Swift Slash (a877 040000).* The four bursts take slot 1's record (event 341 f27-34). Both
      blades (indices 0 and 1) take slot 0's (f28-38). Every priority is 0. So one burst and one
      blade reach a defender, where the model credits four bursts and two blades. Bursts and
      blades never share a record: a swing cannot use slot 1.
    - *Order.* Thread one runs in the `HavokBehavior` task (group 0x4a, through
      `RunTaeAndUpdateAnimQueue`). Thread two runs in `ChrIns_BehaviorSafe` (0x4c), whose first
      call is `RunTaeMulthreadThread2`, and no `WorldChrMan` task sits between them. This holds
      while `Behavior.EnableMultiThread` is on; `CSHkBehManager` reads it with default true.
    - *Cleanup.* The slot's per-frame cleanup FUN_1404a4cb0 (slot vtable +0x20) is called by
      `ChrSlotSys::Update` (0x1403f7ca0, which calls each attached slot's +0x20), from
      `ChrIns::PrePhysicsSafe1` (0x1404021a0) in task group 81 (`ChrIns_PrePhysicsSafe`): after
      both TAE passes (74, 76) and before DmgMan (120 `DmgMan_Pre`, 121 `DmgMan_ShapeCast`, 122
      `DmgMan_Post`), in the step table at 0x142c030e0. Read through Unicorn emulation of the
      Arxan stubs. The record is reference-counted, so the attacks that took it keep it after
      the slot drops its entry. Not read: the scheduler loop that walks the step table, and
      whether a debug property (`game.properties`, none in the install) turns the multithread
      flag off.
    - *Children.* A bullet's hit, expiry and interval children (`KillBullet`, FUN_14039ab10,
      FUN_14039baf0 -> FUN_14039bba0) copy the parent's `BulletAttackInfo` (FUN_14038b740), its
      record (+0x640), priority byte (+0x648) and launch id (+0x658) with it; FUN_14038d130
      copies them into the new bullet (+0xaf0 / +0xaf8 / +0xb08). The child's attack takes the
      byte at +0xaf8 as its priority (FUN_1403960a0 passes it to FUN_140526230 -> FUN_140d245a0
      -> FUN_140d24b10, `*(params+0x11d) = *prio`); nothing in the child's own Bullet or AtkParam
      row overrides it. Only the shared list (+0x638) is dropped, and only when the parent's row
      has `attachEffectType`: FUN_14039bba0 reads it on the row of the parent's current state
      (+0x4a4, which `KillBullet` never writes, so on the expiry path it is still the fly or wait
      state, whose row FUN_14039ee00 sets to the parent's own; bd er-effects-rs-7bye). Prelate's Charge (a613 040000, priority 2): the carrier Bullet 2311
      (AtkParam_Pc row 0, nothing to deal) and its fire 2312 are one launch, so a carrier that
      touches the defender first does not keep the fire off him; the f36 swing (priority 0) or
      the f39 bullet (1) landing first does.
    - *Casts.* Event 64's request +5 is Args byte 0xc (`movsx eax, byte [rbx+0xc]`, stored at
      0x140429f2b); its slot is 0. Of 1028 cast events in the spell TAEs, 835 carry -1, 193 carry
      0-2; 74 start inside a slot-0 window, in 33 animations with two or more (Meteorite and
      Meteorite of Astel 045010: f6 at 0 and f11 at 1 share one record, f15 is outside).
    - *What the model does.* Each skill hit row carries its record (`record`: (animation, record
      index)), `priority` and `record_s` (`anim_actions`, `skill_hits`); a bullet event's root and
      child rows share its record and its `created`, which stands for the launch id.
      `skill_option`'s `dmg` counts the rows that land when every row touches (`record_mask`);
      `skill_landing` skips a hit whose record another launch holds at an equal or lower
      priority (`er-mechanics-attacks.record_contact`); `CaughtModel` gives each geometry its
      record (`HPR`, whose third field is the launch), and a later geometry of a record another
      launch holds lands only with a strictly lower priority, while the holding launch's
      geometries land and tick on. A shot that holds a record keeps all its copies and lists in
      its `CAUGHT` record (until 2026-11-10 one, the root's: `one_record_list`, removed with bd
      er-effects-rs-xb3i). One frame's tie goes to the attack created first. Swift Slash on a
      Backhand Blade: one burst and one blade land (200 of 600 with every hit at 100); Stormcaller:
      each swing (priority 0) reaches him before or with its bullet (1), which lands nothing.
      Savage Claws and Thundercloud Form carry priority -1 or no event 341 and keep every hit.
  - **The lock-on target of a player's shot (VERIFIED, 1.16.2).** FUN_14038e740 takes the shot's
    target from the owner's manipulator (vtable +0xa0); for a player (`PadManipulator`, vtable
    0x142a2b778) that is FUN_140394f90: target kind 1, the `LockTgtMan` entry. For kind 1 with an
    entry FUN_140394910 aims at the entry's own stored position; where the lock-on manager writes it
    was not read (the struct's offset at creation, FUN_140395590, is the target's dummy 220-227, or
    0.75 x its hit height up when it has none). A player's hit height is 1.5 m (`InitForPlayer`
    0x140460050 writes 1.5 to the init data's `hitHeight` and `chrHitHeight`, radius 0.4). The 0.8 x
    hit height of kind 5 is a follow target's, not the lock-on's. So the vertical part of the turn is
    still not modelled, and no height is assumed for it.
  - **The defender's body.** The caster's root gets no nearer than `dist - 2 x 0.4 m` (the reach
    model's push-capsule rule, `front_contact_times`), so a shot leaves where the held-back caster
    is: each bullet sample carries the clip's peak forward root motion up to the launch and the
    launch time, and `_catches` takes off `max(0, peak - (d - 0.8))` with `d` where the defender
    stood when the shot left. Swift Slash at 1.5 m: the dash would launch the f33 burst 5.3 m out,
    past him; held back at 0.7 m it catches him (selftest).
  - **The world (INFERRED).** The ground is flat, a bullet hits it when falling at a height of
    `hitRadius` or less, and no character or target is hit on the way (with no target there is
    no homing turn either: FUN_140398270 needs one). The reach is how far the skill carries when
    it misses.
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

### 15c. Stormcaller belongs on long weapons

User, 2026-10-13: "We have stormcaller on small weapons. I need you to investigate and record
evidence that it should be prioritized on longer weapons, and deprioritized/ranked on smaller
weapons."

**What the weapon contributes (TAE + regulation, `VERIFIED`).** `a623_040000` fires three melee
events (TAE 1, judges 3640/3641/3642, f24/f47/f72) and three bullet events (TAE 2, judges
3643/3644/3645, f26/f49/f74).

- The swings are AtkParam_Pc 300000640/641/642, `hitSourceType` 0 (weapon). Each has three
  capsules on the weapon's own dummies: 100-130 at radius 0.35 (the tip), 130-120 at 0.2, and
  110-120 at 0.1. Their reach is therefore the model's length.
- The bullets launch from dummy 280, which exists only on the body model c0000. It sits at
  (0, 0.0005, -0.034) with no attach bone, so it is the character's root, the same on every
  weapon. Their AtkParam 300000643 has no hit shapes of its own.

Swing extent from the grip, the capsule 100-130 plus its radius (`er-mechanics-reach.shape_extent`
on the weapon's FLVER dummies, `MEASURED`):

| weapon | swing extent (m) | weapon | swing extent (m) |
|---|---|---|---|
| Dagger | 0.71 | Bastard Sword | 1.47 |
| Short Sword | 0.99 | Claymore | 1.54 |
| Iron Cleaver | 1.08 | Grave Scythe | 1.97 |
| Warped Axe | 1.09 | Zweihander | 2.05 |
| Broadsword | 1.12 | Greatsword | 2.10 |
| Uchigatana | 1.46 | Halberd | 2.15 |

**What lands (section 15a model, `er-mechanics-ashes.py landing`).** The test used Heavy +25 and
the corpus poises of RL 140-160, with the defender at 1.5, 2.0, 2.5 and 3.0 m. The first swing
touches at 1.5 and 2.0 m only on the Dagger and Short Sword. It also touches at 2.5 m from about
1.08 m of extent (Iron Cleaver, Warped Axe, Broadsword, Uchigatana, Bastard Sword), and at 3.0 m
from 1.54 m (Claymore, Zweihander, Greatsword, Halberd, Grave Scythe). The bullets touch at every
distance on every weapon.

Over all 294 Stormcaller carriers of the RL 150 Heavy Knight bleed build's sweeps
(`er-mechanics-ash-landing.py`, STR 51 / DEX 17 / ARC 41), the landed share of the cast's damage
has a median of 0.52 and runs from 0.30 to 0.65:

| landed share | carriers |
|---|---|
| 0.30-0.34 | Curved Club, Chainlink Flail, Hand Axe |
| 0.42-0.49 | Short Sword, Iron Cleaver, Pike, Grave Scythe |
| 0.52-0.57 | Broadsword, Uchigatana, Gargoyle's Twinblade, Warped Axe, Great Mace, Claymore |
| 0.60-0.65 | Bastard Sword, Halberd, Banished Knight's Halberd, Nightrider Glaive, Vulgar Militia Shotel |

The share is not monotonic in length, because the 15a lock rule also decides it: a long swing
that lands early can still be escaped before a later one. Pike and Grave Scythe land 2 of 4 hits
at 2.5 and 3.0 m in spite of their reach.

**Why the ranking missed it.**

- The ash table credits all six hits on every carrier; it never calls `skill_landing`.
- The loadout's `area_w` (`set_area_weights`) used the union of the bullets' ground and the
  blade's sweep. The bullets' part is the same on every weapon and dominates the union. On the
  same pool, Blood 1H carriers ranged only from 0.84 (Chainlink Flail) to 1.26 (Pike).
- `reach_w` reads Stormcaller's reach as the bullets' 3.1 m on every carrier. Its first term,
  ln(ash reach / R1 reach), is positive only where the R1 is shorter than 3.1 m. So it gave the
  short weapons a small bonus and the long ones none.

**Change.** `er-mechanics-combo-loadout.py` now takes `--landing-cache` (`set_landing_weights`,
step 8b of `er-mechanics-build-pipeline.sh`). It sets `land_w` to the carrier's landed share over
the ash's median landed share, which runs from x0.58 (Curved Club) to x1.25 (Halberd, Nightrider
Glaive). Dividing by the median moves the carriers against each other and leaves Stormcaller's
standing against other ashes unchanged.

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
  him, so an early roll loses to a long or late live window. For a bullet this is `VERIFIED`
  (1.16.2 named dump, 2026-11-10): a bullet's contact with a character goes through
  FUN_14038b2f0 (from FUN_14051af50 -> FUN_14051acc0), which returns before the bullet's own
  character-hit handler FUN_140398ba0 when the damage gate FUN_1404443e0 refuses the target, and
  that gate refuses one whose `IsImmuneToAttack` holds (the roll's JumpTable 8, combo.md section
  12). So a shot touching him during his i-frames is not killed, makes no `HitBulletID` child
  (FUN_14039dcc0 is not reached), and flies on, homing included. Whether its hit list records him,
  which would stop it touching him again after the i-frames, is not traced.
- **Which direction he rolls (version 22, 2026-11-16).** Knowing the attack, he picks the roll
  direction that leaves the attacker least, the same way he picks the jump over the roll
  (`dodge_families`: each direction its own family in `sequence_outcome`, `volley_outcome`,
  `FollowupModel`, attack-similarity's `OutcomeForm`, the spell kit's `dynamic_outcome`, and
  `reaction_outcome` when no family is named). The directions are the lock-on's, relative to him
  facing the attacker (`roll_motion`). His lookalike group still times the press within a family
  (one `lookalike_plan` per family), as it already did for the roll and the jump: the family is
  chosen per attack, the timing per group. Until then the four directions were pressed a quarter
  each (`INFERRED`). Divine Beast Frost Stomp on the Claymore, whose side rolls escape and whose
  forward and back rolls do not (**MEASURED** 2026-11-16, `sequence_outcome`, no caught model):
  `p_evade` 0.750 -> 1.000 at 2 m, 0.393 -> 0.857 at 4 m (floor 30); `p_punish` at 2 m, floor 0,
  0.077 -> 0.798 with the advance below. Section 14's `dodge_value` (the exchange model's rating of
  a dodge skill against the roll) still uses the four directions equally.
- **When he rolls (INFERRED constants).** He cannot mean to press before the cue plus his reaction:
  lognormal, median `REACTION_MEDIAN_S` 0.25 s, log sd 0.2, plus two network legs of
  `NETWORK_ONE_WAY_S` 0.05 s (he sees the attack late, and his roll reaches the attacker's machine
  late; the attacker's machine decides the damage against its replica of him, `VERIFIED` combo.md
  section 12). Every press lands off the frame he means it by a normal timing error
  `DODGE_TIMING_SD_S` 0.05 s, a press meant at his reaction included (version 28, `pressed`; until
  then an early error was clamped to the reaction, so a press at once carried none: "Every press
  carries his timing error" below), and a press before his recovery's floor comes out at the floor.
  He aims (`dodge_presses`) where his presses are worth most to him (version 25, `_AimTable`):
  one for each that escapes, plus, on the burst that decides his punish (the attack's last that
  reaches him standing, `last_reaching`), the share of the pool's strikes that punish from it
  (`PressGain`, the same count `reaction_outcome` makes). He weighs a hit he lands as one he avoids
  (`INFERRED`); the attacker's worth is not his to weigh, which keeps one plan per geometry for
  every build. The choice is exact over every aim: between the aims where a press enters an
  escaping run, moving later only loses him escapes or punish, so those entries and "at once" are
  the aims tried (the selftest checks it against a 0.01-frame brute force). A lookalike group's
  plan aims and picks its decision time by the same measure, each member's punish from its own
  roll frame (`lookalike_plan` `gains`, `Lookalikes` `rolls`), and a follow-up's opening roll is
  aimed for what it leaves him against the follow-up pressed at once (`FollowupModel._open_vals`).
  Until version 25 he aimed at the middle of the escaping run he was likeliest to hit, whatever
  his punish, so a defender who could not tell the Glintstone Dart from its release punished it
  more (0.5000) than one who knew it (0.3294; **MEASURED**, the PvP ranking's selftest), and knowing
  an attack could hurt him. Median delay: 10.5 real frames. The runs are read off the attack's own geometry at the true distance, so
  he knows exactly when every shot reaches him (`VERIFIED` reading of `dodge_presses` /
  `reaction_outcome`), and the error is integrated over `TIMING_POINTS` 7 quantiles, which reach
  +-1.47 sd (+-2.2 real frames): a run wider than about 4.5 frames is a certain escape.
  What that does to a single fast bolt read from the cast's start (`MEASURED` 2026-11-10, the
  spell kit's `net_parts` at RL 150, the Intelligence line {str 16, dex 18, int 65, fth 9, arc 9},
  Glintstone Staff +25 and the Glintstone Kris at its top level, all 1,208 punish situations): Glintstone
  Cometshard's bolt (18 m/s rising to 25, `REGULATION` Bullet 10402000) is made at f38.7 (tap) or
  f60.7 (charged) and reaches a standing defender 2.4 to 15 frames later; the Glintstone Dart's
  (Bullet 2590 / 2591, 50 / 40 m/s) at f57 (full charge) or f33 (release at f13). Every roll
  direction keeps an escaping run of at least 8 real frames up to that arrival (at 3 m, Cometshard's
  tap: forward f26-41, back f33-41; the Dart's tap: forward f20.5-33.5, back f24-33.5), and his
  presses fall inside it, so he evades them in every situation (`p_evade` 1.0; the Dart's tap 0.996,
  where the whiff's recovery holds him past the run). The recovery floor never binds otherwise
  (situations' recovery: median 13 frames, 99% under 28, at most 41). The punish he lands after is
  near nothing because the caster's roll gate (f50.7 tap, f75.7 charged) comes first: mean net
  Cometshard tap 0.0, charged -0.55 (-14.7 since the aim below), the Dart -0.6, its tap 2.08. The
  zero comes from this defender, not from the flight. The punish model (`net_parts` ->
  `sequence_outcome`) reads every attack from cue 0, not by the cue rule below, which the PvP
  ranking applies (its follow-up model likewise plans for the earliest press), and the cue is what
  moves the zero, an `INFERRED` choice: read from the hand-off (f23.7 at DEX 18, where the tap and
  the charge part, both having played 045000 to there) the tap nets 39.4 (`p_evade` 0.92) and the
  charge still 0.0; read from the bolt's creation both land their whole worth (495.9, 648.3); the
  Dart read from its release (f13) or from the end of its release window (f26, full charge) still
  nets 2.1 and 0.0. Neither a 0.05 s spread with full tails (61 points: Cometshard's tap 0.0, the
  Dart's tap 2.12) nor the aim below moves them. What he can tell apart, and so when he reads the
  cast, is now taken from what is on screen (next bullet).
- **What is on screen (spell kit and its rivals, 2026-11-10).** The punish model's defender no longer
  knows the attack from its first frame; he knows what he can see (`er-mechanics-spells`
  `Lookalikes`, `er-mechanics-ashes.lookalike_plan`; the comment above `er-mechanics-ashes._escape`).
  Two spells whose `Magic.refType` names one TimeAct play the same clips (`TAE`), and with one
  `castSfxId` the same casting SFX (`REGULATION`: Glintstone Cometshard, Comet, Shard Spiral and
  Night Comet are all refType 2, castSfxId 523060, 523070, 523070 and 523200; that equal ids look
  alike and a different id is told apart from the first frame is `INFERRED`, only the ids are read).
  A tap and its charge hand off together and play 045010 alike up to the tap's release (`VERIFIED`
  c0000.hks: `MagicLaunchRight_Upper_onUpdate` leaves 045000 on the JumpTable 93 opening with no
  input test; `MagicFireRight_Upper_onUpdate` plays 045011 on the first frame of 045010's SpEffect
  100610 window with the cast button up; bd spell-tap-vs-charge-speffect-100610-in-045010), and a
  charged skill's hold and its early release play the opening alike up to the release
  (`SwordArtsOneShot_onUpdate`: L2 up inside SpEffect 100285; bd
  skill-tap-vs-hold-speffect-100285-l2-up). So the casts of one (refType, castSfxId) among the
  spells the caster can cast and a player can hold are one group (`spell_lookalikes`), each pair
  parting at the first frame what he sees differs (`look_split`: one switches clip and the other
  does not, or a cast event fires a different ref, the shot's spawn); a charged skill's hold and
  release are one group parting at the release (`skill_lookalikes`). Comet and Shard Spiral, tap and
  charged, are one group of four: at DEX 18 a tap and a charge part at f27.7 (the release, at
  045010's first frame, f23.7, showing 4 frames later), the two taps at f38.7 and the two charges at
  f60.7, their shots' spawns.
  Until a split he either times one roll against every member still alike (`group_presses`: the
  stretch of starts whose weighted chance of a press escaping each member is largest; members
  weighted equally, `INFERRED`: the model has no source of how often each is cast) or waits for the
  split and reacts from it with his usual reaction delay; deciding at a split he knows the members
  still alike then, so in the group of four "wait for the release, then one timing against both
  taps" is an option, and the one he takes on 99.3% of the weight (the rest waits for the spawn).
  He takes the decision time that escapes the group most often in the situation, averaged over the
  roll directions as the outcome averages them; only his first dodge is planned so, the bursts after it
  coming after the split. A cast alone in its group, and an attack with no tap or hold, are read
  exactly as before (selftest: Swift Glintstone Shard, refType 79, castSfxId 522000). He chooses on
  escape alone, not on what lands, so less information can leave the caster less where every
  timing is caught anyway and the plan's timing is caught by a smaller hit (Midra's Flame of
  Frenzy's tap: 194.8 read from its start, 86.7 seen only, both `p_evade` 0). The release frame is
  the caster's choice and he cannot see it coming (2026-11-11): a release is one member of the group
  per real frame of its window (`release_frames` in `er-mechanics-ashes`: [first, last), each
  weighed alike, `INFERRED` uniform), the release's weight split over them; each parts from the
  hold or charge where its release shows and from a later release where the earlier one shows, and
  the release nets the members' weighted mean (the kit's `punish_values`, attack-similarity's
  `mix_arrays`). A release shows on the first frame after its switch at which the blend into the
  release clip (the TAE type-16 end as its blend time, `VERIFIED` through
  `CustomTransitionEffect`, bd player-blend-time-is-tae-type16-end-2026-11-11) puts a bone
  `VISIBLE_M` 0.10 m from where the held clip has it (`release_visible`, `INFERRED` threshold:
  attack-similarity.md section 2c gives 5 and 20 cm); a spell's shots still part two casts where
  they differ, at their spawn. Every member is in the group; the release's own nets are read at
  the window's ends, every `RELEASE_STEP` 5th frame and, bisecting, wherever two read frames' mean
  nets differ by more than `RELEASE_TOL` 4 HP, and filled linearly between (`release_read`,
  `release_fill`): within 3% or 5 HP of reading every frame on the names measured, where no fixed
  grid was (attack-similarity.md section 2c). That is the kit's reading, over its few situations;
  attack-similarity, over 1,208 situations and every attacker build, holds only nodes of the window
  in the group (its ends, every 5th frame and every frame where two of them differ by over 1 HP,
  `release_nodes`, `release_fill_in`), within 0.2 HP of every frame on the 20 entities measured. A window of one real frame is the earliest release
  alone, as before, number for number (selftests). A hold spell whose 045010 has the window is let
  go early too, its 'early' variant
  (`er-mechanics-spells` module doc, "One cast": the breaths, Crystal Barrage, Gelmir's Fury). The
  members' mean, not the caster's best frame, is the rule (`INFERRED`): the defender weighs the
  members as the caster mixes them, and a best frame would be one his plan does not expect.
  Attack-similarity keeps a charged skill's release with an opening hit before it (Black Flame
  Tornado, Jori's Inquisition, Moore's Charge, Shield Crash, Thundercloud Form, Wall of Sparks) a
  press the caster delays only on a roll he saw before the window opened (`FollowupForm`), since
  there the first burst is the opening's, the same for every member; its release frames still sit
  in its hold's group. Numbers: the table under this list, and attack-similarity.md section 2c.
  Attack-similarity reads every profile and every build's net the same way, the group taken per
  line (attack-similarity.md section 2b). The PvP ranking's waiting defender (`skill_reaction`)
  reads a charged skill cast as it opens, the hold, against its early release let go on each
  release frame of its window (`skill_release_part` 'releases', `lookalike_pair`); its other
  attacks, which have no lookalike there, are read as before. The survey below is the release at
  its first frame alone (the 2026-11-10 rule); spread over the window the hold is told apart more
  often, since a hold not yet released is likelier the longer it goes (selftest: Revenger's Blade
  [Falx], escape 0.50 at the first frame alone, 1.00 over its 22 release frames; the Glintstone
  Dart's hold, escaped every time either way, is punished 0.006 at the first frame alone and 0
  over its 13, as he waits for the last release before rolling it; the Dart's release at f13
  shows 3 frames after its switch, as the 2026-11-11 survey has it). On each skill's own weapon (an ash on the first weapon that mounts it), every
  damage stat 99, 100 HP a hit, R1 strikes at 15 and 20 frames, the landing's distances
  (`MEASURED` 2026-11-10: 28 openings with the window, 27 read, Moore's Charge with no reaction to
  read; 10 move): the hold's escape falls on Revenger's Blade [Falx] (1.00 -> 0.50, land share
  0.50 -> 0.75), Flame Spear [Dagger] (1.00 -> 0.63, 0.50 -> 0.65), Divine Beast Frost Stomp
  [Dagger] (0.75 -> 0.50, 0.63 -> 0.75), Dragonwound Slash (1.00 -> 0.90) and Torch Attack
  (1.00 -> 0.97); Black Flame Tornado, Thundercloud Form and Shield Crash keep their escape and are
  caught by a larger hit (land 0.625 -> 0.669, 0.670 and 0.626); the Glintstone Dart and Flower
  Dragonbolt are still escaped every time but rolled later, so punished less (0.12 -> 0.006, 0.016
  -> 0); the other 17 do not move.
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
  With `run` (the spell kit, `er-mechanics-spells`, whose caster stays at range), a dodger who ends
  further away runs the rest in at run speed first (`frames_per_metre`, as `dodge_value` does) and
  punishes when that still beats the caster's roll. He runs no sooner than the roll's move cancel
  (f22, against the R1 cancel f20, **MEASURED** from TAE a000 027110-027113 in all four
  directions), from where the roll has put him then: the behavior script starts a walk or a run
  only through the move-cancel check (env 2000, **VERIFIED**). A dodge that carries him into reach
  between the two presses on that frame (Bloodhound's Step: R1 f17, move f29). `punish_lead`;
  forward at 9 m the press moves from f35.59 to f36.91, back at 3.5 m from f45.78 to f48.60.
- **Where the attacker ends (version 22, 2026-11-16).** One rule for every punish path
  (`attack_advance`, `advance_at`): the attacker's forward root motion is the largest of his hit
  samples' `PK` and of the whole clip's (`ADV`, `clip_advance`: every 1/60 s of the HKX clip,
  carried by `_hit_contacts` and `SpellModel.slot_attack` geometries), ending where the last
  clip ends, and the defender's body stops it 2 x `PUSH_CAPSULE_RADIUS` short of where he is on
  the attacker's axis, taken off for good, as every hit sample is held back (`_held`,
  `er-mechanics-reach.front_contact_times`). `reaction_outcome`, `sequence_outcome`,
  `volley_outcome` (`_volley_burst`), `FollowupModel` (`_punish_arrivals`), the spell kit's
  `dynamic_outcome` and attack-similarity's `_reaction_form` all read it; until then only
  `skill_reaction` and `slot_reaction` passed an advance, the largest `PK` unstopped, which put
  Savage Claws (`ADV` 9.06 m, `PK` 8.93 m, **MEASURED**) past a defender 2 m away; the others read
  0. The defender's strike (below) runs in short of the attacker's travel by his first touch
  (`StrikeReach.advance`: the `PK` of the samples up to it, the most he can have come by the
  strike's landing, `INFERRED`). Not read: the travel of a follow-up's opening clip under its
  follow-up's (each clip is measured from its own first frame, as `skill_reach` measures it), a
  defender rolled aside does not stop the attacker (the hold-back reads the axis only, as the hits'
  does), and a cast's root motion after its last launch (its geometries are bullets).
- **Bursts and the jump (spell kit only).** `sequence_outcome` dodges an attack whose hits come in
  separate bursts one burst at a time, and adds the jump in place (`jump_motion`) as a further
  family of moves when a burst's AtkParams all carry `isInvalidatedByNoDamageInAir` (moveset.md
  section 6a): the jump clip is immune to them from frame 0 to frame 25. The defender keeps to the
  family that leaves the attacker least; since version 22 each roll direction is a family of its
  own (above). Skills are not scored this way yet, so a shockwave skill
  with that flag (Earthshaker, Hoarfrost Stomp) is still taken to be rolled.
- **The strike instead of the roll (spell kit and attack similarity, 2026-11-13).** Until then the
  only answer to an attack was the roll (or the jump), so a free defender 1.5 m from a 55-frame
  cast never hit the caster first and every slow cast landed as if he could only dodge it. Now,
  where he is free before the attack first touches him, he may run in and throw the pool's R1 #1
  instead (`er-mechanics-spells.net_parts`, the comment above its `STRIKE_CARRIED`): pressed no
  sooner than `dodge_presses` lets him roll (`max(cue + reaction delay, floor)`, the delay with
  its two network legs, cue 0 as every attack in the punish model), run in at run speed to
  `PUNISH_REACH_M` 3.69 m and landed the R1's strike frames later (`punish_strikes`, per build),
  when that is before the attack first touches him (standing where he is, or where his run ends)
  and before the attacker's roll gate. Whether it interrupts is the interrupt module's poise rule
  on that one hit (`er-mechanics-interrupt.simulate`, `chip_states` carried as line-fit's
  default): the R1's poise damage (the exchange pool's `poise_dealt`, `saRate` applied: 24 to
  819, median 222 at RL 140-160, `MEASURED`) against the attacker's menu poise, through the
  attack's TAE 795 window open on that frame (a spell's cast clips, `SpellModel.cast_windows`; a
  skill's clips, `SpellModel.skill_windows`; `er-mechanics-interrupt._window`, `VERIFIED` there).
  Broken, every shot launched at or after the strike is lost (a melee window always is) and the
  shots already out fly on and land on him standing; held, the attack lands whole. Either way the
  attacker pays `PUNISH_HP`; a strike too late (after the first touch or the roll gate) lands
  nothing while the attack lands whole, which is standing still: a defender whom rolling leaves
  worse off than standing now stands, as the spell kit's re-aimed casts already let him
  (`dynamic_outcome`). Knowing the attack, he strikes or rolls (or jumps),
  whichever leaves the attacker less, a tie to the roll, as he picks the roll or the jump; the
  choice is made knowing the attack even where his roll's timing is planned against lookalikes
  (`INFERRED`, `er-mechanics-spells` module doc, "Not established"). The attacker's poise is each
  attacker line's own in attack similarity (`line_poise`) and the RL window's median build's (62
  at RL 150, the defender's own) elsewhere (`INFERRED`). The PvP ranking's `skill_reaction` is
  unchanged.
- **The attacker's own guard meets the strike (spell kit and attack similarity, 2026-11-13).** An
  attack whose clips hold TAE JumpTable 3 (the guard flag a raised shield sets; which attacks,
  how they are reached and what a guarded hit does: powerstance-guard.md section 3a) has those
  windows on every geometry (`GUARD`: `SpellModel.skill_guard` for a skill, `slot_attack` for a
  weapon's own attack). A strike that lands inside one is blocked by the guard model's rules
  (`er-mechanics-spells.strike_guard`, `guard_block`: `shield_guard` of the guarding weapon at the
  window's judge, `block_hit`, PvP): the attacker pays `PUNISH_HP` times the share of the R1's raw
  damage his guard passes, not `PUNISH_HP`, the R1 breaks no poise, and the attack ends only where
  the behavior script's guard reaction ends it (every skill and plain attack state; the two
  guard-poke states go on) or the guard breaks (stamina left at or under the guarded stamina:
  chip, the attack ends, one more R1). The stamina left is the bar less the stamina the attack's
  hits have taken by the frame the strike lands, each hit's on the frame its hitbox is created
  (`FUN_1404428f0`, attacks.md section 3, `VERIFIED`; `Guard.charges`, `guard_phases`, since
  2026-11-13; before that all of it was taken before the guard opened, `INFERRED`, which the game
  does not do). A strike outside the windows is read as before, and an
  attack with no guard nets what it did, number for number (`strike_shares`' third part is 0.0).
  What it moves is in attack-similarity.md section 2e. The roll's punish (`reaction_outcome`) does
  not read the guard; on the guarded attacks measured, the punish after a roll lands before the
  attacker's roll gate for at most 1.16 HP per whiff (Roaring Bash; 0.0 on the thrusting shield's
  R1s, `MEASURED` 2026-11-13, attack-similarity.md section 2e).
- **Lock-on aim and independent threats (spell kit only).** `fly_tree(t, bt, target)` aims every
  shot at the target when it is created, root or child, when its `EmittePosType` is 0 or 2: onto
  the target within `lockShootLimitAng` degrees, else by that angle toward it, before the shoot
  angles (`VERIFIED` 2026-11-09, 1.16.2 named dump: FUN_140390c20 -> FUN_14038f120, the cone read
  from BulletParam +0x91; a child keeps the parent's target, FUN_14039bba0; the comment above
  `AIM_EMIT_TYPES` has the chain). So an expiry chain re-aims at every link: Glintstone Nails'
  speed-0 stops (cone 90) and moving legs (13). The stops' `homingAngle` 999 does nothing, since
  the in-flight homing FUN_140398270 needs the bullet to have flown past `homingBeginDist` and a
  speed above 1e-4.
  The aim is the 3D turn onto the lock-on point, DummyPoly 220 plus `targetYOffsetRange` (round 4
  of bd er-effects-rs-7bye; `er-mechanics-ashes` "Launch frames": until then this geometry turned
  the horizontal heading only, while the spell record counts already turned the 3D frame).
  `volley_outcome`
  scores threats that land independently, once each (a fan whose shots keep their own hit lists):
  each threat is a geometry, a burst is dodged as `sequence_outcome` dodges it, and the punish is
  paid once per cast (`INFERRED`: being hit by one shot neither lands the others nor stops him
  dodging them). A chain that re-aims between damaging legs is not scored on fixed geometry: the
  leg after a rolled one, flown at a defender standing still, comes back through the spot he left.
  `fly_dynamic` flies it against his roll instead, each link aimed where he is when it is created
  (the spell kit's `CastThreats.dynamic_outcome`). Skills still fly straight, with no target.
  `sequence_outcome` and `volley_outcome` hold his next dodge back only after a burst he dodged:
  a burst that does not reach him standing is not dodged (2026-11-09; before, it held the next
  dodge back too).
- **In-flight homing (bd er-effects-rs-rw25).** `VERIFIED` 2026-11-10 on the 1.16.2 named dump
  and `eldenring-deobf.bin` (the comment above `homes` in `er-mechanics-ashes.py` has every
  address): from its second frame, once it has flown more than `homingBeginDist`, a shot whose
  `FollowType & 3` is 0 and whose speed is above 1e-4 turns its velocity toward its target's
  position that frame by at most `homingAngle` degrees per second of frame time (the rotation keeps
  its speed), until it comes within `hormingStopRange` of the aim point (the target's position plus
  `targetYOffsetRange` up), after which it flies straight for good. Every bullet starts with the
  enabling flag (CSBulletIns+0xd80 bit 3, set by the constructor and the reset, cleared only at the
  stop range). Modelled in 3D toward his lock-on point plus `targetYOffsetRange` since round 4 of
  bd er-effects-rs-7bye (before, in the horizontal plane, `INFERRED`): `bullet_flight` /
  `fly_shots` turn toward a standing target, `fly_dynamic` / `fly_members` toward one that moves,
  and a contact geometry with a damaging shot that homes (or that a homing shot creates, or a
  carrier: below) carries a `HOMING` record, so `_catches` flies those shots again against each
  dodge start. Flown again, a spell's shot is aimed at where he is on the frame it is created
  (2026-11-10; before, at where he stood): the lock-on aim reads the target's position then
  (FUN_140390c20 -> FUN_140394910, the comment above `AIM_EMIT_TYPES`; `predictionShootObserveTime`,
  which would lead him, is 0 on every bullet a player spell, skill or goods reaches, traced in that
  comment), so a roll he started before the shot
  exists moves its aim, within `lockShootLimitAng` (45 on both). A weapon skill's shot flown again
  is aimed the same way since 2026-11-13 (`er-mechanics-spells.SpellModel.skill_attack`): the same
  flight already homed it after him and killed its carriers on him, which needs him in its target
  slot, and a shot with a target is aimed at creation (FUN_140390c20). Left on its launch heading,
  the Glintblade Phalanx ash landed twice the spell's run-in share from the same blades. A roll timed through the shot
  still clears it; a roll that is over before the shot exists no longer does unless it took him
  outside the cone; a turn fast enough for the remaining flight lands after the invincibility
  (spells selftest, INT 65, 8 m, half-frame sideways-roll starts: Gravity Well's tap 109 clear it
  aimed where he stood, 26 flown again, every new catch after the 13 i-frames; Glintstone
  Cometshard's tap 95 in each direction aimed where he stood, 52 and 43 flown again, every start
  newly caught in f0-25.5, so his i-frames ended before the bolt exists at f38.7, and none newly
  cleared). What the aim moves (`MEASURED` 2026-11-10, the kit's `net_parts` mean net per cast at
  RL 150, each spell on the first of the Intelligence, Faith, Hybrid and Arcane lines of
  attack-similarity.md section 1 that can cast it, Glintstone Staff +25 / Finger Seal +25; 111
  variants of the spells with a homing damaging shot, 'dynamic' casts left out as they were already
  aimed where he is, and 8 spells none of those lines casts): 36 move by more than 10%. Up: Star Shower 363.8 -> 1425.0 charged and
  405.3 -> 1215.8 tap, Glintstone Stars 326.4 -> 1150.2 and 300.5 -> 870.3, Knight's Lightning
  Spear's tap 459.8 -> 928.3, Gelmir's Fury -165.3 -> 96.2, Divine Beast Tornado charged 4.5 ->
  230.5, Glintblade Trio 201.4 -> 403.3 / 80.5 -> 169.1, Unendurable Frenzy charged -2.2 -> 172.6,
  Crystal Barrage -0.5 -> 53.8: each was escaped by a roll begun early enough to be out of the
  shots' line before they existed. Down: a few whose evasion does not change but whose sideways run
  is now cut where the early roll stops escaping, so the run he takes (`dodge_presses`: the first
  of equally sure runs, aimed at its middle) ends earlier, he rolls earlier and his punish beats the
  caster's roll (Glintstone Cometshard charged at 3 m: one sideways run f0-63 becomes f0-44.5 and
  f49-63, the run left early being the roll that ends outside the 45 degree cone; punish 17.7 ->
  102.5 there; Frenzied Burst's tap -44.5 -> -91.4, Multilayered Ring of Light charged -3.3 ->
  -28.1, Comet charged -0.6 -> -20.3). Glintstone Cometshard's tap, Gravity Well's tap, Glintblade
  Phalanx, Carian Phalanx and Glintstone Pebble do not move. A geometry with no homing shot is not
  flown again and keeps its stored aim at where he stood (not modelled). `INFERRED` / not modelled:
  `PlayerCommonParam`'s flare override (CSBulletIns+0xd81 bit 2, setter not read), and the caught
  defender's later hits (read on the flight against him standing). These measurements are from
  before round 4.
- **Map contact and free aim (bd er-effects-rs-rw25).** `VERIFIED` 2026-11-10, 1.16.2 named dump
  (the comment above `floor_bursts` in `er-mechanics-ashes.py` has the chain): a hit record goes
  through FUN_140399e50, which hands a map contact (no entity handle) to FUN_140398f90; that calls
  FUN_14039dcc0, which counts the hit only when the row's `isPenetrateMap` (+0x9b bit 0) is 0
  (FUN_1403aba90; a character contact likewise only when `isPenetrateChr` is 0), creates the
  HitBulletID child at the contact point (FUN_14039baf0 -> the launch gate FUN_14039da40 ->
  FUN_14039bba0), and the bullet is killed unless its `FollowType` is 4. So a carrier that does
  no damage itself bursts where it meets the ground: Great Oracular Bubble's bubble (10511000, no
  damage) makes its 3 m burst (10511001) there. The flat-ground contact rule (falling, at
  `hitRadius` or less) is the model's, `INFERRED`; the contact with the defender's body is flown
  since round 4 for a carrier (next item). Free aim is the user's game fact (2026-11-10: the bubble "can be free aimed directly
  at the floor"; the launch's aim angles, `IsBulletAimAngleSet` in FUN_1403fb0b0, were not traced
  to their setter): `floor_launch` pitches a spell's shot so its carrier lands 1 to 8 m ahead
  (`FLOOR_AIM_M`), with no lock-on and so no homing (`INFERRED`), wherever the damage is only on
  that child (`floor_bursts`, 45 spells), and the outcome takes the better aim per situation
  (`er-mechanics-spells.net_parts_aims`, the kit's `punish_values` and attack-similarity's
  profile alike; attack-similarity.md section 8 lists which aim each one's best net took).
  Applying it to every spell and to no weapon skill is `INFERRED`: nothing read shows a skill's
  bullet taking the aim pitch.
- **One flight model (round 4 of bd er-effects-rs-7bye, 2026-11-12).** The punish geometry flies
  the bullets the spell record counts fly (`fly_members`, one batched implementation of the rules
  "Launch frames in 3D" in `er-mechanics-ashes.py` lists, `VERIFIED` on the 1.16.2 named dump):
  the shoot angles turned in the parent's 3D frame (FUN_140390c20), `isOwnerOverrideInitAngle`,
  `sfxPostureType`, `EmittePosType` 1's ground point, the aim at creation turned in 3D within
  `lockShootLimitAng` onto the lock-on point (DummyPoly 220, 1.243 m up on the idle pose,
  FUN_140394910, plus `targetYOffsetRange`), the 3D homing onto the same point, attached bullets
  following their point (FUN_14039afc0, FUN_1403995b0), each child launched from its parent's frame
  of that moment, and the touch swept along the whole move (FUN_1405226e0). `fly_shots` is that
  flight against one defender standing still, `fly_dynamic` against one who moves, and
  `_homing_entries` against every distance, roll and start at once, each member flown as alone
  (selftest: every shot of five trees, three defenders together against each alone). Members whose
  shot reads the same (its spawn and his place over its flight) share one flight. A dodge grid from
  0 is flown rounded up to 40 starts (`HOMING_START_BUCKET`): the 3D aim makes a geometry's live
  frames, and so the callers' grids, differ by distance, and the rounding lets one flight serve many
  distances again (`_homing_batch`). A part is flown from the fraction of a step in its fire time,
  against the starts taken back by the whole steps, so parts that differ only by a whole number of
  steps share a flight.
  - **Carriers.** A bullet that does no damage of its own (no flat attack field, the spell price),
    targets opponents and stops on a character dies on its first landed touch and makes its child
    at the contact (`carrier`; the game kills every such bullet, damaging or not, FUN_1403aba90 ->
    KillBullet(4)). A damaging one flies through him in the punish flight: its first landed touch is
    the catch, and nothing after it moves the catch. A geometry under a carrier is flown again
    after each dodge, as a homing one is. Cannon of Haima's ball now blasts on a defender standing
    10 m (6 m) out and catches him 9.5 (4.5) real frames after the cast; before, it flew through
    him to the ground 21.1 m out and caught no one standing. Light of Miquella's 999 m/s seeker
    stops on him and its pillar catches him at 58.5 (its child used to be made 66 m out). Great
    Oracular Bubble's bubble is one too: aimed at a defender 4 m out it homes in, stops on him and
    its burst catches him at 82.3, sooner than the free aim at the ground (89.8), so the outcome
    now takes the aim at him where it took the floor aim for a long recovery.
  - **Random draws, as their expected layout.** The spread FUN_140390210 (`shootAngleXMaxRandom` /
    `YMaxRandom`: a pitch about the frame's right, then a turn about the pitched up, each uniform
    over the field's full width), the life (`life` + rand[0, 1] x `lifeRandomRange`, SetBulletLife)
    and the interval timers (rand[min, max] at the launch and at every refill, FUN_14039e8a0,
    FUN_14039ed30) are `VERIFIED`. The model flies the n draws one bullet makes of a row at the n
    strata of each range (`draw_quantile`), one draw at its mean, and every interval after the first
    at the mean: a modelling choice, so a fan's count and catch are those of its shots spread as the
    draws spread them on average, and a single shot's are those of its mean draw.
  - **Hit counts that moved** (`MEASURED`, hits per cast over the variant's events, every player
    spell, 13 of 306 variants): Ancient Dragons' Light Strike charged 5 -> 4, tap 4 -> 3; Aspects
    of the Crucible: Bloom 13 -> 6 both (its 26 spawns are thrown 360 degrees, 0 to 5 m: before, all
    on one point); Elden Stars 34 -> 27; Gelmir's Fury 6 -> 5 both; Land of Shadow 14 -> 13; Rain of
    Fire charged 18 -> 13, tap 18 -> 14; Roiling Magma charged 6 -> 2, tap 7 -> 2; Theodorix's
    Magma 14 -> 15. Each of the 13 flies rows with a random spread or a random life (`REGULATION`
    scan); that the draws are what moved each, and not one of the other rules this round touched
    (the exact contact disc in place of heights rounded to 1 cm, the end child's frame along its final
    velocity), is `INFERRED`. Founding Rain of Stars keeps 9: its 17 clouds now lie
    around the full turn, their stars on a ring about the caster, and the best spot (1 m) still
    lands 9, not the 25 its 0.2 s record would allow over 5 s.
  - **What it moves and costs** (`MEASURED`, attack-similarity `--only "Glintstone Stars" --only
    Comet --only "Carian Grandeur" --jobs 2`, round 3 code against round 4, with r16 running
    beside both). Mean net: Glintstone Stars tap 198.1 -> 256.0 and charged 277.5 -> 354.6 (both
    run-in only -> live: 3D aim and homing on the lock-on point bring the stars down onto him, so
    they reach a defender standing at all of 1.5-12 m, not 8 of those 22 half-metre steps); Comet
    tap 484.7 -> 486.4, charged 200.3 -> 234.8; Carian Grandeur's four unchanged (a skill flies
    with no target). Worker CPU seconds per attack set: Glintstone Stars tap 15.1 -> 294.6 (a dodge
    is read at every distance it now reaches), charged 0.9 -> 1.4; Comet tap 208.2 -> 733.2,
    charged 54.3 -> 68.3; Carian Grandeur 1.0-2.1 -> 1.2-2.6; the attacks stage 282 -> 1,102 CPU s.
  - **Left out, as nothing reaches them.** `predictionShootObserveTime`, which would lead him, is 0
    on every bullet a player reaches (21 rows set it, none reached; ashes selftest). The normal a
    `sfxPostureType` 2 child takes on a character hit is the hknp contact normal of the attack's
    cast against his body (chain in "Launch frames"); none of the 398 such children a character
    hit can make has a moving or angled bullet under it, so +Y stays.
  - **Open.** `EmittePosType` 1's ground point is still the spot nearest him, not its expected
    value (a point cannot stand for a 2D draw); the flights again take no DmgHitRecord (a carrier a
    record would let fly on dies); a weapon skill's stored samples still fly with no target (its
    flight again has him, as a spell's has); the punish flight keeps at most `BULLET_INTERVAL_CAP` interval children per bullet and
    samples touches every 0.1 m, the record counts all of them every 0.05 m.
- **A caught defender (spell kit and attack similarity, `CaughtModel`, bd er-effects-rs-doax /
  er-effects-rs-n7dd).** `sequence_outcome` used to credit a defender caught by one hit with every
  later hit and every tick of a lingering area: he never left, and every later burst reached him
  wherever it landed. Now he is played out on what section 15a already measures. He stands at the
  situation's distance when the catching hit first touches a defender standing there; one caught
  in his own dodge is played from that dodge (version 25, below). Each hit that lands applies its poise (cumulative against the defender's,
  refilled on a break) and plays its level's reaction: a locking one carries him back by
  `displacement_fn` (knockback VERIFIED, damage-clip root motion MEASURED) and holds him until its
  roll cancel (DamageCount chaining included) or its move cancel (TAE damage clips,
  `er-mechanics-frame-advantage.reaction`); level 0 neither holds nor moves him. When it lets him
  he leaves by a medium roll from the roll cancel (its invincibility included) and then at run
  speed the way it carried him, or at run speed from the move cancel, straight back or straight to
  a side (`_escapes`: the roll that ends furthest back, the two side rolls, three runs), whichever
  lands the smaller share of the attack's hits. Until 2026-11-14 he left straight back only, which
  kept him inside a lingering line laid along the attack axis while it lasted: Ghostflame
  Ignition's R1 trail (Bullet 2294, 2.8 to 19.7 m ahead, 5 s) gave a defender caught 2.6 m out 9
  of its 10 ticks (MEASURED). A later hit lands only where its own geometry touches him on
  that path, and a hit list that re-hits every `dmgHitRecordLifeTime` (the `full` count's ticks)
  lands each tick only while a sample within one update touches him. A list with its own Bullet
  rows lands when those bullets touch him (2026-11-16, `CaughtModel._live`); INFERRED: lists of one
  geometry live at the same frame start together and react as the one with the most poise; the
  instances of one list tick together; the knockback is straight back, and what is left of it at
  his escape is dropped.
  **When the catch happens (bd er-effects-rs-fse5, 2026-11-16).** A catch is priced by the
  geometry that makes it, played from that geometry's first touch on a defender standing. A bullet
  chain in one geometry (Rosus's Summons: Bullet 2872 -> 2873 -> 2874, each made by `HitBulletID`
  as the last ends, REGULATION; three 2 m bursts 7.5 real frames apart at one spot, MEASURED
  `fly_tree`) so credited a dodge that escapes burst 1 and is first touched by burst 2 or 3 with
  all three bursts. `split_chains` now makes such a geometry one geometry per list, at the whole
  one's frame and worth weights, so the burst that first touches him is the catch, in
  `reaction_outcome` and attack-similarity's `OutcomeForm` alike. A chain is every list with its
  own rows, each sample one list's, each list's last frame the next one's first; the frame two
  parts share is the later part's (as `_live` reads it). Lists live together, lists without rows
  (Stars of Ruin's homing stars) and lists with a gap between them keep the geometry whole. A
  synthetic chain in a 4 m spot (ashes selftest): the medium-roll starts f46.5-52 escape burst 1,
  are first touched by burst 2 and are credited bursts 2-3, 626.2 of 939.3 HP, where the whole
  geometry credited all of it (MEASURED). Rosus's Summons itself (Rosus' Axe, every damage stat
  99, the RL 150 defender, MEASURED 2026-11-16): of the medium-roll presses at floor 50, 4.5-5.5 m,
  64.3% escape, 10.7% are caught by burst 1 and 25.0% by burst 2, now worth 858.2 HP (bursts 2-3)
  where they were 1,287.3; the escapes are unchanged. `net_parts` over the 3,483 RL 150 situations
  moves by 0.0 in every situation (net 890.28 with the defender's strike, 1,287.3 roll only;
  pressed at once 191.14 and 245.62), so none of the outcomes those nets take, pressed at once or
  at the run-in the attacker picks (86.5% of the weight), has a press that burst 2 catches. INFERRED: the parts keep the
  whole geometry's `AIR` mark; a defender caught mid-roll is still played as if standing. The defender is the RL window's median build (`SpellModel.defender`): poise 62 at RL 150.
  Magma Breath: its ball (70100, poise 324.7) and the 12-tick pool it leaves (70101, no poise
  damage) share one record, and since 2026-11-14 each hit is priced and reacts as its own row; the
  ball staggers him and he takes 1 of the pool's ticks at 4 m, 2 at 6 m (MEASURED, Erdtree Seal at
  99/99/99, RL 150 defender). Until then the 13 were one list at the ball's row, 6,886 HP a cast,
  and he took 3-5 of them (the second stagger's DamageCount roll cancel at 10 frames beat the
  15-frame tick); now 1,216. Swift Slash: the blades break any poise and their middle
  reaction holds him through the f40-43 bursts, so a blade catch takes all six hits at 1.5-2.5 m
  (five of six for the right blade's catch at 4 m, four at 6 m); a burst (level 0) lets him go, so
  a burst catch takes that burst alone. Until version 25 a defender caught mid-roll was played as
  if standing, free to roll out at once, though his own roll's recovery would hold him to its
  dodge cancel (below, "Caught in his own dodge").
  **His whiff's recovery and his own poise (version 21, 2026-11-16).** The user: "So you're saying
  that the opponent has to swing, and wiff before your first claw attack lands, and all the other 5
  will also land?" Three changes to the caught defender:
  1. *Recovery.* A punish situation's floors (`punish_situations`) are real frames from the end of
     his whiff's last hit window, the punish's frame 0, to the whiff's dodge cancel and to its move
     cancel (or the dodge cancel, if later). `dodge_presses` already keeps his dodge behind the
     first (VERIFIED reading: `max(cue + delay, floor)`). Until now `CaughtModel` freed a flinched
     defender on the catching frame. Now a catch before a floor finds him still in that recovery. He
     rolls out no sooner than the dodge floor and runs no sooner than the move floor (env 2000,
     VERIFIED per `punish_lead`), and stands meanwhile, so the hits in between land where they
     touch him there. A level-0 flinch leaves his recovery running: `ExecAddDamage` returns FALSE
     and does not change the state (frame-advantage.md section 1, COMMUNITY). A locking reaction
     ends the recovery, because `W_DamageLv<n>` starts a damage state (same source). After that, its
     own cancels release him and the floors no longer apply. Taking whichever releases later would
     hold him in a recovery that the stagger has already ended. The floors are the ones
     `net_parts` uses, after the attacker's run-in has used them up
     (`choose`). The strike's own pricing of a catch reads none: the defender who struck has
     pressed after his floor. At RL 150 the floors are a median 13 frames, 99% under 28, at most
     41 (section 16a), and most first catches come later. So the floor holds only where a fast
     first hit catches him early.
  2. *Poise.* Each pool build's own poise: `computed.poise.altered` when the planner gives it,
     else `original` (`SpellModel.defender` `poise_pool`, the same builds whose median is
     `poise`). Both are menu units, the scale of a hit's `atkSuperArmor` x 10 x `saRate`.
     `original` is the armor's `toughnessCorrectRate` sum x 1000, and `altered` is that over the
     SpEffects' `toughnessDamageCutRate` product (`er-mechanics-defense.defender`, checked against
     the planner build by build; attacks.md section 2). On this corpus `altered` is Bull-Goat's
     Talisman (x 1.333 on 361 of 370 rows, MEASURED). The play is mixed over the pool exactly.
     A play reads its poise only through the cumulative damages it compares, so every poise in
     (the largest that did not break him, the smallest that did] plays alike, which takes one play
     per run (`CaughtModel._best`). RL 150: 162 distinct poises, median 68 (the median `original`
     is 62). 85.7% are at or under 108, Savage Claws' first claw (MEASURED).
  3. *Comparison and run-on.* A hit breaks him when the cumulative damage reaches his poise (`>=`:
     toughness at 0 or below, `FUN_140486bf0`, VERIFIED; `er-mechanics-interrupt` already reads
     it so). The model had used `>`; `skill_landing` (section 15a) now reads `>=` too. After a roll
     he runs on from its move cancel (f22), not its dodge cancel (f21), as `punish_lead` has him do.
  4. *His own health.* What one catch lands is capped at each pool build's own health:
     CalcCorrectGraph 100 at its own Vigor (`SpellModel.defender` `hp_pool`, and `pool` for poise
     and health together). Before, every defender was capped at the median Vigor's. A cap is not
     linear, so the mean of the builds' caps is not the median's cap. Inside `CaughtModel` the cap
     is taken per run of poises that play alike, with that run's builds' health
     (`CaughtModel.worth_of` `hp`), before the runs are mixed. Elsewhere it is `hp_cap`'s mean over
     `hp_pool`. The defender's strike still reads one attacker poise. That attacker is one build
     (a line's own poise, or the RL window's median build standing in for one), not a draw from the
     pool, so it stays one number.
  5. *The jump.* When a hit is `AIR`, a caught defender may jump out, as a dodger may
     (`sequence_outcome`'s families). It is the jump in place, invulnerable only to `AIR` samples
     while airborne (`_catches`' rule, now in `CaughtModel._pos`). He leaves through the roll's gate
     (INFERRED: `ExecEvasion` takes the jump with the roll and the backstep, and all three clear
     DamageCount) and runs back from the landing.
  6. *Same-frame ties.* In one hit priority record a tie goes to the lower priority, then to the
     attack created first, both in `_catches` and in `CaughtModel._play`. Until now `_catches` gave
     it to the earlier geometry in its list, so the play could land both. The `_catches` cache keys
     on the record.
  **Caught in his own dodge (version 25).** A press caught at or after its dodge's start
  (`_catches`' catch frame against the start on the update grid) is its own play: `CaughtModel`
  places him on that dodge's root motion from its start (read per real frame as `_catches` read
  it), lets the dodge run on until he escapes, and holds a roll (or jump) out until the dodge's
  dodge cancel and a run out until its move cancel, the same mechanism as the whiff's floors; a
  reaction that locks him ends the dodge as it ends a whiff's recovery (`dodge_key`, `_play`).
  `reaction_outcome` prices every such catch so (`_LazyWorth.mid`), attack-similarity's
  `OutcomeForm` keeps one play per (distance, geometry, move, start, catch frame) and prices it per
  build, and `FollowupModel` gives each catch in his roll of the opening, by the opening or by the
  follow-up, a column of its own (`_mid`, priced by `mid_worth`). A follow-up catch made after every
  opening sample is over is the earliest press's play with the dodge started that many frames
  earlier, so one column serves every delay (exact: nothing of the opening can touch him after it).
  Selftest: three flinching hits at f30, f42 and f45; caught at f42 in a back roll from f25 he takes
  the f45 hit too (200 against the standing play's 100), caught at f30 in a roll from f16 his roll
  out covers the rest (100 against 200). Cost (**MEASURED** 2026-11-16, the spell kit's `net_parts`
  over the 3,483 RL 150 situations, one core, the plays off then on in one process, so the second
  read the first's cached touches): Savage Claws 30.0 s -> 31.2 s and no net moved (mean 410.70):
  its first burst is escaped whole and every later burst catches him standing, before his floor
  (the burst's end plus his roll's recovery) lets him press. Version 25 left two things as they
  were, both done in version 26 (below, "The roll he escaped with"): each burst placed him back at
  the distance with the next burst's floor at the burst's end plus his roll's recovery, and a
  follow-up stage's catch made while an opening sample could still touch him was weighed on its
  geometry. A follow-up stage's dodges caught in the dodge (`_stage`, and `_late`'s planned dodge
  that a late press catches) are plays: when the opening is over by the catch the earliest press's
  at every delay, else that delay's own (`_mid` 'follow', the carried move's track moved to that
  delay's clock with it).
- **What one cast can land** is capped at the defender's health: CalcCorrectGraph row 100 at the
  RL window's median Vigor (60 at RL 150: 1900 HP). The punish he lands is not capped. In
  `volley_outcome` the cap is exact when the threats' worths sum to no more than his health and a
  bound otherwise.
- **Feints and delays.** Only the evidence above is used: the R2 charge hold, the sword-art
  follow-up windows, a charged skill's release window (SpEffect 100285) and a spell's tap window
  (SpEffect 100610), each release spread over its window (`release_frames`, 'What is on screen'
  above). No other feint is modelled; a follow-up's press stays the attacker's reactive choice
  (`FollowupModel`), not a frame the defender cannot see coming (attack-similarity.md section 2c).
- **Not counted twice with the contest.** The exchange factor's startup and hyperarmor part, or
  the interrupt factor that replaces it for the openers it covers (`--interrupt`, interrupt.md
  section 6), also rewards a fast startup. So the two split the defenders instead of multiplying:
  in `slot_score` the hit is worth `(1 - share) x f_contest + share x reacted / unreacted`, and the
  stamina part multiplies the whole score. Without a reaction term the contest multiplies the
  whole score, as before. A jump is read from its jump input, 6 frames before the landed clip
  (`SCORE_ENTRY_FRAMES`, moveset.md 6c).

What is on screen moves (`MEASURED` 2026-11-10, the spell kit's `net_parts` mean net per cast over
the 1,208 punish situations at RL 150, each attack on the first of the Intelligence, Faith, Hybrid
and Arcane lines of attack-similarity.md section 1 that can use it, Glintstone Staff +25 / Finger
Seal +25, no free aim; skills at the weapon's top level with `priced_bullets` and `flown_only`; 200
attacks, 150 of them in a group, 49 moved by more than a tenth of their net or 1 HP). "Before" is
the old tap path read from the first frame, "tap path" the c0000.hks tap path read from the first
frame (it moves only the taps whose release plays 045010 first, `er-mechanics-spells.tap_release`),
"seen" the tap path seen as on screen; "he decides at" is the share of the weight on which he
committed at the cue, waited for the split at that frame, or was not reached pressing at once.

| attack | line | before | tap path | seen | p_evade before -> seen | he decides at |
|---|---|---|---|---|---|---|
| Glintstone Cometshard tap | Int | 0.0 | 0.0 | 39.4 | 1.00 -> 0.92 | f23.7 100% |
| Glintstone Cometshard charged | Int | -14.7 | -14.7 | 0.0 | 1.00 -> 1.00 | f23.7 100% |
| Comet tap | Int | 0.0 | 0.0 | 116.2 | 1.00 -> 0.80 | f23.7 99%, f38.7 1% |
| Comet charged | Int | -20.3 | -20.3 | 80.1 | 1.00 -> 0.89 | f23.7 99%, f38.7 1% |
| Shard Spiral tap | Int | 42.8 | 42.8 | 81.3 | 0.68 -> 0.39 | f23.7 99%, f38.7 1% |
| Shard Spiral charged | Int | 42.7 | 42.7 | 123.1 | 0.75 -> 0.28 | f23.7 99%, f38.7 1% |
| Night Comet tap | Int | 0.0 | 0.0 | 6.8 | 1.00 -> 0.98 | f23.7 100% |
| Night Comet charged | Int | -14.8 | -14.8 | 0.0 | 1.00 -> 1.00 | f23.7 100% |
| Glintstone Dart (Glintstone Kris) hold | Int | -0.6 | -0.6 | 0.0 | 1.00 -> 1.00 | f13 100% |
| Glintstone Dart (Glintstone Kris) tap | Int | 2.1 | 2.1 | 2.1 | 1.00 -> 1.00 | f13 100% |
| Unendurable Frenzy tap | Fai | -368.8 | 277.9 | 277.9 | 0.97 -> 0.00 | cue 100% |
| Midra's Flame of Frenzy tap | Fai | 8.1 | 194.8 | 86.7 | 0.89 -> 0.00 | cue 100% |
| Messmer's Orb tap | Fai | 568.2 | 410.5 | 410.5 | 0.00 -> 0.16 | cue 100% |
| Comet Azur tap | Int | -63.8 | -69.1 | -59.6 | 1.00 -> 0.50 | cue 100% |
| Crystal Torrent tap | Int | none | -272.9 | -272.9 | - -> 1.00 | cue 100% |
| Wrath of Gold charged | Fai | 191.5 | 191.5 | 812.8 | 0.71 -> 0.00 | cue 73%, unreached 27% |
| Giantsflame Take Thee charged | Fai | 0.0 | 0.0 | 481.9 | 0.14 -> 0.33 | f22.3 99% |
| The Flame of Frenzy tap | Fai | -121.2 | -121.2 | 235.2 | 0.97 -> 0.05 | f31.7 100% |
| Collapsing Stars tap | Int | 43.1 | 43.1 | 219.3 | 0.61 -> 0.43 | f27.2 86%, unreached 13% |
| Divine Beast Frost Stomp (Club) tap | Int | 4.7 | 4.7 | 176.7 | 0.97 -> 0.51 | cue 89%, f21 9% |
| Flame Spear (Dagger) tap | Int | 1.1 | 1.1 | 119.1 | 0.99 -> 0.50 | cue 72%, f20 28% |
| Glintstone Nail tap | Int | 84.1 | 84.1 | 183.6 | 0.79 -> 0.54 | f22.3 97%, cue 3% |
| Gravity Well tap | Int | 22.8 | 22.8 | 109.7 | 0.86 -> 0.55 | f27.2 100% |
| Carian Sovereignty (Dagger) hold | Int | -149.0 | -149.0 | -22.7 | 1.00 -> 1.00 | f35 80%, cue 20% |
| Frenzied Burst charged | Fai | -180.1 | -180.1 | -37.9 | 1.00 -> 1.00 | f37.3 100% |

Crystal Torrent's tap had no damaging hit before (its 045011 fires an empty ref); it now fires its
045010 beam first, which he rolls every time. The tap path moves only the taps it reaches (Rock Blaster's 570.4 and Divine
Beast Tornado's 201.8 stay, the first because its tap still lands one rock and the final 498-HP
hit, the second because its earlier 045010 cast does no damage and the bolt only comes 25 frames
later). p_evade is the chosen option's: a cast that reaches nobody pressed at once can move to a
run-in he now escapes less (Palm Blast's tap, Dryleaf Arts: 0.9 -> 9.8 with p_evade 0.15 -> 0.97,
the run-in to 1.5 m taking over).

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

**A follow-up pressed late (bd er-effects-rs-me5p; the punish model: attack similarity, through
`FollowupModel`).** User, 2026-11-10: "you can delay your followup slightly if you think your
opponent will attempt to roll a projectile from the first part". Any frame of the window starts the
follow-up: a press inside an input window and a cancel window is ready on that frame (`VERIFIED`,
attacks.md section 4, `CSChrActionRequestModule::UpdateFromManipulator`); that the behavior script
changes state the same frame, and that the TAE window's last frame is not in it, are `INFERRED`.
Every follow-up hit is clip time from the press (`skill_option` offsets the part by it), so a late
press moves its hits, its launches and the attacker's roll frame together. `sequence_outcome` cannot
show the trick: a burst the defender evaded holds his next dodge back to that burst's last live frame
plus his roll's recovery, whatever frame he rolled at (Glintblade Phalanx: the opening volley lives
f33-63.5, so a defender who rolled it met the R2 volley at f65-69 with a floor of f71.5, and it
caught him wherever it reached him). `FollowupModel`
meets the follow-up with the roll he made instead:

- he rolls the opening as `sequence_outcome` dodges it (timed on the opening alone, `INFERRED`);
  the follow-up catches him when it touches him outside that roll's invincibility before its dodge
  cancel (`_catches` on the roll's path), else he is free from the cancel and the follow-up's
  earlier samples do not count again;
- he knows the attack as pressed at once (cue 0, or what `burst_cue` reads: a search launch, a
  skill's timed press; as every attack in the model) and dodges the
  follow-up from there as `sequence_outcome` would. A late press meets that planned dodge that
  many frames early: it catches him in the same way, else he is free again and reacts to the press
  itself (the part's cue). `INFERRED`: he reads no delay ahead;
- the attacker presses at once unless the roll showed on his machine his own reaction before the
  window opened (`attacker_reactions`: `REACTION_MEDIAN_S` and its spread without the network legs,
  since `dodge_presses` already places the roll on his machine; `INFERRED` symmetry); having seen
  it, he presses at the frame that leaves him most against that roll. A defender caught by the
  opening, or who does not roll it, meets the press at once, so the delay never helps against a
  defender who stays.

The PvP ranking's `skill_term` keeps the press at the window's first frame: it reacts to each part
on its own from its own cue with no floor between them (`skill_reaction`), so a late press moves the
part's hits and its cue together and changes only the attacker's roll frame, which can only add
punish (`VERIFIED` reading of `skill_reaction`). Numbers: attack-similarity.md section 2.

**One reaction rule and one option set for the defender (version 23 of attack similarity).** The
user, 2026-11-16: "I don't know what the model is doing, I just want it to be accurate, and
logically consistent". An audit found the punish model's stages giving the defender different
options and different reaction timing for the same attack. What changed, each read from the code
(`VERIFIED` reading; the constants stay `INFERRED` as above):

- **Stand.** `CastThreats.dynamic_outcome` let him stand when rolling cost him more, and
  `FollowupModel.mixup` has a 'stand' row, but `sequence_outcome` and `volley_outcome` had no
  stand. Now all of them end in `stand_or`: he takes the attack as one who never dodges (`base`,
  the first reaching catch played out by `CaughtModel`, never punished) when that leaves the
  attacker less than his best evasion family; a tie keeps the evasion. `FollowupModel.outcome`, the
  fixed roll sequence the mixup is compared with ('fixed'), stays roll-only by its definition; the
  scored net is the mixup's, which has the stand row.
- **The cue.** `burst_cue` is the one reader of when a dodge can start: the latest of the part's
  cue and the earliest frame any geometry of the burst is readable from, each geometry readable
  from the later of its search launch (`CUE`) and the attacker's timed press that starts the
  skill (`PRESS_CUE`: `skill_commit` 'cue', a stance's button or a hand-over he times, which
  `skill_reaction` already read as part 0's cue and the punish model's `net_parts` did not).
  `SpellModel.skill_attack` puts `PRESS_CUE` on every geometry of such a skill.
  `CastThreats.dynamic_outcome` timed a re-aimed cast's first roll from frame 0 whatever its
  geometries carried; it reads `burst_cue` of the first burst that reaches him standing now. A
  follow-up's own press is not a `PRESS_CUE`: `FollowupModel` plays it (planned at once, reacted
  to when late), and an early release's timing is its `Lookalikes` group's. The strike
  (`strike_segments`) and the strafe (`strafe_escape`) still run from the attack's start: neither
  needs a timing read, only that an attack is coming (`INFERRED`).
- **The latency.** `strafe_escape` started the strafe at `attacker_reactions` (no network leg)
  after the attack's start, while a dodge starts at `reaction_delays` (two legs: he sees the
  attack one leg late and his move reaches the attacker's machine one leg late). Nothing in the
  game sets a walk apart from a roll there, so the strafe pays the dodge's delay now.
  `attacker_reactions` stays the attacker's own reaction to a roll already on his machine, as
  above. The caster's press at the end of a whiff (`er-mechanics-spells.punish_situations`) was
  checked too: he reacted to the swing when he ran back (`er-mechanics-neutral.backoff_whiffs`,
  reaction and both legs), so the press is a scheduled one, as the defender's dodge is once he
  has read the attack, and the whiff's end and the whiffer's roll each reach the caster's machine
  one leg late, so those legs cancel. It is left as it was; what it lacks against the defender's
  scheduled dodge is the `DODGE_TIMING_SD_S` timing error (`INFERRED`).
- **The stand row sees him leave (version 25).** In the roll rows the attacker delays on a roll
  he saw before the window opened; a caught defender's escape out of his hit reaction is a move
  the attacker sees too. `CaughtModel.escape_table` now says, per pool poise and escape, when that
  escape starts (`_play` 'te'), its score and what lands, for each press delay; the stand row
  (`FollowupModel._stand_values`) presses at the column's delay unless the escape showed his
  reaction before then, else at the delay from then on that lands most against it, and per poise
  the defender takes the escape whose score is least so pressed. The strafe row's caught share
  reads the same. Until then the stand row pressed at the column's delay whatever he did.
  Selftest: an opening flinch at f20 held to f40, a follow-up 1 frame after a press at f43; the
  stand row at columns 3-8 rises from 100 to 111.1-188.9 and is worth_of exactly where no
  reaction comes in time. Since version 26 the roll rows' presses the opening caught meet the same
  rule (below).
- **The roll he escaped with (version 26).** User, 2026-11-16: "I don't know what the model is
  doing, I just want it to be accurate, and logically consistent". Four places where one stage of
  the outcome model honored a constraint another ignored, each read in the code first:
  1. *The next burst met on his roll.* `sequence_outcome`, `volley_outcome`, attack-similarity's
     `OutcomeForm` and `FollowupModel` put a defender who escaped one burst back at the distance,
     free from the burst's last live frame plus his roll's recovery past its invincibility
     (`INFERRED` then), while the dodge read of that very burst followed his roll's root motion and
     the spell kit's re-aimed casts (`CastThreats._touches`) carried him from roll to roll. All of
     them now follow one rule (`dodge_chain`; `FollowupModel._shape`, `_stage`, `_late`;
     `_volley_burst`): an escaping press leaves him on that roll's root motion from its start (on
     the update grid, from where earlier rolls had carried him), invincible for its own frames,
     able to dodge again only from its start plus its dodge cancel (`dodge_end`), and standing
     where the first whole frame of the dodge cancel (f21; it opens at f20.99999) left him
     (`track_extend` / `settled`: `INFERRED`, the clip's slide past its dodge cancel is not carried,
     as `CaughtModel._escapes` reads a roll's end at its cancel too; the back roll is 3.71 m back at
     f21, 3.17 at its clip's end, a side roll 3.42 m aside at f21, 4.41 at f56, `MEASURED` from
     `roll_motion`'s paths). A later burst caught him in that roll when it touches him outside its invincibility before
     he can dodge again (a play from that roll, `still_move`, priced by `CaughtModel` `dodge`); a
     burst that does not touch him where he is is not dodged. Before each later burst the attacker
     turns to face him (`face_track`, about the attacker's root where his `PK` has carried him by the
     burst's first sample): a roll aside carries his distance into the next burst, not his angle
     off a line the attacker no longer faces (`INFERRED`: a locked-on attacker turns at
     `er-mechanics-reach.DEFAULT_TURN_DEG_PER_S`, 720 deg/s, wherever no TAE 224 sets another rate
     and no JumpTable 7 stops him; the clips' own windows are not read here). The turn faces where his roll
     leaves him, not where it has him on the burst's first frame, so a roll still under way turns
     with him and the follow-up's read of the roll he escaped the opening with (`_faced_roll`) is the
     same turn at every start and delay. Keeping the line fixed instead let every side roll escape
     every later swing of a combo (`MEASURED`, an intermediate build with the line fixed: Savage Claws'
     side rolls evaded all six bursts and punished, mean net -384 against 508 before; with the turn,
     -170, attack-similarity.md section 2i). His punish is the last
     escaping press's, from where that roll started (`punish_lead` `origin`, `press_gain`), against
     the attacker's root as his whole attack left it (`attack_advance`): the two together are the
     gap he runs. Selftests: a 0.5 m burst on the spot he started from never touches a back roll
     that escaped the burst before (3.709 m back); a roll from f30 waits for f51 and is caught at
     f50.5 (version 25 freed him at f50); faced, a side roll's 3.424 m aside is z' 1.7392 straight
     ahead (2.2378 with the attacker's root 1 m up); the volley and the follow-up alike.
  2. *A follow-up catch while the opening could still touch him.* A play (above), of that delay.
  3. *A roll row's press the opening caught.* `FollowupModel.outcome` and `mixup` priced it at the
     column's delay, the attacker reacting to the press as to a roll he saw, while the stand row's
     attacker reacts to the caught defender's escape. The roll rows now apply the stand row's rule
     to every catch of the opening (`_CaughtRows`, `catch_escapes`: `CaughtModel.escape_table` of
     the catch, its `dodge` for a catch in his roll): at the column's delay unless he saw the escape
     in time, else the delay from then on that lands most against it. Selftest: with his floor at
     f30 every roll press comes after the opening's f20 flinch caught him; each roll row is the
     stand row (100, 100, 100, 111.1, ..., 200), where version 25 gave 200 at every column, the
     attacker pressing at f9 for a roll at f30 that never happened.
  4. *The exchange model's dodge.* `Opponents._table` timed the pool's openers by escape alone; it
     now aims as every outcome since version 25 does, for escaping the opener plus his punish from
     the press (`_ExchangeGain`: the chained hit missed too and his strike landing before the
     string's end, `dodge_value`'s own count). Its table is kept per (key, strike, reach) since it
     now reads them. Selftest: an opener at f40-42 and a chained hit at f49; aimed for escape alone
     the presses (f29.5-33.6) never escape both and he never punishes; aimed for both he punishes
     (over half the presses), at the aim a 0.01-frame scan finds best. What it still leaves out
     (**INFERRED**): its four directions are taken equally (the outcome model picks one since
     version 22), and the chained hit's second dodge is the single-hit evade share, not a roll
     carried from the first (item 1's rule). Both done in version 27 (below).
- **The slide, the attacker's facing, and the stages still apart (version 27).** The same user
  words. What version 26 left `INFERRED` or unfixed, each read in the code first:
  1. *His slide.* An escaping roll's segment is now its whole clip (`track_extend`): free to dodge
     again from its dodge cancel, he stays on its root motion until his next press, which starts
     from where it has him then (the back roll 3.71 m back at f21, 3.17 m at its end; a side roll
     3.42 m aside at f21, 4.41 m at f56, `MEASURED` from `roll_motion`). The follow-up's clock trick
     (a press `d` late read as his dodge `d` early) moves his track with it (`_shift_track`), since a
     sliding track is no longer the same at every delay.
  2. *The attacker's facing.* `clip_turn` reads each clip's turn rules on the update grid
     (`er-mechanics-reach.turn_rate_at`: 0 under JumpTable 7, else the last usable TAE 224 rate,
     else 720 deg/s; `VERIFIED` handlers there, the 720 deg/s store `VERIFIED`, that the player
     keeps it `INFERRED`); `_facing` integrates his facing toward the defender every update at no
     more than that rate, from facing him at the clip's start; each sample is read in the facing it
     was made in (melee at its frame, a shot at its launch, `_turn_apply`), about his root, whose
     forward root motion goes along his facing (a lunge made while he turns bends toward the
     defender, `_turn_track`'s drift). Savage Claws turns 600 deg/s through f15-63 and is locked
     from f70; Swift Slash turns 450 then 120 deg/s between its bursts and is locked from f45;
     Rosus's Summons is locked from f41.1, before its first burst; Glintstone Pebble's R2 turns
     720 deg/s at clip f3-6 and 180 deg/s f14-20, locked from f20 (`TAE`, 2026-11-16). Tracks stay
     in the attack's start frame; a roll out of one is taken facing the attacker from where it
     ends (`oriented`). The caught defender's play reads the same facing along his path
     (`CaughtModel._touch_all`). Not applied: a homing or aimed flight flown again (its aim follows
     him at creation), and a geometry with no clip read (a spell's cast, a selftest's), which reads
     as before.
  3. *The volley on the chain.* `volley_outcome` runs on `dodge_chain` (`_volley_chain`): its
     states carry which threats landed together, so the chance of each set is exact and the cap at
     his health is exact; a landed threat is no longer dodged.
  4. *The follow-up's tables* fly a homing geometry after him (`_TouchTable`, `_homing_touch`); an
     opening burst after the last that reaches him standing is read against where his rolls carried
     him (`FollowupModel._shape` 'pend').
  5. *The exchange model* (`Opponents.dodge_value`) takes per opener the direction worth most to
     him, and meets a chained hit his first roll does not escape with a second roll of the same
     direction out of the first, from its dodge cancel, aimed by `dodge_presses` (`_second_roll`),
     punishing from that roll when it escapes.
  Selftests (`_selftest_v27`, hand-computed): Savage Claws' turn rate per update; a toy facing
  and a 2 m lunge's drift; a disc 3 m out that misses a defender stepped 1 m aside unless the
  attacker turns; the slide's place at f51/f79 and a disc at its end; a forward roll out of a side
  roll's end turned toward the attacker; the volley's exact cap (1250 against 1500 chained as
  independent); a caught defender aside read in the facing; Rancorcall's flown skulls in a table;
  an opening burst past the last that reaches him standing; the exchange model's direction pick and
  second roll (0.0 at a chained hit before his dodge cancel, 1.0 at one after). Numbers:
  attack-similarity.md section 2j.
- **Every press carries his timing error (version 28).** The same user words. Until version 28 a
  press was `max(t, a + e)`: his aim a, his timing error e, clamped at the earliest frame t he
  could react. An aim at once (a <= t) gave one press at t with no error, so a defender who decided
  later (reacting to a lookalike's release, or to the frame a release would have shown) pressed
  exactly on time there, which a defender who knew the attack from the cue could only aim at with
  the error. Knowing the attack could then leave him worse off: on a 1.5-frame escaping run, 27/63
  of his presses escape knowing it from the cue and 34/63 reacting from f20.1 (`MEASURED` with the
  clamp put back in the process); version 27's selftest runs at 20:52-21:06 that day measured the
  Glintstone Dart's hold escaped 0.929 and punished 0.125 known, 0.968 and 0.139 against all its
  releases (its final run, 21:59, 1.000 and 0 either way; the check had been weakened to the first
  release alone). The rule now (`pressed`, `_AimTable`): he means to press at a, no sooner than his
  reaction t, and presses at max(F, a + e) for each of the 7 errors, F his recovery's floor. The
  error is his hand's whatever he aims at; only the floor truncates, since a press made while the
  recovery holds him comes out at its end (`INFERRED`, as before). The other rule, a clamp at the
  reaction, is the one that broke it: it takes away the early half of a press's spread at t and
  nowhere else, so a press at a later reaction is sharper than any aim. With the error on every
  press the knowing defender's
  choices contain every plan of a later decision: for each reaction quantile he may aim at any
  a >= t, and a later decision only raises t, so his worth by the plan's own measure (an escape plus
  its punish share) is never less. `er-mechanics-ash-whiff` (its module doc: "an early error is not
  clipped to his reaction") and `er-mechanics-timing-mixup.anticipation_evade` already pressed so.
  It applies wherever he presses: every dodge stage (`dodge_presses`, `group_presses`,
  `lookalike_plan`, and through them `reaction_outcome`, `dodge_chain` with the volley,
  `FollowupModel`, the spell kit's `dynamic_outcome`, `Opponents._table` and `_second_roll`), the
  strike (`er-mechanics-spells.strike_segments`, 63 starts a situation where it had 9), the strafe
  (`FollowupModel.strafe_escape`) and the buff answered on reaction (`Opponents.buff_answer_value`).
  `CaughtModel` presses nothing on reaction (a caught defender leaves at his floors, the game's
  frames), so it is unchanged. The bounds that read his earliest press move with it:
  `_SOONEST_PRESS` (his shortest reaction plus his earliest error, -2.2 frames) in `lookalike_plan`'s
  floor test and `burst_after`. Revenger's Blade on the Falx, not told from its release at f22:
  escape 0.679 -> 0.929, since at 3 m (and 2.5 m rolling forward or back) his plan waited for the
  release to react to it with no error and met the hold too late. Selftests: `pressed`'s presses
  (f17.802 to f22.198 for a press meant at f20; a floor at f19 takes the first two); the 1.5-frame
  run (27/63 from the cue, 23/63 from f20.1, and no decision time f0-29.9 above the cue, with or
  without a punish gain); 320 member moves of two toy groups, none worse off knowing (32 with the
  clamp put back); the Dart's full check restored; the strafe at 47 of 63 starts. Numbers:
  attack-similarity.md section 2k.

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
  through `FUN_1403a2c40` -> `FUN_1403a5a10`, a new entry, which keeps the record its parent's
  attack info carries (`FUN_14038b740` copies +0x638) and makes one only when it inherited none
  (corrected 2026-11-12: bd dmghitrecord-inherited-down-bullet-lineage-1162-2026-11-12; the
  parent's `attachEffectType` drops the inherited one, `FUN_14039bba0`). So the flag makes a
  `numShoot` fan hit a target once; it does not join the four separate launches, whose markers
  2070-2073 have neither the flag nor `attachEffectType` (`REGULATION`). All four 2074s can hit.
- **Landing (section 15a).** On a defender who holds still all six land at 1.5-3.0 m: the f28
  blades break every corpus poise (150 x saRate), the middle reaction's lock outlasts f43, and the
  0.8 m knockback does not carry him out of a 2.2 m burst. Against the reaction dodge 60.7% evade
  (a lateral roll clears the bursts; a back roll is caught), land 0.696.
- **Reach.** The 8.9 m is the melee reach: the 6.4 m dash between f27 and f39 plus the blade. Until
  2026-11-10 the bullet model placed its roots 1 m ahead of the start, not along the dash; now each
  burst leaves DummyPoly 9 where the dash has carried the caster, 0.7 / 1.7 / 3.0 / 5.3 m out
  (section 15, `bullet_launch`), which carries the bursts to 5.1-9.7 m; `f_reach` saturates at
  5.6 m either way.

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

## 19. What a skill or spell costs its own user in HP

User, 2026-11-14: "The model should price HP cost of spells/ashes." Before this, nothing that picks
an ash priced it: the ash table's `score`, the `hit_skill` objective of `er-builds-optimize` and
the attack-similarity nets all scored Bloody Slash's cast as if it were free, and Bloody Slash
was the `--conflict` skill pick of 13 Keen and Heavy weapons (the `hit_skill` objective's of 11). `er-mechanics-ashes.py` read the cost
rows only to classify them (`AshTables.is_buff` / `harms_target`), and attack-similarity only as an
on-hit 'hp drain' token (`speffect_tokens`).

### 19a. How the game takes it (`VERIFIED`, 2.7.2.0 `eldenring-deobf-1.17.2.bin`)

No Magic, SwordArtsParam or BehaviorParam field costs HP (paramdefs read: their only cost fields
are FP, stamina, durability and SA). The cost is a SpEffect row the cast applies to its own user,
with a positive `changeHpRate` (row +0x9c) or `changeHpPoint` (+0xa0):

- `0x1405008f0` / `0x140500920` read the two fields per ticking entry; `0x1404fc7d0` sums them;
  `0x1404f8bb0` makes the HP change `trunc(-(maxHP x changeHpRate x 0.01 + changeHpPoint))`. So
  `changeHpRate` is percent of the user's own max HP and `changeHpPoint` is flat.
- `0x140436ad0` adds it to HP and clamps to 1 only when `0x140437d30` holds (debug no-death flags,
  a ChrDataModule flag, or an active `stateInfo` 143 / 235 SpEffect). No cost row carries those or
  `noDead`: the cost can kill its user.
- TAE event 66 applies its row on the event's first frame (section 5), hit or not. A spell's
  `Bullet.spEffectIDForShooter` lands on the caster at launch (`INFERRED`: the field's consumer is
  not traced).
- A status row applied to the caster goes through the caster's own resist gauge: the event-66
  handler `0x14042c5b0` -> `0x1403e8dc0` -> `0x1403e8e70` -> `0x1403fb0a0`, which applies
  (`0x1404fb790`) and then runs `CSChrResistModule` (`0x14043e0e0`), removing the entry when the
  gauge held. So a status row costs its HP change only on the cast that procs.
- How many times one application ticks (one, for every cost row below) is `INFERRED`: the tick
  code `0x140501ed0` fires whenever the entry's timer (+0x4c) is <= 0 and then adds
  `motionInterval`, but the timer's start was not traced.

Addresses and the full chain: `scripts/er-mechanics-self-damage.py`'s module docstring.

### 19b. Every skill and spell that costs HP (`REGULATION`, `er-mechanics-self-damage.py --list`)

| item | row | cost per cast | when |
|---|---|---|---|
| Bloody Slash (ash) | 1763 | 5% of max HP + 50 | TAE 66, f15 of every opening clip |
| Blood Blade (ash) | 1760 | 4% of max HP | TAE 66, f18 |
| Assassin's Gambit (ash) | 1765 | 35 | TAE 66, f44 |
| Seppuku (ash) | 1753 | 15% + 100, a bleed proc (`bloodAttackPower` 9999) | TAE 66, f31 |
| Frenzyflame Thrust (Vyke's War Spear) | 1782 | madness 4 per event, 5 events (f10-f18): 15% + 100 HP and 10% + 30 FP on a proc | TAE 66 |
| Feeble Lord's Frenzied Flame (Nanaya's Torch) | 20000802 | madness 3 per bullet | shooter |
| Briars of Sin (sorcery 4900) | 1490000 -> 1490001 | 80, then bleed 30 | each launch (f11, f22, f33 clips) |
| Briars of Punishment (sorcery 4910) | 1491000 -> 1491001 | 50, then bleed 25 | each launch |
| Mantle of Thorns (sorcery 2004900) | 21490002 -> 21490003 | 50, then bleed 25 | ref 3 SpEffect, f18 |
| Impenetrable Thorns (sorcery 2004910) | 21491000 -> 21491001 | 50, then bleed 25 | each launch (f18, f52) |
| Inescapable Frenzy / The Flame of Frenzy / Unendurable Frenzy / Frenzied Burst / Midra's Flame of Frenzy | 1730001-2 / 1731002-3 / 1731101 / 1732002-3 / 21730001 | madness 14 and 9 / 4 / 3 / 20 (30 charged) / 6 per launch | shooter |
| Fire's Deadly Sin | 1790000 -> 1790002 | 3 HP ticks while its 40 s aura cycles them | ref SpEffect |

The NPC copies (Magic 8000, 8001, 53043, 53044, 53181, 53182, 53852, 2050090) carry the same or
smaller rows and are not player spells.

### 19c. Where each model prices it

One rule, `er-mechanics-self-damage.cost_rows` / `cost_at`: `pct` percent of the user's max HP plus
`flat`, per application. Status rows (`status_cost`): build-up of at least 9999 is one proc
(Seppuku, `INFERRED`: no player resistance reaches it); a smaller build-up is the share of a proc
it buys against the caster's own resistance, and with no such resistance it is left unpriced and
listed. No model carries its attacker line's resistances (the corpus median is a usage figure, not
a line's), so every small status build-up in the table above is unpriced today.

- Ash table (`er-mechanics-ash-table.apply_hp_cost`): the cost comes off the clock the row is
  scored on, `score` and `dps` (and the string's `dps`), never `dmg`. Max HP is `Table.max_hp`:
  `TARGET_HP` (the 60 VIG RL150 player the table's status is read against, `INFERRED` mirror) on
  its own, each spread's own under `--conflict` (`Conflict.table_rows`).
  `er-mechanics-combo-loadout.set_self_costs` takes that charge back out (`unapply_hp_cost`) and
  charges its own, at its build's max HP and land share, so the loadout pays once.
- `hit_skill` (`er-builds-optimize.Scorer.skill_score`): the cast's damage less
  `skill_hp_cost`, the cost at the build's own VIG (no talismans).
- Attack similarity (version 13): every net, at each line's max HP (`line_hp`; the distance basis
  at the median defender's, `INFERRED`), from the record's 'hp_cost'.

### 19d. What it changed in `--conflict` (`MEASURED` 2026-11-14)

`er-mechanics-ash-stats.py --conflict` on the 14 weapons whose pick was an HP-cost skill, against
the earlier full run. At VIG 58-60 (1871 HP at 58) Bloody Slash costs 143.6 a cast; its table score
at S_hit fell 669 -> 525. `gain` is S_comb's objective over S_hit's.

| weapon | skill pick | objective's skill | gain | S_comb STR/DEX/ARC |
|---|---|---|---|---|
| Bastard Sword (Heavy) | Bloody Slash -> Troll's Roar | Troll's Roar (same) | 0.0% -> 0.0% | unchanged |
| Black Steel Twinblade (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 9.7% -> 0.0% | 18/33/57 -> 18/76/9 |
| Gargoyle's Twinblade (Heavy) | Bloody Slash (same) | Bloody Slash -> Giant Hunt | 6.3% -> 0.0% | 43/15/45 -> 77/15/9 |
| Godskin Peeler (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 8.3% -> 0.0% | 17/44/52 -> 19/80/9 |
| Godskin Stitcher (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 8.2% -> 0.0% | 15/45/52 -> 19/80/9 |
| Great Epee (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 8.5% -> 0.0% | 15/44/53 -> 19/80/9 |
| Idus Sword (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 11.8% -> 0.0% | 10/21/80 -> 19/80/9 |
| Milady (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 9.5% -> 0.0% | 15/42/55 -> 19/80/9 |
| Queelign's Greatsword (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 8.2% -> 0.0% | 15/42/52 -> 15/80/9 |
| Smithscript Dagger (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 18.2% -> 0.2% | 10/20/77 -> 10/79/10 |
| Sword Lance (Heavy) | Bloody Slash -> Giant Hunt | Giant Hunt (same) | 0.0% -> 0.0% | unchanged |
| Twinblade (Keen) | Bloody Slash -> Beast's Roar | Bloody Slash -> Beast's Roar | 9.7% -> 0.0% | 15/41/56 -> 19/80/9 |
| Twinned Knight Swords (Heavy) | Bloody Slash (same) | Bloody Slash -> Giant Hunt | 7.1% -> 0.0% | 36/18/49 -> 74/18/9 |
| Vyke's War Spear (Standard) | Frenzyflame Thrust (same) | same | 8.9% -> 8.9% | unchanged (its madness is unpriced) |

On Gargoyle's Twinblade and Twinned Knight Swords the skill pick stays Bloody Slash: the pick is
the ash whose table score is highest at its own spend, and at an ARC-heavy spread Bloody Slash
still beats Giant Hunt after its cost. The objective, which spends for hit and skill together,
takes Giant Hunt on both.

## 20. One cast on one defender: hit lists and records in the sums

`pvp_damage` used to add up every row `skill_hits` lists. `hit_skill` (`er-builds-optimize`), the
ash-stats spend and every table that calls `pvp_damage` therefore priced one cast of Surge of Faith
at 4,275 HP. The median RL 140-160 defender (VIG 60) has 1,900 HP (`MEASURED`:
`er-builds-optimize._max_hp`). Two game rules were missing from the sum. `skill_option` already
applied the first.

- **Hit priority records** (section 15, bd er-effects-rs-dnok, `VERIFIED`). The attacks of one TAE
  341 record land once between them on a victim, plus one more for each later attack with a
  strictly lower priority.
- **Bullet hit lists** (`HIT_LIST_NOTE` in `er-mechanics-ashes.py`, `VERIFIED` 1.16.2: FUN_14038fe80,
  FUN_14039bba0 -> FUN_14038b740). A launch whose row has `isUseSharedHitList` or
  `attachEffectType` makes one DmgHitRecord. Its whole `numShoot` volley and every child shares it,
  so the tree hits a victim once per `dmgHitRecordLifeTime`.

`skill_hits` now tags each bullet row with its list (`hit_list`). `cast_mask` lands the rows the
records allow, and of those only the row worth most in each list. `pvp_damage` sums only the rows
that land (`lands`), and `skill_landing` skips a bullet whose list has already hit him. Every row
keeps its own `pvp_damage_total`.

The three casts that were questioned, at the `--conflict` stats against the median defender
(`MEASURED` 2026-11-14, worktree `skill-hits`; the old sums equal the stored ones exactly):

| skill (weapon, stats) | old | records only | records and lists | defender's 1,900 HP |
|---|---|---|---|---|
| Surge of Faith (Cranial Vessel Candlestand, STR 26 / FTH 66) | 4,275.3 | 2,266.4 | 1,596.8 | 84% |
| Wave of Gold (Sacred Relic Sword, DEX 24 / FTH 67) | 2,814.0 | 2,814.0 | 849.5 | 45% |
| Onyx Lords' Repulsion (Onyx Lord's Greatsword, INT 68) | 2,627.4 | 1,065.2 | 1,065.2 | 56% |

- **Surge of Faith** (040000; 22 rows, 6 land):
  - Rows that land:
    - the f24 swing, AtkParam 301217900 (4.8);
    - the f62 pillar, Bullet 2370 / 301217905, 1,721.5 fire before defense (922.3). Its row has no
      shared list (`isUseSharedHitList` 0), and the 2371 and 2373 launches beside it in record 0 have
      priority 1, so they are refused;
    - one flame per later record, each 411.66 fire (167.4): f77 2371, f88 2373, f99 2372, f110 2373.
  - Rows that do not land:
    - Each flame row 2371-2374 (`isUseSharedHitList` 1, 3 s list) leaves Bullet 2375 (301217914,
      `HitBulletID`) in the same list. So a flame and its 2375 are one hit, where the old sum
      counted both.
    - Records 1, 3 and 4 hold two or three launches at priority 0 each. One of them lands.
- **Wave of Gold** (Bullet 4000, `numShoot` 17, 4 s list). The wave steps along the ground, 4000 ->
  4001 -> ... -> 4045. It changes AtkParam row as it goes: 300310905, then 907, 906 and 908, at
  FinalDamageRate 0.8, 0.7, 0.65 and 0.6. That is a fall-off with distance, all in one list, so it
  is one hit of 849.5 to 586.2, not four.
- **Onyx Lords' Repulsion**: the f49 swing (284.0) and one of the three f81 spheres (Bullets
  3000-3002, radius 4 / 4.5 / 5 m, 781.1 each). The spheres share record 0 at priority 1.

These figures assume the defender stands where every row reaches him. `skill_landing`, which lets
him leave once he can, gives 693.0, 849.5 and 582.3, the mean over the distances each cast
connects at. In that model Surge of Faith lands one hit at each distance: the swing at 1 m, the
pillar at 2-4.5 m.

On the 389 damaging skill rows of `--conflict`, 176 change: 54 through the records and 124 through
the lists. That is 45 of 146 skills. Before, 10 rows priced one cast above 1,900 HP. Now none does.
The largest changes, per skill:

| skill | weapon | old | records only | new |
|---|---|---|---|---|
| Surge of Faith | Cranial Vessel Candlestand | 4,275.3 | 2,266.4 | 1,596.8 |
| Light | Sword of Light | 2,260.4 | 282.6 | 282.6 |
| Wave of Gold | Sacred Relic Sword | 2,814.0 | 2,814.0 | 849.5 |
| Onyx Lords' Repulsion | Onyx Lord's Greatsword | 2,627.4 | 1,065.2 | 1,065.2 |
| Alabaster Lords' Pull | Alabaster Lord's Sword | 1,992.6 | 791.5 | 791.5 |
| Starcaller Cry | Starscourge Greatsword | 1,453.9 | 290.8 | 290.8 |
| Erdtree Slam | Staff of the Avatar | 2,098.1 | 946.8 | 946.8 |
| Night-and-Flame Stance | Sword of Night and Flame | 2,384.3 | 1,333.1 | 1,333.1 |
| Great Oracular Bubble | Envoy's Greathorn | 1,977.7 | 1,977.7 | 988.8 |
| Siluria's Woe | Siluria's Tree | 2,118.4 | 2,118.4 | 1,136.0 |
| Shadow Sunflower Headbutt | Shadow Sunflower Blossom | 1,725.1 | 871.0 | 871.0 |
| Flame Spit | One-Eyed Shield | 1,650.2 | 1,650.2 | 825.1 |
| Familial Rancor | Family Heads | 1,540.6 | 1,540.6 | 770.3 |
| Waterfowl Dance | Hand of Malenia | 1,520.8 | 786.1 | 786.1 |
| I Command Thee, Kneel! | Axe of Godrick | 1,244.9 | 559.5 | 559.5 |

**A Phalanx blade is one bullet (version 22, 2026-11-16).** A searching attached row
(`search_launched`: life -1, `FollowType` 1, 2 or 5, `autoSearchNPCThinkID` above 0; 14 rows,
among them the spell blades 10430000 / 10430100 / 10430200 and the ash's 2300) hovers, then flies
its `HitBulletID` row in the same bullet (**VERIFIED** 1.16.2, FUN_14039ee00: the bullet's state
list, the fly state's row its `HitBulletID`; bd phalanx-launch-is-caster-facing-target-search-1162-
2026-11-16). One bullet keeps one hit list, of lifetime 0 here, which never lapses, and neither row
penetrates, so a blade lands once. `er-mechanics-spells.SpellModel.bullet_lists` counted the hover
row and its `HitBulletID` row as two lists, so the pouch's `full`, the volley's worth and the
skill's `value` priced each blade twice, while attack-similarity's caught defender
(`CaughtModel`) could land only the flight's list, the hover having ended at the launch: land
shares topped out at 0.5. Now the two rows are one list, priced at the fly row (**MEASURED**:
Glintblade Phalanx `full` 756.5 -> 378.3, Carian Phalanx 891.7 -> 445.9 at Glintstone Staff +25 /
INT 80; the ash 2300 -> 2301 x 4). Whether the hover itself can hit is not traced (the decompiler
on :8765 failed on 0x1403abc00 that day). In the model it cannot land where the model plays it:
it follows the caster 0.33 m ahead of him at 1 m up (the spell's launch) and the spaced situations
put the defender 2.6 m away or more (**MEASURED**), and a hit there would end the blade anyway.

Not modelled yet:

- **A hit after the list runs out.** A member that still touches him after the list's lifetime can
  hit again. Smithing Art Spears, Discus Hurl and Spearcall Ritual have members made after the
  lifetime, so the new figures may under-count them (`HIT_LIST_NOTE`).
- **The callers' own sums.** These callers sum `pvp_damage_total` over all rows, not `lands`:
  `er-mechanics-ash-table.py` (the group shares and `all_dmg`), `er-mechanics-ash-stats.py`
  `contributions`, and `er-mechanics-ash-levers.Levers.cast`, whose attack before defense still
  counts every row.

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
