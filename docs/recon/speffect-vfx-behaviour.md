# What the 41 marked SpEffects carry through `vfxId`

Extracted from the installed regulation with `scripts/generate-speffect-vfx-dump.py`; raw values in
[`speffect-vfx-rows.json`](speffect-vfx-rows.json).

| | |
|---|---|
| regulation | `$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game/regulation.bin`, binder version `11711000` (game 1.17.1) |
| paramdefs | `SpEffectVfx.xml`, `SpEffect.xml` from `../fromsoftware-rs/tools/param-generator/params/eldenring` |
| `SpEffectVfxParam` | 1513 rows, 69 non-padding columns |
| `SpEffectParam` | 11354 rows (the committed `data/effect-master-catalog.json` is binder `11611000` / 11325 rows, i.e. a build behind) |

## The headline, before the tables

**`vfxId` does not explain the marks.** Of the 41 marked ids, 41 have a `vfxId`, and **5** land on a
`SpEffectVfxParam` row that sets any column with a gameplay consequence -- 4090, 4463, 4477, 295190,
295902, all five of them camouflage. A sixth, 295121, sets only the borderline `isVisibleDeadChr`.
The other 35 point at rows that set nothing but particle ids, sound ids, dummy-poly attach points and
play-arbitration priorities.

What the marked rows do carry is **`stateInfo`**, a `SpEffectParam` column -- 23 of 41 set it against
39.1% of the table -- and `stateInfo` values are an engine-side enum with no paramdef enumeration and
no param table behind them. `Unseen Form` (1467001) looks empty in the master catalog because it *is*
empty: it is one of three rows the spell applies, and it is the one that plays the cast flash.

So the question "what is behind `vfxId`" has an answer, and the answer is mostly "nothing you were
looking for". The behaviour you were hunting is split between `stateInfo` (opaque), a small set of
`SpEffectParam` columns the master catalog already carries, and -- for exactly the stealth effects --
the camouflage block in `SpEffectVfxParam`.

## 1. The 41, id by id

Columns at their paramdef default are omitted. `SpEffect` shows the non-default fields minus the
16 `vowType*` flags and the 13 `effectTarget*` flags (which are covered in section 4) and minus the
seven rate fields whose semantic default is 1. `vfx` shows the referenced `SpEffectVfxParam` row's
non-default columns; `--` means the row exists and sets nothing at all beyond the two columns
(`unknown_0x96`, `unknown_0x98`) that read 150 on 1511 of 1513 rows and are therefore a default the
paramdef fails to declare.

A **+** marks a vfx row that carries gameplay, not presentation.

