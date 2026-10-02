# Elden Ring self-buffs for a melee build: what they change, and which ones stack

Covers buff spells, physick tears, great runes with Rune Arc, and buff consumables: which
`SpEffectParam` columns each one sets, where each column enters a hit, which entries replace
which, PvE vs PvP values, and how often each appears in STR PvP builds at RL 140-160. Talismans
and Ashes of War are covered elsewhere; section 10 takes the buff rows an ash puts on its user
from `er-mechanics-ashes.py` and folds them into the expected factors the PvP ranking uses.

Labels, as in the other docs here:
- **VERIFIED**: read from the installed 1.17.1 regulation (`scripts/er-param-read.py`) or from
  the executable.
- **INFERRED**: consistent with the data or names; the code path was not traced.
- **SITE**: the er-build-planner corpus.

Addresses are `1.16.2 VA / 1.17.1 VA`. The 1.16.2 side is the named Ghidra dump on `:8765`
(dump VA = runtime VA on 1.16.2). The 1.17.1 side is from
`docs/recon/rva-map-1162-to-1170.functions.tsv` plus `+0x70` at or above rva `0xafefe9`. Leaf
functions missing from that map were located by a unique byte match in
`eldenring-deobf-1.17.1.bin` (`scripts/find-deobf-bytes.py`). Nothing was launched.

Calculator: `scripts/er-mechanics-buffs.py` (commands in section 9).

## 0. The answer

- **Body buffs don't raise AR. They multiply damage after the defense curve (VERIFIED).**
  Golden Vow, Flame Grant Me Strength (FGMS), Howl of Shabriri, Exalted Flesh, Boiled Crab,
  Opaline Hardtear, the Shrouding/Bloodsucking tears and Thorny Cracked Tear all store their value
  in two column pairs:
  - `atkPlayerDmgCorrectRate_*` vs `atkEnemyDmgCorrectRate_*` on the attacker;
  - `defPlayerDmgCorrectRate_*` vs `defEnemyDmgCorrectRate_*` on the defender.

  Those are multiplied in `CalculateDamageCorrections` after `CalculateDefense`. The `Player`
  column is used when the other side is a player, so these columns are the PvP values. No buff
  item or spell in this set touches `*AttackPowerRate`. The only named player-side rows that do
  are Ashes of War (Roar, War Cry, Cragblade, Braggart's Roar: `physicsAttackPowerRate`
  1.075-1.15), Blue Dancer Charm (`stateInfo 315`, which the accumulator skips) and Silver Tear
  Mask (0.95). That is a regulation scan of every SpEffect row (section 2).
- **Stacking is decided by `spCategory` alone, plus `categoryPriority` for a few categories
  (VERIFIED).** `vfxId` and `stateInfo` do not decide it, except for poison, rot and blood (section
  3). The body-buff families are:
  - **151**: FGMS, Howl of Shabriri, Exalted Flesh, Boiled Crab/Prawn, every Fortification and
    Protection incantation, the livers, Bloodboil Aromatic, Dragon Communion/Dragonscale Flesh,
    Scorpion Stew, Baldachin's Blessing. One live entry; the newest takes the slot.
  - **160**: Golden Vow (spell, item, ash), the attack half of Uplifting Aromatic, Ancient Dragon's
    Blessing. One live entry.
  - **162 / 163**: weapon buffs, right / left hand (greases, Bloodflame Blade, Black Flame Blade,
    Electrify Armament, Order's Blade, Scholar's Armament), and also the buff ashes of war: War
    Cry 1811/1813, Braggart's Roar 1861/1863, Barbaric Roar 1681, Cragblade 1821/1823, Royal
    Knight's Resolve 1701/1703, Determination 1691, Sacred Blade 821/823, Lightning Slash
    1676/1678, Chilling Mist 826. One per hand, so a roar and a grease replace each other
    (section 10).
  - **20**: physick tears, great runes, Terra Magica, Mantle of Thorns. Everything stacks; the same
    id refreshes.

  So the classic PvP stack is Golden Vow (160) plus one 151 buff plus two tears plus the great
  rune. FGMS, Crab and Exalted Flesh are mutually exclusive: the last one used wins.
- **A great rune does nothing for an invader or a cooperator, except Mohg's rune for an invader
  (VERIFIED code, INFERRED which rune).** `ApplyRuneArcEffects` applies the rune rows only to the
  host. For an invader it applies only row 609+10g, and only for the rune named by ConstantParam
  `0xc3`. Only Mohg's 649 is a live row. Section 4.
- **Usage (SITE).** Of 368 STR-tagged PvP builds at RL 140-160:
  - Opaline Hardtear 43.5%, Morgott's Great Rune 36.7%, Boiled Crab 16.6%, Uplifting Aromatic
    14.4%, Exalted Flesh 10.1%.
  - Spell usage cannot be measured: 10 of the 1160 PvP builds in that level window have any spell
    slot filled.

## 1. From an item to its SpEffects (VERIFIED, params)

`Buffs._closure` walks the params; no hand-made id list except the great runes:

| source | link |
|---|---|
| spell (goodsType 5/16/17/18) | `MagicParam.refId1..10` by `refCategory` (1 bullet, 2 SpEffect, 0 attack) |
| consumable / tear (goodsType 0/10) | `EquipParamGoods.refId_default`, `refId_1` by `refCategory` |
| bullet | `spEffectIDForShooter` (the caster), `spEffectId0..4` (what it hits), `HitBulletID`, `intervalCreateBulletId` |
| SpEffect | `cycleOccurrenceSpEffectId`, `replaceSpEffectId` (applied on expiry, section 3 R4), `accumuOver/UnderFireId` |
| great rune (goodsType 15) | none in params; `ApplyRuneArcEffects` (section 4) |

A bullet's `spEffectId0..4` land on whatever the bullet hits. Only rows with
`effectTargetSelfTarget = 1` are kept as the caster's buff (INFERRED). Frostbite, Black Blade's
max-HP cut and Rennala's defense cut all carry `effectTargetOpposeTarget` instead. A row counts
as a buff if any attack, defense, stat, HP/FP/stamina, poise or equip-load column differs from
the paramdef default. `changeHp*` and status build-up columns alone don't count, because cures,
poison and rot are written in the same columns.

## 2. Where each column enters a hit (EXE)

