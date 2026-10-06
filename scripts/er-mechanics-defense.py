#!/usr/bin/env python3
"""Elden Ring defender-side mechanics: defense, absorption, resistances, poise, damage per hit.

Everything is read from the installed regulation through `scripts/er-param-read.py`. The formulas
are the ones traced in the executable; `docs/er-mechanics/defense.md` carries the addresses and the
evidence label of each step. Labels used in comments here: `EXE` (read from the 1.16.2 Ghidra dump
and re-checked in `eldenring-deobf-1.17.1.bin`), `REGULATION` (param values), `INFERRED`, `SITE`
(the build planner's JS).

  python3 scripts/er-mechanics-defense.py --stats vig=40,mnd=20,end=30,str=50,dex=12,int=9,fth=9,arc=9 \\
      --armor "Bull-Goat Helm,Bull-Goat Armor,Bull-Goat Gauntlets,Bull-Goat Greaves" \\
      --talismans "Bull-Goat's Talisman,Dragoncrest Greatshield Talisman" --pvp
  python3 scripts/er-mechanics-defense.py --selftest
  python3 scripts/er-mechanics-defense.py --corpus [--limit N] [--show 20]

Library use:
  m = load_module()                      # importlib, the filename is hyphenated
  t = m.Tables()                         # one regulation read, cached
  d = m.defender(t, stats, armor, talismans, pvp=False)
  m.damage({'physical': 500, 'fire': 200}, 100, d, phys_type='slash')
"""
import argparse, importlib.util, json, math, os, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location('er_param_read', os.path.join(_HERE, 'er-param-read.py'))
PR = importlib.util.module_from_spec(_s)
_s.loader.exec_module(PR)

STATS = ('vig', 'mnd', 'end', 'str', 'dex', 'int', 'fth', 'arc')
TYPES = ('physical', 'strike', 'slash', 'pierce', 'magic', 'fire', 'lightning', 'holy')
# Damage elements as the damage code carries them (FloatsByDamageTypes: physical, magic, fire,
# lightning, dark). The game's "dark" is the menu's holy.
ELEMENTS = ('physical', 'magic', 'fire', 'lightning', 'holy')

# `EXE` 0x1406883e0 (1.17.1: 0x140689230): defense[t] = trunc(CC(sum of stats, 102) + CC(stat, g)).
# Lightning has no stat term. Stat identities come from the PlayerGameData offsets
# +0x298/+0x2a0/+0x288/+0x2a8 (`INFERRED` from the fromsoftware-rs / Ghidra type layout).
LEVEL_DEF_GRAPH = 102
STAT_DEF_GRAPH = {'physical': ('str', 130), 'magic': ('int', 132), 'fire': ('vig', 133),
                  'lightning': None, 'holy': ('arc', 135)}

# `EXE` 0x140688b40 (1.17.1: 0x140689990): resist[t] = trunc(CC(sum, lvl) + CC(stat, g)).
RESISTS = {
    #          level graph, stat, stat graph, EquipParamProtector field, SpEffect rate, SpEffect add
    'poison':  (110, 'vig', 120, 'resistPoison', 'registPoizonChangeRate', 'changePoisonResistPoint'),
    'rot':     (111, 'vig', 121, 'resistDisease', 'registDiseaseChangeRate', 'changeDiseaseResistPoint'),
    'bleed':   (112, 'end', 122, 'resistBlood', 'registBloodChangeRate', 'changeBloodResistPoint'),
    'frost':   (113, 'end', 123, 'resistFreeze', 'registFreezeChangeRate', 'changeFreezeResistPoint'),
    'sleep':   (114, 'mnd', 124, 'resistSleep', 'registSleepChangeRate', 'changeSleepResistPoint'),
    'madness': (115, 'mnd', 125, 'resistMadness', 'registMadnessChangeRate', 'changeMadnessResistPoint'),
    'death':   (116, 'arc', 126, 'resistCurse', 'registCurseChangeRate', 'changeCurseResistPoint'),
}
# The menu groups the seven into four numbers (`SITE` naming).
RESIST_GROUPS = {'immunity': 'poison', 'robustness': 'bleed', 'focus': 'sleep', 'vitality': 'death'}

