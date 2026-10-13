# Elden Ring build mechanics for the build optimizer

The Elden Ring counterpart of ds2-mods-rs's `DS2-BUILD-MECHANICS.md` and `DS2-DPS-MECHANICS.md`.
Same labels: **VERIFIED** (regulation params, or the executable read through the named 1.16.2
Ghidra dump and re-checked against `eldenring-deobf-1.17.1.bin`), **INFERRED**, **SITE**
(er-build-planner), **COMMUNITY**. Nothing was launched. All data is read live from the installed
1.17.1 `regulation.bin` through `scripts/er-param-read.py`.

| topic | doc | calculator | validated against |
| --- | --- | --- | --- |
| Attack rating, scaling, status build-up, spell buff | [attack-rating.md](attack-rating.md) | `scripts/er-mechanics-ar.py` | 44/44: CalcCorrectGraph points plus ThomasJClark's calculator run on its own 1.17 data |
| Defense, absorption, the damage curve, resistances, poise | [defense.md](defense.md) | `scripts/er-mechanics-defense.py` | planner computed values on 5044 builds: defense 99.0%, absorption 91.6%, poise 99.98% |
| HP, FP, stamina, equip load, load tiers, rune cost, gear stat changes | [resources.md](resources.md) | `scripts/er-mechanics-resources.py` | planner computed values on 5699 builds: HP 94.6%, FP 99.0%, stamina 98.7%, equip load 99.5% |
| Weapon to attack chain, motion values, poise damage, stamina cost, hyperarmor | [attacks.md](attacks.md) | `scripts/er-mechanics-attacks.py` | 22/22: decompiled row formula, animation events, community breakpoints |
| Every talisman: effects, conditions, where each field enters a hit | [talismans.md](talismans.md) | `scripts/er-mechanics-talismans.py` | selftest: float32 HP gates, truncation, CalcCorrectGraph 50, slot subcategories re-derived over 458 weapons |
| Status build-up per hit, gauge and refill, proc damage, PvP hits to proc | [status.md](status.md) | `scripts/er-mechanics-status.py` | 31/31: exe constants, AR status references, gauge arithmetic, proc rows |
| Reach, hitbox shape, tracking (turn speed), animation play speed | [reach.md](reach.md) | `scripts/er-mechanics-reach.py`, `scripts/er-hkx-pose.py` | 36/36 and 29/29: FLVER header bbox, Smithbox enums, TimeAct template, handler bytes in 1.16.2 and 1.17.1, pose against bind pose and floor contact |
| Self-buffs (spells, tears, great runes, consumables), where each SpEffect column enters a hit, the stacking rules | [buffs.md](buffs.md) | `scripts/er-mechanics-buffs.py` | 83 checks: regulation rows, the add-path rules replayed on known pairs, hand/sub-category/role gates |
| Powerstance and off-hand attacks, blocking: repel, guard break, guard boost, chip, shield and powerstance adoption | [powerstance-guard.md](powerstance-guard.md) | `scripts/er-mechanics-powerstance-guard.py` | 23/23: behavior-graph states, TAE hand bytes, HKS category list, 1.16.2 guard constants |
| Hit reactions, poise-hold remap, frame advantage on hit, true combos, trades | [frame-advantage.md](frame-advantage.md) | `scripts/er-mechanics-frame-advantage.py` | 24/24: decompiled reaction path and SpEffect 6352 remap, TAE roll gates |
| Cross-hand combos: right-hand attack into the off-hand L1, L1 cancel ids, SubStart, push and reach | [combo.md](combo.md) | `scripts/er-mechanics-combo.py` | 24/24: JumpTable 9/16/87/117 case bytes, HKS routing, TAE frames, knockback fields |
| Ashes of war and weapon skills: skill lookup, melee / bullet / buff events, FP, bullet and melee damage, buff durations, adoption | [ashes-of-war.md](ashes-of-war.md) | `scripts/er-mechanics-ashes.py` | 33/33: decompiled skill lookup, FP half-cost rule and bullet damage, audited ash classification |
| Backstabs, ripostes, crit multiplier, parry rules, parry tools, parry exposure | [crits.md](crits.md) | `scripts/er-mechanics-crits.py` | 24/24: decompiled crit factor and parry test, ThrowParam rows, TAE import chain and windows |
| Void tech: jump casts and jump projectiles that fire twice on the landing frame, who can, why, and that both copies are sent | [void-tech.md](void-tech.md) | `scripts/er-mechanics-voidtech.py` | 12/12: behavior-graph selectors, TAE spawn frames, runtime doubles (Bestial Sling, Smithscript Dagger NPC and player), 88 melee jumps, and the DLL gear table is current |
| Neutral game: walk/run/sprint and dodge motion, each build's reach, who strikes first from outside both reaches | [neutral.md](neutral.md) | `scripts/er-mechanics-neutral.py` | 11/11: hkx root-motion speeds, dodge i-frames and R1 frames against ashes-of-war.md 14a, synthetic races |

