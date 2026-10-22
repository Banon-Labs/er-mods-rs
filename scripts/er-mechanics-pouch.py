#!/usr/bin/env python3
"""Tool slots, spells, grease and flask split for one build, chosen from every good and spell the
installed regulation lets a player use in a multiplayer session, by the repo's own buff, defense
and recast rules.

    python3 scripts/er-mechanics-pouch.py "Poleblade of the Bud" --affinity Standard \\
        --stats vig=58,mnd=8,end=38,str=20,dex=22,int=7,fth=8,arc=68
    python3 scripts/er-mechanics-pouch.py Greatsword --affinity Heavy \\
        --stats vig=58,mnd=15,end=40,str=60,dex=12,int=9,fth=25,arc=9 --plan-weapons "Finger Seal"
    python3 scripts/er-mechanics-pouch.py ... --base ~/.cache/er-build-planner/weapon-builds/<dir>/base.json
    python3 scripts/er-mechanics-pouch.py --selftest

Interface (`er-weapon-build.py` passes the plan's weapons):

    P = importlib (scripts/er-mechanics-pouch.py)
    got = P.pick("Poleblade of the Bud", "Standard", 10, stats, plan_weapons=(), base=base_doc)
    got['tools']    # [{'name', 'order', 'equipIndex'}], the flasks included
    got['spells'], got['grease'], got['flasks'], got['scores'], got['rule'], got['skipped']

**The pool.** Every named `EquipParamGoods` row of `goodsType` 0 (an item) or 5/16/17/18 (a spell)
whose `enable_multi` is set and that one of the build's roles may use: `enable_live` a host,
`enable_hostile` an invader, `enable_friendly` a cooperator (Magic's own flags for a spell;
paramdef descriptions: usable during multiplayer, by a living player, a black ghost, a white
ghost). The roles come from the base build's tags (`Buffs.ROLE_BY_TAG`, host alone when no tag
names one); a kit is scored per role and the roles weighted, as `Buffs.corpus_kits` weights a kit,
a candidate a role may not use being left out of that role's kit. The flasks (`goodsUseAnim` 10
and 19, `goodsType` 9) are not candidates: their slots are kept and their split has its own rule
below. Tears and great runes
are not pouch items; a build's own (`base`) are held fixed in every kit, so stacking against them
is replayed. A spell is a candidate only when a held weapon casts its school
(`EquipParamWeapon.enableMagic` for sorceries, `enableMiracle` for incantations; the focus weapon
and `plan_weapons`), its `requirementIntellect/Faith/Luck` are met by the base stats and its
`slotLength` fits the memory slots left (`MEMORY_SLOTS`). A build that holds no catalyst of the
school still casts a buff the way players do it before contact: swap a catalyst and, when a
requirement is short, one talisman in, cast, swap back. The swap catalyst is the lightest named
`EquipParamWeapon` base row of the school whose requirements the build meets, the lowest requirement
sum breaking a tie (for incantations that is the Frenzied Flame Seal: no requirement, weight 0);
the swap talisman is the one whose passive stat adds (`EquipParamAccessory` `refId` and resident
SpEffects, `add*Status`, as `er-mechanics-defense.gather_effects` reads them) cover the whole shortfall with the least total add (Two Fingers Heirloom's Faith
+5 for Bestial Vitality's 12 at Faith 8). A swap-cast spell is cast once, before contact: a
mid-fight recast would need the same menu swap under fire.
A good whose `EquipParamGoods.isEnhance` is set (every grease) needs the focus weapon's
`EquipParamWeapon.isEnhance` (`CanUseGoods`, grease.md 3c, `VERIFIED`); a weapon-buff spell (a
row in `spCategory` 162/163) is held to the same gate (`INFERRED`: grease.md lists `CanUseMagic`
among the readers of the weapon's +0x106, the gate itself is not traced).

**What a candidate does.** Its SpEffect rows are `er-mechanics-buffs.Buffs.resolve` (items and
spells through their bullets and cycled rows, accumulator tiers and on-trigger rows as that module
selects them) plus its healing-over-time rows (`Buffs.regen_sources`). A kit is replayed through
`Buffs.stack` in use order, so the exclusive families (151 body buffs, 160 Golden Vow family,
162/163 weapon buffs per hand, 161 heals) keep one live entry each. Every live source is recast as
`Buffs.source_plans` recasts it over the fight (`EquipParamGoods.maxNum` uses at the item clip's
`er-mechanics-status.cure_frame` frames, none for an item its user walks with, a spell one FP bar of
casts at `SPELL_CAST_FRAMES`),
and every row counts by its `Buffs.planned_uptime`. Items of one `potGroupId` (the aromatics, the
pots) share one stock, split evenly between the kit's members of the group (`Pouch._limit`). The
fight's points and landed hits are
`er-builds-pvp.Mechanics.set_fight`'s corpus mix (duel and invasion by the window's tags,
`Buffs.fight_mix`).

An item that restores FP is a candidate through the rows that do it: a `changeMpPoint` or
`changeMpRate` below 0 on a row the item reaches, every `motionInterval` s for `effectEndurance` s
(FP per second x duration, `Buffs.regen_row`'s rule for HP; the tick schedule is `INFERRED`), or
once when the row does not tick (`fp_row`). In 1.17.1 that is Starlight Shards (501290: 2 FP every
1 s for 60 s, 120 a use) and the DLC's Lulling Branch (20500910: 5 FP every 2 s for 60 s, 150 a
use). Those rows do nothing to a hit or a cut; the item is valued through the flasks it frees
(below).

**Standing still.** `c0000.hks` `ExecItem` sends almost every item to its event on the upper body
alone while the lower body is moving (`MoveStart(LOWER, Event_Move)` turns the blend to `UPPER`;
only the spyglass, repair, message and quick knife events pass `ALLBODY`), and every `Item*_Upper`
state keeps the legs going through `HalfBlendLowerCommonFunction`, so the script does not tell a
Warming Stone from a Starlight Shard. The clip does: TAE JumpTable 89, which the community template
(WitchyBND `TAE.Template.ER.xml`) names "Disable All Movement", holds the Warming Stone's
`a000_050100` from f20 to f55 and the aromatics' `a000_050620` from f15 to f76, and no frame of
Starlight Shards' `a000_050520`, Lulling Branch's `a000_050800`, the bolus clip `a000_050000` or the
eating clip `a000_050180` (Boiled Crab, Exalted Flesh), which carry JumpTable 90 "Limit Move Speed
To Walk" instead (`TAE`, 1.17.1 `a00.tae`). An item's clip is the one-shot child the behavior graph
keeps for its `goodsUseAnim` (`ItemOneshot`, blended on `IndexItemUseAnim`, which `ExecItem` sets to
it: `Item_65_CMSG00` is `a000_050520`), or the state of its own `ExecItem` sends it to
(`USE_ANIM_CMSG`). An item whose clip holds no JumpTable 89 window walks while it is used, so its
uses cost no frames (time factor 1; user, 2026-10-14: Starlight Shards and the DLC FP item do not
make the user stand still, unlike the Warming Stone). One whose clip holds such a window, or whose
clip is not found, keeps the `item_cast_frames` charge. That the flag does what the template's name
says is `COMMUNITY`; the handler's write is not traced.

**How offense, defense and time make one number.** `er-mechanics-ashes.buff_option` already
values a buff that raises a build's damage and cuts what it takes (ashes-of-war.md 14b, 16b):

    score = buffed x (N + cut) / N x time factor

with `N` the HP the build lands per engagement. That trades HP saved one for one against HP dealt,
which only holds when the fight is even. Here the denominator is `L`, the HP the build loses per
engagement with no kit, so the score is, to first order, the damage the build deals before it
dies: D is offense x HP / (attackers x incoming), a cut share lengthens the life by the same
share at any attacker count, and a flat heal offsets heal / L of the drain. In the default
invasion model (`er-mechanics-buffs.FIGHT_KINDS`: about two opponents at once, 6.5 hosts' HP bars
on average, 10 s to 1 h) a percentage cut is worth what it is in a duel and a flat heal about half
of it.

    score = buffed x (L + cut + heal) / L x time factor

This module computes each term for the build, per kit, against the kit of fixed items alone:

- `buffed / unbuffed` = `offense`: the focus weapon's hit with the kit's `pre` (AR rate times the
  AR ratio of attribute adds, `Buffs.ar_stat_ratio`), `flat` (times the attack's
  `spEffectAtkPowerCorrectRate_byPoint`) and `post` (`atkPlayerDmgCorrectRate` x `*AttackRate`)
  rows, uptime-weighted as `Buffs.kit_factors` weights them, through the defense curve of the
  window's median defender (`er-builds-optimize.bracket_defender`), over the attack families
  `er-mechanics-armor-targets.damage_mix` files the pool's hits under (R1, R2, movement, jump,
  equal shares), over the same unbuffed hit.
- `L` = (1 - `FIGHT_WIN_SHARE`) x attackers x the incoming hit below with no kit.
- `cut` = (1 - `FIGHT_WIN_SHARE`) x attackers x the sum over damage types of what each hit of the window's
  pool would do to this build (`damage_mix` at this build's defense: mean hit x type share, times
  the absorption of `armor` when given, else the window's median absorption) x (1 - kept), kept
  being the product of the kit's uptime-weighted `cut x correct` rows (`Buffs.expected_defense`'s
  per-row rule).
- `heal` = the HP the kit's heal rows restore over one `ENGAGEMENT_SECONDS`, plus the HP of the
  Crimson flasks the kit has beyond the fixed kit, per engagement (the flasks, below).
- `time factor`: 1 - the kit's recasts x cast frames over the fight, `Buffs.kit_factors`' rule,
  an item its user walks with charged no frames (standing still, above).

No weight is chosen here: the currency is HP per engagement, as in buff_option, and the win share
is the symmetric fight `Buffs.fight_hits` already assumes (0.5, `INFERRED`). The defenders' own
kits multiply every candidate's damage alike and are left out of the ratio.

**The search.** The kit is filled greedily: each round tries every candidate both after and before
the current kit (use order decides which row of an exclusive family stays live), keeps the one
that raises the score most, and drops any member left with no live row. It stops when nothing
raises the score or the memory slots are full. The tool slots do not cap it: anything in the
inventory can be used from the menu, only storage is out of reach in combat (user, 2026-10-14). The
16 slots order how quick an item is to reach instead (`layout`): the flasks, then the picks used
most per fight in the pouch (one button plus a direction), then the quickbar (cycled), then the
rest carried with no slot, as the user lays theirs out (planner build `2ac169daa744e1`: Crab and
Raw Meat Dumpling in the pouch, pots, boluses and Uplifting Aromatic on the quickbar).
The grease is the picked source whose right-hand weapon-buff row is live, or None.

**The flasks.** The flask total (the base's `items.flasks.total`, else `FLASK_TOTAL`) is split by
the FP the build spends over the fight: every spell's casts (`Buffs.recast_points` with no use
limit, one cast when it is swap-cast) x `Magic.mp`, and the focus weapon's skill
(`er-mechanics-ashes.skill_fp`): a buff skill recast to cover the fight, any other skill once per
landed hit (`er-mechanics-ashes._hits_cap`'s rule for a skill with no FP limit). That need is paid
first by one FP bar at this build's Mind (`er-mechanics-resources`), then by the kit's FP items
(each its FP per use x its per-fight stock, the uses `_limit` gives it), then by the fewest
Cerulean flasks of `CERULEAN_FP` each; Crimson is the rest (`fp_plan`, `flask_split`).

The rule runs inside the evaluation, so a kit's flasks are part of its score. Each Crimson flask
the kit has beyond the fixed kit heals `FLASK_HEAL_HP` x `FLASK_ETA` once over a fight in which
crimson is drunk, and goes into `heal` as that HP over the fight's engagements: the mean landed hits
of the flask fights' schedule over `FIGHT_WIN_SHARE`, the build landing its hit in that share of
them (`ctx['engagements']`), times the share of the fight points that are flask fights
(`crimson_share`: 1 in the invasion, the flask tags' share in the duel mix, since duel etiquette
drinks no crimson, `Buffs.DUEL_TAGS`). So an FP
item scores by the Crimson it frees, and a spell loses by the Crimson its casts take. Every flask is
drunk either way, as Crimson or as Cerulean, at the same `FLASK_DRINK_FRAMES` (the cerulean clip is
not traced and takes the crimson one's, buffs.md), so the drink time is the same for every kit and
is not charged. FP is not capped at the bar here: a Cerulean drink pays its full 220 and an FP item
its full FP per use whatever the bar holds, as `flask_split` always has.

**Instant heals and their cures.** A good whose one-shot rows heal (`instant_spec`: Raw Meat
Dumpling, 50% of max HP) is scored like an extra crimson flask: each use of its stock heals that
HP over the flask fights' engagements (`FLASK_ETA`, `crimson_share`), less what the status it procs
on its own user costs (the dumpling's poison, 501236, over one battle), and charges its clip in the
time factor. A bolus is a candidate only for that: it is live when it ends such a status, which
then costs the ticks before the cure, and it is pressed once per heal (Neutralizing Boluses beside
the dumpling, as the user's own template carries them).

**The rest of the quickbar.** After the kit, `pots` takes the best pot of each pot group the kit
holds no member of, by HP a throw on the median defender (flat attack plus each on-hit status
row's proc, a status the focus weapon already builds up counting nothing), with the group's whole
container count; and `bolus_fill` lists a bolus for every status some window weapon inflicts,
most carried first. Both fill the quickbar after the picks (`layout`), the first pot taking a pouch
position left free. An FP item that only refills (Starlight Shards) is used between engagements and
takes the quickbar, not the pouch, and a buff the build holds one of (Gourmet Scorpion Stew,
`maxNum` 1) is used before contact from the menu and takes no slot (user, 2026-10-17).

**Left out, and reported in `skipped`.** Body transforms (a row whose `stateInfo` the paramdef labels
`Full Body Transform VFX`): Rock Heart and Priestess Heart turn the player into an ancient dragon
until death and are used disrobed (their GoodsCaption), so they replace the build being scored
rather than buff it, and their damage cut belongs to the dragon, not to this armor. Cure spells: only consumable cures are scored
(above). Thrown items (a damaging bullet) and attack spells: no thrown-item or spell damage term
in the kit score (`er-mechanics-armor-targets` leaves spells out for the same reason); pot-group
throwables are ranked apart (`pots`). Heal spells, and heals with no one-shot row: not modeled.
Status greases: their build-up needs the weapon's
attack chain (`er-mechanics-status.hit_buildup`), not wired here. Effect columns no term reads:
poise (`toughnessDamageCutRate`), `maxHpRate`, stamina, and status build-up adds on body buffs.
The sleep Lulling Branch builds up on its own user (its cycled row 20500911, `sleepAttackPower`
20 a second) is not charged: nothing values a status proc on this build (status cures, above).
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser('~/.cache/er-build-planner')


def _mod(name, fname):
    if name in sys.modules:
        return sys.modules[name]
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


B = _mod('er_mechanics_buffs', 'er-mechanics-buffs.py')

ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')
DAMAGE_KEYS = B.DAMAGE_KEYS             # slash, strike, pierce, standard, then the elements
MV_KEY = {'physical': 'mv_phys', 'magic': 'mv_mag', 'fire': 'mv_fire', 'lightning': 'mv_light',
          'holy': 'mv_holy'}
#: Planner tool positions: `equipIndex` 0..9 the quickbar, 10..15 the pouch (up, right, left,
#: down, then two more), `er-build-import-core/src/equip.rs`, read from the planner's own badge
#: code (`equipIndex < 10` -> quickbar `equipIndex + 1`, else `equipIndex - 10` of the pouch).
QUICKBAR, POUCH = 10, 6
TOOL_SLOTS = QUICKBAR + POUCH
#: Memory slots: 2 a character starts with plus the 8 Memory Stones (`COMMUNITY`), every stone
#: taken as found.
MEMORY_SLOTS = 10
#: The planner default the template carries: 14 flasks at +12.
FLASK_TOTAL = 14
CERULEAN_FP = B.CERULEAN_FP             # goods 1075 -> SpEffect 501062, -220 (`VERIFIED`)
FIGHT_WIN_SHARE = B.FIGHT_WIN_SHARE
#: `GOODS_USE_ANIM` 10 and 19: the crimson and cerulean flasks; `GOODS_TYPE` 9 Wondrous Physick.
FLASK_ANIM = {'crimson': 10, 'cerulean': 19}
PHYSICK_GOODS_TYPE = 9
SORCERY_TYPES, INCANTATION_TYPES = (5, 17), (16, 18)
ITEM_TYPE = 0
#: `goodsType` 11: a pot group's container (`er-mechanics-status`-adjacent census:
#: `scripts/regulation-potgroup-census.py`, `IsRegenerativeMaterial`, `VERIFIED`).
REGENERATIVE_MATERIAL = 11
#: What a grease tier the stat optimizer assumes (`er-builds-optimize.weapon_best`).
ASSUMED_TIER = 'dlc-drawstring'
#: Who may use a good or spell, by role (`Buffs.ROLE_BY_TAG`'s roles; paramdef descriptions: a
#: living player, a black ghost, a white ghost).
ROLE_FLAG = {'host': 'enable_live', 'invader': 'enable_hostile', 'cooperator': 'enable_friendly'}
EPS = 1e-6

#: Catalyst requirement columns (`EquipParamWeapon`) and spell requirement columns (`Magic`).
SWAP_REQUIREMENTS = {'str': 'properStrength', 'dex': 'properAgility', 'int': 'properMagic',
                     'fth': 'properFaith', 'arc': 'properLuck'}
SPELL_REQUIREMENTS = {'int': 'requirementIntellect', 'fth': 'requirementFaith', 'arc': 'requirementLuck'}
SPELL_STATS = tuple(SPELL_REQUIREMENTS)
#: The `SP_EFFECT_TYPE` label of the dragon-form state (`stateInfo` 167 on Rock Heart's 19980).
BODY_TRANSFORM_LABEL = 'Full Body Transform VFX'
#: Goods whose rows the closure reaches but which buff someone else, by the game's own caption,
#: where no param field says so. Goods 135, Phantom Great Rune: "Obtained after using the Mohg's
#: Great Rune and invading another world ... Enemies inside the invaded world receive a blessing of
#: blood" (GoodsCaption 135). Its x1.3 attack row (500139, through child bullet 10013501, whose
#: attack carries `friendlyTarget` 1: the invader's team holds the world's enemies) is that
#: blessing, so team flags cannot tell it from Golden Vow's ally buff.
BUFFS_OTHERS = {135: 'GoodsCaption 135: blesses the invaded world\'s enemies, needs Mohg\'s Great Rune'}
#: TAE type-0 JumpTable id the community template names "Disable All Movement": an item clip that
#: holds it stops its user (standing still, module docstring).
DISABLE_ALL_MOVEMENT_JT = 89
#: The full-body one-shot child the behavior graph keeps per `goodsUseAnim` under `ItemOneshot`
#: (`Item_65_CMSG00` -> `a000_050520`, Starlight Shards).
ONE_SHOT_CMSG = 'Item_{:02d}_CMSG00'
#: `goodsUseAnim` values `c0000.hks` `ExecItem` sends to a state of their own that has no one-shot
#: child (`common_define.hks` `ITEM_THORN` 15 -> `Event_ItemThorn`, `ITEM_ERDTREE` 49 ->
#: `Event_ItemErdtree`, `ITEM_THROW_BIGBOTTLE` 67 -> `Event_ItemThrowBigBottle`), by that state's
#: selector: `a000_050800` (Lulling Branch), `a000_050830` (the Golden Vow item), `a000_050710`.
USE_ANIM_CMSG = {15: 'ItemThorn_CMSG', 49: 'ItemErdtree_CMSG', 67: 'ItemThrowBigBottle_CMSG'}
#: `CustomManualSelectorGenerator` animId offset (`er-behbnd-tree.Tree.describe` reads offsetType
#: and animId as two i32 at +0xa8).
CMSG_ANIM_OFF = 0xac

SKIP_REASON = {
    'buffs others': 'its effect goes to someone else by its caption (BUFFS_OTHERS)',
    'body transform': 'turns the player into another body until death, worn disrobed: it replaces '
                      'the build instead of buffing it',
    'status cure': 'a cure spell: only consumable cures are scored (against a heal\'s own status, '
                   'and as the window\'s bolus fill)',
    'projectile': 'no thrown-item term in the kit score (a damaging bullet); pot-group throwables '
                  'are ranked apart, best per group (pots)',
    'attack spell': 'no spell damage model (er-mechanics-armor-targets leaves spells out too)',
    'instant heal': 'a heal spell, or a heal with no one-shot row (instant_spec): not modeled',
    'status weapon buff': 'status build-up per hit needs the attack chain '
                          '(er-mechanics-status.hit_buildup), not wired here',
    'no PvP term': 'no row any term reads (utility, travel, multiplayer items)',
    'not usable in multiplayer': 'enable_multi off, or neither a living player nor an invader may use it',
}


# -- pure parts, checked by the selftest ---------------------------------------------------------
def combine(offense, cut_hp, heal_hp, loss_hp, time_factor):
    """offense x (L + cut + heal) / L x time factor, `L` the HP the build loses per engagement with
    no kit: the damage it deals before it dies, to first order (module docstring)."""
    return offense * (loss_hp + cut_hp + heal_hp) / loss_hp * time_factor if loss_hp > 0 else 0.0


def flask_split(fp_need, fp_bar, total, per_flask=CERULEAN_FP, paid=0.0):
    """(crimson, cerulean): the fewest cerulean flasks that, on top of one `fp_bar` and the `paid`
    FP the kit's FP items restore, pay for `fp_need`, at most `total`; crimson is the rest. A drink
    restores `per_flask` FP but never past the bar, so a flask is worth the smaller of the two: a
    220 FP Cerulean +12 refills a 72 FP bar and wastes the rest."""
    per = min(per_flask, fp_bar) if fp_bar > 0 else per_flask
    short = max(0.0, fp_need - fp_bar - paid)
    cer = min(int(total), int(math.ceil(short / per - 1e-9)) if per > 0 else 0)
    return int(total) - cer, cer


def layout(picks, flasks, base_positions=None, quick=QUICKBAR, pouch=POUCH, uses=None, slow=(), extra=()):
    """Planner `items.tools.slots`: the flasks at the base build's positions when it has them,
    else the first free pouch positions in order; then the picks by `uses` per fight, most first
    (pick order breaking ties): the free pouch positions (the quickest to reach), then the
    quickbar's, then no position at all. A pick in `slow` is used between engagements and gains
    nothing from the pouch (an FP item that only refills: user, 2026-10-17, of Starlight Shards),
    so it takes a quickbar position. Then `extra` in order (the pots, then the boluses): a pouch
    position still free goes to the first of them, the rest fill the quickbar and are left out
    when it is full. A pick with no position is still carried and used from the inventory menu
    (module docstring), so nothing is dropped."""
    pos = dict(base_positions or {})
    taken = {p for n, p in pos.items() if n in flasks}
    out = []
    pouch_free = [p for p in range(quick, quick + pouch) if p not in taken]
    for n in flasks:
        p = pos.get(n)
        if p is None or p in {s['equipIndex'] for s in out}:
            p = pouch_free.pop(0)
        out.append({'name': n, 'equipIndex': p})
    used = {s['equipIndex'] for s in out}
    pouch_left = [p for p in range(quick, quick + pouch) if p not in used]
    bar_left = [p for p in range(quick) if p not in used]
    ranked = sorted(range(len(picks)), key=lambda i: (-(uses or {}).get(picks[i], 0.0), i))
    carried = []
    for i in ranked:
        if picks[i] not in slow and pouch_left:
            p = pouch_left.pop(0)
        elif bar_left:
            p = bar_left.pop(0)
        elif pouch_left:
            p = pouch_left.pop(0)
        else:
            carried.append({'name': picks[i], 'equipIndex': None})
            continue
        out.append({'name': picks[i], 'equipIndex': p})
    for k, name in enumerate(n for n in extra if n not in picks):
        if k == 0 and pouch_left:
            out.append({'name': name, 'equipIndex': pouch_left.pop(0)})
        elif bar_left:
            out.append({'name': name, 'equipIndex': bar_left.pop(0)})
    out.sort(key=lambda s: s['equipIndex'])
    return [dict(s, order=i) for i, s in enumerate(out + carried)]


def greedy(candidates, evaluate, fixed=(), fits=lambda kit, c: True, eps=EPS):
    """Fill a kit: each round the candidate (tried after and before the kit) that raises
    `evaluate(keys)['score']` most, members left with no live row dropped; stop when nothing
    raises it. `evaluate` returns {'score', 'live': set of keys with a live row}. Returns (kit,
    rounds, final evaluation)."""
    fixed = list(fixed)
    kit, rounds = [], []
    cur = evaluate(fixed)
    while True:
        best = None
        for c in candidates:
            if c in kit or not fits(kit, c):
                continue
            for trial in (kit + [c], [c] + kit):
                e = evaluate(fixed + trial)
                if c not in e['live']:
                    continue
                if best is None or e['score'] > best[0]['score'] + 1e-12:
                    best = (e, trial, c)
        if best is None or best[0]['score'] <= cur['score'] * (1.0 + eps):
            break
        e, trial, c = best
        kit = [k for k in trial if k in e['live']]
        prev = cur['score']
        cur = evaluate(fixed + kit)
        rounds.append({'key': c, 'gain': cur['score'] / prev - 1.0,
                       'dropped': [k for k in trial if k not in e['live']]})
    return kit, rounds, cur


# -- the model -----------------------------------------------------------------------------------
class Pouch:
    """The regulation, the corpus window and every candidate, loaded once per RL window."""

    def __init__(self, rl=150, window=10, regulation=None, fight=B.DEFAULT_FIGHT):
        self.rl, self.window = rl, window
        self.fight_kind = fight
        self.attackers = B.FIGHT_KINDS[fight]['attackers']
        self.AR = _mod('er_mechanics_ar', 'er-mechanics-ar.py')
        self.AT = _mod('er_mechanics_armor_targets', 'er-mechanics-armor-targets.py')
        self.ST = _mod('er_mechanics_status', 'er-mechanics-status.py')
        self.OPT = _mod('er_builds_optimize', 'er-builds-optimize.py')
        self.RES = _mod('er_mechanics_resources', 'er-mechanics-resources.py')
        self.DEF = self.AT.DEF
        self.ATK = self.AT.ATK
        self.m = m = B.Buffs(regulation)
        m.item_cast_frames = float(self.ST.cure_frame())
        self.tables = self.AR.Tables(regulation)
        self.reg = self.ATK.Regulation(regulation)
        self.def_t = self.DEF.Tables(regulation)
        self._ash = None
        PR, files = B.PR, m._files

        def rows(stem, fields):
            return {r['id']: r for r in PR.rows(PR.param_bytes(files, stem), set(fields))[0]}

        self.gflag = rows('EquipParamGoods', ('goodsType', 'goodsUseAnim', 'maxNum', 'isEnhance', 'enable_multi',
                                              'enable_live', 'enable_hostile', 'enable_friendly', 'potGroupId'))
        self.magic = rows('Magic', ('mp', 'slotLength', 'requirementIntellect', 'requirementFaith',
                                    'requirementLuck', 'enable_multi', 'enable_live', 'enable_hostile',
                                    'enable_friendly'))
        self.wflag = rows('EquipParamWeapon', ('enableMagic', 'enableMiracle', 'isEnhance', 'swordArtsParamId'))
        self._swap_pool()
        self.bullet_atk = {i: r['atkId_Bullet'] for i, r in rows('Bullet', ('atkId_Bullet',)).items()}
        self.by_point = {i: r['spEffectAtkPowerCorrectRate_byPoint']
                         for i, r in rows('AtkParam_Pc', ('spEffectAtkPowerCorrectRate_byPoint',)).items()}
        # FP columns `Buffs` does not load: what an FP item restores (`fp_row`).
        self.mp_sp = rows('SpEffectParam', ('changeMpPoint', 'changeMpRate'))
        self._use_clips()
        lo, hi = rl - window, rl + window
        self.opt_rows = self.OPT.corpus_rows(self.OPT.CACHE / 'builds.jsonl', lo, hi)
        self.defender = self.OPT.bracket_defender(self.opt_rows)
        self.pvp_builds = self.AT.corpus_builds(lo, hi)
        self.st_rows = self.ST.corpus_rows(rl=rl, window=window)
        self._fight()
        self._catalogue()
        self._att, self._dfn, self._up, self._spell_fp = {}, {}, {}, {}

    # -- the fight, as er-builds-pvp.Mechanics.set_fight (flasks 'corpus') ----------------------
    def _fight(self):
        """The fight points and landed hits. A duel is the corpus mix of duel etiquette (no crimson)
        and flask fights; an invasion is the invasion's lengths against `targets` hosts' HP bars,
        each drinking the corpus median crimson count."""
        rows, ST = self.st_rows, self.ST
        kind = B.FIGHT_KINDS[self.fight_kind]
        fs = B.fight_kind_points(self.fight_kind)
        hp = ST.quantiles([r['hp'] for r in rows], (0.5,)).get(0.5, 0.0)
        cr = sorted(int(r['flasks']['crimson']) for r in rows if (r.get('flasks') or {}).get('crimson') is not None)
        crimson = cr[len(cr) // 2] if cr else B.CORPUS_CRIMSON
        targets = kind['targets']

        def sched(k):
            return B.fight_hits(fs, hp * targets, ST.FIGHT_REF_DAMAGE, k * targets, B.FLASK_HEAL_HP, B.FLASK_ETA,
                                ST.ENGAGEMENT_SECONDS)
        # The build's own crimson flasks are drunk only in the flask fights: `crimson_share` of the
        # points (`fight_mix`'s rounded shares), whose landed hits are `crimson_hits` (the flasks).
        drink = sched(crimson)
        if self.fight_kind == 'duel':
            shares = B.tag_shares(rows)
            self.points, self.hits = B.fight_mix([(shares['duel'], fs, sched(0)),
                                                  (shares['flasks'], fs, drink)])
            duel_share = round(shares['duel'], 3)
            reps = [round(s * B.FIGHT_MIX_PARTS) for s in (shares['duel'], shares['flasks'])]
            self.crimson_share = reps[1] / sum(reps) if sum(reps) else 0.0
        else:
            self.points, self.hits = tuple(fs), drink
            duel_share = 0.0
            self.crimson_share = 1.0
        self.crimson_hits = B.hits_mean(drink)
        self.fight = {'kind': self.fight_kind, 'attackers': self.attackers, 'targets': targets,
                      'seconds': [round(min(fs)), round(max(fs))], 'points': len(self.points),
                      'hits_mean': round(B.hits_mean(self.hits), 2), 'duel_share': duel_share,
                      'crimson': crimson, 'median_hp': hp}

    # -- the pool --------------------------------------------------------------------------------
    def _swap_pool(self):
        """`swap_catalysts` {school: [(weight, requirement sum, name, requirements)]} sorted, and
        `stat_talismans` [(total add, name, {int, fth, arc})], for swap-casting (module docstring)."""
        t, D = self.tables, self.def_t
        self.swap_catalysts = {'sorcery': [], 'incantation': []}
        for wid, w in t.weapons.items():
            name, f = t.names.get(wid), self.wflag.get(wid) or {}
            if wid % 10000 or not name or name.startswith('['):
                continue
            need = {k: w[col] for k, col in SWAP_REQUIREMENTS.items()}
            for school, flag in (('sorcery', 'enableMagic'), ('incantation', 'enableMiracle')):
                if f.get(flag):
                    self.swap_catalysts[school].append((w['weight'], sum(need.values()), name, need))
        for v in self.swap_catalysts.values():
            v.sort(key=lambda r: r[:3])
        names = self.DEF.PR.row_names('EquipParamAccessory')
        self.stat_talismans = []
        for rid, acc in D.accessory.items():
            name = names.get(rid)
            if not name or name.startswith('['):
                continue
            effects = [D.speffect[s] for s in (acc['refId'], acc['residentSpEffectId1'], acc['residentSpEffectId2'])
                       if s > 0 and s in D.speffect and self.DEF.is_passive(D.speffect[s])]
            adds = {k: sum(sp[self.DEF.STAT_ADD[k]] for sp in effects) for k in SPELL_STATS}
            if any(adds.values()):
                self.stat_talismans.append((sum(adds.values()), name, adds))
        self.stat_talismans.sort(key=lambda r: r[:2])

    def swap_for(self, ctx, c):
        """{'catalyst', 'talisman'} a build swaps in to cast spell candidate `c`, `{}` when it casts
        it as it stands, or None when it cannot cast it at all."""
        mg = self.magic.get(c['goods']) or {}
        st = ctx['stats']
        short = {k: mg.get(col, 0) - st[k] for k, col in SPELL_REQUIREMENTS.items() if mg.get(col, 0) > st[k]}
        held = c['school'] in ctx['schools']
        if not held and c['school'] not in ctx['swap']:
            return None
        if mg.get('slotLength', 1) > ctx['memory_slots']:
            return None
        if held and not short:
            return {}
        talisman = None
        if short:
            talisman = next((n for _, n, adds in self.stat_talismans if all(adds[k] >= v for k, v in short.items())),
                            None)
            if talisman is None:
                return None
        return {'catalyst': None if held else ctx['swap'][c['school']], 'talisman': talisman}

    def _use_clips(self):
        """`use_clip` {goodsUseAnim: (clip animId, walks)} for every item's use animation:
        the clip from the behavior graph (`ONE_SHOT_CMSG`, `USE_ANIM_CMSG`), and whether its
        `a00.tae` events hold no `DISABLE_ALL_MOVEMENT_JT` window (standing still, module
        docstring). (None, None) when the graph, the TAE or the clip is not found."""
        self.use_clip = {}
        fa = self.ST._fa()
        tae = fa.common_tae() or {}
        A = fa.ATTACKS
        try:
            tree = _mod('er_behbnd_tree', 'er-behbnd-tree.py').Tree()
        except (OSError, ValueError, KeyError, IndexError, struct.error):
            tree = None
        for u in sorted({f['goodsUseAnim'] for f in self.gflag.values()}):
            ids = tree.byname.get(USE_ANIM_CMSG.get(u) or ONE_SHOT_CMSG.format(u)) if tree else None
            if not ids:
                self.use_clip[u] = (None, None)
                continue
            clip = tree.raw(tree.tf.items[ids[0]], CMSG_ANIM_OFF, '<i')
            ev = tae.get(clip)
            if ev is None:
                self.use_clip[u] = (clip, None)
                continue
            stops = any(e.type == A.TAE_JUMP_TABLE
                        and struct.unpack_from('<i', e.params, 0)[0] == DISABLE_ALL_MOVEMENT_JT
                        and not struct.unpack_from('<H', e.params, A.JUMP_TABLE_STATE_GATE_OFFSET)[0]
                        for e in ev)
            self.use_clip[u] = (clip, not stops)

    def fp_row(self, sid):
        """(FP per tick, max-FP percent per tick, tick interval s, duration s) of a row that
        restores its owner's FP, or None. `changeMpPoint` / `changeMpRate` below 0 restore (the
        flask writes its own `changeMpEstusFlask*` columns, and a positive value drains); a row
        that ticks (`motionInterval` > 0) lasts `effectEndurance` s, one that lives under
        `REFRESHED_ROW_S` or forever is left out as `Buffs.regen_row` leaves an HP one; a row that
        does not tick restores once (interval 0)."""
        r, mp = self.m.sp.get(sid), self.mp_sp.get(sid)
        if r is None or mp is None:
            return None
        pt, pct = 0.0 - mp['changeMpPoint'], 0.0 - mp['changeMpRate']
        if pt < 0 or pct < 0 or pt + pct <= 0:
            return None
        iv, d = r['motionInterval'], r['effectEndurance']
        if iv <= 0:
            return pt, pct, 0.0, 0.0
        if d < B.REFRESHED_ROW_S:
            return None
        return pt, pct, iv, d

    def fp_per_use(self, c, fp_bar):
        """FP one use of FP item `c` restores: each FP row's FP per tick x ticks (duration over
        interval, `INFERRED`), or its one restore; a percent row at `fp_bar`."""
        out = 0.0
        for sid in c.get('fp_rows', ()):
            pt, pct, iv, d = self.fp_row(sid)
            out += (pt + pct * fp_bar / 100.0) * (d / iv if iv > 0 else 1.0)
        return out

    def instant_spec(self, rows):
        """{'rate', 'point', 'self_status'} of a good whose rows heal its user at once, or None:
        the one-shot rows (`motionInterval` 0 or `effectEndurance` 0) with a negative
        `changeHpRate` / `changeHpPoint` (instant heals, module docstring), and the status rows it
        procs on its own user, [(status, row)] by `er-mechanics-status.row_status` (Raw Meat
        Dumpling: 501235 heals 50% of max HP and replaces itself with 501236, poison 99999 and its
        damage columns, 90 s)."""
        rate = point = 0.0
        own = []
        for sid in rows:
            r = self.m.sp.get(sid)
            if r is None:
                continue
            if (r['changeHpRate'] < 0 or r['changeHpPoint'] < 0) and (r['motionInterval'] <= 0
                                                                       or r['effectEndurance'] <= 0):
                rate -= min(0.0, r['changeHpRate'])
                point -= min(0.0, r['changeHpPoint'])
            rs = self.ST.row_status(r)
            if rs:
                own.append((rs[0], sid))
        if rate <= 0 and point <= 0:
            return None
        return {'rate': rate, 'point': point, 'self_status': own}

    def self_status_hp(self, sid, max_hp, cured):
        """HP a status row a heal procs on its own user costs: its damage per tick (`proc_effect`'s
        rule) over the ticks inside one battle (`er-mechanics-status.BATTLE_SECONDS`, as the ash
        table cuts a damage-over-time proc), or, when the kit cures that status, over the bolus
        clip's `cure_frame` plus `DOT_CURE_DELAY` (`INFERRED`: the cure is pressed as soon as the
        heal ends)."""
        r = self.m.sp[sid]
        per = max_hp * r['changeHpRate'] / 100.0 + r['changeHpPoint']
        iv, dur = r['motionInterval'], r['effectEndurance']
        if per <= 0:
            return 0.0
        if iv <= 0 or dur <= 0:
            return per
        span = (self.ST.cure_frame() + self.ST.DOT_CURE_DELAY) / B.CAST_FPS if cured else self.ST.BATTLE_SECONDS
        return per * max(1, int(min(dur, span) / iv))

    def instant_hp(self, ctx, c, keys):
        """Net HP one use of instant heal `c` gives this build: the heal, through `FLASK_ETA` as a
        flask's is, less what its own status costs (`self_status_hp`), a status the kit `keys`
        cures costing only the ticks before the cure."""
        sp = c['instant']
        cures = {self.cands[k]['cures'] for k in keys if k in self.cands and self.cands[k].get('cures')}
        heal = (ctx['max_hp'] * sp['rate'] / 100.0 + sp['point']) * B.FLASK_ETA
        return heal - sum(self.self_status_hp(sid, ctx['max_hp'], st in cures) for st, sid in sp['self_status'])

    def _bullet_damages(self, roots):
        seen, todo = set(), list(roots)
        while todo:
            i = todo.pop()
            if i is None or i <= 0 or i in seen or i not in self.m.bullet:
                continue
            seen.add(i)
            if self.bullet_atk.get(i, -1) > 0:
                return True
            todo += [self.m.bullet[i][f] for f in B.BULLET_LINKS]
        return False

    def _catalogue(self):
        """{key: candidate} for every usable item and spell, and {class: [names]} for the rest."""
        m = self.m
        regen = m.regen_sources()
        transform = {k for k, v in m.state_names.items() if v == BODY_TRANSFORM_LABEL}
        self.cands, self.unscored, seen = {}, {}, set()
        self.flask_names = {}
        self.throwables = {}
        # Pot capacity per `potGroupId`: the most a player can hold of the group's regenerative
        # material (Cracked Pot 20, Ritual Pot 10, Perfume Bottle 10, Hefty Cracked Pot 10).
        self.pot_capacity = {}
        for gid, f in self.gflag.items():
            if f['goodsType'] == REGENERATIVE_MATERIAL and f['potGroupId'] >= 0:
                self.pot_capacity[f['potGroupId']] = max(self.pot_capacity.get(f['potGroupId'], 0), f['maxNum'])
        for gid, g in sorted(m.goods.items()):
            name = m.goods_name.get(gid)
            f = self.gflag.get(gid)
            if not name or f is None:
                continue
            t = f['goodsType']
            for kind, anim in FLASK_ANIM.items():
                if t == ITEM_TYPE and f['goodsUseAnim'] == anim:
                    self.flask_names.setdefault(kind, name)
            if t == PHYSICK_GOODS_TYPE:
                self.flask_names.setdefault('physick', name)
            if t == ITEM_TYPE and f['goodsUseAnim'] in FLASK_ANIM.values():
                continue
            if t in SORCERY_TYPES + INCANTATION_TYPES:
                kind = 'spell'
                flags = self.magic.get(gid)
            elif t == ITEM_TYPE:
                kind, flags = 'consumable', f
            else:
                continue
            key = f'{kind}:{name}'
            if key in seen or flags is None:
                continue
            seen.add(key)
            if not (flags['enable_multi'] and (flags['enable_live'] or flags['enable_hostile'])):
                self.unscored.setdefault('not usable in multiplayer', []).append(name)
                continue
            rows = []
            if key in m.by_name:
                rows = [i for i, _ in m.resolve([key])[0]]
            rows += [i for i in regen.get(key, ()) if i not in rows]
            fp_rows = []
            if kind == 'consumable':
                refs = B._goods_refs(m, gid, g)
                if refs:
                    fp_rows = [i for i, _ in m._closure(refs[1], refs[2]) if self.fp_row(i)]
            rows += [i for i in fp_rows if i not in rows]
            clip, walks = self.use_clip.get(f['goodsUseAnim'], (None, None))
            c = {'key': key, 'name': name, 'kind': kind, 'goods': gid, 'rows': rows, 'fp_rows': fp_rows,
                 'use_clip': clip if kind == 'consumable' else None, 'walks': kind == 'consumable' and walks is True,
                 'school': ('sorcery' if t in SORCERY_TYPES else 'incantation') if kind == 'spell' else None,
                 'weapon_buff': any(m.sp[i]['spCategory'] in B.WEAPON_BUFF_CATS for i in rows),
                 'needs_enhance': bool(f['isEnhance']),
                 'roles': {r for r, col in ROLE_FLAG.items() if flags[col]},
                 'pot_group': f['potGroupId'] if kind == 'consumable' else -1}
            c['needs_enhance'] |= c['weapon_buff']
            if any(m.sp[i]['stateInfo'] in transform for i in rows):
                self.unscored.setdefault('body transform', []).append(name)
                continue
            if gid in BUFFS_OTHERS:
                self.unscored.setdefault('buffs others', []).append(name)
                continue
            if rows:
                self.cands[key] = c
                continue
            refs = B._goods_refs(m, gid, g)
            allrows = [i for i, _ in m._closure(refs[1], refs[2])] if refs else []
            sp = [m.sp[i] for i in allrows]
            cure = next((cs for cs in (self.ST.cure_status(r) for r in sp) if cs), None) if kind == 'consumable' else None
            if cure:
                # A cure is scored only where it ends a status a kit member procs on its own
                # user (`instant_hp`); the window's own statuses fill the quickbar (`bolus_fill`).
                self.cands[key] = dict(c, cures=cure[0])
                continue
            inst = self.instant_spec(allrows) if kind == 'consumable' else None
            if inst:
                self.cands[key] = dict(c, instant=inst)
                continue
            if (kind == 'consumable' and c['pot_group'] >= 0 and refs and refs[2]
                    and self._bullet_damages(refs[2])):
                self.throwables[key] = dict(c, bullets=[b for b in refs[2] if b and b > 0])
            if any(r[col] < 0 for r in sp for col in B.STATUS.values()):
                cls = 'status cure'
            elif f['isEnhance'] or any(r['spCategory'] in B.WEAPON_BUFF_CATS for r in sp):
                cls = 'status weapon buff'
            elif any(r['changeHpPoint'] < 0 or r['changeHpRate'] < 0 for r in sp):
                cls = 'instant heal'
            elif refs and self._bullet_damages(refs[2]):
                cls = 'attack spell' if kind == 'spell' else 'projectile'
            else:
                cls = 'no PvP term'
            self.unscored.setdefault(cls, []).append(name)

    # -- one build -------------------------------------------------------------------------------
    def context(self, weapon, affinity, level, stats, plan_weapons=(), two_handed=False, talismans=(),
                great_rune=None, armor=(), fixed=(), skill=None, memory_slots=MEMORY_SLOTS, win=FIGHT_WIN_SHARE,
                roles=None, flask_total=FLASK_TOTAL):
        """Everything the evaluation of one build reads: its hit, the hit it takes, its fight,
        its flasks. `roles` is {role: weight}, host alone by default."""
        AR, AT, DEF, ATK = self.AR, self.AT, self.DEF, self.ATK
        t = self.tables
        wid = t.find_weapon(weapon, affinity)
        if level is None:
            level = t.max_level(t.weapons[wid]['reinforceTypeId'])
        dmg = {k: stats[k] for k in ('str', 'dex', 'int', 'fth', 'arc')}
        r = AR.attack_rating(t, weapon, affinity, level, dmg, two_handed)
        ar = {el: r['damage'].get(el, {}).get('total', 0.0) for el in ELEMENTS}
        off = ATK.TWO_HAND_JUDGE_OFFSET if two_handed else 0
        fams = {}
        for fam, judges in AT.FAMILY_JUDGES.items():
            got = [a for a in (ATK.attack_numbers(self.reg, wid, j + off, level) for j in judges)
                   if a is not None and a['can_hit_enemy']]
            if got:
                fams[fam] = got
        slots = [(1.0 / len(fams) / len(got), {el: a[MV_KEY[el]] for el in ELEMENTS}, a['phys_type'],
                  self.by_point.get(a['atk_row'], 100) / 100.0) for got in fams.values() for a in got]
        me = DEF.defender(self.def_t, {STAT_ALIAS.get(k, k): v for k, v in stats.items()}, list(armor),
                          list(talismans), great_rune, pvp=True)
        mix = AT.damage_mix(self.pvp_builds, me['defense'], t, self.reg)
        absorb = {}
        for ty in DAMAGE_KEYS:
            k = 'physical' if ty == 'standard' else ty
            a = me['armor_mult'][k] if armor else self.defender['armor_mult'][k]
            absorb[ty] = a * me['effect_mult'][k]
        incoming = {ty: mix['mean_hit'] * mix['share'][ty] * absorb[ty] for ty in DAMAGE_KEYS}
        res = self.RES.Model().resources(stats, list(talismans), list(armor), great_rune)
        held = [wid] + [t.find_weapon(w) for w in plan_weapons]
        schools = set()
        for w in held:
            f = self.wflag.get(w) or {}
            if f.get('enableMagic'):
                schools.add('sorcery')
            if f.get('enableMiracle'):
                schools.add('incantation')
        swap = {}
        for school, cats in self.swap_catalysts.items():
            if school not in schools:
                got = next((n for _, _, n, need in cats if all(stats[k] >= v for k, v in need.items())), None)
                if got:
                    swap[school] = got
        ratio = B.ar_stat_ratio(weapon, affinity, level, stats, two_handed, t)
        ctx = {'weapon': weapon, 'affinity': affinity, 'level': level, 'stats': dict(stats), 'two': two_handed,
               'swap': swap, 'swap_spells': {},
               'wid': wid, 'enhance': bool((self.wflag.get(wid) or {}).get('isEnhance')), 'ar': ar, 'slots': slots,
               'incoming': incoming, 'mix': mix, 'max_hp': float(res['max_hp']), 'fp_bar': float(res['max_fp']),
               'schools': schools, 'fixed': list(fixed), 'win': win, 'memory_slots': memory_slots,
               'skill': skill if skill is not None else (self.wflag.get(wid) or {}).get('swordArtsParamId'),
               'cache': {}, 'ratio_cache': {}, 'ratio_fn': ratio, 'plan_weapons': list(plan_weapons),
               'roles': dict(roles or {'host': 1.0}), 'flask_total': int(flask_total),
               # The flask fights' engagements: the build lands its hit in `win` of them (the flasks).
               'engagements': self.crimson_hits / win if win > 0 else self.crimson_hits,
               'loss': (1.0 - win) * self.attackers * sum(incoming.values())}
        ctx['base_hit'] = self.hit(ctx)
        return ctx

    def hit(self, ctx, pre=None, flat=None, post=None, ratio=None):
        """The focus weapon's mean hit over the attack families on the median defender."""
        total = 0.0
        for w, mv, phys, bp in ctx['slots']:
            scaled = {}
            for el in ELEMENTS:
                v = ctx['ar'][el] * mv[el] / 100.0
                v *= (pre[el] if pre else 1.0) * (ratio.get(el, 1.0) if ratio else 1.0)
                if flat:
                    v += flat[el] * bp
                scaled[el] = v
            d = self.DEF.damage(scaled, 100.0, self.defender, phys)['by_type']
            total += w * sum(d[el] * (post[el] if post else 1.0) for el in ELEMENTS)
        return total

    def castable(self, ctx, c):
        """A spell the build casts as it stands or through a swap (`swap_for`); a swap is recorded
        in `ctx['swap_spells']`, which holds the spell to one cast before contact (`_limit`)."""
        if c['kind'] != 'spell':
            return True
        how = self.swap_for(ctx, c)
        if how:
            ctx['swap_spells'][c['key']] = how
        return how is not None

    def candidates(self, ctx):
        out = []
        for key, c in self.cands.items():
            if c['needs_enhance'] and not ctx['enhance']:
                continue
            if not self.castable(ctx, c) or not c['roles'] & set(ctx['roles']):
                continue
            out.append(key)
        return out

    def _rows(self, key, role='host'):
        c = self.cands.get(key)
        if c is not None:
            return c['rows']
        return [i for i, _ in self.m.resolve([key], role)[0]]

    def _limit(self, key, keys=(), swapped=()):
        """(uses, cast frames) of a kit source. Items of one `potGroupId` share one stock:
        `GetMaxAmountForItem` (bd consumable-quantity-is-equipparamgoods-maxnum-0x3a-2026-08-30,
        `VERIFIED`) caps a pot item by its group's free container capacity, not by its own
        `maxNum`. The container count is the group's material `maxNum` (`pot_capacity`: the most
        of it a player can hold, `INFERRED` to be what a finished save holds), capped by the
        member's own `maxNum` and split evenly between the kit's members of the group. An item
        its user walks with costs no frames (standing still, module docstring)."""
        c = self.cands.get(key)
        if c is not None and c['kind'] == 'consumable':
            uses = max(1, int(self.gflag[c['goods']]['maxNum'] or 1))
            if c['pot_group'] >= 0:
                uses = min(uses, self.pot_capacity.get(c['pot_group'], uses))
                n = sum(1 for k in keys if k in self.cands and self.cands[k]['pot_group'] == c['pot_group'])
                uses = max(1, uses // max(1, n))
            return uses, 0.0 if c['walks'] else self.m.item_cast_frames
        if key in swapped:
            return 1, self.m.source_recast(key)[1]
        return self.m.source_recast(key)

    def _attack(self, sid, two):
        k = (sid, two)
        if k not in self._att:
            self._att[k] = self.m.attack_context([sid], pvp=True, two_handed=two, apply_stack=False)
        return self._att[k]

    def _defense(self, sid):
        if sid not in self._dfn:
            m, out = self.m, {}
            for pt in B.PHYS_TYPES:
                d = m.defense_context([sid], pvp=True, phys_type=pt, apply_stack=False)
                out[pt] = d['cut']['physical'] * d['correct']['physical']
            d = m.defense_context([sid], pvp=True, apply_stack=False)
            for e in ELEMENTS[1:]:
                out[e] = d['cut'][e] * d['correct'][e]
            self._dfn[sid] = out
        return self._dfn[sid]

    def _uptime(self, sid, pts):
        k = (sid, tuple(pts))
        if k not in self._up:
            self._up[k] = self.m.planned_uptime(sid, pts, self.hits)
        return self._up[k]

    def _ratio(self, ctx, stats, points):
        key = (tuple(sorted((k, int(round(v))) for k, v in stats.items() if int(round(v)))),
               tuple(sorted((k, round(v, 6)) for k, v in points.items() if round(v, 6))))
        if not key[0] and not key[1]:
            return {}
        if key not in ctx['ratio_cache']:
            ctx['ratio_cache'][key] = ctx['ratio_fn'](dict(key[0]), dict(key[1]) or None)
        return ctx['ratio_cache'][key]

    def evaluate(self, ctx, keys):
        """The kit `keys` (use order) on this build: `evaluate_role` for each of the build's roles,
        weighted by them (`Buffs.corpus_kits`' role weights). A candidate a role may not use is
        left out of that role's kit. `live` is every key live in some role."""
        k = tuple(keys)
        if k in ctx['cache']:
            return ctx['cache'][k]
        out = {'live': set(), 'rows': [], 'recasts': {}}
        terms = ('score', 'offense', 'cut_hp', 'heal_hp', 'flask_hp', 'time_factor')
        acc = dict.fromkeys(terms, 0.0)
        for role, w in ctx['roles'].items():
            ks = [x for x in keys if x not in self.cands or role in self.cands[x]['roles']]
            e = self.evaluate_role(ctx, ks, role)
            for t in terms:
                acc[t] += w * e[t]
            out['live'] |= e['live']
            out['rows'] += [r for r in e['rows'] if r not in out['rows']]
            for s, n in e['recasts'].items():
                out['recasts'][s] = out['recasts'].get(s, 0.0) + w * n
        out.update(acc)
        ctx['cache'][k] = out
        return out

    def evaluate_role(self, ctx, keys, role='host'):
        """The kit `keys` (use order) on this build in one role: its terms and `combine`'s score."""
        k = (role, tuple(keys))
        if k in ctx['cache']:
            return ctx['cache'][k]
        m = self.m
        entries = [(sid, key) for key in keys for sid in self._rows(key, role)]
        live = m.stack(entries)
        limits = {key: self._limit(key, keys, ctx['swap_spells']) for key in keys}
        plans = m.source_plans(live, self.points, self.hits, limits)
        cost = [0.0] * len(self.points)
        for src, (_, cast, pts) in plans.items():
            for i, (f, n) in enumerate(pts):
                cost[i] += n * (cast or 0.0) / B.CAST_FPS / f
        # Instant heals: every use is drunk over the fight like a flask (`FLASK_ETA`, the flask
        # fights' share), each at most once per landed hit, and costs its clip unless its user
        # walks; a cure is live only when it ends a status one of them procs on its own user, and
        # is pressed once per such use.
        inst_hp, extra_live = 0.0, set()
        hits_at = B._pairs(tuple(self.points), self.hits)
        for key in keys:
            c = self.cands.get(key)
            if not c or 'instant' not in c:
                continue
            uses, cast = self._limit(key, keys)
            per = self.instant_hp(ctx, c, keys)
            if per <= 0:
                continue
            extra_live.add(key)
            inst_hp += uses * per * self.crimson_share / ctx['engagements'] if ctx['engagements'] else 0.0
            own = {st for st, _ in c['instant']['self_status']}
            frames = cast
            for k2 in keys:
                c2 = self.cands.get(k2)
                if c2 and c2.get('cures') in own:
                    extra_live.add(k2)
                    frames += self._limit(k2, keys)[1]
            for i, (f, h) in enumerate(hits_at):
                cost[i] += min(uses, max(1, h)) * frames / B.CAST_FPS / f
        tf = sum(max(0.0, 1.0 - c) for c in cost) / len(cost)
        pre = dict.fromkeys(ELEMENTS, 1.0)
        post = dict.fromkeys(ELEMENTS, 1.0)
        flat = dict.fromkeys(ELEMENTS, 0.0)
        stats = dict.fromkeys(B.STATS, 0.0)
        rate_points = dict.fromkeys(B.RATE_POINTS, 0.0)
        kept = dict.fromkeys(DAMAGE_KEYS, 1.0)
        heal_s, rows_used = 0.0, []
        for sid, src in live:
            u = self._uptime(sid, plans[src][2])
            if u <= 0:
                continue
            a = self._attack(sid, ctx['two'])
            if a['entries']:
                for e in ELEMENTS:
                    pre[e] *= 1.0 + u * (a['ar_rate'][e] - 1.0)
                    post[e] *= 1.0 + u * (a['pvp_rate'][e] * a['atk_rate'][e] - 1.0)
                    flat[e] += u * a['flat_add'][e]
                for s in stats:
                    stats[s] += u * a['stats'][s]
                for s in rate_points:
                    rate_points[s] += u * a['rate_points'][s]
            dk = self._defense(sid)
            for ty in DAMAGE_KEYS:
                kept[ty] *= 1.0 + u * (dk[ty] - 1.0)
            r = m.regen_row(sid)
            if r:
                heal_s += u * (r[0] + ctx['max_hp'] * r[1] / 100.0)
            rows_used.append((sid, src, round(u, 4)))
        ratio = self._ratio(ctx, stats, rate_points)
        offense = self.hit(ctx, pre, flat, post, ratio) / ctx['base_hit'] if ctx['base_hit'] else 1.0
        win = ctx['win']
        cut = (1.0 - win) * self.attackers * sum(ctx['incoming'][ty] * (1.0 - kept[ty]) for ty in DAMAGE_KEYS)
        heal = heal_s * self.ST.ENGAGEMENT_SECONDS + inst_hp
        flask = self.flask_hp(ctx, keys)
        out = {'score': combine(offense, cut, heal + flask, ctx['loss'], tf), 'offense': offense, 'cut_hp': cut,
               'heal_hp': heal, 'flask_hp': flask, 'time_factor': tf, 'live': {s for _, s in live} | extra_live,
               'rows': rows_used, 'recasts': {s: sum(n for _, n in p[2]) / len(p[2]) for s, p in plans.items()}}
        ctx['cache'][k] = out
        return out

    def flask_hp(self, ctx, keys):
        """HP per engagement of the Crimson flasks kit `keys` has beyond the fixed kit (the
        flasks, module docstring): 0 for a kit with no spell and no FP item, whose split is the
        fixed kit's."""
        if not any(k in self.cands and (self.cands[k]['kind'] == 'spell' or self.cands[k]['fp_rows'])
                   for k in keys):
            return 0.0
        if 'base_crimson' not in ctx:
            ctx['base_crimson'] = self.fp_plan(ctx, ctx['fixed'])['crimson']
        extra = self.fp_plan(ctx, keys)['crimson'] - ctx['base_crimson']
        return extra * self.crimson_hp(ctx)

    def crimson_hp(self, ctx):
        """HP per engagement of one crimson flask: its heal over the flask fights' engagements,
        times the share of the fight points that are flask fights (the flasks, module docstring)."""
        if not ctx['engagements']:
            return 0.0
        return B.FLASK_HEAL_HP * B.FLASK_ETA * self.crimson_share / ctx['engagements']

    def fp_plan(self, ctx, keys):
        """How kit `keys` pays for the FP the build spends over the fight: one FP bar, then the
        kit's FP items, then cerulean flasks (`flask_split`). Returns {'need', 'paid', 'crimson',
        'cerulean', 'sources'}, `sources` listing each spender and each FP item."""
        if 'skill_fp' not in ctx:
            ctx['skill_fp'] = self.skill_fp(ctx)
        cost, uses, buff_skill = ctx['skill_fp']
        need = cost * uses
        sources = [{'source': f"skill {ctx['skill']}", 'fp': cost, 'casts': round(uses, 2), 'buff': buff_skill}]
        restores = []
        for k in keys:
            c = self.cands.get(k)
            if c is None:
                continue
            if c['kind'] == 'spell':
                mp, casts = self.spell_fp(k)
                if k in ctx['swap_spells']:
                    casts = 1.0
                need += mp * casts
                sources.append({'source': k, 'fp': mp, 'casts': round(casts, 2)})
            elif c['fp_rows']:
                per, n = self.fp_per_use(c, ctx['fp_bar']), self._limit(k, keys)[0]
                restores.append(per * n)
                sources.append({'source': k, 'restores': round(per, 1), 'uses': n})
        paid = min(max(0.0, need - ctx['fp_bar']), sum(restores))
        crimson, cerulean = flask_split(need, ctx['fp_bar'], ctx['flask_total'], paid=paid)
        return {'need': need, 'paid': paid, 'crimson': crimson, 'cerulean': cerulean, 'sources': sources}

    def skill_fp(self, ctx):
        """(FP per use, uses wanted over the fight, buff skill?) of the focus weapon's skill."""
        sid = ctx['skill']
        if sid is None or sid < 0:
            return 0, 0.0, False
        if self._ash is None:
            A = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
            self._ash = (A, A.AshTables())
        A, t = self._ash
        if sid not in t.arts:
            return 0, 0.0, False
        cost = A.skill_fp(t, sid)
        roots = []
        try:
            prof = A.skill_profile(t, sid, ctx['wid'], ctx['level'])
            roots = [s['id'] for s in A.skill_buffs(t, prof) if s.get('effect') == 'buff']
        except (SystemExit, KeyError, StopIteration, TypeError, ValueError, FileNotFoundError, OSError):
            roots = []
        if roots:
            durs = [A._buff_duration(t, r) for r in roots]
            dur = -1.0 if -1.0 in durs else max(durs, default=0.0)
            pts = B.recast_points(dur, self.points, None, self.hits)
            return cost, sum(1 + n for _, n in pts) / len(pts), True
        return cost, B.hits_mean(self.hits), False

    def spell_fp(self, key):
        """(FP per cast, casts wanted over the fight) of a picked spell: one cast before contact and
        the recasts that keep it on the fight, with no FP limit (`Buffs.recast_points`)."""
        if key not in self._spell_fp:
            self._spell_fp[key] = self._spell_fp_uncached(key)
        return self._spell_fp[key]

    def _spell_fp_uncached(self, key):
        m = self.m
        rows = self._rows(key)
        one = any(m.one_hit(i) for i in rows)
        timed = [m.sp[i]['effectEndurance'] for i in rows
                 if not m.one_hit(i) and m.sp[i]['effectEndurance'] >= B.REFRESHED_ROW_S]
        pts = B.recast_points(max(timed, default=-1.0), self.points, None, self.hits, one_hit=one)
        mp = (self.magic.get(self.cands[key]['goods']) or {}).get('mp', 0)
        return mp, sum(1 + n for _, n in pts) / len(pts)

    # -- the quickbar's thrown pots and boluses --------------------------------------------------
    def _status_tables(self):
        if getattr(self, '_st', None) is None:
            self._st = self.ST.Tables()
        return self._st

    def _ash_tables(self):
        if self._ash is None:
            A = _mod('er_mechanics_ashes', 'er-mechanics-ashes.py')
            self._ash = (A, A.AshTables())
        return self._ash

    def own_statuses(self, ctx):
        """The statuses the focus weapon builds up itself (`er-mechanics-status.weapon_status`)."""
        if 'own_statuses' not in ctx:
            ws = self.ST.weapon_status(self._status_tables(), ctx['weapon'], ctx['affinity'], ctx['level'],
                                       ctx['stats'], ctx['two'])
            ctx['own_statuses'] = {s['status'] for s in ws['sources']}
        return ctx['own_statuses']

    def pot_hp(self, ctx, key):
        """{'hp', 'damage', 'status'} of one throw of pot `key` on the window's median defender.

        Damage: the flat attack of the bullet chain's most damaging node (`atkPhys` and the
        elemental `atk*` columns; a pot's AtkParam scales with no attribute), through the median
        defender's defense and absorption (`er-mechanics-defense.damage`, as `hit` takes the
        focus weapon's). The thrown bullet and its burst are both damaging nodes on some pots (Hefty
        Rock Pot: 370 each); only the larger is counted, whether one target takes both is not
        traced (`INFERRED`). Status: each on-hit row's build-up over the median resistance of its
        group times its proc HP, a damage-over-time proc cut to one battle, as
        `er-mechanics-ash-table.Table.buildup_hp` values a skill bullet's own status; a status
        the focus weapon already builds up is worth nothing here (the user's rule for Hoarfrost
        Stomp on a frost build, 2026-10-17, carried to pots)."""
        A, t = self._ash_tables()
        st = self._status_tables()
        hp = self.fight['median_hp']
        own = self.own_statuses(ctx)
        best, build = 0.0, {}

        def walk(bt):
            nonlocal best
            if not bt:
                return
            flat = {el: float(bt['flat'].get(el, 0)) for el in ELEMENTS}
            if any(flat.values()):
                phys = self.ATK.PHYS_TYPE_NAMES.get(bt['atk_attribute'], 'standard')
                best = max(best, sum(self.DEF.damage(flat, 100.0, self.defender, phys)['by_type'].values()))
            for sid in bt['on_hit_speffects']:
                rs = self.ST.row_status(st.sp.get(sid, {}))
                if rs:
                    build.setdefault(rs[0], (rs[1], sid))
            for c in bt['children'].values():
                walk(c)
        for b in self.throwables[key]['bullets']:
            walk(A.bullet_tree(t, b))
        status = {}
        for name, (b, sid) in build.items():
            if name in own:
                continue
            group = next(s[7] for s in self.ST.STATUSES if s[0] == name)
            resist = self.ST.quantiles([r['resist'][group] for r in self.st_rows], (0.5,)).get(0.5, 0.0)
            pe = self.ST.proc_effect(st, sid, hp)
            cut = 1.0
            if pe['ticks'] > 1 and pe['interval'] > 0 and pe['hp_total']:
                cut = min(pe['ticks'], int(self.ST.BATTLE_SECONDS / pe['interval'])) / pe['ticks']
            if resist > 0:
                status[name] = pe['hp_total'] * min(1.0, b / resist) * cut
        return {'hp': best + sum(status.values()), 'damage': best, 'status': status}

    def pots(self, ctx, kit=()):
        """The best pot of each pot group the kit holds no member of, by HP per throw
        (`pot_hp`), with the group's whole container count (`pot_capacity`): items of one group
        share that count (`_limit`), and one pot's throws are worth the same whichever slot of the
        count they take, so a second pot of the group only trades throws of the best one away."""
        taken = {self.cands[k]['pot_group'] for k in kit if k in self.cands} - {-1}
        best = {}
        for key, c in self.throwables.items():
            g = c['pot_group']
            if g in taken or not c['roles'] & set(ctx['roles']) or g not in self.pot_capacity:
                continue
            v = self.pot_hp(ctx, key)
            if v['hp'] > 0 and (g not in best or v['hp'] > best[g]['hp']):
                best[g] = dict(v, key=key, name=c['name'], group=g, capacity=self.pot_capacity[g])
        return sorted(best.values(), key=lambda p: -p['hp'] * p['capacity'])

    def bolus_fill(self, kit=()):
        """The boluses the window's opponents make worth carrying, most carried first: every
        status some right-hand weapon of the window's builds builds up (`weapon_status`, the
        weapon's own rows; greases, skills and spells are not read), ordered by the share of the
        builds that record quick items which carry its bolus (`er-mechanics-status.bolus_share`).
        A status no weapon of the window inflicts (death blight at RL 150: 0 of 1074 builds,
        2026-10-17) gets no bolus. One already in `kit` is left out."""
        st = self._status_tables()
        if not hasattr(self, '_inflict'):
            seen, cnt = {}, dict.fromkeys(self.ST.NAMES, 0)
            for r in self.st_rows:
                got = set()
                for s in r['right']:
                    k = (s.get('name'), self.ST.AFF_ALIASES.get(s.get('infusion'), s.get('infusion')))
                    if k not in seen:
                        try:
                            seen[k] = {x['status'] for x in self.ST.weapon_status(st, k[0], k[1])['sources']}
                        except (SystemExit, KeyError, TypeError, ValueError):
                            seen[k] = set()
                    got |= seen[k]
                for n in got:
                    cnt[n] += 1
            self._inflict = {n: v / len(self.st_rows) for n, v in cnt.items()} if self.st_rows else {}
        carry = self.ST.bolus_share(st, self.st_rows)
        names = {self.cands[k]['name'] for k in kit if k in self.cands}
        out = [{'name': st.bolus[s]['name'], 'status': s, 'carry': round(carry.get(s, 0.0), 4),
                'inflict': round(self._inflict.get(s, 0.0), 4)}
               for s in st.bolus if self._inflict.get(s, 0.0) > 0 and st.bolus[s]['name'] not in names]
        return sorted(out, key=lambda b: -b['carry'])


_POUCH = {}


def model(rl=150, window=10, regulation=None, fight=B.DEFAULT_FIGHT):
    """A cached `Pouch` for the RL window and fight model."""
    k = (rl, window, regulation, fight)
    if k not in _POUCH:
        _POUCH[k] = Pouch(rl, window, regulation, fight)
    return _POUCH[k]


def _base_fixed(base):
    """Tears and great rune of a planner build, as `Buffs.resolve` keys."""
    if not base:
        return []
    out = [f'tear:{x}' for x in (base.get('items') or {}).get('crystalTears') or [] if x]
    if base.get('greatRune'):
        out.append(f"great rune:{base['greatRune']}")
    return out


def roles_of(tags):
    """{role: weight} of a planner build's tags, as `Buffs.corpus_kits` spreads a kit: one count
    per tag `Buffs.ROLE_BY_TAG` names, host alone when none does."""
    roles = {}
    for t in tags or ():
        if t in B.ROLE_BY_TAG:
            roles[B.ROLE_BY_TAG[t]] = roles.get(B.ROLE_BY_TAG[t], 0) + 1
    n = sum(roles.values())
    return {r: v / n for r, v in roles.items()} if n else {'host': 1.0}


def pick(weapon, affinity, level, stats, plan_weapons=(), two_handed=False, **kw):
    """Tools, spells, grease and flask split for one build (module docstring).

    `stats` are attribute keys (`vig`, `end`, ...). Keywords: `rl` (default from the stats),
    `window` 10, `talismans`, `great_rune`, `armor` (four piece names; else the window's median
    absorption), `base` (a planner build: its tears and great rune are fixed in every kit, its flask
    positions and total are kept), `fixed` (resolve keys held in every kit, instead of the base's),
    `skill` (SwordArtsParam id; default the weapon's own), `tool_slots`, `memory_slots`,
    `flask_total`, `physick` (keep the physick flask; default when the base carries a tear),
    `win`, `regulation`, `pouch` (a `Pouch` to reuse), `exclude` (`exclude(name, kind)` true for a
    good or spell never picked, kind 'consumable' or 'spell': a duel's ladder bans), `buff_ban`
    (`buff_ban(name, kind)` true for a weapon buff, a grease or buff spell, the build's weapon setup
    may not carry: the ladder's grease and buff restrictions on the focus weapon's grip and its
    off hand, asked only of candidates whose rows buff the weapon), `assumed_tier` (the grease tier
    the stats were optimized with, `ASSUMED_TIER` by default, None when they assume no grease: the
    `grease_tier` verdict compares the pick against it)."""
    rl = kw.get('rl') or (sum(stats[k] for k in ('vig', 'mnd', 'end', 'str', 'dex', 'int', 'fth', 'arc')) - 79)
    p = kw.get('pouch') or model(rl, kw.get('window', 10), kw.get('regulation'), kw.get('fight', B.DEFAULT_FIGHT))
    base = kw.get('base')
    fixed = kw.get('fixed')
    if fixed is None:
        fixed = _base_fixed(base)
    # A duel takes its roles from the base's tags; an invasion is the invader's fight.
    roles = kw.get('roles') or (roles_of(base.get('tags') if base else ()) if p.fight_kind == 'duel'
                                else {B.FIGHT_KINDS[p.fight_kind]['role']: 1.0})
    items = base.get('items') if base else {}
    flasks_doc = (items or {}).get('flasks') or {}
    total = kw.get('flask_total') or flasks_doc.get('total') or FLASK_TOTAL
    ctx = p.context(weapon, affinity, level, stats, plan_weapons, two_handed, kw.get('talismans', ()),
                    kw.get('great_rune', base.get('greatRune') if base else None), kw.get('armor', ()), fixed,
                    kw.get('skill'), kw.get('memory_slots', MEMORY_SLOTS), kw.get('win', FIGHT_WIN_SHARE), roles,
                    total)
    p.m.fp_bar = ctx['fp_bar']
    physick = kw.get('physick')
    if physick is None:
        physick = bool(any((items or {}).get('crystalTears') or [])) if base else True
    keep = [p.flask_names['crimson'], p.flask_names['cerulean']] + ([p.flask_names['physick']] if physick else [])
    # Anything carried can be used from the inventory menu, so the tool slots order how quick an
    # item is to reach (`layout`) and do not cap the kit; `tool_slots` caps it only when passed.
    tool_slots = kw.get('tool_slots')
    tool_slots = None if tool_slots is None else tool_slots - len(keep)
    exclude = kw.get('exclude') or (lambda name, kind: False)
    buff_ban = kw.get('buff_ban') or (lambda name, kind: False)
    cands = [c for c in p.candidates(ctx) if not exclude(p.cands[c]['name'], p.cands[c]['kind'])
             and not (p.cands[c]['weapon_buff'] and buff_ban(p.cands[c]['name'], p.cands[c]['kind']))]
    slot_len = {k: (p.magic.get(p.cands[k]['goods']) or {}).get('slotLength', 1)
                for k in cands if p.cands[k]['kind'] == 'spell'}

    def fits(kit, c):
        if p.cands[c]['kind'] == 'spell':
            return sum(slot_len[k] for k in kit if k in slot_len) + slot_len[c] <= ctx['memory_slots']
        return tool_slots is None or sum(1 for k in kit if p.cands[k]['kind'] == 'consumable') + 1 <= tool_slots

    def ev(keys):
        return p.evaluate(ctx, keys)
    base_e = ev(list(fixed))
    scores = []
    for c in cands:
        e = ev(list(fixed) + [c])
        cd = p.cands[c]
        scores.append({'key': c, 'name': cd['name'], 'kind': cd['kind'], 'score': round(e['score'] / base_e['score'], 5),
                       'offense': round(e['offense'] / base_e['offense'], 5),
                       'cut_hp': round(e['cut_hp'] - base_e['cut_hp'], 2),
                       'heal_hp': round(e['heal_hp'] - base_e['heal_hp'], 2),
                       'flask_hp': round(e['flask_hp'] - base_e['flask_hp'], 2),
                       'time_factor': round(e['time_factor'] / base_e['time_factor'], 5),
                       'weapon_buff': cd['weapon_buff'], 'walks': cd['walks']})
    scores.sort(key=lambda s: -s['score'])
    kit, rounds, final = greedy(cands, ev, fixed, fits)
    tools = [k for k in kit if p.cands[k]['kind'] == 'consumable']
    spells = [k for k in kit if p.cands[k]['kind'] == 'spell']
    grease = None
    for sid, src, _ in final['rows']:
        r = p.m.sp[sid]
        if src in tools and r['spCategory'] in B.WEAPON_BUFF_CATS and p._attack(sid, ctx['two'])['entries']:
            grease = p.cands[src]['name']
    tiers = {n: t for t, by_el in p.OPT.GREASE_NAMES.items() for n in by_el.values()}
    assumed = kw.get('assumed_tier', ASSUMED_TIER)
    if not ctx['enhance']:
        verdict = {'tier': None, 'assumed': assumed, 'holds': None,
                   'why': f"{weapon} {affinity} has isEnhance 0: no grease can be used on it"}
    else:
        tier = tiers.get(grease)
        verdict = {'tier': tier, 'assumed': assumed, 'holds': tier == assumed,
                   'why': f"picked {grease or 'no grease'}" + (f" ({tier})" if tier else '')
                   + ('' if assumed else '; the stats assume no grease')}
    plan = p.fp_plan(ctx, list(fixed) + kit)
    fp_need, fp_parts = plan['need'], plan['sources']
    crimson, cerulean = plan['crimson'], plan['cerulean']
    if cerulean == 0:
        keep = [n for n in keep if n != p.flask_names['cerulean']]
    positions = {s['name']: s['equipIndex'] for s in ((items or {}).get('tools') or {}).get('slots') or []
                 if s.get('name') and s.get('equipIndex') is not None}
    names = [p.cands[k]['name'] for k in tools]
    # Uses per fight: the first use plus the recasts that keep it up.
    uses = {p.cands[k]['name']: 1.0 + final['recasts'].get(k, 0.0) for k in tools}
    # An instant heal is used once per stock item, and a cure as often as the heal it follows.
    for k in tools:
        c = p.cands[k]
        if 'instant' in c:
            n = p._limit(k, kit)[0]
            uses[c['name']] = float(n)
            for k2 in tools:
                if p.cands[k2].get('cures') in {s for s, _ in c['instant']['self_status']}:
                    uses[p.cands[k2]['name']] = max(uses.get(p.cands[k2]['name'], 0.0), float(n))
    slow = {p.cands[k]['name'] for k in tools
            if p.cands[k]['fp_rows'] and set(p.cands[k]['rows']) <= set(p.cands[k]['fp_rows'])}
    # A buff the build holds one of is used once, before contact, so it is used from the
    # inventory menu as a swap-cast spell is and takes no slot (user, 2026-10-17: Raw Meat
    # Dumpling equipped in place of Gourmet Scorpion Stew, `maxNum` 1).
    menu = [p.cands[k]['name'] for k in tools if 'instant' not in p.cands[k] and not p.cands[k].get('cures')
            and p._limit(k, kit)[0] <= 1]
    names = [n for n in names if n not in menu]
    pots = [x for x in p.pots(ctx, kit) if not exclude(x['name'], 'consumable')] if kw.get('pots', True) else []
    boluses = ([x for x in p.bolus_fill(kit) if not exclude(x['name'], 'consumable')]
               if kw.get('boluses', True) else [])
    extra = [x['name'] for x in pots] + [x['name'] for x in boluses]
    layout_rows = layout(names, keep, positions, uses=uses, slow=slow, extra=extra)
    layout_rows += [{'name': n, 'equipIndex': None, 'order': len(layout_rows) + i} for i, n in enumerate(menu)]
    placed = {s['name'] for s in layout_rows if s['equipIndex'] is not None}
    pots = [dict(x, slotted=x['name'] in placed) for x in pots]
    boluses = [dict(x, slotted=x['name'] in placed) for x in boluses]
    skipped = []
    for cls, ns in sorted(p.unscored.items(), key=lambda kv: -len(kv[1])):
        skipped.append({'category': cls, 'count': len(ns), 'why': SKIP_REASON[cls], 'examples': ns[:5]})
    if not ctx['schools'] and not ctx['swap']:
        skipped.append({'category': 'spells', 'count': sum(1 for c in p.cands.values() if c['kind'] == 'spell'),
                        'why': 'no held weapon casts a school and no swap catalyst fits the stats', 'examples': []})
    swaps = {p.cands[k]['name']: ctx['swap_spells'][k] for k in spells if k in ctx['swap_spells']}
    rule = ("Every good and spell usable in a multiplayer session that this build can use is scored by "
            "er-mechanics-ashes.buff_option's offense x (N + HP saved) / N x recast time factor, and the "
            "pouch is filled greedily by that score until nothing adds. The FP the kit spends is paid by "
            "the FP bar, then the kit's FP items, then cerulean flasks, and each crimson flask that "
            "leaves counts as its heal over the fight's engagements; an item whose use clip lets its "
            "user walk costs no recast time.")
    return {'tools': layout_rows, 'spells': [p.cands[k]['name'] for k in spells], 'swaps': swaps,
            'pots': [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in x.items() if k != 'status'}
                     | {'status': {s: round(h, 1) for s, h in x['status'].items()}} for x in pots],
            'boluses': boluses,
            'swap_catalysts': sorted({s['catalyst'] for s in swaps.values() if s.get('catalyst')}),
            'swap_talismans': sorted({s['talisman'] for s in swaps.values() if s.get('talisman')}),
            'grease': grease,
            'grease_tier': verdict, 'flasks': {'level': flasks_doc.get('level', 12), 'total': total,
                                                'crimson': crimson, 'cerulean': cerulean},
            'flask_rule': {'fp_need': round(fp_need, 1), 'fp_bar': ctx['fp_bar'],
                           'per_cerulean': min(CERULEAN_FP, ctx['fp_bar']) if ctx['fp_bar'] > 0 else CERULEAN_FP,
                           'fp_items_paid': round(plan['paid'], 1), 'sources': fp_parts,
                           'heal_per_crimson': round(p.crimson_hp(ctx), 2), 'engagements': round(ctx['engagements'], 2),
                           'crimson_share': round(p.crimson_share, 3)},
            'scores': scores, 'picked': [dict(r, name=p.cands[r['key']]['name'], gain=round(r['gain'], 5))
                                         for r in rounds],
            'kit': {'score': round(final['score'] / base_e['score'], 5),
                    'offense': round(final['offense'] / base_e['offense'], 5),
                    'cut_hp': round(final['cut_hp'], 1), 'heal_hp': round(final['heal_hp'], 1),
                    'flask_hp': round(final['flask_hp'], 1),
                    'time_factor': round(final['time_factor'], 5), 'recasts': {k: round(v, 2) for k, v in
                                                                              final['recasts'].items()}},
            'build': {'base_hit': round(ctx['base_hit'], 1), 'incoming_hit': round(sum(ctx['incoming'].values()), 1),
                      'fp_bar': ctx['fp_bar'], 'max_hp': ctx['max_hp'], 'enhance': ctx['enhance'], 'roles': roles,
                      'schools': sorted(ctx['schools']), 'fixed': list(fixed), 'fight': p.fight,
                      'candidates': len(cands)},
            'rule': rule, 'skipped': skipped,
            'planner': {'tools': {'slots': layout_rows}, 'spells': {'slots': [
                {'name': p.cands[k]['name'], 'order': i} for i, k in enumerate(spells)]},
                'flasks': {'level': flasks_doc.get('level', 12), 'total': total, 'crimson': crimson,
                           'cerulean': cerulean}}}


