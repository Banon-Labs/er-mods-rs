// Weapon buffs ride bullets from the wrong weapon: the Piquebone and Familial Rancor leak.
// Build: python3 scripts/build-whitepaper.py (writes target/whitepapers/weapon-buff-leak.pdf).

#let title = "Weapon Buffs Ride Bullets From the Wrong Weapon"
#let subtitle = "The Piquebone and Familial Rancor leak in ELDEN RING 1.17.1, and a one-comparison fix"
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
  #text(10pt)[Banon Labs · #link(repo)[er-mods-rs] · 4 October 2026]
  #v(14pt)
]

#label-box[
  #text(weight: "bold")[Abstract.]
  A weapon buff in ELDEN RING (any grease, Seppuku, the blade incantations) is a character-wide
  effect tagged with a hand. At every hit the game asks only whether the hit's context byte admits
  that hand; it never asks which weapon made the hit, although the hit records it. So hits from
  weapons that cannot hold a buff carry the buff of the other hand. We trace the defect through
  the 1.17.1 executable, state the exact conditions under which it fires, measure two exploits
  live (the Piquebone Arrow lure smoke and the Familial Rancor spirits), bound the class of
  affected bullets from the regulation, and validate a one-comparison fix on the running game: it
  refused all 111 leaking smoke hits and all 5 leaking spirit hits, and kept all 9 greased melee
  hits.
]

#v(4pt)
#text(9pt)[
  *Conventions.* Executable addresses are for the installed 1.17.1 build (PE 2.7.1.0); 1.16.2
  equivalents are in @tbl-exe. Param fields are named and placed by the ER paramdefs
  (@tbl-fields). Labels: *verified* = read in the executable or the regulation; *live* = observed
  with a Frida hook on the running game; *user* = observed by the player in game.
]

= The defect in brief

A grease or Seppuku buff belongs to one hand. The game enforces that when it removes the buff (a
weapon swap or a grip change removes it), but not when it applies the buff at a hit. At a hit it
compares a context byte, which comes from param data, with the buff's hand tag. Arrow behaviors
are all tagged as right-hand attacks, whichever hand holds the bow, and skill attacks admit a
right-hand buff unconditionally.

Two measured cases follow:

- *Piquebone Arrow lure smoke.* A locked-on Rain of Arrows from a bow spawns a lure smoke where
  the arrows land. Each smoke hit applied a right-hand Drawstring Rot Grease that was put on
  after the arrows had landed: 109 of 115 hits over 3.96 s (*live*).
- *Familial Rancor spirits.* Spirits launched by Family Heads in the right hand applied a
  left-hand Drawstring Rot Grease once the left weapon was two-handed (*live*, *user*).

The proposed fix is one comparison at the hit: apply a hand-tagged buff only if the weapon the
hit records is the weapon now gripped in that buff's hand.

= Background: how a weapon buff is stored

A weapon buff is a SpEffect on the character, not on the weapon (@tbl-buff). A grease cannot be
applied to a weapon whose `EquipParamWeapon.isEnhance` (+0x106) is 0; that includes every bow,
crossbow and Family Heads.

#figure(
  caption: [The SpEffectParam fields that make a weapon buff.],
  ruled(
    columns: (auto, auto, 1fr),
    header: ([Field (offset)], [Value on a grease], [Meaning]),
    [`stateInfo` (+0x156)], [152 or 153], [weapon buff; the hit-time reader considers only these],
    [`wepParamChange` (+0x158)], [1 right, 2 left; 5 and 6 grouped with them], [the hand the buff belongs to],
    [`atkOccurrenceSpEffectId` (+0x12c)], [the on-hit row], [SpEffect applied to the victim on each qualifying hit],
    [`isUseStatusAilmentAtkPowerCorrect` (+0x259 bit 0), on the on-hit row], [1 on 48 of 54 buff on-hit rows, every grease], [the on-hit status is scaled by the hit's AtkParam],
  ),
) <tbl-buff>

Example rows: Blood Grease Right 3190 (`wepParamChange` 1, on-hit 3191, `bloodAttackPower` 30);
Drawstring Rot Grease Right 3312 (on-hit 3313, `diseaseAttackPower` 80); Drawstring Rot Grease
Left 3316 (`wepParamChange` 2, on-hit 3317).

