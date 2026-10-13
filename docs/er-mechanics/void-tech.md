# Void tech: jump casts and jump projectiles that fire twice on landing

Labels as in the other files here: **VERIFIED** = traced EXE code or game data, **MEASURED** =
runtime trace on 1.17.1 (`scripts/frida/void-trace.js`), **BEHAVIOR** / **TAE** = the
`c0000.behbnd` graph and decoded TimeAct, **COMMUNITY** = videos, threads and patch notes,
**INFERRED** = a reading whose consumer was not traced.

Tool: `scripts/er-mechanics-voidtech.py` (`--scan` lists every candidate, `--selftest` passes
12/12). Runtime instrument: `scripts/frida/void-trace.js`, run against the AI lab's test hostiles
(the lab lives on the `feat/ai-lab-turtles` branch). The tech as an AI feature is section 9.

## 0. In plain words

A jump cast or a jump attack that fires a projectile plays one clip in the air and switches to a
landed clip when the character touches down. Both clips carry the same "spawn this" event on the
same frame. If the landing happens on exactly that frame, both clips fire it: the projectile (or
the whole spell volley) comes out twice, one frame apart, and both copies are sent to the other
player. Nothing else about the timing matters: not the apex, not a late press. Melee jump
attacks cannot do it, because the landed clip keeps the air clip's attack instead of starting a
new one.

## 1. What was claimed (COMMUNITY)

| claim | source |
|---|---|
| A jump R1 "will sometimes do twice the damage", frame perfect, "at the last possible moment when at the highest point of your jump" | Snapcaster D, "HTS Double-Hit Glitch / Tech", 2023-03-29, https://www.youtube.com/watch?v=ViaD5k_AZQs |
| Swift Glintstone Shard jump cast takes FP twice and hits for 728; works with fast attacks (HTS jump R1, Swift Shard, Catch Flame); "the frame of damage [is] the same as the landing frame" | Steelovsky, "DOUBLE YOUR DAMAGE with the VOID TECH", 2023-04-04, https://www.youtube.com/watch?v=yMJjIKv9xGo |
| Bestial Sling from a falling jump double-casts "every single time" | same video |
| Patch 1.08 (2022-12-07): "Fixed a bug that could cause multiple damage instances when certain Spells and Incantations were casted while jumping." | https://www.thefpsreview.com/2022/12/07/elden-ring-1-08-update-adds-colosseum-mode-new-hair-styles-balance-adjustments-and-more/ |

Both videos postdate 1.08, so that fix did not close it. No later patch note names it; patch 1.09.1
(2023-04-17), the nearest to "the patch" in the second video, lists no jump fix. Earlier Bestial
Sling jump-cast reports go back to May 2022 (Steam threads 3279194062595456072,
3279194170710105289).

## 2. The rule (MEASURED, 1.17.1)

Every input was frame-exact: the jump from the AI (or the drive), the R1 or cast written into
`CSChrActionRequestModule` +0x10 on entry to `UpdateFromManipulator` (1.17.1 0x140408190) at a
chosen frame after takeoff, swept 1..14. A cast or projectile is counted by its bullets
(`CSBulletManager::SpawnBullet`, 1.17.1 0x1403a2cb0), never by FP: an NPC's FP refills the frame
after it is charged and drops without a cast.

| subject | spawn event on the landing frame | doubled | spawn event on any other frame | doubled |
|---|---|---|---|---|
| Bestial Sling, NPC | 3 | 3 | 31 | 0 |
| Smithscript Dagger 2H jump R1, NPC | 9 | 9 | 58 | 0 |
| Smithscript Dagger 1H jump R1, the player | 2 | 1 | 25 | 0 |
| straight sword and Sword Lance jump R1 (melee), NPC | - | - | 88 | 0 |

A double is the same spawn on the landing frame and again on the next frame. Spawns one frame
before landing (still in the air clip) or one frame after (already in the landed clip) never
doubled. The apex does not appear in the rule: the press only matters through where it puts the
spawn frame relative to the landing.

Melee: across 88 jumps (straight sword one-handed, Sword Lance two-handed, presses 1..10 frames
after takeoff) each jump created exactly one attack and dealt at most one hit, including hits on
the landing frame. The landed clip's attack event finds the air clip's attack still in the same
slot with the same behavior id and reuses it, hit list included (create-or-reuse 1.17.1
0x140442e50; `void-trace.js` header).

## 3. Damage of a double (MEASURED)

