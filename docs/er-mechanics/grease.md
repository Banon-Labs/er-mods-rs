# Elden Ring weapon greases: how the flat elemental attack reaches a hit

How `SpEffectParam.{physics,magic,fire,thunder,dark}AttackPower` (holy is `dark`) from a grease
enters a player weapon's per-element attack, which weapons and attacks it applies to, and how it
meets defense. This closes the "buff, talisman and SpEffect terms enter through attack-context
values that aren't modelled" gap in `attack-rating.md` for the flat-add half of those terms.

Labels:
- **VERIFIED**: read from regulation params (`scripts/er-param-read.py`, installed 1.17.1
  regulation, Smithbox paramdefs) or from the executable.
- **INFERRED**: consistent with the data or the names, but the code path was not traced.

Addresses are written `1.16.2 VA / 1.17.1 VA`. The 1.16.2 side comes from the named Ghidra dump on
`localhost:8765` (dump VA = deobf VA = runtime VA on 1.16.2). Every function below sits under the
1.17.1 boundary `0xafefe9`, so its 1.17.0 and 1.17.1 addresses are equal. The 1.17.1 side is from
`docs/recon/rva-map-1162-to-1170.functions.tsv`, except the leaf getters, which have no `.pdata`
entry and were located by searching `eldenring-deobf-1.17.1.bin` for their exact 24 leading bytes
(one hit each, all `+0xdd0`). The accumulator `0x1404f4520 / 0x1404f52f0` and the hand gate
`0x140500930 / 0x140501700` were disassembled out of both images with capstone: 609/609 and
136/136 identical mnemonics, and the 42 and 8 operands that touch the SpEffect/AtkParam fields
named below are identical. Nothing was launched.

## 0. The answer

For a magic, fire, lightning or holy hit (`e`), with `attack-rating.md` section 1 for the weapon
part:

```
hit[e] = W[e] + G[e]

W[e] = ((attackBase[e] * reinforce.<e>AtkRate [+ 2nd row]) * AtkParam.atk<e>Correction * 0.01
        + isAddBaseAtk term) * M[e] * durability * ctxRate[e] * ctxVec[e] * throwRate * FUN_140691320(...)

G[e] = sum over active, applicable SpEffects s of
         s.<e>AttackPower * c_point(s) * adj(s)                 (for <e>AttackPower > 0)
c_point(s) = s.isUseAtkParamAtkPowerCorrect ? AtkParam.spEffectAtkPowerCorrectRate_byPoint * 0.01 : 1
adj(s)     = product of four per-entry floats, each 1.0 unless s.bAdjust{Magic,Faith,Strength,Agility}Ablity
```

- **Flat, added last (VERIFIED).** `G[e]` is added after every multiplier on the weapon part:
  after the stat multiplier `M[e]`, after the motion value `atk<e>Correction`, after durability,
  after the SpEffect attack-rate product. It is not multiplied by upgrade level, stats,
  `<e>AttackPowerRate` or motion value.
- **It has its own motion value (VERIFIED).** Every grease row has
  `isUseAtkParamAtkPowerCorrect = 1`, so `G` is scaled by the attack's
  `AtkParam.spEffectAtkPowerCorrectRate_byPoint`. That column is independent of `atk*Correction`:
  in `AtkParam_Pc` it is 100 on 8083 of 11017 rows, 90 on 1291, 0 on 733 (mostly bullets), 50 on
  383, down to 25 on some multi-hit Ashes of War, and 135 on the Spinning Wheel finishers. A dagger
  backstab (row 100500) has `atkPhysCorrection 294` and `byPoint 100`, so Fire Grease adds 85 there,
  not 250.
- **For greases `adj = 1` (VERIFIED, params).** All grease rows have the four `bAdjust*Ablity`
  flags at 0.
- **So Fire Grease on a normal moveset attack is +85 fire per hit** (+110 for Drawstring), whatever
  the weapon, its level, the player's stats or the attack's motion value. It then goes through fire
  defense summed with the weapon's own fire (section 4).
