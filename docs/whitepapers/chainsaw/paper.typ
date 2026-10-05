// The chainsaw glitch: a held skill keeps looping on a weapon equipped mid-skill.
// Build: python3 scripts/build-whitepaper.py chainsaw (writes target/whitepapers/chainsaw.pdf).

#let title = "A Skill That Outlives Its Weapon"
#let subtitle = "The chainsaw glitch in ELDEN RING 1.17.1: a one-frame equip window, a fallback clip, and a two-check fix"
#let repo = "https://github.com/Banon-Labs/er-mods-rs"
#let branch = repo + "/blob/feat/er-r3-view-rest-of-catalog/"

#set document(title: title, author: "Banon Labs")
#set page(
  paper: "us-letter",
  margin: (x: 1in, top: 1in, bottom: 1in),
  header: context {
    if counter(page).get().first() > 1 [
      #set text(8pt, fill: luma(110))
      #title #h(1fr) ELDEN RING 1.17.1
      #v(-6pt)
      #line(length: 100%, stroke: 0.4pt + luma(170))
    ]
  },
  footer: context [
    #set text(8pt, fill: luma(110))
    #h(1fr) #counter(page).display("1 of 1", both: true) #h(1fr)
  ],
)
#set text(font: "Libertinus Serif", size: 10.5pt, lang: "en")
#set par(justify: true, leading: 0.62em, spacing: 0.95em)
#set heading(numbering: "1.1")
#show heading.where(level: 1): it => {
  v(1.1em, weak: true)
  set text(13pt, weight: "bold")
  it
  v(0.5em, weak: true)
}
#show heading.where(level: 2): it => {
  v(0.9em, weak: true)
  set text(11pt, weight: "bold")
  it
  v(0.4em, weak: true)
}
#show raw: set text(font: "DejaVu Sans Mono", size: 0.86em)
#show link: it => text(fill: rgb("#1a4d8f"), it)
#show figure.where(kind: table): set figure.caption(position: top)
#show figure.caption: set text(9pt)
#show figure: set block(breakable: true)
#set figure(gap: 0.6em)

// A ruled table: thick rules above and below, a thin rule under the header, no grid.
#let ruled(columns: (), header: (), size: 8.5pt, ..cells) = {
  set text(size: size)
  set par(justify: false, leading: 0.5em)
  table(
    columns: columns,
    stroke: none,
    align: left + top,
    inset: (x: 4pt, y: 3.2pt),
    table.hline(stroke: 0.9pt),
    table.header(..header.map(h => text(weight: "bold", h))),
    table.hline(stroke: 0.5pt),
    ..cells,
    table.hline(stroke: 0.9pt),
  )
}

#let label-box(body) = block(
  width: 100%,
  inset: (x: 10pt, y: 8pt),
  stroke: (left: 2pt + luma(150)),
  fill: luma(246),
  body,
)

// Title block.
#align(center)[
  #v(0.4in)
  #text(19pt, weight: "bold", title)
  #v(4pt)
  #text(11.5pt, style: "italic", subtitle)
  #v(10pt)
  #text(10pt)[Banon Labs · #link(repo)[er-mods-rs] · 5 October 2026]
  #v(14pt)
]

#label-box[
  #text(weight: "bold")[Abstract.]
  In the chainsaw glitch a player starts a skill that attacks for as long as L2 is held, equips a
  different right-hand weapon while it runs, and the new weapon keeps attacking with a borrowed
  loop. The game never records which skill is running on the character; the only record is the
  clip a behavior-graph selector picked when the skill started, and an equip never touches the
  graph. We trace the equip gate to a one-frame window, show that the loop which plays afterwards
  is a fallback clip (Spinning Wheel's) rather than the source skill's, and that its hits resolve
  through the held weapon's own skill rows. A self-driving Frida driver reproduced the glitch from
  all five held-attack skills. The loop does not push a victim harder than an ordinary hit. We
  propose a fix of two checks in the selector; it has not been tested.
]

#v(4pt)
#text(9pt)[
  *Conventions.* Executable addresses are for the installed 1.17.1 build (PE 2.7.1.0); 1.16.2
  equivalents are in @tbl-exe. Labels: *measured* = measured in game with a Frida hook on the
  running game (run named where it matters); *code* = read in the 1.17.1 executable; *data* = read
  in the 1.17.1 regulation or the player's behavior files (the HKS script `c0000.hks`, the behavior
  graph `c0000.behbnd`, the TimeAct `a<category>.tae`); *inferred* = follows from those pieces but
  was not traced end to end.
]