ARMOR_CUT = {'physical': 'neutralDamageCutRate', 'strike': 'blowDamageCutRate',
             'slash': 'slashDamageCutRate', 'pierce': 'thrustDamageCutRate',
             'magic': 'magicDamageCutRate', 'fire': 'fireDamageCutRate',
             'lightning': 'thunderDamageCutRate', 'holy': 'darkDamageCutRate'}
SP_CUT = ARMOR_CUT  # SpEffectParam uses the same field names
CORRECT_SUFFIX = {'physical': 'Physics', 'strike': 'Physics', 'slash': 'Physics',
                  'pierce': 'Physics', 'magic': 'Magic', 'fire': 'Fire',
                  'lightning': 'Thunder', 'holy': 'Dark'}
STAT_ADD = {'vig': 'addLifeForceStatus', 'mnd': 'addWillpowerStatus', 'end': 'addEndureStatus',
            'str': 'addStrengthStatus', 'dex': 'addDexterityStatus', 'int': 'addMagicStatus',
            'fth': 'addFaithStatus', 'arc': 'addLuckStatus'}
GREAT_RUNE_SPEFFECT = {"Godrick's Great Rune": 600, "Radahn's Great Rune": 610,
                       "Morgott's Great Rune": 620, "Rykard's Great Rune": 630,
                       "Mohg's Great Rune": 640, "Malenia's Great Rune": 650}

# Physical sub-type index the damage code uses (AttackDamageInfo.damageType, `EXE` 0x140689c80):
# 0 slash, 1 strike (blow), 2 pierce (thrust), 3 standard (neutral).
PHYS_SUBTYPE = {'slash': 0, 'strike': 1, 'pierce': 2, 'physical': 3, 'standard': 3}


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


# `EXE` CalculateDefense 0x140690cf0 (1.17.1: 0x140691b40); constants at 1.17.1 0x143b37da8..dc4.
_R_LO, _R_ONE, _R_MID, _R_HI = f32(0.12), 1.0, 2.5, 8.0
_P_LO, _P_ONE, _P_MID, _P_HI = 90.0, 60.0, 30.0, 10.0
_EPS = f32(1.1920928955078125e-07)


def defense_curve(attack, defense):
    """Damage left after flat defense: attack * (1 - pct/100), pct a piecewise quadratic of attack/defense."""
    if -_EPS <= attack <= _EPS:
        return 0.0
    r = attack / defense if defense > 0 else _R_HI
    if r < _R_ONE:
        pct = _P_LO if r <= _R_LO else _P_LO + (_P_ONE - _P_LO) / (_R_ONE - _R_LO) ** 2 * (r - _R_LO) ** 2
    elif r <= _R_MID:
        pct = _P_MID + (_P_ONE - _P_MID) / (_R_ONE - _R_MID) ** 2 * (r - _R_MID) ** 2
    elif r < _R_HI:
        pct = _P_HI + (_P_MID - _P_HI) / (_R_MID - _R_HI) ** 2 * (r - _R_HI) ** 2
    else:
        pct = _P_HI
    return (1.0 - pct / 100.0) * attack