# -- CLI -----------------------------------------------------------------------------------------
#: `vit` is the planner's spelling of Endurance, still taken for `end`.
STAT_ALIAS = {'vit': 'end'}


def parse_stats(s):
    out = {}
    for part in s.split(','):
        k, v = part.split('=')
        out[STAT_ALIAS.get(k.strip().lower(), k.strip().lower())] = int(v)
    return out


def show(got, top=25):
    b = got['build']
    print(f"base hit {b['base_hit']}, incoming hit {b['incoming_hit']} (after absorption), FP bar {b['fp_bar']:.0f}, "
          f"max HP {b['max_hp']:.0f}; {b['candidates']} candidates; roles {b['roles']}; "
          f"fixed {', '.join(b['fixed']) or 'none'}")
    print(f"fight: {b['fight']['points']} points, mean landed hits {b['fight']['hits_mean']}")
    print(f"\n{'score':>7} {'offense':>8} {'cut HP':>7} {'heal':>6} {'flask':>6} {'time':>7}  "
          f"candidate (alone, over the fixed kit)")
    for s in got['scores'][:top]:
        print(f"{s['score']:7.4f} {s['offense']:8.4f} {s['cut_hp']:7.1f} {s['heal_hp']:6.1f} {s['flask_hp']:6.1f} "
              f"{s['time_factor']:7.4f}  {s['kind']}: {s['name']}{' (weapon buff)' if s['weapon_buff'] else ''}"
              f"{' (walks)' if s['walks'] else ''}")
    print('\npicked, in order (marginal gain):')
    for r in got['picked']:
        print(f"  {100 * r['gain']:+6.2f}%  {r['name']}" + (f"  (drops {', '.join(r['dropped'])})" if r['dropped'] else ''))
    k = got['kit']
    print(f"kit: score x{k['score']}, offense x{k['offense']}, cut {k['cut_hp']} HP, heal {k['heal_hp']} HP, "
          f"flasks {k['flask_hp']} HP, time factor {k['time_factor']}")
    print('\ntools:')
    for s in got['tools']:
        print(f"  {'--' if s['equipIndex'] is None else format(s['equipIndex'], '2d')}  {s['name']}"
              + ('  (carried, used from the inventory menu)' if s['equipIndex'] is None else ''))
    print('pots (best of each pot group, the group\'s whole count):')
    for x in got.get('pots', ()):
        print(f"  {x['name']:<28} group {x['group']} x{x['capacity']:<3} {x['hp']:6.1f} HP a throw "
              f"(damage {x['damage']}, status {x['status'] or '-'}){'' if x['slotted'] else '  (no slot left)'}")
    print('boluses (statuses the window inflicts, most carried first):')
    for x in got.get('boluses', ()):
        print(f"  {x['name']:<22} {x['status']:<12} carried {100 * x['carry']:5.1f}%  inflicted "
              f"{100 * x['inflict']:5.1f}%{'' if x['slotted'] else '  (no slot left)'}")
    print(f"spells: {', '.join(got['spells']) or 'none'}")
    g = got['grease_tier']
    print(f"grease: {got['grease'] or 'none'}; tier assumption {g['assumed']}: "
          f"{'n/a' if g['holds'] is None else ('holds' if g['holds'] else 'does not hold')} ({g['why']})")
    f, fr = got['flasks'], got['flask_rule']
    parts = '; '.join('%s %s FP x %s' % (x['source'], x['fp'], x['casts']) if 'fp' in x else
                      '%s restores %s FP x %s uses' % (x['source'], x['restores'], x['uses']) for x in fr['sources'])
    print(f"flasks: {f['crimson']} crimson / {f['cerulean']} cerulean of {f['total']}: FP need {fr['fp_need']} over "
          f"the fight against a {fr['fp_bar']:.0f} bar, {fr['fp_items_paid']} paid by FP items and "
          f"{fr['per_cerulean']:.0f} per cerulean ({parts}); a crimson is {fr['heal_per_crimson']} HP per engagement "
          f"({fr['engagements']} engagements, drunk in {fr['crimson_share']} of the fights)")
    print('\nleft out:')
    for s in got['skipped']:
        print(f"  {s['count']:4d}  {s['category']}: {s['why']}")


