# Rain of Arrows with Piquebone, locked on, spreads your right-hand status buff

Observed by the user (ground truth, not re-derived here): Rain of Arrows fired with Piquebone
Arrows at an NPC, followed by Seppuku, puts repeated bleed procs on every enemy in the area. A blood
grease instead of Seppuku does the same.

Labels: `VERIFIED` = read from the 1.17.1 regulation or the 1.16.2 executable (Ghidra :8765,
shift 0); `INFERRED` = follows from verified pieces but not traced end to end; `USER` = the
user's in-game observation. Addresses are 1.17.1 (the installed build, read in
`eldenring-deobf-1.17.1.bin`) with the 1.16.2 address in brackets; 1.17.1 equals 1.17.0 below rva
`0xafefe9` and is `+0x70` at or above it.

## Lock-on (2026-10-04): the smoke is not shown to be the spreader

New `USER` ground truth: the grease builds status only when Rain of Arrows is fired locked on.
Rain of Arrows without lock-on and plain Piquebone shots build none. That contradicts the short
answer below, because the smoke is the same bullet in all three:

| | plain Piquebone shot | Rain of Arrows, unlocked | Rain of Arrows, locked |
|---|---|---|---|
| launch row (all category 1) | BEH 1050403xx/8xx, e.g. 105040300 | 105040851 (FP anims 40060/44560; 105040856 = no-FP single arrow) | same row as unlocked |
| chain | 20003300 -> 20003308 -> 20003309 | 20003351 -> 352 -> emitter 353 -> ~12 x 20003354 -> 20003308 -> 20003309 | same rows |
| lock-dependent fields | none (arrow `homingAngle` 0) | 20003351 `EmittePosType` 6 "above and behind target" has no target; 20003354 `homingAngle` 10 has nothing to home on | emitter placed over the target; ~12 falling arrows home onto it |
| hit that could carry the grease | arrow AtkParam_Pc 5036300 (scale 1.0), smoke AtkParam_Pc 0 (scale 1.0) | smoke (scale 1.0); arrows land at a fixed spot | falling arrows AtkParam_Pc 5036850 (scale 0.65, radius 0.05 -> 1.0 m, record 0.1), smoke (scale 1.0) |
| grease status (`USER`) | none | none | yes |

`VERIFIED` (regulation; the TimeAct of skill 406 on the Longbow fires judge 851 in the FP
animations and 856 in the no-FP ones). Lock-on changes no row, no context byte and no AtkParam. It
changes only where the emitter appears and whether the falling arrows home. So the one hit that
exists in the locked case and not in the other two is the falling arrows landing on the target and
on enemies within their 1 m radius. Every gate passes for them (context 1, scale 0.65).

Falsified by `USER` (2026-10-04): every test used Rain of Arrows with the grease applied well
after the arrows had landed, and lock-on is needed only to make the hitbox cover all the enemies.
The falling arrows are gone before the grease exists, so they cannot be the carrier. The two
bullets below are kept as the reasoning that was ruled out.