The game removes a buff by hand, never by weapon id (@tbl-removers). Its model is therefore that a
hand-tagged buff belongs to whatever weapon is gripped in that hand. Two-handing a left-hand bow
removes a right-hand grease (*user*).

#figure(
  caption: [Buff removal is keyed on the hand tag (*verified*).],
  ruled(
    columns: (1.5fr, 0.9fr, 1.6fr),
    header: ([Remover], [Removes], [Called when]),
    [`PlayerIns::RemoveWepParam1And5SpEffects` 0x140655b40, body 0x1404f79d0], [`wepParamChange` 1, 5], [the right slot changes, or the left weapon is two-handed (`SetChrAsmEquipmentState` 0x140426a70; weapon-switch event 0x14042d4d0)],
    [`SpecialEffect::RemoveWepParam2And6SpEffects` 0x1404f78d0], [`wepParamChange` 2, 6], [the left slot changes, or the right weapon is two-handed],
  ),
) <tbl-removers>

= The hit path

The weapon that made a hit is recorded in it, but the buff reader is never handed it
(*verified*):

+ *Launch.* 0x14038e220 picks the bullet's context byte: `BehaviorParam.category` (+0x1c) for a
  behavior launch, else `EquipParamGoods.spEffectCategory`, else `Magic.spEffectCategory`.
  Children spawned through `Bullet.HitBulletID` (+0x68) copy the parent's attack info.
  0x14038e390 fills the attack info with the hand slot (-1 right, -2 left), the equipment slot
  (a bow becomes its ammo slot) and the `EquipParamWeapon` id of the weapon in that hand.
+ *Hit record.* 0x140d26290 builds the AttackDamageInfo (ADI): context byte at +0xda, equipment
  slot at +0x48, launching weapon id at +0x144, AtkParam status scales at +0x13c and +0x140.
+ *Buff lookup.* `CalculateDamage2` (0x140448910) calls the reader 0x1404f7fb0 at 0x140449335
  with the attacker's SpEffect list and a mask built by 0x1404ffc30. The mask holds only the
  context byte, the sub-category mask and the throw flag; the weapon id is dropped here.
+ *Hand test.* For each live stateInfo 152/153 entry, `SpecialEffectEntry::IsApplicableForCategory`
  (0x140501700, jump table 0x1405018d8) compares the context byte with `wepParamChange`. The
  first entry that passes returns its `atkOccurrenceSpEffectId`.
+ *Apply.* 0x1403e8e70 applies that row to the victim. The buildup is scaled by ADI+0x140 ×
  ADI+0x13c when the row's +0x259 bit 0 is set (test at 0x140449372), and built in 0x1403fb010
  and 0x14043e050.

Two properties make the leak usable. First, the context byte comes from data, not from the hand
that fired: all 1,247 arrow `BehaviorParam_PC` rows are category 1 (right hand). Second, the
on-hit row is looked up live at every hit, while a buff's attack-power additions are snapshotted
at launch (0x1404f52f0). A buff put on after a long-lived bullet was fired still rides it.

= Exact conditions

A hit applies a buff's on-hit status exactly when conditions 1 to 4 hold at the moment of the hit
(@tbl-conditions). Nothing else is checked. It is a cross-weapon leak when condition 5 also
holds.

