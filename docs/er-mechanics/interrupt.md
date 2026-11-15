# Interrupts: which attacks poise-break an opponent mid-swing (RL 150 PvP)

Labels as in the other files here: **VERIFIED** = regulation value or traced EXE code (1.16.2
image `eldenring-deobf.bin`, shift 0 against the named dump on :8765), **TAE** = decoded TimeAct,
**MEASURED** = computed by the commands at the end from the regulation and the corpus mirror,
**INFERRED** = a modelling choice or an untraced consumer. Nothing was launched; no number here has
runtime proof.

Tool: `scripts/er-mechanics-interrupt.py` (`--selftest`: 26 checks). It imports
`er-mechanics-attacks`, `-exchange`, `-reach`, `-moveset`, `-ashes`, `-frame-advantage`, `-jump`
and `-powerstance-guard` read-only and edits none of them.

## Answer

Figures from the 2026-11-10 rerun (section 3), which models hit priority records (bd
er-effects-rs-dnok).

- **Who stops the pool mid-swing** is decided by the strike frame first and poise second. With
  `saRate` (the code path), almost every R1 above dagger class breaks most of the pool in one hit,
  so the ranking is a startup ranking: daggers (R2 at frame 11, 158 poise), then the 12.9-13.4
  frame hatchet and axe R1s. Dane's Footwork led it (42.2%) until its two fists were found to share
  one hit priority record per strike; at one hit per strike it is 39th (35.5%). Without `saRate`,
  one-hit breaks are rare and fists, caestus and thrusting shields lead. Colossal weapons stop
  17-24% of the pool and are stopped least (Giant-Crusher 2H: 7.7% with `saRate`, 29th lowest of
  820; 1.6% without, third lowest).