| id | name | `stateInfo` | vfx | vfx row's non-default columns |
|---|---|---|---|---|
| 1555 | Winged Scythe - No Flask | 154 | 8515 | `existEffectForLarge=1`, `midstDmyId=905`, `midstSfxId=4250`, `playCategory=4`, `playPriority=20` |
| 3580 | Crimsonwhorl Bubbletear | 500 | 5826 | `isFinishFullbody=1`, `midstDmyId=905`, `midstSfxId=331401` |
| 3960 | Immunizing Cured Meat | -- | 5354 | `isFinishFullbody=1`, `isInitFullbody=1`, `midstDmyId=905`, `midstSfxId=301212`, `playCategory=3`, `playPriority=70` |
| 3961 | Invigorating Cured Meat | -- | 5356 | as 5354, `midstSfxId=301222` |
| 3962 | Clarifying Cured Meat | -- | 5358 | as 5354, `midstSfxId=301232` |
| 3963 | Dappled Cured Meat | -- | 5360 | as 5354, `midstSfxId=301242` |
| 3970 | Silver-Pickled Fowl Foot | 66 | 5362 | `isFinishFullbody=1`, `isInitFullbody=1`, `isMidstFullbody=1`, `midstDmyId=220`, `midstSfxId=301252`, `playCategory=3`, `playPriority=100` |
| 3971 | Gold-Pickled Fowl Foot | 76 | 5364 | as 5362, `midstSfxId=301262` |
| 4090 | (unnamed) | 8 | 130000 + | `useCamouflage=1`, `invisibleAtFriendCamouflage=1`, `effectInvisibleAtCamouflage=1`, `isHideFootEffect_forCamouflage=1` |
| 4201 | Golden Leaves Weather (Bonus Runes) | 76 | 5364 | shared with 3971 / 20501230 |
| 4463 | (unnamed) | -- | 104610 + | `useCamouflage=1`, `invisibleAtFriendCamouflage=1`, `effectInvisibleAtCamouflage=1`, `isHideFootEffect_forCamouflage=1`, `midstSeId=430207001`, `finishSeId=430207002` |
| 4477 | (unnamed) | -- | 104610 + | shared with 4463 |
| 13668 | (unnamed) | 154 | 50800 | `existEffectForLarge=1`, `midstDmyId=8`, `midstSfxId=632013`, `playCategory=4`, `playPriority=20` |
| 13685 | (unnamed) | 154 | 50800 | shared with 13668 |
| 13956 | (unnamed) | -- | 53610 | `midstDmyId=199`, `midstSfxId=642620` |
| 102000 | Deflecting Hardtear | -- | 23107000 | `midstDmyId=905`, `midstSfxId=331921` |
| 295121 | (unnamed) | -- | 57011 | `initDmyId=960`, `initSfxId=692`, `initSeId=999997805`, `isVisibleDeadChr=1` |
| 295190 | (unnamed) | -- | 57050 + | `useCamouflage=1`, `invisibleAtFriendCamouflage=1`, `isVisibleDeadChr=1` |
| 295902 | Clayman Corpse Hider 3 | -- | 57900 + | `useCamouflage=1`, `invisibleAtFriendCamouflage=1`, `isVisibleDeadChr=1` |
| 500610 | Albinauric Pot | 154 | 154 | `existEffectForLarge=1`, `midstDmyId=8`, `midstSfxId=300363`, `playCategory=4`, `playPriority=21` |
| 501220 | Deathsbane Jerky | -- | 5366 | `initDmyId=220`, `initSfxId=528152`, `midstDmyId=905`, `midstSfxId=301232`, `playCategory=3`, `playPriority=90` |
| 501310 | Immunizing White Cured Meat | -- | 5354 | shared with 3960 |
| 501320 | Invigorating White Cured Meat | -- | 5356 | shared with 3961 |
| 501330 | Clarifying White Cured Meat | -- | 5358 | shared with 3962 |
| 501340 | Dappled White Cured Meat | -- | 5360 | shared with 3963 |
| 511019 | Twiggy Cracked Tear | 160 | 5846 | `isFinishFullbody=1`, `midstDmyId=220`, `midstSfxId=331211` |
| 511027 | Purifying Crystal Tear | 440 | 5827 | `initDmyId=905`, `initSfxId=331600`, `isFinishFullbody=1`, `midstDmyId=905`, `midstSfxId=331601` |
| 1448000 | Lucidity | 438 | 1448000 | `initDmyId=220`, `initSfxId=523152` |
| 1467001 | Unseen Form | -- | 1467001 | `initDmyId=220`, `initSfxId=523412` |
| 1467002 | Unseen Form | 8 | 1467002 | `playCategory=1`, `playPriority=11` |
| 1467006 | Unseen Form | -- | 1467006 | `initDmyId=220`, `initSfxId=523413` |
| 1604000 | Flame Cleanse Me | 11 | 1604000, 1604001 | `initDmyId=220`, `initSfxId=524321` / `initSfxId=524322` |
| 1644000 | Cure Poison | 10 | 1644000 | `initDmyId=220`, `initSfxId=525083` |
| 1644100 | Lord's Aid | 10 | 1644100 | `initDmyId=220`, `initSfxId=4620` |
| 1644110 | Lord's Aid | 10 | 1644100 | shared with 1644100 |
| 1673000 | Law of Regression | 10 | 1673000 | `initDmyId=220`, `initSfxId=4630` |
| 1673020 | Law of Regression | 10 | 1673000 | shared with 1673000 |
| 1676000 | Law of Causality | 170 | 1676000 | `isMidstFullbody=1`, `midstDmyId=905`, `midstSfxId=528172`, `playCategory=3`, `playPriority=20` |
| 20501220 | Silver Horn Tender | 66 | 5362 | shared with 3970 |
| 20501230 | Golden Horn Tender | 76 | 5364 | shared with 3971 / 4201 |
| 20511060 | Glovewort Crystal Tear | 476 | 5846 | shared with 511019 |