| subject | hits per normal jump | doubles that added a hit |
|---|---|---|
| Bestial Sling (bullets 10680000 x11 stones, 10680001 x3, both AtkParam 68000, `dmgHitRecordLifeTime` 999) | 2 (one per bullet row) | 2 of 2 (3 hits) |
| Smithscript Dagger (behavior 106200350 / 106200353) | 2 | 2 of 9 |

The second copy flies one frame behind the first. When it connects it deals full damage; a "half
hit" was not seen.

## 4. Who can do it: every action with a spawn event in both landing clips (TAE, BEHAVIOR)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-voidtech.py --scan --var JumpAttack_Land`

| family | which | button | spawn frame, air / landed |
|---|---|---|---|
| jump casts | Bestial Sling | cast | 16-17 / 16-17 |
| | Swift Glintstone Shard, Night Shard | cast | 10-11 / 10-11 |
| | Carian Slicer | cast | 12-14 / 12-15 |
| | Catch Flame | cast | 15-16 / 15-16 |
| | Black Flame Blade | cast | 15-16 / 15-16 |
| jump attacks that fire bullets | longbows, shortbows | 2H R1 | 6-13 |
| | crossbows, Pulley Crossbow, Spread Crossbow | 1H R1 | 12-23 |
| | Smithscript Dagger, perfume bottles | R1 and R2, 1H and 2H, powerstance | 7-20 |
| | Smithscript Axe, Smithscript Cirque, Claws of Night | R2, 1H and 2H | 13-18 |
| | Dryleaf Arts, Dane's Footwork | R2, 1H and 2H, powerstance | 13-23 |
| Torrent jump attacks | Smithscript weapons, Dryleaf / Dane's, Claws of Night | (`RideJumpAttack_Land`) | 9-18 |

Spells resolve their TAE as `a<400 + MagicParam.refType>`; weapons by `wepmotionCategory`, or
`spAtkcategory` for the a2xx uniques. One family member per event type was run: casts (type 64)
with Bestial Sling, bullets (type 2) with the Smithscript Dagger; the rest share those code paths.

Two more selectors switch clips mid-action and carry a spawn event in both clips (section 7):
Spinning Strikes' stance loop (a611 040051 -> 040052 on `LocomotionState`, bullet judge 4020 every
4 frames) and the sprinting item use (a000 050190 -> 050191 on `ItemDashSpeedIndex`, item use on
frames 22-23). They go through the same switch (section 5), so they double by the same rule.

## 5. Why it fires twice (VERIFIED decompile, MEASURED clock)

How a clip fires its events (1.16.2 addresses, the named dump):

- `CustomManualSelectorGenerator::update` (1.16.2 0x1419b82e0, 1.17.1 0x1419ba150) keeps the
  child clip's previous local time at node +0xe0 and its new one at +0xe4, and every update fires
  the TAE callback for the clip at +0xec over the window (previous, new]
  (`fireTAECallback` 0x1419b7640 -> `TAE_Callback` 0x14041b740 -> `RunTaeAndUpdateAnimQueue`
  0x140430960 -> `RunExecutorThreadOne` 0x14042d5a0).
- `GetActiveAnimEvent` (0x14264e420) returns every event with `start <= to && from < end`, and
  marks `firstExecution` when `start > from`. There is no per-event "already fired" flag.
- The spawn handlers act only on `firstExecution`: `BulletBehavior` (type 2, 0x140426e60) returns
  early otherwise (unless its repeat flag is set), `CastHighlightedMagic` (type 64, 0x140429db0)
  passes it on and casts only with it. So a spawn fires once per clip, on the update whose window
  first contains its start.

What landing does (MEASURED, `tl` events in `void-trace.js`, the player's Smithscript Dagger jump,
19 landings): the air clip and the landed clip are two different selector nodes. On the landing
frame the air node fires its last window; on the next frame the landed node fires its first
window, starting not at 0 but near the air clip's time. The two windows do not meet exactly:

| landed clip's first window start vs air clip's last window end | landings |
|---|---|
| earlier, overlap 0.001-0.040 s (a frame is about 0.029 s) | 18 |
| later, gap of 0.005 s | 1 |

