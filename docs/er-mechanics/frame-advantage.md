# Frame advantage, hitstun and true combos (player vs player)

Labels, as in the other `docs/er-mechanics/*.md`: **VERIFIED** = a regulation value (installed
1.17.1 `regulation.bin`) or code read out of the executable. EXE addresses are 1.16.2 VAs from the
named Ghidra dump on :8765 (shift 0 against `eldenring-deobf.bin`); carry one through
`scripts/map-rvas-1162-to-1170.py` and `scripts/map-rvas-1170-to-1171.py` before using it on the
running 1.17.1 game. **TAE** = decoded from the player TimeAct (`a00.tae` for reactions, the weapon
TAEs through `scripts/er-mechanics-attacks.py`) or the behavior graph `c0000.behbnd`
(`scripts/er-behbnd-attack-map.py`). **COMMUNITY** = the curated decompile of the player behavior
script `c0000.hks` for ER 1.08.1 (ividyon/EldenRingHKS, shipped in Smithbox's
`Documentation/ER/c0000.hks`), or another outside source. The installed script is compiled
HavokScript and was not decompiled here; the selftest only checks that the names the decompile
relies on are present in it. **INFERRED** = fits the data, consumer not traced. Nothing was
launched; no number here has runtime proof.

Tool: `python3 scripts/er-mechanics-frame-advantage.py` (section 8 for commands). Selftest 24/24.

## 0. What an optimizer needs

1. **A hit that does not break poise causes no hitstun.** With poise intact, small, middle, large,
   push and minimum hits are remapped to level 0, and level 0 plays only an additive flinch that
   leaves the defender's current action running (VERIFIED remap, COMMUNITY HKS). The defender can
   act on the frame of the hit, and a defender mid-swing keeps swinging. Frame advantage on such a
   hit is minus the attacker's own remaining recovery.
2. **A few damage levels react even with poise intact** (VERIFIED, SpEffect 6352): exlarge plays
   small blow, small blow plays middle, ex blast plays exlarge, and fling, upper and breath play
   themselves. Greatsword and Giant-Crusher charged R2 #2 and the Giant-Crusher guard counter are
   fling (level 6), so they knock the defender down whatever their poise.
3. **On a poise break the defender plays the attack's own `dmgLevel`** (or `dmgLevel_vsPlayer` when
   set; 243 AtkParam_Pc rows set it, none of the slots below). Poise then refills to full two
   toughness updates later, so the next hit breaks only if it alone reaches the full pool.
4. **Rolling out of a stagger is gated by a consecutive-stagger count** (COMMUNITY HKS + TAE): the
   first small stagger can roll on frame 10, the second on 7, the third on 4, the fourth on 0.
   Middle: 25/10/5/0. Large: 35/15/5/0. A roll has i-frames from its first frame (TAE).
5. **No chain in the four weapons below is a true combo on a poise break.** The next R1 lands
   18 (Uchigatana), 12-18 (Erdsteel Dagger) or 37-39 (Greatsword, Giant-Crusher) frames after the
   first, and the defender can roll on frame 10 (small, minimum) or 35 (large). A chained R2 #2
   first plays its charge-start clip until SpEffect 100280 lets the release through (section 5),
   which adds 8-15 frames: Uchigatana 2H R2 #1 -> #2 is 35 against 25, Giant-Crusher 49.6 against
   35. Over the 324 RL 150 sweep rows, 2 R2 #1 -> #2 links are true or tie on a break (93 were
   before the lead-in was counted).
6. **Trades** are decided by poise alone: the side hit first is interrupted only if that hit breaks
   its poise, with the hyperarmor bonus added while its window covers that frame.

## 1. Which reaction a player defender plays

### The level written for the behavior script (VERIFIED, 1.16.2)

