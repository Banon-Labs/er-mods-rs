# Disengage: what a dodge buys a player who wants out

Labels as in the other files here: **VERIFIED** = regulation value or code read out of the 1.16.2
executable (named dump on :8765, shift 0), **TAE** = decoded TimeAct, **MEASURED** = computed by
the commands below from game files, **COMMUNITY** = the Smithbox decompile of `c0000.hks` or the
Smithbox TAE template's event names, **INFERRED** = a modelling choice. Nothing was launched.

Tool: `scripts/er-mechanics-disengage.py` (`--selftest` passes 14/14). The scored term is
`er-mechanics-ashes.utility_value` under `DISENGAGE`, switched on by
`er-builds-pvp.py --disengage [sprint|run]` (off by default).

## 0. In plain words

The lead to check: Bloodhound's Step is used to escape, not to close; the neutral model only saw
it close, so it scored the step at the moveset (neutral.md section 5, ashes-of-war.md 17d).

What the data says:

- **The step does leave more room than a roll.** Turned away from the chaser it carries 4.96 m by
  the first frame a flask can be pressed (f27) and 5.24 m in all; the medium roll carries 3.27 m
  by its item frame (f21) and 3.65 m in all. It chains into itself at f23 (the roll at f21) and
  costs 5 FP a use. Against a chaser who sprints after it, each step keeps about 0.2 m more than
  he closes; each roll loses about 1 m.
- **That room does not buy a flask against a sprinting chaser.** The chaser's running R1 reaches
  5.98 m (pool mean). Drinking is denied (hit before the sip lands at f31) below about 8.8 m and
  traded (hit before the drink can be left at f54) below 13.4 m. No dodge sequence gets there: 4.1
  to 4.6 m with the step, 2.7 to 4.1 m with the roll. Against a chaser who stays locked on and runs
  (4.01 m/s) three steps reach 8.7 m and sometimes trade a heal; the scored gain is small (section 5).
- **Where the step does what a roll cannot is out of a stagger.** The skill button leaves a stagger
  before the roll does: 7 against 10 frames out of a small stagger, 24 against 25 out of a middle
  one, 30 against 35 out of a large one (first stagger in a row). A string that is a true combo
  against a roll can be stepped out of. Of combo.md's 30,386 true right-hand to off-hand L1 links,
  24,508 are escapable this way, and 11,525 of its 11,536 ties. Out of a guard reaction it is the
  other way round: the roll comes first.
- **Tracking.** The step makes the player fully transparent from f5 to f10 (TAE 193, COMMUNITY
  name) and hides the weapon over the same frames. Nothing in the step turns off character
  collision, so passing through the attacker is not supported by the data.

## 1. The dodges (TAE + hkx, MEASURED)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py tools`

Frames are real frames from the dodge's first frame. `skill` is the L2 gate under both readings
of the cancel ids (section 2), `chain` the frame the same dodge can start again, `at item` the
distance travelled by the item frame.

| dodge | clip | i-frames | R1 | roll | skill (0 / 8) | item | move | chain | at item | end |
|---|---|---|---|---|---|---|---|---|---|---|
| roll, light (INFERRED load) | a000_027100 | f0-13 | 18 | 21 | 19 / 19 | 21 | 22 | 21 | 3.96 m | 4.34 m |
| roll, medium | a000_027110 | f0-13 | 20 | 21 | 20 / 20 | 21 | 22 | 21 | 3.27 m | 3.65 m |
| roll, heavy (INFERRED) | a000_027120 | f0-12 | 23 | 28 | 23 / 23 | 27 | 30 | 28 | 2.96 m | 3.31 m |
| roll, overloaded (INFERRED) | a000_027130 | none | 60 | 60 | 60 / - | 60 | 60 | 60 | 0.52 m | 0.50 m |
| roll a000_027140 (load not identified) | a000_027140 | f0-13 | 22 | 22 | 22 / - | 22 | 22 | 22 | 4.03 m | 4.21 m |
| roll, medium, back | a000_027111 | f0-13 | 20 | 21 | 20 / 20 | 21 | 22 | 21 | 3.71 m | 3.17 m |
| backstep a000_027000 | | none unconditional | 14 | 16 | 16 / 14 | 17 | 19 | 16 | 2.29 m | 2.50 m |
| backstep a000_027010 | | none | 11 | 18 | 11 / 11 | 18 | 19 | 18 | 2.29 m | 2.50 m |
| backstep a000_027020 | | none | 15 | 27 | 25 / 25 | 27 | 27 | 27 | 2.46 m | 2.50 m |
| Bloodhound's Step, away | a756_040080 | f0-10 | 17 | 25 | 21 / 23 | 27 | 29 | 23 | 4.96 m | 5.24 m |
| Bloodhound's Step, back | a756_040081 | f0-10 | 16 | 23 | 21 / 23 | 25 | 27 | 23 | 4.84 m | 4.72 m |
| Quickstep, away | a755_040080 | f0-9 | 17 | 25 | 21 / 22 | 27 | 29 | 22 | 3.98 m | 4.28 m |

