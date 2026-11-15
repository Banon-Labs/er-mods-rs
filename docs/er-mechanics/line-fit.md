# Line fit: Vigor, Endurance, poise, Mind and the damage stats on one number

`scripts/er-mechanics-line-fit.py` (`--selftest`; `--line Intelligence --line Faith --json <out>`).

The question (user, 2026-11-12): "with a mage build, we could go less than 89 poise, get less than
58 vigor, and dump points into mind, but I just never know if that's actually better". The same
fit now serves the melee lines (section 2a): a line's offense is its kit, spells and a weapon's R1,
and every attacker line of `er-mechanics-attack-similarity` is fitted here (attack-similarity.md
section 1 has their table).

**Carried weight changed after sections 1 and 4 were measured.** Those tables carry each line's
archetype corpus median in the hands (12.5 Intelligence, 13.0 Faith). Since 2026-11-12 a line
carries its kit alone (`carried_for`: the Glintstone Staff 3.0, the Finger Seal 1.5, and a melee
line's weapon), never what its archetype's players carry (user: "46 endurance seems a little crazy
given it can't use weapons though"). The report was not re-run with talismans since; with no
talismans, as attack-similarity fits them, the casters go to VIG 60 and END 38 / 39 at the kit's
weight (**MEASURED** 2026-11-12, attack-similarity.md section 1), where they were VIG 52 / 53 and
END 46 with the corpus figure.

Labels: `VERIFIED` = regulation value or traced code, `MEASURED` = computed from the regulation and
the corpus mirror by this module or the ones it reads, `INFERRED` = a modelling choice or an
untraced consumer. Every `INFERRED` piece is in section 5 with what would measure it.

## 1. What the fit chooses (`MEASURED` 2026-11-12, RL 150 invasion, the installed regulation)

| line | VIG | END | MND | main | HP piece | armor poise (menu, with Bull-Goat's) | talismans | tears, consumables | blue / red |
|---|---|---|---|---|---|---|---|---|---|
| Intelligence (Rock Sling, Glintstone Staff +25) | 50 | 48 | 8 | INT 80 | Imp Head (Lion) | 98 (130.7) | Bull-Goat's, Radagon Icon, Graven-Mass, Graven-School | Opaline Hardtear, Intelligence-knot Crystal Tear; Boiled Crab | 0 / 14 |
| Faith (Lightning Spear tap, Finger Seal +25) | 50 | 49 | 8 | FTH 80 | Imp Head (Lion) | 98 (130.7) | Bull-Goat's, Flock's Canvas, Radagon Icon, Faithful's Canvas | Lightning-Shrouding Cracked Tear, Opaline Hardtear; Boiled Crab | 0 / 14 |

Both are Heavy Knight (STR 15, DEX 11, MND 8 at base), the armor Imp Head (Lion) / Verdigris Armor /
Steel Gauntlets / Verdigris or Bull-Goat Greaves at 0.699 of max load. Against the user's sketch:

- **Under 89 poise: no.** Every fit, every comparison point allowed to choose and every sensitivity
  row keeps the armor at 88-100 poise and takes Bull-Goat's first (+11% and +12% D). The pool's
  attacks land 1.61 hits each on 89 poise and 1.45 on 130 (carried chip), 1.93 on 51; the gain is
  the follow-up hits a break holds the defender for, not the first hit (92.8% of the pool's first
  hits break 89 fresh in the interrupt module's units, section 5).
- **Mind: not at one cast per engagement.** Starlight Shards and Lulling Branch refill 22.5 FP per
  5 s engagement and one 18-FP cast a contested engagement spends 15-17, so every cast is paid at
  the class base and no Cerulean is drunk. Mind starts paying at two casts per engagement: 31 MND and
  one Cerulean (Intelligence), 20 MND and one (Faith); at three or four, 32-41 MND and two to four
  Ceruleans. That is the "non-melee mage" the user named, and the rest put almost nothing in it.
- **Vigor under 58: yes under this module's Crimson pricing, no under the pouch's.** With every
  drink landing (the default), 14 Crimsons are 11,340 HP against 1,750 of bar, so a Vigor point is
  worth less than the Endurance that carries heavier armor: the fit stops at 50 (52 with Imp Head).
  Priced as the pouch prices Crimson (810 spread over 83 engagements) Vigor goes back to 58 with Imp
  Head, END 41-44, poise 88-93. Which is right turns on how often a build dies with flasks left
  (section 5), and that is not measured.

The sketch as stated, VIG 45 with poise under 89 and Mind fitted, is 3.3% (Intelligence) and 3.4%
(Faith) under the fit; with MND 38 it is 9.3% and 12.4% under. Imp Head (Lion) is the best HP piece
but by 0.6% (Intelligence) and 0.4% (Faith) over the best allocation without it.

## 2. The number

D is the damage the build deals before it dies, the reading `er-mechanics-pouch` gives its score
("How offense, defense and time make one number"), with every term the stat block moves written
out instead of taken as a ratio to a fixed build:

    D = win x casts x hit x u x paid x N

| term | what it is | source |
|---|---|---|
| `hit` | one connect of the line's spell on the RL 140-160 window's median defender, at the main stat the allocation leaves, its talismans' rates applied | `er-mechanics-spells.SpellModel.evaluate` `one_hit` (`MEASURED`) |
| `buff` (reported) | the catalyst's spell buff at that stat: Glintstone Staff +25 291.2 at INT 60, 329.5 at INT 80 | `SpellModel.multiplier` x 100 (`MEASURED`, matches er-builds-optimize) |
| `u` | 1 - `contested` x the pool share that breaks the cast in its windup at the build's poise | `er-mechanics-interrupt.reverse` on the cast as a sequence (below) |
| `paid` | the share of the uninterrupted casts the FP plan pays | `fp_paid` (below) |
| `N` | engagements lived: (max HP + Crimson drunk x min(810, max HP - 0.5 x L)) / L, at most the fight's 83.2 | `score` |
| `L` | HP lost per engagement: (1 - win) x attackers x mean pool hit x hits landed per pool attack | below |
| `win`, attackers, fight | 0.5; 2.0 opponents at once; 83.2 engagements a fight | `er-mechanics-pouch.Pouch` invasion (`FIGHT_WIN_SHARE`, `FIGHT_KINDS`, `crimson_hits / win`) |

**The cast as an interrupt sequence.** `INT.reverse(rows, seq, carried)` takes any sequence with a
strike frame and TAE 795 windows, so the interrupt module reads a cast as it reads a weapon opener:
strike = `evaluate`'s `startup` (the press to the first damaging cast event at this casting value),
windows = the TAE 795 events of the start clip up to the hand-off and of the release clip after it,
through `INT._window` with the catalyst's `toughnessCorrectRate`. 20 of the 135 spell TimeActs carry
TAE 795 windows (`MEASURED` scan: Ancient Dragons' Light Spear, Dragonclaw, Rock Blaster, Bayle's
Tyranny and 16 more); Rock Sling (a508) and Lightning Spear (a436) carry none, so for them poise
changes the cast's survival only through the hits that would not break it.

## 2a. A kit: spells and a weapon's R1

`Line.kit` lists the line's attacks, each at its share of them: `Spell(main, catalyst, spell)` and
`Weapon(name, affinity, level, two)`. Empty, it is the line's one spell. D is the same number with
the kit's share-weighted offense: the spells' part as above, paid by the FP plan, and the weapons'
part, which costs no FP and is always paid (`score`'s `free`). Each attack carries its own windup
interrupt (`u`): a spell its cast sequence, a weapon its R1 as `er-mechanics-interrupt.resolve`
gives it (the members cache the pool is read with, else extracted as that cache is, about 1 s a
weapon, **MEASURED**).

- **A weapon's hit** (`GameWorld.weapon_hit`): `er-mechanics-ar.attack_rating` at the five damage
  stats (its 40% requirement penalty included), times the R1's first-judge motion value per element
  (`ATK.attack_numbers` on the affinity row, two-handed with the 200 judge offset) and the weapon's
  vsPlayer rate per element, through `DEF.damage` with the hit's physical type and
  FinalDamageRateParam row: `er-mechanics-ashes.pvp_damage`'s rule, on the median defender the
  spells are read on.
- **`mains`** (a melee line's main stats, or Hybrid's INT and FTH): raised evenly to the cap, then
  STR and DEX, by `SIM.allocate_line`'s rule (`fill_steps`, checked against it point by point), on
  the starting class whose kit hits hardest at the allocation, so a weapon whose requirement one
  class's bases miss is not credited to it. A caster keeps the allocation below.
- **The weapon** (`pick_weapon`, `fit_line`): per candidate weapon (every one a player holds whose
  class has a melee R1, at each affinity and grip it takes, whose requirements the line's stats
  meet; with an archetype given, whose affinity row's scaling is that archetype by
  `er-mechanics-build-tags.SCALING_RULES`), END the least that keeps the set, the kit with that
  weapon and the talismans at medium roll, the stats that leaves, and its R1 hit there. The
  `SCREEN_TOP` (5) hardest are priced with their windup interrupt, and the best hit x uninterrupted
  share is the line's. `fit_line` picks it at the line's shape (VIG at the knee, the lightest
  floor-poise set), fits, picks again at the fit's VIG, MND, HP piece and set, and refits while it
  changes (two fits at most). **INFERRED** as a rule: D reads one attack an engagement, so the pick
  prices the weapon's weight and windup but not its speed or recovery, and lands on heavy hitters.
- **What is carried** (`carried_for`): each catalyst of the kit once and its weapon. No swap.

**FP** (`fp_paid`): one bar at the build's Mind, then the FP items between engagements, then
Ceruleans of min(220, bar). At most one bar is spent in one engagement (the bar refills only between
engagements and never past itself), and an item refill never exceeds what the engagement spent. The
items are Starlight Shards (2 FP/s for 60 s, 10 uses) and Lulling Branch (2.5 FP/s for 60 s, 10
uses), read from the pouch's candidates (`Pouch.fp_row`, `_limit`): 22.5 FP per 5 s engagement while
both run. FP is spent by uninterrupted casts only. The Cerulean count is chosen inside every
allocation (0..14), the rest of the flasks Crimson.

**Hits landed per pool attack** (`chain_hits`), over the pool's attack sequences
(`INT.PoolRows.seqs`, the same rows `AT.poise_curve` reads): the first hit breaks as
`INT.chip_states` has it (carried chip by default); a first hit that does not break is all that
lands; a break holds the defender for the string's next hit, and a hit that lands on refilled poise
and breaks it again holds him for the one after. Each landed hit counts the pool's mean hit.

**Defense.** The mean pool hit is `AT.pool_hit`'s sum, re-done per allocation: the pool's attacks
after the build's flat defense (`DEF.defender`'s level and stat graphs, so INT moves magic defense
and VIG fire defense), times the set's absorption, times the SpEffect cuts of the armor's resident
rows and the talismans (`DEF.defender` `effect_mult`). The set is the `ArmorModel` frontier's at
every poise floor the budget reaches (MEDIUM_ROLL_MAX x max load at that END, less the carried weight
and the talismans), no floor imposed.

## 3. What is searched

- The starting class: for each VIG, END, MND the one that leaves the most points in the main stat
  (STR and DEX at the catalyst's requirement or the class base, the rest at base), as
  `SIM.attacker_builds` picks it.
- VIG 20..80, END from the lowest class base to 60, MND from the lowest class base to 60; the main
  stat takes the rest up to 80 (`SIM.DAMAGE_CAP`) and anything past it goes to STR and DEX
  (`SIM.allocate_line`'s spread).
- The HP piece, looped as `er-weapon-build.joint_for` loops them: none, or one of
  `ArmorModel.hp_pieces` (Imp Head (Lion), Crimson Hood, Twinsage / Olivinus / Lazuli Glintstone
  Crown, Greathood, Fire Knight Helm), worn in the head slot with the frontier choosing the other
  three. A piece's stat adds go into HP, defense and the main stat alike, so a crown's INT counts
  as offense.
- The armor set at every poise the frontier steps to.
- Talismans: four slots, greedy on D at the fitted VIG, END and MND (class, HP piece, set and
  Ceruleans re-chosen for every candidate), one per `accessoryGroup`, then the stats again with the
  set, until the set repeats. The candidates are every talisman whose rows move a term D reads (59
  on the installed regulation): HP, FP, equip load, a stat, a damage cut, poise, the spell's rates, its casting speed,
  its FP cost. A damage talisman and an EHP talisman compete for the same slots on the same D.
- Physick tears (two slots) and consumables (no slot limit, each family's one live member through
  `Buffs.stack`): the ones `AT.buff_table` lists as cutting PvP damage or raising poise, against
  the tears whose rows raise the spell (`CastModel._applies`, the reading it gives a talisman), its
  main stat (Intelligence-knot, Faith-knot) or cut its FP cost (Cerulean Hidden Tear). Each by its
  uptime over the build's life, then the stats once more with them.

## 4. The comparison points

Every row holds the fit's talismans, tears and consumables, so the rows differ in stats, armor and
HP piece only; the two Imp Head rows are fits of their own (each chose its own talismans, the same
four). D is HP dealt over the build's life (its absolute scale rests on `win`, `casts` and the
defender); D / fit is the reading.

**Intelligence** (Rock Sling, 18 FP, startup f26, f21.5 with Radagon Icon; carried 12.5)

| point | VIG | END | MND | INT | STR/DEX | class | HP piece | poise armor (menu) | HP | mean hit | hits / pool attack | first-hit break | buff | spell hit | paid | uninterrupted | FP bar | blue / red | life (eng.) | D | D / fit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fit | 50 | 48 | 8 | 80 | 15/11 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 274 | 1.453 | 90.3% | 329.5 | 188 | 100% | 93.1% | 72 | 0/14 | 32.9 | 2875 | 1.000 |
| current shape: VIG 60, lightest 89-poise set, MND base | 60 | 40 | 10 | 79 | 10/10 | Wretch | none | 89 (118.7) | 1900 | 281 | 1.474 | 91.2% | 327.6 | 187 | 100% | 92.9% | 78 | 0/14 | 32.0 | 2782 | 0.968 |
| VIG 60, poise >= 89, MND fitted | 60 | 44 | 10 | 75 | 10/10 | Wretch | Imp Head (Lion) | 93 (124) | 1912 | 277 | 1.462 | 90.3% | 319.9 | 183 | 100% | 93.1% | 78 | 0/14 | 32.8 | 2798 | 0.973 |
| sketch: VIG 45, poise < 89, MND fitted | 45 | 43 | 8 | 80 | 18/18 | Heavy Knight | Twinsage Glintstone Crown | 87 (116) | 1438 | 280 | 1.474 | 91.2% | 337.6 | 193 | 100% | 92.9% | 72 | 0/14 | 30.9 | 2779 | 0.967 |
| sketch: VIG 45, poise < 89, MND 38 | 45 | 40 | 38 | 70 | 8/12 | Astrologer | Imp Head (Lion) | 88 (117.3) | 1631 | 280 | 1.474 | 91.2% | 310.4 | 178 | 100% | 92.9% | 221 | 0/14 | 31.4 | 2607 | 0.907 |
| split: INT 60, VIG 60, lightest 89-poise set, MND base | 60 | 40 | 8 | 60 | 22/22 | Heavy Knight | none | 89 (118.7) | 1900 | 280 | 1.474 | 91.2% | 291.2 | 166 | 100% | 92.9% | 72 | 0/14 | 32.2 | 2474 | 0.860 |
| split: INT 60, rest fitted | 65 | 53 | 8 | 60 | 15/11 | Heavy Knight | none | 99 (132) | 1930 | 273 | 1.452 | 90.3% | 291.2 | 166 | 100% | 93.1% | 72 | 0/14 | 33.6 | 2588 | 0.900 |
| best without Imp Head (Lion) | 47 | 51 | 8 | 80 | 15/11 | Heavy Knight | none | 100 (133.3) | 1631 | 274 | 1.448 | 89.9% | 329.5 | 188 | 100% | 93.2% | 72 | 0/14 | 32.7 | 2857 | 0.994 |
| best with Imp Head (Lion) | 50 | 48 | 8 | 80 | 15/11 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 274 | 1.453 | 90.3% | 329.5 | 188 | 100% | 93.1% | 72 | 0/14 | 32.9 | 2875 | 1.000 |

**Faith** (Lightning Spear tap, 18 FP, startup f30, f27.5 with Radagon Icon; carried 13.0)

| point | VIG | END | MND | FTH | STR/DEX | class | HP piece | poise armor (menu) | HP | mean hit | hits / pool attack | first-hit break | buff | spell hit | paid | uninterrupted | FP bar | blue / red | life (eng.) | D | D / fit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fit | 50 | 49 | 8 | 80 | 15/11 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 277 | 1.453 | 90.3% | 324.1 | 624 | 100% | 82.7% | 72 | 0/14 | 32.5 | 8406 | 1.000 |
| current shape: VIG 60, lightest 89-poise set, MND base | 60 | 40 | 8 | 79 | 15/11 | Heavy Knight | none | 89 (118.7) | 1900 | 283 | 1.474 | 91.2% | 322.2 | 620 | 100% | 82.4% | 72 | 0/14 | 31.8 | 8123 | 0.966 |
| VIG 60, poise >= 89, MND fitted | 60 | 41 | 8 | 78 | 15/11 | Heavy Knight | Imp Head (Lion) | 89 (118.7) | 1912 | 281 | 1.474 | 91.2% | 320.4 | 616 | 100% | 82.4% | 72 | 0/14 | 32.0 | 8137 | 0.968 |
| sketch: VIG 45, poise < 89, MND fitted | 45 | 47 | 10 | 80 | 14/13 | Wretch | none | 88 (117.3) | 1581 | 278 | 1.474 | 91.2% | 324.1 | 624 | 100% | 82.4% | 78 | 0/14 | 31.5 | 8116 | 0.966 |
| sketch: VIG 45, poise < 89, MND 38 | 45 | 40 | 38 | 68 | 11/10 | Prophet | Imp Head (Lion) | 87 (116) | 1631 | 283 | 1.474 | 91.2% | 301.7 | 574 | 100% | 82.4% | 221 | 0/14 | 31.1 | 7366 | 0.876 |
| split: FTH 60, VIG 60, lightest 89-poise set, MND base | 60 | 40 | 8 | 60 | 23/22 | Heavy Knight | none | 89 (118.7) | 1900 | 282 | 1.474 | 91.2% | 286.8 | 541 | 100% | 82.4% | 72 | 0/14 | 31.9 | 7106 | 0.845 |
| split: FTH 60, rest fitted | 66 | 53 | 8 | 60 | 15/11 | Heavy Knight | none | 99 (132) | 1936 | 275 | 1.452 | 90.3% | 286.8 | 541 | 100% | 82.8% | 72 | 0/14 | 33.2 | 7440 | 0.885 |
| best without Imp Head (Lion) | 48 | 51 | 10 | 80 | 10/10 | Wretch | none | 98 (130.7) | 1656 | 276 | 1.453 | 90.3% | 324.1 | 624 | 100% | 82.7% | 78 | 0/14 | 32.4 | 8375 | 0.996 |
| best with Imp Head (Lion) | 50 | 49 | 8 | 80 | 15/11 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 277 | 1.453 | 90.3% | 324.1 | 624 | 100% | 82.7% | 72 | 0/14 | 32.5 | 8406 | 1.000 |

The split rows price the spell only: what the 40-odd points in STR and DEX buy with a melee weapon
is not in D, so they read as the spell cost of the split.

**Sensitivity** (each row re-fits the stats with the same gear; the last column is that refit's D
over the headline allocation scored the same way, so 1.000 means the headline allocation is
already the best there)

| line | sensitivity | VIG | END | MND | main | class | HP piece | poise armor (menu) | HP | FP bar | blue / red | life (eng.) | refit / headline |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| INT | Crimson priced as the pouch prices it | 58 | 44 | 10 | 77 | Wretch | Imp Head (Lion) | 93 (124) | 1900 | 78 | 0/14 | 6.3 | 1.050 |
| INT | fresh poise (no carried chip) | 50 | 48 | 8 | 80 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 72 | 0/14 | 34.0 | 1.000 |
| INT | half the casts contested | 50 | 48 | 8 | 80 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 72 | 0/14 | 32.9 | 1.000 |
| INT | 2 casts per engagement | 38 | 44 | 31 | 80 | Astrologer | Imp Head (Lion) | 93 (124) | 1450 | 179 | 1/13 | 29.6 | 1.142 |
| INT | 3 casts per engagement | 38 | 44 | 32 | 79 | Astrologer | Imp Head (Lion) | 93 (124) | 1450 | 184 | 3/11 | 25.6 | 1.352 |
| INT | 4 casts per engagement | 38 | 41 | 37 | 77 | Astrologer | Imp Head (Lion) | 89 (118.7) | 1450 | 214 | 4/10 | 23.3 | 1.536 |
| FTH | Crimson priced as the pouch prices it | 58 | 41 | 8 | 80 | Heavy Knight | Imp Head (Lion) | 88 (117.3) | 1900 | 72 | 0/14 | 6.1 | 1.045 |
| FTH | fresh poise (no carried chip) | 50 | 49 | 8 | 80 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 72 | 0/14 | 33.7 | 1.000 |
| FTH | half the casts contested | 50 | 49 | 8 | 80 | Heavy Knight | Imp Head (Lion) | 98 (130.7) | 1750 | 72 | 0/14 | 32.5 | 1.000 |
| FTH | 2 casts per engagement | 42 | 49 | 20 | 80 | Prophet | Imp Head (Lion) | 98 (130.7) | 1555 | 121 | 1/13 | 30.0 | 1.069 |
| FTH | 3 casts per engagement | 30 | 45 | 36 | 80 | Prophet | Imp Head (Lion) | 93 (124) | 1081 | 207 | 2/12 | 26.4 | 1.287 |
| FTH | 4 casts per engagement | 29 | 41 | 41 | 80 | Prophet | Imp Head (Lion) | 88 (117.3) | 1037 | 242 | 3/11 | 23.9 | 1.448 |

The pool's side at each poise (`MEASURED`, RL 140-160 pool, 2,743 attack sequences of 890 builds):

| armor poise (menu) | 0 | 25 | 51 | 70 | 89 | 110 | 130 | 160 |
|---|---|---|---|---|---|---|---|---|
| hits landed per pool attack, carried chip | 2.018 | 2.003 | 1.931 | 1.696 | 1.610 | 1.572 | 1.453 | 1.400 |
| first-hit break share, carried chip | 100% | 99.8% | 98.7% | 97.2% | 95.9% | 94.3% | 90.3% | 87.5% |
| hits landed per pool attack, fresh | 2.018 | 2.001 | 1.905 | 1.672 | 1.585 | 1.543 | 1.403 | 1.340 |
| first-hit break share, fresh (= `AT.poise_curve` `first`) | 100% | 99.6% | 97.6% | 94.7% | 92.8% | 90.3% | 82.8% | 77.6% |
| Rock Sling broken in its windup (f26, no talisman) | 17.5% | 17.5% | 17.2% | 16.8% | 16.5% | 16.1% | 14.9% | 14.1% |
| Lightning Spear tap broken in its windup (f30) | 24.4% | 24.4% | 24.1% | 23.6% | 23.3% | 22.7% | 21.4% | 20.4% |

## 5. What is `INFERRED`, and what would measure it

| piece | reading here | what would measure it |
|---|---|---|
| casts per engagement | 1, the pouch's one attack per engagement; the sensitivity rows run 2, 3, 4 | casts per 5 s of engaged time in recorded mage invasions |
| engagement length | 5 s (`ENGAGEMENT_SECONDS`, `INFERRED` in er-mechanics-buffs): sets the items' 22.5 FP per engagement | engagement spacing from a match log |
| FP items | both tick at once, uses back to back, the whole fight | whether 501290 and 20500910 stack (a runtime FP read with both up) |
| a Cerulean | lands on an empty bar: min(220, bar) each; the closed form is an upper bound by at most one engagement's want per drink (`fp_paid_steps` in the selftest) | the bar at each drink in a log |
| FP on an interrupted cast | not spent (FP leaves at the cast event) | the FP deduction traced, or an FP read across an interrupted cast |
| `contested` | 1: every cast meets one pool attack started in its windup; the 0.5 row halves it | share of casts contested in match logs |
| stagger chain | a non-breaking first hit lets the defender act; a break holds him for the next hit; every hit is the mean hit | the stagger lengths against the string gaps per pool weapon (`er-mechanics-ash-interrupt`'s stagger read covers them) |
| chip | carried (the interrupt module's steady-state cycle); the fresh row shows the other end | hits taken within the 30 s reset in logs |
| poise damage units | the interrupt module's (`poise_damage x 10 x saRate`); at 89 armor poise 92.8% of the pool's first hits break a fresh defender | interrupt.md section 1 and frame-advantage.md section 9 list the open unit question |
| Crimson | every drink lands (`FLASK_ETA` 1), drunk at half an engagement's drain, at most max HP | deaths before the flasks run out: the disengage race (`heal_outcome`) over a fight's escape mix |
| Crimson pricing | pool (above) against the pouch's spread of 810 over 83 engagements: the sensitivity row | the same measurement: whether a build dies with flasks left |
| the spell | Rock Sling (INT), Lightning Spear tap (FTH): 18 FP each | the line's real spell mix |
| the damage mix | the frontier's shares read at one stat block (VIG 60, INT 80); flat defense per allocation | none needed for the ranking; checked against `ArmorModel.hit` within 0.4% (section 6) |
| casting speed | Dexterity 10 in the spell's frames | the line's own DEX (10-14 here) moves `startup` by a frame or less |
| carried weight | the kit's own (catalysts, the weapon); no swap. Sections 1 and 4 were measured with the archetype's corpus median (12.5 INT, 13.0 FTH) | the line's real kit with its swaps |
| the weapon | the screen's best hit x uninterrupted share among the 5 hardest R1s, one attack an engagement (section 2a) | attack speed and recovery priced: a weapon's attacks per engagement |
| a split line's mix | half its attacks the spell, half the weapon's R1 (attack-similarity `SPLIT_MELEE_SHARE`) | the mix in split-build match logs |
| tear and consumable uptime | uses back to back from the start, over the build's life; `AT.buff_table`'s mix-weighted cut | use timing in logs |

## 6. Held fixed or left out

- Great runes: inert for the invader (er-weapon-build, buffs.md section 4). The fight is the
  invasion.
- Talismans whose spell rows need a state no term has a share for: Spear Talisman (counter hit,
  stateInfo 197), Ritual Sword Talisman and Red-Feathered Branchsword (HP conditions).
  `SpellModel.rates` applies them as if always met.
- Tears that drain their user's HP (Bloodsucking Cracked Tear: 20511050 cycles 20511051, 0.2% max HP
  + 20 a tick): the drain is not priced.
- One-hit rows (Opaline Bubbletear, Uplifting Aromatic), body transforms (Rock Heart, Priestess
  Heart), rows under `REFRESHED_ROW_S` (Ironjar Aromatic, Ancient Dragon's Blessing).
- Armor whose resident rows raise a spell: the frontier prices absorption and the HP pieces HP; a
  piece whose only effect is on the spell is not offered.
- A talisman row reached through a cycle with no `motionInterval`: not followed for HP, FP or load
  (`RES.resources` reads the talisman's own rows).
- `er-mechanics-gear-synergy`: its channels read a weapon's melee moveset, so it does not apply to a
  spell; the scorpion charms' element channel is priced here directly through the spell's rates and
  `DEF.defender`.

## 7. Checks

`--selftest` (synthetic numbers where the answer is known, then the flat-defense helper on the
installed regulation):

- `fp_paid`'s closed form against the engagement-by-engagement plan (`fp_paid_steps`): equal with
  no drink, within one engagement's want per drink otherwise.
- `chain_hits` on two hand-made strings: under every hit the whole string lands; past them one
  hit lands; the carried chip breaks a poise no single first hit reaches, in part.
- `score`: life is (HP + Crimson x heal) / drain, a Cerulean takes a Crimson's place, a Crimson
  heals no more than max HP less the drain it is drunk at.
- `search` on a synthetic world: free poise (no stagger, no interrupt) drives poise to 0; a stagger
  share that falls with poise buys poise; FP items that pay every cast leave Mind at the class base
  with no Cerulean; a want past the bar each engagement buys Mind.
- A kit: an FP-free attack deals win x hit per engagement lived, always paid, and adds to a spell
  beside it (`score`'s `free`).
- `fill_steps` gives `SIM.allocate_line`'s damage stats point by point on two class bases and four
  `mains` (STR; STR and DEX; ARC; INT and FTH).
- A synthetic melee kit (an FP-free R1 on STR): Mind stays at the base with no Cerulean, and the
  poise a stagger share buys is bought.
- The flat defense per element is `DEF.defender`'s on two stat blocks.

Every report run also prints `consistency`: the mean pool hit equals `ArmorModel.hit` on three
frontier sets (378.654, 354.643, 348.571), and the fresh first-hit break share equals
`AT.poise_curve` at poise 0, 51, 89 and 130.

## 8. Interface

    F = importlib (scripts/er-mechanics-line-fit.py)
    world = F.GameWorld(rl=150)                       # 37.6 s measured: armor model, pool, pouch, spells
    got = F.fit(F.LINES['Intelligence'], world)       # stats, class, wear, gear, D and its terms
    line = dataclasses.replace(F.LINES['Faith'], casts=2.0, vig=60)
    got = F.fit(line, world)
    melee = F.Line('Strength', 'str', '', '', mains=('str',), kit=(F.Weapon('-'),), talismans=(), tears=(),
                   poise_min=world.floor)
    got, fitted = F.fit_line(melee, world, 'Strength')   # the weapon picked off the game, then fitted

`fit` returns `evaluate`'s dict: `stats` (the eight attributes), `class`, `wear`, `D`, `offense`
{buff, hit, spell, fp_cast, startup, paid, uninterrupted, stopped, casts}, `defense` {hp, mean_hit,
armor_poise, menu_poise, hits_per_attack, stagger, life, drain}, `fp` {bar, spend, cerulean,
crimson}, `gear` {pieces, weight, talismans, mods, carried, burden}, plus `history` (the rounds),
`talisman_gains` and `mod_gains`. A `Line` pins any of VIG, END, MND, bounds poise, limits the HP
pieces, fixes the talismans and tears, or changes the spell, catalyst, cap, casts, carried weight,
Crimson pricing, chip scenario and contested share. One stat search took 3.3-5.1 s in the
2026-11-12 probe (no talismans); a whole `fit` with its greedy rounds was not timed on its own.
