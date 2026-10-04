# Piquebone smoke spreads your right-hand status buff (Rain of Arrows + Seppuku or grease)

Observed by the user (ground truth, not re-derived here): Rain of Arrows fired with Piquebone
Arrows at an NPC, followed by Seppuku, puts repeated bleed procs on every enemy in the area. A blood
grease instead of Seppuku does the same.

Labels: `VERIFIED` = read from the 1.17.1 regulation or the 1.16.2 executable (Ghidra :8765,
shift 0); `INFERRED` = follows from verified pieces but not traced end to end; `USER` = the
user's in-game observation.

## Short answer

Seppuku's self-bleed is not the cause. The cause is the Piquebone Arrow's white lure smoke. The
smoke is a 4 s, 15 m player-owned zero-damage hitbox. Each time it hits an enemy, the game reads
the player's current right-hand status buff and applies that buff's on-attack status row to the
enemy. Seppuku's buff 1755 and Blood Grease 3190 are both such buffs, and both carry bleed 30 per
hit.

## The chain

1. **Piquebone Arrow is a lure arrow, not a bleed arrow.** `VERIFIED`. Its caption reads
   "Releases a white smoke at the point of impact, luring in foes of human build ... drawing their
   aggression". EquipParamWeapon 50540000 / 50550000 have no `spEffectBehaviorId`, so the arrow
   itself builds no status.
2. **Every Piquebone arrow bullet, Rain of Arrows included, spawns the smoke.** `VERIFIED`.
   The Rain of Arrows chain is 20003351 -> 20003352 -> emitter 20003353, which drops a falling
   arrow 20003354 every 0.06-0.07 s for 0.8 s (about 12 arrows). The arrow's hit attack is
   AtkParam_Pc 5036850. Its `HitBulletID` is 20003308 (0.07 s, `launchConditionType` 5,
   create-limit group 30), which chains to 20003309: life 4.0 s, radius growing 0.05 -> 15 m over
   0.1 s, `atkId_Bullet` 0, `spEffectId0/1` 482/483, create-limit group 31. That is the same row
   shape as the Alluring Pot's 10039002. BulletCreateLimitParam 30 and 31 are `limitNum_byGroup`
   1 with `isLimitEachOwner` 1, so the player has one smoke cloud at a time.
3. **The smoke is a real hit on enemies.** `VERIFIED` (regulation). AtkParam_Pc row 0 is
   "Impact: Oppose": zero damage, `opposeTarget` 1. SpEffects 482/483 are blank apart from
   `effectTargetOpposeTarget` 1 and a 1.0 s duration. The lure itself is AI-side and is not
   traced here.
4. **The smoke's hit context comes from the arrow's BehaviorParam category.** `VERIFIED`.
   `FUN_14038e210` copies `BehaviorParam.category` (+0x1c, disassembly 0x14038e2ae) into the
   bullet's attack-context byte. `FUN_14038e380` puts it in the attack info, and `FUN_140d24b10`
   copies it to `AttackDamageInfo+0xda`. Every Piquebone BehaviorParam_PC row (variations 5040 and
   5041, ids 1050408xx / 1050418xx) has category 1. Per memory
   `bullets-carry-firing-weapon-hit-speffects-great-stars-heal-2026-10-01`, children launched
   through `HitBulletID` copy the parent's attack info.
5. **At hit time the game reads the owner's live buffs.** `VERIFIED`. `CalculateDamage2`
   (0x1404483b0; call at 0x140448dd5) runs `FUN_1404f71e0(damageDealer->specialEffect,
   FUN_1404fee60(damageInfo))`. That returns the `atkOccurrenceSpEffectId` of the first live
   stateInfo 152/153 entry passing `IsApplicableForCategory` (0x140500930), and applies it to the
   victim through `FUN_1403e8c90(victim, id, dealer)`. Because the lookup happens per hit, a buff
   applied after the smoke spawned still rides it.
6. **Category 1 admits right-hand buffs only.** `VERIFIED`. The context switch jump table at
   0x140500b08 sends context 1 to 0x140500979, which refuses `wepParamChange` 2, 3 and 4 and
   accepts 1. Seppuku 1755 and Blood Grease Right 3190 are both stateInfo 152, spCategory 162,
   `wepParamChange` 1. Their on-attack rows are 1756 and 3191, each stateInfo 6 with
   `bloodAttackPower` 30. Left-hand rows (1758, 3194; `wepParamChange` 2) are refused, which
   matches the user's "main hand". `USER` + `VERIFIED`.