| column (SpEffectParam offset) | collected by | applied | stage |
|---|---|---|---|
| `physics/magic/fire/thunder/darkAttackPowerRate` `+0x48..+0x54`, `+0x1e0`; `slash..neutralAttackPowerRate` `+0x220..+0x22c` | `FUN_140d4fdf0 / 0x140d51ba0` (switch 0..8), `FUN_1404ff3e0 / 0x1405001b0` (NG+ factor), accumulator `FUN_1404f4520 / 0x1404f52f0` -> AttackInfo `+0x6c`, `+0x70..+0x7c`, `+0x80..+0x8c` | `FUN_1406832a0 / 0x1406840f0`: `weapon part * rate(e)`; physical is `+0x6c * sub-type(+0x44)` | before defense (AR) |
| `*AttackPower` `+0x58..+0x64`, `+0x1e4`, `+0x230..+0x23c` | same accumulator, `* byPoint/100` if `isUseAtkParamAtkPowerCorrect` | added last (grease.md) | before defense |
| `physics/magic/fire/thunder/darkAttackRate` `+0x38..+0x44`, `+0x1dc`; `slash..neutralAttackRate` `+0x210..+0x21c` | same accumulator (`FUN_1405000c0`, `FUN_140500050(i)`, `FUN_1404ffc10`, `FUN_1404ff8b0`, `FUN_1405003f0`, `FUN_1404ff710 / 0x1405004e0`), entries with 0 skipped, `* byDmg/100` under the flag -> AttackInfo `+0x90..+0xb0` | `FUN_140d24a30 / 0x140d261b0` stores `(int)(rate * 100)` as `s16` at AttackDamageInfo `+0x14..+0x1c`; physical folds in the sub-type picked by damage type (`FUN_140d254c0 / 0x140d26c40`). `CalculateDamage 0x1404472b0 / 0x140447810` multiplies `s16 * 0.01 * float(+0x1e8..) * weak-to` into `CalculateDamageBasic 0x1406849d0 / 0x140685820` | after defense, cut to a whole percent |
| `atkPlayerDmgCorrectRate_*` / `atkEnemyDmgCorrectRate_*` `+0x28c..+0x2b0` | `CalculateAtkPlayerDmgCorrectRates 0x1404f5080 / 0x1404f5e50`, `CalculateAtkEnemyDmgCorrectRates 0x1404f4390 / 0x1404f5160` | `CalculateDamageCorrections 0x140684d70 / 0x140685bc0` (defense.md section 3) | after defense |
| `defPlayerDmgCorrectRate_*` / `defEnemyDmgCorrectRate_*` `+0x260..+0x284` | `CalculateDefPlayerDmgCorrectRates 0x1404f5f10 / 0x1404f6ce0` | same | after defense |
| `*DamageCutRate` `+0x1c..+0x34`, `+0x1d0` | `CalculateDefenseModifiers` (defense.md section 2) | `CalculateDamageBasic` | after defense |
| `add*Status` | `CollectStatBuffs` (resources.md) | effective stats, so AR through scaling | before AR |

Evidence for the new rows (VERIFIED):
- **`FUN_140d4fdf0`** returns `+0x48`, `+0x220`, `+0x224`, `+0x228`, `+0x22c`, `+0x4c`, `+0x50`,
  `+0x54`, `+0x1e0` for cases 0..8. So the "nine attack-rate products" in grease.md are the
  `*AttackPowerRate` columns.
- **Accumulator tail.** The `*AttackRate` products go to AttackInfo `+0x90` (physical),
  `+0x94..+0xa0` (slash, strike, pierce, standard), `+0xa4` (magic), `+0xa8` (fire), `+0xac`
  (lightning) and `+0xb0` (holy). `FUN_1406832a0` reads only `+0x48..+0x8c`.
- **`*AttackRate` percent.** `FUN_140d24a30` writes `AttackDamageInfo+0x14 = (int)(AttackInfo+0x90
  * FUN_140d254c0(...) * 100.0)` and `+0x16..+0x1c` from `+0xa4..+0xb0`. `CalculateDamage` then
  builds `unkMult1[e] = s16 * 0.01 * float(+0x1e8 + 4e) * FUN_1403e9bb0(...)`.
  `CalculateDamageBasic` multiplies `CalculateDefense(...)` by `unkMult1`. So a
  `*AttackRate` of 1.15 is +15% damage after defense, truncated to 1%.
- **`stateInfo 197` exception.** `FUN_1404f5310 / 0x1404f60e0` reads `+0x38..+0x44`, `+0x1dc`
  again, but only for the attacker's entries with `stateInfo 197` ("Enhance Thrusting Counter
  Attacks", Spear Talisman 320600). `CalculateDamage` calls it only when the second bucket of
  `CalculateDefenseModifiers` exceeds 1. defense.md names that bucket as the `stateInfo 110`
  (Counter Damage) product. The accumulator skips 197.

Gates, all VERIFIED:
- **The accumulator.** It skips entries with flags `& 0x800c0003` and `stateInfo` 197/315/316.
  It holds `stateInfo` 123..126/186 back unless the attack carries the matching `AttackInfo+0x40`
  bits. Every entry must also pass `IsApplicableForCategory 0x140500930 / 0x140501700`.
- **The attacker PvP/PvE rates.** They use the same flag filter and `IsApplicableForCategory`,
  but no `stateInfo` skips.
- **`IsApplicableForCategory` has two parts.**
  - The hand rule on `wepParamChange`: right-hand attacks refuse 2/3/4, left-hand attacks refuse
    1/3/4, and two-handed attacks accept 0/1/5/6, plus 2 only while the left weapon is held
    two-handed. Body buffs have `wepParamChange 0`, so they reach both hands and two-handed
    attacks.
  - The sub-category mask. `GetMagicSubCategoryChangeMask 0x140d50a30 / 0x140d527e0` sets a bit
    per nonzero `magicSubCategoryChange1..3`. `CheckMagicSubCategoryChangeMask 0x140d50880 /
    0x140d52630` passes when no bit is set, or when a set bit is also in the attack's mask
    (AttackDamageInfo `+0xdc..+0xfb`). So Spiked Cracked Tear (`magicSubCategoryChange1 = 100`,
    Charged Heavy Attack) only applies to charged heavies. Which AtkParam or BehaviorParam field
    fills the attack's mask was not traced.
- **The defender rates.** They use the flag filter and `IsValidForPartGroup` (rule not read). They
  have no hand or sub-category gate.

## 3. Stacking: what the add path does (EXE)

`CS::SpecialEffect::Apply 0x1404fa8e0 / 0x1404fb6b0` first runs `CheckApplyConditions 0x1404fc4e0
/ 0x1404fd2b0`, then the add, `FUN_1404fd090 / 0x1404fde60`:

```
R1  FUN_1404fc690 / 0x1404fd460: if new.spCategory >= 10000 and a live entry has that category -> refused
R2  FUN_1404fc0c0 / 0x1404fce90: does N clash with a live entry E? (E skipped when its controlFlags&2,
    set for duration <= 0, INFERRED)
      (a) E.id == N.id and E.spCategory != 10            FUN_140500510 / 0x1405012e0
      (b) N.stateInfo in {2,5,6} and E.stateInfo == N's; N 107 vs E 109   FUN_140500540 / 0x140501310
      (c) FUN_1405005a0 / 0x140501370, by N.spCategory:
            100, 200, 201            same category and same categoryPriority
            110, 130-133, 140, 150-164, 180, 1003-1006   same category
            E in 165-174             N has E's category
            anything else (0, 1, 20, 120, 1000-1002, ...)   no category clash
    no clash -> NewSpecialEffectEntry: a second, independent entry (the buff stacks)
R3  clash -> FUN_1404fc020 / 0x1404fcdf0 -> FUN_140500c40 / 0x140501a10, first live entry that fits:
            20                       the same id (refresh in place)
            100, 200, 201            same category and priority
            110, 130-133, 140, 150-174, 180   same category
            1003-1006                same category and E.priority >= N.priority
            otherwise                none -> the new buff is refused (-2)
    found -> RemoveStateInfo(E); FUN_140500de0 / 0x140501bb0 re-points E at N's id with
             duration = rate * N.effectEndurance (full new duration)
R4  FUN_1404fae40 / 0x1404fbc10 (per frame): an expired entry applies replaceSpEffectId, then goes;
    live entries apply cycleOccurrenceSpEffectId; durations scale by stateInfo 193 / 301 rows
R5  FUN_140500710 / 0x1405014e0: tier expiry for stateInfo 43-45, 111-114, 192 (no 1.17.1 row uses them)
R6  FUN_1404f8640 / 0x1404f9410: among live category-1001 entries only the lowest categoryPriority
    feeds FUN_140d4ffa0 (Baldachin's poise rows 503356/503362, Endure)
```

