# Cross-hand combos: a right-hand attack into the off-hand L1

Labels as in the other `docs/er-mechanics/*.md`. **VERIFIED** = regulation value (installed 1.17.1
`regulation.bin`) or code read out of the 1.16.2 executable (shift 0 against `eldenring-deobf.bin`).
**TAE** = decoded TimeAct or behavior graph. **COMMUNITY** = Smithbox's decompiled `c0000.hks`
(ER 1.08.1, `Documentation/ER/c0000.hks`) or WitchyBND's TAE template. **MEASURED** = counted over
the regulation, the corpus or a decoded clip. **INFERRED** = fits the data, consumer not traced.
Nothing was launched; no number here has runtime proof.

Tool: `python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py` (commands in
section 7). Selftest 26/26.

## 0. Answer

- **Halberd running R1 into Battle Axe L1 is not a true combo by the static data. It misses by 5
  frames.**
  - The running R1 always breaks poise in PvP: 222 menu poise against a corpus median of 82, so it
    staggers 100% of the RL 140-160 PvP builds.
  - That break plays a middle stagger (`dmgLevel` 2).
  - The axe's L1 lands 30 frames after the running R1's first hit.
  - The defender can roll (or raise a guard) on frame 25 of the stagger.
  - So the only escape is a roll or guard started on frames 25-29. A counter-attack out of the
    stagger always loses, because the defender's R1 opens on frame 24 and no weapon hits within 6
    frames.
  - Online, the axe's damage lands on a defender who rolls on 25-29 only when the round trip
    plus about one update per side reaches 6 frames (200 ms), i.e. a round trip of roughly
    170-200 ms or more (section 12, static RE). At the 2 x 50 ms legs the ranking uses, the
    delay is about 3-4 frames, so the link stays roll-out-able with a 1-2 frame window.
  - Even when the damage lands, its stagger does not: the defender's own machine checks his
    i-frames when the hit packet arrives and writes reaction level 0 (section 12). So the link
    never becomes a stagger lock against a roll on 25-29, at any latency.
  - Halberd R1 #1 or #2 into the axe L1 is a tie: 25 against 25. A tie turns into landed damage
    at any delay of 1 frame or more, so online it lands for any round trip above about 33 ms.
