# Weapon buffs leak onto hits from other weapons

A grease, Seppuku or blade incantation is meant to empower one weapon. The game stores it as a
character-wide SpEffect tagged with a hand, and at each hit it asks only "does this hit's context
byte admit that hand?". It never asks which weapon made the hit. So any hit whose context byte
names the buffed hand carries the buff, even when the weapon behind the hit cannot hold a buff at
all. This page is the evidence, the game's own model of buff ownership, and the fix.

Labels: `VERIFIED` = read from the 1.16.2 executable (Ghidra :8765, shift 0), the 1.17.0 dump
(:8767) or the 1.17.1 regulation; `INFERRED` = follows from verified pieces, not traced end to
end; `USER` = in-game measurement by the user; `LIVE` = Frida trace on the running game.
Addresses are 1.17.1 (the installed build), with the 1.16.2 address in brackets. 1.17.1 equals
1.17.0 below rva `0xafefe9` and is `+0x70` at or above it. Background: `rain-of-arrows-seppuku.md`,
`interaction-candidates.md`. Regulation numbers come from
`python3 scripts/interactions/leak_report.py all` (about a minute; run it in the background).

## Measured cases

| case | launcher | buff | status reached enemies | evidence |
|---|---|---|---|---|
| Piquebone lure smoke 20003309 after locked-on Rain of Arrows | bow in the left hand (`isEnhance` 0) | right-hand grease, applied after the arrows landed | yes | `USER`; `LIVE`: 115 smoke hits over 3.96 s, 109 returned 3313 Drawstring Rot Grease - Right (On Attack) and applied it; the 4 falling-arrow hits before the grease returned nothing |
| Familial Rancor spirits 2020-2025 | Family Heads 13020000 (`isEnhance` 0, `gemMountType` 0) | weapon buff on the other weapon | yes | `USER` |

The smoke is the proven carrier. The falling arrows 20003354 are not: they hit before the grease
existed. Lock-on matters only because it puts the smoke where it covers every enemy.

## 1. What a hit knows about its source

`VERIFIED`. A bullet's attack info is filled at launch by `0x14038e390` [`FUN_14038e380`]. The
TimeAct launch `CSChrTaeAnimEvent::BulletBehavior` `0x1404273b0` [`0x140426e60`] passes two
equipment indices, which reach the spawn data at +0x28 and +0x2c (`0x1403c07c0` [`FUN_1403c07b0`],
`0x1403c10c0` [`FUN_1403c10b0`]):

- the hand: `GetAttackReferenceHandSlot`, or the TimeAct argument; `-2` left weapon, `-1` right
  weapon;
- the equipment slot: the hand, except a bow or crossbow becomes its ammo slot
  (`0x140655d50` [`FUN_140654f00`]: `weaponCategory` 10 -> `-6` left arrow / `-5` right arrow, 11 -> `-4` / `-3`
  bolts).

| AttackInfo offset | written by `0x14038e390` [`FUN_14038e380`] | holds |
|---|---|---|
| +0x10 | spawn +0x8 | hit context byte (`BehaviorParam.category`, goods or magic `spEffectCategory`) |
| +0xe4 | spawn +0x28 | equipment slot (ammo slot for bows) |
| +0xe8 `weaponParamId2` | `GetEquipmentEntry(+0xe4)` | item in that slot (the arrow) |
| +0xec | spawn +0x2c | hand slot (`-2` / `-1`) |
| +0xf0 `weaponParamId1` | `GetEquipmentEntry(+0xec)`, or goods `refVirtualWepId` | weapon in that hand (the bow) |
| +0xf5 `isTwoHanding` | `ChrIns::IsTwoHanding` | arm style at launch |

`0x140d26290` [`FUN_140d24b10`] copies it into `AttackDamageInfo`:

| ADI offset | source | meaning |
|---|---|---|
| +0xda | AttackInfo +0x10 | context byte, the only source field the buff reader sees |
| +0x48 | AttackInfo +0xe4 | equipment slot index of the launch |
| +0x144 | AttackInfo +0xf0 if it is >= 0, else +0xe8 | `EquipParamWeapon` id of the weapon in the launching hand (full id with upgrade and affinity) |
| +0x115 bit 5 | AttackInfo +0xf4 | `attackNotFromWeapon` |
| +0x10c | bullet id | |

The hand slot (+0xec) itself is not copied, but +0x48 encodes the side: `0x1403be3d0` [`FUN_1403be3c0`]
returns 0 for `-6 -4 -2 0 2 4` (left) and 1 otherwise. The blood-loss burst launches with slot
`0xc`, so its ADI+0x144 is -1 (`PlayerIns::GetEquipmentEntryParamId` `0x1406577b0`
[`0x140656960`] refuses indices above 11).

Is it available at the reader? `VERIFIED`: at the call in `CalculateDamage2` `0x140449335`
[`0x140448dd5`] R14 is the ADI and RSI the attacker. But the reader `0x1404f7fb0`
[`FUN_1404f71e0`] receives only `SpEffectSubCategoryMaskData`, which `0x1404ffc30`
[`FUN_1404fee60`] builds from
ADI+0xda (context), ADI+0xdc..0xfb (sub-category mask) and ADI+0xd9 == 2 (throw). The weapon id
and slot are dropped one call before they are needed.

Melee: `0x140690df0` [`FUN_14068ffa0`] fills the same AttackInfo +0xe4 / +0xe8 for weapon swings, so ADI+0x144 is
the swinging weapon there too (`INFERRED`: the function is called from the hit code beside
`CalculateDamage2`, but its callers were not traced).

Timing asymmetry, `VERIFIED`: a player bullet snapshots the buffs' attack-power additions at
launch (`0x1404f52f0` [`FUN_1404f4520`] from `0x14038e390` [`FUN_14038e380`]), while the on-hit
status row is looked up live at every hit (`0x1404f7fb0` [`FUN_1404f71e0`]). That is why a grease applied while the smoke lingers rides it.

## 2. How the game takes a buff away

`VERIFIED`. Two removers, both keyed on the hand tag only:

| remover | removes | called from |
|---|---|---|
| `0x1404f79d0` [`FUN_1404f6c00`] via `PlayerIns::RemoveWepParam1And5SpEffects` `0x140655b40` [`0x140654cf0`] | live entries with `wepParamChange` 1 or 5 (right hand) | `SetChrAsmEquipmentState` `0x140426a70` [`0x140426520`] when the new arm style is left two-handed; weapon-switch event `0x14042d4d0` [`FUN_14042cf80`] when the right slot changes |
| `SpecialEffect::RemoveWepParam2And6SpEffects` `0x1404f78d0` [`0x1404f6b00`] | `wepParamChange` 2 or 6 (left hand) | `SetChrAsmEquipmentState` when right two-handed; `0x14042d4d0` [`FUN_14042cf80`] when the left slot changes |

An earlier version gave `0x1404f79d0` as the 1.17.1 `PlayerIns` method; it is the
`SpecialEffect` helper the method calls. This matches the user's measurement that two-handing a
left bow removes a right-hand grease.

So the game's model is: a hand-tagged buff belongs to whatever weapon is currently gripped in that
hand, and it dies when that hand's weapon changes or the other hand takes over. No weapon id is
stored or compared anywhere. The leak is that the hit side never applies the same model: the
context byte comes from data (`BehaviorParam.category`), not from the hand that launched the hit.
All 1,247 arrow rows are category 1, so a left-hand bow's arrows and their children present as
right-hand hits.

## 3. The fix

### Code

Replace the call at `CalculateDamage2` `0x140449335` [`0x140448dd5`] with a reader that also
takes the ADI (R14 at that point in both builds) and adds one test per entry:

