# Simulated-player AI: Moonrithyll spec

Her brain is rebuilt from the ground up. It replaces her think id's logic (`common10000_Logic`) and
battle goal (29999 `Activate`/`Interrupt`) instead of reweighting the stock GeneralNPC acts, and it
keeps only the lab plumbing: spawn, team 47, ChrAsm gear writes, the CalculateDamage2 hit
measurement, and kill-confirm. The aim is an NPC that plays like a player with her gear.

Answers come from the user (grill session 2026-10-05). The status column says what is already
measured and what still has to be found before the rule can be built.

## Gear

| Rule | Status |
|---|---|
| She wears a planner build: currently "Ordinary Bean" (`?b=98f687a96d43b1`) minus the Albinauric Mask | Written into both ChrAsm copies at spawn; `ai:GetEquipWeaponId(TARGET_SELF, 1, 0)` reads back 12531125 (measured in-process) |
| Weapons carry the build's real Ashes of War (Carian Retaliation, Bloodhound's Step, Poisonous Mist) | Done the player way: spawn-npc.js mints a weapon gaitem with the gem mounted (`lab_equip(think, slot, id, gem)`) and writes its handle into ChrAsm `gaitem_handles`. Her AI then reads `GetArtsID` 228 Poisonous Mist on the right hand and 305 Carian Retaliation on the left (measured in-process). Whether the skill animations play in a fight is unproven |
| Grip follows the build: a strength build whose main weapon scales with strength is two-handed | Controlled by her AI, the way a player does it: the `NPC_ATK_ChangeStyleR` button input. ChrAsm arm style read 3 (right two-handed) mid-fight and 1 (one-handed) out of combat (measured in-process) |
| Face uses the build's `faceData` sliders | An NPC's face comes from CharaInitParam `npcPlayerFaceGenId` (FaceParam 2024320 for her), converted into the same 288-byte face buffer the player has, at PlayerGameData +0x768. spawn-npc.js applies the planner blob at creation with the game's own `CS::PlayerGameData::CopyFaceDataFromBuffer` (1.17.1 `0x140261010`), the call er-build-import-runtime uses on the player (`lab_face(think, hex)`). Measured in-process: 149 of 288 bytes changed and the buffer now equals the build's blob. Seen on screen by the user 2026-10-05: her face matches the build's character |

## Weapons

- She fights with her current gear and swaps only for specific situations.
- Misericorde (usually Bloodhound's Step or Endure): swapped to for escaping to heal, and for a
  riposte after a successful parry.
- A back-slot shield is for status resistance. If it has a parry ash, she swaps to it for a
  low-risk, high-reward parry.

## Defence

- She parries only if the shield she would use has a parry Ash of War. A shield without one is a
  blocking shield.
- Low-risk, high-reward parry, all of these at once:
  - only one enemy near her
  - a slow, telegraphed swing
  - an enemy whose attack can actually be parried
  - healthy enough that a failed parry's hit would not kill or nearly kill her
- She rolls only when an attack would land; otherwise she moves away from hostile targets.

## Offence

1. Use the attack with the least commitment.
2. Mid-attack, judge whether she needs to roll anything coming from her opponents.
3. Then check her health to see if she needs to heal.
4. Then go back to the least-commitment attacks.
5. Exception: commit to a heavy attack when it will hit more than one target.
6. Kill-confirm stays: no follow-up swing after one predicted to kill (validated 2026-10-05,
   two lethal predictions, both targets died).

## Healing

- Disengage below 50% HP: roll away only when attacks would land, otherwise run from hostile
  targets while facing them, heal, then return to combat.
- Flasks: one for FP and the rest for HP. Refilled when the player rests at a Site of Grace. FP is
  topped up beyond that by Starlight Shards. FP itself comes later.
- Her heal is goods 50201 (CharaInitParam 2024320 `item_03`, count 1), in shortcut slot 2
  (`ai:GetEquipItemId(TARGET_SELF, 2, ITEM_SLOTTYPE_SHORTCUT)`). It applies SpEffect 19391,
  `changeHpRate` -30, a 30% max-HP heal: 2512 of her 8375, the exact jump measured in her first
  fight. The count is the quantity of her inventory entry for it (PlayerGameData +0x2b0
  EquipGameData, +0x158 EquipInventoryData, 24-byte entries: gaitem handle, item id, quantity).
- Done in spawn-npc.js: 13 heals written at spawn (measured 1 -> 13). Drinking the last one removes
  the entry, so a refill with no entry goes through the game's own inventory add (`0x140246480`,
  goods handle `0xb0000000 | id`), on the frame tick; measured adding 2 to a stack of 13 -> 15.
- Grace rest: the player's animation enters 68010 (sit down) then 68011 (seated), measured on the
  player's TimeAct queue. spawn-npc.js emits `grace-rest` on entering it and refills her to 13
  (fired live, `why: sit`). A rise in the player's flask total (goods 1000..1099) is the backup
  signal, for a respawn at a grace; it alone misses a rest taken with full flasks.

## Stamina

- Obeys stamina like a player: no attacking, rolling or blocking on an empty bar; back off to
  regenerate when low.
- Her stamina is `ai:GetSp(TARGET_SELF)` (197 at rest, measured in-process); the stock battle goal
  already reads it to skip planning when it is 0.

