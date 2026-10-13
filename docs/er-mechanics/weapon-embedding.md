# Weapon embedding

Vectors of measured weapon mechanics, used to pick weapons within a class for a build's inventory
(first consumer: the PvP inventory on planner build `?b=188e74da444029`, STR 18 / DEX 80, RL150).

## Glossary

| Term | Meaning |
|---|---|
| Weapon vector | One vector per (weapon, affinity, stat block): the weapon's measured mechanics as they play out for that infusion on that character. |
| Stat block | The eight attributes the vector is evaluated at, e.g. STR 18 / DEX 80. Two-handing's STR x1.5 is applied per grip, not per block. |
| Grip | Right hand one-handed, two-handed, or left hand (off-hand L1). Part of the vector key. |
| Affinity | The infusion (Standard, Keen, Cold, Blood, ...). A unique weapon has only its fixed one. |

## Decisions

1. **The vector is keyed by weapon, affinity and stat block** (user, 2026-10-08: "vectors should be
   calculated per weapon for stats/infusions"). Keen Shamshir and Cold Shamshir on STR 18 / DEX 80
   are two vectors; Shamshir on a different stat block is a third.
2. **Grip and hand are in the key too** (user, 2026-10-08: "Moveset changes by grip and
   handedness"). Grips: right hand one-handed (R1/R2 set), two-handed, and left hand (the off-hand
   L1 chain, which Shamshir's own animation file overrides as well). Measured example, Shamshir
   one-handed vs two-handed: R1 reach 3.62 / 3.58 m, stamina per R1 10 / 13, chain damage per second
   197.9 / 204.3; two-handing also multiplies STR by 1.5 for damage and requirements.
3. **A powerstance is its own vector**, keyed by both weapons (with affinities) and the stat block
   (user, 2026-10-08: "powerstanced is unique moveset too"). Which pairs exist is read from
   animation availability -- the pair has a vector when its powerstance animations exist -- not from
   a same-class rule. Shamshir's own animation file does not cover powerstance, so two Shamshirs
   powerstanced use the standard curved sword set and must not be scored as Shamshir's R1 chain.
4. **Moveset features are kept per attack slot, with summaries derived from them** (user,
   2026-10-08). Slots are a fixed list -- R1 #1..n, R2 #1..n, charged R2, running R1/R2, rolling R1,
   backstep R1, jump R1/R2, crouch R1, guard counter, off-hand L1, powerstance L1 -- each with startup, active frames,
   roll-cancel frame, reach, stamina, motion value and poise damage, plus two feint features: the
   hold range (earliest and latest release frame, for charged R2 and held skills such as Unsheathe)
   and the feint window (frames before the first hit where a TAE cancel into roll, guard or another
   attack is open, i.e. the attack can be started and abandoned before it can hit). A slot the weapon lacks is
   absent, not zero. Summaries (R1 chain recovery, R1 #1 reach, chain damage per second, stamina
   per R1) are computed from the slots; ranking uses the summaries, similarity uses the slots.
5. **The ash of war is a separate vector** (decided by the agent; the user deferred, 2026-10-08).
   Ash vectors are keyed (ash, weapon, affinity, grip, stat block), since an ash's damage, reach
   and timing depend on the weapon carrying it. An inventory row is a weapon vector plus an ash
   vector, so one weapon with two ashes is two rows over one weapon vector. A fixed-skill unique's
   skill is its only ash vector. Folding the ash into the weapon key would multiply vectors by up
   to 59 per infusable weapon and let the ash dominate weapon-to-weapon similarity.
6. **Damage features use the fixed PvP defender**: 60 VIG at RL150 with the median RL150
   resistances, as already used for status valuation. Per hit: AR by damage type, damage dealt to
   that defender, status build-up, and poise damage.
7. **Near-twins may both be held** (user, 2026-10-08). Two weapons with near-identical vectors
   (Shamshir and Scimitar; Halberd and Banished Knight's Halberd) each keep whatever roles they win
   on their own; similarity does not remove either from the inventory.
8. **"Fun" means a mechanic few other options have** (user, 2026-10-08: "only unique things like
   deadly poison on claws, unique movesets enabled by ashes of war, physical damage scaling with
   sacred(holy)"). It is a rarity measure over the weapon and ash vectors, not distance from the
   class's standard moveset. Examples of what counts: a rare status type (Deadly Poison on
   Venomous Fang), a moveset an ash adds that nothing else provides, and an unusual scaling route
   (Sacred affinity scaling physical damage with Faith). A weapon that only differs in timing or
   reach is not fun by this rule.
9. **Rarity is measured over the whole game** (user, 2026-10-08): a mechanic is rare when few
   weapon, ash and affinity options anywhere have it. A class may have no fun pick or several.
10. **A fun pick only has to be wieldable** at the build's stats, two-handing's STR x1.5 included
    (user, 2026-10-08). The rare mechanic may be weak there (Sacred scaling at low Faith still
    counts).

## Implementation

`scripts/er-mechanics-weapon-embed.py` writes one JSON record per key, built from the existing
measurement scripts rather than new ones: `er-mechanics-moveset-sweep.py` (per-slot frames,
motion values, stamina), `er-mechanics-weapon-recovery.py` (roll-cancel frames),
`er-mechanics-reach.py` (reach), `er-mechanics-ar` / `er-mechanics-defense` (damage against the
fixed defender), `er-mechanics-ash-table.py` (ash vectors). `er-mechanics-class-roles.py` and
`er-mechanics-combo-loadout.py` read the records instead of recomputing features.

Fun is counted over (base weapon, affinity) options: a mechanic is fun when at most 5% of the
2346 options have it (`FUN_MAX_SHARE`). Measured 2026-10-08: physical-FTH 3.6% and physical-INT
2.9% pass, each elemental infusion's own route (7-17%) and physical-ARC (23%) do not; Venomous
Fang's Deadly Poison (16.7 HP/s for 30 s) is 0.6%, against the common 8.3 HP/s for 90 s. Steps and
buffs are not movesets, so only skills with a damaging hit count.

Decision 8's Sacred example does not hold in the regulation: the Sacred and Flame Art affinities
(AttackElementCorrect 10000, checked on Longsword) scale physical with STR/DEX and only their holy
or fire part with FTH. Physical-FTH is on the 85 options whose own row flags it (Erdsteel Dagger,
the Smithscript weapons, Horned Warrior's swords, ...), and none of them is wieldable at FTH 8, so
the STR 18 / DEX 80 build has no physical-FTH fun pick.