= The glitch in brief

The player holds a right-hand skill whose loop attacks while L2 is held, for example Spinning
Wheel on Ghiza's Wheel. During a weapon switch they equip a different right-hand weapon, for
example the Starscourge Greatsword, and keep L2 held. The HUD shows the new weapon's skill
(Starcaller Cry), but the character goes on spinning with the new weapon's model and keeps paying
FP for it.

Three things have to be true, and each is a separate defect or gap:

- *The equip is accepted mid-skill.* The equipment gate is shut on every frame a skill state runs,
  but it is open for one frame at the start of the skill if that start lands inside a weapon
  switch (@sec-gate).
- *The loop survives the equip.* Nothing on the equip path checks the running skill. The loop
  selector keeps its clip, and if it reselects it falls back to child 0 (@sec-loop).
- *The loop deals damage.* Its hit judges resolve through whatever weapon is now held, so they
  become that weapon's own skill rows (@sec-hits).

= Background: where the running skill lives <sec-background>

A skill's animation is chosen by a `CustomManualSelectorGenerator` (CMSG) node of the behavior
graph whose `offsetType` is 0x12 (*code*). The node asks the resolver 0x14041b0d0 for an offset,
which reads the live hand's `swordArtsTypeNew` and returns `(600 + swordArtsTypeNew) × 1,000,000`;
the skill TimeAct is therefore `a<600 + swordArtsTypeNew>` (*code*, *data*). The node's name lookup
0x1419bb2f0 then picks a child by name: first `a<category>_<anim>`, then `a000_<anim>`, and if
neither exists, child 0 (*code*). The chosen index is stored at node +0x100 by the activate
0x1419b9980, and the TimeAct id of the chosen clip at node +0xec (`argTaeId`) by 0x1419bb530
(*code*). `argTaeId` drives the TimeAct callbacks, so hit boxes and FP charges come from the clip
the node picked (*code*).

The character itself caches no skill. `CSChrSwordArtsModule` (component container +0x110, 0x20
bytes) holds only FP bookkeeping: the reserved cost at +0x10, a no-FP flag at +0x14 and a HUD
state at +0x18 (*code*). Every other lookup re-reads the weapon in the hand: the FP reservation,
`CanCastAow`, and the HKS global `c_SwordArtsID`, which `GetConstVariable` refreshes every frame
through `env(326)` (*code*, *data*). So after an equip the HUD, FP cost and script all move to the
new weapon's skill, while the selector node keeps whatever clip it chose.

= The equip gate and the one-frame window <sec-gate>

The equipment menu refuses with "Cannot change equipment at this time" (`GR_Dialogues` 103130)
when `CanChangeEquipmentInSlot` 0x140789910 returns false (*code*, *data*). For weapon slots it
reduces to a predicate over the flag word at `CSPlayerMenuCtrl` +0x20 (0x1407c1fb0, *code*):

#block(inset: (x: 8pt, y: 6pt), fill: luma(246), width: 100%, radius: 2pt)[
```c
allowed = (f & 0x1) && !(f & 0x10) &&
          ((ChrActionFlagModule.actionAnimationFlags & 1) || (f & 0x6));
```
]

The word is rebuilt every frame (@tbl-gate). The same gate runs again inside the commit
`EquipItemToChrAsmSlot` 0x140788ab0, which returns silently when it fails (*code*).