- **Use requires `isEnhance` (VERIFIED); damage does not re-check it (VERIFIED).** The gate is
  `CanUseGoods`, at item use (section 3).

## 1. The grease rows (VERIFIED, params)

`EquipParamGoods.refId_default` points at the right-hand row. Each grease has a Right and a Left
SpEffect; the left rows are the right id + 2.

| goods | SpEffect (right / left) | field | value | `stateInfo` right / left | `wepParamChange` right / left |
|---|---|---|---|---|---|
| Fire Grease 1400 | 3160 / 3162 | `fireAttackPower` | 85 | 62 / 158 | 1 / 2 |
| Lightning Grease 1410 | 3165 / 3167 | `thunderAttackPower` | 85 | 151 / 158 | 1 / 2 |
| Magic Grease 1420 | 3170 / 3172 | `magicAttackPower` | 85 | 64 / 158 | 1 / 2 |
| Holy Grease 1430 | 3185 / 3187 | `darkAttackPower` | 85 | 205 / 158 | 1 / 2 |
| Holy Water Grease 3300 | 3375 | `darkAttackPower` | 95 | 61 | 1 |
| Drawstring Fire/Lightning/Magic/Holy | 3161, 3166, 3171, 3186 (+2 left) | as above | 110 | as above | 1 / 2 |

- Duration is `effectEndurance` 60 s (Drawstring 11 s).
- No grease sets `physicsAttackPower` or `slash/blow/thrust/neutralAttackPower`, and none sets an
  `*AttackPowerRate`.
- Every row has `isUseAtkParamAtkPowerCorrect = 1`, `bGameClearBonus = 0`,
  `throwAttackParamChange = 0` and `magicSubCategoryChange1..3 = 0`.
- The status greases (Blood, Poison, Freezing, Soporific, Rot) carry no attack power on the
  weapon-side row. Each has a paired "(On Attack)" row (`wepParamChange 3`, status `stateInfo`)
  that holds the build-up. Dragonwound Grease (3260) has no attack power either. Neither mechanism
  is covered here.
- Paramdef byte offsets (Smithbox, `layout()`): `physicsAttackPower +0x58`, `magicAttackPower
  +0x5c`, `fireAttackPower +0x60`, `thunderAttackPower +0x64`, `darkAttackPower +0x1e4`,
  `slash/blow/thrust/neutralAttackPower +0x230..+0x23c`, `stateInfo +0x156`, `wepParamChange
  +0x158`, `bAdjustMagicAblity`/`bAdjustFaithAblity` `+0x160` bits 4/5,
  `bAdjustStrengthAblity`/`bAdjustAgilityAblity` `+0x164` bits 0/1, `isUseAtkParamAtkPowerCorrect
  +0x259` bit 1. `AtkParam.spEffectAtkPowerCorrectRate_byPoint/_byRate/_byDmg` are `+0x18e/+0x190/+0x192`.
  Ghidra's `SP_EFFECT_PARAM_ST` type carries stale names for several of these bits (for example it
  calls `+0x259` bit 1 `isStopSearchedNotify`), so the offsets above were read from disassembly and
  named from the paramdef.

## 2. Collection into the attack context (EXE)

`FUN_1404f4520 / 0x1404f52f0`, `(SpecialEffect*, AttackInfo*)`, walks every active SpEffect entry
and writes the attack context that `FUN_1406832a0` later reads. It is the "ctx-add[e]" and
"ctx-rate[e]" of `attack-rating.md`.

**Per-attack correction rates.** Read once from the attack's own AtkParam row
(`GetAtkParam(isPlayer, AttackInfo.behaviorParamRefId)`):

```
1404f47c7  MOVZX EAX,word ptr [RCX + 0x18e]    ; byPoint
1404f47dc  MULSS XMM1,XMM0                     ; * 0.01 ([0x14329e624])  -> [RSP+0x34]
1404f47d2  MOVZX EAX,word ptr [RCX + 0x190]    ; byRate  * 0.01          -> [RSP+0x38]
1404f47ea  MOVZX EAX,word ptr [RCX + 0x192]    ; byDmg   * 0.01          -> [RSP+0x24]
```