Consequences:
- **Fields that don't matter.** `vfxId`, `SpEffectVfxParam.playCategory/playPriority`,
  `effectEndurance` and `isExtendSpEffectLife` are not read on the add path. No reader of `+0x170`
  exists in the SpecialEffect code (VERIFIED). That the VFX categories only affect visuals is
  INFERRED.
- **Two buffs of one category replace each other.** Different ids in 151 cannot coexist: the
  second takes the first's slot, with its own full duration. So "does X stack with Y" is "are
  their categories different, or both 20/0".
- **Recasting refreshes.** Recasting a 151/160 buff refreshes it in place: rule (a) plus R3 same
  category. Re-applying a category-0 buff whose id is live is refused, with no refresh.
- **Physick.** Every tear in section 5 is category 20, so two different tears always stack.
  Drinking the same Flask again refreshes both.
- **Thorny Cracked Tear.** Its tier rows 3558/3559/3560 are category 120, which has no category
  clash. So different tiers could coexist for the 1.5 s they last. Whether they do in practice
  depends on the accumulator (`stateInfo 308`, thresholds 17/30/45/60 on `accumuOverVal`), which
  was not traced. The calculator applies one tier at a time.

Category map for the buffs in section 5 (VERIFIED, params):

| `spCategory` | members | behaviour |
|---|---|---|
| 151 | FGMS 1605000, Howl of Shabriri 1733000, Exalted Flesh 3950, Boiled Crab 500820, Boiled Prawn 500830, Flame Protect Me, Black Flame's Protection, Barrier of Gold, Protection of the Erdtree (priority 70), the Fortifications, all Dried/Pickled Livers, Bloodboil Aromatic, Dragon Communion/Dragonscale Flesh, Fingerprint Nostrum, Scorpion Stews, Baldachin's Blessings, Sacred Bloody Flesh setup, Vyke's Dragonbolt body row, Dragonbolt Blessing | one at a time, newest wins |
| 160 | Golden Vow incantation 1660000, Golden Vow item 20503170, Golden Vow ash 1730, Uplifting Aromatic attack row 503501 | one at a time |
| 159 | Uplifting Aromatic defense row 503500 | own slot, so it stacks with 160 and 151 |
| 157 | Rennala's Full Moon / Ranni's Dark Moon debuff rows, Greyoll's Roar, Acid Spraymist | debuffs on others |
| 162 / 163 | 162: greases (right rows), Bloodflame Blade, Black Flame Blade, Electrify Armament, Vyke's weapon row, Order's Blade, Scholar's Armament, Shield Grease 501690. 163: the left-hand grease rows, Scholar's Shield, Immutable Shield, Shield Grease 501691 | one per category |
| 201 | Pickled / Well-Pickled Turtle Neck (priority 55 both), Rock Heart / Priestess Heart (50) | same priority replaces (the two Turtle Necks), different priorities stack (a Heart with a Turtle Neck) |
| 20 | tears, great rune rows 600/610/620/630/640/650 and 790, Rune Arc trigger 3450, Terra Magica 1413000, Mantle of Thorns, Sacred Bloody Flesh boost 20501212, Thorny tier 3561 | stack with everything |
| 120 | Thorny tiers 3558-3560 | no clash rule |
| 1001 | Baldachin poise rows | R6 read-side |

## 4. Great runes and Rune Arc (EXE)

- **Rune Arc (VERIFIED).** Rune Arc applies SpEffect 3450 (`stateInfo 277` "Trigger Great Rune",
  0 s). `UpdateMultiplayData 0x14065a930 / 0x14065b780` sees `stateInfo 0x115` and sets
  `PlayerGameData.runeArcActive` (`+0xff`). While that flag is set, the same function refreshes
  the `BigRuneSpEffect` chr slot (`FUN_140499aa0 / 0x14049a000`).
- **The slot (VERIFIED).** `ChrBigRuneSpEffectSlot::Update 0x1404a7730 / 0x1404a7c90` calls
  `ChrIns::ApplyRuneArcEffects 0x1404a6d20 / 0x1404a7280` on its first run and again when the
  equipped rune changes. When the slot stops being refreshed it calls `RemoveGreatRuneSpEffects
  0x1404a7310 / 0x1404a7870`.
- **Rows applied, by role (VERIFIED), with g the `GetEquippedGreatRune` enum.**
  - A host with a rune gets rows 600+10g .. 608+10g. That the loop step is 1 is INFERRED (the
    decompiler mislabels the constant).
  - A host with no rune gets 790, "No Great Rune: Active Effect", `maxHpRate 1.1`.
  - An invader gets 609+10g, only when g equals `GetGreatRuneEnumForConstantParamId(0xc3)`. Row 649
    (Mohg, `stateInfo 441`) is the only 609+10g row that is live, so that rune is Mohg's
    (INFERRED).
  - Other roles, cooperators included, get nothing.
- **Duration.** The rows have `effectEndurance -1`, so no timer. `runeArcActive` is cleared by
  `FUN_14025e1e0` when `PlayerGameData+0x100` is set. That this is death is INFERRED; the setter
  was not traced.
- **Row 3290 does not exist.** `AddRuneArcGreatRuneSpEffect 0x140591260 / 0x1405920b0` applies
  `PlayerCommonParam.systemEnchant_BigRune = 3290`, which is not in the 1.17.1 regulation, so that
  call applies nothing.
- **Effect rows (VERIFIED, params).** 601 (Godrick, `changeHpPoint -295`, 0 s) is the activation
  heal. 608+10g is the activation sfx. Rows 602-607 etc. carry no columns.

| rune | row | effect | stacks with |
|---|---|---|---|
| Godrick's | 600 | +5 every attribute | everything (category 20) |
| Radahn's | 610 | HP, FP, stamina x1.15 | everything |
| Morgott's | 620 | HP x1.25 | everything |
| Rykard's | 630 | `stateInfo 199` Apply Kill Effect (HP on kill) | everything |
| Mohg's | 640 (host), 649 (invader) | `stateInfo 441` | everything |
| Malenia's | 650 | `stateInfo 449` (HP from attacks after a hit) | everything |
| none + Rune Arc | 790 | HP x1.1 | everything |

What the `stateInfo` 199/441/449 behaviours do in numbers was not traced; the calculator
reports them and applies no factor.

## 5. The buffs (VERIFIED, params)

