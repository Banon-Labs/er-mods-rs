# Moveset score: a weapon by the attacks a player opens with (RL 150 PvP)

Labels as in the other files here: **VERIFIED** = regulation value or EXE code, **TAE** = decoded
TimeAct, **COMMUNITY** = outside claim (here Smithbox's decompiled `c0000.hks`), **MEASURED** =
computed by the commands at the end from the regulation and the corpus mirror, **INFERRED** = a
modelling choice, or a reading whose consumer was not traced. Nothing was launched.

Tool: `scripts/er-mechanics-moveset.py` (`--selftest`: 24 checks). It reads an
`er-builds-pvp.py --json` result and scores it with that tool's own `slot_score` and
`entry_frames`; it does not edit `er-builds-pvp.py`. Jumps: `scripts/er-mechanics-jump.py`
(`--selftest`: 18 checks), section 6.

## Answer

- `er-builds-pvp.py --sort score` ranks a weapon by `best_slot`, the maximum `slot_score` over
  every slot but jumps and the guard counter. In 29 of 324 RL 150 rows that maximum is a slot
  nobody can throw from neutral: R1 #3 (20), R1 #2 (4), R1 #5 (4), R1 #4 (1) (MEASURED).
- The moveset score scores openers only. It takes the best engagement per family (R1, R2,
  movement attack) and averages the families with use shares proportional to their score (the
  matching law). A slow R2 now costs a weapon about a third of its weight.
- True-combo damage is folded in, but it changes nothing at RL 150. None of the 324 rows'
  families takes a follow-up. The frame-advantage module reported 95 R2 #1 -> R2 #2 links as true
  or tie on a broken poise, and it leaves out the chained R2 #2's release lead-in. With the
  lead-in 4 remain, and none of those raises the engagement's score (MEASURED; the lead-in path
  is COMMUNITY HKS).
- Powerstance can be scored with the existing attack extraction, and it is implemented for
  same-weapon pairs (153 of the 162 1H rows). No dual L1 chain link is a true combo (398 of 398
  `no`).
- Effect on the ranking: Giant-Crusher stays first. The Greatsword moves from row 22 to 9 and
  from weapon 30 to 10. The weapon score's Spearman rho with primary-weapon adoption rises from
  +0.163 to +0.195 (MEASURED). That gain is inside the noise of 162 weapons: the standard error
  is about 0.08.

- Jump attacks are now a family, timed from the jump input (section 6): the swing can start 6
  frames after the jump, so a jump attack hits 6 frames later and commits 6 frames longer than
  its landing clip alone, and reaches 1.2 / 3.0 / 5.0 m farther (standing / running / sprinting
  jump). On the 2026-09-30 ranking the running jump lifts colossal and great weapons
  (Giant-Crusher 94 -> 39, Greatsword 126 -> 67) and pushes knives and daggers down (6d).

## 1. The model

Every factor inside one slot is `er-builds-pvp.slot_score`, unchanged. What is new is what gets
aggregated.

### 1a. Openers and families (INFERRED grouping)

| family | openers | within the family |
|---|---|---|
| `r1` | `r1_1` | its engagement |
| `r2` | `r2_1` (uncharged, lead-in included), `r2_1c` | the better engagement |
| `move` | `run_r1`, `run_r2`, `roll_r1`, `bstep_r1`, `crouch_r1` (each pays `SCORE_ENTRY_FRAMES`) | the best engagement |
| `jump` | `jump_r1_{n,f,d}`, `jump_r2_{n,f,d}`: standing, running and sprinting jump, timed from the jump input (section 6) | the best engagement |
| `l1` (powerstance) | `dual_1` | its engagement |
| `move` (powerstance adds) | `dual_dash`, `dual_roll`, `dual_crouch`, `dual_bstep` (entry as their right-hand twins) | the best engagement |
| `jump` (powerstance adds) | `dual_jump_{n,f,d}` | the best engagement |

R1 #2.., R2 #2 and the charged R2 #2 enter only as follow-ups of an opener. The guard counter
stays out: it needs a blocked hit first.

Within a family the player picks the best variant of one input. Across families the use share
follows the matching law, share proportional to `score ** a`. The weapon row's score is then
`sum(s ** (1 + a)) / sum(s ** a)`: `a = 0` is the plain mean and `a -> inf` the old maximum
(`MATCHING_EXP` = 1, strict matching). Herrnstein's matching law is a published regularity of
choice behaviour in general (COMMUNITY). That players of this game follow it is INFERRED.
Section 3 measures `a` against adoption.

### 1b. Engagement: an opener plus its true combos

`chain_links` walks the first `combos` entry of each slot, the same walk
`er-builds-pvp.Mechanics.slots.chain` uses for status. Each link lands with
`er-mechanics-status.combo_land`: the `on_intact` verdict with poise holding and `on_break` with
it broken, weighted by the stagger share of the hit before it (true 1, tie 0.5, no 0).

`engagement` scores every depth and keeps the best one. At depth d:

- `dmg` = opener + the sum over links of (the chance it and every earlier link land) x the link's
  `dmg`.
- Commitment runs from the opener's start to the last link's first free roll or button frame:
  `entry + sum(next of each slot before it) + min(roll, next)` of the last link.
- Status HP = the opener's `hp_per_hit` (which is per landed use,
  `er-mechanics-status.status_expected`) x the expected landed uses.
- The frame-advantage term is the last link's expected advantage when every link lands. When one
  misses it is `MISS_ADVANTAGE` = -30, the floor of the score's clamp (INFERRED: a defender who
  escaped acts first).
- Reach, parry and stagger are the opener's.

Depth 0 is the opener's own `slot_score` exactly (selftest). A player who presses a follow-up
that loses rate would not press it, so an engagement never scores below its opener.

### 1c. The chained R2 #2 pays the release lead-in (COMMUNITY decompile, TAE)

`AttackRightHeavy1End_onUpdate` (Smithbox `c0000.hks` line 7778) sends an R2 press to
`W_AttackRightHeavy2Start`. `AttackRightHeavy2Start_onUpdate` (line 7788) moves on to
`W_AttackRightHeavy2End` only once R2 is up and `TAE_FLAG_CHARGING` or SpEffect 100280 is set.
The chained R2 #2 therefore plays its charge-start clip up to the release, the same lead-in
`er-builds-pvp.slot_hit` already adds to an R2 #2's startup.

`er-mechanics-frame-advantage.combo` used to measure the gap to the R2 #2's first hit in its
release clip without that lead-in, which runs 5-17 frames on the rows checked (Greatsword 2H:
14.2). It now adds R2 #2's `release_lead_in` to the gap itself (docs/er-mechanics/frame-advantage.md),
and this module reads its verdict as given; `check_lead_ins` refuses an `er-builds-pvp.py --json`
file made before that fix. The lead-in ends at SpEffect 100280's first frame: `TAE_FLAG_CHARGING`
is set only by TAE event 600, which no player R2 clip carries (VERIFIED, 1.16.2 image).