#figure(
  caption: [The conditions, their source in the executable or the regulation, and the measurement behind each.],
  ruled(
    columns: (auto, 2.2fr, 1.1fr, 1.5fr),
    header: ([], [Condition], [Source], [Measured]),
    [1], [A stateInfo 152/153 buff is live on the attacker at the moment of the hit. When the bullet was fired does not matter.], [reader 0x1404f7fb0], [6 smoke hits before greasing returned no buff; 109 after returned 3313 (*live*)],
    [2], [The context byte admits the buff's hand. Context 1 (arrows, most weapon bullets) admits `wepParamChange` 1; context 2 (bolts) admits 2; context 12 (skills) admits 1 always and 2 only while the left weapon is two-handed at the hit; contexts 3, 4, 10 admit only sorcery, incantation and shaman rows; context 0 and the rest admit only 0, 5, 6.], [0x140501700, table 0x1405018d8], [Rancor took a left grease only once two-handed (*live*); Eruption (context 0) and Poison Mist (context 4) never did (*user*)],
    [3], [The hit's AtkParam keeps both status scales above 0: `statusAilmentAtkPowerCorrectRate` (+0x18c) and `_byPoint` (+0x196).], [0x140d26290; test 0x140449372], [smoke and Rancor rows 100/100; Eruption's AtkParam 30000042 is 0/0],
    [4], [The victim can still build that status (resistance below 999).], [resist module], [immune enemy reads 999 (*live*)],
    [5], [The hit was made by a weapon other than the one gripped in the buff's hand.], [never checked], [smoke: bow vs. greased axe; Rancor: Family Heads vs. greased Urumi (*live*)],
  ),
) <tbl-conditions>

What separates a usable exploit from a theoretical one is timing. A bow must be two-handed to
fire from the left hand, and two-handing it removes the right-hand grease (@tbl-removers). So
condition 1 can only hold if the grease goes on after the shot and the bullet is still hitting
then. An ordinary arrow lands within a fraction of a second, before a grease can be applied,
which is why a plain left-bow shot carries nothing (*user*). The smoke lasts 4.0 s
(`Bullet.life` +0x10) and the Rancor spirits 12.0 s, long enough to grease or change grip after
firing. A left-hand buff through a skill (context 12) always needs this window, because the left
weapon has to be two-handed when the hit lands.

= Evidence

Both exploits were measured by the player and by a Frida hook on `CalculateDamage2`, the reader
and the apply call, logging the ADI of every hit the reader answered with a buff (run
`br-20261004-223342-81c1`, 4 October 2026). @tbl-live lists the hits; @tbl-user the player's
observations, each matched to the condition that decides it.

#figure(
  caption: [Live hits, unpatched game (*live*).],
  ruled(
    columns: (1.6fr, auto, 1.3fr, auto, auto, 1.5fr),
    header: ([Case], [Hits], [Bullet / AtkParam_Pc], [Ctx], [Scale], [Buff applied]),
    [Rain of Arrows falling arrows, before greasing], [4], [20003354 / 5036850], [1], [0.65], [none],
    [Piquebone smoke, before greasing], [6], [20003309 / 0], [1], [1.0], [none],
    [Piquebone smoke, right grease applied after landing], [109 in 3.96 s], [20003309 / 0], [1], [1.0], [3313 Drawstring Rot Grease Right, every hit],
    [Familial Rancor, left weapon two-handed], [6 in 3.4 s], [2021, 2023, 2025 / 301302901, 903, 905], [12], [1.0], [3317 Drawstring Rot Grease Left, every hit],
  ),
) <tbl-live>

In the Rancor hits the ADI records the right-hand launching weapon, 13020010 (Family Heads +10),
while the buff applied is the *left*-hand one. That field is what the fix compares.

#figure(
  caption: [Player observations (*user*) and the condition that decides each.],
  ruled(
    columns: (2.4fr, auto, 1.6fr),
    header: ([Setup], [Status built], [Deciding condition]),
    [Locked-on Rain of Arrows, Piquebone, grease applied after landing], [yes], [all hold],
    [Rain of Arrows without lock-on], [no], [4 in practice: the smoke does not land on the enemies],
    [Plain Piquebone shot from the left bow, right grease], [no], [1: two-handing the bow removes the grease, and the arrow lands before a new one],
    [Familial Rancor, left weapon greased, one-handed], [no], [2: context 12 refuses a left buff unless the left weapon is two-handed],
    [Familial Rancor, then left weapon two-handed], [yes], [all hold],
    [Eruption puddles after a swap, Soporific Grease], [no], [2 (context 0) and 3 (AtkParam 0/0)],
    [Poison Mist incantation, then Blood Grease], [no], [2 (context 4)],
  ),
) <tbl-user>

*Why lock-on matters.* The smoke-spawning bullet 20003308 has `launchConditionType` (+0x99) 5,
which the gate at 0x14039da50 reads as "spawn on a hit with a material, never on expiry". The
material id comes from whatever body the bullet struck, character or map, so the gate fires
wherever an arrow lands (*verified*). Lock-on does not change any row; it places the emitter over
the target and lets the falling arrows home onto it (`homingAngle` 10), so the smoke lands where
the enemies are. Unlocked, the arrows and their smoke land elsewhere (*user*).