Values are written as vs enemies / vs players (`*Enemy*` / `*Player*` columns). "atk" is the
attacker's post-defense damage factor. "def" is the defender's post-defense factor on incoming
damage, below 1 meaning less damage. Categories are from section 3.

| source | row | s | cat | effect (PvE / PvP) |
|---|---|---|---|---|
| Golden Vow (incantation) | 1660000 | 80 | 160 | atk all x1.15 / x1.075; def all x0.90 / x0.95 |
| Golden Vow (SotE item) | 20503170 | 45 | 160 | atk x1.127 / x1.05; def x0.912 / x0.967 |
| Flame, Grant Me Strength | 1605000 | 30 | 151 | atk phys, fire x1.2 / x1.15; stamina regen +5 |
| Howl of Shabriri | 1733000 | 40 | 151 | atk all x1.25 / x1.25; def all x1.30 / x1.30 (you take 30% more) |
| Exalted Flesh | 3950 | 30 | 151 | atk phys x1.2 / x1.15 |
| Boiled Crab | 500820 | 60 | 151 | def phys x0.80 / x0.85 |
| Boiled Prawn | 500830 | 60 | 151 | def phys x0.85 / x0.90 |
| Black Flame's Protection | 1627000 | 70 | 151 | def phys x0.65 / x0.85 |
| Bloodboil Aromatic | 503550 | 60 | 151 | atk phys x1.3 / x1.2; all `*DamageCutRate` x1.25 (you take 25% more); stamina x1.2 |
| Uplifting Aromatic | 503501 + 503500 | 40 | 160 + 159 | atk phys x1.1 / x1.075; plus a row with def x0.1, `stateInfo 42`, `deleteCriteriaDamage 1` (that this ends it on the first hit is INFERRED from the name) |
| Dragonscale / Dragon Communion Flesh | 20501105 / 20501100 | 90 | 151 | +8 / +6 VIG, END, STR, DEX |
| Scorpion Stew | 20501201 | 60 | 151 | def phys x0.90 / x0.92 |
| Opaline Pickled Liver | 20501160 | 120 | 151 | def non-phys x0.82 / x0.94 |
| Radiant Baldachin's Blessing | 503361 + 503362 | 70 | 151 + 1001 | def phys x0.65 / x0.90; poise `toughnessDamageCutRate 0.55` (0.1 s cycled) |
| Sacred Bloody Flesh | 20501210 (+ 20501212 after blood loss) | 30 | 151 (+ 20) | atk all x1.07, +10 ARC; then x1.18 more |
| Bloodflame Blade | 1632000 | 60 | 162 | right hand +40 fire flat (`isUseAtkParamAtkPowerCorrect`), `stateInfo 152` |
| Black Flame Blade | 1626000 | 7 | 162 | right hand +65 fire flat |
| Electrify Armament / Order's Blade | 1696000 / 1677000 | 90 | 162 | right hand +75 lightning / holy flat |
| Terra Magica | 1413000 | 1.5 (re-applied while inside the circle, INFERRED) | 20 | atk magic x1.225 / x1.15 (nothing for physical) |
| Mantle of Thorns | 21490000 | 30 | 20 | +87 physical flat, `stateInfo 124` (roll-triggered; held back from normal hits) |
| Opaline Hardtear | 511011 | 180 | 20 | def all x0.85 / x0.90 |
| Thorny Cracked Tear | 3558 / 3559 / 3560 / 3561 | 1.5 / 1.5 / 1.5 / 0 | 120 / 120 / 120 / 20 | atk all x1.09 / x1.13 / x1.20 / x1.20, same PvE and PvP; accumulator thresholds 17 / 30 / 45 / 60 |
| Spiked Cracked Tear | 511014 | 180 | 20 | `*AttackRate` x1.15, charged heavy attacks only (sub-category 100) |
| Stonebarb Cracked Tear | 511026 | 30 | 20 | poise damage `saAttackPowerRate` x1.3, `staminaAttackRate` x1.3 |
| Strength-knot Crystal Tear | 3515 | 180 | 20 | +10 STR |
| Bloodsucking Cracked Tear | 20511050 | 180 | 20 | atk all x1.2 / x1.125 (plus its HP drain, not modelled) |
| Flame/Magic/Lightning/Holy-Shrouding | 511028..511031 | 180 | 20 | atk that element x1.2 / x1.125 |
| Crimsonspill / Greenspill | 3500 / 3501 | 180 | 20 | HP x1.1 / stamina x1.15 |
| Greenburst | 511010 | 180 | 20 | stamina regen +15 |
| Opaline Bubbletear | 3507 | 180 | 20 | all `*DamageCutRate` x0.1, `deleteCriteriaDamage 1` (one hit, INFERRED as above) |
| Winged Crystal Tear | 511012 | 180 | 20 | `equipWeightChangeRate` 4.5 (a weight-reduction row; its reader is not in this doc) |
| Windy Crystal Tear | 511015 | 180 | 20 | all `*DamageCutRate` x1.15, `stateInfo 290` (extended roll i-frames) |

- **Not self-buffs.** Rotten Butterflies is an attack spell: its rows (21720000/1) are scarlet rot
  on targets. Blessing of Marika is a heal and cleanse chain (20500900..918). Greyoll's Roar and
  Acid Spraymist lower the target's or the user's damage.
- **Where the PvP numbers live.** They are the `*Player*` columns, so PvP-specific values need no
  separate rows.

## 6. Interface for the PvP ranking

```python
spec = importlib.util.spec_from_file_location('buffs', 'scripts/er-mechanics-buffs.py')
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
m = B.Buffs()                         # reads the regulation once (about 1 s)

a = m.attack_context(active, pvp=True, phys_type='strike', hand='right', two_handed=False,
                     left_two_handed=False, by_point=100, by_rate=100, by_dmg=100,
                     hp_ratio=1.0, sub_categories=(), role='host', apply_stack=True)
d = m.defense_context(active, pvp=True, phys_type='strike', hp_ratio=1.0, guarding=False,
                      role='host', apply_stack=True)
```

- **`active` is in application order.** It is a list of names (`"Golden Vow"`,
  `"consumable:Golden Vow"`, `"Thorny Cracked Tear#2"`, `"Sacred Bloody Flesh#triggered"`,
  `B.RUNE_ARC_NO_RUNE`) or raw SpEffect ids. Order matters: `stack()` replays R1-R3, so the last
  151 buff wins. `a['refused']` lists what was replaced or refused.
- **Attacker fields** (all per element: `physical`, `magic`, `fire`, `lightning`, `holy`):
  - `a['ar_rate'][e]`: multiply the weapon part of the AR (before defense).
  - `a['flat_add'][e]`: add after all AR multipliers (before defense).
  - `a['atk_rate'][e]` and `a['pvp_rate'][e]`: multiply the damage after `defense_curve`.
  - `a['stats']`: attribute adds. Feed them to `er-mechanics-ar.py` for AR, and to
    `er-mechanics-resources.py` for HP.
  - `a['status']`: build-up adds.
