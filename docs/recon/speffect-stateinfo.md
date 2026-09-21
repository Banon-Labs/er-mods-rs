# `SpEffectParam.stateInfo` -- what it is, and what each marked state does

Scope: the 41 SpEffect ids `crates/er-net-effects` must never push at another player.
Question asked: what is `stateInfo`, and per state id on those marks, what does it do to a character.

Every address below is a **1.16.2 dump VA** unless it says otherwise -- that is the only Ghidra
program with symbols (`localhost:8765`, `ermaporch1162`). Translations to the installed 1.17.1 build
are given where `docs/recon/rva-map-1162-to-1170.functions.tsv` already carries the pair; where it
does not, the row says so and the address must be mapped before it is used against the live process.

---

## 1. What `stateInfo` is

**A scalar enum tag on a character's active-effect list. Not an index into another param, not a
bitfield.** Four independent pieces of evidence, all measured:

1. **The paramdef types it.** `SpEffect.xml` declares `<Field Def="u16 stateInfo">` with
   a `DisplayName` that reads "state change type", `<Enum>SP_EFFECT_TYPE</Enum>`, and
   `<Maximum>60000</Maximum>`. Four other fields carry the *same* enum: `lifeReductionType`,
   `invocationConditionsStateChange1/2/3`. A fifth param joins the value space --
   `EquipParamGoods.useLimitSpEffectType` ("use-prohibition condition: state change type") and
   `useEnableSpEffectType` ("use-permission condition: state change type"), both declared
   `<Enum>SP_EFFECT_TYPE</Enum>`. (The paramdef display names are Japanese; this file keeps the
   repo's ASCII-only convention and glosses them.)

2. **The engine compares it for equality, never indexes with it.** Every read site in the image is
   of the shape `paramRow->stateInfo == <constant>`. Example, decompiled from the 1.16.2 dump at
   `0x1404f96a0`:

   ```c
   bool CS::SpecialEffect::HasSpEffectWithStateInfo(SpecialEffect *self, ushort wanted) {
     if ((self->count_ & 0xfL << (((wanted % 0xf) + (wanted != 0)) * 4 & 0x3f)) != 0) {
       for (e = self->entryHead; e; e = e->nextEntry) {
         v = e->spEffectData.paramRow ? e->spEffectData.paramRow->stateInfo : 0;
         if (v == wanted) return true;
       }
     }
     return false;
   }
   ```

   There is no table base, no bounds check, no scaled load. `CS::SpecialEffect::count_` is a 64-bit
   word of **16 four-bit saturating counters**, bucketed by `stateInfo % 15` -- a negative-lookup
   accelerator so the list walk can be skipped. `CS::SpecialEffect::RemoveStateInfo`
   (`0x1404fd630`) maintains it and recounts by walking the list when a bucket saturates at 15.
   A bitfield could not be bucketed by a modulus, and 322 distinct values do not fit 16 bits of
   flags.

3. **The field offset is `+0x156` and it is byte-proven against the installed game.** The raw
   decompile of `CS::SpecialEffect::RemoveByStateInfo` (`0x1404f6640`) reads
   `*(ushort *)(*plVar1 + 0x156)`. Reading `SpEffectParam` straight out of the **installed 1.17.1
   `regulation.bin`** at row-byte `+0x156` reproduces `data/effect-master-catalog.json`'s
   `stateInfo` for **11325 of 11325 rows, 0 mismatches**.

4. **The value space is dense and small.** 4439 rows carry a non-zero `stateInfo` across **322
   distinct values**, almost all in `2..511`, plus a single outlier `59999` (3 rows) sitting just
   under the paramdef maximum of 60000. 6886 rows leave it at the default 0.

### How the tag is consumed

Two mechanisms, both proven:

- **Data-driven gating.** `invocationConditionsStateChange1/2/3` (`+0x2b8/+0x2ba/+0x2bc`) are
  evaluated by `FUN_1405013b0` (1.16.2 `0x1405013b0`; 1.17.1 rva `0x502180`), called from
  `TriggerHpRateEffects`. **Polarity is positive-OR**: an effect whose three fields are all zero
  always invokes; otherwise it invokes only if the character *has* an effect carrying at least one
  of the three named states. 62 distinct values are used this way across 252 rows, and **every one
  of the 62 also appears as a `stateInfo` somewhere in the param** -- the two fields are one value
  space with no orphans. `EquipParamGoods.useLimitSpEffectType` / `useEnableSpEffectType` are the
  same idea for item usability, read by `CS::SpecialEffect::CanUseGoods` (`0x1404f9d10`).

- **Hard-coded engine constants.** ~131 call sites pass a literal state id to
  `CS::SpecialEffect::HasSpecialEffectWithStateInfo` (`0x1404f95a0`, 161 xrefs), plus a handful of
  inlined `== <const>` accessors. These are where a state *means* something to the engine rather
  than merely to the param.

---

## 2. Engine read sites that matter here

| 1.16.2 VA | 1.17.1 rva | symbol | what it does with `stateInfo` |
|---|---|---|---|
| `0x1404f95a0` | (unmapped) | `CS::SpecialEffect::HasSpecialEffectWithStateInfo` | the main query; 161 xrefs |
| `0x1404f96a0` | (unmapped) | `CS::SpecialEffect::HasSpEffectWithStateInfo` | same shape, 3 xrefs |
| `0x1404f9620` | (unmapped) | `CS::SpecialEffect::HasEffectWithStateInfo` | used by `CanUseGoods` / HKS |
| `0x1404f6640` | `0x4f7410` | `CS::SpecialEffect::RemoveByStateInfo` | zeroes an entry field for all matches |
| `0x1404f66d0` | `0x4f74a0` | `CS::SpecialEffect::SetDuration0ByStateInfo` | expires all matches |
| `0x1404fd630` | `0x4fe400` | `CS::SpecialEffect::RemoveStateInfo` | maintains the `count_` bucket word |
| `0x1404fa780` | `0x4fb550` | *(unnamed)* the **cure loop** | see section 4 |
| `0x1404fc190` | (unmapped) | *(unnamed)* the **cure table** | see section 4 |
| `0x14043e250` | `0x43e7b0` | `CS::CSChrResistModule::ApplySpEffectStatusClearFlags` | ailment state -> `statusClearFlags` bit |
| `0x1404f8900` | `0x4f96d0` | `CS::SpecialEffect::GetDeathPreventingSPEffectType` | states 69 / 159 / 160 -> 1 / 2 / 3 |
| `0x140686500` | `0x687350` | `CS::PlayerGameData::GetItemDropRateModifier` | state 66 |
| `0x14076fd50` | `0x770bd0` | *(unnamed)* menu item-usability predicate | state 154 |
| `0x1404ac160` | `0x4ac6c0` | *(unnamed)* retribution-magic accumulator | state 170 |
| `0x14065fd30` | `0x660b80` | `PostPhysicsSafe(PlayerIns*)` | state 476 |
| `0x1404b6820` | (unmapped) | `CS::SummonBuddyManager::ApplyGlovewortCrystalTearBuff` | the state-476 payload |
| `0x140ca6470` | `0xca7bb0` | `ValidateNetworkedSpEffect` | see section 5 |
| `0x1405013b0` | `0x502180` | *(unnamed)* `invocationConditionsStateChange` evaluator | positive-OR gate |

---

## 3. Per state id on the 41 marks

23 of the 41 marks carry a `stateInfo`; 18 leave it at 0 (section 3.1).
"Rows" is the count across all 11325 `SpEffectParam` rows.

| state | rows | named members | established meaning | marks in this state |
|---|---|---|---|---|
| **8** | 24 | `1767 Assassin's Gambit`, `1467000/1467002/1467005 Unseen Form`, `21430000 Miriam's Vanishing`, `99004` | **Not established.** Every named member is a stealth / reduce-enemy-perception effect, so the family is unambiguous as a *label*. But no engine constant `8` reaches any `stateInfo` comparison in the image, and the one row that actually reduces perception (`1467000`) does it with `sightSearchEnemyRate: 0.4`, a plain numeric field -- `1467002` carries state 8 and no perception field at all. So state 8 may be a tag with no engine consumer. Do not claim it hides you. | `4090`, `1467002` |
| **10** | 14 | `3060 Neutralizing Boluses`, `1644000 Cure Poison`, `1644100/1644110 Lord's Aid`, `1673000/1673020 Law of Regression`, `1604002 Flame Cleanse Me`, `20500904 Blessing of Marika`, `30000 NPC: Cure Poison`, `3607 Speckled Hardtear (Chained)`, `1623 Poison Moth Flight` | **Proven: removes Poison.** The engine cure table (section 4) maps curer state 10 -> deletes every active effect whose `stateInfo` is 2, and state 2 is Poison. `1644000 Cure Poison`'s entire row is `{stateInfo:10, vfxId}` -- the state is the whole mechanism. | `1644000`, `1644100`, `1644110`, `1673000`, `1673020` |
| **11** | 11 | `3070 Preserving Boluses`, `1604000 Flame Cleanse Me`, `1673002/1673022 Law of Regression`, `20500906 Blessing of Marika`, `30010 NPC: Cure Scarlet Rot`, `3609 Speckled Hardtear (Chained)` | **Proven: removes Scarlet Rot** (curer 11 -> ailment 5). | `1604000` |
| **66** | 3 | `3970 Silver-Pickled Fowl Foot`, `311000 Silver Scarab`, `20501220 Silver Horn Tender` | **Proven: item-discovery bonus gate.** `CS::PlayerGameData::GetItemDropRateModifier` adds `SpecialEffect::CalculateItemDropRate(...)` to the arcane-derived discovery value **only if** `HasSpecialEffectWithStateInfo(self, 0x42)`. All three rows in the state are the three discovery items. | `3970`, `20501220` |
| **76** | 4 | `3971 Gold-Pickled Fowl Foot`, `311100 Gold Scarab`, `20501230 Golden Horn Tender`, `4201 Golden Leaves Weather (Bonus Runes)` | **Meaning established from membership, mechanism not.** All four members are rune-gain items, and 76 is the exact mirror of 66 (`3970`/`3971`, `311000`/`311100`, `20501220`/`20501230` are silver/gold pairs). But no engine constant `76` reaches a `stateInfo` comparison -- I did not find the read site. Treat "extra runes" as high-confidence from the param, unproven from the binary. | `3971`, `4201`, `20501230` |
| **154** | 5 | `1555 Winged Scythe - No Flask`, `500610 Albinauric Pot` (both iconId 20405); unnamed `1521`, `13668`, `13685` | **Proven: blocks flask use.** The menu item-usability predicate at `0x14076fd50` forces its "cannot use" result true when the character has state `0x9a` **and** the goods row's `suppleType - 1 < 2`. Read out of the installed regulation, `suppleType == 1` is goods `1000..1025` + `50200..50203` + `2051004/5` and `suppleType == 2` is goods `1050..1075` -- the Flask of Crimson Tears and Flask of Cerulean Tears families. | `1555`, `500610`, `13668`, `13685` |
| **160** | 1 | `511019 Twiggy Cracked Tear` | **Proven: death-consequence selector.** `GetDeathPreventingSPEffectType` returns `3` for state `0xa0` (vs `1` for state 69 and `2` for state 159 = `360700 Sacrificial Twig`). Its caller `FUN_14065a410` (1.17.1 rva `0x65b260`) turns that into `GameData::SetDeathState(RingCurseResurrection)` and `GameDataMan::SetHasDeathPreventingEffect(true)` on the main player's death -- for type 2 it additionally finds and consumes the equipped accessory `0x200017b6`. The exact rune arithmetic is downstream of `DEATH_STATE` and is **not** established here. `stateInfo` is the row's only behavioural field. | `511019` |
| **170** | 1 | `1676000 [Incantation] Law of Causality` | **Proven: retribution-magic damage counter.** `FUN_1404ac160` runs only while `HasSpecialEffectWithStateInfo(..., 0xaa)`; it accumulates damage events, compares the count against `PLAYER_COMMON_PARAM_ST::retributionMagic_damageCountNum` within `retributionMagic_damageCountRemainTime`, fires `retributionMagic_burstMagicParamId` from `retributionMagic_burstDmypolyId`, then calls `RemoveByStateInfo(..., 0xaa)` to consume the buff. Also the positive gate for `1676005/1676007 Law of Causality (Chained)`. | `1676000` |
| **438** | 14 | `3055 Stimulating Boluses`, `1448000 Lucidity`, `1644104/1644114 Lord's Aid`, `1673008/1673028 Law of Regression`, `20500903 Blessing of Marika`, `30050 NPC: Cure Sleep`, `15452 Godskin Apostle - Sleep Awake`, `3606 Speckled Hardtear (Chained)` | **Proven: removes Sleep** (curer 438 -> ailment 436; section 4). `1448000 Lucidity`'s entire row is `{stateInfo:438, vfxId}`. | `1448000` |
| **440** | 1 | `511027 Purifying Crystal Tear` | **Partly established, and it does not say what the item name suggests.** 511027 is the only row in the whole param that sets 440, and `stateInfo` is its only behavioural field. Four rows -- `10665 [HKS] Unk Event 6001 Event Action Not Possible`, `10666`, `10667`, `10668` -- carry `invocationConditionsStateChange1: 440`, and those rows are cycling HP-drain (`changeHpRate` 5/10, `motionInterval` 2, `atkAttribute` 3, `isIgnoreNoDamage`). Since the gate is **positive** (section 1), state 440 is what *lets those damage rows run*, not what stops them. No hard-coded engine constant `440` exists. I did not establish why the item that reads as a purifier is the enabler; the community `SP_EFFECT_TYPE` label ("Purify Mohg's Nihil") is not supported by anything measured here. | `511027` |
| **476** | 1 | `20511060 Glovewort Crystal Tear` | **Proven, by FromSoft's own symbols.** `PostPhysicsSafe(PlayerIns*)` checks `HasSpecialEffectWithStateInfo(..., 0x1dc)` on the main player each frame and, on the rising edge (it stores the previous result in `chrIns.chrFlags1ca` bit 4), calls `SummonBuddyManager::ApplyGlovewortCrystalTearBuff`, which applies `GAME_SYSTEM_COMMON_PARAM_ST::glovewortCrystalSpiritBuffSpEffectId` to **every summon in the player's summon group**. A second read in the summon-spawn path (`FUN_1404bdaa0`, 1.17.1 rva `0x4bdfc0`) covers summons created while the state is already up. `stateInfo` is the row's only behavioural field. | `20511060` |
| **500** | 1 | `3580 [Tear] Crimsonwhorl Bubbletear` | **Proven as a private handshake tag; no engine constant.** 3580 sets 500 and carries `cycleOccurrenceSpEffectId: 3583`; `3583 Crimsonwhorl Bubbletear (Cycled)` and `3584 (Chained)` carry `invocationConditionsStateChange1: 500` and hold the actual payload (`magic/fire/thunder/darkDamageCutRate: 0.01`, `deleteCriteriaDamage: 3`). So state 500's whole job is to let 3580's own cycled/chained rows fire, including past 3580's own 15 s lifetime (3583 runs 16 s). | `3580` |

### 3.1 The 18 marks with `stateInfo == 0`

`stateInfo` explains nothing about these. Whatever makes them unsafe to push at another player is in
some other field.

`1467006`, `1467001`, `501340`, `501330`, `501320`, `501310`, `501220`, `295902`, `295190`,
`295121`, `102000`, `13956`, `4477`, `4463`, `3963`, `3962`, `3961`, `3960`

---

## 4. The ailment and cure tables (engine-side, hard-coded)

These are the backing facts for marks in states 10, 11 and 438.

**Seven ailment states**, from `CS::CSChrResistModule::ApplySpEffectStatusClearFlags`
(`0x14043e250`, 1.17.1 rva `0x43e7b0`), which maps each to one bit of `statusClearFlags`. Row-name
membership in `SpEffectParam` confirms every one:

| state | bit | ailment | rows |
|---|---|---|---|
| 2 | `0x01` | Poison | 309 |
| 5 | `0x02` | Scarlet Rot | 227 |
| 6 | `0x04` | Blood Loss | 284 |
| 116 | `0x08` | Death Blight | 210 |
| 260 | `0x10` | Frostbite | 276 |
| 436 | `0x20` | Sleep | 224 |
| 437 | `0x40` | Madness | 65 |

**The cure table** is `FUN_1404fc190` (1.16.2 `0x1404fc190`), a pure `switch` on the *curer's*
`stateInfo` returning whether a given active entry should be deleted:

| curer state | deletes entries whose `stateInfo` is |
|---|---|
| **10** | 2 (Poison) |
| **11** | 5 (Scarlet Rot) |
| 12 | 6 (Blood Loss) |
| 13 | 2, 5, 6, 260, 261, 436, 437 (everything) |
| 104 | 99, 105, 106, 107 |
| 106 | 105 |
| 107 | 105, 106 |
| 118 | 116 (Death Blight) |
| 198 | 111, 112, 113, 114, 192 |
| 262 | 261 |
| 276 | 260 (Frostbite) |
| **438** | 436 (Sleep) |
| 439 | 437 (Madness) |

The driver is `FUN_1404fa780` (1.17.1 rva `0x4fb550`): it walks the effect list, and for each
newly-applied entry whose `stateInfo` is one of those curer values it walks the list again calling
`SpecialEffect::RemoveByPointer` on every match, then sets the curer entry's own `duration2 = 0` so
it is consumed. Cross-check: curer 118's rows are named `Rejuvenating Boluses - Remove Blight` /
`NPC: Cure Blight`, 276's are `Thawfrost Boluses` / `NPC: Cure Frostbite`, 438's are
`Stimulating Boluses` / `NPC: Cure Sleep`, 439's are `Clarifying Boluses` / `NPC: Cure Madness` --
each matching the ailment the table says it deletes.

---

## 5. Directly relevant to `er-net-effects`: the game's own net filter

`ValidateNetworkedSpEffect` (1.16.2 `0x140ca6470`, 1.17.1 rva `0xca7bb0`), called from
`TryDequeuePacket38` and `FUN_140c9c7a0` -- i.e. on **inbound** networked SpEffect packets:

```c
bool ValidateNetworkedSpEffect(NetworkedSpEffect *pkt) {
  GetSpEffectParam(&r, pkt->spEffectId);
  return r.paramRow && r.paramRow->stateInfo != 0x129 && r.paramRow->disableFreeze == 0;
}
```

The game refuses a networked SpEffect whose `stateInfo` is **297** or whose `disableFreeze` is set.
`stateInfo == 297` has **zero rows** in the installed 1.17.1 regulation, so only the `disableFreeze`
half is live today. None of the 41 marks trip either condition -- vanilla's own filter would let all
41 through. Separately, `SpEffectParam` has an explicit `isDisableNetSync` (netsutoTong Qi shinai) bit at
row byte `+0x259` bit 4, which is a different and param-driven mechanism.

---

## 6. What I could not establish

- **State 8** -- family is unmistakable by name, engine consumer not found. Possibly an inert tag.
- **State 76** -- family is unmistakable by name and mirrors state 66 row-for-row, engine consumer
  not found.
- **States 440 and 500** -- no engine constant exists; they are consumed only through
  `invocationConditionsStateChange`. For 500 that fully explains the state. For 440 the measured
  relationship (a positive gate enabling four HP-drain rows) contradicts the item's apparent
  purpose and I have no explanation for it.
- **State 160's rune arithmetic** -- the death-state *selector* is proven; how much the
  `RingCurseResurrection` state actually preserves is downstream and was not traced.
- The 18 marks with `stateInfo == 0` are outside this document entirely.

Negative results above are bounded by method, not asserted absolutely. The search for hard-coded
constants covered: the argument at all 161 `HasSpecialEffectWithStateInfo` call sites (131 resolved
to a literal by backward decode, 29 unresolved), and a +-0x400 window scan around all 126
`+0x156` reads in the image. That scan was run with known-present controls in the same pass
(states 69, 116, 118, 123, 124, 126, 160, 199, 278, 437, 443, 457 were all found), so the misses for
8, 66, 76, 154, 170, 440, 476, 500 mean "not a literal near a `+0x156` read" -- 66, 154, 170 and 476
were then found through the resolved call-site arguments instead, 8, 76, 440 and 500 were not found
by either route.

---

## 7. Reproducing this

```bash
bash scripts/ghidra/mcp-up-1162.sh                       # named 1.16.2 program on :8765
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"StateInfo","limit":40}'
python3 scripts/ghidra/mcp_query.py getDecompiledCode    --params '{"address":"1404f96a0"}'
python3 scripts/ghidra/mcp_query.py getXrefsTo           --params '{"address":"1404f95a0","limit":200}'
```

Param-side data comes from `data/effect-master-catalog.json` (11325 rows, `SpEffectParam`,
binder 11611000). The row-byte validation reads the installed `regulation.bin` through the four
decrypt/unpack stages in `scripts/regulation-params.py` (`decrypt` -> `dcx_unpack` ->
`bnd4_entries`, then row entries at `0x40 + i*24`: id `i32` at `+0`, data offset `u64` at `+8`).
`SpEffectParam.stateInfo` is at row byte `+0x156`; `EquipParamGoods.useLimitSpEffectType` is at
`+0x7a`, `useEnableSpEffectType` at `+0x2c` (the binary's offsets, `MOVZX EAX, word ptr [RAX+0x7a]`
and `[RAX+0x2c]` -- note the Smithbox ER paramdef is a slightly older revision and its walked offset
for `useEnableSpEffectType` is one byte off), `suppleType` at `+0x6d`.

The `SP_EFFECT_TYPE` value names quoted as *community labels* come from
`~/.local/share/smithbox/app/Assets/PARAM/ER/Param Enums/SP_EFFECT_TYPE.json`. They are a secondary
source and were used only to form hypotheses; every "proven" line above rests on the binary or on
row membership, and where the two disagree (state 440) the label is the one discarded.

---

## 8. `stateInfo` cannot carry a marked/unmarked rule

Asked whether the marked and unmarked `stateInfo` sets are semantically separable. **They are not,
and the counterexample is inside one hard-coded engine table.**

### There is no dispatch table to find a boundary in

`stateInfo` is never used as an index. All 126 `+0x156` reads in the image are equality compares
against a literal, spread across ~90 unrelated functions, each owning one feature. The value space
is allocated ad hoc per feature, so adjacency carries no meaning: 159 and 160 are two different
death-consequence variants, 160 and 161 are unrelated. The only contiguous run the engine itself
treats as a group is `SpecialEffectEntry::IsStateInfoFrom303To312` (`0x140d50770`), a
`stateInfo - 303 < 10` range check -- and every one of 303..312 is unmarked. There is no
player-facing / enemy-VFX partition anywhere in the field.

### The one real table straddles the split

`FUN_1404fc190` (section 4) is the closest thing to a switch on `stateInfo`, and its cases are the cure
family: 10, 11, 12, 13, 104, 106, 107, 118, 198, 262, 276, 438, 439. The marks cut straight through
it:

| curer state | removes | marked? |
|---|---|---|
| 10 | Poison | **marked** |
| 11 | Scarlet Rot | **marked** |
| 12 | Blood Loss | unmarked |
| 118 | Death Blight | unmarked |
| 276 | Frostbite | unmarked |
| 438 | Sleep | **marked** |
| 439 | Madness | unmarked |

Same table, same driver loop (`FUN_1404fa780`), same `RemoveByPointer` call, same consumed-on-use
behaviour. Three of the seven ailment cures are marked and four are not. No property of the field
distinguishes them.

### It splits sibling rows of a single spell

It is not even consistent per item. `Law of Regression` and `Lord's Aid` each emit one row per
ailment they cleanse, and the marks take some and leave others:

| row | spell | state | marked? |
|---|---|---|---|
| `1673000`, `1673020` | Law of Regression | 10 (Poison) | **marked** |
| `1673002`, `1673022` | Law of Regression | 11 (Scarlet Rot) | unmarked |
| `1673004`, `1673024` | Law of Regression | 12 (Blood Loss) | unmarked |
| `1673008`, `1673028` | Law of Regression | 438 (Sleep) | unmarked |
| `1673012`, `1673032` | Law of Regression | 118 (Death Blight) | unmarked |
| `1644100`, `1644110` | Lord's Aid | 10 (Poison) | **marked** |
| `1644102`, `1644112` | Lord's Aid | 12 (Blood Loss) | unmarked |
| `1644104`, `1644114` | Lord's Aid | 438 (Sleep) | unmarked |

Note rows 2 and 3: states 11 and 438 are *marked states* (via `1604000 Flame Cleanse Me` and
`1448000 Lucidity`), yet Law of Regression's own rows in those same states are not marked. So the
boundary is not even per-state -- it is per-row. The same holds inside a single state: state 154 has
5 rows and 4 are marked (`1521` is not).

### And the harm ordering does not hold either

If the rule were "the marked states are the ones that do something to a player that a player would
object to", these unmarked states break it:

- **132 -- `503350 Bewitching Branch` / `20503350 Charming Branch`**, which changes the target's
  team type. Unmarked, while **66** (an item-discovery *bonus*) is marked.
- **121 -- Damage Level Change**, 49 rows. **155 -- Modify Poise**, 10 rows. **143 -- Character
  Respawn**, 16 rows. **299 -- "enemies attack invaders"**, 1 row. All unmarked and all mechanical.

### Verdict

Use `stateInfo` to *explain* individual marks -- section 3 does that, and for 10, 11, 66, 154, 160, 170,
438, 476 and 500 the explanation is proven. Do not use it to *derive* the set. The marks are a
hand-curated list whose boundary lives outside this field; `iconId != 0` plus
`effectTargetPlayer == 0`, already measured elsewhere, remain the better predictors.