= Scope

The regulation holds thousands of (bullet, context) pairs that pass conditions 2 and 3 for a
player-reachable hand-tagged buff while the launching weapon cannot hold one (@tbl-scope). The
counts are upper bounds: they include NPC-only weapon rows, and a bullet can appear in more than
one group.

#figure(
  caption: [The class the defect admits, from the 1.17.1 regulation.],
  ruled(
    columns: (1.7fr, auto, 1.3fr, auto, auto),
    header: ([Launcher], [Ctx], [Buff hand admitted], [Bullets], [At 100/100]),
    [Weapons with `isEnhance` 0, movesets and skills], [12], [right; left only while left two-handed], [1,768], [523],
    [same], [1], [right], [1,408], [594],
    [Arrows and greatarrows], [1], [right], [1,118], [664],
    [same as the first row], [2], [left], [419], [162],
    [Bolts and greatbolts], [2], [left], [130], [64],
  ),
) <tbl-scope>

The largest owners in the non-ammo groups are the five perfume bottles (about 240 to 260 bullets
each per context), Smithscript Dagger, Ghostflame Ignition, Death's Poker, Ruins Greatsword,
Claws of Night, Sacred Relic Sword, Wave of Gold, Smithscript Cirque, Bear Witness! and Discus
Hurl. Only bullets that keep hitting for a second or more after firing are usable, for the
timing reason above.

A status scale of 100/100 is not a sign of intent. It is the default for a weapon swing (743 of
1,258 skill-swing AtkParam rows); skill bullets split 216 rows at 0/0 against 109 at 100/100.
Familial Rancor's six rows (301302900–301302905) are 100/100, like those of 52 other skills on
weapons that cannot be buffed. No row-by-row rule separates intended from accidental carriers.
The class follows from the missing check.

= Proposed fix

At the buff lookup, skip a hand-tagged buff unless the hit was made by the weapon now gripped in
that buff's hand. This is the hit-side form of the rule the removers already enforce. In 1.17.1,
replace the reader call at 0x140449335 with a reader that also receives the ADI (R14 at that
call; RSI is the attacker) and adds one test per entry:

#block(inset: (x: 8pt, y: 6pt), fill: luma(246), width: 100%, radius: 2pt)[
```c
// same walk as the reader at 0x1404f7fb0, plus:
if ((row->stateInfo == 152 || row->stateInfo == 153) && owner_is_player) {
    int hand = row->wepParamChange;                         // SpEffectParam +0x158
    if (hand == 1 || hand == 5 || hand == 2 || hand == 6) {
        int slot = (hand == 1 || hand == 5) ? -1 : -2;      // right / left weapon
        int held = GetEquipmentEntryParamId(owner, slot);   // PlayerIns, 0x1406577b0
        if (adi->launchWeaponId /* +0x144 */ != held) continue;
    }
}
```
]