- **Defender fields:**
  - `d['cut'][e]` and `d['correct'][e]`: multiply the damage after `defense_curve`, as in
    `er-mechanics-defense.py`'s `effect_mult`.
  - `d['poise_div']`: divide menu poise by it.
  - `d['max_hp_rate']`.
- **Per-hit damage for one element.** In the order the game applies it:
  `defense_curve(AR[e] * a.ar_rate[e] * MV/100 + a.flat_add[e], DEF[e])
  * armor_absorption[e] * d.cut[e] * a.atk_rate[e] * a.pvp_rate[e] * d.correct[e] * finalDamageRate`.
  `flat_add` already carries the attack's `byPoint` (pass it as `by_point`). The last factor is
  FinalDamageRateParam (defense.md section 3).
- **Scope.** The module does not load weapons. It returns factors, so the ranking composes them
  with `er-mechanics-ar.py` and `er-mechanics-defense.py`.

## 7. Corpus usage (SITE)

`python3 scripts/er-mechanics-buffs.py --corpus --rl 140-160`. It counts builds that are
STR-tagged and PvP, where PvP means `isPvE` is false or the build has one of the tags Invasions,
Duels, Co-op/Gank, 2v2 or Ladder. Result: 368 builds.

| source | builds | share |
|---|---|---|
| Opaline Hardtear | 160 | 43.5% |
| Morgott's Great Rune | 135 | 36.7% |
| Boiled Crab | 61 | 16.6% |
| Uplifting Aromatic | 53 | 14.4% |
| Exalted Flesh | 37 | 10.1% |
| Drawstring Dragonbolt Grease | 19 | 5.2% |
| Well-Pickled Turtle Neck | 16 | 4.3% |
| Drawstring Royal Magic Grease | 13 | 3.5% |
| Spiked / Stonebarb Cracked Tear, Ironjar Aromatic | 12 each | 3.3% |
| Thorny Cracked Tear | 11 | 3.0% |
| Golden Vow (SotE item), Greenburst Crystal Tear | 9 each | 2.4% |
| Strength-knot Crystal Tear | 8 | 2.2% |
| Radahn's Great Rune, Bloodboil Aromatic | 7 each | 1.9% |
| Rune Arc (in the tool slots) | 5 | 1.4% |

- **Spells.** The planner's spell slots are nearly empty in this scrape: 10 of 1160 PvP builds at
  RL 140-160 fill any, all with Bestial Vitality. That is a gap in the data, not evidence that
  STR builds skip Golden Vow or FGMS.
- **"Golden Vow" in tool slots.** That entry is the consumable (goods 2003170, 45 s, x1.05 vs
  players), not the incantation.
- **The great rune field.** `greatRune` is set on 1672 of 5699 builds overall. It records the
  equipped rune, not whether Rune Arc is used.

## 8. Not established

- **The attacker's sub-category mask.** Which AtkParam or BehaviorParam field fills AttackDamageInfo
  `+0xdc..+0xfb`, i.e. which attacks count as "charged heavy" (100) for Spiked Cracked Tear.
- **The writer of AttackDamageInfo `+0x1e8..+0x1f8`.** These are the floats multiplied into the
  `*AttackRate` term; 1.0 is assumed.
- **The entry-inactive flag bits (`0x800c0003`).** In particular, how `conditionHp` /
  `conditionHpRate` switch an entry off. The calculator uses "HP% at or below conditionHp" and "at
  or above conditionHpRate", read from names.
- **The R2 skip.** That `controlFlags & 2` (skip in the clash test) means duration <= 0 is
  inferred from the init code. It matters only for permanent rows (-1), all of which are category
  20 or 0 here.
- **The Thorny Cracked Tear accumulator (`stateInfo 308`).** What increments it, and whether
  tiers 3558-3560 overlap.
- **The great rune `stateInfo` effects.** 199 (Rykard), 441 (Mohg) and 449 (Malenia) in numbers.
  Also the setter of `PlayerGameData+0x100` (the flag that clears `runeArcActive`), and the 1.17.1
  addresses of `FUN_14025e1e0` and `IsRuneArcActive 0x140788890`.
- **Some 1.17.1 addresses.** R6's query `FUN_140d4ffa0`, and the part-group filter
  `IsValidForPartGroup` in the defender rates.
- **Other code paths.** Callers of `FUN_1404fd090` other than `Apply` (`FUN_1404f6e90`,
  `FUN_1404f6e30`), which may skip R1.
- **Not modelled.** Bloodsucking Cracked Tear's HP drain, Winged Crystal Tear's weight reader, and
  the `stateInfo 42` (Uplifting Aromatic) and `290` (Windy) behaviours.
- **Section 10's fight model.** 5 landed hits 5 s apart (`er-mechanics-status.py`, spacing
  INFERRED) inside a 3 to 5 minute fight (user, 2026-10-01). Every buff is applied before the
  first hit and recast by section 10's rule. The cast frames charged are stand-ins for items
  (the bolus goods frame) and spells (60 frames); a consumable's use limit is its `maxNum`; and
  the punish window a recast opens is not charged.
- **The 0-duration rows a buff cycles.** Royal Knight's Resolve's 1701 cycles 1704 and
  Determination's 1691 cycles 1694: `*AttackRate` 0.75 on every element, duration 0. Smithbox
  names them "Critical Damage Debuff", so they are taken to apply to critical hits only and are
  left out of a normal hit (INFERRED from the name). If they applied to every hit, those buffs
  would be x1.05 rather than x1.4.