- **The mechanism is the cancel id, not an earlier window.**
  - L1 is ChrActionType 2. Only TAE JumpTable 16 ("Cancel - LH Attack") and 117 ("Cancel - L1
    Attack") allow it; R1's 4/115 never do (VERIFIED).
  - In the halberd's running R1, 16 opens on frame 32 (type-300 early event), four frames after
    R1's 4 opens on frame 28.
  - The L1 still wins because the HKS plays `W_AttackLeftHeavy1` (clip 035000) directly.
  - An R1 re-chain out of any movement attack or R2 goes through `W_AttackRightLightSubStart`
    first (clips 030080..030091, no hitbox). That clip holds until EzState flag 0 (5 frames at
    best), and then R1 #2 still has its own startup.
  - Halberd running R1 -> R1 #2 has a gap of 41 frames; running R1 -> axe L1 has 30.
- **True cross-hand links exist, and many more than same-weapon ones.** Over 324 right-hand
  weapons x the off-hand-capable left weapons (100,016 pairs, 2,216,174 links, one-handed right):
  - **30,386 cross-hand links are true on a poise break; 12,235 of them stay true with the first
    hit's hitstop added to the gap.**
  - 11,536 are ties, and 17,728 pairs have at least one true right-hand -> L1 link.
  - The same-weapon links the HKS plays give 17 true out of 3,021: the axes' and claws' charged R2
    -> R1 through SubStart. Every R1 chain link stays `no`, as frame-advantage.md found.
- **The strongest setups come from a large stagger (roll gate 35) with a fast, heavy off-hand
  L1.**
  - The standout is a colossal sword one-handed rolling or crouch R1 into an axe, greataxe or
    curved-greatsword L1: gap 30-32, robust.
  - Greatsword + Battle Axe rolling R1 -> L1 has gap 30 against 35.
  - Colossal sword + Dragon Greatclaw is gap 34.2 against 35, so it is not robust.
  - A body-contact start stays in reach. A tip-range start pushes the defender out (section 5).
- **Ranking:** section 6.

## 1. How an L1 press leaves a right-hand attack

### EXE (VERIFIED, 1.16.2)

`_ChrActionFlag` (TAE event 0) dispatches through the jump table at 0x140428650:

| JumpTable id | case does | name (WitchyBND) |
|---|---|---|
| 9 | `AllowInputLHAttack` 0x140430070: `SetPossibleInputState(L1 = 2)`, and L2 = 3 unless `actionAnimationFlags & 0x7f8` | Input - LH Attack |
| 87 | `AllowInputRHAttack`, then `AllowInputLHAttack`, then `AllowInputDodge`, ... | Input - Common |
| 16 | `SetAllowedCancelToActionState(2)` and `(3)` | Cancel - LH Attack |
| 117 | `mov edx, 2` then case 115's tail (0x1404277dc) -> `SetAllowedCancelToActionState(2)` | Cancel - L1 Attack |
| 4 / 115 | actions 0, 1, 26, 27 / 0, 26; never 2 | Cancel - RH / R1 Attack |

So the L1 needs its own input (9 or 87) and cancel (16 or 117) windows. Those overlap exactly as
the R1's do in attacks.md section 4. Type-300 `ActivateChrActionFlagEarly` handles 16 and 117 too.
The action id 2 = L1 comes from Ghidra's own decompile of `AllowInputLHAttack`
(`SetPossibleInputState(pCVar1, L1, true)`).

### Behavior script (COMMUNITY)

- `GetAttackRequest`, for an L1 press:
  - a shield or torch in the left hand -> invalid (the L1 guards);
  - a bow, crossbow or staff -> its own branch;
  - `IsEnableDualWielding` -> powerstance;
  - `IsWeaponCanGuard` -> invalid (guard);
  - otherwise `ATTACK_REQUEST_LEFT_HEAVY`.
- `IsWeaponCanGuard` reads `WeaponCategoryID` from `common_define.hks`. That table is VERIFIED
  bytecode (`scripts/er-hks-weapon-category-table.py`). One-handed, only the torch (21) and the
  shields (47, 48, 49, 57) guard from the left hand. So **every melee weapon in the left hand
  that does not pair with the right makes L1 an off-hand attack**.
- `ExecAttack` plays `l2` for `LEFT_HEAVY`. Every right-hand attack state's `onUpdate` passes
  `l2 = "W_AttackLeftHeavy1"`. `W_AttackLeftHeavy1` plays 035000 for a one-handed left weapon; its
  other clips 032010/032020 are two-handed ones (TAE graph; the selector is INFERRED).
- Where R1 goes from each state:

  | state | R1 goes to |
  |---|---|
  | `AttackRightLight<n>` | `W_AttackRightLight<n+1>` (direct) |
  | `Jump_LandAttack_Normal` | `W_AttackRightLight2` (direct) |
  | running, rolling, crouch, backstep, R2 Start/End, guard counter | `W_AttackRightLightSubStart` |

- `AttackRightLightSubStart_onUpdate` moves on to `W_AttackRightLight2` at
  `GetEventEzStateFlag(0)` or the clip's end.
- The off-hand states send R1 to `W_AttackRightLight1` (direct) and L1 to
  `W_AttackLeftHeavy<n+1>`.

The decompile is 1.08.1. The installed 1.17.1 HKS was checked for names only
(frame-advantage.md section 9).

## 2. The link

All frames are 30 fps real time (TAE 608 play speed applied), counted from the first attack's
first hit frame:

```
start  = first frame the follow-up's clip can start (its input and cancel windows overlap)
gap    = start - first hit + lead-in + follow-up's first hit frame
         lead-in: R2 #2's release (frame-advantage.md 5), or SubStart's flag-0 frame for an R1
         re-chain out of a SubStart state (the shortest of the four clips: optimistic)
escape = min(roll for DamageCount 1, guard) of the reaction the first hit plays, + delay
verdict: gap < escape true, = tie, > roll-out-able; on a hit that leaves poise intact the
         reaction is level 0 (no hitstun at all): blocked by poise
```

Poise uses the same corpus as the ranking (`er-builds-pvp.pvp_corpus`, RL 150 +-10). Menu poise
damage is `poise_damage x 10 x FinalDamageRateParam.saRate` (0x140486bf0). `stagger` is the share
of builds whose menu poise is below it. `breaks_victim` compares it with one victim's poise
(default: the corpus median, 82). The poise-unit question in frame-advantage.md section 9 applies
here unchanged.

Each link also carries:

- `window`: frames the defender has to start an escape before the follow-up lands.
- `delay_needed`: the hit-to-reaction delay that would make it true.
- `gap_hitstop`: the gap with `AtkParam.hitStopTime` added.
  - That field is VERIFIED; the value is 0.08 s = 2.4 frames on nearly every melee row.
  - Adding it models only the attacker's clock stopping. Whether hitstop stops the attacker, the
    defender or both was not traced.
  - `robust` = true even with it added.

## 3. Halberd (1H) + Battle Axe (left)

Frames (TAE):

| clip | first hit | R1 start | L1 start | roll |
|---|---|---|---|---|
| running R1 `a038_030200` | 15 | 28 (early 4) | 32 (early 16) | 31 |
| R1 #1 `a210_030000` | 16 | 25 | 28 | 30 |
| axe off-hand L1 #1 `a030_035000` | 13 (clip 14 at speed 1.08) | 22 | 19 | 24 |
| SubStart (shortest of 030080..030091) | | flag 0 at 5 | | |

Links on a poise break (delay 0, DamageCount 1). Every link is `blocked by poise` when poise holds:

| link | via | gap | +hitstop | reaction | escape | verdict | delay needed | PvP poise / stagger |
|---|---|---|---|---|---|---|---|---|
| running R1 -> axe L1 | 16/117 | 30 | 32.4 | middle | 25 | roll-out-able (5-frame window) | 6 | 222 / 100% |
| running R1 -> R1 #2 | SubStart (5) | 41 | 43.4 | middle | 25 | roll-out-able | 17 | 222 / 100% |
| R1 #1 -> axe L1 | 16/117 | 25 | 27.4 | middle | 25 | tie | 1 | 222 / 100% |
| R1 #1 -> R1 #2 | 4/115 | 32 | 34.4 | middle | 25 | roll-out-able | 8 | |
| R1 #2 -> axe L1 | 16/117 | 25 | 27.4 | middle | 25 | tie | 1 | 111 / 86% |
| jump R2 -> axe L1 | 16/117 | 33 | 35.4 | large | 35 | true | 0 | 672 / 100% |
| axe L1 -> Halberd R1 #1 | 4/115 | 25 | 27.4 | small | 10 | roll-out-able | 16 | 150 / 100% |

Why a player sees it land "almost always":

- The defender's R1 out of a middle stagger opens on frame 24. The L1 hits on frame 30, 6 frames
  later, before any attack out of the stagger can land. The L1's 150 menu poise also breaks 100% of
  the corpus.
- Only a roll or guard begun on frames 25-29 escapes. A roll has i-frames from its first frame.
  How long a guard takes to become effective was not read.
- Whether a roll pressed earlier in the stagger is held until the gate opens on frame 25 was not
  traced. If it is, a buffered roll always escapes.

Reach, from `--reach` (INFERRED geometry, section 5):

- The running R1 connects up to 6.30 m (contact centre, from the start position). The attacker has
  moved 3.27 m by the L1's start frame, the push is 1.0 m, and the L1 contacts to 2.71 m.
- So the L1 follows only if the running R1 landed with the defender within about 4.98 m. In the
  outer 1.3 m of the running R1's range, the L1 whiffs.

## 4. Across all pairs (MEASURED, `--sweep`)

The universe is 324 named base melee weapons in the right hand, one-handed. The left hand is every
base melee weapon whose L1 is an off-hand attack against that right weapon: not paired, not
guarding. That gives 100,016 pairs.

| | links | true | tie | robust (true with hitstop) |
|---|---|---|---|---|
| cross hand (R -> L1 #1, L1 #n -> R1 #1, L1 #n -> L1 #n+1) | 2,216,174 | 30,386 | 11,536 | 12,235 |
| same weapon (R1 chain, jump R1 -> R1 #2, SubStart -> R1 #2, R2 #1 -> #2) | 3,021 | 17 | | |

True cross-hand links by opener:

| opener -> follow-up | true links |
|---|---|
| rolling R1 -> L1 | 4,594 |
| crouch R1 -> L1 (the rolling R1's clip on most categories, so this double counts) | 4,594 |
| charged R2 #1 -> L1 | 3,863 |
| jump R2 -> L1 | 3,753 |
| R2 #2 -> L1 | 2,408 |
| off-hand L1 #2 -> R1 #1 | 2,340 |
| R1 #2 -> L1 | 1,970 |
| off-hand L1 #3 -> R1 #1 | 1,356 |
| off-hand L1 #4 -> R1 #1 | 1,134 |
| R1 #3 -> L1 | 897 |
| running R2 -> L1 | 860 |
| off-hand L1 #1 -> R1 #1 | 835 |
| R1 #4 -> L1 | 660 |
| backstep R1 -> L1 | 440 |
| R1 #1 -> L1 | 374 |
| uncharged R2 #1 -> L1 | 182 |
| jump R1 -> L1 | 126 |
| running R1 -> L1 | 0 |

Which left weapon gives a true right -> L1 link against the most right-hand weapons (of 324):

| left weapon | right weapons |
|---|---|
| every dagger (Dagger, Misericorde, Wakizashi, ...) | 144 |
| hatchets (Hand Axe, Forked Hatchet, Icerind Hatchet) | 116 |
| Club, Stone Club, Nightrider Flail | 92 |
| Battle Axe | 71 |

Top pairings by guaranteed follow-up damage:

- The value is stagger share x the follow-up's AR x motion value. The AR is Standard, max
  upgrade, STR 40 / DEX 40 / 10 elsewhere (an INFERRED reference line, not a build, and no
  defense).
- Weapons that share the opener's clip give identical frames, so each (opener clip, left weapon)
  appears once.

| value | right (example of its clip) | left | link | gap / escape | +hitstop | robust | reach body / tip |
|---|---|---|---|---|---|---|---|
| 796 | Greatsword (colossal swords) | Dragon Greatclaw | rolling R1 -> L1 | 34.2 / 35 | 36.6 | no | yes / no |
| 755 | Greatsword | Troll's Hammer | crouch R1 -> L1 | 34.2 / 35 | 36.6 | no | yes / no |
| 744 | Greatsword | Great Club | crouch R1 -> L1 | 34.2 / 35 | 36.6 | no | yes / no |
| 743 | Greatsword | Devonia's Hammer | rolling R1 -> L1 | 34.2 / 35 | 36.6 | no | yes / no |
| 716 | Prelate's Inferno Crozier | Grafted Blade Greatsword | charged R2 -> L1 | 62 / 63 | 64.4 | no | no / no |
| 688 | Greatsword | Bloodhound's Fang | rolling R1 -> L1 | 31.8 / 35 | 34.2 | yes | yes / no |
| 688 | Putrescence Cleaver, Greataxe | Bloodhound's Fang | R2 #2 -> L1 | 34.5 / 35 | 36.9 | no | yes / no |
| 688 | Halberd | Bloodhound's Fang | jump R2 -> L1 | 34.8 / 35 | 37.2 | no | yes / no |
| 648 | Greatsword | Axe of Godrick | rolling R1 -> L1 | 30.9 / 35 | 33.3 | yes | yes / no |
| 646 | Greatsword | Dragon King's Cragblade | crouch R1 -> L1 | 32.0 / 35 | 34.4 | yes | yes / no |
| 634 | Greatsword | Devourer's Scepter | crouch R1 -> L1 | 32.0 / 35 | 34.4 | yes | yes / no |
| 631 | Greatsword | Winged Greathorn | rolling R1 -> L1 | 30.9 / 35 | 33.3 | yes | yes / no |
| 630 | Greatsword | Greataxe | crouch R1 -> L1 | 30.9 / 35 | 33.3 | yes | yes / no |
| 618 | Greatsword | Longhaft Axe, Crescent Moon Axe, Bonny Butchering Knife | crouch R1 -> L1 | 30.9 / 35 | 33.3 | yes | yes / no |

Greatsword (1H) + Battle Axe: rolling R1 -> L1 is 30 / 35, 32.4 with hitstop. That is true and
robust. The pattern is general:

- A colossal sword's one-handed rolling or crouch R1 hits on frame 14-16 and plays a large stagger
  (roll gate 35).
- Its L1 cancel opens 1 frame after the R1 cancel.
- Any off-hand L1 whose first hit is at or before frame 17-20 lands inside the gate.

## 5. Reach and push (INFERRED geometry)

- `AtkParam.knockbackDist` is VERIFIED at +0x10. Taking it as the metres the defender is pushed
  on a stagger is INFERRED.
  - The small, middle and large stagger clips carry no root motion (MEASURED, `a000_0051xx` ..
    `0053xx`), so the push is not in the animation.
  - On a poise break `FUN_14047e540` overwrites the hit's knockback (damage info +0x58) with
    `NpcParam.superArmorBrakeKnockbackDist` when that is > 0 (VERIFIED). The player's NpcParam
    row was not identified, so that override is not applied.
  - Where +0x58 is first filled from `knockbackDist` was not traced.
- `--reach` places the defender in one of two spots:
  - tip: at the first attack's contact distance (its farthest hit);
  - body: at its lunge + two idle front radii.
- It pushes the defender by `knockbackDist`, moves the attacker by the first clip's root motion
  up to the follow-up's start, and asks whether the follow-up's contact distance
  (`er-mechanics-reach`, pose decoder) covers the rest. It is a straight line, with no tracking
  and no body collision.
- Every top pairing reaches from body contact and misses from the tip. A true combo on paper
  therefore also needs the first hit to land close.

## 6. The ranking factor (`er-builds-pvp.py --paired-offhand LEFT`)

What the flag does to each one-handed row:

- It gets `LEFT` (Standard, max upgrade, the row's own stats, no grease, no buffs) in the left
  hand, if the pair gives an off-hand L1.
- `er-mechanics-combo.paired_slots` adds a `left_1` slot, scored by `slot_hit` on the same corpus
  as any slot.
- Any opener whose cross-hand link lands more often than its current first follow-up gets that
  link put first in `combos`. The link carries `start`, the L1's own start frame, which
  `er-mechanics-moveset.engagement` now uses for the follow-up's offset in place of the opener's
  same-button frame.
- Two-handed rows are untouched: L1 is the guard.
- Since 6b, a roll-out-able link carries a landing chance instead of 0, jump openers keep their
  cross-hand links, and a string is scored per opening. 6a is the result before those changes.

### 6a. Result (MEASURED, RL 150, 822 rows, `--sort score`)

The runs were made 2026-09-30. Baseline and paired runs used the same tree, each about 50 min.

| run | rows whose score moved | engagements that take the L1 | largest rank move |
|---|---|---|---|
| `--paired-offhand "Battle Axe"` | 1 (Veteran's Prosthesis 1H 355.8 -> 357.5) | 1 (running R2 -> L1) | none (564 -> 564) |
| `--paired-offhand Dagger` | 2 by more than 0.05 (Shield of the Guilty 1H 154.2 -> 156.2, Thiollier's Hidden Needle 1H 293.6 -> 293.9) | 5 (running R2 -> L1) | Shield of the Guilty 747 -> 741 |

The other rows the dagger run lists as different move by under 0.05, both grips included. That is
run-to-run noise in the skill term, not this factor.

So the factor is wired through but barely moves the ranking. The cause is the scoring rule, not
the frame data. `engagement` keeps a follow-up only if the opener plus the follow-up beats its
family's best single opener in damage per committed frame. Nothing in `slot_score` pays for the
neutral a true combo saves (moveset.md 1e). Counterfactual on the baseline rows:

- Greatsword 1H rolling R1 plus the Battle Axe L1 (true, p = 1) lifts that engagement from 255.5
  to 272.4.
- The move family's best is still running R2 at 330, and the jump family scores 548.
- It would need the L1 to deal about 2x the axe's damage before the move family changes.

### 6b. Per-opening credit (2026-09-30, MEASURED, RL 150, 822 rows)

Three changes, all under `--paired-offhand` only (the baseline run reproduces 6a's baseline row
for row: 0 of 822 scores moved):

1. **Landing chance per link side** (`er-mechanics-combo.paired_slots`): true 1, tie 0.5
   (INFERRED even odds), roll-out-able `roll_out_p`. The defender starts his roll or guard once he
   sees the follow-up clip start, and it is live his reaction plus two network legs later
   (`er-mechanics-ashes.reaction_delays`: lognormal median 0.25 s, sd 0.2, + 2 x 50 ms, 8.45-13.31
   frames over 9 quantiles; INFERRED). The gate is already open (that is what roll-out-able
   means), so he escapes exactly when that delay is shorter than the follow-up's own startup.
   Battle Axe L1 (first hit 13): p = 1/9. The halberd running R1 -> axe L1 therefore scores 0.11,
   not 0; it is still not taken.
2. **Jump openers keep cross-hand links** (`er-mechanics-jump._jump_link`). The L1 start is held
   to the landing, the gap is counted from the jump's own first hit, and each side is re-read
   against its escape.
3. **Per-opening score** (`er-mechanics-moveset.opening_credit`). `slot_score` is damage per
   committed frame, so a follow-up adding damage at the opener's own rate adds nothing, and the
   neutral that has to be won again is charged nowhere (moveset.md 1e). Every engagement now also
   costs the status model's neutral time between engagements (`ENGAGEMENT_SECONDS` 5 s = 150
   frames, INFERRED). A string's score is its `slot_score` x `C_s / (150 + C_s) x (150 + C_o) /
   C_o`, which is 1 for the opener alone. The string then competes with every other opener as a
   whole. The plain rule "(opener + p x follow-up) / (opener commit + extra commit)" was already
   what `engagement` computed, and it keeps Greatsword 1H at 272 against running R2's 330.

| run | rows whose score moved | engagements taking the L1 | L1 openers | largest rank move |
|---|---|---|---|---|
| `--paired-offhand "Battle Axe"` | 56 | 42 | crouch R1 19, running jump R2 17, R1 3, running R2 2, charged R2 1 | Dragon Halberd 1H 443 -> 375 |
| `--paired-offhand Dagger` | 257 | 647 | crouch R1 179, running jump R1 156, R1 138, R2 99, backstep R1 50, jump R2 14, running R2 10 | Spiralhorn Shield 1H 790 -> 684 |

Battle Axe left, rank of 822 (score):

| row | before | after | engagement that changed |
|---|---|---|---|
| Halberd 1H | 69 (611.0) | 51 (626.8) | jump: running jump R2 583.2 -> + axe L1 (true, p 1) 648.2 |
| Halberd 2H | 51 (624.7) | 53 (624.7) | none (L1 guards); passed by 1H rows |
| Greatsword 1H | 203 (554.0) | 193 (558.2) | move: running R2 330.4 -> crouch R1 + axe L1 (true) 386.4 |

- The Battle Axe risers are the halberds and glaives whose running jump R2 breaks poise into a
  large stagger: Dragon Halberd +68, Commander's Standard +62, Golden Halberd +56, Glaive
  +42 (141 -> 99), Banished Knight's Halberd +26 (80 -> 54).
- The Dagger run's largest movers are one-handed shield rows (a shield's R2 into a dagger L1) and
  Pata +83 (303 -> 220), Torch +71, Bloodhound Claws +49, Hookclaws +45. The top 25 is unchanged
  in both runs.
- The Greatsword's move family now prefers the string, but that family carries 0.22 of the use
  share, so the weapon gains 4.2.

The factor stays off by default. Which left weapon to pair is section 9.

## 7. Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py --pair Halberd "Battle Axe"
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py --pair Halberd "Battle Axe" --reach --json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py --pair Halberd "Battle Axe" --delay 6
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-combo.py --sweep --top 25 --reach     # ~3 min
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-pvp.py --rl 150 --sort score --json --paired-offhand "Battle Axe"
python3 /home/banon/projects/er-mods-rs/scripts/er-builds-rank-diff.py base.json paired.json --show Halberd Greatsword
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-offhand.py --class axe     # ~30 s; all classes ~5 min
```

## 8. Not established

- **The size of the reaction start delay online.** Section 12 settles its structure statically:
  it is the round-trip time between the two players plus at most about one 60 fps update on each
  side, not an engine constant. The two quantization terms and the replica's lag behind its
  owner need a runtime measurement (section 12 lists the hooks).
- **Hitstop's effect on the two clocks.** `gap_hitstop` is one bound.
- **Input buffering across the stagger's roll gate** (a roll pressed before frame 25).
- **The guard's time to become effective**, which is how long after the guard input the hit is
  blocked.
- **Which SubStart clip plays** (SpEffects 135-138 from the first clip's TAE). The shortest is
  used, so same-weapon SubStart links are optimistic.
- **Where `knockbackDist` becomes the push**, and the player's
  `superArmorBrakeKnockbackDist`.
- **The 1.08.1 HKS against the installed 1.17.1 script** (names checked only).
- **Powerstance (paired) L1s** are in `er-mechanics-moveset` (398/398 `no`). They were not
  re-measured here with the new cancel-id reading. Their follow-ups use the same ids, 16/117.

## 9. Left weapons ranked as off-hands (`er-mechanics-offhand.py`, 2026-09-30)

The sweep in section 4 orders pairs by one guaranteed follow-up's damage at one stat line
(STR 40 / DEX 40). That can only say which heavy L1 lands after a colossal sword's rolling R1, so
Greataxe and Axe of Godrick top it. It cannot say which left weapon is a good off-hand.
`scripts/er-mechanics-offhand.py` scores the off-hand L1 #1 as a move of its own, per base melee
weapon, against the RL 140-160 PvP corpus (1,108 builds after deduplication):

```
score = slot_score(L1 as an opener) x fit x (1 + p_link x dmg / FIGHT_REF_DAMAGE)
```

- `slot_score` is `er-builds-pvp`'s: damage per committed frame (the L1 #2 start or the roll,
  whichever is first), reach, frame advantage, stagger share, the startup contest and
  `f_stamina`. Damage is Standard at max upgrade at the median damage stats of the corpus builds
  that meet the weapon's requirements (`elig` = their share).
- `fit` = share of corpus builds that stay at medium roll at their own Endurance with this weapon
  in place of their heaviest left-hand item (MEASURED).
- `p_link` = corpus-weighted mean, over the right-hand weapons the corpus carries, of the best
  opener -> L1 #1 landing chance (stagger share x 1 true / 0.5 tie / `roll_out_p` roll-out-able).
  `true` = weighted share of right weapons with a true link; `dual` = share with which the L1 is
  powerstance instead.
- The link factor adds the follow-up's expected damage to an engagement whose opener deals the
  status model's reference hit (471.5). All weights are INFERRED.

### 9a. Axes (MEASURED terms, RL 150)

| axe | score | slot | fit | link | startup | L1 #2 | roll | dmg | stamina | weight | p_link | true | corpus off-hand |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rosus' Axe | 637.9 | 729.2 | 0.72 | 1.22 | 13 | 19 | 24 | 324 | 13 | 5.5 | 0.32 | 0.25 | 0 |
| Ripple Blade | 605.2 | 648.7 | 0.79 | 1.19 | 13 | 19 | 24 | 278 | 12 | 4.5 | 0.32 | 0.25 | 2 |
| Icerind Hatchet | 561.5 | 542.4 | 0.86 | 1.20 | 11.9 | 14.9 | 22.9 | 208 | 12 | 3.0 | 0.46 | 0.33 | 3 |
| Forked Hatchet | 528.6 | 504.2 | 0.88 | 1.19 | 11.9 | 14.9 | 22.9 | 193 | 11 | 2.5 | 0.46 | 0.33 | 4 |
| Hand Axe | 507.2 | 507.2 | 0.84 | 1.19 | 11.9 | 14.9 | 22.9 | 195 | 12 | 3.5 | 0.46 | 0.33 | 42 |
| Battle Axe | 465.2 | 515.0 | 0.79 | 1.15 | 13 | 19 | 24 | 221 | 12 | 4.5 | 0.32 | 0.25 | 0 |

Every axe's L1 does 150 menu poise damage and breaks 100% of the corpus.

- **The hatchets (Hand Axe, Forked, Icerind) are the best-framed axes.** First hit 11.9 against
  13, L1 #2 on 14.9 against 19, and a true link after 33% of the corpus's right-hand weapons
  against 25%. They weigh 2.5-3.5, so 84-88% of builds keep medium roll.
- **Rosus' Axe and Ripple Blade top the class on damage alone.** The frames are the standard
  axe's. Their damage comes from the stats of the 13-17% of builds that meet their requirements,
  so it is the damage of an arcane or faith build, not a typical one.
- **Battle Axe is 14th of 17 axes and 0 of 1,108 corpus builds carry it as an off-hand.** It
  shares the standard axe frames and links with every other axe, deals 221 (Hand Axe 195, +13%)
  and weighs 4.5 (fit 0.79 against 0.84). It looked good in the section 4 sweep because that
  sweep ranks only the follow-up's damage after a true link, and every standard axe gives the
  same links: it rewarded the +13% and nothing else. Against the hatchets it is slower (13 vs
  11.9 first hit, 19 vs 14.9 to L1 #2), links true after fewer right weapons (25% vs 33%) and is
  heavier.

### 9b. Against the corpus

Off-hand use = a left-hand item whose L1 is an off-hand attack with the build's first right-hand
weapon. 344 such items over 104 weapons.

- Spearman(score, off-hand use): **0.136** over all 324 candidates, **0.065** over the 104 used.
  The score does not predict what players carry.
- The corpus's top two agree with the model's frame and link terms: Hand Axe (42 builds) and
  Poisoned Hand (22) are among the fastest, lightest L1s. The model ranks them 34th and 17th of
  324, below heavier, higher-requirement weapons whose `slot_score` is carried by damage.
- The rest of the corpus top 15 is Cleanrot Knight's Sword, Grave Scythe, Shamshir, Cinquedea,
  Great Katana, Fire Knight's Shortsword, Milady, Backhand Blade, Wakizashi, Dagger, Knight's
  Greatsword. That is not a list of good off-hand L1s by any term here (model ranks 90-283).
  Weapons like Great Katana and Knight's Greatsword are likely held left to be two-handed as a
  second weapon, not for their L1. INFERRED: the corpus does not record why an item is in the left
  hand.
- Infusion: the common left items are Heavy 53, Standard 39, Blood 18, Cold 12, Flame Art 11 (of
  the top 13). The model scores Standard only.

So the ranking answers "which L1 is the better off-hand move" (hatchets over Battle Axe, with
numbers). It is not validated as a predictor of adoption, and it should not drive a
`--paired-offhand best` selection yet.

## 10. The off-hand L1 chain as neutral pressure (2026-09-30)

The user reports that players almost only carry the Hand Axe as an off-hand, and that they use it
for neutral L1 chains. The corpus agrees: Hand Axe 42, Icerind Hatchet 3, Battle Axe 0. Three
flags of `er-mechanics-offhand.py`, each separable:

- `--best-affinity`: each build kind in the eligible corpus (grouped by its highest damage stat)
  takes the affinity whose L1 deals the most at that group's median stats. A unique weapon has
  Standard only.
- `--chain`: the L1 #1 -> #n chain thrown on its own is scored as the move (`Scorer.chain`).
  - Hits are thrown while the median stamina bar lasts (no regeneration inside the chain,
    INFERRED). Each starts at the previous hit's `l1_start`.
  - Each gap continues with chance `q` = stagger share x (the break verdict's chance x no counter
    out of the stagger) + (1 - stagger share) x no reactive counter. The counter is the pool's own
    R1 #1: buffered out of the stagger, one frame before its roll gate, or after the reaction
    delay when poise holds.
  - `q` is also x 0 when the pushed victim is out of the next hit's contact from body range
    (`reach_check`).
  - The attacker throws hit k+1 only after hit k landed. An escaped hit is his whiff, and the
    engagement ends on its roll frame.
  - The score is `slot_score` on the chain's expected damage over its expected commitment, with
    L1 #1's reach, contest and stamina factors.
- `--roll-catch` (10b).

### 10a. Hand Axe against Icerind Hatchet (same frames)

| | affinities | ashes mountable | requirements | eligible builds | weight | corpus off-hand |
|---|---|---|---|---|---|---|
| Hand Axe | 13 | 48 | STR 9 DEX 8 | 98.7% | 3.5 | 42 |
| Forked Hatchet | 13 | 48 | STR 9 DEX 14 | 85.6% | 2.5 | 4 |
| Icerind Hatchet | 1 (unique) | 1 | STR 11 DEX 16 | 74.9% | 3.0 | 3 |
| Battle Axe | 13 | 48 | STR 12 DEX 8 | 92.6% | 4.5 | 0 |

- **Infusion separates Hand Axe from Icerind.** With `--best-affinity` the eligible builds pick
  Heavy 36%, Lightning 22%, Sacred 17%, Magic 14%, Occult 11% for the Hand Axe. Its L1 deals 302,
  against Icerind's Standard-only 236.
- Among axes the Hand Axe goes from 9th to 2nd, behind Forked Hatchet (292 damage, lighter).
- **The model cannot separate Hand Axe from Forked Hatchet.** They share frames and both infuse,
  yet the corpus split is 42 to 4. The data's only differences are requirements (98.7% vs 85.6%
  eligible) and weight. Where each is obtained in the game is not in the model.

### 10b. Roll-catch (the user's lead, checked against the frame data)

**Setup.** The defender at body range rolls L1 #1.
- **Roll timing:** the start is one of the nine reaction delays (8.45-13.31 frames) or a buffered
  roll on frame 0; 10% buffered, 90% reactive (INFERRED).
- **The roll** is medium `a000_027110` (`er-mechanics-disengage.tool`): i-frames 0-13, the next
  roll on frame 21, 12 stamina, 3.0 m away 16 frames in.
- **Direction:** away, side or toward the attacker, one third each (INFERRED).
- **Catch rule.** Hit k+1 catches him when its active window overlaps his recovery and he is
  inside its contact. Otherwise he rolls again on reaction to hit k+1, never before frame 21.

| off-hand | catch on the next hit | catch at all | away / side / toward | rolls to escape |
|---|---|---|---|---|
| hatchets (Hand Axe, Forked, Icerind) | 0.67 | 0.67 | 0 / 1.0 / 1.0 | 1.0 |
| standard axes (Battle Axe, Highland, ...) | 0.25 | 0.42 | 0 / 0.38 / 0.88 | 1.5 |
| Messmer Soldier's / Warped Axe, Ripple Blade | 0.58 | 0.62 | 0 / 0.88 / 1.0 | 1.11 |
| Dagger, Cinquedea | 0.50 | 0.50 | 0 / 0.75 / 0.75 | 1.17 |
| Poisoned Hand | 0.33 | 0.33 | 0 / 0 / 1.0 | 1.0 |
| Cleanrot Knight's Sword | 0.29 | 0.29 | 0 / 0 / 0.88 | 1.06 |
| Grave Scythe, Great Katana | 0 | 0 | 0 / 0 / 0 | 1.3-1.7 |

- **The user's claim holds for hatchets, except against a roll away.** A hatchet's L1 #2 lands at
  26.8-29.8 frames on the chain clock. A reactive roll started 8.5-11.9 frames in ends its
  i-frames at 21.5-24.9 and cannot roll again before 29.5-32.9, so the timing catches every
  reactive roll.
- **Distance decides it.** Rolling away puts him 3.0 m farther by the hit, against an L1 #2
  contact of about 2.5 m, so the away roll always escapes. A side roll (about 3.7 m straight
  line before the attacker's travel) and a roll toward him are caught.
- **The standard axes' 19-frame cycle** lets some reactive rolls reach their next roll gate first,
  so fewer are caught.
- **Scoring.** The waiting share of defenders (`REACT_SHARE` 0.5, x the share whose roll evades
  L1 #1) rolls. A caught roll is credited as the same chain again, and the attacker as throwing
  the whole chain meanwhile (INFERRED).

### 10c. Result (MEASURED, all 324 left weapons)

| variant | Spearman all 324 / the 104 used | Hand Axe | Forked Hatchet | Icerind | Battle Axe | Rosus' Axe |
|---|---|---|---|---|---|---|
| L1 #1, Standard (section 9) | 0.136 / 0.065 | 34 | 25 | 14 | 48 | 2 |
| `--best-affinity` | 0.223 / 0.165 | 2 | 1 | 20 | 5 | 16 |
| `--chain` | 0.197 / 0.080 | 41 | 37 | 29 | 50 | 13 |
| `--chain --best-affinity` | 0.268 / 0.182 | 4 | 3 | 43 | 12 | 31 |
| `--chain --roll-catch` | 0.239 / 0.103 | 41 | 36 | 32 | 97 | 44 |
| `--chain --roll-catch --best-affinity` | **0.292 / 0.175** | **7** | 6 | 40 | 63 | 80 |

Axes under all three flags (rank of 324; the hatchets' L1 #2 lands 14.9 frames after L1 #1, the
standard axes' 18.9):

| axe | rank | score = slot x fit x link | chain dmg / commit / DPS | poise/s | catch | corpus |
|---|---|---|---|---|---|---|
| Messmer Soldier's Axe | 1 | 340.3 = 385.0 x 0.72 x 1.23 | 715 / 81.3 / 264 | 81 | 0.62 | 0 |
| Warped Axe | 3 | 291.6 = 367.6 x 0.64 x 1.24 | 749 / 81.3 / 276 | 81 | 0.62 | 1 |
| Ripple Blade | 4 | 291.1 = 311.4 x 0.79 x 1.19 | 585 / 81.3 / 216 | 81 | 0.62 | 2 |
| Forked Hatchet | 6 | 284.4 = 250.8 x 0.88 x 1.29 | 414 / 61.5 / 202 | 84 | 0.67 | 4 |
| Hand Axe | 7 | 279.4 = 257.0 x 0.84 x 1.29 | 427 / 61.5 / 208 | 84 | 0.67 | 42 |
| Icerind Hatchet | 40 | 214.4 = 202.4 x 0.86 x 1.23 | 335 / 61.5 / 163 | 84 | 0.67 | 3 |
| Battle Axe | 63 | 188.1 = 195.7 x 0.79 x 1.22 | 319 / 69.6 / 137 | 56 | 0.42 | 0 |
| Rosus' Axe | 80 | 171.0 = 193.9 x 0.72 x 1.23 | 327 / 69.6 / 141 | 56 | 0.42 | 0 |

- The Messmer Soldier's / Warped / Ripple Blade L1 is a different chain: gaps 16.9, 19.0, 17.0,
  25.0, and its first link continues more often (q 0.44 against the hatchets' 0.22).
- No regular axe rises above the hatchets on frames. Their lead comes from damage per hit.

### 10d. The speed rule on the chain (`er-builds-speed-adoption.py --all-measures`)

| off-hand measure | classes | rho (weighted) | fast=top | share vs weapons | single leader most used | gap rho | slope [CI] |
|---|---|---|---|---|---|---|---|
| chain_gap (mean hit-to-hit) | 25 | 0.272 (0.332) | 22/25 | 0.85 vs 0.76 | 1/2 | **0.596** | -0.005 [-0.215, +0.031] |
| chain_gap, per family | 13 | **0.625 (0.742)** | 11/13 | 0.75 vs 0.63 | 6/8 (0.64 vs 0.44) | -0.01 | -0.050 [-0.411, +0.040] |
| chain_dps | 25 | 0.028 (0.120) | 5/25 | 0.18 vs 0.12 | 5/25 | 0.36 | -0.001 [-0.005, +0.002] |

The chain's hit-to-hit gap is the strongest speed signal measured in this repo:
- within-class rho 0.27 per weapon and 0.63 per animation family;
- the fastest chain family is the most used in 6 of 8 classes with a single leader;
- per weapon, the gap to the second-fastest predicts the leader's excess share (0.60).

The adoption slope's CI still spans 0. By the rule set for section 7 (a term only on a measure
whose slope excludes 0), no relative-speed term is added on it.

### 10e. Forked Hatchet's requirements (the user's lead, `er-mechanics-offhand.py --req-cost`)

Starting stats are VERIFIED from `CharaInitParam` 3000-3009.

| | needs | Vagabond start short by | corpus builds meeting it | of the 42 Hand Axe carriers | of the 582 Vagabond builds |
|---|---|---|---|---|---|
| Hand Axe | STR 9 DEX 8 | 0 | 98.7% | (all) | 100% |
| Forked Hatchet | STR 9 DEX 14 | 1 (DEX 13) | 85.6% (0.32 levels short on average) | 95.2% (0.05 levels) | 91.1% (0.09 levels) |
| Icerind Hatchet | STR 11 DEX 16 | 3 | 74.9% | 88.1% | 84.5% |

- **The user is right at the start of the game:** Forked Hatchet is one DEX level out of a
  Vagabond's reach (13 against 14).
- **By RL 150 it costs almost nothing.** Of the builds that actually carry the Hand Axe as an
  off-hand, 95% already meet Forked Hatchet's requirements, at 0.05 levels on average.
- Weighting each score by the share meeting the requirements (`--req-fit`) scales Forked Hatchet
  by 0.87 against the Hand Axe. The corpus split is 42 to 4, about 0.1.
- So requirements do not explain the split.
- Vagabond is 52% of the corpus (582 of 1,108) but carries 34 of the 42 Hand Axe off-hands
  (81%). The Vagabond starts with a Longsword, a Halberd and a Heater Shield (`CharaInitParam`
  3000 equipment, VERIFIED). No starting class starts with a hatchet.

### 10f. The three hatchets side by side (2026-09-30, MEASURED)

`EquipParamWeapon` 14020000 / 14010000 / 14080000 (`dump-param-rows.py`), L1 terms from
`er-mechanics-offhand.py --class axe --chain --roll-catch --best-affinity`, blade length from
`er-mechanics-reach.py` (grip to the weapon model's tip dummy).

| | Hand Axe | Forked Hatchet | Icerind Hatchet | Battle Axe |
|---|---|---|---|---|
| `behaviorVariationId` / `wepmotionCategory` | 1401 / 30 | 1401 / 30 | 1401 / 30 | 1400 / 30 |
| model, blade tip | wp_a_0700, 0.900 m | wp_a_0406, **0.769 m** | wp_a_0714, 0.947 m | wp_a_0701 |
| L1 contact (`contact_centre_m`) | 2.55 | **2.43** | 2.57 | 2.71 |
| L1 #1 damage, best affinity at the eligible median | 302 | 292 | **236** (unique, Standard) | 331 |
| base physical, STR / DEX scaling | 117, 30 / 55 | 113, 29 / 60 | 115, 20 / 60 | 128, 48 / 33 |
| damage type | Standard | Pierce / Standard (`atkAttribute` 2) | Standard | Standard |
| innate status | none | bleed (`spEffectBehaviorId0` 6401) | frost (6702) | none |
| weight, requirements, eligible | 3.5, 9/8, 99% | 2.5, 9/14, 86% | 3.0, 11/16, 75% | 4.5, 12/8, 93% |
| chain gaps, poise, roll-catch | 14.9/16.0/14.0/19.9, 172, 0.67 | same | same | 18.9/18.8/17.9/24.0, 129, 0.42 |
| corpus off-hand: builds / distinct users | 43 / 28 | 4 / 4 | 3 / 3 (one build, cloned) | 0 |

- **The three hatchets share every L1 frame.** Same behavior variation (so the same `AtkParam`
  rows, hit shapes and poise damage) and the same motion category. Startup, gaps, recovery,
  stamina, poise and the roll-catch are identical; the moveset separates hatchets from the other
  axes, not one hatchet from another.
- **Icerind Hatchet:** it cannot be infused, so its L1 deals 236 against 302 (-22%), and 75% of
  builds meet its requirements.
- **Forked Hatchet against Hand Axe:** the only L1 difference in the data is reach. Its blade is
  0.13 m shorter (0.769 against 0.900), so the L1 contact is 0.12 m shorter (2.43 against 2.55,
  -4.7%). It also deals 3% less (292 against 302). Against that it is 1.0 lighter and carries
  innate bleed, which the scorer does not credit (`status` is empty). The shorter reach changes no
  chain or roll-catch outcome in the model (`reaches` all true, side and toward rolls caught by
  both), so the model still ranks Forked level with the Hand Axe. Not established: whether 0.12 m
  of reach is what players notice.
- **What the corpus counts is mostly a buff carrier.** Of the 43 Hand Axes held left, 24 mount a
  self-buff (Braggart's Roar 13, Endure 4, Cragblade 3, War Cry 2, Barbaric Roar 1, Golden Vow 1)
  and 6 more Beast's Roar. The Hand Axe holds 16 of the corpus's 38 left-hand roars
  (Braggart's / Barbaric / War Cry); no other left item holds more than 3. The 4 Forked Hatchets
  mount Poisonous Mist, Wild Strikes, Sword Dance and The Poison Flower Blooms Twice; the 3
  Icerinds keep Hoarfrost Stomp. So the 43:4 split is mostly a split in what the left axe carries
  as a skill, and the off-hand L1 chain score is not the quantity it measures. Why the roar stick
  is the Hand Axe rather than the lighter Forked Hatchet (same class, same mountable ashes) is not
  in the data. INFERRED, not checked: availability (where each is found in the game is not in the
  regulation).

## 11. The paired loop (`er-builds-pvp.py --paired-loop`, 2026-09-30)

The user's loop: running R1 -> off-hand L1 with Hand Axe, Forked Hatchet or Icerind Hatchet. A
roll is caught by the next hatchet L1, then the attacker goes back to running R1. For every
one-handed row, each of the three hatchets (`PAIRED_LOOP_LEFTS`) is put in the left hand:
- at the affinity whose L1 #1 hits hardest at the row's own stats (requirement penalties
  included);
- with its cross-hand links carrying roll-catch (`er-mechanics-combo.paired_slots` `catch`): a
  roll-out-able or tie link also lands when the roll is caught (`p += (1 - p) x p_catch`, section
  10b's 0.67 for every hatchet), one L1 cycle later;
- scored per opening (section 6b).

The best of the three is kept.

Halberd 1H (sweep build STR 74 / DEX 12, Heavy), running R1 -> L1 (gap 28.9 frames against the
middle-stagger escape 25, so roll-out-able):

| left | affinity, L1 #1 damage | moveset before the skill term | running R1 engagement | L1 #1 lands | caught by L1 #2 | escapes |
|---|---|---|---|---|---|---|
| Hand Axe | Heavy, 324 | 611.2 | depth 1, 661 dmg over 82.3 frames | 0.22 | 0.52 | 0.26 |
| Forked Hatchet | Fire, 157 (DEX 12 < 14) | 498.5 | depth 0 (L1 not taken) | 0.22 | 0.52 | 0.26 |
| Icerind Hatchet | Standard, 55 (STR/DEX short) | 480.7 | depth 0 | 0.22 | 0.52 | 0.26 |

- The defender escapes by rolling away (0.26). Otherwise he either takes L1 #1 (0.22) or is
  caught by L1 #2 (0.52).
- Halberd 1H goes from rank 69 (611.0) to rank 10 (710.2). Halberd 2H falls 51 -> 67 (unchanged
  score, L1 guards).
- At the sweep's DEX 12, Forked Hatchet and Icerind Hatchet take the requirement penalty, so a
  STR halberd build has only the Hand Axe. The Hand Axe is kept on 386 of 394 rows, Forked
  Hatchet on 8.

Whole ranking (822 rows): 396 scores move.
- 1,392 family engagements take the L1: R1 351, R2 307, running jump R1 296, crouch R1 290.
- Largest rank rises: Misericorde 1H 384 -> 59, Main-gauche 434 -> 118, Pata 303 -> 13,
  Hookclaws 364 -> 71.
- Largest score gains: the one-handed small shields (+150 to +195).
- The running R1 -> hatchet engagement is taken by 361 rows. It scores highest on Nightrider
  Glaive, Guardian's Swordspear, Rakshasa's Great Katana, Bloodhound's Fang, Ripple Crescent
  Halberd and Nightrider Flail.

Against the corpus:
- The primary right-hand weapons that corpus builds pair with a hatchet are Misericorde 8,
  Greatsword 6, Zweihander 6, Fire Knight's Greatsword 4, Banished Knight's Halberd 4 and Red
  Bear's Claw 2.
- Spearman of the loop's score gain against that count, over 411 right weapons: **-0.087**.
- The loop gains on Misericorde (+170) and Banished Knight's Halberd (+104). It gains little on
  Greatsword (+6.8), Zweihander (+12.5) and Fire Knight's Greatsword (+9.0), whose large-stagger
  running R1 links are slower.
- Within-class score percentile adoption coefficient (`er-mechanics-ashes.py check`): **+0.546
  [+0.272, +0.799] -> +0.592 [+0.274, +0.875]**, within its CI. Ash coefficient +0.014 -> -0.030.

The flag stays off by default.

## 11a. Setups: each hand with its own weapon buff (`--setup`, 2026-10-01)

The play pattern modelled (the user's, a pattern, not evidence): halberd right, axe left; two-hand
the axe and cast its ash (Chilling Mist) on it; back to paired; grease the halberd; then running
R1 -> off-hand L1 -> roll-catch L1 -> running R1. `er-builds-pvp.py --setup` (implies
`--paired-loop`) generalises it to every one-handed right row x every left weapon with an off-hand
L1 (`--setup-lefts all`, 324) x every weapon buff each hand can carry.

### What the evidence says about the pattern's mechanics

| claim in the pattern | evidence | verdict |
|---|---|---|
| one weapon buff per hand, both live at once | 162 (right) and 163 (left) are different categories; the add path clashes only within one (buffs.md section 3, EXE). `FUN_1404f71e0` (1.16.2 decompile) picks a hit's status buff from entries that pass `IsApplicableForCategory`, the `wepParamChange` hand gate, and the AR accumulator uses the same gate | VERIFIED |
| Chilling Mist puts frost on the axe | the cast applies both chains, 825 -> 826 (162, `wepParamChange` 1) and 827 -> 828 (163, 2) (`skill_buffs`, TAE). 828 is stateInfo 152, so its `atkOccurrenceSpEffectId` 880 is applied on each left hit: **frost 60** (both AtkParam rates), not the 30 in 828's own `freezeAttackPower`, which no hit reads | VERIFIED, value corrected |
| greasing the halberd afterwards keeps both | the cast also put 826 (frost) on the right hand; the grease's 162 row replaces it (R3, same category). Elemental greases are stateInfo 151/158, never 152/153, so they do not take the status slot from 828 | VERIFIED; the order matters: grease first, then the ash, overwrites the halberd's grease with 826 |
| the buff survives the grip change | no `SpEffectParam` field deletes a row on grip change (the paramdef has none); engine-side removal on grip or weapon change is not traced | INFERRED (modelled as persisting) |
| running R1 -> L1 -> roll-catch timings | TAE frame data, sections 10b and 11 | unchanged |

Also from the rows: Cragblade (1821/1823), Royal Knight's Resolve (1701/1703, stateInfo 384/385
next-hit), Determination, Braggart's / Barbaric Roar, War Cry, Shriek of Sorrow, Sacred Blade,
Lightning Slash and Seppuku are all 162/163 weapon-buff rows, one per hand. Cast from the left
weapon they buff the right hand too, unless a grease replaces the right row. The Hand Axe roar
stick of section 10f is therefore a two-hand buff by the data, not a body buff.

### The model (`SetupBuffs`, `_setup_left_choice`)

- Left options per left row (affinity included): none; each grease of `--grease`'s tier where
  `isEnhance`; each mountable skill whose buff closure holds a 163 row. Roots gated by
  `conditionHp` (Shriek of Sorrow's 85/55/30 tiers) are dropped: the attacker is at full HP.
- Effect through the left-hand gate (`Buffs.attack_context` hand `left`): pre, flat, post; status
  through `er-mechanics-status.status_expected` with the 152/153 row's on-attack row, plus the
  left weapon's own status (Forked Hatchet's bleed is now scored).
- The left's hits now take the archetype buff kits and the defenders' buffs (`SetupBuffs.body`),
  as the right's always did. The previous loop gave the left neither.
- Uptime `er-mechanics-buffs.recast_plan` (fight 180..300 s since 2026-10-01, mean over 13
  points; it was 25 s = 5 engagements x 5 s, buffs.md section 10): first cast free; a buff that
  lapses is either left to lapse or recast (timed: ceil(fight / duration) - 1; next-hit: once per
  landed hit of that fight point, buffs.md section 10's schedule), capped by one FP bar plus the
  attacker's cerulean flasks (a grease: its `maxNum`), each recast costing its cast frames of the
  fight and each cerulean drink 54 frames. The right hand's grease is recast and charged the same
  way, only where the right hand keeps it (`Mechanics.grease_plan`).
- Choice: the (affinity, option) whose L1 #1 is worth most (damage + status HP, x time factor).
- Right hand: the sweep's grease, or the chosen skill's 162 row in its place (`spill`: every
  right slot re-hit with it, status rerun), whichever scores the moveset higher. Also tried: the
  skill whose 162 row raises the unpaired moveset most.
- `PROC_OPENING` / `--proc-opening`: hook for the per-hit proc-opening term (frostbite stun) owned
  by `er-mechanics-proc-opening.py`; off here.

### Halberd 1H (Heavy, STR 74 / DEX 12, Drawstring Dragonbolt), moveset before the skill term

| setup | left L1 #1 | moveset | over no left hand (480.7) |
|---|---|---|---|
| section 11 loop, Hand Axe, no kits on the left | 324 | 611.2 | +130.5 |
| Hand Axe Heavy, no left buff (kits and defender buffs on the left) | 306.6 | 597.8 | +117.1 |
| the pattern: Hand Axe Heavy + Chilling Mist, halberd greased | 308.2 (frost 1.6 HP per hit) | 597.8 | +117.1 |
| Hand Axe Heavy + Cragblade, Cragblade's 162 row on the halberd instead of the grease | 365.6 | 668.9 | +188.2 |
| best of all 324 lefts: Iron Cleaver Heavy + Cragblade on both hands | 437.8 | 721.5 | +240.8 |

- **Chilling Mist adds almost nothing here.** One L1 per engagement builds 60 frost against a
  gauge that refills between engagements, so it rarely procs: 1.6 HP per hit. What frost is worth
  is the proc's opening and its +damage-taken, neither scored (the proc-opening hook).
- The gain comes from a physical-rate weapon buff cast from the off-hand, which reaches both
  hands. On the halberd Cragblade's x1.15 physical beats the +135 lightning grease.

- Cragblade is mountable on 151 of the 324 off-hand candidates (`er-setup-mountable.py`,
  `can_mount`); the other 173 (unique weapons, sorcery swords, ...) never get it.

### Stamina (`er-setup-stamina.py`, MEASURED)

Halberd running R1 18, Hand Axe L1 12, catch L1 12: a loop costs 42 of the corpus median bar
145, so 3.4 loops back to back with no regeneration (4.8 without the catch). At 45/s the bar is
full again within the 5 s between engagements, and the pre-fight casts refill before contact.
The loop runs dry only past about three repeats in one string, or with a roll after the third.
Gap: the off-hand L1 carries no stamina factor (`left_1` has no exchange, links charge none);
only right-hand slots pay. The model never credits more than one loop per engagement, so this
does not move the ranking, but it is unmodelled.

### Ranking over the 394 one-handed rows, hatchet lefts (`er-builds-setup-rank.py`)

- Kept left: Hand Axe 380, Forked Hatchet 14. Left buff: Cragblade 223, Royal Knight's Resolve
  147, Sacred Blade 12, Lightning Slash 12. The right hand keeps the left ash's 162 row instead of
  the grease on 383 of 394 rows. Chilling Mist is never in any row's top six L1 options.
- Top setups (score with the skill term): Dane's Footwork + Hand Axe Keen / Royal Knight's
  Resolve 848.4, Backhand Blade + Hand Axe / Cragblade 824.6, Milady 796.5, Banished Knight's
  Halberd 778.5, Halberd 777.2.
- Corpus (Spearman of gain against pair counts): per right, hatchet pairings **-0.087** (the
  section 11 loop on the same footing: -0.069); per right, any off-hand -0.202 (loop -0.234); per
  pair 0.038; within right, mean of 5 rights with 3+ pairs, 0.820. The per-hand buffs do not
  improve agreement with what the corpus pairs.

### Ranking over all 324 off-hand lefts (`--setup-lefts all`, RL 150, 128,219 scored pairs)

- Kept left: Hand Axe 275, Wakizashi 43, Warped Axe 15, Greataxe 14, Dragonscale Blade 14,
  Dagger 11, Forked Hatchet 9, Dragon Halberd 5, Erdsteel Dagger 5, Iron Cleaver 4. Left buff:
  Cragblade 205, Royal Knight's Resolve 131, Lightning Slash 35, Ice Lightning Sword 14, Sacred
  Blade 13, Spinning Slash 5, Flame Spear 5. The right hand keeps the left ash's 162 row on 400 of
  411 rows (grease on 11).
- Top setups (score with the skill term): Dane's Footwork + Dragon Halberd / Spinning Slash 923.6
  (Dragonscale Blade / Ice Lightning Sword ties it exactly), Banished Knight's Halberd + Iron
  Cleaver Heavy / Cragblade 839.4, Halberd + Iron Cleaver Heavy / Cragblade 838.2, Dryleaf Arts +
  Dragon Halberd / Spinning Slash 836.4, Backhand Blade + Hand Axe Keen / Cragblade 824.6, Milady
  + Hand Axe Keen / Cragblade 796.5. Widening the lefts moves the winner two ways: a unique
  skill's 163 row the hatchets cannot carry (Spinning Slash, Ice Lightning Sword), or a heavier
  Cragblade carrier (Iron Cleaver on the halberds, +61 over Hand Axe). Hand Axe still holds 275
  of 411 rows.
- Corpus (Spearman of gain against pair counts): per right, hatchet pairings **-0.062** (hatchet
  lefts -0.087, section 11 loop -0.069); per right, any off-hand -0.158 (hatchet lefts -0.202,
  loop -0.234); per pair 0.010 (0.038); within right, mean of 21 rights with 3+ pairs, 0.064
  (hatchet lefts: 0.820 over 5, not the same rights). Still no agreement with what the corpus
  pairs: every per-right number stays negative.

### The same ranking over a 3 to 5 minute fight (2026-10-01, `rl150-setup-all-fight240.json`)

The user's point: a weapon-buff skill such as Cragblade (60 s) or Royal Knight's Resolve (next
hit) is worse than the 25 s fight made it look, because of its cast time. The fight the buffs
cover is now 180..300 s (buffs.md section 10), every buff is recast by one rule and charged its
cast frames, and the landed-hit count (5) and engagement spacing (5 s) are unchanged. Same
command, same 411 rows and 128,219 pairs.

| # | 25 s fight (before) | score | 180-300 s fight (after) | score |
|---|---|---|---|---|
| 1 | Dane's Footwork + Dragon Halberd / Spinning Slash | 923.6 | Banished Knight's Halberd + Iron Cleaver Heavy / Royal Knight's Resolve | 899.7 |
| 2 | Banished Knight's Halberd + Iron Cleaver Heavy / Cragblade | 839.4 | Halberd + Iron Cleaver Heavy / Royal Knight's Resolve | 898.5 |
| 3 | Halberd + Iron Cleaver Heavy / Cragblade | 838.2 | Dane's Footwork + Hand Axe Keen / Royal Knight's Resolve | 897.5 |
| 4 | Dryleaf Arts + Dragon Halberd / Spinning Slash | 836.4 | Milady + Hand Axe Keen / Royal Knight's Resolve | 838.5 |
| 5 | Backhand Blade + Hand Axe Keen / Cragblade | 824.6 | Dryleaf Arts + Hand Axe Keen / Royal Knight's Resolve | 819.9 |
| 6 | Milady + Hand Axe Keen / Cragblade | 796.5 | Nightrider Flail + Hand Axe Keen / Royal Knight's Resolve | 813.4 |
| 7 | Guardian's Swordspear + Butchering Knife Lightning / Royal Knight's Resolve | 771.5 | Pata + Hand Axe Keen / Royal Knight's Resolve | 803.4 |
| 8 | Nightrider Flail + Hand Axe Keen / Cragblade | 766.0 | Warhawk's Talon + Hand Axe Keen / Royal Knight's Resolve | 801.1 |
| 9 | Warhawk's Talon + Hand Axe Keen / Cragblade | 754.5 | Guardian's Swordspear + Butchering Knife Lightning / Royal Knight's Resolve | 800.7 |
| 10 | Pata + Hand Axe Keen / Cragblade | 751.1 | Great Epee + Hand Axe Keen / Royal Knight's Resolve | 790.3 |

- **Left buff kept:** Cragblade 205 -> 0, Royal Knight's Resolve 131 -> 410 (of 411; one row
  keeps none). Lightning Slash, Spinning Slash, Ice Lightning Sword, Sacred Blade and Flame Spear
  all go to 0. The right hand keeps the left ash's 162 row on 410 rows, the grease on 1.
- **Kept left:** Hand Axe 275 -> 345, Wakizashi 43 -> 15, Greataxe 14 -> 15, Dagger 11,
  Forked Hatchet 9, Iron Cleaver 4.
- **Halberd's best left:** Iron Cleaver Heavy still, now with Royal Knight's Resolve (898.5,
  gain +338.2 over no left hand) instead of Cragblade (838.2). Its L1 #1 options: Royal Knight's
  Resolve 464.2, Determination 431.1, Cragblade 385.0, Braggart's Roar 366.5, Drawstring
  Dragonbolt 358.7 (25 s: Cragblade 437.8, Royal Knight's Resolve 435.9).
- **Corpus (Spearman of gain against pair counts):** per right, any off-hand -0.158 -> -0.039;
  per right, hatchet pairings -0.062 -> 0.008; per pair 0.010 -> 0.015; within right (21
  rights) 0.064 -> 0.061. The paired-loop comparison also moved (-0.234 -> -0.150, -0.069 ->
  -0.007) because the no-left-hand baseline changed with the defender buffs, so most of the
  per-right shift is that baseline, not the setups. Still no agreement with the corpus.

Why it went this way, and what it does and does not test:
- **Cragblade did get worse, as the user said.** 60 s over 180-300 s is 2 to 4 recasts (mean
  3.38) at its 84-frame cast, a time factor of 0.961, where the 25 s fight charged nothing.
- **Royal Knight's Resolve got better, against the user's point, and that is the landed-hit
  count, not the game.** A next-hit buff is recast once per landed hit, and the fight still has
  5 landed hits, so it is 4 recasts whatever the fight length. Its 29-frame cast (first roll frame
  of its opening) was 4 x 29 frames out of 25 s (time factor 0.85); it is now 4 x 29 out of
  180-300 s (0.983). In a real 3 to 5 minute fight there are many more than 5 exchanges, and each
  one wants a recast. Keeping `hits` at 5 (as asked) while the fight grows makes every per-hit
  buff nearly free. The measurement this needs is the number of hits landed (or attempted) in a
  fight of that length, which would also replace the 5-hit fight everywhere else.
- **The defender side moved more than the buffs.** Uplifting Aromatic's one-hit x0.1 guard is
  now recast before each hit (buffs.md section 10: defender factor 0.917 -> 0.849). That is
  roughly uniform across weapons; it lowers every absolute score and the no-left baseline.
- Not charged: the cast's punish window (the next gap), the grip change a left skill needs.

### Landed hits per fight point and cerulean flasks (2026-10-01, later the same day)

The 5-hit artifact above is gone: `hits` is now buffs.md section 10's schedule (HP plus the
crimson flasks drunk, held to what each fight length leaves room for), mixed by planner tag
(duel 0.255 with 5 hits everywhere, invasion/gank 0.745 with 16..22; 52 points, mean 16.5), and a
left skill's recasts are funded by one FP bar plus the attacker's 4 cerulean flasks (220 FP each),
each drink charged 54 frames. Halberd 1H (Heavy, lightning grease) + Iron Cleaver Heavy, L1 #1
value per left option (`--rl 150 --weapon Halberd --one-handed --setup --setup-lefts "Iron
Cleaver" --json`):

| run | Royal Knight's Resolve | Determination | Cragblade | Braggart's Roar | kept (uptime, recasts, time factor) | setup score |
|---|---|---|---|---|---|---|
| `--fight-hits 5 --cerulean-flasks 0` (the old model) | **464.2** | 431.1 | 385.0 | 366.5 | RKR (1.0, 4, 0.9835) | 898.5 |
| default (corpus mix, 4 cerulean) | **456.3** | 425.2 | 401.1 | 381.8 | RKR (1.0, 15.54, 0.9279) | 876.9 |
| `--flasks invasion` (16..22 hits, 4 cerulean) | **453.2** | 422.7 | 406.4 | 386.9 | RKR (1.0, 19.38, 0.9093) | 868.9 |
| default mix, `--cerulean-flasks 0` (one FP bar) | **409.6** | 401.0 | 401.1 | 381.8 | RKR | 791.5 |

- The pinned row reproduces the 5-hit numbers above exactly (VERIFIED repro: same values to the
  tenth).
- Royal Knight's Resolve stays first, now for a stated reason: the cerulean flasks pay for one
  recast per landed hit (15.5 on average), and the drinks plus 29-frame casts cost 7% of the
  fight (time factor 0.928, against 0.984 at 5 hits). Its lead over Cragblade shrinks from 79 to
  55.
- Without cerulean on recasts its lead is 8.5 points, held up by the duel quarter of the points
  (5 hits, where one bar is enough); the scratchpad dig's no-cerulean runs had Cragblade first from
  18 hits on. So the order hinges on one unmeasured habit: whether players drink cerulean flasks
  to keep a per-hit weapon buff up through a long fight.
- Cragblade rises (385.0 -> 401.1 / 406.4) without any change to its own uptime: the defender
  factor on a standard hit goes 0.8487 -> 0.8855 (default) / 0.8978 (invasion) (MEASURED, the
  rows' `buff.def`). Uplifting Aromatic's one-hit x0.1 guard (maxNum 10) covers every one of 5
  hits but only 10 of 16..22, which is the INFERRED cause (the per-source split was not printed).

Full re-rank under the default (`--rl 150 --one-handed --setup --setup-lefts all --json --jobs 4`,
411 rows, 128,219 pairs, `er-builds-setup-rank.py`; MEASURED 2026-10-01):

| # | setup | score | before (5 hits, one bar) |
|---|---|---|---|
| 1 | Dane's Footwork + Hand Axe Keen / Royal Knight's Resolve | 881.3 | 897.5 (3rd) |
| 2 | Banished Knight's Halberd + Iron Cleaver Heavy / Royal Knight's Resolve | 878.0 | 899.7 (1st) |
| 3 | Halberd + Iron Cleaver Heavy / Royal Knight's Resolve | 876.9 | 898.5 (2nd) |
| 4 | Milady + Hand Axe Keen / Royal Knight's Resolve | 823.7 | 838.5 |
| 5 | Dryleaf Arts + Hand Axe Keen / Royal Knight's Resolve | 805.5 | 819.9 |
| 6 | Nightrider Flail + Hand Axe Keen / Royal Knight's Resolve | 803.8 | 813.4 |

- Kept left buff: Royal Knight's Resolve 410 of 411 (unchanged); right hand keeps its 162 row on
  410. Kept left: Hand Axe 346, Greataxe 19, Wakizashi 15, Dagger 11, Forked Hatchet 8.
- Scores fall 10..20 points (the recasts and drinks the schedule now charges) and the top three
  reorder within 3 points; nothing else moves.
- Corpus (Spearman of gain against pair counts): per right, any off-hand -0.039 -> -0.075; per
  right, hatchet 0.008 -> -0.007; per pair 0.015 -> 0.014; within right (21) 0.061 -> 0.061.
  Still no agreement with what the corpus pairs.

The flag stays off by default.

## 12. The hit-to-reaction delay (static RE, 1.16.2, 2026-09-30)

In PvP the delay is not an engine constant. Each player's own machine owns his character, and a
hit on a remote player is decided by the attacker's machine and applied by the victim's. So the
delay that matters for an escape is a network round trip, plus quantization on each side.

### Who decides a hit, and who plays the reaction (VERIFIED, 1.16.2)

```
attacker's machine, DmgManImpl task (0x140525670 registers 0x140527c10 / 0x140527d40)
  FUN_14044a910  hit dispatch (atk info, target, attacker)
    FUN_1404443e0  gate: target->IsImmuneToAttack (vtable +0x1d8;
                   PlayerIns::IsInvincible 0x140656e60 -> ChrIns::IsImmuneToAttack 0x1403f3b90,
                   reads the target's actionFlag bits on the attacker's machine: the replica)
    target damage module vtable +0x38 (slot 7):
      CSPlayerDamageModule 0x14044caf0 -> CSChrDamageModule 0x140445060
        mode = table[victim kind][dealer kind]   (0x142a364d0 / 0x142a36400, 7 x 7 dwords)
        kind (FUN_14044a1b0): 1 main player, 2 remote player, 3/4 NPC, 5/6 other
        victim 2 x dealer 1 -> mode 1 or 4; mode 4 -> slot 0x15 = 0x14044ce40:
          Packet15 built, PlayerNetworkSession->SendHitPacket
          + HitChr(param_4 = 1) on the replica (predicted copy; HP is not taken, 0x1404483b0)
        victim 1 x dealer 2 -> mode 0: no HitChr, so a remote attacker's hitbox never damages
          the local player directly; only his packet does

victim's machine, PlayerIns slot 17 (0x140660120, runs before the base ChrIns update)
  TryDequeuePacket20 loop (0x14066056b) -> FUN_14044cba0 -> HitChr 0x1404445e0 (param_4 = 0)
    -> ApplyDamage 0x1404497d0 -> CalculateDamage2 0x1404483b0:
         HP is taken (FUN_140436590) with no immunity check
         then: if actionFlag bit 8, or IsImmuneToAttack(victim) on the victim's own state,
               AttackDamageInfo+0x25f = 1           (write at 0x1404488dc)
    -> FUN_140446750 -> FUN_140445b20: damageLevel = 0 when +0x25f is set (read at
       0x140445b7a), and FUN_140446750 skips the knockback for the same flag
  then base ChrIns update 0x1404016d0 (call at 0x140660753), which runs the behavior modules
```

- The ChrIns update order inside a frame (`ChrIns_PreBehavior` -> `HavokBehavior` -> ... ->
  `NetFlushSendData` -> ... -> `DmgMan_Pre/ShapeCast/Post`) is COMMUNITY (the
  `CSTaskGroupIndex` order in `fromsoftware-rs` `cs/task.rs`); that hits run in DmgManImpl's
  tasks is VERIFIED above.
- On the victim, the packet is applied before the base update in the same call, so the behavior
  script sees the new level in the frame the packet is dequeued.
- Hitstop's effect on either clock was not traced.

### What it means for an escape

```
attacker clock, first hit on frame 0:
  victim's stagger starts       L1 + p          L1 = attacker -> victim leg
  victim rolls (gate g)         L1 + p + g      p = wait for the next ChrIns_PreBehavior
  attacker's replica rolls      L1 + p + g + L2 + q
                                                q = victim's send flush and the replica's
                                                    own update before the next hit test
  follow-up connects (damage)   gap < g + RTT + p + q
```

- **Damage** follows the attacker-side gate, so `delay = RTT + p + q`. `p` and `q` are each at
  most about one 60 fps update (0.5 model frame). The ranking's own model (2 x 50 ms legs,
  `er-mechanics-ashes.reaction_delays`) gives 100 ms = 3 frames, so `delay` is about 3-4 frames.
- **The follow-up's stagger** follows the victim-side check, which runs on the victim's own clock:
  the packet arrives at stagger frame `gap - p`. A roll begun at frame `g` has i-frames from its
  own frame 0, so when `gap - g` is inside them (halberd -> axe: 5 frames) the follow-up takes HP
  but plays reaction level 0. Latency moves the damage, not the stagger lock.
- Halberd running R1 -> Battle Axe L1 (gap 30, gate 25): damage lands on a roll only when
  `RTT + p + q >= 6` frames, a round trip of roughly 170-200 ms. Below that it is roll-out-able,
  with a window of `5 - delay` frames. Above it, the axe's damage lands but the defender keeps
  rolling, so no third hit is guaranteed.
- Ties (gap == gate) land their damage at any delay of 1 frame or more, which every online
  connection has. The `--delay` option of `er-mechanics-combo.py` models the damage half.

### What a runtime measurement would hook (1.17.1)

All below the 0xafefe9 boundary, so 1.17.0 == 1.17.1
(`docs/recon/rva-map-1162-to-1170.functions.tsv`, `scripts/map-rvas-1170-to-1171.py`); the two
+0x25f sites were byte-checked in `eldenring-deobf-1.17.1.bin`.

| 1.16.2 | 1.17.1 | what to log |
|---|---|---|
| 0x14044ce40 | 0x14044d3a0 | attacker: frame the hit packet is sent |
| 0x1404443e0 | 0x140444940 | attacker: immunity gate result per follow-up |
| 0x14044cba0 | 0x14044d100 | victim: frame the packet is applied |
| 0x1404483b0 (+0x52c) | 0x140448910 (0x140448e3c) | victim: +0x25f set, stagger cancelled |
| 0x140445b20 | 0x140446080 | victim: damageLevel written |
| 0x140660120 | 0x140660f70 | victim: per-frame PlayerIns update (frame counter) |

`p` is the gap between the packet's arrival and 0x14044d100; `q` and the replica's lag need a
timestamp of the victim's roll start on both machines.
