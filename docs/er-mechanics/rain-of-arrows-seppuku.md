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
  while buffed does too, if its goods context also admits `wepParamChange` 1.