def calc_correct(row, x):
    """`EXE` PerformCalcCorrection 0x140690f30 (1.17.1: 0x140691d80) over a CalcCorrectGraph row."""
    v = [row[f'stageMaxVal{i}'] for i in range(5)]
    g = [row[f'stageMaxGrowVal{i}'] for i in range(5)]
    a = [row[f'adjPt_maxGrowVal{i}'] for i in range(5)]
    x = min(x, v[4])
    if x <= 0.0:
        return g[0]
    i = 0
    while i < 4 and x > v[i + 1]:
        i += 1
    if i >= 4:
        i = 3
    span = v[i + 1] - v[i]
    t = (x - v[i]) / span if span else 1.0
    e = a[i]
    w = t ** e if e >= 0 else 1.0 - (1.0 - t) ** (-e)
    y = g[i] + w * (g[i + 1] - g[i])
    return min(max(y, min(g[i], g[i + 1])), max(g[i], g[i + 1]))


class Tables:
    """The params this module reads, decoded once."""

    def __init__(self, regulation=None):
        files = PR.load(regulation)

        def rows(stem, fields=None):
            rs, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rs}

        self.graph = rows('CalcCorrectGraph')
        prot_fields = (['toughnessCorrectRate', 'protectorCategory', 'residentSpEffectId',
                        'residentSpEffectId2', 'residentSpEffectId3', 'weight', 'toughnessDamageCutRate']
                       + list(ARMOR_CUT.values()) + [v[3] for v in RESISTS.values()])
        self.protector = rows('EquipParamProtector', prot_fields)
        self.accessory = rows('EquipParamAccessory', ['refId', 'accessoryGroup', 'weight',
                                                     'residentSpEffectId1', 'residentSpEffectId2'])
        sp_fields = (list(SP_CUT.values()) + list(STAT_ADD.values())
                     + [f'def{w}DmgCorrectRate_{s}' for w in ('Player', 'Enemy')
                        for s in ('Physics', 'Magic', 'Fire', 'Thunder', 'Dark')]
                     + [v[4] for v in RESISTS.values()] + [v[5] for v in RESISTS.values()]
                     + ['toughnessDamageCutRate', 'stateInfo', 'conditionHp', 'conditionHpRate',
                        'effectEndurance'])
        self.speffect = rows('SpEffectParam', sp_fields)
        self.final_rate = rows('FinalDamageRateParam')
        self.prot_by_name = self._by_name('EquipParamProtector', self.protector,
                                          lambda r: r['protectorCategory'] in (0, 1, 2, 3))
        self.acc_by_name = self._by_name('EquipParamAccessory', self.accessory, lambda r: True)
        self.goods = rows('EquipParamGoods', ['refId_default', 'refCategory'])
        self.goods_by_name = self._by_name('EquipParamGoods', self.goods,
                                           lambda r: r['refId_default'] > 0)

    def goods_speffect(self, name):
        """SpEffect a consumable applies (crystal tears), via EquipParamGoods.refId_default."""
        rid = self.goods_by_name.get(_norm(name))
        return None if rid is None else self.goods[rid]['refId_default']

    @staticmethod
    def _by_name(stem, table, keep):
        out = {}
        for rid, name in sorted(PR.row_names(stem).items()):
            if name and rid in table and keep(table[rid]):
                out.setdefault(_norm(name), rid)
        return out

    def armor_row(self, name):
        rid = self.prot_by_name.get(_norm(name))
        return None if rid is None else self.protector[rid]

    def talisman_row(self, name):
        rid = self.acc_by_name.get(_norm(name))
        return None if rid is None else self.accessory[rid]


def _norm(name):
    return ''.join(ch for ch in name.lower().replace('’', "'") if ch.isalnum() or ch in "'+")


def is_passive(sp):
    """An effect that is on whenever the item is equipped (no HP or state condition)."""
    return sp['stateInfo'] == 0 and sp['conditionHp'] < 0 and sp['conditionHpRate'] < 0


def gather_effects(t, armor_rows, talisman_rows, great_rune=None, extra=(), all_talisman_effects=False):
    ids = []
    for r in armor_rows:
        ids += [r[k] for k in ('residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3')]
    for r in talisman_rows:
        ids += [r['refId'], r['residentSpEffectId1'], r['residentSpEffectId2']]
    if great_rune and great_rune in GREAT_RUNE_SPEFFECT:
        ids.append(GREAT_RUNE_SPEFFECT[great_rune])
    ids += list(extra)
    out = []
    for i in ids:
        sp = t.speffect.get(i) if i and i > 0 else None
        if sp and (all_talisman_effects or is_passive(sp)):
            out.append(sp)
    return out


