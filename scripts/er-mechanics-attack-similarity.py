#!/usr/bin/env python3
"""Attack similarity: how every player attack spell and every obtainable weapon skill resemble each
other as punishes (spell x spell, skill x skill, spell x skill), and how they associate: twins
across the two kinds, families, nearest neighbours, and which attack makes another redundant.

    python3 scripts/er-mechanics-attack-similarity.py --out <dir>/attack-similarity.html --json <dir>/attack-similarity.json
    python3 scripts/er-mechanics-attack-similarity.py --only "Zamor Ice Storm" --only Gravitas --only "Rock Sling"
    python3 scripts/er-mechanics-attack-similarity.py --show "Glintblade Phalanx"
    python3 scripts/er-mechanics-attack-similarity.py --only "Rock Sling" \
        --line "Int/Faith 60:vig=36,mnd=20,end=20,str=12,dex=12,int=60,fth=60,arc=9"
    python3 scripts/er-mechanics-attack-similarity.py --timing
    python3 scripts/er-mechanics-attack-similarity.py --selftest

Every run appends its timing to `TIMING_LOG` (`Timing`); `--timing` compares two of them.

Labels as the sibling docs use them: `VERIFIED` = read in the code or the installed params,
`REGULATION` = a param value, `TAE` = decoded TimeAct, `MEASURED` = computed by this tool or a probe,
`INFERRED` = a reading whose consumer was not traced, `COMMUNITY` = an outside claim. Nothing is
launched: every number comes from the models `er-mechanics-spells` and `er-mechanics-ashes` already
compute, read through their own functions, except the attacker builds' nets, which `OutcomeForm`
computes from those functions' own reads (and checks against `net_parts`).

# The attacks

Two kinds of build, against the median defender of the RL window, both with the catalysts
`Glintstone Staff+25` and `Finger Seal+25` (each spell cast with the first that can), no talismans,
no off-hand, one-handed, PvP rates:

- The distance basis (`--stats`, default every damage stat 99): every attack is wieldable and
  castable, so every attack has a profile. No character at the RL window has it (with 10 `VIG`, `MND`
  and `END` it is RL 446). The matrices and every association read it; they compare land shares,
  which are scale-free pressing at once and nearly so with a run-in (below), so they barely move
  with the build. Nothing that compares damage or FP is read on it.
- The attacker builds (`attacker_builds`): lines made from the game's curves at the run's RL (the
  eight attributes sum to RL + 79), one per stat archetype the corpus has at the RL window
  (Strength, Dexterity, Quality, Intelligence, Faith, Arcane, Hybrid) and two split lines
  ('Intelligence + melee', 'Faith + melee'). Every line is `er-mechanics-line-fit.fit`'s
  (`fitted_lines`): VIG, `END` with the armor and HP piece it carries at medium roll, and MND traded
  at fixed RL on one number, the damage the line deals before it dies, measured on its kit
  (`line_kit`): a caster casts its school's spell from its school's catalyst; Hybrid both; a melee
  line swings one R1 of the weapon picked off the game at its own stats (`er-mechanics-line-fit.
  pick_weapon`, of its archetype's weapon scaling); a split line does both, half and half, with its
  main stat stopped at 60. The melee and split lines keep the user's melee poise floor; each
  reports the best allocation below it. The weapon lines' MND is then fitted against the FP of the
  attacks they can use (`fit_mind`), since their R1 costs none. A line carries only its kit, never
  what its archetype's players carry. Every line's FP is priced by line-fit's one FP plan
  (`fp_plan`, `fp_plan_paid`: a bar, then Starlight Shards and the Lulling Branch between
  engagements, then blue flasks of min(220, bar), each a Crimson lost). The corpus gives usage
  alone: which archetypes exist and how many builds each has (user, 2026-11-12: "I think you're
  overvaluing real builds"; the weight they carry is reported as usage, not read). A build uses an
  attack only when
  its base stats meet the carrier's requirements, or the spell's and its catalyst's; whether the
  game's requirement check counts a talisman's or great rune's stat boost is not established, so
  base stats are read. A build's skill rides the group weapon it can wield whose skill damage, its
  opening and every follow-up summed, is largest at its stats (`build_job`, `carrier_damage`), at
  the affinity whose skill damage is largest there, and its spells cast at its own DEX.
  Its nets are paid ones (`BuildView`): an attack's nets times the share of its casts the build's
  FP pays, the rest netting 0. Dominance is read per build (below). An attack no build can use is
  in no build's population; the objective evaluation (below) prices it all the same.
- Each `--line` spread (`given_builds`): one more attacker build, after the made lines, at the
  eight attributes it gives, none traded. It wears the lightest armor set at its `poise` (default
  the melee poise floor, the set a line with no fit wears; 0 wears none), no HP piece and no
  talismans, and its nets are read and paid as a made line's are, on its FP plan (`fp_plan`). What
  it carries in its hands is not given, so its roll is reported on its armor alone. It moves no
  other number: the objective lines start from the made lines alone (`MEASURED` bit for bit on the
  Glintstone Pebble sorcery, check 36; `INFERRED` elsewhere: a spread's lookalike group can join two
  attack sets, which can refine a release's frames further, `release_strata_of`).

A spell variant is one entity per `SpellModel.evaluate` variant (cast, or tap and charged), flown
with the spell rule as its node test (`SpellModel.spell_node_damaging`). A weapon skill is one
entity per carrier opening: the weapons that carry it (an ash: every base weapon that mounts it at
some affinity at its top level, plus the weapons that ship with it; a unique skill: its own
weapons) are grouped by the clip they open with. On the distance basis each opening rides the
weapon of least total requirement in its group, and an attacker build or objective line the group
weapon it can wield whose skill damage is largest (`build_job`), each at the mountable affinity
whose skill damage is largest. A stance skill
is one entity per button, and each follow-up its own entity. A charged skill (its opening carries
the early-release window SpEffect 100285) is two: the default entity holds the charge to the end,
its opening clip, which carries the `AtkParam.subCategory` 111 rows (`MEASURED` round 5: on every
one of the 21 skills that have such rows), and the early release, the clip after it, is a variant
of it (`skill_releases`). L2 let go on any frame of the window plays the release (`VERIFIED`
c0000.hks `SwordArtsOneShot_onUpdate`), the attacker's choice of frame (next section). A release
with an opening hit before it (Black Flame Tornado, Jori's Inquisition, Moore's Charge, Shield
Crash, Thundercloud Form and Wall of Sparks, `TAE` 2026-11-10) is scored as a follow-up is, by
`FollowupForm`: let go at the window's first frame, later only on a roll of that hit the attacker
saw before the window opened ('followup'); any other as the mean of its release frames
('mixture'). A hold spell whose 045010 has the tap window is also let go early, its 'early'
variant (`SpellModel.variants`). Every candidate ends up scored,
partial (some event or hit has no reach model) or excluded, with a reason. A scored entity with two
or more damaging frames also gets one component per frame (at most `MAX_COMPONENTS`), which feeds
'contains' and is never a matrix row; a follow-up's frames before its press are the opening's
(`inherited_frames`) and get none.

# The outcome per situation

The situations are `er-mechanics-spells.punish_situations` (1,208 spaced whiffs of the RL window's
opponent pool at RL 150). Per situation `er-mechanics-spells.net_parts` gives what the attack lands
and how often the defender who dodged it punishes, both for pressing at once (`now`) and for the
best of pressing at once and running in first (`best`), plus the first frame it touches a defender
who does not dodge (`t_first`) and the net on one hit per event (`net_one`). The land share divides
by the attack's own full worth, so the press-now shares are scale-free while the defender's choice
to strike or roll holds: twice the damage lands the same share pressed at once. The choice is not
scale-free (it weighs the worth against the R1's fixed cost), nor is the best option: it picks the
run-in by the net (damage less the punish), so scaling the damage can move the pick and with it
`land` and `pun` (`MEASURED`, selftest: the Zamor Ice Storm tap at 4x its damage is D_rel 0.0098
from itself with the strike, 0.0044 before it).

Every net (`net`, `net_one`, the attacker builds' and the objective lines') is then less the HP the
cast takes from its own user (version 13, 'hp_cost'): the game takes it at a TimeAct event or a
bullet launch, whether or not the cast hits (`VERIFIED` 2.7.2.0, `er-mechanics-self-damage`'s
module docstring), as `changeHpRate` percent of max HP plus `changeHpPoint` flat, so it is paid on
every press and moves no land share. Each line pays it at its own max HP (`line_hp`); the distance
basis, which has no VIG, at the RL window's median defender's (`INFERRED`). A row that costs HP
only through a status proc on the caster (the frenzy incantations' madness, Briars of Sin's bleed
half) is left unpriced, listed in 'hp_cost' 'unpriced': no line carries its own resistances
(`er-mechanics-self-damage.status_cost`). A component carries no cost: the whole entity pays it.

Every net and every catch's worth also carries the status the attack builds (version 30): each hit
list of a caught defender's `CAUGHT` record carries the build-up one of its hits puts on the gauge,
per status (`annotate_status`, `list_status`: the 13 hit SpEffect slots as
`er-mechanics-ash-table.Table.hit_buildup` reads them and `er-mechanics-status._row_amount`'s
formula, at the line's own stats and the carrier's affinity; a spell's hits read no weapon rows),
and the proc row it would apply. Per play of the caught defender (`er-mechanics-ashes.CaughtModel`'s
runs) the landed share of each list's hits gives the build-up landed, less the gauge's refill over
the play; each pool build whose own resistance (`er-mechanics-spells.SpellModel.defender`
`status_pool`, the planner's `computed.resistances`) that empties takes the proc row's HP at his own
max HP, added to what the catch lands before the cap at his health
(`er-mechanics-spells.StatusCaught`, the comment above `STATUS_WORTH_FLOOR` there has the rules and
what is not priced). So a proc counts only when the hits landed in that situation cross the
threshold, the defender's choices read it (he dodges what would proc him as he dodges damage), and
an attack that builds nothing reads the plain caught model and the same numbers. A land share
divides by the full worth with the status HP of every hit landing (`er-mechanics-spells.status_full`).
A status-only spell's hits count as hits of no damage (`status_hit_atks`), so Freezing Mist is scored
on the frost its field builds (`MEASURED` 2026-11-22, check 38: 20 a tick, 1,400 over the field's 70
ticks, 213 HP on the pool when all land; no catch at the first 12 distances crosses a gauge, so its
nets are what the defender's punish leaves). Poison Mist (poison, 1,400) and Fia's Mist (death
blight, 260) are scored the same way (check 37); a death blight row costs 0 HP (its kill by state is
not traced), so a death blight proc prices nothing. A weapon buff (Frozen Armament) is no attack
and adds nothing to any other attack: an item that buys an effect is a choice, priced as a pick.
A skill's status-only bullet (no damage) and a catch by the old rule (no `CAUGHT` record) build
none, `INFERRED` as unpriced, and the one-hit nets (`net_one`) carry the whole status as `net` does.

A follow-up (bd er-effects-rs-me5p) is scored by `FollowupForm` instead, on the same situations,
run-in options and caught defender: its opening is dodged as `sequence_outcome` dodges it, and the
follow-up meets the roll the defender made of it (`er-mechanics-ashes.FollowupModel`: it catches
him outside that roll's invincibility before its dodge cancel; else he dodges it from there as
planned for the press at once, and a late press meets that planned dodge early, after which he
reacts to the press). The attacker presses at the window's first frame unless that roll showed
on his machine his own reaction before the window opened (`attacker_reactions`, `INFERRED`: the
defender's reaction without the network legs); then he presses at the frame of the window that
leaves him most against it. Pressing later moves the whole follow-up clip and his roll frame
(`skill_option` offsets every follow-up hit by the press; a press inside the input and cancel
windows is ready on that frame, `VERIFIED` attacks.md section 4). A defender who does not roll the
opening meets the earliest press, so the delay never helps against him (`base_now` is the earliest
press's). The earliest-press entity is replaced, not kept beside it: it is one choice of the same
attack at the same FP, so keeping both would make every follow-up dominate or twin itself.

The reacting defender sees only what is on screen, with the rule the spell kit uses
(`er-mechanics-spells.Lookalikes`, ashes-of-war.md section 16a 'What is on screen'): he cannot
tell a spell from the other casts of its TimeAct and casting SFX until a shot that differs spawns,
nor a tap from its charge before the tap's release shows (`SpellModel.look_split`); nor a charged
skill's hold from its early release before the release shows (`skill_lookalikes`; where a release
shows: below). Until the frame that tells
them apart he either times one dodge against the whole group or waits for that frame and reacts
from it, whichever escapes the group more often in that situation (`er-mechanics-ashes.
lookalike_plan`), and every later burst is dodged knowing the attack. The group is read per line,
the distance basis, each attacker build and each objective line apart (`look_specs`): a spell's
is every variant of the spells of its (`Magic.refType`, `castSfxId`) among the spell candidates
(usable in multiplayer, held by a player) that the line casts with the run's catalysts, at that
line's own cast speed, whatever `--only` keeps; a component is read as its whole entity. An attack
alone in its group is read as before, number for number. Before this (version 7 and earlier) he
knew the attack from its first frame, so a single bolt with an escaping run of a few frames in
every roll direction was rolled in every situation (Glintstone Cometshard and the Glintstone Dart
netted about 0).

A release (a spell's tap, a hold spell let go early, a charged skill's early release) is let go on
a frame of its window the attacker picks and the defender cannot see coming (version 9): phase 1
scores it at every real frame of its window (`er-mechanics-ashes.release_frames`, each weighed
alike, `release_members`), and it is read at nodes of them (`release_strata_of`: the window's
first and last frames and every `RELEASE_STEP`th between, `release_nodes`, with every frame
added between two neighbours whose mean nets at the distance basis differ by more than
`RELEASE_SPLIT_HP` over the share of the window between them, in one more read,
`release_fill_in`; every line reads the basis's nodes). The frames between two nodes count as the straight line
between them (`node_weights`), so each node is one member of the group weighing its frames
(`strata_spec`), told from the hold or charge where its release shows and from a later release
where the earlier one shows (`look_spec`; shows: `er-mechanics-ashes.release_visible`, the first
frame of the blend into the release clip at which a bone is `VISIBLE_M` from the held pose,
`INFERRED`), and a release he sees before his decision time he reacts to as it shows
(`er-mechanics-ashes.lookalike_plan` `events`, `INFERRED`; without it a decision taken at the last
release frame met the first that late). The entity's arrays and nets are the nodes' weighted mean
(`mix_arrays`, `INFERRED`: the attacker draws the frame uniformly, which is what the group's
weights take him to do; taking his best frame instead would be a frame the defender's plan does
not expect). Within 3% or 5 HP of every frame in the group and read on every release measured
(attack-similarity.md section 2c). A window of one real frame is the earliest release alone, as
before.

# The distances

All in [0, 1], symmetric, zero on equal profiles, weighted by the situations' weights w (sum W).
The profile of an attack is u = w_s x (land_now, pun_now, land, pun) over the cells (situation,
outcome); its mass is the weighted mean of those four shares.

- D_rel(a, b) = sum |u_a - u_b| / sum max(u_a, u_b): the share of the two profiles' combined
  outcome they do not have in common (the weighted Ruzicka, or Soergel, distance: a metric). It
  is relative to the pair's own mass: two attacks that both land little are alike only when they
  land in the same situations, any profile is 1 from an empty one, and an attack that lands half
  the share of another in the same situations is 0.5 from it.
- D_land: D_rel over the land channels alone (land_now, land), so a punish chance two attacks
  share cannot make them alike when they land in different situations.
- D_now(a, b) = sum_s w_s (|land_now_a - land_now_b| + |pun_now_a - pun_now_b|) / 2W, the absolute
  gap pressing at once (a weighted L1 pseudometric). It is at most the sum of the two press-now
  masses, so it is small for any two attacks that land little whatever they are: it is shown beside
  D_rel and greyed when either mass is under `--null`, and it never decides an association.
- D_best: the same on the run-in-or-not choice.
- D_idle(a, b) = sum_s w_s (0.5 |base share_a - base share_b| + 0.5 c_s) / W, with c_s 0 when
  neither touches an idle defender at s, 1 when one does, else dt / (dt + F) for first-touch frames
  dt apart, F = (`REACTION_MEDIAN_S` + 2 `NETWORK_ONE_WAY_S`) x 30 = 10.5 frames (`INFERRED`: the
  smallest gap a defender can act on). It reads 'lands alike on a defender who does not dodge', and
  is the neighbour measure of the attacks that are always evaded.
- D_assoc(a, b) = max(D_rel, D_land, `--idle-rate` x D_idle), default rate 0.5: the distance
  every association reads. Against a dodging defender most of the profile is the run-in, whose
  land share is one number in every situation it is picked in (a quarter, one per roll
  direction, for many attacks), so D_rel alone made a shield bash a twin of a glintblade volley
  (`MEASURED`, round 2: D_rel 0.082, D_idle 0.499). The idle term asks that they also reach the
  same distances at about the same frame. A max of metrics is a pseudometric.
- D_contain(c, b) = max(D_land, `--idle-rate` x D_idle): the 'contains' distance between one frame
  of an attack and a whole attack, on what lands only. A frame inside a longer attack rides that
  attack's recovery, so its punish chance is not the frame's own (the Zamor Curved Sword storm
  frame lands what the Zamor Ice Storm tap lands in every situation and is punished more).

# Groups

By land mass, the weighted mean share of its own worth an attack lands: live when pressing at once
lands at least `--null`; runin_only when only the best option (a run-in) does; nets_nothing when
neither does ('never reaches' when an idle defender is touched on less than `--null` of the weight,
else 'always evaded'). A punish chance never makes an attack live. A live attack that nets at most
`--hp` on average is flagged 'punished as much as it lands'; one that is not live and nets under
-`--hp` (the Zamor Ice Storm casts, Flame Spit) 'lands little, punished more'. Both flags read the
distance basis's nets; each attacker build lists its own (`build_dominance` 'punished').

# Associations

An entity takes part in twins, matches, contains, families and the yardstick when it is scored and
lands at least `--live` of its worth (the larger land mass). Every association sits behind the
delivery gate: two attacks associate only when they share a way of delivering their worth (melee
hits, bullets; a record's `delivery`, a component's at its frame). The run-in land share is one
number in every situation it is picked in, so on the profiles alone a melee whip frame contains a
glintblade volley (Painful Strike's f34 and Carian Phalanx, `MEASURED` round 3, D_contain 0.056; on
the 2026-11-10 full run Painful Strike's f34 and the Glintblade Phalanx cast, D_contain 0.089).
Twins: D_assoc at most `--twin`, full worth within a factor 2^`--scale-band`; or a component twin,
one frame of X within `--twin` of a whole Y of the other kind by D_contain with the whole of X within
that band of Y (the Zamor Curved Sword's storm frame and the Zamor Ice Storm tap). Cross-type
matches: mutual nearest across the kinds by D_assoc within `--twin`, and every contains whose two
whole worths are within the band. Contains: a component of X is strictly nearer by D_contain to a
whole Y of the other kind than any other item of X's kind, and Y is that component's nearest whole
of its kind, within `--twin`. Every twin, match and contains row says which term of its distance
binds ('bound': 'idle' or 'land') and whether it rests on D_idle alone ('rests_on_idle'): the idle
term binds and both sides land one flat share wherever they land (`flat_land`: a quarter, one roll
direction of four; 3/14, one direction at six of seven timing points), so their land channels
agree on one number whatever the attacks are. Such a pair says the two reach an idle defender
alike and land the same quantum in the same situations, not that they are alike otherwise
(`MEASURED` round 4: every whole cross-type match of the full run, the Glintblade Phalanx spell and
ash among them). Families: average linkage (UPGMA) by D_assoc behind the gate, cut at
`--cut`, checked against `Magic.subCategory1` by purity and same-category pair share beside the
base rate as well as by the adjusted Rand index, which singletons hold down. Neighbours: by
D_assoc, or by D_idle among the attacks that reach for one that is always evaded; a pair the gate
keeps apart is marked, and its delivery shows with the tokens it does not share. Effect tokens
(status, HP drain, defense down, pull / push / gravity, homing, unblockable, grab, jumpable)
annotate entities and pairs; as tokens they never enter a distance (a status's build-up enters the
nets and land shares, version 30, the outcome section; the rest the outcome model cannot see). A
skill's status tokens include its carrier's own status rows when a scored hit is from the weapon (`weapon_status_tokens`: `VERIFIED`, round 5 trace). An attack excluded for having no
damaging hit still has its tokens read, on every node and
AtkParam it fires, and is listed in its effect families as an exclusion (Poison Mist, Deadly Poison
Spray); a skill whose only hit is a grab the model does not price is excluded as 'grab not
modelled' (Lifesteal Fist).

A partial entity is scored on its hits that have a reach model, the same rule for spells and
skills: the left-out hits and their worth are listed (`missing`, `missing_hp`). A hit list priced on
a bullet the straight flight never launches is one of them (`SpellModel.launch_gaps`: an on-hit
child, launchConditionType 5, since the flight hits no character; Roiling Magma's 10482004 under
10482002), whether or not the carrier above it does damage; one that launches only on water or
never (`NEVER_ON_LAND`: Honed Bolt's 10693006) is dropped from the worth (`dropped`, `dropped_hp`)
and leaves the status alone. A follow-up is keyed by its button, clip and press frame, and leaves
out the opening's hits that start at or after its press, which cancels the opening (`skill_attack`:
Stamp's opening is scored on the follow-up's own clip, which counted twice); a key produced twice
stops the run.

# Dominance

Read per attacker build, never on the distance basis: a build's population is the attacks it can
use that are scored and cost FP, each with its nets per situation at the build's stats
(`OutcomeForm`: one pass over an attack's geometry gives `net_parts`' net for any worth, so a
build that flies the distance basis's geometry at its own damage costs no second flight; every run
checks the form against `net_parts` wherever a build flies that geometry, `FORM_TOL`). The twin
marks on its edges are the distance basis's twins. Within a build:

a dominates b when a costs no more FP (and, spell over spell, no more memory slots), both nets more
than `--hp` on more than `--tolerance` of the weight, a loses to b by more than `--hp` on at most
`--tolerance` of the weight (default 1 / n_eff), a's weighted mean gain over b is at least -`--hp`,
and a is strictly better: a mean gain above `--hp`, less FP, or fewer slots with a mean gain of at
least 0 (so the relation has no cycle: `dom_rows`). An edge is robust when
the same test passes on one hit per event, a's FP is not a lower bound (a held loop: its FP is
cast plus ticks, of which only the cast is counted) and a carries no 'full count' warning (live
longer than `LONG_LIVE_FRAMES` or channelled). One hit per event does not discount a lingering
area: `_bursts` credits the geometry that catches first with every later frame, and an area that
ticks once per frame has net_one equal to net. The relation is pairwise; every edge is kept, and a
run checks its edges make no cycle (`find_cycle`). A dominator of b is maximal when no other
dominator of b dominates it; the robust ones are maximal among themselves by the robust edges and
listed first.

# Objective evaluation

Whether a corpus build happens to use an attack says nothing about what the attack is worth, so
every scored and partial entity is also priced at two lines of its own (`objective_lines`, stored
as the record's 'objective'), the same way a build prices its attacks (phase 1 at the line's
damage stats, `OutcomeForm` nets per situation, their weighted mean as 'mean_net'), FP or none:

- 'adjusted': the attacker build line that puts the most points where the attack scales
  (`line_fit`: the scaling-weighted mean of its damage stats; ties to the earlier line: the
  archetype with more corpus builds, usage, the split lines last), fitted
  per entity: an opening, each follow-up and each early release has its own. A skill entity's
  weights are `hit_stat_weights` of its own priced hits at the distance basis: per hit and
  element, the base attack before scaling times how much that element's multiplier rises when the
  stat goes from the requirement to 99 on the hit's AECP row (`er-mechanics-ar.
  element_multiplier`, the AtkParam's `overwriteAttackElementCorrectId` when set), over the total
  base attack (its job's first entity with weights when its own hits give none, the carrier's
  correct* fields when no hit of the job does). A spell weighs its school stat (`SCHOOL_STAT`).
  Every damage stat under the attack's requirement (`attack_need`: the
  carrier's, one-handed, or the spell's and its catalyst's, on base stats) is raised to it, and as
  many points are paid back one at a time from the line's highest damage stat above its own
  requirement and the lowest starting-class base (`adjust_line`), so the RL stays the line's. A line
  that already meets the requirements is the line itself, and where that build prices the attack
  its mean net is the build's (selftest).
- 'minimum': the lowest-RL character that meets the requirements exactly: per starting class, each
  attribute at its base or its requirement, whichever is higher, and the class whose line has the
  lowest RL (`minimum_line`, 'from_class').

A skill rides the group weapon `build_job` picks at the line (of the group weapons the line can
wield, the one whose skill damage is largest there; both lines meet the distance basis's carrier's
requirements, so there is always one), at the affinity whose skill damage is largest there. A job is run
once per distinct line among its entities, and each entity is kept from the run at its own line;
entities on one line share its `OutcomeForm` groups. The objective lines are not in any
population: dominance stays per attacker build.

# Not established

- Whether the game's requirement check counts talisman or great-rune stat boosts: requirements
  are read on base stats. The attacker builds use the run's two catalysts, not the catalyst each
  build carries.
- The objective 'adjusted' line is fitted to the share of a skill entity's base attack each stat
  scales (its own hits), not to what lands: the fit is a choice
  (the line with the most points where the attack scales), not a search for the line at which the
  attack nets most.
- Homing is flown in the horizontal plane only (`er-mechanics-ashes` `homes`, FUN_140398270): the
  aim point's height is not decoded, and a caught defender's later hits are read on the straight
  flight. Pull and knockback are invisible to the outcome model and are only annotated as tokens;
  a status is priced only through a proc's HP (frost's damage-taken rise, sleep and a death blight
  kill are not). Whether a skill bullet's children keep the root's 'from the weapon', and so the
  carrier's status, is not traced.
- Whether letting a charged skill go before the opening's FP event still pays the FP is not traced;
  the late release (SpEffect 100286, Carian Grandeur alone) is not scored. The release frame is the
  attacker's choice inside the 100285 window (`FollowupForm`'s rules, below).
- What is on screen (`INFERRED`, as the kit): the members of a group weigh the same (nothing says how
  often each is cast); equal `castSfxId` SFX look the same and a different one is told apart from
  the first frame (only the ids are read). A charged skill's hold is grouped with its early release
  let go at the window's first frame, not at the later frames the release may come at. The early
  release itself and every follow-up (`FollowupForm`) are still read by a defender who knows them
  from the attack's start: his roll of the opening is planned for the press at once, and a defender
  who cannot tell the release from the hold would need a rule for a release that may come on any
  frame of the window, which is not made.
- A follow-up's press: that the behavior script changes state on the frame the press is ready, that
  the window's last frame is not in it, the attacker's reaction (the defender's, `INFERRED`), that
  he delays only on a roll he saw before the window opened (a read made before the roll shows,
  `FollowupModel.outcome(read=True)`, is measured but not scored), and that the defender times his
  roll of the opening on the opening alone, plans for the press at once and reacts to a late one
  only from the press. The opening's own samples after his roll are not tested again, and a
  defender caught mid-roll is played as `CaughtModel` places him.
- The full count over-credits lingering areas. The robust class leaves out dominators live
  longer than `LONG_LIVE_FRAMES` or channelled; a shorter area that ticks is still counted whole.
- A held loop spell's FP per cast is a lower bound.
- An on-hit child launches in the game when its parent hits the defender; the straight flight hits
  no character, so its hits are left out (partial) rather than flown from the parent's contact.
- Direct-AtkParam spells have no reach model; two-handed openings are read one-handed.
- The cache stamp covers the regulation, the paramdefs, the blacklist, every loaded script, the
  HKS parser `er-hks-weapon-category-table.py` and the `common_define.hks` it reads (which pick
  every skill's opening clip), not the TAE/HKX extraction or the FMG name cache: `--refresh` after
  re-extracting either.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import dataclasses
import datetime
import functools
import hashlib
import html
import importlib.util
import io
import json
import math
import os
import random
import shutil
import statistics
import struct
import subprocess
import sys
import time
import tomllib
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _spells():
    """`er-mechanics-spells`, registered under the name its own `_mod` uses."""
    name = 'er_mechanics_spells'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / 'er-mechanics-spells.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


S = _spells()
EX = S._mod('er_mechanics_exchange', 'er-mechanics-exchange.py')
PROG = S._mod('er_mechanics_progress', 'er-mechanics-progress.py')
PR, T, AR = S.PR, S.T, S.AR


def _ai():
    """`er-mechanics-ash-interrupt`: the pool's core pinning and throttle."""
    return S._mod('er_mechanics_ash_interrupt', 'er-mechanics-ash-interrupt.py')


def _pool_lock():
    """`er-mechanics-pool-lock`: the one-pooled-run-at-a-time lock every checkout shares."""
    return S._mod('er_mechanics_pool_lock', 'er-mechanics-pool-lock.py')


def pool_held(jobs, what, wait=True, log=None):
    """A context holding `er-mechanics-pool-lock` for a pooled compute (`jobs` above 1), naming the
    run `what`; a single-core compute takes nothing (`contextlib.nullcontext`). Held by another run,
    it waits and says who holds it (pid and working directory, to `log`, default stderr), or with
    `wait` False exits 3."""
    import contextlib
    if jobs is None or jobs <= 1:
        return contextlib.nullcontext()
    return _pool_lock().held(what, wait=wait, log=log)


def _build_tags():
    """`er-mechanics-build-tags`: the planner's archetype rule and the corpus reader it imports."""
    return S._mod('er_mechanics_build_tags', 'er-mechanics-build-tags.py')


def _self_damage():
    """`er-mechanics-self-damage`: the HP a cast takes from its own user (`hp_cost_of`)."""
    return S._mod('er_mechanics_self_damage', 'er-mechanics-self-damage.py')


def _status():
    """`er-mechanics-status`: status build-up per hit and what a proc costs (`annotate_status`)."""
    return S._mod('er_mechanics_status', 'er-mechanics-status.py')


def hp_cost_of(cost):
    """The JSON-safe part of an `er-mechanics-self-damage.cost_rows` result an entity carries as
    'hp_cost': {'pct', 'flat', 'rows' (SpEffect ids), 'unpriced' (reasons)}, or None when the cast
    takes nothing and nothing is left unpriced."""
    if not cost or not (cost['pct'] or cost['flat'] or cost['unpriced']):
        return None
    return {'pct': round(float(cost['pct']), 4), 'flat': round(float(cost['flat']), 4),
            'rows': sorted({r['id'] for r in cost['rows']}), 'unpriced': sorted({u['why'] for u in cost['unpriced']})}


def hp_charge(x):
    """HP phase-2 input `x` (`profile_inputs`) pays per cast: its 'hp_cost' at its line's
    'max_hp' (`er-mechanics-self-damage.cost_at`), 0 for an entity that takes nothing or a
    component (which carries no 'hp_cost': the cast pays once, as the whole entity)."""
    c = (x or {}).get('hp_cost')
    return _self_damage().cost_at(c, x.get('max_hp')) if c else 0.0


_HP_AT_VIG = []


def line_hp(c, line=None, build=None):
    """The max HP an entity's cost is a share of at one line: an attacker build's own (its FP
    plan's 'hp', line-fit's, with the HP piece it carries; else its VIG on the game's curve), an
    adjusted objective line's attacker build's (it keeps that build's VIG, `END` and armor,
    `objective_row`), a minimum line's VIG on the curve, and for the distance basis, which has no
    VIG of its own, the RL window's median defender's (`INFERRED`: a mirror of the defender it is
    scored on)."""
    if build is None and line is not None and line.get('from_line'):
        build = next((b for b in c.builds if b['name'] == line['from_line']), None)
    if build is not None:
        plan = ((build.get('rule') or {}).get('fp_plan') or {})
        if plan.get('hp'):
            return float(plan['hp'])
    st = (build or {}).get('stats') or (line or {}).get('stats')
    if st and st.get('vig'):
        if not _HP_AT_VIG:
            _HP_AT_VIG.append(resource_curves()[0])
        return float(_HP_AT_VIG[0](int(st['vig'])))
    return float(c.dfn.get('hp') or 0.0)


#: Bump when what the cache holds changes meaning. 2: a partial spell's value keeps only the hits
#: with a reach model; Firebreather, Torch Attack and Buckler Parry ride their own weapons. 3: a hit
#: on a bullet the straight flight never launches is missing (or dropped, on water or never); a
#: follow-up is keyed by its press frame and leaves out the opening's hits from the press on; every
#: record carries its delivery; a tap is never channelled. 4: every attacker build of the RL
#: window's corpus is scored too, with its nets per situation (dominance reads those); a
#: follow-up's frames before its press, which are the opening's, get no component. 5: every scored
#: or partial entity carries its objective evaluation ('objective': `objective_lines`). 6: a
#: follow-up's nets are its delayed press's (`FollowupForm`), a spell's the better of its lock-on
#: and free aims per situation (`aims_won`), and homing shots are flown (`HOMING`). 7: a charged
#: skill's early release is a press in its window (`FollowupForm`); a spell's tap plays 045010 up
#: to its release first (`er-mechanics-spells.tap_release`). 8: the reacting defender sees only
#: what is on screen: a spell is read with the casts of its TimeAct and casting SFX the line casts,
#: and a charged skill's hold with its early release (`look_specs`); every record carries its
#: distance-basis group ('lookalikes'). 9: a release (a spell's tap, a hold spell let go early, the
#: new 'early' variant, and a charged skill's early release) is let go on a frame of its window
#: the attacker picks: one group member per release frame (`release_members`), the entity netting
#: their weighted mean; a charged skill's early release with no opening hit before it is read so
#: ('mixture', no longer `FollowupForm`); every release record carries its 'releases'. 10: the
#: attacker builds are lines made from the game's curves, the corpus giving usage alone ('n', the
#: weight carried), and each carries its 'rule' (`attacker_builds`). 11: every attacker line is
#: line-fit's, on its own kit (a weapon picked off the game for a melee line) and carrying only it;
#: and a defender free before the attack first touches him may strike instead of rolling
#: (`er-mechanics-spells.net_parts`, the module comment above `STRIKE_CARRIED`): his R1 interrupts
#: what has not left the attacker when it breaks the attacker's poise (each line's own,
#: `line_poise`), through the attack's TAE 795 windows (`TOUGHNESS`). 12: that strike meets the
#: attacker's own guard where the attack holds one up (TAE JumpTable 3, `GUARD`,
#: `er-mechanics-spells.strike_guard`): the guarded chip instead of the R1, no poise break, and the
#: attack ends only on a guard reaction or a guard break (Shield Bash, Shield Crash, Roaring Bash,
#: Moore's Charge, Revenge of the Night, Golden Retaliation, Flame Spit, Tongues of Fire). 13: every
#: net is less the HP the cast takes from its own user (Bloody Slash's 5% of max HP + 50, Briars of
#: Sin's 80 a launch: 'hp_cost', `er-mechanics-self-damage`), at each line's own max HP (`line_hp`).
#: 14: each hit of a shared bullet record is priced at the AtkParam of the bullet that landed it, and
#: a caught defender may also leave to either side (section 6b; Ghostflame Ignition then R1 926 -> 408).
#: 15: a skill's bullets land once per shared hit list and its attacks once per hit priority record
#: (`er-mechanics-ashes.cast_mask`, ashes-of-war.md section 20; Surge of Faith 4,275 -> 1,597 a cast).
#: 16: a defender runs no sooner than his move cancel (the HKS walk starts only through it, env
#: 2000): the whiffer who runs in to strike from his whiff's ('run' of each situation, Longsword
#: R1 #1 f29 against its dodge cancel f24) and the roller who runs in to punish from his roll's
#: (f22 against its R1 cancel f20; `er-mechanics-ashes.punish_lead`).
#: 17: an attack's contacts take the attacker's best free-aim pitch off lock-on (TAE 700
#: `EnableTwistModifier` over the camera pitch, `er-mechanics-reach.free_aim_pick`; Longsword
#: powerstance L1 #1 at -35 degrees reaches 3.709 -> 3.844 m and lands f14.5 -> f13.0 at 1.5 m).
#: 18: a caught defender takes a bullet list only where its own bullets touch him on his path, each
#: with its own poise and reaction (`er-mechanics-ashes.CaughtModel`): Rosus's Summons' three bursts,
#: 0.25 s apart at one spot, were all landed at once on any catch; now each breaks the pool's 62
#: poise (202.5 PvP) and holds him for the next, and a 700-poise defender leaves after the first.
#: Such a bullet chain is also one geometry per list (`er-mechanics-ashes.split_chains`, bd
#: er-effects-rs-fse5), so a dodger first touched by burst 2 is credited bursts 2-3, not all three.
#: 19: an attacker build or objective line rides the group weapon it can wield whose skill damage
#: (opening and follow-ups summed, `carrier_damage`) is largest, not the one of least requirement:
#: Glintstone Pebble then R2 on Intelligence + melee 964.2 on the Dagger -> 1245.1 on the Omen
#: Cleaver, Double Slash 313.3 -> 519.9 on the Uchigatana (`skill_option` damage, `MEASURED`
#: 2026-11-16).
#: 20: a shot a search launches (the Glintblade Phalanx and Carian Phalanx blades: hover at least
#: `er-mechanics-ashes.SEARCH_ARM_S`, then launch when the bullet's search, facing the caster's
#: facing, finds the target, `VERIFIED`) is read by the defender from that launch, not the cast
#: (`er-mechanics-ashes.burst_cue`); and a follow-up nets the mixup's value (`FollowupForm.mixup`:
#: the defender's roll or stand against the attacker's press policy at every default delay, at
#: `STRAFE_SHARE` 0, `INFERRED`), not one fixed sequence. Section 2a.
#: 21: a caught defender stays in his whiff's recovery until its cancels let him go (a roll from the
#: situation's dodge floor, a run from its move floor; a reaction that locks him ends it), and holds
#: with each pool build's own poise (Bull-Goat's `altered`, else `original`), not the median
#: `original` (`er-mechanics-ashes.CaughtModel`, section 6). The strafe row reads the move floor
#: itself, not the situation key's, which is the dodge floor up close. One cast is capped at each
#: pool build's own health, not the median Vigor's; a caught defender may jump out of `AIR` hits; a
#: same-frame tie in one hit priority record goes by priority in `_catches` too; a hit breaks poise
#: at `>=` (skill_landing as well).
#: 22: a Phalanx blade is one hit list over its hover and flight; the defender picks his roll
#: direction; the punish and the strike read where the attacker's root motion left him; a
#: follow-up is read in its opening's lookalike group. 2a.
#: 23: the defender's one option set and reaction rule (stand, cue, latency, strike place; section 2g).
#: 24: 21, 22 and 23 together.
#: 25: dodges aimed for escape and punish; catches in a dodge played from it; stand row sees him leave.
#: 26: each escaping roll carries the defender into the next burst (`er-mechanics-ashes.dodge_chain`:
#: on its root motion to its dodge cancel, where it leaves him, free to dodge again only from its
#: start plus that cancel, the attacker turning to face him before each later burst); the same in
#: the follow-up (`FollowupModel`, its opening roll and planned dodges) and the volley path; every
#: follow-up catch in a dodge of his priced from that dodge; a roll row's press the opening caught
#: met by the attacker who sees the caught defender leave (`FollowupModel._stand_values`'s rule);
#: the exchange model's dodges aimed for the punish too (`Opponents._table`). Section 2i.
#: 27: the roll's whole clip carries him until he next acts (no stop at its dodge cancel); the
#: attacker's facing follows each clip's TAE 224 / JumpTable 7 turn rules every update, within a
#: burst and between bursts, for every touch read and the caught defender's play
#: (`er-mechanics-ashes.clip_turn`, `_facing`), a roll out of a track taken facing him (`oriented`);
#: the volley on the shared chain with which threats landed together (`_volley_chain`); the
#: follow-up's tables fly a homing geometry and its opening bursts past the last that reaches him
#: standing are read; the exchange model picks his direction per opener and rolls again for the
#: chained hit (`Opponents._second_roll`). Section 2j.
#: 28: every press of the defender carries his timing error, one he presses at his reaction
#: included (`er-mechanics-ashes.pressed`; until then an early error was clamped to the reaction, so
#: a defender reacting to a later event pressed with none and could do better than one who knew the
#: attack): his dodges in every stage (`_AimTable`), his strike (`er-mechanics-spells.strike_segments`)
#: and his strafe. Only his recovery's floor truncates a press. Section 2k.
#: 29: a quick spell's cast while sprinting ('dash') waits on the dodge-button hold (0.4 s) left
#: when the window opens, after what his run back already held (`pairs_of`,
#: `er-mechanics-spells.sprint_situations`); until then it was pressed as if sprinting already.
#: 31: the full worth the land shares divide by counts a hit priority record's hits once
#: (`er-mechanics-spells.full_worth`), as `CaughtModel` lands them: Starcaller Cry's five nested
#: opening spheres were five times its worth there (1879.9 HP, now 376.0), and an attack whose later
#: launches the record holds back had land shares below what lands (Gavel of Haima's largest 0.233,
#: now 0.417). The status full worth leaves the held-back hits out too (`status_full`). The nets do
#: not move (attack and build nets bit-identical, `MEASURED` 2026-11-23 with perf-similarity-dump.py).
#: 32: a bleed or frost proc on a caught defender whose poise held stuns him: HKS `ExecDamage` raises
#: the hit to `DAMAGE_LEVEL_SMALL` (`COMMUNITY`), so his escape waits for the small stagger's cancel
#: (a roll from frame 10, 7 / 4 / 0 on the 2nd to 4th in a row) and the later hits land on him held
#: there, in every stage that plays him (`er-mechanics-spells.StatusCaught`, the comment above
#: `STATUS_WORTH_FLOOR` there). Until then a proc was priced as its HP alone. An attack that builds
#: neither, or never enough to proc on the pool, reads the same plays as before. The nets of Zamor
#: Ice Storm, Freezing Mist and Corpse Piler do not move (0 of 436 situations each, every 8th,
#: `MEASURED` 2026-11-23 with er-mechanics-form-check.py): their procs come only in escapes the
#: defender does not take (enough ticks to empty a robustness of 173 or more mean he stayed, and his
#: best escape leaves first), or on a hit that already staggers him (Corpse Piler's second). The
#: whole similarity output of those three, Bloody Slash, Stars of Ruin and Carian Slicer is bit-
#: identical to version 31's (perf-similarity-dump.py, 120 arrays), at 1.5x to 2.5x the CPU on the
#: stun attacks (Freezing Mist tap 2,377 -> 3,818 s, two dumps run side by side).
SIMILARITY_VERSION = 32
#: The distance basis: every damage stat 99, so every attack is wieldable and castable. No
#: character at the RL window has it (with 10 `VIG`, `MND` and `END` it is RL 446), so nothing that
#: compares damage or FP is read on it: dominance is read per attacker build (`attacker_builds`).
DEFAULT_STATS = 'str=99,dex=99,int=99,fth=99,arc=99'
#: The eight attributes, and the RL they give: their sum less `LEVEL_OFFSET` (the Wretch's ten 10s
#: are RL 1; the selftest checks it on every starting class against `CharaInitParam.soulLv`).
ATTRS = ('vig', 'mnd', 'end', 'str', 'dex', 'int', 'fth', 'arc')
LEVEL_OFFSET = 79
#: `CharaInitParam` base field per attribute, read on the starting classes' rows.
CLASS_FIELDS = {'vig': 'baseVit', 'mnd': 'baseWil', 'end': 'baseEnd', 'str': 'baseStr', 'dex': 'baseDex',
                'int': 'baseMag', 'fth': 'baseFai', 'arc': 'baseLuc'}
CLASS_ROWS = range(3000, 3012)
#: The starting classes' rows the row-name table does not name (the Tarnished Pack's two, as
#: `er-mechanics-resources.CLASS_ROWS` names them after crates/er-build-import-core/src/class.rs).
CLASS_NAMES = {3010: 'Idus Knight', 3011: 'Heavy Knight'}
#: The corpus group whose stats `stat_archetype` gives no tag: counted as usage, made into no line.
NO_ARCHETYPE = 'none'
#: The highest an attribute goes.
STAT_MAX = 99
#: An attacker line's main stats stop at the second soft cap of the scaling graphs, `stageMaxVal3`
#: (`er-mechanics-ar.SOFT_CAP_STAGE`): 80 on the STR and DEX graphs 0 and 1 and on graph 16, the one
#: both catalysts' spell buff reads (`REGULATION`; graph 16 gives 55 points over 18 to 60, then 15
#: over 60 to 80; the selftest checks the 80). `MEASURED` on the Glintstone Staff +25: sorcery
#: scaling +3.3 for INT 59 -> 60, then about +2.0 a point to 80.
DAMAGE_CAP = 80
#: The split lines' main stat stops at the first soft cap, `stageMaxVal2`: 60 on the same graphs.
SPLIT_CAP = 60
#: STR and DEX, the stats the weapons a line carries ask for (`REGULATION`, 2026-11-12: of the 597
#: named base weapons, 517 require STR, 409 DEX, 108 FTH, 84 INT and 33 ARC): a line's points past
#: its main stats' cap go to them.
WEAPON_STATS = ('str', 'dex')
#: The spell each school's lines cast (`line_kit`): `er-mechanics-line-fit.LINES`' own, Rock Sling
#: and Lightning Spear, 18 FP each (`INFERRED` there: a PvP staple of the school), from the first of
#: the run's catalysts that casts it.
SCHOOL_LINE = {'int': 'Intelligence', 'fth': 'Faith'}
#: A split line's share of its attacks that are its weapon's R1, the rest its spell (`INFERRED`: no
#: match log says how a split build mixes them).
SPLIT_MELEE_SHARE = 0.5
#: CalcCorrectGraph the Mind fit reads a stat's damage curve on (`stat_curve`): 16 for INT and FTH,
#: the graph both catalysts' spell buff reads (`REGULATION`), 0 for STR, DEX and ARC, the physical
#: graph most weapons read (`INFERRED`: a skill's carrier and affinity have graphs of their own).
FIT_GRAPH = {'str': 0, 'dex': 0, 'int': 16, 'fth': 16, 'arc': 0}
#: The attacker lines (`attacker_builds`): name, main stats (raised evenly), their cap, and the
#: corpus archetypes whose median carried weight is reported beside the line as usage, the larger
#: (never carried: a line carries its kit, `line_kit`). A line is made when the corpus has its
#: first archetype at the RL window. The last two answer the user's question of 2026-11-12, "are
#: [mages] optimal with enough int where they don't have stat points to get melee weapons?": the
#: main stat stops at `SPLIT_CAP` and the rest goes to STR and DEX.
ATTACKER_LINES = (
    ('Strength', ('str',), DAMAGE_CAP, ('Strength',)),
    ('Dexterity', ('dex',), DAMAGE_CAP, ('Dexterity',)),
    ('Quality', ('str', 'dex'), DAMAGE_CAP, ('Quality',)),
    ('Intelligence', ('int',), DAMAGE_CAP, ('Intelligence',)),
    ('Faith', ('fth',), DAMAGE_CAP, ('Faith',)),
    ('Arcane', ('arc',), DAMAGE_CAP, ('Arcane',)),
    ('Hybrid', ('int', 'fth'), DAMAGE_CAP, ('Hybrid',)),
    ('Intelligence + melee', ('int',), SPLIT_CAP, ('Intelligence', 'Quality')),
    ('Faith + melee', ('fth',), SPLIT_CAP, ('Faith', 'Quality')),
)
#: How far `OutcomeForm.net` may be from `net_parts`' `net` (stored as float32) on the same inputs:
#: absolute HP plus a share of the net. A form that disagrees by more stops the run.
FORM_TOL = (1e-3, 1e-5)
DEFAULT_CATALYSTS = ('Glintstone Staff+25', 'Finger Seal+25')
#: `EquipParamGoods.goodsType` of a spell item (sorcery, incantation and their DLC types).
SPELL_GOODS_TYPES = (5, 16, 17, 18)
#: SwordArtsParam rows that are no skill: none, the default, `No Skill`.
NOT_A_SKILL = (0, 1, 10)
#: Components kept per entity, the most valuable frames first.
MAX_COMPONENTS = 8
#: A geometry live longer than this many frames is counted whole (`full`), which over-credits a
#: lingering area: the frontier warns about it.
LONG_LIVE_FRAMES = 60
#: A land or punish share above this counts as the attack doing something in a situation.
LIVE_EPS = 0.005
#: `STATUS_BUILDUP` field -> token name.
STATUS_TOKEN = {'poizonAttackPower': 'poison', 'diseaseAttackPower': 'rot', 'bloodAttackPower': 'bleed',
                'curseAttackPower': 'death', 'freezeAttackPower': 'frost', 'sleepAttackPower': 'sleep',
                'madnessAttackPower': 'madness'}
#: `SpEffectParam.stateInfo` of the pull / push rows (SpEffect 470 on every gravity bullet,
#: `REGULATION`); the applying AtkParam's `knockbackDist` sign says which way.
STATE_INFO_PULL = 398
#: `EquipParamWeapon.wepType` of the greatbows and ballistas, which are only held two-handed
#: (`INFERRED` from the game's equip rule): their skill's reach is read one-handed here.
TWO_HAND_ONLY_WEP_TYPES = (53, 56)
#: D_assoc cuts of the sensitivity table.
SENSITIVITY_CUTS = (0.05, 0.1, 0.2, 0.3)
#: Profile channels of D_rel, in this order: pressed at once, then the best option.
REL_CHANNELS = ('land_now', 'pun_now', 'land', 'pun')
#: Profile channels of D_land and D_contain: what lands, pressed at once and with the best option.
LAND_CHANNELS = ('land_now', 'land')
#: `--idle-rate` default: D_idle's weight in D_assoc and D_contain. At 0.5 a pair within `--twin`
#: 0.1 reaches an idle defender within D_idle 0.2 (`MEASURED`, round 2 full run: the same-bullet
#: pairs sit at 0.061 to 0.138, the melee-against-projectile false twins at 0.229 to 0.99).
IDLE_RATE = 0.5
#: Spell variants that hold into a loop clip, whose FP is the per-tick cost (J3).
HELD_VARIANTS = ('charged',)
WEAPON_NEED = {'str': 'properStrength', 'dex': 'properAgility', 'int': 'properMagic', 'fth': 'properFaith',
               'arc': 'properLuck'}
#: `EquipParamWeapon` scaling field per damage stat (`er-mechanics-ar.STAT_FIELDS`' second column).
WEAPON_SCALING = {'str': 'correctStrength', 'dex': 'correctAgility', 'int': 'correctMagic', 'fth': 'correctFaith',
                  'arc': 'correctLuck'}
#: The stat a spell's damage scales with, per `SpellModel.school` flag: INT for a sorcery, FTH for an
#: incantation. A catalyst's own correct* fields are not read (the Glintstone Staff's
#: `correctStrength` 12 and the Finger Seal's 15, `REGULATION`).
SCHOOL_STAT = {'enableMagic': 'int', 'enableMiracle': 'fth'}
#: The objective evaluation's lines (`objective_lines`), in the order they are shown.
OBJECTIVE_LINES = ('adjusted', 'minimum')
BULLET_EXTRA = ['homingAngle', 'sfxId_Bullet'] + [f'spEffectId{i}' for i in range(5)]
ATK_EXTRA = ['disableGuard', 'throwTypeId', 'statusAilmentAtkPowerCorrectRate',
             'statusAilmentAtkPowerCorrectRate_byPoint', 'isInvalidatedByNoDamageInAir', 'knockbackDist',
             'disableHitSpEffect', 'opposeTarget', 'friendlyTarget', 'selfTarget']
#: `Magic.refCategory` of a SpEffect ref (er-mechanics-spells module doc: 0 AtkParam_Pc, 1 Bullet,
#: 2 SpEffect).
REF_SPEFFECT = 2
#: `BehaviorParam_PC.refType` of an AtkParam_Pc row and of a Bullet row (er-mechanics-ashes module
#: doc; er-duel-rules-gen reads 1 as a bullet the same way).
BEH_ATTACK, BEH_BULLET = 0, 1
#: Per-situation arrays of one profile (`profile_arrays`).
ARRAYS = ('land_now', 'pun_now', 'base_now', 't_first', 'land', 'pun', 'net', 'net_one', 'gap')
ERRORS = (KeyError, TypeError, ValueError, StopIteration, SystemExit, IndexError, ZeroDivisionError)


# ---------------------------------------------------------------------------------------------
# the build and the situations

class Context:
    """The model, the distance basis build, the attacker builds and the situations every entity is
    scored in. `builds` None makes the attacker builds from the game's curves and the corpus's
    usage (`attacker_builds`, `corpus_usage`), then one more per spread of `lines` ([`parse_line`],
    `--line`; `given_builds`), which needs the line fit's world and so only goes with `builds`
    None."""

    def __init__(self, stats, catalysts=DEFAULT_CATALYSTS, rl=150, include_blacklisted=False, builds=None,
                 lines=()):
        self.SM = S.SpellModel()
        self.A, self.t = self.SM.ash()
        # Everything the outcome reads lazily, loaded now: the workers inherit it and the code stamp
        # covers it.
        self.A._pose(), self.A._neutral(), self.A.roll_motion(), self.A.jump_motion()
        # The poise rule the defender's strike reads (`er-mechanics-spells.strike_breaks`).
        S._interrupt()
        self.stats = dict(stats)
        self.catalysts = list(catalysts)
        self.cats = [self.SM.catalyst(c) for c in self.catalysts]
        self.rl = rl
        self.include_blacklisted = include_blacklisted
        self.situations = S.punish_situations(rl)['situations']
        # (distance, dodge-cancel floor, move-cancel floor): `net_parts`' situations.
        self.pairs = [(s['distance'], s['recovery'], s['run']) for s in self.situations]
        # The same situations met by a cast pressed while sprinting (a quick spell's 'dash'
        # variant, `sprint` in `profile_inputs`): `er-mechanics-spells.sprint_situations`.
        self.sprint_pairs = S.sprint_situations(self.situations)
        self.w = np.array([s['weight'] for s in self.situations], float)
        self.W = float(self.w.sum())
        self.n_eff = n_eff(self.w)
        self.strikes = S.punish_strikes(rl)
        self.dfn = self.SM.defender(rl)
        # The status chain (`annotate_status`, version 30): `er-mechanics-status`'s tables on the
        # model's own attack-rating tables.
        self.st = _status().Tables(ar_tables=self.SM.ar)
        self.F = (self.A.REACTION_MEDIAN_S + 2 * self.A.NETWORK_ONE_WAY_S) * S.FPS
        files = self.SM.cast.t._files

        def by_id(stem, fields):
            return {r['id']: r for r in PR.rows(PR.param_bytes(files, stem), fields)[0]}
        self.bx = by_id('Bullet', BULLET_EXTRA)
        self.ax = by_id('AtkParam_Pc', ATK_EXTRA)
        # A status-only spell's hits count, flown and scored as hits of no damage (version 30).
        self.status_atks, self.status_atks_shared = status_hit_atks(self)
        self.SM.status_atks = frozenset(self.status_atks)
        # What a SpEffect's `behaviorId` makes (`behavior_damage`).
        self.beh = by_id('BehaviorParam_PC', ['refType', 'refId'])
        self.goods = by_id('EquipParamGoods', ['goodsType', 'sortId'])
        self.subcat = {mid: self.SM.cast.magic.get(mid, {}).get('subCategory1', 0) for mid in self.SM.names}
        self.black = blacklist()
        names = PR.row_names('CharaInitParam')
        rows = PR.rows(PR.param_bytes(files, 'CharaInitParam'), ['soulLv'] + list(CLASS_FIELDS.values()))[0]
        self.classes = {(names.get(r['id']) or CLASS_NAMES.get(r['id']) or str(r['id'])).removeprefix('Class - '):
                        {'level': r['soulLv'], **{k: r[f] for k, f in CLASS_FIELDS.items()}}
                        for r in rows if r['id'] in CLASS_ROWS}
        #: Per attribute, the lowest base of any starting class: the floor of the objective lines.
        self.class_floor = {k: min(cl[k] for cl in self.classes.values()) for k in ATTRS}
        self.usage = None
        #: The melee poise floor the made lines' armor is read at (`given_builds`' default).
        self.poise_floor = None
        hp, load, fp = resource_curves()
        if builds is None:
            # Every line is line-fit's on its own kit (`fitted_lines`); the corpus gives usage: the
            # archetypes, their counts and, reported only, the weight their builds carry.
            wnames = PR.row_names('EquipParamWeapon')
            weight = {}
            for wid, r in sorted(by_id('EquipParamWeapon', ['weight']).items()):
                if wnames.get(wid):
                    weight.setdefault(wnames[wid], float(r['weight']))
            AT = S._mod('er_mechanics_armor_targets', 'er-mechanics-armor-targets.py')
            floor = AT.melee_poise_floor(AT.pool_rows(rl - S.WINDOW, rl + S.WINDOW)[0], AT.ATK.Regulation(None))[0]
            cands = AT.armor_candidates(AT.DEF.Tables())
            self.armor = lightest_armor(cands, floor)
            self.poise_floor = floor
            self.usage = corpus_usage(rl, weight=weight)
            # One Mind and FP model: `er-mechanics-line-fit` fits the lines it can express and
            # prices every line's FP. The world is local, so it is gone once the lines are made and
            # the workers that fork this context do not carry it.
            F = _line_fit()
            world = F.GameWorld(rl)
            fitted = fitted_lines(F, world, self.usage, catalysts=self.catalysts)

            def plan(stats):
                return fp_plan(F, world, stats, self.armor['pieces'] if self.armor else [])
            self.builds = attacker_builds(rl, self.usage, self.classes, hp, load, self.armor, floor,
                                          mind=self.mind_fitter(fp, plan), fitted=fitted, plan=plan)
            if lines:
                self.builds += given_builds(lines, self.classes, rl, load, lambda p: lightest_armor(cands, p),
                                            lambda st, pieces: fp_plan(F, world, st, pieces), floor,
                                            [b['name'] for b in self.builds])
        else:
            if lines:
                raise ValueError('attack similarity: --line spreads are made with the attacker builds, not beside '
                                 'builds given whole')
            self.builds = [dict(b) for b in builds]
        for b in self.builds:
            b.update(reach=reachable(b['stats'], self.classes, rl))

    def line_attacks(self, stats, spell_ids, skill_jobs):
        """[(FP cost, (kind, id))]: the FP-costing attacks attribute line `stats` can use, of the
        phase-0 candidates: each spell it casts (`cast_short`) at `Magic.mp`, each skill opening
        whose group weapon it wields (`build_job`) at its L2 charge (`er-mechanics-ashes.
        skill_fp`)."""
        st = damage_stats(stats)
        A, t = self.SM.ash()
        out = [(float(self.SM.magic[m]['mp']), ('spell', m)) for m in spell_ids if not self.cast_short(m, st)]
        for j in skill_jobs:
            jb = build_job(self, j, st)
            if jb is not None:
                out.append((float(A.skill_fp(t, j['sid'])), ('skill', jb['carrier'])))
        return [(cst, key) for cst, key in out if cst > 0]

    def mind_fitter(self, fp, plan):
        """The `mind` `attacker_builds` takes for a line whose fit does not price its FP (a weapon
        line: its R1 costs none): `fit_mind` on the attacks its MND-at-base line can use
        (`line_attacks`, of the full phase-0 candidates whatever `--only` keeps), each paid on that
        line's FP plan (`at`, the fit's, else `plan(stats)`; `fp_plan_paid`) at max FP `fp(mnd)`,
        its HP, defense and poise held at the MND-at-base
        line's (`INFERRED`: a point moved into MND moves flat defense a little); each attack's
        damage the catalyst's multiplier at the line for a spell (`SpellModel.multiplier`, its
        school's element) and, for a skill, its carrier's `WEAPON_SCALING` fields over
        `stat_curve` (`INFERRED`)."""
        RES = S._mod('er_mechanics_resources', 'er-mechanics-resources.py')
        model = RES.Model()
        spell_ids, _ = spell_candidates(self)
        skill_jobs, _ = skill_candidates(self)

        def damage(key, line):
            kind, x = key
            st = damage_stats(line)
            if kind == 'spell':
                el = 'magic' if self.SM.school[x] == 'enableMagic' else 'holy'
                return self.SM.multiplier(self.catalyst_for(x), el, st)
            w = self.SM.ar.weapons[x]
            return 1.0 + sum(w[f] / 100.0 * (stat_curve(model, k, st[k]) - 1.0) for k, f in WEAPON_SCALING.items())

        def mind(name, main, cap, base, total, vig, end, at=None):
            first = allocate_line(base, total, vig, end, main, cap)
            if first is None:
                return None, None
            attacks = self.line_attacks(first, spell_ids, skill_jobs)
            at = at or plan(first)
            memo = {}

            def share(cost, bar):
                if (cost, bar) not in memo:
                    memo[cost, bar] = fp_plan_paid(at, cost, bar)['share']
                return memo[cost, bar]
            line, facts = fit_mind(base, total, vig, end, main, cap, attacks, fp, share, damage)
            if facts is not None:
                worst = max(attacks, default=(0.0, None))[0]
                facts.update(attacks=len(attacks), dearest=worst,
                             paid_dearest=fp_plan_paid(at, worst, facts['bar']))
            return line, facts
        return mind

    def spell_goods(self, mid):
        g = self.goods.get(mid)
        return bool(g) and g['goodsType'] in SPELL_GOODS_TYPES and g['sortId'] != S.UNOBTAINABLE_SORT_ID

    def requirement(self, wid):
        w = self.SM.ar.weapons[wid]
        return sum(w[f] for f in WEAPON_NEED.values())

    def wields(self, wid, stats=None):
        """Whether `stats` (default the context's) meet weapon `wid`'s requirements, one-handed. A
        requirement is read against base stats: whether the game's check counts a talisman's or a
        great rune's stat boost is not established."""
        stats = self.stats if stats is None else stats
        w = self.SM.ar.weapons[wid]
        return all(stats.get(k, 0) >= w[f] for k, f in WEAPON_NEED.items())

    def catalyst_for(self, mid):
        """The first catalyst of the run that casts spell `mid`'s school, or None."""
        return next((cc for cc in self.cats if self.SM.can_cast(mid, cc)), None)

    def cast_short(self, mid, stats=None):
        """{what: shortfall} that keeps `stats` (default the context's) from casting spell `mid`:
        the spell's own requirements, and the requirements of the catalyst it is cast with (a
        catalyst whose requirement is not met casts nothing, `COMMUNITY`). Empty when it can."""
        stats = self.stats if stats is None else stats
        out = dict(self.SM.short(mid, stats))
        cat = self.catalyst_for(mid)
        for k, f in WEAPON_NEED.items():
            if cat is not None and stats.get(k, 0) < cat['wep'][f]:
                out[f"{cat['name']} {k}"] = cat['wep'][f] - stats.get(k, 0)
        return out


def damage_stats(line):
    """The five damage stats of an attribute line, what the models read."""
    return {k: int(line[k]) for k in WEAPON_NEED}


def at_build(c, stats):
    """`c` at another build's damage stats: the same model, catalysts, defender and situations."""
    out = copy.copy(c)
    out.stats = dict(stats)
    return out


def carried_weapons(doc, weight):
    """(weight, unknown) of the weapons planner build `doc` has in its hands' slots: every
    `inventory` slot with an `equipSet`, both hands' three, so the swaps are in it. `unknown`
    counts the slots whose name `weight` ({weapon name: EquipParamWeapon.weight}) has no row for.
    None for a build with more than one weapon set, whose `equipSet` numbers sets rather than
    slots (260 of 4,576 corpus builds; `MEASURED` 2026-11-12)."""
    if len((doc.get('sets') or {}).get('weapons') or [None]) > 1:
        return None
    got, unknown = 0.0, 0
    for s in (doc.get('inventory') or {}).get('slots') or []:
        if not s.get('equipSet'):
            continue
        w = weight.get(s.get('name'))
        if w is None:
            unknown += 1
        else:
            got += w
    return got, unknown


def corpus_usage(rl, mirror=None, weight=None):
    """What the corpus is read for, usage alone: [{'name' (the archetype), 'n' (builds), 'carried',
    'carried_n'}], commonest first. The build-planner corpus the opponent pool comes from
    (`er_corpus.iter_rows`, so nothing saved before its floor), its builds not marked PvE whose RL
    (the eight attributes of the document's `stats`, `PLANNER_KEY` spelling, less `LEVEL_OFFSET`:
    base stats, before talismans) is in the RL window, grouped by
    `er-mechanics-build-tags.stat_archetype` on those stats (`NO_ARCHETYPE` when it gives no tag),
    not deduplicated by user. With `weight` ({weapon name: weight}) a group also has 'carried', its
    median weight of the weapons in its hands (`carried_weapons`, lower middle value, over the
    builds that carry any) and 'carried_n', how many builds that is read on. No stat line is read:
    the attacker lines are the game's curves' (`attacker_builds`)."""
    BT = _build_tags()
    path = Path(mirror) if mirror is not None else EX.CACHE / 'builds.jsonl'
    if not path.exists():
        return []
    lo, hi = rl - S.WINDOW, rl + S.WINDOW
    groups, carried = Counter(), {}
    for row in BT.er_corpus.iter_rows(path):
        b = row.get('build') or {}
        if b.get('isPvE') is True:
            continue
        st = b.get('stats') or {}
        base = {k: int(st.get(BT.PLANNER_KEY.get(k, k)) or 0) for k in ATTRS}
        level = sum(base.values()) - LEVEL_OFFSET
        if not lo <= level <= hi:
            continue
        name = BT.stat_archetype(base, level) or NO_ARCHETYPE
        groups[name] += 1
        got = carried_weapons(b, weight) if weight is not None else None
        if got is not None and got[0] > 0:
            carried.setdefault(name, []).append(got[0])
    out = []
    for name in sorted(groups, key=lambda g: (-groups[g], g)):
        u = {'name': name, 'n': groups[name]}
        if carried.get(name):
            u.update(carried=round(float(statistics.median_low(carried[name])), 1), carried_n=len(carried[name]))
        out.append(u)
    return out


def resource_curves(model=None):
    """(hp, load, fp): max HP at a VIG, max equip load at an `END` and max FP at a MND, everything
    else 10, no talismans (`er-mechanics-resources.Model.resources`: CalcCorrectGraph `GRAPH_HP`
    100, `GRAPH_EQUIP_LOAD` 220 and `GRAPH_FP` 101). Each depends on its one attribute alone."""
    RES = S._mod('er_mechanics_resources', 'er-mechanics-resources.py')
    model = model or RES.Model()
    ten = {k: 10 for k in ATTRS}
    hp = {v: model.resources({**ten, 'vig': v})['max_hp'] for v in range(1, STAT_MAX + 1)}
    load = {e: model.resources({**ten, 'end': e})['max_equip_load'] for e in range(1, STAT_MAX + 1)}
    fp = {m: float(model.resources({**ten, 'mnd': m})['max_fp']) for m in range(1, STAT_MAX + 1)}
    return hp.__getitem__, load.__getitem__, fp.__getitem__


def _line_fit():
    """`er-mechanics-line-fit`, the one line fit and FP model (`fitted_lines`, `fp_plan_paid`). It
    reads this module back as `er_mechanics_attack_similarity`, so this copy is registered under
    that name first and it does not load a second one."""
    sys.modules.setdefault('er_mechanics_attack_similarity', sys.modules[__name__])
    return S._mod('er_mechanics_line_fit', 'er-mechanics-line-fit.py')


def fp_plan(F, world, stats, pieces, casts=None, crimson=None):
    """The FP plan a line of attribute line `stats` wearing armor `pieces` (no HP piece, no
    talismans) is paid on (`fp_plan_paid`): `er-mechanics-line-fit`'s pieces for it. {'casts',
    'crimson' (`Line`'s defaults unless given), 'hp' (max HP), 'hit' (`GameWorld.incoming`, the
    pool's mean hit after the set's absorption and the stats' flat defense), 'hits' (`hits_at`,
    hits landed per pool attack at the set's menu poise, carried chip), 'menu_poise', 'bar' (max
    FP), 'fight' (`GameWorld.fight`)}."""
    st = world.named_set(pieces)
    eff = {k: stats[k] + st['adds'][k] for k in ATTRS}
    level = world.total + sum(st['adds'].values())
    cut = st['cut'] * st['eff']
    hit = float(world.incoming(np.array([level]), {k: np.array([eff[k]]) for k in F.ELEMENT_STAT}, cut[None, :])[0])
    menu = st['armor_poise'] / st['tcut']
    hits = float(world.hits_at(np.array([menu]), True)[0][0])
    res = world.res.resources(stats)
    dflt = F.Line('x', 'int', '', '')
    return {'casts': dflt.casts if casts is None else casts, 'crimson': dflt.crimson if crimson is None else crimson,
            'hp': float(res['max_hp']), 'hit': round(hit, 3), 'hits': round(hits, 5), 'menu_poise': round(float(menu), 1),
            'bar': float(res['max_fp']), 'fight': world.fight}


def fp_plan_paid(plan, cost, bar=None):
    """How FP plan `plan` (`fp_plan`; `bar` in place of its own) pays an attack of `cost` FP
    thrown `plan['casts']` times an engagement, as `er-mechanics-line-fit.score` prices a spell
    (one model for every line): one bar, then the FP items between engagements, then blue flasks
    of min(220, bar), at most one bar spent in an engagement, and the count of blue flasks the one
    that deals most before death, each costing a Crimson (`crimson` pricing). Every cast spends
    its FP: the similarity nets price whether it lands (`INFERRED`: `fit` spends FP only on the
    casts not broken in their windup). {'share' (of the casts paid), 'cerulean', 'crimson', 'life'
    (engagements), 'want' (FP an engagement)}; a free attack is always paid."""
    if not cost or cost <= 0:
        return {'share': 1.0, 'cerulean': 0, 'crimson': None, 'life': None, 'want': 0.0}
    F = _line_fit()
    one = np.array([1.0])
    D, share, N, _, s = F.score(one, float(cost), plan['casts'], one, np.array([plan['hp']]), np.array([plan['hit']]),
                                np.array([plan['hits']]), np.array([plan['bar'] if bar is None else bar]),
                                plan['fight'], plan['crimson'])
    k = int(np.argmax(D[0]))
    return {'share': float(share[0, k]), 'cerulean': k, 'crimson': int(plan['fight']['flasks']) - k,
            'life': float(N[0, k]), 'want': float(s[0, 0])}


def paid_share(rule, cost):
    """The share of an attack of `cost` FP an attacker build pays on its 'rule' ('fp_plan',
    `fp_plan_paid`); 1 for a build with none (a cache made before version 10)."""
    plan = (rule or {}).get('fp_plan')
    if not plan or cost is None:
        return 1.0
    return fp_plan_paid(plan, float(cost))['share']


def line_poise(c, line=None, build=None):
    """The menu poise the defender's strike reads of an attacker line (`er-mechanics-spells`
    `strike_breaks`): an attacker build's own set ('rule' 'fp_plan' 'menu_poise', its armor's poise
    over its toughness cut: line-fit's armor for a fitted line), an objective line's attacker line's
    (an adjusted line keeps its VIG, `END` and armor, `objective_row`), and None for a line with no
    armor of its own (a minimum line, the distance basis): `net_parts`' default, the RL window's
    median build (`SpellModel.defender` 'poise', `INFERRED`)."""
    if build is None and line is not None and line.get('from_line'):
        build = next((b for b in c.builds if b['name'] == line['from_line']), None)
    plan = ((build or {}).get('rule') or {}).get('fp_plan') or {}
    return float(plan['menu_poise']) if plan.get('menu_poise') is not None else None


def stat_curve(model, stat, value):
    """1 + CalcCorrectGraph `FIT_GRAPH[stat]` at `value` / 100: a stat's damage multiplier at a
    correct rate of 100 (`er-mechanics-ar.stat_multiplier`'s shape, `INFERRED` for the skills the
    Mind fit weighs)."""
    return 1.0 + model.calc_correct(FIT_GRAPH[stat], value) / 100.0


def fit_mind(base, total, vig, end, main, cap, attacks, bar_at, share, damage):
    """(line, facts): the MND that, at fixed RL, best trades a point of the stat it is paid from
    against what it buys: the MND of the line `allocate_line` makes on base attributes `base`
    whose value is highest, ties to the lowest MND, searched from the base up while any attack is
    unpaid. Raising MND by one gives `allocate_line` one point less, so it is paid from the last
    stat the rule raised: STR or DEX past the main stats' cap, else the main stats. The value is
    the mean over `attacks` ([(FP cost, key)], the FP-costing attacks the line can use) of
    `share(cost, bar)` x `damage(key, line)`, every attack weighed alike (`INFERRED`: no net
    exists before the run to pick the ones the line throws most), at bar `bar_at(mnd)`. Facts:
    {'mnd', 'base', 'bar', 'from' {stat: points}, 'value', 'value_base', 'share', 'share_base',
    'searched'}."""
    m0 = int(base['mnd'])
    first = allocate_line(base, total, vig, end, main, cap)
    if first is None:
        return None, None

    def value(line, bar):
        if not attacks:
            return 1.0, 1.0
        ps = [share(cst, bar) for cst, _ in attacks]
        return statistics.fmean(p * damage(key, line) for p, (_, key) in zip(ps, attacks)), statistics.fmean(ps)
    v0, s0 = value(first, bar_at(m0))
    best, m, now = (v0, m0, first, s0), m0, s0
    while now < 1.0 and m < STAT_MAX:
        line = allocate_line({**base, 'mnd': m + 1}, total, vig, end, main, cap)
        if line is None:
            break
        m += 1
        v, now = value(line, bar_at(m))
        if v > best[0] + 1e-12:
            best = (v, m, line, now)
    v, mnd, line, s = best
    took = {k: first[k] - line[k] for k in WEAPON_NEED if first[k] != line[k]}
    return line, {'mnd': mnd, 'base': m0, 'bar': bar_at(mnd), 'from': took, 'value': round(v, 6),
                  'value_base': round(v0, 6), 'share': round(s, 6), 'share_base': round(s0, 6), 'searched': m}


def vigor_knee(hp):
    """(VIG, HP gain of that point, of the next): the knee of max HP `hp(vig)`, the first VIG whose
    next point gains under half what it did. 60 on the installed regulation (`MEASURED`
    2026-11-12): 48 HP for 39 -> 40, 26 for 40 -> 41 falling to 13 for 59 -> 60, 6 for 60 -> 61."""
    gain = {v: hp(v) - hp(v - 1) for v in range(2, STAT_MAX + 1)}
    for v in range(2, STAT_MAX):
        if 2 * gain[v + 1] < gain[v]:
            return v, gain[v], gain[v + 1]
    return STAT_MAX, gain[STAT_MAX], 0


def least_end(need, load, lo=1):
    """The least `END` from `lo` at which burden `need` keeps medium roll at max equip load
    `load(end)`: `er-mechanics-resources.Model.weight_type` (`GetWeightType` 0x14068d930: burden
    over max load at most `WEIGHT_HEAVY`, in single precision) medium or lighter. `STAT_MAX` + 1
    when no `END` does."""
    RES = S._mod('er_mechanics_resources', 'er-mechanics-resources.py')
    return next((e for e in range(lo, STAT_MAX + 1) if RES.Model.weight_type(need, load(e)) <= 2), STAT_MAX + 1)


def allocate_line(base, total, vig, end, main, cap, spill=WEAPON_STATS):
    """The attribute line a starting class of base attributes `base` makes with `total` points:
    VIG `vig` and `END` `end` (or the base, when higher), every other attribute at its base, MND
    included, and every point left to the damage stats: `main` raised evenly (each point to the
    one with the fewest, ties in `main` order) up to `cap`, then `spill` less `main` evenly up to
    `STAT_MAX`, then every damage stat evenly. None when the base, VIG and `END` take more than
    `total`, or the damage stats cannot take what is left."""
    line = {k: int(base[k]) for k in ATTRS}
    line['vig'] = max(line['vig'], int(vig))
    line['end'] = max(line['end'], int(end))
    left = total - sum(line.values())
    if left < 0:
        return None
    rest = tuple(k for k in spill if k not in main)
    for group, top in ((tuple(main), cap), (rest, STAT_MAX), (tuple(WEAPON_NEED), STAT_MAX)):
        while left > 0:
            free = [k for k in group if line[k] < top]
            if not free:
                break
            k = min(free, key=lambda s: (line[s], group.index(s)))
            line[k] += 1
            left -= 1
    return line if left == 0 else None


def attacker_builds(rl, usage, classes, hp, load, armor, floor, lines=ATTACKER_LINES, mind=None, fitted=None,
                    plan=None):
    """The attacker lines dominance is read for, made from the game's curves at RL `rl` (the eight
    attributes summing to `rl` + `LEVEL_OFFSET`): [{'name', 'stats' (the eight attributes), 'n'
    (usage: the corpus builds of its archetype, 0 for a split line), 'rl', 'rule'}], the commonest
    archetype first and the split lines last. One per `lines` row whose first archetype is in
    `usage` (`corpus_usage`, which gives nothing else: which archetypes exist and the weight their
    builds carry in their hands). Per line (user, 2026-11-12: "111 real Intelligence builds might
    all be bad builds", "I think you're overvaluing real builds"):

    - VIG at the knee of max HP `hp` (`vigor_knee`).
    - `END` the least at which 'carried' (usage: the line's archetypes' median, the larger) plus
      `armor` (`lightest_armor` at the melee poise floor `floor`) keep medium roll at max equip
      load `load(end)` (`least_end`), which depends on `END` alone with no talismans. User,
      2026-11-12: "If 33 endurance isn't enough, then it isn't a real build, its just stat
      allocations"; a heavy roller takes 26% more from the same attacks (bd
      heavy-roll-vs-damage-stats-punish-model-2026-11-12).
    - every point left to the main stats up to their cap, then STR and DEX (`allocate_line`). No
      requirement of any one weapon or spell is assumed: the population is the attacks the line's
      stats can use.
    - the starting class (`classes`, `CharaInitParam`) whose line, MND at its base, ends with the
      most points in the main stats, then in STR and DEX, ties to the first name; every attribute
      is at least its base, and the line is `reachable`.
    - MND: with `mind` (`mind(name, main, cap, base, total, vig, end)` -> (line, facts), the
      Context's `fit_mind` on the line's own attacks and FP plan; user, 2026-11-12: "it sounds
      like we put no value on mind then?") the fitted line, else the class's base.

    A line in `fitted` (`fitted_lines`, every line when the Context builds them) is
    `er-mechanics-line-fit.fit_line`'s instead: its stats and class, VIG, `END` with the armor and HP
    piece, and MND traded on damage dealt before death on its own kit, carrying that kit alone; a
    weapon line's MND is then `mind`'s on the fit's FP plan. The rules above are the fallback for a
    line with no fit. `plan(stats)` (`fp_plan`) gives such a line the FP plan its nets are paid on.

    'rule' records how: {'class', 'main', 'cap', 'spill', 'source' ('allocate_line' or
    'line-fit'), 'fp_plan' (`paid_share`; None without `plan`)}, and for an 'allocate_line' line
    'vig' {'knee', 'hp', 'gain', 'gain_after'}, 'end' {'end', 'least', 'need', 'carried',
    'carried_from', 'carried_n', 'armor', 'poise_floor', 'max_load', 'burden'} and 'mind'
    (`fit_mind`'s facts; None without `mind`), for a 'line-fit' line 'fit' (`fit_summary`), 'usage'
    ({'carried', 'carried_from', 'carried_n'}: the archetype's corpus median, reported, not carried)
    and 'mind' (a weapon line's `fit_mind` facts, else None)."""
    total = rl + LEVEL_OFFSET
    knee, gain, after = vigor_knee(hp)
    groups = {u['name']: u for u in usage}
    worn = round(armor['weight'], 1) if armor else 0.0
    out = []
    for name, main, cap, carry_from in lines:
        if carry_from[0] not in groups:
            continue
        src = max((groups[g] for g in carry_from if g in groups), key=lambda u: u.get('carried', 0.0))
        carried = src.get('carried', 0.0)
        need = round(carried + worn, 1)
        end = least_end(need, load)
        rest = tuple(k for k in WEAPON_STATS if k not in main)
        best = None
        for cls in sorted(classes):
            line = allocate_line(classes[cls], total, knee, end, main, cap)
            if line is None or not reachable(line, classes, rl)['ok']:
                continue
            key = (sum(line[k] for k in main), sum(line[k] for k in rest))
            if best is None or key > best[0]:
                best = (key, cls, line)
        if best is None:
            continue
        _, cls, line = best
        n = groups[name]['n'] if name in groups else 0
        usage = {'carried': carried, 'carried_from': src['name'], 'carried_n': src.get('carried_n', 0)}
        if fitted and name in fitted:
            got, ln = fitted[name]['fit'], fitted[name]['line']
            line = {k: int(got['stats'][k]) for k in ATTRS}
            d, f = got['defense'], got['fp']
            fp_at = {'casts': ln.casts, 'crimson': ln.crimson, 'hp': float(d['hp']), 'hit': d['mean_hit'],
                     'hits': d['hits_per_attack'], 'menu_poise': d['menu_poise'], 'bar': float(f['bar']),
                     'fight': fitted[name]['fight']}
            facts = None
            # A weapon line's fit spends no FP (its R1 costs none), so its MND is fitted against the
            # attacks it can use, on the fit's own FP plan, VIG and `END` held at the fit's.
            if mind is not None and not any(p['kind'] == 'spell' for p in got['offense'].get('parts') or []):
                moved, facts = mind(name, main, cap, classes[got['class']], total, line['vig'], line['end'], fp_at)
                if moved is not None and reachable(moved, classes, rl)['ok']:
                    line = moved
                    fp_at['bar'] = float(facts['bar'])
            rule = {'class': got['class'], 'main': list(main), 'cap': ln.cap, 'spill': list(rest), 'source': 'line-fit',
                    'fit': fit_summary(got, ln, fitted[name]), 'usage': usage, 'mind': facts, 'fp_plan': fp_at}
            out.append({'name': name, 'stats': line, 'n': n, 'rl': sum(line.values()) - LEVEL_OFFSET, 'rule': rule})
            continue
        facts = None
        if mind is not None:
            moved, facts = mind(name, main, cap, classes[cls], total, knee, end)
            if moved is not None and reachable(moved, classes, rl)['ok']:
                line = moved
        rule = {'class': cls, 'main': list(main), 'cap': cap, 'spill': list(rest), 'source': 'allocate_line',
                'vig': {'knee': knee, 'hp': hp(knee), 'gain': gain, 'gain_after': after},
                'end': {'end': line['end'], 'least': end, 'need': need, 'carried': carried,
                        'carried_from': src['name'], 'carried_n': src.get('carried_n', 0), 'armor': armor,
                        'poise_floor': floor, 'max_load': round(float(load(line['end'])), 2),
                        'burden': round(need / float(load(line['end'])), 4)},
                'mind': facts, 'fp_plan': plan(line) if plan is not None else None}
        out.append({'name': name, 'stats': line, 'n': n, 'rl': sum(line.values()) - LEVEL_OFFSET, 'rule': rule})
    return sorted(out, key=lambda b: -b['n'])


#: The keys a `--line` spread may give besides the eight attributes (`parse_line`).
GIVEN_LINE_EXTRA = ('poise',)


def parse_line(text):
    """(name, {attribute: value}, {'poise': float or None}) of a `--line` spec
    'NAME:vig=..,mnd=..,end=..,str=..,dex=..,int=..,fth=..,arc=..[,poise=..]': the name before the
    last ':', every one of `ATTRS` (1 to `STAT_MAX`), and `poise`, the armor poise its set is the
    lightest at (`given_builds`; default the run's melee floor). An exit naming what is wrong
    otherwise."""
    name, sep, body = (text or '').rpartition(':')
    name = name.strip()
    if not sep or not name:
        raise SystemExit(f'attack similarity: --line {text!r}: want NAME:vig=..,mnd=..,end=..,str=..,dex=..,int=..,'
                         f'fth=..,arc=..[,poise=..]')
    st, extra = {}, {k: None for k in GIVEN_LINE_EXTRA}
    for part in filter(None, (p.strip() for p in body.split(','))):
        k, eq, v = part.partition('=')
        k = k.strip().lower()
        if not eq or (k not in ATTRS and k not in extra):
            raise SystemExit(f'attack similarity: --line {name!r}: {part!r} is not one of '
                             f"{', '.join(ATTRS + GIVEN_LINE_EXTRA)} = a number")
        try:
            if k in extra:
                extra[k] = float(v)
            else:
                st[k] = int(v)
        except ValueError:
            raise SystemExit(f'attack similarity: --line {name!r}: {part!r} is not a number') from None
    missing = [k for k in ATTRS if k not in st]
    if missing:
        raise SystemExit(f"attack similarity: --line {name!r} gives no {', '.join(missing)}: a spread names all "
                         f"eight attributes")
    bad = [k for k in ATTRS if not 1 <= st[k] <= STAT_MAX]
    if bad:
        raise SystemExit(f"attack similarity: --line {name!r}: {', '.join(f'{k} {st[k]}' for k in bad)} outside 1 to "
                         f'{STAT_MAX}')
    return name, {k: st[k] for k in ATTRS}, extra


def given_builds(specs, classes, rl, load, armor_at, plan, floor, taken=()):
    """The attacker builds of `--line` spreads `specs` ([`parse_line`]), after the made ones (names
    `taken`), in the order given: [{'name', 'stats' (as given), 'n' 0 (no corpus usage), 'rl',
    'given' True, 'rule'}]. What a fitted line's nets read of its line, made the way the lines with
    no fit make it (`attacker_builds`' fallback):

    - its stats as given: no VIG, `END`, MND or damage stat is traded;
    - its armor `armor_at(poise)` (`lightest_armor`: the lightest set whose armor poise reaches
      `poise`, default `floor`, the run's melee poise floor, the set every line with no fit wears;
      `poise=0`, no armor), no HP piece and no talismans;
    - its FP plan `plan(stats, pieces)` (`fp_plan`): max HP, max FP, the pool's mean hit and hits per
      attack at the set's menu poise, which the nets read (`line_hp`, `line_poise`, `paid_share`).

    Not derived for a spread (`INFERRED`, recorded in 'rule'): what it carries in its hands, so its
    roll is read on the armor alone ('end' 'burden', and 'medium', whether the armor alone keeps
    medium roll; the nets read no roll of the attacker's), and its starting class, the first by name
    of those it is `reachable` from (any of them levels to it at the same RL). An exit when a spread
    is reached from no class within RL `rl`, or its name is taken."""
    out, names = [], set(taken)
    RES = S._mod('er_mechanics_resources', 'er-mechanics-resources.py')
    for name, st, extra in specs:
        if name in names:
            raise SystemExit(f'attack similarity: --line {name!r}: a build of that name is already in the run')
        names.add(name)
        reach = reachable(st, classes, rl)
        if not reach['ok']:
            raise SystemExit(f"attack similarity: --line {name!r} is RL {reach['rl']}, "
                             + (f'past the run\'s RL {rl}' if reach['classes'] else 'below every starting class\'s base'
                                ' in some attribute'))
        poise = floor if extra.get('poise') is None else float(extra['poise'])
        armor = armor_at(poise)
        if armor is None:
            raise SystemExit(f'attack similarity: --line {name!r}: no armor set reaches {poise:g} poise')
        max_load = float(load(st['end']))
        rule = {'class': reach['classes'][0], 'main': [], 'cap': None, 'spill': [], 'source': 'given',
                'end': {'end': st['end'], 'armor': armor, 'poise_floor': poise, 'max_load': round(max_load, 2),
                        'carried': None, 'burden': round(armor['weight'] / max_load, 4),
                        'medium': RES.Model.weight_type(armor['weight'], max_load) <= 2},
                'mind': None, 'fp_plan': plan(st, armor['pieces'])}
        out.append({'name': name, 'stats': dict(st), 'n': 0, 'rl': reach['rl'], 'given': True, 'rule': rule})
    return out


def line_kit(F, world, name, main, cap, catalysts):
    """(`er-mechanics-line-fit.Line`, weapon archetype or None) for attacker line `name` of main
    stats `main` up to `cap`, no talismans or tears, carrying its kit alone (`Line.carried` None):

    - one school (INT or FTH) at `DAMAGE_CAP`: a caster, casting its school's spell (`SCHOOL_LINE`)
      from the first of `catalysts` (the run's, as written) that casts it; no poise floor;
    - two schools (Hybrid): both spells, half each, `mains` raised evenly; no poise floor. No weapon
      whose scaling is Hybrid's (`SCALING_RULES`) is wieldable at its stats (`MEASURED` 2026-11-12:
      none of 130 at STR 10 DEX 10 INT 45 FTH 45);
    - one school at `SPLIT_CAP`: the school's spell and a weapon's R1 (`SPLIT_MELEE_SHARE`), the
      weapon of any scaling, the poise floor kept;
    - STR, DEX or ARC mains: a weapon's R1 alone, of the archetype's own scaling (`name`), `mains`
      raised evenly, the poise floor kept.

    The poise floor is the melee one (`er-mechanics-armor-targets.melee_poise_floor`, 89 on the RL
    140-160 pool), kept on every line whose kit swings a weapon; user, 2026-10-13: "most people
    would laugh at me if I gave them a build that had less than 89 poise without bullgoat, unless it
    was a mage build". A weapon is a placeholder here; `er-mechanics-line-fit.fit_line` picks it
    off the game."""
    schools = [k for k in main if k in SCHOOL_LINE]

    def spell(school, share):
        ref = F.LINES[SCHOOL_LINE[school]]
        mid = world.sm.by_name[world.SP.T._norm(ref.spell)]
        cat = next((c for c in catalysts if world.sm.can_cast(mid, world.sm.catalyst(c))), ref.catalyst)
        return F.Spell(school, cat, ref.spell, share)
    base = dict(talismans=(), tears=(), cap=cap)
    if schools and len(schools) == len(main) and len(main) == 1:
        sp = spell(schools[0], 1.0 if cap == DAMAGE_CAP else 1.0 - SPLIT_MELEE_SHARE)
        if cap == DAMAGE_CAP:
            return F.Line(name, sp.main, sp.catalyst, sp.spell, kit=(sp,), **base), None
        return F.Line(name, sp.main, sp.catalyst, sp.spell, kit=(sp, F.Weapon('-', share=SPLIT_MELEE_SHARE)),
                      poise_min=world.floor, **base), None
    if schools and len(schools) == len(main):
        sps = tuple(spell(s, 1.0 / len(schools)) for s in schools)
        return F.Line(name, sps[0].main, sps[0].catalyst, sps[0].spell, mains=tuple(main), kit=sps, **base), None
    return F.Line(name, main[0], '', '', mains=tuple(main), kit=(F.Weapon('-'),), poise_min=world.floor,
                  **base), name


def fitted_lines(F, world, usage, lines=ATTACKER_LINES, catalysts=DEFAULT_CATALYSTS, log=None):
    """{line name: {'line' (its fitted `er-mechanics-line-fit.Line`), 'fit' (`fit_line`), 'fight',
    'below_floor', 'vig_knee', 'knee_shape'}} for each of `lines` whose first archetype is in
    `usage`, its kit `line_kit`'s. No talismans or tears: the similarity run prices every line
    without them (section 1 of the doc), so a fit that chose them would be scored without the gear
    its stats were chosen for; and `fit`'s greedy talismans run one `search` per candidate per slot
    (59 candidates, 4 slots, 3.3-5.1 s a search, `MEASURED` by that module's author). Every other
    `Line` field is its default (`INFERRED` there: one attack an engagement, Crimson priced as the
    pool prices it, every attack contested, carried chip). Beside each fit, on the same kit and the
    same D: 'below_floor', the fit with no poise floor (a floored line's best allocation below the
    floor, or the fit itself when that is at or above it); 'vig_knee', VIG pinned at the knee of
    max HP and the rest fitted; 'knee_shape', the line as `allocate_line` made it before (VIG at the
    knee, `END` the least that carries the kit and the lightest set at the floor, MND at the base)."""
    groups = {u['name']: u for u in usage}
    quiet = log or (lambda *a, **k: None)
    out = {}
    for name, main, cap, frm in lines:
        if frm[0] not in groups:
            continue
        ln0, tag = line_kit(F, world, name, main, cap, catalysts)
        got, ln = F.fit_line(ln0, world, tag, log=quiet)
        if got is None:
            continue
        gear = world.gear(ln, ln.talismans or ())
        below = got if ln.poise_min <= 0 else F.fit(dataclasses.replace(ln, poise_min=0.0), world, log=quiet)
        knee = F.fit_stats(dataclasses.replace(ln, vig=world.knee), world, gear)
        shape = F.shape_point(dataclasses.replace(ln, vig=world.knee, poise_min=world.floor, lightest=True),
                              world, gear)
        out[name] = {'line': ln, 'fit': got, 'fight': world.fight, 'tag': tag, 'below_floor': below,
                     'vig_knee': knee, 'knee_shape': shape}
    return out


def fit_summary(got, ln, extra=None):
    """The breakdown of an `er-mechanics-line-fit.fit` result `got` for line `ln` that a build's
    'rule' keeps: D and its terms (`fit`'s 'offense', 'defense', 'fp', 'gear'), the kit ('kit', one
    row per attack: spell or weapon, its share, hit, FP and uninterrupted share) and, from
    `fitted_lines`' entry `extra`, the weapon screens and the comparison points on the same kit and
    D: 'below_floor', 'vig_knee', 'knee_shape' (each {'D', 'ratio' to the fit, 'stats', 'wear',
    'armor_poise', 'end'})."""
    o, d, f, g = got['offense'], got['defense'], got['fp'], got['gear']
    out = {'D': round(float(got['D']), 1), 'stats': {k: int(got['stats'][k]) for k in ATTRS}, 'class': got['class'],
           'wear': got['wear'], 'pieces': list(g['pieces']),
           'armor_weight': g['weight'], 'armor_poise': d['armor_poise'], 'menu_poise': d['menu_poise'],
           'hp': d['hp'], 'mean_hit': d['mean_hit'], 'hits_per_attack': d['hits_per_attack'], 'life': d['life'],
           'drain': d['drain'], 'bar': f['bar'], 'spend': f['spend'], 'cerulean': f['cerulean'], 'crimson': f['crimson'],
           'spell': o['spell'], 'catalyst': ln.catalyst or None, 'fp_cast': o['fp_cast'], 'buff': o['buff'],
           'hit': o['hit'], 'paid': o['paid'], 'uninterrupted': o['uninterrupted'], 'casts': o['casts'],
           'kit': [dict(p) for p in o.get('parts') or []], 'carried': g['carried'],
           'burden': g['burden'], 'cap': ln.cap, 'mains': list(ln.mains or (ln.main,)), 'vig_pin': ln.vig,
           'end_pin': ln.end, 'poise_floor': ln.poise_min or None,
           'edges': list(got.get('edges') or []), 'crimson_pricing': ln.crimson,
           'contested': ln.contested, 'scenario': ln.scenario,
           'weapon_settled': got.get('weapon_settled'), 'weapon_screens': got.get('weapon_screens') or []}
    for key in ('below_floor', 'vig_knee', 'knee_shape'):
        p = (extra or {}).get(key)
        if p is not None:
            out[key] = {'D': round(float(p['D']), 1), 'ratio': round(float(p['D']) / float(got['D']), 4) if got['D'] else None,
                        'stats': {k: int(p['stats'][k]) for k in ATTRS}, 'class': p['class'], 'wear': p['wear'],
                        'armor_poise': p['defense']['armor_poise'], 'pieces': list(p['gear']['pieces']),
                        'hp': p['defense']['hp'], 'life': p['defense']['life']}
    return out


def lightest_armor(cands, floor):
    """{'pieces', 'weight', 'poise'}: the four pieces (an empty slot allowed) of least weight whose
    armor poise is at least `floor`, from `er-mechanics-armor-targets.armor_candidates` `cands`, or
    None when no set reaches it. A dynamic program over poise in tenths, capped at the floor; each
    piece's poise is rounded to its tenth, since the float32 rate stores Steel Gauntlets' 10 as
    9.9999998 and a floor would drop the 89.0 set of Steel Helm, Tree Sentinel Armor, Steel
    Gauntlets and Greaves of Solitude (47.7) for one 1.2 heavier (`MEASURED` 2026-11-12)."""
    cap = int(round(floor * 10))
    best = {0: (0.0, ())}
    for slot in range(4):
        nxt = {}
        for p, (w, ps) in best.items():
            for a in cands[slot]:
                q = min(cap, p + int(round(a['poise'] * 10)))
                cand = (w + a['weight'], ps + (a,))
                if q not in nxt or cand[0] < nxt[q][0] - 1e-9:
                    nxt[q] = cand
        best = nxt
    if cap not in best:
        return None
    w, ps = best[cap]
    pieces = [p for p in ps if p['name'] != '-']
    return {'pieces': [p['name'] for p in pieces], 'weight': round(w, 1),
            'poise': round(sum(p['poise'] for p in pieces), 1)}



def reachable(line, classes, rl):
    """{'rl': the RL of attribute line `line`, 'classes': the starting classes whose every base
    attribute is at most the line's (`CharaInitParam`), 'ok': a class can level to it within RL
    `rl`}."""
    level = sum(line[k] for k in ATTRS) - LEVEL_OFFSET
    fits = sorted(nm for nm, cl in classes.items() if all(cl[k] <= line[k] for k in ATTRS))
    return {'rl': level, 'classes': fits, 'ok': bool(fits) and level <= rl}


def n_eff(w):
    """Effective number of situations: (sum w)^2 / sum w^2."""
    w = np.asarray(w, float)
    return float(w.sum() ** 2 / (w ** 2).sum())


def blacklist():
    path = Path(S.ASH_BLACKLIST)
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding='utf-8'))


def _dups(keys):
    return sorted(k for k, n in Counter(keys).items() if n > 1)


def _excl(typ, ident, name, key, reason, extra=None, **more):
    """One exclusion record; `extra` (an entity's fields so far) and `more` are kept beside it."""
    out = {**(extra or {}), **more}
    out.update(key=key, type=typ, id=ident, name=name, status='excluded', reason=reason)
    return out


# ---------------------------------------------------------------------------------------------
# phase 0: every candidate, cheaply

def spell_candidates(c):
    """([magic id] to score, [exclusion]) over every id in `SpellModel.names`, `table`'s order of
    reasons with the goods row check after the multiplayer one."""
    SM = c.SM
    jobs, out = [], []
    for mid in sorted(SM.names):
        nm, m = SM.names[mid], SM.magic[mid]
        why = None
        if not m['enable_multi']:
            why = 'not usable in multiplayer'
        elif not c.spell_goods(mid):
            why = 'no spell goods row'
        elif not any(SM.can_cast(mid, cat) for cat in c.cats):
            why = 'no catalyst of its school'
        else:
            short = c.cast_short(mid)
            if short:
                why = 'short ' + ', '.join(f'{k} {v}' for k, v in short.items())
        if why:
            out.append(_excl('spell', mid, nm, f'spell:{mid}', why))
        else:
            jobs.append(mid)
    return jobs, out


def base_weapons(c):
    """Named, non-NPC base weapons (`spell_like_skills`' rule), one id per name: the id the name
    resolves to, which is what the models are handed."""
    SM, t = c.SM, c.t
    seen, out = set(), []
    for wid in sorted(SM.ar.weapons):
        nm = SM.ar.names.get(wid)
        if wid % 10000 or wid not in t.reg.weapon or not nm or nm.startswith('[') or nm in seen:
            continue
        seen.add(nm)
        out.append(wid)
    return out


def skill_candidates(c):
    """([opening group] to score, [exclusion]): every unique skill (a non-ash skill of a named base
    weapon) and every ash of war, with its carriers grouped by opening (module doc)."""
    A, t, SM = c.A, c.t, c.SM
    gems = t.ash_gems()
    base = base_weapons(c)
    own = {}
    for wid in base:
        own.setdefault(t.reg.weapon[wid]['swordArtsParamId'], []).append(wid)
    cands = {sid: 'unique' for sid in own if sid not in gems}
    cands.update({sid: 'ash' for sid in gems})
    held = {g['swordArtsParamId'] for g in t.gem.values() if g['sortId'] != S.UNOBTAINABLE_SORT_ID}
    tops = {wid: SM.ar.max_level(SM.ar.weapons[wid]['reinforceTypeId']) for wid in base}
    jobs, out = [], []
    for sid, kind in sorted(cands.items()):
        name = t.arts_name(sid)
        key = f'skill:{sid}'
        if sid in NOT_A_SKILL:
            out.append(_excl('skill', sid, name, key, 'not a skill', kind=kind))
            continue
        # An ash no gem row of which is obtainable is still the skill of the weapons that ship with
        # it (Firebreather on the Steel-Wire Torch, Torch Attack on the torches, Buckler Parry on
        # the Buckler): only the mounts go. It is left out when no such weapon is left.
        gem_ok = kind != 'ash' or sid in held
        if not gem_ok and not own.get(sid):
            out.append(_excl('skill', sid, name, key, 'unobtainable', kind=kind))
            continue
        if name in c.black and not c.include_blacklisted:
            out.append(_excl('skill', sid, name, key, 'blacklisted', kind=kind))
            continue
        if A.skill_tae(t, sid) is None:
            out.append(_excl('skill', sid, name, key, 'no skill TimeAct', kind=kind))
            continue
        mount = []
        if kind == 'ash' and gem_ok:
            gem = gems[sid]
            mount = [w for w in base if any(t.can_mount(w, gem, i, tops[w])[0] for i in range(len(AR.AFFINITIES)))]
        pool = list(mount) + [w for w in own.get(sid, []) if w not in mount]
        wield = [w for w in pool if c.wields(w)]
        if not wield:
            out.append(_excl('skill', sid, name, key, 'no carrier the stats can wield', kind=kind))
            continue
        buckets = {}
        for w in wield:
            k = (t.reg.weapon[w]['wepmotionCategory'], A.weapon_diff_category(t, sid, w, 'one'))
            buckets.setdefault(k, []).append(w)
        groups, errors = {}, []
        for k, ws in sorted(buckets.items()):
            probe = min(ws, key=lambda w: (c.requirement(w), w))
            try:
                opening = A.skill_profile(t, sid, probe, 0, False)['opening']
            except ERRORS as e:
                errors.append((k, repr(e)))
                continue
            groups.setdefault(opening, []).extend(ws)
        for k, err in errors:
            out.append(_excl('skill', sid, name, f'{key}:class{k[0]}-{k[1]}', f'model error: {err}', kind=kind))
        if not groups:
            continue
        mclasses = len({t.reg.weapon[w]['wepType'] for w in mount}) if kind == 'ash' else None
        mset = set(mount)
        # A weapon that ships with this ash and mounts no gem (`gemMountType` not 1 or 2: the
        # Meteoric Ore Blade's Gravitas) is a carrier nobody can swap out, at its own affinity and
        # scaling: it is scored on its own as well as inside its group.
        fixed = {w for w in own.get(sid, []) if kind == 'ash' and t.reg.weapon[w]['gemMountType'] not in (1, 2)}
        for opening, ws in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            carrier = min(ws, key=lambda w: (c.requirement(w), w))
            if opening is None:
                out.append(_excl('skill', sid, name, f'{key}:none', 'no opening clip', kind=kind,
                                 carrier=SM.ar.names[carrier]))
                continue
            # The group's weapons ride along: an attacker build takes the one of them it can wield
            # (`build_job`).
            job = {'sid': sid, 'name': name, 'kind': kind, 'opening': opening, 'carrier': carrier,
                   'mountable': carrier in mset, 'gem': gems.get(sid), 'gem_obtainable': gem_ok,
                   'mount_classes': mclasses,
                   'pool': len(pool), 'group': len(ws), 'requirement': c.requirement(carrier),
                   'classes': sorted({t.reg.weapon[w]['wepmotionCategory'] for w in ws}),
                   'members': sorted(ws), 'mount_members': sorted(w for w in ws if w in mset), 'fixed': None}
            jobs.append(job)
            for w in sorted(fixed & set(ws) - {carrier}):
                jobs.append({**job, 'carrier': w, 'mountable': False, 'group': 1, 'requirement': c.requirement(w),
                             'classes': [t.reg.weapon[w]['wepmotionCategory']], 'members': [w], 'mount_members': [],
                             'fixed': w})
    return jobs, out


#: {(job parent, damage stats): (carrier, {weapon: `carrier_damage`})}: `build_job`'s picks, made in
#: the main process (phase 0, the objective items, the Mind fit) before the workers fork.
_CARRIERS = {}


def carrier_damage(c, job, wid):
    """The skill damage per cast of opening group `job` on group weapon `wid` at the context's
    stats, which is what `build_job` ranks the group by: at the mountable affinity `best_affinity`
    picks there (Standard on a weapon that mounts no gem), the `er-mechanics-ashes.skill_option`
    damage of the default cast or each stance button, plus each follow-up the opening offers
    (`skill_followups`) pressed after it, summed. A follow-up is counted because its swing is the
    carrier's: Glintstone Pebble's bullet scales on the affinity alone (AtkParam 300200890: MV 0,
    flat 137 magic, AttackElementCorrect 51080's fixed INT rate 25, `REGULATION`), so every Magic
    carrier ties on the cast, while its R2 swing is the carrier's own AR (Dagger 540.7, Omen Cleaver
    821.7 at STR 26 / DEX 25 / INT 60, `MEASURED` 2026-11-16). Summing the entities with equal
    weight is `INFERRED`: how often a player presses the follow-up is not measured. None when the
    model raises on every reading."""
    A, t, SM = c.A, c.t, c.SM
    weapon = SM.ar.names[wid]
    try:
        aff = best_affinity(c, {**job, 'carrier': wid}, weapon)[0] if wid in set(job['mount_members']) \
            else 'Standard'
        top = SM.ar.max_level(SM.ar.weapons[wid]['reinforceTypeId'])
        prof = A.skill_profile(t, job['sid'], wid, 0, False)
        ctx = A.WeaponContext(weapon, aff, None, c.stats, False, ar_tables=SM.ar)
    except ERRORS:
        return None
    total, read = 0.0, False
    for stance in [b for b, _ in A.stance_buttons(prof)] or [None]:
        try:
            follows = A.skill_followups(t, job['sid'], wid, top, 'one', stance)
        except ERRORS:
            follows = []
        for f in [None] + follows:
            try:
                o = A.skill_option(t, wid, job['sid'], ctx, ctx.level, c.dfn, S.DEF, follow=f, stance=stance)
            except ERRORS:
                continue
            read = True
            total += float((o or {}).get('dmg') or 0.0)
    return total if read else None


def build_job(c, job, stats):
    """Opening group `job` for an attacker build of damage stats `stats`: carried by the group
    weapon the build can wield (`Context.wields`) whose skill damage there is largest
    (`carrier_damage`; a weapon the model raises on ranks last), ties to the least total
    requirement and then the lower id, at the affinity `skill_job` picks at its stats; None when it
    can wield none of them. The weapons of one opening group play the same clips with the same
    follow-up buttons and windows (`MEASURED` 2026-11-16 on the eleven Intelligence-line kit skills'
    groups, no member differing), so what the pick moves is the carrier's AR, reach and status:
    the group weapon of least requirement it took before left Double Slash 66% and Glintstone
    Pebble then R2 29% under the line's best carrier (Dagger against Uchigatana and Omen Cleaver at
    STR 26 / DEX 25 / INT 60)."""
    ws = [w for w in job['members'] if c.wields(w, stats)]
    if not ws:
        return None
    if len(ws) == 1:
        carrier = ws[0]
    else:
        key = (job_parent(job), tuple(int(stats.get(k, 0)) for k in WEAPON_NEED))
        if key not in _CARRIERS:
            cl = at_build(c, damage_stats(stats))
            _CARRIERS[key] = {w: carrier_damage(cl, job, w) for w in ws}
        dmg = _CARRIERS[key]

        def rank(w):
            d = dmg.get(w)
            return (d is not None, d or 0.0, -c.requirement(w), -w)
        carrier = max(ws, key=rank)
    return {**job, 'carrier': carrier, 'mountable': carrier in set(job['mount_members']), 'group': len(ws),
            'requirement': c.requirement(carrier)}


def job_parent(job):
    """The parent key of opening group `job`: every entity of it is keyed under this."""
    return f"skill:{job['sid']}:{job['opening']:06d}" + (f":fixed-{job['fixed']}" if job.get('fixed') else '')


def job_tag(r):
    """The job entity `r` (a record) is scored from: ('spell', magic id) or ('skill', parent key)."""
    return ('spell', r['id']) if r['type'] == 'spell' else ('skill', r['parent'])


# ---------------------------------------------------------------------------------------------
# the objective lines: every scored attack at a line that can use it, read off no corpus build

def attack_need(c, kind, x):
    """{damage stat: requirement} of using job `x` (a spell's magic id, or an opening group): a
    skill's carrier (the distance basis's, its base row, one-handed), or a spell's own
    requirements and its catalyst's, the larger per stat. Read against base stats, as
    `Context.wields` and `Context.cast_short` read them."""
    if kind == 'skill':
        w = c.SM.ar.weapons[x['carrier']]
        return {k: int(w[f]) for k, f in WEAPON_NEED.items()}
    m = c.SM.magic[x]
    need = dict.fromkeys(WEAPON_NEED, 0)
    for k, f in (('int', 'requirementIntellect'), ('fth', 'requirementFaith'), ('arc', 'requirementLuck')):
        need[k] = int(m[f])
    cat = c.catalyst_for(x)
    for k, f in WEAPON_NEED.items():
        need[k] = max(need[k], int(cat['wep'][f]) if cat is not None else 0)
    return need


def hit_stat_weights(c, wid, aff, hits):
    """{damage stat: weight} of one skill entity's priced hits (`skill_option` 'hit_rows') on weapon
    `wid` at affinity `aff`: per hit and element, its base attack before stat scaling (the
    weapon's upgraded base x MV / 100, plus the flat part x `baseAtkRate` when `isAddBaseAtk`, as
    `er-mechanics-ashes.hit_attack` builds it; a hit not from the weapon has no scaling and only
    adds to the total), times how much that element's multiplier rises when the stat goes from the
    weapon's requirement to 99 (`WeaponContext.multiplier`, which is `er-mechanics-ar.
    element_multiplier` on the hit's AECP row: the weapon's `attackElementCorrectId`, or the
    AtkParam's `overwriteAttackElementCorrectId`), summed and divided by the total base attack. So
    each stat weighs the share of the attack it scales: the Zamor Curved Sword's storm (AECP 50000,
    no stat flag) weighs nothing. None when the hits have no base attack."""
    A, AR_ = c.A, c.SM.ar
    w = AR_.weapons[wid]
    need = {k: int(w[f]) for k, f in WEAPON_NEED.items()}
    name = AR_.names[wid]
    ctx0 = A.WeaponContext(name, aff or 'Standard', None, need, False, ar_tables=AR_)
    up = {s: A.WeaponContext(name, aff or 'Standard', None, {**need, s: 99}, False, ar_tables=AR_)
          for s in WEAPON_NEED}
    tot, out = 0.0, dict.fromkeys(WEAPON_NEED, 0.0)
    for h in hits:
        n = float(h.get('count') or 1)
        flat = h.get('flat') or {}
        for el in A.ELEMENTS:
            base = (ctx0.base_by[el] * h['mv'][el] / 100.0 + flat.get(el, 0) * ctx0.base_atk_rate
                    if h.get('from_weapon', True) else float((h.get('attack') or {}).get(el, 0.0)))
            if base <= 0:
                continue
            tot += n * base
            if not h.get('from_weapon', True):
                continue
            ov, d2 = h.get('element_correct_override', -1), h.get('disable_2h', False)
            m0 = ctx0.multiplier(el, ov, d2)
            for s, cx in up.items():
                out[s] += n * base * max(0.0, cx.multiplier(el, ov, d2) - m0)
    return {s: v / tot for s, v in out.items()} if tot > 0 else None


def attack_scaling(c, kind, x, affinity=None, rec=None, job_rec=None):
    """({damage stat: weight} the attack's damage scales with, what it is read from): a spell's
    school stat (`SCHOOL_STAT`) 1, the rest 0 ('school'); a skill entity's `hit_stat_weights` of
    its own hits as the distance basis scored them (`rec`, 'hits'), else of `job_rec`, its job's
    first entity with any ('job hits'), else its carrier's affinity row's `WEAPON_SCALING` fields
    ('correct fields'), which do not say which element each stat scales."""
    if kind == 'spell':
        st = SCHOOL_STAT[c.SM.school[x]]
        return {k: float(k == st) for k in WEAPON_NEED}, 'school'
    for r, read in ((rec, 'hits'), (job_rec, 'job hits')):
        sw = (r or {}).get('stat_weights')
        if sw and sum(sw.values()) > 0:
            return {k: float(sw[k]) for k in WEAPON_NEED}, read
    try:
        wid = c.SM.ar.find_weapon(x['carrier'], affinity or 'Standard')
    except SystemExit:
        wid = x['carrier']
    w = c.SM.ar.weapons[wid]
    return {k: float(w[f]) for k, f in WEAPON_SCALING.items()}, 'correct fields'


def line_fit(line, scale):
    """The scaling-weighted mean of attribute line `line`'s damage stats under weights `scale`: how
    many points the line puts where the attack scales. 0 when nothing scales."""
    tot = sum(scale.values())
    return sum(scale[k] * line[k] for k in WEAPON_NEED) / tot if tot > 0 else 0.0


def adjust_line(line, need, floor, scale=None):
    """(line, {stat: points raised}, {stat: points paid}): attribute line `line` with every damage
    stat under `need` raised to it, and as many points paid back, one at a time, from the highest
    damage stat above its floor (`floor`, which a caller sets at no less than `need`), ties to the
    stat `scale` weighs least, then to `WEAPON_NEED` order. So the RL is the line's whenever there is
    a point above the floors to pay; a line that meets `need` comes back unchanged."""
    out = {k: int(line[k]) for k in ATTRS}
    raised = {k: need[k] - out[k] for k in WEAPON_NEED if out[k] < need.get(k, 0)}
    for k, v in raised.items():
        out[k] += v
    owe = sum(raised.values())
    scale = scale or {}
    order = {k: i for i, k in enumerate(WEAPON_NEED)}
    paid = {}
    while owe > 0:
        free = [k for k in WEAPON_NEED if out[k] > max(floor.get(k, 0), need.get(k, 0))]
        if not free:
            break
        k = max(free, key=lambda s: (out[s], -scale.get(s, 0.0), -order[s]))
        out[k] -= 1
        paid[k] = paid.get(k, 0) + 1
        owe -= 1
    return out, raised, paid


def minimum_line(need, classes):
    """(line, class name): the lowest-RL character that meets `need` exactly. Per starting class of
    `classes` ({name: base attributes}), each attribute at its base or its requirement, whichever is
    higher; the class whose line has the lowest RL, ties to the first name. Taking each attribute's
    lowest base over every class instead gives a line no class has (RL -1 for the Zamor Curved
    Sword's 16 / 18, `MEASURED` 2026-11-10)."""
    lines = {nm: {k: max(int(cl[k]), int(need.get(k, 0))) for k in ATTRS} for nm, cl in classes.items()}
    nm = min(sorted(lines), key=lambda n: sum(lines[n].values()))
    return lines[nm], nm


def objective_lines(c, kind, x, affinity=None, rec=None, job_rec=None):
    """{'adjusted': line or None, 'minimum': line} for entity `rec` of job `x` (module doc,
    objective evaluation). Each line is {'stats' (the eight attributes), 'rl', 'from_line', 'need',
    ...}. 'adjusted' starts from the attacker build line that fits the entity's own scaling best
    (`attack_scaling`, `line_fit`; ties to the earlier line, `attacker_builds` order) and raises
    what it lacks, paying from its highest damage stats (`adjust_line`); None when the run has no
    attacker build. 'minimum' meets the job's requirements exactly, the rest at the base of the
    starting class that makes that the lowest RL (`minimum_line`, 'from_class'), so it is the same
    for every entity of the job."""
    need = attack_need(c, kind, x)
    scale, read = attack_scaling(c, kind, x, affinity, rec, job_rec)
    floor = c.class_floor
    out = {'adjusted': None}
    # A `--line` spread (`given_builds`) adds its own nets and moves no other number: the adjusted
    # line starts from the made lines alone.
    made = [(i, b) for i, b in enumerate(c.builds) if not b.get('given')]
    if made:
        i, b = max(made, key=lambda ib: (line_fit(ib[1]['stats'], scale), -ib[0]))
        st, raised, paid = adjust_line(b['stats'], need, {k: max(floor[k], need[k]) for k in WEAPON_NEED}, scale)
        out['adjusted'] = {'stats': st, 'rl': sum(st[k] for k in ATTRS) - LEVEL_OFFSET, 'from_line': b['name'],
                           'from_rl': b['rl'], 'fit': round(line_fit(b['stats'], scale), 2), 'raised': raised,
                           'paid': paid, 'need': need, 'scaling': {k: round(v, 5) for k, v in scale.items()},
                           'scaling_from': read}
    mn, cls = minimum_line(need, c.classes)
    out['minimum'] = {'stats': mn, 'rl': sum(mn[k] for k in ATTRS) - LEVEL_OFFSET, 'from_line': None,
                      'from_class': cls, 'need': need}
    return out


def objective_items(c, spell_ids, skill_jobs, records):
    """({entity key: `objective_lines`}, [phase-1 item]) for every scored or partial entity of
    `records` (the distance basis's). Each entity's adjusted line is fitted on its own hits (a
    follow-up adds hits its opening does not have: Tremendous Phalanx's R2 adds a melee swing to the
    opening's INT-only bullet), at the affinity the distance basis scored; its minimum line is its
    job's. One item per job and distinct line: the job at that line's damage stats, a skill on the
    group weapon `build_job` picks there (the distance basis's carrier, whose requirements every
    line meets). Of what an item scores, each key keeps only the entity at its own line (`compute`)."""
    by_job = {}
    for r in records.values():
        by_job.setdefault(job_tag(r), []).append(r)
    lines, items, made = {}, [], set()
    todo = [('spell', m, ('spell', m)) for m in spell_ids] + [('skill', j, ('skill', job_parent(j))) for j in skill_jobs]
    for kind, x, tag in todo:
        rs = by_job.get(tag)
        if not rs:
            continue
        # The job's first entity in key order (its opening, before its follow-ups) whose hits give
        # stat weights: the fallback of an entity whose own hits give none.
        job_rec = next((r for r in rs if sum((r.get('stat_weights') or {}).values()) > 0), None)
        for r in rs:
            sk = kind == 'skill'
            ln = lines[r['key']] = objective_lines(c, kind, x, r.get('affinity') if sk else None, r if sk else None,
                                                   job_rec if sk else None)
            for nm in OBJECTIVE_LINES:
                if ln[nm] is None:
                    continue
                st = damage_stats(ln[nm]['stats'])
                who = ('objective', nm, tuple(st[k] for k in WEAPON_NEED))
                if (tag, who) in made:
                    continue
                made.add((tag, who))
                if not sk:
                    items.append(('spell', x, who))
                    continue
                jb = build_job(c, x, st)
                if jb is None:
                    ln[nm]['reason'] = 'no group weapon it can wield'
                    continue
                items.append(('skill', jb, who))
    return lines, items


def line_key(line):
    """A line's damage stats in `WEAPON_NEED` order: what an objective item is run at."""
    return tuple(int(line['stats'][k]) for k in WEAPON_NEED)


def objective_row(c, line, rec, nets):
    """One objective line of one entity: the line, what scored there (`rec`, None when no entity
    of that key came back) and its 'mean_net' / 'mean_net_one' from `nets` ((net, net_one) per
    situation, or an error string), the weighted mean `BuildView` gives a build's, paid as a build
    pays: times 'fp_share', the share of its casts the FP plan of the attacker line it started from
    pays (`paid_share` on that build's 'rule', whose VIG, MND and END the adjusted line keeps). A
    minimum line has no attacker line, HP, armor or FP plan of its own and is not paid (share 1)."""
    if line is None:
        return None
    out = {k: v for k, v in line.items()}
    out.update(mean_net=None, mean_net_one=None)
    if rec is None:
        out.update(status='absent', reason=line.get('reason') or 'no entity of this key at this line')
        return out
    out.update({k: rec.get(k) for k in ('status', 'reason', 'carrier', 'affinity', 'fp', 'full')})
    if isinstance(nets, str):
        out['reason'] = nets
    elif nets is not None:
        src = next((b for b in c.builds if b['name'] == line.get('from_line')), None) if line.get('from_line') else None
        p = paid_share(src.get('rule'), rec.get('fp')) if src else 1.0
        out['fp_share'] = p
        out['mean_net'] = float(c.w @ (nets[0].astype(float) * p) / c.W)
        out['mean_net_one'] = float(c.w @ (nets[1].astype(float) * p) / c.W)
    return out


# ---------------------------------------------------------------------------------------------
# phase 1: the inputs of one spell or one opening group

def _walk(bt):
    """Every node of a bullet tree."""
    todo, out = [bt], []
    while todo:
        n = todo.pop()
        if n is None:
            continue
        out.append(n)
        todo += list(n['children'].values())
    return out


def speffect_tokens(c, sp, atk):
    """Tokens one SpEffect row applied by AtkParam `atk` (None: no AtkParam to read) gives."""
    row = c.t.speffect.get(sp)
    if not row:
        return set()
    out = set()
    rates = c.ax.get(atk) or {}
    # The status gate (scripts/interactions/regdata.py): a row on the AtkPowerCorrect path builds
    # nothing when either of the hit's correct rates is 0.
    gated = bool(row.get('isUseStatusAilmentAtkPowerCorrect')) and atk is not None and (
        not rates.get('statusAilmentAtkPowerCorrectRate') or not rates.get('statusAilmentAtkPowerCorrectRate_byPoint'))
    if not gated:
        out |= {f'status:{nm}' for f, nm in STATUS_TOKEN.items() if row.get(f, 0) > 0}
    if row.get('changeHpRate', 0) > 0 or row.get('changeHpPoint', 0) > 0:
        out.add('hp drain')
    if any(k.endswith('DamageCutRate') and isinstance(v, (int, float)) and v > 1 for k, v in row.items()):
        out.add('defense down')
    if row.get('stateInfo') == STATE_INFO_PULL:
        kb = rates.get('knockbackDist', 0.0)
        out.add('pull' if kb < 0 else 'push' if kb > 0 else 'gravity')
    return out


def effect_tokens(c, trees, atks, node_test):
    """(tokens, flags, sfx ids) of the bullet trees an attack fires and the damaging AtkParams it
    hits with directly (`atks`). `node_test(node)` says which bullet nodes hit."""
    A, t = c.A, c.t
    tokens, damaging, flown = set(), set(atks), []
    for bt in trees:
        for node in _walk(bt):
            for sp in node['on_hit_speffects']:
                tokens |= speffect_tokens(c, sp, node['atk_row'])
            if node_test(node):
                damaging.add(node['atk_row'])
        flown += [node for node, _, _, _ in A.fly_tree(t, bt)]
    for a in damaging:
        ex = t.atk_extra.get(a) or {}
        for i in range(5):
            sp = ex.get(f'spEffectId{i}')
            if sp and sp > 0:
                tokens |= speffect_tokens(c, sp, a)
    flags = set()
    # Homing is `Bullet.homingAngle` > 0 on a flown node. `isEnableAutoHoming` is read once in the
    # image (`VERIFIED` 2026-11-12, 0x14040afea in FUN_14040afa0, er-mechanics-ashes' note at
    # `HOMING_MIN_SPEED`): it picks an auto-target only for a caster who is not locked on, so it
    # does nothing to the locked-on cast the run scores.
    if any((c.bx.get(n['bullet']) or {}).get('homingAngle', 0) > 0 for n in flown):
        flags.add('homing')
    if any((c.ax.get(a) or {}).get('disableGuard') for a in damaging):
        flags.add('unblockable')
    if any((c.ax.get(a) or {}).get('throwTypeId', 0) != 0 for a in damaging):
        flags.add('grab')
    if any((c.ax.get(a) or {}).get('isInvalidatedByNoDamageInAir') for a in damaging):
        flags.add('jumpable_any_row')
    sfx = sorted({(c.bx.get(n['bullet']) or {}).get('sfxId_Bullet', -1) for n in flown} - {-1})
    return sorted(tokens), sorted(flags), sfx


def weapon_status_tokens(c, wid, affinity, hits):
    """(tokens, [SpEffect id]) of the carrier's own status rows that the scored skill hits `hits`
    (`skill_option` hit rows) apply. `VERIFIED` on the 1.16.2 dump (round 5 trace; the slot code
    is in docs/er-mechanics/status.md section 1): hit SpEffect slots 5..7 are
    `EquipParamWeapon.spEffectBehaviorId0..2` + `ReinforceParamWeapon.spEffectId1..3` of the
    weapon at its affinity and level, filled unless the AttackInfo's `attackNotFromWeapon` (+0xf4)
    is set or the AtkParam has `disableHitSpEffect`; a melee hit is not from the weapon when its
    `hitSourceType` is non-zero and its TAE event carries no flag of 0x68 (FUN_14068ffa0: the
    hit row's `from_weapon`), and a skill's TAE bullet never sets it (only a spell's or a goods
    bullet does). A weapon slot's build-up is the row's amount x `statusAilmentAtkPowerCorrectRate`
    / 100, so a hit at rate 0 builds nothing. A bullet's children are left out: whether they keep
    the root's 'from the weapon' is not traced."""
    ar = c.SM.ar
    try:
        row_id = ar.find_weapon(ar.names[wid], affinity or 'Standard')
    except SystemExit:
        return [], []
    wep = ar.weapons[row_id]
    reinf = ar.reinforce.get(wep['reinforceTypeId'] + ar.max_level(wep['reinforceTypeId'])) or {}
    rows = [wep.get(f'spEffectBehaviorId{k}', -1) + reinf.get(f'spEffectId{k + 1}', 0) for k in range(3)
            if wep.get(f'spEffectBehaviorId{k}', -1) > 0]
    rows = [r for r in rows if r > 0 and r in c.t.speffect]
    if not rows:
        return [], []

    def applies(h):
        ax = c.ax.get(h.get('atk_row')) or {}
        if ax.get('disableHitSpEffect') or not ax.get('statusAilmentAtkPowerCorrectRate'):
            return False
        if h.get('kind') == 'melee':
            return bool(h.get('from_weapon', True))
        return h.get('kind') == 'bullet' and h.get('bullet') is not None and h.get('bullet') == h.get('root_bullet')
    if not any(applies(h) for h in hits):
        return [], []
    tokens = set()
    for r in rows:
        row = c.t.speffect[r]
        tokens |= {f'status:{nm}' for f, nm in STATUS_TOKEN.items() if row.get(f, 0) > 0}
    return sorted(tokens), sorted(rows)


def status_hit_atks(c):
    """(the AtkParam_Pc ids a hit with no flat damage counts by, the ones left out): an AtkParam
    with no flat attack field (`er-mechanics-spells.SpellModel._damaging`'s spell rule) that a
    Bullet row hits with while its own `spEffectId0..4` (hit slots 0..4 of a bullet hit,
    `list_status`) put a status on a hostile victim (`er-mechanics-status.row_status`, no
    `hostile_target_refusal`), and that `opposeTarget` lets hit him. Left out: AtkParam 0, and any
    such row another bullet hits with that builds no status, since counting it would fly that
    bullet's hits too (the second list, kept as `Context.status_atks_shared`). So Freezing Mist's
    10442000, which carries AtkParam 0 and its frost, lands no counted hit as it travels (`INFERRED`
    to cost little: it lives 1 s), while its 10442001 field's AtkParam 44200 counts.
    `er-mechanics-spells.SpellModel.status_atks` takes the first."""
    ST, st, SM = _status(), c.st, c.SM
    status, plain = set(), set()
    for b, r in SM.bullet.items():
        a = r['atkId_Bullet']
        sps = [(c.bx.get(b) or {}).get(f'spEffectId{i}', -1) for i in range(5)]
        if any(sp > 0 and sp in st.sp and ST.row_status(st.sp[sp]) and not ST.hostile_target_refusal(st.sp[sp])
               for sp in sps):
            status.add(a)
        else:
            plain.add(a)
    flat = {a for a in status if a in SM.atk and any(SM.atk[a][f] > 0 for f in S.ATK_FIELD.values())}
    cand = {a for a in status - flat if a > 0 and a in SM.atk and (c.ax.get(a) or {}).get('opposeTarget')}
    return sorted(cand - plain), sorted(cand & plain)


def spell_status_ws(c):
    """The weapon record (`er-mechanics-status.weapon_status`'s shape) a spell's hits read for their
    status: no weapon rows and every factor 1. A spell's bullet carries `attackNotFromWeapon`
    (`weapon_status_tokens`' trace: only a spell's or a goods bullet sets it), so hit slots 5..7 stay
    empty (`VERIFIED` there); that the casting catalyst's arcane and `vsPlayerDmgCorrectRate_<s>`
    factors do not apply to its rows is `INFERRED` (the 13-slot reader's caller was not traced for a
    spell)."""
    return {'finals': {n: {'arcane': 1.0, 'vs_player': 1.0} for n in _status().NAMES}, 'req': 1.0,
            'sources': [], 'spell': True}


def skill_status_ws(c, wid, affinity):
    """`er-mechanics-status.weapon_status` of carrier `wid` at `affinity`, its top level (the level
    `weapon_status_tokens` reads) and the context's stats, one-handed, PvP; None when the affinity
    has no row."""
    ar = c.SM.ar
    try:
        level = ar.max_level(ar.weapons[ar.find_weapon(ar.names[wid], affinity or 'Standard')]['reinforceTypeId'])
        return _status().weapon_status(c.st, ar.names[wid], affinity or 'Standard', level, c.stats, False, True)
    except (SystemExit, KeyError):
        return None


def list_status(c, l, ws):
    """{status: constants} one `CAUGHT` hit list `l` builds (`er-mechanics-spells` comment above
    `STATUS_WORTH_FLOOR`) with weapon record `ws`: per hit, the rows of the 13 hit SpEffect slots
    (`er-mechanics-ash-table.Table.hit_buildup`, `VERIFIED` there: slots 0..4 are a bullet hit's
    Bullet `spEffectId0..4`, a swing's AtkParam `spEffectId0..4`, scaled by the AtkParam's
    `statusAilmentAtkPowerCorrectRate` x `_byPoint`; slots 5..7 the weapon's own rows, scaled by the
    rate alone), each through `er-mechanics-status._row_amount` (the build-up formula, the hostile
    target gate). The weapon's rows reach a melee hit from the weapon and a skill's root bullet,
    never a spell's hit nor a bullet's children (`weapon_status_tokens`' rule). The proc row is the
    weapon's own row for the status, else the first row that built it
    (`er-mechanics-status.proc_row_for`); a damage over time is credited
    `er-mechanics-status.BATTLE_SECONDS` of ticks at most (`er-mechanics-ash-table.Table.buildup_hp`)."""
    ST, st = _status(), c.st
    atk = st.atk.get(l.get('atk'))
    if atk and atk['disableHitSpEffect']:
        return {}
    bullet = l.get('bullet')
    if bullet is not None:
        own = [(c.bx.get(bullet) or {}).get(f'spEffectId{i}', -1) for i in range(5)]
    else:
        own = [atk[f'spEffectId{i}'] for i in range(5)] if atk else []
    weapon = not ws.get('spell') and (l.get('from_weapon', False) if bullet is None
                                     else bullet == l.get('root'))
    used = ws if weapon else dict(ws, sources=[])
    rows = [(rid, True) for rid in own if rid and rid > 0] + [(s['speffect'], False) for s in used['sources']]
    amount, first = {}, {}
    for rid, both in rows:
        r = ST._row_amount(st, used, rid, atk, both, 1.0, None)
        if r and r[1] > 0:
            amount[r[0]] = amount.get(r[0], 0) + r[1]
            first.setdefault(r[0], rid)
    out = {}
    for name, b in amount.items():
        rid = ST.proc_row_for(st, used, name, first[name])
        row = st.sp.get(rid) if rid else None
        if row is None:
            continue
        pe = ST.proc_effect(st, rid, 0.0)
        ticks = pe['ticks']
        if ticks > 1 and pe['interval'] > 0:
            ticks = min(ticks, int(ST.BATTLE_SECONDS / pe['interval']))
        out[name] = {'b': int(b), 'group': ST.GROUP_OF[name], 'row': int(rid), 'rate': float(row['changeHpRate']),
                     'point': float(row['changeHpPoint']), 'ticks': int(ticks), 'lock': float(row['effectEndurance']),
                     'recover': float(st.recover[name])}
    return out


def annotate_status(c, geo, ws):
    """Give every `CAUGHT` hit list of `geo` its 'status' (`list_status`) with weapon record `ws`
    (`spell_status_ws`, `skill_status_ws`), in place: what `er-mechanics-spells.StatusCaught` prices
    (version 30). Returns {status: build-up of every hit of every list}, each list once (a bullet
    chain's parts share their lists, `er-mechanics-ashes.split_chains`)."""
    total, seen = {}, set()
    if ws is None:
        return total
    for g, _ in geo or ():
        cg = g.get('CAUGHT') if g is not None else None
        for l in (cg or {}).get('lists') or ():
            if id(l) in seen:
                continue
            seen.add(id(l))
            got = list_status(c, l, ws)
            if got:
                l['status'] = got
                for name, d in got.items():
                    total[name] = total.get(name, 0.0) + d['b'] * float(l.get('hits', l['n']))
            else:
                l.pop('status', None)
    return {k: round(v, 1) for k, v in sorted(total.items())}


def _geo_facts(geo):
    if not geo:
        return {'live_frames': 0.0, 'jumpable': False, 'cost': 0.0}
    lo = min(float(g['T'].min()) for g, _ in geo)
    hi = max(float(g['T'].max()) for g, _ in geo)
    return {'live_frames': round(hi - lo, 1), 'jumpable': all(bool(g.get('AIR')) for g, _ in geo),
            'cost': float(sum(len(g['T']) * (float(g['T'].max()) - float(g['T'].min()) + 1.0) for g, _ in geo))}


def loop_lower_bound(variant, fp_loop, channel):
    """Whether a spell variant's FP (`Magic.mp`) is only a lower bound: a loop spell's variant that
    holds, which pays `consumeLoopMP_forMenu` per tick on top (`REGULATION`, J3). A tap ends before
    the loop (`INFERRED` from the clip it plays), so its `Magic.mp` is taken as exact. A 'cast'
    variant with a loop cost is a held cast: `SpellModel.variants` gives a hold spell whose tap clip
    casts nothing one cast of `045010`, the clip round 4 scored as its held 'charged' variant, so it
    keeps that flag whether or not the spell has a loop clip (`channel`). This narrows the spec's
    `bool(fp_loop)` by the tap reading: a choice, not a param fact. Such a cast let go early
    ('early', `RELEASED_VARIANTS`) ends at its release, as a tap does, so its FP is taken as exact
    too."""
    return bool(fp_loop) and (variant in HELD_VARIANTS or variant == 'cast')


#: Spell variants let go from 045010 before the end: a tap, and a hold spell let go early
#: (`er-mechanics-spells.SpellModel.variants`).
RELEASED_VARIANTS = ('tap', 'early')


def channelled(variant, loop_clip):
    """Whether a spell variant counts as channelled for the 'full count' rule, on the reading
    `loop_lower_bound` takes: the spell has a loop clip (`evaluate`'s `channel`, a property of the
    whole spell) and the variant is no release (`RELEASED_VARIANTS`), which ends before the loop
    (`INFERRED`)."""
    return bool(loop_clip) and variant not in RELEASED_VARIANTS


def _any_node(node):
    """The node test of an attack with no damaging hit: every node it fires, so the effect tokens
    of a status-only or grab-only attack are still read."""
    return True


def spell_ref_tokens(c, mid, anims):
    """{'tokens', 'flags', 'sfx'} of every ref the release clips `anims` of spell `mid` fire, read
    on every node and every AtkParam (a spell `evaluate` gives no damaging hit: Poison Mist, Fia's
    Mist)."""
    SM, A, t = c.SM, c.A, c.t
    m = SM.magic[mid]
    try:
        refs = sorted({n for anim in anims for _, n in SM.release_events(mid, anim)[1]})
        trees = [A.bullet_tree(t, m[f'refId{n}']) for n in refs if m[f'refCategory{n}'] == S.REF_BULLET]
        atks = [m[f'refId{n}'] for n in refs if m[f'refCategory{n}'] == S.REF_ATTACK and m[f'refId{n}'] > 0]
        tokens, flags, sfx = effect_tokens(c, [x for x in trees if x], atks, _any_node)
    except ERRORS as e:
        return {'tokens': [], 'flags': [], 'sfx': [], 'tokens_error': repr(e)}
    return {'tokens': tokens, 'flags': flags, 'sfx': sfx}


def behavior_damage(c, sp, depth=0):
    """[(BehaviorParam_PC id, Bullet id or None, AtkParam id)]: the damaging AtkParams (the spell
    rule, `SpellModel._damaging`) SpEffect `sp` makes through its `behaviorId` (a Bullet tree or an
    AtkParam, `BEH_BULLET` / `BEH_ATTACK`), and through the rows its `cycleOccurrenceSpEffectId` and
    `replaceSpEffectId` name. The spell reader flies none of them: it follows a fired ref's Bullet
    tree and AtkParam only (`SpellModel.ref_hits`)."""
    row = c.t.speffect.get(sp)
    if not row or depth > 6:
        return []
    out = []
    bid = row.get('behaviorId', -1)
    beh = c.beh.get(bid)
    if beh and beh['refId'] > 0:
        if beh['refType'] == BEH_BULLET:
            bt = c.A.bullet_tree(c.t, beh['refId'])
            out += [(bid, n['bullet'], n['atk_row']) for n in _walk(bt) if c.SM._damaging(n['atk_row'])] if bt else []
        elif beh['refType'] == BEH_ATTACK and c.SM._damaging(beh['refId']):
            out.append((bid, None, beh['refId']))
    for k in ('cycleOccurrenceSpEffectId', 'replaceSpEffectId'):
        v = row.get(k, -1)
        if isinstance(v, int) and v > 0 and v != sp:
            out += behavior_damage(c, v, depth + 1)
    return list(dict.fromkeys(out))


def hits_no_character(c, atk):
    """Whether AtkParam `atk` hits no character: `opposeTarget`, `friendlyTarget` and `selfTarget`
    all 0 (`INFERRED` from the field names; `opposeTarget` is the one the hit lists of every damaging
    spell bullet set)."""
    ax = c.ax.get(atk) or {}
    return not (ax.get('opposeTarget') or ax.get('friendlyTarget') or ax.get('selfTarget'))


def spell_hidden_damage(c, mid, anims):
    """Damage a spell with no damaging hit by the spell rule still makes, read on the refs the
    release clips `anims` fire: [{'ref', 'speffect', 'path' ([Bullet id] from the fired root to the
    node that applies it; [] for a SpEffect ref), 'gate' (the first Bullet on that path whose child
    is made only on a hit while its own AtkParam hits no character, `hits_no_character`: only another
    bullet can end it so, or None), 'behind' (`behavior_damage`)}].

    Carian Retaliation (Magic 4640, `REGULATION`): ref 1 fires Bullet 10464000 (0.35 s, radius 1.0,
    AtkParam 79981 with no target flag set, `launchConditionType` 6: its `HitBulletID` child only on
    a hit, `er-mechanics-ashes.LAUNCH_ON`); that child 10464001 (AtkParam 7, `selfTarget`) puts
    SpEffect 1464000 on the caster, whose `behaviorId` 3010 is Bullet 10464002 (3 shots, `FollowType`
    1, AtkParam 46400 `atkMag` 248) and its on-hit 10464003 (AtkParam 46401 `atkMag` 248). So its
    damage needs something to hit 10464000, which no character can do: an incoming projectile
    (`INFERRED` that a bullet hitting a bullet passes the type 6 gate, which `LAUNCH_ON` takes as
    passing on any hit; user, 2026-11-22: "Carian Retaliation is a parry that also works for spell
    projectiles")."""
    SM, A, t = c.SM, c.A, c.t
    m = SM.magic[mid]
    refs = sorted({n for anim in anims for _, n in SM.release_events(mid, anim)[1]})
    out = []
    for n in refs:
        kind, rid = m[f'refCategory{n}'], m[f'refId{n}']
        if rid <= 0:
            continue
        if kind == REF_SPEFFECT:
            got = behavior_damage(c, rid)
            if got:
                out.append({'ref': n, 'speffect': rid, 'path': [], 'gate': None, 'behind': got})
            continue
        if kind != S.REF_BULLET:
            continue
        todo = [(A.bullet_tree(t, rid), [])]
        while todo:
            node, path = todo.pop()
            if node is None:
                continue
            here = path + [node]
            sps = list(node['on_hit_speffects']) + [
                s for s in ((t.atk_extra.get(node['atk_row']) or {}).get(f'spEffectId{i}', -1) for i in range(5)) if s > 0]
            for sp in dict.fromkeys(sps):
                got = behavior_damage(c, sp)
                if not got:
                    continue
                gate = next((p['bullet'] for p, ch in zip(here, here[1:])
                             if p['children'].get('HitBulletID') is ch and hits_no_character(c, p['atk_row'])
                             and not A.launches(t, p['bullet'], ch['bullet'], 'expire')), None)
                out.append({'ref': n, 'speffect': sp, 'path': [x['bullet'] for x in here], 'gate': gate,
                            'behind': got})
            todo += [(ch, here) for ch in node['children'].values()]
    return out


def no_hit_reason(c, mid, anims, tokens):
    """(reason, extra fields) of a spell `evaluate` gives no damaging hit, read on the refs the release
    clips `anims` fire, the first that holds:

    - 'needs an incoming projectile (...)': its only damage is behind a `spell_hidden_damage` gate,
      a bullet no character can hit. The punish situations are spaced whiffs of the opponent pool's
      melee openers (`er-mechanics-spells.punish_situations`: R1, R2, running and jump attacks), so
      none fires a projectile at the caster and none can make that damage. Carian Retaliation.
    - 'damage only through a SpEffect behavior the spell reader does not fly (...)': the same with no
      such gate; the reader would have to fly `behavior_damage`'s bullets. No player spell of the
      2026-11-22 regulation is one (`MEASURED`, target/nodmg/scan.py in the nodmg worktree).
    - 'status only (<status>): its status hits are not counted (...)': a fired node builds a
      status on whoever it hits (`tokens`' 'status:' entries, `speffect_tokens`) and nothing deals
      damage, and its AtkParams are not `status_hit_atks`' (another bullet hits with them and builds
      none, or AtkParam 0), so `evaluate` has no hit to fly. Until version 30 every status-only spell
      was this (Freezing Mist, Poison Mist, Fia's Mist); a counted one is scored on its build-up
      (module doc, the outcome section).
    - 'no damaging hit': none of these, a buff or a field with no damage and no status (Unseen
      Blade's SpEffect 1466000, Scholar's Armament's 1446000)."""
    try:
        hidden = spell_hidden_damage(c, mid, anims)
    except ERRORS as e:
        return 'no damaging hit', {'hidden_error': repr(e)}
    if hidden:
        h = next((x for x in hidden if x['gate'] is not None), hidden[0])
        beh, bul, atk = h['behind'][0]
        chain = (f"SpEffect {h['speffect']} -> BehaviorParam_PC {beh} -> "
                 f"{f'Bullet {bul} AtkParam {atk}' if bul is not None else f'AtkParam {atk}'}")
        extra = {'hidden': [{**x, 'behind': [list(b) for b in x['behind']]} for x in hidden]}
        if all(x['gate'] is not None for x in hidden):
            return (f"needs an incoming projectile (its damage is made only after Bullet {h['gate']}, which hits no "
                    f"character, is hit: {chain}; no punish situation fires one at the caster)"), extra
        return f'damage only through a SpEffect behavior the spell reader does not fly ({chain})', extra
    status = sorted(x.split(':', 1)[1] for x in tokens if x.startswith('status:'))
    if status:
        return (f"status only ({', '.join(status)}): its status hits are not counted (no AtkParam of its hits is "
                f"one status_hit_atks counts)"), {}
    return 'no damaging hit', {}


def spell_job(c, mid):
    """[entity] for every variant of one spell: inputs, status, tokens."""
    SM, A, t = c.SM, c.A, c.t
    nm, m = SM.names[mid], SM.magic[mid]
    cat = next(cc for cc in c.cats if SM.can_cast(mid, cc))
    head = {'type': 'spell', 'id': mid, 'name': nm, 'parent': f'spell:{mid}', 'carrier': cat['name'],
            'affinity': None, 'slots': m['slotLength'], 'subcat': c.subcat.get(mid, 0)}
    try:
        got = SM.evaluate(mid, cat, c.stats, (), None, c.dfn, False, True)
    except KeyError as e:
        return [_excl('spell', mid, nm, f'spell:{mid}', f'unreadable: {e}')]
    except ERRORS as e:
        return [_excl('spell', mid, nm, f'spell:{mid}', f'model error: {e!r}')]
    if not got:
        anims = [anim for _, anim in SM.variants(mid)]
        tok = spell_ref_tokens(c, mid, anims)
        # A spell whose every list is on a bullet dry ground never makes (`never_made`) says so.
        try:
            wet = [x for _, anim in SM.variants(mid)
                   for x in never_made(c, mid, cat, [(round(f, 1), n) for f, n in SM.release_events(mid, anim)[1]])]
        except ERRORS:
            wet = []
        if wet:
            return [_excl('spell', mid, nm, f'spell:{mid}', 'every hit on a bullet that launches only on water or never',
                          None, dropped=[[h['frame'], h['ref'], h['atk'], h['bullet'], lc, round(h['damage'] * h['count'], 1)]
                                         for h, lc in wet], **tok)]
        # Why there is none: damage the spell reader does not reach, a status it does not price, or
        # nothing (`no_hit_reason`).
        why, more = no_hit_reason(c, mid, anims, tok['tokens'])
        return [_excl('spell', mid, nm, f'spell:{mid}', why, None, **tok, **more)]
    first = got[0]['variant']
    # `evaluate` skips a variant without a damaging hit while keeping the others (the tap of a loop
    # spell, the charged cast of Loretta's Greatbow): each one is an exclusion of its own.
    out = [_excl('spell', mid, nm, f'spell:{mid}:{label_}', 'no damaging hit (variant)', head, variant=label_,
                 **spell_ref_tokens(c, mid, [anim]))
           for label_, anim in SM.variants(mid) if label_ not in {v['variant'] for v in got}]
    for v in got:
        key = f"spell:{mid}:{v['variant']}"
        try:
            sc = spell_scoring(c, mid, cat, v)
        except ERRORS as e:
            out.append(_excl('spell', mid, nm, key, f'model error: {e!r}', head, variant=v['variant']))
            continue
        events, geo, value, one, aims, spec = (sc[k] for k in ('events', 'geo', 'value', 'value_one', 'aims', 'spec'))
        missing, dropped, gone, off = sc['missing'], sc['dropped'], sc['gone'], sc['off']
        missing_hp = round(sum(h['damage'] * h['count'] for h in v['hits'] if (h['frame'], h['ref']) in gone)
                           + sum(h['damage'] * h['count'] for h, _ in off), 1)
        refs = sorted({n for _, n in events})
        trees = [A.bullet_tree(t, m[f'refId{n}']) for n in refs if m[f'refCategory{n}'] == S.REF_BULLET]
        direct = [m[f'refId{n}'] for n in refs if m[f'refCategory{n}'] == S.REF_ATTACK and SM._damaging(m[f'refId{n}'])]
        tokens, flags, sfx = effect_tokens(c, [x for x in trees if x], direct, SM.spell_node_damaging)
        facts = _geo_facts(geo)
        if geo and facts['jumpable']:
            flags = sorted(set(flags) | {'jumpable'})
        # A frame is melee where its geometry is the cast's own AtkParam hitbox
        # (`er-mechanics-spells.SpellModel.direct_geometry`: Carian Slicer), bullet otherwise.
        kinds = {}
        for g, f in geo or ():
            kinds.setdefault(f, set()).add('melee' if g.get('DIRECT') is not None else 'bullet')
        kinds = {f: sorted(kinds.get(f) or {'bullet'}) for f in value}
        # The release clip, the hand-off frame and the 045010 frame a release is let go at: what
        # `SpellModel.look_split` reads to say when a defender can tell this cast from a lookalike
        # one (`look_specs`).
        rec = {**head, 'key': key, 'variant': v['variant'], 'variant_of': None if v['variant'] == first
               else f'spell:{mid}:{first}', 'anim': v['anim'], 'hand': spec['hand'], 'lead': v['lead'],
               'fp': v['fp_cast'] if v['fp_loop'] else v['fp'],
               'fp_loop': v['fp_loop'], 'fp_lower_bound': loop_lower_bound(v['variant'], v['fp_loop'], v['channel']),
               'roll': v['roll'],
               'startup': v['startup'], 'one_hit': round(max(one.values()), 1) if one else v['one_hit'],
               'full': round(S.full_worth(geo, value), 1), 'worth': S.full_worth(geo, value),
               'missing_hp': missing_hp,
               'channel': channelled(v['variant'], v['channel']), 'loop_clip': v['channel'],
               'missing': [[round(f, 1), n, why] for f, n, why in missing],
               'dropped': [[h['frame'], h['ref'], h['atk'], h['bullet'], lc, round(h['damage'] * h['count'], 1)]
                           for h, lc in dropped],
               'dropped_hp': round(sum(h['damage'] * h['count'] for h, _ in dropped), 1),
               'delivery': sorted({k for ks in kinds.values() for k in ks}) or ['bullet'], 'kinds': kinds,
               'tokens': tokens, 'flags': flags, 'sfx': sfx, 'frames': len(value), **facts,
               # The status every hit builds, and its HP on the pool when every hit lands (version 30).
               'status_buildup': sc['status'], 'status_full': round(S.status_full(geo, c.dfn), 1)}
        # What the cast takes from its caster (Briars of Sin's 80 HP a launch): each fired ref's
        # `spEffectIDForShooter` or SpEffect (`er-mechanics-self-damage.spell_cost`), charged in phase
        # 2 (`hp_charge`). A release member keeps the variant's own fired refs (`INFERRED`).
        try:
            fired = [(round(f, 1), n) for f, n in SM.release_events(mid, v['anim'], v['lead'])[1]]
            rec['hp_cost'] = hp_cost_of(_self_damage().spell_cost(t.speffect, t.bullet, m, fired))
        except ERRORS as e:
            rec.update(hp_cost=None, hp_cost_error=repr(e))
        if not geo or not value:
            reasons = list(dict.fromkeys(why for _, _, why in missing))
            if dropped and not value:
                reasons.append('every hit on a bullet that launches only on water or never')
            rec.update(status='excluded', reason=' + '.join(reasons or ['no flight']))
        else:
            rec.update(status='partial' if missing else 'scored', geo=geo, value=value, value_one=one, aims=aims)
            # A release (a tap, an early release) let go on each later frame of its window
            # (`er-mechanics-spells.cast_inputs`' rule, `release_frames`): a member of its own.
            try:
                release_members(c, rec, [(v['lead'], v['releases'][0][1])] + [
                    (lead, w, next((x for x in SM.evaluate(mid, cat, c.stats, (), None, c.dfn, False, True, lead=lead)
                                    if x['variant'] == v['variant']), None)) for lead, w in v['releases'][1:]],
                                lambda vv: spell_scoring(c, mid, cat, vv))
            except ERRORS as e:
                rec.update(status='excluded', reason=f'model error (release members): {e!r}')
        out.append(rec)
    return out


def spell_scoring(c, mid, cat, v):
    """What phase 1 scores of `evaluate` variant `v` of spell `mid` cast with catalyst `cat`, let go
    at its own `lead`: {'events', 'spec' (`geometry`'s variant), 'geo', 'value', 'value_one',
    'aims', 'missing', 'dropped', 'gone', 'off', 'kept'}. `geo` is None when no event flies."""
    SM, A = c.SM, c.A
    events = sorted({(h['frame'], h['ref']) for h in v['hits']})
    spec = {'events': events, 'hand': v['startup'] - min(f for f, _ in events), 'anim': v['anim'],
            'weapon': cat['base'], 'lead': v['lead']}
    missing = []
    geo = SM.geometry(mid, spec, damaging=SM.spell_node_damaging, missing=missing)
    # The hits of an event with no flight are left out of the worth, as `skill_attack` leaves a
    # skill's skipped hits out: a partial entity is scored on what it can reach, and its land
    # share is a share of that. So is a hit list priced on a bullet the straight flight never
    # launches, under an event that does fly (`unflown_hits`): `evaluate`'s `full` rule prices
    # every list of the tree, and the flown root's geometry would carry it.
    gone = {(f, n) for f, n, _ in missing}
    # A list a carrier makes where it touches him is flown on the carrier's path
    # (`er-mechanics-spells.SpellModel.carried_rows`: Loretta's Greatbow), not unflown.
    carried = {g['EVENT']: set(g['CARRIED']) for g, _ in geo or () if g.get('CARRIED') and g.get('EVENT')}
    off, dropped = unflown_hits(c, mid, [h for h in v['hits'] if (h['frame'], h['ref']) not in gone])
    off = [(h, why) for h, why in off if h['bullet'] not in carried.get((h['frame'], h['ref']), ())]
    # The lists `evaluate` no longer prices because dry ground never makes their bullet: recorded
    # as dropped, never in the worth (`never_made`).
    fired = [(round(f, 1), n) for f, n in SM.release_events(mid, v['anim'], v['lead'])[1]]
    dropped += never_made(c, mid, cat, [(f, n) for f, n in dict.fromkeys(fired) if (f, n) not in gone],
                          {h['bullet'] for h in v['hits']})
    missing += [(h['frame'], h['ref'], why) for h, why in off]
    out_ids = {id(h) for h, _ in off} | {id(h) for h, _ in dropped}
    kept = [h for h in v['hits'] if (h['frame'], h['ref']) not in gone and id(h) not in out_ids]
    value, one = {}, {}
    for h in kept:
        value[h['frame']] = value.get(h['frame'], 0.0) + h['damage'] * h['count']
        one[h['frame']] = max(one.get(h['frame'], 0.0), h['damage'])
    value, one = dict(sorted(value.items())), dict(sorted(one.items()))
    # An event that lost every hit to `unflown_hits` keeps no geometry: `_bursts` would credit
    # its catch with every later frame's worth.
    geo = [(g, f) for g, f in (geo or []) if f in value] or None
    # Each event's hit lists, tick by tick, for the caught defender (`net_parts`), with the status
    # each builds (`annotate_status`, version 30).
    SM.attach_caught(mid, geo, kept, value)
    ws = spell_status_ws(c)
    status = annotate_status(c, geo, ws)
    # The same cast free-aimed at the ground, where a carrier's ground contact is its damage
    # (`er-mechanics-ashes.floor_launch`): the outcome takes the better aim per situation
    # (`er-mechanics-spells.net_parts_aims`).
    aims = []
    if geo and SM.floor_aims(mid, spec, SM.spell_node_damaging):
        for g_m in A.FLOOR_AIM_M:
            fgeo = [(g, f) for g, f in SM.geometry(mid, spec, damaging=SM.spell_node_damaging, missing=[],
                                                   floor=g_m) or () if f in value]
            if fgeo:
                SM.attach_caught(mid, fgeo, kept, value)
                annotate_status(c, fgeo, ws)
                aims.append((f'floor {g_m:g} m', fgeo))
    return {'events': events, 'spec': spec, 'geo': geo, 'value': value, 'value_one': one, 'aims': aims,
            'missing': missing, 'dropped': dropped, 'gone': gone, 'off': off, 'kept': kept, 'status': status}


def member_key(key, delay):
    """The key of entity `key`'s release let go `delay` real frames after its earliest
    (`release_members`)."""
    return f'{key}~{round(float(delay), 3):g}'


def release_members(c, rec, rel, score):
    """Give release entity `rec` (scored, at its earliest release) its later releases: the release
    frame is the attacker's choice, which the defender cannot see coming (`er-mechanics-ashes`
    `release_frames`; module doc, 'The outcome per situation').
    `rel` is [(its own release frame, its weight)] then [(release frame, weight, what `score` takes)]
    for each later one; `score(x)` gives {'geo', 'value', 'value_one', 'aims'} and, for a spell,
    'spec', for a skill 'roll' and 'press'. Sets `rec['release_weight']` (its own weight),
    `rec['release_members']` ([{'key' (`member_key`), 'at' (its release frame), 'delay' (after the
    earliest), 'weight', 'geo', 'value', 'value_one', 'aims', 'roll', for a spell 'mid', 'anim',
    'hand', 'lead', for a skill 'press'}]) and
    `rec['releases']` ([[release frame, weight]], the record's). A later release with no flight is
    left out and the others' weights scaled back to 1, as `er-mechanics-spells.cast_inputs` does.
    One release frame: no member, weight 1, the entity as before."""
    first, w0 = float(rel[0][0]), float(rel[0][1])
    members = []
    for at, w, x in rel[1:]:
        got = score(x) if x is not None else None
        if not got or not got.get('geo') or not got.get('value'):
            continue
        m = {'key': member_key(rec['key'], float(at) - first), 'at': float(at), 'delay': float(at) - first,
             'weight': float(w), 'geo': got['geo'], 'value': got['value'], 'value_one': got['value_one'],
             'aims': got.get('aims') or [], 'roll': got['roll'] if 'roll' in got else x['roll']}
        if got.get('spec') is not None:
            m.update(mid=rec['id'], anim=rec['anim'], hand=got['spec']['hand'], lead=got['spec']['lead'])
        if got.get('press') is not None:
            m['press'] = float(got['press'])
        members.append(m)
    total = w0 + sum(m['weight'] for m in members)
    rec['release_weight'] = w0 / total
    for m in members:
        m['weight'] /= total
    rec['release_members'] = members
    rec['releases'] = [[round(first, 3), round(rec['release_weight'], 6)]] + [
        [round(m['at'], 3), round(m['weight'], 6)] for m in members]
    return rec


def unflown_hits(c, mid, hits):
    """([(hit, reason)] missing, [(hit, launch condition)] dropped) among `evaluate` hits of spell
    `mid` whose event flies: a hit list priced on a bullet `SpellModel.launch_gaps` names. One that
    launches on water or never (`NEVER_ON_LAND`: Honed Bolt's 10693006, condition 2) is dropped;
    any other (Roiling Magma's 10482004, under the on-hit child 10482002) has no reach model."""
    SM, A, t = c.SM, c.A, c.t
    m = SM.magic[mid]
    gaps = {}
    off, dropped = [], []
    for h in hits:
        n = h['ref']
        if h.get('bullet') is None or m[f'refCategory{n}'] != S.REF_BULLET:
            continue
        if n not in gaps:
            bt = A.bullet_tree(t, m[f'refId{n}'])
            gaps[n] = SM.launch_gaps(bt) if bt else {}
        if h['bullet'] not in gaps[n]:
            continue
        lc = gaps[n][h['bullet']]
        if lc in S.NEVER_ON_LAND:
            dropped.append((h, lc))
        else:
            off.append((h, f"{S.MISS_UNFLOWN} ({S.launch_text([lc])}; bullet {h['bullet']}, atk {h['atk']})"))
    return off, dropped


def never_made(c, mid, cat, events, have=()):
    """[(hit, launch condition)]: the hit lists of spell `mid`'s cast events `events` [(frame, ref)]
    that sit on a bullet dry ground never makes (`SpellModel.launch_gaps` names it with a condition
    in `NEVER_ON_LAND`: on water, or never) and that `evaluate` no longer prices, so that the record
    still says what was dropped. `evaluate` stopped pricing them when `bullet_lists` began skipping
    a child neither ending makes on dry ground (`er-mechanics-ashes.launches`, bd
    er-effects-rs-rggx): Honed Bolt's 10693006 (condition 2) left its hits and with them the
    record's `dropped`. The subtree under the topmost such node is grouped into hit lists by
    `bullet_lists`' record rule (a flagged or attach row starts a record its volley and its
    children share, unless a row's `attachEffectType` drops it; an unflagged row a list per
    bullet), each list priced as `evaluate` prices a hit (`SpellModel.hit` of its best member at
    the context's stats and defender, no talismans), once per instance (`INFERRED`: no tick rule,
    since it is never made on land; `bullet_lists` itself gates the subtree's children by
    `launches` and so gives nothing for it). Bullets in `have` (already among `evaluate`'s hits)
    are left to `unflown_hits`."""
    SM, A, t = c.SM, c.A, c.t
    m = SM.magic[mid]
    rate, correct, _ = SM.rates(mid, cat, ())
    out = []
    for n in sorted({n for _, n in events}):
        if m[f'refCategory{n}'] != S.REF_BULLET or m[f'refId{n}'] <= 0:
            continue
        bt = A.bullet_tree(t, m[f'refId{n}'])
        gaps = SM.launch_gaps(bt) if bt else {}
        tops, todo = [], [(bt, False)]
        while todo:
            node, above = todo.pop()
            if node is None:
                continue
            gap = node['bullet'] in gaps and gaps[node['bullet']] in S.NEVER_ON_LAND
            if gap and not above:
                tops.append(node['bullet'])
            todo += [(ch, above or gap) for ch in node['children'].values()]
        for top in tops:
            lists = []

            def walk(b, instances, record, path):
                r = SM.bullet.get(b)
                if r is None or b in path or b in have or len(path) > S.MAX_DEPTH:
                    return
                shots = max(int(r['numShoot']), 1)
                recorded = bool(r['isUseSharedHitList'] or r['attachEffectType'])
                own = record
                if own is None:
                    own = {'mult': instances * (1 if recorded else shots), 'best': None}
                    lists.append(own)
                atk = r['atkId_Bullet']
                if atk and atk > 0 and SM._damaging(atk) and (own['best'] is None
                                                              or SM._base(atk) > SM._base(own['best'][0])):
                    own['best'] = (atk, b)
                down = own if (record is not None or recorded) and not r['attachEffectType'] else None
                if r['HitBulletID'] > 0:
                    walk(r['HitBulletID'], instances * shots, down, path + (b,))
                iv = (r['intervalCreateTimeMin'] + r['intervalCreateTimeMax']) / 2
                if r['intervalCreateBulletId'] > 0 and iv > 0 and r['life'] > 0:
                    k = len(A.interval_frames(r, interval=iv))
                    walk(r['intervalCreateBulletId'], instances * shots * k, down, path + (b,))
            walk(top, 1, None, ())
            for li in lists:
                if li['best'] is None:
                    continue
                atk, b = li['best']
                dmg = SM.hit(atk, b, cat, c.stats, rate, correct, c.dfn)['damage']
                out += [({'frame': f, 'ref': n, 'atk': atk, 'bullet': b, 'damage': dmg, 'count': float(li['mult'])}, gaps[top])
                        for f, n_ in events if n_ == n]
    return out


def best_affinity(c, job, weapon):
    """(affinity, [(affinity, error repr)]): the mountable affinity whose `skill_option` damage is
    largest, ties to the lower index (Standard is index 0), and every affinity the model raised on,
    which the pick could not weigh."""
    A, t, SM = c.A, c.t, c.SM
    wid = job['carrier']
    top = SM.ar.max_level(SM.ar.weapons[wid]['reinforceTypeId'])
    best, errors = None, []
    for i, aff in enumerate(AR.AFFINITIES):
        if not t.can_mount(wid, job['gem'], i, top)[0]:
            continue
        try:
            ctx = A.WeaponContext(weapon, aff, None, c.stats, False, ar_tables=SM.ar)
            o = A.skill_option(t, wid, job['sid'], ctx, ctx.level, c.dfn, S.DEF)
        except ERRORS as e:
            errors.append((aff, repr(e)))
            continue
        dmg = (o or {}).get('dmg') or 0.0
        if best is None or dmg > best[0]:
            best = (dmg, aff)
    return (best[1] if best else 'Standard'), errors


def skill_job(c, job):
    """[entity] for one opening group of one skill: the default cast or each stance button, and each
    follow-up."""
    A, t, SM = c.A, c.t, c.SM
    sid, wid = job['sid'], job['carrier']
    weapon = SM.ar.names[wid]
    parent = job_parent(job)
    head = {'type': 'skill', 'id': sid, 'name': job['name'], 'parent': parent, 'carrier': weapon,
            'carrier_id': wid, 'opening': job['opening'], 'kind': job['kind'], 'mount_classes': job['mount_classes'],
            'gem_obtainable': job.get('gem_obtainable', True),
            'slots': 0, 'subcat': None, 'group_size': job['group'], 'pool': job['pool'],
            'requirement': job['requirement'], 'affinity': None}
    try:
        aff, aff_errors = best_affinity(c, job, weapon) if job['mountable'] else ('Standard', [])
        top = SM.ar.max_level(SM.ar.weapons[wid]['reinforceTypeId'])
        prof = A.skill_profile(t, sid, wid, 0, False)
        buttons = [b for b, _ in A.stance_buttons(prof)]
    except ERRORS as e:
        return [_excl('skill', sid, job['name'], parent, f'model error: {e!r}', head)]
    head['affinity'] = aff
    head['affinity_errors'] = [list(x) for x in aff_errors]
    two_only = t.reg.weapon[wid].get('wepType') in TWO_HAND_ONLY_WEP_TYPES
    out = []
    for stance in buttons or [None]:
        key = parent + (f':stance-{stance}' if stance else '')
        main = skill_entity(c, head, weapon, aff, stance, None, key, None, two_only)
        out.append(main)
        try:
            releases = skill_releases(c, sid, wid, top, stance)
        except ERRORS as e:
            out.append(_excl('skill', sid, job['name'], key + ':tap', f'model error: {e!r}', head))
            releases = []
        # A charged skill: the default entity holds the charge to the end (its opening clip, which
        # carries the full-charge hits), and the early release is a variant of its own, as a
        # spell's tap is.
        if releases:
            main['release'] = 'full'
        for r in releases:
            out.append(skill_entity(c, head, weapon, aff, stance, r, release_key(key, r), key, two_only))
        try:
            follows = A.skill_followups(t, sid, wid, top, 'one', stance)
        except ERRORS as e:
            out.append(_excl('skill', sid, job['name'], key + ':then', f'model error: {e!r}', head))
            follows = []
        # Two follow-ups can share a button and a clip and differ in the press window (Stamp
        # (Sweep) on the Club: R2 040010 from f18 and from f10, two TAE 66 windows): the key
        # carries the press frame. Entries equal in all three are one attack, kept once.
        kept = {}
        for f in follows:
            k2 = followup_key(key, f)
            if k2 in kept:
                kept[k2]['follow_duplicates'] = kept[k2].get('follow_duplicates', 0) + 1
                continue
            kept[k2] = skill_entity(c, head, weapon, aff, stance, f, k2, key, two_only)
            out.append(kept[k2])
    return out


def followup_key(key, f):
    """The key of follow-up `f` (a `skill_followups` entry) of entity `key`: its button, clip and
    the real frame it is pressed at."""
    return key + f":then-{f['button']}-{f['anim']:06d}-f{round(float(f['first']), 1):g}"


#: TAE event 66 SpEffect on a charged skill's opening during which letting go of L2 plays the early
#: release (`er-mechanics-spells.RELEASE_EARLY_SPEFFECT`, which has the sources).
RELEASE_EARLY_SPEFFECT = S.RELEASE_EARLY_SPEFFECT


def skill_releases(c, sid, wid, level, stance=None):
    """`er-mechanics-spells.skill_releases` on the context's model: the early release of skill
    `sid`'s opening on weapon `wid`, let go at the `RELEASE_EARLY_SPEFFECT` window's first real
    frame. `skill_option` prices it as a follow-up, whose FP adds the button's column;
    `skill_entity` takes that back off, the release being the same cast."""
    return S.skill_releases(c.A, c.t, sid, wid, level, stance)


def release_key(key, r):
    """The key of early release `r` (a `skill_releases` entry) of entity `key`."""
    return key + f":tap-{r['anim']:06d}-f{round(float(r['first']), 1):g}"


def skill_entity(c, head, weapon, aff, stance, follow, key, variant_of, two_only):
    A, t, SM = c.A, c.t, c.SM
    sid, wid = head['id'], head['carrier_id']
    rec = {**head, 'key': key, 'variant': None, 'variant_of': variant_of, 'stance': stance,
           'follow': None if follow is None else {k: follow[k] for k in ('anim', 'button', 'first', 'last')},
           'release': None if follow is None else follow.get('release'),
           'fp_loop': None, 'fp_lower_bound': False, 'channel': False, 'tokens': [], 'flags': [], 'sfx': [],
           'missing': [], 'missing_hp': None, 'dropped': [], 'dropped_hp': 0.0}
    try:
        d = SM.skill_attack(weapon, c.stats, aff, None, sid, c.dfn, False, follow, stance, priced_bullets=True,
                            flown_only=True)
        # The status each hit list builds on the carrier at its affinity and the line's stats
        # (`annotate_status`, version 30).
        ws = skill_status_ws(c, wid, aff)
        rec['status_buildup'] = annotate_status(c, d['geo'], ws)
        rec['status_full'] = round(S.status_full(d['geo'], c.dfn), 1)
    except ERRORS as e:
        return {**rec, 'status': 'excluded', 'reason': f'model error: {e!r}'}
    o = d['option'] or {}
    fp = d['fp']
    if rec['release'] and fp is not None:
        # `skill_option` adds the follow-up button's column (`useMagicPoint_L2`); a release is no
        # second press. Whether letting go before the opening's FP event still pays it is not traced.
        fp -= t.arts[sid][f"useMagicPoint_{follow['button'].upper()}"]
    # A follow-up's press frame from the attack's start (`skill_option` part 1's offset): its
    # geometries from there on are the follow-up's, which a later press moves (`FollowupForm`). An
    # early release is one too: L2 let go on any frame of the 100285 window plays it (`VERIFIED`
    # c0000.hks `SwordArtsOneShot_onUpdate`, bd skill-tap-vs-hold-speffect-100285-l2-up), the
    # attacker's choice: its first frame here, the later ones its release members (below).
    parts_ = o.get('parts') or []
    press = float(parts_[1]['offset']) if follow is not None and len(parts_) > 1 else None
    rec.update(variant='tap' if rec['release'] else d['variant'], fp=fp, roll=d['roll'], press=press,
               opening_dropped=d['opening_dropped'], cross_part_duplicates=d.get('part_duplicates', 0),
               dropped=[[a, b, lc, round(hp, 1)] for a, b, lc, hp in d['dropped']],
               dropped_hp=round(sum(hp for *_, hp in d['dropped']), 1))
    if d['why'] == 'no damaging hit':
        try:
            prof = A.skill_profile(t, sid, wid, 0, False, stance)
            acts = [x for a, xs in prof['anims'].items() if a not in prof['no_fp_anims'] for x in xs]
            classes = prof['classes']
            # The effects of what it does fire, read on every node and AtkParam, so a status-only or
            # grab-only skill still joins its effect family as an exclusion.
            trees = [A.bullet_tree(t, x['bullet']['bullet']) for x in acts
                     if x.get('kind') == 'bullet' and isinstance(x.get('bullet'), dict)]
            melee = [x['atk_row'] for x in acts if x.get('kind') == 'melee' and x.get('atk_row')]
            tokens, flags, sfx = effect_tokens(c, [x for x in trees if x], melee, _any_node)
        except ERRORS as e:
            return {**rec, 'status': 'excluded', 'reason': f'model error: {e!r}'}
        rec.update(tokens=tokens, flags=flags, sfx=sfx)
        if any(x.get('kind') == 'unresolved' for x in acts):
            return {**rec, 'status': 'excluded', 'reason': 'bullet judge unresolved (ammo)'}
        # A throw: an AtkParam of its clips is a grab (`throwTypeId`) and `skill_option` priced no
        # hit (Lifesteal Fist: the grab row 300000025 is in clip 040000, which the hit reading never
        # reaches from the 040090 opening, `MEASURED` round 2), so the model has nothing to price.
        throws = sorted({(c.ax.get(r) or {}).get('throwTypeId', 0) for r in melee} - {0})
        if throws:
            return {**rec, 'status': 'excluded',
                    'reason': f"grab not modelled (throwTypeId {', '.join(str(x) for x in throws)})"}
        return {**rec, 'status': 'excluded', 'reason': f"no damaging hit ({', '.join(classes) or 'no class'})"}
    if d['why']:
        return {**rec, 'status': 'excluded', 'reason': d['why']}
    if two_only:
        return {**rec, 'status': 'excluded', 'reason': 'two-handed opening, reach read one-handed'}
    hits = o.get('hit_rows') or []
    trees = [A.bullet_tree(t, b) for b in dict.fromkeys(h['root_bullet'] for h in hits
                                                         if h['kind'] == 'bullet' and h.get('root_bullet'))]
    melee = [h['atk_row'] for h in hits if h['kind'] == 'melee']
    tokens, flags, sfx = effect_tokens(c, [x for x in trees if x], melee, A._bullet_damages_self)
    wtok, wrows = weapon_status_tokens(c, wid, aff, hits)
    tokens = sorted(set(tokens) | set(wtok))
    rec['weapon_status'] = wrows if wtok else []
    try:
        rec['stat_weights'] = hit_stat_weights(c, wid, aff, hits)
    except ERRORS as e:
        rec.update(stat_weights=None, stat_weights_error=repr(e))
    facts = _geo_facts(d['geo'])
    if facts['jumpable']:
        flags = sorted(set(flags) | {'jumpable'})
    value, one = d['value'], d['value_one']
    if follow is None:
        # An opening with no hit of its own is scored on another FP-charging clip (`skill_hits`'
        # fallback: Stamp's 040000 on 040010, its R2 follow-up), whose own charge `skill_fp` leaves
        # out (`REGULATION` Stamp 5 / 8): its FP is a lower bound, as a held loop spell's is.
        try:
            prof = A.skill_profile(t, sid, wid, 0, False, stance)
        except ERRORS:
            prof = None
        if prof is not None:
            main = A.main_anim(prof)
            anims = sorted({h.get('anim') for h in o.get('hit_rows') or []} - {None})
            if main is not None and anims and all(x in prof['fp_anims'] and x // 10 != main // 10 for x in anims):
                rec.update(fp_lower_bound=True,
                           fp_lower_why=f"opening {main:06d} has no hit; scored on the FP-charging clip "
                                        f"{', '.join(f'{x:06d}' for x in anims)}")
    # The full worth counts a hit priority record's hits once (`er-mechanics-spells.full_worth`,
    # version 31): `value` sums every geometry of a frame, which `CaughtModel` weighs, not lands.
    worth = S.full_worth(d['geo'], value)
    rec.update(tokens=tokens, flags=flags, sfx=sfx, startup=round(min(value), 1),
               one_hit=round(max(one.values()), 1), full=round(worth, 1), worth=worth, frames=len(value),
               missing=[[a, k, why] for a, k, why in d['skipped']],
               missing_hp=d['skipped_hp'] if d['skipped'] else None, geo=d['geo'], value=value, value_one=one,
               kinds=d['kinds'], delivery=sorted({k for ks in d['kinds'].values() for k in ks}),
               status='partial' if d['skipped'] else 'scored', **facts)
    rec['inherited_frames'] = inherited_frames(rec)
    # What the cast takes from its own user (Bloody Slash's 1763 at f15, hit or not): its opening's
    # TimeAct SpEffects and a follow-up's or release's clip (`er-mechanics-self-damage.skill_cost`),
    # charged against every net at each line's own max HP in phase 2 (`hp_charge`).
    try:
        rec['hp_cost'] = hp_cost_of(_self_damage().skill_cost(
            t, sid, wid, 0, False, stance, follow['anim'] if follow is not None else None))
    except ERRORS as e:
        rec.update(hp_cost=None, hp_cost_error=repr(e))
    if rec['release'] == 'tap' and press is not None:
        # The early release is let go on a frame of its window the attacker picks: one member per
        # release frame (`release_members`), each the release clip from its own press, the
        # opening's hits before that press kept (`skill_attack` with the press moved). With no
        # opening hit before the release the defender has nothing to roll before it, and the
        # release is read as a lookalike of its hold, the attacker netting the members' mean
        # ('mixture'); with one, the release stays a press the attacker times on the roll he saw
        # (`FollowupForm`, 'followup'), which the members' mean would drop.
        rec['release_rule'] = 'followup' if rec['inherited_frames'] else 'mixture'
        rel = A.release_frames(follow['first'], follow['last'])

        def score(f2):
            d2 = SM.skill_attack(weapon, c.stats, aff, None, sid, c.dfn, False, f2, stance, priced_bullets=True,
                                 flown_only=True)
            p2 = (d2.get('option') or {}).get('parts') or []
            if d2['why'] or len(p2) < 2:
                return None
            annotate_status(c, d2['geo'], ws)
            return {'geo': d2['geo'], 'value': d2['value'], 'value_one': d2['value_one'], 'roll': d2['roll'],
                    'press': float(p2[1]['offset'])}
        try:
            release_members(c, rec, [rel[0]] + [(r, w, {**follow, 'first': r}) for r, w in rel[1:]], score)
            # Where each release shows: real frames from its press to the first frame its pose is
            # off the hold's (`er-mechanics-ashes.skill_release_visible`), what its split is.
            shows = {round(float(r), 6): A.skill_release_visible(t, sid, follow['anim'], r) for r, _ in rel}
            rec['release_show'] = shows[round(float(rel[0][0]), 6)]
            for m in rec['release_members']:
                m['shows'] = shows[round(m['at'], 6)]
        except ERRORS as e:
            return {**rec, 'status': 'excluded', 'reason': f'model error (release members): {e!r}'}
    return rec


def inherited_frames(rec):
    """The damaging frames of follow-up entity `rec` that come before its press: the opening's own
    hits, which it keeps (`skill_attack` leaves out only those at or after the press). A component
    there would re-emit the opening's association (the Glintblade Phalanx R2 follow-ups' f33
    volley, the opening's whole worth: D_contain 0.06897 to the spell, the opening's own D_assoc,
    `MEASURED` round 4), so it gets none and the association stays the opening's. The press is
    `press` (a stance's follows its entry: Moon-and-Fire Stance R1, window f35, press f43), else the
    window's first frame."""
    f = rec.get('follow')
    at = rec.get('press') if rec.get('press') is not None else (float(f['first']) if f is not None else None)
    return sorted(x for x in rec.get('value') or {} if at is not None and x < float(at) - 1e-9)


def components(rec):
    """[component input] of one scored entity: one per damaging frame, the `MAX_COMPONENTS` most
    valuable, and how many were left out. A follow-up's frames before its press are the opening's
    (`inherited_frames`) and get none."""
    if rec.get('status') != 'scored' or len(rec['value']) < 2:
        return [], 0
    skip = set(inherited_frames(rec))
    geos = {}
    for g, fr in rec['geo']:
        geos.setdefault(fr, []).append((g, fr))
    # A frame's worth counts its hit priority records once (`er-mechanics-spells.full_worth`,
    # version 31), for its rank as for its land shares.
    worth = {f: S.full_worth(geos.get(f), {f: rec['value'][f]}) for f in rec['value']}
    frames = sorted((f for f in rec['value'] if f not in skip), key=lambda f: (-worth[f], f))
    out = []
    for f in frames[:MAX_COMPONENTS]:
        geo = geos.get(f)
        if not geo:
            continue
        out.append({'key': f"{rec['key']}@f{round(f, 2):g}", 'of': rec['key'], 'type': rec['type'], 'frame': f,
                    'geo': geo, 'value': {f: rec['value'][f]}, 'value_one': {f: rec['value_one'][f]},
                    'roll': rec['roll'], 'full': round(worth[f], 1), 'worth': worth[f], 'parent': rec['parent'],
                    'delivery': list((rec.get('kinds') or {}).get(f) or rec.get('delivery') or []),
                    'cost': _geo_facts(geo)['cost']})
    return out, max(0, len(frames) - MAX_COMPONENTS)


# ---------------------------------------------------------------------------------------------
# phase 2: the per-situation profile

def pairs_of(c, x_or_sprint):
    """The situations (`net_parts`' form) an entity's input `x` meets, or, given a bool, those of a
    cast pressed while sprinting or not: `c.sprint_pairs` for a 'dash' (`profile_inputs` 'sprint',
    `er-mechanics-spells.sprint_situations`), `c.pairs` for everything else. Index i of either is
    the same whiff, weighing `c.w[i]`."""
    sprint = x_or_sprint if isinstance(x_or_sprint, bool) else bool(x_or_sprint.get('sprint'))
    return c.sprint_pairs if sprint else c.pairs


def profile_arrays(c, x, form=None, look=None, catches=None):
    """{array: float32 per situation} of one entity or component (`net_parts`; a follow-up's,
    `x['press']` set, `FollowupForm.parts`, from `form` when given; otherwise with `aims`, the
    free-aimed alternatives, per situation the better aim, `er-mechanics-spells.net_parts_aims`,
    and `aims_won`: {aim: share of the situation weight it is taken in} when one of them is). A
    follow-up has no free aim: free aim is a spell's. `look` (an `er-mechanics-spells.Lookalikes`
    member, `look_specs`) is what the defender cannot tell the attack from; None, he knows it from
    its first frame. `catches`, a dict, keeps the geometry's reads for the next call on the same
    inputs with another `look` (`er-mechanics-spells.net_parts_aims`)."""
    if x.get('press') is not None:
        parts = (form or FollowupForm(c, x, look=look)).parts(x['value'], x['value_one'], x['geo'])
    else:
        parts = S.net_parts_aims(c.A, x['geo'], x.get('aims'), x['value'], pairs_of(c, x), x['roll'], c.strikes,
                                 x['value_one'], defender=c.dfn, look=look, catches=catches)
    # A hit priority record's hits count once (`er-mechanics-spells.full_worth`, version 31).
    full = S.full_worth(x['geo'], x['value'])
    # Its full worth carries the status HP of every hit landing (`er-mechanics-spells.status_full`,
    # version 30), which what lands now carries: a share stays a share of the whole.
    sf = S.status_full(x['geo'], c.dfn) if S.has_status(x['geo']) else 0.0
    full = (full + sf if sf else full) or 1.0
    f32 = np.float32
    won = {}
    for p, w in zip(parts, c.w):
        won[p.get('aim', 'target')] = won.get(p.get('aim', 'target'), 0.0) + float(w) / c.W
    return {'aims_won': {k: round(v, 4) for k, v in sorted(won.items())} if set(won) - {'target'} else None,
            'land_now': np.array([p['now']['value'] / full for p in parts], f32),
            'pun_now': np.array([p['now']['p_punish'] for p in parts], f32),
            'base_now': np.array([p['now']['base'] for p in parts], f32),
            't_first': np.array([p['now']['t_first'] for p in parts], f32),
            'land': np.array([p['value'] / full for p in parts], f32),
            'pun': np.array([p['p_punish'] for p in parts], f32),
            'net': np.array([p['net'] for p in parts], f32),
            'net_one': np.array([p['net_one'] for p in parts], f32),
            'gap': np.array([np.nan if p['gap'] is None else p['gap'] for p in parts], f32)}


def geo_fingerprint(geo, roll, aims=None, press=None, delays=None, look=None, sprint=False):
    """The first 16 hex digits of a sha256 over what `net_parts` reads of an attack besides its
    worth: every geometry's arrays (the launch times `TL` too), `AIR` mark and frame, its `CAUGHT`
    lists' shape, its hit priority record (`HPR`), its homing record's signature (`HOMING`: the
    tree that is flown again after each dodge, `er-mechanics-ashes._homing_touch`), the roll frame,
    its free-aimed alternatives, for a follow-up its press frame and delays, and the group of
    attacks the defender cannot tell it from (`look`, `look_sig`: none for an attack alone in its
    group, which fingerprints as it did before groups). Two inputs with one fingerprint share one
    `OutcomeForm` (a follow-up's: `FollowupForm`); the worth weights a `CAUGHT` record also holds
    are each build's own (`OutcomeForm.net`). A cast pressed while sprinting (`sprint`, `pairs_of`)
    meets other situations, so it fingerprints apart; anything else fingerprints as before."""
    h = hashlib.sha256(repr(float(roll)).encode())
    if sprint:
        h.update(b'sprint')
    if S.has_status(geo):
        # A form's caught defender is the status model when the geometry it is made from builds a
        # status (`er-mechanics-spells.status_caught`), so a geometry that builds none never shares
        # its form (version 30). The 2026-11-22 selftest stopped on Sword Dance's 045800 opening, 0.159
        # HP past `FORM_TOL`: the distance basis rides the Reverse-Bladed Sword at Cold (183 frost),
        # the Strength, Faith and Intelligence lines the same sword at Fire, Sacred and Magic (no
        # status, the same shapes), so a form made from one of their geometries (the plain caught
        # model) netted the basis with its frost left out (`MEASURED` with perf-similarity-dump: the
        # same 0.1585336 HP on that code, and no disagreement with this). Anything else
        # fingerprints as before.
        h.update(b'status')
    if look is not None:
        h.update(f'look {look}'.encode())
    if press is not None:
        h.update(repr((float(press), [float(d) for d in delays or ()])).encode())
    if aims:
        # The free-aimed alternatives (`profile_arrays`), each its own geometry list.
        for label, g in aims:
            h.update(f'aim {label} {geo_fingerprint(g, roll)}'.encode())
    for g, fr in geo:
        # What `er-mechanics-ashes.CaughtModel` plays besides the worth weights (`caught_shape`).
        lists = (g.get('CAUGHT') or {}).get('lists')
        # A list's own Bullet rows (`rows` over the geometry's `B`) decide when it lands.
        shape = None if lists is None else [(l['n'], round(l['rec'], 4), round(l['poise'], 4), l['atk'])
                                            + ((l['rows'],) if l.get('rows') and 'B' in g else ())
                                            for l in lists]
        hom = (g.get('HOMING') or {}).get('sig')
        # The TAE 795 windows the defender's strike reads (`er-mechanics-spells.strike_breaks`), and
        # the attacker's own guard it meets (`er-mechanics-spells.strike_guard`).
        tough = g.get('TOUGHNESS') or None
        guard = g.get('GUARD') or None
        # A bullet chain's part (`er-mechanics-ashes.split_chains`): the frame it shares with the next.
        chain = g.get('CHAIN')
        # The frame a search launches it, which the defender reacts from (`er-mechanics-ashes.burst_cue`).
        cue = g.get('CUE')
        # The press the attacker times to start a skill, which the defender reacts from too.
        pcue = g.get('PRESS_CUE')
        # The clip's whole root motion, which the punish reads (`er-mechanics-ashes.attack_advance`).
        adv = g.get('ADV')
        # The clip's turn rules the attacker's facing follows (`er-mechanics-ashes._facing`, version 27).
        turn = g.get('TURN')
        h.update(repr((float(fr), bool(g.get('AIR')), shape, g.get('HPR'), hom) + ((tough,) if tough else ())
                      + (('guard', guard) if guard else ())
                      + (('chain', tuple(float(x) for x in chain)) if chain is not None else ())
                      + (('cue', float(cue)) if cue is not None else ())
                      + (('press_cue', float(pcue)) if pcue is not None else ())
                      + (('adv', tuple(round(float(x), 4) for x in adv)) if adv is not None else ())
                      + (('turn', turn['sig'], float(turn['t0']), g.get('T0')) if turn is not None else ())).encode())
        for k in ('T', 'X', 'Z', 'R', 'PK', 'TL'):
            if k in g:
                h.update(np.ascontiguousarray(np.asarray(g[k], float)).tobytes())
        if shape and any(len(s) > 4 for s in shape):
            h.update(np.ascontiguousarray(np.asarray(g['B'], float)).tobytes())
    return h.hexdigest()[:16]


def _geo_bursts(geo):
    """`er-mechanics-spells._bursts` without the worths: [(geometries, their frames)], the same
    order and the same grouping (overlapping live frames)."""
    bursts = []
    for g, fr in sorted(geo, key=lambda e: e[0]['T'].min()):
        if bursts and g['T'].min() <= bursts[-1][2]:
            bursts[-1][0].append(g), bursts[-1][1].append(fr)
            bursts[-1][2] = max(bursts[-1][2], g['T'].max())
        else:
            bursts.append([[g], [fr], g['T'].max()])
    return [(gs, frs) for gs, frs, _ in bursts]


def _situation_keys(c, sprint=False):
    """Per situation of `c.pairs` (`c.sprint_pairs` with `sprint`, `pairs_of`), the
    `er-mechanics-spells.situation_key`s `net_parts` reads, each
    with the move floor it is read at (the key drops it up close; a caught defender's run out reads
    it, version 21): pressing at once, then each run-in of `APPROACH_GAPS_M` short of its distance,
    whose run uses up the defender's two floors alike (his dodge cancel and his move cancel)."""
    per_m = c.A._neutral().frames_per_metre()
    out = []
    for sit in pairs_of(c, sprint):
        dist, floor = sit[0], sit[1]
        run = sit[2] if len(sit) > 2 else floor
        row = [(dist, floor, run)] + [(g, max(0.0, floor - (dist - g) * per_m), max(0.0, run - (dist - g) * per_m))
                                      for g in S.APPROACH_GAPS_M if g < dist]
        out.append([(S.situation_key(c.A, d, f, r), r) for d, f, r in row])
    return out


class OutcomeForm:
    """`er-mechanics-spells.net_parts`' `net` per situation as a function of an attack's worth per
    frame, for one geometry list and roll frame: what an attacker build with other stats lands from
    the same reach at other damage. A defender's outcome is linear in the worths and his evasion
    and punish chances do not read them: the bursts chain as `sequence_outcome` chains them
    (`er-mechanics-ashes.dodge_chain`, worth-free), the defender keeps to the move family that leaves the attacker least or
    stands where that leaves less (`er-mechanics-ashes.stand_or`), and
    the attacker takes the best of pressing at once and each run-in of `APPROACH_GAPS_M`, as in
    `net_parts`. So one pass over the geometries serves every worth, the one-hit worths included.
    Every run compares it with `net_parts` wherever a build shares the distance basis's geometry
    (`FORM_TOL`), and the selftest on a spell, a multi-burst skill and a jumpable attack. `look` (a
    `Lookalikes` member, `profile_arrays`') times his dodge of the first burst that reaches him
    standing in each family, as `sequence_outcome`'s `plan`; the form then belongs to that group as
    well as to the geometry (`geo_fingerprint` `look`). `sprint` reads the situations of a cast
    pressed while sprinting (`pairs_of`), as `profile_arrays` reads a 'dash'."""

    def __init__(self, c, geo, roll, look=None, sprint=False):
        A = c.A
        bursts = _geo_bursts(geo)
        self.frames = [float(fr) for _, frs in bursts for fr in frs]
        sizes = [len(gs) for gs, _ in bursts]
        offs = np.cumsum([0] + sizes)[:-1]
        G = len(self.frames)
        families = A.dodge_families(any(g is not None and g.get('AIR') for gs, _ in bursts for g in gs))
        # Where the whole attack's root motion leaves the attacker for the punish, as
        # `sequence_outcome` reads it (`er-mechanics-ashes.attack_advance`).
        advance = A.attack_advance([g for gs, _ in bursts for g in gs])
        # A caught defender played out (`er-mechanics-ashes.CaughtModel`, as `net_parts` plays him)
        # and what one cast lands capped at his health: each pool build's own poise and health
        # (`net_parts`' `cpoise` and `hp`, version 21).
        # Its status build-up priced per play when it builds any (`er-mechanics-spells.status_caught`,
        # version 30), as `net_parts` prices it.
        self.caught = S.status_caught(geo, c.dfn.get('pool') or c.dfn.get('poise_pool') or c.dfn.get('poise'),
                                      None, c.dfn)
        self.hp = c.dfn.get('hp_pool') or c.dfn.get('hp')
        # Per (dist, floor) the roll's form (`keys`): the dodge reads no `run`. Per `situation_key`
        # (dist, floor, run) the strike's (`skeys`). Per (situation key, the move floor where it can
        # hold a caught defender, `CaughtModel.held_run`) an option (`cks`), naming its roll form
        # (`self.ROLL`) and its strike (`self.SK`): `net_parts`' (`situation_key`, `roll_key`).
        keys, skeys, cks, opts = {}, {}, {}, []
        for row in _situation_keys(c, sprint):
            ids = []
            for k, run in row:
                if k not in skeys:
                    skeys[k] = len(skeys)
                    keys.setdefault(k[:2], len(keys))
                ck = (k, self.caught.held_run(k[0], run) if self.caught is not None else None)
                ids.append(cks.setdefault(ck, len(cks)))
            opts.append(ids)
        self.CK = list(cks)
        self.ROLL = np.array([keys[k[:2]] for k, _ in self.CK], int)
        self.SK = np.array([skeys[k] for k, _ in self.CK], int)
        K, Fm = len(keys), len(families)
        # Per roll key, the geometry that first catches a defender who never dodges (-1: none).
        self.BH = np.full(K, -1, int)
        self.COEF = np.zeros((K, Fm, G))
        self.PE = np.zeros((K, Fm))
        self.PP = np.zeros((K, Fm))
        cat = {}
        plays, mid_rows, self.moves_by_anim = {}, [], {}
        gbursts = [gs for gs, _ in bursts]
        for (dist, floor), ki in keys.items():
            plan = look.plan(dist) if look is not None else None
            # The last burst that reaches him standing decides his punish, so he aims it for that too
            # (`er-mechanics-ashes.last_reaching`, version 25): with worths above 0 (`net` checks
            # them), a burst reaches him when it touches him standing.
            last = None
            for bi, (gs, _) in enumerate(bursts):
                memo = cat.setdefault((0, bi), {})
                if ('base', dist) not in memo:
                    memo[('base', dist)] = A._catches(gs, dist, None, [0.0])
                if any(g is not None for g in gs) and int(memo[('base', dist)][0][0]) >= 0:
                    last = bi
            for fi, moves in enumerate(families):
                # The bursts chained as `sequence_outcome` chains them (`er-mechanics-ashes.dodge_chain`,
                # version 26: each roll carries him into the next burst, which he can dodge again only
                # from its start plus its dodge cancel); the plan times the first burst that reaches
                # him standing. A burst touches him when it reaches him, which with worths above 0
                # (`net` checks them) is when `sequence_outcome` counts it.
                ch = A.dodge_chain(gbursts, dist, moves, floor, 0.0, roll, c.strikes, True, advance,
                                   A._ChainMemo(cat, fi), plan, last)
                if ch['first'] is not None:
                    self.BH[ki] = int(offs[ch['first']]) + ch['first_h']
                for bi, hh, play, w in ch['events']:
                    g = int(offs[bi]) + hh
                    if play is None or self.caught is None:
                        self.COEF[ki, fi, g] += w
                        continue
                    # A catch in a dodge of his (version 25), or in the roll he is still on (version
                    # 26): one play per (distance, geometry, move, start, catch frame), priced per
                    # build in `net`.
                    mv, s0, tc = play
                    pk = (dist, g, mv['anim'], s0, tc)
                    if pk not in plays:
                        plays[pk] = len(plays)
                        self.moves_by_anim[mv['anim']] = mv
                    mid_rows.append((ki, fi, plays[pk], w))
                self.PE[ki, fi], self.PP[ki, fi] = ch['p_evade'], ch['p_punish']
        # The catches in his own dodge: per play (distance, geometry, move anim, start, catch
        # frame), and per weight (roll key, family, play, weight).
        self.plays = list(plays)
        mr = np.array(mid_rows, float).reshape(len(mid_rows), 4)
        self.MK, self.MF, self.MP = (mr[:, j].astype(int) for j in range(3))
        self.MW = mr[:, 3]
        width = max(len(r) for r in opts) if opts else 1
        self.OPT = np.array([r + [len(cks)] * (width - len(r)) for r in opts], int).reshape(len(opts), width)
        self.punish_hp = A.PUNISH_HP
        self.dists = [d for (d, _), _ in sorted(keys.items(), key=lambda e: e[1])]
        # Per roll key, the catches the outcome reads: a geometry with a dodge weight, or a base catch.
        self.need = [sorted(set(np.nonzero((self.COEF[ki] > 0).any(axis=0))[0].tolist())
                            | ({int(self.BH[ki])} if self.BH[ki] >= 0 else set())) for ki in range(K)]
        # The defender's strike (`er-mechanics-spells.net_parts`): per key the runs of strikes that
        # land (`strike_segments`), which read no worth and no poise; `net` prices them per build.
        self.strike = _StrikeForm(c, geo, roll, list(skeys))

    def net(self, value, geo=None, poise=None):
        """Per situation: what an attack of worth `value` ({frame: damage}, as `net_parts` takes it)
        nets with this form's reach, float64. `geo` is that attack's own geometries (the same
        shapes), whose `CAUGHT` worth weights the caught defender's take is priced with; `poise`
        the attacker's menu poise the defender's strike reads (None: `net_parts`' default, the
        defender's own)."""
        R = self.ROLL
        if self.caught is None:
            # `er-mechanics-ashes.hp_cap`: a number, or each pool build's own health.
            hp = self.hp
            worth = np.array([(lambda x: x if hp is None else min(x, hp) if not isinstance(hp, (list, tuple))
                               else sum(w * min(x, c_) for c_, w in hp))(sum(value[f] for f in value if f >= fr))
                              for fr in self.frames], float)
            worth = np.broadcast_to(worth, (len(self.CK), len(self.frames)))
        else:
            # Per option, held by its floors and capped at each defender's health (`worth_of`): the
            # catches no outcome reads keep a placeholder worth of 1 (weight 0, never a base).
            worth = np.ones((len(self.CK), len(self.frames)))
            memo = {}                         # options that hold him alike share their catches
            for ci, ((d, f, _), run) in enumerate(self.CK):
                for g in self.need[R[ci]]:
                    held = self.caught.held(d, g, (f, f if run is None else run))
                    if (d, g, held) not in memo:
                        memo[(d, g, held)] = self.caught.worth_of(d, g, value, geo, held, hp=self.hp)
                    worth[ci, g] = memo[(d, g, held)]
        if not (worth > 0).all():
            # The dodge floors chained in `__init__` assume every touching burst has a base above 0.
            raise ValueError(f'OutcomeForm: a worth at or under 0 ({worth.min()}) breaks the burst chain')
        BH = self.BH[R]
        # What lands per family: the catches weighed per geometry (their weights already carry the
        # chance he got that far, `dodge_chain`), plus the catches in a dodge of his.
        val = np.einsum('kfg,kg->kf', self.COEF[R], worth)
        if len(self.MW):
            # The catches in his own dodge, each played from that dodge (its cancels hold him, not the
            # situation's floors) and capped at his health as the rest (version 25; version 26 the
            # roll he is still on from an earlier burst too).
            pw = np.array([self.caught.worth_of(d, g, value, geo, hp=self.hp, dodge=(self.moves_by_anim[a], s0, tc))
                           for d, g, a, s0, tc in self.plays], float)
            MV = np.zeros(self.COEF.shape[:2])
            np.add.at(MV, (self.MK, self.MF), self.MW * pw[self.MP])
            val = val + MV[R]
        pun = self.PP[R]
        # Or he stands, where that leaves the attacker less than every move family
        # (`er-mechanics-ashes.stand_or`, as `sequence_outcome`): the first catch a defender who
        # never dodges takes, the same in every family (0 where nothing reaches him). Rows are
        # already per strike key (`R`), so no `ROLL` gather follows.
        stand = np.where(BH >= 0, worth[np.arange(worth.shape[0]), np.maximum(BH, 0)], 0.0)
        netk = np.minimum((val - pun * self.punish_hp).min(axis=1), stand)
        # He strikes where that leaves the attacker less than every move family (a tie keeps them).
        netk = np.append(np.minimum(netk, self.strike.nets(value, geo, poise, self.caught)[self.SK]), -np.inf)
        return netk[self.OPT].max(axis=1)


class _StrikeForm:
    """The defender's strike for a form (`OutcomeForm`, `FollowupForm`) on one geometry list and roll
    frame, as `er-mechanics-spells.net_parts` reads it: per key (dist, floor, run,
    `er-mechanics-spells.situation_key`) the runs of strikes
    that land (`strike_segments`, read here once), then per build what they leave the attacker
    with its worth and its poise (`nets`, `outcome`). Off, every net is inf, when the run's strikes
    carry no poise damage (`er-mechanics-spells.Strikes`) or no poise is known."""

    def __init__(self, c, geo, roll, keys):
        self.A, self.strikes, self.hp = c.A, c.strikes, c.dfn.get('hp_pool') or c.dfn.get('hp')
        self.poise = c.dfn.get('poise')
        self.on = S.strike_table(c.strikes) is not None and self.poise is not None
        self.keys = list(keys)
        if not self.on:
            return
        self.reach = S.StrikeReach(c.A, geo)
        # The attacker's own guard over the attack (`er-mechanics-spells.strike_guard`): its HP per
        # strike, read once (it reads no worth and no poise); None, no guard is read. Its stamina
        # regenerates at 45/s: no line carries a talisman (`line_kit`), so `regen_add` is 0.
        self.saved = S.strike_saved(c.A, c.strikes, self.reach.guard)
        gwin = self.reach.guard_windows if self.saved is not None else ()
        self.segs = [S.strike_segments(c.A, c.strikes, d, f, self.reach.touch(d), roll, self.reach.cuts,
                                       self.reach.windows, gwin, run=r, advance=self.reach.advance(d))
                     for d, f, r in self.keys]
        # Per attacker poise as `strike_breaks` keys it, each key's shares of strikes that land and
        # break him (`er-mechanics-spells.strike_shares`), and those shares as `nets`' arrays: they
        # read no worth, so the value, the one-hit value and every line of that poise share them.
        self.shares, self.arrays = {}, {}

    def _kept(self, value, geo, caught):
        """kept(d, cut): what lands on him standing at distance `d` from the attack stopped before
        cut `cut` (None: none stopped), for worth `value` on geometries `geo` (their `CAUGHT`
        weights, priced by `caught` as the form prices a catch). Read once per distance and cut, at
        the place the touch was timed (`er-mechanics-spells.StrikeReach.where`), as `net_parts`."""
        got = {}

        def kept(d, cut):
            if (d, cut) not in got:
                at_ = self.reach.where(d)
                if caught is not None:
                    def price(h, vk, cap):
                        return caught.worth_of(at_, h, vk, geo, hp=cap)
                else:
                    def price(h, vk, cap):
                        return self.A.hp_cap(self.reach.raw_price(h, vk), cap)
                got[(d, cut)] = self.reach.kept(at_, cut, value, price, self.hp)
            return got[(d, cut)]
        return kept

    def _shares(self, poise):
        """(`strike_breaks` at attacker poise `poise`, None: the defender's own; per key
        `er-mechanics-spells.strike_shares` through it), kept per poise."""
        poise = self.poise if poise is None else poise
        beta = S.strike_breaks(self.strikes, self.reach.windows, poise,
                               guard=self.reach.guard if self.saved is not None else None, A=self.A)
        pk = round(float(poise), 4)
        if pk not in self.shares:
            cum = S.strike_cum(beta)
            self.shares[pk] = [S.strike_shares(self.strikes, sg, beta, len(self.reach.cuts), cum, self.saved)
                               for sg in self.segs]
        return beta, self.shares[pk]

    def _arrays(self, poise):
        """`_shares` at `poise` as `nets` reads them: (per key the punish, the share that lands times
        `PUNISH_HP` less what the attacker's guard spares him; per key 1 less the sum of the shares
        that break him; [key, cut] the share that breaks him there), each number made as
        `strike_outcome` makes it. Kept per poise."""
        shares = self._shares(poise)[1]
        pk = round(float(self.poise if poise is None else poise), 4)
        if pk not in self.arrays:
            K, C = len(self.keys), len(self.reach.cuts) + 1
            pun, rest, brk = np.empty(K), np.empty(K), np.zeros((K, C))
            for i, (land, broken, spared) in enumerate(shares):
                pun[i] = land * self.A.PUNISH_HP - spared
                rest[i] = 1.0 - sum(broken)
                brk[i] = broken
            self.arrays[pk] = (pun, rest, brk)
        return self.arrays[pk]

    def outcomes(self, value, geo, poise, caught):
        """Per key, `er-mechanics-spells.strike_outcome` for worth `value` ({frame: damage}) on
        geometries `geo` (their `CAUGHT` weights, priced by `caught` as the form prices a catch)
        and attacker poise `poise` (None: the defender's own, `net_parts`' default); [] when off.
        What lands is read once per distance and cut."""
        if not self.on:
            return []
        beta, shares = self._shares(poise)
        kept = self._kept(value, geo, caught)
        return [S.strike_outcome(self.A, self.strikes, self.segs[i], beta, lambda cut, d=d: kept(d, cut),
                                 kept(d, None), len(self.reach.cuts), shares[i]) for i, (d, *_) in enumerate(self.keys)]

    def nets(self, value, geo, poise, caught, against=None):
        """Per key, what the strike leaves the attacker (`outcomes`' value less its punish); inf
        where the strike is off, or, with `against` (per key), where it does not leave less. Made
        for every key at once, each number by the same operations in the same order as
        `er-mechanics-spells.strike_outcome` makes it, so the same numbers as `outcomes`."""
        out = np.full(len(self.keys), np.inf)
        if self.on:
            pun, rest, brk = self._arrays(poise)
            kept = self._kept(value, geo, caught)
            dists = [k[0] for k in self.keys]
            val = np.array([kept(d, None) for d in dists], float) * rest
            # Each cut a strike breaks him before adds what lands of the attack stopped there, in
            # the cuts' order.
            for ci in range(brk.shape[1]):
                col = brk[:, ci]
                hit = col > 0
                if hit.any():
                    k = np.zeros(len(dists))
                    for i in np.flatnonzero(hit).tolist():
                        k[i] = kept(dists[i], ci)
                    val = np.where(hit, val + col * k, val)
            out = val - pun
        if against is not None:
            out = np.where(out < against, out, np.inf)
        return out


def form_error(got, want):
    """The largest excess of |got - want| over `FORM_TOL`, 0 when every situation is within it."""
    got, want = np.asarray(got, float), np.asarray(want, float)
    return float(max(0.0, (np.abs(got - want) - (FORM_TOL[0] + FORM_TOL[1] * np.abs(want))).max(initial=0.0)))


def followup_delays(follow):
    """The press delays a follow-up's window offers (bd er-effects-rs-me5p): every real frame from
    its first frame up to, not including, its last (`skill_followups` window, `INFERRED` TAE end
    exclusive): Glintblade Phalanx's f45-62 gives 0 to 16."""
    span = float(follow['last']) - float(follow['first'])
    return [float(d) for d in np.arange(0.0, span - 1e-9, 1.0)] or [0.0]


#: The share of a strafing defender's escapes the scored mixup keeps (`FollowupForm.mixup`,
#: `er-mechanics-ashes.FollowupModel.mixup` `strafe_share`). `INFERRED`: no param or measurement
#: says how often desync and execution let a strafe through (his reaction and the two network legs
#: are in the flown strafe since version 23, as a dodge's), and the user is to give a
#: realistic share. At 0.0 the strafe row is the stand row, so the mixup moves only the defender's
#: roll or stand and the attacker's blind delays. Glintstone Pebble then R2 on Intelligence + melee
#: nets 413.1 / 342.4 / 320.4 at shares 0 / 0.5 / 1.0 (`MEASURED` 2026-11-16).
STRAFE_SHARE = 0.0


def profile_inputs(rec):
    """What phase 2 reads of entity or component `rec`: its geometry, worth, roll and free-aimed
    alternatives (`aims`), and for a follow-up its press frame and press delays (`FollowupForm`).
    A release scored as the mean of its release frames (a spell's, or a charged skill's 'mixture',
    `release_members`) carries its own 'weight' and its later releases as 'members' ([{'key',
    'weight', 'geo', 'value', 'value_one', 'roll', 'aims'}]), each read as a lookalike of its own
    (`_profile_work`); a 'followup' release keeps its press and delays. A spell's 'dash' variant
    (`er-mechanics-spells.DASH`) carries 'sprint': it meets the situations of a cast pressed while
    sprinting (`pairs_of`)."""
    out = {**{k: rec[k] for k in ('geo', 'value', 'value_one', 'roll')}, 'aims': rec.get('aims') or []}
    if rec.get('type') == 'spell' and rec.get('variant') == 'dash':
        out['sprint'] = True
    if rec.get('hp_cost'):
        # What one cast takes from its user; the caller adds the line's 'max_hp' (`hp_charge`).
        out['hp_cost'] = rec['hp_cost']
    if rec.get('press') is not None and rec.get('release_rule') != 'mixture':
        out.update(press=rec['press'], delays=followup_delays(rec['follow']))
    elif rec.get('release_members'):
        out.update(weight=rec['release_weight'], members=[
            {**{k: m[k] for k in ('key', 'weight', 'geo', 'value', 'value_one', 'roll', 'aims')},
             **({'sprint': True} if out.get('sprint') else {})}
            for m in rec['release_members']])
    return out


# ---------------------------------------------------------------------------------------------
# what is on screen: the attacks a reacting defender cannot tell apart (module doc, 'The outcome
# per situation'; `er-mechanics-spells.Lookalikes`, ashes-of-war.md section 16a)
#
# A group is read per line (the distance basis, each attacker build, each objective line), since
# what a line casts and how fast it casts it are its own: the spells of one (`Magic.refType`,
# `castSfxId`) among phase 0's spell candidates (usable in multiplayer, held by a player, cast by
# a catalyst of the run) that the line casts, every variant of each, parting where
# `SpellModel.look_split` says (`er-mechanics-spells.spell_lookalikes`' rule, the kit's); a charged
# skill's hold and its early release, parting at the release (`skill_lookalikes`' rule). A member's
# geometry is phase 1's at that line (`LookStore`); a spell no phase-1 item ran at a line that needs
# it (an `--only` run, an objective line) is run there for the group alone (`look_needs`). Phase 2
# reads the group (`look_specs`) as `net_parts`' and `OutcomeForm`'s `look`, and a component as
# its whole entity is read (its own escaping starts against the whole's lookalikes).

def stats_key(stats):
    """Damage stats ({stat: value}) as a tuple in `WEAPON_NEED` order: the line a phase-1 item runs
    at (`line_key`'s form), which keys `LookStore`."""
    return tuple(int(stats[k]) for k in WEAPON_NEED)


def look_key(SM, mid):
    """(`Magic.refType`, `castSfxId`) of spell `mid`: equal for two spells that play one TimeAct's
    clips with one casting SFX (`INFERRED` that they look alike until a shot that differs spawns,
    `er-mechanics-spells.spell_lookalikes`)."""
    m = SM.magic[mid]
    return m['refType'], m['castSfxId']


def look_pool(c, spell_ids):
    """{`look_key`: [spell id]} of phase 0's spell candidates `spell_ids` (taken before `--only`
    narrows them: a spell left out of the run is still on screen)."""
    out = {}
    for m in spell_ids:
        out.setdefault(look_key(c.SM, m), []).append(m)
    return out


def look_mids(c, mid, stats, pool):
    """The spells a defender cannot tell spell `mid` from at a line of damage stats `stats`, `mid`
    first: those of `pool` (`look_pool`) of its `look_key` that the line casts with the run's
    catalysts (`Context.cast_short`: the spell's and the catalyst's requirements, as phase 1 reads a
    build's)."""
    return [mid] + [m for m in pool.get(look_key(c.SM, mid), ()) if m != mid and not c.cast_short(m, stats)]


def look_entry(rec):
    """What a group reads of phase-1 entity `rec` at the line it was run at, or None: a scored or
    partial spell variant's geometry, spell id, release clip, hand-off frame and release lead
    (`SpellModel.look_split`); a charged skill's hold's geometry; its early release's geometry with
    the hold it releases, its press, the release frame (`skill_entity`), and its rule. 'weight' is
    its share of its variant (`release_members`: the earliest release's, 1 for any other)."""
    if rec.get('status') not in ('scored', 'partial') or not rec.get('geo'):
        return None
    w = float(rec.get('release_weight', 1.0))
    # 'roll': the attacker's roll frame after it, from which a member's punish is read when the
    # defender aims his dodge for it (`er-mechanics-spells.Lookalikes` `rolls`, version 25).
    if rec['type'] == 'spell':
        return {'geo': rec['geo'], 'mid': rec['id'], 'anim': rec['anim'], 'hand': rec['hand'], 'lead': rec.get('lead'),
                'weight': w, 'roll': rec.get('roll')}
    if rec.get('release') == 'full':
        return {'geo': rec['geo'], 'hold': True, 'weight': w, 'roll': rec.get('roll')}
    if rec.get('release') == 'tap' and rec.get('press') is not None:
        return {'geo': rec['geo'], 'of': rec['variant_of'], 'press': float(rec['press']), 'weight': w,
                'shows': float(rec.get('release_show', 0)), 'rule': rec.get('release_rule'), 'roll': rec.get('roll')}
    return None


class LookStore:
    """Phase 1's reads for the groups, per line (`stats_key`): `entries` {(line, entity key):
    `look_entry`}, `ran` {(line, spell id)} for every spell job run there (whatever it scored),
    `spells` {(line, spell id): its variants' keys} and `taps` {(line, hold key): its early
    releases' keys}. A release's later release frames (`release_members`) are entries of their own,
    keyed by `member_key`, in its spell's or its hold's set. A line reached twice (two objective
    lines of equal damage stats) reads the same records again."""

    def __init__(self):
        self.entries, self.ran, self.spells, self.taps = {}, set(), {}, {}
        # {(line, follow-up key): its opening's key}: a follow-up (R1 / R2 after the skill, not a
        # release) is its opening until the press, so it is read in its opening's group
        # (`look_specs`).
        self.opening = {}

    def add(self, line, recs):
        for rec in recs:
            if rec.get('type') == 'spell':
                self.ran.add((line, rec['id']))
            if rec.get('follow') and not rec.get('release') and rec.get('variant_of'):
                self.opening[(line, rec['key'])] = rec['variant_of']
            e = look_entry(rec)
            if e is None:
                continue
            self.entries[(line, rec['key'])] = e
            group = self.spells.setdefault((line, e['mid']), set()) if 'mid' in e else \
                self.taps.setdefault((line, e['of']), set()) if 'of' in e else None
            if group is not None:
                group.add(rec['key'])
            for m in rec.get('release_members') or ():
                me = {'geo': m['geo'], 'weight': float(m['weight']), 'roll': m.get('roll')}
                if 'mid' in e:
                    me.update({k: m[k] for k in ('mid', 'anim', 'hand', 'lead')})
                elif 'of' in e:
                    me.update(of=e['of'], press=m['press'], shows=float(m.get('shows', 0)), rule=e['rule'])
                else:
                    continue
                self.entries[(line, m['key'])] = me
                group.add(m['key'])


def look_needs(c, store, wants, pool):
    """[(spell id, line)] no phase-1 item ran that a group of `wants` ([(entity key, who, line,
    whole key or None)], `look_specs`') holds: what `compute` runs for the groups alone."""
    out = set()
    for k, _, line, of in wants:
        e = store.entries.get((line, of or k))
        if e is None or 'mid' not in e:
            continue
        out |= {(m, line) for m in look_mids(c, e['mid'], dict(zip(WEAPON_NEED, line)), pool)
                if (line, m) not in store.ran}
    return sorted(out)


def look_spec(c, store, pool, line, key, memo=None):
    """The group a defender cannot tell entity `key` from at `line`, as phase 2 reads it: {'line',
    'keys' (the members, sorted, `key` among them), 'me' (`key`), 'splits' {(a, b): real frame from
    the press at which he tells a from b}, 'weights' {member: weight}}, or None when `key` is alone.
    A spell's members are the variants at `line` of `look_mids`, and each release's later release
    frames (`release_members`), each pair split by `SpellModel.look_split` (`spell_lookalikes`); a
    charged skill's hold's are its early release at each release frame, each split from it at its
    press and two of them at the earlier (`skill_lookalikes`), and a release read as the mean of its
    release frames ('mixture') is read in its hold's group. The variants weigh the same (`INFERRED`,
    as the kit), a release's weight split over its release frames, and in a group with a release
    spread over its window every release is one he reacts to as it comes ('events', the kit's
    `release_events`). `memo` keeps `look_split` per pair of casts."""
    memo = {} if memo is None else memo
    e = store.entries.get((line, key))
    if e is None:
        return None
    if 'mid' in e:
        keys = sorted(k for m in look_mids(c, e['mid'], dict(zip(WEAPON_NEED, line)), pool)
                      for k in store.spells.get((line, m), ()))
        casts = {k: {x: store.entries[(line, k)].get(x) for x in ('mid', 'anim', 'hand', 'lead')} for k in keys}
        splits = {}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                pair = (tuple(casts[a].values()), tuple(casts[b].values()))
                if pair not in memo:
                    memo[pair] = c.SM.look_split(casts[a], casts[b])
                splits[(a, b)] = memo[pair]
        # A cast he tells from every other one at the press (a quick cast, `er-mechanics-spells.
        # known_at_once`) is alone, and the others are a group without it.
        alone = S.known_at_once(keys, splits)
        if key in alone:
            return None
        keys = [k for k in keys if k not in alone]
        splits = {p: s for p, s in splits.items() if not set(p) & alone}
        events = S.release_events(c.SM, {k: store.entries[(line, k)] for k in keys})
    elif e.get('hold') or ('of' in e and e.get('rule') == 'mixture'):
        hold = key if e.get('hold') else e['of']
        if (line, hold) not in store.entries:
            return None
        taps = sorted(store.taps.get((line, hold), ()))
        keys = sorted([hold] + taps)
        # Where each release shows: its press plus the frames its pose takes to leave the hold's
        # (`er-mechanics-ashes.skill_release_visible`).
        seen = {tk: store.entries[(line, tk)]['press'] + store.entries[(line, tk)].get('shows', 0.0) for tk in taps}
        splits = {(hold, tk): seen[tk] for tk in taps}
        splits.update({(a, b): min(seen[a], seen[b]) for i, a in enumerate(taps) for b in taps[i + 1:]})
        events = dict(seen) if len(taps) > 1 else None
    else:
        return None
    weights = {k: float(store.entries[(line, k)].get('weight', 1.0)) for k in keys}
    return {'line': line, 'keys': keys, 'me': key, 'splits': splits, 'weights': weights,
            'events': events} if len(keys) > 1 else None


def look_specs(c, store, wants, pool):
    """{(entity key, who): `look_spec`} for every (key, who, line, whole) of `wants` whose key is not
    alone at its line; a component (`whole` set) takes its whole entity's group, read as the whole
    (`look_spec` 'me'). `who` is None for the distance basis, an attacker build's index, or an
    objective line's name (`_profile_work`). A follow-up takes its opening's group, read as the
    opening ('me'): until the press it is the opening, and the group times only the first dodge,
    of the first burst that reaches him standing (`er-mechanics-ashes.FollowupModel` `look`). Until
    version 22 a follow-up was read alone: a defender who could not tell a charged opening's hold
    from its early release knew it once a follow-up came after it."""
    out, memo = {}, {}
    for k, who, line, of in wants:
        sp = look_spec(c, store, pool, line, of or k, memo)
        if sp is None and (line, of or k) in store.opening:
            sp = look_spec(c, store, pool, line, store.opening[(line, of or k)], memo)
        if sp is not None:
            out[(k, who)] = sp
    return out


def look_sig(spec, store, me=None):
    """`geo_fingerprint`'s `look`: the first 16 hex digits of a sha256 over what
    `er-mechanics-ashes.lookalike_plan` reads of a group besides the attack's own escaping starts
    (which its own geometry fingerprints): which member the attack is read as (`me`, else
    `spec['me']`: a release member is read as itself), every other member's geometry
    (`geo_fingerprint` of it), the splits, and the weights when one is not 1."""
    me = spec['me'] if me is None else me
    h = hashlib.sha256()
    idx = {k: i for i, k in enumerate(spec['keys'])}
    for k in spec['keys']:
        h.update(repr('me' if k == me else geo_fingerprint(store.entries[(spec['line'], k)]['geo'], 0.0)).encode())
    h.update(repr(sorted((idx[a], idx[b], float(f)) for (a, b), f in spec['splits'].items())).encode())
    ws = [float((spec.get('weights') or {}).get(k, 1.0)) for k in spec['keys']]
    if any(w != 1.0 for w in ws):
        h.update(repr(ws).encode())
    if spec.get('events'):
        h.update(repr(sorted((idx[k], round(float(f), 6)) for k, f in spec['events'].items() if k in idx)).encode())
    # The members' roll frames, from which each one's punish is read (`look_group` `rolls`, version 25).
    rolls = [store.entries[(spec['line'], k)].get('roll') for k in spec['keys']]
    if any(r is not None for r in rolls):
        h.update(repr(['me' if k == me else (None if r is None else round(float(r), 6))
                       for k, r in zip(spec['keys'], rolls)]).encode())
    return h.hexdigest()[:16]


def look_group(c, spec, store):
    """The `er-mechanics-spells.Lookalikes` of `spec` (`look_spec`): its members' geometries at its
    line, its splits, its weights (a release's split over its release frames, every other member
    1) and its release events (a release spread over its window, `S.release_events`)."""
    return S.Lookalikes(c.A, {k: store.entries[(spec['line'], k)]['geo'] for k in spec['keys']}, spec['splits'],
                        spec.get('weights'), spec.get('events'),
                        {k: store.entries[(spec['line'], k)].get('roll') for k in spec['keys']})


class FollowupForm:
    """`net_parts` for a follow-up entity (`x['press']` set) under the press the attacker picks
    (`er-mechanics-ashes.FollowupModel`, bd er-effects-rs-me5p): per situation, the opening is
    dodged as `sequence_outcome` dodges it, the follow-up meets the defender's own roll, and the
    attacker presses later in the window only on a roll he saw before it opened. The run-in
    choice, the family choice and the worth of a caught defender (`CaughtModel`, capped at his
    health) are `net_parts`' own. One form serves every build on the same shapes (`geo_fingerprint`
    with the press); `parts` and `net` take that build's worth and geometries (their `CAUGHT`
    weights). Scored, the net is the mixup's value (`mixup`, version 20): the defender's roll or
    stand against the attacker's press policy at each default delay, at `STRAFE_SHARE`; `mix`
    False scores the fixed sequence (the defender rolls the opening, the attacker presses by the
    policy) instead. `see` False keeps every press at once: the earliest press under the same
    rules, the fixed sequence's; `read` lets the attacker also pick the delay he presses at unseen
    (a diagnostic, never scored: `FollowupModel.outcome`)."""

    def __init__(self, c, x, see=True, read=False, mix=True, look=None):
        A = c.A
        self.A, self.see, self.read, self.mix = A, see, read, mix
        geo = x['geo']
        self.press = float(x['press'])
        self.delays = [float(d) for d in x['delays']]
        order = sorted(range(len(geo)), key=lambda i: geo[i][0]['T'].min())
        rank = {i: j for j, i in enumerate(order)}
        self.follows = [geo[i][1] >= self.press - 1e-9 for i in order]
        opening = [(geo[i][0], rank[i]) for i in order if not self.follows[rank[i]]]
        follow = [(geo[i][0], rank[i]) for i in order if self.follows[rank[i]]]
        # `look`: the opening's lookalike group (`look_specs`), which times his first dodge of the
        # opening (`er-mechanics-ashes.FollowupModel` `look`).
        self.model = A.FollowupModel(opening, follow, self.press, self.delays, x['roll'], c.strikes, look=look)
        # The poises and health a caught defender holds with (`net_parts`' `cpoise` and `hp`,
        # version 21).
        self.poise = c.dfn.get('pool') or c.dfn.get('poise_pool') or c.dfn.get('poise')
        self.hp = c.dfn.get('hp_pool') or c.dfn.get('hp')
        # Whose resistances a status build-up is read against (`er-mechanics-spells.status_caught`,
        # version 30).
        self.dfn = c.dfn
        # Per (`er-mechanics-spells.situation_key` (dist, floor, run), the move floor rounded to half
        # a frame): the key drops the move floor up close, which a caught defender's run out and the
        # strafe row read (version 21). The strike reads the situation key alone (`skeys`, `SK`).
        self.keys, self.opts, skeys = {}, [], {}
        for row in _situation_keys(c):
            self.opts.append([self.keys.setdefault((k, round(run * 2) / 2), len(self.keys)) for k, run in row])
        for k, _ in self.keys:
            skeys.setdefault(k, len(skeys))
        self.SK = [skeys[k] for k, _ in self.keys]
        self.gaps = [[None] + [g for g in S.APPROACH_GAPS_M if g < sit[0]] for sit in c.pairs]
        self.now_dist = [round(sit[0], 2) for sit in c.pairs]
        self.dists = sorted({k[0] for k, _ in self.keys})
        # The first touch of a defender who does not dodge, at the earliest press (`net_parts`).
        gs = [g for g, _ in geo]
        self.t_first = {d: float(A._catches(gs, d, None, [0.0])[1][0]) for d in self.dists}
        self._models = {}
        # The defender's strike, as `net_parts` reads it: on the attack pressed at once (he does not
        # roll, so the attacker sees no roll to delay for, `FollowupModel`), priced on this shape's
        # caught defender.
        self.strike = _StrikeForm(c, geo, x['roll'], list(skeys))
        self._strike_cm = S.status_caught(geo, self.poise, None, self.dfn) if self.strike.on else None

    def _with_strike(self, o, value, geo, poise):
        """`o` (per key) with the defender's strike taken wherever it leaves the attacker less, as
        `net_parts` takes it (`_StrikeForm`); the press then comes at once."""
        so = self.strike.outcomes(value, geo, poise, self._strike_cm)
        if so:
            for i in range(len(o)):
                s = so[self.SK[i]]
                rn = o[i]['value'] - o[i]['punish_hp']
                # A tie keeps the roll, with `net_parts`' hair of room.
                if s['value'] - s['punish_hp'] < rn - 1e-9 * max(1.0, abs(rn)):
                    o[i] = {**s, 'delayed': 0.0}
        return o

    def _shifted(self, value, geo, d):
        """`value` and `geo` with the follow-up pressed `delays[d]` late."""
        dl = self.delays[d]
        if dl == 0.0:
            return value, geo
        cut = self.press - 1e-9
        return ({(f + dl if f >= cut else f): v for f, v in value.items()},
                [((self.A.shift_geometry(g, dl), fr + dl) if fr >= cut else (g, fr)) for g, fr in geo])

    def _worths(self, value, geo):
        """(dist, d, indices) -> per index of the attack (`FollowupModel`), what lands when it
        catches first with the press `delays[d]` late: `CaughtModel` on that build's worth and
        weights, or the old rule (that frame's worth and every later one's), capped at his health.
        `floors` (his dodge floor, his move floor; the attack's clock, which a late press does not
        move) hold a caught defender in his whiff's recovery (`CaughtModel.held`, version 21). Each
        (dist, d, index, the floors that hold him) is priced once, when first asked for, and kept
        until `release(dist)`: `_outcomes` and `mixup` walk the distances once each and release each
        one when its keys are done (2026-11-18). Until then a pass kept every distance's escape
        tables to its end: Waves of Darkness then R2 f68's second mixup pass read 41,262 of them,
        23 KB each, in its last 4,781 of 5,514 situation keys (`MEASURED`, `target/wf/waves/a1`)."""
        A = self.A
        shifted, got = {}, {}

        def model(d):
            if d not in shifted:
                shifted[d] = self._shifted(value, geo, d)
                if d not in self._models:
                    self._models[d] = S.status_caught(shifted[d][1], self.poise, None, self.dfn)
            return self._models[d], shifted[d]

        def escapes(dist, h, floors=None):
            # `FollowupModel.mixup`'s `stand_escapes` for the catch by geometry `h` (version 25):
            # per delay, `CaughtModel.escape_table` on that build's worth, held and capped as `at`
            # prices it. None without a played caught defender (the old rule).
            if model(0)[0] is None:
                return None

            def f(d):
                cm, (v, g) = model(d)
                return cm.escape_table(dist, h, v, g, floors, self.hp)
            return f

        def at(dist, d, idx, floors=None):
            cm, (v, g) = model(d)
            out = np.empty(len(idx))
            for j, h in enumerate(np.asarray(idx).tolist()):
                held = cm.held(dist, h, floors) if cm is not None else None
                key = (dist, d, h, held)
                if key not in got:
                    if cm is not None:
                        got[key] = float(cm.worth_of(dist, h, v, g, held, hp=self.hp))
                    else:
                        fr = sorted(fr for _, fr in g)[h]
                        got[key] = A.hp_cap(float(sum(v[f] for f in v if f >= fr)), self.hp)
                out[j] = got[key]
            return out

        def mid(dist, d, h, move, start, catch):
            # `FollowupModel`'s `mid_worth` (version 25): a catch in his own dodge played from it on
            # that delay's clock (`CaughtModel.worth_of` `dodge`), capped as `at` caps; the old rule
            # without a played caught defender.
            cm, (v, g) = model(d)
            if cm is None:
                return float(at(dist, d, [h])[0])
            key = ('mid', dist, d, h, move['anim'], float(start), float(catch))
            if key not in got:
                got[key] = float(cm.worth_of(dist, h, v, g, hp=self.hp, dodge=(move, start, catch)))
            return got[key]

        def col_escapes(dist, col, floors=None):
            # `FollowupModel.outcome`'s `catch_escapes` (version 26): for a catch by the opening, a
            # whole-attack index held by the situation's floors or an 'open' play in his dodge,
            # `CaughtModel.escape_table` per delay on that build's worth, capped as `at` caps (kept
            # per delay); None for another column or without a played caught defender.
            if model(0)[0] is None:
                return None
            if col < self.model.size:
                if self.follows[col]:
                    return None
                h, dodge = col, None
            else:
                kind, h, move, s, tc, _ = self.model.plays[col - self.model.size]
                if kind != 'open':
                    return None
                dodge = (move, s, tc)

            def f(d):
                key = ('esc', dist, col, None if dodge is not None else floors, d)
                if key not in got:
                    cm, (v, g) = model(d)
                    got[key] = cm.escape_table(dist, h, v, g, None if dodge is not None else floors, self.hp,
                                               dodge=dodge)
                return got[key]
            return f

        def release(dist):
            # Every key names its distance: first, or after a kind tag ('mid', 'esc').
            for k in [k for k in got if (k[1] if isinstance(k[0], str) else k[0]) == dist]:
                del got[k]
        at.escapes, at.mid, at.col_escapes, at.release = escapes, mid, col_escapes, release
        return at

    def _outcomes(self, value, geo, see):
        """`FollowupModel.outcome` per key, walked one distance at a time so the touch tables of a
        distance are dropped once its keys are done."""
        at = self._worths(value, geo)
        out = [None] * len(self.keys)
        by_dist = {}
        for ((dist, floor, _), run), i in self.keys.items():
            by_dist.setdefault(dist, {}).setdefault((floor, run), []).append(i)
        for dist, rows in sorted(by_dist.items()):
            for (floor, run), idx in rows.items():
                o = self.model.outcome(dist, floor, lambda d, ix, dist=dist, fl=(floor, run): at(dist, d, ix, fl),
                                       see, self.read,
                                       mid_worth=lambda d, h, mv, s, tc, dist=dist: at.mid(dist, d, h, mv, s, tc),
                                       catch_escapes=lambda col, dist=dist, fl=(floor, run): at.col_escapes(dist, col, fl))
                for i in idx:
                    out[i] = o
            self.model.release(dist)
            at.release(dist)
        return out

    def _mixed(self, see, mix):
        """Whether `parts` and `net` score the mixup: `mix` (default the form's), and never with
        `see` False (the press at once is the fixed sequence's)."""
        return (self.mix if mix is None else mix) and see

    def parts(self, value, value_one, geo, see=None, poise=None, mix=None):
        """`net_parts`' list for this build: per situation `net`, the chosen option's `value`,
        `base`, `p_evade`, `p_punish`, `punish_hp`, `gap` and `delayed`, `now` (the run-in left
        out, at the situation's own distance and floor, with `t_first`) and `net_one`. `poise` is
        the attacker's, which the defender's strike reads (`OutcomeForm.net`'s). Scored as the
        mixup (`_mixed`), `net`, `net_one` and `gap` are the mixup's (`mixup`: its value and the
        run-in it picks), `fixed` is the fixed sequence's net, and `value`, `base`, `p_evade`,
        `p_punish`, `punish_hp`, `delayed` and `now` stay the fixed sequence's at its own best
        run-in: what lands when the defender rolls, the descriptive arrays."""
        see = self.see if see is None else see
        o = self._with_strike(self._outcomes(value, geo, see), value, geo, poise)
        o1 = self._with_strike(self._outcomes(value_one, geo, see), value_one, geo, poise) \
            if value_one is not None else None
        mixed = self._mixed(see, mix)
        m = self.mixup(value, geo, poise=poise) if mixed else None
        m1 = self.mixup(value_one, geo, poise=poise) if mixed and value_one is not None else None
        out = []
        for i, opts in enumerate(self.opts):
            nets = [o[k]['value'] - o[k]['punish_hp'] for k in opts]
            j = int(np.argmax(nets))
            best, now = o[opts[j]], o[opts[0]]
            net_one = max(o1[k]['value'] - o1[k]['punish_hp'] for k in opts) if o1 is not None else None
            net, gap = nets[j], self.gaps[i][j]
            if m is not None:
                net, gap = m[i]['net'], self.gaps[i][m[i]['opt']]
                net_one = m1[i]['net'] if m1 is not None else None
            out.append({'net': net, 'fixed': nets[j], 'value': best['value'], 'base': best['base'],
                        'p_evade': best['p_evade'], 'p_punish': best['p_punish'], 'punish_hp': best['punish_hp'],
                        'gap': gap, 'delayed': best['delayed'],
                        'now': {'net': nets[0], 'value': now['value'], 'base': now['base'], 'p_evade': now['p_evade'],
                                'p_punish': now['p_punish'], 't_first': self.t_first[self.now_dist[i]]},
                        'net_one': net_one})
        return out

    def mixup(self, value, geo, open_roll=None, poise=None, blind=True, strafe_share=None):
        """Per situation, the attack as a two-sided choice (`er-mechanics-ashes.FollowupModel.mixup`):
        {'net': the game's value, the run-in the attacker picks taken as `parts` takes it, 'opt':
        that run-in's index in the situation's options (`gaps`), 'fixed': the fixed sequence's net
        (`parts` with `mix` False: the defender rolls the opening, the attacker presses by
        `outcome`'s policy), 'pure': the most the defender's best pure reply concedes in the game
        picked (min over his rows of the attacker's best column, under the strike as 'net' is), an
        upper bound of 'net', 'stand' / 'strafe': the defender's mix on those rows, 'none': the
        attacker's mix on no follow-up, 'late': on a default delay above 0}. `open_roll` is the
        attacker's first roll frame when he does not press (the opening entity's `roll`; None
        offers no 'none' column, as scored). `strafe_share` defaults to `STRAFE_SHARE`. The
        defender's strike (`_StrikeForm`) is a row the attacker's choice does not move (`INFERRED`:
        it lands before the press), so each key's value is the smaller of the game's and the
        strike's."""
        A = self.A
        strafe_share = STRAFE_SHARE if strafe_share is None else strafe_share
        at = self._worths(value, geo)
        order = sorted(range(len(geo)), key=lambda i: geo[i][0]['T'].min())
        opening = [i for i in order if geo[i][1] < self.press - 1e-9]
        to_open = {order.index(i): j for j, i in enumerate(opening)}
        ogeo = [geo[i] for i in opening]
        oval = {f: v for f, v in value.items() if f < self.press - 1e-9}
        ocm = S.status_caught(ogeo, self.poise, None, self.dfn)
        ofr = sorted(fr for _, fr in ogeo)
        omemo = {}

        def open_worth(dist, floors):
            def f(idx):
                out = []
                for h in np.asarray(idx).tolist():
                    j = to_open[h]
                    held = ocm.held(dist, j, floors) if ocm is not None else None
                    k = (dist, h, held)
                    if k not in omemo:
                        omemo[k] = float(ocm.worth_of(dist, j, oval, ogeo, held, hp=self.hp)) if ocm is not None \
                            else A.hp_cap(float(sum(oval[q] for q in oval if q >= ofr[j])), self.hp)
                    out.append(omemo[k])
                return np.array(out)
            return f

        def open_mid(dist):
            # A catch in his roll of the opening priced by the opening alone, from that roll
            # (`FollowupModel.mixup` `open_mid`, version 25).
            def f(h, move, start, catch):
                k = ('mid', dist, h, move['anim'], float(start), float(catch))
                if k not in omemo:
                    omemo[k] = float(ocm.worth_of(dist, to_open[h], oval, ogeo, hp=self.hp, dodge=(move, start, catch))) \
                        if ocm is not None else float(open_worth(dist, None)([h])[0])
                return omemo[k]
            return f
        games = [None] * len(self.keys)
        by_dist = {}
        for ((dist, floor, _), run), i in self.keys.items():
            by_dist.setdefault(dist, []).append((floor, run, i))
        for dist, rows in sorted(by_dist.items()):
            for floor, run, i in rows:
                # The strafe reads his move cancel itself: until version 21 it read the situation
                # key's, which is the dodge floor up close (`situation_key`).
                # The stand row's caught defender, whose escape the attacker sees as he sees a roll
                # (`stand_escapes`, version 25).
                stand = self.model._shape(dist, floor, 0)['base']
                games[i] = self.model.mixup(dist, floor,
                                            lambda d, ix, dist=dist, fl=(floor, run): at(dist, d, ix, fl),
                                            open_worth(dist, (floor, run)) if open_roll is not None else None,
                                            open_roll, run, strafe_share, blind,
                                            stand_escapes=at.escapes(dist, stand, (floor, run))
                                            if stand is not None else None,
                                            mid_worth=lambda d, h, mv, s, tc, dist=dist: at.mid(dist, d, h, mv, s, tc),
                                            open_mid=open_mid(dist) if open_roll is not None else None,
                                            catch_escapes=lambda col, dist=dist, fl=(floor, run):
                                            at.col_escapes(dist, col, fl))
            self.model.release(dist)
            at.release(dist)
        strike = self.strike.outcomes(value, geo, poise, self._strike_cm) if self.strike.on else None
        vals, fixed, pure = np.empty(len(games)), np.empty(len(games)), np.empty(len(games))
        for i, g in enumerate(games):
            vals[i], fixed[i], pure[i] = g['value'], g['fixed'], float(np.asarray(g['M']).max(axis=1).min())
            if strike is not None:
                s = strike[self.SK[i]]['value'] - strike[self.SK[i]]['punish_hp']
                vals[i], fixed[i], pure[i] = min(vals[i], s), min(fixed[i], s), min(pure[i], s)
        out = []
        for opts in self.opts:
            j = int(np.argmax(vals[opts]))
            g = games[opts[j]]

            def share(name):
                return float(sum(x for r, x in zip(g['rows'], g['x']) if r == name))
            out.append({'net': float(vals[opts[j]]), 'opt': j, 'fixed': float(fixed[opts].max()),
                        'pure': float(pure[opts[j]]),
                        'stand': share('stand'), 'strafe': share('strafe'),
                        'none': float(sum(y for c_, y in zip(g['cols'], g['y']) if c_ == 'none')),
                        'late': float(sum(y for c_, y in zip(g['cols'], g['y']) if c_ != 'none' and c_ > 0))})
        return out

    def net(self, value, geo, see=None, poise=None):
        """`OutcomeForm.net`'s shape: per situation, what an attack of worth `value` ({frame:
        damage}) and geometries `geo` nets, float64 (`poise` as `parts`')."""
        see = self.see if see is None else see
        if self._mixed(see, None):
            return np.array([p['net'] for p in self.mixup(value, geo, poise=poise)], float)
        o = self._with_strike(self._outcomes(value, geo, see), value, geo, poise)
        return np.array([max(o[k]['value'] - o[k]['punish_hp'] for k in opts) for opts in self.opts], float)


_SIM = {}


def _input_work(item):
    """Phase 1 of one item: (who, [entity]). `who` is None for the distance basis, an attacker
    build's index, ('objective', line name, damage stats in `WEAPON_NEED` order), or ('lookalike',
    'lookalike', damage stats): a spell run at a line for a group alone (`look_needs`)."""
    c = _SIM['ctx']
    kind, x, who = item
    if isinstance(who, tuple):
        c = at_build(c, dict(zip(WEAPON_NEED, who[2])))
    elif who is not None:
        c = at_build(c, damage_stats(c.builds[who]['stats']))
    return who, (spell_job(c, x) if kind == 'spell' else skill_job(c, x))


def _profile_work(key):
    """Phase 2 of one key: (key, its distance-basis profile arrays or None, {who: (net, net_one)},
    [form error]); `who` is an attacker build's index or an objective line's name. The distance
    basis's profile comes from `net_parts`; the attacker builds and the objective lines that score
    the key get their nets from one `OutcomeForm` per geometry they fly, and the form of one that
    flies the distance basis's own geometry is checked against that profile (`form_error`). An
    objective line whose worth the form refuses gets the error's text instead of nets.

    Each of them reads the key with the group the defender cannot tell it from at its own line
    (`look_specs`, `_SIM['looks']` and `_SIM['look_store']`), one `Lookalikes` per distinct group;
    the group is part of the fingerprint, so a form is shared, and checked against the profile, only
    by builds whose group is the same.

    A release scored as the mean of its release frames (`profile_inputs` 'members') is read at its
    nodes (`release_strata_of`: its window's ends and every `RELEASE_STEP`th frame, and every frame
    between two neighbours whose mean nets at the distance basis differ by more than
    `RELEASE_SPLIT_HP` over the share between them), each read as itself in the group (`look_sig` `me`), the group holding one
    member per node weighing the frames the straight lines between nodes give it (`strata_spec`);
    its arrays and nets are their weighted mean (`mix_arrays`): the attacker draws the frame,
    `INFERRED`. Every line reads the distance basis's nodes. The members of one group share its
    `Lookalikes`, so the plan's reads are made once for all of them; the form of each member the
    basis read is checked against that member's own profile. One release frame is the key read
    alone, exactly as before.

    One key is `_profile_batch` of it alone: a release in its group that is not it keeps its first
    nodes unrefined, where `compute` reads the keys whose groups hold one another together and
    refines each of their releases."""
    return _profile_batch([key])[0][:4]


def look_batches(order, looks, cost):
    """`order`'s keys cut into the sets `_profile_batch` reads together: keys whose groups at any
    line (`looks`) hold one another (a release member, 'key~frames', is its key's) are one set, so
    their groups' reads are made once (`er-mechanics-spells.share_lookalikes`); each set keeps
    `order`'s order, the sets dearest first (`cost`, summed)."""
    keys = set(order)
    up = {k: k for k in order}

    def root(k):
        while up[k] != k:
            up[k] = up[up[k]]
            k = up[k]
        return k
    for (k, _), sp in looks.items():
        if k not in keys:
            continue
        for m in sp['keys']:
            b = m.split('~')[0]
            if b in keys:
                up[root(b)] = root(k)
    sets = {}
    for k in order:
        sets.setdefault(root(k), []).append(k)
    return sorted(sets.values(), key=lambda ks: (-sum(cost[k] for k in ks), ks[0]))


def memory_now(mods=()):
    """This process's memory now: 'pid'; 'rss' and 'hwm', its resident and peak resident bytes
    (/proc/self/status VmRSS, VmHWM); with `mods`, 'memos': {module.name: [entries, array bytes]} of
    each module-level dict of theirs named `_CAPITALS` that holds any, the bytes those of the numpy
    arrays its values reach within three levels, each array counted once (r12 was stopped for low
    system memory at 578 of 944 sets, and nothing said which process or memo held it)."""
    out = {'pid': os.getpid()}
    try:
        for ln in Path('/proc/self/status').read_text().splitlines():
            if ln.startswith(('VmRSS:', 'VmHWM:')):
                out['rss' if ln.startswith('VmRSS') else 'hwm'] = int(ln.split()[1]) * 1024
    except OSError:
        pass

    def array_bytes(d):
        seen, n, todo = set(), 0, [(v, 0) for v in d.values()]
        while todo:
            v, depth = todo.pop()
            if id(v) in seen:
                continue
            seen.add(id(v))
            if isinstance(v, np.ndarray):
                n += v.nbytes
            elif depth < 3 and isinstance(v, (tuple, list)):
                todo += [(x, depth + 1) for x in v]
            elif depth < 3 and isinstance(v, dict):
                todo += [(x, depth + 1) for x in v.values()]
        return n
    memos = {}
    for m in mods:
        for nm, v in vars(m).items():
            if nm.startswith('_') and nm[1:2].isupper() and nm.upper() == nm and isinstance(v, dict) and v:
                memos[f"{m.__name__.rsplit('.', 1)[-1]}.{nm}"] = [len(v), array_bytes(v)]
    if mods:
        out['memos'] = memos
    return out


def release_heap():
    """Hand the heap pages freed so far back to the system (glibc `malloc_trim`); nothing where the
    C library has no such call. Without it a worker keeps the peak of the heaviest set it has read:
    in r13 one held 6.36 GB resident with 13 MB in its memos, its homing memo of 1.4 to 2.8 GB
    emptied a set earlier."""
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _profile_batch(keys):
    """`_profile_work` of each of `keys` in turn, every group's reads shared across them: [(key, its
    profile arrays, its nets, its form errors, wall seconds, CPU seconds, `memory_now` after it)],
    the last key's memory with the model's memos. The releases among them are cut into strata at the
    distance basis first (`release_strata_of`), each one's reads there timed with it; a read shared
    is timed with the key that made it first."""
    S.share_lookalikes(True)
    # No other set reads this one's attacks, so the model's memos of earlier sets' flights and
    # touches would only hold memory.
    _SIM['ctx'].A.forget_reads()
    out = []
    try:
        spent = {}
        strata, basis = release_strata_of(keys, spent)
        for i, key in enumerate(keys):
            t, cpu = time.perf_counter(), time.process_time()
            k, arr, nets, errs = _profile_work_shared(key, strata, basis)
            w0, c0 = spent.get(key, (0.0, 0.0))
            wall, cpu = time.perf_counter() - t + w0, time.process_time() - cpu + c0
            mods = (_SIM['ctx'].A, S, T, sys.modules[__name__]) if i == len(keys) - 1 else ()
            out.append((k, arr, nets, errs, wall, cpu, memory_now(mods)))
    finally:
        S.share_lookalikes(False)
    # The set's flights and touches go now rather than at the next set's start, and so does the heap
    # they held; `rss_after` is what the worker keeps between sets.
    _SIM['ctx'].A.forget_reads()
    release_heap()
    if out:
        out[-1][6]['rss_after'] = memory_now()['rss']
    return out


def release_units(x):
    """{release frames after the earliest: inputs} of a release scored over its release frames
    (`profile_inputs` 'members', each keyed `member_key`): 0 for `x` itself."""
    out = {0.0: x}
    for m in x.get('members') or ():
        out[float(m['key'].rsplit('~', 1)[1])] = m
    return out


def strata_spec(sp, strata):
    """`look_spec` `sp` with each release in it read at its nodes only (`release_strata_of`): the
    members of one release, its key and its 'key~frames' release members, read at the nodes
    `strata` gives it ({key: [release frames after the earliest]}, the distance basis's; at this
    line the frame nearest each; a release it does not name, a lookalike-only one, at
    `er-mechanics-ashes.release_nodes` of its own frames), each node one member weighing the frames
    the straight line between nodes gives it (`node_weights`), every other member as it is. None for
    None."""
    if sp is None:
        return None
    A = _SIM['ctx'].A
    by = {}
    for k in sp['keys']:
        b, _, d = k.partition('~')
        by.setdefault(b, {})[float(d) if d else 0.0] = k
    keep, weights = [], {}
    for b, frames in by.items():
        if len(frames) == 1:
            k = next(iter(frames.values()))
            keep.append(k)
            weights[k] = sp['weights'][k]
            continue
        ds = sorted(frames)
        want = strata.get(b) or [ds[i] for i in A.release_nodes(len(ds))]
        nodes = sorted({min(ds, key=lambda d: (abs(d - x), d)) for x in want})
        for d, w in A.node_weights(nodes, {d: sp['weights'][frames[d]] for d in ds}).items():
            keep.append(frames[d])
            weights[frames[d]] = w
    keys = sorted(keep)
    kset = set(keys)
    events = sp.get('events')
    return {**sp, 'keys': keys, 'weights': {k: weights[k] for k in keys},
            'splits': {p: f for p, f in sp['splits'].items() if p[0] in kset and p[1] in kset},
            'events': None if not events else {k: f for k, f in events.items() if k in kset}}


def release_strata_of(keys, spent=None):
    """The nodes each release among `keys` (`profile_inputs` with 'members') is read at ({key:
    [release frames after the earliest]}), and their members' distance-basis profiles ({(key, node
    frame): arrays}). Each release starts at `er-mechanics-ashes.release_nodes` of its frames (its
    first, its last and every `RELEASE_STEP` between), every release of the group read at its nodes
    at the distance basis; then every frame between two neighbours whose mean nets differ by more
    than `RELEASE_SPLIT_HP` over the share of the window between them is added (`release_fill_in`)
    and the group read once more (the rule attack-similarity.md section 2c measured against the
    others). `spent` gathers each key's (wall, CPU) seconds."""
    c = _SIM['ctx']
    A = c.A
    looks, store = _SIM.get('looks') or {}, _SIM.get('look_store')
    rel = {k: _SIM['inputs'][k] for k in keys if (_SIM['inputs'].get(k) or {}).get('members')}
    if not rel:
        return {}, {}
    units = {k: release_units(x) for k, x in rel.items()}
    delays = {k: sorted(u) for k, u in units.items()}
    idx = {k: A.release_nodes(len(ds)) for k, ds in delays.items()}
    # A node's own reads (its geometry against each dodge) are the same whatever other nodes the
    # group holds, so they are kept across rounds; only the group's plan is read again.
    kept = {}

    def read(idx_):
        """(nodes as frames, their profiles, {key: {node index: mean net}}) with every release of the
        batch at the nodes `idx_`."""
        strata = {k: [delays[k][i] for i in idx_[k]] for k in rel}
        basis, nets = {}, {}
        for k in rel:
            t, cpu = time.perf_counter(), time.process_time()
            sp = strata_spec(looks.get((k, None)), strata)
            group = look_group(c, sp, store) if sp else None
            nets[k] = {}
            for i, d in zip(idx_[k], strata[k]):
                me = k if d == 0.0 else member_key(k, d)
                basis[(k, d)] = profile_arrays(c, units[k][d], None, group.member(me) if group else None,
                                               kept.setdefault((k, d), {}))
                nets[k][i] = float(c.w @ basis[(k, d)]['net'].astype(float) / c.W)
            if spent is not None:
                w0, c0 = spent.get(k, (0.0, 0.0))
                spent[k] = (w0 + time.perf_counter() - t, c0 + time.process_time() - cpu)
        return strata, basis, nets
    got = read(idx)
    # Where two neighbouring nodes differ, every frame between them, in one more read
    # (`release_fill_in`).
    new = {k: A.release_fill_in(idx[k], [got[2][k][i] for i in idx[k]], len(delays[k])) for k in rel}
    if any(new[k] is not idx[k] for k in rel):
        got = read(new)
    return got[0], got[1]


def _profile_work_shared(key, strata=None, basis=None):
    """`_profile_work` with the groups' reads shared, every release read at its strata (`strata`,
    `release_strata_of`; None: the midpoint rule unrefined) and `basis` its members' distance-basis
    profiles already read."""
    c = _SIM['ctx']
    strata, basis = strata or {}, basis or {}
    base = _SIM['inputs'].get(key)
    builds = _SIM.get('builds', {}).get(key) or {}
    objs = _SIM.get('objective', {}).get(key) or {}
    looks, store = _SIM.get('looks') or {}, _SIM.get('look_store')
    groups, sigs, specs = {}, {}, {}

    def spec_of(who):
        if who not in specs:
            specs[who] = strata_spec(looks.get((key, who)), strata)
        return specs[who]

    def look_of(who, me=None):
        """(the `Lookalikes` member this key, or its release member `me`, is read as for `who`, its
        `look_sig`), (None, None) when it is alone."""
        sp = spec_of(who)
        if sp is None:
            return None, None
        me = sp['me'] if me is None else me
        gk = (sp['line'], tuple(sp['keys']), tuple(sp['weights'][k] for k in sp['keys']))
        if gk not in groups:
            groups[gk] = look_group(c, sp, store)
        if (gk, me) not in sigs:
            sigs[(gk, me)] = look_sig(sp, store, me)
        return groups[gk].member(me), sigs[(gk, me)]

    def units(x, who):
        """[(inputs, look, its sig, weight, release frames after the earliest)]: `x` itself alone, or
        for a release one member per stratum (`strata_spec`), weighing its stratum."""
        if not x.get('members'):
            lk, sig = look_of(who)
            return [(x, lk, sig, float(x.get('weight', 1.0)), 0.0)]
        sp = spec_of(who)
        us = release_units(x)
        if sp is None:
            return [(u, None, None, float(u.get('weight', 1.0)), d) for d, u in sorted(us.items())]
        out = []
        for d, u in sorted(us.items()):
            k = key if d == 0.0 else member_key(key, d)
            if k in sp['weights']:
                out.append((u,) + look_of(who, k) + (sp['weights'][k], d))
        return out

    def fingerprint(x, sig=None):
        return geo_fingerprint(x['geo'], x['roll'], aims=x.get('aims'), press=x.get('press'), delays=x.get('delays'),
                               look=sig, sprint=bool(x.get('sprint')))
    base_units = units(base, None) if base is not None else []
    # A follow-up's distance-basis profile and the builds and objective lines on its shapes share
    # one `FollowupForm`.
    followup_forms = {}
    if base is not None and base.get('press') is not None:
        followup_forms[fingerprint(base, base_units[0][2])] = FollowupForm(c, base, look=base_units[0][1])

    # The distance basis: a release is read at its nodes (those `release_strata_of` read already),
    # and its profile is their mix, weighed along the straight lines between them.
    marrs = {}
    for j, (u, lk, sig, _, d) in enumerate(base_units):
        marrs[j] = basis.get((key, d)) if len(base_units) > 1 and (key, d) in basis else \
            profile_arrays(c, u, followup_forms.get(fingerprint(u, sig)), lk)
    if base is None:
        arr = None
    elif len(base_units) == 1:
        arr = marrs[0]
    else:
        arr = mix_arrays([marrs[j] for j in range(len(base_units))], [w for *_, w, _ in base_units])
    base_fps = {fingerprint(u, sig): j for j, (u, _, sig, _, _) in enumerate(base_units)}
    forms_of, checked, errs = {}, set(), []

    def best_of(fp, x0, lk0):
        """The net function of the forms of fingerprint `fp` (made from `x0` and `lk0` once)."""
        if fp not in forms_of:
            if x0.get('press') is not None:
                # A follow-up: no free aim (free aim is a spell's), one `FollowupForm`.
                form = followup_forms.get(fp) or FollowupForm(c, x0, look=lk0)
                followup_forms[fp] = form

                def best(x, which, poise=None, form=form):
                    return form.net(x[which], x['geo'], poise=poise)
            else:
                # One form per aim (the lock-on aim, then each free aim, `profile_arrays`); a build
                # or an objective line nets the better of them per situation. Every aim is met by
                # the group's plan (`net_parts_aims` `look`).
                sp0 = bool(x0.get('sprint'))
                forms = [OutcomeForm(c, x0['geo'], x0['roll'], lk0, sp0)] + [OutcomeForm(c, g, x0['roll'], lk0, sp0)
                                                                             for _, g in x0.get('aims') or ()]

                def best(x, which, poise=None, forms=forms):
                    geos = [x['geo']] + [g for _, g in x.get('aims') or ()]
                    return np.max([f.net(x[which], g, poise) for f, g in zip(forms, geos)], axis=0)
            forms_of[fp] = best
        best = forms_of[fp]
        # The form of the distance basis's own geometry is checked against its profile, once.
        j = base_fps.get(fp)
        if j is not None and j in marrs and fp not in checked:
            checked.add(fp)
            bu = base_units[j][0]
            errs.append(max(form_error(best(bu, 'value'), marrs[j]['net']),
                            form_error(best(bu, 'value_one'), marrs[j]['net_one'])))
        return best
    lines = sorted(builds.items()) + sorted(objs.items())
    # Each line's units and their fingerprints, read before the first form is made, so that each
    # form is let go after the last unit that reads it (`last`: line, unit). A form keeps
    # everything its `net` reads (the plays of a caught defender, the strike's runs), and every
    # line of a key ran with all of them held: one process reading Fleeting Microcosm held 1,033
    # MiB in `forms_of` 51 minutes into it, and grew from 3.3 to 7.9 GB in 64 (2026-11-17,
    # `MEASURED` by target/memdiag/oneset.py). No form is asked for after its last unit, so none
    # is made twice and no number moves. A line whose units raise is read again below, where it
    # raises again.
    read, last = {}, {}
    for i, (who, x) in enumerate(lines):
        try:
            read[i] = [(u, lk, sig, w, fingerprint(u, sig)) for u, lk, sig, w, _ in units(x, who)]
        except ValueError:
            continue
    # The lines are read form by form (2026-11-18): those whose first unit reads a form made before
    # them (the distance basis's) first, then each other form's lines together, in the order the
    # forms first appear, so a form is let go before the next one is made. Read in line order, a
    # form lived from its first line to its last, across every form read in between: Waves of
    # Darkness then R2 f68 held the basis's form (its line 'minimum' is the key's last) and the one
    # its lines 1, 4 and 'adjusted' share beside each other form, and stopped at a 10 GiB cap
    # (`MEASURED`, `target/wf/waves/a1`); read form by form it finished in 4,855 s of CPU with 5.5 GB
    # at most (`target/wf/waves/a4`). Each line's nets read nothing another line leaves behind
    # but forms and reads kept for reuse, which give the same numbers made afresh, and `nets` is
    # handed back in line order.
    first = {}
    for i in sorted(read):
        for *_, fp in read[i]:
            first.setdefault(fp, len(first))
    made = set(followup_forms)
    order = sorted(range(len(lines)), key=lambda i: (0, -1, i) if i not in read else
                   (0 if read[i][0][4] in made else 1, first[read[i][0][4]], i))
    for i in order:
        for k, (*_, fp) in enumerate(read.get(i, ())):
            last[fp] = (i, k)
    nets = {}
    for i in order:
        who, x = lines[i]
        try:
            # A release reads its nodes, the distance basis's (`strata_spec`). The defender's strike
            # reads the line's own poise (`line_poise`), every member of it alike.
            n0 = n1 = None
            us = read.pop(i, None)
            if us is None:
                us = [(u, lk, sig, w, fingerprint(u, sig)) for u, lk, sig, w, _ in units(x, who)]
            for k, (u, lk, sig, w, fp) in enumerate(us):
                best = best_of(fp, u, lk)
                e0, e1 = best(u, 'value', x.get('poise')), best(u, 'value_one', x.get('poise'))
                best = None
                if last.get(fp) == (i, k):
                    forms_of.pop(fp, None)
                    followup_forms.pop(fp, None)
                n0 = w * e0 if n0 is None else n0 + w * e0
                n1 = w * e1 if n1 is None else n1 + w * e1
            nets[who] = (n0.astype(np.float32), n1.astype(np.float32))
        except ValueError as e:
            if not isinstance(who, str):
                raise
            nets[who] = f'model error: {e}'
    nets = {who: nets[who] for who, _ in lines if who in nets}
    # The HP the cast takes from its own user, paid on every press whatever the defender does (the
    # game takes it at a TimeAct event or a launch, hit or not: `er-mechanics-self-damage`): off
    # every net, each line at its own max HP (`hp_charge`, `line_hp`). After the form check, which
    # compares the two worths' reads before it.
    cost = hp_charge(base)
    if arr is not None and cost:
        arr = dict(arr, net=arr['net'] - np.float32(cost), net_one=arr['net_one'] - np.float32(cost))
    for who, x in list(builds.items()) + list(objs.items()):
        cw = hp_charge(x)
        if cw and who in nets and not isinstance(nets[who], str):
            nets[who] = (nets[who][0] - np.float32(cw), nets[who][1] - np.float32(cw))
    return key, arr, nets, errs


def mix_arrays(arrs, weights):
    """The `profile_arrays` of a release scored as the mean of its release frames (`release_members`):
    per array and situation the weighted mean of its members' (`weights` summing to 1). The run-in
    gap is the mean over the members that ran in, NaN when none did; `aims_won` the weighted mean of
    the members' shares, None when no member took a free aim."""
    f32 = np.float32
    out = {}
    for k in ARRAYS:
        if k == 'gap':
            g = np.array([a['gap'] for a in arrs], float)
            wv = np.array(weights, float)[:, None] * ~np.isnan(g)
            tot = wv.sum(axis=0)
            out[k] = np.where(tot > 0, np.nansum(np.nan_to_num(g) * wv, axis=0) / np.where(tot > 0, tot, 1.0),
                              np.nan).astype(f32)
        else:
            out[k] = sum(w * np.asarray(a[k], float) for w, a in zip(weights, arrs)).astype(f32)
    won = {}
    for w, a in zip(weights, arrs):
        for aim, share in (a.get('aims_won') or {'target': 1.0}).items():
            won[aim] = won.get(aim, 0.0) + w * share
    out['aims_won'] = {k: round(v, 4) for k, v in sorted(won.items())} if set(won) - {'target'} else None
    return out


def mem_available():
    """The system's available memory in bytes (/proc/meminfo MemAvailable), None where there is none."""
    try:
        with open('/proc/meminfo') as f:
            for ln in f:
                if ln.startswith('MemAvailable:'):
                    return int(ln.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


#: `MemoryGuard`'s defaults: below `MEM_STOP_GIB` of available memory the attack set started last is
#: stopped and dealt again later; a set starts only with `MEM_START_GIB` available. The stop is the
#: 8 GiB `target/v28-mem/memwatch.py` stopped the 2026-11-18 kit run at; a worker of that run held
#: 5 to 9 GiB for hours (its memory log), so a set starts with room for one such worker over the stop.
MEM_STOP_GIB = 8.0
MEM_START_GIB = 16.0


class MemoryGuard:
    """What `pooled` (`fresh`) asks before it starts an item and between items: start one only while
    `available()` is at least `start` bytes, and when it falls under `stop`, stop the item started
    last (`victim`), which `pooled` deals again once memory is back. A stop costs only that item's
    work so far: the run keeps its process, its pooled-compute lock and every item it has done
    (2026-11-18: the kit run's whole process was stopped at 139 of 218 sets, and a restart reads its
    context and phase 1 again, 8 to 15 minutes in the timing log's runs of 2026-11-13 to 2026-11-18).
    `log` names each stop and each wait."""

    def __init__(self, stop=MEM_STOP_GIB * 2**30, start=MEM_START_GIB * 2**30, available=mem_available,
                 poll_s=5.0, log=None):
        self.stop, self.start, self.available, self.poll_s = float(stop), float(max(start, stop)), available, poll_s
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.stops, self.waits = 0, 0
        self._waiting = False

    def _now(self):
        got = self.available()
        return math.inf if got is None else float(got)

    def may_start(self):
        """Whether an item may start now; says once per wait that it waits, and once that it goes on."""
        ok = self._now() >= self.start
        if not ok and not self._waiting:
            self.waits += 1
            self.log(f'memory guard: {self._now() / 2**30:.1f} GiB available, under {self.start / 2**30:g}; '
                     f'no item starts until it is back')
        elif ok and self._waiting:
            self.log(f'memory guard: {self._now() / 2**30:.1f} GiB available again; items start')
        self._waiting = not ok
        return ok

    def victim(self, running):
        """Of `running` ({handle: start time}), the one to stop now (the one started last), or None
        while available memory is at least `stop`."""
        if not running or self._now() >= self.stop:
            return None
        self.stops += 1
        return max(running, key=lambda h: running[h])


#: How many times one item of `pooled` (`fresh`) may die without a result (a signal, the kernel's
#: OOM killer) before the run stops with an error; a stop by `MemoryGuard` is not counted.
FRESH_RETRIES = 2
#: Seconds with no attack set ended after which `_fresh_pool` has the counter print a line per set
#: still running (`PROG.Count.running`), and the seconds between those lines while none ends. A
#: counter line counts a set only when it ends: on 2026-11-19 the run's last line read "207/208
#: attack sets ... ~5m48s left" at 13:04, its last set running since 10:44, and nothing more was
#: printed by 13:45 (`MEASURED` by the main session).
RUNNING_QUIET_S = 300.0
RUNNING_EVERY_S = 600.0


@contextlib.contextmanager
def gc_frozen():
    """Every object this process holds moved to the collector's permanent generation for a fork made
    inside it (`gc.freeze`), and back after it (`gc.unfreeze`, so this process collects as before).
    A forked child shares its parent's pages until it writes one, and a collection writes into every
    object it scans (`INFERRED` from CPython's collector, which keeps its marks in each object's
    header): without this a set's process copied about half of the 3.1 GiB it inherited (the
    context and phase 1). `MEASURED` 2026-11-18 by target/wf/memory/setmem.py, each set in a child
    forked for it with its /proc smaps_rollup read every 0.5 s: Swift Slash's set kept 1.5 GiB of
    it shared and peaked at 3.00 GiB of its own; frozen, 3.0 GiB shared and 2.69 GiB of its own.
    Divine Beast Frost Stomp's set: 1.69 GiB of its own, frozen 1.33. A collection moves no number
    (the two sets' results equal, frozen or not).
    The parent stays frozen while its children run, not just across each fork: a page either side
    writes is copied, and an unfrozen parent's full collection writes the same headers a child's
    would. `MEASURED` 2026-11-18 by target/wf/verify-memory/gctoy.py (3 M dicts, 673 MiB shared, the
    parent keeping 1.2 M new dicts for 8 s while its child slept): unfrozen right after the fork,
    194 MiB of the child's pages went private, the same as with no freeze (186); kept frozen, none.
    It yields `gc.freeze`, to call again right before each later fork, so what the parent made since
    is shared as well; the cycles the parent drops meanwhile wait for the unfreeze at the end."""
    import gc
    gc.freeze()
    try:
        yield gc.freeze
    finally:
        gc.unfreeze()


def _die_with_parent(ppid):
    """Have the kernel kill this process when its parent ends (Linux `PR_SET_PDEATHSIG`), and end it
    now if the parent `ppid` already has: a run stopped by a signal to its own process alone would
    otherwise leave its set processes computing for hours, holding their memory and the inherited
    pooled-compute lock, which the next run then waits for."""
    import signal
    try:
        import ctypes
        ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)
    except (OSError, AttributeError):
        return
    if os.getppid() != ppid:
        os._exit(1)


def _fresh_child(fn, item, core, conn, ppid=None):
    """One item of `pooled` (`fresh`) in a process of its own on `core`: ('ok', `_timed`) or
    ('error', traceback text) sent through `conn`. It ends with its parent `ppid`."""
    if ppid is not None:
        _die_with_parent(ppid)
    try:
        _ai().throttle([core])
        got = ('ok', _timed(fn, item))
    except BaseException:
        import traceback
        got = ('error', traceback.format_exc())
    try:
        try:
            conn.send(got)
        except Exception as e:
            # A result that does not pickle is an error of the item, not a death to deal again.
            conn.send(('error', f'its result could not be sent: {e!r}'))
    finally:
        conn.close()


def _fresh_pool(fn, rest, jobs, cores, guard=None, retries=FRESH_RETRIES, log=None, running=None,
                quiet_s=None, every_s=None, started=None):
    """`_timed(fn, x)` of each of `rest` ([(index, item)]), each in a process forked from this one for
    it alone and pinned to the core of `cores` fewest of them hold, at most `jobs` at once; results as
    they end. Every process is forked from this thread with no other thread running, so none starts
    holding a lock another thread held (a `multiprocessing.Pool` with `maxtasksperchild` forks from
    its own thread: a toy run of it hung a worker within 3 to 19 tasks in 4 of 4 tries while another
    thread wrote to stderr, and 0 of 3,000 with long-lived workers; target/wf/resume/forkhang.py,
    2026-11-18). An item whose process dies without a result is dealt again, at most `retries` times;
    `guard` (`MemoryGuard`) holds new items back and, while more than one runs, stops the one started
    last, to be dealt again first; an item it stopped twice runs alone. With `running`, once no item
    has ended for `quiet_s` (default `RUNNING_QUIET_S`), and every `every_s` (`RUNNING_EVERY_S`)
    after that while none does, `running(item, seconds it has run)` for each item in flight, the
    oldest first (`pooled` prints them under its counter). With `started`, `started(item)` as each
    item's process starts (again, when it is dealt again)."""
    import multiprocessing as mp
    from collections import deque
    from multiprocessing.connection import wait
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    ctx = mp.get_context('fork')
    todo = deque(rest)
    run = {}
    use = {c: 0 for c in cores}
    lost, held = Counter(), Counter()
    quiet_s = RUNNING_QUIET_S if quiet_s is None else quiet_s
    every_s = RUNNING_EVERY_S if every_s is None else every_s
    # When an item last ended (the pool's start before any), and when the items in flight were last
    # told since then (None: not yet).
    ended, told = time.monotonic(), None

    def due():
        return ended + quiet_s if told is None else told + every_s
    # Frozen from the first fork to the last child's end (`gc_frozen`).
    frozen = contextlib.ExitStack()
    refreeze = frozen.enter_context(gc_frozen())
    try:
        while todo or run:
            # An item the guard stopped twice runs alone, and is never stopped while it does: one
            # that needs more than the room the guard leaves would otherwise be stopped and dealt
            # again for ever.
            while (todo and len(run) < max(1, jobs) and not any(x['alone'] for x in run.values())
                   and not (held[todo[0][0]] >= 2 and run) and (guard is None or guard.may_start())):
                item = todo.popleft()
                core = min(cores, key=lambda c: (use[c], cores.index(c)))
                r, w = ctx.Pipe(duplex=False)
                p = ctx.Process(target=_fresh_child, args=(fn, item, core, w, os.getpid()))
                refreeze()
                p.start()
                w.close()
                use[core] += 1
                run[r] = {'p': p, 'item': item, 'core': core, 't': time.monotonic(), 'stopped': False,
                          'alone': held[item[0]] >= 2}
                if started is not None:
                    started(item)
            if not run:
                time.sleep(guard.poll_s if guard is not None else 0.1)
                continue
            timeout = guard.poll_s if guard is not None else None
            if running is not None:
                # Woken in time to tell the items in flight, which no result may come to do.
                soon = max(0.0, due() - time.monotonic())
                timeout = soon if timeout is None else min(timeout, soon)
            wait(list(run) + [x['p'].sentinel for x in run.values()], timeout=timeout)
            for conn in list(run):
                x = run[conn]
                got = None
                if conn.poll():
                    try:
                        got = conn.recv()
                    except (EOFError, OSError):
                        got = None
                elif x['p'].is_alive():
                    continue
                elif conn.poll():
                    # It sent its result and ended between the two looks.
                    try:
                        got = conn.recv()
                    except (EOFError, OSError):
                        got = None
                x['p'].join()
                conn.close()
                del run[conn]
                use[x['core']] -= 1
                if got is not None and got[0] == 'ok':
                    ended, told = time.monotonic(), None
                    yield got[1]
                    continue
                if got is not None:
                    raise RuntimeError(f'attack similarity: item {x["item"][0]} failed in its process:\n{got[1]}')
                i = x['item'][0]
                if x['stopped']:
                    held[i] += 1
                else:
                    lost[i] += 1
                    if lost[i] > retries:
                        raise RuntimeError(f'attack similarity: item {i} died without a result {lost[i]} times '
                                           f'(last exit code {x["p"].exitcode})')
                    log(f'attack similarity: item {i} died without a result (exit code {x["p"].exitcode}); '
                        f'dealing it again ({lost[i]} of {retries})')
                todo.appendleft(x['item'])
            now = time.monotonic()
            if running is not None and run and now >= due():
                for x in sorted(run.values(), key=lambda x: x['t']):
                    if not x['stopped']:
                        running(x['item'], now - x['t'])
                told = now
            # One item running is left to run: the guard has already held every other back.
            cands = {conn: x['t'] for conn, x in run.items() if not x['stopped']}
            if guard is not None and len(cands) > 1:
                v = guard.victim(cands)
                if v is not None:
                    x = run[v]
                    x['stopped'] = True
                    log(f'memory guard: {guard._now() / 2**30:.1f} GiB available, under {guard.stop / 2**30:g}; '
                        f'stopping item {x["item"][0]} (pid {x["p"].pid}, {time.monotonic() - x["t"]:.0f} s in) '
                        f'to deal it again')
                    x['p'].kill()
    finally:
        for x in run.values():
            x['p'].kill()
        for conn, x in run.items():
            x['p'].join()
            conn.close()
        frozen.close()


def _timed(fn, item):
    """(index, `fn(x)`, wall seconds, CPU seconds of the process that ran it) for `item` = (index,
    x): one item of `pooled`, timed where it runs."""
    i, x = item
    t, cpu = time.perf_counter(), time.process_time()
    r = fn(x)
    return i, r, time.perf_counter() - t, time.process_time() - cpu


def pooled(fn, items, jobs, unit, timing=None, label=str, weight=None, fresh=False, guard=None,
           expect=None, source='measurement', older=None, older_expect=None):
    """`fn` over `items`, results as they finish: the first in this process (what it reads first the
    workers inherit), the rest on `jobs` forked workers each on a core of its own
    (`er-mechanics-ash-interrupt` throttle); `jobs` 1 runs here. Counts `  i/N <unit>`, its time
    left by `weight(item)`, the item's estimated cost, when one is given (`PROG.Count`). With
    `timing`, each item's seconds go to `Timing.item` under `unit`, named `label(item)`. With
    `fresh`, each item after the first runs in a process of its own (`_fresh_pool`), held back and
    stopped by `guard` (`MemoryGuard`) when memory runs short, and while none ends the counter
    prints a line per item still running (`PROG.Count.running`), named `label(item)`, against
    `expect(item)`, its expected seconds by `source`, or None; with none, against
    `older_expect(item)` when given, (its seconds on older code, those scaled to this code) or None.
    `older(item)` is the part of `weight(item)` taken from older code's times, whose share of the
    time left the counter names. With a `weight` the time left is wall-clock (`PROG.Count` `jobs`,
    `sizes`): each item's own seconds, at most `jobs` items at once (`_fresh_pool`, the pool's size),
    the ones in flight from when they started; in a pool of long-lived workers (not `fresh`), whose
    items' starts this process does not see, the items not ended count whole."""
    items = list(items)
    if not items:
        return
    ws = None if weight is None else [float(weight(x)) for x in items]
    os_ = None if ws is None or older is None else [float(older(x)) for x in items]
    tick = PROG.Count(len(items), unit, weight=None if ws is None else sum(ws),
                      older=None if os_ is None else sum(os_), jobs=None if ws is None else max(1, jobs),
                      sizes=ws)

    def note(got, here):
        i, r, wall, cpu = got
        if timing is not None:
            timing.item(unit, label(items[i]), wall, cpu, here)
        tick(w=0.0 if ws is None else ws[i], o=0.0 if os_ is None else os_[i], i=i, s=wall)
        return r
    tick.started(0)
    first = _timed(fn, (0, items[0]))
    yield note(first, True)
    rest = list(enumerate(items))[1:]
    if jobs <= 1 or not rest:
        for x in rest:
            tick.started(x[0])
            yield note(_timed(fn, x), True)
        return
    import multiprocessing as mp
    AI = _ai()
    cpus = AI.allowed_cpus()
    workers = cpus[1:] or cpus
    ctx = mp.get_context('fork')
    with AI.caller_throttled(cpus[0]):
        if fresh:
            # A process per attack set, forked from this one (so no read this process made is
            # repeated): a worker kept its memos and freed heap from every set it had read. The
            # 2026-11-18 kit run's workers sat at 5-9 GiB each for hours (its memory log); how much of
            # that earlier sets held is not measured.
            def running(item, ran):
                x = item[1]
                e = None if expect is None else expect(x)
                tick.running(label(x), ran, e, source,
                             older=None if e is not None or older_expect is None else older_expect(x))
            for got in _fresh_pool(fn, rest, jobs, workers, guard, running=running,
                                   started=lambda item: tick.started(item[0])):
                yield note(got, False)
            return
        # The workers fork as the pool starts, and the parent stays frozen while they run (`gc_frozen`).
        with gc_frozen(), ctx.Pool(max(1, jobs), initializer=AI._worker_init,
                                   initargs=(workers, ctx.Value('i', 0))) as pool:
            for got in pool.imap_unordered(functools.partial(_timed, fn), rest, chunksize=1):
                yield note(got, False)


def default_jobs():
    return min(8, max(1, len(_ai().allowed_cpus()) - 1))


# ---------------------------------------------------------------------------------------------
# timing

#: The timing log: one JSON line per run (`Timing.record`), appended and never rewritten, so every
#: run can be set beside an earlier one (`--timing`).
TIMING_LOG = EX.CACHE / 'attack-similarity-timing.jsonl'
#: The seed of the attack sets' shuffle (`set_order`): fixed, so two runs of one code read the sets
#: in one order.
SET_ORDER_SEED = 11


def trail_path(log=None):
    """Where a run beside timing log `log` (default `TIMING_LOG`) appends its items as they end
    (`Timing.note`): the log's name with `.items.jsonl` for `.jsonl`."""
    return Path(log if log is not None else TIMING_LOG).with_suffix('.items.jsonl')


#: The power of a key's geometric `cost` its seconds go as: fitted on r12's 771 items (log seconds on
#: log cost, Spearman 0.58; the plain cost, power 1, projected worse than counting sets). On r13's
#: 297 items, fitted on none of them, it put the time left within a median 40% of the sample's at a
#: tenth of it done and 22% at a quarter, where counting sets put it within 54% and 31% (400 random
#: orders each).
COST_TIME_POWER = 0.42


def set_weight(keys, cost):
    """The attack set of `keys`' share of the stage's time, for its counter (`pooled` weight)."""
    return sum(max(float(cost.get(k, 0.0)), 1e-9) ** COST_TIME_POWER for k in keys)


#: A set whose measured seconds (`measured_seconds`, its keys' summed) are at least this share of
#: every set's measured seconds is read before the shuffled rest, the dearest first (`set_order`).
#: The run cannot end before its longest set does, and a long set handed out late ends late: r21's
#: Flame Fall Upon Them set, dealt 538th of 952, was still running 5.6 h later when the run was
#: stopped at 9 h (`MEASURED`, its trail: the worker's items end at set 536 and sum to 12,153 s of
#: the 32,400 s stage), and Founding Rain of Stars' 26,364 s set was dealt 155th. Replaying r21's
#: per-set seconds (set 537's from r11's, which ran it whole) on 3 workers: 17.8 h in the seeded
#: shuffle, 14.4 h, the longest set's own, with the sets over 0.5% to 5% first (`INFERRED`: a
#: replay, not a run).
LONG_SET_SHARE = 0.02


def measured_seconds(trail=None, code=None, other=False):
    """{attack key: wall seconds of its newest 'attacks' item} in the timing log's trail (`trail`,
    default `trail_path()`), any run and any version: what `set_order` deals the long sets by. {}
    when there is no trail. Read for the order alone, which moves no number. With `code` (a
    `code_tag`), only the rows measured on that code (`same_code_seconds`); with `other` as well,
    only the rows measured on any other code, an unmarked run's included (`older_code_seconds`)."""
    out = {}
    try:
        with Path(trail if trail is not None else trail_path()).open() as f:
            for ln in f:
                if '"attacks"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if code is not None and (r.get('code') == code) == other:
                    continue
                if r.get('stage') == 'attacks' and isinstance(r.get('item'), str) and r.get('wall') is not None:
                    # `Timing.item` names a key seen twice in one run '<key>#2'.
                    out[r['item'].partition('#')[0]] = float(r['wall'])
    except OSError:
        return {}
    return out


def same_code_seconds(timing):
    """{attack key: newest wall seconds} measured on the code `timing` runs (`code_tag` of its
    `mark_code`) in its trail; {} when it keeps no trail or is not marked. Read once, before the
    attack sets start, so this run's own sets count only on a later run of the same code (a resume)."""
    if timing is None or timing.trail is None or timing.code is None:
        return {}
    return measured_seconds(timing.trail, code=code_tag(timing.code))


def older_code_seconds(timing):
    """{attack key: newest wall seconds} measured on any code but the one `timing` runs, in its
    trail (an unmarked run's rows included, and every row when `timing` is not marked); {} when it
    keeps no trail. What the counter weighs a key by when it has no same-code measurement
    (`measured_weight`): 2026-11-21's run read none for the 125 keys it had left, weighed them by
    the cost term at its 25 kept sets' pace, and after its 1.2 s first set read "~43m27s left" at
    14:17:45 while Cannon of Haima, Founding Rain of Stars and Crystal Barrage ran past 1h15m; the
    trail held spell:4080:tap at 9,807 s from older code (`MEASURED`, its wrap.log and the trail)."""
    if timing is None or timing.trail is None:
        return {}
    if timing.code is None:
        return measured_seconds(timing.trail)
    return measured_seconds(timing.trail, code=code_tag(timing.code), other=True)


#: How many keys measured on both this code and older code `older_code_ratio` needs before it scales
#: older code's seconds by them; with fewer, older code's seconds are taken as they are.
OLDER_RATIO_MIN = 3


def older_code_ratio(same, older):
    """The median, over the keys measured on this code (`same`) and on older code (`older`), of
    their seconds on this code over their seconds on older code: what an older-code time is scaled
    by before the counter weighs a key by it (`measured_weight`). 1.0 below `OLDER_RATIO_MIN` keys.
    Scaled, not raw, because the code's speed moves between versions: on 2026-11-21 the 32 keys
    measured on 60a09d70756e and on older code took a median 1.66 times their older seconds
    (quartiles 1.32 and 1.79; 1.45 to 1.77 by older code; 1.57 over the 12 keys over 60 s on older
    code; `MEASURED`, the trail). The median, so one key whose model changed (two took 5.3 and 5.6
    times) moves it little. That the ratio of the keys measured both ways holds for the keys
    measured only on older code is `INFERRED`."""
    xs = [float(same[k]) / float(older[k]) for k in same if k in older and float(older[k]) > 0]
    return statistics.median(xs) if len(xs) >= OLDER_RATIO_MIN else 1.0


def seconds_per_weight(cost, same):
    """The median, over the keys of `cost` with a measurement (`same`: the same-code seconds, or
    with older code's scaled ones under them, `known_seconds`), of their seconds per
    `COST_TIME_POWER` power of their cost: what a key with none weighs per unit of `set_weight`'s
    term. None when no key of `cost` has one. `INFERRED`: the brief's "seconds-per-cost" read as
    per unit of the powered cost, the unit `set_weight` is fitted in."""
    xs = [float(same[k]) / max(float(cost[k]), 1e-9) ** COST_TIME_POWER for k in same if k in cost]
    return statistics.median(xs) if xs else None


def known_seconds(same, older=None, ratio=1.0):
    """{key: seconds}: each key's same-code seconds (`same`), and a key with none its older code's
    (`older`) times `ratio` (`older_code_ratio`)."""
    return {**{k: float(v) * ratio for k, v in (older or {}).items()}, **same}


def measured_weight(keys, cost, same, scale, older=None, ratio=1.0):
    """The attack set of `keys`' share of the stage's time for its counter (`pooled` weight), in
    seconds: each key's same-code measurement (`same`), a key with none its newest older-code
    measurement (`older`) times `ratio` (`older_code_ratio`), and a key with neither its
    `set_weight` term times `scale` (`seconds_per_weight` of `known_seconds`); with no `scale` (no
    key measured on any code), `set_weight`. 2026-11-19's counter weighed by `set_weight` alone read
    "207/208 attack sets ... 98.6% of the estimated work, ~5m48s left" at 13:04 while the last set
    had run since 10:44 and ran on past 13:45 (`MEASURED` by the main session)."""
    if scale is None:
        return set_weight(keys, cost)
    older = older or {}
    return sum(float(same[k]) if k in same else float(older[k]) * ratio if k in older
               else scale * max(float(cost.get(k, 0.0)), 1e-9) ** COST_TIME_POWER for k in keys)


def older_weight(keys, same, scale, older=None, ratio=1.0):
    """The part of `measured_weight(keys, ...)` taken from older code's seconds: the keys with no
    same-code measurement and an older one, at `ratio`; 0 with no `scale` (then no part is seconds)."""
    if scale is None or not older:
        return 0.0
    return sum(float(older[k]) * ratio for k in keys if k not in same and k in older)


def expected_seconds(keys, same):
    """The attack set of `keys`' seconds by its same-code measurements (`same`), summed, or None
    unless every key has one. A set's keys reach the trail together, when the set ends (`compute`),
    so on one code a set is measured whole or not at all."""
    if not keys or any(k not in same for k in keys):
        return None
    return sum(float(same[k]) for k in keys)


def older_expected(keys, same, older, ratio=1.0):
    """(seconds, seconds scaled to this code) of the attack set of `keys` by its newest older-code
    measurements (`older`, at `ratio`, `older_code_ratio`), a key measured on this code (`same`) by
    that, for its still-running line when it has no same-code seconds (`expected_seconds`); None
    unless every key has one or the other."""
    if not keys or any(k not in same and k not in older for k in keys):
        return None
    raw = sum(float(same[k]) if k in same else float(older[k]) for k in keys)
    return raw, sum(float(same[k]) if k in same else float(older[k]) * ratio for k in keys)


def set_label(keys, records):
    """An attack set as a still-running line names it: its first key's attack name (`records`), or
    the key when it has no record, and how many keys more."""
    if not keys:
        return '?'
    name = (records.get(keys[0]) or {}).get('name') or keys[0]
    return name + (f' and {len(keys) - 1} more' if len(keys) > 1 else '')


def set_order(sets, seed=SET_ORDER_SEED, measured=None):
    """`look_batches`' sets (dearest first) in the order the attack sets are read: the cheapest
    first, since `pooled` reads its first item before it starts the workers, which wait for it; then
    the rest shuffled. The geometric cost does not predict a set's time (r11's 2nd and 4th sets took
    55 s and 433 s per 1% of it), so the dearest-first order gave the counter no honest time left; in
    a shuffled order the sets done are a random sample of them, and the count-linear time left is a
    projection from that sample. The order moves no number: no set reads another's (each empties the
    groups' shared reads, `_profile_batch`; the model's memos give what a fresh read gives) and
    every result is filed by its key.

    With `measured` ({key: seconds}, `measured_seconds`) the sets of the shuffle whose measured
    seconds are at least `LONG_SET_SHARE` of all of theirs come first, dearest first, and the rest
    keep the shuffle's order: a long set dealt late is what the run waits on at its end."""
    rest = list(sets[:-1])
    random.Random(seed).shuffle(rest)
    if measured:
        secs = [sum(float(measured.get(k, 0.0)) for k in s) for s in rest]
        total = sum(secs)
        long_ = sorted((i for i, x in enumerate(secs) if total > 0 and x >= LONG_SET_SHARE * total),
                       key=lambda i: (-secs[i], i))
        if long_:
            first = set(long_)
            rest = [rest[i] for i in long_] + [s for i, s in enumerate(rest) if i not in first]
    return list(sets[-1:]) + rest


def resume_order(todo, cost):
    """`todo` (the sets of `set_order` not yet read) with its cheapest set (`look_batches`' measure,
    the summed `cost`; the first of equals) moved to the front and the rest in their order: `pooled`
    reads the first in this process while the workers wait. A fresh run's first set is already the
    cheapest, so its order is unchanged."""
    if len(todo) < 2:
        return list(todo)
    i = min(range(len(todo)), key=lambda j: (sum(cost[k] for k in todo[j]), j))
    return [todo[i]] + todo[:i] + todo[i + 1:]


def _load():
    """The 1, 5 and 15 minute load averages, or None where the system gives none."""
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def _git(*args):
    """The output of `git -C <this directory> <args>`, or None when git cannot say."""
    try:
        p = subprocess.run(['git', '-C', str(HERE), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


class Timing:
    """Where a run's time goes: wall and CPU seconds per stage (`lap`) and per item of a pooled
    stage (`item`, which `pooled` calls), appended to the timing log with the code the run ran and
    the machine's load (`record`). Every run keeps one (`main`, the selftest). CPU seconds per item
    are what a code change moves; wall seconds move with the workers and the load as well."""

    def __init__(self, clock=time.perf_counter, cpu=time.process_time, out=None, trail=None):
        self.clock, self.cpu = clock, cpu
        # out: where each lap prints its stage's line as the stage ends (`lap_line`), so a tail tab
        # shows how long every stage took while the run goes on; None prints nothing.
        self.out = out
        # trail: a file each item is appended to as it ends (`note`), so a run stopped before
        # `record` still leaves its items' seconds: r11 was stopped after 10 of 944 attack sets and
        # left none (2026-11-11). None keeps them in memory alone.
        self.trail = trail
        self.start = self._t = clock()
        self._c = cpu()
        self.when = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        self.load = _load()
        # facts: what `run` knows of the run (the scope, the stamp, the cache outcome, the workers).
        self.stages, self.items, self.facts, self._workers = {}, {}, {}, {}
        # The code the run ran (`code_stamp`), taken once its scripts are loaded (`mark_code`): a
        # file edited while it runs is then not taken for what ran.
        self.code = None

    def mark_code(self, code=None):
        self.code = code_stamp() if code is None else code

    def item(self, stage, name, wall, cpu, here, **extra):
        """One item of pooled stage `stage`; `here` when it ran in this process, whose own CPU
        seconds (`lap`) then hold it already. A name seen before gets `#2`, `#3`. `extra` (the
        process's `memory_now`) goes to the trail only."""
        its = self.items.setdefault(stage, {})
        key, n = name, 1
        while key in its:
            n += 1
            key = f'{name}#{n}'
        its[key] = [round(wall, 4), round(cpu, 4)]
        if not here:
            self._workers[stage] = self._workers.get(stage, 0.0) + cpu
        self.note(stage=stage, item=key, wall=its[key][0], cpu=its[key][1], **extra)

    def note(self, **row):
        """Append `row`, with when the run began and, once it is marked (`mark_code`), the tag of
        the code it ran (`code_tag`, 'code'), to `trail` at once as one JSON line. The tag is what
        lets a later run of the same code weigh its counter by these seconds (`same_code_seconds`):
        seconds measured on other code did not predict this code's (over the 229 keys the
        2026-11-19 kit run, target/v28-mem/kit.py, finished, its seconds over each key's newest
        earlier measurement had median 2.81, p10 1.36, p90 27.1, and summed 61,521 s against
        10,902 s; `MEASURED` 2026-11-19 from the trail)."""
        if self.trail is None:
            return
        path = Path(self.trail)
        path.parent.mkdir(parents=True, exist_ok=True)
        tag = {} if self.code is None else {'code': code_tag(self.code)}
        with path.open('a') as f:
            f.write(json.dumps({'when': self.when, **tag, **row}, default=_json_default) + '\n')

    def lap(self, stage):
        """Close `stage`: the seconds since the previous lap or the start, and for a pooled stage
        its item count and its workers' CPU seconds."""
        t, c = self.clock(), self.cpu()
        row = {'wall': round(t - self._t, 3), 'cpu': round(c - self._c, 3)}
        if stage in self.items:
            row.update(items=len(self.items[stage]), workers_cpu=round(self._workers.get(stage, 0.0), 3))
        self.stages[stage] = row
        self._t, self._c = t, c
        if self.out is not None:
            print(f'attack similarity: {self.lap_line(stage)}', file=self.out, flush=True)

    def lap_line(self, stage):
        """Closed stage `stage` as its lap prints it: its wall time, a pooled stage's items and CPU
        seconds, and the time since the run began."""
        s = self.stages[stage]
        return (f"{stage} took {PROG.span(s['wall'])}"
                + (f" ({s['items']} items, {stage_cpu(s):,.0f} CPU s)" if 'items' in s else '')
                + f"; {PROG.span(self._t - self.start)} since the start")

    def line(self):
        """The stderr line: the total wall seconds and each stage's, with a pooled stage's items and
        CPU seconds."""
        parts = []
        for k, s in self.stages.items():
            parts.append(f"{k} {s['wall']:,.0f} s" + (f" ({s['items']} items, {stage_cpu(s):,.0f} CPU s)"
                                                      if 'items' in s else ''))
        return f'total {self.clock() - self.start:,.0f} s: ' + '; '.join(parts)

    def record(self, path=None, code=None):
        """Append this run to `path` (default `TIMING_LOG`) as one JSON line and return it: when it
        started, `facts`, the total wall seconds, git's branch and head and whether this directory
        differs from it, the code it ran (`mark_code`, else now: what the report compares, since a
        dirty tree is not its head) and the files that changed on disk while it ran, the CPUs it may
        use, the load at the start and the end, the stages and the items."""
        dirty = _git('status', '--porcelain', '--', '.')
        now = code_stamp()
        code = code if code is not None else self.code if self.code is not None else now
        row = {'when': self.when, **self.facts, 'wall': round(self.clock() - self.start, 3),
               'git': {'branch': _git('rev-parse', '--abbrev-ref', 'HEAD'), 'head': _git('rev-parse', 'HEAD'),
                       'scripts_dirty': None if dirty is None else bool(dirty)},
               'code': code, 'code_moved': sorted(f for f in set(code) & set(now) if code[f] != now[f]),
               'cpus': sorted(os.sched_getaffinity(0)), 'load': self.load, 'load_end': _load(),
               'stages': self.stages, 'items': self.items}
        path = Path(path) if path is not None else TIMING_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as f:
            f.write(json.dumps(row, default=_json_default) + '\n')
        return row


def stage_cpu(s):
    """A stage's CPU seconds: this process's and its workers'."""
    return s.get('cpu', 0.0) + s.get('workers_cpu', 0.0)


def timing_runs(path=None):
    """The timing log's runs, oldest first, each with its line number as 'n'; a line that does not
    parse (a run killed while appending) is skipped."""
    path = Path(path) if path is not None else TIMING_LOG
    out = []
    if not path.exists():
        return out
    for i, ln in enumerate(path.read_text().splitlines(), 1):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append({**r, 'n': i})
    return out


def timing_scope(r):
    """What two runs share to be compared: the scope (the full run, an --only set, the selftest),
    the names, and whether the profiles came from the cache."""
    return r.get('scope'), r.get('only'), r.get('cache') == 'hit'


def timing_pick(runs, specs):
    """(run A, run B) for the `--timing` arguments: none is the newest run and the newest earlier
    run of its scope (`timing_scope`); one names B; two name A and B. A run is named by its line
    number, or from the end when negative (-1 the newest). None where there is no such run."""
    def get(s):
        n = int(s)
        if n < 0:
            return runs[n] if -n <= len(runs) else None
        return next((r for r in runs if r['n'] == n), None)
    if len(specs) >= 2:
        return get(specs[0]), get(specs[1])
    b = get(specs[0]) if specs else (runs[-1] if runs else None)
    if b is None:
        return None, None
    return next((r for r in reversed(runs) if r['n'] < b['n'] and timing_scope(r) == timing_scope(b)), None), b


def timing_report(a, b, top=10, out=sys.stdout):
    """Run B beside run A: each stage's wall and CPU seconds and their ratio, the CPU seconds per
    item, the code files whose sha256 differs, and per pooled stage the items whose CPU seconds
    moved most, with their share of the stage's change."""
    def head(tag, r):
        g = r.get('git') or {}
        only = r.get('only') or []
        return (f"{tag} = run {r['n']}, {r.get('when')}, {r.get('scope')}"
                f"{' --only ' + (', '.join(only) if len(only) <= 4 else f'{len(only)} names') if only else ''}, "
                f"cache {r.get('cache')}, "
                f"stamp {r.get('stamp')}, {g.get('branch')} {(g.get('head') or '')[:8]}"
                f"{' (scripts differ from it)' if g.get('scripts_dirty') else ''}, {r.get('jobs')} workers on "
                f"{len(r.get('cpus') or ())} CPUs, load {(r.get('load') or [None])[0]} -> {(r.get('load_end') or [None])[0]}")

    def ratio(x, y):
        return f'{y / x:.2f}' if x else '-'

    def per(s):
        return f"{stage_cpu(s) / s['items']:.3f}" if s.get('items') else '-'
    print(head('A', a), file=out)
    print(head('B', b), file=out)
    print(f"{'stage':<18}{'wall A':>10}{'wall B':>10}{'B/A':>6}{'CPU A':>10}{'CPU B':>10}{'B/A':>6}{'items A/B':>13}"
          f"{'CPU/item A':>12}{'CPU/item B':>12}", file=out)
    sa, sb = a.get('stages') or {}, b.get('stages') or {}
    for k in list(sa) + [k for k in sb if k not in sa]:
        x, y = sa.get(k) or {}, sb.get(k) or {}
        print(f"{k:<18}{x.get('wall', 0):>10,.1f}{y.get('wall', 0):>10,.1f}{ratio(x.get('wall'), y.get('wall', 0)):>6}"
              f"{stage_cpu(x):>10,.1f}{stage_cpu(y):>10,.1f}{ratio(stage_cpu(x), stage_cpu(y)):>6}"
              f"{str(x.get('items', '-')) + '/' + str(y.get('items', '-')):>13}{per(x):>12}{per(y):>12}", file=out)
    ca, cb = sum(map(stage_cpu, sa.values())), sum(map(stage_cpu, sb.values()))
    print(f"{'total':<18}{a.get('wall', 0):>10,.1f}{b.get('wall', 0):>10,.1f}{ratio(a.get('wall'), b.get('wall', 0)):>6}"
          f"{ca:>10,.1f}{cb:>10,.1f}{ratio(ca, cb):>6}", file=out)
    code_a, code_b = a.get('code') or {}, b.get('code') or {}
    moved = sorted(f for f in set(code_a) | set(code_b) if code_a.get(f) != code_b.get(f))
    print(f"code that differs: {', '.join(moved) if moved else 'none'}", file=out)
    for k in sb:
        ia, ib = (a.get('items') or {}).get(k) or {}, (b.get('items') or {}).get(k) or {}
        if not ia or not ib:
            continue
        both = sorted(set(ia) & set(ib))
        d = {n: ib[n][1] - ia[n][1] for n in both}
        tot = sum(d.values())
        only_a, only_b = sorted(set(ia) - set(both)), sorted(set(ib) - set(both))
        print(f"{k}: CPU {sum(ia[n][1] for n in both):,.1f} -> {sum(ib[n][1] for n in both):,.1f} s over the "
              f"{len(both)} items in both" + (f"; {len(only_a)} only in A ({sum(ia[n][1] for n in only_a):,.1f} s), "
                                              f"{len(only_b)} only in B ({sum(ib[n][1] for n in only_b):,.1f} s)"
                                              if only_a or only_b else ''), file=out)
        for n in sorted(both, key=lambda n: (-abs(d[n]), n))[:top]:
            share = f'  {d[n] / tot:+.0%} of the change' if tot else ''
            print(f'  {d[n]:+10.2f} s {ia[n][1]:10.2f} -> {ib[n][1]:10.2f}  {n}{share}', file=out)
    return 0


# ---------------------------------------------------------------------------------------------
# the run

class Result:
    """Every record, the profiles and the per-entity shares; built by `compute` or `load_cache`.
    `builds` is one dict per attacker build: 'name', 'stats', 'n', 'rl', 'reach', 'usable', its
    'records' and 'excluded' (no profiles) and its 'nets' {key: (net, net_one)}."""

    def __init__(self, meta, records, excluded, comps, arrays, comp_arrays, w, F, truncated=None, builds=None):
        self.meta = meta
        self.builds = builds or []
        self.records = records              # key -> scored / partial entity
        self.excluded = excluded            # [exclusion]
        self.comps = comps                  # key -> component
        self.keys = [k for k in records if k in arrays]
        self.comp_keys = [k for k in comps if k in comp_arrays]
        self.A = {n: np.stack([arrays[k][n] for k in self.keys]) if self.keys else np.zeros((0, len(w)), np.float32)
                  for n in ARRAYS}
        self.C = {n: np.stack([comp_arrays[k][n] for k in self.comp_keys]) if self.comp_keys
                  else np.zeros((0, len(w)), np.float32) for n in ARRAYS}
        self.w = np.asarray(w, float)
        self.W = float(self.w.sum())
        self.F = F
        self.truncated = truncated or {}
        self.index = {k: i for i, k in enumerate(self.keys)}
        self.cindex = {k: i for i, k in enumerate(self.comp_keys)}
        for k, i in self.index.items():
            r = self.records[k]
            r.update(shares(self.A, i, self.w, self.W))
        for k, i in self.cindex.items():
            self.comps[k].update(shares(self.C, i, self.w, self.W))


def shares(arr, i, w, W):
    """Per-entity summaries of one profile: the support shares (`live_now`, `live_best`: weight
    where a land or punish share is above `LIVE_EPS`), `reach`, `mean_net`, the land masses (the
    weighted mean land share pressing at once, with the best option, and the larger of the two:
    what the groups and the association floor read) and the profile masses (mean of the land and
    punish shares: what D_now, D_best and D_rel are measured against)."""
    ln, pn, l, p = (np.asarray(arr[n][i], float) for n in REL_CHANNELS)
    lm_now, lm_best = float(w @ ln / W), float(w @ l / W)
    return {'live_now': float(w[(ln > LIVE_EPS) | (pn > LIVE_EPS)].sum() / W),
            'live_best': float(w[(l > LIVE_EPS) | (p > LIVE_EPS)].sum() / W),
            'reach': float(w[arr['base_now'][i] > 0].sum() / W),
            'mean_net': float(w @ arr['net'][i].astype(float) / W),
            'land_mass_now': lm_now, 'land_mass_best': lm_best, 'land_mass': max(lm_now, lm_best),
            'mass_now': float(w @ (ln + pn) / (2 * W)), 'mass_best': float(w @ (l + p) / (2 * W)),
            'mass': float(w @ (ln + pn + l + p) / (4 * W))}


def build_items(c, spell_ids, skill_jobs):
    """([(kind, spell id or opening group, build index)], [{'spells', 'of_spells',
    'skill_openings', 'of_skill_openings'} per build]): what each attacker build can cast
    (`Context.cast_short`) and wield (`build_job`), of the distance basis's candidates."""
    items, usable = [], []
    for bi, b in enumerate(c.builds):
        st = damage_stats(b['stats'])
        sp = [m for m in spell_ids if not c.cast_short(m, st)]
        sk = [jb for jb in (build_job(c, j, st) for j in skill_jobs) if jb is not None]
        items += [('spell', m, bi) for m in sp] + [('skill', jb, bi) for jb in sk]
        usable.append({'spells': len(sp), 'of_spells': len(spell_ids), 'skill_openings': len(sk),
                       'of_skill_openings': len(skill_jobs)})
    return items, usable


#: Spread under which the land shares of an attack count as one number (`flat_land`).
FLAT_EPS = 1e-4


def flat_land(arr, i, land_mass_now, null):
    """The one share of its worth row i of profile arrays `arr` lands wherever its best option
    lands anything, when it lands under `null` pressing at once (its profile is the run-in alone)
    and every such share is within `FLAT_EPS` of one value; else None. A run-in share was a sum of
    roll directions (a quarter each until version 22, which lets him pick one) and timing points,
    and for many attacks it was the same
    number in every situation it is picked in (1/4: one direction of four caught; 3/14: one
    direction at six of seven timing points, the Glintblade Phalanx spell and ash, `MEASURED`
    round 4), so two such attacks agree on the land channels whatever they are."""
    if land_mass_now >= null:
        return None
    lv = np.asarray(arr['land'][i], float)
    on = lv > LIVE_EPS
    if not on.any():
        return None
    v = lv[on]
    return round(float(v.mean()), 4) if float(v.max() - v.min()) <= FLAT_EPS else None


def input_label(c, item):
    """A phase-1 item's name in the timing log: its job (`spell:<magic id>`, or a skill's parent key)
    and who it is run for: nothing for the distance basis, an attacker build's name, an objective
    line's name and damage stats."""
    kind, x, who = item
    job = f'spell:{x}' if kind == 'spell' else job_parent(x)
    if who is None:
        return job
    if isinstance(who, tuple):
        return f"{job}@{who[1]} {'/'.join(map(str, who[2]))}"
    return f"{job}@{c.builds[who]['name']}"


def compute(c, only=None, jobs=1, log=print, timing=None, keep=False, sets_cap=None, checkpoint=None,
            measured=None, guard=None, kinds=None):
    """Phase 0 to 2 (module doc): a `Result`, the distance basis and every attacker build. Each
    phase is a lap of `timing`, and each pooled item one of its items. `keep` (the selftest) leaves
    the groups phase 2 read on the result (`lookalikes`: {'looks': `look_specs`, 'store':
    `LookStore`}). `sets_cap` (--sets) reads only the first that many attack sets of their order and
    returns None after them: a sample of the stage's time and memory, with no result. With
    `checkpoint` (`set_dir`), the attack sets kept there are read from it and each set read is kept
    there as it ends. `measured` ({key: seconds}, `measured_seconds`) deals the long attack sets
    first (`set_order`), which moves no number. Each attack set after the first runs in a process of
    its own (`pooled` `fresh`), and `guard` (`MemoryGuard`) holds sets back and stops the one started
    last while memory runs short; neither moves a number. `kinds` (a subset of 'spell', 'skill')
    keeps only those kinds of attack: 'Glintstone Pebble' names a sorcery and an ash of war."""
    timing = timing or Timing()

    def label(item):
        return input_label(c, item)
    spell_ids, spell_out = spell_candidates(c)
    # Every spell candidate is on screen, whatever `only` keeps (`look_pool`).
    pool = look_pool(c, spell_ids)
    skill_jobs, skill_out = skill_candidates(c)
    excluded = spell_out + skill_out
    if only:
        spell_ids = [m for m in spell_ids if T._norm(c.SM.names[m]) in only]
        skill_jobs = [j for j in skill_jobs if T._norm(j['name']) in only]
        excluded = [e for e in excluded if T._norm(e['name']) in only]
    if kinds is not None:
        spell_ids = spell_ids if 'spell' in kinds else []
        skill_jobs = skill_jobs if 'skill' in kinds else []
        excluded = [e for e in excluded if e['type'] in kinds]
    bitems, usable = build_items(c, spell_ids, skill_jobs)
    items = [('spell', m, None) for m in spell_ids] + [('skill', j, None) for j in skill_jobs] + bitems
    _SIM.clear()
    _SIM['ctx'] = c
    log(f'attack similarity: {len(spell_ids)} spells and {len(skill_jobs)} skill openings to read, and '
        f'{len(bitems)} more for {len(c.builds)} attacker builds', file=sys.stderr)
    records, inputs, comps, truncated = {}, {}, {}, {}
    seen = {e['key'] for e in excluded}
    if len(seen) != len(excluded):
        raise ValueError(f'attack similarity: duplicate exclusion keys in phase 0: {_dups(e["key"] for e in excluded)}')
    builds = [{**{k: b[k] for k in ('name', 'stats', 'n', 'rl', 'reach')}, 'rule': b.get('rule'), 'usable': u, 'records': {},
               'excluded': [], 'nets': {}, **({'given': True} if b.get('given') else {})}
              for b, u in zip(c.builds, usable)]
    bseen = [set() for _ in builds]
    binputs = {}
    # Each line's spells and charged skills as phase 1 reads them there, for the groups (`LookStore`).
    store = LookStore()
    line0 = stats_key(c.stats)
    blines = [stats_key(damage_stats(b['stats'])) for b in c.builds]
    timing.lap('candidates')

    def claim(key, have=seen, who=''):
        """Every key once: a second record under a key would overwrite the first silently."""
        if key in have:
            raise ValueError(f'attack similarity: key {key} produced twice{who}')
        have.add(key)
    for bi, got in pooled(_input_work, items, jobs, 'inputs', timing, label):
        store.add(line0 if bi is None else blines[bi], got)
        for rec in got:
            slim = {k: v for k, v in rec.items() if k not in ('geo', 'value', 'value_one', 'kinds', 'aims',
                                                              'release_members')}
            if bi is not None:
                # An attacker build's entity: no profile, no component; its nets come in phase 2.
                claim(rec['key'], bseen[bi], f" for build {builds[bi]['name']}")
                if rec['status'] == 'excluded':
                    builds[bi]['excluded'].append(slim)
                    continue
                builds[bi]['records'][rec['key']] = slim
                if rec['status'] == 'scored' and rec.get('fp') and float(rec['fp']) > 0:
                    binputs.setdefault(rec['key'], {})[bi] = {**profile_inputs(rec),
                                                              'poise': line_poise(c, build=c.builds[bi]),
                                                              'max_hp': line_hp(c, build=c.builds[bi])}
                continue
            claim(rec['key'])
            if rec['status'] == 'excluded':
                excluded.append(slim)
                continue
            inputs[rec['key']] = {**profile_inputs(rec), 'max_hp': line_hp(c)}
            records[rec['key']] = {k: v for k, v in rec.items()
                                   if k not in ('geo', 'value_one', 'kinds', 'aims', 'release_members')}
            cs, cut = components(rec)
            if cut:
                truncated[rec['key']] = cut
            for x in cs:
                claim(x['key'])
                inputs[x['key']] = {k: x[k] for k in ('geo', 'value', 'value_one', 'roll')}
                comps[x['key']] = {k: v for k, v in x.items() if k not in ('geo', 'value_one')}
    records = dict(sorted(records.items()))
    comps = dict(sorted(comps.items()))
    timing.lap('inputs')
    # The objective evaluation (module doc): every entity the distance basis scores, again at its
    # objective lines (`objective_items`): each job run once per distinct line among its entities,
    # each entity kept from the run at its own line.
    olines, oitems = objective_items(c, spell_ids, skill_jobs, records)
    log(f'attack similarity: {len(oitems)} objective-line items for {len(olines)} scored entities', file=sys.stderr)
    orecs, oinputs = {}, {}
    oseen = {nm: set() for nm in OBJECTIVE_LINES}
    for who, got in pooled(_input_work, oitems, jobs, 'objective inputs', timing, label):
        nm = who[1]
        # Every entity the job scored at the line, kept or not: a group there reads them.
        store.add(who[2], got)
        for rec in got:
            ln = (olines.get(rec['key']) or {}).get(nm)
            if ln is None or line_key(ln) != who[2]:
                continue
            claim(rec['key'], oseen[nm], f' for the {nm} line')
            orecs.setdefault(rec['key'], {})[nm] = {k: rec.get(k) for k in ('status', 'reason', 'carrier', 'affinity',
                                                                              'fp', 'full')}
            if rec['status'] in ('scored', 'partial'):
                # A follow-up's objective line is scored with its delayed press too (`profile_inputs`).
                oinputs.setdefault(rec['key'], {})[nm] = {**profile_inputs(rec), 'poise': line_poise(c, ln),
                                                          'max_hp': line_hp(c, ln)}
    timing.lap('objective inputs')
    # What is on screen (`look_specs`): every profile and every net reads the key with the group the
    # defender cannot tell it from at its own line. A spell of a group that no item ran at that line
    # (one `only` left out, or one an objective line casts) is run there for the group alone.
    wants = [(k, None, line0, None) for k in records] + [(k, None, line0, x['of']) for k, x in comps.items()]
    wants += [(k, bi, blines[bi], None) for k, xs in binputs.items() for bi in xs]
    wants += [(k, nm, line_key(olines[k][nm]), None) for k, xs in oinputs.items() for nm in xs]
    litems = [('spell', m, ('lookalike', 'lookalike', line)) for m, line in look_needs(c, store, wants, pool)]
    for who, got in pooled(_input_work, litems, jobs, 'lookalike inputs', timing, label):
        store.add(who[2], got)
    looks = look_specs(c, store, wants, pool)
    for k, r in records.items():
        sp = looks.get((k, None))
        # The distance basis's group: each other member and the real frame from which the defender
        # tells it from this one (None: never).
        # A follow-up is read as its opening (`look_specs` 'me').
        me = sp['me'] if sp is not None else k
        r['lookalikes'] = None if sp is None else [
            [m, None if not math.isfinite(f) else round(float(f), 2)]
            for m in sp['keys'] if m != me for f in [sp['splits'].get((me, m), sp['splits'].get((m, me), math.inf))]]
    log(f'attack similarity: {len(looks)} of {len(wants)} profiles and nets read by a defender who cannot tell the '
        f'attack from others ({len(litems)} lookalike-only items)', file=sys.stderr)
    timing.lap('lookalike inputs')
    def cost_of(x):
        # A release scored over its release frames costs one read per frame.
        return sum(_geo_facts(u['geo'])['cost'] for u in [x] + list(x.get('members') or ()))
    cost = {k: cost_of(v) for k, v in inputs.items()}
    for k, xs in list(binputs.items()) + list(oinputs.items()):
        cost[k] = cost.get(k, 0.0) + sum(cost_of(x) for x in xs.values()) / 4.0
    order = sorted(set(inputs) | set(binputs) | set(oinputs), key=lambda k: (-cost[k], k))
    # Each key's estimated cost beside its measured seconds (`items`), so a later run can check how
    # well the estimate the counter's time left rests on predicts the time.
    timing.facts['attack_cost'] = {k: round(cost[k], 1) for k in order}
    timing.note(attack_cost=timing.facts['attack_cost'])
    _SIM['inputs'] = inputs
    _SIM['builds'] = binputs
    _SIM['objective'] = oinputs
    _SIM['looks'] = looks
    _SIM['look_store'] = store
    # Every distance the outcomes read (each situation's and its run-in gaps', as `OutcomeForm` and
    # `net_parts` round them): a homing flight asked for at one is flown with the others in one batch
    # (`er-mechanics-ashes.HOMING_DISTS`), which gives the numbers of flying each alone.
    # A sprint cast's situations (`pairs_of`) are read too.
    c.A.HOMING_DISTS = tuple(sorted({round(d, 2) for d, *_ in c.pairs + c.sprint_pairs} | {
        round(g, 2) for d, *_ in c.pairs + c.sprint_pairs for g in S.APPROACH_GAPS_M if g < d} | {
        # where the defender's run to strike ends (`er-mechanics-spells.StrikeReach.touch`)
        round(c.A.PUNISH_REACH_M, 2)}))
    arrays, checked, bad, onets = {}, 0, [], {}
    # The keys whose groups hold one another are read together, their groups' reads made once
    # (`look_batches`, `_profile_batch`); each key is timed as an item of its own.
    here = os.getpid()

    sets = set_order(look_batches(order, looks, cost), measured=measured)
    log(f'attack similarity: {len(sets)} attack sets, the cheapest first and the rest in a shuffled order, so '
        f'the time left is a projection from the random sample done', file=sys.stderr)
    if measured:
        secs = [sum(float(measured.get(k, 0.0)) for k in s) for s in sets[1:]]
        long_ = [s for s, x in zip(sets[1:], secs) if x >= LONG_SET_SHARE * sum(secs) > 0]
        if long_:
            log(f'attack similarity: {len(long_)} long sets dealt first by their measured seconds '
                f'({", ".join(s[0] for s in long_[:5])}{", ..." if len(long_) > 5 else ""})', file=sys.stderr)
    # The order the sets are handed out in: with the sets that ended, it names the ones a stopped run
    # was reading.
    timing.note(sets=sets)
    if sets_cap is not None:
        timing.facts['sets'] = [min(sets_cap, len(sets)), len(sets)]
        sets = sets[:sets_cap]
        log(f'attack similarity: --sets {sets_cap}: reading the first {len(sets)}, then stopping', file=sys.stderr)
    kept, todo = [], []
    if checkpoint is not None:
        why = open_checkpoint(checkpoint)
        if why:
            log(f'attack similarity: the sets kept in {checkpoint} were made {why}; reading none of them',
                file=sys.stderr)
    for s in sets:
        got = load_set(checkpoint, s) if checkpoint is not None else None
        if got is None:
            todo.append(s)
        else:
            kept.append(got)
    if checkpoint is not None:
        timing.facts['sets_kept'] = len(kept)
        if kept:
            log(f'attack similarity: {len(kept)} of {len(sets)} attack sets read from {checkpoint}, '
                f'{len(todo)} to read', file=sys.stderr)
    # `pooled` reads its first item in this process before it starts the workers, which wait for it,
    # so the cheapest set left goes first (`resume_order`): on a resume the first set `set_order`
    # left unread can be any of them (r18 read r17's 532 sets and handed this process Founding Rain
    # of Stars, which ran alone here for hours with no worker started, 2026-11-13).
    todo = resume_order(todo, cost)
    # The counter weighs each set by its keys' seconds measured on this code, a key with none by its
    # newest older-code seconds scaled to this code (`older_code_ratio`), a key with neither by its
    # `set_weight` term at the measured keys' median seconds per unit of it, and by `set_weight`
    # alone when no key was measured on any code (`measured_weight`).
    same = same_code_seconds(timing)
    prior = older_code_seconds(timing)
    ratio = older_code_ratio(same, prior)
    scale = seconds_per_weight(cost, known_seconds(same, prior, ratio))
    # The time left is wall-clock: at most `jobs` sets at once (`_fresh_pool`), the first alone.
    if todo:
        keys_ = [k for s in todo for k in s]
        log(f'attack similarity: {sum(k in same for k in keys_)} of {len(keys_)} keys to read measured on this '
            f'code{f" ({code_tag(timing.code)})" if timing.code is not None else ""}, '
            f'{sum(k not in same and k in prior for k in keys_)} more on older code (its seconds x{ratio:.2f}); '
            f'the counter weighs by {"their seconds" if scale is not None else "set_weight"}, '
            f'{max(1, jobs)} sets at once', file=sys.stderr)

    def each():
        for got in kept:
            yield from got
        for got in pooled(_profile_batch, todo, jobs, 'attack sets',
                          label=lambda s: set_label(s, records),
                          weight=lambda s: measured_weight(s, cost, same, scale, prior, ratio),
                          older=lambda s: older_weight(s, same, scale, prior, ratio),
                          fresh=True, guard=guard, expect=lambda s: expected_seconds(s, same),
                          source='same-code measurement',
                          older_expect=lambda s: older_expected(s, same, prior, ratio)):
            # A set whose form disagrees with `net_parts` stops the run as it finishes, before
            # it is kept (`finished_set`).
            finished_set(got, checkpoint, log)
            for key, arr, nets, errs, wall, cpu, mem in got:
                timing.item('attacks', key, wall, cpu, mem['pid'] == here, **mem)
                yield key, arr, nets, errs
    for key, arr, nets, errs in each():
        if arr is not None:
            arrays[key] = arr
            if arr.get('aims_won') and key in records:
                records[key]['aims_won'] = arr['aims_won']
        for who, nn in nets.items():
            if isinstance(who, str):
                onets.setdefault(key, {})[who] = nn
            else:
                builds[who]['nets'][key] = nn
        checked += len(errs)
        bad += [(key, e) for e in errs if e > 0]
    for k in ('inputs', 'builds', 'objective', 'looks', 'look_store'):
        _SIM.pop(k, None)
    if guard is not None:
        # How often memory held the sets back or stopped one, beside the stage's time.
        timing.facts['mem_guard'] = {'stops': guard.stops, 'waits': guard.waits}
    timing.lap('attacks')
    if bad:
        # Only a set read back from the checkpoint can still carry one: a set read here stopped the
        # run as it finished (`finished_set`).
        form_stop(bad, log)
    if sets_cap is not None:
        return None
    for k, r in records.items():
        lines = olines.get(k) or {}
        r['objective'] = {nm: objective_row(c, lines.get(nm), (orecs.get(k) or {}).get(nm), (onets.get(k) or {}).get(nm))
                          for nm in OBJECTIVE_LINES}
    for b in builds:
        b['records'] = dict(sorted(b['records'].items()))
        b['excluded'].sort(key=lambda e: e['key'])
    meta = run_meta(c, only)
    meta['form_checked'] = checked
    res = Result(meta, records, sorted(excluded, key=lambda e: e['key']), comps,
                 {k: arrays[k] for k in records}, {k: arrays[k] for k in comps}, c.w, c.F, truncated, builds)
    if keep:
        res.lookalikes = {'looks': looks, 'store': store}
    timing.lap('results')
    return res


def form_errors(got):
    """[(key, HP over `FORM_TOL`)] of one finished attack set's results ([(key, arrays, nets, form
    errors, ...)], `_profile_batch`)."""
    return [(g[0], e) for g in got for e in g[3] if e > 0]


def form_stop(bad, log=print):
    """Print `bad` ([(key, HP over `FORM_TOL`)]) and stop the run with it."""
    msg = (f'attack similarity: OutcomeForm and net_parts disagree beyond FORM_TOL {FORM_TOL} on {len(bad)} keys '
           f'(key, HP over the tolerance): {bad[:5]}')
    log(msg, file=sys.stderr)
    raise ValueError(msg)


def finished_set(got, checkpoint=None, log=print, save=None):
    """One attack set `got` as it finishes: a key whose form disagrees with `net_parts` stops the
    run there, printed at once (`form_stop`) and not kept, so the next set never starts and no
    resume reads it back; otherwise it is kept under `checkpoint` (`save_set`, or `save`). Until
    2026-11-22 the check ran after every set: the selftest's one disagreement (Sword Dance's 045800)
    stopped it after 7 hours of sets (`MEASURED`, r21 target/v34-status/selftest.log)."""
    bad = form_errors(got)
    if bad:
        form_stop(bad, log)
    if checkpoint is not None:
        (save or save_set)(checkpoint, got)


def run_meta(c, only=None):
    return {'version': SIMILARITY_VERSION, 'stats': c.stats, 'catalysts': c.catalysts, 'rl': c.rl,
            'include_blacklisted': c.include_blacklisted, 'situations': len(c.pairs), 'W': c.W,
            'n_eff': c.n_eff, 'F': c.F, 'only': sorted(only) if only else None,
            'builds': [{k: b[k] for k in ('name', 'stats', 'n', 'rl')} for b in c.builds]}


# ---------------------------------------------------------------------------------------------
# the cache

def _ours(m):
    f = getattr(m, '__file__', None)
    return bool(f) and Path(f).resolve().parent == HERE


def code_stamp():
    """{file name: sha256} of every module of this directory the process has loaded (the
    weapon-embed form), including the ones a sibling loaded without registering them in
    `sys.modules` (`er-mechanics-ashes._load`): reached through module attributes and the lists and
    dicts that hold them (`_POSE`, `_MODS`)."""
    import types
    seen, todo = {}, [m for m in list(sys.modules.values()) if _ours(m)]
    while todo:
        m = todo.pop()
        if id(m) in seen or not _ours(m):
            continue
        seen[id(m)] = m
        for v in list(vars(m).values()):
            if isinstance(v, types.ModuleType):
                todo.append(v)
            elif isinstance(v, (list, tuple)) and len(v) < 64:
                todo += [x for x in v if isinstance(x, types.ModuleType)]
            elif isinstance(v, dict) and len(v) < 64:
                todo += [x for x in v.values() if isinstance(x, types.ModuleType)]
    out = {}
    for m in seen.values():
        p = Path(m.__file__)
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def code_tag(code):
    """A short name of a `code_stamp` (its first 12 hex digits of sha256 over the sorted JSON), which
    each trail row carries (`Timing.note`): two rows with one tag were measured on the same code."""
    return hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()[:12]


def members_stamp():
    """The opponent members cache the pool's run-ins are read from (`er-mechanics-interrupt.load_cache`,
    through `er-mechanics-ash-interrupt`): the sources stamp it was built under, the one its sources
    give now, and the sha256 of its bytes; None when there is none. Its front-contact frames move the
    cached numbers, and no loaded script's code holds them: the 2026-11-15 full run read a cache built
    on 2026-11-12, and rebuilt on that run's code 557 of its 5,919 members reached front contact 0.4
    to 10 frames earlier and 15 more had one (`MEASURED` 2026-11-16)."""
    intr = _ai().INTR
    path = intr.seq_cache_path()
    if not path.exists():
        return None
    raw = path.read_bytes()
    return {'built': json.loads(raw).get('stamp'), 'sources': intr._source_stamp(),
            'sha256': hashlib.sha256(raw).hexdigest()}


def require_fresh_members(got=None):
    """`members_stamp`, or an exit naming the rebuild when the cache is missing or was built from
    other sources: a run on it would compute for hours on run-ins the current code does not give."""
    got = members_stamp() if got is None else got
    if got is None or got['built'] != got['sources']:
        raise SystemExit('attack similarity: the opponent members cache '
                         f"{'is missing' if got is None else 'was built from other sources'}; rebuild it first: "
                         'python3 scripts/er-mechanics-interrupt.py --build-cache --jobs 3')
    return got


def stamp(stats, catalysts, rl, include_blacklisted, situations, strikes, defender, code=None,
          version=SIMILARITY_VERSION, paramdefs=None, hks=None, builds=(), members=None):
    """(first 16 hex digits of the sha256, its inputs) over what the cached numbers depend on: the
    distance basis build, the attacker builds (name, attribute line, count), the situations, the
    regulation file, the paramdef XMLs that decide every field offset read from it
    (`er-param-read.PARAMDEF_DIR`), the blacklist, every loaded script, the HKS inputs that
    pick every skill's opening clip (`hks_stamp`; None leaves them out, for a caller with no ash
    model), and the opponent members cache (`members_stamp`)."""
    reg = Path(PR.RP.DEFAULT_REGULATION)
    st = reg.stat() if reg.exists() else None
    black = Path(S.ASH_BLACKLIST)
    inputs = {'version': version, 'stats': dict(sorted(stats.items())), 'catalysts': list(catalysts), 'rl': rl,
              # A `--line` spread's armor too (`given_builds`), which the made lines' code holds.
              'builds': [[b['name'], [b['stats'][k] for k in ATTRS], b['n']]
                         + ([['given', b['rule']['end']['poise_floor']]] if b.get('given') else []) for b in builds],
              'include_blacklisted': bool(include_blacklisted), 'situations': situations,
              'strikes': [float(x) for x in strikes], 'defender': defender,
              'regulation': [str(reg), st.st_size if st else None, st.st_mtime_ns if st else None],
              'paramdefs': paramdef_stamp() if paramdefs is None else paramdefs,
              'blacklist': hashlib.sha256(black.read_bytes()).hexdigest() if black.exists() else None,
              'code': code_stamp() if code is None else code, 'hks': hks, 'members': members}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16], inputs


#: The HKS parser `er-mechanics-ashes.sword_arts_categories` loads into a local variable, which
#: `code_stamp`'s walk over module attributes never reaches.
HKS_PARSER = HERE / 'er-hks-weapon-category-table.py'


def hks_stamp(A):
    """{'parser': sha256 of `HKS_PARSER`, 'common_define': [path, size, mtime_ns] of
    `A.COMMON_DEFINE_HKS`}: the two inputs of the `SwordArtsCategory` table, which picks the opening
    clip of every skill on every weapon (`weapon_diff_category`, `ONE_SHOT_VARIANT`) and keys the
    opening buckets. A missing file stamps as None (its table is then empty)."""
    hk = Path(A.COMMON_DEFINE_HKS)
    st = hk.stat() if hk.exists() else None
    return {'parser': hashlib.sha256(HKS_PARSER.read_bytes()).hexdigest() if HKS_PARSER.exists() else None,
            'common_define': [str(hk), st.st_size if st else None, st.st_mtime_ns if st else None]}


def paramdef_stamp():
    """{file name: [size, mtime_ns]} of the paramdef XMLs `er-param-read` reads."""
    root = Path(PR.PARAMDEF_DIR)
    return {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(root.glob('*.xml'))} if root.is_dir() else {}


def stamp_diff(old, new):
    """[why] two stamp inputs differ: the top-level keys, and under 'code', 'paramdefs' and 'hks'
    the entries that differ."""
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict) and k in ('code', 'paramdefs', 'hks'):
            files = sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f))
            out.append(f"{k}: {', '.join(files[:12])}{' and %d more' % (len(files) - 12) if len(files) > 12 else ''}")
        else:
            out.append(k)
    return out


def cache_paths(rl, h16, root=None):
    stem = f'attack-similarity-{rl - S.WINDOW}-{rl + S.WINDOW}-{h16}'
    root = Path(root) if root is not None else EX.CACHE
    return root / f'{stem}.json', root / f'{stem}.npz'


def set_dir(rl, h16, root=None):
    """Where a run of stamp `h16` keeps each attack set's results as the set ends (`save_set`), so a
    run stopped part way resumes from them (r12 was stopped at 578 of 944 sets and kept none): the
    cache's name with `.sets`, emptied once the cache is written."""
    return cache_paths(rl, h16, root)[0].with_suffix('.sets')


def checkpoint_versions():
    """{'python', 'numpy'}: what a kept set's bits depend on that the stamp does not hold. CPython
    sums plain floats compensated since 3.12 (`_cap_sum` keys its memo by the type for that), and a
    rolling system can update either during a run that takes a day."""
    import platform
    return {'python': platform.python_version(), 'numpy': np.__version__}


def open_checkpoint(root, versions=None):
    """Make `root` ready to keep and read attack sets: delete the temporary files of writers no longer
    running (a run stopped while `save_set` wrote), and when the sets in it were made under other
    `checkpoint_versions` (its `versions.json`), delete them. Returns why they were deleted, or ''.
    A directory with sets and no `versions.json` was made before the file was; its sets are kept."""
    root = Path(root)
    versions = checkpoint_versions() if versions is None else versions
    root.mkdir(parents=True, exist_ok=True)
    for p in root.glob('.*.tmp.npz'):
        pid = p.name.rsplit('.tmp.npz', 1)[0].rsplit('.', 1)[-1]
        if not (pid.isdigit() and Path(f'/proc/{pid}').exists()):
            p.unlink(missing_ok=True)
    vf = root / 'versions.json'
    try:
        old = json.loads(vf.read_text())
    except (OSError, ValueError):
        old = None
    why = ''
    if old is not None and old != versions:
        why = f'under {old}, not {versions}'
        for p in root.glob('*.npz'):
            if not p.name.startswith('.'):
                p.unlink(missing_ok=True)
    if old != versions:
        tmp = vf.with_name(f'.versions.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(versions))
        os.replace(tmp, vf)
    return why


def _pack(x, arrs):
    """`x` as JSON data, each numpy array and scalar moved into `arrs` as it is (its dtype, its
    bits); dicts keep their keys' types, tuples stay tuples, floats are kept as hex. `_unpack`
    gives `x` back."""
    if isinstance(x, (np.ndarray, np.generic)):
        a = np.asarray(x)
        if a.dtype.hasobject:
            raise TypeError(f'attack similarity: an object array cannot be kept without pickle ({a.dtype})')
        arrs[f'a{len(arrs)}'] = a
        return {'np' if isinstance(x, np.generic) else 'nd': f'a{len(arrs) - 1}'}
    if isinstance(x, dict):
        return {'dict': [[_pack(k, arrs), _pack(v, arrs)] for k, v in x.items()]}
    if isinstance(x, tuple):
        return {'tuple': [_pack(v, arrs) for v in x]}
    if isinstance(x, list):
        return [_pack(v, arrs) for v in x]
    if isinstance(x, float):
        return {'float': x.hex()}
    if x is None or isinstance(x, (bool, int, str)):
        return x
    raise TypeError(f'attack similarity: no way to keep a {type(x).__name__}')


def _unpack(x, z):
    if isinstance(x, list):
        return [_unpack(v, z) for v in x]
    if not isinstance(x, dict):
        return x
    (tag, v), = x.items()
    if tag == 'nd':
        return z[v]
    if tag == 'np':
        return z[v][()]
    if tag == 'dict':
        return {_unpack(k, z): _unpack(w, z) for k, w in v}
    if tag == 'tuple':
        return tuple(_unpack(w, z) for w in v)
    return float.fromhex(v)


def set_file(root, keys):
    """The file of the attack set of `keys` (in their order) under `root`."""
    return Path(root) / f"{hashlib.blake2b(chr(10).join(keys).encode(), digest_size=10).hexdigest()}.npz"


def save_set(root, got):
    """Keep one attack set's [(key, arrays, nets, form errors)] under `root`, written to a temporary
    name, synced and moved into place, so a run stopped while writing, or a machine that loses power
    after, leaves no file that reads part of a set. An empty set (`got` []) keeps nothing."""
    if not got:
        return
    arrs = {}
    keys = [g[0] for g in got]
    doc = json.dumps({'keys': keys, 'got': _pack([tuple(g[:4]) for g in got], arrs)})
    p = set_file(root, keys)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f'.{p.stem}.{os.getpid()}.tmp.npz')
    try:
        with open(tmp, 'wb') as f:
            np.savez(f, __doc__=np.array(doc), **arrs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_set(root, keys):
    """The kept results of the attack set of `keys`, or None when there are none or they cannot be
    read (an empty file, which `np.load` meets with EOFError, among them)."""
    p = set_file(root, keys)
    if not p.exists():
        return None
    try:
        with np.load(p, allow_pickle=False) as z:
            doc = json.loads(str(z['__doc__']))
            return _unpack(doc['got'], z) if doc['keys'] == list(keys) else None
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None


def miss_reason(rl, h16, inputs, root=None):
    """Why no cache matches `h16`: the differences from the newest cache of the same RL window, or
    that there is none."""
    jp, _ = cache_paths(rl, h16, root)
    olds = sorted(jp.parent.glob(f'attack-similarity-{rl - S.WINDOW}-{rl + S.WINDOW}-*.json'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    olds = [p for p in olds if p != jp]
    if not olds:
        return f'no cache for RL {rl - S.WINDOW}-{rl + S.WINDOW} yet'
    try:
        old = json.loads(olds[0].read_text()).get('stamp_inputs')
    except (OSError, ValueError) as e:
        return f'{olds[0].name} unreadable ({e!r})'
    if not old:
        return f'{olds[0].name} carries no stamp inputs (made before they were stored)'
    return f"{olds[0].name} differs in {'; '.join(stamp_diff(old, inputs)) or 'nothing it recorded'}"


def _jsonable_value(v):
    return [[float(f), float(x)] for f, x in sorted(v.items())]


def save_cache(res, h16, rl, doc_extra, root=None, inputs=None):
    """The JSON and the `.npz`, each written to a temporary name in the cache directory and moved
    into place, the `.npz` first: a run killed while saving leaves no JSON that matches."""
    jp, npz = cache_paths(rl, h16, root)
    jp.parent.mkdir(parents=True, exist_ok=True)
    recs = {k: {**r, 'value': _jsonable_value(r['value'])} for k, r in res.records.items()}
    comps = {k: {**r, 'value': _jsonable_value(r['value'])} for k, r in res.comps.items()}
    mats = doc_extra.get('_matrices', {})
    builds = [{k: v for k, v in b.items() if k != 'nets'} for b in res.builds]
    doc = {'stamp': h16, 'stamp_inputs': inputs, 'meta': res.meta, 'entities': recs, 'components': comps,
           'excluded': res.excluded, 'truncated': res.truncated, 'builds': builds,
           **{k: v for k, v in doc_extra.items() if k != '_matrices'}}
    arrs = {f'e_{n}': res.A[n] for n in ARRAYS}
    arrs.update({f'c_{n}': res.C[n] for n in ARRAYS})
    # Each attacker build's nets, row by row in `b<i>_keys` order.
    for i, b in enumerate(res.builds):
        ks = sorted(b['nets'])
        arrs[f'b{i}_keys'] = np.array(ks, dtype=str)
        arrs[f'b{i}_net'] = np.stack([b['nets'][k][0] for k in ks]) if ks else np.zeros((0, len(res.w)), np.float32)
        arrs[f'b{i}_net_one'] = np.stack([b['nets'][k][1] for k in ks]) if ks else np.zeros((0, len(res.w)), np.float32)
    arrs['keys'] = np.array(res.keys)
    arrs['comp_keys'] = np.array(res.comp_keys)
    arrs['w'] = res.w
    # The matrices over every profiled whole entity (`keys` order), and the rows of each kind and of
    # the partial entities: the spell x spell block is D[idx_spell][:, idx_spell], and so on.
    for n in MATRICES:
        if n in mats:
            arrs[n] = mats[n]
    arrs['idx_spell'] = np.array([i for i, k in enumerate(res.keys) if res.records[k]['type'] == 'spell'], int)
    arrs['idx_skill'] = np.array([i for i, k in enumerate(res.keys) if res.records[k]['type'] == 'skill'], int)
    arrs['idx_partial'] = np.array([i for i, k in enumerate(res.keys) if res.records[k]['status'] == 'partial'], int)
    tmp_npz = npz.with_name(f'.{npz.stem}.{os.getpid()}.tmp.npz')
    tmp_json = jp.with_name(f'.{jp.name}.{os.getpid()}.tmp')
    try:
        np.savez_compressed(tmp_npz, **arrs)
        tmp_json.write_text(json.dumps(doc, default=_json_default))
        os.replace(tmp_npz, npz)
        os.replace(tmp_json, jp)
    finally:
        for p in (tmp_npz, tmp_json):
            if p.exists():
                p.unlink()


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def load_cache(rl, h16, F, root=None, log=None):
    """The cached `Result` of stamp `h16`, or None when there is none or it cannot be read (a
    truncated `.npz` or JSON is a miss, said through `log`, never a crash)."""
    import zipfile
    jp, npz = cache_paths(rl, h16, root)
    if not (jp.exists() and npz.exists()):
        return None
    try:
        doc = json.loads(jp.read_text())
        if doc.get('stamp') != h16:
            return None
        with np.load(npz, allow_pickle=False) as z:
            # Each `z[name]` decompresses the whole array again: read every one once.
            keys, ckeys = [str(k) for k in z['keys']], [str(k) for k in z['comp_keys']]
            E = {n: z[f'e_{n}'] for n in ARRAYS}
            C = {n: z[f'c_{n}'] for n in ARRAYS}
            w = z['w']
            builds = []
            for i, b in enumerate(doc['builds']):
                bk, bn, b1 = [str(k) for k in z[f'b{i}_keys']], z[f'b{i}_net'], z[f'b{i}_net_one']
                builds.append({**b, 'nets': {k: (bn[j], b1[j]) for j, k in enumerate(bk)}})
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as e:
        if log:
            log(f'attack similarity: cache {jp.stem} unreadable ({e!r}): a miss', file=sys.stderr)
        return None
    recs = {k: {**r, 'value': {f: v for f, v in r['value']}} for k, r in doc['entities'].items()}
    comps = {k: {**r, 'value': {f: v for f, v in r['value']}} for k, r in doc['components'].items()}
    arrays = {k: {n: E[n][i] for n in ARRAYS} for i, k in enumerate(keys)}
    carrays = {k: {n: C[n][i] for n in ARRAYS} for i, k in enumerate(ckeys)}
    return Result(doc['meta'], recs, doc['excluded'], comps, arrays, carrays, w, F, doc.get('truncated'), builds)


# ---------------------------------------------------------------------------------------------
# distances

def d_now_rows(LA, PA, LB, PB, w, W):
    """D_now (or D_best on the best-option arrays) between every row of A and every row of B."""
    out = np.empty((len(LA), len(LB)))
    LA, PA, LB, PB = (np.asarray(x, float) for x in (LA, PA, LB, PB))
    for i in range(len(LA)):
        out[i] = (np.abs(LB - LA[i]) @ w + np.abs(PB - PA[i]) @ w) / (2.0 * W)
    return out


def d_idle_rows(BA, TA, BB, TB, w, W, F):
    """D_idle between every row of A (base share, first touch) and every row of B."""
    out = np.empty((len(BA), len(BB)))
    BA, TA, BB, TB = (np.asarray(x, float) for x in (BA, TA, BB, TB))
    finB = np.isfinite(TB)
    TB0 = np.where(finB, TB, 0.0)
    for i in range(len(BA)):
        fa = np.isfinite(TA[i])
        ta = np.where(fa, TA[i], 0.0)
        both = finB & fa
        one = finB ^ fa
        dt = np.where(both, np.abs(TB0 - ta), 0.0)
        cs = np.where(both, dt / (dt + F), np.where(one, 1.0, 0.0))
        out[i] = (0.5 * np.abs(BB - BA[i]) + 0.5 * cs) @ w / W
    return out


def rel_profile(arr, idx=None, channels=REL_CHANNELS):
    """The D_rel profile rows: the `channels` arrays side by side (n x len(channels) situations);
    `LAND_CHANNELS` gives the D_land and D_contain rows."""
    parts = [np.asarray(arr[c], float) for c in channels]
    if idx is not None:
        parts = [p[idx] for p in parts]
    return np.hstack(parts) if parts[0].ndim == 2 else np.concatenate(parts)


def assoc(d_rel, d_land, d_idle, rate):
    """D_assoc (module doc): max(D_rel, D_land, rate x D_idle), elementwise."""
    return np.maximum(np.maximum(d_rel, d_land), rate * np.asarray(d_idle, float))


def d_contain_rows(LA, BA, TA, LB, BB, TB, w, W, F, rate):
    """D_contain between every row of A and every row of B: max(D_rel over the land rows `L`,
    rate x D_idle over the base shares `B` and first touches `T`)."""
    return np.maximum(d_rel_rows(LA, LB, w), rate * d_idle_rows(BA, TA, BB, TB, w, W, F))


def d_rel_rows(UA, UB, w):
    """D_rel between every row of A and every row of B (`rel_profile` rows): sum |u_a - u_b| /
    sum max(u_a, u_b) with each cell weighted by its situation's w; 0 between two empty profiles."""
    UA, UB = np.atleast_2d(np.asarray(UA, float)), np.atleast_2d(np.asarray(UB, float))
    ww = np.tile(np.asarray(w, float), UA.shape[1] // len(w))
    out = np.empty((len(UA), len(UB)))
    for i in range(len(UA)):
        num = np.abs(UB - UA[i]) @ ww
        den = np.maximum(UB, UA[i]) @ ww
        out[i] = np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)
    return out


def overlap(ua, ub, w):
    """The weighted mean of min(u_a, u_b) over the D_rel cells: the outcome the two profiles share,
    on the mass scale (`shares` 'mass')."""
    ua, ub = np.asarray(ua, float), np.asarray(ub, float)
    ww = np.tile(np.asarray(w, float), len(ua) // len(w))
    return float(np.minimum(ua, ub) @ ww / ww.sum())


def _sym(D):
    D = (D + D.T) / 2.0
    np.fill_diagonal(D, 0.0)
    return np.clip(D, 0.0, 1.0).astype(np.float32)


def base_share(arr, full):
    return arr['base_now'].astype(float) / np.maximum(np.asarray(full, float), 1e-9)[:, None]


#: The matrices, in the order the cache and the page give them.
MATRICES = ('D_assoc', 'D_rel', 'D_land', 'D_now', 'D_best', 'D_idle')


def exact_full(recs, keys):
    """The exact worth `profile_arrays` divides the land shares by; the record's `full` is rounded
    to 0.1 HP, which put a 21.33 HP Kick's base share at 1.0014. `worth` is that sum with a hit
    priority record's hits counted once (`er-mechanics-spells.full_worth`, version 31); a record
    without it (a cache from before) reads the plain sum."""
    return [float(recs[k]['worth']) if recs[k].get('worth') is not None else float(sum(recs[k]['value'].values()))
            for k in keys]


def raw_matrices(res):
    """{matrix: float64 as computed, before `_sym`}: every row against every row, over every
    profiled whole entity (scored and partial, `res.keys` order). D_assoc is built from these by
    `matrices`, at the `--idle-rate` of the run."""
    A, w, W = res.A, res.w, res.W
    B = base_share(A, exact_full(res.records, res.keys))
    U = rel_profile(A)
    UL = rel_profile(A, channels=LAND_CHANNELS)
    return {'D_rel': d_rel_rows(U, U, w), 'D_land': d_rel_rows(UL, UL, w),
            'D_now': d_now_rows(A['land_now'], A['pun_now'], A['land_now'], A['pun_now'], w, W),
            'D_best': d_now_rows(A['land'], A['pun'], A['land'], A['pun'], w, W),
            'D_idle': d_idle_rows(B, A['t_first'], B, A['t_first'], w, W, res.F)}


def matrices(res, rate=IDLE_RATE):
    """{`MATRICES`}: float32, symmetric, zero diagonal (`raw_matrices`, which are symmetric as
    computed: the selftest checks the raw ones), and D_assoc from the stored three at `rate`."""
    out = {k: _sym(v) for k, v in raw_matrices(res).items()}
    out['D_assoc'] = _sym(assoc(out['D_rel'].astype(float), out['D_land'].astype(float),
                                out['D_idle'].astype(float), rate))
    return {k: out[k] for k in MATRICES}


# ---------------------------------------------------------------------------------------------
# groups, families

#: The flags `group_of` gives: a live attack that nets at most `--hp` on average, and an attack
#: that is not live and loses more than `--hp` per use on average.
PUNISHED_LIVE = 'punished as much as it lands'
PUNISHED_NOT_LIVE = 'lands little, punished more'


def group_of(r, null, hp):
    """(group, sub-reason, flags) of one profiled entity, by its land masses (module doc
    'Groups'): what it lands, never how often it is punished. The flags read the mean net: a live
    attack netting at most `hp` is `PUNISHED_LIVE`; one that is not live and nets under -`hp`
    (the Zamor Ice Storm casts, Flame Spit) is `PUNISHED_NOT_LIVE`. Both read the distance basis's
    nets."""
    flags = []
    if r['land_mass_now'] >= null:
        if r['mean_net'] <= hp:
            flags.append(PUNISHED_LIVE)
        return 'live', None, flags
    if r['mean_net'] < -hp:
        flags.append(PUNISHED_NOT_LIVE)
    if r['land_mass_best'] >= null:
        return 'runin_only', None, flags
    return 'nets_nothing', ('never reaches' if r['reach'] < null else 'always evaded'), flags


def twin_pairs(D, keys, ok, full, parent, t_max, band):
    """[(key a, key b)] of the twins: both `ok`, not siblings, D at most `t_max`, full worth within a
    factor 2^`band`."""
    out = []
    n = len(keys)
    for i in range(n):
        if not ok[i]:
            continue
        for j in range(i + 1, n):
            if (ok[j] and parent[i] != parent[j] and D[i, j] <= t_max
                    and abs(math.log2(full[i] / full[j])) <= band):
                out.append((keys[i], keys[j]))
    return out


def mutual_nearest(D, keys, ok, typ, t_max):
    """[(i, j)] index pairs, i < j, of `ok` items of different kinds each the other's nearest of the
    other kind by D, within `t_max`; ties go to the lower key."""
    idx = [i for i in range(len(keys)) if ok[i]]
    near = {}
    for i in idx:
        other = [j for j in idx if typ[j] != typ[i]]
        if other:
            near[i] = min(other, key=lambda j: (D[i, j], keys[j]))
    return sorted({(min(i, j), max(i, j)) for i, j in near.items() if near.get(j) == i and D[i, j] <= t_max},
                  key=lambda p: (D[p[0], p[1]], keys[p[0]]))


def upgma(D):
    """[(cluster a, cluster b, height, size)]: average linkage over the square matrix `D`
    (Lance-Williams). Leaves are 0..n-1 and merge k makes cluster n + k."""
    n = len(D)
    if n < 2:
        return []
    M = np.array(D, float)
    np.fill_diagonal(M, np.inf)
    ids, size = list(range(n)), [1] * n
    active = np.ones(n, bool)
    merges = []
    for k in range(n - 1):
        sub = np.where(active[:, None] & active[None, :], M, np.inf)
        i, j = np.unravel_index(int(np.argmin(sub)), sub.shape)
        i, j = min(i, j), max(i, j)
        h = float(M[i, j])
        merges.append((ids[i], ids[j], h, size[i] + size[j]))
        row = (size[i] * M[i] + size[j] * M[j]) / (size[i] + size[j])
        M[i, :] = row
        M[:, i] = row
        M[i, i] = np.inf
        M[j, :] = np.inf
        M[:, j] = np.inf
        active[j] = False
        size[i] += size[j]
        ids[i] = n + k
    return merges


def cut_tree(merges, n, cut):
    """[[leaf]] clusters of the merges at height at most `cut`."""
    members = {i: [i] for i in range(n)}
    for k, (a, b, h, _) in enumerate(merges):
        if h > cut:
            break
        members[n + k] = members.pop(a) + members.pop(b)
    return sorted((sorted(v) for v in members.values()), key=lambda v: (-len(v), v))


def leaf_order(merges, n):
    """Leaves in dendrogram order."""
    if n == 0:
        return []
    if not merges:
        return list(range(n))
    kids = {n + k: (a, b) for k, (a, b, _, _) in enumerate(merges)}
    roots = set(range(n)) | set(kids)
    for a, b in kids.values():
        roots -= {a, b}
    out = []
    for r in sorted(roots):
        todo = [r]
        while todo:
            x = todo.pop()
            if x < n:
                out.append(x)
            else:
                a, b = kids[x]
                todo += [b, a]
    return out


def ari(a, b):
    """Adjusted Rand index of two labellings of the same items."""
    n = len(a)
    if n < 2:
        return None

    def c2(x):
        return x * (x - 1) / 2.0
    sij = sum(c2(v) for v in Counter(zip(a, b)).values())
    sa = sum(c2(v) for v in Counter(a).values())
    sb = sum(c2(v) for v in Counter(b).values())
    exp = sa * sb / c2(n)
    mx = (sa + sb) / 2.0
    return 1.0 if mx == exp else (sij - exp) / (mx - exp)


# ---------------------------------------------------------------------------------------------
# dominance

def dom_rows(Nb, N, fp_b, fp, slots_b, slots, spell_b, spell, w, W, hp, delta):
    """For one b against every row a of `N`: (dominates, loss share, mean gain, equivalent). The
    test (module doc, section 8 of the doc): (i) FP and, spell over spell, slots no higher;
    (iii) loss share at most `delta`, and a mean gain of at least -`hp`, so a cheaper a may not
    trade a large loss on a few situations for its FP; (iv) strictly better. (ii), that both net
    something, is the caller's.

    A spell strictly better on slots alone must also have a mean gain of at least 0. The mean gain
    is additive around a cycle (it is a mean of differences), and FP cannot fall around one, so a
    cycle needs edges of negative gain; with only gain-strict (gain above `hp`), FP-strict (never
    on a cycle) and these, none is left: slot steps each up to `hp` worse, closed through a
    cross-kind edge, made one (`MEASURED`, round 3, synthetic)."""
    N = N.astype(float)
    Nb = Nb.astype(float)
    loss = (N < Nb - hp) @ w / W
    back = (Nb < N - hp) @ w / W
    gain = (N - Nb) @ w / W
    both_spell = spell & spell_b
    cheap = (fp <= fp_b) & (~both_spell | (slots <= slots_b))
    strict = (gain > hp) | (fp < fp_b) | (both_spell & (slots < slots_b) & (gain >= 0))
    dom = cheap & (loss <= delta) & (gain >= -hp) & strict
    equiv = (fp == fp_b) & (~both_spell | (slots == slots_b)) & (loss <= delta) & (back <= delta) & (np.abs(gain) <= hp)
    return dom, loss, gain, equiv


def full_count(r):
    """Whether entity `r`'s worth is its every tick over a long life (live longer than
    `LONG_LIVE_FRAMES`, or channelled): the 'full count' warning, and a dominator that is never
    robust."""
    return r.get('live_frames', 0) > LONG_LIVE_FRAMES or bool(r.get('channel'))


def nets_something(N, w, W, hp, delta):
    """Per row of `N`: the weight share where it nets more than `hp` is above `delta`. A row that
    passes only on less weight than a dominator may lose on is on the 'nets nothing' list: it is
    never dominated and dominates nothing."""
    return (np.asarray(N, float) > hp) @ w / W > delta


def dominance(res, keys, hp, delta, twins, sibling_only=False):
    """Every edge among `keys` (scored, fp > 0): {'edges', 'equivalent', 'nets_nothing'}. Siblings
    are left out, or, with `sibling_only`, only siblings are compared (the own-variant table)."""
    if not keys:
        return {'edges': [], 'equivalent': [], 'nets_nothing': []}
    R = res.records
    idx = np.array([res.index[k] for k in keys])
    N, N1 = res.A['net'][idx], res.A['net_one'][idx]
    w, W = res.w, res.W
    fp = np.array([float(R[k]['fp']) for k in keys])
    slots = np.array([float(R[k].get('slots') or 0) for k in keys])
    spell = np.array([R[k]['type'] == 'spell' for k in keys])
    lower = np.array([bool(R[k].get('fp_lower_bound')) for k in keys])
    whole = np.array([full_count(R[k]) for k in keys])
    parent = np.array([R[k]['parent'] for k in keys])
    some = nets_something(N, w, W, hp, delta)
    some1 = nets_something(N1, w, W, hp, delta)
    edges, equiv = [], []
    for j, kb in enumerate(keys):
        if not some[j]:
            continue
        dom, loss, gain, eq = dom_rows(N[j], N, fp[j], fp, slots[j], slots, spell[j], spell, w, W, hp, delta)
        dom1, _, _, _ = dom_rows(N1[j], N1, fp[j], fp, slots[j], slots, spell[j], spell, w, W, hp, delta)
        same = parent == parent[j]
        mask = (same if sibling_only else ~same) & some
        mask[j] = False
        for i in np.nonzero(dom & mask)[0]:
            ka = keys[i]
            # Why the edge is not robust: one hit per event does not discount a lingering area
            # (`_bursts` credits the first catch with every later frame), so a dominator counted
            # whole over a long life is full_only whatever its one-hit test says.
            why = [x for x, bad in (('one hit per event', not (dom1[i] and some1[j] and some1[i])),
                                    ('FP lower bound', lower[i]), ('full count', whole[i])) if bad]
            robust = not why
            a, b = R[ka], R[kb]
            blind = sorted((set(b['tokens']) | set(b['flags'])) - (set(a['tokens']) | set(a['flags'])))
            cross = a['type'] != b['type']
            needs = (f"needs {a['carrier']} ({a.get('affinity') or 'Standard'})" if a['type'] == 'skill'
                     else 'needs a memory slot') if cross else None
            edges.append({'a': ka, 'b': kb, 'loss': round(float(loss[i]), 5), 'gain': round(float(gain[i]), 2),
                          'class': 'robust' if robust else 'full_only', 'full_only_why': why,
                          'strict': bool(loss[i] == 0.0),
                          'blind': blind, 'cross': cross, 'needs': needs,
                          'twin': (ka, kb) in twins or (kb, ka) in twins})
        for i in np.nonzero(eq & mask)[0]:
            if i < j and some[i]:
                equiv.append({'a': keys[i], 'b': kb, 'gain': round(float(gain[i]), 2)})
    return {'edges': edges, 'equivalent': equiv, 'nets_nothing': [k for j, k in enumerate(keys) if not some[j]]}


def dominance_report(res, pop, dom, own=None):
    """Redundant lists, both frontiers, maximal dominators. A dominator of b is maximal when no
    other dominator of b dominates it, own-variant edges (`own`) included: a tap is not maximal
    beside the charged cast that dominates it. The robust dominators are maximal among themselves
    by the robust edges alone and listed first: over every edge a full_only dominator could hide
    each robust one, so b would show only full_only dominators although a robust one exists
    (Eochaid's Dancing Blade, Bayle's Tyranny and 12 more, round 4)."""
    R = res.records
    by_b, by_pair, by_pair_r = {}, {}, set()
    for e in dom['edges']:
        by_b.setdefault(e['b'], []).append(e)
        by_pair[(e['a'], e['b'])] = e
        if e['class'] == 'robust':
            by_pair_r.add((e['a'], e['b']))
    for e in (own or {}).get('edges', []):
        by_pair.setdefault((e['a'], e['b']), e)
        if e['class'] == 'robust':
            by_pair_r.add((e['a'], e['b']))
    redundant = []
    for b, es in by_b.items():
        doms = {e['a'] for e in es}
        rob = [e for e in es if e['class'] == 'robust']
        rdoms = {e['a'] for e in rob}
        top_r = sorted((e for e in rob if not any((o, e['a']) in by_pair_r for o in rdoms if o != e['a'])),
                       key=lambda e: -e['gain'])
        top_f = sorted((e for e in es if e['class'] != 'robust'
                        and not any((o, e['a']) in by_pair for o in doms if o != e['a'])), key=lambda e: -e['gain'])
        maximal = top_r + top_f
        robust = bool(rob)
        twin = sorted((e for e in es if e['twin']), key=lambda e: (e['class'] != 'robust', -e['gain']))
        redundant.append({'b': b, 'class': 'robust' if robust else 'full_only', 'dominators': len(es),
                          'robust_dominators': len(rob), 'maximal_robust': len(top_r),
                          'maximal': maximal[:3], 'twin_dominators': twin[:3], 'by_twin': bool(twin),
                          'twin_cross': any(e['cross'] for e in twin),
                          'twin_same': any(not e['cross'] for e in twin)})
    redundant.sort(key=lambda r: (r['class'] != 'robust', not r['by_twin'], R[r['b']]['name']))
    passing = [k for k in pop if k not in set(dom['nets_nothing'])]
    dominated_any = set(by_b)
    dominated_robust = {e['b'] for e in dom['edges'] if e['class'] == 'robust'}

    def warn(k):
        r = R[k]
        out = []
        if full_count(r):
            out.append('full count')
        if r.get('fp_lower_bound'):
            out.append('FP lower bound (loop)' if r['type'] == 'spell' else 'FP lower bound (follow-up clip)')
        return out
    frontier = [{'key': k, 'warnings': warn(k)} for k in passing if k not in dominated_any]
    frontier_robust = [{'key': k, 'warnings': warn(k)} for k in passing if k not in dominated_robust]
    return {'redundant': redundant, 'frontier': frontier, 'frontier_robust': frontier_robust,
            'cycle': find_cycle(dom['edges'] + list((own or {}).get('edges', [])))}


def find_cycle(edges):
    """One cycle of the edges (a dominates b) as [key, ..., key], or None: `dom_rows` admits none
    (its docstring), and this is the check on the edges a run made."""
    nxt = {}
    for e in edges:
        nxt.setdefault(e['a'], []).append(e['b'])
    state = {}
    for root in sorted(nxt):
        if state.get(root):
            continue
        stack, path = [(root, iter(sorted(nxt.get(root, ()))))], [root]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            child = next(it, None)
            if child is None:
                state[node] = 2
                stack.pop()
                path.pop()
            elif state.get(child) == 1:
                return path[path.index(child):] + [child]
            elif not state.get(child):
                state[child] = 1
                stack.append((child, iter(sorted(nxt.get(child, ())))))
                path.append(child)
    return None


# ---------------------------------------------------------------------------------------------
# associations

#: Delivery kinds as bits, for the gate: a melee-only attack and a bullet-only one never associate.
DELIVERY_BITS = {'melee': 1, 'bullet': 2}


def delivery_bits(rec):
    """The bits of a record's `delivery` (every kind when it carries none)."""
    return sum(DELIVERY_BITS[k] for k in set(rec.get('delivery') or DELIVERY_BITS))


#: Effects that move or hold the target (`speffect_tokens`' 'pull' / 'push' / 'gravity', the 'grab'
#: flag of `effect_tokens`). The outcome model prices damage only, so an attack whose job is to
#: displace him and one that only hurts him can land alike and still do different things: the
#: 2026-11-10 full run matched the Gravity Well tap (it pulls) with the Glintblade Phalanx ash (it
#: does not) at D_assoc 0.049, the one association of 61 that crossed a displacement (user,
#: 2026-11-10). Status is left out of the gate: 30 of the 61 crossed a status, nearly all one ash
#: on carriers whose own status differs, which is still one move.
DISPLACE_TOKENS = ('pull', 'push', 'gravity', 'grab')


def displace_bits(rec):
    """The bits of the `DISPLACE_TOKENS` a record's tokens and flags carry."""
    have = set(rec.get('tokens') or ()) | set(rec.get('flags') or ())
    return sum(1 << i for i, tok in enumerate(DISPLACE_TOKENS) if tok in have)


def delivery_gate(bits_a, bits_b=None, disp_a=None, disp_b=None):
    """Whether two attacks share a way of delivering their worth and, given their `displace_bits`,
    move or hold the target alike: [i, j] true when they do."""
    bits_a = np.asarray(bits_a, int)
    bits_b = bits_a if bits_b is None else np.asarray(bits_b, int)
    G = (bits_a[:, None] & bits_b[None, :]) != 0
    if disp_a is not None:
        disp_a = np.asarray(disp_a, int)
        disp_b = disp_a if disp_b is None else np.asarray(disp_b, int)
        G &= disp_a[:, None] == disp_b[None, :]
    return G


def gated(D, G):
    """`D` with every pair the delivery gate `G` keeps apart at 1 (the diagonal untouched)."""
    out = np.where(G, np.asarray(D, float), 1.0)
    if out.shape[0] == out.shape[1]:
        np.fill_diagonal(out, np.diag(np.asarray(D, float)))
    return out


def subcat_agreement(labels):
    """How the spell families agree with `Magic.subCategory1`, over [(family, subcategory)]: the
    ARI with the facts it is limited by (clusters, singletons), the purity of the families of two
    or more (the share of their members in their family's commonest subcategory), and the share of
    within-family spell pairs sharing a subcategory beside the share of all spell pairs that do (the
    base rate). ARI alone reads low when most clusters are singletons, whatever the families say."""
    if len(labels) < 2:
        return {'ari': None, 'spells': len(labels)}
    fams = Counter(f for f, _ in labels)
    by = {}
    for f, s in labels:
        by.setdefault(f, []).append(s)
    multi = [v for v in by.values() if len(v) > 1]
    pure = sum(Counter(v).most_common(1)[0][1] for v in multi)
    inside = [(x == y) for v in multi for i, x in enumerate(v) for y in v[i + 1:]]
    subs = [s for _, s in labels]
    allp = sum(c * (c - 1) / 2 for c in Counter(subs).values())
    tot = len(subs) * (len(subs) - 1) / 2
    return {'ari': ari([f for f, _ in labels], subs), 'spells': len(labels), 'clusters': len(fams),
            'singletons': sum(1 for c in fams.values() if c == 1), 'subcategories': len(set(subs)),
            'multi_families': len(multi), 'multi_members': sum(len(v) for v in multi),
            'purity': (pure / sum(len(v) for v in multi)) if multi else None,
            'pairs_inside': len(inside), 'same_inside': (sum(inside) / len(inside)) if inside else None,
            'same_base': allp / tot if tot else None}


def analyze(res, a):
    """Every association and the dominance at the thresholds of `a` (the cached profiles hold no
    threshold; the thresholds stored beside them only record the run that wrote the cache). The
    associations read D_assoc (contains and component twins: D_contain) behind the delivery gate;
    D_rel, D_land, D_now, D_best and D_idle ride along as columns."""
    R, keys, w, W = res.records, res.keys, res.w, res.W
    n = len(keys)
    delta = a.tolerance if a.tolerance is not None else 1.0 / n_eff(w)
    cut = a.cut if a.cut is not None else a.twin
    rate = getattr(a, 'idle_rate', IDLE_RATE)
    for k in keys:
        g, sub, flags = group_of(R[k], a.null, a.hp)
        R[k].update(group=g, group_reason=sub, group_flags=flags)
    Mx = matrices(res, rate)
    MAT = {x: Mx[x].astype(float) for x in MATRICES}
    Da, Dr, Dl, Dn, Db, Di = (MAT[x] for x in MATRICES)
    typ = np.array([R[k]['type'] for k in keys])
    parent = np.array([R[k]['parent'] for k in keys])
    scored = np.array([R[k]['status'] == 'scored' for k in keys])
    eligible = scored & np.array([R[k]['land_mass'] >= a.live for k in keys], bool)
    full = np.array([max(float(R[k]['full']), 1e-9) for k in keys])
    mass = {m: np.array([R[k][m] for k in keys]) for m in ('land_mass', 'mass_now', 'mass_best')}
    never = np.array([R[k]['group_reason'] == 'never reaches' for k in keys])
    bits = np.array([delivery_bits(R[k]) for k in keys], int)
    disp = np.array([displace_bits(R[k]) for k in keys], int)
    G = delivery_gate(bits, None, disp) if n else np.zeros((0, 0), bool)
    Dg = gated(Da, G) if n else Da

    def sib(i, j):
        return parent[i] == parent[j]

    def toks(k):
        return set(R[k]['tokens']) | set(R[k]['flags'])

    def delivery(k):
        return '+'.join(sorted(R[k].get('delivery') or ['?']))

    def measure(i):
        """The neighbour measure of entity i, or None ('never reaches')."""
        r = R[keys[i]]
        if r['group'] in ('live', 'runin_only'):
            return 'D_assoc'
        return 'D_idle' if r['group_reason'] == 'always evaded' else None

    def faint(i, j):
        """The measures whose value between i and j is read on a mass under `--null` on either
        side: an absolute gap there is small whatever the attacks are, and D_rel compares noise."""
        return [m for m, f in (('D_assoc', 'land_mass'), ('D_rel', 'land_mass'), ('D_land', 'land_mass'),
                               ('D_now', 'mass_now'), ('D_best', 'mass_best'))
                if min(mass[f][i], mass[f][j]) < a.null]

    def pair_row(i, j):
        ki, kj = keys[i], keys[j]
        # A delivery the pair does not share is shown with the tokens; the gate keeps a pair that
        # shares none (melee only against bullet only) out of every association.
        diff = [] if delivery(ki) == delivery(kj) else [f'delivery {delivery(ki)} / {delivery(kj)}']
        return {'key': kj, **{m: round(float(MAT[m][i, j]), 5) for m in MATRICES}, 'faint': faint(i, j),
                'empty': bool(max(mass['land_mass'][i], mass['land_mass'][j]) <= 0.0),
                'gated': not bool(G[i, j]),
                'full_ratio': round(float(full[j] / full[i]), 3),
                'dFP': (None if R[kj]['fp'] is None or R[ki]['fp'] is None else round(float(R[kj]['fp']) - float(R[ki]['fp']), 1)),
                'droll': round(float(R[kj]['roll']) - float(R[ki]['roll']), 1),
                'tokens_not_shared': diff + sorted(toks(ki) ^ toks(kj)), 'partial': R[kj]['status'] == 'partial'}

    # Nearest neighbours. By D_idle (an attack that is always evaded) the candidates are the
    # attacks that reach: where neither of two attacks touches an idle defender D_idle counts the
    # situation as alike, so an attack that never reaches sits near any that rarely does.
    neighbours = {}
    for i, k in enumerate(keys):
        by = measure(i)
        if by is None:
            continue
        D = MAT[by]
        out = {}
        for side, want in (('own', typ[i]), ('other', 'skill' if typ[i] == 'spell' else 'spell')):
            cand = [j for j in range(n) if j != i and typ[j] == want and not sib(i, j)
                    and not (by == 'D_idle' and never[j])]
            cand.sort(key=lambda j: (D[i, j], keys[j]))
            out[side] = [pair_row(i, j) for j in cand[:a.k]]
        neighbours[k] = {'by': by, **out}

    # Twins: whole attacks within --twin by D_assoc behind the delivery gate, and component twins (a
    # frame of X within --twin by D_contain of a whole Y of the other kind, the whole of X within
    # the scale band of Y: the Zamor Curved Sword's storm and the Zamor Ice Storm tap).
    whole_twins = twin_pairs(Dg, keys, eligible, full, parent, a.twin, a.scale_band)
    cont = contains_pairs(res, a, eligible, typ, rate)
    contains, comp_twins = cont['contains'], cont['component_twins']
    flat = [flat_land(res.A, i, R[keys[i]]['land_mass_now'], a.null) for i in range(n)]

    def basis(i, j):
        """Which term binds D_assoc between whole entities i and j, and whether the pair rests on
        D_idle alone: the idle term binds and both land one flat share wherever they land
        (`flat_land`), so their land channels agree on one quantized number whatever the attacks
        are (module doc 'Associations')."""
        bound = 'idle' if rate * Di[i, j] >= max(Dr[i, j], Dl[i, j]) else 'land'
        return {'bound': bound, 'flat': [flat[i], flat[j]],
                'rests_on_idle': bound == 'idle' and flat[i] is not None and flat[j] is not None}
    twin_kind = {p: {'by': 'whole', 'D': round(float(Da[res.index[p[0]], res.index[p[1]]]), 5),
                     **basis(res.index[p[0]], res.index[p[1]])} for p in whole_twins}
    for x in comp_twins:
        p = tuple(sorted((x['x'], x['y']), key=lambda k: res.index[k]))
        if p not in twin_kind:
            twin_kind[p] = {'by': 'component', 'component': x['component'], 'D': x['D_contain'],
                            **{k: x[k] for k in ('bound', 'flat', 'rests_on_idle')}}
    twins = sorted(twin_kind, key=lambda p: (res.index[p[0]], res.index[p[1]]))
    twin_set = set(twins)
    same_skill = [p for p in twins if R[p[0]]['type'] == R[p[1]]['type'] == 'skill' and R[p[0]]['id'] == R[p[1]]['id']]

    def match_rows(pairs):
        out = []
        for i, j in pairs:
            s, k_ = (i, j) if typ[i] == 'spell' else (j, i)
            out.append({'spell': keys[s], 'skill': keys[k_], **{m: round(float(MAT[m][i, j]), 5) for m in MATRICES},
                        'overlap': round(overlap_share(res, keys[i], keys[j]), 3),
                        'full_ratio': round(float(full[k_] / full[s]), 3), 'by': 'whole', 'D': round(float(Da[i, j]), 5),
                        **basis(s, k_)})
        return out
    matches = match_rows(mutual_nearest(Dg, keys, eligible, typ, a.twin))
    # A 'contains' is mutual nearest at the component level; with the two whole worths within the
    # scale band it is a cross-type match too.
    have = {(x['spell'], x['skill']) for x in matches}
    for x in contains:
        if abs(math.log2(max(float(R[x['x']]['full']), 1e-9) / max(float(R[x['y']]['full']), 1e-9))) > a.scale_band:
            continue
        s, k_ = (x['y'], x['x']) if R[x['y']]['type'] == 'spell' else (x['x'], x['y'])
        if (s, k_) in have:
            continue
        i, j = res.index[s], res.index[k_]
        matches.append({'spell': s, 'skill': k_, **{m: round(float(MAT[m][i, j]), 5) for m in MATRICES},
                        'overlap': round(overlap_share(res, s, k_), 3), 'full_ratio': round(float(full[j] / full[i]), 3),
                        'by': 'component', 'component': x['component'], 'D': x['D_contain'],
                        **{k: x[k] for k in ('bound', 'flat', 'rests_on_idle')}})
    matches.sort(key=lambda x: (x['D'], x['spell']))
    whole_tk = [v for v in twin_kind.values() if v['by'] == 'whole']
    rests = {'twins': sum(v['rests_on_idle'] for v in twin_kind.values()), 'twins_of': len(twin_kind),
             'whole_twins_idle_bound': sum(v['bound'] == 'idle' for v in whole_tk), 'whole_twins': len(whole_tk),
             'matches': sum(x['rests_on_idle'] for x in matches), 'matches_of': len(matches),
             'matches_idle_bound': sum(x['bound'] == 'idle' for x in matches)}

    # Families: average linkage over the eligible entities by D_assoc behind the delivery gate.
    elig_idx = [i for i in range(n) if eligible[i]]
    merges = upgma(Dg[np.ix_(elig_idx, elig_idx)])
    order = [elig_idx[x] for x in leaf_order(merges, len(elig_idx))]

    def families_at(c_):
        out = []
        for cl in cut_tree(merges, len(elig_idx), c_):
            mem = [elig_idx[x] for x in cl]
            if len(mem) < 2:
                continue
            sub = Dg[np.ix_(mem, mem)]
            med = mem[int(np.argmin(sub.sum(axis=1)))]
            fps = [float(R[keys[m]]['fp'] or 0) for m in mem]
            fulls = [full[m] for m in mem]
            out.append({'members': [keys[m] for m in mem], 'medoid': keys[med],
                        'mixed': len({typ[m] for m in mem}) > 1, 'full_spread': round(max(fulls) / min(fulls), 2),
                        'one_parent': len({parent[m] for m in mem}) == 1,
                        'one_skill': len({(typ[m], R[keys[m]]['id']) for m in mem}) == 1,
                        'fp_range': [min(fps), max(fps)], 'height': round(float(sub.max()), 4)})
        return out
    families = families_at(cut)
    # The spell members (singletons included) against Magic.subCategory1.
    fam_of = {}
    for fi, cl in enumerate(cut_tree(merges, len(elig_idx), cut)):
        for x in cl:
            fam_of[keys[elig_idx[x]]] = fi
    lab = [(fam_of[k], R[k]['subcat']) for k in fam_of if R[k]['type'] == 'spell' and R[k].get('subcat')]
    agree = subcat_agreement(lab)
    ari_v = agree['ari']

    # The pairs the delivery gate keeps apart, by D_assoc: how near a melee-only attack comes to a
    # bullet-only one (the negative-control margin), and how many such pairs each cut would admit.
    gp = [(float(Da[i, j]), i, j) for i in elig_idx for j in elig_idx
          if i < j and not G[i, j] and not sib(i, j)]
    gp.sort()
    gated_near = [{'a': keys[i], 'b': keys[j], 'D_assoc': round(d, 5), 'D_idle': round(float(Di[i, j]), 5),
                   'delivery': [delivery(keys[i]), delivery(keys[j])]} for d, i, j in gp[:10]]

    sensitivity = []
    for c_ in SENSITIVITY_CUTS:
        fam = families_at(c_)
        sensitivity.append({'cut': c_, 'twin_pairs': len(twin_pairs(Dg, keys, eligible, full, parent, c_, a.scale_band)),
                            'mutual_nearest': len(mutual_nearest(Dg, keys, eligible, typ, c_)),
                            'families': len(fam),
                            'largest': max((len(f['members']) for f in fam), default=1 if elig_idx else 0),
                            'gated': sum(1 for d, _, _ in gp if d <= c_)})

    # Yardstick: tap against charged cast of one spell, both eligible.
    taps = []
    for i, k in enumerate(keys):
        if R[k]['type'] == 'spell' and R[k]['variant'] == 'charged':
            j = res.index.get(f"spell:{R[k]['id']}:tap")
            if j is not None and eligible[i] and eligible[j]:
                taps.append(float(Da[i, j]))
    yard = round(float(np.median(taps)), 5) if taps else None

    # Effect families.
    tokens = {}
    for k in keys:
        for tk in sorted(toks(k)):
            tokens.setdefault(tk, []).append(k)
    for e in res.excluded:
        for tk in sorted(set(e.get('tokens') or []) | set(e.get('flags') or [])):
            tokens.setdefault(tk, []).append(e['key'])
    ex_by_key = {e['key']: e for e in res.excluded}
    effect = {}
    for tk, mem in sorted(tokens.items()):
        rows = []
        for k in mem:
            i = res.index.get(k)
            if i is None:
                e = ex_by_key[k]
                rows.append({'key': k, 'name': e['name'] + (f" ({e['variant']})" if e.get('variant') else ''),
                             'type': e['type'], 'group': 'excluded: ' + e['reason'],
                             'mean_net': None, 'fp': e.get('fp'), 'carrier': e.get('carrier'), 'nearest': None})
                continue
            by = measure(i) or 'D_idle'
            other = [res.index[m] for m in mem if m in res.index and typ[res.index[m]] != typ[i]]
            if by == 'D_assoc':
                # D_assoc against a member that lands nothing is 1 and says nothing: among the
                # landing members, or, when none of the other kind lands, by D_idle.
                land = [j for j in other if R[keys[j]]['group'] in ('live', 'runin_only')]
                other, by = (land, by) if land else (other, 'D_idle')
            if by == 'D_idle':
                other = [j for j in other if not never[j]] or other
            D = MAT[by]
            near = min(other, key=lambda j: (D[i, j], keys[j])) if other else None
            rows.append({'key': k, 'name': R[k]['name'], 'type': R[k]['type'], 'group': R[k]['group'],
                         'mean_net': round(R[k]['mean_net'], 1), 'fp': R[k]['fp'], 'carrier': R[k]['carrier'],
                         'nearest': None if near is None else {'key': keys[near], 'by': by, 'D': round(float(D[i, near]), 5),
                                                               'faint': by in faint(i, near)}})
        effect[tk] = rows

    probes = external_probes(res, a, MAT, typ, parent, twin_set, measure, faint)

    # Dominance, per attacker build: the distance basis has none (module doc 'Dominance').
    builds = [build_dominance(BuildView(b, res.w), a.hp, delta, twin_set) for b in res.builds]

    return {'delta': delta, 'cut': cut, 'idle_rate': rate, 'matrices': Mx, 'order': [keys[i] for i in order],
            'neighbours': neighbours, 'gate': G,
            'eligible': [keys[i] for i in elig_idx], 'twins': twins, 'twin_kind': twin_kind, 'same_skill_twins': same_skill,
            'component_twins': comp_twins, 'matches': matches, 'contains': contains, 'rests_on_idle': rests,
            'families': families, 'ari': ari_v, 'subcat': agree, 'gated_near': gated_near,
            'sensitivity': sensitivity, 'yardstick': yard, 'yardstick_n': len(taps),
            'effect': effect, 'probes': probes, 'builds': builds}


class BuildView:
    """One attacker build's records and nets, shaped as `dominance`, `dominance_report` and
    `spell_rollup` read a `Result`: `records` and `keys` (every scored and partial entity),
    `excluded`, `index` and `A` ('net', 'net_one') over the entities with nets (scored, FP above
    0), `w` and `W`. The nets are paid ones: each entity's scored nets times the share of its casts
    the build's FP pays (`paid_share` on its 'rule', recorded as 'fp_share'), the unpaid casts netting
    0 (the run scores no FP-free answer of the attacker's, such as an R1). Each record with nets
    gets its `mean_net` at the build's stats."""

    def __init__(self, b, w):
        self.build = b
        self.records = {k: dict(r) for k, r in b['records'].items()}
        self.keys = list(self.records)
        self.excluded = b['excluded']
        nk = [k for k in self.keys if k in b['nets']]
        self.index = {k: i for i, k in enumerate(nk)}
        self.w = np.asarray(w, float)
        self.W = float(self.w.sum())
        empty = np.zeros((0, len(self.w)), np.float32)
        paid = np.array([paid_share(b.get('rule'), self.records[k].get('fp')) for k in nk], float)
        self.A = {'net': np.stack([b['nets'][k][0] for k in nk]) * paid[:, None] if nk else empty,
                  'net_one': np.stack([b['nets'][k][1] for k in nk]) * paid[:, None] if nk else empty}
        for k, i in self.index.items():
            self.records[k]['fp_share'] = float(paid[i])
            self.records[k]['mean_net'] = float(self.w @ self.A['net'][i].astype(float) / self.W)


def build_dominance(bv, hp, delta, twins):
    """The dominance of one attacker build (`BuildView`): its population (scored, FP above 0), the
    edges and the own-variant edges, the report, the spell rollup, the scored attacks left out for
    costing no FP, and the population members netting at most `hp` on average."""
    R = bv.records
    pop = [k for k in bv.keys if k in bv.index]
    no_fp = [k for k in bv.keys if R[k]['status'] == 'scored' and k not in bv.index]
    dom = dominance(bv, pop, hp, delta, twins)
    own = dominance(bv, pop, hp, delta, twins, sibling_only=True)
    rep = dominance_report(bv, pop, dom, own)
    rollup, part = spell_rollup(bv, dom, pop)
    b = bv.build
    top = max(pop, key=lambda k: (R[k]['mean_net'], k), default=None)
    return {'name': b['name'], 'stats': b['stats'], 'n': b['n'], 'rl': b['rl'], 'reach': b.get('reach'),
            'rule': b.get('rule'), 'usable': b.get('usable'), 'view': bv, 'population': pop, 'no_fp': no_fp,
            'top': top, 'top_paid': R[top]['fp_share'] if top else None,
            'punished': [k for k in pop if R[k]['mean_net'] <= hp],
            'dominance': dom, 'own': own, 'report': rep, 'rollup': rollup, 'rollup_part': part}


def overlap_share(res, ka, kb):
    """The share of the smaller profile mass two whole entities have in common (1: the smaller
    profile lies inside the larger; 0: they share no outcome)."""
    ia, ib = res.index[ka], res.index[kb]
    ua, ub = rel_profile(res.A, ia), rel_profile(res.A, ib)
    lo = min(res.records[ka]['mass'], res.records[kb]['mass'])
    return overlap(ua, ub, res.w) / lo if lo > 0 else 0.0


def spell_rollup(res, dom, pop):
    """([redundant spell], [spell dominated on its landing variants only]). A spell is redundant when
    every variant of it, the variants `evaluate` gave no hit and the ones without a reach model
    included, is scored and robustly dominated from outside it. When every scored variant that
    nets something is, but another variant is excluded, partial or nets nothing, it goes on the
    second list with those variants named."""
    R = res.records
    robust_b = {}
    for e in dom['edges']:
        if e['class'] == 'robust':
            robust_b.setdefault(e['b'], []).append(e['a'])
    excl = {}
    for e in res.excluded:
        if e['type'] == 'spell' and e['key'].count(':') == 2:
            excl.setdefault(e['id'], []).append(e)
    nothing, popset = set(dom['nets_nothing']), set(pop)
    full_, part = [], []
    for mid in sorted({R[k]['id'] for k in res.keys if R[k]['type'] == 'spell'}):
        vs = [k for k in res.keys if R[k]['type'] == 'spell' and R[k]['id'] == mid]
        landing = [k for k in vs if R[k]['status'] == 'scored' and k in popset and k not in nothing]
        if not landing or not all(k in robust_b for k in landing):
            continue
        rest = [[k, 'partial' if R[k]['status'] == 'partial' else 'no FP' if k not in popset else 'nets nothing']
                for k in vs if k not in landing]
        rest += [[e['key'], e['reason']] for e in excl.get(mid, [])]
        row = {'spell': R[vs[0]]['name'], 'id': mid, 'variants': landing,
               'by': {k: sorted(robust_b[k])[:3] for k in landing}, 'not_dominated': rest}
        (part if rest else full_).append(row)
    return full_, part


def contains_pairs(res, a, eligible, typ, rate=IDLE_RATE):
    """{'contains', 'component_twins'} behind the delivery gate: an item is a candidate for Y only
    when it shares a way of delivering its worth with Y (`delivery_gate`), so a melee frame never
    contains a bullet spell (Painful Strike's f34 whip against Carian Phalanx, `MEASURED` round 3:
    D_contain 0.056, tighter than the Glintblade Phalanx twin; against the Glintblade Phalanx cast
    0.089 on the 2026-11-10 full run).

    'X contains Y' (module doc): Y's nearest item of X's kind by D_contain (every scored whole
    and every component of that kind) is a component c of X, strictly nearer than every whole; Y is
    c's nearest whole of its kind; D_contain within `--twin`; c and Y land at least `--live` of
    their worth. Ties go to the lower key. D_contain reads what lands only: a frame inside a longer
    attack is punished on that attack's recovery, not its own.

    A component twin: a component c of X within `--twin` of a whole Y of the other kind by
    D_contain, both landing at least `--live`, and the whole worth of X within a factor
    2^`--scale-band` of Y's; one row per (X, Y), its nearest component."""
    R, keys, w, W = res.records, res.keys, res.w, res.W
    if not res.comp_keys:
        return {'contains': [], 'component_twins': []}
    ctyp = np.array([res.comps[k]['type'] for k in res.comp_keys])
    cok = np.array([res.comps[k]['land_mass'] >= a.live for k in res.comp_keys])
    whole = [i for i in range(len(keys)) if R[keys[i]]['status'] == 'scored']
    LA, LC = rel_profile(res.A, channels=LAND_CHANNELS), rel_profile(res.C, channels=LAND_CHANNELS)
    BA = base_share(res.A, exact_full(R, keys))
    BC = base_share(res.C, exact_full(res.comps, res.comp_keys))
    TA, TC = res.A['t_first'], res.C['t_first']
    bA = np.array([delivery_bits(R[k]) for k in keys], int)
    bC = np.array([delivery_bits(res.comps[k]) for k in res.comp_keys], int)
    # A component carries its whole attack's effects (`INFERRED`: the tokens are read per attack,
    # not per frame).
    eA = np.array([displace_bits(R[k]) for k in keys], int)
    eC = np.array([displace_bits(R[res.comps[k]['of']]) for k in res.comp_keys], int)

    def dc(P, i, Q, j):
        """D_contain between rows `i` of side P and rows `j` of side Q (each 'A' or 'C'), with the
        pairs the delivery gate keeps apart at infinity."""
        L, B, Tf, b1, e1 = {'A': (LA, BA, TA, bA, eA), 'C': (LC, BC, TC, bC, eC)}[P]
        L2, B2, T2, b2, e2 = {'A': (LA, BA, TA, bA, eA), 'C': (LC, BC, TC, bC, eC)}[Q]
        D = d_contain_rows(L[i], B[i], Tf[i], L2[j], B2[j], T2[j], w, W, res.F, rate)
        return np.where(delivery_gate(b1[i], b2[j], e1[i], e2[j]), D, math.inf)

    def row(c, y, d):
        ck = res.comp_keys[c]
        x = res.comps[ck]['of']
        d_land = float(d_rel_rows(LC[c], LA[y], w)[0, 0])
        d_idle = float(d_idle_rows(BC[[c]], TC[[c]], BA[[y]], TA[[y]], w, W, res.F)[0, 0])
        d_rel = float(d_rel_rows(rel_profile(res.C, c), rel_profile(res.A, y), w)[0, 0])
        bound = 'idle' if rate * d_idle >= d_land else 'land'
        fl = [flat_land(res.C, c, res.comps[ck]['land_mass_now'], a.null),
              flat_land(res.A, y, R[keys[y]]['land_mass_now'], a.null)]
        return {'x': x, 'component': ck, 'y': keys[y], 'D_contain': round(float(d), 5),
                'D_land': round(d_land, 5), 'D_idle': round(d_idle, 5), 'D_rel': round(d_rel, 5),
                'full_ratio': round(float(R[keys[y]]['full']) / max(float(R[x]['full']), 1e-9), 3),
                'bound': bound, 'flat': fl, 'rests_on_idle': bound == 'idle' and None not in fl}
    out, twins = [], []
    for kind in ('spell', 'skill'):
        other = 'skill' if kind == 'spell' else 'spell'
        Y = [i for i in whole if typ[i] == other and eligible[i]]
        Xw = [i for i in whole if typ[i] == kind]
        Xc = [j for j in range(len(res.comp_keys)) if ctyp[j] == kind]
        if not Y or not Xc:
            continue
        Dyw = dc('A', Y, 'A', Xw) if Xw else np.zeros((len(Y), 0))
        Dyc = dc('A', Y, 'C', Xc)
        Yall = [i for i in whole if typ[i] == other]
        Dcy = dc('C', Xc, 'A', Yall)
        for yi, y in enumerate(Y):
            best = {}
            for ci, c in enumerate(Xc):
                d = Dyc[yi, ci]
                if not cok[c] or not d <= a.twin:
                    continue
                x = res.comps[res.comp_keys[c]]['of']
                if abs(math.log2(max(float(R[x]['full']), 1e-9) / max(float(R[keys[y]]['full']), 1e-9))) > a.scale_band:
                    continue
                if x not in best or d < best[x][1]:
                    best[x] = (c, d)
            twins += [row(c, y, d) for c, d in best.values()]
            best_w = float(Dyw[yi].min()) if Dyw.shape[1] else math.inf
            ci = int(np.argmin(Dyc[yi]))
            if not Dyc[yi, ci] < best_w:
                continue
            c = Xc[ci]
            if not cok[c] or Dyc[yi, ci] > a.twin:
                continue
            if Yall[int(np.argmin(Dcy[ci]))] != y:
                continue
            out.append(row(c, y, Dyc[yi, ci]))
    return {'contains': sorted(out, key=lambda r: (r['D_contain'], r['x'], r['y'])),
            'component_twins': sorted(twins, key=lambda r: (r['D_contain'], r['x'], r['y']))}


def external_probes(res, a, MAT, typ, parent, twin_set, measure, faint):
    """Pairs named from outside the distances: cross-kind same name, same-kind same-name skills, and
    cross-kind pairs firing a bullet with the same `sfxId_Bullet`. The rank is b's place among a's
    nearest of b's kind by a's own neighbour measure."""
    R, keys = res.records, res.keys
    n = len(keys)
    why = {}
    norm = [T._norm(R[k]['name']) for k in keys]
    for i in range(n):
        for j in range(i + 1, n):
            if parent[i] == parent[j]:
                continue
            tags = []
            if norm[i] == norm[j]:
                tags.append('same name' if typ[i] != typ[j] else 'same-name skills' if typ[i] == 'skill' else None)
            if typ[i] != typ[j] and set(R[keys[i]]['sfx']) & set(R[keys[j]]['sfx']):
                tags.append('shared bullet sfx')
            tags = [x for x in tags if x]
            if tags:
                why[(i, j)] = tags

    never = [R[k]['group_reason'] == 'never reaches' for k in keys]

    def rank(i, j):
        """b's place among a's nearest of b's kind, by a's neighbour measure and among the
        candidates `analyze`'s neighbour lists take (by D_idle: the attacks that reach)."""
        by = measure(i)
        if by is None:
            return None, None
        D = MAT[by]
        other = sorted((m for m in range(n) if typ[m] == typ[j] and parent[m] != parent[i] and m != i
                        and not (by == 'D_idle' and never[m])), key=lambda m: (D[i, m], keys[m]))
        return (other.index(j) + 1 if j in other else None), by
    Da = MAT['D_assoc']
    out = []
    for (i, j), tags in sorted(why.items(), key=lambda kv: (Da[kv[0][0], kv[0][1]], keys[kv[0][0]])):
        nn, by = rank(i, j)
        out.append({'a': keys[i], 'b': keys[j], 'why': tags, **{m: round(float(MAT[m][i, j]), 5) for m in MATRICES},
                    'faint': faint(i, j), 'nn_rank': nn, 'nn_by': by,
                    'twin': (keys[i], keys[j]) in twin_set or (keys[j], keys[i]) in twin_set,
                    'groups': [R[keys[i]]['group'], R[keys[j]]['group']]})
    return out


# ---------------------------------------------------------------------------------------------
# output

def label(r):
    if r is None:
        return '?'
    if r['type'] == 'spell':
        return r['name'] + ('' if r.get('variant') in (None, 'cast') else f" ({r['variant']})")
    aff = r.get('affinity')
    out = f"{r['name']} [{r.get('carrier')}{'' if aff in (None, 'Standard') else ' ' + aff}]"
    out += f" {r['variant']}" if r.get('variant') else ''
    out += ' charged' if r.get('release') == 'full' else ''
    # Two follow-ups can share a button and a clip: the press frame tells them apart.
    return out + (f" at f{float(r['follow']['first']):g}" if r.get('follow') else '')


def find(res, name):
    """Keys (entity, component-free) and exclusions whose name matches `name` (`T._norm`)."""
    want = T._norm(name)
    ks = [k for k, r in res.records.items() if T._norm(r['name']) == want]
    ex = [e for e in res.excluded if T._norm(e['name']) == want]
    return ks, ex


def counts(res):
    """(status counts, group counts, exclusion reason counts, keys with affinity errors)."""
    st = Counter(r['status'] for r in res.records.values())
    st['excluded'] = len(res.excluded)
    gr = Counter((r['type'], r.get('group'), r.get('group_reason')) for r in res.records.values())
    rs = Counter((e['type'], e['reason'] if not e['reason'].startswith('model error') else 'model error')
                 for e in res.excluded)
    aff = sorted({r['key'] for r in list(res.records.values()) + res.excluded if r.get('affinity_errors')})
    return st, gr, rs, aff


def thresholds(an, a):
    """The thresholds an analysis was made at."""
    return {'twin': a.twin, 'cut': an['cut'], 'live': a.live, 'null': a.null, 'tolerance': an['delta'], 'hp': a.hp,
            'scale_band': a.scale_band, 'k': a.k, 'idle_rate': an['idle_rate']}


def json_doc(res, an, a):
    R = res.records
    ents = {k: {kk: vv for kk, vv in r.items() if kk not in ('value',)} for k, r in R.items()}
    st, gr, rs, aff = counts(res)
    return {'meta': {**res.meta, 'thresholds': thresholds(an, a)},
            'counts': {'status': dict(st), 'groups': [[*k, v] for k, v in sorted(gr.items(), key=str)],
                       'reasons': [[*k, v] for k, v in sorted(rs.items())], 'eligible': len(an['eligible']),
                       'affinity_errors': aff},
            'entities': ents, 'excluded': res.excluded, 'truncated_components': res.truncated,
            'matches': an['matches'], 'twins': [[*p, an['twin_kind'][p]] for p in an['twins']],
            'same_skill_twins': an['same_skill_twins'], 'component_twins': an['component_twins'],
            'contains': an['contains'], 'gated_near': an['gated_near'],
            'effect_families': an['effect'], 'families': an['families'], 'ari_subcategory1': an['ari'],
            'subcategory_agreement': an['subcat'],
            'sensitivity': an['sensitivity'], 'yardstick': an['yardstick'], 'yardstick_n': an['yardstick_n'],
            'neighbours': an['neighbours'], 'probes': an['probes'], 'order': an['order'],
            'rests_on_idle': an['rests_on_idle'],
            'build_dependent': BUILD_DEPENDENT, 'build_independent': BUILD_INDEPENDENT,
            'dominance': {'basis': 'per attacker build', 'builds': [build_json(b) for b in an['builds']]}}


#: What reads damage and FP, so holds only for the build it is read at (per attacker build), and
#: what does not (the distance basis, every damage stat 99: scale-free shares, so it barely moves
#: with the build; `MEASURED`, the Zamor tap at 4x its worth is D_rel 0.0098 from itself).
BUILD_DEPENDENT = ('dominance', 'redundant lists', 'frontiers', 'own-variant edges', 'spell rollup',
                   "the 'punished' flags of each build")
BUILD_INDEPENDENT = ('matrices', 'twins', 'cross-type matches', 'contains', 'families', 'neighbours',
                     'effect families', 'probes', 'groups')


def build_json(b):
    """One attacker build's dominance for the association JSON."""
    return {k: b.get(k) for k in ('name', 'stats', 'n', 'rl', 'reach', 'rule', 'usable', 'population', 'no_fp',
                                  'punished', 'top', 'top_paid')} | {
        'edges': b['dominance']['edges'], 'equivalent': b['dominance']['equivalent'],
        'nets_nothing': b['dominance']['nets_nothing'], **b['report'], 'own_variants': b['own']['edges'],
        'spell_rollup': b['rollup'], 'spell_rollup_landing_only': b['rollup_part'],
        'mean_net': {k: round(b['view'].records[k]['mean_net'], 2) for k in b['population']},
        'entities': {k: {kk: r.get(kk) for kk in ('name', 'type', 'carrier', 'affinity', 'variant', 'follow', 'fp',
                                                   'fp_share', 'status', 'release')}
                     for k, r in b['view'].records.items()}}


#: The measures that compare what lands: '-' between two attacks that both land nothing, where
#: D_assoc falls to the idle term alone and would read like a twin (Gravitas against Blood Blade).
LAND_MEASURES = ('D_assoc', 'D_rel', 'D_land')


def _d(x, m):
    """One distance of a pair row, '~' marking a value read on a mass under `--null`, '-' a land
    measure between two attacks that land nothing."""
    if m in LAND_MEASURES and x.get('empty'):
        return '     -'
    return f"{x[m]:.4f}{'~' if m in x.get('faint', ()) else ''}"


def line_text(st):
    """An attribute line, 'vig58 mnd10 ...'."""
    return ' '.join(f'{k}{st[k]}' for k in ATTRS if k in st)


def build_title(b):
    """'Strength (Heavy Knight; vig60 mnd8 ...; RL 150; usage: 327 corpus builds)': the line, the
    class it is made on, and how many corpus builds its archetype has (`corpus_usage`), which a
    split line has none of."""
    cls = (b.get('rule') or {}).get('class')
    use = f"; usage: {b['n']} corpus builds" if b.get('n') else ''
    return f"{b['name']} ({cls + '; ' if cls else ''}{line_text(b['stats'])}; RL {b['rl']}{use})"


def rule_cells(b):
    """The page's cells of how attacker build `b` was made (`attacker_builds` 'rule'): its starting
    class, its VIG, its `END` with the burden it keeps at medium roll, and its MND. Dashes for a
    build with no rule (a cache made before version 10)."""
    r = b.get('rule')
    if not r:
        return ['-', '-', '-', '-']
    main = ' and '.join(k.upper() for k in r['main'])
    if r.get('source') == 'given':
        e, p = r['end'], r.get('fp_plan') or {}
        armor = e.get('armor') or {}
        return [html.escape(f"{r['class']}; given (--line), no stat traded"),
                html.escape(f"{b['stats']['vig']} (given): HP {p.get('hp', 0):g}, no HP piece"),
                html.escape(f"{b['stats']['end']} (given): {', '.join(armor.get('pieces') or []) or 'no armor'}, "
                            f"{armor.get('poise', 0):g} poise (the lightest at {e['poise_floor']:g}); armor alone "
                            f"{armor.get('weight', 0.0)} of {e['max_load']} ({e['burden']}, "
                            f"{'medium roll' if e['medium'] else 'past medium roll'}); hands not counted"),
                html.escape(f"{b['stats']['mnd']} (given): bar {p.get('bar', 0):g}")]
    if r.get('source') == 'line-fit':
        f = r['fit']
        kit = '; '.join(f"{p['name']}" + (f" from {p['catalyst']}" if p['kind'] == 'spell' else
                                          f" ({p['affinity']}{', two-handed' if p['two'] else ''}) R1")
                        + (f" x{p['share']:g}" if p['share'] != 1 else '') + f", hit {p['hit']:g}"
                        for p in f.get('kit') or []) or f"{f['spell']}, {f['catalyst']}"
        bf = f.get('below_floor')
        floor = (f"; floor {f['poise_floor']:g}" + (
            f", best below it: VIG {bf['stats']['vig']} END {bf['stats']['end']}, {bf['armor_poise']:g} poise, "
            f"D x{bf['ratio']:.3f}" if bf and bf['armor_poise'] < f['poise_floor'] else ", nothing better below it")
                 ) if f.get('poise_floor') else ''
        knee = f.get('vig_knee')
        m = r.get('mind')
        return [html.escape(f"{r['class']}; er-mechanics-line-fit, {main} to {f['cap']} ({kit}); D {f['D']:g}"),
                html.escape(f"{b['stats']['vig']} (traded): HP {f['hp']} with {f['wear'] or 'no HP piece'}"
                            + (f"; VIG at the knee, rest fitted: D x{knee['ratio']:.3f}" if knee else '')),
                html.escape(f"{b['stats']['end']} (traded): {', '.join(f['pieces'])}, {f['armor_poise']:g} poise{floor}; "
                            f"carries {f['carried']} (its kit; usage {(r.get('usage') or {}).get('carried')}); "
                            f"burden {f['burden']}"),
                html.escape(f"{b['stats']['mnd']} ({'the Mind fit' if m else 'traded'}): bar {f['bar']}, "
                            f"{f['cerulean']} blue / {f['crimson']} red; its spell paid {f['paid']:.3f}, "
                            f"{f['life']:g} engagements lived" + (f"; took {m['from'] or 'nothing'}" if m else ''))]
    v, e, m = r['vig'], r['end'], r.get('mind') or {}
    armor = e.get('armor') or {}
    spill = ' and '.join(k.upper() for k in r['spill']) or 'every damage stat'
    d = m.get('paid_dearest') or {}
    mind = (f"{m['mnd']} (base {m['base']}, bar {m['bar']:g}; took {m['from'] or 'nothing'}); its "
            f"{m['attacks']} FP attacks paid {m['share']:.4f} (at the base {m['share_base']:.4f}); the dearest, "
            f"{m['dearest']:g} FP an engagement: paid {d.get('share', 1):.3f} with {d.get('cerulean', 0)} blue "
            f"flasks, {d.get('life') or 0:.1f} engagements lived") if m else 'class base (no fit)'
    return [html.escape(f"{r['class']}; {main} to {r['cap']}, then {spill}"),
            f"{v['knee']} (the knee, fixed, not traded): +{v['gain']}, then +{v['gain_after']} HP",
            html.escape(f"{e['end']}: {e['carried']} ({e['carried_from']} usage, {e['carried_n']} builds) + "
                        f"{armor.get('weight', 0.0)} ({armor.get('poise')} poise) = {e['need']} of {e['max_load']} "
                        f"({e['burden']})"),
            html.escape(mind)]


def first_dominators(b, robust=True):
    """Counter of the first-listed dominator over the redundant attacks of one build (robust ones:
    the first maximal robust dominator)."""
    out = Counter()
    for r in b['report']['redundant']:
        if robust and r['class'] != 'robust':
            continue
        if r['maximal']:
            out[r['maximal'][0]['a']] += 1
    return out


def build_summary(b, top=5):
    """Lines of one attacker build's dominance for the printed summary."""
    R = b['view'].records
    d, rep, u, rch = b['dominance'], b['report'], b.get('usable') or {}, b.get('reach') or {}
    red = rep['redundant']
    rob = [r for r in red if r['class'] == 'robust']
    fr = sorted(rep['frontier_robust'], key=lambda x: -R[x['key']]['mean_net'])
    firsts = first_dominators(b)
    nrob = sum(e['class'] == 'robust' for e in d['edges'])
    out = [f"build {build_title(b)}: from {', '.join(rch.get('classes') or []) or 'no starting class'}"
           f"{'' if rch.get('ok') else ' (NOT reachable at the RL window)'}; casts {u.get('spells')} of "
           f"{u.get('of_spells')} spells, wields {u.get('skill_openings')} of {u.get('of_skill_openings')} skill "
           f"openings",
           f"  population {len(b['population'])} (no FP: {', '.join(label(R[k]) for k in b['no_fp']) or '-'}); "
           f"{len(d['nets_nothing'])} net nothing; {len(d['edges'])} edges, {nrob} robust; redundant {len(red)}, "
           f"{len(rob)} robustly; {len(b['punished'])} punished as much as they land; cycle "
           f"{'none' if not rep.get('cycle') else ' -> '.join(rep['cycle'])}",
           f"  robust frontier {len(fr)}, by mean net: "
           + '; '.join(f"{label(R[x['key']])} {R[x['key']]['mean_net']:.0f}"
                       f"{' (' + ', '.join(x['warnings']) + ')' if x['warnings'] else ''}" for x in fr[:top])
           + ('; ...' if len(fr) > top else ''),
           "  first-listed robust dominator: "
           + ('; '.join(f"{label(R[k])} for {n}" for k, n in firsts.most_common(top)) or '-'),
           f"  spells robustly dominated on every variant ({len(b['rollup'])}): "
           + (', '.join(x['spell'] for x in b['rollup']) or '-')]
    return out


def objective_moves(o):
    """'int+9 (paid str-5 dex-4)': what an objective line raised and paid, '' when nothing."""
    if not o or not o.get('raised'):
        return ''
    paid = ' '.join(f'{k}-{v}' for k, v in (o.get('paid') or {}).items()) or 'nothing'
    return ' '.join(f'{k}+{v}' for k, v in o['raised'].items()) + f' (paid {paid})'


def objective_text(o):
    """One objective line of an entity: the line, where it comes from, what it raised and paid, the
    carrier it rode and its mean net."""
    if o is None:
        return 'no line (the run has no attacker build)'
    src = (f"the {o['from_line']} line" + (f", {objective_moves(o)}" if o.get('raised') else ', unchanged')
           if o.get('from_line') else f"the requirements on the {o.get('from_class')} base")
    aff = o.get('affinity')
    rode = f" [{o.get('carrier')}{'' if aff in (None, 'Standard') else ' ' + aff}]" if o.get('carrier') else ''
    net = (f"mean net {o['mean_net']:.1f}, FP {o.get('fp')}" if o.get('mean_net') is not None
           else f"no net ({o.get('status')}{': ' + o['reason'] if o.get('reason') else ''})")
    return f"{line_text(o['stats'])}, RL {o['rl']} ({src}){rode}: {net}"


def priced_by(an, k):
    """The attacker builds that have nets for key `k` (it is in their population)."""
    return [b['name'] for b in an['builds'] if k in b['view'].index]


def objective_summary(res, an):
    """One line: how many scored and partial attacks each objective line prices, how many adjusted
    lines moved points and kept the RL, and how many attacks no attacker build prices."""
    R = res.records
    n = len(R)
    got = {nm: sum((r.get('objective') or {}).get(nm) is not None
                   and r['objective'][nm].get('mean_net') is not None for r in R.values()) for nm in OBJECTIVE_LINES}
    adj = [r['objective']['adjusted'] for r in R.values() if (r.get('objective') or {}).get('adjusted')]
    moved = [o for o in adj if o.get('raised')]
    kept = sum(o['rl'] == o['from_rl'] for o in moved)
    orphan = [k for k in R if not priced_by(an, k)]
    return (f"objective evaluation (every scored and partial attack at a line that can use it, read off no "
            f"corpus build): adjusted line priced for {got['adjusted']} of {n} ({len(moved)} lines raised to "
            f"the requirements, {kept} of them at their archetype line's RL), minimum line for {got['minimum']} of "
            f"{n}; {len(orphan)} of the {n} have nets in no attacker build")


def print_summary(res, an, out=sys.stdout):
    R = res.records
    st, gr, rs, aff = counts(res)
    p = lambda *x: print(*x, file=out)  # noqa: E731
    m = res.meta
    p(f"attack similarity: distance basis stats {m['stats']} (wieldability-free; every association reads it), "
      f"RL {m['rl']}, {m['situations']} situations (W {m['W']:.3f}, n_eff {m['n_eff']:.1f})"
      f"{'' if not m.get('only') else ', subset ' + ', '.join(m['only'])}")
    p(f"attacker builds (lines made from the game's curves, the corpus giving usage alone; dominance is read "
      f"per build, at base stats; requirements on base stats: whether talisman and great-rune boosts count is "
      f"not established): " + '; '.join(build_title(b) for b in res.builds))
    p('status: ' + ', '.join(f'{k} {v}' for k, v in sorted(st.items()))
      + f"; {len(an['eligible'])} land enough to associate")
    p('groups:')
    for (ty, g, sub), v in sorted(gr.items(), key=str):
        p(f"  {ty:5s} {g}{'' if not sub else ' / ' + sub}: {v}")
    p('excluded, by reason:')
    for (ty, why), v in sorted(rs.items(), key=lambda kv: (-kv[1], kv[0])):
        p(f'  {ty:5s} {v:4d}  {why}')
    if aff:
        p(f"affinity the model raised on, left out of the pick ({len(aff)}): {', '.join(aff[:10])}")
    if an['yardstick'] is not None:
        p(f"yardstick: median D_assoc(tap, charged) {an['yardstick']:.4f} over {an['yardstick_n']} spells "
          f"('as alike as two casts of one spell')")
    p(f"cross-type matches (mutual nearest within twin: D_assoc whole to whole, or D_contain through a "
      f"component), top 20 of {len(an['matches'])}:")
    for x in an['matches'][:20]:
        via = '' if x['by'] == 'whole' else f", via {x['component'].rsplit('@', 1)[1]}"
        p(f"  {x['D']:.4f}  {label(R[x['spell']])}  <->  {label(R[x['skill']])}  (full x{x['full_ratio']}, "
          f"D_assoc {x['D_assoc']:.4f}, D_idle {x['D_idle']:.4f}{via}, bound by {x['bound']}"
          f"{', rests on D_idle: flat ' + ' / '.join(f'{v:g}' for v in x['flat']) if x['rests_on_idle'] else ''})")
    tk = an['twin_kind']
    p(f"twin pairs: {len(an['twins'])} ({sum(v['by'] == 'whole' for v in tk.values())} whole, "
      f"{sum(v['by'] == 'component' for v in tk.values())} through a component), cross-type "
      f"{sum(R[a_]['type'] != R[b]['type'] for a_, b in an['twins'])}, one skill on two carriers "
      f"{len(an['same_skill_twins'])}")
    if an['gated_near']:
        g = an['gated_near'][0]
        p(f"nearest pair the delivery gate keeps apart: {label(R[g['a']])} / {label(R[g['b']])} at D_assoc "
          f"{g['D_assoc']:.4f} ({' / '.join(g['delivery'])}); within each sensitivity cut: "
          + ', '.join(f"{x['cut']}: {x['gated']}" for x in an['sensitivity']))
    sa = an['subcat']
    if sa.get('ari') is not None:
        p(f"spell families against Magic.subCategory1: ARI {sa['ari']:.3f} over {sa['spells']} spells in "
          f"{sa['clusters']} clusters ({sa['singletons']} singletons); families of 2+ "
          f"{'-' if sa['purity'] is None else f'{100 * sa['purity']:.0f}%'} pure, within-family pairs sharing a "
          f"subcategory {'-' if sa['same_inside'] is None else f'{sa['same_inside']:.2f}'} against "
          f"{'-' if sa['same_base'] is None else f'{sa['same_base']:.3f}'} over all spell pairs")
    p(f"contains ({len(an['contains'])}):")
    for x in an['contains']:
        p(f"  {label(R[x['x']])} contains {label(R[x['y']])}: {x['component'].rsplit('@', 1)[1]} at "
          f"D_contain {x['D_contain']:.4f} (D_land {x['D_land']:.4f}, D_idle {x['D_idle']:.4f}; full x{x['full_ratio']})")
    ri = an['rests_on_idle']
    p(f"resting on D_idle alone (the idle term binds and both land one flat run-in share): "
      f"{ri['matches']} of {ri['matches_of']} cross-type matches ({ri['matches_idle_bound']} idle-bound), "
      f"{ri['twins']} of {ri['twins_of']} twin pairs ({ri['whole_twins_idle_bound']} of {ri['whole_twins']} whole "
      "twins idle-bound)")
    p(f"build-dependent, per attacker build: {', '.join(BUILD_DEPENDENT)}; on the distance basis: "
      f"{', '.join(BUILD_INDEPENDENT)}")
    for b in an['builds']:
        for line in build_summary(b):
            p(line)
    p(objective_summary(res, an))
    for tk in ('pull', 'push', 'gravity') + tuple(f'status:{s}' for s in STATUS_TOKEN.values()):
        rows = an['effect'].get(tk)
        if not rows:
            continue
        p(f"effect family '{tk}' ({len(rows)}):")
        for x in rows:
            near = x['nearest']
            name = label(R[x['key']]) if x['key'] in R else x['name']
            net = '' if x['mean_net'] is None else f"{x['mean_net']:.1f}"
            tail = '' if not near else (f"  nearest other kind {label(R[near['key']])} {near['D']:.4f}"
                                        f"{'~' if near.get('faint') else ''} ({near['by']})")
            p(f"  {x['type']:5s} {name[:56]:56s} {str(x['group'])[:30]:30s} net {net:>8} FP {x['fp']}{tail}")
    p("('~': read on a profile mass under --null on either side)")


def print_show(res, an, name, out=sys.stdout):
    R = res.records
    p = lambda *x: print(*x, file=out)  # noqa: E731
    ks, ex = find(res, name)
    if not ks and not ex:
        p(f'{name}: no entity or exclusion by that name')
        return 1
    for e in ex:
        p(f"{e['key']}: excluded, {e['reason']}")
    twins = an['twins']
    for k in ks:
        r = R[k]
        p(f"{k}: {label(r)} {r['status']}, group {r['group']}{'' if not r['group_reason'] else ' / ' + r['group_reason']}"
          f" land mass now {r['land_mass_now']:.4f} best {r['land_mass_best']:.4f}, reach {r['reach']:.3f},"
          f" mean net {r['mean_net']:.1f}, full {r['full']}, FP {r['fp']}{' (lower bound)' if r.get('fp_lower_bound') else ''},"
          f" roll {r['roll']}")
        p(f"  tokens {', '.join(r['tokens']) or '-'}; flags {', '.join(r['flags']) or '-'}"
          f"{'; missing ' + str(r['missing']) if r['missing'] else ''}"
          f"{'; affinity errors ' + str(r['affinity_errors']) if r.get('affinity_errors') else ''}")
        for nm in OBJECTIVE_LINES:
            p(f"  objective, {nm} line: {objective_text((r.get('objective') or {}).get(nm))}")
        nb = an['neighbours'].get(k)
        if nb:
            for side in ('own', 'other'):
                p(f"  nearest {side} kind by {nb['by']} (D_assoc / D_rel / D_now / D_best / D_idle):")
                for x in nb[side]:
                    p(f"    {_d(x, 'D_assoc')} {_d(x, 'D_rel')} {_d(x, 'D_now')} {_d(x, 'D_best')} {x['D_idle']:.4f}  "
                      f"{label(R[x['key']])} full x{x['full_ratio']} dFP {x['dFP']} droll {x['droll']}"
                      f"{' tokens differ: ' + ', '.join(x['tokens_not_shared']) if x['tokens_not_shared'] else ''}"
                      f"{' (partial)' if x['partial'] else ''}")
        else:
            p('  no neighbours: it never reaches an idle defender')
        tw = [(b if a_ == k else a_, an['twin_kind'][(a_, b)]) for a_, b in twins if k in (a_, b)]
        p(f"  twins: " + (', '.join(label(R[x]) + ('' if v['by'] == 'whole' else
                                                    f" (through {v['component'].rsplit('@', 1)[1]})")
                                    for x, v in tw) or '-'))
        for x in an['matches']:
            if k in (x['spell'], x['skill']):
                o = x['skill'] if k == x['spell'] else x['spell']
                via = 'D_assoc' if x['by'] == 'whole' else f"D_contain through {x['component'].rsplit('@', 1)[1]}"
                p(f"  cross-type match: {label(R[o])} at {via} {x['D']:.4f} (D_assoc {x['D_assoc']:.4f}, "
                  f"D_rel {x['D_rel']:.4f}, D_idle {x['D_idle']:.4f}, full x{x['full_ratio']})")
        for c in an['contains']:
            if k in (c['x'], c['y']):
                p(f"  {label(R[c['x']])} contains {label(R[c['y']])}: {c['component'].rsplit('@', 1)[1]} at "
                  f"D_contain {c['D_contain']:.4f} (D_land {c['D_land']:.4f}, D_idle {c['D_idle']:.4f})")
        for fi, f in enumerate(an['families']):
            if k in f['members']:
                p(f"  family {fi + 1} of {len(f['members'])} (medoid {label(R[f['medoid']])}, largest D_assoc inside "
                  f"{f['height']:.4f}): {'; '.join(label(R[m]) for m in f['members'] if m != k)}")
        for x in an['probes']:
            if k in (x['a'], x['b']):
                o = x['b'] if k == x['a'] else x['a']
                p(f"  probe ({', '.join(x['why'])}) {label(R[o])}: D_assoc {_d(x, 'D_assoc')} D_rel {_d(x, 'D_rel')} "
                  f"D_idle {x['D_idle']:.4f}, rank {x['nn_rank']} by {x['nn_by']}{', twin' if x['twin'] else ''}")
        if r.get('inherited_frames'):
            p(f"  frames before the press {', '.join(f'f{x:g}' for x in r['inherited_frames'])} are the opening's: "
              "no component, so any association through them is the opening's")

        def cls(e):
            why = e.get('full_only_why') or []
            return e['class'] + (f" ({', '.join(why)})" if why else '')
        # Dominance per attacker build: the build's own carrier, affinity and nets.
        for b in an['builds']:
            Rb = b['view'].records
            if k not in Rb:
                continue
            rb = Rb[k]
            into = sorted((e for e in b['dominance']['edges'] if e['b'] == k),
                          key=lambda e: (e['class'] != 'robust', -e['gain']))
            outof = [e for e in b['dominance']['edges'] if e['a'] == k]
            net = f"mean net {rb['mean_net']:.1f}" if 'mean_net' in rb else 'no nets (' + rb['status'] + \
                (', no FP' if rb['status'] == 'scored' else '') + ')'
            p(f"  build {b['name']}: {label(rb)}, {net}; dominated by {len(into)}, dominates {len(outof)}")
            for e in into[:5]:
                p(f"    dominated by {label(Rb[e['a']])} ({cls(e)}, gain {e['gain']:+.1f}, loss {e['loss']:.4f})")
            for e in sorted(outof, key=lambda e: -e['gain'])[:5]:
                p(f"    dominates {label(Rb[e['b']])} ({cls(e)}, gain {e['gain']:+.1f}, loss {e['loss']:.4f})")
        absent = [b['name'] for b in an['builds'] if k not in b['view'].records]
        if absent:
            p(f"  not usable by (cannot wield or cast it, or another carrier's key): {', '.join(absent)}")
    return 0


def _png(D, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 6), dpi=100)
    im = ax.imshow(D, cmap='viridis', vmin=0.0, vmax=max(0.05, float(np.nanmax(D)) if D.size else 1.0),
                   interpolation='nearest', aspect='auto')
    ax.set_title(title, fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def render(res, an, a):
    R = res.records
    esc = html.escape
    m = res.meta
    st, gr, rs, aff = counts(res)

    def L(k):
        r = R.get(k)
        return esc(label(r)) + (' <small>(partial)</small>' if r and r['status'] == 'partial' else '') if r else esc(k)

    def table(head, rows):
        return ('<table><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in head) + '</tr>'
                + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows) + '</table>')

    def f4(x):
        return '' if x is None else f'{x:.4f}'

    def D(x, k):
        """One distance of a pair row, grey when it is read on a mass under --null, '-' for a land
        measure between two attacks that land nothing."""
        if k in LAND_MEASURES and x.get('empty'):
            return '<span class=faint title="both land nothing">-</span>'
        v = f4(x[k])
        return f'<span class=faint title="a mass under --null">{v}</span>' if k in x.get('faint', ()) else v

    def nb_cell(x):
        return (f"{L(x['key'])}{' <small>(kept apart by delivery)</small>' if x.get('gated') else ''} "
                f"{D(x, 'D_assoc')} / {D(x, 'D_rel')} / {D(x, 'D_now')} / {D(x, 'D_best')} / "
                f"{f4(x['D_idle'])} "
                f"x{x['full_ratio']} dFP {x['dFP']} droll {x['droll']}"
                + (' <small>' + esc(', '.join(x['tokens_not_shared'])) + '</small>' if x['tokens_not_shared'] else ''))
    out = ['<html><head><meta charset=utf-8><title>Attack similarity</title><style>'
           'body{font-family:sans-serif;margin:24px;max-width:1400px}table{border-collapse:collapse;margin-bottom:20px}'
           'td,th{border:1px solid #888;padding:3px 7px;font-size:13px}th{background:#ddd}small{color:#555}'
           '.faint{color:#aaa}h2{margin-top:32px}img{max-width:100%}</style></head><body>',
           '<h1>Attack similarity</h1>']
    # 1. build
    out.append('<h2>1. Builds, situations, how to read it</h2>')
    out.append(f"<p><b>Distance basis</b>: stats {esc(json.dumps(m['stats']))}, every attack wieldable and castable "
               f"(no character at RL {m['rl']} has it); catalysts {esc(', '.join(m['catalysts']))}; RL {m['rl']}; "
               f"{m['situations']} situations, W {m['W']:.3f}, n_eff {m['n_eff']:.1f}"
               f"{'' if not m.get('only') else '; subset: ' + esc(', '.join(m['only']))}. On it: "
               f"{esc(', '.join(BUILD_INDEPENDENT))} (sections 2, 3, 7 to 14). The shares they read are scale-free "
               "pressed at once and nearly so with a run-in, so they barely move with the build.</p>")
    out.append('<p><b>Attacker builds</b>: lines made from the game\'s curves at the run\'s RL, one per stat archetype '
               'the RL window\'s corpus has, plus two split lines whose main stat stops at '
               f"{SPLIT_CAP} and whose other points go to STR and DEX: VIG at the HP graph's knee, END the least that "
               'keeps medium roll with the weapons the archetype\'s corpus builds carry in their hands and the '
               'lightest armor at the melee poise floor, MND at the starting class\'s base (the model prices no FP '
               f"pool, so MND changes no net), every point left in the main stats up to {DAMAGE_CAP}, then STR and DEX; "
               'on the starting class that leaves the most points in the main stats. The corpus gives usage alone: '
               'which archetypes exist, how many builds each has, and the weight they carry. '
               '<b>Build-dependent</b>, read per build: '
               f"{esc(', '.join(BUILD_DEPENDENT))} (sections 4 to 6). A build uses an attack only when its base stats "
               'meet the carrier\'s or the spell\'s and its catalyst\'s requirements (whether talisman and great-rune '
               'boosts count is not established), on the opening group weapon it can wield whose skill damage, '
               'opening and follow-ups summed, is largest at its stats, at '
               'the affinity whose skill damage is largest at its stats, and casts at its own DEX. An attack no '
               'build can use is in no build\'s dominance; every scored attack is still priced at two lines that '
               'can use it, read off no build (<a href="#objective">section 15</a>).</p>'
               + table(['build', 'usage: corpus builds of the archetype', 'line', 'RL', 'made on', 'VIG: HP gain at, after',
                        'END: carried + armor of max load', 'MND: the Mind fit', 'top attack: paid share',
                        'starting classes that reach it', 'spells', 'skill openings', 'population'],
                       [[esc(b['name']), b['n'] or '-', esc(line_text(b['stats'])), b['rl']] + rule_cells(b)
                        + [esc(f"{label(b['view'].records[b['top']])}: {b['view'].records[b['top']]['fp_share']:.3f} "
                               f"({b['view'].records[b['top']]['fp']} FP)") if b.get('top') else '-',
                           esc(', '.join((b.get('reach') or {}).get('classes') or [])) or 'none',
                           f"{(b.get('usable') or {}).get('spells')} / {(b.get('usable') or {}).get('of_spells')}",
                           f"{(b.get('usable') or {}).get('skill_openings')} / "
                           f"{(b.get('usable') or {}).get('of_skill_openings')}",
                           len(b['population'])] for b in an['builds']]))
    out.append('<p><b>D_rel</b>: the share of two attacks\' combined outcome against a dodging defender (land share and '
               'punish chance, pressed at once and with the best option, in every situation) they do not have in '
               'common. 0 = the same profile; 0.5 = one lands half as often as the other in the same situations; 1 = '
               'nothing in common, or one never lands. <b>D_land</b>: the same on the land shares alone. '
               '<b>D_idle</b>: how alike they land on a defender who does not dodge (share of worth, and first-touch '
               f'frame within F = {res.F:.1f} frames). <b>D_assoc</b> = max(D_rel, D_land, {an["idle_rate"]} x D_idle), '
               'the distance every association reads: most of the dodging profile is the run-in, whose land share is '
               'one number wherever it is picked, so D_rel alone calls a shield bash and a glintblade volley alike; '
               'the idle term asks that they also reach the same distances at about the same frame. <b>D_contain</b> = '
               f'max(D_land, {an["idle_rate"]} x D_idle), for contains. <b>D_now</b> and <b>D_best</b>: the absolute '
               'weighted gap pressing at once and with the best option; small for any two attacks that land little. '
               'D_now, D_best, D_rel, D_land and D_assoc are grey when either side\'s mass (land mass for the last '
               'three) is under --null. All in [0, 1].</p>')
    out.append('<p>Groups by land mass (the weighted mean share of its worth an attack lands): <b>live</b> pressed at once '
               f'lands at least {a.null}; <b>runin_only</b> only the best option does; <b>nets_nothing</b> neither. Twins, '
               f'matches, contains, families and the yardstick take scored attacks that land at least {a.live} of their '
               f"worth: {len(an['eligible'])} here. A partial attack is scored on its hits with a reach model. Every "
               'association sits behind the <b>delivery gate</b>: an attack whose worth comes only from melee hits and '
               'one whose worth comes only from bullets never associate (the run-in land share is one number wherever '
               'it is picked, so a whip frame can land like a glintblade volley on the profiles alone). A '
               '<b>component twin</b> is one frame of X within the twin cut of a whole Y of the other kind by '
               'D_contain, the whole of X within the scale band of Y.</p>')
    out.append(f"<p>Thresholds: twin {a.twin}, cut {an['cut']}, live {a.live}, null {a.null}, tolerance "
               f"{an['delta']:.5f}, hp {a.hp}, scale band {a.scale_band}, k {a.k}, idle rate {an['idle_rate']}.</p>")
    if an['yardstick'] is not None:
        out.append(f"<p>Yardstick: the median D_assoc between a spell's tap and its charged cast is "
                   f"<b>{an['yardstick']:.4f}</b> over {an['yardstick_n']} spells: as alike as two casts of one spell.</p>")
    out.append('<h3>Sensitivity (D_assoc cuts)</h3><p>Whole twins and whole mutual-nearest pairs; the last column '
               'counts the pairs within the cut that the delivery gate keeps apart (a melee-only attack against a '
               'bullet-only one).</p>'
               + table(['cut', 'whole twin pairs', 'mutual nearest', 'families of 2+', 'largest', 'kept apart by delivery'],
                       [[x['cut'], x['twin_pairs'], x['mutual_nearest'], x['families'], x['largest'], x['gated']]
                        for x in an['sensitivity']]))
    if an['gated_near']:
        out.append('<p>Nearest pairs the delivery gate keeps apart (D_assoc / D_idle):</p>' + table(
            ['a', 'b', 'delivery', 'D_assoc', 'D_idle'],
            [[L(g['a']), L(g['b']), esc(' / '.join(g['delivery'])), f4(g['D_assoc']), f4(g['D_idle'])]
             for g in an['gated_near']]))
    out.append('<h3>Counts</h3>' + table(['status', 'n'], [[esc(k), v] for k, v in sorted(st.items())])
               + table(['kind', 'group', 'sub', 'n'], [[esc(str(x)) for x in (*k, v)] for k, v in sorted(gr.items(), key=str)]))
    if aff:
        out.append(f"<p>Affinities the model raised on, left out of the pick: {esc(', '.join(aff))}.</p>")
    # 2. matches, contains
    out.append('<h2>2. Cross-type matches and contains</h2>')
    ri = an['rests_on_idle']

    def rests(x):
        """The 'bound by' cell: which term of D_assoc (D_contain) binds, and the flat shares."""
        fl = x.get('flat') or [None, None]
        return (('D_idle' if x.get('bound') == 'idle' else 'land') + ('' if None in fl else
                f" <small>flat {fl[0]:g} / {fl[1]:g}: rests on D_idle</small>"))
    out.append('<p>Mutual nearest across the kinds within --twin: whole to whole by D_assoc, or through a component '
               '(a contains whose two whole worths are within the scale band), by D_contain. \'Bound by\' names the term '
               'of the distance that decides it; a pair <b>rests on D_idle</b> when that is the idle term and both '
               'sides land one flat run-in share wherever they land (a quarter: one roll direction of four; 3/14: one '
               'direction at six of seven timing points), so their land channels agree on one number whatever the '
               f"attacks are: {ri['matches']} of {ri['matches_of']} matches and {ri['twins']} of {ri['twins_of']} twin "
               'pairs here.</p>')
    out.append(table(['D', 'by', 'bound by', 'D_assoc', 'D_rel', 'D_land', 'D_idle', 'D_now', 'shared share', 'spell',
                      'skill', 'full ratio'],
                     [[f4(x['D']), 'whole' if x['by'] == 'whole' else esc(x['component'].rsplit('@', 1)[1]), rests(x),
                       f4(x['D_assoc']), f4(x['D_rel']), f4(x['D_land']), f4(x['D_idle']), f4(x['D_now']), x['overlap'],
                       L(x['spell']), L(x['skill']), x['full_ratio']] for x in an['matches']]))
    tk = an['twin_kind']
    cross = [p for p in an['twins'] if R[p[0]]['type'] != R[p[1]]['type']]
    out.append(f"<p>{len(an['twins'])} twin pairs: {sum(v['by'] == 'whole' for v in tk.values())} whole, "
               f"{sum(v['by'] == 'component' for v in tk.values())} through a component; {len(cross)} cross-type; "
               f"{len(an['same_skill_twins'])} are one skill on two carrier openings (JSON 'twins', "
               "'same_skill_twins').</p>")
    out.append('<h3>Cross-type twins</h3>' + table(
        ['a', 'b', 'by', 'bound by', 'D'], [[L(p[0]), L(p[1]), 'whole' if tk[p]['by'] == 'whole'
                                             else esc(tk[p]['component'].rsplit('@', 1)[1]), rests(tk[p]), f4(tk[p]['D'])]
                                            for p in cross]))
    out.append('<p>Contains: one frame of X lands like the whole of Y (D_contain, on the land shares and the idle '
               'defender); D_rel, which also reads the punish chance, is shown beside it: a frame inside a longer '
               'attack is punished on that attack\'s recovery.</p>')
    out.append(table(['X', 'contains Y', 'component', 'D_contain', 'D_land', 'D_idle', 'D_rel', 'full ratio'],
                     [[L(x['x']), L(x['y']), esc(x['component'].rsplit('@', 1)[1]), f4(x['D_contain']), f4(x['D_land']),
                       f4(x['D_idle']), f4(x['D_rel']), x['full_ratio']] for x in an['contains']]))
    # 3. effect families
    out.append('<h2>3. Effect families</h2><p>Tokens annotate; they never enter a distance.</p>')
    for tk, rows in an['effect'].items():
        def near(x):
            n_ = x['nearest']
            if not n_:
                return ''
            v = f"{n_['D']:.4f}"
            return f"{L(n_['key'])} {('<span class=faint>' + v + '</span>') if n_.get('faint') else v} ({n_['by']})"
        out.append(f'<h3>{esc(tk)} ({len(rows)})</h3>' + table(
            ['kind', 'attack', 'group', 'mean net', 'FP', 'carrier', 'nearest other kind'],
            [[x['type'], L(x['key']) if x['key'] in R else esc(x['name']), esc(str(x['group'])),
              '' if x['mean_net'] is None else x['mean_net'], x['fp'], esc(str(x['carrier'])), near(x)]
             for x in rows]))
    # 4. redundant, per attacker build
    out.append('<h2>4. Redundant (per attacker build)</h2><p><b>Build-dependent</b>: read at each attacker build\'s '
               'base stats, on the attacks it can wield or cast, with its own carriers and affinities (section 1). b is '
               'redundant when another attack nets at least as much in practically every situation at no more FP '
               '(spell over spell: no more slots), with a mean gain of at least -hp; both must net something on more '
               'than the tolerance. Robust first (the test also holds on one hit per event, the dominator\'s FP is '
               'exact and it is not counted whole over a long life: one hit per event does not discount a lingering '
               'area). Up to 3 maximal dominators each, the robust ones first and maximal among the robust edges.</p>')
    for b in an['builds']:
        Rb = b['view'].records

        def Lb(k, Rb=Rb):
            return esc(label(Rb[k])) if k in Rb else esc(k)
        red = b['report']['redundant']
        out.append(f"<h3>{esc(build_title(b))}: {len(red)} redundant, "
                   f"{sum(r['class'] == 'robust' for r in red)} robustly</h3>")
        out.append(table(['redundant', 'class', 'FP', 'dominators (robust)', 'dominator', 'gain', 'loss share', 'needs',
                          'blind to'],
                         [[Lb(r['b']), r['class'], Rb[r['b']]['fp'], f"{r['dominators']} ({r['robust_dominators']})",
                           '<br>'.join(Lb(e['a']) + f" <small>({e['class']}{', twin' if e['twin'] else ''})</small>"
                                       for e in r['maximal']),
                           '<br>'.join(f"{e['gain']:+.1f}" for e in r['maximal']),
                           '<br>'.join(f"{e['loss']:.4f}{' strict' if e['strict'] else ''}" for e in r['maximal']),
                           '<br>'.join(esc(e['needs'] or '') for e in r['maximal']),
                           '<br>'.join(esc(', '.join(e['blind'])) for e in r['maximal'])] for r in red]))
    # 5. frontier, per attacker build
    out.append('<h2>5. Frontier (per attacker build)</h2><p><b>Build-dependent.</b> Undominated and netting something, '
               'over robust edges and over every edge; mean net at the build\'s stats.</p>')
    for b in an['builds']:
        Rb = b['view'].records
        fr_all = {x['key'] for x in b['report']['frontier']}
        rows = sorted(b['report']['frontier_robust'], key=lambda x: -Rb[x['key']]['mean_net'])
        out.append(f"<h3>{esc(build_title(b))}: {len(rows)} over robust edges, {len(fr_all)} over every edge</h3>"
                   + table(['attack', 'mean net', 'FP', 'warnings', 'also undominated over every edge'],
                           [[esc(label(Rb[x['key']])), f"{Rb[x['key']]['mean_net']:.1f}", Rb[x['key']]['fp'],
                             esc(', '.join(x['warnings'])), 'yes' if x['key'] in fr_all else ''] for x in rows]))
        if b['no_fp']:
            out.append('<p>Scored but out of the population for costing no FP: '
                       + esc(', '.join(label(Rb[k]) for k in b['no_fp'])) + '.</p>')
    # 6. own variants and the spell rollup, per attacker build
    out.append('<h2>6. Own variants and the spell rollup (per attacker build)</h2><p><b>Build-dependent.</b></p>')
    for b in an['builds']:
        Rb = b['view'].records

        def Lb(k, Rb=Rb):
            return esc(label(Rb[k])) if k in Rb else esc(k)
        out.append(f"<h3>{esc(build_title(b))}</h3>" + table(
            ['dominator', 'dominated', 'class', 'gain', 'loss'],
            [[Lb(e['a']), Lb(e['b']), e['class'], f"{e['gain']:+.1f}", f"{e['loss']:.4f}"] for e in b['own']['edges']]))
        out.append(f"<p>Spells every variant of which is scored and robustly dominated ({len(b['rollup'])}):</p>" + table(
            ['spell', 'by'], [[esc(x['spell']), '<br>'.join(f"{Lb(k)}: {', '.join(Lb(d) for d in v)}"
                                                            for k, v in x['by'].items())] for x in b['rollup']]))
        out.append(f"<p>Dominated on their landing variants only ({len(b['rollup_part'])}): every scored variant that "
                   'nets something is robustly dominated, but another variant has no reach model, is partial or nets '
                   'nothing.</p>' + table(
                       ['spell', 'dominated', 'not dominated'],
                       [[esc(x['spell']), '<br>'.join(f"{Lb(k)}: {', '.join(Lb(d) for d in v)}" for k, v in x['by'].items()),
                         '<br>'.join(f"{esc(k)} ({esc(why)})" for k, why in x['not_dominated'])] for x in b['rollup_part']]))
    # 7. families
    sa = an['subcat']

    def pct(x):
        return '-' if x is None else f'{100 * x:.0f}%'
    agree = ('' if sa.get('ari') is None else
             f"Against Magic.subCategory1, over {sa['spells']} spells in {sa['clusters']} clusters ({sa['singletons']} "
             f"singletons, {sa['subcategories']} subcategories): the families of two or more spells are "
             f"{pct(sa['purity'])} pure; {pct(sa['same_inside'])} of the spell pairs inside a family share a "
             f"subcategory, against {pct(sa['same_base'])} of all spell pairs. The adjusted Rand index is "
             f"{sa['ari']:.3f}, held down by the singletons, which it counts as disagreeing pairs.")
    out.append(f"<h2>7. Families (UPGMA by D_assoc at {an['cut']}, behind the delivery gate)</h2><p>{agree} 'one "
               "parent': every member is a variant of one attack (siblings, which no pair list joins); 'one skill': "
               "one skill on several carriers.</p>")
    out.append(table(['size', 'mixed', 'one parent / skill', 'medoid', 'members', 'full spread', 'FP',
                      'largest D_assoc inside'],
                     [[len(f['members']), 'yes' if f['mixed'] else '',
                       'one parent' if f['one_parent'] else 'one skill' if f['one_skill'] else '',
                       L(f['medoid']), '<br>'.join(L(k) for k in f['members']),
                       f['full_spread'], f"{f['fp_range'][0]:g}-{f['fp_range'][1]:g}", f['height']] for f in an['families']]))
    # 8. neighbours
    out.append('<h2>8. Nearest neighbours (live)</h2><p>By D_assoc. Each cell: D_assoc / D_rel / D_now / D_best / '
               'D_idle, the full ratio, dFP, droll and the tokens the pair does not share.</p>')
    for nm, own_t, side in (('spell to spell', 'spell', 'own'), ('skill to skill', 'skill', 'own'),
                            ('spell to skill', 'spell', 'other'), ('skill to spell', 'skill', 'other')):
        rows = []
        for k, nb in an['neighbours'].items():
            if R[k]['type'] != own_t or R[k]['group'] != 'live':
                continue
            rows.append([L(k), nb['by'], '<br>'.join(nb_cell(x) for x in nb[side])])
        out.append(f'<h3>{nm}</h3>' + table(['attack', 'by', 'nearest'], rows))
    # 9. run-in only
    rows = [k for k in res.keys if R[k]['group'] == 'runin_only']
    out.append('<h2>9. Lands only after a run-in</h2>' + table(
        ['attack', 'land mass now / best', 'mean net', 'nearest by D_assoc'],
        [[L(k), f"{R[k]['land_mass_now']:.4f} / {R[k]['land_mass_best']:.4f}", f"{R[k]['mean_net']:.1f}",
          '<br>'.join(nb_cell(x) for x in (an['neighbours'].get(k) or {}).get('own', [])
                      + (an['neighbours'].get(k) or {}).get('other', []))] for k in rows]))
    # 10. nets nothing
    rows = [k for k in res.keys if R[k]['group'] == 'nets_nothing']
    out.append('<h2>10. Nets nothing</h2><p>D_now and D_rel say nothing about these (their profiles are nearly empty); '
               'the neighbours of an attack that is always evaded are by D_idle, among the attacks that reach an idle '
               'defender.</p>' + table(
                   ['attack', 'why', 'land mass best', 'reach share', 'nearest by D_idle'],
                   [[L(k), esc(R[k]['group_reason']), f"{R[k]['land_mass_best']:.4f}", f"{R[k]['reach']:.3f}",
                     '<br>'.join(f"{L(x['key'])} {x['D_idle']:.4f}" for x in (an['neighbours'].get(k) or {}).get('own', [])
                                 + (an['neighbours'].get(k) or {}).get('other', []))] for k in rows]))
    # 11. partial
    rows = [k for k in res.keys if R[k]['status'] == 'partial']
    out.append('<h2>11. Partial</h2><p>Scored on the hits that have a reach model; the left-out hits and their worth '
               'are not in its worth. A hit on a bullet the straight flight never launches is one of them (an on-hit '
               'child: the flight hits no character).</p>'
               + table(['attack', 'group', 'left out', 'left-out worth'],
                       [[L(k), esc(R[k]['group']), esc('; '.join(str(x) for x in R[k]['missing'])),
                         '' if R[k].get('missing_hp') is None else R[k]['missing_hp']] for k in rows]))
    rows = [k for k in res.keys if R[k].get('dropped')]
    out.append('<h3>Hits dropped: a bullet that launches only on water or never</h3><p>Not missing: there is no '
               'reach to model, so the attack keeps its status with these out of its worth.</p>'
               + table(['attack', 'status', 'dropped (frame or atk, bullet, launch condition, worth)', 'worth'],
                       [[L(k), R[k]['status'], esc('; '.join(str(x) for x in R[k]['dropped'])), R[k]['dropped_hp']]
                        for k in rows]))
    # 12. probes
    out.append('<h2>12. External probes</h2>' + table(
        ['a', 'b', 'why', 'D_assoc', 'D_rel', 'D_now', 'D_idle', 'NN rank (by)', 'twin', 'groups'],
        [[L(x['a']), L(x['b']), esc(', '.join(x['why'])), D(x, 'D_assoc'), D(x, 'D_rel'), D(x, 'D_now'), f4(x['D_idle']),
          '' if x['nn_rank'] is None else f"{x['nn_rank']} ({x['nn_by']})", 'yes' if x['twin'] else '',
          esc(' / '.join(x['groups']))] for x in an['probes']]))
    # 13. excluded
    out.append('<h2>13. Not in the matrices</h2>' + table(['kind', 'reason', 'n'], [[esc(k[0]), esc(k[1]), v] for k, v in
                                                                                     sorted(rs.items(), key=lambda kv: -kv[1])]))
    out.append(table(['key', 'name', 'reason'], [[esc(e['key']), esc(e['name']), esc(e['reason'])] for e in res.excluded]))
    # 14. heatmaps
    if not a.no_heatmaps:
        out.append('<h2>14. Heatmaps</h2><p>D_assoc over the attacks that land enough to associate, in UPGMA leaf '
                   'order; D_idle over every scored attack, those same attacks first in that order and the rest after '
                   'them in key order. The order is under each picture.</p>')
        idx = {k: i for i, k in enumerate(res.keys)}
        typ = {k: R[k]['type'] for k in res.keys}
        elig_order = an['order']
        scored_order = list(elig_order) + [k for k in res.keys if k not in set(elig_order) and R[k]['status'] == 'scored']
        for nm, Dm, order in (('D_assoc, attacks that land', an['matrices']['D_assoc'], elig_order),
                              ('D_idle, scored', an['matrices']['D_idle'], scored_order)):
            for bn, ta, tb in (('spell x spell', 'spell', 'spell'), ('skill x skill', 'skill', 'skill'),
                               ('spell x skill', 'spell', 'skill')):
                ra = [idx[k] for k in order if typ[k] == ta]
                rb = [idx[k] for k in order if typ[k] == tb]
                if not ra or not rb:
                    continue
                sub = np.asarray(Dm)[np.ix_(ra, rb)]
                out.append(f'<h3>{esc(nm)}: {bn}</h3><img src="{_png(sub, nm + ": " + bn)}">')
                out.append('<p><small>rows: ' + esc('; '.join(label(R[res.keys[i]]) for i in ra)) + '<br>columns: '
                           + esc('; '.join(label(R[res.keys[i]]) for i in rb)) + '</small></p>')
    # 15. objective evaluation
    out.append('<h2 id=objective>15. Objective evaluation (every scored attack)</h2><p>Read off no corpus build: '
               'each scored or partial attack priced as the attacker builds price theirs (mean net over the '
               'situations, at the line\'s stats, carrier and affinity), at two lines that can use it. '
               '<b>Adjusted</b>: the attacker build line that puts the most points where the attack scales (a '
               'skill: the share of its hits\' base attack each stat scales, read on each hit\'s AttackElementCorrect '
               'row at the affinity the distance basis scored; a spell: its school stat), '
               'every requirement it misses raised to the minimum and paid back from its highest damage stats, so '
               'its RL stays the line\'s. <b>Minimum</b>: the requirements met exactly on the base of the starting '
               'class that makes that the lowest RL. \'Builds\' counts the attacker builds whose population holds '
               'it.</p>')
    orows = []

    def adj_net(k):
        o = (R[k].get('objective') or {}).get('adjusted') or {}
        return o['mean_net'] if o.get('mean_net') is not None else -math.inf
    for k in sorted(R, key=lambda k: (-adj_net(k), k)):
        ob = R[k].get('objective') or {}
        cells = [L(k)]
        for nm in OBJECTIVE_LINES:
            o = ob.get(nm)
            if o is None:
                cells += ['', '']
                continue
            net = (f"{o['mean_net']:.1f}" if o.get('mean_net') is not None
                   else f"<small>{esc(str(o.get('status')))}{': ' + esc(o['reason']) if o.get('reason') else ''}</small>")
            src = (esc(o['from_line']) + (f" <small>{esc(objective_moves(o))}</small>" if o.get('raised') else '')
                   if o.get('from_line') else f"<small>on the {esc(str(o.get('from_class')))} base</small>")
            cells += [net, f"{esc(line_text(o['stats']))}; RL {o['rl']} {src}"]
        cells.append(len(priced_by(an, k)))
        orows.append(cells)
    out.append(table(['attack', 'adjusted mean net', 'adjusted line', 'minimum mean net', 'minimum line', 'builds'],
                     orows))
    out.append('</body></html>')
    return '\n'.join(out)


# ---------------------------------------------------------------------------------------------
# the command

def build_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--stats', default=DEFAULT_STATS, help=f'default {DEFAULT_STATS}')
    ap.add_argument('--catalyst', action='append', default=[],
                    help=f"repeatable; default {', '.join(DEFAULT_CATALYSTS)}")
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--jobs', type=int, default=None, help='forked workers; default min(8, CPUs - 1); 1 runs here')
    ap.add_argument('--no-wait', action='store_true',
                    help='exit 3 when another pooled run holds er-mechanics-pool-lock instead of waiting for it')
    ap.add_argument('--refresh', action='store_true', help='recompute even when the cache stamp matches')
    ap.add_argument('--out', help='HTML page')
    ap.add_argument('--json', help='association JSON (no vectors, no matrices)')
    ap.add_argument('--k', type=int, default=5, help='nearest neighbours per side')
    ap.add_argument('--twin', type=float, default=0.1,
                    help='D_assoc (contains: D_contain) at most this for twins, matches and contains (0.1: the pair '
                         'shares at least 90%% of its combined outcome)')
    ap.add_argument('--cut', type=float, default=None, help='family cut on D_assoc, default --twin')
    ap.add_argument('--idle-rate', type=float, default=IDLE_RATE,
                    help=f'weight of D_idle in D_assoc and D_contain, default {IDLE_RATE}: at --twin 0.1 a pair must '
                         'be within D_idle 0.2 on a defender who does not dodge')
    ap.add_argument('--live', type=float, default=0.05,
                    help='association floor: an attack takes part in twins, matches, contains, families and the '
                         'yardstick when it lands at least this share of its worth')
    ap.add_argument('--null', type=float, default=0.02,
                    help='group floor on land mass (live, runin_only, nets_nothing), and the mass under which D_now, '
                         'D_best and D_rel are greyed')
    ap.add_argument('--tolerance', type=float, default=None, help='loss share a dominator may have, default 1/n_eff')
    ap.add_argument('--hp', type=float, default=1.0)
    ap.add_argument('--scale-band', type=float, default=1.0, help='twins: full worth within a factor 2^this')
    ap.add_argument('--only', action='append', default=[], help='score only spells and skills of this name; repeatable')
    ap.add_argument('--line', action='append', default=[],
                    metavar='NAME:vig=..,mnd=..,end=..,str=..,dex=..,int=..,fth=..,arc=..[,poise=..]',
                    help='one more attacker build at this spread, beside the made lines; repeatable. All eight '
                         'attributes are given and none is traded; it wears the lightest armor set at `poise` '
                         '(default the run\'s melee poise floor; 0 wears none), no HP piece and no talismans '
                         '(`given_builds`). It adds its own nets and moves no other number (the objective lines '
                         'start from the made lines alone), but the run\'s stamp holds the builds, so a run with '
                         'it keeps its own cache and sets')
    ap.add_argument('--show', action='append', default=[],
                    help="print one attack's associations from the cache (or, with --only, from that subset); repeatable")
    ap.add_argument('--include-blacklisted', action='store_true')
    ap.add_argument('--sets', type=int, default=None, metavar='N',
                    help='read only the first N attack sets of their order and stop: their seconds and memory '
                         'go to the timing log and its trail, the sets are kept for a full run to resume from, '
                         'and no result is cached')
    ap.add_argument('--mem-stop-gib', type=float, default=MEM_STOP_GIB,
                    help=f'stop the attack set started last when available memory falls under this, and deal it '
                         f'again later (default {MEM_STOP_GIB:g}); 0 never stops one')
    ap.add_argument('--mem-start-gib', type=float, default=MEM_START_GIB,
                    help=f'start an attack set only with this much memory available (default {MEM_START_GIB:g})')
    ap.add_argument('--no-heatmaps', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--keep-going', action='store_true',
                    help='with --selftest: run every check past a failure, print each, and exit 1 at the end')
    ap.add_argument('--selftest-estimate', action='store_true',
                    help="run only the checks of the attack sets' counter weights and still-running lines (31e), "
                         'which need no game data, and exit 1 when one fails')
    ap.add_argument('--selftest-reasons', action='store_true',
                    help='run only the checks of why a spell with no damaging hit is excluded (37): '
                         'Carian Retaliation, the status-only mists, the buffs, one process, and exit 1 when '
                         'one fails')
    ap.add_argument('--selftest-status', action='store_true',
                    help='run only the status build-up checks (38): the proc rule on a made-up pool, then Stars of '
                         'Ruin, Bloody Slash and Freezing Mist on every eighth situation, one process, and exit 1 '
                         'when one fails')
    ap.add_argument('--selftest-status-groups', action='store_true',
                    help='run only the criteria of checks 8 and 9 that status build-up can move, on Zamor Ice '
                         'Storm, Bloody Slash and Starcaller Cry scored alone (--jobs, default 1); with '
                         '--selftest-status, both on one context; exit 1 when one fails')
    ap.add_argument('--selftest-lines', action='store_true',
                    help='run only the --line checks (36): the made-up spreads, then the Glintstone Pebble sorcery '
                         'with and without a spread on an eighth of the situations (--jobs, default 1), and exit 1 '
                         'when one fails')
    ap.add_argument('--timing', nargs='*', default=None, metavar='RUN',
                    help='compare two runs of the timing log and exit: none named, the newest and the newest '
                         'earlier run of its scope; one, that run and the newest earlier run of its scope; two, A '
                         'and B. A run is its line number in the log, or negative from the end')
    ap.add_argument('--timing-log', default=None,
                    help=f'the timing log every run appends to and --timing reads, default {TIMING_LOG}')
    return ap, ap.parse_args(argv)


#: The full run, for the message --show gives when the cache it reads is missing: this checkout's
#: own absolute path.
FULL_RUN = (f'python3 {HERE / "er-mechanics-attack-similarity.py"} '
            '--out <dir>/attack-similarity.html --json <dir>/attack-similarity.json')
#: The full run's wall time (`MEASURED`): before the attacker builds (version 3), 1,034 s with 8
#: workers and 1,851 s beside other load; `pooled` pins every run's workers to the same cores
#: (`er-mechanics-ash-interrupt`), so two pooled runs at once share them. Version 4 scores every
#: attacker build as well; its time is in the doc's run log.
def full_run_time(path=None):
    """How long the newest full run in the timing log took, which `--show` cites when the cache
    misses: a measured figure or a statement that there is none. The string this replaced said
    "17 min or more with 8 workers"; r9 took 3h31m and r10 was stopped after more than 3h57m of
    attack sets (2026-11-11)."""
    full = [r for r in timing_runs(path) if r.get('scope') == 'full' and r.get('wall') is not None]
    if not full:
        return 'no full run is in the timing log yet, so its time is unmeasured'
    r = full[-1]
    return (f"the last full run, version {r.get('version')} on {str(r.get('when'))[:10]} with {r.get('jobs')} "
            f"workers, took {PROG.span(r['wall'])} by the timing log")


def run(a, log=print, timing=None):
    """(`Result`, analysis) for the arguments `a`, from the cache when its stamp matches; None with
    --show (and no --only) when it does not, since --show reads the cache and never computes.
    Each step is a lap of `timing`, and what the run is goes in its facts. With no `timing`, each
    set's seconds and memory still go to `trail_path()`: a caller that imported `run` dropped them,
    and a run the harness then stopped for low memory left nothing saying what held it (2026-11-17)."""
    timing = timing or Timing(trail=trail_path())
    stats = S.parse_stats(a.stats)
    c = Context(stats, a.catalyst or DEFAULT_CATALYSTS, a.rl, a.include_blacklisted,
                lines=[parse_line(x) for x in getattr(a, 'line', None) or ()])
    jobs = a.jobs if a.jobs is not None else default_jobs()
    only = {T._norm(x) for x in a.only} or None
    sets_cap = getattr(a, 'sets', None)
    timing.facts.update(scope='sample' if sets_cap is not None else 'only' if only else 'full',
                        only=sorted(only) if only else None, jobs=jobs, version=SIMILARITY_VERSION)
    timing.lap('context')
    res = None
    h16 = inputs = None
    _ai()
    # One pooled run at a time across every checkout (`er-mechanics-pool-lock`): each compute below
    # holds it while its workers run; --no-wait exits 3 instead of waiting for another run.
    what = (f"attack-similarity {'--only ' + ','.join(sorted(only)) if only else 'full'}"
            f"{f' --sets {sets_cap}' if sets_cap is not None else ''} --jobs {jobs}")
    wait = not getattr(a, 'no_wait', False)
    guard = MemoryGuard(getattr(a, 'mem_stop_gib', MEM_STOP_GIB) * 2**30,
                        getattr(a, 'mem_start_gib', MEM_START_GIB) * 2**30,
                        log=lambda msg: log(msg, file=sys.stderr))
    if sets_cap is not None and only:
        # A sample of a subset has no result to cache and no sets a full run reads.
        timing.mark_code()
        with pool_held(jobs, what, wait):
            compute(c, only, jobs, log, timing, sets_cap=sets_cap, guard=guard)
        return None
    if only:
        # A subset keeps its sets too, under the stamp of everything they depend on (the members
        # cache as it is, fresh or not, since a subset may run on a stale one): the kit run of
        # 2026-11-18 was stopped by its memory guard at 139 of 218 sets and, keeping none, lost them.
        h16, inputs = stamp(stats, c.catalysts, a.rl, a.include_blacklisted, c.situations, c.strikes, c.dfn,
                            hks=hks_stamp(c.A), builds=c.builds, members=members_stamp())
        timing.facts['stamp'] = h16
        timing.mark_code(inputs['code'])
        if a.refresh:
            # --refresh recomputes: a subset's kept sets are read no more than a full run's are.
            log(f'attack similarity: --refresh, recomputing the subset (stamp {h16})', file=sys.stderr)
            shutil.rmtree(set_dir(a.rl, h16).with_suffix('.only-sets'), ignore_errors=True)
    else:
        h16, inputs = stamp(stats, c.catalysts, a.rl, a.include_blacklisted, c.situations, c.strikes, c.dfn,
                            hks=hks_stamp(c.A), builds=c.builds, members=require_fresh_members())
        timing.facts['stamp'] = h16
        timing.mark_code(inputs['code'])
        timing.lap('stamp')
        if sets_cap is not None:
            # A sample's sets are kept where a full run of this stamp resumes from.
            with pool_held(jobs, what, wait):
                compute(c, None, jobs, log, timing, sets_cap=sets_cap, checkpoint=set_dir(a.rl, h16), guard=guard)
            return None
        jp, _ = cache_paths(a.rl, h16)
        if a.refresh and not a.show:
            log(f'attack similarity: --refresh, recomputing ({jp.name})', file=sys.stderr)
            shutil.rmtree(set_dir(a.rl, h16), ignore_errors=True)
        else:
            res = load_cache(a.rl, h16, c.F, log=log)
            timing.lap('cache read')
            if res is None:
                log(f'attack similarity: no cache with stamp {h16}: {miss_reason(a.rl, h16, inputs)}', file=sys.stderr)
                if a.show:
                    log(f'attack similarity: --show reads the cache; make it first ({full_run_time()}, '
                        f'in the background): {FULL_RUN}', file=sys.stderr)
                    return None
                log('attack similarity: computing', file=sys.stderr)
    timing.facts['cache'] = 'computed' if res is None else 'hit'
    if res is None:
        # The long sets go first by the seconds the trail measured (`set_order`); a --sets sample
        # above keeps the shuffle, so its sets stay a random sample.
        with pool_held(jobs, what, wait):
            res = compute(c, only, jobs, log, timing,
                          checkpoint=set_dir(a.rl, h16).with_suffix('.only-sets' if only else '.sets'),
                          measured=measured_seconds(trail_path(a.timing_log)), guard=guard)
        an = analyze(res, a)
        timing.lap('analyze')
        if not only:
            # The associations and the dominance stored beside the profiles are those of this run's
            # thresholds, which are stored with them; a later run recomputes both from the profiles.
            save_cache(res, h16, a.rl, {'tokens': sorted(an['effect']), '_matrices': an['matrices'],
                                        'thresholds': thresholds(an, a),
                                        'associations': {k: an[k] for k in ('matches', 'twins', 'contains', 'families')},
                                        'dominance': {b['name']: {'edges': b['dominance']['edges'], **b['report']}
                                                      for b in an['builds']}},
                       inputs=inputs)
            # The cache holds every set now.
            shutil.rmtree(set_dir(a.rl, h16), ignore_errors=True)
            timing.lap('cache write')
    else:
        an = analyze(res, a)
        timing.lap('analyze')
    return res, an


def main(argv=None):
    ap, a = build_args(argv)
    if a.selftest:
        return selftest(a.timing_log, a.keep_going)
    if a.selftest_estimate:
        failed = []

        def check(name, ok):
            print(f'{"ok" if ok else "FAIL"} {name}', file=sys.stderr, flush=True)
            if not ok:
                failed.append(name)
        selftest_estimate(check)
        selftest_form_stop(check)
        return 1 if failed else 0
    if a.selftest_reasons:
        failed = []

        def check(name, ok):
            print(f'{"ok" if ok else "FAIL"} {name}', file=sys.stderr, flush=True)
            if not ok:
                failed.append(name)
        selftest_no_hit(check, Context(S.parse_stats(DEFAULT_STATS)))
        return 1 if failed else 0
    if a.selftest_status:
        failed = []

        def check(name, ok):
            print(f'{"ok" if ok else "FAIL"} {name}', file=sys.stderr, flush=True)
            if not ok:
                failed.append(name)
        cs_ = Context(S.parse_stats(DEFAULT_STATS))
        selftest_status(check, cs_)
        if a.selftest_status_groups:
            selftest_status_groups(check, cs_, a.jobs or 1)
        return 1 if failed else 0
    if a.selftest_status_groups:
        failed = []

        def check(name, ok):
            print(f'{"ok" if ok else "FAIL"} {name}', file=sys.stderr, flush=True)
            if not ok:
                failed.append(name)
        selftest_status_groups(check, Context(S.parse_stats(DEFAULT_STATS)), a.jobs or 1)
        return 1 if failed else 0
    if a.selftest_lines:
        failed = []

        def check(name, ok):
            print(f'{"ok" if ok else "FAIL"} {name}', file=sys.stderr, flush=True)
            if not ok:
                failed.append(name)
        selftest_lines_synthetic(check)
        selftest_lines_run(check, Context(S.parse_stats(DEFAULT_STATS)), a.jobs or 1)
        return 1 if failed else 0
    if a.timing is not None:
        ra, rb = timing_pick(timing_runs(a.timing_log), a.timing)
        if ra is None or rb is None:
            print(f'attack similarity: --timing{"".join(" " + s for s in a.timing)}: no such pair of runs in '
                  f'{a.timing_log or TIMING_LOG} (with no run named, the newest and the newest earlier run of its '
                  f'scope)', file=sys.stderr)
            return 2
        return timing_report(ra, rb)
    timing = Timing(out=sys.stderr, trail=trail_path(a.timing_log))
    timing.facts['argv'] = sys.argv[1:] if argv is None else list(argv)
    got = run(a, timing=timing)
    if a.sets is not None:
        timing.record(a.timing_log)
        print(f'attack similarity: {timing.line()}; logged in {a.timing_log or TIMING_LOG}, each set\'s seconds and '
              f'memory in {trail_path(a.timing_log)}', file=sys.stderr)
        return 0
    if got is None:
        return 2
    res, an = got
    rc = 0
    if a.show:
        for nm in a.show:
            rc |= print_show(res, an, nm)
        timing.lap('show')
    else:
        if a.json:
            # The run's own timing so far: the stages up to here, the writes after it are in the log.
            Path(a.json).write_text(json.dumps({**json_doc(res, an, a), 'timing': {
                'wall': round(timing.clock() - timing.start, 3), 'stages': timing.stages}},
                default=_json_default, indent=1))
            print(f'wrote {a.json}', file=sys.stderr)
            timing.lap('json')
        if a.out:
            Path(a.out).write_text(render(res, an, a))
            print(f'wrote {a.out}', file=sys.stderr)
            timing.lap('page')
        else:
            print_summary(res, an)
            timing.lap('summary')
    timing.record(a.timing_log)
    print(f'attack similarity: {timing.line()}; logged in {a.timing_log or TIMING_LOG} (compare: --timing)',
          file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------------------------
# selftest

#: The subset the selftest scores: the spec's names, the native carriers of ashes with no
#: obtainable gem (Firebreather, Torch Attack, Buckler Parry), and the live negative controls of
#: the round-2 known-pairs pass: melee skills that D_rel alone called twins or matches of
#: projectile spells (Roaring Bash, Sword Dance, Spinning Guillotine, Red Bear Hunt against the
#: Glintblade Phalanx spell, Radagon's Rings of Light, Triple Rings of Light, Magic Glintblade).
SELFTEST_NAMES = ('Zamor Ice Storm', 'Glintblade Phalanx', 'Glintstone Pebble', 'Gravitas', "Alabaster Lords' Pull",
                  "Onyx Lords' Repulsion", 'Starcaller Cry', 'Gravity Well', 'Collapsing Stars', 'Gravitational Missile',
                  'Rock Sling', "Rellana's Twin Moons", 'Carian Slicer', "Rykard's Rancor", 'Death Lightning',
                  'Swarm of Flies', "Gelmir's Fury", "Siluria's Woe", 'Briars of Sin', 'Bloody Slash',
                  'Firebreather', 'Torch Attack', 'Buckler Parry', 'Roaring Bash', 'Sword Dance',
                  "Radagon's Rings of Light", 'Spinning Guillotine', 'Triple Rings of Light', 'Red Bear Hunt',
                  'Magic Glintblade', 'Lifesteal Fist', 'Poison Mist', 'Deadly Poison Spray',
                  # Round 3: two follow-ups on one clip (Stamp), hits on bullets that never fly
                  # (Roiling Magma's on-hit child, Honed Bolt's water bullet), and the melee frame
                  # the profiles alone made contain a glintblade volley.
                  'Stamp (Sweep)', 'Stamp (Upward Cut)', 'Roiling Magma', 'Honed Bolt', 'Painful Strike',
                  'Carian Phalanx', 'Watchful Spirit',
                  # Round 5: a charged skill, its early release a variant of its own; a skill whose
                  # carrier's own status its melee hits apply.
                  'Carian Grandeur', "Bloodhound's Finesse",
                  # The negative controls picked from the 2026-11-15 full run (`NEGATIVE_CONTROLS`).
                  'Familial Rancor', 'Rain of Fire', 'Devourer of Worlds', "Loretta's Mastery",
                  'Cannon of Haima', 'Collapsing Stars',
                  # Check 32's lookalike group (in the set since the r7 controls).
                  'Star Shower', 'Glintstone Stars',
                  # The objective evaluation: skills no attacker build of the 2026-11-10 full run r5
                  # could use (`OBJECTIVE_NAMES`).
                  'Tremendous Phalanx', 'Rancor Slash', 'Great Oracular Bubble')
#: Skills with no per-build net in the 2026-11-10 full run r5 (no attacker line met their carriers'
#: requirements): Tremendous Phalanx (Moonrithyll's Knight Sword), Rancor Slash (Spirit Sword,
#: Spirit Glaive), Great Oracular Bubble (Envoy's Greathorn, STR 28 / FTH 24). The selftest checks
#: each gets an objective net.
OBJECTIVE_NAMES = ('Tremendous Phalanx', 'Rancor Slash', 'Great Oracular Bubble')
#: The live negative controls: (skill name, its carrier, its follow-up button or None) against
#: (spell name, variant). Each pair is within --twin (0.1) on D_rel alone and past it on D_assoc,
#: with no twin, match or contains between them, on the 2026-11-15 full run (version 17, stamp
#: 4a87d5408c1fba25): D_rel 0.040 / 0.045 / 0.088 / 0.092, D_assoc 0.308 / 0.365 / 0.697 / 0.429.
#: That run had 8 such pairs over 3 skills; these are the two nearest of each of Familial Rancor and
#: Devourer of Worlds, of four different spells (bullets on both sides). The eighth, Carian
#: Sovereignty's L2 tap follow-up against Rock Sling, is the only melee one, at D_rel 0.0995, on
#: the edge. The r9 set (Charge Forth [Short Spear] / Cherishing Fingers tap, Charge Forth
#: [Torchpole] / Tibia's Summons cast, Tongues of Fire / Magma Shot tap, Spinning Weapon /
#: Collapsing Stars tap) was at D_rel 0.587 / 0.742 / 0.735 / 0.710 in the version 17 selftest.
NEGATIVE_CONTROLS = ((('Familial Rancor', 'Family Heads', None), ('Rain of Fire', 'tap')),
                     (('Devourer of Worlds', "Devourer's Scepter", None), ("Loretta's Mastery", 'charged')),
                     (('Familial Rancor', 'Family Heads', None), ('Cannon of Haima', 'charged')),
                     (('Devourer of Worlds', "Devourer's Scepter", None), ('Great Oracular Bubble', 'tap')))
#: The round-2 controls (each within D_rel 0.1 in the round-2 full run). Since the launches are
#: placed and the caught defender walks out (bd er-effects-rs-doax / er-effects-rs-n7dd,
#: 2026-11-10) none of them is within --twin on D_rel (0.14-0.73), so they no longer test that
#: D_idle keeps a D_rel-close pair apart; they are still checked to stay apart.
APART_CONTROLS = ((('Roaring Bash', 'Golden Lion Shield', None), ('Glintblade Phalanx', 'cast')),
                  (('Sword Dance', 'Hefty Scimitar', None), ("Radagon's Rings of Light", 'charged')),
                  (('Spinning Guillotine', 'Putrescence Cleaver', 'r2'), ('Triple Rings of Light', 'cast')),
                  (('Red Bear Hunt', "Red Bear's Claw", None), ('Magic Glintblade', 'tap')),
                  (('Red Bear Hunt', "Red Bear's Claw", None), ('Magic Glintblade', 'charged')))


def _wpgma(D):
    """Weighted average linkage (WPGMA): the merged row is the plain mean of the two rows. Only the
    selftest uses it, to show `upgma` is not this."""
    n = len(D)
    M = np.array(D, float)
    np.fill_diagonal(M, np.inf)
    ids, active, out = list(range(n)), np.ones(n, bool), []
    for k in range(n - 1):
        sub = np.where(active[:, None] & active[None, :], M, np.inf)
        i, j = sorted(np.unravel_index(int(np.argmin(sub)), sub.shape))
        out.append((ids[i], ids[j], float(M[i, j])))
        row = (M[i] + M[j]) / 2.0
        M[i, :], M[:, i] = row, row
        M[i, i] = np.inf
        M[j, :], M[:, j] = np.inf, np.inf
        active[j] = False
        ids[i] = n + k
    return out


def selftest_synthetic(check):
    """The checks that need no game data, run first so a broken distance or linkage fails within
    seconds. Each closed form below depends on the situation weights: dropping them, or putting
    them on the wrong cells, changes the answer."""
    rng = np.random.default_rng(20261109)
    F = 10.5
    # The cast's own HP cost (version 13): Bloody Slash's 5% of max HP + 50 at a 2000 HP line is
    # 150 off every net; a cast that takes nothing, and a component, pay nothing.
    bs = hp_cost_of({'pct': 5.0, 'flat': 50, 'rows': [{'id': 1763}], 'unpriced': []})
    check('HP cost: 5% + 50 at 2000 max HP is 150 a cast; none, and no cost carried, are 0',
          (hp_charge({'hp_cost': bs, 'max_hp': 2000.0}), hp_charge({'max_hp': 2000.0}),
           hp_cost_of({'pct': 0.0, 'flat': 0, 'rows': [], 'unpriced': []})) == (150.0, 0.0, None)
          and 'hp_cost' in profile_inputs({'geo': None, 'value': {}, 'value_one': {}, 'roll': 0, 'hp_cost': bs}))
    # 4. Distance properties on synthetic profiles.
    S_ = 50
    w = rng.random(S_) + 0.1
    W = float(w.sum())
    L3, P3 = rng.random((3, S_)), rng.random((3, S_)) * 0.5
    D3 = d_now_rows(L3, P3, L3, P3, w, W)
    check('4. D_now: zero on itself, symmetric, triangle inequality, within [0, 1]',
          np.allclose(np.diag(D3), 0) and np.allclose(D3, D3.T) and D3[0, 2] <= D3[0, 1] + D3[1, 2] + 1e-12
          and D3[1, 0] <= D3[1, 2] + D3[2, 0] + 1e-12 and D3.min() >= 0 and D3.max() <= 1)
    one = d_now_rows(np.full((1, S_), 0.2), np.zeros((1, S_)), np.full((1, S_), 1.0), np.zeros((1, S_)), w, W)
    check('4. D_now of land 0.2 against 1.0 with no punish is 0.4', abs(one[0, 0] - 0.4) < 1e-12)
    Bx = np.full((2, S_), 0.3)
    Tx = np.vstack([np.full(S_, np.inf), np.full(S_, 20.0)])
    Dx = d_idle_rows(Bx, Tx, Bx, Tx, w, W, F)
    check('4. D_idle with one side never touching and equal base share is 0.5', abs(Dx[0, 1] - 0.5) < 1e-12
          and abs(Dx[0, 0]) < 1e-12 and abs(Dx[1, 0] - 0.5) < 1e-12)
    Tf = np.vstack([np.full(S_, 20.0), np.full(S_, 20.0 + F)])
    Df = d_idle_rows(Bx, Tf, Bx, Tf, w, W, F)
    check(f'4. D_idle with first touches F = {F:.1f} frames apart at equal base share is 0.25',
          abs(Df[0, 1] - 0.25) < 1e-12 and abs(Df[1, 0] - 0.25) < 1e-12)
    T3 = np.vstack([np.full(S_, 20.0), np.full(S_, 20.0 + 3 * F)])
    D3f = d_idle_rows(Bx, T3, Bx, T3, w, W, F)
    check('4. D_idle with first touches 3F apart is 0.5 x 3F / 4F = 0.375 (the timing term dt / (dt + F), not '
          'a capped ramp, which gives 0.5)', abs(D3f[0, 1] - 0.375) < 1e-12)
    pn = d_now_rows(np.full((1, S_), 0.5), np.full((1, S_), 0.3), np.full((1, S_), 0.5), np.full((1, S_), 0.7), w, W)
    check('4. D_now of equal land with punish chances 0.3 and 0.7 is 0.2 (the punish term counts)',
          abs(pn[0, 0] - 0.2) < 1e-12)
    # Weighted closed forms: 10 situations, the first weighing 6 (or 9) and the others 1.
    w6 = np.array([6.0] + [1.0] * 9)
    W6 = float(w6.sum())                            # 15
    w9 = np.array([9.0] + [1.0] * 9)
    W9 = float(w9.sum())                            # 18
    e0, e1, z10 = np.eye(10)[0][None, :], np.eye(10)[1][None, :], np.zeros((1, 10))
    check('4. D_now weighs each situation: landing on the heavy one only is 9 / 36, on a light one 1 / 36',
          abs(d_now_rows(z10, z10, e0, z10, w9, W9)[0, 0] - 0.25) < 1e-12
          and abs(d_now_rows(z10, z10, e1, z10, w9, W9)[0, 0] - 1 / 36) < 1e-12)
    # D_rel's cells are (channel, situation), channel-major: each cell takes its situation's weight.
    ua, ub = np.zeros(40), np.zeros(40)
    ua[0 * 10 + 1], ua[2 * 10 + 0], ub[0 * 10 + 1] = 1.0, 1.0, 1.0
    va, vb = np.zeros(40), np.zeros(40)
    va[0 * 10 + 0], va[2 * 10 + 1], vb[0 * 10 + 0] = 1.0, 1.0, 1.0
    check('4. D_rel weighs each cell by its situation: the unshared cell on the heavy situation is 9 / 10 of '
          'the pair, on a light one 1 / 10',
          abs(d_rel_rows(ua, ub, w9)[0, 0] - 0.9) < 1e-12 and abs(d_rel_rows(va, vb, w9)[0, 0] - 0.1) < 1e-12)
    U8 = rng.random((8, 4 * S_)) * (rng.random((8, 4 * S_)) < 0.5)

    def oracle(x, y):
        num = sum(w[s] * abs(x[ch * S_ + s] - y[ch * S_ + s]) for ch in range(4) for s in range(S_))
        den = sum(w[s] * max(x[ch * S_ + s], y[ch * S_ + s]) for ch in range(4) for s in range(S_))
        return num / den if den > 0 else 0.0
    D8 = d_rel_rows(U8, U8, w)
    check('4. D_rel equals a cell-by-cell loop over (channel, situation) on 8 random weighted profiles',
          max(abs(D8[i, j] - oracle(U8[i], U8[j])) for i in range(8) for j in range(8)) < 1e-12)
    Bi, Ti = np.full((2, 10), 0.3), np.vstack([np.full(10, 20.0), np.where(np.arange(10) == 0, 20.0, np.inf)])
    Ti2 = np.vstack([np.full(10, 20.0), np.where(np.arange(10) == 0, np.inf, 20.0)])
    Bb = np.vstack([np.zeros(10), np.where(np.arange(10) == 0, 1.0, 0.0)])
    Tb = np.full((2, 10), 20.0)
    check('4. D_idle weighs each situation: touching on the heavy one only is 0.5 x 9 / 15, on every light one '
          'only 0.5 x 6 / 15, a base share apart on the heavy one 0.5 x 6 / 15',
          abs(d_idle_rows(Bi[[0]], Ti[[0]], Bi[[1]], Ti[[1]], w6, W6, F)[0, 0] - 0.3) < 1e-12
          and abs(d_idle_rows(Bi[[0]], Ti2[[0]], Bi[[1]], Ti2[[1]], w6, W6, F)[0, 0] - 0.2) < 1e-12
          and abs(d_idle_rows(Bb[[0]], Tb[[0]], Bb[[1]], Tb[[1]], w6, W6, F)[0, 0] - 0.2) < 1e-12)
    # D_rel: a metric in [0, 1], relative to the pair's mass.
    U = rng.random((30, 4 * S_)) * (rng.random((30, 4 * S_)) < 0.4)
    Dr = d_rel_rows(U, U, w)
    tri = (Dr[:, :, None] <= Dr[:, None, :] + Dr.T[None, :, :] + 1e-12).all()
    check('4. D_rel: zero on itself, exactly symmetric, triangle inequality on every triple of 30 profiles, within [0, 1]',
          np.abs(np.diag(Dr)).max() < 1e-12 and np.array_equal(Dr, Dr.T) and tri and Dr.min() >= 0 and Dr.max() <= 1)
    # Each lands 0.02 of its worth pressing at once on its own half of the situations, nothing else.
    half = np.arange(S_) < S_ // 2
    lo_a, lo_b = np.zeros(4 * S_), np.zeros(4 * S_)
    lo_a[:S_], lo_b[:S_] = np.where(half, 0.02, 0.0), np.where(half, 0.0, 0.02)
    check('4. two low-mass profiles on disjoint halves: D_now 0.02 or less, D_rel 1 (the round-1 twin artifact)',
          d_now_rows(lo_a[None, :S_], lo_a[None, S_:2 * S_], lo_b[None, :S_], lo_b[None, S_:2 * S_], w, W)[0, 0] <= 0.02
          and abs(d_rel_rows(lo_a, lo_b, w)[0, 0] - 1.0) < 1e-12)
    check('4. D_rel of a profile and 0.5 x itself is 0.5; of a profile and an empty one 1; of two empty ones 0',
          abs(d_rel_rows(U[0], 0.5 * U[0], w)[0, 0] - 0.5) < 1e-12
          and d_rel_rows(U[0], np.zeros_like(U[0]), w)[0, 0] == 1.0
          and d_rel_rows(np.zeros_like(U[0]), np.zeros_like(U[0]), w)[0, 0] == 0.0)
    # D_land and D_assoc: a shared punish chance does not make two attacks alike.
    S2 = 1208
    w2 = np.ones(S2)
    hl = np.arange(S2) < S2 // 2
    pa = np.concatenate([np.where(hl, 0.1, 0.0), np.full(S2, 0.95), np.zeros(S2), np.zeros(S2)])
    pb = np.concatenate([np.where(hl, 0.0, 0.1), np.full(S2, 0.95), np.zeros(S2), np.zeros(S2)])
    dr_ = d_rel_rows(pa, pb, w2)[0, 0]
    dl_ = d_rel_rows(np.concatenate([pa[:S2], pa[2 * S2:3 * S2]]), np.concatenate([pb[:S2], pb[2 * S2:3 * S2]]), w2)[0, 0]
    da_ = assoc(np.array([[0.0, dr_], [dr_, 0.0]]), np.array([[0.0, dl_], [dl_, 0.0]]), np.zeros((2, 2)), IDLE_RATE)
    check(f'4. land on disjoint halves with a shared punish chance 0.95: D_rel {dr_:.4f} <= 0.1, but D_land 1 and '
          'D_assoc 1, so no twin', dr_ <= 0.1 and abs(dl_ - 1.0) < 1e-12 and abs(da_[0, 1] - 1.0) < 1e-12
          and twin_pairs(da_, ['a', 'b'], [True, True], np.array([1.0, 1.0]), ['a', 'b'], 0.1, 1.0) == [])
    # D_assoc: a pseudometric (a max of a metric, a metric and a scaled pseudometric).
    UL = U[:, :2 * S_]
    B30, T30 = rng.random((30, S_)), np.where(rng.random((30, S_)) < 0.3, np.inf, rng.random((30, S_)) * 60)
    Da = assoc(Dr, d_rel_rows(UL, UL, w), d_idle_rows(B30, T30, B30, T30, w, W, F), IDLE_RATE)
    check('4. D_assoc: zero on itself, symmetric, triangle inequality on every triple of 30 profiles, within [0, 1]',
          np.abs(np.diag(Da)).max() < 1e-12 and np.allclose(Da, Da.T, atol=1e-12)
          and (Da[:, :, None] <= Da[:, None, :] + Da.T[None, :, :] + 1e-12).all() and Da.min() >= 0 and Da.max() <= 1)
    # 15. UPGMA.
    pts = np.array([0.0, 1.0, 5.0, 7.0])
    mg = upgma(np.abs(pts[:, None] - pts[None, :]))
    check('15. UPGMA on 0, 1, 5, 7: merges (0,1) at 1, (2,3) at 2, then at 5.5; two clusters at a cut of 3',
          [(x[0], x[1], x[2]) for x in mg] == [(0, 1, 1.0), (2, 3, 2.0), (4, 5, 5.5)]
          and all(b[2] >= a_[2] for a_, b in zip(mg, mg[1:])) and len(cut_tree(mg, 4, 3.0)) == 2)
    pu = np.array([0.0, 1.0, 2.0, 10.0])
    Du = np.abs(pu[:, None] - pu[None, :])
    mu, mw = upgma(Du), _wpgma(Du)
    check('15. UPGMA weighs a cluster by its size: on 0, 1, 2, 10 it joins {0, 1, 2} to 10 at 9.0 (the mean of 10, '
          f'9 and 8), where WPGMA gives {mw[-1][2]}', [round(x[2], 12) for x in mu] == [1.0, 1.5, 9.0]
          and abs(mw[-1][2] - 9.0) > 0.1)
    check('15. ARI is 1 on equal partitions, about 0 against a constant labelling',
          ari([0, 0, 1, 1], ['a', 'a', 'b', 'b']) == 1.0 and abs(ari([0, 1, 0, 1], [1, 1, 1, 1])) < 1e-12)
    # Twins and mutual nearest on toy matrices.
    D2 = np.array([[0.0, 0.01], [0.01, 0.0]])
    check('7. scale band: full worth 2.01 apart is no twin at band 1, 1.99 apart is',
          twin_pairs(D2, ['x', 'y'], [True, True], np.array([1.0, 2.01]), ['x', 'y'], 0.1, 1.0) == []
          and twin_pairs(D2, ['x', 'y'], [True, True], np.array([1.0, 1.99]), ['x', 'y'], 0.1, 1.0) == [('x', 'y')]
          and twin_pairs(D2, ['x', 'y'], [True, True], np.array([1.0, 1.0]), ['p', 'p'], 0.1, 1.0) == [])
    D4 = np.array([[0, 0.5, 0.05, 0.2], [0.5, 0, 0.03, 0.3], [0.05, 0.03, 0, 0.5], [0.2, 0.3, 0.5, 0]])
    k4, t4 = ['s1', 's2', 'k1', 'k2'], np.array(['spell', 'spell', 'skill', 'skill'])
    check('7. mutual nearest: only the pair that are each the other\'s nearest of the other kind, within the cut',
          mutual_nearest(D4, k4, [True] * 4, t4, 0.1) == [(1, 2)] and mutual_nearest(D4, k4, [True] * 4, t4, 0.01) == []
          and mutual_nearest(D4, k4, [True, False, True, True], t4, 0.1) == [(0, 2)])
    check('12. loop lower bound: held variants of a loop spell only (a charged cast, or the one cast of a hold '
          'spell, loop clip or not); never a tap, never a spell with no loop cost',
          loop_lower_bound('charged', 3, True) and loop_lower_bound('cast', 1, True)
          and loop_lower_bound('cast', 2, False)
          and not loop_lower_bound('tap', 10, True) and not loop_lower_bound('charged', None, False)
          and not loop_lower_bound('cast', None, True))
    check('12. channelled on the same reading: a tap of a loop spell never, its charged cast and a loop cast yes',
          not channelled('tap', True) and channelled('charged', True) and channelled('cast', True)
          and not channelled('cast', False) and not channelled('charged', False))
    g_ = group_of({'land_mass_now': 0.2, 'land_mass_best': 0.2, 'reach': 1.0, 'mean_net': -87.8}, 0.02, 1.0)
    g2 = group_of({'land_mass_now': 0.0, 'land_mass_best': 0.0, 'reach': 1.0, 'mean_net': -87.8}, 0.02, 1.0)
    g3 = group_of({'land_mass_now': 0.0, 'land_mass_best': 0.03, 'reach': 1.0, 'mean_net': -0.5}, 0.02, 1.0)
    check(f"8. a live attack netting -87.8 on average is flagged '{PUNISHED_LIVE}'; one that lands nothing and nets "
          f"-87.8 is '{PUNISHED_NOT_LIVE}'; a run-in-only one netting -0.5 is not flagged",
          g_ == ('live', None, [PUNISHED_LIVE]) and g2 == ('nets_nothing', 'always evaded', [PUNISHED_NOT_LIVE])
          and g3 == ('runin_only', None, []))
    # D_idle: a pseudometric. The base-share term is an L1 distance and the timing term dt / (dt + F)
    # (1 when only one side touches) a concave transform of one; their weighted sum keeps the triangle.
    B40, T40 = rng.random((40, S_)), np.where(rng.random((40, S_)) < 0.3, np.inf, rng.random((40, S_)) * 60)
    Di40 = d_idle_rows(B40, T40, B40, T40, w, W, F)
    check('4. D_idle: zero on itself, symmetric, triangle inequality on every triple of 40 profiles with never-touch '
          'cells, within [0, 1]',
          np.abs(np.diag(Di40)).max() < 1e-12 and np.allclose(Di40, Di40.T, atol=1e-12)
          and (Di40[:, :, None] <= Di40[:, None, :] + Di40.T[None, :, :] + 1e-12).all()
          and Di40.min() >= 0 and Di40.max() <= 1)
    check('13. find_cycle finds a -> b -> c -> a and nothing in a chain',
          find_cycle([{'a': 'a', 'b': 'b'}, {'a': 'b', 'b': 'c'}, {'a': 'c', 'b': 'a'}]) == ['a', 'b', 'c', 'a']
          and find_cycle([{'a': 'a', 'b': 'b'}, {'a': 'b', 'b': 'c'}, {'a': 'a', 'b': 'c'}]) is None)
    fl_ = {'land': np.array([[0.25, 0.25, 0.0, 0.25], [0.25, 0.3, 0.0, 0.25]], np.float32)}
    check('5. flat_land: one share wherever it lands is that share; two shares, or landing pressed at once, is None',
          flat_land(fl_, 0, 0.0, 0.02) == 0.25 and flat_land(fl_, 1, 0.0, 0.02) is None
          and flat_land(fl_, 0, 0.05, 0.02) is None)
    lv = {k: 10 for k in ATTRS}
    cls_ = {'Wretch': dict(lv), 'Low': {**lv, 'arc': 7}}
    check('6. reachable: a line every class base is under, at RL sum - 79; a class with a base above the line is out',
          reachable({**lv, 'str': 60}, cls_, 150) == {'rl': 51, 'classes': ['Low', 'Wretch'], 'ok': True}
          and reachable({**lv, 'arc': 8}, cls_, 150)['classes'] == ['Low']
          and not reachable({k: 99 for k in ATTRS}, cls_, 150)['ok'])
    sa_ = subcat_agreement([(0, 'a'), (0, 'a'), (1, 'b'), (2, 'b'), (3, 'c')])
    check('7. subcategory agreement: one pure family of two, three singletons; 1 pair inside, all shared; the '
          'base rate 2 / 10', sa_['purity'] == 1.0 and sa_['same_inside'] == 1.0 and abs(sa_['same_base'] - 0.2) < 1e-12
          and sa_['singletons'] == 3 and sa_['clusters'] == 4)
    Gd = delivery_gate([1, 2, 3])
    check('7. delivery gate: melee only and bullet only are kept apart, either joins an attack that has both',
          not Gd[0, 1] and Gd[0, 2] and Gd[1, 2] and Gd[0, 0] and Gd[1, 1]
          and gated(np.zeros((3, 3)), Gd)[0, 1] == 1.0 and gated(np.zeros((3, 3)), Gd)[0, 2] == 0.0)
    Ge = delivery_gate([2, 2, 2], None, [displace_bits({'tokens': ['pull', 'homing']}),
                                         displace_bits({'flags': ['homing']}), displace_bits({'tokens': ['pull']})])
    check('7. displacement gate: a bullet that pulls and one that does not are kept apart, two that pull join '
          '(the Gravity Well tap and the Glintblade Phalanx ash, 2026-11-10)',
          not Ge[0, 1] and Ge[0, 2] and not Ge[1, 2] and Ge[1, 1])
    check('3. follow-up keys carry the press frame: two R2 040010 windows from f18 and f10 get two keys',
          followup_key('skill:107:040000', {'button': 'r2', 'anim': 40010, 'first': 18})
          != followup_key('skill:107:040000', {'button': 'r2', 'anim': 40010, 'first': 10})
          and followup_key('k', {'button': 'r2', 'anim': 40010, 'first': 10.0}) == 'k:then-r2-040010-f10')
    # 30. The objective lines' arithmetic.
    lo7 = dict.fromkeys(ATTRS, 7)
    il = {'vig': 40, 'mnd': 20, 'end': 25, 'str': 12, 'dex': 14, 'int': 60, 'fth': 9, 'arc': 9}
    nd = {'str': 18, 'dex': 0, 'int': 33, 'fth': 0, 'arc': 0}
    ofl = {k: max(lo7[k], nd[k]) for k in WEAPON_NEED}
    a1, r1, p1 = adjust_line(il, nd, ofl, {'str': 0.0, 'dex': 0.0, 'int': 1.0, 'fth': 0.0, 'arc': 0.0})
    a2, r2, p2 = adjust_line(il, {'str': 10, 'dex': 0, 'int': 30, 'fth': 0, 'arc': 0}, ofl)
    check('30. adjust_line: STR 12 under a need of 18 is raised 6 and the 6 are paid from the highest damage stat '
          '(INT 60 -> 54), so the RL is kept; a line that meets the need comes back unchanged',
          a1 == {**il, 'str': 18, 'int': 54} and r1 == {'str': 6} and p1 == {'int': 6}
          and sum(a1.values()) == sum(il.values()) and a2 == il and not r2 and not p2)
    ql = {**lo7, 'str': 48, 'dex': 47, 'int': 9, 'fth': 9}
    qs = {'str': 45.0, 'dex': 45.0, 'int': 30.0, 'fth': 0.0, 'arc': 0.0}
    nq = {'str': 20, 'dex': 14, 'int': 18, 'fth': 0, 'arc': 0}
    a3, r3, p3 = adjust_line(ql, nq, {k: max(7, nq[k]) for k in WEAPON_NEED}, qs)
    a4, _, p4 = adjust_line({**lo7, 'str': 30, 'dex': 30}, {**nq, 'str': 0, 'dex': 0, 'int': 8},
                            dict.fromkeys(WEAPON_NEED, 7), {**qs, 'str': 0.0})
    a5, r5, p5 = adjust_line(lo7, {**nq, 'dex': 0, 'int': 0}, dict.fromkeys(WEAPON_NEED, 7))
    check('30. adjust_line pays the highest stat first and splits a tie, the stat the attack weighs least first '
          '(INT 9 -> 18 on STR 48 / DEX 47 gives 43 / 43; on STR 30 / DEX 30, STR when only DEX scales); with '
          'nothing above the floors the raise stays unpaid and the RL rises',
          a3 == {**ql, 'str': 43, 'dex': 43, 'int': 18} and r3 == {'int': 9} and p3 == {'str': 5, 'dex': 4}
          and a4['str'] == 29 and a4['dex'] == 30 and p4 == {'str': 1}
          and a5 == {**lo7, 'str': 20} and r5 == {'str': 13} and not p5)
    il_ = {**lo7, 'str': 16, 'dex': 18, 'int': 65, 'fth': 9, 'arc': 9}
    w10 = dict.fromkeys(ATTRS, 10)
    astro = {'vig': 9, 'mnd': 15, 'end': 9, 'str': 8, 'dex': 12, 'int': 16, 'fth': 7, 'arc': 9}
    two = {'Wretch': w10, 'Astrologer': astro}
    zi = dict.fromkeys(WEAPON_NEED, 0)
    check('30. line_fit: a STR 45 / DEX 45 / INT 30 weapon fits STR 48 / DEX 47 better than INT 65; an INT-only '
          'spell the other way. minimum_line: the class whose base raised to the need is the lowest RL (INT 36: '
          'the Astrologer, 105 points against 106; STR 20: the Wretch, 90 against 97)',
          line_fit(ql, qs) > line_fit(il_, qs) and line_fit(il_, {**dict.fromkeys(WEAPON_NEED, 0.0), 'int': 1.0})
          > line_fit(ql, {**dict.fromkeys(WEAPON_NEED, 0.0), 'int': 1.0}) and line_fit(ql, dict.fromkeys(WEAPON_NEED, 0.0)) == 0.0
          and minimum_line({**zi, 'int': 36}, two) == ({**astro, 'int': 36}, 'Astrologer')
          and minimum_line({**zi, 'str': 20}, two) == ({**w10, 'str': 20}, 'Wretch'))
    check('16. stamp_diff names the top-level keys and the code and HKS entries that differ',
          stamp_diff({'a': 1, 'b': 2, 'code': {'x': '1', 'y': '2'}, 'hks': {'parser': 'p', 'common_define': [1]}},
                     {'a': 1, 'b': 3, 'code': {'x': '1', 'y': '3'}, 'hks': {'parser': 'q', 'common_define': [1]}})
          == ['b', 'code: y', 'hks: parser'])
    # 31. Timing: laps on a fake clock, pooled items, the log, the pick and the report.
    import tempfile
    clk = [0.0, 0.0]
    tm = Timing(clock=lambda: clk[0], cpu=lambda: clk[1])
    clk[:] = [2.0, 1.5]
    tm.lap('context')
    got_ = list(pooled(abs, [-1, 2, -3], 1, 'toy', tm, lambda x: f'x{x}'))
    tm.item('toy', 'x2', 0.5, 0.25, False)
    clk[:] = [7.0, 2.5]
    tm.lap('toy')
    check('31. a lap is the time since the previous one; a pooled stage holds its items, a repeated name gets #2, and '
          'its workers\' CPU is only the items that ran on them',
          got_ == [1, 2, 3] and tm.stages['context'] == {'wall': 2.0, 'cpu': 1.5}
          and tm.stages['toy'] == {'wall': 5.0, 'cpu': 1.0, 'items': 4, 'workers_cpu': 0.25}
          and list(tm.items['toy']) == ['x-1', 'x2', 'x-3', 'x2#2'] and stage_cpu(tm.stages['toy']) == 1.25)
    clk[:] = [0.0, 0.0]
    buf_ = io.StringIO()
    tl = Timing(clock=lambda: clk[0], cpu=lambda: clk[1], out=buf_)
    clk[:] = [75.0, 70.0]
    tl.lap('inputs')
    tl.item('attacks', 'a', 1.0, 1800.0, False)
    tl.item('attacks', 'b', 1.0, 1800.0, False)
    clk[:] = [3800.0, 80.0]
    tl.lap('attacks')
    check(f'31. each lap prints its stage as it ends: the time it took, a pooled stage\'s items and CPU, and the '
          f'time since the start ({buf_.getvalue().splitlines()})',
          buf_.getvalue().splitlines() == ['attack similarity: inputs took 1m15s; 1m15s since the start',
                                           'attack similarity: attacks took 1h02m (2 items, 3,610 CPU s); '
                                           '1h03m since the start'])

    def fake(scope, k2, code):
        t_ = Timing(clock=lambda: 0.0, cpu=lambda: 0.0)
        t_.facts.update(scope=scope, only=None, cache='computed', jobs=3)
        t_.stages = {'attacks': {'wall': 10.0, 'cpu': 1.0, 'items': 2, 'workers_cpu': 4.0 + k2}}
        t_.items = {'attacks': {'k1': [5.0, 4.0], 'k2': [k2, k2]}}
        return t_, code
    with tempfile.TemporaryDirectory() as tmp:
        lp = Path(tmp) / 'timing.jsonl'
        for t_, code in (fake('full', 5.0, {'a.py': '1', 'b.py': '2'}), fake('selftest', 1.0, {'a.py': '1', 'b.py': '2'})):
            t_.record(lp, code=code)
        with lp.open('a') as f:
            f.write('{"killed while appending\n')
        t_, code = fake('full', 24.0, {'a.py': '1', 'b.py': '3'})
        row = t_.record(lp, code=code)
        t2_ = Timing()
        t2_.mark_code({**code_stamp(), Path(__file__).name: 'as it was when the run began'})
        moved_ = t2_.record(Path(tmp) / 'other.jsonl')['code_moved']
        runs_ = timing_runs(lp)
        ra, rb = timing_pick(runs_, [])
        buf = io.StringIO()
        timing_report(ra, rb, out=buf)
        rep = buf.getvalue()
        check(f'31. the log keeps every whole line (a torn one is skipped), the newest run is compared with the newest '
              f'earlier run of its scope, and the report names the code that moved and the item that took the change '
              f'({rep.splitlines()[-1].strip()})',
              [r['n'] for r in runs_] == [1, 2, 4] and (ra['n'], rb['n']) == (1, 4)
              and timing_pick(runs_, ['2']) == (None, runs_[1]) and timing_pick(runs_, ['1', '-1']) == (runs_[0], runs_[2])
              and {'when', 'git', 'cpus', 'load', 'load_end', 'code', 'stages', 'items', 'scope'} <= set(row)
              and 'code that differs: b.py' in rep and '+19.00 s' in rep and 'k2  +100% of the change' in rep
              and 'CPU 9.0 -> 28.0 s over the 2 items in both' in rep)
        check(f'31. the code a run ran is the code when it was marked; a file that changed on disk since is named '
              f'({moved_})', moved_ == [Path(__file__).name])
        frt = (full_run_time(lp), full_run_time(Path(tmp) / 'none.jsonl'))
        check(f'31. a cache miss cites the newest full run\'s measured time, or says there is none ({frt})',
              frt == (f"the last full run, version None on {str(row['when'])[:10]} with 3 workers, took 0s by the "
                      f"timing log",
                      'no full run is in the timing log yet, so its time is unmeasured'))
        tr_ = Path(tmp) / 'x.items.jsonl'
        t3_ = Timing(trail=tr_)
        t3_.note(attack_cost={'k1': 2.0})
        t3_.item('attacks', 'k1', 1.5, 1.25, False)
        trail_ = [json.loads(ln) for ln in tr_.read_text().splitlines()]
        check(f'31. each item is in the trail as it ends, before the run is recorded ({trail_})',
              trail_ == [{'when': t3_.when, 'attack_cost': {'k1': 2.0}},
                         {'when': t3_.when, 'stage': 'attacks', 'item': 'k1', 'wall': 1.5, 'cpu': 1.25}]
              and trail_path(Path(tmp) / 'a-timing.jsonl') == Path(tmp) / 'a-timing.items.jsonl'
              and Timing().trail is None)
        import types
        fake_ = types.ModuleType('pkg.fake')
        arr_ = np.zeros(4)
        fake_._MEMO, fake_._EMPTY, fake_._Mixed, fake_.lower = {'a': (arr_, [np.zeros(2)]), 'b': {'c': arr_}}, {}, {1: 2}, {1: 2}
        mem_ = memory_now((fake_,))
        t3_.item('attacks', 'k2', 1.0, 1.0, False, **mem_)
        last_ = json.loads(tr_.read_text().splitlines()[-1])
        check(f'31. a set\'s memory names its process, its resident and peak bytes, and each memo that holds any, '
              f'its arrays counted once ({mem_})',
              mem_['pid'] == os.getpid() and 0 < mem_['rss'] <= mem_['hwm'] and mem_['memos'] == {'fake._MEMO': [2, 48]}
              and set(memory_now()) == {'pid', 'rss', 'hwm'} and last_['memos'] == {'fake._MEMO': [2, 48]}
              and last_['item'] == 'k2')
        # One mapped block freed raises glibc's mmap threshold past 160 KB, so the next ones come
        # from the heap, and the small blocks kept between them stop it shrinking from its top.
        np.ones(20000)
        held_, pins_ = [], []
        for _ in range(2000):
            held_.append(np.ones(20000))
            pins_.append(np.ones(100))
        before_ = memory_now()['rss']
        del held_
        freed_ = memory_now()['rss']
        release_heap()
        trimmed_ = memory_now()['rss']
        check(f'31. 2,000 freed arrays of 160 KB stay resident until the heap is handed back '
              f'({before_ >> 20} MB held, {freed_ >> 20} freed, {trimmed_ >> 20} after release_heap)',
              trimmed_ < freed_ - (200 << 20))
        sd_ = Path(tmp) / 'x.sets'
        set_ = [('k1', {'net': np.arange(3, dtype=np.float32), 'aims_won': {'target': 0.25}, 'n': np.int64(7)},
                 {0: (np.ones(2), np.zeros(2)), 'line': (np.full(2, np.nan), None)}, [0.1, float('inf')], 'mem'),
                ('k2', None, {}, [], 'mem')]
        save_set(sd_, set_)
        back_ = load_set(sd_, ['k1', 'k2'])

        def same_(x, y):
            if isinstance(x, np.ndarray) or isinstance(x, np.generic):
                return type(x) is type(y) and x.dtype == y.dtype and np.array_equal(x, y, equal_nan=True)
            if isinstance(x, dict):
                return isinstance(y, dict) and list(x) == list(y) and all(same_(x[k], y[k]) for k in x)
            if isinstance(x, (list, tuple)):
                return type(x) is type(y) and len(x) == len(y) and all(same_(u, v) for u, v in zip(x, y))
            return type(x) is type(y) and (x == y or x != x and y != y)
        set_file(sd_, ['k3']).write_bytes(b'not a zip')
        check(f'31. a set\'s results are kept as they are (dtypes, key types, tuples, NaN) and read back only for '
              f'its own keys ({len(list(sd_.iterdir()))} files)',
              same_(back_, [tuple(g[:4]) for g in set_]) and load_set(sd_, ['k2', 'k1']) is None
              and load_set(sd_, ['k3']) is None and load_set(Path(tmp) / 'none', ['k1']) is None
              and set_dir(150, 'ab', tmp) == Path(tmp) / f'attack-similarity-{150 - S.WINDOW}-{150 + S.WINDOW}-ab.sets')
        # A stopped run's leftovers: an empty set file (power lost after the rename), a writer's
        # temporary file whose pid ended and one whose writer still runs (this process), and sets made
        # under other Python or numpy versions.
        set_file(sd_, ['k4']).write_bytes(b'')
        dead_ = sd_ / f'.abc.{2**22 + 7}.tmp.npz'
        live_ = sd_ / f'.abc.{os.getpid()}.tmp.npz'
        dead_.write_bytes(b'x')
        live_.write_bytes(b'x')
        save_set(sd_, [])

        def sets_(d):
            return [p for p in d.glob('*.npz') if not p.name.startswith('.')]
        n_ = len(sets_(sd_))
        why0_ = open_checkpoint(sd_, {'python': 'a', 'numpy': 'b'})
        why1_ = open_checkpoint(sd_, {'python': 'a', 'numpy': 'b'})
        kept_ = load_set(sd_, ['k1', 'k2']) is not None
        why2_ = open_checkpoint(sd_, {'python': 'a', 'numpy': 'c'})
        check(f"31. an empty set file reads as no set, an empty set keeps no file, a dead writer's temporary file "
              f"is deleted and a live one's kept, sets made before versions.json are kept and sets of other "
              f"versions deleted ({why2_!r})",
              load_set(sd_, ['k4']) is None and n_ == 3 and not dead_.exists() and live_.exists()
              and why0_ == '' and why1_ == '' and kept_ and 'numpy' in why2_
              and not sets_(sd_) and json.loads((sd_ / 'versions.json').read_text())['numpy'] == 'c')
        live_.unlink()
    # `pooled` with `fresh`: each item after the first in a process of its own on a core no other
    # holds; an item whose process dies is dealt again; the memory guard stops the item started last
    # and starts none until memory is back. Toy items, no model.
    import signal
    cores_ = _ai().allowed_cpus()
    cores_ = cores_[1:] or cores_

    def toy_(x):
        time.sleep(x[1])
        return x[0] * 10, os.getpid(), sorted(os.sched_getaffinity(0))
    toys_ = [(i, 0.3) for i in range(6)]
    got_ = list(pooled(toy_, toys_, 2, 'toy', fresh=True))
    pids_ = {g[1] for g in got_[1:]}
    check(f'31. a fresh pool gives what a serial run gives, each item after the first in a process of its own '
          f'pinned to one core ({[g[2] for g in got_]})',
          sorted(g[0] for g in got_) == [i * 10 for i in range(6)] and len(pids_) == 5 and os.getpid() not in pids_
          and all(len(g[2]) == 1 and g[2][0] in cores_ for g in got_[1:]))
    with tempfile.TemporaryDirectory() as tmp_:
        mark_ = Path(tmp_)

        def dies_once_(x):
            if x[0] == 3 and not (mark_ / 'once').exists():
                (mark_ / 'once').write_text('')
                os.kill(os.getpid(), signal.SIGKILL)
            if x[0] == 4 and x[1] == 'always':
                os.kill(os.getpid(), signal.SIGKILL)
            return x[0]
        msgs_ = []
        try:
            again_ = list(_fresh_pool(lambda x: dies_once_(x), [(i, (i, 'once')) for i in range(1, 6)], 2, cores_,
                                      log=msgs_.append))
        except RuntimeError as e_:
            again_ = e_
        try:
            list(_fresh_pool(lambda x: dies_once_(x), [(4, (4, 'always'))], 1, cores_, retries=1, log=msgs_.append))
            gave_up_ = False
        except RuntimeError as e_:
            gave_up_ = 'died without a result 2 times' in str(e_)
    check(f'31. an item whose process dies without a result is dealt again, and one that always dies stops the run '
          f'after its retries ({msgs_})',
          not isinstance(again_, RuntimeError) and sorted(g[0] for g in again_) == [1, 2, 3, 4, 5]
          and sum('item 3 died' in m for m in msgs_) == 1 and gave_up_)
    t0_ = time.monotonic()
    low_ = (t0_ + 0.4, t0_ + 1.2)
    gmsgs_ = []
    guard_ = MemoryGuard(8 * 2**30, 16 * 2**30, poll_s=0.05, log=gmsgs_.append,
                         available=lambda: 2 * 2**30 if low_[0] <= time.monotonic() < low_[1] else 64 * 2**30)
    gotg_ = list(_fresh_pool(toy_, [(i, (i, 1.0)) for i in range(1, 5)], 2, cores_, guard=guard_, log=gmsgs_.append))
    stops_ = [m for m in gmsgs_ if m.startswith('memory guard') and 'stopping item' in m]
    check(f'31. the memory guard stops the item started last first, starts none while memory is short, and every '
          f'item ends ({gmsgs_})',
          sorted(g[1][0] for g in gotg_) == [10, 20, 30, 40] and guard_.stops >= 1 and stops_
          and 'stopping item 2 ' in stops_[0] and guard_.waits >= 1
          and not any('died without a result' in m for m in gmsgs_))
    # An item the guard stopped twice runs alone: it starts once nothing else runs, nothing starts
    # beside it, and it is not stopped.
    with tempfile.TemporaryDirectory() as tmp_:
        st_ = Path(tmp_)

        def timed_(x):
            n = len(list(st_.glob(f'start-{x[0]}-*'))) + 1
            (st_ / f'start-{x[0]}-{n}').write_text('')
            t = time.monotonic()
            time.sleep(x[1])
            return x[0], t, time.monotonic()
        box_ = {}

        def avail2_():
            s, n2 = box_['g'].stops, len(list(st_.glob('start-2-*')))
            low = (s == 0 and n2 >= 1 and (st_ / 'start-1-1').exists()) or (s == 1 and n2 >= 2)
            return 2 * 2**30 if low else 64 * 2**30
        box_['g'] = MemoryGuard(8 * 2**30, 16 * 2**30, poll_s=0.02, log=lambda m: None, available=avail2_)
        got2_ = {g[1][0]: g[1] for g in _fresh_pool(timed_, [(1, (1, 2.0)), (2, (2, 0.5)), (3, (3, 0.2))], 2,
                                                     cores_, guard=box_['g'], log=lambda m: None)}
        starts2_ = len(list(st_.glob('start-2-*')))
    check(f'31. an item the memory guard stopped twice runs alone: item 2 started {starts2_} times, its last '
          f'start {got2_[2][1] - got2_[1][2]:+.2f} s after item 1 ended, item 3 {got2_[3][1] - got2_[2][2]:+.2f} s '
          f'after item 2 ended',
          box_['g'].stops == 2 and starts2_ == 3 and got2_[2][1] >= got2_[1][2] and got2_[3][1] >= got2_[2][2])
    # A run killed alone (a SIGKILL to its process, not its group): its set process ends with it.
    with tempfile.TemporaryDirectory() as tmp_:
        pidf_ = Path(tmp_) / 'child'

        def long_(x):
            pidf_.write_text(str(os.getpid()))
            time.sleep(60)
        mid_ = os.fork()
        if mid_ == 0:
            try:
                list(_fresh_pool(long_, [(1, None)], 1, cores_, log=lambda m: None))
            finally:
                os._exit(0)
        t1_ = time.monotonic()
        while not pidf_.exists() and time.monotonic() - t1_ < 10:
            time.sleep(0.05)
        kid_ = int(pidf_.read_text()) if pidf_.exists() else None
        os.kill(mid_, signal.SIGKILL)
        os.waitpid(mid_, 0)
        t1_ = time.monotonic()

        def gone_(pid):
            try:
                return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z'
            except OSError:
                return True
        while kid_ is not None and not gone_(kid_) and time.monotonic() - t1_ < 5:
            time.sleep(0.05)
    check(f'31. a set process ends when its run is killed alone ({kid_} gone '
          f'{time.monotonic() - t1_:.2f} s after its run)', kid_ is not None and gone_(kid_))
    so_ = [set_order([['d'], ['c'], ['b'], ['a'], ['e'], ['f'], ['z']]) for _ in range(2)]
    check(f'31. the attack sets are read cheapest first, then the rest in one shuffled order ({so_[0]})',
          so_[0] == so_[1] == [['z'], ['b'], ['d'], ['c'], ['f'], ['e'], ['a']])
    # The long sets by measured seconds first, dearest first, the rest in the shuffle's order; a
    # key never measured counts 0, and no measure is the shuffle itself.
    mo_ = set_order([['d'], ['c', 'x'], ['b'], ['a'], ['e'], ['f'], ['z']],
                    measured={'a': 50.0, 'c': 10.0, 'x': 30.0, 'f': 1.0, 'z': 900.0})
    check(f'31. with measured seconds the long sets come first, dearest first, then the shuffle ({mo_})',
          mo_ == [['z'], ['a'], ['c', 'x'], ['b'], ['d'], ['f'], ['e']]
          and set_order([['d'], ['c'], ['b'], ['a'], ['e'], ['f'], ['z']], measured={}) == so_[0])
    with tempfile.TemporaryDirectory() as tmp_:
        tr_ = Path(tmp_) / 'x.items.jsonl'
        tr_.write_text('\n'.join([json.dumps({'when': 'w1', 'stage': 'attacks', 'item': 'k1', 'wall': 5.0, 'cpu': 4.0}),
                                  json.dumps({'when': 'w1', 'stage': 'inputs', 'item': 'k2', 'wall': 9.0, 'cpu': 9.0}),
                                  '{"stage": "attacks", "item": "torn',
                                  json.dumps({'when': 'w2', 'stage': 'attacks', 'item': 'k1', 'wall': 7.0, 'cpu': 6.0}),
                                  json.dumps({'when': 'w2', 'stage': 'attacks', 'item': 'k3#2', 'wall': 2.0, 'cpu': 2.0})])
                       + '\n')
        ms_ = measured_seconds(tr_)
    check(f"31. a key's measured seconds are its newest 'attacks' item's in the trail, a torn line skipped ({ms_})",
          ms_ == {'k1': 7.0, 'k3': 2.0} and measured_seconds(Path('/nonexistent/trail.jsonl')) == {})
    # A resumed run whose kept sets include the cheapest: the cheapest left goes first, the rest keep
    # their order (r18's first unread set was the 5th dearest of 952, read in this process).
    cost_ = {'z': 1.0, 'b': 5.0, 'd': 9.0, 'c': 3.0, 'f': 2.0, 'e': 7.0, 'a': 8.0}
    ro_ = resume_order([['b'], ['d'], ['c'], ['f'], ['e'], ['a']], cost_)
    check(f'31. a resumed run reads the cheapest set left first, the rest in their order ({ro_})',
          ro_ == [['f'], ['b'], ['d'], ['c'], ['e'], ['a']] and resume_order(so_[0], cost_) == so_[0]
          and resume_order([], cost_) == [] and resume_order([['a']], cost_) == [['a']])
    # The attack sets' counter takes its time left from each set's estimated cost (`pooled` weight).
    buf_ = io.StringIO()
    orig_ = PROG.Count
    PROG.Count = lambda *a_, **k_: orig_(*a_, **{**k_, 'clock': lambda: 0.0, 'out': buf_, 'wall': lambda: 0.0})
    try:
        got_ = list(pooled(lambda x: x * 2, [3, 1, 1], 1, 'sets', weight=lambda x: x))
    finally:
        PROG.Count = orig_
    check(f'31. a weighted pool counts the share of the estimated work done ({buf_.getvalue().splitlines()})',
          got_ == [6, 2, 2] and buf_.getvalue().splitlines() == [
              f'  1/3 sets  0s, 0 s/item, 60.0% of the estimated work, ~0s left  '
              f'[{PROG.hms(0.0)}, ends ~{PROG.hms(0.0)}]', f'  3/3 sets  0s, 0 s/item  [{PROG.hms(0.0)}]'])
    sw_ = set_weight(['a', 'b', 'gone'], {'a': 1.0, 'b': 2.0 ** (1 / COST_TIME_POWER)})
    check(f'31. a set weighs its keys\' costs to the power COST_TIME_POWER, a key with none next to nothing ({sw_})',
          abs(sw_ - 3.0) < 1e-3)
    selftest_estimate(check)
    selftest_form_stop(check)


def selftest_lines_synthetic(check):
    """Check 36 on made-up classes and armor: a `--line` spread (`parse_line`, `given_builds`)."""
    cls_s = {'A': {'vig': 10, 'mnd': 12, 'end': 10, 'str': 10, 'dex': 10, 'int': 14, 'fth': 8, 'arc': 8},
             'B': {'vig': 12, 'mnd': 8, 'end': 12, 'str': 12, 'dex': 9, 'int': 10, 'fth': 9, 'arc': 9}}
    cands_s = {0: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'h', 'weight': 7.9, 'poise': 13.0}],
               1: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'b', 'weight': 18.9, 'poise': 39.0}],
               2: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'a', 'weight': 6.1, 'poise': 10.0}],
               3: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'l', 'weight': 14.8, 'poise': 27.0}]}
    spread_s = 'vig=45,mnd=8,end=15,str=22,dex=21,int=10,fth=9,arc=9'

    def given_s(*texts, taken=()):
        return given_builds([parse_line(t) for t in texts], cls_s, 60, lambda e: 50.0 + 2 * e,
                            lambda p: lightest_armor(cands_s, p),
                            lambda st, pieces: {'hp': 900.0 + st['vig'], 'menu_poise': float(len(pieces)),
                                                'pieces': list(pieces)}, 89.0, taken)

    def refused(fn):
        try:
            fn()
        except SystemExit:
            return True
        return False
    g_s = given_s(f'Mine: {spread_s}', f'Bare:{spread_s},poise=0')
    check('36. a --line spread is a build of its stats as given (Mine: ' + (line_text(g_s[0]['stats']) if g_s else '-')
          + '), RL from them, on the first class it is reachable from (B), usage 0, the lightest set at the melee '
          'floor (h, b, a, l) or at poise=0 (none), its burden that armor over max load at its END, and its FP plan '
          "from its stats and that set, which line_hp and line_poise read; the name is the text before the last ':'; "
          'a spread missing an attribute, with an unknown key, past the RL, below every class, or of a taken name is '
          'refused',
          len(g_s) == 2 and [b['name'] for b in g_s] == ['Mine', 'Bare']
          and g_s[0]['stats'] == {'vig': 45, 'mnd': 8, 'end': 15, 'str': 22, 'dex': 21, 'int': 10, 'fth': 9, 'arc': 9}
          and all(b['rl'] == 60 and b['n'] == 0 and b['given'] and b['rule']['source'] == 'given'
                  and b['rule']['class'] == 'B' for b in g_s)
          and g_s[0]['rule']['end']['armor']['pieces'] == ['h', 'b', 'a', 'l'] and g_s[0]['rule']['end']['poise_floor'] == 89.0
          and g_s[0]['rule']['end']['burden'] == round(47.7 / 80.0, 4) and g_s[0]['rule']['end']['medium']
          and g_s[1]['rule']['end']['armor']['pieces'] == [] and g_s[1]['rule']['end']['burden'] == 0.0
          and g_s[0]['rule']['fp_plan'] == {'hp': 945.0, 'menu_poise': 4.0, 'pieces': ['h', 'b', 'a', 'l']}
          and line_hp(None, build=g_s[0]) == 945.0 and line_poise(None, build=g_s[0]) == 4.0
          and parse_line(f'a:b: {spread_s}')[0] == 'a:b'
          and refused(lambda: parse_line('vig=45')) and refused(lambda: parse_line('x:vig=45,mnd=8'))
          and refused(lambda: parse_line(f'x:{spread_s},luck=3')) and refused(lambda: parse_line(f'x:{spread_s},vig=0'))
          and refused(lambda: given_s(f"x:{spread_s.replace('vig=45', 'vig=46')}"))
          and refused(lambda: given_s(f"x:{spread_s.replace('mnd=8', 'mnd=7').replace('vig=45', 'vig=46')}"))
          and refused(lambda: given_s(f'Quality:{spread_s}', taken=['Quality']))
          and refused(lambda: given_s(f'x:{spread_s}', f'x:{spread_s}')))


def selftest_lines_run(check, c, jobs=1):
    """Check 36 in a run (`given_builds`) on Context `c` (made with its attacker builds): on the
    Glintstone Pebble sorcery and an eighth of the situations, a run with a `--line` spread and one
    without it give the made lines, the distance basis and the objective lines the same numbers bit
    for bit, and the spread nets of its own on its own FP plan; a run without one stamps its builds as
    before. `c.A.HOMING_DISTS`, which `compute` sets from the situations, is put back."""
    F_ = _line_fit()
    world_ = F_.GameWorld(c.rl)
    AT_ = S._mod('er_mechanics_armor_targets', 'er-mechanics-armor-targets.py')
    cands_ = AT_.armor_candidates(AT_.DEF.Tables())
    spec_l = parse_line('Int/Faith 60:vig=36,mnd=20,end=20,str=12,dex=12,int=60,fth=60,arc=9')
    given_ = given_builds([spec_l], c.classes, c.rl, resource_curves()[1], lambda p: lightest_armor(cands_, p),
                          lambda st, pieces: fp_plan(F_, world_, st, pieces), c.poise_floor, [b['name'] for b in c.builds])
    for b in given_:
        b.update(reach=reachable(b['stats'], c.classes, c.rl))
    cx_ = copy.copy(c)
    cx_.pairs, cx_.sprint_pairs, cx_.w = c.pairs[::8], c.sprint_pairs[::8], c.w[::8]
    cx_.W = float(cx_.w.sum())
    cy_ = copy.copy(cx_)
    cy_.builds = list(c.builds) + given_
    dists_ = c.A.HOMING_DISTS
    only_ = {T._norm('Glintstone Pebble')}

    def quiet_(*args, **kwargs):
        return None
    # The sorcery alone: on one core and every situation its one attack set took 31 s, and with the
    # ash of war of the name one set took 44 minutes and the subset was 4 of 10 sets in after 90
    # (`MEASURED` 2026-11-20, scripts/perf-similarity-dump.py).
    try:
        r0_ = compute(cx_, only_, jobs, quiet_, Timing(), kinds=('spell',))
        r1_ = compute(cy_, only_, jobs, quiet_, Timing(), kinds=('spell',))
    finally:
        c.A.HOMING_DISTS = dists_

    def nets_equal_(x, y):
        return x.keys() == y.keys() and all(np.array_equal(x[k][j], y[k][j]) for k in x for j in (0, 1))
    gb_ = r1_.builds[-1] if len(r1_.builds) == len(c.builds) + 1 else {}
    own_ = [b for b in r1_.builds[:-1] if set(b['nets']) & set(gb_.get('nets') or {})]
    st_ = c.stats
    check(f"36. a --line spread ({gb_.get('name')}, {line_text(gb_.get('stats') or {})}) in a run of the Glintstone "
          f"Pebble sorcery: the {len(c.builds)} made lines' nets, the distance basis's arrays and the objective lines "
          f"are the same bit for bit with it and without it; it has {len(gb_.get('nets') or {})} nets of its own (none "
          f"equal to a made line's of the same key: {', '.join(b['name'] for b in own_)}), paid on its own FP plan "
          f"(HP {((gb_.get('rule') or {}).get('fp_plan') or {}).get('hp')}); a run without one stamps its builds as before",
          len(r0_.builds) == len(c.builds) and bool(gb_) and gb_.get('given') and gb_['name'] == 'Int/Faith 60'
          and all(nets_equal_(a_['nets'], b_['nets']) for a_, b_ in zip(r0_.builds, r1_.builds))
          and r0_.keys == r1_.keys and all(np.array_equal(r0_.A[m], r1_.A[m], equal_nan=True) for m in ARRAYS)
          and json.dumps({k: r['objective'] for k, r in r0_.records.items()}, sort_keys=True, default=_json_default)
          == json.dumps({k: r['objective'] for k, r in r1_.records.items()}, sort_keys=True, default=_json_default)
          and bool(gb_['nets']) and set(gb_['nets']) <= set(r1_.records) and bool(own_)
          and all(not np.array_equal(gb_['nets'][k][0], b['nets'][k][0]) for b in own_ for k in set(b['nets']) & set(gb_['nets']))
          and line_hp(cy_, build=cy_.builds[-1]) == gb_['rule']['fp_plan']['hp']
          and stamp(st_, c.catalysts, c.rl, False, c.situations, c.strikes, c.dfn, code={}, paramdefs={},
                    builds=c.builds)[1]['builds'] == [[b['name'], [b['stats'][k] for k in ATTRS], b['n']] for b in c.builds]
          and stamp(st_, c.catalysts, c.rl, False, c.situations, c.strikes, c.dfn, code={}, paramdefs={},
                    builds=cy_.builds)[1]['builds'][-1][-1] == ['given', c.poise_floor])


def selftest_no_hit(check, c):
    """37. Why a spell with no damaging hit by the spell rule is excluded (`no_hit_reason`), on spells
    off the subset, each read alone with `spell_job` (`--selftest-reasons` runs only these).
    `REGULATION` 2026-11-22: Carian Retaliation's damage (Bullet 10464002, AtkParam 46400 `atkMag`
    248) is behind its parry bullet 10464000, which hits no character; Freezing Mist's bullets
    10442000/10442001 deal none and put SpEffect 1442000 (`freezeAttackPower` 20, stateInfo 260) on
    whoever they hit; Unseen Blade fires only SpEffect 1466000 (a 45 s buff, `spCategory` 162 as
    Scholar's Armament's 1446000)."""
    SM = c.SM

    def job(name):
        return spell_job(c, SM.by_name[S.T._norm(name)])
    cr = job('Carian Retaliation')
    hid = [h for e in cr for h in e.get('hidden', [])]
    check(f"37. Carian Retaliation: excluded as needing an incoming projectile, its damage behind Bullet 10464000 "
          f"(SpEffect 1464000 -> BehaviorParam_PC 3010 -> Bullet 10464002 AtkParam 46400): "
          f"{[(e['status'], e['reason']) for e in cr]}",
          bool(cr) and all(e['status'] == 'excluded' and e['reason'].startswith('needs an incoming projectile')
                           for e in cr)
          and any(h['gate'] == 10464000 and h['speffect'] == 1464000 and [3010, 10464002, 46400] in h['behind']
                  for h in hid))
    # Version 30: a status-only spell whose hits `status_hit_atks` counts is scored on its build-up
    # (38); one it does not count keeps the status-only exclusion.
    for name, status, key in (('Freezing Mist', 'frost', 'frost'), ('Poison Mist', 'poison', 'poison'),
                              ("Fia's Mist", 'death', 'death_blight')):
        got = job(name)
        check(f"37. {name}: scored on its {status} build-up, or excluded as status only ({status}) carrying "
              f"'status:{status}': {[(e['status'], e.get('reason'), e.get('tokens'), e.get('status_buildup')) for e in got]}",
              bool(got) and all((e['status'] in ('scored', 'partial') and (e.get('status_buildup') or {}).get(key, 0) > 0)
                                or (e['status'] == 'excluded' and (e.get('reason') or '').startswith(f'status only ({status})')
                                    and f'status:{status}' in e['tokens']) for e in got))
    buffs = ('Unseen Blade', "Scholar's Armament", 'Frozen Armament', 'Terra Magicus', 'Lucidity', 'Eternal Darkness')
    got = {nm: job(nm) for nm in buffs}
    check(f"37. the buffs and fields with no damage and no status keep 'no damaging hit': "
          f"{ {nm: sorted({e['reason'] for e in v}) for nm, v in got.items()} }",
          all(v and all(e['status'] == 'excluded' and e['reason'] == 'no damaging hit' for e in v) for v in got.values()))


def selftest_status_synthetic(check):
    """38a. The proc rule of a status build-up (`er-mechanics-spells.play_buildup`,
    `StatusPool`), on a made-up pool, with no game data: a proc counts only when the build-up
    landed empties the gauge (resistance 100: 99 does not, 100 does), at that build's own max HP,
    capped at it; the refill between ticks comes off."""
    pool = [(100.0, 0.5, 1000.0, {'immunity': 300, 'robustness': 100, 'focus': 200, 'vitality': 200}),
            (100.0, 0.25, 2000.0, {'immunity': 300, 'robustness': 150, 'focus': 200, 'vitality': 200}),
            (200.0, 0.25, 1500.0, {'immunity': 300, 'robustness': 400, 'focus': 200, 'vitality': 200})]
    sp = S.StatusPool(pool)
    frost = {'b': 20, 'group': 'robustness', 'row': 0, 'rate': 10.0, 'point': 30.0, 'ticks': 1, 'lock': 30.0,
             'recover': 0.0}
    one = [[(10.0, 6.0, 10.0, {'frost': frost})]]
    got = {r: S.play_buildup([[r]], one, sp.floor) for r in (0.4, 0.495, 0.5, 0.75)}
    hp = {r: (sp.worth(0.0, b, list(sp.by)) if b else 0.0) for r, b in got.items()}
    capped = sp.worth(950.0, got[0.5], [100.0], pooled=True)
    check(f"38a. a proc counts only when the build-up crosses the gauge: 10 hits of 20 frost on resistances 100 / "
          f"150 / 400, landing 40% {hp[0.4]}, 49.5% {hp[0.495]}, 50% {hp[0.5]}, 75% {hp[0.75]} HP (the 1000 HP build's "
          f"130 at half its share, then the 2000 HP build's 230 at a quarter); 950 landed besides on the 1000 HP "
          f"build caps at its health ({capped})",
          got[0.4] is None and got[0.495] is None and abs(hp[0.5] - 65.0) < 1e-9 and abs(hp[0.75] - 122.5) < 1e-9
          and abs(capped - (0.5 * 1000.0 + 0.25 * 950.0)) < 1e-9)
    slow = S.play_buildup([[0.5]], [[(10.0, 6.0, 10.0, {'frost': dict(frost, recover=5.0)})]])
    check(f"38a. the gauge refills 5 a second over the 4 x 6 frames between the 5 ticks landed: 100 less 4 is "
          f"{slow['frost'][0]}, which leaves the resistance-100 build short", abs(slow['frost'][0] - 96.0) < 1e-9
          and not S.StatusPool.procs(*sp.by[100.0][::2], slow).any())


def selftest_status_stun(check, c):
    """38b. The stun of a bleed proc (version 32, `er-mechanics-spells.StatusCaught`), on a made-up
    attack: three level-0 hits (AtkParam_Pc 600000074, Swift Slash's burst row) at f30, f38 and f50
    on a 1 m disc 2.5 m out, each building 100 bleed, on a pool defender of 1000 poise (none breaks)
    and robustness 50, so the first hit procs. Played without the stun he rolls out at the catch
    (f30); with it he is held in the small stagger to its DamageCount-1 roll cancel, 10 frames later
    (`er-mechanics-proc-opening.proc_reaction`'s), so the f38 hit lands too; with SpEffect 6340 on
    him (`promotion_exempt`) the hit stays level 0 and the play is the plain one. Robustness 100
    and 101 are two plays: the first hit's 100 procs at under 101 (`first_proc`: int(gauge) - b < 1)."""
    S_, A = S, c.A
    PO = S_.proc_opening()

    def disc(frame):
        T = np.array([frame, frame + 0.5])
        lst = A.caught_list(c.t, 600000074, 405.0)
        lst['status'] = {'bleed': {'b': 100, 'group': 'robustness', 'row': 6400, 'rate': 15.0, 'point': 100.0,
                                   'ticks': 1, 'lock': 0.0, 'recover': 0.0}}
        return ({'T': T, 'X': np.zeros(2), 'Z': np.full(2, -2.5), 'R': np.full(2, 1.0), 'PK': np.full(2, -math.inf),
                 'CAUGHT': {'lists': [lst], 'wf': 1.0}}, frame)
    geo = [disc(30.0), disc(38.0), disc(50.0)]
    value = {30.0: 100.0, 38.0: 100.0, 50.0: 100.0}
    res = {'immunity': 300, 'robustness': 50, 'focus': 200, 'vitality': 200}
    pool = [(1000.0, 1.0, 1500.0)]
    dfn = {'status_pool': [(1000.0, 1.0, 1500.0, res)]}
    plain = A.CaughtModel.of(geo, pool, value)
    stun = S_.status_caught(geo, pool, value, dfn)
    exempt = S_.status_caught(geo, pool, value, dict(dfn, speffects=(6340,)))
    roll = next(i for i, e in enumerate(plain.escapes) if e[0] == 'roll')

    def te(cm):
        return float(cm.escape_table(2.5, 0)[0][0, roll])

    def landed(cm):
        return [round(x[0], 3) for x in cm.landed(2.5)[0]]
    r = PO.proc_reaction('bleed', 0)
    br = A.stun_branch()
    hold = te(stun) - te(plain)
    check(f"38b. a bleed proc on a held poise holds him 10 frames (proc-opening's small stagger: roll "
          f"{r['roll']}, the caught branch {br['roll']}): he rolls out at f{te(plain):g} without it, f{te(stun):g} "
          f"with it (+{hold:g}); hits landed {landed(plain)} -> {landed(stun)}; with SpEffect 6340 on him "
          f"(exempt {PO.promotion_exempt((6340,))}) f{te(exempt):g} and {landed(exempt)}",
          type(stun).__name__ == 'StatusCaught' and hold == 10.0 and r['roll'][1] == 10 and br['roll'][0] == 10
          and te(exempt) == te(plain) and landed(exempt) == landed(plain)
          and sum(landed(stun)) > sum(landed(plain)))
    split = [(1000.0, 0.5, 1500.0, dict(res, robustness=100)), (1000.0, 0.5, 1500.0, dict(res, robustness=101))]
    two = S_.status_caught(geo, [(1000.0, 1.0, 1500.0)], value, {'status_pool': split})
    runs = two.runs(2.5, 0)
    check(f"38b. robustness 100 procs on the first hit's 100 and 101 does not: two plays "
          f"({[(round(w, 3), [two.spool.classes('robustness')[k][1] for k in run.ks], [round(x[0], 3) for x in got]) for got, w, run, _ in runs]})",
          len(runs) == 2 and sum(x[0] for x in runs[0][0]) > sum(x[0] for x in runs[1][0]))


def selftest_status(check, c):
    """38. Status build-up in the net (version 30, `--selftest-status` runs only these): an attack
    that builds none reads the plain caught defender and the same nets; Bloody Slash's bleed adds
    HP only where a play's build-up crosses some pool build's resistance and never takes any away;
    Freezing Mist, which deals no damage, is scored on its frost (`REGULATION`: Bullet 10442001,
    AtkParam 44200, SpEffect 1442000 `freezeAttackPower` 20). Every eighth situation."""
    selftest_status_synthetic(check)
    selftest_status_stun(check, c)
    SM, A = c.SM, c.A
    pairs = c.pairs[::8]
    plain = {k: v for k, v in c.dfn.items() if k != 'status_pool'}

    def nets(x, dfn):
        return np.array([p['net'] for p in S.net_parts(A, x['geo'], x['value'], pairs, x['roll'], c.strikes,
                                                       x['value_one'], defender=dfn)])
    sor = [r for r in spell_job(c, SM.by_name[S.T._norm('Stars of Ruin')]) if r.get('geo')]
    cm = [S.status_caught(r['geo'], c.dfn['pool'], r['value'], c.dfn) for r in sor]
    same = [np.array_equal(nets(r, c.dfn), nets(r, plain)) for r in sor]
    check(f"38. Stars of Ruin builds no status ({[r.get('status_buildup') for r in sor]}): the plain caught model "
          f"({sorted({type(m).__name__ for m in cm})}) and bit-identical nets with and without the status pool "
          f"({same})", bool(sor) and all(not r.get('status_buildup') for r in sor)
          and all(type(m) is A.CaughtModel for m in cm) and all(same))
    def crossings(r):
        """(catches whose status HP is above the plain model's exactly where a play's build-up crosses
        some pool build's resistance, catches read, catches that cross): geometry by geometry at the
        first 12 distances of `pairs`, the defender standing (no floors)."""
        sc = S.status_caught(r['geo'], c.dfn['pool'], r['value'], c.dfn)
        pc = A.CaughtModel.of(r['geo'], c.dfn['pool'], r['value'])
        lists = sc.status_lists()
        crossed = agree = n = 0
        for d in sorted({p[0] for p in pairs})[:12]:
            for h in range(len(sc.geoms)):
                built = [S.play_buildup(rows, lists, sc.spool.floor) for rows, _, _, _ in sc.runs(d, h, None)]
                procs = any(b is not None and any(S.StatusPool.procs(C, R, b).any() for C, _, R in sc.spool.by.values())
                            for b in built)
                # The plain model prices a catch of no damage at 0, the status model at its floor.
                gain = sc.worth_of(d, h) - max(pc.worth_of(d, h), S.STATUS_WORTH_FLOOR)
                crossed += procs
                agree += (gain > 1e-9) == procs
                n += 1
        return agree, n, crossed
    jobs, _ = skill_candidates(c)
    bjob = next((j for j in jobs if j['name'] == 'Bloody Slash'), None)
    bs = [r for r in (skill_job(c, bjob) if bjob else []) if r.get('geo') and not r.get('follow')][:1]
    for r in bs:
        with_, without = nets(r, c.dfn), nets(r, plain)
        agree, n, crossed = crossings(r)
        # `MEASURED` 2026-11-22: one Bloody Slash on the Dagger at the distance basis builds 60 bleed with
        # every hit landed, under every pool build's robustness, so it procs on no one.
        check(f"38. Bloody Slash ({r['key']}, {r['carrier']}) builds bleed ({r.get('status_buildup')}) short of every "
              f"gauge of the pool ({r.get('status_full')} HP when every hit lands, robustness from "
              f"{c.dfn and S.status_pool_of(c.dfn['status_pool']).floor['robustness']:g}): no catch gains HP "
              f"({agree} of {n} agree, {crossed} cross), and its nets are bit-identical with and without the status "
              f"pool ({np.array_equal(with_, without)}, mean {with_.mean():.1f})",
              (r.get('status_buildup') or {}).get('bleed', 0) > 0 and agree == n and crossed == 0
              and np.array_equal(with_, without))
    check('38. Bloody Slash has a scored opening', bool(bs))
    fm = spell_job(c, SM.by_name[S.T._norm('Freezing Mist')])
    scored = [r for r in fm if r.get('status') in ('scored', 'partial')]
    fnets = [nets(r, c.dfn) for r in scored]
    fx = [crossings(r) for r in scored]
    check(f"38. Freezing Mist, no damage, is scored on its frost: "
          f"{[(r['key'], r['status'], r.get('reason'), r.get('status_buildup'), r.get('status_full')) for r in fm]}; "
          f"a catch gains HP exactly where a play crosses some build's resistance ({fx}: agree, read, cross); "
          f"mean nets {[round(float(x.mean()), 1) for x in fnets]}, best {[round(float(x.max()), 1) for x in fnets]}",
          bool(scored) and all((r.get('status_buildup') or {}).get('frost', 0) > 0 and r.get('status_full', 0) > 0
                               for r in scored) and all(a == n for a, n, _ in fx)
          and all(np.isfinite(x).all() for x in fnets))
    # A damaging attack whose one cast can cross a gauge: Zamor Ice Storm's frost (its storm ticks) and
    # Corpse Piler's bleed (Rivers of Blood's many hits). Where a catch crosses, its nets rise with the
    # status pool and never fall anywhere.
    multi = [r for r in spell_job(c, SM.by_name[S.T._norm('Zamor Ice Storm')]) if r.get('geo')]
    cjob = next((j for j in jobs if j['name'] == 'Corpse Piler'), None)
    multi += [r for r in (skill_job(c, cjob) if cjob else []) if r.get('geo') and not r.get('follow')][:1]
    rows_ = []
    for r in multi:
        agree, n, crossed = crossings(r)
        w, wo = nets(r, c.dfn), nets(r, plain)
        rows_.append((r['key'], r.get('status_buildup'), r.get('status_full'), agree, n, crossed,
                      int((w > wo + 1e-6).sum()), int((w < wo - 1e-6).sum()), round(float(w.mean() - wo.mean()), 2)))
    check(f"38. a status that crosses a gauge in a catch adds HP there and nowhere takes any away: "
          f"(key, build-up, full HP, agree, read, cross, situations up, down, mean gain) {rows_}",
          bool(rows_) and all(x[3] == x[4] and x[7] == 0 for x in rows_) and any(x[5] > 0 and x[6] > 0 for x in rows_))


#: The attacks `selftest_status_groups` scores: the status attacks of the subset whose groups check 8
#: and check 9 pin (Zamor Ice Storm's frost, Bloody Slash's bleed) and Starcaller Cry, which check 8
#: reads beside them.
STATUS_GROUP_NAMES = ('Zamor Ice Storm', 'Bloody Slash', 'Starcaller Cry')


def selftest_one_record_worth(check, c, res, sc):
    """39 (version 31): Starcaller Cry's opening counts its hit priority record once in the full
    worth. Its five nested spheres (AtkParam_Pc 300405900-300405904, 2 to 10 m) are five launches of
    one record, of which one lands per victim (bd er-effects-rs-dnok, er-effects-rs-xb3i;
    `er-mechanics-spells.record_landings`). The record's `worth` is `full_worth` of its
    geometries, the plain sum less the four spheres held back; every land share is at most 1."""
    SM = c.SM
    main = min(sc, key=lambda r: len(r['key']), default=None)
    if main is None:
        check('39. Starcaller Cry is scored', False)
        return
    d = SM.skill_attack(main['carrier'], c.stats, main['affinity'], None, main['id'], c.dfn, False, None,
                        main.get('stance'), priced_bullets=True, flown_only=True)
    keyed = {}
    for (g, fr), ok in zip(d['geo'], S.record_landings(d['geo'])):
        if g.get('HPR') is not None and (g.get('CHAIN') is None or g['CHAIN'][0] == 0):
            keyed.setdefault(g['HPR'][0], []).append((fr, g['HPR'][2], ok, g['CAUGHT']['wf'] * d['value'][fr]))
    big = max(keyed.values(), key=len, default=[])
    launches, landed = {x[1] for x in big}, {x[1] for x in big if x[2]}
    plain = float(sum(d['value'].values()))
    lost = sum(x[3] for x in big if not x[2])
    worth = S.full_worth(d['geo'], d['value'])
    i = res.index.get(main['key'])
    land = float(np.max([res.A[n][i] for n in LAND_CHANNELS])) if i is not None else math.inf
    check(f"39. Starcaller Cry's worth counts its opening once: {len(launches)} launches of one record, "
          f"{len(landed)} landing; worth {worth:.1f} = sum {plain:.1f} less {lost:.1f} held back (record "
          f"{main.get('worth')}, full {main['full']}); largest land share {land:.4f}",
          len(launches) >= 5 and len(landed) == 1 and lost > 0 and abs(plain - lost - worth) < 1e-6
          and main.get('worth') is not None and abs(float(main['worth']) - worth) < 1e-6 and land <= 1.0 + 1e-5)


def selftest_status_groups(check, c, jobs=1):
    """8 and 9 on `STATUS_GROUP_NAMES` alone (`--selftest-status-groups`): the criteria of the full
    selftest's checks that status build-up can move (version 30), each restated here on a subset of
    three attacks so they run without the 7-hour subset: Bloody Slash is never live; the Zamor Ice
    Storm casts and Starcaller Cry are not live, net under -hp and carry the not-live flag; the
    Zamor tap and charged are siblings, never twins; the tap
    and skill 1168 read alone to the roll alone are D_now 0.02 or less, the charged cast and the
    skill over --twin pressed at once, the whole skill over --twin from the tap by D_assoc, and the
    skill contains the tap exactly when a caught defender takes the same share of both storms."""
    _, a = build_args(['--only', 'x'])
    a.only = list(STATUS_GROUP_NAMES)
    tm = Timing(out=sys.stderr)
    res = compute(c, {T._norm(x) for x in STATUS_GROUP_NAMES}, jobs, print, tm, keep=True)
    an = analyze(res, a)
    R, SM = res.records, c.SM

    def ents(name, kind=None, var=None):
        return [r for r in R.values() if T._norm(r['name']) == T._norm(name) and (kind is None or r['type'] == kind)
                and (var is None or r.get('variant') == var)]
    bs = [r for r in ents('Bloody Slash', 'skill') if not r.get('follow')]
    check(f"8 (status groups). Bloody Slash is never live ({[(r['key'], r['group'], round(r['live_now'], 3)) for r in bs]})",
          bool(bs) and all(r['group'] == 'nets_nothing' for r in bs) and max(r['live_now'] for r in bs) > 0.5)
    zid = SM.by_name['zamor ice storm']
    ztap, zch = R.get(f'spell:{zid}:tap'), R.get(f'spell:{zid}:charged')
    zsk = min((r for r in ents('Zamor Ice Storm', 'skill') if r['id'] == 1168 and not r.get('follow')),
              key=lambda r: len(r['key']), default=None)
    sc = [r for r in ents('Starcaller Cry', 'skill') if not r.get('follow')]
    zsc = [r for r in (ztap, zch) if r is not None]
    check(f"8 (status groups). Starcaller Cry not live, under -hp, with '{PUNISHED_NOT_LIVE}' (check 8's pin since "
          f"version 22) ({[(r['group'], round(r['mean_net'], 1)) for r in sc]}); the Zamor casts not live, under -hp, "
          f"with '{PUNISHED_NOT_LIVE}' "
          f"({[(r['key'], r['group'], round(r['mean_net'], 1), r.get('status_full')) for r in zsc]})",
          bool(sc) and all(r['group'] != 'live' and r['mean_net'] < -a.hp and PUNISHED_NOT_LIVE in r['group_flags']
                           for r in sc)
          and len(zsc) == 2 and all(r['group'] != 'live' and r['mean_net'] < -a.hp
                                    and PUNISHED_NOT_LIVE in r['group_flags'] for r in zsc))
    selftest_one_record_worth(check, c, res, sc)
    if None in (ztap, zch, zsk):
        check('9 (status groups). the Zamor tap, charged cast and skill 1168 are scored', False)
        return
    Mx = an['matrices']
    Dn, Dam = Mx['D_now'], Mx['D_assoc']
    twins = set(an['twins']) | {(b, a_) for a_, b in an['twins']}
    i_t, i_c, i_s = res.index[ztap['key']], res.index[zch['key']], res.index[zsk['key']]
    check('9 (status groups). Zamor tap and charged are sibling keys and never twins',
          ztap['parent'] == zch['parent'] and (ztap['key'], zch['key']) not in twins)
    cr = copy.copy(c)
    cr.strikes = np.array(np.asarray(c.strikes), dtype=float)

    def alone(cc, x):
        return {n: np.asarray(v)[None] for n, v in profile_arrays(cc, x).items() if n in ARRAYS}
    zt_in = profile_inputs(next(x for x in spell_job(c, ztap['id']) if x['key'] == ztap['key']))
    d = SM.skill_attack(zsk['carrier'], c.stats, zsk['affinity'], None, zsk['id'], c.dfn, False, None, zsk.get('stance'),
                        priced_bullets=True, flown_only=True)
    annotate_status(c, d['geo'], skill_status_ws(c, zsk['carrier_id'], zsk['affinity']))
    zs_in = {k: d[k] for k in ('geo', 'value', 'value_one', 'roll')}
    zt1r, zs1r = alone(cr, zt_in), alone(cr, zs_in)
    dn_roll = float(d_now_rows(zt1r['land_now'], zt1r['pun_now'], zs1r['land_now'], zs1r['pun_now'], res.w, res.W)[0, 0])
    check(f'9 (status groups). D_now(Zamor tap, skill 1168) <= 0.02 read alone to the roll alone (got {dn_roll:.5f}; '
          f'status build-up tap {ztap.get("status_buildup")}, skill {zsk.get("status_buildup")})', dn_roll <= 0.02)
    check(f'9 (status groups). D_now(Zamor charged, skill) > --twin {a.twin} (got {Dn[i_c, i_s]:.4f})',
          Dn[i_c, i_s] > a.twin)
    zc = [k for k in res.comp_keys if res.comps[k]['of'] == zsk['key']]
    late = max(zc, key=lambda k: res.comps[k]['frame']) if zc else None

    def storm_ticks(x, frame=None):
        return max((l['n'] for g, f in x['geo'] if frame is None or f == frame for l in g['CAUGHT']['lists']),
                   default=None)
    tick_t = storm_ticks(zt_in)
    tick_s = storm_ticks(zs_in, res.comps[late]['frame'] if late else None)
    alike = tick_t == tick_s
    contained = any(x['x'] == zsk['key'] and x['y'] == ztap['key'] and x['component'] == late
                    and x['D_contain'] <= a.twin for x in an['contains'])
    check(f'9 (status groups). skill 1168 contains the Zamor tap exactly when a caught defender takes the same share '
          f'of both storms (ticks: tap {tick_t}, skill {tick_s}; contained {contained}); the whole skill is D_assoc '
          f'{Dam[i_t, i_s]:.3f} from the tap, over --twin', tick_t is not None and contained == alike
          and Dam[i_t, i_s] > a.twin)


def selftest_form_stop(check):
    """31f. A set whose form disagrees with `net_parts` stops the run as it finishes (`finished_set`):
    on made-up sets, the first kept, the second (one key 0.16 HP over `FORM_TOL`) printed and not
    kept, the third never started."""
    started, saved, printed = [], [], []

    def sets():
        for name, errs in (('a', [0.0]), ('b', [0.0, 0.1585]), ('c', [0.0])):
            started.append(name)
            yield [(f'{name}:key', None, {}, errs, 0.0, 0.0, {'pid': 0})]
    err = None
    try:
        for got in sets():
            finished_set(got, 'made-up', lambda *a, **k: printed.append(a[0]), save=lambda root, g: saved.append(g[0][0]))
    except ValueError as e:
        err = str(e)
    check(f"31f. a form error stops the run when its set finishes: started {started}, kept {saved}, printed "
          f"{printed}",
          started == ['a', 'b'] and saved == ['a:key'] and err is not None and "('b:key', 0.1585)" in err
          and printed == [err])


def selftest_estimate(check):
    """31e: the attack sets' counter weighed by seconds measured on the same code, and the line per
    set still running when none ends (`--selftest-estimate` runs these alone, in seconds)."""
    import re
    import tempfile
    pw_spec = importlib.util.spec_from_file_location('er_mechanics_pipeline_watch',
                                                     HERE / 'er-mechanics-pipeline-watch.py')
    pw = importlib.util.module_from_spec(pw_spec)
    pw_spec.loader.exec_module(pw)
    # Each trail row carries the tag of the code its run was marked with; an unmarked run's carry none.
    with tempfile.TemporaryDirectory() as tmp_:
        tr_ = Path(tmp_) / 'x.items.jsonl'
        old_ = Timing(trail=tr_)
        old_.mark_code({'a.py': 'old'})
        old_.item('attacks', 'k1', 100.0, 90.0, False)
        old_.item('attacks', 'k2', 100.0, 90.0, False)
        new_ = Timing(trail=tr_)
        new_.mark_code({'a.py': 'new'})
        new_.item('attacks', 'k1', 7.0, 6.0, False)
        new_.item('attacks', 'k3', 2.0, 2.0, False)
        Timing(trail=tr_).item('attacks', 'k1', 50.0, 50.0, False)
        rows_ = [json.loads(ln) for ln in tr_.read_text().splitlines()]
        tag_ = code_tag({'a.py': 'new'})
        again_ = Timing(trail=tr_)
        again_.mark_code({'a.py': 'new'})
        same_ = same_code_seconds(again_)
        unmarked_ = same_code_seconds(Timing(trail=tr_))
        any_ = measured_seconds(tr_)
    check(f'31e. a trail row carries its run\'s code tag, and a run reads back only the seconds measured on its own '
          f'code ({same_})',
          [r.get('code') for r in rows_] == [code_tag({'a.py': 'old'})] * 2 + [tag_] * 2 + [None]
          and len(tag_) == 12 and tag_ != code_tag({'a.py': 'old'})
          and same_ == {'k1': 7.0, 'k3': 2.0} and unmarked_ == {} and same_code_seconds(Timing()) == {}
          and any_ == {'k1': 50.0, 'k2': 100.0, 'k3': 2.0})
    # A key with a same-code measurement weighs its seconds; one with none its `set_weight` term at
    # the measured keys' median seconds per unit of it (10 and 15 here: 12.5); none measured is
    # `set_weight` itself. A set's expected seconds need every key measured.
    cost_ = {'a': 1.0, 'b': 2.0 ** (1 / COST_TIME_POWER), 'c': 4.0 ** (1 / COST_TIME_POWER)}
    same_ = {'a': 10.0, 'b': 30.0, 'gone': 5.0}
    scale_ = seconds_per_weight(cost_, same_)
    ws_ = [measured_weight(s, cost_, same_, scale_) for s in (['a', 'c'], ['b'], ['c'])]
    check(f'31e. a set weighs its keys\' same-code seconds, a key with none its cost term at their median seconds '
          f'per term, and set_weight when none is measured ({scale_}, {ws_})',
          abs(scale_ - 12.5) < 1e-9 and all(abs(x - y) < 1e-6 for x, y in zip(ws_, (60.0, 30.0, 50.0)))
          and seconds_per_weight(cost_, {}) is None and seconds_per_weight(cost_, {'gone': 1.0}) is None
          and measured_weight(['a', 'c'], cost_, {}, None) == set_weight(['a', 'c'], cost_)
          and expected_seconds(['a', 'b'], same_) == 40.0 and expected_seconds(['a', 'c'], same_) is None
          and expected_seconds([], same_) is None
          and set_label(['k1', 'k2', 'k3'], {'k1': {'name': 'Founding Rain of Stars'}}) == 'Founding Rain of Stars and 2 more'
          and set_label(['comp:1'], {}) == 'comp:1')
    # The counter's share of the work done is the measured seconds' share: 10 of 90.
    buf_ = io.StringIO()
    orig_ = PROG.Count
    PROG.Count = lambda *a_, **k_: orig_(*a_, **{**k_, 'clock': lambda: 0.0, 'out': buf_, 'wall': lambda: 0.0})
    try:
        list(pooled(lambda s: s, [['a'], ['b'], ['c']], 1, 'sets', weight=lambda s: measured_weight(s, cost_, same_, scale_)))
    finally:
        PROG.Count = orig_
    check(f'31e. the counter counts the share of the measured seconds done ({buf_.getvalue().splitlines()[0]})',
          buf_.getvalue().splitlines()[0].startswith('  1/3 sets  0s, 0 s/item, 11.1% of the estimated work'))
    # 2026-11-21's run: none of the keys left measured on this code, its kept sets' keys were (here
    # k1-k3, which took twice their older seconds), the long sets were measured on older code, and
    # the first set, a cheap one, ended after 1 s. Weighed by the cost term at the kept keys' pace
    # it read "~43m27s left" while three sets ran past 1h15m; weighed by older code's seconds,
    # doubled, a key never measured (u) at the median seconds per cost term of the keys measured on
    # either code (2 here), the time left on 3 workers is the longest set's 20,002 s at the first
    # set's own pace (1 s for its weight of 1), more than the 39,614 s left over 3. Its own seconds
    # come from a stand-in for `_timed`, so the pace is not this machine's.
    with tempfile.TemporaryDirectory() as tmp_:
        tr_ = Path(tmp_) / 'x.items.jsonl'
        old_ = Timing(trail=tr_)
        old_.mark_code({'a.py': 'old'})
        for k_, s_ in (('c', 0.5), ('L1', 9806.0), ('L2', 6000.0), ('L3', 4000.0), ('k1', 1.0), ('k2', 1.0),
                       ('k3', 1.0)):
            old_.item('attacks', k_, s_, s_, False)
        new_ = Timing(trail=tr_)
        new_.mark_code({'a.py': 'new'})
        for k_ in ('k1', 'k2', 'k3'):
            new_.item('attacks', k_, 2.0, 2.0, False)
        again_ = Timing(trail=tr_)
        again_.mark_code({'a.py': 'new'})
        same_, prior_ = same_code_seconds(again_), older_code_seconds(again_)
        unmarked_ = older_code_seconds(Timing(trail=tr_))
    ratio_ = older_code_ratio(same_, prior_)
    cost_ = {k_: 1.0 for k_ in ('c', 'L1', 'L2', 'L3', 'u', 'k1', 'k2', 'k3')}
    todo_ = [['c'], ['L1'], ['L2', 'L3', 'u']]
    scale_ = seconds_per_weight(cost_, known_seconds(same_, prior_, ratio_))
    was_ = seconds_per_weight(cost_, same_)
    ws_ = [measured_weight(s_, cost_, same_, scale_, prior_, ratio_) for s_ in todo_]
    was_ws_ = [measured_weight(s_, cost_, same_, was_) for s_ in todo_]
    lines_ = []
    timed0_ = globals()['_timed']

    def timed_(fn, item):
        clk_[0] += 1.0
        return item[0], fn(item[1]), 1.0, 1.0
    for wt_, old_w_ in ((lambda s: measured_weight(s, cost_, same_, scale_, prior_, ratio_),
                         lambda s: older_weight(s, same_, scale_, prior_, ratio_)),
                        (lambda s: measured_weight(s, cost_, same_, was_), None)):
        buf_, clk_ = io.StringIO(), [0.0]
        PROG.Count = lambda *a_, **k_: orig_(*a_, **{**k_, 'clock': lambda: clk_[0], 'out': buf_,
                                                      'wall': lambda: 0.0})
        globals()['_timed'] = timed_
        try:
            # The first set alone, read in this process before any worker starts; closing the pool
            # there forks none.
            gen_ = pooled(lambda s: s, todo_, 3, 'attack sets', weight=wt_, older=old_w_, fresh=True)
            next(gen_)
            gen_.close()
        finally:
            PROG.Count = orig_
            globals()['_timed'] = timed0_
        lines_.append(buf_.getvalue().splitlines()[0])
    check(f'31e. a key with no same-code seconds weighs its newest older-code seconds at the keys measured on both '
          f"codes' ratio, so a cheap first set leaves their total, not the cheap pace ({lines_}, {ws_}, {ratio_})",
          prior_ == {'c': 0.5, 'L1': 9806.0, 'L2': 6000.0, 'L3': 4000.0, 'k1': 1.0, 'k2': 1.0, 'k3': 1.0}
          and unmarked_ == {**prior_, 'k1': 2.0, 'k2': 2.0, 'k3': 2.0}
          and ratio_ == 2.0 and older_code_ratio({'k1': 2.0}, {'k1': 1.0}) == 1.0 and scale_ == 2.0
          and ws_ == [1.0, 19612.0, 20002.0] and was_ws_ == [2.0, 2.0, 6.0]
          and [older_weight(s_, same_, scale_, prior_, ratio_) for s_ in todo_] == [1.0, 19612.0, 20000.0]
          and measured_weight(['k1'], cost_, same_, scale_, prior_, ratio_) == 2.0
          and older_weight(['k1'], same_, scale_, prior_, ratio_) == 0.0
          and older_weight(['L1'], same_, None, prior_, ratio_) == 0.0
          and lines_[0] == ("  1/3 attack sets  1s, 1 s/item, 0.0% of the estimated work, ~5h33m left, "
                            f"100% of the estimate from older code's times  [{PROG.hms(0.0)}, "
                            f"ends ~{PROG.hms(20002.0)}]")
          and lines_[1].startswith('  1/3 attack sets  1s, 1 s/item, 20.0% of the estimated work, ~3s left')
          and older_expected(['L2', 'L3'], same_, prior_, ratio_) == (10000.0, 20000.0)
          and older_expected(['k1', 'L1'], same_, prior_, ratio_) == (9808.0, 19614.0)
          and older_expected(['L1', 'u'], same_, prior_, ratio_) is None and older_expected([], same_, prior_) is None)
    # While no set ends, a line per set still running, under the counter and never read as one; it
    # names the set, how long it has run, and its expected seconds or that there are none. A pool
    # whose sets end within the quiet time prints none.
    global RUNNING_QUIET_S, RUNNING_EVERY_S
    quiet0_ = (RUNNING_QUIET_S, RUNNING_EVERY_S)
    buf_ = io.StringIO()
    PROG.Count = lambda *a_, **k_: orig_(*a_, **{**k_, 'out': buf_})

    def nap_(x):
        time.sleep(x[1])
        return x[0]
    try:
        RUNNING_QUIET_S, RUNNING_EVERY_S = 0.3, 0.3
        got_ = sorted(pooled(nap_, [('first', 0.0), ('slow', 1.4), ('blind', 1.4), ('aged', 1.4)], 3, 'sets',
                             label=lambda x: x[0], fresh=True, expect=lambda x: 0.7 if x[0] == 'slow' else None,
                             source='same-code measurement',
                             older_expect=lambda x: None if x[0] == 'blind' else (0.4, 0.8)))
        lines_ = buf_.getvalue().splitlines()
        buf_ = io.StringIO()
        RUNNING_QUIET_S, RUNNING_EVERY_S = 5.0, 5.0
        quick_ = list(pooled(nap_, [('first', 0.0), ('a', 0.05), ('b', 0.05), ('c', 0.05)], 2, 'sets',
                             label=lambda x: x[0], fresh=True))
        quick_lines_ = buf_.getvalue().splitlines()
    finally:
        RUNNING_QUIET_S, RUNNING_EVERY_S = quiet0_
        PROG.Count = orig_
    slow_ = [x for x in lines_ if x.startswith('  still running: slow  ')]
    blind_ = [x for x in lines_ if x.startswith('  still running: blind  ')]
    aged_ = [x for x in lines_ if x.startswith('  still running: aged  ')]
    heads_ = [x for x in lines_ if pw.COUNTER.match(x)]
    check(f'31e. while no set ends, a line per set still running under the counter, never read as a counter line; '
          f'a set with no same-code seconds but older code\'s says what older code took, scaled ({lines_})',
          got_ == ['aged', 'blind', 'first', 'slow'] and len(slow_) >= 2 and len(blind_) == len(slow_) == len(aged_)
          and ' of ~1s expected (same-code measurement)  [' in slow_[0]
          and ', past the ~1s expected (same-code measurement)  [' in slow_[-1]
          and all(', no same-code measurement  [' in x for x in blind_)
          and all(', older code took 0s (scaled 1s)' in x for x in aged_)
          and aged_[0].endswith(']') and ', ends ~' in aged_[0] and aged_[-1].count(', past it  [') == 1
          and all(re.search(r'  \[\d\d:\d\d:\d\d(, ends ~\d\d:\d\d:\d\d)?\]$', x) for x in slow_ + blind_ + aged_)
          and heads_ and heads_[0].startswith('  1/4 sets  ') and heads_[-1].startswith('  4/4 sets  ')
          and len(heads_) + len(slow_) + len(blind_) + len(aged_) == len(lines_)
          and lines_.index(slow_[0]) > lines_.index(heads_[0]))
    check(f'31e. sets that end within the quiet time leave no still-running line ({quick_lines_})',
          sorted(quick_) == ['a', 'b', 'c', 'first'] and not any('still running' in x for x in quick_lines_))


def selftest(timing_log=None, keep_going=False):
    """Every check; a run that passes them all is a run of the timing log (scope 'selftest'). The
    first failure ends it, unless `keep_going`: then every check runs, each failure is printed, and
    a run with failures is logged with their names (`failed`) and returns 1, so a check known to
    fail (10, whose controls are re-picked from a full run) hides none after it."""
    n, failed = [0], []
    tm = Timing(out=sys.stderr)

    def check(name, ok):
        n[0] += 1
        if not ok:
            if not keep_going:
                sys.exit(f'FAIL {name}')
            failed.append(name)
            print(f'FAIL {name}', file=sys.stderr, flush=True)
            return
        print(f'ok {name}', file=sys.stderr, flush=True)

    selftest_synthetic(check)
    # One pooled run at a time (2026-11-16: a wait loop let a pooled --jobs run start while another
    # was going): with another process holding the shared lock, a pooled compute's lock (`pool_held`,
    # --jobs 2) under --no-wait exits 3 naming that process; one core takes none.
    import contextlib
    lok, ldetail = _pool_lock().exclusive_check(lambda w, log: pool_held(2, 'selftest', w, log))
    check(f'34. --jobs 2 --no-wait meets a held er-mechanics-pool-lock: {ldetail}; --jobs 1 takes none',
          lok and isinstance(pool_held(1, 'selftest', False), contextlib.nullcontext))
    tm.lap('synthetic')
    require_fresh_members()
    _, a = build_args(['--only', 'x'])
    a.only = list(SELFTEST_NAMES)
    stats = S.parse_stats(DEFAULT_STATS)
    c = Context(stats)
    _ai()
    tm.mark_code()
    tm.lap('context')
    SM, A, t = c.SM, c.A, c.t
    selftest_no_hit(check, c)
    selftest_status(check, c)

    # 14. n_eff.
    # 466.2 on 1,208 situations before version 16's move-cancel split, 1029.0 on 3,483 since 17.
    check('14. n_eff at RL 150 is 1029.0 +- 0.1', abs(c.n_eff - 1029.0) <= 0.1)
    # 25. The attacker builds.
    check('25. RL is the eight attributes less 79 on every starting class (CharaInitParam soulLv): '
          + ', '.join(f"{nm} {cl['level']}" for nm, cl in sorted(c.classes.items())),
          len(c.classes) >= 10 and all(sum(cl[k] for k in ATTRS) - LEVEL_OFFSET == cl['level'] for cl in c.classes.values()))
    BT = _build_tags()
    spec_ = {nm: (main, cap, frm) for nm, main, cap, frm in ATTACKER_LINES}
    use_ = {u['name']: u for u in c.usage or []}
    names_ = [b['name'] for b in c.builds]
    check('25. the attacker builds are the lines made from the game\'s curves, one per archetype the corpus has '
          '(usage) and the two split lines, the commonest archetype first and the split lines last, none for the '
          'corpus builds with no archetype: ' + '; '.join(build_title(b) for b in c.builds),
          len(c.builds) >= 2 and set(BT.ARCHETYPES) <= set(spec_) and NO_ARCHETYPE not in names_
          and sorted(names_) == sorted(nm for nm, (_, _, frm) in spec_.items() if frm[0] in use_)
          and [b['n'] for b in c.builds] == sorted((b['n'] for b in c.builds), reverse=True)
          and all(b['n'] == (use_[b['name']]['n'] if b['name'] in use_ else 0) for b in c.builds)
          and all(b['n'] > 0 for b in c.builds if b['name'] in BT.ARCHETYPES)
          and all(set(b['stats']) == set(ATTRS) and all(isinstance(v, int) for v in b['stats'].values()) for b in c.builds))
    check(f"25. every attacker build sums to RL {c.rl} + {LEVEL_OFFSET}, is reachable from the class it is made on "
          "and keeps every attribute at that class's base or more: "
          + '; '.join(f"{b['name']} RL {b['reach']['rl']} on {b['rule']['class']}, reachable from "
                      f"{', '.join(b['reach']['classes'])}" for b in c.builds)
          + f"; the distance basis with 10 VIG, MND and END is RL "
          f"{reachable({'vig': 10, 'mnd': 10, 'end': 10, **stats}, c.classes, 150)['rl']}, not",
          all(sum(b['stats'].values()) == c.rl + LEVEL_OFFSET and b['rl'] == c.rl and b['reach']['ok']
              and b['rule']['class'] in b['reach']['classes']
              and all(b['stats'][k] >= c.classes[b['rule']['class']][k] for k in ATTRS) for b in c.builds)
          and not reachable({'vig': 10, 'mnd': 10, 'end': 10, **stats}, c.classes, 150)['ok'])
    RES_ = S._mod('er_mechanics_resources', 'er-mechanics-resources.py')
    res_ = RES_.Model()
    ten_ = {k: 10 for k in ATTRS}
    hp_ = {v: res_.resources({**ten_, 'vig': v})['max_hp'] for v in range(1, STAT_MAX + 1)}
    gain_ = {v: hp_[v] - hp_[v - 1] for v in range(2, STAT_MAX + 1)}
    knee_ = next(v for v in range(2, STAT_MAX) if 2 * gain_[v + 1] < gain_[v])
    # Every line is `er-mechanics-line-fit`'s, on its own kit and carrying it alone.
    fit_b = [b for b in c.builds if b['rule'].get('source') == 'line-fit']
    wep_b = [b for b in fit_b if any(p['kind'] == 'weapon' for p in b['rule']['fit']['kit'])]
    check(f"25. every attacker line is er-mechanics-line-fit's fit on its own kit: "
          + '; '.join(f"{b['name']} ({', '.join(p['name'] for p in b['rule']['fit']['kit'])})" for b in fit_b),
          len(fit_b) == len(c.builds) and {b['name'] for b in fit_b} == {nm for nm, (_, _, frm) in spec_.items()
                                                                       if frm[0] in use_}
          and all(b['rule'].get('fp_plan') for b in c.builds))
    check(f"25. the knee of max HP (CalcCorrectGraph {RES_.GRAPH_HP}) is {knee_}, the first VIG whose next point gains "
          f"under half (+{gain_[40]} for 39 -> 40, +{gain_[41]} for 40 -> 41, +{gain_[60]} for 59 -> 60, "
          f"+{gain_[61]} for 60 -> 61); every line's VIG is traded, and its fit is worth at least VIG at the knee with "
          "the rest fitted, and the knee shape allocate_line made (lightest floor set, least END, MND base), on the "
          "same kit and D: "
          + '; '.join(f"{b['name']} VIG {b['stats']['vig']}: knee x{b['rule']['fit']['vig_knee']['ratio']:.3f}, "
                      f"knee shape x{b['rule']['fit']['knee_shape']['ratio']:.3f}" for b in fit_b
                      if b['rule']['fit'].get('vig_knee') and b['rule']['fit'].get('knee_shape')),
          knee_ == 60 and all(
              b['rule']['fit'].get('vig_knee') and b['rule']['fit']['vig_knee']['stats']['vig'] == knee_
              and b['rule']['fit']['vig_knee']['ratio'] <= 1 + 1e-6
              and b['rule']['fit'].get('knee_shape') and b['rule']['fit']['knee_shape']['stats']['vig'] == knee_
              and b['rule']['fit']['knee_shape']['ratio'] <= 1 + 1e-3 for b in fit_b))

    def carried_(b):
        return max(use_[g].get('carried', 0.0) for g in spec_[b['name']][2] if g in use_)

    def kit_weight_(b):
        """The kit's weight read off the regulation: each catalyst once, each weapon's affinity row."""
        kit = b['rule']['fit']['kit']
        cats = dict.fromkeys(p['catalyst'] for p in kit if p['kind'] == 'spell')
        w = sum(float(SM.catalyst(x)['wep']['weight']) for x in cats)
        for p in kit:
            if p['kind'] == 'weapon':
                w += float(SM.ar.weapons[SM.ar.find_weapon(p['name'], 'Standard')
                                         + S.AR.AFFINITIES.index(p['affinity']) * 100]['weight'])
        return round(w, 2)
    check("25. every line carries its kit alone (its catalysts and the weapon its offense is measured on, no swap), "
          "not its archetype's corpus median, which is kept as usage; burden at most 0.7 of max load: "
          + '; '.join(f"{b['name']} carries {b['rule']['fit']['carried']:g} (usage {b['rule']['usage']['carried']:g}), "
                      f"END {b['stats']['end']}, burden {b['rule']['fit']['burden']}" for b in fit_b),
          bool(fit_b) and all(abs(b['rule']['fit']['carried'] - kit_weight_(b)) < 1e-6
                              and b['rule']['usage']['carried'] == carried_(b)
                              and b['rule']['fit']['burden'] <= RES_.WEIGHT_HEAVY + 1e-9 for b in fit_b))
    fp_ = {m: float(res_.resources({**ten_, 'mnd': m})['max_fp']) for m in range(1, STAT_MAX + 1)}
    F_ = _line_fit()

    def kit_ok(b):
        """The kit `line_kit` gives the line: a caster's one spell from the first run catalyst that
        casts it; Hybrid's two, half each; a split line's spell and a weapon at `SPLIT_MELEE_SHARE`; a
        melee line's one weapon of its archetype's own scaling. Every weapon wieldable at the stats."""
        main, cap, _ = spec_[b['name']]
        kit = b['rule']['fit']['kit']
        spells = [p for p in kit if p['kind'] == 'spell']
        weps = [p for p in kit if p['kind'] == 'weapon']
        for p in spells:
            mid = SM.by_name[S.T._norm(p['name'])]
            if p['catalyst'] != next(x for x, cc in zip(c.catalysts, c.cats) if SM.can_cast(mid, cc)):
                return False
        for p in weps:
            base_id = SM.ar.find_weapon(p['name'], 'Standard')
            r = S.AR.attack_rating(SM.ar, base_id, p['affinity'], p['level'], damage_stats(b['stats']), p['two'])
            if any(v.get('scaling', 0.0) < 0 for v in r['damage'].values()):
                return False
        schools = [k for k in main if k in SCHOOL_LINE]
        if schools and len(schools) == len(main) == 1 and cap == DAMAGE_CAP:
            return len(kit) == 1 and len(spells) == 1 and spells[0]['share'] == 1.0
        if schools and len(schools) == len(main) == 1:
            return (len(spells) == len(weps) == 1 and abs(weps[0]['share'] - SPLIT_MELEE_SHARE) < 1e-9
                    and abs(spells[0]['share'] + weps[0]['share'] - 1) < 1e-9)
        if schools and len(schools) == len(main):
            return len(kit) == len(spells) == len(schools) and all(abs(p['share'] - 1 / len(schools)) < 1e-9 for p in spells)
        row = SM.ar.weapons[SM.ar.find_weapon(weps[0]['name'], 'Standard') + S.AR.AFFINITIES.index(weps[0]['affinity']) * 100] \
            if len(weps) == 1 else None
        return (len(kit) == 1 and row is not None and weps[0]['share'] == 1.0
                and F_.GameWorld.scaling_tag(None, row) == b['name'])

    def stats_ok(b):
        """The build's stats are the fit's (a weapon line's MND moved by its Mind fit, `mind_ok`); a
        line with `mains` is `allocate_line` on its class at the fit's VIG, `END` and MND."""
        f, main, cap = b['rule']['fit'], spec_[b['name']][0], spec_[b['name']][1]
        st = f['stats']
        if b['rule'].get('mind') is None and b['stats'] != st:
            return False
        if len(f['mains']) > 1 or not set(f['mains']) & set(SCHOOL_LINE):
            base = c.classes[f['class']]
            if allocate_line(dict(base, mnd=st['mnd']), c.rl + LEVEL_OFFSET, st['vig'], st['end'], main, cap) != st:
                return False
        return all(st[k] <= cap for k in main)
    check("25. every line's kit is line_kit's (a caster's school spell from the run's catalyst; Hybrid both; a split "
          f"line its spell and a weapon, {SPLIT_MELEE_SHARE:g} each; a melee line one weapon of its own archetype's "
          "scaling, picked off the game and settled at the fit), every weapon wieldable at the line's stats; the "
          "stats the fit's, the main stats at most their cap; the melee poise floor kept on every line with a "
          "weapon and on no caster, the best below it no worse; its FP plan the fit's HP, defense, poise and bar: "
          + '; '.join(f"{b['name']} {line_text(b['stats'])} on {b['rule']['class']}, {b['rule']['fit']['wear'] or 'no HP piece'}, "
                      f"poise {b['rule']['fit']['armor_poise']:g}"
                      + (f" (floor {b['rule']['fit']['poise_floor']:g}; below it x{b['rule']['fit']['below_floor']['ratio']:.3f} "
                         f"at {b['rule']['fit']['below_floor']['armor_poise']:g})" if b['rule']['fit'].get('poise_floor') else '')
                      + f", bar {b['rule']['fit']['bar']}, {b['rule']['fit']['cerulean']} blue, D {b['rule']['fit']['D']:g}"
                      for b in fit_b),
          bool(fit_b) and all(
              kit_ok(b) and stats_ok(b) and b['rule']['fit']['cap'] == spec_[b['name']][1]
              and ((b in wep_b) == bool(b['rule']['fit'].get('poise_floor')))
              and (b not in wep_b or (b['rule']['fit']['armor_poise'] >= b['rule']['fit']['poise_floor']
                                      and b['rule']['fit']['weapon_settled']
                                      and b['rule']['fit']['below_floor']['ratio'] >= 1 - 1e-9))
              and b['rule']['fp_plan']['bar'] == fp_[b['stats']['mnd']]
              and (b['rule'].get('mind') is not None or b['rule']['fit']['bar'] == b['rule']['fp_plan']['bar'])
              and b['rule']['fp_plan']['hp'] == b['rule']['fit']['hp'] for b in fit_b))
    check("25. line-fit's FP plan, the one every line is paid on: bar 78 with 2 blue flasks pays 234 FP of a 780 FP "
          f"want (not 78 + 440: {float(F_.fp_paid(10.0, 78.0, 78.0, [], 2.0, 220.0)):g}); a free attack is always paid",
          abs(float(F_.fp_paid(10.0, 78.0, 78.0, [], 2.0, 220.0)) - 234.0) < 1e-9
          and paid_share(c.builds[0]['rule'], 0) == 1.0 and fp_plan_paid(c.builds[0]['rule']['fp_plan'], 0)['share'] == 1.0)

    def mind_ok(b):
        """Whether weapon line `b`'s MND is its Mind fit's: the fit's own line at the class base (its
        FP-free R1 buys no MND), then at least the class base, its bar max FP there, the points it
        took the base-MND line's less its own, and no MND searched worth more."""
        m, base, f = b['rule']['mind'], c.classes[b['rule']['class']], b['rule']['fit']
        main, cap, _ = spec_[b['name']]
        first = allocate_line(base, c.rl + LEVEL_OFFSET, f['stats']['vig'], f['stats']['end'], main, cap)
        return (first == f['stats']
                and m['base'] == base['mnd'] <= m['mnd'] == b['stats']['mnd'] <= m['searched'] and m['bar'] == fp_[m['mnd']]
                and sum(m['from'].values()) == m['mnd'] - m['base']
                and all(first[k] - b['stats'][k] == m['from'].get(k, 0) for k in WEAPON_NEED)
                and m['value'] >= m['value_base'] and m['share'] >= m['share_base'] - 1e-9)
    mind_b = [b for b in wep_b if not any(p['kind'] == 'spell' for p in b['rule']['fit']['kit'])]
    items_ = fit_b[0]['rule']['fp_plan']['fight'].get('item_names') if fit_b else None
    check("25. every weapon line's MND is its Mind fit's on its fit's FP plan (a point to MND only while it buys "
          f"more paid damage than the stat it comes from; the plan {'; '.join(items_ or [])}), every other line's "
          "the fit's own: "
          + '; '.join(f"{b['name']} MND {b['rule']['mind']['base']} -> {b['stats']['mnd']} (bar {b['rule']['mind']['bar']:g}, "
                      f"took {b['rule']['mind']['from'] or 'nothing'}; paid {b['rule']['mind']['share_base']:.4f} -> "
                      f"{b['rule']['mind']['share']:.4f} of {b['rule']['mind']['attacks']} attacks, the dearest "
                      f"{b['rule']['mind']['dearest']:g} FP {b['rule']['mind']['paid_dearest']['share']:.3f})" for b in mind_b
                      if b['rule'].get('mind')),
          bool(items_) and len(items_) >= 2 and bool(mind_b) and all(b['rule']['mind'] and mind_ok(b) for b in mind_b)
          and all(b['rule'].get('mind') is None for b in fit_b if b not in mind_b))
    # The Mind fit on made-up numbers: a 100 FP attack needs a bar of 50 to start; MND 10 gives 40.
    base_m = {'vig': 10, 'mnd': 10, 'end': 10, 'str': 10, 'dex': 10, 'int': 10, 'fth': 10, 'arc': 10}

    def bar_m(m):
        return 4.0 * m

    def share_m(cost, bar):
        return 1.0 if A.AshTables.has_enough_fp(bar, cost) else 0.0

    def dmg_m(key, line):
        return line['int'] / 80.0
    hi_m, hf_m = fit_mind(base_m, 150, 10, 10, ('int',), 80, [(100.0, 'a')], bar_m, share_m, dmg_m)
    lo_m, lf_m = fit_mind(base_m, 150, 10, 10, ('int',), 80, [(10.0, 'a')], bar_m, share_m, dmg_m)
    check(f"25. fit_mind on made-up numbers: a 100 FP attack unpaid on MND 10's 40 FP bar raises MND to 13 (bar 52, "
          f"paid from INT: {hf_m and hf_m['from']}), a 10 FP one keeps MND 10 ({lf_m and lf_m['mnd']}); the INT a point "
          "of MND costs is what the cheap line keeps",
          hi_m is not None and hf_m['mnd'] == 13 == hi_m['mnd'] and hf_m['from'] == {'int': 3} and hf_m['share_base'] == 0.0
          and hf_m['share'] == 1.0 and abs(hf_m['value'] - 77 / 80) < 1e-6
          and lf_m['mnd'] == 10 == lo_m['mnd'] and lo_m['int'] == 80 == hi_m['int'] + 3 and lf_m['from'] == {})

    def screen_ok(b):
        """The weapon a line's offense is measured on is its last screen's best (hit x uninterrupted
        share) of those priced, and that screen's candidates are hardest-first by hit."""
        f = b['rule']['fit']
        wp = next(p for p in f['kit'] if p['kind'] == 'weapon')
        last = f['weapon_screens'][-1] if f['weapon_screens'] else []
        priced = [r for r in last if r.get('value') is not None]
        top = max(priced, key=lambda r: r['value']) if priced else None
        return (top is not None and (top['name'], top['affinity'], top['two']) == (wp['name'], wp['affinity'], wp['two'])
                and [r['hit'] for r in last] == sorted((r['hit'] for r in last), reverse=True))
    check("25. every weapon line's weapon is the one er-mechanics-line-fit.pick_weapon reads off the game at the "
          "fit (the hardest R1 hits with END paid for the weapon's weight, priced with their windup interrupt): "
          + '; '.join(f"{b['name']}: " + ', '.join(f"{r['name']} ({r['affinity']}{', 2h' if r['two'] else ''}, "
                                                    f"{r['weight']:g}, END {r['end']}) hit {r['hit']:g}"
                                                    + (f" x{r['uninterrupted']:.3f}" if r.get('uninterrupted') else '')
                                                    for r in b['rule']['fit']['weapon_screens'][-1][:3])
                      for b in wep_b if b['rule']['fit']['weapon_screens']),
          bool(wep_b) and all(screen_ok(b) for b in wep_b))
    graphs_ = res_.graphs
    check(f"25. {DAMAGE_CAP} and {SPLIT_CAP} are the scaling graphs' second and first soft caps (stageMaxVal3, 2) on "
          "the STR and DEX graphs 0 and 1 and on graph 16, the one both catalysts' spell buff reads",
          all(graphs_[g]['stageMaxVal3'] == DAMAGE_CAP and graphs_[g]['stageMaxVal2'] == SPLIT_CAP for g in (0, 1, 16))
          and all(cat['wep']['correctType_Magic'] == 16 for cat in c.cats))
    # The allocator on made-up classes: A puts more in INT, B in STR and DEX and less in MND.
    cls_s = {'A': {'vig': 10, 'mnd': 12, 'end': 10, 'str': 10, 'dex': 10, 'int': 14, 'fth': 8, 'arc': 8},
             'B': {'vig': 12, 'mnd': 8, 'end': 12, 'str': 12, 'dex': 9, 'int': 10, 'fth': 9, 'arc': 9}}
    use_s = [{'name': 'Quality', 'n': 9, 'carried': 25.0, 'carried_n': 7},
             {'name': 'Intelligence', 'n': 5, 'carried': 20.0, 'carried_n': 4}]
    lines_s = (('Intelligence', ('int',), 30, ('Intelligence',)), ('Quality', ('str', 'dex'), 30, ('Quality',)),
               ('Intelligence + melee', ('int',), 20, ('Intelligence', 'Quality')), ('Arcane', ('arc',), 30, ('Arcane',)))

    def hp_s(v):
        return 20 * min(v, 30) + 11 * min(max(v - 30, 0), 15) + 5 * max(v - 45, 0)
    syn = {b['name']: b for b in attacker_builds(60, use_s, cls_s, hp_s, lambda e: 50.0 + 2 * e,
                                                 {'weight': 30.0, 'poise': 89.0}, 89, lines_s)}
    want_s = {'Quality': {'vig': 45, 'mnd': 8, 'end': 15, 'str': 22, 'dex': 21, 'int': 10, 'fth': 9, 'arc': 9},
              'Intelligence': {'vig': 45, 'mnd': 8, 'end': 12, 'str': 13, 'dex': 13, 'int': 30, 'fth': 9, 'arc': 9},
              'Intelligence + melee': {'vig': 45, 'mnd': 8, 'end': 15, 'str': 17, 'dex': 16, 'int': 20, 'fth': 9,
                                       'arc': 9}}
    check('25. attacker_builds on made-up classes at RL 60: VIG at the knee 45 (+11 then +5), END the least that '
          'keeps 50 or 55 under 0.7 of 50 + 2 END (11, or 12 on a base of 12; 15), MND at the base, the main stats '
          'to their cap then STR and DEX evenly, class B for its STR and DEX over A\'s INT, no line for an archetype '
          'the corpus lacks, the commonest first; allocate_line spills past STR and DEX to every damage stat, and '
          'gives None when the base takes more than the points: '
          + '; '.join(f"{nm} {line_text(b['stats'])} on {b['rule']['class']}" for nm, b in syn.items()),
          {nm: b['stats'] for nm, b in syn.items()} == want_s and list(syn) == ['Quality', 'Intelligence',
                                                                                'Intelligence + melee']
          and all(b['rule']['class'] == 'B' and b['rule']['vig']['knee'] == 45 for b in syn.values())
          and (syn['Intelligence']['rule']['vig']['gain'], syn['Intelligence']['rule']['vig']['gain_after']) == (11, 5)
          and syn['Intelligence']['rule']['end']['least'] == 11 and syn['Quality']['rule']['end']['least'] == 15
          and syn['Intelligence + melee']['rule']['end']['carried'] == 25.0 and syn['Intelligence + melee']['n'] == 0
          and allocate_line({k: 10 for k in ATTRS}, 92, 10, 10, ('str', 'dex'), 12, ())
          == {'vig': 10, 'mnd': 10, 'end': 10, 'str': 13, 'dex': 13, 'int': 12, 'fth': 12, 'arc': 12}
          and allocate_line({k: 10 for k in ATTRS}, 79, 10, 10, ('str',), 80) is None)
    arm = lightest_armor({0: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'h', 'weight': 7.9, 'poise': 13.0000003}],
                          1: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'b', 'weight': 18.9, 'poise': 39.0000008}],
                          2: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'a', 'weight': 6.1, 'poise': 9.9999998}],
                          3: [{'name': '-', 'weight': 0.0, 'poise': 0.0}, {'name': 'l', 'weight': 14.8, 'poise': 27.0000007},
                              {'name': 'L', 'weight': 16.0, 'poise': 28.0000009}]}, 89)
    check('25. lightest_armor counts a float32 9.9999998 as 10 (h, b, a, l at 47.7, not L)',
          arm == {'pieces': ['h', 'b', 'a', 'l'], 'weight': 47.7, 'poise': 89.0})
    selftest_lines_synthetic(check)
    lo_, hi_ = c.rl - S.WINDOW, c.rl + S.WINDOW
    counted = Counter()
    for row in BT.er_corpus.iter_rows(EX.CACHE / 'builds.jsonl'):
        b_ = row.get('build') or {}
        st_ = {k: int((b_.get('stats') or {}).get(BT.PLANNER_KEY.get(k, k)) or 0) for k in ATTRS}
        lv_ = sum(st_.values()) - LEVEL_OFFSET
        if b_.get('isPvE') is not True and lo_ <= lv_ <= hi_:
            counted[BT.stat_archetype(st_, lv_) or NO_ARCHETYPE] += 1
    check(f"25. the usage counts are the corpus's non-PvE builds at RL {lo_}-{hi_} per archetype "
          f"({sum(counted.values())}, {counted.get(NO_ARCHETYPE, 0)} with none), each archetype line's 'n' its own",
          {u['name']: u['n'] for u in c.usage or []} == dict(counted)
          and all(b['n'] == counted[b['name']] for b in c.builds if b['name'] in counted))
    # 13. Synthetic dominance, through `dominance` itself (hp 0.5: a mean gain of 1 is then strict).
    Wu = np.ones(100)
    hp, delta = 0.5, 1.0 / c.n_eff

    def dom(a_, b_, w_=Wu):
        """(edge class or None, equivalent) of a over b."""
        res_ = _toy({'a': a_, 'b': b_}, w_)
        d = dominance(res_, ['a', 'b'], hp, delta, set())
        e = [x for x in d['edges'] if (x['a'], x['b']) == ('a', 'b')]
        return (e[0]['class'] if e else None), bool(d['equivalent'])
    v5, v4, v9 = np.full(100, 5.0), np.full(100, 4.0), np.full(100, 9.0)
    check('13. [5, 5] dominates [4, 4] at equal FP', dom({'net': v5}, {'net': v4})[0] == 'robust')
    check('13. equal vectors at equal FP are equivalent, not dominance', dom({'net': v5}, {'net': v5}) == (None, True))
    check('13. equal vectors at lower FP dominate', dom({'net': v5, 'fp': 8}, {'net': v5})[0] == 'robust')
    check('13. cheaper but worse does not dominate', dom({'net': v4, 'fp': 5}, {'net': v9})[0] is None)
    check('13. b with net <= hp everywhere is never dominated', dom({'net': v9}, {'net': np.full(100, 0.3)})[0] is None)
    check('13. a 2-slot spell does not dominate a 1-slot spell',
          dom({'net': v9, 'type': 'spell', 'slots': 2}, {'net': v4, 'type': 'spell', 'slots': 1})[0] is None)
    lossy = v9.copy()
    lossy[0] = 0.0
    check('13. a loss on 1% of the weight is rejected at delta = 1/n_eff', dom({'net': lossy}, {'net': v4})[0] is None)
    check('13. an edge whose one-hit test fails is full_only',
          dom({'net': np.full(100, 6.0), 'net_one': np.zeros(100)}, {'net': v4})[0] == 'full_only')
    check('13. a loop dominator is full_only', dom({'net': np.full(100, 6.0), 'lower': True}, {'net': v4})[0] == 'full_only')
    # A lingering area ticks once per frame, so its one-hit net is its net and the one-hit test
    # passes: only the 'full count' rule keeps it out of the robust class.
    linger = {'net': np.full(100, 6.0), 'net_one': np.full(100, 6.0)}
    check(f'13. a dominator live {LONG_LIVE_FRAMES + 30} frames, or channelled, whose one-hit test passes is '
          'full_only (full count); the same dominator live 40 frames is robust',
          dom({**linger, 'live_frames': LONG_LIVE_FRAMES + 30}, {'net': v4})[0] == 'full_only'
          and dom({**linger, 'channel': True}, {'net': v4})[0] == 'full_only'
          and dom({**linger, 'live_frames': 40}, {'net': v4})[0] == 'robust')
    wl = np.ones(100)
    wl[0] = 0.05                                    # 0.05 / 99.05 = 0.0005 of the weight, under delta
    spike = np.zeros(100)
    spike[0] = 9.0
    check('13. b that nets something only on less weight than delta is on the nets-nothing list, never dominated',
          dom({'net': v5, 'fp': 5}, {'net': spike}, wl)[0] is None)
    check('13. a that nets nothing dominates nothing, however cheap',
          dom({'net': np.full(100, 0.45), 'fp': 5}, {'net': np.where(np.arange(100) < 50, 0.9, 0.0)})[0] is None)
    w2 = np.ones(100)
    w2[0] = 0.05                                    # 0.05 / 99.05 = 0.0005 of the weight, under delta
    big = np.full(100, 400.0)
    big_a = big.copy()
    big_a[0] -= 200000.0
    check('13. a cheaper a 200,000 HP worse on 0.05% of the weight (mean gain -101) does not dominate',
          dom({'net': big_a, 'fp': 9}, {'net': big}, w2)[0] is None)
    toy = _toy({'b': {'net': v4}, 'tap': {'net': v5, 'parent': 'P'}, 'charged': {'net': np.full(100, 6.0), 'parent': 'P'}},
               Wu)
    d_ = dominance(toy, ['b', 'tap', 'charged'], hp, delta, set())
    o_ = dominance(toy, ['b', 'tap', 'charged'], hp, delta, set(), sibling_only=True)
    rep_ = dominance_report(toy, ['b', 'tap', 'charged'], d_, o_)
    mx = [e['a'] for r in rep_['redundant'] if r['b'] == 'b' for e in r['maximal']]
    check('13. the maximal dominators of b leave out a tap its own charged cast dominates', mx == ['charged'])
    # A robust dominator that a full_only one dominates is still listed, first (round 4: 14 robustly
    # redundant attacks showed only full_only dominators).
    toy2 = _toy({'b': {'net': v4}, 'r': {'net': np.full(100, 6.0)},
                 'f': {'net': np.full(100, 8.0), 'live_frames': LONG_LIVE_FRAMES + 30}}, Wu)
    d2 = dominance(toy2, ['b', 'r', 'f'], hp, delta, set())
    rb2 = next(r for r in dominance_report(toy2, ['b', 'r', 'f'], d2)['redundant'] if r['b'] == 'b')
    check('13. b dominated robustly by r and full_only by f, which dominates r: r is listed first and robust, f after',
          [(e['a'], e['class']) for e in rb2['maximal']] == [('r', 'robust'), ('f', 'full_only')]
          and rb2['class'] == 'robust' and any((e['a'], e['b'], e['class']) == ('f', 'r', 'full_only') for e in d2['edges']))

    def edge(specs, w_, hp_):
        """Edge class of 'a' over 'b' (or None) through `dominance` at `hp_` and delta 1/n_eff."""
        res_ = _toy(specs, w_)
        d = dominance(res_, list(specs), hp_, delta, set())
        e = [x for x in d['edges'] if (x['a'], x['b']) == ('a', 'b')]
        return e[0]['class'] if e else None
    # The loss share is weighted: a 50 HP loss on a situation of weight 0.05 in 99.05 (0.0005, under
    # delta while n_eff is under 1,981) is allowed; unweighted it is 1 situation in 100 (0.01, over
    # delta). At 0.1 in 99.1 (0.001) it went over delta when version 16 took n_eff from 466 to 1,035.
    wls = np.array([0.05] + [1.0] * 99)
    nb_ = np.full(100, 4.0)
    na_ = nb_ + 2.0
    na_[0] = nb_[0] - 50.0
    check('13. weighted loss share: a loss on 0.05 of 99.05 weight (0.0005 <= 1/n_eff) still dominates; counted '
          'per situation it would be 0.01 and fail', edge({'a': {'net': na_}, 'b': {'net': nb_}}, wls, 1.0) == 'robust')
    # The mean gain is weighted: -0.9 on a situation weighing 50 against +1.5 on 50 of weight 1 is
    # 0.3, not strict at hp 1; per situation it would be 1.45 and strict.
    wmg = np.array([50.0] + [1.0] * 50)
    nb2 = np.full(51, 5.0)
    na2 = nb2 + 1.5
    na2[0] = nb2[0] - 0.9
    check('13. weighted mean gain: -0.9 on weight 50 and +1.5 on 50 of weight 1 is a gain of 0.3, not strict at '
          'hp 1, so no edge; per situation it would be 1.45', edge({'a': {'net': na2}, 'b': {'net': nb2}}, wmg, 1.0) is None)
    # No cycle: slot steps each 0.99 worse, closed by a skill 1.485 better than the first and worse
    # than the last (`MEASURED` round 3, synthetic: the old rule listed all five as redundant).
    base_ = 10.0
    cyc = {'x': {'net': np.full(100, base_), 'type': 'spell', 'slots': 1},
           'y': {'net': np.full(100, base_ + 0.99), 'type': 'spell', 'slots': 2},
           'z': {'net': np.full(100, base_ + 1.98), 'type': 'spell', 'slots': 3},
           'v': {'net': np.full(100, base_ + 2.97), 'type': 'spell', 'slots': 4},
           'k': {'net': np.full(100, base_ + 1.485), 'type': 'skill', 'slots': 0}}
    toyc = _toy(cyc, Wu)
    dc_ = dominance(toyc, list(cyc), 1.0, delta, set())
    repc = dominance_report(toyc, list(cyc), dc_)
    pairs_c = {(e['a'], e['b']) for e in dc_['edges']}
    check('13. no dominance cycle: a spell better on slots alone but up to hp worse makes no edge, so the frontier '
          f"is not empty (edges {sorted(pairs_c)})",
          pairs_c == {('v', 'k'), ('k', 'x')} and {x['key'] for x in repc['frontier']} == {'v', 'y', 'z'})

    # Phase 0, in full.
    spell_ids, spell_out = spell_candidates(c)
    skill_jobs, skill_out = skill_candidates(c)
    ex0 = spell_out + skill_out
    exk = {e['key']: e for e in ex0}
    acc = Counter(spell_ids) + Counter(e['id'] for e in spell_out)
    check('6. every SpellModel.names id is a job or excluded, once', set(acc) == set(SM.names) and max(acc.values()) == 1)
    cands = {sid for sid in (set(t.ash_gems()) | {t.reg.weapon[w]['swordArtsParamId'] for w in base_weapons(c)})}
    covered = {j['sid'] for j in skill_jobs} | {e['id'] for e in skill_out}
    check('6. every skill candidate has a job or an exclusion', cands <= covered)
    check("6. 8000 and 8001 have 'no spell goods row'; 4900 and 4910 are jobs",
          all(exk.get(f'spell:{m}', {}).get('reason') == 'no spell goods row' for m in (8000, 8001))
          and {4900, 4910} <= set(spell_ids))
    rev = sorted(g for g, r in c.goods.items() if r['goodsType'] in SPELL_GOODS_TYPES
                 and r['sortId'] != S.UNOBTAINABLE_SORT_ID and g not in SM.names)
    check(f"6. the reverse: every obtainable spell goods row ({sum(1 for r in c.goods.values() if r['goodsType'] in SPELL_GOODS_TYPES and r['sortId'] != S.UNOBTAINABLE_SORT_ID)}) "
          f"is a SpellModel.names spell, so none is missed (missing: {rev[:5]})", not rev)
    check("6. sids 0, 1 and 10 are 'not a skill'",
          all(exk.get(f'skill:{s}', {}).get('reason') == 'not a skill' for s in NOT_A_SKILL if s in cands))
    by_name = {}
    for e in skill_out:
        by_name.setdefault(e['name'], []).append(e['reason'])
    native = {nm: [j for j in skill_jobs if j['name'] == nm] for nm in ('Firebreather', 'Torch Attack', 'Buckler Parry')}
    want_carrier = {'Firebreather': {'Steel-Wire Torch'}, 'Buckler Parry': {'Buckler'},
                    'Torch Attack': {'Torch', 'Ghostflame Torch', 'Beast-Repellent Torch', "Sentry's Torch"}}
    check("6. Firebreather, Torch Attack and Buckler Parry, whose every gem row is unobtainable, are jobs on the "
          "weapons that ship with them (REGULATION: Steel-Wire Torch 223; the torches 117; Buckler 303), never mounted: "
          + '; '.join(f"{nm} on {', '.join(SM.ar.names[j['carrier']] for j in js)}" for nm, js in native.items()),
          all(js and all(SM.ar.names[j['carrier']] in want_carrier[nm] and not j['gem_obtainable'] and not j['mountable']
                         and t.reg.weapon[j['carrier']]['swordArtsParamId'] == j['sid'] for j in js)
              and 'unobtainable' not in by_name.get(nm, []) for nm, js in native.items()))
    shipped = {t.reg.weapon[w]['swordArtsParamId'] for w in base_weapons(c)}
    check("6. 'unobtainable' is left only on a skill no named base weapon ships with",
          all(e['id'] not in shipped for e in skill_out if e['reason'] == 'unobtainable'))
    check("6. the blacklist names are 'blacklisted'",
          all('blacklisted' in by_name.get(nm, []) for nm in c.black if nm in {t.arts_name(s) for s in cands}))
    # 6b. A line's skill rides its best carrier (version 19). Glintstone Pebble's 040000 group at the
    # Intelligence + melee line: its bullet (AtkParam 300200890: MV 0, flat 137 magic, AECP 51080's
    # fixed INT rate) ties every Magic carrier, so only the R2 swing tells them apart; the Dagger, the
    # group weapon of least requirement the line rode before, swings 540.7 against the Omen
    # Cleaver's 821.7 (`MEASURED` 2026-11-16).
    pj = next(j for j in skill_jobs if j['name'] == 'Glintstone Pebble' and j['opening'] == 40000 and not j['fixed'])
    pst = {'str': 26, 'dex': 25, 'int': 60, 'fth': 8, 'arc': 9}
    pjb = build_job(c, pj, pst)
    pdm = _CARRIERS[(job_parent(pj), tuple(pst[k] for k in WEAPON_NEED))]
    pws = [w for w in pj['members'] if c.wields(w, pst)]
    plo = min(pws, key=lambda w: (c.requirement(w), w))
    pcl = at_build(c, pst)

    def pebble_bullet(name):
        cx = A.WeaponContext(name, 'Magic', None, pst, False, ar_tables=SM.ar)
        return round(sum(sum(h['attack'].values()) for h in A.skill_hits(t, t.find_weapon(name), pj['sid'], cx, cx.level)
                         if h['kind'] == 'bullet'), 2)
    check(f"6b. a line rides the group weapon it can wield whose skill damage is largest (build_job): Glintstone Pebble "
          f"040000 at STR 26 / DEX 25 / INT 60 rides {SM.ar.names[pjb['carrier']]} ({pdm[pjb['carrier']]:.1f}, opening "
          f"plus then-R2) over the least-requirement {SM.ar.names[plo]} ({pdm[plo]:.1f}), of {len(pws)} it can wield; "
          f"the bullet before defense is {pebble_bullet('Dagger')} on the Dagger and {pebble_bullet('Omen Cleaver')} on "
          "the Omen Cleaver, both Magic",
          pjb['carrier'] != plo and all(pdm[pjb['carrier']] >= (pdm[w] or 0.0) for w in pws)
          and pdm[pjb['carrier']] > 1.2 * pdm[plo] and pebble_bullet('Dagger') == pebble_bullet('Omen Cleaver')
          and carrier_damage(pcl, pj, pjb['carrier']) == pdm[pjb['carrier']])

    # The subset, scored.
    jobs = default_jobs()
    tm.lap('phase 0 checks')
    res = compute(c, {T._norm(x) for x in SELFTEST_NAMES}, jobs, print, tm, keep=True)
    an = analyze(res, a)
    tm.lap('analyze')
    R = res.records
    check('31. every phase of the subset is a lap of the run\'s timing, every pooled item timed once under its own '
          'name: ' + ', '.join(f"{k} {s['items']}" for k, s in tm.stages.items() if 'items' in s),
          {'candidates', 'inputs', 'objective inputs', 'attacks', 'results'} <= set(tm.stages)
          and all(tm.stages[k]['items'] == len(tm.items[k]) > 0 for k in ('inputs', 'objective inputs', 'attacks'))
          and tm.stages['attacks']['items'] >= len(res.keys) + len(res.comp_keys)
          and all(v[0] >= 0 and v[1] >= 0 for its in tm.items.values() for v in its.values()))
    allk = list(R) + [e['key'] for e in res.excluded]
    check('6. every key has one status, and a group when profiled',
          len(allk) == len(set(allk)) and all(R[k]['status'] in ('scored', 'partial') and R[k].get('group') for k in R))
    have = set(allk)
    sub_ids = [m for m in spell_ids if T._norm(SM.names[m]) in {T._norm(x) for x in SELFTEST_NAMES}]
    check('6. every variant SpellModel.variants offers is an entity or an exclusion (or its whole spell is excluded)',
          all(f'spell:{m}' in have or all(f'spell:{m}:{lb}' in have for lb, _ in SM.variants(m)) for m in sub_ids))
    fb = [r for r in R.values() if r['name'] == 'Firebreather' and not r.get('follow')]
    ta = [r for r in R.values() if r['name'] == 'Torch Attack' and not r.get('follow')]
    bp = [e for e in res.excluded if e['name'] == 'Buckler Parry']
    pops = {b['name']: set(b['population']) for b in an['builds']}
    nofp = {b['name']: set(b['no_fp']) for b in an['builds']}
    fb_in = [nm for nm, p_ in pops.items() if fb and fb[0]['key'] in p_]
    check('6. Firebreather is scored on the Steel-Wire Torch (FP 8: in the population of every attacker build that '
          f"scores it: {fb_in}), Torch Attack on a torch (FP {ta[0]['fp'] if ta else None}: in none, and named in each "
          f"build's no-FP list), Buckler Parry excluded as a parry with no damaging hit ({bp[0]['reason'] if bp else None})",
          bool(fb) and all(r['carrier'] == 'Steel-Wire Torch' for r in fb) and bool(fb_in)
          and all(fb[0]['key'] in pops[b['name']] for b in an['builds']
                  if (b['view'].records.get(fb[0]['key']) or {}).get('status') == 'scored')
          and bool(ta) and all(not r['fp'] for r in ta)
          and not any(r['key'] in p_ for r in ta for p_ in pops.values())
          and all(r['key'] in nofp[b['name']] for r in ta for b in an['builds']
                  if (b['view'].records.get(r['key']) or {}).get('status') == 'scored')
          and bool(bp) and all(e['reason'].startswith('no damaging hit') and 'parry' in e['reason'] for e in bp)
          and not any(e['reason'] == 'unobtainable' for e in res.excluded
                      if e['name'] in ('Firebreather', 'Torch Attack', 'Buckler Parry')))
    # 26. Dominance per attacker build: what a build cannot cast or wield is not in it.
    strength = next((b for b in an['builds'] if b['name'] == 'Strength'), None)
    intel = next((b for b in an['builds'] if b['name'] == 'Intelligence'), None)
    int_spells = [m for m in sub_ids if SM.school[m] == SM.school[SM.by_name['zamor ice storm']]
                  and strength is not None and SM.magic[m]['requirementIntellect'] > strength['stats']['int']]
    int_keys = {k for k in R if R[k]['type'] == 'spell' and R[k]['id'] in int_spells}
    in_str = set() if strength is None else (set(strength['view'].records) | set(strength['population'])
                                             | {x for e in strength['dominance']['edges'] for x in (e['a'], e['b'])})
    check(f"26. the sorceries of the subset that need more INT than the Strength build's {strength and strength['stats']['int']} "
          f"({', '.join(SM.names[m] for m in int_spells)}) are absent from its records, population and edges, and the "
          f"scored ones are in the Intelligence build's population",
          strength is not None and intel is not None and bool(int_keys) and not (int_keys & in_str)
          and all(k in set(intel['population']) for k in int_keys if R[k]['status'] == 'scored'
                  and float(R[k]['fp'] or 0) > 0
                  and not c.cast_short(R[k]['id'], damage_stats(intel['stats']))
                  and (intel['view'].records.get(k) or {}).get('status') == 'scored'))
    check('26. a build\'s records are the attacks it can use: every spell record is castable at its stats and every '
          'skill record rides a carrier it can wield',
          all((not c.cast_short(r['id'], damage_stats(b['stats'])) if r['type'] == 'spell'
               else c.wields(r['carrier_id'], damage_stats(b['stats'])))
              for b in an['builds'] for r in b['view'].records.values()))

    def ents(name, kind=None, var=None):
        return [r for r in R.values() if T._norm(r['name']) == T._norm(name) and (kind is None or r['type'] == kind)
                and (var is None or r.get('variant') == var)]

    def exs(name, kind=None):
        return [e for e in res.excluded if T._norm(e['name']) == T._norm(name) and (kind is None or e['type'] == kind)]

    # 30. The objective evaluation: every scored attack priced at a line that can use it, read off no
    # corpus build.
    def need_met(r, st):
        st = damage_stats(st)
        return not c.cast_short(r['id'], st) if r['type'] == 'spell' else c.wields(r['carrier_id'], st)
    no_obj = [k for k, r in R.items() if any((r.get('objective') or {}).get(nm) is None for nm in OBJECTIVE_LINES)]
    unpriced = [(k, nm, r['objective'][nm].get('status'), r['objective'][nm].get('reason')) for k, r in R.items()
                if k not in no_obj for nm in OBJECTIVE_LINES if r['objective'][nm].get('mean_net') is None]
    check(f'30. every scored and partial entity of the subset ({len(R)}) has both objective lines, each priced '
          f'(no line: {no_obj[:3]}; unpriced: {unpriced[:3]})', bool(R) and not no_obj and not unpriced)
    # Rancor Slash is partial on both carriers (a bullet with no flight, about 200 HP left out):
    # partial entities are priced too.
    tgt = {nm: ents(nm, 'skill') for nm in OBJECTIVE_NAMES}
    check('30. the skills r5 priced in no build get objective nets: ' + '; '.join(
        f"{nm}: " + ', '.join(f"{label(r)} adjusted {r['objective']['adjusted']['mean_net']:.1f} from "
                              f"{r['objective']['adjusted']['from_line']}, minimum {r['objective']['minimum']['mean_net']:.1f}"
                              f" (builds pricing it: {', '.join(priced_by(an, r['key'])) or 'none'})" for r in rs)
        for nm, rs in tgt.items()), all(rs for rs in tgt.values()))
    adj = [(k, r, r['objective']['adjusted']) for k, r in R.items()]
    lines_by = {b['name']: b for b in an['builds']}
    moved = [(k, r, o) for k, r, o in adj if o['raised']]
    check(f"30. every adjusted line meets its attack's requirements, moves damage stats only, and pays every point "
          f"it raised, so it keeps its archetype line's RL ({len(moved)} of {len(adj)} raised a requirement, e.g. "
          + '; '.join(f"{label(r)} from {o['from_line']} {objective_moves(o)}" for _, r, o in moved[:3]) + ')',
          bool(moved) and all(need_met(r, o['stats']) and o['rl'] == o['from_rl'] == lines_by[o['from_line']]['rl']
                              and sum(o['raised'].values()) == sum(o['paid'].values())
                              and all(o['stats'][x] == lines_by[o['from_line']]['stats'][x] for x in ('vig', 'mnd', 'end'))
                              for _, r, o in adj))
    def lowest_rl(o):
        """Whether minimum line `o` is its class's base raised to the need, at an RL no class beats."""
        base = c.classes[o['from_class']]
        rls = [sum(max(cl[x], o['need'].get(x, 0)) for x in ATTRS) for cl in c.classes.values()]
        return (o['stats'] == {x: max(base[x], o['need'].get(x, 0)) for x in ATTRS}
                and o['rl'] == sum(o['stats'].values()) - LEVEL_OFFSET == min(rls) - LEVEL_OFFSET)
    mins = [r['objective']['minimum'] for r in R.values()]
    check('30. every minimum line meets its attack\'s requirements exactly on a starting class\'s base, at the '
          'lowest RL any class gives (e.g. ' + '; '.join(f"{label(r)} RL {r['objective']['minimum']['rl']} on the "
                                                         f"{r['objective']['minimum']['from_class']} base"
                                                         for r in list(R.values())[:2]) + ')',
          all(need_met(r, r['objective']['minimum']['stats']) and lowest_rl(r['objective']['minimum']) for r in R.values())
          and all(reachable(o['stats'], c.classes, c.rl)['ok'] for o in mins))
    check('30. every adjusted line is reachable from a starting class at the RL window',
          all(reachable(o['stats'], c.classes, c.rl)['ok'] for _, _, o in adj))
    same = [(k, o, lines_by[o['from_line']]) for k, r, o in adj if not o['raised']]
    priced_same = [(k, o, b) for k, o, b in same if k in b['view'].index]
    gaps = [abs(o['mean_net'] - b['view'].records[k]['mean_net']) for k, o, b in priced_same]
    check(f"30. an attack its archetype line can already use ({len(same)}) gets that line unchanged, and where that "
          f"build prices it ({len(priced_same)}, e.g. "
          + '; '.join(f"{label(R[k])} at {b['name']} {o['mean_net']:.1f}" for k, o, b in priced_same[:2])
          + f") the objective mean net is the build's (largest gap {max(gaps, default=None)})",
          bool(priced_same) and all(o['stats'] == b['stats'] for _, o, b in same) and max(gaps) <= 1e-6)

    def top_line(stat):
        return max(an['builds'], key=lambda b: b['stats'][stat])['name']

    def best_fit(sw):
        """The attacker build line `line_fit` picks for weights `sw` (ties to the commonest)."""
        return max(enumerate(c.builds), key=lambda ib: (line_fit(ib[1]['stats'], sw), -ib[0]))[1]['name']
    tp, gb = ents('Tremendous Phalanx', 'skill'), ents('Great Oracular Bubble', 'skill')
    tp_open = [r for r in tp if not r.get('follow')]
    tp_r2 = [r for r in tp if (r.get('follow') or {}).get('button') == 'r2']
    own = [r for r in R.values() if r['type'] == 'skill' and sum((r.get('stat_weights') or {}).values()) > 0]
    check("30. the fit reads what the hits scale: Tremendous Phalanx [Moonrithyll's Knight Sword]'s opening (its bullet "
          "AtkParam 700000052, flat magic on AECP 51080) weighs INT alone and fits the line of most INT "
          f"({top_line('int')}: {tp_open and objective_moves(tp_open[0]['objective']['adjusted'])}), not the Quality line "
          "its correct* fields (STR 45 DEX 45 INT 30) fit; Great Oracular Bubble [Envoy's Greathorn] (flat holy) fits "
          f"the line of most FTH ({top_line('fth')})",
          bool(tp_open) and bool(gb)
          and all(r['objective']['adjusted']['scaling_from'] == 'hits' for r in tp_open + gb)
          and all(r['objective']['adjusted']['from_line'] == top_line('int') for r in tp_open)
          and all(r['stat_weights']['int'] > 0 and not any(r['stat_weights'][x] for x in ('str', 'dex', 'fth', 'arc'))
                  for r in tp_open)
          and all(r['objective']['adjusted']['from_line'] == top_line('fth') for r in gb))
    check("30. each entity is fitted on its own hits: a follow-up whose hits scale otherwise than its opening's gets its "
          "own line (Tremendous Phalanx's R2: " + '; '.join(
              f"weights {({x: round(v, 3) for x, v in r['stat_weights'].items() if v})} -> {r['objective']['adjusted']['from_line']}"
              f" {r['objective']['adjusted']['mean_net']:.1f}" for r in tp_r2)
          + f" against the opening's {tp_open and tp_open[0]['objective']['adjusted']['from_line']}), and every skill entity "
          f"with weights ({len(own)}) sits on the line its own weights fit",
          bool(tp_r2) and bool(tp_open)
          and all(r['objective']['adjusted']['scaling_from'] == 'hits' and r['stat_weights'] != tp_open[0]['stat_weights']
                  and r['objective']['adjusted']['from_line'] != tp_open[0]['objective']['adjusted']['from_line']
                  for r in tp_r2)
          and all(r['objective']['adjusted']['from_line'] == best_fit(r['stat_weights']) for r in own))
    # Why the Zamor Curved Sword's skill nets the same at every line (2026-11-10, measured): its f45
    # storm bullets (AtkParam 303401600, 303401601) carry overwriteAttackElementCorrectId 50000, a
    # row with no is*Correct flag, so their worth is flat 110 x baseAtkRate; its f24 slash, the part
    # that scales, never lands on a dodging defender (no dodge weight in any situation).
    zr = min((r for r in ents('Zamor Ice Storm', 'skill') if r['id'] == 1168 and not r.get('follow')),
             key=lambda r: len(r['key']), default=None)
    zd = SM.skill_attack(zr['carrier'], c.stats, zr['affinity'], None, 1168, c.dfn, False, None, None,
                         priced_bullets=True, flown_only=True) if zr else None
    storm = [h for h in (zd['option'] or {}).get('hit_rows') or [] if h['kind'] == 'bullet'] if zd else []
    a50 = SM.ar.aecp.get(50000, {})
    zf = OutcomeForm(c, zd['geo'], zd['roll']) if zd else None
    slash = [i for i, f in enumerate(zf.frames) if f < 30] if zf else []
    zo = zr['objective'] if zr else {}
    check(f"30. the Zamor Curved Sword's skill: its storm bullets ({sorted(h['atk_row'] for h in storm)}) read AECP "
          "50000, which sets no stat flag, so they weigh no stat and their worth is flat; its f24 slash has no dodge "
          "weight in any situation; so its net is the same at the distance basis and both objective lines "
          f"({zr and zr['mean_net']:.2f} / {zo and zo['adjusted']['mean_net']:.2f} / {zo and zo['minimum']['mean_net']:.2f})"
          f" while its full worth is not ({zr and zr['full']} / {zo and zo['adjusted']['full']} / "
          f"{zo and zo['minimum']['full']})",
          zr is not None and len(storm) == 2 and all(h['element_correct_override'] == 50000 for h in storm)
          and bool(a50) and not any(v for k, v in a50.items() if k.startswith('is') and '_by' in k)
          and not any(hit_stat_weights(c, zr['carrier_id'], zr['affinity'], storm).values())
          and bool(slash) and float(np.abs(zf.COEF[:, :, slash]).sum()) == 0.0
          and abs(zo['adjusted']['mean_net'] - zr['mean_net']) < 0.01 and abs(zo['minimum']['mean_net'] - zr['mean_net']) < 0.01
          and zo['adjusted']['full'] != zo['minimum']['full'])

    gfid = SM.by_name["gelmir's fury"]
    # `SpellModel.variants` gives a hold spell whose tap clip casts nothing one cast (its docstring:
    # the breaths, Crystal Barrage, Gelmir's Fury, ...): round 4 had its tap as an exclusion.
    nohit = [(lb, e['key']) for e in res.excluded if e['type'] == 'spell'
             for lb in [e.get('variant')] if e['reason'] == 'no damaging hit (variant)']
    gfe = R.get(f'spell:{gfid}:early')
    check(f"6. Gelmir's Fury, a hold spell, is one held cast and, its 045010 having the tap window, the cast let go "
          f"early ({[lb for lb, _ in SM.variants(gfid)]}), both entities, the early one a variant of the cast over "
          f"{len((gfe or {}).get('releases') or [])} release frames, one per real frame of its window "
          f"({((gfe or {}).get('releases') or [None])[:2]}...); every 'no damaging hit (variant)' exclusion is a "
          f"variant SpellModel.variants offers ({nohit})",
          [lb for lb, _ in SM.variants(gfid)] == ['cast', 'early'] and f'spell:{gfid}:cast' in R
          and gfe is not None and gfe['variant_of'] == f'spell:{gfid}:cast'
          and len(gfe['releases']) == len(SM.releases(gfid, gfe['anim'])) > 6
          and len({w for _, w in gfe['releases']}) == 1
          and not gfe['fp_lower_bound'] and not gfe['channel']
          and all(lb in {x for x, _ in SM.variants(int(k.split(':')[1]))} for lb, k in nohit))

    zid = SM.by_name['zamor ice storm']
    ztap, zch = R.get(f'spell:{zid}:tap'), R.get(f'spell:{zid}:charged')
    zsk = [r for r in ents('Zamor Ice Storm', 'skill') if r['id'] == 1168 and not r.get('follow')]
    zsk = min(zsk, key=lambda r: len(r['key'])) if zsk else None
    gpid = SM.by_name['glintblade phalanx']
    gps = R.get(f'spell:{gpid}:cast') or R.get(f'spell:{gpid}:tap') or next(iter(ents('Glintblade Phalanx', 'spell')), None)
    gsk = [r for r in ents('Glintblade Phalanx', 'skill') if r['id'] == 200 and not r.get('follow')]
    gsk = min(gsk, key=lambda r: (r.get('requirement', 0), len(r['key']))) if gsk else None
    ppid = SM.by_name['glintstone pebble']
    pps = R.get(f'spell:{ppid}:cast')
    psk = [r for r in ents('Glintstone Pebble', 'skill') if not r.get('follow')]
    psk = min(psk, key=lambda r: (r.get('requirement', 0), len(r['key']))) if psk else None
    rsl = ents('Rock Sling', 'spell')
    check('the subset has the Zamor tap, charged and skill 1168, the Glintblade and Pebble spells and skills, Rock Sling',
          None not in (ztap, zch, zsk, gps, gsk, pps, psk) and bool(rsl))

    # 1. net_parts against net_values, with and without the memo.
    def inputs_of(r):
        mid = r['id']
        v = next(x for x in SM.evaluate(mid, next(cc for cc in c.cats if SM.can_cast(mid, cc)), c.stats, (), None,
                                       c.dfn, False, True) if x['variant'] == r['variant'])
        events = sorted({(h['frame'], h['ref']) for h in v['hits']})
        value = {f: sum(h['damage'] * h['count'] for h in v['hits'] if h['frame'] == f) for f, _ in events}
        one = {f: max(h['damage'] for h in v['hits'] if h['frame'] == f) for f, _ in events}
        cat_ = next(cc for cc in c.cats if SM.can_cast(mid, cc))
        geo = SM.geometry(mid, {'events': events, 'hand': v['startup'] - min(f for f, _ in events), 'anim': v['anim'],
                                'weapon': cat_['base']}, damaging=SM.spell_node_damaging, missing=[])
        SM.attach_caught(mid, geo, v['hits'], value)
        # The status each list builds, as `spell_scoring` gives it (version 30).
        annotate_status(c, geo, spell_status_ws(c))
        return {'geo': geo, 'value': value, 'value_one': one, 'roll': v['roll']}

    def spell_inputs(r):
        """The run's own inputs of spell entity `r`: `spell_job` again (`profile_inputs`, its release
        members with it)."""
        return profile_inputs(next(x for x in spell_job(c, r['id']) if x['key'] == r['key']))

    def skill_inputs(r):
        """The run's own inputs of skill entity `r`: `skill_attack` as `skill_entity` calls it."""
        d = SM.skill_attack(r['carrier'], c.stats, r['affinity'], None, r['id'], c.dfn, False, r.get('follow'),
                            r.get('stance'), priced_bullets=True, flown_only=True)
        # The status each list builds, as `skill_entity` gives it (version 30).
        annotate_status(c, d['geo'], skill_status_ws(c, r['carrier_id'], r['affinity']))
        return {k: d[k] for k in ('geo', 'value', 'value_one', 'roll')}
    for r in (ztap, gps):
        x = inputs_of(r)
        nv = S.net_values(A, x['geo'], x['value'], c.pairs, x['roll'], c.strikes, c.dfn)
        p1 = [p['net'] for p in S.net_parts(A, x['geo'], x['value'], c.pairs, x['roll'], c.strikes, x['value_one'],
                                            defender=c.dfn)]
        p0 = [p['net'] for p in S.net_parts(A, x['geo'], x['value'], c.pairs, x['roll'], c.strikes, memo=False,
                                            defender=c.dfn)]
        check(f"1. {label(r)}: net_parts nets == net_values == the replay without the memo", nv == p1 == p0)
    # 2. skill_events against skill_attack; priced bullets.
    zs = SM.skill_events('Zamor Curved Sword', c.stats, defender=c.dfn)
    za = SM.skill_attack('Zamor Curved Sword', c.stats, defender=c.dfn)
    check('2. skill_events is the skill_attack defaults tuple on the Zamor skill',
          zs is not None and zs[1:] == (za['value'], za['roll'], za['fp'], za['name']) and len(zs[0]) == len(za['geo']))
    sw = [r for r in ents("Siluria's Woe", 'skill') if not r.get('follow')]
    if sw:
        r = sw[0]
        lo = SM.skill_attack(r['carrier'], c.stats, r['affinity'], None, r['id'], c.dfn, False, None, r.get('stance'))
        hi = SM.skill_attack(r['carrier'], c.stats, r['affinity'], None, r['id'], c.dfn, False, None, r.get('stance'),
                             priced_bullets=True)
        check("2. Siluria's Woe: priced bullets count more than the spell rule",
              sum(hi['value'].values()) > sum(lo['value'].values()))
    else:
        check("2. Siluria's Woe is scored", False)
    # 3. Default geometry; Swarm of Flies.
    slicer = SM.by_name['carian slicer']
    ev = SM.evaluate(slicer, c.cats[0], c.stats, defender=c.dfn)[0]
    check('3. default SpellModel.geometry is still None for Carian Slicer',
          SM.geometry(slicer, {'events': sorted({(h['frame'], h['ref']) for h in ev['hits']}), 'hand': 0.0}) is None)
    sof = ents('Swarm of Flies', 'spell') + [e for e in exs('Swarm of Flies', 'spell')]
    miss = (S.MISS_DIRECT, S.MISS_TREE, S.MISS_LAUNCH, S.MISS_HEIGHT, S.MISS_OTHER, S.MISS_UNFLOWN)
    check('3. Swarm of Flies flies under the spell rule (scored, or a reason from the list)',
          bool(sof) and all(x['status'] in ('scored', 'partial')
                            or all(r.startswith(miss) for r in x['reason'].split(' + ')) for x in sof))
    # 5. Scale-free pressed at once while his choice to strike or roll holds; the choice is not (it
    # weighs the worth against the R1's fixed cost), nor is the best option (it picks the run-in by
    # the net).
    x = inputs_of(ztap)
    scaled = {k: {**x, 'value': {f: k * v for f, v in x['value'].items()},
                  'value_one': {f: k * v for f, v in x['value_one'].items()}} for k in (1, 2, 4)}
    p1, p2, p4 = (profile_arrays(c, scaled[k]) for k in (1, 2, 4))
    s1, s2, s4 = (np.array([p['now']['strike'] for p in S.net_parts_aims(
        c.A, scaled[k]['geo'], scaled[k].get('aims'), scaled[k]['value'], c.pairs, scaled[k]['roll'], c.strikes,
        scaled[k]['value_one'], defender=c.dfn)]) for k in (1, 2, 4))
    same = (s1 == s2) & (s1 == s4)
    d14 = float(d_rel_rows(rel_profile(p1), rel_profile(p4), c.w)[0, 0])
    moved = float(np.abs(p4['pun'] - p1['pun']).max())
    check(f'5. Zamor tap at 2x and 4x its worth: land_now and pun_now as they were wherever he strikes at all three '
          f'or rolls at all three ({float(c.w[same].sum() / c.W):.3f} of the weight; he strikes at once on '
          f'{float(c.w[s1].sum() / c.W):.3f} at 1x, {float(c.w[s4].sum() / c.W):.3f} at 4x); the best option moves '
          f'(pun by up to {moved:.3f} in one situation), D_rel to itself {d14:.4f} at 4x, under --twin',
          bool(same.any()) and all(np.array_equal(p[k][same], p1[k][same]) for p in (p2, p4)
                                   for k in ('land_now', 'pun_now')) and d14 < a.twin)

    # 7. Reasons.
    def reasons(name):
        out = set()
        for r in ents(name, 'spell'):
            out |= {m[2] for m in r['missing']}
        for e in exs(name, 'spell'):
            out |= set(e['reason'].split(' + '))
        return out
    # Its AtkParam hit (refCategory 0) was 'direct AtkParam ref (refCategory 0)' until 2026-11-20: now
    # a melee hit of the caster's own capsule over the cast event's window
    # (`er-mechanics-spells.SpellModel.direct_geometry`, `VERIFIED` chain there), scored.
    cs_ = ents('Carian Slicer', 'spell')
    check(f"7. Carian Slicer: scored as a melee hit of its own AtkParam, nothing missing "
          f"({[(r['variant'], r['status'], r.get('delivery'), r['full']) for r in cs_]}; reasons "
          f"{sorted(reasons('Carian Slicer'))})",
          bool(cs_) and all(r['status'] == 'scored' and not r['missing'] and r.get('delivery') == ['melee']
                            for r in cs_) and S.MISS_DIRECT not in reasons('Carian Slicer'))
    # Rykard's Rancor reported 'never reaches hurtbox height' while the flight aimed level and low:
    # aimed at the lock point (Spine2 dummy 220, 1.243 m, `VERIFIED` bd er-effects-rs-7bye round 3)
    # its homing skulls reach him, as they do in the game, so it is scored. `MEASURED` 2026-11-12
    # (er-mechanics-ashes sha256 0783e76a..., er-mechanics-spells 3d7c46f3...): one list on bullet
    # 10483005, 4 hits of 285.1 a cast. The height reason is still given (Magic Downpour and Ancient
    # Death Rancor on the final 2026-11-13 flight, ashes 0babd02a..., spells b491d417...), neither in
    # this subset.
    rr, rr_why = ents("Rykard's Rancor", 'spell'), reasons("Rykard's Rancor")
    check(f"7. Rykard's Rancor: scored, its skulls reaching the hurtbox ({[(r['variant'], r['status']) for r in rr]}, "
          f"reasons {sorted(rr_why)})",
          bool(rr) and all(r['status'] == 'scored' for r in rr) and S.MISS_HEIGHT not in rr_why)
    # The flight of `er-mechanics-ashes.fly_members` (2026-11-13) touches him, and a touch makes the
    # launchConditionType 5 child, as the game does on a hit (`er-mechanics-ashes.launches`, `VERIFIED`
    # FUN_14039da40, bd er-effects-rs-rggx); it also follows a caster-attached root, so its interval
    # child spawns. Death Lightning, Watchful Spirit and Roiling Magma, which those two gaps kept
    # excluded or partial, are scored with every hit in their worth (`MEASURED` 2026-11-13, ashes
    # 0babd02a..., spells b491d417...: Death Lightning 1246 tap / 1370 charged and Watchful Spirit 743
    # at the distance basis). The on-hit reason is still given where the carrier does not reach him:
    # Lightning Spear's charged cast (checked here off the model, it is not in this subset). A cast
    # whose every damaging list is made by a carrier that never meets the ground (Loretta's Greatbow)
    # is flown on its carrier's path since 2026-11-20
    # (`er-mechanics-spells.SpellModel.carried_rows`): the game makes the child where it touches him.
    for nm_ in ('Death Lightning', 'Watchful Spirit'):
        got_, why_ = ents(nm_, 'spell'), reasons(nm_)
        check(f"7. {nm_}: scored, every hit in its worth now that the flight makes the child that carries its damage "
              f"({[(r['variant'], r['status'], r['full']) for r in got_]}; reasons {sorted(why_)})",
              bool(got_) and all(r['status'] == 'scored' and not r['missing'] for r in got_) and not why_)
    ls_mid = SM.by_name['lightning spear']
    ls_cat = c.catalyst_for(ls_mid)
    ls_why = {why for v in SM.evaluate(ls_mid, ls_cat, c.stats, (), None, c.dfn, False, True) if v['variant'] == 'charged'
              for _, _, why in spell_scoring(c, ls_mid, ls_cat, v)['missing']}
    check(f"7. Lightning Spear charged: damage only in an on-hit child its carrier never brings to him, "
          f"launchConditionType 5 ({sorted(ls_why)})", f'{S.MISS_LAUNCH} (launchConditionType 5)' in ls_why)
    rmid = SM.by_name['roiling magma']
    rm = [R.get(f'spell:{rmid}:{v}') for v in ('tap', 'charged')]
    # Its on-hit child's list (one connect 731.1 and 923.0 HP, `MEASURED` round 3) was missing while
    # the flight hit no one; now it is in the worth, at the count the record flight gives it (1 each,
    # `MEASURED` 2026-11-13, same files: 501.2 + 731.1 tap, 750.2 + 923.0 charged).
    want_rm = {'tap': (731.1, 10482004), 'charged': (923.0, 10482054)}
    rm_hits = {v['variant']: [h for h in v['hits'] if h['bullet'] == want_rm[v['variant']][1]]
               for v in SM.evaluate(rmid, c.catalyst_for(rmid), c.stats, (), None, c.dfn, False, True)
               if v['variant'] in want_rm}
    check("7. Roiling Magma tap and charged: the on-hit child's list (10482004, 10482054; one connect 731.1 and 923.0 "
          "HP) is made by the carrier's touch and counted in the worth, nothing missing: "
          + '; '.join(f"{r['variant']} {r['status']} full {r['full']}, missing {r['missing_hp']}" for r in rm if r),
          all(r is not None and r['status'] == 'scored' and not r['missing'] and rm_hits.get(r['variant'])
              and all(abs(h['damage'] - want_rm[r['variant']][0]) < 0.5 for h in rm_hits[r['variant']])
              and abs(r['full'] - sum(h['damage'] * h['count'] for v in SM.evaluate(rmid, c.catalyst_for(rmid), c.stats,
                                                                                    (), None, c.dfn, False, True)
                                      if v['variant'] == r['variant'] for h in v['hits'])) < 0.5 for r in rm))
    hb = R.get(f"spell:{SM.by_name['honed bolt']}:cast")
    # `evaluate` no longer prices the water branch (bd er-effects-rs-rggx: `bullet_lists` skips a
    # child neither ending makes on dry ground), so `never_made` names it. 68.3 HP, `MEASURED`
    # 2026-11-12 (ashes 0783e76a..., spells 3d7c46f3...): one list, the flagged 10693005 starting a
    # record that 10693006's 16 shots and their children share (bd er-effects-rs-7bye's rule); 136.7
    # was the same hit counted twice by the lists before that rule.
    check(f"7. Honed Bolt: its water-only bullet 10693006 (launchConditionType 2) is dropped from the worth, not "
          f"missing (dropped {hb and hb['dropped']}, {hb and hb['dropped_hp']} HP, status {hb and hb['status']})",
          hb is not None and abs(hb['dropped_hp'] - 68.3) < 0.5 and any(d_[4] == 2 and d_[3] == 10693006 for d_ in hb['dropped'])
          and not any('launchConditionType 2' in m_[2] for m_ in hb['missing']))
    # Stamp: the follow-up is the opening's own attack clip; two press windows, two keys.
    stamps = [r for r in R.values() if r['name'].startswith('Stamp (') and r['type'] == 'skill']
    sfol = [r for r in stamps if r.get('follow')]
    club = [r for r in sfol if r['name'] == 'Stamp (Sweep)' and r['carrier'] == 'Club']
    club0 = [r for r in stamps if r['name'] == 'Stamp (Sweep)' and r['carrier'] == 'Club' and not r.get('follow')]
    f10 = [r for r in club if r['follow']['first'] == 10]
    check(f"3. Stamp: no hit counted in two parts at one frame; every follow-up leaves out the opening's hits from "
          f"its press on; Stamp (Sweep) on the Club keeps both R2 windows ({sorted(r['key'] for r in club)}), "
          'the f10 one worth what the opening is worth',
          bool(sfol) and all(r.get('cross_part_duplicates') == 0 for r in R.values() if r['type'] == 'skill')
          and all(r.get('opening_dropped', 0) > 0 for r in sfol)
          and sorted(r['follow']['first'] for r in club) == [10, 18] and len({r['key'] for r in club}) == 2
          and bool(club0) and len(f10) == 1 and f10[0]['value'] == club0[0]['value'] and f10[0]['fp'] > club0[0]['fp'])
    base_of = {r['key']: r for r in stamps if not r.get('follow')}
    same10 = [(r['key'], r['value'] == base_of[r['variant_of']]['value']) for r in sfol
              if r['follow']['first'] == 10 and r['variant_of'] in base_of]
    check(f"3. every Stamp follow-up pressed at f10 is worth what its opening is worth, also where the opening is "
          f"scored on another class's clip (Large Club: 040010 under 040210): {same10}",
          bool(same10) and all(ok for _, ok in same10))
    check("12. a Stamp opening, scored on its FP-charging follow-up clip, carries an FP lower bound (its "
          f"{sorted({r['fp'] for r in base_of.values()})} FP leaves that clip's charge out); a skill whose opening "
          "hits does not (Glintblade Phalanx, Zamor)",
          bool(base_of) and all(r['fp_lower_bound'] and 'has no hit' in r.get('fp_lower_why', '')
                                for r in base_of.values())
          and not gsk['fp_lower_bound'] and not zsk['fp_lower_bound'])
    # 8. Groups, by land mass.
    # Rock Sling's damage is all on its 1.5 m blast 10471012 (atk 47100), made only on a hit
    # (launchConditionType 5) by the homing rocks 10471009-10471011 (`REGULATION`). A rock is a
    # carrier (`er-mechanics-ashes.carrier`: opposeTarget, isPenetrateChr 0, no damage of its own):
    # it dies on the defender and makes the blast there. The flight before 2026-11-13 hit no one, so
    # it never made a launchConditionType 5 child (check 7 above: Death Lightning, Roiling Magma), and
    # the pin said Rock Sling never reaches, which a staple PvP sorcery that hits players does not
    # do: the pin held a defect of that flight. Now the cast reaches an idle
    # defender in every situation and lands 0.079 of its worth pressed at once, 0.238 with a run-in
    # (`MEASURED` 2026-11-13, `--only` run).
    check(f"8. Rock Sling: every variant reaches an idle defender in every situation and is live "
          f"({[(r['variant'], round(r['reach'], 3), round(r['land_mass_now'], 4), r['group']) for r in rsl]})",
          bool(rsl) and all(r['group'] == 'live' and r['reach'] >= 0.99 for r in rsl))
    # Since version 11 a defender free before an attack first touches him may strike instead of
    # rolling (attack-similarity.md section 2d, `er-mechanics-spells.net_parts`), and what has left
    # the attacker before his strike breaks him lands on him standing. So an attack every roll
    # evades can land pressed at once, but only where he trades his R1 for it: it is live, and
    # nets at most --hp ('punished as much as it lands'). These pins were the roll-only defender's
    # until the version 13 commit, whose selftest was not run (its commit message). With the
    # strikes handed in as plain frames (the roll alone) every value pinned before comes back:
    # Gravitas nets_nothing 'always evaded' on every carrier, the Gravity Well tap runin_only (land
    # 0.0033 pressed at once, 0.3567 with a run-in), the Glintblade Phalanx spell runin_only
    # (0.0001, 0.0357), Starcaller Cry nets_nothing at -87.8 (`MEASURED` 2026-11-15, `compute` on
    # these names at the distance basis with and without the strike; identical on the committed
    # version 13 code, so neither version 14 nor 15 moved them).
    gv = [r for r in ents('Gravitas', 'skill') if not r.get('follow')]
    gv = min(gv, key=lambda r: (r.get('requirement') or 0, r['key'])) if gv else None
    check(f"8. Gravitas on its least-requirement carrier ({gv and gv['carrier']}): every roll evades it, so the "
          f"{gv and round(gv['land_mass_now'], 4)} of its worth it lands pressed at once is the defender's strike "
          f"trading for it (live, '{PUNISHED_LIVE}'); its best option lands {gv and round(gv['land_mass_best'], 4)} "
          f"and it nets {gv and round(gv['mean_net'], 1)}, nothing",
          gv is not None and gv['group'] == 'live' and PUNISHED_LIVE in gv['group_flags']
          and gv['land_mass_best'] < a.null and gv['mean_net'] <= a.hp)
    gw = ents('Gravity Well', 'spell', 'tap') or ents('Gravity Well', 'spell')
    cs_ = ents('Collapsing Stars', 'spell', 'tap') or ents('Collapsing Stars', 'spell')
    # Collapsing Stars' tap is live: its stars 10472105 leave each orb at 45 m/s on the orb's own
    # heading after the orb has homed at 150 deg/s, and home at 180 deg/s from their first move
    # (`REGULATION`), so a back roll, which leaves him on their line, never clears them at 2.6 to
    # 5.8 m: one roll direction of four caught pressed at once, 0.25 of its worth to 4.0 m and 0.22
    # to 5.8 m (`MEASURED` 2026-11-13, its first release read alone). Gravity Well's tap (10472000,
    # 90 deg/s) is cleared in every direction pressed at once and lands only after a run-in to a
    # rolling defender; what it lands pressed at once (0.2464, `MEASURED` 2026-11-15) is the strike's
    # trade, and it nets -181.9.
    check(f"8. Gravity Well's tap, which every roll pressed at once clears, is live only by the defender's strike "
          f"({gw[0]['group'] if gw else None}, {gw and round(gw[0]['land_mass_now'], 4)} pressed at once, nets "
          f"{gw and round(gw[0]['mean_net'], 1)}, '{PUNISHED_LIVE}'); Collapsing Stars' tap is live, landing "
          f"{cs_[0]['land_mass_now'] if cs_ else float('nan'):.4f} of its worth pressed at once",
          bool(gw) and bool(cs_) and gw[0]['group'] == 'live' and PUNISHED_LIVE in gw[0]['group_flags']
          and gw[0]['mean_net'] < -a.hp and cs_[0]['group'] == 'live')
    gm = ents('Gravitational Missile', 'spell', 'tap')
    check('8. Gravitational Missile tap is live', bool(gm) and gm[0]['group'] == 'live')
    check(f"8. Glintblade Phalanx spell lands {gps['land_mass_now']:.4f} of its worth pressed at once, "
          f"{gps['land_mass_best']:.4f} with a run-in: live only by the defender's strike, which it pays for (nets "
          f"{gps['mean_net']:.1f}, '{PUNISHED_LIVE}'); to the roll alone it is runin_only",
          gps['group'] == 'live' and PUNISHED_LIVE in gps['group_flags'] and gps['mean_net'] <= a.hp)
    bs = [r for r in ents('Bloody Slash', 'skill') if not r.get('follow')]
    check('8. Bloody Slash, punished on most of the weight but landing almost nothing, is never live',
          bool(bs) and all(r['group'] == 'nets_nothing' for r in bs) and max(r['live_now'] for r in bs) > 0.5)
    # Starcaller Cry was live by the defender's strike, -152.9 (`MEASURED` 2026-11-15), while he
    # pressed the four roll directions a quarter each: the strike was his pick on 0.49 of the weight
    # and traded into 0.044 of its worth pressed at once. Since version 22 he picks the roll direction
    # that leaves the attacker least (`er-mechanics-ashes.dodge_families`, attack-similarity.md section
    # 2a 'One stage, one rule'), one of which escapes it whole in every situation: evade 1.0, no strike,
    # nothing lands, punished on 0.954 of the weight, -370.2, so it is not live and 'lands little,
    # punished more' (`MEASURED` 2026-11-22: bisected single-process, v21 -120.7 and v22 -348.3 at
    # land 0, then v25 on -370.2; on the current code with the four directions put back into one
    # family, -151.8, land 0.0436, strike on 0.491). The pull it applies, which might hold him in a
    # side roll, is not modelled (module doc, 'Not established'). The Zamor Ice Storm casts
    # (nets_nothing, -220.0 tap, -350.9 charged) carry the not-live flag in the subset now.
    sc = [r for r in ents('Starcaller Cry', 'skill') if not r.get('follow')]
    zsc = [r for r in (ztap, zch) if r is not None]
    notlive = [r for r in R.values() if r['group'] != 'live']
    live_ = [r for r in R.values() if r['group'] == 'live']
    check(f"8. Starcaller Cry, evaded whole in some roll direction in every situation, nets "
          f"{', '.join(f'{r['mean_net']:.1f}' for r in sc)} on average, is not live and carries "
          f"'{PUNISHED_NOT_LIVE}'; the Zamor Ice Storm casts, not live, net "
          f"{', '.join(f'{r['mean_net']:.1f}' for r in zsc)} and carry '{PUNISHED_NOT_LIVE}'; so does every attack of "
          f"the subset that is not live and nets under -hp ({sum(r['mean_net'] < -a.hp for r in notlive)} of "
          f"{len(notlive)}), and no other, and every live one netting at most hp carries '{PUNISHED_LIVE}'",
          bool(sc) and all(r['group'] != 'live' and r['mean_net'] < -a.hp and PUNISHED_NOT_LIVE in r['group_flags']
                           for r in sc)
          and len(zsc) == 2 and all(r['group'] != 'live' and r['mean_net'] < -a.hp
                                    and PUNISHED_NOT_LIVE in r['group_flags'] for r in zsc)
          and all((PUNISHED_NOT_LIVE in r['group_flags']) == (r['mean_net'] < -a.hp) for r in notlive)
          and all((PUNISHED_LIVE in r['group_flags']) == (r['mean_net'] <= a.hp) for r in live_))
    # 9. Zamor.
    Mx = an['matrices']
    Dn, Drm, Dam = Mx['D_now'], Mx['D_rel'], Mx['D_assoc']
    twins = set(an['twins']) | {(b, a_) for a_, b in an['twins']}
    i_t, i_c, i_s = res.index[ztap['key']], res.index[zch['key']], res.index[zsk['key']]
    check('9. Zamor tap and charged are sibling keys and never twins',
          ztap['parent'] == zch['parent'] and (ztap['key'], zch['key']) not in twins)
    # The storms alike: read to the roll alone. Since version 11 the defender may strike instead of
    # rolling (attack-similarity.md section 2d), and his strike reads each clip's own windup, TAE 795
    # windows and guard, so it parts a 12-frame cast from a storm that leaves at f45 of a skill clip
    # whatever the storms are. With the strike the tap read alone is punished pressed at once on
    # 0.379 of the weight and the skill on 0.619, and the tap read alone is D_now 0.126 from the
    # skill and its storm frame D_land 0.905 from it; to the roll alone (the strikes as plain frames,
    # `er-mechanics-spells.Strikes`) D_now 0.00525, and the storm frame D_now 0.00459, D_land 0.0387,
    # D_rel 0.377, the pins of the 2026-11-13 runs (`MEASURED` 2026-11-15; the run's tap, read with
    # its charged cast, is D_now 0.0163 from the skill to the roll alone, 0.0382 with the strike).
    cr = copy.copy(c)
    cr.strikes = np.array(np.asarray(c.strikes), dtype=float)

    def alone(cc, x):
        return {n: np.asarray(v)[None] for n, v in profile_arrays(cc, x).items() if n in ARRAYS}
    zt_in = profile_inputs(next(x for x in spell_job(c, ztap['id']) if x['key'] == ztap['key']))
    zs_in = skill_inputs(zsk)
    zt1, zt1r, zs1r = alone(c, zt_in), alone(cr, zt_in), alone(cr, zs_in)
    dn_roll = float(d_now_rows(zt1r['land_now'], zt1r['pun_now'], zs1r['land_now'], zs1r['pun_now'], res.w, res.W)[0, 0])
    check(f'9. D_now(Zamor tap, skill 1168) <= 0.02 read alone to the roll alone (got {dn_roll:.5f}): alike pressed at '
          f'once; the run\'s pair, which the strike reads, is {Dn[i_t, i_s]:.5f}', dn_roll <= 0.02)
    zc = [k for k in res.comp_keys if res.comps[k]['of'] == zsk['key']]
    late = max(zc, key=lambda k: res.comps[k]['frame']) if zc else None
    # The tap read alone, as a defender who knows it from its first frame reads it: the skill's
    # storm is read so (no group), and the run's tap is read with its charged cast (check 32). The
    # storm frame is made from the skill's own inputs as `components` makes it, and must read as the
    # run's component does with the strike.
    dl = dland = drel = dgrp = dland_s = same = None
    if late:
        ci = res.cindex[late]
        zf = res.comps[late]['frame']
        zcomp = {'geo': [(g, fr) for g, fr in zs_in['geo'] if fr == zf], 'value': {zf: zs_in['value'][zf]},
                 'value_one': {zf: zs_in['value_one'][zf]}, 'roll': zs_in['roll']}
        zc1 = alone(c, zcomp)
        same = all(np.allclose(zc1[n][0], res.C[n][ci], atol=1e-6, equal_nan=True) for n in ('land_now', 'pun_now', 'land', 'pun'))
        zc1r = alone(cr, zcomp)
        dl = float(d_now_rows(zc1r['land_now'], zc1r['pun_now'], zt1r['land_now'], zt1r['pun_now'], res.w, res.W)[0, 0])
        dland = float(d_rel_rows(rel_profile(zc1r, 0, LAND_CHANNELS), rel_profile(zt1r, 0, LAND_CHANNELS), res.w)[0, 0])
        drel = float(d_rel_rows(rel_profile(zc1r, 0), rel_profile(zt1r, 0), res.w)[0, 0])
        dland_s = float(d_rel_rows(rel_profile(res.C, ci, LAND_CHANNELS), rel_profile(zt1, 0, LAND_CHANNELS), res.w)[0, 0])
        dgrp = float(d_rel_rows(rel_profile(res.C, ci, LAND_CHANNELS), rel_profile(res.A, i_t, LAND_CHANNELS), res.w)[0, 0])
    # Since the caught defender walks out (bd er-effects-rs-n7dd) he takes one tick of either storm,
    # 1 of the tap's 5 and 1 of the skill's 4 (`SpellModel.bullet_lists`), so their land shares part
    # by that much (D_land about 0.2) where the full count made them equal.
    check(f'9. the Zamor skill storm component ({late}, made from the skill\'s inputs: the run\'s {same}) lands, to the '
          f'roll alone, what the tap read alone lands pressed at once (D_now {dl}, 0.01 or less), apart from the one '
          f'tick of 5 or of 4 a caught defender takes (D_land {dland}, 0.25 or less), and differs most in the punish '
          f'chance (D_rel {drel}): it rides the skill\'s recovery. The strike, which reads the skill\'s clip, parts them '
          f'(D_land {dland_s}); the run\'s tap, read with its charged cast (version 8), is D_land {dgrp} from it',
          dl is not None and same and dl <= 0.01 and dland <= 0.25 and drel > dland + 0.01 and dland_s > dland)
    # A caught defender takes one tick of either storm and walks out (bd er-effects-rs-n7dd): one of the
    # tap's ticks and one of the skill storm's (`SpellModel.bullet_lists`). Land shares are shares of
    # the full count, so the two storms are alike only when those counts are equal.
    def storm_ticks(rec, frame=None):
        return max((l['n'] for g, f in rec['geo'] if frame is None or f == frame for l in g['CAUGHT']['lists']),
                   default=None)
    tick_t = storm_ticks(spell_inputs(ztap))
    tick_s = storm_ticks(skill_inputs(zsk), res.comps[late]['frame'] if late else None)
    alike = tick_t == tick_s
    contained = any(x['x'] == zsk['key'] and x['y'] == ztap['key'] and x['component'] == late
                    and x['D_contain'] <= a.twin for x in an['contains'])
    check(f'9. skill 1168 contains the Zamor tap (that storm component, by D_contain within --twin) exactly when a '
          f'caught defender takes the same share of both storms (ticks: tap {tick_t}, skill {tick_s}; contained '
          f'{contained})', tick_t is not None and contained == alike)
    zpair = tuple(sorted((ztap['key'], zsk['key']), key=lambda k: res.index[k]))
    zkind = an['twin_kind'].get(zpair) or {}
    through = zkind.get('by') == 'component' and zkind.get('component') == late and any(
        x['spell'] == ztap['key'] and x['skill'] == zsk['key'] and x['by'] == 'component' for x in an['matches'])
    check(f"9. the whole skill is D_assoc {Dam[i_t, i_s]:.3f} from the tap, over --twin (the skill lands "
          f"{zsk['land_mass_best']:.3f} of its worth with a run-in against the tap's {ztap['land_mass_best']:.3f}), "
          f"and they are twins and a cross-type match through the storm component ({zkind}) exactly when its "
          f"storm ticks as often as the tap's", Dam[i_t, i_s] > a.twin and through == alike)
    # Over 0.2 while a caught defender took every tick; with him walking out of both storms after
    # one (bd er-effects-rs-n7dd) they part by less, still past the twin cut.
    check(f'9. D_now(Zamor charged, skill) > --twin {a.twin} (got {Dn[i_c, i_s]:.4f}): not alike pressed at once',
          Dn[i_c, i_s] > a.twin)
    zb = [b for b in an['builds'] if ztap['key'] in b['population'] and zch['key'] in b['population']]
    # Since the release frame is the caster's choice (version 9) the tap nets nothing there (the
    # basis -4.9 against the charge's 388.8, `MEASURED` 2026-11-11), and an attack that nets nothing
    # is listed, never dominated.
    check(f"9. in every attacker build that casts Zamor Ice Storm ({', '.join(b['name'] for b in zb)}) the charged cast "
          "dominates the tap in its own-variant table, or the tap nets nothing there "
          f"({[(b['name'], ztap['key'] in b['own']['nets_nothing']) for b in zb]})",
          bool(zb) and all(any(e['a'] == zch['key'] and e['b'] == ztap['key'] for e in b['own']['edges'])
                           or ztap['key'] in b['own']['nets_nothing'] for b in zb))
    both_z = [b['name'] for b in an['builds'] if zsk['key'] in b['view'].records and ztap['key'] in b['view'].records]
    check(f"9. the Zamor skill and the tap, which the distance basis's all-99 dominance once compared, are in one "
          f"attacker build only where it wields the Zamor Curved Sword and casts the spell ({both_z or 'none'})",
          all(c.wields(zsk['carrier_id'], damage_stats(b['stats'])) and not c.cast_short(ztap['id'], damage_stats(b['stats']))
              for b in an['builds'] if b['name'] in both_z))
    # 10. Glintblade Phalanx: the spell and the ash fly the same blades from equivalent launches
    # (`REGULATION`: the roots 10430000 / 2300 and the children 10430001 / 2301 share every flight
    # field, the roots life -1 and FollowType 1, the children 8 m/s, life 1 s, homing 30 deg/s after
    # 1 m and lockShootLimitAng 30; the roots differ only in numShoot 5 / 4 and hitRadius 0.1 / 0.3 m;
    # `MEASURED` 2026-11-13, both launched from DummyPoly 10100's place, the spell's at (-0.04, 1.0,
    # 0.33) m from its cast event at f17 after a 12-frame hand-off, the ash's at (-0.15, 1.0, 0.33) m at
    # f33). Until 2026-11-13 the ash's flight again was not aimed at its shots' creation while the
    # spell's was (`er-mechanics-spells.SpellModel.flight` `aim`), and that alone gave the ash twice the
    # spell's run-in share (0.1429 against 0.0714; either flag swapped, D_rel 0.0). Read alone the two
    # are one profile. The run reads the spell with the cast it cannot be told from (Carian Phalanx,
    # check 32), which halves its run-in share (0.0357, `MEASURED` the same day) and puts it under
    # --live, so the pair is no twin; that split is the lookalike group, not the flight.
    # The flights are compared to the roll alone, as check 9 compares the storms: the defender's
    # strike (version 11, attack-similarity.md section 2d) reads the cast clip and the skill clip,
    # not the blades, and with it the spell read alone is D_rel 0.598 from the ash read alone, each
    # landing about 0.026 pressed at once where he trades his R1 for it; to the roll alone D_rel 0.0,
    # both one flat run-in share of 0.0714 (`MEASURED` 2026-11-15, the pins of 2026-11-13).
    i_g, i_k = res.index[gps['key']], res.index[gsk['key']]
    Dim = Mx['D_idle']
    gp_in = profile_inputs(next(x for x in spell_job(c, gps['id']) if x['key'] == gps['key']))
    gp1 = alone(c, gp_in)
    gp1s = shares(gp1, 0, res.w, res.W)
    gp1r, gk1r = alone(cr, gp_in), alone(cr, skill_inputs(gsk))
    d_alone = float(d_rel_rows(rel_profile(gp1r, 0), rel_profile(gk1r, 0), res.w)[0, 0])
    d_strike = float(d_rel_rows(rel_profile(gp1, 0), rel_profile(res.A, i_k), res.w)[0, 0])
    fl_alone = flat_land(gp1r, 0, shares(gp1r, 0, res.w, res.W)['land_mass_now'], a.null)
    fl_ash = flat_land(gk1r, 0, shares(gk1r, 0, res.w, res.W)['land_mass_now'], a.null)
    # The cross-type match is mutual-nearest over every spell and skill of the subset; one of the
    # spell's is required only to be with a Glintblade Phalanx skill.
    gmatch = [x['skill'] for x in an['matches'] if x['spell'] == gps['key']]
    gtwin = (gps['key'], gsk['key']) in twins or (gsk['key'], gps['key']) in twins
    check(f'10. Glintblade Phalanx spell and ash ({gsk["key"]}) fly alike: to the roll alone the spell read alone is '
          f'D_rel {d_alone:.4f} from the ash, both landing one flat run-in share ({fl_alone}, {fl_ash}); the strike, '
          f'which reads their clips, puts them D_rel {d_strike:.4f} apart; read with its lookalike group '
          f'the spell lands {gps["land_mass_best"]:.4f} with a run-in against {gp1s["land_mass_best"]:.4f} alone, and '
          f'they are twins ({gtwin}) exactly when both land at least --live ({gps["land_mass"]:.3f}, '
          f'{gsk["land_mass"]:.3f}) within --twin (D_assoc {Dam[i_g, i_k]:.4f}, D_rel {Drm[i_g, i_k]:.4f}, D_idle '
          f'{Dim[i_g, i_k]:.4f}); any cross-type match of the spell is a Glintblade Phalanx skill ({gmatch})',
          d_alone <= 0.01 and fl_alone is not None and fl_alone == fl_ash
          and gps['land_mass_best'] < gp1s['land_mass_best']
          and gtwin == (min(gps['land_mass'], gsk['land_mass']) >= a.live and Dam[i_g, i_k] <= a.twin)
          and all((R.get(k) or {}).get('id') == 200 for k in gmatch))
    # Every match and twin row says which term binds. (Until 2026-11-13 this also required the
    # Glintblade Phalanx twin to rest on D_idle; with the ash aimed as the spell is, above, there is
    # no such twin in this subset.)
    check("10. every match and twin row says which term binds, as its numbers give",
          all(x['bound'] == ('idle' if an['idle_rate'] * x['D_idle'] >= max(x['D_rel'], x['D_land']) - 1e-4 else 'land')
                  or abs(an['idle_rate'] * x['D_idle'] - max(x['D_rel'], x['D_land'])) < 1e-4
                  for x in an['matches'] if x['by'] == 'whole')
          and all('bound' in v and 'rests_on_idle' in v for v in an['twin_kind'].values()))
    # 4w69: a follow-up's frames before its press are the opening's.
    gfol = [r for r in ents('Glintblade Phalanx', 'skill') if r.get('follow') and r['id'] == 200]
    comp_of = {}
    for k_ in res.comp_keys:
        comp_of.setdefault(res.comps[k_]['of'], []).append(res.comps[k_]['frame'])
    check(f"10. the Glintblade Phalanx R2 follow-ups keep the opening's volley before the press "
          f"({[(r['follow']['first'], r['inherited_frames']) for r in gfol]}) and get no component there, so no twin, "
          "match or contains runs through a frame before a follow-up's press",
          bool(gfol) and all(r['inherited_frames'] and not set(r['inherited_frames']) & set(comp_of.get(r['key'], []))
                             for r in gfol)
          and not [x for x in an['contains'] + an['component_twins']
                   if res.comps[x['component']]['frame'] in set(R[x['x']].get('inherited_frames') or [])])
    # The mixup (bd er-effects-rs-me5p; the user, 2026-11-16: "what is called a 50/50"): on a
    # Glintblade Phalanx R2 follow-up its fixed sequence is the net `parts` gives with `mix` False,
    # the scored net (version 20) is the game's value, which adds the defender's stand and the
    # attacker's default delays (no opening roll given, so no 'none' column), and the game is a
    # distribution on both sides in every situation. Its opening's blades are search-launched, so
    # it has no strafe row at any share (`er-mechanics-ashes.FollowupModel.strafe_ok`).
    if gfol:
        fr_ = gfol[0]
        fx = {**skill_inputs(fr_), 'press': float(fr_.get('press', fr_['follow']['first'])),
              'delays': followup_delays(fr_['follow'])}
        ff = FollowupForm(c, fx)
        fp_ = ff.parts(fx['value'], fx['value_one'], fx['geo'], mix=False)
        fs_ = ff.parts(fx['value'], fx['value_one'], fx['geo'])
        fm_ = ff.mixup(fx['value'], fx['geo'])
        f1_ = ff.mixup(fx['value'], fx['geo'], strafe_share=1.0)
        check(f"10. FollowupForm.mixup on {label(fr_)}: its fixed sequence is parts' net with mix False in every "
              f"situation (max gap {max(abs(a['fixed'] - b['net']) for a, b in zip(fm_, fp_)):.2e}), the scored "
              f"parts' net is the game's; the game's mean {float(c.w @ np.array([p['net'] for p in fm_]) / c.W):.1f} "
              f"against the fixed {float(c.w @ np.array([p['fixed'] for p in fm_]) / c.W):.1f}; no strafe row at "
              f"share 1 ({ff.model.strafe_ok()})",
              all(abs(a['fixed'] - b['net']) <= 1e-6 * max(1.0, abs(b['net'])) for a, b in zip(fm_, fp_))
              and all(a['net'] == b['net'] and a['fixed'] == q['net'] for a, b, q in zip(fs_, fm_, fp_))
              and all(0.0 <= p['stand'] + p['strafe'] <= 1.0 + 1e-9 and p['none'] == 0.0 for p in fm_)
              and not ff.model.strafe_ok()
              and all(p['strafe'] == 0.0 and p['net'] == q['net'] for p, q in zip(f1_, fm_)))
    # Live negative controls: each pair lands at least --live of its worth on both sides, D_assoc
    # must keep them apart, and no association may link them. Each was within --twin on D_rel alone
    # on the full run it was picked from, so the control bites; the profiles move with the models
    # they are read from (round 5: Sword Dance / Radagon's Rings of Light charged went to D_rel 0.152
    # after that day's edits to er-mechanics-ashes and er-mechanics-spells), so it is required that
    # all but one still bite. The round-2 controls (`APART_CONTROLS`) are only kept apart.
    def skill_ent(name, carrier, button):
        got = [r for r in R.values() if r['type'] == 'skill' and r['name'] == name and r['carrier'] == carrier
               and (r.get('follow') or {}).get('button') == button and not r.get('stance')]
        return min(got, key=lambda r: r['key']) if got else None
    contained = {(x['x'], x['y']) for x in an['contains']}
    matched = {(x['spell'], x['skill']) for x in an['matches']}
    bites, live, apart_read = [], [], 0
    for ctl, ((snm, carrier, button), (pnm, var)) in ([('control', p) for p in NEGATIVE_CONTROLS]
                                                     + [('apart', p) for p in APART_CONTROLS]):
        sk, sp = skill_ent(snm, carrier, button), R.get(f"spell:{SM.by_name[T._norm(pnm)]}:{var}")
        ok = sk is not None and sp is not None
        alive = False
        if ok:
            i, j = res.index[sk['key']], res.index[sp['key']]
            alive = sk['status'] == sp['status'] == 'scored' and min(sk['land_mass'], sp['land_mass']) >= a.live
            if ctl == 'control':
                live.append(alive)
                if alive:
                    bites.append(bool(Drm[i, j] <= a.twin))
            else:
                apart_read += 1
            # A control that no longer lands --live on a side is no control: since the launches are
            # placed (bd er-effects-rs-doax) Magic Glintblade's blade forms 0.2 m ahead, not 1 m, and
            # a reacting defender rolls every press (its run-in to 1.5 m nets nothing).
            ok = not alive or (Dam[i, j] > a.twin and (sk['key'], sp['key']) not in twins
                               and (sp['key'], sk['key']) not in matched and (sk['key'], sp['key']) not in contained
                               and (sp['key'], sk['key']) not in contained)
        check(f"10. {'negative' if ctl == 'control' else 'round-2'} control {snm} [{carrier}]"
              f"{' ' + button if button else ''} / {pnm} ({var}): "
              + (f"land {sk['land_mass']:.3f} / {sp['land_mass']:.3f} ({'live' if alive else 'not live: no control'}), "
                 f"D_rel {Drm[i, j]:.4f} ({'<=' if Drm[i, j] <= a.twin else '>'} --twin), D_idle {Dim[i, j]:.4f}, "
                 f"D_assoc {Dam[i, j]:.4f}: no twin, match or contains" if sk is not None and sp is not None
                 else 'missing'), ok)
    check(f'10. at least three negative controls are live ({sum(live)} of {len(live)})', sum(live) >= 3)
    check(f'10. live negative controls that still bite: {sum(bites)} of {len(bites)} within --twin on D_rel '
          'alone (all but one; re-pick NEGATIVE_CONTROLS from a full run when this fails)',
          sum(bites) >= len(bites) - 1)
    check(f'10. every control is read ({len(live)} of {len(NEGATIVE_CONTROLS)} negative, {apart_read} of '
          f'{len(APART_CONTROLS)} round-2)',
          len(live) == len(NEGATIVE_CONTROLS) and len(bites) == sum(live) and apart_read == len(APART_CONTROLS))
    # Painful Strike's f34 whip frame against a glintblade volley: within --twin by D_contain on the
    # profiles alone, kept apart only by how each delivers its worth. Round 3 measured it against
    # Carian Phalanx (D_contain 0.056); since the launches are placed and the caught defender walks
    # out (bd er-effects-rs-doax / er-effects-rs-n7dd) that pair is 0.51 apart, and the 2026-11-10
    # full run puts the Glintblade Phalanx cast nearest, at 0.089 (Troll's Roar's f43 against the
    # charged Wrath of Gold, 0.082, is the only nearer melee frame). Since the defender's strike
    # (version 11) the pair is 0.76 to 0.93 apart, and the 2026-11-15 full run (stamp
    # 4a87d5408c1fba25) has no melee-only skill frame within --twin by D_contain of any bullet spell
    # (0 of 252 frames against 124 spells, all landing --live; `MEASURED` 2026-11-16), so no pair of
    # the game tests the gate on the profiles alone any more: check 7 tests it on synthetic rows, and
    # this pair is only kept apart.
    ps = [r for r in R.values() if r['name'] == 'Painful Strike' and r['carrier'] == 'Tooth Whip' and not r.get('follow')]
    cph = R.get(f"spell:{SM.by_name['glintblade phalanx']}:cast")
    pcomp = [k for k in res.comp_keys if ps and res.comps[k]['of'] == ps[0]['key'] and abs(res.comps[k]['frame'] - 34.0) < 0.6]
    dpc = None
    if pcomp and cph is not None:
        ci_, yi_ = res.cindex[pcomp[0]], res.index[cph['key']]
        BC_ = base_share(res.C, exact_full(res.comps, res.comp_keys))
        BA_ = base_share(res.A, exact_full(R, res.keys))
        dpc = round(float(d_contain_rows(rel_profile(res.C, ci_, LAND_CHANNELS), BC_[[ci_]], res.C['t_first'][[ci_]],
                                         rel_profile(res.A, yi_, LAND_CHANNELS), BA_[[yi_]], res.A['t_first'][[yi_]],
                                         res.w, res.W, res.F, an['idle_rate'])[0, 0]), 5)
    linked = ({(x['x'], x['y']) for x in an['contains']} | {(x['y'], x['x']) for x in an['contains']}
              | {(x['spell'], x['skill']) for x in an['matches']} | {(x['skill'], x['spell']) for x in an['matches']}
              | twins)
    check(f"10. negative control Painful Strike [Tooth Whip] (melee only) / Glintblade Phalanx cast (bullets): its "
          f"f34 frame is D_contain {dpc} from the spell, and the delivery gate "
          "keeps them out of every twin, match and contains",
          bool(ps) and cph is not None and dpc is not None
          and ps[0]['delivery'] == ['melee'] and cph['delivery'] == ['bullet']
          and (ps[0]['key'], cph['key']) not in linked)
    bad_gate = [p for p in linked if p[0] in R and p[1] in R and not (delivery_bits(R[p[0]]) & delivery_bits(R[p[1]]))]
    bad_gate += [(x['component'], x['y']) for x in an['contains'] + an['component_twins']
                 if not (delivery_bits(res.comps[x['component']]) & delivery_bits(R[x['y']]))]
    check(f'10. every twin, match and contains shares a delivery (bad: {bad_gate[:3]})', not bad_gate)
    bad_disp = [p for p in linked if p[0] in R and p[1] in R and displace_bits(R[p[0]]) != displace_bits(R[p[1]])]
    bad_disp += [(x['component'], x['y']) for x in an['contains'] + an['component_twins']
                 if displace_bits(R[res.comps[x['component']]['of']]) != displace_bits(R[x['y']])]
    check(f'10. every twin, match and contains moves or holds the target alike, {DISPLACE_TOKENS} '
          f'(bad: {bad_disp[:3]})', not bad_disp)
    i_p, i_q = res.index[pps['key']], res.index[psk['key']]
    ovp = overlap_share(res, pps['key'], psk['key'])
    check(f"10. Glintstone Pebble spell and ash, an explained non-twin: the spell lands {pps['land_mass']:.4f} of its "
          f"worth, under --live, its profile lies inside the ash's ({ovp:.3f} shared) and the ash lands "
          f"{psk['land_mass']:.4f}, D_rel {Drm[i_p, i_q]:.3f}",
          (pps['key'], psk['key']) not in twins and pps['land_mass'] < a.live and ovp >= 0.9
          and psk['land_mass'] >= 1.5 * pps['land_mass'] and Drm[i_p, i_q] > a.twin)
    # 11. Tokens.

    def toks(name, kind=None):
        out = set()
        for r in ents(name, kind) + exs(name, kind):
            out |= set(r.get('tokens') or []) | set(r.get('flags') or [])
        return out
    pulls = ('Gravitas', "Alabaster Lords' Pull", 'Starcaller Cry', 'Gravity Well', 'Collapsing Stars',
             'Gravitational Missile')
    check("11. 'pull' on " + ', '.join(pulls), all('pull' in toks(x) for x in pulls))
    check("11. 'push' on Onyx Lords' Repulsion", 'push' in toks("Onyx Lords' Repulsion"))
    check("11. 'status:frost' on both Zamor sorcery variants and skill 1168",
          'status:frost' in set(ztap['tokens']) and 'status:frost' in set(zch['tokens']) and 'status:frost' in set(zsk['tokens']))
    check("11. 'homing' on Gravity Well, not on the Zamor tap", 'homing' in toks('Gravity Well', 'spell')
          and 'homing' not in set(ztap['flags']))
    check("11. 'jumpable' on Rellana's Twin Moons", 'jumpable' in toks("Rellana's Twin Moons", 'spell'))
    lf = exs('Lifesteal Fist', 'skill')
    check("11. Lifesteal Fist, whose only hit is a grab the model does not price: excluded 'grab not modelled "
          "(throwTypeId 20)', flagged 'grab', and in the grab family as an exclusion",
          bool(lf) and all(e['reason'] == 'grab not modelled (throwTypeId 20)' and 'grab' in e['flags'] for e in lf)
          and {e['key'] for e in lf} <= {x['key'] for x in an['effect'].get('grab', [])})
    poison = {x['key']: x for x in an['effect'].get('status:poison', [])}
    pm, dps = exs('Poison Mist', 'spell'), exs('Deadly Poison Spray', 'skill')
    check("11. Poison Mist and Deadly Poison Spray, which deal no damage, carry 'status:poison' and sit in the poison "
          "family as exclusions",
          bool(pm) and bool(dps) and all('status:poison' in e['tokens'] and e['key'] in poison
                                         and poison[e['key']]['group'].startswith('excluded: ') for e in pm + dps))
    pull = {x['key']: x for x in an['effect'].get('pull', [])}
    gmc = R.get(f"spell:{SM.by_name['gravitational missile']}:charged")
    # Gravitational Missile's charged cast is live, so its family neighbour is by D_assoc among the
    # skills of the family that land, and by D_idle only when none does. To the roll alone none did
    # (Gravitas, Alabaster Lords' Pull, Starcaller Cry: nets_nothing, 'always evaded'); since the
    # defender's strike (version 11, attack-similarity.md section 2d) each lands a share pressed at
    # once where he trades his R1 for it, and none nets above --hp (`MEASURED` 2026-11-15: Gravitas
    # 0.0 to -157.8, Alabaster Lords' Pull 0.0, Starcaller Cry -152.9; the neighbour Gravitas
    # [Reverse-Bladed Sword], D_assoc 0.829).
    pskills = [x for x in pull.values() if x['type'] == 'skill' and x['key'] in R]
    gnear = ((pull.get(gmc['key']) if gmc else None) or {}).get('nearest') or {}
    check(f"11. the pull family gives Gravitational Missile a D_assoc neighbour among its skills that land "
          f"({gnear.get('key')}, {gnear.get('by')} {gnear.get('D')}), every skill of it landing only by the defender's "
          f"strike and netting nothing ({[(x['name'], x['group'], x['mean_net']) for x in pskills]})",
          gmc is not None and gnear.get('by') == 'D_assoc' and gnear.get('key') in R
          and R[gnear['key']]['type'] == 'skill' and R[gnear['key']]['group'] in ('live', 'runin_only')
          and bool(pskills) and all(x['mean_net'] <= a.hp for x in pskills))
    # 12. Loop FP.
    gf = ents("Gelmir's Fury", 'spell', 'cast')
    check(f"12. Gelmir's Fury, one held cast: FP {gf and gf[0]['fp']} (Magic.mp 16), loop {gf and gf[0]['fp_loop']} "
          "(3 per tick), a lower bound",
          bool(gf) and gf[0]['fp'] == 16 and gf[0]['fp_loop'] == 3 and gf[0]['fp_lower_bound'] is True)
    check('12. Zamor charged: FP 17, no loop', zch['fp'] == 17 and zch['fp_loop'] is None)
    def edge_rules(b):
        Rb, E = b['view'].records, b['dominance']['edges']
        nothing = set(b['dominance']['nets_nothing'])
        return (all(e['class'] == 'full_only' for e in E if Rb[e['a']].get('fp_lower_bound') or full_count(Rb[e['a']]))
                and not ({e['b'] for e in E} | {e['a'] for e in E}) & nothing
                and all(e['gain'] >= -a.hp for e in E) and b['report']['cycle'] is None
                and all(r['maximal'] and r['maximal'][0]['class'] == 'robust'
                        for r in b['report']['redundant'] if r['class'] == 'robust'))
    check('13. in every attacker build: every edge from a loop dominator or a full-count dominator is full_only, no '
          'nets-nothing entity is on either end of an edge, every edge keeps a mean gain of at least -hp, the edges '
          'make no cycle, and a robustly redundant attack lists a robust dominator first: '
          + '; '.join(f"{b['name']} {len(b['dominance']['edges'])} edges" for b in an['builds']),
          all(edge_rules(b) for b in an['builds']))
    def rollup_rules(b):
        Rb = b['view'].records
        bad_full = [x['spell'] for x in b['rollup']
                    if any(e['type'] == 'spell' and e['id'] == x['id'] for e in b['view'].excluded)
                    or any(r['type'] == 'spell' and r['id'] == x['id'] and r['status'] != 'scored' for r in Rb.values())]
        return not bad_full and all(x['not_dominated'] for x in b['rollup_part'])
    check('24. in every attacker build, no spell with an excluded or partial variant is rolled up as redundant, and '
          'every spell dominated on its landing variants only names a variant that is not: '
          + '; '.join(f"{b['name']} {len(b['rollup'])} + {len(b['rollup_part'])}" for b in an['builds']),
          all(rollup_rules(b) for b in an['builds']))
    # m6e8: a charged skill's default entity is the full charge; its early release is a variant.
    cg = [r for r in ents('Carian Grandeur', 'skill') if not r.get('follow')]
    cgt = [r for r in ents('Carian Grandeur', 'skill') if r.get('release') == 'tap']
    check(f"27. Carian Grandeur: the default entity holds the charge ({[(r['key'], r.get('release'), r['full']) for r in cg]}) "
          f"and its early release at the {RELEASE_EARLY_SPEFFECT} window's first frame is a variant of its own "
          f"({[(r['key'], r['full'], r['fp']) for r in cgt]}: worth less, the same FP, a sibling)",
          bool(cg) and bool(cgt) and all(r.get('release') == 'full' for r in cg)
          and all(t_['variant_of'] in {r['key'] for r in cg} and t_['parent'] == R[t_['variant_of']]['parent']
                  and t_['full'] < R[t_['variant_of']]['full'] and t_['fp'] == R[t_['variant_of']]['fp']
                  and t_['follow']['anim'] == R[t_['variant_of']]['opening'] + 1 for t_ in cgt))
    cgd = [(r.get('press'), r.get('release_rule'), r.get('releases'), r.get('inherited_frames')) for r in cgt]
    cgi = [profile_inputs(next(x for j in skill_jobs if job_parent(j) == r['parent'] for x in skill_job(c, j)
                               if x['key'] == r['key'])) for r in cgt]
    check(f"27. and the release is let go on any frame of its window (c0000.hks SwordArtsOneShot_onUpdate: L2 up on "
          f"any frame of {RELEASE_EARLY_SPEFFECT}), the attacker's choice: press at its first frame, releases "
          f"{[x[2] for x in cgd]} (release_frames); no opening hit comes before it, so it is read as a lookalike of "
          f"its hold, the mean of its release frames ({[x[1] for x in cgd]}), not by FollowupForm",
          bool(cgd) and all(p == float(r['follow']['first']) and rule == 'mixture' and not inh
                            and [a for a, _ in rel] == [round(x_, 3) for x_, _ in A.release_frames(
                                r['follow']['first'], r['follow']['last'])]
                            and abs(sum(w for _, w in rel) - 1.0) < 1e-5
                            for (p, rule, rel, inh), r in zip(cgd, cgt))
          and all('press' not in x and len(x['members']) == len(rel) - 1 for x, (_, _, rel, _) in zip(cgi, cgd)))
    # d3qk: the carrier's own status, through the hits that are from the weapon.
    bf = [r for r in ents("Bloodhound's Finesse", 'skill') if not r.get('follow')]
    check(f"28. Bloodhound's Finesse on {bf and bf[0]['carrier']}: its melee hits are from the weapon, so the "
          f"carrier's bleed rows {bf and bf[0]['weapon_status']} make it 'status:bleed'; the Glintblade Phalanx ash on "
          f"{gsk['carrier']} ({gsk['affinity']}) has no weapon status",
          bool(bf) and all('status:bleed' in r['tokens'] and r['weapon_status'] for r in bf) and not gsk['weapon_status'])
    # 16. The stamp.
    pd = {'Bullet.xml': [1, 2]}
    s1 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd)[0]
    s2 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd)[0]
    s3 = stamp({**stats, 'str': 98}, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'},
               paramdefs=pd)[0]
    s4 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'},
               version=SIMILARITY_VERSION + 1, paramdefs=pd)[0]
    s5 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'},
               paramdefs={'Bullet.xml': [1, 3]})[0]
    hk = hks_stamp(A)
    s6, ins6 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                     hks=hk)
    s7 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
               hks={**hk, 'parser': 'other'})[0]
    check('16. the stamp is equal for equal inputs and moves with the stats, the version, the paramdefs and the HKS '
          'inputs',
          s1 == s2 and len({s1, s3, s4, s5, s6, s7}) == 6 and len(paramdef_stamp()) > 0)
    sb1 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                builds=c.builds)[0]
    sb2 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                builds=[dict(b) for b in c.builds])[0]
    sb3 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                builds=c.builds[:-1])[0]
    moved = [{**b, 'stats': {**b['stats'], 'int': b['stats']['int'] + 1}} if i == 0 else b for i, b in enumerate(c.builds)]
    sb4 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                builds=moved)[0]
    check('16. the stamp moves with the attacker build set: one build fewer, or one attribute of one line, is another '
          'stamp; the same set is the same stamp', sb1 == sb2 and len({s1, sb1, sb3, sb4}) == 4)
    ms = members_stamp()
    sm1 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                members=ms)[0]
    sm2 = stamp(stats, c.catalysts, 150, False, c.situations, c.strikes, c.dfn, code={'x': '1'}, paramdefs=pd,
                members={**(ms or {}), 'sha256': 'other'})[0]
    stale = None
    try:
        require_fresh_members({**(ms or {}), 'built': 'other'})
    except SystemExit as e:
        stale = str(e)
    check(f"16. the stamp moves with the opponent members cache (built {ms and ms['built']}, its sources "
          f"{ms and ms['sources']}), which is fresh, and a run refuses one built from other sources",
          ms is not None and ms['built'] == ms['sources'] and len({s1, sm1, sm2}) == 3
          and stale is not None and '--build-cache' in stale)
    check(f"16. the HKS inputs are the parser {HKS_PARSER.name}, which no module attribute holds (so code_stamp "
          f"cannot see it), and {A.COMMON_DEFINE_HKS}",
          ins6['hks']['parser'] == hashlib.sha256(HKS_PARSER.read_bytes()).hexdigest()
          and ins6['hks']['common_define'][0] == str(A.COMMON_DEFINE_HKS) and HKS_PARSER.name not in code_stamp())
    # 17. Fork safety.
    _SIM['ctx'] = c
    _SIM['inputs'] = {}
    for r in (ztap, zsk, gsk):
        if r['type'] == 'spell':
            _SIM['inputs'][r['key']] = spell_inputs(r)
        else:
            _SIM['inputs'][r['key']] = skill_inputs(r)
    ks = list(_SIM['inputs'])
    # Two builds that fly the distance basis's own geometry at its worth: the form is checked
    # against the profile on the way, and their nets must be the profile's. Each reads the key with
    # the distance basis's group, as the run did (the Zamor tap's charged cast).
    _SIM['builds'] = {k: {0: _SIM['inputs'][k], 1: _SIM['inputs'][k]} for k in ks}
    run_looks = res.lookalikes['looks']
    _SIM['looks'] = {(k, w): run_looks[(k, None)] for k in ks if (k, None) in run_looks for w in (None, 0, 1)}
    _SIM['look_store'] = res.lookalikes['store']
    one_ = {k: (arr, nets, errs) for k, arr, nets, errs in pooled(_profile_work, ks, 1, 'fork check')}
    tm2 = Timing()
    two_ = {k: (arr, nets, errs) for k, arr, nets, errs in pooled(_profile_work, ks, 2, 'fork check', tm2)}
    tm2.lap('fork check')
    check('17. --jobs 2 gives the arrays and the build nets --jobs 1 gives',
          all(np.array_equal(one_[k][0][m], two_[k][0][m], equal_nan=True) for k in ks for m in ARRAYS)
          and all(np.array_equal(one_[k][1][b][j], two_[k][1][b][j]) for k in ks for b in (0, 1) for j in (0, 1)))
    check(f"31. a forked item is timed in its worker: each of the {len(ks)} keys once under its own name, the "
          f"workers' {tm2.stages['fork check']['workers_cpu']:.2f} CPU s their own items' sum",
          sorted(tm2.items['fork check']) == sorted(ks) and tm2.stages['fork check']['items'] == len(ks)
          and tm2.stages['fork check']['workers_cpu'] > 0
          and abs(tm2.stages['fork check']['workers_cpu']
                  - sum(v[1] for k, v in tm2.items['fork check'].items() if k != ks[0])) < 1e-2)
    check('17. and the run\'s own arrays for those keys', all(
        np.array_equal(one_[k][0][m], res.A[m][res.index[k]], equal_nan=True) for k in ks for m in ARRAYS))
    check('17. a build on the distance basis\'s geometry and worth is form-checked once per key and release frame '
          f"both read ({ {k: len(one_[k][2]) for k in ks} }), within FORM_TOL, and its nets are the profile's",
          all(1 <= len(one_[k][2]) <= 1 + len(_SIM['inputs'][k].get('members') or ()) and set(one_[k][2]) == {0.0}
              and form_error(one_[k][1][0][0], one_[k][0]['net']) == 0.0
              and form_error(one_[k][1][1][1], one_[k][0]['net_one']) == 0.0 for k in ks))
    _SIM.clear()
    # 29. The outcome form against `net_parts`, in float64, at the entity's worth and at a worth
    # changed frame by frame by different factors (what an attacker build changes): a spell, a
    # follow-up of two frames, a jumpable spell (two move families) and the subset's skill of most
    # bursts.
    rtm = next(iter(ents("Rellana's Twin Moons", 'spell')), None)
    many = max((r for r in R.values() if r['type'] == 'skill' and r['status'] == 'scored' and not r.get('release')),
               key=lambda r: (r['frames'], r['key']))
    probes_ = [(ztap, spell_inputs(ztap)), (gfol[0], skill_inputs(gfol[0])), (rtm, spell_inputs(rtm)),
               (many, skill_inputs(many))]
    errs_ = []
    for r, x in probes_:
        form = OutcomeForm(c, x['geo'], x['roll'])
        fr_ = sorted(x['value'])
        for scale in (None, 'per frame'):
            mult = {f: (1.0 if scale is None else 0.6 + 0.5 * i) for i, f in enumerate(fr_)}
            v = {f: x['value'][f] * mult[f] for f in x['value']}
            v1 = {f: x['value_one'][f] * mult[f] for f in x['value_one']}
            parts = S.net_parts(A, x['geo'], v, c.pairs, x['roll'], c.strikes, v1, defender=c.dfn)
            want, want1 = np.array([p['net'] for p in parts]), np.array([p['net_one'] for p in parts])
            got, got1 = form.net(v, x['geo']), form.net(v1, x['geo'])
            errs_.append((label(r), scale, len(_geo_bursts(x['geo'])), len(form.PE[0]),
                          float(max(np.abs(got - want).max(), np.abs(got1 - want1).max()))))
    check('29. OutcomeForm.net equals net_parts\' net and net_one within 1e-6 HP: '
          + '; '.join(f"{nm} ({sc or 'as scored'}, {nb} bursts, {nf} families) {e:.1e}" for nm, sc, nb, nf, e in errs_),
          rtm is not None and any(nf == 2 for *_, nf, _ in errs_) and any(nb >= 2 for _, _, nb, _, _ in errs_)
          and all(e <= 1e-6 for *_, e in errs_))
    check('29. the inputs re-read for those entities are the run\'s', all(x['value'] == R[r['key']]['value'] for r, x in probes_))
    # 30. A follow-up is scored under the press the attacker picks (bd er-effects-rs-me5p): the
    # run's profile is `FollowupForm`'s (version 20: the mixup's value, check 10), its fixed
    # sequence never below the press at once in any situation, and a defender who does not dodge
    # meets the press at once.
    fx = profile_inputs({**R[gfol[0]['key']], **skill_inputs(gfol[0])})
    ff = FollowupForm(c, fx)
    fa = ff.parts(fx['value'], fx['value_one'], fx['geo'])
    fe = ff.parts(fx['value'], fx['value_one'], fx['geo'], see=False)
    i_f = res.index[gfol[0]['key']]
    fnet, enet = np.array([p['net'] for p in fa]), np.array([p['net'] for p in fe])
    fxd = np.array([p['fixed'] for p in fa])
    check(f"30. {label(gfol[0])}: press {fx['press']}, delays {fx['delays'][0]:g}-{fx['delays'][-1]:g}; the run's "
          f"nets are FollowupForm's (mean {c.w @ fnet / c.W:.1f}), its fixed sequence at least the press at once's "
          f"everywhere (mean {c.w @ fxd / c.W:.1f} against {c.w @ enet / c.W:.1f}, delayed on "
          f"{c.w @ np.array([p['delayed'] for p in fa]) / c.W:.3f} of the weight), and base_now is the press at once's",
          np.array_equal(res.A['net'][i_f], fnet.astype(np.float32)) and bool((fxd >= enet - 1e-9).all())
          and np.array_equal(res.A['base_now'][i_f], np.array([p['now']['base'] for p in fe], np.float32)))
    # 30. The mixup at `STRAFE_SHARE` 0 (version 20) against its bounds, on Ghostflame Ignition then R1 on
    # Death's Poker, where the attacker's blind delays help: in every situation the game's value is at most
    # what the defender's best pure reply concedes ('pure'), and over the situations it is at least the
    # fixed sequence's (406.4 -> 432.5 on the distance basis this check reads, 361.5 -> 386.1 read
    # alone at Intelligence + melee, `MEASURED` 2026-11-16).
    gi_sid, gi_wid = t.find_arts('Ghostflame Ignition'), t.find_weapon("Death's Poker")
    gi_fol = [f for f in A.skill_followups(t, gi_sid, gi_wid, SM.ar.max_level(SM.ar.weapons[gi_wid]['reinforceTypeId']),
                                           'one', None) if f['button'] == 'r1'][:1]
    if gi_fol:
        gd = SM.skill_attack("Death's Poker", c.stats, 'Standard', None, gi_sid, c.dfn, False, gi_fol[0], None,
                             priced_bullets=True, flown_only=True)
        gx = {'geo': gd['geo'], 'value': gd['value'], 'value_one': gd['value_one'], 'roll': gd['roll'],
              'press': float(gd['option']['parts'][1]['offset']), 'delays': followup_delays(gi_fol[0])}
        gm = FollowupForm(c, gx).mixup(gx['value'], gx['geo'])
        gnet, gfix = np.array([p['net'] for p in gm]), np.array([p['fixed'] for p in gm])
        check(f"30. FollowupForm.mixup at STRAFE_SHARE {STRAFE_SHARE:g} on Ghostflame Ignition then R1 (press "
              f"{gx['press']:g}, delays 0-{gx['delays'][-1]:g}): at most the defender's best pure reply in every "
              f"situation (largest excess {float(max(p['net'] - p['pure'] for p in gm)):.2e}), and its mean "
              f"{c.w @ gnet / c.W:.1f} at least the fixed sequence's {c.w @ gfix / c.W:.1f}",
              STRAFE_SHARE == 0.0 and all(p['net'] <= p['pure'] + 1e-6 * max(1.0, abs(p['pure'])) for p in gm)
              and float(c.w @ gnet) >= float(c.w @ gfix) - 1e-6)
    else:
        check("30. Ghostflame Ignition on Death's Poker has an R1 follow-up (skill_followups)", False)
    # 32. What is on screen (`look_specs`, `er-mechanics-spells.Lookalikes`): the groups the run read,
    # an attack alone in its group read number for number as before, the kit's rule on the same
    # casts, and the group in the fingerprint.
    LK = res.lookalikes
    line0 = stats_key(c.stats)

    def mates(r):
        """The (name, variant or release key) of the other members of `r`'s distance-basis group."""
        out = []
        for m, _ in r.get('lookalikes') or []:
            e = LK['store'].entries[(line0, m)]
            out.append((SM.names[e['mid']], m.rsplit(':', 1)[1]) if 'mid' in e else ('release', m))
        return sorted(out)

    def mates_by(r):
        """`mates` as {(name, variant): [release frames after the earliest, 0 for the variant's own]}."""
        out = {}
        for nm, v in mates(r):
            b, _, d = v.partition('~')
            out.setdefault((nm, b), []).append(float(d) if d else 0.0)
        return {k: sorted(v) for k, v in out.items()}

    def frames_of(mid, anim, own=False):
        """The release frames after the earliest of spell `mid`'s release clip `anim`, with 0 unless `own`."""
        return [float(k) for k in range(1 if own else 0, len(SM.releases(mid, anim)))]
    sst, ggs = (SM.by_name[x] for x in ('star shower', 'great glintstone shard'))
    gst = SM.by_name['glintstone stars']
    tap_anim = R[f'spell:{sst}:tap']['anim']
    cgh = next((r for r in ents('Carian Grandeur', 'skill') if r.get('release') == 'full'), None)
    cgt_ = [r for r in ents('Carian Grandeur', 'skill') if r.get('release') == 'tap' and cgh and r['variant_of'] == cgh['key']]
    bad_lines = [(k, w) for (k, w), sp in LK['looks'].items() if 'mid' in LK['store'].entries[(sp['line'], sp['me'])]
                 for m in sp['keys'] for e in [LK['store'].entries[(sp['line'], m)]]
                 if c.cast_short(e['mid'], dict(zip(WEAPON_NEED, sp['line'])))
                 or look_key(SM, e['mid']) != look_key(SM, LK['store'].entries[(sp['line'], sp['me'])]['mid'])]
    check(f"32. the groups the run read at the distance basis (each release by the frames after its earliest it is "
          f"let go at): Star Shower tap with {mates_by(R[f'spell:{sst}:tap'])}; Glintstone Pebble with {mates(pps)}, "
          f"Great Glintstone Shard left out of the subset and run for the group alone; the Zamor tap with "
          f"{mates_by(ztap)}; the Glintblade Phalanx cast with {mates(gps)}; the Carian Grandeur hold with its early "
          f"release, split where each release shows, its press f{cgt_ and cgt_[0]['press']} plus its visible delay "
          f"({cgh and cgh['lookalikes'][:3]}...); every "
          f"member at every line is castable there and of the attack's TimeAct and casting SFX (bad: {bad_lines[:3]})",
          mates_by(R[f'spell:{sst}:tap']) == {('Glintstone Stars', 'charged'): [0.0],
                                              ('Glintstone Stars', 'tap'): frames_of(gst, tap_anim),
                                              ('Star Shower', 'charged'): [0.0],
                                              ('Star Shower', 'tap'): frames_of(sst, tap_anim, own=True)}
          and len(frames_of(sst, tap_anim)) > 3
          and mates(pps) == [('Great Glintstone Shard', 'cast')] and f'spell:{ggs}:cast' not in R
          and (line0, ggs) in LK['store'].ran and tm.stages['lookalike inputs'].get('items', 0) > 0
          and mates_by(ztap) == {('Zamor Ice Storm', 'charged'): [0.0],
                                 ('Zamor Ice Storm', 'tap'): frames_of(zid, tap_anim, own=True)}
          and len(frames_of(zid, tap_anim)) > 3 and mates(gps) == [('Carian Phalanx', 'cast')]
          and cgh is not None and len(cgt_) == 1
          and LK['store'].taps[(line0, cgh['key'])] == {cgt_[0]['key']} | {
              member_key(cgt_[0]['key'], r_ - float(cgt_[0]['follow']['first']))
              for r_, _ in A.release_frames(cgt_[0]['follow']['first'], cgt_[0]['follow']['last'])[1:]}
          and sorted(cgh['lookalikes']) == sorted(
              [k, round(LK['store'].entries[(line0, k)]['press'] + LK['store'].entries[(line0, k)].get('shows', 0.0), 2)]
              for k in LK['store'].taps[(line0, cgh['key'])])
          and all(LK['store'].entries[(line0, k)].get('shows', 0.0) > 0 for k in LK['store'].taps[(line0, cgh['key'])])
          and not bad_lines)

    def run_inputs(r):
        """`profile_inputs` of spell entity `r` as phase 1 made it (`spell_job` again, free aims too)."""
        return profile_inputs(next(x for x in spell_job(c, r['id']) if x['key'] == r['key']))
    sing = next((r for r in R.values() if r['type'] == 'spell' and r['status'] == 'scored' and r['lookalikes'] is None
                 and not run_inputs(r)['aims']), None)
    cx = copy.copy(c)
    cx.pairs, cx.sprint_pairs, cx.w = c.pairs[::8], c.sprint_pairs[::8], c.w[::8]
    cx.W = float(cx.w.sum())
    sx = run_inputs(sing) if sing else None
    lone = S.Lookalikes(A, {'x': sx['geo']}, {}).member('x') if sing else None
    s_run = profile_arrays(c, sx) if sing else None
    s_old, s_one = (profile_arrays(cx, sx), profile_arrays(cx, sx, look=lone)) if sing else (None, None)
    check(f"32. an attack alone in its group is read as before, number for number: {sing and label(sing)} has no group "
          f"at any line, the run's arrays are profile_arrays' with no group, and a group of it alone (one member, no "
          f"split) gives the same arrays and OutcomeForm nets exactly",
          sing is not None and not any(k == sing['key'] for k, _ in LK['looks'])
          and all(np.array_equal(s_run[m], res.A[m][res.index[sing['key']]], equal_nan=True) for m in ARRAYS)
          and all(np.array_equal(s_old[m], s_one[m], equal_nan=True) for m in ARRAYS)
          and np.array_equal(OutcomeForm(cx, sx['geo'], sx['roll'], lone).net(sx['value'], sx['geo']),
                             OutcomeForm(cx, sx['geo'], sx['roll']).net(sx['value'], sx['geo'])))
    # The kit's rule on the same casts (`spell_lookalikes` / `release_look`, `net_parts` with its
    # `look`) against attack-similarity's group in its outcome form, every 8th situation.
    pool0 = look_pool(c, spell_ids)
    kstore = LookStore()
    csid = SM.by_name['glintstone cometshard']
    cs_recs = [x for x in spell_job(c, csid) if x['status'] in ('scored', 'partial')]
    kstore.add(line0, cs_recs)
    kit = S.spell_lookalikes(SM, A, {x['key']: {
        'mid': csid, 'anim': x['anim'], 'hand': x['hand'], 'lead': x['lead'], 'weight': x['release_weight'],
        'attack': x['geo'], 'roll': x['roll'],
        'members': [{'mid': csid, 'anim': m['anim'], 'hand': m['hand'], 'lead': m['lead'], 'weight': m['weight'],
                     'attack': m['geo'], 'roll': m['roll']} for m in x['release_members']]}
        for x in cs_recs})
    djob = next((j for j in skill_jobs if j['name'] == 'Glintstone Dart'), None)
    d_recs = [x for x in (skill_job(c, djob) if djob else []) if x['status'] in ('scored', 'partial') and x.get('release')]
    kstore.add(line0, d_recs)
    dh = next((x for x in d_recs if x['release'] == 'full'), None)
    if dh is not None:
        d_attack = SM.skill_attack(dh['carrier'], c.stats, dh['affinity'], None, dh['id'], c.dfn, False, None,
                                   dh.get('stance'), priced_bullets=True, flown_only=True)
        kit[dh['key']] = SM.release_look(d_attack, dh['carrier'], c.stats, dh['affinity'], None, dh['id'], c.dfn,
                                         priced_bullets=True, flown_only=True)
    rows = {}
    for x in cs_recs + ([dh] if dh else []):
        sp = look_spec(c, kstore, pool0, line0, x['key'])
        ours = look_group(c, sp, kstore) if sp else None
        new = OutcomeForm(cx, x['geo'], x['roll'], ours.member(x['key'])).net(x['value'], x['geo']) if ours else None
        old = OutcomeForm(cx, x['geo'], x['roll']).net(x['value'], x['geo'])
        want = np.array([p['net'] for p in S.net_parts(A, x['geo'], x['value'], cx.pairs, x['roll'], c.strikes,
                                                       defender=c.dfn, look=kit.get(x['key']))])
        kg = kit[x['key']].group if kit.get(x['key']) is not None else None
        when = sorted({round(at, 2) for (kk, *_), at in (kg.chosen if kg else {}).items() if kk == kit[x['key']].key})
        what = x.get('variant') if x['type'] == 'spell' else 'hold'
        rows[what] = {'what': f"{x['name']} {what}",
                      'split': sp and sorted(round(float(v), 4) for v in sp['splits'].values() if math.isfinite(v)),
                      'kit_split': kg and sorted(round(float(kg.splits[i][j]), 4) for i in range(len(kg))
                                                 for j in range(i + 1, len(kg)) if math.isfinite(kg.splits[i][j])),
                      'weights': sp and sorted(round(v, 6) for v in sp['weights'].values()),
                      'kit_weights': kg and sorted(round(v, 6) for v in kg.weights),
                      'err': None if new is None else form_error(new, want),
                      'old': float(cx.w @ old / cx.W), 'new': None if new is None else float(cx.w @ new / cx.W),
                      'when': when, 'hand': x.get('hand')}
    dtp = next((x for x in d_recs if x['release'] == 'tap'), None)
    # The Dart's hold parts first from the release that shows first (`er-mechanics-ashes.skill_release_visible`).
    dshow = dtp and min([float(dtp['press']) + dtp['release_show']]
                        + [float(m['press']) + m['shows'] for m in dtp.get('release_members') or ()])
    check("32. Glintstone Cometshard's tap (its earliest release) and charged and the Glintstone Dart's hold: "
          "attack-similarity's group, release frames and all, in its outcome form, is the kit's on-screen read of the "
          "same cast (the kit's spell_lookalikes / release_look and net_parts with its look) within FORM_TOL in every "
          "situation read, the same splits and weights (every pair parts), and the tap's bolt is no longer rolled "
          "every time: " + '; '.join(
              f"{w['what']} {len(w['weights'] or ())} members, {len(w['split'] or ())} splits from "
              f"f{(w['split'] or [None])[0]} (kit {len(w['kit_split'] or ())} from f{(w['kit_split'] or [None])[0]}), "
              f"decided at {w['when'][:4]}, mean net "
              f"{w['old']:.1f} -> {w['new'] if w['new'] is None else round(w['new'], 1)}, error {w['err']}"
              for w in rows.values()),
          set(rows) == {'tap', 'charged', 'hold'} and all(w['err'] == 0.0 and w['split'] == w['kit_split']
                                                          and w['weights'] == w['kit_weights'] for w in rows.values())
          and all(len(w['split']) == len(w['weights']) * (len(w['weights']) - 1) // 2 for w in rows.values())
          and len(rows['tap']['weights']) > 4 and len(rows['hold']['weights']) > 4
          and rows['tap']['old'] < 1.0 and rows['tap']['new'] > 1.0
          and dtp is not None and rows['hold']['split'][0] == round(dshow, 4) and dtp['release_show'] > 0)
    # The group is in the fingerprint: one key, two builds on the distance basis's geometry, one read
    # with its group and one alone, get two forms; only the first is checked against the profile.
    zr_ = next(x for x in cs_recs if x['variant'] == 'tap')
    zk, zx = zr_['key'], profile_inputs(zr_)
    zsp = look_spec(c, kstore, pool0, line0, zk)
    zsub = {**zsp, 'keys': sorted([zk, dh['key']]) if dh else [zk], 'splits': {}}
    sig_ = look_sig(zsp, kstore)
    _SIM.update(ctx=c, inputs={zk: zx}, builds={zk: {0: zx, 1: zx}}, objective={},
                looks={(zk, None): zsp, (zk, 0): zsp}, look_store=kstore)
    _, zarr, znets, zerrs = _profile_work(zk)
    _SIM.clear()
    # The build read alone: with no group there are no strata, so every release frame, their
    # weighted mean.
    zunits = [zx] + list(zx.get('members') or ())
    zold = mix_arrays([profile_arrays(c, u) for u in zunits], [float(u.get('weight', 1.0)) for u in zunits])
    check(f"32. the group is in the fingerprint: the Cometshard tap's ({sig_}) is not a group of other members "
          f"({look_sig(zsub, kstore)}) and neither fingerprints as the attack alone; of two builds on its geometry, the "
          f"one read with its group shares the profile's form and is checked against it ({zerrs}), the one alone gets "
          f"its own form and the old read (mean net {c.w @ znets[1][0].astype(float) / c.W:.1f} against "
          f"{c.w @ znets[0][0].astype(float) / c.W:.1f}, the profile {c.w @ zarr['net'].astype(float) / c.W:.1f})",
          len({geo_fingerprint(zx['geo'], zx['roll']), geo_fingerprint(zx['geo'], zx['roll'], look=sig_),
               geo_fingerprint(zx['geo'], zx['roll'], look=look_sig(zsub, kstore))}) == 3
          and sig_ == look_sig(dict(zsp), kstore) and 1 <= len(zerrs) <= len(zunits) and set(zerrs) == {0.0}
          and len(zunits) == len(SM.releases(csid, zr_['anim']))
          and form_error(znets[0][0], zarr['net']) == 0.0 and form_error(znets[1][0], zold['net']) == 0.0
          and not np.array_equal(znets[0][0], znets[1][0]))
    # 18. The matrices, as computed (before `_sym` averages, zeroes and clips them).
    # The profiles are float32, so a share can sit a float32 step above 1 (a base share of
    # 21.329569 / 21.3295685): the range is checked to 1e-6, symmetry and the diagonal exactly.
    raw = raw_matrices(res)
    facts = {nm: (float(np.abs(M - M.T).max()), float(np.abs(np.diag(M)).max()), float(M.min()), float(M.max()))
             for nm, M in raw.items()}
    bad = [nm for nm, (asym, diag, lo, hi) in facts.items()
           if not (asym <= 1e-12 and diag <= 1e-12 and lo >= -1e-12 and hi <= 1.0 + 1e-6)]
    check('18. raw D_rel, D_land, D_now, D_best and D_idle are symmetric, zero on the diagonal, within [0, 1]: '
          + '; '.join(f'{nm} asym {f[0]:.1e} diag {f[1]:.1e} range {f[2]:.4f}..{f[3]:.7f}' for nm, f in facts.items()),
          not bad)
    check('18. and the stored float32 matrices are those, rounded',
          all(np.allclose(an['matrices'][nm], raw[nm], atol=1e-6) for nm in raw))
    check('18. the stored D_assoc is max(D_rel, D_land, idle rate x D_idle) of the stored three, symmetric, zero on '
          'the diagonal', np.allclose(Mx['D_assoc'], np.maximum(np.maximum(Mx['D_rel'], Mx['D_land']),
                                                                 np.float32(IDLE_RATE) * Mx['D_idle']), atol=1e-7)
          and np.array_equal(Mx['D_assoc'], Mx['D_assoc'].T) and not np.diag(Mx['D_assoc']).any())
    # 19. First touch.
    dist = np.array([d for d, *_ in c.pairs])
    tf_t, tf_c = res.A['t_first'][i_t], res.A['t_first'][i_c]
    check('19. Zamor tap: no first touch at any distance from 4.5 m', np.isinf(tf_t[dist >= 4.5]).all())
    check('19. Zamor charged: a first touch at some distance in (4.5, 6.0] m',
          np.isfinite(tf_c[(dist > 4.5) & (dist <= 6.0)]).any())
    # The cache: round trip, the miss reason, a truncated file read as a miss.
    import tempfile
    EX.CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=EX.CACHE) as tmp:
        ins = {'version': SIMILARITY_VERSION, 'code': {'a.py': '1', 'b.py': '2'}}
        save_cache(res, 'selftest', 150, {'_matrices': an['matrices']}, root=tmp, inputs=ins)
        back = load_cache(150, 'selftest', c.F, root=tmp)
        an2 = analyze(back, a) if back is not None else None
        z = np.load(cache_paths(150, 'selftest', tmp)[1])
        check('20. the cache gives back the same keys, arrays, twins, build nets and per-build edges', back is not None
              and back.keys == res.keys and back.comp_keys == res.comp_keys
              and all(np.array_equal(back.A[m], res.A[m], equal_nan=True) for m in ARRAYS)
              and an2['twins'] == an['twins']
              and [b['name'] for b in an2['builds']] == [b['name'] for b in an['builds']]
              and all(b2['dominance']['edges'] == b1['dominance']['edges'] for b1, b2 in zip(an['builds'], an2['builds']))
              and all(set(b2['nets']) == set(b1['nets']) and all(np.array_equal(b2['nets'][k][j], b1['nets'][k][j])
                                                              for k in b1['nets'] for j in (0, 1))
                      for b1, b2 in zip(res.builds, back.builds))
              and np.array_equal(z['D_rel'], an['matrices']['D_rel'])
              and len(z['idx_spell']) + len(z['idx_skill']) == len(res.keys)
              and all(back.records[k].get('objective') == res.records[k].get('objective') for k in res.keys))
        why = miss_reason(150, 'other', {**ins, 'code': {'a.py': '1', 'b.py': '3'}}, root=tmp)
        check(f'20. a miss names what moved ({why})', 'code: b.py' in why)
        jp, npz = cache_paths(150, 'selftest', tmp)
        npz.write_bytes(npz.read_bytes()[:200])
        check('20. a truncated .npz with a matching JSON is a miss, not a crash', load_cache(150, 'selftest', c.F, root=tmp) is None)
        check('20. no temporary file is left behind', not [p for p in Path(tmp).iterdir() if '.tmp' in p.name])
    doc = json.loads(json.dumps(json_doc(res, an, a), default=_json_default))
    check('21. the association JSON holds no vector and no matrix',
          'matrices' not in doc and not any(m in r for r in doc['entities'].values() for m in ARRAYS))
    page = render(res, an, a)
    check('22. the page has its 15 sections and a heatmap', all(f'<h2>{i}.' in page or f'<h2 id=objective>{i}.' in page
                                                                 for i in range(1, 16))
          and 'data:image/png;base64,' in page)
    buf = io.StringIO()
    print_summary(res, an, buf)
    # The attacks whose rows are shown: a contains row's skill and a cross-type match's spell of this
    # subset (Zamor Ice Storm and Glintblade Phalanx until 2026-11-10, when the placed launches and the
    # caught defender moved those two pairs apart in this subset, bd er-effects-rs-doax / n7dd).
    cname = R[an['contains'][0]['x']]['name'] if an['contains'] else 'Zamor Ice Storm'
    mname = R[an['matches'][0]['spell']]['name'] if an['matches'] else 'Glintblade Phalanx'
    rc = print_show(res, an, cname, buf)
    shown = buf.getvalue()
    buf2 = io.StringIO()
    rc2 = print_show(res, an, mname, buf2)
    buf3 = io.StringIO()
    rc3 = print_show(res, an, 'Glintblade Phalanx', buf3)
    # This subset has had no contains since 2026-11-10 (the Zamor storm component no longer contains the
    # tap, check 9), so the contains row is checked only when one exists. Since 2026-11-13 it has no
    # cross-type match either (the Glintblade Phalanx spell, read with its lookalike group, lands under
    # --live, check 10), so the match row is checked the same way.
    parts23 = {'every show returns 0': rc == 0 and rc2 == 0 and rc3 == 0,
               'summary lists cross-type matches': 'cross-type matches' in shown, 'D_assoc neighbours': 'D_assoc' in shown,
               'contains row': ' contains ' in shown or not an['contains'],
               'cross-type match row': 'cross-type match:' in buf2.getvalue() or not an['matches'],
               'probe rows': 'probe (' in buf3.getvalue(), 'per-build rows': '  build ' in buf3.getvalue(),
               'build-dependent heading': 'build-dependent, per attacker build' in shown,
               'objective summary': 'objective evaluation' in shown,
               'objective rows': all(f'objective, {nm} line: ' in buf3.getvalue() for nm in OBJECTIVE_LINES),
               'every build in the summary': all(f"build {build_title(b)}" in shown for b in an['builds']),
               'every build on the page': 'Build-dependent' in page and all(
                   esc_ in page for esc_ in (html.escape(build_title(b)) for b in an['builds']))}
    check(f'23. the summary and --show print; --show gives D_assoc neighbours, the contains row ({cname}, '
          f'{len(an["contains"])} contains in the subset), the '
          f'cross-type match ({mname}), the probe rows and the per-build dominance (Glintblade Phalanx); the summary '
          f'names what is build-dependent and gives every build (missing: {[k for k, v in parts23.items() if not v]})',
          all(parts23.values()))
    selftest_lines_run(check, c, jobs)
    tm.lap('later checks')
    tm.facts.update(scope='selftest', only=sorted({T._norm(x) for x in SELFTEST_NAMES}), jobs=jobs, cache='computed',
                    version=SIMILARITY_VERSION, argv=['--selftest'] + ['--keep-going'] * keep_going)
    if failed:
        tm.facts['failed'] = failed
    tm.record(timing_log)
    head = (f'selftest FAILED {len(failed)} of {n[0]} checks ({"; ".join(f[:60] for f in failed)})' if failed
            else f'selftest ok ({n[0]} checks)')
    print(f'{head}; {tm.line()}; logged in {timing_log or TIMING_LOG}')
    return 1 if failed else 0


def _toy(specs, w):
    """A `Result` of synthetic scored entities for the dominance and cache checks: {key: {'net',
    'net_one' (default 'net'), 'fp' (10), 'slots' (1), 'type' ('skill'), 'lower' (False), 'parent'
    (the key), 'live_frames' (0), 'channel' (False)}}."""
    recs, arrays = {}, {}
    for k, s in specs.items():
        net = np.asarray(s['net'], np.float32)
        z = np.zeros_like(net)
        recs[k] = {'key': k, 'name': k, 'type': s.get('type', 'skill'), 'parent': s.get('parent', k),
                   'fp': s.get('fp', 10), 'slots': s.get('slots', 1), 'fp_lower_bound': s.get('lower', False),
                   'tokens': [], 'flags': [], 'carrier': 'X', 'affinity': None, 'status': 'scored', 'full': 1.0,
                   'live_frames': float(s.get('live_frames', 0.0)), 'channel': bool(s.get('channel', False)),
                   'value': {0.0: 1.0}}
        arrays[k] = {'land_now': z, 'pun_now': z, 'base_now': z, 't_first': z, 'land': z, 'pun': z, 'net': net,
                     'net_one': np.asarray(s.get('net_one', net), np.float32), 'gap': z}
    return Result({}, recs, [], {}, arrays, {}, w, 10.5)


if __name__ == '__main__':
    sys.exit(main())