- **How a next-hit buff ends (traced 2026-10-01, 1.16.2 named dump; 1.17.1 addresses byte-checked
  in brackets).** Royal Knight's Resolve 1701/1703 and Determination 1691/1693 carry
  `deleteCriteriaDamage` 0: nothing on the buff row ends it. The attack ends it.
  - VERIFIED, params: 4408 of the 11017 `AtkParam_Pc` rows carry SpEffect 1665 in `spEffectId3`,
    3358 carry 1667 in `spEffectId4` (2407 carry both). 1665 "Determination Removal - Right" has
    `invocationConditionsStateChange1` 384, `effectTargetAttacker` 1, and cycles 1661, a
    0-duration category 162 row; 1667 is the same with 385 and 1666 (category 163).
  - VERIFIED, EXE: in `CalculateDamage2` 0x1404483b0 [0x140448910], the hit-SpEffect loop sends a
    slot row with `effectTargetAttacker` (SpEffectParam `+0x160` bit 1, read at 0x140448aed
    [0x14044904d]) to the damage dealer through `FUN_1403e8b70` instead of the victim. The row's
    `invocationConditionsStateChange1..3` are tested against its owner's own SpEffects
    (`FUN_1405013b0` [0x140502180] -> `HasSpecialEffectWithStateInfo`, run per frame from
    `TriggerHpRateEffects`), so 1665 does something only on an attacker holding a `stateInfo` 384
    row. Its cycle row 1661 then takes the category 162 slot, which holds one entry (section 3),
    and Royal Knight's Resolve is gone. A buff is spent by a registered hit, not by a damaging
    one: the loop has no damage test (`IsZeroDamage` and HP > 0 gate only HP and armor
    durability), so a blocked hit spends it too.
  - VERIFIED, EXE: a hit is registered only past the attacker-side gate `FUN_1404443e0`
    [0x140444940], called from `FUN_14044a910`, which asks the target's `IsImmuneToAttack`
    (JumpTable 8 i-frames, bit `0x2`). If the target is immune, there is no `HitChr`, no
    `CalculateDamage2`, no 1665, and the buff stays. A parry returns from `FUN_14044a910`
    before that gate as well. In PvP the attacker's machine decides: for a remote victim, mode 4
    sends the hit packet and runs `HitChr` with the predicted flag on the replica, and
    `ApplyDamage` 0x1404497d0 passes that flag to `CalculateDamage2`, where it gates only the HP
    write. So the buff is spent on the attacker's own machine exactly when it registers a hit.
    The victim's machine then takes the packet's HP without consulting its own i-frames (combo.md
    section 12; `FUN_14044cba0` and the dequeue loop in `0x140660120` re-checked here).
  - Verdict on "it also goes away when I hit a rolling enemy and deal no damage": not supported.
    A contact with an i-framed target, as the attacker's machine sees it, spends nothing. A
    contact it registers spends the buff and deals damage on the victim's machine whatever the
    victim's roll is doing there. What the trace allows is the latency case: a roll that has
    begun on the victim's screen but not yet on the attacker's replica is a registered, damaging
    hit that spends the buff. So charges consumed per fight = registered hits = landed hits plus
    blocked hits; i-framed contacts add nothing. The ranking has no blocked-hit share, so its
    one-charge-per-landed-hit rule is unchanged (INFERRED: `damageInfo+0x267` bit 8, copied from
    the hit packet, skips the whole loop; what sets it was not traced).
  - Defender one-hit rows (Opaline Bubbletear 3507, Uplifting Aromatic's 503500,
    `deleteCriteriaDamage` 1): VERIFIED `FUN_1404f67c0` [0x1404f7590], the only caller being the
    end of `CalculateDamage2`, zeroes the remaining time of every such row on the victim when the
    hit is not zero-damage and its final damage (`AttackDamageInfo+0x228`) is above 0. A hit the
    attacker's machine did not register never gets there, so an i-framed hit does not spend these
    either; a fully blocked hit (damage 0) does not, a chip-damage block does.
  - Not traced: whether the victim machine's copy of 1665, sent to the remote dealer replica by
    `FUN_1403e8c90`, reaches the attacker's machine. It cannot change the count: that machine
    already spent the buff when it sent the packet.
- **Roles.** Duel, gank and 2v2 builds are taken as hosts. A summoned duelist is a cooperator,
  for whom a great rune does nothing; the tags cannot tell the two apart.

## 9. Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --table [--kind tear]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --show "Golden Vow" "Opaline Hardtear"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --context "Golden Vow" \
    "Flame, Grant Me Strength" "Opaline Hardtear" "Morgott's Great Rune" --pvp --phys-type strike \
    [--role invader] [--hand left] [--two-handed] [--sub 100] [--guarding]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --corpus --rl 140-160
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --expected --rl 140-160 \
    [--archetype Strength] [--weapon Greatsword --aff Heavy --stats str=60,dex=12 --two-handed] \
    [--skill-buff 1860] [--fight-seconds 180 300]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-buffs.py --recast [--fight-seconds 180 300] [--top 30]