- **The user's Cleanrot claim does not hold as a Cleanrot property.** Cleanrot Knight's Sword 1H
  ranks 200th of 820 weapon-grips (29.5% of the pool stopped) with `saRate` and 485th (17.5%)
  without; 2H 71st / 285th; Cleanrot Spear 1H 143rd / 405th, 2H 704th / 654th. Against low-poise
  builds (armor poise 53 or less, the pool's bottom quarter) Cleanrot Knight's Sword 1H stops 31%
  (189th) / 26% (333rd); daggers stop 44% / 41%. The mechanism the claim describes is real, and
  with `saRate` it is universal: a Cleanrot R1 #1 deals 104, above every low-poise build, but so
  does every straight sword, katana and axe R1. What decides the interrupt is landing first, and
  Cleanrot's R1 reaches 2.5 m on frame 15.5, behind daggers (11), axes (12.9) and claws (14).
  Its chain does not help much against the pool: R1 #2 lands about 15 frames after #1, at 54
  poise (`saRate` 1.35), and few pool windups last 30 frames.
- **Poise chip is real and fully persistent** (section 2, VERIFIED): no regeneration at all until
  30 s after the last hit taken, for every armor set. Guarded hits do not chip; hits into
  hyperarmor and trades do. Counter hits do not raise poise damage.
- **What carried chip changes** (fresh -> carried, stop share): with `saRate`, the median weapon
  gains nothing and fast chains gain in their R1 (dagger 1H R1 12 -> 24%, twinblades 18 -> 28%);
  without `saRate`, the median R1 gains 5.9 points and the best answer 2.6. Dagger 1H 21 -> 31%,
  Hookclaws 1H 16 -> 22%, Cleanrot Knight's Sword 1H 18 -> 22% (R1 5 -> 13%). Giant-Crusher and
  Greatsword gain 0: their hits break outright or not at all, and the defender's weapon-window
  floor heals chip above 20% of the boosted pool.

## 1. The question and the method

For a weapon W (each grip) and each attack A it has, against each attack B of the RL 140-160
opponent pool: B has started, and A starts `o` frames later. Does A land, and break the
defender's poise, before B's hitbox reaches A? `o` runs over every frame from B's first frame to
its strike frame (one frame apart, equally likely: INFERRED). The exchange module only tried
`o = 0` against the pool's R1 #1 (exchange.md section 6).

- **Strike frame** of any attack = the frame its hitbox first touches a defender 2.5 m straight
  ahead (`er-mechanics-reach` `front_contact_frame_real`; the distance is INFERRED, as in
  exchange.md section 2), plus the R2 release lead-in and the entry frames. 924 of 15509 members
  have no pose; they take the class-median delay from first hit to contact (INFERRED, the rule of
  `er-mechanics-reach.class_fallback` over the cached rows). Powerstance and skill hits take the
  weapon's 1H R1 #1 delay (INFERRED proxy, as `er-mechanics-moveset`).
- **A's hits**: every main-judge window and extra hitbox that opens a fresh hit record
  (`sweep_hit`, attacks.md section 4). A later hit of the same clip keeps its window's distance
  from the first (INFERRED). The R1, R2 and powerstance L1 openers carry their whole chain: the
  next clip starts at the previous clip's cancel frame for the same button, and an R2 #2 pays its
  own release lead-in (INFERRED: the button is buffered).
- **Entries** (frames before the attack's clip): roll 20, backstep 14, crouch 8, sprint 20
  (`er-builds-pvp.SCORE_ENTRY_FRAMES`, the sprint INFERRED), jump 6
  (`er-mechanics-jump.takeoff`, TAE). The medium roll's entry is invincible on frames 0-13 (TAE
  `a000_027110`, JumpTable 8 with no stateInfo gate). The backstep's only JumpTable 8 (f0-7) is
  gated on stateInfo 473, the Fine Crucible Feather, so a plain backstep has none (TAE +
  REGULATION; talismans.md section on 290/473).
- **Poise dealt**, menu units: `poise_damage x 10 x FinalDamageRateParam.saRate`, the
  exchange module's expression. A straight-sword R1 deals 110 against a pool median of 81; the
  community "51 poise survives one R1" is a PvE breakpoint, and the community 1.17 PvP table gives
  the same 110 (VERIFIED 2026-10-01, bd `pvp-poise-units-resolved-51-is-pve-breakpoint-2026-10-01`). Results below are still given twice; the `saRate`
  column is the game's, the other is a PvE comparison.
- **Defender poise**: each pool build's own armor poise when the pool is struck; the pool's poise
  distribution at its 10/30/50/70/90th percentiles (36-114) when W is struck (INFERRED: W wears a
  corpus-typical set, as in exchange.md).
- **Pool weights**: build frequency (945 builds, each carrying the R1-capable right-hand weapon
  the exchange pool picks) x attack family. The pool's attacks are grouped into four families,
  r1 (R1 #1), r2 (uncharged and charged R2 #1), move (running R1/R2, rolling, backstep, crouch)
  and jump (jump R1/R2), equal shares, equal within a family. There is no basis for use shares:
  the corpus records what players carry, not what they press (moveset.md section 3). The pool
  records neither the build's ash of war nor a powerstance pair, so B never is a skill or an L1.

Aggregates per weapon-grip (MEASURED over this model):

| column | meaning |
|---|---|
| `stop_best` | pool-weighted share of (B, offset) that W stops with its best attack at that offset (the player picks the answer, INFERRED) |
| `stop_family` | the same, averaged over W's opener families (r1, r2, move, jump, l1, skill), each family at its best |
| `stop_r1` | the R1 chain alone |
| `stopped` | share of (B, offset, W's poise) in which B, started during A's windup, breaks W before A strikes; averaged over W's families |

## 2. Poise state: chip, regeneration, and what does not chip (VERIFIED)

Read out of the 1.16.2 image with the Ghidra MCP on :8765 and byte-checked by the selftest
(`EXE_ANCHORS`):

- **A hit** (`FUN_140486bf0`): `dmg = info[+0x244] x info[+0x100] x saRate x unk1`; then
  `current -= cutRate x damageRatio x dmg`. At or below 0 the defender is broken, current is
  clamped to 0 and the stagger fires.
- **A guarded hit chips nothing.** The subtraction is skipped when `info+0x258` is set
  (`cmp byte [rdi+0x258], 0` at 0x140486d52). That flag is the guarded flag: `ApplyDamage`
  (0x1404497d0) takes the guard-material branch (`FUN_140454340` with `guardBehaviorId`) on it
  and skips the throw. The name is INFERRED from that branch; the skip is VERIFIED.
- **Hits absorbed by hyperarmor chip.** Nothing in the path skips inside a window; the window
  only raises max and multiplies by `unk1` (0.45 / 0.65) and `damageRatio`. When a window opens
  or closes, the update (`FUN_140486e50`) sets
  `current = clamp(newMax - (oldMax - current), minToughness% x newMax, newMax)`, so the damage
  taken carries across the window. The one exception is the floor: entering a weapon window
  (rows 100-151, `minToughness` 80) lifts current to 80% of the boosted max, which heals chip
  above 20% of it.
- **Trades chip both sides.** A trade is two landed hits, each through the same path.
- **No gradual regeneration.** The only refill without a break is a timer at `+0x20`. Every hit
  whose damage level (`info+0x24`, the AtkParam `dmgLevel` written by `FUN_140d24b10`) is nonzero
  sets it to toughness vtable slot 8 (`cmp byte [rdi+0x24], 0` at 0x140486d93, then
  `call [rax+0x40]; movss [rbx+0x20], xmm0`). The update counts it down by `deltaTime` and, when
  it reaches 0 with no break pending, sets current = max in one step.
- **The reset delay** (slot 8 = 0x140487b00, which tail-jumps to `FUN_140688e50` at
  0x140487c70): `GameSystemCommonParam.baseToughnessRecoverTime x
  PlayerCommonParam.toughnessRecoverCorrection x prod over the four armor pieces of (1 +
  EquipParamProtector.toughnessRecoverCorrection)` = **30.0 s x 1.0 x 1.0** (REGULATION 1.17.1:
  every one of the 838 protector rows holds 0.0). So poise refills 900 frames after the last hit
  taken, for every player, whatever they wear.
- **After a break** poise refills to max two toughness updates later (frame-advantage.md
  section 1), so chip never carries past a break.

What this means for the interrupt model: chip from any earlier hit stays until 30 s without being
hit, or a break. Only guarded hits do not count.

### Counter hits do not add poise damage (VERIFIED)

SpEffect 45 (the counter state every player weapon attack puts on its user from its first hitbox
frame, bd `er-damage-type-252-253-and-pierce-counter-spEffect45-2026-09-29`): `thrustDamageCutRate`
1.15, `toughnessDamageCutRate` 1.0, `changeSuperArmorPoint` 0 (REGULATION). The poise path reads
only `toughnessDamageCutRate` from SpEffects (toughness vtable slot 7, attacks.md section 2), so a
counter hit multiplies pierce HP damage by 1.15 and poise damage by 1.0. It also starts at B's
first hitbox frame, so during B's windup, where every interrupt in this model lands, B is not in
the counter state at all.

### Chip in the model

- **Within an attack and a chain**: every hit A lands before B's strike subtracts from the same
  current value, through B's windows as above. That is the `fresh` column.
- **Between engagements** (`carried`): the defender is at a uniformly random point of the cycle
  an attacker who keeps landing the same first hit `d` produces within the 30 s timer: chip
  `j x d` for `j = 0 .. ceil(P / d) - 1`, equally likely (INFERRED: every landed hit within 30 s
  of the previous one, and nothing else breaks or resets the defender in between; at most 8
  evenly spaced states). A fight paced slower than one landed hit per 30 s is `fresh`; the truth
  for a given duel lies between the two columns.

## 3. Top 20 by `stop_best` (MEASURED, RL 140-160 pool, fresh / carried, percent)

Rerun 2026-11-10 in the worktree with the hit priority records (bd er-effects-rs-dnok):
`er-mechanics-interrupt.py --build-cache` then `--run [--no-sa-rate] --jobs 3 --sweep
~/.cache/er-build-planner/grease-sweep-dlc-drawstring-150-200.jsonl` (the default `--sweep`,
`...-150-200-cond-floors.jsonl`, does not exist on this machine; `--run` reads only the weapon
names from it). 890 pool builds, 2743 distinct attack sequences, 7218 defender rows (sequence x
armor poise), 820 scored weapon-grips of 822 (the 411 sweep weapons, both grips). Section 1's
counts are the 2026-09-30 run's. Weapons with identical movesets tie; the rows below are ranks
1-20 as the tool prints them.

With `saRate` (`interrupt-150.json`):

| # | weapon | best | low poise | R1 chain | stopped | R1 strike, first-hit poise |
|---|---|---|---|---|---|---|
| 1-7 | Bloodstained Dagger, Cinquedea, Crystal Knife, Dagger, Parrying Dagger, Reduvia, Wakizashi 2H | 41.0 / 41.0 | 44.7 | 16.0 / 26.9 | 7.7 | 11.0, 53 |
| 8-10 | Main-gauche, Misericorde, Scorpion's Stinger 2H | 39.9 / 40.4 | 44.6 | 16.0 / 26.9 | 8.0 | 11.0, 53 |
| 11 | Glintstone Kris 2H | 38.4 / 39.6 | 44.5 | 16.0 / 26.9 | 8.0 | 11.0, 53 |
| 12 | Ivory Sickle 1H | 38.2 / 40.5 | 44.4 | 11.8 / 23.5 | 9.5 | 11.0, 41 |
| 13-20 | Bloodstained Dagger, Celebrant's Sickle, Cinquedea, Crystal Knife, Dagger, Great Knife, Parrying Dagger, Reduvia 1H | 38.1 / 40.4 | 44.3 | 11.8 / 23.5 | 9.7 | 11.0, 41 |

The dagger lead comes from the uncharged R2 (strike frame 11, 158 poise, breaks every pool
build) more than the R1.

Without `saRate` (`interrupt-150-nosarate.json`):

| # | weapon | best | low poise | R1 chain | stopped |
|---|---|---|---|---|---|
| 1 | Dane's Footwork 1H | 31.8 / 34.4 | 36.8 | 11.7 / 20.1 | 8.6 |
| 2-3 | Caestus, Spiked Caestus 2H | 28.6 / 30.6 | 35.1 | 4.2 / 10.8 | 6.3 |
| 4-5 | Carian Thrusting Shield, Dueling Shield 1H | 28.2 / 30.3 | 35.0 | 25.6 / 29.0 | 12.7 |
| 6 | Dryleaf Arts 1H | 27.2 / 30.2 | 32.0 | 6.5 / 16.1 | 8.5 |
| 7-11 | Grafted Dragon, Iron Ball, Madding Hand, Poisoned Hand, Star Fist 2H | 26.8 / 27.4 | 29.4 | 4.2 / 10.8 | 6.2-6.7 |
| 12-13 | Caestus, Spiked Caestus 1H | 26.1 / 33.2 | 38.4 | 10.8 / 20.8 | 7.7 |
| 14 | Dueling Shield 2H | 25.8 / 26.8 | 29.5 | 23.7 / 25.5 | 7.1 |
| 15 | Ornamental Straight Sword 2H | 25.7 / 28.5 | 33.1 | 10.6 / 20.6 | 8.3 |
| 16 | Carian Thrusting Shield 2H | 25.7 / 26.7 | 29.4 | 23.7 / 25.5 | 7.1 |
| 17 | Pickaxe 1H | 25.3 / 25.4 | 27.3 | 25.0 / 25.1 | 11.3 |
| 18-21 | Pata, Grafted Dragon, Iron Ball, Star Fist 1H | 25.2 / 32.7-32.8 | 38.3 | 10.3-10.8 / 20.6-20.8 | 7.8 |

**What the hit priority records moved** (MEASURED 2026-11-10: `--run --only` on the main tree's
code, which differs from the worktree's only by bd er-effects-rs-dnok, against the rerun). Dane's
Footwork and Dryleaf Arts throw both fists on most strikes, both fists of a strike sit in one TAE
341 record, and one of them lands (attacks.md, hit priority records). The thrusting shields'
running R2 halves too, but it is not their best answer, so they do not move. Best, fresh /
carried (rank of 820):

| weapon | `saRate` before | after | no `saRate` before | after |
|---|---|---|---|---|
| Dane's Footwork 1H | 42.2 / 43.9 (1) | 35.5 / 37.5 (39) | 34.7 / 36.7 (1) | 31.8 / 34.4 (1) |
| Dane's Footwork 2H | 38.3 / 40.4 (13) | 26.7 / 32.9 (336) | 26.3 / 34.6 (12) | 13.5 / 24.7 (718) |
| Dryleaf Arts 1H | 37.1 / 39.0 | 31.5 / 33.6 (111) | 29.7 / 32.2 (2) | 27.2 / 30.2 (6) |
| Dryleaf Arts 2H | 30.7 / 33.7 | 29.6 / 32.9 (194) | 17.6 / 25.1 | 17.1 / 24.8 (523) |

Every other row moved by 0.5 points or less against the 2026-09-30 tables, and the main-tree run
gives the same values for those it covers (Dagger 1H and 2H, both modes), so those moves come
from the code between the two dates, not from the records.

## 4. The named weapons (MEASURED 2026-11-10 rerun, fresh / carried, percent; rank of 820 in brackets)

| weapon | best, `saRate` | best, no `saRate` | R1 chain, `saRate` | R1 chain, no `saRate` | stopped, `saRate` / no | R1 strike | R1 #1 poise `saRate` / no |
|---|---|---|---|---|---|---|---|
| Cleanrot Knight's Sword 1H | 29.5 / 29.8 (200) | 17.5 / 21.6 (485) | 19.7 / 21.9 | 4.7 / 12.8 | 10.7 / 7.7 | 15.5 | 104 / 40 |
| Cleanrot Knight's Sword 2H | 33.5 / 33.5 (71) | 20.3 / 25.3 (285) | 30.9 / 30.9 | 9.6 / 19.4 | 10.0 / 7.2 | 13.5 | 135 / 52 |
| Cleanrot Spear 1H | 30.7 / 30.7 (143) | 18.2 / 24.4 (405) | 13.8 / 15.6 | 3.9 / 10.0 | 13.8 / 9.9 | 18.0 | 105 / 50 |
| Cleanrot Spear 2H | 20.8 / 20.8 (704) | 15.6 / 16.7 (654) | 17.4 / 17.4 | 6.6 / 12.0 | 13.1 / 9.3 | 18.0 | 137 / 65 |
| Giant-Crusher 1H | 23.7 / 23.7 (583) | 17.9 / 17.9 (423) | 23.4 | 17.7 | 9.6 / 2.9 | 18.0 | 630 / 180 |
| Giant-Crusher 2H | 22.0 / 22.0 (652) | 17.2 / 17.2 (518) | 22.0 | 17.1 | 7.7 / 1.6 | 18.9 | 819 / 234 |
| Greatsword 1H | 21.8 / 21.8 (681) | 16.5 / 16.5 (552) | 21.2 | 16.4 | 9.5 / 3.8 | 18.7 | 504 / 144 |
| Greatsword 2H | 23.5 / 23.5 (601) | 17.8 / 17.8 (445) | 23.4 | 17.6 | 8.1 / 3.1 | 18.2 | 655 / 187 |
| Hand Axe 1H | 36.3 / 36.3 (27) | 20.7 / 27.0 (266) | 34.3 | 10.1 / 21.3 | 14.6 / 10.7 | 12.9 | 150 / 50 |
| Hand Axe 2H | 32.7 / 32.7 (81) | 23.3 / 26.1 (70) | 29.4 | 13.5 / 21.1 | 12.8 / 9.4 | 14.0 | 195 / 65 |
| Lance 1H | 21.4 / 21.4 (687) | 16.6 / 17.1 (540) | 18.1 | 13.6 / 15.5 | 15.6 / 11.5 | 18.0 | 224 / 102 |
| Lance 2H | 22.0 / 22.0 (643) | 17.8 / 17.8 (434) | 19.1 | 17.4 | 10.2 / 4.7 | 18.0 | 290 / 132 |

Why Cleanrot sits where it does (Cleanrot Knight's Sword 1H, from `--weapon`):

- **Per-hit poise**: base `saWeaponDamage` 4.0, so R1 #1 is 40 menu and R1 #2 onward 40; with
  `saRate` 2.6 on #1 and 1.35 on the chain (REGULATION rows 500000 / 500010), 104 then 54. A
  straight sword or katana R1 #1 is 110.
- **Chain speed**: R1 #1 reaches 2.5 m on frame 15.5 (front contact, the window opens on 15),
  the next R1 starts at the cancel frame 18 and its hit lands at 12, so hits arrive at 15.5,
  30, 46.5, 63.5 ... A pool attack has to still be winding up 30 frames in for the second hit
  to count. The powerstance L1 chain is the same shape (10 hits, 24 menu each without
  `saRate`).
- **Counter bonus**: none on poise (section 2), and interrupts land in B's windup, before B's
  counter state starts.
- **Carried chip** is where Cleanrot gains the most among the named weapons without `saRate`
  (R1 chain 4.7 -> 12.8%): its 40-poise hits need two or three landed hits within 30 s to reach
  a typical build, which is exactly what the carried cycle models.

Does the user's claim hold? The data says: against low-poise players, a Cleanrot hit does break
them when it lands first (with `saRate`, one R1 is enough for every build at 104 poise or less,
80% of the pool; without it, two or three R1s within 30 s). But the claim is a property of
fast, light R1s in general, and Cleanrot's R1 is slower to reach 2.5 m and lighter per hit than
the straight swords, katanas, axes and daggers beside it. Nothing in the frame, poise or chip
data sets Cleanrot apart. If Cleanrot really does better in practice, the cause is outside this
model: its thrust tracking and reach at other distances than 2.5 m, the Sacred Order / Sacred
Phalanx buffs, scarlet rot pressure changing what opponents press, or opponents' armor at the
low end being lower than the corpus.

## 5. Chip, weapon by weapon (MEASURED 2026-11-10 rerun)

`--report <file> --chip`. The carried column assumes every landed hit within 30 s of the last.

| weapon | best, no `saRate`, fresh -> carried | R1 chain, no `saRate` | R1 chain, `saRate` |
|---|---|---|---|
| Dagger 1H | 21.3 -> 30.9 | 6.2 -> 19.3 | 11.8 -> 23.5 |
| Misericorde 1H | 21.0 -> 30.5 | 6.2 -> 19.3 | 11.8 -> 23.5 |
| Hookclaws 1H | 16.0 -> 22.1 | 4.2 -> 13.7 | 13.4 -> 21.1 |
| Bloodhound Claws 2H | 24.1 -> 27.6 | 7.4 -> 18.3 | 25.8 -> 28.4 |
| Cleanrot Knight's Sword 1H | 17.5 -> 21.6 | 4.7 -> 12.8 | 19.7 -> 21.9 |
| Uchigatana 1H | 21.1 -> 24.1 | 7.3 -> 16.5 | 22.9 -> 25.1 |
| Hand Axe 1H | 20.7 -> 27.0 | 10.1 -> 21.3 | 34.3 -> 34.3 |
| Giant-Crusher 2H | 17.2 -> 17.2 | 17.1 -> 17.1 | 22.0 -> 22.0 |
| Greatsword 2H | 17.8 -> 17.8 | 17.6 -> 17.6 | 23.4 -> 23.4 |

Over all 820 weapon-grips the carried chip raises `stop_best` by a median 2.6 points and the R1
chain by 5.9 without `saRate`; with `saRate` the medians are 0 (mean 0.4 and 1.1), because most
first hits already break outright. The largest gains are the light fast hits: Smithscript Cirque
1H 15.3 -> 28.8, Dane's Footwork 2H 13.5 -> 24.7 (one fist per strike since the hit priority
records), Black Knife / Erdsteel Dagger 1H 17.8 -> 28.1, Claws of Night 2H 9.9 -> 20.2 (no
`saRate`). Fast chains against slow single hits: chip moves daggers and claws up by 6-13 points
and Giant-Crusher by 0, but Giant-Crusher is the weapon least stopped in return (1.6-7.7% against
7-11% for the fast chains), because its hyperarmor turns the fast chains' chip into trades.

## 6. Proposed wiring into the ranking (not applied)

`er-builds-pvp.py` belongs to another agent. The factor this module offers per slot is
`f_interrupt = 1 + INTERRUPT_WEIGHT x (stop - stopped)` (`slot_interrupt`, `INTERRUPT_WEIGHT`
0.25, INFERRED, the span of `EXCHANGE_WEIGHT`). It measures the same thing the exchange factor's
startup and hyperarmor parts do, over offsets and the whole moveset instead of one simultaneous
start, so it replaces `f_exchange` for the slots it covers and keeps `f_stamina`. The exact
change, against `scripts/er-builds-pvp.py` as of 2026-09-30:

```diff
@@ EXCH = _sibling("er-mechanics-exchange")
 EXCH = _sibling("er-mechanics-exchange")
+INTR = _sibling("er-mechanics-interrupt")
@@ def slot_score(s: dict, entry: float = 0.0) -> dict | None:
     ex = s.get("exchange") or {}
     f_ex = ex.get("factor", 1.0)
+    # The interrupt factor (docs/er-mechanics/interrupt.md section 6) replaces the exchange's
+    # startup and hyperarmor part for the openers it covers; the stamina part stays.
+    it = s.get("interrupt")
+    if it is not None:
+        f_ex = ex.get("f_stamina", 1.0) * it["f_interrupt"]
@@
-            "f_exchange": ex.get("f_exchange", 1.0), "f_stamina": ex.get("f_stamina", 1.0), "f_weight": f_weight}
+            "f_exchange": ex.get("f_exchange", 1.0), "f_stamina": ex.get("f_stamina", 1.0), "f_weight": f_weight,
+            "f_interrupt": (it or {}).get("f_interrupt", 1.0)}
@@ def main() -> int:
     ap.add_argument("--no-exchange", action="store_true", help="leave out the exchange and stamina factors")
+    ap.add_argument("--interrupt", type=Path,
+                    help="an er-mechanics-interrupt.py --run result; its f_interrupt replaces f_exchange's "
+                         "startup and hyperarmor part (docs/er-mechanics/interrupt.md section 6)")
@@
     pool = None if a.no_exchange else EXCH.Pool(EXCH.opponent_pool(reg, a.mirror, a.rl - a.window, a.rl + a.window))
+    imatrix = INTR.load_matrix(a.interrupt) if a.interrupt else None
@@
             if pool is not None:
                 slots[key]["exchange"] = EXCH.slot_exchange(pool, reg, base_id, atk, slots[key], entry_frames(key))
+            if imatrix is not None:
+                slots[key]["interrupt"] = INTR.slot_interrupt(imatrix, row["weapon"], row["two"], key)
             slots[key]["score"] = slot_score(slots[key], entry_frames(key))
```

Chain follow-ups (R1 #2 ...), the charged R2 #2 and the guard counter get `None` and keep the
exchange factor. The effect on the ranking was not measured.

## 7. Not established

- Resolved 2026-10-01: poise units. `saRate` applies (section 1).
- **Offsets are uniform** over B's windup. Real reaction timing is not in any data here.
- **Use shares.** Four equal pool families; no match log exists.
- **The pool's skills and powerstance** are not in the pool (the corpus pool records neither).
  W's own skill is computed per slot but left out of every aggregate: its frames come from the
  skill TimeAct's `main_anim`, which for several skills is a later part of the move (Marika's
  Hammer strikes on frame 1.5, Wild Strikes on 5.5), and bullet and stance skills are not modelled.
- **Distance.** Everything is at 2.5 m straight ahead; tracking, spacing and lateral hits are not
  in it (reach.md).
- **Carried chip** assumes one landed hit per 30 s at least and no other source of breaks. The
  pace of real duels is not measured.
- **Windows passed between two hits** are not recomputed individually (only the state at each
  hit), so a window opened and closed between two hits does not apply its floor. Rare in the
  pool's windups.
- **The guarded flag's name** (`info+0x258`) is INFERRED from the guard-material branch; the skip
  of the poise subtraction on it is VERIFIED.
- Class-median contact delays for 924 of 15509 members, the powerstance and skill proxies, and
  the sprint entry are INFERRED.

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --build-cache --jobs 12   # about 5 min
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --run --jobs 14 --out interrupt-150.json              # about 25 min
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --run --jobs 14 --no-sa-rate --out interrupt-150-nosarate.json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --report interrupt-150.json --top 20
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-interrupt.py --report interrupt-150.json --weapon "Cleanrot Knight's Sword" --grip one
# the EXE reads behind section 2 (Ghidra MCP on :8765, 1.16.2):
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140486bf0"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140486e50"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140487b00"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140688e50"}'
```

The member cache lives at `~/.cache/er-build-planner/interrupt-members.json`, stamped with the
hash of the sibling modules it reads; a stale stamp only warns.