def defender(t, stats, armor=(), talismans=(), great_rune=None, pvp=False, extra_speffects=(),
             cap_stats=False, strict=True):
    """Defense, absorption, resistance and poise of a player.

    `stats`: dict with vig, mnd, end, str, dex, int, fth, arc (base levels).
    `armor`, `talismans`: item names (row names from Smithbox) or row ids.
    `pvp`: absorption uses the SpEffect defPlayerDmgCorrectRate_* columns instead of defEnemy*.
    """
    armor_rows, talisman_rows, missing = [], [], []
    for n in armor:
        r = t.protector.get(n) if isinstance(n, int) else t.armor_row(n)
        (armor_rows.append(r) if r else missing.append(n))
    for n in talismans:
        r = t.accessory.get(n) if isinstance(n, int) else t.talisman_row(n)
        (talisman_rows.append(r) if r else missing.append(n))
    if missing and strict:
        raise KeyError(f'unknown item name(s): {missing}')
    effects = gather_effects(t, armor_rows, talisman_rows, great_rune, extra_speffects)

    eff = {s: stats.get(s, 0) + sum(sp[STAT_ADD[s]] for sp in effects) for s in STATS}
    if cap_stats:
        eff = {s: min(v, 99) for s, v in eff.items()}
    level_sum = sum(eff.values())

    defense, defense_site = {}, {}
    lvl = calc_correct(t.graph[LEVEL_DEF_GRAPH], level_sum)
    for el in ELEMENTS:
        sg = STAT_DEF_GRAPH[el]
        st = calc_correct(t.graph[sg[1]], eff[sg[0]]) if sg else 0.0
        defense[el] = math.trunc(lvl + st)
        defense_site[el] = math.floor(lvl) + math.floor(st)
    for sub in ('strike', 'slash', 'pierce'):
        defense[sub] = defense['physical']
        defense_site[sub] = defense_site['physical']

    who = 'Player' if pvp else 'Enemy'
    armor_mult, effect_mult, absorption = {}, {}, {}
    for ty in TYPES:
        a = 1.0
        for r in armor_rows:
            a *= r[ARMOR_CUT[ty]]
        e = 1.0
        for sp in effects:
            e *= sp[SP_CUT[ty]] * sp[f'def{who}DmgCorrectRate_{CORRECT_SUFFIX[ty]}']
        armor_mult[ty], effect_mult[ty] = a, e
        absorption[ty] = 100.0 * (1.0 - a * e)

    resist, resist_base, resist_armor, resist_site = {}, {}, {}, {}
    for name, (lg, st, sg, pf, rate_f, add_f) in RESISTS.items():
        c_lvl, c_st = calc_correct(t.graph[lg], level_sum), calc_correct(t.graph[sg], eff[st])
        base = math.trunc(c_lvl + c_st)
        arm = sum(r[pf] for r in armor_rows)
        rate = 1.0
        add = 0
        for sp in effects:
            rate *= sp[rate_f]
            add += sp[add_f]
        resist_base[name], resist_armor[name] = base, arm
        resist[name] = max(1, math.trunc(rate * base + arm + add))
        # The planner floors the two graph values separately (`SITE`).
        resist_site[name] = max(1, math.trunc(rate * (math.floor(c_lvl) + math.floor(c_st)) + arm + add))

    poise_sum = sum(r['toughnessCorrectRate'] for r in armor_rows)
    tcut = 1.0
    for sp in effects:
        tcut *= sp['toughnessDamageCutRate']
    return {
        'effective_stats': eff, 'level_sum': level_sum,
        'defense': defense, 'defense_site': defense_site,
        'absorption': absorption, 'armor_mult': armor_mult, 'effect_mult': effect_mult,
        'resist': resist, 'resist_base': resist_base, 'resist_armor': resist_armor,
        'resist_groups': {g: resist[k] for g, k in RESIST_GROUPS.items()}, 'resist_site': resist_site,
        'poise_raw': poise_sum, 'poise': poise_sum * 1000.0,
        'poise_effective': poise_sum * 1000.0 / tcut, 'toughness_cut': tcut,
        'missing': missing, 'effects': [sp['id'] for sp in effects],
    }


