# Exchange: startup, hyperarmor trades and stamina as score factors

Labels as in the other files here: **VERIFIED** = regulation value or traced EXE code,
**MEASURED** = computed by the commands below from the regulation and the corpus mirror,
**INFERRED** = a modelling choice or an untraced consumer. Nothing was launched.

Tool: `scripts/er-mechanics-exchange.py` (`--selftest` passes 35/35 with the de-Arxan'd images present, 30/30 without). `er-builds-pvp.py` shows
startup (first active frame), hyperarmor and stamina per slot, and its `--sort score` reads none
of them. This module turns the three into factors measured against the attacks the RL window's
PvP builds actually carry.

## 1. The opponent pool (MEASURED)

Every PvP build of RL 140-160 (`er-builds-pvp.is_pvp`: `isPvE` false, or unset with a PvP tag),
RL equal to its attributes, deduplicated on (user, equipped tokens) as
`er-builds-embed.load_corpus` does. Each build contributes one attack: the R1 #1 of its first
right-hand weapon (active-set position 0-2) that has an R1 hit, in the build's grip (`is2h`), and
it keeps its own armor poise (`computed.poise.altered`, else `original`, menu units). Which attack
a real opponent throws is not in the corpus; R1 #1 is the one every moveset has (INFERRED).

| | value |
|---|---|
| PvP builds in the window | 1065 |
| in the pool | 945 (310 distinct weapon-grip R1s) |
| dropped | 79 no right-hand weapon with an R1 hit (seals, staves), 47 RL disagrees with attributes, 29 duplicates, 3 names not in the regulation |
| pool R1 first hit, percentiles 10/25/50/75/90 | 13.0 / 14.0 / 15.9 / 17.7 / 18.0 real frames |
| median `computed.maxStamina` | 145.0 |
| pool median R1s per median bar | 9.67 |

Most carried: Icon Shield 1H 37, Greatsword 1H 32, Greatsword 2H 30, Zweihander 1H 24, Fire
Knight's Greatsword 1H 20, Milady 1H 16, Great Katana 2H 15, Lance 1H 15, Misericorde 1H 15.
Uniques and shields are in the pool: it is who you fight, not what the grease sweep can build.

`Pool` also carries, per row, `dmg`: the hit that opponent lands on the scored player, the
`r1_1` `dmg` of the ranking row for its weapon and grip (`Pool.set_damage`, read from
`--opponents-from` the way `er-mechanics-ashes.opponents_from_results` reads it), else
`OPPONENT_HP_FALLBACK` 388. And per entry a weight, None for the plain pool. `Pool.weighted`
builds a pool whose rows are (profile, opener) pairs and whose entries split each build over the
openers it throws (`--opponent-pool families`, neutral.md section 6); `exchange()` then takes
weighted means.

Per pool attack (same expressions as `er-builds-pvp.slot_hit`):
- first hit frame: the main judge's first window, or an earlier separate sweep hit (real frames,
  TAE 608 play speed applied by `er-mechanics-attacks`);
- PvP poise damage, menu units: `poise_damage x 10 x FinalDamageRateParam[finalDamageRateId].saRate`
  (consumer VERIFIED at 0x140486bf0 per attacks.md section 2);
- hyperarmor: TAE 795 windows, bonus `100 x correctionRate x toughnessCorrectRate` (x10 menu) and
  ToughnessParam `unk1` (0.45 rows x1, 0.65 rows x0), VERIFIED handler per attacks.md section 2.

## 2. The exchange

Both players start an attack on the same frame (INFERRED: the brief's "simultaneous exchange";
an offset distribution would be a free weight). Whoever's strike frame is earlier strikes.

The strike frame (`strike_frame`, the same rule for the scored slot and every pool attack) is the
real frame the hitbox first touches a defender 2.5 m straight ahead of the attacker's start
facing (`er-mechanics-reach` `front_contact_frame_real`; the 2.5 m distance is INFERRED). Plus
the R2 release lead-in, and plus the entry frames for a crouch or running attack. When the reach
module has no pose for the attack, it falls back to the first hit frame. A horizontal sweep opens
its window off to the side and reaches the front later: Giant-Crusher 2H R1 is 17.9 at window
open but 18.9 at 2.5 m, and Greatsword 2H R1 goes from 16.7 to 18.2. The pool cache is
`POOL_VERSION` 3. The results in section 5 predate this, so they use first hit frames.

The
struck side is interrupted only when that hit's PvP poise damage, times the struck side's `unk1`
if its window covers that frame, reaches its armor poise plus the window's bonus; otherwise it
keeps swinging and both hits land. That is `er-mechanics-frame-advantage.trade` (window half
open, `start <= frame < end`), run over the pool. The attacker's own armor is not known for a
sweep build, so "they break me" is the share of the corpus poise distribution at or below
`dealt - bonus` (INFERRED: the attacker wears a corpus-typical set). Poise is full at the start
of the exchange (INFERRED).

```
win   = I hit first and interrupt them
loss  = they hit first and interrupt me
trade = both land (same frame, or the first hit did not break the struck side)
net   = P(win) - P(loss)                                  in [-1, 1]
f_exchange = 1 + EXCHANGE_WEIGHT * net                    EXCHANGE_WEIGHT = 0.25, INFERRED
f_startup  = 1 + EXCHANGE_WEIGHT * (P(first) - P(second)) first hit always wins: startup alone
f_hyper    = f_exchange / f_startup                       what poise and hyperarmor change
```

`f_startup x f_hyper = f_exchange` exactly (selftest), so the score multiplies `f_exchange` once.
`EXCHANGE_WEIGHT` 0.25 gives the factor the span of the existing frame-advantage factor
(`SCORE_ADV_WEIGHT` at +-30 frames). By default a trade counts 0 in `net`; `--trades priced`
prices it (section 2a).