**Per entry.** The three factors default to 1.0 (`XMM8`) and switch to the AtkParam values only
when the SpEffect asks for it:

```
1404f490b  MOVZX EAX,byte ptr [RAX + 0x259]
1404f4912  SHR EAX,0x1 / AND EAX,0x1           ; isUseAtkParamAtkPowerCorrect
1404f491b  MOVAPS XMM9,XMM11                   ; add factor  = byPoint*0.01
1404f491f  MOVAPS XMM10,XMM12                  ; rate factor = byRate*0.01
1404f4923  MOVAPS XMM6,XMM13                   ; dmg factor  = byDmg*0.01
1404f492a  CALL 0x140500010                    ; physicsAttackPower
1404f492f  MULSS XMM0,XMM9
1404f4939  ADDSS XMM0,dword ptr [RBP + RDI*0x4 + -0x80]
```

In the decompile, each add is `acc[b] += getter(entry) * c_point` and each rate is
`acc[b] *= rateGetter(entry,i) * c_rate`. `b` is 0, or 1 for the stateInfo group in section 3; the
two buckets are merged at the end (adds summed, rates multiplied), so the split does not change the
result.

**The getters** read the int field as a float. When the value is positive they multiply it by four
floats on the SpEffect entry:

```
FUN_1404ff870 / 0x140500640 (fire)
  fVar1 = (float)*(int *)(row + 0x60);
  if (0.0 < fVar1) return fVar1 * entry[+0x5c] * entry[+0x58] * entry[+0x50] * entry[+0x54];
```

Same shape for `0x140500010 / 0x140500de0` (`+0x58` physical), `0x1404ffbd0 / 0x1405009a0`
(`+0x5c` magic), `0x1405003b0 / 0x140501180` (`+0x64` lightning), `0x1404ff6d0 / 0x1405004a0`
(`+0x1e4` holy), and `0x1404fff80(i)` (1.16.2) for `+0x230/+0x234/+0x238/+0x23c`.

**The four entry floats (VERIFIED).** `SpecialEffectEntry` `+0x50..+0x5c` are set to 1.0 by
`FUN_1404fec90` in the entry constructor. `FUN_140500e70 / 0x140501c40` overwrites each from a
caller-supplied vector only when the matching paramdef flag is set:

```
140500e9b  TEST byte ptr [RAX + 0x164],0x1   -> [RBX + 0x58]   bAdjustStrengthAblity
140500eb2  TEST byte ptr [RAX + 0x164],0x2   -> [RBX + 0x5c]   bAdjustAgilityAblity
140500ec9  TEST byte ptr [RAX + 0x160],0x10  -> [RBX + 0x50]   bAdjustMagicAblity
140500edf  +0x160 >> 5 & 1                   -> [RBX + 0x54]   bAdjustFaithAblity
```

Greases set none of them, so all four stay 1.0.

**Where the sums land (VERIFIED, decompile tail of `FUN_1404f4520`).** In the `AttackInfo` that
`FUN_1406832a0` receives as `param_6`:

| AttackInfo | holds | SpEffect source |
|---|---|---|
| `+0x48` | physical add | `physicsAttackPower` |
| `+0x4c/+0x50/+0x54/+0x58` | physical sub-type add | `slash/blow/thrust/neutralAttackPower` |
| `+0x5c` | magic add | `magicAttackPower` |
| `+0x60` | fire add | `fireAttackPower` |
| `+0x64` | lightning add | `thunderAttackPower` |
| `+0x68` | holy add | `darkAttackPower` |
| `+0x6c`, `+0x70..+0x7c`, `+0x80..+0x8c` | rate products, same order | `FUN_1404ff3e0(i)`, i = 0..8 |

The rate getter `FUN_1404ff3e0` multiplies by `ClearCountCorrectParam.<X>AttackRate` when
`bGameClearBonus` (`+0x160` bit 6) is set. That NG+ factor reaches the rates only, never the adds.
Which paramdef fields `FUN_140d4fdf0` reads for the nine rates was not checked; the order matches
the ClearCountCorrectParam names (physics, slash, blow, thrust, neutral, magic, fire, thunder, dark).

