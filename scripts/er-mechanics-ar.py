#!/usr/bin/env python3
"""Elden Ring weapon attack rating from the installed regulation, offline.

A port of the executable's own scaling code (1.16.2 addresses, byte-compared
against the 1.17.1 image; see docs/er-mechanics/attack-rating.md):

  `PerformCalcCorrection`  0x140690f30  CalcCorrectGraph curve
  `PerformWeaponScaling`   0x140690870  one stat -> multiplier (or the 0.6 penalty)
  `FUN_140690390`          0x140690390  combine five stats for one element, 2H STR x1.5
  `FUN_1406832a0`          0x1406832a0  attackBase * ReinforceParamWeapon rate * multiplier
  `FUN_1407c0390/0430`     sorcery / incantation buff = 100 * magic / holy multiplier

  python3 scripts/er-mechanics-ar.py "Uchigatana" --affinity Keen --level 25 --stats str=18,dex=65
  python3 scripts/er-mechanics-ar.py "Claymore" --stats str=11,dex=13 --two-handed
  python3 scripts/er-mechanics-ar.py --selftest

Labels in comments: `VERIFIED` read from the executable or params, `COMMUNITY`
taken from ThomasJClark/elden-ring-weapon-calculator, `INFERRED` consistent with
data but not traced.
"""
import argparse, importlib.util, json, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('er_param_read', os.path.join(_HERE, 'er-param-read.py'))
EPR = importlib.util.module_from_spec(_s)
_s.loader.exec_module(EPR)

# Affinity = (EquipParamWeapon id % 10000) // 100, names read off the Uchigatana rows
# 9000000..9001200 (`VERIFIED` against Smithbox row names).
AFFINITIES = ['Standard', 'Heavy', 'Keen', 'Quality', 'Fire', 'Flame Art', 'Lightning',
              'Sacred', 'Magic', 'Cold', 'Poison', 'Blood', 'Occult']

STATS = ('str', 'dex', 'int', 'fth', 'arc')
STAT_ALIASES = {'fai': 'fth', 'faith': 'fth', 'strength': 'str', 'dexterity': 'dex',
                'intelligence': 'int', 'arcane': 'arc', 'luck': 'arc'}
# Paramdef spellings per stat: AECP name, weapon correct field, reinforce rate, requirement.
STAT_FIELDS = {
    'str': ('Strength', 'correctStrength', 'correctStrengthRate', 'properStrength'),
    'dex': ('Dexterity', 'correctAgility', 'correctAgilityRate', 'properAgility'),
    'int': ('Magic', 'correctMagic', 'correctMagicRate', 'properMagic'),
    'fth': ('Faith', 'correctFaith', 'correctFaithRate', 'properFaith'),
    'arc': ('Luck', 'correctLuck', 'correctLuckRate', 'properLuck'),
}
# Element index 0..4 as the executable numbers them (`FUN_140690390` param_4 picks
# correctType at +0xec, +0x17d, +0x17e, +0x17f, +0x18e).
ELEMENTS = [
    # name, AECP suffix, attackBase field, reinforce rate field, correctType field
    ('physical', 'Physics', 'attackBasePhysics', 'physicsAtkRate', 'correctType_Physics'),
    ('magic', 'Magic', 'attackBaseMagic', 'magicAtkRate', 'correctType_Magic'),
    ('fire', 'Fire', 'attackBaseFire', 'fireAtkRate', 'correctType_Fire'),
    ('lightning', 'Thunder', 'attackBaseThunder', 'thunderAtkRate', 'correctType_Thunder'),
    ('holy', 'Dark', 'attackBaseDark', 'darkAtkRate', 'correctType_Dark'),
]
# Status build-up: SpEffectParam field and the weapon's correctType field. The four with a
# graph are the four arcane multipliers `FUN_1406832a0` stores at out+0x18/+0x20/+0x2c/+0x30
# (`VERIFIED`); the other three get no multiplier.
STATUSES = [
    ('poison', 'poizonAttackPower', 'correctType_Poison'),
    ('scarlet_rot', 'diseaseAttackPower', None),
    ('bleed', 'bloodAttackPower', 'correctType_Blood'),
    ('frost', 'freezeAttackPower', None),
    ('sleep', 'sleepAttackPower', 'correctType_Sleep'),
    ('madness', 'madnessAttackPower', 'correctType_Madness'),
    ('death_blight', 'curseAttackPower', None),
]
# `COMMUNITY`: bows, greatbows, crossbows and ballistae are always two-handed.
ALWAYS_TWO_HANDED_WEP_TYPES = {50, 51, 53, 55, 56}