- **Which clip a load plays** is read from the ten-block order (light, medium, heavy, overloaded)
  and is INFERRED, except that medium 027110 is the one the ranking already uses. The behavior
  graph picks it through `EvasionWeightIndex` (COMMUNITY `SetWeightIndex`); the selector was not
  decoded. The backsteps carry i-frames only under a stateInfo gate (473), none unconditional.
- **Which step clip plays** (COMMUNITY, `SWORDARTS_REQUEST_RIGHT_STEP`): not locked on, direction 0
  (the forward clip) turned to the stick, so a player running away steps away with 040080; locked
  on, the four directions, back being 040081. The roll does the same (`ExecEvasion`,
  `RollingDirectionIndex` 0 when not locked on). a756_040085 (i-frames f0-5) is taken to be the
  without-FP copy, as `er-mechanics-ashes.skill_evasion` reads the ten-block.
- **The step chains into itself.** A step pressed inside a step plays
  `W_SwordArtsRolling_SelfTrans` / `_SelfTrans2` (COMMUNITY; the behavior graph has both states).
  The press opens at f21 (cancel 16) or f23 (cancel 103 / 104). The later one is used.
- **FP and stamina.** Bloodhound's Step costs 5 FP (SwordArtsParam 801 `useMagicPoint_L2`),
  Quickstep 3 (800). Both charge the roll's 12 stamina (ashes-of-war.md 14a).

## 2. Leaving a stagger or a block (VERIFIED gates, TAE windows, COMMUNITY state logic)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py gates`

An action needs an input window and a cancel window that overlap. Read out of `_ChrActionFlag`
(0x1404275e0) on the named 1.16.2 dump:

| JumpTable | allows |
|---|---|
| 25, 87 (input) + 26 (cancel) | roll: `SP_MOVE`, `BACKSTEP`, `ROLLING` |
| 9, 87 (input) + 16, 118 (cancel) | L2, when `actionAnimationFlags & 0x7f8` is 0 (16 also opens L1) |
| 106 (input) + 103 (cancel, flags 8) / 104 (cancel, flags 8 or 16) | L2 |
| 30, 87 (input) + 31 (cancel) | item |

What sets `actionAnimationFlags` bits 3-4 was not traced, so both readings are measured; in every
stagger clip they agree.

Out of a stagger the behavior script (`DamageCommonFunction`, COMMUNITY) calls `ExecEvasion` with
`UseChainRecover`: the roll also waits for EzState flag 2 / 3 / 4 / 5 by `DamageCount` 1 / 2 / 3 /
4+ (TAE 227). `ExecAttack`, which carries every L2 skill, is not gated by the flag.

| reaction | clip | roll, DamageCount 1 / 2 / 3 / 4+ | skill | R1 | item |
|---|---|---|---|---|---|
| minimum | a000_005000 | 10 / 7 / 4 / 0 | 7 | 7 | 13 |
| small | a000_005100 | 10 / 7 / 4 / 0 | 7 | 7 | 12 |
| middle | a000_005200 | 25 / 10 / 5 / 0 | 24 | 24 | 25 |
| large | a000_005300 | 35 / 15 / 5 / 0 | 30 | 30 | 35 |
| push | a000_005500 | 35 / 15 / 5 / 0 | 35 | 35 | 40 |
| guard small | a000_019200 | 5 | 10 (0 under flags 0) | 5 | 13 |
| guard middle | a000_019210 | 10 | 31 (10 under flags 0) | 10 | 31 |
| guard large | a000_019220 | 47 | 50 | 50 | 52 |
| guard break | a000_019500 | 43 | 55 | 55 | 55 |

- **First stagger in a row, the skill comes out first**: 3 frames out of a small stagger, 1 out of
  a middle one, 5 out of a large one. A step started there has i-frames f0-10, which covers every
  one of those windows. From the second stagger in a row on, the roll gate falls below the skill's.