```c
// same walk as 0x1404f7fb0 [FUN_1404f71e0], plus:
if (stateInfo is 152 or 153 && owner is a PlayerIns) {
    int hand = row->wepParamChange;
    if (hand == 1 || hand == 5 || hand == 2 || hand == 6) {
        int slot = (hand == 1 || hand == 5) ? -1 : -2;             // right / left weapon
        int held = owner->GetEquipmentEntry(slot);                 // vtable +0x228, 0x1406577b0 [0x140656960]
        if (adi->weaponId_0x144 != held) continue;                 // hit was not made by the buffed weapon
    }
}
```

That single comparison:

- refuses the left bow's arrows and smoke (ADI+0x144 is the bow, not the greased right weapon);
- refuses Family Heads' spirits (ADI+0x144 is Family Heads);
- refuses a bullet launched by the previous weapon after a swap, which the hand test alone would
  not catch;
- keeps every hit that should carry the buff: the buffed weapon's swings and its own skill
  projectiles (Firebreather, Vacuum Slice, Lightning Slash);
- leaves hand-neutral buffs (`wepParamChange` 0) and NPC attackers unchanged.

5 and 6 are grouped with 1 and 2 because the removers group them that way. An optional second test, `EquipParamWeapon(ADI+0x144)
.isEnhance != 0`, is redundant once the id matches, because a grease cannot be applied to a weapon
with `isEnhance` 0 and a swap removes it (`INFERRED` for the use refusal; the removal is
`VERIFIED`).

The cheaper alternative, patching `IsApplicableForCategory` `0x140501700` [`0x140500930`], does
not work alone: it is also used at launch by `0x1404f52f0` [`FUN_1404f4520`] and it never
receives the ADI. Extending `SpEffectSubCategoryMaskData` (`0x1404ffc30` [`FUN_1404fee60`]) with
ADI+0x144 would work but changes a struct three callers share.

Address carrying: `scripts/map-rvas-1162-to-1170.py` gave unique matches for the reader,
`CalculateDamage2` `0x140448910` [`0x1404483b0`], `IsApplicableForCategory`, the mask builder
and both removers' callers; the ADI builder came from the nearest-anchor delta and was confirmed by
its writes to +0xda, +0x48, +0x144 and +0x13c in the 1.17.0 dump. All are below rva `0xafefe9`
and unchanged from 1.17.0, except the ADI builder (1.17.0 `0x140d26220`, 1.17.1 `0x140d26290`).
Each 1.17.1 address on this page was read in `eldenring-deobf-1.17.1.bin`.

### Data only, and why it misses cases

A regulation patch can only zero one of the two gates per row:

- set `BehaviorParam_PC.category` to 0 on every ammo row (context 0 admits only hand-neutral
  buffs), or
- set `statusAilmentAtkPowerCorrectRate` or `_byPoint` to 0 on each carrier's AtkParam row.

Both fail as a general fix:

- The hand that launches a hit is not in the data. The same BehaviorParam row fires from either
  hand, so no category value is right for both. Ammo could take category 0 safely, but a skill
  row shared by buffable and unbuffable weapons (a generic ash) needs category 1 or 12 for the
  buffable weapon's own buff and so stays open for the unbuffable one.
- Zeroing AtkParam rates also kills the scaling of the weapon's own on-hit status, and
  AtkParam_Pc row 0 (the smoke's) is shared by many zero-damage bullets.
- The class is thousands of rows (below), each needs a separate decision, and every regulation
  update or mod adds more. A missed row is a live leak with no symptom in data review.

## 4. Familial Rancor: design or oversight

Status buildup scale over AtkParam_Pc rows a skill reaches (`leak_report.py rates`, 1.17.1):

| hits | rows | 0/0 | 100/100 | other |
|---|---|---|---|---|
| skill bullets, all skills | 378 (140 skills) | 216 (86 skills) | 109 (64 skills) | 53 |
| skill bullets, skills whose every weapon has `isEnhance` 0 | | 129 | 120 (53 skills) | 70/70: 13, 0/100: 9, 50/50: 5, ... |
| skill swings (melee events) | 1,258 (191 skills) | 77 | 743 (169 skills) | 438 |

100/100 is the default for a weapon swing, and skill projectiles on buffable weapons that plainly
should carry the buff use it too: Firebreather, Flaming Strike, Lightning Slash, Vacuum Slice,
Phantom Slash, Swift Slash, Prelate's Charge. All six Familial Rancor bullets (AtkParam_Pc
301302900-301302905) are 100/100, like 52 other skills on unbuffable weapons. So the rate is not an
outlier and not a decision to carry buffs; it is the swing default.