TWO_HAND_STR_MULT = 1.5          # 0x143b33d7c (1.16.2), 0x14069134c load on 1.17.1
PENALTY_CAP_PCT = 20.0           # 0x143b33d80
PENALTY_FULL_PCT = 100.0         # 0x143b33d84


class Tables:
    """Every param the calculation reads, keyed by row id."""

    def __init__(self, regulation=None):
        files = EPR.load(regulation)

        def by_id(stem, fields=None):
            rs, _, _ = EPR.rows(EPR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rs}

        self.weapons = by_id('EquipParamWeapon')
        self.reinforce = by_id('ReinforceParamWeapon')
        self.aecp = by_id('AttackElementCorrectParam')
        self.graphs = by_id('CalcCorrectGraph')
        self.speffects = by_id('SpEffectParam', [f for _, f, _ in STATUSES])
        pc = by_id('PlayerCommonParam', ['lowStatus_AtkPowDown'])
        self.low_status_atk_pow_down = next(iter(pc.values()))['lowStatus_AtkPowDown']
        self.names = EPR.row_names('EquipParamWeapon')

    def max_level(self, reinforce_type_id):
        """Highest level with a ReinforceParamWeapon row, counting up from the type id.

        `maxReinforceLevel` is not used: somber row 2210 carries 25 there.
        """
        lvl = 0
        while reinforce_type_id + lvl + 1 in self.reinforce:
            lvl += 1
        return lvl

    def find_weapon(self, name_or_id, affinity='Standard'):
        """Return the EquipParamWeapon base-row id (level 0) for a name or id plus affinity."""
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            wid = int(name_or_id)
            base = (wid // 10000) * 10000
            if affinity in (None, 'Standard') and wid % 10000:
                return (wid // 100) * 100
        else:
            want = str(name_or_id).strip().lower()
            hits = [i for i, n in self.names.items()
                    if n and n.lower() == want and i % 10000 == 0 and i in self.weapons]
            if not hits:
                hits = [i for i, n in self.names.items()
                        if n and n.lower() == want and i in self.weapons]
                if not hits:
                    raise SystemExit(f'no weapon named {name_or_id!r}')
                return (min(hits) // 100) * 100
            base = min(hits)
        aff = affinity or 'Standard'
        idx = next((i for i, a in enumerate(AFFINITIES) if a.lower() == aff.lower()), None)
        if idx is None:
            raise SystemExit(f'unknown affinity {affinity!r}; one of {AFFINITIES}')
        wid = base + idx * 100
        if wid not in self.weapons:
            raise SystemExit(f'{self.names.get(base, base)} has no {aff} row ({wid})')
        return wid


def calc_correct(graph, x):
    """`PerformCalcCorrection` 0x140690f30: CalcCorrectGraph value (percent) at stat x."""
    if graph is None:
        return x                                  # missing row: returns the input
    v = [graph[f'stageMaxVal{i}'] for i in range(5)]
    g = [graph[f'stageMaxGrowVal{i}'] for i in range(5)]
    a = [graph[f'adjPt_maxGrowVal{i}'] for i in range(5)]
    if v[4] <= x:
        x = v[4]
    if not x > 0:
        return g[0]
    i = 0
    while i < 3 and not x <= v[i + 1]:
        i += 1
    dv, dg = v[i + 1] - v[i], g[i + 1] - g[i]
    if dv == 0:
        return g[i + 1]
    if a[i] >= 0:
        out = g[i] + ((x - v[i]) / dv) ** a[i] * dg
    else:
        out = g[i] + (1.0 - ((dv - (x - v[i])) / dv) ** (-a[i])) * dg
    lo, hi = min(g[i], g[i + 1]), max(g[i], g[i + 1])
    return min(max(out, lo), hi)


def stat_multiplier(tables, requirement, stat, rate, graph_id, stat_mult=1.0, raw_for_graph=False):
    """`PerformWeaponScaling` 0x140690870 for one stat.

    Returns 1 + rate/100 * graph/100 when the requirement is met, 1 when the rate is
    zero, and the lack-of-stats factor when not met. The requirement is checked
    against trunc(stat * stat_mult), so two-handing counts toward STR requirements.
    """
    eff = int(stat * stat_mult)
    if requirement - eff > 0 and requirement > 0:
        short = min((1.0 - eff / requirement) * 100.0, PENALTY_CAP_PCT)
        k = (PENALTY_FULL_PCT - 100.0) / (1.0 - PENALTY_CAP_PCT * PENALTY_CAP_PCT)
        floor_ = PENALTY_CAP_PCT if k > 0 else 0.0
        out = ((short - floor_) * k * (short - floor_) + (100.0 - floor_ * k * floor_)) / 100.0
        return max(out - tables.low_status_atk_pow_down, 0.0)
    if rate > 0:
        x = stat if raw_for_graph else eff
        return (rate / 100.0) * (calc_correct(tables.graphs.get(graph_id), x) / 100.0) + 1.0
    return 1.0


def element_multiplier(tables, wep, reinf, aecp, elem_suffix, graph_id, stats, str_mult=1.0):
    """`FUN_140690390`: combine the five stat multipliers for one element."""
    ms = []
    for s in STATS:
        aname, cfield, rfield, pfield = STAT_FIELDS[s]
        if not aecp.get(f'is{aname}Correct_by{elem_suffix}'):
            ms.append(1.0)
            continue
        over = aecp.get(f'overwrite{aname}CorrectRate_by{elem_suffix}', -1)
        infl = aecp.get(f'Influence{aname}CorrectRate_by{elem_suffix}', 100) * 0.01
        rate = (over if over >= 0 else wep[cfield]) * reinf[rfield]
        m = stat_multiplier(tables, wep[pfield], stats.get(s, 0), rate, graph_id,
                            str_mult if s == 'str' else 1.0)
        ms.append(infl * m)
    if any(m < 1.0 for m in ms):
        return min([1.0] + ms)
    return 1.0 + sum(m - 1.0 for m in ms)


def normalise_stats(stats):
    out = {s: 0 for s in STATS}
    for k, v in (stats or {}).items():
        k = STAT_ALIASES.get(k.lower(), k.lower())
        if k in out:
            out[k] = int(v)
    return out


def attack_rating(tables, weapon, affinity='Standard', level=0, stats=None, two_handed=False):
    """AR per damage type, status build-up and spell buff for one weapon.

    Each entry is {'base', 'scaling', 'total'}; total = base * multiplier.
    """
    stats = normalise_stats(stats)
    base_id = tables.find_weapon(weapon, affinity)
    wep = tables.weapons[base_id]
    maxlvl = tables.max_level(wep['reinforceTypeId'])
    if not 0 <= level <= maxlvl:
        raise SystemExit(f'level {level} out of range 0..{maxlvl} for {tables.names.get(base_id)}')
    # EquipParamWeapon::GetEntry 0x140d54600: reinforce row = reinforceTypeId + id % 100.
    reinf = tables.reinforce[wep['reinforceTypeId'] + level]
    aecp = tables.aecp.get(wep['attackElementCorrectId'], {})
    two = two_handed or wep['wepType'] in ALWAYS_TWO_HANDED_WEP_TYPES
    if wep.get('isDualBlade'):
        two = False                                   # `COMMUNITY`: paired weapons
    str_mult = TWO_HAND_STR_MULT if two else 1.0

    out = {'weapon': tables.names.get(base_id), 'id': base_id + level, 'level': level,
           'max_level': maxlvl, 'two_handed_bonus': two, 'damage': {}, 'status': {},
           'spell_buff': {}}
    for name, suf, bfield, rfield, gfield in ELEMENTS:
        base = wep[bfield] * reinf[rfield]
        if not base:
            continue
        mult = element_multiplier(tables, wep, reinf, aecp, suf, wep[gfield], stats, str_mult)
        out['damage'][name] = {'base': base, 'scaling': base * (mult - 1.0), 'total': base * mult}
    out['total'] = sum(d['total'] for d in out['damage'].values())

    # Status: spEffectBehaviorIdN + ReinforceParamWeapon.spEffectId(N+1) (`COMMUNITY`
    # offset pairing), scaled by the arcane multiplier the exe computes (`VERIFIED`
    # multiplier, `INFERRED` that the SpEffect value is what it multiplies).
    arc_rate = wep['correctLuck'] * reinf['correctLuckRate']
    for slot in range(3):
        sp = wep.get(f'spEffectBehaviorId{slot}', -1)
        if sp is None or sp < 0:
            continue
        row = tables.speffects.get(sp + reinf.get(f'spEffectId{slot + 1}', 0))
        if not row:
            continue
        for name, field, gfield in STATUSES:
            val = row.get(field, 0)
            if not val:
                continue
            mult = 1.0
            if gfield:
                mult = stat_multiplier(tables, wep['properLuck'], stats['arc'], arc_rate, wep[gfield])
            prev = out['status'].get(name, {'base': 0.0, 'scaling': 0.0, 'total': 0.0})
            out['status'][name] = {'base': prev['base'] + val,
                                   'scaling': prev['scaling'] + val * (mult - 1.0),
                                   'total': prev['total'] + val * mult}

    # 0x1407c0390 (enableMagic) / 0x1407c0430 (enableMiracle): 100 * element multiplier,
    # stat-only path with the two-handing multiplier fixed at 1.0.
    for flag, (name, suf, _, _, gfield) in (('enableMagic', ELEMENTS[1]),
                                            ('enableMiracle', ELEMENTS[4])):
        if wep.get(flag):
            out['spell_buff'][name] = 100.0 * element_multiplier(
                tables, wep, reinf, aecp, suf, wep[gfield], stats, 1.0)
    return out


def _fmt(r):
    lines = [f"{r['weapon']} +{r['level']} (id {r['id']}, max +{r['max_level']}, "
             f"2H STR bonus {'on' if r['two_handed_bonus'] else 'off'})"]
    for k, d in r['damage'].items():
        lines.append(f"  {k:<10} {d['base']:8.2f} + {d['scaling']:8.2f} = {d['total']:8.2f}")
    lines.append(f"  {'total':<10} {r['total']:30.2f}")
    for k, d in r['status'].items():
        lines.append(f"  {k:<10} {d['base']:8.2f} + {d['scaling']:8.2f} = {d['total']:8.2f}")
    for k, v in r['spell_buff'].items():
        lines.append(f"  {k} spell buff {v:.2f}")
    return '\n'.join(lines)


# Reference values: ThomasJClark/elden-ring-weapon-calculator (master, calculator.ts and
# regulationData.ts) run unmodified under deno on its own public/regulation-vanilla-v1.17.js,
# 2026-09-29. Independent code and an independent regulation extraction (`COMMUNITY`).
# Keys: weapon, affinity, level, stats, two_handed, expected {path: value}.
_S = dict(str=10, dex=10, int=10, fth=10, arc=10)
SELFTEST_CASES = [
    ('Uchigatana', 'Standard', 25, {**_S, 'str': 18, 'dex': 65}, False,
     {'damage.physical': 502.835703125, 'status.bleed': 45}),
    ('Uchigatana', 'Keen', 25, {**_S, 'str': 18, 'dex': 65}, False,
     {'damage.physical': 558.45911472457}),
    ('Uchigatana', 'Heavy', 10, {**_S, 'str': 40, 'dex': 12}, True,
     {'damage.physical': 99.66}),                       # dex 12 < 15: the 0.6 penalty
    ('Uchigatana', 'Blood', 20, {**_S, 'str': 14, 'dex': 20, 'arc': 45}, False,
     {'damage.physical': 329.4092807528492, 'status.bleed': 101.255}),
    ('Claymore', 'Standard', 0, {**_S, 'str': 10, 'dex': 13}, False,
     {'damage.physical': 82.8}),                        # str 10 < 16
    ('Claymore', 'Standard', 0, {**_S, 'str': 11, 'dex': 13}, True,
     {'damage.physical': 160.27025590219586}),          # trunc(11*1.5)=16 meets 16
    ('Claymore', 'Standard', 25, {**_S, 'str': 66, 'dex': 20}, True,
     {'damage.physical': 623.2539255360258}),           # 2H STR 99 on graph 0
    ('Glintstone Staff', 'Standard', 25, {**_S, 'int': 80}, False,
     {'damage.physical': 144.15625, 'spell_buff.magic': 329.5}),
    ('Finger Seal', 'Standard', 25, {**_S, 'fth': 60}, False,
     {'damage.physical': 125.45312499999999, 'spell_buff.holy': 286.75}),
    ('Moonveil', 'Standard', 10, {**_S, 'str': 12, 'dex': 40, 'int': 40}, False,
     {'damage.physical': 272.2620429860482, 'damage.magic': 366.61800000000005,
      'status.bleed': 50}),
    ('Uchigatana', 'Sacred', 25, {**_S, 'str': 11, 'dex': 15, 'fth': 60}, False,
     {'damage.physical': 232.29268014616676, 'damage.holy': 422.147}),
    ('Uchigatana', 'Occult', 25, {**_S, 'str': 11, 'dex': 15, 'arc': 60}, False,
     {'damage.physical': 533.0127116493978, 'status.bleed': 85.40119999999999}),
    ('Uchigatana', 'Poison', 25, {**_S, 'str': 11, 'dex': 15, 'arc': 30}, False,
     {'damage.physical': 341.9290183831503, 'status.poison': 105.84781249999999,
      'status.bleed': 42.339124999999996}),
    ('Uchigatana', 'Cold', 25, {**_S, 'str': 11, 'dex': 15, 'int': 30}, False,
     {'damage.physical': 250.42745811566667, 'damage.magic': 225.984, 'status.bleed': 38,
      'status.frost': 105}),
    ('Smithscript Greathammer', 'Magic', 25, {**_S, 'str': 20, 'dex': 12, 'int': 40}, False,
     {'damage.physical': 159.6, 'damage.magic': 386.8526666666667}),  # AECP overwrite 5
    ('Longbow', 'Standard', 25, {**_S, 'dex': 40}, False,
     {'damage.physical': 249.28464071953917}),          # bow: always two-handed
    ('Starscourge Greatsword', 'Standard', 10, {**_S, 'str': 30, 'dex': 14, 'int': 20}, True,
     {'damage.physical': 189.63, 'damage.magic': 232.63240000000005}),  # paired: no 2H bonus
    ('Rivers of Blood', 'Standard', 10, {**_S, 'str': 12, 'dex': 18, 'arc': 60}, False,
     {'damage.physical': 326.56476173086077, 'damage.fire': 280.21238000000005,
      'status.bleed': 76.73}),
]

# Curve points read straight off CalcCorrectGraph rows (graph 0: stage ends 1/18/60/80/150
# at 0/25/75/90/110), so these test the port of 0x140690f30, not a reference calculator.
GRAPH_CASES = [(0, 1, 0.0), (0, 18, 25.0), (0, 60, 75.0), (0, 80, 90.0), (0, 150, 110.0),
               (0, 200, 110.0), (6, 25, 10.0), (6, 45, 75.0), (6, 30, 26.25)]


def selftest(tables):
    bad = 0
    n = 0
    for gid, x, want in GRAPH_CASES:
        got = calc_correct(tables.graphs[gid], x)
        n += 1
        if abs(got - want) > 1e-4:
            bad += 1
            print(f'FAIL graph {gid} x={x}: {got} != {want}')
    for weapon, aff, lvl, stats, two, expected in SELFTEST_CASES:
        r = attack_rating(tables, weapon, aff, lvl, stats, two)
        for path, want in expected.items():
            sect, key = path.split('.')
            node = r[sect].get(key)
            got = node if isinstance(node, (int, float)) or node is None else node['total']
            n += 1
            tol = max(0.02, abs(want) * 1e-4)
            if got is None or abs(got - want) > tol:
                bad += 1
                print(f'FAIL {aff} {weapon} +{lvl} {path}: got {got} want {want}')
            else:
                print(f'ok   {aff} {weapon} +{lvl} {path}: {got:.4f} (ref {want:.4f})')
    print(f'{n - bad}/{n} passed')
    return bad == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--affinity', default='Standard')
    ap.add_argument('--level', type=int, default=0)
    ap.add_argument('--stats', default='', help='str=18,dex=65,int=10,fth=10,arc=10')
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--regulation')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    tables = Tables(a.regulation)
    if a.selftest:
        sys.exit(0 if selftest(tables) else 1)
    if not a.weapon:
        ap.error('weapon is required unless --selftest')
    stats = dict(kv.split('=') for kv in a.stats.split(',') if kv)
    r = attack_rating(tables, a.weapon, a.affinity, a.level, stats, a.two_handed)
    print(json.dumps(r, indent=2) if a.json else _fmt(r))


if __name__ == '__main__':
    main()