#figure(
  caption: [What the comparison does to each kind of hit.],
  ruled(
    columns: (2fr, 1.2fr, 1.2fr, auto),
    header: ([Hit], [ADI+0x144], [Weapon in buff's hand], [Result]),
    [Piquebone smoke, right grease], [the bow], [greased right weapon], [refused],
    [Familial Rancor spirits, left grease], [Family Heads], [greased left weapon], [refused],
    [A bullet fired by the previous weapon after a swap], [old weapon], [new weapon], [refused],
    [The buffed weapon's own swings and skill projectiles], [buffed weapon], [buffed weapon], [kept],
    [Hand-neutral buffs (`wepParamChange` 0), NPC attackers], [not tested], [not tested], [unchanged],
  ),
) <tbl-fix>

Rejected alternatives:

- *Patching `IsApplicableForCategory` alone* (0x140501700). It never receives the ADI, and the
  launch-time snapshot (0x1404f52f0) also calls it.
- *Extending the mask* built by 0x1404ffc30 with ADI+0x144. This works, but changes a struct
  three callers share.
- *A regulation-only fix*, either `BehaviorParam.category` 0 on carrier rows or zero status
  scales per carrier. The firing hand is not in the data (one behavior row fires from either
  hand), zeroing the scales also kills a weapon's own status scaling (AtkParam_Pc row 0 is shared
  by many zero-damage bullets), and the class is thousands of rows that every regulation update
  can silently reopen.

= Validation

The fix was prototyped as a Frida hook on the reader that returns "no buff" when the check fails,
and run against both exploits and a greased-melee control in the same session (@tbl-validation).

#figure(
  caption: [The fix on the running game (*live*, run `br-20261004-223342-81c1`).],
  ruled(
    columns: (1.9fr, auto, 1.3fr, 1.3fr, auto),
    header: ([Test], [Hits], [ADI+0x144], [Weapon in buff's hand], [Result]),
    [Greased melee control, left grease], [9], [20070200 Keen Urumi], [20070200 Keen Urumi], [kept; 3317 applied],
    [Piquebone smoke, right grease, fix on], [111], [40000025 Shortbow +25], [14020100 Heavy Hand Axe], [refused; none applied],
    [Familial Rancor, left grease, left weapon two-handed, fix on], [5], [13020010 Family Heads], [20070200 Keen Urumi], [refused; none applied],
  ),
) <tbl-validation>

The prototype differs from the proposed code in two ways: it refuses the first passing buff
instead of walking on, and it skips the ten on-hit ids that buffs of both hands share (880, 882,
1724, 1794, 1809, 1884, 1894, 20000874, 20000884, 20001034), since the returned id alone does not
name the hand. The proposed code receives the buff entry, so neither limit applies to it.

#pagebreak(weak: true)
= Sources

*Data.* The regulation is the installed 1.17.1 `regulation.bin`, decrypted and read with
#link(repo + "/blob/main/scripts/er-param-read.py")[`scripts/er-param-read.py`]. Field names and
offsets come from the ER paramdefs shipped with #link("https://github.com/vawser/Smithbox")[Smithbox],
and row names from its English row-name files.

#figure(
  caption: [Paramdef fields cited.],
  ruled(
    columns: (auto, 1fr, auto),
    header: ([Param], [Field], [Offset]),
    [SpEffectParam], [`effectEndurance`], [+0x8],
    [SpEffectParam], [`atkOccurrenceSpEffectId`], [+0x12c],
    [SpEffectParam], [`spCategory`], [+0x13e],
    [SpEffectParam], [`stateInfo`], [+0x156],
    [SpEffectParam], [`wepParamChange`], [+0x158],
    [SpEffectParam], [`magParamChange` / `miracleParamChange`], [+0x160 / +0x161],
    [SpEffectParam], [`isUseStatusAilmentAtkPowerCorrect`], [+0x259 bit 0],
    [BehaviorParam_PC], [`category`], [+0x1c],
    [AtkParam_Pc], [`statusAilmentAtkPowerCorrectRate` / `_byPoint`], [+0x18c / +0x196],
    [Bullet], [`atkId_Bullet`, `life`, `hitRadiusMax`, `dmgHitRecordLifeTime`], [+0x0, +0x10, +0x48, +0x58],
    [Bullet], [`HitBulletID`, `spEffectId0`, `homingAngle`, `launchConditionType`], [+0x68, +0x6c, +0x82, +0x99],
    [EquipParamWeapon], [`weaponCategory`, `isEnhance`], [+0xe6, +0x106],
  ),
) <tbl-fields>

#figure(
  caption: [Regulation rows cited.],
  ruled(
    columns: (auto, 1fr),
    header: ([Param], [Rows]),
    [SpEffectParam], [3190/3191 Blood Grease Right; 3312/3313 Drawstring Rot Grease Right; 3316/3317 Drawstring Rot Grease Left; 1755/1756 Seppuku; 482/483 lure markers],
    [Bullet], [Piquebone 20003300, 20003308, 20003309 (smoke: life 4.0 s, radius 0.05 → 15 m, AtkParam 0, `dmgHitRecordLifeTime` 0); Rain of Arrows 20003351–20003354; Familial Rancor 2020–2025 (life 12.0 s); Eruption 2012, 2013, 2018, 2019; Poison Mist 10722000, 10722001],
    [BehaviorParam_PC], [105040300 Piquebone shot; 105040851 Rain of Arrows; 301302901 Familial Rancor (category 12); 300000042 Eruption (category 0)],
    [AtkParam_Pc], [0 smoke (100/100); 5036850 falling arrow (65/100); 301302900–301302905 Rancor (100/100); 30000042 Eruption (0/0)],
    [EquipParamWeapon], [13020000 Family Heads; every bow and crossbow (`isEnhance` 0)],
  ),
) <tbl-rows>