A spawn event whose start falls inside the overlap is "first" for both nodes, so it fires on the
landing frame from the air clip and on the next frame from the landed clip. That is the double,
one frame apart, and only for spawns on the landing frame. Spawns earlier in the air are already
behind the landed window's start; later ones are only ever in the landed clip. The size of the
overlap varies landing to landing, which is why 12 of 13 landing-frame spawns doubled and not 13.
The one gap means an event in it would fire from neither clip (not seen with a spawn in it).
Where the landed clip's start time comes from (VERIFIED hkx, TAE and 1.16.2 EXE; the formula
MEASURED on all 19 landings, every one within 0.0001 s):
- The landed clips (`a437_045074_hkx_AutoSet_00/_01`, and the weapon ones alike) bind their
  `hkbClipGenerator.startTime` to the behavior variable `TimeActEditor_10`; the air clip has no
  such binding.
- The air animation writes that variable every frame with TAE event 605, index 10 (handler
  0x140426b30, queued into `CSChrBehaviorModule` +0x1690 through 0x14041bf30 / 0x14041d8e0, named
  `TimeActEditor_%02d` by 0x14041e690). Its args are A (+8) and B (+0xC) over the event's window
  [s, e): value = A + (B - A) * min(1, (t - s) / (e - s)), a time remap from the air clip to the
  landed clip.
- So landed start = F(air clip's time at the start of its last window). The tables:

| air clip | window | A -> B |
|---|---|---|
| a053 031030 (Smithscript Dagger jump R1) | 0-0.2333 / 0.2333-0.6667 / 0.6667-1.0 | 0 -> 0.25 / 0.27 -> 0.64 / 0.64 flat |
| a438 045070 (Bestial Sling jump cast) | 0-0.3333 / 0.3333-0.5333 / 0.5333-1.0 | 0 -> 0.34 / 0.366 -> 0.53 / 0.80 flat |

The map runs slightly fast inside each segment and jumps ahead at each boundary, so the landed
clip starts up to about one frame behind or ahead of where the air clip stopped. With the air
clip's last window (p, p + dt], the landed clip's first window starts at F(p): every spawn event
whose start lies in (F(p), p + dt] fires from both clips. The `JumpAttack_Condition` transition
effect plays no part in the time: duration 0, no sync flags, toGeneratorStartTimeFraction 0, and
its activate (0x1419bd150) only sets the blend length.

Melee does not double for a different reason: an attack event fires on every frame of its window,
not only the first, and the landed clip's events find the air clip's attack in the same slot with
the same behavior id and reuse it, hit list included (create-or-reuse 1.17.1 0x140442e50).

## 6. Both copies reach the other player (VERIFIED, MEASURED)

- Sender: `CSBulletManager::SpawnBullet` (1.16.2 0x1403a2ca0) builds one sync entry for the
  spawn when BulletSpawnData +0x44 bit 8 is set and queues it for the network. Every bullet the
  player's dagger jump spawned carried flags 0x9, both copies of the double included (MEASURED).
- Receiver: packet 0x3f (`FUN_1403a9960`) walks the received entries; each makes one
  `CreateBulletSpawnData` (0x14038d480) and one bullet create (`FUN_1403a5a10`), the same create
  the sender's `SpawnBullet` uses.
- Hit records: each spawned bullet entry takes its own record (`FUN_14051ced0`, no lookup;
  lifetime from `dmgHitRecordLifeTime`, `FUN_1403960a0`), so both copies may hit the same target.
  The second copy flies one frame behind the first, which is why it often misses (section 3).

## 7. Which clip switches can do this (BEHAVIOR, HKS)

The scan finds 421 clip pairs over 17 selector variables. A pair can only double if its variable
changes while the state is playing and the selector follows it.

How a selector follows its variable (VERIFIED, 1.16.2; the Havok class is unnamed in the dump and
was found through its RTTI `.?AVhkbManualSelectorGenerator@@`, vtable 0x142d4b8e8):
- Layout, from the clone constructor 0x141479790 (the tagfile item has the same layout, 0x108
  bytes): generators +0x98, `selectedGeneratorIndex` +0xa8, `indexSelector` +0xb0 (null on all
  799), `selectedIndexCanChangeAfterActivate` +0xb8, `generatorChangedTransitionEffect` +0xc0,
  `currentGeneratorIndex` +0xe8, the index at activation +0xea.
- `hkbBehaviorGraph::update` (0x14141ac90) copies bound variables into each active node
  (`copyVariablesToMembers`) before calling its update, so `selectedGeneratorIndex` holds the
  variable's value every frame.
- Activate (0x141479d50) resolves the index once. Update (0x14147a6e0) re-resolves it only when
  +0xb8 is set; when the index changes it starts the transition effect at +0xc0 (if any) from the
  old child to the new one and activates the new child. With +0xb8 clear the activation-time child
  plays to the end.

Selectors by bound variable, flag at +0xb8 (1.17.1 `c0000.hkx`): `JumpAttack_Land` 59 of 59 set,
`RideJumpAttack_Land` 6/6, `LocomotionState` 21/21, `ItemDashSpeedIndex` 1/1,
`RollingMagicDirection` 4/4, `JumpAttack_HandCondition` 2/16, `SwingPose` 0/8; 664 of 799 overall.

| variable | written (c0000.hks, 1.17.1 line) | switches mid-action |
|---|---|---|
| `JumpAttack_Land` | `JumpAttack_Start_Falling_onUpdate` 23377-23386 (1, or 2 `Land_High` when `GetLandIndex` is `LAND_HEAVY`), F/D 23436/23481, `JumpCommonFunction` 23588/23681 | yes, on landing |
| `RideJumpAttack_Land` | `RideAttack_Jump_*_onUpdate` 22801-23019 (1 only) | yes, on landing |
| `LocomotionState` | `Update` 18681, every frame | yes |
| `ItemDashSpeedIndex` | `ItemDash_Upper_onUpdate` 15546 (0 -> 1 once) | yes |
| `SwingPose` | mid-state, but all 8 selectors latch at activation (byte 0) | no |
| `JumpAttack_HandCondition`, `RollingMagicDirection`, `Magic_SpecialStaffCategory`, `Magic_DuelingShieldCategory`, `SwordArtsOneShot*Category`, `SwordArtsSubCategory`, `ItemWeaponType`, `DrawStanceRightAttackLightCategory` | request path, before the state's event | no |
| `SwordArtsChargeCategory`, `SwordArtsSubCategory2` | in `onUpdate`, then an event to the next state in the same tick | no |

The `Land_High` clip is the same selector's third child, so falling from height goes through the
same switch: a high fall doubles under the same condition as a short one, not "every time".

## 8. Not established

Nothing. The two details an earlier draft left open are decoded: the landed clip's start time
(section 5, the TAE 605 time remap) and the selector's switch-after-activate flag (section 7,
+0xb8, read in the update at 0x14147a6e0).

