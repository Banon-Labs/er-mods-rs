# Critical hits and parrying

Calculator: `scripts/er-mechanics-crits.py`. Labels as in the other docs here: **VERIFIED**
(regulation value, or read out of the named 1.16.2 Ghidra dump and carried to 1.17.1 with
`map-rvas-1162-to-1170.py` + `map-rvas-1170-to-1171.py`), **TAE** (decoded animation event,
player TimeAct unpacked from `c0000.anibnd.dcx`), **INFERRED**, **COMMUNITY**, **SITE**
(er-build-planner corpus). Nothing was launched. Frames are TAE seconds x 30 (the same
convention as attacks.md). Addresses are 1.16.2 / 1.17.1; "byte-checked" means the 1.17.1
address was found by its bytes in `eldenring-deobf-1.17.1.bin`, otherwise it is the mapper's
candidate.

## 1. What a crit is

A backstab or riposte is a throw. ThrowParam picks the attacker and victim animations; the
attacker animation fires TAE event 304 `ThrowAttackBehavior` whose Args+4 is a behavior judge;
that judge resolves through `PlayerIns::ResolveBehaviorId` exactly as an ordinary attack does
(attacks.md section 0, family fallback included).

| crit | ThrowParam row | throwType | attacker anim | victim anim | judges fired |
| --- | --- | --- | --- | --- | --- |
| backstab | 10000000 | 1 | 031710 | a000_070000 (167 f) | 500 [+ 501] |
| riposte | 30000000 | 20 | 031700 | a000_070010 (195 f) | 510 [+ 511] |

- VERIFIED (regulation): the only ThrowParam rows with `AtkChrId == DefChrId == 0` are
  10000000 and 30000000. Backstab: facings within +-135 deg, range 1.8 m. Riposte: facings
  more than 90 deg apart (Min 90 > Max -90 wraps), range 1.5 m.
- VERIFIED (EXE, `CSThrowNode::ValidateThrowForHKSSpEffects` 0x140485990 / 0x140485ef0):
  throwType 1 requires `IsBackStab` (0x140d51900 / 0x140d536b0) or a defender SpEffect with
  `throwCondition` 10; 20..25 require the defender to carry `throwCondition` 10..15; 10 and 11
  require the attacker to carry 2 / 3; `throwCondition` 1 on either side blocks every throw.
  That type 20 is the riposte and `throwCondition` 10 is the "parried" state is INFERRED (fits,
  no string names it).
- TAE: a weapon category's own crit entries are `ImportOtherAnim` references (mini-header type
  1, imported full id at +0x18): `a020_031710 -> a023_031710` (dagger, one hit), `a026_*` and
  `a031_*` (greatsword, colossal) `-> a032_*` (two hits), `a029_*` (katana) `-> a023_*`. The
  import entry keeps its own sound and effect events but no damage events, so the tool takes
  the union. That the game merges them this way is INFERRED; the EXE confirms only that crit
  damage comes from event 304 (section 2).
- Attacker animation lengths (hkx): `a023_031710` 118 f, `a023_031700` 161 f,
  `a032_031710` 135 f, `a032_031700` 171 f.
- Other throws in the same range, not modelled: 031720 fires 530/531 (row names "Ground Stab"),
  031750 fires 560/561 ("Large Throw"), 031760 fires 550 ("Plunging Throw").

### Crit motion values (regulation, per resolved row)

| weapon family (row) | backstab | backstab MV | riposte | riposte MV |
| --- | --- | --- | --- | --- |
| dagger (100100xxx) | 500 | 294 | 510 | 420 |
| katana (100900xxx) | 500 | 230 | 510 | 345 |
| colossal sword / greatsword (100400xxx) | 500 + 501 | 52 + 168 = 220 | 510 + 511 | 53 + 210 = 263 |
| colossal weapon (102300xxx) | 500 + 501 | 52 + 168 = 220 | 510 + 511 | 53 + 210 = 263 |

All crit rows carry `throwFlag` 2 ("Throw" in the Smithbox enum) and `isAddBaseAtk` 0. The EXE
copies `throwFlag` into `AttackDamageInfo+0xd9` (0x140d24b10) but no damage consumer of it was
found; it is not what enables the crit multiplier.

## 2. Crit damage