| RL 150, 324 rows (MEASURED) | links |
|---|---|
| R2 #1 -> R2 #2 true or tie on break, without the lead-in | 93 |
| true or tie with the lead-in | 2 (Raptor Talons 2H true, 24.9 against 25; 1H tie) |
| engagements whose best depth takes a follow-up | 0 of 970 row families |
| R1 chain links true or tie on either side | 2 ties (R1 #2 -> #3 on break), 0 true (822-row re-measure: section 1e) |
| dual L1 chain links (powerstance), intact / break | 398 `no` / `no` |

So at RL 150 the combo fold is a mechanism with nothing to fold. It will matter when a weapon's
follow-up lands inside the stagger, and the four near misses show how close some R2s come.

### 1e. Why every engagement is depth 0 (MEASURED, 822 rows, 2026-09-29)

Re-measured on the 822-row ranking output (`er-builds-pvp.py --json`, R2 lead-in included). All
2438 family engagements (`r1` 820, `r2` 798, `move` 820) stop at depth 0. That is what the frame data says, not a fault in `engagement`.

Link verdicts, `on_break` / `on_intact`:

| link | true | tie | no | no verdict |
|---|---|---|---|---|
| R1 #1 -> #2 | 0 / 0 | 0 / 0 | 817 / 817 | 3 |
| R1 #n -> #n+1, n >= 2 | 0 / 0 | 5 / 0 | 1928 / 1933 | 3 |
| R2 #1 -> #2 | 1 / 0 | 1 / 0 | 789 / 791 | 13 |

Cause, in order:

1. No R1 link is true because the roll gate of the first stagger opens before the next R1 hits.
   With `DamageCount` 1 the roll needs EzState flag 2, which opens on frame 10 of every small and
   minimum clip, 25 of every middle and 35 of every large (TAE, `a00.tae`, the same in all 20
   hit-direction clips of each level; `er-mechanics-frame-advantage.md` sections 2-3). The R1 gap
   (first hit to next first hit) is 12-48 frames against escape 10 (1876 links, median 18.8),
   25-45 against 25 (724, median 30) and 37-42 against 35 (150). Every R1 #1 -> #2 misses by at
   least 2 frames. The five ties are R1 #2 -> #3 at gap 25 against a middle stagger. A poise
   intact hit is level 0 (additive flinch), which does not lock the defender at all, so the
   `on_intact` side is `no` everywhere.
2. `chain_links` stops at the first link whose landing chance is 0, so a `no` on both sides ends
   the chain before `engagement` scores anything.
3. The two R2 links are both Raptor Talons. 1H: tie (gap 25 against 25), stagger share 1.0, so p
   = 0.5. 2H: true (24.9 against 25), stagger share 0.76, so p = 0.76. Depth 1 scores 337 against
   the opener's 597 (1H) and 850 against 1175 (2H). The follow-up doubles the commitment (27.5 ->
   56.5 frames) while adding only p x its damage, and the expected advantage falls to the miss
   branch's -30.

The stagger weighting is not what zeros the links: 626 of 820 R1 #1 slots already have a stagger
share of 1.0 (and only 2 have 0). The weighting multiplies the `on_break` chance by that share,
and that chance is 0.

The scoring rule can prefer depth > 0. Counterfactual on the same rows, every R1 `on_break`
verdict forced to `true`: 164 of 820 R1 engagements take a follow-up (138 depth 1, 26 depth 2);
forced to `tie` (p = 0.5): none. A landed link adds its damage and its frames at about the
opener's own rate, so a sure link wins only when its damage per frame beats the opener's, and a
link below certainty always loses. That is the rule of 1b (a player does not press a follow-up
that loses rate). It follows from `slot_score` measuring damage per committed frame of an opener
that is assumed to land: nothing in it charges the cost of winning neutral again, which is what a
true combo saves. That cost is not modelled anywhere in `er-builds-pvp.slot_score` (INFERRED gap,
outside this module).