- **Out of a guard reaction the roll is first or equal**, so a step buys nothing out of a block.
- **combo.md's links re-read** (`cross-hand`, MEASURED, DamageCount 1, no hit-to-reaction delay):
  of 30,386 right-hand to off-hand L1 links that are true against a roll, 24,508 have their gap at
  or after the skill's gate (large stagger 27,548 and middle 8,485 of the true-or-tie ones), and so
  are escapable with a step; 11,525 of the 11,536 ties too. The same-weapon strings of the RL 150
  pool give 7 such links (6 ties), so against the opponent pool the ranking uses this is worth
  nothing yet. It is not in the scored term: the pool throws no cross-hand strings until the
  off-hand combo credit gives its rows one.
- Whether a press made earlier in the stagger is held until the gate opens was not traced (the
  same open question combo.md has for the roll).

## 3. The flask (REGULATION + TAE)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py flask`

- Flask of Crimson Tears +12 is EquipParamGoods 1025 -> `refId_default` 501012, whose
  `changeHpEstusFlaskPoint` is -810: **810 HP** (VERIFIED). +0 is 250.
- The drink is `W_ItemRecover`, clip a000_050000 (behavior graph; that `goodsUseAnim` 10 selects it
  is INFERRED). TAE 65 `Consume Selected Goods` (COMMUNITY name) fires at **f31**; nothing (roll,
  R1, item, move) cancels the clip before **f54**; the clip is 55 frames long and has no root motion.
- A hit before f31 is taken to interrupt the drink without using the flask; a hit between f31 and
  f54 lands on a player who has healed (INFERRED).
- Several SpEffects scale flask healing (`changeHpEstusFlaskCorrectRate`, e.g. 651 at 0.85); which
  of them apply in PvP was not traced, so the full 810 is used.

## 4. The race (INFERRED model, MEASURED inputs)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py race [--chaser run] [--hidden]`

- Both players start 2.5 m apart. The escaper leaves in a straight line with his dodge, and chains
  it on the first frame he can.
- The chaser follows at sprint speed, 6.04 m/s (a chaser who sprints drops his lock-on, neutral.md
  section 1), or at the locked-on run, 4.01 m/s. He leaves 10.5 real frames after the dodge starts
  (the median of `er-mechanics-ashes.reaction_delays`: 0.25 s plus two 50 ms network legs), or
  when his own recovery ends, whichever is later.
- Once both run at the same speed the gap stays, so the escaper drinks at the first frame his last
  dodge lets him use an item.
- The chaser then lands whichever of his attacks lands first from that distance: running R1
  (a sprint plays the running attacks), running and sprinting jump R1 / R2 (moveset.md section 6,
  travel included), or R1 #1 (`chaser_options`). Pool mean running R1: reach 5.98 m, first hit
  f16.9, 410 HP; the sprinting jump R2 is the longest reach for 78% of the pool (build weight).

Separation when the drink starts:

| sequence | sprinting chaser | running chaser |
|---|---|---|
| medium roll, away | 3.66 m | 4.37 m |
| medium roll x2 | 2.71 m | 4.83 m |
| medium roll, back | 4.10 m | 4.81 m |
| medium roll, back, x2 | 3.58 m | 5.71 m |
| backstep 027000 | 3.49 m | 3.92 m |
| step, away | 4.14 m | 5.26 m |
| step x2 | 4.30 m | 6.96 m |
| step, back | 4.42 m | 5.40 m |
| step, back, x2 | 4.60 m | 7.12 m |
| Quickstep | 3.16 m | 4.28 m |

Against the pool-mean running R1 the drink is denied below 8.82 m (sprinting chaser) / 7.87 m
(running), and traded below 13.44 m / 10.94 m. So:

- **Against a sprinting chaser no dodge buys a drink**, even three steps in a row (4.46 m).
- **Against a locked-on running chaser** three steps reach 8.67 m, enough to trade a heal against
  the shorter chasers; a roll never gets there.
- **Hidden frames (`--hidden`, INFERRED).** If the chaser can only react once the step shows the
  escaper again (f10), the step gains another 2.0 m against a sprinter (6.15 m after one step, 6.47
  m after three) and 1.3 m against a runner. Still short of a drink against a sprinter.

## 5. The scored term (`--disengage`)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py pool --opponents-from rank.json [--chaser run]`