## 3. Which SpEffects count for a given attack (EXE)

### 3a. Entry filter in the accumulator

For each entry, `FUN_1404f4520` skips it (VERIFIED, `0x1404f486a..0x1404f48ec`) when:

- `stateInfo` is 197 (`0xc5`) or 315/316 (`0x13b`, `0x13c`);
- `entry.flags & 0x800c0003` is non-zero;
- `IsApplicableForCategory` (3b) returns false;
- `stateInfo` is 123..126 or 186 and `FUN_140500b70(entry, AttackInfo+0x40)` is false. That
  function requires AttackInfo `+0x40` bit `0x4` for 123/124, `0xb4` for 125, `0x4c` for 126,
  `0x48` for 186. When `AttackInfo+0x10 == 5`, every entry goes through that check.

Grease `stateInfo` values (62, 151, 64, 205, 61, 158) are in none of these sets.

### 3b. Hand gate: `IsApplicableForCategory` `0x140500930 / 0x140501700`

It switches on `AttackInfo+0x10` through the jump table at `0x140500b08` (read out of
`eldenring-deobf.bin`), tested against `SpEffectParam.wepParamChange` (`+0x158`: 0 none,
1 right-hand, 2 left-hand, 3 self, 4 kick):

| `AttackInfo+0x10` | rule | code |
|---|---|---|
| 1 | reject `wepParamChange` 2, 3, 4 | `0x140500985..0x140500996` |
| 2 | reject 1, 3, 4 | `0x1405009af..0x1405009b8` |
| 12 | accept 1; accept 2 only if `GetArmStyle()` (vcall `+0x250`) is 2 (`LeftBothHands`); accept 0/5/6 (`IsWepParamChange056`) | `0x140500a49..0x140500a8b` |

An accepted entry must also pass `CheckMagicSubCategoryChangeMask` `0x140d50880` (all greases have
`magicSubCategoryChange* = 0`; that 0 counts as "no restriction" is INFERRED from the
compare-to-constant at `0x143d67a08`). If `throwAttackParamChange` (`+0x164` bit 3) is set, the
hit must also be a throw (`AttackInfo+0x109`); greases have it clear.

**Where `+0x10` comes from (VERIFIED).** On a real attack, `FUN_1404428f0 / 0x140442e50` looks up
the BehaviorParam row and calls `FUN_14068ffa0 / 0x140690df0(&info, chr, refId, ..., category,
mask)`, which stores `BehaviorParam.category` into `+0x10` and the mask into `+0x40`, then calls
the accumulator (`0x1406900a0`). In `BehaviorParam_PC`, the dagger moveset rows 100100000.. have
category 1, 100100200.. category 12, and 100100400.. category 2 (REGULATION). That 1 is the
right-hand moveset, 2 the left-hand one and 12 the two-handed one is INFERRED from those ranges and
from the other two callers: `FUN_14065c120` sets `(slot != right slot) + 1`, and the menu sets 1,
or 2 for the left hand.

**Net effect for greases (VERIFIED code, INFERRED category meaning).** A right-hand grease row
(`wepParamChange 1`) feeds right-hand and two-handed attacks. A left-hand row (`wepParamChange 2`)
feeds left-hand attacks, and two-handed attacks only while the left weapon is held two-handed.

### 3c. `isEnhance`: checked when the grease is used, not when it hits

`CanUseGoods` `0x14068e010 / 0x14068ee60`, `(goodsId, player, spEffect, chrType, rightWeaponId,
leftWeaponId, ...)`:

```
14068e289  MOVZX EAX,byte ptr [RCX + 0x4a]     ; EquipParamGoods +0x4a
14068e28d  SHR EAX,0x1 / AND EAX,0x1           ; goods.isEnhance
14068e29a  MOV EAX,EBX / SHR EAX,0x2           ; EBX = 1 << ChrAsm.armStyle
14068e29f  TEST R12B,AL                        ; armStyle == 2 (LeftBothHands)?
   yes: EquipParamWeapon::GetEntry(leftWeaponId)
14068e2f1  MOVZX R12D,byte ptr [RAX + 0x106]
14068e2f9  SHR R12B,0x7                        ; left weapon isEnhance
14068e302  TEST BL,0xa                         ; armStyle 1 or 3?
   yes: EquipParamWeapon::GetEntry(rightWeaponId)
14068e358  MOVZX R12D,byte ptr [RAX + 0x106]
14068e360  SHR R12B,0x7                        ; right weapon isEnhance
   armStyle 0 (empty-handed): R12 = 0
```

`EquipParamWeapon.isEnhance` is `+0x106` bit 7 in the paramdef. A zero result is one of the terms
of the function's final `return false` condition (`bVar34 == 0`). So a grease can be used only when
the weapon it would coat is buffable: the right weapon, or the left one while it is held
two-handed.

The damage path does not read `isEnhance`. Neither `FUN_1404f4520` nor `FUN_1406832a0` touches the
weapon row's `+0x106`. An enumeration of every `+0x106` displacement in the 1.16.2 image
(`scripts/find-deobf-field-access.py 0x106`, 51 hits) finds none in the SpEffect, AttackInfo or
damage code. The weapon-row readers it does find are `CanUseGoods`, `CanUseMagic`,
`CanBeCastedByWeaponParam`, the AI's `CanWeaponEnhance` and menu code.
`RemoveWepParam2And6SpEffects` `0x1404f6b00 / 0x1404f78d0` drops every entry with
`wepParamChange` 2 or 6; its callers, and whether a weapon swap clears right-hand buffs, were not
traced.

## 4. From the context to the hit, and through defense (EXE)

**The add is the last operation (VERIFIED).** `FUN_1406832a0 / 0x1406840f0`, fire element,
non-arrow branch:

```
140683849  MOVSS XMM12,dword ptr [RDI + 0x60]    ; AttackInfo fire add (G_fire)
140683852  MOVSS XMM13,dword ptr [RDI + 0x84]    ; AttackInfo fire rate product
140683891  ADDSS XMM8,XMM10                      ; baseR + baseL
140683896  MULSS XMM8,XMM9                       ; * atkFireCorrection*0.01 (motion value)
14068389b  ADDSS XMM8,XMM0                       ; + isAddBaseAtk term
1406838a0  MULSS XMM8,XMM11                      ; * M (FUN_140690390, stat multiplier)
1406838a5  MULSS XMM8,XMM14                      ; * durability
1406838aa  MULSS XMM8,XMM13                      ; * SpEffect fire rate
1406838af  MULSS XMM8,dword ptr [RBP + -0x50]    ; * FUN_1404f3c60 vector .z
1406838b5  MULSS XMM8,XMM15                      ; * throwAtkRate term
1406838ba  MULSS XMM8,dword ptr [RBP + 0xc8]     ; * FUN_140691320
1406838c3  ADDSS XMM8,XMM12                      ; + G_fire
1406838c8  MOVSS dword ptr [R12 + 0x8],XMM8      ; out[2]
```

Magic, lightning and holy add `+0x5c`, `+0x64`, `+0x68` the same way (loaded at `0x14068370e`,
`0x140683984`, `0x140683abf`). Physical adds `+0x48` plus the sub-type slot `+0x4c..+0x58` picked
by `AttackInfo+0x44` (`0x140683598`). The `AttackInfo+0xf4` branch has the same shape: its adds
are the `ADDSS` at `0x140683d69`, `0x140683e0c`, `0x140683e5c`, `0x140683eac` and `0x140683eff`.

**The call chain (VERIFIED to the damage record).**

```
FUN_1404428f0 (attack starts)
  -> FUN_14068ffa0: AttackInfo + FUN_1404f4520 (SpEffect sums)
  -> FUN_140526430 (DmgMan) -> FUN_140d24440 -> FUN_140d24b10 / 0x140d26290
       FUN_14038cb10 copies the AttackInfo
       FUN_140683020 / 0x140683e70 -> [chr vtable +0x358] = FUN_140651d40 / 0x140652b90 (players)
                                   -> FUN_1406832a0(out, ..., durability, AttackInfo, ..., specialEffect)
       *param_1 = out[0..1]; param_1[1] = out[2..3]; *(float*)(param_1+2) = out[4]
```