# -- selftest ------------------------------------------------------------------------------------
def selftest():
    fails, n = [], [0]

    def check(label, got, want, tol=1e-6):
        n[0] += 1
        ok = abs(got - want) <= tol if isinstance(want, float) else got == want
        if not ok:
            fails.append(f'{label}: got {got!r}, want {want!r}')

    # The checker can fail.
    before = len(fails)
    check('negative control', 1.0, 2.0)
    if len(fails) == before:
        print('selftest FAILED: the checker accepted a wrong value')
        return 1
    del fails[:]
    n[0] = 0
    # combine: buff_option's shape, over the HP lost per engagement.
    check('combine: offense x (L + cut + heal) / L x time', combine(1.1, 50.0, 10.0, 100.0, 0.9),
          1.1 * (100.0 + 60.0) / 100.0 * 0.9)
    check('combine: no kit is 1', combine(1.0, 0.0, 0.0, 300.0, 1.0), 1.0)
    check('combine: a cut of half the HP lost is x1.5', combine(1.0, 75.0, 0.0, 150.0, 1.0), 1.5)
    check('combine: a cut share is worth the same at five attackers, a flat heal a fifth',
          (combine(1.0, 5 * 15.0, 0.0, 5 * 100.0, 1.0), combine(1.0, 0.0, 10.0, 5 * 100.0, 1.0)),
          (combine(1.0, 15.0, 0.0, 100.0, 1.0), combine(1.0, 0.0, 2.0, 100.0, 1.0)))
    # flask_split.
    check('flasks: 396 FP over a 72 bar needs 5, each drink refilling 72 of its 220',
          flask_split(396.0, 72.0, 14), (9, 5))
    check('flasks: a bar over 220 takes the full 220 a drink', flask_split(250.0 + 440.0, 250.0, 14), (12, 2))
    check('flasks: no FP spend, no cerulean', flask_split(0.0, 72.0, 14), (14, 0))
    check('flasks: a bar that covers it, no cerulean', flask_split(70.0, 72.0, 14), (14, 0))
    check('flasks: capped at the total', flask_split(1e6, 72.0, 14), (0, 14))
    check('flasks: exactly one flask short', flask_split(72.0 + 72.0, 72.0, 14), (13, 1))
    check('flasks: FP items pay before the flasks, 646 over 72 with 240 paid needs 5',
          (flask_split(646.0, 72.0, 14), flask_split(646.0, 72.0, 14, paid=240.0)), ((6, 8), (9, 5)))
    check('flasks: FP items that cover the need leave no cerulean', flask_split(646.0, 72.0, 14, paid=574.0), (14, 0))
    # layout.
    lay = layout(['A', 'B'], ['Crimson', 'Cerulean'], {'Crimson': 12, 'Cerulean': 11, 'Old': 0}, uses={'A': 1, 'B': 5})
    check('layout: flasks keep the base positions, the most used pick takes the first free pouch slot',
          [(s['name'], s['equipIndex']) for s in lay], [('B', 10), ('Cerulean', 11), ('Crimson', 12), ('A', 13)])
    check('layout: order follows equipIndex', [s['order'] for s in lay], [0, 1, 2, 3])
    lay = layout([f'x{i}' for i in range(20)], ['C', 'E', 'P'], {})
    check('layout: no base, flasks take the first pouch positions', [s['equipIndex'] for s in lay if s['name'] in 'CEP'],
          [10, 11, 12])
    check('layout: the pouch fills before the quickbar',
          [s['name'] for s in lay if s['equipIndex'] is not None and s['equipIndex'] >= 10][3:], ['x0', 'x1', 'x2'])
    check('layout: past 16 positions a pick is carried with no slot, not dropped',
          (len(lay), sum(1 for s in lay if s['equipIndex'] is None)), (23, 7))
    lay = layout(['Shard', 'Heal'], ['C'], {}, uses={'Shard': 10, 'Heal': 3}, slow={'Shard'},
                 extra=['Pot'] + [f'b{i}' for i in range(12)])
    pos = {s['name']: s['equipIndex'] for s in lay}
    check('layout: a slow pick takes the quickbar even when used most, the first extra a free pouch slot',
          (pos['Shard'], pos['Heal'], pos['Pot']), (0, 11, 12))
    check('layout: extras past the quickbar are left out', (pos.get('b8'), 'b9' in pos), (9, False))
    # greedy over a synthetic evaluator: A and B replace each other (the later one stays), A is
    # better; C adds; D costs more time than it gives.
    value = {'A': 0.20, 'B': 0.10, 'C': 0.05, 'D': -0.01}

    def ev(keys):
        live = set()
        for k in keys:
            if k in ('A', 'B'):
                live -= {'A', 'B'}
            live.add(k)
        s = 1.0
        for k in live:
            s *= 1.0 + value[k]
        return {'score': s, 'live': live}
    kit, rounds, final = greedy(['B', 'D', 'C', 'A'], ev)
    check('greedy: the better of an exclusive pair, then the additive one, never the losing one',
          (sorted(kit), [r['key'] for r in rounds]), (['A', 'C'], ['A', 'C']))
    check('greedy: final score', round(final['score'], 6), round(1.2 * 1.05, 6))
    kit, _, _ = greedy(['B', 'C', 'A'], ev, fits=lambda k, c: len(k) < 1)
    check('greedy: a full kit stops', kit, ['A'])
    check('greedy: nothing that helps leaves the kit empty', greedy(['D'], ev)[0], [])

    def ev2(keys):
        # A and X replace each other, the later one staying; X is worth 0.30 beside Y, 0.05 without.
        live = set()
        for k in keys:
            if k in ('A', 'X'):
                live -= {'A', 'X'}
            live.add(k)
        s = (1.10 if 'A' in live else 1.0) * (1.02 if 'Y' in live else 1.0)
        if 'X' in live:
            s *= 1.30 if 'Y' in live else 1.05
        return {'score': s, 'live': live}
    kit, rounds, _ = greedy(['A', 'X', 'Y'], ev2)
    check('greedy: a member a later pick replaces is dropped and reported',
          (kit, [r['key'] for r in rounds], rounds[-1]['dropped'] if rounds else None),
          (['Y', 'X'], ['A', 'Y', 'X'], ['A']))
    # Real regulation.
    p = model(150, 10)
    inv = B.FIGHT_KINDS['invasion']
    check('the default fight is the invasion: its engaged attackers, 6.5 hosts, 10 s to 1 h',
          (p.fight['kind'], p.fight['attackers'], p.fight['targets'], p.fight['seconds']),
          ('invasion', inv['attackers'], 6.5, [10, 3600]))
    try:
        _real(p, check)
    except Exception as ex:  # a missing row is a failure, not a crash
        fails.append(f'real regulation: {type(ex).__name__}: {ex}')
    if fails:
        print('selftest FAILED:')
        for f in fails:
            print('  ' + f)
        return 1
    print(f'selftest ok ({n[0]} checks)')
    return 0