def damage(ar_by_type, motion_value, defender_info, phys_type='physical', final_rate=None,
           mp_correction=1.0):
    """One hit on a defender, `EXE` CalculateDamageBasic 0x1406849d0 (1.17.1: 0x140685820).

    ar_by_type: {'physical'|'magic'|'fire'|'lightning'|'holy': attack rating}.
    motion_value: percent (100 = 1.0x). Applying it to AR before the defense step is `INFERRED`.
    phys_type: which physical absorption applies (standard/physical, strike, slash, pierce). 'none'
      (an attack whose resolved `damageType` is not 0..3, such as 254) meets no physical cut rate:
      `CalculateAbsorptions` and `GetPhysicalDamageCutRateByType` fall through to 1.0 (`EXE`,
      defense.md section 2a).
    final_rate: FinalDamageRateParam row (dict with physRate..darkRate) or None; the game applies it
      only when attacker and defender are both players (`EXE` 0x140684d70).
    """
    fr = {'physical': 'physRate', 'magic': 'magRate', 'fire': 'fireRate',
          'lightning': 'thunRate', 'holy': 'darkRate'}
    per = {}
    for el in ELEMENTS:
        ar = ar_by_type.get(el, 0.0)
        atk = ar * motion_value / 100.0
        ab_key = ('physical' if phys_type == 'standard' else phys_type) if el == 'physical' else el
        d = defense_curve(atk, defender_info['defense'][el])
        if ab_key != 'none':
            d *= defender_info['armor_mult'][ab_key] * defender_info['effect_mult'][ab_key]
        if final_rate:
            d *= final_rate[fr[el]]
        d *= mp_correction
        per[el] = max(0.0, d)
    total = sum(per.values())
    if 0.0 < total < 1.0:
        total = math.ceil(total)
    return {'total': total, 'by_type': per}


# ---------------------------------------------------------------- corpus

CORPUS = os.path.expanduser('~/.cache/er-build-planner/builds.jsonl')


def _equipped(slots):
    return [s['name'] for s in (slots or []) if s.get('equipIndex') is not None]


def build_inputs(b):
    st = b.get('stats') or {}
    stats = {'vig': st.get('vig', 0), 'mnd': st.get('mnd', 0), 'end': st.get('vit', 0),
             'str': st.get('str', 0), 'dex': st.get('dex', 0), 'int': st.get('int', 0),
             'fth': st.get('fth', 0), 'arc': st.get('arc', 0)}
    prot = b.get('protectors') or {}
    armor = []
    for k in ('head', 'body', 'arms', 'legs'):
        # The planner takes the first slot with an equipIndex per category (`SITE`).
        armor += _equipped((prot.get(k) or {}).get('slots'))[:1]
    tal = _equipped((b.get('talismans') or {}).get('slots'))
    tears = []
    if (b.get('conditions') or {}).get('crystalTears'):
        tears = [x for x in ((b.get('items') or {}).get('crystalTears') or []) if x]
    return stats, armor, tal, tears


