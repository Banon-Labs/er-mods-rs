# Attack similarity: spells and weapon skills as punishes

Labels as in [ashes-of-war.md](ashes-of-war.md): **VERIFIED** = read in the code or the installed
params, **REGULATION** = a param value, **MEASURED** = computed by the tool or a probe,
**INFERRED** = consistent with the data, consuming code not traced, **COMMUNITY** = outside claim.
Nothing was launched.

Tool: `scripts/er-mechanics-attack-similarity.py` (`--selftest` passes 146/146, round 5: 312 s
with 3 workers on 4 pinned cores; 166/166 with the objective evaluation, section 6a, 2026-11-10,
about 30 min beside other load at a load average of 11 to 16). Later on 2026-11-10 it stops at
'10. live negative controls that still bite' (2 of 4: Oracular Bubble / Scouring Black Flame's tap
D_rel 0.69, Sorcery of the Crozier / Glintstone Nail charged 0.55, both on the code before the aim
change of section 8 too); run past that check, the other 166 of 167 pass (bd er-effects-rs-8hqt:
re-pick `NEGATIVE_CONTROLS` from a current full run).

Where the numbers come from (**MEASURED**):

- **Round-4 full run**: cache stamp `7821862ab710e5ec` (version 3 of the cache), 1,851 s with 8
  workers beside other load, at the default thresholds, made by the code before round 5 and before
  the same day's later edits to er-mechanics-spells and er-mechanics-ashes (hold spells as one
  cast, the burst chain, per-shot fans). Every number labelled 'round-4 full run', and every
  full-run figure of sections 1, 3, 5 and 7 that carries no other label, is read off it; round 5
  changes what some of them would be (charged skills' early releases and fixed carriers are new
  entities, follow-ups get no component before their press, skills gain weapon status tokens, hold
  spells are one cast). The full runs r4 and r5 below moved most of them (r5 reads 370 skill
  openings, not 327, and counts 542 scored, 29 partial and 182 excluded); they are not yet
  re-read from either.
- **Round-5 selftest**: a 42-name subset scored by this code (round 4's 40 plus Carian Grandeur
  and Bloodhound's Finesse), against er-mechanics-spells and er-mechanics-ashes as they stood at
  20:43 on 2026-11-09 (both were still being edited after it).
- **Round-5 sample**: a 38-name subset scored by this code (section 9), the source of section 6's
  per-build figures unless a round-5 full run is named there.
- **Full run r4** (2026-11-10): cache stamp `8732718cb9514f58` (version 4), all 1,278 attacks and
  the 8 attacker builds, made by the code with the placed launches, the caught defender and the
  health cap (section 6). Section 6 was read off it until r5.
- **Full run r5** (2026-11-10): cache stamp `f3d7eafb8693154b` (version 4), all 1,278 attacks and
  the 8 attacker builds, made by r4's code plus the hit priority records (bd er-effects-rs-dnok,
  section 8), about 30 min from its log's first line to its last (workers and load not recorded).
  Section 6's per-build tables and the paragraph under them are read off it.
- **Full run r6** (2026-11-10): cache stamp `22ad3a4c5998dae6` (version 4), r5's code plus the
  displacement half of the gate (section 1). Dominance is identical to r5 in all 8 builds (every
  mean net and edge count), so section 6 stands. Associations: twins (57) and contains (2) are
  unchanged; of the 2 cross-type matches, the Gravity Well tap / Glintblade Phalanx ash [Dagger]
  match is gone and the Glintblade Phalanx cast / ash [Dagger] match takes its place.
- **Full run r7** (2026-11-10): cache stamp `89e630dd74378aa7` (version 6), r6's code plus the
  objective lines (section 6a), the delayed follow-up press (section 2a), in-flight homing and free
  aim at the floor (section 8). 6,863 s from its log's first line to its last, with 3 workers on 4
  CPUs beside other runs (r5 1,808 s, r6 3,502 s by the same measure; neither recorded its load,
  so the three are not comparable: every run since is timed, Commands). 542 scored, 29 partial,
  182 excluded; 63 twins (r6 57), 2 cross-type matches (Greyoll's Roar cast / Lightspeed Slash
  [Greatsword of Radahn (Light)] L2 through its f37.5 component, and the Glintblade Phalanx cast /
  ash [Dagger]), 2 contains. The selftest's negative controls are re-picked from it
  (`NEGATIVE_CONTROLS`). Section 6's per-build tables are still r5's; the follow-ups moved most
  (Glintblade Phalanx [Ritual Thrusting Shield] R2 nets 426 to 605 per build, Golden Land [Large
  Club] R2 809 to 1,059), so they are not re-read until the dodge model's open question (Glintstone
  Dart and Cometshard net about 0 on every line) is settled.
- **Full run r8** (2026-11-10): cache stamp `77cd5372fec887cd` (version 6), r7's code plus the
  lock-on aim at a shot's creation (section 8). The first timed full run: 9,827 s, 9,699 of them
  the attacks (1,276 items, 12,945 CPU s), beside other selftests (the log records the load). 542
  scored, 29 partial, 182 excluded; 65 twins, 3 cross-type matches (r7's two and the Glintstone
  Nails tap / Lightspeed Slash [Greatsword of Radahn (Light)] through its f59 component), 3
  contains. The aim moved few same-name pairs: the Glintstone Nail nets 130 to 447 (charged, INT)
  and 99 to 311 (tap), so Oracular Bubble no longer dominates it on the Hybrid line; Glintstone
  Cometshard charged went from -1 to -15. Three of the four negative controls picked from r7 no
  longer bite: Roaring Bash / Star Shower, Red Bear Hunt / Glintstone Stars and Glintblade Phalanx
  [Ritual Thrusting Shield] / Star Shower are at D_rel 0.668, 0.660 and 0.673 (Charge Forth /
  Cherishing Fingers stays at 0.042), and of the 10 pairs left within D_rel 0.07 and past D_assoc
  0.15, 8 are Charge Forth. Section 6 is still r5's.
