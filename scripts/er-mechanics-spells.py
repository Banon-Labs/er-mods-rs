#!/usr/bin/env python3
"""Attack-spell damage: what one cast of a sorcery or incantation does to a player, and the FP and
frames it costs, for one build's stats and catalyst.

    python3 scripts/er-mechanics-spells.py --stats int=80,dex=20 --catalyst "Carian Regal Scepter"
    python3 scripts/er-mechanics-spells.py --stats fth=60,str=20 --catalyst "Erdtree Seal" \\
        --talismans "Flock's Canvas Talisman" --sort full_per_fp
    python3 scripts/er-mechanics-spells.py --stats int=60 --catalyst "Glintstone Staff+25" --show "Rock Sling"
    python3 scripts/er-mechanics-spells.py --stats str=40,dex=40 --attack "Carian Thrusting Shield" --slot guard
    python3 scripts/er-mechanics-spells.py --stats str=40,dex=40 --attack "Smoldering Shield@Shield Bash"
    python3 scripts/er-mechanics-spells.py --selftest

Labels as the sibling docs use them: `VERIFIED` = read in the 1.16.2 named dump (addresses are
1.16.2), `REGULATION` = read from the installed params, `TAE` = decoded TimeAct, `INFERRED` = a
reading whose consumer was not traced.

# One hit (`VERIFIED`)

`FUN_1406832a0`, the per-hit attack power (the vtable slot `FUN_140651d40` calls for a player),
has two branches on `AttackInfo+0xf4`, which the dump names `attackNotFromWeapon`:

    set:    AR[e] = AtkParam.atk<e> x FUN_140690390(weaponParamId1, e) x durability x ctx-rate[e] + ctx-add[e]
    clear:  AR[e] = ((base[e] of both hands) x AtkParam.atk<e>Correction / 100 + isAddBaseAtk term)
                    x FUN_140690390(...) x ...   (the weapon hit attack-rating.md section 1 models)

`FUN_140690390` is the catalyst's own multiplier for element `e`: its `attackElementCorrectId` row,
replaced by the hit's `AtkParam.overwriteAttackElementCorrectId` when that is >= 0, that element's
`correctType_*` graph, STR x1.5 when `AttackInfo.isTwoHanding` is set and the AtkParam's
`isDisableBothHandsAtkBonus` is not, and the 0.6 penalty on an unmet requirement. That is
`er-mechanics-ar.element_multiplier`, which `spell_buff` already calls for the magic and holy rows
(Glintstone Staff +25 at INT 80 is 329.5, attack-rating.md section 4). So a fire incantation scales
with the seal's fire row, not its holy row, and the AtkParam correction fields are not read.
`durability` is `ItemEntryInfo::GetDurabilityConditionMultiplier`, 1.0 at full durability (the
caller passes it as `param_5`). For a bullet, `weaponParamId1` is the equipment in the casting
hand (`FUN_14038e380`: `GetEquipmentEntry(spawn+0x2c)`), so it is the catalyst.

A spell's bullet takes the first branch (`VERIFIED` 2026-11-09, 1.16.2 named dump, every function
byte-matched to 2.7.2.0): `FUN_14038e540` (2.7.2.0 0x14038e550) sets `BulletAttackInfo+0x198`
(`isGoodsOrMagicBullet`) when the spawn's magic id or goods id is >= 0, and the bullet's hit
(`FUN_1403960a0`, 2.7.2.0 0x1403960b0) copies that byte into the attack copy's `+0xf4`, which is
why no store to `+0xf4` shows in the bullet builders. Child bullets keep it (`FUN_14039bba0`
copies the parent's attack data). So Furious Blade of Ansbach and Scarlet Aeonia take it too,
whatever their `isAddBaseAtk`. A consumable's bullet takes the same branch with its
`EquipParamGoods.refVirtualWepId` as `weaponParamId1` (`FUN_14038e380`), so a thrown item
scales with the stats its virtual weapon names: Glinting Nail is 210 magic x 1.560 at INT 20,
x2.440 at INT 80. The other branch could never have been a spell's in any case: no catalyst has a
fire or lightning `attackBase*` (`--selftest` checks it over the regulation).

On top come the attacker's SpEffect `*AttackRate` and `atkPlayerDmgCorrectRate_*` rows the spell's
context admits (`Magic.spEffectCategory` -> `magParamChange` / `miracleParamChange`, then
`magicSubCategoryChange1..3` against the spell's subcategories), exactly as
`er-mechanics-cast-speed.CastModel` applies them for talismans and the held weapons' residents.
Then the defense step is `er-mechanics-defense.damage` with the hit's own
`FinalDamageRateParam` row (`AtkParam.finalDamageRateId`, applied when both sides are players,
`VERIFIED` 0x140686070). The physical type is the bullet's `atkAttribute` unless 254, else the
AtkParam's, 253/252 taking the catalyst's (defense.md section 2b).

# One cast (`TAE` + `REGULATION`)

The spell's TimeAct is `a<400 + Magic.refType>`. Clip `045000` is the start clip, which hands off
at the opening of JumpTable 93 (the cast-speed window, `er-mechanics-cast-speed`); `045010` is the
release; a spell with `Magic.mp_charge` > 0 is chargeable, and then `045010` is the charged release
and `045011` the tap. The data settles which is which: on every chargeable spell checked the
`045010` ref carries the larger hit (Lightning Spear 293 against 234, Comet 365 against 292).
Two exceptions, both read off the data (`TAE` + `REGULATION`, 2026-11-09). A hold spell
(`consumeLoopMP_forMenu` > 0, which `mp_charge` equals on every such row: it is the hold's per-tick
cost, not a charge) whose `045011` carries no TAE 64 event at all is one cast of `045010` at
`Magic.mp`: the ten breaths (Agheel's Flame, Borealis's Mist, Dragonfire, Dragonice, Ekzykes's
Decay, Ghostflame Breath, Glintstone Breath, Rotten Breath, Smarag's Glint Breath, Aspects of the
Crucible: Breath), Crystal Barrage, Gelmir's Fury, Meteorite, Meteorite of Astel, Spira and Divine
Bird Feathers. Their `045011` has gates but no cast, so it is the release-out, not a tap
(`INFERRED` from that alone). Crystal Torrent's `045011` casts, but ref 3, which its Magic row
leaves empty, so its tap lands only what its `045010` casts first (below). Loretta's Greatbow and
Mastery fire their charged arrow from `045021` (ref 5), their `045010` fires nothing. A hold spell
let go early plays 045010 up to its release (below): its 'early' variant.
The behavior script settles the path (`VERIFIED` 2026-11-10, c0000.hks decompiled,
/home/banon/er-extract/1172-20261021-hks-decompiled/c0000.dec.lua; bd
spell-tap-vs-charge-speffect-100610-in-045010-2026-11-10): `MagicLaunchRight_Upper_onUpdate` leaves
045000 for 045010 on `env(313)`, the JumpTable 93 opening, with no input test, so a tap and a
charge hand off together. In 045010 `MagicFireRight_Upper_onUpdate` plays 045011 on the first frame
its SpEffect `TAP_SPEFFECT` (100610) window is active and the cast button is up; held past the
window, 045010 plays on and casts the charge. So a tap is 045000, then 045010 up to the release,
then 045011 (`tap_release`, `_cast_events`): the release is the caster's choice of frame inside
the window, and a press let go before it waits for its first frame. The window opens at 045010's
first frame on most of the TimeActs that tap, so there the tap is 045011 from the hand-off, as
before; Rock Blaster (a457, f10-46), Messmer's Orb (a541, f16-26) and Divine Beast Tornado (a547,
f25-28) open it later, and their taps play 045010 to its first frame, casts included (Rock
Blaster's f3, f3 and f7, Divine Beast Tornado's f16). Without the window a hold spell
(`CheckIfHoldMagic`: a 045012 loop or `refType` 35, `hold_magic`) goes to 045011 at 045010's end
when the button is up (`env(339, 1)`, `INFERRED` the clip's end), so the taps of Comet Azur and
Crystal Torrent (a407), Unendurable Frenzy (a488) and Midra's Flame of Frenzy (a537) play all of
045010 first; with neither there is no tap. The release frame is the caster's choice, which the
defender cannot see coming: a tap is let go on each real frame of its window (`er-mechanics-ashes`
`release_frames`, weighed alike; its own nets read where they move, `release_read`), each one a
member of the group he cannot tell apart (`spell_lookalikes`: it parts
from the charge, and from a later release, where its own release shows, the first frame of the
blend into 045011 that puts a bone `VISIBLE_M` from 045010 held on, `release_shows`; a release he
sees before his decision he reacts to as it shows, `release_events`), and the tap nets their weighted
mean (`punish_values`; `INFERRED`: the caster draws the frame uniformly, as evenly as the group's
equal weights take him to; the switch lands on the release frame, not the next, the TAE and HKS
update order not being read). A window of one real frame is the earliest release alone, as
before. A hold spell whose 045010 has the window is also let go early, 'early' (`variants`): Dragonfire,
Dragonice, Glintstone and Rotten Breath (a467, f64-82), Agheel's Flame, Borealis's Mist, Ekzykes's
Decay, Ghostflame and Smarag's Glint Breath (a450, f69-91), Crystal Barrage (a404, f36-89), Gelmir's
Fury (a502, f34.3-115) and Aspects of the Crucible: Breath (a500, f55-93): 045010's casts before the
release land, 045011 casts nothing, and the caster rolls on 045011's own gate from the release
(Dragonfire: f49's breath at every release, f65's empty marker after f65, never f93's second breath;
`INFERRED`: a bullet made before the release flies its life whatever the caster plays next). A hold
spell without the window (Meteorite, Meteorite of Astel, Spira, Divine Bird Feathers, Surge, O
Flame) goes to 045011 only at 045010's end, after every cast the 'cast' scores, so it is not given
one: it would differ from the cast only in the recovery clip.
TAE type 64 `CastHighlightedMagic` fires `Magic.refId<n>`, n being its byte 8 plus one (COMMUNITY:
WitchyBND `TAE.Template.ER.xml` names it `RefType`). A ref is an `AtkParam_Pc` row hit directly
(`refCategory` 0, Carian Slicer), a `Bullet` row (1), or a SpEffect (2, no damage here).

Hits on one target are counted two ways, since no param says how many of a volley land on a moving
player:

- `one_hit`: the largest single hit the cast can land, the value of one connect.
- `full`: every hit the cast can put on a player standing in its path, counted per hit list. A
  target is hit once per list, and again every `dmgHitRecordLifeTime` s while a penetrating or
  endless member still touches it. A moving bullet touches it for the time it takes to cross the
  player's idle depth (0.564 m, reach.md section 3) plus its own diameter, at its slower of
  `initVellocity` / `maxVellocity`. A bullet that stands still or follows its owner (`FollowType`)
  touches it for its whole `life`. A trail child adds its life to its parent's pass. A list is a
  DmgHitRecord, handed down the bullet lineage (`VERIFIED` 2026-11-12, 1.16.2 named dump; bd
  dmghitrecord-inherited-down-bullet-lineage-1162-2026-11-12, er-effects-rs-7bye). A launch makes
  one record, which its whole `numShoot` volley shares, when its row has `isUseSharedHitList` or
  `attachEffectType` and it inherited none (FUN_14038fe80, for each shot entry FUN_1403a5a10
  makes). A child (hit, expiry or interval: `KillBullet`, FUN_14039baf0 and FUN_14039ab10, each
  through FUN_14039bba0) copies its parent's attack info, the record with it (FUN_14038b740,
  +0x638), so it joins the parent's list whatever its own flag, unless the parent's row has
  `attachEffectType`. FUN_14039bba0 then drops the copy, and reads that field on the row of the
  parent's current state (CSBulletIns +0x4a4), which is the parent's own. +0x4a4 is written only
  by `UpdateBullet` once a state starts (0x14039a2ca) and at the launch (0x14039e9ca), never by
  `KillBullet`, so on the expiry path it is still the fly or wait state. The states' rows are
  filled by FUN_14039ee00 with the base `SetBulletParamAndGetHitBullet` (all three vtables): the
  first state takes the bullet's own row, the next its `HitBulletID`. A row with
  `autoSearchNPCThinkID` that starts attached (`FollowType` 1, 2, 5: FUN_140399390) flies its
  `HitBulletID` row in the same bullet after its wait. Only Glintblade, Carian and Greatblade
  Phalanx do that among spell trees, and their rows carry no record. A bullet with no record has
  a list of its own. Children: `HitBulletID` one per parent
  bullet when it hits or expires, `intervalCreateBulletId` none when the parent is made, the first
  one mean interval after `intervalCreateWaitTime` and one every mean interval after that while
  the parent has life left (`er-mechanics-ashes.interval_frames`, `VERIFIED`): a stationary
  timer chain makes one star per timer, so Stars of Ruin fires twelve, Glintstone Stars three and
  Star Shower six, the counts their own `GoodsInfo` text gives (it used to count one more at the
  creation and three for Stars of Ruin's 0.1 s timers). Whether a `HitBulletID` child is made at
  all is `er-mechanics-ashes.launches` (`VERIFIED` FUN_14039da40, bd er-effects-rs-rggx): on a hit
  the parent's own `launchConditionType` decides, on expiry the child's. A child no ending makes
  on dry ground is not counted. Below a non-penetrating damaging bullet, a child its parent's
  value refuses on a hit continues a shot that missed. With no record inherited it joins that
  shot's list, since a shot that hits him ends there. So each star of the three star sorceries is
  one list: its first leg's 4 makes the second leg only when the first expires. Glintstone Nail
  and Nails fly as five such 0.2 s segments, one hit per nail, not five.

  How often a list hits him (`VERIFIED` 2026-11-12, 1.16.2 named dump):
  - Every bullet's attack carries two hit lists. One is its own (CSBulletIns +0xae0 is its
    lifetime, FUN_1403960a0; lifetime 0 makes one at the first hit that never lapses,
    FUN_140523540). The other is the shared record (+0x280, FUN_140527200).
  - A contact is dropped while he is in either (FUN_140523710 -> FUN_14051c5d0). A contact that
    passes the victim check puts him in both (FUN_140524910). Each entry lapses after its
    record's lifetime (FUN_14051cfb0 stores it, FUN_14051c610 ages it), and a lifetime of 0 never
    lapses.
  - Both lifetimes are the one the attack info carries down the lineage: the first row from the
    root down with a `dmgHitRecordLifeTime` above 0 (`record_lifetime`). So Elden Stars' stars,
    whose own rows carry 0, keep the root's 0.15 s.
  - A list with no record is one bullet and the shots it continues. It hits again every lifetime
    while it touches him, timed as above (the formula, `contact`).
  - A record's hits are played out (`record_hits`): every member flown in 3D at a defender
    standing straight ahead (`er-mechanics-ashes.bullet_flight`, launch frames as FUN_140390c20
    and FUN_14039bba0 build them, aimed at and homing on his lock-on point, DummyPoly 220,
    1.24 m up on the idle pose, `MEASURED`). Each member's sweep from one frame's position to the
    next (FUN_1405226e0) lands its touches on his idle hurtbox in frame order at the record's
    spacing. A non-penetrating member dies on its landed touch and makes its hit child at the
    contact point; one a record blocks flies on. The count takes the best distance of
    `RECORD_TARGET_M`. A member that never reaches him adds nothing: Shard Spiral's trail shards
    touch only where he stands, so its record lands 3, not one per shard.
  - A record whose shots do not aim at him (Explosive Ghostflame's five arms at fixed yaws,
    Meteorite's four unaimed meteors, Crystal Release's ref 3, two of Ancient Dragons' Light
    Strike's) lands nothing on the axis and is played with him off it too
    (`RECORD_BEARINGS_DEG`), the caster keeping his facing; the best spot is taken, the same
    "in its path" choice as the distance. No damaging record keeps the formula any more
    (2026-11-12, every spell's bullet refs).
  - What closed the old flight gaps (bd er-effects-rs-7bye, round 3; `er-mechanics-ashes`,
    "Launch frames"): the shoot angles turn in the parent's 3D frame, so Meteorite of Astel's
    sideways leg under its rising marker goes sideways, not up; `isOwnerOverrideInitAngle`
    gives Meteorite's meteor and Crystal Release's shards the caster's level frame instead of
    their marker's climb; `sfxPostureType` 1 levels Founding Rain's cloud, whose stars then fall
    straight down on him; `EmittePosType` 1 puts Death Lightning's strike on a random ground
    point around the caster (the one under him taken), where it meets the map; Light of
    Miquella's seeker carries AtkParam_Pc 0, which targets opponents, so with the sweep it stops
    on him and its pillar lands under him (its `isEnableAutoHoming` only picks a target for an
    unlocked cast, FUN_14040afa0); Discus of Light's `FollowType` 5 disc comes back through him.

  A spread volley without a shared list counts all its bullets, which makes `full` an upper bound
  for a spread. A channelled spell (one with a `045012` loop) counts its release clip only.

FP is `Magic.mp` for a tap and `mp_charge` for a charged cast (`INFERRED` from the paramdef name;
the two are equal on every chargeable spell in the list above). Frames run from the press:
`startup` is the hand-off at this build's casting value plus the release clip's first damaging
cast event (bullet travel is not counted, it depends on distance), and `act` is the hand-off plus
the release clip's first R1 or roll gate (`er-mechanics-disengage.clip`).

# The kit: how many spells (`--kit`)

One damage number per cast always picks one spell. A second spell earns its slots only where the
first does not reach, and the situations that decide it here are punishes:

- Each build of the RL window's opponent pool (`er-mechanics-exchange.opponent_pool`) throws the
  openers of the four attack families the pouch files its own hit under (R1, R2, running, jump;
  equal shares, `INFERRED`), each from its own world reach, and the caster runs back on reaction
  (`er-mechanics-neutral` `backoff_whiffs`, neutral.md section 7). Where that whiffs, the caster presses at the end of its
  last hit window (`INFERRED`), at the distance the run left between them, and the opponent cannot
  roll before his swing's dodge cancel (`TAE`). One situation per distinct (distance, recovery),
  weighted by builds x reaction quantile, so an opener that is rarely backed out of counts little.
  The caster may also run in first, to a gap of 1.5, 2.5, 3.5 or 4.5 m at run speed, while the
  opponent's lock runs down (`net_values`, `APPROACH_GAPS_M`), and takes the best of the two.
- From then on he rolls on reaction to the cast: the ash model's `reaction_outcome`
  (ashes-of-war.md section 16a: reaction lognormal around 0.25 s plus two 0.05 s network legs, the
  medium roll's four directions, `INFERRED` constants), held back to that dodge cancel. A cast
  whose cast events fly as separate bursts (live frames that do not overlap) is dodged burst by
  burst (`sequence_outcome`). Against a burst whose every AtkParam has
  `isInvalidatedByNoDamageInAir` he may jump instead: the jump clip makes him immune to it from
  its first frame to its last (`jump_motion`, moveset.md section 6a). Rellana's Twin Moons is the
  only player spell with such hits, and each of its moons lingers 16 frames against the jump's 25.
  Knowing the spell, he takes whichever leaves the caster less.
- He sees only what is on screen (`Lookalikes`, ashes-of-war.md section 16a, 'What is on screen'):
  the casts of one TimeAct and one `castSfxId` among the spells the caster can cast and a player
  can hold look alike until a shot that differs spawns, and a tap like its charge until the tap's
  release shows (`spell_lookalikes`, `look_split`, `release_shows`); a charged skill's hold like
  its early release until the release shows (`skill_lookalikes`, the kit's rivals: `release_look`;
  `er-mechanics-ashes.release_visible`). Until the split he either
  times one roll against the whole group (equal weights, `INFERRED`: the model has no source of how
  often each is cast) or waits for it and reacts from it with his usual delay, whichever escapes
  the group more often in that situation (`er-mechanics-ashes.lookalike_plan`); a cast alone in
  its group is read as before. Before this he read every cast from its first frame, so a single
  bolt with an escaping run of a few frames in every roll direction was rolled in every situation
  (Glintstone Cometshard and the Glintstone Dart netted about 0); now Cometshard's tap is read
  from its release, as its charge plays the same clips to there. A release is let go on a frame
  of its window the caster picks (`release_frames`): one member per release frame, a tap's weight
  split over them, and the tap netting their weighted mean (module doc, "One cast"); the rivals'
  charged skills likewise (`release_look`).
- The cast's bullets fly under the ash model's flight rules (`fly_tree`, section 15: each event's
  root shot leaves its DummyPoly where the release clip, after the start clip's travel, has
  carried the caster, `cast_launch` / `er-mechanics-ashes.bullet_launch`, 1 m above the root,
  `INFERRED`; a homing shot turns after him in flight, `er-mechanics-ashes.homes`, `VERIFIED`
  FUN_140398270, horizontal only, `INFERRED`), judged by the spell rule's node test
  (`spell_node_damaging`, the flat fields: `VERIFIED`, "One hit"), and each shot is aimed at him
  when it is created (the lock-on aim, `VERIFIED` FUN_140390c20 / `lockShootLimitAng`, the comment
  above `er-mechanics-ashes.AIM_EMIT_TYPES`; the caster locked on to him and the aim horizontal,
  `INFERRED`). For most casts the aim changes nothing (`CastThreats` mode 'static', 167 of the
  206 flown multiplayer variants at INT / FTH / ARC 99, `MEASURED` 2026-11-09): they fly once,
  straight, and a cast event that catches him first is worth what lands on him while his hit
  reactions hold him and then as he rolls or runs out (`er-mechanics-ashes.CaughtModel`: each later
  event and each `full`-count tick needs its own geometry to touch him), at most his health
  (`SpellModel.defender` `hp`). 21 are flown at each
  distance and scored the same way ('aimed': Ambush Shard, Ancient Dragons' Light Strike, Blades
  of Stone's tap, Fire Serpent, Gelmir's Fury, Glintblade Trio, Gravitational Missile, Knight's
  Lightning Spear, Light of Miquella, Lightning Strike, Meteorite of Astel, Ranni's Dark Moon,
  Rennala's Full Moon, Rock Sling, Shattering Crystal). 18 are made of independent threats, each
  landing at most once, the punish of a fully dodged cast paid once: a fan whose shots keep their
  own hit lists is one threat per shot worth its own lists ('volley', `er-mechanics-ashes`
  `volley_outcome`: Ancient Death Rancor, Carian Phalanx, Collapsing Stars, Crystal Burst,
  Glintblade Phalanx, Greatblade Phalanx, Oracle Bubbles, Rings of Spectral Light), and a chain
  re-aimed between damaging legs is flown against the defender's own roll, each link aimed at
  where the roll took him ('dynamic', `CastThreats.dynamic_outcome`: Glintstone Nail and Nails,
  Blades of Stone's charged cast). Flown so, a single roll timed to a lone Glintstone Nail's pass
  at 3 or 6 m is met by the re-aimed next leg 14-22 frames into the roll in three of the four roll
  directions, inside the roll's 21-frame dodge cycle (`MEASURED` 2026-11-09: the chain flown
  against one roll started 2, 5 or 8 frames before the pass; `--selftest` checks the 5-frame
  case). He rolls again at each pass that comes back once his dodge cancel lets
  him, up to `DYNAMIC_ROLLS` (three, the user's in-game count for one Glintstone Nails), and
  stands when rolling would cost him more; so a defender takes some nails of one cast and clears
  others, as the user sees in game.
- A roll that evades the cast is answered: he runs in at run speed and throws the pool's R1 #1
  (`punish_strikes`, `PUNISH_HP`), and lands it when that comes before the caster's own roll
  cancel (`roll`: the hand-off plus the release clip's roll gate, else its length). A cast's worth
  in a situation is what it lands less that punish, so a long cast that is easy to roll can be
  worth less than nothing.
- He need not roll at all (2026-11-13, the module comment above `STRIKE_CARRIED`): free before the
  cast first touches him, he can run in and throw that R1 at the caster instead. He presses it no
  sooner than he can roll (`dodge_presses`' rule: his reaction to the cast, network legs included,
  or his recovery floor, whichever is later, `VERIFIED` reading), runs at run speed to
  `PUNISH_REACH_M` and lands it the R1's strike frames later. It lands when that comes before the
  cast first touches him, standing where he is or where his run ends (`StrikeReach.touch`), and
  before the caster's roll gate. Whether the caster's poise holds is
  `er-mechanics-interrupt.simulate` on that one hit (`strike_breaks`): the R1's poise damage (the
  exchange pool's `poise_dealt`, `saRate` applied, carried on `punish_strikes`' `Strikes`) against
  the caster's menu poise, through the cast's TAE 795 window open on that frame
  (`SpellModel.cast_windows`, 20 of the 217 spells have one; a weapon skill's,
  `SpellModel.skill_windows`), from line-fit's carried chip (`STRIKE_CARRIED`, `INFERRED` there).
  The caster's poise is the RL window's median build's, the defender's own, unless the caller
  names it (`attacker_poise`; attack-similarity gives each line its own). Broken, every shot
  launched at or after the strike is stopped (a melee window always is), and what had left flies
  on and lands on him standing; held, the cast lands whole on him standing. Either way the caster
  pays the R1 (or, inside the attacker's own guard, its guarded chip: below), and a strike too late
  lands nothing while the cast lands whole. Knowing the spell,
  he strikes or rolls (or jumps), whichever leaves the caster less, a tie to the roll; where no
  strike lands that is standing, so a defender whom rolling leaves worse off than standing now
  stands, as `CastThreats.dynamic_outcome`'s already did. So a slow
  cast pressed close to a free defender is struck before it leaves the hand (Stars of Ruin's
  charged cast at the Intelligence line, INT 80 DEX 11, 1.5 m, floor 0, the RL 150 pool's strikes,
  poise 62: rolled it nets 783, struck -350, `MEASURED` 2026-11-13; the selftest's case, with R1s
  that break any poise, nets exactly -388), and a cast that reaches no one standing is punished
  whenever the strike beats the caster's roll gate, where before it netted 0.
- An attack that holds its own guard up meets the strike with it (2026-11-13; the comment above
  `STRIKE_CARRIED`, powerstance-guard.md section 3a): a strike landing inside the attack's TAE
  JumpTable 3 windows (`tae_guard`, carried on each geometry as `GUARD`) is blocked by the guard
  model's rules (`strike_guard`, `guard_block`): it costs the attacker the guarded chip, not the
  R1, breaks no poise, and ends the attack only where the guard reaction does (every skill and
  plain attack state) or the guard breaks; the two guard-poke states go on. A skill reads its
  clips' windows (`SpellModel.skill_guard`: Shield Bash, Shield Crash, Roaring Bash, Moore's
  Charge, Revenge of the Night, Golden Retaliation, Flame Spit, Tongues of Fire). A weapon's own
  attack is scored here too (`SpellModel.slot_attack`, `--attack`): a slot, or the R1 from a raised
  guard (`guard_poke`, `GUARD_POKES`). A thrusting shield's R1 guards from its hit frame (f14 of
  R1 #1, hit f13), which no pool R1 can beat before the thrust touches, so the strike leaves its
  nets as they were; the guard pays where the guard covers a windup.
- Weapon skills are scored the same way: each skill's hits, damage, roll and FP are the ash
  model's (`skill_option`), a melee hit flies its posed window and a bullet its whole tree at the
  `full` count (`skill_events`). The rivals are the weapons the build carries or would swap to
  (`--weapon`) and, unless `--no-ash-rivals`, every skill the stats can wield that fires a damaging
  bullet (`spell_like_skills`: the ashes and unique skills that do a spell's job). A spell must
  beat the best of them in a situation to count there. The Zamor Curved Sword's skill fires the
  same 3 m storm as a tapped Zamor Ice Storm, and its damage moves only with the sword's level
  (the storm's AtkParam 303401600 swaps in AECP 50000, which scales nothing).
- Kit value: the weighted mean over situations of the best member's worth, or of the best carried
  skill's where that is more, and nothing where neither is worth more than doing nothing. The
  search is the pouch's greedy: add the spell that raises it most while its `slotLength` fits the
  memory slots, stop when nothing raises it by more than `KIT_EPS`.

The rounds show where the gain flattens. A slot's real price is the buff it displaces:
`er-mechanics-pouch` scores the same punishes per engagement against the buffs and items a build
could carry instead (its module doc, "Attack spells"), and that is where the count is decided. A
spell that hits an AtkParam directly (Carian Slicer) has no flight and is left out; a cast event
with no flight is left out of a cast that has others (Bayle's Flame Lightning's f69 stomp, its
bolt scored).

# Parts other tools read (`er-mechanics-attack-similarity`)

- `net_parts`: `net_values` with the outcome behind each number (what lands, the punish chance,
  the run-in gap chosen, whether the defender struck instead of rolling (`strike`, at once and
  for the option chosen), the press-at-once outcome and its first-touch frame, and the same choice
  on one hit per event). It shares one `_catches` memo across the call, which leaves every number
  as it was and makes `net_values`, the kit and the rivals faster. The strike is on when `strikes`
  is `punish_strikes`' (`Strikes`, which carries each R1's poise damage) and a poise is known;
  `attacker_poise` names the caster's. `StrikeReach`, `strike_segments`, `strike_breaks`,
  `strike_shares` and `strike_outcome` are the pieces, which attack-similarity's forms read the
  same way; with an attacker's guard (`StrikeReach.guard`), `strike_breaks(..., guard, A)` adds
  its row, `strike_segments` takes its windows and `strike_saved` the HP it spares, which
  `strike_shares` returns third (0.0 without a guard, so every other number is as before).
- `slot_attack` and `guard_poke`: a weapon's own attack in `skill_attack`'s shape, and
  `attack_scores` its weight-mean net per whiff with its guard read and ignored (`--attack`).
- `skill_attack`: `skill_events` as a dict that also says why a skill has no score and which hits
  had no reach model (and their worth), the kind of hit at each frame, takes a follow-up or a
  stance button, and with `priced_bullets` prices a bullet's hit lists by the skill's own hits
  instead of the spell rule's flat fields. With `flown_only` a hit list on a bullet the straight
  flight never launches (`launch_gaps`) is not credited: on water or never (`NEVER_ON_LAND`) it is
  dropped, any other is a hit with no reach model. A follow-up leaves out the opening's hits that
  start at or after its press, which cancels the opening (Stamp: 040000 has no hit, so
  `skill_hits` scores the opening on 040010, the R2 follow-up itself, which then counted twice).
- `evaluate` also gives `fp_cast` (`Magic.mp`) and `fp_loop` (`consumeLoopMP_forMenu` when above
  0, `REGULATION`): on every loop spell that field equals `mp_charge`, so a charged loop spell's
  `fp` is its per-tick cost, not the cast's (`INFERRED`; `COMMUNITY` writes them 'cast (per tick)').
- Opt-in predicates, each defaulting to what the kit uses: `bullet_hits(damaging, base)`,
  `flight(damaging, target)` and `geometry(damaging, missing)`; `spell_node_damaging` is the spell
  rule as a node test and every flight's default (a weapon skill's bullet passes the ash model's
  `_bullet_damages_self`), and `geometry` with a `missing` list keeps the events that fly and names
  why the others do not (`flight_miss`). `geometry` flies straight, one merged shape per event, as
  before; the kit's aimed and per-threat flight is `cast_threats` (`CastThreats`), which `net_parts`
  takes in place of `geo`. `launch_gaps` names the nodes of a bullet tree the straight flight never
  launches, by the launch condition that stops them.

# Not established

- How many hits of a spread or a lingering bullet land on a moving player; `one_hit` and `full`
  bracket it.
- Charged-spell boosts that are not a `*AttackRate` row (Godfrey Icon), and SpEffect rows that ask
  for the AtkParam's `spEffectAtkPowerCorrectRate_by*` (grease.md section 2): no row is applied
  beyond what `CastModel` applies.
- Whether a hold spell's breath already made is cut short when the caster lets go: the 'early'
  variant lets every bullet made before the release fly its life (Dragonfire's f49 breath, Bullet
  10704000, 1.5 s, `FollowType` 4, a free flight sliding on the ground).
- Whether the release frame is drawn uniformly: the caster could prefer some frames, and a
  defender who knew it would weigh them so.
- For the kit: the vertical part of the aim and of the homing turn, the lock-on aim following a
  roll made before a shot exists for a shot with no homing in its lineage (only 'dynamic' chains
  and homing geometries are flown again after him, each aimed shot aimed where he is when it is
  created; any other keeps its stored aim at where he stood), the defender's re-roll policy
  against a re-aiming chain (`_rolled`: roll at each
  pass he can still meet, the same direction, up to three), and where a roll leaves him for the
  next burst of a cast that does not re-aim (each burst places him back at the distance); the
  caster's own reaction to the whiff, and every punish other than a whiffed R1 #1 (a blocked
  string, a roll read at range). What a game run would settle for Glintstone Nails: how many of
  the six nails a defender who rolls on reaction takes.
- For the strike: that he picks it knowing the attack, as he picks the roll or the jump, even where
  he cannot yet tell it from its lookalikes (`Lookalikes`; his choice against the group would need
  every member's outcomes in one place, which `net_parts` and attack-similarity's forms read one
  member at a time), so a group with a fast member is struck as if he knew it was the slow one;
  one R1 (no second hit of the string, which would break more); his R1 free on the frame his roll
  is (the situation's floor is his whiff's dodge cancel; the pool's R1 cancel opens at or before it
  on 85.1% of the whiff weight, a mean 2.3 frames sooner, `MEASURED` 2026-11-13, so the strike is
  if anything a little late, and a press there plays the string's next R1, not R1 #1, which is not
  modelled); his strike reaching the caster's
  machine one network leg after he sees the cast plus one more (the roll's two legs, `INFERRED`:
  which machine decides his hit is not traced); the cast's first touch on him read standing at
  either end of his run, not along it; the shots already launched landing on him as on a
  defender who stands; a strike that comes after the first touch counting as caught, never as a
  trade; and the caster's poise state (`STRIKE_CARRIED`; fresh breaks less).
- For the attacker's guard: the pool R1's guarded share is read from its base weapon's attack
  split and motion values, unscaled (the pool names no affinity or stats); the attacker's stamina
  is the pool's median bar less the stamina his hits have taken by the frame the strike lands,
  each hit's on the frame its hitbox is created (`Guard.charges`, `guard_phases`), plus what his
  clip regenerates before the guard opens (`Guard.regen`, at 45/s plus his talismans' `staminaRecoverChangeSpeed`,
  `attacker_regen`; no caller names any talisman, and every attack read holds TAE 225 at 0% there,
  so it adds nothing today); the follow-up R1 on a guard break
  is taken to land; the R1's poise damage, which a guarded hit still deals (`saGuardCutRate` 0),
  is not carried into anything after. Whether the guard reaction table gives no reaction under
  hyperarmor was not traced.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import importlib.util
import json
import math
import os
import re
import statistics
import struct
import sys
import tomllib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, fname):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AR = _mod('er_mechanics_ar', 'er-mechanics-ar.py')
CS = _mod('er_mechanics_cast_speed', 'er-mechanics-cast-speed.py')
DEF = _mod('er_mechanics_defense', 'er-mechanics-defense.py')
D = _mod('er_mechanics_disengage', 'er-mechanics-disengage.py')
ATK = CS.A
PR = CS.PR
T = CS.T


def _interrupt():
    """`er-mechanics-interrupt`, the poise rule the defender's strike is read with (`simulate`,
    `chip_states`, `_window`; `strike_breaks`), loaded when first asked for."""
    return _mod('er_mechanics_interrupt', 'er-mechanics-interrupt.py')


_GUARD = []


def _guard_model():
    """(`er-mechanics-powerstance-guard`, its `Tables`): the guard rules a strike that meets the
    attacker's own guard is read with (`shield_guard`, `block_hit`; `strike_guard`), loaded once
    when first asked for."""
    if not _GUARD:
        G = _mod('er_mechanics_powerstance_guard', 'er-mechanics-powerstance-guard.py')
        _GUARD.append((G, G.Tables()))
    return _GUARD[0]


#: TAE event type 0 `ChrActionFlag` with Args[0] 3: `_ChrActionFlag` raises the guard flag and
#: stores ArgB (Args+8) as the guard judge while the event is live (`VERIFIED`, powerstance-guard.md
#: section 3). An attack clip that carries one is guarded over that span: the thrusting shields'
#: attacks (judge 461 / 471), their two-handed guard poke (`AttackBothLightGuard` 032900, 470), the
#: spear, rapier, great spear and heavy thrusting sword R1 from a raised guard (`AttackRightWhileGuard`
#: 030900, 460), and the skills Shield Bash, Shield Crash, Golden Retaliation, Flame Spit, Tongues of
#: Fire, Moore's Charge, Revenge of the Night and Roaring Bash (`TAE`, every player TAE scanned).
GUARD_JUMP_TABLE = 3
#: The attacker's guard over an attack (`tae_guard`, carried on each geometry as `GUARD`): its
#: windows ((start, end) real frames from the attack's start), the guarding weapon and level, the
#: judge, the grip, whether the state goes on through a guarded hit (`continues`), and the stamina
#: the attack itself spends (`cost`), and `regen`: the frames of full-rate stamina regeneration the
#: attack gives back before its first guard window opens (its clip's TAE 225 `SetSPRegenRatePercent`
#: integrated from the clip's start, `tae_regen_frames`; 0 where the clip holds regeneration at 0%,
#: as every attack clip read here does there), and `charges`: ((real frame, stamina), ...) per
#: melee hit, the frame its AttackBehavior event first creates its hitbox, which is when the game
#: takes its stamina (`FUN_1404428f0`, attacks.md section 3, `VERIFIED`), so a strike that lands
#: inside the guard meets the stamina of the hits charged by then (`guard_phases`). A guard with
#: no `charges` takes all of `cost` before it opens, the shape every `Guard` had before 2026-11-13.
#:
#: `continues` is the behavior script's: a guarded hit (`DAMAGE_TYPE_GUARD`) plays a guard reaction
#: (`W_GuardDamageSmall` / `Middle` / `Large`, all body) from any state whose passive action is
#: called with `is_attackwhileguard` `FALSE`, which ends the attack; `AttackCommonFunction` passes `TRUE`
#: only for `TO_GUARDON`, which `AttackRightWhileGuard_onUpdate` and `AttackBothLightGuard_onUpdate`
#: alone pass, and then `ExecDamage` returns without a reaction and the attack goes on. A plain R1
#: (`AttackRightLight1_onUpdate`) passes `FALSE`, and so does every skill state through
#: `ArtsCommonFunction` (`VERIFIED` c0000.hks 1.17.2 decompile, `ExecDamage` /
#: `ExecPassiveAction` / `AttackCommonFunction` / `ArtsCommonFunction`). A guard break plays
#: `W_GuardBreak` from every state (`is_parry` alone gates it). A geometry carries it as a plain
#: tuple (`GUARD`), which pickles and hashes as the TAE 795 windows do; `Guard(*g['GUARD'])` reads it.
Guard = collections.namedtuple('Guard', 'windows weapon level judge two continues cost regen charges',
                               defaults=(0.0, ()))


def guard_phases(guard):
    """[(windows, charged)] of `guard` (`Guard`): its windows cut at each frame a hit takes its
    stamina (`Guard.charges`), each piece with the stamina charged by its start, in time order. A
    strike that lands on the frame a hitbox is created meets that hit charged (`INFERRED`: which of
    the two the frame runs first was not traced). With no `charges`, one piece charged `cost`."""
    if not guard.charges:
        return [(tuple((float(s), float(e)) for s, e in guard.windows), float(guard.cost))]
    frames = sorted({float(f) for f, _ in guard.charges})
    edges = [-math.inf] + frames + [math.inf]
    out = []
    for a, b in zip(edges, edges[1:]):
        wins = tuple((max(float(s), a), min(float(e), b)) for s, e in guard.windows if max(s, a) < min(e, b))
        if wins:
            out.append((wins, float(sum(c for f, c in guard.charges if f <= a))))
    return out


def guard_phase_windows(guard):
    """`guard_phases`' windows as (start, end, phase) triples, what `strike_window` reads: a strike
    inside phase k is in `strike_breaks`' guard row k. () for no guard."""
    if guard is None:
        return ()
    return tuple((s, e, k) for k, (wins, _) in enumerate(guard_phases(guard)) for s, e in wins)
#: TAE event 225 `SetSPRegenRatePercent`: Args[0] is the percent of the stamina regeneration rate
#: that holds while it runs, back to 100 every frame it does not (er-mechanics-exchange
#: `TAE_SP_REGEN`, `VERIFIED` there).
TAE_SP_REGEN = 225


def tae_guard(events, offset=0.0, to_real=None):
    """(((start, end) real frames, ...), the judge of the first) of the guard windows TAE JumpTable 3
    holds in `events` (one clip), `offset` added (where the attack starts that clip), clip time
    mapped by `to_real` (default `ATK.clip_to_real(events)`, its TAE 608 play speed). An event gated
    on a SpEffect state (Args+0xe, `ATK.JUMP_TABLE_STATE_GATE_OFFSET`) is left out, as the attack
    rules leave a gated hitbox out. ((), None) when there is none."""
    real = to_real or ATK.clip_to_real(events)
    wins, judge = [], None
    for e in events:
        p = bytes(e.params)
        if e.type != ATK.TAE_JUMP_TABLE or len(p) < 16 or struct.unpack_from('<i', p, 0)[0] != GUARD_JUMP_TABLE:
            continue
        if struct.unpack_from('<H', p, ATK.JUMP_TABLE_STATE_GATE_OFFSET)[0]:
            continue
        wins.append((offset + ATK.real_frame(real(e.start)), offset + ATK.real_frame(real(e.end))))
        judge = struct.unpack_from('<i', p, 8)[0] if judge is None else judge
    return tuple(sorted((float(a), float(b)) for a, b in wins)), judge


#: The two attack states that keep the guard up from the press and go on through a guarded hit
#: (`Guard`, `continues`), as `SpellModel.slot_attack` clips: (label, TAE anim, two-handed).
#: - `AttackBothLightGuard` 032900: R1 while guarding with a two-handed thrusting shield and L1 held
#:   (`ExecAttack` `ATTACK_REQUEST_BOTH_LIGHT`: `env(225, atk_hand) == WEAPON_CATEGORY_DUELING_SHIELD`,
#:   `is_guard` and `env(1108, ACTION_ARM_L1) > 0`; behavior graph state `AttackBothLightGuard`
#:   plays 032900). a057_032900: guard 470 f0-42, hit (judge 200, the 2H R1 #1's) f15-19 (`TAE`).
#: - `AttackRightWhileGuard` 030900: R1 while guarding one-handed with L1 held, when the right weapon
#:   is a spear, rapier, great spear or heavy thrusting sword (`GetAttackRequest`:
#:   `ATTACK_REQUEST_ATTACK_WHILE_GUARD`), guarding with the left weapon (`SetGuardHand(HAND_LEFT)`;
#:   `IndexAttackWhileGuard` 3 for a thrusting shield, 2 for a torch, else its guard style; the state
#:   plays 030900 / 030901 / 030904, which carry the same events in every category scanned).
#:   a036_030900 (spear): guard 460 f0-65, hit (judge 170) f12-16; a027 f0-60 / f11-14; a037 f0-60 /
#:   f16-19; a039 f0-37 / f13-16 (`TAE`). All `VERIFIED` c0000.hks 1.17.2 decompile and behbnd 1.17.1.
GUARD_POKES = {'both_guard': ('2H R1 from guard (L1 held)', 32900, True),
               'right_guard': ('R1 from guard (L1 held)', 30900, False)}
#: `WEAPON_CATEGORY_*` (c0000.hks; the weapon's `wepmotionCategory`) of the right weapons that get
#: `AttackRightWhileGuard`, and of the thrusting shields (`AttackBothLightGuard`).
GUARD_POKE_CATEGORIES = {27: 'rapier', 36: 'spear', 37: 'great spear', 39: 'heavy thrusting sword'}
THRUSTING_SHIELD_CATEGORY = 57


def tae_regen_frames(events, upto, to_real=None):
    """Real frames of full-rate stamina regeneration a clip gives from its start to real frame
    `upto`: TAE 225's percent integrated, 100 where no event runs and the later event in TAE order
    where two do (`er-mechanics-exchange.regen_windows`' rule)."""
    if upto <= 0:
        return 0.0
    real = to_real or ATK.clip_to_real(events)
    wins = [(ATK.real_frame(real(e.start)), ATK.real_frame(real(e.end)) if e.end < 1e6 else math.inf,
             float(bytes(e.params)[0])) for e in events if e.type == TAE_SP_REGEN and e.params]
    cuts = sorted({0.0, float(upto)} | {x for a, b, _ in wins for x in (a, b) if 0.0 < x < upto})
    total = 0.0
    for a, b in zip(cuts, cuts[1:]):
        mid, pct = (a + b) / 2, 100.0
        for s_, e_, p_ in wins:
            if s_ <= mid < e_:
                pct = p_
        total += (b - a) * pct / 100.0
    return total


START, RELEASE, TAP, LOOP = 45000, 45010, 45011, 45012
#: SpEffect whose TAE window in 045010 lets a cast button let go play 045011, the tap (`VERIFIED`
#: c0000.hks `MagicFireRight_Upper_onUpdate`: `env(1116, 100610)` and the button's
#: `env(1108, ...)` <= 0 -> `Event_MagicFireRightCancel`; bd spell-tap-vs-charge-speffect-100610).
TAP_SPEFFECT = 100610
#: `Magic.refType` c0000.hks `CheckIfHoldMagic` counts as a hold spell besides a TimeAct with 045012.
HOLD_REF_TYPE = 35
#: CalcCorrectGraph row of max HP against Vigor (`er-mechanics-defense.calc_correct`: 300 at 1,
#: 1450 at 40, 1900 at 60, 2100 at 99).
HP_GRAPH = 100
#: The charged release of a spell whose 045010 fires nothing: Loretta's Greatbow and Mastery
#: (TAE a405: 045021 fires ref 5, `refId5` 10438050 / 10438150; 045010 has no cast event).
CHARGED_ALT = 45021
SPELL_TAE_BASE = 400
TAE_CAST = CS.TAE_CAST
FPS = ATK.TAE_FPS
#: AtkParam element field per element name (`AR.ELEMENTS` order).
ATK_FIELD = {'physical': 'atkPhys', 'magic': 'atkMag', 'fire': 'atkFire', 'lightning': 'atkThun',
             'holy': 'atkDark'}
#: `atkAttribute` -> the physical absorption `er-mechanics-defense.damage` applies (defense.md 2b);
#: any other value meets no physical cut rate.
PHYS_TYPE = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}
ATK_FIELDS = list(ATK_FIELD.values()) + [
    'finalDamageRateId', 'overwriteAttackElementCorrectId', 'atkAttribute', 'isAddBaseAtk',
    'isDisableBothHandsAtkBonus', 'isInvalidatedByNoDamageInAir', 'opposeTarget']
BULLET_FIELDS = ['atkId_Bullet', 'HitBulletID', 'intervalCreateBulletId', 'intervalCreateTimeMin',
                 'intervalCreateTimeMax', 'intervalCreateWaitTime', 'numShoot', 'life',
                 'dmgHitRecordLifeTime', 'isPenetrateChr', 'isEndlessHit', 'isUseSharedHitList',
                 'atkAttribute', 'initVellocity', 'maxVellocity', 'hitRadius', 'hitRadiusMax', 'FollowType',
                 'launchConditionType', 'attachEffectType']
#: Front-to-back depth of a player standing idle: the farthest hurtbox surface ahead of the
#: origin (0.316, the forward foot) plus behind it (0.248, the rear calf), reach.md section 3
#: (`MEASURED`, `er-mechanics-reach.py --hurtbox`).
BODY_DEPTH = 0.316 + 0.248
MAGIC_FIELDS = ['refType', 'mp', 'mp_charge', 'spEffectCategory', 'slotLength', 'requirementIntellect',
                'requirementFaith', 'requirementLuck', 'enable_multi', 'enable_live', 'enable_hostile',
                'enable_friendly', 'consumeLoopMP_forMenu', 'castSfxId'] + [f'refCategory{i}' for i in range(1, 11)] + [
                    f'refId{i}' for i in range(1, 11)]
REF_ATTACK, REF_BULLET = 0, 1
#: Why a cast event has no flight (`geometry` with `missing`, `flight_miss`), first match wins.
#: `MISS_LAUNCH` carries the launch conditions that stop it, '(launchConditionType 5)': 5 is 'on a
#: hit', and the straight flight hits no character, so damage only in an on-hit child of a carrier
#: is never flown.
MISS_DIRECT = 'direct AtkParam ref (refCategory 0)'
MISS_TREE = 'bullet tree unreadable'
MISS_HEIGHT = 'never reaches hurtbox height'
MISS_LAUNCH = 'damage only in a bullet the straight flight never launches'
MISS_OTHER = 'other'
#: One hit list on a bullet the straight flight never launches, under a tree that does fly.
MISS_UNFLOWN = 'hit on a bullet the straight flight never launches'
#: `launchConditionType` values no flight over dry, flat ground meets: 1 and 2 on water, 254 and
#: 255 never (`VERIFIED` FUN_14039da50, `er-mechanics-ashes.LAUNCH_ON`). A hit on such a bullet is
#: dropped, not missing: it has no reach to model.
NEVER_ON_LAND = (1, 2, 254, 255)
_FLOWN = object()


def _chain_root(base, ro):
    """`er-mechanics-ashes.root_offset` `ro` of a clip played after the clips `base` sums (None: from
    the caster's start): its position turned by the base yaw and moved by the base position, the
    yaws added, the larger forward peak, as `bullet_launch` lays a clip on its `base`."""
    if ro is None:
        return base
    if base is None:
        return {'pos': ro['pos'], 'yaw': ro['yaw'], 'peak': ro['peak']}
    c, s = math.cos(math.radians(base['yaw'])), math.sin(math.radians(base['yaw']))
    px, py, pz = ro['pos']
    bx, by, bz = base['pos']
    return {'pos': (bx + px * c + pz * s, by + py, bz - px * s + pz * c), 'yaw': base['yaw'] + ro['yaw'],
            'peak': max(base['peak'], bz + ro['peak'])}


def launch_text(lcs):
    """'launchConditionType 5' (or '2, 5'), 'an interval child never spawned', 'past the depth
    cap', or several of them joined, for the reasons `launch_gaps` gives."""
    lcs = set(lcs)
    out = []
    ints = sorted(x for x in lcs if isinstance(x, int))
    if ints:
        out.append('launchConditionType ' + ', '.join(str(x) for x in ints))
    if 'interval' in lcs:
        out.append('an interval child never spawned')
    if None in lcs:
        out.append('past the depth cap')
    return '; '.join(out)
#: The `EquipParamWeapon` flag a catalyst needs, by the Magic row name's school. Not
#: `Magic.spEffectCategory`, which is the SpEffect context (`er-mechanics-cast-speed`) and is 3 on
#: some incantations: Briars of Punishment.
SCHOOL_FLAG = {'[Sorcery]': 'enableMagic', '[Incantation]': 'enableMiracle'}
#: Links followed below a fired bullet. A timer chain is long, not a loop: Stars of Ruin's twelve
#: 0.1 s timers put its last star 12 links down and that star's second leg 13 (8 used to cut off
#: four of the twelve stars), the deepest spell tree is 15 (Blades of Stone, Litany of Proper
#: Death, the Light Spears) and the deepest Bullet chain 59 (Wave of Gold, 4000). No Bullet row
#: reaches itself again except two that name themselves (20002202, 200002827); `bullet_lists`
#: skips a row already on its path (`REGULATION`, scanned 2026-11-12).
MAX_DEPTH = 64
#: Where `record_hits` stands the defender, metres straight ahead of the caster: the count is the
#: best of these (`INFERRED`: `full` is an upper bound, and where he stands is not the caster's to
#: pick, so the spot a record lands the most on stands for "in its path").
RECORD_TARGET_M = (1.0, 2.0, 3.0, 4.5, 6.0, 8.0, 10.0, 13.0, 16.0, 20.0)
#: Bearings off the caster's facing (+x 90) at which `record_hits` stands him, at each of
#: `RECORD_TARGET_M`, for a record that lands nothing on the axis: the same "in its path" spot
#: choice turned sideways (`INFERRED` reading of `full`; a caster locked on faces his target, so on
#: the axis is where a lock-on cast meets him).
RECORD_BEARINGS_DEG = tuple(b for b in range(-170, 181, 10) if b)
#: Bullets one `_fly_records` cast may make before it stops making more (a safety bound; the
#: largest spell cast makes about 400, `MEASURED` 2026-11-12).
RECORD_BULLET_CAP = 5000


def record_lifetime(inherited, r):
    """The DmgHitRecord lifetime a launch of Bullet row `r` carries: the one its parent's attack
    info hands down when that is above 0, else the row's own `dmgHitRecordLifeTime` (`VERIFIED`
    2026-11-12, 1.16.2 named dump: FUN_14038fe80 sets entry +0x960 from the row only when it is
    not above 0; FUN_14038b740 and FUN_14038d130 copy attack info +0x630 to the child and into
    the bullet, CSBulletIns +0xae0). A cast's root inherits none (`INFERRED`: the spawn request's
    +0x630 is taken to start at 0)."""
    return inherited if inherited > 0 else max(r['dmgHitRecordLifeTime'], 0.0)
#: RL window of the median defender, either side of `--rl`.
WINDOW = 10
#: `EquipParamGoods.goodsType` of a spell item (sorcery, incantation and their DLC types).
SPELL_GOODS_TYPES = (5, 16, 17, 18)


class SpellModel:
    """One regulation read: player spells, the bullets and AtkParams they reach, their TimeAct."""

    def __init__(self, regulation=None):
        self.ar = AR.Tables(regulation)
        self.cast = CS.CastModel(regulation)
        files = self.cast.t._files

        def by_id(stem, fields):
            return {r['id']: r for r in PR.rows(PR.param_bytes(files, stem), fields)[0]}

        self.magic = by_id('Magic', MAGIC_FIELDS)
        self.bullet = by_id('Bullet', BULLET_FIELDS)
        self.atk = by_id('AtkParam_Pc', ATK_FIELDS)
        self.final_rate = by_id('FinalDamageRateParam', None)
        self.names, self.school = {}, {}
        for mid, nm in PR.row_names('Magic').items():
            if mid in self.magic and nm and nm.startswith(('[Sorcery] ', '[Incantation] ')):
                self.names[mid] = nm.split('] ', 1)[1]
                self.school[mid] = SCHOOL_FLAG[nm.split('] ', 1)[0] + ']']
        self.by_name = {}
        for mid, nm in sorted(self.names.items()):
            self.by_name.setdefault(T._norm(nm), mid)
        self._defender = {}
        self._ash = None
        self._profile = None
        self._def_t = None
        self._goods = None
        self._taps = {}
        self._records = {}

    # -- the catalyst ------------------------------------------------------------------------

    def catalyst(self, text, level=None):
        """(weapon row id at its level, base row, reinforce row, name) for 'Name' or 'Name+10'."""
        m = re.match(r'^(.*?)(?:\s*\+\s*(\d+))?$', text.strip())
        name, lvl = m.group(1), m.group(2)
        base = self.ar.find_weapon(name, 'Standard')
        wep = self.ar.weapons[base]
        top = self.ar.max_level(wep['reinforceTypeId'])
        lvl = top if lvl is None and level is None else int(lvl if lvl is not None else level)
        if not 0 <= lvl <= top:
            raise SystemExit(f'{name} has levels 0..{top}')
        return {'id': base + lvl, 'base': base, 'wep': wep, 'level': lvl, 'name': self.ar.names.get(base, name),
                'reinf': self.ar.reinforce[wep['reinforceTypeId'] + lvl]}

    def multiplier(self, cat, element, stats, atk_row=None, two_handed=False):
        """`FUN_140690390` for one element of a spell hit: the catalyst's element multiplier, with
        the AtkParam's AECP overwrite and two-handing bonus."""
        wep, reinf = cat['wep'], cat['reinf']
        aecp_id = wep['attackElementCorrectId']
        if atk_row and atk_row.get('overwriteAttackElementCorrectId', -1) >= 0:
            aecp_id = atk_row['overwriteAttackElementCorrectId']
        two = two_handed and not (atk_row or {}).get('isDisableBothHandsAtkBonus')
        _, suf, _, _, gfield = next(e for e in AR.ELEMENTS if e[0] == element)
        return AR.element_multiplier(self.ar, wep, reinf, self.ar.aecp.get(aecp_id, {}), suf, wep[gfield],
                                     AR.normalise_stats(stats), AR.TWO_HAND_STR_MULT if two else 1.0)

    def can_cast(self, mid, cat):
        return bool(cat['wep'].get(self.school[mid]))

    def obtainable(self, mid):
        """Whether a player can hold spell `mid`: an `EquipParamGoods` row of its id of a spell
        `goodsType` (`SPELL_GOODS_TYPES`) whose `sortId` is not `UNOBTAINABLE_SORT_ID`
        (`REGULATION`, the rule attack-similarity's `Context.spell_goods` reads)."""
        if self._goods is None:
            self._goods = {r['id']: r for r in PR.rows(PR.param_bytes(self.cast.t._files, 'EquipParamGoods'),
                                                         ['goodsType', 'sortId'])[0]}
        g = self._goods.get(mid)
        return bool(g) and g['goodsType'] in SPELL_GOODS_TYPES and g['sortId'] != UNOBTAINABLE_SORT_ID

    def short(self, mid, stats):
        """{stat: shortfall} of the spell's own requirements; empty when it can be cast."""
        m = self.magic[mid]
        out = {}
        for k, f in (('int', 'requirementIntellect'), ('fth', 'requirementFaith'), ('arc', 'requirementLuck')):
            if stats.get(k, 0) < m[f]:
                out[k] = m[f] - stats.get(k, 0)
        return out

    # -- the cast ----------------------------------------------------------------------------

    def chargeable(self, mid):
        return self.magic[mid]['mp_charge'] > 0

    def _events(self, cat, anim):
        for c in (cat, SPELL_TAE_BASE):
            got = ATK.resolve_events(c, anim)
            if got and got[2]:
                return c, got[2]
        return None, None

    def _casts(self, cat, anim):
        """Whether clip `anim` of TimeAct `cat` carries a TAE 64 cast event."""
        ev = self._events(cat, anim)[1]
        return bool(ev) and any(e.type == TAE_CAST for e in ev)

    def variants(self, mid):
        """[(label, release clip)]: 'tap' (045011) and 'charged' (045010, or 045021 when only that
        one fires) for a chargeable spell whose 045011 casts, else one 'cast' (045010). A hold spell
        (`consumeLoopMP_forMenu` > 0, whose `mp_charge` is that per-tick cost) has a 045011 with no
        cast event on 16 of them (the breaths, Crystal Barrage, Gelmir's Fury, Meteorite, Spira,
        Divine Bird Feathers: `REGULATION` + `TAE`): it is not a tap release, so they get one cast
        (module doc, "One cast"). Such a spell whose 045010 has the `TAP_SPEFFECT` window also gets
        'early' (045011): the cast let go inside that window, which plays 045010 up to the release
        and then that release-out (`tap_release`; module doc, "One cast")."""
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        out = []
        if self.chargeable(mid) and self._events(cat, TAP)[1] and (
                self._casts(cat, TAP) or self.magic[mid]['consumeLoopMP_forMenu'] <= 0) \
                and self.tap_release(mid) is not None:
            out.append(('tap', TAP))
            charged = CHARGED_ALT if not self._casts(cat, RELEASE) and self._casts(cat, CHARGED_ALT) else RELEASE
            out.append(('charged', charged))
        else:
            out.append(('cast', RELEASE))
            if self.magic[mid]['consumeLoopMP_forMenu'] > 0 and self._events(cat, TAP)[1] \
                    and not self._casts(cat, TAP) and (self.tap_release(mid) or {}).get('window') is not None:
                out.append(('early', TAP))
        return out

    def release_lead(self, mid, anim, lead=None):
        """Real frames of 045010 that release clip `anim` plays before its own: for 045011 (a tap, or
        a hold spell let go early) `lead` when given (a later release, `release_frames`), else its
        earliest (`tap_release`); 0 for any other clip."""
        if anim != TAP:
            return 0.0
        return float(lead) if lead is not None else (self.tap_release(mid) or {}).get('lead', 0.0)

    def release_shows(self, mid, lead):
        """Real frames from a tap's (or an early release's) switch to 045011 at real frame `lead` of
        045010 to the first frame the defender can see it (`er-mechanics-ashes.release_visible`: the
        blend into 045011 against 045010 held on, then 045012 for a hold spell, `hold_magic`, the
        clip a held button goes to). 0 when a clip of the pair has no unpacked animation to read
        (the switch frame, as before)."""
        A = self.ash()[0]
        refcat = SPELL_TAE_BASE + self.magic[mid]['refType']
        c = self._events(refcat, RELEASE)[0]
        if c is None:
            return 0
        loop = LOOP if self.hold_magic(mid) and self._events(refcat, LOOP)[1] else None
        try:
            return A.release_visible(c, RELEASE, TAP, lead, loop)
        except (FileNotFoundError, KeyError, ValueError, IndexError, TypeError):
            return 0

    def releases(self, mid, anim):
        """`er-mechanics-ashes.release_frames` of release clip `anim` of spell `mid`: [(lead, weight)],
        the 045010 frames a tap or an early release may be let go at (`tap_release`'s window), each a
        `lead` for `evaluate`; [(its lead, 1.0)] without a window and for any other clip."""
        tr = self.tap_release(mid) if anim == TAP else None
        if not tr:
            return [(self.release_lead(mid, anim), 1.0)]
        return self.ash()[0].release_frames(tr['lead'], tr['window'][1] if tr['window'] else None)

    def hold_magic(self, mid):
        """c0000.hks `CheckIfHoldMagic` (`VERIFIED` decompile, bd spell-tap-vs-charge-speffect-100610):
        the spell's TimeAct has a 045012 loop, or its `refType` is 35."""
        mg = self.magic[mid]
        return bool(self._events(SPELL_TAE_BASE + mg['refType'], LOOP)[1]) or mg['refType'] == HOLD_REF_TYPE

    def tap_release(self, mid):
        """How a tap leaves 045010 for 045011 (module doc, "One cast"; `VERIFIED` c0000.hks
        `MagicFireRight_Upper_onUpdate`): {'lead': the real frames of 045010 a tap plays first at its
        earliest release, 'window': (first, last) real frames of 045010's SpEffect `TAP_SPEFFECT`
        window, inside which the cast button let go plays 045011 (any frame of it, the caster's
        choice; a press let go before it waits for its first frame), or None, 'by'}. Without the
        window a hold spell (`hold_magic`) goes to 045011 at 045010's end when the button is up
        (`env(339, 1)`, `INFERRED` the layer's clip end): 'lead' is then 045010's length. None when
        nothing takes 045010 to 045011 (no tap)."""
        if mid in self._taps:
            return self._taps[mid]
        A = self.ash()[0]
        c, ev = self._events(SPELL_TAE_BASE + self.magic[mid]['refType'], RELEASE)
        out = None
        wins = [e for e in ev or () if e.type in A.EV_SPEFFECT and struct.unpack_from('<i', e.params, 0)[0] == TAP_SPEFFECT]
        if wins:
            real = ATK.clip_to_real(ev)
            first, last = (round(real(x) * FPS, 3) for x in (min(e.start for e in wins), max(e.end for e in wins)))
            out = {'lead': first, 'window': (first, last), 'by': f'SpEffect {TAP_SPEFFECT}'}
        elif ev and self.hold_magic(mid):
            info = D.clip(c, RELEASE) or {}
            out = {'lead': float(info.get('length') or 0.0), 'window': None, 'by': 'hold spell, 045010 end'}
        self._taps[mid] = out
        return out

    def _cast_events(self, mid, anim, lead=None):
        """(TimeAct of clip `anim`, [(real frame from the hand-off, ref index, clip it is in, real frame
        in that clip)]) of the TAE 64 events release `anim` plays: its own, and for a tap (045011)
        the casts 045010 makes before the tap's release (`release_lead`: `lead`, else its earliest),
        then 045011's that many frames on."""
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']

        def own(a):
            c, ev = self._events(cat, a)
            if ev is None:
                return c, []
            real = ATK.clip_to_real(ev)
            return c, sorted((real(e.start) * FPS, struct.unpack_from('<b', e.params, 8)[0] + 1)
                             for e in ev if e.type == TAE_CAST)
        c, mine = own(anim)
        lead = self.release_lead(mid, anim, lead)
        if not lead:
            return c, [(f, n, anim, f) for f, n in mine]
        pre = [(f, n, RELEASE, f) for f, n in own(RELEASE)[1] if f < lead - 1e-6]
        return c, sorted(pre + [(lead + f, n, TAP, f) for f, n in mine])

    def handoff(self, mid, value):
        """Real frames into the start clip at which it hands off to the release (JumpTable 93 at
        casting value `value`), or its whole length when it has no such window, or 0 without one."""
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        c, ev = self._events(cat, START)
        if ev is None:
            return 0.0
        real = ATK.clip_to_real(ev)
        for e in ev:
            if e.type == ATK.TAE_JUMP_TABLE_EARLY and struct.unpack_from('<hh', e.params, 0) == (
                    CS.CAST_START_JUMP, CS.EARLY_CASTING_SPEED):
                iv = ATK._early_interval(e, value)
                return real(e.end if iv is None else iv[0]) * FPS
        info = D.clip(c, START)
        return float(info['length']) if info and info.get('length') else 0.0

    @staticmethod
    def _clip_seconds(events, frames):
        """The clip time at which `frames` real frames of the clip with TAE `events` have played
        (the inverse of `clip_to_real`, by bisection)."""
        real = ATK.clip_to_real(events)
        hi = max(1.0, 4 * frames / FPS)
        while real(hi) * FPS < frames and hi < 120.0:
            hi *= 2
        lo = 0.0
        for _ in range(48):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if real(mid) * FPS < frames else (lo, mid)
        return hi

    def look_split(self, a, b):
        """The real frame from the press from which a defender can tell cast `a` from cast `b`, two
        casts of one `Magic.refType` and `castSfxId` (`spell_lookalikes`), each {'mid', 'anim' (its
        release clip), 'hand' (its hand-off frame), 'lead' (optional: the 045010 frame a release is
        let go at, `release_lead`)}; inf when he never can. Both play the clips of one TimeAct,
        `a<400 + refType>` (module doc, "One cast", `TAE`): 045000 to the hand-off, then 045010, and
        a tap leaves 045010 for 045011 at its release (`tap_release`; without a 'lead', its
        earliest). So two releases of one spell part where the earlier one shows. They part at the
        first frame where what he sees differs (`_look`): one
        switches clip and the other does not (an earlier hand-off; a tap's release against a charge
        held on, once the blend into 045011 shows, `release_shows`), or a cast event fires a
        different `refId<n>`, the shot's spawn (`INFERRED`: the
        SFX of an equal `castSfxId` and the clips look the same; a different shot is seen when it is
        created). Two rows that fire the same refs at every event never part."""
        marks = []
        for c in (a, b):
            seen = {}
            for f, k in self._look(c):
                seen.setdefault(round(f, 2), (f, []))[1].append(k)
            marks.append(seen)
        for key in sorted(set(marks[0]) | set(marks[1])):
            ka, kb = (sorted(m_.get(key, (0.0, []))[1]) for m_ in marks)
            if ka != kb:
                return min(m_[key][0] for m_ in marks if key in m_)
        return math.inf

    def _look(self, c):
        """[(real frame from the press, mark)] of what a defender sees of cast `c` (`look_split`'s
        dict): the clip it switches to (045000 from the press, 045010 at the hand-off, 045011 at a
        tap's release, `tap_release`) and the ref each cast event fires (none when `refId<n>` is
        empty)."""
        marks = [(0.0, ('clip', START)), (c['hand'], ('clip', RELEASE))]
        if c['anim'] == TAP:
            # The switch to 045011 shows once the blend into it has moved a bone far enough
            # (`release_shows`), not on the switch frame.
            lead = self.release_lead(c['mid'], TAP, c.get('lead'))
            marks.append((c['hand'] + lead + self.release_shows(c['mid'], lead), ('clip', TAP)))
        mg = self.magic[c['mid']]
        for f, n in self.release_events(c['mid'], c['anim'], c.get('lead'))[1]:
            if mg[f'refId{n}'] > 0:
                marks.append((c['hand'] + f, ('cast', mg[f'refCategory{n}'], mg[f'refId{n}'])))
        return marks

    def _cast_at(self, mid, anim, frame, n, lead=None):
        """(TimeAct, clip, its events, the event) of the cast event of ref `n` that release `anim`
        (let go at `lead`, `release_lead`) plays at real frame `frame` from the hand-off
        (`_cast_events`), or None."""
        hit = next(((clip, cf) for f, k, clip, cf in self._cast_events(mid, anim, lead)[1]
                    if k == n and abs(f - frame) < 0.06), None)
        if hit is None:
            return None
        clip, cf = hit
        c, ev = self._events(SPELL_TAE_BASE + self.magic[mid]['refType'], clip)
        real = ATK.clip_to_real(ev)
        pos = next((i for i, e in enumerate(ev) if e.type == TAE_CAST and struct.unpack_from('<b', e.params, 8)[0] + 1 == n
                    and abs(real(e.start) * FPS - cf) < 0.06), None)
        return None if pos is None else (c, clip, ev, pos)

    def cast_launch(self, mid, anim, frame, n, hand, weapon=None, lead=None):
        """`er-mechanics-ashes.bullet_launch` of the cast event (TAE 64) of ref `n` that release
        `anim` plays at real frame `frame` from the hand-off: its DummyPoly (the event's first int)
        placed by its clip's pose, the clip starting where the clips before it have carried the
        caster: the start clip (045000) to the hand-off, `hand` real frames in (`root_offset`), and
        for a tap's 045011 045010 to its release too (`release_lead`: `lead`, else the earliest).
        `weapon` is the catalyst's row (its model carries the 10xxx dummies); None leaves a prefixed
        dummy on the placeholder. None when the event is not in the release."""
        A, t = self.ash()
        got = self._cast_at(mid, anim, frame, n, lead)
        if got is None:
            return None
        c, clip, ev, pos = got
        e = ev[pos]
        cat = SPELL_TAE_BASE + self.magic[mid]['refType']
        base = None
        c0, ev0 = self._events(cat, START)
        if ev0 is not None and hand > 0:
            base = A.root_offset(c0, START, self._clip_seconds(ev0, hand))
        lead = self.release_lead(mid, clip, lead)
        if lead > 0:
            c1, ev1 = self._events(cat, RELEASE)
            base = _chain_root(base, A.root_offset(c1, RELEASE, self._clip_seconds(ev1, lead)))
        models = A.held_models(t, weapon) if weapon is not None else {}
        # Args +0 the DummyPoly, +5 the orientation byte (FUN_140429db0 -> FUN_1403c1450, 1.16.2).
        return A.bullet_launch(c, clip, e.start, struct.unpack_from('<i', e.params, 0)[0], models, base,
                               orient=e.params[5] if len(e.params) > 5 else 0)

    def cast_record(self, mid, anim, frame, n, lead=None):
        """The hit priority record the cast event (TAE 64) of ref `n` that release `anim` (let go at
        `lead`, `release_lead`) plays at real frame `frame` from the hand-off takes, as `HPR` (key,
        priority, created) for `er-mechanics-ashes.CaughtModel`, or None (`HIT_RECORD` in
        er-mechanics-attacks: a cast takes slot 0's record covering it when its Args byte 0xc is
        not negative, FUN_140429db0 -> FUN_1403c1450 `xor edx,edx`, 1.16.2; its shots and their
        children copy it, its priority and the launch's id, so the event's position `created` names
        the launch). The record is its own clip's (045010 for a tap's casts before its release)."""
        got = self._cast_at(mid, anim, frame, n, lead)
        if got is None:
            return None
        c, clip, ev, pos = got
        rec, prio = ATK.event_record(ev[pos], ATK.priority_records(ev, ATK.tae_content_version(c)))
        return (('cast', c, clip, rec), prio, pos) if rec is not None else None

    def spell_poise(self, atk_id):
        """Poise damage of one spell or goods hit, menu units: `atkSuperArmor` x `POISE_MENU` x the
        FinalDamageRateParam `saRate` (`INFERRED`: the flat branch, as the hit's damage is priced on
        the flat fields)."""
        A, t = self.ash()
        a = self.atk.get(atk_id) or {}
        fr = self.final_rate.get(a.get('finalDamageRateId', -1)) if a.get('finalDamageRateId', -1) >= 0 else None
        return (t.reg.atk.get(atk_id) or {}).get('atkSuperArmor', 0.0) * A.POISE_MENU * (fr['saRate'] if fr else 1.0)

    def attach_caught(self, mid, geo, hits, value, roots=None):
        """Give each cast-event geometry of `geo` (`geometry` output, its `EVENT`) the `CAUGHT`
        record `er-mechanics-ashes.CaughtModel` plays: the event's hit lists by the `full` rule
        (`bullet_lists`) that `hits` (`evaluate` entries, one per list) still price, each hit's
        share of the event's worth, its poise (`spell_poise`), and the event's share of its frame's
        worth in `value` ({frame: worth}). `roots` as `CastThreats`'s. Returns `geo`."""
        A, t = self.ash()
        m = self.magic.get(mid) if mid is not None else None
        for g, frame in geo or ():
            ev = g.get('EVENT')
            if ev is None:
                continue
            f, n = ev
            rid = roots.get((f, n)) if roots is not None else m[f'refId{n}']
            pool = {}
            for h in hits:
                if (h['frame'], h['ref']) == (f, n):
                    pool.setdefault((h['atk'], h['bullet']), []).append(h)
            lists, worth = [], 0.0
            for li in self.bullet_lists(rid) if rid else ():
                got = pool.get((li['atk'], li['bullet']))
                if not got:
                    continue
                h = got.pop(0)
                worth += h['damage'] * h['count']
                lists.append((li, h['damage']))
            if worth <= 0:
                continue
            # A record (`HPR`) holds back none of the event's lists: its shots and their children
            # are one launch and carry its id (`HIT_RECORD` in er-mechanics-attacks).
            g['CAUGHT'] = {'lists': [A.caught_list(t, li['atk'], self.spell_poise(li['atk']), li['ticks'], li['rec'],
                                                   li['mult'] * d / worth) for li, d in lists],
                           'wf': worth / value[frame] if value.get(frame) else 0.0}
        return geo

    def release_events(self, mid, anim, lead=None):
        """[(real frame from the hand-off, ref index 1..10)] for the TAE 64 events release `anim`
        plays (`_cast_events`): the clip's own, and for a tap 045010's before its release (`lead`,
        else its earliest), the 045011 ones after them."""
        c, ev = self._cast_events(mid, anim, lead)
        return c, [(f, n) for f, n, _, _ in ev]

    def ref_hits(self, mid, n):
        """[(atk id, bullet id or None, count)] one fired `refId<n>` can put on a target that stays
        in it (the `full` rule in the module doc), one entry per hit list."""
        m = self.magic[mid]
        kind, rid = m[f'refCategory{n}'], m[f'refId{n}']
        if rid <= 0:
            return []
        if kind == REF_ATTACK:
            return [(rid, None, 1.0)] if self._damaging(rid) else []
        if kind != REF_BULLET:
            return []
        return self.bullet_hits(rid)

    def bullet_hits(self, rid, damaging=None, base=None):
        """`ref_hits` of one fired bullet `rid`: its hit lists by the `full` rule. `damaging(atk id)`
        and `base(atk id)` say which AtkParams hit and which of a list's hits is its best; None is
        the spell rule (`_damaging`, `_base`: the flat attack fields). A weapon skill passes its
        own priced hits (`skill_attack`), whose bullet AtkParams scale off the weapon instead."""
        return [(li['atk'], li['bullet'], float(li['mult'] * li['ticks'])) for li in self.bullet_lists(rid, damaging, base)]

    def bullet_lists(self, rid, damaging=None, base=None):
        """`bullet_hits` before the count is multiplied out: [{'atk', 'bullet', 'mult' (instances
        that each keep the list), 'ticks' (hits per instance on a target that stays in it), 'rec'
        (`dmgHitRecordLifeTime` s between them)}], what `er-mechanics-ashes.CaughtModel` plays
        tick by tick."""
        damaging = self._damaging if damaging is None else damaging
        base = self._base if base is None else base
        A = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
        t = self.ash()[1]
        lists = []

        def walk(b, instances, record, before, depth, shot=None, path=(), lifetime=0.0):
            """`record`: the list of the DmgHitRecord this launch inherits from its parent (module
            doc, "Hits on one target"), or None. `lifetime`: the record lifetime its attack info
            carries down (`record_lifetime`)."""
            r = self.bullet.get(b)
            if r is None or depth > MAX_DEPTH or instances <= 0 or b in path:
                return
            parent = path[-1] if path else None
            path += (b,)
            shots = max(r['numShoot'], 1)
            life = max(r['life'], 0.0)
            lifetime = record_lifetime(lifetime, r)
            recorded = bool(r['isUseSharedHitList'] or r['attachEffectType'])
            if record is not None:
                # The parent's record, whatever this row's own flag (FUN_14038b740 copies it).
                own = record
            elif shot is not None and not A.launches(t, parent, b, 'hit'):
                # Not made when the shot before it hits him (its gate is that shot's own value,
                # `launches`), so made only when it missed: a shot that hit him ended there.
                own = shot
            else:
                # A new record for the launch, which its whole `numShoot` volley shares
                # (FUN_14038fe80), or none: a list for every bullet.
                own = {'mult': instances * (1 if recorded else shots), 'rec': lifetime,
                       'contact': 0.0, 'best': None, 'row': b, 'record': recorded}
                lists.append(own)
                before = 0.0
            # What its children inherit: its record, unless its row's `attachEffectType` drops it
            # (FUN_14039bba0 reads the row of its current state, its own).
            down = own if (record is not None or recorded) and not r['attachEffectType'] else None
            touch = before + self.contact(r)
            if damaging(r['atkId_Bullet']):
                own['contact'] = max(own['contact'], touch)
                if own['best'] is None or base(r['atkId_Bullet']) > base(own['best'][0]):
                    own['best'] = (r['atkId_Bullet'], b)
            hit = r['HitBulletID']
            if hit > 0 and (A.launches(t, b, hit, 'hit') or A.launches(t, b, hit, 'expire')):
                ends = damaging(r['atkId_Bullet']) and not r['isPenetrateChr']
                walk(hit, instances * shots, down, 0.0, depth + 1, own if ends else shot, path, lifetime)
            iv = (r['intervalCreateTimeMin'] + r['intervalCreateTimeMax']) / 2
            if r['intervalCreateBulletId'] > 0 and iv > 0 and life > 0:
                # None at its creation, the first one interval after the wait, one an interval
                # after that while it has life left: one per 0.1 s timer at 0.05 s, not three.
                k = len(A.interval_frames(r, interval=iv))
                # A trail: the children dropped while the parent touched the target outlast it.
                walk(r['intervalCreateBulletId'], instances * shots * k, down, touch, depth + 1, None, path, lifetime)

        walk(rid, 1, None, 0.0, 0)
        flown = self.record_hits(rid, damaging) if any(li['record'] and li['best'] for li in lists) else None
        out, done = [], set()
        for li in lists:
            if li['best'] is None:
                continue
            if li['record'] and flown is not None and li['row'] in flown['reached']:
                # A record's hits are the ones its members land flown against him (`record_hits`).
                # One the flight never brings to him at any distance keeps the count below: the
                # flight's own gaps (module doc, "Hits on one target") are not the record's.
                if li['row'] in done:
                    continue
                done.add(li['row'])
                rec, per = flown['records'].get(li['row'], (li['rec'], []))
                for hits, n in sorted(collections.Counter(h for h in per if h > 0).items()):
                    out.append({'atk': li['best'][0], 'bullet': li['best'][1], 'mult': n, 'ticks': hits,
                                'rec': rec if hits > 1 else 0.0})
                continue
            rec = li['rec']
            hits = 1 + int(li['contact'] // rec) if rec > 0 and li['contact'] > rec else 1
            out.append({'atk': li['best'][0], 'bullet': li['best'][1], 'mult': li['mult'], 'ticks': hits,
                        'rec': rec if hits > 1 else 0.0})
        return out

    def _tree_rows(self, rid):
        """Bullet rows a fired `rid` can reach through `HitBulletID` and `intervalCreateBulletId`."""
        seen, todo = set(), [rid]
        while todo:
            b = todo.pop()
            r = self.bullet.get(b)
            if r is None or b in seen:
                continue
            seen.add(b)
            todo += [c for c in (r['HitBulletID'], r['intervalCreateBulletId']) if c > 0]
        return seen

    def record_hits(self, rid, damaging=None):
        """The hits each DmgHitRecord of a fired `rid` lands on a defender standing `d` m straight
        ahead, the `d` of `RECORD_TARGET_M` that lands the most: {'d', 'records': {creating row:
        (lifetime, [hits of each record that row makes])}, 'reached': the creating rows whose
        records land a hit at some spot, 'bearing': {creating row: (d, bearing)} for those taken off
        the axis}. Every bullet of the tree is flown in 3D (`er-mechanics-ashes.bullet_flight`,
        launch frames, aim at creation and homing on his lock-on point as `_fly_records` lists),
        and the cast is played in frame order against him. A record that lands nothing at any `d`
        on the axis (its shots do not aim at him) is played at `RECORD_BEARINGS_DEG` too, and the
        spot that lands its records the most is taken for them. Cached per root and the set of its
        AtkParams that `damaging` counts."""
        damaging = self._damaging if damaging is None else damaging
        rows = self._tree_rows(rid)
        key = (rid, frozenset(self.bullet[b]['atkId_Bullet'] for b in rows
                              if damaging(self.bullet[b]['atkId_Bullet'])))
        if key not in self._records:
            best, reached, made = None, set(), set()
            for d in RECORD_TARGET_M:
                got = self._fly_records(rid, damaging, d)
                total = sum(sum(per) for _, per in got.values())
                reached |= {row for row, (_, per) in got.items() if any(per)}
                made |= set(got)
                if best is None or total > best[0]:
                    best = (total, d, got)
            records, bearing = dict(best[2]), {}
            missing = made - reached
            if missing:
                # Shots that do not aim at him (a fan at fixed yaws, a meteor at a fixed offset) miss
                # him on the caster's axis: such a record is played with him off it, the caster
                # keeping his facing and the aimed members still aiming at him.
                off = None
                for b in RECORD_BEARINGS_DEG:
                    for d in RECORD_TARGET_M:
                        got = self._fly_records(rid, damaging, d, b)
                        total = sum(sum(got[row][1]) for row in missing if row in got)
                        if total and (off is None or total > off[0]):
                            off = (total, d, b, got)
                if off is not None:
                    for row in missing:
                        if row in off[3] and any(off[3][row][1]):
                            records[row] = off[3][row]
                            reached.add(row)
                            bearing[row] = (off[1], off[2])
            self._records[key] = {'d': best[1], 'records': records, 'reached': reached, 'bearing': bearing}
        return self._records[key]

    def _fly_records(self, rid, damaging, d, bearing=0.0):
        """{creating row: (lifetime, [hits per record])} of one cast of `rid` at a defender standing
        `d` m from the caster at `bearing` degrees off his facing (+x 90), facing him
        (`record_hits`). The rules (`VERIFIED` 2026-11-12 unless marked, 1.16.2 named dump; module
        doc, "Hits on one target"):

        - a bullet touches him on a frame its sphere, swept from where it was the frame before to
          where it is (FUN_1405226e0: the attack's shape is cast from DmgIns +0x200, last frame's
          +0x1d0, to +0x1d0 in FUN_140523e80's substeps, FUN_1405232a0 each; a bullet's DmgIns,
          made by FUN_140526230, never gets the +0x190 that sends the others to FUN_140524c20),
          meets his idle hurtbox (`er-mechanics-reach.contact_disc_radius`, sampled every
          `er-mechanics-ashes.SWEEP_STEP_M`, 0.05 m, the hurtbox profile's own slice: a 999 m/s
          seeker moves 16.65 m a frame and is tested along all of it; `path_touches`), when its
          AtkParam targets opponents (`opposeTarget`): FUN_140523710 adds
          him to its records only past the victim check FUN_14051af50, whose team gate reads it
          (attacks.md, `FUN_14051af50 -> FUN_14044a910 -> FUN_1404443e0`). AtkParam_Pc 1, which
          many markers carry, targets no one, so they touch nothing; AtkParam_Pc 0 targets
          opponents (`REGULATION`), so Light of Miquella's seeker stops on him;
        - a touch lands unless he is in the bullet's own record or its shared one (FUN_140523710
          returns at FUN_14051c5d0 for either). A landed touch puts him in both (FUN_140524910 ->
          FUN_140523540), and each entry lapses after its record's lifetime (FUN_14051cfb0 stores
          it, FUN_14051c610 ages it); a lifetime of 0 never lapses. The own record's lifetime is the
          bullet's (FUN_1403960a0 makes it from CSBulletIns +0xae0), the shared one's its maker's
          (FUN_14038fe80 passes entry +0x960): both `record_lifetime`;
        - a touch counts as a hit when the bullet's AtkParam is `damaging`;
        - a bullet that does not pass through characters (`isPenetrateChr` 0) dies on a landed
          touch (FUN_1403aba90 -> KillBullet(4)) and makes its `HitBulletID` at the contact point
          if its own `launchConditionType` lets it (`launches`) and it does not slide (`FollowType`
          4, FUN_140398ac0). One that passes through makes none on him. A touch a record blocks
          leaves it flying: FUN_140523710 returns before InitDamageStruct, and the bullet's hit
          (FUN_140398ba0, and its map and object twins) is reached only through FUN_14051af50 ->
          FUN_14051acc0 -> FUN_14038b2f0 / FUN_14038b610, FUN_14051af50's one caller being
          FUN_140523710 and FUN_14051acc0's FUN_14051af50;
        - at the end of its flight it makes its `HitBulletID` by `launches` (a map hit or expiry),
          and its interval children while it lives (`er-mechanics-ashes.interval_frames`, the mean
          interval after its drawn first one, as `bullet_lists` counts them);
        - each bullet flies, and each shot and child is launched, as every flight of the model is
          (`er-mechanics-ashes.row_shots`, `_fly_rows`, `_frames_at`, `_bchild_frame`, the flight
          `fly_members` gives the punish geometry): every shot leaves its spawn frame as
          FUN_140390c20 builds it and every child its parent's frame of that moment as FUN_14039bba0
          builds it, in 3D ("Launch frames": the shoot angles in the frame, the random spread as its
          expected layout, `sfxPostureType` 1's levelling, `isOwnerOverrideInitAngle`,
          `EmittePosType` 1's ground point), aimed at and homing on his lock-on point (`lock_point`,
          the DummyPoly 220 FUN_140394910 returns for a lock target, plus the row's
          `targetYOffsetRange`), each with its drawn life;
        - a row attached by `FollowType` follows its point: 3 his lock point (the launch target in
          CSBulletIns +0xd30, FUN_14039e8a0), 5 the caster's (0, hitHeight / 2, 0) (FUN_140394350),
          1 and 2 the cast's DummyPoly, the root's fixed placement here (FUN_140394670 with
          CSBulletIns +0x4b8; that the caster's hand stays put over its life is `INFERRED`);
        - records are handed down as `bullet_lists` hands them (FUN_14038b740, FUN_14039bba0).
        A fan's shots are flown one by one; the root leaves `er-mechanics-ashes`'s fixed placement
        (`INFERRED`) facing +z, and travel to him is in the flight. The punish flight differs only in
        what it reads off the same flights: a damaging bullet is not stopped by him there, since a
        first touch is all it reads (`er-mechanics-ashes.carrier`)."""
        import heapq
        import numpy as np
        A, t = self.ash()
        b0 = math.radians(bearing)
        target = (d * math.sin(b0), d * math.cos(b0))
        tx, tz = np.array([target[0]]), np.array([target[1]])
        fps = A.BULLET_FPS
        pos0, yaw0, pitch0, _ = A._launch_start(None)
        owner = np.array(A.yaw_frame(yaw0))
        cast = np.array([pos0], float)
        back = np.array([(0.0, A.FOLLOW_RETURN_SHARE * A.PLAYER_HIT_HEIGHT_M, 0.0)])
        lock = A._lock_at(tx, tz)
        records, bullets, heap, seq = [], [], [], [0]
        order = {'touch': 0, 'spawn': 1, 'end': 2}

        def push(f, kind, i, k, at=None):
            seq[0] += 1
            heapq.heappush(heap, (f, order[kind], seq[0], kind, i, k, at))

        def launch(b, pos, frame, speed, f0, rec, lifetime, depth, slot=(0, 1)):
            r = t.bullet.get(b)
            if r is None or depth > MAX_DEPTH or len(bullets) > RECORD_BULLET_CAP:
                return
            lifetime = record_lifetime(lifetime, r)
            if rec is None and (r['isUseSharedHitList'] or r['attachEffectType']):
                rec = len(records)
                records.append({'row': b, 'L': lifetime, 'ready': -math.inf, 'hits': 0})
            down = None if r['attachEffectType'] else rec
            radius = max(r['hitRadius'], r['hitRadiusMax'])
            iv = (r['intervalCreateTimeMin'] + r['intervalCreateTimeMax']) / 2
            # The point an aimed shot turns toward and a homing one homes on: his lock-on point
            # plus the row's `targetYOffsetRange` (FUN_140390c20, FUN_140398270).
            point = A._lock_at(tx, tz, r.get('targetYOffsetRange') or 0.0)
            follow = None
            if r['FollowType'] in A.FOLLOW_ATTACHED:
                ft = int(r['FollowType'])
                spot = lock if ft == 3 else back if ft == 5 else cast
                follow = lambda k, spot=spot: spot  # noqa: E731
            steer = (lambda k, point=point: point) if A.homes(r) else None
            hits = (self.atk.get(r['atkId_Bullet']) or {}).get('opposeTarget')
            for start, fr, life, first in A.row_shots(r, np.array([pos], float), np.array([frame], float), point,
                                                      True, np.array([target]), slot):
                P, L, ended, D, S = A._fly_rows(r, start, A._bnorm(fr[:, 2, :]), speed, life, steer, follow, True)
                n = int(L[0])
                idx = len(bullets)
                bullets.append({'b': b, 'r': r, 'P': P, 'n': n, 'rec': rec, 'down': down, 'L': lifetime,
                                'alive': True, 'own': -math.inf, 'ending': 'hit' if ended[0] else 'expire', 'D': D,
                                'S': S, 'fr': fr, 'depth': depth})
                if hits:
                    for k, at in A.path_touches(P[:n, 0], radius, target):
                        push(f0 + k, 'touch', idx, k, at)
                if r['intervalCreateBulletId'] > 0 and iv > 0:
                    spawn = A.interval_frames(r, n, interval=iv, first=first, life=life)
                    for j, k in enumerate(spawn):
                        push(f0 + k, 'spawn', idx, k, (j, len(spawn)))
                push(f0 + n - 1, 'end', idx, n - 1)

        def child(bl, c, k, f, how, at=None, slot=(0, 1)):
            row = t.bullet.get(c)
            if row is None:
                return
            # FUN_14039bba0: the parent's frame of that moment, made the child's; a hit child at the
            # contact point, the others where the parent is.
            end = how in ('hit', 'expire')
            fr = A._frames_at(bl['P'], np.array([k]), np.array([0]), bl['fr'], bl['D'], bl['S'],
                              np.array([end or (how == 'spawn' and k == bl['n'] - 1)]))
            base = A._bchild_frame(row, fr, owner)[0]
            speed = None
            if bl['r']['isInheritSpeedToChild'] and how != 'spawn':
                step = bl['P'][k, 0] - bl['P'][max(k - 1, 0), 0]
                speed = bl['S'] if end or k == 0 else np.array([math.sqrt(float(step @ step)) * fps])
            launch(c, bl['P'][k, 0] if at is None else at, base, speed, f, bl['down'], bl['L'], bl['depth'] + 1, slot)

        launch(rid, pos0, A.look_frame(A._unit(yaw0, pitch0)), None, 0, None, 0.0, 0)
        while heap:
            f, _, _, kind, i, k, at = heapq.heappop(heap)
            bl = bullets[i]
            if not bl['alive']:
                continue
            r = bl['r']
            if kind == 'touch':
                now = f / fps
                rc = records[bl['rec']] if bl['rec'] is not None else None
                if now < bl['own'] - 1e-9 or (rc is not None and now < rc['ready'] - 1e-9):
                    continue
                bl['own'] = now + bl['L'] if bl['L'] > 0 else math.inf
                if rc is not None:
                    rc['ready'] = now + rc['L'] if rc['L'] > 0 else math.inf
                    if damaging(r['atkId_Bullet']):
                        rc['hits'] += 1
                if not r['isPenetrateChr']:
                    bl['alive'] = False
                    c = r['HitBulletID']
                    if c > 0 and r['FollowType'] != 4 and A.launches(t, bl['b'], c, 'hit'):
                        child(bl, c, k, f, 'contact', at)
            elif kind == 'spawn':
                child(bl, r['intervalCreateBulletId'], k, f, 'spawn', slot=at)
            else:
                bl['alive'] = False
                c = r['HitBulletID']
                if c > 0 and A.launches(t, bl['b'], c, bl['ending']):
                    child(bl, c, k, f, bl['ending'])
        out = {}
        for rc in records:
            out.setdefault(rc['row'], (rc['L'], []))[1].append(rc['hits'])
        return out

    def contact(self, r):
        """Seconds one bullet can touch a target standing in its path: 0 unless it penetrates or
        is endless (it ends on the hit); its whole life when it stands still or follows its owner;
        else the time to pass through the target's depth plus its own diameter."""
        if not (r['isPenetrateChr'] or r['isEndlessHit']):
            return 0.0
        life = max(r['life'], 0.0)
        speeds = [v for v in (r['initVellocity'], r['maxVellocity']) if v > 0]
        if r['FollowType'] or not speeds:
            return life
        return min(life, (2 * max(r['hitRadius'], r['hitRadiusMax']) + BODY_DEPTH) / min(speeds))

    def _base(self, atk_id):
        a = self.atk.get(atk_id)
        return sum(a[f] for f in ATK_FIELD.values()) if a else 0

    def _damaging(self, atk_id):
        a = self.atk.get(atk_id)
        return bool(a) and any(a[f] > 0 for f in ATK_FIELD.values())

    def phys_type(self, atk_id, bullet_id, cat):
        attr = self.atk.get(atk_id, {}).get('atkAttribute', 3)
        b = self.bullet.get(bullet_id) if bullet_id is not None else None
        if b is not None and b['atkAttribute'] != 254:
            attr = b['atkAttribute']
        if attr == 253:
            attr = cat['wep']['atkAttribute']
        elif attr == 252:
            attr = cat['wep']['atkAttribute2']
        return PHYS_TYPE.get(attr, 'none')

    # -- what the build wears ---------------------------------------------------------------

    def rates(self, mid, cat, talismans=(), left=None):
        """({element: attack rate}, {element: PvP correct rate}, casting-speed bonus) from the
        talismans and held weapons' residents this spell's context admits (`CastModel`)."""
        sp = self.cast.spell(mid)
        rows = self.cast._rows_of(talismans, (cat['name'], left))
        bonus = sum(r.get(CS.CAST_SPEED_FIELD, 0) for _, r in rows)
        rate = {e: 1.0 for e in ATK_FIELD}
        correct = dict(rate)
        for _, r in rows:
            if not self.cast._applies(sp, r):
                continue
            for e in rate:
                rate[e] = T.f32(rate[e] * T.f32(r.get(T.ATTACK_RATE[e], 1.0)))
                correct[e] *= r.get('atkPlayerDmgCorrectRate_' + T.EL_SUFFIX[e], 1.0)
        # `*AttackRate` reaches the damage as a whole percent (FUN_140d278a0, talismans.py).
        rate = {e: int(T.f32(v * T.f32(100.0))) / 100.0 for e, v in rate.items()}
        return rate, correct, bonus

    def defender(self, rl):
        """The median defender of the RL window (`er-builds-optimize.bracket_defender`), plus his
        `vig` (the median Vigor of the same builds), `hp` (CalcCorrectGraph row 100 at it, the HP
        curve `PerformCalcCorrection` reads, `er-mechanics-defense.calc_correct`) and `poise` (the
        median `poise.original` the planner computes): what one cast can land on him at most, and
        the poise a caught defender holds with (`er-mechanics-ashes.CaughtModel`)."""
        if rl not in self._defender:
            opt = _mod('er_builds_optimize', 'er-builds-optimize.py')
            rows = opt.corpus_rows(opt.CACHE / 'builds.jsonl', rl - WINDOW, rl + WINDOW)
            d = dict(opt.bracket_defender(rows))
            have = [r for r in rows if r['computed'].get('defenses') and r['computed'].get('absorption')]
            vig = statistics.median(r['stats']['vig'] for r in have)
            poise = [p for p in ((r['computed'].get('poise') or {}).get('original') for r in have) if p is not None]
            d.update(vig=vig, hp=DEF.calc_correct(self.def_tables().graph[HP_GRAPH], vig),
                     poise=statistics.median(poise) if poise else None)
            self._defender[rl] = d
        return self._defender[rl]

    def def_tables(self):
        if self._def_t is None:
            self._def_t = DEF.Tables()
        return self._def_t

    # -- the number --------------------------------------------------------------------------

    def hit(self, atk_id, bullet_id, cat, stats, rate, correct, defender, two_handed=False, pvp=True):
        """AR by element and damage of one hit on `defender`."""
        a = self.atk[atk_id]
        ar = {}
        for e, f in ATK_FIELD.items():
            if a[f] > 0:
                ar[e] = a[f] * self.multiplier(cat, e, stats, a, two_handed) * rate[e] * (correct[e] if pvp else 1.0)
        fr = self.final_rate.get(a['finalDamageRateId']) if pvp and a['finalDamageRateId'] >= 0 else None
        pt = self.phys_type(atk_id, bullet_id, cat)
        dmg = DEF.damage(ar, 100.0, defender, pt, fr)['total']
        return {'ar': {e: round(v, 2) for e, v in ar.items()}, 'damage': dmg, 'phys_type': pt,
                'final_rate': fr['magRate'] if fr else 1.0}

    def evaluate(self, mid, cat, stats, talismans=(), left=None, defender=None, two_handed=False, pvp=True,
                 lead=None):
        """Every variant of one spell: hits, one_hit, full, FP and frames. A variant that releases
        from 045010 (a tap, an early release) is let go at its earliest release, or at `lead` (real
        frames into 045010, `release_lead`) when given; 'releases' are its `releases`, the leads it
        may be let go at."""
        rate, correct, bonus = self.rates(mid, cat, talismans, left)
        m = self.magic[mid]
        sp = self.cast.spell(mid)
        value = CS.casting_value(stats.get('dex', 10), bonus, sp['lo'], sp['hi'])
        hand = self.handoff(mid, value)
        out = []
        for label, anim in self.variants(mid):
            c, events = self.release_events(mid, anim, lead)
            hits, first = [], None
            for frame, n in events:
                for atk_id, bid, count in self.ref_hits(mid, n):
                    h = self.hit(atk_id, bid, cat, stats, rate, correct, defender, two_handed, pvp)
                    hits.append(dict(h, ref=n, atk=atk_id, bullet=bid, count=count, frame=round(frame, 1)))
                    first = frame if first is None else min(first, frame)
            if not hits:
                continue
            info = D.clip(c, anim) if c is not None else None
            g = (info or {}).get('gates') or {}
            length = (info or {}).get('length') or 0.0
            gate = min((v for v in (g.get('r1'), g.get('roll')) if v is not None), default=length)
            roll = g.get('roll') if g.get('roll') is not None else length
            fp = m['mp_charge'] if label == 'charged' and m['mp_charge'] > 0 else m['mp']
            full = sum(h['damage'] * h['count'] for h in hits)
            # A tap plays 045010 up to its release first (`tap_release`), at once its earliest
            # unless `lead` names a later one.
            tr = self.tap_release(mid) if anim == TAP else None
            at = self.release_lead(mid, anim, lead)
            act = hand + at + gate
            out.append({'variant': label, 'anim': anim, 'fp': fp, 'fp_cast': m['mp'],
                        'fp_loop': m['consumeLoopMP_forMenu'] if m['consumeLoopMP_forMenu'] > 0 else None,
                        'startup': round(hand + first, 1), 'act': round(act, 1),
                        'roll': round(hand + at + roll, 1), 'lead': at,
                        'window': tr['window'] if tr else None, 'releases': self.releases(mid, anim),
                        'one_hit': round(max(h['damage'] for h in hits), 1), 'full': round(full, 1),
                        'full_per_fp': round(full / fp, 2) if fp else None,
                        'full_per_s': round(full / (act / FPS), 1) if act > 0 else None,
                        'channel': bool(self._events(SPELL_TAE_BASE + m['refType'], LOOP)[1]),
                        'hits': hits})
        return out

    def table(self, cats, stats, talismans=(), left=None, rl=150, two_handed=False, pvp=True, only=None):
        """[(spell row, variant result)] for every player attack spell a catalyst casts and the stats
        meet; plus {name: why} for the ones left out."""
        dfn = self.defender(rl)
        rows, skipped = [], {}
        for mid, nm in sorted(self.names.items(), key=lambda x: x[1]):
            if only and T._norm(nm) not in only:
                continue
            m = self.magic[mid]
            if not m['enable_multi']:
                skipped[nm] = 'not usable in multiplayer'
                continue
            cat = next((c for c in cats if self.can_cast(mid, c)), None)
            if cat is None:
                skipped[nm] = 'no catalyst of its school'
                continue
            short = self.short(mid, stats)
            if short:
                skipped[nm] = 'short ' + ', '.join(f'{k} {v}' for k, v in short.items())
                continue
            try:
                got = self.evaluate(mid, cat, stats, talismans, left, dfn, two_handed, pvp)
            except KeyError as e:
                skipped[nm] = f'unreadable: {e}'
                continue
            if not got:
                if only:
                    skipped[nm] = 'no damaging hit'
                continue
            for v in got:
                rows.append(({'id': mid, 'name': nm, 'catalyst': cat['name'], 'slots': m['slotLength']}, v))
        return rows, skipped

    # -- the kit -----------------------------------------------------------------------------

    def ash(self):
        if self._ash is None:
            self._ash = (_mod('er_mechanics_ashes', 'er-mechanics-ashes.py'),)
            self._ash += (self._ash[0].AshTables(),)
        return self._ash

    def floor_aims(self, mid, variant, damaging=None):
        """Whether a free-aimed `variant` of spell `mid` can burst a carrier on the ground: one of
        its events flies a tree whose damage is only on the child the carrier's ground contact
        creates (`er-mechanics-ashes.floor_bursts`)."""
        A, t = self.ash()
        m = self.magic[mid]
        test = self.spell_node_damaging if damaging is None else damaging
        return any(m[f'refCategory{n}'] == REF_BULLET and A.floor_bursts(t, A.bullet_tree(t, m[f'refId{n}']), test)
                   for _, n in variant['events'])

    def cast_windows(self, mid, variant):
        """The cast's TAE 795 windows: ((start, end, poise bonus, poise-taken multiplier, floor),
        ...) in real frames from the press, as `er-mechanics-interrupt._window` reads a weapon's
        (`VERIFIED` there: handler 0x14042c8c0, update 0x140487490), the catalyst's
        `toughnessCorrectRate` in the bonus (`variant['weapon']`, the base row: every level of the
        Glintstone Staff and the Finger Seal holds 0.011, `REGULATION`). The start clip's up to the
        hand-off, then the release clip's (a tap: 045010 up to its release, then 045011), the reading
        `er-mechanics-line-fit` `GameWorld.cast_seq` makes for the windup interrupt. 20 of the 217
        player spells have one (Fortissax's Light Spear: row 180 on 045010 f15-96, +3.3 poise and
        half the poise damage taken; `REGULATION` + `TAE`, 2026-11-13). () for a variant with no
        release clip (`anim`) or hand-off; `geometry` puts them on every geometry as `TOUGHNESS`,
        what the defender's strike reads (`strike_breaks`)."""
        if variant.get('anim') is None or variant.get('hand') is None:
            return ()
        A, t = self.ash()
        INT = _interrupt()
        refcat = SPELL_TAE_BASE + self.magic[mid]['refType']
        hand, lead = float(variant['hand']), float(variant.get('lead') or 0.0)
        clips = [(START, 0.0, hand)]
        if variant['anim'] == TAP and lead:
            clips += [(RELEASE, hand, hand + lead), (TAP, hand + lead, math.inf)]
        else:
            clips.append((variant['anim'], hand, math.inf))
        wep = self.ar.weapons.get(variant.get('weapon')) or {'toughnessCorrectRate': 0.0}
        out = []
        for anim, off, end in clips:
            _, ev = self._events(refcat, anim)
            if not ev:
                continue
            real = ATK.clip_to_real(ev)
            for e in ev:
                if e.type != ATK.TAE_TOUGHNESS:
                    continue
                s0 = off + ATK.real_frame(real(e.start))
                if s0 >= end:
                    continue
                s1 = min(off + ATK.real_frame(real(e.end)), end)
                ratio = struct.unpack_from('<f', bytes(e.params), 4)[0]
                out.append(tuple(float(x) for x in INT._window(t.reg, wep, e.params[0], e.params[1], ratio, (s0, s1))))
        return tuple(out)

    def geometry(self, mid, variant, damaging=None, missing=None, floor=None):
        """(per damaging cast event in frame order: `contact_geometry` of its bullet tree flown
        straight ahead from the press, its real frame; the geometry's `EVENT` says which (frame,
        ref) it is), or None when an event has no flight (a direct AtkParam hit, whose reach is not
        modelled here). With `variant['anim']` (the release clip) each event's root shot leaves
        where `cast_launch` puts it, the dummies of `variant['weapon']` (the catalyst row) read,
        a release let go at `variant['lead']` when given (`release_lead`); without, the old fixed
        placement. An event whose every damaging AtkParam has
        `isInvalidatedByNoDamageInAir` is marked for the jump (`air`). A homing event's `HOMING`
        record is flown aimed (`flight` with `aim`): each shot aimed at where his dodge has taken
        him when it is created, then turning after him.

        `damaging` is `flight`'s node test (default `spell_node_damaging`, the flat fields a spell's
        bullet is priced on). With `missing` a list, an
        event without a flight is appended to it as (frame, ref, reason) and the rest are still
        flown, so the result is the events that have one, or None when none has (`MISS_*` reasons,
        `flight_miss`); without it, an event that is not a bullet ref, or has no tree, makes the
        whole cast None and one whose flight is None is skipped, as before.

        With `floor` (metres) the cast is free-aimed (`er-mechanics-ashes.floor_launch`): each event
        whose tree `floor_bursts` is pitched so its carrier meets the ground `floor` m ahead of the
        caster's start, the rest leave level, and nothing homes (no lock-on target). None when no
        event can be put down there."""
        A, t = self.ash()
        m = self.magic[mid]
        test = self.spell_node_damaging if damaging is None else damaging
        out, grounded = [], False
        for frame, n in variant['events']:
            if m[f'refCategory{n}'] != REF_BULLET:
                if missing is None:
                    return None
                missing.append((frame, n, MISS_DIRECT))
                continue
            bt = A.bullet_tree(t, m[f'refId{n}'])
            if bt is None:
                if missing is None:
                    return None
                missing.append((frame, n, MISS_TREE))
                continue
            launch = self.cast_launch(mid, variant['anim'], frame, n, variant['hand'], variant.get('weapon'),
                                      variant.get('lead')) if variant.get('anim') is not None else None
            if floor is not None:
                down = A.floor_launch(t, bt, launch, floor) if A.floor_bursts(t, bt, test) else None
                grounded = grounded or down is not None
                launch = down or {**(launch or {'pos': A._launch_start(None)[0], 'yaw': 0.0, 'pitch': 0.0,
                                                 'peak': -math.inf}), 'aim': False}
            geom = self.flight(bt, (variant['hand'] + frame) / FPS, damaging, launch=launch, homing=floor is None,
                               aim=True)
            if geom is not None:
                geom['EVENT'] = (frame, n)
                rec = self.cast_record(mid, variant['anim'], frame, n, variant.get('lead')) \
                    if variant.get('anim') is not None else None
                if rec is not None:
                    geom['HPR'] = rec
                out.append((geom, frame))
            elif missing is not None:
                missing.append((frame, n, self.flight_miss(bt, damaging)))
        if floor is not None and not grounded:
            return None
        wins = self.cast_windows(mid, variant) if out else ()
        for g, _ in out if wins else ():
            g['TOUGHNESS'] = wins
        return out or None

    def flight_miss(self, bt, damaging=None):
        """Why `flight` of tree `bt` is None, the first of: `MISS_HEIGHT` (damaging nodes fly but
        no point of them is at a height the idle hurtbox has a contact disc at: Meteorite's charged
        meteor), `MISS_LAUNCH` with the launch conditions that stop them (a damaging node of the
        tree is never flown: `er-mechanics-ashes.fly_tree` launches a child only on the parent
        endings its `launchConditionType` admits, `LAUNCH_ON`, and 5, 'on a hit', needs a character
        the flight never hits: Death Lightning, whose bolt does no damage and whose damage is its
        on-hit child), `MISS_OTHER`. `damaging` as `flight`'s."""
        A, t = self.ash()
        test = self.spell_node_damaging if damaging is None else damaging
        reach_mod = A._pose()[1]
        profile = reach_mod.defender_profile('idle')
        flown = [(node, path) for node, path, _, _ in A.fly_tree(t, bt) if test(node)]
        if flown and all(reach_mod.contact_disc_radius(q[1], node['radius_m'], profile) is None
                         for node, path in flown for q in path):
            return MISS_HEIGHT
        gaps = self.launch_gaps(bt)
        stopped, todo = [], [bt]
        while todo:
            node = todo.pop()
            if node is None:
                continue
            if test(node) and node['bullet'] in gaps:
                stopped.append(gaps[node['bullet']])
            todo += list(node['children'].values())
        if stopped:
            return f'{MISS_LAUNCH} ({launch_text(stopped)})'
        return MISS_OTHER

    def launch_gaps(self, bt, launch=None):
        """{bullet id: why} for every node of tree `bt` that `er-mechanics-ashes.fly_tree` never
        flies straight ahead, read off the topmost node on its path that is not flown: its
        `launchConditionType` (an int) when it is a `HitBulletID` child, 'interval' when it is an
        interval child the flight never spawns (Watchful Spirit's 210760001: its root follows the
        caster, `FollowType` 1, and is flown as one point, so its 2 s wait never runs out), None
        past the depth cap or with no Bullet row. Roiling Magma's hit 10482004 (condition 0) hangs
        under 10482002 (5, on a hit): 5. A node flown on another branch counts as flown. `launch`
        as `flight`'s: the launch height decides whether a falling shot meets the ground in time."""
        A, t = self.ash()
        flown = {node['bullet'] for node, _, _, _ in A.fly_tree(t, bt, launch=launch)}
        out = {}

        def gate(parent, b):
            # The value that kept it from being made (`er-mechanics-ashes.launches`): the parent's
            # own on a hit, the child's on expiry; when both refuse, the one no dry ground meets.
            p, c = t.bullet.get(parent) or {}, t.bullet[b]
            on_hit = p.get('launchConditionType', 0)
            on_exp = on_hit if p.get('attachEffectType', 0) in A.GATE_KEEPS_PARENT else c['launchConditionType']
            hit_ok, exp_ok = A.launches(t, parent, b, 'hit'), A.launches(t, parent, b, 'expire')
            if hit_ok and not exp_ok:
                return on_exp
            if exp_ok and not hit_ok:
                return on_hit
            if not hit_ok and not exp_ok:
                return on_exp if on_exp in NEVER_ON_LAND or on_hit not in NEVER_ON_LAND else on_hit
            return c['launchConditionType']

        def walk(node, gap, depth, slot, parent=None):
            if node is None or depth > MAX_DEPTH:
                return
            b = node['bullet']
            if b in flown:
                here = _FLOWN
            elif gap is _FLOWN:
                r = t.bullet.get(b)
                here = None if r is None else 'interval' if slot == 'intervalCreateBulletId' else gate(parent, b)
            else:
                here = gap
            if here is not _FLOWN:
                out.setdefault(b, here)
            for key, child in node['children'].items():
                walk(child, here, depth + 1, key, b)
        if bt:
            walk(bt, _FLOWN, 0, None)
        return out

    def spell_node_damaging(self, node):
        """`flight`'s node test for a spell or a goods bullet, and its default: the node's AtkParam
        has a flat attack field (`_damaging`), which is how such a bullet is priced (`FUN_1406832a0`'s
        flat branch, module doc "One hit", `VERIFIED`). The ash model's `_bullet_damages_self` reads
        the weapon-scaled correction fields, which nine spells' damaging AtkParams leave at 0
        (Bayle's Flame Lightning, Bayle's Tyranny, Comet Azur, Frenzied Burst, Gelmir's Fury, Howl of
        Shabriri, Night Maiden's Mist, Swarm of Flies, Unendurable Frenzy); a weapon skill's bullet
        takes the weapon branch and keeps that test (`skill_attack`)."""
        return self._damaging(node['atk_row'])

    def _path_points(self, node, path, t0, start, launch=None):
        """[(seconds, x, z, contact disc)] of one flown shot (`fly_tree` entry) from `start` real
        seconds: the segment to the next frame sampled every `CONTACT_STEP_M`, as a skill's is, at
        the heights the idle hurtbox has a contact disc. With a `launch` (`er-mechanics-ashes`
        `bullet_launch`) whose peak is known, each point also carries (peak, `start`), so the
        defender's body holds it back as it held the caster (`contact_geometry`)."""
        A, _ = self.ash()
        reach_mod = A._pose()[1]
        if self._profile is None:
            self._profile = reach_mod.defender_profile('idle')
        held = (launch['peak'], start) if launch is not None and math.isfinite(launch['peak']) else ()
        pts = []
        for k, q in enumerate(path):
            nxt = path[k + 1] if k + 1 < len(path) else q
            steps = max(1, math.ceil(math.dist(q, nxt) / A.CONTACT_STEP_M))
            for j in range(steps if k + 1 < len(path) else 1):
                u = j / steps
                p3 = [q[i] + (nxt[i] - q[i]) * u for i in range(3)]
                d = reach_mod.contact_disc_radius(p3[1], node['radius_m'], self._profile)
                if d is not None:
                    pts.append((start + t0 + (k + u) / A.BULLET_FPS, p3[0], p3[2], d) + held)
        return pts

    def _point_arrays(self, node, path, t0, fire):
        """`_path_points` as arrays (T real frames, X, Z, disc), the shot fired at real frame `fire`."""
        import numpy as np
        A, _ = self.ash()
        reach_mod = A._pose()[1]
        if self._profile is None:
            self._profile = reach_mod.defender_profile('idle')
        P = np.asarray(path, float)
        if len(P) > 1:
            seg = P[1:] - P[:-1]
            steps = np.maximum(1, np.ceil(np.linalg.norm(seg, axis=1) / A.CONTACT_STEP_M)).astype(int)
            ks = np.repeat(np.arange(len(seg)), steps)
            js = np.arange(len(ks)) - np.repeat(np.cumsum(steps) - steps, steps)
            u = js / steps[ks]
            pts = np.vstack([P[ks] + seg[ks] * u[:, None], P[-1:]])
            when = np.concatenate([ks + u, [len(P) - 1]])
        else:
            pts, when = P, np.zeros(len(P))
        heights, inv = np.unique(pts[:, 1], return_inverse=True)
        discs = [reach_mod.contact_disc_radius(float(y), node['radius_m'], self._profile) for y in heights]
        D = np.array([math.nan if d is None else d for d in discs], float)[inv]
        keep = ~np.isnan(D)
        T = fire + (t0 + when[keep] / A.BULLET_FPS) * FPS
        return T, pts[keep, 0], pts[keep, 2], D[keep]

    def _air(self, node):
        return bool((self.atk.get(node['atk_row']) or {}).get('isInvalidatedByNoDamageInAir'))

    def flight(self, bt, start, damaging=None, target=None, launch=None, homing=True, aim=None):
        """`contact_geometry` of one bullet tree (`er-mechanics-ashes.bullet_tree`) flown ahead from
        `start` real seconds (`er-mechanics-ashes.fly_shots`, the record counts' flight), marked
        `air` when every damaging AtkParam in it has `isInvalidatedByNoDamageInAir`; None when no
        damaging part of it reaches a defender. `damaging(node)` says which nodes hit; default
        `spell_node_damaging`. With `target` (x, z) a defender stands there: each shot is aimed at
        his lock-on point when created, a homing one turns toward it in flight and a carrier dies on
        him (`er-mechanics-ashes.carrier`); `launch` is where the root shot leaves
        (`er-mechanics-ashes.bullet_launch`; None: the old fixed placement). When a damaging shot
        homes or is created by one that does, or by a carrier, the geometry carries its `HOMING` record
        (`er-mechanics-ashes.homing_spec`), so a dodge is read against the tree flown again after
        him (`er-mechanics-ashes._homing_touch`); not with `homing` False (a free-aimed cast has
        no lock-on target, `er-mechanics-ashes.floor_launch`). `aim` says whether that second
        flight aims each shot at where he is when it is created, the lock-on aim (default: when
        `target` is given); `geometry` passes True, a spell being cast locked on to him (`INFERRED`,
        as `CastThreats`), so its untargeted stored samples are the one place the aim is left out.
        `skill_attack` passes True as well: the flight again homes a skill's shots after him and
        kills its carriers on him, which needs him in their target slot (FUN_140398270 returns
        without a target), and a shot with a target is aimed at creation unless its spawn's +0x190
        int is 1 (FUN_140390c20; a child's is written 0, FUN_14039bba0; a root's is its event's
        orientation byte, `bullet_launch` 'aim'). Left unaimed, the Glintblade Phalanx ash (bullets
        2300/2301) landed twice the spell's run-in share (10430000/10430001, the same flight fields,
        `MEASURED` 2026-11-13)."""
        A, t = self.ash()
        test = self.spell_node_damaging if damaging is None else damaging
        pts, air = [], True
        for node, path, t0, _ in A.fly_tree(t, bt, target, launch):
            if not test(node):
                continue
            air = air and self._air(node)
            pts += self._path_points(node, path, t0, start, launch)
        g = A.contact_geometry(bullet_points=sorted(pts), air=air)
        if g is not None and homing:
            # Flown again, an aimed shot is aimed at where he is when it is created (a roll he began
            # before it exists moves its aim) and then turns after him; an unaimed one keeps its
            # launch heading and only turns.
            spec = A.homing_spec(t, bt, start, launch, test, aim=target is not None if aim is None else aim)
            if spec is not None:
                g['HOMING'] = spec
        return g

    def cast_threats(self, mid, events, hand, worth, damaging=None):
        """`CastThreats` of one cast variant: `events` [(frame, ref)], `hand` its hand-off frame,
        `worth` {(frame, ref): damage of that event's hit lists}; None when `geometry` gives the
        variant no flight (a direct AtkParam ref, an unreadable tree, nothing at hurtbox height)."""
        if self.geometry(mid, {'events': events, 'hand': hand}, damaging) is None:
            return None
        return CastThreats(self, mid, events, hand, worth, damaging)

    def cast_inputs(self, mid, cat, stats, talismans=(), left=None, defender=None, two_handed=False, pvp=True,
                    releases=True):
        """[{'mid', 'variant', 'anim', 'hand', 'roll', 'geo', 'value', 'attack', 'spec', 'hits',
        'gone', 'lead', 'weight', 'members'}]: what `punish_values` scores of each variant of spell
        `mid` that has a flight. `attack` is what `net_parts` takes: the straight `geo` where the aim
        and the per-threat split leave the cast as it was ('static'), else its `CastThreats`; `spec`
        is `geometry`'s variant, which the free aims fly again.

        A variant let go from 045010 (a tap, an early release) is the attacker's choice of release
        frame (`releases`, `er-mechanics-ashes.release_frames`): the entry is its earliest release,
        'members' the same variant let go at each later one (entries of their own, 'members' empty),
        and 'weight' each one's share of the window (the earliest's own and its members' sum to 1).
        A later release with no flight is left out and the others' weights scaled back to 1. With
        `releases` False, or a variant with no window, the entry is the earliest alone, weight 1."""
        out = []
        for v in self.evaluate(mid, cat, stats, talismans, left, defender, two_handed, pvp):
            entry = self._cast_input(mid, cat, v)
            if entry is None:
                continue
            rel = v['releases'] if releases else [(v['lead'], 1.0)]
            entry['weight'] = rel[0][1]
            for lead, w in rel[1:]:
                vv = next((x for x in self.evaluate(mid, cat, stats, talismans, left, defender, two_handed, pvp,
                                                    lead=lead) if x['variant'] == v['variant']), None)
                got = self._cast_input(mid, cat, vv) if vv is not None else None
                if got is not None:
                    entry['members'].append({**got, 'weight': w})
            total = entry['weight'] + sum(x['weight'] for x in entry['members'])
            for x in [entry] + entry['members']:
                x['weight'] /= total
            out.append(entry)
        return out

    def _cast_input(self, mid, cat, v):
        """One `cast_inputs` entry of `evaluate` variant `v` (its own lead), None without a flight."""
        events = sorted({(h['frame'], h['ref']) for h in v['hits']})
        hand = v['startup'] - min(f for f, _ in events)
        missing = []
        spec = {'events': events, 'hand': hand, 'anim': v['anim'], 'weapon': cat['base'], 'lead': v['lead']}
        geo = self.geometry(mid, spec, missing=missing)
        if geo is None:
            return None
        # An event with no flight (a direct AtkParam hit: Bayle's Flame Lightning's f69 stomp)
        # is left out of the worth, as attack-similarity leaves it out.
        gone = {(f, n) for f, n, _ in missing}
        hits = [h for h in v['hits'] if (h['frame'], h['ref']) not in gone]
        events = [e for e in events if e not in gone]
        value, worth = {}, {}
        for h in hits:
            value[h['frame']] = value.get(h['frame'], 0.0) + h['damage'] * h['count']
            worth[(h['frame'], h['ref'])] = worth.get((h['frame'], h['ref']), 0.0) + h['damage'] * h['count']
        value = dict(sorted(value.items()))
        self.attach_caught(mid, geo, hits, value)
        launches = {(f, n): self.cast_launch(mid, v['anim'], f, n, hand, cat['base'], v['lead']) for f, n in events}
        records = {(f, n): self.cast_record(mid, v['anim'], f, n, v['lead']) for f, n in events} \
            if v.get('anim') is not None else {}
        ct = CastThreats(self, mid, events, hand, worth, launches=launches, hits=hits, records=records,
                         windows=next((g['TOUGHNESS'] for g, _ in geo if g.get('TOUGHNESS')), ()))
        return {'mid': mid, 'variant': v['variant'], 'anim': v['anim'], 'hand': hand, 'roll': v['roll'],
                'geo': geo, 'value': value, 'attack': geo if ct.mode == 'static' else ct,
                'spec': {**spec, 'events': events}, 'hits': hits, 'gone': gone, 'lead': v['lead'], 'weight': 1.0,
                'members': []}

    def lookalike_group(self, mid, own, cats, stats, talismans=(), left=None, defender=None, two_handed=False,
                        pvp=True):
        """({(spell, variant): `cast_inputs` entry}, `spell_lookalikes` of them): `mid`'s casts with
        catalyst `own`, and those of every other spell of its (`Magic.refType`, `castSfxId`) that a
        caster with `stats` casts with one of `cats`, usable in multiplayer and held by a player
        (`obtainable`)."""
        mg = self.magic[mid]
        key = (mg['refType'], mg['castSfxId'])
        casts = {}
        for x in sorted(self.names):
            m_ = self.magic[x]
            cat = own if x == mid else next((c for c in cats if self.can_cast(x, c)), None)
            if x != mid and ((m_['refType'], m_['castSfxId']) != key or not m_['enable_multi'] or cat is None
                             or self.short(x, stats) or not self.obtainable(x)):
                continue
            for c in self.cast_inputs(x, cat, stats, talismans, left, defender, two_handed, pvp):
                casts[(x, c['variant'])] = c
        held = {k: c for k, c in casts.items() if k[0] != mid or self.obtainable(mid)}
        looks = spell_lookalikes(self, self.ash()[0], held)
        return casts, looks

    def punish_values(self, mid, cat, stats, situations, talismans=(), left=None, defender=None,
                      two_handed=False, pvp=True, strikes=None, casts=None, looks=None, cats=None):
        """Per situation: the damage one cast lands on a defender caught in his recovery at the
        situation's distance, less the punish he lands when he rolls it and runs in before the
        caster's roll cancel (`strikes`, `punish_strikes`; `kit` in the module doc), the best of the
        spell's variants; with the variant each one came from. None when no variant has a flight.
        `casts` are `cast_inputs` already read; `looks` {(spell, variant) or, for a later release,
        (spell, variant, lead): `Lookalikes` member or None} the lookalikes a defender cannot tell
        each variant from (`spell_lookalikes`). With `cats` (the caster's catalysts) and no `looks`,
        the spell's group among what he casts with them is read here (`lookalike_group`); with
        neither, the defender knows the spell. A variant with release members (`cast_inputs`) nets
        their weighted mean per situation: the attacker draws the release frame, each member read
        by the defender's plan against the whole group (`INFERRED`, `release_frames`); a member's
        own nets are read where they matter and filled in between (`er-mechanics-ashes`
        `release_read`, `release_fill`)."""
        A = self.ash()[0]
        if cats is not None and looks is None:
            got, looks = self.lookalike_group(mid, cat, list(cats), stats, talismans, left, defender, two_handed, pvp)
            casts = [c for (x, _), c in got.items() if x == mid] if casts is None else casts
        casts = self.cast_inputs(mid, cat, stats, talismans, left, defender, two_handed, pvp) if casts is None else casts
        best, label = None, None
        for c in casts:
            units = [c] + list(c.get('members') or ())

            def read(i, units=units, c=c):
                x = units[i]
                look = (looks or {}).get(release_key((mid, c['variant']), x, x is c))
                return self._member_values(A, mid, x, situations, strikes, defender, look)
            if len(units) == 1:
                vals = read(0)
            else:
                # The releases' own nets read where they matter (`er-mechanics-ashes.release_read`,
                # on the plain mean over the situations), the others filled in between.
                got = A.release_read(len(units), read, lambda v: sum(v) / len(v))
                vals = None
                for x, (a, b, s) in zip(units, A.release_fill(got, len(units))):
                    w = float(x.get('weight', 1.0))
                    each = got[a] if a == b else [(1 - s) * p + s * q for p, q in zip(got[a], got[b])]
                    vals = [w * v for v in each] if vals is None else [p + w * v for p, v in zip(vals, each)]
            if best is None or sum(vals) > sum(best):
                best, label = vals, c['variant']
        return (best, label) if best is not None else None

    def _member_values(self, A, mid, c, situations, strikes, defender, look):
        """`net_values` of one `cast_inputs` entry (a variant, or one of its release members), and
        per situation the better of it and its free-aimed alternatives."""
        # A cast the aim and the per-threat split leave as it was keeps its one straight flight.
        vals = net_values(A, c['attack'], c['value'], situations, c['roll'], strikes, defender, look=look)
        # Free-aimed at the ground instead, where a carrier's ground contact is the damage
        # (`er-mechanics-ashes.floor_launch`): per situation the better aim.
        if self.floor_aims(mid, c['spec']):
            for g_m in A.FLOOR_AIM_M:
                fgeo = [(g, f) for g, f in self.geometry(mid, c['spec'], missing=[], floor=g_m) or ()
                        if g.get('EVENT') not in c['gone']]
                if fgeo:
                    self.attach_caught(mid, fgeo, c['hits'], c['value'])
                    vals = [max(a, b) for a, b in zip(vals, net_values(A, fgeo, c['value'], situations,
                                                                        c['roll'], strikes, defender, look=look))]
        return vals

    def skill_events(self, weapon, stats, affinity='Standard', level=None, skill=None, defender=None,
                     two_handed=False):
        """One weapon skill in a cast's terms (`kit` in the module doc): ([(geometry, real frame from
        the press)], {frame: damage}, roll frame, FP, name), or None when no hit of it has a reach
        model. The hits, their damage, the roll and the FP are the ash model's (`skill_option`, the
        weapon as built at its top level by default; the skill is the weapon's own unless `skill`
        names a SwordArtsParam id). A melee hit flies its posed window (`_hit_contacts`); a bullet
        flies its whole tree from the hit's frame as a cast's does (`flight`), worth its hit lists
        by the `full` rule (`bullet_hits`), so a skill's storm counts its ticks as a spell's does.
        A hit with neither is left out. `skill_attack` with its defaults, in a tuple."""
        d = self.skill_attack(weapon, stats, affinity, level, skill, defender, two_handed)
        return None if d['why'] else (d['geo'], d['value'], d['roll'], d['fp'], d['name'])

    def skill_attack(self, weapon, stats, affinity='Standard', level=None, skill=None, defender=None,
                     two_handed=False, follow=None, stance=None, priced_bullets=False, flown_only=False):
        """`skill_events` as a dict, with what it leaves out: `geo`, `value` ({frame: damage}),
        `value_one` ({frame: the largest single hit opening then}), `kinds` ({frame: ['bullet'] /
        ['melee'] / both}: how the worth at that frame is delivered), `roll`, `fp`, `name`,
        `variant`, `option` (the `skill_option`), `skipped` ([(atk row, kind, reason)] for each hit
        with no reach model: 'melee hit with no posed window', 'bullet flight None' or, with
        `flown_only`, `MISS_UNFLOWN`) with `skipped_hp` their worth, `dropped` ([(atk row, bullet,
        launch condition, worth)]: with `flown_only`, the hit lists on a bullet that never launches
        on dry ground, `NEVER_ON_LAND`), `opening_dropped` (the opening's hits a follow-up leaves
        out, below), `part_duplicates` (hits kept in two parts at the same real frame and AtkParam:
        0 unless a clip is counted twice) and `why` (None, or 'no damaging hit' / 'no roll frame' /
        'no hit with a reach model', when `skill_events` returns None). `follow` (a
        `skill_followups` entry) and `stance` ('r1' / 'r2') go to `skill_option`. With
        `priced_bullets` a bullet's hit lists are found and priced by the option's own hits (an
        AtkParam hits when its `pvp_damage` is above 0), not by the spell rule's flat fields, which
        a weapon-scaled bullet AtkParam can leave at 0.

        With `follow`, the opening's hits that start at or after the press are left out: the press
        cancels the opening. `skill_option` keeps them, which counted a clip twice where
        `skill_hits` scores an opening with no hit of its own (Stamp's 040000) on its first
        FP-charging clip, the follow-up itself (Stamp (Upward Cut): atk 300000290 at f37 twice;
        Stamp (Sweep) on the Large Club: 040010's hits under 040210's, twice the worth, `MEASURED`
        round 3)."""
        A, t = self.ash()
        wid = t.find_weapon(weapon)
        sid = t.find_arts(skill) if isinstance(skill, str) else skill
        sid = sid if sid is not None else t.reg.weapon[wid]['swordArtsParamId']
        ctx = A.WeaponContext(weapon, affinity, level, stats, two_handed, ar_tables=self.ar)
        o = A.skill_option(t, wid, sid, ctx, ctx.level, defender, DEF, follow=follow, stance=stance)
        out = {'geo': [], 'value': {}, 'value_one': {}, 'kinds': {}, 'roll': (o or {}).get('roll'),
               'fp': (o or {}).get('fp'), 'name': t.arts_name(sid), 'variant': (o or {}).get('variant'),
               'option': o, 'skipped': [], 'skipped_hp': 0.0, 'dropped': [], 'opening_dropped': 0, 'why': None}
        if not o or not o['hit_rows']:
            out['why'] = 'no damaging hit'
            return out
        if o['roll'] is None:
            out['why'] = 'no roll frame'
            return out
        out['name'] = o['name']
        offset = {id(h): p['offset'] for p in o['parts'] for h in p['hits']}
        pairs = list(zip(o['hit_rows'], A._hit_contacts(t, wid, sid, o['hit_rows'])))
        if follow is not None and len(o['parts']) > 1:
            # The press cancels the opening: its hits that start at or after the press frame never
            # come out.
            press = o['parts'][1]['offset']
            first = {id(h) for h in o['parts'][0]['hits']}
            kept = [(h, c) for h, c in pairs if id(h) not in first or c[0] * FPS + offset[id(h)] < press - 1e-6]
            out['opening_dropped'] = len(pairs) - len(kept)
            pairs = kept
        part_of = {id(h): i for i, p in enumerate(o['parts']) for h in p['hits']}
        at = {}
        for h, c in pairs:
            at.setdefault((round(c[0] * FPS + offset.get(id(h), 0.0), 1), h['atk_row']), set()).add(part_of.get(id(h)))
        out['part_duplicates'] = sum(len(v) > 1 for v in at.values())
        dmg = {h['atk_row']: h['pvp_damage'] for h in o['hit_rows']}
        priced = dict(damaging=lambda a: dmg.get(a, 0.0) > 0, base=lambda a: dmg.get(a, 0.0)) \
            if priced_bullets else {}

        def poise_of(h):
            fr = t.final_rate.get(h['final_rate_id']) if h.get('final_rate_id', -1) >= 0 else None
            return (h.get('poise') or 0.0) * (fr['saRate'] if fr else 1.0)
        poise = {}
        for h in o['hit_rows']:
            poise.setdefault(h['atk_row'], poise_of(h))
        geo, value, one, kinds, seen = out['geo'], out['value'], out['value_one'], {}, set()
        for h, (start, _, source, g) in pairs:
            off = offset.get(id(h), 0.0)
            if h['kind'] == 'bullet' and h.get('root_bullet'):
                key = (h.get('anim'), h['frame'] + off, h['root_bullet'])
                if key in seen:
                    continue
                seen.add(key)
                bt = A.bullet_tree(t, h['root_bullet'])
                # The root shot leaves the event's DummyPoly where the clip has carried the caster
                # (`er-mechanics-ashes.bullet_launch`).
                launch = A.skill_bullet_launch(t, wid, sid, h['anim'], h['frame'], h['root_bullet'])
                # A skill's bullet is judged by the ash model's test, not the spell's: its spawn
                # names no magic or goods id, so `isGoodsOrMagicBullet` stays clear and the hit
                # takes the weapon branch (`INFERRED` from FUN_14038e540's test, module doc).
                # Flown again after a dodge, its shots are aimed at him when created, as a spell's
                # are (`flight` `aim`): the same target slot homes them and kills its carriers on him.
                geom = self.flight(bt, start + off / FPS, A._bullet_damages_self, launch=launch,
                                   aim=True) if bt else None
                lists = self.bullet_lists(h['root_bullet'], **priced)
                if flown_only and bt:
                    gaps, kept = self.launch_gaps(bt, launch), []
                    for li in lists:
                        a, b, c = li['atk'], li['bullet'], li['mult'] * li['ticks']
                        hp = c * dmg.get(a, 0.0)
                        if b not in gaps or hp <= 0:
                            kept.append(li)
                        elif gaps[b] in NEVER_ON_LAND:
                            out['dropped'].append((a, b, gaps[b], hp))
                        else:
                            out['skipped'].append((a, 'bullet', f'{MISS_UNFLOWN} ({launch_text([gaps[b]])})'))
                            out['skipped_hp'] += hp
                    lists = kept
                worth = sum(li['mult'] * li['ticks'] * dmg.get(li['atk'], 0.0) for li in lists)
                top = max((dmg.get(li['atk'], 0.0) for li in lists), default=0.0)
                if geom is None and worth > 0:
                    out['skipped'].append((h['atk_row'], 'bullet', 'bullet flight None'))
                    out['skipped_hp'] += worth
                if geom is not None and worth > 0:
                    caught = [(li, dmg.get(li['atk'], 0.0)) for li in lists if dmg.get(li['atk'], 0.0) > 0]
                    if h.get('record') is not None:
                        # The launch's copies and children all carry its id, so the record holds
                        # back none of its lists (`HIT_RECORD` in er-mechanics-attacks).
                        geom['HPR'] = (('skill', part_of.get(id(h)), h['record']), h['priority'], h['created'])
                    geom['CAUGHT'] = {'lists': [A.caught_list(t, li['atk'], poise.get(li['atk'], 0.0), li['ticks'],
                                                              li['rec'], li['mult'] * d / worth) for li, d in caught],
                                      'wf': worth}
            elif source == 'pose':
                geom = {**g, 'T': g['T'] + off}
                if (self.atk.get(h['atk_row']) or {}).get('isInvalidatedByNoDamageInAir'):
                    geom['AIR'] = True
                worth = h['pvp_damage_total']
                top = h['pvp_damage']
                geom['CAUGHT'] = {'lists': [A.caught_list(t, h['atk_row'], poise_of(h))], 'wf': worth}
                if h.get('record') is not None:
                    geom['HPR'] = (('skill', part_of.get(id(h)), h['record']), h['priority'], h['created'])
            else:
                out['skipped'].append((h['atk_row'], h['kind'], 'melee hit with no posed window'
                                       if h['kind'] == 'melee' else 'bullet flight None'))
                out['skipped_hp'] += h.get('pvp_damage_total') or 0.0
                continue
            if geom is None or worth <= 0:
                continue
            frame = float(geom['T'].min())
            geo.append((geom, frame))
            value[frame] = value.get(frame, 0.0) + worth
            one[frame] = max(one.get(frame, 0.0), top)
            kinds.setdefault(frame, set()).add('bullet' if h['kind'] == 'bullet' else 'melee')
        # `wf` held each geometry's worth: now its share of its frame's (`CaughtModel.weights`).
        for g, frame in geo:
            if 'CAUGHT' in g:
                g['CAUGHT']['wf'] = g['CAUGHT']['wf'] / value[frame] if value[frame] > 0 else 0.0
        # The skill's TAE 795 windows, what the defender's strike reads (`strike_breaks`).
        wins = self.skill_windows(o, wid, sid) if geo else ()
        for g, _ in geo if wins else ():
            g['TOUGHNESS'] = wins
        # The skill's own guard (TAE JumpTable 3), which the defender's strike meets (`strike_guard`).
        guard = self.skill_guard(o, wid, sid, ctx.level, two_handed) if geo else None
        for g, _ in geo if guard else ():
            g['GUARD'] = tuple(guard)
        out['kinds'] = {f: sorted(k) for f, k in kinds.items()}
        out['skipped_hp'] = round(out['skipped_hp'], 1)
        if not geo:
            out['why'] = 'no hit with a reach model'
        return out

    def skill_windows(self, option, wid, sid):
        """A skill option's TAE 795 windows in real frames from the press, as `cast_windows` gives a
        cast's: per part of `option` (`er-mechanics-ashes.skill_option` 'parts'), the windows of each
        clip its hits are on, from the part's offset (where `skill_attack` starts that clip), the
        weapon's `toughnessCorrectRate` in the bonus (`er-mechanics-interrupt._window`, as
        `er-mechanics-ashes.anim_actions` reads them). A clip that carries no hit (a stance's entry)
        adds none (`INFERRED`: the strike that interrupts lands in the windup of a hit)."""
        A, t = self.ash()
        INT = _interrupt()
        tae = A.skill_tae(t, sid) or {}
        wep = t.reg.weapon.get(wid) or {'toughnessCorrectRate': 0.0}
        out = []
        for p in (option or {}).get('parts') or ():
            off = float(p.get('offset') or 0.0)
            for anim in sorted({h.get('anim') for h in p['hits'] if h.get('anim') is not None}):
                ev = tae.get(anim) or []
                real = ATK.clip_to_real(ev) if ev else None
                for e in ev:
                    if e.type != ATK.TAE_TOUGHNESS:
                        continue
                    ratio = struct.unpack_from('<f', bytes(e.params), 4)[0]
                    out.append(tuple(float(x) for x in INT._window(
                        t.reg, wep, e.params[0], e.params[1], ratio,
                        (off + ATK.real_frame(real(e.start)), off + ATK.real_frame(real(e.end))))))
        return tuple(sorted(out))

    def skill_guard(self, option, wid, sid, level, two_handed=False):
        """The skill's own guard (`Guard`), or None: per part of `option`, the TAE JumpTable 3
        windows (`tae_guard`) of each clip its hits are on, from the part's offset, as
        `skill_windows` reads the TAE 795 windows. The guarding weapon is the carrier (the hand the
        skill is on: `ExecAttack`'s `SWORDARTS_REQUEST_LEFT_NORMAL` sets the guard hand to the
        skill's hand, `VERIFIED` c0000.hks), at the skill's level and grip. A skill state goes
        through `ArtsCommonFunction`, so a guarded hit ends it (`Guard`, `continues` False). Its
        stamina is every melee hit's `stamina_cost` (`cost`), each taken on the real frame its
        hitbox is created (`charges`: `FUN_1404428f0` charges a hit when its AttackBehavior first
        fires, attacks.md section 3, `VERIFIED`), so a strike before a hit meets that hit's stamina
        not yet spent. Until 2026-11-13 all of it was taken before the guard opened (`INFERRED`,
        not what the game does)."""
        A, t = self.ash()
        tae = A.skill_tae(t, sid) or {}
        wins, judge, cost, first, charges = [], None, 0, None, []
        for p in (option or {}).get('parts') or ():
            off = float(p.get('offset') or 0.0)
            for anim in sorted({h.get('anim') for h in p['hits'] if h.get('anim') is not None}):
                ev = tae.get(anim) or []
                w, j = tae_guard(ev, off)
                wins += w
                judge = j if judge is None else judge
                if w and (first is None or w[0][0] < first[0]):
                    first = (w[0][0], off, ev)
                real = ATK.clip_to_real(ev) if ev else (lambda x: x)
                charges += [(off + float(ATK.real_frame(real(h['frame'] / FPS))), int(h['stamina_cost']))
                            for h in p['hits'] if h.get('anim') == anim and h.get('kind') == 'melee'
                            and h.get('stamina_cost')]
            cost += sum(int(h.get('stamina_cost') or 0) for h in p['hits'] if h.get('kind') == 'melee')
        if not wins:
            return None
        # Regeneration before the first window, in the clip that holds it. The parts before that
        # clip's offset add none: the game regenerates at each frame's TAE 225 percent (100 where no
        # event runs, `TAE_SP_REGEN`, `VERIFIED`), and every skill clip that holds a guard window
        # (195 clips in 16 of the skill TimeActs, all of them scanned) holds 225 at 0% from frame 0 past its
        # first window, as the opening a follow-up is pressed from does (Moore's Charge 040000, 0%
        # to f104) (`MEASURED` 2026-11-13 with `tae_guard` and `tae_regen_frames` over
        # `er-mechanics-ashes.skill_tae`).
        regen = tae_regen_frames(first[2], first[0] - first[1]) if first else 0.0
        return Guard(tuple(sorted(wins)), wid, level, judge, bool(two_handed), False, cost, regen,
                     tuple(sorted(charges)))

    def guard_poke(self, weapon):
        """(`clip` for `slot_attack`, two-handed) of the attack from a raised guard `weapon` has
        (`GUARD_POKES`): a thrusting shield's two-handed one, a spear's, rapier's, great spear's or
        heavy thrusting sword's one-handed one (guarding with a left-hand shield); None for any
        other weapon."""
        _, t = self.ash()
        w = t.reg.weapon[t.find_weapon(weapon)]
        cat = w['wepmotionCategory']
        if cat == THRUSTING_SHIELD_CATEGORY:
            label, anim, two = GUARD_POKES['both_guard']
        elif cat in GUARD_POKE_CATEGORIES:
            label, anim, two = GUARD_POKES['right_guard']
        else:
            return None
        return (ATK.motion_category(w, anim, right_hand_fallback=False), anim, label, True), two

    def slot_attack(self, weapon, stats, affinity='Standard', level=None, slot='r1_1', two_handed=False,
                    defender=None, clip=None, guard_weapon=None, guard_level=None):
        """A weapon's own attack in the shape `skill_attack` gives a skill (`geo`, `value`,
        `value_one`, `kinds`, `roll`, `name`, `guard`, `why`), so `net_parts` ranks it beside the
        skills and spells: an attack slot of `ATK.SLOTS_ONE_HAND` (`slot`; two-handed with
        `two_handed`), or a clip no slot names, `clip` = (TAE category, anim id, label, continues):
        the guard pokes (`GUARD_POKES`). Its melee hits are read as a skill's are
        (`er-mechanics-ashes.anim_actions` on the clip's TAE, `hit_attack` at `stats`,
        `pvp_damage` against `defender`), each flown on the reach model's posed window
        (`er-mechanics-reach.skill_reach` on the clip, `contact_geometry`); its roll frame is the
        clip's dodge cancel after its last hit (`ATK.recovery_windows`, as `anim_recovery` reads a
        skill's). Its guard (`Guard`) is the clip's TAE JumpTable 3 windows held by `guard_weapon`
        at `guard_level` (default: the attacking weapon, at its level: a thrusting shield guards with
        itself), going on through a guarded hit as `clip`'s `continues` says (a slot: False, the
        R1 state's guard reaction ends it). No bullet, no follow-up, no chain: one press of one clip."""
        A, t = self.ash()
        wid = t.find_weapon(weapon)
        w = t.reg.weapon[wid]
        ctx = A.WeaponContext(weapon, affinity, level, stats, two_handed, ar_tables=self.ar)
        out = {'geo': [], 'value': {}, 'value_one': {}, 'kinds': {}, 'roll': None, 'name': None, 'guard': None,
               'why': None, 'weapon': wid}
        if clip is None:
            key, label, _, anim, _ = next(s for s in ATK.SLOTS_ONE_HAND if s[0] == slot)
            if two_handed:
                anim, label = anim + ATK.TWO_HAND_ANIM_OFFSET, '2H ' + label
            anim, cat, _ = ATK.slot_animation(w, key, anim, 'both' if two_handed else 'one')
            continues = False
        else:
            cat, anim, label, continues = clip
        out['name'] = f"{t.reg.weapon_names.get(wid) or weapon} {label}"
        _, _, events = ATK.resolve_events(cat, anim)
        if not events:
            out['why'] = 'no TAE entry'
            return out
        to_real = ATK.clip_to_real(events)
        hits = []
        for x in A.anim_actions(t, wid, events, ctx.level):
            if x.get('event') != 'attack' or x.get('kind') != 'melee':
                continue
            attack = A.hit_attack(ctx, x, x.get('from_weapon', True))
            if sum(attack.values()) <= 0:
                continue
            fr = t.final_rate.get(x.get('final_rate_id', -1)) if x.get('final_rate_id', -1) >= 0 else None
            hits.append({'kind': 'melee', 'frame': x['frames'][0], 'atk_row': x['atk_row'], 'judge': x['judge'],
                         'attack': attack, 'final_rate_id': x.get('final_rate_id', -1), 'count': 1,
                         # Menu poise with the hit's saRate, as `skill_attack`'s `poise_of` reads it.
                         'poise': round((x['poise'] or 0.0) * A.POISE_MENU, 2) * (fr['saRate'] if fr else 1.0),
                         'stamina_cost': x['stamina_cost'], 'record': x.get('record'),
                         'priority': x.get('priority')})
        if not hits:
            out['why'] = 'no damaging hit'
            return out
        A.pvp_damage(t, wid, hits, defender if defender is not None else self.defender(150), DEF)
        reach = A._pose()[1]
        geo, value, one = out['geo'], out['value'], out['value_one']
        for h in hits:
            row = reach.skill_reach(wid, cat, anim, h['judge'])
            entries = ((row or {}).get('window_contacts') or {}).get((h['frame'], h['judge']))
            if not entries or h['pvp_damage_total'] <= 0:
                continue
            geom = A.contact_geometry(entries=entries)
            geom['CAUGHT'] = {'lists': [A.caught_list(t, h['atk_row'], h['poise'])], 'wf': h['pvp_damage_total']}
            if h.get('record') is not None:
                geom['HPR'] = (('slot', 0, (anim, h['record'])), h['priority'], hits.index(h))
            frame = float(geom['T'].min())
            geo.append((geom, frame))
            value[frame] = value.get(frame, 0.0) + h['pvp_damage_total']
            one[frame] = max(one.get(frame, 0.0), h['pvp_damage'])
            out['kinds'].setdefault(frame, ['melee'])
        for g, frame in geo:
            g['CAUGHT']['wf'] = g['CAUGHT']['wf'] / value[frame] if value[frame] > 0 else 0.0
        if not geo:
            out['why'] = 'no hit with a reach model'
            return out
        # The roll frame: the first roll the clip opens after its last hit (`anim_recovery`'s rule).
        after = max(h['frame'] for h in hits) / FPS
        win = ATK.recovery_windows(events, w['weaponWeightRate'])
        _, _, inputs, _ = next(r for r in ATK.RECOVERY_ACTIONS if r[0] == 'dodge')
        pairs = [(max(i[0], c[0]), min(i[1], c[1])) for i in win['dodge']['input'] for c in win['dodge']['cancel']]
        starts = [max(s, after) for s, e in pairs if max(s, after) < e]
        out['roll'] = ATK.real_frame(to_real(min(starts))) if starts else None
        if out['roll'] is None:
            out['why'] = 'no roll frame'
        wins = []
        for e in events:
            if e.type == ATK.TAE_TOUGHNESS:
                INT = _interrupt()
                ratio = struct.unpack_from('<f', bytes(e.params), 4)[0]
                wins.append(tuple(float(x) for x in INT._window(
                    t.reg, w, e.params[0], e.params[1], ratio,
                    (ATK.real_frame(to_real(e.start)), ATK.real_frame(to_real(e.end))))))
        gw, judge = tae_guard(events, 0.0, to_real)
        guard = None
        if gw:
            gwid = t.find_weapon(guard_weapon) if guard_weapon is not None else wid
            glevel = guard_level if guard_level is not None else (
                ctx.level if gwid == wid else self.ar.max_level(t.reg.weapon[gwid]['reinforceTypeId']))
            # Each hit's stamina on the frame its hitbox is created (`Guard`, `charges`).
            guard = Guard(gw, gwid, glevel, judge, bool(two_handed) and gwid == wid, bool(continues),
                          sum(int(h['stamina_cost'] or 0) for h in hits),
                          tae_regen_frames(events, gw[0][0], to_real),
                          tuple(sorted((float(ATK.real_frame(to_real(h['frame'] / FPS))), int(h['stamina_cost']))
                                       for h in hits if h['stamina_cost'])))
        out['guard'] = guard
        for g, _ in geo:
            if wins:
                g['TOUGHNESS'] = tuple(sorted(wins))
            if guard is not None:
                g['GUARD'] = tuple(guard)
        return out

    def spell_like_skills(self, stats):
        """`skill_events` keywords for every weapon skill a build with `stats` can wield that fires
        a damaging bullet: the skills a spell competes with for the same job (Zamor Ice Storm's
        storm, Gravitas's pull, Glintblade Phalanx). A unique skill rides its own weapon; an ash of
        war rides the weapon of least total requirement that mounts it (`INFERRED`: the class
        changes the animation, not the bullet), at the mountable affinity whose scaling gives the
        bullet most (any, when every damaging AtkParam swaps in its own scaling table); all at the
        top level. A weapon whose requirements the stats miss carries nothing; an `[NPC]` weapon, an
        ash no inventory row carries (every gem row `sortId` 999999) and one in
        `data/ash-blacklist.toml` are left out, as the ash table leaves them out."""
        A, t = self.ash()
        black = set()
        if os.path.exists(ASH_BLACKLIST):
            with open(ASH_BLACKLIST, encoding='utf-8') as f:
                black = set(tomllib.loads(f.read()))
        held = {g['swordArtsParamId'] for g in t.gem.values() if g['sortId'] != UNOBTAINABLE_SORT_ID}
        gems = {s: g for s, g in t.ash_gems().items() if s in held and t.arts_name(s) not in black}
        need = {'str': 'properStrength', 'dex': 'properAgility', 'int': 'properMagic', 'fth': 'properFaith',
                'arc': 'properLuck'}
        wield = sorted((sum(w[f] for f in need.values()), wid) for wid, w in self.ar.weapons.items()
                       if wid % 10000 == 0 and wid in t.reg.weapon and self.ar.names.get(wid)
                       and not self.ar.names[wid].startswith('[')
                       and all(stats.get(k, 0) >= w[f] for k, f in need.items()))
        pairs = {}
        for _, wid in wield:
            own = t.reg.weapon[wid]['swordArtsParamId']
            if own in t.arts and own not in t.ash_gems() and t.arts_name(own) not in black:
                pairs.setdefault(own, (wid, None))
        for sid, gem in gems.items():
            if sid in pairs or sid not in t.arts:
                continue
            for _, wid in wield:
                top = self.ar.max_level(self.ar.weapons[wid]['reinforceTypeId'])
                ok = [i for i in range(len(AR.AFFINITIES)) if t.can_mount(wid, gem, i, top)[0]]
                if ok:
                    pairs[sid] = (wid, ok)
                    break
        out = []
        for sid, (wid, affs) in sorted(pairs.items()):
            try:
                prof = A.skill_profile(t, sid, wid)
            except (KeyError, TypeError, ValueError, StopIteration, SystemExit):
                continue
            rows = {node['atk_row'] for x in (e for es in prof['anims'].values() for e in es)
                    if x.get('kind') == 'bullet' and x.get('bullet')
                    for node, _, _, _ in A.fly_tree(t, x['bullet']) if A._bullet_damages_self(node)}
            if not rows:
                continue
            name = self.ar.names[wid]
            aff = 'Standard'
            if affs:
                def gives(i):
                    try:
                        cat = {'wep': self.ar.weapons[self.ar.find_weapon(name, AR.AFFINITIES[i])], 'reinf': None}
                    except SystemExit:
                        return -1.0
                    cat['reinf'] = self.ar.reinforce[cat['wep']['reinforceTypeId'] + self.ar.max_level(
                        cat['wep']['reinforceTypeId'])]
                    return sum(self.atk[r][f] * self.multiplier(cat, e, stats, self.atk[r])
                               for r in rows if r in self.atk for e, f in ATK_FIELD.items() if self.atk[r][f] > 0)
                aff = AR.AFFINITIES[max(affs, key=gives)]
            out.append({'weapon': name, 'affinity': aff, 'level': None, 'skill': sid})
        return out

    def rival_values(self, weapons, stats, situations, defender=None, strikes=None):
        """Per situation, the best punish among the skills of `weapons` (dicts of `skill_events`
        keywords: `weapon`, `affinity`, `level`, `skill`, `two_handed`), floored at nothing, the
        line a spell has to beat; with {label: its own mean over the situations' order} for the
        report."""
        best, each = [0.0] * len(situations), {}
        for w in weapons:
            try:
                d = self.skill_attack(stats=stats, defender=defender, **w)
                look = self.release_look(d, stats=stats, defender=defender, **w) if not d['why'] else None
            except (KeyError, TypeError, ValueError, StopIteration, SystemExit):
                # A catalyst or a weapon without a readable skill rivals nothing.
                d, look = None, None
            if d is None or d['why']:
                each[weapon_label(w)] = None
                continue
            # A charged skill's hold looks like its early release up to the release
            # (`release_look`).
            vals = net_values(self.ash()[0], d['geo'], d['value'], situations, d['roll'], strikes, defender, look=look)
            each[f"{weapon_label(w)} ({d['name']})"] = vals
            best = [max(b, x) for b, x in zip(best, vals)]
        return best, each

    def release_look(self, hold, weapon, stats, affinity='Standard', level=None, skill=None, defender=None,
                     two_handed=False, priced_bullets=False, flown_only=False):
        """The `Lookalikes` member 'hold' of a charged skill held to the end, `hold` its
        `skill_attack` (taken with the same keywords), against its early release let go on each
        release frame of its window (`skill_releases`, `release_frames`, `skill_lookalikes`): None
        when the skill has no early release."""
        A, t = self.ash()
        wid = t.find_weapon(weapon)
        sid = t.find_arts(skill) if isinstance(skill, str) else skill
        sid = sid if sid is not None else t.reg.weapon[wid]['swordArtsParamId']
        top = self.ar.max_level(self.ar.weapons[wid]['reinforceTypeId']) if level is None else level
        rel = skill_releases(A, t, sid, wid, top)
        if not rel:
            return None
        frames = A.release_frames(rel[0]['first'], rel[0]['last'])
        taps = [(self.skill_attack(weapon, stats, affinity, level, skill, defender, two_handed, {**rel[0], 'first': r},
                                   priced_bullets=priced_bullets, flown_only=flown_only), w) for r, w in frames]
        # A hold only reads the releases' first escaping starts, so every one is a member; each
        # parts from it where it shows (`er-mechanics-ashes.skill_release_visible`).
        shows = [A.skill_release_visible(t, sid, rel[0]['anim'], r) for r, _ in frames]
        group = skill_lookalikes(A, hold, taps[0][0], taps[1:], shows)
        return group.member('hold') if group is not None else None

    def kit(self, cats, stats, talismans=(), left=None, rl=150, two_handed=False, pvp=True, slots=None,
            weapons=(), ash_rivals=True):
        """The greedy attack-spell kit by punish coverage (module doc): rounds of (spell, its
        slots, kit value after it, gain), the spells left out with why, and the rivals: {label: mean
        worth} of the skills of `weapons` and, with `ash_rivals`, of every spell-like skill the
        stats can wield (`spell_like_skills`), which the kit starts from."""
        slots = MEMORY_SLOTS if slots is None else slots
        weapons = list(weapons) + (self.spell_like_skills(stats) if ash_rivals else [])
        sit = punish_situations(rl)['situations']
        pairs = [(s['distance'], s['recovery']) for s in sit]
        weight = [s['weight'] for s in sit]
        total = sum(weight)
        dfn = self.defender(rl)
        strikes = punish_strikes(rl)
        base, each = self.rival_values(weapons, stats, pairs, dfn, strikes)
        rivals = {k: (None if v is None else round(sum(w * max(x, 0.0) for w, x in zip(weight, v)) / total, 1))
                  for k, v in each.items()}
        if each:
            rivals['best of them'] = round(sum(w * b for w, b in zip(weight, base)) / total, 1)
        values, skipped = {}, {}
        rows, why = self.table(cats, stats, talismans, left, rl, two_handed, pvp)
        skipped.update(why)
        casts = {}
        for mid in sorted({r[0]['id'] for r in rows}):
            cat = next(c for c in cats if self.can_cast(mid, c))
            casts[mid] = self.cast_inputs(mid, cat, stats, talismans, left, dfn, two_handed, pvp)
        # A defender cannot tell apart, from their start, the casts of one TimeAct and casting SFX
        # among the spells this caster can cast and a player can hold (`spell_lookalikes`).
        looks = spell_lookalikes(self, self.ash()[0], {(mid, c['variant']): c for mid, cs in casts.items()
                                                       if self.obtainable(mid) for c in cs})
        for mid in sorted(casts):
            cat = next(c for c in cats if self.can_cast(mid, c))
            got = self.punish_values(mid, cat, stats, pairs, talismans, left, dfn, two_handed, pvp, strikes,
                                     casts=casts[mid], looks=looks)
            if got is None:
                skipped[self.names[mid]] = 'no bullet flight (reach not modelled)'
                continue
            values[mid] = got
        return greedy_kit({mid: v for mid, (v, _) in values.items()}, weight, total,
                          {mid: self.magic[mid]['slotLength'] for mid in values}, slots,
                          {mid: lab for mid, (_, lab) in values.items()}, self.names, base), skipped, rivals


#: `CastThreats.dynamic_outcome`: the first-roll starts flown, every this many real frames, and the
#: defender distance it rounds to (metres); both bound the cost of one flight per start. At 2 frames
#: Glintstone Nail(s) at INT 80 land what they land at 1 frame within 1% (3-8 m, `MEASURED`).
DYNAMIC_STEP = 2.0
DYNAMIC_DIST_M = 0.25
#: Rolls a defender makes against one re-aiming cast (`CastThreats._rolled`): three, the user's
#: in-game observation (2026-11-09: three rolls clear three different nails of one Glintstone Nails).
DYNAMIC_ROLLS = 3
#: Sampled shot paths `CastThreats._touches` keeps before it starts over (memory, not numbers).
PTS_CACHE = 4000
#: Defender distances at which `CastThreats` compares a cast's aimed flight with its straight one:
#: the closest run-in gap, the middle and the far end of the punish situations (2.6-11.9 m at RL 150).
AIM_PROBE_M = (1.5, 6.0, 12.0)


class CastThreats:
    """One cast variant's bullets as threats on a defender `dist` m straight ahead, the caster locked
    on to him (`INFERRED`), each shot aimed at him when it is created (`er-mechanics-ashes`
    `fly_shots`, the lock-on aim, `VERIFIED` FUN_140390c20). A threat is one shot of a fan whose
    shots keep their own hit lists (`numShoot` > 1 without `isUseSharedHitList`), worth an even
    share of its event's `worth` (each shot has its own lists, `bullet_hits`), else one event.
    `mode`:

    - 'dynamic': a shot's chain is re-aimed between two damaging legs (the chain back from a
      damaging bullet to the previous damaging one passes a non-damaging link, and a link of that
      stretch is aimed: Glintstone Nail and Nails' speed-0 stops, Blades of Stone's charged cast).
      Each threat is flown against the defender's own roll, every link aimed at where the roll has
      taken him (`dynamic_outcome`): flown against a defender standing still, the leg after a
      rolled one comes back through the spot he left, which is not what finds him. The legs fixed
      for a still defender are `bursts` (base and first touch). A homing shot turns after his roll
      in that flight too (`er-mechanics-ashes.fly_dynamic`).
    - 'volley': a fan as above and no re-aimed chain (Glintblade Phalanx, Ancient Death Rancor):
      each shot is its own geometry, dodged as `er-mechanics-ashes.volley_outcome` dodges them.
    - 'aimed': neither, but the aim moves a damaging shot's path at one of `AIM_PROBE_M`: the
      events are flown at the defender's distance and dodged as before (`sequence_outcome`).
    - 'static': the aimed flight is the straight one, so `punish_values` keeps `geometry`.
    In 'volley', 'aimed' and 'static' a geometry with a homing shot carries its `HOMING` record
    (`flight`; a volley leg's names its own shots), and each dodge is read against it flown again
    after him (`er-mechanics-ashes._homing_touch`), each aimed shot aimed at where his dodge has
    taken him when it is created.
    """

    def __init__(self, sm, mid, events, hand, worth, damaging=None, roots=None, launches=None, hits=None,
                 records=None, windows=()):
        """`windows` are the cast's TAE 795 windows (`SpellModel.cast_windows`), what the defender's
        strike reads (`strike_reach`). `roots` ({(frame, ref): Bullet id}) replaces the Magic row's refs: a consumable's bullet
        (`EquipParamGoods.refId_default`), with `mid` None. `launches` ({(frame, ref):
        `er-mechanics-ashes.bullet_launch`}) is where each event's root shot leaves (missing: the
        old fixed placement); `hits` (the `evaluate` entries behind `worth`) lets an 'aimed' cast's
        caught defender be played out (`attach_caught`, `er-mechanics-ashes.CaughtModel`).
        `records` ({(frame, ref): `cast_record`}) are the hit priority records the events take: an
        'aimed' event's geometry carries its own. Each event is one launch, whose shots all carry
        its id, so a record holds back none of an event's own shots; the events of one record
        land one between them. In a 'volley' shot `s` of every event of one record is one threat
        (`INFERRED`: worth the first such event's share of a shot), and in 'dynamic' the record's
        events are weighed by `_shared`."""
        self.sm, self.mid, self.events, self.hand = sm, mid, list(events), hand
        self.windows = tuple(windows or ())
        self.launches = dict(launches or {})
        self.records = {k: v for k, v in (records or {}).items() if v is not None}
        self.hits = hits
        self.worth = dict(worth)
        self.test = sm.spell_node_damaging if damaging is None else damaging
        self.value = {}
        for (f, _), w in sorted(self.worth.items()):
            self.value[f] = self.value.get(f, 0.0) + w
        A, t = sm.ash()
        if roots is None:
            m = sm.magic[mid]
            roots = {(f, n): m[f'refId{n}'] for f, n in self.events if m[f'refCategory{n}'] == REF_BULLET}
        self.roots = roots
        self._geo, self._caught = {}, {}
        self.trees, self.fans = {}, {}
        for f, n in self.events:
            bt = A.bullet_tree(t, roots[(f, n)]) if (f, n) in roots else None
            if bt is None:
                continue
            r = sm.bullet.get(roots[(f, n)]) or {}
            own = r.get('numShoot', 1) > 1 and not r.get('isUseSharedHitList')
            self.trees[(f, n)], self.fans[(f, n)] = bt, r['numShoot'] if own else 1
        self._at, self._dyn, self._pts = {}, {}, {}
        reaimed = any(self._legs(A.fly_shots(t, bt))[1] for bt in self.trees.values())
        if reaimed:
            self.mode = 'dynamic'
        elif any(k > 1 for k in self.fans.values()):
            self.mode = 'volley'
        elif any(self._moved(A, t, bt, d, self.launches.get(k)) for k, bt in self.trees.items() for d in AIM_PROBE_M):
            self.mode = 'aimed'
        else:
            self.mode = 'static'

    def _legs(self, entries):
        """({entry index: leg number} for the damaging entries of `fly_shots` output, how many legs
        start at a re-aim) by the rule in the class doc. A damaging entry with no damaging link
        above it starts a leg of its own."""
        leg_of, n, reaimed = {}, 0, 0
        for i, e in enumerate(entries):
            if not self.test(e[0]):
                continue
            p, gap, aimed = e[5], False, e[6]
            while p >= 0 and not self.test(entries[p][0]):
                gap, aimed, p = True, aimed or entries[p][6], entries[p][5]
            if p >= 0 and not (gap and aimed):
                leg_of[i] = leg_of[p]
            else:
                reaimed += p >= 0
                leg_of[i], n = n, n + 1
        return leg_of, reaimed

    def _moved(self, A, t, bt, dist, launch=None):
        a = [p for n, p, _, _ in A.fly_tree(t, bt, launch=launch) if self.test(n)]
        b = [p for n, p, _, _ in A.fly_tree(t, bt, (0.0, dist), launch) if self.test(n)]
        return len(a) != len(b) or any(len(x) != len(y) or any(math.dist(q, r) > 1e-6 for q, r in zip(x, y))
                                       for x, y in zip(a, b))

    def bursts(self, dist):
        """At `dist` (rounded to 0.01 m): 'volley', ([(geometries, their threats)] in time order,
        {threat: worth}); otherwise `_bursts` of the events' aimed geometries. A threat is
        (frame, ref, root shot) for a fan with its own lists, else (frame, ref, None)."""
        key = round(dist, 2)
        if key in self._at:
            return self._at[key]
        A, t = self.sm.ash()
        target = (0.0, key)
        if self.mode in ('volley', 'dynamic'):
            geoms, worth = [], {}
            for (f, n), bt in self.trees.items():
                start, k = (self.hand + f) / FPS, self.fans[(f, n)]
                launch = self.launches.get((f, n))
                entries = A.fly_shots(t, bt, target, launch)
                leg_of, _ = self._legs(entries)
                legs = {}
                rec = self.records.get((f, n))
                for i, e in enumerate(entries):
                    if i in leg_of:
                        shot = e[4] if k > 1 else None
                        unit = ('record', rec[0], shot) if rec is not None else (f, n, shot)
                        pts, air, idx = legs.setdefault((unit, leg_of[i]), ([], [True], []))
                        pts += self.sm._path_points(e[0], e[1], e[2], start, launch)
                        air[0] = air[0] and self.sm._air(e[0])
                        idx.append(i)
                for s in range(k):
                    shot = s if k > 1 else None
                    if rec is not None:
                        worth.setdefault(('record', rec[0], shot), self.worth.get((f, n), 0.0) / k)
                    else:
                        worth[(f, n, shot)] = self.worth.get((f, n), 0.0) / k
                for (unit, _), (pts, air, idx) in sorted(legs.items(), key=lambda x: (str(x[0][0]), x[0][1])):
                    g = A.contact_geometry(bullet_points=sorted(pts), air=air[0])
                    if g is not None:
                        # A leg with a homing shot, or one a carrier makes, is read against its shots
                        # flown again after each dodge (`er-mechanics-ashes._homing_touch`), named
                        # by their lineage keys, which every flight of the tree shares.
                        part, homing = A.homing_part(t, bt, start, launch, self.test, [entries.keys[i] for i in idx])
                        if homing:
                            g['HOMING'] = A.homing_record(t, [part])
                        geoms.append((g, unit))
            out = (_threat_bursts(geoms), worth)
        else:
            geo = []
            for (f, n), bt in self.trees.items():
                g = self.sm.flight(bt, (self.hand + f) / FPS, self.test, target, launch=self.launches.get((f, n)))
                if g is not None:
                    g['EVENT'] = (f, n)
                    if (f, n) in self.records:
                        g['HPR'] = self.records[(f, n)]
                    geo.append((g, f))
            if self.hits is not None:
                self.sm.attach_caught(self.mid, geo, self.hits, self.value, self.roots)
            self._geo[key] = geo
            out = _bursts(geo, self.value)
        self._at[key] = out
        return out

    def outcome(self, A, dist, floor, my_roll, strikes, catches=None, hp=None, poise=None, plan=None):
        """`volley_outcome`, `sequence_outcome` or `dynamic_outcome` at `dist` (`net_parts`' `at`),
        what one cast lands capped at `hp`; an 'aimed' cast's caught defender is played out with
        `poise` (`er-mechanics-ashes.CaughtModel`) when the geometries carry their `CAUGHT`
        records. `plan` times his first dodge (`er-mechanics-ashes.lookalike_plan`, `Lookalikes`)."""
        if self.mode == 'dynamic':
            return self.dynamic_outcome(A, dist, floor, my_roll, strikes, hp, plan=plan)
        b = self.bursts(dist)
        if self.mode == 'volley':
            return A.volley_outcome(b[0], b[1], dist, floor=floor, my_roll=my_roll, strikes=strikes, run=True,
                                    catches=catches, hp=hp, plan=plan)
        key = round(dist, 2)
        if key not in self._caught:
            self._caught[key] = A.CaughtModel.of(self._geo.get(key), poise, self.value)
        return A.sequence_outcome(b, dist, floor=floor, my_roll=my_roll, strikes=strikes, run=True, catches=catches,
                                  caught=self._caught[key], hp=hp, plan=plan)

    def first_oks(self, A, dist, moves):
        """`Lookalikes`' read of this cast at `dist`: per move of `moves`, the escaping starts of
        the first dodge (`outcome`'s `ok`, on the `DODGE_STEP` grid) against its first burst that
        reaches a defender standing there, None when none does. A 'dynamic' cast's are
        `dynamic_outcome`'s, each first roll followed by the re-rolls `_rolled` makes."""
        import numpy as np
        if self.mode == 'dynamic':
            key = round(round(dist / DYNAMIC_DIST_M) * DYNAMIC_DIST_M, 3)
            if (key, 'still') not in self._dyn:
                self._dyn[(key, 'still')] = self._touches(key, None)
            if not self._dyn[(key, 'still')]:
                return None
            geoms = self.geometries(key)
            last = max(float(g['T'].max()) for g in geoms) if geoms else 0.0
            starts = np.arange(0.0, last + DYNAMIC_STEP, DYNAMIC_STEP)
            roll = [m['anim'] for m in A.roll_motion()]
            grid = np.arange(0.0, last + 1.0, A.DODGE_STEP)
            idx = np.minimum((grid // DYNAMIC_STEP).astype(int), len(starts) - 1)
            out = []
            for m in moves:
                # `dynamic_outcome` keys its rows by roll direction.
                rk = (key, roll.index(m['anim'])) if m['anim'] in roll else (key, 'move', m['anim'])
                if rk not in self._dyn:
                    self._dyn[rk] = [self._rolled(key, m, s) for s in starts]
                rows = self._dyn[rk]
                out.append(np.array([not rows[i] for i in idx], bool))
            return out
        b = self.bursts(dist)
        if self.mode == 'volley':
            for geoms, _ in b[0]:
                if np.isfinite(A._catches_each(geoms, dist, None, [0.0])[0]).any():
                    grid = np.arange(0.0, max(float(g['T'].max()) for g in geoms) + 1.0, A.DODGE_STEP)
                    return [~np.isfinite(A._catches_each(geoms, dist, m, grid)).any(axis=1) for m in moves]
            return None
        return _burst_oks(A, [gs for gs, _ in b], dist, moves)

    def geometries(self, dist):
        b = self.bursts(dist)
        return [g for gs, _ in (b[0] if self.mode in ('volley', 'dynamic') else b) for g in gs]

    def _shared(self, worth, units):
        """What threats `units` land between them: the events of one hit priority record
        (`records`) land one between them, every shot of it in `units` (one launch, one id),
        worth the most any event's shots carry (`INFERRED`: the event that touches first lands)."""
        held, total = {}, 0.0
        for u in units:
            r = self.records.get(u[:2])
            if r is None:
                total += worth[u]
            else:
                ev = held.setdefault(r[0], {})
                ev[u[:2]] = ev.get(u[:2], 0.0) + worth[u]
        return total + sum(max(ev.values()) for ev in held.values())

    def _units(self):
        return [(f, n, s if self.fans[(f, n)] > 1 else None) for (f, n) in self.trees
                for s in range(self.fans[(f, n)])]

    def _touches(self, dist, move, starts=()):
        """{threat: first real frame it touches him} for a defender `dist` m ahead who stands, then
        rolls `move` at each real frame in `starts` (each roll carries him on from where the last one
        left him; invincible for `move['iframes']` from each start), every shot aimed at where he is
        when it is created (`er-mechanics-ashes.fly_dynamic`). `move` None or no starts: standing."""
        import numpy as np
        A, t = self.sm.ash()
        starts = tuple(starts) if move is not None else ()
        path = np.array(move['path'], float) if move is not None else np.zeros((1, 2))
        n = len(path)
        # Where each roll begins: the end of the one before at its own start.
        base = [np.zeros(2)]
        for a, b in zip(starts, starts[1:]):
            base.append(base[-1] + path[min(int(b - a), n - 1)])
        base = np.array(base) if starts else np.zeros((0, 2))
        rolls = np.array(starts, float)

        def at(T):
            """(x, z, invincible) at real frames `T` (an array)."""
            T = np.asarray(T, float)
            if not len(rolls):
                return np.zeros(T.shape), np.full(T.shape, dist), np.zeros(T.shape, bool)
            i = np.searchsorted(rolls, T, side='right') - 1
            moving = i >= 0
            ii = np.maximum(i, 0)
            u = T - rolls[ii]
            j = np.clip(np.floor(u).astype(int), 0, n - 1)
            x = np.where(moving, base[ii, 0] + path[j, 0], 0.0)
            z = dist + np.where(moving, base[ii, 1] + path[j, 1], 0.0)
            return x, z, moving & (u < (move['iframes'] if move is not None else 0.0))

        plain = [tuple(map(float, b)) for b in base]

        def where(frame):
            i = bisect.bisect_right(starts, frame) - 1
            if i < 0:
                return (0.0, dist)
            j = min(max(int(math.floor(frame - starts[i])), 0), n - 1)
            return (plain[i][0] + float(path[j, 0]), dist + plain[i][1] + float(path[j, 1]))
        # A shot whose every frame point stays further from his start than the rolls carry him, plus
        # a metre of hurtbox, its radius and half its longest frame step, cannot touch him: skip it.
        reach = float(np.hypot(path[:, 0], path[:, 1]).max()) * max(1, len(starts)) if len(starts) else 0.0
        bound = reach + 1.0
        hit = {}
        push = A._pose()[1].PUSH_CAPSULE_RADIUS
        for (f, nref), bt in self.trees.items():
            fire = self.hand + f
            k = self.fans[(f, nref)]
            launch = self.launches.get((f, nref))
            if launch is not None and math.isfinite(launch['peak']):
                # The shot leaves where his body held the caster back when it was fired.
                held = max(0.0, launch['peak'] - (where(fire)[1] - 2 * push))
                launch = {**launch, 'pos': (launch['pos'][0], launch['pos'][1], launch['pos'][2] - held)}
            def defender(T, who):
                x, z, immune = at(T)
                return x, z, ~immune
            for node, p, t0, shot in A.fly_dynamic(t, bt, defender, fire, launch):
                unit = (f, nref, shot if k > 1 else None)
                if not self.test(node):
                    continue
                half = float(np.linalg.norm(np.diff(p, axis=0), axis=1).max()) / 2 if len(p) > 1 else 0.0
                if float(np.hypot(p[:, 0], p[:, 2] - dist).min()) > bound + node['radius_m'] + half:
                    continue
                # The legs flown before his first roll are the same at every start: sample them once.
                pk = (node['bullet'], t0, fire, p.tobytes())
                if pk not in self._pts:
                    if len(self._pts) > PTS_CACHE:
                        self._pts.clear()
                    self._pts[pk] = self.sm._point_arrays(node, p, t0, fire)
                T, X, Z, D = self._pts[pk]
                if not len(T):
                    continue
                xr, zr, immune = at(T)
                touch = ~immune & (np.hypot(X - xr, Z - zr) < D)
                if touch.any():
                    first = float(T[touch].min())
                    hit[unit] = min(hit.get(unit, math.inf), first)
        return hit

    def _rolled(self, dist, move, first):
        """`_touches` of a defender who rolls `move` at real frame `first` and then rolls again, up to
        `DYNAMIC_ROLLS` rolls, each as soon as the last one's dodge cancel lets him, centred on the
        next touch it can still cover (`INFERRED` policy: he watches the shots that come back)."""
        cycle = move['dodge'] or len(move['path'])
        rolls = [first]
        hit = self._touches(dist, move, rolls)
        while hit and len(rolls) < DYNAMIC_ROLLS:
            ahead = [T for T in hit.values() if T >= rolls[-1] + cycle]
            if not ahead:
                break
            rolls.append(max(rolls[-1] + cycle, min(ahead) - move['iframes'] / 2))
            hit = self._touches(dist, move, rolls)
        return hit

    def dynamic_outcome(self, A, dist, floor, my_roll, strikes, hp=None, plan=None):
        """`reaction_outcome` for a cast whose shots re-aim at him at every link (mode 'dynamic'):
        each threat flown against his own roll (`_touches`), so a re-aimed leg finds him where the
        roll took him. His first roll is timed as `dodge_presses` times it to escape every threat
        (or as `plan` times it, `reaction_outcome`'s); then he rolls again at each pass that comes
        back and that his dodge cancel lets him meet (`_rolled`, up to `DYNAMIC_ROLLS`). Each
        threat that touches him lands once, independently (`INFERRED`). He stands when rolling
        would cost him more. Same keys as `volley_outcome`; first rolls every `DYNAMIC_STEP` real
        frames, distances to `DYNAMIC_DIST_M`. What lands at one press is capped at `hp`."""
        import numpy as np
        key = round(round(dist / DYNAMIC_DIST_M) * DYNAMIC_DIST_M, 3)
        units = self._units()
        worth = {u: self.worth.get(u[:2], 0.0) / self.fans[u[:2]] for u in units}
        cap = (lambda v: min(v, hp)) if hp is not None else (lambda v: v)
        if (key, 'still') not in self._dyn:
            self._dyn[(key, 'still')] = self._touches(key, None)
        still = self._dyn[(key, 'still')]
        base = cap(self._shared(worth, still))
        if base <= 0:
            return {'value': 0.0, 'base': 0.0, 'p_evade': 0.0, 'p_punish': 0.0, 'punish_hp': 0.0}
        last = max(float(g['T'].max()) for g in self.geometries(key)) if self.geometries(key) else 0.0
        starts = np.arange(0.0, last + DYNAMIC_STEP, DYNAMIC_STEP)
        moves = A.roll_motion()
        strikes = None if strikes is None else np.asarray(strikes, float)
        value = p_evade = p_punish = 0.0
        # The dodge grid is `DODGE_STEP`; each step reads the start at or before it.
        grid = np.arange(0.0, last + 1.0, A.DODGE_STEP)
        oks = []
        for k, m in enumerate(moves):
            if (key, k) not in self._dyn:
                self._dyn[(key, k)] = [self._rolled(key, m, s) for s in starts]
            rows = self._dyn[(key, k)]
            idx = np.minimum((grid // DYNAMIC_STEP).astype(int), len(rows) - 1)
            oks.append(np.array([not rows[i] for i in idx], bool))
        planned = plan(moves, oks, 0.0, floor) if plan is not None else None
        for k, m in enumerate(moves):
            rows = self._dyn[(key, k)]
            idx = np.minimum((grid // DYNAMIC_STEP).astype(int), len(rows) - 1)
            ok = oks[k]
            ready = m['ready'] if m['ready'] is not None else len(m['path'])
            x, z = m['path'][min(int(ready), len(m['path']) - 1)]
            gap = math.hypot(x, key + z)
            close = max(0.0, gap - A.PUNISH_REACH_M) * A._neutral().frames_per_metre()
            arrive = np.sort(strikes + close) if strikes is not None and my_roll is not None and len(strikes) \
                else None
            for s, w in (planned[k] if planned is not None else A.dodge_presses(ok, 0.0, floor=floor)):
                i = int(round(s / A.DODGE_STEP))
                got = rows[idx[i]] if 0 <= i < len(idx) else still
                w /= len(moves)
                if got:
                    value += w * cap(self._shared(worth, got))
                    continue
                p_evade += w
                if arrive is not None:
                    p_punish += w * (int(np.searchsorted(arrive, my_roll - s - ready, side='left')) / len(arrive))
        if value - p_punish * A.PUNISH_HP > base:
            # Rolling would leave the caster more than standing does (a re-aimed nail finds the roller
            # that would have missed him standing): knowing the spell, he stands.
            return {'value': base, 'base': base, 'p_evade': 0.0, 'p_punish': 0.0, 'punish_hp': 0.0, 'moves': None}
        return {'value': value, 'base': base, 'p_evade': p_evade, 'p_punish': p_punish,
                'punish_hp': p_punish * A.PUNISH_HP, 'moves': moves[0]['anim']}

    def first_touch(self, A, dist):
        """The real frame the cast first touches a defender `dist` m ahead who does not dodge."""
        return min((float(A._catches([g], dist, None, [0.0])[1][0]) for g in self.geometries(dist)),
                   default=math.inf)

    def strike_reach(self, A, dist, hp=None, poise=None):
        """(cuts, touch, kept, base): what the defender's strike reads of this cast at `dist`
        (`net_parts`, `StrikeReach` for a plain geometry list): the launches in order between which
        a strike can stop it, its first touch on him standing there or where his run in to
        `PUNISH_REACH_M` ends, `kept(cut)` what lands on him standing when only the shots launched by
        `cuts[cut - 1]` fly, and `base` what lands with none stopped, each capped at `hp`. A
        'dynamic' or 'volley' threat lands once, alone, as `dynamic_outcome` and `volley_outcome`
        count it, launched at its event's fire frame or its shot's launch; an 'aimed' or 'static'
        cast is read as a plain list of this distance's geometries (`poise`: the defender's, its
        caught defender's, as `outcome` takes it)."""
        cap = (lambda v: min(v, hp)) if hp is not None else (lambda v: v)
        near = round(min(dist, A.PUNISH_REACH_M), 2)
        touch = min(self.first_touch(A, dist), self.first_touch(A, near))

        def below(cuts, cut):
            return math.inf if cut is None else (cuts[cut - 1] if cut > 0 else -math.inf)
        if self.mode == 'dynamic':
            key = round(round(dist / DYNAMIC_DIST_M) * DYNAMIC_DIST_M, 3)
            if (key, 'still') not in self._dyn:
                self._dyn[(key, 'still')] = self._touches(key, None)
            still = self._dyn[(key, 'still')]
            units = self._units()
            worth = {u: self.worth.get(u[:2], 0.0) / self.fans[u[:2]] for u in units}
            fire = {u: self.hand + u[0] for u in units}
            cuts = sorted(set(fire.values()))

            def kept(cut):
                lim = below(cuts, cut)
                return cap(self._shared(worth, {u: t for u, t in still.items() if fire[u] <= lim}))
            return cuts, touch, kept, kept(None)
        if self.mode == 'volley':
            bursts, worth = self.bursts(dist)
            seen = {}
            for geoms, units in bursts:
                tau = A._catches_each(geoms, dist, None, [0.0])[0]
                for g, u, x in zip(geoms, units, tau):
                    if g is None:
                        continue
                    L, hit = seen.get(u, (math.inf, False))
                    seen[u] = (min(L, _launch(g)), hit or math.isfinite(float(x)))
            cuts = sorted({L for L, _ in seen.values() if math.isfinite(L)})

            def kept(cut):
                lim = below(cuts, cut)
                return cap(sum(worth[u] for u, (L, hit) in seen.items() if hit and L <= lim))
            return cuts, touch, kept, kept(None)
        self.bursts(dist)
        key = round(dist, 2)
        geo = self._geo.get(key) or []
        if not geo:
            return [], touch, lambda cut: 0.0, 0.0
        if key not in self._caught:
            self._caught[key] = A.CaughtModel.of(geo, poise, self.value)
        cm = self._caught[key]
        reach = StrikeReach(A, geo)
        price = (lambda h, vk: cm.worth_of(key, h, vk)) if cm is not None else reach.raw_price
        return reach.cuts, touch, lambda cut: reach.kept(key, cut, self.value, price, hp), \
            reach.kept(key, None, self.value, price, hp)


def _threat_bursts(geoms):
    """[(geometry, threat)] grouped into bursts of overlapping live frames: [(geometries, threats)]."""
    bursts = []
    for g, u in sorted(geoms, key=lambda e: (float(e[0]['T'].min()), str(e[1]))):
        if bursts and g['T'].min() <= bursts[-1][2]:
            bursts[-1][0].append(g), bursts[-1][1].append(u)
            bursts[-1][2] = max(bursts[-1][2], g['T'].max())
        else:
            bursts.append([[g], [u], g['T'].max()])
    return [(gs, us) for gs, us, _ in bursts]


#: Memory slots: `er-mechanics-pouch.MEMORY_SLOTS` (eight Memory Stones and Moon of Nokstella).
MEMORY_SLOTS = 10
#: Relative gain below which the kit search stops: `er-mechanics-pouch.EPS`.
KIT_EPS = 1e-6
#: Bump when `punish_situations` changes what it caches.
SITUATIONS_VERSION = 3
#: Gaps an attacker may run in to before he presses (`net_values`; `INFERRED`: a 1 m grid from
#: about body contact out to the pool's mean reach).
APPROACH_GAPS_M = (1.5, 2.5, 3.5, 4.5)
#: Rival skills the kit report lists, best first.
RIVALS_SHOWN = 10
#: The ash rankings' blacklist and unobtainable marker (`er-mechanics-ash-table`), which
#: `spell_like_skills` honours too.
ASH_BLACKLIST = os.path.join(HERE, '..', 'data', 'ash-blacklist.toml')
UNOBTAINABLE_SORT_ID = 999999


def greedy_kit(values, weight, total, slot_cost, slots, labels=None, names=None, base=None):
    """Add spells one at a time, each round the one that raises the kit value most, while it fits
    the memory slots and raises it by more than `KIT_EPS`. `values[spell]` lists its landed damage
    per situation, `weight` the situations' weights. Kit value: the weighted mean over situations
    of the best member's damage there, or of `base` (what the build lands without a spell: its
    weapons' skills) where that is more."""
    best = list(base) if base is not None else [0.0] * len(weight)
    used, rounds = 0, []
    cur = sum(w * b for w, b in zip(weight, best)) / total
    left = dict(values)
    while left:
        pick = None
        for s, v in left.items():
            if used + slot_cost[s] > slots:
                continue
            val = sum(w * max(b, x) for w, b, x in zip(weight, best, v)) / total
            if pick is None or val > pick[1] + 1e-12:
                pick = (s, val)
        if pick is None or pick[1] <= cur * (1.0 + KIT_EPS) + 1e-12:
            break
        s, val = pick
        v = left.pop(s)
        covers = sum(w for w, b, x in zip(weight, best, v) if x > b) / total
        best = [max(b, x) for b, x in zip(best, v)]
        used += slot_cost[s]
        rounds.append({'id': s, 'name': (names or {}).get(s, s), 'variant': (labels or {}).get(s),
                       'slots': slot_cost[s], 'value': round(val, 1), 'gain': round(val - cur, 1),
                       'best_share': round(covers, 3)})
        cur = val
    return rounds


def punish_situations(rl):
    """{'situations': [{'distance', 'recovery', 'weight'}], 'whiff_share'}: the spaced whiffs of the
    RL window's opponent pool (`er-mechanics-exchange.opponent_pool`). Each build throws the openers
    of the four attack families the pouch files its own hit under (`er-mechanics-armor-targets`
    `FAMILY_JUDGES`: R1, R2, running, jump; equal family shares, `INFERRED` as there), each from its
    world reach (`er-mechanics-reach.reach_summary`, the class median when unposed) at a defender
    who runs back on reaction (`er-mechanics-neutral.backoff_whiffs`). A whiff leaves him the real
    frames from the end of its last hit window to its dodge cancel, at the distance the run left
    between them. One situation per distinct (distance, recovery), weighted by builds x family share
    x reaction quantile; `whiff_share` is the share of the openers that whiff. Cached beside the
    pool."""
    EX = _mod('er_mechanics_exchange', 'er-mechanics-exchange.py')
    NEU = _mod('er_mechanics_neutral', 'er-mechanics-neutral.py')
    AT = _mod('er_mechanics_armor_targets', 'er-mechanics-armor-targets.py')
    reg = ATK.Regulation(None)
    raw = EX.opponent_pool(reg, EX.CACHE / 'builds.jsonl', rl - WINDOW, rl + WINDOW)
    stamp = [SITUATIONS_VERSION] + list(raw['stamp'])
    path = EX.CACHE / f"spell-punish-{rl - WINDOW}-{rl + WINDOW}-{raw['stamp'][-1]}.json"
    if path.exists():
        got = json.loads(path.read_text())
        if got.get('stamp') == stamp:
            return got['spaced']
    reach_mod = EX._reach_module()
    ids = EX.weapon_ids()
    count = {}
    for key, _ in raw['builds']:
        count[key] = count.get(key, 0) + 1
    merged, thrown, whiffed = {}, 0.0, 0.0
    print(f'spell punish situations: reading {len(count)} pool weapons ({path.name} is stale or missing)',
          file=sys.stderr, flush=True)
    for i, (key, n) in enumerate(sorted(count.items()), 1):
        if i % 25 == 0:
            print(f'  {i}/{len(count)} pool weapons', file=sys.stderr, flush=True)
        name, grip = key.rsplit('|', 1)
        two = grip == '2h'
        g = 'both' if two else 'one'
        off = ATK.TWO_HAND_JUDGE_OFFSET if two else 0
        rows = ATK.weapon_attacks(reg, ids[name], g)
        summary = reach_mod.reach_summary(ids[name], g)
        fams = []
        for judges in AT.FAMILY_JUDGES.values():
            got = [r for r in rows if r.get('judge') in {j + off for j in judges} and r.get('hit_windows')
                   and r['cancel_frame'].get('dodge') is not None]
            if got:
                fams.append(got)
        for got in fams:
            for atk in got:
                share = n / len(fams) / len(got)
                reach = (summary.get(atk['slot']) or {}).get('world_reach_m')
                if reach is None:
                    reach = reach_mod.class_fallback(ids[name], g, atk['slot']).get('world_reach_m')
                if reach is None:
                    continue
                start = min(w[0] for w in atk['hit_windows'])
                end = max(w[1] for w in atk['hit_windows'])
                gate = atk['cancel_frame']['dodge']
                thrown += share
                for w, whiff, gap in NEU.backoff_whiffs(reach, (start, end)):
                    if not whiff:
                        continue
                    whiffed += share * w
                    k = (round(gap, 1), round(max(gate - end, 0.0) * 2) / 2)
                    merged[k] = merged.get(k, 0.0) + share * w
    out = {'situations': [{'distance': d, 'recovery': f, 'weight': round(n, 6)}
                          for (d, f), n in sorted(merged.items())],
           'whiff_share': round(whiffed / thrown, 6) if thrown else 0.0}
    path.write_text(json.dumps({'stamp': stamp, 'spaced': out}))
    return out


def _burst_oks(A, bursts, dist, moves):
    """Per move of `moves`, the escaping starts of a first dodge against the first of `bursts`
    (geometry lists in time order) that reaches a defender standing `dist` m ahead, on the grid
    `er-mechanics-ashes.reaction_outcome` reads them on; None when none reaches him."""
    import numpy as np
    for gs in bursts:
        live = [g['T'].max() for g in gs if g is not None]
        if not live or A._catches(gs, dist, None, [0.0])[0][0] < 0:
            continue
        grid = np.arange(0.0, max(live) + 1.0, A.DODGE_STEP)
        return [A._catches(gs, dist, m, grid)[0] < 0 for m in moves]
    return None


#: TAE event 66 SpEffect on a charged skill's opening during which letting go of L2 plays the early
#: release: `er-mechanics-ashes.RELEASE_EARLY_SPEFFECT`, which has the sources (this module loads
#: the ash model only when a `SpellModel` asks for it, so the id is written here too; the selftest
#: checks the two agree).
RELEASE_EARLY_SPEFFECT = 100285


def skill_releases(A, t, sid, wid, level, stance=None):
    """`er-mechanics-ashes.skill_releases` (the rule and its sources) one-handed: [the early
    release of skill `sid`'s opening on weapon `wid`, let go at the `RELEASE_EARLY_SPEFFECT`
    window's first real frame], or []. `skill_option` prices it as a follow-up pressed there, so
    the tap plays the opening up to that frame and its own clip from it, while the hold plays the
    opening on: the two part there (`skill_lookalikes`)."""
    return A.skill_releases(t, sid, wid, level, 'one', stance)


def skill_lookalikes(A, hold, tap, later=(), shows=None):
    """The `Lookalikes` of a charged skill's hold and its early release: `hold` and `tap` are
    `SpellModel.skill_attack` results (the tap's with its `skill_releases` entry as `follow`),
    keyed 'hold' and 'tap'. They play the opening clip alike up to the release, the tap's second
    part's offset (`skill_option`: its press), and part where the release shows: the press plus
    `shows[0]` real frames (`er-mechanics-ashes.release_visible`; None: 0, the press itself).
    `later` [(skill_attack, weight)] are the same release let go later in its window
    (`release_frames`; the tap's own weight is 1 less theirs, `shows[i]` each one's), keyed
    'tap+<frames after the tap's press>': each parts from the hold where it shows and two releases
    where the first of them shows, and each is a release he reacts to as it shows (`Lookalikes`
    `events`). The hold weighs 1. None without a tap part; a later release without one is left out
    and the tap keeps its weight."""
    shows = list(shows) if shows is not None else [0] * (1 + len(later))

    def press(x):
        parts = ((x or {}).get('option') or {}).get('parts') or []
        return float(parts[1]['offset']) if x is not None and not x.get('why') and len(parts) > 1 else None
    first = press(tap)
    if hold is None or hold.get('why') or first is None:
        return None
    members, at, weights = {'hold': hold['geo'], 'tap': tap['geo']}, {'tap': first + shows[0]}, {'hold': 1.0, 'tap': 1.0}
    for (x, w), s in zip(later, shows[1:]):
        p = press(x)
        if p is None:
            continue
        k = f'tap+{round(p - first, 3):g}'
        members[k], at[k], weights[k] = x['geo'], p + s, float(w)
        weights['tap'] -= float(w)
    keys = list(at)
    splits = {('hold', k): at[k] for k in keys}
    splits.update({(a, b): min(at[a], at[b]) for i, a in enumerate(keys) for b in keys[i + 1:]})
    # Spread over its window, each release is one he reacts to as it shows (`release_events`).
    return Lookalikes(A, members, splits, weights, dict(at) if len(keys) > 1 else None)


def _geo_digest(attack):
    """A key equal only for equal geometry lists [(contact geometry, frame)]: every array
    `_catches` reads (`T`, `X`, `Z`, `R`, `PK`, `TL`), the `AIR` mark, the `HOMING` record's
    signature and the frame. None for anything else (a `CastThreats`), which is then read alone."""
    import hashlib
    import numpy as np
    if not isinstance(attack, list):
        return None
    h = hashlib.blake2b(digest_size=16)
    for g, fr in attack:
        h.update(repr((float(fr), bool(g.get('AIR')), (g.get('HOMING') or {}).get('sig'))).encode())
        for k in ('T', 'X', 'Z', 'R', 'PK', 'TL'):
            if k in g:
                h.update(k.encode() + np.ascontiguousarray(np.asarray(g[k], float)).tobytes())
    return ('geo', h.hexdigest())


def _oks_tag(oks):
    """A key equal only for equal per-move escaping starts (`er-mechanics-ashes.lookalike_plan`'s
    `tags`)."""
    import numpy as np
    return b''.join(np.packbits(np.asarray(o, bool)).tobytes() + len(o).to_bytes(4, 'little') for o in oks)


#: Reads every `Lookalikes` of this process shares while `share_lookalikes` is on (attack-similarity
#: turns it on and empties it per item): a geometry list's escaping starts per distance and family
#: (`first_oks`, keyed by `_geo_digest`: equal geometry reads equal) and the plans' memos of groups
#: whose members, splits, weights and events are equal (`lookalike_plan` keys every entry by what
#: it read). Off, each group keeps its own, as before; either way the reads are the same.
_SHARED = {'on': False, 'oks': {}, 'memos': {}, 'gen': 0}


def share_lookalikes(on=True):
    """Turn the reads `Lookalikes` share across groups on or off, and empty them ('gen' counts the
    emptyings, so what a group keeps of them is not kept past one: `Lookalikes.plan`)."""
    _SHARED['on'] = bool(on)
    _SHARED['oks'].clear()
    _SHARED['memos'].clear()
    _SHARED['gen'] += 1


class Lookalikes:
    """Attacks a reacting defender cannot tell apart from their start (ashes-of-war.md section 16a,
    'What is on screen'), each with the frame from which each pair looks different. Two spells
    whose `Magic.refType` and `castSfxId` are equal play one TimeAct's clips with one casting SFX,
    so they look alike until a shot spawns that differs (`spell_lookalikes`, `look_split`); a
    chargeable spell's tap and charge, and a charged skill's tap and hold, until the clips part.
    `members` is {key: attack}, the attack a geometry list [(contact geometry, frame)] as
    `net_parts` takes it, or a `CastThreats`; `splits` {(key, key): real frame from the press},
    a pair left out never splits; `weights` {key: weight}, equal when None (`INFERRED`: the model
    has no source of how often each is cast).

    `member(key).plan(dist)` is the `plan` (`er-mechanics-ashes.reaction_outcome`'s) of member
    `key` at `dist`: the other members' first bursts read once per distance and family of moves
    (`first_oks`), the attack's own handed in by the outcome (so a free-aimed alternative brings
    its own), and `er-mechanics-ashes.lookalike_plan` between them. `events` {key: real frame it
    releases} are the releases he reacts to as they come (`lookalike_plan` `events`; None, or a
    key left out, none)."""

    def __init__(self, A, members, splits, weights=None, events=None):
        self.A = A
        self.members = dict(members)
        self.keys = list(self.members)
        self.splits = [[math.inf if a == b else splits.get((a, b), splits.get((b, a), math.inf))
                        for b in self.keys] for a in self.keys]
        self.weights = [1.0 if weights is None else float(weights[k]) for k in self.keys]
        self.events = None if not events else [events.get(k) for k in self.keys]
        self._oks, self._memo, self.chosen, self._digest, self._reads = {}, {}, {}, {}, {}
        self._content = False

    def __len__(self):
        return len(self.keys)

    def _digest_of(self, key):
        if key not in self._digest:
            self._digest[key] = _geo_digest(self.members[key]) or ('member', key)
        return self._digest[key]

    def content(self):
        """A key equal only for groups whose plans read the same (`share_lookalikes`): every member's
        geometry (`_geo_digest`), in order, the splits, the weights and the events; None when a member
        is a `CastThreats`, whose reads stay its group's own."""
        if self._content is False:
            digests = [self._digest_of(k) for k in self.keys]
            self._content = None if any(d[0] != 'geo' for d in digests) else (
                tuple(d[1] for d in digests), tuple(tuple(r) for r in self.splits), tuple(self.weights),
                None if self.events is None else tuple(self.events))
        return self._content

    def first_oks(self, key, dist, moves):
        """Member `key`'s first-dodge escaping starts at `dist` per move (`CastThreats.first_oks`,
        or `_burst_oks` of a geometry list's bursts), None when nothing of it reaches him. Members
        whose geometry lists are equal (`_geo_digest`: a hold spell let go early on frames between
        which it casts nothing, Gelmir's Fury's 81) share one read, which is what each would read,
        and with `share_lookalikes` on so do every group's of this process."""
        digest = self._digest_of(key)
        fk = (digest, round(dist, 2), tuple(m['anim'] for m in moves))
        cache = _SHARED['oks'] if _SHARED['on'] and digest[0] == 'geo' else self._oks
        if fk not in cache:
            x = self.members[key]
            got = x.first_oks(self.A, dist, moves) if isinstance(x, CastThreats) else \
                _burst_oks(self.A, [gs for gs, _ in _bursts(x, {})], dist, moves)
            cache[fk] = (got, None if got is None else _oks_tag(got))
        return cache[fk]

    def plan(self, key, dist, moves, oks, cue, floor):
        """`lookalike_plan`'s presses for member `key` at `dist`, its own escaping starts `oks`.
        One memo per distance and family serves every member and floor (`lookalike_plan` `tags`)."""
        me = self.keys.index(key)
        family = tuple(m['anim'] for m in moves)
        # The other members' reads and the memo at this distance and family are what every call of
        # one member there looks up again: kept, until the shared reads are emptied or turned on or
        # off (`share_lookalikes`).
        rk = (me, dist, family, _SHARED['on'], _SHARED['gen'])
        if rk not in self._reads:
            others = [None if j == me else self.first_oks(k, dist, moves) for j, k in enumerate(self.keys)]
            # A group that reads as another of this process does (`content`) shares its memo.
            content = self.content() if _SHARED['on'] else None
            self._reads[rk] = others, (self._memo if content is None else _SHARED['memos'].setdefault(
                content, {})).setdefault((round(dist, 2), family), {})
        others, memo = self._reads[rk]
        tag = _oks_tag(oks)
        every = [oks if j == me else g[0] for j, g in enumerate(others)]
        tags = [tag if j == me else g[1] for j, g in enumerate(others)]
        got = self.A.lookalike_plan(every, self.weights, self.splits, me, cue, floor, tags=tags, memo=memo,
                                    events=self.events)
        # When he decided, for the reports: the cue (he committed) or a split (he waited for it).
        self.chosen[(key, round(dist, 2), floor, family)] = got['at']
        return got['presses']

    def member(self, key):
        """What `net_parts` takes as `look` for member `key`."""
        return _Lookalike(self, key)


class _Lookalike:
    """One member of a `Lookalikes` group, as `net_parts` takes it (`look`)."""

    def __init__(self, group, key):
        self.group, self.key = group, key

    def plan(self, dist):
        group, key = self.group, self.key
        return lambda moves, oks, cue, floor: group.plan(key, dist, moves, oks, cue, floor)


def release_key(key, member, first=False):
    """The `spell_lookalikes` key of release member `member` (a `cast_inputs` entry) of the cast
    keyed `key`: `key` itself for the earliest release (`first`), else `key` + (its lead,), or for
    a string key '<key>@<its lead>'."""
    if first:
        return key
    lead = round(float(member['lead']), 3)
    return f'{key}@{lead:g}' if isinstance(key, str) else tuple(key) + (lead,)


def release_events(sm, casts):
    """`Lookalikes`' `events` of the casts `casts` ({key: {'mid', 'anim', 'hand', 'lead', 'weight'}}):
    {key: real frame from the press its leaving 045010 shows (`release_lead` plus
    `release_shows`)} for each release (045011: a tap, an early release), when one of them is a
    release spread over its window (a 'weight' below 1, `cast_inputs`); else None, the plan as it
    was."""
    if not any(float(c.get('weight', 1.0)) < 1.0 for c in casts.values()):
        return None
    out = {}
    for k, c in casts.items():
        if c['anim'] == TAP:
            lead = sm.release_lead(c['mid'], TAP, c.get('lead'))
            out[k] = c['hand'] + lead + sm.release_shows(c['mid'], lead)
    return out


def spell_lookalikes(sm, A, casts):
    """{key: `Lookalikes` member view, or None} for the casts `casts` ({key: {'mid', 'anim' (its
    release clip), 'hand' (its hand-off frame), 'attack' (what `net_parts` scores), and from
    `cast_inputs` 'lead', 'weight', 'members'}}) of the spells one caster can cast: those of one
    (`Magic.refType`, `castSfxId`) are one group (`look_split` says when each pair parts), the casts
    weighted equally. A cast's release members (`cast_inputs` 'members': the same tap let go later)
    are members of the group too, keyed by `release_key`, the cast's weight split over them by
    their 'weight' (`er-mechanics-ashes.release_frames`). A cast alone in its group gets None, which
    leaves `net_parts` exactly as it was. `INFERRED`: equal `castSfxId` SFX look the same and a
    different one is told apart from the first frame (only the ids are read)."""
    flat = {}
    for k, c in casts.items():
        flat[k] = c
        for x in c.get('members') or ():
            flat[release_key(k, x)] = x
    by = {}
    for k, c in flat.items():
        m = sm.magic[c['mid']]
        by.setdefault((m['refType'], m['castSfxId']), []).append(k)
    out = {}
    for keys in by.values():
        if len(keys) < 2:
            out[keys[0]] = None
            continue
        splits = {(a, b): sm.look_split(flat[a], flat[b]) for i, a in enumerate(keys) for b in keys[i + 1:]}
        # A release spread over its window: every release of the group is one he reacts to as it
        # comes (`release_events`).
        group = Lookalikes(A, {k: flat[k]['attack'] for k in keys}, splits,
                           {k: float(flat[k].get('weight', 1.0)) for k in keys},
                           release_events(sm, {k: flat[k] for k in keys}))
        for k in keys:
            out[k] = group.member(k)
    return out


def net_values(A, geo, value, situations, my_roll, strikes=None, defender=None, look=None, attacker_poise=None):
    """Per (distance, recovery) situation, what an attack nets as a punish: what it lands less the
    punish of a dodge of it (`er-mechanics-ashes.sequence_outcome`). `geo` is [(contact geometry,
    frame)], `value` {frame: damage of the hits that open then}; geometry h first to catch him is
    worth its frame's hits and every later frame's, or, when every geometry carries its `CAUGHT`
    record and `defender` (`SpellModel.defender`) gives his poise, what lands while he takes his
    hit reactions and then leaves (`er-mechanics-ashes.CaughtModel`); `defender`'s `hp` caps what
    one cast lands. Geometries whose live frames overlap are one burst, dodged together. The
    attacker may first run in at run speed to one of `APPROACH_GAPS_M`
    (`er-mechanics-neutral.frames_per_metre`), the opponent's lock running down meanwhile, and
    takes the best of pressing there and pressing at once. `net_parts`' `net`, which this is
    (`look` and `attacker_poise` as there)."""
    return [p['net'] for p in net_parts(A, geo, value, situations, my_roll, strikes, defender=defender, look=look,
                                        attacker_poise=attacker_poise)]


def net_parts_aims(A, geo, aims, value, situations, my_roll, strikes=None, value_one=None, defender=None,
                   look=None, catches=None, attacker_poise=None):
    """`net_parts` of an attack the caster may aim more than one way: `geo` its lock-on aim, `aims`
    [(label, geometries)] the others (`SpellModel.geometry` with `floor`: free-aimed at the
    ground). Per situation the aim whose `net` is larger (the lock-on aim on a tie); each part says
    the `aim` it took ('target' or the label), and `net_one` is the largest of the aims' own
    (`net_parts` chooses it on its own worth too). With `look` each aim is met by the plan of a
    defender who cannot tell the attack from its lookalikes, the aim thrown read as thrown and the
    others as their own lock-on aims (`INFERRED`: nothing on screen gives the aim away before the
    shot exists). `catches`, a dict, keeps each aim's `net_parts` memo for the next call on the
    same geometries (its reads do not depend on `look`). `attacker_poise` as `net_parts`'."""
    def memo(label):
        return True if catches is None else catches.setdefault(label, {})
    best = [dict(p, aim='target') for p in net_parts(A, geo, value, situations, my_roll, strikes, value_one,
                                                       memo=memo('target'), defender=defender, look=look,
                                                       attacker_poise=attacker_poise)]
    for label, g in aims or ():
        for i, p in enumerate(net_parts(A, g, value, situations, my_roll, strikes, value_one, memo=memo(label),
                                        defender=defender, look=look, attacker_poise=attacker_poise)):
            ones = [x for x in (best[i]['net_one'], p['net_one']) if x is not None]
            if p['net'] > best[i]['net']:
                best[i] = dict(p, aim=label)
            best[i]['net_one'] = max(ones) if ones else None
    return best


def _bursts(geo, value):
    """`geo` in time order, grouped into bursts of overlapping live frames: [(geometries, worth of
    each when it catches first)] (`net_values`)."""
    bursts = []
    for g, fr in sorted(geo, key=lambda e: e[0]['T'].min()):
        w = sum(value[f] for f in value if f >= fr)
        if bursts and g['T'].min() <= bursts[-1][2]:
            bursts[-1][0].append(g), bursts[-1][1].append(w)
            bursts[-1][2] = max(bursts[-1][2], g['T'].max())
        else:
            bursts.append([[g], [w], g['T'].max()])
    return [(gs, ws) for gs, ws, _ in bursts]


# ---------------------------------------------------------------------------------------------
# the defender's strike (module doc, "The kit")
#
# A defender free before an attack first touches him need not roll it: he can run in and throw his
# R1 at the attacker, and an attacker whose poise breaks under it loses whatever of the attack has
# not left him yet. The strike is read with the punish's own pieces (`er-mechanics-ashes`
# `reaction_outcome`): he presses no sooner than his reaction to the attack (cue 0) and no sooner
# than his recovery floor (`dodge_presses`' rule, `max(cue + delay, floor)`, `VERIFIED` reading),
# runs at run speed to `PUNISH_REACH_M` and lands the pool's R1 #1 its strike frames later
# (`punish_strikes`), when that is before the attack first touches him (standing where he is, or
# where his run ends) and before the attacker can roll (`my_roll`). Whether the attacker's poise
# holds is `er-mechanics-interrupt.simulate` on one hit: the R1's poise damage (`Strikes.dealt`)
# against his menu poise, through the attack's TAE 795 window open at that frame (`TOUGHNESS`),
# from the chip `STRIKE_CARRIED` names. Broken, every geometry launched at or after the strike is
# lost (a melee window always is: it is his own hitbox) and what was launched before flies on and
# lands on him standing; held, the attack lands whole on him standing. Either way the attacker
# takes the R1 (`PUNISH_HP`), and a strike that comes too late lands nothing while the attack
# lands whole. Knowing the attack, he strikes or rolls, whichever leaves the attacker less, as he
# picks the roll or the jump (`net_parts`).
#
# An attack that holds its own guard up (TAE JumpTable 3, `tae_guard`, carried as `GUARD`) meets a
# strike that lands inside a guard window with that guard (2026-11-13, the user: "Thrusting shields
# can block and attack at the same time"). The R1 is blocked by the guard model's rules
# (`er-mechanics-powerstance-guard.block_hit` against `shield_guard` of the guarding weapon at the
# window's judge, PvP): the attacker takes the guarded chip, `PUNISH_HP` times the share of the R1's
# raw damage the guard passes, instead of `PUNISH_HP`, and pays its guarded stamina. A guarded hit
# breaks no poise: the behavior script routes it to the guard branch (`DAMAGE_TYPE_GUARD`), which
# plays a guard reaction or nothing, never a stagger. The guard reaction ends the attack like a
# poise break (everything not launched is lost) unless the state is one of the two that go on
# through a guarded hit or the guard table gives no reaction (`Guard`, `continues`). When the stamina
# left (`Strikes.bar`, the pool's median bar, less the stamina the attack's hits have taken by the
# strike's frame, `guard_phases`) is at or under the
# guarded stamina, the guard breaks: the R1 still lands guarded (powerstance-guard.md section 3),
# the attack ends, and the defender lands one more R1 on the guard-broken attacker
# (`PUNISH_HP`, `INFERRED`: the blocker's own R1 is 55 frames off after a guard break and his roll
# 43, `GUARD_BREAK_R1`, long past an R1 chain's next swing). Not modelled: the R1's poise damage
# carried into the next hit (a guarded hit's poise damage is cut by `saGuardCutRate`, 0 on every
# thrusting shield, so it still builds), and the share of the R1 a repel takes (none here: every
# thrusting shield repels at 10, under every attacker's 40 or more).

#: The chip the attacker carries into the defender's strike (`strike_breaks`,
#: `er-mechanics-interrupt.chip_states`): carried, line-fit's default scenario (`Line.scenario`),
#: so the strike and line-fit's windup interrupt read one poise state (`INFERRED`, as there).
STRIKE_CARRIED = True


class Strikes(np.ndarray):
    """`punish_strikes`: the pool's R1 #1 strike frames, one per build, as an array (what the roll's
    punish reads), carrying `dealt`, each one's poise damage in menu units, `saRate` applied
    (`er-mechanics-exchange.attack_profile`), which the defender's strike reads. Frames handed in
    as a plain list (a selftest's) carry none, and the outcome is the roll's alone, as before. An
    array made from one (a sum, a sort) carries none either.

    What an attacker's guard reads of each R1 (`strike_guard`): `prof`, per strike the index of its
    row in `rows`, and `rows`, per pool profile (weapon id, two-handed); `bar`, the stamina the
    attacker starts the attack with. Without them (None) a guard is not read and the strike lands
    as before."""

    def __new__(cls, frames, dealt, prof=None, rows=None, bar=None):
        obj = np.asarray(frames, float).view(cls)
        obj.dealt = np.asarray(dealt, float)
        if obj.dealt.shape != obj.shape:
            raise ValueError(f'Strikes: {obj.dealt.shape} poise values for {obj.shape} strikes')
        obj.prof = None if prof is None else np.asarray(prof, int)
        obj.rows = None if rows is None else tuple(tuple(r) for r in rows)
        obj.bar = None if bar is None else float(bar)
        if obj.prof is not None and obj.prof.shape != obj.shape:
            raise ValueError(f'Strikes: {obj.prof.shape} profile indices for {obj.shape} strikes')
        return obj

    def __array_finalize__(self, obj):
        self.dealt = None
        self.prof = self.rows = self.bar = None

    def __reduce__(self):
        ctor, args, state = super().__reduce__()
        return ctor, args, (state, self.dealt, self.prof, self.rows, self.bar)

    def __setstate__(self, state):
        base, dealt = state[:2]
        super().__setstate__(base)
        self.dealt = dealt
        self.prof, self.rows, self.bar = state[2:] if len(state) == 5 else (None, None, None)


def strike_table(strikes):
    """(frames, poise dealt) of `strikes` sorted by frame, and the memo `strike_breaks` keeps: None
    when `strikes` carries no `dealt` (`Strikes`)."""
    if getattr(strikes, 'dealt', None) is None:
        return None
    tab = getattr(strikes, '_table', None)
    if tab is None:
        o = np.argsort(np.asarray(strikes, float), kind='stable')
        strikes._order = o
        tab = strikes._table = (np.asarray(strikes, float)[o], strikes.dealt[o], {})
    return tab


def guard_of(geo_or_guard):
    """The `Guard` a geometry carries (`GUARD`), or one handed in as a tuple; None for none."""
    g = geo_or_guard.get('GUARD') if isinstance(geo_or_guard, dict) else geo_or_guard
    return Guard(*g) if g else None


_BASE_ATTACK = {}
#: `EquipParamWeapon` base attack per element, the split `strike_guard` prices the guarded share by.
BASE_ATTACK_FIELDS = {'physical': 'attackBasePhysics', 'magic': 'attackBaseMagic', 'fire': 'attackBaseFire',
                      'lightning': 'attackBaseThunder', 'holy': 'attackBaseDark'}


def _base_attack():
    """{weapon id: {element: base attack}} of every `EquipParamWeapon` row, read once."""
    if not _BASE_ATTACK:
        rows, _, _ = PR.rows(PR.param_bytes(PR.load(None), 'EquipParamWeapon'), list(BASE_ATTACK_FIELDS.values()))
        _BASE_ATTACK.update({r['id']: {el: float(r[f]) for el, f in BASE_ATTACK_FIELDS.items()} for r in rows})
    return _BASE_ATTACK


def guard_block(row, guard):
    """One pool R1 (`row`: weapon id, two-handed) into `guard` (`Guard`), by the guard model's
    rules (`er-mechanics-powerstance-guard.block_hit`, PvP, no attacker SpEffect): (the share of
    its raw damage the guard passes, the stamina it takes, the guard reaction). The share prices the
    weapon's base attack split by the R1's motion values (`INFERRED`: the pool names no affinity or
    stats, so its R1 is read as the base weapon's, unscaled). None when the R1 resolves to no
    attack."""
    G, reg = _guard_model()
    wid, two = row
    if wid not in reg.weapon:
        return None
    nums = ATK.attack_numbers(reg, wid, ATK.TWO_HAND_JUDGE_OFFSET if two else 0)
    if not nums:
        return None
    gd = G.shield_guard(reg, guard.weapon, guard.level, guard.judge, guard.two)
    ar = _base_attack().get(wid) or {}
    blk = G.block_hit(reg, dict(nums, weapon=wid), ar, gd, pvp=True)
    raw = sum(ar.get(el, 0.0) * nums[mv] / 100.0 for el, _, _, mv in G.ELEMENT_CUTS)
    return (blk['chip_raw_total'] / raw if raw > 0 else 0.0), blk['stamina_to_blocker'], blk['reaction']


def strike_guard(A, strikes, guard, regen_add=0.0):
    """(stops, saved), (phases, strikes) arrays in `strike_table` order, row k for a strike that
    lands inside phase k of `guard` (`guard_phases`; module comment above `STRIKE_CARRIED`):
    `stops` 1 where it ends the attack (a guard break, or a guard reaction in a state that does not
    go on), else 0; `saved`, the HP the guard spares the attacker against a strike that lands clean
    (`PUNISH_HP` less the guarded chip, less one more `PUNISH_HP` on a guard break). None when
    `guard` is None or `strikes` carries no pool rows. The stamina the attacker has to guard with in
    phase k is `Strikes.bar` less the stamina his hits have taken by then (`guard_phases`, from
    `Guard.charges`; all of `cost` for a guard without them) plus what he regenerates before the
    guard opens (`Guard.regen` frames at his full rate: 45/s plus `regen_add`, his SpEffect
    `staminaRecoverChangeSpeed` sum, `er-mechanics-talismans.stamina_regen_add` of his talismans;
    every caller here passes none, its attacker wearing none). Kept in `strike_table`'s memo per
    guard and add."""
    if guard is None or getattr(strikes, 'rows', None) is None or strike_table(strikes) is None:
        return None
    _, _, memo = strike_table(strikes)
    guard = Guard(*guard)
    key = ('guard', tuple(guard), float(A.PUNISH_HP), float(regen_add))
    if key not in memo:
        per = [guard_block(r, guard) for r in strikes.rows]
        G, _ = _guard_model()
        phases = guard_phases(guard)
        bar = strikes.bar if strikes.bar is not None else math.inf
        back = (G.BASE_STAMINA_REGEN_PER_S + regen_add) * guard.regen / FPS
        stop, save = np.zeros((len(phases), len(per))), np.zeros((len(phases), len(per)))
        for k, (_, charged) in enumerate(phases):
            left = bar - charged + back
            for i, b in enumerate(per):
                if b is None:
                    continue
                share, stamina, reaction = b
                broken = stamina >= left
                stop[k, i] = 1.0 if broken or (not guard.continues and reaction != 'none') else 0.0
                save[k, i] = A.PUNISH_HP - (share * A.PUNISH_HP + (A.PUNISH_HP if broken else 0.0))
        o = strikes._order
        memo[key] = (stop[:, strikes.prof][:, o], save[:, strikes.prof][:, o])
    return memo[key]


def strike_breaks(strikes, windows, poise, carried=STRIKE_CARRIED, guard=None, A=None, regen_add=0.0):
    """(len(windows) + 1, strikes) array: per strike in `strike_table` order, the share of the
    attacker's chip states in which it breaks him, row 0 with no window open and row 1 + w while
    window w of `windows` is (`er-mechanics-interrupt.simulate` on the one hit, the window alone,
    `chip_states` with `carried`). `poise` is his menu poise. Kept per (windows, poise, carried).
    With `guard` (and `A`, the ash module) one more row per phase of the guard (`guard_phases`),
    the last rows: per strike landing inside that phase, whether it stops the attack
    (`strike_guard` `stops`; the guard takes the hit, so no poise rule is read there; `regen_add`
    as `strike_guard` takes it)."""
    s, d, memo = strike_table(strikes)
    rows = strike_guard(A, strikes, guard, regen_add) if guard is not None and A is not None else None
    if rows is not None:
        gkey = (tuple(windows), round(float(poise), 4), bool(carried), 'guard', tuple(guard), float(regen_add))
        if gkey not in memo:
            base = strike_breaks(strikes, windows, poise, carried)
            memo[gkey] = np.concatenate([base, rows[0]], axis=0)
        return memo[gkey]
    key = (tuple(windows), round(float(poise), 4), bool(carried))
    if key not in memo:
        INT = _interrupt()
        n = len(s)
        chip, cw = INT.chip_states(np.full(n, float(poise)), d, carried)
        out = np.zeros((len(windows) + 1, n))
        for w in range(-1, len(windows)):
            seq = {'windows': [list(windows[w])] if w >= 0 else [], 'invuln': []}
            win = INT._pad_windows([seq])[:, 0][:, None, None, :]
            inv = INT._pad_invuln([seq])[:, 0][:, None, None, :]
            t = windows[w][0] if w >= 0 else 0.0
            broken = INT.simulate(np.full((n, 1, 1), float(t)), d[:, None, None], np.ones((n, 1, 1), bool), math.inf,
                                  win, inv, float(poise), chip, 0.0)
            out[w + 1] = (broken * cw).sum(axis=1)
        memo[key] = out
    return memo[key]


def strike_window(windows, t, guard=()):
    """The window of `windows` open at real frames `t` (an array), -1 where none is: the first in
    their order that covers it, as `er-mechanics-interrupt.simulate` picks one. Where one of `guard`
    (the attacker's guard windows: (start, end) pairs, or `guard_phase_windows`' (start, end, phase)
    triples) covers it, `len(windows)` + its phase instead: the guard takes the hit, whatever TAE
    795 window is open (`strike_breaks`' guard rows)."""
    t = np.asarray(t, float)
    if not windows:
        out = np.full(t.shape, -1, int)
    else:
        ws = np.array([w[0] for w in windows], float)
        we = np.array([w[1] for w in windows], float)
        cover = (ws <= t[..., None]) & (t[..., None] < we)
        out = np.where(cover.any(-1), np.argmax(cover, -1), -1)
    if guard:
        gs = np.array([w[0] for w in guard], float)
        ge = np.array([w[1] for w in guard], float)
        gk = np.array([w[2] if len(w) > 2 else 0 for w in guard], int)
        cover = (gs <= t[..., None]) & (t[..., None] < ge)
        out = np.where(cover.any(-1), len(windows) + gk[np.argmax(cover, -1)], out)
    return out


#: `strike_segments`' runs, per (strike frames, cuts, windows): the frames as a list and {frame the
#: strikes start from: `_strike_runs`}. They read nothing else, so an entry serves every key, call
#: and form on those three (an attack that launches no shot and has no TAE 795 window has neither,
#: so one entry serves every such attack a process scores). The `STRIKE_RUNS_CACHE` entries used
#: last are kept. The frames the strikes start from are the situations' (`net_parts`' keys), so an
#: entry holds at most a few thousand.
_STRIKE_RUNS = {}
STRIKE_RUNS_CACHE = 16


def strike_segments(A, strikes, dist, floor, touch, my_roll, cuts, windows, guard=()):
    """The strikes of the defender at (`dist`, `floor`) that land (module comment above), as runs:
    [(weight of one strike of the run, first, end, cut, window)], `first` .. `end` - 1 in
    `strike_table` order, every strike of a run before the same number `cut` of `cuts` (sorted
    launches) and in the same window of `windows` (-1: none; `len(windows)` + its phase: inside one
    of the attacker's `guard` windows, `strike_window`). Each reaction quantile
    (`er-mechanics-ashes.reaction_delays`) is a run set of its own; a strike lands when it comes
    before `touch` (the attack's first touch on him) and `my_roll`. Reads no worth and no poise.
    A quantile's runs are those of every strike pressed from its start (its reaction or his
    recovery floor, whichever is later, plus his run in), cut where its strikes stop landing: kept
    per start (`_STRIKE_RUNS`), which the quantiles his floor outlasts share, and the keys up close
    share across distances."""
    s = strike_table(strikes)[0]
    close = max(0.0, dist - A.PUNISH_REACH_M) * A._neutral().frames_per_metre()
    late = min(touch, my_roll if my_roll is not None else math.inf)
    frames, memo = _strike_memo(s, cuts, windows, guard)
    out = []
    for delay, w in A.reaction_delays():
        base = max(delay, floor) + close
        x = late - base
        # The count `np.searchsorted(s, x, side='left')` gives (numpy orders a NaN last: a NaN
        # bound is left to it).
        b = bisect.bisect_left(frames, x) if x == x else int(np.searchsorted(s, x, side='left'))
        if b <= 0:
            continue
        got = memo.get(base)
        if got is None or got[0] < b:
            got = memo[base] = _strike_runs(s, base, min(len(s), max(b, 2 * got[0])) if got else b, cuts, windows,
                                            guard)
        _, firsts, runs = got
        # The runs that start before strike b, the last one ending there.
        k = bisect.bisect_left(firsts, b)
        out += [(w, a, e, ci, wi) for a, e, ci, wi in runs[:k - 1]]
        a, _, ci, wi = runs[k - 1]
        out.append((w, a, b, ci, wi))
    return out


def _strike_memo(s, cuts, windows, guard=()):
    """(`s` as a list, {start frame: `_strike_runs`}) for strike frames `s` (`strike_table`'s), `cuts`,
    `windows` and `guard` windows (`_STRIKE_RUNS`; the entry holds `s`, so its id names it while it
    is kept)."""
    key = (id(s), tuple(cuts), tuple(tuple(w) for w in windows)) + ((tuple(tuple(w) for w in guard),) if guard else ())
    got = _STRIKE_RUNS.pop(key, None)
    if got is None or got[0] is not s:
        while len(_STRIKE_RUNS) >= STRIKE_RUNS_CACHE:
            _STRIKE_RUNS.pop(next(iter(_STRIKE_RUNS)))
        got = (s, s.tolist(), {})
    # Last in the order: the entry used last.
    _STRIKE_RUNS[key] = got
    return got[1], got[2]


def _strike_runs(s, base, upto, cuts, windows, guard=()):
    """`strike_segments`' runs of the first `upto` strikes of `s` (sorted frames) pressed from
    `base`: (`upto`, [first of each run], [(first, end, cut, window)]), a run's strikes before the
    same cut and in the same window (`strike_window`, `guard` read), the last run ending at `upto`.
    Each strike's cut and window read its own frame alone, so a quantile whose last strike that
    lands comes before `upto` has these runs, cut there."""
    t = base + s[:upto]
    ci = np.searchsorted(np.asarray(cuts, float), t, side='left')
    wi = strike_window(windows, t, guard)
    phases = 1 + max((w[2] if len(w) > 2 else 0 for w in guard), default=0)
    code = ci * (len(windows) + phases + 1) + wi + 1
    edges = (np.flatnonzero(np.diff(code)) + 1).tolist()
    firsts = [0] + edges
    return upto, firsts, [(a, e, int(ci[a]), int(wi[a])) for a, e in zip(firsts, edges + [upto])]


def strike_cum(beta):
    """`beta` (`strike_breaks`) summed along each row from its start, a 0 in front, as nested lists:
    what `strike_shares` reads (Python floats, the same numbers as numpy's)."""
    return np.concatenate([np.zeros((beta.shape[0], 1)), np.cumsum(beta, axis=1)], axis=1).tolist()


def strike_saved(A, strikes, guard, regen_add=0.0):
    """`strike_guard`'s `saved` summed from its start, a 0 in front, one list per phase of the
    guard (what `strike_shares` reads, as `strike_cum` gives `beta`'s rows), or None when no guard
    is read."""
    rows = strike_guard(A, strikes, guard, regen_add)
    return None if rows is None else [[0.0] + np.cumsum(r).tolist() for r in rows[1]]


def strike_shares(strikes, segs, beta, ncuts, cum=None, saved=None):
    """(share of strikes that land, [per cut: share that lands and breaks him], HP the attacker's
    guard spares him) of `segs` (`strike_segments`) through `beta` (`strike_breaks`), what
    `strike_outcome` reads of them; `cum` is `strike_cum(beta)` when the caller keeps it. `saved`
    (`strike_saved`, one list per guard phase) is read on the runs inside the guard (`beta`'s last
    rows, one per phase); without it the HP is 0.0 and the first two are as before."""
    n = len(strike_table(strikes)[0])
    cum = strike_cum(beta) if cum is None else cum
    land = 0.0
    broken = [0.0] * (ncuts + 1)
    spared = 0.0
    gw = len(cum) - 1 - len(saved) if saved is not None else None
    for w, a, e, ci, wi in segs:
        land += w * (e - a) / n
        row = cum[wi + 1]
        broken[ci] += w * (row[e] - row[a]) / n
        if gw is not None and wi >= gw:
            sv = saved[wi - gw]
            spared += w * (sv[e] - sv[a]) / n
    return land, broken, spared


def strike_outcome(A, strikes, segs, beta, kept, base, ncuts, shares=None, saved=None):
    """The outcome dict of the defender's strike (`net_parts`' shape): over every reaction quantile
    and pool build equally, a strike that lands costs the attacker `PUNISH_HP`; where it breaks him
    (`beta`, `strike_breaks`) what lands is `kept(cut)`, the attack stopped before launch `cut`, and
    everywhere else `base`, the attack whole on him standing. `p_evade` is 0: he never dodges.
    `shares` is `strike_shares` of `segs` when the caller keeps it (it reads no worth). A strike
    inside the attacker's guard (`saved`, `strike_saved`, with `beta` from `strike_breaks` with that
    guard) costs him its guarded HP instead, and stops the attack where `beta`'s guard row says;
    `p_punish` still counts it as a strike that lands."""
    land, broken, spared = strike_shares(strikes, segs, beta, ncuts, saved=saved) if shares is None else shares
    value = base * (1.0 - sum(broken))
    for ci, b in enumerate(broken):
        if b > 0:
            value += b * kept(ci)
    return {'value': value, 'base': base, 'p_evade': 0.0, 'p_punish': land,
            'punish_hp': land * A.PUNISH_HP - spared, 'strike': True}


def _launch(g):
    """The real frame geometry `g` leaves the attacker, past which an interrupt stops none of it:
    its shot's launch (`TL`), else the first sample of a bullet (its `PK` all minus infinity,
    `er-mechanics-ashes.contact_geometry`); a melee window never leaves him (inf): it is his own
    hitbox and goes with his attack."""
    if 'TL' in g:
        return float(np.min(g['TL']))
    if not np.isfinite(np.asarray(g['PK'], float)).any():
        return float(np.min(g['T']))
    return math.inf


class StrikeReach:
    """What the defender's strike reads of an attack given as [(geometry, frame)] (`net_parts`,
    attack-similarity's forms): its geometries in `_bursts` order, each with its burst, frame and
    launch (`_launch`); `cuts`, the finite launches in order, between which a strike can stop it;
    `windows`, its TAE 795 windows (`TOUGHNESS`, the same on every geometry); `guard`, the attacker's
    own guard over the attack (`GUARD`, `Guard`, the same on every geometry; None: none) and
    `guard_windows` its windows cut into stamina phases (`guard_phase_windows`; () for none); the
    first touch on a defender standing (`touch`), and what lands when it is stopped (`kept`)."""

    def __init__(self, A, geo):
        order = sorted(geo, key=lambda e: e[0]['T'].min())
        self.A = A
        self.geoms = [g for g, _ in order]
        self.frames = [fr for _, fr in order]
        # Bursts as `_bursts` groups them: overlapping live frames.
        self.burst, end, b = [], None, -1
        for g in self.geoms:
            if end is not None and g['T'].min() <= end:
                end = max(end, g['T'].max())
            else:
                b, end = b + 1, g['T'].max()
            self.burst.append(b)
        self.launch = [_launch(g) for g in self.geoms]
        self.cuts = sorted({x for x in self.launch if math.isfinite(x)})
        self.windows = next((tuple(g['TOUGHNESS']) for g in self.geoms if g.get('TOUGHNESS')), ())
        self.guard = next((guard_of(g) for g in self.geoms if g.get('GUARD')), None)
        self.guard_windows = guard_phase_windows(self.guard)
        self._tau, self._touch = {}, {}

    def tau(self, dist):
        """Per geometry, the real frame it first touches a defender `dist` m ahead who stands (inf:
        never), `_catches` on it alone."""
        d = round(dist, 2)
        if d not in self._tau:
            self._tau[d] = [float(self.A._catches([g], d, None, [0.0])[1][0]) for g in self.geoms]
        return self._tau[d]

    def touch(self, dist):
        """The attack's first touch on him standing at `dist`, or at the end of his run in to
        `PUNISH_REACH_M` when he starts further out, whichever is sooner (`INFERRED`: the two ends
        of the run bracket where it meets him). Kept per distance."""
        if dist not in self._touch:
            near = round(min(dist, self.A.PUNISH_REACH_M), 2)
            self._touch[dist] = min(min(self.tau(dist), default=math.inf), min(self.tau(near), default=math.inf))
        return self._touch[dist]

    def kept(self, dist, cut, value, price, hp=None):
        """What lands on him standing at `dist` from the geometries launched by `cuts[cut - 1]`
        (`cut` None: all of them, nothing stopped): the first burst one of them touches, its first
        kept geometry there to touch him priced by `price(index, value kept)` (the hits of every
        geometry stopped left out of `value`), capped at `hp`, as `sequence_outcome` takes the base."""
        lim = math.inf if cut is None else (self.cuts[cut - 1] if cut > 0 else -math.inf)
        keep = [cut is None or L <= lim for L in self.launch]
        lost = {fr for fr, k in zip(self.frames, keep) if not k}
        vk = {f: v for f, v in value.items() if f not in lost} if lost else value
        tau = self.tau(dist)
        for b in sorted(set(self.burst)):
            hs = [h for h in range(len(self.geoms)) if self.burst[h] == b and keep[h] and math.isfinite(tau[h])]
            if not hs:
                continue
            h = min(hs, key=lambda i: (tau[i], i))
            w = price(h, vk)
            w = min(w, hp) if hp is not None else w
            if w > 0:
                return w
        return 0.0

    def raw_price(self, h, value):
        """The old rule's worth of geometry `h` catching first: its frame's hits and every later
        frame's (`_bursts`)."""
        fr = self.frames[h]
        return sum(v for f, v in value.items() if f >= fr)


def net_parts(A, geo, value, situations, my_roll, strikes=None, value_one=None, memo=True, defender=None,
              look=None, attacker_poise=None, attacker_regen=0.0):
    """`net_values` with its parts, per situation: `net` (what `net_values` returns), the chosen
    option's `value`, `base`, `p_evade`, `p_punish`, `punish_hp` and `gap` (the run-in gap it was
    pressed at, None for at once), `now` (the same for pressing at once at the situation's own
    distance and floor, plus `t_first`: the frame the attack first touches a defender who does not
    dodge, inf when it never does) and `net_one` (the same choice on `value_one`, {frame: one hit's
    worth}, None without it). With `memo` one `_catches` memo serves every outcome of the call
    (`er-mechanics-ashes.reaction_outcome` `catches`: the geometries are the same at every
    distance and floor, and the reads depend on nothing else, `look` included); a dict is that memo,
    kept by the caller for another call on the same geometries; without it each read is made again,
    which gives the same numbers. `geo` may instead be a `CastThreats`, whose geometry depends on the
    distance: `value` and `value_one` are then unused and `net_one` is None. `defender` as
    `net_values`' (None: the old rule and no cap).

    `look` (a `Lookalikes` member) is the attacks the defender cannot tell this one from: his
    first dodge is timed by its plan at each distance (`er-mechanics-ashes.lookalike_plan`). None
    is the defender who knows the attack from its first frame, as before.

    With `strikes` from `punish_strikes` (`Strikes`) he may strike instead of rolling (the module
    comment above `STRIKE_CARRIED`), at each (distance, floor) whichever leaves the attacker less,
    a tie to the roll; each part's `strike` says so (`p_evade` 0, `p_punish` the share of strikes
    that land). `attacker_poise` is the attacker's menu poise (None: `defender`'s, the RL window's
    median build); a plain list of strike frames, or no poise at all, leaves the roll alone.
    `attacker_regen` is the attacker's SpEffect `staminaRecoverChangeSpeed` sum, which his own guard
    regenerates with (`strike_guard`; 0: no talismans, as every caller's attacker today)."""
    threats = geo if isinstance(geo, CastThreats) else None
    bursts = _bursts(geo, value) if threats is None else None
    bursts1 = _bursts(geo, value_one) if value_one is not None and threats is None else None
    hp = (defender or {}).get('hp')
    poise = (defender or {}).get('poise')
    caught = A.CaughtModel.of(geo, poise, value) if threats is None else None
    caught1 = caught.with_value(value_one) if caught is not None and value_one is not None else None
    per_m = A._neutral().frames_per_metre()
    cat = memo if isinstance(memo, dict) else ({} if memo else None)
    memos = ({}, {})
    # The defender's strike (module comment above `STRIKE_CARRIED`): on when `strikes` carries the
    # R1s' poise damage (`Strikes`) and a poise is known, the attacker's (`attacker_poise`) or else
    # the RL window's median build's, the defender's own (`INFERRED`: one corpus for both).
    apoise = attacker_poise if attacker_poise is not None else poise
    strike_on = strikes is not None and strike_table(strikes) is not None and apoise is not None
    reach = StrikeReach(A, geo) if strike_on and threats is None else None
    windows = (reach.windows if reach is not None else tuple(threats.windows)) if strike_on else ()
    # The attacker's own guard over the attack (module comment above `STRIKE_CARRIED`): a cast has none.
    guard = reach.guard if reach is not None else None
    gwin = reach.guard_windows if reach is not None else ()
    beta = strike_breaks(strikes, windows, apoise, guard=guard, A=A, regen_add=attacker_regen) if strike_on else None
    cum = strike_cum(beta) if strike_on else None
    saved = strike_saved(A, strikes, guard, attacker_regen) if strike_on else None
    # Per key the strikes that land and their shares (they read no worth: both `which` share them);
    # per distance a cast's `strike_reach`; per (which, distance, cut) what lands (`kept`, which
    # reads no floor).
    segs, reached, held = {}, {}, {}

    def strike_at(key, which):
        """The strike's outcome at `key` ((dist, floor) rounded as `at` rounds them)."""
        dist, floor = key
        if threats is not None:
            if dist not in reached:
                reached[dist] = threats.strike_reach(A, dist, hp, poise)
            cuts, touch, keep, _ = reached[dist]
        else:
            cuts = reach.cuts

        def kept(c):
            k = (which, dist, c)
            if k not in held:
                if threats is not None:
                    held[k] = keep(c)
                else:
                    v, cm = (value_one, caught1) if which else (value, caught)
                    price = (lambda h, vk: cm.worth_of(dist, h, vk)) if cm is not None else reach.raw_price
                    held[k] = reach.kept(dist, c, v, price, hp)
            return held[k]
        if key not in segs:
            if threats is None:
                touch = reach.touch(dist)
            got = strike_segments(A, strikes, dist, floor, touch, my_roll, cuts, windows,
                                  gwin if saved is not None else ())
            segs[key] = (got, strike_shares(strikes, got, beta, len(cuts), cum, saved))
        got, shares = segs[key]
        return strike_outcome(A, strikes, got, beta, kept, kept(None), len(cuts), shares)

    def at(dist, floor, which=0):
        key = (round(dist, 2), round(floor * 2) / 2)
        if key not in memos[which]:
            plan = look.plan(key[0]) if look is not None else None
            if threats is not None:
                o = threats.outcome(A, key[0], key[1], my_roll, strikes, cat, hp=hp, poise=poise, plan=plan)
            else:
                o = A.sequence_outcome(bursts1 if which else bursts, key[0], floor=key[1], my_roll=my_roll,
                                       strikes=strikes, run=True, catches=cat, caught=caught1 if which else caught,
                                       hp=hp, plan=plan)
            if strike_on:
                # Knowing the attack, he strikes or rolls (or jumps), whichever leaves the attacker
                # less; a tie keeps the roll.
                s = strike_at(key, which)
                if s['value'] - s['punish_hp'] < o['value'] - o['punish_hp']:
                    o = s
            memos[which][key] = o
        return memos[which][key]

    def net(o):
        return o['value'] - o['punish_hp']

    def first_touch(dist):
        d = round(dist, 2)
        if threats is not None:
            return threats.first_touch(A, d)
        best = math.inf
        for bi, (gs, _) in enumerate(bursts):
            if cat is not None and (0, bi) in cat and ('base', d) in cat[(0, bi)]:
                t = cat[(0, bi)][('base', d)][1][0]
            else:
                t = A._catches(gs, d, None, [0.0])[1][0]
            best = min(best, float(t))
        return best

    def choose(dist, floor, which):
        now = at(dist, floor, which)
        best, gap, v = now, None, net(now)
        for g in APPROACH_GAPS_M:
            if g < dist:
                o = at(g, max(0.0, floor - (dist - g) * per_m), which)
                if net(o) > v:
                    best, gap, v = o, g, net(o)
        return now, best, gap, v

    out = []
    for dist, floor in situations:
        now, best, gap, v = choose(dist, floor, 0)
        one = choose(dist, floor, 1)[3] if bursts1 is not None else None
        out.append({'net': v, 'value': best['value'], 'base': best['base'], 'p_evade': best['p_evade'],
                    'p_punish': best['p_punish'], 'punish_hp': best['punish_hp'], 'gap': gap,
                    'strike': bool(best.get('strike')),
                    'now': {'net': net(now), 'value': now['value'], 'base': now['base'],
                            'p_evade': now['p_evade'], 'p_punish': now['p_punish'], 't_first': first_touch(dist),
                            'strike': bool(now.get('strike'))},
                    'net_one': one})
    return out


def parse_weapon(text):
    """'Name[+level][:Affinity][@Skill]' -> `skill_events` keywords (`skill` still a name)."""
    m = re.match(r'^\s*([^+:@]+?)\s*(?:\+\s*(\d+))?\s*(?::\s*([^@]+?))?\s*(?:@\s*(.+?))?\s*$', text)
    if not m:
        raise SystemExit(f'cannot read weapon {text!r}')
    return {'weapon': m.group(1), 'level': int(m.group(2)) if m.group(2) else None,
            'affinity': m.group(3) or 'Standard', 'skill': m.group(4)}


def weapon_label(w):
    return (f"{w['weapon']}{'' if w.get('level') is None else f' +{w['level']}'}"
            f"{'' if w.get('affinity', 'Standard') == 'Standard' else ' ' + w['affinity']}")


_STRIKES = {}


def punish_strikes(rl):
    """The RL window pool's R1 #1 strike frames, one per build: the punish a defender who rolled a
    cast throws (ashes-of-war.md section 16a, as `er-builds-pvp` passes `strikes`), and the strike
    a free defender throws instead of rolling (`Strikes`: each carries its poise damage, the
    exchange pool's `poise_dealt`, menu units with `saRate`: 24 to 819 at RL 140-160, median 222,
    `MEASURED` 2026-11-13 over its 890 builds), and what an attacker's guard reads of each (`rows`:
    the pool profile's weapon, its base id by name, and grip; `bar`: the pool's median stamina bar,
    the attacker's at the start of his attack, `INFERRED`: one corpus for both sides)."""
    if rl not in _STRIKES:
        EX = _mod('er_mechanics_exchange', 'er-mechanics-exchange.py')
        pool = EX.Pool(EX.opponent_pool(ATK.Regulation(None), EX.CACHE / 'builds.jsonl', rl - WINDOW, rl + WINDOW))
        ids = EX.weapon_ids()
        rows = [(ids.get(k.rsplit('|', 1)[0], -1), k.rsplit('|', 1)[1] == '2h') for k in pool.keys]
        _STRIKES[rl] = Strikes(pool.startup[pool.build_prof], pool.poise_dealt[pool.build_prof],
                               pool.build_prof, rows, pool.bar)
    return _STRIKES[rl]


def parse_stats(text):
    out = {}
    for part in filter(None, (text or '').split(',')):
        k, v = part.split('=')
        out[k.strip().lower()] = int(v)
    return out


def selftest():
    m = SpellModel()
    n = [0]

    def check(name, ok):
        n[0] += 1
        if not ok:
            sys.exit(f'FAIL {name}')

    # The branch argument: the weapon branch would put no element on a spell.
    cats = [w for w in m.ar.weapons.values() if w.get('enableMagic') or w.get('enableMiracle')]
    check('catalysts exist', len(cats) > 20)
    check('no catalyst has a fire or lightning attackBase', all(
        w[f] == 0 for w in cats for f in ('attackBaseFire', 'attackBaseThunder')))
    reached, add_base = set(), set()
    for mid in m.names:
        for i in range(1, 11):
            for atk_id, _, _ in m.ref_hits(mid, i):
                reached.add(atk_id)
                if m.atk[atk_id]['isAddBaseAtk']:
                    add_base.add(m.names[mid])
    check('spells reach hundreds of damaging AtkParams', len(reached) > 200)
    check('isAddBaseAtk only on two spells', add_base == {'Furious Blade of Ansbach', 'Scarlet Aeonia'})
    check('fire and lightning spell hits exist', any(m.atk[a]['atkFire'] > 0 for a in reached)
          and any(m.atk[a]['atkThun'] > 0 for a in reached))
    # The multiplier is the spell buff attack-rating.md section 4 checked against `COMMUNITY`.
    staff = m.catalyst('Glintstone Staff+25')
    check('Glintstone Staff +25 INT 80 is 329.5', round(100 * m.multiplier(staff, 'magic', {'int': 80}), 2) == 329.5)
    seal = m.catalyst('Finger Seal+25')
    check('Finger Seal +25 FTH 60 is 286.75', round(100 * m.multiplier(seal, 'holy', {'fth': 60}), 2) == 286.75)
    check('unmet catalyst requirement is 0.6', round(m.multiplier(staff, 'magic', {'int': 5}), 4) == 0.6)
    pebble = m.by_name['glintstone pebble']
    h = m.hit(40000, 10400000, staff, {'int': 80}, {e: 1.0 for e in ATK_FIELD}, {e: 1.0 for e in ATK_FIELD},
              m.defender(150))
    check('Pebble AR is 152 x 3.295', h['ar'] == {'magic': round(152 * 3.295, 2)})
    # Clip roles, from the data.
    spear = m.by_name['lightning spear']
    check('Lightning Spear is chargeable', m.chargeable(spear) and [v for v, _ in m.variants(spear)] == ['tap', 'charged'])
    top = {lab: max(m.atk[a]['atkThun'] for _, ref in m.release_events(spear, anim)[1]
                    for a, _, _ in m.ref_hits(spear, ref)) for lab, anim in m.variants(spear)}
    check('charged spear hits harder than the tap', top == {'tap': 234, 'charged': 293})
    sling = m.by_name['rock sling']
    check('Rock Sling fires refs 1, 2, 3', [r for _, r in m.release_events(sling, RELEASE)[1]] == [1, 2, 3])
    check('Pebble is one cast', [v for v, _ in m.variants(pebble)] == ['cast'])
    slicer = m.by_name['carian slicer']
    check('Carian Slicer hits an AtkParam directly', m.ref_hits(slicer, 1) == [(44400, None, 1.0)])
    # A whole cast, end to end.
    got = m.evaluate(pebble, staff, {'int': 80, 'dex': 10}, defender=m.defender(150))
    check('Pebble: one variant, one hit, 7 FP', len(got) == 1 and len(got[0]['hits']) == 1 and got[0]['fp'] == 7)
    check('Pebble: one_hit == full', got[0]['one_hit'] == got[0]['full'] > 0)
    arc = m.by_name['glintstone arc']
    check('Glintstone Arc: eight bullets, one shared list, one hit', m.ref_hits(arc, 1) == [(40070, 10407000, 1.0)])
    comet = m.by_name['comet']
    check('charged Comet passes through once', [c for _, _, c in m.ref_hits(comet, 2)] == [1.0])
    still = dict(m.bullet[10403001])
    check('a standing bullet touches for its life', m.contact(still) == still['life'] and still['initVellocity'] == 0)
    # The kit: the greedy, the flight, and the recovery that holds the roll back.
    rounds = greedy_kit({'a': [10, 0], 'b': [0, 8], 'c': [5, 5]}, [1, 1], 2, {'a': 1, 'b': 1, 'c': 1}, 3)
    check('greedy: the second pick covers what the first misses, then it stops',
          [r['id'] for r in rounds] == ['a', 'b'] and rounds[-1]['value'] == 9.0)
    check('greedy: the slots cap the kit',
          [r['id'] for r in greedy_kit({'a': [10, 0], 'b': [0, 8]}, [1, 1], 2, {'a': 1, 'b': 2}, 2)] == ['a'])
    check('Carian Slicer has no flight', m.geometry(slicer, {'events': [(0.0, 1)], 'hand': 0.0}) is None)
    geo = m.geometry(pebble, {'events': m.release_events(pebble, RELEASE)[1], 'hand': 20.0})
    check('Pebble flies', geo is not None and len(geo) == 1)
    A, _ = m.ash()
    geoms = [g for g, _ in geo]
    free = A.reaction_outcome(geoms, [100.0], 6.0)
    held = A.reaction_outcome(geoms, [100.0], 6.0, floor=float(geoms[0]['T'].max()) + 1.0)
    check('a defender locked past the flight takes the cast', round(held['value'], 6) == held['base'] == 100.0)
    check('a free defender rolls some of it away', free['value'] < held['value'])
    check('Pebble: the roll cancel comes after the release', got[0]['roll'] > got[0]['startup'])
    late = A.reaction_outcome(geoms, [100.0], 6.0, my_roll=300.0, strikes=[15.0], run=True)
    soon = A.reaction_outcome(geoms, [100.0], 6.0, my_roll=geoms[0]['T'].min(), strikes=[15.0], run=True)
    check('a rolled cast is punished from 6 m when the caster rolls late, never when he rolls first',
          round(late['p_punish'], 6) == round(late['p_evade'], 6) > 0 and soon['p_punish'] == 0.0)
    moons = m.by_name["rellana's twin moons"]
    check("Rellana's Twin Moons is the only spell a jump is immune to",
          {m.names[s] for s in m.names for i in range(1, 11) for a, _, _ in m.ref_hits(s, i)
           if (m.atk.get(a) or {}).get('isInvalidatedByNoDamageInAir')} == {"Rellana's Twin Moons"})
    mg = m.geometry(moons, {'events': m.release_events(moons, RELEASE)[1], 'hand': 50.0})
    check('its three moons fly as three jumpable events', mg is not None and len(mg) == 3
          and all(g.get('AIR') for g, _ in mg))
    jumped = m.punish_values(moons, staff, {'int': 80, 'dex': 10}, [(4.0, 14.0)], defender=m.defender(150),
                             strikes=[15.0])
    check('jumped moon by moon, the cast lands nothing: at best it is not punished',
          jumped is not None and -A.PUNISH_HP - 1e-6 <= jumped[0][0] <= 0.0)
    # Rivals: the skills of the weapons the build carries.
    check('a spell that only matches what the carried weapons already land adds nothing',
          greedy_kit({'a': [5.0, 5.0], 'b': [9.0, 0.0]}, [1, 1], 2, {'a': 1, 'b': 1}, 3, base=[5.0, 5.0])
          == [{'id': 'b', 'name': 'b', 'variant': None, 'slots': 1, 'value': 7.0, 'gain': 2.0, 'best_share': 0.5}])
    check("weapon spec 'Name+level:Affinity@Skill'",
          parse_weapon('Zamor Curved Sword+10:Standard@Zamor Ice Storm')
          == {'weapon': 'Zamor Curved Sword', 'level': 10, 'affinity': 'Standard', 'skill': 'Zamor Ice Storm'})
    check("the Zamor Curved Sword storm (bullet 3055): the burst's one hit of AtkParam 303401600 and the "
          "storm 3057's four ticks of 303401601 in a record of its own (3055's attachEffectType 1 hands "
          "its record to no child; until 2026-11-12 one list of four ticks)",
          m.bullet_hits(3055) == [(303401600, 3055, 1.0), (303401601, 3057, 4.0)])
    check('a nail flown as expiry-made segments hits once: Glintstone Nail 1, Glintstone Nails 6 (numShoot)',
          (m.bullet_hits(210450000), m.bullet_hits(210451000))
          == ([(210450000, 210450000, 1.0)], [(210451000, 210451000, 6.0)]))
    # A stationary timer chain drops one star per timer (`er-mechanics-ashes.interval_frames`), the
    # count each spell's GoodsInfo text gives. A star's first leg has launchConditionType 4, so on a
    # hit (where the gate reads the hitting leg's own value, bd er-effects-rs-rggx) it makes no
    # second leg; the second leg (0) comes only when the first expires, so it only continues a star
    # that missed: one list a star.
    legs = {r['intervalCreateBulletId'] for r in m.bullet.values() if r['intervalCreateBulletId'] > 0}
    star_lists = {nm: m.bullet_lists(rid) for nm, rid in (('Stars of Ruin', 10422000), ('Glintstone Stars', 10404000),
                                                          ('Star Shower', 10405000))}
    check('one star per timer, one list of one hit each: Stars of Ruin 12 stars (its last 12 links down) and 12 '
          'hits, Glintstone Stars 3 and 3, Star Shower 6 and 6 (were 24, 6 and 12 while the second leg was '
          "gated by its own launchConditionType 0 on a hit; 45, 12 and 24 before that)",
          {nm: (sum(li['mult'] for li in ls if li['bullet'] in legs), sum(li['mult'] * li['ticks'] for li in ls),
                {li['mult'] for li in ls}) for nm, ls in star_lists.items()}
          == {'Stars of Ruin': (12, 12, {1}), 'Glintstone Stars': (3, 3, {1}), 'Star Shower': (6, 6, {1})})
    check('the gate on a hit reads the hitting bullet: Stars of Ruin first leg 10422017 (launchConditionType 4) makes '
          'its second leg 10422032 on expiry only, a 0 under it would make it on a hit too',
          (A.launches(m.ash()[1], 10422017, 10422032, 'hit'), A.launches(m.ash()[1], 10422017, 10422032, 'expire'),
           A.launches(m.ash()[1], 10422032, 10422017, 'hit'))
          == (False, True, True))
    # The DmgHitRecord lineage (bd er-effects-rs-7bye): a child joins its parent's record whatever its
    # own flag, a child with the flag under a parent that has none starts a record of its own, and a
    # parent whose row has `attachEffectType` hands none down.
    check("hit lists follow the record lineage: Honed Bolt one list of one hit (was two: the unflagged "
          "10693004 under the recorded 10693003 kept its own); Scarlet Aeonia's 10724000 the root's hit and "
          "its flower 10724001's own record, 7 landed hits of 0.7 s over its 4.2 s (was one list of one hit: the "
          "flower joined the root's, whose record lifetime is 0); Zamor Ice Storm's 10441000 the root's hit and "
          "10441002's 5 (the root's attachEffectType 1 hands its record to no child; was one list of 5)",
          tuple([(li['atk'], li['bullet'], li['mult'], li['ticks']) for li in m.bullet_lists(rid)]
                for rid in (10693000, 10724000, 10441000))
          == ([(69300, 10693002, 1, 1)], [(72401, 10724000, 1, 1), (72402, 10724001, 1, 7)],
              [(44100, 10441000, 1, 1), (44101, 10441002, 1, 5)]))
    # A record's hits are its members' landed touches, each flown against him from its own spawn,
    # one per record lifetime (`record_hits`); a member that never reaches him adds none.
    es, ss = m.record_hits(10672000), m.record_hits(10403000)
    # Round 4: the stars (10672001, shootAngleXMaxRandom 90, shootAngleYMaxRandom 360) are flown as
    # their draws spread them (`er-mechanics-ashes.draw_quantile`), not all at the nominal heading
    # (34 then), and the chain's interval timers start from their drawn first intervals.
    check("a record's hits are its members' touches flown at him, one per lifetime: Elden Stars' 0.15 s record "
          "lands 27 on a defender 20 m out (4 by its longest member's touch, 66 with every star touching at its "
          "spawn, 34 with every star at its nominal heading), Shard Spiral's 0.3 s record 3 (its trail shards "
          "touch only where he stands, not 15); the stars, whose own rows carry 0, take the root's 0.15 s down "
          "the lineage",
          (es['d'], es['records'][10672000], ss['records'][10403000][1],
           record_lifetime(record_lifetime(0.0, m.bullet[10672000]), m.bullet[10672001]))
          == (20.0, (es['records'][10672000][0], [27]), [3], m.bullet[10672000]['dmgHitRecordLifeTime']))
    # The flight gaps closed (bd er-effects-rs-7bye, round 3): every record is flown in 3D, none
    # keeps the formula. Founding Rain's cloud levels (sfxPostureType 1) and rains on him; the aimed
    # meteor (Meteorite ref 2) comes down at him once its marker hands it the caster's frame
    # (isOwnerOverrideInitAngle), the unaimed one (ref 1) meets him off the axis; Light of Miquella's
    # 999 m/s seeker stops on him (swept contact, AtkParam_Pc 0 targets opponents) and its pillar
    # lands under him; Discus of Light returns through him (FollowType 5); Death Lightning strikes
    # its random ground point (EmittePosType 1); Crystal Release's shards fall from the caster's
    # frame.
    rec = {rid: m.record_hits(rid) for rid in (10421007, 10470005, 10470000, 210670000, 10670000, 10504000,
                                                10451010)}
    lom6 = m._fly_records(210670000, m._damaging, 6.0)
    # Round 4 (the random draws flown as their expected layout): Founding Rain's 17 clouds are laid
    # around the full turn by their shootAngleYMaxRandom 360 rather than on one point, so their stars
    # fall on a ring about the caster; the best spot is 1 m out and still lands 9 (it was 9 at 6 m).
    # Meteorite ref 1's drawn life (lifeRandomRange) moves its best spot off the axis to 10 m.
    check('flight gaps closed: Founding Rain ref 3 lands 9 at 1 m; Meteorite ref 2 1 on the axis, ref 1 1 off it '
          '(10 m, -30 deg); Light of Miquella 7 at 10 m and 1 at 6 m (was 5, the seeker overshooting); '
          'Discus of Light 2 (out and back); Death Lightning 1; Crystal Release ref 2 7 at 2 m',
          ((rec[10421007]['d'], rec[10421007]['records'][10421007][1]),
           (rec[10470005]['records'][10470005][1], rec[10470005]['bearing']),
           (rec[10470000]['records'][10470000][1], rec[10470000]['bearing']),
           (rec[210670000]['d'], rec[210670000]['records'][210670000][1], lom6[210670000][1]),
           rec[10670000]['records'][10670000][1], rec[10504000]['records'][10504000][1],
           (rec[10451010]['d'], rec[10451010]['records'][10451010][1]))
          == ((1.0, [9]), ([1], {}), ([1], {10470000: (10.0, -30)}), (10.0, [7], [1]), [2], [1], (2.0, [7])))
    # Round 4: the punish geometry flies the record flight's bullets (`er-mechanics-ashes.fly_members`).
    # A carrier (no damage of its own, targets opponents, stops on a character) dies on the defender
    # and makes its child at the contact. Cannon of Haima's ball (AtkParam_Pc 40800, no flat attack)
    # aimed at a defender 10 m (6 m) out meets him at 9.4 m (5.1 m) and its blast catches him 9.5
    # (4.5) real frames after the cast; the old punish flight flew it through him to the ground at
    # 21.1 m, made the blast there, and caught no one standing (inf at both, `MEASURED` on the round 3
    # code). Light of Miquella's 999 m/s seeker dies on its first move and its child starts 0.4 m in
    # front of him; its pillar catches him 58.5 real frames after the cast (round 3: its child was made
    # 66 m out and caught no one).
    Aa, ta = m.ash()
    coh, lomt = Aa.bullet_tree(ta, 10408000), Aa.bullet_tree(ta, 210670000)
    caught = []
    for dd in (10.0, 6.0):
        sh = Aa.fly_shots(ta, coh, (0.0, dd))
        g = m.flight(coh, 0.0, target=(0.0, dd))
        caught.append((round(sh[1][1][0][2], 2), float(Aa._catches([g], dd, None, [0.0])[1][0])))
    lom10 = Aa.fly_shots(ta, lomt, (0.0, 10.0))
    lomg = m.flight(lomt, 0.0, target=(0.0, 10.0))
    check('a carrier dies on the defender and makes its child there: Cannon of Haima at 10 m and 6 m blasts at '
          '9.4 / 5.1 m, catching him at 9.5 / 4.5 real frames (with no one there the ball lands at 21.1 m); Light '
          "of Miquella's seeker stops on its first move, its child at 9.6 m, and the pillar catches him at 58.5",
          (caught, round(Aa.fly_shots(ta, coh)[1][1][0][2], 2), len(lom10[0][1]), round(lom10[1][1][0][2], 2),
           round(float(Aa._catches([lomg], 10.0, None, [0.0])[1][0]), 6), Aa.carrier(ta, coh), Aa.carrier(ta, lomt))
          == ([(9.39, 9.5), (5.05, 4.5)], 21.11, 2, 9.6, 58.5, True, True))
    like ={(m.ash()[1].arts_name(w['skill']), w['weapon']) for w in m.spell_like_skills({'str': 16, 'dex': 18})}
    check('spell-like skills at STR 16 DEX 18: the Zamor storm and Gravitas, no [NPC] weapon, no Firebreather',
          ('Zamor Ice Storm', 'Zamor Curved Sword') in like and any(s == 'Gravitas' for s, _ in like)
          and not any(w.startswith('[') or s == 'Firebreather' for s, w in like))
    zs = m.skill_events('Zamor Curved Sword', {'str': 16, 'dex': 18}, defender=m.defender(150))
    check('its skill: the slash at f24, the storm at f45, worth 4 x 110 x 4.0 magic before defense',
          zs is not None and sorted(zs[1]) == [24.0, 45.0] and zs[1][45.0] > 4 * zs[1][24.0])
    # The run-in: a 2.5 m storm at f10-12 from 6 m catches nothing, from 1.5 m a locked opponent.
    short = A.contact_geometry(bullet_points=[(f / FPS, 0.0, 1.0, 1.5) for f in (10.0, 11.0, 12.0)])
    near, far = net_values(A, [(short, 10.0)], {10.0: 100.0}, [(6.0, 40.0)], 30.0), \
        A.sequence_outcome([([short], [100.0])], 6.0, floor=40.0)['value']
    check('running in to 1.5 m first reaches the opponent still locked; pressing at 6 m does not',
          far == 0.0 and near[0] > 50.0)
    # The parts: the memo changes no number, and skill_events is skill_attack's defaults.
    zamor = m.by_name['zamor ice storm']
    tap = next(v for v in m.evaluate(zamor, staff, {'int': 80, 'dex': 10}, defender=m.defender(150))
               if v['variant'] == 'tap')
    ev = sorted({(h['frame'], h['ref']) for h in tap['hits']})
    zv = {f: sum(h['damage'] * h['count'] for h in tap['hits'] if h['frame'] == f) for f, _ in ev}
    zg = m.geometry(zamor, {'events': ev, 'hand': tap['startup'] - min(f for f, _ in ev)})
    grid = [(d, f) for d in (2.6, 3.5, 4.5, 6.0, 8.0) for f in (2.0, 10.0, 20.0, 41.0)]
    check('net_values of the Zamor tap is net_parts without the catches memo, exactly',
          zg is not None and net_values(A, zg, zv, grid, tap['roll'], [15.0, 20.0])
          == [p['net'] for p in net_parts(A, zg, zv, grid, tap['roll'], [15.0, 20.0], memo=False)])
    za = m.skill_attack('Zamor Curved Sword', {'str': 16, 'dex': 18}, defender=m.defender(150))
    check('skill_events is the skill_attack defaults tuple (the Zamor Curved Sword skill)',
          zs is not None and za['why'] is None and zs[1:] == (za['value'], za['roll'], za['fp'], za['name'])
          and len(zs[0]) == len(za['geo']) and all(
              fa == fb and set(ga) == set(gb) and all(
                  bool((ga[k] == gb[k]).all()) if hasattr(ga[k], 'shape') else ga[k] == gb[k] for k in ga)
              for (ga, fa), (gb, fb) in zip(zs[0], za['geo'])))
    # The spell rule's node test is the flight's default (k6d2 a).
    seal99, st99 = m.catalyst('Erdtree Seal'), {'int': 99, 'fth': 99, 'arc': 99, 'dex': 20, 'str': 20}
    nine = ["Bayle's Flame Lightning", "Bayle's Tyranny", 'Comet Azur', 'Frenzied Burst', "Gelmir's Fury",
            'Howl of Shabriri', "Night Maiden's Mist", 'Swarm of Flies', 'Unendurable Frenzy']
    flies, ash_flies = {}, {}
    for nm in nine:
        mid = m.by_name[T._norm(nm)]
        cat = staff if m.can_cast(mid, staff) else seal99
        for v in m.evaluate(mid, cat, st99, defender=m.defender(150)):
            ev = sorted({(h['frame'], h['ref']) for h in v['hits']})
            var = {'events': ev, 'hand': v['startup'] - min(f for f, _ in ev)}
            flies[nm] = flies.get(nm, False) or m.geometry(mid, var, missing=[]) is not None
            ash_flies[nm] = ash_flies.get(nm, False) or m.geometry(
                mid, var, A._bullet_damages_self, missing=[]) is not None
    check('the nine spells with flat-only damaging bullets fly under the default (spell) test, seven of them '
          'not at all under the ash test', all(flies.values()) and len(flies) == 9
          and sorted(k for k, x in ash_flies.items() if not x) == sorted(
              ["Bayle's Flame Lightning", "Bayle's Tyranny", 'Frenzied Burst', 'Howl of Shabriri',
               "Night Maiden's Mist", 'Swarm of Flies', 'Unendurable Frenzy']))
    check("Bayle's Flame Lightning: the f69 stomp is a direct AtkParam hit, the bolt a bullet; the kit "
          "scores the bolt", m.punish_values(m.by_name["bayle's flame lightning"], seal99, st99, [(4.0, 14.0)],
                                             defender=m.defender(150), strikes=[15.0]) is not None)
    # Hold spells and Loretta's (h5dr): the variants that fire.
    df_ = m.by_name['dragonfire']
    dfv = {v['variant']: v for v in m.evaluate(df_, seal99, st99, defender=m.defender(150))}
    check('a hold spell (consumeLoopMP_forMenu > 0, 045011 with no cast event) is one cast of 045010, '
          'FP Magic.mp', [v for v, _ in m.variants(df_)][0] == 'cast' and dfv['cast']['fp'] == 28
          and not any(lab == 'tap' for lab, _ in m.variants(df_)))
    df_win = m.tap_release(df_)['window']
    df_rel = m.releases(df_, TAP)
    df_at = {r: [round(f, 1) for f, _ in m.release_events(df_, TAP, r)[1]] for r, _ in df_rel}
    df_late = next(v for v in m.evaluate(df_, seal99, st99, defender=m.defender(150), lead=df_rel[1][0])
                   if v['variant'] == 'early')
    df_c = m._events(SPELL_TAE_BASE + m.magic[df_]['refType'], TAP)[0]
    df_045011 = D.clip(df_c, TAP) or {}
    check(f"Dragonfire let go early ('early', 045011): its 045010 opens SpEffect {TAP_SPEFFECT} at f{df_win[0]:g}-"
          f"{df_win[1]:g}, so a release is one of its {len(df_rel)} real frames "
          f"({[(round(r, 1), round(w, 3)) for r, w in df_rel][:3]}...); each lands 045010's casts before it and none "
          f"after ({ {r: df_at[r] for r in (64.0, 65.0, 66.0, 81.0)} }), 045011 casting nothing; the roll is "
          f"045011's own gate from the release (f{dfv['early']['roll']} at the first, f{df_late['roll']} at "
          f"f{df_rel[1][0]:g}), FP Magic.mp {dfv['early']['fp']}",
          [v for v, _ in m.variants(df_)] == ['cast', 'early'] and df_win == (64.0, 82.0)
          and [r for r, _ in df_rel] == [float(k) for k in range(64, 82)]
          and df_at[64.0] == df_at[65.0] == [49.0] * 3
          and all(df_at[float(k)] == [49.0] * 3 + [65.0] for k in range(66, 82))
          and dfv['early']['lead'] == 64.0 and abs(df_late['roll'] - dfv['early']['roll'] - 1.0) < 0.11
          and dfv['early']['fp'] == 28 and not m._casts(SPELL_TAE_BASE + m.magic[df_]['refType'], TAP)
          and (df_045011.get('gates') or {}).get('roll') is not None)
    check("Loretta's Greatbow charges into 045021 (its 045010 fires nothing), and the charged arrow lands",
          m.variants(m.by_name["loretta's greatbow"]) == [('tap', TAP), ('charged', CHARGED_ALT)]
          and len(m.evaluate(m.by_name["loretta's greatbow"], staff, {'int': 99, 'dex': 10},
                             defender=m.defender(150))) == 2)
    # Glintstone Nails (k6d2 b, c): six nails, each re-aimed at every link, dodged leg by leg.
    nails = m.by_name['glintstone nails']
    nv = next(v for v in m.evaluate(nails, staff, {'int': 80, 'dex': 20}, defender=m.defender(150))
              if v['variant'] == 'tap')
    nev = sorted({(h['frame'], h['ref']) for h in nv['hits']})
    nworth = {e: sum(h['damage'] * h['count'] for h in nv['hits'] if (h['frame'], h['ref']) == e) for e in nev}
    ct = m.cast_threats(nails, nev, nv['startup'] - min(f for f, _ in nev), nworth)
    nb, nw = ct.bursts(6.0)
    arrive = {}
    for bi, (gs, us) in enumerate(nb):
        for g, u in zip(gs, us):
            if math.isfinite(float(A._catches([g], 6.0, None, [0.0])[1][0])):
                arrive.setdefault(u, bi)
    check('Glintstone Nails at 6 m: six threats worth a sixth each, each leg its own burst, the six '
          'arriving in more than one of them (the fan re-aimed from different angles)',
          ct.mode == 'dynamic' and len(nw) == 6 and len({round(x, 6) for x in nw.values()}) == 1
          and len(arrive) == 6 and len(set(arrive.values())) >= 2
          and all(len(set(us)) == len(us) for _, us in nb))
    bt = A.bullet_tree(m.ash()[1], 210451000)
    near = {}
    for aim in (None, (0.0, 6.0)):
        for node, path, _, _, shot, _, _ in A.fly_shots(m.ash()[1], bt, aim):
            if m.spell_node_damaging(node) and any(math.hypot(q[0], q[2] - 6.0) < 0.5 for q in path):
                near.setdefault(aim, set()).add(shot)
    check('flown straight one nail of the fan passes a defender 6 m ahead; aimed, all six reach him',
          (len(near.get(None, ())), len(near.get((0.0, 6.0), ()))) == (1, 6))
    no = ct.outcome(A, 6.0, 10.0, nv['roll'], [15.0], {})
    check('a defender who rolls avoids some nails but not all of them',
          round(no['base'], 6) == round(sum(nw.values()), 6) and 0.0 < no['value'] < no['base'])
    one = m.by_name['glintstone nail']
    ov = next(v for v in m.evaluate(one, staff, {'int': 80, 'dex': 20}, defender=m.defender(150))
              if v['variant'] == 'tap')
    oev = sorted({(h['frame'], h['ref']) for h in ov['hits']})
    ct1 = m.cast_threats(one, oev, ov['startup'] - min(f for f, _ in oev),
                         {e: sum(h['damage'] * h['count'] for h in ov['hits'] if (h['frame'], h['ref']) == e)
                          for e in oev})
    arrive1 = min(float(A._catches([g], 6.0, None, [0.0])[1][0]) for g in ct1.geometries(6.0))
    side = A.roll_motion()[1]
    check('Glintstone Nail at 6 m: a roll timed to the nail\'s arrival (5 frames before) clears that pass, '
          'and the leg re-aimed at where the roll took him finds him after the 13 i-frames',
          ct1.mode == 'dynamic' and bool(ct1._touches(6.0, side, (arrive1 - 5.0,)))
          and not ct1._touches(6.0, {**side, 'iframes': 1e9}, (arrive1 - 5.0,)))
    global DYNAMIC_ROLLS
    keep, landed = DYNAMIC_ROLLS, {}
    for rolls in (1, keep):
        DYNAMIC_ROLLS = rolls
        fresh = m.cast_threats(one, oev, ov['startup'] - min(f for f, _ in oev), dict(ct1.worth))
        landed[rolls] = fresh.outcome(A, 6.0, 10.0, ov['roll'], [15.0])['value']
    DYNAMIC_ROLLS = keep
    check(f'rolling again at the passes that come back lands less of Glintstone Nail at 6 m than one roll '
          f'({landed[1]:.0f} -> {landed[keep]:.0f})', 0.0 <= landed[keep] < landed[1])
    grid2 = [(4.0, 10.0), (8.0, 20.0)]
    check('net_values of the Nails threats is net_parts without the catches memo, exactly',
          net_values(A, ct, None, grid2, nv['roll'], [15.0]) == [p['net'] for p in net_parts(
              A, ct, None, grid2, nv['roll'], [15.0], memo=False)])
    # bd er-effects-rs-doax / er-effects-rs-n7dd: a cast leaves its DummyPoly where the clips have
    # carried the caster; a caught defender walks out of a lingering area; one cast lands at most the
    # defender's health.
    dfn = m.defender(150)
    check(f"the RL 150 defender: Vigor {dfn['vig']} -> {dfn['hp']} HP (CalcCorrectGraph {HP_GRAPH}), poise {dfn['poise']}",
          dfn['hp'] == DEF.calc_correct(m.def_tables().graph[HP_GRAPH], dfn['vig']) and 300 <= dfn['hp'] <= 2100
          and dfn['poise'] and dfn['poise'] > 0)
    zev = sorted({(h['frame'], h['ref']) for h in tap['hits']})
    zl = m.cast_launch(zamor, tap['anim'], zev[0][0], zev[0][1], tap['startup'] - min(f for f, _ in zev), staff['base'])
    check(f"the Zamor tap's storm leaves its DummyPoly after the cast's forward step "
          f"({zl and zl['src']}, peak {zl and round(zl['peak'], 2)} m)", zl is not None and zl['peak'] > 0.3)
    mb = m.by_name['magma breath']
    mv = m.evaluate(mb, seal99, st99, defender=dfn)[0]
    mev = sorted({(h['frame'], h['ref']) for h in mv['hits']})
    mhand = mv['startup'] - min(f for f, _ in mev)
    mgeo = m.geometry(mb, {'events': mev, 'hand': mhand, 'anim': mv['anim'], 'weapon': seal99['base']}, missing=[])
    mval = {}
    for h in mv['hits']:
        mval[h['frame']] = mval.get(h['frame'], 0.0) + h['damage'] * h['count']
    m.attach_caught(mb, mgeo, mv['hits'], mval)
    ml = [m.cast_launch(mb, mv['anim'], f, n, mhand, seal99['base']) for f, n in mev]
    dm = m.by_name['dragonmaw']
    dv = m.evaluate(dm, seal99, st99, defender=dfn)[0]
    dev = sorted({(h['frame'], h['ref']) for h in dv['hits']})
    dl = m.cast_launch(dm, dv['anim'], dev[0][0], dev[0][1], dv['startup'] - min(f for f, _ in dev), seal99['base'])
    check(f"Magma Breath's casts carry orientation byte 2 and leave the dummy itself along its forward axis "
          f"({[(x['src'], round(x['pitch'], 1)) for x in ml]}); Dragonmaw's byte 1 skips the lock-on turn",
          all(x['src'] == 'dummy axis' and x['aim'] for x in ml) and dl is not None and not dl['aim'])
    cm = A.CaughtModel.of(mgeo, dfn['poise'], mval)
    pools = [li for g, _ in mgeo for li in g['CAUGHT']['lists'] if li['n'] > 1]
    ticks = [round(x * pools[0]['n'], 2) for x in (cm.landed(4.0)[0][0][0], cm.landed(6.0)[0][0][0])] if pools else None
    check(f"Magma Breath: a defender the breath catches takes {ticks} of its pool's {pools and pools[0]['n']} "
          f"ticks at 4 / 6 m (he staggers, rolls out at the DamageCount roll cancel), not all of them",
          pools and all(1 <= x < pools[0]['n'] for x in ticks))
    plain = {k: v for k, v in dfn.items() if k not in ('hp', 'poise')}
    sit = [(2.6, 14.0), (6.0, 14.0)]
    old = m.punish_values(mb, seal99, st99, sit, defender=plain, strikes=[15.0])[0]
    new = m.punish_values(mb, seal99, st99, sit, defender=dfn, strikes=[15.0])[0]
    # Round 4: at 2.6 m the ball (10701000, aimed at the lock-on point in 3D, then 10 deg up) now
    # misses a defender who rolls away at f60-66, so every roll direction has an escape and the
    # breath lands nothing there (1430.5 / 184.0 before, -291 both now): the caught model has no
    # landed tick to take off at 2.6 m, and takes them off at 6 m.
    check(f"Magma Breath's punish at 2.6 / 6 m: {[round(x) for x in old]} with every tick credited -> "
          f"{[round(x) for x in new]}, at most {dfn['hp']} HP",
          all(b <= dfn['hp'] + 1e-6 and b <= a for a, b in zip(old, new)) and new[1] < old[1])
    # bd er-effects-rs-dnok: a two-cast hit priority record. a415_045010 runs event 341 slot 0 over
    # its f6 (Args 0xc 0) and f11 (1) casts; the f15 one is outside it.
    ast_ = m.by_name['meteorite of astel']
    av = next(v for v in m.evaluate(ast_, staff, {'int': 80, 'dex': 10}, defender=m.defender(150)) if v['anim'] == 45010)
    aev = sorted({(h['frame'], h['ref']) for h in av['hits']})
    arec = [m.cast_record(ast_, av['anim'], f, n) for f, n in aev]
    check(f"Meteorite of Astel 045010: the f6 and f11 casts take one record at priorities 0 and 1, the f15 one "
          f"none ({arec})", [r and (r[0], r[1]) for r in arec]
          == [(('cast', 415, 45010, 0), 0), (('cast', 415, 45010, 0), 1), None])
    ageo = m.geometry(ast_, {'events': aev, 'hand': av['startup'] - min(f for f, _ in aev), 'anim': av['anim'],
                             'weapon': staff['base']}, missing=[])
    aval = {}
    for h in av['hits']:
        aval[h['frame']] = aval.get(h['frame'], 0.0) + h['damage'] * h['count']
    m.attach_caught(ast_, ageo, av['hits'], aval)
    acm = A.CaughtModel.of(ageo, 62.0, aval)
    aplain = A.CaughtModel.of([({k: v for k, v in g.items() if k != 'HPR'}, fr) for g, fr in ageo], 62.0, aval)
    # Round 4: the f11 cast's marker (10470151, life 0.1 s, lifeRandomRange 0.5) flies its mean
    # drawn life, 0.35 s, and its meteor no longer meets him on the axis at any distance 0.5-14 m
    # (`MEASURED`; that the longer rise is why is `INFERRED`). Until then it landed after the f6
    # catch at 2 m without the record and nothing with it; now the record has nothing to hold back
    # here, and this spell no longer exercises the caught model's record (open, bd
    # er-effects-rs-7bye round 4).
    check(f"Meteorite of Astel at 2 m: caught by the f6 meteor; the f11 one of its record misses him on the axis, "
          f"record or not ({acm.row(2.0, 0)[1]}; without the record {aplain.row(2.0, 0)[1]}), {acm.worth_of(2.0, 0):.0f} "
          f"lands either way",
          acm.row(2.0, 0)[1] == [0.0] and aplain.row(2.0, 0)[1] == [0.0]
          and acm.worth_of(2.0, 0) == aplain.worth_of(2.0, 0)
          and all(not math.isfinite(float(A._catches([ageo[1][0]], dd, None, [0.0])[1][0]))
                  for dd in (2.0, 4.0, 6.0)))
    # bd er-effects-rs-xb3i: an event's shots and their children are one launch with one id
    # (BulletAttackInfo +0x658, copied by FUN_14038b740; FUN_14051b870 0x14051b8c9 passes the
    # entry's own id, 1.16.2), so its record holds none of them back; another event of the record
    # is another launch.
    ct = CastThreats.__new__(CastThreats)
    ct.records = {(6, 1): (('cast', 1, 2, 0), 0, 0), (11, 1): (('cast', 1, 2, 0), 1, 3)}
    shared = ct._shared({(6, 1, 0): 10.0, (6, 1, 1): 10.0, (11, 1, None): 15.0, (20, 1, None): 5.0},
                        [(6, 1, 0), (6, 1, 1), (11, 1, None), (20, 1, None)])
    check(f"a record's events land one between them, each with every shot it fired: the f6 event's two shots "
          f"(10 + 10) over the f11 one's 15, plus 5 outside the record ({shared})", shared == 25.0)
    # In-flight homing (bd er-effects-rs-rw25): a defender 8 m ahead who rolls sideways, each cast
    # read with and without its geometry's `HOMING` record. Without it the stored samples are the
    # shot aimed where he stood; with it each shot is aimed where he is when it is created and turns
    # after him (FUN_140390c20, FUN_140398270).
    import numpy as np
    sideways = [mv for mv in A.roll_motion() if abs(mv['path'][-1][0]) > 1.0]

    def rolled(name):
        sid = m.by_name[name]
        sv = next(v for v in m.evaluate(sid, staff, {'int': 65, 'dex': 18}, defender=m.defender(150))
                  if v['variant'] == 'tap')
        sev = sorted({(h['frame'], h['ref']) for h in sv['hits']})
        g = m.geometry(sid, {'events': sev, 'hand': sv['startup'] - min(f for f, _ in sev), 'anim': sv['anim'],
                             'weapon': staff['base']})[0][0]
        straight = {k: v for k, v in g.items() if k != 'HOMING'}
        grid = np.arange(0.0, g['T'].max() + 1.0, A.DODGE_STEP)
        counts, late, new, freed = [], [], [], 0
        for mv in sideways:
            hs, _ = A._catches([straight], 8.0, mv, grid)
            hh, th = A._catches([g], 8.0, mv, grid)
            counts.append((int((hs < 0).sum()), int((hh < 0).sum())))
            late += [float(th[i] - s) for i, s in enumerate(grid) if hs[i] < 0 and hh[i] >= 0]
            new += [float(s) for i, s in enumerate(grid) if hs[i] < 0 and hh[i] >= 0]
            freed += int(((hs >= 0) & (hh < 0)).sum())
        return 'HOMING' in g, counts, late, new, freed, float(g['TL'].min() if 'TL' in g else g['T'].min())
    ch, cc, cl, cn, cf, cborn = rolled('glintstone cometshard')
    iframes = sideways[0]['iframes']
    # The pin of the aim at creation (bd er-effects-rs-rw25): a roll begun on reaction to the cast
    # start is over before the bolt exists, so the bolt is aimed where it took him.
    check(f"Glintstone Cometshard at 8 m: a sideways roll whose {iframes:.0f} i-frames end before the "
          f"bolt exists (f{cborn:.1f}) no longer clears it, the bolt being aimed where the roll took him "
          f"({cc}, aimed where he stood then where he is; {len(cn)} starts newly caught, f"
          f"{min(cn, default=0):.1f}-{max(cn, default=0):.1f}, each touched {min(cl, default=0):.1f}+ frames "
          f"in), and no start it clears was caught aimed where he stood",
          ch and len(cc) == 2 and all(0 < h < s for s, h in cc) and cf == 0 and bool(cn)
          and max(cn) + iframes <= cborn and min(cn) <= A.reaction_delays()[A.REACTION_POINTS // 2][0])
    gh, gc, gl = rolled('gravity well')[:3]
    check(f"Gravity Well at 8 m: its 90 deg/s shot turns after a sideways roller, so fewer roll starts "
          f"clear it ({gc}, straight then homing) though a roll through it still does, and every start it "
          f"newly lands on is touched after his {sideways[0]['iframes']:.0f} i-frames ({len(gl)} starts, the "
          f"earliest {min(gl, default=0):.1f} frames in)",
          gh and len(gc) == 2 and all(0 < h < s for s, h in gc) and bool(gl) and min(gl) >= sideways[0]['iframes'])
    # Free aim at the ground (bd er-effects-rs-rw25): Great Oracular Bubble's damage is its burst,
    # made where the bubble meets the ground; aimed at him it flies level and never lands near him.
    gob = m.by_name['great oracular bubble']
    bv = next(v for v in m.evaluate(gob, staff, {'int': 65, 'dex': 18}, defender=m.defender(150)) if v['variant'] == 'tap')
    bev = sorted({(h['frame'], h['ref']) for h in bv['hits']})
    bspec = {'events': bev, 'hand': bv['startup'] - min(f for f, _ in bev), 'anim': bv['anim'], 'weapon': staff['base']}
    blevel = m.geometry(gob, bspec)
    bfloor = m.geometry(gob, bspec, floor=4.0)
    bval = {}
    for h in bv['hits']:
        bval[h['frame']] = bval.get(h['frame'], 0.0) + h['damage'] * h['count']
    bparts = net_parts_aims(A, blevel, [('floor 4 m', bfloor)], bval, [(4.0, 30.0), (4.0, 0.0)], bv['roll'], [15.0],
                            defender=m.defender(150))
    # Round 4: the bubble (10511000: no damage, targets opponents, stops on a character) is a carrier.
    # Aimed at him it homes in (50 deg/s), stops on him at 3.2 m and bursts there, so it catches him
    # (82.3 real frames; round 3 flew it through him and it never did), sooner than the free aim at
    # the ground (89.8), and the outcome takes the aim at him in both recoveries (it took the floor
    # aim in the long one).
    blt = float(A._catches([g for g, _ in blevel], 4.0, None, [0.0])[1][0])
    bft = float(A._catches([g for g, _ in bfloor], 4.0, None, [0.0])[1][0])
    check(f"Great Oracular Bubble at 4 m: aimed at him the bubble stops on him and its burst catches him "
          f"({blt:.1f}); free-aimed at the ground 4 m out its burst does too ({bft:.1f}), no shot homes there, "
          f"and the outcome takes the aim at him ({[p['aim'] for p in bparts]})",
          m.floor_aims(gob, bspec) and math.isfinite(blt) and math.isfinite(bft) and blt < bft
          and any('HOMING' in g for g, _ in blevel) and not any('HOMING' in g for g, _ in bfloor)
          and [p['aim'] for p in bparts] == ['target', 'target'] and bparts[0]['net'] > 0)
    # The tap path (c0000.hks MagicFireRight_Upper_onUpdate): 045010 up to the release, then 045011.
    cs_, rb_, az_ = (m.by_name[x] for x in ('glintstone cometshard', 'rock blaster', 'comet azur'))
    check(f"Glintstone Cometshard's 045010 opens SpEffect {TAP_SPEFFECT} at its first frame "
          f"({m.tap_release(cs_)}), so its tap is 045011 from the hand-off, as it was",
          m.tap_release(cs_)['lead'] == 0.0 and m.tap_release(cs_)['window'] == (0.0, 20.0)
          and m.release_events(cs_, TAP)[1] == [(15.0, 1)])
    rbt = m.tap_release(rb_)
    rbe = m._cast_events(rb_, TAP)[1]
    rbv = {v['variant']: v for v in m.evaluate(rb_, staff, st99, defender=dfn)}
    rbh = rbv['charged']['startup'] - min(f for f, _ in m.release_events(rb_, RELEASE)[1])
    check(f"Rock Blaster opens it at 045010 f{rbt['lead']:g} ({rbt['window']}): its tap plays 045010 to there, "
          f"casting 045010's f3, f3 and f7 on the way, then 045011 from f{rbt['lead']:g} "
          f"({[(round(f, 2), clip) for f, _, clip, _ in rbe][:5]}...), its roll frame that much later",
          rbt['window'][0] == 10.0 and [clip for f, _, clip, _ in rbe[:3]] == [RELEASE] * 3
          and all(clip == TAP and f >= 10.0 - 1e-6 for f, _, clip, _ in rbe[3:])
          and abs(rbv['tap']['startup'] - (rbh + 3.0)) < 0.11 and rbv['tap']['lead'] == 10.0
          and m.cast_launch(rb_, TAP, rbe[3][0], rbe[3][1], rbh, staff['base']) is not None)
    azt = m.tap_release(az_)
    check(f"Comet Azur's 045010 has no {TAP_SPEFFECT} window; a hold spell (045012), its tap plays all of "
          f"045010 (f{azt['lead']:g}, its f20 casts included) and then 045011",
          m.hold_magic(az_) and azt['window'] is None and azt['lead'] == 35.0
          and [round(f, 1) for f, _ in m.release_events(az_, TAP)[1]] == [20.0] * 4 + [35.0])
    rbc = [{'mid': rb_, 'anim': a_, 'hand': rbh} for a_ in (TAP, RELEASE)]
    rbs = m.release_shows(rb_, rbt['lead'])
    rbm = [f for f, k in m._look(rbc[0]) if k == ('clip', TAP)]
    rbx = sorted({f for f, _ in {(round(f, 2), k) for f, k in m._look(rbc[0]) if k[0] == 'cast'}
                  ^ {(round(f, 2), k) for f, k in m._look(rbc[1]) if k[0] == 'cast'}})
    check(f"a tap and its charge part where the tap leaves 045010, f{rbh:.1f} + {rbt['lead']:g} for Rock Blaster "
          f"({m.look_split(*rbc):.1f}), not at the hand-off: the tap's clip shows {rbs} frames later (f{rbm}), but "
          f"the two shots already differ at the release (casts at {rbx[:3]}... in one and not the other), which is "
          f"seen when it spawns",
          abs(m.look_split(*rbc) - (rbh + rbt['lead'])) < 1e-6 and rbs > 0
          and rbm == [rbh + rbt['lead'] + rbs] and rbx and abs(rbx[0] - (rbh + rbt['lead'])) < 0.01)
    # What is on screen (ashes-of-war.md section 16a): the casts one TimeAct and casting SFX play
    # alike, and a tap and its charge up to the tap's release, are one group he cannot split. These
    # read each tap at its earliest release alone (`cast_inputs` releases False, a one-frame window:
    # the rule before release frames were spread, whose numbers they keep); the spread follows.
    lst, lsit, lstrike = {'int': 80, 'dex': 18}, [(3.0, 0.0), (6.0, 10.0), (9.0, 0.0)], [15.0, 20.0]
    lnames = ('glintstone cometshard', 'comet', 'shard spiral', 'night comet', 'swift glintstone shard')
    lcasts = {}
    for nm in lnames:
        for c in m.cast_inputs(m.by_name[nm], staff, lst, defender=dfn, releases=False):
            lcasts[(m.names[c['mid']], c['variant'])] = c
    looks = spell_lookalikes(m, A, lcasts)
    check('Glintstone Cometshard, Comet, Shard Spiral and Night Comet are refType 2 (one TimeAct, a402); '
          'castSfxId 523060, 523070, 523070, 523200 (REGULATION)',
          [(m.magic[m.by_name[nm]]['refType'], m.magic[m.by_name[nm]]['castSfxId']) for nm in lnames[:4]]
          == [(2, 523060), (2, 523070), (2, 523070), (2, 523200)])
    groups = {k: None if v is None else sorted(v.group.keys) for k, v in looks.items()}
    check(f"the groups: Comet and Shard Spiral, taps and charges, are one ({groups[('Comet', 'tap')]}); "
          f"Cometshard and Night Comet each their own tap and charge; Swift Glintstone Shard (refType 79, "
          f"castSfxId 522000, one cast) alone, None",
          groups[('Comet', 'tap')] == groups[('Shard Spiral', 'charged')] == sorted(
              [('Comet', 'tap'), ('Comet', 'charged'), ('Shard Spiral', 'tap'), ('Shard Spiral', 'charged')])
          and groups[('Glintstone Cometshard', 'tap')] == sorted([('Glintstone Cometshard', 'tap'),
                                                                  ('Glintstone Cometshard', 'charged')])
          and groups[('Night Comet', 'charged')] == sorted([('Night Comet', 'tap'), ('Night Comet', 'charged')])
          and groups[('Swift Glintstone Shard', 'cast')] is None)
    hand = lcasts[('Comet', 'tap')]['hand']
    clead = m.tap_release(m.by_name['comet'])['lead']
    cshows = m.release_shows(m.by_name['comet'], clead)
    split = {(a, b): m.look_split(lcasts[a], lcasts[b]) for a, b in (
        (('Comet', 'tap'), ('Comet', 'charged')), (('Comet', 'tap'), ('Shard Spiral', 'tap')),
        (('Comet', 'charged'), ('Shard Spiral', 'charged')), (('Comet', 'tap'), ('Shard Spiral', 'charged')))}
    check(f"the splits at DEX 18: a tap and a charge where the tap's release at the hand-off (f{hand:.1f}) shows, "
          f"{cshows} frames on; the two taps at their shot's spawn (045011 casts at f15: f{hand + 15:.1f}), the "
          f"two charges at theirs (045010, f37: f{hand + 37:.1f}) ({sorted(round(x, 2) for x in split.values())})",
          clead == 0.0 and 0 < cshows < 15
          and [round(x, 3) for x in split.values()] == [round(hand + cshows, 3), round(hand + 15.0, 3),
                                                        round(hand + 37.0, 3), round(hand + cshows, 3)])
    ct_tap = lcasts[('Comet', 'tap')]
    lb = net_parts(A, ct_tap['attack'], ct_tap['value'], lsit, ct_tap['roll'], lstrike, defender=dfn)
    la = net_parts(A, ct_tap['attack'], ct_tap['value'], lsit, ct_tap['roll'], lstrike, defender=dfn,
                   look=looks[('Comet', 'tap')])
    grp = looks[('Comet', 'tap')].group
    when = sorted({at for (k, d, f, _), at in grp.chosen.items() if k == ('Comet', 'tap') and (d, f) in lsit})
    roll_ = A.roll_motion()
    taps = [grp.first_oks(k, 6.0, roll_)[0] for k in (('Comet', 'tap'), ('Shard Spiral', 'tap'))]
    planned = grp.plan(('Comet', 'tap'), 6.0, roll_, taps[0], 0.0, 10.0)
    check(f"the Comet tap at 3 / 6 / 9 m: the defender who knows it escapes every roll "
          f"({[round(p['p_evade'], 6) for p in lb]}); "
          f"seeing only the cast he waits for the tap's release at the hand-off to show (decided at {when}, "
          f"f{hand:.1f} + {cshows}) and then picks one timing against both taps (group_presses from there), so he "
          f"takes some: net {[round(p['net'], 1) for p in lb]} -> {[round(p['net'], 1) for p in la]}",
          all(round(p['p_evade'], 6) == 1.0 and p['net'] == 0.0 for p in lb) and all(p['net'] > 0 for p in la)
          and when == [hand + cshows]
          and planned == [A.group_presses([taps[0][k], taps[1][k]], [1.0, 1.0], hand + cshows, floor=10.0)
                          for k in range(len(roll_))])
    cs_tap = lcasts[('Glintstone Cometshard', 'tap')]
    cs_look = looks[('Glintstone Cometshard', 'tap')]
    ctt = cs_tap['attack']
    cs_seen = cs_tap['hand'] + m.release_shows(cs_, 0.0)
    same = []
    for d, f in lsit:
        got = ctt.outcome(A, d, f, cs_tap['roll'], lstrike, {}, hp=dfn['hp'], poise=dfn['poise'], plan=cs_look.plan(d))
        cue = A.sequence_outcome(ctt.bursts(d), d, cue=cs_seen, floor=f, my_roll=cs_tap['roll'], strikes=lstrike,
                                 run=True, catches={}, caught=ctt._caught[round(d, 2)], hp=dfn['hp'])
        same.append(all(abs(got[x] - cue[x]) < 1e-9 for x in ('value', 'p_evade', 'p_punish')))
    cb = net_parts(A, ctt, cs_tap['value'], lsit, cs_tap['roll'], lstrike, defender=dfn)
    ca = net_parts(A, ctt, cs_tap['value'], lsit, cs_tap['roll'], lstrike, defender=dfn, look=cs_look)
    check(f"Glintstone Cometshard's tap against its own charge: the two play 045000 alike to the hand-off, and "
          f"the tap's release there shows {cs_seen - cs_tap['hand']:g} frames later, so the defender waits for it "
          f"and reacts from f{cs_seen:.1f}, exactly the cast read from there "
          f"({same}); its bolt is no longer rolled every time: net {[round(p['net'], 1) for p in cb]} -> "
          f"{[round(p['net'], 1) for p in ca]}, p_evade {[round(p['p_evade'], 3) for p in ca]}",
          all(same) and all(round(p['p_evade'], 6) == 1.0 for p in cb)
          and all(round(p['p_evade'], 6) < 1.0 and p['net'] > 0 for p in ca))
    lg, ll = m.lookalike_group(m.by_name['comet'], staff, [staff], lst, defender=dfn)
    lsize = 2 + sum(len(lg[(x, 'tap')]['members']) + 1 for x in (m.by_name['comet'], m.by_name['shard spiral']))
    lwant = 2 + sum(len(m.releases(m.by_name[x], TAP)) for x in ('comet', 'shard spiral'))
    check(f"lookalike_group (the pouch's punish_values with cats): Comet's group among what INT 80 casts with "
          f"the staff is Comet and Shard Spiral, tap and charged ({sorted((m.names[a], b) for a, b in lg)}), each "
          f"tap with its later releases, one per real frame of its window ({lsize} members)",
          sorted((m.names[a], b) for a, b in lg) == sorted(groups[('Comet', 'tap')]) and lsize == lwant > 4
          and all(v is not None and len(v.group) == lsize for v in ll.values()))
    sw = lcasts[('Swift Glintstone Shard', 'cast')]
    alone = Lookalikes(A, {'x': sw['attack']}, {}).member('x')
    keep_ = ('net', 'value', 'base', 'p_evade', 'p_punish', 'punish_hp', 'gap')
    sw0 = net_parts(A, sw['attack'], sw['value'], lsit, sw['roll'], lstrike, defender=dfn)
    sw1 = net_parts(A, sw['attack'], sw['value'], lsit, sw['roll'], lstrike, defender=dfn, look=alone)
    check(f"Swift Glintstone Shard, alone in its group and one cast: no look, and even a one-member group's plan "
          f"gives net_parts exactly ({[round(p['net'], 3) for p in sw0]})",
          looks[('Swift Glintstone Shard', 'cast')] is None
          and [[p[x] for x in keep_] for p in sw0] == [[p[x] for x in keep_] for p in sw1])
    # A charged skill's tap and hold: the Glintstone Kris's Glintstone Dart.
    At = m.ash()[1]
    kris = At.find_weapon('Glintstone Kris')
    dsid = At.reg.weapon[kris]['swordArtsParamId']
    drel = skill_releases(A, At, dsid, kris, m.ar.max_level(m.ar.weapons[kris]['reinforceTypeId']))
    kst = {'str': 16, 'dex': 18, 'int': 65}
    dhold = m.skill_attack('Glintstone Kris', kst, defender=dfn, priced_bullets=True, flown_only=True)
    dtap = m.skill_attack('Glintstone Kris', kst, defender=dfn, follow=drel[0], priced_bullets=True, flown_only=True)
    dgrp = skill_lookalikes(A, dhold, dtap)
    hlook = dgrp.member('hold')
    dsame = []
    for d, f in lsit:
        plan_ = A.sequence_outcome(_bursts(dhold['geo'], dhold['value']), d, floor=f, my_roll=dhold['roll'],
                                   strikes=lstrike, run=True, plan=hlook.plan(d))
        cue = A.sequence_outcome(_bursts(dhold['geo'], dhold['value']), d, cue=float(drel[0]['first']), floor=f,
                                 my_roll=dhold['roll'], strikes=lstrike, run=True)
        dsame.append(all(abs(plan_[x] - cue[x]) < 1e-9 for x in ('value', 'p_evade', 'p_punish')))
    check(f"the Glintstone Dart: its early release is let go at f{drel[0]['first']} (the {RELEASE_EARLY_SPEFFECT} "
          f"window's first frame, the id er-mechanics-ashes reads too), where the tap's clip starts (its press "
          f"f{dtap['option']['parts'][1]['offset']}); before it both play the opening, so that is the split "
          f"({dgrp.splits[0][1]}), and the full charge is read from there ({dsame})",
          RELEASE_EARLY_SPEFFECT == A.RELEASE_EARLY_SPEFFECT
          and drel and float(drel[0]['first']) == float(dtap['option']['parts'][1]['offset'])
          == dgrp.splits[0][1] and min(min(f for f in dhold['value']), min(f for f in dtap['value'])) > drel[0]['first']
          and all(dsame))
    # The release frame is the attacker's choice (`er-mechanics-ashes.release_frames`): a tap is one
    # member per release frame of its window, and what it nets is their weighted mean.
    csid = m.by_name['glintstone cometshard']
    ci = {c['variant']: c for c in m.cast_inputs(csid, staff, lst, defender=dfn)}
    ctap = ci['tap']
    cmem = [ctap] + ctap['members']
    check(f"Glintstone Cometshard's tap (045010 window f0-20): released on each of its 20 real frames "
          f"({[x['lead'] for x in cmem][:4]}... weighed 1/20), each the same 045011 bolt that many frames on, its "
          f"roll that much later; the charge has none",
          [x['lead'] for x in cmem] == [float(k) for k in range(20)] and all(abs(x['weight'] - 0.05) < 1e-12 for x in cmem)
          and [x['spec']['events'] for x in cmem] == [[(15.0 + x['lead'], 1)] for x in cmem]
          and all(abs(x['roll'] - ctap['roll'] - x['lead']) < 0.11 for x in cmem) and ci['charged']['members'] == []
          and ci['charged']['weight'] == 1.0)
    ckeys = {('cs', 'tap'): ctap, ('cs', 'charged'): ci['charged']}
    cflat = {release_key(('cs', 'tap'), x, x is ctap): x for x in cmem}
    chand = ctap['hand']
    cshow = {lead: m.release_shows(csid, lead) for lead in (0.0, 5.0, 10.0)}
    csplit = {(a, b): round(m.look_split(cflat[a], cflat[b]) - chand, 6)
              for a, b in ((('cs', 'tap'), ('cs', 'tap', 5.0)), (('cs', 'tap', 5.0), ('cs', 'tap', 10.0)))}
    csplit[(('cs', 'tap', 10.0), ('cs', 'charged'))] = round(m.look_split(cflat[('cs', 'tap', 10.0)], ci['charged'])
                                                              - chand, 6)
    clooks = spell_lookalikes(m, A, ckeys)
    cgroup = clooks[('cs', 'charged')].group
    check(f"each release parts from the charge where its blend into 045011 shows (shows {cshow} frames after the "
          f"switch, release_visible) and from a later one where the first shows (after the hand-off: {csplit}); the "
          f"group is the charge and the 20 releases, weighted 1 and 1/20 each, each an event where it shows",
          list(csplit.values()) == [cshow[0.0], 5.0 + cshow[5.0], 10.0 + cshow[10.0]]
          and all(s > 0 for s in cshow.values()) and len(cgroup) == 21
          and sorted(round(w, 9) for w in cgroup.weights) == [0.05] * 20 + [1.0]
          and cgroup.events[cgroup.keys.index(('cs', 'tap', 5.0))] == chand + 5.0 + cshow[5.0])
    cpv = m.punish_values(csid, staff, lst, lsit, defender=dfn, strikes=lstrike, cats=[staff])
    cfull = None
    for x in cmem:
        v_ = m._member_values(A, csid, x, lsit, lstrike, dfn, clooks[release_key(('cs', 'tap'), x, x is ctap)])
        cfull = [x['weight'] * b for b in v_] if cfull is None else [a + x['weight'] * b for a, b in zip(cfull, v_)]
    cread = A.release_read(len(cmem), lambda i: m._member_values(A, csid, cmem[i], lsit, lstrike, dfn,
                                                                 clooks[release_key(('cs', 'tap'), cmem[i], i == 0)]),
                           lambda v: sum(v) / len(v))
    check(f"the tap nets the weighted mean of its releases, each read by the plan against the whole group, the "
          f"releases' own nets read at {sorted(cread)} and filled in between (release_read): per situation "
          f"{[round(x, 1) for x in cpv[0]]} against every release read {[round(x, 1) for x in cfull]}, within "
          f"{A.RELEASE_TOL:g} HP; punish_values takes the better variant ({cpv[1]})",
          cpv[1] != 'tap' or max(abs(a - b) for a, b in zip(cpv[0], cfull)) <= A.RELEASE_TOL)
    az1 = m.cast_inputs(az_, staff, lst, defender=dfn)
    az0 = m.cast_inputs(az_, staff, lst, defender=dfn, releases=False)
    azv1 = m.punish_values(az_, staff, lst, lsit, defender=dfn, strikes=lstrike, casts=az1,
                           looks=spell_lookalikes(m, A, {(az_, c['variant']): c for c in az1}))
    azv0 = m.punish_values(az_, staff, lst, lsit, defender=dfn, strikes=lstrike, casts=az0,
                           looks=spell_lookalikes(m, A, {(az_, c['variant']): c for c in az0}))
    check(f"a window of one real frame is the earliest release alone (release_frames(64, 65)); so is a release "
          f"with no window (Comet Azur's tap, at 045010's end), weight 1, and its nets are bit for bit those read "
          f"without releases ({[round(x, 2) for x in azv1[0]]}, {azv1[1]})",
          A.release_frames(64.0, 65.0) == [(64.0, 1.0)] and m.releases(az_, TAP) == [(35.0, 1.0)]
          and all(c['members'] == [] and c['weight'] == 1.0 for c in az1)
          and azv1 is not None and azv1 == azv0)
    # A charged skill's early release, let go on any frame of its window: the Dart's f13-26.
    dlook = m.release_look(dhold, 'Glintstone Kris', kst, defender=dfn, priced_bullets=True, flown_only=True)
    dg = dlook.group
    dpress = float(dtap['option']['parts'][1]['offset'])
    dsp = [dg.splits[0][i] - dpress for i in range(1, len(dg.keys))]
    dshow = [A.skill_release_visible(At, dsid, drel[0]['anim'], 13.0 + k) for k in range(13)]
    check(f"the Dart's hold against its release let go on each of f13-25 (release_frames over [13, 26)): members "
          f"weighed 1 and 1/13 each, the hold told from each where it shows (after the first press: "
          f"{[round(x, 2) for x in dsp]}; shows {dshow} frames after its switch), two releases where the first shows",
          dg.keys == ['hold', 'tap'] + [f'tap+{k}' for k in range(1, 13)]
          and [round(w * 13, 6) for w in dg.weights] == [13.0] + [1.0] * 13
          and [round(x, 6) for x in dsp] == [float(k + s) for k, s in zip(range(13), dshow)]
          and dg.splits[2][3] == min(dg.splits[0][2], dg.splits[0][3]) and dg.splits[1][3] == min(dg.splits[0][1],
                                                                                                 dg.splits[0][3]))
    # The defender's strike (module doc, "The kit"). Stars of Ruin's charged cast, INT 80: its stars
    # leave at its startup (f54.9 at DEX 10). Pressed at 1.5 m on a defender free at once, two R1s
    # that break any poise (500) reach the caster by f13.3 + 20 at the latest, before the stars
    # leave and before his roll gate: struck, interrupted whole (nothing lands), the R1 paid in full.
    # Pressed while the defender's recovery holds him to f80, past the caster's roll gate: no
    # strike lands, he rolls, the net the roll alone gives.
    sr_mid = m.by_name['stars of ruin']
    sr_v = next(x for x in m.evaluate(sr_mid, staff, {'int': 80}, defender=dfn) if x['variant'] == 'charged')
    sr_ev = sorted({(h['frame'], h['ref']) for h in sr_v['hits']})
    sr_spec = {'events': sr_ev, 'hand': sr_v['startup'] - min(f for f, _ in sr_ev), 'anim': sr_v['anim'],
               'weapon': staff['base'], 'lead': sr_v['lead']}
    sr_geo = m.geometry(sr_mid, sr_spec, missing=[])
    sr_val = {}
    for h in sr_v['hits']:
        sr_val[h['frame']] = sr_val.get(h['frame'], 0.0) + h['damage'] * h['count']
    m.attach_caught(sr_mid, sr_geo, sr_v['hits'], sr_val)
    sr_sit = [(1.5, 0.0), (1.5, 80.0)]
    sr_new = net_parts(A, sr_geo, sr_val, sr_sit, sr_v['roll'], Strikes([15.0, 20.0], [500.0, 500.0]), defender=dfn)
    sr_old = net_parts(A, sr_geo, sr_val, sr_sit, sr_v['roll'], [15.0, 20.0], defender=dfn)
    sr_launch = min(_launch(g) for g, _ in sr_geo)
    check(f"a defender free at once strikes Stars of Ruin's charged cast at 1.5 m before its stars leave "
          f"(f{sr_launch:.1f}, roll gate f{sr_v['roll']}): it lands nothing ({sr_new[0]['value']:.2g}) and pays the R1 "
          f"(net {sr_new[0]['net']:.1f}, rolling it netted {sr_old[0]['net']:.1f}); held to f80 he rolls it as before "
          f"({sr_new[1]['net']:.1f})",
          sr_new[0]['strike'] and abs(sr_new[0]['value']) < 1e-6 and abs(sr_new[0]['p_punish'] - 1.0) < 1e-9
          and abs(sr_new[0]['net'] + A.PUNISH_HP) < 1e-6 and sr_old[0]['net'] > 0 and sr_launch > 13.31 + 20.0
          and not sr_new[1]['strike'] and not sr_new[1]['now']['strike'] and sr_new[1]['net'] == sr_old[1]['net'])
    # A TAE 795 window holds poise the R1 would break: Fortissax's Light Spear's 045010 f15-96
    # (row 180, `toughnessCorrectRate` 0.011: +3.3 menu poise, half the poise taken). A 100-poise R1
    # breaks 62 poise outside it and not inside (62 + 3.3 - 50 > 0).
    fx_mid = next(i for i, nm in sorted(m.names.items()) if nm.startswith("Fortissax's"))
    fx_v = m.evaluate(fx_mid, seal, {'fth': 80}, defender=dfn)[0]
    fx_ev = sorted({(h['frame'], h['ref']) for h in fx_v['hits']})
    fx_spec = {'events': fx_ev, 'hand': fx_v['startup'] - min(f for f, _ in fx_ev), 'anim': fx_v['anim'],
               'weapon': seal['base'], 'lead': fx_v['lead']}
    fx_w = m.cast_windows(fx_mid, fx_spec)
    fx_b = strike_breaks(Strikes([15.0], [100.0]), fx_w, 62.0)
    check(f"Fortissax's 045010 window ({[[round(x, 2) for x in w] for w in fx_w]}, hand-off f{fx_spec['hand']}) "
          f"holds a 100-poise R1 that breaks 62 poise outside it (break share {fx_b[:, 0].tolist()})",
          len(fx_w) == 1 and abs(fx_w[0][0] - fx_spec['hand'] - 15.0) < 0.11 and abs(fx_w[0][1] - fx_spec['hand'] - 96.0) < 0.11
          and abs(fx_w[0][2] - 3.3) < 1e-3 and abs(fx_w[0][3] - 0.5) < 1e-9 and fx_b[0, 0] == 1.0 and fx_b[1, 0] == 0.0)
    # The attacker's own guard (TAE JumpTable 3, `tae_guard`), read off the clips `GUARD_POKES` and a
    # thrusting shield's R1 name (`TAE`, every player TAE scanned 2026-11-13): the two-handed guard
    # poke holds 470 f0-42 over its f15-19 thrust, the R1 #1 461 from f14 (its hit is f13-16) to f45,
    # the spear's R1 from guard 460 f0-65 over f12-16.
    tg = {k: tae_guard(ATK.resolve_events(c, a)[2] or []) for k, (c, a) in
          {'poke': (57, 32900), 'r1': (57, 30000), 'spear': (36, 30900), 'sword': (3, 30000)}.items()}
    check(f"TAE guard windows: 2H thrusting shield guard poke {tg['poke']}, its R1 #1 {tg['r1']}, the spear's R1 from "
          f"guard {tg['spear']}, a straight sword R1 none {tg['sword']}",
          tg['poke'] == (((0.0, 42.0),), 470) and tg['r1'] == (((14.0, 45.0),), 461)
          and tg['spear'] == (((0.0, 65.0),), 460) and tg['sword'] == ((), None))
    # The same Stars of Ruin cast with a guard up over it (a synthetic `GUARD`: the Carian Thrusting
    # Shield two-handed at +25, judge 470), two pool R1s (a Dagger's and a Claymore's) breaking any
    # poise. Guarded, they break nothing: in a state that goes on the cast lands whole and he rolls it
    # as before, since striking now costs the caster only the guarded chip; in a state the guard
    # reaction ends, the strike stops it and costs him the chip (the guard model's pass share of the
    # R1 times `PUNISH_HP`), not the R1; with no stamina left the guard breaks, it stops and costs the
    # chip and one more R1.
    _, tt = m.ash()
    g_rows = [(tt.find_weapon('Dagger'), False), (tt.find_weapon('Claymore'), False)]
    g_cts = tt.find_weapon('Carian Thrusting Shield')

    def guarded(continues, bar, regen=0.0, add=0.0, charges=()):
        sk = Strikes([15.0, 20.0], [500.0, 500.0], [0, 1], g_rows, bar)
        gd = tuple(Guard(((0.0, 200.0),), g_cts, 25, 470, True, continues, 18, regen, charges))
        geo = [({**g, 'GUARD': gd}, fr) for g, fr in sr_geo]
        return net_parts(A, geo, sr_val, sr_sit[:1], sr_v['roll'], sk, defender=dfn, attacker_regen=add)[0]
    g_chip = [guard_block(r, Guard(((0.0, 200.0),), g_cts, 25, 470, True, True, 18)) for r in g_rows]
    g_go, g_end, g_brk = guarded(True, 146.0), guarded(False, 146.0), guarded(False, 10.0)
    chip = sum(b[0] for b in g_chip) / 2 * A.PUNISH_HP
    check(f"Stars of Ruin's charged cast under a guard: the Dagger and Claymore R1s pass {[round(b[0], 3) for b in g_chip]} "
          f"of their damage and take {[b[1] for b in g_chip]} stamina ({[b[2] for b in g_chip]} reaction); going on, "
          f"he rolls it again (net {g_go['net']:.1f} == {sr_old[0]['net']:.1f}); ended by the guard reaction, the "
          f"strike stops it for the chip (net {g_end['net']:.2f} == -{chip:.2f}); guard broken, chip and one more R1 "
          f"(net {g_brk['net']:.2f})",
          all(b is not None and 0.0 < b[0] < 0.2 for b in g_chip) and not g_go['strike']
          and abs(g_go['net'] - sr_old[0]['net']) < 1e-9 and g_end['strike'] and abs(g_end['value']) < 1e-6
          and abs(g_end['p_punish'] - 1.0) < 1e-9 and abs(g_end['net'] + chip) < 1e-6
          and abs(g_brk['net'] + chip + A.PUNISH_HP) < 1e-6)
    # The attacker's own regeneration before his guard opens (`Guard.regen`, his talismans'
    # `attacker_regen`): the thrusting shield R1 #1 and its two-handed guard poke hold TAE 225 at 0%
    # from frame 0 past their guard's start, so they give back nothing; a clip with no TAE 225 gives
    # its whole length. With 30 such frames and a bar 31 under the Claymore R1's guarded stamina
    # (left: bar - 18 + 45), the Claymore R1 breaks the guard at 45/s and not with Green Turtle's
    # +8 (53/s): the break's extra R1 goes, half the strikes' worth.
    rg = {k: tae_regen_frames(ATK.resolve_events(57, a)[2], tae_guard(ATK.resolve_events(57, a)[2])[0][0][0])
          for k, a in (('r1', 30000), ('poke', 32900))}
    g_bar = g_chip[1][1] - 31.0
    g_45, g_53 = guarded(False, g_bar, 30.0), guarded(False, g_bar, 30.0, 8.0)
    check(f"regeneration before the guard: R1 #1 {rg['r1']}, guard poke {rg['poke']} frames, no TAE 225 over 30 "
          f"frames {tae_regen_frames([], 30.0)}; a bar of {g_bar:g} with 30 such frames: the Claymore R1 breaks the "
          f"guard at 45/s (net {g_45['net']:.2f}) and not at 53/s (net {g_53['net']:.2f})",
          rg == {'r1': 0.0, 'poke': 0.0} and tae_regen_frames([], 30.0) == 30.0
          and g_chip[0][1] < g_chip[1][1] - 4.0
          and abs(g_45['net'] + chip + A.PUNISH_HP / 2) < 1e-6 and abs(g_53['net'] + chip) < 1e-6)
    # The attack's stamina is taken hit by hit, on the frame each hitbox is created (`Guard.charges`,
    # `FUN_1404428f0`): the guard is cut into phases at those frames, each with what has been taken by
    # its start, and a strike meets its phase's stamina. A bar 17 over the Claymore R1's guarded
    # stamina breaks the guard only once the 18 are spent: with them taken at frame 0 (as a guard
    # with no `charges` takes them) the Claymore breaks it; with them taken at frame 1000, after
    # every strike, neither R1 does.
    gpd = Guard(((0.0, 40.0), (50.0, 80.0)), g_cts, 25, 470, True, False, 30, 0.0, ((25.0, 10), (60.0, 20)))
    gph = guard_phases(gpd)
    gpw = strike_window((), [10.0, 30.0, 55.0, 70.0, 45.0], guard_phase_windows(gpd)).tolist()
    g_bar2 = g_chip[1][1] + 17.0
    g_old, g_now, g_late = (guarded(False, g_bar2), guarded(False, g_bar2, charges=((0.0, 18),)),
                            guarded(False, g_bar2, charges=((1000.0, 18),)))
    check(f"stamina taken per hit: phases {gph}, strike phases {gpw}; a bar of {g_bar2:g}: all 18 taken first "
          f"(net {g_old['net']:.2f}), at frame 0 (net {g_now['net']:.2f}), at frame 1000 (net {g_late['net']:.2f})",
          gph == [(((0.0, 25.0),), 0.0), (((25.0, 40.0), (50.0, 60.0)), 10.0), (((60.0, 80.0),), 30.0)]
          and guard_phases(gpd._replace(charges=())) == [(((0.0, 40.0), (50.0, 80.0)), 30.0)]
          and gpw == [0, 1, 1, 2, -1]
          and abs(g_old['net'] + chip + A.PUNISH_HP / 2) < 1e-6 and abs(g_now['net'] - g_old['net']) < 1e-9
          and abs(g_late['net'] + chip) < 1e-6)
    # A real attack carries its hits' charge frames: the Carian Thrusting Shield's two-handed guard
    # poke (a057_032900) guards f0-42 and creates its one hitbox at f15.
    gpc, gp2 = m.guard_poke('Carian Thrusting Shield')
    gpa = m.slot_attack('Carian Thrusting Shield', {'str': 30, 'dex': 30}, two_handed=gp2, defender=dfn, clip=gpc)
    gpg = gpa['guard']
    check(f"Carian Thrusting Shield guard poke: guard {gpg and gpg.windows}, cost {gpg and gpg.cost}, "
          f"charges {gpg and gpg.charges}",
          gpg is not None and gpg.windows == ((0.0, 42.0),) and len(gpg.charges) == 1
          and gpg.charges[0][0] == 15.0 and gpg.charges[0][1] == gpg.cost > 0)
    print(f'selftest ok ({n[0]} checks)')


def attack_scores(m, spec, stats, rl=150, slot='r1_1', guard_weapon='Kite Shield'):
    """One attack of `spec` ('Name[+level][:Affinity][@Skill]', `parse_weapon`) ranked as a punish
    over `punish_situations(rl)` against `SpellModel.defender(rl)` with `punish_strikes(rl)`: its
    `skill_attack` with @Skill, else `slot_attack` of `slot` ('2h_' in front: two-handed; 'guard':
    `guard_poke`, guarded by `guard_weapon` on a one-handed poke). Returns {'name', 'why', 'guard',
    'net', 'net_unguarded', 'strike', 'strike_unguarded', 'land'}: the weight-mean nets per whiff
    with the attacker's guard read (`GUARD`) and with it taken off the geometries (the model before
    2026-11-13, number for number), the weight share of situations the defender strikes in, and the
    mean worth that lands."""
    w = parse_weapon(spec)
    dfn = m.defender(rl)
    if w['skill']:
        d = m.skill_attack(w['weapon'], stats, w['affinity'], w['level'], w['skill'], dfn)
    else:
        two = slot.startswith('2h_')
        key = slot.removeprefix('2h_')
        if key == 'guard':
            got = m.guard_poke(w['weapon'])
            if got is None:
                return {'name': f"{w['weapon']} R1 from guard", 'why': 'no attack from a raised guard'}
            clip, two = got
            gw = None if clip[1] == GUARD_POKES['both_guard'][1] else guard_weapon
            d = m.slot_attack(w['weapon'], stats, w['affinity'], w['level'], two_handed=two, defender=dfn,
                              clip=clip, guard_weapon=gw)
        else:
            d = m.slot_attack(w['weapon'], stats, w['affinity'], w['level'], key, two, dfn)
    out = {'name': d['name'], 'why': d['why'], 'guard': None}
    if d['why']:
        return out
    guard = next((guard_of(g) for g, _ in d['geo'] if g.get('GUARD')), None)
    out['guard'] = guard and {'windows': list(guard.windows), 'weapon': guard.weapon, 'judge': guard.judge,
                              'continues': guard.continues}
    sit = punish_situations(rl)['situations']
    pairs = [(x['distance'], x['recovery']) for x in sit]
    wts = np.array([x['weight'] for x in sit], float)
    A = m.ash()[0]
    strikes = punish_strikes(rl)
    # His own guard regenerates with what the weapons he holds add (the Great Turtle Shell, +3); he
    # wears no talisman here.
    adds = _mod('er_mechanics_talismans', 'er-mechanics-talismans.py').equipment_regen_adds()['weapon']
    held = {x for x in (d.get('weapon'), guard.weapon if guard is not None else None) if x is not None}
    areg = sum(adds.get(x, 0.0) for x in held)
    out['attacker_regen'] = areg

    def score(geo):
        parts = net_parts(A, geo, d['value'], pairs, d['roll'], strikes, defender=dfn, attacker_regen=areg)
        net = np.array([p['net'] for p in parts], float)
        strike = np.array([bool(p['strike']) for p in parts], float)
        land = np.array([p['value'] for p in parts], float)
        return float(net @ wts / wts.sum()), float(strike @ wts / wts.sum()), float(land @ wts / wts.sum())
    out['net'], out['strike'], out['land'] = score(d['geo'])
    if guard is not None:
        bare = [({k: v for k, v in g.items() if k != 'GUARD'}, fr) for g, fr in d['geo']]
        out['net_unguarded'], out['strike_unguarded'], _ = score(bare)
    else:
        out['net_unguarded'], out['strike_unguarded'] = out['net'], out['strike']
    return out


def attack_main(a):
    """`--attack`: each attack's `attack_scores`, as a table or JSON."""
    m = SpellModel()
    stats = parse_stats(a.stats)
    rows = [attack_scores(m, spec, stats, a.rl, a.slot, a.guard_weapon) for spec in a.attack]
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    print(f"punish nets per whiff vs the RL {a.rl - WINDOW}-{a.rl + WINDOW} pool's spaced whiffs (median defender, "
          f"PvP rates); 'guard' reads the attacker's own guard, 'no guard' takes it off")
    print(f"{'attack':52s} {'net':>7s} {'no guard':>8s} {'struck':>7s} {'(no g.)':>7s}  guard windows")
    for r in sorted(rows, key=lambda r: -(r.get('net') or -1e9)):
        if r['why']:
            print(f"{r['name'][:52]:52s} {r['why']}")
            continue
        g = r['guard']
        gw = ' '.join(f'f{x:g}-{y:g}' for x, y in g['windows']) + f" judge {g['judge']}" + \
            (' goes on' if g['continues'] else '') if g else '-'
        print(f"{r['name'][:52]:52s} {r['net']:>7.1f} {r['net_unguarded']:>8.1f} {100 * r['strike']:>6.1f}% "
              f"{100 * r['strike_unguarded']:>6.1f}%  {gw}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--stats', default='', help='e.g. int=80,dex=20,fth=8 (unset stats are 0, DEX 10)')
    ap.add_argument('--catalyst', action='append', default=[],
                    help="a staff or seal, 'Name' or 'Name+10' (default max level); repeatable")
    ap.add_argument('--left', help='the off-hand, e.g. a second seal')
    ap.add_argument('--talismans', default='', help='talismans worn, comma-separated')
    ap.add_argument('--two-handed', action='store_true', help='two-hand the catalyst (STR x1.5)')
    ap.add_argument('--rl', type=int, default=150, help=f'median defender of corpus builds within RL +-{WINDOW}')
    ap.add_argument('--pve', action='store_true', help='no PvP rates (FinalDamageRateParam, atkPlayerDmgCorrectRate)')
    ap.add_argument('--show', action='append', default=[], help='spell to break down hit by hit; repeatable')
    ap.add_argument('--sort', default='one_hit', choices=('one_hit', 'full', 'full_per_fp', 'full_per_s', 'startup'),
                    help='default one_hit: the only column no hit-count rule moves')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--kit', action='store_true', help='the greedy kit by punish coverage, spell by spell')
    ap.add_argument('--slots', type=int, default=MEMORY_SLOTS, help='memory slots the kit may fill')
    ap.add_argument('--weapon', action='append', default=[],
                    help="--kit: a weapon the build carries or swaps to, 'Name[+level][:Affinity][@Skill]'; "
                         "a spell must beat its skill to earn a slot; repeatable")
    ap.add_argument('--no-ash-rivals', action='store_true',
                    help='--kit: do not make spells beat the spell-like weapon skills the stats can wield')
    ap.add_argument('--attack', action='append', default=[],
                    help="rank one weapon attack as a punish, 'Name[+level][:Affinity][@Skill]' (with @Skill "
                         "its skill, else --slot); repeatable. Prints the mean net per whiff with the "
                         "attacker's own guard read and ignored")
    ap.add_argument('--slot', default='r1_1',
                    help="--attack without @Skill: an attack slot key ('r1_1', 'run_r1', ...; '2h_' in front "
                         "two-handed), or 'guard' for its R1 from a raised guard (GUARD_POKES)")
    ap.add_argument('--guard-weapon', default='Kite Shield',
                    help="--slot guard on a spear, rapier, great spear or heavy thrusting sword: the left "
                         "weapon guarding (default 'Kite Shield')")
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.attack:
        return attack_main(a)
    if not a.catalyst:
        ap.error('at least one --catalyst is required')
    m = SpellModel()
    stats = parse_stats(a.stats)
    cats = [m.catalyst(c) for c in a.catalyst]
    tals = [x.strip() for x in a.talismans.split(',') if x.strip()]
    if a.kit:
        rounds, skipped, rivals = m.kit(cats, stats, tals, a.left, a.rl, a.two_handed, not a.pve, a.slots,
                                        [parse_weapon(w) for w in a.weapon], not a.no_ash_rivals)
        if a.json:
            print(json.dumps({'rounds': rounds, 'skipped': skipped, 'rivals': rivals}, indent=1))
            return 0
        share = punish_situations(a.rl)['whiff_share']
        print(f"punish kit vs the RL {a.rl - WINDOW}-{a.rl + WINDOW} pool's openers, backed out of on reaction "
              f"({100 * share:.1f}% whiff){'' if a.pve else ', PvP rates'}; value = mean damage landed less the "
              f"punish of a dodged cast, per whiff")
        best = rivals.pop('best of them', None)
        shown = sorted(((v, k) for k, v in rivals.items() if v is not None), reverse=True)[:RIVALS_SHOWN]
        print(f"rival weapon skills: {len(rivals)} scored, {sum(v is None for v in rivals.values())} with no "
              f"reach model; best of them per whiff {best if best is not None else 0.0:.1f}, the line a spell must beat")
        for v, k in shown:
            print(f"   {v:7.1f}  {k}")
        print(f"{'#':>2s} {'spell':34s} {'variant':8s} {'slots':>5s} {'value':>7s} {'gain':>7s} {'best':>6s}")
        for i, r in enumerate(rounds, 1):
            print(f"{i:>2d} {r['name'][:34]:34s} {r['variant'] or '':8s} {r['slots']:>5d} {r['value']:>7.1f} "
                  f"{r['gain']:>7.1f} {100 * r['best_share']:>5.1f}%")
        return 0
    only = {T._norm(s) for s in a.show} or None
    rows, skipped = m.table(cats, stats, tals, a.left, a.rl, a.two_handed, not a.pve, only)
    rev = a.sort != 'startup'
    rows.sort(key=lambda r: (r[1][a.sort] or 0) * (1 if rev else -1), reverse=True)
    if a.json:
        print(json.dumps({'rows': [dict(s, **v) for s, v in rows], 'skipped': skipped}, indent=1))
        return 0
    print(f"vs the median defender of RL {a.rl - WINDOW}-{a.rl + WINDOW}{'' if a.pve else ', PvP rates'}; "
          f"catalysts {', '.join(f'{c['name']} +{c['level']}' for c in cats)}")
    print(f"{'spell':34s} {'variant':8s} {'FP':>3s} {'start':>6s} {'act':>6s} {'one hit':>8s} {'full':>8s} "
          f"{'full/FP':>8s} {'full/s':>7s}")
    for s, v in rows[:a.top]:
        tag = ' (loop: first cycle)' if v['channel'] else ''
        print(f"{s['name'][:34]:34s} {v['variant']:8s} {v['fp']:>3d} {v['startup']:>6.1f} {v['act']:>6.1f} "
              f"{v['one_hit']:>8.1f} {v['full']:>8.1f} {v['full_per_fp'] or 0:>8.2f} {v['full_per_s'] or 0:>7.1f}{tag}")
        if only:
            for h in v['hits']:
                print(f"    ref{h['ref']} f{h['frame']:<5g} atk {h['atk']} x{h['count']:g}: AR "
                      f"{', '.join(f'{e} {x:g}' for e, x in h['ar'].items())} -> {h['damage']:.1f} "
                      f"({h['phys_type']}, PvP x{h['final_rate']:g})")
    if only and skipped:
        for nm, why in skipped.items():
            print(f'  {nm}: {why}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