### 2a. Priced trades (`--trades priced`, `priced_net`)

A trade is two hits landing, so what it is worth depends on whose hit is bigger. The opponent's
hit is the pool row's `dmg` (section 1: the ranking's own corpus-mean damage of that weapon and
grip, so a hit on a corpus-typical armor set, the same footing as the scored slot's `dmg`). Per
pool entry k:

```
trade_k = 1 - win_k - loss_k
hp_k    = (win_k + trade_k) x D_me - (loss_k + trade_k) x D_k
net_hp  = mean_k [ hp_k / ((D_me + D_k) / 2) ]       = win - loss exactly when D_me = D_k
f       = 1 + EXCHANGE_WEIGHT x clamp(net_hp, -TRADE_CLAMP, TRADE_CLAMP)
```

The same expression replaces `win - loss` in `f_exchange` here and in `f_neutral`
(neutral.md section 3), for slots, jump openers, skill options and the dodge-skill rerun.
`f_startup` is unchanged, so `f_hyper = f_exchange / f_startup` now carries the trade price too.
Without `--opponents-from` every `D_k` is 388 (`OPPONENT_HP_FALLBACK`).

**The clamp, `TRADE_CLAMP` = 1 (INFERRED, decided on the measurement below).** On the RL 150
ranking (822 rows, `scripts/er-builds-trade-dig.py`, the stored slots re-scored) the priced net of
the scored neutral contests has p1/p10/p50/p90/p99 = -1.35 / -1.07 / -0.49 / +0.65 / +1.06, min
-1.47, max +1.27; the clamp binds on 398 of 2371 contests, 338 of them on the losing side (a
light hit trading into a heavy one). Clamped and unclamped rank alike: |rank change| between them
median 0, p90 1, max 21, and adoption rho .4620 against .4622 (CI of each against unpriced
[-.0036, +.0076] / [-.0036, +.0079]). So the corpus cannot choose. The clamp is kept because it
leaves the contest factor the span it had unpriced, 1 -+ 0.25, the span the other factors were
weighed against; unclamped, pricing would also raise the contest's weight (to about 0.63 - 1.32)
under cover of a correctness fix.

Measured effect, clamp 1, against unpriced:
- trade share of the neutral contest median 0.13 (p10/p90 0.09 / 0.40); of the 2.5 m exchange
  0.33 (0.12 / 0.63);
- |rank change| median 5, p90 20, max 67; top 20 keeps 17, top 30 keeps 26;
- adoption rho, all weapons .4601 -> .4620 (d +.0019, CI [-.0036, +.0076]), adopted .2638 ->
  .2627 (CI [-.0135, +.0125]): neither confirmed nor refuted;
- by class, mean rank change: colossal weapons +21.5, colossal swords +17.7, great hammers +8.5,
  twinblades +5.6, greataxes +4.1; daggers -16.4, flails -15.7, thrusting swords -9.6, heavy
  thrusting -8.5. A hyperarmor heavy trades a 600-700 hit for a ~390 one (Troll's Hammer 2H R1 net
  +0.68 -> +1.01); a dagger trades 253 for 390 (-0.51 -> -0.73). Giant-Crusher 2H 32 -> 22,
  Great Club 2H 85 -> 52, Moonveil 1H 33 -> 53, Dagger 2H 114 -> 148.

The default stays `zero`: the selftests pass, but the corpus comparison neither supports nor
refutes pricing, and the opponent damage it needs is only real with `--opponents-from` (a second
pass). A default run is byte-identical with and without this code.

**Full run, both flags (MEASURED 2026-10-01).** `er-builds-pvp.py --rl 150 --sort score --json
--jobs 4 --opponents-from <the 2026-10-01 RL 150 ranking> --opponent-pool families --trades
priced` against the same command with neither flag at the previous branch head (822 rows each;
37 minutes for the new run and 52 for the reference, on a box at load 30). The jump-family fix of ashes-of-war.md section 16c is in
the new run only, and moved no chosen skill on a five-weapon check.

- Spearman 0.998; |rank change| median 8, p90 23, max 73; top 20 keeps 18, top 30 keeps 26.
- Scores x0.961 median (p10 0.919, p90 0.992); base (moveset) scores x0.953.
- Adoption rho, all weapons .4633 -> .4614 (d -.0019, CI [-.0079, +.0040]); adopted .2593 ->
  .2527 (d -.0066, CI [-.0217, +.0082]). Not supported, not refuted; the defaults stay `r1` and
  `zero`.
- Mean rank change by class: colossal swords +22.5, colossal weapons +19.0, claws +15.3, spears
  +6.2, fists +6.1, halberds +4.8; flails -25.2, curved greatswords -16.5, daggers -16.0, katanas
  -8.8, reapers -8.3, greatswords -7.0, thrusting swords -5.9.