What would move the R1 verdicts is the reaction start delay, which was never measured
(frame-advantage.md section 9): true R1 links by frames between the hit and the reaction clip's
first frame, 0: 0, 1: 5, 2: 8, 3: 212, 4: 526, 5: 709. An outside claim that many R1 strings are
true on a poise break would need a delay of 3 or more (hitstop that runs the defender's clock
but not the attacker's would act the same way) or a later roll gate than the 1.08.1 decompile
shows. Neither is established, so the verdicts stand. Links after the first are judged at
`DamageCount` 1; a second break would open the gate earlier (small 7, middle 10, large 15), so
those verdicts are, if anything, generous.

### 1d. Grips (MEASURED share, INFERRED use)

Each weapon has a 1H and a 2H sweep row, each with the stats the sweep optimised for that grip.
`grip_blend` weights the two rows' moveset scores by the share of the window's PvP builds that
carry the weapon as primary and set `is2h`:

- Corpus: RL 140-160, not PvE, deduplicated as in `er-builds-adoption-gap.corpus`, 992 builds.
  26% of them set `is2h`.
- A weapon's share is shrunk toward its `wepType`'s share by `GRIP_PRIOR_BUILDS` = 5
  pseudo-builds (INFERRED).
- Examples: Greatsword 46%, Giant-Crusher 48%, Warped Axe 29%.

`is2h` is one flag per build, so reading it as the share of time a player spends two-handing is
INFERRED. Each grip also keeps its own build, so the blend describes the weapon's players rather
than one character. When one grip is missing, the other row stands alone.

## 2. Powerstance: feasible with the existing extraction

The attack extraction can score powerstance movesets once each hit is resolved to its hand.

- The `AttackDualWield_SM` clips 0340x0 / 034200 / 034300 / 034310 / 034400 (TAE, see
  powerstance-guard.md section 1) carry AttackBehavior events with judges 800-899.
- The Source byte names the hand (VERIFIED, `CSChrTaeAnimEvent::AttackBehavior` 0x1404266d0).
- Nothing in `attack_numbers`, `slot_hit` or the frame-advantage functions assumes a slot has
  only one weapon.

`dual_attacks` resolves each clip the way `er-mechanics-attacks.tae_details` resolves an attack.
`er-mechanics-powerstance-guard.powerstance_rows` does not do these steps, and it reads clip time:

- It resolves the TAE file through `motion_category` (spAtkcategory first) and imports through
  `resolve_events`.
- It applies TAE 608 play speed (`clip_to_real`). Greatsword L1 #1 plays at 1.2x over its first
  0.4 s: next L1 43 -> 41 and roll 50 -> 48 real frames.
- It skips state-gated hitboxes (Args+0xe) and counts only hits that open a fresh record and are
  not a takeover (`hit_records`, the same rule as `own_sweep_hits`).
- The next L1 comes from powerstance-guard's `_l1_recovery` pairing, carried to real time. The
  roll, R1, R2, guard and move frames come from `recovery_details`.

`DualScorer` turns those rows into `er-builds-pvp` slot dicts:

- Each hit goes through `slot_hit` with the row's 1H AR, and the damage is summed.
- PvP poise is summed over the hits. The stagger share is the defenders below that sum (INFERRED:
  poise damage accumulates within the few frames between the two hands).
- Advantage and reaction are read from the last hit (`fa.reaction_level`, `fa.advantage`), and
  dual L1 #n -> #n+1 links use `fa.combo`, measured from that last hit.
- Status goes through `status_expected`, with the second hand's hit as an extra hitbox of the same
  use.
- Parry exposure is JumpTable 5 in the resolved clip, as `er-mechanics-crits.parry_exposure`
  reads it: 1009 of 1054 dual slots are parryable.

Scope and gaps:

| item | state |
|---|---|
| Pairs | Same weapon in both hands (`can_powerstance(w, w)`): 153 of the 162 1H RL 150 rows. A mixed pair needs the left weapon's own AR and build and is not in the sweep; `dual_attacks` resolves every hit against one weapon id. |
| Grease on the left-hand hits | Not added. The goods applies the right-hand SpEffect row (grease.md section 1). Whether a dual judge's hit reads it through the BehaviorParam category mask (grease.md section 3b) is not traced: INFERRED, conservative. |
| Reach | The reach module poses right-hand hitboxes only. A dual slot takes the row's 1H R1 #1 reach (INFERRED proxy). |
| Crouch | `dual_crouch` plays what the behavior script plays (section 8d): 034310 for categories 23, 24, 27, 28, 36, 37, 58, the rolling L1 034300 for every other pair. Its reach, coverage, reaction dodge and exchange are measured on that clip (`measure_dual_slot`). |
| Weight | The second copy's weight is not charged. `er-builds-pvp.py` has no equip-load term at all (adoption-gap.md section 3, END floor). |
| Jump L1 | Extracted from its landed clip (034570) and timed from the jump input like the right-hand jumps (section 6). Its reach is the R1 #1 proxy plus the jump's travel. |

Effect: a powerstance loadout gets the 1H R1/R2/movement families plus `l1` and the dual
movement attacks. At RL 150 it scores above the same weapon's 1H row for 140 of 153 weapons.
The 13 below are mostly axes and clubs whose 1H R1/R2 already outscore their L1 (Warped Axe 1078
-> 1055, Hand Axe 1000 -> 958); adding a weaker family lowers a matching mean. Examples:
Giant-Crusher 1001 -> 1157, Greatsword 847 -> 934. `dual_1` is the best single engagement on 121
of the 153 rows, `r1_1` on 18 and `r2_1` on 14.

## 3. Usage weighting against adoption (MEASURED)

There is no match log. The corpus records only what players carry, and the planner builds have
no free text (173 of 5699 names are longer than 40 characters, and nothing describes attacks).
The one measurable check on a usage weighting is how well the resulting weapon score ranks
adoption. Adoption here is primary-weapon counts over the same 992 builds, across 162 sweep
weapons, 48 of them adopted. Spearman rho by matching exponent, grips blended as in 1d:

| weapon score | all sweep weapons | adopted only |
|---|---|---|
| best slot (old), grips blended the same way | +0.163 | +0.173 |
| moveset, a = 0 (mean over families) | +0.209 | +0.162 |
| a = 0.5 | +0.205 | +0.163 |
| a = 1 (default) | +0.195 | +0.162 |
| a = 2 | +0.177 | +0.163 |
| a = 4 | +0.152 | +0.151 |
| a = 8 | +0.153 | +0.159 |
| a = inf (max over families) | +0.152 | +0.158 |

Every a at or below 2 ranks adoption better than the old maximum. Across the adopted weapons the
curve is flat. The spread between exponents (0.06) is below one standard error (about 0.08 at
n = 162), so adoption cannot choose a. `MATCHING_EXP` stays at 1, strict matching, which is the
law's own point value, and the choice is INFERRED.

## 4. Effect on the RL 150 ranking (MEASURED, sweep `grease-sweep-dlc-drawstring-150-200`)

Rows (weapon x grip), top 12. `old` is the best single slot's score, with its old rank in
brackets.

| # | row | moveset | old | families (best opener, engagement score, use share) |
|---|---|---|---|---|
| 1 | Giant-Crusher 2H | 1438 | 1706 (1) | r1_1 1706 41%, r2_1c 1335 32%, run_r2 1161 28% |
| 2 | Prelate's Inferno Crozier 2H | 1339 | 1620 (2) | r1_1 1620, r2_1c 1167, run_r2 1108 |
| 3 | Watchdog's Greatsword 2H | 1336 | 1578 (4) | r1_1 1288, r2_1 1578, run_r2 1028 |
| 4 | Golem's Halberd 2H | 1290 | 1578 (3) | |
| 5 | Warped Axe 2H | 1280 | 1429 (12) | |
| 6 | Duelist Greataxe 2H | 1274 | 1481 (8) | |
| 7 | Troll's Golden Sword 2H | 1260 | 1490 (7) | |
| 8 | Bloodfiend's Arm 2H | 1243 | 1440 (9) | |
| 9 | Greatsword 2H | 1238 | 1330 (22) | r1_1 1330 36%, r2_1 1306 36%, crouch_r1 1033 28% |
| 10 | Rotten Greataxe 2H | 1236 | 1435 (10) | |
| 11 | Jawbone Axe 2H | 1236 | 1388 (15) | |
| 12 | Star Fist 2H | 1233 | 1526 (5) | r1_1 1152, r2_1 1526, run_r2 774 |

The Greatsword rises because its three families are close to each other. Star Fist and Iron Ball
fall because one strong R2 carried them.

Weapons (grips blended): Giant-Crusher 1 (old 1), Warped Axe 2 (4), Prelate's Inferno Crozier 3
(3), Duelist Greataxe 4 (10), Jawbone Axe 5 (11), ..., Greatsword 10 (30, adoption 57).

Giant-Crusher against its twin (giant-crusher-adoption-gap.md):

- The slow uncharged R2 is now charged, but the charged R2 carries the family: 2H `r2_1` 1209,
  `r2_1c` 1335, against the Greatsword's `r2_1` 1306.
- Giant-Crusher keeps a 200-point lead. What still separates the twins in adoption is outside the
  score: 3.5 more weight, the reach and swing shape the model does not read, and taste. Those are
  items 2-4 of that file's section 5.

## 5. Open

- Whether the behavior script sees SpEffect 100280 on the frame it is applied or the next; one
  frame either way moves the two remaining R2 links (1c).
- The matching exponent is not identified by adoption (section 3).
- Grip share is a build flag used as a time share, and each grip keeps its own build (1d).
  Scoring the other grip with the same build needs `slot_hit` and `Mechanics.slots` run twice per
  row in `er-builds-pvp.py`.
- Powerstance: mixed pairs, left-hand grease, dual reach, the second weapon's weight (section 2).
- Movement-attack entry for sprinting is still the INFERRED 20 frames of `SCORE_ENTRY_FRAMES`.
- Jumps: the landed clip continuing the air clip's clock, the fall after the jump clip, the
  exchange and reaction-dodge factors that still count from the landed clip (section 6c).

## 6. Jumps

Tool: `scripts/er-mechanics-jump.py` (`--weapon <name>` prints the sequence per grip, button and
jump; `--selftest` 18 checks). Labels: **HKS** = the installed compiled `c0000.hks` read with
`scripts/er-hks-disasm.py` (1.17.1 bytecode; line numbers are its own debug info), **BEHAVIOR** =
`c0000.behbnd` through `scripts/hkx-tagfile.py`.

The sweep's `jump_r1` / `jump_r2` slots are the landed clip `Jump_LandAttack_*` (031070 /
031270, 2H 033070 / 033270), timed from that clip's first frame. That is why they were left out:
a jump's airtime came before it. They now enter as the whole sequence.

### 6a. The sequence