```
attack_el = AR_el * MV_el / 100 * (throwAtkRate + 100) * 0.01 [* crit-only SpEffects]
PvP:        * EquipParamWeapon.vsPlayerDmgCorrectRate_el, then the defense step with the
            hit's FinalDamageRateParam row (defense.md), summed over the crit's hits
```

- VERIFIED (EXE, attack-power builder 0x1406832a0 / 0x1406840f0; the
  `movsx eax, word [rbx+0xe0]; add eax, 0x64` read is at 0x140683453 / 0x1406842a3,
  byte-checked):
  `mult = 1.0; if (attackInfo[0x109] && weaponRow) mult = (throwAtkRate + 100) * 0.01f;`
  It multiplies all five elements (physical, magic, fire, lightning, holy), not status
  build-up and not throw escape, and not on the `attackNotFromWeapon` branch.
- VERIFIED gate: `AttackInfo+0x109` is written by `FUN_140526670` (DmgMan attack spawn; the
  mapper could not carry it to 1.17) from its argument; its single caller `FUN_14043fc30`
  (0x140440190) passes constant 1, runs only with a valid throw partner
  (`CSChrThrowModule::GetForwardingTargetHandle`), and is reached from `FUN_14042c0f0`
  (0x14042c640), the `ThrowAttackBehavior` handler in `CSChrTaeAnimEvent::ExecuteThreadTwo`.
  That nothing else writes the byte is INFERRED (the search covered immediate stores only).
- `throwAtkRate` (regulation): 0 on 566 of 601 named weapons; Dagger 30, Rapier 30,
  Misericorde 40, Executioner's Greataxe 15, and 10 on 30 others (most daggers and claws,
  some straight swords, Lordsworn's Greatsword, Highland Axe, Death Ritual Spear). The in-game
  "Critical" number is 100 + this value (COMMUNITY), matching the EXE.
