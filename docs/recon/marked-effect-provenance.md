# What grants the 41 hand-marked SpEffects, and what the game tells the player they do

`crates/er-net-effects` applies raw SpEffect ids to the local player and, with network sync on, to
other players in a Seamless Co-op session. Forty-one ids were hand-marked as "never push this at
another player" (`crates/er-net-effects/src/marked_effects.rs`). This file names, for each of them,
the item / spell / weapon / enemy that grants it in the vanilla game, the row that does the
granting, the text the game shows the player, and what the effect does to a character. Id `3700`
is unmarked and was traced alongside them because it was unidentified.

The point of the exercise is the rule that separates these from the ~800 other entries in the
visuals-only catalogs; this file is the input to that rule, not the rule.

## How this was derived

The lookup is backwards from the usual direction: SpEffect id -> whatever references it.

* **Params.** Every param in the installed `regulation.bin` (194 of them, none skipped) was read
  through Smithbox's `Andre.Formats` / `SoulsFormats`, with Paramdex `Defs` applied by `ParamType`,
  and every integer cell whose field name matches `speffect|refid|refvirtual|effectid|refcategory`
  was compared against the target ids. Chains (`replaceSpEffectId`, `cycleOccurrenceSpEffectId`)
  were followed upward to their roots and each root re-scanned, until either a non-`SpEffectParam`
  referrer appeared or the closure was exhausted. For the ids that came back empty, a second pass
  scanned **every** integer field of every param; the results were all coincidental collisions
  (`SoundBankId`, `sortId`, `baseChangePrice`, `chrId`) and are listed under
  [No granter found](#no-granter-found).
* **Text.** `msg/engus/item.msgbnd.dcx` and its two DLC siblings, already unpacked to
  `/home/banon/projects/er-msg/engus/`. `GoodsName`/`GoodsCaption`/`GoodsInfo` are keyed by the
  `EquipParamGoods` row id; spells are `EquipParamGoods` rows whose id equals their `Magic` row id,
  so the spell text lives in the Goods FMGs too. `MagicName.fmg` holds nothing but two dummies.
* **Fields.** `data/effect-master-catalog.json` (11325 rows, `SpEffectParam` from the same
  regulation) for the non-default fields of each row.

Reproduce with `target/speffect-granter-scan/` (throwaway, gitignored):

```bash
bash target/speffect-granter-scan/run.sh scan out.json <id>...      # speffect-ish fields
bash target/speffect-granter-scan/run.sh scanall out.json <id>...   # every integer field
bash target/speffect-granter-scan/run.sh dump <Param> <rowId>...    # one row, non-default cells
```

`target/` is wiped by `cargo clean`, so if that directory is gone, rebuild it the way
`scripts/generate-effect-master-catalog.py` builds its own generator: a ~100-line `Program.cs`
plus a `net9.0` csproj referencing `Andre.Formats.dll` and `Andre.SoulsFormats.dll` out of
`$SMITHBOX_BINARY_DIR` (`$HOME/.local/share/smithbox/app` here), run under
`DOTNET_ROLL_FORWARD=Major`. The whole program is `SFUtil.DecryptERRegulation` ->
`Param.ReadIgnoreCompression` per binder file -> `ApplyParamdef` with the Paramdex def whose
`<ParamType>` matches -> compare cells.

`refCategory` in `Magic` and `EquipParamGoods` decides how a `refId` reads: **1 = Bullet,
2 = SpEffect**. That distinction matters below, and it is also what rules out three apparent
hits: `BehaviorParam 102000`, `BehaviorParam_PC 100500610` and `BehaviorParam_PC 100501310` carry
`refCategory = 0` (Attack), so their `refId` values are `AtkParam` ids that merely collide
numerically with SpEffect ids `102000`, `500610` and `501310`.

## The table

33 of the 42 trace to a granter. 9 do not.

| SpEffect | Granter (param row) | In-game name | In-game one-liner | What it does to a character |
|---|---|---|---|---|
| 1555 | `AtkParam_Pc` 301906900 / 301906901 `spEffectId1`, the attacks of `SwordArtsParam` 1189 on `EquipParamWeapon` 19060000 | Winged Scythe, skill "Angel's Wings" | *The white wings impede recovery actions using a flask of tears.* | 20 s, `stateInfo` 154 -- seals flask use on whoever is hit. |
| 3580 | `EquipParamGoods` 11020 `refId_default` | Crimsonwhorl Bubbletear | Converts damage received into HP | 15 s; cycles 3583 and chains 3585, `stateInfo` 500 -- incoming non-physical damage is converted to healing. |
| 3700 | **no granter found** | -- | -- | 7200 s (2 h), `stateInfo` 299, `vfxId` 299, `iconId` 10035. The only row in the game with `stateInfo` 299. |
| 3960 | `EquipParamGoods` 1110 `refId_default` | Immunizing Cured Meat | Temporarily boosts immunity | 60 s, `changePoisonResistPoint` / `changeDiseaseResistPoint` +100. |
| 3961 | `EquipParamGoods` 1120 `refId_default` | Invigorating Cured Meat | Temporarily boosts robustness | 60 s, `changeBloodResistPoint` / `changeFreezeResistPoint` +100. |
| 3962 | `EquipParamGoods` 1130 `refId_default` | Clarifying Cured Meat | Temporarily boosts focus | 60 s, `changeSleepResistPoint` / `changeMadnessResistPoint` +100. |
| 3963 | `EquipParamGoods` 1140 `refId_default` | Dappled Cured Meat | Temporarily boosts immunity, robustness and focus | 60 s, all six resistance points +100. |
| 3970 | `EquipParamGoods` 1190 `refId_default` | Silver-Pickled Fowl Foot | Temporarily boosts item discovery | 180 s, `itemDropRate` +0.5, `stateInfo` 66 -- changes what the character's kills drop. |
| 3971 | `EquipParamGoods` 1200 `refId_default` | Gold-Pickled Fowl Foot | Boosts rune acquisition for a time | 180 s, `soulRate` 1.3, `stateInfo` 76 -- +30 % runes from kills. |
| 4090 | **no granter found** | -- | -- | 10 s, `stateInfo` 8 (the invisibility state), `spCategory` 200, `vfxId` 130000 -- the only row using that VFX. |
| 4201 | **no granter found** | (Paramdex calls it "Golden Leaves Weather (Bonus Runes)") | -- | Permanent, `soulRate` 1.05, `stateInfo` 76 -- +5 % runes; same VFX 5364 and `stateInfo` as the rune-boost items. |
| 4463 | **no granter found** | -- | -- | 1 s AI timer, `spCategory` 100, `categoryPriority` 210, `replanningOnFire`; its `replaceSpEffectId` 4469 **does not exist** in `SpEffectParam`, so the chain dead-ends. |
| 4477 | **no granter found** | -- | -- | 1 s AI timer, member of the closed ring 4474 -> 4475 -> 4476 -> 4477 -> 4474; nothing outside `SpEffectParam` enters it. |
| 13668 | **no granter found** | -- | -- | 25 s, `stateInfo` 154 -- the flask seal, mechanically the Albinauric Pot's effect with a different icon (5018) and VFX (50800). |
| 13685 | **no granter found** | -- | -- | Byte-identical to 13668. |
| 13956 | `NpcParam` 44202000 / 44202020 / 44205000 / 44205050 / 56302000 / 56302081 / 56307081 `spEffectID16` -- Giant Crayfish | -- (enemy attribute, no item text) | Permanent, `registFreezeChangeRate` 0 (no frost buildup) and `soulStealRate` 0. |
| 102000 | `EquipParamGoods` 2011070 `refId_default`; also `EquipParamAccessory` 204000 `refId` (`refCategory` 2) -> SpEffect 20511070, a permanent ticker (`motionInterval` 5) whose `cycleOccurrenceSpEffectId` re-applies 102000. That accessory row has `sortId` 999999, no name in `AccessoryName.fmg` and no Paramdex name, and no neighbouring rows exist at 200000 / 203000 / 204010, so it is a carrier row rather than a wearable talisman | Deflecting Hardtear | Enhances spontaneous guard in mixed physick | 300 s -- strengthens the deflect window: damage negation and guard poise right after assuming a guard, and stronger guard counters. |
| 295121 | **no granter found** | -- | -- | 1.2 s, `vfxId` 57011, `dontDeleteOnDead`, chains to 295130. One frame of the spirit-summon dissolve cascade (295100..295139 -> 295190). |
| 295190 | **no granter found** | -- | -- | Permanent, `vfxId` 57050, `dontDeleteOnDead`, no mechanical field at all -- the terminal frame of that cascade, which never expires. |
| 295902 | `NpcParam` 137502000 "[Spirit Summon] Clayman" `spEffectID24` -> 295900 -> 295901 -> 295902 | -- (spirit ash behaviour, no item text) | Permanent, `vfxId` 57900, `dontDeleteOnDead` -- the Clayman's corpse-hiding visual, applied on death (`conditionHp` 0) and surviving it. |
| 500610 | `EquipParamGoods` 610 `refId_default` -> `Bullet` 10061000 -> `HitBulletID` 10061001 `spEffectId0`. Enemy `Bullet` 4370121 (Castle Foot Soldier) and 205650121 (DLC Messmer Foot Soldier) apply the same row. | Albinauric Pot | Uses FP. Throw to impede healing using a flask of tears. | 25 s, `stateInfo` 154 -- seals flask use on whoever the pot hits. |
| 501220 | `EquipParamGoods` 1220 `refId_default` | Deathsbane Jerky -- the English FMG ships the name as `[ERROR]Deathsbane Jerky`, FromSoft's marker for an unused row | Boost instant death resistance for a short time | 60 s, `changeCurseResistPoint` +90. |
| 501310 | `EquipParamGoods` 1310 `refId_default` | Immunizing White Cured Meat | Temporarily boosts immunity | 120 s, poison/disease resistance +75 -- longer and weaker than the plain cured meat. |
| 501320 | `EquipParamGoods` 1320 `refId_default` | Invigorating White Cured Meat | Temporarily boosts robustness | 120 s, blood/frost resistance +75. |
| 501330 | `EquipParamGoods` 1330 `refId_default` | Clarifying White Cured Meat | Temporarily boosts focus | 120 s, sleep/madness resistance +75. |
| 501340 | `EquipParamGoods` 1340 `refId_default` | Dappled White Cured Meat | Temporarily boosts immunity, robustness and focus | 120 s, all six resistance points +75. |
| 511019 | `EquipParamGoods` 11019 `refId_default` | Twiggy Cracked Tear | Temporarily stops rune loss on death | 180 s, `stateInfo` 160 -- the character keeps their runes when they die. |
| 511027 | `EquipParamGoods` 11027 `refId_default` | Purifying Crystal Tear | Purifies the curse of the Lord of Blood | Permanent until rest (`effectEndurance` -1, `eraseOnBonfireRecover`), `stateInfo` 440, `spCategory` 1006 -- removes Mohg's blood curse. |
| 1448000 | `Magic` 4480 `refId1` (`refCategory1` 1) -> `Bullet` 10448000 `spEffectId0` | Lucidity | Alleviates buildup of sleep and madness | Instant, `stateInfo` 438 -- drains sleep and madness buildup. |
| 1467001 | `Magic` 4670 `refId2` (`refCategory2` 2, direct) | Unseen Form | Makes the caster semi-invisible | 1 s, VFX only -- the puff at the moment of casting. |
| 1467002 | `Magic` 4670 `refId4` (`refCategory4` 2, direct) | Unseen Form | Makes the caster semi-invisible | 30 s, `stateInfo` 8 -- the invisibility itself, without the enemy-sight reduction that the companion row 1467000 carries. |
| 1467006 | `Magic` 4670 `refId3` (`refCategory3` 1) -> `Bullet` 10467001 `spEffectId1`; the same bullet's `spEffectId0` is 1467005 | Unseen Form (the mount leg: *While on horseback, effect extends to cover the mount*) | Makes the caster semi-invisible | 1 s, VFX only -- the puff on whatever the bullet touches. |
| 1604000 | `Magic` 6040 `refId1` (`refCategory1` 1) -> `Bullet` 10604000 `spEffectId0` | Flame, Cleanse Me | Alleviates buildup & cures poison and scarlet rot | Instant, `stateInfo` 11 -- cures poison and scarlet rot and drains their buildup. |
| 1644000 | `Magic` 6440 `refId1` (`refCategory1` 1) -> `Bullet` 10644000 `spEffectId0` | Cure Poison | Alleviates poison buildup and cures poison | Instant, `stateInfo` 10. |
| 1644100 | `Magic` 6441 `refId1` (`refCategory1` 1) -> `Bullet` 10644100 "Lord's Aid" `spEffectId0` | Lord's Aid | Alleviates poison, blood loss, sleep buildup for self and allies | Instant, `stateInfo` 10 -- the caster's half. |
| 1644110 | `Magic` 6441 `refId3` (`refCategory3` 1) -> `Bullet` 10644110 "Lord's Aid - Allies" `spEffectId0` | Lord's Aid | (same) | Instant, `stateInfo` 10 -- the half the bullet carries to nearby allies. |
| 1673000 | `Magic` 6730 `refId2` (`refCategory2` 1) -> `Bullet` 10673000 `spEffectId0` | Law of Regression | Heals all ailments and dispels all special effects | Instant, `stateInfo` 10 -- clears every negative status, strips active special effects, and reveals mimicry. |
| 1673020 | `Magic` 6730 `refId5` (`refCategory5` 1) -> `Bullet` 10673010 `spEffectId0` | Law of Regression | (same) | Instant, `stateInfo` 10 -- the second bullet's copy of the same dispel. |
| 1676000 | `Magic` 6760 `refId2` (`refCategory2` 2, direct); the NPC copy `Magic` 53245 uses the same row | Law of Causality | Retaliates upon receiving a number of blows | 120 s, `stateInfo` 170, chains to 1676008 -- arms an automatic counter-attack that fires after enough blows are taken. |
| 20501220 | `EquipParamGoods` 2001220 `refId_default` | Silver Horn Tender | Temporarily boosts item discovery | 180 s, `itemDropRate` +0.6, `stateInfo` 66. |
| 20501230 | `EquipParamGoods` 2001230 `refId_default` | Golden Horn Tender | Boosts rune acquisition for a time | 180 s, `soulRate` 1.4, `stateInfo` 76 -- +40 % runes from kills. |
| 20511060 | `EquipParamGoods` 2011060 `refId_default` | Glovewort Crystal Tear | Enhances attacks of spirits in mixed physick | 180 s, `stateInfo` 476 -- raises the attack power of the character's summoned spirits. |

## Full in-game text

Line breaks are the game's own.

**Goods 610 -- Albinauric Pot**
> Craftable item prepared using a ritual pot.
> Enchanted by sorceries of the Cuckoos.
>
> Consumes FP. Throw at enemies to impede recovery actions using a flask of tears for a certain duration.
>
> The Knights of the Cuckoos do declare. Behold, thy defiled blood. Unlike any humor that flows in our grand realm.

**Goods 1110 -- Immunizing Cured Meat**
> Cured strip of meat, dried out after pickling in a green medicinal solution.
> Craftable item.
>
> Temporarily boosts immunity.
>
> Higher immunity helps to mitigate the buildup of various poisons and scarlet rot.

**Goods 1120 -- Invigorating Cured Meat**
> Cured strip of meat, dried out after pickling in a red medicinal solution.
> Craftable item.
>
> Temporarily boosts robustness.
>
> Higher robustness helps to mitigate the buildup of frost and blood loss.

**Goods 1130 -- Clarifying Cured Meat**
> Cured strip of meat, dried out after pickling in a purple medicinal solution.
> Craftable item.
>
> Temporarily boosts focus.
>
> Higher focus helps to mitigate the buildup of sleep and madness.

**Goods 1140 -- Dappled Cured Meat**
> Cured strip of meat, dried out after pickling in a dappled medicinal solution.
> Craftable item.
>
> Temporarily boosts immunity, robustness, and focus.

**Goods 1190 -- Silver-Pickled Fowl Foot**
> Four-toed foot of a fowl, pickled in a silvery medicinal solution.
> Craftable item.
>
> Temporarily boosts item discovery.
>
> Since old times, the needy would scrape the meat clean even from a fowl's claw.

**Goods 1200 -- Gold-Pickled Fowl Foot**
> Four-toed foot of a fowl, pickled in a golden medicinal solution.
> Craftable item.
>
> Boosts the amount of runes obtained from defeating enemies for a certain duration.
>
> Since old times, the needy would scrape the meat clean even from a fowl's claw.

**Goods 1220 -- `[ERROR]Deathsbane Jerky`**
> A grey colored cured liver, dried out
> after pickling in a medicinal solution.
> Craftable item.
>
> Boosts instant death resistance
> for a short time.

**Goods 1310 / 1320 / 1330 / 1340 -- White Cured Meats**
> A white sliced meat, dried out after pickling in a *green / red / purple / dappled* medicinal solution.
> Craftable item.
>
> Temporarily boosts *immunity / robustness / focus / immunity, robustness, and focus*.
>
> Lasts longer than traditional cured meat, but with reduced effectiveness.

**Goods 11019 -- Twiggy Cracked Tear**
> A crystal tear formed slowly over the ages where the Erdtree's bounty falls to the ground.
>
> Can be mixed in the Flask of Wondrous Physick.
> The resulting concoction prevents one's runes from being lost upon death. However, the effect lasts only for a short time.

**Goods 11020 -- Crimsonwhorl Bubbletear**
> A crystal tear formed slowly over the ages where the Erdtree's bounty falls to the ground.
>
> Can be mixed in the Flask of Wondrous Physick.
> The resulting concoction converts incoming damage into recovered HP instead. However, physical damage cannot be converted.
>
> This effect is only brief and will quickly expire.

**Goods 11027 -- Purifying Crystal Tear**
> A crystal tear formed slowly over the ages where the Erdtree's bounty falls to the ground.
>
> Can be mixed in the Flask of Wondrous Physick.
> The resulting concoction purifies the curse from Mohg, Lord of Blood's terrifying rite of blood.

**Goods 2001220 -- Silver Horn Tender**
> Old currency used by hornsent made by coating spiral horns with silver.
>
> Temporarily boosts item discovery.
> Can also be sold for a high price.
>
> These trinkets were once symbolic of society's upper echelons.

**Goods 2001230 -- Golden Horn Tender**
> Old currency used by hornsent made by coating spiral horns with gold.
>
> Boosts the amount of runes obtained from defeating enemies for a certain duration. Can also be sold for a high price.
>
> Once bestowed upon inquisitors as an honor.

**Goods 2011060 -- Glovewort Crystal Tear**
> A crystal tear formed slowly over the ages, where the scattered sap of the Scadutree pools deep within the furnace golems.
>
> Can be mixed in the Flask of Wondrous Physick.
> The resulting concoction temporarily increases the attack power of spirits.

**Goods 2011070 -- Deflecting Hardtear**
> A crystal tear formed slowly over the ages, where the scattered sap of the Scadutree pools deep within the furnace golems.
>
> Can be mixed in the Flask of Wondrous Physick.
> The resulting concoction temporarily enhances spontaneous guard.
>
> Damage negation and guard poise will be heightened in the moment immediately after assuming a guarding stance. Successfully executing a spontaneous guard will also strengthen guard counters.

**Goods 4480 -- Lucidity** (sorcery, `Magic` 4480)
> One of the sorceries of the Carian royal family.
>
> Alleviates buildup of sleep and madness.
> This sorcery can be cast while in motion.
>
> The Carian knights never waver.

**Goods 4670 -- Unseen Form** (sorcery, `Magic` 4670)
> One of the night sorceries of Sellia, Town of Sorcery.
>
> Makes the caster semi-invisible.
> While on horseback, effect extends to cover the mount.
> This sorcery can be cast while in motion.
>
> The Sellian assassins considered every option that aided their dirty work.

**Goods 6040 -- Flame, Cleanse Me** (incantation, `Magic` 6040)
> One of the incantations of the Fire Monks.
>
> Creates a fire within that burns away toxins.
> Alleviates poison and scarlet rot buildup and cures these ailments.
>
> This incantation leaves the caster with subtle burns--a reminder that they must fear the flame.

**Goods 6440 -- Cure Poison** (incantation, `Magic` 6440)
> Incantation of the Two Fingers' faithful.
>
> Alleviates poison buildup and cures poison.
> This incantation can be cast while in motion.
>
> The Two Fingers has high hopes for the Tarnished; that even if they should be wounded, even should they fall, they will continue to fight for their duty.

**Goods 6441 -- Lord's Aid** (incantation, `Magic` 6441)
> Incantation bestowed by the Two Fingers upon the Tarnished deemed worthy of becoming a lord.
>
> Alleviates buildup of poison, blood loss, and sleep for the caster and nearby allies. Additionally, cures poison.
>
> Hold to continue praying and delay activation.

**Goods 6730 -- Law of Regression** (incantation, `Magic` 6730)
> Incantation of the Golden Order fundamentalists.
> One of the key fundamentals.
>
> Heals all negative statuses, dispels special effects, and reveals mimicry in all its forms.
>
> The fundamentalists describe the Golden Order through the powers of regression and causality. Regression is the pull of meaning; that all things yearn eternally to converge.

**Goods 6760 -- Law of Causality** (incantation, `Magic` 6760; NPC copy `Magic` 53245)
> One of the incantations of the Golden Order fundamentalists.
> One of the key fundamentals.
>
> Manifests a small ring of causality within that allows the caster to automatically retaliate upon receiving a certain number of blows.
>
> The fundamentalists describe the Golden Order through the powers of regression and causality. Causality is the pull between meanings; that which links all things in a chain of relation.

**Weapon 19060000 -- Winged Scythe**
> Sacred scythe resembling a pair of white wings. Deals holy damage.
>
> According to pagan belief, white-winged maidens are said to be Death's gentle envoys.

**Arts 1189 -- Angel's Wings** (the Winged Scythe's unique skill)
> Unique Skill: Angel's Wings
>
> Jump and imbue the wing-blade of the armament with light, then deliver a slashing attack on the enemy.
> The white wings impede recovery actions using a flask of tears.

