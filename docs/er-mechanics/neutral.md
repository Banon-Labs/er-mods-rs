# Neutral: who starts the exchange, and from where

Labels as in the other files here: **VERIFIED** = regulation value or traced EXE code,
**MEASURED** = computed by the commands below from game files, **COMMUNITY** = the Smithbox
decompile of `c0000.hks`, **INFERRED** = a modelling choice. Nothing was launched.

Tool: `scripts/er-mechanics-neutral.py` (`--selftest` passes 14/14). It is read by
`er-builds-pvp.py` (every slot and every skill option) and by `er-mechanics-ashes.py` (the dodge
and defensive-buff utilities, ashes-of-war.md section 17).

## 0. In plain words

The exchange (exchange.md) starts both players' attacks on the same frame with the defender 2.5 m
ahead. So a lance thrust that reaches 4.5 m gets nothing for its reach there, and a dagger is
never asked to walk in. Here the same exchange starts from the neutral game: both players stand
outside both reaches and both commit. Whoever reaches farther only has to swing; the other has to
close the difference first, by running or by rolling in. Then the first hit, poise and hyperarmor
decide it exactly as in the exchange. Reach turns into time: at run speed one metre costs 7.5 real
frames.

## 1. Movement (MEASURED, hkx root motion)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py movement`

| move | clip | speed or motion |
|---|---|---|
| walk | a000_020000-020003 (forward, back, both sides) | 1.50 m/s |
| run | a000_020100 (forward; 020110 / 020120 the same, 020130 2.79) | 4.01 m/s = 7.48 real frames a metre |
| sprint | a000_020200 (020210 / 020220 the same, 020230 4.13) | 6.04 m/s |

| dodge | clip | i-frames | first R1 | toward the other player at the R1 frame | end |
|---|---|---|---|---|---|
| medium roll forward / back | a000_027110 / 027111 | f0-13 | 20 | 3.23 / 3.64 m | 3.65 / 3.17 m |
| medium backstep | a000_027000 | none unconditional | 14 | 2.16 m (back) | 2.50 m |
| Bloodhound's Step forward / back | a756_040080 / 040081 | f0-10 | 17 / 16 | 3.80 / 3.96 m | 5.24 / 4.72 m |
| Quickstep forward / back | a755_040080 / 040081 | f0-9 | 17 / 16 | 3.25 / 3.11 m | 4.28 / 3.23 m |

The i-frames and the R1 frame are the TAE's (ashes-of-war.md section 14a); the distance is the
root motion at that frame. The step travels on after its R1 opens: 3.80 m at f17, 5.24 m at the
end.

Which speed a locked-on player moves at (COMMUNITY, `c0000.hks` `SpeedUpdate` /
`ChangeMoveSpeedIndex`): the stick sets `MoveSpeedIndex` 0 walk, 1 run (stick above 0.6), 2
sprint (the dodge button held); `MoveSpeedIndexBLR`, the index for back and side movement, is
capped at 1, and sprinting calls `LockonFixedAngleCancel`, which drops the lock-on facing. So a
locked-on player closes and backs off at run speed. The back and side run clips do not exist,
so that they also move at 4.01 m/s is INFERRED.

## 2. Threat (MEASURED)

`python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py pool --rl 150`

Each exchange-pool build (exchange.md section 1: 945 PvP builds of RL 140-160, 310 weapon-grip
R1s) gets its R1 #1 world reach, `er-mechanics-reach` `world_reach_m` (the root motion to the
farthest hit point included), the class median when the pose is missing, and its first hit's live
frames. Cached in `~/.cache/er-build-planner/neutral-pool-reach-140-160.json`, keyed on the pool.

| pool R1 #1 world reach | mean | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|---|
| m | 3.65 | 2.79 | 3.40 | 3.73 | 4.06 | 4.45 |

## 3. The race (`neutral_exchange`)

For an attack with strike frame `s` (the exchange's first contact 2.5 m ahead, entry and R2
lead-in included), world reach `R`, PvP poise damage and hyperarmor windows, against each pool
build `b`:

    D        = max(R, R_b)                          the players start where the longer reach is in range
    my start = arrival(D - R)                       frames before my attack can start
    b start  = arrival(D - R_b)
    arrival(g) = min(g x k,                          running, k = 30 / 4.01 frames a metre
                     R1 frame of a dodge + max(0, g - its distance at that frame) x k)
    my hit   = my start + s,   b's hit = b start + s_b

- **Both commit, both run (INFERRED).** Starting farther out adds the same running time to both
  while both run toward each other, so it cancels: only the difference in reach is run. A player
  who dodges in instead pays the dodge's R1 frame; the medium roll pays off above 2.67 m, the step
  above 2.27 m. Every player carries the medium roll (the sweep builds are at medium load); the
  step only when the ash is Bloodhound's Step (section 5).