Proven live the same day (Frida, `scripts/frida/weapon-buff-bullet-hits.js`, run
`br-20261004-223342-81c1`): the smoke 20003309 is the carrier. One locked-on Rain of Arrows gave
4 falling-arrow hits with no buff live, then 115 smoke hits over 3,959 ms (the smoke's 4.0 s
life). The first 6 returned no buff. The other 109 came after the grease went on: the buff reader
returned 3313 (Drawstring Rot Grease - Right, rot 80, scale 1.0) and applied it on every hit. Why
lock-on matters is still open. The `launchConditionType` 5 lead is ruled out: the gate spawns the
smoke wherever a falling arrow lands, on a body or on the ground (`weapon-buff-leak.md`, "Smoke
spawn gate and re-hit cadence").

Two consequences, both `INFERRED` and both superseded by the paragraph above:

- The spreader is the homed falling arrows, not the smoke. The smoke passes both gates in all three
  cases but builds nothing in two of them, so something not in the static data stops it (the
  hit-record behaviour of a `dmgHitRecordLifeTime` 0 bullet, a damage-path skip for a zero-damage
  hit, or `launchConditionType` 5 on 20003308, see below). The scan now flags the smoke as a
  measured negative it cannot explain and ranks the falling arrows as the positive.
- The falling arrows land about 1.3 s into the animation plus the 0.8 s emitter, so the grease has
  to be live before that. A grease applied after firing could not have reached them; if the
  locked-on test applied it after firing, the arrows theory is wrong too and only the trace below
  can say what carried it.

`launchConditionType` is read in `0x14039da50` [`FUN_14039da40`], called on a hit from
`0x14039dcd0` [`FUN_14039dcc0`] and on expiry from `KillBullet` `0x14039f0c0` [`0x14039f0b0`].
Hit info +0x2c is the struck Havok body's hit material, set for character and map hits alike;
only the expiry path passes -1. Case 5 spawns on a hit and never on expiry, case 4 the reverse.
On a hit the hitting bullet's own value decides; on expiry the child's. So 20003308 needs the
falling arrow to land anywhere, and 20003309 follows 20003308 whether it hits or expires.
`VERIFIED`; details and the full table in `weapon-buff-leak.md`.

The trace that settles it is `scripts/frida/weapon-buff-bullet-hits.js` (not run: another session
holds the game). Per enemy hit it logs the AtkParam id (0 = smoke, 5036850 = falling arrow,
5036300 = plain arrow), the bullet id, the context byte, the status scale, whether the
`0x267 & 8` skip fired, whether the buff reader ran, the on-hit SpEffect it returned, and every
SpEffect applied to the victim. Three runs (plain shot, unlocked, locked, each with the grease
on) answer which hit applies 3191 / 3151 and whether the smoke reaches the reader at all.

## Short answer (superseded in part, see "Lock-on" above)

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
   `0x14038e220` [`FUN_14038e210`] copies `BehaviorParam.category` (+0x1c, disassembly
   `0x14038e2be` [`0x14038e2ae`]) into the bullet's attack-context byte. `0x14038e390`
   [`FUN_14038e380`] puts it in the attack info, and `0x140d26290` [`FUN_140d24b10`] copies it to
   `AttackDamageInfo+0xda`. Every Piquebone BehaviorParam_PC row (variations 5040 and
   5041, ids 1050408xx / 1050418xx) has category 1. Per memory
   `bullets-carry-firing-weapon-hit-speffects-great-stars-heal-2026-10-01`, children launched
   through `HitBulletID` copy the parent's attack info.
5. **At hit time the game reads the owner's live buffs.** `VERIFIED`. `CalculateDamage2`
   `0x140448910` [`0x1404483b0`] (call at `0x140449335` [`0x140448dd5`]) runs
   `0x1404f7fb0(damageDealer->specialEffect, 0x1404ffc30(damageInfo))` [`FUN_1404f71e0`,
   `FUN_1404fee60`]. That returns the `atkOccurrenceSpEffectId` of the first live stateInfo
   152/153 entry passing `IsApplicableForCategory` `0x140501700` [`0x140500930`], and applies it
   to the victim through `0x1403e8e70(victim, id, dealer)` [`FUN_1403e8c90`]. Because the lookup happens per hit, a buff
   applied after the smoke spawned still rides it.
6. **Category 1 admits right-hand buffs only.** `VERIFIED`. The context switch jump table at
   `0x1405018d8` [`0x140500b08`] sends context 1 to `0x140501749` [`0x140500979`], which refuses `wepParamChange` 2, 3 and 4 and
   accepts 1. Seppuku 1755 and Blood Grease Right 3190 are both stateInfo 152, spCategory 162,
   `wepParamChange` 1. Their on-attack rows are 1756 and 3191, each stateInfo 6 with
   `bloodAttackPower` 30. Left-hand rows (1758, 3194; `wepParamChange` 2) are refused, which
   matches the user's "main hand". `USER` + `VERIFIED`.
7. **Repeated procs on everyone in range.** `INFERRED`. The smoke reaches 15 m and hits every
   enemy in it. `0x1403960b0` [`FUN_1403960a0`] opens a timed `DmgHitRecord` only when
   `dmgHitRecordLifeTime` is above 0, and the smoke's is 0 with no shared list. The static read
   (`weapon-buff-leak.md`, "Smoke spawn gate and re-hit cadence") then finds a lazily made record
   whose entries never expire, which predicts one hit per victim per smoke. The live trace saw 115
   smoke hits in 3.96 s, so either many victim/smoke pairs or a reset not yet found; open. Bleed's re-proc
   lockout is 1 s (memory `er-status-reproc-lockout-spcategory-2026-09-29`), so each enemy in the
   cloud would proc about once a second for as long as it keeps building 30 per hit.

## What makes the Piquebone smoke different (2026-10-04)

Two of the predictions below failed in game (`USER`, 2026-10-04): Eruption's puddles after a swap
and Soporific Grease put no sleep on a sleepable enemy, and the Poison Mist incantation followed by
Blood Grease put no bleed on a bleedable one. Both failures, and the Piquebone success, come down to
two gates. Neither is a Bullet field and neither involves the weapon held at the hit. Run
`python3 scripts/interactions/carrier_diff.py` for the full field diff.

| | Piquebone smoke 20003309 | Poisonous Mist ash cloud 2416 | Eruption puddle 2019 | Poison Mist incantation cloud 10722001 |
|---|---|---|---|---|
| launched by | arrow BEH, e.g. Rain of Arrows 105040851 | BEH 300000162 | BEH 300000042 (TimeAct judge 3042) | Magic 7220, bullet by id 10722000 |
| hit context byte | 1 (`BehaviorParam.category`) | 1 | 0 | 4 (`Magic.spEffectCategory`) |
| gate 1: context admits `wepParamChange` 1 | yes | yes | no: context 0 takes only 0/5/6 | no: context 4 takes only `miracleParamChange` rows, and none of the 60 stateInfo 152 rows set it |
| AtkParam_Pc row | 0 | 20 | 30000042 | 72200 |
| `statusAilmentAtkPowerCorrectRate` / `_byPoint` | 100 / 100 | 0 / 0 | 0 / 0 | 100 / 100 |
| gate 2: buff's status buildup scale | 1.0 | 0 | 0 | 1.0 (gate 1 already failed) |
| status from your grease | yes (`USER`) | none predicted | none (`USER`) | none (`USER`) |
| status the cloud applies on its own | lure 482/483, no status | poison 834 (120 per hit) from `spEffectId0` | none | poison 1722000 from `spEffectId0` |

Gate 1 `VERIFIED`: the context byte is ADI+0xda, written from the launch (see the table under
"Which launches can carry a right-hand buff"). Offsets checked against the paramdef:
`wepParamChange` +0x158, `magParamChange` +0x160 bit 7, `miracleParamChange` +0x161 bit 0,
`shamanParamChange` +0x259 bit 5, each read by the matching case of `IsApplicableForCategory`
`0x140501700` [`0x140500930`].

Gate 2 `VERIFIED`, and it was missing from every earlier version of this page:

- `0x140d26290` [`FUN_140d24b10`] writes ADI+0x13c = AtkParam `statusAilmentAtkPowerCorrectRate` x 0.01 and
  ADI+0x140 = `statusAilmentAtkPowerCorrectRate_byPoint` x 0.01.
- In `CalculateDamage2`, right after `0x1404f7fb0` [`FUN_1404f71e0`] returns the buff's on-hit id
  (`0x140449372` [`0x140448e12`]): if that on-hit row has byte +0x259 bit 0 set (`isUseStatusAilmentAtkPowerCorrect`;
  Ghidra's struct mislabels it `isCheckAboveShadowTest`), the rate passed on is
  ADI+0x140 x ADI+0x13c x the hit's rate.
- That rate reaches `0x1403e8e70` -> `0x1403fb010` -> `0x14043e050` [`FUN_1403e8c90` ->
  `FUN_1403fade0` -> `FUN_14043daf0`], which builds
  `row.<status>AttackPower x rate x ADI.finalStatuses.<status>` (stateInfo 2 poison, 5 rot,
  6 bleed, 0x74 frost, 0x104 sleep, ...).
- 48 of the 54 weapon-buff on-hit rows set the bit: every grease (3191 bleed 30, 3151 sleep 33,
  3141 frost 63, ...), Seppuku's 1756, and the mist skills' own 882/880. The six that do not are
  1521, 1511 (Stormhawk Axe), 3182 (Drawstring Poison Grease Left), 13451, and Black Flame Blade
  1626001 / Bloodflame Blade 1632001.

So the Piquebone smoke works because the arrow's BehaviorParam row is category 1 and the smoke's
AtkParam_Pc row 0 keeps both status rates at 100. Most skill bullets set those rates to 0, so they
pass a grease's on-hit row to the enemy with zero buildup even when the context admits it.

### Is the swap required?

No. The reader walks the attacker's whole SpEffect list (`dealer->specialEffect`) at each hit and
filters by context, hand byte, arm style (context 12) and sub-category mask; no weapon id is
compared anywhere in `0x1404f7fb0` [`FUN_1404f71e0`] or `IsApplicableForCategory` (`VERIFIED`). A grease or
Seppuku buff is a character-wide SpEffect entry tagged with a hand (`wepParamChange` 1 right,
2 left), not something stored on the weapon. The swap in the measured case is there only because
no bow or crossbow can hold a grease (`isEnhance` 0 on every bow, light bow, greatbow and crossbow
row, `VERIFIED`) and a grease does not stay on a weapon you swap away from (`USER`: the grease was
applied after the swap; the removal itself is not traced).

Every arrow BehaviorParam_PC row is category 1, whichever hand holds the bow (1,247 ammo rows are
category 1; the 205 category-2 rows are all bolt judges 4xx, `VERIFIED`). So the no-swap version
is: bow in the left hand, a greasable weapon in the right with the grease or Seppuku already on it,
two-hand the bow, fire Rain of Arrows with Piquebone arrows. `INFERRED`, untested: it rests on the
right-hand buff entry staying live (flags `& 0x800c0003` clear) while the left bow is two-handed,
and those flag bits are not traced.

The only skill bullet with a window of at least 1 s that passes both gates for a right- or
left-hand grease on a weapon that can hold one is Firebreather (ash 223; bullet 2631, 3 s,
0.6 m, endless with record 1.0; context 12 from BEH 300000590, context 2 from 300000596;
AtkParam_Pc 300000591 rates 100 / 100). That is the skill's own breath carrying your grease, not a
lingering field, and the ash allows Standard, Heavy, Keen and Quality, which keep `isEnhance` 1.

## What Seppuku's self-bleed does, and why it is not the spreader

`VERIFIED`. Seppuku 1753 (`bloodAttackPower` 9999) procs bleed on the player. Every bleed row
cycles SpEffect 500 "Blood Loss (Cycled)" (stateInfo 467, `behaviorId` 2100, dmypoly 220) onto
the bleeding character: `0x1404fbc10` [`FUN_1404fae40`] applies `cycleOccurrenceSpEffectId` to
`container->owner`. `0x1404fb840` [`FUN_1404faa70`] then fires BehaviorParam 2100 -> Bullet 1000 "Blood Loss -
Bullet" out of that character's own `chrBulletShooter`. Bullet 1000 is a 0.5 s, 7 m sphere with
AtkParam 2 ("Impact: Oppose and Self") that applies 501 "Presence of Blood" (stateInfo 379, the
Lord of Blood's Exultation trigger). The launch passes equip slot 0xc, and
`PlayerIns::GetEquipmentEntryParamId` `0x1406577b0` [`0x140656960`] returns -1 for it, so the burst carries no
weapon. BehaviorParam_PC 2100 is category 0, which takes the switch's default branch: only
`wepParamChange` 0/5/6 rows pass there (`IsWepParamChange056` `0x140d52740` [`0x140d50990`]), and the
right-hand 1755 does not. So the player's blood burst carries no buff, and a grease reproduces the
effect without it. An enemy's own bleed proc fires an enemy-owned burst (NPC BehaviorParam 2100,
category 6) with no buff to carry, so procs do not chain from enemy to enemy.

## Not proven

- Smoke re-hit cadence with `dmgHitRecordLifeTime` 0 and `isEndlessHit` 0 (step 7): the static
  read predicts one hit per victim per smoke, the live trace saw 115 hits in 3.96 s. Settle it by
  logging the victim (`[RCX+0x8]`) and the bullet (ADI+0x1d8) per `CalculateDamage2`
  `0x140448910` [`0x1404483b0`] call and counting distinct pairs.
- Why lock-on is needed. Not the `launchConditionType` gate (answered above: the smoke spawns
  wherever a falling arrow lands).
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
`0x14038e220` [`FUN_14038e210`] takes it from one of three places, by what the bullet spawn data carries:

| spawn data | context byte | values in the regulation |
|---|---|---|
| no explicit bullet id (+0x14 == -1) | `BehaviorParam.category` (+0x1c) | 0, 1, 2, 4, 5, 9, 12 |
| goods id (+0x18) | `EquipParamGoods.spEffectCategory` (+0x40) | 0, 5 |
| magic id (+0xc) | `Magic.spEffectCategory` (+0x28) | 3 (sorcery), 4 (incantation) |

The whole `IsApplicableForCategory` switch (jump table `0x1405018d8` [`0x140500b08`], index = context - 1; context
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

Correction (2026-10-04): the mist clouds pass gate 1 but fail gate 2 (see "What makes the
Piquebone smoke different"). Their AtkParam_Pc 20 has `statusAilmentAtkPowerCorrectRate` 0 and
`_byPoint` 0, so a grease, Seppuku or the skill's own buff (831 Poisonous Mist / 826 Chilling Mist,
on-hit 882 / 880, both flagged `isUseStatusAilmentAtkPowerCorrect`) reaches the enemy with zero
buildup. The poison and frost the cloud does apply come from its own bullet SpEffect 834 (poison
120) / 829 (frost 120), which the slot loop applies without that scale. Prediction: Poisonous Mist
ash plus Blood Grease, in either order, gives no bleed from the cloud. The earlier text here said
the skill's buff replaced your grease and spread 882 / 880; the replacement is still `INFERRED`,
but nothing the buff carries survives gate 2 either way.

### (b) Lingering, re-hitting damage bullets that carry the buff

Correction (2026-10-04, after the user's Eruption + Soporific Grease test failed): Eruption is
not one. Its TimeAct (anim 40000, frames 62-64) fires judge 3042 = BEH 300000042, category 0, ->
2012 -> 2013 -> 2018 (five globs) -> 2019. No TimeAct names the category-1 rows 300000041 and
300000048; the earlier table tied 300000048 to Eruption only because it launches the same bullet
2018, and the scan has stopped making that link. 2019 fails both gates: context 0 refuses
`wepParamChange` 1, and its AtkParam_Pc 30000042 has both status rates at 0. Greasing the
weapon that casts Eruption instead of swapping changes neither. Rows `VERIFIED`.

The only bullet in this group that passes both gates on a weapon that can hold a buff is
Firebreather's breath (see "Is the swap required?").

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

Gate 2 (2026-10-04) splits that table. Status scale from the bullet's AtkParam
(`statusAilmentAtkPowerCorrectRate` x `_byPoint` / 100^2): Ghostflame Ignition 1.0, Zamor Ice
Storm 1.0, Spear of the Impaler 1.0, Frenzyflame Thrust 0.35, Rolling Sparks 0.01, Bloodboon
Ritual 0, Bloodfiends' Bloodboon 0. A zero means no grease status even with a swap. Fires of
Slumber is 0.078 and Soul Stifler 0.

Two zero-damage clouds with the Piquebone shape are on unbuffable weapons as well: Soul Stifler
(Winged Greathorn; BEH 301511900, category 12 -> 2065, 9.0 s, radius 0.1 -> 5 m, record 0,
`spEffectId0` 1545) and Fires of Slumber (St. Trina's Torch; 2826, category 12 right-hand and 2
left-hand rows, 5.0 s, 1 m, endless, record 0.4). A buff applied after a swap does ride a bullet
fired before it (the measured Piquebone case), so a swap is not what rules these out; gate 2 is.

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
  Storm, Smithing Art Spears, and Eruption's first zero-damage burst 2011. These would spread the
  buff once per enemy per bullet, but Stormcaller (AtkParam_Pc 300000643), Storm Assault and 2011
  have both status rates at 0, so gate 2 zeroes them.
- Shriek of Milos (Sword of Milos, unbuffable): 2960 / 2961, zero damage, 10 m, but 1.0 s.
- Rows with no player source (`VERIFIED`: no named weapon owns the variation): 2171 (BEH
  102001910, variation 2001, 8 s endless damage) and 1000000 (BEH 103490910, variation 3490 whose
  weapons 34900000 / 34960000 have no name; a scarlet rot cloud). Catalyst-variation rows that
  fire spell bullets under category 1 exist (Greyoll's Roar 103400920 under the Finger Seal
  variation, Rejection, Rancorcall, Carian Phalanx), but the spells themselves launch through
  `Magic.refId1` (Greyoll's Roar 7090 -> 10709000). Whether those behavior rows are ever used is
  not traced, and Greyoll's Roar is single-hit anyway.

### Not proven, for these

- Re-hit cadence. Corrected 2026-10-04: `0x1403960b0` [`FUN_1403960a0`] is called once when the
  fly state starts (`0x1403abfd0` [`FUN_1403abfc0`], a fresh damage entry) and again from
  `0x1403abf40` [`FUN_1403abf30`] only while the radius spreads, throttled to 1/6 s, passing the
  previous handle each time, so the new entry inherits the old one's hit lists. With record 0 the
  hit list is made on the first hit and never ages, which predicts one hit per victim per cloud
  for the Piquebone smoke, White Shadow's Lure and Soul Stifler. That conflicts with the 115
  live smoke hits; see `weapon-buff-leak.md`.
- No combination above was tested in game.