```
CSChrDamageModule::HitChr 0x1404445e0
  level = ChrIns::GetDamageLevel 0x1406901d0
        = AtkParam.dmgLevel, or FUN_140d22e90(AtkParam.dmgLevel_vsPlayer) when the defender
          is a player and dmgLevel_vsPlayer != 0
  -> ChrDamageModule::ApplyDamage 0x1404497d0
       FUN_140486bf0: toughness (poise) damage is applied here, before the reaction is chosen
       -> FUN_140446750 -> FUN_140445960 -> FUN_140445b20:
            actionFlag->damageLevel (+0x1c) = FUN_140690250(level)
            actionFlag->receivedDamageType (+0x34), guardLevel (+0x20)

FUN_140690250(level):
  if guarded or level == 0: keep level
  elif toughness slot 6 (0x140486ba0) is true        # max > 1.19e-7, current > 0, not dead
      v = FUN_1404f8700: for a player (ChrIns vtable +0x118),
          SpEffectParam[toughness slot 9 (0x1404878c0)].dmgLv_*[level]   # byte at row+0x148+level
  else                                               # poise broken
      v = FUN_1404f8640: the highest-priority SpEffect with spCategory 1001, else 0
  return REMAP[v] if 0 < v < 13 else level           # FUN_140d22e90, table at 0x142bafc88
REMAP = 0, 0, 8, 1, 2, 3, 7, 4, 6, 9, 5, 10, 11
```

- Toughness slot 9 returns `ToughnessParam[current row].spEffectId`. Only when the row is missing
  does it fall back to 6350 + tier, the tier chosen by `toughnessDurablityUnk` against
  30/50/70/100 (floats at 0x143b179f0, base at 0x143b17a00). VERIFIED.
- REGULATION: 58 of the 60 `ToughnessParam` rows, row 0 (no hyperarmor window) and every weapon
  hyperarmor row among them, name SpEffect **6352**. Rows 50 and 51 name 6353, which maps every
  level to 1 (none), so a window on those rows suppresses every reaction.
- The per-level byte is `SP_EFFECT_PARAM_ST.dmgLv_*`. The Smithbox paramdef puts `dmgLv_None` at
  +0x148, the offset the EXE reads (selftest checks both paramdef variants).

SpEffect 6352, i.e. what each level becomes while poise holds (VERIFIED):

| level | name (paramdef field) | 6352 value | plays with poise intact |
|---|---|---|---|
| 1 | small (`dmgLv_S`) | 1 | 0 none |
| 2 | middle (`dmgLv_M`) | 1 | 0 none |
| 3 | large (`dmgLv_L`) | 1 | 0 none |
| 4 | exlarge (`dmgLv_BlowM`) | 6 | 7 small blow |
| 5 | push | 1 | 0 none |
| 6 | fling (`dmgLv_Strike`) | 0 | 6 fling (kept) |
| 7 | small blow (`dmgLv_BlowS`) | 4 | 2 middle |
| 8 | minimum (`dmgLv_Min`) | 1 | 0 none |
| 9 | upper (`dmgLv_Uppercut`) | 0 | 9 upper (kept) |
| 10 | ex blast (`dmgLv_BlowLL`) | 7 | 4 exlarge |
| 11 | breath | 0 | 11 breath (kept) |

The level names 1..11 match the behavior-graph states `DamageLv1_Small` .. `DamageLv11_Breath`
(TAE) and the paramdef field order; the numeric constants themselves live in a script file that
is not in the extraction, so the pairing is COMMUNITY + TAE, not traced.

### Poise refill (VERIFIED, `FUN_140486bf0` / update `FUN_140486e50`)

When a hit takes current toughness to 0 or below it is clamped to 0, `field_0x2a` and
`field_0x2c` are set, and `FUN_14047e540` raises bit 3 of `actionFlag+0x2c` and sets the
knockback distance. The next update copies `field_0x2a` into `field_0x2b`; the update after
that sees `field_0x2b` and refills current toughness to max. Without a break, a separate timer
(`field_0x20`, reset by hits whose `+0x24` flag is set) refills to max when it runs out. So the
second hit of a string meets a full pool. The update runs once per character update (INFERRED:
once per frame).

### What the behavior script does with the level (COMMUNITY, `ExecDamage`)

- Level 0 -> `ExecAddDamage`: sets `AddDamageLv0_Blend` and fires `W_AddDamageLv0` with
  `ExecEventNoReset` on the additive state machine `AddDamageLv0_SM` (clips `a000_010000..2`), and
  returns FALSE, so the caller's state is not changed. Those clips carry no JumpTable events at all
  (TAE), so they cannot close any action window.