`escape_value`: per opponent row of `er-mechanics-ashes.Opponents` (each family's opener, at its
use share) and per reaction-timed press (the `dodge_presses` rule section 16c uses), the string
must be evaded with the dodge taken away from the attacker (`Opponents._escapes`). The chaser
leaves at the later of his recovery (`end`) and the press plus his median reaction. The escaper
drinks after one, two or three dodges, whichever nets more, and only when the net is above 0:

    net = 810                    the chaser's first hit lands at or after f54 of the drink
        = 810 - his hit          it lands between f31 and f54
        = -his hit (not drunk)   it lands before f31

    heal attempts per string = HEAL_NEED_SHARE x mean landed hit / 810      (0.5 x 264 / 810 = 0.163)
    disengage HP per string  = attempts x (heal with the skill - heal with the medium roll)
    option HP               += p_second x disengage HP                       (the evade term's frame)

`HEAL_NEED_SHARE` 0.5 (INFERRED: a player wants back about half of what the fight's engagements
land, while flasks last).

Measured on the RL 150 ranking's opponents (`rank-neutral-int.json`, 868 rows, 95.3% of pool
builds matched; 63.8% of the strings are evaded by any dodge taken away, the rest are pressed too
late):

| dodge | sprinting chaser: traded / safe, heal per string | running chaser: traded / safe, heal per string | over the roll x attempts (run) |
|---|---|---|---|
| medium roll | 0 / 0, 0 | 0 / 0, 0 | 0 |
| light roll | 0 / 0, 0 | 6.6% / 0, 26.5 | +4.3 HP |
| Bloodhound's Step | 0 / 0, 0 | 9.6% / 0, 40.7 | +6.6 HP |
| Bloodhound's Step, back | 0 / 0, 0 | 9.8% / 0, 41.8 | +6.8 HP |
| Bloodhound's Step, hidden f5-10 | 0 / 0, 0 | 12.7% / 0, 55.4 | +9.0 HP |
| Quickstep | 0 / 0, 0 | 0.1% / 0, 0.3 | 0 |

No heal is ever safe: the drink's 54 frames are longer than any chaser needs from any separation
a dodge leaves.

### 5a. Validation (MEASURED 2026-09-30)

Same eight 2H rows as ashes-of-war.md 17e (Lance and Messmer Soldier's Spear, Heavy and Keen,
with and without the lightning grease, `--measure-all`, opponents from `rank-react.json`), rerun
on the current tree for all three columns:

| configuration | Spearman, mean of the eight 2H rows | Bloodhound's Step, Lance 2H Heavy+lightning |
|---|---|---|
| no disengage term | 0.656 | 462 (= moveset) |
| `--disengage sprint` | 0.656 | 462 (term 0) |
| `--disengage run` | 0.668 (Messmer 2H Keen 0.63 -> 0.73, the rest unchanged) | 469 (+3.8 HP per engagement) |

On the Lance 2H Heavy+lightning the step's option under `--disengage run`: evade term +0.19 HP,
disengage term 3.82 HP per engagement (p_second 0.614 x 6.2 HP per string; 0.160 heal attempts,
9.1% of strings end in a traded heal, none in a safe one). Quickstep: +0.03. Order of the nine
ashes on that row is unchanged (Flaming Strike 658, Braggart's Roar 516, then Bloodhound's Step
469 breaks its tie with Chilling Mist and Endure at 462).

Whole RL 150 ranking with `--interrupt`, current tree, with and without `--disengage run` (53
min, both in parallel): the term is present on 666 dodge-skill options (mean +2.5, largest +6.6
HP per engagement), but no row's score moves: a dodge option never becomes the row's best
option. The ash adoption coefficient is +0.031 (CI [-0.149, +0.182]) in both, within-class score
percentile +0.517 in both. Lance 2H 618 (rank 126), Messmer Soldier's Spear 2H 639 (90) either way.

So the term does not explain the step's 30 mounts. What the data leaves as the step's real edge
is section 2's stagger gate.

### 5b. The stagger gate against the pool's off-hands (MEASURED 2026-09-30)

The pool now carries each build's left hand (`er-mechanics-exchange.opponent_pool` 'offhand', a
build's left weapons sharing it equally; two-handed builds none), and `offhand_escapes` gives each
opponent row, per opener, the off-hand L1 #1 HP a dodge skill leaves before and a roll does not:

    per left weapon: share x opener stagger (ranking slot) x roll's landing chance (true 1, tie 0.5) x L1 HP
                     when the link's gap >= the skill's gate for its reaction level
    option HP += p_second x sum over rows (w x land x that HP)          (under --disengage)

L1 HP is the left weapon's own one-handed R1 #1 damage in the ranking scaled by the two motion
values (`INFERRED`: same AR in either hand).