- Risers: Grafted Blade Greatsword 2H 239 -> 174, Bloodhound Claws 2H 224 -> 159, Troll's Hammer
  2H 218 -> 155, Gazing Finger 2H 135 -> 81, Anvil Hammer 2H 149 -> 95, Shadow Sunflower Blossom
  2H 27 -> 12, Fire Knight's Greatsword 2H 25 -> 15, Giant-Crusher 2H 30 -> 21. Fallers: Shamshir
  1H 367 -> 440, Flail 1H 159 -> 207, Bloodhound's Fang 1H 173 -> 217, Gargoyle's Black Halberd
  2H 465 -> 508, Misericorde 2H 196 -> 229. The top 2 hold (Dane's Footwork 2H, Dryleaf Arts 2H).

Outputs: `/tmp/claude-1000/-home-banon-projects-er-mods-rs/d2b82757-dbef-4576-8664-fb4d80352bf7/scratchpad/next/full/{ab-ref,full-new}.json`.

**That top 2 does not survive the hit priority records (MEASURED 2026-11-10).** Both throw two
fists per strike, and both fists of a strike sit in one TAE 341 record, so one lands (bd
er-effects-rs-dnok, attacks.md). `er-builds-pvp.py --rl 150 --sort score --json --jobs 2 --sweep
~/.cache/er-build-planner/grease-sweep-dlc-drawstring-150-200.jsonl` (the plain run, neither
flag; the both-flags run was not repeated), once on the main tree's code and once with the
records, the only difference (820 scored rows each):

| row | without the records | with them |
|---|---|---|
| Dryleaf Arts 2H | 1 (859.1) | 35 (589.2) |
| Dane's Footwork 2H | 2 (853.3) | 288 (472.3) |
| Dane's Footwork 1H | 4 (699.9) | 524 (390.6) |
| Dryleaf Arts 1H | 10 (644.1) | 547 (377.2) |
| Backhand Blade 2H | 27 (608.0) | 321 (462.9) |
| Great Epee 2H | 3 (711.8) | 1 (711.8) |
| Pata 2H | 5 (694.4) | 2 (694.4) |

The fist rows fall through their moveset (Dane's Footwork 2H base 831.4 -> 468.1). Backhand Blade
falls through its skill term alone (base 443.4 both): Swift Slash's four bursts share one record
and its two blades another, so its credited value drops from 329.0 to 38.9 and Royal Knight's
Resolve becomes the best mountable skill. 102 of the 820 rows move by more than 0.05 and none
rises. Eight move through their moveset: the four fist rows and Carian Thrusting Shield and
Dueling Shield, both grips (running R2 halved, base down 4-10). The rest move through skill terms
(Night-and-Flame Stance, Shadow Sunflower Headbutt and others).

Also reported: `trade_through` = of the exchanges the opponent strikes first, the share the slot
keeps swinging through; `interrupts` = of the exchanges it strikes first, the share it staggers.

Rolling and backstep attacks (`INVINCIBLE_ENTRY_SLOTS`) get a neutral exchange. The roll's
invincibility is not modelled, and counting the entry frames as startup would score a rolling
attack as losing to an R1 it actually rolls through.

Crouch and running attacks have no invincibility, so they are exchanged like any other slot. They
start from standing: the entry frames (`SCORE_ENTRY_FRAMES`: crouch 8, run 20) are added to the
first hit and to the hyperarmor windows. For example, Giant-Crusher's 2H crouch R1 hits on frame
14, so it counts as 22 against the standing R1's 17.9 (14 if the player is already crouched; that
the exchange starts before the crouch or sprint is INFERRED). The entry cost also stays in the
slot's commitment.

### 2b. The slot's own guard (2026-11-13)

The user: "Do you place any value on being able to block and attack simultaneously?", after the
2H Carian Thrusting Shield was ranked by damage per second; and, ground truth, "Thrusting shields
can block and attack at the same time". Until now no exchange read it.

A thrusting shield's attack clips hold TAE JumpTable 3, the guard flag a raised shield sets,
from the hit frame through the recovery (R1 #1: hit f13-16, guard f14-45; 2H R1 #1 f13-17,
f14-55) and through its R2s' windup (charged R2 #1 f10-37, hit f47), and a few shield slots hold
short windows before their hit (powerstance-guard.md section
3a has every clip, how each is reached and what a guarded hit does). `guard_windows(atk, shift)`
reads them off the slot's clip (ungated events, real frames, moved by the lead-in and entry as the
hyperarmor windows are), and `exchange(..., guard=...)` reads them:

```
his hit lands in my guard          blocked: no poise break; he lands its guarded chip (taken as 0)
  after my hit landed              my hit stands unanswered                 -> win (as a whiff)
  before it, the state goes on     my hit lands later, unanswered           -> win
  before it, the state ends        the guard reaction ends my attack        -> void: neither lands
```