## No granter found

Eight ids are referenced by nothing in `regulation.bin` except, in two cases, other `SpEffectParam`
rows. Both scans were run: the field-name-filtered one and the exhaustive one over every integer
cell of all 194 params. The exhaustive pass produced 445 hits for these ids and every one is a
numeric coincidence in an unrelated column -- `NpcParam.SoundBankId`, `NpcParam.SoundAddBankId`,
`NpcParam.RetargetReferenceChrId`, `CharaInitParam.equip_Accessory04`,
`EquipParamWeapon.baseChangePrice`, `EquipParamWeapon.swordArtsParamId`,
`RuntimeBoneControlParam.chrId`, `Magic.sortId`, `ThrowParam.DefChrId`, `TalkParam.msgId`.

| SpEffect | Status |
|---|---|
| 3700 | Nothing references it. Unique `stateInfo` 299 and `vfxId` 299; nothing else in `SpEffectParam` uses either. 2-hour duration. |
| 4090 | Nothing references it. Unique `vfxId` 130000. |
| 4201 | Nothing references it. Paramdex names it "Golden Leaves Weather (Bonus Runes)"; its `soulRate` 1.05, `stateInfo` 76 and `vfxId` 5364 match the rune-boost item family exactly, which is consistent with a weather/event-applied buff rather than an item one. |
| 4463 | Nothing outside `SpEffectParam` references it, and its own `replaceSpEffectId` 4469 names a row that does not exist. |
| 4477 | Nothing outside `SpEffectParam` references it. It sits in a closed ring (4474 -> 4475 -> 4476 -> 4477 -> 4474) with no entry point; the parallel ring 4470 -> 4471 -> 4472 -> 4473 -> 4470 has the same shape. |
| 13668, 13685 | Nothing references either. They are identical to each other and mechanically identical to the Albinauric Pot's flask seal (25 s, `stateInfo` 154) with a distinct icon and VFX -- the shape of a cut or superseded copy. |
| 295121 | Nothing outside `SpEffectParam` references it or its two ancestors, 295111 and 295101. The whole 42-row family it belongs to (4305-4313, 295100-295139, 295210-295233, 4474-4477) was scanned together and produced 37 hits, all of them `SpEffectParam` rows pointing at each other. |
| 295190 | Same closure as above. The roots (295100-295103, 295210-295213, 4305-4308) carry `conditionHp` 0 and `dontDeleteOnDead`, i.e. the game starts the chain when a character's HP reaches zero, which is a code path rather than a param reference. |