- Crit-only SpEffects. VERIFIED (EXE): `SpecialEffectEntry::IsApplicableForCategory`
  0x140500930 / 0x140501700 (byte-checked at 0x140501892) tests SpEffectParam `+0x164` bit 3,
  `throwAttackParamChange`; with it set, the effect's attack modifiers apply only when the
  attack's copy of `AttackInfo+0x109` is set. Regulation: rows with the bit are the Dagger
  Talisman 320900 (`*AttackRate` 1.17 on all five elements) and 1694 / 1699 / 1704 / 1709 /
  1711..1714 (0.75, row names "Determination / Royal Knight's Resolve - Critical Damage
  Debuff"). That `*AttackRate` is the field applied is INFERRED from the rows; the tool
  applies them per element through `speffects=`. The Assassin's daggers do not carry the bit
  (their effect is not a damage multiplier).
- PvP final rate. VERIFIED (regulation, all 450 AtkParam_Pc rows with `throwFlag` 2): the
  FinalDamageRateParam row is 1.0 on every element except the Backhand Blade crit rows (6400xxx:
  row 3500000, 0.85; 6402xxx: row 3550000, 0.9) and three non-weapon rows (73001, 300000026 at
  1.25; 600000862 at 0.8). 27 rows have no final-rate row (-1). The tool applies each hit's row. No PvP branch exists in the attack-power builder (VERIFIED).
- Crucible Scale. VERIFIED (EXE, `CalculateDamage` 0x1404472b0 / 0x140447810, read in full;
  the compare `cmp byte [r15+0xd9], 2` at 0x140447c08 / 0x140448168 byte-checked in both images):
  the defense stage has one throw-specific step. When the hit's `throwFlag` copy
  (`AttackDamageInfo+0xd9`) is 2 and its AtkParam row has `throwDamageAttribute` 1, the victim's
  `CalculateDamageCutRates` (0x1404f5210 / 0x1404f5fe0) multiplies every element by the cut rates
  of its SpEffects with stateInfo 335. Regulation: stateInfo 335 is the Crucible Scale Talisman
  (SpEffect 340600, its `refId`) and the Talisman of All Crucibles (20382402, its
  `residentSpEffectId2`), 0.7 on every element. `throwDamageAttribute` 1 is on the backstab judges
  500/501/505/506 and on no riposte row (510/511 all 0), so the talisman cuts backstabs by 30% and
  ripostes not at all. The COMMUNITY description "reduces critical damage" is wider than this.
  `crit_damage(defender_speffects=...)` applies it.
- Nothing else in `CalculateDamage` is crit- or PvP-specific for a player victim:
  `stealthAtkDamageRate` needs the victim's AI to be unaware (not a player), and
  `GetMPLevelCorrection` is multiplayer level sync, applied to every hit. The victim animations
  a000_070000 / 070010 apply SpEffects 90, 9641 and 19385, none of which carries a damage cut
  (regulation). `ApplyDamage` / `CalculateDamage2` were searched for throw fields, not read in
  full: only throw-escape handling turned up.
- `FUN_140691320` (0x140692170) in the same product is not crit-specific: weapons with
  `isHeroPointCorrect` get 1.05..1.21 from a `PlayerGameData+0x60` level (VERIFIED). Not modelled.
- Two-handing uses the same crit rows (no +200 crit judge); only the x1.5 STR in the AR changes.

### Guard counters are not crits

The guard counter is judge 180 (1H) / 380 (2H), fired by TAE type 1 `AttackBehavior`. That path
does not set `AttackInfo+0x109`, so `throwAtkRate` and the Dagger Talisman do not reach it
(VERIFIED by the gate above). Its rows are ordinary attacks with PvP final-rate ids (dagger
MV 140, row 100105; colossal MV 125, row 2300000); attacks.md handles them.

## 3. RL 150 table

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --table`

Attacker: the `REF_ATTACKERS` line (strength / quality / dexterity / faith, each summing to
RL 150) with the highest one-handed AR, Standard affinity, max level, no talismans. Defender:
vig 60, mnd 20, end 40, str 44, dex 40, int 9, fth 9, arc 7 in the Banished Knight set, PvP
absorption columns. Damage is the HP damage of the whole crit (all hits). Parry columns are
the exposure of section 4 (both grips, 1H only, 2H only).

| weapon | stats | crit % | AR 1H | BS MV | rip MV | BS 1H | BS 2H | rip 1H | rip 2H | parry | 1H | 2H |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Greatsword | strength | 0 | 763 | 220 | 263 | 924 | 978 | 1115 | 1179 | 0.40 | 0.80 | 0.00 |
| Giant-Crusher | strength | 0 | 821 | 220 | 263 | 1097 | 1176 | 1322 | 1416 | 0.40 | 0.80 | 0.00 |
| Erdsteel Dagger | faith | 10 | 284 | 294 | 420 | 535 | 566 | 785 | 824 | 0.83 | 0.83 | 0.83 |
| Misericorde | dexterity | 40 | 392 | 294 | 420 | 964 | 981 | 1377 | 1402 | 0.83 | 0.83 | 0.83 |
| Uchigatana | quality | 0 | 536 | 230 | 345 | 705 | 745 | 1057 | 1118 | 0.82 | 0.82 | 0.82 |
| Dagger | dexterity | 30 | 367 | 294 | 420 | 838 | 868 | 1198 | 1241 | 0.83 | 0.83 | 0.83 |
| Zweihander | quality | 0 | 657 | 220 | 263 | 790 | 841 | 956 | 1015 | 0.40 | 0.80 | 0.00 |
| Claymore | quality | 0 | 616 | 220 | 275 | 811 | 877 | 1013 | 1096 | 0.81 | 0.81 | 0.81 |
| Cleanrot Knight's Sword | quality | 0 | 466 | 253 | 380 | 704 | 759 | 1058 | 1140 | 0.83 | 0.83 | 0.83 |
| Banished Knight's Halberd | quality | 0 | 585 | 220 | 308 | 770 | 819 | 1078 | 1147 | 0.81 | 0.81 | 0.81 |
| Executioner's Greataxe | strength | 15 | 703 | 231 | 275 | 1035 | 1098 | 1242 | 1316 | 0.81 | 0.81 | 0.81 |

Per unit of AR the dagger family's riposte is 420 x 1.3 = 546 (Dagger) or 588 (Misericorde)
against 263 for a colossal, so a Misericorde riposte out-damages a greatsword one even at half
the AR. The colossal pays for its crits with the lowest exposure: two-handed, nothing it does
can be parried.

## 4. Which attacks can be parried

VERIFIED (EXE, `FUN_14044a910(hit, chrA, chrB)` 0x14044a910 / 0x14044ae70). Two triggers, one
condition on the attacker:

- **Hitbox.** The parry skill fires TAE type 1 `AttackBehavior` with AttackType 64
  (template "Parry"), which spawns a hit of type 0x40 (judge 591 -> AtkParam 90591 "Parry
  Attack", MV 0). When it touches an attacker, the attacker is parried if its
  `actionModifiersFlags & 0x400` is set, its guard-hand weapon's `defenseBaseParry <=` the
  parrier's `attackBaseParry`, and the angle check passes.
- **Contact.** An ordinary hit (type < 0x40) that touches a defender with
  `actionModifiersFlags` bit 37 set (TAE JumpTable 119, template "TryToInvokeForceParryMode")
  is turned into the same 0x40 test, provided the hit's `AttackDamageInfo+0x115 & 0x10` is
  clear. That bit is AtkParam `isDisableParry` (default 1 when there is no row, 0x140d24b10),
  and the defender's `attackBaseParry` must be `>=` the attacker's `defenseBaseParry`.
- Both end in `CSChrDamageModule::ValidateParryAngles` (vtable slot 9, 0x140444840 /
  0x140444da0), which again requires the attacker's 0x400: facings with dot product below
  cos 120 deg, both aim cones, facing rotated by AtkParam `parryForwardOffset`.

The 0x400 flag is set by the attacker's own TAE type 0 JumpTable 5 (attacks.md section 4;
template "Get-Parried Window"). So **an attack is parryable exactly while JumpTable 5 is open
in its animation**, whichever route fires. `isDisableParry` 0 widens the catch (the attack is
also parried by walking into a JT119 state, not only by being struck by the parry hitbox) but
never adds exposure. `attackBaseParry` and `defenseBaseParry` are 0 on every named weapon
(regulation), so their comparison always passes in this build.

Measured (TAE + regulation):

- In every weapon animation read, JT5 is the first frame of the hitbox (dagger R1 #1: JT5
  f10-11, hit f10-12). The attacker is exposed for one TAE frame per swing.
- Small weapons (dagger, straight sword, katana, Claymore family): every ground attack in both
  grips, charged R2 included. Jump R1/R2 and the guard counter have no JT5, so they are not
  parryable although their rows have `isDisableParry` 0.
- Greatsword, colossal sword, colossal weapon: one-handed, every ground attack including the
  charged R2s; jump attacks and the guard counter not. Two-handed: no JT5 anywhere, nothing.
  Their rows all have `isDisableParry` 1.
- Halberd, greataxe: 13 of 16 slots per grip (all but jump R1/R2 and guard counter).
- COMMUNITY "colossal weapons can only be parried one-handed" and "jump attacks cannot be
  parried" agree with this.

**Parry exposure** (`parry_exposure()`): the share of the attack slots attacks.md lists (both
grips, crouch R1 included, skills and off-hand excluded) that have JT5. Every slot counts once,
not weighted by how often it is used.

On a parry, `FUN_140445960` asks the parried character's damage module for a
`receivedDamageType`: players always get 5 (`CSPlayerDamageModule` 0x140446340); enemies use the
JT5 byte argument (6/7/8 -> 0x3f3..0x3f5). How long the parried player stays open to a
riposte is not set by the parry code (section 6).

## 5. Parry tools

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --parry-tools`

Parry skills live in TAE category `600 + SwordArtsParam.swordArtsTypeNew` (INFERRED: a692..a699
are exactly the files that hold AttackType 64 events, and rows 302..309 carry
swordArtsTypeNew 92..99). Windows are the AttackType 64 event (TAE, 30 fps frames); the JT119
contact state is open over the same frames except where noted.

| skill (SwordArtsParam) | TAE anims | parry hitbox | JT119 | default on |
| --- | --- | --- | --- | --- |
| Parry (302) | a692 040000/030/040 | f4-6 | f4-6 | Parrying Dagger and others |
| Parry (302) | a692 042000/030/040 | f4-8 | f4-8 | |
| Parry (302) | a692 042800/830/840 | f4-6 | f4-6 | |
| Parry (302) | a692 044800/830/840 | f4-9 | f4-9 | |
| Buckler Parry (303) | a693 | f4-9 | f4-9 | Buckler |
| Carian Retaliation (305) | a695 040000 / 040005 | f4-10 / f6-8 | same | ash only |
| Storm Wall (306) | a696 | f4-9 / f6-8 | same | ash only |
| Golden Parry (307) | a697 040000 / 040005 | f4-7 (judge 3690) / f6-8 | f4-10 / f6-8 | ash only |
| Thops's Barrier (309) | a699 | f4-8 / f6-8 | same | ash only |
| Golden Retaliation (1196) | a796 | none found | | Erdtree Greatshield |

- `EquipParamWeapon.parryDamageLife`: VERIFIED (EXE, `FUN_1404428f0` 0x1404428f0 / 0x140442e50,
  read at 0x140442b7b): only for hits of type 0x40, a value above 0 sets the parry hit's
  lifetime to `parryDamageLife / 30` s, else 10000 s (bounded by the TAE event). It is 10 on
  665 of 676 base rows and -1 on 11 (regulation), so 10 TAE frames: longer than every window
  above, so in this build the TAE window is what limits the parry hitbox.
- The hitbox route needs the parry hit to overlap the attacker's one-frame JT5; the contact
  route needs the attacker's hit to land inside the parrier's JT119 window while its own JT5 is
  open. Either way the effective timing is "the attacker's first active frame falls inside the
  parry window" (INFERRED from the two conditions; not measured).
- Which Parry variant plays for which weapon class and hand is decided by the behavior graph and
  was not traced. The `04x005` variants (f6-8) look like the follow-up press; not traced.
- Golden Parry's first window is skill judge 3690 (the wide golden hitbox) rather than 591; its
  JT119 stays open to f10. Golden Retaliation (a796) has no AttackType 64 event, so no window is
  given for it.

### Parry tools in the corpus

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --corpus`

STR PvP build = PvP (`isPvE` false or a tag in Invasions/Duels/Co-op/Gank/2v2/Ladder) and STR
at least every other damage stat. A build counts when an equipped weapon's skill (its
`weaponArt`, else the weapon's default) is one of the seven parry skills. Hands by `equipIndex`
(SITE: 0..2 right, 3..5 left; shields sit at 3/4/5 in 1206 of 1352 equipped cases).

- 1482 STR PvP builds; 169 (11.4%) carry a parry skill, 150 of them in the left hand.
- By skill: Carian Retaliation 87, Parry 42, Golden Parry 19, Golden Retaliation 8, Storm Wall 8,
  Buckler Parry 3, Thops's Barrier 2.
- The "44%" is the Misericorde, carried as a crit weapon, not as a parry tool (section 7).
- These numbers use this module's older STR filter and tag set (`PVP_TAGS` here has `Co-op` and
  `Gank`, the planner's tag is `Co-op/Gank`) and `equipIndex` rather than the active set. The
  crit factor uses the `er-builds-pvp.py` corpus instead: 20.4% of its RL 150 window carries a
  parry skill (section 7).

## 6. Not established

- **Riposte window after a parry.** Nothing in the parry code sets a timer. Regulation + TAE:
  the only SpEffects with `throwCondition` 10 are 30 ("HKS - Unk Throw Def Invalid") and 16592,
  and SpEffect 30 is applied by TAE type 67 over f0-47 (1.57 s) in exactly seven player
  animations, a000_005800 / 005810 and a000_019500 / 510 / 520 / 530 / 560. That the 0195x0
  group is the parried reaction (`W_DamageParry` in `c0000.hks`) and 00580x the guard break is
  INFERRED from the counts; the behavior graph that picks them was not read.
- The numeric aim-cone angles in `ValidateParryAngles`, and where a parry hitbox gets its 0x40
  type (it is passed in by the caller of 0x140d24440).
- `ApplyDamage` / `CalculateDamage2` were only searched, not read (section 2). Grease on a crit:
  whether the flat grease attack takes the crit multiplier was not traced, so the factor below
  leaves grease out of crits.
- That the SpEffect `*AttackRate` fields are what `throwAttackParamChange` effects multiply
  (the gate is VERIFIED, the field is INFERRED from the rows).
- Event merging for `ImportOtherAnim` crit entries (section 1).
- 30 fps as the TAE frame rate (inherited from attacks.md).
- `FUN_140526670` has no 1.17.1 address; several other 1.17.1 addresses above are mapper
  candidates, not byte-checked (only 0x1406842a3 and 0x140501892 were).

## 7. Crit factor for the PvP score

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --factor --rl 150`

The PvP ranking (`er-builds-pvp.py --sort score`) scores a slot by damage per second of
commitment. Crits enter it the way status does: as HP added to the slot's damage, per exchange
(one use of the slot). Two terms, both in HP:

```
crit_hp   = q_riposte x riposte_eff + BACKSTAB_PER_EXCHANGE x backstab
q_riposte = parry_tool x PARRY_LAND x exposure
riposte_eff = (1 - swap) x riposte + swap x max(riposte, swap weapon's riposte)
parry_hp  = parry_tool x PARRY_LAND x corpus riposte        (only on a parryable slot)
```

`riposte`, `backstab`: this build's own crits (its weapon, affinity, level, stats and grip) as
the mean over the RL window's PvP corpus, through `er-builds-pvp.py`'s own `Defenders` and
`corpus_hit` (the backstab cut by the corpus's Crucible share, section 2). Crits are never
counter hits. Grease is not added (section 6).

### Measured on the corpus (SITE, RL 150 +- 10, the same 1074 builds `er-builds-pvp.py` scores against)

| quantity | value | definition |
| --- | ---: | --- |
| `parry_tool` | 20.4% (17.9% left hand) | active weapon set carries a parry skill (`weaponArt`, else the weapon's default) in either hand. Parry 110, Carian Retaliation 82, Golden Parry 15, Buckler Parry 5, Storm Wall 5, Golden Retaliation 2 |
| `swap` | 32.7% | a right-hand weapon other than the primary with `throwAtkRate` >= 30: all of them Misericordes (Fire 143, Lightning 85, Magic 46, Sacred 25, ...) |
| crit-cut talisman | 0.1% | 1 build wears the Talisman of All Crucibles, none the Crucible Scale |
| `exposure` | 0.689 | mean `parry_exposure` of each build's primary weapon in its own grip (979 builds resolved) |
| corpus riposte | 1413 HP | mean of each build's best riposte among its right-hand weapons, on the corpus (991 builds) |
| corpus backstab | 1009 HP | the same for backstabs |

The "parry dagger in 44% of STR builds" of giant-crusher-adoption-gap.md is the Misericorde
(43.8% of STR-tag builds, right hand). Its skill in those builds is almost never a parry: in
the RL 150 window's right hands it carries Bloodhound's Step 202, Endure 90, Quickstep 19,
Royal Knight's Resolve 8, Kick 8, Parry 6. It is carried as a crit weapon. The corpus
measures that as `swap`, not as `parry_tool`. That players swap to it after a parry is
COMMUNITY; the carriage is SITE.

### Weights (INFERRED)

- `PARRY_LAND = 0.10`: chance a tool carrier parries a parryable attack in one exchange. No
  match data exists; the parry hitbox is 3 to 6 TAE frames against the attacker's one-frame
  JumpTable 5 (sections 4 and 5), which is a tight read.
- `BACKSTAB_PER_EXCHANGE = 0.02`: chance an exchange ends in a backstab. No data.
- `SWAP_MIN_THROW_RATE = 30`: the Misericorde (40), Dagger and Rapier (30) qualify; the 10s do not.

### What it does to the score (RL 150 reference attackers, `--factor`)

`crit_hp` is 26 to 47 HP per exchange: 40 to 47 for the Greatsword, Giant-Crusher and
Executioner's Greataxe (high AR), 34 to 38 for the quality straight swords, katana, halberd and
Dagger, 42 for the Misericorde, 27 for a faith Erdsteel Dagger. The swap flattens it: a third of attackers
riposte with a Misericorde whatever they hold, so a low-crit weapon loses less than its own
riposte suggests. Against slot damage of 400 to 800 HP this is a 4 to 9% addition, and the
spread between weapons is 2 to 3 points.

### The parry penalty, re-examined

The old `SCORE_PARRY_FACTOR` 0.85 took 15% off every parryable slot. With the corpus numbers the
cost of a parryable use is `0.204 x PARRY_LAND x 1413` = 28.8 HP at `PARRY_LAND` 0.10, about 5% of
a 600 HP slot. For 0.85 to be right, `implied_parry_land(ev, 0.85, dmg)` = 0.31 at 600 HP (0.21 at
400, 0.42 at 800): a parry-tool carrier would have to parry a third of the parryable attacks it
faces. The old factor was also flat in the slot's damage, while the real cost (a riposte taken)
does not grow with the attack that was parried, so it over-penalised the heavy hitters most.
Replacing it by `parry_hp` removes both.

`--rescore` on the RL 150 run (324 weapon/grip rows, 2026-09-29): most of the reordering comes
from the parry change, not the crit term. Parryable best slots gain about 12% against
non-parryable ones: Star Fist 2H 5 -> 2, Warped Axe 2H 12 -> 4, Iron Greatsword 2H 14 -> 8.
Weapons whose lead came from being unparryable fall: Urumi 2H 61 -> 169, Zweihander 2H 43 -> 111,
Fire Knight's Greatsword 2H 32 -> 81. Giant-Crusher 2H stays first (crit_hp 50, the highest of
the top 25).

## 8. Interface for the PvP ranking

```python
spec = importlib.util.spec_from_file_location('er_mechanics_crits', 'scripts/er-mechanics-crits.py')
CR = importlib.util.module_from_spec(spec); spec.loader.exec_module(CR)
t = CR.load_tables()                            # a few seconds; build once and reuse
CR.crit_profile(t, 'Misericorde')               # multiplier, per crit kind: hits (judge, row,
                                                # MV, frame), attacker/victim animation lengths
CR.crit_damage(t, 'Greatsword', 'riposte', affinity='Heavy', level=25, stats={...},
               two_handed=True, defender=DEF.defender(...), pvp=True,
               speffects=(CR.DAGGER_TALISMAN_SPEFFECT,))
                                                # -> {'attack', 'damage', 'hits': [...], ...}
CR.parry_exposure(t, 'Giant-Crusher')           # -> {'exposure', 'exposure_1h', 'exposure_2h',
                                                #     'classic', 'contact', 'detail': [per slot]}
CR.parry_tools(t)                               # parry skills, windows, default carriers
CR.corpus_parry_share(t)                        # the corpus numbers above
CR.crit_attack(t, 'Dagger', 'backstab', ...)    # per hit: attack_by element, phys, final_rate,
                                                # crit_cut (Crucible applies)
ev = CR.crit_evidence(t, rl, window, mirror, defenders=defenders)   # section 7, once per run
CR.weapon_crit(t, ev, weapon, aff, level, stats, two_handed, defenders)
                                                # -> {'crit_hp', 'parry_hp', 'riposte_hp',
                                                #     'backstab_hp', 'swap_riposte_hp', ...}
```

`crit_damage(..., defender_speffects=(CR.CRUCIBLE_SCALE_SPEFFECT,))` gives a crit on a defender
wearing the Crucible Scale. `--rescore <er-builds-pvp --sort score --json output>` re-ranks an
existing PvP run with section 7 in place of the parry factor, before the ranking itself changes.

Integration into `er-builds-pvp.py` (section 7):

1. In `Mechanics.__init__`, after `self.cr_t`: nothing new is loaded there.
2. In `main()`, after `mech = Mechanics(...)`: `crit_ev = mech.cr.crit_evidence(mech.cr_t, a.rl,
   a.window, a.mirror, defenders=defenders)`.
3. Per sweep row, after `base_id`: `crit = mech.cr.weapon_crit(mech.cr_t, crit_ev, base_id, b["aff"],
   level, stats, row["two"], defenders)`; store it on the result (`"crit": crit`).
4. Pass it to `slot_score(slots[key], entry_frames(key), crit)`. In `slot_score`, with
   `crit_hp = crit["crit_hp"] if crit else 0` and `parry_hp = crit["parry_hp"] if crit and
   s.get("parryable") else 0`: `rate = (s["dmg"] + SCORE_STATUS_WEIGHT * status_hp +
   SCORE_CRIT_WEIGHT * crit_hp - parry_hp) / commit * SCORE_FPS`, `SCORE_CRIT_WEIGHT = 1.0`, and
   drop `f_parry` (or set it to 1.0 and keep the key). Return `crit_hp` and `parry_hp` in the dict.
5. Selftest: `slot_score({**base, "parryable": True}, 0, {"crit_hp": 0, "parry_hp": 30})` scores
   lower than `base`; a `crit_hp` above 0 scores higher.

`defender` is a dict from `er-mechanics-defense.py`'s `defender()`; `None` returns the
pre-defense attack only. `stats` takes the AR module's keys (str, dex, int, fth, arc; extra keys
are ignored). `weapon` is a name or an EquipParamWeapon id.

## Commands

```
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --table
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --parry-tools
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --corpus
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --factor --rl 150
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --sort score --json > pvp150.json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py --rescore pvp150.json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py Dagger Greatsword
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-crits.py Misericorde --json
```