```

The self-test checks:
- the regulation values the tables cite;
- the item-to-row links;
- the stacking replay (R1, R2, R3 on the section 3 cases);
- the hand, sub-category, guard and role gates;
- the float32 products.
- the section 10 uptime rules, weapon-buff slot, kit factors, skill alternatives and defender side;
- the fight range and recasts: a 60 s buff in a 180-300 s fight gets 2 to 4 recasts, a next-hit
  buff one per landed hit, the use limits, the recast time factor in `post`, and the engagement
  spacing staying 5 s.

## 10. Expected factors over the corpus (the ranking's buff term)

This section gives, per weapon as built, the factors the buffs that PvP builds actually carry put
on a hit, averaged over those builds. `er-builds-pvp.py` used to assume no buff on anyone. It now
applies these factors to every slot and skill hit (`Mechanics.buffs`, `slot_hit` `buff`), with
Strength as the attackers' tag (`SCORE_BUFF_ARCHETYPE`); `--no-buffs` switches them off.

**Kits (SITE).** `corpus_kits` turns every PvP build of the RL window into a kit: its great rune,
crystal tears, tool slots and spell slots, resolved to buff sources the way section 7 counts them.
- Attackers are the archetype's builds (default the Strength tag: 360 at RL 140-160).
- Defenders are every PvP build (1141).
- 72% of the Strength kits hold at least one buff source.
- Spell slots are almost empty in the scrape (section 7), so Golden Vow and FGMS are in
  practically no kit. That is a hole in the data, not a finding about players.

**Roles (SITE + INFERRED).** A build's role comes from its tags: Invasions is invader, every
other PvP tag is host. Of the Strength kits, 29.5% of the weight is invader, whose great rune does
nothing except Mohg's (section 4).

**Order inside a kit (INFERRED).** A kit that holds two rows of one exclusive category keeps only
the one the replay applies last (Exalted Flesh and Boiled Crab are both 151). Attacker kits apply
their offensive sources last, and defender kits their defensive ones.

**Fight length (user directive 2026-10-01; the range itself is the user's, INFERRED).** A fight
lasts 3 to 5 minutes. It used to be 25 s (5 landed hits x `ENGAGEMENT_SECONDS` 5 s), so a 60 s
weapon buff was cast once before contact, free, and never recast: Cragblade and Royal Knight's
Resolve cost nothing. Now two things are kept apart:

| quantity | value | read by |
|---|---|---|
| landed hits `hits` | 5 at RL 150 (`Defenders.fight_engagements`, HP-based) | one-hit buffs, the engagement count |
| engagement spacing `ENGAGEMENT_SECONDS` | 5 s (INFERRED) | status decay between hits, the spacing a visible skill buff must outlast, sustain pacing, per-opening credit |
| fight length `FIGHT_SECONDS` | 180..300 s, sampled every 10 s (13 points, equal weights) | buff duration, uptime, recasts, recast time cost, nothing else |

A recast count is a step function of the fight length (ceil(fight / duration) - 1), so uptime,
recasts and the time factor are each the mean over the 13 points, not the value at 240 s.
`er-builds-pvp.py --fight-seconds MIN MAX` and `er-mechanics-buffs.py --fight-seconds MIN MAX`
change the range; `--engagement-seconds` no longer touches it.

**Recasts (`recast_plan`, `source_plans`, `source_recast`).** Every buff is applied before the
first hit, free (INFERRED). After that every timed buff is recast as often as keeps it on the
whole fight, capped by its use limit, and each recast takes its cast frames out of the fight:
time factor `1 - recasts x cast / fight`. The same rule now runs every buff kind:

| buff | use limit | cast frames per recast | where |
|---|---|---|---|
| kit consumable (aromatics, flesh, crab, Golden Vow item...) | `EquipParamGoods.maxNum` (INFERRED as what a PvP player carries; 10 for most, 99 for Boiled Crab/Prawn, 30 for Drawstring greases) | `er-mechanics-status.cure_frame` 31, the bolus clip's goods frame standing in for every item clip (INFERRED) | `kit_factors`, `expected_defense` |
| kit spell | casts one FP bar pays for, full casts only (`Magic.mp`; whether spells have the skills' half-cost rule is not traced) | `SPELL_CAST_FRAMES` 60 (INFERRED stand-in: no spell clip is read) | same |
| crystal tear | 1 (one physick charge, COMMUNITY): never recast | - | same |
| great rune | none needed (permanent) | - | same |
| skill buff (option, ashes-of-war.md 16b) | `fp_uses` of the FP bar | its opening's first roll frame (`anim_recovery`) | `er-mechanics-ashes.buff_option` |
| left-hand weapon buff (`--setup`) | skill: `fp_uses`; grease: `maxNum` | skill: as above; grease: `cure_frame` | `er-builds-pvp.SetupBuffs.effect` |
| right-hand grease (sweep) | `maxNum` | `cure_frame` | `er-builds-pvp.Mechanics.grease_plan`, every greased row (it was always-on before) |

A next-hit row (`deleteCriteriaDamage` 1, `stateInfo` 384/385) keeps its per-hit rule: one cast
per landed hit, `hits` - 1 recasts, whatever the fight length (so Royal Knight's Resolve's 4
recasts cost 4 casts out of 180-300 s, a smaller share than they did of 25 s). A row's uptime is
then:

| row | uptime | example (180-300 s) |
|---|---|---|
| duration -1 | 1 | great runes |
| one-hit | (1 + recasts) / hits | Uplifting Aromatic's x0.1 guard: 5/5 (it was 1/5); Royal Knight's Resolve: 5/5 |
| under 2 s, or 0 | 0 | Thorny Cracked Tear's 1.5 s tiers; what keeps them alive is not traced |
| otherwise | (1 + recasts) x duration / fight, capped at 1, mean over the points | a 180 s tear: 0.769 (one charge); a 25 s Drawstring grease: 1 (9 recasts) |

Each surviving row contributes `1 + uptime x (factor - 1)`. Where the time is charged:

- An attacker kit's summed recast time is folded into its `post` (`kit_factors` `time_factor`,
  0.9916 for the Strength kits). It is the same for every weapon unless a skill alternative
  differs.
- A skill buff's time is charged by its own option (`buff_option`), not in `kit_factors`.
  `buff_option` used to spread a recast's frames over the 5 engagements as extra commitment,
  `C / (C + recasts x cast / 5)`, while `SetupBuffs` took them off the fight. The two only agreed
  by accident while the fight was 25 s and almost nothing was recast. Both now use the time factor.
- A defender kit's casts are not charged: that is the defender's time, not the attacker's damage
  per hit.

The next gap is the cast's punish exposure: a recast is a window the opponent can hit, and only
its time is charged here.

**Result on the setup ranking (combo.md section 11a, RL 150, all 324 lefts).** Cragblade (60 s,
84-frame cast: 3.38 recasts, time factor 0.961) goes from the kept left buff on 205 of 411 rows
to 0. Royal Knight's Resolve goes from 131 to 410, because a next-hit buff's recasts follow the
landed-hit count (still 5), not the fight length: its 4 recasts at 29 frames cost 15% of a 25 s
fight and 1.7% of a 180-300 s one. That is an artifact of keeping 5 landed hits in a 3 to 5
minute fight, not a finding; the hits per fight is the measurement it needs.

**The weapon-buff slot (VERIFIED, params).** The buff ashes store their effect in `spCategory`
162 (right hand) and 163 (left), the same two categories as the greases. The replay then does
the rest:
- `War Cry then a grease`: the grease replaces the roar. The selftest checks the reverse,
  `grease then War Cry`.
- The grease sweep already chose each greased build's grease, so a kit's own greases are dropped.
- On a greased build, a skill's weapon-buff row is dropped too (`drop_skill_weapon_buffs`), since
  the grease holds the slot. Whether a player would roar instead of greasing is not modelled;
  the demo below shows both.

**Can the weapon take a grease (VERIFIED).** `EquipParamWeapon.isEnhance` gates weapon-buff items
(grease.md 3c). It is set only on Standard, Heavy, Keen and Quality rows (228/172/172/172 of the
rows of 676/240/240/239 per affinity). Every elemental affinity has it at 0, except for one or two
rows. Of the base weapons, 99 of the 265 that take ashes and 326 of the 336 that do not have it
at 0. `can_take_weapon_buff`
reads it for the exact row. A skill's own buff never passes through `CanUseGoods`, so Sacred
Blade coats a Sacred weapon (INFERRED: the TAE path reads no `isEnhance`).

**Skills.** `er-mechanics-ashes.skill_term` hands over `buff_alternatives`: the buff rows of each
skill the corpus mounts on the weapon, with that skill's probability p and its FP casts. A weapon
holds one skill, so `expected_attack` scores each kit once per alternative and weights the results
by p. They are never stacked together; if they were, a replay would let Sacred Blade's 821 replace
Braggart's Roar's 1861.

**Skills as options (ashes-of-war.md section 16b, the default since 2026-09-30).** The corpus
alternatives above are now left out of the ranking's base (`--no-buff-options` puts them back).
Instead every mountable skill that buffs its user, in its opening or in a follow-up it offers
(Flaming Strike's 040010), is scored as its own option: `Mechanics.skill_buff` runs
`expected_attack` with that skill's roots at p 1 and its FP casts, `drop_skill_weapon_buffs` off,
and reports whether its rows are 162/163 (`weapon_slot`). When they are, the sweep's grease comes off
the build for that option, since the two replace each other. Every slot is then hit again with those
factors and the moveset scored again. Since 2026-10-01 a 60 s roar or a 40 s blade no longer
covers the fight with its first cast: it is recast by the rule above (2 to 4 times for 60 s).

**Outputs.** Per element:

| output | what it is | where it applies |
|---|---|---|
| `pre` | AR-rate product x the AR ratio of the kit's attribute adds (`ar_stat_ratio`, `er-mechanics-ar.attack_rating`) | before defense |
| `post` | `atkPlayerDmgCorrectRate` x `*AttackRate` | after defense |
| `flat` | flat adds | after the AR multipliers, times the attack's `byPoint` |
| `joint` | mean of pre x post | one factor per element |

`expected_defense` gives the defenders' `cut x correct` factor per damage key, plus their mean
max-HP rate, which is reported but not applied.

**Measured at RL 140-160 (Strength attackers vs all PvP defenders, 2026-09-29):**

| | physical | magic | fire | lightning | holy |
|---|---|---|---|---|---|
| attacker `post`, no skill buff | 1.0309 | 1.0041 | 1.0045 | 1.0024 | 1.0031 |
| defender factor | 0.9167 (every physical type) | 0.9368 | 0.9376 | 0.9367 | 0.9369 |
| attacker `post`, 180-300 s fight (2026-10-01, recast time factor 0.9916 included) | 1.0193 | 0.9951 | 0.9954 | 0.9938 | 0.9943 |
| defender factor, 180-300 s fight | 0.8487 | 0.8636 | 0.8645 | 0.8637 | 0.8637 |

The 2026-10-01 rows: the defender side moves most, and not because of the fight length. Uplifting
Aromatic (142 of 1141 defender kits) is a 0.1 damage-taken row that ends on the next hit taken;
it used to cover 1 of 5 hits and is now recast before each (maxNum 10), which alone takes the
defender factor from 0.9167 to 0.8415 at 25 s. The longer fight then lowers the one-charge tears
(Opaline 0.9 at uptime 0.769), back up to 0.8487. The defender factor is close to uniform across
weapons, so it moves the scale more than the order.

- **Attacker.** Opaline Hardtear is on 43% of the kits and Morgott's rune is live on 23% (after
  roles). Uplifting Aromatic's 1.075 attack row is on 16% and Exalted Flesh on 9%.
- **Defender.** Opaline Hardtear's 0.9 on 43% of the kits, and Boiled Crab's 0.85 on physical
  only. So buffs move a physical hit about 2% more than an elemental one.

**Per weapon, with the skill alternatives.** Heavy builds with the lightning Drawstring grease
from the sweep, damage-weighted over their element mix:

| weapon | skill buffs in its choice | joint physical, grease holds the slot | joint physical, skill buff kept | x defender |
|---|---|---|---|---|
| Giant-Crusher | Braggart's Roar p 0.31, Sacred Blade p 0.10 | 1.0315 | 1.0632 (+9.3 holy flat) | 0.945 / 0.973 |
| Greatsword | Royal Knight's Resolve p 0.09 | 1.0314 | 1.0704 | 0.945 / 0.981 |
| Claymore | Flame Spear p 0.12 | 1.0314 | 1.0314 (+11.6 fire flat) | 0.945 |

Royal Knight's Resolve reaches every one of the 5 hits here because a 85-FP bar pays for 6 casts
at 15 FP. (Table of 2026-09-29, 25 s fight. Since 2026-10-01 its 4 recasts are charged their
cast time, section 10.)

```python
B = importlib (scripts/er-mechanics-buffs.py); m = B.Buffs()
att = B.corpus_kits(m, rl=(140, 160), archetype='Strength', order='offense-last')
dfn = B.corpus_kits(m, rl=(140, 160), archetype=None, order='defense-last')
ratio = B.ar_stat_ratio(weapon, aff, level, stats, two_handed)
e = m.expected_attack(att, two_handed, ratio, alternatives=term['buff_alternatives'],
                      drop_skill_weapon_buffs=bool(grease), fight_seconds=..., hits=...)
