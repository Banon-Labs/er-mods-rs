# ELDEN RING defender mechanics: defense, absorption, damage per hit, resistances, poise

Labels: **VERIFIED** = read from the regulation (`scripts/er-param-read.py`, Smithbox paramdefs,
installed 1.17.1 regulation) or from the executable; below it is written **REGULATION** or **EXE**
to say which. Executable reads come from the named 1.16.2
Ghidra dump on `localhost:8765`, and every function cited was carried to 1.17 with
`scripts/map-rvas-1162-to-1170.py` (all sit below the 1.17.1 boundary `0xafefe9`, so the 1.17.0 and
1.17.1 addresses are equal). The ones marked "(1.17.1 bytes)" were disassembled again out of
`eldenring-deobf-1.17.1.bin` and read the same constants and graph ids. **INFERRED** = consistent
with the data or names, but the code path was not traced. **SITE** = the build planner's JS
(`~/.cache/er-build-planner/js/notifications-BSZ1DATO.js`, where the character model lives;
`router-B83O9Qb7.js` holds only the armor optimiser). **COMMUNITY** = wiki claim. Nothing was launched.

Addresses are written `1.16.2 VA / 1.17.1 VA`.

Code: `scripts/er-mechanics-defense.py` (module and CLI; `--selftest`, `--corpus`).
**MEASURED** = computed over the scraped planner corpus (`~/.cache/er-build-planner/builds.jsonl`).

## 0. The hit, top to bottom (EXE)

`CSChrDamageModule::CalculateDamage` `0x1404472b0 / 0x140447810` (called from `ApplyDamage`) builds
the inputs and calls `CalculateDamageBasic` `0x1406849d0 / 0x140685820`:

```
for each element e in (physical, magic, fire, lightning, holy):      # holy is "dark" in code
    d[e] = CalculateDefense(attack[e], defense[e])
         * armorAbsorption[e] * unk1[e] * spCut[e] * guard[e] * spCut110[e] * spCut434[e]
         * throwCut[e] * corrections[e]
    d[e] = max(d[e], 0)
k     = 1.0, or a hit-state factor (below)
total = (sum d[e]) * k * mpCorrection
if 0 < total < 1: total = ceil(total)          # a landing hit does at least 1
```

| factor | source | label |
|---|---|---|
| `attack[e]` | `AttackDamageInfo.rawDamage` (+0x00), times `CalculateDefObjectAttackPowerRate` for object attackers and the stealth rate for stealth hits | EXE for the multipliers; what rawDamage holds is INFERRED (below) |
| `defense[e]` | chr vcall, for players `CalculateDefenseData` `0x140685650 / 0x1406864a0` (section 1) | EXE |
| `armorAbsorption[e]` | chr vcall `CalculateAbsorptions` `0x140652d20` -> `0x140689c80 / 0x14068aad0` (section 2) | EXE |
| `spCut`, `spCut110`, `spCut434` | `SpecialEffect::CalculateDefenseModifiers` `0x1404f53e0 / 0x1404f61b0`, products of SpEffect `*DamageCutRate` (section 2); `spCut110` is the counter-hit factor (section 2b) | EXE |
| `guard[e]` | `CalculateGuardDamage`, only when the hit is blocked | EXE (not modelled) |
| `throwCut[e]` | `CalculateDamageCutRates`, only for throws with `AtkParam.throwDamageAttribute == 1` | EXE |
| `corrections[e]` | `AttackDamageInfo::CalculateDamageCorrections` `0x140684d70 / 0x140685bc0` (section 3) | EXE |
| `unk1[e]` | `short AttackDamageInfo+0x14..0x1c * 0.01 * float +0x1e8.. * vcall(chr, +0xb4)`. `+0x1e8..+0x1f8` is 1.0 from `InitDamageStruct` `0x140528200` and only the bullet path lowers it: `CSBulletFlyState::OnUpdate` `0x1403939d0` -> `FUN_14039a810` decays it by `BulletParam.*DamageDamp * 0.01 * dt` once the bullet's flight time passes a threshold (1526 of 15475 bullets have one). A remote hit carries the product in Packet15's `*AttackRate` shorts (`FUN_140443c40`) | EXE (1.16.2); 1.0 on every melee hit; the vcall is not identified |
| `k` | flags `AttackDamageInfo+0x25b` / `+0x25c`: if either is set, `k = float +0x214` when that is >= 0, else 1.125 (flag 0x25b) or 0.8 (flag 0x25c); constants at `0x143b33d64/68` (1.16.2). The flags are the attacker's sweet spot / sour spot: TAE JumpTable 42 sets action flag 0x800 (most of the flail moveset, a few halberd and skill animations), 59 sets 0x1000; the hit record copies them in (`FUN_140521a80` -> `FUN_140521490`). A hit on a remote player goes as Packet15, which does not carry them, and the victim's `FUN_1404434f0` writes both to 0 and `+0x214 = 1.0` | EXE (1.16.2); k = 1.0 in PvP, x1.125 / x0.8 against NPCs. That the victim's own computation is the HP loss that counts is INFERRED |
| `mpCorrection` | `SpecialEffect::GetMPLevelCorrection` with the attacker weapon's `EquipParamWeapon.levelSyncCorrectId`; only returns != 1 while SpEffect 590 or 592 is on the defender | EXE |