The vtable slot was found by searching the image for the pointer `0x140651d40`: it is `+0x358`
from a vtable whose base `ReplayGhostIns` references (`0x142a494d8`), and `FUN_140683020` is the
`call [rcx+0x358]` at `0x1406830c7`.

**Defense (VERIFIED shape, one INFERRED link).** `FUN_140d24b10` stores the five per-element
values at `+0x00..+0x10` of its output record. `CSChrDamageModule::CalculateDamage` reads
`AttackDamageInfo.rawDamage` at `+0x00..+0x10` in the same element order, and
`CalculateDamageBasic` runs `CalculateDefense(attack[e], defense[e])` on each (`defense.md`
section 0). That the record `FUN_140d24b10` fills is the `AttackDamageInfo` given to
`CalculateDamage` is INFERRED from the matching offsets and order; the hand-off was not traced. On
that reading the grease add is summed into the element before the defense curve, and is not
defended separately. Against 100 fire defense, with `defense_curve` from
`scripts/er-mechanics-defense.py`:

| weapon fire for this hit | + grease | damage, summed (game) | damage, defended separately |
|---|---|---|---|
| 150 | 85 | f(235) = 163.8 | f(150) + f(85) = 85.0 + 26.0 = 111.0 |
| 0 | 85 | f(85) = 26.0 | same |

**Menu (VERIFIED shape).** `FUN_1407c04d0 / 0x1407c1350` builds an AttackInfo with
`behaviorParamRefId 101`, `+0x10 = 1` (or 2 for the left hand), runs `FUN_1404f4520` over the
player's `MenuRefSpecialEffect`, then `FUN_1406832a0`. `AtkParam_Pc` row 101 has `byPoint 100`, so
that path yields weapon AR + 85 for Fire Grease. That this is the number the equipment screen shows
is INFERRED.

## Not established

- That the record `FUN_140d24b10` fills is the `AttackDamageInfo` whose `rawDamage`
  `CalculateDamage` reads (same offsets and order; hand-off not traced).
- The meaning of BehaviorParam `category` 1 / 2 / 12 (right, left, two-handed) is read from row
  ranges and the menu's use of 1 and 2, not from code that names them.
- `CheckMagicSubCategoryChangeMask`: that `magicSubCategoryChange = 0` means "applies to every
  attack" rests on the compare against the constant at `0x143d67a08`, whose value was not read.
- `FUN_140d50990` (`IsWepParamChange056`) and the `stateInfo` 123..126 / 186 mask bits at
  `AttackInfo+0x40`: which attacks set which bits.
- The four `SpecialEffectEntry +0x50..+0x5c` floats when a `bAdjust*Ablity` flag is set: which
  caller of `FUN_140500e70` (`FUN_1404fd090`) supplies the vector and what it holds. Greases do not
  use them.
- Resolved in `buffs.md` section 2: `FUN_140d4fdf0` reads the nine `*AttackPowerRate` columns
  (`+0x48`, `+0x220..+0x22c`, `+0x4c`, `+0x50`, `+0x54`, `+0x1e0`).
- The `FUN_1404f3c60` per-element vector and `FUN_140691320`. They multiply the weapon part only
  and do not reach the grease add.
- Resolved in `buffs.md` section 3: the grease rows and the weapon-buff incantations are
  `spCategory 162` (right) / 163 (left), and a new entry of that category takes over the live
  one's slot, so one weapon buff per hand is active.
- Callers of `RemoveWepParam2And6SpEffects`, and what removes a right-hand grease on a weapon swap.
- Status greases (the "(On Attack)" rows) and Dragonwound Grease.
- `FUN_1404fff80` (sub-type physical adds) and `FUN_140500b70` were not located in the 1.17.1
  image.