Their `SpEffectParam` sides, for the rows where that is where the behaviour actually sits:

- Resistance grants: 3960/3961/3962/3963 set `change{Disease,Poison,Blood,Freeze,Madness,Sleep}ResistPoint = 100`; the `501xxx` White variants set 75 with a longer `effectEndurance`. 501220 sets `changeCurseResistPoint = 90`.
- Discovery and runes: 3970 and 20501220 set `itemDropRate` (0.5 and 0.6) with `stateInfo=66`; 3971, 4201 and 20501230 set only `stateInfo=76` -- so the rune bonus itself is engine-side behind state 76, not a param multiplier.
- Chains: 3580 sets `cycleOccurrenceSpEffectId=3583` and `replaceSpEffectId=3585`; 1676000 sets `replaceSpEffectId=1676008`; 295121 sets `replaceSpEffectId=295130`; 4463/4477 set `replaceSpEffectId` 4469/4474 plus `replanningOnFire=1`.
- 13956 sets `registFreezeChangeRate = 0` and `effectEndurance = -1` -- a permanent frost-immunity grant. `NpcParam` rows 44202000, 44202020, 44205000, 44205050, 56302000, 56302081, 56307081 carry it in `spEffectID16`.
- 102000 (Deflecting Hardtear) sets nothing but `effectEndurance=300`, `eraseOnBonfireRecover`, `iconId=20524`, `spCategory=20`, `vfxId`. Whatever the deflection is, it is not in this row and not in vfx row 23107000. Same shape as the `stateInfo`-only rows, minus even the `stateInfo`.

Where a mark is reachable in ordinary play, the referrer scan found it (`speffect_referrers` in the
JSON): `EquipParamGoods.refId_default` for every consumable and tear, `EquipParamAccessory.refId`
for the Scarab talismans, `Magic.refId*` for the incantations, `NpcParam.spEffectID*` for 13956 and
the corpse-hider family. Nothing in the scanned grant tables references 1555, 4090, 4201, 4463,
4477, 13668, 13685, 295121, 295190, 295902, 500610, 1448000, 1604000, 1644000/1644100/1644110,
1673000/1673020 -- those arrive from `AtkParam`, `Bullet`, behaviour/TAE or event scripts, which the
scan deliberately does not cover.

## 2. Which `SpEffectVfxParam` columns carry player-visible behaviour

Ranked by how much of the game's observable state the column moves, with the evidence. "Presentation"
below means it changes what is drawn or heard and nothing else.

### Behaviour, with direct evidence

1. **`useCamouflage`** (82 rows) -- switches on the camouflage system: the character is rendered at a
   reduced alpha and enemy perception treats it as hidden. Evidence: the rows carrying it are
   `Mimic's Veil` (5330), `Assassin's Gambit` (8526), `Concealing Veil` (360100), `Unseen Form`
   (1467000/1467005), `Miriam's Vanishing` (21430000), `Clayman Corpse Hider 3` (57900). It also
   tracks `stateInfo=8` closely in both directions: of the 24 `SpEffectParam` rows with that state,
   20 have a vfx row and 18 of those set `useCamouflage` (the exceptions are 10311 and 1467002).