def corpus(t, limit=None, show=20):
    from collections import Counter, defaultdict
    n = 0
    agree = Counter()
    total = Counter()
    bad = defaultdict(list)
    skipped = Counter()
    with open(CORPUS) as fh:
        for line in fh:
            if limit and n >= limit:
                break
            rec = json.loads(line)
            b = rec['build']
            comp = b.get('computed') or {}
            if not comp.get('defenses'):
                skipped['no computed'] += 1
                continue
            stats, armor, tal, tears = build_inputs(b)
            if not any(stats.values()):
                skipped['no stats'] += 1
                continue
            tear_ids = [t.goods_speffect(x) for x in tears]
            d = defender(t, stats, armor, tal, great_rune=b.get('greatRune'),
                         pvp=not b.get('isPvE'), extra_speffects=[i for i in tear_ids if i],
                         strict=False)
            d['missing'] += [x for x, i in zip(tears, tear_ids) if not i]
            if d['missing']:
                skipped['unknown item name'] += 1
                bad['names'].append((rec['id'], d['missing']))
                continue
            n += 1
            for ty in TYPES:
                for key, ours in (('defense', d['defense'][ty]), ('defense_site', d['defense_site'][ty])):
                    total[key] += 1
                    want = comp['defenses'].get(ty)
                    if want == ours:
                        agree[key] += 1
                    elif key == 'defense_site':
                        bad[key].append((rec['id'], ty, want, ours, d['defense'][ty], stats, tal))
                want = (comp.get('absorption') or {}).get(ty)
                if want is not None:
                    total['absorption'] += 1
                    if abs(want - d['absorption'][ty]) < 0.01:
                        agree['absorption'] += 1
                    else:
                        bad['absorption'].append((rec['id'], ty, round(want, 3),
                                                  round(d['absorption'][ty], 3), tal))
            for g, k in RESIST_GROUPS.items():
                want = (comp.get('resistances') or {}).get(g)
                if want is None:
                    continue
                total['resist'] += 1
                total['resist_site'] += 1
                agree['resist_site'] += want == d['resist_site'][k]
                if want == d['resist'][k]:
                    agree['resist'] += 1
                if want != d['resist_site'][k]:
                    bad['resist_site'].append((rec['id'], g, want, d['resist_site'][k], armor, tal))
            po = comp.get('poise') or {}
            if 'original' in po:
                total['poise'] += 1
                ours = round(d['poise'], 6)
                if abs(po['original'] - ours) < 0.01:
                    agree['poise'] += 1
                else:
                    bad['poise'].append((rec['id'], po['original'], ours, armor))
            if 'altered' in po:
                total['poise_altered'] += 1
                if abs(po['altered'] - d['poise_effective']) < 0.05:
                    agree['poise_altered'] += 1
                else:
                    bad['poise_altered'].append((rec['id'], po['altered'], round(d['poise_effective'], 2), tal))
    print(f'builds compared: {n}; skipped: {dict(skipped)}')
    for k in ('defense', 'defense_site', 'absorption', 'resist', 'resist_site', 'poise', 'poise_altered'):
        if total[k]:
            print(f'  {k:14s} {agree[k]:6d}/{total[k]:<6d} = {100.0 * agree[k] / total[k]:6.2f}%')
    for k, v in bad.items():
        print(f'-- {k}: {len(v)} disagreements; first {min(show, len(v))}:')
        for x in v[:show]:
            print('   ', x)
    return agree, total, bad


# ---------------------------------------------------------------- self test