- Levels 1, 2, 3, 5, 8 (and weak point) call `CalcDamageCount` (`DamageCount += 1`,
  `UseChainRecover = 1`) and fire `W_DamageLv<n>_...`. Levels 4, 6, 7, 9, 10, 11 call
  `ResetDamageCount`.
- Large plays `W_DamageLarge2` (5350..) instead of `W_DamageLv3_Large` when
  `env(GetBehaviorID, 3)` is true. What behavior id 3 is was not traced; both are in the table.
- `ExecDamage` is called from `ExecPassiveAction`, which every damage state runs first each update
  through `DamageCommonFunction`, so a hit taken during a stagger is evaluated again: a level-0
  hit (poise refilled) leaves the stagger running, a new break restarts it.
- Guarded hits branch to guard reactions (`GetGuardLevelAction`), not covered here.

## 2. Reaction timings (TAE, `a00.tae`; `--reactions`)

Frames at 30 fps from the reaction clip's first frame. R1/R2/guard/move use the same JumpTable
input/cancel pairs as the attacks doc (section 4 there). The roll additionally needs the HKS gate
in section 3. Every clip of a state was read; they agree except where a spread is shown.

| level | reaction | clips | clip len | R1 | R2 | guard | move | roll after 1st/2nd/3rd/4th consecutive stagger |
|---|---|---|---|---|---|---|---|---|
| 0 | none (additive flinch) | 010000..010002 | 15 | 0 | 0 | 0 | 0 | 0 (not locked) |
| 8 | minimum | 005000..005002 | 15 | 7 | 7 | 10 | 11 | 10 / 7 / 4 / 0 |
| 1 | small | 005100..005134 (20) | 34 | 7 | 7 | 10 | 15 | 10 / 7 / 4 / 0 |
| 2 | middle | 005200..005234 (20) | 57 | 24 | 24 | 25 | 28 | 25 / 10 / 5 / 0 |
| 3 | large | 005300..005334 (20) | 60 | 30 | 30 | 35 | 37 | 35 / 15 / 5 / 0 |
| 3 | large (`DamageLarge2`) | 005350..005353 | 74 | 40 | 40 | 41 | 57 | 40 / 15 / 5 / 0 |
| 5 | push | 005500..005530 | 55 | 35 | 35 | 40 | 43 | 35 / 15 / 5 / 0 |
| 7 | small blow | 005400..005430 | 105 | 93 | 93 | 95 | 95 | 36 |
| 4 | exlarge | 005450..005480 | 120 | 107 | 107 | 109 | 111 | 50 |
| 10 | ex blast | same clips as exlarge | 120 | 107 | 107 | 109 | 111 | 50 |
| 6 | fling | 005700 | 80 | 59 | 59 | 59 | 61 | 23 |
| 9 | upper | 005710..005712 | 109 | 89-99 | 89-99 | 89-99 | 91-101 | 63 |
| 11 | breath | 005600..005630 | 120 | 112-116 | 112-116 | 114-118 | 116-120 | 50 |