#figure(
  caption: [Writers of the gate's inputs (*code*; HKS callers *data*).],
  ruled(
    columns: (auto, 1.4fr, 2fr),
    header: ([Bit], [Writer], [Called from]),
    [`= 9`], [per-frame chr update 0x140401a30], [every frame: sets bit 0, clears the rest],
    [`0x10` (forbid)], [HKS `act(163)`], [`ArtsCommonFunction`, run by every skill state's `onUpdate`, every frame],
    [`0x4` (allow)], [HKS `act(147)`], [fall, land, ladder and event states],
    [`0x2` (allow)], [TimeAct `ChrActionFlag` type 11, handler 0x140427b30], [any clip carrying the movement-cancel window],
    [anim flag bit 0], [HKS `act(9100)` (`Wait`); cleared each frame in `PreBehaviorSafe` 0x140401f30], [idle, stop, move, guard states],
  ),
) <tbl-gate>

No skill sets a persistent lock; it shuts the gate only because its state's `onUpdate` calls
`act(163)` on each frame (*code*, *data*). A weapon switch (0x14042d4d0) writes nothing the gate
reads (*code*); what matters is that its clip carries a type-11 window.

The self-driving driver measured where the window is (@tbl-pivot). Its sequence, the pivot: lock
on, tap d-pad left (a soft swap of the off-hand between two items that defer L2 to the right
hand), press L2 a set number of frames later, and commit the right-hand equip from the frontend
update `CSFeManImp::Update` 0x140772a50 on the frame the selector picks the source's start clip.
The driver calls the game's own commit, which runs the same gate the menu does; it does not walk
the menu in this mode.

#figure(
  caption: [The window, Spinning Wheel on Ghiza's Wheel → Starscourge Greatsword (*measured*, two runs, same outcome).],
  ruled(
    columns: (auto, 2.6fr, auto),
    header: ([Frame], [Event], [Gate flags]),
    [189], [selector writer 0x1419bb530 stores `argTaeId` 839040050 (Spinning Wheel start)], [11, open],
    [189], [`CSFeManImp::Update` commits Starscourge into the right slot; the commit's gate passes], [11, open],
    [190 on], [main-player tick: the skill state's `act(163)` sets 0x10], [25, shut],
    [287], [loop selector stores 839040051 with Starscourge held], [25],
  ),
) <tbl-pivot>

What the runs settle (*measured*):

- The window is one frame: from the selector choosing the source start clip to the next
  main-player tick. A driver that reads the gate on the main-player tick never sees it open with
  the source clip (6 of 6 attempts, L2 delays 10 to 20 frames).
- The open bit is 0x2, from the swap clip's type-11 window.
- Only a fresh L2 press starts a skill. Pressed 10 frames after the swap tap, the press dies in the
  swap clip; at 12 frames the glitch reproduces. Holding L2 into idle never starts a skill.
- A commit on the frame of L2 down, before the selection, plays the target's own skill (Starcaller
  Cry, 832040000): the equip only changed which skill L2 starts.

How a human hits this window through the pause menu was not traced; the driver commits from the
same frontend update the menu's commit runs in (*inferred* that the two paths behave alike).

= Why the loop survives, and which loop plays <sec-loop>

The menu equip writes only game data (`EquipItemToChrAsmSlot`, then a network broadcast). The
character picks the change up in its pre-update 0x140660f70, which marks a poise update, calls
`ResetInputQueue`, copies the ChrAsm and strips the changed hand's weapon SpEffects (*code*).
Nothing there fires a behavior event, compares anything with `swordArtsTypeNew`, or touches the
selector.

The stance loop selectors never reselect after activation: `CustomManualSelectorGenerator::update`
0x1419ba150 re-runs the choice only when `changeTypeOfSelectedIndexAfterActivate` (+0xb6) is not 0,
and on the stance loops it is 0, except `DrawStanceRightLoop_CMSG00`, where it is 1 (*code*,
*data*). When the loop node is entered after the equip, the lookup tries `a832_040051` (Starcaller
Cry has no 040051), then `a000_040051`, then takes child 0 (*code*, *data*). On
`DrawStanceRightLoop_CMSG` child 0 is `a839_040051`, Spinning Wheel's loop (*data*).

This was measured from every source (@tbl-sources): each source's own start clip is selected
(610, 611, 625, 839 or 909 × 1,000,000 + 040050), and the loop that then plays is 839040051 in every
case (*measured*). So the glitch does not carry the source skill's loop. It starts the source
skill, and the loop node falls back to Spinning Wheel's clip because the held weapon has none. A
one-shot source (Bloodhound's Step, `a756`) carries nothing: the commit lands and nothing loops
(*measured*).

The loop ends when L2 is released, or for `c_SwordArtsID` 25 and 239 when FP runs out (*data*,
`DrawStanceRightLoop_Upper_onUpdate`). Its only weapon checks divert bows and crossbows to their
own stance code (*data*). No line compares the running clip with `c_SwordArtsID` (*data*).

= What the loop hits with <sec-hits>

A TimeAct attack event resolves its judge through `PlayerIns::ResolveBehaviorId` 0x1406530d0 with
the `behaviorVariationId` of the weapon in the slot when the event fires (*code*). The attack info
is built fresh per hit window from the held weapon (0x140690df0: weapon id, two-handing, AtkParam),
and `CalculateDamage2` 0x140448910 computes attack power from that weapon (*code*). Nothing on the
damage path reads the skill that was active when L2 was pressed (*code*).

Spinning Wheel's loop has judges 3900 and 3902 (*data*). Through Starscourge's
`behaviorVariationId` 405 they resolve to AtkParam_Pc 300405900 to 300405902, the rows named
"Starscourge Greatsword [AOW] Starcaller Cry" (*data*). So the chainsaw hits with the held weapon's
own skill rows, scaled by its own attack rating. Live hits used 300405900, 300405901 and 300405903
(*measured*); one humanoid run recorded 670 damage per hit with 300405903 (*measured*, run
`drive-watch40`). A weapon whose variation has no row for these judges creates no hit: Miséricorde
looped 196 frames and hit nothing at 4.66 m (*measured*), and it is not among the carriers below
(*data*); its zero was unresolved rows, not reach.

#figure(
  caption: [Weapons that turn the fallback loop into damage, top rows by `dmgLevel` of the resolved AtkParam (*data*; 53 weapons in all).],
  ruled(
    columns: (1.7fr, auto, 1.4fr, auto, auto),
    header: ([Held weapon], [Variation], [Resolved AtkParam_Pc], [dmgLevel], [Phys. MV]),
    [Dragon King's Cragblade 6040000], [601], [303401200, 303401202 (Thundercloud Form)], [7], [256],
    [Rotten Staff 23140000], [2307], [302307900, 302307902 (Erdtree Slam)], [7], [0],
    [Staff of the Avatar 23070000], [2307], [302307900, 302307902 (Erdtree Slam)], [7], [0],
    [Cipher Pata 21130000], [2113], [302113900 (Unblockable Blade)], [3], [297],
    [Godslayer's Greatsword 4070000], [404], [300800300 (Queen's Black Flame)], [3], [220],
    [Starscourge Greatsword 4050000], [405], [300405900, 300405902 (Starcaller Cry)], [3], [60],
    [Greatsword of Radahn, Light and Lord], [405], [same as Starscourge], [3], [60],
  ),
) <tbl-carriers>

The full list is printed by `er-mechanics-chainsaw-class.py --loop-carriers`. Ghiza's Wheel is in
it with its own loop, which is the normal skill.

*FP.* Each charged swing spends a reservation that `UpdateActiveAowFpStats` 0x140480070 computes
fresh from the held weapon's skill row, cast number 3, `useMagicPoint_L2` (*code*). After the equip
that is Starcaller Cry's 20 instead of Spinning Wheel's 3 (*data*). The per-swing amount was not
isolated live; one run drained FP 75 → 0 over 192 loop frames with Starscourge held (*measured*).

= The affected class

A source must be a skill whose `swordArtsTypeNew` is in the HKS predicate `IsAttackStanceArts` (10,
11, 25, 239, 309, 340, 341) and whose 040051 loop carries a hitbox (*data*). Ids 340 and 341 have no
`SwordArtsParam` row in 1.17.1, so five skills remain (@tbl-sources). Other stance skills (Square
Off, Unsheathe, the bow skills, Muleta) share the states but their loop holds a pose and hits
nothing (*data*).

#figure(
  caption: [Source skills (*data*) and the reproduction onto Starscourge Greatsword (*measured*, target NpcParam 46000014 at 4.66 m).],
  ruled(
    columns: (1.3fr, auto, auto, 2fr, auto),
    header: ([Skill], [Row], [Type], [Carriers], [Hits]),
    [Wild Strikes], [110], [10], [innate on 7 axes (Ripple Blade locked) and Great Omenkiller Cleaver; ash 11000 mounts on 72], [33],
    [Spinning Strikes], [111], [11], [no innate; ash 11100 mounts on 24 spears, halberds, reapers], [in 3–33],
    [Spinning Chain], [125], [25], [Nightrider Flail, Flail, Chainlink Flail; no ash], [in 3–33],
    [Spinning Wheel], [1039], [239], [Ghiza's Wheel, locked], [17],
    [Unending Dance], [5090], [309], [Dancing Blade of Ranah, locked], [in 3–33],
  ),
) <tbl-sources>

The run notes give 3 to 33 hits across the five sources, 33 for Wild Strikes and 17 for Spinning
Wheel at this distance; the counts for the other three were not recorded separately.

A second prerequisite is the off-hand. One-handed, L2 fires the left weapon's skill unless that
row has `isRefRightArts` 1; 47 of 278 rows have it at 0, among them every shield skill (*data*,
*code* 0x14047fcd0). The driver used Frenzied Flame Seal and Watchdog's Staff, both of which defer.

Target weapons: the skill TimeAct is chosen by skill, not by the weapon's motion category, so any
weapon class plays the clip (*data*). The HKS loop update diverts only bows and crossbows (*data*).
Whether a held weapon deals damage depends on the rows its variation resolves (@tbl-carriers).

= Evidence

#figure(
  caption: [Runs of the self-driving driver on build `98f687a96d43b1` (*measured*, 5 October 2026).],
  ruled(
    columns: (1.9fr, 1.1fr, 2fr),
    header: ([Run], [Setup], [Result]),
    [Pivot, two runs], [Spinning Wheel → Starscourge, no enemy], [success on attempt 2 (L2 at 12 frames); 192 loop frames, FP 75 → 0; no hits, nothing in range],
    [`drive-watch16`], [same, with enemies kept nearby], [success on attempt 1; 16 hits with Starscourge held],
    [`drive-watch22`, `23`], [player and target pinned 4.66 m apart, same facing], [glitch 17 hits (300405900/901); control, Spinning Wheel on Ghiza's Wheel, 0 hits in 176 loop frames],
    [`drive-watch25`], [Spinning Wheel → Miséricorde], [196 loop frames, 0 hits],
    [`drive-watch26`], [Bloodhound's Step → Starscourge], [commit lands, nothing loops],
    [`drive-watch27` to `30`], [each of the five sources → Starscourge], [success on attempt 1 each; loop 839040051 every time; 3 to 33 hits],
    [`drive-watch36` to `38`], [Spinning Wheel → the three dmgLevel-7 weapons], [19, 18 and 5 hits (@sec-push)],
    [`drive-watch40`], [Spinning Wheel → Starscourge, humanoid NpcParam 30003014], [6 hits, AtkParam 300405903, 670 each (@sec-push)],
  ),
) <tbl-runs>

The reach pair shows what the glitch gains: the same skill, at the same distance and facing,
hits with Starscourge and misses with Ghiza's Wheel. The hit boxes follow the held weapon's model
(*inferred* from that pair; the hit-box geometry was not read). Unpinned runs mislead: the loop
turns the character by up to 26°, and a bystander 1.09 m away was never hit (*measured*).

*Test setup, not part of the glitch.* The driver blocks the player's pad, keyboard and mouse and
writes its own input into the player's action request module; it equips the loadout natively,
refills FP, sets every attribute to 99 for the dmgLevel-7 runs, keeps nearby characters at full
HP, and pins characters in place where a run calls for it. Enemies get a Darkness SpEffect so they
drop their target. Each step waits for a game event and fails with a named state, never on a timer.

= Knockback <sec-push>

A player observed that Starscourge's L2 pulls its target. We measured victim movement on
humanoids and on one large enemy, splitting each victim's move from one frame before a hit to 8
frames after into a component away from the player and a sideways one
(`er-mechanics-chainsaw-pull.py`). All of @tbl-push is *measured*, 5 October 2026.

#figure(
  caption: [Victim movement in the 8 frames after a hit.],
  ruled(
    columns: (1.7fr, 1.4fr, auto, 2fr),
    header: ([Case], [Attacks], [Hits], [Movement]),
    [Humanoids, no hit (baseline)], [none], [–], [0.00 to 0.07 m toward the player per 8 frames],
    [Humanoids, plain hits], [AtkParam 400000, 400010, 2300400], [13 on 3 enemies], [0.17 to 0.33 m away from the player (one 1.1 m); sideways 0.05 m or less],
    [Humanoid, chainsaw on Starscourge (`drive-watch40`)], [300405903, 670 damage], [6], [mean 0.19 m away; mean sideways −0.03 m],
    [Large enemy 46000014, chainsaw on Rotten Staff], [302307900–902], [19], [largest 0.64 m; 0.026 m/frame after hits vs 0.023 otherwise],
    [same, Staff of the Avatar], [302307900–902], [18], [largest 0.60 m; 0.032 vs 0.026 m/frame],
    [same, Dragon King's Cragblade], [303401200], [5], [largest 0.45 m; 0.033 vs 0.026 m/frame],
  ),
) <tbl-push>

The humanoids were brought in with the Piquebone Arrow's lure smoke: bullet 20003309, spawned at
the player's feet through `CSBulletManager::SpawnBullet` 0x1403a2cb0 (manager global 0x143d667a8).
Four humanoids walked in from 13 to 20 m to under 4.4 m (*measured*). The large enemy was pinned
4.66 m away until the equip landed, then freed, with the player held at a fixed offset from it.

The player reported the chainsaw target in `drive-watch40` dead after its sixth hit. The likely cause (*inferred*, HP was not logged): a single hit above the target's
maximum HP lands before the per-frame HP refill. The tools now also set the data module's no-death
bit, +0x19b bit 0, which the game tests at 0x1404379d0 (*code*).

So the chainsaw does not push harder than an ordinary hit: on humanoids it pushes straight away
from the player by about the same distance, and on the large enemy the dmgLevel-7 rows add a small
bump over its own walking. A player's observation that Starscourge's L2 pulls a target to the
side opposite the player's facing was not reproduced on humanoids. It has not been tested on large
enemies and remains open.

= Other animation fallbacks <sec-other>

A `CustomManualSelectorGenerator` builds the name `a<category>_<animId>` and plays the first child
whose node name contains it. With no match it tries `a000_<animId>`, and with no match there either
it plays child 0; there is no default field (*code*: 1.17.1 `0x1419bb2f0`, 1.16.2 `FUN_1419b9480`).
The TimeAct that fires, hitboxes and FP included, is child 0's; the held weapon only decides which
rows each hitbox resolves to. The stance loop on Starscourge is the only fallback seen in game
(`drive-watch27`). The rest of @tbl-fallbacks is read from `c0000.behbnd` and the TimeAct files
(*data*) and has not been driven.

#figure(
  caption: [Selectors whose child 0 is another skill's attack clip.],
  ruled(
    columns: (1.6fr, 1.6fr, 2fr),
    header: ([States], [Child 0 played on fallback], [Reached by]),
    [Stance start, loop, loop while moving], [Spinning Wheel `a839_040050/51/52` (13, 8, 10 hit events)], [any skill without its own clip, after a mid-skill equip (loop *measured*)],
    [Stance loop without sync, without FP], [Wild Strikes `a610_*`], [same],
    [Combo finishers], [Spinning Slash `a603_*`; second finisher Bloodboon Ritual `a834_040020` or Stormcaller `a623_042420`], [Stormcaller on a Twinblade swapped to Starscourge on its first clip, L2 again inside the 100052 window (live 2.2-2.75 s): `a603_042410` on two attempts (*measured*, `drive-watch45`; the window SpEffect survives the swap, `drive-watch44`)],
    [Charged skill released early], [Charge Forth `a605_040001`], [Glintstone Dart (Glintstone Kris) swapped to Meteoric Ore Blade on its first clip, L2 released: `a605_040001` played on two attempts, 4 hits with the held weapon's row 301701905 (*measured*, `drive-watch41`)],
    [Euporia Vortex / Causality's Wrath states], [`a928_040111` (38 hit events), `a928_040110` (14)], [Causality's Wrath follow-up after activation (*measured*); a plain weapon switch breaks it (*measured*)],
    [Spinning Chain with 0 < FP < cost], [Spinning Wheel `a839_040055` (start), Wild Strikes `a610_040056/57` (loop)], [does not happen: at FP 5 (cost above it) the Flail played its own `a625_040050/51/52/53`, the loop running on at FP 0 (*measured*)],
    [Thrusting-shield heavy specials], [axe `a030` or claw `a022` clips], [Dueling, Carian and Ritual Shields],
    [Scythe left heavy 5], [straight sword `a023_035040`], [scythes],
  ),
) <tbl-fallbacks>

Causality's Wrath (Golden Order Flail) reaches the `SwordArtsOneShot_111` selector the way its
caption says: activating the skill plays `a973_040000`, and the additional input plays
`a973_040111` (*measured*, `scripts/frida/skill-clip-trace.js`, 5 October 2026). Activating it and
then switching the right hand to another weapon by hand does not carry it over: the follow-up input
started the new weapon's own skill, `a663_040000`, twice. The skill id the behavior script reads
follows the held weapon, so an ordinary switch ends Causality's Wrath before the fallback can
happen. Reaching `a928_040111` on another weapon would need the equip to land inside the skill, the
way the stance loop is reached; that has not been tried.

The stance follow-up attacks (`040060/65/70`) have generic `a000_` children and borrow nothing.
Working notes:
#link(branch + "docs/er-mechanics/chainsaw/cmsg-fallbacks.md")[`cmsg-fallbacks.md`], produced by
#link(branch + "scripts/er-behbnd-cmsg-fallbacks.py")[`scripts/er-behbnd-cmsg-fallbacks.py`].

= Proposed fix

The check belongs in `CustomManualSelectorGenerator::update` 0x1419ba150, which runs every frame for
every sword-arts node and holds both halves of the comparison: the live skill from the resolver
and the running clip's `argTaeId`.

#block(inset: (x: 8pt, y: 6pt), fill: luma(246), width: 100%, radius: 2pt)[
```c
// in CustomManualSelectorGenerator::update, 0x1419ba150
if (node->offsetType == 0x12) {                       // sword arts
    int live = resolve_offset(ctx, 0x12);             // (swordArtsTypeNew + 600) * 1000000, 0x14041b0d0
    if (live / 1000000 != node->argTaeId / 1000000)   // +0xec: the running clip is another skill's
        end_node(node);                               // fire endEvent (+0xc8) as the anim-end path does
}
```
]

A second change is needed in the name lookup 0x1419bb2f0: for a sword-arts node it must not fall
back to child 0 when neither `a<600 + type>_<anim>` nor `a000_<anim>` exists. Without it, a re-entry
after the first check ends the node can still pick Spinning Wheel's loop, which is what plays in
every measured run.

#figure(
  caption: [What the two checks do (*inferred*; not prototyped).],
  ruled(
    columns: (2fr, 1.4fr, 1.2fr),
    header: ([Case], [Live type vs. clip], [Result]),
    [Source skill, its own weapon held], [839 = 839], [unchanged],
    [Mid-skill equip of a weapon with another skill], [832 vs. 839], [node ends],
    [Re-entry with a skill that has no 040051], [lookup finds nothing], [no clip; state ends],
    [Two-hand toggle or off-hand swap that keeps the right skill], [equal], [unchanged],
  ),
) <tbl-fix>

Alternatives:

- *Close the gate.* Making the commit refuse on the selection frame removes this route, but not
  others: any path that changes the right weapon while a skill node lives (a TimeAct switch, a
  future equip path) reopens it.
- *An HKS-only fix.* `c0000.hks` is data: end the state at the top of each loop `onUpdate` when
  `IsStanceArts(c_SwordArtsID)` is false. It works but needs one check per loop state.

Unlike the weapon-buff fix, this one has not been prototyped on the running game. A Frida
prototype would hook the update and end the node; that is the next step.

#pagebreak(weak: true)
= Sources

*Data.* The regulation is the installed 1.17.1 `regulation.bin`, read with
#link(repo + "/blob/main/scripts/er-param-read.py")[`scripts/er-param-read.py`], with field names
from the ER paramdefs shipped with #link("https://github.com/vawser/Smithbox")[Smithbox]. The
behavior files were extracted from the installed 1.17.1 game and checked against a pre-1.17 copy;
the rule and the five skills' TimeActs are unchanged
(#link(branch + "docs/er-mechanics/chainsaw/affected-class.md")[`affected-class.md`]).

#figure(
  caption: [Regulation and behavior rows cited.],
  ruled(
    columns: (auto, 1fr),
    header: ([Source], [Rows]),
    [SwordArtsParam], [110 Wild Strikes, 111 Spinning Strikes, 125 Spinning Chain, 1039 Spinning Wheel (L2 3), 5090 Unending Dance, 1032 Starcaller Cry (L2 20, R2 20, R1 −1)],
    [EquipParamWeapon], [23100000 Ghiza's Wheel, 4050000 Starscourge Greatsword (variation 405), 7520000 Dancing Blade of Ranah, 1030000 Miséricorde, the carriers in @tbl-carriers],
    [EquipParamGem], [11000 Ash of War: Wild Strikes, 11100 Ash of War: Spinning Strikes],
    [AtkParam_Pc], [300405900–903 Starcaller Cry; 302307900–902 Erdtree Slam; 303401200, 303401202 Thundercloud Form; 400000, 400010, 2300400 plain hits],
    [Bullet], [20003309 Piquebone lure smoke],
    [NpcParam], [46000014 (large enemy), 30003014 (humanoid)],
    [TimeAct], [`a610`, `a611`, `a625`, `a839`, `a909` (sources); `a832` (no 040051); `a756`],
    [Behavior graph], [`DrawStanceRightLoop_CMSG` (child 0 `a839_040051`), `SwordArtsStanceNoSyncLoop_CMSG` (child 0 `a610_040051`)],
    [HKS], [`IsAttackStanceArts`, `ExecArtsStance`, `DrawStanceRightLoop_Upper_onUpdate`, `ArtsCommonFunction`, `GetConstVariable`],
  ),
) <tbl-rows>

*Executable.* Every 1.17.1 address was read in the de-Arxan'd 1.17.1 image. Names come from a
#link("https://github.com/NationalSecurityAgency/ghidra")[Ghidra] dump of 1.16.2 and were carried
forward with
#link(repo + "/blob/main/scripts/map-rvas-1162-to-1170.py")[`scripts/map-rvas-1162-to-1170.py`];
all of them except the selector functions sit below rva 0xafefe9, where 1.17.0 and 1.17.1 agree.

#figure(
  caption: [Executable functions cited, 1.17.1 and 1.16.2.],
  ruled(
    columns: (1.7fr, auto, auto, 1.2fr),
    header: ([Function], [1.17.1], [1.16.2], [Working notes]),
    [`CanChangeEquipmentInSlot`], [0x140789910], [0x140788a90], [equip-gate],
    [gate predicate], [0x1407c1fb0], [0x1407c1130], [equip-gate],
    [`EquipDialog` slot confirm (vtable +0x90)], [0x1408de350], [0x1408dd1b0], [equip-gate],
    [`EquipItemToChrAsmSlot`], [0x140788ab0], [0x140787c30], [equip-gate; called live],
    [per-frame chr update (flags = 9)], [0x140401a30], [0x1404016d0], [equip-gate],
    [`PreBehaviorSafe`], [0x140401f30], [0x140401bd0], [driver frame clock],
    [`HksAct`], [0x14040d100], [0x14040cbd0], [equip-gate, damage-and-fp],
    [TimeAct `ChrActionFlag`], [0x140427b30], [0x1404275e0], [equip-gate],
    [weapon switch], [0x14042d4d0], [0x14042cf80], [hooked live],
    [`CSFeManImp::Update`], [0x140772a50], [0x140771bd0], [hooked live],
    [PlayerIns pre-update (ChrAsm sync)], [0x140660f70], [0x140660120], [skill-survives-equip],
    [arts offset resolver], [0x14041b0d0], [0x14041aba0], [skill-survives-equip],
    [active hand skill], [0x14047fcd0], [0x14047f770], [affected-class],
    [CMSG activate], [0x1419b9980], [0x1419b7b10], [skill-survives-equip],
    [CMSG update], [0x1419ba150], [0x1419b82e0], [skill-survives-equip],
    [CMSG name lookup], [0x1419bb2f0], [0x1419b9480], [skill-survives-equip],
    [`argTaeId` writer], [0x1419bb530], [0x1419b96c0], [hooked live],
    [`ResolveBehaviorId`], [0x1406530d0], [0x140652280], [damage-and-fp],
    [melee attack-info fill], [0x140690df0], [0x14068ffa0], [damage-and-fp],
    [`CalculateDamage2`], [0x140448910], [0x1404483b0], [hooked live],
    [`UpdateActiveAowFpStats`], [0x140480070], [0x14047fb10], [damage-and-fp],
    [`ConsumeFp`], [0x14047fba0], [0x14047f640], [hooked live],
    [`CalculateFpConsumption`], [0x14068c070], [0x14068b220], [damage-and-fp],
    [`UpdateFromManipulator`], [0x140408190], [0x140407c60], [skill-survives-equip],
    [`CSBulletManager::SpawnBullet`], [0x1403a2cb0], [0x1403a2ca0], [called live],
    [bullet manager global], [0x143d667a8], [0x143d62748], [read in place],
    [no-death test (+0x19b bit 0)], [0x1404379d0], [not mapped], [read in place],
  ),
) <tbl-exe>

*Tools.* Live work uses #link("https://github.com/frida/frida")[Frida]. The driver is
#link(branch + "scripts/frida/chainsaw-driver.js")[`scripts/frida/chainsaw-driver.js`] (offline
selftest with `node`), the lure and knockback probe
#link(branch + "scripts/frida/lure-trace.js")[`scripts/frida/lure-trace.js`], the knockback split
#link(branch + "scripts/er-mechanics-chainsaw-pull.py")[`scripts/er-mechanics-chainsaw-pull.py`], and
the class and carrier tables
#link(branch + "scripts/er-mechanics-chainsaw-class.py")[`scripts/er-mechanics-chainsaw-class.py`].
Working notes:
#link(branch + "docs/er-mechanics/chainsaw/driver.md")[`driver.md`],
#link(branch + "docs/er-mechanics/chainsaw/equip-gate.md")[`equip-gate.md`],
#link(branch + "docs/er-mechanics/chainsaw/skill-survives-equip.md")[`skill-survives-equip.md`],
#link(branch + "docs/er-mechanics/chainsaw/damage-and-fp.md")[`damage-and-fp.md`] and
#link(branch + "docs/er-mechanics/chainsaw/affected-class.md")[`affected-class.md`], all under
`docs/er-mechanics/chainsaw/`. Where they disagree with this paper, this paper carries the later
measurement: `damage-and-fp.md` computes the loop's hits from the source skill's rows, and
`skill-survives-equip.md` has the fallback child as inferred. All are in #link(repo)[er-mods-rs],
branch `feat/er-r3-view-rest-of-catalog`.