## Targeting

- She fights whoever threatens her.
- She never swings at a target that cannot be hit: one that is dead or dying, or one in the middle
  of being backstabbed or riposted (user report 2026-10-05: she swings a lot at both).
- Dead or dying: `ai:GetHp(TARGET_ENE_0)` is 0. kill_confirm.lua already drops every attack goal
  of an act whose target reads 0 HP (phase `dead-target` in the lab log); unproven in a fight.
- Mid-critical: a ChrIns's throw module (modules +0x88, fromsoftware-rs CSChrThrowModule) holds a
  throw node whose `ThrowNodeState` is 4 `InThrowTarget` or 6 `DeathTarget` for the victim. Which
  node word holds it is not measured: every character read quiet (words 0x58..0x7c stable, the
  word at +0x6c reading 1 on NPCs and 2 on the player). spawn-npc.js emits `throw-change` for any
  character within 30 m whose throw words change, so the first backstab or riposte near her names
  the word.
- Every character is enumerable: WorldChrMan `chr_sets[196]` at +0x1dee8 (just below
  `main_player` +0x1e508), ChrSet capacity +0x10, entries +0x18, 16-byte entries. Measured 160
  characters; `nearby(radius)` gives each one's NpcParam, team, HP, distance, animation and throw
  words. Her AI's `TARGET_ENE_0` is not her PlayerIns lock-on handle (+0x6b0 stayed empty through
  a fight), so her target is matched by distance: Frida pushes the distances of the characters
  that cannot be hit, and her Lua compares `ai:GetDist(TARGET_ENE_0)` against them. The same
  enumeration answers "only one enemy near" and "hits more than one target".

## Reading an enemy's attack

- Any ChrIns's current animation and how far into it: modules +0x18 CSChrTimeActModule,
  `anim_queue` at +0x20 (ten 16-byte entries: anim id, play time, -, length), `write_idx` +0xc0.
  Read live on her (anim 12020013, 1.074 s of 1.333 s; measured in-process via `peekAnim`).
- Windup per enemy attack: spawn-npc.js emits `incoming` for every hit she takes, with the
  attacker's NpcParam id and the animation and play time at the hit; that play time is the
  attack's windup. Measured in one fight:

  | Attacker NpcParam | Animation | Hit lands at |
  |---|---|---|
  | 51601099 | 1003001 | 0.56-0.62 s |
  | 51601099 | 1003002 | 0.97-1.14 s |
  | 51601099 | 1003004 | 0.54-1.32 s (several hits) |
  | 52404099 | 3014 | 2.2-3.0 s |
- The engine fires `Interrupt_FindAttack` on her goal when an enemy starts an attack, and
  `Interrupt_ParryTiming` at a parry moment.
- Parryability is per attack: AtkParam `isDisableParry`, plus NpcParam `parryAttack` /
  `parryDefence`. scripts/er-moveset-table-gen.py already resolves an enemy animation's TimeAct
  attack events to their AtkParam rows, so a per (enemy, animation) table of windup start and
  `isDisableParry` is an extension of that generator.

## Out of combat

- Circles the immediate area, and after pacing a bit, holds at a chokepoint: a narrow passage that
  sits between the player and the danger.
- When the player leaves the area, she abandons the chokepoint, follows, and finds a new one
  where the player stops.
- Finding one: `ai:GetExistMeshOnLineDist(TARGET_SELF, AI_DIR_TYPE_*, 50)` gives the walkable
  distance from her in a direction (measured 4.07 m ahead, 9.09 m left, 7.15 m right), and
  `GetExistMeshOnLineDistSpecifyAngle` takes any angle. While she circles, the passage width where
  she stands is left + right across the line from the player to the danger; Frida records her
  position with each width, and the narrowest spot on the segment between the player and the
  nearest hostiles (from the character enumeration) is where she holds.

## Unknowns to measure before building

| Unknown | Needed for |
|---|---|
| Which throw-node word holds `ThrowNodeState` (waits on a backstab or riposte near her; the sampler is armed) | Skipping mid-critical targets |
| The per (enemy, animation) windup and `isDisableParry` table (being generated) | Parry window, "would this attack land" |

## Co-op sync

- She is summoned the spirit-ash way so a Seamless peer sees her: `CreateSummonChr`, then the
  `BuddyGenerator` tail (take local net control, broadcast the summon packet). spawn-npc.js
  `path: 'summon'`, `announceSummon()`. Seen by the user's Seamless partner 2026-10-05, with Seamless
  `allow_summons = 1` on both sides.
- The peer builds her from CharaInitParam, so her minted ashes and face are local only.
- She is removed with `NotifyBuddyUnsummon`, never `RemoveChrIns` (that crashed the game).
- Open: she fights only after being hit, not when an enemy approaches.

## Control surface

Her whole brain is Lua. `common10000_Logic` (010000_logic) only adds the battle goal through
`COMMON_EasySetup3`, and `common10000_Interupt` returns false. Battle goal 29999 is a goal table
(`RegisterTableGoal`) of Initialize, Activate, Update, Terminate and Interrupt. Replacing those for
her think id covers every decision; the only other Lua that runs is the Interrupt of whichever
common sub-goals her brain adds (read from the decompiled scripts).