## The chain, end to end

```
stats + talismans/armor/great rune --(resources)--> effective stats, HP/FP/stamina, max load
weapon id = base + affinity*100 + level --(attack-rating)--> AR per element (5 stats per element,
    CalcCorrectGraph curves, 0.6 if a requirement is unmet, 2H STR x1.5)
AR x motion value (AtkParam_Pc via BehaviorParam_PC, with family fallback) --(attacks)--> attack
attack vs defender --(defense)--> per element: attack x (1 - curve(attack/defense)),
    x product of 4 armor absorptions x talisman/SpEffect cuts x PvP rates, summed
```

## Where the DS2 model differs

| | DS2 SotFS | Elden Ring |
| --- | --- | --- |
| Damage vs defense | `max(AR*10 - DEF, lower) / 12` physical, subtractive | piecewise curve on attack/defense ratio, 90% .. 10% reduction; equal attack and defense keeps 40% |
| Armor elemental | additive cut `(D + 100) / 1000` | multiplicative absorption per piece |
| Flat defense | stat table, armor adds defense | level curve + one stat curve; armor adds none |
| Scaling | `bonus * coef`, per element | five stats per element, each a curve multiplier, combined; any unmet requirement is a flat 0.6 |
| PvP | none found | per-attack `FinalDamageRateParam`, SpEffect PvP columns (Dragoncrest 0.80 PvE, 0.95 PvP), PvP-only hyperarmor damage reduction |

## Open across all four

Each doc ends with its own "Not established" list. The ones that matter most to an optimizer:

- The attacker side that writes AR x motion value into the hit (defense.md, attacks.md). The
  per-element factors of the damage function are now identified: the bullet damage decay in
  `unk1`, the sweet / sour spot `k` (1.0 in PvP), the flick cut and the hyperarmor toughness cut
  (defense.md sections 0, 2b, 3), and the AR context terms (attack-rating.md section 7). Still open:
  the `vcall(chr, +0xb4)` term of `unk1`, and that the victim's machine owns PvP HP (INFERRED).
- Whether duplicate SpEffects stack (Rakshasa pieces), and where the one-talisman-per-group rule is
  enforced.
- Status build-up: the arcane multiplier's consumer and the reinforce offset pairing are community
  data, not traced.
- Attack timing: cancel windows are decoded (attacks.md section 4) and play speed (TAE 608,
  reach.md section 5) is applied, so tool frames are real time with clip time kept in `*_clip`
  fields. `behaviorDataFactor` is a debug override, 1.0 in the shipped game (attacks.md section 4).
- The attacks doc's 1.16.2 addresses are re-found in 1.17.1 (attacks.md section 9),
  and the other three docs were re-checked.
- About 20 item names added after the Smithbox name list and the 2026-07-13 message extraction
  (Steel, Leontiel's, Silver Grooved sets and others) do not resolve.