7. **Repeated procs on everyone in range.** `INFERRED`. The smoke reaches 15 m and hits every
   enemy in it. `FUN_1403960a0` opens a timed `DmgHitRecord` only when `dmgHitRecordLifeTime` is
   above 0, and the smoke's is 0 with no shared list, so it runs with no hit record. Whether that
   means re-hitting every tick is not traced inside `DmgMan`. The 1.0 s lure duration on 482/483
   and the observed repeated procs both fit a continuously re-hitting cloud. Bleed's re-proc
   lockout is 1 s (memory `er-status-reproc-lockout-spcategory-2026-09-29`), so each enemy in the
   cloud would proc about once a second for as long as it keeps building 30 per hit.

## What Seppuku's self-bleed does, and why it is not the spreader

`VERIFIED`. Seppuku 1753 (`bloodAttackPower` 9999) procs bleed on the player. Every bleed row
cycles SpEffect 500 "Blood Loss (Cycled)" (stateInfo 467, `behaviorId` 2100, dmypoly 220) onto
the bleeding character: `FUN_1404fae40` applies `cycleOccurrenceSpEffectId` to
`container->owner`. `FUN_1404faa70` then fires BehaviorParam 2100 -> Bullet 1000 "Blood Loss -
Bullet" out of that character's own `chrBulletShooter`. Bullet 1000 is a 0.5 s, 7 m sphere with
AtkParam 2 ("Impact: Oppose and Self") that applies 501 "Presence of Blood" (stateInfo 379, the
Lord of Blood's Exultation trigger). The launch passes equip slot 0xc, and
`PlayerIns::GetEquipmentEntryParamId` (0x140656960) returns -1 for it, so the burst carries no
weapon. BehaviorParam_PC 2100 is category 0, which takes the switch's default branch: only
`wepParamChange` 0/5/6 rows pass there (`IsWepParamChange056` 0x140d50990), and the
right-hand 1755 does not. So the player's blood burst carries no buff, and a grease reproduces the
effect without it. An enemy's own bleed proc fires an enemy-owned burst (NPC BehaviorParam 2100,
category 6) with no buff to carry, so procs do not chain from enemy to enemy.

## Not proven

- Smoke re-hit cadence with `dmgHitRecordLifeTime` 0 and `isEndlessHit` 0 (step 7): needs a trace
  of how `DmgMan` treats a null hit record, or a Frida hook on `FUN_1403e8c90` counting
  applications of 1756/3191 per victim per second.
- `launchConditionType` 5 on 20003308 is "Unknown" in Paramdex, and its reader was not
  identified. Whether the smoke needs the arrow to hit a character (the user aimed at an NPC) is
  open.
- Untested predictions from the gate: a right-hand Freezing, Poison or Soporific grease (3140,
  3175, 3150) spreads its status the same way; a left-hand grease does not; an Alluring Pot thrown
  while buffed does too, if its goods context also admits `wepParamChange` 1. (Answered below: the
  goods context is 0 or 5, which refuses it.)

## Other sources

Question: what else spreads the right-hand status buff the way the Piquebone smoke does? Answered
by walking all 4,958 bullet rows of BehaviorParam_PC (13,865 rows) through `HitBulletID` and
`intervalCreateBulletId`, keeping bullets that hit enemies (`opposeTarget` 1), last at least 1 s,
cover at least 1 m and move at most 5 m/s, then naming each launch through the skill TimeAct
(`er-mechanics-ashes` skill profiles, every skill on every weapon type that can mount it) and the
Smithbox row names. Ids are 1.17.1 regulation.

### Which launches can carry a right-hand buff

`VERIFIED` (1.16.2 :8765, shift 0). The context byte is not always `BehaviorParam.category`.
`FUN_14038e210` takes it from one of three places, by what the bullet spawn data carries:

| spawn data | context byte | values in the regulation |
|---|---|---|
| no explicit bullet id (+0x14 == -1) | `BehaviorParam.category` (+0x1c) | 0, 1, 2, 4, 5, 9, 12 |
| goods id (+0x18) | `EquipParamGoods.spEffectCategory` (+0x40) | 0, 5 |
| magic id (+0xc) | `Magic.spEffectCategory` (+0x28) | 3 (sorcery), 4 (incantation) |

The whole `IsApplicableForCategory` switch (jump table 0x140500b08, index = context - 1; context
0 and anything above 12 take the default branch):