After `CalculateDamageBasic`, `CalculateDamage` multiplies by the parts-damage rate
(`FUN_140605b50`, `field_0x244`) and `FUN_140447180`, then stores `(int)` into
`AttackDamageInfo.damage`. So the HP loss is truncated, and the per-element finals
(`fireFinalDamage` etc.) are truncated separately.

`FUN_140447180` is the flick (deflect) cut (EXE, 1.16.2). Defender flick power is the max of the
armor `defFlickPower` sum (weighted 1.1/1.5/1.2/1.2 x 0.25, less 5/10 per durability tier; vslot
0x380 `0x1406555d0`, or 0x378 `0x140655510` while blocking) and SpEffect `defFlickPower`; attacker
flick power is the max of `AttackDamageInfo+0x2c` and SpEffect `atkFlickPower`. When the attacker's
is higher the factor is 1.0. Otherwise `+0x264` is set and the factor is
`min(base, clamp(1 - max SpEffect flickDamageCutRate / 100, 0, 1))`, base being 1 - the weighted
armor `flickDamageCutRate` sum x 0.25/100 unblocked, or
`GameSystemCommonParam.flickDamageCutRateSuccessGurad` (0.5) blocked. Skipped when `+0x267 & 2`.
Every protector row has `defFlickPower` 0 and `flickDamageCutRate` 0, and the only SpEffect with a
`flickDamageCutRate` is 11550, so an unblocked player hit takes 1.0; a blocked hit takes x0.5 when
the guard's flick power is at least the attack's (blocking is not modelled).

**Motion value.** The damage code above never reads an attack rating and a motion value separately.
It receives `rawDamage` per element, already scaled. That the attacker side multiplies AR by the
AtkParam correction (motion value) before this point is INFERRED; the planner's
`finalDamageForAR` feeds bare AR into the same curve (SITE). `damage()` in the script applies
`AR * MV / 100` as the attack, which is the conventional reading, and says so in its docstring.

## 1. Flat defense (EXE + REGULATION)

`CalculateDefenseData` `0x140685650 / 0x1406864a0`:

```
base  = FUN_1406883e0(player)                          # stat part, below
armor = sum over the 4 protector slots of CalculateProtectorDefence(...)   # 0x140687a60 / 0x1406888b0
out[e] = (base[e] + armor[e]) * spRate[e] + spAdd[e]   # SpEffect::CalculateDefenceModifiers 0x1404f5b40 / 0x1404f6910
```

`FUN_1406883e0` `0x1406883e0 / 0x140689230` (1.17.1 bytes) calls `PerformCalcCorrection`
`0x140690f30 / 0x140691d80` (a standard CalcCorrectGraph evaluation) and truncates each result
(`cvttss2si` at `0x1406884f3..0x14068853a`):

| element | stat term | level term | EXE site |
|---|---|---|---|
| physical (all four sub-types) | CC(STR, graph **130**) | CC(L, graph **102**) | `mov edx,0x82` `0x140688419`, STR read `[rdi+0x298]` |
| magic | CC(INT, **132**) | CC(L, 102) | `0x140688442`, `[rdi+0x2a0]` |
| fire | CC(VIG, **133**) | CC(L, 102) | `0x140688472`, `[rdi+0x288]` |
| lightning | none | CC(L, 102) | `0x1406884a3` |
| holy | CC(ARC, **135**) | CC(L, 102) | `0x1406884bd`, `[rdi+0x2a8]` |