def selftest(t=None):
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('ok   ' if cond else 'FAIL ') + msg)
        ok = ok and cond

    # Curve anchor points and continuity.
    check(abs(defense_curve(100, 100) - 40.0) < 1e-4, 'ratio 1 keeps 40%')
    check(abs(defense_curve(250, 100) - 175.0) < 1e-4, 'ratio 2.5 keeps 70%')
    check(abs(defense_curve(800, 100) - 720.0) < 1e-3, 'ratio 8 keeps 90%')
    check(abs(defense_curve(10, 100) - 1.0) < 1e-4, 'ratio 0.1 keeps 10%')
    check(abs(defense_curve(100, 0) - 90.0) < 1e-4, 'zero defense uses ratio 8')
    check(defense_curve(0, 100) == 0.0, 'zero attack gives zero')
    for r in (_R_LO, 1.0, 2.5, 8.0):
        lo, hi = defense_curve(r * 100 - 1e-4, 100), defense_curve(r * 100 + 1e-4, 100)
        check(abs(lo - hi) < 1e-2, f'continuous at ratio {r:g}')
    prev = 0.0
    mono = True
    for i in range(1, 2000):
        v = defense_curve(i, 100) / i
        mono = mono and v >= prev - 1e-9
        prev = v
    check(mono, 'kept fraction never decreases as attack/defense rises')

    t = t or Tables()
    check(calc_correct(t.graph[102], 1) == 40.0 and calc_correct(t.graph[102], 150) == 100.0,
          'CalcCorrectGraph 102 anchors 40 at 1 and 100 at 150')
    # Planner's first corpus build (`SITE`): all 99, Greatjar + Fire Prelate, 4 talismans.
    d = t and defender(t, {s: 99 for s in STATS},
                       ['Greatjar', 'Fire Prelate Armor', 'Fire Prelate Gauntlets', 'Fire Prelate Greaves'],
                       ["Erdtree's Favor +2", 'Dragoncrest Greatshield Talisman',
                        'Pearldrake Talisman +3', 'Two-Headed Turtle Talisman'], pvp=True)
    want = {'fire': 225, 'holy': 225, 'magic': 225, 'physical': 195, 'lightning': 155}
    check(all(d['defense'][k] == v for k, v in want.items()), f'all-99 defenses {want}')
    check(abs(d['poise'] - 96) < 0.01, 'Greatjar + Fire Prelate set poise 96')
    check({g: d['resist'][k] for g, k in RESIST_GROUPS.items()}
          == {'focus': 428, 'immunity': 392, 'vitality': 349, 'robustness': 387},
          'all-99 resistances match the planner')
    hit = damage({'physical': 500}, 100, d)
    check(hit['total'] > 0 and hit['total'] < 500, 'a 500 AR hit is reduced')
    tiny = damage({'physical': 0.5}, 100, d)
    check(tiny['total'] == 1, 'a positive sub-1 total is raised to 1')
    print('selftest', 'passed' if ok else 'FAILED')
    return ok


def _parse_stats(s):
    out = {k: 0 for k in STATS}
    for kv in s.split(','):
        k, v = kv.split('=')
        out[{'vit': 'end'}.get(k.strip(), k.strip())] = int(v)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stats')
    ap.add_argument('--armor', default='')
    ap.add_argument('--talismans', default='')
    ap.add_argument('--great-rune')
    ap.add_argument('--pvp', action='store_true')
    ap.add_argument('--ar', help='e.g. physical=400,fire=200: also print one hit')
    ap.add_argument('--mv', type=float, default=100.0)
    ap.add_argument('--phys-type', default='physical')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--corpus', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--show', type=int, default=20)
    ap.add_argument('--regulation')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest(Tables(a.regulation)) else 1)
    if a.corpus:
        corpus(Tables(a.regulation), a.limit, a.show)
        sys.exit(0)
    if not a.stats:
        ap.error('--stats, --selftest or --corpus')
    t = Tables(a.regulation)
    d = defender(t, _parse_stats(a.stats), [x for x in a.armor.split(',') if x],
                 [x for x in a.talismans.split(',') if x], a.great_rune, a.pvp)
    show = {k: d[k] for k in ('effective_stats', 'defense', 'absorption', 'resist', 'resist_groups',
                              'poise', 'poise_effective', 'effects')}
    print(json.dumps(show, indent=1))
    if a.ar:
        ar = {k: float(v) for k, v in (kv.split('=') for kv in a.ar.split(','))}
        print(json.dumps(damage(ar, a.mv, d, a.phys_type), indent=1))