Every slot is a state the guard reaction ends (`guard_goes_on` False; only the two guard-poke
states, which no slot names, go on, VERIFIED c0000.hks). `void` is neither a trade nor a loss: it
counts 0 in `net` as a trade does, and `priced_net` leaves it out of the trades (`void_b`). The
chip is left out: through the Carian Thrusting Shield +25 the RL 150 pool's R1s pass a median 6% of
their damage (p90 31%), and one never breaks the guard (at most 35 stamina of the pool's 146 bar),
MEASURED with `er-mechanics-spells.guard_block`. The pool's own guards are not read: an opponent
thrusting shield's R1 that hits me first and does not break me still trades with my hit, though
my hit may land in his guard (INFERRED that it is rare: it needs his strike before mine and mine
inside his f14-45).

What it moves (MEASURED 2026-11-13, `slot_exchange` as the `--weapon` CLI calls it, RL 140-160
pool of 890 builds, unpriced trades, no entry frames; "before" is `.worktrees/sim-interrupt`'s
code, the same pool). The three thrusting shields give the same numbers to the third decimal:

| slot (Carian / Dueling / Ritual Thrusting Shield) | strike | win / trade / loss before | after (void) | blocked | f_exchange |
|---|---|---|---|---|---|
| R1 #1 | 13.0 | 0.635 / 0.352 / 0.014 | 0.931 / 0.055 / 0.014 | 0.884 | 1.155 -> 1.230 |
| R1 #2 | 15.5 | 0.337 / 0.461 / 0.202 | 0.631 / 0.166 / 0.202 | 0.536 | 1.034 -> 1.107 |
| R1 #4 | 18.0 | 0.089 / 0.390 / 0.521 | 0.288 / 0.192 / 0.521 | 0.288 | 0.892 -> 0.942 |
| 2H R1 #1 | 14.5 | 0.493 / 0.490 / 0.016 | 0.884 / 0.099 / 0.016 | 0.884 | 1.119 -> 1.217 |
| 2H R1 #2 | 15.0 | 0.440 / 0.524 / 0.035 | 0.782 / 0.182 / 0.035 | 0.782 | 1.101 -> 1.187 |
| running R1 | 17.0 | 0.175 / 0.377 / 0.447 | 0.374 / 0.178 / 0.447 | 0.288 | 0.932 -> 0.982 |
| R2 #1 (released) | 27.0 | 0 / 0.121 / 0.879 | 0 / 0.043 / 0.091 (0.866) | 0.866 | 0.780 -> 0.977 |
| charged R2 #1 | 47.0 | 0 / 0.121 / 0.879 | 0 / 0 / 0 (1.000) | 1.000 | 0.780 -> 1.000 |
| 2H R2 #2 | 23.0 | 0 / 0.371 / 0.629 | 0 / 0.006 / 0.057 (0.937) | 0.937 | 0.843 -> 0.986 |
| jump R1 | 11.0 | 0.661 / 0.339 / 0.001 | 0.957 / 0.042 / 0.001 | 0.931 | 1.165 -> 1.239 |
| 2H backstep R1 | 23.0 | 0 / 0.339 / 0.661 | 0 / 0.276 / 0.655 (0.069) | 0.069 | 0.835 -> 0.836 |

Longsword R1 #1 and 2H R1 #1 and the Kite Shield's R1s are unchanged (no window); the Kite Shield's
running R1 (guard before its hit) goes 0.811 -> 0.922 and its backstep R1 0.991 -> 1.007, all void.

So the thrusting shield's R1 turns most of its trades into wins: its f13 thrust already wins
63.5% (first and breaking), and the pool R1 that answers it (median strike frame 16.5) lands
inside the f14-45 guard. Its R2s, guarded through the windup, stop losing to the R1s that
used to break them and lose their own hit instead (void). The rank this moves is the PvP
ranking's (`er-builds-pvp.py`), which multiplies `f_exchange` into each slot. That ranking was not
re-run: `er-builds-pvp.py --weapon "Carian Thrusting Shield"` stops on the missing sweep cache
`grease-sweep-dlc-drawstring-150-200-cond-floors.jsonl` (2026-11-13).

Not read: the R2's lead-in clip's own guard (the slot's clip is read, the lead-in shifts it, as
the hyperarmor windows are), the chip and stamina of a blocked hit, a guard break, and the pool's
own guards (above).

## 3. Stamina

The old factor, `clamp((swings per bar / pool median swings per bar) ** 0.25, 0.8..1.1)`, counted
swings and ignored what a swing is worth and when the bar refills. It is retired; it survives as
`stamina_factor_swings` for comparison (`f_stamina_swings`, and `--rank stamina-swings`).

### 3a. Cost (VERIFIED, unchanged)

```
cost = stamina_cost x main-judge windows + stamina_cost of every extra damaging hitbox
       + the entry's own cost: roll 12, backstep 8, jump 10
```

`FUN_1404428f0` charges the cost each time an AttackBehavior event creates a hitbox (attacks.md
section 3), so a slot with two events pays twice; the summation is INFERRED from that and matters
for 53 of 1515 one-handed slots. The entry costs are HKS `common_define.hks`
`STAMINA_REDUCE_ROLLING` -12, `STAMINA_REDUCE_BACKSTEP` -8, `STAMINA_REDUCE_JUMP` -10, charged by
`AddStamina` in `ExecEvasion` / `ExecJump`.

### 3b. Regeneration (VERIFIED on 1.16.2, rate re-read on 1.17.1)

- **Player rate is a constant, not NpcParam.** `FUN_1404016d0` (per-frame `ChrIns` tick, called
  by both `PlayerIns` and `EnemyIns` updates) adds
  `GetStaminaRecoverySpeed x SPRegenRatePercent / 100 x staminaRecoveryModifier` scaled by the
  frame's `FD4Time`, carrying the fraction. `PlayerIns::GetStaminaRecoverySpeed` 0x1406566b0
  returns `45.0` (float at 0x143b33c08, its only reader; 1.17.1 0x140657500 -> 0x143b37c18, also
  45.0) plus the SpEffect `staminaRecoverChangeSpeed` sum (turtle talismans, Greenburst).
  NpcParam `staminaRecoverBaseVel` (row 1: 21) is read only by `EnemyIns::GetStaminaRecoverySpeed`
  0x1404cf9e0; it never reaches a player. The unit is per second of game time, so 1.5 per real
  30 fps frame (VERIFIED 1.16.2 named dump, every hop byte-matched in 2.7.2.0 and checked by
  `er-mechanics-exchange.py --selftest`; 2.7.2.0 addresses):
  - the tick multiplies its rate by `FD4Time::SetScaled` 0x140188b80, `rate x frameDelta.time`
    (`mulss xmm1, [r8+8]`, called at 0x140401bad);
  - `frameDelta` is `ChrIns+0xb0`, loaded into the `FD4Time` the vtable slot 0x88 update gets
    (0x1403f9434); `WorldChrMan_ConstructUpdateList` copies it from its task's
    `FD4TaskData.delta_time` (0x14051151a; an omitted far character gets N x it every Nth frame);
  - `FD4TaskGroup::ExecuteTask` builds that `FD4TaskData` from a global (0x1426d94ce) that
    `FD4TaskManager` stores from the time `MainUpdate` hands it each frame (0x14265933d):
    `CSFlipper.gameSpeed x taskDelta` (0x140deda87), or a fixed 1/30 s while `dynamicFpsActive`
    is clear or in transition; `gameSpeed` is 1.0, its only store the `CSFlipperImp` constructor;
  - `CSFlipperImp::UpdateFlipTiming` sets `lastFrameTime` = (QPC now - QPC before) /
    `QueryPerformanceFrequency` (0x140e865d2), seconds, and `taskDelta` = the sum of the last
    16 `lastFrameTime`s x 0.0625 (0x140e867c1).
- **Equip load scales it.** `staminaRecoveryModifier` (`ChrIns+0xe8`) is 0.8 at heavy load
  (`GetWeightType` 3), 0.7 overloaded (4) and 1.0 below (the `PlayerIns` update, store
  0x140661a2d in 2.7.2.0, VERIFIED; the tick's multiply is `mulss xmm6, [rdi+0xe8]` at
  0x140401b81, powerstance-guard.md section 7). The lines this module scores are at medium roll,
  so 1.0. The guard model's corpus blockers take theirs from the load their own rows hold
  (powerstance-guard.md section 8, 2026-11-13).
- **There is no timer-style delay.** The pause is the animation's own TAE event 225
  `SetSPRegenRatePercent`: the inline case at 0x14042e3b1 of `ExecuteThreadOne` stores Args[0]
  into `ChrCtrlModifier+0x14`, and `ChrCtrlModifierData::Reset` 0x1403c3b60 (from
  `PreBehaviorSafe`) writes it back to 100 every frame. So regeneration runs at the percent the
  current clip sets, frame by frame, and at 100 when no event covers the frame. (talismans.md
  section 4 calls this "TAE 255"; the dispatch table and the template both say 225.)
- **Attacks: 0% from frame 0 for most of the clip.** Every 2H R1 below carries one 225 event at
  0% from clip frame 0: Giant-Crusher to clip 62 (real 54.9), Greatsword 56 (51.7), Hand Axe 33
  (31.0), Claymore to real 39.0, Lance 41.0. Each window runs past the slot's cancel frame, so
  chained attacks regenerate nothing, and after the last one regeneration resumes at
  `min(window end, move-cancel frame)`.
- **Rolls and backsteps:** a000 roll clips (27100..) 0% for 30 clip frames, backsteps (27000..)
  26.
- **Guarding: 20%.** a000 entries 100, 110, 120 and 160 carry 225 at 20% (that they are the guard
  clips is INFERRED from their ids and value).
- **Stopped outright** by HKS `act(110)` (`HksAct` case 110 `SetStaminaRecoveryDisabled`, `or
  actionFlags, 0x40`, which `FUN_1404016d0` tests): in every `GuardDamage*` state, in
  `SpeedUpdate` (sprinting) and in HKS `AddStamina` (rolls, backsteps, jumps, quickstep). Guard
  stamina damage itself is the guard module's (powerstance-guard.md). A guard reaction holds
  `act(110)` until a blocker holding L1 is let back into the guard on the reaction clip's first
  TAE JumpTable 22 frame, f16 / f32 / f53, later than its TAE 0% span (powerstance-guard.md
  section 7, 2026-11-13).
- **Floor and gates.** `CSChrDataModule::SetStamina` 0x140438490 clamps to [-50, max]: an action
  started with 1 stamina left can end at -49. `GetEvasionRequest` and `ExecGuard` refuse at
  stamina <= 0 (`STAMINA_MINIMUM` 0), so a roll needs stamina above 0, not above 12.
  `ExecAttack` only resets the combo at <= 0; that the attack itself needs > 0 is INFERRED.
- **Bar.** The corpus median `computed.maxStamina`, 145 in RL 140-160 (resources.md section 2,
  curve 104: Endurance 30 -> 130, 50 -> 155; 145 is Endurance 42).
- **The build's add (2026-11-13).** The budget regenerates at `regen_per_frame(add)` = (45 + add) /
  30 a real frame at 100%, `add` the attacker's SpEffect `staminaRecoverChangeSpeed` sum
  (`slot_stamina` / `slot_exchange` `regen_add`), read off his own equipment only: the talismans the
  caller names (`stamina_regen_add`, through `er-mechanics-talismans.stamina_regen_add`;
  `er-builds-pvp.py --talismans`, its skills' budget too, and this module's `--weapon ...
  --talismans`) and the weapon he scores (`weapon_regen_add`). A regen item is a choice, never a
  default (the user, 2026-11-13: "Its not like people will always use either talisman, or the
  shield for increased regen"): the sweep rows the ranking scores carry no talisman, so a run
  without the flag regenerates 45/s unless the scored weapon is the shell. The items, REGULATION
  scan of every equipment param: Green Turtle Talisman 8 (SpEffect 311500), Two-Headed Turtle
  Talisman 10 (20370300), the Great Turtle Shell 3 in every affinity (resident SpEffect 5311400,
  "[Weapon] Great Turtle Shell - Boost Stamina Recovery"), no armor. Not applied, since no model
  here applies a buff's regeneration: Braggart's Roar +10 (+7 with no FP, ashes-of-war.md section
  5), Flame, Grant Me Strength +5, the Greenburst Crystal Tear +15, the Pickled and Well-Pickled
  Turtle Necks +10 / +13. A model that picks talismans for a line prices none of these:
  `er-mechanics-line-fit` offers only talismans that move a term its D reads (HP, FP, equip load,
  stats, cuts, poise, the spell), and stamina is not one; `er-weapon-build.py` ranks talismans by
  corpus co-occurrence (`er-mechanics-gear-synergy`) and bans the regen items in a duel
  (`REGEN_EQUIPMENT`).

  What one would change, side by side and not applied (MEASURED 2026-11-13, `slot_stamina` on the
  RL 140-160 pool's 146 bar, W = 10 s; the plain column equals `.worktrees/sim-interrupt`'s code to
  the last digit):

  | slot | f_stamina plain | with Green Turtle (+8) | with Two-Headed Turtle (+10) | roll lock after the burst |
  |---|---|---|---|---|
  | Giant-Crusher 2H R1 | 0.606 | 0.618 | 0.623 | 31.25 -> 30.25 / 30.04 |
  | Giant-Crusher 2H charged R2 | 0.654 | 0.654 | 0.654 | 59.98 -> 54.85 / 53.80 |
  | Greatsword 2H R1 | 0.763 | 0.763 | 0.763 | 30.32 -> 28.61 / 28.26 |
  | Dagger R1 | 0.862 | 0.871 | 0.873 | 13.33 -> 12.53 / 12.36 |
  | Carian Thrusting Shield R1 | 0.842 | 0.854 | 0.856 | 11.33 -> 10.83 / 10.73 |
  | Carian Thrusting Shield charged R2 | 0.889 | 0.901 | 0.903 | 33.33 -> 29.81 / 29.09 |
  | Claymore R1 | 0.900 | 0.900 | 0.900 | 16.29 -> 15.78 / 15.68 |
  | Claymore rolling R1 | 0.702 | 0.726 | 0.731 | 29.37 -> 27.05 / 26.58 |

  Where the window ends on a whole swing the factor does not move (Greatsword 2H R1, Claymore R1);
  elsewhere a turtle adds 1-3%. The regeneration a slot's clip blocks (TAE 225 at 0% from frame 0)
  is blocked for the turtle too: the add speeds only the frames the clip leaves above 0% and the
  walk-off waits.

### 3c. The budget and the new factor

`stamina_budget` plays one attacker repeating the slot for a fight window `W` from a full bar:

```
swing when the previous commitment T is over and stamina > cost    (keeps a roll: INFERRED policy)
  stamina -= cost (floor -50), + the clip's own regeneration inside T
otherwise walk off at the move-cancel frame and wait until stamina > cost
N(W)      = swings started in W, the last one pro rata
f_stamina = N(W) * T / W              in (0, 1]
score     = rate * f_stamina = dmg * N(W) / W   (damage per second the build keeps up)
```

`T` is `slot_score`'s commitment (entry + earlier of same-button and roll cancel). No exponent, no
clamp and no pool reference: the factor is a physical share. `W` = 10 s (`FIGHT_WINDOW_S`,
INFERRED) is the one free parameter; it sets how much of the fight the bar covers before regen
governs. Once the bar is spent, one swing costs `min(window end, move cancel) + cost / 1.5`
frames, so the sustained rate is `dmg / (Z + cost / 1.5)`: damage per stamina, plus the frames
each clip blocks regeneration. That is the term the old factor lacked.

Also reported per slot: `burst` (greedy chained swings from a full bar until stamina is gone),
`burst_end`, `lock_frames` (frames after the last commitment before stamina is back above 0,
the defender's free punish window beyond ordinary recovery), `burst_safe` (swings that keep a roll),
`dmg_per_bar = dmg x bar / cost`, `dmg_window`, `dmg_burst_safe`.

### 3d. Measured (RL 140-160 pool, bar 145, W = 10 s)

MEASURED 2026-09-30 from `--rank none --dump` (each slot's best-scoring build row; `dmg` is
`slot_hit`'s mean over the corpus defenders). Frames are real 30 fps; `Z` is where the 0% window
ends.

| 2H slot | cost | T | Z | dmg | dmg / stamina | dmg / bar | swings / bar | f old | f new | swings in 10 s | greedy burst (end, lock) | swings keeping a roll |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Giant-Crusher R1 | 31 | 30.3 | 54.9 | 937 | 30.2 | 4385 | 4.68 | 0.834 | 0.606 | 6.0 | 5 (-10, 31.9 f) | 4 |
| Greatsword R1 | 27 | 32.7 | 51.7 | 828 | 30.7 | 4448 | 5.37 | 0.863 | 0.763 | 7.0 | 6 (-17, 31.0 f) | 5 |
| Hand Axe R1 | 13 | 15.0 | 31.0 | 510 | 39.2 | 5683 | 11.15 | 1.036 | 0.700 | 14.0 | 12 (-11, 24.0 f) | 11 |
| Claymore R1 | 18 | 25.0 | 39.0 | 583 | 32.4 | 4699 | 8.06 | 0.955 | 0.827 | 9.9 | 9 (-17, 26.0 f) | 8 |
| Lance R1 | 22 | 30.0 | 41.0 | 547 | 24.9 | 3608 | 6.59 | 0.909 | 0.800 | 8.0 | 7 (-9, 17.7 f) | 6 |

What the data says about the lead (Giant-Crusher's cost against its damage):

- Per stamina point, Giant-Crusher's R1 is not efficient: 30.2 damage, level with Greatsword
  (30.7) and Claymore (32.4), below Hand Axe (39.2), above Lance (24.9). Per bar it delivers 4385,
  about the same as Greatsword and Claymore. So "counts swings, ignores damage" was the right
  complaint about the old factor, but correcting it does not make Giant-Crusher cheap.
- What sets Giant-Crusher apart is regeneration: its R1 blocks regen for 54.9 frames, the longest
  of the five. Once the bar is spent, each R1 costs 54.9 + 31 / 1.5 = 75.6 frames, against 39.6
  for Hand Axe and 51.0 for Claymore.
- The old factor rewarded fast weapons (Hand Axe x1.036) for their swing count. But their 0%
  windows are also about twice their commitment, so on a 10 s budget they run out too (Hand Axe
  0.70). The penalty on Giant-Crusher goes up in absolute terms (0.834 -> 0.606), but the gap to
  light weapons shrinks.
- An empty bar costs every weapon about the same: the greedy burst leaves the attacker 17.7-31.9
  frames past recovery unable to roll. That is the defender's free punish window, and it would
  need the opponent's damage to price (same unknown as trades).

### 3e. Rank changes (`er-builds-pvp.py --rl 150 --sort score`, moveset score)

`python3 scripts/er-mechanics-exchange.py --rank <mode> --rl 150 --top 400`, collected by
`scripts/er-exchange-stamina-report.py`. The rank is the weapon's best 2H row:

| weapon (2H) | no factor | old stamina | new stamina (10 s) | new, 5 s | new, 20 s | old stamina x exchange | new stamina x exchange |
|---|---|---|---|---|---|---|---|
| Giant-Crusher | 24 (823) | 47 (731) | 16 (675) | 51 (737) | 12 (631) | 72 (712) | 30 (655) |
| Greatsword | 63 (744) | 85 (678) | 28 (651) | 76 (707) | 21 (597) | 110 (671) | 41 (639) |
| Hand Axe | 19 (843) | 18 (825) | 87 (594) | 16 (843) | 170 (493) | 15 (926) | 34 (651) |
| Claymore | 36 (785) | 81 (682) | 52 (620) | 65 (716) | 53 (563) | 86 (691) | 59 (622) |
| Lance | 325 (574) | 352 (540) | 230 (524) | 304 (574) | 183 (488) | 365 (520) | 261 (504) |

Top 10, new stamina x exchange: Dane's Footwork 1H/2H, Dryleaf Arts 2H, Backhand Blade 2H,
Dryleaf Arts 1H, Backhand Blade 1H, Pata 2H, Raptor Talons 2H, Venomous Fang 2H, Jawbone Axe 2H.
Not rerun: `er-mechanics-exchange.py` no longer has `--rank`. In the 2026-11-10 default
`er-builds-pvp.py --rl 150 --sort score` ranking, which carries `f_exchange x f_stamina` among
its later factors (section 2a's last table, with the hit priority records), those rows are
Dane's Footwork 1H/2H 524th/288th, Dryleaf Arts 2H/1H 35th/547th, Backhand Blade 2H/1H
321st/534th, Pata 2H 2nd, Raptor Talons 2H 12th, Venomous Fang 2H 30th, Jawbone Axe 2H 17th.
Colossal and great weapons move up against light axes and clubs; the hand-to-hand and claw R2s
move up most.

The window `W` decides the colossal-versus-axe order: at 5 s the bar covers almost everything
and Hand Axe is back at 16, at 20 s it falls to 170. The old ^0.25 exponent was an unmeasured
weight; `W` is an unmeasured length with a physical reading (how long one player keeps up
pressure before a neutral reset). No extra weight is needed, but `W` has to be chosen, and the
corpus does not contain it. Measuring it would need fight recordings.


## 4. Measured values (RL 140-160 pool)

`python3 scripts/er-mechanics-exchange.py --rl 150 --weapon <name> --grip <one|both>`:

| slot | first | poise | win / trade / loss | P first | trade-through | per bar | f_startup | f_hyper | f_stamina | product |
|---|---|---|---|---|---|---|---|---|---|---|
| Giant-Crusher 2H R1 | 17.9 | 819 | 17 / 65 / 18% | 17% | 78% | 4.7 | 0.837 | 1.190 | 0.834 | 0.831 |
| Greatsword 2H R1 | 16.7 | 655 | 37 / 62 / 1% | 37% | 98% | 5.4 | 0.949 | 1.150 | 0.863 | 0.942 |
| Giant-Crusher 2H R2 | 40.0 | 635 | 0 / 29 / 71% | 0% | 29% | 2.8 | 0.750 | 1.098 | 0.800 | 0.659 |
| Greatsword 2H R2 | 21.0 | 721 | 0 / 77 / 23% | 0% | 77% | 3.6 | 0.750 | 1.255 | 0.800 | 0.753 |
| Dagger 1H R1 | 10.0 | 41 | 11 / 89 / 0% | 95% | 4% | 16.1 | 1.238 | 0.829 | 1.100 | 1.129 |

(All MEASURED. The R2 first frames include the charge-start lead-in.)

### Correction to giant-crusher-adoption-gap.md section 3

That doc says no adoption-weighted opponent R1 breaks Giant-Crusher's R1 hyperarmor in PvP. The
selftest reproduces that number, 100% trade-through, only when `saRate` is divided back out of
both sides: it was computed on poise without the PvP `saRate` multiplier. With `saRate` applied, as
`er-builds-pvp.slot_hit` and `er-mechanics-frame-advantage.pvp_poise_damage` now both do, the
greatsword-class R1s (504 menu 1H, 655 2H, x0.45 inside the window = 227 / 295) exceed a
corpus-typical 81 poise plus Giant-Crusher's +99, and 22% of the pool attacks that land first
break it (MEASURED). Those greatswords land first only because Giant-Crusher's first hit is 17.9
and theirs 16.7-17.7: the Greatsword 2H R1 at 16.7 beats them to it or ties, and keeps 98%
trade-through. So a 1.2-frame startup gap is where the twins separate in the exchange, not the
hyperarmor.

Both multipliers compose on one path (VERIFIED 2026-09-29, 1.16.2 dump, bd
`pvp-poise-damage-expression-sarate-unk1-2026-09-29`): `FUN_140486bf0` computes
`info[+0x244] * info[+0x100] * saRate * unk1` at 0x140486d27..d3e and subtracts
`cutRate * damageRatio * that` from toughness at 0x140486d78..d8e. saRate and unk1 start at 1.0
and change only under the player-vs-player gate; unk1 only while `toughness+0x28` (window) is
set, with the row read from `+0x1c` at 0x140486cfb. `+0x100` is the attacker's raw poise damage
(`FUN_14068af30`, no saRate in it) and `+0x244` is a hit-location rate (`partsDamageRate`, 1.0
unless `partsDmgType` or an arrow), so nothing is counted twice. The comparison is attacker float
against `100 x armor sum` with no conversion, so multiplying both sides by 10 for menu units is
consistent. The correction above therefore stands.

## 5. Effect on the RL 150 `--sort score` ranking

MEASURED 2026-09-29, `er-builds-pvp.py --rl 150 --sort score` against the same run with the
factors multiplied into `slot_score` (`--rank`, an in-memory patch; the ranking script itself is
unchanged). Rank (score):

| weapon, best slot | baseline | x f_exchange | x f_stamina | x both |
|---|---|---|---|---|
| Giant-Crusher 2H R1 | 1 (1706) | 3 (1700) | 1 (1423) | 16 (1418) |
| Prelate's Inferno Crozier 2H R1 | 2 (1620) | 5 (1614) | 4 (1362) | 23 (1357) |
| Golem's Halberd 2H R1 | 3 (1578) | 7 (1572) | 3 (1389) | 20 (1384) |
| Star Fist 2H R2 | 5 (1526) | 1 (1773) | 5 (1330) | 2 (1545) |
| Hand Axe 2H R1 | 19 (1343) | 9 (1551) | 2 (1392) | 1 (1607) |
| Greatsword 2H R1 | 22 (1330) | 19 (1451) | not in top 40 | 40 (1253) |

Top 10 with both factors: Hand Axe 2H, Star Fist 2H R2, Stone Club 2H, Iron Ball 2H R2, Iron
Cleaver 2H, Jawbone Axe 2H, Warped Axe 2H, Chainlink Flail 1H, Forked Hatchet 2H, Nightrider
Flail 1H. The colossal R1s were first on damage per frame of commitment. Their exchange factor
is close to neutral: they strike first rarely (`f_startup` 0.84), but they trade through most
attacks (`f_hyper` 1.19). What drops them is stamina, 4.7 R1s per bar against the pool's 9.7.
Fast, cheap axe and club R1s at 12 frames win first-strike exchanges and cost half as much. The
stamina exponent (0.25) and clamp are the least grounded numbers in this module, so the colossal
drop is set mostly by an INFERRED weight. The exchange factor on its own moves nothing more than
a few places.

## 6. Open unknowns

- The PvP gate passes for the main player or a `PlayerIns` with `characterEventId` 1..9997;
  the gate (`IsNpcChrEventID` 0x140657270) never calls `IsHostChrEventId`, and the guest-side
  packet 7 handler 0x140c9bf20 refuses ids outside 1..9997, so every session player passes and
  the model's "both always" is right (VERIFIED 2026-10-01, bd
  `pvp-sarate-gate-all-session-players-pass-2026-09-30`). Still INFERRED: that the host's id 1
  reaches the guest's copy of the host's player data.
- The opponent's attack is R1 #1 by default; `--opponent-pool families` throws the ranking's own
  family openers (neutral.md section 6), but no corpus source says which attacks players throw.
- Trades count 0 by default; `--trades priced` (section 2a) prices them with the ranking's
  corpus-mean damage of the opponent's weapon, not that build's own AR, and against corpus-typical
  armor on both sides.
- The attacker's armor poise is the corpus distribution, not the sweep build's (the sweep builds
  carry no armor).
- Rolling and backstep attacks: neutral exchange (invincibility not modelled). Crouch and running attacks are exchanged from standing.
- Chain follow-ups (`r1_2`, `r1_3`) are scored as if they opened the exchange from their own clip
  start; in a real chain the opponent already reacted to the first hit.
- Stamina (section 3): the fight window `W`, the keep-a-roll policy, walking (100%) rather than
  guarding (20%) while waiting, and repeating one slot rather than mixing. The punish exposure
  after an empty bar is measured in frames but not priced. The attack-start gate at stamina > 0
  is INFERRED (HKS `ExecAttack` only resets the combo). A slot whose clip the TAE does not hold
  regenerates 100% after its entry, as the game does through a clip with no TAE 225 event
  (`RegenProfile`); no scored slot has no clip. NpcParam `staminaRecoverBaseVel` is NPC-only (section 3b).
- Section 4's `f_stamina` column and section 5 are the retired factor.

## Commands

```bash
python3 scripts/er-mechanics-exchange.py --selftest
python3 scripts/er-mechanics-exchange.py --rl 150 --pool
python3 scripts/er-mechanics-exchange.py --rl 150 --weapon Giant-Crusher --grip both
python3 scripts/er-mechanics-exchange.py --rank all --rl 150 --top 30        # er-builds-pvp --sort score x all factors
python3 scripts/er-mechanics-exchange.py --rank exchange --rl 150 --top 30   # f_exchange only
python3 scripts/er-mechanics-exchange.py --rank stamina --rl 150 --top 30    # f_stamina only
python3 scripts/er-mechanics-exchange.py --rank stamina --rl 150 --stamina-window 20 --top 30
python3 scripts/er-mechanics-exchange.py --rank stamina-swings --rl 150 --top 30   # the retired factor
python3 scripts/er-mechanics-exchange.py --rank none --rl 150 --top 400 --dump /tmp/rank-none.json
python3 scripts/er-exchange-stamina-report.py <dir with rank-<mode>.txt/.json> --out <dir>/rank-stamina.json
```

`--rank` passes `--no-exchange` to the ranking, so the factors are applied once (until 2026-09-30
the ranking also applied them itself, and `--rank` applied them twice).

The pool is cached at `~/.cache/er-build-planner/exchange-pool-<lo>-<hi>.json`, keyed on the
mirror's size and mtime; `--no-cache` rebuilds it (about 40 s).