- **i-frames.** A hit whose whole live window falls inside the other's dodge i-frames misses him
  (the rule `er-mechanics-ashes.Opponents._escapes` uses, without the distance part: he is
  closing).
- **Then the exchange.** First hit, and the same break rule as `er-mechanics-exchange.exchange`:
  the first hit wins when its poise damage (times the struck side's `unk1` if a hyperarmor window
  of his covers that frame, counted from his own attack's start, so the run-in is never armored)
  reaches his armor poise plus the window's bonus; otherwise both land. A hit on a player whose own
  hit whiffed into i-frames always wins.

      f_neutral = 1 + EXCHANGE_WEIGHT x (P(win) - P(loss))      EXCHANGE_WEIGHT 0.25 (exchange.md)

- **Where it enters the score.** `f_neutral` replaces `f_exchange` as the contest in
  `er-builds-pvp.slot_score` (the committed share of the hit's worth, section 16a of
  ashes-of-war.md). Under `--interrupt` the interrupt factor stays and is multiplied by
  `f_neutral / f_exchange`: the part the distance changes, on the same poise rule. A rolling or
  backstep attack keeps its neutral exchange (exchange.md). `--no-neutral` restores the 2.5 m
  contest.
- **Jumps.** The jump openers (`er-builds-pvp.jump_openers`, moveset.md section 6) are raced with
  the jump's own reach (its travel included) and its first hit counted from the jump input; the
  landed clip's delay from first hit to contact and its hyperarmor windows are moved onto that
  clock (INFERRED).
- **Skills.** A skill option is raced with its own measured reach (ashes-of-war.md section 15)
  and the contact and poise of `skill_contest_inputs` (section 17).

The strike frame is the contact 2.5 m ahead. That it also stands for the contact at the attack's
full reach is INFERRED: a lunge reaches its far end a little later, so a long lunge is slightly
favoured.

| attack | reach | 2.5 m exchange f | neutral f |
|---|---|---|---|
| Lance 2H R1 (Heavy+25) | 4.45 m | 0.978 | 1.153 |
| Lance 1H R1 | 4.45 m | 0.893 | 1.146 |
| Lance 2H Impaling Thrust | 6.42 m | 0.779 | 1.250 (wins every exchange) |
| Lance 2H Flaming Strike opening | 3.24 m | 0.779 | 0.810 |
| Lance 2H Flaming Strike, then R2 | 5.17 m | 0.779 | 1.115 |
| Lance 2H Stormcaller | 4.12 m | 0.847 | 0.885 |

## 4. A visible buff is waited out (INFERRED, used by ashes-of-war.md section 17)

A defensive buff only pays in the committed share: a defender who waits and dodges takes nothing
either way. A buff whose SpEffect carries a `vfxId` (the regulation value: the effect is drawn
on the character) is seen when it starts, and one that ends before the next engagement would come
(the fight's engagement spacing, 25 s over 5 landed hits = 5 s, `er-mechanics-status`) can be
waited out at no cost by backing off at the same run speed the caster chases at. So cast ahead,
it covers nothing. Cast as an answer to the opponent's attack it is compared with the roll it
replaces (`Opponents.buff_answer_value`).

## 5. What an instant long-distance dodge buys

Measured on the Lance and the Messmer Soldier's Spear (RL 150, `--measure-all`):

- **Entering on your own terms.** In the race the step is used where it closes sooner than
  running or the roll: gaps above 2.27 m. Against the pool's R1s a great spear is outreached by
  less than that almost always (pool p90 4.45 m against the Lance's 4.45 m), so `f_neutral` is the
  same with and without the step (1.235 on the Lance 2H's best opener, 1.216 on the Messmer
  Soldier's Spear 2H). It would matter for a short weapon against the longest reaches.
- **Whiff punishes from outside the defender's reach.** `Opponents.dodge_value` now lets a dodger
  who ends outside his opener's reach run the rest before striking. The pool's openers recover
  10-25 frames after their hit, and a reactive dodge is ready 16-20 frames after it starts, so the
  punish stays rare: 0.2% with the step against 0.1% with the roll.
- **Escaping a punish.** A punish lands before the attacker's own roll frame by construction
  (section 16a), so no dodge can escape it; the step changes nothing there.

So from frames and distances the step is still worth a medium roll, give or take 0.4 HP per
string. What players get from it that this model does not see (baiting, running, escaping a
second opponent, tracking) remains open.

Escaping with it (running away, drinking, leaving a stagger) is measured in disengage.md: the step
leaves 0.5-1.6 m more room than the roll, not enough to drink against a sprinting chaser, and it
leaves a first stagger 1-5 frames before a roll can.

## 6. What the pool throws (`--opponent-pool families`)

By default every pool build throws its R1 #1 (exchange.md section 1). `--opponent-pool families`
(it needs `--opponents-from`, a stored `--sort score --json` ranking) has each build throw what
that ranking says its weapon and grip are used for instead: every moveset family's best opener
(`moveset.families[*].opener`, moveset.md) at that family's use share
(`NeutralPool.from_results`). Two passes, the same shape as the skill term's opponents: a ranking
scored against R1 #1 supplies the openers for the next one.

- **Per opener**, from the ranking row's own slot: `neutral_in` (strike frame 2.5 m ahead from the
  opener's own input, entry and R2 lead-in included; world reach; PvP poise; TAE 795 windows on the
  same clock; live frames) and `dmg`. The jump openers are synthesized by
  `er-builds-pvp.jump_openers`, whose strike counts from the jump input (22 f on Alabaster Lord's
  Sword 2H), not the landed clip's 17.5 f contact.
- **Shares** are renormalised over the openers that resolve. A build whose weapon has no row, or no
  resolving opener, keeps its R1 #1. Each build keeps its own armor poise over all its openers.
- **Weights.** `er-mechanics-exchange.Pool` now carries a weight per entry and `exchange()` and
  `neutral_exchange` take weighted means (`Pool.mean`; plain `np.mean` when the weights are None, so
  the R1 pool scores bit for bit as before). The family pool replaces the R1 one for the slots'
  exchange and neutral contests, `skill_exchange`, `skill_neutral` and the dodge-skill rerun
  (ashes-of-war.md section 17). The reaction-dodge whiff punish (`mech.strikes`) keeps the pool's
  R1 #1, the fastest punish.

MEASURED on the RL 150 ranking of 2026-10-01 (822 rows), `scripts/er-opponent-family-pool-probe.py`
(the library pool matches the probe's own to 1.3e-15 in `f_neutral`):

| pool | rows | strike frame | world reach | PvP poise | rows with hyperarmor |
|---|---|---|---|---|---|
| R1 #1 | 310 | 16.6 f | 3.65 m | 259 | 30.5% |
| families | 1132 | 20.5 f | 4.37 m | 318 | 30.7% |

Opener shares: R1 #1 .288, R2 #1 .265, forward jump R1 .204, crouch R1 .128, forward jump R2 .068,
running R2 .032, running R1 .015, charged R2 .001. 4.7% of builds (44 of 945) keep R1 #1 (no
row), and 1.1% of the share is dropped (running openers with no `neutral_in`).

Re-deriving every family score with the new contest (base score, skill term not rerun): Spearman
0.998, top 20 keeps 19, |rank change| median 6, p90 23, max 63; scores fall 3% (median x0.970,
p10 0.953, p90 0.989) because the family pool reaches farther and breaks poise harder. Long, slow,
poise-heavy openers lose most (Freyja's Greatsword 1H 78 -> 100, Crescent Moon Axe 2H 95 -> 116,
Omen Cleaver 1H 85 -> 106); fast short weapons rise (Veteran's Prosthesis 2H 110 -> 67, Bloodhound
Claws 2H 122 -> 83, Spiked Caestus 2H 100 -> 68). Adoption (`er-builds-sweep-compare.py`, the
stored score moved by the base score's ratio): all weapons rho .4601 -> .4556, d -.0045, CI
[-.0099, +.0008]; adopted .2638 -> .2621, CI [-.0119, +.0084]; the top 30 keeps 26. A real run on
five weapons (`--weapon` Giant-Crusher, Uchigatana, Dagger, Lance, Great Stars) moves base scores
x0.953-0.979, the same range.

The default stays `r1`: the corpus neither confirms nor refutes the family pool, and it needs a
prior ranking (a second pass). The flag is the more faithful model of what the pool throws; use
it when that matters more than comparability with earlier runs.

## 7. Not established

- Feints, spacing inside the other's reach, chasing a retreating player (equal run speeds cancel),
  more than one engagement.
- That locked-on back and side movement is at run speed (no clip).
- What a corpus player actually throws: the family pool takes the ranking's own use shares, not
  recorded button use. Skills and powerstance openers are not in it (the corpus records neither
  the build's ash nor a powerstance pair).
- The contact at full reach, taken as the 2.5 m contact.

## Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py movement
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py pool --rl 150
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-neutral.py race --reach 4.45 --strike 18 --poise 300 --tools roll,step
```