def _real(p, check):
    """Checks against the installed 1.17.1 regulation and the corpus window."""
    check('flask names from the regulation', (p.flask_names['crimson'], p.flask_names['cerulean'],
                                              p.flask_names['physick']),
          ('Flask of Crimson Tears', 'Flask of Cerulean Tears', 'Flask of Wondrous Physick'))
    check('a bolus is a candidate that cures its status',
          (p.cands['consumable:Stanching Boluses'].get('cures'), p.cands['consumable:Neutralizing Boluses'].get('cures')),
          ('bleed', 'poison'))
    dump = p.cands['consumable:Raw Meat Dumpling']
    check('Raw Meat Dumpling: heals 50% of max HP at once and procs poison on its user (501236)',
          (dump['instant']['rate'], dump['instant']['point'], dump['instant']['self_status']),
          (50.0, 0.0, [('poison', 501236)]))
    ctx0 = {'max_hp': 2000.0}
    raw = p.instant_hp(ctx0, dump, [dump['key']])
    cured = p.instant_hp(ctx0, dump, [dump['key'], 'consumable:Neutralizing Boluses'])
    check('Raw Meat Dumpling: the poison costs less when Neutralizing Boluses cure it, never more than the heal',
          (cured > raw, 0 < raw < 1000.0, cured <= 1000.0), (True, True, True))
    check('pot capacity: Cracked Pot 20, Perfume Bottle 10, Ritual Pot 10, Hefty Cracked Pot 10',
          {g: p.pot_capacity[g] for g in sorted(p.pot_capacity)}, {1: 20, 2: 10, 3: 10, 4: 10})
    check('Freezing Pot and Fire Pot are pot throwables',
          ('consumable:Freezing Pot' in p.throwables, 'consumable:Fire Pot' in p.throwables), (True, True))
    boluses = p.bolus_fill()
    check('bolus fill: no death blight (no window weapon inflicts it), bleed first',
          ('death_blight' in {b['status'] for b in boluses}, boluses[0]['status'] if boluses else None),
          (False, 'bleed'))
    check('a throwing knife is a projectile', 'Throwing Dagger' in p.unscored.get('projectile', []), True)
    check('a whistle is not usable in multiplayer',
          'Spectral Steed Whistle' in p.unscored.get('not usable in multiplayer', []), True)
    check('the Phantom Great Rune blesses the world\'s enemies, not the user',
          ('Phantom Great Rune' in p.unscored.get('buffs others', []), 'consumable:Phantom Great Rune' in p.cands),
          (True, False))
    check('the dragon-form hearts are body transforms, not candidates',
          ({'Rock Heart', 'Priestess Heart'} <= set(p.unscored.get('body transform', [])),
           any(c['name'] in ('Rock Heart', 'Priestess Heart') for c in p.cands.values())), (True, False))
    check('Boiled Crab is a candidate', 'consumable:Boiled Crab' in p.cands, True)
    check('a grease needs isEnhance', p.cands['consumable:Drawstring Royal Magic Grease']['needs_enhance'], True)
    st = {'vig': 58, 'mnd': 8, 'end': 38, 'str': 20, 'dex': 22, 'int': 7, 'fth': 8, 'arc': 68}
    ctx = p.context('Poleblade of the Bud', 'Standard', 10, st)
    p.m.fp_bar = ctx['fp_bar']
    check('Poleblade of the Bud takes no grease (isEnhance 0)', ctx['enhance'], False)
    check('no catalyst, no school', ctx['schools'], set())
    cands = p.candidates(ctx)
    check('no candidate needs a buffable weapon', any(p.cands[k]['needs_enhance'] for k in cands), False)
    check('no catalyst held: incantations through the lightest seal it meets', ctx['swap'].get('incantation'),
          'Frenzied Flame Seal')
    bv = 'spell:Bestial Vitality'
    check('Bestial Vitality (Faith 12) at Faith 8: the seal and the least +Faith talisman swapped in',
          (bv in cands, ctx['swap_spells'].get(bv)), (True, {'catalyst': 'Frenzied Flame Seal',
                                                            'talisman': 'Two Fingers Heirloom'}))
    check('a swap-cast spell is cast once, before contact', p._limit(bv, [bv], ctx['swap_spells'])[0], 1)
    check('Golden Vow (Faith 25) is 17 short: no one talisman covers it', 'spell:Golden Vow' in cands, False)
    e = p.evaluate(ctx, ['consumable:Boiled Crab', 'consumable:Exalted Flesh'])
    check('Exalted Flesh after Boiled Crab: one 151 entry, the later one', e['live'], {'consumable:Exalted Flesh'})
    e0 = p.evaluate(ctx, [])
    check('the empty kit scores 1 (combine of nothing)', round(e0['score'], 9), 1.0)
    ex = p.evaluate(ctx, ['consumable:Exalted Flesh'])
    check('Exalted Flesh raises the hit, and its eating clip walks: no recast time',
          (ex['offense'] > 1.0, ex['time_factor'], ex['cut_hp']), (True, 1.0, 0.0))
    up = 'consumable:Uplifting Aromatic'
    ua = p.evaluate(ctx, [up])
    check('Uplifting Aromatic stands still: it keeps the item cast charge and costs recast time',
          (p._limit(up, [up])[1], ua['time_factor'] < 1.0), (p.m.item_cast_frames, True))
    # Standing still: JumpTable 89 in the use clip, read from the behavior graph and a00.tae.
    check('use clips: Warming Stone (7) a000_050100 and the aromatic (91) stand, Starlight Shards (65) '
          'a000_050520, Lulling Branch (15) a000_050800, bolus (0), eating (26) walk',
          tuple(p.use_clip.get(u) for u in (7, 91, 65, 15, 0, 26)),
          ((50100, False), (50620, False), (50520, True), (50800, True), (50000, True), (50180, True)))
    # FP items, from the regulation.
    ss, lb = 'consumable:Starlight Shards', 'consumable:Lulling Branch'
    check('Starlight Shards and Lulling Branch are candidates', (ss in p.cands, lb in p.cands, ss in cands),
          (True, True, True))
    check('Starlight Shards: SpEffect 501290, 2 FP every 1 s for 60 s, 120 a use, 10 uses',
          ([(i, p.fp_row(i)) for i in p.cands[ss]['fp_rows']], p.fp_per_use(p.cands[ss], ctx['fp_bar']),
           p._limit(ss, [ss])[0]), ([(501290, (2.0, 0.0, 1.0, 60.0))], 120.0, 10))
    check('Lulling Branch: 5 FP every 2 s for 60 s, 150 a use',
          p.fp_per_use(p.cands[lb], ctx['fp_bar']), 150.0)
    check('the flask is not an FP item (it writes its own columns)',
          any(p.fp_row(i) for i in (501062,)), False)
    sh = p.evaluate(ctx, [ss])
    check('Starlight Shards walks: time factor 1, no hit, no cut', (sh['time_factor'], round(sh['offense'], 6),
                                                                    sh['cut_hp']), (1.0, 1.0, 0.0))
    without, with_ss = p.fp_plan(ctx, []), p.fp_plan(ctx, [ss])
    check('a big FP need (the Poleblade skill over the invasion) takes fewer cerulean with Starlight Shards',
          (without['need'] > ctx['fp_bar'] + CERULEAN_FP, with_ss['cerulean'] < without['cerulean'],
           with_ss['crimson'] - without['crimson'] == without['cerulean'] - with_ss['cerulean']), (True, True, True))
    freed = with_ss['crimson'] - without['crimson']
    check('the invasion drinks crimson in every fight, over its landed hits / win share engagements',
          (p.crimson_share, round(ctx['engagements'], 6)),
          (1.0, round(B.hits_mean(p.hits) / FIGHT_WIN_SHARE, 6)))
    check('each freed crimson is its heal over the fight engagements, and it scores',
          (round(sh['flask_hp'], 6), sh['score'] > 1.0),
          (round(freed * B.FLASK_HEAL_HP * B.FLASK_ETA / ctx['engagements'], 6), True))
    idle = {k: v for k, v in ctx.items() if k != 'base_crimson'}
    idle['skill_fp'] = (0, 0.0, False)
    check('an FP item with no FP need frees nothing', p.flask_hp(idle, list(ctx['fixed']) + [ss]), 0.0)
    crab = p.evaluate(ctx, ['consumable:Boiled Crab'])
    check('Boiled Crab saves HP and does not raise the hit', (crab['cut_hp'] > 0, round(crab['offense'], 6)),
          (True, 1.0))
    up, bb = 'consumable:Uplifting Aromatic', 'consumable:Bloodboil Aromatic'
    check('two aromatics share the perfume stock (pot group): 10 alone, 5 each together',
          (p._limit(up, [up])[0], p._limit(up, [up, bb])[0], p._limit(bb, [up, bb])[0]), (10, 5, 5))
    # Role gating, on a candidate made invader-only for the check (the regulation's one invader-only
    # consumable, the Phantom Great Rune, is left out as `buffs others`).
    ck = 'consumable:Boiled Crab'
    saved = p.cands[ck]['roles']
    p.cands[ck]['roles'] = {'invader'}
    ci = p.context('Poleblade of the Bud', 'Standard', 10, st, roles={'invader': 1.0})
    check('an invader-only item is an invader candidate and not a host one',
          (ck in p.candidates(ci), ck in p.candidates(ctx)), (True, False))
    p.cands[ck]['roles'] = saved
    check('roles from tags: one count per role tag', roles_of(['Invasions', 'Duels', 'Dexterity']),
          {'invader': 0.5, 'host': 0.5})
    check('roles from tags: host when none names one', roles_of(['Dexterity']), {'host': 1.0})
    # Greasable: Heavy Greatsword at a STR spread; a Fire one takes none.
    gs = {'vig': 58, 'mnd': 10, 'end': 40, 'str': 60, 'dex': 12, 'int': 9, 'fth': 9, 'arc': 9}
    cg = p.context('Greatsword', 'Heavy', 25, gs)
    check('Heavy Greatsword is buffable', cg['enhance'], True)
    cf = p.context('Greatsword', 'Fire', 25, gs)
    greases = [k for k in p.candidates(cf) if p.cands[k]['weapon_buff']]
    check('Fire Greatsword: no weapon-buff candidate', greases, [])
    one = p.evaluate(cg, ['consumable:Drawstring Golden Grease'])
    check('a grease raises the hit through its flat add', one['offense'] > 1.0, True)
    two = p.evaluate(cg, ['consumable:Drawstring Golden Grease', 'consumable:Golden Grease'])
    check('two greases: one right-hand entry, the later one', two['live'], {'consumable:Golden Grease'})
    # A duel (`er-weapon-build.py --fight duel`) passes the ladder's `tool_ban` as `exclude`: no
    # grease it leaves is a scored candidate, so no banned grease is assumed (the 2026-10-19 upload
    # refused Drawstring Dragonbolt Grease).
    ladder = _mod('er_builds_ladder', 'er-builds-ladder.py')
    left = [p.cands[k]['name'] for k in p.candidates(cg)
            if p.cands[k]['weapon_buff'] and p.cands[k]['kind'] == 'consumable' and not ladder.tool_ban(p.cands[k]['name'])]
    check('duel: no grease candidate survives the ladder bans', left, [])
    duel = pick('Greatsword', 'Heavy', 25, gs, pouch=p, pots=False, boluses=False, assumed_tier=None,
                exclude=lambda n, kind: (ladder.spell_ban if kind == 'spell' else ladder.tool_ban)(n))
    check('duel pick: no grease, and the verdict assumes none', (duel['grease'], duel['grease_tier']['assumed'],
                                                                 duel['grease_tier']['holds']), (None, None, True))
    check('duel pick: no banned tool', [t['name'] for t in duel['tools'] if ladder.tool_ban(t['name'])
                                        and 'Crimson' not in t['name']], [])
    # A seal opens incantations; requirements are read from the regulation.
    cs = p.context('Greatsword', 'Heavy', 25, dict(gs, fth=25), plan_weapons=['Finger Seal'])
    spells = [k for k in p.candidates(cs) if p.cands[k]['kind'] == 'spell']
    check('a held seal opens incantations only',
          {p.cands[k]['school'] for k in spells if not (cs['swap_spells'].get(k) or {}).get('catalyst')},
          {'incantation'})
    lift = {n: adds['fth'] for _, n, adds in p.stat_talismans}
    check('every castable incantation meets its faith requirement, with its swap talisman if any',
          all(p.magic[p.cands[k]['goods']]['requirementFaith']
              <= 25 + lift.get((cs['swap_spells'].get(k) or {}).get('talisman'), 0) for k in spells), True)
    check('Golden Vow (FTH 25) is castable at FTH 25', 'spell:Golden Vow' in spells, True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--affinity', default='Standard')
    ap.add_argument('--level', type=int, help='weapon level (default its max)')
    ap.add_argument('--stats', help='vig,mnd,end,str,dex,int,fth,arc (vit is taken for end)')
    ap.add_argument('--plan-weapons', default='', help='comma list of the other weapons the build holds')
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--talismans', default='')
    ap.add_argument('--armor', default='', help='comma list of four armor pieces (default: median absorption)')
    ap.add_argument('--base', help='planner build JSON: its tears, great rune, flask positions and total')
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--fight', choices=sorted(B.FIGHT_KINDS), default=B.DEFAULT_FIGHT,
                    help='fight model (er-mechanics-buffs FIGHT_KINDS)')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon or not a.stats:
        ap.error('name a weapon and --stats')
    base = json.load(open(os.path.expanduser(a.base), encoding='utf-8')) if a.base else None
    split = lambda s: [x.strip() for x in s.split(',') if x.strip()]
    got = pick(a.weapon, a.affinity, a.level, parse_stats(a.stats), split(a.plan_weapons), a.two_handed,
               talismans=split(a.talismans), armor=split(a.armor), base=base, window=a.window, fight=a.fight)
    if a.json:
        print(json.dumps(got, indent=1, default=sorted))
    else:
        show(got, a.top)
    return 0


if __name__ == '__main__':
    sys.exit(main())