`defense[e] = trunc(CC(L,102) + CC(stat,g))`. `L` is `FUN_140687fb0 / 0x140688e00`
(1.17.1 bytes): a weighted sum of the nine effective-stat words at `PlayerGameData+0x288..0x2a8`,
where every weight is 1.0 (`0x143b33df8..` in 1.16.2, `0x143b37e08..` in 1.17.1). Graph 102 runs
to 792 = 8 x 99, so the ninth word (`+0x294`, the DS3 vitality slot) is 0 for players: L is the sum
of the eight real stats, RL + 79 (INFERRED from the graph range and the corpus). Which word is
which stat comes from the fromsoftware-rs `PlayerGameData` layout and the Ghidra type (INFERRED;
the corpus agrees). Effective stats include SpEffect `add*Status` from talismans, armor and the
great rune (`addLifeForceStatus` = VIG, `addWillpowerStatus` = MND, `addEndureStatus` = END,
`addMagicStatus` = INT, `addLuckStatus` = ARC; REGULATION, matched on Radagon's Soreseal).

The graphs (REGULATION, all exponents 1.0, so linear between stages):

| graph | stage x | value |
|---|---|---|
| 102 (level) | 1 / 150 / 170 / 240 / 792 | 40 / 100 / 120 / 135 / 155 |
| 130 (STR) | 0 / 30 / 40 / 60 / 99 | 0 / 10 / 15 / 30 / 40 |
| 132 (INT), 135 (ARC) | 0 / 20 / 35 / 60 / 99 | 0 / 40 / 50 / 60 / 70 |
| 133 (VIG) | 0 / 30 / 40 / 60 / 99 | 0 / 20 / 40 / 60 / 70 |

**Armor adds no flat defense.** `CalculateProtectorDefence` reads `EquipParamProtector.defense*`
times `ReinforceParamProtector.*DefRate` (and for physical `(defenseSlash|Blow|Thrust + 100) *
0.01` by attack type). All 838 protector rows have `defensePhysics/Magic/Fire/Thunder/Slash/Blow/
Thrust = 0`; `defenseDark = 100` only on 41 hair rows (`protectorCategory 4`); every
`reinforceTypeId` is 0 and ReinforceParamProtector row 0 is all 1.0 (REGULATION). So for any real
armor this term is 0, and the defense number depends on stats alone.

The planner (SITE) floors the two graph values separately: `floor(CC102) + floor(CC_stat)`. The game
truncates their sum. They differ by one whenever the fractional parts add past 1.

## 2. Absorption (EXE + REGULATION)

**Armor**, `CalculateAbsorptions` `0x140689c80 / 0x14068aad0`:

```
mult[e] = product over 4 slots of (1 - (1 - cutRate[e]) * durability[slot])
physical uses exactly one of slash/blow/thrust/neutral DamageCutRate, picked by the hit's
AttackDamageInfo.damageType (+0x25): 0 slash, 1 strike, 2 pierce, 3 standard (neutral)
magic/fire/lightning/holy use magic/fire/thunder/darkDamageCutRate
```

It is multiplicative, not additive. `durability` is the per-slot condition factor; ER armor has
`durability = 0` in every row and the factor was taken as 1 (INFERRED).

**SpEffects** (talismans, armor `residentSpEffectId`s, buffs, physick). Two independent paths,
both products over every active SpEffect entry:

1. `SpecialEffect::CalculateDefenseModifiers` `0x1404f53e0 / 0x1404f61b0`: the SpEffect
   `*DamageCutRate` columns (physical by sub-type via `GetPhysicalDamageCutRateByType`
   `0x140d4fe90`). Entries are bucketed by `stateInfo`: 110 and 434 go to their own products, 335
   is skipped here (Crucible Scale's `stateInfo 335`), 158/204 only count when the hit was guarded
   (`AttackDamageInfo+0x258`).
   All three products multiply the hit, so the bucketing does not change the result for a planner.
2. `CalculateDamageCorrections` (section 3): `defPlayerDmgCorrectRate_*` when the attacker is a
   player, `defEnemyDmgCorrectRate_*` otherwise (`CalculateDefPlayerDmgCorrectRates` `0x1404f5f10
   / 0x1404f6ce0`, `FUN_1404f5d80 / 0x1404f6b50`). Physical has one column for all sub-types.

This is where the PvP talisman values live (REGULATION):

| item | SpEffect | vs enemies (`defEnemy*`) | vs players (`defPlayer*`) |
|---|---|---|---|
| Dragoncrest Shield Talisman | 340000 | phys 0.90 | phys 0.98 |
| Dragoncrest Greatshield Talisman | 340030 | phys 0.80 | phys 0.95 |
| Pearldrake Talisman +3 | 20371300 | non-phys 0.89 | non-phys 0.95 |
| Ritual Shield Talisman | 340900 (`conditionHpRate 100`) | all 0.70 | all 0.70 |
| Fire Scorpion Charm | 320200 | phys 1.10 | phys 1.15 |
| Opaline Hardtear | 511011 (180 s) | all 0.85 | all 0.90 |
| Radagon's Soreseal | 310510 | all `*DamageCutRate` 1.15 (both) | same |
| Rakshasa set pieces | 6516000 on each piece | all `*DamageCutRate` 1.02 (both) | same |

So absorption shown as a percent is `100 * (1 - armorMult * spMult)` for the relevant column set.

**Menu.** `FUN_1407cb680 / 0x1407cc500` (status screen) multiplies the armor product by
`SpecialEffect::CalculatePlayerStatusDefence` `0x1404f5800`, which uses `*DamageCutRate *
defEnemyDmgCorrectRate_*` and skips `stateInfo 335`. The menu therefore always shows the PvE
number (EXE). Its `physical` row is sub-type 3 (neutral).

### 2a. Which physical type a hit carries (EXE + REGULATION)

`FUN_140685a90 / 0x1406868e0` (1.17.1 bytes) resolves an attack's types from its
`AtkParam.atkAttribute` / `spAttribute` and the attacking weapon's `EquipParamWeapon` row:

```
atk = AtkParam.atkAttribute
if atk == 253 (cmp cl,0xfd): atk = EquipParamWeapon.atkAttribute    (movzx ecx, byte [rax+0x104])
if atk == 252 (cmp cl,0xfc): atk = EquipParamWeapon.atkAttribute2   (movzx ecx, byte [rax+0x191])
sp  = AtkParam.spAttribute;  if sp == 255: sp = EquipParamWeapon.spAttribute (+0xed)
if the attacker is a chr: FUN_1404f42e0 over its active SpEffects:
    SpEffect.atkAttribute (+0x154) == 255 -> atk = 255
    SpEffect.spAttribute  (+0x155) != 254 -> sp  = that value
bullets: BulletParam atkAttribute / spAttribute replace them unless 254
```

The caller `FUN_140d24b10 / 0x140d26220` stores `atk` at `AttackDamageInfo+0x25` (`damageType`)
and `sp` at `+0x26`. `CalculateDamage` passes `+0x25` to `CalculateAbsorptions` and to
`CalculateDefenseModifiers`, which pick slash/blow/thrust/neutral by 0/1/2/3; any other value
(254, "None") meets no physical cut rate at all (both functions fall through to 1.0). The field
offsets +0x104 and +0x191 are `atkAttribute` and `atkAttribute2` in the Smithbox paramdef layout
(row size 664 matches the param stride), and Smithbox's enum names 252 "atkAttribute2 reference"
and 253 "atkAttribute reference" (COMMUNITY), which agrees.

So, on the data (REGULATION, 1.17.1):

- AtkParam_Pc rows: 253 on 3987, 3 (standard) on 2623, 2 (pierce) on 2142, 1 on 965, 252 on 841,
  0 on 457, 254 on 2. Most weapon attacks take the weapon's first attribute; 252 marks the
  moveset's thrusts. Greatsword (`atkAttribute` 3, `atkAttribute2` 2): its R2s are 253, standard;
  its rolling and crouch R1 rows are a literal 2, pierce. Uchigatana (0, 2): everything is 253
  (slash) except the 2H running R2, 252 (pierce). Giant-Crusher (1, 1) is strike throughout.
- Affinity does not change it: of 2920 affinity rows only two ammunition rows (`wepType 92`) carry
  a different pair from their base weapon. Greases and weapon buffs do not change it: no SpEffect
  row has `atkAttribute` 255 (19 have 3, none 255), so only the special attribute (`spAttribute`,
  the status/element tag used for effects and SFX) changes under a buff. That tag is not a damage
  multiplier anywhere in `CalculateDamage`.
- "Standard" is a physical type of its own (neutral cut rate), not a mix: a hit carries exactly one
  physical type, and a weapon listed as "Standard/Pierce" has attacks of each.
- No player SpEffect scales slash/strike/pierce differently on the attacker side: 5 SpEffect rows
  have unequal `slash/blow/thrust/neutralAttackPowerRate`, all NPC or unnamed, and they differ only
  in the neutral column.

`scripts/er-builds-pvp.py` had 252 and 253 swapped until 2026-09-29, which scored every
253 attack on a Standard/Pierce weapon as pierce (Greatsword R2) and every 252 thrust as the first
type.

### 2b. Counter hits (EXE + REGULATION + TAE)

The counter bonus is a defender SpEffect, not a flag on the attack.
`CalculateDefenseModifiers` `0x1404f53e0 / 0x1404f61b0` puts every active SpEffect whose
`stateInfo` is 110 (`cmp ax, 0x6e`, 1.17.1 `0x1404f631e`) into its own product: the physical
column is `GetPhysicalDamageCutRateByType` of the hit's `damageType` (section 2a), the others
`magic/fire/thunder/darkDamageCutRate`. `CalculateDamage` then, when any component of that product
is above 1, multiplies it by `FUN_1404f5310 / 0x1404f60e0` over the attacker's SpEffects with
`stateInfo` 197 (`cmp word [rcx+0x156], 0xc5`): `physicsAttackRate` (+0x38) on the physical
component, `magic/fire/thunderAttackRate` (+0x3c/+0x40/+0x44) and `darkAttackRate` (+0x1dc) on the
rest, each only where the defender factor exceeds 1. The result is `counterAttackDefRate` in
`CalculateDamageBasic`, a straight multiplier on each element's damage after the defense curve.

The stateInfo-110 rows (REGULATION):

| SpEffect | Smithbox name | cut rates | effectEndurance |
|---|---|---|---|
| 31 | Behavior - Counter Frames | thrust 1.3, all else 1.0 | 0.1 s |
| 45 | [HKS] Counter Frames | thrust 1.15, all else 1.0 | 0.1 s |
| 99008 | (none) | every type 1.4 | 0.1 s |

The one stateInfo-197 row is SpEffect 320600, Spear Talisman: `physicsAttackRate` 1.15, the
rest 1.0.

Who applies them (TAE): TAE event 66 is `CSChrTaeAnimEvent::AddSpEffect` `0x14042bfd0` (the
dispatch table `scripts/er-tae-dispatch-decode.py` reads; the handler applies SpEffect `Args[0]`
to the animating character, or refreshes it). Across the player TAEs 5940 event-66 entries apply
SpEffect 45 and 30 apply SpEffect 31, all in `a938`; 99008 is never applied by a player TAE.
Greatsword R1 (`a023_030000`) applies 45 from frame 14 to 29, around its frame 13-16 hitbox.

So in PvP: a hit that lands while the defender is inside one of their own attacks' event-66 window
(plus the 0.1 s endurance) does 1.15x on its physical damage if it is pierce, and nothing extra
otherwise. Spear Talisman raises that to 1.15 x 1.15 = 1.3225. Slash, strike and standard hits get
no counter bonus from this path, and the elemental part of a pierce hit gets none either. Poise
damage is not in this path; whether counters change poise damage was not traced.

`AttackDamageInfo+0x25b/+0x25c` (the `k` factor 1.125 / 0.8 in section 0) are not the counter.
They are the attacker's sweet spot / sour spot (EXE, 1.16.2): TAE JumpTable 42 ORs `0x800` and 59
ORs `0x1000` into the attacker's `CSChrActionFlagModule` (`0x140428130` / `0x1404281e4`);
`FUN_140521a80` writes them to the hit record (`+0x250` / `+0x251`, with the SpEffect
`vitalSpotChangeRate` / `normalSpotChangeRate` product at `+0x254`, -1 on every row today), and
`FUN_140521490` copies the record into `+0x25b/+0x25c/+0x214`. A direct-store search could not find
the setter because it is that struct copy. The network path `FUN_1404434f0`, which applies a
remote player's Packet15 on the victim's machine, clears both and sets `+0x214` to 1.0, and the
serializer `FUN_140443c40` does not carry them, so a PvP hit takes k = 1.0. JumpTable 42 is in the
flail TAE a34 (71 animations), a38 / a938 halberd skill animations, a807 and a835; 59 only in a938
30605.

## 3. PvP-only factors on the defender (EXE + REGULATION)

`CalculateDamageCorrections` `0x140684d70 / 0x140685bc0`. `ShouldUsePvPDamage` `0x140682d50` is
`IsPlayerIns()`, overridden by `NpcParam.isCalculatePvPDamage` when the chr has an NpcParam.

```
(skipped for object attackers: then everything is SpEffect defObjDmgCorrectRate)
if the defender is a PvP chr:
    if the attacker is too: x *= attacker's EquipParamWeapon.vsPlayerDmgCorrectRate_{Physics,Magic,Fire,Thunder,Dark}
    x *= attacker SpEffect atkPlayerDmgCorrectRate (CalculateAtkPlayerDmgCorrectRates)
else:
    x *= attacker SpEffect atkEnemyDmgCorrectRate  (CalculateAtkEnemyDmgCorrectRates)
x *= defender SpEffect defPlayerDmgCorrectRate_* if the attacker is a PvP chr, else defEnemyDmgCorrectRate_*
if both are the main player or chrs with event id < 9998 (players):
    x *= FinalDamageRateParam[AtkParam.finalDamageRateId].{phys,mag,fire,thun,dark}Rate
    x *= FUN_140486b10(defender toughness module, finalDamageRateId)   # ToughnessParam.unk2 in a hyperarmor window, below
```

There is **no global PvP damage reduction**. The PvP scaling is per attack: `AtkParam_Pc.
finalDamageRateId` picks a `FinalDamageRateParam` row (REGULATION: 377 rows; row 0 is 1.0, rows
`x1` are 0.9, `10000` 0.8 ... `10006` 0.5; 1579 player attacks use 10000, 1685 use 0, 1852 have -1
which skips the lookup). The same row's `staminaRate` (+0x14) scales guard stamina damage in
`CalculateDamage` (EXE, the `*(row+0x14)` read), and `saRate` (+0x18) is the likely poise-damage
scale (INFERRED from the name). This is attacker data applied on the defender's side of the hit.

`FUN_140486b10` (EXE, 1.16.2) returns `ToughnessParam[toughness+0x1c].unk2` (+0x14) while the
defender's toughness `+0x28` (the TAE 795 hyperarmor window) is set and toughness vslot 6
`0x140486ba0` holds: max toughness above 0, current toughness above 0, owner alive. Otherwise 1.0.
The `finalDamageRateId` argument is overwritten before use. Values: 0.925 (rows x0), 0.825 (rows
x1); attacks.md section 2 models it. The cut ends once the defender's poise breaks.

## 4. The defense curve (EXE)

`CalculateDefense(attack, defense)` `0x140690cf0 / 0x140691b40` (1.17.1 bytes; constants at
`0x143b37da8..0x143b37dc4`, `100.0` at `0x1432a1998`):

```
if |attack| <= FLT_EPSILON: return 0
r = attack / defense            (r = 8 when defense <= 0)
pct = 90                                   r <= 0.12
    = 90 - 30 * ((r - 0.12) / 0.88)^2      0.12 < r < 1
    = 30 + 30 * ((2.5 - r) / 1.5)^2        1 <= r <= 2.5
    = 10 + 20 * ((8 - r) / 5.5)^2          2.5 < r < 8
    = 10                                   r >= 8
damage = attack * (1 - pct / 100)
```

So an attack equal to defense keeps 40%, 2.5x keeps 70%, 8x or more keeps 90%, and attacks under
0.12x defense keep a floor of 10%. The four pieces are quadratics that meet at 0.12/1/2.5/8; the
kept fraction never decreases as the ratio rises (selftest). The planner's `adjustForDefense`
uses the same numbers (SITE), and applies it per element to AR.

Physical defense is one number for all four sub-types (section 1); the sub-type only changes which
armor absorption column applies.

## 5. Resistances (EXE + REGULATION)

`GetResistanceData` `0x140688b40 / 0x140689990` (1.17.1 bytes) and `CalcTotalResistance`
`0x14068a570 / 0x14068b3c0`:

```
base[t]  = trunc(CC(L, lvlGraph[t]) + CC(stat[t], statGraph[t]))     # L from ResistanceData::Default, weights all 1.0
armor[t] = sum over 4 slots of EquipParamProtector.resist*[t] * ReinforceParamProtector rate * durability
            (CalculateProtectorResistance 0x14068a1e0 -> per-piece 0x140682cb0 -> EquipParamProtector::GetResistance 0x140d47180)
total[t] = max(1, spRate[t] * base[t] + armor[t] + spAdd[t])
            spRate = product of SpEffect regist*ChangeRate, spAdd = sum of change*ResistPoint
```

| type (menu group) | stat | graphs (level, stat) |
|---|---|---|
| poison, rot (immunity) | VIG | 110 / 120, 111 / 121 |
| bleed, frost (robustness) | END | 112 / 122, 113 / 123 |
| sleep, madness (focus) | MND | 114 / 124, 115 / 125 |
| death (vitality) | ARC | 116 / 126 |

Level graphs 110..116: 1/150/190/240/792 -> 75/105/145/160/180. Stat graphs 120..125: 0/30/40/60/99
-> 0/0/30/40/50; 126: 0/15/40/60/99 -> 0/15/30/40/50 (REGULATION). Each armor piece has equal
poison/rot, bleed/frost and sleep/madness values in the regulation, which is why the menu can show
four numbers.

## 6. Poise (EXE + REGULATION, combat link INFERRED)

`CalculateToughnessDamageCutRate` `0x140688d60 / 0x140689bb0` (the status screen calls it at
`0x1407cb7f9` and stores the float at `+0x30`):

```
poise = (sum over protector slots 0..3 of EquipParamProtector.toughnessCorrectRate) / product(SpEffect.toughnessDamageCutRate)
```

`toughnessCorrectRate` is the float at row `+0x14` (`movss xmm0,[rax+0x14]` at `0x140688de3`,
and the paramdef puts it there). The displayed poise is that sum times 1000 (Greatjar 0.014 +
Fire Prelate set = 96 in the planner; the x1000 is the menu's, INFERRED). Bull-Goat's Talisman is
SpEffect 312100 with `toughnessDamageCutRate 0.75`, so it divides by 0.75: x1.3333 (REGULATION).
Only Bull-Goat has a non-1 value among talismans (REGULATION). `SpecialEffect::
CalculateToughnessDamageCutRate` `0x1404f3e20` is a plain product over active entries.

How incoming poise damage is taken: `CSChrSuperArmorModule::ApplySuperArmorDamage` `0x14047dea0`
reduces the pool by `partsRate(+0x244) * baseSA(+0x100) * rideRate * stealthRate * (1 - vcall4)
* product(SpEffect SaReceiveDamageRate)` against a max of `vcall2() + field 0x18`, capped at
`saDurability` (EXE shape). That the player's pool max is the section-6 poise number is INFERRED;
the link between the toughness module and the super-armor module was not traced.

## 7. Corpus agreement

`python3 scripts/er-mechanics-defense.py --corpus` against the planner's own `computed` block for
5699 scraped builds. 5044 compared; skipped: 474 without a computed block, 55 without stats, 126
with a name the regulation's row names do not have (`Steel Gauntlets`, `Steel Armor`,
`Steel Greaves`, `Broken Gold Mask`, which exist on the site and not in the Smithbox names).
Inputs: first equipped piece per armor category (the planner's rule), equipped talismans,
`greatRune` (SpEffect 600 + 10k, "Effect 0"), and crystal tears when `conditions.crystalTears`.

| metric | agreement | notes |
|---|---|---|
| defense, game rounding `trunc(a+b)` | 33224 / 40352 = 82.34% | of 7128 misses, 6727 are the game value one above the planner's where planner rounding matches; the other 401 are the row below |
| defense, planner rounding `floor(a)+floor(b)` | 39951 / 40352 = **99.01%** | |
| absorption (8 types, 0.01 tolerance) | 36941 / 40352 = **91.55%** (4580 / 5044 builds) | |
| resistances, game rounding | 17871 / 20152 = 88.68% | |
| resistances, planner rounding | 18669 / 20152 = **92.64%** | |
| poise (armor sum x 1000) | 4828 / 4829 = **99.98%** | |
| poise with talismans | 880 / 978 = 89.98% | |

The game-rounding rows are the executable's arithmetic; the planner rows show the regulation data
and structure agree, and the planner rounds differently.

Disagreements, explained:

- **Defense (401 values, planner rounding).** All come from planner stat bonuses that the scraped
  build does not carry: the planner's final stats differ from ours in those builds (for example
  ARC +10 on 21 builds, +6 MND/INT/FTH/ARC on 9, +5 all on 6, STR +5 on 2). The planner applies
  manually toggled effects (`character.activeEffects`, tools and spells); no scraped build has that
  key, so these cannot be reproduced from the corpus.
- **Absorption (464 builds).** 170 have physick active. In the largest such groups the planner's
  implied Opaline Hardtear factor is 0.86 (PvE) and 0.99 (PvP) where the regulation has 0.85 and
  0.90; tears whose effect is conditional or timed in other ways were not modelled. 116 wear Rakshasa
  pieces: each piece carries SpEffect 6516000 (1.02 on every cut rate); our model multiplies once
  per piece, the planner does something else (whether the game stacks the same SpEffect id from
  several pieces is not established). 30 have no armor in the scrape but a computed absorption. 148
  more show a planner-only factor (0.4878, 1.3, 0.9, 0.1, 1.25 ...) from manual effects.
- **Resistances (1483 values, planner rounding).** Armor pieces whose resident SpEffect changes a
  resistance (`changeSleepResistPoint`/`changeMadnessResistPoint`, applied once here): the planner
  is lower by exactly that amount again, which fits applying it twice (INFERRED). Divine Bird Helm
  -45 focus: 142 builds; Divine Beast Helm -45: 98; Divine Beast Head -60: 51; Horned Warrior Helm
  -30: 45; Curseblade Mask -30: 43. The Solitude pieces are +50 immunity/robustness/focus higher on the planner than in the
  regulation (no resident SpEffect on them). 23 values are off by about -783, a planner bug.
- **Poise with talismans (98).** 92 are the planner rounding Bull-Goat to x1.33 where the
  regulation is 1/0.75; 6 carry a planner multiplier with no Bull-Goat (manual effects).
- **Poise (1).** A build with no armor in the scrape and a computed poise of 71.

## 8. What the script does

`scripts/er-mechanics-defense.py`:

- `Tables()` reads CalcCorrectGraph, EquipParamProtector, EquipParamAccessory, EquipParamGoods,
  SpEffectParam and FinalDamageRateParam once, with Smithbox row names for lookup by item name.
- `defender(t, stats, armor, talismans, great_rune=None, pvp=False, extra_speffects=())` returns
  `defense` (game rounding), `defense_site`, `absorption`, `armor_mult`, `effect_mult`, `resist`
  (seven types), `resist_groups`, `resist_site`, `poise`, `poise_effective`. SpEffects count only
  when unconditional (`stateInfo 0`, no HP condition); pass conditional ones through
  `extra_speffects`.
- `defense_curve(attack, defense)`, `calc_correct(row, x)`.
- `damage(ar_by_type, motion_value, defender, phys_type='physical', final_rate=None)`: per element
  `defense_curve(AR * MV / 100, defense) * armor_mult * effect_mult * finalRate`, summed, sub-1
  totals raised to 1. `final_rate` is a FinalDamageRateParam row for player-vs-player hits.
  `phys_type='none'` skips the physical cut rate (section 2a).

## 9. What a PvP hit meets: the corpus distribution (MEASURED)

`scripts/er-builds-pvp.py` scores each attack against every PvP build of the RL window instead of
one median build. PvP here means `isPvE` false, or `isPvE` missing with a PvP tag (Invasions,
Duels, Co-op/Gank, 2v2, Ladder, Fishing); `isPvE` true builds are left out because the planner
computes their absorption with the `defEnemy*` rates (section 2). The numbers are the planner's
`computed.defenses` and `computed.absorption` (section 7 for how well they match the game).

RL 140-160, 1074 builds (`python3 scripts/er-builds-pvp.py --rl 150 --summary`):

| | mean | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|---|
| slash abs% | 29.3 | 13.4 | 24.5 | 32.2 | 35.4 | 37.0 |
| strike abs% | 26.7 | 12.3 | 22.1 | 29.1 | 32.1 | 33.6 |
| pierce abs% | 28.1 | 12.1 | 23.6 | 31.1 | 34.1 | 36.3 |
| standard abs% | 29.2 | 13.6 | 24.3 | 32.3 | 35.2 | 36.8 |
| magic abs% | 24.3 | 18.7 | 22.4 | 25.0 | 26.3 | 26.9 |
| fire abs% | 25.6 | 19.5 | 23.4 | 26.2 | 27.6 | 30.8 |
| lightning abs% | 23.6 | 16.6 | 20.8 | 24.6 | 26.1 | 27.2 |
| holy abs% | 24.4 | 18.0 | 22.3 | 25.4 | 26.3 | 27.2 |
| physical defense | 146.7 | 137 | 138 | 141 | 157 | 163 |

What this says about damage type: strike is the weakest physical absorption in the window (median
29.1% against 31.1-32.3% for the other three, mean 26.7% against 28.1-29.3%), so a strike hit
keeps about 3% more of its physical damage than a slash or standard hit of the same attack power,
and pierce sits between. The spread between builds is far larger than the spread between types:
the bottom decile (p10 12-14%) is builds wearing little or no armor, which is also why the mean
sits 2-3 points under the median and why a hit's mean damage over the corpus runs about 3-4%
above its damage on the median defender.

## Not established

- What `AttackDamageInfo.rawDamage` holds: that it is AR x motion value (AtkParam correction) is
  INFERRED; the attacker-side writer was not traced.
- The `vcall(chr, +0xb4)` term of `unk1` (its `+0x1e8..` part is the bullet decay, section 0).
  What sets the bullet's decay start field (`CSBulletIns+0xa7c`), and whether `dt` is seconds.
- That the victim's own computation is the PvP HP loss (owner-authoritative HP), which is what
  makes k = 1.0 in PvP (section 2b). The separate damage-result broadcast
  `P2PBroadcast(0xf, 0x140 bytes)` (`FUN_140c9f370`) and its receiver were not followed.
- Whether a counter hit changes poise damage; section 2b covers HP damage only.
- The exact counter window: the event-66 span plus SpEffect 45's 0.1 s endurance is the model;
  whether the refresh on the last event frame starts the 0.1 s then was not traced.
- Who applies SpEffect 11550, the only row with a `flickDamageCutRate` (section 0).
- Whether the same SpEffect id from several armor pieces (Rakshasa, 6516000) stacks.
- The durability factor for armor, taken as 1 because ER armor has no durability.
- That the player's in-combat poise pool equals the menu poise; the super-armor module's max was
  not tied to `toughnessCorrectRate`. The x1000 display scale is the menu's.
- That `+0x294` (the ninth effective-stat word) is always 0 for players.
- SpEffect `CalculateDefenceRate` / flat defense adds (`CalculateDefenceModifiers`
  `0x1404f5b40`): the structure is read, the per-field mapping is not, and no talisman uses them.
- Which SpEffects with conditions (HP rate, `stateInfo`) are active at a given moment. The script
  counts only unconditional ones.
- `GetMPLevelCorrection` (row 303 on defense rates, weapon `levelSyncCorrectId` on damage): only
  active with SpEffect 590/592; its graph values were not read.
- Guard absorption (`CalculateGuardDamage`) and the stamina side of blocking.