Verdict: oversight (`INFERRED`). Family Heads cannot hold a grease (`isEnhance` 0, `gemMountType`
0), and a hand's buff is removed when that hand's weapon changes, so a buff that reached enemies
through Rancor was living on the other weapon. The spirits' context is 12, which admits
right-hand buffs unconditionally. No design intends one weapon's grease to ride another weapon's
summon.

## 5. The cross-weapon leak class

Every (bullet, context) that passes both gates for some player-reachable hand-tagged buff
(`wepParamChange` 1 or 2) while the launching weapon cannot hold a buff
(`leak_report.py leaks`). Counts include NPC-only weapon rows and bullets that also appear in
other groups, so they bound the class from above.

| launcher | context | buff hand admitted | bullets | at full scale | reachable when | status |
|---|---|---|---|---|---|---|
| arrows and greatarrows (bows `isEnhance` 0) | 1 | right | 1,118 | 664 | bow in the left hand, buff on the right weapon | smoke 20003309 `LIVE` + `USER`; rest `INFERRED` |
| bolts and greatbolts (crossbows `isEnhance` 0) | 2 | left | 130 | 64 | crossbow in the right hand, buff on the left weapon | `INFERRED`, untested; includes the Piquebone Bolt smoke 20003309 at context 2 |
| weapons with `isEnhance` 0, movesets and skills | 12 | right; left only while left two-handed | 1,768 | 523 | weapon in the left hand | Familial Rancor 2020-2025 `USER`; rest `INFERRED` |
| same | 1 | right | 1,408 | 594 | weapon in the left hand | `INFERRED` |
| same | 2 | left | 419 | 162 | weapon in the right hand | `INFERRED` |

Largest owners in the last three groups: the five perfume bottles (about 240 to 260 bullets each
per context), Smithscript Dagger, Ghostflame Ignition, Death's Poker, Ruins Greatsword, Claws of
Night, Sacred Relic Sword and Wave of Gold, Smithscript Cirque, Bear Witness!, Discus Hurl.
Lingering ammo children worth testing first: the Piquebone smoke at context 2 (left grease,
right crossbow) and any 1 s+ child of an arrow at scale 1.0.

The fix in section 3 closes every row of this table without naming any of them, because each
hit's ADI+0x144 is the bow, crossbow or unbuffable weapon, never the buffed one.

## Not proven

- Melee hits fill ADI+0x144 the same way (callers of `0x140690df0` [`FUN_14068ffa0`] not
  traced). The fix must be
  checked against a swing of the buffed weapon before shipping, or it would strip legitimate
  buffs.
- Whether a left-hand perfume bottle or unique weapon fires the context-1/12 rows from the left
  hand while the right buff is live, for each owner above.
- Why the smoke builds status only on a locked-on Rain of Arrows. It is not the
  `launchConditionType` gate: that spawns the smoke wherever an arrow lands (see below).
- Why one smoke hit 115 times in 3.96 s when the code predicts one hit per victim (see below).

## Smoke spawn gate and re-hit cadence