The two chains that *do* resolve resolve to enemies rather than items: 295902 through
295901/295900 to `NpcParam` 137502000 "[Spirit Summon] Clayman", and 13956 to seven Giant Crayfish
`NpcParam` rows. Neither is obtainable by a player through any item, spell or weapon.

## What the marks have in common

Recorded as an observation on the traced set, not as the rule.

All 42 sit in `data/effect-catalogs/visual-vfx.json` (2861 ids); 24 also sit in
`nonmechanical-visual-sfx.json` (594 ids). Sorted by what they do to a character, the 41 marks
fall into six groups, and every group is something that changes another player's *state*, never
only their appearance. Id 3700 is not in any of them: it is unmarked, nothing grants it, and its
`stateInfo` 299 is used by no other row, so there is nothing to compare it against.

1. **Rune and drop economy** -- 3970, 3971, 4201, 20501220, 20501230 (`soulRate`, `itemDropRate`)
   and 13956 (`soulStealRate` 0). These change what the character's kills are worth.
2. **Status resistance** -- 3960-3963, 501310-501340, 501220 (`change*ResistPoint`).
3. **Status cleansing and dispel** -- 1448000, 1604000, 1644000, 1644100, 1644110, 1673000,
   1673020. Law of Regression in particular *strips other active special effects*, so pushing it
   at another player deletes their buffs.
4. **Denial** -- 500610, 1555, 13668, 13685 (`stateInfo` 154, flask seal) and 511019/511027
   (death-rune retention, curse purge). These take something away from, or hand something to, a
   character that persists past the effect.
5. **Combat behaviour** -- 1676000 (auto-retaliate), 3580 (damage-to-HP conversion), 102000
   (deflect window), 20511060 (spirit attack power).
6. **Visibility and AI** -- 1467001, 1467002, 1467006, 4090 (`stateInfo` 8), 4463, 4477
   (`replanningOnFire` AI timers), 295121, 295190, 295902 (`dontDeleteOnDead` death cascades).

Group 6 is the only one where a marked row carries no mechanical field at all: 295190 and 295902
are pure `vfxId` + `dontDeleteOnDead` + permanent. Those two are the counter-example to a rule
written purely over mechanical fields -- they are marked because `dontDeleteOnDead` and
`effectEndurance` -1 together mean the victim cannot get rid of the VFX, not because of what any
stat field says.