Staves and seals (`wepType` 57, 61) are left out of a left hand that holds anything else: they
are cast from and switched off. The Frenzied Flame Seal's 264 RL 140-160 builds record a spell 7
times, Bestial Vitality each time (FTH requirement 12, the builds' median Faith 12; the faith
talisman swapped in to cast it is not in the saved loadout, 0 Marika's Soreseal).

On the RL 150 ranking (`rank-par-base2.json`): 0.1 HP per string, 4 of 868 opponent rows. The
pool's one-handed builds hold 689 build-shares of left hand: none (or a catalyst alone) 174,
Spiralhorn Shield 41, Hand Axe 25.5 (the most carried off-hand melee weapon; 18 before the
catalysts were dropped), Twinbird Kite Shield 19, Icon Shield 19, Buckler 16.5. Paired weapons
powerstance instead. So the gate the step has over the roll is real but almost never met in this
corpus: it does not explain the step's mounts either.

**Powerstance L1 (`Paired`, MEASURED 2026-09-30).** With a pair in hand (`left_mode` 'dual', 90.5
of the 945 pool builds' left-hand shares, against 219.3 off-hand melee) L1 is powerstance
(`COMMUNITY` `c0000.hks`): every right attack's onUpdate passes `W_AttackLeftLight1`, which
`ATTACK_REQUEST_DUAL_RIGHT` turns into `W_AttackDualLight1`, so a right opener chains into
powerstance L1 #1 at the same L1 window; `AttackDualLight<n>` sends L1 to `<n+1>` up to
`GetDualAttackMaxNumber` (3, 4 or 6 by category, `DUAL_MAX`); the movement powerstance attacks go
to L1 #2. A powerstance clip hits two or three times, and the skill only beats the roll at
DamageCount 1 (at 2+ the roll gate is at or before the skill's on every level), so a link out of a
clip is scored from its one breaking hit: per corpus poise value, hits accumulate poise damage and
a break resets it (`INFERRED`), and only strings with exactly one break count. The follow-up's
avoided HP is its first hit plus any hit inside the roll's DamageCount 2 gate.

Result on `rank-par-base2.json`: still 0.1 HP per string, the same 4 rows. Right opener ->
powerstance L1 #1 passes the gate on some pairs, but on none of the openers the pool's moveset
families throw (0.000 HP on the scored rows). Powerstance L1 #1 -> #2 never does: Treespear,
Lance and Messmer Soldier's Spear pairs link at gap 37 out of a large stagger, Sword of Light,
Coded Sword and Bandit's Curved Sword at 15-18 out of a small one, all past the roll's gate
(roll-out-able), so the roll already leaves them.

**The pool throwing L1 strings (`--paired-strings`, off in the ranking).** `paired_rows` gives
each attacking left hand an opponent row opened with its L1 #1 (off-hand or powerstance; the
clip's second hit as the chained one, reach and landing chance borrowed from an R1 #1,
`INFERRED`), and `er-mechanics-ashes.PAIRED_STRINGS` = p moves p of that left hand's share onto
it. 232 rows. Bloodhound's Step minus the medium roll per string (reference dodger, before
`p_second`): +0.34 HP at p 0, +0.21 at 0.25, +0.10 at 0.5 (evade -0.14, punish -0.06, stagger
escape -0.04 at 0.5; the L1-opened rows add 0.000 escape HP; mean opponent hit 425 -> 412). The
corpus records no button use, so p has no measured value; either way the L1 strings lower the
step's edge rather than explain its mounts. `er-mechanics-exchange.exchange` itself still throws
R1 #1 only (`OPPONENT_SLOT`); the L1 rows reach only the dodge, buff and disengage terms.

## 6. Not established

- Which roll clip belongs to which equip load, and what sets `actionAnimationFlags` bits 3-4
  (both L2 readings are measured).
- That a hit before the flask's f31 leaves the flask unused; the PvP scaling of flask healing.
- The chaser's lock-on choice and speed (both measured), his reaction to a step that hides the
  escaper, and instant acceleration to top speed.
- Terrain, second opponents, and stamina (the chaser has just attacked).
- Whether a press held through a stagger comes out at the gate.
- How often players open with L1 (`--paired-strings`), whether a hit landing inside a stagger
  without breaking poise again keeps DamageCount, and whether a break resets accumulated poise
  (section 5b's powerstance model).

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py tools
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py gates
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py flask --level 12
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py race --chaser sprint [--hidden]
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py pool --opponents-from rank.json --chaser run
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py pool --opponents-from rank.json --paired-strings 0.25 0.5   # ~5 min
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-disengage.py cross-hand     # ~30 s
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --weapon "Lance,Messmer Soldier's Spear" \
    --sort score --json --measure-all --opponents-from rank.json --disengage run
```
