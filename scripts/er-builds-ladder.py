#!/usr/bin/env python3
"""Rank public RL 125 planner builds by how many duel/ladder rules they break, by name.

    python3 scripts/er-builds-scrape.py --rl 125     # refresh the mirror first
    python3 scripts/er-builds-ladder.py              # builds updated since 2026-06-01
    python3 scripts/er-builds-ladder.py --since 2026-09-01 --top 50

The ban lists are read out of `crates/er-duel-rules-core/src/tables.rs` (generated from the game by
`er-duel-rules-gen.py`) plus the hand-named rules in `rules.rs`, matched against the names the
planner stores. Rules that need params or play to judge -- shield guard above 90%, light roll,
two-hand-only bans, buff restrictions per setup, the two-blue-flasks-per-round limit (a count of
drinks, not the planner's flask split) -- are not checked, so zero here means no
name-level break, not a certified legal build.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / 'crates/er-duel-rules-core/src/tables.rs').read_text()
MIRROR = Path.home() / '.cache/er-build-planner/builds-rl125.jsonl'


def table(name):
    i = SRC.index('pub const ' + name)
    j = SRC.index('\n];', i)
    body = SRC[i:j]
    return set(re.findall(r'name: "((?:[^"\\]|\\.)*)"', body)) | set(re.findall(r'\(\s*"([^"]+)"', body))


#: Flasks of Cerulean Tears a duelist may drink per round: `rules.rs` `CERULEAN_FLASK_LIMIT`
#: ("Blue flasks limited to 2"), read rather than copied. Not checked against builds (module
#: docstring); `er-mechanics-ash-whiff` sizes a defender's FP for his movement skills with it.
CERULEAN_FLASKS = int(re.search(r'pub const CERULEAN_FLASK_LIMIT: u32 = (\d+);',
                                (ROOT / 'crates/er-duel-rules-core/src/rules.rs').read_text()).group(1))
ARMOR = table('BANNED_ARMOR')
TEARS = table('BANNED_TEARS')
BUFFS = table('BODY_BUFFS')
HEALS = table('HEALS')
REGEN = table('REGEN_EQUIPMENT')
SPELLS = table('SPELL_BULLETS')
PARRY = table('PARRY_SKILLS')
# rules.rs BANNED_WEAPONS, plus the thrusting shield class.
WEAPONS = {'Smithscript Cirque', 'Claws of Night', 'Smithscript Shield', 'Spread Crossbow',
           'Smithscript Dagger', 'Milady', 'Dueling Shield', 'Carian Thrusting Shield'}
TALISMANS = {'Crucible Feather Talisman', 'Talisman of All Crucibles',
             'Fine Crucible Feather Talisman', 'Red-Feathered Branchsword',
             'Two-Handed Sword Talisman'}
ASH_TALISMANS = {'Warrior Jar Shard', 'Shard of Alexander', "Rellana's Cameo", 'Godfrey Icon'}
# rules.rs BANNED_SKILLS, plus the generated parries.
ASHES = {"Bloodhound's Step", 'Quickstep', 'Raptor of the Mists', 'Endure', 'Oath of Vengeance',
         "Royal Knight's Resolve", 'Determination', 'Flaming Strike', 'Storm Stomp',
         'Storm Assault', 'Stormcaller', 'Swift Slash', 'Blind Spot', 'Needle Piercer',
         'Red Bear Hunt', 'Barbaric Roar', 'Shriek of Sorrow', 'Painful Strike',
         'Savage Claws'} | PARRY
SPIRIT = re.compile(r'Ashes$|Puppet$')

# The ERPVP rules "as of 10-3" and the "ADDITIONAL BANS" forward "UPDATED 9-27", verbatim in
# docs/er-pvp-ladder-rules-2026-10.md; the user chose both together, the strictest reading
# (2026-10-18). What tables.rs does not already hold is added here, by the rules' own words.
WEAPONS |= {'Pata',                    # "Pata only allowed as Offhand Moveset": not modelled, so out
            'Twinbird Kite Shield', 'Sword Lance'}                                  # 9-27
TALISMANS |= {'Dagger Talisman',                                                    # 9-27
              'Shard of Alexander', 'Warrior Jar Shard'}   # the planner's ladder bans them outright
ARMOR |= {'Divine Beast Head', 'Royal Remains Helm', 'Royal Remains Armor',          # 9-27; regen
          'Royal Remains Gauntlets', 'Royal Remains Greaves'}
TEARS |= {'Cerulean Hidden Tear'}                                    # 9-27 "Infinite FP"
ASHES |= {'Fast Sword Dance', 'Muleta', 'Painful Strikes'}
SPELLS |= {'Gravitational Missile', "Night Maiden's Mist", 'The Flame of Frenzy', 'Roar of Rugalea',
           "Placidusax's Ruin", 'Unendurable Frenzy', 'Founding Rain of Stars', 'Fire Serpent',
           'Magic Glintblade', 'Giant Golden Arc'}                                  # 10-3 and 9-27
#: "All Methods of HP Recovery via ... Consumables", the 9-27 "giant rancor pot" (Hefty Rancor
#: Pot is the only larger rancor pot) and "Spectral Steed Whistle & Spirit Ashes".
TOOLS = {'Flask of Crimson Tears', 'Hefty Rancor Pot', 'Spectral Steed Whistle'}
#: The planner site's own ladder check (er-build-planner.nyasu.business, `rulesets-BMUmMfbT.js`,
#: "ER PvP Ladder (Seamless Co-op/Den Maps)", rule "All Damage Greases", read 2026-10-19). It refused
#: the RL 125 duel upload of 2026-10-19 with "All Damage Greases: Banned Item: Drawstring Dragonbolt
#: Grease". The 10-3 text bans no damage grease outright; the site does, and an upload has to pass
#: the site. Every grease here whose weapon-buff rows add attack is also found from the regulation
#: (`greases`); Blood, Freezing and Shield Grease add none and are banned only because the site
#: names them. The site leaves out Fire Grease and Drawstring Fire Grease; the regulation rule bans
#: them, since they add attack exactly as the other elements do.
PLANNER_DAMAGE_GREASES = frozenset({
    'Lightning Grease', 'Magic Grease', 'Holy Grease', 'Blood Grease', 'Messmerfire Grease',
    'Dragonbolt Grease', 'Royal Magic Grease', 'Golden Grease', 'Freezing Grease', 'Shield Grease',
    'Festive Grease', 'Drawstring Lightning Grease', 'Drawstring Magic Grease', 'Drawstring Holy Grease',
    'Drawstring Blood Grease', 'Drawstring Messmerfire Grease', 'Drawstring Dragonbolt Grease',
    'Drawstring Royal Magic Grease', 'Drawstring Golden Grease'})
#: Weapon classes banned whole (`wepType`): Backhand Blade ("all bhb EXCEPT 2h curseblades", 9-27,
#: and the 10-3 2H and powerstance bans; the curseblade exception is left out), Light Greatsword
#: (9-27), and every shield ("blocking with shields of any kind", 9-27).
BANNED_CLASSES = {92: 'Backhand Blade', 93: 'Light Greatsword', 65: 'Small Shield', 67: 'Medium Shield',
                  69: 'Greatshield', 90: 'Thrusting Shield'}
#: (weapon, affinity or None, grip): a weapon banned only that way (`10-3 [ WEAPONS ]`).
ROW_BANS = [("Cleanrot Knight's Sword", None, '2H'), ('Club', None, '2H'), ('Stone Club', None, '2H'),
            ('Raptor Talons', 'Lightning', '2H')]
#: `10-3 [ BUFF RESTRICTIONS ]`: "All Greases, Spells and Ash of Wars that buff a weapon are banned
#: on" these setups. (hand, grip, `wepType` classes, weapon names, names excepted, the rule's words).
#: `hand` 'main' is the weapon wielded in the right hand or two-handed, 'off' the left-hand one,
#: None either; `grip` None any. A class is matched by `wepType` (tables.rs `wep_type`), a weapon
#: the rule names by name. Wakizashi is a dagger by `wepType` (1), so an off-hand Wakizashi is caught
#: by the dagger rule; Reverse Blades are the Backhand Blade class (92); Idus Sword is a Light
#: Greatsword (93). Those two classes are banned whole already (`BANNED_CLASSES`).
BUFF_SETUPS = [
    ('main', '2H', {15}, (), (), '2H Thrusting Sword'),
    ('main', '2H', (), {'Stone Club', 'Club'}, (), '2H Stone Club/Club'),
    ('main', '2H', (), {'Sword Lance'}, (), '2H Sword Lance'),
    ('main', '2H', (), {'Raptor Talons'}, (), '2H Raptor Talons'),
    ('off', None, {1}, (), (), 'Offhand Daggers'),
    ('off', None, {17}, (), (), 'Offhand Hatchets'),
    ('main', '1H', (), {'Wakizashi'}, (), 'Mainhand 1H Wakizashi'),
    ('main', '2H', {28}, (), {"Mohgwyn's Sacred Spear"}, "2H Greatspears excluding Mohgwyn's"),
    (None, None, {65, 67, 69, 90}, (), (), 'Shields'),                    # `SHIELD_TYPES`
    ('main', '2H', (), {'Star Fist'}, (), '2H Star Fists'),
    ('main', '2H', {14}, (), (), '2H Twinblades (class)'),
    ('main', '2H', {92}, (), (), '2H Reverse Blades'),
    (None, None, (), {'Idus Sword'}, (), 'Idus Sword'),
]


def equipped(slots, every=False):
    """The slots in the active set (`equipIndex`), or with `every` every slot the build owns: the
    planner's own ladder check counts an owned item whether worn or not (it flagged a base build's
    leftover Briar and Rakshasa pieces on 2026-10-18), so a generated duel build is judged that way."""
    return [s for s in (slots or []) if s and (every or s.get('equipIndex') is not None)]


RULES_SRC = (ROOT / 'crates/er-duel-rules-core/src/rules.rs').read_text()
SHIELD_PHYS_GUARD_CAP = float(re.search(r'SHIELD_PHYS_GUARD_CAP: f32 = ([\d.]+);', RULES_SRC)[1])
#: `wepType` of the shield classes and of the thrusting shield (tables.rs `wep_type`).
SHIELD_TYPES, THRUSTING_SHIELD = (65, 67, 69, 90), 90
_GUARD = {}
_LOADED = {}


def _mod(name, fname):
    import importlib.util
    import sys
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / fname)
    m = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tables():
    """`er-mechanics-ar.Tables` of the installed regulation, loaded once."""
    if 'tables' not in _LOADED:
        _LOADED['tables'] = _mod('er_mechanics_ar', 'er-mechanics-ar.py').Tables(None)
    return _LOADED['tables']


def ids(name):
    """An `&[i32]` table of tables.rs as a set."""
    body = re.search(rf'pub const {name}: &\[i32\] = &\[(.*?)\];', SRC, re.S)[1]
    return {int(x) for x in re.findall(r'-?\d+', body)}


#: Weapon buff rows of the status greases the rules name (tables.rs, `er-duel-rules-gen.py`).
ROT_GREASE, SLEEP_GREASE, POISON_GREASE = ids('ROT_GREASE'), ids('SLEEP_GREASE'), ids('POISON_GREASE')
RIPPLE_WEAPONS = table('RIPPLE_WEAPONS')


def greases():
    """{name: {'rows', 'attack'}} of every item (`goodsType` 0) whose SpEffect closure holds a
    weapon-buff row (`spCategory` 162/163, `er-mechanics-buffs.WEAPON_BUFF_CATS`): the rows the
    pouch reads as a weapon buff, reached the way it reaches them (`_goods_refs`, `Buffs._closure`).
    `attack` is the attack columns those rows set off their default (flat `*AttackPower`, AR rate
    `*AttackPowerRate`, damage rate `*AttackRate`): a non-empty `attack` is a damage grease. Status
    build-up is not on these rows (Blood, Poison, Freezing and Sleep Grease set none of the status
    columns), so a status grease is known by its rows in the tables.rs status tables instead.
    Read from the regulation on first use."""
    if 'greases' not in _LOADED:
        B = _mod('er_mechanics_buffs', 'er-mechanics-buffs.py')
        m = _LOADED.get('buffs') or B.Buffs(None)
        _LOADED['buffs'] = m
        cols = [c for d in (B.AP_FLAT, B.AP_FLAT_PT, B.AP_RATE, B.AP_RATE_PT, B.ATK_RATE, B.ATK_RATE_PT)
                for c in d.values()]
        out = {}
        for gid, g in sorted(m.goods.items()):
            name = m.goods_name.get(gid)
            refs = B._goods_refs(m, gid, g) if name and g['goodsType'] == 0 else None
            if not refs:
                continue
            rows = [i for i, _ in m._closure(refs[1], refs[2])
                    if i in m.sp and m.sp[i]['spCategory'] in B.WEAPON_BUFF_CATS]
            if rows and name not in out:
                out[name] = {'rows': rows, 'attack': sorted({c for i in rows for c in cols if m.nondefault(i, [c])})}
        _LOADED['greases'] = out
    return _LOADED['greases']


def grease_ban(name):
    """Why a grease is banned in every setup, or None: a rot grease (10-3 "Rot Greases"), a damage
    grease by the regulation (`greases`' `attack`), or one the planner site's "All Damage Greases"
    names (`PLANNER_DAMAGE_GREASES`)."""
    g = greases().get(name)
    if g and set(g['rows']) & ROT_GREASE:
        return f'rot grease {name}'
    if g and g['attack']:
        return f"damage grease {name} ({', '.join(g['attack'])})"
    if name in PLANNER_DAMAGE_GREASES:
        return f'{name} (planner "All Damage Greases")'
    return None


def buff_setup_ban(weapon, grip, hand):
    """Why every weapon buff (grease, buff spell, buff skill) is banned while `weapon` is held in
    `grip` ('1H'/'2H') in `hand` ('main'/'off'), by `10-3 [ BUFF RESTRICTIONS ]` (`BUFF_SETUPS`), or
    None."""
    try:
        wep_type = _tables().weapons[_tables().find_weapon(weapon)]['wepType']
    except (KeyError, SystemExit):
        wep_type = None
    for h, g, classes, names, excepted, words in BUFF_SETUPS:
        if h not in (None, hand) or g not in (None, grip) or weapon in excepted:
            continue
        if weapon in names or wep_type in classes:
            return f'weapon buff on {words} ({weapon})'
    return None


def grease_setup_ban(name, weapon, grip, hand):
    """Why grease `name` is banned with `weapon` held in `grip` in `hand`, or None: sleep greases on
    Ripple weapons, poison greases on a two-handed Shamshir or in the off hand (the blacklisted
    "Offhand drawstring poison grease"; any off-hand poison grease, the stricter reading), and every
    grease in a `BUFF_SETUPS` setup. `grease_ban` covers what is banned in every setup."""
    g = greases().get(name)
    if g is None:
        return None
    rows = set(g['rows'])
    if rows & SLEEP_GREASE and weapon in RIPPLE_WEAPONS:
        return f'sleep grease {name} on Ripple weapon {weapon}'
    if rows & POISON_GREASE and hand == 'off':
        return f'poison grease {name} on off-hand {weapon}'
    if rows & POISON_GREASE and weapon == 'Shamshir' and grip == '2H':
        return f'poison grease {name} on 2H Shamshir'
    return buff_setup_ban(weapon, grip, hand)


def class_bans():
    """{weapon name: reason} the ladder bans by the weapon's own params: every weapon of a
    `BANNED_CLASSES` class, and any other shield whose base row blocks more than
    `SHIELD_PHYS_GUARD_CAP`% physical (rules.rs `ShieldGuard`). Read from the regulation on first
    use, so `check` on a public build stays a name lookup and loads nothing."""
    if not _GUARD:
        t = _tables()
        for wid, r in t.weapons.items():
            name = t.names.get(wid)
            if wid % 10000 or not name:
                continue
            if r['wepType'] in BANNED_CLASSES:
                _GUARD[name] = f"{name} ({BANNED_CLASSES[r['wepType']]} class)"
            elif r['wepType'] in SHIELD_TYPES and r['physGuardCutRate'] > SHIELD_PHYS_GUARD_CAP:
                _GUARD[name] = f"{name} ({r['physGuardCutRate']:g}% physical guard)"
        _GUARD.setdefault(None, None)
    return {k: v for k, v in _GUARD.items() if k}


# One predicate per kind of thing a build holds, each returning why it is banned or None. `check`
# judges a finished build with them; `er-weapon-build.py --fight duel` leaves out what they name
# before it picks anything.
def weapon_ban(name):
    return name if name in WEAPONS else f'{name} (regen)' if name in REGEN else None


def ash_ban(ash, weapon='', affinity=''):
    if ash in ASHES:
        return f'{ash} on {weapon}'
    if ash == 'Chilling Mist' and affinity == 'Cold':
        return f'Chilling Mist on Cold {weapon}'
    return None


def armor_ban(name):
    return name if name in ARMOR else f'{name} (regen)' if name in REGEN else None


def talisman_ban(name):
    return name if name in TALISMANS else f'{name} (regen)' if name in REGEN else None


def tear_ban(name):
    return f'tear {name}' if name in TEARS else None


def spell_ban(name):
    return (f'spell {name}' if name in SPELLS else f'buff spell {name}' if name in BUFFS
            else f'heal spell {name}' if name in HEALS else None)


def row_ban(weapon, affinity, grip):
    """Why a weapon is banned in this grip and affinity only (`ROW_BANS`), or None."""
    for w, aff, g in ROW_BANS:
        if weapon == w and grip == g and aff in (None, affinity):
            return f"{g} {aff + ' ' if aff else ''}{weapon}"
    return None


def tool_ban(name):
    if name in TOOLS:
        return name
    if name in BUFFS:
        return f'buff item {name}'
    if name in HEALS:
        return f'heal item {name}'
    if name == 'Fan Daggers':
        return name
    if name.endswith('Grease'):
        return grease_ban(name)
    return f'spirit {name}' if SPIRIT.search(name) else None


def muleta_ban(talismans):
    """Muleta is banned only beside an ash-damage talisman, so whether it is depends on the talismans."""
    return 'Muleta + ash-damage talisman' if set(talismans) & ASH_TALISMANS else None


def check(b, every=False, ladder_rl=125, params=False):
    """Every ladder rule `b` breaks by name; `ladder_rl` None skips the rune level rule, for a duel
    build at another RL that keeps the ladder's item bans. `params` also judges what needs the
    regulation (`class_bans`). A grease is judged from the regulation either way (`grease_ban`):
    whether it adds attack is a param fact, not a name."""
    v = []
    rl = (b.get('stats') or {}).get('rl')
    if ladder_rl is not None and rl not in (ladder_rl, None):
        v.append(f'RL {rl}')
    # The weapon inventory is the generated plan itself, so with `every` all of it is judged.
    inv = (b.get('inventory') or {}).get('slots')
    weapons = [s for s in inv or [] if s] if every else equipped(inv)
    for s in weapons:
        n = s.get('name', '')
        v += filter(None, (weapon_ban(n), ash_ban(s.get('weaponArt'), n, s.get('infusion')),
                           class_bans().get(n) if params else None))
    for part in ('head', 'body', 'arms', 'legs'):
        for s in equipped(((b.get('protectors') or {}).get(part) or {}).get('slots'), every):
            v += filter(None, (armor_ban(s['name']),))
    tal = {s['name'] for s in equipped((b.get('talismans') or {}).get('slots'), every)}
    v += sorted(tal & TALISMANS) + [f'{t} (regen)' for t in sorted(tal & REGEN)]
    if any(s.get('weaponArt') == 'Muleta' for s in weapons) and muleta_ban(tal):
        v.append(muleta_ban(tal))
    for s in equipped((b.get('spells') or {}).get('slots'), every):
        v += filter(None, (spell_ban(s.get('name', '')),))
    items = b.get('items') or {}
    v += filter(None, map(tear_ban, items.get('crystalTears') or []))
    for s in (items.get('tools') or {}).get('slots') or []:
        v += filter(None, (tool_ban(s.get('name', '')),))
    return v


def version_key(x):
    return tuple(int(p) for p in re.findall(r'\d+', x or '0'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--since', default='2026-06-01', help='updatedAt floor (ISO date)')
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    out = []
    for line in MIRROR.open():
        r = json.loads(line)
        b = r['build']
        if b.get('isPvE') or (r['updatedAt'] or '') < a.since:
            continue
        v = check(b)
        out.append((len(v), r['updatedAt'], r['id'], b.get('version'), b.get('name'), b.get('tags') or [], v))
    out.sort(key=lambda o: (o[0], 'Ladder' not in o[5], [-x for x in version_key(o[3])], o[1]), reverse=False)
    print(f'{len(out)} PvP RL 125 builds updated since {a.since}')
    for o in out[:a.top]:
        tags = ','.join(t for t in o[5] if t in ('Ladder', 'Duels'))
        print(f"{o[0]} | {o[1][:10]} | v{o[3]} | {o[2]} | {o[4]!r} | {tags} | {'; '.join(o[6])}")


def selftest():
    fails = []

    def check_(what, got, want):
        ok = got == want
        print(f"{'ok  ' if ok else 'FAIL'} {what}: {got!r}" + ('' if ok else f' (want {want!r})'))
        if not ok:
            fails.append(what)

    g = greases()
    # The refused upload of 2026-10-19: "All Damage Greases: Banned Item: Drawstring Dragonbolt Grease".
    check_('Drawstring Dragonbolt Grease adds lightning attack', g['Drawstring Dragonbolt Grease']['attack'],
           ['thunderAttackPower'])
    check_('Drawstring Dragonbolt Grease banned', bool(tool_ban('Drawstring Dragonbolt Grease')), True)
    check_('Fire Grease banned by the regulation (the site leaves it out)', bool(tool_ban('Fire Grease')), True)
    check_('every site-named grease is banned', sorted(n for n in PLANNER_DAMAGE_GREASES if not tool_ban(n)), [])
    check_('Blood Grease adds no attack', g['Blood Grease']['attack'], [])
    check_('Blood Grease banned only by the site', tool_ban('Blood Grease'),
           'Blood Grease (planner "All Damage Greases")')
    check_('Rot Grease banned', tool_ban('Rot Grease'), 'rot grease Rot Grease')
    check_('Drawstring Rot Grease banned', tool_ban('Drawstring Rot Grease'), 'rot grease Drawstring Rot Grease')
    check_('greases legal in every setup', sorted(n for n in g if not tool_ban(n)),
           ['Dragon Communion Grease', 'Dragonwound Grease', 'Drawstring Eternal Sleep Grease',
            'Drawstring Poison Grease', 'Drawstring Soporific Grease', 'Eternal Sleep Grease',
            'Poison Grease', 'Soporific Grease'])
    check_('Poison Grease on a 1H Shamshir', grease_setup_ban('Poison Grease', 'Shamshir', '1H', 'main'), None)
    check_('Poison Grease on a 2H Shamshir', grease_setup_ban('Poison Grease', 'Shamshir', '2H', 'main'),
           'poison grease Poison Grease on 2H Shamshir')
    check_('Drawstring Poison Grease off hand',
           bool(grease_setup_ban('Drawstring Poison Grease', 'Longsword', '1H', 'off')), True)
    check_('Soporific Grease on Ripple Blade',
           bool(grease_setup_ban('Soporific Grease', 'Ripple Blade', '1H', 'main')), True)
    check_('Soporific Grease on a Longsword', grease_setup_ban('Soporific Grease', 'Longsword', '2H', 'main'), None)
    check_('2H Estoc (Thrusting Sword class)', bool(buff_setup_ban('Estoc', '2H', 'main')), True)
    check_('1H Estoc', buff_setup_ban('Estoc', '1H', 'main'), None)
    check_('off-hand Dagger', bool(buff_setup_ban('Dagger', '1H', 'off')), True)
    check_('main-hand Dagger', buff_setup_ban('Dagger', '1H', 'main'), None)
    check_('off-hand Hand Axe', bool(buff_setup_ban('Hand Axe', '1H', 'off')), True)
    check_('1H main-hand Wakizashi', bool(buff_setup_ban('Wakizashi', '1H', 'main')), True)
    check_('2H Lance (Great Spear class)', bool(buff_setup_ban('Lance', '2H', 'main')), True)
    check_("2H Mohgwyn's Sacred Spear excepted", buff_setup_ban("Mohgwyn's Sacred Spear", '2H', 'main'), None)
    check_('2H Godskin Peeler (Twinblade class)', bool(buff_setup_ban('Godskin Peeler', '2H', 'main')), True)
    check_('2H Star Fist', bool(buff_setup_ban('Star Fist', '2H', 'main')), True)
    check_('2H Club', bool(buff_setup_ban('Club', '2H', 'main')), True)
    check_('off-hand Buckler', bool(buff_setup_ban('Buckler', '1H', 'off')), True)
    check_('Idus Sword in either hand', bool(buff_setup_ban('Idus Sword', '1H', 'off')), True)
    check_('2H Longsword', buff_setup_ban('Longsword', '2H', 'main'), None)
    print('selftest', 'FAILED: ' + ', '.join(fails) if fails else 'passed')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