*Executable.* Every 1.17.1 address was read in the de-Arxan'd 1.17.1 image. Names come from a
#link("https://github.com/NationalSecurityAgency/ghidra")[Ghidra] dump of 1.16.2 and were carried
forward with
#link(repo + "/blob/main/scripts/map-rvas-1162-to-1170.py")[`scripts/map-rvas-1162-to-1170.py`];
candidates without a unique signature were confirmed by matching prologues or call sites. At or
above rva 0xafefe9, 1.17.1 = 1.17.0 + 0x70.

#figure(
  caption: [Executable functions cited, 1.17.1 and 1.16.2.],
  ruled(
    columns: (1.6fr, auto, auto, 1.2fr),
    header: ([Function], [1.17.1], [1.16.2], [Confirmed by]),
    [context byte picker], [0x14038e220], [0x14038e210], [unique signature],
    [attack info fill], [0x14038e390], [0x14038e380], [unique signature],
    [ADI builder], [0x140d26290], [0x140d24b10], [its writes to ADI],
    [`CalculateDamage2`], [0x140448910], [0x1404483b0], [unique signature; hooked live],
    [reader call site], [0x140449335], [0x140448dd5], [read in place],
    [on-hit scale test], [0x140449372], [0x140448e12], [read in place],
    [buff reader], [0x1404f7fb0], [0x1404f71e0], [unique signature; hooked live],
    [mask builder], [0x1404ffc30], [0x1404fee60], [unique signature],
    [`IsApplicableForCategory`], [0x140501700], [0x140500930], [unique signature],
    [its jump table], [0x1405018d8], [0x140500b08], [read in place],
    [apply on-hit SpEffect], [0x1403e8e70], [0x1403e8c90], [unique signature; hooked live],
    [status buildup], [0x1403fb010, 0x14043e050], [0x1403fade0, 0x14043daf0], [unique signatures],
    [launch-time buff snapshot], [0x1404f52f0], [0x1404f4520], [prologue match],
    [`RemoveWepParam1And5SpEffects`], [0x140655b40], [0x140654cf0], [body match],
    [right-hand remover body], [0x1404f79d0], [0x1404f6c00], [prologue match],
    [`RemoveWepParam2And6SpEffects`], [0x1404f78d0], [0x1404f6b00], [prologue match],
    [`SetChrAsmEquipmentState`], [0x140426a70], [0x140426520], [unique signature],
    [weapon-switch event], [0x14042d4d0], [0x14042cf80], [unique signature],
    [`GetEquipmentEntryParamId`], [0x1406577b0], [0x140656960], [unique signature; called live],
    [melee attack-info fill], [0x140690df0], [0x14068ffa0], [unique signature],
    [TimeAct bullet launch], [0x1404273b0], [0x140426e60], [unique signature],
    [bow to ammo slot], [0x140655d50], [0x140654f00], [unique signature],
    [launch-condition gate], [0x14039da50], [0x14039da40], [call graph],
    [on-hit child spawn], [0x14039dcd0], [0x14039dcc0], [call graph],
    [bullet attack issue], [0x1403960b0], [0x1403960a0], [prologue match],
  ),
) <tbl-exe>

*Tools.* Live traces use #link("https://github.com/frida/frida")[Frida]. The probe and the fix
prototype are
#link(branch + "scripts/frida/weapon-buff-bullet-hits.js")[`scripts/frida/weapon-buff-bullet-hits.js`];
the scope counts come from
#link(branch + "scripts/interactions/leak_report.py")[`scripts/interactions/leak_report.py`] and
the carrier comparison from
#link(branch + "scripts/interactions/carrier_diff.py")[`scripts/interactions/carrier_diff.py`].
Working notes with the full traces:
#link(branch + "docs/er-mechanics/weapon-buff-leak.md")[`docs/er-mechanics/weapon-buff-leak.md`].
All are in #link(repo)[er-mods-rs], branch `feat/er-r3-view-rest-of-catalog`.