| context | accepts | carries a `wepParamChange` 1 buff |
|---|---|---|
| 1 | anything but `wepParamChange` 2, 3, 4 | yes |
| 2 | anything but 1, 3, 4 | no (left-hand buffs instead) |
| 3 | rows with `magParamChange` set | no |
| 4 | rows with `miracleParamChange` set | no |
| 10 | rows with `shamanParamChange` set | no |
| 11 | anything but 3, 4 (both hands) | yes, but no row uses 11 |
| 12 | 1 always; 0/5/6; 2 only while `GetArmStyle` returns 2 (left weapon two-handed) | yes |
| 9 | 0/5/6 or 4 (kick) | no |
| 0, 5-8 | 0/5/6 only (`IsWepParamChange056`) | no |

An accepted row must then pass `CheckMagicSubCategoryChangeMask`. Every grease, Seppuku and
armament buff has `magicSubCategoryChange1..3` all 0, which takes the no-mask early return (the
only stateInfo 152 row with a mask is the Serpent Bow's 1938). None of the 60 stateInfo 152 rows
has `magParamChange`, `miracleParamChange` or `shamanParamChange`, so sorceries, incantations and
thrown goods never carry a weapon buff. Category 1 is the plain right-hand moveset and right-hand
ammo; category 12 is most weapon skills.

### (a) Zero-damage lingering cloud that applies the buff's status on each hit

| source | bullets | context | re-hit settings |
|---|---|---|---|
| Piquebone Arrow, Piquebone Arrow (Fletched) | 40 launch rows (variations 5040/5041, all category 1) -> 20003308 -> smoke 20003309 | 1 | `dmgHitRecordLifeTime` 0, `isEndlessHit` 0 |
| Piquebone Bolt (52520000), crossbow in the right hand | BEH 1052203xx, 105220500, 105220505 (7 rows, category 1) -> 20010000-20010055 -> 20003308 -> 20003309 | 1 | same smoke |
| Piquebone Bolt, crossbow in the left hand | BEH 105220400, 410, 440-442 (5 rows, category 2) -> same smoke | 2 | same smoke; spreads a left-hand buff (Blood Grease Left 3194, left Seppuku 1758) and refuses the right-hand one |
| Poisonous Mist (ash 228), Chilling Mist (ash 227) | BEH 300000162 / 300000161 (category 1) -> 2415 / 2410 -> cloud 2416 / 2411: 4.0 s, radius 1.0 -> 2.5 m, AtkParam_Pc 20 (zero damage, `opposeTarget` 1), `spEffectId0` 834 / 829 | 1 | `isEndlessHit` 1, `dmgHitRecordLifeTime` 0.7, shared hit list, `isHitBothTeam` 1 |

All rows `VERIFIED` from the regulation. That the bolt's judges 3xx are the right-hand shots and
4xx the left-hand ones is `INFERRED` from the category split.

The mists are the one case where the cloud does not carry your grease. The skill applies its own
weapon buff first (TAE frame 16; the cloud spawns at frame 52): 831 Poisonous Mist poison buff /
826 Chilling Mist frost buff, stateInfo 152, spCategory 162, `wepParamChange` 1, on-hit 882
(poison 60) / 880 (frost 60). Seppuku 1755 and every grease are spCategory 162 too, so the
skill's buff replaces them (`INFERRED`: same-spCategory replacement, not traced here). The cloud
therefore spreads 882 / 880 on every hit, on top of its own bullet SpEffect 834 (poison 120) /
829 (frost). Untested prediction: cast from a one-handed left weapon, the skill applies the
left-hand row (833 / 828, `wepParamChange` 2), which category 1 refuses, so a right-hand grease or
Seppuku would ride the cloud instead.

### (b) Lingering, re-hitting damage bullets that carry the buff

Only one is reachable on a weapon that can hold a buff:

| source | bullets | context | re-hit settings |
|---|---|---|---|
| Eruption (ash 207; Greatsword, Greataxe, Great Katana, Large Club, Bastard Sword and three more mount types) | BEH 300000048 (category 1) -> 2018 -> 2019: 5.0 s, radius 1.0 m, damaging | 1 | `dmgHitRecordLifeTime` 1.0 against life 5.0, so up to five hits per enemy (`INFERRED` cadence) |

Eruption's other launch, BEH 300000042 -> 2012 -> the same 2019, is category 0 and carries
nothing; which animation fires which row was not split out. Rows `VERIFIED`.

The same mechanism, but on weapons that cannot hold a buff (`isEnhance` 0 and `gemMountType` 0 on
every one, so no grease, no armament spell and no Seppuku mount; rows `VERIFIED`):

| skill (weapon) | bullet, context | life, radius, re-hit |
|---|---|---|
| Bloodboon Ritual (Mohgwyn's Sacred Spear) | 2340, 12 | 2.2 s, 8 m, endless, record 0.3 |
| Ghostflame Ignition (Death's Poker) | 2294, 1 | 5.0 s, 1 m, record 0.5 |
| Zamor Ice Storm (Zamor Curved Sword) | 3057, 1 | 1.5 s, 3 m, record 0.4 |
| Frenzyflame Thrust (Vyke's War Spear) | 2749, 12 | 2.0 s, 0.1 -> 1 m, record 0.4 |
| Bloodfiends' Bloodboon (Bloodfiend's Sacred Spear) | 200003205, 12 | 2.0 s, 2.5 m, record 0.4 |
| Rolling Sparks (the four Perfume Bottles) | 200003036 / 046 / 056 / 066, 12 | 1.3 s, 1 m, record 0.5 |
| Spear of the Impaler moves | 200041913, 1 and 12 | 1.0 s, 1 m, record 0.5 |

Two zero-damage clouds with the Piquebone shape are on unbuffable weapons as well: Soul Stifler
(Winged Greathorn; BEH 301511900, category 12 -> 2065, 9.0 s, radius 0.1 -> 5 m, record 0,
`spEffectId0` 1545) and Fires of Slumber (St. Trina's Torch; 2826, category 12 right-hand and 2
left-hand rows, 5.0 s, 1 m, endless, record 0.4). That a weapon buff does not survive a weapon
swap, the only way one could be live while these run, is `INFERRED`.

### (c) Near misses

- Category refuses the right-hand buff (`VERIFIED`):
  - White Shadow's Lure (ash 850): the closest twin of the Piquebone smoke, a zero-damage lure
    cloud 2672 (5.0 s, radius 0.1 -> 15 m, record 0, `spEffectId0` 487, create-limit group 19),
    but its rows BEH 300000760-762 are category 0.
  - Prelate's Charge (ash 113): fire trail 2312 / 2314, 5.0 s, record 1.0, category 0.
  - Knowledge Above All (Scepter of the All-Knowing): 2114 / 2115, 1.0 s, radius up to 65 m,
    category 0.
  - Ghostflame Call (DLC skill 4220): 200002594, 5.0 s, record 0.5, category 0.
  - Deadly Poison Spray (DLC skill 5490): 200003260, 7.0 s, radius 1 -> 6 m, category 0.
  - Thrown goods (Alluring Pot and every pot or throwable): context is
    `EquipParamGoods.spEffectCategory`, 0 or 5 on every row. That a goods throw fills the goods
    id rather than a behavior id is `INFERRED`; the behavior rows goods name are category 0, 5 or
    9, so either path refuses.
  - Every sorcery and incantation, lingering ones included: context 3 or 4, see above.
- Single hit per bullet (`dmgHitRecordLifeTime` at or above the bullet's life), context 1 or 12,
  `VERIFIED`: Stormcaller (ash 123, seven bullets 2640-2646), Storm Assault (ash 122, 2600 /
  2601), Thunderstorm (Stormhawk Axe), Magma Guillotine, Moon-and-Fire Stance, Horn Calling:
  Storm, Smithing Art Spears, and Eruption's first zero-damage burst 2011. These spread the buff
  once per enemy per bullet; Stormcaller is the only one on buffable weapons.
- Shriek of Milos (Sword of Milos, unbuffable): 2960 / 2961, zero damage, 10 m, but 1.0 s.
- Rows with no player source (`VERIFIED`: no named weapon owns the variation): 2171 (BEH
  102001910, variation 2001, 8 s endless damage) and 1000000 (BEH 103490910, variation 3490 whose
  weapons 34900000 / 34960000 have no name; a scarlet rot cloud). Catalyst-variation rows that
  fire spell bullets under category 1 exist (Greyoll's Roar 103400920 under the Finger Seal
  variation, Rejection, Rancorcall, Carian Phalanx), but the spells themselves launch through
  `Magic.refId1` (Greyoll's Roar 7090 -> 10709000). Whether those behavior rows are ever used is
  not traced, and Greyoll's Roar is single-hit anyway.

### Not proven, for these

- Re-hit cadence. `FUN_1403960a0` is called from the bullet state updates `FUN_1403abfc0` and
  `FUN_1403abf30`. It passes the previous `DmgMan` handle only while the radius is still
  expanding and -1 otherwise, so a bullet past its growth asks `FUN_140526230` for a fresh damage
  entry on each call. How often those state updates run, and how `DmgMan` dedupes a target with
  no hit record, decides what record 0 means for the Piquebone smoke, White Shadow's Lure and
  Soul Stifler.
- No combination above was tested in game.