Static RE, 2026-10-04. Addresses are 1.17.1 [1.16.2]. Every 1.17.1 address below was read in
`eldenring-deobf-1.17.1.bin`. The ones `map-rvas-1162-to-1170.py` left ambiguous (2 or more shape
candidates: the gate, the on-hit spawn, the child creator, the shape cast, the attack issue) were
settled by call graph and by the :8767 dump returning each as a function entry. The call graph in
1.17.1: `0x14039bb00` calls `0x14039da50` then `0x14039bbb0`; `KillBullet` `0x14039f0c0` calls
the same pair; `0x14039dcd0` calls `0x14039bb00`. All are below rva `0xafefe9` except the material
lookup, which is 1.17.0 `0x140c75500` + `0x70`.

| function | 1.17.1 [1.16.2] |
|---|---|
| launch-condition gate | `0x14039da50` [`0x14039da40`] |
| on-hit child spawn | `0x14039dcd0` [`0x14039dcc0`] |
| gate then spawn | `0x14039bb00` [`0x14039baf0`] |
| create the `HitBulletID` child | `0x14039bbb0` [`0x14039bba0`] |
| `KillBullet` (expiry path) | `0x14039f0c0` [`0x14039f0b0`] |
| load each state's row | `0x14039ee10` [`0x14039ee00`] |
| fly-state on-hit handler | `0x1403991f0` [`0x1403991e0`] |
| hit info from `AttackDamageInfo` | `0x14038c110` [`0x14038c100`] |
| contact to `AttackDamageInfo` | `0x140524510` [`0x140523710`] |
| contact loop | `0x140523390` [`0x140522590`] |
| shape cast, writes the contact | `0x140522a80` [`0x140521c80`] |
| hit material of a Havok body | `0x140c75570` [`0x140c73e30`] |
| fly-state start, first attack issue | `0x1403abfd0` [`0x1403abfc0`] |
| spread re-issue | `0x1403abf40` [`0x1403abf30`] |
| bullet attack issue | `0x1403960b0` [`0x1403960a0`] |
| new DmgIns inherits hit records | `0x140525200` [`0x140524400`] |
| attach bullet hit record | `0x140528000` [`0x140527200`] |
| hit list check | `0x14051d3d0` [`0x14051c5d0`] |
| add victim to hit list | `0x140525710` / `0x140524340` [`0x140524910` / `0x140523540`] |
| timed record aging | `0x14051e0e0` / `0x14051d410` [`0x14051d2e0` / `0x14051c610`] |
| `CSBulletFlyState::OnUpdate` | `0x1403939e0` [`0x1403939d0`] |
| `ApplyDamage` | `0x140449d30` [`0x1404497d0`] |
| `CalculateDamage2` | `0x140448910` [`0x1404483b0`] |

### What hit info +0x2c is

`VERIFIED`. It is the hit material id of the Havok body that was struck. The chain:
`0x14038c110` copies `AttackDamageInfo+0x218` to hit info +0x2c. `0x140524510` fills `+0x218`
(and `defenseMaterialSEId` +0x220) from its sixth argument. `0x140523390` passes contact +0x24
there. `0x140522a80` writes contact +0x24 from `0x140c75570`, which reads the struck body's
material and remaps it through `HitMtrlConv`. The default before the lookup is 0.

It is set for every hit, character or map. The only -1 is the expiry path: `KillBullet` calls the
gate with -1 when the bullet's life runs out. So -1 means "no hit", not "hit the ground".
Case 1 accepts materials 20, 21, 22, 27, 41; case 2 accepts a 20-entry list (9, 20-27, 36-39,
41, 46, 47). Those are probably water and swamp materials (`INFERRED`).

### Whose `launchConditionType` is read

`VERIFIED`. The gate reads the row of the bullet's current state. Each bullet has a fly state
holding its own row and an explosion state holding its `HitBulletID` row (`0x14039ee10`,
`CSBulletState::SetBulletParamAndGetHitBullet`).

- On a hit the state is still fly, so the hitting bullet's own value decides. The on-hit handler
  (fly vtable +0x28, `0x1403991f0`) does not change state.
- On expiry `KillBullet` has already moved to the explosion state, so the child's value decides.
- If the previous state's row has `attachEffectType` 4 to 6, that row is used instead.

