# Exchange: startup, hyperarmor trades and stamina as score factors

Labels as in the other files here: **VERIFIED** = regulation value or traced EXE code,
**MEASURED** = computed by the commands below from the regulation and the corpus mirror,
**INFERRED** = a modelling choice or an untraced consumer. Nothing was launched.

Tool: `scripts/er-mechanics-exchange.py` (`--selftest` passes 30/30). `er-builds-pvp.py` shows
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
(`SCORE_ADV_WEIGHT` at +-30 frames). A trade counts 0 in `net` (INFERRED: its value depends on
both hits' damage, and the opponent's damage would need the opponent's AR).

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
  0x1404cf9e0; it never reaches a player. The unit is per second (INFERRED: `FD4Time` holds
  seconds), so 1.5 per real 30 fps frame.
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
  stamina damage itself is the guard module's (powerstance-guard.md).
- **Floor and gates.** `CSChrDataModule::SetStamina` 0x140438490 clamps to [-50, max]: an action
  started with 1 stamina left can end at -49. `GetEvasionRequest` and `ExecGuard` refuse at
  stamina <= 0 (`STAMINA_MINIMUM` 0), so a roll needs stamina above 0, not above 12.
  `ExecAttack` only resets the combo at <= 0; that the attack itself needs > 0 is INFERRED.
- **Bar.** The corpus median `computed.maxStamina`, 145 in RL 140-160 (resources.md section 2,
  curve 104: Endurance 30 -> 130, 50 -> 155; 145 is Endurance 42).

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
- The opponent's attack is R1 #1 only; no corpus source says which attacks players throw.
- Trades count 0; a damage-weighted trade value needs the opponent build's AR.
- The attacker's armor poise is the corpus distribution, not the sweep build's (the sweep builds
  carry no armor).
- Rolling and backstep attacks: neutral exchange (invincibility not modelled). Crouch and running attacks are exchanged from standing.
- Chain follow-ups (`r1_2`, `r1_3`) are scored as if they opened the exchange from their own clip
  start; in a real chain the opponent already reacted to the first hit.
- Stamina (section 3): the fight window `W`, the keep-a-roll policy, walking (100%) rather than
  guarding (20%) while waiting, and repeating one slot rather than mixing. The punish exposure
  after an empty bar is measured in frames but not priced. `FD4Time` counting seconds is
  INFERRED; the attack-start gate at stamina > 0 is INFERRED (HKS `ExecAttack` only resets the
  combo). NpcParam `staminaRecoverBaseVel` is NPC-only (section 3b).
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