2. **`camouflageMinAlpha`** (17 rows, values 1..95) -- the alpha floor in percent. Default 0, so a
   camouflage row that does not set it is fully transparent. Unseen Form uses 50; `Miriam's
   Vanishing` and `Clayman Corpse Hider 3` leave it at 0. This is the difference between "shimmer"
   and "gone".
3. **`camouflageBeginDist` / `camouflageEndDist`** (26 / 32 rows) -- the distance band the fade runs
   over, default -1 meaning no distance gating. `Assassin's Gambit` is 17/12, `Concealing Veil`
   17/16, `Unseen Form` neither. A distance-gated row hides you from things far away and not from
   things next to you.
4. **`invisibleAtFriendCamouflage`** (62 rows) -- hides the camouflaged character from *allies* as
   well as enemies. Never appears without `useCamouflage` (0 rows), so it is strictly a modifier, and
   the one named row that omits it is `Concealing Veil` (360100), which is also distance-gated
   (17/16) rather than unconditional. Whether that omission is the design intent or an oversight is
   not something the data says. See section 4.
5. **`halfCamouflage`** (5 rows) -- a semi-transparent variant; the named users are `Assassin's
   Gambit` (8526) and 99004/20001020.
6. **`transformProtectorId`** (8 rows) + **`isFullBodyTransformProtectorId`** (4) -- swaps the
   character's armour model for an `EquipParamProtector` row. Named users: `Rock Heart` (5040000),
   `Priestess Heart` (5050000), `Lamenter's Mask` (5170000), and a fist-weapon vfx (10000). This
   changes the model every other client draws.
7. **`isSilence`** (19 rows) -- mutes the character's own noise. The named users are exactly the
   quiet-movement set: `Soft Cotton` (5310-5314), `Mimic's Veil` (5330), `Assassin's Gambit` (8525),
   `Crepus's Vial` (360000), `Assassin's Approach` (1651000-1651004), `Black Knife Armor` (6018010).
   Six independent stealth items agreeing is about as strong as a name-only inference gets; the
   column's own description says only "is silence".
8. **`isInvisibleWeapon`** (3 rows) -- hides the held weapon. Named user: `Unseen Blade` (1466000).
   The description states it outright (`0:Wu Qi Biao Shi , 1:Wu Qi Fei Biao Shi `).
9. **`forceDeceasedType`** (4 rows) -- forces the player's alive/dead appearance. Named users:
   `Furled Finger's Trick-Mirror` (360800) and `Host's Trick-Mirror` (360900) -- the two items whose
   whole function is to misrepresent your role to other players. See section 4.
10. **`phantomParamOverwriteType`** (58 rows, values 1 and 2) + **`phantomParamOverwriteId`** (56) --
    forces a `PhantomParam` row, i.e. the phantom tint/appearance. Named users include `Host's
    Trick-Mirror` (id 61), `Player all black` (220), the two `Color` rows (200/201) and the
    `STRAGGLER` plan effects (210/211). Cosmetic in isolation, but the cosmetic is the multiplayer
    identity cue. See section 4.
11. **`wetAspectType`** (19 rows, values 1..19) -- selects a `WetAspectParam` row. Named users: `Oil
    Pot` / `Hefty Oil Pot` / `Oil-Soaked Tear` (5), frost weapons (4), `Ironjar Aromatic` (7), `Soap`
    (1), rain (1). Wetness is not purely cosmetic in this game, but the consequence lives in
    `WetAspectParam`, which this dump does not extract.
12. **`effectType`** (120 rows, values 1 and 2) -- which hand's enchant slot the effect occupies.
    Evidence is unambiguous: every grease row named "- Right" sits on a vfx row with `effectType=1`
    and every "- Left" on `effectType=2`. It gates whether a second enchant can coexist, so it is
    behaviour rather than looks.
13. **`isVisibleDeadChr`** (146 rows) -- the vfx keeps drawing on a corpse. Borderline: it decides
    whether a dead body still shows the effect, which is information other players read.

### Presentation only