| step | what the data says | label |
|---|---|---|
| Which jump | `ExecJump` (lines 3584-3587): `W_Jump_D` when `LocomotionState` 1 and `MoveSpeedIndex` 2 (sprint), `W_Jump_F` when `MoveSpeedLevel` > 0.6 (running), else `W_Jump_N`. Some SpEffects (503520, 5520, 425, 4100, 4101, 19670) force N. | HKS |
| Jump clip | The lower layer of the state: N `a000_202000` (stick at rest) or `202010` (stick forward), F `202020`, D `202030` (and back/side variants). | BEHAVIOR |
| Air attack | `JumpCommonFunction` (lines 23560-23584): while SpEffect 140 is on (`env(1116, 140)`) and `JumpAttackForm` is 0, an R1 / R2 / L1 request fires `Event_JumpNormalAttack_Add` and sets the form to 1 / 2 / 3. Env 1116 is `GetSpEffectID`: `AttackRightHeavy2Start_onUpdate` asks it for 100280, the decompile's `GetSpEffectID(100280)`. | HKS |
| Press window | The jump clips put SpEffect 140 on frames 0-17 (N), 0-19 (F), 0-16 (D), and open the attack's cancel id (JumpTable 4 for R1/R2, 117 for L1) from frame 6. The request is ready only while its cancel id is open (attacks.md section 4), so the earliest swing starts 6 frames after the jump input. That a press made before frame 6 is held until then is INFERRED. | TAE |
| Swing | The attack is the upper layer of the same state. Per hand and button a selector holds the air clip (031030 / 031040 / 031050 for N / F / D; R2 0312x0; powerstance 0345x0) and the landed clip (031070 ...). The air clip's frame 0 is an airborne pose (pelvis 0.93 m, legs tucked); the landed clip's frame 3 is a landing crouch (pelvis 0.51 m). So the swing's clock starts at the press, not at the jump input. | BEHAVIOR, MEASURED |
| One swing, two versions | Air and landed clips raise and swing on the same frames: over every player TAE, 453 of 476 pairs open their first hit within one frame, all within three (a025's R2 air clip is three frames early). The air hit window runs on through the descent (a020 R2: 13-31 against 13-16). The weapon bone follows the same raise-and-swing path in both (a026). | TAE, MEASURED |
| Landing | `JumpCommonFunction` (lines 23641-23689): on ground contact (env 248, the same env `Act_Jump` pairs with the fall-death check) with SpEffect 140 off, a jump in attack form sets `JumpAttack_Land` (N: the selector switches to the landed clip) or fires `W_Jump_Attack_Land_F` (F, D). The landed clip is taken to continue the air clip's clock. | HKS; clock INFERRED |
| Air phase | The jump clip carries the rise and fall as root motion with gravity off (JumpTable 27, frames 0-24): peak 1.13-1.17 m at frame 15. It ends above the ground (N 0.45 m, F 0.24 m, D 0.48 m); the rest of the fall runs at its last speed, landing at 26.2 (N), 25.1 (F), 23.0 (D). The fall after the clip was not read. | MEASURED; fall INFERRED |
| Travel | The air clip has no root motion, the jump clip carries it: N 1.41 m to the landing (0 with the stick at rest), F 3.30 m, D 5.12 m. The landed clip adds its own from the frame it takes over (about 0.2 m after an air hit). | MEASURED |
| Jump invincibility | The jump clips hold JumpTable 132 for their whole length. It sets `actionModifiersFlags` bit 4 (case 0x1404285c6, `or qword [rbx+0x40], 0x10`), and `ChrIns::IsImmuneToAttack` 0x1403f3b90 makes the character immune while it is set only to attacks whose AtkParam has `isInvalidatedByNoDamageInAir` (73 of 11017 `AtkParam_Pc` rows: shockwave skills such as Ground Slam, Earthshaker, Hoarfrost Stomp, some colossal and warhammer rows). It is not lower-body invulnerability. Whether it stays on once the attack layer plays was not traced. Not scored. | VERIFIED (1.16.2), row count MEASURED |
| Hyperarmor | Event 795 in the air clip and the landed clip, on the swing's clock (Greatsword 1H R1: air clip a026_031030 8-23, landed 7-24): from the jump input press + those frames. The takeoff before the press has none. | TAE |

So, from the jump input: first hit = 6 + the air clip's first hit if that comes before the landing,
else 6 + the landed clip's; recovery = 6 + the landed clip's roll or next-attack frame, but never
before the landing (the air clips carry no roll or attack cancel id; INFERRED that nothing else
ends the state in the air). Where the landing holds a recovery back, the frame advantages lose
the same frames.

### 6b. Five weapons (frames at 30 fps from the jump input; `m@hit` = travel to the first hit)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-jump.py --weapon <name>`. The
standing (N) and sprinting (D) jumps hit on the same frame as the running (F) one unless the
sprint lands first; only travel changes (N 1.2 m, D 5.0 m at the hit).

| weapon | grip | button | first hit | recovery (roll / next) | running jump: m@hit, total |
|---|---|---|---|---|---|
| Giant-Crusher | 1H | R1 | 22 | 40 | 3.02, 3.30 |
| Giant-Crusher | 1H | R2 | 25 | 54 | 3.29, 3.47 |
| Giant-Crusher | 2H | R1 | 22 | 42 | 3.02, 3.30 |
| Giant-Crusher | 2H | R2 | 25 | 50 | 3.29, 3.48 |
| Greatsword | 1H | R1 | 22 | 43 | 3.02, 3.30 |
| Greatsword | 1H | R2 | 24 | 53 | 3.20, 3.46 |
| Greatsword | 2H | R1 | 23 | 41 | 3.11, 3.30 |
| Greatsword | 2H | R2 | 24 | 52 | 3.20, 3.51 |
| Claymore | 1H | R1 | 23 | 36 | 3.11, 3.30 |
| Claymore | 1H | R2 | 23 (air clip 17; landed 20) | 52 | 3.11, 3.61 |
| Claymore | 2H | R1 | 22 | 33 | 3.02, 3.30 |
| Claymore | 2H | R2 | 23 | 51 | 3.11, 3.61 |
| Lance | 1H / 2H | R1 | 18 | 35 | 2.49, 3.33 |
| Lance | 1H / 2H | R2 | 21 | 49 | 2.91, 3.39 |
| Dane's Footwork | 1H | R1 | 17 | 25.1 (next R1 at 24 is held to the landing) | 2.35, 3.30 |
| Dane's Footwork | 1H | R2 | 22 | 37 | 3.02, 3.45 |
| Dane's Footwork | 2H | R1 | 16 (air clip 10) | 30 | 2.20, 3.38 |
| Dane's Footwork | 2H | R2 | 17 (air clip 11) | 39 | 2.35, 3.87 |

Against the landed-clip slot the sweep had, a jump attack is 6 frames later to hit and 6 frames
longer to commit, and reaches the jump's travel farther.

### 6c. What the score takes, and what it does not

`with_jumps` (moveset) turns each sweep slot into three openers through
`er-mechanics-jump.jump_slots`: entry = 6 (plus the sprint's INFERRED 20 for D, the running
attacks' `SCORE_ENTRY_FRAMES` value), reach = the slot's world reach less the landed clip's root
motion at the hit (the swing's reach from the body) plus the jump's travel to the hit. Damage,
poise, stagger, frame advantage, status and parry come from the slot unchanged (the air and
landed clips fire the same AtkParam row). Jump attacks have no JumpTable 5, so they are not
parryable (crits.md).

Not corrected, because the numbers live in `er-builds-pvp.py`, which this work does not edit:

- The exchange factor (`er-mechanics-exchange.slot_exchange`) and the reaction dodge
  (`Mechanics.slots`, `slot_reaction`) count the jump slots from the landed clip's first frame,
  so they miss the 6-frame takeoff. `slot_exchange` already shifts the strike frame and the
  hyperarmor windows by the entry it is given; it is given `entry_frames(key)`, which is 0 for
  the jump slots. The proposed change:

  ```diff
   SCORE_ENTRY_FRAMES = {"roll_r1": 20.0, "bstep_r1": 14.0, "crouch_r1": 8.0, "run_r1": 20.0,
  -                      "run_r2": 20.0}
  +                      "run_r2": 20.0, "jump_r1": 6.0, "jump_r2": 6.0}
  ```

  with a comment line above it: `#: A jump attack's swing starts at the press, 6 frames after the
  jump input at the earliest (er-mechanics-jump.takeoff, TAE).` The moveset's jump openers use
  their own `jump_entry` and keys (`jump_r1_f`, ...), so this does not charge the takeoff twice;
  it moves the exchange's strike frame and the sweep's own `score` for the `jump_*` slots. The
  sprint jump's extra 20 frames would still be missing from its exchange.
- The reaction dodge would also need the entry added to the times it reads (`roll - lead`,
  `first - lead` in `Mechanics.slots`); not proposed as a line diff, since the whole block reads
  the landed clip's contact samples.

### 6d. Effect on the RL 150 ranking

MEASURED with `er-mechanics-moveset.py --pvp <ranking json> --jump-effect`: the grip-blended
moveset score of every weapon with and without the `jump` family, over the same slots (so the
comparison isolates the family). Ranking json: `er-builds-pvp.py --rl 150 --sort score --json` run
2026-09-30 on the tree as it stood (411 weapons; that tool's scoring was being changed at the
same time, so its absolute scores are about 0.4x the 2026-09-29 run's).

- The jump family's best opener is the running jump (`jump_r1_f` or `jump_r2_f`) on every row
  shown: its 3 m of travel lifts the reach factor, and the 6-frame takeoff costs less than that.
- Largest gains: colossal and great weapons whose running jump outscores their other families
  (Greatsword 126 -> 67, Guardian's Swordspear 184 -> 128, Giant-Crusher 94 -> 39, Prelate's
  Inferno Crozier 148 -> 94, Watchdog's Greatsword 134 -> 90, Golden Halberd 264 -> 222).
- Largest losses: knives, daggers and fast one-handers whose jump engagement scores well below
  their R1, so it pulls the matching mean down (Crystal Knife 174 -> 218, Golem Fist 72 -> 114,
  Smithscript Axe 88 -> 130, Great Knife 60 -> 93, Black Knife 192 -> 225).
- The five named weapons: Giant-Crusher 94 -> 39, Greatsword 126 -> 67, Claymore 97 -> 92, Lance
  259 -> 246, Dane's Footwork stays 1 (its jump family is its weakest, 11% use share).
- On the 2026-09-29 ranking json the same comparison put thrusting shields and short thrusting
  swords at the top instead (Dueling Shield 185 -> 55, Carian Thrusting Shield 169 -> 40); which
  weapons gain depends on the other factors of `slot_score`, not on the jump data.

These numbers still carry the sweep's exchange and reaction-dodge factors counted from the landed
clip (6c), which flatter the jump by the 6-frame takeoff.

### 6e. The jump openers as an opponent throws them (2026-10-01)

The families are also what the opponents throw: the skill term's dodge table
(`er-mechanics-ashes.opponents_from_results`, ashes-of-war.md section 16c) and, with
`--opponent-pool families`, the exchange and neutral contests (neutral.md section 6). Both read
the jump openers through `er-builds-pvp.jump_openers`, which computes each one's `neutral_in`
(strike at 2.5 m counted from the jump input, travel-inclusive reach, hyperarmor windows moved to
the same clock) whether or not a neutral pool is given. Before 2026-10-01 the dodge table looked
the jump openers up in the stored slots, found none, and dropped the family (er-effects-rs-8uha).

## 7. Relative speed within a class (2026-09-30)

The rule under test: the fastest weapon of a class is often the one ranked highest, more so when
its lead over the rest of the class is large. The model already rewards absolute speed inside
`slot_score` (commitment, the startup contest, the reaction dodge). This section asks whether
speed relative to the class adds anything.

### 7a. Test (MEASURED, `scripts/er-builds-speed-adoption.py`, RL 140-160 PvP corpus)

Class = `wepType`. Main hand: the 1H R1 #1's first active frame against primary adoption. Off-hand:
the off-hand L1 #1's first active frame against off-hand use (`er-mechanics-offhand.py`). Classes
with at least 3 weapons and some adoption.

| | main hand | off-hand |
|---|---|---|
| classes | 30 | 25 |
| within-class Spearman(-startup, adoption), mean / adoption-weighted | 0.059 / 0.151 | 0.139 / 0.162 |
| fastest weapon(s) among the most used | 24 of 30 | 21 of 25 |
| fastest weapons' adoption share vs their share of the class's weapons | 0.807 vs 0.815 | 0.826 vs 0.794 |
| classes with a single fastest weapon | 2 (most used in 0) | 2 (most used in 0) |
| Spearman(gap to second-fastest, excess adoption share of the fastest) | 0.101 | 0.059 |
| log adoption share per frame behind the fastest, bootstrap CI over classes | +0.008 [-0.120, +0.255] | +0.029 [-0.355, +0.295] |

- **First-frame startup barely varies within a class.** Weapons of a class share their R1 and L1
  animations, so in 28 of 30 main-hand classes the fastest startup is tied between several
  weapons. Often it is tied by most of the class, which is why "the fastest is among the most
  used" holds in 24 of 30 classes: the tie usually includes the most used weapon.
- **The fastest weapons get no more than their share of adoption** (0.807 vs 0.815). Adoption does
  not fall with frames behind the class's fastest (slope +0.008 per frame, CI spanning 0), and the
  gap does not predict the leader's excess share (0.10 / 0.06).
- So the corpus does not support the rule as stated, with startup measured as the first active
  frame. The shape of the term cannot be read from this data: the fitted slope is 0, which would
  make the term a no-op. The value used below was chosen by hand, not from the corpus.

### 7b. The term (`--relative-speed TAU`, `er-builds-pvp.apply_relative_speed`)

```
f_speed = exp(-(startup - fastest startup of the class) / TAU)
```

- One parameter, TAU, in frames.
- It is 1 for the class's fastest weapon, and a leader's advantage over the next weapon grows with
  the gap.
- It multiplies each row's final score (skill term included), with the class taken per grip. The
  absolute speed inside `slot_score` is unchanged.
- `er-mechanics-offhand.py --relative-speed TAU` applies the same function to the off-hand L1.
- The factor is applied after all scoring, so `er-builds-speed-adoption.py --apply TAU` on an
  existing ranking gives the same rows as a full `--relative-speed` run.

### 7c. Effect, TAU = 2 (INFERRED value), RL 150

| check | off | TAU 2 | TAU 1 |
|---|---|---|---|
| within-class score percentile coefficient (`er-mechanics-ashes.py check`) | +0.546 [+0.272, +0.799] | +0.527 [+0.253, +0.760] | +0.523 [+0.249, +0.755] |
| ash coefficient | +0.014 | +0.039 | +0.041 |
| off-hand Spearman(score, use), all 324 / the 104 used | 0.136 / 0.065 | 0.229 / 0.039 | 0.245 / 0.046 |
| great-spear ash Spearman | unchanged | unchanged | unchanged |

- The great-spear ash Spearman is unchanged by construction. `ashrank` compares a weapon's own
  ash option scores, and the factor multiplies all of them alike.
- **Off-hand axes.** The factor moves the axes as the user expects: the hatchets (L1 first hit
  11.9) keep factor 1, and the 13-frame axes get 0.58.

  | axe | overall rank, off | overall rank, TAU 2 |
  |---|---|---|
  | Hand Axe | 34 (507.2) | 28 (507.2) |
  | Rosus' Axe | 2 (637.9) | 77 (368.0) |
  | Battle Axe | 48 (465.2) | 157 (268.4) |

- **Main ranking, biggest movers.** The drops come from slower sub-families inside a class:
  the halberd class's fastest R1 #1 is 16 (Halberd, Banished Knight's Halberd), so the glaive
  moveset at 19 gets 0.22.

  | row | rank, off | rank, TAU 2 |
  |---|---|---|
  | Nightrider Glaive 1H | 63 | 739 |
  | Gargoyle's Halberd 1H | 124 | 750 |
  | Glaive 1H | 141 | 751 |
  | Vulgar Militia Shotel 1H | 166 | 753 |

  Every other row rises by up to 150 places (Poisoned Hand 1H 559 -> 409, Cinquedea 1H
  552 -> 404), because it is at its class's fastest and keeps its score.
- The within-class percentile's fit to adoption falls slightly (within its CI). The off-hand
  correlation rises over all 324 candidates but falls over the 104 that are used. The term
  reproduces the user's axe order, and the corpus does not confirm it.

### 7d. Other measures of speed (MEASURED, `er-builds-speed-adoption.py --all-measures`)

The same test on every measure `er-builds-pvp.speed_measures` defines (lower = faster):
- `rec_roll` / `rec_next`: hit to the earliest roll / R1 #2 start.
- `second_hit`: the frame R1 #2 lands, from R1 #1's start.
- `string_dps`: the whole R1 string's damage per second, each R1 started at the previous one's
  `next`.
- `run_r1` / `roll_r1`: those attacks' first active frame.

The off-hand uses its L1 equivalents. Each measure is run per weapon and per animation family: a
(`wepType`, R1 #1 animation set) pair becomes one unit with its adoption summed, so halberd and
glaive movesets, or axe and hatchet L1s, are compared as the families players compare. Columns:
- `rho`: mean within-class Spearman.
- `fast=top`: classes where the fastest is among the most used.
- `single`: with one fastest, how often it is the most used, and its share against 1/n.
- `gap`: Spearman of the gap against the excess share.
- `slope`: log adoption share per unit behind the fastest, with a class-bootstrap CI.

| hand, measure, unit | classes | rho | fast=top | single: top, share vs 1/n | gap | slope [CI] |
|---|---|---|---|---|---|---|
| main first_hit | 30 | 0.059 | 24/30 | 0/2, 0.11 vs 0.06 | 0.10 | +0.008 [-0.120, +0.255] |
| main first_hit family | 14 | -0.342 | 8/14 | 4/10, 0.39 vs 0.43 | 0.06 | +0.142 [-0.256, +0.589] |
| main rec_roll | 30 | -0.094 | 25/30 | 0/2 | -0.32 | +0.050 [-0.086, +0.195] |
| main rec_roll family | 14 | 0.217 | 9/14 | 3/7, 0.40 vs 0.48 | 0.34 | +0.042 [-0.050, +0.397] |
| main rec_next | 30 | -0.064 | 23/30 | 1/3 | 0.02 | +0.008 [-0.059, +0.100] |
| main rec_next family | 14 | -0.217 | 6/14 | 2/10, 0.30 vs 0.43 | -0.40 | +0.059 [+0.005, +0.390] |
| main second_hit | 30 | 0.047 | 24/30 | 1/2 | 0.20 | -0.012 [-0.059, +0.077] |
| main second_hit family | 14 | -0.125 | 7/14 | 3/10, 0.39 vs 0.43 | -0.35 | +0.036 [-0.005, +0.142] |
| **main string_dps** | 30 | **0.195** (weighted 0.280) | 10/30 | **10/29, 0.21 vs 0.12** | 0.07 | **-0.0022 [-0.0042, -0.0008]** |
| main string_dps family | 14 | -0.125 | 4/14 | 4/14, 0.39 vs 0.45 | 0.02 | +0.002 [-0.012, +0.021] |
| main run_r1 | 29 | 0.054 | 28/29 | 0/1 | -0.04 | +0.079 [-0.470, +0.107] |
| main roll_r1 | 28 | -0.139 | 25/28 | 0/2 | -0.50 | +0.057 [-0.073, +0.477] |
| off first_hit | 25 | 0.139 | 21/25 | 0/2 | 0.06 | +0.029 [-0.355, +0.295] |
| off first_hit family | 13 | 0.158 | 9/13 | 5/9, 0.47 vs 0.43 | 0.17 | -0.467 [-0.973, +0.228] |
| off rec_next family | 13 | 0.667 | 9/13 | 4/8, 0.39 vs 0.44 | -0.18 | -0.030 [-0.069, +0.237] |
| off second_hit | 25 | 0.231 | 21/25 | 1/2 | 0.42 | +0.007 [-0.122, +0.048] |
| off second_hit family | 13 | 0.500 | 9/13 | 5/9, 0.51 vs 0.43 | 0.27 | -0.048 [-0.299, -0.008] |
| off string_dps | 25 | -0.251 | 0/25 | 0/25, 0.03 vs 0.12 | -0.40 | +0.0015 [+0.000, +0.003] |

The run_r1 / roll_r1 family rows and the remaining off-hand rows are all within noise
(`speed-measures.txt` beside the run has every row).

- **Only the main hand's R1-string DPS shows a fastest-in-class effect.**
  - The weapon with a class's highest R1-string DPS is the most used in 10 of the 29 classes
    where it is unique.
  - It takes 0.21 of the class's adoption against 0.12 by chance.
  - Adoption falls with DPS behind the leader, and the class-bootstrap CI excludes 0.
  - The gap to the second weapon does not predict the leader's share (0.07).
  - DPS mixes damage and speed, so this is not a pure speed result.
- **The off-hand second-hit family slope** is the only other CI that excludes 0, just (-0.008).
  It is one of 24 tests and its weapon-level version is flat, so it is not taken as support.
  Nothing changed for the off-hand.
- **Recovery, the chained second hit, the running and rolling R1, and the family grouping** show
  nothing. The first-hit family row is negative.

### 7e. The term on string DPS (`--relative-speed 450 --relative-speed-measure string_dps`)

`TAU` = 1 / 0.00221 = 450 DPS is the corpus slope above, so the parameter is chosen from the same
corpus the check below uses.

| check | off | string_dps, TAU 450 |
|---|---|---|
| within-class score percentile coefficient | +0.546 [+0.272, +0.799] | +0.483 [+0.232, +0.723] |
| ash coefficient | +0.014 | +0.054 |
| great-spear ash Spearman | unchanged (by construction, 7c) | unchanged |
| rows whose score moves | | 752 of 822 (factor median 0.85, 10th percentile 0.71, minimum 0.35) |

Biggest movers:

| row | rank, off | rank, TAU 450 |
|---|---|---|
| Raptor Talons 2H | 40 | 283 |
| Celebrant's Cleaver 2H | 118 | 355 |
| Glaive 1H | 141 | 376 |
| Banished Knight's Halberd 1H | 80 | 313 |
| Cross-Naginata 2H | 423 | 270 |
| Treespear 2H | 260 | 116 |
| Beast Claw 1H | 549 | 401 |

**The term makes the score agree less with adoption** (percentile coefficient +0.546 -> +0.483),
even though adoption on its own leans to the class's highest-DPS weapon. `slot_score` is already
damage per committed frame, so the within-class DPS order is largely in the score. Multiplying by
it again over-weights it and reshuffles 752 rows.

The flag stays off by default, and no default scoring changed. The off-hand keeps no speed term
(its `--relative-speed` stays on `first_hit`, which 7a showed unsupported).

Not established: a pure-speed measure that separates weapons within a class. The first active
frame, recovery and the second hit do not. Nor whether a flat, additive or rank-based form of
the DPS term would help where the multiplicative one hurts; that would be a fit to the corpus.

## 8. Standout moves: scoring a weapon by its best tool (2026-09-30)

The rule under test (user lead): many weapons are carried by one part of their moveset that is as
good as the best weapons have, and a matching mean over families dilutes it. Tool:
`scripts/er-builds-aggregation-adoption.py` (`--selftest`), aggregation forms in
`er-mechanics-moveset.aggregate` / `AGGREGATE`, full-run flag `er-builds-pvp.py --aggregate`.

Forms, one parameter each:
- `family:a` the existing matching mean over families (`a` = 1 default; `inf` = the best family,
  which is the best opener);
- `standout:w` = (1 - w) x the family matching mean + w x the best opener's engagement;
- `openers:a` the matching mean over every opener's own engagement instead of over families.

### 8a. Post-hoc sweep (MEASURED, RL 150, 822 rows, RL 140-160 corpus)

Each form recomputed from the stored slots of the default ranking (jump openers rebuilt with their
neutral contest, skill term rebuilt from the stored mountable options; both reproduce the stored
scores exactly). `pct` is `er-mechanics-ashes.py check`'s within-class score percentile
coefficient; rho is the weapon-level Spearman against primary adoption.

| form | pct coef [95% CI] | ash coef | rho all | rho adopted |
|---|---|---|---|---|
| family:1 (default) | +0.546 [+0.272, +0.799] | +0.014 | +0.460 | +0.264 |
| family:0 (plain mean) | +0.527 [+0.258, +0.805] | +0.020 | +0.457 | +0.262 |
| family:2 | +0.494 [+0.236, +0.741] | +0.048 | +0.461 | +0.262 |
| family:4 | +0.458 [+0.210, +0.693] | +0.074 | +0.458 | +0.264 |
| family:inf (best opener) | +0.381 [+0.137, +0.609] | +0.114 | +0.457 | +0.277 |
| standout:0.25 | +0.499 [+0.240, +0.748] | +0.045 | +0.462 | +0.266 |
| standout:0.5 | +0.468 [+0.216, +0.701] | +0.068 | +0.462 | +0.268 |
| standout:0.75 | +0.406 [+0.161, +0.632] | +0.100 | +0.460 | +0.274 |
| openers:1 | +0.454 [+0.155, +0.734] | +0.051 | +0.435 | +0.221 |
| openers:2 | +0.477 [+0.180, +0.740] | +0.042 | +0.439 | +0.230 |
| openers:4 | +0.436 [+0.178, +0.683] | +0.075 | +0.445 | +0.240 |

- **The more a form leans on the best opener, the worse the within-class percentile fits
  adoption**: monotone from +0.546 (a = 1) to +0.381 (max). Every CI overlaps the default's, and the
  forms were compared on the same corpus they are checked against, so this says "not supported",
  not "refuted". The weapon-level Spearman barely moves (0.457-0.462).
- Who rises when the best opener counts more: 2H daggers, claws and katars carried by a strong R2
  (Parrying Dagger 2H 261 -> 72 at standout 0.5, Cinquedea 2H 341 -> 177, Cipher Pata 2H 255 ->
  127), the 1H hatchets' R1 (Hand Axe 208 -> 62, Forked Hatchet 209 -> 66) and the Guardian's
  Swordspear's running jump R2 (246 -> 112).

### 8b. Full run, `--aggregate standout:0.5` (w chosen by hand as the midpoint, not fitted)

| | default | standout:0.5 |
|---|---|---|
| pct coef | +0.546 [+0.272, +0.799] | +0.463 [+0.194, +0.714] |
| ash coef | +0.014 | +0.062 |
| rows moving more than 50 places | | 65 of 822 |

The post-hoc estimate (+0.468) and the full run (+0.463) agree; the difference is the buff options,
which the post-hoc holds fixed. The flag stays off by default.

The two user examples are not in this ranking. The off-hand L1 chain is a separate ranking
(`er-mechanics-offhand.py`, `--paired-offhand`), and powerstance is not a row of
`er-builds-pvp.py` at all (section 8c).

### 8c. Powerstance rows beside the ranking (`--powerstance`, `INFERRED` proxy)

`er-builds-pvp.py` scores one row per weapon and grip; powerstance exists only in
`er-mechanics-moveset.py --powerstance`, whose `DualScorer` slots carry no contest, reaction dodge,
guard pressure, coverage, crit or parry term, so their scores cannot be compared with the
ranking's. `er-builds-aggregation-adoption.powerstance_rows` builds a third row per same-weapon pair
(315 at RL 150): the 1H row's slots plus the dual L1 slots, each dual slot taking those fields
from its right-hand twin (`DUAL_TWIN`: L1 #n <- R1 #n, dash <- running R1, rolling <- rolling R1,
crouch <- crouch R1, backstep <- backstep R1, jump <- jump R1). The skill options are the 1H row's.

The crouch L1 follows the behavior script and is measured on its own clip (section 8d). The table
below is the earlier run, whose crouch L1 took the one-hand gate and borrowed every factor from the
crouch R1; section 8d has the corrected ranks.

Ranks among all 1137 rows (822 + 315), default scoring (superseded by section 8d):

| weapon | powerstance | 1H | 2H | powerstance's best opener |
|---|---|---|---|---|
| Giant-Crusher | 31 | 215 | 63 | `dual_crouch` (the rolling L1) 773 |
| Prelate's Inferno Crozier | 52 | 256 | 108 | `dual_crouch` 729 |
| Great Club | 56 | 471 | 149 | `dual_crouch` 759 |
| Duelist Greataxe | 64 | 233 | 55 | `dual_1` 721 |
| Rotten Greataxe | 86 | 268 | 77 | `dual_1` 697 |
| Anvil Hammer | 244 | 638 | 297 | `dual_1` 711 |
| Greatsword (colossal sword) | 189 | 345 | 36 | `dual_jump_f` 627 |
| Zweihander | 220 | 381 | 71 | `dual_1` 564 |

- The lead holds for the colossal weapons: powerstanced they rank far above their own 1H row, and
  the crouch (rolling) L1 is their best single opener. For Giant-Crusher it is the best opener of
  any of its three rows (773 against 2H R1 #1 639).
- It does not hold for the colossal swords: their powerstance rows sit below their 2H rows.
- Adding the powerstance rows as a third grip (best row per weapon) moves the percentile coefficient
  from +0.546 to +0.600 [+0.322, +0.845], inside the CI.

### 8d. The powerstance crouch L1, resolved and measured (2026-09-30)

The chain from input to clip, each link read from game data:

| link | value | label |
|---|---|---|
| crouch + L1 while powerstanced | `StealthActionCommonFunction` passes `r1 = W_AttackRightLightStealth`; `ExecAttack` takes `ATTACK_REQUEST_DUAL_RIGHT` | HKS |
| gate | `ExecAttack` line 1645 calls `IsUseStealthAttack(TRUE)`, not the one-hand `IsUseStealthAttack(FALSE)` the earlier run used. Bytecode pcs 0-29: `TRUE` only for right-hand category 23, 24, 27, 28, 36, 37, 58 (`DUAL_STEALTH_ATTACK_CATEGORIES`); no spAtkcategory test | HKS bytecode, `er-hks-disasm.py --dump IsUseStealthAttack` |
| refused | `W_AttackDualRolling`; `c0000.behbnd` state `AttackDualRolling` plays clip 034300 and nothing else (`er-behbnd-attack-map.py`) | BEHAVIOR |
| allowed | `W_AttackDualStealth` -> state `AttackDualStealth` -> 034310 | BEHAVIOR |
| cross-check | 034310 exists in exactly the allowed TAEs plus 39, which the gate refuses | TAE |

So the colossal weapons (31) and the colossal swords (26) both play their rolling L1 clip; the
earlier run dropped the colossal swords' crouch L1, gave spears (36, 37) the rolling L1 instead of
their 034310, and gave heavy thrusting swords (39) 034310 instead of the rolling L1.

Giant-Crusher and Great Club, a031_034300, real frames from the clip's start:

| item | crouch L1 (a031_034300) | the crouch R1 it borrowed from (1H, a031_030300) |
|---|---|---|
| hits | right 14-16 (judge 870), left 14-16 (875) | 15-18 |
| front contact at 1.5-3.0 m | 14, 14, 14, 14.5 | 15 |
| next L1 / dodge / move | 35 / 36 / 52; clip 73 | |
| world reach | 4.23 m (Great Club 4.20), lunge 1.85 m | 4.74 m (4.81), lunge 2.16 m |
| stamina | 40 (two AttackBehavior events, 20 each) | 20 |
| reaction dodge at 2.5 m, evade | 0.91 | 0.92 |

`measure_dual_slot` (moveset.py) now writes reach, coverage, reaction dodge, exchange and neutral
onto `dual_crouch` from this clip; `er-builds-aggregation-adoption.powerstance_rows` borrows from
the twin only the fields a slot did not measure. Both hands are posed on `R_Weapon` (the reach
module does not read the Source byte), `INFERRED` for a symmetric slam. Charging both events'
stamina is the per-event charge of attacks.md section 3 summed over events (`INFERRED`, as in
`er-mechanics-exchange.stamina_total`).

Giant-Crusher's crouch L1 slot score, RL 150: 773 -> 467. Factors: stamina 1.00 -> 0.72 (the twin
never paid the left hand), neutral contest 1.154 -> 1.010 and reach 1.376 -> 1.301 (the clip
lunges 0.3 m less), hit worth 0.617 -> 0.550. The reaction dodge barely moves: at 14 frames a
waiting defender still evades 91% on reaction.

Ranks among 1137 rows after the fix (`--stored-only --powerstance`, same stored rankings as 8c
and 9d):

| weapon | default: before -> after | `--timing-mixup crouch`: before -> after | best opener now (default) |
|---|---|---|---|
| Giant-Crusher | 31 -> 91 | 27 -> 97 | `dual_1` 724 |
| Great Club | 56 -> 204 | 37 -> 167 | `dual_1` 733 |
| Duelist Greataxe | 64 -> 89 | 49 -> 107 | `dual_1` 721 |
| Prelate's Inferno Crozier | 52 -> 126 | 50 -> 129 | `dual_1` 689 |
| Rotten Greataxe | 86 -> 116 | 74 -> 135 | `dual_1` 697 |
| Anvil Hammer | 244 -> 361 | 101 -> 324 | `dual_1` 711 |
| Zweihander (colossal sword) | 220 -> 203 | | `dual_1` 564 |

- Percentile coefficient with powerstance as a third grip: +0.600 -> +0.590 [+0.314, +0.838]
  default, +0.557 -> +0.574 [+0.302, +0.811] under `--timing-mixup crouch`; both inside the CI.
- The model does not reproduce the user's claim that this attack makes powerstanced colossal
  weapons top tier. What would have to change is outside the clip: the reaction model lets a
  waiting defender evade a 14-frame start 91% of the time, and early rolls out of nerves are not
  modelled (section 9b).
- `dual_roll` plays the same clip; it borrowed the rolling R1's factors (single stamina charge, its
  reach) and outscored `dual_crouch` on Giant-Crusher (475 against 467). It is now in
  `MEASURED_DUAL_SLOTS`, entered as `roll_r1`: the crouch L1 is again Giant-Crusher's best movement
  attack (466.8) and its row moves 91 -> 92 of 1137; Great Club 204 -> 201, Duelist Greataxe 89,
  Prelate's Inferno Crozier 126 -> 125, Anvil Hammer 361 -> 360 (`--stored-only --powerstance`).

## 9. Timing mixups: held starts (2026-09-30)

User lead: a crouched attacker can sit in the crouch and release the crouch attack (the rolling
attack's animation) at any moment, so the defender cannot time a reaction, and an early roll gets
caught. Tool: `scripts/er-mechanics-timing-mixup.py` (`--selftest`, `movement`, `weapon`); flag
`er-builds-pvp.py --timing-mixup [crouch,entry]`.

### 9a. What the game data says

| item | value | label |
|---|---|---|
| crouch is held | `Stealth_Idle_onUpdate` sets `STEALTH_IDLE` and calls only `IdleCommonFunction`, `ExecArtsStance`, `ExecGuard`; nothing ends it on a timer | HKS |
| crouch R1 | `Stealth_to_Stealth_Idle_onUpdate` passes R1 as `W_AttackRightLightStealth`; `ExecAttack` rewrites it to the rolling R1 when `IsUseStealthAttack` is false (every category but 21, 23, 24, 26, 27, 28, 39, 58) | HKS |
| entering the crouch from standing | a000_390000, R1 input and cancel overlap from frame 8 | TAE |
| crouch movement | a000_320000 1.37 m/s, 320100 2.98 m/s, 320200 4.41 m/s; a locked-on crouch moves at index 1, 2.98 m/s (10.06 frames a metre, against the run's 7.48) | MEASURED; index INFERRED |
| rolling R1 input after a roll | a000_027110 frames 20-50 (30 frames the attacker may wait); backstep 027000 frames 14-56 | TAE |

### 9b. What the reaction model already does

`er-builds-pvp.Mechanics.slots` times every reaction dodge from the attack clip's own start (cue
0): the R2s from their release clip, and the crouch, running, rolling and backstep attacks from
their attack clip. Only jumps are read from the jump input. So every opener's start is already
treated as unreadable, the held ones included. Against that model the crouch attack is not
undodgeable: at 2.5 m a waiting defender (reaction 0.25 s median + 2 x 50 ms network, the medium
roll) evades it whenever its first live frame is late enough.

`er-mechanics-timing-mixup.py weapon` (2.5 m, frames from the attack clip's start; `pre caught` =
share of rolls pressed up to one roll-length before the attack that the attack catches):

| weapon, grip, opener | live frames | travel m | evade on reaction | pre-roll caught |
|---|---|---|---|---|
| Giant-Crusher 2H crouch R1 (= rolling R1) | 14-16 | 2.32 | 0.84 | 0.25 |
| Giant-Crusher 1H crouch R1 | 15-18 | 2.45 | 0.92 | 0.25 |
| Greatsword 2H crouch R1 | 16.3-18.3 | 1.34 | 0.98 | 0.25 |
| Claymore 2H crouch R1 | 15-18 | 1.26 | 0.94 | 0.25 |
| Longsword 2H crouch R1 | 11-14 | 1.12 | 0.56 | 0.25 |
| Dagger 1H crouch R1 | 8-10 | 1.24 | 0.06 | 0.21 |
| Dagger 1H R1 #1 | 10-12 | 1.29 | 0.46 | 0.24 |

- A colossal crouch attack is dodged on reaction 84-92% of the time; a dagger's 6%. The held start
  denies the defender a cue, but at 14+ frames reaction alone is enough. The roll-catch is real
  (a roll overlapping the attack is caught a quarter of the time on these weapons), but a pre-roll
  against a start uniform over a hold of W frames overlaps the attack only `21 / W` of the time.
  On the slow crouch attacks it loses to waiting (Giant-Crusher 2H at W = 60: 81% against 84%); on
  the fast ones it wins (Dagger 1H: 32% at W = 60, 57% at W = 30, against 6% for waiting). So the
  held crouch hurts the defender where the attack is too fast to react to, which the reaction
  model already scores, and the pre-roll is the defender's better answer there, not the attacker's
  gain.
- Not modelled: a defender who rolls early out of nerves (a share would be a new free parameter),
  and any reaction slower than the lognormal above.

### 9c. The term (`--timing-mixup`, `INFERRED`)

- `crouch`: the crouch R1 is thrown from a held crouch. No entry at the engagement (the 8-frame
  standing crouch was spent before it), and in the neutral race the attacker closes any reach gap
  at the crouch speed with no dodge. The powerstance crouch L1 inherits it through its twin.
- `entry`: a rolling or backstep attack is preceded by a visible roll or backstep. The waiting
  defender takes the better of reacting to the attack and anticipating it from the entry's start
  (one press at his best time, the attack's start uniform over the entry's R1 window).
  Anticipating evades 0.73-0.89 on the weapons above, so it only matters for fast rolling attacks
  (Dagger 1H 0.06 -> 0.84, Longsword 0.46-0.56 -> 0.83).

### 9d. Effect on the RL 150 ranking (full runs, MEASURED)

| run | pct coef [95% CI] | ash | with powerstance rows |
|---|---|---|---|
| default | +0.546 [+0.272, +0.799] | +0.014 | +0.600 [+0.322, +0.845] |
| `--timing-mixup crouch` | +0.563 [+0.284, +0.820] | +0.010 | +0.557 [+0.286, +0.803] |
| `--timing-mixup` (crouch + entry) | +0.563 [+0.284, +0.820] | +0.010 | +0.557 [+0.286, +0.803] |
| `--aggregate standout:0.5` | +0.463 [+0.194, +0.714] | +0.062 | +0.559 [+0.289, +0.815] |
| both flags | +0.484 [+0.219, +0.731] | +0.054 | +0.430 [+0.178, +0.673] |

- `entry` changes 580 of 1619 rolling/backstep slots (score ratio down to 0.61) but no row's best
  opener, so the coefficient is the crouch part's.
- `crouch` moves 19 rows by more than 50 places: Claws of Night 2H 217 -> 58, Katar 2H 120 -> 43,
  Venomous Fang 2H 131 -> 49, Anvil Hammer 2H 174 -> 109, Dragon Greatclaw 2H 280 -> 220; the crouch
  R1 becomes the best opener on several of them.
- Powerstance under `--timing-mixup` (ranks among 1137 rows): Giant-Crusher 31 -> 27, Great Club
  56 -> 37, Duelist Greataxe 64 -> 49, Prelate's Inferno Crozier 52 -> 50, Rotten Greataxe 86 -> 74,
  Anvil Hammer 244 -> 101; the crouch L1 is now their best opener (Giant-Crusher 909). With both
  flags: Giant-Crusher 26, Great Club 29, Duelist Greataxe 39. Claws of Night powerstance is row 1
  under either.
- The within-class fit to adoption rises slightly with `crouch` (+0.017, inside the CI) and falls
  with the standout aggregation; neither change is established by the corpus. Both flags stay off
  by default.

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --json > pvp150.json      # ~4 min
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-moveset.py --pvp pvp150.json --powerstance --calibrate \
    --weapon Giant-Crusher --weapon Greatsword                                                     # ~1.5 min
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-moveset.py --pvp pvp150.json --jump-effect --top 12
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-moveset.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-jump.py --weapon Greatsword --weapon Lance
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-jump.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-aggregation-adoption.py --pvp pvp150.json --movers 12   # section 8a
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-aggregation-adoption.py --pvp pvp150.json --stored-only --powerstance
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --sort score --json --aggregate standout:0.5 > agg.json
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --sort score --json --timing-mixup crouch > mix.json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-timing-mixup.py weapon --weapon Giant-Crusher --grip both
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-timing-mixup.py --selftest
```