d = m.expected_defense(dfn, fight_seconds=..., hits=...)
m.can_take_weapon_buff(weapon_row_id)
```

## 11. Healing over time (the ranking's sustain term)

`er-builds-pvp.py --sustain window|paced`, off by default. `er-mechanics-buffs.py --regen` lists
the sources and the defenders carrying them.

**What heals (VERIFIED, params).** A row heals its owner when `changeHpPoint` or `changeHpRate` is
negative; poison and rot write the same columns positive (`er-mechanics-status.proc_effect`
subtracts them, so the sign is the split). It ticks every `motionInterval` s. Rows that live
under 2 s or 0 s are left out (Crimson-Sapping's 0.25 s on-hit row, Crimsonwhorl's chained
one-shot, Minor Erdtree's field ticks), as are instant heals (Heal, Crimson Crystal Tear).

| source | row | HP/s | duration | in RL 140-160 PvP builds |
|---|---|---|---|---|
| Bestial Vitality (spell, and implied by a held Frenzied Flame Seal) | 1685000 | 5 | 120 s (600 HP/cast) | 284 |
| Icon Shield (resident, first slot of a hand) | 5321400 | 3 | permanent | 68 |
| Crimsonburst Crystal Tear | 511009 | 7 | 180 s | 21 |
| Blessed Dew Talisman | 350200 | 2 | permanent | 17 |
| Royal Remains, per piece | 6069000..6069030 | 2 | permanent | 7-12 per piece |
| Gourmet Scorpion Stew / Scorpion Stew | 20501207 / 20501202 | 12 / 8 | 60 s | 10 / 0 |
| Blessing of the Erdtree / Blessing's Boon | 1643100 / 1643000 | 12 / 8 | 90 s | 0 |

None of these rows carries `changeHpRate`, so every heal is flat HP. The timed heals all sit in
`spCategory` 161 and replace each other (section 3): a build holding two keeps the stronger
(applied weakest first, INFERRED). Equipment rows are category 0 and stack. That a weapon's
resident heal needs it in the first slot of a hand is INFERRED. 356 of 1118 PvP defenders heal;
over the 25 s of engagements (5 x `ENGAGEMENT_SECONDS`; sustain is paced by the engagements, not
by section 10's 3 to 5 minute buff fight) the mean is 44.8 HP per defender, 140.7 per healer,
against a median HP of 1946.

**The fold.** Effective HP over a fight of n engagements is HP + R(n x `ENGAGEMENT_SECONDS`),
every row live from the start and cast once (INFERRED, as section 10).

- `window`: n = `fight_engagements` (5). The factor HP / (HP + R), meaned over defenders, is
  0.9801 for every weapon.
- `paced`: n solves n x hit = HP + R(n x 5 s) per defender, `hit` = the slot's landed damage plus
  status HP per engagement; the factor is HP / (n x hit). A weapon that needs more engagements
  gives the heals longer: 0.941 at 150 HP per engagement, 0.990 at 900.

The factor multiplies the landed part of `slot_score`'s rate (damage, status and crit HP), not
the parry and whiff punishes.

**Measured, RL 150, 822 rows (same tree, three runs).**

| run | score / base | max rank move | mean rank move | pct coef [CI] | rho all | rho adopted |
|---|---|---|---|---|---|---|
| base | 1 | - | - | +0.546 [+0.272, +0.799] | +0.460 | +0.264 |
| window | 0.976-1.019 (mean 0.979) | 17 | 0.58 | +0.544 [+0.272, +0.797] | +0.460 | +0.263 |
| paced | 0.920-0.996 (mean 0.974) | 17 | 2.15 | +0.562 [+0.280, +0.818] | +0.460 | +0.262 |

- `window` is uniform up to the unscaled punish terms and a few best-opener switches (Banished
  Knight's Shield 2H 743 -> 726).
- `paced` is not uniform, but slow weapons do not lose more. Over the rows' best openers the
  score ratio has Spearman +0.845 with landed damage per engagement and +0.118 with commitment:
  the low-damage quartile keeps 0.958, the high-damage quartile 0.983; the fast-commit quartile
  0.972, the slow one 0.975. Fight length here is counted in engagements, not swings, so a slow
  swing does not lengthen it; a light hit does. Daggers and Misericorde fall 10-14 places, jump
  attacks of heavy swords rise 7-9. The top 25 changes membership at the edge.
- The adoption coefficient moves inside its CI in both modes: not supported or refuted by the
  corpus.

Not modelled: the `hpRecoverRate` column that scales incoming heals (26 rows not at 1.0), recasts
within a fight, the time a cast costs, and the per-defender spread of landed damage (`paced` uses
the slot's mean hit against each defender's own HP and heals).