`midstSfxId` (995), `midstDmyId` (915), `midstSeId` (323), `initSfxId` (136), `initDmyId` (176),
`initSeId` (23), `finishSfxId` (45), `finishSeId` (9), `finishDmyId` (47), `isMidstFullbody` (92),
`isInitFullbody` (18), `isFinishFullbody` (47), `existEffectForLarge` (74), `existEffectForSoul` (1),
`traceSfxIdOffsetType` (119), `SfxIdOffsetType` (2), `isUseOffsetEnchantSfxSize` (93),
`enchantStartDmyId_0..7` / `enchantEndDmyId_0..1` (133 down to 1), `soulParamIdForWepEnchant` (65 --
the enchant's phantom tint; `Holy`=10, `Magic`=1, `Frost`=6, `Sleep`=12 across the grease rows),
`materialParamId` / `materialParamInitValue` / `materialParamTargetValue` / `materialParamFadeTime`
(74/45/53/64 -- a material fade, e.g. Jellyfish Shield and Rykard's phase change),
`effectInvisibleAtCamouflage` (37 -- suppresses this vfx while camouflaged),
`isHideFootEffect_forCamouflage` (24), `footEffectOffset` (35), `footDecalMaterialOffsetOverwriteId`
(64).

`playCategory` (255 rows) and `playPriority` (256) are arbitration: when several effects want to
draw in the same category the lower priority wins. They change which particle you see, not what
happens.

### Columns that are dead in this regulation

Never non-default across all 1513 rows: `decalId1`, `decalId2`, `enchantEndDmyId_2..7`,
`footEffectPriority`, `unknown_0x97`, `unknown_0x99`. `unknown_0x96` and `unknown_0x98` are 150 on
1511 rows and 67 on 2, i.e. an undeclared default rather than a signal. `unknown_0x9a` is set on one
row and `unknown_0x2f_7` on three; no interpretation is available for any of them.

## 3. The three specific questions

### `Unseen Form` 1467001 -- nothing makes it invisible

1467001 is not the invisibility. `Magic` row 4670 -- unnamed in the regulation, identified as the
spell by the fact that it is the only row referencing all three -- applies `refId1 = 1467000`,
`refId2 = 1467001`, `refId4 = 1467002`.

- **1467000** is the effect. `SpEffectParam`: `effectEndurance=30`, `sightSearchEnemyRate=0.4`,
  `stateInfo=8`, `iconId=20496`. `SpEffectVfxParam` row 1467000: `useCamouflage=1`,
  `camouflageMinAlpha=50`, `invisibleAtFriendCamouflage=1`. So: enemy sight range against you is
  multiplied by 0.4, you render at 50% alpha, and allies do not see you either.
- **1467001** (marked) sets `effectEndurance=1`, `spCategory=20`, `isContractSpEffectLife=1` and
  nothing else; its vfx row is `initDmyId=220`, `initSfxId=523412`. A one-second carrier for the
  cast flash.
- **1467002** (marked) is 1467000 minus the two fields that matter: same 30-second duration and same
  `stateInfo=8`, but no `sightSearchEnemyRate`, no `iconId`, and a vfx row (1467002) that sets only
  `playCategory=1`, `playPriority=11` -- a placeholder that claims the play slot so a competing effect
  in category 1 does not draw.
- **1467005 / 1467006** are the mounted twins (`effectTargetPcHorse=1`); 1467005 carries the same
  `sightSearchEnemyRate=0.4` and camouflage as 1467000.

So the marked `Unseen Form` rows are the two inert components. The one that hides you, 1467000, is
not in the marked set. `stateInfo=8` is the shared flag across all 24 camouflage-capable rows
(`Assassin's Gambit` 1767, `Miriam's Vanishing` 21430000, 4090, 4380, 8551, 10310, 10991, 11434,
11658, 13056, 13942, 99004, 20001020, 20004380 and others) -- but what the engine does with the value
8 is not derivable from param data, since `stateInfo` has no enum in the paramdef and no param table
behind it.

### `Clayman Corpse Hider 3` 295902 -- full invisibility, including to allies

`SpEffectParam`: `effectEndurance=-1` (permanent), `dontDeleteOnDead=1`, `vfxId=57900`, nothing else.

`SpEffectVfxParam` row 57900: `useCamouflage=1`, `invisibleAtFriendCamouflage=1`,
`isVisibleDeadChr=1`. `camouflageMinAlpha` is left at its default 0 and both camouflage distances at
-1, which means no distance gating and no alpha floor: **fully transparent at every range, to allies
as well as enemies, permanently, and the effect survives death.** 295190 is the same row shape
against vfx 57050 with identical columns.

This is the strongest gameplay payload in the marked set, and the one that would be most obviously
wrong to push at another player: it is permanent, it has no self-clear, and the target would be
invisible to everyone including themselves-as-seen-by-teammates.

### Row 3700, `stateInfo=299` -- not derivable

`SpEffectParam` 3700: `effectEndurance=7200` (two hours), `iconId=10035`, `spCategory=20`,
`stateInfo=299`, `vfxId=299`. No other non-default field.

Three things are measured and none of them answer the question:

- **`vfxId=299` is a dangling reference.** There is no row 299 in `SpEffectVfxParam`. The `vfxId`
  contributes nothing.
- **`stateInfo=299` occurs exactly once** in 11354 rows, so there is no sibling row to compare
  against and no item or spell in the scanned grant tables (`EquipParamGoods`, `Magic`,
  `SwordArtsParam`, `EquipParam*`, `NpcParam`, `SpEffectSetParam`, `CharaInitParam`, ...) references
  3700 at all.
- **The one neighbour is row 3710**: same `iconId=10035`, same `spCategory=20`, `stateInfo=300`,
  `effectEndurance=-1`, no `vfxId`. A pair of otherwise-empty rows sharing a status icon with
  consecutive state ids and no referrer anywhere.

I cannot say what 3700 does. The param data says only that it is a two-hour status with a shared
icon and an engine-side state that nothing else in the regulation uses. The independent scan in
[`marked-effect-provenance.md`](marked-effect-provenance.md) reached the same verdict over all 194
params rather than the 16 here, so "no granter" is not an artefact of my narrower scan; what this
extraction adds is that the `vfxId` side is a dangling reference too. Answering it needs either the
menu `FMG` text for icon 10035 or a look at the `stateInfo` switch in the executable; neither is in
this extraction.

## 4. Multiplayer and PvP applicability gates

**Nothing in `SpEffectVfxParam` gates whether an effect *applies* in multiplayer.** The applicability
gates are all in `SpEffectParam` -- `isDisableNetSync` and the `effectTarget*` family; for the engine
rule built on the first of those, see [`speffect-multiplayer-gate.md`](speffect-multiplayer-gate.md).
What `SpEffectVfxParam` has are three columns that change what *other players see*, which is a
different and narrower thing:

| column | what it decides | named evidence |
|---|---|---|
| `invisibleAtFriendCamouflage` | whether teammates also lose sight of you | present on 62 of the 82 camouflage rows, never present without `useCamouflage`; the one named row that omits it is `Concealing Veil` (360100) |
| `forceDeceasedType` | whether you render as alive or dead to others | `Furled Finger's Trick-Mirror` (360800), `Host's Trick-Mirror` (360900) -- the two items whose function is to disguise your multiplayer role |
| `phantomParamOverwriteType` / `phantomParamOverwriteId` | forces the phantom tint others see | `Host's Trick-Mirror` (61), `Player all black` (220), `Color` (200) / `Color (Puppet)` (201) |

Add `transformProtectorId` if a swapped armour model counts, which for a PvP identity question it
probably does.

The real applicability gate is the `effectTarget*` family on `SpEffectParam`, and it separates three
of the 41 sharply. Baseline across all 11354 rows:

| flag | rows set | share |
|---|---|---|
| `effectTargetSelfTarget` | 10219 | 90.0% |
| `effectTargetGhost` | 7779 | 68.5% |
| `effectTargetLive` | 7778 | 68.5% |
| `effectTargetPlayer` | 7752 | 68.3% |
| `effectTargetAI` | 7695 | 67.8% |
| `effectTargetFriend` | 7646 | 67.3% |
| `effectTargetSelf` | 7164 | 63.1% |
| `effectTargetEnemy` | 6990 | 61.6% |
| `effectTargetOpposeTarget` | 4985 | 43.9% |
| `effectTargetFriendlyTarget` | 4749 | 41.8% |
| `effectTargetAttacker` | 84 | 0.7% |
| `effectTargetPcHorse` | 24 | 0.2% |
| `effectTargetPcDeceased` | 0 | 0.0% |

Of the 41 marks, **13956, 4463 and 4477 have `effectTargetPlayer = 0`** -- 4463 and 4477 have every
target flag clear except `effectTargetSelfTarget`, and 13956 has every one clear. They are applied by
id from `NpcParam` / behaviour data, bypassing the filter. 38 of 41 have `effectTargetPlayer = 1`.

There is **no covenant gate in the marked set**: `vowType0..15` are all 1 on all 41, and the baseline
is 11328-11330 of 11354 rows for every one of the 16 flags, so the column is effectively constant in
this regulation and carries no signal.

Two fields from the ER paramdef are worth naming because they do *not* exist and a rule must not
reach for them: there is no `effectTargetWhitePhantom` and no `effectTargetBlackPhantom`. The
phantom-role distinction is not expressed in `SpEffectParam` at all.

## 5. What this does and does not settle

Proven from the data:

- The mapping from all 41 ids to their vfx rows and every non-default column on those rows.
- That only 5 of the 41 reach a vfx row with gameplay content, and that in all 5 cases the content is
  the camouflage block.
- The meaning of `useCamouflage`, `camouflageMinAlpha`, the camouflage distances,
  `invisibleAtFriendCamouflage`, `isSilence`, `isInvisibleWeapon`, `transformProtectorId`,
  `forceDeceasedType`, `phantomParamOverwrite*` and `effectType`, each from the set of named items
  that use it.
- That `Unseen Form` 1467001 carries no invisibility and 1467000 does.
- That `Clayman Corpse Hider 3` is permanent full invisibility including to allies.
- That `effectTargetPlayer` separates 3 of the 41 from the rest.

Not settled:

- What any particular `stateInfo` value means. The values on the marked set (8, 10, 11, 66, 76, 154,
  160, 170, 438, 440, 476, 500) can be *grouped* by which other rows share them -- 10 with
  `Neutralizing Boluses`, 11 with `Preserving Boluses`, 438 with `Stimulating Boluses`, 66 with
  `Silver Scarab`, 76 with `Gold Scarab` -- which is suggestive of cure-poison / cure-rot /
  cure-sleep / item-discovery / rune-gain respectively, but that is inference from company kept, not
  measurement. The enum itself is in the executable.
- What row 3700 does, per section 3.
- What `Deflecting Hardtear` (102000) does. Neither param carries it.
- `unknown_0x96`, `unknown_0x97`, `unknown_0x98`, `unknown_0x99`, `unknown_0x9a`, `unknown_0x2f_7`.
- Anything behind `WetAspectParam`, `MaterialExParam`, `PhantomParam` or `EquipParamProtector`, since
  none of the 41's vfx rows reference them.

## Reproducing

```bash
python3 scripts/generate-speffect-vfx-dump.py \
  --detail-ids docs/recon/speffect-vfx-detail-ids.jsonc \
  --output docs/recon/speffect-vfx-rows.json
```

Defaults resolve the regulation from `ER_GAME_DIR` / `ER_REGULATION_BIN`, the paramdefs from the
sibling `fromsoftware-rs` checkout with a Paramdex fallback, and `--detail-ids` from the selector's
own `er-net-effects-marked.jsonc` in the game directory. The JSON stores each row as its non-default
columns against `column_defaults`, so a full row is `column_defaults` overlaid with `fields`.