The values, `VERIFIED` from the switch: 0 and 3 always; 1 and 2 only on the listed materials;
4 only with -1 (spawn on expiry, never on a hit); 5 only with a material (spawn on a hit, never
on expiry); 6 only when the third argument is 0; anything else never. The earlier note that the
child's value decides in both cases is wrong for hits.

### What that does to the Piquebone chain

- Falling arrow 20003354 (`launchConditionType` 0) hits anything: 20003308 spawns. It expires in
  the air: 20003308's own 5 refuses. So 20003308 needs the arrow to land, on the ground or on a
  body.
- 20003308 (speed 0, 0.07 s, penetrates characters and map) hits anything: its own 5 sees a
  material and spawns 20003309. It expires: 20003309's 0 spawns it anyway.

So the code does not make the smoke depend on hitting a character. Every arrow that lands makes
a smoke, locked on or not. The lock-on difference is not explained by this gate. It is more
likely where the smoke sits: create-limit groups 30 and 31 allow one each per owner, and a
locked-on arrow lands on the target (`INFERRED`, not traced).

### How the smoke decides to hit again

The bullet issues its attack once when the fly state starts (`0x1403abfd0`, a new DmgIns). The
DmgIns lives until killed (duration 10000 s, `.rdata` `0x142a1d924` [`0x142a1a914`], read by
`0x140527030` [`FUN_140526230`]). `OnUpdate` re-issues (`0x1403abf40`) only while the hit radius
is spreading, throttled to 1/6 s (`0x142a2a3b0` [`0x142a273a0`]); for the smoke's 0.1 s spread
that is once, just after the spread.
A re-issue kills the old DmgIns and the new one inherits its hit records (`0x140525200`,
`VERIFIED`). This corrects `rain-of-arrows-seppuku.md`, which said the previous handle is passed
only while the radius grows: it is passed whenever the spread progress is above 0, that is on
every re-issue.

Each DmgIns has two hit lists. `0x140524510` skips a victim already in either.

- +0x278: when `dmgHitRecordLifeTime` > 0, `0x1403960b0` attaches a record with that
  lifetime (`0x140528000`, only if +0x278 is still empty). Its entries age in `0x14051d410`, and
  an expired entry lets that victim be hit again: one hit per victim per `dmgHitRecordLifeTime`.
- With `dmgHitRecordLifeTime` 0 nothing is attached. After the first hit `0x140524340` makes
  one lazily, with lifetime 0 (`xorps xmm2` at 1.17.1 `0x140524438`), so it is not on the aging
  list and its entries never expire.
- +0x280: the shared list (`isUseSharedHitList` or `attachEffectType`). The smoke has neither.

So the code predicts one hit per victim per smoke (`INFERRED` from the verified pieces). The live
115 hits over 3.96 s do not fit that unless there were about that many victim/smoke pairs, or a
reset exists that this read did not find. Open. The probe that settles it: log the victim and the
bullet per `CalculateDamage2` call, and count distinct pairs.

### Who is who in `CalculateDamage2`

`VERIFIED` on both builds (`ApplyDamage` passes RCX, RDX, R8 through unchanged to it):

- RCX = the victim's `CSChrDamageModule`. The victim `ChrIns*` is `[RCX+0x8]` (`owningChr`).
- RDX = the damage dealer `ChrIns*`. For a bullet hit it is the bullet's owner, not the bullet.
  It can be null.
- R8 = `AttackDamageInfo*`. The victim is also at +0x1e0 and the attacking field instance (the
  bullet itself) at +0x1d8. 1.17.1 reads +0x1e0 and +0x218 at the same offsets
  (`0x14051bacf`, `0x14038c13f`, `0x14038c15f` [`0x14051accf`, `0x14038c12f`, `0x14038c14f`]).

For Frida on 1.17.1: `victim = args[0].add(8).readPointer()`, `owner = args[1]`,
`bullet = args[2].add(0x1d8).readPointer()`.