- **Full run r9** (2026-11-11): cache stamp `f4971cc856bcd144` (version 8), r8's code plus the
  record's resolve rule read in full (one launch is one attack to the record, same-frame ties to
  the attack created first) and the on-screen defender (section 2b), with a tap playing 045010 up
  to its release. 12,716 s, the attacks 12,569 of them (1,285 items, 15,123 CPU s); over the 1,264
  items in both runs the attacks went 12,933 -> 15,060 CPU s (x1.16), Theodorix's Magma (364 ->
  763 s) and Aspects of the Crucible: Bloom (tap 448 -> 757, charged 574 -> 854) dearest, Explosive
  Ghostflame (584 -> 314) and Night Maiden's Mist charged (571 -> 325) cheaper. 543 scored, 29
  partial, 181 excluded; 67 twins (r8's 65 plus Shattering Crystal tap / Glintstone Cometshard tap
  and Flame Spear [Short Spear] / [Bastard Sword]), r8's 3 cross-type matches, 4 contains (adds The
  Flame of Frenzy tap's f8 frame in Sacred Ring of Light [Short Spear]). The largest moves at the
  distance basis are the lookalikes': Wrath of Gold charged 247 -> 989, Unendurable Frenzy tap -229
  -> 405, Giantsflame Take Thee charged 0 -> 605, Revenger's Blade [Falx] 0 -> 307, Flame Spear
  [Bastard Sword] -1 -> 340, Thundercloud Form tap 880 -> 671. Same-name dominance (per build,
  section 6): the Glintblade Phalanx ash, either carrier, with or without its R2, robustly
  dominates the Glintblade Phalanx and Carian Phalanx sorceries on the Intelligence and Hybrid
  lines (sorcery nets 68 / 47 and 77 / 39 against the Dagger's 143 / 120); the Glintstone Dart's
  R2 follow-up dominates the Glintstone Cometshard tap on Intelligence (213 against 39); the
  Zamor Curved Sword's skill the Zamor Ice Storm tap there (full_only); Jori's Inquisition's early
  release both Golden Arcs variants on 'none' alone. The negative controls were re-picked from it
  (`NEGATIVE_CONTROLS`): r7's three that r8 broke stayed at D_rel 0.66-0.67.

## 0. In plain words

Every player attack spell and every obtainable weapon skill is thrown, one at a time, into the
same 1,208 punish situations: the opponent has just whiffed an attack at some distance and is
stuck in its recovery for some frames. The models the kit already uses say, per situation, how
much of the attack's worth lands when it is pressed at once, how often the defender who rolled it
gets a punish in, the same when the attacker may run in first, and how it lands on a defender who
does not dodge at all.

Two attacks resemble each other when both views agree: against the defender who dodges, they land
the same share of their worth in the same situations (D_rel), and against the one who does not,
they reach the same distances at about the same frame (D_idle). The association distance, D_assoc,
is the worse of the two. On top of it sits the **delivery gate**: an attack whose worth comes only
from melee hits and one whose worth comes only from bullets never associate, however alike their
numbers, and neither do an attack that moves or holds the target (a pull, a push, a grab) and one
that does not. Every association reads D_assoc behind the gate: the twins, the cross-type matches, the
families and the nearest neighbours. 'Contains' asks whether one frame of an attack lands like the
whole of another; when the two whole attacks are of about the same worth, that frame makes them
twins too (the Zamor Curved Sword's storm and the Zamor Ice Storm tap). Dominance is separate: one
attack nets at least as much as another nearly everywhere at no more FP, so the other is redundant.

Which build each part is read at:

- **Build-independent** (the distance basis, every damage stat 99, so every attack can be used):
  the matrices, twins, cross-type matches, contains, families, neighbours, effect families,
  probes and groups. They compare shares of each attack's own worth, which do not move with the
  damage pressed at once and barely move with a run-in (section 2).
- **Build-dependent** (read per attacker build, section 6): dominance, the redundant lists, the
  frontiers, the own-variant edges, the spell rollup and each build's 'punished' list. These
  compare absolute nets and FP, so they hold only for the build they are read at. No character at
  RL 150 has the distance basis (with 10 VIG, MND and END it is RL 446), so none of them is read on
  it.
- **Objective evaluation** (section 6a): every scored attack is also priced at two lines of its
  own that can use it, read off no attacker line, so an attack none of the nine lines can use still
  has a net.

## 1. The attacks

Shared by every build (all of it is in the cache stamp, all of it overridable): catalysts
Glintstone Staff +25 and Finger Seal +25, each spell cast with the first that can (both
rate-neutral); no talismans, no off-hand, one-handed, PvP rates; the median defender of RL 140-160.

- **The distance basis** (`--stats`, default every damage stat 99): every attack is wieldable and
  castable, so every attack has a profile; the associations read it.
- **The attacker builds** (`attacker_builds`, version 10, 2026-11-12) are lines made from the
  game's curves, not corpus medians. User, 2026-11-12: "111 real Intelligence builds might all be
  bad builds :)", "I told you this information because I think you're overvaluing real builds",
  and earlier "If 33 endurance isn't enough, then it isn't a real build, its just stat
  allocations". The corpus gives usage alone (`corpus_usage`): which stat archetypes have builds
  at the RL window, how many, and the weight they carry in their hands. That is the build-planner
  corpus the opponent pool comes from (`~/.cache/er-build-planner/builds.jsonl` through
  `er_corpus.iter_rows`), its builds not marked PvE whose RL (the eight attributes of the
  document's `stats`, base stats, less 79) is 140-160, grouped by
  `er-mechanics-build-tags.stat_archetype`. 'n' is that count; the 114 builds with no archetype
  ('none') make no line. RL = attribute sum less 79 is checked on every starting class against
  `CharaInitParam.soulLv` (**VERIFIED**).

  One line per archetype present (Strength, Dexterity, Quality, Intelligence, Faith, Arcane,
  Hybrid) and two split lines, 'Intelligence + melee' and 'Faith + melee', for the user's
  question "are [mages] optimal with enough int where they don't have stat points to get melee
  weapons?". At RL 150 every line's eight attributes sum to 229.

  **Every line is `er-mechanics-line-fit`'s** (`fitted_lines`, `line_kit`; its doc is
  `line-fit.md`). VIG, END (with the armor set, its poise and the HP piece it carries at medium
  roll) and MND are traded at fixed RL on one number, D, the damage the line deals before it dies.
  The user asked for the melee lines to trade Vigor too: "In theory, with a mage build, we could go
  less than 89 poise, get less than 58 vigor, and dump points into mind, but I just never know if
  that's actually better", and of the HP pieces, "Its possible that its not the best, because there
  are other ways to raise HP or EHP". Until 2026-11-12 the Strength, Dexterity, Quality, Arcane and
  Hybrid lines were `allocate_line`'s with VIG fixed at the knee: line-fit had one main stat and one
  spell, and `er-weapon-build.joint_for` (damage x EHP over VIG, END and armor) is a closure inside
  one weapon's build run (**VERIFIED**, `scripts/er-weapon-build.py` around line 2118), so neither
  could price a melee line. Line-fit now takes a kit of attacks (`Spell`, `Weapon`), so one
  objective serves every line, and `allocate_line` is only the fallback for a line with no fit.

  - **What D is measured on, the kit** (`line_kit`):
    - **Intelligence, Faith**: the school's spell (Rock Sling, Lightning Spear: line-fit's own,
      **INFERRED** there as a PvP staple) from the first of the run's catalysts that casts it.
    - **Hybrid**: both spells, half each, INT and FTH raised evenly. No weapon whose scaling is
      Hybrid's (`SCALING_RULES`, below) is wieldable at its stats (**MEASURED** 2026-11-12: none of
      130 wieldable at STR 10 DEX 10 INT 45 FTH 45).
    - **Strength, Dexterity, Quality, Arcane**: one R1 hit of a weapon picked off the game at the
      line's own stats (`er-mechanics-line-fit.pick_weapon`), among the weapons whose affinity
      row's scaling archetype is the line's (`er-mechanics-build-tags.SCALING_RULES` on its
      `correct*` fields: the planner's own weapon archetypes). The hit is `er-mechanics-ar.
      attack_rating` at the five damage stats (its requirement penalty included) times the R1's
      motion values and the weapon's vsPlayer rates, through the defense of the median defender the
      spells are read on, with its FinalDamageRateParam row (`er-mechanics-ashes.pvp_damage`'s
      rule). The R1 is the interrupt module's own sequence, so its windup is priced as a cast's is.
    - **The split lines**: the school's spell and a weapon's R1, half each (`SPLIT_MELEE_SHARE`,
      **INFERRED**: no match log says how a split build mixes them), the weapon of any scaling, the
      main stat stopped at 60. D now prices what the STR and DEX points buy, so VIG and END are no
      longer pinned.
    - **The weapon pick** (**INFERRED** as a rule): per candidate, END the least that keeps the
      lightest floor-poise set, the kit with that weapon and nothing else at medium roll, the stats
      that leaves, and its R1 hit there; the five hardest are priced with their R1's windup
      interrupt at the set's poise, and the best hit x uninterrupted share is the line's. Then it
      is picked again at the fit's own VIG, MND, HP piece and set, and the line refitted while it
      changes. D reads one attack an engagement, so the pick prices the weapon's weight (through
      the END it costs) and its windup, not its attack speed or recovery, and it lands on the
      heaviest hitters. The weapon is never what the archetype's players carry most: the corpus is
      not read for it.
  - **What a line carries**: its kit alone, each catalyst once and the weapon its offense is
    measured on (`er-mechanics-line-fit.carried_for`). No swap is carried: a swap kit is not
    modelled. Until 2026-11-12 every line carried its archetype's corpus median, the weight in the
    hands of its corpus builds, swaps included; the user, of the Intelligence line at STR 15 DEX
    11 and END 46: "46 endurance seems a little crazy given it can't use weapons though". That
    median (`carried_weapons`, `corpus_usage`) is kept beside each line as usage
    (`rule.usage`), never carried. The Glintstone Staff weighs 3.0 and the Finger Seal 1.5
    (**REGULATION**, EquipParamWeapon 33000000 and 34000000).
  - **The poise floor**: every line whose kit swings a weapon keeps the user's melee floor, 89 on
    the RL 140-160 pool (`er-mechanics-armor-targets.melee_poise_floor`; user, 2026-10-13: "most
    people would laugh at me if I gave them a build that had less than 89 poise without bullgoat,
    unless it was a mage build"). The casters (Intelligence, Faith, Hybrid) have none. Each floored
    line also reports its best allocation with no floor (`rule.fit.below_floor`), as
    er-weapon-build does.
  - **Two choices kept from before**: no talismans or tears (the similarity run prices every line
    without them, so a fit that chose them would be scored without the gear its stats were chosen
    for, and the greedy talismans run one `search` per candidate per slot, 59 x 4, 3.3-5.1 s a
    search, **MEASURED** by that module's author); line-fit's `INFERRED` pieces (line-fit.md
    section 5) at their defaults: one attack an engagement, Crimson priced as the pool prices it,
    every attack contested, carried chip.
  - **Comparison points on the same kit and D**: `rule.fit.vig_knee`, VIG pinned at the knee of max
    HP and the rest fitted; `rule.fit.knee_shape`, the line as `allocate_line` made it (VIG at the
    knee, END the least that carries the kit and the lightest floor set, MND at the base). The knee
    is `vigor_knee` (CalcCorrectGraph 100 through `er-mechanics-resources`), the first VIG whose
    next point gains under half what it did: max HP gains 48 for 39 -> 40, 26 for 40 -> 41 falling
    to 13 for 59 -> 60, then 6 for 60 -> 61 (**MEASURED**), so 60.
  - **Damage stats and class**: a line's main stats go to 80 (60 on a split line). That is the
    second soft cap, `stageMaxVal3` of CalcCorrectGraph 0 and 1 (STR and DEX) and of 16, the graph
    both catalysts' spell buff reads (**REGULATION**, selftest); 60 is `stageMaxVal2`. Two main
    stats rise evenly, and points past the cap go to STR and DEX evenly (`allocate_line`'s rule,
    which line-fit's `fill_steps` reproduces point by point, selftest). A melee or Hybrid line is
    on the starting class whose kit hits hardest at the allocation (a weapon whose requirement one
    class's bases miss is not credited to it), a caster on the one that leaves the most points in
    its main stat.
  - **MND**: the casters' and split lines' from the fit, which prices their spell's FP. A weapon
    line's R1 costs no FP, so its fit leaves MND at the class base; its MND is then fitted against
    what it pays for on the fit's own FP plan (`fit_mind`, below), VIG and END held at the fit's.

  | line | usage: builds, carried | kit (D measured on) | class | VIG | END | MND (bar) | STR | DEX | INT | FTH | ARC | HP piece; armor poise | carries; burden | D at VIG 60, rest fitted / fit | D at the old knee shape / fit |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | Strength | 327; 18.5 | Giant-Crusher (Heavy, two-handed) R1, 708.6 | Heavy Knight | 47 | 59 | 8 (72) | 80 | 11 | 7 | 8 | 9 | Imp Head (Lion); 98 | 26.5; 0.697 | 0.977 | 0.966 |
  | Dexterity | 182; 11.5 | Zweihander (Lightning, two-handed) R1, 533.3 | Heavy Knight | 48 | 49 | 8 (72) | 20 | 80 | 7 | 8 | 9 | Imp Head (Lion); 98 | 15.5; 0.694 | 0.985 | 0.974 |
  | Faith | 152; 13.0 | Lightning Spear from Finger Seal+25, 499.6 | Heavy Knight | 60 | 39 | 8 (72) | 15 | 11 | 7 | 80 | 9 | none (Greatjar); 99 | 1.5; 0.695 | 1.000 | 0.960 |
  | Intelligence | 111; 12.5 | Rock Sling from Glintstone Staff+25, 160.5 | Heavy Knight | 60 | 38 | 8 (72) | 15 | 11 | 80 | 8 | 9 | none (Divine Beast Head); 98 | 3.0; 0.699 | 1.000 | 0.961 |
  | Arcane | 78; 11.0 | Zweihander (Occult, two-handed) R1, 514.9 | Heavy Knight | 38 | 48 | 8 (72) | 20 | 20 | 7 | 8 | 80 | Imp Head (Lion); 97 | 15.5; 0.693 | 0.970 | 0.962 |
  | Hybrid | 52; 14.5 | Rock Sling 143.0 and Lightning Spear 444.3, half each | Wretch | 31 | 30 | 10 (78) | 10 | 10 | 64 | 64 | 10 | Imp Head (Lion); 90 | 4.5; 0.696 | 0.906 | 0.876 |
  | Quality | 50; 18.0 | Giant-Crusher (Quality, two-handed) R1, 662.4 | Heavy Knight | 37 | 52 | 8 (72) | 54 | 54 | 7 | 8 | 9 | Imp Head (Lion); 90 | 26.5; 0.700 | 0.921 | 0.915 |
  | Intelligence + melee | -; 18.0 | Rock Sling 138.2 and Royal Greatsword (Standard, two-handed) R1 592.3, half each | Heavy Knight | 38 | 55 | 8 (72) | 26 | 25 | 60 | 8 | 9 | Imp Head (Lion); 97 | 23.0; 0.697 | 0.970 | 0.962 |
  | Faith + melee | -; 18.0 | Lightning Spear 430.5 and Shadow Sunflower Blossom (Standard, two-handed) R1 575.3, half each | Heavy Knight | 44 | 50 | 8 (72) | 26 | 25 | 7 | 60 | 9 | Imp Head (Lion); 98 | 17.5; 0.700 | 0.988 | 0.970 |

  (**MEASURED** 2026-11-12, `fitted_lines` on the 2026-11-12 corpus, worktree sim-flight with
  er-mechanics-ashes sha256 `0783e76a...` and er-mechanics-spells `3d7c46f3...`; the selftest's
  Context gave the same nine lines, and the weapon lines' Mind fit keeps MND 8 on all four; check
  25's output is the same, number for number, on the 2026-11-13 flight, ashes `0babd02a...` and
  spells `b491d417...`). The
  page's section 1 and the JSON's `builds[].rule` give the same per
  run, line-fit's breakdown under `rule.fit`.) The hits are one connect on the median defender.
  Every floored line's fit is already at or above 89 poise (90 to 98), so the best below the floor
  is the fit itself on all six. The armor around the HP piece is Verdigris Armor, Steel Gauntlets
  and Verdigris, Bull-Goat or Fire Prelate Greaves (Tree Sentinel Armor on Quality and Hybrid).

  Against the lines before (VIG at the knee on five of them, every line carrying its archetype's
  corpus median):

  - **The casters' freed points went to VIG.** Intelligence carries 3.0, not 12.5, and goes from
    VIG 52 END 46 to VIG 60 END 38; Faith carries 1.5, not 13.0, VIG 53 END 46 to VIG 60 END 39.
    The 8 and 7 END points went to VIG; INT and FTH stay at 80, and both drop the HP piece (at VIG
    60 the Imp Head's Vigor is past the knee) for a heavier helm at the same poise.
  - **The melee lines carry more than their median**, because the pick lands on heavy hitters:
    Strength and Quality carry 26.5 (18.5 and 18.0 before), Dexterity and Arcane 15.5 (11.5,
    11.0). Their END rose (Strength 43 -> 59, Dexterity 36 -> 49, Quality 43 -> 52, Arcane 35 ->
    48) and the fit trades VIG under the knee for it (47, 48, 37, 38) and an Imp Head at 97-98
    poise. VIG at 60 with the rest fitted costs them 1.5 to 8% of D.
  - **The split lines, unpinned**, carry their own weapon and catalyst (23.0 and 17.5, Quality's
    18.0 before), and the fit puts the points into END and STR/DEX over VIG: VIG 60 -> 38 and 44,
    END 43 -> 55 and 50, STR/DEX 21/20 and 18/18 -> 26/25.
  - **Hybrid** goes furthest: VIG 31, END 30, INT and FTH 64 each (45 before), 9.4% over VIG 60.

  Why VIG falls under the knee on every line with heavy carry: D prices the Crimson as the pool
  does, every drink landing (line-fit.md section 1, 'Vigor under 58'), so 14 flasks of 810 HP
  against a bar of 1,400-1,700 make a Vigor point worth less than the END that carries heavier
  armor. That pricing is line-fit's open `INFERRED` piece (section 5 there, 'Crimson'); under the
  pouch's pricing its casters went back to VIG 58. The lines come first by usage, commonest
  archetype first, the split lines last, which breaks the objective fit's ties
  (`objective_lines`). Building them costs the Context about 150 s: line-fit's world 39.9 s and
  the nine fits with their weapon picks and comparison points 109.1 s (**MEASURED**, one build,
  r16 running beside it).

- **Mind and the FP plan** (user, 2026-11-12: "it sounds like we put no value on mind then?").
  There is one FP plan, line-fit's (`fp_plan`, `fp_plan_paid`, which call its `score` and
  `fp_paid`), and every line's nets are paid on it (`BuildView`, `paid_share`). Each attack's
  scored nets are multiplied by the share of its casts the plan pays; the unpaid casts net 0,
  because the run scores no FP-free answer of the attacker's, such as an R1, to fall back to. The
  plan is line-fit's invasion fight (83.2 engagements, 2.0 opponents at once, `win` 0.5). The line
  throws the attack once an engagement (line-fit's `casts` 1, **INFERRED** there) and lives as
  many engagements as its HP and the Crimson it keeps let it. The FP is paid, in this order, by:
  - one bar at its MND;
  - Starlight Shards (2 FP/s for 60 s, 10 uses) and the Lulling Branch (2.5 FP/s for 60 s, 10
    uses), 22.5 FP between engagements while both run;
  - then blue flasks of min(220, bar), at most one bar spent in an engagement.

  The number of blue flasks is the one that deals most before death, each a Crimson lost. Every
  line's plan is its fit's HP, defense, poise and bar (a line with no fit, the `allocate_line`
  fallback, reads line-fit's pieces on the line and the 89 set: `GameWorld.incoming` for the
  pool's mean hit after the set and the stats' flat defense, `hits_at` for the hits each pool
  attack lands at its poise). One difference from the fit: here every cast spends its FP
  (**INFERRED**), where line-fit's fit spends FP only on the casts not broken in their windup.
  The similarity nets price whether an attack lands.

  **The casters and split lines** take their MND from the fit, whose D prices their spell's FP: 8
  (bar 72) on the Heavy Knight lines, 10 (78) on Hybrid, the Wretch's base (**MEASURED**
  2026-11-12, section 1's table). Each keeps all 14 flasks red. The breakdown, D and its terms, is
  in `rule.fit`.

  **The weapon lines** (Strength, Dexterity, Quality, Arcane): their fit's R1 costs no FP, so the
  fit leaves MND at the class base, and MND is then fitted by trade at fixed RL (`fit_mind`) on the
  fit's own FP plan, VIG and END held at the fit's (`rule.mind`). On the fitted lines
  (**MEASURED** 2026-11-12, selftest check 25) every one keeps the base, 8 (bar 72): Strength's 154
  FP attacks are paid 0.9992 at the base, Dexterity's 213 0.9992, Arcane's 216 0.9980, Quality's
  232 0.9979, the dearest (30, 30, 30, 32 FP) paid 1.000, 1.000, 0.936, 1.000. The table below is
  from the `allocate_line` lines before the fit (VIG 60, the corpus median carried). Each MND from the
  class's base up costs a point of the stat the rule raised last: STR or DEX past the cap, else
  the main stat. The line takes the MND whose value is highest, searching while some attack is
  unpaid. The value is the mean, over the FP-costing attacks the line can use (`line_attacks`, of
  the full candidates whatever `--only` keeps), of paid share x damage. It is **INFERRED** in
  three ways:
  - every attack weighs alike, since no net exists before the run to say which ones the line
    throws most;
  - a spell's damage is its catalyst's multiplier at the line (`SpellModel.multiplier`), and a
    skill's is its carrier's `correct*` fields over CalcCorrectGraph 0 (16 for INT and FTH) at a
    correct rate of 100;
  - HP, defense and poise are held at the MND-at-base line's.

  On the 2026-11-12 corpus (**MEASURED**, this code) every one of them keeps its class's base:

  | line | MND (bar) | FP attacks | paid share at the base, mean | dearest attack: FP, blue / red flasks, engagements lived, paid share | MND took |
  | --- | --- | --- | --- | --- | --- |
  | Strength | 8 (72) | 187 | 0.9993 | 30: 2 / 12, 21.0, 1.000 | nothing |
  | Dexterity | 8 (72) | 213 | 0.9980 | 30: 1 / 13, 21.7, 0.936 | nothing |
  | Arcane | 8 (72) | 177 | 0.9980 | 30: 1 / 13, 21.8, 0.936 | nothing |
  | Hybrid | 10 (78) | 286 | 0.9948 | 65: 7 / 7, 13.3, 1.000 | nothing |
  | Quality | 8 (72) | 230 | 0.9991 | 32: 2 / 12, 20.7, 0.996 | nothing |

  The paid share of each line's top attack, the population member of highest paid mean net,
  needs a full run: the page's section 1 table and the JSON's `builds[].top_paid` give it.

  What drives it. The two FP items refill 22.5 FP between engagements, so an attack of 22 FP or
  less an engagement is paid by the items alone and the bar only has to hold one cast. A dearer
  one (the skills of 30-32 FP, Hybrid's 65 FP incantations) drains what the items do not refill.
  The plan then drinks blue flasks, and gives up red ones and with them engagements lived, rather
  than buy MND. On a 72 bar a blue flask restores 72 FP. A point of MND adds 3 to 7 FP to the bar
  and to each blue flask (max FP 72 at MND 8, 121 at 20, 221 at 38, **MEASURED**), against a point
  of the main stat, 0.4 to 0.6% of damage. Over a
  line's 177 to 286 attacks, most of them paid in full, that never wins.

  The user's account of play is that most players put almost no points into Mind and take no
  more than 1 or 2 blue flasks in invasions, unless they are a non-melee mage, "because starlight
  shards exist and the craftable DLC item to give FP". The model puts nothing into Mind on any
  line, casters included. On the melee lines the dearest attack takes 1 or 2 blue flasks, as the
  user describes. The casters are not raised because the model counts one cast an engagement.
  line-fit.md section 1 shows MND starting to pay at two casts an engagement (31 MND on
  Intelligence, 20 on Faith, with talismans), the "non-melee mage" the user named.
- **What a build can use**: an attack is in a build's dominance only when the build's base stats
  meet the carrier's requirements, or the spell's and its catalyst's (a catalyst whose requirement
  is not met casts nothing, **COMMUNITY**). Whether the game's requirement check counts talisman
  or great-rune stat boosts is **not established**, so base stats are read. A build's skill rides
  the weapon of least total requirement in the opening group that it can wield, at the affinity
  whose skill damage is largest at its stats; its spells cast at its own DEX, which moves every
  hit frame (`casting_value` between `analogDexterityMin` and `Max`). The populations come only
  from the attacker lines, so an attack no line can use is in none of them (full run r5, on the
  corpus-median lines before version 10, **MEASURED**: Tremendous Phalanx, Rancor Slash and Great
  Oracular Bubble had no per-build net, Jori's Inquisition one only in Faith and 'none'); section
  6a prices every scored attack at lines that can use it.

- **Spell variants**: one entity per `SpellModel.evaluate` variant (cast, or tap and charged, and
  a hold spell's 'early' release, section 2c), keyed `spell:<magicId>:<variant>`. A spell is left out when it is not usable in multiplayer, has
  no spell goods row (an `EquipParamGoods` row with its id, a spell goodsType and a real `sortId`:
  this drops 8000, the Stonesword Key, and 8001), has no catalyst of its school, is short of a stat,
  or has no damaging hit. Its events are flown with the spell rule as the node test
  (`SpellModel.spell_node_damaging`: a flat attack field), not the ash model's weapon-scaled rule,
  which some spell AtkParams leave at 0.
- **A variant `evaluate` drops**: `evaluate` skips a variant without a damaging hit and keeps the
  others, so `SpellModel.variants` can offer tap and charged while only one comes back. Each such
  variant is its own exclusion, 'no damaging hit (variant)': 19 in the round-4 full run, among
  them the taps of Crystal Barrage, Gelmir's Fury, Dragonfire and every breath spell, and the
  charged cast of Loretta's Greatbow. `SpellModel.variants` (er-mechanics-spells.py, changed since
  round 4) now gives a hold spell whose tap clip carries no cast event one 'cast' (its docstring
  names 16: the breaths, Crystal Barrage, Gelmir's Fury, Meteorite, Spira, Divine Bird Feathers),
  so those taps are no longer offered and the held cast is the spell's one entity.
- **Hits on bullets the flight never launches** (**VERIFIED** in the code: `evaluate`'s `full`
  rule prices every hit list of a bullet tree, `er-mechanics-ashes.fly_tree` flies only the
  children whose launch condition a straight flight meets). `SpellModel.launch_gaps` names every
  node of a tree that is never flown and why, read off the topmost unflown node on its path. A hit
  list on such a node is left out of the worth even when its event flies:
  - launch condition 5, 'on a hit': made only when the carrier touches a character. Until
    2026-11-13 the straight flight touched no one, so such a child was never made: Roiling Magma's
    list under 10482002 was missing (partial), and Death Lightning, whose bolt does no damage, was
    excluded. The flight of `er-mechanics-ashes.fly_members` (2026-11-13) touches him and makes the
    child there, as the game does on a hit (`launches`, **VERIFIED** FUN_14039da40, bd
    er-effects-rs-rggx), and follows a caster-attached root, so Watchful Spirit's interval child
    spawns too. On that flight (**MEASURED** 2026-11-13, er-mechanics-ashes sha256 `0babd02a...`,
    er-mechanics-spells `b491d417...`) Roiling Magma is scored with its child's list in the worth
    (one connect 731.1 tap, 923.0 charged, one hit each), Death Lightning scored (1,246 tap, 1,370
    charged at the distance basis), Watchful Spirit scored (743). The condition is still named where
    the carrier never reaches him: Lightning Spear (charged), Wrath from Afar (both), Loretta's
    Greatbow (both). No spell keeps a hit list under a flown event that the flight never makes
    (`unflown_hits`' missing branch is empty on that flight).
  - launch condition 1 or 2 (water) or 254 / 255 (never; `NEVER_ON_LAND`): such a hit never
    happens on dry ground. It is dropped from the worth and the status is left alone (`dropped`,
    `dropped_hp`). Since `bullet_lists` skips a child neither ending makes on dry ground (bd
    er-effects-rs-rggx), `evaluate` no longer prices these lists at all, so this module names them
    itself (`never_made`: the topmost such node's subtree grouped by the record rule, each list one
    hit at its best member, a count **INFERRED** since it is never made on land). Honed Bolt's
    10693006: 68.3 HP, one list (the flagged 10693005 starts a record its 16 shots share); 136.7
    before was the same hit counted twice by the lists before that rule. Lightning Spear's tap now
    names its on-water 10690002 the same way (**MEASURED** 2026-11-12, same files). A spell whose
    every list is on such a bullet is excluded with that reason, not 'no damaging hit'.
  - an interval child the flight never spawns: Watchful Spirit's root follows the caster
    (`FollowType` 1) and, flown as one point, its 2 s spawn wait never ran out (excluded, 928.5
    HP). The 2026-11-13 flight follows the attached root and spawns it, so no spell gives this
    reason on it (**MEASURED**, same files).
  The same rule applies to a skill's bullets (`skill_attack(flown_only=True)`).
- **FP** (**REGULATION**, J3): on every loop spell `Magic.consumeLoopMP_forMenu` equals
  `mp_charge`, so `evaluate`'s charged `fp` is the per-tick cost (Crystal Barrage 14/2/2, Gelmir's
  Fury 16/3/3, Dragonfire 28/4/4, Comet Azur 40/10/10). An entity of a loop spell takes `Magic.mp`.
  Only a variant that holds is flagged `fp_lower_bound` (**COMMUNITY**: the wiki writes 'Dragonfire
  28 (4)'): a charged cast, or the one cast `SpellModel.variants` now gives a hold spell, which is
  the `045010` clip round 4 scored as its held 'charged' variant, so it keeps the flag whether or
  not the spell has a loop clip. This narrows the spec's
  `bool(fp_loop)` by a choice: a tap ends before the loop clip, so its `Magic.mp` is taken as exact
  (**INFERRED** from the clip it plays). The 'full count' rule (section 6) reads channelling on the
  same reading: `evaluate`'s `channel` is a property of the whole spell (its refType has a loop
  clip), and a tap of such a spell is not channelled. Zamor Ice Storm, a non-loop chargeable spell,
  keeps 17/17.
- **Weapon skills**: every unique skill of a named base weapon and every ash of war. Left out:
  sids 0, 1 and 10 (not a skill) and `data/ash-blacklist.toml` (unless `--include-blacklisted`).
  An ash whose every gem row has `sortId` 999999 loses its mounts but keeps the weapons that ship
  with it (**REGULATION**, `swordArtsParamId`): Firebreather on the Steel-Wire Torch, Torch Attack
  on the Torch, Ghostflame Torch, Beast-Repellent Torch and Sentry's Torch, Buckler Parry on the
  Buckler. Only an ash with no such weapon would be 'unobtainable': none is, in the full run.
- **Carriers**: an ash's carriers are every base weapon that mounts it at some affinity at its
  top level, plus the weapons that ship with it (Meteoric Ore Blade for Gravitas); a unique
  skill's are its own weapons. They are bucketed by weapon class and skill diff category, one
  weapon per bucket is probed for its opening clip, and buckets with the same opening merge. Each
  opening is one entity group (`skill:<sid>:<opening>`), carried by its weapon of least total
  requirement, at the mountable affinity whose skill damage is largest (ties to Standard). An
  affinity the model raises on is recorded on the entity (`affinity_errors`): none in the full
  run. A stance skill gives one entity per button. A weapon that ships with the ash and mounts no
  gem (`gemMountType` not 1 or 2: the Meteoric Ore Blade's Gravitas) is a carrier nobody can swap
  out, at its own affinity and scaling, so it is also scored on its own, keyed
  `skill:<sid>:<opening>:fixed-<weapon id>` (round 4 scored it only when it was its group's carrier
  of least requirement).
- **Charged skills** (**VERIFIED** by a round-5 survey of params and TimeActs, `MEASURED` worths):
  68 `AtkParam_Pc` rows carry `subCategory` 111 ('charged skill'; the value is the talisman gate,
  talismans.md). 21 skills (24 opening groups) have such rows, every one in the opening clip and its
  no-FP copy, so the default entity, which scores the opening clip, is the full charge. 28 skills
  carry the early-release window, SpEffect 100285, on the opening (those 21 plus Thundercloud Form,
  Kowtower's Resentment, Shield Crash, Black Flame Tornado, Torch Attack, Jori's Inquisition and
  Moore's Charge). Letting go of L2 while it is on plays the clip after the opening (**VERIFIED**
  HKS reading, chainsaw/fallback-test-plan.md section 4). So a skill with the window gets a second
  entity, its early release (`skill_releases`): that clip let go on a frame of the window the
  attacker picks (section 2c: one member per release frame, scored as their mean, or with an
  opening hit before the window by `FollowupForm`), keyed `...:tap-<clip>-f<first frame>`, a
  variant of the default, which is labelled 'charged'. `skill_option` prices a release as a follow-up and adds
  the button's FP column; the entity takes it back off, the release being the same cast. Whether
  letting go before the opening's FP event still pays the FP is not traced. A probe before this
  change (round 5, `MEASURED`): Carian Grandeur's early release is worth 757.4 against the full
  charge's 1,377.1 (roll 73.3 against 111.3), Swift Slash's 1,540.2 against 2,567.1. The late
  release (SpEffect 100286, Carian Grandeur alone) is not scored: which clip it plays is not
  established.
- **Follow-ups**: each follow-up is its own entity, keyed by button, clip and the real frame it is
  pressed at (`:then-r2-040010-f10`). Round 3 keyed it by button and clip only, and Stamp (Sweep)
  offers R2 040010 from two TAE 66 windows (f10 and f18), so one silently overwrote the other; a
  key produced twice now stops the run. The press cancels the opening, so `skill_attack` leaves out
  the opening's hits that start at or after the press. `skill_option` keeps them, which counted
  Stamp's attack twice: `skill_hits` scores Stamp's 040000, which has no hit, on its first
  FP-charging clip 040010, the R2 follow-up itself, so the follow-up listed atk 300000290 at f37
  twice, worth 2x at FP 13 (Stamp (Sweep) on the Large Club the same through 040010 under
  040210). 32 follow-up entities in the round-4 full run; 5 leave opening hits out, and no entity
  keeps a hit in two parts at one frame. The opening's hits before the press stay in the
  follow-up, and they are the opening's: in round 4 the Glintblade Phalanx R2 follow-ups were twins
  of the spell through their f33 volley, the opening's own (D_contain 0.06897, the opening's whole
  D_assoc), 2 of 8 component twins and 2 of 12 cross-type twins. A follow-up's frames before its
  press now get no component (`inherited_frames`), so no association runs through them; `--show`
  names them. The press is the follow-up part's offset (`press`), which a stance's entry moves
  past the window's first frame (Moon-and-Fire Stance R1: window f35, press f43, so its f37 and
  f38 frames are the opening's). The follow-up is scored under the press the attacker picks in
  its window, not at its first frame alone (section 2a); the key keeps the window's first frame.
- **An opening scored on another clip**: when every hit of a default skill entity is in an
  FP-charging clip outside its opening's ten-block (`skill_hits`' fallback), its FP (`skill_fp`,
  the L2 column) leaves that clip's own charge out (**REGULATION** Stamp: 5 / 8), so it is flagged
  `fp_lower_bound` like a held loop spell: its dominance edges are full_only and the frontier warns
  'FP lower bound (follow-up clip)'. Five in the full run: Stamp (Upward Cut) [Club], Stamp (Sweep)
  [Club] and [Large Club], Dynast's Finesse [Bloody Helice] and Death Flare [Eclipse Shotel], all
  scored on 040010. Whether the game plays that clip without the button press is not traced.
- **Status**: scored (every event or hit has a reach model), partial (some have none: a skill's
  melee hit has no posed window or a bullet has no flight, a spell event or hit has one of the
  reasons in section 7) or excluded (none has). A partial entity is scored on its hits that have a
  reach model, the same rule for spells and skills; the left-out hits and their worth are listed
  (`missing`, `missing_hp`).
- **Delivery**: every entity records how its worth is delivered (`delivery`: melee hits, bullets,
  or both; a spell's scored hits are all bullets), and every component how its frame's is.
- **Components**: a scored entity with two or more damaging frames also gets one component per
  frame (the 8 most valuable; 5 entities lose 36 frames to that cap). Components feed 'contains'
  and component twins and are never matrix rows.

Full run: 213 spells and 327 skill openings read; 1,137 profiles (499 whole entities: 203 spell
variants and 296 skill entities; 638 components). Status: 470 scored, 29 partial, 184 excluded, no
model error. Groups (section 3):

| kind | live | runin_only | nets_nothing, always evaded | nets_nothing, never reaches |
| --- | --- | --- | --- | --- |
| spell | 71 | 15 | 72 | 45 |
| skill | 150 | 20 | 107 | 19 |

12 live entities net at most 1 HP on average and carry 'punished as much as it lands', among them
Jori's Inquisition (-229.1), Bubble Shower (-224.5) and Light (-154.9) (round-4 full run). Round 3
flagged none: it required |mean net| <= 1 HP and so missed the attacks punished more than they
land. An attack that is not live and nets under -1 HP now carries 'lands little, punished more':
round 4 left 65 such entities unflagged, among them Flame Spit [One-Eyed Shield] (-358.3), Claw
Flick (-155.9), Ancient Lightning Spear (-123.0) and Starcaller Cry (-87.8). Both flags read the
distance basis's nets; each attacker build lists its own members netting at most 1 HP (section
6).

## 2. The outcome per situation

The situations are `er-mechanics-spells.punish_situations(150)`: 1,208 rows, W = 779.833, n_eff =
(sum w)^2 / sum w^2 = 466.2 (**MEASURED**). Per situation `er-mechanics-spells.net_parts` gives:

- **now**: pressed at once at the situation's distance and floor: what lands (`land_now` = landed
  worth / full worth), the punish chance after a roll (`pun_now`), what lands on a defender who
  does not dodge (`base_now`) and the frame the attack first touches him (`t_first`).
- **best**: the better of pressing at once and running in first to 1.5-4.5 m (`land`, `pun`, the
  gap chosen) and its net, which is exactly what `net_values` returns (the selftest replays it
  without the memo and compares every number).
- **net_one**: the same choice with one hit per event instead of the full count.

`net_parts` shares one `_catches` memo per call (**VERIFIED**, J1: both reads depend only on the
geometries, the distance, the move and a grid that depends only on the geometries), which leaves
every number as it was and makes the kit and the rival pass faster too.

The press-now shares are scale-free while the defender's choice to strike or roll holds: twice or
four times the damage lands the same share pressed at once, and the punish chance does not move
(selftest check 5, on the Zamor Ice Storm tap). Since version 11 the choice itself is not
scale-free: `net_parts` takes the strike when it leaves the attacker less, worth less the R1's
fixed `PUNISH_HP` share against worth less the roll's punish, so scaling the worth can flip it in a
situation (**VERIFIED**, `er-mechanics-spells.net_parts` `at`; the first selftest under version 11
failed the old all-situations equality on it). The best option is not scale-free either: it picks
the run-in by the net (damage less the punish), so scaling the damage can move the pick. At 4x its
damage the Zamor tap's `pun` moves by up to 0.621 in one situation and its profile is D_rel 0.0098
from the 1x profile, under `--twin` (**MEASURED**, selftest under version 11; before the strike
0.414 and 0.0044).

The press-now profile is mostly empty (**MEASURED**): the median entity lands 0.7% of its worth on
average pressing at once, 2.5% with the best option. Most of what an attack lands against a
dodging defender is the run-in, and the run-in mostly lands one share wherever it is picked: the
share of the four roll directions the attack still catches. Of the 212 entities above the
association floor in the full run, 192 run in somewhere (on a median 0.717 of the weight); for 131
of them the land share is the same in every situation they run in, and 26 land exactly 0.250 in
each. So the land channels of two attacks often agree on one number whatever the attacks are,
which is why the association distance below also asks how an attack reaches a defender who does
not dodge, and why the delivery gate exists.

### 2a. A follow-up pressed late (bd er-effects-rs-me5p)

User, 2026-11-10: "you can delay your followup slightly if you think your opponent will attempt to
roll a projectile from the first part". Until this change every follow-up was scored pressed at the
first frame of its window only, and `sequence_outcome` could not have shown a later press paying:
a burst the defender evaded held his next dodge back to that burst's last live frame plus his
roll's recovery, whatever frame he rolled at. Glintblade Phalanx's opening volley lives f33-63.5,
so a defender who rolled it met the R2 volley (f65-69) with a floor of f71.5 and was caught
wherever it reached him (**MEASURED**, round 5 geometry). A follow-up entity is now scored by
`FollowupForm` (`er-mechanics-ashes.FollowupModel`; the rules are in ashes-of-war.md section 16a):

- his roll of the opening is played as `sequence_outcome` plays it, and the follow-up meets that
  roll: it catches him outside the roll's invincibility before its dodge cancel, else he is free;
- he knows the attack as pressed at once (cue 0, as every attack here) and dodges the follow-up
  from his dodge cancel on; a late press meets that planned dodge early, then he reacts to the
  press (**INFERRED**: he reads no delay ahead);
- the attacker presses late only on a roll that showed on his machine his own reaction before the
  window opened (`attacker_reactions`, **INFERRED** symmetry), at the frame of the window that
  leaves him most against it; any frame of the window starts the follow-up and moves it whole
  (**VERIFIED** press rule, attacks.md section 4; `skill_option` offsets the part by the press).

So the stayer (`base_now`, `t_first`) meets the earliest press, and per situation the policy nets
at least what the press at once nets (selftest 30). The earliest-press entity is replaced, not
kept: it is one choice of the same attack at the same FP, so keeping it would make each follow-up
dominate or twin itself. `FollowupModel.outcome(read=True)` also measures a read, a default delay
taken before any roll shows; it is not scored, because the planned-dodge defender never expects a
delay, so a blind delay beats him wherever the window allows.

Mean net per situation weight, HP (**MEASURED** 2026-11-10, this code against the round-5 rule on
the same inputs; the INT line {str 16, dex 18, int 65, fth 9, arc 9} and the Dexterity line {str 19,
dex 69, int 9, fth 12, arc 7}, each on the carrier and affinity `build_job` / `skill_job` pick at
its stats). 'At once' is the new rule with every press at the window's first frame; 'scored' is
the policy; 'late' is the share of the weight the attacker presses late on:

| follow-up (window) | carrier, INT / DEX | INT before / at once / scored (late) | DEX before / at once / scored (late) |
| --- | --- | --- | --- |
| Glintblade Phalanx R2 (f45-62) | Dagger | 449.4 / 165.1 / 369.2 (0.8) | 297.2 / 95.2 / 242.1 (0.8) |
| Glintblade Phalanx R2 (f45-62) | Ritual Thrusting Shield | 719.7 / 292.8 / 604.9 (0.8) | 538.6 / 187.8 / 445.8 (0.8) |
| Glintstone Dart R2 (f63-82) | Glintstone Kris / none | 45.1 / 0.0 / 213.3 (1.0) | not wielded |
| Stamp (Upward Cut) R2 (f10-43) | Club | -17.1 / -17.1 / -17.1 (0) | -17.1 / -17.1 / -17.1 (0) |
| Stamp (Sweep) R2 (f10-43) | Club | 267.0 / 267.0 / 267.0 (0) | 276.5 / 276.5 / 276.5 (0) |
| Stamp (Sweep) R2 (f10-43) | Hefty Scimitar (040200 group) | 370.0 / 370.0 / 370.0 (0) | 354.3 / 354.3 / 354.3 (0) |
| Earthshaker R2 (f58.8-86.8) | none / Zweihander | not wielded | -76.9 / -93.9 / 274.4 (1.0) |
| Golden Land R2 (f68-96) | none / Zweihander | not wielded | 258.3 / 789.1 / 905.0 (0.9) |
| Waves of Darkness R2 (f68-96) | none / Zweihander | not wielded | 242.7 / 247.6 / 252.6 (0.0) |

The Stamp rows do not move: their opening has no hit, so there is no roll to see, and at once the
new rule is the old one (no roll, the follow-up planned from the attack's start). The Glintblade
Phalanx rows fall at once, because the defender who rolled the opening volley is free at his own
dodge cancel and dodges the R2 volley he expects (before, the f71.5 floor caught him), and the late
press wins back most of it on the 0.8 of the weight where his roll shows before f45. Golden Land
rises at once: its wave comes 3 frames after the press, and his dodge cancel from his roll of the
f55-57 burst (rolls at about f49-54 by the debug read at 1.5 m, so f70-75) is later than the old
floor (f57 + 8 = f65).

Cost (**MEASURED**, first build on a form, shapes built): Golden Land [Zweihander] 433 s before the
touch tables, 7.5 s with them; Glintblade Phalanx 22-28 s, 9-15 s; every per-situation array of the
table above is bitwise the same. The follow-up's reads all come from one `_TouchTable` per
distance and move (`er-mechanics-ashes`): its 5,950-sample wave was re-tested for every floor a
roll left him at, though at most 1.3% of its sample and roll pairs ever touch him (345, 322 and 98
of 26,232 at 2, 4 and 6 m; `_may_touch` leaves out the samples a bound on where his root goes
shows can never touch), and a trimmed or delayed read is an
index into that table. A caught defender is played only for the (delay, geometry) pairs the
policy can read.

### 2b. What is on screen (version 8)

Until version 7 the reacting defender knew the attack he faced from its first frame and when each
of its shots would reach him, so a single bolt with an escaping run of a few frames in every roll
direction was rolled in every situation: Glintstone Cometshard and the Glintstone Dart netted about
0 on every line (section 8, the open question r7 left). Since version 8 he sees only what is on
screen, by the rule the spell kit uses (ashes-of-war.md section 16a, 'What is on screen';
`er-mechanics-spells.Lookalikes`, `er-mechanics-ashes.lookalike_plan`):

- a spell is one cast of a group: every variant of every spell of its (`Magic.refType`,
  `castSfxId`) among the spell candidates (usable in multiplayer, held by a player, cast by a
  catalyst of the run) that the line casts with the run's catalysts, each pair told apart at the
  frame `SpellModel.look_split` gives (a tap and its charge where the tap's release shows, section
  2c; two spells at the first shot that differs). The clips and the 045010 hand-off are **VERIFIED** (c0000.hks, bd
  spell-tap-vs-charge-speffect-100610-in-045010-2026-11-10); that equal `castSfxId` SFX look alike and
  a different one is told apart from the first frame is **INFERRED**;
- a charged skill's hold is one with its early release, told apart where the release shows (section
  2c; the release in its window, **VERIFIED** c0000.hks `SwordArtsOneShot_onUpdate`, bd
  skill-tap-vs-hold-speffect-100285-l2-up-2026-11-10);
- until that frame he either times one dodge against every member still alike or waits for it and
  reacts from it, whichever escapes the group more often in that situation, the members weighted
  equally (**INFERRED**: nothing says how often each is cast); only his first dodge is planned so.

The group is read per line (`look_specs`): the distance basis, each attacker build and each
objective line have their own, since what a line casts and how fast it casts it are its own. A
member's geometry is phase 1's at that line; a spell no item ran there (one `--only` left out, one
an objective line casts) is run there for the group alone (the 'lookalike inputs' stage). A
component is read as its whole entity, against the whole's lookalikes. The group is part of the
form's fingerprint (`look_sig`), so an `OutcomeForm` is shared, and checked against `net_parts`,
only between builds whose group is the same. An attack alone in its group, every follow-up and
an early release with an opening hit before it (`FollowupForm`, whose defender still plans his
roll of the opening for the press at once) are read as before (since version 9 any other early
release is read in its hold's group, section 2c); each record names its distance-basis group
('lookalikes').

What it moves at the distance basis (**MEASURED** 2026-11-10, `profile_arrays` with and without the
group, weighted mean net over the 1,208 situations; every spell candidate with a tap and a charge
or a lookalike spell, and every charged skill's hold on its opening group's carrier: 183 entities
with a group, 53 moved by more than 1 HP and a tenth of their net, 49 up and 4 down):

| attack | before | after |
| --- | --- | --- |
| Glintstone Cometshard tap / charged | 0.7 / -1.0 | 39.0 / 0.0 |
| Comet tap / charged | 0.9 / -7.4 | 128.8 / 76.2 |
| Shard Spiral tap / charged | 54.0 / 51.8 | 104.3 / 149.2 |
| Night Comet tap / charged | 0.3 / -4.2 | 8.6 / 0.0 |
| Glintstone Dart [Glintstone Kris] hold | -0.6 | 0.0 |
| Wrath of Gold charged | 247.1 | 989.2 |
| Giantsflame Take Thee charged | 0.0 | 604.6 |
| The Flame of Frenzy tap | -118.4 | 246.9 |
| Flame Spear hold (its 040200 carrier group) | -1.4 | 339.9 |
| Revenger's Blade hold | -0.3 | 307.1 |
| Midra's Flame of Frenzy tap | 279.3 | 124.9 |
| Zamor Ice Storm tap | 43.3 | 3.4 |
| Aspects of the Crucible: Bloom charged | 58.7 | 10.1 |
| Cherishing Fingers charged | 183.8 | 160.7 |

The four that fall are the rule's, not a defect: he picks his timing to escape the whole group
(`group_presses`), and that timing can escape the attack itself more often than the timing
`dodge_presses` picks against it alone (the Zamor tap: he commits at the cue on every situation,
and the run-in that landed 0.050 of its worth lands 0.008); the kit shows the same on Midra's Flame
of Frenzy (ashes-of-war.md section 16a). The selftest's check 9 therefore compares the Zamor skill's
storm with the tap read alone, which is what it tests (the caught defender's ticks).

Cost (**MEASURED**, the selftest's subset against the previous selftest record, both 3 workers on 4
CPUs): the 'attacks' stage 1,241 -> 1,472 CPU s (x1.19), the whole run 1,385 -> 1,575 CPU s; the
dearest are the four-member group's (Star Shower tap 52 -> 106 s, charged 49 -> 93 s), whose
members' escaping starts are read again for every form.

### 2c. The release frame is the attacker's choice (version 9)

Until version 8 a release was read at its window's first frame: a spell's tap at the first frame
of 045010's SpEffect 100610 window, a charged skill's early release at the first of its 100285
window. The frame is the attacker's choice on any frame of the window (**VERIFIED** c0000.hks
`MagicFireRight_Upper_onUpdate` and `SwordArtsOneShot_onUpdate`, the bd memories of section 2b),
and nothing on screen tells the defender which frame is coming. Since version 9:

- **Members.** A release is one member of its group per real frame of its window
  (`er-mechanics-ashes.release_frames`): [first, last), the end left out (**INFERRED** as for a
  follow-up's window), each weighed alike (`release_members`; Glintstone Cometshard's f0-20: 20
  members, a twentieth each; the Glintstone Dart's f13-26: 13; Revenger's Blade's and Flame Spear's
  22; Rock Blaster's f10-46: 36; Gelmir's Fury's f34.3-115: 81). The release's weight 1 is split
  over them, so the other members of the group (the charge, the hold, another spell's casts) keep
  their weight. Each member is that release let go there: a spell's 045010 up to it and 045011
  from it (`SpellModel.evaluate` `lead`, its launches and roll moved with it), a skill's release
  clip from its own press with the opening's hits before it (`skill_attack` with the press moved).
  Phase 1 scores every one; the group and the release's own reads hold nodes of them, each weighing
  the frames around it ('Which frames are read' below).
- **Splits.** A member parts from the hold or charge where its release shows (below: the release
  frame plus its visible delay) and from a later release where the earlier one shows; a spell's
  casts still part where `look_split` says when their shots differ first (a shot is seen when it
  spawns, unchanged; Rock Blaster's tap and charge part at the tap's release, f10, where 045010's
  f10 rock and 045011's first shot differ, before the tap's clip shows 5 frames later).
- **What the defender does.** `lookalike_plan` as in section 2b, plus one thing (**INFERRED**): a
  release he sees before his decision time he reacts to as it shows, with what he knows then
  (`events`, each release's shown frame). Without it, a decision taken at the last release frame
  reacts to the first release that late: the first run of this change, without it, put Glintstone
  Cometshard's tap at 264 HP and its charge at 78 (against 39 and 0 in version 8), which is the
  plan's limit, not the attack's. A group with no release spread over several frames passes no
  events and is read as in version 8.
- **Where a release shows** (`er-mechanics-ashes.release_visible`, cached per category, hold
  clip, release clip and release frame). On the switch the release clip blends in over the
  smoothstep 3w^2 - 2w^3 of the share w of the blend time elapsed, which is the release clip's TAE type-16 end (**VERIFIED**:
  `CustomTransitionEffect` activate 0x1419bd150 -> FUN_1419bd2b0 -> the `CSChrTimeActModule`
  callback -> FUN_1401aa350, smoothstep FUN_141499950; per bone, positions lerped and rotations
  nlerped, root motion blended; bd player-blend-time-is-tae-type16-end-2026-11-11). The held side
  is the clip the button held on plays (a skill's hold clip, a spell's 045010, then its 045012
  loop for a hold spell), past its end its last frame. The release shows on the first real frame k
  >= 0 after the switch at which some bone (`*_Target` and `*Dummy*` left out) of the blended pose,
  root motion and TAE speed counted (`clip_to_real`), is `VISIBLE_M` 0.10 m or more from where the
  held clip has it. **INFERRED**: that 10 cm is what a defender reads as a different move; the
  results at 5 and 20 cm are below. It moves only the splits that come from a pose: a tap from its
  charge, a release from its hold, a hold spell let go early from the held cast, and two releases
  from each other. Real frames from the switch to the shown frame (**MEASURED** 2026-11-11,
  `release_visible`, `target/rel/vis_pairs.py`; at the window's first frame, then the least /
  median / most over its frames; the 2026-11-11 survey in dnok-hit-records' `target/vis` has the
  same first-frame numbers for the Dart, Revenger's Blade, Flame Spear, Carian Grandeur and
  Lightning Spear at all three thresholds, and 3 where this has 4 for Zamor Ice Storm at 10 cm):

  | release | frames | 5 cm | 10 cm | 20 cm |
  | --- | --- | --- | --- | --- |
  | Glintstone Cometshard tap, Comet tap | 20 from f0 | 4; 3/4/4 | 4; 4/5/6 | 6; 6/6/10 |
  | Lightning Spear tap | 10 from f0 | 3; 3/3/3 | 3; 3/3/3 | 4; 4/4/4 |
  | Wrath of Gold tap | 10 from f0 | 3; 3/3/3 | 4; 4/4/4 | 4; 4/4/4 |
  | Zamor Ice Storm tap | 13 from f0 | 3; 2/3/3 | 4; 3/3/4 | 5; 4/4/5 |
  | Rock Blaster tap | 36 from f10 | 3; 2/3/4 | 5; 3/4/5 | 7; 4/7/7 |
  | Dragonfire early | 18 from f64 | 5; 3/5/5 | 8; 4/6.5/8 | 18; 6/9.5/18 |
  | Ekzykes's Decay early | 22 from f69 | 3; 2/3/3 | 4; 3/4/4 | 6; 4/5/6 |
  | Crystal Barrage early | 53 from f36 | 7; 4/7/30 | 12; 5/11/30 | 15; 7/15/30 |
  | Gelmir's Fury early | 81 from f34.3 | 5; 5/5/5 | 10; 10/10/13 | 14; 14/14/24 |
  | Revenger's Blade release [Falx] | 22 from f22 | 1; 1/1/2 | 2; 1/2/3 | 2; 2/2/4 |
  | Flame Spear release [Dagger] | 22 from f20 | 2; 2/2/2 | 2; 2/2/2 | 3; 3/3/3 |
  | Glintstone Dart release | 13 from f13 | 2; 2/3/3 | 3; 3/3/4 | 4; 4/5/5 |
  | Carian Grandeur release [Dagger] | 12 from f27.3 | 2; 2/2/2 | 3; 3/3/3 | 4; 3/3/4 |
  | Swift Slash release [Reverse-Bladed Sword] | 10 from f10 | 2; 2/2/2 | 2; 2/2/2 | 3; 3/3/3 |
  | Divine Beast Frost Stomp release [Club] | 15 from f21 | 1; 1/1/2 | 2; 2/2/2 | 2; 2/2/3 |

  At 10 cm a skill's release shows within 1 to 4 frames, a spell's within 3 to 13, except where the held
  breath or barrage and its release-out stay close for long: Crystal Barrage let go late in its
  window does not show within `VISIBLE_SCAN` 30 frames, which is then taken as its shown frame.
  Where it matters is a single fast shot whose escaping run is a few frames wide (**MEASURED**
  2026-11-11, `target/rel/cs_why.py`, the kit's rule at 3, 6 and 9 m, INT 80, DEX 18, Glintstone
  Cometshard's tap at its earliest release against its charge): waiting for the release to show,
  he decides at f27.7 instead of the hand-off's f23.7 and escapes the bolt, cast at f38.7, 0.51 of
  the time instead of 0.92, so the tap nets 272.7 instead of 44.0 in each situation. What the
  threshold moves (**MEASURED** 2026-11-11, `compute` on the 14 names of the table below at the
  distance basis, `target/rel/vis_smoke.py` at each threshold, mean net per situation weight; the
  rows not here, Revenger's Blade, Flame Spear on the Bastard Sword and Short Spear, Swift Slash,
  Wrath of Gold, Rock Blaster's charge, Crystal Barrage, Dragonfire and Ekzykes's Decay, move by
  under 0.1 HP):

  | attack | no delay | 5 cm | 10 cm | 20 cm |
  | --- | --- | --- | --- | --- |
  | Glintstone Cometshard tap | 38.5 | 220.9 | 227.5 | 223.0 |
  | Glintstone Cometshard charged | 0.0 | 0.0 | 0.0 | -2.2 |
  | Comet tap | 124.4 | 350.9 | 437.4 | 461.3 |
  | Comet charged | 117.3 | 180.5 | 217.4 | 250.4 |
  | Glintstone Dart early release | 0.5 | 5.4 | 9.6 | 25.0 |
  | Carian Grandeur early release [Dagger] | 14.1 | 103.7 | 231.6 | 251.4 |
  | Carian Grandeur hold [Dagger] | -117.7 | -96.0 | -85.3 | -85.3 |
  | Divine Beast Frost Stomp early release | 155.8 | 158.3 | 161.1 | 161.1 |
  | Divine Beast Frost Stomp hold | 4.4 | 5.4 | 5.8 | 5.8 |
  | Flame Spear early release [Dagger] | 137.2 | 137.3 | 137.3 | 137.3 |
  | Rock Blaster tap | 113.6 | 113.6 | 113.6 | 120.6 |
  | Lightning Spear tap | 16.0 | 22.0 | 22.0 | 20.8 |

  Most of what the delay moves comes with its first few frames (5 cm already moves Glintstone
  Cometshard's tap 38.5 -> 220.9); from 10 to 20 cm the nets move by under a tenth except the
  Glintstone Dart's release (9.6 -> 25.0) and Comet's charge (217.4 -> 250.4), so the 10 cm
  choice decides little beyond those two.
- **What the attacker nets.** The members' weighted mean per situation (`mix_arrays`; the kit's
  `punish_values`), every array likewise (the run-in gap over the members that ran in).
  **INFERRED**: he draws the frame uniformly over the window. That is the reading consistent with
  how the group is weighed: the defender's plan weighs the members equally (section 2b), which is
  right only if the attacker mixes so; his best frame against that plan would be a frame the plan
  does not expect, and a defender who knew it would weigh it so.
- **Which frames are read: nodes** (`release_strata_of`; `er-mechanics-ashes.release_nodes`,
  `node_weights`, `release_fill_in`, the rule 'once' below). Phase 1 scores every release
  frame; phase 2 reads a release at nodes of them: the window's first and last frames and every
  `RELEASE_STEP` 5th between, and then, where two neighbouring nodes' mean nets at the distance
  basis differ by more than `RELEASE_SPLIT_HP` 1 HP over the share of the window between them ((b
  - a) / n x |difference|), every frame between the two, in one more read of the group. A round
  costs about one plan of the whole group over all its nodes (the plan and each node's outcome
  under it change with the group; a node's own reads of its geometry do not, and are kept across
  rounds, exactly, `kept`, though that saved nothing measurable), so one round that reads a
  stretch frame by frame beats halving it round after round. The frames between two nodes count as the straight line between them: each node is one
  member of the group weighing its share of them (`strata_spec`), and the release's arrays and nets
  are the nodes' weighted mean. Every attacker build and objective line reads the basis's nodes. The
  ends are always nodes because a release can differ at them from all its others (Rock Blaster's
  tap: its first frame nets 705 HP, every later one 97, so the mean of its 36 is 113.6) and because
  the last release is when the defender knows it was the hold (Flame Spear's hold: -5.9 when the
  last member is two frames short of the window's end, -46.1 when it is the end). The
  coordinator's first version of this, strata of about 5 frames read at their middles and a
  stratum cut in two where its weight times its member's step to a neighbour is over 1 HP, missed
  Rock Blaster by 17 HP (no middle ever sees the first frame, and its neighbours agree) and cut
  Star Shower's needlessly; the table below has both. History, the first grid measurement
  (**MEASURED** 2026-11-11 at the distance basis, before the visible delay below; the fixed rules
  put the members on their grid in the group too, 'left' scoring each run of frames at its first,
  'mid' at its middle; mean net, '!' where it misses 3% and 5 HP of every frame read; 'read where
  it moves' is the adaptive rule this replaced, every frame in the group and its own nets read at
  the ends, every 5th and where two read ones differ by over 4 HP, `release_read`):

  | entity | every frame | read where it moves | left 10 | mid 10 | left 5 | mid 5 | left 3.3 | mid 3.3 | left 3.3 cap 4 | mid 3.3 cap 4 | left 3.3 cap 6 | mid 3.3 cap 6 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | Revenger's Blade release [Falx] | 242.7 | 242.6 | 260.3 ! | 245.6 | 296.1 ! | 226.6 ! | 259.9 ! | 247.3 | 264.4 ! | 261.2 ! | 242.3 | 238.9 |
  | Revenger's Blade hold | -31.6 | -31.6 | -20.2 ! | -31.6 | -23.4 ! | -25.9 ! | -31.6 | -31.6 | -31.6 | -31.6 | -20.2 ! | -31.6 |
  | Flame Spear release [Dagger] | 137.2 | 137.2 | 151.0 ! | 135.4 | 168.0 ! | 125.3 ! | 146.2 ! | 143.2 ! | 154.1 ! | 144.7 ! | 156.7 ! | 133.0 |
  | Flame Spear hold | -46.1 | -46.1 | -10.2 ! | -46.1 | -46.1 | -27.8 ! | -45.6 | -45.8 | -24.8 ! | -46.1 | -36.4 ! | -45.8 |
  | Glintstone Dart release | 0.4 | 0.5 | 1.0 | 1.0 | 0.8 | 0.7 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 | 0.6 |
  | Glintstone Cometshard tap | 38.5 | 38.5 | 38.7 | 38.7 | 38.6 | 38.6 | 38.5 | 38.5 | 38.6 | 38.6 | 38.5 | 38.5 |
  | Wrath of Gold tap | 824.3 | 824.3 | 250.6 ! | 250.6 ! | 824.3 | 824.3 | 824.3 | 824.3 | 824.3 | 824.3 | 824.3 | 824.3 |
  | Wrath of Gold charged | 247.1 | 247.1 | 989.2 ! | 989.2 ! | 247.1 | 247.1 | 247.1 | 247.1 | 247.1 | 247.1 | 247.1 | 247.1 |
  | Rock Blaster tap | 113.6 | 113.6 | 248.8 ! | 248.8 ! | 164.3 ! | 164.3 ! | 147.4 ! | 147.4 ! | 248.8 ! | 248.8 ! | 198.1 ! | 198.1 ! |
  | Gelmir's Fury early | 330.0 | 330.0 | - | - | 330.0 | 330.0 | - | - | - | - | - | - |
  | CPU s, these entities | 600 | 382 | 38* | 38* | 98 | 104 | 96* | 97* | 58* | 58* | 77* | 78* |

  (* without Gelmir's Fury, which reading every frame takes about 100 s of.) No fixed grid got
  within the tolerance on every name (Rock Blaster's tap missed on every one), and the adaptive
  rule did, within 0.2 HP, but at 382 CPU s against 600 for every frame: it kept every frame in
  the group, so the group cost stayed. Over a selftest's subset that made the 'attacks' stage x4.13
  of version 8's, which is what the node rule replaces. The node rule against every frame in the
  group and read (**MEASURED** 2026-11-11, `target/rel/rel_strata.py`, visible delay 10 cm, each
  item's keys read together as `compute` reads them; the seven names above and the two the
  coordinator named, Roiling Magma and Collapsing Stars, whose taps a midpoint grid of 5 missed by
  33 and 31 HP, and Star Shower, which no rule should need to cut; mean net at the distance basis,
  '!' outside 3% or 5 HP, then nodes read of the frames; CPU per item at the basis on the current
  code; the adaptive rule's from the code before the one-job-per-group cut below, which took 37%
  off a sample of full items, so not like for like):

  | entity | every frame | nodes | midpoint 5 | midpoint 5, cut where its step to a neighbour is over 1 HP | adaptive (CPU) |
  | --- | --- | --- | --- | --- | --- |
  | Revenger's Blade release [Falx] | 242.7 (19 s) | 242.6, 13 of 22 (22 s) | 222.5 ! (4 s) | 244.2, 13 strata (39 s) | 18 s |
  | Revenger's Blade hold | -31.6 | -31.6 | -22.9 ! | -28.6 | 10 s |
  | Flame Spear release [Dagger] | 137.4 (51 s) | 137.3, 14 of 22 (59 s) | 124.3 ! (14 s) | 138.9, 14 strata (125 s) | 62 s |
  | Flame Spear hold | -46.1 | -46.1 | -5.9 ! | -42.0 | 39 s |
  | Glintstone Dart release | 9.5 (42 s) | 9.6, 4 of 13 (6 s) | 9.5 (9 s) | 9.5, 3 strata (10 s) | 27 s |
  | Glintstone Cometshard tap | 227.5 (61 s) | 225.3, 14 of 20 (63 s) | 237.6 ! (16 s) | 229.4, 14 strata (107 s) | 76 s |
  | Wrath of Gold tap | 824.3 (7 s) | 824.3, 3 of 10 (1 s) | 824.3 (2 s) | 824.3, 2 strata (1 s) | 4 s |
  | Rock Blaster tap | 113.6 (91 s) | 113.6, 10 of 36 (24 s) | 96.7 ! (19 s) | 96.7 !, 8 strata (9 s) | 56 s |
  | Gelmir's Fury early | 330.0 (180 s) | 330.0, 17 of 81 (18 s) | 330.0 (38 s) | 330.0, 17 strata (20 s) | 86 s |
  | Roiling Magma tap | 119.3 (99 s) | 119.3, 11 of 12 (66 s) | 157.4 ! (29 s) | 119.3, 11 strata (60 s) | 95 s |
  | Collapsing Stars tap | 730.6 (59 s) | 730.6, 7 of 13 (26 s) | 713.1 (16 s) | 713.1, 3 strata (10 s) | 27 s |
  | Star Shower tap | 1,429.5 (174 s) | 1,429.5, 5 of 10 (49 s) | 1,428.8 (45 s) | 1,429.5, 7 strata (82 s) | 140 s |
  | all 15 rows (with the charges, CPU) | 794 s | 340 s, worst 2.1 HP | 204 s, 7 outside, worst 40 HP | 470 s, 1 outside, worst 17 HP | 663 s |

  (The 'nodes' column is the halving version at 1 HP, round after round, measured first.) So nodes
  stay within the tolerance where the plain midpoint grid misses 7 of the 15 rows: what they add
  is the ends (both holds, Rock Blaster) and the nodes where the net turns (Revenger's Blade's and
  Flame Spear's releases, Roiling Magma). How to refine them, on those rows and on the three
  dearest releases of the selftest's subset (Radagon's Rings of Light, Great Oracular Bubble,
  Gravity Well), 20 entities (**MEASURED** 2026-11-11, `target/rel/rel_strata.py` and
  `rule_table.py`; CPU at the distance basis summed over the 20, the rules run beside one another
  in two batches of five to seven processes on 4 CPUs; worst miss against every frame, and how
  many fall outside 3% or 5 HP):

  | rule | CPU s | worst miss | outside |
  | --- | --- | --- | --- |
  | every frame | 1,294 | - | - |
  | **every frame between two nodes that differ by over 1 HP, one more read ('once', the rule)** | **894** | **0.2 HP** | **0** |
  | the same with windows of 10 to 22 frames or fewer read at every frame | 975 to 1,046 | 0.2 HP | 0 |
  | the same over 3 HP (17 of the 20, beside the 1 HP rule run again: 389 against 401 s) | -3% | 2.8 HP | 0 |
  | halving between two nodes that differ by over 1 HP, round after round | 1,106 | 2.1 HP | 0 |
  | the same over 3 HP | 1,030 | 2.4 HP | 0 |
  | halving every open stretch, stopping when a round moves the whole net by 1 HP or less | 1,195 | 1.6 HP | 0 |
  | the same stopping at 2.5 HP or 1.5% of the net | 1,077 | 7.5 HP (Flame Spear's release) | 1 |
  | nodes chosen on each frame's net against a defender who knows the release (no plan), then one group read | 1,098 | 9.6 HP (Roiling Magma's tap) | 5 |

  The other rules in this table were removed from the code after it; 'once' is the only one left.

  No frame count below which every frame is cheaper exists: every frame wins on Radagon's Rings of
  Light's tap (16 frames, 120 against 153 s) and Gravity Well's (13, 25 against 59) and loses on
  Star Shower's (10, 175 against 98). The rounds that stop on the whole net do not stop Radagon's
  Rings of Light after one round: a round moves its whole net by more than the 1.6 HP the adaptive
  rule's and a midpoint grid's totals differ by, so it ends at every frame anyway, over more rounds.
  A defender who knows the release does not show where the plan turns (Revenger's Blade's,
  Flame Spear's and Roiling Magma's releases miss). With the visible delay the adaptive rule had held
  too (**MEASURED** 2026-11-11, `target/rel/rel_vis.py`): every entity within 0.16 HP; the delay
  moves Glintstone Cometshard's tap 38.5 -> 227.5 and the Glintstone Dart's release 0.4 -> 9.5,
  and none of the other seven by more than 0.2 HP.
- **What was cut, exactly.** Bit for bit against the code before the cuts (**MEASURED**
  2026-11-11, `target/rel/verify_cut.py`: every array of four entities equal): `lookalike_plan`
  keeps its plan per decision time and per floor only where the floor can bind (a floor before
  the soonest reaction moves no press), and per member the index of what he has seen and the
  escape chance at each time; the reaction delays and timing errors are read once
  (`_SOONEST_REACTION`, `reaction_delays`, `_timing_errors`); `reaction_outcome` counts the
  arrivals for every press of a move in one search, adding them in the same order; members whose
  geometry is equal share one read of their escaping starts (`_geo_digest`: Gelmir's Fury let go
  between two casts). Glintstone Cometshard's tap 18.2 -> 8.7 s, Comet's charge 37.2 -> 7.1 s,
  Revenger's Blade's release 6.5 -> 4.2 s and its hold 4.1 -> 2.9 s. Sharing a later release's
  read with the earliest's shifted in time is exact for Comet's, Cometshard's and Revenger's Blade's
  members (every read of 1,440 to 1,920 equal) but not for Flame Spear's (1,127 of 1,920 differ, by
  up to 0.11 frames), so it is not used. One job per set of entities whose groups hold one another
  (`look_batches`, `_profile_batch`): their groups' reads, every member's escaping starts per
  distance (keyed by its geometry, `_geo_digest`) and every plan of a group that reads as another
  (`Lookalikes.content`), are made once for the set (`er-mechanics-spells.share_lookalikes`, on
  inside a job only and emptied after it). Exact (**MEASURED** 2026-11-11,
  `target/rel/share_check.py` on Star Shower, Glintstone Stars, Radagon's Rings of Light,
  Glintstone Cometshard and Revenger's Blade with every attacker build and the objective lines: a
  sha256 over every entity's arrays, build nets and objective rows equal with it and without), and
  it took the sample's 'attacks' stage 1,996 -> 1,265 CPU s. The same sharing within one entity
  alone bought 3% (1,996 -> 1,937; the lines' geometries differ, so they share little), and a
  per-member cache of `group_presses`' chances was tried with it and left out.
- **A hold spell let go early** (the new 'early' variant, `SpellModel.variants`): a hold spell
  (`consumeLoopMP_forMenu` > 0, a 045011 with no cast event) whose 045010 has the 100610 window:
  Dragonfire, Dragonice, Glintstone and Rotten Breath (a467, f64-82), Agheel's Flame, Borealis's
  Mist, Ekzykes's Decay, Ghostflame and Smarag's Glint Breath (a450, f69-91), Crystal Barrage
  (a404, f36-89), Gelmir's Fury (a502, f34.3-115) and Aspects of the Crucible: Breath (a500,
  f55-93). Its release path is the tap's (**VERIFIED** HKS, as above): 045010 up to the release,
  then 045011, the release-out, which casts nothing, so a release lands 045010's casts before it
  and the caster rolls on 045011's gate from it (Dragonfire: the f49 breath at every release, never
  the f93 one; rolls f98.3-113.3 at DEX 99 against the held cast's f173.3). FP `Magic.mp`, taken as
  exact as a tap's. **INFERRED**: a bullet made before the release flies its life whatever the
  caster plays next (Dragonfire's f49 breath, Bullet 10704000, 1.5 s, free flight). The hold spells
  without the window (Meteorite, Meteorite of Astel, Spira, Divine Bird Feathers, Surge, O Flame)
  reach 045011 only at 045010's end, after every cast the 'cast' scores, so they get none.
- **A charged skill's early release.** With no opening hit before its window the release is read
  as above, a lookalike of its hold, the mean of its release frames ('mixture'); it was scored by
  `FollowupForm`, whose defender knew the release from the attack's start. With an opening hit
  before it (Black Flame Tornado, Jori's Inquisition, Moore's Charge, Shield Crash, Thundercloud
  Form, Wall of Sparks) it stays `FollowupForm`'s ('followup'): there the first burst is the
  opening's, the same for every member, so the group's plan changes nothing, and `FollowupForm`
  keeps what the members' mean would drop, the attacker timing the release on the roll he saw. Its
  release frames are still members of its hold's group either way.
- **A follow-up's press is not given this treatment.** `FollowupModel`'s attacker already chooses
  the press from what he saw (a later frame only on a roll that showed before the window), which is
  why its policy never nets less than the press at once (selftest 30), and a frame drawn unseen
  would replace that choice, not add to it. Its defender plans the follow-up after his roll of the
  opening, from a floor that differs per roll start, so a group plan over the press frames would
  have to be solved per distance, family and post-roll floor inside `_late`, where one planned dodge
  now serves every delay.
- **Unchanged, by test.** A member alone, a window of one real frame and a release with no window
  give exactly the version-8 numbers (the spells selftest on Comet Azur's tap, released at 045010's
  end; the ash model's on the Glintstone Dart and Revenger's Blade read at their first frame alone,
  and on a `VISIBLE_M` of 0, which is the switch frame; this selftest's checks 17 and 32).

What it moves (**MEASURED** 2026-11-11: `compute` on these 14 names, version 8 (the dnok-hit-records
code, `target/rel/table_old.log`) against version 9 with the visible delay off (`VISIBLE_M` 0, the
switch frame) and at 10 cm (`target/rel/vis_0.log`, `vis_10.log`, `vis_smoke.py`), components and
objective lines left out; mean net per situation weight, HP, at the distance basis and at the
corpus's Intelligence line {str 16, dex 18, int 65, fth 9, arc 9} or, for an incantation, its Faith
line {str 19, dex 18, int 9, fth 60, arc 7}; '-' where the line cannot use it or the variant is new;
'releases' is the version-9 member count; 'shows' the 10 cm delay at the window's first frame; CPU
the 'attacks' stage's per item, the two runs side by side, 2 workers each on 4 CPUs under the same
load, load average about 11; the follow-ups of these skills, Flame Spear on the Short Spear and
Carian Grandeur on the Carian Knight's Sword move as their rows here):

| attack | carrier | releases | shows | basis v8 -> no delay -> 10 cm | line v8 -> no delay -> 10 cm | CPU s no delay / 10 cm |
| --- | --- | --- | --- | --- | --- | --- |
| Glintstone Cometshard tap | Glintstone Staff | 20 | 4 | 39.0 -> 38.5 -> 227.5 | INT 39.4 -> 39.4 -> 184.3 | 90.6 / 190.2 |
| Glintstone Cometshard charged | Glintstone Staff | 1 | - | 0.0 -> 0.0 -> 0.0 | INT 0.0 -> 0.0 -> 0.0 | 111.9 / 80.6 |
| Comet tap | Glintstone Staff | 20 | 4 | 128.8 -> 124.4 -> 437.4 | INT 116.2 -> 115.8 -> 398.7 | 347.4 / 359.7 |
| Comet charged | Glintstone Staff | 1 | - | 76.2 -> 117.3 -> 217.4 | INT 80.1 -> 94.0 -> 185.4 | 271.4 / 421.9 |
| Glintstone Dart hold | Glintstone Kris | 1 | - | 0.0 -> 0.0 -> 0.0 | INT 0.0 -> 0.0 -> 0.0 | 40.0 / 23.1 |
| Glintstone Dart early release | Glintstone Kris | 13 mixture | 3 | 2.2 -> 0.5 -> 9.6 | INT 2.1 -> 0.5 -> 9.1 | 30.2 / 32.4 |
| Dragonfire cast (held) | Finger Seal | 1 | - | -132.3 -> -132.3 -> -132.3 | - | 11.8 / 7.4 |
| Dragonfire early (new) | Finger Seal | 18 | 8 | - -> -182.0 -> -182.0 | - | 51.3 / 54.9 |
| Ekzykes's Decay cast (held) | Finger Seal | 1 | - | -321.2 -> -321.2 -> -321.2 | - | 6.1 / 6.1 |
| Ekzykes's Decay early (new) | Finger Seal | 22 | 4 | - -> 0.0 -> 0.0 | - | 6.0 / 5.9 |
| Flame Spear hold | Dagger | 1 | - | 210.9 -> -46.1 -> -46.1 | INT 87.2 -> -46.1 -> -46.1 | 48.2 / 49.7 |
| Flame Spear early release | Dagger | 22 mixture | 2 | 1.8 -> 137.2 -> 137.3 | INT 1.1 -> 70.6 -> 70.6 | 84.4 / 75.5 |
| Flame Spear hold | Bastard Sword (040200) | 1 | - | 339.9 -> -49.7 -> -49.7 | INT 185.2 -> -49.7 -> -49.7 | 50.8 / 51.3 |
| Flame Spear early release | Bastard Sword (040200) | 22 mixture | 2 | 3.0 -> 199.6 -> 199.6 | INT 1.9 -> 113.8 -> 113.8 | 92.4 / 82.1 |
| Revenger's Blade hold | Falx | 1 | - | 307.1 -> -31.6 -> -31.6 | INT 156.8 -> -31.6 -> -31.6 | 13.2 / 12.4 |
| Revenger's Blade early release | Falx | 22 mixture | 2 | 0.1 -> 242.6 -> 242.6 | INT 0.1 -> 115.3 -> 115.3 | 22.3 / 32.4 |
| Wrath of Gold charged | Finger Seal | 1 | - | 989.2 -> 247.1 -> 247.1 | FTH 812.8 -> 336.8 -> 336.8 | 5.5 / 3.1 |
| Wrath of Gold tap | Finger Seal | 10 | 4 | 250.6 -> 824.3 -> 824.3 | FTH 174.9 -> 479.8 -> 479.8 | 5.1 / 4.9 |
| Rock Blaster tap | Glintstone Staff | 36 | 5 | 705.1 -> 113.6 -> 113.6 | INT 570.4 -> 85.9 -> 85.9 | 106.4 / 66.9 |
| Rock Blaster charged | Glintstone Staff | 1 | - | 96.7 -> 96.7 -> 96.7 | INT 72.1 -> 72.1 -> 72.1 | 1.3 / 0.9 |
| Divine Beast Frost Stomp early release | Club | 15 mixture | 2 | 6.0 -> 155.8 -> 161.1 | INT 4.7 -> 124.0 -> 128.0 | 121.4 / 84.4 |
| Divine Beast Frost Stomp hold | Club | 1 | - | 143.2 -> 4.4 -> 5.8 | INT 85.1 -> 1.5 -> 2.6 | 45.3 / 42.2 |
| Carian Grandeur early release | Dagger | 12 mixture | 3 | -23.7 -> 14.1 -> 231.6 | INT -23.7 -> 13.0 -> 220.5 | 8.4 / 5.2 |
| Carian Grandeur hold | Dagger | 1 | - | -180.2 -> -117.7 -> -85.3 | INT -180.2 -> -117.7 -> -85.3 | 8.0 / 5.0 |
| Swift Slash early release | Reverse-Bladed Sword | 10 mixture | 2 | 412.4 -> 481.5 -> 481.5 | INT 308.6 -> 359.8 -> 359.8 | 43.7 / 41.2 |
| Swift Slash hold | Reverse-Bladed Sword | 1 | - | 640.4 -> 560.0 -> 560.0 | INT 481.9 -> 422.3 -> 422.3 | 20.8 / 12.3 |
| Crystal Barrage early (new) | Glintstone Staff | 53 | 12 | - -> 63.5 -> 63.5 | INT - -> -27.0 -> -27.0 | 1,069.3 / 1,044.1 |
| Crystal Barrage cast (held) | Glintstone Staff | 1 | - | 195.4 -> 184.7 -> 184.7 | INT 53.9 -> 44.5 -> 44.5 | 486.0 / 472.3 |
| Lightning Spear tap | Finger Seal | 10 | 3 | 10.4 -> 16.0 -> 22.0 | FTH 2.2 -> 11.1 -> 15.4 | 62.1 / 31.2 |

The 'attacks' stage's 35 items take 3,493 CPU s with the delay off and 3,475 at 10 cm: the delay
itself is read once per release frame in phase 1 (inputs 11.5 -> 13.0 CPU s in the workers,
lookalike inputs 1.4 -> 5.9 s). Per item, the items the delay cannot touch (the Dart's and
Revenger's Blade's R2 follow-ups, which have no group) move x1.05 and x0.62 between the two runs,
which is the load's share; Glintstone Cometshard's tap (91 -> 190 s, x2.1) and Comet's charge (271 -> 422 s,
x1.55) move more, **INFERRED** because their releases' nets now differ more from frame to frame, so
`release_read` reads more of them (not counted), and Lightning Spear's tap (62 -> 31 s) and the Frost
Stomp's release (121 -> 84 s) less.

What moves them:

- A charged skill's hold loses its cover. Spread over the window, the release members weigh a
  share each against the hold's 1, so the hold is the heaviest member the defender plans for, and
  once the last release frame has passed he knows it is the hold. Flame Spear's and Revenger's
  Blade's holds fall to a net loss; their releases, which a defender used to know from the start
  (`FollowupForm`), now land where a single timing cannot cover a release that may come on any of
  22 frames.
- With the delay off, a tap whose release frame barely matters stays put: Glintstone Cometshard's
  and Comet's taps move by under 5 HP, because the defender reacts to each release as it comes
  (`events`).
- The visible delay moves the releases whose single fast shot he escapes only on a narrow run of
  timings, by making him react that many frames later (Glintstone Cometshard's tap, traced above:
  38.5 -> 227.5; Comet's tap 124.4 -> 437.4, and its charge 117.3 -> 217.4 with it, **INFERRED** the
  same group's plan decided later; the Glintstone Dart's release 0.5 -> 9.6; Lightning Spear's tap
  16.0 -> 22.0), and, not traced, Carian Grandeur's release on the Dagger 14.1 -> 231.6 and its
  hold -117.7 -> -85.3, Divine Beast Frost Stomp's release 155.8 -> 161.1 and its hold 4.4 -> 5.8.
  Revenger's Blade, Flame Spear, Wrath of Gold, Rock Blaster, Swift Slash
  and the 'early' variants do not move by more than 0.1 HP; why per situation is not traced.
- A tap whose later releases differ from its earliest can fall: Rock Blaster's window is f10-46
  (36 releases), and a release late in it casts more of 045010's rocks and leaves the caster
  rolling up to 35 frames later; the mean of its releases nets 113.6 against the earliest
  release's 705.1 (which of the two moves it is not split out). Six strata scored at their first
  frames had it at 198.1, which is the grid error the quadrature table shows.
- Who the plan protects can swap: Wrath of Gold's charge and tap trade places, read here as the
  defender's best timing against the group now escaping the charge and being caught by the taps,
  the plan choosing on escape alone (section 2b; **INFERRED** from the two nets, not traced per
  situation).
- The 'early' variants are new: Dragonfire let go early lands its f49 breath only and nets less
  than holding it (-182.0 against -132.3), Crystal Barrage let go early nets 63.5 at the basis,
  Ekzykes's Decay let go early 0.0.

Cost (**MEASURED** 2026-11-11, this selftest's subset; timing-log run 10, version 9 as above,
against run 6, the dnok-hit-records selftest of version 8, and run 7, version 9's first rule
(strata of 5 frames, at most six, the release at its switch frame); 3 workers on 4 CPUs each.
Run 10 ran under load 7.5 to 9.7 against run 6's 3.7, so the load is read off the items version 9
leaves alone: the 111 items over 0.5 CPU s that are no release's and no release spell's or skill's
(`target/rel/timing_items.py`) take x0.98 of run 6's CPU in run 10, median, quartiles x0.63 and
x1.31, and x1.06 in run 7). Over the 193 items of both runs the 'attacks' stage goes 1,437 ->
5,826 CPU s, x4.13 with the load taken out (run 7: x1.48), and Gelmir's Fury 'early', in run 10
alone, takes 336 s more; the whole run 1,682 -> 6,488 CPU s. The release items (50: every variant
of a spell with a tap or an early release, a charged skill's hold and early release) take 881 ->
5,135 s, the others 556 -> 691. The dearest: Star Shower's tap 64 -> 614 s, Radagon's Rings of
Light's charge 66 -> 480 and tap 82 -> 457, Magic Glintblade's tap 46 -> 355, Gravitational
Missile's tap 27 -> 338, Roiling Magma's tap 38 -> 247, Great Oracular Bubble's tap 28 -> 243,
Gelmir's Fury's held cast 22 -> 229 (its group now holds the 81 early releases). A version-8
selftest from another session ran beside run 10 (run 9, load 9.8 to 12.4) and gives the same
picture: 1,759 -> 5,826 CPU s over the same 193 items, x3.53 with its load taken out (control
median x0.94). So the converged rule costs about four times version 8's 'attacks' stage, and
about 2.8 times version 9's first rule (5,941 against 2,132 load-corrected s over the same items):
the group now holds every release frame (Glintstone Cometshard's tap and charge are 21 members,
each read for its first escaping starts at every distance), and the release is read where it moves on top.
Where it went, per item (the 16 dearest additions, run 6 -> run 10 CPU s; members as the release's
own / its group's under the adaptive rule and a midpoint grid of 5; frames the adaptive rule read;
mean net at the distance basis, `target/rel/rel_rule.py`):

| item | CPU s | adaptive own / group | midpoint 5 own / group | read | net adaptive | net midpoint 5 | moved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Star Shower tap | 64 -> 614 | 10 / 22 | 2 / 6 | 5 | 1,429.5 | 1,428.8 | +0.8 |
| Radagon's Rings of Light charged | 66 -> 480 | 1 / 17 | 1 / 5 | 1 | 66.5 | 66.5 | 0.0 |
| Radagon's Rings of Light tap | 82 -> 457 | 16 / 17 | 4 / 5 | 15 | 588.0 | 586.4 | +1.6 |
| Gelmir's Fury early (new) | - -> 336 | 81 / 82 | 17 / 18 | 17 | 330.0 | 330.0 | 0.0 |
| Gravitational Missile tap | 27 -> 338 | 13 / 14 | 3 / 4 | 4 | 734.3 | 734.3 | 0.0 |
| Magic Glintblade tap | 46 -> 355 | 10 / 11 | 2 / 3 | 3 | 0.0 | 0.0 | 0.0 |
| Great Oracular Bubble tap | 28 -> 243 | 32 / 33 | 7 / 8 | 11 | 48.7 | 48.9 | -0.1 |
| Roiling Magma tap | 38 -> 247 | 12 / 13 | 3 / 4 | 11 | 119.3 | 152.1 | -32.8 |
| Gelmir's Fury cast | 22 -> 229 | 1 / 82 | 1 / 18 | 1 | 330.0 | 330.0 | 0.0 |
| Star Shower charged | 94 -> 292 | 1 / 22 | 1 / 6 | 1 | 1,425.0 | 1,425.0 | 0.0 |
| Gravity Well tap | 18 -> 187 | 13 / 14 | 3 / 4 | 12 | 94.3 | 92.9 | +1.4 |
| Magic Glintblade charged | 68 -> 218 | 1 / 11 | 1 / 3 | 1 | 0.0 | 0.0 | 0.0 |
| Collapsing Stars tap | 19 -> 153 | 13 / 14 | 3 / 4 | 7 | 730.6 | 761.4 | -30.7 |
| Collapsing Stars charged | 11 -> 110 | 1 / 14 | 1 / 4 | 1 | 68.1 | 68.1 | 0.0 |
| Gravity Well charged | 12 -> 109 | 1 / 14 | 1 / 4 | 1 | 0.0 | 0.0 | 0.0 |
| Gravitational Missile charged | 18 -> 109 | 1 / 14 | 1 / 4 | 1 | 1,042.0 | 1,042.0 | 0.0 |

The charges and held casts cost as much as the taps although they have one member each: they are
read against the whole group. All but two moved by under 2 HP for 3 to 6 times the CPU, which is
why the node rule replaced the adaptive one (above).

With the node rule ('once') and one job per group (**MEASURED** 2026-11-11, timing-log run 12, the
selftest of record, against run 6; 3 workers on 4 CPUs each, loads 5.4 -> 2.3 and 3.7 -> 4.2, so
both near idle, and the raw ratio is the one to read: the skill items in no group take x0.65 of
run 6's CPU, which is the exact cuts above reaching every item, not load): the 'attacks' stage
1,438 -> 3,020 CPU s (x2.10), the whole run 1,682 -> 3,266 (x1.94); over the 193 items of both runs
1,437 -> 2,957 (x2.06), of which the release items (50) 881 -> 2,455 and the others 556 -> 502,
and Gelmir's Fury 'early' adds 63 s. (Run 11, the halving version of the nodes, was x2.37, and
run 10, the adaptive rule, x4.29 raw.) The dearest now: Radagon's Rings of Light's tap 82 -> 407
s, Star Shower's 64 -> 401, Great Oracular Bubble's 28 -> 247, Gravity Well's 18 -> 168, Roiling
Magma's 38 -> 157, Collapsing Stars' 19 -> 126. The rule stays at 1 HP: at 3 HP it saved 3% at the
basis for a worst miss of 2.8 HP instead of 0.2, short of the 5% that x2 needs. What the x2.1 buys: the release items' nets
within 0.2 HP of every frame read on the 20 entities measured, where the cheapest grid, a plain
midpoint grid of 5, misses 7 of the 15 validation rows by up to 40 HP, both holds among them
(Flame Spear's -5.9 against -46.1), because its members never reach the window's ends.

### 2d. The defender's strike (version 11)

Until version 11 the defender answered an attack only by rolling (or jumping a jump-immune
burst), and struck only after a roll that evaded it. So a free defender 1.5 m from a 55-frame cast
never hit the caster first, and every slow cast landed as if he could only dodge it. r16
(`attack-similarity-140-160-6018f71a37aae4e2`, version 10) shows what that did: Stars of Ruin's
charged cast netted 776 to 877 at the Intelligence line with 3 distinct values over the 1,208
situations, Fortissax's Light Spear 1236.5 in every one at the Faith line, so the best spell
beat every other in every situation and a second spell added no coverage (**MEASURED**, r16's
`b3_net` and `b2_net`).

Now `er-mechanics-spells.net_parts` gives him a strike as an alternative to the roll wherever he
is free before the attack first touches him (that module's doc, "The kit", and the comment above
its `STRIKE_CARRIED`; ashes-of-war.md section 16a). In short: he presses no sooner than he could
roll (`max(reaction delay, floor)`, cue 0), runs to `PUNISH_REACH_M` and lands the pool's R1 #1 its
strike frames later; it lands when that comes before the attack first touches him and before the
attacker's roll gate. `er-mechanics-interrupt.simulate` on that one hit, through the TAE 795
window open then (`TOUGHNESS` on every geometry: a spell's cast clips, a skill's hit clips), says
whether the attacker's poise breaks; broken, what has not left him is lost, and what has left
lands on the defender standing. He takes whichever of the strike and the roll leaves the attacker
less, a tie to the roll, per (distance, floor), as he takes the roll or the jump.

Here, the forms that price every build and objective line (`OutcomeForm`, `FollowupForm`) read it
through `_StrikeForm`: the runs of strikes that land per key (`strike_segments`, no worth, no
poise) once per form, then per build its worth and its poise. The attacker's poise is each line's
own (`line_poise`): an attacker build's set ('rule' 'fp_plan' 'menu_poise', line-fit's armor for
a fitted line), an adjusted objective line its attacker line's, and the RL window's median build
(62 at RL 150, `SpellModel.defender`, **INFERRED**) for the distance basis and a minimum line,
which have no armor. A follow-up is struck at its earliest press: a defender who does not roll
shows the attacker nothing to delay for (`FollowupModel`). `geo_fingerprint` reads the windows,
and the homing batch flies the run's end, 3.69 m, with the situations' distances.

Checks (**MEASURED** 2026-11-13, the worktree's code): `OutcomeForm.net` equals `net_parts`' net
to 0.0 HP in every situation on Stars of Ruin (both variants), Glintstone Pebble, Rock Sling (with
its free aims), Fortissax's Light Spear (a window), at the attacker's poise 62, 89 and 20;
the spells selftest checks a struck and an unstruck Stars of Ruin cast and Fortissax's window.

What it moves (**MEASURED** 2026-11-13): `net_parts` (`net_parts_aims`, free aims included) called
directly at r16's Intelligence and Faith lines (INT or FTH 80, STR 15, DEX 11, Glintstone Staff +25
/ Finger Seal +25), the 1,208 situations, the RL 150 pool's strikes, the defender who knows the
attack (no lookalike group, before and after alike), a release netting its members' weighted mean
(`release_members`). "Before" is the same code with the strikes handed in as plain frames (the
roll alone); it is not r16, whose stored nets came from older code (Fortissax's 1236.5 there,
1643.4 here, its line's full worth). "After" reads the line's own poise, 98 (r16's 'menu_poise'
for both lines); the 62 column is the default poise. Mean over the situation weights; distinct =
distinct float32 nets; strike = the weight on which the defender strikes in the option chosen.

| attack | line | before: mean, distinct | after: mean, distinct, strike | min .. max after | after at poise 62 |
|---|---|---|---|---|---|
| Stars of Ruin, charged | Int | 876.1, 3 | -205.5, 191, 0.985 | -327.7 .. 877.0 | -225.2 |
| Stars of Ruin, tap (10 releases) | Int | 569.1, 3 | -101.1, 372, 0.937 | -348.8 .. 569.3 | -110.9 |
| Glintstone Pebble | Int | 0.0, 2 | -2.8, 4, 0.128 | -22.4 .. 40.6 | -3.4 |
| Rock Sling (partial) | Int | 114.6, 1 | -211.1, 104, 0.939 | -268.1 .. 40.1 | -211.4 |
| Glintstone Nails, tap (10 releases) | Int | 538.0, 1 | -137.3, 435, 0.957 | -351.0 .. 538.0 | -146.6 |
| Glintstone Nails, charged | Int | 667.8, 1 | -263.3, 149, 0.979 | -342.1 .. 667.8 | -278.3 |
| Fortissax's Light Spear | Fth | 1643.4, 1 | 227.6, 257, 0.941 | -260.4 .. 821.7 | 85.4 |
| Flame of the Fell God | Fth | 1788.5, 1 | -200.5, 120, 0.998 | -257.3 .. 894.2 | -244.1 |

Every cast that netted one number almost everywhere now nets hundreds, from below -300 (struck
before it leaves the hand, the R1 paid) to its whole worth (pressed where his run or his recovery
makes the strike late). The strike lands before Stars of Ruin's stars leave (f54.9 charged, f40.9
tap) wherever his reaction or his floor plus his run and the R1's frames come first; Rock Sling's
rocks leave at f25.8 but reach a defender only at f57.8 or later and its roll gate is f78.8, so he
trades the one rock that catches him standing (160.5) for the R1 nearly everywhere and the cast
nets 34.4 at most where the strike is late (9 m) and he still rolls. Fortissax's window (f15-96 of
045010: +3.3 poise, half the poise taken) holds against the weaker R1s at 98 poise, not at 62
(227.6 against 85.4). Glintstone Pebble, fast and rolled before, is struck on 12.8% of the
weight.

The strike does not by itself make a second spell pay (**MEASURED**, the same call, the
Intelligence line, poise 98, four casts with no release window: Stars of Ruin charged, Glintstone
Pebble, Rock Sling, Glintstone Nails charged). Before, Stars of Ruin's charged cast nets most in
every situation and adding any of the other three to it gains 0.0. After, none of the four nets
above 0 on 86.3% of the weight; on the other 13.6% Stars of Ruin's charged cast still nets most,
and adding any other of the four to it still gains 0.0 (its kit value, the mean of the best above
0, falls from 876.1 to 68.0). The fast cast is not what the strike spares: Glintstone Pebble nets
at most 40.6 before and after (a roll on reaction escapes it), and where Stars of Ruin's charged
cast nets 0 or less, so do the other three.

Nothing that reads these nets counts the interrupt a second time (**VERIFIED** reading,
2026-11-13). line-fit's D multiplies by `u`, the share of casts its pool does not break in their
windup (`INT.reverse` on `GameWorld.cast_seq`), but its offense is `hit_out`, one connect
(`SpellModel.evaluate` `one_hit`; `score`, `kit_hit`), not these nets. The objective rows pay a
net by `paid_share`, which runs line-fit's `score` with `u` = 1 (`fp_plan_paid`: "Every cast
spends its FP: the similarity nets price whether it lands"). `er-mechanics-pouch` prices
`punish_values` with no interrupt factor. The two read different events: `u` a pool attack
started inside a contested cast's windup, the strike a defender free after his own whiff. A
consumer that multiplied these nets by `u` would count a struck cast twice.

His R1 is taken free on the frame his roll is, the whiff's dodge cancel. Over the RL 150 pool's
spaced whiffs, weighted as `punish_situations` weighs them, the whiff's R1 cancel (`cancel_frame`
'r1') opens at or before its dodge cancel on 85.1% of the weight, a mean 2.3 frames sooner (the
floor from the end of the last hit is 11.8 frames to the R1 cancel against 14.1 to the dodge
cancel), and up to 12 frames later on the rest (**MEASURED** 2026-11-13, the merged tree's
`punish_situations` loop). So the dodge cancel leaves the strike a little late on most of the
weight, not early. A press at the R1 cancel plays the string's next R1, not the R1 #1 whose
frames the strike uses; that is not modelled.

### 2e. The attacker's own guard (version 12)

The user, 2026-11-13: "Do you place any value on being able to block and attack simultaneously?"
(ground truth: "Thrusting shields can block and attack at the same time"). Until version 12 no
model did: the defender's strike (2d) took every attacker to be open.

An attack clip can hold TAE JumpTable 3, the guard flag a raised shield sets, over part of the
attack (powerstance-guard.md section 3a has every clip, how each is reached and what a guarded hit
does; **TAE** and **VERIFIED** c0000.hks there). `er-mechanics-spells` reads those windows onto
every geometry (`GUARD`; `SpellModel.skill_guard` for a skill), and a strike that lands inside one
meets the guard (`strike_guard`, the comment above `STRIKE_CARRIED`): the attacker pays the
guarded chip of the R1 (`PUNISH_HP` times the share of the R1's raw damage the guard passes,
`guard_block` on `er-mechanics-powerstance-guard.block_hit`), not `PUNISH_HP`; the R1 breaks no
poise; the attack ends only where the guard reaction ends it (every skill state goes through
`ArtsCommonFunction`, which plays one) or the guard breaks (chip and one more R1). `strike_breaks`
carries the guard as its last row and `strike_shares` the HP it spares as its third part, so
`_StrikeForm` reads it as `net_parts` does, and `geo_fingerprint` reads `GUARD`. An attack with no
guard nets exactly what it did.

Checks (**MEASURED** 2026-11-13, the worktree's code): `OutcomeForm.net` equals `net_parts`' net to
3e-13 HP in every situation on Shield Bash, Shield Crash, Roaring Bash and Revenge of the Night,
at the default poise and at 89; with the guard taken off the geometries, `net_parts` gives the
version 11 code's (`.worktrees/sim-interrupt`) mean to 1e-4 on Shield Bash, Shield Crash, Roaring
Bash, Revenge of the Night and Flame Spit at two lines. The spells selftest checks the windows of
the two-handed guard poke, the thrusting shield R1 #1 and the spear's guard poke, and the three
outcomes of a guarded strike on Stars of Ruin's charged cast (goes on, ended, guard broken).

What it moves (**MEASURED** 2026-11-13, `er-mechanics-spells.attack_scores`: the RL 150 pool's
1,208 spaced whiffs, the median defender, the pool's strikes, weight-mean net per whiff; "before"
is the same call with `GUARD` taken off). Lines: quality STR 40 DEX 40, strength STR 66 DEX 16,
int STR 24 DEX 20 INT 60, faith STR 24 DEX 14 FTH 60 (10 in the rest). Struck = the weight on
which the defender strikes.

| attack (carrier) | guard (judge, real frames) | quality | strength | int | faith | struck, quality |
|---|---|---|---|---|---|---|
| Shield Bash (Smoldering Shield) | 460 f7.5-46.1 | -1.8 -> 0.1 | -0.2 -> 0.1 | -3.2 -> 0.0 | 0.0 -> 0.1 | 16% -> 0% |
| Shield Crash (Dragon Towershield) | 460 f20.8-99.2 | -162.0 -> 29.3 | -153.1 -> 35.9 | -202.6 -> -0.6 | -202.6 -> -0.6 | 73% -> 73% |
| Roaring Bash (Golden Lion Shield) | 460 f7.5-46.1 | 55.5 -> 74.5 | 88.3 -> 91.2 | 29.0 -> 61.3 | 29.0 -> 61.3 | 100% -> 0% |
| Revenge of the Night (Shield of Night) | 460 f24-43 | -16.9 -> -2.3 | -22.4 -> -5.8 | -21.2 -> -5.0 | -29.7 -> -10.3 | 13% -> 9% |
| Flame Spit (One-Eyed Shield) | 461 f52-56 | -334.7 -> -331.1 | the same | the same | the same | 42% -> 36% |
| Tongues of Fire (Visage Shield) | 461 f38-73 | -121.0, unchanged | | | | 41% |
| Golden Retaliation (Erdtree Greatshield) | 470 f5-36 | 137.9, unchanged (380.1 faith) | | | | 0% |
| Carian Thrusting Shield R1 #1 / 2H R1 #1 | 461 f14-45 / 471 f14-55 | 86.4 / 51.2, unchanged | 83.3 / 48.5 | 75.0 / 44.0 | 64.3 / 38.1 | 8% / 6% |
| Carian Thrusting Shield 2H R1 from guard | 470 f0-42, goes on | 21.2, unchanged | 20.1 | 18.2 | 15.7 | 0% |
| Dueling Shield R1 #1 / 2H R1 from guard | 461 / 470 | 89.2 / 21.6, unchanged | 81.5 / 19.5 | 69.0 / 16.6 | 64.4 / 15.6 | 8% / 0% |
| Short Spear / Estoc / Lance / Great Epee R1 from guard (Kite Shield) | 460 f0-65 / 60 / 60 / 37, goes on | 56.2 / 117.3 / 15.9 / 66.8, unchanged | | | | 0-4% |

Moore's Charge on the Verdigris Greatshield has no damaging hit in `skill_attack` and is not
scored.

What the table says. The guard pays where it covers a windup that a strike can reach: Shield Crash
(guard from f20.8 of its press, its five hits from clip f41) stops costing the R1 on 73% of the
weight and nets 190 more; Roaring Bash and Shield Bash (guard from f7.5, the hit from clip f34)
stop being struck at all. A thrusting shield gains nothing here: its R1 guards from f14, after its
f13 thrust touches, and no pool R1 lands that soon (the pool's strike frames start at 10.5, plus
the reaction's 8.5 frames at the quickest), so every "struck" situation of its R1 is one where the
strike lands nothing and the defender stands. Its guard poke and the spears' guard their windup,
but no strike lands before their f12-16 thrusts either. Tongues of Fire's guard opens with its
breath (f38), and a strike must land before the breath first touches him, so before the guard.
Where a thrusting shield's guard pays is the exchange, where both swing at once: exchange.md
section 2b.

The roll's punish (`reaction_outcome`) does not read the guard. It barely could: the punish that
follows a roll lands before the attacker's roll gate almost nowhere on these attacks (**MEASURED**
2026-11-13, the same call, quality line, the weight on which the defender rolls): 0.0 HP per whiff
on the thrusting shield R1 #1 and 2H R1 #1 (roll gate f27), its guard poke, the Short Spear's,
Shield Bash and Revenge of the Night; 0.15 HP on Shield Crash and 1.16 HP on Roaring Bash. So the
most reading it there could add is about 1 HP per whiff.

### 2f. What the cast costs its own user (version 13)

User, 2026-11-14: "The model should price HP cost of spells/ashes." Until version 12 no net did:
`profile_arrays`' 'net' was `net_parts`' HP dealt less HP taken, and the only read of
`changeHpRate` / `changeHpPoint` was `speffect_tokens`' on-hit 'hp drain' token, a row landing on
the defender.

Every entity now carries 'hp_cost' (`hp_cost_of`): a skill's from its opening's TimeAct SpEffects
and a follow-up's or release's clip, plus the `spEffectIDForShooter` of each bullet those clips
launch (`er-mechanics-self-damage.skill_cost`); a spell's from each fired ref's
`spEffectIDForShooter` or SpEffect (`spell_cost`). Phase 2 takes `pct` percent of the line's max HP
plus `flat` off every net of the entity (`_profile_work_shared`, `hp_charge`), after the form check.
The game takes it at a TimeAct event or a launch, hit or not (`VERIFIED` 2.7.2.0, ashes-of-war.md
section 19a), so the same amount comes off every situation and no land share moves. Max HP
(`line_hp`): an attacker build's FP plan 'hp' (line-fit's, with its HP piece), an objective line's
from its attacker build or its VIG, and for the distance basis the RL window's median defender's
(`INFERRED`: the basis has no VIG of its own). Bloody Slash pays 5% of max HP + 50 a cast, Briars of
Sin 80 a launch; the status half of a cost (Briars' bleed 30, the frenzy incantations' madness) is
listed in 'unpriced', since no line carries its own resistances (ashes-of-war.md section 19c). A
release member pays its variant's own fired refs (`INFERRED`), and a component pays nothing: the
whole entity pays once.


## 3. The distances and their properties

The profile of an attack is u = w_s x (land_now, pun_now, land, pun) over the cells (situation,
outcome). Its **land mass** is the weighted mean land share (pressing at once, with the best
option, and the larger of the two); its **mass** is the weighted mean of all four shares.

- **D_rel**(a, b) = sum |u_a - u_b| / sum max(u_a, u_b), 0 between two empty profiles: the share of
  the two profiles' combined outcome they do not share. It is the weighted Ruzicka (Soergel)
  distance, a metric on non-negative vectors. Any profile is 1 from an empty one, and a profile is
  0.5 from half itself.
- **D_land**: D_rel over the land channels alone (`land_now`, `land`). A punish chance two attacks
  share cannot make them alike when they land in different situations: land 0.1 on disjoint
  halves with a shared punish chance of 0.95 is D_rel 0.095 but D_land 1 (selftest).
- **D_now**(a, b) = sum_s w_s (|land_now_a - land_now_b| + |pun_now_a - pun_now_b|) / 2W, the
  absolute gap pressing at once; **D_best** the same on the best option. Both are at most the sum
  of the two masses, so any two attacks that land little are close whatever they are. Round 1 used
  D_now for every association and failed on exactly that.
- **D_idle**(a, b) = sum_s w_s (0.5 |base_now_a / full_a - base_now_b / full_b| + 0.5 c_s) / W, c_s
  0 when neither touches an idle defender at s, 1 when one does, else dt / (dt + F) for first
  touches dt frames apart, F = (0.25 + 2 x 0.05) s x 30 = 10.5 frames (**INFERRED**: the smallest
  gap a defender can act on). 'Lands alike on a defender who does not dodge.' A pseudometric: the
  timing term is a concave transform of |dt|. D_idle is at most the weight share where either
  attack touches an idle defender, so it vetoes little between two short-reach attacks: 0.014 of
  the weight is at 3 m or closer, 0.199 at 4 m, 0.341 at 4.5 m (**MEASURED**).
- **D_assoc**(a, b) = max(D_rel, D_land, `--idle-rate` x D_idle), rate 0.5: the distance every
  association reads. A max of (pseudo)metrics is a pseudometric; the selftest checks the triangle
  inequality on every triple of 30 random profiles. At `--twin` 0.1 a pair must be within D_idle
  0.2.
- **D_contain**(c, b) = max(D_land, `--idle-rate` x D_idle), between one frame of an attack and a
  whole attack. It reads what lands only: a frame inside a longer attack is punished on that
  attack's recovery, not on its own, so its punish chance is not the frame's.
- **The delivery gate**: two items associate only when they share a delivery (melee hits,
  bullets) and carry the same displacement tokens (`DISPLACE_TOKENS`: pull, push, gravity, grab; a
  component carries its whole attack's, **INFERRED**). The outcome model prices damage only, so
  without that half the 2026-11-10 full run matched the Gravity Well tap, which pulls, with the
  Glintblade Phalanx ash, which does not (D_assoc 0.049, the one association of 61 that crossed a
  displacement). Status is not gated: 30 of those 61 crossed a status, nearly all one ash on
  carriers whose own status differs, and the neighbour rows show it. The gate never enters a matrix; the twins, matches, contains, component twins and
  families read D_assoc (or D_contain) with every pair the gate keeps apart at 1. A neighbour row
  marks a pair the gate keeps apart and shows the two deliveries with the tokens they do not share.

The rate 0.5 and the cut 0.1 were read off the same pairs the selftest asserts (**MEASURED**, round
2): the pairs that fire the same bullets sit at D_idle 0.061 (the Zamor storm frame and the tap) to
0.138 (the Glintblade Phalanx spell and ash); the melee skills D_rel alone called twins of
projectile spells sit at 0.229 to 0.99. No pair was held out of that fit, so the controls below
confirm the fit, not the rule.

D_rel alone failed the round-2 negative control, and D_assoc alone the round-3 one (**MEASURED**,
the full runs and this selftest): their run-in shares happened to agree.

| skill (melee in the model) | spell | D_rel | D_idle | D_assoc or D_contain | kept apart by |
| --- | --- | --- | --- | --- | --- |
| Roaring Bash [Golden Lion Shield] | Glintblade Phalanx | 0.0821 | 0.4986 | 0.2493 | D_assoc, delivery |
| Sword Dance [Hefty Scimitar] | Radagon's Rings of Light (charged) | 0.0649 | 0.5152 | 0.2576 | D_assoc, delivery |
| Spinning Guillotine [Putrescence Cleaver], R2 follow-up | Triple Rings of Light | 0.0856 | 0.4316 | 0.2158 | D_assoc, delivery |
| Red Bear Hunt [Red Bear's Claw] | Magic Glintblade (tap) | 0.0186 | 0.9860 | 0.4930 | D_assoc, delivery |
| Red Bear Hunt [Red Bear's Claw] | Magic Glintblade (charged) | 0.0186 | 0.9865 | 0.4932 | D_assoc, delivery |
| Painful Strike [Tooth Whip], its f34 frame | Carian Phalanx | | 0.1117 | 0.0559 (D_contain) | delivery only |

All six land at least `--live` of their worth on both sides, so the control is not one that passes
by construction; the selftest asserts no twin, match or contains between any of them, and that
every twin, match and contains of the subset shares a delivery. The numbers move with the models
they are read from: in the round-5 selftest, after that day's edits to er-mechanics-ashes and
er-mechanics-spells (which edit moved it is not traced), Sword Dance / Radagon's Rings of Light
(charged) is D_rel 0.1518, over `--twin`, so that control no longer bites on D_rel alone, and
Spinning Guillotine / Triple Rings of Light is 0.0917; the other three are as above. The selftest
now asks that at least four of the five whole-entity controls bite. Painful Strike's f34 hit is melee
only (atk 600000601, no bullet) and in round 3 'contained' the glintblade volley at D_contain
0.056, tighter than the true Glintblade Phalanx twin on both the land and the idle term: no
threshold on these distances can separate the two, so the gate does. Without it, at the default
cut that frame would be a component twin of Carian Phalanx (the whole worths are 1.39x apart).

The melee control's margin (**MEASURED**, full run): the nearest pair the gate keeps apart is
Thundercloud Form [Dragon King's Cragblade] and Bayle's Tyranny at D_assoc 0.1145 (D_idle 0.229),
0.0145 above `--twin`; the next are Dynastic Sickleplay [Obsidian Lamina] and Bestial Sling
(0.138) and Thundercloud Form with Bloodboon Ritual [Mohgwyn's Sacred Spear] (0.138). Within each
cut the gate keeps apart 0 pairs at 0.05 and 0.1, 22 at 0.2 and 130 at 0.3 (the sensitivity table
in section 5). On D_assoc alone, any `--twin` above 0.114 would let a melee-only skill be a twin
of a spell.

The selftest's synthetic checks have closed forms that depend on the situation weights (a cell on
a heavy situation against one on a light one), a cell-by-cell loop that D_rel must equal, a D_now
case with a punish difference only (0.2), a D_idle case at 3F (0.375, where a capped ramp would
give 0.5), a UPGMA case with unequal cluster sizes (0, 1, 2, 10: UPGMA joins {0, 1, 2} to 10 at
9.0, WPGMA at 8.75), and weighted dominance cases (below). Mutants each fail it (**MEASURED**):
D_rel with unit weights, D_rel with the weights on the wrong cells (`np.repeat` for `np.tile`),
D_idle and D_now with unit weights, WPGMA for UPGMA, and dominance with unit weights (both new
closed forms fail on it; round 3's selftest passed it, 106 of 106).

**Groups**, by land mass, never by a punish chance:

- **live**: pressing at once lands at least `--null` (0.02) of its worth.
- **runin_only**: only the best option, which here means a run-in, does. The Glintblade Phalanx
  spell is one: it lands 0.0071 of its worth pressing at once and 0.2143 with a run-in.
- **nets_nothing**: neither does; 'never reaches' when an idle defender is touched on less than
  0.02 of the weight, else 'always evaded'.

**Association floor**: an entity takes part in twins, matches, contains, families and the yardstick
when it is scored and its land mass is at least `--live` (0.05): 212 entities in the full run (72
spell variants, 140 skill entities).

## 4. Matrices

`D_assoc`, `D_rel`, `D_land`, `D_now`, `D_best` and `D_idle` over every profiled whole entity
(scored and partial), float32, symmetric, zero diagonal, in the cache `.npz` with `idx_spell`,
`idx_skill` and `idx_partial`: the spell x spell block is `D[idx_spell][:, idx_spell]` and so on.
The matrices are the plain distances; the delivery gate is applied when an association reads them.
The selftest checks the matrices as computed, before the stored copy is averaged with its
transpose and clipped (asymmetry and diagonal exactly 0, range within [0, 1] to a float32 step),
and that the stored D_assoc is the max of the stored three. The page draws D_assoc over the
entities above the association floor in UPGMA leaf order, and D_idle over every scored entity:
the floor entities first, in that order, then the rest in key order. The order is under each
picture.

## 5. Associations

- **Twins**: D_assoc <= `--twin` (0.1), both above the association floor, full worth within a
  factor 2, not variants of one parent, sharing a delivery. **Component twins**: one frame of X
  within `--twin` of a whole Y of the other kind by D_contain, both above the floor, the whole of X
  within a factor 2 of Y, the frame and Y sharing a delivery.
- **Cross-type matches**: mutual nearest across kinds by D_assoc among the entities above the
  floor, within `--twin`, behind the gate; and every 'contains' whose two whole worths are within
  a factor 2.
- **Contains**: Y's nearest item of X's kind by D_contain (every scored whole and every component
  of that kind that shares a delivery with Y) is a component of X, strictly nearer than every
  whole; Y is that component's nearest whole of Y's kind; D_contain within `--twin`; both above
  the floor.
- **Families**: UPGMA (average linkage, Lance-Williams) over the entities above the floor by
  D_assoc behind the gate, cut at `--cut` (default `--twin`). A family whose members are all
  variants of one attack is marked 'one parent' (siblings, which no pair list joins), one that is
  one skill on several carriers 'one skill'.
- **Neighbours**: by D_assoc for an attack that lands; by D_idle for one that is always evaded,
  among the attacks that reach an idle defender (where neither touches, D_idle counts the
  situation as alike, so an attack that never reaches sat near any that rarely does: in round 2,
  72 of 1,800 such rows were attacks that never reach). Between two attacks that both land
  nothing, D_assoc, D_rel and D_land are shown as '-': D_assoc falls to the idle term alone there
  and would read like a twin (Gravitas against Blood Blade [Reverse-Bladed Sword, Occult], D_rel 0,
  D_assoc 0.069 in round 3).
- **Effect tokens** annotate and never enter a distance (the outcome model cannot see them):
  status (on the gate `scripts/interactions/regdata.py` states), HP drain, defense down, pull /
  push / gravity (SpEffect stateInfo 398 by the sign of the applying AtkParam's `knockbackDist`),
  homing, unblockable, grab, jumpable. An attack excluded for having no damaging hit still has its
  tokens read, on every node and AtkParam it fires, and is listed in its families as an exclusion:
  Poison Mist ('status:poison', 'hp drain'), Fia's Mist ('status:death'), Freezing Mist
  ('status:frost', 'defense down', 'hp drain') and Deadly Poison Spray ('status:poison', 'hp
  drain') (**MEASURED**, probe). Lifesteal Fist is excluded as 'grab not modelled (throwTypeId
  20)', flagged 'grab': its hit is AtkParam 300000025 in clip 040000, which the hit reading never
  reaches from its 040090 opening. A member's nearest other-kind member is by D_assoc among the
  landing members, or by D_idle (among those that reach) when it is always evaded or none of the
  other kind lands.
- **Weapon-borne status** (**VERIFIED**, a round-5 trace on the 1.16.2 dump, extending
  status.md section 1): hit SpEffect slots 5..7 are the weapon's `spEffectBehaviorId0..2` +
  `ReinforceParamWeapon.spEffectId1..3` (weapon at AttackInfo +0xe8), filled unless AttackInfo
  +0xf4 `attackNotFromWeapon` is set (`FUN_140d24b10` reads its copy at `0x140d24f76`) or the
  AtkParam has `disableHitSpEffect`. For a melee hit `FUN_14068ffa0` sets +0xf4 to `(flags & 0x68)
  == 0 && hitSourceType != 0` (`0x1406900a5`..`0x1406900cb`); TAE event 1 passes flags 1, so
  `hitSourceType` alone decides, and event 307 passes its Args+4, whose flag 8 forces 'from the
  weapon' (Hoarfrost Stomp, Storm Stomp, Divine Beast Frost Stomp). A skill's TAE bullet never sets
  it (only a bullet with a magic or goods id does), so a skill bullet takes the weapon's status
  too; a spell's never does. A weapon slot's build-up is the row's amount x
  `statusAilmentAtkPowerCorrectRate` / 100 (`CalculateDamage2`, `0x140448d1a`), so a hit at rate
  0 builds nothing. In the regulation (**REGULATION**): 1,903 skill melee rows, `disableHitSpEffect`
  0 on all of them, `hitSourceType` 0 on 1,873, of which 1,616 have a rate above 0; 6 of the 30
  others are fired by event 307 with flag 8. The tool therefore adds the carrier's own status to a
  skill's tokens when a scored melee hit is from the weapon (the hit row's `from_weapon`, which
  `er-mechanics-ashes` computes by the same rule) or a scored hit is a TAE-fired root bullet, at a
  rate above 0 (`weapon_status_tokens`, record `weapon_status`): Bloodhound's Finesse on
  Bloodhound's Fang is 'status:bleed' (rows 300804900/901, rate 100, the Fang's 6402). Whether a
  bullet's children keep the root's 'from the weapon' is not traced, so they add nothing.

Full run (**MEASURED**): 59 twin pairs, 51 whole and 8 through a component; 12 cross-type; 8 spell
with spell and 39 skill with skill, 24 of those one skill on two carrier openings. 6 cross-type
matches (4 whole, 2 through a component); 4 contains; 23 families of two or more (the largest 6; 4
mix spells and skills; 2 are one parent, 9 one skill on several carriers). Yardstick: the median
D_assoc between a spell's tap and its charged cast, both above the floor, is 0.508 over 21 spells.

The families against `Magic.subCategory1`, over the 55 labelled spells above the floor
(**MEASURED**): they fall in 50 clusters, 47 of them singletons, against 18 subcategories. The 3
families with two or more spells hold 8 spell members, 6 of them in their family's commonest
subcategory (purity 0.75; 0.96 counting the singletons, which are pure by definition), and 6 of
the 8 spell pairs inside a family share a subcategory, against 0.061 of all spell pairs. The
adjusted Rand index is 0.112, held down by the singletons, which it counts as pairs the families
fail to join: a pure refinement of the subcategories with these family sizes scores about 0.15
(**MEASURED**, round 3). Where the families do form, they agree with the game's categories; most
spells simply have no family at the default cut.

| D_assoc cut | whole twin pairs | mutual nearest | families of 2+ | largest family | kept apart by delivery |
| --- | --- | --- | --- | --- | --- |
| 0.05 | 30 | 1 | 16 | 4 | 0 |
| 0.1 (default) | 51 | 4 | 23 | 6 | 0 |
| 0.2 | 236 | 11 | 31 | 14 | 22 |
| 0.3 | 560 | 18 | 34 | 18 | 130 |

Cross-type matches ('by' is whole, or the frame of the skill through which it contains the spell;
no scale band on the whole matches, so the full ratio is shown):

| D | by | spell | skill | D_assoc | D_idle | skill full / spell full |
| --- | --- | --- | --- | --- | --- | --- |
| 0.030 | f45 | Zamor Ice Storm (tap) | Zamor Ice Storm [Zamor Curved Sword] | 0.459 | 0.092 | 1.06 |
| 0.036 | whole | Collapsing Stars (tap) | Tongues of Fire [Visage Shield] | 0.036 | 0.072 | 0.31 |
| 0.054 | whole | Shard Spiral (charged) | Oracular Bubble [Envoy's Horn] | 0.054 | 0.107 | 0.59 |
| 0.066 | whole | Swarm of Flies | Sorcery of the Crozier [Watchdog's Staff] | 0.066 | 0.131 | 1.80 |
| 0.069 | whole | Glintblade Phalanx | Glintblade Phalanx [Dagger, Magic] | 0.069 | 0.138 | 1.23 |
| 0.071 | f49 | Cherishing Fingers (charged) | Horn Calling: Storm [Horned Warrior's Greatsword] | 0.535 | 0.251 | 1.22 |

Cross-type twins (12):

| skill | spell | by | D |
| --- | --- | --- | --- |
| Zamor Ice Storm [Zamor Curved Sword] | Zamor Ice Storm (tap) | f45 | 0.030 |
| Zamor Ice Storm [Zamor Curved Sword] | Cherishing Fingers (tap) | f45 | 0.057 |
| Zamor Ice Storm [Zamor Curved Sword] | Cherishing Fingers (charged) | f45 | 0.085 |
| Ordovis's Vortex [Ordovis's Greatsword] | Zamor Ice Storm (tap) | f72 | 0.058 |
| Ordovis's Vortex [Ordovis's Greatsword] | Cherishing Fingers (charged) | f72 | 0.075 |
| Horn Calling: Storm [Horned Warrior's Greatsword] | Cherishing Fingers (charged) | f49 | 0.071 |
| Oracular Bubble [Envoy's Horn] | Shard Spiral (charged) | whole | 0.054 |
| Sorcery of the Crozier [Watchdog's Staff] | Swarm of Flies | whole | 0.066 |
| Glintblade Phalanx [Dagger, Magic] | Glintblade Phalanx | whole | 0.069 |
| Glintblade Phalanx [Ritual Thrusting Shield, Magic] | Glintblade Phalanx | whole | 0.069 |
| Glintblade Phalanx [Dagger, Magic], R2 040010 at f45 | Glintblade Phalanx | f33 | 0.069 |
| Glintblade Phalanx [Ritual Thrusting Shield, Magic], R2 045910 at f45 | Glintblade Phalanx | f33 | 0.069 |

Collapsing Stars and Tongues of Fire are a match but not twins: the skill is worth 0.31 of the
spell, outside the factor-2 band. Only Glintblade Phalanx and Zamor Ice Storm share a name.

**What the four whole matches rest on** (**MEASURED**, round-4 full run, cache
`7821862ab710e5ec`): on every one the idle term binds D_assoc (0.5 x D_idle is above D_rel and
D_land), and both sides land one flat share wherever they land, nothing pressed at once: a quarter
on both sides for Collapsing Stars (tap) / Tongues of Fire (D_rel 0.006, D_land 0.005), Shard
Spiral (charged) / Oracular Bubble (0.037, 0.000) and Swarm of Flies / Sorcery of the Crozier
(0.041, 0.041), and 3/14 on both for Glintblade Phalanx and its ash (0.004, 0.000). A run-in share
is a sum over the roll's four directions and the timing points (a quarter is one direction of four
caught; 3/14 one direction at six of seven timing points), so two such attacks agree on the land
channels whenever they run in on the same situations, whatever they are. Such a match says the two
reach an idle defender within D_idle 0.2 of each other and land the same quantum in the same
situations; it does not say they are alike in any other way, and round 4's reading of the three
other-name matches as 'alike as punishes' was more than the numbers carry. 31 of the 51 whole twins
were idle-bound too, 7 of them with both sides flat. Every match and twin row now says which term
binds (`bound`) and whether it rests on D_idle alone (`rests_on_idle`: idle-bound and both sides
flat, `flat_land`); the summary and the page count them. Glintblade Phalanx is the one such pair
known to be the same attack (the same bullets); the others are unresolved until something other
than the run-in quantum separates them (flying homing, section 8, left the Phalanx pair's nets as
they were on the INT 65 line; a punish model with more than four roll directions might).

The storm frames form a small set of their own: the Zamor Curved Sword's f45 storm is a twin of
the Zamor tap and of both Cherishing Fingers casts, and Ordovis's Vortex's f72 frame of the Zamor
tap and the charged Cherishing Fingers. The Glintblade Phalanx R2 follow-up rows of the twin table
below are round 4's and run through the opening's f33 volley; this code gives a follow-up no
component before its press (section 1), so they are the opening's association, not their own.

Contains (one frame of the skill lands like the whole spell):

| X | contains Y | X's frame | D_contain | D_land | D_idle | Y full / X full |
| --- | --- | --- | --- | --- | --- | --- |
| Zamor Ice Storm [Zamor Curved Sword] | Zamor Ice Storm (tap) | f45 | 0.030 | 0.000 | 0.061 | 0.94 |
| Rolling Sparks [Firespark Perfume Bottle] | Divine Beast Tornado (tap) | f19 | 0.035 | 0.035 | 0.067 | 0.24 |
| Moon-and-Fire Stance [Rellana's Twin Blades], stance R2 040070 | Fire's Deadly Sin (charged) | f69 | 0.035 | 0.000 | 0.070 | 0.42 |
| Horn Calling: Storm [Horned Warrior's Greatsword] | Cherishing Fingers (charged) | f49 | 0.071 | 0.064 | 0.142 | 0.82 |

Round 3 listed a fifth, Painful Strike's f34 whip frame containing Carian Phalanx; the delivery
gate removes it (section 3).

Families of two or more at the default cut, the largest: Blood Tax and Repeating Thrust on the
same three carriers (6); Stamp (Sweep) [Large Club] with its two R2 follow-ups and the Flaming
Strike [Club, Heavy] R2 follow-up (4); Storm Assault on four carriers (4, one skill); the charged
breaths Dragonfire, Dragonice, Rotten Breath and Glintstone Breath (4, every pair at D_assoc
0.000); Stamp (Sweep) [Club] with its two R2 follow-ups (3, one parent); Glintblade Phalanx spell
and ash on both openings (3, mixed); the taps of Glintstone Nails, Shard Spiral and Scouring Black
Flame (3). The four mixed families are the four whole cross-type matches above.

The named pairs, **MEASURED** ('~': read on a mass under `--null`, greyed on the page). Of the 9
names a spell and a skill share, only these 4 have both sides profiled: the Carian Greatsword
spell (direct AtkParam), the Carian Retaliation and Thops's Barrier spells and skills (no damaging
hit), the Darkness and Golden Vow spells (no damaging hit) and the Golden Vow ash (blacklisted)
are left out.

| pair | D_assoc | D_rel | D_idle | rank of the other by the first's measure | twin | groups |
| --- | --- | --- | --- | --- | --- | --- |
| Glintblade Phalanx [Dagger, Magic] / spell | 0.069 | 0.004 | 0.138 | 1 (D_assoc) | yes | runin_only / runin_only |
| Zamor Ice Storm [Zamor Curved Sword] / tap | 0.459 | 0.459 | 0.092 | 3 (D_assoc) | yes, through f45 | live / live |
| Zamor Ice Storm [Zamor Curved Sword] / charged | 0.661 | 0.661 | 0.678 | 45 (D_assoc) | | live / live |
| Glintstone Pebble [Dagger, Magic] / spell | 0.504~ | 0.504 | 0.076 | 2 (D_idle) | | nets_nothing / nets_nothing |
| Great Oracular Bubble [Envoy's Greathorn] / tap and charged | 1.000~ | 1.000 | 0.001 | - | | nets_nothing / nets_nothing (never reaches) |

- **Glintblade Phalanx** spell and ash: twins and a cross-type match. Pressing at once both land
  0.0071 of their worth; with a run-in both land 0.2143, they share 1.000 of the smaller profile,
  and on an idle defender they are 0.138 apart (the ash's blades first touch about 4 frames later).
  The ash's R2 follow-ups are twins of the spell through their f33 volley.
- **Zamor Ice Storm**: the Zamor Curved Sword skill and the tap are twins through the storm frame,
  a cross-type match through it, and the skill contains the tap. The skill is a melee plunge at f24
  (atk 303401604, 203.3 HP, which lands nothing on its own) and then the storm at f45, the same
  bullets as the tap's (3055/3057 against 10441000/10441002, 3.0 m), 771.6 HP. The storm frame
  lands exactly what the tap lands in every situation (D_land 0.000, D_now 0.0051) and touches an
  idle defender 7 frames later wherever the tap does (D_idle 0.061). It differs from the tap only
  in the punish chance (D_rel 0.150): it comes 7 frames later inside the skill and rides the
  skill's recovery. The whole skill is D_assoc 0.459 from the tap: with a run-in it lands 0.481 of
  its worth against the tap's 0.250, because the plunge adds worth the storm alone does not, and
  the run-in share is one number per attack (section 2). The two whole worths are 1.06x apart, so
  the frame that matches is the whole of the difference: that is the component twin. The skill
  dominates the tap (FP 15 against 17), full_only: the skill carries 'full count'.
- **Glintstone Pebble** spell and ash: associated only by D_idle rank. The spell's profile lies
  inside the ash's (0.995 of it shared), but the ash lands twice the share (0.0179 against
  0.0090), so D_rel is 0.504, and both are under the association floor. On an idle defender they
  are 0.076 apart, each the other's second nearest of its kind. Both bullets home
  (`Bullet.homingAngle`; the spell's 10400000 at 35 deg/s). Homing is now flown (section 8) and
  leaves the spell where it was on the INT 65 line (0.1 mean net before and after): at 13 to 25
  m/s the shot is on him before that turn can follow a roll, so the pair stays unresolved.
- **Gravitas** has no spell twin under the outcome model and no survey in docs/, scripts/ or data/
  names one (the mentions are ashes-of-war.md, chainsaw/fallback-test-plan.md and code comments).
  It is associated with the pull spells by its token only. It nets nothing (land mass 0, reach
  0.977: it touches an idle defender and every defender here dodges it; its pull bullets come after
  the longest recovery, 41 frames). In the pull family its nearest spell is the Gravity Well tap by
  D_idle, 0.303 to 0.337 over its three carriers; its nearest spell overall is The Flame of Frenzy
  (tap), 0.164 to 0.191, which pulls nothing.

The pull family (**MEASURED**): 11 entities. Every skill in it nets nothing: Gravitas on three
carriers and Alabaster Lords' Pull land no share of their worth, and Starcaller Cry is punished
more than it lands (mean net -87.8). Onyx Lords' Repulsion, the one 'push', nets nothing too. The
spells that land are the Gravitational Missile tap and charged (live, mean net 670 and 990), the
Collapsing Stars tap and charged and the Gravity Well tap (runin_only); Gravity Well charged nets
nothing. Since no skill of the family lands, every member's nearest other-kind member is by
D_idle; the closest pair is Starcaller Cry and the Gravity Well tap (0.066).

## 6. Dominance

**Build-dependent: read per attacker build** (section 1), never on the distance basis. Round 4
read it on every damage stat 99, a build no character at RL 150 has, where every attack is
wieldable and castable and deals its most: the Zamor Ice Storm spell beat the ash at 99 INT, and a
16 INT build cannot cast it. Distances divide by each attack's own worth, so they mostly do not
depend on the build; dominance compares absolute nets and FP, so it holds only for the build it is
read at.

Per build, the population is the attacks it can use (section 1) that are scored and cost FP, each
with its own carrier, affinity, cast speed and nets per situation. The nets come from
`OutcomeForm`: within one burst a dodging defender's outcome is linear in the worth of each
geometry, and his evasion and punish chances do not read the worth (`er-mechanics-ashes.
reaction_outcome`), so one pass over an attack's geometry gives `net_parts`' net for any worth.
A build that flies the distance basis's geometry (a skill on the same carrier) costs no second
flight; a spell cast at the build's DEX, or a skill on another carrier, gets its own pass. Every run
checks the form against `net_parts` wherever a build flies the distance basis's geometry (within
1e-3 HP + 1e-5 of the net, the float32 the profile is stored in; a disagreement stops the run), and
the selftest checks it in float64 to 1e-6 HP on a spell, a two-frame follow-up, a jumpable spell
(two move families) and the subset's skill of most frames, at the scored worth and at a worth
changed frame by frame. The twin marks on a build's edges are the distance basis's twins. FP-0
attacks (Torch Attack, the kicks) are out of every population and each build names them
(`no_fp`). A population holds only what its attacker line can use; section 6a prices every scored
attack at its own lines and leaves the populations, and so dominance, as they are.

Within a build, a dominates b when all hold (the nets are the build's paid ones, section 1):

- a costs no more FP (spell over spell: no more memory slots);
- both net more than 1 HP on more than the tolerance (1 / n_eff = 0.00214) of the weight; an
  attack that does not is on the 'nets nothing' list and is neither dominated nor a dominator;
- a loses to b by more than 1 HP on at most the tolerance (a weighted share);
- a's weighted mean gain over b is at least -1 HP;
- a is strictly better: a mean gain above 1 HP, less FP, or fewer slots with a mean gain of at
  least 0.

Equal FP, equal slots and a mean difference within 1 HP both ways is 'equivalent', not dominance.
An edge is **robust** when the same test passes on one hit per event, the dominator's FP is exact
(not a held loop spell, not an opening scored on another clip) and the dominator carries no 'full
count' warning (live longer than 60 frames, or channelled); else **full_only**, with the reasons
on the edge (`full_only_why`). Siblings (variants of one parent) are compared only in the
own-variant table; a dominator of b is maximal when no other dominator of b dominates it,
own-variant edges included. The robust dominators are maximal among themselves by the robust
edges alone and listed first: round 4 took maximality over every edge, so a full_only dominator
could hide each robust one and 14 robustly redundant attacks showed only full_only dominators
(Eochaid's Dancing Blade, Bayle's Tyranny, Destined Death, Devonia's Vortex, Bloodboon Ritual
among them), which round 4's first-dominator counts left out. Every run checks each build's edges
make no cycle (`find_cycle`).

The slot condition closes a cycle round 3 left open (**MEASURED**, synthetic): a spell strictly
better on slots alone could be up to 1 HP worse in every situation, so a chain of such steps closed
through a cross-type edge made every member redundant and the frontier empty. The mean gain is
additive around a cycle and FP cannot fall around one, so with gain-strict edges above 1 HP and
slot-strict ones at 0 or more no cycle is left. The full run had none either way.

The weighted checks (selftest, closed forms): a loss of 50 HP on a situation weighing 0.1 of 99.1
(0.001 of the weight, under the tolerance) still dominates, where counting situations would give
0.01 and reject it; a gain of -0.9 on a situation weighing 50 against +1.5 on 50 of weight 1 is a
mean gain of 0.3, not strict, where counting situations would give 1.45 and make an edge. Round 3
measured that unit weights in the dominance test change 1,181 edges on real profiles.

Why 'full count' decides the class (**VERIFIED**, `_bursts` in er-mechanics-spells.py): the
geometry that catches first was credited with its own frame's hits and every later frame's. One hit
per event only replaces each frame's sum with that frame's largest hit, so an area that ticks once
per frame has net_one equal to net and passes the one-hit test unchanged (round 2: 1,853 of 9,096
robust edges had a 'full count' dominator).

**The caught defender and the health cap (2026-11-10, bd er-effects-rs-doax / er-effects-rs-n7dd).**
The pre-change full run had robust-frontier mean nets above an RL 150 health bar: Magma Breath
3,387 (Hybrid) and 2,461 (none), Ghostflame Ignition [Death's Poker] then R1 2,074
(Intelligence), Bear Witness! [Grafted Dragon] 1,643 (none). A defender caught by the first hit
took every later hit and every tick of a lingering area, and never walked out. Now every scored
attack's geometries carry a `CAUGHT` record (each hit list's tick count, re-hit interval, poise and
AtkParam, and its share of the geometry's worth), and `net_parts` and `OutcomeForm` price a catch
with `er-mechanics-ashes.CaughtModel`: the hit reactions hold and push him as section 15a measures
them, then he rolls or runs back out, and each later hit and tick needs its own geometry to touch
him on that path (ashes-of-war.md section 16). What one cast lands is capped at the defender's
health, CalcCorrectGraph 100 at the RL window's median Vigor (1900 HP at RL 150); the punish is not
capped. The fingerprint that shares one `OutcomeForm` across builds covers the `CAUGHT` lists'
shape and the launch times; the worth weights are each build's own (`OutcomeForm.net(value, geo)`).

**Launch points (2026-11-10).** A skill's or spell's root bullet now leaves the event's DummyPoly
where the clip has carried the caster (`er-mechanics-ashes.bullet_launch`, ashes-of-war.md
section 15), held back by the defender's body as the caster is; until then every root left 1 m
ahead of the caster's start. Swift Slash's four bursts leave 0.7-5.3 m out along the dash. Of the
283 multiplayer cast variants with a cast event, 124 have carried the caster 0.3 m or more forward
by one of their cast events (`MEASURED` release-clip root motion; Messmer's Orb charged 8.2 m,
Bayle's Flame Lightning 6.1 m, Furious Blade of Ansbach charged 6.0 m, Scarlet Aeonia 5.0 m), and a
cast's start clip adds its own travel up to the hand-off.

**Withdrawn**: round 4's dominance figures (467 entities with FP, 10,810 edges, 281 redundant, a
32-attack robust frontier led by Theodorix's Magma, a 40-spell rollup) were read on the distance
basis, a build no character at RL 150 has, so they hold for no real build and are not repeated
here. Its definitions above stand.

**Per attacker build, full run r5** (**MEASURED**, stamp `f3d7eafb8693154b`; 'casts' and
'wields' count the 213 spells and 370 skill openings each build can use):

| build | builds | line (str dex int fth arc) | RL | casts / wields | population | edges (robust) | redundant (robust) | robust frontier | commonest first robust dominator |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Strength | 327 | 60 18 10 12 7 | 143 | 22 / 229 | 220 | 3,153 (2,602) | 140 (137) | 18 | Savage Claws [Beast Claw, Heavy] x68 |
| Dexterity | 182 | 19 69 9 12 7 | 147 | 21 / 221 | 214 | 2,902 (2,379) | 134 (127) | 25 | Savage Claws [Beast Claw, Lightning] x64 |
| Faith | 152 | 19 18 9 60 7 | 143 | 92 / 233 | 289 | 4,033 (3,197) | 162 (154) | 23 | Sacred Ring of Light [Short Spear, Sacred] x106 |
| none (no stat archetype) | 114 | 24 36 10 22 12 | 136 | 52 / 266 | 270 | 4,075 (3,304) | 165 (155) | 27 | Sacred Ring of Light [Short Spear, Sacred] x95 |
| Intelligence | 111 | 16 18 65 9 9 | 150 | 62 / 218 | 268 | 4,032 (3,142) | 157 (152) | 22 | Glintblade Phalanx [Ritual Thrusting Shield, Magic] x77 |
| Arcane | 78 | 16 17 8 12 60 | 133 | 25 / 194 | 191 | 2,475 (2,039) | 121 (115) | 18 | Savage Claws [Beast Claw, Occult] x59 |
| Hybrid | 52 | 16 17 34 36 11 | 140 | 151 / 231 | 338 | 5,344 (4,179) | 184 (172) | 30 | Sacred Ring of Light [Short Spear, Sacred] x127 |
| Quality | 50 | 48 47 9 9 7 | 146 | 0 / 242 | 222 | 3,129 (2,587) | 135 (132) | 23 | Savage Claws [Beast Claw, Quality] x70 |

(The full lines, VIG MND END first: Strength 58 10 47, Dexterity 60 10 40, Faith 59 20 30, none
58 19 34, Intelligence 59 20 33, Arcane 59 11 29, Hybrid 57 23 25, Quality 58 10 37.) No build's
edges make a cycle. The robust frontier's highest mean nets per cast:

| build | first | second | third |
| --- | --- | --- | --- |
| Strength | Savage Claws [Beast Claw, Heavy] 587 | Sacred Ring of Light [Short Spear, Sacred] 532 | Repeating Thrust [Bastard Sword, Heavy] 481 |
| Dexterity | Thundercloud Form [Dragon King's Cragblade] 1,349 | its early release 754 | Savage Claws [Beast Claw, Lightning] 654 |
| Faith | Fortissax's Light Spear (cast) 1,094 | Sacred Ring of Light [Short Spear, Sacred] 749 | Savage Claws [Beast Claw, Sacred] 609 |
| none | Regal Beastclaw [Beastclaw Greathammer] 635 | Sacred Ring of Light [Short Spear, Sacred] 599 | Glintblade Phalanx [Ritual Thrusting Shield, Magic] 536 |
| Intelligence | Gravitational Missile (charged) 788 | Glintblade Phalanx [Ritual Thrusting Shield, Magic] 720 | Savage Claws [Beast Claw, Magic] 652 |
| Arcane | Savage Claws [Beast Claw, Occult] 577 | Sacred Ring of Light [Short Spear, Sacred] 532 | Repeating Thrust [Bastard Sword, Occult] 469 |
| Hybrid | Sacred Ring of Light [Short Spear, Sacred] 665 | Glintblade Phalanx [Ritual Thrusting Shield, Magic] 637 | Night-and-Flame Stance R2 [Sword of Night and Flame] 601 |
| Quality | Thundercloud Form [Dragon King's Cragblade] 1,259 | its early release 713 | Savage Claws [Beast Claw, Quality] 615 |

Before the placed launches and the caught defender every build's frontier was led by Swift Slash
[Reverse-Bladed Sword] held to the full charge (1,212 to 1,562). With the hit priority records
(section 8) the full charge's four bursts take slot 1's record (TAE a877_040000, event 341
f27-34) and its two blades slot 0's (f28-38), all at priority 0, so one burst and one blade land.
In r5 the full charge nets 315 to 406 and its f10 early release 241 to 309, and both are robustly
dominated in every build. The early release's first robust dominator is Savage Claws [Beast
Claw] in four builds, Sacred Ring of Light [Short Spear, Sacred] in three and Glintblade Phalanx
[Ritual Thrusting Shield, Magic] for Intelligence, at 9 to 14 FP against its 16. It is the only
attack to leave or join any build's robust frontier. The records move the mean net of 11 to 15
attacks per build by more than 1 HP, and two others of them sit on a robust frontier. Storm
Assault [Ritual Thrusting Shield] stays on every one, at 228 to 301: in a622_045900 its f54 bullet
(AtkParam 300000462, priority 1) shares the f52 slam's record (300000460, priority 0) and lands
nothing on a defender the slam has hit. Night-and-Flame Stance's R2 falls from first to third for
Hybrid, at 601: in a819_040070 its two f46 fire bullets (2220 and 2221, AtkParam 300214925 and 300214926, fire motion
value 800 each) take the f44-50 slot-0 record at priority 1, so one of them lands, beside the f44
swing 300214930, which takes no record (priority -1). The no-damage hitbox there (judge 3920,
priority 0) never exists: it is on attack index 0 with 3930, whose event group runs first, and
FUN_1404428f0 drops an event whose index is already claimed that frame (0x14044299d; FUN_140445d30
clears the claims each frame); its AtkParam_Pc 300214920 also has every hit radius 0, so
FUN_140d25390 would build it no shape (**VERIFIED** 1.16.2, bd er-effects-rs-xb3i). A hitbox that
does exist and touches does spend the record whatever it deals: FUN_140521bf0 tests no team and
no damage, and FUN_14051b370 sets the landed flag before the hit is applied. The largest falls, each to under half its r4 net, are on no frontier: Troll's
Roar (a743_040000: its f42 and f43 swings share one record at priority 0), Magma Guillotine
(a776_040000: its f45 swing and f49 bullet share one at priority 0) and Flame Skewer's R2
follow-up [Ritual Thrusting Shield] (a881_045910: its five f18-22 flame bullets of AtkParam
600000102 share one at priority 0, so one lands). Thundercloud Form's lead
rests on one hit: its f84 swing (AtkParam 303401200, motion value 256 on every element) after six
lightning ticks of 303401201. The records leave it as it was: a826_040000 has no event 341, so no
swing takes one. Not yet modelled, and it moves these figures: the vertical part of the lock-on
turn (bd er-effects-rs-1lr3, which leaves Magma Breath under-credited at 600 for Hybrid, fourth
there, under Night-and-Flame Stance's R2 by less than 1 HP).

The round-4 frontier table below was read on the distance basis and is kept only as that record
(**not a real build**). By mean net:

| attack | mean net | FP | warnings | also undominated over every edge |
| --- | --- | --- | --- | --- |
| Theodorix's Magma | 8860.3 | 45 | full count | yes |
| Magma Breath | 6462.2 | 30 | full count | yes |
| Surge of Faith [Cranial Vessel Candlestand] | 2788.9 | 15 | full count | yes |
| Placidusax's Ruin | 2555.1 | 62 | full count | |
| Bear Witness! [Grafted Dragon] | 2362.9 | 20 | full count | |
| Ghostflame Ignition [Death's Poker], R1 040010 at f43.2 | 2218.6 | 25 | full count | |
| Swift Slash [Reverse-Bladed Sword, Cold] | 2068.6 | 16 | | |
| Fortissax's Light Spear | 1769.7 | 35 | | |
| Thundercloud Form [Dragon King's Cragblade] | 1621.7 | 28 | | |
| Rosus's Summons [Rosus' Axe] | 1287.3 | 15 | | |
| Savage Claws [Beast Claw, Quality] | 1014.7 | 13 | | yes |
| Dynast's Finesse [Bloody Helice], R2 040010 at f26 | 820.3 | 12 | | yes |
| Dryleaf Whirlwind [Dryleaf Arts, Quality] | 807.9 | 9 | | yes |
| Eochaid's Dancing Blade [Marais Executioner's Sword] | 775.5 | 15 | | |
| Rolling Sparks [Firespark Perfume Bottle] | 737.1 | 14 | | yes |
| Spinning Wheel [Ghiza's Wheel] | 714.7 | 3 | | yes |
| Repeating Thrust [Bastard Sword, Poison] | 711.4 | 7 | | yes |
| Bloodboon | 708.1 | 13 | full count | yes |
| Repeating Thrust [Ritual Thrusting Shield, Quality] | 626.9 | 7 | | yes |
| Dynast's Finesse [Bloody Helice] | 410.6 | 5 | FP lower bound (follow-up clip) | yes |
| Golden Retaliation [Erdtree Greatshield] | 396.0 | 4 | | yes |
| Gelmir's Fury (charged) | 278.9 | 16 | full count, FP lower bound (loop) | |
| Stormcaller [Twinblade, Sacred] | 208.4 | 9 | full count | yes |
| Explosive Ghostflame | 200.9 | 29 | full count | |
| Rotten Breath (charged) | 181.6 | 36 | full count, FP lower bound (loop) | |
| Sword Dance [Reverse-Bladed Sword, Cold] | 131.2 | 6 | | yes |
| Surge, O Flame | 71.1 | 1 | full count, FP lower bound (loop) | yes |
| Firebreather [Steel-Wire Torch] | 53.7 | 8 | | |
| Flame Skewer [Ritual Thrusting Shield, Quality], R2 045910 at f33 | 51.2 | 26 | | |
| Collapsing Stars (charged) | 47.5 | 18 | | |
| Glintstone Pebble [Ritual Thrusting Shield, Magic], R2 045910 at f38 | 21.6 | 12 | | yes |
| Bloodhound's Finesse [Bloodhound's Fang], R2 040010 at f53 | 14.8 | 20 | | |

A full-count attack can still sit on a robust frontier: nothing robustly dominates it. Its
place there rests on its own over-credit, which the warning marks. Firebreather is new on it
against round 3's doc: round 2 had dropped it as unobtainable. Dynast's Finesse's opening (FP 5)
is there with an FP lower bound: it is scored on 040010, its own R2 follow-up's clip (section 1).
A low-net member such as the Glintstone Pebble follow-up is there because every attack at no more
FP that nets more on average still loses to it by more than 1 HP on more than the tolerance, not
because it is strong.

**Spell rollup** (per build): a spell is redundant when every variant of it, the ones with no hit
or no reach model included, is scored and robustly dominated from outside it. Round 4, on the
distance basis (**not a real build**), listed 40:
Glintstone Pebble, Great Glintstone Shard, Swift Glintstone Shard, Shard Spiral, Glintstone Arc,
Rock Blaster, Glintblade Phalanx, Carian Phalanx, Magic Glintblade, Glintstone Icecrag, Zamor Ice
Storm, Ambush Shard, Night Shard, Magma Shot, Catch Flame, Flame Sling, Whirl, O Flame, Scouring
Black Flame, Black Flame Ritual, Wrath of Gold, Night Maiden's Mist, Discus of Light, Triple Rings
of Light, Radagon's Rings of Light, Bestial Sling, Gurranq's Beast Claw, Greyoll's Roar, Swarm of
Flies, Unendurable Frenzy, Glintstone Nail, Glintstone Nails, Gravitational Missile, Vortex of
Putrescence, Aspects of the Crucible: Bloom, Bayle's Tyranny, Midra's Flame of Frenzy, Cherishing
Fingers, Golden Arcs, Giant Golden Arc and Divine Beast Tornado. When every scored variant that
nets something is robustly dominated but another variant is excluded, partial or nets nothing,
the spell goes on a separate list, 'dominated on its landing variants only', with those variants
named: 21 spells, among them Crystal Barrage, Meteorite of Astel, Dragonfire, Dragonice and
Glintstone Breath (tap: no damaging hit), Lightning Spear (charged: its damage is only in an
on-hit child), and Comet, Comet Azur, Night Comet, Gravity Well and Knight's Lightning Spear (one
variant nets nothing).

The loop-FP finding (**REGULATION**, J3): three of the six frontier members of the design probe sat
there on their per-tick FP (Crystal Barrage, Gelmir's Fury, Surge, O Flame). Taking `Magic.mp` and
marking the held variant's edges full_only is the correction; the real total per cast is still
unknown.

## 6a. Objective evaluation

Whether a corpus build happens to use an attack says nothing about what the attack is worth
against the game's other options (user, 2026-11-10: "Having real builds on Nyasu is irrelevant to
objectives"). So every scored and partial entity is also priced at two lines of its own, read off
no corpus build (`objective_lines`; JSON `entities[key].objective`, `--show`, page section 15):

- **adjusted**: start from the attacker build line that puts the most points where the attack
  scales (`line_fit`, the scaling-weighted mean of the line's damage stats; ties to the commonest
  line), fitted per entity: an opening, each follow-up and each early release has its own line. A
  skill entity weighs each stat by the share of its own hits' base attack that stat scales
  (`hit_stat_weights`, on its hits as the distance basis scored them; its job's first entity with
  weights when its own hits give none): per hit and element, the base attack before stat scaling (the weapon's upgraded base x
  MV / 100, plus the flat part x `baseAtkRate` when `isAddBaseAtk`, as `er-mechanics-ashes.
  hit_attack` builds it) times how much that element's multiplier rises when the stat goes from
  the weapon's requirement to 99, read by `er-mechanics-ar.element_multiplier` on the hit's own
  AttackElementCorrectParam row (the AtkParam's `overwriteAttackElementCorrectId` when set, else the
  weapon's `attackElementCorrectId`), over the total base attack. The carrier's correct* fields
  are read only when no hit gives a weight ('scaling_from'). Round 1 of this section fitted the
  correct* fields alone, which put Moonrithyll's Knight Sword (STR 45 DEX 45 INT 30) and Envoy's
  Greathorn on the Quality line; their skills' hits are a flat magic bullet on AECP 51080 (INT
  only) and flat holy bullets (FTH only), so they now fit the Intelligence and Faith lines. A
  spell weighs its school stat (INT for a sorcery, FTH for an incantation; the catalyst's own
  fields are not read, the Glintstone Staff's `correctStrength` 12 and the Finger Seal's 15 among
  them, **REGULATION**). Every damage stat under the attack's requirement (`attack_need`: the
  carrier's, one-handed, or the spell's and its catalyst's, on base stats) is raised to it, and as
  many points are paid back one at a time from the line's highest damage stat still above its own
  requirement and the lowest starting-class base, ties to the stat the attack weighs least
  (`adjust_line`). So the RL stays the line's, and a line that already meets the requirements is
  the line itself.
- **minimum**: the lowest-RL character that meets the requirements exactly: per starting class
  (`CharaInitParam`), each attribute at its base or its requirement, whichever is higher, and the
  class whose line has the lowest RL (`minimum_line`, 'from_class'). Each attribute's lowest base
  over every class would be a line no class has (RL -1 for the Zamor Curved Sword's STR 16 / DEX
  18, **MEASURED** on the first try).

Each line is priced as an attacker build prices its attacks (section 6): the job again at the
line's damage stats, once per distinct line among its entities, each entity kept from the run at
its own line (a skill on the group weapon `build_job` picks there, which is the distance
basis's carrier since both lines meet its requirements, at the affinity whose skill damage is
largest there; a spell at the line's DEX), its nets per situation from `OutcomeForm` (one form
shared with every build that flies the same geometry), and 'mean_net' their weighted mean. FP-0
and partial attacks are priced too. No objective line enters a population, so dominance is
unchanged. The fit weighs what the hits scale, not what lands; it is a choice, not a search for
the line at which each attack nets most.

**A 15-name sample** (**MEASURED**, 2026-11-10, this code, `--only` over the names below;
`/tmp/claude-1000/-home-banon-projects-er-mods-rs/5ec7b779-1950-45a6-84dd-ef5b2c3e9082/scratchpad/doax/reach-only.json`).
Lines are STR DEX INT FTH ARC; 'builds' counts the attacker builds whose population holds it.

| attack | distance basis | adjusted: mean net, line | minimum: mean net, line | builds |
| --- | --- | --- | --- | --- |
| Tremendous Phalanx [Moonrithyll's Knight Sword], R2 at f45 | 778.8 | 579.1, Quality 43 43 18 9 7 (INT +9, paid STR 5 DEX 4), RL 146 | 430.3, Astrologer 20 14 18 7 9, RL 22 | 0 |
| Tremendous Phalanx, the opening | 0.0 | 0.0 (never reaches), Intelligence 20 18 61 9 9 (STR +4, paid INT 4), RL 150 | 0.0 | 0 |
| Rancor Slash [Spirit Sword] (partial) | 55.9 | 37.1, Intelligence 16 18 65 9 9, RL 150 | 28.4, Astrologer 8 16 16 7 9, RL 10 | 0 |
| Rancor Slash [Spirit Glaive] (partial) | 36.8 | 26.0, Dexterity 19 62 16 12 7 (INT +7, paid DEX 7), RL 147 | 17.8, Astrologer 14 17 16 7 9, RL 17 | 0 |
| Great Oracular Bubble [Envoy's Greathorn] | -0.9 | -0.9, Faith 28 18 9 51 7 (STR +9, paid FTH 9), RL 143 | -0.9, Prophet 28 12 7 24 10, RL 34 | 0 |
| Jori's Inquisition [Barbed Staff-Spear], early release | 113.9 | 96.2, Faith 19 18 9 60 7, RL 143 | 61.4, Idus Knight 14 18 8 22 6, RL 22 | 2 |
| Jori's Inquisition, full charge | -217.4 | -226.6 | -244.8 | 2 |
| Zamor Ice Storm (charged) | 388.8 | 317.5, Intelligence, RL 150 | 182.3, Astrologer 8 12 36 7 9, RL 26 | 1 |
| Zamor Ice Storm [Zamor Curved Sword] | 66.9 | 66.9, Quality 48 47 9 9 7, RL 146 | 66.9, Idus Knight 16 18 8 11 6, RL 13 | 6 |
| Golden Arcs (tap) | 158.5 | 119.4, Intelligence 16 18 52 22 9 (FTH +13, paid INT 13), RL 150 | 24.2, Wretch 10 10 10 22 10, RL 13 | 2 |
| Glintblade Phalanx [Ritual Thrusting Shield, Magic] | 227.8 | 213.7, Intelligence, RL 150 | 131.9, Wretch 13 12 10 10 10, RL 6 | 8 |
| Glintblade Phalanx (cast) | 123.5 | 91.3, Intelligence, RL 150 | 22.1, Astrologer 8 12 22 7 9, RL 12 | 2 |
| Carian Phalanx (cast) | 73.7 | 51.3, Intelligence, RL 150 | 19.7, Astrologer 8 12 34 7 9, RL 24 | 2 |
| Gravity Well (tap) | 41.4 | 22.8, Intelligence, RL 150 | 0.0, Astrologer 8 12 17 7 9, RL 7 | 2 |
| Glintstone Dart [Glintstone Kris], R2 at f63 | 58.9 | 45.1, Intelligence 16 18 65 9 9, RL 150 | 34.8, Astrologer 8 12 16 7 9, RL 6 | 2 |

Greatblade Phalanx, Glintstone Cometshard, Rancorcall, Gravitas (every carrier) and Alabaster
Lords' Pull net between -5 and 0 at every line. With the hit-weighted fit (above), Tremendous
Phalanx, Glintstone Dart, Alabaster Lords' Pull and Gravitas on the Meteoric Ore Blade moved to
the Intelligence line and Great Oracular Bubble [Envoy's Greathorn] to Faith. Fitted on the
opening's hits alone, Tremendous Phalanx's R2 follow-up sat on the opening's Intelligence line and
netted 485.0 there; on its own hits (the opening's INT-only bullet plus a melee swing: STR 0.238,
DEX 0.272, INT 0.180) it fits the Quality line and nets 579.1. The Glintblade Phalanx and
Glintstone Dart follow-ups fit their openings' Intelligence line on their own hits too.

**Why the Zamor Curved Sword's skill nets 66.9 at every line** (**MEASURED**, 2026-11-10; the
selftest checks it). Its full worth falls from 974.9 (distance basis) to 923.6 (adjusted) and
865.2 (minimum), and all of the fall is its f24 slash (AtkParam 303401604: melee, MV 60 on the
weapon's physical base, the weapon's AECP 10000, which flags STR and DEX on physical), worth 203.3,
152.0 and 93.6. The f45 storm, two bullets (AtkParam 303401600 and 303401601, `isAddBaseAtk`, flat
magic 110, MV magic 100 on a weapon with no magic base), carries `overwriteAttackElementCorrectId`
50000, an AttackElementCorrectParam row with no `is*Correct` flag (**REGULATION**), so its
multiplier is 1.0 at any stats and each bullet's attack is 110 x `baseAtkRate` 4.0 (+10) = 440,
worth 771.6 at every line. Against a dodging defender the slash never lands: its geometry has no
dodge weight in any of the 1,208 situations, and tripling its worth moves no situation's net. So
what the skill nets is the storm's alone, and the same at every line. The model is consistent
with the params it reads (`er-mechanics-ashes.hit_attack`, **VERIFIED** against `0x1406845a0`);
that the game applies AECP 50000 this way is that function's reading, not a separate trace.

## 7. What is left out, by reason

Spell events without a flight, first match wins: 'direct AtkParam ref (refCategory 0)' (Carian
Slicer, Carian Greatsword), 'bullet tree unreadable', 'never reaches hurtbox height' (damaging
nodes fly but no point of them is at a height the idle hurtbox has a contact disc at: Rykard's
Rancor, Meteorite charged, Founding Rain of Stars, Magic Downpour), 'damage only in a bullet the
straight flight never launches' with what stops it ('launchConditionType 5': the damage is in an
on-hit child of a carrier that does no damage, and the straight flight hits no character; 'an
interval child never spawned'), 'other'. Height is checked first, so a variant whose damaging node
flies and never touches is labelled by height (Meteorite charged; round 3 called it 'never
launched' because an on-hit child under the meteor is not flown). Under an event that flies, a hit
on an unflown bullet is 'hit on a bullet the straight flight never launches (...)' (missing), or
dropped when its condition is water or never (section 1). Skills: 'no skill TimeAct', 'no opening
clip', 'bullet judge unresolved (ammo)' (bows, greatbows, ballistas), 'grab not modelled
(throwTypeId N)' (a clip AtkParam is a throw and no hit was priced), 'no damaging hit
(<classes>)', 'no roll frame', 'no hit with a reach model', 'model error: <repr>', and 'two-handed
opening, reach read one-handed' (**INFERRED** rule for greatbow and ballista carriers). That last
rule is checked after `skill_attack`'s own reasons and did not fire in the full run: those carriers
stop at 'bullet judge unresolved (ammo)' first.

Full run, per reason (**MEASURED**, 184 in all; no model error):

| kind | reason | n |
| --- | --- | --- |
| spell | no damaging hit | 58 |
| spell | no damaging hit (variant) | 19 |
| spell | direct AtkParam ref (refCategory 0) | 11 |
| spell | damage only in a bullet the straight flight never launches (launchConditionType 5) | 6 |
| spell | never reaches hurtbox height | 6 |
| spell | no spell goods row | 4 |
| spell | damage only in a bullet the straight flight never launches (an interval child never spawned) | 1 |
| skill | no damaging hit (buff) | 27 |
| skill | bullet judge unresolved (ammo) | 10 |
| skill | no damaging hit (parry) | 6 |
| skill | blacklisted | 5 |
| skill | no damaging hit (bullet (no damage)) | 5 |
| skill | no roll frame | 5 |
| skill | no damaging hit (no class) | 4 |
| skill | no damaging hit (bullet (no damage), parry) | 3 |
| skill | no damaging hit (i-frames) | 3 |
| skill | no hit with a reach model | 3 |
| skill | no skill TimeAct | 2 |
| skill | not a skill | 2 |
| skill | grab not modelled (throwTypeId 20) | 1 |
| skill | no damaging hit (buff, bullet (no damage)) | 1 |
| skill | no damaging hit (buff, melee, stance effect) | 1 |
| skill | no damaging hit (bullet (status only)) | 1 |

29 entities are partial: 21 spell variants (direct AtkParam events in 15, hurtbox height in 3, Rock
Sling's on-hit child, and Roiling Magma's two variants) and 8 skills (a melee hit with no posed
window, or a bullet with no flight).

## 8. Not established

- Homing is flown in the horizontal plane only (bd er-effects-rs-rw25; the rule is `VERIFIED`,
  ashes-of-war.md 'In-flight homing'): a geometry with a damaging shot that homes is read against
  each dodge with its shots flown again after the defender, a spell's shot aimed at where he is when
  it is created (2026-11-10: a roll made before the shot exists moves its aim, FUN_140390c20;
  before, each left on its stored straight heading) and a skill's on its launch heading. Not
  modelled: the turn's vertical part (decoded since 2026-11-12, the lock-on DummyPoly 220 plus
  `targetYOffsetRange`, and used by the spell record flight only, bd er-effects-rs-7bye), the lock-on aim
  following such a roll for a shot with no homing (not flown again), a caught defender's later hits
  (read on the straight flight), and the
  on-hit child a homing carrier creates where it reaches him (Great Oracular Bubble's damage is its
  child: aimed at him it still never reaches; free-aimed at the ground it does, next bullet).
  Measured for homing alone on the INT 65 line {str 16, dex 18, int 65, fth 9, arc 9} before and
  after: Rancorcall's tap 0.0 -> 10.1 mean net (its 60 and 120 deg/s
  skulls now find a standing defender at all), every other probe unchanged (Glintstone
  Cometshard tap 0.0 and charged -0.53, Glintstone Dart -0.6 / tap 2.08 / then R2 45.13, Glintstone
  Pebble 0.1, Gravity Well tap 22.79, Glintblade Phalanx 91.3 and its ash 142.63, Carian Phalanx
  51.28, Greatblade Phalanx and Great Oracular Bubble 0). Cometshard's 25 deg/s and the Dart's
  35 deg/s turn too little in flight at these distances to follow a roll: what clears them in the
  model is a roll timed through the shot, on reaction to a cast whose bolt reaches a defender 3 m
  away 41 real frames after the press (Cometshard's tap), not leaving its line. Since the aim at
  creation follows his roll (2026-11-10) the same holds and leaving the line early no longer
  clears a homing spell's shot: the kit's mean nets that move, and why both bolts still netted about
  0 (the defender, who read the cast from its first frame and knew when each shot reached him,
  not the flight), are ashes-of-war.md section 16a, 'When he rolls' and 'In-flight homing'; since
  version 8 he reads only what is on screen (section 2b). The full-run numbers above (r4-r7)
  predate it.
- A carrier meeting the ground makes its HitBulletID child there (`VERIFIED` 1.16.2: a map contact
  counts when `isPenetrateMap` is 0, FUN_140398f90 -> FUN_14039dcc0 -> FUN_14039bba0;
  ashes-of-war.md 'Map contact and free aim'), and a spell whose damage is only on that child
  (`er-mechanics-ashes.floor_bursts`: 45 spells) is also scored free-aimed at the flat ground 1 to
  8 m ahead (`FLOOR_AIM_M`; no lock-on, so no homing), per situation the better aim
  (`er-mechanics-spells.net_parts_aims`; a record's `aims_won` gives the share of the situation
  weight each aim took). Free aim is the user's game fact (Great Oracular Bubble, 2026-11-10); that
  every spell takes it and no weapon skill does, and that the caster puts the shot down exactly at
  the chosen point, are `INFERRED` (an upper bound on what aiming by hand reaches). INT 65 line,
  mean net aimed at him -> best aim (the aim taking most of the weight): Great Oracular Bubble tap
  0.0 -> 42.0 (floor 3 m, then 5 m) and charged 0.0 -> 36.6 (spread over 3 to 8 m); Blades of
  Stone 0.0 -> 81.1 / 527.5 (5 m / 8 m); Fleeting Microcosm 0.0 -> 195.4 / 524.2 (4 m / 3 m); Tibia's
  Summons 0.0 -> 148.6 (2 m); Spira 0.0 -> 116.6 (4 m); Magma Shot 1.0 -> 88.5 / 3.3 -> 110.5 (2 m /
  3 m); Flame of the Fell God 81.1 -> 93.9 (1 m in 0.56 of the weight); Gravitational Missile 536.2
  -> 593.9 / 787.8 -> 829.7 (target, 2 m in 0.38 / 0.22); Briars of Punishment 0.0 -> 25.2 (8 m;
  its 8001 row 0.0 -> 1.8); Ancient Death Rancor 0.0 -> 6.6 / 7.6 (target, 8 m in 0.11); Impenetrable
  Thorns 0.0 -> 3.2 (8 m); Night Maiden's Mist 0.4 -> 0.8 / 1.9 -> 4.0 (4 m / 3 m); Black Flame
  charged 0.1 -> 1.3; Explosive Ghostflame -55.9 -> -37.3 (3 m); Land of Shadow -93.2 -> -78.0
  (target); Dragonclaw -193.8 -> -193.6. A floor aim is offered but never better for Cannon of
  Haima, Rennala's Full Moon, Ranni's Dark Moon, Ambush Shard, Rock Sling, Oracle Bubbles,
  Giantsflame Take Thee, Black Flame's tap, Stone of Gurranq, Honed Bolt, Rings of Spectral
  Light, Light of Miquella and Bayle's Tyranny; no ground point 1 to 8 m is reachable for the
  short-lived carriers of Rock Blaster, Loretta's Mastery, Night Shard, Night Comet, Meteorite of
  Astel, Catch Flame, O, Flame!, Burn, O Flame!, Black Flame Ritual, Lightning Spear's tap,
  Rellana's Twin Moons and Bayle's Flame Lightning. Envoy's Greathorn's Great Oracular Bubble is a
  weapon skill and is not offered it: -0.91, nets nothing, as before (r5: -0.9).
- An on-hit child (launchConditionType 5) launches in the game when its parent hits the defender;
  the straight flight hits no character, so such hits are left out (Roiling Magma partial; Death
  Lightning, Lightning Spear charged and Wrath from Afar excluded) rather than flown from the
  parent's contact. A bullet attached to the caster is flown as one point, so its interval
  children never spawn (Watchful Spirit).
- Pull, knockback and status are invisible to the outcome model; tokens annotate them, and the
  displacement tokens gate the associations (above), so a pull is never called a twin of a plain
  hit. A pull's worth is still only its damage: what it sets up is not credited. Whether
  stateInfo 398 moves a player whose poise holds is untraced. Fires of Slumber carries no sleep
  token under the regdata gate; where its sleep comes from is not read.
- The full count is no longer credited whole to a caught defender (`CaughtModel`, above), but the
  instances of one hit list (a fan, interval children) tick together, a geometry's lists start at
  its first touch, he leaves straight back (not to the side), and he is placed at the situation's
  distance when caught even when caught mid-roll (**INFERRED**). A threat of a volley or a
  re-aiming chain (`volley_outcome`, `dynamic_outcome`) is still worth its whole full count when it
  lands. The robust class still leaves out dominators live longer than 60 frames or channelled.
- A shot whose event's orientation byte is 0 (most of them) heads level along the caster's facing
  from 1 m above his root, not from the DummyPoly's own height: the lock-on turn's vertical part,
  which would pitch it at the target, is not modelled (**INFERRED**). A shot with any other byte (the
  breaths, Comet Azur, the bow skills) leaves the dummy itself along its posed axis
  (ashes-of-war.md section 15, `VERIFIED` branch, the axis mapping **INFERRED**), with no vertical
  turn, which under-credits a breath the turn would bring down onto the target (Magma Breath). Each
  bullet event fires once, at its start, as the game does (`VERIFIED`). Attacks that take one
  `HitPriorityRecord` land one hit per defender among them, plus one for each later attack with a
  strictly lower priority (`VERIFIED`, ashes-of-war.md section 15; bd er-effects-rs-dnok): each
  geometry carries its record (`HPR`, in `geo_fingerprint`) and the caught defender
  (`CaughtModel`) takes a later geometry of a record another launch holds only at a strictly
  lower priority. One bullet launch is one attack to its record: its `numShoot` copies and all
  its children carry the launch's id (`BulletAttackInfo` +0x658) and the entry's own id passes
  even after it has landed (FUN_14051b870 0x14051b8c9; `VERIFIED`, bd er-effects-rs-xb3i), so a
  shot that holds a record keeps every copy and hit list (until 2026-11-10 the model kept one,
  the root's). One frame's tie goes to the attack created first (DmgMan's list, reversed by each
  FUN_140527d40 pass; until 2026-11-10 the model gave it to the last). Swift Slash's four bursts
  (four launches) share slot 1's record and its two blades slot 0's, so one of each lands;
  Meteorite of Astel's f6 and f11 meteors share one. `full` still counts every geometry of a
  shared record, since which one lands depends on the defender; the caught play takes one.
- The selftest's negative controls (`NEGATIVE_CONTROLS`) were picked again from the 2026-11-10 full
  run (stamp bd9e62039efdf179), because the round-2 ones no longer bite on this model: those that
  stay live are D_rel 0.14-0.73 apart, past --twin, and Magic Glintblade (both variants) lands
  nothing on a reacting defender since its blade forms 0.2 m ahead instead of 1 m. The new four are
  within --twin on D_rel alone and past it on D_assoc, with no twin, match or contains between
  them: Oracular Bubble [Envoy's Horn] / Scouring Black Flame (tap) at D_rel 0.008, D_assoc 0.128;
  Sorcery of the Crozier [Watchdog's Staff] / Glintstone Nail (charged) at 0.041, 0.179; Charge
  Forth [Short Spear] L2 / Cherishing Fingers (tap) at 0.042, 0.321; Deadly Dance [Curseblade's
  Cirque] / Fortissax's Light Spear (cast) at 0.088, 0.252. All but one have to bite. The round-2
  pairs (`APART_CONTROLS`) are still checked to stay apart. The delivery gate's pair is now
  Painful Strike's f34 frame against the Glintblade Phalanx cast (D_contain 0.089 on the profiles
  alone; Carian Phalanx is 0.51 away since the launches are placed). The only nearer melee frame
  is Troll's Roar's f43 against the charged Wrath of Gold (0.082).
- A follow-up's press (section 2a): that the behavior script changes state on the frame the press
  is ready and that the window's last frame is not in it; the attacker's reaction (the defender's,
  without the network legs); that he delays only on a roll he saw before the window opened; that
  the defender times his roll of the opening on the opening alone, plans for the press at once and
  reacts to a late one from the press. The opening's own samples after his roll are not tested
  again, and he is placed back at the situation's distance after it (as after every burst).
- A held loop spell's FP total per cast (cast plus ticks) is unknown: a lower bound. That a tap's
  `Magic.mp` is exact is **INFERRED**. Stamp's, Dynast's Finesse's and Death Flare's openings are
  scored on their FP-charging follow-up clip at the L2 FP: a lower bound too, and whether the game
  plays that clip without the press is not traced.
- Direct-AtkParam spells have no melee-pose reach.
- The associations read one build, the distance basis (every damage stat 99, DEX 99 cast speed);
  a build that casts slower shifts every hit of a spell later, which moves its punish profile. The
  shares they compare are scale-free pressed at once, but the run-in pick and the cast speed are
  not, so a twin read on the distance basis is not checked at each attacker build.
- Whether the game's requirement check counts talisman or great-rune stat boosts: requirements
  are read on base stats. The attacker builds use the run's two catalysts, not the catalyst each
  build carries, and one line per archetype made from the game's curves (section 1), not any
  corpus build.
- FP in the nets (version 10) and dominance's FP test, test (i) of section 6. A build's nets now
  carry its FP: the casts line-fit's FP plan cannot pay net 0 (`paid_share`). Test (i) is kept,
  because it still means something the paid nets do not. A paid share is 1 wherever the plan
  keeps up: on the 2026-11-12 lines that is every attack of 22 FP or less an engagement, and
  most dearer ones once blue flasks are drunk. There a cheaper attack's FP left over still buys
  what the model does not count: buffs, neutral casts, a second attack in one engagement. So the
  nets price FP only where it runs out, and test (i) prices it where it does not.
- The FP plan (section 1) is line-fit's, with its `INFERRED` pieces (line-fit.md section 5): one
  cast an engagement, the 5 s engagement, both FP items ticking at once, Crimson pricing. Here
  every cast spends its FP, and an unpaid cast nets 0, not an R1 (the run scores no attacker R1).
  The weapon lines' Mind fit weighs every usable FP attack alike, reads a skill's damage on
  CalcCorrectGraph 0 (16 for INT and FTH) at a correct rate of 100, and holds HP, defense and
  poise at the fit's (**INFERRED**). Every line is fitted without talismans or tears. A melee or
  split line's offense is one weapon's R1, the weapon picked by hit with its weight and windup
  paid, one attack an engagement (section 1, **INFERRED**: speed and recovery unpriced); a split
  line mixes it half and half with its spell. A swap kit is not carried.
- A release is let go on a frame of its window drawn uniformly (section 2c, **INFERRED**); a
  caster who prefers some frames, and a defender who knows it, are not modelled. Whether a charged
  skill's early release still pays the opening's FP, and which clip the late release plays, are
  not traced. A follow-up's press, and a charged skill's release with an opening hit before it,
  are read by a defender who plans for the press at once (`FollowupForm`, section 2c says why).
  A component of a release is its earliest release's frame, read as that release.
- Where a release shows (section 2c): that a bone 10 cm off the held pose is what a defender reads
  as a different move is **INFERRED** (5 and 20 cm in section 2c), and so is the blend timer's
  first frame (it reads 0 or one frame on the switch frame, a frame either way). Effects, spells'
  SFX and the hold spell's breath or barrage bullets are not part of the pose: a release whose
  clip barely moves but whose bullets stop is read only by its pose (Crystal Barrage let go late
  shows only at `VISIBLE_SCAN`).
- Whether a skill bullet's children keep the root's 'from the weapon' (and so the carrier's
  status) is not traced: the child-spawn caller of `SpawnBullet` would settle it.
- Two-handed openings; bows, Muleta, Causality's Wrath (a probe of their skill TimeAct found no
  clip on any carrier, 1H or 2H), Shield Strike.
- Skills with no roll frame.
- Why Surge of Faith sits on the frontier.
- The thresholds (`--twin` 0.1, `--live` 0.05, `--null` 0.02, `--idle-rate` 0.5) are choices read
  against the yardstick, the negative controls and the sensitivity table, all from the same pairs,
  not measured facts. All six negative controls are melee against a projectile; none is melee
  against melee or short reach against short reach, where D_idle can veto little (section 3).
- The run-in land share is one number per attack for most attacks (section 2): D_assoc asks the
  idle view to agree as well and the gate asks for a shared delivery, but two bullet attacks that
  reach alike and land the same run-in share are still twins whatever they look like. Such pairs
  are now marked `rests_on_idle` (section 5); nothing yet separates them.
- Homing token counts: the round-4 full run flags 180 entities and 11 exclusions 'homing' (this
  doc used to name 5), under the rule then (`Bullet.homingAngle` > 0 or `isEnableAutoHoming` on a
  flown node). The rule is now `homingAngle` > 0 alone: `isEnableAutoHoming` is read once in the
  image (**VERIFIED** 2026-11-12, 0x14040afea in FUN_14040afa0), where it picks an auto-target
  only for a caster who is not locked on, and the run scores the locked-on cast. The run's
  free-aimed casts (the floor bursts, section 8 above) are not locked on, but they are flown with
  no homing at all, so the auto-target is unmodelled there either way; whether a free-aimed pitch
  counts as aiming (`PrecisionShootingRequested`, which also turns the auto-target off) is not
  traced. Losing the token on that change (**MEASURED** 2026-11-13, flown nodes of
  `er-mechanics-ashes.fly_tree` on the final flight, ashes sha256 `0babd02a...`, spells
  `b491d417...`): Thops's Barrier (cast, early), Death Lightning (tap, charged; its on-hit child,
  flown since that flight, is auto-homing only), Stone of Gurranq (cast), Howl of Shabriri (cast),
  Fire's Deadly Sin (tap, charged), and Beast's Roar on its Club and Ritual Thrusting Shield
  openings; 140 spell variants and the other 45 skill openings flagged in the 2026-11-12 11:30
  cache keep it. Checked on one positive and one negative.

## 9. Sample run

**Round-5 sample** (**MEASURED**): 38 names, scored by this code in 221 s with 3 workers on 4
pinned cores, against er-mechanics-spells and er-mechanics-ashes as loaded at 20:49 on 2026-11-09
(both were edited again minutes later); the outputs are
`/tmp/claude-1000/-home-banon-projects-er-mods-rs/5ec7b779-1950-45a6-84dd-ef5b2c3e9082/scratchpad/attack-similarity-sample-r5.json`
and `.html` beside it. The names: round 4's 34 (the 9 names a spell and a skill share: Carian
Greatsword, Carian Retaliation, Darkness, Glintblade Phalanx, Glintstone Pebble, Golden Vow, Great
Oracular Bubble, Thops's Barrier, Zamor Ice Storm; Gravitas; and Firebreather, Dragonfire, Agheel's
Flame, Gravitational Missile, Gravity Well, Collapsing Stars, Starcaller Cry, Alabaster Lords' Pull,
Onyx Lords' Repulsion, Roaring Bash, Painful Strike, Carian Phalanx, Stamp (Sweep), Roiling Magma,
Honed Bolt, Tongues of Fire, Shard Spiral, Oracular Bubble, Savage Claws, Lightning Spear, Death
Lightning, Rock Sling, Lifesteal Fist, Poison Mist) and Carian Grandeur, Bloodhound's Finesse,
Swift Slash and Sword Dance. 22 spell variants and 47 skill entities: 66 scored, 3 partial (Rock
Sling, 174.8 HP of an on-hit child left out; Roiling Magma tap and charged, 731.1 and 923.0 HP), 14
excluded; 37 above the association floor. Honed Bolt is scored with 136.7 HP of its water-only
bullet dropped. Section 6 gives the full run's dominance per attacker build (r5), not this
sample's.

- **New entities**: the early releases of Carian Grandeur (757.4 against the full charge's 1,377.1,
  both FP 26), Carian Greatsword (757.4 against 1,170.5, FP 16) and Swift Slash (1,540.2 against
  2,567.1, FP 16), each a variant of its charged default; the fixed carriers Lazuli Glintstone
  Sword (Glintstone Pebble), Meteoric Ore Blade (Gravitas) and Carian Knight's Sword (Carian
  Grandeur, and its early release).
- **Associations**: 4 cross-type matches (the Zamor Ice Storm tap with the Zamor Curved Sword skill
  through its f45 storm, D_contain 0.030; Collapsing Stars tap with Tongues of Fire, 0.036; Shard
  Spiral charged with Oracular Bubble, 0.053; Glintblade Phalanx with its ash, 0.069), every one
  idle-bound and 2 resting on D_idle alone (Collapsing Stars / Tongues of Fire and Glintblade
  Phalanx, both sides flat). 6 twin pairs, 4 cross-type, 1 through a component (the Zamor storm),
  3 resting on D_idle; round 4's sample had 7, the volley of each Glintblade Phalanx R2 follow-up
  among them, which is the opening's (section 1) and no longer a component. 1 contains (the Zamor
  storm), 7 families, 3 of them mixed.
- **The delivery gate**: the nearest pair it keeps apart in the sample is Roaring Bash and the
  Collapsing Stars tap at D_assoc 0.211 (D_idle 0.422), so at these names no threshold up to 0.2
  depends on the gate.
- **Effect families**: 'pull' has 12 members, every skill netting nothing (Gravitas on four
  carriers, the Meteoric Ore Blade among them; Starcaller Cry and the Gravity Well tap the nearest
  pair by D_idle, 0.066). With the carrier's own status read: 'status:frost' 7 (the Zamor Curved
  Sword's own 6702 and Swift Slash, Sword Dance and Gravitas on the Cold Reverse-Bladed Sword),
  'status:bleed' 4 (Bloodhound's Finesse and its R2 follow-up, Gravitas on the Meteoric Ore Blade,
  Savage Claws), 'status:poison' 2 (Painful Strike through its carrier's 6511, and Poison Mist,
  excluded with no damaging hit). 'grab' holds Lifesteal Fist ('grab not modelled (throwTypeId 20)').
- **Flags**: 10 entities that are not live net under -1 HP and carry 'lands little, punished
  more': Starcaller Cry (-87.8), Carian Grandeur's full charge (-184.1) and Carian Greatsword's
  (-165.6) among them.
- **Name-sharing pairs** (round-4 sample): of the 9, Glintblade Phalanx (twins, match), Zamor Ice Storm (twins and
  a match through the storm frame, contains), Glintstone Pebble (D_idle rank 2 only) and Great
  Oracular Bubble (both never reach) have both sides profiled, as in the full run. Carian
  Greatsword (spell: direct AtkParam), Carian Retaliation and Thops's Barrier (spell: no damaging
  hit; skill: no damaging hit (bullet (no damage), parry)), Darkness (spell: no damaging hit; the
  Sword of Darkness skill is live, mean net -48.3 at FP 30) and Golden Vow (spell: no damaging hit;
  ash: blacklisted) are left out on at least one side.

## Commands

```bash
# the full run: before the attacker builds (round 4) about 17 min with 8 workers on an otherwise
# idle machine, 31 min under other load; with the eight attacker builds it does more (round 5's
# sample of 38 names took 221 s with 3 workers, the selftest's 42 names 312 s); the full runs r4
# and r5 took about 33 and 30 min from log start to end, workers and load not recorded (every
# run since is timed, below). Version 8's full run r9 took 3h31m with 3 workers; version 9's
# release members made r10 run 110 s per attack set at its last pace (39 sets in 4,310 s), and it
# was stopped. `--show` names the newest full run's time from the timing log when the cache
# misses. A concurrent pooled run shares the same cores. Run it in the background. The attack
# sets run the cheapest first and the rest in a seeded shuffle (`set_order`), so the counter's
# time left is a projection from a random sample of them; each item's seconds, and each key's
# estimated cost, go to attack-similarity-timing.items.jsonl as they end, so a stopped run
# leaves them too (r11, stopped at 10 of 944 sets, left none). Each set's row there also has the
# process that read it, its resident and peak bytes and the model's memos (`memory_now`): r12 was
# stopped for low system memory at 578 of 944 sets with nothing saying what held it; the 200-set
# sample r13 then showed workers keeping the peak of their heaviest set (6.36 GB resident, 13 MB
# in memos), freed heap glibc had not handed back, which each set now hands back at its end
# (`release_heap`, `rss_after` in the trail). The trail also has the sets' order, which named the
# set r12 was stuck in: set 272 (Flame Fall Upon Them, Giantsflame Take Thee) read alone grew
# about 3 GB a minute to 44 GB, in `lookalike_plan`'s memos, now bounded together
# (`er-mechanics-ashes.LOOKALIKE_MEMO_ENTRIES`) and emptied per set. Each set's
# results are kept in attack-similarity-<band>-<stamp>.sets/ beside the cache as the set ends
# (`save_set`), and a run of the same stamp reads them instead of reading the sets again; the
# folder is emptied once the cache is written, and --refresh empties it first.
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py \
    --out <dir>/attack-similarity.html --json <dir>/attack-similarity.json
# a sample of the attack sets: the first 200 of their order, then stop; their seconds and memory
# go to the timing log and its trail, and the sets are kept for the full run to resume from
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py --sets 200 --jobs 3
# the selftest took 813 s with 3 workers in its last run, its attack sets 636 s and 1,446 CPU s
# (timing log, 2026-11-11);
# --keep-going runs every check past a failure (check 10 fails until a full run re-picks its
# negative controls) and exits 1 at the end
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py --selftest --keep-going
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py --show "Glintblade Phalanx"
# a sample: scores only these names, never writes the cache
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py \
    --only "Zamor Ice Storm" --only Gravitas --only "Glintblade Phalanx" --only "Glintstone Pebble" \
    --out <dir>/attack-similarity-sample.html --json <dir>/attack-similarity-sample.json
# the newest run beside the newest earlier run of its scope; or name runs by line number
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py --timing
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-attack-similarity.py --timing 3 -1
```

Every run is timed, with nothing to switch on: the full run, an `--only` sample, a `--show` and a
selftest that passes each append one JSON line to `~/.cache/er-build-planner/attack-similarity-timing.jsonl`
(`--timing-log` names another), and the stderr's last line gives the total and each stage. A line
holds when the run started, its scope (`full`, `only` with the names, `selftest`), the cache stamp
and whether the profiles were computed or read from the cache, the workers and the CPUs the run
could use, the load average at the start and the end, git's branch and head and whether `scripts/`
differs from it, the sha256 of every loaded script (`code_stamp`, what a dirty tree's head does
not say), taken once the scripts are loaded, with the files edited on disk while the run ran
(`code_moved`), the wall and CPU seconds of each stage (`context`, `stamp`, `cache read`, `candidates`,
`inputs`, `objective inputs`, `attacks`, `results`, `analyze`, `cache write`, `json`, `page`), and
the wall and CPU seconds of every pooled item, timed in the worker that ran it: an input by its job
and who it is run for (`spell:<id>`, a skill's parent key, `@<build>`, `@<objective line> <stats>`),
an attack by its key. The `--json` output carries the stages up to its own write. `--timing` sets
two runs side by side: each stage's wall and CPU seconds and their ratio, the CPU seconds per
item, which scripts' sha256 differs, and per pooled stage the items whose CPU seconds moved most,
with their share of the stage's change. Compare CPU seconds: wall seconds move with the workers
and with the rest of the machine's load (11 to 16 on the runs of 2026-11-10), CPU seconds per item
much less. So a change that costs time is found in the report, and justified, or not, by what it
moved in the numbers.

`<dir>` is any directory; the session that made this doc used its scratchpad. The full run caches
its profiles in `~/.cache/er-build-planner/attack-similarity-140-160-<stamp>.json` and `.npz`; a
run with a matching stamp reads them and only recomputes the associations (about 30 s). The cache
holds each attacker build's records and nets too (`b<i>_keys`, `b<i>_net`, `b<i>_net_one` in the
`.npz`). The stamp covers the distance basis build, the attacker builds (name, attribute line and
count, so a corpus that moves a line or a count moves the stamp), the situations, the regulation
file, the paramdef XMLs, the blacklist, every loaded script (er-mechanics-build-tags and
er_corpus among them), and the two HKS inputs that pick every skill's opening clip: the parser
`scripts/er-hks-weapon-category-table.py` (which `er-mechanics-ashes.sword_arts_categories` loads
into a local variable, out of reach of the loaded-module walk) and the `common_define.hks` it
reads (`ER_COMMON_DEFINE_HKS`). It does not cover the TAE/HKX extraction or the FMG name cache:
`--refresh` after re-extracting either. The cache JSON stores what went into it, so a miss says
what moved, and records the thresholds of the run that wrote it; the cached profiles hold no
threshold, and a later run recomputes the associations and dominance from them. Both files are
written to a temporary name and moved into place, and an unreadable cache is a miss. `--show`
reads the cache only and says how to make it when it is missing; it prints an attack's status,
group, tokens, its two objective lines (section 6a), neighbours, twins, cross-type matches,
contains rows, family, probe rows, the frames a follow-up inherits, and per attacker build its
carrier, mean net and dominance edges (or that the build cannot use it). The cache's version 5
holds each record's objective evaluation, so a version-4 cache is a miss. Thresholds (`--twin`, `--cut`, `--live`, `--null`, `--tolerance`, `--hp`,
`--scale-band`, `--k`, `--idle-rate`) apply after the cache. `--only` runs never write it.