## 9. Void tech as an AI brain feature (MEASURED, 1.17.1)

`er_npc_summons.dll` gives any Lua brain the tech. A brain calls `brain_void()`, and from then
on every jump its characters make gets the one press their gear can double, timed by the DLL;
`brain_void_act(ai, goal, range)` jumps, or first switches grip when only the other grip doubles.
The shipped brain `crates/er-npc-summons/brains/void.lua` engages any enemy target within 40 m,
runs in to 5 m and jumps; gear that cannot double leaves the stock AI untouched.

| part | where |
|---|---|
| which press a gear allows | `crates/er-npc-summons-core/data/void-table.tsv` (84 weapon jumps, 10 spells, 63 catalysts), written by `er-mechanics-voidtech.py --ai-table` from section 4's scan; `--selftest` fails when it is stale |
| press, landing, spawn | detours on `UpdateFromManipulator`, the attack create-or-reuse (behavior 550: takeoff, then refreshed every airborne frame) and `SpawnBullet` |
| timing | learned per action in game seconds: air time, press-to-spawn, and a bias from the spawn-minus-landing residual; a landing more than 0.1 s from the learned air time is ignored |
| a dropped press | pressed again every 4 frames until the jump attack animation plays, at most 6 times |

Measured with a Mimic Tear companion holding a Finger Seal and Bestial Sling, fighting world
enemies: 10 doubles in 47 jumps, then 5 in 19 on the final build. The ceiling is the landing, not
the press: the cast's spawn comes 0.57-0.59 s after the press, which has to go in 0.03-0.06 s
after takeoff, while landings scatter over 0.61-0.68 s, so the spawn lands on the landing frame
about one jump in three. The frames counted per jump are no clock: one 0.65 s jump counted 13
updates and another 22, which is why everything is learned in seconds. 8 of 19 jumps on the final
build never started the cast through any of their presses (the jump start animation, 202100, was
still playing at the last press).

## Commands

```
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-voidtech.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-voidtech.py --scan --var JumpAttack_Land
python3 /home/banon/projects/er-mods-rs/scripts/er-frida-up.py
uv run --with frida python3 /home/banon/projects/er-mods-rs/scripts/er-frida-watch.py --agent /home/banon/projects/er-mods-rs/scripts/frida/void-trace.js --config-json '{"drivePlayer":true,"attempts":28,"mask":1}'
```