- The TAE opens the roll input and cancel (JumpTable 25/26) from frame 0 in every small, middle,
  large, push and minimum clip; the later numbers come from the HKS gate. For levels that reset
  `DamageCount` the gate is off and the TAE window is the answer (the knockdown ones fire
  `W_EStepDown` for exlarge, per `ExecEvasion`'s `ESTEP_DOWN`).
- Every stagger clip also carries JumpTable 71 (`SetPoiseBrockenState` on the super-armor module)
  for its whole length, and JumpTable 111/112 (`EMERGENCYSTEP` input and cancel) on its first 3-5
  frames (0-3 in `005100`, 0-5 in `005300`).
  The emergency step needs `L1` held and `IsEmergencyEvasionPossible` (COMMUNITY
  `GetEvasionRequest`); what grants it was not traced, so it is not treated as an escape.
- Standing roll clips `a000_027100..027127` have JumpTable 8 (i-frames) from frame 0 (TAE), so the
  roll's first frame is already invulnerable.

## 3. Rolling out: the consecutive-stagger gate (COMMUNITY + TAE)

`ExecEvasion` (1.08.1 decompile), when the request is a roll and `UseChainRecover` is set:

```
DamageCount >= 4: needs env(GetEventEzStateFlag, 5)
DamageCount == 3: flag 4
DamageCount == 2: flag 3
DamageCount <= 1: flag 2
```

The flags are TAE event 227 (`EventEzStateFlag<HKS_env301>` in WitchyBND's template, arg 0 = flag
id). In `a000_005100` flag 2 opens on frame 10, 3 on 7, 4 on 4, 5 on 0 (TAE). `DamageCount` is
cleared by rolling, backstepping, jumping, and by any action taken out of a damage state
(`DamageCommonFunction`), so it counts staggers taken back to back without acting. The installed
1.17.1 `c0000.hks` contains `UseChainRecover`, `DamageCount`, `DamageCommonFunction`,
`ExecAddDamage`, `W_AddDamageLv0` and the stagger events (selftest); that the gate logic itself is
unchanged since 1.08.1 is not proven.

## 4. Frame advantage per attack slot

Definition used by the tool, measured from the attack's first hit frame:

```
attacker_ready = min(cancel_frame over r1, r2, dodge, guard, move) - first hit frame
defender_ready = delay + min(R1, guard, roll[count]) of the reaction clip    (0 if not locked)
advantage      = defender_ready - attacker_ready      (positive: the attacker acts first)
```

`delay` (frames between the hit and the reaction clip starting) is 0 by default and is not known
(section 9). Hitting later in the hit window raises the advantage one for one. `count` = which
consecutive stagger this is (default 1).

Columns: `hit` = hit window; `lvl` = AtkParam `dmgLevel`; `break`/`intact` = reaction level with
poise broken / holding; `att` = attacker_ready; `def` = defender_ready on a break (and the
action); `adv break`/`adv intact` = advantage. All regulation 1.17.1, `DamageCount` 1.

### Greatsword 4000000, two-handed

| slot | hit | lvl | break | intact | att | def (break) | adv break | adv intact |
|---|---|---|---|---|---|---|---|---|
| R1 #1 | 21-25 | 3 | 3 large | 0 | 16 | 30 R1 | +14 | -16 |
| R1 #2 | 22-26 | 3 | 3 | 0 | 17 | 30 | +13 | -17 |
| R1 #3 | 23-27 | 3 | 3 | 0 | 23 | 30 | +7 | -23 |
| crouch R1 | 14-16 | 3 | 3 | 0 | 16 | 30 | +14 | -16 |
| R2 #1 | 17-21 | 3 | 3 | 0 | 20 | 30 | +10 | -20 |
| R2 #2 | 21-23 | 3 | 3 | 0 | 22 | 30 | +8 | -22 |
| charged R2 #1 | 48-53 | 3 | 3 | 0 | 25 | 30 | +5 | -25 |
| charged R2 #2 | 60-63 | 6 | 6 fling | 6 fling | 23 | 23 roll | 0 | 0 |

### Giant-Crusher 23110000, two-handed

| slot | hit | lvl | break | intact | att | def (break) | adv break | adv intact |
|---|---|---|---|---|---|---|---|---|
| R1 #1 | 22-25 | 3 | 3 | 0 | 15 | 30 R1 | +15 | -15 |
| R1 #2 | 25-28 | 3 | 3 | 0 | 12 | 30 | +18 | -12 |
| R1 #3 | 27-30 | 3 | 3 | 0 | 21 | 30 | +9 | -21 |
| crouch R1 | 19-21 | 3 | 3 | 0 | 21 | 30 | +9 | -21 |
| R2 #1 | 32-34 | 3 | 3 | 0 | 17 | 30 | +13 | -17 |
| R2 #2 | 18-20 | 3 | 3 | 0 | 24 | 30 | +6 | -24 |
| charged R2 #1 | 58-60 | 6 | 6 fling | 6 fling | 18 | 23 roll | +5 | +5 |
| charged R2 #2 | 53-55 | 6 | 6 fling | 6 fling | 26 | 23 roll | -3 | -3 |

### Uchigatana 9000000, two-handed

| slot | hit | lvl | break | intact | att | def (break) | adv break | adv intact |
|---|---|---|---|---|---|---|---|---|
| R1 #1 | 14-17 | 1 | 1 small | 0 | 3 | 7 R1 | +4 | -3 |
| R1 #2 | 15-19 | 1 | 1 | 0 | 5 | 7 | +2 | -5 |
| R1 #3 | 13-17 | 1 | 1 | 0 | 5 | 7 | +2 | -5 |
| R1 #4 | 13-17 | 1 | 1 | 0 | 7 | 7 | 0 | -7 |
| R1 #5 | 17-20 | 1 | 1 | 0 | 14 | 7 | -7 | -14 |
| crouch R1 | 12-15 | 1 | 1 | 0 | 10 | 7 | -3 | -10 |
| R2 #1 | 13-16 | 2 | 2 middle | 0 | 11 | 24 R1 | +13 | -11 |
| R2 #2 | 9-13 | 2 | 2 | 0 | 18 | 24 | +6 | -18 |

### Erdsteel Dagger 1150000, one-handed

| slot | hit | lvl | break | intact | att | def (break) | adv break | adv intact |
|---|---|---|---|---|---|---|---|---|
| R1 #1 | 10-12 | 8 | 8 minimum | 0 | 4 | 7 R1 | +3 | -4 |
| R1 #2 | 8-10 | 8 | 8 | 0 | 4 | 7 | +3 | -4 |
| R1 #3 | 9-11 | 8 | 8 | 0 | 4 | 7 | +3 | -4 |
| R1 #4 | 8-10 | 8 | 8 | 0 | 4 | 7 | +3 | -4 |
| R1 #5 | 9-11 | 8 | 8 | 0 | 4 | 7 | +3 | -4 |
| R1 #6 | 14-16 | 1 | 1 small | 0 | 14 | 7 | -7 | -14 |
| crouch R1 | 10-12 | 8 | 8 | 0 | 7 | 7 | 0 | -7 |
| R2 #1 | 12-15 | 8 | 8 | 0 | 9 | 7 | -2 | -9 |
| R2 #2 | 5-8 | 8 | 8 | 0 | 16 | 7 | -9 | -16 |

- Crouch R1: Greatsword reads its own `a026_032310`; Giant-Crusher (`a031`), Uchigatana (`a029`)
  and Erdsteel Dagger (`a020`) have no crouch clip and use the rolling R1's, the attacks doc's
  fallback. Its row therefore equals the rolling R1 for those three.
- Clip sources for the R2s: Greatsword `a136`, Giant-Crusher `a197`, Erdsteel Dagger `a103` (each
  weapon's `spAtkcategory`); everything else from `wepmotionCategory` (a26, a31, a29, a20).
- The defender's earliest action after small, middle and large is R1, not the roll. An R1 out of a
  stagger has no i-frames, so for escaping the roll frame is what matters; `roll_advantage` in the
  JSON gives that difference.

## 5. True combos

A follow-up is a true combo when its first hit frame comes before the defender's first escape
(roll, which is invulnerable from its first frame, or guard) out of the reaction the first hit
caused. `gap` = frames from the first hit's first frame to the follow-up's first hit frame,
through the attacker's cancel into it. The follow-ups modelled are the ones the HKS plays by
default (COMMUNITY): R1 #n -> R1 #n+1 through the R1 cancel, uncharged R2 #1 -> R2 #2 through
the R2 cancel.

### The chained R2 #2 pays a release lead-in

R2 #1 -> R2 #2 does not go straight to the clip that hits. `AttackRightHeavy1End_onUpdate`
(COMMUNITY, Smithbox `c0000.hks` line 7778; the two-handed `AttackBothHeavy1End` likewise) sends
R2 to `W_AttackRightHeavy2Start`, and `AttackRightHeavy2Start_onUpdate` (line 7788) moves on to
`W_AttackRightHeavy2End` only when R2 is up and
`GetGeneralTAEFlag(TAE_FLAG_CHARGING) == 1 or GetSpEffectID(100280)`. The R2 #2 hit frames are
measured from the start of the End clip, so `gap` includes `lead_in` = the first frame of
SpEffect 100280 in the Start clip (`er-mechanics-attacks.release_lead_in`, real frames). A link
whose lead-in cannot be read (Start clip or its SpEffect event absent) gets no verdict (None)
instead of an optimistic one.

The flag half of the gate cannot open the release earlier (VERIFIED, 1.16.2, plus TAE):

- `GetGeneralTAEFlag` (the `HksEnv` case, `FUN_1404167b0`) tests bit n of
  `CSChrBehaviorDataModule+0x308`.
- `PreBehaviorSafe` clears that field every frame (`FUN_1404146b0`, `mov qword [rcx+0x308], 0`).
- Its only setter, `0x140416930` (`bts`), is reached from `CSChrTaeAnimEvent::ExecuteThreadOne`
  for TAE event 600 (`cmp eax, 0x258; je 0x14042e764`), with bit = the event's first argument
  (WitchyBND names it `EnableBehaviorFlags`, `Mask (0-63)`).
- None of the 1164 player R2 Start/End clips (0305xx, 0325xx, 0405xx, 0425xx in every `a*.tae`)
  carries an event 600, so whatever number `TAE_FLAG_CHARGING` is, the flag is never set in
  them. SpEffect 100280 is the only way through.
- The Start clips apply 100280 with TAE 67; 66 and 67 both reach
  `CSChrTaeAnimEvent::AddSpEffect` (0x14042bfd0, 67 with `doNotSync` set), so that window is the
  one that counts.

| string | gap (lead-in) | escape on break (1st stagger) | verdict |
|---|---|---|---|
| Greatsword 2H R1 #1 -> #2 | 37 | 35 (large: roll/guard) | no |
| Greatsword 2H R1 #2 -> #3 | 39 | 35 | no |
| Greatsword 2H R2 #1 -> #2 | 55.2 (14.2) | 35 | no |
| Giant-Crusher 2H R1 #1 -> #2 | 37.4 | 35 | no |
| Giant-Crusher 2H R1 #2 -> #3 | 39 | 35 | no |
| Giant-Crusher 2H R2 #1 -> #2 | 49.6 (14.6) | 35 | no |
| Uchigatana 2H R1 #1 -> #2, #2 -> #3, #3 -> #4 | 18 | 10 (small: roll) | no |
| Uchigatana 2H R1 #4 -> #5 | 24 | 10 | no |
| Uchigatana 2H R2 #1 -> #2 | 35 (15) | 25 (middle: roll/guard) | no |
| Erdsteel Dagger R1 #n -> #n+1 (n = 1..4) | 12-13 | 10 (minimum: roll) | no |
| Erdsteel Dagger R1 #5 -> #6 | 18 | 10 | no |
| Erdsteel Dagger R2 #1 -> #2 | 20 (8) | 10 | no |

Over the 324 rows of `er-builds-pvp.py --rl 150`, R2 #1 -> #2 on a break went from 80 true and 13
tie without the lead-in to 1 true and 1 tie with it, both Raptor Talons (2H 24.9 against 25, 1H
25 against 25). The 5 twinblade rows have no verdict either way.

- **On a non-break nothing is a true combo**: level 0 does not lock the defender at all. The only
  exceptions are the always-reacting levels of section 1 (fling, upper, breath, and exlarge /
  small blow / ex blast in their reduced form).
- `tie` = the follow-up lands on the same frame the roll can start; the order of the hit and the
  defender's behavior update on that frame decides, and it was not traced.
- If the follow-up also breaks poise (it has to reach the full pool again, section 1), the
  stagger restarts at that hit with `DamageCount` + 1, and the next gap is judged against the
  earlier gate (`--count 2`: small 7, middle 10, large 15). Back-to-back breaks therefore get
  easier to roll out of, which is what the gate exists for.
- Not modelled: R1 -> R2, crouch/rolling R1 -> R1. The HKS sends those through `*SubStart` clips
  (`030080..030091`, `032080..032091`, `032501`) that carry no hitbox in these categories (TAE), and
  the lead-in from them to the hitting clip was not traced.

## 6. Trades against hyperarmor

`trade(tables, first, second, offset, second_poise, first_poise)`: `first` starts on frame 0,
`second` on `offset`; whoever's first hit frame is earlier strikes. The struck side is interrupted
only if the hit breaks its poise (reaction level otherwise 0, section 1). Poise held = armor
poise + the hyperarmor bonus when the struck side's 795 window covers that frame of its own clip.
Poise dealt (VERIFIED multiply order in `FUN_140486bf0`, both sides players):

```
hit[+0x100] * hit[+0x244] * FinalDamageRateParam[atk.finalDamageRateId].saRate
            * (ToughnessParam[window row].unk1 while the struck side's window is active)
            * toughnessDamageCutRate * damageRatio
```

The tool takes `hit[+0x100]` = the attacks doc's `poise_damage` and `+0x244` = 1.0; both are
VERIFIED for a melee hit on a player (`FUN_140d24b10` stores `poise_damage` unscaled, and
`CalculateDamage` sets `+0x244` to 1.0, overridden only for arrows or `partsDmgType != 0`, which
no armor row sets). `saRate` is large on these rows: Longsword/Uchigatana R1 #1 2.2, chain R1s 1.35,
Greatsword/Giant-Crusher 2H R1 #1 3.5, R2 #1 3.85 (REGULATION). Examples with 51 menu poise on
both sides (5.1 internal):

| first (frame 0) | second | offset | hits first | struck side in HA | dealt / held | result |
|---|---|---|---|---|---|---|
| Greatsword 2H R1 #1 | Uchigatana 2H R1 #1 | 0 | Uchigatana, f14 | yes (GS f10-30, row 101) | 6.44 / 14.1 | trade |
| Greatsword 2H R1 #1 | Uchigatana 2H R1 #1 | 10 | Greatsword, f21 | no | 65.5 / 5.1 | Uchigatana interrupted |
| Uchigatana 2H R1 #1 | Giant-Crusher 2H R1 #1 | 0 | Uchigatana, f14 | yes (GC f10-42) | 6.44 / 15.0 | trade |

The 6.44 already includes the PvP hyperarmor factor `unk1` 0.45 of rows x1. A straight-sword R1
#1 deals 11.0 internal (110 menu) between players; the community "51 poise survives one R1"
breakpoint is PvE, and the community 1.17 PvP poise table gives the same 110 (VERIFIED 2026-10-01, bd `pvp-poise-units-resolved-51-is-pve-breakpoint-2026-10-01`).

## 7. API for the PvP ranking

```python
FA = _load('er_mechanics_frame_advantage', 'er-mechanics-frame-advantage.py')
reg = FA.ATTACKS.Regulation()
tables = FA.Tables(reg)
rows = FA.slot_profile(reg, tables, weapon_id, grip='both', slots=None, delay=0, count=1)
```

Each row: `slot`, `label`, `anim`, `atk_row`, `hit_windows`, `cancel_frame`, `hyperarmor`
(`frames`, `row`, `bonus`), `poise_damage`, `pvp_sa_rate`, `dmg_level`, `dmg_level_vs_player`,
`reaction_on_break`, `reaction_poise_intact`, `on_break` / `on_intact` (`attacker_ready`,
`defender_ready`, `defender_action`, `advantage`, `roll_advantage`, per-action `defender`), and
`combos` (`next`, `via`, `on_break` / `on_intact` with `gap`, `lead_in`, `escape`, `escape_by`,
`verdict`; `gap` already contains `lead_in`, so a consumer reads the verdict as given).
Also: `reaction(level, large2=False)`, `reaction_level(tables, atk_row, poise_broken,
toughness_row=0)`, `advantage(...)`, `combo(...)`, `trade(...)`, `pvp_poise_damage(...)`.

## 8. Commands

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py            # section 4-5 tables
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py --reactions
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py Uchigatana --grip both --count 2
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py Greatsword --grip both --json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-frame-advantage.py --trade Greatsword:both:2h_r1_1 Uchigatana:both:2h_r1_1 --offset 0 --poise 5.1 5.1
# the EXE reads behind section 1 (Ghidra MCP on :8765, 1.16.2):
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140690250"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140445b20"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"0x140486bf0"}'
# the behavior-graph clip lists (takes over two minutes):
python3 /home/banon/projects/er-mods-rs/scripts/er-behbnd-attack-map.py ~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/c0000-behbnd-dcx
```

Selftest (33 passed, 0 failed, 0 skipped):

| check | reference |
|---|---|
| remap table at 0x142bafc88, fallback thresholds and base at 0x143b179f0/0x143b17a00, player toughness vtable slots 6 and 9 | EXE `eldenring-deobf.bin` (1.16.2) |
| general TAE flag: setter bytes at 0x140416930, the getter's `test [rsi+0x308]`, event 600's jump to the setter, the per-frame clear | EXE `eldenring-deobf.bin` (1.16.2) |
| Greatsword 2H R2 #1 -> #2 gap includes R2 #2's lead-in; its Start clips carry no TAE 600; an R2 link with no lead-in has no verdict | attacks module + TAE |
| `dmgLv_*` sit at +0x148..0x153 in both paramdef variants | Smithbox paramdef against the offset the EXE reads |
| ToughnessParam row 0 -> 6352; 6352's twelve bytes; the derived intact remap | REGULATION 1.17.1 |
| installed `c0000.hks` contains the seven names the decompile's logic uses | installed HKS bytecode |
| `a000_005100` flags 2/3/4/5 open at 10/7/4/0; roll i-frames from frame 0; additive clips have no JumpTable events; small stagger R1 7, roll 10/7/4/0 | TAE `a00.tae` |
| event 227 is `EventEzStateFlag<HKS_env301>` | COMMUNITY: WitchyBND template |
| Uchigatana 2H R1 #1 -> #2 gap 18 against roll 10 | attacks module + reaction table |

## 9. Not established

- **Reaction start delay.** Whether the reaction clip starts on the hit frame or one frame later
  depends on the order of the damage step and the defender's behavior update; `--delay` shifts
  every defender number. No runtime measurement was made.
- **Same-frame order** for the `tie` verdict (hit against roll start).
- **The R2 release frame itself.** The lead-in ends on the first frame SpEffect 100280 is applied;
  whether `AttackRightHeavy2Start_onUpdate` sees it that frame or the next, and whether the End
  clip's frame 0 is that frame, depends on the TAE / HKS update order, which was not traced. The
  numeric value of `TAE_FLAG_CHARGING` (set in `common_define.hks`) was not decoded; it does not
  matter for R2, since no R2 clip sets any general TAE flag.
- **The HKS is the 1.08.1 decompile**, not the installed 1.17.1 script. The gate (`DamageCount` ->
  flag 2..5), the `ExecAddDamage` path and `DamageCommonFunction`'s action order are COMMUNITY. The
  installed file was only checked for names. Decompiling the installed HavokScript would settle it.
- The env id behind `GetDamageLevel` (236) reading `actionFlag->damageLevel`: the `HksEnv` case that
  returns that field was found (0x140410820, `case 9` of a sub-switch), but the sub-switch's base id
  was not decoded.
- `env(GetBehaviorID, 3)` (large vs `DamageLarge2`).
- The level-number constants (`DAMAGE_LEVEL_*`) are defined outside the extracted script; the
  mapping rests on the graph state names and the paramdef field order.
- Resolved 2026-10-01: poise units for the break test. `saRate` applies, and 51 is a PvE
  breakpoint (section 6). Still INFERRED: the menu's x1000 on the defender's pool, which a Frida
  read of `CSPlayerToughnessModule+0x18` at rest would close.
- Category-1001 SpEffects that override the level on a break (`FUN_1404f8640`); none are assumed.
- What grants `IsEmergencyEvasionPossible` (the frame 0-3 emergency step in every stagger).
- Guard as an escape: the guard-ready frame is used, but the guard's own time to become effective
  was not read.
- R1 -> R2 and crouch/rolling R1 follow-ups through the `*SubStart` clips.
- The defender acting out of a stagger with an attack can trade with the follow-up; that
  interaction is not modelled beyond `trade()`.
- Guard and parry reactions (`W_GuardDamage*`, `W_Repelled_*`, `W_GuardBreak`).
